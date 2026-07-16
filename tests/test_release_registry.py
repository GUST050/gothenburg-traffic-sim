import json

import pytest

import release_registry as rr


def test_release_is_content_addressed_and_can_be_activated_and_rolled_back(tmp_path):
    source = tmp_path / "case.json"
    source.write_text('{"ok": true}\n')
    manifest = rr.create_release("r1", {"normal": source}, root=tmp_path / "releases")
    assert manifest["status"] == "staged"
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
