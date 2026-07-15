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

from traffic_sim.simulation.runtime import sumo_home
from traffic_sim.core.fingerprint import make_fingerprint
from traffic_sim.core.contracts import (DemandBuildSpec, load_demand_build_spec,
                                         write_demand_build_spec)
from traffic_sim.demand import cache as candidate_cache
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


def fit_summary(report: dict) -> dict:
    """Keep publication-relevant calibration gates for one variant."""
    purpose = report.get("purpose_allocation_summary", {})
    return {
        "geh_pct": report.get("geh_pct"),
        "infeasible_intervals": report.get("infeasible_intervals", 0),
        "vehicles": report.get("vehicles"),
        "unserviceable_edges": list(report.get("unserviceable_edges", [])),
        "bound_violations": list(report.get("bound_violations", [])),
        "purpose_incompatible_quarters": purpose.get(
            "quarters_with_incompatible_routes", 0),
        "relaxation_summary": report.get("relaxation_summary", {}),
    }


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
    p.add_argument("--demand-spec", type=Path, default=None,
                   help="Validated DemandBuildSpec JSON. When supplied it is "
                        "the authoritative date/source/window contract; "
                        "legacy flags may be repeated only with matching values.")
    p.add_argument("--seed",  type=int, default=42)
    p.add_argument("--keep-scenarios", action="store_true",
                   help="Do NOT delete the existing web scenario JSONs after "
                        "this build. serve.py's publish-after-validate "
                        "recalibration (IMPROVEMENT_PLAN.md E2) passes this so the live, "
                        "coherent old scenario set keeps serving until the "
                        "NEW baseline has been built and validated in "
                        "staging — the old set is then replaced atomically. "
                        "CLI default stays the documented 2026-07-09 "
                        "behaviour (clear + empty manifest), because a bare "
                        "`make demand` has no guarantee run_scenario.py "
                        "runs next.")
    p.add_argument("--engine", choices=["pfe", "routesampler"], default="pfe",
                   help="Calibration engine: pfe = the level-1/2/3 hierarchy "
                        "(hard counts, conservation bounds, learned priors); "
                        "routesampler = reference implementation (counts only)")
    p.add_argument("--legacy-random-pool", action="store_true",
                   help="Use uniform randomTrips instead of the subarea/DeSO/"
                        "RVU candidate generator (build_candidates.py). Kept "
                        "only for comparison; the grounded generator is default.")
    p.add_argument("--through-fraction", type=float, default=0.5,
                   help="θ passed to build_candidates.py: the sensor-"
                        "conditioned candidate pool's E-E supply share — "
                        "NOT the area-wide through share (Stockholm ANPR "
                        "measures that at ~9.4%%, Trafikverket 2017:123; "
                        "small German cores 18-25%%). A 2026-07-13 3-point "
                        "sweep showed re-mixing the pool toward those "
                        "numbers degrades every structural gate; see "
                        "build_candidates.py --through-fraction for the "
                        "full investigation.")
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
                        "deterrence_weights and IMPROVEMENT_PLAN.md. "
                        "Default 1.5 chosen from a real "
                        "9-combo sweep on the real graph (tools/"
                        "fit_deterrence_kernel.py, 2026-07-12) under the "
                        "conditional-sampling redesign: with β=1.8 the "
                        "kernel mode sits at 2.7 km and the generated "
                        "destination near-sensor share lands exactly at "
                        "the all-edges baseline (1.9%% vs 1.8%% — no "
                        "unexplained spike, the research doc's acceptance "
                        "criterion) while still emitting short trips. "
                        "DELIBERATE DEVIATION from the sweep's pre-declared "
                        "lowest-L1 rule: the L1 winner (α=3, β=2.6, "
                        "L1=0.746) got there by erasing the 0-1 km bin to "
                        "0.1%% vs RVU's 15%% — 'do not simply forbid all "
                        "short trips' is an explicit constraint in the "
                        "research doc, so L1 (which rewards that erasure) "
                        "was overruled for the in-range α the doc itself "
                        "recommends (α≈1.5-2).")
    p.add_argument("--cross-fraction", type=float, default=0.3,
                   help="θ passed to build_candidates.py: share of tours "
                        "that are E-I/I-E cross-boundary commuting (one end "
                        "at a gate) rather than pure I-I. Supply-tuned "
                        "value — see build_candidates.py --cross-fraction "
                        "and --through-fraction for the 2026-07-13 "
                        "investigation and sources.")
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

    def provided(flag: str) -> bool:
        return any(value == flag or value.startswith(flag + "=")
                   for value in sys.argv[1:])

    if args.date is not None and args.start_date is not None:
        p.error("use either --date or --start-date, not both")
    if args.date is not None and args.days != 1:
        p.error("--date is an alias for --start-date DATE --days 1")

    if args.demand_spec is not None:
        try:
            spec = load_demand_build_spec(args.demand_spec)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            p.error(f"invalid --demand-spec: {exc}")
        # Explicit legacy flags are checked, not silently ignored.  This lets
        # old integrations migrate incrementally without allowing two sources
        # of truth to produce a build with an ambiguous identity.
        checks = (
            ("--source", args.source, spec.source),
            ("--days", args.days, spec.days),
            ("--begin", args.begin, spec.begin),
            ("--end", args.end, spec.end),
        )
        for flag, actual, expected in checks:
            if provided(flag) and actual != expected:
                p.error(f"{flag} conflicts with --demand-spec ({actual!r} != {expected!r})")
        if provided("--date") and args.date != spec.start_date:
            p.error("--date conflicts with --demand-spec")
        if provided("--start-date") and args.start_date not in (None, spec.start_date):
            p.error("--start-date conflicts with --demand-spec")
        args.date = None
        args.start_date = spec.start_date
        args.source = spec.source
        args.days = spec.days
        args.begin = spec.begin
        args.end = spec.end
        args.demand_contract = spec
    else:
        if args.days < 1:
            p.error("--days must be at least 1")
        args.start_date = args.start_date or args.date or "2025-09-16"
        if args.days > 1:
            args.begin, args.end = "00:00", "24:00"
        try:
            args.demand_contract = DemandBuildSpec(
                start_date=args.start_date, source=args.source, days=args.days,
                begin=args.begin, end=args.end,
                structural_reference_date=STRUCTURAL_REFERENCE_DATE)
        except ValueError as exc:
            p.error(str(exc))
    return args


