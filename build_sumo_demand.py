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
import multiprocessing as mp
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from build_sumo_net import sumo_home
from train_agent1 import HOLIDAY_DATES_2025
from build_agent1_flows import HOLIDAY_MAPPING_2027_TO_2025

FLOWS_PATH          = Path("web/data/flows.json")
FLOWS_FORECAST_PATH = Path("web/data/flows_forecast.json")
GEO_PATH   = Path("web/data/network.geojson")
SUMO_DIR   = Path("sumo")
NET_PATH   = SUMO_DIR / "net.net.xml"
SCEN_DIR   = Path("web/data/scenarios")

INTERVAL = pd.Timedelta(minutes=15)

# Bounds/priors/corridor coupling encode STRUCTURAL relationships (network
# conservation math, learned direction-shares, spatial corridor ratios) —
# not date-specific facts. There's no network-wide "2027 historical" data
# to recompute them from (the forecast only has point estimates AT the 6
# sensors), so simulating a forecast date reuses these as-is from a fixed
# real reference date rather than trying to derive them from the forecast.
STRUCTURAL_REFERENCE_DATE = "2025-09-16"

# Candidate-pool density: one random trip every N seconds of the window.
# The pool needs route DIVERSITY, not volume — routeSampler repeats routes.
CANDIDATE_PERIOD_S = 2.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--date", default=None,
                   help="Backward-compatible alias for --start-date DATE --days 1")
    p.add_argument("--start-date", default=None,
                   help="First simulation date, YYYY-MM-DD (default: Tue 2025-09-16)")
    p.add_argument("--days", type=int, default=1,
                   help="Number of consecutive calendar days (default: 1; multi-day build is B2)")
    p.add_argument("--source", choices=["historical", "forecast"], default="historical",
                   help="historical = calibrate against actual 2025 sensor "
                        "counts (--date must be in 2025); forecast = "
                        "calibrate against Agent 1's LightGBM forecast for "
                        "2027 (--date must be in 2027) — 'simulate the "
                        "future'. Bounds/priors/corridor coupling are "
                        "structural and always come from the real 2025-09-16 "
                        "reference regardless of --source (see "
                        "STRUCTURAL_REFERENCE_DATE).")
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
                   help="θ passed to build_candidates.py. CORRECTED "
                        "2026-07-08: this had claimed since 2026-07-05 to be "
                        "'frozen from a trip-length fit against RVU' — that "
                        "fit was never actually implemented (calibrate_theta.py "
                        "only ever did GEH-based scoring, which saturates at "
                        "100% and carries no signal). Now that the real fit "
                        "exists (build_candidates.trip_length_fit), 2.6 sits "
                        "near a real but shallow optimum (L1=0.59, vs 0.58 "
                        "best found up to 12km) — a genuine, if modest, "
                        "improvement over the untested claim, not a precise "
                        "calibration. See calibrate_theta.py's docstring for "
                        "why the RVU 5.1-10km/>10km bins cannot be matched "
                        "regardless of θ: this network's own diameter is "
                        "~7.8 km, a hard geometric ceiling.")
    p.add_argument("--cross-fraction", type=float, default=0.3,
                   help="θ passed to build_candidates.py: share of tours "
                        "that are E-I/I-E cross-boundary commuting (one end "
                        "at a gate) rather than pure I-I. Disclosed-"
                        "unidentifiable neutral prior, same status as "
                        "through-fraction.")
    p.add_argument("--no-assignment-prior", action="store_true",
                   help="Disable the weak gravity-assignment prior "
                        "(assignment_priors.py) — kept for the controlled "
                        "A/B comparison; the prior is on by default.")
    p.add_argument("--congestion-iterations", type=int, default=1,
                   help="Re-generate candidate routes against each "
                        "iteration's own MEASURED (congested) travel time "
                        "and re-solve PFE, instead of routing once against "
                        "free-flow cost and freezing it (research review "
                        "2026-07-08: simultaneous count+equilibrium "
                        "calibration beats one-shot sequential). Stops early "
                        "if GEH stabilizes. DEFAULT IS 1 (today's exact "
                        "one-shot behaviour, no extra runtime) because each "
                        "extra iteration re-runs the full whole-day PFE "
                        "solve (~7-19 min observed) plus a feedback step — "
                        "raise this deliberately, and raise serve.py's "
                        "/api/recalibrate subprocess timeout to match if "
                        "triggering it through the web app.")
    p.add_argument("--congestion-method", choices=["bpr", "simulate"], default="bpr",
                   help="How each congestion-feedback iteration measures "
                        "travel time. 'bpr' (default): the standard Bureau-"
                        "of-Public-Roads volume-delay function computed "
                        "directly from PFE's own solved flow — ~1000x "
                        "cheaper than a real simulation, which is what "
                        "makes enough iterations to actually converge "
                        "(literature: MSA/Frank-Wolfe typically need 10-25+, "
                        "not 1-2) practical. 'simulate': a real meso pass "
                        "per iteration (more accurate per step, far slower — "
                        "use for a final accuracy check, not routine runs).")
    args = p.parse_args()
    if args.days < 1:
        p.error("--days must be at least 1")
    if args.date is not None and args.start_date is not None:
        p.error("use either --date or --start-date, not both")
    if args.date is not None and args.days != 1:
        p.error("--date is an alias for --start-date DATE --days 1")
    args.start_date = args.start_date or args.date or "2025-09-16"
    return args


