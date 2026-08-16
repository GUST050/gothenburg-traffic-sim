import json

import pytest

from traffic_sim.ops import releases as rr


def golden_validation():
    return {
        "status": "pass",
        "cases": {case: {"status": "pass"} for case in rr.GOLDEN_CASES},
        "acceptance_gates": {
            gate: {"status": "pass"} for gate in rr.GOLDEN_ACCEPTANCE_GATES
        },
    }


def create_validated_golden(release_id, tmp_path, root):
    paths = {}
    for case in rr.GOLDEN_CASES:
        path = tmp_path / f"{release_id}-{case}.json"
        path.write_text(json.dumps({"release": release_id, "case": case}))
        paths[case] = path
    rr.create_release(release_id, paths, root=root)
    rr.mark_validated(release_id, golden_validation(), root=root)


def test_release_is_content_addressed_and_can_be_activated_and_rolled_back(tmp_path):
    source = tmp_path / "case.json"
    source.write_text('{"ok": true}\n')
    manifest = rr.create_release("r1", {"normal": source}, root=tmp_path / "releases")
    assert manifest["status"] == "staged"
    assert manifest["cases"]["normal"]["artifacts"][0]["file"] == "normal/case.json"
    assert rr.validate_release("r1", root=tmp_path / "releases") == []
    rr.mark_validated("r1", {"all_gates": True}, root=tmp_path / "releases")
    rr.activate_release("r1", root=tmp_path / "releases")
    assert rr.active_release(root=tmp_path / "releases")["release_id"] == "r1"

    source2 = tmp_path / "case2.json"
    source2.write_text('{"ok": 2}\n')
    rr.create_release("r2", {"normal": source2}, root=tmp_path / "releases")
    rr.mark_validated("r2", {}, root=tmp_path / "releases")
    rr.activate_release("r2", root=tmp_path / "releases")
    rr.rollback_release(root=tmp_path / "releases")
    assert rr.active_release(root=tmp_path / "releases")["release_id"] == "r1"


def test_release_integrity_and_publication_gates(tmp_path):
    source = tmp_path / "case.json"
    source.write_text("payload")
    root = tmp_path / "releases"
    rr.create_release("r1", {"normal": source}, root=root)
    with pytest.raises(ValueError, match="validated"):
        rr.activate_release("r1", root=root)
    artifact = root / "r1" / "normal" / "case.json"
    artifact.write_text("tampered")
    assert rr.validate_release("r1", root=root)
    with pytest.raises(ValueError, match="integrity"):
        rr.mark_validated("r1", {}, root=root)


def test_staged_validation_progress_does_not_validate_release(tmp_path):
    source = tmp_path / "case.json"
    source.write_text("payload")
    root = tmp_path / "releases"
    rr.create_release("r1", {"normal": source}, root=root)

    manifest = rr.update_staged_validation(
        "r1", {"status": "pending", "measured": True}, root=root)

    assert manifest["status"] == "staged"
    assert manifest["validation"]["measured"] is True
    with pytest.raises(ValueError, match="validated"):
        rr.activate_release("r1", root=root)
    rr.mark_validated("r1", {"status": "pass"}, root=root)
    with pytest.raises(ValueError, match="only while staged"):
        rr.update_staged_validation("r1", {}, root=root)


def test_release_case_bundle_copies_and_validates_every_artifact(tmp_path):
    scenario = tmp_path / "scenario.json"
    trajectory = tmp_path / "trajectory.json"
    scenario.write_text("scenario")
    trajectory.write_text("trajectory")
    root = tmp_path / "releases"

    manifest = rr.create_release(
        "r1", {"normal": [scenario, trajectory]}, root=root)

    artifacts = manifest["cases"]["normal"]["artifacts"]
    assert [artifact["file"] for artifact in artifacts] == [
        "normal/scenario.json", "normal/trajectory.json"]
    assert rr.validate_release("r1", root=root) == []
    (root / "r1" / "normal" / "trajectory.json").write_text("changed")
    assert rr.validate_release("r1", root=root) == [
        "normal/trajectory.json: size changed",
        "normal/trajectory.json: sha256 changed",
    ]


