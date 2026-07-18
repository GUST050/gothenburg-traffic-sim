import json

import pytest

import release_registry as rr


def test_release_is_content_addressed_and_can_be_activated_and_rolled_back(tmp_path):
    source = tmp_path / "case.json"
    source.write_text('{"ok": true}\n')
    manifest = rr.create_release("r1", {"normal": source}, root=tmp_path / "releases")
    assert manifest["status"] == "staged"
    assert manifest["cases"]["normal"]["artifacts"][0]["file"] == "case.json"
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
    artifact = root / "r1" / "case.json"
    artifact.write_text("tampered")
    assert rr.validate_release("r1", root=root)
    with pytest.raises(ValueError, match="integrity"):
        rr.mark_validated("r1", {}, root=root)


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
        "scenario.json", "trajectory.json"]
    assert rr.validate_release("r1", root=root) == []
    (root / "r1" / "trajectory.json").write_text("changed")
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
    rr.mark_validated("golden", {
        "cases": {case: {"status": "pass"} for case in rr.GOLDEN_CASES}
    }, root=root)
    assert rr.validate_golden_release("golden", root=root) == []


def test_golden_activation_refuses_incomplete_release(tmp_path):
    root = tmp_path / "releases"
    source = tmp_path / "normal.json"
    source.write_text("normal")
    rr.create_release("r1", {"normal": source}, root=root)
    rr.mark_validated("r1", {}, root=root)
    with pytest.raises(ValueError, match="golden release validation"):
        rr.activate_golden_release("r1", root=root)


def test_release_rejects_duplicate_artifact_names_and_path_traversal(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text("a")
    b.write_text("b")
    root = tmp_path / "releases"
    with pytest.raises(ValueError, match="duplicate filename"):
        rr.create_release("r1", {"a": a, "b": a}, root=root)
    with pytest.raises(ValueError, match="single path component"):
        rr.create_release("../r1", {"a": a}, root=root)
