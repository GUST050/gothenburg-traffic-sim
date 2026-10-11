"""Content-addressed demand/scenario build identity tests."""

import json

import pipeline_fingerprint as pf


def test_fingerprint_changes_when_an_artifact_changes(tmp_path):
    artifact = tmp_path / "routes.rou.xml"
    source = tmp_path / "builder.py"
    artifact.write_text("<routes>one</routes>")
    source.write_text("version = 1")

    first = pf.make_fingerprint(
        contract={"date": "2025-09-16"},
        artifacts={"routes": artifact}, source_files={"builder": source})
    artifact.write_text("<routes>two</routes>")
    second = pf.make_fingerprint(
        contract={"date": "2025-09-16"},
        artifacts={"routes": artifact}, source_files={"builder": source})

    assert first["build_id"] != second["build_id"]
    assert first["artifacts"]["routes"]["sha256"] != \
        second["artifacts"]["routes"]["sha256"]


def test_missing_artifacts_are_explicit_and_stable(tmp_path):
    missing = tmp_path / "does-not-exist"
    record = pf.make_fingerprint(
        contract={"n": 1}, artifacts={"missing": missing}, source_files={})
    assert record["artifacts"]["missing"] == {"sha256": None, "bytes": None}
    json.dumps(record, sort_keys=True)


def test_fingerprint_binds_the_solver_runtime(tmp_path, monkeypatch):
    """scipy 1.13.1 -> 1.18.1 (HiGHS 1.2 -> 1.12) changed the calibrated
    population of one build from 20486 to 20342 vehicles with the same
    code, inputs and seed (validation/demand_count_difference_investigation_
    20261010.json): a build identity that ignores the solver is not one."""
    from importlib import metadata
    record = pf.make_fingerprint(contract={"n": 1}, artifacts={},
                                 source_files={})
    assert record["solver_runtime"]["scipy"] == metadata.version("scipy")
    assert set(record["solver_runtime"]) == {"numba", "numpy", "scipy"}

    import traffic_sim.core.fingerprint as fingerprint
    real = fingerprint.solver_runtime()
    monkeypatch.setattr(fingerprint, "solver_runtime",
                        lambda: {**real, "scipy": "1.13.1"})
    other = pf.make_fingerprint(contract={"n": 1}, artifacts={},
                                source_files={})
    assert other["build_id"] != record["build_id"]
