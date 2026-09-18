#!/usr/bin/env python3
"""Diagnostic A/B/B/A for parse reuse in the independent daily-cost oracle.

Both arms read ONE frozen workload contract
(``wci_daily_cache_workload_v1``) and price its 65 canonical schedules
through the production oracle path — ``ArchiveDisruptionProvider.disruption``
with a ``DailyCostCache`` — into a fresh cache root, in its own process.
Baseline and candidate alternate A/B/B/A so a machine that drifts one way
through the run cannot be mistaken for a code change.

This is a separate benchmark path, not a modified production chain. It
neither invokes nor alters any production entry point, and it skips no
production gate: the month profile, census and registration are simply not
consulted here, because they bind the bytes of the costing sources an arm
changes and therefore cannot describe both arms at once. Everything a cost
actually depends on is re-proved from disk against the contract instead.

The result can justify rebuilding the production chain. It can never stand
in for that rebuild: ``adoption_eligible`` is false by construction.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_daily_cache_workload_v1 as workload  # noqa: E402
import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_daily_cache_parse_ab_v1"
ACCEPTED = "DIAGNOSTICALLY_ACCEPTED_PROFILE_REBUILD_REQUIRED"
REJECTED = "REJECTED"
#: Sources whose bytes differ between the arms on purpose.
ARM_SOURCES = (
    "run_scenario.py",
    "tools/build_daily_cost_cache.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
)


def _swap_used_bytes() -> Optional[int]:
    try:
        text = subprocess.run(["/usr/sbin/sysctl", "-n", "vm.swapusage"],
                              capture_output=True, text=True,
                              check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"used\s*=\s*([0-9.]+)M", text)
    return int(float(match.group(1)) * 1024 * 1024) if match else None


def _children() -> List[str]:
    """Live descendants of this process, excluding the listing itself."""
    try:
        text = subprocess.run(["/bin/ps", "-eo", "pid=,ppid=,comm="],
                              capture_output=True, text=True,
                              check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    mine = os.getpid()
    found = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        _pid, ppid, comm = parts
        if ppid.strip() == str(mine) and Path(comm.strip()).name not in (
                "ps", "sysctl"):
            found.append(comm.strip())
    return found


def _instrument(undo: List[Any]) -> Dict[str, Any]:
    """Count XML parses, bytes read and real cost computations."""
    from traffic_sim.simulation import deterministic_disruption as dd
    from traffic_sim.simulation import disruption

    counters: Dict[str, Any] = {"parses": [], "parsed_bytes": 0,
                                "cost_computations": []}
    parse_production = disruption.parse_route_vehicles
    compute_production = dd.ArchiveDisruptionProvider._compute

    def parse_spy(path, **kwargs):
        counters["parses"].append(Path(path).name)
        try:
            counters["parsed_bytes"] += Path(path).stat().st_size
        except OSError:
            pass
        return parse_production(path, **kwargs)

    def compute_spy(self, schedule):
        counters["cost_computations"].append(schedule.schedule_id)
        return compute_production(self, schedule)

    disruption.parse_route_vehicles = parse_spy
    dd.ArchiveDisruptionProvider._compute = compute_spy
    undo.append(lambda: setattr(disruption, "parse_route_vehicles",
                                parse_production))
    undo.append(lambda: setattr(dd.ArchiveDisruptionProvider, "_compute",
                                compute_production))
    return counters


def run_arm(args: argparse.Namespace) -> int:
    """Price the contract's 65 schedules once, measuring what it cost."""
    from traffic_sim.core.contracts import ClosureSchedule, ClosureSearchSpec
    from traffic_sim.simulation.deterministic_disruption import (
        ArchiveDisruptionProvider, DailyCostCache, NetworkCostModel,
        costing_source_identity)

    contract = json.loads(args.workload.read_text(encoding="utf-8"))
    problems = workload.verify_contract(contract)
    if problems:
        raise SystemExit(f"the workload does not verify: {problems}")
    if args.cache_root.exists():
        raise SystemExit(f"an arm needs a fresh cache root: {args.cache_root}")

    spec = ClosureSearchSpec.from_dict(contract["spec"])
    schedules = [(item["unit_id"], ClosureSchedule.from_dict(item["schedule"]))
                 for item in contract["schedules"]]
    archive = Path(contract["archive"]["path"])

    undo: List[Any] = []
    counters = _instrument(undo)
    swap_before = _swap_used_bytes()
    before = resource.getrusage(resource.RUSAGE_SELF)
    started = time.perf_counter()
    network = NetworkCostModel()
    cache = DailyCostCache(args.cache_root)
    provider = ArchiveDisruptionProvider(spec, archive=archive,
                                         network=network, cache=cache)
    computed: Dict[str, Any] = {}
    for unit_id, schedule in schedules:
        records = provider.disruption(schedule)
        computed[unit_id] = {
            "cache_key": cache.key(provider.cache_identity(schedule)),
            "digest": common.digest([dict(item) for item in records]),
        }
    wall = time.perf_counter() - started
    after = resource.getrusage(resource.RUSAGE_SELF)
    swap_after = _swap_used_bytes()
    for restore in undo:
        restore()

    # Read every record back through a fresh cache, exactly as the builder
    # does before it publishes, so a stored number that differs from the
    # computed one can never be reported as a result.
    fresh = DailyCostCache(args.cache_root)
    mismatches = []
    for unit_id, schedule in schedules:
        stored = fresh.load(provider.cache_identity(schedule))
        if stored is None or common.digest(
                [dict(item) for item in stored]) != computed[unit_id][
                    "digest"]:
            mismatches.append(unit_id)
    identity = dict(provider.identity())
    without_sources = {key: value for key, value in identity.items()
                       if key != "costing_sources"}

    record = {
        "schema": f"{SCHEMA}_arm",
        "diagnostic_only": True,
        "release_evidence": False,
        "arm": args.arm,
        "position": args.position,
        "label": args.label,
        "workload": {"path": str(args.workload),
                     "sha256": common.file_sha256(args.workload),
                     "content_key": contract["content_key"],
                     "build_key": contract["build_key"],
                     "daily_units": contract["daily_units"],
                     "schedule_order_digest": contract[
                         "schedule_order_digest"]},
        "arm_identity": {
            "git": common.git_state(),
            "arm_sources": {name: common.file_sha256(ROOT / name)
                            for name in ARM_SOURCES},
            "costing_sources": dict(costing_source_identity()),
        },
        "runtime": common.runtime_manifest(),
        "daily_units": len(computed),
        "mismatches": mismatches,
        "timing": {"wall_s": wall,
                   "user_s": after.ru_utime - before.ru_utime,
                   "sys_s": after.ru_stime - before.ru_stime},
        "memory": {"max_rss_bytes": int(after.ru_maxrss),
                   "lifetime_max_phys_footprint_bytes": common.process_memory(
                   )["lifetime_max_phys_footprint_bytes"]},
        "swap": {"before_bytes": swap_before, "after_bytes": swap_after,
                 "growth_bytes": (None if swap_before is None
                                  or swap_after is None
                                  else swap_after - swap_before)},
        "work": {
            "xml_parses": len(counters["parses"]),
            "xml_parses_by_file": {
                name: counters["parses"].count(name)
                for name in sorted(set(counters["parses"]))},
            "xml_bytes_read": counters["parsed_bytes"],
            "cost_computations": len(counters["cost_computations"]),
            "distinct_cost_computations": len(
                set(counters["cost_computations"])),
        },
        "outputs": {
            "units": computed,
            "result_digest": common.digest(
                {key: computed[key]["digest"] for key in sorted(computed)}),
            "cache_key_digest": common.digest(
                {key: computed[key]["cache_key"]
                 for key in sorted(computed)}),
            "provider_identity_digest": common.digest(identity),
            "provider_identity_without_costing_sources_digest":
                common.digest(without_sources),
        },
        "surviving_children": _children(),
        "cache_root": str(args.cache_root),
    }
    record["content_key"] = common.digest(
        {key: value for key, value in record.items() if key != "content_key"})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({"arm": args.arm, "position": args.position,
                      "wall_s": wall,
                      "xml_parses": record["work"]["xml_parses"],
                      "xml_bytes_read": record["work"]["xml_bytes_read"],
                      "cost_computations": record["work"][
                          "cost_computations"],
                      "max_rss_bytes": record["memory"]["max_rss_bytes"],
                      "mismatches": mismatches,
                      "result_digest": record["outputs"]["result_digest"]},
                     indent=1))
    return 0


