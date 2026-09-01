"""Freeze the outcome-blind contract for the sub-hour comparison.

This tool intentionally has no import or filesystem path for benchmark
outcomes.  It records the question, immutable execution differences and
resource gates before a bounded run can produce an outcome.  Existing v5
registrations remain historical evidence and are never read or rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "validation" / (
    "subhour_monthly_search_preregistration_v1.json")

SCHEMA = "subhour_monthly_search_preregistration_v1"
SEMANTIC_SOURCES = (
    "traffic_sim/simulation/cost_ordered_search.py",
    "traffic_sim/simulation/cost_ordered_execution.py",
    "traffic_sim/simulation/monthly_search.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/monthly_sumo.py",
    "traffic_sim/simulation/finalist_decision.py",
    "traffic_sim/simulation/pilot_selection.py",
    "tools/product_arm.py",
    "tools/cost_ordered_benchmark.py",
    "tools/cost_ordered_benchmark_suite.py",
    "tools/freeze_subhour_preregistration.py",
    "tools/profile_monthly_cost_ledger.py",
)


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def content_key(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_registration(*, root: Path = ROOT) -> dict[str, Any]:
    """Build a new registration without consulting any outcome artifact."""
    root = Path(root).resolve()
    sources = {}
    for relative in SEMANTIC_SOURCES:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"required source is missing: {relative}")
        sources[relative] = _sha256(path)
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": "subhour_monthly_search_preregistration",
        "registered_at": time.strftime("%Y-%m-%d"),
        "reads_outcomes": False,
        "selection_reads_outcomes": False,
        "activation": {
            "release_evidence": False,
            "activates_policy_v3": False,
            "opens_global_best": False,
            "permits_ui_claim": False,
        },
        "search_contract": {
            "dates": 30,
            "windows_per_date": 65,
            "consecutive_days": 5,
            "same_window_for_all_days": True,
            "objective": "closure_cost_v1",
            "variants": ["q10", "q50", "q90"],
            "aggregation": "robust_worst_variant_exact_v1",
            "tie_band_vehicle_hours": "policy_bound",
            "routing": "original_origin_to_destination_fastest_legal_path_without_closed_edges",
            "evidence": [
                "matched_baseline",
                "closure_integrity",
                "health",
                "recovery",
                "provenance",
            ],
        },
        "arms": {
            "cost_ordered": {"disable_early_stop": False},
            "ordered_exhaustive": {"disable_early_stop": True},
            "only_allowed_difference": "disable_early_stop",
            "shared_kernel": "run_cost_ordered_execution",
        },
        "terminal_results": [
            "READY",
            "INCONCLUSIVE_TIMEOUT",
            "INCONCLUSIVE_CAPACITY",
            "INCONCLUSIVE_BUDGET_EXHAUSTED",
        ],
        "timeout_protocol": {
            "unresolved_timeout_is_terminal": True,
            "start_no_later_candidate": True,
            "fallback_to_exhaustive": False,
            "publish_winner": False,
        },
        "budget": {
            "active_seconds": 55 * 60,
            "publication_reserve_seconds": 5 * 60,
            "daily_workers": 1,
            "seed_workers": 1,
            "max_active_sumo_slots": 1,
        },
        "synthetic_matrix": [
            "clean_band_stop", "hard_failure_backfill", "pre_sumo_no_detour",
            "exact_boundary_tie", "over_capacity_tie",
            "secondary_tertiary_ordering", "timeout_before_viable",
            "timeout_after_viable", "cancel_resume_each_cursor",
            "terminal_restart", "corrupt_or_swapped_evidence",
            "corrupt_or_swapped_ledger", "corrupt_baseline_or_route_provenance",
            "no_viable",
        ],
        "comparison_gates": {
            "status_selected_ids_final_decision_identical": True,
            "full_ledger_field_identical": True,
            "verified_prefix_failures_health_identical": True,
            "restart_cancel_equivalent": True,
            "minimum_exact_verification_reduction": 0.30,
            "minimum_active_time_reduction": 0.30,
            "resource_regression_allowed": False,
        },
        "sources": sources,
        "claim_boundary": "preregistration_only_no_outcome_claim",
    }
    body = {key: value for key, value in record.items()
            if key not in {"content_key", "registered_at"}}
    record["content_key"] = content_key(body)
    return record


def verify_registration(record: Mapping[str, Any], *, root: Path = ROOT) -> None:
    if record.get("schema") != SCHEMA:
        raise ValueError("unsupported sub-hour preregistration schema")
    if record.get("reads_outcomes") is not False \
            or record.get("selection_reads_outcomes") is not False:
        raise ValueError("preregistration is not outcome-blind")
    body = {key: value for key, value in record.items()
            if key not in {"content_key", "registered_at"}}
    if record.get("content_key") != content_key(body):
        raise ValueError("preregistration content key does not match body")
    for relative, expected in (record.get("sources") or {}).items():
        path = Path(root).resolve() / relative
        if not path.is_file() or _sha256(path) != expected:
            raise ValueError(f"preregistration source drift: {relative}")


def write_registration(path: Path, record: Mapping[str, Any]) -> None:
    path = Path(path)
    if path.exists():
        raise FileExistsError(
            f"refusing to overwrite frozen preregistration: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if args.verify:
        record = json.loads(args.out.read_text(encoding="utf-8"))
        verify_registration(record)
        print(f"verified {args.out} ({record['content_key']})")
    else:
        record = build_registration()
        write_registration(args.out, record)
        print(f"wrote {args.out} ({record['content_key']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
