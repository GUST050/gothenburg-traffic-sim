#!/usr/bin/env python3
"""How much memory does the profile's PREPARE phase need, per parent?

The approved 4 GiB budget came from `wci_two_phase_decision_v1`, whose peaks
were measured by `wci_two_phase_canary_v1` — and that canary calls
`build_cost_ledger` directly. It never builds the product arm and never runs
`runner.prepare` over the month's 1,690 parents. The real profile does that
first, and the supervised rebuild on 2026-09-18 reached 4.13 GiB inside it
without pricing a single daily unit (the daily-cost cache was still empty).

So this measures the phase the forecast skipped, on bounded prefixes of the
same canonical parent order, staying well under the budget. It prices nothing
and builds no demand: it stops right after `prepare` returns.

Diagnostic (`release_evidence: false`). Append-only.
"""
from __future__ import annotations

import argparse
import gc
import json
import shutil
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_prepare_memory_scaling_v1"


def _measure(spec: Any, parents: Sequence[Any], *, runs_root: Path,
             manifest: Dict[str, Any], work_root: Path,
             limit_bytes: int) -> Dict[str, Any]:
    """Prepare one bounded prefix of parents and report what it costs."""
    from tools import product_arm

    for name in ("release", "daily-cost-cache", "daily-results"):
        target = work_root / name
        if target.exists():
            shutil.rmtree(target)

    gc.collect()
    tracemalloc.start()
    before = common.process_memory()
    started = time.time()

    runner, _screen, source = product_arm.build_arm(
        spec, cost_ordered=True, runs_root=runs_root,
        release_root=work_root / "release",
        daily_cost_cache=work_root / "daily-cost-cache",
        daily_results_cache_root=work_root / "daily-results",
        study_provenance_key="prepare-memory-scaling",
        objective_method="closure_cost_v1", seed_workers=1, daily_workers=1,
        max_active_sumo_slots=1, qualified_demand_manifest=manifest)

    built = time.time()
    stopped = None
    try:
        runner.prepare(list(parents))
    except MemoryError as exc:
        stopped = f"MemoryError: {exc}"
    prepared = time.time()

    after = common.process_memory()
    heap_current, heap_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    population = {}
    for attribute in ("_units", "_parents"):
        value = getattr(runner, attribute, None)
        if value is not None:
            population[attribute.strip("_")] = len(value)
    build_keys = None
    resolver = getattr(source, "_resolver", None) or getattr(
        runner, "daily_runner", None)
    keys = getattr(resolver, "_schedule_build_keys", None)
    if isinstance(keys, dict):
        build_keys = len(set(keys.values()))

    record = {
        "parents": len(parents),
        "stopped": stopped,
        "wall_build_arm_s": built - started,
        "wall_prepare_s": prepared - built,
        "population": population,
        "distinct_build_keys": build_keys,
        "footprint_before_bytes": before["phys_footprint_bytes"],
        "footprint_after_bytes": after["phys_footprint_bytes"],
        "footprint_growth_bytes": (after["phys_footprint_bytes"]
                                   - before["phys_footprint_bytes"]),
        "lifetime_max_footprint_bytes": after[
            "lifetime_max_phys_footprint_bytes"],
        "python_heap_held_bytes": heap_current,
        "python_heap_peak_bytes": heap_peak,
        "over_limit": after["phys_footprint_bytes"] > limit_bytes,
    }
    del runner, source, resolver
    gc.collect()
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--qualified-demand-manifest", type=Path,
                        required=True)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--parents", type=int, action="append", required=True)
    parser.add_argument("--limit-bytes", type=int, default=3 * 1024 ** 3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")

    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.core.closure_calendar import iter_closure_schedules

    spec = ClosureSearchSpec.from_dict(
        json.loads(args.spec.read_text(encoding="utf-8")))
    all_parents = tuple(iter_closure_schedules(spec))
    manifest = json.loads(
        args.qualified_demand_manifest.read_text(encoding="utf-8"))

    runs: List[Dict[str, Any]] = []
    for count in sorted(args.parents):
        if count > len(all_parents):
            raise SystemExit(f"{count} parents requested, spec has "
                             f"{len(all_parents)}")
        runs.append(_measure(
            spec, all_parents[:count], runs_root=args.runs_root,
            manifest=manifest, work_root=args.work_root / f"n{count}",
            limit_bytes=args.limit_bytes))
        print(json.dumps(runs[-1], sort_keys=True), flush=True)

    # Cost per parent across the measured prefixes, then extrapolated to the
    # month. A prefix touches fewer archives than the month, so this is a
    # LOWER bound on the full prepare, not a prediction of it.
    slope = None
    projected = None
    if len(runs) >= 2:
        first, last = runs[0], runs[-1]
        span = last["parents"] - first["parents"]
        if span:
            slope = ((last["footprint_after_bytes"]
                      - first["footprint_after_bytes"]) / span)
            projected = (last["footprint_after_bytes"]
                         + slope * (len(all_parents) - last["parents"]))

    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "production_chain_rebuild_required": True,
        "claim_boundary": (
            "the memory cost of `runner.prepare` alone, on bounded prefixes "
            "of the month's parents. It prices no daily unit, builds no "
            "demand and authorises no budget change"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "inputs": {
            "spec": str(args.spec),
            "search_id": spec.search_id,
            "search_content_key": spec.content_key,
            "month_parents": len(all_parents),
            "qualified_demand_manifest": str(args.qualified_demand_manifest),
            "manifest_content_key": manifest.get("content_key"),
            "runs_root": str(args.runs_root),
        },
        "runs": runs,
        "footprint_bytes_per_parent": slope,
        "projected_full_prepare_footprint_bytes": projected,
        "projection_caveat": (
            "a prefix of the parents touches fewer of the 30 archives than "
            "the whole month, so the per-parent slope understates the full "
            "prepare and this projection is a lower bound"),
    }
    published = common.publish(args.out, record)
    print(json.dumps({
        "runs": [{"parents": run["parents"],
                  "footprint_mb": round(run["footprint_after_bytes"] / 1e6, 1),
                  "prepare_s": round(run["wall_prepare_s"], 1),
                  "build_keys": run["distinct_build_keys"]}
                 for run in runs],
        "bytes_per_parent": slope,
        "projected_full_prepare_bytes": projected,
        "content_key": published["content_key"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
