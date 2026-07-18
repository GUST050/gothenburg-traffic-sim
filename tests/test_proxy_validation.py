import copy
import json
from pathlib import Path

import pytest

from traffic_sim.simulation.monthly_proxy import PROXY_VERSION
from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
from traffic_sim.simulation.proxy_validation import (
    evaluate_partial_validation_set,
    evaluate_validation_case,
    evaluate_validation_set,
    validate_validation_manifest,
)


def _manifest():
    spec_a = ClosureSearchSpec(
        search_id="validation-a",
        directed_edges=("a_b_0",),
        demand_build_id="forecast-a",
        source="forecast",
        permitted_date_start="2027-04-05",
        permitted_date_end="2027-04-05",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
    )
    spec_b = ClosureSearchSpec(
        search_id="validation-b",
        directed_edges=("c_d_0",),
        demand_build_id="forecast-b",
        source="forecast",
        permitted_date_start="2027-04-06",
        permitted_date_end="2027-04-06",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
    )
    raw = {
        "schema_version": 1,
        "kind": "monthly_proxy_validation_manifest",
        "proxy_version": PROXY_VERSION,
        "frozen_at": "2026-07-18T12:00:00Z",
        "frozen_before_outcomes": True,
        "minimum_cases": 2,
        "minimum_spearman_case_fraction": 1.0,
        "minimum_ranking_case_fraction": 0.5,
        "required_strata": {
            "road_class": ["primary", "secondary"],
            "sensor_distance_band": ["near", "far"],
            "topology": ["grid", "bridge"],
            "duration": ["short", "long"],
            "day_type": ["weekday", "weekend"],
        },
        "cases": [
            {
                "case_id": "case-a",
                "closure_search_spec": spec_a.to_dict(),
                "strata": {
                    "road_class": "primary",
                    "sensor_distance_band": "near",
                    "topology": "grid",
                    "duration": "short",
                    "day_type": "weekday",
                },
                "schedule_ids": [
                    schedule.schedule_id
                    for schedule in generate_closure_schedules(spec_a)
                ],
            },
            {
                "case_id": "case-b",
                "closure_search_spec": spec_b.to_dict(),
                "strata": {
                    "road_class": "secondary",
                    "sensor_distance_band": "far",
                    "topology": "bridge",
                    "duration": "long",
                    "day_type": "weekend",
                },
                "schedule_ids": [
                    schedule.schedule_id
                    for schedule in generate_closure_schedules(spec_b)
                ],
            },
        ],
    }
    return validate_validation_manifest(raw)


def _provenance(case_id):
    return {
        "network_sha256": "1" * 64,
        "demand_sha256": "2" * 64,
        "proxy_input_sha256": "3" * 64,
        "simulation_mode": "meso",
        "seed_set": [1000, 1001, 1002],
        "sumo_version": "SUMO 1.24.0",
        "matched_baseline_id": f"baseline-{case_id}",
    }


def _case_outcome(case_id, schedule_ids, *, objectives=None,
                  shortlist=(0, 1), ranks=None, failing=(4,)):
    objectives = objectives or [0.0, 10.0, 20.0, 100.0, 200.0]
    ranks = ranks or list(range(5))
    candidates = []
    for index in range(5):
        eligible = index not in failing
        candidates.append({
            "schedule_id": schedule_ids[index],
            "eligible": eligible,
            "sumo_objective": objectives[index] if eligible else None,
            "proxy_rank": ranks[index],
            "shortlisted": index in shortlist,
            "proxy_failure_flag": index in failing,
        })
    return {
        "case_id": case_id,
        "exhaustive": True,
        "provenance": _provenance(case_id),
        "candidates": candidates,
    }


def _outcomes(manifest, **overrides):
    schedules = {
        case["case_id"]: case["schedule_ids"] for case in manifest["cases"]
    }
    cases = [
        _case_outcome("case-a", schedules["case-a"]),
        _case_outcome("case-b", schedules["case-b"]),
    ]
    cases_by_id = {case["case_id"]: case for case in cases}
    cases_by_id.update(overrides)
    return {
        "schema_version": 1,
        "kind": "monthly_proxy_validation_outcomes",
        "proxy_version": PROXY_VERSION,
        "manifest_content_key": manifest["content_key"],
        "cases": list(cases_by_id.values()),
    }


def test_manifest_is_normalized_and_content_addressed():
    manifest = _manifest()
    assert len(manifest["content_key"]) == 64
    assert [case["case_id"] for case in manifest["cases"]] == [
        "case-a", "case-b"
    ]


def test_manifest_refuses_missing_representation_stratum():
    raw = _manifest()
    raw.pop("content_key")
    raw["required_strata"]["topology"].append("roundabout")
    with pytest.raises(ValueError, match="misses required strata"):
        validate_validation_manifest(raw)


def test_manifest_content_tamper_is_detected():
    manifest = _manifest()
    manifest["cases"][0]["schedule_ids"][0] = "tampered"
    with pytest.raises(ValueError, match="schedule IDs do not exactly match"):
        validate_validation_manifest(manifest)


def test_case_reports_recall_regret_spearman_and_failures():
    manifest = _manifest()
    descriptor = manifest["cases"][0]
    report = evaluate_validation_case(
        descriptor, _case_outcome("case-a", descriptor["schedule_ids"])
    )
    assert report["winner_recalled"] is True
    assert report["normalized_shortlist_regret"] == 0
    assert report["spearman_rho"] == pytest.approx(1.0)
    assert report["failure_disqualification_recall"] == 1.0


