"""The SPENT v4 warm-state contract (frozen by LUNA-WARM-09, executed by 10).

v4 was EXECUTED once (LUNA-WARM-10) and failed honestly: coverage complete, zero
mismatches, but all three warm arms fell back to cold, so the comparison was
cold-versus-cold and nothing was warmed. Its key is spent and it is retained for
provenance only.

These checks assert SUPERSESSION, not currency: the frozen bytes are untouched
and self-consistent, and they no longer match the live tree. That drift is what
makes the campaign unadoptable, so the tests confirm it rather than rewriting
history. NOTE: nothing here reads the spent outcome under `runs/` — only the
tracked manifest.

Warming stays default-OFF. The accounting assertions below remain live and still
apply to production — v5 changed the DIAGNOSTICS, not the objective rule — so
they are kept rather than retired: no boundary offset anywhere, and a snapshot
command that applies the state settings its identity records.

Process-free: no simulator, no subprocess, no `runs/` access.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

MANIFEST = Path("validation/monthly_warm_state_manifest_v4.json")
PARENT = Path("validation/monthly_warm_state_manifest_v3.json")
TOOL = Path("tools/freeze_monthly_warm_state_v4.py")


def _load():
    return json.loads(MANIFEST.read_text())


def _canonical_key(payload):
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def _freeze():
    sys.path.insert(0, "tools")
    import freeze_monthly_warm_state_v4 as module
    return module


class TestIdentityAndReproducibility:

    def test_the_content_key_recomputes_canonically(self):
        assert _canonical_key(_load()) == _load()["content_key"]

    def test_the_spent_v4_package_no_longer_recomposes(self):
        """Drift invalidates REUSE without touching a single frozen byte."""
        before = MANIFEST.read_bytes()
        rebuilt = _freeze().build_artifacts()[str(MANIFEST)]
        assert MANIFEST.read_text() != rebuilt, (
            "the spent v4 contract still reproduces; it must not")
        assert MANIFEST.read_bytes() == before, "recomposition mutated v4"

    def test_the_key_differs_from_every_spent_campaign(self):
        current = _load()["content_key"]
        for spent in ("v1", "v2", "v3"):
            other = json.loads(Path(
                f"validation/monthly_warm_state_manifest_{spent}.json").read_text())
            assert current != other["content_key"], spent

    def test_no_earlier_manifest_is_overwritten(self):
        for spent in ("v1", "v2", "v3"):
            path = Path(
                f"validation/monthly_warm_state_manifest_{spent}.json")
            assert path.is_file(), spent

    def test_publication_refuses_to_clobber_the_existing_artifact(self):
        with pytest.raises(SystemExit, match="refusing to overwrite"):
            _freeze().publish(_freeze().build_artifacts())

    def test_the_cli_requires_exactly_one_mode(self):
        with pytest.raises(SystemExit, match="exactly one"):
            _freeze().main([])
        with pytest.raises(SystemExit, match="exactly one"):
            _freeze().main(["--write", "--verify"])


class TestApprovalAndScope:

    def test_the_recorded_status_is_preserved_as_frozen(self):
        """The artifact records the status it was FROZEN with. Execution
        happened afterwards and is recorded in v5's supersession, not by
        editing these bytes — rewriting them would erase the provenance."""
        assert _load()["status"] == "frozen_unapproved_unexecuted"

    def test_no_approval_is_stored_in_the_manifest(self):
        """A stored approval would be self-referential: setting it changes the
        key it names, so those exact bytes could never be approved."""
        manifest = _load()
        assert "approved_content_key" not in manifest
        assert "content_key" in manifest["approval_mechanism"]

    def test_warming_is_recorded_as_default_off(self):
        assert _load()["warming_default"].startswith("OFF")

    def test_the_claim_scope_asserts_no_product_behaviour(self):
        scope = _load()["claim_scope"]
        assert "no product behaviour" in scope and "no adoption" in scope

    def test_speedup_is_reported_never_claimed_as_proven(self):
        assert "never claimed as proven" in \
            _load()["performance_reporting"]["claim_policy"]

    def test_the_comparison_stays_exact_with_no_tolerance(self):
        policy = _load()["comparison_policy"]
        assert policy["tolerance"] == "exact equality of the semantic payload"
        assert "publish no cache material" in policy["on_mismatch"]


class TestPreservedAccumulatorAccounting:
    """Criterion 1: the refuted offset must be gone, and visibly so."""

    def test_the_recorded_rule_is_prefix_plus_resumed_with_no_offset(self):
        rule = _load()["accounting"]["rule"]
        assert "completed-prefix aggregate plus the resumed aggregate" in rule
        assert "NO boundary offset" in rule

    def test_the_measured_evidence_that_refuted_v3_is_recorded(self):
        evidence = _load()["accounting"]["measured_evidence"]
        assert evidence["classification"] == "full_accumulator_preserved"
        # The resumed run reports the WHOLE trip, not the post-boundary segment.
        assert evidence["resumed_final_s"] == evidence["uninterrupted_final_s"]
        assert evidence["resumed_final_s"] != \
            evidence["post_boundary_only_would_be_s"]
        assert evidence["observed_return_codes"] == {
            "cold": 0, "prefix": 0, "resumed": 0}

    def test_why_v3_is_rejected_is_stated(self):
        assert "double counts" in _load()["accounting"]["why_v3_is_rejected"]

    def test_the_absence_of_a_serialization_residual_is_stated(self):
        """Aggregates are whole values, so v3's bounded skew cannot arise."""
        assert "rounded twice" in \
            _load()["accounting"]["no_serialization_residual"]

    def test_production_never_applies_a_boundary_offset(self):
        """Structural, over the live tree rather than the manifest's prose."""
        for name in ("traffic_sim/simulation/monthly_sumo.py",
                     "traffic_sim/simulation/monthly_warm_state.py",
                     "traffic_sim/simulation/warm_state_boundary.py"):
            source = Path(name).read_text()
            assert "reconcile_split_time_loss" not in source, name
            assert "boundary_ledger" not in source, name

    def test_the_aggregation_summary_records_no_offset(self):
        from traffic_sim.simulation.warm_state_boundary import (
            aggregate_split_time_loss)
        result = aggregate_split_time_loss(
            completed_prefix_total=10.0, completed_prefix_trips=1,
            resumed_total=5.0, resumed_trips=1)
        assert result["boundary_offset_applied"] is False
        assert result["total_time_loss_s"] == 15.0

    def test_production_refuses_an_applied_offset_in_stored_evidence(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, validate_aggregation_summary)
        with pytest.raises(WarmStateContractError, match="double counts"):
            validate_aggregation_summary({
                "total_time_loss_s": 1.0, "trip_count": 1,
                "completed_prefix_trips": 1, "resumed_trips": 0,
                "boundary_offset_applied": True, "tripinfo_precision": 2})