def _verdict(arms: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Accept only on identical results and an unambiguous improvement."""
    baseline = [item for item in arms if item["arm"] == "A"]
    candidate = [item for item in arms if item["arm"] == "B"]
    walls = {"A": [item["timing"]["wall_s"] for item in baseline],
             "B": [item["timing"]["wall_s"] for item in candidate]}
    checks = {
        "every_arm_read_the_same_workload_contract": len(
            {item["workload"]["content_key"] for item in arms}) == 1,
        "every_arm_produced_the_same_results": len(
            {item["outputs"]["result_digest"] for item in arms}) == 1,
        "every_arm_shared_one_provider_identity_apart_from_the_source_hashes":
            len({item["outputs"][
                "provider_identity_without_costing_sources_digest"]
                for item in arms}) == 1,
        "every_stored_record_matched_what_was_computed": all(
            not item["mismatches"] for item in arms),
        "every_arm_priced_every_unit_separately": all(
            item["work"]["cost_computations"]
            == item["work"]["distinct_cost_computations"]
            == item["daily_units"] for item in arms),
        "the_baseline_parsed_once_per_unit_and_variant": all(
            item["work"]["xml_parses"] == 3 * item["daily_units"]
            for item in baseline),
        "the_candidate_parsed_once_per_variant": all(
            item["work"]["xml_parses"] == 3 for item in candidate),
        "the_two_arms_really_were_different_code": len(
            {json.dumps(item["arm_identity"]["arm_sources"], sort_keys=True)
             for item in arms}) == 2,
        "every_candidate_arm_beat_every_baseline_arm": (
            max(walls["B"]) < min(walls["A"])),
        "no_arm_grew_swap": all(
            (item["swap"]["growth_bytes"] or 0) <= 0 for item in arms),
        "no_arm_left_a_child_process": all(
            not item["surviving_children"] for item in arms),
    }
    return {
        "checks": checks,
        "verdict": ACCEPTED if all(checks.values()) else REJECTED,
        "wall_s": walls,
        "separation_s": min(walls["A"]) - max(walls["B"]),
        "speedup": statistics.median(walls["A"]) / statistics.median(
            walls["B"]),
        "cpu_s": {
            "A": [item["timing"]["user_s"] + item["timing"]["sys_s"]
                  for item in baseline],
            "B": [item["timing"]["user_s"] + item["timing"]["sys_s"]
                  for item in candidate]},
        "peak_rss_bytes": {
            "A": [item["memory"]["max_rss_bytes"] for item in baseline],
            "B": [item["memory"]["max_rss_bytes"] for item in candidate]},
        "peak_footprint_bytes": {
            "A": [item["memory"]["lifetime_max_phys_footprint_bytes"]
                  for item in baseline],
            "B": [item["memory"]["lifetime_max_phys_footprint_bytes"]
                  for item in candidate]},
        "xml_parses": {"A": [item["work"]["xml_parses"] for item in baseline],
                       "B": [item["work"]["xml_parses"]
                             for item in candidate]},
        "xml_bytes_read": {
            "A": [item["work"]["xml_bytes_read"] for item in baseline],
            "B": [item["work"]["xml_bytes_read"] for item in candidate]},
        "result_digest": sorted(
            {item["outputs"]["result_digest"] for item in arms}),
        "cache_key_digests": sorted(
            {item["outputs"]["cache_key_digest"] for item in arms}),
        "cache_keys_note": (
            "a cache key hashes the five costing sources and the candidate "
            "edits three of them, so the arms MUST differ here; that is the "
            "cache invalidating itself as designed. The costs stored under "
            "those keys are identical, which is what "
            "every_arm_produced_the_same_results states"),
    }


def combine(args: argparse.Namespace) -> int:
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")
    arms = [json.loads(Path(path).read_text(encoding="utf-8"))
            for path in args.arm_record]
    arms.sort(key=lambda item: item["position"])
    verdict = _verdict(arms)
    per_unit = [item["timing"]["wall_s"] / item["daily_units"]
                for item in arms if item["arm"] == "B"]
    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "production_chain_rebuild_required": True,
        "claim_boundary": (
            "one build key, four arms, one machine. It measures parse reuse "
            "against a frozen workload contract and nothing else. It does "
            "not adopt the candidate and does not report the month profile, "
            "census or registration as valid under the candidate's source "
            "identity; those bind the costing sources and must be rebuilt "
            "before any production use"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "design": {
            "order": [item["arm"] for item in arms],
            "why": ("A/B/B/A: a machine that drifts one way through the run "
                    "cannot make the candidate look good in both its arms"),
            "workload_content_key": arms[0]["workload"]["content_key"],
            "build_key": arms[0]["workload"]["build_key"],
            "separate_processes": True,
            "separate_fresh_cache_roots": sorted(
                {item["cache_root"] for item in arms}),
            "answers_reused_between_arms": False,
            "production_gates_changed_or_skipped": "none",
        },
        "arms": arms,
        "result": verdict,
        "candidate_rate_per_unit_s": {
            "values": per_unit,
            "median": statistics.median(per_unit),
            "low": min(per_unit),
            "high": max(per_unit),
        },
        "what_this_does_not_authorise": [
            "starting the month cache build",
            "rebuilding the month ledger profile, the census or the month "
            "registration",
            "treating any existing frozen chain as valid under the "
            "candidate's costing-source hashes",
        ],
    }
    record["output_sha256"] = common.digest(
        {"arms": [item["content_key"] for item in arms], "result": verdict})
    published = common.publish(args.out, record)
    print(json.dumps({"verdict": verdict["verdict"],
                      "checks": verdict["checks"],
                      "wall_s": verdict["wall_s"],
                      "separation_s": verdict["separation_s"],
                      "speedup": verdict["speedup"],
                      "peak_rss_bytes": verdict["peak_rss_bytes"],
                      "xml_parses": verdict["xml_parses"],
                      "content_key": published["content_key"]}, indent=1))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    arm = sub.add_parser("arm")
    arm.add_argument("--workload", type=Path, required=True)
    arm.add_argument("--cache-root", type=Path, required=True)
    arm.add_argument("--arm", choices=["A", "B"], required=True)
    arm.add_argument("--position", type=int, required=True)
    arm.add_argument("--label", required=True)
    arm.add_argument("--out", type=Path, required=True)
    arm.set_defaults(func=run_arm)
    joined = sub.add_parser("combine")
    joined.add_argument("--arm-record", type=Path, action="append",
                        required=True)
    joined.add_argument("--out", type=Path, required=True)
    joined.set_defaults(func=combine)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
