from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from traffic_sim.simulation.closure_ranking import ClosureCost
from traffic_sim.simulation.cost_ordered_execution import ParentCost
from traffic_sim.simulation.cost_ordered_search import run_cost_ordered_search
from traffic_sim.core.closure_calendar import generate_closure_schedules
from tests.test_cost_ordered_execution import (
    FakeCostSource,
    FakeRunner,
    _ordered_prices,
    _screen_builder,
    _spec as _execution_spec,
)
from tests.test_cost_ordered_search import (
    _candidate,
    _evidence,
    _policy,
)

from tools.freeze_subhour_preregistration import (
    build_registration,
    verify_registration,
    write_registration,
)
from tools.profile_monthly_cost_ledger import profile_ledger
from tools import profile_monthly_cost_ledger as profile_module
from tools import subhour_cost_ordered_benchmark as benchmark_module
from tools import ai_flow
from tools.product_arm import _FixtureCostSource, _FixtureRunner
from tools.subhour_cost_ordered_benchmark import outcome_free_tuple
from tools.evaluate_subhour_q_policy import (
    FINALIST_STRESS,
    INCONCLUSIVE,
    Q50_ONLY,
    ROBUST_THREE_VARIANT,
    build_registration as build_gate_s_registration,
    classify,
    evaluate as evaluate_gate_s,
    extract_gate_s_evidence,
    _load_gate_evidence,
    _derive_gate_inputs,
)
from run_monthly_closure_search import (
    build_phase_status_artifact,
    build_phase6_registration,
    phase6_outcome,
    _phase6_terminal_status,
    _phase6_runtime_telemetry,
    _require_phase6_green_prerequisites,
    verify_phase6_registration,
)
from traffic_sim.simulation.monthly_search import ActiveBudgetExceeded, ActiveTimeController
from traffic_sim.simulation import monthly_demand
from traffic_sim.simulation.phase6_eligibility import (
    phase6_prerequisites_allow,
)


def _qualified_demand_manifest():
    keys = {"weekday": "a" * 32, "weekend": "b" * 32}
    variant = {"route_file": "calibrated.rou.xml", "content_digests": {}}
    manifest = {
        "schema": monthly_demand._PHASE_D_QUALIFIED_MANIFEST_SCHEMA,
        "kind": monthly_demand._PHASE_D_QUALIFIED_MANIFEST_KIND,
        "evidence_id": "phase-d-tests", "status": "PASS",
        "code_approved": True, "source_digest": "1" * 64,
        "code_approval": {"status": "CODE_APPROVED", "source_digest": "1" * 64,
                          "source_manifest_sha256": "2" * 64,
                          "checks_sha256": "3" * 64, "checks_status": "PASS",
                          "impact_inventory_sha256": "4" * 64},
        "search_contract": dict(monthly_demand._PHASE_D_SEARCH_CONTRACT),
        "sensor_route_policy_version": "test-policy", "network_sha256": "5" * 64,
        "support_audit_pass": True, "adopted_catalog_keys": keys,
        "adoption": {"sha256": "6" * 64, "catalog_keys": keys},
        "catalogs": {pool: {"catalog_key": key, "manifest_sha256": "7" * 64,
                              "routes_sha256": "8" * 64,
                              "metadata_sha256": "9" * 64}
                     for pool, key in keys.items()},
        "archives": {"fixture": {"build_key": "fixture",
                                    "archive_content_key": "a" * 64,
                                    "variants": {name: dict(variant) for name in
                                                 ("q10", "q50", "q90")}}},
    }
    manifest["content_key"] = monthly_demand._canonical_digest(manifest)
    return manifest


def _write_registration_binding(tmp_path, registration):
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(registration), encoding="utf-8")
    return path


def _minimal_bounded_registration(tmp_path):
    registration = {
        "evidence_id": "preflight-resource-test",
        "content_key": "registration-content",
        "selection": {"selected_ids": ["search-content"]},
        "selected_cases": [{"case_id": "case-1",
                             "search_content_key": "search-content"}],
        "fresh_roots": {
            "workspace_namespace": str(tmp_path / "workspace"),
            "daily_cost_cache": str(tmp_path / "cache"),
            "output_namespace": str(tmp_path / "outputs"),
        },
        "caps": {},
    }
    return registration, _write_registration_binding(tmp_path, registration)


def test_preregistration_is_outcome_blind_and_append_only(tmp_path):
    record = build_registration()
    assert record["reads_outcomes"] is False
    assert record["selection_reads_outcomes"] is False
    assert record["arms"]["only_allowed_difference"] == "disable_early_stop"
    verify_registration(record)
    destination = tmp_path / "preregistration.json"
    write_registration(destination, record)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_registration(destination, record)
    assert json.loads(destination.read_text(encoding="utf-8"))["content_key"] \
        == record["content_key"]


def test_phase3_source_drift_unpromotes_completed_case(monkeypatch, tmp_path):
    from traffic_sim.simulation import workspace as workspace_module
    from tools import process_census

    class AvailableLock:
        path = tmp_path / "runs" / ".demand-workspace.lock"

        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            return True

        def release(self):
            pass

    calls = 0

    def verify_then_drift(_registration, *, root):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ValueError("source digest drift")

    registration = {
        "evidence_id": "phase3-drift-test",
        "content_key": "registration-key",
        "selection": {"selected_ids": ["search-key"]},
        "selected_cases": [{"case_id": "case-1"}],
        "fresh_roots": {"workspace_namespace": "workspace"},
        "caps": {},
    }
    _write_registration_binding(tmp_path, registration)
    monkeypatch.setattr(workspace_module, "WorkspaceLock", AvailableLock)
    monkeypatch.setattr(process_census, "process_group_snapshot",
                        lambda: [(1, 1, 1)])
    monkeypatch.setattr(benchmark_module, "verify_registration",
                        verify_then_drift)
    monkeypatch.setattr(
        benchmark_module, "_run_case",
        lambda *_args, **_kwargs: {
            "case_id": "case-1", "search_content_key": "search-key",
            "gates_passed": True})

    outcome = benchmark_module.run_registered(
        registration, runs_root=tmp_path / "runs", data_root=tmp_path,
        workspace_root=tmp_path / "workspace",
        registration_path=tmp_path / "registration.json")

    assert outcome["status"] == "INCONCLUSIVE_SOURCE_DRIFT"
    assert outcome["case_results"] == []
    assert outcome["unpromoted_execution"]["completed_case_count"] == 1
    completed_record = outcome["unpromoted_execution"]["completed_records"][0]
    assert completed_record["case_id"] == "case-1"
    assert len(completed_record["unpromoted_record_sha256"]) == 64
    assert outcome["correctness_gates"]["status"] \
        == "NOT_EVALUATED_SOURCE_DRIFT"
    assert outcome["selection"]["selected_ids"] == ["search-key"]
    assert outcome["suite_consumption"]["attempts"] == 0
    assert outcome["suite_consumption"]["active_seconds"] > 0
    assert outcome["suite_consumption"]["disk_growth_bytes"] == 0
    assert outcome["suite_consumption"]["execution_started"] is True
    assert "disk_roots" in outcome["resources"]


@pytest.mark.parametrize("field", ["phase_3", "phase_4"])
def test_phase6_gate_rejects_inconclusive_bounded_prerequisite(field):
    statuses = {
        "phase_0": "PASS", "phase_1": "PASS", "phase_2": "PASS",
        "phase_3": "PASS", "phase_4": "PASS", "phase_5": "NOT_TRIGGERED",
        "review": "PASS",
    }
    statuses[field] = "INCONCLUSIVE"
    assert phase6_prerequisites_allow(
        statuses, phase3_population_eligible=False, phase_d_pass=True) is False


def test_phase6_ready_uses_publication_reserve_after_work_stop():
    result = {"status": "unique_winner"}
    assert _phase6_terminal_status(
        result, work_stopped_elapsed_s=55 * 60 - 1,
        publication_elapsed_s=55 * 60 + 60,
    ) == "READY"
    assert _phase6_terminal_status(
        result, work_stopped_elapsed_s=55 * 60 + 1,
        publication_elapsed_s=55 * 60 + 60,
    ) == "INCONCLUSIVE"
    assert _phase6_terminal_status(
        result, work_stopped_elapsed_s=55 * 60 - 1,
        publication_elapsed_s=60 * 60 + 1,
    ) == "INCONCLUSIVE"
    assert _phase6_terminal_status(
        result, work_stopped_elapsed_s=55 * 60 - 1,
        publication_elapsed_s=55 * 60 + 60,
        process_tree_rss_error="census lost after work",
    ) == "INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE"


