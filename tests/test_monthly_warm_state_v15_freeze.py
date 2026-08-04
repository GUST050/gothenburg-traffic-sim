"""Lifecycle and boundary-transport checks for frozen v15. Process-free."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

MANIFEST = Path("validation/monthly_warm_state_manifest_v15.json")
PARENT = Path("validation/monthly_warm_state_manifest_v14.json")
PARENT_KEY = "76ebb43577b9ef91a4e2c5b8504ee11bab960f560fa64f3fca4d0c1a27fd082c"
FROZEN_V14 = (
    ("tools/freeze_monthly_warm_state_v14.py",
     "414968d0e24efa9a9706cb6946d901a4b0405db44ff950cea53877de66c07707"),
    ("validation/monthly_warm_state_manifest_v14.json",
     "d2236864b8e30a7e1d56c074871e2fda5fc879b79fa11dcf890732ab376dbe24"),
)
FROZEN_V15 = (
    ("tools/freeze_monthly_warm_state_v15.py",
     "9bdb13eb3a2855acdfe62bedb611b90cc37e965ca5cfda2ec289141b2ddb8e9c"),
    ("validation/monthly_warm_state_manifest_v15.json",
     "e55c0c75f304f5b4be9d74ee569b0de4bb940dd3234b733bbf796e74ec65b238"),
)


def _load(path=MANIFEST):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _key(payload):
    body = {key: value for key, value in payload.items()
            if key != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def _freeze():
    sys.path.insert(0, "tools")
    import freeze_monthly_warm_state_v15 as module
    return module


def _harness():
    spec = importlib.util.spec_from_file_location(
        "warm_harness_v15", "run_monthly_warm_state_validation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFrozenIdentity:

    def test_content_key_and_frozen_bytes_remain_intact(self):
        manifest = _load()
        assert _key(manifest) == manifest["content_key"]
        for name, digest in FROZEN_V15:
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest

    @pytest.mark.parametrize("name,digest", FROZEN_V14)
    def test_v14_tool_and_manifest_are_byte_identical(self, name, digest):
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest

    def test_v15_inherits_the_exact_v14_physical_experiment(self):
        manifest, parent = _load(), _load(PARENT)
        assert manifest["inherited_from"]["content_key"] == PARENT_KEY
        assert parent["content_key"] == PARENT_KEY
        for field in (
                "archive_files_sha256", "frozen_schedule_ids", "seeds",
                "demand_variants", "comparison_policy", "demand_requirement",
                "network_requirement", "spec_template",
                "resumed_execution_contract", "warm_attempt_contract",
                "localhost_bind_preflight"):
            assert manifest[field] == parent[field], field
        for field in ("directed_edges", "closure_begin_s", "closure_end_s",
                      "closure_bound_warm_point_s"):
            assert manifest["cases"][0][field] == parent["cases"][0][field]

    def test_v15_is_superseded_by_bound_source_drift(self):
        drifted = {
            name for name, digest in _load()["source_fingerprints"].items()
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest}
        assert "traffic_sim/simulation/warm_state_boundary.py" in drifted
        assert "tests/test_warm_state_boundary.py" in drifted


class TestBoundaryTransportContract:

    def test_manifest_binds_the_new_identity_schemas_and_native_step(self):
        contract = _load()["meso_accumulator_reconstruction"]
        assert contract["boundary_active_schema"] == "warm_boundary_active_v2"
        assert contract["prefix_accumulator_schema"] == \
            "warm_prefix_meso_accumulator_v2"
        assert contract["transport_flush"] == {
            "duration": "exact traci.simulation.getDeltaT()",
            "post_boundary_records": "excluded unless captured active",
            "prefix_process_end": "warm point plus one native step",
            "saved_state_time": "unchanged exact warm point",
        }
        assert contract["tolerance"] == 0.0
        assert contract["traci_time_loss_used"] is False

    def test_successor_removes_the_refuted_transport_step(self):
        source = Path(
            "traffic_sim/simulation/warm_state_boundary.py").read_text()
        start = source.index("def _connected_phase")
        end = source.index("def _restore_phase", start)
        phase = source[start:end]
        assert phase.index("capture_boundary_active(") < \
            phase.index("simulation.saveState(")
        assert "simulationStep(flush_end)" not in phase
        assert "getDeltaT()" not in phase
        assert ".getTimeLoss(" not in phase

    def test_review_records_only_the_observed_v14_mismatch(self):
        review = _load()["v14_review"]
        assert review["disposition"] == "executed_semantic_mismatch_no_cache"
        assert review["valid_warm_executions"] == 3
        assert review["semantic_mismatches"] == 3
        assert review["only_mismatched_field"] == \
            "candidate.metrics.total_time_loss_s"
        assert review["cold_minus_warm_s"] == {
            "q10": 21.73, "q50": 23.10, "q90": 24.94}
        assert review["cache_published"] is False


class TestLifecycle:

    def test_v15_is_default_off_unapproved_and_unexecuted(self):
        manifest = _load()
        assert manifest["campaign_version"] == "v15"
        assert manifest["status"] == "frozen_unapproved_unexecuted"
        assert manifest["warming_default"].startswith("OFF")
        assert "approved_content_key" not in manifest
        assert manifest["hypothesis_under_test"]["status"] == \
            "UNPROVEN — v15 is unapproved and unexecuted"

    def test_harness_no_longer_points_to_or_loads_v15(self):
        harness = _harness()
        assert harness.DEFAULT_MANIFEST != MANIFEST
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(MANIFEST)

    def test_execution_is_refused_on_source_drift_before_approval(self):
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            _harness().main(["--manifest", str(MANIFEST), "--execute"])

    def test_v14_approval_token_cannot_authorize_v15(self):
        harness = _harness()
        with pytest.raises(SystemExit, match="approval token does not match"):
            harness.require_approval(_load(), PARENT_KEY)

    def test_checks_only_mode_refuses_the_superseded_manifest(self):
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            _harness().main(["--manifest", str(MANIFEST)])

    def test_versioned_suite_does_not_pin_a_predecessor_test(self):
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        assert not (key.value.startswith("tests/")
                                    and key.value.endswith("_freeze.py"))
