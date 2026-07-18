import json

import pytest

from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
from traffic_sim.simulation.search_workspace import (
    create_search_workspace,
    load_search_workspace,
    verify_search_workspace,
)


def _spec(search_id="search-july"):
    return ClosureSearchSpec(
        search_id=search_id,
        directed_edges=("edge-a",),
        demand_build_id="demand-build",
        source="forecast",
        permitted_date_start="2027-07-01",
        permitted_date_end="2027-07-31",
        required_work_minutes=30 * 60,
        max_consecutive_start_days=7,
        permitted_daily_band=DailyTimeBand("07:00", "19:00"),
    )


def test_workspace_is_exclusive_and_stores_exact_input(tmp_path):
    workspace = create_search_workspace(_spec(), root=tmp_path)

    assert workspace.directory == tmp_path / "search-july"
    assert workspace.scratch_dir.is_dir()
    assert workspace.artifacts_dir.is_dir()
    assert json.loads(workspace.spec_path.read_text())["content_key"] == (
        _spec().content_key)
    assert verify_search_workspace(workspace) == []

    with pytest.raises(FileExistsError):
        create_search_workspace(_spec(), root=tmp_path)


def test_artifact_is_copied_once_with_explicit_provenance(tmp_path):
    workspace = create_search_workspace(_spec(), root=tmp_path / "runs")
    source = tmp_path / "result.json"
    source.write_text('{"winner":"schedule-a"}\n')

    published = workspace.publish_artifact(
        source,
        "decisions/result.json",
        kind="decision_result",
        provenance={"scenario_id": "schedule-a", "evidence": "sumo"},
    )
    source.write_text('{"winner":"mutated"}\n')

    assert json.loads(published.read_text())["winner"] == "schedule-a"
    assert verify_search_workspace(workspace) == []
    with pytest.raises(FileExistsError):
        workspace.publish_artifact(
            source,
            "decisions/result.json",
            kind="decision_result",
            provenance={"scenario_id": "schedule-a"},
        )


@pytest.mark.parametrize("name", ["../escape.json", "/tmp/escape.json", "a/../../b"])
def test_artifact_path_cannot_escape_workspace(tmp_path, name):
    workspace = create_search_workspace(_spec(), root=tmp_path / "runs")
    source = tmp_path / "result.json"
    source.write_text("{}")

    with pytest.raises(ValueError, match="inside the workspace"):
        workspace.publish_artifact(
            source, name, kind="test", provenance={"source": "test"})


def test_cancel_removes_only_scratch_and_retains_immutable_artifacts(tmp_path):
    outside = tmp_path / "active_release.json"
    outside.write_text('{"release_id":"golden"}\n')
    workspace = create_search_workspace(_spec(), root=tmp_path / "searches")
    source = tmp_path / "ledger.json"
    source.write_text('{"candidates":[]}\n')
    published = workspace.publish_artifact(
        source, "candidate-ledger.json", kind="candidate_ledger",
        provenance={"search_content_key": _spec().content_key})
    (workspace.scratch_dir / "temporary.xml").write_text("scratch")

    workspace.finish("cancelled", keep_scratch=True)

    assert not workspace.scratch_dir.exists()
    assert published.is_file()
    assert outside.read_text() == '{"release_id":"golden"}\n'
    assert load_search_workspace(workspace.directory).status == "cancelled"


def test_failure_preserves_scratch_for_diagnosis(tmp_path):
    workspace = create_search_workspace(_spec(), root=tmp_path)
    evidence = workspace.scratch_dir / "sumo.stderr"
    evidence.write_text("failed")

    workspace.finish("failed", error="SUMO failed")

    loaded = load_search_workspace(workspace.directory)
    assert loaded.status == "failed"
    assert evidence.read_text() == "failed"
    assert loaded.manifest["error"] == "SUMO failed"


def test_success_verifies_integrity_then_becomes_immutable(tmp_path):
    workspace = create_search_workspace(_spec(), root=tmp_path)

    workspace.finish("succeeded")

    assert not workspace.scratch_dir.exists()
    assert load_search_workspace(workspace.directory).status == "succeeded"
    with pytest.raises(RuntimeError, match="already"):
        workspace.finish("succeeded")


def test_input_or_artifact_tampering_fails_closed(tmp_path):
    workspace = create_search_workspace(_spec(), root=tmp_path)
    source = tmp_path / "result.json"
    source.write_text('{"status":"pass"}')
    artifact = workspace.publish_artifact(
        source, "result.json", kind="result",
        provenance={"search_content_key": _spec().content_key})
    artifact.write_text('{"status":"tampered"}')

    with pytest.raises(ValueError, match="artifact digest"):
        load_search_workspace(workspace.directory)

    artifact.write_text('{"status":"pass"}')
    workspace.spec_path.write_text("{}")
    with pytest.raises(ValueError, match="input"):
        load_search_workspace(workspace.directory)


def test_unledgered_artifact_blocks_successful_publication(tmp_path):
    workspace = create_search_workspace(_spec(), root=tmp_path)
    (workspace.artifacts_dir / "orphan.json").write_text("{}")

    assert verify_search_workspace(workspace) == [
        "workspace artifact is not in the ledger: artifacts/orphan.json"]
    with pytest.raises(ValueError, match="not in the ledger"):
        workspace.finish("succeeded")
