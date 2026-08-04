"""The frozen v10 paired warm-state contract (LUNA-WARM-23).

v10 is frozen, UNAPPROVED and UNEXECUTED, and warming stays default-OFF. These
checks prove what the artifact is — reproducible, canonically keyed, completely
bound, honestly scoped — and deliberately prove nothing about cold/warm
equivalence or any speedup, which only a separately approved campaign could
establish.

v10 exists to test ONE correction, and it is the first contract in this family
written against a MEASURED failure rather than a hypothesis about one.

v9 executed under LUNA-WARM-16 — the first campaign whose warm arm actually ran —
and failed with a residual of -7.73 / -80.62 / -138.97 s, bit-identical to v2's
and therefore not caused by the state-serialization settings v9 applied. The
LUNA-WARM-22 forensic diagnostic then localized it exactly: 5 of 44, 10 of 50 and
12 of 51 vehicles IN FLIGHT across the warm point carry the entire gap between
them, all negative, all in the resumed phase, and most of them restored with
exactly 0.0 accumulated time loss. Every other vehicle — 99.99% — is identical.

So both earlier rules are refuted. A blanket per-vehicle offset (v3) would double
count the ~80% whose accumulator survives; assuming universal preservation
(generalised from LUNA-WARM-08's single-vehicle probe) loses the minority that IS
the residual. v10 binds a selective correction instead: measure each active
vehicle's accumulated time loss at the save instant and again immediately after
the load, and restore only the difference actually observed. Why those particular
vehicles lose it remains unknown, and this correction does not need to know.

This suite also keeps the lifecycle rules v9 established:
  * immutable-history maps pin predecessor TOOL and MANIFEST bytes only;
  * nothing here asserts that the harness default IS v10 — that expires. Which
    manifest is current is proved in the generic current suite, and the few
    currency-dependent facts below read the answer from production and adapt.

Warm execution HAS now occurred. Warm equivalence has not.

Process-free: no simulator, no subprocess, no `runs/` access.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

MANIFEST = Path("validation/monthly_warm_state_manifest_v10.json")
PARENT = Path("validation/monthly_warm_state_manifest_v9.json")
TOOL = Path("tools/freeze_monthly_warm_state_v10.py")

# The v9 artifacts that are genuinely immutable: the freeze tool and the manifest
# it produced. Deliberately NOT the v8 test file — see the module docstring, and
# `test_no_predecessor_test_file_is_pinned` below, which enforces it here too.
FROZEN_V9 = {
    "tools/freeze_monthly_warm_state_v9.py":
        "23bfb8c0118bb1580f7c128411fa6e2471e6262086bbd9e5cb02758ff291ab4b",
    "validation/monthly_warm_state_manifest_v9.json":
        "556e6a6fd489b4b7d0527970bb7d2bfa713b313cf0d261c4c0c26bf892afa8a2",
}

# The EXECUTED residual diagnostic that localized v9's failure. Pinned because
# this campaign's whole design rests on its measurement; tool and contract only,
# never a test file — see the lifecycle rules.
FROZEN_RESIDUAL_V2 = {
    "tools/diagnose_monthly_warm_state_residual_v2.py":
        "4ec7284dc3e0a507fab5552f23f43b3c9fa2c425695523490d1f0fa668367995",
    "validation/monthly_warm_state_residual_contract_v2.json":
        "d583b7065dfae6e4312319c619d0e75e96f9b6ca34e743df560cde46dd77a892",
}


def _load():
    return json.loads(MANIFEST.read_text())


def _canonical_key(payload):
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def _freeze():
    sys.path.insert(0, "tools")
    import freeze_monthly_warm_state_v10 as module
    return module


def _harness():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "warm_harness_v10", "run_monthly_warm_state_validation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_current():
    """Read currency from production instead of hardcoding it.

    This is the mechanism that lets the assertions below outlive v10's tenure as
    the default: they describe both sides of the lifecycle and pick the right
    one, rather than needing a rewrite when a successor lands.
    """
    return _harness().DEFAULT_MANIFEST == MANIFEST


class TestIdentityAndReproducibility:

    def test_the_content_key_recomputes_canonically(self):
        assert _canonical_key(_load()) == _load()["content_key"]

    def test_recomposition_tracks_currency_and_never_mutates_the_artifact(self):
        """Reproduces byte-for-byte while current; differs once superseded.

        Two ends of one lifecycle. A CURRENT contract must recompose exactly or
        its key describes something the tree cannot rebuild; a SUPERSEDED one
        must not, because its bound sources moved on — and that drift is what
        keeps a retired campaign unadoptable.
        """
        before = MANIFEST.read_bytes()
        rebuilt = _freeze().build_artifacts()[str(MANIFEST)]
        if _is_current():
            assert rebuilt == MANIFEST.read_text()
        else:
            assert rebuilt != MANIFEST.read_text(), (
                "a superseded v10 still recomposes; it must not")
        assert MANIFEST.read_bytes() == before, "recomposition mutated v10"

    def test_the_key_differs_from_every_earlier_campaign(self):
        current = _load()["content_key"]
        for other in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"):
            payload = json.loads(Path(
                f"validation/monthly_warm_state_manifest_{other}.json").read_text())
            assert current != payload["content_key"], other

    def test_no_earlier_manifest_is_overwritten(self):
        for other in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"):
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


class TestLifecycleRules:
    """The lifecycle rules v9 established, still enforced at freeze time."""

    def test_the_immutable_history_map_pins_tools_and_manifests_only(self):
        for name in FROZEN_V9:
            assert name.endswith((".py", ".json"))
            assert not name.startswith("tests/"), name

    @pytest.mark.parametrize("name, digest", sorted(FROZEN_V9.items()))
    def test_the_v9_artifact_is_byte_identical(self, name, digest):
        """A superseded contract's tool and manifest are frozen forever."""
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name

    def test_no_predecessor_test_file_is_pinned(self):
        """Rule 1, checked against this file's own source.

        v8 pinned `tests/test_monthly_warm_state_v7_freeze.py`. That file then
        had to change to describe its own retirement, which made v8's immutable
        map wrong — and unfixable without editing frozen evidence.
        """
        source = Path(__file__).read_text()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Dict):
                continue
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    assert not (key.value.startswith("tests/")
                                and key.value.endswith("_freeze.py")), key.value

    def test_this_suite_never_asserts_it_is_the_current_default(self):
        """Rule 2, checked against this file's own source.

        Asserting `DEFAULT_MANIFEST == MANIFEST` is true only until a successor
        lands. Currency is proved in the generic current suite; here it may only
        be READ (via `_is_current`, which branches on it) or negated.

        The scope is `assert` statements specifically, mirroring the freeze
        tool's rule. My first version of both checked EVERY comparison and so
        flagged `_is_current` itself — a suite that reads currency to adapt is
        the goal, not the violation.
        """
        source = Path(__file__).read_text()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Assert):
                continue
            for inner in ast.walk(node.test):
                if isinstance(inner, ast.Compare) and inner.ops and \
                        isinstance(inner.ops[0], ast.Eq):
                    rendered = ast.dump(inner)
                    assert not ("DEFAULT_MANIFEST" in rendered
                                and "MANIFEST" in rendered), rendered

    def test_the_freeze_tool_enforces_rule_one(self, tmp_path, monkeypatch):
        """The guard fires — proven by feeding it a violation."""
        module = _freeze()
        bad = tmp_path / "suite.py"
        bad.write_text('FROZEN = {"tests/test_monthly_warm_state_v8_freeze.py": '
                       '"%s"}\n' % ("a" * 64))
        monkeypatch.setattr(module, "ROOT", tmp_path)
        with pytest.raises(SystemExit, match="pins a predecessor test file"):
            module.verify_lifecycle_rules("suite.py")

    def test_the_freeze_tool_enforces_rule_two(self, tmp_path, monkeypatch):
        module = _freeze()
        bad = tmp_path / "suite.py"
        bad.write_text("def test_current():\n"
                       "    assert _harness().DEFAULT_MANIFEST == MANIFEST\n")
        monkeypatch.setattr(module, "ROOT", tmp_path)
        with pytest.raises(SystemExit, match="asserts the harness default IS"):
            module.verify_lifecycle_rules("suite.py")

    def test_a_negated_currency_check_is_permitted(self, tmp_path, monkeypatch):
        """"No longer current" stays true forever once true, so it never needs
        rewriting and must not be banned."""
        module = _freeze()
        ok = tmp_path / "suite.py"
        ok.write_text("def test_retired():\n"
                      "    assert _harness().DEFAULT_MANIFEST != MANIFEST\n")
        monkeypatch.setattr(module, "ROOT", tmp_path)
        module.verify_lifecycle_rules("suite.py")          # no raise

    def test_a_missing_suite_fails_closed(self, tmp_path, monkeypatch):
        module = _freeze()
        monkeypatch.setattr(module, "ROOT", tmp_path)
        with pytest.raises(SystemExit, match="versioned suite is missing"):
            module.verify_lifecycle_rules("nope.py")

    def test_the_rules_are_recorded_in_the_contract(self):
        rules = _load()["lifecycle_rules"]
        assert "tools and manifests" in rules["immutable_history_pins"] or \
            "TOOL and MANIFEST" in rules["immutable_history_pins"]
        assert "current suite" in \
            rules["no_currency_assertions_in_versioned_suites"]
        assert "never repaired or re-synced" in \
            rules["drift_is_the_retirement_mechanism"]
        assert rules["enforced_at_freeze"] is True

    def test_the_v9_result_is_recorded_without_softening_it(self):
        """v9 RAN and FAILED. It is the one campaign in this family that warmed,
        and the record must say both halves plainly."""
        review = _load()["v9_result"]
        assert review["disposition"] == "executed_failed"
        assert "warm execution SUCCEEDED" in review["outcome"]
        assert "FAILED" in review["outcome"]
        assert "no cache published" in review["outcome"]
        assert "slower" in review["performance"]


