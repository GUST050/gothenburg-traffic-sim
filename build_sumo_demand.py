"""
Calibrate SUMO demand against the 6 sensors for a chosen time window.

Run after build_sumo_net.py:
  python3 build_sumo_demand.py                         # Tue 2025-09-16 06:00-10:00
  python3 build_sumo_demand.py --date 2025-09-16 --begin 07:00 --end 09:00

Method (SUMO's routeSampler workflow):
  1. Sensor counts for the window → 15-min edgeData intervals (counts.xml).
     "Total" sensors measure the SUM of both directions; direction is not
     recoverable (no directional re-export is coming), so the count is split
     50/50 over the two directed edges — the max-entropy assumption. Sensor
     1076 ("S") is a true single-direction count on one edge.
  2. randomTrips.py + duarouter → a large pool of CANDIDATE routes through
     the full network (diverse origins/destinations, through-traffic favored).
  3. routeSampler.py samples routes from the pool so that simulated 15-min
     flows match the sensor counts → calibrated.rou.xml.

Honesty note: only traffic that crosses a sensor is calibrated. Streets far
from sensors carry little/no sampled traffic — which is correct: we have no
ground truth there, and the per-edge confidence in the web app says exactly
that.

Writes (sumo/):
  counts.xml, candidates.rou.xml, calibrated.rou.xml, demand_meta.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from build_sumo_net import sumo_home

FLOWS_PATH = Path("web/data/flows.json")
GEO_PATH   = Path("web/data/network.geojson")
SUMO_DIR   = Path("sumo")
NET_PATH   = SUMO_DIR / "net.net.xml"

EPOCH    = pd.Timestamp("2025-01-01T00:00:00")
INTERVAL = pd.Timedelta(minutes=15)

# Candidate-pool density: one random trip every N seconds of the window.
# The pool needs route DIVERSITY, not volume — routeSampler repeats routes.
CANDIDATE_PERIOD_S = 2.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--date",  default="2025-09-16",
                   help="Simulation date (default: Tue 2025-09-16 — normal September weekday)")
    p.add_argument("--begin", default="06:00", help="Window start HH:MM (default 06:00)")
    p.add_argument("--end",   default="10:00",
                   help="Window end HH:MM; '24:00' = whole day (default 10:00)")
    p.add_argument("--seed",  type=int, default=42)
    p.add_argument("--engine", choices=["pfe", "routesampler"], default="pfe",
                   help="Calibration engine: pfe = the level-1/2/3 hierarchy "
                        "(hard counts, conservation bounds, learned priors); "
                        "routesampler = reference implementation (counts only)")
    p.add_argument("--legacy-random-pool", action="store_true",
                   help="Use uniform randomTrips instead of the subarea/DeSO/"
                        "RVU candidate generator (build_candidates.py). Kept "
                        "only for comparison; the grounded generator is default.")
    p.add_argument("--through-fraction", type=float, default=0.5,
                   help="θ passed to build_candidates.py. NOT locally "
                        "identifiable (no external cordon counts exist to "
                        "discriminate it) — 0.5 is a disclosed neutral prior, "
                        "not a calibrated value.")
    p.add_argument("--gravity-km", type=float, default=2.6,
                   help="θ passed to build_candidates.py. Frozen from a "
                        "trip-length fit against RVU Västra Götaland's "
                        "measured distance bins (see calibrate_theta.py's "
                        "docstring and the trip-length check it replaced "
                        "GEH-based scoring with — GEH saturated at 100% for "
                        "all 9 grid points and could not discriminate θ).")
    p.add_argument("--no-assignment-prior", action="store_true",
                   help="Disable the weak gravity-assignment prior "
                        "(assignment_priors.py) — kept for the controlled "
                        "A/B comparison; the prior is on by default.")
    return p.parse_args()


def load_sensor_edges() -> dict[str, list[str]]:
    """{sensor_id: [edge_id, ...]} from network.geojson (1 or 2 edges)."""
    with open(GEO_PATH) as f:
        geo = json.load(f)
    result: dict[str, list[str]] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        if p.get("sensor_id"):
            result.setdefault(str(p["sensor_id"]), []).append(p["id"])
    return result


def load_direction_split(key: str = "edge_shares") -> dict[str, list[float]]:
    """{edge_id: [96 shares]} from the estimated split file, {} if not built.

    key selects the quantile: "edge_shares" (q50 point estimate) or
    "edge_shares_q10"/"edge_shares_q90" (interval bounds from
    dirsplit/predict.py — used to build demand VARIANTS so Monte Carlo
    includes direction uncertainty)."""
    path = SUMO_DIR / "direction_split.json"
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    shares: dict[str, list[float]] = {}
    for d in data.values():
        shares.update(d.get(key) or d["edge_shares"])
    return shares


def has_split_quantiles() -> bool:
    path = SUMO_DIR / "direction_split.json"
    if not path.exists():
        return False
    with open(path) as f:
        data = json.load(f)
    return any("edge_shares_q10" in d for d in data.values())


def build_targets(
    flows: dict[str, list],
    sensor_edges: dict[str, list[str]],
    qi_start: int,
    n_intervals: int,
    split_key: str = "edge_shares",
) -> list[dict[str, float]]:
    """Per-quarter measured targets {edge: count} — the level-1 constraints."""
    est_shares = load_direction_split(split_key)
    out: list[dict[str, float]] = []
    for i in range(n_intervals):
        qi, slot = qi_start + i, (qi_start + i) % 96
        t: dict[str, float] = {}
        for edges in sensor_edges.values():
            for edge_id in edges:
                share = est_shares.get(edge_id, [1.0 / len(edges)] * 96)[slot]
                v = flows.get(edge_id, [None])[qi] if qi < len(flows.get(edge_id, [])) else None
                if v is not None:
                    t[edge_id] = v * share
        out.append(t)
    return out


def ensure_bounds(date: str, begin: str, end: str) -> dict:
    """Level-2 interval bounds for this window — computed on demand."""
    path = Path("web/data/observability_bounds.json")
    if path.exists():
        with open(path) as f:
            d = json.load(f)
        with open(GEO_PATH) as f:
            n_now = len(json.load(f)["features"])
        # date/window AND graph fingerprint must match — stale bounds from a
        # different network silently poison the calibration as infeasibility
        if ((d["date"], d["begin"], d["end"]) == (date, begin, end)
                and d.get("graph_edges") == n_now):
            return d
    print("Computing level-2 bounds (observability LP) …")
    from observability import compute_bounds_cli
    compute_bounds_cli(date, begin, end)
    with open(path) as f:
        return json.load(f)


def ensure_observability() -> dict:
    """Fresh Agent-B products (derived flows, corridor priors) for THIS graph."""
    path = Path("web/data/observability.json")
    with open(GEO_PATH) as f:
        n_now = len(json.load(f)["features"])
    if path.exists():
        with open(path) as f:
            d = json.load(f)
        if d.get("graph_edges") == n_now:
            return d
    print("Running observability (Agent B) …")
    res = subprocess.run([sys.executable, "observability.py"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-800:])
        return {"corridor_priors": {}, "derived_flows": {}}
    with open(path) as f:
        return json.load(f)


def ensure_assignment_priors() -> dict:
    """Weak gravity-assignment prior for every otherwise-unconstrained edge
    (assignment_priors.py) — replaces the PFE's implicit 'pull to zero'
    (parsimony term) with 'pull toward the gravity-implied realistic
    level' everywhere a real measurement, bound, direction prior or
    corridor coupling doesn't already apply."""
    path = Path("sumo/assignment_priors.json")
    if not path.exists():
        print("Computing assignment priors (assignment_priors.py) …")
        res = subprocess.run([sys.executable, "assignment_priors.py"],
                             capture_output=True, text=True)
        if res.returncode != 0:
            print(res.stderr[-800:])
            return {"weight": 0.0, "flows": {}}
    with open(path) as f:
        return json.load(f)


