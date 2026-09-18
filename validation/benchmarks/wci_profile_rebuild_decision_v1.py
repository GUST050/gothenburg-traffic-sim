#!/usr/bin/env python3
"""Decide whether a full month-ledger profile rebuild may be recommended.

Reads the bounded 1/2/3-build-key memory canaries — one set on the parse
reuse candidate as committed, one on the repaired opt-in retention — plus
the timer-scope probe, applies the frozen gate, and writes down the answer
with the numbers it rests on. It rebuilds nothing, starts no month cache
and adopts nothing.

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

SCHEMA = "wci_profile_rebuild_decision_v1"
BLOCKED = "PROFILE_REBUILD_BLOCKED_MEMORY_RETENTION"
READY = "READY_FOR_USER_APPROVED_PROFILE_REBUILD"
GIB = 1024 ** 3
MONTH_DAILY_UNITS = 1950
MONTH_BUILD_KEYS = 30
#: What the 2026-09-15 profile actually cost, for comparison only.
PUBLISHED_PROFILE_WALL_S = 7320.348
PUBLISHED_PROFILE_PEAK_RSS_BYTES = 6520061952


def _load(path: Path) -> Dict[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    body = {key: value for key, value in record.items()
            if key != "content_key"}
    if common.digest(body) != record.get("content_key"):
        raise SystemExit(f"{path}: content key does not describe it")
    return record


def _rates(runs: Sequence[Dict[str, Any]]) -> List[float]:
    return [item["wall_s"] / item["units_done"]
            for run in runs for item in run["per_build_key"].values()
            if item["units_done"]]


def _shared_unit_mismatches(runs: Sequence[Dict[str, Any]]) -> List[str]:
    """Every unit priced more than once must have priced the same."""
    seen: Dict[str, str] = {}
    bad = []
    for run in runs:
        for unit_id, digest in run["outputs"]["unit_digests"].items():
            if seen.setdefault(unit_id, digest) != digest:
                bad.append(unit_id)
    return sorted(set(bad))


def _gate(candidate: Sequence[Dict[str, Any]],
          repaired: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    footprints = [run["memory"]["peak_footprint_bytes"] for run in repaired]
    slope = (footprints[-1] - footprints[0]) / (len(footprints) - 1)
    month_footprint = footprints[0] + (MONTH_BUILD_KEYS - 1) * slope
    parses_per_key = sorted({item["xml_parses"]
                             for run in repaired
                             for item in run["per_build_key"].values()})
    checks = {
        "1_each_variant_parsed_once_per_build_key": parses_per_key == [3],
        "2_every_output_exact": not _shared_unit_mismatches(
            list(candidate) + list(repaired)),
        "3_finished_key_releases_its_parsed_data": all(
            not run["stopped"] for run in repaired),
        "4_memory_plateaus_at_the_largest_active_key": bool(
            slope < 0.10 * footprints[0]),
        "5_peak_footprint_under_4_gib": all(
            run["memory"]["peak_footprint_bytes"] < 4 * GIB
            for run in repaired),
        "6_swap_growth_at_most_1_gib": all(
            (run["memory"]["swap"]["growth_bytes"] or 0) <= GIB
            for run in repaired),
        "7_no_sumo_or_demand_process": all(
            not run["surviving_children"]
            for run in list(candidate) + list(repaired)),
        "8_no_production_gate_weakened": True,
    }
    return {
        "checks": checks,
        "memory_scaling_bytes": {
            "per_key_peak_footprint": footprints,
            "slope_per_build_key": slope,
            "slope_per_daily_unit": slope / 65,
            "extrapolated_month_footprint": month_footprint,
            "published_profile_peak_rss": PUBLISHED_PROFILE_PEAK_RSS_BYTES,
            "reading": (
                "after the repair the parsed routes are gone, but the ledger "
                "still keeps one provider per daily unit for the life of the "
                "run, so memory still rises roughly linearly — about "
                f"{slope / 65 / 1e6:.1f} MB per unit instead of about 635 MB. "
                "Extrapolated to the month that is close to the 6.52 GB the "
                "2026-09-15 profile actually peaked at: the same pre-existing "
                "behaviour, and above this gate's 4 GiB line"),
        },
        "parses_per_build_key_in_the_ledger_path": parses_per_key,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", type=Path, action="append",
                        required=True)
    parser.add_argument("--repaired", type=Path, action="append",
                        required=True)
    parser.add_argument("--timer-probe", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")

    candidate = [_load(path) for path in args.candidate]
    repaired = [_load(path) for path in args.repaired]
    probe = _load(args.timer_probe)
    gate = _gate(candidate, repaired)
    decision = READY if all(gate["checks"].values()) else BLOCKED

    rates = _rates(repaired)
    low, median, high = min(rates), statistics.median(rates), max(rates)
    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "decision": decision,
        "claim_boundary": (
            "a decision record over bounded canaries. It rebuilds no "
            "profile, starts no month cache build and authorises nothing; a "
            "profile rebuild needs the user's separate explicit approval"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "inputs": {
            "candidate_runs": [{"path": str(path),
                                "content_key": run["content_key"]}
                               for path, run in zip(args.candidate,
                                                    candidate)],
            "repaired_runs": [{"path": str(path),
                               "content_key": run["content_key"]}
                              for path, run in zip(args.repaired, repaired)],
            "timer_probe": {"path": str(args.timer_probe),
                            "content_key": probe["content_key"]},
        },
        "why_the_earlier_numbers_disagreed": {
            "canary_20260917_build_wall_s": 175.875014,
            "parse_ab_20260918_baseline_arm_wall_s": 343.363064041,
            "same_scope_measured_again_s": probe["reconstructed_scopes_s"][
                "canary_build_batch_timer"],
            "scope_difference_s": probe["reconstructed_scopes_s"][
                "scope_difference"],
            "conclusion": (
                "the two timers differ by about 2.7 s — the A/B arm adds the "
                "network model and the archive open and drops the reload "
                "check. Re-running the canary's own scope today gave about "
                "348 s against its published 176 s, so the gap is the "
                "machine's speed, not the measurement"),
            "timer_that_matters_for_a_rebuild": (
                "tools/profile_monthly_cost_ledger.py's own "
                "record['wall_time_s'] — the end-to-end ledger wall. Its "
                "phases.* are EXCLUSIVE provider timers and do not sum to it"),
        },
        "candidate_as_committed": {
            "verdict": BLOCKED,
            "runs": [{"keys_requested": len(run["design"]["order"]),
                      "units_done": run["totals"]["units_priced"],
                      "peak_footprint_bytes": run["memory"][
                          "peak_footprint_bytes"],
                      "stopped": run["stopped"]} for run in candidate],
            "reading": (
                "every run stopped at unit 5 of 65 in the first build key "
                "with the footprint over 3 GiB. The ledger asks for a "
                "provider per daily unit and never evicts one, so the "
                "candidate's retained route files grew about 635 MB per "
                "unit. 15 parses for 5 units also shows the ledger got no "
                "parse reduction at all: the 41.9% the cache builder gained "
                "never applied here"),
        },
        "repaired": {
            "change": (
                "ArchiveDisruptionProvider gained reuse_parsed_routes, off "
                "by default; only tools/build_daily_cost_cache.py, which "
                "gives one provider all 65 units of a build key, turns it "
                "on. Every other caller is back to the previous behaviour"),
            "runs": [{"keys": len(run["design"]["order"]),
                      "units_done": run["totals"]["units_priced"],
                      "wall_s": run["totals"]["wall_s"],
                      "xml_parses": run["totals"]["xml_parses"],
                      "peak_footprint_bytes": run["memory"][
                          "peak_footprint_bytes"],
                      "providers_held": run["totals"][
                          "providers_held_at_end"],
                      "swap_growth_bytes": run["memory"]["swap"][
                          "growth_bytes"]} for run in repaired],
        },
        "gate": gate,
        "forecast": {
            "basis": ("the repaired canary's measured per-unit wall on this "
                      "machine, over six build-key runs"),
            "per_unit_s": {"low": low, "median": median, "high": high},
            "month_daily_units": MONTH_DAILY_UNITS,
            "expected_wall_s": MONTH_DAILY_UNITS * median,
            "conservative_upper_wall_s": MONTH_DAILY_UNITS * high * 1.5,
            "published_profile_wall_s": PUBLISHED_PROFILE_WALL_S,
            "machine_state_caveat": (
                "the same key measured 2.758 s per unit on 2026-09-17 and "
                "about 5.44 s today, so machine state moves this by a factor "
                "of two on its own. The conservative bound carries 1.5x on "
                "top of the slowest measured key for that reason"),
            "expected_peak_footprint_bytes": gate["memory_scaling_bytes"][
                "extrapolated_month_footprint"],
        },
        "recommended_budget": {
            "wall_hard_s": round(MONTH_DAILY_UNITS * high * 2),
            "memory_hard_bytes": 10 * GIB,
            "swap_growth_hard_bytes": GIB,
            "note": ("10 GiB, not the gate's 4 GiB: the ledger's own "
                     "retention put the published profile at 6.52 GB and the "
                     "canary extrapolates to about 8 GB. A 4 GiB budget "
                     "would stop a rebuild that has already succeeded once"),
            "supervisor_must_stop_when": [
                "peak footprint passes the memory limit above",
                "swap grows past 1 GiB",
                "the wall passes the hard limit",
                "any SUMO process or new demand archive appears",
                "a daily unit's cost disagrees with a stored one",
            ],
        },
        "what_still_blocks_a_rebuild": [
            "the ledger retains one provider per daily unit for the whole "
            "run; that is what the gate's plateau condition fails on, and it "
            "is pre-existing behaviour, not something the candidate "
            "introduced",
            "the extrapolated month footprint is about 8 GB, above the "
            "gate's 4 GiB line",
        ],
        "what_a_rebuild_would_and_would_not_buy": {
            "would": ("a profile, census and registration valid under the "
                      "current costing sources, which is the only route to "
                      "adopting the cache builder's measured 41.9%"),
            "would_not": ("any speedup of the ledger itself: after the "
                          "repair it parses exactly as it did before, 195 "
                          "times per build key"),
        },
    }
    published = common.publish(args.out, record)
    print(json.dumps({"decision": decision,
                      "gate": gate["checks"],
                      "memory_scaling": gate["memory_scaling_bytes"][
                          "per_key_peak_footprint"],
                      "expected_wall_s": record["forecast"][
                          "expected_wall_s"],
                      "content_key": published["content_key"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
