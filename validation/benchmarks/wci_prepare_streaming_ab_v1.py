#!/usr/bin/env python3
"""Materialised prepare against streaming prepare, on one frozen workload.

Arm A is `runner.prepare(parents)` — the path 29e0017 profiles with, which
rebuilds the unit set and the reverse unit->parent graph in memory. Arm B is
`runner.prepare_from_ledgers(directory, parent_ids)`, which reads the
published enumeration and keeps only the forward parent->units mapping.

Both arms run against the same verified ledgers, the same archives and the
same qualified manifest, and both stop the moment `prepare` returns: nothing
here prices a daily unit, builds demand or starts SUMO.

Memory is judged on the tracemalloc heap, which is restarted per arm so each
figure is what THAT arm allocated and still holds. The physical footprint is
recorded beside it and is NOT retention evidence on its own: it is a
high-water mark, and tracing inflates it.

Diagnostic (``release_evidence: false``). Append-only.
"""
from __future__ import annotations

import argparse
import gc
import json
import resource
import shutil
import subprocess
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

SCHEMA = "wci_prepare_streaming_ab_v1"
FORBIDDEN = ("sumo", "sumo-gui", "duarouter", "netconvert", "marouter",
             "jtrrouter", "polyconvert", "od2trips", "activitygen")


def _forbidden_processes() -> List[str]:
    """Forbidden processes anywhere on the machine, by command name."""
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


