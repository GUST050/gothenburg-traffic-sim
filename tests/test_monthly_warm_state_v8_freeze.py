"""The frozen v8 paired warm-state contract (LUNA-WARM-15).

v8 is frozen, UNAPPROVED and UNEXECUTED, and warming stays default-OFF. These
checks prove what the artifact is — reproducible, canonically keyed, completely
bound, honestly scoped — and deliberately prove nothing about cold/warm
equivalence or any speedup, which only a separately approved campaign could
establish.

What v8 exists to fix is narrow and worth stating plainly. v7's resolver repair
was correct: TraCI resolved from the exact active SUMO home with its ORIGIN
proven, and that same resolver run as a mandatory preflight before any artifact
root can be created. But v7's fingerprint set omitted
`tests/test_warm_state_boundary.py` and `tests/test_monthly_warm_state.py`, so
the regressions that give the repair its meaning at the controller and
accounting boundaries could have been weakened while its key still validated.
v8 binds the complete set and enforces that at freeze time.

Warm execution has still never occurred, under any campaign in this family.

Process-free: no simulator, no subprocess, no `runs/` access.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

MANIFEST = Path("validation/monthly_warm_state_manifest_v8.json")
PARENT = Path("validation/monthly_warm_state_manifest_v7.json")
TOOL = Path("tools/freeze_monthly_warm_state_v8.py")

# The exact v7 bytes revision 4 is required to preserve. Pinned here so the
# preservation is a test, not a promise in a handoff note.
# The v7 artifacts that are genuinely immutable: the freeze tool and the
# manifest it produced. A superseded contract's EVIDENCE is frozen forever.
#
# Its TEST file is deliberately absent. Pinning it here was a lifecycle mistake:
# a versioned suite must be free to describe its own retirement, and v7's tests
# were rewritten — legitimately, under LUNA-WARM-15 revision 5 — to assert
# supersession once v8 became current. Pinning mutable predecessor test bytes
# forces an edit to THIS immutable-history map every time a successor lands,
# which is precisely the coupling frozen evidence exists to avoid.
FROZEN_V7 = {
    "tools/freeze_monthly_warm_state_v7.py":
        "6aea802198ff8f51176d1aad9efad22fb48bdc24ebe86b635a9203d1302ea468",
    "validation/monthly_warm_state_manifest_v7.json":
        "9c0ed7610d34e360a4a1c7600f38f7cc9211cb32106e8e3c444c18727008968e",
}


# v8 is RETIRED. It was superseded by v9 before it was ever approved or
# executed, and a superseded contract never becomes current again — so the
# assertions below state supersession outright rather than branching on it.
#
# An earlier version carried a currency-reading helper so these tests adapted to
# whichever manifest the harness defaulted to. That belongs in a suite whose
# contract might still BE current; here the adaptive branch was unreachable and
# only obscured what this suite is now for.


def _load():
    return json.loads(MANIFEST.read_text())


def _canonical_key(payload):
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def _freeze():
    sys.path.insert(0, "tools")
    import freeze_monthly_warm_state_v8 as module
    return module


class TestIdentityAndReproducibility:

    def test_the_content_key_recomputes_canonically(self):
        assert _canonical_key(_load()) == _load()["content_key"]

    def test_the_superseded_package_no_longer_recomposes_but_its_bytes_are_intact(
            self):
        """A retired contract must NOT rebuild from the live tree.

        Its bound sources have moved on, and that drift is what keeps a campaign
        nobody approved from being picked up and run. Re-syncing the fingerprints
        would resurrect it. The stored bytes are pinned by hash, so the artifact
        itself is proven untouched rather than merely compared to itself.
        """
        before = MANIFEST.read_bytes()
        rebuilt = _freeze().build_artifacts()[str(MANIFEST)]
        assert rebuilt != MANIFEST.read_text(), (
            "the superseded v8 contract still recomposes; it must not")
        assert MANIFEST.read_bytes() == before, "recomposition mutated v8"
        assert hashlib.sha256(before).hexdigest() == (
            "12c26f0dd93f079aeb9e9efb2939fd35b7cfc34fcc4185381c500a7ba980af17")

    def test_the_key_differs_from_every_earlier_campaign(self):
        current = _load()["content_key"]
        for other in ("v1", "v2", "v3", "v4", "v5", "v6", "v7"):
            payload = json.loads(Path(
                f"validation/monthly_warm_state_manifest_{other}.json").read_text())
            assert current != payload["content_key"], other

    def test_no_earlier_manifest_is_overwritten(self):
        for other in ("v1", "v2", "v3", "v4", "v5", "v6", "v7"):
            path = Path(f"validation/monthly_warm_state_manifest_{other}.json")
            assert path.is_file(), other

    def test_publication_refuses_to_clobber_the_existing_artifact(self):
        with pytest.raises(SystemExit, match="refusing to overwrite"):
            _freeze().publish(_freeze().build_artifacts())

    def test_the_cli_requires_exactly_one_mode(self):
        with pytest.raises(SystemExit, match="exactly one"):
            _freeze().main([])
        with pytest.raises(SystemExit, match="exactly one"):
            _freeze().main(["--write", "--verify"])


class TestTheReviewedV7CandidateIsPreservedExactly:
    """Criterion 1: v8 corrects v7 by SUCCEEDING it, never by editing it.

    A rejected candidate that gets quietly repaired in place leaves no record
    that the review found anything, and the next reader cannot tell which bytes
    were reviewed.
    """

    @pytest.mark.parametrize("name, digest", sorted(FROZEN_V7.items()))
    def test_the_v7_artifact_is_byte_identical(self, name, digest):
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name

    def test_v7_is_recorded_as_rejected_not_spent(self):
        """It was never approved and never ran, so 'spent' would overstate it."""
        review = _load()["v7_review"]
        assert review["disposition"] == "rejected_unapproved_unexecuted"
        assert review["content_key"] == json.loads(PARENT.read_text())["content_key"]

    def test_the_rejection_names_the_actual_defect(self):
        defect = _load()["v7_review"]["defect"]
        assert "tests/test_warm_state_boundary.py" in defect
        assert "tests/test_monthly_warm_state.py" in defect

    def test_what_v7_got_right_is_recorded_too(self):
        """A review record that lists only the fault misdescribes the work."""
        correct = _load()["v7_review"]["correct_in_v7"]
        assert "resolver repair" in correct and "unchanged" in correct

    def test_the_rejection_cost_no_campaign(self):
        assert "process-free" in _load()["v7_review"]["found_by"]

    def test_the_consumed_probe_is_not_overclaimed(self):
        """It proved an import resolves. It proved nothing about warming."""
        probe = _load()["v7_review"]["probe"]
        assert "CONSUMED" in probe
        assert "not rerun" in probe
        assert "never occurred" in probe


class TestTheRegressionBindingIsComplete:
    """Criterion 3: the exact defect v7 was rejected for cannot recur."""

    REQUIRED = ("tests/test_sumo_runtime.py",
                "tests/test_monthly_sumo.py",
                "tests/test_warm_state_boundary.py",
                "tests/test_monthly_warm_state.py",
                "tests/test_monthly_warm_state_freeze.py",
                "tests/test_monthly_warm_state_v8_freeze.py")

    @pytest.mark.parametrize("name", REQUIRED)
    def test_each_required_regression_is_fingerprinted(self, name):
        assert name in _load()["source_fingerprints"], name

    def test_the_two_regressions_v7_omitted_are_now_bound(self):
        srcs = _load()["source_fingerprints"]
        parent = json.loads(PARENT.read_text())["source_fingerprints"]
        for name in ("tests/test_warm_state_boundary.py",
                     "tests/test_monthly_warm_state.py"):
            assert name not in parent, f"{name} was already bound in v7?"
            assert name in srcs, name

    def test_the_manifest_names_exactly_what_it_binds(self):
        """A contract that DESCRIBES more than it fingerprints repeats v7's
        defect in a form that reads as fixed."""
        binding = _load()["regression_binding"]
        assert set(binding["required"]) == set(self.REQUIRED)
        for name in binding["required"]:
            assert name in _load()["source_fingerprints"], name

    def test_the_binding_is_enforced_by_the_freeze_not_asserted_in_prose(self):
        module = _freeze()
        assert _load()["regression_binding"]["enforced_at_freeze"] is True
        assert set(module.REQUIRED_REGRESSIONS) <= set(module.SOURCES)

    def test_the_freeze_refuses_an_incomplete_binding(self, monkeypatch):
        """The guard fires — proven by removing one, not by trusting the list."""
        module = _freeze()
        monkeypatch.setattr(
            module, "SOURCES",
            [s for s in module.SOURCES if s != "tests/test_warm_state_boundary.py"])
        with pytest.raises(SystemExit, match="not bound as sources"):
            module.verify_regression_binding()

    def test_the_freeze_refuses_a_regression_that_does_not_exist(self, monkeypatch):
        module = _freeze()
        monkeypatch.setattr(module, "REQUIRED_REGRESSIONS",
                            [*module.REQUIRED_REGRESSIONS, "tests/test_ghost.py"])
        monkeypatch.setattr(module, "SOURCES",
                            [*module.SOURCES, "tests/test_ghost.py"])
        with pytest.raises(SystemExit, match="do not exist"):
            module.verify_regression_binding()

    @pytest.mark.parametrize("module", [
        "tests/test_warm_state_boundary.py",
        "tests/test_monthly_warm_state.py",
        "tests/test_sumo_runtime.py",
        "traffic_sim/simulation/runtime.py",
        "run_monthly_warm_state_validation.py",
        "tools/freeze_monthly_warm_state_v8.py",
    ])
    def test_weakening_a_bound_regression_invalidates_the_key(self, module):
        """Proven without touching the tree: one changed byte breaks the key."""
        manifest = _load()
        assert module in manifest["source_fingerprints"]
        mutated = Path(module).read_bytes() + b"\n# drift\n"
        assert (hashlib.sha256(mutated).hexdigest()
                != manifest["source_fingerprints"][module])

    def test_the_freeze_tool_binds_itself(self):
        assert "tools/freeze_monthly_warm_state_v8.py" in \
            _load()["source_fingerprints"]

    def test_the_bound_sources_have_drifted_which_is_what_retires_v8(self):
        """Drift is the retirement mechanism, not damage to be repaired.

        The recorded hashes stay FROZEN at what v8 actually described. This
        suite must never be the reason someone re-syncs a retired contract.
        """
        drifted = sorted(
            name for name, digest in _load()["source_fingerprints"].items()
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest)
        assert drifted, "v8 fingerprints match again; were they re-synced?"


class TestApprovalAndScope:

    def test_it_is_frozen_unapproved_and_unexecuted(self):
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

    def test_zero_warm_executions_is_recorded_as_the_standing_fact(self):
        """The one number this whole family of campaigns has never moved."""
        history = _load()["execution_history"]
        assert history["warm_executions_to_date"] == 0
        assert "fell back cold" in history["note"]


class TestPreservedAccumulatorAccounting:
    """The accounting, inherited unchanged: the offset v3 applied was refuted by
    measurement and must stay gone, visibly so."""

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

    def test_production_never_applies_a_boundary_offset(self):
        """Structural, over the live tree rather than the manifest's prose."""
        for name in ("traffic_sim/simulation/monthly_sumo.py",
                     "traffic_sim/simulation/monthly_warm_state.py",
                     "traffic_sim/simulation/warm_state_boundary.py"):
            source = Path(name).read_text()
            assert "reconcile_split_time_loss" not in source, name
            assert "boundary_ledger" not in source, name

    def test_production_refuses_an_applied_offset_in_stored_evidence(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, validate_aggregation_summary)
        with pytest.raises(WarmStateContractError, match="double counts"):
            validate_aggregation_summary({
                "total_time_loss_s": 1.0, "trip_count": 1,
                "completed_prefix_trips": 1, "resumed_trips": 0,
                "boundary_offset_applied": True, "tripinfo_precision": 2})


