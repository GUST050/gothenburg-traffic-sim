"""Fail-closed report tests for the narrow real-SUMO replay tool."""

from copy import deepcopy

import pytest

from tools import verify_closure_routing_frozen_units as verify
from traffic_sim.simulation import closure_routing
from traffic_sim.simulation.finalist_decision import DEMAND_VARIANTS
from traffic_sim.simulation.monthly_search import canonical_seed


FORMER_TIMEOUT_UNIT = "daily-unit-24737391111be0e137537df7"


def _variant(unit_id, schedule_id, variant, *, healthy=False):
    seed = canonical_seed(variant, 0)
    launch = {
        "candidate_id": schedule_id,
        "work_date": "2027-09-28",
        "stage": "pilot",
        "variant": variant,
        "seed": seed,
        "attempt": 1,
        "timed_out": False,
        "outcome": "success",
    }
    healthy_check = None
    if healthy:
        healthy_check = {
            "all_passed": True,
            "reference_comparison": {
                "all_equal": True,
                "reference_report": "reference.json",
                "reference_report_sha256": "a" * 64,
            },
        }
    return {
        "seed": seed,
        "first_attempt_wall_s": 30.0,
        "run_candidate_wall_s": 30.0,
        "launch_telemetry_delta": {
            "pilot": {"attempts": 1, "timeouts": 0, "other_outcomes": 1},
            "finalist": {"attempts": 0, "timeouts": 0, "other_outcomes": 0},
        },
        "launch_state": {
            "records": [launch],
            "attempt_count": 1,
            "first_attempt": launch,
            "first_attempt_wall_s": 30.0,
            "run_candidate_wall_s": 30.0,
            "retry_records": [],
            "timeout_records": [],
            "final_outcome": "success",
        },
        "error": None,
        "hard_failures": [],
        "timeout_undecided": [],
        "observation": {
            "hard_failures": [],
            "feasibility_hard_failures": [],
            "routing_provenance": {
                "unit_id": unit_id,
                "candidate_id": schedule_id,
                "work_date": "2027-09-28",
                "demand_variant": variant,
                "seed": seed,
                "execution_arm": "cold",
                "vehicle_class": closure_routing.DEFAULT_VCLASS,
                "denied_count": 0,
            },
            "teleports": {"baseline": 0, "candidate": 0},
            "active_closed_edge_throughput": 0,
            "denial_reasons": {},
            "recovery": {"recovered": True},
            "unaffected_route_check": {
                "byte_identical_to_source": True,
                "missing_vehicle_ids": [],
                "mismatched_vehicle_ids": [],
            },
            "healthy_control_semantic_check": healthy_check,
        },
    }


def _unit(unit_id):
    schedule_id = f"schedule-{unit_id[-6:]}"
    healthy = unit_id == verify.HEALTHY_CONTROL_UNIT_ID
    return {
        "unit_id": unit_id,
        "schedule_id": schedule_id,
        "work_date": "2027-09-28",
        "variants": {
            variant: _variant(unit_id, schedule_id, variant, healthy=healthy)
            for variant in DEMAND_VARIANTS
        },
    }


def _report():
    return {
        "schema": verify.SCHEMA,
        "reference_report": "reference.json",
        "reference_report_sha256": "a" * 64,
        "units": [
            _unit(FORMER_TIMEOUT_UNIT),
            _unit(verify.HEALTHY_CONTROL_UNIT_ID),
        ],
    }


def test_complete_report_passes_all_fail_closed_criteria():
    result = verify._verification_result(_report(), reference_required=True)
    assert result["status"] == "passed"
    assert result["all_passed"] is True
    assert result["failures"] == []


def test_missing_named_unit_fails_verification():
    report = _report()
    report["units"].pop()
    result = verify._verification_result(report, reference_required=False)
    assert result["status"] == "failed"
    assert any("missing required units" in item for item in result["failures"])


def test_timeout_retry_or_slow_first_attempt_cannot_exit_successfully():
    report = deepcopy(_report())
    variant = report["units"][0]["variants"]["q10"]
    retry = dict(variant["launch_state"]["first_attempt"], attempt=2,
                 timed_out=True, outcome="timeout")
    variant["launch_state"]["records"].append(retry)
    variant["launch_state"]["retry_records"] = [retry]
    variant["launch_state"]["timeout_records"] = [retry]
    variant["launch_state"]["first_attempt_wall_s"] = 300.0
    variant["launch_state"]["final_outcome"] = "timeout"
    result = verify._verification_result(report, reference_required=False)
    assert result["all_passed"] is False
    assert any("exactly one launch" in item for item in result["failures"])
    assert any("finite, positive and below 300s" in item
               for item in result["failures"])
    assert any("retry was required" in item for item in result["failures"])
    assert any("timeout launch" in item for item in result["failures"])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, 0.0])
def test_non_finite_or_non_positive_timing_fails_verification(value):
    report = deepcopy(_report())
    variant = report["units"][0]["variants"]["q10"]
    variant["first_attempt_wall_s"] = value
    variant["run_candidate_wall_s"] = value
    variant["launch_state"]["first_attempt_wall_s"] = value
    variant["launch_state"]["run_candidate_wall_s"] = value
    result = verify._verification_result(report, reference_required=False)
    assert result["all_passed"] is False
    assert any("finite, positive" in item for item in result["failures"])


def test_duplicated_timing_fields_must_agree():
    report = deepcopy(_report())
    report["units"][0]["variants"]["q10"]["run_candidate_wall_s"] = 31.0
    result = verify._verification_result(report, reference_required=False)
    assert result["all_passed"] is False
    assert any("timing fields disagree" in item for item in result["failures"])


def test_reference_run_requires_equal_healthy_control_fields():
    report = deepcopy(_report())
    healthy = report["units"][1]["variants"]["q50"]["observation"]
    healthy["healthy_control_semantic_check"]["reference_comparison"] = None
    result = verify._verification_result(report, reference_required=True)
    assert result["all_passed"] is False
    assert any("reference comparison failed" in item
               for item in result["failures"])


def test_new_launch_records_preserves_exact_identity_bearing_records():
    old = [{
        "candidate_id": "old", "work_date": "2027-09-27", "stage": "pilot",
        "variant": "q10", "seed": 1000, "attempt": 1,
        "timed_out": False, "outcome": "success",
    }]
    new = {
        "candidate_id": "new", "work_date": "2027-09-28", "stage": "pilot",
        "variant": "q50", "seed": 1001, "attempt": 1,
        "timed_out": False, "outcome": "success",
    }
    assert verify._new_launch_records(old, [*old, new]) == [new]