class TestTheRegressionBindingIsComplete:
    """Inherited from v8/v9: the defect v7 was rejected for cannot recur."""

    REQUIRED = ("tests/test_sumo_runtime.py",
                "tests/test_monthly_sumo.py",
                "tests/test_warm_state_boundary.py",
                "tests/test_monthly_warm_state.py",
                "tests/test_monthly_warm_state_freeze.py",
                "tests/test_monthly_warm_state_v10_freeze.py")

    @pytest.mark.parametrize("name", REQUIRED)
    def test_each_required_regression_is_fingerprinted(self, name):
        assert name in _load()["source_fingerprints"], name

    def test_the_manifest_names_exactly_what_it_binds(self):
        binding = _load()["regression_binding"]
        assert set(binding["required"]) == set(self.REQUIRED)
        for name in binding["required"]:
            assert name in _load()["source_fingerprints"], name

    def test_the_binding_is_enforced_by_the_freeze_not_asserted_in_prose(self):
        module = _freeze()
        assert _load()["regression_binding"]["enforced_at_freeze"] is True
        assert set(module.REQUIRED_REGRESSIONS) <= set(module.SOURCES)

    def test_the_freeze_refuses_an_incomplete_binding(self, monkeypatch):
        module = _freeze()
        monkeypatch.setattr(
            module, "SOURCES",
            [s for s in module.SOURCES if s != "tests/test_warm_state_boundary.py"])
        with pytest.raises(SystemExit, match="not bound as sources"):
            module.verify_regression_binding()

    def test_the_source_binding_tracks_currency(self):
        """Matches the live tree while current; drift is expected after.

        The recorded hashes stay FROZEN either way — drift retires a contract
        and must never be re-synced.
        """
        drifted = sorted(
            name for name, digest in _load()["source_fingerprints"].items()
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest)
        if _is_current():
            assert not drifted, drifted
        else:
            assert drifted, "v9 fingerprints match again; were they re-synced?"

    def test_the_freeze_tool_binds_itself(self):
        assert "tools/freeze_monthly_warm_state_v10.py" in \
            _load()["source_fingerprints"]

    @pytest.mark.parametrize("module", [
        "tests/test_warm_state_boundary.py",
        "tests/test_monthly_warm_state.py",
        "tests/test_sumo_runtime.py",
        "traffic_sim/simulation/runtime.py",
        "tools/freeze_monthly_warm_state_v10.py",
    ])
    def test_weakening_a_bound_regression_invalidates_the_key(self, module):
        """Proven without touching the tree: one changed byte breaks the key."""
        manifest = _load()
        assert module in manifest["source_fingerprints"]
        mutated = Path(module).read_bytes() + b"\n# drift\n"
        assert (hashlib.sha256(mutated).hexdigest()
                != manifest["source_fingerprints"][module])


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

    def test_the_execution_history_records_that_warming_finally_ran(self):
        """The number this family never moved for nine contracts — and did.

        v9 warmed all three identities under LUNA-WARM-16. Warm EXECUTION is no
        longer unprecedented; warm EQUIVALENCE is still unproven, and the note
        must not blur the two.
        """
        history = _load()["execution_history"]
        assert history["warm_executions_to_date"] == 3
        assert "still unproven" in history["note"]
        assert "fell back cold" in history["note"]