def _arm(spec: Any, parents: Sequence[Any], *, arm: str, runs_root: Path,
         manifest: Dict[str, Any], ledger_root: Path,
         work_root: Path) -> Dict[str, Any]:
    """Prepare one arm and report what it cost and what it produced."""
    from tools import product_arm
    from tools import profile_monthly_cost_ledger as profiler

    if work_root.exists():
        shutil.rmtree(work_root)

    gc.collect()
    tracemalloc.start()
    before = common.process_memory()
    before_cpu = resource.getrusage(resource.RUSAGE_SELF)
    started = time.time()

    runner, _screen, _source = product_arm.build_arm(
        spec, cost_ordered=True, runs_root=runs_root,
        release_root=work_root / "release",
        daily_cost_cache=work_root / "daily-cost-cache",
        daily_results_cache_root=work_root / "daily-results",
        study_provenance_key="prepare-streaming-ab",
        objective_method="closure_cost_v1", seed_workers=1, daily_workers=1,
        max_active_sumo_slots=1, qualified_demand_manifest=manifest)
    built = time.time()

    parent_ids = [parent.schedule_id for parent in parents]
    used_materialised = False
    if arm == "A":
        runner.prepare(list(parents))
        used_materialised = True
    elif arm == "B":
        # Prove the seam is real rather than assuming it: a runner without it
        # would take the materialising branch inside the helper unnoticed.
        if not callable(getattr(runner, "prepare_from_ledgers", None)):
            raise SystemExit("arm B needs a runner with prepare_from_ledgers")
        original = runner.prepare

        def refuse(_schedules):
            raise AssertionError("arm B fell back to materialised prepare")

        runner.prepare = refuse
        try:
            profiler.prepare_runner_from_ledgers(
                runner, ledger_root, parent_ids)
        finally:
            runner.prepare = original
    else:
        raise SystemExit(f"unknown arm {arm!r}")
    prepared = time.time()

    after = common.process_memory()
    after_cpu = resource.getrusage(resource.RUSAGE_SELF)
    heap_current, heap_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    provenance = dict(runner.provenance())
    # `daily_units_for` is the public seam and yields exactly the prepared
    # unit set, so nothing here reaches into the runner's private state.
    relationships: Dict[str, List[str]] = {}
    unit_schedules: Dict[str, Any] = {}
    for parent in parents:
        ordered = runner.daily_units_for(parent)
        relationships[parent.schedule_id] = [
            unit_id for unit_id, _schedule in ordered]
        for unit_id, schedule in ordered:
            unit_schedules.setdefault(unit_id, schedule.to_dict())

    # Distinct build keys, through a public seam. `candidate_provenance`
    # returns the child runner for a unit's build key, so the number of
    # DISTINCT per-unit backend digests is the number of archive runners
    # `daily_runner.prepare` created — the quantity that decides whether this
    # memory is per-unit or per-archive.
    per_unit = provenance.get("unit_backend_digests") or {}
    record = {
        "arm": arm,
        "parents": len(parents),
        "units": len(unit_schedules),
        "distinct_build_keys": len(set(per_unit.values())),
        "wall_build_arm_s": built - started,
        "wall_prepare_s": prepared - built,
        "cpu_s": ((after_cpu.ru_utime + after_cpu.ru_stime)
                  - (before_cpu.ru_utime + before_cpu.ru_stime)),
        "python_heap_held_bytes": heap_current,
        "python_heap_peak_bytes": heap_peak,
        "footprint_before_bytes": before["phys_footprint_bytes"],
        "footprint_after_bytes": after["phys_footprint_bytes"],
        "lifetime_max_footprint_bytes": after[
            "lifetime_max_phys_footprint_bytes"],
        "max_rss_bytes": after["max_rss_bytes"],
        "used_materialised_prepare": used_materialised,
        "daily_backend_digest": provenance.get("daily_backend_digest"),
        "provenance_digest": common.digest(provenance),
        "unit_ids_digest": common.digest(sorted(unit_schedules)),
        "relationships_digest": common.digest(
            dict(sorted(relationships.items()))),
        "unit_schedules_digest": common.digest(
            {key: unit_schedules[key] for key in sorted(unit_schedules)}),
        "forbidden_processes": _forbidden_processes(),
    }
    del runner, unit_schedules, relationships, provenance
    gc.collect()
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--qualified-demand-manifest", type=Path,
                        required=True)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--ledger-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--parents", type=int, action="append", required=True)
    parser.add_argument("--arms", default="A,B")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")

    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from tools import profile_monthly_cost_ledger as profiler

    spec = ClosureSearchSpec.from_dict(
        json.loads(args.spec.read_text(encoding="utf-8")))
    manifest = json.loads(
        args.qualified_demand_manifest.read_text(encoding="utf-8"))
    ledger_manifest = profiler.prepared_ledgers(args.ledger_root, spec)
    all_parents = tuple(iter_closure_schedules(spec))
    arms = [item.strip() for item in args.arms.split(",") if item.strip()]

    archives_before = _archives(args.runs_root)
    runs: List[Dict[str, Any]] = []
    for count in sorted(args.parents):
        if count > len(all_parents):
            raise SystemExit(f"{count} parents requested, spec has "
                             f"{len(all_parents)}")
        for arm in arms:
            runs.append(_arm(
                spec, all_parents[:count], arm=arm,
                runs_root=args.runs_root, manifest=manifest,
                ledger_root=args.ledger_root,
                work_root=args.work_root / f"{arm}-n{count}"))
            print(json.dumps(runs[-1], sort_keys=True), flush=True)
    archives_after = _archives(args.runs_root)

    # Semantics: every digest an arm produces must agree at the same size.
    comparisons = []
    for count in sorted({item["parents"] for item in runs}):
        at_size = [item for item in runs if item["parents"] == count]
        if len(at_size) < 2:
            continue
        keys = ("units", "unit_ids_digest", "relationships_digest",
                "unit_schedules_digest", "provenance_digest",
                "daily_backend_digest")
        comparisons.append({
            "parents": count,
            "arms": [item["arm"] for item in at_size],
            **{f"{key}_identical": len({item[key] for item in at_size}) == 1
               for key in keys},
        })

    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "production_chain_rebuild_required": True,
        "claim_boundary": (
            "the cost and the output of PREPARE alone, materialised against "
            "streaming, on one frozen workload. It prices no daily unit, "
            "builds no demand, starts no SUMO and authorises nothing"),
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
            "ledger_root": str(args.ledger_root),
            "ledger_manifest_content_key": ledger_manifest.key,
            "ledger_parent_count": ledger_manifest.parent_count,
            "ledger_unique_unit_count": ledger_manifest.unique_unit_count,
        },
        "runs": runs,
        "semantics": comparisons,
        "process_checks": {
            "forbidden_processes_seen": sorted({
                name for item in runs for name in item["forbidden_processes"]
            }),
            "demand_archives_before": archives_before,
            "demand_archives_after": archives_after,
            "arms_that_used_materialised_prepare": sorted({
                item["arm"] for item in runs
                if item["used_materialised_prepare"]}),
        },
        "instrument_note": (
            "python_heap_* is tracemalloc, restarted per arm, so each figure "
            "is what that arm allocated and still holds. The footprint is a "
            "high-water mark and tracing inflates it; it is recorded for "
            "completeness, not as retention evidence"),
    }
    published = common.publish(args.out, record)
    print(json.dumps({
        "runs": [{"arm": item["arm"], "parents": item["parents"],
                  "units": item["units"],
                  "heap_mb": round(item["python_heap_held_bytes"] / 1e6, 1),
                  "prepare_s": round(item["wall_prepare_s"], 1)}
                 for item in runs],
        "semantics": comparisons,
        "content_key": published["content_key"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