class TestStateSettingsContract:

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

    def test_the_recorded_argv_is_preserved_but_retired(self):
        from traffic_sim.simulation.warm_state_boundary import (
            BoundaryLedgerError, validate_snapshot_command)
        argv = ["sumo", *_load()["state_settings"]["snapshot_arguments"]]
        assert argv == ["sumo", "--save-state.rng", "true",
                        "--save-state.precision", "16"]
        with pytest.raises(BoundaryLedgerError, match="--precision"):
            validate_snapshot_command(argv)

    def test_the_runner_appends_the_settings_to_its_prefix_command(self):
        """Structural: the actual snapshot command, not a claim about it."""
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        assert "*snapshot_settings_arguments()" in source


class TestTraciResolutionContract:
    """The resolver rules are bound into the campaign identity, unchanged."""

    def test_the_resolution_rule_is_recorded(self):
        from traffic_sim.simulation.runtime import (
            REQUIRED_TRACI_API, REQUIRED_TRACI_NAMESPACES, TOOLS_DIRNAME,
            TRACI_PACKAGE)
        rule = _load()["traci_resolution"]
        assert rule["package"] == TRACI_PACKAGE
        assert rule["tools_dirname"] == TOOLS_DIRNAME
        assert rule["required_api"] == list(REQUIRED_TRACI_API)
        assert rule["required_namespaces"] == {
            k: list(v) for k, v in sorted(REQUIRED_TRACI_NAMESPACES.items())}

    def test_the_resolver_rules_are_carried_from_the_parent_verbatim(self):
        """v8 changes what the contract BINDS, never how it resolves."""
        assert _load()["traci_resolution"] == \
            json.loads(PARENT.read_text())["traci_resolution"]

    def test_the_origin_rule_is_stated(self):
        """A module from a SUMO nobody chose is the failure this prevents."""
        rule = _load()["traci_resolution"]["rule"]
        assert "origin" in rule and "refused" in rule

    def test_the_preflight_order_is_stated(self):
        order = _load()["traci_resolution"]["preflight_order"]
        assert "approval token" in order
        assert order.index("approval token") < order.index("artifact-root")
        assert "creates no root" in order

    def test_the_controller_rule_is_stated(self):
        assert "starts no process" in \
            _load()["traci_resolution"]["controller_rule"]

    def test_the_v6_diagnosis_is_recorded_without_softening(self):
        d = _load()["v6_diagnosis"]
        assert d["warm_executions"] == 0
        assert "No module named 'traci'" in d["observed"]
        assert "bare `import traci`" in d["cause"]
        assert d["campaigns_spent_narrowing_it"] == ["v4", "v5", "v6"]

    def test_production_contains_no_bare_traci_import(self):
        for name in ("traffic_sim/simulation/warm_state_boundary.py",
                     "run_monthly_warm_state_validation.py"):
            for node in ast.walk(ast.parse(Path(name).read_text())):
                if isinstance(node, ast.Import):
                    assert not any(a.name == "traci" for a in node.names), name