class TestInheritedRulesAreUnchanged:
    """v9 changes how a contract retires, never how anything runs."""

    def test_the_resolver_rules_are_carried_from_the_parent_verbatim(self):
        assert _load()["traci_resolution"] == \
            json.loads(PARENT.read_text())["traci_resolution"]

    def test_the_accounting_changes_deliberately_and_says_so(self):
        """v10 is the first contract since v4 to change the objective rule, and
        it must be visible rather than inherited silently."""
        parent = json.loads(PARENT.read_text())
        assert _load()["prefix_evidence_schema"] != parent["prefix_evidence_schema"]
        assert _load()["prefix_evidence_schema"] == "monthly_prefix_evidence_v4"
        correction = _load()["restore_correction"]
        assert "ONLY the observed positive difference" in correction["rule"]
        assert correction["nothing_is_inferred"]

    def _superseded_accounting_check(self):
        parent = json.loads(PARENT.read_text())
        assert _load()["accounting"] == parent["accounting"]
        assert _load()["prefix_evidence_schema"] == parent["prefix_evidence_schema"]

    def test_the_hypothesis_is_carried_unchanged_and_still_unproven(self):
        hypothesis = _load()["hypothesis"]
        assert hypothesis["status"].startswith("UNPROVEN")
        assert hypothesis == json.loads(PARENT.read_text())["hypothesis"]

    def test_the_warm_attempt_contract_matches_production(self):
        from traffic_sim.simulation.monthly_sumo import (
            WARM_ATTEMPT_SCHEMA, WARM_INFORMATIONAL_CODES, WARM_OUTCOMES,
            WARM_TERMINAL_CODES)
        contract = _load()["warm_attempt_contract"]
        assert contract["schema"] == WARM_ATTEMPT_SCHEMA
        assert contract["outcomes"] == sorted(WARM_OUTCOMES)
        assert contract["terminal_codes"] == sorted(WARM_TERMINAL_CODES)
        assert contract["informational_codes"] == sorted(WARM_INFORMATIONAL_CODES)
        assert not (set(contract["terminal_codes"])
                    & set(contract["informational_codes"]))

    def test_the_state_settings_match_the_cache_constants(self):
        from traffic_sim.simulation.warm_state_boundary import (
            snapshot_settings_arguments, validate_snapshot_command)
        from traffic_sim.simulation.warm_state_cache import (
            STATE_PRECISION, STATE_RNG_SAVED)
        settings = _load()["state_settings"]
        assert settings["save_state_precision"] == STATE_PRECISION
        assert settings["save_state_rng"] is STATE_RNG_SAVED
        assert settings["snapshot_arguments"] == snapshot_settings_arguments()
        assert validate_snapshot_command(
            ["sumo", *settings["snapshot_arguments"]]) == {
                "save_state_rng": True, "save_state_precision": 16}

    def test_production_never_applies_a_boundary_offset(self):
        """Structural, over the live tree rather than the manifest's prose."""
        for name in ("traffic_sim/simulation/monthly_sumo.py",
                     "traffic_sim/simulation/monthly_warm_state.py",
                     "traffic_sim/simulation/warm_state_boundary.py"):
            source = Path(name).read_text()
            assert "reconcile_split_time_loss" not in source, name
            assert "boundary_ledger" not in source, name

    def test_the_v6_diagnosis_is_recorded_without_softening(self):
        d = _load()["v6_diagnosis"]
        assert d["warm_executions"] == 0
        assert "No module named 'traci'" in d["observed"]
        assert "bare `import traci`" in d["cause"]

    def test_production_contains_no_bare_traci_import(self):
        for name in ("traffic_sim/simulation/warm_state_boundary.py",
                     "run_monthly_warm_state_validation.py"):
            for node in ast.walk(ast.parse(Path(name).read_text())):
                if isinstance(node, ast.Import):
                    assert not any(a.name == "traci" for a in node.names), name


