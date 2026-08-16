"""Content-addressed demand/scenario build identity tests."""

import json

from traffic_sim.core import fingerprint as pf


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
