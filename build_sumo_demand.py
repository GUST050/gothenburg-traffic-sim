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
from importlib import metadata as importlib_metadata
import json
import multiprocessing as mp
import os
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from traffic_sim.simulation.runtime import sumo_home
from traffic_sim.core.fingerprint import (fingerprint_files, make_fingerprint,
                                           sha256_file)
from traffic_sim.core.contracts import (DemandBuildSpec, load_demand_build_spec,
                                         write_demand_build_spec)
from traffic_sim.demand import cache as candidate_cache
from traffic_sim.demand.build_lock import demand_build_lock, parent_holds_lock
from traffic_sim.demand.provenance import (DAY_PROVENANCE_NAME,
                                           validate_assembled_provenance,
                                           validate_calibrated_provenance)
from traffic_sim.demand.source_identity import demand_source_paths
from demand.day_library import (DayIdentity, DayLibrary, assemble_window,
                                merge_day_reports)
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


def _digest_payload(payload) -> str:
    """Stable content digest of a JSON-able calibration input."""
    import hashlib
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           allow_nan=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_files() -> dict[str, Path]:
    """Every source file whose content defines a demand build's identity.

    ONE inventory, delegated to demand_source_paths() -- the module whose own
    docstring states the contract: "builders and consumers share this exact
    inventory and reject archives produced by any other source bytes."

    This used to be a SECOND, hand-maintained copy of that list, and the two
    drifted (found 2026-08-06, by the annual warming failing with "demand build
    completed without a valid archive"). A build records
    STARTUP_SOURCE_HASHES, taken from here; validate_demand_archive compares it
    against demand_source_fingerprints(). The copy here was missing five
    entries the canonical inventory has -- calibrated_provenance,
    demand_source_identity and the traffic_sim/demand/*.py glob
    (__init__.py, build_lock.py, route_support.py) -- so the two dicts could
    never be equal and NO archive could EVER validate, whatever it contained.
    Archives back to 2026-07-16 carry the same gap, which is why no annual unit
    has ever run.

    A hand-maintained mirror of a globbed inventory cannot stay correct: adding
    any file under traffic_sim/demand/ silently broke it. Delegating removes
    the failure mode rather than re-syncing the copy.

    Resolution is anchored to this file's own directory, not the process cwd,
    so a build launched from anywhere fingerprints the same tree.
    """
    return demand_source_paths(Path(__file__).resolve().parent)


SOURCE_FILES = _source_files()
# Captured at STARTUP, not when the metadata is written. A demand build runs
# for hours; hashing the sources at the end records whatever is on disk by
# then, which after a mid-build edit is code that never ran. Found 2026-07-21:
# an envelope whose candidates were generated at 14:24 was fingerprinted at
# 17:35 against an edited build_candidates.py, so its provenance named a
# generator it had not used - and the release guard that reads exactly this
# record would then refuse a perfectly consistent search.
STARTUP_SOURCE_HASHES = fingerprint_files(SOURCE_FILES)


