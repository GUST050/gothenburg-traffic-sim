"""Lifecycle-safe checks for frozen, unapproved, unexecuted warm-state v11.

Process-free. No SUMO, TraCI package import, subprocess, runs/archive/outcome or
cache access. Injected fakes exercise only the controller's pure call shape.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

MANIFEST = Path("validation/monthly_warm_state_manifest_v11.json")
PARENT = Path("validation/monthly_warm_state_manifest_v10.json")
TOOL = Path("tools/freeze_monthly_warm_state_v11.py")

FROZEN_V10 = {
    "tools/freeze_monthly_warm_state_v10.py":
        "498b164f2866914b87b5d5ebf8c623f63bf3387785cc03aecf532bcd5efd0a2b",
    "validation/monthly_warm_state_manifest_v10.json":
        "55a3c857abad18434a80ea8da861a966a0fed1ff5c35f48579a91dc159f2dc56",
}


def _load():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _canonical_key(payload):
    body = {key: value for key, value in payload.items()
            if key != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def _freeze():
    sys.path.insert(0, "tools")
    import freeze_monthly_warm_state_v11 as module
    return module


def _harness():
    spec = importlib.util.spec_from_file_location(
        "warm_harness_v11", "run_monthly_warm_state_validation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_current():
    return _harness().DEFAULT_MANIFEST == MANIFEST


class TestIdentityLifecycleAndScope:

    def test_the_content_key_recomputes(self):
        assert _canonical_key(_load()) == _load()["content_key"]

    def test_v11_no_longer_recomposes_and_its_bytes_are_untouched(self):
        """v11 is RETIRED — rejected for incomplete process-free closure, never
        approved and never executed.

        Unconditional, not currency-dependent: a rejected candidate does not
        come back, so branching on whether it happens to be the pointer would
        describe a lifecycle it no longer has. Its recomposition must differ
        because its bound sources moved on, and its stored bytes must not.
        """
        before = MANIFEST.read_bytes()
        rebuilt = _freeze().build_artifacts()[str(MANIFEST)]
        assert rebuilt != MANIFEST.read_text(encoding="utf-8"), (
            "a rejected v11 still recomposes; it must not")
        assert MANIFEST.read_bytes() == before, "recomposition mutated v11"

    @pytest.mark.parametrize("name,digest", sorted(FROZEN_V10.items()))
    def test_v10_artifacts_are_byte_identical(self, name, digest):
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest

    def test_v10_is_rejected_without_rewriting_it(self):
        record = _load()["supersedes"]
        assert record["campaign"] == "v10"
        assert "REJECTED" in record["outcome"]
        assert "UNEXECUTED" in record["outcome"]

    def test_v11_is_unapproved_unexecuted_and_default_off(self):
        manifest = _load()
        assert manifest["status"] == "frozen_unapproved_unexecuted"
        assert manifest["warming_default"].startswith("OFF")
        assert "approved_content_key" not in manifest

    def test_no_predecessor_test_file_is_pinned(self):
        """Structural, over dict KEYS — never a substring of the file.

        The earlier version searched its own source for the pinned path, which
        its own assertion text contains, so it could never pass. A check that
        matches itself is not a check.
        """
        import ast as _ast
        tree = _ast.parse(Path(__file__).read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Dict):
                continue
            for key in node.keys:
                if isinstance(key, _ast.Constant) and isinstance(key.value, str):
                    assert not (key.value.startswith("tests/")
                                and key.value.endswith("_freeze.py")), key.value

    def test_the_bound_sources_have_drifted_which_is_what_retires_v11(self):
        """Drift is the retirement mechanism and is never re-synced."""
        drifted = sorted(
            name for name, digest in _load()["source_fingerprints"].items()
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest)
        assert drifted, "v11 fingerprints match again; were they re-synced?"

    def test_required_regressions_are_really_bound(self):
        manifest = _load()
        for name in manifest["regression_binding"]["required"]:
            assert name in manifest["source_fingerprints"]


class TestExactSelectiveContract:

    def test_prefix_and_boundary_schemas_match_production(self):
        from traffic_sim.simulation.monthly_warm_state import PREFIX_EVIDENCE_SCHEMA
        from traffic_sim.simulation.warm_state_boundary import (
            FINAL_LEDGER_SCHEMA, RESTORE_AUDIT_SCHEMA,
            RESTORE_CORRECTION_SCHEMA, SAVE_LEDGER_SCHEMA)
        manifest = _load()
        if _is_current():
            assert manifest["prefix_evidence_schema"] == PREFIX_EVIDENCE_SCHEMA
        else:
            assert manifest["prefix_evidence_schema"] != PREFIX_EVIDENCE_SCHEMA
            with pytest.raises(SystemExit, match="frozen sources drifted"):
                _harness().load_frozen_manifest(MANIFEST)
        correction = manifest["restore_correction"]
        assert correction["save_ledger_schema"] == SAVE_LEDGER_SCHEMA
        assert correction["restore_audit_schema"] == RESTORE_AUDIT_SCHEMA
        assert correction["final_ledger_schema"] == FINAL_LEDGER_SCHEMA
        assert correction["correction_schema"] == RESTORE_CORRECTION_SCHEMA

    def test_retention_is_exact_and_one_terminal_advance_is_bound(self):
        contract = _load()["retention_contract"]
        assert contract["option"] == "--keep-after-arrival"
        assert contract["exact_retention_s"] == (
            contract["terminal_time_s"] - contract["warm_point_s"])
        assert contract["simulation_step_calls_after_restore"] == 1

    def test_rounding_rule_names_the_unrounded_whole_value(self):
        rule = _load()["restore_correction"]["rule"]
        assert "unrounded TraCI value + deficit" in rule
        assert "once" in rule
        assert _load()["restore_correction"]["raw_outputs_rewritten"] is False

    def test_the_hypothesis_is_explicitly_unproven(self):
        hypothesis = _load()["hypothesis_under_test"]
        assert hypothesis["status"].startswith("UNPROVEN")
        assert "publishes no cache" in hypothesis["refutation_condition"]

    def test_physical_case_is_inherited_unchanged(self):
        manifest, parent = _load(), json.loads(PARENT.read_text())
        for field in ("directed_edges", "closure_begin_s", "closure_end_s",
                      "closure_bound_warm_point_s"):
            assert manifest["cases"][0][field] == parent["cases"][0][field]
        assert manifest["route_safety"] == parent["route_safety"]
        assert manifest["archive_files_sha256"] == parent["archive_files_sha256"]


class TestRetentionBuilder:

    def _build(self, tmp_path, **kwargs):
        """`load_state_arguments` verifies the state EXISTS before emitting the
        flag, so the fixture has to write one. Skipping that tested nothing
        about retention and failed for an unrelated reason."""
        import run_scenario as rs
        state = tmp_path / "state.gz"
        state.write_bytes(b"STATE")
        (tmp_path / "route.xml").write_text("<routes/>\n")
        return rs.build_sumo_invocation(
            1001, tmp_path / "route.xml", [], 1000, tmp_path,
            begin_s=100, load_state_path=state, **kwargs)[0]

    def test_default_argv_is_unchanged(self, tmp_path):
        assert self._build(tmp_path) == self._build(
            tmp_path, keep_after_arrival_s=None)

    def test_one_typed_retention_option_is_emitted(self, tmp_path):
        cmd = self._build(tmp_path, keep_after_arrival_s=1200)
        assert cmd.count("--keep-after-arrival") == 1
        index = cmd.index("--keep-after-arrival")
        assert cmd[index + 1] == "1200"

    @pytest.mark.parametrize("value", [True, 0, -1, 1.5, "9"])
    def test_invalid_retention_is_refused(self, tmp_path, value):
        with pytest.raises(ValueError, match="positive integer"):
            self._build(tmp_path, keep_after_arrival_s=value)


class _ResumedTraci:
    def __init__(self, warm, terminal, restored, final):
        self._time = float(warm)
        self._terminal = float(terminal)
        self._restored = dict(restored)
        self._final = dict(final)
        self._entries = self._restored
        self.simulation = self.vehicle = self
        self.step_targets = []
        self.closed = False

    def init(self, port):
        self.port = port

    def getTime(self):
        return self._time

    def getIDList(self):
        return list(self._entries)

    def getTimeLoss(self, vehicle):
        return self._entries[vehicle]

    def simulationStep(self, target):
        self.step_targets.append(target)
        self._time = float(target)
        self._entries = self._final

    def close(self):
        self.closed = True


class TestOneAdvanceAndUnroundedFinalCapture:

    def test_restore_phase_advances_once_and_captures_only_affected_ids(self):
        from tests.test_warm_state_boundary import _ledger
        from traffic_sim.simulation.warm_state_boundary import WarmPrefixController
        warm, terminal = 900, 5000
        saved = _ledger({"lost": 0.002, "kept": 4.0},
                        time_s=warm, warm=warm)
        traci = _ResumedTraci(
            warm, terminal, {"lost": 0.0, "kept": 4.0},
            {"lost": 1.004, "kept": 9.0})
        result = WarmPrefixController(
            traci_module=traci, clock=lambda: 0)._restore_phase(
                traci=traci, port=123, warm_point_s=warm,
                terminal_time_s=terminal, saved_ledger=saved, deadline=1)
        assert traci.step_targets == [float(terminal)]
        assert result["restore_audit"]["deficits"] == {"lost": 0.002}
        assert result["final_ledger"]["entries"] == {"lost": 1.004}
        assert traci.closed

    def test_rounded_tripinfo_counterexample_reconstructs_to_1_01(self):
        from tests.test_warm_state_boundary import _ledger
        from traffic_sim.simulation.warm_state_boundary import (
            apply_restore_deficits, build_restore_audit, capture_final_ledger)
        saved = _ledger({"v": 0.002}, time_s=900, warm=900)
        restored = _ledger({"v": 0.0}, time_s=900, warm=900)
        audit = build_restore_audit(saved, restored, expected_warm_point_s=900)
        traci = _ResumedTraci(900, 5000, {"v": 0.0}, {"v": 1.004})
        traci.simulationStep(5000)
        final = capture_final_ledger(
            traci, ["v"], warm_point_s=900, terminal_time_s=5000,
            audit_digest=audit["digest"])
        result = apply_restore_deficits(
            {"v": 1.00}, final, audit, expected_warm_point_s=900,
            expected_terminal_time_s=5000)
        assert result["records"]["v"] == 1.01
        assert round(1.00 + 0.002, 2) == 1.0


class TestHarnessCurrencyAndApproval:

    def test_loading_v11_fails_closed(self):
        """Unloadable, whatever the pointer says. Which gate catches it first is
        not the property under test — that a rejected contract is REFUSED is."""
        with pytest.raises(SystemExit):
            _harness().load_frozen_manifest(MANIFEST)

    def test_execute_is_refused_before_any_runtime_preflight(self):
        """A rejected contract is refused BEFORE approval is even considered, so
        no token could resurrect it. Not skipped: skipping on retirement would
        remove the assertion exactly when it matters most."""
        with pytest.raises(SystemExit) as caught:
            _harness().main(["--manifest", str(MANIFEST), "--execute"])
        assert "requires an approval token" not in str(caught.value), (
            "a rejected contract must fail before the approval gate")

    def test_publication_refuses_to_overwrite_v11(self):
        with pytest.raises(SystemExit, match="refusing to overwrite"):
            _freeze().publish(_freeze().build_artifacts())
