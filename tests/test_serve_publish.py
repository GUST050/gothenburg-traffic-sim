"""E2 publish-after-validate tests (PLAN.md; audit P0-2).

A recalibration must never destroy the live scenario set before a
validated replacement exists. These test the two pure helpers; the HTTP
flow wires them between the same subprocess calls as before.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import serve


def _staged(tmp_path, geh=100.0, infeasible=0, with_baseline=True,
            flows=True):
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    scen = {"name": "baseline", "file": "baseline.json"}
    index = {"scenarios": [scen] if with_baseline else []}
    (staging / "index.json").write_text(json.dumps(index))
    if with_baseline:
        (staging / "baseline.json").write_text(json.dumps(
            {"flows": {"e1": [1, 2]} if flows else {}}))
        (staging / "baseline_traj.json").write_text(json.dumps(
            {"vehicles": []}))
    meta = tmp_path / "demand_meta.json"
    meta.write_text(json.dumps({"pfe_fit": {
        "geh_pct": geh, "infeasible_intervals": infeasible,
        "vehicles": 20000}}))
    return staging, meta


class TestValidateStagedScenarios:
    def test_healthy_staging_passes(self, tmp_path):
        staging, meta = _staged(tmp_path)
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert ok, reason

    def test_missing_baseline_is_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, with_baseline=False)
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "baslinjen" in reason

    def test_empty_flows_are_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, flows=False)
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "inga flöden" in reason

    def test_geh_collapse_is_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, geh=71.3)
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "71.3" in reason

    def test_infeasible_intervals_are_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, infeasible=4)
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "olösliga" in reason

    def test_corrupt_staged_json_is_refused(self, tmp_path):
        staging, meta = _staged(tmp_path)
        (staging / "baseline.json").write_text("{not json")
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "ogiltig JSON" in reason

    def test_older_meta_without_pfe_fit_passes(self, tmp_path):
        staging, meta = _staged(tmp_path)
        meta.write_text(json.dumps({"date": "2025-09-16"}))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert ok, reason


class TestPublishStagedScenarios:
    def test_publish_replaces_live_set_and_prunes_stale(self, tmp_path):
        staging, _meta = _staged(tmp_path)
        live = tmp_path / "live"
        live.mkdir()
        (live / "index.json").write_text(json.dumps({"scenarios": [
            {"name": "old_closure", "file": "old_closure.json"}]}))
        (live / "old_closure.json").write_text(json.dumps({"flows": {}}))
        (live / "old_closure_traj.json").write_text(json.dumps({}))

        n = serve.publish_staged_scenarios(staging, live)

        assert n == 3
        live_names = {p.name for p in live.glob("*.json")}
        assert live_names == {"index.json", "baseline.json",
                              "baseline_traj.json"}
        index = json.loads((live / "index.json").read_text())
        assert index["scenarios"][0]["file"] == "baseline.json"

    def test_failed_validation_leaves_live_untouched(self, tmp_path):
        # The wiring contract: validation runs BEFORE publish; a refused
        # staging must leave the live set byte-identical.
        staging, meta = _staged(tmp_path, geh=50.0)
        live = tmp_path / "live"
        live.mkdir()
        (live / "index.json").write_text('{"scenarios": []}')
        ok, _ = serve.validate_staged_scenarios(staging, meta)
        assert not ok
        assert (live / "index.json").read_text() == '{"scenarios": []}'