def test_release_validation_accepts_legacy_single_file_manifest(tmp_path):
    root = tmp_path / "releases"
    directory = root / "legacy"
    directory.mkdir(parents=True)
    artifact = directory / "case.json"
    artifact.write_text("legacy")
    (directory / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "release_id": "legacy",
        "cases": {
            "normal": {
                "file": artifact.name,
                "bytes": artifact.stat().st_size,
                "sha256": rr.sha256_file(artifact),
            }
        },
    }))

    assert rr.validate_release("legacy", root=root) == []


def test_golden_release_requires_all_cases_and_per_case_pass_status(tmp_path):
    root = tmp_path / "releases"
    paths = {}
    for case in rr.GOLDEN_CASES:
        path = tmp_path / f"{case}.json"
        path.write_text(json.dumps({"case": case}))
        paths[case] = path
    rr.create_release("golden", paths, root=root)
    assert any("per-case validation" in error
               for error in rr.validate_golden_release("golden", root=root))
    rr.mark_validated("golden", golden_validation(), root=root)
    assert rr.validate_golden_release("golden", root=root) == []


def test_golden_release_rejects_pending_acceptance_gate(tmp_path):
    root = tmp_path / "releases"
    paths = {}
    for case in rr.GOLDEN_CASES:
        path = tmp_path / f"{case}.json"
        path.write_text(json.dumps({"case": case}))
        paths[case] = path
    validation = {
        "status": "pending_acceptance_gates",
        "cases": {case: {"status": "pass"} for case in rr.GOLDEN_CASES},
        "acceptance_gates": {
            gate: {"status": "pass"} for gate in rr.GOLDEN_ACCEPTANCE_GATES
        },
    }
    validation["acceptance_gates"]["browser_api_smoke"]["status"] = "pending"
    rr.create_release("golden", paths, validation=validation, root=root)

    errors = rr.validate_golden_release("golden", root=root)

    assert "golden release validation status is not passing" in errors
    assert ("golden acceptance gate is not passing: browser_api_smoke"
            in errors)


def test_golden_activation_refuses_incomplete_release(tmp_path):
    root = tmp_path / "releases"
    source = tmp_path / "normal.json"
    source.write_text("normal")
    rr.create_release("r1", {"normal": source}, root=root)
    rr.mark_validated("r1", {}, root=root)
    with pytest.raises(ValueError, match="golden release validation"):
        rr.activate_golden_release("r1", root=root)


def test_golden_rollback_revalidates_the_previous_release(tmp_path):
    root = tmp_path / "releases"
    create_validated_golden("r1", tmp_path, root)
    create_validated_golden("r2", tmp_path, root)
    rr.activate_golden_release("r1", root=root)
    rr.activate_golden_release("r2", root=root)

    rr.rollback_golden_release(root=root)

    pointer = rr.active_release(root=root)
    assert pointer["release_id"] == "r1"
    assert pointer["previous_release_id"] == "r2"

    r2_artifact = root / "r2" / "normal" / "r2-normal.json"
    r2_artifact.write_text("tampered")
    with pytest.raises(ValueError, match="golden release validation failed"):
        rr.rollback_golden_release(root=root)
    assert rr.active_release(root=root)["release_id"] == "r1"


def test_release_rejects_duplicate_artifact_names_and_path_traversal(tmp_path):
    a = tmp_path / "a.json"
    a.write_text("a")
    root = tmp_path / "releases"
    with pytest.raises(ValueError, match="duplicate filename"):
        rr.create_release("r1", {"normal": [a, a]}, root=root)
    with pytest.raises(ValueError, match="single path component"):
        rr.create_release("../r1", {"a": a}, root=root)
