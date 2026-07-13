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
from collections import Counter
import json
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
import time
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
    p.add_argument("--gravity-km", type=float, default=1.8,
                   help="θ passed to build_candidates.py: deterrence scale "
                        "β (km). RE-FIT 2026-07-12 alongside the new "
                        "--gravity-alpha under the conditional-sampling "
                        "redesign (tools/fit_deterrence_kernel.py) — the "
                        "old 2.6 default came from a fit against the "
                        "pre-redesign sampler and pure-exponential kernel, "
                        "both replaced. (History: the 2.6 value was itself "
                        "a 2026-07-08 correction of an earlier untested "
                        "claim; see calibrate_theta.py for why RVU's "
                        "5.1-10km/>10km bins cannot be matched regardless "
                        "of θ — this network's own diameter is ~7.8 km, a "
                        "hard geometric ceiling.)")
    p.add_argument("--gravity-alpha", type=float, default=1.5,
                   help="θ passed to build_candidates.py: Tanner/gamma "
                        "deterrence shape α — see build_candidates."
                        "deterrence_weights and DESTINATION_BIAS_RESEARCH_"
                        "2026-07-12.md. Default 1.5 chosen from a real "
                        "9-combo sweep on the real graph (tools/"
                        "fit_deterrence_kernel.py, 2026-07-12) under the "
                        "conditional-sampling redesign: with β=1.8 the "
                        "kernel mode sits at 2.7 km and the generated "
                        "destination near-sensor share lands exactly at "
                        "the all-edges baseline (1.9% vs 1.8% — no "
                        "unexplained spike, the research doc's acceptance "
                        "criterion) while still emitting short trips. "
                        "DELIBERATE DEVIATION from the sweep's pre-declared "
                        "lowest-L1 rule: the L1 winner (α=3, β=2.6, "
                        "L1=0.746) got there by erasing the 0-1 km bin to "
                        "0.1% vs RVU's 15% — 'do not simply forbid all "
                        "short trips' is an explicit constraint in the "
                        "research doc, so L1 (which rewards that erasure) "
                        "was overruled for the in-range α the doc itself "
                        "recommends (α≈1.5-2).")
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