class TestInheritanceFromTrackedV8:
    """v9 reads no archive; the physical facts come from the tracked v8 parent."""

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
        with pytest.raises(SystemExit, match="not the expected frozen v9"):
            module.load_parent()

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

    def test_the_identity_set_is_the_parents(self):
        """v9 would run exactly what v8 would have run."""
        parent = json.loads(PARENT.read_text())
        manifest = _load()
        assert manifest["frozen_schedule_ids"] == parent["frozen_schedule_ids"]
        assert manifest["seeds"] == parent["seeds"]
        assert manifest["demand_variants"] == parent["demand_variants"]

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
        # The regression-binding and lifecycle guards run first and read ROOT,
        # so the fake tree must carry those files or THAT is what fails.
        for name in module.REQUIRED_REGRESSIONS:
            stub = root / name
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text("# stub\n")
        (root / module.VERSIONED_SUITE).write_text("# stub\n")
        monkeypatch.setattr(module, "ROOT", root)
        with pytest.raises(SystemExit, match="no longer matches the network"):
            module.build_manifest()

    def test_the_supersession_records_v9_as_executed_and_failed(self):
        supersedes = _load()["supersedes"]
        assert supersedes["campaign"] == "v9"
        assert "EXECUTED once" in supersedes["outcome"]
        assert "FAILED honestly" in supersedes["outcome"]
        assert supersedes["also_spent"] == ["v1", "v2", "v4", "v5", "v6"]
        assert supersedes["also_superseded_unexecuted"] == ["v3", "v7", "v8"]

    def test_the_never_executed_campaigns_are_not_called_spent(self):
        """v3, v7 and v8 were retired on review; calling them spent would imply
        a campaign was burned that never was."""
        supersedes = _load()["supersedes"]
        for never_run in ("v3", "v7", "v8"):
            assert never_run not in supersedes["also_spent"], never_run