def ensure_priors(date: str) -> dict:
    """Level-3 learned priors for unmeasured opposite directions."""
    path = Path("sumo/prior_flows.json")
    if path.exists():
        with open(path) as f:
            d = json.load(f)
        if d.get("date") == date:
            return d
    print("Computing level-3 priors (prior_flows) …")
    res = subprocess.run([sys.executable, "prior_flows.py", "--date", date],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-1000:])
        print("  (no priors available — continuing without level 3)")
        return {"edges": {}}
    with open(path) as f:
        return json.load(f)


def write_counts(
    flows: dict[str, list],
    sensor_edges: dict[str, list[str]],
    qi_start: int,
    n_intervals: int,
    out_path: Path,
    split_key: str = "edge_shares",
) -> int:
    """15-min edgeData intervals; sim time 0 = window start. Returns n written.

    Direction share per Total edge comes from the estimated split file
    (dirsplit model or Gaussian fallback) when available, else an even
    split. "S" sensor edges always take the full count.
    """
    est_shares = load_direction_split(split_key)
    if est_shares:
        print(f"  Using ESTIMATED direction split ({split_key})")
    else:
        print("  No direction_split.json — falling back to even split")

    n_measurements = 0
    with open(out_path, "w") as f:
        f.write("<data>\n")
        for i in range(n_intervals):
            qi   = qi_start + i
            slot = qi % 96
            f.write(f'  <interval id="q{qi}" begin="{i * 900}" end="{(i + 1) * 900}">\n')
            for edges in sensor_edges.values():
                for edge_id in edges:
                    share = est_shares.get(edge_id, [1.0 / len(edges)] * 96)[slot]
                    v = flows.get(edge_id, [None])[qi] if qi < len(flows.get(edge_id, [])) else None
                    if v is None:
                        continue
                    f.write(f'    <edge id="{edge_id}" count="{v * share:.1f}"/>\n')
                    n_measurements += 1
            f.write("  </interval>\n")
        f.write("</data>\n")
    return n_measurements


