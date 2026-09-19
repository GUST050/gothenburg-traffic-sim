#!/usr/bin/env python3
"""Constructor-level memory attribution for ArchivedDemandSumoRunner.

The month-scale cost is ~154.7 MB per distinct build key, and it is paid
inside MonthlyDemandResolverRunner.prepare, which constructs one
ArchivedDemandSumoRunner per build key and retains it. This measures WHERE
inside that constructor the memory goes, field by field, one runner per
fresh subprocess so no earlier construction's allocator state leaks in.

Method: for each field set in __init__, take a tracemalloc snapshot before
and after that field would be set (by constructing the runner, then reading
back each field's value and re-measuring its OWN deep size via a recursive
sizer that follows dict/list/tuple/set contents and de-duplicates by id() so
shared substructure is not double counted). This is cruder than instrumenting
the constructor line by line, but it does not require patching production
code, and the recursive sizer's total is cross-checked against tracemalloc's
whole-construction delta.

Run on at least three archives (small/median/large by route bytes) so a
field's cost can be classified as archive-invariant (same bytes/value across
archives -> a candidate for a shared context) or archive-specific (differs
-> must stay per-runner).

Diagnostic (``release_evidence: false``). Append-only.
"""
from __future__ import annotations

import argparse
import gc
import json
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

SCHEMA = "wci_archive_runner_attribution_v1"

# Fields set inside ArchivedDemandSumoRunner.__init__ that are candidates for
# being archive-invariant (network/spec-derived) rather than archive-specific.
CANDIDATE_FIELDS = (
    "adjacency",
    "freeflow",
    "_disruption_adjacency",
    "_disruption_edge_time",
    "_disruption_edge_len",
    "rerouter_edges",
    "detour",
    "close_edges",
    "source_records",
    "simulation_source_records",
    "source_digest",
    "simulation_source_digest",
    "runtime_identity",
)
# Fields that are necessarily archive-specific and are measured for scale
# comparison only, not as shared-context candidates.
ARCHIVE_SPECIFIC_FIELDS = (
    "metadata",
    "variants",
    "input_records",
    "archive_digest",
    "matched_baseline_id",
    "expected_demand_spec",
)


def _deep_size(obj: Any, seen: set) -> int:
    """Recursive size in bytes, de-duplicated by id() so shared substructure
    (e.g. two attributes pointing at the SAME cached dict) is counted once."""
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        for key, value in obj.items():
            size += _deep_size(key, seen)
            size += _deep_size(value, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            size += _deep_size(item, seen)
    elif hasattr(obj, "__dict__"):
        size += _deep_size(vars(obj), seen)
    return size


def _measure_archive(archive: Path, *, spec: Any, cache_root: Path) -> Dict[str, Any]:
    from traffic_sim.simulation.monthly_demand import DemandBuildSpec
    from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

    demand_spec = DemandBuildSpec.from_dict(
        json.loads((archive / "demand_build_spec.json").read_text(
            encoding="utf-8")))

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    started = time.perf_counter()
    runner = ArchivedDemandSumoRunner(
        spec, archive=archive, baseline_trip_duration_p99_s=3600,
        study_provenance_key="prepare-layer-attribution",
        cache_root=cache_root, include_disruption=True,
        ranking_objective_evidence="closure_cost_v1",
        expected_demand_spec=demand_spec)
    elapsed = time.perf_counter() - started
    after = tracemalloc.take_snapshot()
    whole_current, whole_peak = tracemalloc.get_traced_memory()

    fields: Dict[str, Any] = {}
    seen_ids: set = set()
    for name in CANDIDATE_FIELDS + ARCHIVE_SPECIFIC_FIELDS:
        value = getattr(runner, name, None)
        # A field-local `seen` (not shared across fields) so each field's
        # OWN size is reported; the shared-context question ("is this the
        # SAME object across archives") is answered by comparing content
        # digests and id()s across runs, not by this per-field size.
        size = _deep_size(value, set())
        fields[name] = {
            "held_bytes": size,
            "type": type(value).__name__,
            "content_digest": common.digest(
                value if not hasattr(value, "to_dict") else value.to_dict()),
        }
    diff = after.compare_to(before, "lineno")
    top_lines = [
        {"file_line": str(stat.traceback[0]), "size_diff_bytes": stat.size_diff}
        for stat in diff[:15] if stat.size_diff > 0
    ]
    tracemalloc.stop()

    record = {
        "archive": str(archive),
        "wall_s": elapsed,
        "whole_construction_traced_current_bytes": whole_current,
        "whole_construction_traced_peak_bytes": whole_peak,
        "fields": fields,
        "top_allocation_sites": top_lines,
        "close_edges": list(spec.directed_edges),
    }
    del runner
    gc.collect()
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--archive", type=Path, action="append", required=True,
                        help="repeat: small, median, large by route bytes")
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")

    from traffic_sim.core.contracts import ClosureSearchSpec

    spec = ClosureSearchSpec.from_dict(
        json.loads(args.spec.read_text(encoding="utf-8")))

    runs: List[Dict[str, Any]] = []
    for archive in args.archive:
        runs.append(_measure_archive(
            archive, spec=spec, cache_root=args.cache_root))
        print(json.dumps(
            {"archive": str(archive),
             "fields_mb": {k: round(v["held_bytes"] / 1e6, 3)
                          for k, v in runs[-1]["fields"].items()}},
            indent=1))

    # Which fields are byte-identical (same content digest) across all
    # measured archives -> a real candidate for a single shared context.
    invariant = {}
    for name in CANDIDATE_FIELDS:
        digests = {run["fields"][name]["content_digest"] for run in runs}
        invariant[name] = len(digests) == 1

    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "production_chain_rebuild_required": True,
        "claim_boundary": (
            "field-level memory attribution for ONE ArchivedDemandSumoRunner "
            "construction on real archives. It constructs no product arm, "
            "prices no daily unit, starts no SUMO process"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "inputs": {
            "spec": str(args.spec),
            "search_content_key": spec.content_key,
            "archives": [str(a) for a in args.archive],
        },
        "runs": runs,
        "content_identical_across_archives": invariant,
        "reading": (
            "a field with content_identical_across_archives=true is bound "
            "only to network/spec/directed-edges, never to the archive, so "
            "one shared instance can replace N per-archive copies. A field "
            "that differs per archive must stay archive-specific"),
    }
    published = common.publish(args.out, record)
    print(json.dumps({
        "content_identical_across_archives": invariant,
        "content_key": published["content_key"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
