import hashlib
import json
from pathlib import Path

from tools import run_monthly_v2_benchmark as benchmark


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "validation" / "monthly_search_v2_benchmark_plan_v1.json"
RECORD = ROOT / "validation" / "monthly_search_v2_benchmark_v1.json"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_benchmark_plan_binds_immutable_inputs_and_isolated_outputs():
    plan = _read(PLAN)
    paths = benchmark.validate_plan(plan)

    assert paths["archive"] == (
        ROOT / "runs" / "releases" / "golden-2025-09-16-v1" / "normal"
    ).resolve()
    assert paths["search_root"] != paths["baseline_cache"]
    assert plan["safety"] == {
        "execution_mode": "cold",
        "seed_workers": 1,
        "shared_demand_workspace_used": False,
        "annual_warming_inputs_mutated": False,
        "release_evidence": False,
        "heldout_evidence": False,
    }


def test_benchmark_record_matches_persisted_result_and_keeps_gate_closed():
    record = _read(RECORD)
    result_path = (
        ROOT
        / "runs"
        / "closure-objective-v2-benchmark-v1"
        / "search"
        / "golden-monthly-search-2025-09-16-v6"
        / "artifacts"
        / "result.json"
    )
    result = _read(result_path)

    assert record["status"] == "passing_diagnostic"
    assert record["provenance"]["result_sha256"] == _sha256(result_path)
    assert result["pilot_selection"]["method"] == (
        "deterministic_closure_cost_pilot_v1"
    )
    assert result["robust_decision"]["method"] == (
        "deterministic_worst_variant_closure_cost_v1"
    )
    assert result["robust_decision"]["winner_id"] == (
        record["result"]["winner_id"]
    )
    assert record["claim_boundary"]["global_best_claim_allowed"] is False
    assert record["claim_boundary"]["heldout_gate_record"] is None