SECTORS = ["N", "NO", "O", "SO", "S", "SV", "V", "NV"]


def export_od(calib_path: Path, sensor_edges: dict[str, list[str]], meta: dict) -> None:
    """
    Aggregate the calibrated routes into an origin/destination matrix.

    The sampled routes ARE trips with origins and destinations, so the OD
    matrix that falls out is by construction consistent with the sensor
    counts — one plausible OD among the many the 6 counters cannot
    distinguish. Zones: the two sensor cluster areas (<400 m from a cluster
    centre, named by geometry: western = Götaplatsen, eastern = Scandinavium)
    plus eight compass entry sectors around the network.

    Writes web/data/od_matrix.json + od_matrix.csv.
    """
    import xml.etree.ElementTree as ET

    # Edge midpoints in metric EPSG:3007 from the plain edges file
    mids: dict[str, tuple[float, float]] = {}
    for e in ET.parse(SUMO_DIR / "plain.edg.xml").getroot().findall("edge"):
        pts = [tuple(map(float, p.split(","))) for p in e.get("shape").split()]
        mids[e.get("id")] = (sum(p[0] for p in pts) / len(pts),
                             sum(p[1] for p in pts) / len(pts))

    # Cluster centres: group sensors whose edges lie within 600 m of each other
    import math
    sensor_pos = {sid: mids[edges[0]] for sid, edges in sensor_edges.items()
                  if edges[0] in mids}
    clusters: list[list[str]] = []
    for sid, pos in sensor_pos.items():
        for cl in clusters:
            cx = sum(sensor_pos[s][0] for s in cl) / len(cl)
            cy = sum(sensor_pos[s][1] for s in cl) / len(cl)
            if math.hypot(pos[0] - cx, pos[1] - cy) < 600:
                cl.append(sid)
                break
        else:
            clusters.append([sid])
    centres = [(sum(sensor_pos[s][0] for s in cl) / len(cl),
                sum(sensor_pos[s][1] for s in cl) / len(cl)) for cl in clusters]
    # Western cluster = Götaplatsen, eastern = Scandinavium (pure geometry)
    names = ["Götaplatsen-området", "Scandinavium-området"]
    cluster_zones = sorted(zip(centres, names))  # sorted by x (west first)

    net_cx = sum(m[0] for m in mids.values()) / len(mids)
    net_cy = sum(m[1] for m in mids.values()) / len(mids)

    def zone_of(edge_id: str) -> str:
        mid = mids.get(edge_id)
        if mid is None:
            return "okänd"
        for (cx, cy), zname in cluster_zones:
            if math.hypot(mid[0] - cx, mid[1] - cy) < 400:
                return zname
        ang = math.degrees(math.atan2(mid[0] - net_cx, mid[1] - net_cy)) % 360
        return f"Infart {SECTORS[int((ang + 22.5) // 45) % 8]}"

    od: dict[tuple[str, str], int] = {}
    n_trips = 0
    for veh in ET.parse(calib_path).getroot().findall("vehicle"):
        edges = veh.find("route").get("edges").split()
        key = (zone_of(edges[0]), zone_of(edges[-1]))
        od[key] = od.get(key, 0) + 1
        n_trips += 1

    zones = sorted({z for pair in od for z in pair})
    matrix = {o: {d: od.get((o, d), 0) for d in zones} for o in zones}

    out_json = Path("web/data/od_matrix.json")
    with open(out_json, "w") as f:
        json.dump({
            "window":  f"{meta['date']} {meta['begin']}–{meta['end']}",
            "n_trips": n_trips,
            "zones":   zones,
            "matrix":  matrix,
            "note":    "ESTIMATED OD — one plausible matrix consistent with the "
                       "6 sensor counts and the estimated direction split; the "
                       "true OD is not identifiable from 6 counting points.",
        }, f, ensure_ascii=False, indent=1)

    out_csv = Path("web/data/od_matrix.csv")
    with open(out_csv, "w") as f:
        f.write("origin\\destination," + ",".join(zones) + "\n")
        for o in zones:
            f.write(o + "," + ",".join(str(matrix[o][d]) for d in zones) + "\n")

    print(f"\nOD-matris ({n_trips} kalibrerade resor) → {out_json} + {out_csv}")
    top = sorted(od.items(), key=lambda kv: -kv[1])[:8]
    for (o, d), n in top:
        print(f"  {o:<22} → {d:<22} {n:>5}")


