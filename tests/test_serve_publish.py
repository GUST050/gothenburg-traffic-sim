"""E2 publish-after-validate tests (IMPROVEMENT_PLAN.md; audit P0-2).

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
            flows=True, build_id=None, variant_fit=None, demand_key=None):
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    scen = {"name": "baseline", "file": "baseline.json"}
    index = {"scenarios": [scen] if with_baseline else []}
    (staging / "index.json").write_text(json.dumps(index))
    if with_baseline:
        payload = {"flows": {"e1": [1, 2]} if flows else {}}
        if build_id:
            payload["scenario"] = {"build_id": build_id}
            if demand_key:
                payload["scenario"]["demand_build_key"] = demand_key
        (staging / "baseline.json").write_text(json.dumps(payload))
        (staging / "baseline_traj.json").write_text(json.dumps(
            {"vehicles": []}))
    meta = tmp_path / "demand_meta.json"
    meta_payload = {"pfe_fit": {
        "geh_pct": geh, "infeasible_intervals": infeasible,
        "vehicles": 20000}}
    if build_id:
        meta_payload["build_id"] = build_id
    if demand_key:
        meta_payload["demand_build_key"] = demand_key
        meta_payload["demand_spec"] = {"build_key": demand_key}
    if variant_fit is not None:
        meta_payload["pfe_fit_variants"] = variant_fit
    meta.write_text(json.dumps(meta_payload))
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

    def test_build_id_mismatch_is_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, build_id="new-build")
        (staging / "baseline.json").write_text(json.dumps({
            "flows": {"e1": [1, 2]},
            "scenario": {"build_id": "old-build"},
        }))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "build-ID" in reason

    def test_demand_build_key_mismatch_is_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, build_id="new-build",
                                demand_key="new-demand")
        (staging / "baseline.json").write_text(json.dumps({
            "flows": {"e1": [1, 2]},
            "scenario": {"build_id": "new-build",
                          "demand_build_key": "old-demand"},
        }))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "demand-build" in reason

    def test_each_uncertainty_variant_is_gated(self, tmp_path):
        staging, meta = _staged(
            tmp_path,
            variant_fit={
                "edge_shares": {"geh_pct": 100, "infeasible_intervals": 0},
                "edge_shares_q10": {"geh_pct": 98.0, "infeasible_intervals": 0},
            })
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "edge_shares_q10" in reason


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
