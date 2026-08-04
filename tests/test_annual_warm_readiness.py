import json

import pytest

from tools.freeze_annual_warm_readiness import (
    OUTPUT,
    ROOT,
    build_readiness_manifest,
)


def _stored():
    return json.loads((ROOT / OUTPUT).read_text(encoding="utf-8"))


def test_readiness_manifest_is_superseded_by_the_new_calendar_source():
    assert _stored()["status"] == "ready_for_full_population"
    with pytest.raises(
        ValueError, match="does not pass for the current plan"
    ):
        build_readiness_manifest()


def test_full_command_is_exact_unbounded_and_uses_three_approved_workers():
    manifest = _stored()
    command = manifest["commands"]["execute_full"]
    assert command == (
        "python3 tools/populate_annual_warming.py --execute --plan-key "
        f"{manifest['plan']['content_key']} --state-workers 3"
    )
    assert "--max-units" not in command
    assert "--demand-build-key" not in command
    assert manifest["preflight"]["approved_state_workers"] >= 3


def test_readiness_does_not_claim_product_activation_or_dst_coverage():
    boundary = _stored()["execution_boundary"]
    assert boundary["population_only"] is True
    assert boundary["product_activation"] is False
    assert boundary["release_or_deployment"] is False
    assert boundary["annual_artifacts_are_candidate_free_and_uncertified_for_product_reuse"] \
        is True
    assert boundary["two_dst_transition_dates_excluded"] == [
        "2027-03-28", "2027-10-31"
    ]


def test_real_population_pilot_and_empty_resume_root_are_exact():
    manifest = _stored()
    pilot = manifest["population_pilot"]
    initialized = manifest["initialized_population"]
    assert pilot["summary"]["succeeded"] == 3
    assert pilot["summary"]["failed"] == pilot["summary"]["running"] == 0
    assert {(item["variant"], item["seed"]) for item in pilot["artifacts"]} == {
        ("q10", 1000), ("q50", 1001), ("q90", 1002)
    }
    assert initialized["summary"]["pending"] == 3267
    assert initialized["summary"]["succeeded"] == 0
    assert initialized["summary"]["ledger_revision"] == 0


def test_storage_pilot_restored_exact_bytes_with_material_reduction():
    pilot = _stored()["storage_pilot"]
    assert pilot["artifact_count"] == 3
    assert pilot["physical_stored_bytes"] < pilot["unique_original_bytes"]
    assert pilot["physical_ratio"] < 0.10


def test_superseded_readiness_source_binding_cannot_appear_current():
    import hashlib

    mismatches = [
        relative
        for relative, expected in _stored()["source_fingerprints"].items()
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected
    ]
    assert "tests/test_annual_warm_readiness.py" in mismatches
