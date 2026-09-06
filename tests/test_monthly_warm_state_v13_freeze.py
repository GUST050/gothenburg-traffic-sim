"""Lifecycle and mechanism checks for frozen, unapproved v13. Process-free."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

MANIFEST = Path("validation/monthly_warm_state_manifest_v13.json")
PARENT = Path("validation/monthly_warm_state_manifest_v12.json")
TOOL = Path("tools/freeze_monthly_warm_state_v13.py")

FROZEN_V12 = {
    "tools/freeze_monthly_warm_state_v12.py":
        "a8874b73026f4a21749001e86a9556158ecaa400ce9b7def10fe7d36e343253b",
    "validation/monthly_warm_state_manifest_v12.json":
        "49becf24e37b9f30c4e42800ce89a9ac14a60bad76526851252049a13e955a69",
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
    import freeze_monthly_warm_state_v13 as module
    return module


def _harness():
    spec = importlib.util.spec_from_file_location(
        "warm_harness_v13", "run_monthly_warm_state_validation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFrozenIdentity:

    def test_frozen_content_key_recomputes_but_superseded_sources_do_not(self):
        manifest = _load()
        assert _key(manifest) == manifest["content_key"]
        assert _freeze().build_artifacts()[str(MANIFEST)] != \
            MANIFEST.read_text(encoding="utf-8")

    @pytest.mark.parametrize("name,digest", sorted(FROZEN_V12.items()))
    def test_v12_tool_and_manifest_are_byte_identical(self, name, digest):
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest

    def test_v13_inherits_the_exact_v12_physical_case(self):
        manifest, parent = _load(), _load(PARENT)
        assert manifest["inherited_from"]["content_key"] == parent["content_key"]
        assert manifest["archive_files_sha256"] == parent["archive_files_sha256"]
        assert manifest["frozen_schedule_ids"] == parent["frozen_schedule_ids"]
        assert manifest["seeds"] == parent["seeds"]
        assert manifest["demand_variants"] == parent["demand_variants"]
        for field in ("directed_edges", "closure_begin_s", "closure_end_s",
                      "closure_bound_warm_point_s"):
            assert manifest["cases"][0][field] == parent["cases"][0][field]

    def test_v12_failure_is_recorded_without_reinterpretation(self):
        review = _load()["v12_review"]
        assert review["disposition"] == "executed_refuted_no_cache"
        assert review["residual_cold_minus_warm_s"] == [
            7.730000004, 80.620000002, 138.970000003]
        assert review["cache_published"] is False


class TestSourceEstablishedMechanism:

    def test_frozen_schemas_and_precisions_are_bound(self):
        manifest = _load()
        mechanism = manifest["meso_accumulator_reconstruction"]
        assert manifest["prefix_evidence_schema"] == "monthly_prefix_evidence_v7"
        assert manifest["cache_schema_version"] == 2
        assert mechanism["boundary_active_schema"] == "warm_boundary_active_v1"
        assert mechanism["prefix_accumulator_schema"] == \
            "warm_prefix_meso_accumulator_v1"
        assert mechanism["reconciliation_schema"] == \
            "warm_meso_reconciliation_v2"
        assert mechanism["prefix_output"] == {
            "write_unfinished": True, "precision": 16}
        assert mechanism["production_tripinfo_precision"] == 2
        assert mechanism["traci_time_loss_used"] is False
        assert mechanism["tolerance"] == 0.0
        assert "recompute warm_boundary_active_v1" in \
            mechanism["active_population_digest_validation"]
        assert "prefix XML order" in mechanism["completed_record_order"]
        assert "without segment regrouping" in mechanism["rule"]
        assert manifest["resumed_execution_contract"]["keep_after_arrival"] \
            is False
        assert "retention_contract" not in manifest
        assert "accounting" not in manifest and "hypothesis" not in manifest

    def test_source_facts_name_the_exact_mismatch(self):
        defect = _load()["source_established_defect"]
        assert defect["tripinfo_field"] == "MSDevice_Tripinfo::myMesoTimeLoss"
        assert "omit myMesoTimeLoss" in defect["state_omission"]
        assert "getWaitingTime" in defect["traci_mismatch"]
        assert len(defect["official_sources"]) == 3

    def test_authorized_successors_and_later_source_changes_keep_v13_retired(self):
        drifted = sorted(
            name for name, digest in _load()["source_fingerprints"].items()
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest)
        assert {
            "run_monthly_warm_state_validation.py",
            "tests/test_monthly_warm_state_freeze.py",
            "tests/test_monthly_warm_state_v13_freeze.py",
        } <= set(drifted)


class TestLifecycle:

    def test_v13_is_default_off_unapproved_and_unexecuted(self):
        manifest = _load()
        assert manifest["campaign_version"] == "v13"
        assert manifest["status"] == "frozen_unapproved_unexecuted"
        assert manifest["warming_default"].startswith("OFF")
        assert "approved_content_key" not in manifest

    def test_harness_default_has_advanced_and_v13_load_fails_closed(self):
        harness = _harness()
        assert harness.DEFAULT_MANIFEST != MANIFEST
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(MANIFEST)

    def test_old_approval_cannot_resurrect_v13_after_source_drift(self):
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            _harness().main(["--manifest", str(MANIFEST), "--execute"])

    def test_checks_only_mode_refuses_superseded_v13_process_free(self):
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            _harness().main(["--manifest", str(MANIFEST)])

    def test_versioned_suite_does_not_pin_a_predecessor_test(self):
        import ast
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        assert not (key.value.startswith("tests/")
                                    and key.value.endswith("_freeze.py"))
