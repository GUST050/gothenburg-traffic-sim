"""Lifecycle and bind-gate checks for frozen, unapproved v14. Process-free."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

MANIFEST = Path("validation/monthly_warm_state_manifest_v14.json")
PARENT = Path("validation/monthly_warm_state_manifest_v13.json")
TOOL = Path("tools/freeze_monthly_warm_state_v14.py")
PARENT_KEY = "0c8d42eb828c24e398acc3b642b4750c732addc2321db0e85935d015fe9eac77"

FROZEN_V13 = {
    "tools/freeze_monthly_warm_state_v13.py":
        "9bfae44e3ff31825ef72640c8597f3477b3a4a025d13955865ed317d46325de0",
    "validation/monthly_warm_state_manifest_v13.json":
        "589670db1c462930be7a862a2fe0cc4fad694af61bffb765c1ed1fc2a768f76d",
}
FROZEN_V14 = {
    "tools/freeze_monthly_warm_state_v14.py":
        "414968d0e24efa9a9706cb6946d901a4b0405db44ff950cea53877de66c07707",
    "validation/monthly_warm_state_manifest_v14.json":
        "d2236864b8e30a7e1d56c074871e2fda5fc879b79fa11dcf890732ab376dbe24",
}


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
    import freeze_monthly_warm_state_v14 as module
    return module


def _harness():
    spec = importlib.util.spec_from_file_location(
        "warm_harness_v14", "run_monthly_warm_state_validation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFrozenIdentity:

    def test_content_key_and_frozen_bytes_remain_intact(self):
        manifest = _load()
        assert _key(manifest) == manifest["content_key"]
        for name, digest in FROZEN_V14.items():
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest

    @pytest.mark.parametrize("name,digest", sorted(FROZEN_V13.items()))
    def test_v13_tool_and_manifest_are_byte_identical(self, name, digest):
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest

    def test_v14_inherits_the_exact_v13_experiment_and_mechanism(self):
        manifest, parent = _load(), _load(PARENT)
        assert manifest["inherited_from"]["content_key"] == PARENT_KEY
        assert parent["content_key"] == PARENT_KEY
        for field in (
                "archive_files_sha256", "frozen_schedule_ids", "seeds",
                "demand_variants", "comparison_policy", "demand_requirement",
                "network_requirement", "spec_template",
                "meso_accumulator_reconstruction", "resumed_execution_contract",
                "warm_attempt_contract"):
            assert manifest[field] == parent[field], field
        for field in ("directed_edges", "closure_begin_s", "closure_end_s",
                      "closure_bound_warm_point_s"):
            assert manifest["cases"][0][field] == parent["cases"][0][field]

    def test_v14_is_superseded_by_bound_source_drift(self):
        drifted = {
            name for name, digest in _load()["source_fingerprints"].items()
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest}
        assert "traffic_sim/simulation/warm_state_boundary.py" in drifted
        assert "tests/test_warm_state_boundary.py" in drifted


class TestReviewedV13Disposition:

    def test_environment_failure_is_recorded_without_claim_inflation(self):
        review = _load()["v13_review"]
        assert review == {
            "bytes_preserved": True,
            "cache_published": False,
            "campaign": "v13",
            "claim": ("environmental failure only; the fallback pairs prove "
                      "neither warm equivalence nor warm performance"),
            "content_key": PARENT_KEY,
            "disposition": "executed_environment_blocked_no_cache",
            "failure": "PermissionError: [Errno 1] Operation not permitted",
            "failure_location": (
                "WarmPrefixController._free_port before prefix launch"),
            "permission_denied_cold_fallbacks": 3,
            "required_identities": 3,
            "semantic_mismatches": 0,
            "valid_warm_executions": 0,
        }

    def test_bind_gate_is_exact_and_precedes_root(self):
        contract = _load()["localhost_bind_preflight"]
        assert contract["required"] is True
        assert contract["family"] == "AF_INET"
        assert contract["socket_type"] == "SOCK_STREAM"
        assert contract["address"] == ["127.0.0.1", 0]
        assert contract["ordering"] == [
            "approval_token", "production_traci_origin_api",
            "ipv4_tcp_loopback_bind", "keyed_root_absence",
            "paired_campaign"]
        assert "before keyed-root" in contract["on_failure"]


class TestLifecycle:

    def test_v14_is_default_off_unapproved_and_unexecuted(self):
        manifest = _load()
        assert manifest["campaign_version"] == "v14"
        assert manifest["status"] == "frozen_unapproved_unexecuted"
        assert manifest["warming_default"].startswith("OFF")
        assert "approved_content_key" not in manifest
        assert manifest["hypothesis_under_test"]["status"] == \
            "UNPROVEN — v14 is unapproved and unexecuted"

    def test_harness_no_longer_points_to_or_loads_v14(self):
        harness = _harness()
        assert harness.DEFAULT_MANIFEST != MANIFEST
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(MANIFEST)

    def test_execution_is_refused_on_source_drift_before_approval(self):
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            _harness().main(["--manifest", str(MANIFEST), "--execute"])

    def test_v13_approval_token_cannot_authorize_v14(self):
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