def test_registered_exception_keeps_outer_phase3_telemetry_and_all_roots(
        monkeypatch, tmp_path):
    """An executed exception remains measurable and unpromoted."""
    from traffic_sim.simulation import workspace as workspace_module
    from tools import process_census
    from tools import product_arm

    class AvailableLock:
        path = tmp_path / "runs" / ".demand-workspace.lock"

        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            return True

        def release(self):
            pass

    class Sampler:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            return self

        def stop(self):
            return 77

    registration, registration_path = _minimal_bounded_registration(tmp_path)

    def failing_case(_registration, _case, **kwargs):
        case_root = kwargs["workspace_root"] / "case-1" / "arm"
        case_root.mkdir(parents=True)
        (case_root / "isolated-arm-result-cost.json").write_text(
            json.dumps({"result": {
                "exact_launch_records": [{"attempt": 1}],
                "active_elapsed_s": 2.5,
                "peak_rss_bytes": 31,
            }}), encoding="utf-8")
        (tmp_path / "cache").mkdir()
        (tmp_path / "cache" / "cache.bin").write_bytes(b"cache")
        (tmp_path / "outputs").mkdir()
        (tmp_path / "outputs" / "output.bin").write_bytes(b"output")
        raise ValueError("producer failed after SUMO work")

    monkeypatch.setattr(workspace_module, "WorkspaceLock", AvailableLock)
    monkeypatch.setattr(process_census, "process_group_snapshot",
                        lambda: [(1, 1, 1)])
    monkeypatch.setattr(benchmark_module,
                        "_verify_registration_for_execution",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(product_arm, "ProcessTreeRSSSampler", Sampler)
    monkeypatch.setattr(benchmark_module, "_run_case", failing_case)

    outcome = benchmark_module.run_registered(
        registration, runs_root=tmp_path / "runs", data_root=tmp_path,
        workspace_root=tmp_path / "unused", registration_path=registration_path)

    telemetry = outcome["case_results"][0]["phase3_telemetry"]
    assert outcome["status"] == "INCONCLUSIVE_BOUNDED_GATES"
    assert telemetry["attempts"] == 1
    assert telemetry["active_seconds"] == 2.5
    assert telemetry["peak_rss_bytes"] == 77
    assert telemetry["disk_growth_bytes"] == 112
    assert set(telemetry["disk_roots"]) == {
        str((tmp_path / name).resolve())
        for name in ("workspace", "cache", "outputs")
    }
    assert outcome["resources"]["attempts"] == 1
    assert outcome["resources"]["disk_growth_bytes"] == 112


def test_registered_execution_accepts_owned_roots_after_start_and_on_resume(
        monkeypatch, tmp_path):
    """Freshness is checked once; immutable bindings remain checked per case."""
    from traffic_sim.simulation import workspace as workspace_module
    from tools import process_census

    class AvailableLock:
        path = tmp_path / "runs" / ".demand-workspace.lock"

        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            return True

        def release(self):
            pass

    registration = {
        "evidence_id": "phase3-resume-roots-test",
        "content_key": "registration-key",
        "selection": {"selected_ids": ["search-a", "search-b"]},
        "selected_cases": [{"case_id": "case-a"}, {"case_id": "case-b"}],
        "fresh_roots": {
            "workspace_namespace": "workspace",
            "daily_cost_cache": "cache",
            "output_namespace": "outputs",
        },
        "caps": {},
    }
    _write_registration_binding(tmp_path, registration)
    calls = []
    fail_after = {"count": 0}

    def verify(_registration, *, root, require_fresh_roots=True):
        calls.append(require_fresh_roots)
        fail_after["count"] += 1
        if fail_after["count"] == 5:
            raise ValueError("source digest drift after two cases")

    def run_case(_registration, case, **kwargs):
        case_root = kwargs["workspace_root"] / case["case_id"]
        case_root.mkdir(parents=True, exist_ok=True)
        (case_root / "completed.json").write_text("{}", encoding="utf-8")
        # Drift terminals account the complete registered namespace, not
        # only the current case directory.
        (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
        (tmp_path / "cache" / "partial.cache").write_bytes(b"cache")
        (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "outputs" / "partial.out").write_bytes(b"output")
        return {"case_id": case["case_id"], "gates_passed": False}

    monkeypatch.setattr(workspace_module, "WorkspaceLock", AvailableLock)
    monkeypatch.setattr(process_census, "process_group_snapshot",
                        lambda: [(1, 1, 1)])
    monkeypatch.setattr(benchmark_module, "verify_registration", verify)
    monkeypatch.setattr(benchmark_module, "_run_case", run_case)

    outcome = benchmark_module.run_registered(
        registration, runs_root=tmp_path / "runs", data_root=tmp_path,
        workspace_root=tmp_path / "ignored-cli-root",
        registration_path=tmp_path / "registration.json")

    assert outcome["status"] == "INCONCLUSIVE_SOURCE_DRIFT"
    assert outcome["unpromoted_execution"]["completed_case_count"] == 2
    assert outcome["suite_consumption"]["disk_growth_bytes"] == 15
    assert outcome["resources"]["disk_growth_bytes"] == 15
    assert (tmp_path / "workspace" / "case-a" / "completed.json").is_file()
    assert (tmp_path / "workspace" / "case-b" / "completed.json").is_file()
    assert calls == [True, False, False, False, False]

    fail_after["count"] = 0
    resumed = benchmark_module.run_registered(
        registration, runs_root=tmp_path / "runs", data_root=tmp_path,
        workspace_root=tmp_path / "ignored-cli-root",
        registration_path=tmp_path / "registration.json")
    assert resumed["status"] == "INCONCLUSIVE_SOURCE_DRIFT"
    assert resumed["unpromoted_execution"]["completed_case_count"] == 2
    assert calls[5:] == [False, False, False, False, False]


def test_normal_registered_terminal_binds_top_level_evidence_identity(
        monkeypatch, tmp_path):
    from traffic_sim.simulation import workspace as workspace_module
    from tools import process_census

    class AvailableLock:
        path = tmp_path / "runs" / ".demand-workspace.lock"

        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            return True

        def release(self):
            pass

    registration = {
        "evidence_id": "normal-producer-id",
        "content_key": "registration-key",
        "selection": {"selected_ids": ["search-key"]},
        "selected_cases": [{"case_id": "case-1"}],
        "fresh_roots": {
            "workspace_namespace": str(tmp_path / "workspace"),
            "daily_cost_cache": str(tmp_path / "cache"),
            "output_namespace": str(tmp_path / "outputs"),
        },
        "caps": {},
    }
    registration_path = _write_registration_binding(tmp_path, registration)
    monkeypatch.setattr(workspace_module, "WorkspaceLock", AvailableLock)
    monkeypatch.setattr(process_census, "process_group_snapshot",
                        lambda: [(1, 1, 1)])
    monkeypatch.setattr(benchmark_module, "_verify_registration_for_execution",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        benchmark_module, "_run_case",
        lambda *_args, **_kwargs: {
            "case_id": "case-1", "search_content_key": "search-key",
            "gates_passed": True, "terminal_status": None,
            "comparison": {
                "active_elapsed_s": {"cost_ordered": 1.0,
                                     "ordered_exhaustive": 1.0},
                "restart_active_elapsed_s": 0.0,
                "restart": {"exact_launch_attempts": 0},
            },
            "arms": {"cost_ordered": {"exact_launch_records": []},
                     "ordered_exhaustive": {"exact_launch_records": []}},
        },
    )
    monkeypatch.setattr(
        benchmark_module, "_populate_gate_s",
        lambda _results: {"population_complete": True,
                          "variants": {variant: {} for variant in ("q10", "q50", "q90")}},
    )

    outcome = benchmark_module.run_registered(
        registration, runs_root=tmp_path / "runs", data_root=tmp_path,
        workspace_root=tmp_path / "unused", registration_path=registration_path)
    assert outcome["status"] == "PASS"
    assert outcome["evidence_id"] == registration["evidence_id"]
    assert outcome["registration"]["evidence_id"] == registration["evidence_id"]


def test_registered_performance_miss_preserves_gate_s_population(
        monkeypatch, tmp_path):
    """A measured speed miss remains usable input for the separate Gate S."""
    from traffic_sim.simulation import workspace as workspace_module
    from tools import process_census

    class AvailableLock:
        path = tmp_path / "runs" / ".demand-workspace.lock"

        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            return True

        def release(self):
            pass

    registration_path = tmp_path / "registration.json"
    registration = {
        "schema": "subhour_cost_ordered_bounded_registration_v1",
        "evidence_id": "performance-miss-registration",
        "content_key": "registration-content",
        "selection": {"selected_ids": ["search-content"]},
        "selected_cases": [{"case_id": "case-1",
                             "search_content_key": "search-content"}],
        "fresh_roots": {
            "workspace_namespace": str(tmp_path / "workspace"),
            "daily_cost_cache": str(tmp_path / "cache"),
            "output_namespace": str(tmp_path / "outputs"),
        },
        "caps": {},
    }
    registration["content_key"] = benchmark_module._key({
        key: value for key, value in registration.items()
        if key not in {"content_key", "registered_at"}
    })
    registration_path.write_text(json.dumps(registration), encoding="utf-8")
    gate_decision = {
        "hard_failures": [], "viable_set": ["case-1:a"],
        "finalists": ["case-1:a"], "winner": ["case-1:a"],
        "capacity_exceeded": False,
    }
    gate_variants = {
        variant: {
            "decision": gate_decision,
            "decision_relevant_failures": [],
            "winner_cost": 1.0,
            "reference_winner_cost": 1.0,
            "candidate_costs": {
                "case-1:a": {
                    "added_vehicle_hours": 1.0,
                    "added_metres_total": 1.0,
                    "vehicles_affected": 1,
                    "vehicles_no_detour": 0,
                    "feasible": True,
                }
            },
        }
        for variant in ("q10", "q50", "q90")
    }
    monkeypatch.setattr(workspace_module, "WorkspaceLock", AvailableLock)
    monkeypatch.setattr(process_census, "process_group_snapshot",
                        lambda: [(1, 1, 1)])
    monkeypatch.setattr(benchmark_module,
                        "_verify_registration_for_execution",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        benchmark_module, "_run_case",
        lambda *_args, **_kwargs: {
            "case_id": "case-1", "search_content_key": "search-content",
            "decision_population_complete": True,
            "performance_gates_passed": False,
            "gates_passed": False, "terminal_status": "READY",
            "comparison": {
                "active_elapsed_s": {"cost_ordered": 1.0,
                                     "ordered_exhaustive": 2.0},
                "restart_active_elapsed_s": 0.0,
                "restart": {"exact_launch_attempts": 0},
            },
            "arms": {"cost_ordered": {"exact_launch_records": []},
                     "ordered_exhaustive": {"exact_launch_records": []}},
        },
    )
    monkeypatch.setattr(
        benchmark_module, "_populate_gate_s",
        lambda _results: {"population_complete": True,
                          "variants": gate_variants},
    )
    outcome = benchmark_module.run_registered(
        registration, runs_root=tmp_path / "runs", data_root=tmp_path,
        workspace_root=tmp_path / "unused", registration_path=registration_path)

    assert outcome["status"] == "INCONCLUSIVE_PERFORMANCE_GATE"
    assert outcome["gate_s"]["population_complete"] is True
    assert outcome["registration"]["path"] == str(registration_path.resolve())
    source_path = tmp_path / "bounded-outcome.json"
    source_path.write_text(json.dumps(outcome), encoding="utf-8")
    extracted = extract_gate_s_evidence(
        source_path=source_path, evidence_id="gate-evidence-performance-miss")
    evidence_path = tmp_path / "gate-evidence.json"
    evidence_path.write_text(json.dumps(extracted, sort_keys=True), encoding="utf-8")
    gate_registration = build_gate_s_registration(
        evidence_path=evidence_path,
        evidence_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        evidence_id="gate-registration-performance-miss")
    assert evaluate_gate_s(gate_registration)["status"] == Q50_ONLY


@pytest.mark.parametrize("slow_exhaustive", [False, True],
                         ids=["performance-miss", "bounded-pass"])
def test_real_registered_case_publishes_completeness_for_gate_s(
        monkeypatch, tmp_path, slow_exhaustive):
    """Run the registered producer path, not a fabricated case result.

    The SUMO seam is the repository's controlled in-memory runner used by the
    differential benchmark tests.  ``_run_case``, the paired comparison,
    workspace artifacts, Gate S extraction, and the registered outer terminal
    remain production code throughout this regression.
    """
    from traffic_sim.simulation import workspace as workspace_module
    from tests.test_monthly_sumo import _durable_chain
    from traffic_sim.simulation import monthly_sumo
    from traffic_sim.simulation.independent_daily import daily_unit_records
    from tools import process_census

    repo_root = Path(__file__).resolve().parents[1]
    validation = tmp_path / "validation"
    validation.mkdir()
    evidence_policy = ai_flow.load_config(
        Path(__file__).resolve().parents[1]
        / ".ai-flow" / "config.complete-subhour.toml",
        Path(__file__).resolve().parents[1],
    ).evidence_policy
    # This test exercises the post-Phase-6/Gate-S validator, not the bounded
    # production config's Phase-5 execution ceiling.
    evidence_policy = replace(
        evidence_policy, allow_phase6=False, allow_gate_s=True)
    initial_evidence_baseline = ai_flow.evidence_inventory(
        tmp_path, evidence_policy.registration_globs)

    spec = _execution_spec()
    schedules, prices = _ordered_prices(spec)
    # Keep every controlled fixture inside the real verified population.  The
    # ascending cost ledger gives the real cost-ordered producer a measurable
    # early-stop boundary; the unslowed parameter remains a performance miss.
    prices = {schedule.schedule_id: (0.0 if index < 4 else float(index + 1))
              for index, schedule in enumerate(schedules)}
    schedule_ids = [item.schedule_id for item in schedules]
    daily_ids = {
        schedule.schedule_id: next(
            build().schedule_id
            for _unit_id, _identity, build in daily_unit_records(spec, schedule)
        )
        for schedule in schedules
    }
    (tmp_path / "sumo").mkdir()
    (tmp_path / "sumo" / "net.net.xml").write_text("<net/>", encoding="utf-8")
    (tmp_path / "sumo" / "plain.edg.xml").write_text(
        "<edges/>", encoding="utf-8")
    registration = {
        "schema": "subhour_cost_ordered_bounded_registration_v1",
        "evidence_id": "real-registered-performance-miss",
        "content_key": "",
        "reads_outcomes": False,
        "selection": {"selected_ids": [spec.content_key]},
        "selected_cases": [{
            "case_id": "case-1", "search_content_key": spec.content_key,
            "spec": spec.to_dict(),
        }],
        "fresh_roots": {
            "workspace_namespace": str(tmp_path / "workspace"),
            "daily_cost_cache": str(tmp_path / "cache"),
            "output_namespace": str(tmp_path / "outputs"),
        },
        "caps": {
            "active_seconds": 3600.0,
            "attempts_per_case": 1000,
            "restart_timeout_seconds": 30.0,
            "peak_rss_bytes": 10**9,
            "disk_growth_bytes": 10**9,
            "suite_active_seconds": 3600.0,
            "attempts_per_suite": 1000,
            "suite_disk_growth_bytes": 10**9,
        },
        "fixtures": {
            name: {
                "declared": True, "symmetric": True,
                "application": f"controlled-{name}",
            }
            for name in ("backfill", "no_detour", "dense_boundary",
                         "restart_cancel")
        },
    }
    # Build the smallest complete registration accepted by the production
    # verifier.  The eight entries are controlled metadata inputs; no outcome
    # is consulted to select them.  This keeps the test cheap while still
    # exercising the real registration binding and freshness checks.
    selection_rule = (
        "eligible metadata only; sort by sha256(canonical((demand_period, "
        "directed_edge, date, window, search_content_key))) and take the "
        "first member of each lexicographically smallest four-edge x two-period "
        "stratum, then fill in that same order")
    selected_cases = []
    eligible = []
    for index in range(8):
        case_spec = replace(
            spec, directed_edges=(f"controlled-edge-{index}_0",))
        item = {
            "search_id": case_spec.search_id,
            "search_content_key": case_spec.content_key,
            "spec": case_spec.to_dict(),
            "candidate_count": len(schedules),
            "unique_daily_unit_count": len(daily_ids),
            "work_dates": [schedules[0].first_work_date],
            "demand_period": f"controlled-period-{index // 4}",
        }
        item["selection_tuple"] = list(outcome_free_tuple(item))
        item["selection_sha256"] = benchmark_module._key(
            outcome_free_tuple(item))
        eligible.append(item)
        selected_cases.append({**item, "case_id": f"case-{index + 1}"})
    registration.update({
        "data_root": str(repo_root),
        "runs_root": str((tmp_path / "runs").resolve()),
        "archives": {},
        "selection": {
            "rule": selection_rule,
            "eligible_list_digest": "controlled-eligible-digest",
            "eligible_count": len(eligible),
            "selected_ids": [item["search_content_key"] for item in selected_cases],
            "distinct_edges": ["edge-a", "edge-b", "edge-c", "edge-d"],
            "distinct_periods": ["controlled-period-0", "controlled-period-1"],
            "selected_case_count": len(selected_cases),
        },
        "selected_cases": selected_cases,
        "policy": {
            "path": "validation/monthly_search_policy_v3.json",
            "sha256": benchmark_module.sha256_file(
                repo_root / "validation/monthly_search_policy_v3.json"),
        },
        "sources": benchmark_module._source_digests(),
        "runtime": {"schema": "controlled-runtime-v1"},
        "network": {
            "path": "sumo/net.net.xml",
            "sha256": benchmark_module.sha256_file(repo_root / "sumo/net.net.xml"),
        },
        "network_metadata": {
            "path": "sumo/network_metadata.json",
            "sha256": benchmark_module.sha256_file(
                repo_root / "sumo/network_metadata.json"),
        },
        "arms": {
            "only_allowed_difference": "disable_early_stop",
            "cost_ordered": {"disable_early_stop": False},
            "ordered_exhaustive": {"disable_early_stop": True},
        },
        "gates": {},
        "outcome_record": "controlled-outcome.json",
    })
    qualified = _qualified_demand_manifest()
    qualified_path = validation / "qualified-demand.json"
    qualified_path.write_text(json.dumps(qualified), encoding="utf-8")
    registration["qualified_demand_manifest"] = {
        "path": str(qualified_path),
        "sha256": hashlib.sha256(qualified_path.read_bytes()).hexdigest(),
        "content_key": qualified["content_key"],
        "evidence_id": qualified["evidence_id"],
    }
    registration["caps"]["attempts_per_suite"] = 4000
    monkeypatch.setattr(
        benchmark_module, "select_cases",
            lambda _runs_root, *_args: {
            "eligible": eligible,
            "eligible_list_digest": "controlled-eligible-digest",
            "selected_ids": registration["selection"]["selected_ids"],
            "selected": selected_cases,
        },
    )
    monkeypatch.setattr(benchmark_module.base, "sumo_runtime_identity",
                        lambda _data_root: registration["runtime"])
    registration["content_key"] = benchmark_module._key({
        key: value for key, value in registration.items()
        if key not in {"content_key", "registered_at"}
    })
    registration_path = validation / (
        "subhour_bounded_sumo_registration_real-performance-miss.json")
    registration_path.write_text(json.dumps(registration), encoding="utf-8")

    class AvailableLock:
        path = tmp_path / "runs" / ".demand-workspace.lock"

        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            return True

        def release(self):
            pass

    class ObservableFakeRunner(FakeRunner):
        """Controlled backend that publishes the producer's identity surfaces.

        The search, workspace, comparison and validator remain production
        code.  This fixture only supplies the same durable observation and
        result-neutral telemetry surfaces that a bounded backend must expose.
        """

        _durable_launch_records = {}
        _durable_cache_events = {}

        def __init__(self, *, prices, cache_root, spec_arg,
                     slow_exhaustive=False, slow_cost_ordered=False):
            super().__init__(prices=prices)
            self._spec = spec_arg
            self._schedule_ids = [
                item.schedule_id for item in generate_closure_schedules(spec_arg)]
            self._daily_ids = {
                schedule.schedule_id: next(
                    build().schedule_id for _unit_id, _identity, build
                    in daily_unit_records(spec_arg, schedule))
                for schedule in generate_closure_schedules(spec_arg)
            }
            self._cache_root = Path(cache_root)
            self._storage_runner = DurableStore(self._cache_root)
            durable_key = str(self._cache_root.resolve())
            self._digests = {}
            self._launch_records = [
                dict(item) for item in self._durable_launch_records.get(
                    durable_key, [])]
            self._cache_event_records = [
                dict(item) for item in self._durable_cache_events.get(
                    durable_key, [])]
            self._seen_events = set()
            self._durable_key = durable_key
            self._slow_exhaustive = slow_exhaustive
            self._slow_cost_ordered = slow_cost_ordered

        def _canonical_evidence_cache_root(self):
            return self._cache_root

        def _disruption(self, candidate_id):
            records = super()._disruption(candidate_id)
            if candidate_id in set(self._schedule_ids[2:4]):
                return tuple({
                    **dict(record),
                    "added_vehicle_hours": 0.0,
                    "added_metres_total": 0.0,
                    "vehicles_affected": 0,
                    "vehicles_no_detour": 0,
                } for record in records)
            return records

        def run_candidate(self, schedule, *, target_repetitions, existing,
                          stage):
            # A controlled backend delay gives the paired producer either a
            # measured wall/active-time PASS or performance-miss case without
            # replacing the comparator's producer-owned timing fields.
            if self._slow_exhaustive:
                time.sleep(0.003)
            elif self._slow_cost_ordered:
                time.sleep(0.05)
            evidence = super().run_candidate(
                schedule, target_repetitions=target_repetitions,
                existing=existing, stage=stage)
            if not evidence.disruption and schedule.schedule_id in self.prices:
                evidence = replace(
                    evidence,
                    disruption=self._disruption(schedule.schedule_id),
                )
            digests = []
            for observation in evidence.observations:
                daily_id = self._daily_ids[schedule.schedule_id]
                digest = self._digests.get((schedule.schedule_id,
                                            observation.demand_variant,
                                            observation.seed))
                if digest is None:
                    digest = _durable_chain(
                        self._storage_runner,
                        candidate_id=daily_id,
                        work_date=schedule.first_work_date,
                        variant=observation.demand_variant,
                        seed=observation.seed,
                        unit_id=f"unit-{schedule.schedule_id}",
                    )
                self._digests[(schedule.schedule_id,
                              observation.demand_variant,
                              observation.seed)] = digest
                digests.append(digest)
                launch_identity = (
                    daily_id, schedule.first_work_date, stage,
                    observation.demand_variant, observation.seed, 1)
                if launch_identity not in {
                    tuple(item[key] for key in (
                        "candidate_id", "work_date", "stage", "variant",
                        "seed", "attempt"))
                    for item in self._launch_records}:
                    self._launch_records.append({
                        "candidate_id": daily_id,
                        "work_date": schedule.first_work_date,
                        "stage": stage,
                        "variant": observation.demand_variant,
                        "seed": observation.seed,
                        "attempt": 1,
                        "timed_out": False,
                        "outcome": "success",
                    })
                    self._durable_launch_records[self._durable_key] = [
                        dict(item) for item in self._launch_records]
                event_identity = (schedule.schedule_id,
                                  observation.demand_variant,
                                  observation.seed)
                if event_identity not in self._seen_events:
                    self._seen_events.add(event_identity)
                    unit_id = ":".join(str(value) for value in event_identity)
                    self._cache_event_records.extend((
                        {"unit_id": unit_id, "event": "miss"},
                        {"unit_id": unit_id, "event": "publication"},
                    ))
                    self._durable_cache_events[self._durable_key] = [
                        dict(item) for item in self._cache_event_records]
            return replace(
                evidence,
                canonical_observation_digests=tuple(digests),
            )

        def timing_snapshot(self):
            snapshot = super().timing_snapshot()
            launch_records = list(self._launch_records)
            snapshot.update({
                "exact_launch_records": launch_records,
                "exact_launch_telemetry": {
                    stage: {
                        "attempts": sum(
                            item["stage"] == stage for item in launch_records),
                        "timeouts": 0,
                        "other_outcomes": sum(
                            item["stage"] == stage for item in launch_records),
                    }
                    for stage in ("pilot", "finalist")
                },
                "cache_hits": 0,
                "cache_misses": sum(
                    item["event"] == "miss"
                    for item in self._cache_event_records),
                "cache_corrupt": 0,
                "cache_publications": sum(
                    item["event"] == "publication"
                    for item in self._cache_event_records),
                "cache_event_records": list(self._cache_event_records),
            })
            return snapshot

    class DurableStore:
        """Small content-addressed store for the real validator chain."""

        def __init__(self, cache_root):
            self.cache_root = Path(cache_root)

        def _store(self, subdir, source, suffix):
            payload = Path(source).read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            destination = self.cache_root / subdir / digest[:2] / (
                f"{digest}{suffix}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                destination.write_bytes(payload)
            return digest

        def _preserve_transformed_route(self, path):
            return self._store("transformed-routes", path, ".rou.xml")

        def _preserve_access_impact_evidence(self, path):
            return self._store("access-impact", path, ".json")

        def _preserve_canonical_observation(self, payload):
            digest = monthly_sumo._canonical_digest(payload)
            destination = (self.cache_root / "canonical-observations"
                           / digest[:2] / f"{digest}.json")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                destination.write_text(
                    json.dumps(payload, sort_keys=True), encoding="utf-8")
            return digest

    def fake_build_arm(spec_arg, *, cost_ordered, objective_method, **_kwargs):
        local_schedules = generate_closure_schedules(spec_arg)
        local_prices = {
            schedule.schedule_id: (
                0.0 if index < 4 else float(index + 1))
            for index, schedule in enumerate(local_schedules)
        }
        runner = ObservableFakeRunner(
            prices=local_prices,
            cache_root=Path(_kwargs["daily_results_cache_root"]) / "canonical",
            spec_arg=spec_arg,
            slow_exhaustive=(slow_exhaustive and "ordered_exhaustive" in str(
                _kwargs["daily_results_cache_root"])),
            slow_cost_ordered=((not slow_exhaustive) and "cost_ordered" in str(
                _kwargs["daily_results_cache_root"])),
        )
        source = FakeCostSource(local_prices) if cost_ordered else None
        return runner, _screen_builder(
            spec_arg, [item.schedule_id for item in local_schedules]), source

    original_comparison = benchmark_module.base.run_ordered_exhaustive_comparison

    def real_comparison(spec_arg, policy, **kwargs):
        kwargs = dict(kwargs)
        kwargs.pop("isolate_arms", None)
        return original_comparison(
            spec_arg, policy, isolate_arms=False, **kwargs)

    monkeypatch.setattr(workspace_module, "WorkspaceLock", AvailableLock)
    monkeypatch.setattr(process_census, "process_group_snapshot",
                        lambda: [(1, 1, 1)])
    monkeypatch.setattr(benchmark_module.base.pa, "build_arm", fake_build_arm)
    # This controlled integration deliberately runs both fake arms in the
    # pytest process.  ru_maxrss would therefore include every test module
    # imported before this case and make the result order-dependent.  Keep the
    # registered 1 GB production cap unchanged while controlling the fake
    # arms' own resource measurement, just as their SUMO/process census is
    # controlled above.
    monkeypatch.setattr(benchmark_module.base.pa, "peak_rss_bytes", lambda: 0)
    monkeypatch.setattr(benchmark_module.base,
                            "run_ordered_exhaustive_comparison", real_comparison)

    outcome = benchmark_module.run_registered(
        registration, runs_root=tmp_path / "runs", data_root=tmp_path,
        workspace_root=tmp_path / "ignored", registration_path=registration_path)

    case = outcome["case_results"][0]
    assert "comparison" in case, case.get("error")
    comparison = case["comparison"]
    assert case["decision_population_complete"] is True
    assert comparison["exact_attempt_population_check"]["valid"] is True
    assert comparison["both_stop_proofs_valid"] is True
    assert comparison["fixture_application"]["applied"] is True
    assert comparison["fixture_application"]["restart_cancel_observed"] is True
    assert comparison["cancellation"]["called"] is True
    assert comparison["cancellation"]["queued_work_cancelled"] is True
    assert comparison["cancellation"]["no_later_starter"] is True
    # The performance-miss fixture adds a controlled delay to the cost-ordered
    # producer arm.  The bounded-pass variant delays the exhaustive arm; both
    # variants traverse the same real registration and comparison path.
    expected_status = (
        "PASS" if slow_exhaustive else "INCONCLUSIVE_PERFORMANCE_GATE"
    )
    assert outcome["status"] == expected_status, {
        "status": outcome["status"],
        "performance": comparison.get("performance_gates_passed"),
        "attempts": comparison.get("exact_attempts_reduction_fraction"),
        "active": comparison.get("awake_active_time_reduction_fraction"),
        "wall": comparison.get("wall_time_reduction_fraction"),
        "comparison": comparison,
    }
    if slow_exhaustive:
        assert all(item.get("gates_passed") is True
                   for item in outcome["case_results"]), outcome["case_results"]
    assert outcome["gate_s"]["population_complete"] is True
    assert outcome["registration"]["content_key"] == registration["content_key"]
    assert ai_flow._phase3_gate_source_paths(
        tmp_path,
        {"references": [{
            "path": str(tmp_path / "bounded-outcome.json")
        }]},
    ) == ()

    # The final validator consumes the exact published bytes, not the return
    # value held in memory by the producer.
    outcome_path = validation / (
        "subhour_bounded_sumo_outcome_real-performance-miss.json")
    outcome_path.write_text(json.dumps(outcome), encoding="utf-8")
    assert ai_flow._phase3_gate_source_paths(
        tmp_path,
        {"references": [{"path": str(outcome_path)}]},
    ) == (outcome_path.resolve(),)

    gate_evidence = extract_gate_s_evidence(
        source_path=outcome_path, evidence_id="gate-evidence-real-performance-miss"
    )
    gate_evidence_path = tmp_path / "gate-evidence-staged.json"
    gate_evidence_path.write_text(json.dumps(gate_evidence), encoding="utf-8")
    gate_registration = build_gate_s_registration(
        evidence_path=gate_evidence_path,
        evidence_sha256=hashlib.sha256(gate_evidence_path.read_bytes()).hexdigest(),
        evidence_id="gate-registration-real-performance-miss",
    )
    assert evaluate_gate_s(gate_registration)["status"] in {
        Q50_ONLY, FINALIST_STRESS, ROBUST_THREE_VARIANT, INCONCLUSIVE
    }

    gate_registration_path = tmp_path / "gate-registration-staged.json"
    gate_registration_path.write_text(
        json.dumps(gate_registration), encoding="utf-8")
    gate_outcome = evaluate_gate_s(gate_registration, root=tmp_path)
    gate_outcome_path = tmp_path / "gate-outcome-staged.json"
    gate_outcome_path.write_text(json.dumps(gate_outcome), encoding="utf-8")

    def reference(path):
        value = json.loads(path.read_text(encoding="utf-8"))
        return {"path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "content_key": value["content_key"]}

    profile = {
        "schema": "monthly_cost_ledger_profile_v1",
        "kind": "monthly_cost_ledger_profile", "release_evidence": False,
        "evidence_id": "phase4-real-performance-miss", "status": "PASS",
        "wall_time_s": 3.0, "sumo_attempts": 0, "sumo_started": False,
        "sumo_start_observation": {"before": 0, "after": 0, "delta": 0},
        "peak_rss_bytes": 12, "disk_growth_bytes": 4,
        "fresh_roots": {"output": str((tmp_path / "phase4").resolve())},
        "population_complete": True, "phase_timing_complete": True,
        "sumo_zero_launch_gate": True,
        "population": {"daily_units": 1950, "daily_variant_records": 5850,
                       "parents": 1690},
        "phase_5_decision": "TRIGGERED",
    }
    profile["content_key"] = ai_flow._canonical_digest(profile)
    profile_path = validation / (
        "monthly_cost_ledger_profile_subhour-real-performance-miss.json")
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    window_index = {
        "schema": "subhour_phase5_window_cost_index_evidence_v1",
        "kind": "subhour_phase5_window_cost_index", "release_evidence": False,
        "evidence_id": "phase5-real-performance-miss", "status": "PASS",
        "oracle": {"field_identical": True, "oracle_complete": True},
    }
    window_index["content_key"] = ai_flow._canonical_digest(window_index)
    window_index_path = validation / (
        "window_cost_index_subhour-real-performance-miss.json")
    window_index_path.write_text(json.dumps(window_index), encoding="utf-8")
    status_artifacts = {}
    phase3_report_status = "PASS" if slow_exhaustive else "INCONCLUSIVE"
    phase4_report_status = "INCONCLUSIVE" if slow_exhaustive else "PASS"
    if slow_exhaustive:
        profile["status"] = "INCONCLUSIVE"
        profile["content_key"] = ai_flow._canonical_digest({
            key: value for key, value in profile.items()
            if key != "content_key"
        })
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
    qualified_demand_manifest = {
        "schema": "subhour_qualified_demand_manifest_v1",
        "kind": "subhour_qualified_demand_manifest", "release_evidence": False,
        "evidence_id": "phase-d-real-performance-miss",
        "adopted_catalog_keys": {
            "weekday": "46f619b93152b0f2e21cd37a1c5e4991",
            "weekend": "fd92cb5c2cccf9112c4143c4eb6355ff",
        },
        "demand_variants": ["q10", "q50", "q90"],
    }
    qualified_demand_manifest["content_key"] = ai_flow._canonical_digest(
        qualified_demand_manifest)
    qualified_demand_manifest_path = validation / (
        "subhour_qualified_demand_manifest_real-performance-miss.json")
    qualified_demand_manifest_path.write_text(
        json.dumps(qualified_demand_manifest), encoding="utf-8")
    for phase, producer_path, status in (
            ("phase_3", outcome_path, phase3_report_status),
            ("phase_4", profile_path, phase4_report_status),
            ("phase_5", profile_path, "PASS")):
        artifact = {
            "schema": "subhour_phase_status_v1",
            "kind": "subhour_phase_status", "phase": phase,
            "status": status, "release_evidence": False,
            "evidence_id": f"{phase}-real-performance-miss-status",
            "lineage": {}, "references": [reference(producer_path)]
            + ([reference(registration_path),
                reference(qualified_demand_manifest_path)]
               if phase == "phase_3" else []),
        }
        artifact["content_key"] = ai_flow._canonical_digest(artifact)
        status_path = validation / f"{phase}-real-performance-miss-status.json"
        status_path.write_text(json.dumps(artifact), encoding="utf-8")
        status_artifacts[phase] = reference(status_path)
    phase5_status_path = validation / "phase_5-real-performance-miss-status.json"
    phase5_status = json.loads(phase5_status_path.read_text(encoding="utf-8"))
    phase5_status["references"].append(reference(window_index_path))
    phase5_status["content_key"] = ai_flow._canonical_digest({
        key: value for key, value in phase5_status.items() if key != "content_key"
    })
    phase5_status_path.write_text(json.dumps(phase5_status), encoding="utf-8")
    status_artifacts["phase_5"] = reference(phase5_status_path)
    qualified_demand_manifest = _qualified_demand_manifest()
    qualified_demand_manifest["evidence_id"] = "phaseD-real-performance-miss"
    qualified_demand_manifest["source_digest"] = "real-performance-miss-source"
    qualified_demand_manifest["code_approval"]["source_digest"] = (
        "real-performance-miss-source")
    qualified_demand_manifest["code_approval"]["phase_prerequisites"] = {
        "phase_0": {
            **ai_flow._FROZEN_SEARCH_CONTRACT,
            "q_variants": ["q10", "q50", "q90"],
            "work_budget_seconds": 3300, "publication_budget_seconds": 300,
            "fresh_roots": True, "tie_finalist_rules_unchanged": True,
            "timeouts_capacity_terminals_bound": True,
        },
        "phase_1": {
            "shared_kernel": "run_cost_ordered_execution",
            "only_allowed_difference": "disable_early_stop",
            "cost_ordered": {"disable_early_stop": False},
            "ordered_exhaustive": {"disable_early_stop": True},
            "shared_ledger_order_verifier_attempt_health_reconciliation_cursor": True,
        },
        "phase_2": {"status": "PASS", "required_tests": ["deterministic"]},
    }
    qualified_demand_manifest["content_key"] = ai_flow._canonical_digest({
        key: value for key, value in qualified_demand_manifest.items()
        if key != "content_key"
    })
    qualified_demand_manifest_path = validation / (
        "subhour_qualified_demand_manifest_real-performance-miss.json")
    qualified_demand_manifest_path.write_text(
        json.dumps(qualified_demand_manifest), encoding="utf-8")
    # The preliminary manifest above exists only so the Phase 3 producer can
    # be assembled before the source-bound Phase 0--2 contract is available.
    # Refresh the Phase 3 reference after replacing those bytes; otherwise the
    # end-to-end validator correctly rejects the fixture as drifted evidence.
    phase3_status_path = validation / (
        "phase_3-real-performance-miss-status.json")
    phase3_status = json.loads(
        phase3_status_path.read_text(encoding="utf-8"))
    phase3_status["references"][-1] = reference(
        qualified_demand_manifest_path)
    phase3_status["content_key"] = ai_flow._canonical_digest({
        key: value for key, value in phase3_status.items()
        if key != "content_key"
    })
    phase3_status_path.write_text(
        json.dumps(phase3_status), encoding="utf-8")
    status_artifacts["phase_3"] = reference(phase3_status_path)
    for phase in ("phase_0", "phase_1", "phase_2"):
        artifact = {
            "schema": "subhour_phase_status_v1",
            "kind": "subhour_phase_status", "phase": phase,
            "status": "PASS", "release_evidence": False,
            "evidence_id": f"{phase}-real-performance-miss-status",
            "lineage": {}, "references": [reference(qualified_demand_manifest_path)],
        }
        artifact["content_key"] = ai_flow._canonical_digest(artifact)
        status_path = validation / f"{phase}-real-performance-miss-status.json"
        status_path.write_text(json.dumps(artifact), encoding="utf-8")
        status_artifacts[phase] = reference(status_path)
    frozen = {"digest": "real-performance-miss-source"}
    checkpoint = ai_flow.build_phase_3_5_checkpoint(
        tmp_path, evidence_policy, initial_evidence_baseline, frozen)
    gate_evidence_path = validation / (
        "subhour_gate_s_evidence_real-performance-miss.json")
    gate_evidence_path.write_text(json.dumps(gate_evidence), encoding="utf-8")
    gate_registration = build_gate_s_registration(
        evidence_path=gate_evidence_path,
        evidence_sha256=hashlib.sha256(
            gate_evidence_path.read_bytes()).hexdigest(),
        evidence_id="gate-registration-performance-miss")
    gate_registration_path = validation / (
        "subhour_gate_s_registration_real-performance-miss.json")
    gate_registration_path.write_text(
        json.dumps(gate_registration), encoding="utf-8")
    gate_outcome = evaluate_gate_s(gate_registration, root=tmp_path)
    gate_outcome_path = validation / (
        "subhour_gate_s_outcome_real-performance-miss.json")
    gate_outcome_path.write_text(json.dumps(gate_outcome), encoding="utf-8")
    review = {"status": "PASS", "content_digest": "real-performance-miss-review"}
    phases = {
        "phase_0": "PASS", "phase_1": "PASS", "phase_2": "PASS",
        "phase_3": phase3_report_status, "phase_4": phase4_report_status,
        "phase_5": "PASS",
        "phase_6": "NOT_ALLOWED",
        "phase_7": ("INCONCLUSIVE" if gate_outcome["status"] == INCONCLUSIVE
                    else "PASS"),
    }
    evidence_ids = {
        phase: [f"{phase}-real-performance-miss-status",
                qualified_demand_manifest["evidence_id"]]
        for phase in ("phase_0", "phase_1", "phase_2")
    }
    evidence_ids.update({
        "phase_3": list(dict.fromkeys([
            "phase_3-real-performance-miss-status",
            outcome["evidence_id"], registration["evidence_id"],
            qualified_demand_manifest["evidence_id"]])),
        "phase_4": ["phase_4-real-performance-miss-status", profile["evidence_id"]],
        "phase_5": ["phase_5-real-performance-miss-status", profile["evidence_id"],
                    window_index["evidence_id"]],
        "phase_6": [],
        "phase_7": [gate_registration["evidence_id"], gate_evidence["evidence_id"]],
    })
    derived = ai_flow._derive_report_measurements(
        tmp_path, status_artifacts, phases, None)
    report = {
        "schema": "subhour_phase_report_v1", "kind": "subhour_phase_report",
        "release_evidence": False, "status": "COMPLETE", "phases": phases,
        "evidence_ids": evidence_ids,
        "measurements": {key: value for key, value in derived.items()
                         if key != "phase_resources"},
        "phase_resources": derived["phase_resources"],
        "status_artifacts": status_artifacts,
        "lineage": {
            "source_digest": frozen["digest"],
            "checkpoint_content_digest": checkpoint["content_digest"],
            "review_content_digest": review["content_digest"],
            "review_lineage_digest": checkpoint["lineage_digest"],
        },
        "artifacts": {
            "phase_7_evidence": reference(gate_evidence_path),
            "phase_7_registration": reference(gate_registration_path),
            "phase_7_outcome": reference(gate_outcome_path),
        },
    }
    report["content_key"] = ai_flow._canonical_digest(report)
    report_path = validation / (
        "subhour_phase_report_real-performance-miss.json")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    ai_flow.validate_post_review_terminal_artifacts(
        tmp_path, evidence_policy, frozen, checkpoint, review,
        initial_evidence_baseline)


def test_bounded_preexecution_workspace_terminal_publishes_numeric_resources(
        monkeypatch, tmp_path):
    from traffic_sim.simulation import workspace as workspace_module

    registration, registration_path = _minimal_bounded_registration(tmp_path)

    class BusyLock:
        path = tmp_path / "runs" / ".demand-workspace.lock"

        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            return False

        def holder(self):
            return {"owner": "held", "pid": 123}

    monkeypatch.setattr(workspace_module, "WorkspaceLock", BusyLock)
    monkeypatch.setattr(benchmark_module, "_verify_registration_for_execution",
                        lambda *_args, **_kwargs: None)
    outcome = benchmark_module.run_registered(
        registration, runs_root=tmp_path / "runs", data_root=tmp_path,
        workspace_root=tmp_path / "unused", registration_path=registration_path)

    assert outcome["status"] == "INCONCLUSIVE_WORKSPACE_BUSY"
    assert outcome["preflight"]["execution_started"] is False
    assert outcome["suite_consumption"] == {
        "attempts": 0, "active_seconds": 0.0,
        "disk_growth_bytes": 0, "execution_started": False,
    }
    assert outcome["resources"]["peak_rss_bytes"] == 0
    assert outcome["resources"]["disk_growth_bytes"] == 0


def test_bounded_preexecution_census_terminal_publishes_numeric_resources(
        monkeypatch, tmp_path):
    from traffic_sim.simulation import workspace as workspace_module
    from tools import process_census

    registration, registration_path = _minimal_bounded_registration(tmp_path)

    class AvailableLock:
        path = tmp_path / "runs" / ".demand-workspace.lock"

        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            return True

        def release(self):
            pass

    monkeypatch.setattr(workspace_module, "WorkspaceLock", AvailableLock)
    monkeypatch.setattr(benchmark_module, "_verify_registration_for_execution",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        process_census, "process_group_snapshot",
        lambda: (_ for _ in ()).throw(
            process_census.ProcessCensusUnavailable("no census")),
    )
    outcome = benchmark_module.run_registered(
        registration, runs_root=tmp_path / "runs", data_root=tmp_path,
        workspace_root=tmp_path / "unused", registration_path=registration_path)

    assert outcome["status"] == "INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE"
    assert outcome["preflight"]["execution_started"] is False
    assert outcome["resources"]["peak_rss_bytes"] == 0
    assert outcome["resources"]["disk_growth_bytes"] == 0
    assert outcome["suite_consumption"]["execution_started"] is False


def test_budget_terminal_has_no_decision_or_later_verification():
    candidates = [_candidate(index, float(index)) for index in range(1, 5)]
    seen = []

    def verify(candidate_id):
        seen.append(candidate_id)
        candidate = next(item for item in candidates
                         if item.candidate_id == candidate_id)
        return _evidence(candidate, _policy(minimum=1))

    result = run_cost_ordered_search(
        candidates,
        _policy(minimum=1),
        verify=verify,
        search_content_key="0123456789abcdef0123",
        provider_identity={"schema": "test"},
        max_verifications=2,
        disable_early_stop=True,
    )
    assert len(seen) == 2
    assert result.terminal_status == "INCONCLUSIVE_BUDGET_EXHAUSTED"
    assert result.selection.selected_ids == ()
    assert result.stop_proof["valid_for_ready"] is False


class _CostSource:
    cache_hits = 0
    computed_units = 2

    def identity(self):
        return {"schema": "profile-test"}

    def parent_cost(self, parent):
        records = tuple({
            "demand_variant": variant,
            "vehicles_affected": 1,
            "vehicles_no_detour": 0,
            "added_vehicle_hours": 1.0,
            "added_metres_total": 1.0,
        } for variant in ("q10", "q50", "q90"))
        return ParentCost(
            candidate_id=parent.schedule_id,
            cost=ClosureCost(
                candidate_id=parent.schedule_id,
                added_vehicle_hours=1.0,
                added_metres_total=1.0,
                vehicles_affected=1,
                vehicles_no_detour=0,
            ),
            per_variant=records,
            daily_unit_ids=(parent.schedule_id,),
        )


class _CompleteProfileSource(_CostSource):
    def timing_snapshot(self):
        return {
            "xml_parse": 0.1,
            "route_vehicle_grouping": 0.2,
            "shortest_path_detour": 0.3,
            "window_aggregation": 0.4,
            "parent_aggregation_sorting": 0.5,
        }

    def population_snapshot(self):
        return {"daily_units": 2, "daily_variant_records": 6}


def test_profile_accounts_unique_daily_units_and_never_starts_sumo(tmp_path):
    spec = SimpleNamespace(content_key="profile-search")
    parents = [SimpleNamespace(schedule_id="parent-a"),
               SimpleNamespace(schedule_id="parent-b")]
    record = profile_ledger(
        spec, parents, _CostSource(), output_root=tmp_path / "profile",
        expected_daily_units=2, expected_parents=2,
        qualified_demand_manifest=_qualified_demand_manifest())
    assert record["sumo_started"] is False
    assert record["population"] == {
        "daily_units": 2,
        "variants_per_daily_unit": 3,
        "daily_variant_records": 6,
        "parents": 2,
    }
    assert (tmp_path / "profile" / "cost-ledger.json").is_file()


def test_profile_cache_layers_reconcile_every_parent_unit_lookup(tmp_path):
    class TelemetrySource(_CostSource):
        def __init__(self):
            self._seen = False
            self._memory_hits = 0
            self._memory_misses = 0
            self._disk_hits = 0
            self._disk_misses = 0

        def parent_cost(self, parent):
            if self._seen:
                self._memory_hits += 1
            else:
                self._seen = True
                self._memory_misses += 1
                self._disk_misses += 1
            return super().parent_cost(parent)

        def cache_snapshot(self):
            return {
                "memory_cache_hits": self._memory_hits,
                "memory_cache_misses": self._memory_misses,
                "disk_cache_hits": self._disk_hits,
                "disk_cache_misses": self._disk_misses,
                "disk_cache_lookups": self._disk_hits + self._disk_misses,
            }

    source = TelemetrySource()
    record = profile_ledger(
        SimpleNamespace(content_key="profile-cache-accounting"),
        [SimpleNamespace(schedule_id="parent-a"),
         SimpleNamespace(schedule_id="parent-b")],
        source, output_root=tmp_path / "profile", expected_daily_units=1,
        expected_parents=2,
        qualified_demand_manifest=_qualified_demand_manifest())
    assert record["cache"]["lookups"] == 2
    assert record["cache"]["memory_cache_hits"] == 1
    assert record["cache"]["memory_cache_misses"] == 1
    assert record["cache"]["disk_cache_lookups"] == 1
    assert record["cache"]["disk_cache_hits"] == 0
    assert record["cache"]["disk_cache_misses"] == 1
    assert record["cache"]["accounting_consistent"] is True


def test_profile_phase5_trigger_does_not_require_rss_census(
        tmp_path, monkeypatch):
    class CensusUnavailableSampler:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            from tools.product_arm import ProcessCensusUnavailable
            raise ProcessCensusUnavailable("ps unavailable")

    monkeypatch.setattr(profile_module, "ProcessTreeRSSSampler",
                        CensusUnavailableSampler)
    record = profile_ledger(
        SimpleNamespace(content_key="complete-profile"),
        [SimpleNamespace(schedule_id="parent-a"),
         SimpleNamespace(schedule_id="parent-b")],
        _CompleteProfileSource(), output_root=tmp_path / "profile",
        expected_daily_units=2, expected_parents=2,
        sumo_start_probe=lambda: 0,
        qualified_demand_manifest=_qualified_demand_manifest())
    assert record["population_complete"] is True
    assert record["phase_timing_complete"] is True
    assert record["process_tree_rss_complete"] is False
    assert record["phase_5_decision"] == "NOT_TRIGGERED"
    assert record["phase_5_window_cost_index_needed"] is False
    assert record["status"] == "INCONCLUSIVE"


def test_profile_rejects_nonzero_inherited_sumo_launch_baseline(tmp_path):
    record = profile_ledger(
        SimpleNamespace(content_key="contaminated-profile"),
        [SimpleNamespace(schedule_id="parent-a")],
        _CostSource(), output_root=tmp_path / "profile",
        expected_daily_units=1, expected_parents=1,
        sumo_start_probe=lambda: 1,
        sumo_start_before=1,
        qualified_demand_manifest=_qualified_demand_manifest(),
    )
    assert record["sumo_started"] is True
    assert record["sumo_zero_launch_gate"] is False
    assert record["status"] == "INCONCLUSIVE"


def test_profile_publishes_observed_sumo_attempt_delta(tmp_path):
    """A contaminated cold profile retains the real launch population."""
    record = profile_ledger(
        SimpleNamespace(content_key="observed-sumo-profile"),
        [SimpleNamespace(schedule_id="parent-a")], _CostSource(),
        output_root=tmp_path / "profile", expected_daily_units=1,
        expected_parents=1, sumo_start_probe=lambda: 2,
        sumo_start_before=0,
        qualified_demand_manifest=_qualified_demand_manifest())
    assert record["sumo_attempts"] == 2
    assert record["sumo_start_observation"] == {
        "before": 0, "after": 2, "delta": 2,
        "measured": False, "error": None,
    }
    assert record["sumo_started"] is True
    assert record["sumo_zero_launch_gate"] is False
    assert record["status"] == "INCONCLUSIVE"


def test_product_arm_preserves_telemetry_when_producer_raises_after_work(
        monkeypatch, tmp_path):
    """The real arm producer returns its partial launch population to callers."""
    from tools import product_arm
    from traffic_sim.simulation import monthly_search

    class Runner:
        def cleanup(self):
            pass

        def timing_snapshot(self):
            return {
                "exact_launch_records": [{
                    "candidate_id": "candidate-1", "work_date": "2026-01-01",
                    "stage": "pilot", "variant": "q50", "seed": 1,
                    "attempt": 1, "timed_out": False, "outcome": "error",
                }],
                "exact_launch_telemetry": {},
            }

    runner = Runner()
    monkeypatch.setattr(product_arm, "build_arm",
                        lambda *_args, **_kwargs: (runner, None, None))
    monkeypatch.setattr(product_arm, "peak_rss_bytes", lambda: 100)
    monkeypatch.setattr(monthly_search, "run_monthly_search",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            ValueError("producer failed after launch")))

    result = product_arm.run_arm(
        SimpleNamespace(search_id="error-search", content_key="error-content"),
        SimpleNamespace(objective_method="closure_cost_v1"), cost_ordered=True,
        workspace_root=tmp_path / "workspace", runs_root=tmp_path / "runs",
        release_root=tmp_path / "release", daily_cost_cache=tmp_path / "cache",
        study_provenance_key="study")
    assert result["execution_error"] == {
        "type": "ValueError", "message": "producer failed after launch",
    }
    assert len(result["exact_launch_records"]) == 1
    assert result["result"]["terminal_status"] == "INCONCLUSIVE_EXECUTION_ERROR"


def test_no_detour_fixture_is_a_ledger_disqualification_before_runner():
    class InnerCostSource:
        def identity(self):
            return {"schema": "fixture"}

        def parent_cost(self, parent):
            records = tuple({
                "demand_variant": variant,
                "vehicles_affected": 1,
                "vehicles_no_detour": 0,
                "added_vehicle_hours": 1.0,
                "added_metres_total": 1.0,
            } for variant in ("q10", "q50", "q90"))
            return ParentCost(
                candidate_id=parent.schedule_id,
                cost=ClosureCost(parent.schedule_id, 1.0, 1.0, 1, 0),
                per_variant=records,
                daily_unit_ids=("unit-a",),
            )

    state = {}
    controls = {"no_detour": {"candidate_id": "target"}}
    source = _FixtureCostSource(InnerCostSource(), controls, state)
    priced = source.parent_cost(SimpleNamespace(schedule_id="target"))

    assert priced.cost.vehicles_no_detour == 1
    assert {item["vehicles_no_detour"] for item in priced.per_variant} == {1}
    assert "no_detour" in state["applied"]
    assert state["runner_events"][0]["stage"] == "cost_ledger"

    class InnerRunner:
        def run_candidate(self, *args, **kwargs):
            raise AssertionError("SUMO runner must not be called")

    runner = _FixtureRunner(InnerRunner(), controls, state)
    with pytest.raises(AssertionError, match="must be rejected"):
        runner.run_candidate(
            SimpleNamespace(schedule_id="target"),
            target_repetitions={"q10": 1, "q50": 1, "q90": 1},
            existing=None, stage="pilot")


def test_phase3_selection_tuple_is_exactly_the_registered_five_fields():
    item = {
        "spec": {
            "source": "forecast",
            "directed_edges": ["edge"],
            "permitted_daily_band": {
                "earliest_start": "06:00", "latest_end": "14:00",
            },
        },
        "work_dates": ["2027-03-01"],
        "search_content_key": "content",
        "search_id": "must-not-participate",
    }
    assert outcome_free_tuple(item) == (
        "forecast:2027-03", "edge", "2027-03-01", "06:00-14:00", "content")
    assert len(outcome_free_tuple(item)) == 5


def _gate_decision(finalists, winner, failures=()):
    return {
        "hard_failures": list(failures), "viable_set": ["a", "b"],
        "finalists": list(finalists), "winner": winner,
        "capacity_exceeded": False,
    }


def test_gate_s_has_strict_four_result_policy():
    same = {case: _gate_decision(["a"], "a") for case in ("q10", "q50", "q90")}
    assert classify(same)["status"] == INCONCLUSIVE
    regret = {case: 0.0 for case in ("q10", "q50", "q90")}
    failures = {case: {"all": [], "q50_recalled": []}
                for case in ("q10", "q50", "q90")}
    stressed = dict(same)
    stressed["q90"] = _gate_decision(["b"], "b")
    # Caller-supplied regret/failure populations are no longer trusted by the
    # public classifier; evaluate() derives them from bound evidence bytes.
    assert classify(stressed, decision_regret={**regret, "q90": 1.0},
                    variant_unique_failures=failures)["status"] == INCONCLUSIVE
    assert classify(same, decision_regret=regret,
                    variant_unique_failures=failures,
                    q50_failure_recall=1.0)["status"] == INCONCLUSIVE
    assert classify({"q50": same["q50"]})["status"] == INCONCLUSIVE


def test_gate_s_uses_q50_policy_regret_and_identity_qualified_failures():
    decisions = {
            variant: {
                "hard_failures": [], "viable_set": ["a", "b"],
                "finalists": ["a"], "winner": ["a"],
                "capacity_exceeded": False,
        }
        for variant in ("q10", "q50", "q90")
    }
    decisions["q90"] = {
        **decisions["q90"], "finalists": ["b"], "winner": ["b"],
    }
    costs = {
        variant: {
            "a": {"added_vehicle_hours": 3.0,
                   "added_metres_total": 1.0, "vehicles_affected": 1,
                   "vehicles_no_detour": 0, "feasible": True},
            "b": {"added_vehicle_hours": 1.0,
                   "added_metres_total": 1.0, "vehicles_affected": 1,
                   "vehicles_no_detour": 0, "feasible": True},
        }
        for variant in ("q10", "q50", "q90")
    }
    evidence = {
        "variants": {
            variant: {
                "decision": decisions[variant],
                "candidate_costs": costs[variant],
                "decision_relevant_failures": (
                    ["case:a:shared"] if variant != "q90"
                    else ["case:a:shared", "case:b:stress-only"]),
            }
            for variant in ("q10", "q50", "q90")
        }
    }
    bound_decisions, regret, failures = _derive_gate_inputs(evidence)
    # Regret is a lexicographic decision indicator, not a vehicle-hours
    # difference that can hide secondary-field changes.
    assert regret["q90"] == pytest.approx(1.0)
    assert failures["q90"]["q50_recalled"] == ["case:a:shared"]
    result = classify(bound_decisions, health={"status": "PASS"},
                      decision_regret=regret,
                      variant_unique_failures=failures,
                      _trusted_bound=True)
    assert result["status"] == ROBUST_THREE_VARIANT


def test_gate_s_preserves_variant_only_hard_failures(monkeypatch):
    candidate = "candidate-q90-failure"
    monkeypatch.setattr(
        benchmark_module.base, "_raw_cost_ledger",
        lambda _arm: {"costs": [{
            "candidate_id": candidate,
            "per_variant": [{
                "demand_variant": variant,
                "vehicles_affected": 1,
                "vehicles_no_detour": 0,
                "added_vehicle_hours": 1.0,
                "added_metres_total": 1.0,
            } for variant in ("q10", "q50", "q90")],
        }]} )
    monkeypatch.setattr(
        benchmark_module.base, "_published_search_policy",
        lambda _arm: SimpleNamespace(pilot=SimpleNamespace(minimum_finalists=1)))
    monkeypatch.setattr(
        benchmark_module.base, "_candidate_semantic_evidence",
        lambda _arm: {candidate: {"pilot": {
            "hard_failures": ["sumo_execution_failure:q90:seed-1"],
            "disruption": [],
        }}})

    decisions = benchmark_module._variant_decisions({"workspace": "unused"})

    assert decisions["q10"]["decision"]["hard_failures"] == []
    assert decisions["q50"]["decision"]["hard_failures"] == []
    assert decisions["q90"]["decision"]["hard_failures"] == [
        "sumo_execution_failure:q90:seed-1"]
    assert decisions["q90"]["decision"]["viable_set"] == []


def test_gate_s_uses_secondary_costs_and_registered_finalist_band(monkeypatch):
    candidates = ("low-distance", "high-distance", "outside-band")
    monkeypatch.setattr(
        benchmark_module.base, "_raw_cost_ledger",
        lambda _arm: {"costs": [{
            "candidate_id": candidate,
            "per_variant": [{
                "demand_variant": variant,
                "vehicles_affected": 1,
                "vehicles_no_detour": 0,
                "added_vehicle_hours": (1.0 if candidate != "outside-band" else 2.0),
                "added_metres_total": (1.0 if candidate == "low-distance" else 2.0),
            } for variant in ("q10", "q50", "q90")],
        } for candidate in candidates]},
    )
    monkeypatch.setattr(
        benchmark_module.base, "_published_search_policy",
        lambda _arm: SimpleNamespace(
            pilot=SimpleNamespace(minimum_finalists=1, maximum_finalists=3),
            finalist=SimpleNamespace(practical_equivalence_vehicle_hours=0.0),
        ),
    )
    monkeypatch.setattr(
        benchmark_module.base, "_candidate_semantic_evidence",
        lambda _arm: {candidate: {"pilot": {
            "hard_failures": [], "disruption": [],
        }} for candidate in candidates},
    )

    decisions = benchmark_module._variant_decisions({"workspace": "unused"})
    q50 = decisions["q50"]["decision"]
    assert q50["winner"] == "low-distance"
    assert q50["finalists"] == ["low-distance", "high-distance"]
    assert q50["finalists"] != ["low-distance"]
    assert q50["capacity_exceeded"] is False


def test_gate_s_capacity_crossing_is_not_q50_only(monkeypatch):
    candidate_ids = ("a", "b")
    monkeypatch.setattr(
        benchmark_module.base, "_raw_cost_ledger",
        lambda _arm: {"costs": [{
            "candidate_id": candidate,
            "per_variant": [{
                "demand_variant": variant,
                "vehicles_affected": 1,
                "vehicles_no_detour": 0,
                "added_vehicle_hours": 1.0,
                "added_metres_total": 1.0,
            } for variant in ("q10", "q50", "q90")],
        } for candidate in candidate_ids]},
    )
    monkeypatch.setattr(
        benchmark_module.base, "_published_search_policy",
        lambda _arm: SimpleNamespace(
            pilot=SimpleNamespace(minimum_finalists=1, maximum_finalists=1),
            finalist=SimpleNamespace(practical_equivalence_vehicle_hours=0.0),
        ),
    )
    monkeypatch.setattr(
        benchmark_module.base, "_candidate_semantic_evidence",
        lambda _arm: {candidate: {"pilot": {
            "hard_failures": [], "disruption": [],
        }} for candidate in candidate_ids},
    )
    decisions = benchmark_module._variant_decisions({"workspace": "unused"})
    assert all(item["decision"]["capacity_exceeded"] for item in decisions.values())
    failures = {variant: {"all": [], "q50_recalled": []}
                for variant in ("q10", "q50", "q90")}
    assert classify(
        {variant: item["decision"] for variant, item in decisions.items()},
        health={"status": "PASS"},
        decision_regret={variant: 0.0 for variant in decisions},
        variant_unique_failures=failures,
        _trusted_bound=True,
    )["status"] == INCONCLUSIVE


def test_gate_s_aggregate_preserves_capacity_from_real_case_decisions(monkeypatch):
    decision = {
        "hard_failures": [], "viable_set": ["candidate"],
        "finalists": ["candidate"], "winner": "candidate",
        "capacity_exceeded": True,
    }
    variant = {
        "decision": decision, "decision_relevant_failures": [],
        "winner_cost": 1.0, "candidate_costs": {
            "candidate": {
                "added_vehicle_hours": 1.0, "added_metres_total": 1.0,
                "vehicles_affected": 1, "vehicles_no_detour": 0,
                "feasible": True,
            }
        },
    }
    monkeypatch.setattr(
        benchmark_module, "_variant_decisions",
        lambda _arm: {name: variant for name in ("q10", "q50", "q90")},
    )
    gate = benchmark_module._populate_gate_s([{
        "case_id": "case-capacity", "gates_passed": True,
        "decision_population_complete": True,
        "arms": {"cost_ordered": {}, "ordered_exhaustive": {}},
    }])
    assert all(item["decision"]["capacity_exceeded"]
               for item in gate["variants"].values())


def test_phase6_runtime_telemetry_does_not_substitute_rusage_for_process_tree():
    class Runner:
        def timing_snapshot(self):
            return {"process_tree_peak_rss_bytes": 999999}

    telemetry = _phase6_runtime_telemetry(
        Runner(), Path("/tmp/does-not-exist"), 0)
    assert telemetry["peak_rss_bytes"] is None
    assert telemetry["process_tree_rss_complete"] is False


def test_ready_publication_receipt_fails_closed_after_destination_commit(
        tmp_path, monkeypatch):
    import run_monthly_closure_search as monthly_cli

    now = [0.0]
    controller = ActiveTimeController(
        hard_stop_s=10.0, publication_reserve_s=2.0,
        clock=lambda: now[0])
    original_link = monthly_cli.os.link
    calls = []

    def delayed_destination_link(source, destination):
        calls.append(destination)
        if len(calls) == 2:
            now[0] = 13.0
        return original_link(source, destination)

    monkeypatch.setattr(monthly_cli.os, "link", delayed_destination_link)
    with pytest.raises(ActiveBudgetExceeded, match="crossed"):
        monthly_cli.write_append_only_json(
            tmp_path / "ready.json", {"status": "READY"},
            controller=controller)
    receipt = json.loads(
        (tmp_path / ".ready.json.receipt.json").read_text(encoding="utf-8"))
    assert (tmp_path / "ready.json").exists()
    assert receipt["within_deadline"] is False
    assert receipt["committed_elapsed_s"] == 13.0
    assert receipt["authoritative_status"] == "INCONCLUSIVE_BUDGET_EXHAUSTED"


@pytest.mark.parametrize("terminal", [
    "pass", "census", "held", "pre_deadline", "post_deadline", "recovered",
])
def test_real_phase6_cli_terminals_publish_receipt_bound_producers(
        tmp_path, monkeypatch, terminal):
    """Exercise ``main`` through each bounded Phase 6 publication boundary.

    The CLI owns the terminal status and telemetry in every case.  Fixtures
    only replace the expensive search, process census and clock with small
    deterministic seams; they do not supply a terminal payload or telemetry
    to ``phase6_outcome``.
    """
    import run_monthly_closure_search as monthly_cli
    import traffic_sim.simulation.monthly_search as monthly_search
    from tools import process_census, product_arm

    registration = {
        "schema": "subhour_full_month_registration_v1",
        "kind": "subhour_full_month_registration",
        "release_evidence": False,
        "evidence_id": f"phase6-cli-{terminal}",
        "activation": "controlled-test",
    }
    validation = tmp_path / "validation"
    validation.mkdir()
    phase6_root = tmp_path / "phase6"
    phase6_root.mkdir()
    initial_evidence_baseline = ai_flow.evidence_inventory(
        tmp_path, ai_flow.load_config(
            Path(__file__).resolve().parents[1]
            / ".ai-flow" / "config.complete-subhour.toml",
            Path(__file__).resolve().parents[1],
        ).evidence_policy.registration_globs)
    registration_path = validation / f"subhour_full_month_registration_{terminal}.json"
    outcome_path = validation / f"subhour_full_month_outcome_{terminal}.json"
    now = [0.0]

    class ControlledController(ActiveTimeController):
        def __init__(self, **kwargs):
            super().__init__(clock=lambda: now[0], **kwargs)

        def checkpoint(self, label, *, completed=None, total=None,
                      publication=False):
            if terminal == "pre_deadline" and publication:
                now[0] = 3601.0
            return super().checkpoint(
                label, completed=completed, total=total,
                publication=publication)

    class Runner(FakeRunner):
        def __init__(self):
            schedules = generate_closure_schedules(spec)
            super().__init__(prices={item.schedule_id: float(index + 1)
                                     for index, item in enumerate(schedules)})

    class Resolver:
        def __init__(self, *_args, **_kwargs):
            self.runner = Runner()

        def __getattr__(self, name):
            return getattr(self.runner, name)

    class Sampler:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            return self

        def stop(self):
            if terminal == "census":
                raise RuntimeError("controlled process census loss")
            return 123

    class AvailableLock:
        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, **_kwargs):
            return terminal != "held"

        def holder(self):
            return {"owner": "controlled-holder", "pid": 7}

        def release(self):
            pass

    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy
    spec = _execution_spec()
    policy = MonthlySearchPolicy.from_dict(json.loads(
        Path("validation/monthly_search_policy_v3.json").read_text()))
    policy = replace(policy, status="golden_frozen")

    # Produce the prerequisite envelopes through the same phase-status and
    # registration builders used by the controller.  The referenced bounded
    # and cold-profile payloads are controlled producer inputs, kept outside
    # the configured evidence directory so this test does not alter its
    # append-only inventory.
    prerequisite_root = tmp_path / "phase6-prerequisites"
    prerequisite_root.mkdir()
    lineage_files = {}
    for name, payload in {
        "source.json": {"source": "controlled"},
        "input.json": {"input": "controlled"},
        "runtime.json": {"runtime": "controlled"},
        "policy.json": {"policy": "controlled"},
    }.items():
        path = prerequisite_root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        lineage_files[name] = path
    lineage = {
        "source_digests": {
            "source.json": hashlib.sha256(
                lineage_files["source.json"].read_bytes()).hexdigest()},
        "input_digests": {
            "input.json": hashlib.sha256(
                lineage_files["input.json"].read_bytes()).hexdigest()},
        "runtime_digest": hashlib.sha256(
            lineage_files["runtime.json"].read_bytes()).hexdigest(),
        "policy_digest": hashlib.sha256(
            lineage_files["policy.json"].read_bytes()).hexdigest(),
    }
    common_references = [
        {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in lineage_files.values()
    ]
    qualified_manifest = _qualified_demand_manifest()
    qualified_path = prerequisite_root / "qualified-demand.json"
    qualified_path.write_text(json.dumps(qualified_manifest), encoding="utf-8")

    def qualified_reference():
        return {
            "path": str(qualified_path.resolve()),
            "sha256": hashlib.sha256(qualified_path.read_bytes()).hexdigest(),
            "content_key": qualified_manifest["content_key"],
            "evidence_id": qualified_manifest["evidence_id"],
        }

    phase3_registration = {
        "schema": "subhour_cost_ordered_bounded_registration_v1",
        "evidence_id": f"phase3-{terminal}",
        "selection": {"selected_ids": ["phase3-search"]},
        "selected_cases": [{
            "case_id": "controlled-case",
            "search_content_key": "phase3-search",
        }],
        "caps": {
            "peak_rss_bytes": 100,
            "active_seconds": 100.0,
            "disk_growth_bytes": 100,
        },
        "qualified_demand_manifest": qualified_reference(),
    }
    phase3_registration["content_key"] = benchmark_module._key({
        key: value for key, value in phase3_registration.items()
        if key not in {"content_key", "registered_at"}
    })
    phase3_registration_path = prerequisite_root / "phase3-registration.json"
    phase3_registration_path.write_text(
        json.dumps(phase3_registration), encoding="utf-8")
    phase3_outcome = benchmark_module._with_content_key({
        "schema": "subhour_cost_ordered_bounded_outcome_v1",
        "kind": "subhour_bounded_sumo_outcome",
        "release_evidence": False, "evidence_id": f"phase3-{terminal}",
        "status": "PASS",
        "registration": {
            **qualified_reference(),
            "path": str(phase3_registration_path.resolve()),
            "sha256": hashlib.sha256(
                phase3_registration_path.read_bytes()).hexdigest(),
            "content_key": phase3_registration["content_key"],
            "evidence_id": phase3_registration["evidence_id"],
        },
        "selection": {"selected_ids": ["phase3-search"]},
        "decision_population_complete": True,
        "case_results": [{
            "case_id": "controlled-case",
            "search_content_key": "phase3-search",
            "gates_passed": True,
            "decision_population_complete": True,
            "comparison": _valid_phase3_comparison(),
        }],
        "gate_s": {
            "population_complete": True,
            "variants": {variant: {} for variant in ("q10", "q50", "q90")},
        },
    })
    phase3_path = prerequisite_root / "phase3-outcome.json"
    phase3_path.write_text(json.dumps(phase3_outcome), encoding="utf-8")
    phase4_profile = {
        "schema": "monthly_cost_ledger_profile_v1",
        "kind": "monthly_cost_ledger_profile", "release_evidence": False,
        "evidence_id": f"phase4-{terminal}", "status": "PASS",
        "population_complete": True, "phase_timing_complete": True,
        "sumo_zero_launch_gate": True,
        "population": {"daily_units": 1950, "daily_variant_records": 5850,
                       "parents": 1690},
        "phase_5_decision": "NOT_TRIGGERED",
    }
    phase4_path = prerequisite_root / "phase4-profile.json"
    phase4_path.write_text(json.dumps(phase4_profile), encoding="utf-8")

    def reference(path):
        return {"path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    phase3_refs = [*common_references, reference(qualified_path),
                   reference(phase3_path)]
    phase4_refs = [*common_references, reference(phase4_path)]
    prerequisites = {}
    for phase in ("phase_0", "phase_1", "phase_2", "phase_3", "phase_4"):
        refs = (phase3_refs if phase == "phase_3" else phase4_refs
                if phase == "phase_4" else
                [*common_references, reference(qualified_path)])
        artifact = build_phase_status_artifact(
            phase=phase, status="PASS", evidence_id=f"{phase}-{terminal}",
            lineage=lineage, references=refs)
        path = prerequisite_root / f"{phase}.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        prerequisites[phase] = reference(path)
    phase5_artifact = build_phase_status_artifact(
        phase="phase_5", status="NOT_TRIGGERED", evidence_id=f"phase5-{terminal}",
        lineage=lineage, references=phase4_refs)
    phase5_path = prerequisite_root / "phase_5.json"
    phase5_path.write_text(json.dumps(phase5_artifact), encoding="utf-8")
    prerequisites["phase_5"] = reference(phase5_path)
    checkpoint_body = {
        "schema_version": 1, "kind": "ai_flow_phase_3_5_checkpoint",
        "status": "PENDING_INDEPENDENT_REVIEW",
        "source_digest": "s" * 64,
        "artifact_inventory": {}, "artifact_inventory_digest": "a" * 64,
        "lineage_digest": "l" * 64, "phase6_registration_globs": [],
    }
    checkpoint = {**checkpoint_body,
                  "content_digest": ai_flow._canonical_digest(checkpoint_body)}
    checkpoint_path = prerequisite_root / "checkpoint.json"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    review_response = {"status": "APPROVED", "summary": "controlled review",
                       "findings": [], "blocked_reason": ""}
    response_path = prerequisite_root / "review-response.json"
    response_path.write_text(json.dumps(review_response), encoding="utf-8")
    review_artifact = ai_flow.build_phase_3_5_review_artifact(
        checkpoint, review_response, f"phase-review-{terminal}")
    review_path = prerequisite_root / "review.json"
    review_path.write_text(json.dumps(review_artifact), encoding="utf-8")
    prerequisites["review"] = {
        "path": str(review_path),
        "sha256": hashlib.sha256(review_path.read_bytes()).hexdigest(),
        "status": "PASS",
        "checkpoint": reference(checkpoint_path),
        "review_response": reference(response_path),
    }
    registration = build_phase6_registration(
        spec, policy, evidence_id=f"phase6-cli-{terminal}",
        prerequisites=prerequisites, output_root=phase6_root,
        workspace_root=phase6_root)
    registration_path.write_text(json.dumps(registration), encoding="utf-8")
    args = SimpleNamespace(
        spec=tmp_path / "spec.json", policy=tmp_path / "policy.json",
        baseline_trip_duration_p99_s=1, bounded_exhaustive_cap=2,
        independent_exhaustive_candidate_cap=2,
        independent_exhaustive_daily_cap=2, seed_workers=1,
        daily_workers=1, max_active_sumo_slots=1, warm_execution=False,
        daily_unit_budget=None, daily_unit_total_cap=2,
        window_cost_index=None, screening_mode="independent-cost-ordered-exact",
        root=phase6_root, baseline_cache=None, demand_archive=None,
        demand_runs_root=tmp_path / "demand-runs",
        demand_release_root=tmp_path / "demand-release",
        no_build_missing_demand=False, daily_result_cache=tmp_path / "daily",
        daily_cost_cache=tmp_path / "costs", workspace_wait_s=0.0,
        phase6_registration=registration_path, phase6_outcome=outcome_path,
    )
    args.policy.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(monthly_cli, "parse_args", lambda: args)
    monkeypatch.setattr(monthly_cli, "load_closure_search_spec",
                        lambda _path: spec)
    monkeypatch.setattr(monthly_cli.MonthlySearchPolicy, "from_dict",
                        staticmethod(lambda _value: policy))
    monkeypatch.setattr(
        monthly_cli, "_IndependentExhaustiveScreenBuilder",
        lambda **_kwargs: _screen_builder(
            spec, [item.schedule_id for item in generate_closure_schedules(spec)]),
    )
    # Only the NETWORK digest is stubbed. This used to be a blanket
    # `lambda _path: "network"`, written when `sha256_file` had exactly one
    # caller in this module. The Phase D work gave the same name two more
    # jobs -- hashing the bound Phase 3 registration and the qualified-demand
    # manifest -- so a path-blind stub silently answered "network" for those
    # too and disabled the very byte-binding checks this test exists to
    # exercise. Fall through to the real digest for every other path.
    _real_sha256_file = monthly_cli.sha256_file
    monkeypatch.setattr(
        monthly_cli, "sha256_file",
        lambda path: ("network" if Path(path).name == "net.net.xml"
                      else _real_sha256_file(path)))
    monkeypatch.setattr(monthly_cli, "_independent_exhaustive_preflight",
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        monthly_search, "load_passing_heldout_gate",
        lambda: {
            "proxy_version": "independent_daily_exhaustive_sumo_v1",
            "interday_policy": "independent_daily_reset_v1",
            "gate_status": "pass",
        },
    )
    monkeypatch.setattr(monthly_cli, "approved_seed_workers", lambda: 8)
    monkeypatch.setattr(monthly_cli, "ActiveTimeController",
                        ControlledController)
    monkeypatch.setattr(monthly_cli, "WorkspaceLock", AvailableLock)
    monkeypatch.setattr(monthly_cli, "_start_macos_keep_awake", lambda: None)
    monkeypatch.setattr(monthly_cli, "_stop_macos_keep_awake", lambda _p: None)
    monkeypatch.setattr(monthly_cli, "_simulation_backends",
                        lambda: (object, Resolver, lambda: None))
    monkeypatch.setattr(monthly_cli, "IndependentDailyRunner",
                        lambda _spec, *, daily_runner, cache_root: daily_runner)
    monkeypatch.setattr(
        monthly_cli, "_cost_source_for",
        lambda _spec, _runner, _args, **_kwargs: FakeCostSource(
            {item.schedule_id: float(index + 1) for index, item in enumerate(
                generate_closure_schedules(_spec))}),
    )
    monkeypatch.setattr(process_census, "process_group_snapshot",
                        lambda: [(1, 1, 1)])
    monkeypatch.setattr(product_arm, "ProcessTreeRSSSampler", Sampler)

    if terminal == "post_deadline":
        original_link = monthly_cli.os.link
        calls = []

        def delayed_link(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                now[0] = 3601.0
            return original_link(source, destination)

        monkeypatch.setattr(monthly_cli.os, "link", delayed_link)

    monthly_cli.main()
    assert outcome_path.is_file()
    outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
    receipt_path = outcome_path.with_name("." + outcome_path.name + ".receipt.json")
    if terminal == "recovered":
        # Reproduce the exact on-disk state a process death between the two
        # commit points leaves behind: the immutable outcome is durable and
        # its receipt was never written.  The kill-point regressions below
        # prove a real SIGKILL produces this state; here the complete CLI
        # terminal is carried through recovery into the report validator.
        committed_bytes = outcome_path.read_bytes()
        receipt_path.unlink()
        with pytest.raises(FileExistsError, match="recovery receipt"):
            monthly_cli.write_append_only_json(outcome_path, outcome)
        assert outcome_path.read_bytes() == committed_bytes
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["path"] == str(outcome_path.resolve())
    assert receipt["payload_sha256"] == hashlib.sha256(
        outcome_path.read_bytes()).hexdigest()
    if terminal == "pass":
        assert outcome["status"] == "READY"
        assert receipt["authoritative_status"] == "READY"
    elif terminal == "census":
        assert outcome["status"] == "INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE"
        assert outcome["telemetry"]["process_tree_rss_complete"] is False
        assert outcome["telemetry"]["peak_rss_bytes"] is None
    elif terminal == "held":
        assert outcome["status"] == "INCONCLUSIVE_BUDGET_EXHAUSTED"
        assert outcome["execution_started"] is False
        assert outcome["telemetry"]["sumo_attempts"] == 0
    elif terminal == "pre_deadline":
        assert outcome["status"] == "INCONCLUSIVE_BUDGET_EXHAUSTED"
        assert receipt["within_deadline"] is False
        assert receipt["authoritative_status"] == "INCONCLUSIVE_BUDGET_EXHAUSTED"
    elif terminal == "recovered":
        # The producer really did reach READY; only the proof that it
        # committed inside the reserve was lost with the process.
        assert outcome["status"] == "READY"
        assert receipt["recovered"] is True
        assert receipt["committed_elapsed_s"] is None
        assert receipt["within_deadline"] is False
        assert receipt["authoritative_status"] == (
            "INCONCLUSIVE_PUBLICATION_UNVERIFIED")
    else:
        assert outcome["status"] == "READY"
        assert receipt["within_deadline"] is False
        assert receipt["authoritative_status"] == "INCONCLUSIVE_BUDGET_EXHAUSTED"

    if terminal in {"pass", "census", "held", "pre_deadline", "post_deadline",
                    "recovered"}:
        # Feed the exact CLI-produced census-loss outcome through the complete
        # controller validator.  The supporting Phase 3--5 records are small
        # producer-shaped fixtures; the Phase 6 bytes and receipt above are
        # never reconstructed by the test.
        # This test exercises the Phase 6 CLI's own terminal publication, so
        # the run it validates must be one that PERMITS Phase 6. The tracked
        # sub-hour config deliberately sets `allow_phase6 = false` because the
        # current campaign stops at the Phase 3-5 checkpoint; inheriting that
        # ceiling here would make the controller refuse every terminal before
        # the CLI logic under test was reached. The ceiling itself is covered
        # separately by
        # `test_controller_ceiling_applies_when_producer_omits_authorization`.
        policy = replace(
            ai_flow.load_config(
                Path(__file__).resolve().parents[1]
                / ".ai-flow" / "config.complete-subhour.toml",
                Path(__file__).resolve().parents[1],
            ).evidence_policy,
            allow_phase6=True, allow_gate_s=True,
        )
        checkpoint_baseline = ai_flow.evidence_inventory(
            tmp_path, policy.registration_globs)
        phase3_registration = {
            "schema": "subhour_cost_ordered_bounded_registration_v1",
            "evidence_id": "phase3-validator-fixture",
            "selection": {"selected_ids": ["phase3-search"]},
            "selected_cases": [{
                "case_id": "case-1", "search_content_key": "phase3-search",
            }],
            # Gate S eligibility RE-DERIVES the decision population from these
            # bytes instead of trusting the outcome's own flag, so an eligible
            # fixture has to carry the caps the rule measures against.
            "caps": {
                "peak_rss_bytes": 8 * 1024**3,
                "active_seconds": 3600.0,
                "disk_growth_bytes": 8 * 1024**3,
            },
        }
        phase3_registration["content_key"] = ai_flow._canonical_digest(
            phase3_registration)
        phase3_registration_path = validation / (
            "subhour_bounded_sumo_registration_validator.json")
        phase3_registration_path.write_text(
            json.dumps(phase3_registration), encoding="utf-8")
        phase3_registration_ref = {
            "evidence_id": phase3_registration["evidence_id"],
            "content_key": phase3_registration["content_key"],
            "path": str(phase3_registration_path.resolve()),
            "sha256": hashlib.sha256(
                phase3_registration_path.read_bytes()).hexdigest(),
        }
        phase3 = {
            "schema": "subhour_cost_ordered_bounded_outcome_v1",
            "kind": "subhour_bounded_sumo_outcome",
            "release_evidence": False,
            "evidence_id": phase3_registration["evidence_id"],
            "registration": phase3_registration_ref,
            "status": "PASS",
            "selection": {"selected_ids": ["phase3-search"]},
            "decision_population_complete": True,
            "case_results": [{
                "case_id": "case-1", "search_content_key": "phase3-search",
                "decision_population_complete": True, "gates_passed": True,
                "comparison": _valid_phase3_comparison(),
            }],
            "gate_s": {
                "population_complete": True,
                "variants": {
                    variant: {
                        "decision": {
                            "hard_failures": [], "viable_set": ["case-1"],
                            "finalists": ["case-1"], "winner": "case-1",
                            "capacity_exceeded": False,
                        },
                        "decision_relevant_failures": [],
                        "winner_cost": 1.0, "reference_winner_cost": 1.0,
                        "candidate_costs": {"case-1": {
                            "added_vehicle_hours": 1.0,
                            "added_metres_total": 1.0,
                            "vehicles_affected": 1,
                            "vehicles_no_detour": 0,
                            "feasible": True,
                        }},
                    }
                    for variant in ("q10", "q50", "q90")
                },
            },
            "suite_consumption": {
                "attempts": 0, "active_seconds": 0.0,
                "disk_growth_bytes": 0, "execution_started": False,
            },
            "resources": {
                "peak_rss_bytes": 0, "disk_growth_bytes": 0,
                "disk_roots": [str(tmp_path / "phase3")],
            },
        }
        phase3["content_key"] = ai_flow._canonical_digest(phase3)
        phase3_path = validation / "subhour_bounded_sumo_outcome_validator.json"
        phase3_path.write_text(json.dumps(phase3), encoding="utf-8")
        profile = {
            "schema": "monthly_cost_ledger_profile_v1",
            "kind": "monthly_cost_ledger_profile",
            "release_evidence": False,
            "evidence_id": "phase4-validator-fixture",
            "status": "PASS", "wall_time_s": 3.0,
            "sumo_attempts": 0, "sumo_started": False,
            "sumo_start_observation": {"before": 0, "after": 0, "delta": 0},
            "peak_rss_bytes": 12, "disk_growth_bytes": 4,
            "fresh_roots": {"output": str(tmp_path / "phase4")},
            "population_complete": True, "phase_timing_complete": True,
            "sumo_zero_launch_gate": True,
            "population": {"daily_units": 1950,
                           "daily_variant_records": 5850,
                           "parents": 1690},
            "phase_5_decision": "TRIGGERED",
        }
        profile["content_key"] = ai_flow._canonical_digest(profile)
        profile_path = validation / "monthly_cost_ledger_profile_subhour-validator.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        window_index = {
            "schema": "subhour_phase5_window_cost_index_evidence_v1",
            "kind": "subhour_phase5_window_cost_index",
            "release_evidence": False,
            "evidence_id": "phase5-validator-fixture",
            "status": "PASS",
            "oracle": {"field_identical": True, "oracle_complete": True},
        }
        window_index["content_key"] = ai_flow._canonical_digest(window_index)
        window_index_path = validation / "window_cost_index_subhour-validator.json"
        window_index_path.write_text(json.dumps(window_index), encoding="utf-8")

        def reference(path):
            value = json.loads(path.read_text(encoding="utf-8"))
            return {"path": str(path.resolve()),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "content_key": value["content_key"]}

        qualified_demand_manifest = {
            "schema": "subhour_qualified_demand_manifest_v1",
            "kind": "subhour_qualified_demand_manifest", "release_evidence": False,
            "evidence_id": "phase-d-validator-fixture",
            "adopted_catalog_keys": {
                "weekday": "46f619b93152b0f2e21cd37a1c5e4991",
                "weekend": "fd92cb5c2cccf9112c4143c4eb6355ff",
            },
            "demand_variants": ["q10", "q50", "q90"],
        }
        qualified_demand_manifest["content_key"] = ai_flow._canonical_digest(
            qualified_demand_manifest)
        qualified_demand_manifest_path = validation / (
            "subhour_qualified_demand_manifest_validator.json")
        qualified_demand_manifest_path.write_text(
            json.dumps(qualified_demand_manifest), encoding="utf-8")

        status_artifacts = {}
        for phase, producer_path, producer_id, status in (
            ("phase_3", phase3_path, phase3["evidence_id"], "PASS"),
            ("phase_4", profile_path, profile["evidence_id"], "PASS"),
            ("phase_5", profile_path, profile["evidence_id"], "PASS"),
        ):
            artifact = {
                "schema": "subhour_phase_status_v1",
                "kind": "subhour_phase_status", "phase": phase,
                "status": status, "release_evidence": False,
                "evidence_id": f"{phase}-validator-status",
            "lineage": {}, "references": [
                reference(producer_path),
                *([reference(phase3_registration_path),
                   reference(qualified_demand_manifest_path)]
                  if phase == "phase_3" else []),
            ],
            }
            artifact["content_key"] = ai_flow._canonical_digest(artifact)
            status_path = validation / f"{phase}-validator-status.json"
            status_path.write_text(json.dumps(artifact), encoding="utf-8")
            status_artifacts[phase] = reference(status_path)
        phase5_status_path = validation / "phase_5-validator-status.json"
        phase5_status = json.loads(
            phase5_status_path.read_text(encoding="utf-8"))
        phase5_status["references"].append(reference(window_index_path))
        phase5_status["content_key"] = ai_flow._canonical_digest({
            key: value for key, value in phase5_status.items()
            if key != "content_key"
        })
        phase5_status_path.write_text(
            json.dumps(phase5_status), encoding="utf-8")
        status_artifacts["phase_5"] = reference(phase5_status_path)
        qualified_demand_manifest = _qualified_demand_manifest()
        qualified_demand_manifest["evidence_id"] = "phaseD-validator-fixture"
        qualified_demand_manifest["source_digest"] = "validator-source"
        qualified_demand_manifest["code_approval"]["source_digest"] = (
            "validator-source")
        qualified_demand_manifest["code_approval"]["phase_prerequisites"] = {
            "phase_0": {
                **ai_flow._FROZEN_SEARCH_CONTRACT,
                "q_variants": ["q10", "q50", "q90"],
                "work_budget_seconds": 3300, "publication_budget_seconds": 300,
                "fresh_roots": True, "tie_finalist_rules_unchanged": True,
                "timeouts_capacity_terminals_bound": True,
            },
            "phase_1": {
                "shared_kernel": "run_cost_ordered_execution",
                "only_allowed_difference": "disable_early_stop",
                "cost_ordered": {"disable_early_stop": False},
                "ordered_exhaustive": {"disable_early_stop": True},
                "shared_ledger_order_verifier_attempt_health_reconciliation_cursor": True,
            },
            "phase_2": {"status": "PASS", "required_tests": ["deterministic"]},
        }
        qualified_demand_manifest["content_key"] = ai_flow._canonical_digest({
            key: value for key, value in qualified_demand_manifest.items()
            if key != "content_key"
        })
        qualified_demand_manifest_path = validation / (
            "subhour_qualified_demand_manifest_validator.json")
        qualified_demand_manifest_path.write_text(
            json.dumps(qualified_demand_manifest), encoding="utf-8")
        # Keep the earlier Phase 3 producer reference bound to the final
        # source-approved manifest bytes rather than the temporary stub.
        phase3_status_path = validation / "phase_3-validator-status.json"
        phase3_status = json.loads(
            phase3_status_path.read_text(encoding="utf-8"))
        phase3_status["references"][-1] = reference(
            qualified_demand_manifest_path)
        phase3_status["content_key"] = ai_flow._canonical_digest({
            key: value for key, value in phase3_status.items()
            if key != "content_key"
        })
        phase3_status_path.write_text(
            json.dumps(phase3_status), encoding="utf-8")
        status_artifacts["phase_3"] = reference(phase3_status_path)
        for phase in ("phase_0", "phase_1", "phase_2"):
            artifact = {
                "schema": "subhour_phase_status_v1",
                "kind": "subhour_phase_status", "phase": phase,
                "status": "PASS", "release_evidence": False,
                "evidence_id": f"{phase}-validator-status",
                "lineage": {}, "references": [
                    reference(qualified_demand_manifest_path)],
            }
            artifact["content_key"] = ai_flow._canonical_digest(artifact)
            status_path = validation / f"{phase}-validator-status.json"
            status_path.write_text(json.dumps(artifact), encoding="utf-8")
            status_artifacts[phase] = reference(status_path)

        frozen = {"digest": "validator-source"}
        checkpoint = ai_flow.build_phase_3_5_checkpoint(
            tmp_path, policy, checkpoint_baseline, frozen)
        review = {"status": "PASS", "content_digest": "validator-review"}
        gate_evidence = extract_gate_s_evidence(
            source_path=phase3_path, evidence_id="gate-validator-fixture")
        gate_evidence_path = validation / "subhour_gate_s_evidence_validator.json"
        gate_evidence_path.write_text(
            json.dumps(gate_evidence), encoding="utf-8")
        gate_registration = build_gate_s_registration(
            evidence_path=gate_evidence_path,
            evidence_sha256=hashlib.sha256(
                gate_evidence_path.read_bytes()).hexdigest(),
            evidence_id="gate-registration-validator-fixture")
        gate_registration_path = validation / (
            "subhour_gate_s_registration_validator.json")
        gate_registration_path.write_text(
            json.dumps(gate_registration), encoding="utf-8")
        gate_outcome = evaluate_gate_s(gate_registration, root=tmp_path)
        gate_outcome_path = validation / "subhour_gate_s_outcome_validator.json"
        gate_outcome_path.write_text(
            json.dumps(gate_outcome), encoding="utf-8")
        phases = {
            "phase_0": "PASS", "phase_1": "PASS", "phase_2": "PASS",
            "phase_3": "PASS", "phase_4": "PASS",
            "phase_5": "PASS",
            "phase_6": "PASS" if terminal == "pass" else "INCONCLUSIVE",
            "phase_7": "PASS",
        }
        phase6_reference = reference(outcome_path)
        registration_reference = reference(registration_path)
        evidence_ids = {
            phase: [f"{phase}-validator-status",
                    qualified_demand_manifest["evidence_id"]]
            for phase in ("phase_0", "phase_1", "phase_2")
        }
        evidence_ids.update({
            "phase_3": ["phase_3-validator-status", phase3["evidence_id"],
                        qualified_demand_manifest["evidence_id"]],
            "phase_4": ["phase_4-validator-status", profile["evidence_id"]],
            "phase_5": ["phase_5-validator-status", profile["evidence_id"],
                        window_index["evidence_id"]],
            "phase_6": [registration["evidence_id"]],
            "phase_7": [gate_registration["evidence_id"],
                        gate_evidence["evidence_id"]],
        })
        derived = ai_flow._derive_report_measurements(
            tmp_path, status_artifacts, phases, outcome)
        report = {
            "schema": "subhour_phase_report_v1",
            "kind": "subhour_phase_report", "release_evidence": False,
            "status": "COMPLETE", "phases": phases,
            "evidence_ids": evidence_ids,
            "measurements": {key: value for key, value in derived.items()
                             if key != "phase_resources"},
            "phase_resources": derived["phase_resources"],
            "status_artifacts": status_artifacts,
            "lineage": {
                "source_digest": frozen["digest"],
                "checkpoint_content_digest": checkpoint["content_digest"],
                "review_content_digest": review["content_digest"],
                "review_lineage_digest": checkpoint["lineage_digest"],
            },
            "artifacts": {
                "phase_6_registration": registration_reference,
                "phase_6_outcome": phase6_reference,
                "phase_7_evidence": reference(gate_evidence_path),
                "phase_7_registration": reference(gate_registration_path),
                "phase_7_outcome": reference(gate_outcome_path),
            },
        }
        report["content_key"] = ai_flow._canonical_digest(report)
        report_path = validation / "subhour_phase_report_validator.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        ai_flow.validate_post_review_terminal_artifacts(
            tmp_path, policy, frozen, checkpoint, review,
            initial_evidence_baseline)


def test_phase6_cli_missing_registration_is_not_allowed_without_evidence(
        tmp_path, monkeypatch):
    """The real CLI refuses an unregistered Phase 6 without publishing output."""
    import run_monthly_closure_search as monthly_cli

    spec = SimpleNamespace(interday_policy="independent_daily_reset_v1")
    policy = SimpleNamespace(objective_method="closure_cost_v1")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text("{}", encoding="utf-8")
    args = SimpleNamespace(
        spec=tmp_path / "spec.json", policy=policy_path,
        baseline_trip_duration_p99_s=1, bounded_exhaustive_cap=2,
        independent_exhaustive_candidate_cap=2,
        independent_exhaustive_daily_cap=2, seed_workers=1,
        daily_workers=1, max_active_sumo_slots=1, warm_execution=False,
        daily_unit_budget=None, daily_unit_total_cap=2,
        window_cost_index=None, screening_mode="independent-cost-ordered-exact",
        root=tmp_path, baseline_cache=None, demand_archive=None,
        demand_runs_root=tmp_path / "demand-runs",
        demand_release_root=tmp_path / "demand-release",
        no_build_missing_demand=False, daily_result_cache=tmp_path / "daily",
        daily_cost_cache=tmp_path / "costs", workspace_wait_s=0.0,
        phase6_registration=None, phase6_outcome=None,
    )
    monkeypatch.setattr(monthly_cli, "parse_args", lambda: args)
    monkeypatch.setattr(monthly_cli, "load_closure_search_spec",
                        lambda _path: spec)
    monkeypatch.setattr(monthly_cli.MonthlySearchPolicy, "from_dict",
                        staticmethod(lambda _value: policy))
    with pytest.raises(SystemExit, match="missing manifest"):
        monthly_cli.main()
    assert not list(tmp_path.rglob("subhour_full_month_*.json"))


def test_phase6_producer_terminal_is_receipt_bound_before_final_validation(
        tmp_path):
    """Traverse phase6_outcome -> append-only publication -> validator."""
    import run_monthly_closure_search as monthly_cli

    now = [12.5]
    controller = ActiveTimeController(
        hard_stop_s=10.0, publication_reserve_s=2.0,
        clock=lambda: now[0])
    controller.stop_new_starters = True
    outcome_path = tmp_path / "phase6-outcome.json"
    receipt_path = tmp_path / ".phase6-outcome.json.receipt.json"
    outcome = monthly_cli.phase6_outcome(
        registration={"evidence_id": "phase6-chain",
                      "content_key": "registration-key"},
        status="INCONCLUSIVE_BUDGET_EXHAUSTED",
        controller=controller,
        detail="ActiveBudgetExceeded after starter cancellation",
        new_starters_after_hard_stop=0,
        telemetry={
            "sumo_attempts": 0,
            "peak_rss_bytes": 0,
            "disk_growth_bytes": 0,
            "disk_roots": [str((tmp_path / "phase6").resolve())],
            "process_tree_rss_complete": False,
            "process_tree_rss_error": "budget terminal before census",
            "execution_started": False,
        },
        publication_receipt_path=receipt_path,
        publication_outcome_path=outcome_path,
    )
    receipt = monthly_cli.write_append_only_json(
        outcome_path, outcome, controller=controller)

    assert receipt["status"] == outcome["status"]
    assert receipt["authoritative_status"] == outcome["status"]
    validated = ai_flow._phase6_publication_receipt(
        tmp_path, outcome, expected_payload_path=outcome_path)
    assert validated == receipt
    assert json.loads(outcome_path.read_text(encoding="utf-8"))["content_key"] \
        == outcome["content_key"]


def test_gate_s_zero_regret_finalist_change_is_allowed_stress_terminal():
    decisions = {
        variant: _gate_decision(["a"], "a")
        for variant in ("q10", "q50", "q90")
    }
    decisions["q90"] = _gate_decision(["b"], "b")
    failures = {
        variant: {"all": [], "q50_recalled": []}
        for variant in ("q10", "q50", "q90")
    }
    result = classify(
        decisions,
        health={"status": "PASS"},
        decision_regret={variant: 0.0 for variant in decisions},
        variant_unique_failures=failures,
        _trusted_bound=True,
    )
    assert result["status"] == FINALIST_STRESS
    assert result["q50_only_active"] is False


def test_active_time_controller_stops_new_starters_and_reserves_publication():
    now = [0.0]
    controller = ActiveTimeController(
        hard_stop_s=10.0, publication_reserve_s=2.0, clock=lambda: now[0])
    now[0] = 10.0
    with pytest.raises(ActiveBudgetExceeded):
        controller.checkpoint("pilot")
    assert controller.stop_new_starters is True
    now[0] = 11.0
    controller.checkpoint("publish", publication=True)
    now[0] = 12.0
    with pytest.raises(ActiveBudgetExceeded):
        controller.checkpoint("publish", publication=True)


def test_active_time_controller_enforces_10_45_and_55_minute_boundaries():
    now = [0.0]
    controller = ActiveTimeController(clock=lambda: now[0])

    now[0] = 10 * 60
    controller.checkpoint("pilot", completed=1, total=2)
    assert controller.eta_checkpoints[0]["label"] == "10m"

    now[0] = 45 * 60
    # Nine of eleven identical units at this measured rate leave an ETA that
    # fits exactly within the registered 55-minute hard stop.
    controller.checkpoint("pilot", completed=9, total=11)
    checkpoint = next(item for item in controller.eta_checkpoints
                      if item["label"] == "45m")
    assert checkpoint["admission"]["fits_before_hard_stop"] is True
    assert controller.stop_new_starters is False

    now[0] = 55 * 60
    with pytest.raises(ActiveBudgetExceeded):
        controller.checkpoint("pilot")
    assert controller.stop_new_starters is True


def test_active_time_controller_fails_closed_at_45m_without_a_fitting_eta():
    now = [0.0]
    controller = ActiveTimeController(clock=lambda: now[0])
    now[0] = 45 * 60
    with pytest.raises(ActiveBudgetExceeded, match="45-minute admission"):
        controller.checkpoint("pilot")
    assert controller.stop_new_starters is True


def test_active_time_controller_uses_phase_local_rate_after_preflight_and_ledger():
    now = [0.0]
    controller = ActiveTimeController(clock=lambda: now[0])
    now[0] = 1000.0
    controller.checkpoint("preflight")
    now[0] = 2100.0
    controller.checkpoint("ledger")
    controller.checkpoint("pilot", completed=0, total=2)
    now[0] = 2700.0
    controller.checkpoint("pilot", completed=1, total=2)
    checkpoint = next(item for item in controller.eta_checkpoints
                      if item["label"] == "45m")
    assert checkpoint["phase"] == "pilot"
    assert checkpoint["phase_completed_units"] == 1
    assert checkpoint["phase_elapsed_s"] == pytest.approx(600.0)
    assert checkpoint["conservative_eta_s"] == pytest.approx(600.0)
    assert checkpoint["admission"]["fits_before_hard_stop"] is True
    now[0] = 3300.0
    with pytest.raises(ActiveBudgetExceeded):
        controller.checkpoint("pilot")


def test_active_time_controller_rechecks_45m_admission_before_later_starter():
    now = [0.0]
    controller = ActiveTimeController(clock=lambda: now[0])
    now[0] = 2100.0
    controller.checkpoint("pilot", completed=0, total=2)
    now[0] = 2700.0
    controller.checkpoint("pilot", completed=1, total=2)
    assert controller.stop_new_starters is False

    # The first estimate fit (600 s remaining at t=2700).  A slower updated
    # identical-unit measurement no longer fits before the 55-minute stop;
    # the next starter must be refused even though the original 45m record fit.
    now[0] = 3200.0
    with pytest.raises(ActiveBudgetExceeded, match="45-minute admission"):
        controller.checkpoint("pilot", completed=2, total=3)
    checkpoint = next(item for item in controller.eta_checkpoints
                      if item["label"] == "45m")
    assert len(checkpoint["admission_history"]) == 2
    assert checkpoint["admission_history"][-1]["fits_before_hard_stop"] is False
    assert controller.stop_new_starters is True


def test_monthly_publication_rechecks_reserve_after_atomic_workspace_write(
        tmp_path, monkeypatch):
    import traffic_sim.simulation.monthly_search as monthly_search
    from tests.test_monthly_search import _policy, _screen_builder, _spec, FakeRunner

    now = [0.0]
    controller = ActiveTimeController(
        hard_stop_s=10.0, publication_reserve_s=2.0,
        clock=lambda: now[0])
    original_publish = monthly_search._publish_json

    def slow_publish(*args, **kwargs):
        original_publish(*args, **kwargs)
        if len(args) >= 3 and args[2] == "result.json":
            now[0] = 12.0

    monkeypatch.setattr(monthly_search, "_publish_json", slow_publish)
    with pytest.raises(ActiveBudgetExceeded, match="publish"):
        monthly_search.run_monthly_search(
            _spec("publication-overrun"), _policy(), runner=FakeRunner(),
            screen_builder=_screen_builder, root=tmp_path,
            active_controller=controller)
    assert (tmp_path / "publication-overrun" / "artifacts" / "result.json").is_file()


def test_append_only_outcome_write_cleans_interrupted_temporary_file(
        tmp_path, monkeypatch):
    import run_monthly_closure_search as monthly_cli

    destination = tmp_path / "phase6-outcome.json"

    def interrupt_link(*_args, **_kwargs):
        raise RuntimeError("simulated interrupted publication")

    monkeypatch.setattr(monthly_cli.os, "link", interrupt_link)
    with pytest.raises(RuntimeError, match="interrupted"):
        monthly_cli.write_append_only_json(destination, {"status": "INCONCLUSIVE"})
    assert not destination.exists()
    assert list(tmp_path.glob("phase6-outcome.json.*.tmp")) == []


def test_publication_receipt_never_leaves_partial_authoritative_file(
        tmp_path, monkeypatch):
    import run_monthly_closure_search as monthly_cli

    destination = tmp_path / ".phase6.receipt.json"

    def interrupt_link(*_args, **_kwargs):
        raise RuntimeError("simulated receipt interruption")

    monkeypatch.setattr(monthly_cli.os, "link", interrupt_link)
    with pytest.raises(RuntimeError, match="receipt interruption"):
        monthly_cli._publish_receipt_no_clobber(
            destination, {"schema": "append_only_publication_receipt_v1"})
    assert not destination.exists()
    assert list(tmp_path.glob(".phase6.receipt.json.*.tmp")) == []


_KILL_POINT_PUBLISHER = '''\
import json
import os
import sys
from pathlib import Path

repo_root, outcome_arg, kill_call_arg, kill_when = sys.argv[1:5]
sys.path.insert(0, repo_root)

import run_monthly_closure_search as monthly_cli
from traffic_sim.simulation.monthly_search import ActiveTimeController

outcome_path = Path(outcome_arg)
kill_call = int(kill_call_arg)
controller = ActiveTimeController(
    hard_stop_s=3300.0, publication_reserve_s=300.0, clock=lambda: 100.0)
outcome = monthly_cli.phase6_outcome(
    registration={"evidence_id": "phase6-kill-point",
                  "content_key": "registration-key"},
    status="READY",
    controller=controller,
    detail="kill-point regression",
    search_result={
        "status": "unique_winner",
        "claim_boundary": {"global_best_claim_allowed": True},
        "cost_ordered_execution": {
            "terminal_status": None,
            "stop_proof": {"valid_for_ready": True},
        },
    },
    new_starters_after_hard_stop=0,
    telemetry={
        "sumo_attempts": 1,
        "peak_rss_bytes": 4096,
        "disk_growth_bytes": 8,
        "disk_roots": [str(outcome_path.parent.resolve())],
        "process_tree_rss_complete": True,
        "execution_started": True,
    },
    publication_receipt_path=monthly_cli.append_only_receipt_path(outcome_path),
    publication_outcome_path=outcome_path,
)
outcome_path.parent.mkdir(parents=True, exist_ok=True)
(outcome_path.parent / "intent.json").write_text(
    json.dumps(outcome, indent=2, sort_keys=True), encoding="utf-8")

real_link = os.link
calls = []


def killing_link(source, destination):
    calls.append(str(destination))
    if len(calls) == kill_call and kill_when == "before":
        os._exit(97)
    real_link(source, destination)
    if len(calls) == kill_call and kill_when == "after":
        os._exit(97)


monthly_cli.os.link = killing_link
monthly_cli.write_append_only_json(outcome_path, outcome, controller=controller)
print("published")
'''


def _run_killed_publisher(tmp_path, *, kill_call, kill_when):
    """Publish a real Phase 6 terminal in a process that dies at one link.

    ``os._exit`` skips every ``finally`` block, so this reproduces process
    death rather than an exception that still cleans up after itself.  The
    link calls are, in order: the staging commit, the destination commit and
    the receipt commit.
    """
    repo_root = Path(__file__).resolve().parents[1]
    script = tmp_path / "kill_point_publisher.py"
    script.write_text(_KILL_POINT_PUBLISHER, encoding="utf-8")
    outcome_path = tmp_path / "evidence" / "subhour_full_month_outcome_kill.json"
    result = subprocess.run(
        [sys.executable, str(script), str(repo_root), str(outcome_path),
         str(kill_call), kill_when],
        cwd=str(repo_root), capture_output=True, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return outcome_path, result


def _publication_controller():
    return ActiveTimeController(
        hard_stop_s=3300.0, publication_reserve_s=300.0, clock=lambda: 100.0)


def test_process_death_after_the_staging_commit_never_blocks_the_retry(tmp_path):
    """A killed staging link must not poison the destination forever.

    The staging link used to have one fixed name.  A process killed after it
    left that name on disk, and every later attempt to publish the same
    outcome failed on it -- refusing a publication that had never committed.
    """
    import run_monthly_closure_search as monthly_cli

    outcome_path, result = _run_killed_publisher(
        tmp_path, kill_call=1, kill_when="after")
    assert result.returncode == 97, result.stderr
    receipt_path = monthly_cli.append_only_receipt_path(outcome_path)
    assert not outcome_path.exists()
    assert not receipt_path.exists()
    orphans = sorted(
        outcome_path.parent.glob(outcome_path.name + ".*.committed.tmp"))
    assert orphans, "the killed process must leave its staging link behind"

    payload = json.loads(
        (outcome_path.parent / "intent.json").read_text(encoding="utf-8"))
    receipt = monthly_cli.write_append_only_json(
        outcome_path, payload, controller=_publication_controller())
    assert outcome_path.is_file()
    assert "recovered" not in receipt
    assert receipt["within_deadline"] is True
    assert receipt["authoritative_status"] == "READY"
    validated = ai_flow._phase6_publication_receipt(
        tmp_path, payload, expected_payload_path=outcome_path)
    assert validated == receipt
    # The orphan is inert, not evidence, and is never deleted by the retry.
    assert all(path.exists() for path in orphans)
    assert not any(
        path.name.endswith(".json") for path in orphans)


@pytest.mark.parametrize("kill_call,kill_when", [(2, "after"), (3, "before")])
def test_process_death_before_the_receipt_commit_recovers_without_timing(
        tmp_path, kill_call, kill_when):
    """A committed outcome with no receipt must become validatable, once.

    The outcome bytes are immutable and must never be rewritten.  The commit
    time died with the process, so the recovered receipt states that it is
    unknown instead of inventing one, and its authoritative terminal can
    never be promoted.
    """
    import run_monthly_closure_search as monthly_cli

    outcome_path, result = _run_killed_publisher(
        tmp_path, kill_call=kill_call, kill_when=kill_when)
    assert result.returncode == 97, result.stderr
    receipt_path = monthly_cli.append_only_receipt_path(outcome_path)
    assert outcome_path.is_file()
    assert not receipt_path.exists()
    committed = outcome_path.read_bytes()
    payload = json.loads(committed.decode("utf-8"))
    assert payload["status"] == "READY"

    with pytest.raises(FileExistsError, match="recovery receipt"):
        monthly_cli.write_append_only_json(
            outcome_path, payload, controller=_publication_controller())
    assert outcome_path.read_bytes() == committed

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["recovered"] is True
    assert receipt["committed_elapsed_s"] is None
    assert receipt["publication_deadline_s"] is None
    assert receipt["within_deadline"] is False
    assert receipt["status"] == "READY"
    assert receipt["authoritative_status"] == "INCONCLUSIVE_PUBLICATION_UNVERIFIED"
    assert receipt["payload_sha256"] == hashlib.sha256(committed).hexdigest()

    validated = ai_flow._phase6_publication_receipt(
        tmp_path, payload, expected_payload_path=outcome_path)
    assert validated == receipt

    # Recovery happens exactly once.  A further retry is the ordinary refusal
    # and leaves both append-only files byte-identical.
    snapshot = (outcome_path.read_bytes(), receipt_path.read_bytes())
    with pytest.raises(FileExistsError) as error:
        monthly_cli.write_append_only_json(
            outcome_path, payload, controller=_publication_controller())
    assert "recovery receipt" not in str(error.value)
    assert (outcome_path.read_bytes(), receipt_path.read_bytes()) == snapshot


def test_process_death_after_the_receipt_commit_leaves_a_complete_publication(
        tmp_path):
    """Death after the last commit needs no repair and permits no rewrite."""
    import run_monthly_closure_search as monthly_cli

    outcome_path, result = _run_killed_publisher(
        tmp_path, kill_call=3, kill_when="after")
    assert result.returncode == 97, result.stderr
    receipt_path = monthly_cli.append_only_receipt_path(outcome_path)
    assert outcome_path.is_file()
    assert receipt_path.is_file()
    payload = json.loads(outcome_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert "recovered" not in receipt
    assert receipt["within_deadline"] is True
    assert receipt["authoritative_status"] == "READY"
    assert ai_flow._phase6_publication_receipt(
        tmp_path, payload, expected_payload_path=outcome_path) == receipt

    snapshot = (outcome_path.read_bytes(), receipt_path.read_bytes())
    with pytest.raises(FileExistsError) as error:
        monthly_cli.write_append_only_json(
            outcome_path, payload, controller=_publication_controller())
    assert "recovery receipt" not in str(error.value)
    assert (outcome_path.read_bytes(), receipt_path.read_bytes()) == snapshot


def test_recovered_receipt_cannot_claim_timing_or_a_promotable_terminal(
        tmp_path):
    """Every self-attested escape from a recovered receipt must fail closed."""
    import run_monthly_closure_search as monthly_cli

    outcome_path, result = _run_killed_publisher(
        tmp_path, kill_call=2, kill_when="after")
    assert result.returncode == 97, result.stderr
    receipt_path = monthly_cli.append_only_receipt_path(outcome_path)
    payload = json.loads(outcome_path.read_text(encoding="utf-8"))
    with pytest.raises(FileExistsError):
        monthly_cli.write_append_only_json(
            outcome_path, payload, controller=_publication_controller())
    original = receipt_path.read_bytes()
    receipt = json.loads(original.decode("utf-8"))

    # Positive control: the producer's own recovered receipt validates.
    assert ai_flow._phase6_publication_receipt(
        tmp_path, payload, expected_payload_path=outcome_path) == receipt

    for mutation, message in (
        ({"committed_elapsed_s": 0.0}, "claims commit timing"),
        ({"committed_elapsed_s": 12.5}, "claims commit timing"),
        ({"within_deadline": True}, "claims commit timing"),
        ({"authoritative_status": "READY"}, "not authoritative"),
        ({"authoritative_status": "INCONCLUSIVE_BUDGET_EXHAUSTED"},
         "not authoritative"),
        ({"recovered": "yes"}, "recovery flag is invalid"),
        ({"recovered": False}, "timing is incomplete"),
    ):
        tampered = {key: value for key, value in receipt.items()
                    if key != "content_key"}
        tampered.update(mutation)
        tampered["content_key"] = ai_flow._canonical_digest(tampered)
        receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(ai_flow.FlowError, match=message):
            ai_flow._phase6_publication_receipt(
                tmp_path, payload, expected_payload_path=outcome_path)

    # A restored receipt still validates, so each rejection above is caused by
    # its own mutation and the boundary is not left permanently closed.
    receipt_path.write_bytes(original)
    assert ai_flow._phase6_publication_receipt(
        tmp_path, payload, expected_payload_path=outcome_path) == receipt


def test_recovery_refuses_a_committed_outcome_that_is_not_readable_json(
        tmp_path):
    """Recovery may only describe bytes it can actually read."""
    import run_monthly_closure_search as monthly_cli

    destination = tmp_path / "phase6-outcome.json"
    destination.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not readable JSON"):
        monthly_cli.recover_append_only_publication(destination)
    assert not monthly_cli.append_only_receipt_path(destination).exists()

    destination.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a JSON object"):
        monthly_cli.recover_append_only_publication(destination)
    assert not monthly_cli.append_only_receipt_path(destination).exists()


def _valid_phase3_comparison():
    return {
        "semantic_comparison_complete": True,
        "candidate_costs_field_identical": True,
        "hard_failures_identical": True,
        "health_classifications_identical": True,
        "timeout_outcomes_identical": True,
        "terminal_status_identical": True,
        "selected_ids_identical": True,
        "execution_contract_valid": True,
        "final_decision_identical": True,
        "restart_equivalent": True,
        "restart_cursor_identical": True,
        "restart_evidence_identical": True,
        "restart_attempt_identity_identical": True,
        "both_stop_proofs_valid": True,
        "stop_proof_valid": True,
        "cache_hits_consistent": True,
        "daily_results_cache_events_valid": True,
        "exact_attempt_population_check": {"valid": True},
        "active_elapsed_basis_consistent": True,
        "exact_attempts_reduction_meets_30_percent": True,
        "awake_active_time_reduction_meets_30_percent": True,
        "no_resource_cap_regression": True,
        "resource_measurements_complete": True,
        "fixture_application": {
            "applied": True, "arm_inputs_identical": True,
            "restart_cancel_observed": True,
            "no_detour_pre_sumo_gate": True,
        },
        "cancellation": {
            "performed": True, "called": True,
            "queued_work_cancelled": True, "no_later_starter": True,
        },
        "peak_rss_bytes": {
            "cost_ordered": 1, "ordered_exhaustive": 1,
        },
        "active_elapsed_s": {
            "cost_ordered": 1.0, "ordered_exhaustive": 2.0,
        },
        "disk_growth_bytes": 1,
        "disk_growth_bytes_by_arm": {
            "cost_ordered": 1, "ordered_exhaustive": 1,
        },
    }


def test_phase3_resource_gate_accepts_a_fully_valid_pair():
    from tools.subhour_cost_ordered_benchmark import _resource_gates_pass

    assert _resource_gates_pass(_valid_phase3_comparison(), {
        "peak_rss_bytes": 8 * 1024**3,
        "disk_growth_bytes": 20 * 1024**3,
        "active_seconds": 3300,
    }) is True


@pytest.mark.parametrize("field", [
    "terminal_status_identical", "selected_ids_identical",
    "restart_cursor_identical", "restart_evidence_identical",
    "restart_attempt_identity_identical", "stop_proof_valid",
    "no_resource_cap_regression",
])
def test_phase3_resource_gate_rejects_each_supplied_divergence(field):
    from tools.subhour_cost_ordered_benchmark import _resource_gates_pass

    comparison = _valid_phase3_comparison()
    comparison[field] = False
    assert _resource_gates_pass(comparison, {
        "peak_rss_bytes": 8 * 1024**3,
        "disk_growth_bytes": 20 * 1024**3,
        "active_seconds": 3300,
    }) is False


def test_gate_s_reads_only_the_digest_bound_evidence_and_rejects_swaps(tmp_path):
    decisions = {case: _gate_decision(["a"], "a")
                 for case in ("q10", "q50", "q90")}
    evidence = {
        "decisions": decisions,
        "decision_regret": {case: 0.0 for case in ("q10", "q50", "q90")},
        "variant_unique_failures": {
            case: {"all": [], "q50_recalled": []}
            for case in ("q10", "q50", "q90")},
    }
    path = tmp_path / "bound.json"
    raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="current bound evidence schema"):
        build_gate_s_registration(
            evidence_path=path, evidence_sha256=hashlib.sha256(raw).hexdigest())


def test_gate_s_rejects_empty_bound_phase3_population():
    registration = json.loads(Path(
        "validation/subhour_gate_s_registration_20260831-v3.json"
    ).read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="complete registered case set|decision schema"):
        evaluate_gate_s(registration)


@pytest.mark.parametrize("expected,variant_overrides,status", [
    (ROBUST_THREE_VARIANT, {"q90": {"hard_failures": ["fault"],
                                      "viable_set": [], "finalists": [],
                                      "winner": None}}, "PASS"),
        (INCONCLUSIVE, {"q90": {"finalists": ["b"], "winner": "b"}},
         "PASS"),
    (Q50_ONLY, {}, "PASS"),
    (INCONCLUSIVE, {}, "INCONCLUSIVE_BOUNDED_GATES"),
])
def test_gate_s_end_to_end_derives_all_four_policy_outcomes(
        tmp_path, expected, variant_overrides, status):
    """A complete source is digest-bound, and PASS is a healthy source status."""
    base_decision = {
        "hard_failures": [], "viable_set": ["a"],
        "finalists": ["a"], "winner": "a",
        "capacity_exceeded": False,
    }
    variants = {}
    for variant in ("q10", "q50", "q90"):
        decision = dict(base_decision)
        decision.update(variant_overrides.get(variant, {}))
        variants[variant] = {
            "decision": decision,
            "decision_relevant_failures": list(decision["hard_failures"]),
            "winner_cost": 1.0,
            "reference_winner_cost": 1.0,
            "candidate_costs": {
                candidate: {
                    "added_vehicle_hours": (0.5 if candidate == "b" else 1.0),
                    "added_metres_total": 1.0,
                    "vehicles_affected": 1,
                    "vehicles_no_detour": 0,
                    "feasible": candidate in decision["viable_set"],
                }
                for candidate in ("a", "b")
            },
        }
    source = {
        "schema": "subhour_cost_ordered_bounded_outcome_v1",
        "kind": "subhour_bounded_sumo_outcome",
        "release_evidence": False,
        "evidence_id": "source-gate-test",
        "registration": {
            "evidence_id": "source-gate-test",
            "content_key": "registration-key",
        },
        "status": status,
        "selection": {"selected_ids": ["case-content"]},
        "case_results": [{"case_id": "case-1",
                          "search_content_key": "case-content",
                          "gates_passed": True,
                          "decision_population_complete": True}],
        "gate_s": {"population_complete": True, "variants": variants},
    }
    from tools.subhour_cost_ordered_benchmark import _with_content_key
    registration_path = tmp_path / "bounded-registration.json"
    registration_record = {
        "schema": "subhour_cost_ordered_bounded_registration_v1",
        "evidence_id": "source-gate-test",
        "content_key": "",
        "selection": {"selected_ids": ["case-content"]},
        "selected_cases": [{"case_id": "case-1",
                             "search_content_key": "case-content"}],
    }
    registration_record["content_key"] = benchmark_module._key({
        key: value for key, value in registration_record.items()
        if key not in {"content_key", "registered_at"}
    })
    registration_path.write_text(json.dumps(registration_record), encoding="utf-8")
    source["registration"]["content_key"] = registration_record["content_key"]
    source["registration"].update({
        "path": str(registration_path),
        "sha256": hashlib.sha256(registration_path.read_bytes()).hexdigest(),
    })
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(_with_content_key(source), sort_keys=True)
                           + "\n", encoding="utf-8")
    extracted = extract_gate_s_evidence(
        source_path=source_path, evidence_id="gate-evidence-test")
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(extracted, sort_keys=True) + "\n",
                             encoding="utf-8")
    raw = evidence_path.read_bytes()
    registration = build_gate_s_registration(
        evidence_path=evidence_path,
        evidence_sha256=hashlib.sha256(raw).hexdigest(),
        evidence_id="gate-registration-test")
    outcome = evaluate_gate_s(registration)
    assert outcome["status"] == expected


def test_phase6_registration_accepts_not_triggered_but_binds_both_roots(tmp_path):
    spec = SimpleNamespace(search_id="phase6-contract", content_key="phase6-content")
    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy
    policy = MonthlySearchPolicy.from_dict(json.loads(
        Path("validation/monthly_search_policy_v3.json").read_text()))
    output = tmp_path / "output"
    workspace = tmp_path / "workspace"
    prerequisites = {name: "PASS" for name in (
        "phase_0", "phase_1", "phase_2", "phase_3", "phase_4", "review")}
    prerequisites["phase_5"] = "NOT_TRIGGERED"
    with pytest.raises(ValueError, match="must bind an artifact"):
        build_phase6_registration(
            spec, policy, evidence_id="phase6-contract-v1",
            prerequisites=prerequisites, output_root=output,
            workspace_root=workspace)


def test_phase6_registration_rejects_caller_claims_without_real_phase_evidence(tmp_path):
    spec = SimpleNamespace(search_id="phase6-contract", content_key="phase6-content")
    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy
    policy = MonthlySearchPolicy.from_dict(json.loads(
        Path("validation/monthly_search_policy_v3.json").read_text()))
    source = tmp_path / "source.json"
    source.write_text('{"source": true}\n', encoding="utf-8")
    input_file = tmp_path / "input.json"
    input_file.write_text('{"input": true}\n', encoding="utf-8")
    runtime_file = tmp_path / "runtime.json"
    runtime_file.write_text('{"runtime": true}\n', encoding="utf-8")
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"policy": true}\n', encoding="utf-8")
    lineage = {
        "source_digests": {"source.json": hashlib.sha256(source.read_bytes()).hexdigest()},
        "input_digests": {"input.json": hashlib.sha256(input_file.read_bytes()).hexdigest()},
        "runtime_digest": hashlib.sha256(runtime_file.read_bytes()).hexdigest(),
        "policy_digest": hashlib.sha256(policy_file.read_bytes()).hexdigest(),
    }
    references = [
        {"path": str(source), "sha256": lineage["source_digests"]["source.json"]},
        {"path": str(input_file), "sha256": lineage["input_digests"]["input.json"]},
        {"path": str(runtime_file), "sha256": lineage["runtime_digest"]},
        {"path": str(policy_file), "sha256": lineage["policy_digest"]},
    ]
    prerequisites = {}
    for name in ("phase_0", "phase_1", "phase_2", "phase_3", "phase_4"):
        artifact = build_phase_status_artifact(
            phase=name, status="PASS", evidence_id=f"{name}-evidence",
            lineage=lineage, references=references)
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        prerequisites[name] = {
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "status": "PASS", "lineage": {"invented": True},
        }
    artifact = build_phase_status_artifact(
        phase="phase_5", status="NOT_TRIGGERED", evidence_id="phase_5-evidence",
        lineage=lineage,
        references=references)
    path = tmp_path / "phase_5.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    prerequisites["phase_5"] = {
        "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "status": "PASS", "lineage": {"invented": True},
    }
    checkpoint_body = {
        "schema_version": 1,
        "kind": "ai_flow_phase_3_5_checkpoint",
        "status": "PENDING_INDEPENDENT_REVIEW",
        "source_digest": "a" * 64,
        "artifact_inventory": {"validation/registration-*.json": {}},
        "artifact_inventory_digest": "b" * 64,
        "lineage_digest": "c" * 64,
        "phase6_registration_globs": [],
    }
    checkpoint = dict(checkpoint_body)
    checkpoint["content_digest"] = ai_flow._canonical_digest(checkpoint_body)
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    response = {
        "status": "APPROVED", "summary": "independent review",
        "findings": [], "blocked_reason": "",
    }
    response_path = tmp_path / "review-response.json"
    response_path.write_text(json.dumps(response), encoding="utf-8")
    review = ai_flow.build_phase_3_5_review_artifact(
        checkpoint, response, "phase-review-01"
    )
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    prerequisites["review"] = {
        "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "status": "PASS",
        "checkpoint": {
            "path": str(checkpoint_path),
            "sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
        },
        "review_response": {
            "path": str(response_path),
            "sha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
        },
    }
    with pytest.raises(ValueError, match="actual evidence"):
        build_phase6_registration(
            spec, policy, evidence_id="phase6-contract-v2", prerequisites=prerequisites,
            output_root=tmp_path / "output", workspace_root=tmp_path / "workspace")


