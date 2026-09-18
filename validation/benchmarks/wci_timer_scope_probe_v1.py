#!/usr/bin/env python3
"""What each daily-cost timer actually covers, measured rather than argued.

Two published numbers for the same build key disagree: the 2026-09-17
canary recorded 175.9 s and the 2026-09-18 A/B baseline arm recorded about
342 s. They are different timers over different scopes, on a machine whose
speed may also have moved. This probe runs one build key once and reports
every sub-scope separately, so both published figures can be reconstructed
from the same run and whatever is left over is attributed honestly.

It changes no code to make the numbers agree. Diagnostic and append-only
(``release_evidence: false``).
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_timer_scope_probe_v1"


def _usage() -> Dict[str, float]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"user_s": usage.ru_utime, "sys_s": usage.ru_stime,
            "max_rss_bytes": int(usage.ru_maxrss)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--build-key", required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")
    if args.cache_root.exists():
        raise SystemExit("the probe needs a fresh cache root: "
                         f"{args.cache_root}")

    from tools import build_daily_cost_cache as builder
    from tools import build_window_cost_index as wci
    from tools import freeze_wci_month_case as month
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation import disruption as disruption_analysis
    from traffic_sim.simulation.deterministic_disruption import (
        ArchiveDisruptionProvider, DailyCostCache, NetworkCostModel)

    registration = json.loads(args.registration.read_text(encoding="utf-8"))

    marks: Dict[str, float] = {}
    started = time.perf_counter()
    problems = month.verify_registration(registration)
    marks["verify_registration_s"] = time.perf_counter() - started
    if problems:
        raise SystemExit(f"the registration does not verify: {problems}")

    at = time.perf_counter()
    bound = wci._bound_inputs(args.profile)
    marks["bound_inputs_s"] = time.perf_counter() - at

    spec = ClosureSearchSpec.from_dict(registration["spec"])
    at = time.perf_counter()
    grouped = builder.units_of(spec, bound["runs_root"],
                               bound["qualified_manifest"])
    marks["units_of_s"] = time.perf_counter() - at
    units = grouped[args.build_key]

    at = time.perf_counter()
    identity = builder.identity_of(registration, args.build_key)
    drift = builder.content_drift(identity)
    marks["identity_and_content_drift_s"] = time.perf_counter() - at
    if drift:
        raise SystemExit(f"content drift: {drift}")

    # 1. The network model: 16 MB of XML plus the adjacency build. The A/B
    #    arm timed this; the canary's build_batch timer did not.
    at = time.perf_counter()
    network = NetworkCostModel()
    marks["network_cost_model_s"] = time.perf_counter() - at

    archive = Path(registration["archives"][args.build_key]["archive"])
    cache = DailyCostCache(args.cache_root)
    at = time.perf_counter()
    provider = ArchiveDisruptionProvider(spec, archive=archive,
                                         network=network, cache=cache)
    marks["provider_open_s"] = time.perf_counter() - at

    # 2. Pricing. The first unit also pays the one-off import of
    #    run_scenario and its scientific stack, inside both published timers.
    per_unit = []
    compute_started = time.perf_counter()
    for index, (unit_id, _unit_identity, schedule) in enumerate(units):
        at = time.perf_counter()
        provider.disruption(schedule)
        elapsed = time.perf_counter() - at
        per_unit.append({"unit_id": str(unit_id), "wall_s": elapsed})
        if index == 0:
            marks["first_unit_s"] = elapsed
    marks["price_all_units_s"] = time.perf_counter() - compute_started

    # 3. The reload verification: inside the canary's build_batch timer,
    #    outside the A/B arm's.
    at = time.perf_counter()
    fresh = DailyCostCache(args.cache_root)
    mismatches = []
    for unit_id, _unit_identity, schedule in units:
        stored = fresh.load(provider.cache_identity(schedule))
        if stored is None:
            mismatches.append(str(unit_id))
    marks["reload_verification_s"] = time.perf_counter() - at

    rest = [item["wall_s"] for item in per_unit[1:]]
    usage = _usage()
    canary_scope = marks["price_all_units_s"] + marks["reload_verification_s"]
    ab_scope = (marks["network_cost_model_s"] + marks["provider_open_s"]
                + marks["price_all_units_s"])
    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "claim_boundary": ("one build key, one process, one machine; it "
                           "separates timer scope from machine speed and "
                           "changes nothing to make the numbers agree"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "build_key": args.build_key,
        "archive": str(archive),
        "daily_units": len(units),
        "marks_s": marks,
        "per_unit_s": {
            "first": marks["first_unit_s"],
            "rest_median": sorted(rest)[len(rest) // 2],
            "rest_low": min(rest),
            "rest_high": max(rest),
            "rest_mean": sum(rest) / len(rest),
        },
        "reconstructed_scopes_s": {
            "canary_build_batch_timer": canary_scope,
            "ab_arm_timer": ab_scope,
            "scope_difference": ab_scope - canary_scope,
            "what_the_canary_timer_covers": [
                "every daily unit priced through provider.disruption, "
                "including the one-off import of run_scenario on the first",
                "the reload of every stored record through a fresh cache",
            ],
            "what_the_canary_timer_excludes": [
                "verify_registration", "_bound_inputs", "units_of",
                "identity_of and content_drift",
                "NetworkCostModel construction",
                "opening the archive (ArchiveInputs)",
            ],
            "what_the_ab_arm_timer_adds": [
                "NetworkCostModel construction",
                "opening the archive (ArchiveInputs)",
            ],
            "what_the_ab_arm_timer_drops": ["the reload verification"],
        },
        "resources": {"max_rss_bytes": usage["max_rss_bytes"],
                      "user_s": usage["user_s"], "sys_s": usage["sys_s"],
                      "lifetime_max_phys_footprint_bytes":
                          common.process_memory()[
                              "lifetime_max_phys_footprint_bytes"]},
        "published_figures_for_comparison": {
            "canary_20260917_v2_build_wall_s": 175.875014,
            "parse_ab_20260918_v1_baseline_arm_wall_s": [343.363064041,
                                                         341.816591959],
            "note": ("both were produced by the same b627887 costing code "
                     "on the same build key and archive"),
        },
        "mismatches": mismatches,
        "max_assumed_congestion_delay_s":
            disruption_analysis.MAX_ASSUMED_CONGESTION_DELAY_S,
    }
    published = common.publish(args.out, record)
    print(json.dumps({"marks_s": marks,
                      "per_unit_s": record["per_unit_s"],
                      "canary_scope_s": canary_scope,
                      "ab_scope_s": ab_scope,
                      "content_key": published["content_key"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