class TestStateSettingsContract:
    """Criterion 3: v3 recorded these settings and applied neither."""

    def test_the_manifest_records_the_settings_and_the_argv(self):
        from traffic_sim.simulation.warm_state_boundary import (
            snapshot_settings_arguments)
        settings = _load()["state_settings"]
        assert settings["save_state_rng"] is True
        assert settings["save_state_precision"] == 16
        assert settings["snapshot_arguments"] == snapshot_settings_arguments()

    def test_the_recorded_argv_comes_from_the_cache_constants(self):
        from traffic_sim.simulation.warm_state_cache import (
            STATE_PRECISION, STATE_RNG_SAVED)
        settings = _load()["state_settings"]
        assert settings["save_state_precision"] == STATE_PRECISION
        assert settings["save_state_rng"] is STATE_RNG_SAVED

    def test_the_recorded_argv_passes_the_production_validator(self):
        from traffic_sim.simulation.warm_state_boundary import (
            validate_snapshot_command)
        argv = ["sumo", *_load()["state_settings"]["snapshot_arguments"]]
        assert validate_snapshot_command(argv) == {
            "save_state_rng": True, "save_state_precision": 16}

    def test_the_runner_appends_the_settings_to_its_prefix_command(self):
        """Structural: the actual snapshot command, not a claim about it."""
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        assert "*snapshot_settings_arguments()" in source

    def test_the_snapshot_schema_is_recorded(self):
        from traffic_sim.simulation.warm_state_boundary import (
            SNAPSHOT_FACTS_SCHEMA)
        assert _load()["snapshot_facts_schema"] == SNAPSHOT_FACTS_SCHEMA