def test_phase6_registration_rejects_self_attested_review_envelope(tmp_path):
    with pytest.raises(ValueError, match="self-attested"):
        from run_monthly_closure_search import build_phase_review_artifact
        build_phase_review_artifact(lineage={}, references=[])


def test_phase6_registration_rejects_fabricated_pass_artifacts(tmp_path):
    spec = SimpleNamespace(search_id="phase6-contract", content_key="phase6-content")
    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy
    policy = MonthlySearchPolicy.from_dict(json.loads(
        Path("validation/monthly_search_policy_v3.json").read_text()))
    path = tmp_path / "fake.json"
    path.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    reference = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    prerequisites = {
        name: {"path": str(path), "sha256": reference["sha256"]}
        for name in ("phase_0", "phase_1", "phase_2", "phase_3", "phase_4", "phase_5", "review")
    }
    with pytest.raises(ValueError, match="invalid schema or kind"):
        build_phase6_registration(
            spec, policy, evidence_id="phase6-contract-fake", prerequisites=prerequisites,
            output_root=tmp_path / "output")


def test_phase6_outcome_publishes_gate_s_compatible_population(tmp_path):
    controller = ActiveTimeController(clock=lambda: 0.0)
    search_result = {
        "status": "unique_winner",
        "pilot_selection": {"selected_ids": ["case-1"]},
        "case_results": [{"case_id": "case-1", "search_content_key": "case-1"}],
        "gate_s": {"population_complete": True, "variants": {"q10": {}, "q50": {}, "q90": {}}},
    }
    outcome = phase6_outcome(
        registration={"evidence_id": "phase6", "content_key": "registration"},
        status="INCONCLUSIVE", controller=controller, search_result=search_result)
    body = {key: value for key, value in outcome.items() if key != "content_key"}
    assert outcome["content_key"] == hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert outcome["evidence_id"] == "phase6"
    assert outcome["selection"]["selected_ids"] == ["case-1"]
    assert outcome["case_results"] == search_result["case_results"]
    assert set(outcome["gate_s"]["variants"]) == {"q10", "q50", "q90"}