def run_tool(script: str, args: list[str], home: Path) -> None:
    cmd = [sys.executable, str(home / "tools" / script), *args]
    env = {
        "SUMO_HOME": str(home),
        "PATH": f"{home / 'bin'}:/usr/bin:/bin",
        "HOME": str(Path.home()),
    }
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    tail = (res.stdout + res.stderr)[-2500:]
    print(tail)
    if res.returncode != 0:
        sys.exit(f"{script} failed")


def main() -> None:
    args = parse_args()
    if not NET_PATH.exists():
        sys.exit("sumo/net.net.xml missing — run build_sumo_net.py first")

    t0 = pd.Timestamp(f"{args.date} {args.begin}")
    if args.end == "24:00":   # whole day — pandas rejects hour 24
        t1 = t0.normalize() + pd.Timedelta(days=1)
    else:
        t1 = pd.Timestamp(f"{args.date} {args.end}")
    qi_start    = int((t0 - EPOCH) / INTERVAL)
    n_intervals = int((t1 - t0) / INTERVAL)
    duration_s  = n_intervals * 900
    print(f"Window: {t0} → {t1}  ({n_intervals} × 15 min)")

    with open(FLOWS_PATH) as f:
        flows = json.load(f)["flows"]
    sensor_edges = load_sensor_edges()
    print(f"Sensors: { {sid: len(e) for sid, e in sensor_edges.items()} }")

    home = sumo_home()

    cand_path = SUMO_DIR / "candidates.rou.xml"
    if args.legacy_random_pool:
        print("\nGenerating candidate route pool (LEGACY: uniform randomTrips) …")
        # The pool needs DIVERSITY, not volume — cap it so whole-day windows
        # don't produce 40k candidates that routeSampler then has to chew through.
        period = max(CANDIDATE_PERIOD_S, duration_s / 10_000)
        run_tool("randomTrips.py", [
            "-n", str(NET_PATH),
            "-r", str(cand_path),
            "-o", str(SUMO_DIR / "trips.trips.xml"),
            "-b", "0", "-e", str(duration_s),
            "-p", str(period),
            "--fringe-factor", "5",
            "--seed", str(args.seed),
            "--validate",
        ], home)
    else:
        print("\nGenerating candidate route pool (subarea/DeSO/RVU generator) …")
        n_total = max(6000, int(12000 * duration_s / 86400))
        res = subprocess.run(
            [sys.executable, "build_candidates.py",
             "--through-fraction", str(args.through_fraction),
             "--gravity-km", str(args.gravity_km),
             "--n-total", str(n_total), "--seed", str(args.seed)],
            capture_output=True, text=True)
        print(res.stdout[-1200:])
        if res.returncode != 0:
            print(res.stderr[-1500:])
            sys.exit("build_candidates.py failed")

    # ── Calibrate: one route set per direction-split variant ───────────────────
    # q50 = the default (calibrated.rou.xml). If the split file carries
    # quantile bounds, two extra variants are built — run_scenario spreads
    # its Monte Carlo seeds over them so direction uncertainty reaches the
    # per-edge confidence numbers.
    variants = [("", "edge_shares")]
    if has_split_quantiles():
        variants += [("_v1", "edge_shares_q10"), ("_v2", "edge_shares_q90")]

    calib_path = SUMO_DIR / "calibrated.rou.xml"

    if args.engine == "pfe":
        # ── The full hierarchy: hard counts + conservation bounds + priors ────
        import pfe
        bounds_data = ensure_bounds(args.date, args.begin, args.end)
        priors_data = ensure_priors(args.date)
        obs_data    = ensure_observability()
        corridor    = obs_data.get("corridor_priors", {})
        if corridor:
            print(f"  corridor coupling: {len(corridor)} edges between "
                  f"sensor pairs get data-derived priors")
        assign_data = ensure_assignment_priors() if not args.no_assignment_prior else {"weight": 0.0, "flows": {}}
        assign_w    = assign_data.get("weight", 0.0)
        assign_flows = assign_data.get("flows", {})
        if assign_flows:
            print(f"  gravity-assignment prior: {len(assign_flows)} otherwise-"
                  f"unconstrained edges get a weak (w={assign_w}) realistic pull")
        prior_variant = {"": "prior", "_v1": "prior_low", "_v2": "prior_high"}

        for suffix, key in variants:
            targets = build_targets(flows, sensor_edges, qi_start,
                                    n_intervals, split_key=key)
            bounds_pq, priors_pq = [], []
            for i in range(n_intervals):
                bq = {}
                for e, arr in bounds_data["bounds"].items():
                    if i < len(arr) and arr[i]:
                        bq[e] = (arr[i][0], arr[i][1])
                pq = {}
                slot = (qi_start + i) % 96
                pkey = prior_variant.get(suffix, "prior")
                for e, d in priors_data.get("edges", {}).items():
                    val = d[pkey][slot]
                    if val is None:
                        continue
                    lo = d["prior_low"][slot] or 0.0
                    hi = d["prior_high"][slot] or val
                    pq[e] = (float(val), 1.0 / max(1.0, hi - lo))
                # Sensors helping each other: corridor blends between sensor
                # pairs — data-derived, so their (narrow) band gives them
                # naturally higher weight than the learned priors
                for e, d in corridor.items():
                    qi = qi_start + i
                    if qi >= len(d["prior"]) or d["prior"][qi] is None:
                        continue
                    band = d["band"][qi] or 8.0
                    pq[e] = (float(d["prior"][qi]), 1.0 / max(1.0, band))
                # Gravity-assignment field: a WIDE INTERVAL BOUND (not a
                # soft L1 prior) on edges no stronger source covers. A prior
                # costs 2 extra LP variables + a row EACH — at ~6 500 edges
                # that made the per-quarter LP intractable (a whole-day
                # solve stalled >35 min with 0 progress, killed). A bound is
                # 1-2 inequality rows with NO new variables — the same
                # mechanism level-2 bounds already use — and is arguably
                # more honest anyway: this field is a rough plausibility
                # range, not a confident target.
                if assign_w > 0:
                    slot = (qi_start + i) % 96
                    for e, series in assign_flows.items():
                        if e in bq or e in pq or slot >= len(series):
                            continue
                        v = series[slot]
                        if v is None:
                            continue
                        bq[e] = (0.0, max(5.0, 5.0 * v))
                bounds_pq.append(bq)
                priors_pq.append(pq)

            out = SUMO_DIR / f"calibrated{suffix}.rou.xml"
            report = pfe.calibrate(cand_path, out, targets,
                                   bounds_pq, priors_pq)
            print(f"  PFE {key:<16} {report['vehicles']:>6} veh  "
                  f"GEH<5: {report['geh_pct']}%  "
                  f"(infeasible intervals: {report['infeasible_intervals']})")
            if report["geh_pct"] < 100:
                print("  ⚠ measured-edge fit below gate — inspect before use")
    else:
        for suffix, key in variants:
            counts_path = SUMO_DIR / f"counts{suffix}.xml"
            n = write_counts(flows, sensor_edges, qi_start, n_intervals,
                             counts_path, split_key=key)
            print(f"Wrote {counts_path}  ({n} edge×interval measurements)")
            print(f"Sampling routes to match counts ({key}) …")
            run_tool("routeSampler.py", [
                "-r", str(cand_path),
                "--edgedata-files", str(counts_path),
                "--edgedata-attribute", "count",
                "-o", str(SUMO_DIR / f"calibrated{suffix}.rou.xml"),
                "--seed", str(args.seed),
            ], home)

    meta = {
        "date": args.date, "begin": args.begin, "end": args.end,
        "qi_start": qi_start, "n_intervals": n_intervals,
        # ISO with 'T' — Safari/Firefox reject "YYYY-MM-DD HH:MM" in new Date()
        "epoch_sim": t0.isoformat(),
        "direction_split": "estimated" if load_direction_split() else "even",
        "n_variants": len(variants),
        "note": "Total sensor counts split over the two directed edges using "
                "the estimated time-of-day split (estimate_directions.py); "
                "direction is not measured in the delivered data.",
    }
    with open(SUMO_DIR / "demand_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\nWrote {calib_path} + demand_meta.json")

    export_od(calib_path, sensor_edges, meta)


if __name__ == "__main__":
    main()