def test_normalized_regret_uses_observed_eligible_impact_range():
    manifest = _manifest()
    outcome = _case_outcome(
        "case-a", manifest["cases"][0]["schedule_ids"],
        shortlist=(1,),
        failing=(),
    )
    report = evaluate_validation_case(manifest["cases"][0], outcome)
    # Eligible p90 of [0,10,20,100,200] is 160. Best shortlist is 10.
    assert report["normalized_shortlist_regret"] == pytest.approx(10 / 160)


def test_gate_passes_only_complete_held_out_evidence():
    manifest = _manifest()
    report = evaluate_validation_set(manifest, _outcomes(manifest))
    assert report["gate_status"] == "pass"
    assert report["ui_exposure_allowed"] is True
    assert report["metrics"] == {
        "winner_recall": 1.0,
        "p90_normalized_shortlist_regret": 0.0,
        "median_spearman": 1.0,
        "spearman_case_fraction": 1.0,
        "ranking_case_fraction": 1.0,
        "failure_disqualification_recall": 1.0,
    }


def test_gate_fails_when_winner_recall_regret_and_rank_quality_fail():
    manifest = _manifest()
    bad = _case_outcome(
        "case-b",
        manifest["cases"][1]["schedule_ids"],
        shortlist=(3,),
        ranks=[4, 3, 2, 1, 0],
        failing=(4,),
    )
    report = evaluate_validation_set(
        manifest, _outcomes(manifest, **{"case-b": bad})
    )
    assert report["gate_status"] == "fail"
    assert report["ui_exposure_allowed"] is False
    assert report["gate_checks"]["winner_recall"] is False
    assert report["gate_checks"]["p90_normalized_shortlist_regret"] is False
    assert report["gate_checks"]["median_spearman"] is False


def test_missing_case_or_non_exhaustive_case_fails_closed():
    manifest = _manifest()
    partial = _outcomes(manifest)
    partial["cases"].pop()
    with pytest.raises(ValueError, match="exactly cover frozen cases"):
        evaluate_validation_set(manifest, partial)

    incomplete = _outcomes(manifest)
    incomplete["cases"][0]["exhaustive"] = False
    with pytest.raises(ValueError, match="not exhaustive"):
        evaluate_validation_set(manifest, incomplete)


def test_provenance_is_mandatory_and_exact_schedule_set_is_enforced():
    manifest = _manifest()
    bad = _outcomes(manifest)
    bad["cases"][0]["provenance"]["network_sha256"] = "not-a-hash"
    with pytest.raises(ValueError, match="network_sha256"):
        evaluate_validation_set(manifest, bad)

    missing = _outcomes(manifest)
    missing["cases"][0]["candidates"].pop()
    with pytest.raises(ValueError, match="missing exhaustive schedules"):
        evaluate_validation_set(manifest, missing)


def test_actual_disqualification_must_be_flagged_or_simulated_for_recall():
    manifest = _manifest()
    outcome = _case_outcome(
        "case-a", manifest["cases"][0]["schedule_ids"]
    )
    outcome["candidates"][4]["proxy_failure_flag"] = False
    outcome["candidates"][4]["shortlisted"] = False
    report = evaluate_validation_case(manifest["cases"][0], outcome)
    assert report["failure_disqualification_recall"] == 0.0


def test_all_disqualified_case_is_failure_evidence_not_fake_ranking_evidence():
    manifest = _manifest()
    outcome = _case_outcome(
        "case-a",
        manifest["cases"][0]["schedule_ids"],
        failing=(0, 1, 2, 3, 4),
    )
    report = evaluate_validation_case(manifest["cases"][0], outcome)
    assert report["case_role"] == "failure_only"
    assert report["winner_recalled"] is None
    assert report["normalized_shortlist_regret"] is None
    assert report["spearman_rho"] is None
    assert report["failure_disqualification_recall"] == 1.0


def test_partial_report_is_diagnostic_and_can_never_open_ui_gate():
    manifest = _manifest()
    partial = _outcomes(manifest)
    partial["cases"].pop()
    report = evaluate_partial_validation_set(manifest, partial)
    assert report["gate_status"] == "incomplete"
    assert report["case_count"] == 1
    assert report["required_case_count"] == 2
    assert report["missing_case_ids"] == ["case-b"]
    assert report["ui_exposure_allowed"] is False
    assert report["global_best_claim_allowed"] is False
    assert report["gate_checks"]["all_frozen_cases_complete"] is False


def test_partial_report_can_conclusively_fail_when_best_case_is_unreachable():
    manifest = _manifest()
    partial = _outcomes(manifest)
    partial["cases"].pop()
    for candidate in partial["cases"][0]["candidates"]:
        candidate["shortlisted"] = candidate["schedule_id"].endswith("no-match")
    report = evaluate_partial_validation_set(manifest, partial)
    assert report["metrics"]["winner_recall"] == 0.0
    assert report["metrics"]["winner_recall_upper_bound"] == 0.5
    assert report["gate_status"] == "fail"
    assert "cannot raise winner recall" in report["reason"]
    assert report["ui_exposure_allowed"] is False


def test_tracked_v1_gate_record_matches_frozen_manifest_and_stays_closed():
    manifest = validate_validation_manifest(json.loads(
        Path("validation/monthly_proxy_manifest.json").read_text()
    ))
    gate = json.loads(
        Path("validation/monthly_proxy_v1_gate.json").read_text()
    )
    assert gate["manifest_content_key"] == manifest["content_key"]
    assert gate["completed_cases"] < gate["required_cases"]
    assert (
        gate["metrics"]["winner_recall_optimistic_upper_bound"]
        < gate["thresholds"]["winner_recall_minimum"]
    )
    assert gate["gate_status"] == "fail"
    assert gate["ui_exposure_allowed"] is False
    assert gate["global_best_claim_allowed"] is False