def test_phase6_budget_terminal_carries_complete_runtime_telemetry():
    now = [12.5]
    controller = ActiveTimeController(clock=lambda: now[0])
    now[0] = 25.0
    controller.starter_events.append({"phase": "pilot", "after_hard_stop": False})
    controller.cancel_requests = 1
    controller.stop_new_starters = True
    outcome = phase6_outcome(
        registration={"evidence_id": "phase6-budget", "content_key": "registration"},
        status="INCONCLUSIVE_BUDGET_EXHAUSTED",
        controller=controller,
        detail="budget exhausted",
        new_starters_after_hard_stop=0,
        telemetry={
            "sumo_attempts": 4,
            "peak_rss_bytes": 100,
            "disk_growth_bytes": 200,
        },
    )
    assert outcome["telemetry"] == {
        "sumo_attempts": 4,
        "peak_rss_bytes": 100,
        "disk_growth_bytes": 200,
        "active_elapsed_s": 12.5,
        "disk_roots": None,
        "process_tree_rss_complete": None,
        "process_tree_rss_error": None,
    }
    assert outcome["budget_telemetry"]["work_stopped_elapsed_s"] == 12.5
    assert outcome["budget_telemetry"]["cancel_requests"] == 1
    assert outcome["budget_telemetry"]["starter_events"]


def test_ready_append_only_write_fails_closed_before_deadline_crossing(
        tmp_path, monkeypatch):
    import run_monthly_closure_search as monthly_cli

    now = [0.0]
    controller = ActiveTimeController(
        hard_stop_s=10.0, publication_reserve_s=2.0,
        clock=lambda: now[0])
    original_fsync = monthly_cli.os.fsync

    def slow_fsync(fd):
        original_fsync(fd)
        now[0] = 13.0

    monkeypatch.setattr(monthly_cli.os, "fsync", slow_fsync)
    with pytest.raises(ActiveBudgetExceeded, match="publish"):
        monthly_cli.write_append_only_json(
            tmp_path / "ready.json", {"status": "READY"},
            controller=controller)
    assert not (tmp_path / "ready.json").exists()