class TestWarmAttemptContract:

    def test_the_frozen_attempt_vocabulary_is_internally_consistent(self):
        contract = _load()["warm_attempt_contract"]
        assert contract["schema"] == "monthly_warm_attempt_v1"
        assert contract["outcomes"] == ["cold_fallback", "warm_executed"]
        assert contract["terminal_codes"] == sorted(set(
            contract["terminal_codes"]))
        assert contract["informational_codes"] == sorted(set(
            contract["informational_codes"]))
        assert not (set(contract["terminal_codes"])
                    & set(contract["informational_codes"]))

    def test_coverage_counts_identities_not_events(self):
        """One attempt may record several events on its way to one outcome."""
        contract = _load()["warm_attempt_contract"]
        assert "never events" in contract["coverage_rule"]
        assert contract["required_attempts"] == 3

    def test_a_gap_forbids_publication(self):
        assert "forbids cache publication" in \
            _load()["warm_attempt_contract"]["on_gap"]

    def test_the_informational_and_terminal_codes_are_disjoint(self):
        """A cache miss must never be mistaken for a failure."""
        contract = _load()["warm_attempt_contract"]
        assert not (set(contract["terminal_codes"])
                    & set(contract["informational_codes"]))


class TestInheritanceFromTrackedV7:
    """v8 reads no archive; the physical facts come from the tracked v7 parent."""

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
        with pytest.raises(SystemExit, match="not the expected frozen v7"):
            module.load_parent()

    def test_the_inherited_facts_are_identical_to_the_parent(self):
        manifest, parent = _load(), json.loads(PARENT.read_text())
        for field in manifest["inherited_from"]["fields"]:
            assert manifest[field] == parent[field], field

    def test_every_ledgered_field_really_is_identical_to_the_parent(self):
        parent = json.loads(PARENT.read_text())
        fields = _load()["inherited_from"]["fields"]
        assert set(fields) == {"route_safety", "archive_files_sha256",
                               "demand_requirement", "network_requirement"}
        for field in fields:
            assert _load()[field] == parent[field], field

    def test_the_case_is_the_same_physical_closure(self):
        case = _load()["cases"][0]
        parent_case = json.loads(PARENT.read_text())["cases"][0]
        for field in ("directed_edges", "closure_begin_s", "closure_end_s",
                      "closure_bound_warm_point_s"):
            assert case[field] == parent_case[field], field

    def test_the_identity_set_is_the_parent_s(self):
        """v8 would run exactly what v7 would have run."""
        parent = json.loads(PARENT.read_text())
        manifest = _load()
        assert manifest["frozen_schedule_ids"] == parent["frozen_schedule_ids"]
        assert manifest["seeds"] == parent["seeds"]
        assert manifest["demand_variants"] == parent["demand_variants"]

    def test_the_freeze_refuses_a_drifted_identity_set(self, monkeypatch):
        module = _freeze()
        monkeypatch.setattr(module, "SEEDS", [1000, 1001, 1002])
        monkeypatch.setitem(module.CASES[0], "closure_begin_s", 25200)
        original = module.frozen_identity_set
        monkeypatch.setattr(module, "frozen_identity_set",
                            lambda: (["closure-not-the-frozen-one"], original()[1]))
        with pytest.raises(SystemExit, match="frozen schedule ids differ"):
            module.build_manifest()

    def test_route_safety_still_covers_all_three_variants(self):
        safety = _load()["route_safety"]
        assert set(safety) == {"q10", "q50", "q90"}
        for variant, record in safety.items():
            assert isinstance(record["safe_warm_point_s"], int), variant

    def test_the_network_identity_matches_the_parent_verbatim(self):
        assert _load()["network_requirement"] == \
            json.loads(PARENT.read_text())["network_requirement"]

    def test_the_freeze_refuses_a_network_that_no_longer_matches(
            self, tmp_path, monkeypatch):
        module = _freeze()
        parent = json.loads(PARENT.read_text())
        root = tmp_path / "repo"
        (root / "validation").mkdir(parents=True)
        (root / module.PARENT).write_text(PARENT.read_text())
        network = root / parent["network_requirement"]["path"]
        network.parent.mkdir(parents=True, exist_ok=True)
        network.write_text("a different network")
        # The regression-binding guard runs first and is checked against ROOT,
        # so the fake tree must carry those files or THAT is what fails and the
        # network check is never reached.
        for name in module.REQUIRED_REGRESSIONS:
            stub = root / name
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text("# stub\n")
        monkeypatch.setattr(module, "ROOT", root)
        with pytest.raises(SystemExit, match="no longer matches the network"):
            module.build_manifest()

    def test_the_supersession_records_v7_as_rejected_not_executed(self):
        supersedes = _load()["supersedes"]
        assert supersedes["campaign"] == "v7"
        assert "REJECTED" in supersedes["outcome"]
        assert "Never run, never approved" in supersedes["outcome"]
        assert supersedes["also_spent"] == ["v1", "v2", "v4", "v5", "v6"]
        assert supersedes["also_superseded_unexecuted"] == ["v3"]

    def test_the_never_executed_campaigns_are_not_called_spent(self):
        """v3 and v7 were superseded on review; calling them spent would imply
        a campaign was burned that never was."""
        supersedes = _load()["supersedes"]
        assert "v3" not in supersedes["also_spent"]
        assert "v7" not in supersedes["also_spent"]


