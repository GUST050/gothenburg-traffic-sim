"""Frozen v16 formatter-correction and lifecycle checks. Process-free."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from traffic_sim.simulation.warm_state_boundary import normalize_time_loss

MANIFEST = Path("validation/monthly_warm_state_manifest_v16.json")
PARENT = Path("validation/monthly_warm_state_manifest_v15.json")
PARENT_KEY = "b8af525102482bf29a4e64bf80d962ee6e6014c37f9d07ae04ee082112910700"
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
    import freeze_monthly_warm_state_v16 as module
    return module


def _harness():
    spec = importlib.util.spec_from_file_location(
        "warm_harness_v16", "run_monthly_warm_state_validation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFrozenIdentity:

    def test_content_key_recomputes_while_retired_sources_do_not_recompose(self):
        manifest = _load()
        before = MANIFEST.read_bytes()
        assert _key(manifest) == manifest["content_key"]
        assert _freeze().build_artifacts()[str(MANIFEST)] != \
            MANIFEST.read_text(encoding="utf-8")
        assert MANIFEST.read_bytes() == before

    @pytest.mark.parametrize("name,digest", FROZEN_V15)
    def test_v15_tool_and_manifest_are_byte_identical(self, name, digest):
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest

    def test_v16_inherits_the_exact_v15_physical_experiment(self):
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

    def test_bound_source_drift_retires_v16_without_rewriting_it(self):
        drifted = sorted(
            name for name, digest in _load()["source_fingerprints"].items()
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest)
        assert drifted
        assert "run_scenario.py" in drifted


class TestFormatterCorrection:

    @pytest.mark.parametrize("raw,expected", [
        (36.555, 36.56), (151.545, 151.55), (1.005, 1.01),
        (2.675, 2.68), (0.004, 0.0),
    ])
    def test_live_normalizer_matches_sumo_half_up_ties(self, raw, expected):
        assert normalize_time_loss(raw) == expected

    def test_manifest_records_the_complete_q10_localization(self):
        review = _load()["v15_review"]
        assert review["q10_forensics"] == {
            "diagnostic_only": True,
            "per_vehicle_delta_s": -0.01,
            "population_partition_exact": True,
            "sum_delta_s": -21.73,
            "time_loss_mismatch_vehicle_count": 2173,
        }
        assert review["cache_published"] is False

    def test_manifest_binds_no_post_save_step_and_new_schemas(self):
        contract = _load()["meso_accumulator_reconstruction"]
        assert contract["boundary_active_schema"] == "warm_boundary_active_v3"
        assert contract["prefix_accumulator_schema"] == \
            "warm_prefix_meso_accumulator_v3"
        assert contract["post_save_simulation_step"] is False
        assert contract["normalization"]["python_builtin_round_used"] is False
        assert contract["tolerance"] == 0.0

    def test_controller_saves_at_the_boundary_and_never_looks_ahead(self):
        source = Path(
            "traffic_sim/simulation/warm_state_boundary.py").read_text()
        start = source.index("def _connected_phase")
        end = source.index("def _restore_phase", start)
        phase = source[start:end]
        assert phase.index("capture_boundary_active(") < \
            phase.index("simulation.saveState(")
        assert "simulationStep(flush_end)" not in phase
        assert "getDeltaT()" not in phase


class TestLifecycle:

    def test_v16_is_default_off_unapproved_and_unexecuted(self):
        manifest = _load()
        assert manifest["campaign_version"] == "v16"
        assert manifest["status"] == "frozen_unapproved_unexecuted"
        assert manifest["warming_default"].startswith("OFF")
        assert "approved_content_key" not in manifest

    def test_harness_still_points_to_but_refuses_retired_v16(self):
        harness = _harness()
        assert harness.DEFAULT_MANIFEST == MANIFEST
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(MANIFEST)

    def test_execution_is_refused_before_runtime_without_token(self):
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            _harness().main(["--manifest", str(MANIFEST), "--execute"])

    def test_v15_approval_token_cannot_authorize_v16(self):
        with pytest.raises(SystemExit, match="approval token does not match"):
            _harness().require_approval(_load(), PARENT_KEY)

    def test_checks_only_mode_is_process_free(self):
        before = set(sys.modules)
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            _harness().main(["--manifest", str(MANIFEST)])
        assert not ({"traci", "libsumo"} & (set(sys.modules) - before))

    def test_versioned_suite_does_not_pin_a_predecessor_test(self):
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        assert not (key.value.startswith("tests/")
                                    and key.value.endswith("_freeze.py"))