# Date/window intake — moved to demand/intake.py (H1, 2026-07-14).
# Patch SUMO_DIR/GEO_PATH on demand.intake for these functions.
from demand.intake import (build_targets, classify_day, demand_metadata,
                           has_split_quantiles, load_direction_split,
                           load_sensor_edges, multi_day_blocks,
                           real_day_shape, validate_date_range)

# Structure metrics/gates — demand/structure.py (H1). GEO_PATH and the
# geometry cache live there — monkeypatch THERE.
from demand.structure import (DEST_GROUP_CAP_MULT, LENGTH_BIN_EDGES_KM,
                              PURPOSE_LENGTH_MIN_N, STRUCTURE_FLAG_MULT,
                              calibrated_agent_summary,
                              calibrated_structure_report,
                              load_edge_geometry, purpose_lengths_km,
                              structure_groups_for_shapes)

# Bounds/priors intake — demand/priors.py (H1). Patch subprocess/GEO_PATH
# on demand.priors for these.
from demand.priors import (ensure_assignment_priors, ensure_bounds,
                           ensure_observability, ensure_priors,
                           structural_bounds_and_priors)


# Publication stage — moved to demand/publication.py (H1, 2026-07-14).
# Patch SUMO_DIR/SCEN_DIR on demand.publication for these functions.
from demand.publication import clear_stale_scenarios, export_od, write_counts


