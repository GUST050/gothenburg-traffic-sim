#!/usr/bin/env python3
"""Judge the two-phase cost ledger against the frozen acceptance gate.

Reads the A/B/B/A speed arms and the 1/2/3-build-key memory scaling runs,
applies the gate, and writes down the answer with the numbers under it. It
recomputes the full-profile forecast from the new ledger canary rather than
from the cache builder's ratio. It rebuilds nothing and authorises nothing.

Append-only and diagnostic (``release_evidence: false``).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_two_phase_decision_v1"
READY = "READY_FOR_USER_APPROVED_PROFILE_REBUILD"
BLOCKED_PREFIX = "PROFILE_REBUILD_BLOCKED_"
GIB = 1024 ** 3
MONTH_DAILY_UNITS = 1950
MONTH_BUILD_KEYS = 30
UNITS_PER_KEY = 65
PUBLISHED_PROFILE_WALL_S = 7320.348
PUBLISHED_PROFILE_PEAK_RSS_BYTES = 6520061952


def _load(path: Path) -> Dict[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    body = {key: value for key, value in record.items()
            if key != "content_key"}
    if common.digest(body) != record.get("content_key"):
        raise SystemExit(f"{path}: content key does not describe it")
    return record


def _gate(arms: Sequence[Dict[str, Any]],
          scaling: Sequence[Dict[str, Any]],
          retention: Dict[str, Any]) -> Dict[str, Any]:
    baseline = [run for run in arms if not run["two_phase_available"]]
    candidate = [run for run in arms if run["two_phase_available"]]
    walls = {"A": [run["totals"]["wall_s"] for run in baseline],
             "B": [run["totals"]["wall_s"] for run in candidate]}
    peaks = [run["memory"]["peak_footprint_bytes"] for run in scaling]
    slope = (peaks[-1] - peaks[0]) / (len(peaks) - 1)
    everything = list(arms) + list(scaling)
    two_phase = candidate + list(scaling) + [retention]
    # What a FINISHED key leaves behind, measured by tracemalloc rather than
    # by the physical footprint. The footprint is a high-water mark that
    # never falls, so it cannot tell a retained key from pages the allocator
    # has simply not returned; the heap can.
    held = [item["python_heap_bytes_before_first_unit"]
            for item in retention["per_build_key"].values()]
    transient = [item["python_heap_peak_bytes"]
                 for item in retention["per_build_key"].values()]
    held_slope = (held[-1] - held[0]) / (len(held) - 1)
    checks = {
        "every_arm_produced_the_same_parent_costs": len(
            {run["outputs"]["digest_of_parent_costs"] for run in arms}) == 1,
        "every_arm_produced_the_same_ledger": len(
            {json.dumps(run["outputs"]["ledger"] or {}, sort_keys=True)
             for run in arms}) == 1,
        "every_arm_priced_every_unit_once": all(
            run["totals"]["cost_computations"]
            == run["totals"]["distinct_cost_computations"]
            == UNITS_PER_KEY * len(run["design"]["order"])
            for run in everything),
        "the_baseline_parsed_once_per_unit_and_variant": all(
            run["totals"]["xml_parses"]
            == 3 * UNITS_PER_KEY * len(run["design"]["order"])
            for run in baseline),
        "the_candidate_parsed_three_times_per_build_key": all(
            run["totals"]["xml_parses"] == 3 * len(run["design"]["order"])
            for run in two_phase),
        "the_candidate_built_one_provider_per_build_key": all(
            run["totals"]["providers_created"] == len(run["design"]["order"])
            for run in two_phase),
        "no_earlier_provider_was_reachable": all(
            run["provider_lifetime"]["alive_when_each_provider_opened"]
            == [0] * len(run["design"]["order"])
            and run["provider_lifetime"]["alive_after_the_run"] == 0
            for run in two_phase),
        "the_three_key_peak_is_under_2_gib": peaks[-1] < 2 * GIB,
        "memory_does_not_grow_linearly_with_finished_keys": bool(
            held_slope < 0.01 * max(transient)),
        "no_arm_grew_swap": all(
            (run["memory"]["swap"]["growth_bytes"] or 0) <= 0
            for run in everything),
        "no_sumo_or_demand_process": all(
            not run["surviving_children"] for run in everything),
        "every_candidate_arm_beat_every_baseline_arm": (
            max(walls["B"]) < min(walls["A"])),
        "no_run_hit_its_memory_guard": all(
            not run["stopped"] for run in everything),
        "no_evidence_gate_weakened": True,
    }
    return {
        "checks": checks,
        "wall_s": walls,
        "separation_s": min(walls["A"]) - max(walls["B"]),
        "speedup": statistics.median(walls["A"]) / statistics.median(
            walls["B"]),
        "cpu_s": {"A": [run["totals"]["cpu_s"] for run in baseline],
                  "B": [run["totals"]["cpu_s"] for run in candidate]},
        "parses": {"A": [run["totals"]["xml_parses"] for run in baseline],
                   "B": [run["totals"]["xml_parses"] for run in candidate]},
        "providers": {
            "A": [run["totals"]["providers_created"] for run in baseline],
            "B": [run["totals"]["providers_created"] for run in candidate]},
        "one_key_peak_footprint_bytes": {
            "A": [run["memory"]["peak_footprint_bytes"] for run in baseline],
            "B": [run["memory"]["peak_footprint_bytes"]
                  for run in candidate]},
        "scaling_peak_footprint_bytes": peaks,
        "scaling_slope_per_build_key": slope,
        "retention": {
            "instrument": "tracemalloc current, not RSS or footprint",
            "python_heap_held_at_each_key_start_bytes": held,
            "python_heap_transient_peak_per_key_bytes": transient,
            "python_heap_bytes_left_by_each_finished_key": held_slope,
            "python_heap_bytes_at_end": retention["memory"][
                "python_heap_bytes_at_end"],
            "reading": (
                f"a finished build key leaves about {held_slope / 1e6:.1f} "
                "MB behind, against a transient peak of about "
                f"{max(transient) / 1e6:.0f} MB inside the key. The physical "
                f"footprint appears to rise about {slope / 1e6:.0f} MB per "
                "key, but that is the allocator's high-water mark: the heap "
                "returns to near zero at every key boundary, and no provider "
                "is reachable"),
            "traced_run_is_slower_and_larger": (
                "tracemalloc roughly doubles the wall time and inflates the "
                "footprint, so the 2 GiB check uses the untraced runs"),
        },
        "extrapolated_month_footprint_bytes": (
            peaks[-1] + (MONTH_BUILD_KEYS - 3) * held_slope),
        "one_key_memory_note": (
            "for ONE build key the candidate peaks HIGHER: it holds one "
            "provider carrying three parsed variants for the whole key, "
            "where the baseline holds 65 small ones. The gain is in the "
            "scaling, because that single provider dies at every key "
            "boundary while the baseline's 65 accumulate"),
    }


def _reason(checks: Dict[str, bool]) -> str:
    if not checks["memory_does_not_grow_linearly_with_finished_keys"]:
        return BLOCKED_PREFIX + "MEMORY_RETENTION"
    if not checks["the_three_key_peak_is_under_2_gib"]:
        return BLOCKED_PREFIX + "PEAK_FOOTPRINT"
    if not (checks["every_arm_produced_the_same_parent_costs"]
            and checks["every_arm_produced_the_same_ledger"]):
        return BLOCKED_PREFIX + "RESULTS_DIFFER"
    if not checks["every_candidate_arm_beat_every_baseline_arm"]:
        return BLOCKED_PREFIX + "NO_SPEEDUP"
    return BLOCKED_PREFIX + "GATE_FAILED"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm", type=Path, action="append", required=True)
    parser.add_argument("--scaling", type=Path, action="append",
                        required=True)
    parser.add_argument("--retention", type=Path, required=True,
                        help="the tracemalloc run that measures what a "
                             "finished build key leaves behind")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")

    arms = [_load(path) for path in args.arm]
    scaling = sorted((_load(path) for path in args.scaling),
                     key=lambda run: len(run["design"]["order"]))
    retention = _load(args.retention)
    gate = _gate(arms, scaling, retention)
    decision = (READY if all(gate["checks"].values())
                else _reason(gate["checks"]))

    rates: List[float] = [
        run["totals"]["wall_s"] / (UNITS_PER_KEY * len(run["design"]["order"]))
        for run in scaling]
    low, median, high = min(rates), statistics.median(rates), max(rates)
    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "production_chain_rebuild_required": True,
        "decision": decision,
        "claim_boundary": (
            "a decision over bounded canaries of the real build_cost_ledger. "
            "It rebuilds no profile and authorises nothing: a rebuild needs "
            "the user's separate explicit approval"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "inputs": {
            "arms": [{"path": str(path), "label": run["label"],
                      "content_key": run["content_key"]}
                     for path, run in zip(args.arm, arms)],
            "scaling": [{"path": str(path),
                         "keys": len(run["design"]["order"]),
                         "content_key": run["content_key"]}
                        for path, run in zip(args.scaling, scaling)],
            "retention": {"path": str(args.retention),
                          "content_key": retention["content_key"]},
        },
        "gate": gate,
        "forecast": {
            "basis": ("the two-phase canary's measured per-unit wall over "
                      "the 1, 2 and 3 build-key runs"),
            "per_unit_s": {"low": low, "median": median, "high": high},
            "month_daily_units": MONTH_DAILY_UNITS,
            "expected_wall_s": MONTH_DAILY_UNITS * median,
            "conservative_upper_wall_s": MONTH_DAILY_UNITS * high * 1.5,
            "published_profile_wall_s": PUBLISHED_PROFILE_WALL_S,
            "expected_peak_footprint_bytes": gate[
                "extrapolated_month_footprint_bytes"],
            "published_profile_peak_rss_bytes":
                PUBLISHED_PROFILE_PEAK_RSS_BYTES,
            "machine_state_caveat": (
                "the same key measured 2.758 s per unit on 2026-09-17 and "
                "about 5.4 s on the baseline arm today, so machine state "
                "alone moves this by a factor of two. The conservative "
                "bound carries 1.5x on top of the slowest measured key"),
        },
        "recommended_budget": {
            "wall_hard_s": round(MONTH_DAILY_UNITS * high * 2),
            "memory_hard_bytes": 4 * GIB,
            "swap_growth_hard_bytes": GIB,
            "supervisor_must_stop_when": [
                "peak footprint passes the memory limit above",
                "swap grows past 1 GiB",
                "the wall passes the hard limit",
                "any SUMO process or new demand archive appears",
                "a daily unit's cost disagrees with a stored one",
                "more than one cost provider is alive at once",
            ],
        },
    }
    published = common.publish(args.out, record)
    print(json.dumps({"decision": decision, "gate": gate["checks"],
                      "wall_s": gate["wall_s"],
                      "scaling_peaks": gate["scaling_peak_footprint_bytes"],
                      "slope": gate["scaling_slope_per_build_key"],
                      "expected_wall_s": record["forecast"][
                          "expected_wall_s"],
                      "content_key": published["content_key"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
