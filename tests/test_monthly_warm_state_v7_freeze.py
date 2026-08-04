"""The frozen v7 paired warm-state contract (LUNA-WARM-15).

v7 is frozen, UNAPPROVED and UNEXECUTED, and warming stays default-OFF. These
checks prove what the artifact is — reproducible, canonically keyed, correctly
bound, honestly scoped — and deliberately prove nothing about cold/warm
equivalence or any speedup, which only a separately approved campaign could
establish.

They also pin what v7 exists to get right: TraCI is resolved from the exact
active SUMO home with its ORIGIN proven, and that same resolver runs as a
mandatory campaign preflight before any artifact root can be created. v6 executed
(LUNA-WARM-14) and recorded the cause — `No module named 'traci'` on every
identity — which meant warming had never once started.

Process-free: no simulator, no subprocess, no `runs/` access.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

MANIFEST = Path("validation/monthly_warm_state_manifest_v7.json")
PARENT = Path("validation/monthly_warm_state_manifest_v6.json")
TOOL = Path("tools/freeze_monthly_warm_state_v7.py")


def _load():
    return json.loads(MANIFEST.read_text())


def _canonical_key(payload):
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def _freeze():
    sys.path.insert(0, "tools")
    import freeze_monthly_warm_state_v7 as module
    return module


class TestIdentityAndReproducibility:

    def test_the_content_key_recomputes_canonically(self):
        assert _canonical_key(_load()) == _load()["content_key"]

    def test_the_superseded_package_no_longer_recomposes_but_its_bytes_are_intact(
            self):
        """v7 is SUPERSEDED by v8, so recomposition must now differ.

        This test asserted byte-for-byte reproduction while v7 was current. That
        expectation expired when v8 took over and the harness — a bound source —
        legitimately changed. Drift invalidates REUSE of a superseded contract
        without touching a single stored byte, which is exactly what keeps it
        unadoptable; re-syncing the fingerprints would resurrect it.

        The immutability half of the original check is kept and strengthened: the
        stored bytes are pinned by hash, not merely compared before and after.
        """
        before = MANIFEST.read_bytes()
        rebuilt = _freeze().build_artifacts()[str(MANIFEST)]
        assert rebuilt != MANIFEST.read_text(), (
            "the superseded v7 contract still recomposes; it must not")
        assert MANIFEST.read_bytes() == before, "recomposition mutated v7"
        assert hashlib.sha256(before).hexdigest() == (
            "9c0ed7610d34e360a4a1c7600f38f7cc9211cb32106e8e3c444c18727008968e")

    def test_the_key_differs_from_every_spent_campaign(self):
        current = _load()["content_key"]
        for spent in ("v1", "v2", "v3", "v4", "v5", "v6"):
            other = json.loads(Path(
                f"validation/monthly_warm_state_manifest_{spent}.json").read_text())
            assert current != other["content_key"], spent

    def test_no_earlier_manifest_is_overwritten(self):
        for spent in ("v1", "v2", "v3", "v4", "v5", "v6"):
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


class TestPreservedAccumulatorAccounting:
    """The accounting, inherited unchanged from v4/v5: the offset v3 applied was
    refuted by measurement and must stay gone, visibly so."""

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
    """The state settings, inherited unchanged. v3 recorded them and applied
    neither; v4 fixed that, and v6 inherits the corrected contract."""

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


class TestWarmAttemptContract:
    """Criterion 6: the diagnostic contract is bound into the campaign."""

    def test_the_attempt_schema_and_vocabulary_are_recorded(self):
        from traffic_sim.simulation.monthly_sumo import (
            WARM_ATTEMPT_SCHEMA, WARM_INFORMATIONAL_CODES, WARM_OUTCOMES,
            WARM_TERMINAL_CODES)
        contract = _load()["warm_attempt_contract"]
        assert contract["schema"] == WARM_ATTEMPT_SCHEMA
        assert contract["outcomes"] == sorted(WARM_OUTCOMES)
        assert contract["terminal_codes"] == sorted(WARM_TERMINAL_CODES)
        assert contract["informational_codes"] == sorted(WARM_INFORMATIONAL_CODES)

    def test_coverage_counts_identities_not_events(self):
        """One attempt may record several events on its way to one outcome."""
        contract = _load()["warm_attempt_contract"]
        assert "never events" in contract["coverage_rule"]
        assert contract["required_attempts"] == 3

    def test_a_gap_forbids_publication(self):
        assert "forbids cache publication" in _load()["warm_attempt_contract"]["on_gap"]

    def test_the_runner_binds_the_attempt_producer(self):
        assert "traffic_sim/simulation/monthly_sumo.py" in \
            _load()["source_fingerprints"]
        assert "run_monthly_warm_state_validation.py" in \
            _load()["source_fingerprints"]

    def test_the_informational_and_terminal_codes_are_disjoint(self):
        """A cache miss must never be mistaken for a failure."""
        contract = _load()["warm_attempt_contract"]
        assert not (set(contract["terminal_codes"])
                    & set(contract["informational_codes"]))


class TestTheBootstrapDiagnosticIsWired:
    """What v6 exists to fix: the production call now forwards the attempt."""

    def test_the_production_call_site_forwards_the_attempt(self):
        import re
        source = re.sub(
            r"\s+", " ",
            Path("traffic_sim/simulation/monthly_sumo.py").read_text())
        call = re.search(r"self\.bootstrap_warm_state\((.*?)\)", source).group(1)
        assert "attempt=attempt" in call, call

    def test_the_bootstrap_source_is_bound_into_the_contract(self):
        """A campaign whose diagnostic emitter can change under an unchanged key
        would repeat exactly the failure v6 exists to prevent."""
        assert "traffic_sim/simulation/monthly_sumo.py" in \
            _load()["source_fingerprints"]


class TestTraciResolutionContract:
    """Criterion 6: the resolver rules are bound into the campaign identity."""

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

    def test_the_resolver_and_its_regression_are_bound_sources(self):
        srcs = _load()["source_fingerprints"]
        for name in ("traffic_sim/simulation/runtime.py",
                     "tests/test_sumo_runtime.py"):
            assert name in srcs, name

    def test_production_contains_no_bare_traci_import(self):
        import ast
        for name in ("traffic_sim/simulation/warm_state_boundary.py",
                     "run_monthly_warm_state_validation.py"):
            for node in ast.walk(ast.parse(Path(name).read_text())):
                if isinstance(node, ast.Import):
                    assert not any(a.name == "traci" for a in node.names), name


class TestSchemasAndSourceBinding:

    def test_the_live_schemas_are_recorded(self):
        from traffic_sim.simulation.monthly_warm_state import (
            PREFIX_EVIDENCE_SCHEMA)
        assert _load()["prefix_evidence_schema"] == PREFIX_EVIDENCE_SCHEMA
        assert _load()["prefix_evidence_schema"] == "monthly_prefix_evidence_v3"

    def test_the_accounting_is_deliberately_unchanged_from_the_parent(self):
        """v5 changes DIAGNOSTICS, not accounting. A silent objective change
        alongside a diagnostic one would be untraceable."""
        assert _load()["prefix_evidence_schema"] == \
            json.loads(PARENT.read_text())["prefix_evidence_schema"]
        assert _load()["accounting"]["rule"] == \
            json.loads(PARENT.read_text())["accounting"]["rule"]

    def test_the_bound_sources_have_drifted_which_is_what_retires_v7(self):
        """Superseded contracts drift. That drift is the retirement mechanism.

        While v7 was current every bound source matched the tree. v8 is now the
        live contract and the harness it binds has legitimately changed, so the
        match must FAIL — and the recorded hashes must stay frozen at what v7
        actually described. Re-syncing them would make a rejected, never-approved
        candidate look adoptable again.

        The drift set is asserted precisely rather than loosely, so an unnoticed
        change to some OTHER bound source still shows up here as a surprise. Two
        files legitimately moved: the harness, whose default became v8, and this
        very file — v7 binds its own regression suite, so retiring it necessarily
        drifts it. That self-reference is the mechanism working, not a flaw: a
        contract that binds its own tests cannot have them rewritten while its
        key still validates.
        """
        drifted = sorted(
            name for name, digest in _load()["source_fingerprints"].items()
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest)
        assert drifted, "v7 fingerprints match again; were they re-synced?"
        assert "run_monthly_warm_state_validation.py" in drifted
        assert set(drifted) <= {"run_monthly_warm_state_validation.py",
                                "tests/test_monthly_warm_state_v7_freeze.py"}, \
            drifted

    @pytest.mark.parametrize("module", [
        "traffic_sim/simulation/warm_state_boundary.py",
        "traffic_sim/simulation/warm_state_cache.py",
        "traffic_sim/simulation/monthly_warm_state.py",
        "traffic_sim/simulation/monthly_sumo.py",
        "run_monthly_warm_state_validation.py",
        "tools/freeze_monthly_warm_state_v7.py",
    ])
    def test_drift_in_a_bound_source_invalidates_the_contract(self, module):
        """Proven without touching the tree: one changed byte breaks the key."""
        manifest = _load()
        assert module in manifest["source_fingerprints"]
        mutated = Path(module).read_bytes() + b"\n# drift\n"
        assert (hashlib.sha256(mutated).hexdigest()
                != manifest["source_fingerprints"][module])

    def test_the_freeze_tool_binds_itself(self):
        assert "tools/freeze_monthly_warm_state_v7.py" in \
            _load()["source_fingerprints"]


class TestInheritanceFromTrackedV5:
    """v6 reads no archive; the physical facts come from the tracked v5 parent."""

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
        with pytest.raises(SystemExit, match="not the expected frozen v6"):
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

    def test_the_supersession_records_the_named_v6_cause(self):
        supersedes = _load()["supersedes"]
        assert supersedes["campaign"] == "v6"
        assert "No module named 'traci'" in supersedes["outcome"]
        assert "Zero warm executions" in supersedes["outcome"]
        assert supersedes["also_spent"] == ["v1", "v2", "v3", "v4", "v5"]


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

    def test_the_harness_default_is_no_longer_v7(self):
        """v7 is retired, so the default has moved on — and stays moved on.

        The earlier version also asserted the default WAS v8. That named a
        successor, which is a fact with an expiry date: it broke the moment v9
        landed, and a frozen historical suite must never need rewriting because
        the future arrived. Which manifest is current belongs to the generic
        current suite. What is durable here is only that it is not this one.
        """
        assert self._harness().DEFAULT_MANIFEST != MANIFEST

    def test_loading_v7_now_fails_closed_on_source_drift(self):
        """A superseded contract must be unloadable, not merely unfashionable.

        This asserted v7 loaded cleanly while it was current. Now the harness it
        binds has changed, so the identity is invalid and the loader refuses it.
        The refusal names the drifted source rather than failing vaguely.
        """
        with pytest.raises(SystemExit, match="frozen sources drifted") as caught:
            self._harness().load_frozen_manifest(MANIFEST)
        assert "run_monthly_warm_state_validation.py" in str(caught.value)

    def test_executing_v7_is_refused_before_approval_is_even_considered(self):
        """Creating the contract authorized nothing; superseding it authorizes
        less. The refusal now happens EARLIER than the approval-token check —
        a drifted identity is rejected before any token could be presented, so
        no approval could resurrect this campaign even if one were offered.
        """
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            self._harness().main(["--manifest", str(MANIFEST), "--execute"])


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


class TestAttemptGateInTheHarness:
    """Criteria 3-5: attempt evidence is a GATE, not decoration."""

    IDS = {("s1", "q10", 1000), ("s1", "q50", 1001), ("s1", "q90", 1002)}
    POINT = {"q10": 24300, "q50": 24300, "q90": 24300}

    def _harness(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "warm_harness_v7g", "run_monthly_warm_state_validation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _attempt(self, identity, outcome="warm_executed", code=None,
                 events=None):
        terminal = code or ("warm_completed" if outcome == "warm_executed"
                            else "invoker_declined")
        return {"schema": "monthly_warm_attempt_v1", "schedule_id": identity[0],
                "demand_variant": identity[1], "seed": identity[2],
                "outcome": outcome,
                "events": events or [{"code": terminal, "details": {}}]}

    def _comparison(self, identity, warm_arm="warm"):
        return {"schedule_id": identity[0], "demand_variant": identity[1],
                "seed": identity[2], "cold_arm": "cold", "warm_arm": warm_arm,
                "warm_point_s": 24300, "equivalent": True, "mismatches": []}

    def _states(self):
        class _Id:
            warmup_end_s = 24300
        return [{"schedule_id": s, "variant": v, "seed": d, "identity": _Id()}
                for s, v, d in sorted(self.IDS)]

    def _report(self, attempts, comparisons=None, published=None):
        return self._harness().execution_evidence_report(
            comparisons or [self._comparison(i) for i in sorted(self.IDS)],
            self._states(), self.IDS, self.POINT,
            published if published is not None
            else ["k%d" % i for i in range(3)],
            warm_attempts=attempts)

    def test_a_complete_set_of_attempts_is_accepted(self):
        report = self._report([self._attempt(i) for i in sorted(self.IDS)])
        assert report["complete"], report["problems"]
        assert report["warm_attempt_count"] == 3

    def test_the_attempts_are_persisted_in_the_evidence(self):
        """So a future failure can be read without a rerun."""
        report = self._report([self._attempt(i) for i in sorted(self.IDS)])
        assert len(report["warm_attempts"]) == 3
        assert {a["schedule_id"] for a in report["warm_attempts"]} == {"s1"}

    def test_a_missing_attempt_fails_the_record(self):
        report = self._report([self._attempt(i)
                               for i in sorted(self.IDS)][:2])
        assert not report["complete"]
        assert any("no warm attempt was recorded" in p
                   for p in report["problems"])

    def test_a_duplicate_attempt_fails_the_record(self):
        attempts = [self._attempt(i) for i in sorted(self.IDS)]
        attempts.append(self._attempt(sorted(self.IDS)[0]))
        report = self._report(attempts)
        assert not report["complete"]
        assert any("expected exactly 1" in p for p in report["problems"])

    def test_an_unexpected_attempt_fails_the_record(self):
        attempts = [self._attempt(i) for i in sorted(self.IDS)]
        attempts.append(self._attempt(("s1", "q99", 7)))
        report = self._report(attempts)
        assert not report["complete"]
        assert any("unexpected warm attempt" in p for p in report["problems"])

    def test_a_malformed_attempt_fails_the_record(self):
        attempts = [self._attempt(i) for i in sorted(self.IDS)]
        attempts[0] = dict(attempts[0], events=[])
        report = self._report(attempts)
        assert not report["complete"]
        assert any("malformed" in p for p in report["problems"])

    def test_a_warm_arm_without_a_successful_attempt_fails(self):
        """A contradiction between two things the same run reported."""
        attempts = [self._attempt(i) for i in sorted(self.IDS)]
        attempts[0] = self._attempt(sorted(self.IDS)[0], outcome="cold_fallback")
        report = self._report(attempts)
        assert not report["complete"]
        assert any("its attempt says" in p for p in report["problems"])

    def test_a_cold_arm_without_a_terminal_decline_fails(self):
        first = sorted(self.IDS)[0]
        comparisons = [self._comparison(i) for i in sorted(self.IDS)]
        comparisons[0] = self._comparison(first, warm_arm="cold")
        report = self._report([self._attempt(i) for i in sorted(self.IDS)],
                              comparisons=comparisons)
        assert not report["complete"]
        assert any("fell back to cold but its attempt says" in p
                   for p in report["problems"])

    def test_multi_event_bootstrap_success_is_accepted(self):
        """A cache miss then a bootstrap then success is one attempt, not three."""
        attempts = [self._attempt(i) for i in sorted(self.IDS)]
        attempts[0] = self._attempt(
            sorted(self.IDS)[0],
            events=[{"code": "cache_miss", "details": {"reason": "absent"}},
                    {"code": "bootstrap_started", "details": {}},
                    {"code": "warm_completed", "details": {}}])
        report = self._report(attempts)
        assert report["complete"], report["problems"]
        assert report["warm_attempt_count"] == 3, "events must not inflate coverage"

    def test_multi_event_bootstrap_failure_is_readable(self):
        first = sorted(self.IDS)[0]
        comparisons = [self._comparison(i) for i in sorted(self.IDS)]
        comparisons[0] = self._comparison(first, warm_arm="cold")
        attempts = [self._attempt(i) for i in sorted(self.IDS)]
        attempts[0] = self._attempt(
            first, outcome="cold_fallback",
            events=[{"code": "cache_miss", "details": {"reason": "absent"}},
                    {"code": "bootstrap_started", "details": {}},
                    {"code": "snapshot_failed",
                     "details": {"error": "sumo exited 1"}}])
        report = self._report(attempts, comparisons=comparisons)
        # The arm/outcome pair now agrees; the remaining problems are about the
        # warm arm not having run, which is the honest separate finding.
        recorded = report["warm_attempts"][0]
        assert recorded["events"][-1]["code"] == "snapshot_failed"
        assert "sumo exited 1" in recorded["events"][-1]["details"]["error"]

    def test_attempt_gaps_forbid_cache_publication(self):
        """Fail-closed: an unexplained fallback must never seed a warm cache."""
        report = self._report([self._attempt(i) for i in sorted(self.IDS)][:1])
        assert not report["complete"]
        record = self._harness().build_equivalence_record(
            _load(), [self._comparison(i) for i in sorted(self.IDS)],
            {"phase_runtime_s": {}, "peak_rss_bytes": 1},
            self.IDS, report)
        assert record["status"] == "fail"
        assert record["cache_material_publishable"] is False

    def test_the_record_canonicalization_includes_the_diagnostics(self):
        """A post-write change to the attempts must break the content key."""
        import copy
        report = self._report([self._attempt(i) for i in sorted(self.IDS)])
        harness = self._harness()
        record = harness.build_equivalence_record(
            _load(), [self._comparison(i) for i in sorted(self.IDS)],
            {"phase_runtime_s": {}, "peak_rss_bytes": 1}, self.IDS, report)
        tampered = copy.deepcopy(record)
        tampered["execution_evidence"]["warm_attempts"][0]["outcome"] = \
            "cold_fallback"
        assert harness.canonical_key(tampered) != record["content_key"]


class TestTheContractIsValidatedNotJustRecorded:
    """Sol review: a recorded vocabulary nobody checks can promise reasons this
    build cannot emit. `load_frozen_manifest` now validates it."""

    def _harness(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "warm_harness_v7c", "run_monthly_warm_state_validation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _write(self, tmp_path, manifest):
        path = tmp_path / "m.json"
        manifest = dict(manifest)
        manifest["content_key"] = _canonical_key(manifest)
        path.write_text(json.dumps(manifest))
        return path

    def test_a_well_formed_contract_does_not_save_a_superseded_manifest(self):
        """The recorded vocabulary still validates; the IDENTITY no longer does.

        This asserted v7 loaded and reported its campaign version while it was
        current. What it proves now is more useful: v7's warm-attempt contract
        has NOT drifted from production, so it clears every vocabulary gate this
        class exercises — and the load still fails, at the source-fingerprint
        check that runs last. A retired campaign cannot be readmitted on the
        strength of a contract that still looks well-formed.
        """
        harness = self._harness()
        manifest = _load()
        # Every contract gate this class tests would pass on these bytes...
        contract = manifest["warm_attempt_contract"]
        from traffic_sim.simulation.monthly_sumo import (
            WARM_ATTEMPT_SCHEMA, WARM_OUTCOMES)
        assert contract["schema"] == WARM_ATTEMPT_SCHEMA
        assert contract["outcomes"] == sorted(WARM_OUTCOMES)
        # ...and the manifest is refused anyway, on identity.
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(MANIFEST)

    @pytest.mark.parametrize("mutate, needle", [
        (lambda c: c.update(schema="monthly_warm_attempt_v0"),
         "warm-attempt schema"),
        (lambda c: c.update(outcomes=["warm_executed"]), "outcomes drifted"),
        (lambda c: c.update(terminal_codes=["invoker_declined"]),
         "terminal_codes drifted"),
        (lambda c: c.update(informational_codes=[]),
         "informational_codes drifted"),
        (lambda c: c.update(required_attempts=99), "warm attempts"),
    ])
    def test_a_drifted_contract_is_refused(self, tmp_path, mutate, needle):
        manifest = _load()
        contract = dict(manifest["warm_attempt_contract"])
        mutate(contract)
        manifest["warm_attempt_contract"] = contract
        with pytest.raises(SystemExit, match=needle):
            self._harness().load_frozen_manifest(
                self._write(tmp_path, manifest))

    def test_a_missing_contract_is_refused(self, tmp_path):
        manifest = _load()
        manifest.pop("warm_attempt_contract")
        with pytest.raises(SystemExit, match="no warm_attempt_contract"):
            self._harness().load_frozen_manifest(
                self._write(tmp_path, manifest))


class TestNetworkIdentityIsInherited:
    """Sol review: route safety describes the PARENT's network, so re-hashing
    the live file could pair a changed network with stale audits."""

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
        monkeypatch.setattr(module, "ROOT", root)
        with pytest.raises(SystemExit, match="no longer matches the network"):
            module.build_manifest()

    def test_the_inherited_route_safety_and_network_travel_together(self):
        """Both come from the parent, so they cannot describe different runs."""
        parent = json.loads(PARENT.read_text())
        assert _load()["route_safety"] == parent["route_safety"]
        assert _load()["network_requirement"] == parent["network_requirement"]

    def test_the_ledger_lists_the_network_among_the_inherited_facts(self):
        """Sol review: it was inherited and live-verified but unlisted, so the
        ledger described less than the manifest actually inherits."""
        assert "network_requirement" in _load()["inherited_from"]["fields"]

    def test_every_ledgered_field_really_is_identical_to_the_parent(self):
        parent = json.loads(PARENT.read_text())
        fields = _load()["inherited_from"]["fields"]
        assert set(fields) == {"route_safety", "archive_files_sha256",
                               "demand_requirement", "network_requirement"}
        for field in fields:
            assert _load()[field] == parent[field], field