# Congestion-feedback stage — moved to demand/feedback.py (H1, 2026-07-14).
# Re-exported here so existing callers keep working; the module globals
# these functions read (SUMO_DIR/NET_PATH/GEO_PATH/subprocess) now live in
# demand.feedback — patch THERE.
from demand.feedback import (BPR_PERIOD_S, FEEDBACK_SIM_TIMEOUT_S,
                             bpr_travel_times,
                             damp_travel_times, run_feedback_simulation,
                             run_tool, write_weight_file)

# PFE calibration orchestration — moved to demand/calibration.py (H1,
# 2026-07-14). The _PFE_PAR_* pool globals live there.
from demand.calibration import (run_pfe_variants_flat_parallel,
                                warn_bound_violations,
                                warn_purpose_allocation_drift,
                                warn_unserviceable_measured_edges)


def main() -> None:
    args = parse_args()
    demand_spec: DemandBuildSpec = args.demand_contract
    if demand_spec.structural_reference_date != STRUCTURAL_REFERENCE_DATE:
        sys.exit("demand spec structural_reference_date does not match "
                 f"the pipeline reference {STRUCTURAL_REFERENCE_DATE}")
    # Keep the path stable, but do not overwrite the previous contract until
    # calibration has succeeded.  A failed build must leave the old demand and
    # its provenance coherent for the live scenario set.
    demand_spec_path = SUMO_DIR / "demand_build_spec.json"
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
            cache_outputs = {
                "candidates.rou.xml": cand_path,
                "candidates.meta.json": cand_path.with_name("candidates.meta.json"),
                "tours.trips.xml": SUMO_DIR / "tours.trips.xml",
                "trip_length_fit.json": SUMO_DIR / "trip_length_fit.json",
                "sensor_coverage_report.json": SUMO_DIR / "sensor_coverage_report.json",
            }
            cache_inputs = {
                "network": NET_PATH,
                "graph": Path("web/data/graph.graphml"),
                "map_network": GEO_PATH,
                "source_flows": flows_path,
                "normal_profile": Path("web/data/normal_profile.json"),
                "direction_split": SUMO_DIR / "direction_split.json",
                "population": Path("data_in/deso/population_2023.json"),
                "deso": Path("data_in/deso/deso_goteborg.geojson"),
                "poi": Path("data_in/deso/osm_pois.geojson"),
                "real_day_shape": day_shape_path or Path(
                    "sumo/.missing-real-day-shape"),
                "day_blocks": day_blocks_path or Path(
                    "sumo/.missing-day-blocks"),
            }
            cache_config = {
                "n_total": n_total,
                "through_fraction": args.through_fraction,
                "gravity_km": args.gravity_km,
                "gravity_alpha": args.gravity_alpha,
                "cross_fraction": args.cross_fraction,
                "is_weekend": use_weekend_shape,
                "min_per_sensor": 50,
                "route_diversity": 2.0,
                "seed": args.seed,
                "weight_file": None,
            }
            cache_key = candidate_cache.cache_key(
                cache_config, cache_inputs,
                {"build_candidates": Path("build_candidates.py"),
                 "build_sumo_demand": Path(__file__),
                 "build_data": Path("build_data.py"),
                 "dirsplit_geo": Path("dirsplit/geo.py"),
                 "candidate_cache": Path("traffic_sim/demand/cache.py"),
                 "pipeline_fingerprint": Path(
                     "traffic_sim/core/fingerprint.py")})
            if candidate_cache.restore(candidate_cache.DEFAULT_ROOT,
                                       cache_key, cache_outputs):
                elapsed = time.perf_counter() - started
                timings_s["candidate_generation"] = (
                    timings_s.get("candidate_generation", 0.0) + elapsed)
                print(f"  candidate cache hit {cache_key} ({elapsed:.2f}s)")
                return
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
            try:
                candidate_cache.store(candidate_cache.DEFAULT_ROOT,
                                      cache_key, cache_outputs)
                print(f"  candidate cache stored {cache_key}")
            except (FileNotFoundError, OSError) as exc:
                # Cache failure must never invalidate an otherwise valid
                # demand build; the cache is an optimization, not an input.
                print(f"  WARNING candidate cache not stored: {exc}")
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
    variant_fit_reports: dict[str, dict] = {}
    report = None

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
        for iteration in range(n_iter):
            generate_candidates(weight_file)
            if iteration == n_iter - 1:
                # Always use the guarded flat solver, even for a single q50
                # variant. The old q50-only pfe.calibrate() path omitted the
                # destination/length structure groups entirely.
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
                    variant_fit_reports[key] = fit_summary(variant_report)
                    label = "PFE" if suffix == "" and n_iter == 1 else (
                        f"[congestion-feedback {iteration+1}/{n_iter}]"
                        if suffix == "" else "PFE"
                    )
                    print(f"  {label} {key:<16} {variant_report['vehicles']:>6} veh  "
                          f"GEH<5: {variant_report['geh_pct']}%  "
                          f"(infeasible intervals: {variant_report['infeasible_intervals']})")
                    warn_unserviceable_measured_edges(variant_report, key)
                    warn_purpose_allocation_drift(variant_report, key)
                    warn_bound_violations(variant_report, key)
                    if variant_report["geh_pct"] < 100:
                        print("  ⚠ measured-edge fit below gate — inspect before use")
                report = reports[""]
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
        n_variants=len(variants), demand_spec=demand_spec.to_dict(),
        build_options={
            "engine": args.engine,
            "seed": args.seed,
            "legacy_random_pool": args.legacy_random_pool,
            "through_fraction": args.through_fraction,
            "gravity_km": args.gravity_km,
            "gravity_alpha": args.gravity_alpha,
            "cross_fraction": args.cross_fraction,
            "no_assignment_prior": args.no_assignment_prior,
            "congestion_iterations": args.congestion_iterations,
            "congestion_method": args.congestion_method,
        },
    )
    meta["timings_s"] = {name: round(seconds, 3)
                         for name, seconds in timings_s.items()}
    if args.engine == "pfe" and report is not None and report.get("timings_s"):
        meta["pfe_timing_s"] = report["timings_s"]
    if report is not None:
        # Machine-readable fit summary — E2's publish gate (serve.py) and
        # E3's health gates read this instead of scraping stdout.
        meta["pfe_fit"] = {
            "geh_pct": report.get("geh_pct"),
            "infeasible_intervals": report.get("infeasible_intervals"),
            "vehicles": report.get("vehicles"),
        }
    if variant_fit_reports:
        meta["pfe_fit_variants"] = variant_fit_reports
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
    # Every scenario must identify the exact demand/network/toolchain that
    # produced its route files. Paths are labels only; the fingerprint uses
    # content hashes so an uncommitted local edit cannot masquerade as the
    # previous build.
    fingerprint_artifacts = {
        "network": NET_PATH,
        "source_flows": flows_path,
        "candidate_routes": cand_path,
        "candidate_metadata": cand_path.with_name(
            cand_path.name.replace(".rou.xml", ".meta.json")),
        "direction_split": SUMO_DIR / "direction_split.json",
        "observability": Path("web/data/observability.json"),
        "bounds": Path("web/data/observability_bounds.json"),
        "priors": SUMO_DIR / "prior_flows.json",
        "assignment_priors": SUMO_DIR / "assignment_priors.json",
        "calibrated_q50": SUMO_DIR / "calibrated.rou.xml",
        "demand_spec": demand_spec_path,
    }
    for suffix, _key in variants:
        if suffix:
            fingerprint_artifacts[f"calibrated{suffix}"] = (
                SUMO_DIR / f"calibrated{suffix}.rou.xml")
    source_files = {
        "build_sumo_demand": Path(__file__),
        "build_data": Path("build_data.py"),
        "sensor_registry": Path("traffic_sim/intake/sensors.py"),
        "sensor_registry_data": Path("data_in/sensors.json"),
        "build_candidates": Path("build_candidates.py"),
        "build_sumo_net": Path("build_sumo_net.py"),
        "pfe": Path("pfe.py"),
        "candidate_cache": Path("traffic_sim/demand/cache.py"),
        "pipeline_fingerprint": Path("traffic_sim/core/fingerprint.py"),
        "assignment_priors": Path("assignment_priors.py"),
        "prior_flows": Path("prior_flows.py"),
        "observability": Path("observability.py"),
    }
    for module_path in sorted(Path("demand").glob("*.py")):
        source_files[f"demand/{module_path.name}"] = module_path
    # The exact contract is written only after all expensive calibration and
    # structure gates have completed, immediately before the matching metadata
    # fingerprint is created.
    write_demand_build_spec(demand_spec_path, demand_spec)
    meta["build_fingerprint"] = make_fingerprint(
        contract={k: v for k, v in meta.items()
                  if k not in {"timings_s", "pfe_timing_s",
                               "build_fingerprint"}},
        artifacts=fingerprint_artifacts,
        source_files=source_files,
        sumo_home=home,
    )
    meta["build_id"] = meta["build_fingerprint"]["build_id"]
    meta_path = SUMO_DIR / "demand_meta.json"
    meta_tmp = meta_path.with_name(meta_path.name + ".tmp")
    with open(meta_tmp, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(meta_tmp, meta_path)
    print(f"\nWrote {calib_path} + demand_meta.json")

    if args.keep_scenarios:
        print("Keeping existing web scenarios (--keep-scenarios): caller "
              "is responsible for replacing them after validation")
    else:
        n_stale = clear_stale_scenarios()
        if n_stale:
            print(f"Removed {n_stale} stale web scenario JSON files — run run_scenario.py to rebuild")

    export_od(calib_path, sensor_edges, meta)


def _tracked_main() -> None:
    """Run main() under the E1 run registry (runs.py).

    Slice 1 of IMPROVEMENT_PLAN.md E1: products keep their legacy shared paths, but
    every build additionally gets an immutable runs/<id>/ directory with a
    manifest written BEFORE work starts, archived copies of the key
    products, and a latest_demand pointer flipped only on success — so a
    finished-looking artifact can never again be separated from the code
    and inputs that made it."""
    from traffic_sim.ops import runs

    run = runs.start_run("demand", inputs={"argv": sys.argv[1:]})
    try:
        main()
    except BaseException as exc:
        run.finish("failed", error=f"{type(exc).__name__}: {exc}")
        raise
    meta_path = SUMO_DIR / "demand_meta.json"
    # Archive the outputs that actually exist (2026-07-14 accuracy review
    # §P1-7: the uncertainty variants are named calibrated_v1/_v2, not
    # calibrated_q10/_q90 — every earlier manifest recorded the two
    # phantom names as missing_outputs).
    products = [meta_path, SUMO_DIR / "calibrated.rou.xml",
                SUMO_DIR / "calibrated.agents.json"]
    products += sorted(SUMO_DIR.glob("calibrated_v*.rou.xml"))
    products += sorted(SUMO_DIR.glob("calibrated_v*.agents.json"))
    for product in products:
        run.add_output(product)
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        run.record("calibrated_structure",
                   meta.get("calibrated_structure", {}))
        run.record("structure_flags", meta.get(
            "calibrated_structure", {}).get("structure_flags", []))
    # G3: refresh the assembled validation report whenever demand changes;
    # never let reporting fail the build it reports on.
    try:
        import validation_report
        report = validation_report.write_report()
        run.record("validation_overall", report["overall"])
        run.add_output(validation_report.OUT_PATH)
    except Exception as exc:
        print(f"validation report: {type(exc).__name__}: {exc}")
    run.finish("succeeded")


if __name__ == "__main__":
    _tracked_main()
