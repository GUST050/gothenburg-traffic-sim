#!/usr/bin/env python3
"""One MonthlyDemandResolverRunner.prepare() call, measured in isolation.

A/B/B/A driver for the shared-runner-context fix: this script is run
UNCHANGED from two worktrees -- one at 1407044 (arm A, no shared context;
every ArchivedDemandSumoRunner builds its own network/costing state and
retains the full parsed demand_meta.json), one at the candidate commit
(arm B, MonthlyDemandResolverRunner.prepare builds one SharedRunnerContext
and every archive runner shares it, and self.metadata is local). It never
imports the shared-context symbols directly (arm A's worktree does not have
them), so it works identically in both.

Exactly N unique daily-unit schedules are selected from the real frozen
month, in enumeration order, so that resolver.prepare() constructs exactly
the requested number of distinct build-key archive runners -- real parents
each span 5 archives, so no parent-count prefix can hit 1-3 build keys; this
selects UNIT schedules directly instead, which is what prepare() actually
consumes.

Diagnostic (``release_evidence: false``). Append-only. Each invocation is
its own fresh process by construction (the caller launches a new
interpreter per arm/repetition), so no earlier run's allocator state can
leak into this one.
"""
from __future__ import annotations

import argparse
import gc
import json
import resource
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_resolver_prepare_ab_v1"
FORBIDDEN = ("sumo", "sumo-gui", "duarouter", "netconvert", "marouter",
             "jtrrouter", "polyconvert", "od2trips", "activitygen")


def _forbidden_processes() -> List[str]:
    result = subprocess.run(["ps", "-Ao", "comm="], capture_output=True,
                            text=True, check=False)
    return sorted({
        Path(line.strip()).name for line in result.stdout.splitlines()
        if Path(line.strip()).name in FORBIDDEN})


def _archives(root: Path) -> int:
    try:
        return sum(1 for item in Path(root).iterdir()
                   if item.is_dir() and item.name.startswith("demand-"))
    except OSError:
        return -1


def _select_unit_schedules(spec: Any, resolver: Any, count: int) -> List[Any]:
    """The first `count` unique daily-unit schedules, in enumeration order."""
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from traffic_sim.simulation.independent_daily import daily_unit_records

    seen: set = set()
    out: List[Any] = []
    for parent in iter_closure_schedules(spec):
        for unit_id, _identity, build in daily_unit_records(spec, parent):
            if unit_id in seen:
                continue
            seen.add(unit_id)
            out.append(build())
            if len(out) >= count:
                return out
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--unit-count", type=int, required=True,
                        help="daily units to select; also the expected "
                             "resulting build-key count")
    parser.add_argument("--expected-build-keys", type=int, required=True)
    parser.add_argument("--label", required=True, help="e.g. A1, B2, B3, A4")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")

    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation.monthly_demand import MonthlyDemandResolverRunner

    spec = ClosureSearchSpec.from_dict(
        json.loads(args.spec.read_text(encoding="utf-8")))

    archives_before = _archives(args.runs_root)
    probe = MonthlyDemandResolverRunner(
        spec, baseline_trip_duration_p99_s=3600,
        study_provenance_key="ab-unit-select")
    schedules = _select_unit_schedules(spec, probe, args.unit_count)

    gc.collect()
    tracemalloc.start()
    before_cpu = resource.getrusage(resource.RUSAGE_SELF)
    before = common.process_memory()
    started = time.time()

    resolver = MonthlyDemandResolverRunner(
        spec, baseline_trip_duration_p99_s=3600,
        study_provenance_key="ab-resolver-prepare",
        runs_root=args.runs_root, release_root=args.work_root / "release",
        cache_root=args.work_root / "cache", include_disruption=True,
        ranking_objective_evidence="closure_cost_v1")
    resolver.prepare(schedules)
    elapsed = time.time() - started

    after = common.process_memory()
    after_cpu = resource.getrusage(resource.RUSAGE_SELF)
    heap_current, heap_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    build_keys = sorted(resolver._runners.keys())
    provenance = dict(resolver.provenance())
    shared_context = getattr(resolver, "_shared_context", None)
    runners = list(resolver._runners.values())
    shared_ids: Dict[str, Any] = {}
    for field in ("adjacency", "freeflow", "rerouter_edges", "detour"):
        values = [getattr(r, field, None) for r in runners]
        shared_ids[field] = {
            "distinct_object_ids": len({id(v) for v in values}),
            "distinct_content_digests": len({
                common.digest(dict(v) if isinstance(v, Mapping) else list(v))
                for v in values}),
        }

    archives_after = _archives(args.runs_root)
    release = dict(resolver._release or {})

    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "production_chain_rebuild_required": True,
        "claim_boundary": (
            "one MonthlyDemandResolverRunner.prepare() call over exactly "
            "the requested number of real daily units, in its own fresh "
            "process. It prices no daily unit, builds no demand and starts "
            "no SUMO simulation; context construction performs one bounded "
            "sumo --version runtime fingerprint probe"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "label": args.label,
        "inputs": {
            "spec": str(args.spec),
            "search_content_key": spec.content_key,
            "runs_root": str(args.runs_root),
            "unit_count_requested": args.unit_count,
            "expected_build_keys": args.expected_build_keys,
        },
        "wall_s": elapsed,
        "cpu_s": ((after_cpu.ru_utime + after_cpu.ru_stime)
                  - (before_cpu.ru_utime + before_cpu.ru_stime)),
        "python_heap_held_bytes": heap_current,
        "python_heap_peak_bytes": heap_peak,
        "footprint_before_bytes": before["phys_footprint_bytes"],
        "footprint_after_bytes": after["phys_footprint_bytes"],
        "lifetime_max_footprint_bytes": after[
            "lifetime_max_phys_footprint_bytes"],
        "max_rss_bytes": after["max_rss_bytes"],
        "build_keys": build_keys,
        "build_key_count": len(build_keys),
        "child_runner_count": len(runners),
        "has_shared_context": shared_context is not None,
        "shared_field_identity": shared_ids,
        # Strictly cross the production JSON boundary before using the
        # diagnostic helper (whose default=str is intentionally permissive).
        "provenance_digest": common.digest(json.loads(json.dumps(
            provenance, sort_keys=True, separators=(",", ":"),
            allow_nan=False))),
        "demand_release_content_key": release.get("content_key"),
        "candidate_provenance_digests": {
            schedule.schedule_id: common.digest(json.loads(json.dumps(
                dict(resolver.candidate_provenance(schedule)),
                sort_keys=True, separators=(",", ":"), allow_nan=False)))
            for schedule in schedules},
        "forbidden_processes": _forbidden_processes(),
        "demand_archives_before": archives_before,
        "demand_archives_after": archives_after,
    }
    published = common.publish(args.out, record)
    print(json.dumps({
        "label": args.label, "build_key_count": len(build_keys),
        "wall_s": elapsed,
        "heap_mb": round(heap_current / 1e6, 2),
        "footprint_mb": round(after["phys_footprint_bytes"] / 1e6, 2),
        "has_shared_context": shared_context is not None,
        "content_key": published["content_key"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
