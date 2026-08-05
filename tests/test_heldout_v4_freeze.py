"""Non-SUMO checks for the frozen, pre-outcome V4 campaign design."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from run_monthly_proxy_validation import VALIDATION_POLICY, gate_record_for
from traffic_sim.simulation.monthly_proxy import (
    HELD_OUT_VALIDATED_SHORTLIST_POLICY,
    SHORTLIST_VERSION,
    stratified_shortlist,
)
from traffic_sim.simulation.proxy_validation import (
    validate_validation_manifest,
    validation_manifest_content_key,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "validation/monthly_proxy_policy_v4.json"
SELECTION = ROOT / "validation/heldout_v4_selection.json"
MANIFEST = ROOT / "validation/monthly_proxy_manifest_v4.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def _edges(manifest: dict) -> set[str]:
    return {
        edge
        for case in manifest["cases"]
        for edge in (
            case.get("directed_edges")
            or (case.get("closure_search_spec") or {}).get("directed_edges")
            or []
        )
    }


def _canonical_without(raw: dict, key: str) -> str:
    value = dict(raw)
    value.pop(key, None)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def test_v4_policy_freezes_exact_date_endpoints_and_unchanged_gates():
    policy = _read(POLICY)
    selection = policy["selection_rule"]
    assert policy["campaign_version"] == "v4"
    assert policy["shortlist_version"] == "stratified_shortlist_v3"
    assert selection["maximum_shortlist"] == 96
    assert selection["bounded_exhaustive_limit"] == 1
    assert selection["exact_first_work_date_endpoints"] == {
        "enabled": True,
        "directions": ["proxy_minimum", "proxy_maximum"],
        "per_exact_date": 2,
    }
    assert policy["thresholds"] == {
        "practical_equivalence_s": 300.0,
        "practical_winner_recall_minimum": 0.9,
        "p90_normalized_shortlist_regret_maximum": 0.1,
        "failure_disqualification_recall_minimum": 0.6,
        "minimum_discriminating_case_fraction": 0.4,
        "discriminating_practical_winner_recall_minimum": 0.9,
    }


def test_v4_manifest_is_frozen_before_outcomes_with_canonical_schedules():
    manifest = _read(MANIFEST)
    assert manifest["campaign_version"] == "v4"
    assert manifest["shortlist_version"] == "stratified_shortlist_v3"
    assert manifest["status"] == "frozen_pre_outcome_design"
    assert manifest["frozen_before_outcomes"] is True
    assert manifest["outcomes_present_at_freeze"] is False
    assert manifest["outcomes_path"] is None
    assert len(manifest["cases"]) == 5
    assert sum(len(case["schedule_ids"]) for case in manifest["cases"]) == 75
    assert all(len(set(case["schedule_ids"])) == 15 for case in manifest["cases"])


def test_v4_discriminating_cases_are_pilot_backed_and_over_300_seconds():
    selection = _read(SELECTION)
    intended = [
        case for case in selection["selected_edges"]
        if case["expected_discriminating"]
    ]
    assert len(intended) == 2
    assert all(
        case["pilot"]["both_probe_starts_eligible"]
        and case["pilot"]["probe_spread_s"] > 300.0
        for case in intended
    )
    ranking_templates = [
        case for case in selection["selected_edges"]
        if case.get("expected_case_type") != "failure_only"
    ]
    assert len(intended) / len(ranking_templates) == 2 / 3


def test_v4_edges_are_disjoint_from_v1_v2_and_v3():
    selection = _read(SELECTION)
    selected = {case["edge_id"] for case in selection["selected_edges"]}
    v1 = _edges(_read(ROOT / "validation/monthly_proxy_manifest.json"))
    v2 = _edges(_read(ROOT / "validation/monthly_proxy_manifest_v2.json"))
    v3 = set(selection["disjointness"]["v3_edges"])
    assert selected.isdisjoint(v1 | v2 | v3)
    assert selection["disjointness"]["selected_edges_disjoint_from_v1_v2_v3"] is True


def test_v4_selection_and_manifest_have_distinct_frozen_identities():
    policy = _read(POLICY)
    selection = _read(SELECTION)
    manifest = _read(MANIFEST)
    assert policy["identity"]["source_identity"] == (
        "monthly-proxy-v4-stratified-shortlist-v3"
    )
    assert policy["identity"]["outcome_identity"] == "untouched-v4-only"
    assert policy["identity"]["v3_replay_status"] == (
        "diagnostic_only_not_release_evidence"
    )
    assert policy["identity"]["policy_content_key"] != (
        "7cd20362813c21cd7ea8e80b703a10d0"
    )
    assert selection["selection_content_key"] != "TO_BE_FILLED_AFTER_CANONICAL_FREEZE"
    assert manifest["content_key"] != "TO_BE_FILLED_AFTER_CANONICAL_FREEZE"
    assert manifest["policy_content_key"] == policy["identity"]["policy_content_key"]
    assert manifest["shortlist_policy_content_key"] == VALIDATION_POLICY.content_key
    assert manifest["selection_content_key"] == selection["selection_content_key"]


def test_v4_design_does_not_claim_a_release_gate():
    policy = _read(POLICY)
    manifest = _read(MANIFEST)
    assert policy["status"] == "design_frozen_pre_outcome"
    assert manifest["status"] == "frozen_pre_outcome_design"
    assert "gate_record" not in manifest
    assert "outcomes" not in manifest


REQUIRED_BOUND_SOURCES = {
    # Produce the campaign's evidence.
    "traffic_sim/simulation/monthly_proxy.py",
    "traffic_sim/simulation/proxy_validation.py",
    "traffic_sim/core/closure_calendar.py",
    "run_monthly_proxy_validation.py",
    "validation/monthly_proxy_policy_v4.json",
    "validation/heldout_v4_selection.json",
    # Enforces the frozen campaign/manifest gate at release time. Without it
    # bound, the loader could be weakened after the freeze and the manifest
    # identity would not notice.
    "traffic_sim/simulation/monthly_search.py",
}


def test_v4_recorded_enforcement_hashes_remain_frozen():
    """V4's recorded fingerprints are IMMUTABLE history, never re-synced.

    LUNA-V5-01 hardened the gate loader, so the live enforcement source no
    longer matches what v4 recorded. That drift is the POINT: v4's evidence was
    produced under the old self-certifying loader and must not silently inherit
    a licence under the new one. The recorded hashes stay exactly as frozen.
    """
    manifest = _read(MANIFEST)
    recorded = manifest["source_fingerprints"]
    assert REQUIRED_BOUND_SOURCES <= set(recorded)
    # Every recorded value is still a well-formed frozen digest...
    for relative, expected in recorded.items():
        assert isinstance(expected, str) and len(expected) == 64

    drifted = {
        relative for relative, digest in recorded.items()
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != digest
    }
    assert drifted == {
        "traffic_sim/core/closure_calendar.py",
        "run_monthly_proxy_validation.py",
        "traffic_sim/simulation/monthly_search.py",
        # 2026-08-05: the gate schema took the tolerance-field migration when
        # the ranking objective moved to vehicle-hours.
        "traffic_sim/simulation/proxy_validation.py",
    }


def test_v4_is_not_adoptable_against_the_hardened_loader():
    """V4 audit passed, but adoption was REJECTED and cannot be reinstated.

    No default artifact path can adopt v4: the product ships with neither a
    gate record nor an adoption certificate, so the loader returns None and the
    monthly path stays bounded-exhaustive.
    """
    import traffic_sim.simulation.monthly_search as monthly_search

    enforcement = "traffic_sim/simulation/monthly_search.py"
    assert Path(monthly_search.__file__).resolve() == (ROOT / enforcement)

    # The rejected v4 product candidate is gone and is not recreated.
    assert not (ROOT / "validation" / "monthly_proxy_v4_gate.json").exists()
    # Neither default adoption artifact exists, so the gate is closed.
    assert not monthly_search.HELDOUT_GATE_RECORD.exists()
    assert not monthly_search.HELDOUT_GATE_CERTIFICATE.exists()
    assert monthly_search.load_passing_heldout_gate() is None


def _ranked_candidates(count=120):
    dates = ["2027-07-15", "2027-07-16", "2027-07-17", "2027-07-18"]
    candidates = []
    for index in range(count):
        candidates.append({
            "schedule_id": f"schedule-{index:03}",
            "proxy_rank": index,
            "day_count": index % 3 + 1,
            "first_work_date": dates[index % len(dates)],
            "evidence": {"support_level": "high"},
        })
    return candidates


def test_v4_uses_the_executable_policy_and_production_manifest_validator():
    manifest = _read(MANIFEST)
    assert SHORTLIST_VERSION == "stratified_shortlist_v3"
    assert HELD_OUT_VALIDATED_SHORTLIST_POLICY == VALIDATION_POLICY
    assert HELD_OUT_VALIDATED_SHORTLIST_POLICY.maximum_shortlist == 96
    assert HELD_OUT_VALIDATED_SHORTLIST_POLICY.bounded_exhaustive_limit == 1
    assert HELD_OUT_VALIDATED_SHORTLIST_POLICY.exact_date_quantiles == (0.0, 1.0)
    assert manifest["shortlist_policy_content_key"] == (
        HELD_OUT_VALIDATED_SHORTLIST_POLICY.content_key
    )
    normalized = validate_validation_manifest(manifest)
    assert normalized["content_key"] == validation_manifest_content_key(manifest)
    assert normalized["content_key"] == manifest["content_key"]


def test_v4_release_record_preserves_frozen_shortlist_identity():
    manifest = _read(MANIFEST)
    record = gate_record_for(
        {
            "gate_status": "pass",
            "shortlist_version": manifest["shortlist_version"],
            "shortlist_policy_content_key": (
                manifest["shortlist_policy_content_key"]
            ),
            "manifest_content_key": manifest["content_key"],
        },
        manifest,
        len(manifest["cases"]),
    )
    assert record is not None
    assert record["shortlist_version"] == "stratified_shortlist_v3"
    assert record["shortlist_policy_content_key"] == (
        HELD_OUT_VALIDATED_SHORTLIST_POLICY.content_key
    )
    assert record["heldout_set"] == "v4"


def test_v4_executable_shortlist_keeps_both_exact_date_endpoints():
    result = stratified_shortlist(
        _ranked_candidates(),
        all_candidate_count=120,
        unavailable_candidate_count=0,
        policy=HELD_OUT_VALIDATED_SHORTLIST_POLICY,
    )
    assert len(result["entries"]) <= 96
    for date in {candidate["first_work_date"] for candidate in _ranked_candidates()}:
        entries = [
            entry for entry in result["entries"]
            if entry["first_work_date"] == date
        ]
        assert any(
            f"exact_date_control:{date}:q0.00" in entry["selection_reasons"]
            for entry in entries
        )
        assert any(
            f"exact_date_control:{date}:q1.00" in entry["selection_reasons"]
            for entry in entries
        )


def test_v4_executable_shortlist_caps_and_withholds_unscoreable_candidates():
    unavailable = [
        {
            "schedule_id": f"unavailable-{index:03}",
            "day_count": 1,
            "first_work_date": "2027-07-19",
        }
        for index in range(120)
    ]
    result = stratified_shortlist(
        _ranked_candidates(),
        all_candidate_count=240,
        unavailable_candidate_count=120,
        unavailable_candidates=unavailable,
        policy=HELD_OUT_VALIDATED_SHORTLIST_POLICY,
    )
    assert len(result["entries"]) == 96
    assert result["withhold_recommendation"] is True
    assert "unscoreable_candidates_exceed_shortlist_capacity" in result["withhold_reasons"]