class TestHarnessBehaviourThatDoesNotExpire:
    """Currency itself is proved in the generic current suite. What is asserted
    here stays true for the rest of v9's existence."""

    def test_a_drifted_source_is_refused_by_the_harness(self, tmp_path):
        manifest = _load()
        manifest["source_fingerprints"] = dict(
            manifest["source_fingerprints"],
            **{"tests/test_warm_state_boundary.py": "0" * 64})
        manifest["content_key"] = _canonical_key(manifest)
        path = tmp_path / "drifted.json"
        path.write_text(json.dumps(manifest))
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            _harness().load_frozen_manifest(path)

    def test_a_tampered_key_is_refused(self, tmp_path):
        manifest = dict(_load(), simulation_mode="micro")
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(manifest))   # key deliberately NOT recomputed
        with pytest.raises(SystemExit, match="content key does not recompute"):
            _harness().load_frozen_manifest(path)

    def test_the_predecessors_fail_closed_on_drift(self):
        """Retired contracts stay unloadable without editing one of their bytes."""
        harness = _harness()
        for retired in ("v7", "v9"):
            path = Path(f"validation/monthly_warm_state_manifest_{retired}.json")
            # A retired contract must be REFUSED; which gate catches it first
            # is not the property under test, and asserting one exact message
            # made this break whenever an earlier gate started firing.
            with pytest.raises(SystemExit):
                harness.load_frozen_manifest(path)


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

    def test_the_freeze_tool_reads_no_runs_path(self):
        """Inheritance comes from tracked `validation/` artifacts only."""
        for node in ast.walk(ast.parse(TOOL.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not node.value.startswith("runs/") or \
                    node.value == "runs/monthly-warm-state-validation", node.value