class TestSchemasAndAccountingAreUnchanged:

    def test_the_recorded_schema_is_frozen_and_no_longer_live(self):
        from traffic_sim.simulation.monthly_warm_state import (
            PREFIX_EVIDENCE_SCHEMA)
        assert _load()["prefix_evidence_schema"] == "monthly_prefix_evidence_v3"
        assert _load()["prefix_evidence_schema"] != PREFIX_EVIDENCE_SCHEMA

    def test_the_accounting_is_deliberately_unchanged_from_the_parent(self):
        """v8 changes BINDING, not accounting. A silent objective change
        alongside a binding one would be untraceable."""
        parent = json.loads(PARENT.read_text())
        assert _load()["prefix_evidence_schema"] == parent["prefix_evidence_schema"]
        assert _load()["accounting"] == parent["accounting"]

    def test_the_snapshot_schema_is_recorded(self):
        from traffic_sim.simulation.warm_state_boundary import (
            SNAPSHOT_FACTS_SCHEMA)
        assert _load()["snapshot_facts_schema"] == SNAPSHOT_FACTS_SCHEMA

    def test_the_hypothesis_is_carried_unchanged_and_still_unproven(self):
        hypothesis = _load()["hypothesis"]
        assert hypothesis["status"].startswith("UNPROVEN")
        assert hypothesis == json.loads(PARENT.read_text())["hypothesis"]

    def test_the_refutation_condition_is_stated_before_execution(self):
        """A campaign that cannot fail proves nothing."""
        condition = _load()["hypothesis"]["refutation_condition"]
        assert "REFUTED" in condition and "fails honestly" in condition