def validate_date_range(start_date: str, days: int, source_year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return [start, end) after requiring the whole calendar range in one year."""
    if days < 1:
        raise ValueError("--days must be at least 1")
    try:
        start = pd.Timestamp(start_date)
    except (TypeError, ValueError):
        raise ValueError(f"--start-date must be YYYY-MM-DD, got {start_date!r}") from None
    if start.strftime("%Y-%m-%d") != start_date:
        raise ValueError(f"--start-date must be YYYY-MM-DD, got {start_date!r}")
    end_exclusive = start + pd.Timedelta(days=days)
    year_end = pd.Timestamp(year=source_year + 1, month=1, day=1)
    if start.year != source_year or end_exclusive > year_end:
        raise ValueError(
            f"date range {start.date()} through {(end_exclusive - pd.Timedelta(days=1)).date()} "
            f"crosses or lies outside the {source_year} source year")
    return start, end_exclusive


def demand_metadata(*, start_date: str, days: int, source: str, begin: str,
                    end: str, qi_start: int, n_intervals: int,
                    epoch_sim: pd.Timestamp, direction_split: str,
                    n_variants: int) -> dict:
    """Demand metadata contract; B2 will make multi-day calibration consume it."""
    start, end_exclusive = validate_date_range(start_date, days, epoch_sim.year)
    meta = {
        "start_date": start.strftime("%Y-%m-%d"),
        "days": days,
        "end_date_exclusive": end_exclusive.strftime("%Y-%m-%d"),
        "day_boundaries_s": [day * 86400 for day in range(days + 1)],
        "day_kinds": [classify_day(day.strftime("%Y-%m-%d"), day.dayofweek)[1]
                      for day in pd.date_range(start, periods=days, freq="D")],
        "source": source,
        "qi_start": qi_start,
        "n_intervals": n_intervals,
        # ISO with 'T' — Safari/Firefox reject "YYYY-MM-DD HH:MM" in new Date()
        "epoch_sim": epoch_sim.isoformat(),
        "direction_split": direction_split,
        "n_variants": n_variants,
        "note": "Total sensor counts split over the two directed edges using "
                "the estimated time-of-day split (estimate_directions.py); "
                "direction is not measured in the delivered data.",
    }
    # Legacy consumers deliberately retain their exact single-day fields.
    if days == 1:
        meta.update({"date": start_date, "begin": begin, "end": end})
    return meta


def classify_day(date_str: str, dayofweek: int) -> tuple[bool, str]:
    """(use_weekend_shape, day_kind) for build_candidates.py's departure-
    time profile choice. A holiday on a weekday (Midsommarafton, Juldagen,
    ...) has nothing like a normal commute peak either — normal_profile.json
    has no separate holiday shape to read, so 'weekend' (later start, no
    sharp AM/PM peaks) is the closest real analog available, reusing Agent
    1's own HOLIDAY_DATES_2025/HOLIDAY_MAPPING_2027_TO_2025 rather than
    re-deciding what a holiday is a second time. Found 2026-07-09: the first
    weekday/weekend fix didn't check this, so a holiday Tuesday would still
    get the sharp commute shape. dayofweek: pandas convention, Mon=0..Sun=6."""
    is_weekend = dayofweek >= 5
    is_holiday = date_str in HOLIDAY_DATES_2025 or date_str in HOLIDAY_MAPPING_2027_TO_2025
    if is_weekend:
        return True, "weekend"
    if is_holiday:
        return True, "holiday"
    return False, "weekday"


REAL_DAY_SHAPE_MIN_VALID_HOURS = 18   # of 24 — below this, the day's own
                                     # data is too gappy to trust at all


def real_day_shape(flows: dict[str, list], sensor_edges: dict[str, list[str]],
                   qi_start: int) -> np.ndarray | None:
    """The REAL (or, for --source forecast, Agent 1's forecast) departure-
    time shape measured at the 6 sensors on the EXACT calendar day being
    simulated — not a bucket average. Directly captures whatever actually
    happened that day (a holiday, a school break that isn't a public
    holiday, a snow day, a local event, ...) without needing a maintained
    holiday list or any day-type classification at all: the real data
    already IS the classification. Falls back to None (caller blends with
    classify_day()'s smoothed average, or uses it outright) if too much of
    the day is missing to trust a single day's measurement.

    qi_start may point anywhere inside the target day (e.g. a 06:00-10:00
    window's start) — this always pulls the FULL 96-quarter day containing
    it, since departure-time shape must cover all 24 hours regardless of
    the calibration window."""
    day_qi_start = qi_start - (qi_start % 96)
    hourly = np.zeros(24)
    valid_hours = np.zeros(24, dtype=bool)
    for edges in sensor_edges.values():
        for e in edges:
            arr = flows.get(e, [])
            for h in range(24):
                qis = range(day_qi_start + h * 4, day_qi_start + h * 4 + 4)
                vals = [arr[qi] for qi in qis if qi < len(arr) and arr[qi] is not None]
                if vals:
                    hourly[h] += sum(vals) / len(vals)
                    valid_hours[h] = True
    if valid_hours.sum() < REAL_DAY_SHAPE_MIN_VALID_HOURS or hourly.sum() <= 0:
        return None
    return hourly / hourly.sum()


def multi_day_blocks(flows: dict[str, list], sensor_edges: dict[str, list[str]],
                     start: pd.Timestamp, days: int, qi_start: int) -> list[dict]:
    """Candidate-generator blocks with each calendar day's own profile.

    Geometry is pooled by the generator's actual behavioural day type, while
    profiles are intentionally not pooled: every block retains its exact-day
    measured/forecast departure shape.
    """
    from build_candidates import blend_day_shape, daily_shape

    blocks = []
    for day_index in range(days):
        day = start + pd.Timedelta(days=day_index)
        weekend, kind = classify_day(day.strftime("%Y-%m-%d"), day.dayofweek)
        real = real_day_shape(flows, sensor_edges, qi_start + day_index * 96)
        fallback = daily_shape(weekend)
        profile = blend_day_shape(real, fallback) if real is not None else fallback
        blocks.append({
            "profile": profile.tolist(), "offset_s": day_index * 86400,
            "id_prefix": f"d{day_index}_", "is_weekend": weekend,
            # Purpose logic is the same for weekend and holiday blocks, so
            # that is the safe geometry-reuse boundary.
            "pool_key": "weekend" if weekend else "weekday",
        })
        origin = "real" if real is not None else "fallback"
        print(f"  day {day.strftime('%Y-%m-%d')} ({kind}): {origin} departure shape")
    return blocks


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
                         capture_output=True, text=True, timeout=1200)
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
                             capture_output=True, text=True, timeout=1200)
        if res.returncode != 0:
            print(res.stderr[-800:])
            return {"weight": 0.0, "flows": {}}
    with open(path) as f:
        return json.load(f)


def clear_stale_scenarios() -> int:
    """Remove web scenarios generated from an older calibrated demand.

    A demand rebuild changes the route file that every scenario simulates.
    Leaving old baseline/closure JSON in web/data/scenarios makes the UI
    list scenarios that look current but were produced from the previous
    date/window/source. serve.py already did this for web-triggered
    recalibration; doing it here makes the CLI path equally safe.

    Deletes index.json along with the scenario files (it's as stale as
    they are), but immediately replaces it with an empty manifest rather
    than leaving it missing: web/index.html fetches index.json directly,
    and serve.py's version of this cleanup gets away with a momentary gap
    only because it always calls run_scenario.py right after — this CLI
    path has no such guarantee (`make demand` and `make scenario` are
    separate targets), so a missing manifest could sit there until the
    next `make scenario` run.
    """
    if not SCEN_DIR.exists():
        return 0
    n = 0
    for path in SCEN_DIR.glob("*.json"):
        path.unlink()
        n += 1
    with open(SCEN_DIR / "index.json", "w") as f:
        json.dump({"scenarios": []}, f, indent=2)
    return n


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
                         capture_output=True, text=True, timeout=1200)
    if res.returncode != 0:
        print(res.stderr[-1000:])
        print("  (no priors available — continuing without level 3)")
        return {"edges": {}}
    with open(path) as f:
        return json.load(f)


def structural_bounds_and_priors(begin: str, end: str) -> tuple[dict, dict]:
    """Load date-invariant structural inputs, never target-date inputs."""
    return (ensure_bounds(STRUCTURAL_REFERENCE_DATE, begin, end),
            ensure_priors(STRUCTURAL_REFERENCE_DATE))


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

    if "date" in meta:
        window = f"{meta['date']} {meta['begin']}–{meta['end']}"
    else:
        window = f"{meta['start_date']} → {meta['end_date_exclusive']} " \
                 f"({meta['days']} days)"

    out_json = Path("web/data/od_matrix.json")
    with open(out_json, "w") as f:
        json.dump({
            "window":  window,
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


FEEDBACK_SIM_TIMEOUT_S = 300


def run_feedback_simulation(route_path: Path, duration_s: int, home: Path,
                            iteration: int) -> Path:
    """One meso pass over the CURRENT calibrated routes, aggregated into a
    single interval spanning the whole window, to get each edge's actual
    (congestion-adjusted) travel time. This is the missing half of the
    calibration: PFE picks route USE COUNTS to match sensor totals, but the
    candidate routes it picks from were generated against FREE-FLOW cost —
    a route that's actually congested under the calibrated demand never
    gets deprioritized. Feeding this back into duarouter (as --weight-files)
    for the next candidate-generation pass closes that loop.

    Research check (2026-07-08): simultaneous count+equilibrium calibration
    is documented to significantly outperform a one-shot sequential run —
    hence iterating this a few times rather than doing it once and freezing.
    """
    ed_file = SUMO_DIR / f"feedback_edgedata_{iteration}.xml"
    add_file = SUMO_DIR / f"feedback_additional_{iteration}.add.xml"
    with open(add_file, "w") as f:
        f.write(f'<additional><edgeData id="fb" file="{ed_file.name}" '
                f'begin="0" end="{duration_s}"/></additional>\n')
    cmd = [
        str(home / "bin" / "sumo"),
        "--mesosim", "true",
        "--meso-junction-control", "true",
        "--meso-junction-control.limited", "true",
        "-n", str(NET_PATH.resolve()),
        "-r", str(route_path.resolve()),
        "-a", str(add_file.resolve()),
        "--begin", "0", "--end", str(duration_s + 3600),
        "--no-step-log", "true", "--no-warnings", "true",
        "--ignore-route-errors", "true", "--seed", "1000",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=str(SUMO_DIR), env={"SUMO_HOME": str(home)},
                             timeout=FEEDBACK_SIM_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        sys.exit(f"feedback simulation timed out after {FEEDBACK_SIM_TIMEOUT_S}s")
    if res.returncode != 0:
        print(res.stderr[-2000:])
        sys.exit("feedback simulation failed")
    return ed_file


# BPR (Bureau of Public Roads 1964) volume-delay function: t = t_free *
# (1 + alpha*(v/c)^beta) — the standard closed-form Frank-Wolfe/MSA traffic
# assignment travel-time estimate, computed directly from PFE's own solved
# flow with NO extra simulation needed. ~1000x cheaper per iteration than
# run_feedback_simulation (a real meso pass), which is what makes iterating
# enough times to actually converge (research: MSA/Frank-Wolfe typically
# need 10-25+ iterations, not 1-2) practical instead of prohibitively slow.
BPR_ALPHA = 0.15
BPR_BETA  = 4.0
# HCM-consistent effective urban-street capacity per lane (veh/h) — signals/
# turning movements make this well below highway free-flow capacity (~1900
# pcu/h/lane); matches build_sumo_net.py's DEFAULT_SPEED_KMH/DEFAULT_LANES
# road-type granularity.
CAPACITY_PER_LANE_VPH = {
    "motorway": 1900, "motorway_link": 1500, "trunk": 1700, "trunk_link": 1300,
    "primary": 900, "primary_link": 800, "secondary": 800, "secondary_link": 700,
    "tertiary": 700, "tertiary_link": 600,
    "residential": 500, "living_street": 300, "unclassified": 600,
}
DEFAULT_CAPACITY_PER_LANE_VPH = 600


BPR_PERIOD_S = 3600.0   # 1 hour: fine enough to catch a rush-hour peak
                        # without so many periods duarouter's routing table
                        # rebuild (--weight-period) gets expensive.


def bpr_travel_times(achieved: dict[str, list[float]], quarter_s: float = 900.0,
                     net_path: Path = NET_PATH, geo_path: Path = GEO_PATH,
                     period_s: float = BPR_PERIOD_S,
                     ) -> dict[str, list[float]]:
    """{edge_id: [travel_time_s per period_s-sized period]} from PFE's own
    achieved per-quarter flow — no simulation needed. A SINGLE flat average
    over the whole calibration window (the first version of this function)
    dilutes a sharp rush-hour peak into a mild multi-hour average — real
    dynamic/time-dependent traffic assignment practice computes this PER
    PERIOD, which is what lets duarouter (--weight-period) route trips
    against the congestion that's actually present when they depart,
    instead of a watered-down daily mean. Free-flow time + lane count come
    from the SUMO net (the routing graph itself); road type (for capacity)
    from network.geojson."""
    net = ET.parse(net_path).getroot()
    freeflow: dict[str, float] = {}
    lanes: dict[str, int] = {}
    for edge in net.findall("edge"):
        if edge.get("function") == "internal":
            continue
        lane_els = edge.findall("lane")
        if not lane_els:
            continue
        eid = edge.get("id")
        length = float(lane_els[0].get("length"))
        speed = float(lane_els[0].get("speed"))
        freeflow[eid] = length / speed if speed > 0 else 1.0
        lanes[eid] = len(lane_els)

    highway: dict[str, str] = {}
    with open(geo_path) as f:
        for feat in json.load(f)["features"]:
            p = feat["properties"]
            highway[p["id"]] = p.get("highway") or "unclassified"

    quarters_per_period = max(1, round(period_s / quarter_s))
    tt: dict[str, list[float]] = {}
    for eid, series in achieved.items():
        if eid not in freeflow:
            continue
        cap_per_lane = CAPACITY_PER_LANE_VPH.get(highway.get(eid),
                                                 DEFAULT_CAPACITY_PER_LANE_VPH)
        capacity = max(1, lanes[eid]) * cap_per_lane
        t_free = freeflow[eid]
        periods = []
        for start in range(0, len(series), quarters_per_period):
            chunk = series[start:start + quarters_per_period]
            v_per_hour = sum(chunk) / (len(chunk) * quarter_s / 3600.0)
            periods.append(t_free * (1 + BPR_ALPHA * (v_per_hour / capacity) ** BPR_BETA))
        tt[eid] = periods
    return tt


def damp_travel_times(
    new: dict[str, list[float]], prev: dict[str, list[float]] | None,
    iteration: int,
) -> dict[str, list[float]]:
    """Method of Successive Averages step, applied per period: blend this
    iteration's estimate with the running average instead of fully
    replacing it. A full replacement each round is a known-worse
    convergence strategy than even plain MSA (let alone Frank-Wolfe) — it
    can oscillate between two congestion patterns instead of settling.
    Step size 1/(iteration+1) is the classic MSA schedule."""
    if prev is None:
        return {eid: list(periods) for eid, periods in new.items()}
    step = 1.0 / (iteration + 1)
    out = {eid: list(periods) for eid, periods in prev.items()}
    for eid, new_periods in new.items():
        prev_periods = prev.get(eid, new_periods)
        out[eid] = [(1 - step) * p + step * n
                   for p, n in zip(prev_periods, new_periods)]
    return out


def write_weight_file(travel_times: dict[str, list[float]], out_path: Path,
                      period_s: float = BPR_PERIOD_S) -> None:
    """meandata XML, one <interval> per period — the format
    run_feedback_simulation's real edgeData output also produces (as
    consecutive 900s intervals), so duarouter -w/--weight-attribute
    traveltime --weight-period reads either interchangeably."""
    n_periods = max((len(p) for p in travel_times.values()), default=0)
    with open(out_path, "w") as f:
        f.write("<meandata>\n")
        for i in range(n_periods):
            f.write(f'  <interval begin="{i * period_s:.2f}" '
                    f'end="{(i + 1) * period_s:.2f}">\n')
            for eid, periods in travel_times.items():
                if i < len(periods):
                    f.write(f'    <edge id="{eid}" traveltime="{periods[i]:.2f}"/>\n')
            f.write("  </interval>\n")
        f.write("</meandata>\n")


def run_tool(script: str, args: list[str], home: Path) -> None:
    cmd = [sys.executable, str(home / "tools" / script), *args]
    env = {
        "SUMO_HOME": str(home),
        "PATH": f"{home / 'bin'}:/usr/bin:/bin",
        "HOME": str(Path.home()),
    }
    res = subprocess.run(cmd, capture_output=True, text=True, env=env,
                         timeout=1200)
    tail = (res.stdout + res.stderr)[-2500:]
    print(tail)
    if res.returncode != 0:
        sys.exit(f"{script} failed")


_PFE_PAR_SHAPES = None
_PFE_PAR_ROUTE_COST = None


def _run_pfe_interval_job(job: dict):
    """ProcessPool worker for one independent (variant, quarter) PFE solve.

    The shared shape pool and route-cost vector are inherited by fork, so the
    heavy candidate geometry is not pickled once per quarter.
    """
    import pfe

    if _PFE_PAR_SHAPES is None or _PFE_PAR_ROUTE_COST is None:
        raise RuntimeError("PFE interval worker was not initialized")
    sol = pfe.solve_interval_with_relaxation(
        _PFE_PAR_SHAPES,
        job["targets"],
        job["bounds"],
        job["priors"],
        route_cost=_PFE_PAR_ROUTE_COST,
    )
    return job["suffix"], job["key"], job["quarter"], sol


def run_pfe_variants_flat_parallel(cand_path: Path, variants: list[tuple[str, str]],
                                  variant_inputs: dict[str, dict],
                                  max_workers: int | None = None) -> dict[str, dict]:
    """Solve all final direction variants through one flat worker pool.

    This avoids nesting multiprocessing pools: the unit of parallel work is one
    15-minute interval, across all variants, and route files are written only
    after every solution has been collected in deterministic quarter order.
    """
    import pfe

    global _PFE_PAR_SHAPES, _PFE_PAR_ROUTE_COST
    shapes, route_cost = pfe.prepare_calibration(cand_path)
    _PFE_PAR_SHAPES = shapes
    _PFE_PAR_ROUTE_COST = route_cost
    try:
        tasks = []
        solutions = {}
        for suffix, key in variants:
            data = variant_inputs[suffix]
            nq = len(data["targets"])
            solutions[suffix] = [None] * nq
            for i in range(nq):
                tasks.append({
                    "suffix": suffix,
                    "key": key,
                    "quarter": i,
                    "targets": data["targets"][i],
                    "bounds": data["bounds_pq"][i],
                    "priors": data["priors_pq"][i],
                })

        n_workers = min(max_workers or (os.cpu_count() or 1), len(tasks))
        print(f"  PFE final variants: solving {len(tasks)} independent "
              f"variant×quarter intervals in one pool ({n_workers} workers)")
        with mp.get_context("fork").Pool(processes=n_workers) as pool:
            for suffix, _key, quarter, sol in pool.imap_unordered(
                _run_pfe_interval_job, tasks
            ):
                solutions[suffix][quarter] = sol

        reports = {}
        for suffix, key in variants:
            data = variant_inputs[suffix]
            reports[suffix] = pfe.write_calibration_report(
                shapes, data["out_path"], data["targets"], solutions[suffix],
                data["bounds_pq"])
            if not data.get("keep_achieved", False):
                reports[suffix] = {
                    k: v for k, v in reports[suffix].items() if k != "achieved"
                }
        return reports
    finally:
        _PFE_PAR_SHAPES = None
        _PFE_PAR_ROUTE_COST = None


def warn_unserviceable_measured_edges(report: dict, label: str) -> None:
    """Make missing candidate coverage visible without blocking a demand run.

    This intentionally follows the existing non-blocking GEH gate: a route
    pool defect must be unmistakable to the operator, while still preserving
    the usable output for the remaining measured edges.
    """
    edges = report.get("unserviceable_edges", [])
    if edges:
        print(f"  ⚠ UNSERVICEABLE MEASURED EDGES ({label}): "
              f"{', '.join(edges)} — no candidate route can serve these "
              "hard measurements; regenerate/fix the candidate pool.")


def warn_bound_violations(report: dict, label: str) -> None:
    """Surface a structural gap found in a bug review 2026-07-10: the
    integer-rounding step (round_preserving_measured, pfe.py) has no
    visibility into level-2 bounds, so a route shared between a measured
    edge and a separately-bounded edge can be nudged in a way that pushes
    the bounded edge's rounded total outside its own bound. Diagnostic
    only — reports the condition without blocking the run or attempting
    a repair (that needs its own careful pass on a function with a
    documented history of subtle failed designs)."""
    violations = report.get("bound_violations", [])
    if violations:
        sample = ", ".join(
            f"{v['edge']}@q{v['quarter']} ({v['achieved']:.0f} vs "
            f"[{v['bound_lo']:.0f},{v['bound_hi']:.0f}])"
            for v in violations[:5])
        more = f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""
        print(f"  ⚠ BOUND VIOLATIONS FROM INTEGER ROUNDING ({label}): "
              f"{len(violations)} edge-quarters exceed their level-2 bound "
              f"after rounding — {sample}{more}. The continuous solution "
              "respected these bounds; only the final integer rounding "
              "does not currently check them (known gap, diagnostic only).")


def main() -> None:
    args = parse_args()
    flows_path = FLOWS_FORECAST_PATH if args.source == "forecast" else FLOWS_PATH
    with open(flows_path) as f:
        flows_payload = json.load(f)
    source_epoch = pd.Timestamp(flows_payload["epoch"])
    flows        = flows_payload["flows"]

    try:
        range_start, range_end = validate_date_range(args.start_date, args.days, source_epoch.year)
    except ValueError as exc:
        sys.exit(str(exc))
    if not NET_PATH.exists():
        sys.exit("sumo/net.net.xml missing — run build_sumo_net.py first")

    if args.days > 1:
        # Multi-day demand is a continuous sequence of complete calendar
        # days. The legacy begin/end window remains exactly as-is for one day.
        t0, t1 = range_start, range_end
    else:
        t0 = pd.Timestamp(f"{args.start_date} {args.begin}")
        if args.end == "24:00":   # whole day — pandas rejects hour 24
            t1 = t0.normalize() + pd.Timedelta(days=1)
        else:
            t1 = pd.Timestamp(f"{args.start_date} {args.end}")
    qi_start    = int((t0 - source_epoch) / INTERVAL)
    n_intervals = int((t1 - t0) / INTERVAL)
    duration_s  = n_intervals * 900
    use_weekend_shape, day_kind = classify_day(args.start_date, t0.dayofweek)
    print(f"Window: {t0} → {t1}  ({n_intervals} × 15 min)  source={args.source}"
          f"  {day_kind}")

    sensor_edges = load_sensor_edges()
    print(f"Sensors: { {sid: len(e) for sid, e in sensor_edges.items()} }")

    # Prefer the day's OWN measured (or, for --source forecast, Agent 1's
    # forecast) shape over a generic weekday/weekend/holiday bucket average
    # — it directly reflects whatever actually happened/will happen that
    # exact date (captures e.g. a school-break Friday that isn't a public
    # holiday, with no list to maintain) rather than an assumption about it.
    real_shape = real_day_shape(flows, sensor_edges, qi_start)
    day_shape_path = None
    if real_shape is not None:
        day_shape_path = SUMO_DIR / "real_day_shape.json"
        day_shape_path.write_text(json.dumps(real_shape.tolist()))
        print(f"  real day-shape: {int((real_shape > 0).sum())}/24 hours with "
              f"data — blended with the {day_kind} fallback in build_candidates.py")
    else:
        print(f"  real day-shape: too sparse, using {day_kind} fallback only")

    day_blocks_path = None
    if args.days > 1:
        day_blocks_path = SUMO_DIR / "candidate_day_blocks.json"
        day_blocks_path.write_text(json.dumps(
            multi_day_blocks(flows, sensor_edges, range_start, args.days, qi_start)))

    home = sumo_home()

    cand_path = SUMO_DIR / "candidates.rou.xml"

    def generate_candidates(weight_file: Path | None = None) -> None:
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
            # A multi-day PFE needs additional time coverage, not a fresh
            # 12k route geometries per day. build_candidates reuses one pool
            # per behavioural day type and only re-samples day departures.
            n_total = 12000 if args.days > 1 else max(6000, int(12000 * duration_s / 86400))
            cmd = [sys.executable, "build_candidates.py",
                  "--through-fraction", str(args.through_fraction),
                  "--gravity-km", str(args.gravity_km),
                  "--cross-fraction", str(args.cross_fraction),
                  "--n-total", str(n_total), "--seed", str(args.seed)]
            if use_weekend_shape:
                cmd += ["--is-weekend"]
            if day_shape_path is not None:
                cmd += ["--real-day-shape-file", str(day_shape_path)]
            if day_blocks_path is not None:
                cmd += ["--day-blocks-file", str(day_blocks_path)]
            if weight_file is not None:
                cmd += ["--weight-file", str(weight_file),
                       "--weight-period", str(BPR_PERIOD_S)]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
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
        # Structural (see STRUCTURAL_REFERENCE_DATE) — always the real 2025
        # reference date, even when simulating a --source forecast date.
        bounds_data, priors_data = structural_bounds_and_priors(args.begin, args.end)
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

        def build_bounds_priors(suffix: str) -> tuple[list[dict], list[dict]]:
            bounds_pq, priors_pq = [], []
            for i in range(n_intervals):
                bq = {}
                for e, arr in bounds_data["bounds"].items():
                    # Bounds are structural reference-day relationships;
                    # repeat their 96 time-of-day slots for each target day.
                    slot_i = i % 96
                    if slot_i < len(arr) and arr[slot_i]:
                        bq[e] = (arr[slot_i][0], arr[slot_i][1])
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
            return bounds_pq, priors_pq

        # ── Congestion-feedback loop (primary "" / q50 variant only) ──────────
        # PFE picks route USE COUNTS to match sensor totals, but the candidate
        # routes it picks from were generated against FREE-FLOW cost — a route
        # that's actually congested under the calibrated demand never gets
        # deprioritized (one-shot sequential: calibrate once, freeze). Research
        # (2026-07-08 review) shows SIMULTANEOUS count+equilibrium calibration
        # significantly beats a one-shot sequential run, so re-generate
        # candidates against each iteration's own MEASURED travel time and
        # re-solve, stopping once GEH stabilizes. Skipped for
        # --legacy-random-pool (randomTrips has no weight-file support).
        n_iter = 1 if args.legacy_random_pool else args.congestion_iterations
        weight_file = None
        prev_geh = None
        prev_tt_raw = None    # last iteration's un-damped BPR estimate
        damped_tt = None      # MSA running average actually written out
        report = None
        for iteration in range(n_iter):
            generate_candidates(weight_file)
            if iteration == n_iter - 1:
                if len(variants) > 1:
                    variant_inputs = {}
                    for suffix, key in variants:
                        targets = build_targets(flows, sensor_edges, qi_start,
                                                n_intervals, split_key=key)
                        bounds_pq, priors_pq = build_bounds_priors(suffix)
                        out = calib_path if suffix == "" else SUMO_DIR / f"calibrated{suffix}.rou.xml"
                        variant_inputs[suffix] = {
                            "out_path": out,
                            "targets": targets,
                            "bounds_pq": bounds_pq,
                            "priors_pq": priors_pq,
                            "keep_achieved": False,
                        }
                    reports = run_pfe_variants_flat_parallel(
                        cand_path, variants, variant_inputs,
                        max_workers=os.cpu_count() or 1)
                    for suffix, key in variants:
                        variant_report = reports[suffix]
                        label = "PFE" if suffix == "" and n_iter == 1 else (
                            f"[congestion-feedback {iteration+1}/{n_iter}]"
                            if suffix == "" else "PFE"
                        )
                        print(f"  {label} {key:<16} {variant_report['vehicles']:>6} veh  "
                              f"GEH<5: {variant_report['geh_pct']}%  "
                              f"(infeasible intervals: {variant_report['infeasible_intervals']})")
                        warn_unserviceable_measured_edges(variant_report, key)
                        warn_bound_violations(variant_report, key)
                        if variant_report["geh_pct"] < 100:
                            print("  ⚠ measured-edge fit below gate — inspect before use")
                    report = reports[""]
                else:
                    targets = build_targets(flows, sensor_edges, qi_start,
                                            n_intervals, split_key="edge_shares")
                    bounds_pq, priors_pq = build_bounds_priors("")
                    report = pfe.calibrate(cand_path, calib_path, targets,
                                           bounds_pq, priors_pq)
                    tag = f"[congestion-feedback {iteration+1}/{n_iter}]" if n_iter > 1 else "PFE"
                    print(f"  {tag} edge_shares       {report['vehicles']:>6} veh  "
                          f"GEH<5: {report['geh_pct']}%  "
                          f"(infeasible intervals: {report['infeasible_intervals']})")
                    warn_unserviceable_measured_edges(report, "edge_shares")
                    warn_bound_violations(report, "edge_shares")
                break

            targets = build_targets(flows, sensor_edges, qi_start,
                                    n_intervals, split_key="edge_shares")
            bounds_pq, priors_pq = build_bounds_priors("")
            report = pfe.calibrate(cand_path, calib_path, targets,
                                   bounds_pq, priors_pq)
            tag = f"[congestion-feedback {iteration+1}/{n_iter}]" if n_iter > 1 else "PFE"
            print(f"  {tag} edge_shares       {report['vehicles']:>6} veh  "
                  f"GEH<5: {report['geh_pct']}%  "
                  f"(infeasible intervals: {report['infeasible_intervals']})")
            warn_unserviceable_measured_edges(report, "edge_shares")
            warn_bound_violations(report, "edge_shares")

            if args.congestion_method == "simulate":
                # Simple GEH-based early stop — this method is meant for an
                # occasional, more-accurate check, not a many-iteration loop.
                if prev_geh is not None and abs(report["geh_pct"] - prev_geh) < 0.5:
                    print("  GEH stable across iterations — converged early, "
                          "skipping remaining congestion-feedback rounds")
                    break
                prev_geh = report["geh_pct"]
                weight_file = run_feedback_simulation(calib_path, duration_s, home, iteration)
            else:
                # BPR is cheap enough to use the real MSA/Frank-Wolfe
                # convergence criterion — has travel time itself settled —
                # rather than only watching GEH (which can hit 100% while
                # routes are still shifting under it).
                new_tt = bpr_travel_times(report["achieved"])
                shared = set(new_tt) & set(prev_tt_raw or {})
                if shared:
                    diffs = [abs(a - b) / b
                            for e in shared
                            for a, b in zip(new_tt[e], prev_tt_raw[e]) if b]
                    rel_change = sum(diffs) / len(diffs) if diffs else 0.0
                    print(f"  mean relative travel-time change vs previous "
                          f"iteration: {rel_change:.1%}")
                    if rel_change < 0.02:
                        print("  travel times converged — skipping remaining "
                              "congestion-feedback rounds")
                        break
                prev_tt_raw = new_tt
                damped_tt = damp_travel_times(new_tt, damped_tt, iteration)
                weight_file = SUMO_DIR / f"feedback_weights_{iteration}.xml"
                write_weight_file(damped_tt, weight_file)
        if report["geh_pct"] < 100:
            print("  ⚠ measured-edge fit below gate — inspect before use")
    else:
        generate_candidates()
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

    meta = demand_metadata(
        start_date=args.start_date, days=args.days, source=args.source,
        begin=args.begin, end=args.end, qi_start=qi_start,
        n_intervals=n_intervals, epoch_sim=t0,
        direction_split="estimated" if load_direction_split() else "even",
        n_variants=len(variants),
    )
    with open(SUMO_DIR / "demand_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\nWrote {calib_path} + demand_meta.json")

    n_stale = clear_stale_scenarios()
    if n_stale:
        print(f"Removed {n_stale} stale web scenario JSON files — run run_scenario.py to rebuild")

    export_od(calib_path, sensor_edges, meta)


if __name__ == "__main__":
    main()
