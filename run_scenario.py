"""
Run a SUMO scenario (baseline or road closure) and export flows for the web app.

Run after build_sumo_demand.py:
  python3 run_scenario.py                                    # baseline
  python3 run_scenario.py --close 60786979_3575001205_0      # closure scenario

Method:
  - Simulates the calibrated demand (sumo/calibrated.rou.xml) N times with
    different random seeds (Monte Carlo).
  - --close <edgeId> adds a rerouter that closes the edge for all traffic;
    SUMO reroutes vehicles around it by construction.
  - 15-min per-edge flows ("entered" vehicle counts) are averaged over seeds
    and written in the flows.json format with the SAME edge IDs as the map.

Per-edge confidence (0-1), written into the scenario file:
    confidence = spatial_prior × exp(-CV)
  where spatial_prior comes from network.geojson (distance to nearest sensor)
  and CV is the mean coefficient of variation across Monte Carlo seeds.
  Far from sensors AND unstable across seeds → low confidence.

Writes:
  web/data/scenarios/<name>.json   — flows + confidence for the web app
  web/data/scenarios/index.json    — manifest the web app lists scenarios from
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from build_sumo_net import sumo_home

SUMO_DIR  = Path("sumo")
NET_PATH  = SUMO_DIR / "net.net.xml"
GEO_PATH  = Path("web/data/network.geojson")
OUT_DIR   = Path("web/data/scenarios")

# A whole-day meso run normally takes ~10-15 s (measured: 3-seed whole-day
# closure ~35 s total). This is a safety net, not a normal-path limit: without
# it, a hung sumo process has no bound and — if this script's own PARENT
# process (e.g. serve.py's outer subprocess.run) times out and kills THIS
# process first — becomes permanently orphaned, since a timeout can only ever
# kill its own direct child. Found in review 2026-07-07.
SUMO_TIMEOUT_S = 300


def demand_signature(meta: dict) -> str:
    """Stable fingerprint for the demand that produced a scenario.

    Scenario files are only meaningful for the calibrated demand currently
    represented by sumo/demand_meta.json. If someone runs build_sumo_demand.py
    manually, old closure scenarios from the previous date/window otherwise
    remain in index.json and look selectable even though they were generated
    from different routes. The signature keeps the manifest scoped to one
    demand build.
    """
    keys = ("date", "source", "begin", "end", "n_intervals", "epoch_sim", "n_variants")
    payload = {k: meta.get(k) for k in keys}
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def index_for_current_demand(index: dict, signature: str) -> dict:
    """Drop manifest entries generated from another demand calibration."""
    scenarios = [
        s for s in index.get("scenarios", [])
        if s.get("demand_signature") == signature
    ]
    return {"demand_signature": signature, "scenarios": scenarios}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--close", nargs="*", default=[], metavar="EDGE_ID",
                   help="Edge(s) to close (must exist in network.geojson). "
                        "Omit for baseline.")
    p.add_argument("--name",  default=None,
                   help="Scenario name (default: 'baseline' or 'close_<edge…>')")
    p.add_argument("--seeds", type=int, default=3,
                   help="Monte Carlo runs (default 3)")
    p.add_argument("--micro", action="store_true",
                   help="Use microscopic simulation (default: mesoscopic — "
                        "~20x faster, adequate for 15-min edge flows)")
    p.add_argument("--no-trajectories", action="store_true",
                   help="Skip the per-vehicle trajectory export (one extra "
                        "seed-1000 run with vehroute exit-times)")
    return p.parse_args()


def load_geojson_meta() -> tuple[dict[str, float], dict[str, str]]:
    """{edge_id: spatial confidence prior}, {edge_id: street name}."""
    with open(GEO_PATH) as f:
        geo = json.load(f)
    prior: dict[str, float] = {}
    names: dict[str, str] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        prior[p["id"]] = p.get("confidence") or 0.5
        names[p["id"]] = p.get("name") or p["id"]
    return prior, names


def write_edgedata_additional(path: Path, edgedata_file: Path,
                              duration_s: int) -> None:
    with open(path, "w") as f:
        f.write("<additional>\n")
        f.write(f'  <edgeData id="ed" file="{edgedata_file.name}" period="900" '
                f'begin="0" end="{duration_s}" excludeEmpty="true"/>\n')
        f.write("</additional>\n")


REROUTER_RADIUS_M = 400


def edges_near(close_edges: list[str], radius_m: float) -> list[str]:
    """Edges whose midpoint lies within radius_m of a closed edge's midpoint.

    The rerouter is attached to THESE edges only. Attaching it to all 2 251
    edges makes every vehicle re-check its route on every edge entry —
    measured cost: 11.5 min per whole-day seed vs ~1 min with a local
    rerouter. Locality is also behaviourally reasonable: drivers divert when
    they encounter the closure area, not telepathically at departure.
    """
    import math
    import xml.etree.ElementTree as ET
    mids: dict[str, tuple[float, float]] = {}
    for e in ET.parse(SUMO_DIR / "plain.edg.xml").getroot().findall("edge"):
        pts = [tuple(map(float, p.split(","))) for p in e.get("shape").split()]
        mids[e.get("id")] = (sum(p[0] for p in pts) / len(pts),
                             sum(p[1] for p in pts) / len(pts))
    centres = [mids[ce] for ce in close_edges if ce in mids]
    out = set(close_edges)
    for eid, (x, y) in mids.items():
        if any(math.hypot(x - cx, y - cy) <= radius_m for cx, cy in centres):
            out.add(eid)
    return sorted(out)


def write_closure_additional(path: Path, close_edges: list[str],
                             all_edges: list[str], duration_s: int) -> None:
    """One shared closure file per scenario. Vehicles whose remaining route
    uses a closed edge recompute when they enter a rerouter edge (the
    closure's neighbourhood)."""
    with open(path, "w") as f:
        f.write("<additional>\n")
        f.write(f'  <rerouter id="closure" edges="{" ".join(all_edges)}">\n')
        f.write(f'    <interval begin="0" end="{duration_s + 3600}">\n')
        for ce in close_edges:
            f.write(f'      <closingReroute id="{ce}" disallow="all"/>\n')
        f.write("    </interval>\n")
        f.write("  </rerouter>\n")
        f.write("</additional>\n")


def demand_variants() -> list[Path]:
    """Calibrated route sets — q50 plus (if built) the q10/q90 direction-
    split variants. Monte Carlo seeds are spread over them so the seed
    spread — and the confidence — includes direction uncertainty."""
    paths = [SUMO_DIR / "calibrated.rou.xml"]
    for suffix in ("_v1", "_v2"):
        p = SUMO_DIR / f"calibrated{suffix}.rou.xml"
        if p.exists():
            paths.append(p)
    return paths


def run_sumo(seed: int, route_path: Path, add_paths: list[Path],
             duration_s: int, home: Path, micro: bool = False) -> None:
    # cwd=SUMO_DIR so the edgeData output file (relative in the additional
    # file) lands in sumo/ — inputs must therefore be absolute paths.
    # Mesoscopic by default: our product is 15-min edge flows, which does not
    # need microscopic car-following — meso is ~20x faster (whole-day seed:
    # minutes → seconds), which is what makes interactive closures possible.
    cmd = [
        str(home / "bin" / "sumo"),
        # meso junction control: LIMITED mode only, not fully off. Full
        # control models signal delays from GUESSED (not real) timing plans
        # and measurably throttles below the flows reality (107-N delivery
        # 0.57 with plain junction-control, 0.92 without — both re-verified
        # 2026-07-06, along with --tls.default-type actuated and
        # --meso-tls-penalty, neither of which changed the result: SUMO's
        # mesoscopic engine doesn't model actuated programs at all, and the
        # penalty had no measurable effect either). --meso-junction-control
        # .limited only engages control at junctions actually approaching
        # saturation ("this prevents faulty traffic lights from hindering
        # flow in low-traffic situations" — SUMO docs) — re-measured
        # delivery identical to fully-off (0.822 mean either way) at today's
        # demand, so it costs nothing, while still being ready to model a
        # signal's real capacity limit if a scenario ever pushes a junction
        # into genuine saturation (e.g. a closure that concentrates flow).
        # Re-enable plain (unlimited) junction control when the city
        # provides real signal timings.
        *([] if micro else ["--mesosim", "true",
                            "--meso-junction-control", "true",
                            "--meso-junction-control.limited", "true"]),
        "-n", str(NET_PATH.resolve()),
        "-r", str(route_path.resolve()),
        "-a", ",".join(str(p.resolve()) for p in add_paths),
        "--seed", str(seed),
        "--begin", "0",
        # generous flush: meso insertion queues delay departures (measured
        # ~170 s avg backlog); a short flush silently drops the tail
        "--end", str(duration_s + 3600),
        "--no-step-log", "true",
        "--no-warnings", "true",
        # Vehicles whose destination IS the closed edge have no valid route —
        # drop them instead of aborting (standard for closure studies).
        "--ignore-route-errors", "true",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=str(SUMO_DIR), env={"SUMO_HOME": str(home)},
                             timeout=SUMO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        sys.exit(f"sumo timed out after {SUMO_TIMEOUT_S}s (seed {seed})")
    if res.returncode != 0:
        print(res.stderr[-2000:])
        sys.exit(f"sumo failed (seed {seed})")


def export_trajectories(name: str, route_path: Path, closure_add: list[Path],
                        duration_s: int, home: Path,
                        web_edges: set[str]) -> str | None:
    """One extra meso run with vehroute exit-times → a compact per-vehicle
    edge-timeline the web animates: EVERY DOT IS A REAL SIMULATED VEHICLE
    with its origin, destination, route and congestion-accurate timing.

    Format (indices into a shared edge list, times in whole seconds):
      {"edges": [...], "vehicles": [{"d": depart, "e": [i...], "x": [t...]}]}
    """
    vr_file = SUMO_DIR / f"vehroutes_{name}.xml"
    cmd = [
        str(home / "bin" / "sumo"),
        "--mesosim", "true",
        "--meso-junction-control", "true",
        "--meso-junction-control.limited", "true",
        "-n", str(NET_PATH.resolve()),
        "-r", str(route_path.resolve()),
        *(("-a", ",".join(str(p.resolve()) for p in closure_add))
          if closure_add else ()),
        "--vehroute-output", vr_file.name,
        "--vehroute-output.exit-times", "true",
        "--begin", "0", "--end", str(duration_s + 3600),
        "--no-step-log", "true", "--no-warnings", "true",
        "--ignore-route-errors", "true", "--seed", "1000",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=str(SUMO_DIR), env={"SUMO_HOME": str(home)},
                             timeout=SUMO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"sumo (trajectory export) timed out after {SUMO_TIMEOUT_S}s")
        return None
    if res.returncode != 0:
        print(res.stderr[-800:])
        return None

    edge_index: dict[str, int] = {}
    vehicles = []
    for veh in ET.parse(vr_file).getroot().iter("vehicle"):
        route = veh.find("route")
        if route is None or not route.get("exitTimes"):
            continue
        edges = route.get("edges").split()
        exits = [int(float(t)) for t in route.get("exitTimes").split()]
        if len(edges) != len(exits):
            continue
        # keep only edges the map can draw (all net edges are in the geojson,
        # but guard against internal/unknown ids)
        idxs = []
        for e in edges:
            if e not in edge_index:
                if e not in web_edges:
                    idxs = None
                    break
                edge_index[e] = len(edge_index)
            idxs.append(edge_index[e])
        if not idxs:
            continue
        vehicles.append({"d": int(float(veh.get("depart"))),
                         "e": idxs, "x": exits})

    vehicles.sort(key=lambda v: v["d"])
    inv = [None] * len(edge_index)
    for e, i in edge_index.items():
        inv[i] = e
    traj_name = f"{name}_traj.json"
    with open(OUT_DIR / traj_name, "w") as f:
        json.dump({"edges": inv, "vehicles": vehicles}, f,
                  separators=(",", ":"))
    size_mb = (OUT_DIR / traj_name).stat().st_size / 1e6
    print(f"  trajectories: {len(vehicles)} vehicles → {traj_name} "
          f"({size_mb:.1f} MB)")
    return traj_name


def parse_edgedata(path: Path, n_intervals: int) -> dict[str, np.ndarray]:
    """{edge_id: array of 'entered' counts per 15-min interval}."""
    flows: dict[str, np.ndarray] = {}
    root = ET.parse(path).getroot()
    for interval in root.findall("interval"):
        i = int(float(interval.get("begin")) // 900)
        if i >= n_intervals:
            continue
        for edge in interval.findall("edge"):
            eid = edge.get("id")
            entered = float(edge.get("entered") or 0)
            if eid not in flows:
                flows[eid] = np.zeros(n_intervals)
            flows[eid][i] = entered
    return flows


def main() -> None:
    args = parse_args()
    home = sumo_home()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(SUMO_DIR / "demand_meta.json") as f:
        meta = json.load(f)
    n_intervals = meta["n_intervals"]
    duration_s  = n_intervals * 900
    sig = demand_signature(meta)

    prior, names = load_geojson_meta()
    for ce in args.close:
        if ce not in prior:
            sys.exit(f"--close {ce}: not an edge in network.geojson")

    if args.name:
        name = args.name
    elif args.close:
        name = "close_" + "+".join(args.close)
        if len(name) > 80:   # many edges → keep the filename sane
            import hashlib
            name = f"close_{len(args.close)}edges_" + \
                   hashlib.sha1("+".join(args.close).encode()).hexdigest()[:8]
    else:
        name = "baseline"

    if args.close:
        streets = sorted({names[ce] for ce in args.close})
        label = "Avstängning: " + ", ".join(streets)
    else:
        label = "Baslinje (ingen avstängning)"

    # Edge list for the rerouter (net edge IDs = plain edge IDs, no internals)
    net_edges = [e.get("id") for e in ET.parse(NET_PATH).getroot().findall("edge")
                 if not e.get("function")]

    print(f"Scenario '{name}'  ({label})  —  {args.seeds} seeds × {n_intervals} × 15 min")

    variants = demand_variants()
    if len(variants) > 1:
        print(f"  {len(variants)} demand variants (q50 + direction-split bounds)")

    closure_add: list[Path] = []
    if args.close:
        rerouter_edges = edges_near(args.close, REROUTER_RADIUS_M)
        print(f"  rerouter on {len(rerouter_edges)} edges within "
              f"{REROUTER_RADIUS_M} m of the closure")
        cpath = SUMO_DIR / f"closure_{name}.add.xml"
        write_closure_additional(cpath, args.close, rerouter_edges, duration_s)
        closure_add = [cpath]

    per_seed: list[dict[str, np.ndarray]] = []
    for s in range(args.seeds):
        seed = 1000 + s
        route_path = variants[s % len(variants)]
        ed_file  = SUMO_DIR / f"edgedata_{name}_{seed}.xml"
        add_path = SUMO_DIR / f"additional_{name}_{seed}.add.xml"
        write_edgedata_additional(add_path, ed_file, duration_s)
        run_sumo(seed, route_path, [add_path] + closure_add, duration_s, home,
                 micro=args.micro)
        per_seed.append(parse_edgedata(ed_file, n_intervals))
        print(f"  seed {seed} ({route_path.name}): "
              f"{len(per_seed[-1])} edges with traffic")

    # ── Aggregate: mean flows + Monte Carlo confidence ─────────────────────────
    web_edges = set(prior)   # only edges the map can draw
    all_ids   = set().union(*per_seed) & web_edges

    flows_out: dict[str, list[int]] = {}
    conf_out:  dict[str, float] = {}
    for eid in sorted(all_ids):
        stack = np.stack([ps.get(eid, np.zeros(n_intervals)) for ps in per_seed])
        mean  = stack.mean(axis=0)
        flows_out[eid] = [int(round(v)) for v in mean]

        busy = mean > 2           # CV is meaningless for near-zero flows
        cv   = float((stack.std(axis=0)[busy] / mean[busy]).mean()) if busy.any() else 0.0
        conf_out[eid] = round(prior[eid] * float(np.exp(-cv)), 3)

    traj_name = None
    if not args.no_trajectories:
        traj_name = export_trajectories(name, variants[0], closure_add,
                                        duration_s, home, web_edges)

    payload = {
        "epoch":            meta["epoch_sim"],
        "interval_minutes": 15,
        "n_quarters":       n_intervals,
        "generated_at":     pd.Timestamp.now().isoformat(timespec="seconds"),
        "trajectories":     traj_name,
        "scenario": {
            "name": name, "label": label,
            "closed_edges": args.close,
            "date": meta["date"], "source": meta.get("source", "historical"),
            "begin": meta["begin"], "end": meta["end"],
            "seeds": args.seeds,
            "demand_signature": sig,
        },
        "flows":      flows_out,
        "confidence": conf_out,
    }
    out_path = OUT_DIR / f"{name}.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"Wrote {out_path}  ({len(flows_out)} edges)")

    # ── Manifest ───────────────────────────────────────────────────────────────
    index_path = OUT_DIR / "index.json"
    index = json.load(open(index_path)) if index_path.exists() else {"scenarios": []}
    index = index_for_current_demand(index, sig)
    index["scenarios"] = [s for s in index["scenarios"] if s["name"] != name]
    src_tag = " · Prognos" if meta.get("source") == "forecast" else ""
    index["scenarios"].append({
        "name": name, "label": label, "file": f"{name}.json",
        "closed_edges": args.close,
        "demand_signature": sig,
        "window": f"{meta['date']} {meta['begin']}–{meta['end']}{src_tag}",
    })
    index["scenarios"].sort(key=lambda s: s["name"])
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    print(f"Updated {index_path}  ({len(index['scenarios'])} scenarios)")


if __name__ == "__main__":
    main()