class TestHarnessDefault:

    def _harness(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "warm_harness_v8", "run_monthly_warm_state_validation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_harness_default_has_moved_past_v8(self):
        """v9 superseded v8. Which manifest is CURRENT is proved in the generic
        suite, which tracks the pointer by construction; this only asserts that
        v8 is no longer it."""
        assert self._harness().DEFAULT_MANIFEST != MANIFEST

    def test_loading_a_superseded_v8_fails_closed_on_source_drift(self):
        """Unloadable, not merely unfashionable. The refusal names the drift."""
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            self._harness().load_frozen_manifest(MANIFEST)

    def test_a_superseded_v8_never_reaches_the_approval_gate(self):
        """Creating the contract authorized nothing; superseding it authorizes
        less. `--execute` is refused on the drifted identity BEFORE the approval
        token is considered, so no token could resurrect this campaign even if
        one were offered."""
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            self._harness().main(["--manifest", str(MANIFEST), "--execute"])

    def test_a_drifted_source_is_refused_by_the_harness(self, tmp_path):
        manifest = _load()
        manifest["source_fingerprints"] = dict(
            manifest["source_fingerprints"],
            **{"tests/test_warm_state_boundary.py": "0" * 64})
        manifest["content_key"] = _canonical_key(manifest)
        path = tmp_path / "drifted.json"
        path.write_text(json.dumps(manifest))
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            self._harness().load_frozen_manifest(path)

    def test_a_tampered_resolver_contract_is_refused(self, tmp_path):
        """The resolver rules are identity: changing them must break the key."""
        manifest = _load()
        manifest["traci_resolution"] = dict(manifest["traci_resolution"],
                                            package="not_traci")
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(manifest))   # key deliberately NOT recomputed
        with pytest.raises(SystemExit, match="content key does not recompute"):
            self._harness().load_frozen_manifest(path)

    def test_v7_is_naturally_non_current_without_editing_a_v7_byte(self):
        """Superseded contracts drift and stop validating. That drift is the
        mechanism keeping them unadoptable — it is never repaired."""
        harness = self._harness()
        assert harness.DEFAULT_MANIFEST != PARENT
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(PARENT)
        assert hashlib.sha256(PARENT.read_bytes()).hexdigest() == \
            FROZEN_V7["validation/monthly_warm_state_manifest_v7.json"]


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

    def test_the_freeze_tool_reads_no_runs_path(self):
        """Inheritance comes from tracked `validation/` artifacts only."""
        source = TOOL.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not node.value.startswith("runs/") or \
                    node.value == "runs/monthly-warm-state-validation", node.value
