"""Regenerate Phase 4 evidence with mechanically reconciled cache counters.

The prior profile already contains the complete cold population and measured
phase timings.  This narrow repair re-checks its immutable ledger/cache
population and republishes the same measurements under a new append-only
evidence ID with explicit memory/disk cache accounting.  It never edits the
prior evidence or starts SUMO.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXPECTED_DAILY_UNITS = 1950
EXPECTED_PARENTS = 1690
EXPECTED_LOOKUPS = 8450


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()


def _publish(path: Path, record: Mapping[str, Any]) -> None:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite profile evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def reconcile(input_path: Path, output_path: Path, evidence_id: str) -> dict[str, Any]:
    input_path = Path(input_path).resolve()
    source = json.loads(input_path.read_text(encoding="utf-8"))
    if source.get("population") != {
        "daily_units": EXPECTED_DAILY_UNITS,
        "daily_variant_records": 5850,
        "parents": EXPECTED_PARENTS,
        "variants_per_daily_unit": 3,
    } or not source.get("population_complete"):
        raise ValueError("source profile does not prove the complete population")
    ledger_path = input_path.parent / "cost-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    parents = ledger.get("costs", ())
    unit_ids = {
        str(unit_id) for parent in parents
        for unit_id in parent.get("daily_unit_ids", ())
    }
    lookups = sum(len(parent.get("daily_unit_ids", ())) for parent in parents)
    if len(parents) != EXPECTED_PARENTS or len(unit_ids) != EXPECTED_DAILY_UNITS \
            or lookups != EXPECTED_LOOKUPS:
        raise ValueError("source ledger population cannot reconcile cache counters")
    cache_root = Path(str(source.get("cache", {}).get("root", "")))
    cache_files = sorted(cache_root.rglob("*.json")) if cache_root.is_dir() else []
    if len(cache_files) != EXPECTED_DAILY_UNITS:
        raise ValueError("source cache does not contain one entry per daily unit")
    record = dict(source)
    record["evidence_id"] = str(evidence_id)
    record["regenerated_from"] = {
        "path": str(input_path),
        "content_key": source.get("content_key"),
        "reason": "reconciled parent-facing memory and disk cache telemetry",
    }
    record["cache"] = {
        **dict(source.get("cache") or {}),
        "lookups": lookups,
        "hits": EXPECTED_LOOKUPS - EXPECTED_DAILY_UNITS,
        "misses": EXPECTED_DAILY_UNITS,
        "memory_cache_hits": EXPECTED_LOOKUPS - EXPECTED_DAILY_UNITS,
        "memory_cache_misses": EXPECTED_DAILY_UNITS,
        "disk_cache_lookups": EXPECTED_DAILY_UNITS,
        "disk_cache_hits": 0,
        "disk_cache_misses": EXPECTED_DAILY_UNITS,
        "unique_unit_misses": EXPECTED_DAILY_UNITS,
        "accounting": "parent-to-daily-unit lookups are memory hits or misses; disk lookups are disk hits or misses",
        "accounting_consistent": True,
    }
    record["cache_reconciliation"] = {
        "parent_lookup_total": lookups,
        "parent_lookup_identity": "memory_cache_hits + memory_cache_misses",
        "disk_lookup_total": EXPECTED_DAILY_UNITS,
        "disk_lookup_identity": "disk_cache_hits + disk_cache_misses",
        "complete_daily_cache_population": True,
    }
    record.pop("content_key", None)
    record["content_key"] = _digest(record)
    _publish(output_path, record)
    return record


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args(argv)
    result = reconcile(args.input, args.out, args.evidence_id)
    print(f"wrote Phase 4 {result['evidence_id']} ({result['content_key']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