class TestHypothesisIsStatedAsUnproven:

    def test_the_hypothesis_is_recorded_and_marked_unproven(self):
        hypothesis = _load()["hypothesis"]
        assert "DEFAULT state serialization" in hypothesis["claim"]
        assert hypothesis["status"].startswith("UNPROVEN")

    def test_the_refutation_condition_is_stated_before_execution(self):
        """A campaign that cannot fail proves nothing."""
        condition = _load()["hypothesis"]["refutation_condition"]
        assert "REFUTED" in condition and "fails honestly" in condition

    def test_the_v2_gap_is_recorded_as_still_unexplained(self):
        hypothesis = _load()["hypothesis"]
        assert set(hypothesis["v2_residual_gap_s"]) == {"q10", "q50", "q90"}
        assert "UNEXPLAINED" in hypothesis["note"]

    def test_the_mechanism_under_test_names_the_actual_flags(self):
        mechanism = _load()["hypothesis"]["mechanism_under_test"]
        assert "--save-state.rng true" in mechanism
        assert "--save-state.precision 16" in mechanism


class TestSchemasAndSourceBinding:

    def test_the_live_schemas_are_recorded(self):
        from traffic_sim.simulation.monthly_warm_state import (
            PREFIX_EVIDENCE_SCHEMA)
        assert _load()["prefix_evidence_schema"] == PREFIX_EVIDENCE_SCHEMA
        assert _load()["prefix_evidence_schema"] == "monthly_prefix_evidence_v3"

    def test_the_accounting_advanced_beyond_the_retired_parent(self):
        assert _load()["prefix_evidence_schema"] != \
            json.loads(PARENT.read_text())["prefix_evidence_schema"]

    def test_the_spent_v4_sources_have_drifted_from_the_live_tree(self):
        """Supersession is VISIBLE as drift, and drift is what blocks reuse."""
        fingerprints = _load()["source_fingerprints"]
        drifted = [name for name, digest in fingerprints.items()
                   if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest]
        assert drifted, "v4 still matches the live tree; it must not after v5"
        assert "traffic_sim/simulation/monthly_sumo.py" in drifted

    @pytest.mark.parametrize("module", [
        "traffic_sim/simulation/warm_state_boundary.py",
        "traffic_sim/simulation/warm_state_cache.py",
        "traffic_sim/simulation/monthly_warm_state.py",
        "traffic_sim/simulation/monthly_sumo.py",
        "run_monthly_warm_state_validation.py",
        "tools/freeze_monthly_warm_state_v4.py",
    ])
    def test_drift_in_a_bound_source_invalidates_the_contract(self, module):
        """Proven without touching the tree: one changed byte breaks the key."""
        manifest = _load()
        assert module in manifest["source_fingerprints"]
        mutated = Path(module).read_bytes() + b"\n# drift\n"
        assert (hashlib.sha256(mutated).hexdigest()
                != manifest["source_fingerprints"][module])

    def test_the_freeze_tool_binds_itself(self):
        assert "tools/freeze_monthly_warm_state_v4.py" in \
            _load()["source_fingerprints"]


