from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import tools.cost_ordered_benchmark as base
import tools.cost_ordered_benchmark_suite as suite


ROOT = Path(__file__).resolve().parents[1]


def test_suite_cli_can_be_executed_by_file_path():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools/cost_ordered_benchmark_suite.py"),
         "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert "--preregister" in completed.stdout


def _profile(key: str, work_date: str, edge: str, *, candidates: int = 13):
    return {
        "search_id": f"search-{key}",
        "search_content_key": key * 20,
        "spec": {"directed_edges": [edge]},
        "candidate_count": candidates,
        "unique_daily_unit_count": candidates,
        "work_dates": [work_date],
        "work_dates_with_calibrated_archive": [work_date],
        "structurally_eligible": True,
    }


def test_suite_selection_spans_dates_and_never_reuses_an_edge():
    selection = {"evaluated": [
        _profile("a", "2027-03-22", "edge-a"),
        _profile("b", "2027-07-15", "edge-a"),
        _profile("c", "2027-03-22", "edge-b"),
        _profile("d", "2027-07-15", "edge-c"),
        _profile("e", "2027-03-22", "edge-d"),
        _profile("f", "2027-07-15", "edge-e"),
        _profile("g", "2027-07-15", "edge-f", candidates=9),
    ]}
    chosen = suite.select_suite_cases(selection)
    assert len(chosen) == 4
    assert len({suite._edge(item) for item in chosen}) == 4
    assert {date for item in chosen for date in suite._dates(item)} == {
        "2027-03-22", "2027-07-15"}
    assert all(item["candidate_count"] == 13 for item in chosen)


def test_suite_selection_does_not_mutate_discovery_records():
    records = [_profile(str(index), "2027-03-22", f"edge-{index}")
               for index in range(5)]
    before = copy.deepcopy(records)
    suite.select_suite_cases({"evaluated": records})
    assert records == before


def test_run_suite_counterbalances_arm_order_across_cases(monkeypatch,
                                                            tmp_path):
    """Cross-case control complementing per-case process isolation.

    A systematic host-load difference between "runs first" and "runs
    second" must not land entirely on one arm across a whole suite, so
    `run_suite` alternates `counterbalance` case by case. This asserts the
    alternation itself without running any arm — `base.run_benchmark` is
    replaced with a recorder.
    """
    registration = {
        "output_roots": {
            "workspace_namespace": "ns",
            "daily_cost_cache": "cache.json",
        },
        "selected_cases": [
            _profile(str(index), "2027-03-22", f"edge-{index}")
            | {"case_id": f"case-{index}"}
            for index in range(4)
        ],
    }
    seen: list[bool] = []

    def fake_run_benchmark(child, *, runs_root, data_root, release_root,
                            workspace_root, fault_injection, counterbalance):
        seen.append(counterbalance)
        return {"arms": {"exhaustive": {}}, "comparison": {}}

    monkeypatch.setattr(base, "run_benchmark", fake_run_benchmark)
    suite.run_suite(
        registration, runs_root=tmp_path, data_root=tmp_path,
        release_root=tmp_path / "releases", workspace_root=tmp_path / "ws")
    assert seen == [False, True, False, True]


def test_each_case_gets_its_own_daily_cost_cache_root(monkeypatch, tmp_path):
    """A shared cache root let case 2+ silently resume case 1's real SUMO

    evidence: `_isolated_daily_results_cache_root` only clones the FIRST
    time a given destination is seen, and that destination is derived from
    `daily_cost_cache`'s parent — so every case sharing one `daily_cost_cache`
    path shared one real evidence cache too, corrupting the very
    attempt-count/wall-time numbers this benchmark measures. Each case must
    get a distinct, non-overlapping cache root.
    """
    registration = {
        "output_roots": {
            "workspace_namespace": "ns",
            "daily_cost_cache": "runs/suite-daily-costs",
        },
        "selected_cases": [
            _profile(str(index), "2027-03-22", f"edge-{index}")
            | {"case_id": f"case-{index}"}
            for index in range(4)
        ],
    }
    seen_caches: list[str] = []

    def fake_run_benchmark(child, *, runs_root, data_root, release_root,
                            workspace_root, fault_injection, counterbalance):
        seen_caches.append(child["output_roots"]["daily_cost_cache"])
        return {"arms": {"exhaustive": {}}, "comparison": {}}

    monkeypatch.setattr(base, "run_benchmark", fake_run_benchmark)
    suite.run_suite(
        registration, runs_root=tmp_path, data_root=tmp_path,
        release_root=tmp_path / "releases", workspace_root=tmp_path / "ws")
    assert len(set(seen_caches)) == 4, (
        "every case must get a distinct daily_cost_cache root, or "
        f"case caches collide: {seen_caches}")
    for case_id, cache in zip(
            (f"case-{index}" for index in range(4)), seen_caches):
        assert case_id in cache


def _case(*, semantic=True, saved=1, viable=2, error=False):
    checks = {
        "candidate_costs_field_identical": semantic,
        "hard_failures_identical": semantic,
        "health_classifications_identical": semantic,
        "status_identical": semantic,
        "selected_ids_identical": semantic,
        "final_decision_identical": semantic,
        "sumo_verifications_saved": saved > 0,
        "stop_proof_valid": semantic,
        "cache_hits_consistent": semantic,
        "restart_equivalent": semantic,
        "no_resource_cap_regression": semantic,
    }
    comparison = {"sumo_verifications_saved": saved}
    if error:
        comparison["execution_error"] = {"type": "RuntimeError"}
    return {
        "pilot_health_viable_candidate_count": viable,
        "comparison": comparison,
        "gates": {"checks": checks, "passed": semantic and saved > 0},
    }


def _thresholds():
    return {
        "case_count": 4,
        "minimum_cases_with_two_health_viable_candidates": 2,
        "minimum_total_sumo_verifications_saved": 1,
    }


def test_suite_gate_requires_equivalence_health_and_positive_saving():
    result = suite.suite_gate_results(
        [_case(saved=0), _case(saved=1), _case(saved=0), _case(saved=0)],
        _thresholds())
    assert result["passed"] is True
    assert result["measured"] == {
        "case_count": 4,
        "cases_with_two_health_viable_candidates": 4,
        "total_sumo_verifications_saved": 1,
    }


def test_suite_gate_fails_if_any_case_changes_semantics():
    result = suite.suite_gate_results(
        [_case(), _case(), _case(semantic=False), _case()], _thresholds())
    assert result["passed"] is False
    assert result["checks"]["all_case_equivalence_gates"] is False


def test_suite_gate_fails_without_two_discriminating_health_cases():
    result = suite.suite_gate_results(
        [_case(viable=2), _case(viable=1), _case(viable=0), _case(viable=1)],
        _thresholds())
    assert result["passed"] is False
    assert result["checks"][
        "minimum_cases_with_two_health_viable_candidates"] is False


def test_suite_gate_fails_closed_on_a_case_execution_error():
    result = suite.suite_gate_results(
        [_case(), _case(), _case(), _case(error=True)], _thresholds())
    assert result["passed"] is False
    assert result["checks"]["no_execution_errors"] is False
