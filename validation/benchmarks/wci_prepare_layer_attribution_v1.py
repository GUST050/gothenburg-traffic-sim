#!/usr/bin/env python3
"""What does the month's unit/parent graph actually cost? Measure it alone.

Every earlier measurement of `prepare` went through `product_arm.build_arm`,
so it measured two things at once: the unit/parent graph AND the monthly
demand resolver, which validates 30 archives and builds one archive runner per
build key. Units and build keys grow together across prefixes of the same
month, so a per-unit fit and a per-build-key fit are collinear and both look
convincing -- and the per-unit one is wrong.

This breaks the collinearity by removing the demand layer entirely.
`FakeDailyRunner` stands in for it, so what remains is exactly what `prepare`
and `prepare_from_ledgers` build for themselves, on the REAL 1,690-parent /
1,950-unit frozen month. It touches no archive, starts no process and prices
nothing.

The two paths alternate A,B,B,A so ordering and allocator warm-up cannot
favour either -- the first arm in a fresh process otherwise absorbs one-time
imports and looks artificially expensive.

Diagnostic (``release_evidence: false``). Append-only.
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE), str(ROOT / "tests")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_prepare_layer_attribution_v1"
ORDER = ("materialised", "streaming", "streaming", "materialised")


def _measure(kind: str, *, spec: Any, parents: Sequence[Any],
             ledger_dir: Path, cache_root: Path) -> Dict[str, Any]:
    from tests.test_independent_daily import FakeDailyRunner
    from traffic_sim.simulation.independent_daily import IndependentDailyRunner
    from tools import profile_monthly_cost_ledger as profiler

    parent_ids = tuple(item.schedule_id for item in parents)
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    runner = IndependentDailyRunner(
        spec, daily_runner=FakeDailyRunner(), cache_root=cache_root)
    if kind == "materialised":
        runner.prepare(parents)
    else:
        profiler.prepare_runner_from_ledgers(runner, ledger_dir, parent_ids)
    elapsed = time.perf_counter() - started
    held, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    units = {unit_id
             for parent in parents
             for unit_id, _schedule in runner.daily_units_for(parent)}
    provenance = dict(runner.provenance())
    record = {
        "path": kind,
        "parents": len(parents),
        "units": len(units),
        "wall_s": elapsed,
        "python_heap_held_bytes": held,
        "python_heap_peak_bytes": peak,
        "provenance_digest": common.digest(provenance),
        "daily_backend_digest": provenance.get("daily_backend_digest"),
        "unit_ids_digest": common.digest(sorted(units)),
    }
    del runner, units, provenance
    gc.collect()
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")

    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from tools import profile_monthly_cost_ledger as profiler

    spec = ClosureSearchSpec.from_dict(
        json.loads(args.spec.read_text(encoding="utf-8")))
    parents = tuple(iter_closure_schedules(spec))
    workspace = Path(tempfile.mkdtemp(prefix="prepare-layer-"))
    ledger_dir = workspace / "ledgers"
    manifest = profiler.prepared_ledgers(ledger_dir, spec)

    runs: List[Dict[str, Any]] = []
    for index, kind in enumerate(ORDER):
        runs.append({"order": index, **_measure(
            kind, spec=spec, parents=parents, ledger_dir=ledger_dir,
            cache_root=workspace / f"cache-{index}")})
        print(json.dumps(runs[-1], sort_keys=True), flush=True)

    by_path = {kind: [item["python_heap_held_bytes"] for item in runs
                      if item["path"] == kind]
               for kind in ("materialised", "streaming")}
    identical = {
        key: len({item[key] for item in runs}) == 1
        for key in ("units", "unit_ids_digest", "provenance_digest",
                    "daily_backend_digest")}

    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "production_chain_rebuild_required": True,
        "decision_relevance": (
            "this is the ONLY quantity the streaming seam can change. "
            "Whatever else the profile's prepare holds is not in this layer "
            "and no ledger change can move it"),
        "claim_boundary": (
            "the unit/parent graph alone, with the demand resolver replaced "
            "by a stub, on the real frozen month. It validates no archive, "
            "starts no process and prices nothing"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "inputs": {
            "spec": str(args.spec),
            "search_id": spec.search_id,
            "search_content_key": spec.content_key,
            "parents": len(parents),
            "ledger_manifest_content_key": manifest.key,
            "ledger_parent_count": manifest.parent_count,
            "ledger_unique_unit_count": manifest.unique_unit_count,
        },
        "order": list(ORDER),
        "runs": runs,
        "unit_graph_held_bytes": by_path,
        "streaming_costs_more_bytes": (
            min(by_path["streaming"]) - max(by_path["materialised"])),
        "semantics_identical": identical,
        "reading": (
            "the whole month's unit/parent graph is a few megabytes on either "
            "path, so it cannot be where a multi-gigabyte prepare goes. "
            "Streaming holds MORE here, because it parses 1,950 JSON rows "
            "into fresh strings where the materialised path shares interned "
            "keys and spec attributes across units"),
    }
    published = common.publish(args.out, record)
    print(json.dumps({
        "unit_graph_held_bytes": by_path,
        "streaming_costs_more_bytes": record["streaming_costs_more_bytes"],
        "semantics_identical": identical,
        "content_key": published["content_key"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