def runtime_package_identity(packages: tuple[str, ...]) -> dict[str, str | None]:
    """Return the numerical runtime that can change deterministic artifacts.

    Source hashes and seeds are insufficient for byte-reproducible pool and
    picker caches: NumPy explicitly does not promise Generator streams across
    versions, and SciPy/HiGHS releases can change MILP behaviour.  Bind the
    relevant package versions plus Python/platform to cache identities instead
    of later labelling restored artifacts with whichever runtime is current.
    """
    identity: dict[str, str | None] = {
        "python": sys.version,
        "platform": platform.platform(),
    }
    for package in sorted(set(packages)):
        try:
            identity[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            identity[package] = None
    return identity


def demand_day_source_hashes(
    records: dict[str, dict] | None = None,
) -> dict[str, str | None]:
    """Bind a calibrated day to the complete canonical source inventory.

    A selective allow-list silently omitted newly introduced helpers such as
    ``traffic_sim/demand/structure_caps.py``.  That allowed the day library to
    restore picker output produced under old cap semantics.  The canonical
    inventory is already glob-complete, so use all of it here as well.
    """
    active = STARTUP_SOURCE_HASHES if records is None else records
    return {
        name: record.get("sha256")
        for name, record in sorted(active.items())
    }


def candidate_routing_weight_cache_input(weight_file: Path | None) -> Path:
    """Return the exact artifact that determines candidate routing costs.

    Congestion-feedback iterations must never restore a free-flow candidate
    pool just because the behavioural parameters match. The cache fingerprints
    file contents, so a stable missing sentinel cleanly distinguishes the
    initial free-flow pass from every real feedback-weight file.
    """
    return weight_file if weight_file is not None else SUMO_DIR / ".no-routing-weights"


def candidate_router_cache_input(home: Path) -> Path:
    """Return the exact router executable that defines candidate geometry.

    Candidate routes are outputs of ``duarouter``, not just of our Python
    generator.  SUMO routing behaviour can change between releases, so a
    cache key that fingerprints only the network, inputs and Python sources
    can otherwise restore an old route pool after a SUMO upgrade.  Hash the
    executable bytes as a required cache input; a missing binary remains a
    distinct, fail-closed fingerprint and the subsequent routing invocation
    still emits the operational error.
    """
    return Path(home) / "bin" / "duarouter"


def pfe_fit_by_day(report: dict, targets: list[dict],
                   days: int) -> list[dict]:
    """Recompute PFE's hourly GEH gate separately for every full day.

    A range-wide percentage can hide which date failed. Multi-day releases
    therefore retain one compact row per day and direction-split variant.
    ``achieved`` is kept only long enough to build these rows; the large
    edge×quarter map is still omitted from demand metadata.
    """
    if days <= 1:
        return []
    quarters_per_day = 96
    if len(targets) != days * quarters_per_day:
        raise ValueError(
            "multi-day PFE targets must contain exactly 96 quarters per day")
    achieved = report.get("achieved")
    if not isinstance(achieved, dict):
        raise ValueError("multi-day PFE report is missing achieved flows")

    rows = []
    for day_index in range(days):
        start = day_index * quarters_per_day
        end = start + quarters_per_day
        geh_ok = geh_total = 0
        for hour_start in range(start, end, 4):
            edges_in_hour: set[str] = set()
            for quarter in range(hour_start, hour_start + 4):
                edges_in_hour.update(targets[quarter])
            for edge_id in edges_in_hour:
                measured = sum(
                    achieved.get(edge_id, [0.0] * len(targets))[
                        hour_start:hour_start + 4])
                target = sum(
                    targets[quarter].get(edge_id, 0.0)
                    for quarter in range(hour_start, hour_start + 4))
                if measured + target <= 0:
                    continue
                geh = float(np.sqrt(
                    2 * (measured - target) ** 2 / (measured + target)))
                geh_total += 1
                geh_ok += geh < 5
        rows.append({
            "day": day_index + 1,
            "quarter_start": start,
            "quarter_end": end,
            "geh_ok": geh_ok,
            "geh_total": geh_total,
            "geh_pct": round(100 * geh_ok / max(1, geh_total), 1),
        })
    return rows


def fit_summary(report: dict, *, targets: list[dict] | None = None,
                days: int = 1) -> dict:
    """Keep publication-relevant calibration gates for one variant."""
    purpose = report.get("purpose_allocation_summary", {})
    result = {
        "geh_pct": report.get("geh_pct"),
        "infeasible_intervals": report.get("infeasible_intervals", 0),
        "vehicles": report.get("vehicles"),
        "unserviceable_edges": list(report.get("unserviceable_edges", [])),
        "bound_violations": list(report.get("bound_violations", [])),
        "relaxed_bound_violations": list(report.get("relaxed_bound_violations", [])),
        "purpose_incompatible_quarters": purpose.get(
            "quarters_with_incompatible_routes", 0),
        "purpose_mix_relaxed_quarters": purpose.get(
            "quarters_with_relaxed_mix", 0),
        "purpose_mix_reallocation_vehicles": purpose.get(
            "mix_reallocation_vehicles", 0),
        "purpose_replaced_routes": purpose.get("replaced_routes", 0),
        "relaxation_summary": report.get("relaxation_summary", {}),
    }
    if days > 1:
        if targets is None:
            raise ValueError("multi-day PFE summary requires frozen targets")
        result["per_day"] = pfe_fit_by_day(report, targets, days)
    return result


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
    p.add_argument("--pfe-workers", type=int, default=None,
                   help="Maximum PFE worker processes (default: CPU count; "
                        "use 1 for serial solving and publication)")
    p.add_argument("--through-share-target", type=float, default=0.25,
                   help="Enforced calibrated through share (start AND end "
                        "outside the canvas). The share is unidentifiable "
                        "from sensor counts (GEH indifferent, measured "
                        "2026-07-17) — this level is prior-anchored in "
                        "measured city-cordon studies (9-29%%), selected by "
                        "held-out LOSO (median 1.71→0.99) and confirmed on "
                        "a second day (1.63→1.11). NOT a measured "
                        "Gothenburg value. <=0 disables (emergent ~59%%).")
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

    if (not np.isfinite(args.through_share_target)
            or args.through_share_target >= 1):
        p.error("--through-share-target must be finite and below 1; "
                "values <=0 disable the target")
    if args.pfe_workers is not None and args.pfe_workers < 1:
        p.error("--pfe-workers must be a positive integer")
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
from demand.intake import (activity_purpose_shares_for_window, build_targets,
                           classify_day, demand_metadata, has_split_quantiles,
                           day_pool_blocks, load_direction_split,
                           load_sensor_edges, multi_day_blocks,
                           observed_sensor_series, real_day_shape,
                           target_series, validate_date_range,
                           window_pool_composition)

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
from demand.priors import (build_interval_constraints,
                           ensure_assignment_priors, ensure_bounds,
                           ensure_observability, ensure_priors,
                           opposite_direction_bounds,
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
from demand.calibration import (_agent_path_for, _report_is_publishable,
                                run_pfe_variants_flat_parallel,
                                warn_bound_violations,
                                warn_prior_relaxations,
                                warn_purpose_quota_relaxations,
                                warn_relaxed_bound_violations,
                                warn_widened_measurement_band,
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
    # Candidate departures are stored on the behavioural day's absolute
    # clock; target quarter zero is this build window's clock. Keeping this
    # offset explicit prevents a sub-day 06:00–10:00 run from inheriting the
    # candidate purpose mix for midnight–04:00.
    purpose_departure_offset_s = int((t0 - t0.normalize()).total_seconds())
    activity_purpose_shares = activity_purpose_shares_for_window(t0, n_intervals)
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

    def generate_candidates(weight_file: Path | None = None,
                            cache_date: str | None = None) -> None:
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
                "endpoint_location_report.json": SUMO_DIR / "endpoint_location_report.json",
            }
            cache_inputs = {
                "network": NET_PATH,
                # The router is part of the pool-producing implementation.
                # Its exact bytes, rather than only the later demand report's
                # human-readable SUMO version, must invalidate cached routes.
                "duarouter_binary": candidate_router_cache_input(home),
                "graph": Path("web/data/graph.graphml"),
                "map_network": GEO_PATH,
                "source_flows": flows_path,
                # Candidate anchoring is defined by the reviewed physical
                # registry, not merely by whichever keys happen to exist in
                # the current flow artifact. A new/re-snapped sensor must
                # never restore a pool built for the old edge set.
                "sensor_registry": Path("data_in/sensors.json"),
                "normal_profile": Path("web/data/normal_profile.json"),
                "direction_split": SUMO_DIR / "direction_split.json",
                "population": Path("data_in/deso/population_2023.json"),
                "deso": Path("data_in/deso/deso_goteborg.geojson"),
                # An official GeoJSON, if later supplied, wins over the OSM
                # fallback. Fingerprint both paths so either source changing
                # invalidates the candidate pool that depends on it.
                "official_buildings": Path("data_in/deso/buildings.geojson"),
                "osm_buildings": Path("data_in/deso/osm_buildings.geojson"),
                "poi": Path("data_in/deso/osm_pois.geojson"),
                "real_day_shape": day_shape_path or Path(
                    "sumo/.missing-real-day-shape"),
                "day_blocks": day_blocks_path or Path(
                    "sumo/.missing-day-blocks"),
                "routing_weights": candidate_routing_weight_cache_input(weight_file),
                # Gate draws follow the structural assignment field (2026-07-17)
                # — a changed field must invalidate the candidate pool.
                "assignment_priors": SUMO_DIR / "assignment_priors.json",
            }
            # Local: build_candidates does heavy module-level OSM/registry work
            # that every importer of this module would otherwise pay for.
            import build_candidates
            cache_config = {
                "n_total": n_total,
                "through_fraction": args.through_fraction,
                "gravity_km": args.gravity_km,
                "gravity_alpha": args.gravity_alpha,
                "cross_fraction": args.cross_fraction,
                "is_weekend": use_weekend_shape,
                # Candidate departures are keyed by calendar date (stage B),
                # so the date is part of the pool's identity. Without it two
                # dates whose measured day-shape happens to be identical (or
                # absent) would share a cache entry and one would silently be
                # served the other's candidates.
                # In day-library mode each day generates its own pool, and
                # the same calendar day must hit the same cache entry from
                # ANY window - keying on the window start would regenerate an
                # identical pool per envelope (cache_date is the day's date).
                "start_date": cache_date or range_start.strftime("%Y-%m-%d"),
                "min_per_sensor": 50,
                # Imported, never restated. This key must name the jitter the
                # pool was ACTUALLY built with, and the subprocess below does
                # not pass --route-diversity, so it inherits build_candidates'
                # default. A local copy of the number silently decoupled the
                # two the moment that default changed, which would have served
                # a pool built at one jitter for a request at another.
                "route_diversity": build_candidates.DEFAULT_ROUTE_DIVERSITY,
                "max_stretch": build_candidates.DEFAULT_MAX_STRETCH,
                "max_local_stretch": build_candidates.DEFAULT_MAX_LOCAL_STRETCH,
                # Changes WHICH route shapes exist per departure hour, so a
                # pool built at one floor must never be served for a request
                # at another.
                "pool_departure_floor":
                    build_candidates.POOL_DEPARTURE_UNIFORM_FLOOR,
                "seed": args.seed,
                # build_candidates uses Generator plus graph/geometry
                # libraries whose algorithms are outside our source tree.
                # A dependency upgrade must not inherit an older pool merely
                # because the seed and Python files are unchanged.
                "runtime": runtime_package_identity((
                    "networkx", "numpy", "osmnx", "shapely")),
                # The content fingerprint above is the identity. Keep this
                # label stable so moving a byte-identical weight file does
                # not create a needless second cache entry.
                "routing_cost_mode": "feedback" if weight_file is not None else "free_flow",
            }
            cache_sources = {
                "build_candidates": Path("build_candidates.py"),
                "build_sumo_demand": Path(__file__),
                "build_data": Path("build_data.py"),
                "dirsplit_geo": Path("dirsplit/geo.py"),
                "endpoint_locations": Path("demand/locations.py"),
                "candidate_cache": Path("traffic_sim/demand/cache.py"),
                "sensor_registry_loader": Path(
                    "traffic_sim/intake/sensors.py"),
                "pipeline_fingerprint": Path("traffic_sim/core/fingerprint.py"),
            }
            cache_key = candidate_cache.cache_key(
                cache_config, cache_inputs, cache_sources)
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
                  "--assignment-priors", str(SUMO_DIR / "assignment_priors.json"),
                  "--n-total", str(n_total), "--seed", str(args.seed)]
            if use_weekend_shape:
                cmd += ["--is-weekend"]
            if day_shape_path is not None:
                cmd += ["--real-day-shape-file", str(day_shape_path)]
            if day_blocks_path is not None:
                cmd += ["--day-blocks-file", str(day_blocks_path)]
            else:
                # Single-day builds carry their date the same way a multi-day
                # block does, so one day's candidates are identical whether it
                # is built alone or inside a window (stage B).
                cmd += ["--date", range_start.strftime("%Y-%m-%d")]
            if weight_file is not None:
                cmd += ["--weight-file", str(weight_file),
                       "--weight-period", str(BPR_PERIOD_S)]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
            print(res.stdout[-1200:])
            if res.returncode != 0:
                print(res.stderr[-1500:])
                sys.exit("build_candidates.py failed")
            try:
                # The first candidate build may materialise an OSM building/
                # POI cache that was absent when ``cache_key`` was computed.
                # Store under the post-build input identity so the *next*
                # run can restore immediately instead of needlessly doing a
                # second complete candidate generation just to see the new
                # cache file in its fingerprint.
                store_key = candidate_cache.cache_key(
                    cache_config, cache_inputs, cache_sources)
                candidate_cache.store(candidate_cache.DEFAULT_ROOT,
                                      store_key, cache_outputs)
                print(f"  candidate cache stored {store_key}")
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
    # Preserve the exact directional Level-1 targets that this demand build
    # used.  Scenario playback must be able to distinguish its displayed
    # representative seed from the ensemble mean and from the calibration
    # target, without reconstructing a potentially newer split model later.
    targets_by_variant: dict[str, list[dict[str, float]]] = {}
    report = None
    candidate_provenance = None
    edge_support_augmentation = None

    if args.engine == "pfe":
        # ── The full hierarchy: hard counts + conservation bounds + priors ────
        from traffic_sim.demand import pfe
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
        assign_data = (timed(
                           "assignment_priors",
                           lambda: ensure_assignment_priors(
                               gravity_km=args.gravity_km,
                               through_fraction=args.through_fraction,
                               cross_fraction=args.cross_fraction,
                               gravity_alpha=args.gravity_alpha,
                               seed=args.seed))
                       if not args.no_assignment_prior
                       else {"weight": 0.0, "flows": {}})
        assign_w    = assign_data.get("weight", 0.0)
        assign_flows = assign_data.get("flows", {})
        if assign_flows:
            print(f"  gravity-assignment prior: {len(assign_flows)} otherwise-"
                  f"unconstrained edges get a weak (w={assign_w}) realistic pull")
        prior_variant = {"": "prior", "_v1": "prior_low", "_v2": "prior_high"}

        # The unmeasured carriageway at every single-direction station, as an
        # interval the solver must satisfy. Per variant, because the interval
        # comes from the direction model's own q10/q90 and therefore moves
        # with the variant being built.
        opposite_by_variant = {
            suffix: opposite_direction_bounds(
                flows, n_intervals, qi_start, split_key=key)
            for suffix, key in variants
        }

        def build_bounds_priors(suffix: str) -> tuple[list[dict], list[dict], list[dict]]:
            return build_interval_constraints(
                n_intervals, qi_start, bounds_data, priors_data, corridor,
                assign_data, prior_variant.get(suffix, "prior"),
                opposite_bounds=opposite_by_variant.get(suffix))

        # ── Day library: build each calendar day once, then assemble ──────────
        # A closure search re-derives the same calendar days for every
        # overlapping envelope. Since stage B made a day's demand a function
        # of the day itself, a whole-day window is now built as
        # "build the missing days, then concatenate" - and assembly is the
        # ONLY way a multi-day window is produced, so there is no second
        # implementation that could drift (plan invariant 3).
        #
        # Sub-day windows (06:00-10:00) have no day to store and keep the
        # direct path, as do congestion-feedback and legacy-pool builds,
        # whose candidate pool depends on the whole window's own solution.
        whole_day_window = (
            n_intervals % 96 == 0
            and purpose_departure_offset_s == 0
            and n_intervals == args.days * 96
        )
        use_day_library = (
            whole_day_window
            and not args.legacy_random_pool
            and (1 if args.legacy_random_pool else args.congestion_iterations) == 1
        )
        composition = window_pool_composition(range_start, args.days)
        # The stored day directories this window was assembled from, empty
        # unless the day library actually ran. It selects which provenance
        # contract the published window is held to below, so it must be set by
        # the code that assembles, not inferred from flags that only say what
        # was INTENDED.
        assembled_day_dirs: list[Path] = []

        def day_identity(day: pd.Timestamp, day_index: int,
                         variant_inputs: dict) -> DayIdentity:
            """Everything this day's calibration is a function of."""
            span = slice(day_index * 96, (day_index + 1) * 96)
            constraints = {
                suffix: {
                    "targets": variant_inputs[suffix]["targets"][span],
                    "bounds": variant_inputs[suffix]["bounds_pq"][span],
                    "hard_bounds": variant_inputs[suffix]["hard_bounds_pq"][span],
                    "priors": variant_inputs[suffix]["priors_pq"][span],
                }
                for suffix, _key in variants
            }
            return DayIdentity(
                date=day.strftime("%Y-%m-%d"),
                source=args.source,
                pool_composition=composition,
                inputs={
                    "constraints": _digest_payload(constraints),
                    "purpose_shares": _digest_payload(
                        activity_purpose_shares[span]),
                    "through_share_target": args.through_share_target,
                    "candidate_pool": sha256_file(cand_path),
                    "candidate_metadata": sha256_file(
                        cand_path.with_name("candidates.meta.json")),
                    "edge_geometry": sha256_file(GEO_PATH),
                    "variants": [key for _suffix, key in variants],
                    "picker_runtime": runtime_package_identity((
                        "numba", "numpy", "scipy")),
                },
                source_hashes=demand_day_source_hashes(),
            )

        def calibrate_window(variants, variant_inputs, **options):
            if not use_day_library:
                return run_pfe_variants_flat_parallel(
                    cand_path, variants, variant_inputs, **options)
            nonlocal day_blocks_path
            library = DayLibrary()
            nonlocal assembled_day_dirs
            day_blocks_path = SUMO_DIR / "candidate_day_blocks.json"
            day_directories: list[Path] = []
            per_variant_reports: dict[str, list[dict]] = {
                suffix: [] for suffix, _key in variants}
            reused = 0
            for day_index in range(args.days):
                day = range_start + pd.Timedelta(days=day_index)
                day_blocks_path.write_text(json.dumps(day_pool_blocks(
                    flows, sensor_edges, day, qi_start + day_index * 96,
                    composition)))
                generate_candidates(weight_file,
                                    cache_date=day.strftime("%Y-%m-%d"))
                identity = day_identity(day, day_index, variant_inputs)
                entry = library.get(identity)
                if entry is None:
                    _calibrate_one_day(library, identity, day_index,
                                       variants, variant_inputs, options)
                    entry = library.get(identity)
                    if entry is None:
                        raise RuntimeError(
                            f"demand day {identity.date} did not store")
                else:
                    reused += 1
                    print(f"  day {identity.date}: library hit "
                          f"{identity.key[:12]}")
                day_directories.append(library.path_for(identity))
                for suffix, _key in variants:
                    per_variant_reports[suffix].append(json.loads(
                        (library.path_for(identity)
                         / f"fit{suffix}.json").read_text()))
            print(f"  demand day library: {reused}/{args.days} day(s) reused, "
                  f"{args.days - reused} calibrated")
            # Hand the proven days to the window-level check. Non-empty is what
            # marks this window as day-assembled: its candidate ids belong to
            # per-day pools, not to whatever pool is left on disk.
            assembled_day_dirs = list(day_directories)
            # The direction variants form one demand contract, exactly as in
            # the direct path: assemble every variant to a staged sibling,
            # verify every merged report against the same publication gate,
            # and only then flip the complete set. Without this, a crash
            # after q50's assembly leaves a hybrid on disk - the failure mode
            # the staged flow in run_pfe_variants_flat_parallel exists to
            # prevent, which the library path must not quietly reintroduce.
            reports = {}
            staged: list[tuple[Path, Path]] = []
            try:
                for suffix, _key in variants:
                    out_path = Path(variant_inputs[suffix]["out_path"])
                    staged_path = out_path.with_name(out_path.name + ".staged")
                    assemble_window(
                        day_directories, staged_path,
                        _agent_path_for(staged_path),
                        names=(f"calibrated{suffix}.rou.xml",
                               f"calibrated{suffix}.agents.json"))
                    staged.append((staged_path, out_path))
                    report = merge_day_reports(per_variant_reports[suffix])
                    if not _report_is_publishable(report):
                        raise RuntimeError(
                            f"assembled window failed the publication gate "
                            f"for calibrated{suffix}; no variants published")
                    if not variant_inputs[suffix].get("keep_achieved", False):
                        report.pop("achieved", None)
                    reports[suffix] = report
                for staged_path, out_path in staged:
                    os.replace(staged_path, out_path)
                    os.replace(_agent_path_for(staged_path),
                               _agent_path_for(out_path))
            finally:
                for staged_path, _out_path in staged:
                    staged_path.unlink(missing_ok=True)
                    _agent_path_for(staged_path).unlink(missing_ok=True)
            return reports

        def _calibrate_one_day(library, identity, day_index, variants,
                               variant_inputs, options):
            """Calibrate and publish ONE day, day-local, into the library."""
            span = slice(day_index * 96, (day_index + 1) * 96)
            with tempfile.TemporaryDirectory(prefix="demand-day-") as scratch:
                scratch_dir = Path(scratch)
                day_inputs = {
                    suffix: {
                        **variant_inputs[suffix],
                        "targets": variant_inputs[suffix]["targets"][span],
                        "bounds_pq": variant_inputs[suffix]["bounds_pq"][span],
                        "hard_bounds_pq":
                            variant_inputs[suffix]["hard_bounds_pq"][span],
                        "priors_pq": variant_inputs[suffix]["priors_pq"][span],
                        "out_path": scratch_dir / f"calibrated{suffix}.rou.xml",
                        # The window report is merged from these, so a day
                        # always keeps its own achieved map.
                        "keep_achieved": True,
                    }
                    for suffix, _key in variants
                }
                day_options = dict(options)
                day_options["activity_purpose_shares_by_quarter"] = (
                    activity_purpose_shares[span])
                day_options["day_quarters"] = 96
                day_options["purpose_departure_offset_s"] = 0.0
                day_reports = run_pfe_variants_flat_parallel(
                    cand_path, variants, day_inputs, **day_options)
                artifacts = {}
                for suffix, _key in variants:
                    route = scratch_dir / f"calibrated{suffix}.rou.xml"
                    artifacts[route.name] = route
                    agents = _agent_path_for(route)
                    artifacts[agents.name] = agents
                    fit = scratch_dir / f"fit{suffix}.json"
                    fit.write_text(json.dumps(day_reports[suffix],
                                              separators=(",", ":")))
                    artifacts[fit.name] = fit
                # Prove provenance HERE, where the context holds: cand_path and
                # its sidecar are THIS day's pool right now, and the day's own
                # ``d0_`` ids resolve against it exactly. Once the loop moves on
                # this pool is overwritten by the next day's, so a window can
                # never re-derive it -- which is precisely the bug that stopped
                # the 2026-08-06 warming launch. The stored proof is what
                # validate_assembled_provenance verifies later, and it travels
                # with the day, so a LIBRARY HIT carries its proof forward
                # instead of silently skipping the check.
                day_provenance = validate_calibrated_provenance(
                    cand_path,
                    cand_path.with_name(
                        cand_path.name.replace(".rou.xml", ".meta.json")),
                    [(scratch_dir / f"calibrated{suffix}.rou.xml",
                      _agent_path_for(scratch_dir / f"calibrated{suffix}.rou.xml"))
                     for suffix, _key in variants],
                )
                record = scratch_dir / DAY_PROVENANCE_NAME
                record.write_text(json.dumps(day_provenance,
                                             separators=(",", ":")))
                artifacts[record.name] = record
                library.put(identity, artifacts, fit={
                    "geh_pct": day_reports[""]["geh_pct"],
                    "vehicles": day_reports[""]["vehicles"],
                })

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
                                            n_intervals, split_key=key,
                                            anchor_day=args.start_date,
                                            anchor_epoch=source_epoch)
                    targets_by_variant[key] = targets
                    bounds_pq, priors_pq, hard_bounds_pq = build_bounds_priors(suffix)
                    out = calib_path if suffix == "" else SUMO_DIR / f"calibrated{suffix}.rou.xml"
                    variant_inputs[suffix] = {
                        "out_path": out,
                        "targets": targets,
                        "bounds_pq": bounds_pq,
                        "hard_bounds_pq": hard_bounds_pq,
                        "priors_pq": priors_pq,
                        # Multi-day publication must prove every date
                        # independently. Keep the large achieved map only
                        # until fit_summary has compacted it into daily rows.
                        "keep_achieved": args.days > 1,
                        "required_anchor_edges": sorted({
                            edge for edges in sensor_edges.values()
                            for edge in edges
                        }),
                    }
                reports = timed(
                    "pfe_variants_and_rounding",
                    lambda: calibrate_window(
                        variants, variant_inputs,
                        max_workers=args.pfe_workers or (os.cpu_count() or 1),
                        purpose_departure_offset_s=purpose_departure_offset_s,
                        activity_purpose_shares_by_quarter=activity_purpose_shares,
                        through_share_target=args.through_share_target,
                        # Whole-day windows restart each day's endpoint draw
                        # ordinals, so a day's published vehicles depend only
                        # on that day (stage B). Sub-day windows have no day
                        # boundary to restart at.
                        day_quarters=(96 if n_intervals % 96 == 0 else None)))
                for suffix, key in variants:
                    variant_report = reports[suffix]
                    variant_fit_reports[key] = fit_summary(
                        variant_report,
                        targets=variant_inputs[suffix]["targets"],
                        days=args.days)
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
                    warn_relaxed_bound_violations(variant_report, key)
                    warn_purpose_quota_relaxations(variant_report, key)
                    warn_prior_relaxations(variant_report, key)
                    warn_widened_measurement_band(variant_report, key)
                    if variant_report["geh_pct"] < 100:
                        print("  ⚠ measured-edge fit below gate — inspect before use")
                report = reports[""]
                break

            targets = build_targets(flows, sensor_edges, qi_start,
                                    n_intervals, split_key="edge_shares",
                                    anchor_day=args.start_date,
                                    anchor_epoch=source_epoch)
            targets_by_variant["edge_shares"] = targets
            bounds_pq, priors_pq, hard_bounds_pq = build_bounds_priors("")
            report = timed(
                "pfe_and_rounding",
                lambda: pfe.calibrate(
                    cand_path, calib_path, targets,
                    bounds_pq, priors_pq,
                    enforce_integer_bounds=True,
                    integer_bounds_per_q=hard_bounds_pq,
                    purpose_departure_offset_s=purpose_departure_offset_s,
                    activity_purpose_shares_by_quarter=activity_purpose_shares,
                    through_share_target=args.through_share_target,
                    required_anchor_edges=sorted({
                        edge for edges in sensor_edges.values()
                        for edge in edges
                    })))
            tag = f"[congestion-feedback {iteration+1}/{n_iter}]" if n_iter > 1 else "PFE"
            print(f"  {tag} edge_shares       {report['vehicles']:>6} veh  "
                  f"GEH<5: {report['geh_pct']}%  "
                  f"(infeasible intervals: {report['infeasible_intervals']})")
            warn_unserviceable_measured_edges(report, "edge_shares")
            warn_bound_violations(report, "edge_shares")
            warn_relaxed_bound_violations(report, "edge_shares")
            warn_purpose_quota_relaxations(report, "edge_shares")
            warn_prior_relaxations(report, "edge_shares")
            warn_widened_measurement_band(report, "edge_shares")

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
            targets_by_variant[key] = build_targets(
                flows, sensor_edges, qi_start, n_intervals, split_key=key,
                anchor_day=args.start_date, anchor_epoch=source_epoch)
            counts_path = SUMO_DIR / f"counts{suffix}.xml"
            n = write_counts(flows, sensor_edges, qi_start, n_intervals,
                             counts_path, split_key=key,
                             anchor_day=args.start_date,
                             anchor_epoch=source_epoch)
            print(f"Wrote {counts_path}  ({n} edge×interval measurements)")
            print(f"Sampling routes to match counts ({key}) …")
            run_tool("routeSampler.py", [
                "-r", str(cand_path),
                "--edgedata-files", str(counts_path),
                "--edgedata-attribute", "count",
                "-o", str(SUMO_DIR / f"calibrated{suffix}.rou.xml"),
                "--seed", str(args.seed),
            ], home)

    if args.engine == "pfe":
        # BASELINE RULE (2026-08-05): no synthetic support vehicles. The
        # augmentation added routes built with forbidden_edges=measured, i.e.
        # traffic that by construction can never cross a sensor. Under the
        # rule "only what is measured is simulated" it must not exist.
        edge_support_augmentation = {"schema_version": 1,
                                     "status": "disabled_baseline_rule",
                                     "variants": {}}
        variant_artifacts = []
        for suffix, _key in variants:
            route_path = (calib_path if suffix == ""
                          else SUMO_DIR / f"calibrated{suffix}.rou.xml")
            variant_artifacts.append((route_path, _agent_path_for(route_path)))
        if assembled_day_dirs:
            # Day-assembled window: every candidate id belongs to the pool of
            # the DAY that drew it, and each day's pool was overwritten by the
            # next one. Resolving these ids against the single pool left on
            # disk is not a weaker check, it is a WRONG one -- it would compare
            # day 0's agents against day 1's candidates. The days carry their
            # own proofs instead; this verifies them and binds them to the
            # published artifacts.
            candidate_provenance = validate_assembled_provenance(
                assembled_day_dirs, variant_artifacts)
            print("  calibrated candidate provenance: "
                  f"{candidate_provenance['vehicles']} vehicles across "
                  f"{len(candidate_provenance['variants'])} variant(s) from "
                  f"{len(candidate_provenance['days'])} proven day(s) — PASS")
        else:
            candidate_provenance = validate_calibrated_provenance(
                cand_path,
                cand_path.with_name(
                    cand_path.name.replace(".rou.xml", ".meta.json")),
                variant_artifacts,
            )
            print("  calibrated candidate provenance: "
                  f"{candidate_provenance['vehicles']} vehicles across "
                  f"{len(candidate_provenance['variants'])} variant(s) — PASS")

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
            # Provenance contract: prior-anchored + held-out-selected +
            # second-day-confirmed — never a measured Gothenburg value.
            # validate_sim reads this so LOSO always validates the shipped
            # configuration.
            "through_share_target": args.through_share_target,
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
    if candidate_provenance is not None:
        meta["candidate_provenance"] = candidate_provenance
    if edge_support_augmentation is not None:
        meta["edge_support_augmentation"] = edge_support_augmentation
    if targets_by_variant:
        meta["sensor_targets"] = {
            "schema_version": 1,
            "variants": {
                key: target_series(targets)
                for key, targets in targets_by_variant.items()
            },
        }
        # The source value is distinct from a directional target for a
        # two-way Total station.  Persist both so the UI can state exactly
        # what was measured/forecast and exactly what was derived from it.
        meta["sensor_observations"] = observed_sensor_series(
            flows, sensor_edges, qi_start, n_intervals)
        # Make the reviewed sensor/network pairing explicit in the demand
        # artifact.  The build fingerprint also hashes these files, but this
        # compact contract lets a publisher and UI explain exactly which
        # registry and graph produced the constraints without recomputing it.
        meta["sensor_contract"] = {
            "schema_version": 1,
            "registry_sha256": sha256_file(Path("data_in/sensors.json")),
            "network_sha256": sha256_file(NET_PATH),
            "sensor_edges": {
                str(sensor_id): sorted(str(edge_id) for edge_id in edges)
                for sensor_id, edges in sorted(sensor_edges.items())
            },
        }
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
        # Name the yardstick. These two L1s are against DIFFERENT targets and
        # were previously both printed as "vs RVU short bins", which made
        # calibration look like it had wrecked a fit generation had nailed.
        yard = ("availability-corrected RVU"
                if tl.get("target_is_availability_corrected") else "RAW RVU")
        print(f"  calibrated structure: destinations within {prox['radius_m']:.0f} m "
              f"of a sensor {prox['pct_within']}% (all-edges baseline "
              f"{prox['baseline_pct_within']}%), trip-length shares {tl['shares']} "
              f"L1={tl['l1_distance']} vs {yard}"
              + (f" (L1 vs raw RVU {tl['l1_vs_raw_rvu']}, which an intra-canvas "
                 f"tour structurally cannot reach)"
                 if tl.get("target_is_availability_corrected") else ""))
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
        "calibrated_q50_agents": SUMO_DIR / "calibrated.agents.json",
        "demand_spec": demand_spec_path,
    }
    for suffix, _key in variants:
        if suffix:
            fingerprint_artifacts[f"calibrated{suffix}"] = (
                SUMO_DIR / f"calibrated{suffix}.rou.xml")
            fingerprint_artifacts[f"calibrated{suffix}_agents"] = (
                SUMO_DIR / f"calibrated{suffix}.agents.json")
    # Keep main's resolver rather than stage B's module-level SOURCE_FILES:
    # both survive the merge, but demand_source_paths() is what the current
    # tree fingerprints with.
    source_files = demand_source_paths(Path.cwd())
    # The exact contract is written only after all expensive calibration and
    # structure gates have completed, immediately before the matching metadata
    # fingerprint is created.
    write_demand_build_spec(demand_spec_path, demand_spec)
    meta["build_fingerprint"] = make_fingerprint(
        source_file_records=STARTUP_SOURCE_HASHES,
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
    # Archive only the files this build can produce.  A glob here is unsafe:
    # SUMO keeps old closure-specific routes in the shared legacy directory,
    # so earlier runs accidentally archived unrelated files as if they were
    # part of the new demand build. Missing optional q10/q90 variants are
    # omitted rather than recorded as phantom outputs.
    for product in demand_run_products(SUMO_DIR):
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


def demand_run_products(sumo_dir: Path = SUMO_DIR) -> list[Path]:
    """Return only demand artifacts produced by one calibration run.

    The legacy ``sumo/`` directory is shared with closure experiments, so
    callers must never use a broad glob when constructing an immutable run
    manifest. Optional direction variants are included only when present.
    """
    sumo_dir = Path(sumo_dir)
    candidates = [
        sumo_dir / "demand_meta.json",
        sumo_dir / "demand_build_spec.json",
        # The calibrated structure and purpose audit is relative to this exact
        # generated pool. Archive it with the release so restoring demand can
        # never silently pair calibrated routes with another run's pool.
        sumo_dir / "candidates.rou.xml",
        sumo_dir / "candidates.meta.json",
        sumo_dir / "calibrated.rou.xml",
        sumo_dir / "calibrated.agents.json",
        sumo_dir / "calibrated_v1.rou.xml",
        sumo_dir / "calibrated_v1.agents.json",
        sumo_dir / "calibrated_v2.rou.xml",
        sumo_dir / "calibrated_v2.agents.json",
    ]
    return [path for path in candidates if path.is_file()]


if __name__ == "__main__":
    if parent_holds_lock():
        _tracked_main()
    else:
        with demand_build_lock():
            _tracked_main()