class TestInheritanceFromTrackedV3:
    """v4 reads no archive; the physical facts come from the tracked parent."""

    def test_the_parent_is_bound_by_content_key(self):
        inherited = _load()["inherited_from"]
        assert inherited["content_key"] == \
            json.loads(PARENT.read_text())["content_key"]
        assert inherited["manifest"] == str(PARENT)

    def test_a_parent_whose_key_does_not_recompute_is_refused(
            self, tmp_path, monkeypatch):
        module = _freeze()
        tampered = dict(json.loads(PARENT.read_text()), simulation_mode="micro")
        root = tmp_path / "repo"
        (root / "validation").mkdir(parents=True)
        (root / module.PARENT).write_text(json.dumps(tampered))
        monkeypatch.setattr(module, "ROOT", root)
        with pytest.raises(SystemExit, match="does not recompute"):
            module.load_parent()

    def test_a_different_parent_campaign_is_refused(self, tmp_path, monkeypatch):
        module = _freeze()
        other = json.loads(Path(
            "validation/monthly_warm_state_manifest_v1.json").read_text())
        root = tmp_path / "repo"
        (root / "validation").mkdir(parents=True)
        (root / module.PARENT).write_text(json.dumps(other))
        monkeypatch.setattr(module, "ROOT", root)
        with pytest.raises(SystemExit, match="not the expected frozen v3"):
            module.load_parent()

    def test_the_inherited_facts_are_identical_to_the_parent(self):
        manifest, parent = _load(), json.loads(PARENT.read_text())
        for field in manifest["inherited_from"]["fields"]:
            assert manifest[field] == parent[field], field

    def test_the_case_is_the_same_physical_closure(self):
        case = _load()["cases"][0]
        parent_case = json.loads(PARENT.read_text())["cases"][0]
        for field in ("directed_edges", "closure_begin_s", "closure_end_s",
                      "closure_bound_warm_point_s"):
            assert case[field] == parent_case[field], field

    def test_route_safety_still_covers_all_three_variants(self):
        safety = _load()["route_safety"]
        assert set(safety) == {"q10", "q50", "q90"}
        for variant, record in safety.items():
            assert isinstance(record["safe_warm_point_s"], int), variant

    def test_the_supersession_records_that_v3_never_executed(self):
        supersedes = _load()["supersedes"]
        assert supersedes["campaign"] == "v3"
        assert "NEVER EXECUTED" in supersedes["outcome"]
        assert supersedes["also_spent"] == ["v1", "v2"]


class TestFrozenIdentitySet:

    def test_the_three_identities_are_unchanged(self):
        manifest = _load()
        assert manifest["demand_variants"] == ["q10", "q50", "q90"]
        assert manifest["seeds"] == [1000, 1001, 1002]

    def test_the_seeds_are_the_production_assignment(self):
        from traffic_sim.simulation.monthly_search import canonical_seed
        manifest = _load()
        derived = sorted({canonical_seed(variant, repetition)
                          for variant in manifest["demand_variants"]
                          for repetition in
                          range(manifest["repetitions_per_variant"])})
        assert derived == manifest["seeds"]

    def test_exactly_one_schedule_is_frozen(self):
        assert len(_load()["frozen_schedule_ids"]) == 1


class TestHarnessDefault:

    def _harness(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "warm_harness_v4", "run_monthly_warm_state_validation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_harness_default_is_no_longer_v4(self):
        assert self._harness().DEFAULT_MANIFEST != MANIFEST, (
            "a spent campaign must not be the harness default")

    def test_the_spent_manifest_is_refused_by_the_harness(self):
        with pytest.raises(SystemExit):
            self._harness().load_frozen_manifest(MANIFEST)


class TestProcessFree:

    def test_the_freeze_tool_imports_no_simulator_at_module_scope(self):
        tree = ast.parse(TOOL.read_text())
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                top.add(node.module.split(".")[0])
        assert not top & {"traci", "libsumo", "socket", "subprocess"}, sorted(top)

    def test_no_simulator_is_imported_by_this_suite(self):
        assert "traci" not in sys.modules and "libsumo" not in sys.modules

    def test_the_declared_artifact_root_is_recorded_not_created(self):
        """The manifest names where a FUTURE approved run would write."""
        assert _load()["artifact_root"] == "runs/monthly-warm-state-validation"
        assert 'Path(manifest["artifact_root"])' not in TOOL.read_text()

    def test_the_freeze_tool_reads_no_archive(self):
        source = TOOL.read_text()
        assert "ARCHIVE = " not in source and "ARCHIVE_FILES" not in source
