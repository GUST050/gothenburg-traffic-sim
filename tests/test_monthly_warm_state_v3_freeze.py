"""The SUPERSEDED v3 warm-state contract (frozen by LUNA-WARM-08, retired by 09).

v3 was frozen, never approved and NEVER EXECUTED: the approved LUNA-WARM-08
diagnostic refuted its boundary-offset premise before any campaign ran. It is
retained for provenance only.

These checks therefore assert SUPERSESSION, not currency: the frozen bytes are
untouched and still self-consistent, and they no longer match the live tree. That
drift is the mechanism that makes the campaign unadoptable, so the tests confirm
it rather than rewriting history to hide it.

Process-free: no simulator, no subprocess, no `runs/` access.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

MANIFEST = Path("validation/monthly_warm_state_manifest_v3.json")
PARENT = Path("validation/monthly_warm_state_manifest_v2.json")


def _load():
    return json.loads(MANIFEST.read_text())


def _canonical_key(payload):
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def _freeze():
    sys.path.insert(0, "tools")
    import freeze_monthly_warm_state_v3 as module
    return module


class TestIdentityAndReproducibility:

    def test_the_content_key_recomputes_canonically(self):
        assert _canonical_key(_load()) == _load()["content_key"]

    def test_the_retired_v3_package_can_no_longer_recompose(self):
        """The strongest form of unadoptability, and it costs no frozen byte.

        v3's freeze tool is bound to the boundary-ledger accounting that
        LUNA-WARM-09 removed, so it cannot even import against the live tree.
        The tool is deliberately NOT repaired — a retired campaign that could
        still rebuild itself would invite reuse.
        """
        before = MANIFEST.read_bytes()
        with pytest.raises(ImportError):
            _freeze().build_artifacts()
        assert MANIFEST.read_bytes() == before, "the frozen bytes were mutated"

    def test_the_frozen_bytes_are_still_internally_consistent(self):
        """Retired is not corrupt: the artifact still recomputes its own key."""
        assert _canonical_key(_load()) == _load()["content_key"]

    def test_the_key_differs_from_both_spent_campaigns(self):
        current = _load()["content_key"]
        for spent in ("v1", "v2"):
            other = json.loads(Path(
                f"validation/monthly_warm_state_manifest_{spent}.json").read_text())
            assert current != other["content_key"], spent

    def test_the_spent_manifests_are_not_overwritten(self):
        assert PARENT.is_file()
        assert Path("validation/monthly_warm_state_manifest_v1.json").is_file()

    def test_the_artifact_is_still_present_and_unmodified(self):
        """Provenance requires the retired evidence to remain readable."""
        assert MANIFEST.is_file() and MANIFEST.stat().st_size > 0
        assert _load()["status"] == "frozen_unapproved_unexecuted"


class TestApprovalAndScope:

    def test_it_is_frozen_unapproved_and_unexecuted(self):
        manifest = _load()
        assert manifest["status"] == "frozen_unapproved_unexecuted"

    def test_no_approval_is_stored_in_the_manifest(self):
        """A stored approval would be self-referential: setting it changes the
        key it names, so those exact bytes could never be approved."""
        manifest = _load()
        assert "approved_content_key" not in manifest
        assert "approval" not in manifest
        assert "content_key" in manifest["approval_mechanism"]

    def test_the_claim_scope_asserts_no_product_behaviour(self):
        scope = _load()["claim_scope"]
        assert "no product behaviour" in scope and "no adoption" in scope

    def test_speedup_is_reported_never_claimed_as_proven(self):
        policy = _load()["performance_reporting"]["claim_policy"]
        assert "never claimed as proven" in policy


class TestBoundaryContract:

    def test_it_records_the_schemas_it_was_frozen_with(self):
        """v3 keeps ITS accounting, not today's. Asserting it tracks the live
        schema would force a retired campaign to follow the code forward —
        exactly the drift its frozen key exists to prevent."""
        manifest = _load()
        assert manifest["prefix_evidence_schema"] == "monthly_prefix_evidence_v2"
        assert manifest["boundary_ledger_schema"] == \
            "warm_boundary_active_ledger_v1"

    def test_the_refuted_premise_is_preserved_not_edited(self):
        """The artifact still states the hypothesis that was later refuted. That
        is the historical record, and rewriting it would erase the finding."""
        accounting = _load()["boundary_accounting"]
        assert "boundary-active" in accounting["hypothesis"]

    def test_no_serialized_precision_is_recorded(self):
        """Deliberately absent. An earlier revision bound one, on the theory
        that writing more digits made the split exact. It does not: for ANY
        finite precision the true sum can sit closer to a rounding boundary
        than the serialization error, and `--precision` is global so it also
        perturbed recovery and waiting semantics. Recording a number that
        cannot deliver exactness would misdescribe the contract."""
        assert "warm_output_precision" not in _load()

    def test_the_accounting_advanced_beyond_the_spent_parent(self):
        assert _load()["prefix_evidence_schema"] != \
            json.loads(PARENT.read_text())["prefix_evidence_schema"]

    def test_the_hypothesis_and_its_refutation_condition_are_recorded(self):
        """A campaign that cannot fail proves nothing."""
        accounting = _load()["boundary_accounting"]
        assert "boundary-active" in accounting["hypothesis"]
        assert "NOT the cause" in accounting["refutation_condition"]
        assert "fails honestly" in accounting["refutation_condition"]

    def test_the_known_residual_is_declared_before_execution(self):
        """A campaign that would have to explain a failure afterwards is weaker
        than one that names the failure mode in advance."""
        residual = _load()["boundary_accounting"]["known_residual"]
        assert residual["bound_s_per_boundary_vehicle"] == 0.01
        assert "no finite output precision suffices" in \
            residual["why_not_fixed_by_more_digits"]
        assert "global" in residual["why_not_fixed_by_more_digits"]
        # The residual must never be presented as a reason to relax the contract.
        assert "exact with no tolerance" in residual["consequence"]

    def test_the_measured_v2_residual_gap_is_recorded_per_identity(self):
        gap = _load()["boundary_accounting"]["v2_residual_gap_s"]
        assert set(gap) == {"q10", "q50", "q90"}
        # Warm was LOWER on every identity, and monotone in demand volume —
        # the ordering that identified boundary-active vehicles as the cause.
        assert all(value < 0 for value in gap.values())
        assert gap["q10"] > gap["q50"] > gap["q90"]


class TestSourceBinding:

    def test_the_spent_v3_sources_have_drifted_from_the_live_tree(self):
        """Supersession is VISIBLE as drift, and drift is what blocks reuse.

        Preserved-accumulator accounting changed the files v3 bound. The frozen
        bytes are untouched; they simply no longer describe the live tree.
        """
        fingerprints = _load()["source_fingerprints"]
        drifted = [name for name, digest in fingerprints.items()
                   if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest]
        assert drifted, "v3 still matches the live tree; it must not after v4"
        assert "traffic_sim/simulation/warm_state_boundary.py" in drifted

    def test_the_boundary_module_is_bound(self):
        """Unbound, the reconciliation could change under an unchanged key —
        the precise failure v3 exists to fix."""
        assert "traffic_sim/simulation/warm_state_boundary.py" in \
            _load()["source_fingerprints"]

    @pytest.mark.parametrize("module", [
        "traffic_sim/simulation/warm_state_boundary.py",
        "traffic_sim/simulation/monthly_warm_state.py",
        "traffic_sim/simulation/monthly_sumo.py",
        "run_scenario.py",
        "run_monthly_warm_state_validation.py",
        "tools/freeze_monthly_warm_state_v3.py",
    ])
    def test_drift_in_a_bound_source_invalidates_the_contract(self, module):
        """Proven without touching the tree: one changed byte breaks the key."""
        manifest = _load()
        assert module in manifest["source_fingerprints"]
        mutated = Path(module).read_bytes() + b"\n# drift\n"
        assert (hashlib.sha256(mutated).hexdigest()
                != manifest["source_fingerprints"][module])

    def test_the_freeze_tool_binds_itself(self):
        assert "tools/freeze_monthly_warm_state_v3.py" in \
            _load()["source_fingerprints"]


class TestInheritanceFromTrackedV2:
    """v3 reads no archive; the physical facts come from the tracked parent."""

    def test_the_parent_is_bound_by_content_key(self):
        inherited = _load()["inherited_from"]
        parent = json.loads(PARENT.read_text())
        assert inherited["content_key"] == parent["content_key"]
        assert inherited["manifest"] == str(PARENT)

    def test_a_parent_whose_key_does_not_recompute_is_refused(self, tmp_path,
                                                              monkeypatch):
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
        with pytest.raises(SystemExit, match="not the expected frozen v2"):
            module.load_parent()

    def test_the_inherited_facts_are_identical_to_the_parent(self):
        manifest, parent = _load(), json.loads(PARENT.read_text())
        for field in _load()["inherited_from"]["fields"]:
            assert manifest[field] == parent[field], field

    def test_the_case_is_the_same_physical_closure_as_the_parent(self):
        """Inherited route safety would otherwise describe a different closure."""
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
            assert record["safe_warm_point_s"] > 0, variant

    def test_the_supersession_records_the_executed_v2_outcome(self):
        supersedes = _load()["supersedes"]
        assert supersedes["campaign"] == "v2"
        assert "failed" in supersedes["outcome"]


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

    def test_the_variants_are_the_production_tuple(self):
        from traffic_sim.simulation.finalist_decision import DEMAND_VARIANTS
        assert sorted(_load()["demand_variants"]) == sorted(DEMAND_VARIANTS)

    def test_exactly_one_schedule_is_frozen(self):
        assert len(_load()["frozen_schedule_ids"]) == 1

    def test_comparison_stays_exact_with_no_tolerance(self):
        policy = _load()["comparison_policy"]
        assert policy["tolerance"] == "exact equality of the semantic payload"
        assert "publish no cache material" in policy["on_mismatch"]


class TestHarnessDefault:

    def test_the_harness_default_is_no_longer_v3(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "warm_harness_v3", "run_monthly_warm_state_validation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.DEFAULT_MANIFEST != MANIFEST, (
            "a retired campaign must not be the harness default")

    def test_the_retired_manifest_is_refused_by_the_harness(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "warm_harness_v3b", "run_monthly_warm_state_validation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with pytest.raises(SystemExit):
            module.load_frozen_manifest(MANIFEST)


class TestProcessFree:

    def test_the_freeze_tool_reads_no_archive_and_names_no_archive_root(self):
        source = Path("tools/freeze_monthly_warm_state_v3.py").read_text()
        assert "ARCHIVE = " not in source
        assert "ARCHIVE_FILES" not in source
        for forbidden in ("subprocess", "import traci", "libsumo", "socket"):
            assert forbidden not in source, forbidden

    def test_no_simulator_module_is_imported_by_this_suite(self):
        assert "traci" not in sys.modules
        assert "libsumo" not in sys.modules

    def test_the_declared_artifact_root_is_recorded_not_created(self):
        """The manifest names where a FUTURE approved run would write. This
        task created no such root, and nothing here opens it."""
        assert _load()["artifact_root"] == "runs/monthly-warm-state-validation"
        source = Path("tools/freeze_monthly_warm_state_v3.py").read_text()
        assert 'Path(manifest["artifact_root"])' not in source