def calibrated_agent_summary(route_path: Path, n_intervals: int) -> dict | None:
    """Summarise the individual demand agents emitted beside a PFE route file.

    Older candidate pools have no purpose sidecar, so absence stays explicit
    rather than fabricating a purpose split from route geometry.
    """
    agent_path = route_path.with_name(route_path.name.replace(".rou.xml", ".agents.json"))
    if not agent_path.exists():
        return None
    with open(agent_path) as f:
        agents = json.load(f).get("agents", [])
    purposes = Counter(agent.get("purpose", "unknown") for agent in agents)
    by_quarter = [Counter() for _ in range(n_intervals)]
    for agent in agents:
        quarter = int(float(agent.get("departure_s", 0)) // 900)
        if 0 <= quarter < n_intervals:
            by_quarter[quarter][agent.get("purpose", "unknown")] += 1
    return {
        "agent_file": agent_path.name,
        "n_agents": len(agents),
        "purpose_counts": dict(sorted(purposes.items())),
        "purpose_counts_by_quarter": [dict(sorted(counts.items()))
                                      for counts in by_quarter],
    }


def _route_structure_metrics(route_path: Path) -> dict | None:
    """Per-vehicle structure metrics for one SUMO route file — the shared
    machinery behind calibrated_structure_report (below). Emits, per
    DESTINATION_BIAS_RESEARCH_2026-07-12.md §4A step 4:

    - trip_length_fit (straight-line O→D, RVU bins)
    - dest_sensor_proximity (share ending within 200 m of a sensor)
    - onward_after_last_sensor: how far each vehicle's route CONTINUES
      after the last measured sensor edge it crosses (polyline metres) —
      the sharpest signature of the original bug ("crosses the sensor,
      then vanishes"), sharper than nearest-sensor distance because it
      cannot be confused by a destination that happens to sit near a
      DIFFERENT sensor the route never crossed.
    - sensor_passages: how many measured edges each route crosses.

    Reuses build_candidates' metric implementations — imported lazily so
    build_sumo_demand's import time doesn't pay for osmnx/shapely."""
    from build_candidates import (destination_sensor_proximity,
                                  gravity_distance_km, trip_length_fit)
    if not route_path.exists() or not GEO_PATH.exists():
        return None
    with open(GEO_PATH) as f:
        geo = json.load(f)
    edge_latlon: dict[str, tuple[float, float]] = {}
    edge_len_m: dict[str, float] = {}
    sensor_edge_ids: set[str] = set()
    for feat in geo["features"]:
        if feat["geometry"]["type"] != "LineString":
            continue
        coords = feat["geometry"]["coordinates"]
        mid = coords[len(coords) // 2]
        eid = feat["properties"]["id"]
        edge_latlon[eid] = (mid[1], mid[0])          # (lat, lon)
        lats = np.array([c[1] for c in coords])
        lons = np.array([c[0] for c in coords])
        edge_len_m[eid] = float(sum(
            gravity_distance_km(np.array([lats[k + 1]]), np.array([lons[k + 1]]),
                                lats[k], lons[k])[0] * 1000.0
            for k in range(len(coords) - 1)))
        if feat["properties"].get("sensor_id"):
            sensor_edge_ids.add(eid)

    lengths_km: list[float] = []
    dest_edges: list[str] = []
    onward_m: list[float] = []
    passages = Counter()
    n_no_sensor = 0
    for veh in ET.parse(route_path).getroot().iter("vehicle"):
        route = veh.find("route")
        if route is None or not route.get("edges"):
            continue
        edges = route.get("edges").split()
        o, d = edge_latlon.get(edges[0]), edge_latlon.get(edges[-1])
        dest_edges.append(edges[-1])
        if o is not None and d is not None:
            lengths_km.append(float(gravity_distance_km(
                np.array([d[0]]), np.array([d[1]]), o[0], o[1])[0]))
        n_pass = sum(1 for e in edges if e in sensor_edge_ids)
        passages[min(n_pass, 3)] += 1   # 3 == "3 or more"
        if n_pass == 0:
            n_no_sensor += 1
        else:
            last = max(k for k, e in enumerate(edges) if e in sensor_edge_ids)
            onward_m.append(sum(edge_len_m.get(e, 0.0) for e in edges[last + 1:]))

    if not dest_edges:
        return None
    onward_sorted = sorted(onward_m)
    return {
        "trip_length_fit": trip_length_fit(lengths_km),
        "dest_sensor_proximity": destination_sensor_proximity(
            dest_edges, edge_latlon, sorted(sensor_edge_ids)),
        "onward_after_last_sensor": {
            "median_m": round(onward_sorted[len(onward_sorted) // 2], 1)
                        if onward_sorted else None,
            "pct_under_200m": round(100 * sum(1 for v in onward_m if v < 200.0)
                                    / len(onward_m), 1) if onward_m else None,
            "n_routes_without_sensor": n_no_sensor,
        },
        "sensor_passages": {("3+" if k == 3 else str(k)): passages[k]
                            for k in sorted(passages)},
    }


# Structure-preservation slack: a structural group's calibrated share may be
# at most this multiple of the candidate pool's own share. 2.0 gives the
# solver genuine freedom (activity near sensors is real — Korsvägen etc.)
# while blocking the measured 5-10x amplification.
DEST_GROUP_CAP_MULT = 2.0
# Straight-line O→D length bins whose calibrated share is likewise capped
# relative to the pool — RVU's own bin edges, matching trip_length_fit.
LENGTH_BIN_EDGES_KM = (1.0, 5.0, 10.0)

# Flag threshold: calibrated may exceed the pool by at most the structure-
# cap multiple plus a 25% margin (the per-quarter caps carry a 2-vehicle
# integer floor, so mild aggregate overshoot is expected, not a defect).
STRUCTURE_FLAG_MULT = DEST_GROUP_CAP_MULT * 1.25


def calibrated_structure_report(route_path: Path,
                                pool_path: Path | None = None) -> dict | None:
    """Structure metrics of the CALIBRATED output + drift FLAGS vs the
    candidate pool it was calibrated from — the permanent validation gate
    for the 2026-07-12 destination-clustering bug (DESTINATION_BIAS_
    RESEARCH_2026-07-12.md §4A step 4: "Publish validation gates with
    every demand build ... Reject or flag a build when it has good GEH but
    fails those structural checks").

    Measured then: the candidate POOL looked acceptable while the PFE-
    calibrated output had 36.5% of vehicles ending within 200 m of a sensor
    (2.1% random-edge baseline) — calibration distorted the seed's
    structure invisibly, because the only fit ever computed ran at
    candidate-generation time. Flags compare calibrated vs pool (the seed
    structure PFE is supposed to preserve), not vs an absolute number."""
    report = _route_structure_metrics(route_path)
    if report is None:
        return None
    if pool_path is not None:
        pool = _route_structure_metrics(pool_path)
        if pool is not None:
            report["pool"] = pool
            flags = []

            def ratio_flag(name: str, calibrated_v, pool_v) -> None:
                if calibrated_v is None or pool_v is None:
                    return
                if calibrated_v > max(pool_v, 0.5) * STRUCTURE_FLAG_MULT:
                    flags.append(f"{name}: calibrated {calibrated_v} vs pool "
                                 f"{pool_v} (over {STRUCTURE_FLAG_MULT:.2f}x)")

            ratio_flag("dest_within_200m_of_sensor_pct",
                       report["dest_sensor_proximity"]["pct_within"],
                       pool["dest_sensor_proximity"]["pct_within"])
            ratio_flag("onward_under_200m_pct",
                       report["onward_after_last_sensor"]["pct_under_200m"],
                       pool["onward_after_last_sensor"]["pct_under_200m"])
            ratio_flag("trips_under_1km_pct",
                       report["trip_length_fit"]["shares"][0] * 100,
                       pool["trip_length_fit"]["shares"][0] * 100)
            report["structure_flags"] = flags
            for flag in flags:
                print(f"  WARNING structure drift: {flag}")
    return report


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
    # Atomic (write-temp-then-replace) so a live browser polling index.json
    # mid-cleanup never observes a truncated file — same reasoning as
    # run_scenario.py's atomic_write_json, duplicated here rather than
    # imported to keep this CLI-only path independent of run_scenario.py's
    # module-level state (2026-07-10).
    index_path = SCEN_DIR / "index.json"
    fd, tmp_name = tempfile.mkstemp(dir=SCEN_DIR, prefix=".index.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"scenarios": []}, f, indent=2)
        os.replace(tmp_name, index_path)
    except BaseException:
        os.unlink(tmp_name)
        raise
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
_PFE_PAR_STRUCTURE_GROUPS = None   # list of (name, member indices, cap share)

def structure_groups_for_shapes(shapes) -> list[tuple[str, list[int], float]]:
    """PFE structure-preservation groups (name, member shape indices, capped
    assigned share) — DESTINATION_BIAS_RESEARCH_2026-07-12.md §4A step 3:
    "PFE may adjust route use to satisfy sensor counts, but it may not turn
    a realistic candidate distribution into mostly sensor-adjacent
    destinations." Two families, both capped at DEST_GROUP_CAP_MULT × the
    pool's own share:

    1. near_sensor_dest — routes ENDING within 200 m of a sensor. ROOT
       CAUSE (measured 2026-07-12, after the stage-1 generation fix had
       already brought the POOL's share down to the activity-field level
       of ~2-4%): PFE still calibrated 19.4% of vehicles onto such routes
       — 87 of 948 active shapes carried 4 550 vehicles at 52 veh/shape vs
       24 for all others. A route crossing exactly one sensor and ENDING
       is a free variable for closing that sensor's hard count band
       without disturbing any other sensor's band (ruled OUT empirically:
       assignment-prior bounds — a --no-assignment-prior build still
       showed 18.3%).
    2. length_bin_* — straight-line O→D length bins (RVU's edges). Same
       under-determination family, measured after the near-sensor cap was
       already in place: the calibrated 0-1 km share was still inflated
       1.2% → 7.5% (6×) relative to the pool. §4A step 3 prescribes
       length-bin bands explicitly.

    Caps are ceilings only (lo=0 — a group must never force flow), applied
    per quarter with an absolute value derived from that quarter's total,
    and dropped (never the counts) if an interval can't otherwise be
    served — see solve_interval_with_relaxation's ladder, the pass-1
    fallback in _run_pfe_interval_job, and the integer-stage repair in
    write_calibration_report. Groups whose cap would be >= 100% are
    omitted (no-ops)."""
    from build_candidates import gravity_distance_km
    if not GEO_PATH.exists():
        return []
    with open(GEO_PATH) as f:
        geo = json.load(f)
    edge_latlon: dict[str, tuple[float, float]] = {}
    s_lats, s_lons = [], []
    for feat in geo["features"]:
        if feat["geometry"]["type"] != "LineString":
            continue
        coords = feat["geometry"]["coordinates"]
        mid = coords[len(coords) // 2]
        edge_latlon[feat["properties"]["id"]] = (mid[1], mid[0])
        if feat["properties"].get("sensor_id"):
            s_lats.append(mid[1])
            s_lons.append(mid[0])
    if not s_lats:
        return []
    s_lats_a, s_lons_a = np.array(s_lats), np.array(s_lons)

    def near(eid: str) -> bool:
        ll = edge_latlon.get(eid)
        if ll is None:
            return False
        return bool((gravity_distance_km(s_lats_a, s_lons_a, ll[0], ll[1])
                     * 1000.0).min() <= 200.0)

    def od_length_km(shape) -> float | None:
        o = edge_latlon.get(shape.edges[0])
        d = edge_latlon.get(shape.edges[-1])
        if o is None or d is None:
            return None
        return float(gravity_distance_km(
            np.array([d[0]]), np.array([d[1]]), o[0], o[1])[0])

    def length_bin(km: float) -> int:
        for b, edge in enumerate(LENGTH_BIN_EDGES_KM):
            if km <= edge:
                return b
        return len(LENGTH_BIN_EDGES_KM)

    candidate_groups: dict[str, list[int]] = {"near_sensor_dest": []}
    bin_names = [f"length_bin_{lo}-{hi}km" for lo, hi in
                 zip((0,) + LENGTH_BIN_EDGES_KM, LENGTH_BIN_EDGES_KM + ("inf",))]
    for name in bin_names:
        candidate_groups[name] = []
    for i, shape in enumerate(shapes):
        if near(shape.edges[-1]):
            candidate_groups["near_sensor_dest"].append(i)
        km = od_length_km(shape)
        if km is not None:
            candidate_groups[bin_names[length_bin(km)]].append(i)

    n_total = sum(len(s.source_candidates) for s in shapes)
    groups: list[tuple[str, list[int], float]] = []
    for name, members in candidate_groups.items():
        if not members:
            continue
        n_members = sum(len(shapes[i].source_candidates) for i in members)
        # Pool share weighted by how many source candidates each shape
        # carries — the seed structure the calibration must preserve.
        pool_share = n_members / max(1, n_total)
        cap_share = DEST_GROUP_CAP_MULT * pool_share
        if cap_share >= 1.0:
            continue   # cap can never bind — omit the no-op
        groups.append((name, members, cap_share))
        print(f"  PFE structure guard [{name}]: {len(members)} shapes, "
              f"{100 * pool_share:.1f}% of pool candidates — assigned share "
              f"capped at {100 * cap_share:.1f}%")
    return groups


def _run_pfe_interval_job(job: dict):
    """ProcessPool worker for one independent (variant, quarter) PFE solve.

    The shared shape pool and route-cost vector are inherited by fork, so the
    heavy candidate geometry is not pickled once per quarter.

    Two-pass structure preservation (2026-07-12): pass 1 solves with counts/
    bounds/priors only; if ANY structure group (near-sensor destinations,
    length bins) exceeds its cap share of the interval's total, pass 2
    re-solves with every group capped at cap_share × pass-1 total (the
    bands need ABSOLUTE ceilings, which only exist once a total is known).
    If pass 2 is infeasible, the pass-1 solution is kept — the structure
    caps must never cost an interval its real sensor counts.
    """
    import pfe

    if _PFE_PAR_SHAPES is None or _PFE_PAR_ROUTE_COST is None:
        raise RuntimeError("PFE interval worker was not initialized")
    sol, rung = pfe.solve_interval_with_relaxation(
        _PFE_PAR_SHAPES,
        job["targets"],
        job["bounds"],
        job["priors"],
        route_cost=_PFE_PAR_ROUTE_COST,
    )
    if sol is not None and _PFE_PAR_STRUCTURE_GROUPS:
        total = float(sol.sum())
        violated = total > 0 and any(
            float(sol[members].sum()) > cap_share * total
            for _name, members, cap_share in _PFE_PAR_STRUCTURE_GROUPS)
        if violated:
            capped_sol, capped_rung = pfe.solve_interval_with_relaxation(
                _PFE_PAR_SHAPES,
                job["targets"],
                job["bounds"],
                job["priors"],
                route_cost=_PFE_PAR_ROUTE_COST,
                groups=[(members, 0.0, cap_share * total)
                        for _name, members, cap_share in _PFE_PAR_STRUCTURE_GROUPS],
            )
            if capped_sol is not None:
                sol, rung = capped_sol, capped_rung
    return job["suffix"], job["key"], job["quarter"], sol, rung


def run_pfe_variants_flat_parallel(cand_path: Path, variants: list[tuple[str, str]],
                                   variant_inputs: dict[str, dict],
                                   max_workers: int | None = None) -> dict[str, dict]:
    """Solve all final direction variants through one flat worker pool.

    This avoids nesting multiprocessing pools: the unit of parallel work is one
    15-minute interval, across all variants, and route files are written only
    after every solution has been collected in deterministic quarter order.
    """
    import pfe

    global _PFE_PAR_SHAPES, _PFE_PAR_ROUTE_COST, _PFE_PAR_STRUCTURE_GROUPS
    phase_started = time.perf_counter()
    shapes, route_cost = pfe.prepare_calibration(cand_path)
    prepare_s = time.perf_counter() - phase_started
    print(f"  timing PFE prepare: {prepare_s:.1f}s")
    _PFE_PAR_SHAPES = shapes
    _PFE_PAR_ROUTE_COST = route_cost
    # Set BEFORE the pool forks so workers inherit it, like the shape pool.
    _PFE_PAR_STRUCTURE_GROUPS = structure_groups_for_shapes(shapes)
    try:
        tasks = []
        solutions = {}
        rungs = {}
        for suffix, key in variants:
            data = variant_inputs[suffix]
            nq = len(data["targets"])
            solutions[suffix] = [None] * nq
            rungs[suffix] = [pfe.RUNG_INFEASIBLE] * nq
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
        solve_started = time.perf_counter()
        with mp.get_context("fork").Pool(processes=n_workers) as pool:
            for suffix, _key, quarter, sol, rung in pool.imap_unordered(
                _run_pfe_interval_job, tasks
            ):
                solutions[suffix][quarter] = sol
                rungs[suffix][quarter] = rung
        solve_s = time.perf_counter() - solve_started
        print(f"  timing PFE interval solving: {solve_s:.1f}s")

        reports = {}
        for suffix, key in variants:
            data = variant_inputs[suffix]
            report_started = time.perf_counter()
            reports[suffix] = pfe.write_calibration_report(
                shapes, data["out_path"], data["targets"], solutions[suffix],
                data["hard_bounds_pq"], rungs[suffix], enforce_integer_bounds=True,
                structure_groups=[(members, cap_share) for _n, members, cap_share
                                  in (_PFE_PAR_STRUCTURE_GROUPS or [])])
            publish_s = time.perf_counter() - report_started
            reports[suffix]["timings_s"] = {
                "prepare_shared": round(prepare_s, 3),
                "interval_solving_shared": round(solve_s, 3),
                "route_publish": round(publish_s, 3),
            }
            print(f"  timing PFE route publish {key}: {publish_s:.1f}s")
            if not data.get("keep_achieved", False):
                reports[suffix] = {
                    k: v for k, v in reports[suffix].items() if k != "achieved"
                }
        return reports
    finally:
        _PFE_PAR_SHAPES = None
        _PFE_PAR_ROUTE_COST = None
        _PFE_PAR_STRUCTURE_GROUPS = None


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
    timings_s: dict[str, float] = {}

    def timed(name: str, fn):
        started = time.perf_counter()
        result = fn()
        elapsed = time.perf_counter() - started
        timings_s[name] = timings_s.get(name, 0.0) + elapsed
        print(f"  timing {name}: {elapsed:.1f}s")
        return result

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
        started = time.perf_counter()
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
                  "--gravity-alpha", str(args.gravity_alpha),
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
        elapsed = time.perf_counter() - started
        timings_s["candidate_generation"] = (
            timings_s.get("candidate_generation", 0.0) + elapsed)
        print(f"  timing candidate_generation: {elapsed:.1f}s")

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
        bounds_data, priors_data = timed(
            "structural_bounds_priors",
            lambda: structural_bounds_and_priors(args.begin, args.end))
        obs_data = timed("observability", ensure_observability)
        corridor    = obs_data.get("corridor_priors", {})
        if corridor:
            print(f"  corridor coupling: {len(corridor)} edges between "
                  f"sensor pairs get data-derived priors")
        assign_data = (timed("assignment_priors", ensure_assignment_priors)
                       if not args.no_assignment_prior
                       else {"weight": 0.0, "flows": {}})
        assign_w    = assign_data.get("weight", 0.0)
        assign_flows = assign_data.get("flows", {})
        if assign_flows:
            print(f"  gravity-assignment prior: {len(assign_flows)} otherwise-"
                  f"unconstrained edges get a weak (w={assign_w}) realistic pull")
        prior_variant = {"": "prior", "_v1": "prior_low", "_v2": "prior_high"}

        def build_bounds_priors(suffix: str) -> tuple[list[dict], list[dict], list[dict]]:
            bounds_pq, priors_pq, hard_bounds_pq = [], [], []
            for i in range(n_intervals):
                hard_bq = {}
                for e, arr in bounds_data["bounds"].items():
                    # Bounds are structural reference-day relationships;
                    # repeat their 96 time-of-day slots for each target day.
                    slot_i = i % 96
                    if slot_i < len(arr) and arr[slot_i]:
                        hard_bq[e] = (arr[slot_i][0], arr[slot_i][1])
                # The solver uses mathematical constraints plus wide
                # behavioral assignment ranges below. Only this first set is
                # a true post-rounding publication gate.
                bq = dict(hard_bq)
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
                hard_bounds_pq.append(hard_bq)
            return bounds_pq, priors_pq, hard_bounds_pq

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
                        bounds_pq, priors_pq, hard_bounds_pq = build_bounds_priors(suffix)
                        out = calib_path if suffix == "" else SUMO_DIR / f"calibrated{suffix}.rou.xml"
                        variant_inputs[suffix] = {
                            "out_path": out,
                            "targets": targets,
                            "bounds_pq": bounds_pq,
                            "hard_bounds_pq": hard_bounds_pq,
                            "priors_pq": priors_pq,
                            "keep_achieved": False,
                        }
                    reports = timed(
                        "pfe_variants_and_rounding",
                        lambda: run_pfe_variants_flat_parallel(
                            cand_path, variants, variant_inputs,
                            max_workers=os.cpu_count() or 1))
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
                    bounds_pq, priors_pq, hard_bounds_pq = build_bounds_priors("")
                    report = timed(
                        "pfe_and_rounding",
                        lambda: pfe.calibrate(
                            cand_path, calib_path, targets,
                            bounds_pq, priors_pq,
                            enforce_integer_bounds=True,
                            integer_bounds_per_q=hard_bounds_pq))
                    tag = f"[congestion-feedback {iteration+1}/{n_iter}]" if n_iter > 1 else "PFE"
                    print(f"  {tag} edge_shares       {report['vehicles']:>6} veh  "
                          f"GEH<5: {report['geh_pct']}%  "
                          f"(infeasible intervals: {report['infeasible_intervals']})")
                    warn_unserviceable_measured_edges(report, "edge_shares")
                    warn_bound_violations(report, "edge_shares")
                break

            targets = build_targets(flows, sensor_edges, qi_start,
                                    n_intervals, split_key="edge_shares")
            bounds_pq, priors_pq, hard_bounds_pq = build_bounds_priors("")
            report = timed(
                "pfe_and_rounding",
                lambda: pfe.calibrate(
                    cand_path, calib_path, targets,
                    bounds_pq, priors_pq,
                    enforce_integer_bounds=True,
                    integer_bounds_per_q=hard_bounds_pq))
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
    meta["timings_s"] = {name: round(seconds, 3)
                         for name, seconds in timings_s.items()}
    if args.engine == "pfe" and report is not None and report.get("timings_s"):
        meta["pfe_timing_s"] = report["timings_s"]
    agent_summary = calibrated_agent_summary(calib_path, n_intervals)
    if agent_summary is not None:
        meta["agent_demand"] = agent_summary
    structure = calibrated_structure_report(
        calib_path, pool_path=SUMO_DIR / "candidates.rou.xml")
    if structure is not None:
        meta["calibrated_structure"] = structure
        prox = structure["dest_sensor_proximity"]
        tl = structure["trip_length_fit"]
        onward = structure["onward_after_last_sensor"]
        print(f"  calibrated structure: destinations within {prox['radius_m']:.0f} m "
              f"of a sensor {prox['pct_within']}% (all-edges baseline "
              f"{prox['baseline_pct_within']}%), trip-length shares {tl['shares']} "
              f"L1={tl['l1_distance']} vs RVU short bins")
        print(f"    onward after last crossed sensor: median {onward['median_m']} m, "
              f"{onward['pct_under_200m']}% under 200 m; sensor passages "
              f"{structure['sensor_passages']}"
              + (f"; {len(structure.get('structure_flags', []))} drift flag(s)"
                 if structure.get('structure_flags') else "; no drift flags"))
    with open(SUMO_DIR / "demand_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\nWrote {calib_path} + demand_meta.json")

    n_stale = clear_stale_scenarios()
    if n_stale:
        print(f"Removed {n_stale} stale web scenario JSON files — run run_scenario.py to rebuild")

    export_od(calib_path, sensor_edges, meta)


if __name__ == "__main__":
    main()
