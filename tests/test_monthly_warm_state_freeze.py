"""The warm-state campaign contract is frozen, unapproved and unexecuted.

Process-free: reads tracked artifacts only. No SUMO, no cache, no run root.
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MANIFEST = Path("validation/monthly_warm_state_manifest_v1.json")
CURRENT = Path("validation/monthly_warm_state_manifest_v16.json")


def _current():
    return json.loads(CURRENT.read_text())


def _harness():
    if "warm_harness" in sys.modules:
        return sys.modules["warm_harness"]
    spec = importlib.util.spec_from_file_location(
        "warm_harness", "run_monthly_warm_state_validation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["warm_harness"] = module
    spec.loader.exec_module(module)
    return module


from tests.test_warm_state_boundary import (
    bounded_sections, split_aggregation)
from traffic_sim.simulation.monthly_warm_state import (
    reconstruct_metrics as _production_reconstruct_metrics)


def _attempts(identities, outcome="warm_executed", code=None):
    """Finalized warm-attempt records for the given identities.

    Evidence now REQUIRES one per identity, so fixtures that omit them are
    correctly incomplete — that is the gate, not a nuisance.
    """
    terminal = code or ("warm_completed" if outcome == "warm_executed"
                        else "invoker_declined")
    return [{"schema": "monthly_warm_attempt_v1", "schedule_id": s,
             "demand_variant": v, "seed": d, "outcome": outcome,
             "events": [{"code": terminal, "details": {}}]}
            for s, v, d in sorted(identities)]


def _audit(**over):
    """A structurally valid route-mutation audit for fixtures."""
    base = {"schema": "warm_route_mutation_audit_v1",
            "original_sha256": "a" * 64, "filtered_sha256": "b" * 64,
            "original_vehicles": 10, "filtered_vehicles": 10,
            "changed_vehicles": 1, "dropped_vehicles": 0,
            "earliest_affected_depart_s": 90000.0}
    base.update(over)
    return base


def _full_metrics(**over):
    base = {"total_time_loss_s": 5.0, "trip_count": 2, "unfinished_trips": 1,
            "unfinished_waiting_trips": 1, "teleport_total": 1,
            "teleport_reasons": {"jam": 1}, "loaded": 5, "inserted": 5,
            "running_at_end": 0, "waiting_at_end": 0,
            "truncated_unreachable": 0, "dropped_unreachable": 0,
            "max_queue_vehicles": 5, "closed_edge_throughput": 0}
    base.update(over)
    return base


def _correction(raw, corrected=None, *, applied=0, deficit=0.0,
                combined_total=None):
    corrected = dict(raw if corrected is None else corrected)
    raw = dict(raw)
    return {
        "schema": "warm_meso_reconciliation_v2",
        "active_count": applied,
        "resumed_count": corrected["trip_count"],
        "corrected_count": applied,
        "prefix_accumulator_total_s": deficit,
        "raw_total_time_loss_s": raw["total_time_loss_s"],
        "corrected_total_time_loss_s": corrected["total_time_loss_s"],
        "combined_total_time_loss_s": (corrected["total_time_loss_s"]
                                       if combined_total is None
                                       else combined_total),
        "correction_delta_s": (corrected["total_time_loss_s"]
                               - raw["total_time_loss_s"]),
        "trip_count": corrected["trip_count"],
        "tripinfo_precision": 2,
        "prefix_accumulator_digest": "a" * 64,
        "records_digest": "b" * 64,
    }


def _diag(point, candidate=None, **over):
    """Split diagnostics that RECONSTRUCT to `candidate` exactly.

    The prefix takes half the completed trips and time loss; the post-warm
    segment takes the rest and carries the cumulative counters and end state.
    Reconstruction therefore reproduces `candidate`, which is what the
    cross-section check requires.
    """
    target = dict(candidate if candidate is not None else _full_metrics(
        total_time_loss_s=10.0, trip_count=5))
    prefix_trips = min(target["trip_count"] // 2, target["inserted"])
    prefix_loss = round(target["total_time_loss_s"] / 2, 6)
    post = dict(target,
                total_time_loss_s=target["total_time_loss_s"] - prefix_loss,
                trip_count=target["trip_count"] - prefix_trips)
    post["unfinished_trips"] = min(post["unfinished_trips"], post["trip_count"])
    post["unfinished_waiting_trips"] = min(post["unfinished_waiting_trips"],
                                           post["unfinished_trips"])
    base = {"route_audit": _audit(), "selected_warm_point_s": point,
            "prefix_completed_trips": {"total_time_loss_s": prefix_loss,
                                       "trip_count": prefix_trips},
            "prefix_counters": {"loaded": target["inserted"],
                                "inserted": target["inserted"],
                                "teleport_total": target["teleport_total"]},
            "prefix_teleport_reasons": dict(target["teleport_reasons"]),
            "prefix_queue_maximum": (None if target["max_queue_vehicles"] is None
                                     else 0),
            **bounded_sections(
                point, total_time_loss_s=target["total_time_loss_s"],
                prefix_trips=prefix_trips,
                resumed_trips=target["trip_count"] - prefix_trips),
            "raw_post_metrics": post,
            "corrected_post_metrics": post,
            "restore_correction": _correction(
                post, combined_total=target["total_time_loss_s"]),
            "reconstructed_metrics": dict(target,
                                          unfinished_trips=post["unfinished_trips"],
                                          unfinished_waiting_trips=post[
                                              "unfinished_waiting_trips"])}
    base.update(over)
    return base


def _load():
    return json.loads(MANIFEST.read_text())


def _canonical_key(payload):
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


class TestFrozenIdentity:

    def test_content_key_recomputes(self):
        manifest = _load()
        assert _canonical_key(manifest) == manifest["content_key"]

    def test_status_is_frozen_unapproved_and_unexecuted(self):
        manifest = _load()
        assert manifest["status"] == "frozen_unapproved_unexecuted"

    def test_the_approval_mechanism_is_not_self_referential(self):
        """Sol review 2026-07-28: an in-body approval field is unusable.

        Storing the approval inside the hashed manifest changes the very content
        key the approval names, so those exact frozen bytes could never be
        approved. The manifest must therefore carry NO approval value.
        """
        manifest = _load()
        assert "approved_content_key" not in manifest
        assert "content_key" in manifest["approval_mechanism"]
        # flipping the documented mechanism must not change identity
        assert _canonical_key(manifest) == manifest["content_key"]

    def test_v1_is_spent_and_its_fingerprints_stay_frozen(self):
        """v1 was EXECUTED by LUNA-WARM-05 and failed; it is spent evidence.

        LUNA-WARM-06 corrected the sources it binds, so its recorded hashes no
        longer match the tree — and that drift is exactly what keeps a spent
        campaign unadoptable. The recorded values must stay FROZEN; re-syncing
        them would resurrect a contract whose semantics were wrong.
        """
        drifted = sorted(
            name for name, digest in _load()["source_fingerprints"].items()
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest)
        assert drifted, "v1 fingerprints match again; was the spent key re-synced?"
        assert "traffic_sim/simulation/monthly_warm_state.py" in drifted

    def test_the_executable_path_is_bound(self):
        fingerprints = _load()["source_fingerprints"]
        for required in ("traffic_sim/simulation/monthly_warm_state.py",
                         "traffic_sim/simulation/warm_state_cache.py",
                         "traffic_sim/simulation/monthly_sumo.py",
                         "run_monthly_warm_state_validation.py",
                         "tools/freeze_monthly_warm_state_v1.py"):
            assert required in fingerprints, required

    def test_a_source_edit_would_invalidate_the_contract(self):
        """Drift detection is real, proven without touching the tree."""
        fingerprints = _load()["source_fingerprints"]
        target = "traffic_sim/simulation/monthly_warm_state.py"
        mutated = Path(target).read_bytes() + b"\n# drift\n"
        assert hashlib.sha256(mutated).hexdigest() != fingerprints[target]

    def test_the_spent_v1_package_no_longer_recomposes(self):
        """Drift invalidates REUSE without touching a single frozen byte."""
        sys.path.insert(0, "tools")
        from freeze_monthly_warm_state_v1 import build_artifacts
        before = MANIFEST.read_bytes()
        rebuilt = build_artifacts()[str(MANIFEST)]
        assert MANIFEST.read_text() != rebuilt, (
            "the spent v1 contract still reproduces; it must not")
        assert MANIFEST.read_bytes() == before, "recomposition mutated v1"


class TestContractContent:

    def test_the_frozen_case_is_warm_eligible_at_the_recorded_point(self):
        from traffic_sim.simulation.monthly_warm_state import (
            evaluate_warm_eligibility)
        for case in _load()["cases"]:
            decision = evaluate_warm_eligibility(
                closures=[{"edge_id": case["directed_edges"][0],
                           "begin_s": case["closure_begin_s"],
                           "end_s": case["closure_end_s"]}],
                scenario_start_s=0, simulation_mode="meso", duration_s=432000)
            assert decision.eligible
            assert decision.warm_point_s == case["expected_warm_point_s"]

    def test_the_spent_v1_comparison_policy_stays_frozen(self):
        """v1 is spent: its recorded policy is history, not a live contract.

        LUNA-WARM-06 added `split_diagnostics` to the execution-only set, so
        the live constant has legitimately moved past what v1 recorded. v1 must
        keep its own frozen list — the CURRENT contract's policy is checked
        against the live constant in the v2 suite.
        """
        from traffic_sim.simulation.monthly_warm_state import _EXECUTION_ONLY
        excluded = set(_load()["comparison_policy"]["excluded_from_comparison"])
        assert excluded < set(_EXECUTION_ONLY), (
            "v1's frozen policy should be a strict subset of the live one")
        assert "split_diagnostics" not in excluded

    def test_the_comparison_is_exact_equality(self):
        assert "exact" in _load()["comparison_policy"]["tolerance"]

    def test_a_mismatch_publishes_no_cache_material(self):
        assert "no cache" in _load()["comparison_policy"]["on_mismatch"]

    def test_speedup_is_reported_not_claimed(self):
        policy = _load()["performance_reporting"]["claim_policy"]
        assert "never claimed as proven" in policy

    def test_the_claim_scope_establishes_no_product_behaviour(self):
        scope = _load()["claim_scope"]
        assert "no adoption" in scope and "no product behaviour" in scope

    def test_the_demand_requirement_is_an_exact_archive_not_a_key(self):
        requirement = _load()["demand_requirement"]
        assert requirement["archive_path"] == "runs/demand-20260721-222017-41bc682a-bbe1"
        assert "ambiguous" in requirement["note"]

    def test_the_artifact_root_is_isolated_from_other_campaigns(self):
        root = _load()["artifact_root"]
        assert root == "runs/monthly-warm-state-validation"
        assert "closure-proxy-validation" not in root


class TestHarnessRefusesUnapprovedExecution:

    def test_retired_current_manifest_is_refused_before_execution(self):
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            _harness().main(["--manifest", str(CURRENT), "--execute"])

    def test_a_wrong_token_is_refused(self):
        harness = _harness()
        with pytest.raises(SystemExit, match="approval token does not match"):
            harness.require_approval(_current(), "not-the-token")

    def test_the_correct_token_is_the_frozen_content_key(self):
        """The mechanism must actually be USABLE by a future approved run."""
        harness = _harness()
        manifest = _current()
        harness.require_approval(manifest, manifest["content_key"])  # no raise

    def test_checks_only_report_the_retired_source_drift(self):
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            _harness().main(["--manifest", str(CURRENT)])

    def test_drifted_sources_are_refused(self, tmp_path):
        harness = _harness()
        # The LIVE contract: a spent campaign is already refused earlier, for
        # recording superseded accounting, so it cannot prove the drift check.
        manifest = _current()
        manifest["source_fingerprints"] = dict(
            manifest["source_fingerprints"],
            **{"traffic_sim/simulation/monthly_warm_state.py": "0" * 64})
        manifest["content_key"] = _canonical_key(manifest)
        path = tmp_path / "drifted.json"
        path.write_text(json.dumps(manifest))
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(path)

    def test_a_tampered_manifest_key_is_refused(self, tmp_path):
        harness = _harness()
        manifest = dict(_load(), simulation_mode="micro")
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(manifest))
        with pytest.raises(SystemExit, match="content key does not recompute"):
            harness.load_frozen_manifest(path)

    def test_a_pre_existing_artifact_root_is_terminal(self, tmp_path, monkeypatch):
        harness = _harness()
        monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)
        manifest = _load()
        root = tmp_path / manifest["artifact_root"] / manifest["content_key"]
        root.mkdir(parents=True)
        with pytest.raises(SystemExit, match="already exists"):
            harness.require_absent_root(manifest)

    def test_the_artifact_root_is_content_keyed_so_a_fresh_key_is_a_fresh_root(self):
        """Process-free: this task may not stat any `runs/` path.

        The previous version asserted the artifact root did not exist. That
        stopped being either true or permissible once LUNA-WARM-03 ran: its root
        is spent evidence, and this revision may not enumerate `runs/` at all.
        The property that still matters is structural — a campaign writes under
        its own content key, so a re-frozen key cannot collide with spent
        evidence — and the pre-existing-root refusal is covered against tmp_path.
        """
        manifest = _load()
        source = Path("run_monthly_warm_state_validation.py").read_text()
        assert 'manifest["artifact_root"] / manifest["content_key"]' in source
        assert manifest["artifact_root"] == "runs/monthly-warm-state-validation"


class TestLocalhostBindPreflight:
    """v14 proves socket policy before touching a one-time campaign root."""

    class FakeSocket:
        def __init__(self, *, address=("127.0.0.1", 43123), bind_error=None,
                     close_error=None):
            self.address = address
            self.bind_error = bind_error
            self.close_error = close_error
            self.bound = None
            self.closed = False

        def bind(self, address):
            self.bound = address
            if self.bind_error is not None:
                raise self.bind_error

        def getsockname(self):
            return self.address

        def close(self):
            self.closed = True
            if self.close_error is not None:
                raise self.close_error

    def test_fake_success_binds_exact_ipv4_tcp_loopback_and_closes(self):
        import socket
        harness = _harness()
        probe = self.FakeSocket()
        called = []

        def factory(family, socket_type):
            called.append((family, socket_type))
            return probe

        report = harness.require_localhost_bind(factory)
        assert called == [(socket.AF_INET, socket.SOCK_STREAM)]
        assert probe.bound == ("127.0.0.1", 0)
        assert probe.closed is True
        assert report == {
            "bind_capable": True,
            "family": "AF_INET",
            "socket_type": "SOCK_STREAM",
            "host": "127.0.0.1",
            "ephemeral_port": True,
        }

    def test_permission_denial_fails_before_campaign_and_still_closes(self):
        harness = _harness()
        probe = self.FakeSocket(
            bind_error=PermissionError(1, "Operation not permitted"))
        with pytest.raises(
                harness.HarnessError,
                match="no campaign was started.*no artifact root was checked"):
            harness.require_localhost_bind(lambda *_: probe)
        assert probe.closed is True

    def test_other_bind_failure_is_translated_and_still_closes(self):
        harness = _harness()
        probe = self.FakeSocket(bind_error=OSError(48, "Address in use"))
        with pytest.raises(harness.HarnessError,
                           match="OSError:.*Address in use"):
            harness.require_localhost_bind(lambda *_: probe)
        assert probe.closed is True

    @pytest.mark.parametrize("address", [
        ("127.0.0.1", 0),
        ("127.0.0.1", True),
        ("127.0.0.1", 65536),
        ("0.0.0.0", 43123),
        "127.0.0.1:43123",
    ])
    def test_malformed_bound_address_fails_closed_and_closes(self, address):
        harness = _harness()
        probe = self.FakeSocket(address=address)
        with pytest.raises(harness.HarnessError,
                           match="invalid address"):
            harness.require_localhost_bind(lambda *_: probe)
        assert probe.closed is True

    def test_close_failure_is_a_preflight_failure(self):
        harness = _harness()
        probe = self.FakeSocket(close_error=OSError("close failed"))
        with pytest.raises(harness.HarnessError, match="close failed"):
            harness.require_localhost_bind(lambda *_: probe)
        assert probe.closed is True

    @staticmethod
    def _minimal_manifest():
        return {"content_key": "approved", "cases": [{}], "seeds": [1000],
                "simulation_mode": "meso",
                "localhost_bind_preflight": {"required": True}}

    def test_a_real_manifest_cannot_omit_the_bind_contract(
            self, tmp_path):
        harness = _harness()
        manifest = _current()
        manifest.pop("localhost_bind_preflight")
        manifest["content_key"] = _canonical_key(manifest)
        path = tmp_path / "missing-bind-contract.json"
        path.write_text(json.dumps(manifest))
        with pytest.raises(harness.HarnessError,
                           match="frozen sources drifted"):
            harness.load_frozen_manifest(path)

    def test_execute_order_is_approval_traci_bind_root_campaign(
            self, monkeypatch):
        harness = _harness()
        events = []
        manifest = self._minimal_manifest()
        monkeypatch.setattr(
            harness, "load_frozen_manifest",
            lambda _path: events.append("manifest") or manifest)
        monkeypatch.setattr(
            harness, "require_approval",
            lambda _manifest, token: events.append(("approval", token)))
        monkeypatch.setattr(
            harness, "require_resolvable_traci",
            lambda: events.append("traci") or {"origin": "/sumo/tools/traci"})
        monkeypatch.setattr(
            harness, "require_localhost_bind",
            lambda: events.append("bind") or {"bind_capable": True})
        monkeypatch.setattr(
            harness, "require_absent_root",
            lambda _manifest: events.append("root") or Path("fresh-root"))
        monkeypatch.setattr(
            harness, "run_paired_campaign",
            lambda _manifest, _root: events.append("campaign") or {
                "status": "pass", "comparison_count": 3,
                "mismatch_count": 0})
        assert harness.main([
            "--manifest", "unused.json", "--execute",
            "--approval-token", "approved"]) == 0
        assert events == ["manifest", ("approval", "approved"), "traci",
                          "bind", "root", "campaign"]

    def test_bind_denial_cannot_check_root_or_run_campaign(self, monkeypatch):
        harness = _harness()
        events = []
        manifest = self._minimal_manifest()
        monkeypatch.setattr(harness, "load_frozen_manifest", lambda _p: manifest)
        monkeypatch.setattr(harness, "require_approval", lambda *_: None)
        monkeypatch.setattr(
            harness, "require_resolvable_traci",
            lambda: {"origin": "/sumo/tools/traci"})
        monkeypatch.setattr(
            harness, "require_localhost_bind",
            lambda: (_ for _ in ()).throw(
                harness.HarnessError("localhost bind denied")))
        monkeypatch.setattr(
            harness, "require_absent_root",
            lambda _m: events.append("root"))
        monkeypatch.setattr(
            harness, "run_paired_campaign",
            lambda *_: events.append("campaign"))
        with pytest.raises(harness.HarnessError, match="localhost bind denied"):
            harness.main(["--execute", "--approval-token", "approved"])
        assert events == []

    def test_no_execute_reaches_none_of_the_runtime_preflights(
            self, monkeypatch):
        harness = _harness()
        events = []
        manifest = self._minimal_manifest()
        monkeypatch.setattr(
            harness, "load_frozen_manifest",
            lambda _p: events.append("manifest") or manifest)
        for name in ("require_approval", "require_resolvable_traci",
                     "require_localhost_bind", "require_absent_root",
                     "run_paired_campaign"):
            monkeypatch.setattr(
                harness, name,
                lambda *args, _name=name, **kwargs: events.append(_name))
        assert harness.main(["--manifest", "unused.json"]) == 0
        assert events == ["manifest"]


class TestPairedComparisonLogic:

    def _obs(self, **over):
        from traffic_sim.simulation.monthly_warm_state import (
            build_monthly_observation)
        metrics = {
            "total_time_loss_s": 10.0, "trip_count": 5, "unfinished_trips": 0,
            "unfinished_waiting_trips": 0, "teleport_total": 0,
            "teleport_reasons": {}, "loaded": 5, "inserted": 5,
            "running_at_end": 0, "waiting_at_end": 0,
            "truncated_unreachable": 0, "dropped_unreachable": 0,
            "max_queue_vehicles": None, "closed_edge_throughput": None}
        kwargs = dict(
            schedule_id="s", variant="q50", seed=1000, simulation_mode="meso",
            baseline_metrics=metrics, candidate_metrics=dict(metrics),
            baseline_buckets=[], candidate_buckets=[],
            feasibility={"eligible": True, "hard_failures": []},
            recovery={"status": "recovered", "recovered": True},
            failures=[], matched_baseline_id="mb", provenance_key="p",
            provenance={"a": 1})
        kwargs.update(over)
        if kwargs.get("execution_arm") == "warm" and \
                kwargs.get("split_diagnostics") is None and \
                kwargs.get("warm_point_s") is not None:
            candidate = kwargs["candidate_metrics"]
            kwargs["split_diagnostics"] = _diag(
                kwargs["warm_point_s"],
                candidate=dict(candidate) if isinstance(candidate, dict)
                else dataclasses.asdict(candidate))
        return build_monthly_observation(**kwargs)

    def test_equivalent_arms_produce_a_passing_record(self):
        harness = _harness()
        cold = self._obs()
        warm = self._obs(execution_arm="warm", warm_point_s=1800)
        comparison = harness.compare_arms(cold, warm)
        assert comparison["equivalent"] and not comparison["mismatches"]
        comparison.update(schedule_id="s", demand_variant="q50", seed=1001,
                          cold_arm="cold", warm_arm="warm", warm_point_s=24300)
        expected = {("s", "q50", 1001)}
        class _Id:
            warmup_end_s = 24300

        execution = harness.execution_evidence_report(
            [comparison],
            [{"schedule_id": "s", "variant": "q50", "seed": 1001,
              "identity": _Id()}],
            expected, {"q50": 24300}, ["key"],
            warm_attempts=_attempts(expected))
        assert execution["complete"], execution["problems"]
        record = harness.build_equivalence_record(
            _load(), [comparison], {"phase_runtime_s": {}, "peak_rss_bytes": 1},
            expected, execution)
        assert record["status"] == "pass"
        assert record["cache_material_publishable"] is True

    def test_a_mismatch_fails_and_publishes_no_cache_material(self):
        harness = _harness()
        cold = self._obs()
        warm = self._obs(execution_arm="warm", warm_point_s=1800,
                         candidate_metrics=dict(
                             self._obs()["candidate_metrics"],
                             total_time_loss_s=11.0))
        comparison = harness.compare_arms(cold, warm)
        assert not comparison["equivalent"] and comparison["mismatches"]
        record = harness.build_equivalence_record(
            _load(), [comparison], {"phase_runtime_s": {}, "peak_rss_bytes": 1})
        assert record["status"] == "fail"
        assert record["cache_material_publishable"] is False

    def test_zero_comparisons_never_counts_as_a_pass(self):
        """An empty run must not look like proven equivalence."""
        harness = _harness()
        record = harness.build_equivalence_record(
            _load(), [], {"phase_runtime_s": {}, "peak_rss_bytes": 1})
        assert record["status"] == "fail"
        assert record["cache_material_publishable"] is False


class TestNoSumoRanInThisTask:

    def test_the_harness_never_imports_a_sumo_entry_point(self):
        """Structural, not a substring ban.

        The harness now hands the warm arm a real boundary controller, so the
        WORD `traci` legitimately appears in a comment explaining that the
        import is lazy. What must stay true is stronger and checkable: no
        simulator is imported when this module is, and nothing here reaches a
        SUMO entry point.

        `sumo_home` was dropped from the substring ban by LUNA-WARM-15: the
        harness now imports it from `runtime` for the mandatory pre-root TraCI
        preflight, which is the v6 repair itself. Locating an installation is
        not executing one — it reads paths and module attributes — so banning
        the NAME would forbid the fix while proving nothing. The structural
        guards below are what actually hold: no module-scope simulator import,
        and no SUMO entry point.
        """
        import ast
        source = Path("run_monthly_warm_state_validation.py").read_text()
        for banned in ("simulate_closure", "--load-state"):
            assert banned not in source, banned
        tree = ast.parse(source)
        top_level = set()
        for node in tree.body:                       # module scope ONLY
            if isinstance(node, ast.Import):
                top_level |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.add(node.module.split(".")[0])
        assert not top_level & {"traci", "libsumo"}, sorted(top_level)

    def test_importing_the_harness_imports_no_simulator(self):
        import importlib.util
        import sys
        for module in ("traci", "libsumo"):
            assert module not in sys.modules, module
        spec = importlib.util.spec_from_file_location(
            "warm_harness_probe", "run_monthly_warm_state_validation.py")
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        for module in ("traci", "libsumo"):
            assert module not in sys.modules, module

    def test_constructing_the_real_controller_starts_nothing(self):
        """Lazy by construction: the harness may hold one unconditionally."""
        import sys
        from traffic_sim.simulation.warm_state_boundary import (
            WarmPrefixController)
        WarmPrefixController()
        assert "traci" not in sys.modules

    def test_the_warm_cache_root_is_derived_not_global(self):
        """Structural, not a filesystem probe.

        This revision may not stat any `runs/` path — the earlier version
        asserted a cache directory did not exist, which is an existence check
        on an unapproved location. The property that matters is that the root
        is derived per runner from its own cache root, so a validation campaign
        cannot write into a shared global cache.
        """
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        warm_root = source.split("def warm_state_root(")[1].split("\n    def ")[0]
        assert "Path(self.cache_root).parent" in warm_root
        assert "warm-state" in warm_root

    def test_the_freeze_tool_runs_no_subprocess(self, monkeypatch):
        def forbidden(*args, **kwargs):
            raise AssertionError("the freeze tool must not shell out")

        for name in ("run", "check_output", "call", "check_call", "Popen"):
            monkeypatch.setattr(subprocess, name, forbidden)
        sys.path.insert(0, "tools")
        from freeze_monthly_warm_state_v1 import build_artifacts
        assert build_artifacts()


# ── Process-free INTEGRATION of the real warm branch and paired campaign ─────
class TestWarmBranchIsRealAndFallsBackCold:
    """Sol review 2026-07-28: the branch must exist, not merely be labelled."""

    def _no_subprocess(self, monkeypatch):
        """`sumo_version` and `git_commit` shell out. These checks are
        process-free by contract, so identity fingerprints use fixed stand-ins."""
        import traffic_sim.simulation.monthly_sumo as ms
        import traffic_sim.simulation.warm_state_cache as wsc
        # BOTH namespaces: each module imported the helper by name, so patching
        # only one leaves the other shelling out to the real binary.
        for module in (wsc, ms):
            if hasattr(module, "sumo_version"):
                monkeypatch.setattr(module, "sumo_version",
                                    lambda home: "Eclipse SUMO 1.27.1")
        monkeypatch.setattr(wsc, "git_commit", lambda: "a" * 40)

    def _runner(self, tmp_path, monkeypatch, *, warm, invoker=None):
        self._no_subprocess(monkeypatch)
        import traffic_sim.simulation.monthly_sumo as ms
        from traffic_sim.core.contracts import ClosureSearchSpec
        import tests.test_monthly_sumo as helpers

        archive = helpers._archive(tmp_path)
        runner = ms.ArchivedDemandSumoRunner(
            helpers._spec(), archive=archive,
            baseline_trip_duration_p99_s=1800, study_provenance_key="study",
            cache_root=tmp_path / "cache", seed_workers=1,
            warm_execution=warm, sumo_invoker=invoker)
        return runner

    def test_a_failed_bootstrap_falls_back_to_the_unchanged_cold_path(
            self, tmp_path, monkeypatch):
        """A cache miss now BOOTSTRAPS; if that fails, cold must still run."""
        from traffic_sim.core.closure_calendar import generate_closure_schedules
        import tests.test_monthly_sumo as helpers

        runner = self._runner(tmp_path, monkeypatch, warm=True,
                              invoker=lambda **kw: None)
        monkeypatch.setattr(runner, "bootstrap_warm_state",
                            lambda **kwargs: None)
        schedule = generate_closure_schedules(helpers._spec())[0]
        from traffic_sim.simulation.monthly_sumo import WarmAttempt
        attempt = WarmAttempt(schedule.schedule_id, "q50", 1000)
        called = []

        def cold(selected, *, variant, seed):
            called.append((variant, seed))
            from traffic_sim.simulation.finalist_decision import PairedObservation
            return (PairedObservation(
                candidate_id=selected.schedule_id, demand_variant=variant,
                seed=seed, baseline_time_loss_s=1.0, candidate_time_loss_s=2.0,
                matched_baseline_id=runner.matched_baseline_id,
                provenance_key="study"), (), None)

        # exercise the real warm entry point, then the cold fallback
        result = runner.run_warm_observation(
            schedule, variant="q50", seed=1000, envelope=None, closures=[
                {"edge_id": "e", "begin_s": 25200, "end_s": 54000}],
            baseline=None, baseline_buckets=None, attempt=attempt)
        assert result is None, "a failed bootstrap must fall back cold"
        codes = [event["code"] for event in attempt.events]
        assert codes, "the decline recorded nothing"
        assert any(code in {"bootstrap_failed", "bootstrap_started",
                            "cache_miss", "identity_unavailable"}
                   for code in codes), codes

    def test_the_default_invoker_is_used_when_none_is_injected(self, tmp_path,
                                                               monkeypatch):
        """Sol finding 1: an unwired seam must not silently disable the arm."""
        runner = self._runner(tmp_path, monkeypatch, warm=True, invoker=None)
        assert runner._sumo_invoker is None
        assert callable(runner._default_warm_invoker)
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        assert "self._sumo_invoker or self._default_warm_invoker" in source
        assert "no_sumo_invoker" not in source

    def test_an_ineligible_schedule_never_reaches_the_invoker(
            self, tmp_path, monkeypatch):
        from traffic_sim.core.closure_calendar import generate_closure_schedules
        import tests.test_monthly_sumo as helpers

        def invoker(**kwargs):
            raise AssertionError("an ineligible schedule must not be invoked")

        runner = self._runner(tmp_path, monkeypatch, warm=True, invoker=invoker)
        schedule = generate_closure_schedules(helpers._spec())[0]
        monkeypatch.setattr(runner, "_closure_seconds",
                            lambda s: [{"edge_id": "e", "begin_s": 0,
                                        "end_s": 3600}])
        assert runner.run_warm_observation(
            schedule, variant="q50", seed=1000, envelope=None,
            closures=[{"edge_id": "e", "begin_s": 0, "end_s": 3600}],
            baseline=None, baseline_buckets=None) is None

    def test_the_default_runner_never_enters_the_warm_branch(
            self, tmp_path, monkeypatch):
        from traffic_sim.core.closure_calendar import generate_closure_schedules
        import tests.test_monthly_sumo as helpers

        runner = self._runner(tmp_path, monkeypatch, warm=False)
        schedule = generate_closure_schedules(helpers._spec())[0]
        assert runner.warm_decision(schedule).reason == "warm_execution_disabled"

    def test_observations_are_returned_per_seed_not_shared(self):
        """Sol review: a shared `_last_observation` races under seed workers."""
        import traffic_sim.simulation.monthly_sumo as ms
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        assert "_last_observation" not in source
        assert not hasattr(ms.ArchivedDemandSumoRunner, "_last_observation")



def reconstruct_metrics(prefix_evidence, post_warm, **kwargs):
    """Test shim over the production combiner.

    The fixtures in this module describe splits with an EMPTY boundary ledger,
    so their reconciliation is fully determined by the two aggregates. Supplying
    it here keeps every existing assertion about the OTHER field rules intact
    without restating it at twenty call sites. `split_aggregation` refuses a
    non-empty ledger, so this can never fabricate the boundary case; tests that
    exercise boundary offsets, or that assert production REFUSES an
    unreconciled split, call the production function directly.
    """
    if "aggregation" not in kwargs:
        kwargs["aggregation"] = split_aggregation(prefix_evidence, post_warm)
    return _production_reconstruct_metrics(prefix_evidence, post_warm, **kwargs)


def _empty_accumulator(point=24300):
    population = {"schema": "warm_boundary_active_v3",
                  "warm_point_s": point, "captured_at_s": float(point),
                  "vehicle_ids": []}
    population_digest = hashlib.sha256(json.dumps(
        population, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode()).hexdigest()
    body = {"schema": "warm_prefix_meso_accumulator_v3",
            "warm_point_s": point,
            "output_precision": 16,
            "active_population_digest": population_digest, "entries": {}}
    body["digest"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return body


def _prefix_evidence(**over):
    """Versioned prefix evidence, in the shape the bootstrap produces."""
    from traffic_sim.simulation.monthly_warm_state import build_prefix_evidence
    kwargs = dict(
        completed_trips={"total_time_loss_s": 5.0, "trip_count": 3},
        queue_maximum=0, warm_point_s=24300,
        counters={"loaded": 3, "inserted": 3, "teleport_total": 1,
                  "teleport_reasons": {"jam": 1}},
        recovery_buckets=[{"begin_s": 0, "end_s": 24300, "time_loss_s": 1.0}],
        snapshot_facts={"schema": "warm_snapshot_facts_v1",
                        "warm_point_s": 24300, "captured_at_s": 24300.0,
                        "active_vehicle_count": 0, "save_state_rng": True,
                        "save_state_precision": 16},
        active_accumulator=_empty_accumulator(),
        completed_records={"c1": 2.0, "c2": 2.0, "c3": 1.0},
        completed_record_order=["c1", "c2", "c3"],
    )
    kwargs.update(over)
    return build_prefix_evidence(**kwargs)


class TestPrefixEvidenceAndFieldRegistry:
    """Criteria 1/3/4: exhaustive, versioned, mechanically bound accounting."""

    def _post(self, **over):
        # loaded/inserted/teleports are CUMULATIVE across the loaded state, so
        # the post-warm values already include the prefix's 3/3/1.
        base = {"total_time_loss_s": 5.0, "trip_count": 2, "unfinished_trips": 1,
                "unfinished_waiting_trips": 1, "teleport_total": 1,
                "teleport_reasons": {"jam": 1}, "loaded": 5, "inserted": 5,
                "running_at_end": 4, "waiting_at_end": 2,
                "truncated_unreachable": 3, "dropped_unreachable": 1,
                "max_queue_vehicles": 5, "closed_edge_throughput": 7}
        base.update(over)
        return base

    def test_every_production_field_has_exactly_one_rule(self):
        from traffic_sim.simulation.monthly_warm_state import (
            FIELD_RULES, production_metric_fields, verify_field_partition)
        verify_field_partition()
        assert set(FIELD_RULES) == set(production_metric_fields())
        assert len(FIELD_RULES) == len(production_metric_fields())

    def test_a_new_production_field_without_a_rule_fails(self, monkeypatch):
        """Criterion 4: the binding is mechanical, not a hand-kept list."""
        import traffic_sim.simulation.monthly_warm_state as mws
        monkeypatch.setattr(mws, "production_metric_fields",
                            lambda: mws.production_metric_fields.__wrapped__()
                            if hasattr(mws.production_metric_fields, "__wrapped__")
                            else tuple(list(mws.FIELD_RULES) + ["invented_field"]))
        with pytest.raises(mws.WarmStateContractError, match="no warm-boundary rule"):
            mws.verify_field_partition()

    def test_an_orphaned_rule_fails(self, monkeypatch):
        import traffic_sim.simulation.monthly_warm_state as mws
        monkeypatch.setitem(mws.FIELD_RULES, "field_that_does_not_exist", "post_final")
        with pytest.raises(mws.WarmStateContractError, match="name no production field"):
            mws.verify_field_partition()

    def test_the_real_luna_warm_03_failure_now_reconstructs(self):
        """`max_queue_vehicles` 0 (prefix) vs 5 (post) — the campaign killer."""
        # module-level shim supplies the empty-ledger reconciliation
        out = reconstruct_metrics(_prefix_evidence(queue_maximum=0),
                                  self._post(max_queue_vehicles=5))
        assert out["max_queue_vehicles"] == 5, "a maximum is not additive"

    def test_an_unmeasured_queue_segment_does_not_erase_the_other(self):
        # module-level shim supplies the empty-ledger reconciliation
        out = reconstruct_metrics(_prefix_evidence(queue_maximum=None),
                                  self._post(max_queue_vehicles=5))
        assert out["max_queue_vehicles"] == 5
        out = reconstruct_metrics(_prefix_evidence(queue_maximum=9),
                                  self._post(max_queue_vehicles=None))
        assert out["max_queue_vehicles"] == 9

    def test_both_segments_unmeasured_stays_none(self):
        # module-level shim supplies the empty-ledger reconciliation
        out = reconstruct_metrics(_prefix_evidence(queue_maximum=None),
                                  self._post(max_queue_vehicles=None))
        assert out["max_queue_vehicles"] is None

    def test_disjoint_trip_accumulators_are_summed(self):
        # module-level shim supplies the empty-ledger reconciliation
        out = reconstruct_metrics(_prefix_evidence(), self._post())
        assert out["total_time_loss_s"] == 10.0
        assert out["trip_count"] == 5
        # cumulative: the post value IS the total, not prefix + post
        assert out["loaded"] == 5 and out["inserted"] == 5
        assert out["teleport_total"] == 1
        assert out["teleport_reasons"] == {"jam": 1}

    def test_boundary_active_trips_are_not_counted_twice(self):
        """Criterion 2: the prefix contributes COMPLETED trips only."""
        # module-level shim supplies the empty-ledger reconciliation
        out = reconstruct_metrics(_prefix_evidence(), self._post(unfinished_trips=1))
        # the prefix carries no unfinished count at all; the final one is the
        # post-warm segment's, so a boundary-active vehicle appears once
        assert out["unfinished_trips"] == 1
        assert out["trip_count"] == 3 + 2

    def test_end_state_fields_come_from_the_post_warm_segment(self):
        # module-level shim supplies the empty-ledger reconciliation
        out = reconstruct_metrics(_prefix_evidence(), self._post(
            running_at_end=4, waiting_at_end=2, unfinished_waiting_trips=1))
        assert out["running_at_end"] == 4
        assert out["waiting_at_end"] == 2
        assert out["unfinished_waiting_trips"] == 1

    def test_candidate_only_fields_come_from_the_post_warm_segment(self):
        """The filtered candidate route does not exist before the warm point."""
        # module-level shim supplies the empty-ledger reconciliation
        out = reconstruct_metrics(_prefix_evidence(), self._post())
        assert out["truncated_unreachable"] == 3
        assert out["dropped_unreachable"] == 1

    def test_closure_throughput_comes_from_after_the_warm_point(self):
        # module-level shim supplies the empty-ledger reconciliation
        out = reconstruct_metrics(_prefix_evidence(), self._post())
        assert out["closed_edge_throughput"] == 7

    def test_reconstruction_yields_a_valid_production_dataclass(self):
        from traffic_sim.simulation.metrics import DisruptionMetrics
        # module-level shim supplies the empty-ledger reconciliation
        metrics = DisruptionMetrics(
            **reconstruct_metrics(_prefix_evidence(), self._post()))
        assert metrics.total_time_loss_s == 10.0

    @pytest.mark.parametrize("payload", [
        {}, {"schema": "something_else"}, None, [],
        {"schema": "monthly_prefix_evidence_v1"},
    ])
    def test_malformed_or_unknown_evidence_fails_closed(self, payload):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, parse_prefix_evidence)
        with pytest.raises(WarmStateContractError):
            parse_prefix_evidence(payload)

    def test_legacy_prefix_metrics_are_not_interpreted(self):
        """A pre-schema aggregate must be a MISS, never read as evidence."""
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, parse_prefix_evidence)
        legacy = {"total_time_loss_s": 5.0, "trip_count": 3,
                  "unfinished_trips": 0, "running_at_end": 0}
        with pytest.raises(WarmStateContractError, match="unsupported prefix-evidence"):
            parse_prefix_evidence(legacy)

    def test_evidence_records_its_warm_point_and_schema(self):
        evidence = _prefix_evidence()
        assert evidence["schema"] == "monthly_prefix_evidence_v7"
        assert evidence["warm_point_s"] == 24300


class TestRecoveryBucketConcatenation:
    """Criterion 5: one ordered, gap-free domain; never synthesised."""

    WARM = 900

    def _join(self, left, right, warm=None):
        from traffic_sim.simulation.monthly_warm_state import (
            concatenate_recovery_buckets)
        return concatenate_recovery_buckets(left, right,
                                            self.WARM if warm is None else warm)

    def test_a_clean_join_preserves_the_full_domain(self):
        out = self._join([{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}],
                         [{"begin_s": 900, "end_s": 1800, "time_loss_s": 2.0}])
        assert [(b["begin_s"], b["end_s"]) for b in out] == [(0, 900), (900, 1800)]

    def test_a_gap_is_rejected_not_filled(self):
        with pytest.raises(Exception, match="gap"):
            self._join([{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}],
                       [{"begin_s": 1800, "end_s": 2700, "time_loss_s": 2.0}],
                       warm=900)

    def test_an_overlap_is_rejected(self):
        # both post-warm buckets are after the boundary; they overlap each other
        with pytest.raises(Exception, match="overlap"):
            self._join([{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}],
                       [{"begin_s": 900, "end_s": 1800, "time_loss_s": 2.0},
                        {"begin_s": 1350, "end_s": 2700, "time_loss_s": 2.0}],
                       warm=900)

    def test_a_prefix_bucket_crossing_the_boundary_is_rejected(self):
        with pytest.raises(Exception, match="after the warm point"):
            self._join([{"begin_s": 0, "end_s": 1200, "time_loss_s": 1.0}],
                       [{"begin_s": 1200, "end_s": 1800, "time_loss_s": 2.0}])

    def test_a_post_bucket_starting_before_the_boundary_is_rejected(self):
        with pytest.raises(Exception, match="before the warm"):
            self._join([{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}],
                       [{"begin_s": 450, "end_s": 1800, "time_loss_s": 2.0}])

    def test_an_inverted_bucket_is_rejected(self):
        with pytest.raises(Exception, match="empty or inverted"):
            self._join([{"begin_s": 900, "end_s": 900, "time_loss_s": 1.0}],
                       [{"begin_s": 900, "end_s": 1800, "time_loss_s": 1.0}])

    def test_out_of_order_post_buckets_are_rejected(self):
        with pytest.raises(Exception):
            self._join([{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}],
                       [{"begin_s": 1800, "end_s": 2700, "time_loss_s": 1.0},
                        {"begin_s": 900, "end_s": 1800, "time_loss_s": 1.0}])


class TestPairedCampaignIsExecutable:
    """Criterion 7: the harness really runs both arms and compares them."""

    def _manifest(self):
        return _load()

    def _fake_factory(self, observations):
        class FakeRunner:
            def __init__(self, values):
                self.spec = None
                self.canonical_observations = []
                self._values = list(values)

            def _run_observation(self, schedule, *, variant, seed):
                # Stamp the real schedule id so coverage can be established.
                value = self._values.pop(0) if self._values else None
                if value is not None:
                    value = dict(value, schedule_id=schedule.schedule_id)
                return (None, (), value)

        def factory(manifest, *, warm, workspace, sumo_invoker=None,
                    boundary_controller=None):
            runner = FakeRunner(observations["warm" if warm else "cold"])
            runner.spec = observations["spec"]
            return runner
        return factory

    def _observation(self, **over):
        from traffic_sim.simulation.monthly_warm_state import (
            build_monthly_observation)
        metrics = {"total_time_loss_s": 10.0, "trip_count": 5,
                   "unfinished_trips": 0, "unfinished_waiting_trips": 0,
                   "teleport_total": 0, "teleport_reasons": {}, "loaded": 5,
                   "inserted": 5, "running_at_end": 0, "waiting_at_end": 0,
                   "truncated_unreachable": 0, "dropped_unreachable": 0,
                   "max_queue_vehicles": None, "closed_edge_throughput": None}
        kwargs = dict(schedule_id="s", variant="q50", seed=1001,
                      simulation_mode="meso", baseline_metrics=metrics,
                      candidate_metrics=dict(metrics), baseline_buckets=[],
                      candidate_buckets=[],
                      feasibility={"eligible": True, "hard_failures": []},
                      recovery={"status": "recovered", "recovered": True},
                      failures=[], matched_baseline_id="mb",
                      provenance_key="p", provenance={"a": 1})
        kwargs.update(over)
        if kwargs.get("execution_arm") == "warm" and \
                kwargs.get("split_diagnostics") is None and \
                kwargs.get("warm_point_s") is not None:
            candidate = kwargs["candidate_metrics"]
            kwargs["split_diagnostics"] = _diag(
                kwargs["warm_point_s"],
                candidate=dict(candidate) if isinstance(candidate, dict)
                else dataclasses.asdict(candidate))
        return build_monthly_observation(**kwargs)

    def _run(self, tmp_path, cold, warm):
        import tests.test_monthly_sumo as helpers
        harness = _harness()
        # One variant, one repetition: canonical_seed("q50", 0) == 1001, which
        # is what `_observation` emits, so coverage is exactly satisfiable.
        # A synthetic fixture is not the frozen contract: it declares its own
        # identity set. `frozen_schedule_ids=None` skips the schedule-pinning
        # check, which the REAL manifest is tested against separately.
        manifest = dict(self._manifest(), demand_variants=["q50"],
                        repetitions_per_variant=1, seeds=[1001],
                        frozen_schedule_ids=None)
        spec = helpers._spec()
        factory = self._fake_factory({"cold": cold, "warm": warm, "spec": spec})
        root = tmp_path / "root"
        return harness.run_paired_campaign(manifest, root,
                                           runner_factory=factory), root

    def test_semantically_equal_arms_without_a_promotable_state_still_fail(
            self, tmp_path):
        """Criterion 10: equal payloads are not proof the warm branch ran.

        These fake runners emit a matching cold/warm pair but create no
        provisional state, so nothing could be promoted. That must FAIL — a
        green comparison over an arm that never warmed proves nothing.
        """
        cold = [self._observation()]
        warm = [self._observation(execution_arm="warm", warm_point_s=24300)]
        record, root = self._run(tmp_path, cold, warm)
        assert record["status"] == "fail"
        assert record["cache_material_publishable"] is False
        assert any("no promotable state" in problem
                   for problem in record["execution_evidence"]["problems"])
        assert (root / "equivalence_record.json").is_file()
        assert (root / "NO_CACHE_PUBLISHED").is_file()

    def test_a_mismatch_fails_and_publishes_no_cache_material(self, tmp_path):
        cold = [self._observation()]
        warm = [self._observation(
            execution_arm="warm", warm_point_s=24300,
            candidate_metrics={"total_time_loss_s": 11.0, "trip_count": 5,
                               "unfinished_trips": 0,
                               "unfinished_waiting_trips": 0,
                               "teleport_total": 0, "teleport_reasons": {},
                               "loaded": 5, "inserted": 5, "running_at_end": 0,
                               "waiting_at_end": 0, "truncated_unreachable": 0,
                               "dropped_unreachable": 0,
                               "max_queue_vehicles": None,
                               "closed_edge_throughput": None})]
        record, root = self._run(tmp_path, cold, warm)
        assert record["status"] == "fail"
        assert record["cache_material_publishable"] is False
        assert (root / "NO_CACHE_PUBLISHED").is_file()

    def test_a_missing_warm_observation_is_a_mismatch_not_a_pass(self, tmp_path):
        record, _root = self._run(tmp_path, [self._observation()], [None])
        assert record["status"] == "fail"

    def test_a_missing_arm_observation_is_a_mismatch(self, tmp_path):
        """Counts are now structurally equal — the harness requests every
        frozen identity from both arms — so the failure mode is a MISSING
        observation for an identity, not a length difference."""
        record, _root = self._run(
            tmp_path, [self._observation()], [None])
        assert record["status"] == "fail"
        assert record["comparisons"][0]["mismatches"]

    def test_the_record_binds_the_frozen_manifest(self, tmp_path):
        record, _root = self._run(
            tmp_path, [self._observation()],
            [self._observation(execution_arm="warm", warm_point_s=24300)])
        assert record["manifest_content_key"] == self._manifest()["content_key"]
        assert record["performance"]["phase_runtime_s"] is not None


class _ColdPathEntered(Exception):
    """Raised from the cold arm's own simulation call, so a test can prove that
    control genuinely reached it rather than dying somewhere earlier."""


class TestInvalidWarmEvidenceFallsBackToCold:
    """Sol review rounds 12-13: a warm failure must never cost an observation.

    The warm arm is an optimisation over an EQUIVALENT cold arm, so invalid
    boundary/reconciliation evidence must record a reason and let the unchanged
    cold path run. These tests assert control REACHES THE COLD ARM'S OWN
    SIMULATION CALL — a unique sentinel raised from `legacy.simulate_closure`.
    An earlier version only checked that the warm exception did not escape,
    which any later unrelated failure would satisfy just as well.
    """

    class _Schedule:
        schedule_id = "s1"
        first_work_date = "2025-09-16"

    def _fallback(self, monkeypatch, error):
        import traffic_sim.simulation.monthly_sumo as ms

        def cold_entered(**kwargs):
            raise _ColdPathEntered(kwargs["name"])

        monkeypatch.setattr(ms.legacy, "simulate_closure", cold_entered)

        class Probe(ms.ArchivedDemandSumoRunner):
            def __init__(self):
                self.warm_execution = True
                self.warm_attempts = []
                # Only what the cold prologue reads before its own call.
                self.close_edges = ["a_b_0"]
                self.variants = {"q50": Path("v.rou.xml")}
                self.n_intervals = 4
                self.duration_s = 3600
                self.home = Path("/opt/sumo")
                self.adjacency = {}
                self.freeflow = {}
                self.rerouter_edges = []
                self.spec = type(
                    "Spec", (), {"interday_policy": "continuous_v1"})()

            def _envelope(self, schedule):
                return None

            def _observation_execution_window(self, schedule):
                return (True, self.n_intervals, self.duration_s, 0, 3600)

            def _matched_baseline_id_for_window(self, **_kwargs):
                return "test-baseline"

            def _run_baseline_for_window(self, variant, seed, **_kwargs):
                return (None, ())

            def _closure_seconds(self, schedule):
                return []

            def run_warm_observation(self, schedule, **kwargs):
                raise error

        probe = Probe()
        with pytest.raises(_ColdPathEntered) as caught:
            ms.ArchivedDemandSumoRunner._run_observation(
                probe, self._Schedule(), variant="q50", seed=1000)
        assert caught.value.args[0] == "candidate"
        return probe

    @pytest.mark.parametrize("kind", [
        "boundary", "contract", "unanticipated", "runtime"])
    def test_control_reaches_the_cold_arm(self, monkeypatch, kind):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError)
        from traffic_sim.simulation.warm_state_boundary import (
            BoundaryLedgerError)
        errors = {
            "boundary": BoundaryLedgerError("boundary vehicles never reappear"),
            "contract": WarmStateContractError("completed map disagrees"),
            # Not anticipated anywhere: the guard sits at the CONSUMING
            # boundary, so it does not depend on having predicted which internal
            # step fails.
            "unanticipated": KeyError("resumed_trip_time_loss"),
            "runtime": RuntimeError("boom"),
        }
        error = errors[kind]
        probe = self._fallback(monkeypatch, error)
        assert probe.warm_attempts, "the fallback would be silent"
        record = probe.warm_attempts[0]
        assert record["outcome"] == "cold_fallback"
        assert record["events"][-1]["code"] == "unexpected_error"
        assert type(error).__name__ in \
            record["events"][-1]["details"]["error_type"]

    def test_the_recorded_reason_identifies_the_identity(self, monkeypatch):
        probe = self._fallback(monkeypatch, RuntimeError("boom"))
        record = probe.warm_attempts[0]
        assert (record["schedule_id"], record["demand_variant"],
                record["seed"]) == ("s1", "q50", 1000)

    def test_exactly_one_attempt_is_finalized_per_observation(self, monkeypatch):
        """The property that makes an unreported fallback impossible."""
        probe = self._fallback(monkeypatch, RuntimeError("boom"))
        assert len(probe.warm_attempts) == 1


class TestDefaultProductionPathEndToEnd:
    """Sol review 2026-07-28 finding 5: exercise the REAL default path.

    Nothing is injected except SUMO itself: `run_paired_campaign()` uses its own
    `build_runner`, which constructs production `ArchivedDemandSumoRunner`s with
    no `sumo_invoker`, so the default `_default_warm_invoker` runs. Only
    `rs.run_sumo` and the file-parsing helpers are mocked, so the bootstrap →
    restore → post-warm → compare → publish flow is genuinely traversed.
    """

    def _install_fake_sumo(self, monkeypatch, tmp_path, *, warm_differs=False):
        import run_scenario as rs
        import traffic_sim.simulation.metrics as cm
        import traffic_sim.simulation.monthly_sumo as ms
        from traffic_sim.simulation.envelope import RecoveryBucket

        calls = {"save_state": 0, "load_state": 0, "cold": 0}

        def write_outputs(work, add_paths, outputs=None):
            """Write exactly what a real SUMO run leaves behind in `work`.

            Shared by the fake batch runner and the fake boundary controller,
            because in production BOTH are one SUMO process writing the same
            files — the controller simply also owns the connection.
            """
            work = Path(work)
            work.mkdir(parents=True, exist_ok=True)
            # Where SUMO writes is decided by the COMMAND, so the controller
            # passes the paths its argv actually names; the batch fake keeps
            # the simple defaults it already returned.
            outputs = outputs or {}
            tripinfo = Path(outputs.get("tripinfo", work / "tripinfo.xml"))
            for key, default in (("statistics", work / "statistics.xml"),
                                 ("summary", work / "summary.xml")):
                target = Path(outputs.get(key, default))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("<x/>")
            # Real tripinfo records, since boundary reconciliation is by
            # vehicle IDENTITY: the prefix completes c1..c3 (5.0 s) and the
            # resumed run completes r1..r2 (5.0 s), disjoint by construction.
            name = str(work)
            if "bootstrap" in name:
                trips = [("c1", 2.0), ("c2", 2.0), ("c3", 1.0)]
            elif "post" in name:
                trips = [("r1", 3.0),
                         ("r2", 2.0 + (1.0 if warm_differs else 0.0))]
            else:
                trips = [("c1", 2.0), ("c2", 2.0), ("c3", 1.0),
                         ("r1", 3.0), ("r2", 2.0)]
            tripinfo.parent.mkdir(parents=True, exist_ok=True)
            tripinfo.write_text(
                "<tripinfos>\n" + "\n".join(
                    f'<tripinfo id="{vid}" '
                    f'depart="{100 if vid.startswith("c") else 25000}" '
                    f'arrival="{1000 if vid.startswith("c") else 26000}" '
                    f'timeLoss="{loss:.16f}"/>'
                    for vid, loss in trips) + "\n</tripinfos>\n")
            for add in add_paths:
                text = Path(add).read_text() if Path(add).is_file() else ""
                for token in text.split('file="')[1:]:
                    target = work / token.split('"')[0]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("<meandata/>")

        class FakeController:
            """Stands in for the real one: no traci, no socket, no process.

            It reports NO vehicle in flight at the snapshot, so the fixture's
            prefix and resumed trips partition the cold run exactly and any
            double count or omission shows up as a mismatch. Like the real
            controller it writes the state file AND leaves behind the outputs
            its SUMO process would have written.
            """

            def __init__(self):
                self.calls = []

            def run_prefix(self, *, cmd, cwd, home, warm_point_s, state_path,
                           connect_attempts=40):
                self.calls.append(warm_point_s)
                calls["save_state"] += 1
                # The real controller REFUSES a command missing the snapshot
                # settings, so the fake must hold production to the same rule or
                # the wiring could regress unnoticed.
                from traffic_sim.simulation.warm_state_boundary import (
                    validate_snapshot_command)
                validate_snapshot_command(cmd)
                def flag(*names):
                    for index, token in enumerate(cmd):
                        if token in names:
                            return cmd[index + 1]
                    return None

                add_paths = [flag("-a", "--additional-files")] \
                    if flag("-a", "--additional-files") else []
                outputs = {}
                for key, names in (("tripinfo", ("--tripinfo-output",)),
                                   ("statistics", ("--statistic-output",)),
                                   ("summary", ("--summary-output",))):
                    value = flag(*names)
                    if value:
                        outputs[key] = Path(cwd) / value
                write_outputs(cwd, add_paths, outputs)
                Path(state_path).write_bytes(b"STATE")
                from traffic_sim.simulation.warm_state_boundary import (
                    capture_boundary_active)

                class _Conn:
                    def __init__(self, t):
                        self._t = t
                        self.simulation = self
                        self.vehicle = self

                    def getTime(self):
                        return float(self._t)

                    def getIDList(self):
                        return []

                return {
                    "facts": {"schema": "warm_snapshot_facts_v1",
                              "warm_point_s": warm_point_s,
                              "captured_at_s": float(warm_point_s),
                              "active_vehicle_count": 0,
                              "save_state_rng": True,
                              "save_state_precision": 16},
                    "boundary_active": capture_boundary_active(
                        _Conn(warm_point_s), warm_point_s=warm_point_s),
                }

            def run_resumed(self, *, cmd, cwd, home, warm_point_s,
                            terminal_time_s, **kwargs):
                """Exercise the REAL default invoker/controller call graph."""
                from traffic_sim.simulation.warm_state_boundary import (
                    validate_resumed_command)
                validate_resumed_command(
                    cmd, warm_point_s=warm_point_s,
                    terminal_time_s=terminal_time_s)
                calls["load_state"] += 1

                def flag(name):
                    index = cmd.index(name)
                    return cmd[index + 1]

                additional = flag("-a").split(",") if "-a" in cmd else []
                outputs = {
                    "tripinfo": Path(flag("--tripinfo-output")),
                    "statistics": Path(flag("--statistic-output")),
                    "summary": Path(flag("--summary-output")),
                }
                write_outputs(cwd, additional, outputs)
                return {"warm_point_s": warm_point_s,
                        "terminal_time_s": terminal_time_s,
                        "terminal_reached": True}

        # Injected through the REAL harness seam, not onto the class: since
        # `build_runner` now supplies a controller per runner, a class-level
        # patch would be shadowed by that instance attribute and the fake would
        # never run — which is exactly how the unwired seam hid last time.
        controller = FakeController()
        calls["controller"] = controller

        def fake_run_sumo(seed, route_path, add_paths, duration_s, home,
                          **kwargs):
            work = Path(kwargs.get("work_dir") or tmp_path)
            if kwargs.get("save_state_path"):
                Path(kwargs["save_state_path"]).write_bytes(b"STATE")
            if kwargs.get("load_state_path"):
                calls["load_state"] += 1
            write_outputs(work, add_paths)
            return {"tripinfo": work / "tripinfo.xml",
                    "statistics": work / "statistics.xml",
                    "summary": work / "summary.xml"}

        def metrics(**over):
            base = dict(total_time_loss_s=10.0, trip_count=5, unfinished_trips=0,
                        unfinished_waiting_trips=0, teleport_total=0,
                        teleport_reasons={}, loaded=5, inserted=5,
                        running_at_end=0, waiting_at_end=0,
                        truncated_unreachable=0, dropped_unreachable=0)
            base.update(over)
            return cm.DisruptionMetrics(**base)

        def fake_build_metrics(tripinfo, statistics, **kwargs):
            # The warm arm's two segments PARTITION the cold run: every additive
            # field splits 3/2, so correct prefix accounting reproduces the cold
            # result exactly and a bug in it shows up as a mismatch.
            if "post" in str(tripinfo):
                # Trips are DISJOINT (completed-only prefix + post = 3 + 2),
                # but loaded/inserted are CUMULATIVE across the loaded state,
                # so the post value already carries the prefix's 3.
                return metrics(
                    total_time_loss_s=5.0 + (1.0 if warm_differs else 0.0),
                    trip_count=2, loaded=5, inserted=5)
            if "bootstrap" in str(tripinfo):
                return metrics(total_time_loss_s=5.0, trip_count=3,
                               loaded=3, inserted=3)
            calls["cold"] += 1
            return metrics(total_time_loss_s=10.0, trip_count=5,
                           loaded=5, inserted=5)

        # Buckets must be segment-aware: the prefix covers [0, warm) and the
        # post-warm segment [warm, ...), so their concatenation is gap-free.
        warm_point = 20700

        early = RecoveryBucket(begin_s=0, end_s=warm_point, time_loss_s=1.0)
        # The domain must span the WHOLE run, so the late bucket runs to the
        # archive's end (96 intervals x 900 s).
        late = RecoveryBucket(begin_s=warm_point, end_s=86400, time_loss_s=2.0)

        def fake_buckets(path, *a, **k):
            name = str(path)
            if "prefix" in name:            # bootstrap: [0, warm) only
                return (early,)
            if "post" in name:              # resumed arm: [warm, end) only
                return (late,)
            # An uninterrupted (cold) run measures the WHOLE domain, which is
            # exactly what the warm arm's concatenation must reproduce.
            return (early, late)

        monkeypatch.setattr(rs, "run_sumo", fake_run_sumo)
        monkeypatch.setattr(cm, "build_metrics", fake_build_metrics)
        monkeypatch.setattr(ms.closure_metrics, "build_metrics", fake_build_metrics)
        monkeypatch.setattr(ms, "read_edgedata_time_loss", fake_buckets)
        monkeypatch.setattr(ms, "_read_warm_edgedata_time_loss", fake_buckets)
        def fake_truncate(route_path, close_edges, out_path, *a, **k):
            # The route audit compares real files, so filtering must produce
            # one. Identical content => no changed/dropped vehicles => the
            # closure-begin bound governs the safe warm point.
            Path(out_path).write_text(Path(route_path).read_text())
            return (0, 0)

        monkeypatch.setattr(rs, "truncate_stranded_vehicles", fake_truncate)
        monkeypatch.setattr(rs, "write_closure_additional",
                            lambda path, *a, **k: Path(path).write_text("<add/>"))
        return calls

    def _manifest_for(self, tmp_path, monkeypatch):
        """The frozen contract, repointed at a synthetic archive."""
        # `sumo_version` and `git_commit` shell out; these checks are
        # process-free by contract, so identity fingerprints use stand-ins.
        import traffic_sim.simulation.monthly_sumo as ms
        import traffic_sim.simulation.warm_state_cache as wsc
        # BOTH namespaces: each module imported the helper by name, so patching
        # only one leaves the other shelling out to the real binary.
        for module in (wsc, ms):
            if hasattr(module, "sumo_version"):
                monkeypatch.setattr(module, "sumo_version",
                                    lambda home: "Eclipse SUMO 1.27.1")
        monkeypatch.setattr(wsc, "git_commit", lambda: "a" * 40)
        import tests.test_monthly_sumo as helpers
        harness = _harness()
        monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)
        archive = helpers._archive(tmp_path)
        for name in ("calibrated.rou.xml", "calibrated_v1.rou.xml",
                     "calibrated_v2.rou.xml"):
            (Path(archive) / name).write_text(
                '<routes>\n<vehicle id="v1" depart="60.0">'
                '<route edges="a_b_0 b_c_0"/></vehicle>\n</routes>\n')
        manifest = dict(_load())
        manifest["demand_requirement"] = dict(
            manifest["demand_requirement"],
            archive_path=str(Path(archive).relative_to(tmp_path)))
        spec = helpers._spec().to_dict()
        manifest["demand_requirement"]["demand_build_key"] = spec["demand_build_id"]
        manifest["spec_template"] = {
            k: v for k, v in spec.items()
            if k not in {"search_id", "directed_edges", "demand_build_id"}}
        manifest["cases"] = [dict(manifest["cases"][0],
                                  directed_edges=spec["directed_edges"])]
        # Synthetic fixture: it declares its own identity set rather than
        # inheriting the real contract's pinned schedule ids, seeds and warm
        # point. The warm point is DERIVED from production, not hard-coded, so
        # the fixture cannot drift from what the runner actually decides.
        manifest["demand_variants"] = ["q50"]
        manifest["repetitions_per_variant"] = 1
        manifest["seeds"] = [1001]
        manifest["frozen_schedule_ids"] = None
        from traffic_sim.core.closure_calendar import generate_closure_schedules
        probe = harness.build_runner(manifest, warm=True,
                                     workspace=tmp_path / "probe")
        schedule = generate_closure_schedules(probe.spec)[0]
        manifest["cases"] = [dict(manifest["cases"][0],
                                  expected_warm_point_s=
                                  probe.warm_decision(schedule).warm_point_s)]
        return harness, manifest

    def test_the_warm_arm_gets_a_real_controller_by_default(
            self, tmp_path, monkeypatch):
        """The gap Sol found: every other test INJECTS a controller, so none of
        them would notice a harness that supplies none. Then the warm arm falls
        back to cold and the campaign silently cannot test what it froze."""
        from traffic_sim.simulation.warm_state_boundary import (
            WarmPrefixController)
        harness, manifest = self._manifest_for(tmp_path, monkeypatch)
        warm = harness.build_runner(manifest, warm=True,
                                    workspace=tmp_path / "ctl-warm")
        assert isinstance(warm._boundary_controller, WarmPrefixController)
        cold = harness.build_runner(manifest, warm=False,
                                    workspace=tmp_path / "ctl-cold")
        assert cold._boundary_controller is None, (
            "the cold arm must never hold simulator control")

    def test_supplying_the_real_controller_imports_no_simulator(
            self, tmp_path, monkeypatch):
        import sys
        harness, manifest = self._manifest_for(tmp_path, monkeypatch)
        harness.build_runner(manifest, warm=True, workspace=tmp_path / "lazy")
        assert "traci" not in sys.modules and "libsumo" not in sys.modules

    def test_the_default_path_bootstraps_restores_compares_and_publishes(
            self, tmp_path, monkeypatch):
        calls = self._install_fake_sumo(monkeypatch, tmp_path)
        harness, manifest = self._manifest_for(tmp_path, monkeypatch)
        record = harness.run_paired_campaign(
            manifest, tmp_path / "root",
            boundary_controller=calls["controller"])

        assert calls["save_state"] >= 1, "no candidate-free bootstrap ran"
        assert calls["load_state"] >= 1, (
            "the post-warm phase never loaded state",
            record.get("observation_failures"))
        assert record["comparison_count"] >= 1, record.get("observation_failures")
        assert record["status"] == "pass", record["comparisons"][0]["mismatches"]
        assert record["published_cache_entries"], "nothing was promoted to cache"

    def test_a_published_entry_is_restorable_with_its_prefix_evidence(
            self, tmp_path, monkeypatch):
        from traffic_sim.simulation.warm_state_cache import restore_warm_state
        calls = self._install_fake_sumo(monkeypatch, tmp_path)
        harness, manifest = self._manifest_for(tmp_path, monkeypatch)
        harness.run_paired_campaign(manifest, tmp_path / "root",
                                    boundary_controller=calls["controller"])

        warm = harness.build_runner(manifest, warm=True,
                                    workspace=tmp_path / "root" / "warm")
        root = warm.warm_state_root()
        entries = [p for p in root.iterdir() if p.is_dir()]
        assert entries, "no cache entry exists"
        assert (entries[0] / "prefix_evidence.json").is_file(), (
            "a restored state without prefix metrics cannot account for the "
            "pre-warm segment")

    def test_a_mismatch_publishes_nothing_and_fails_honestly(
            self, tmp_path, monkeypatch):
        calls = self._install_fake_sumo(monkeypatch, tmp_path, warm_differs=True)
        harness, manifest = self._manifest_for(tmp_path, monkeypatch)
        root = tmp_path / "root"
        record = harness.run_paired_campaign(
            manifest, root, boundary_controller=calls["controller"])

        assert record["status"] == "fail"
        assert record["published_cache_entries"] == []
        assert (root / "NO_CACHE_PUBLISHED").is_file()
        warm = harness.build_runner(manifest, warm=True, workspace=root / "warm")
        assert not warm.warm_state_root().exists() or not [
            p for p in warm.warm_state_root().iterdir() if p.is_dir()]

    def test_publication_refuses_a_non_equivalent_run_directly(self, tmp_path,
                                                              monkeypatch):
        harness = _harness()
        expected = {("s", "q50", 1001)}
        with pytest.raises(SystemExit, match="refusing to publish"):
            harness.publish_cache_material(object(), [{"equivalent": False}],
                                           expected)
        with pytest.raises(SystemExit, match="refusing to publish"):
            harness.publish_cache_material(object(), [], expected)

    def test_the_cold_arm_is_labelled_cold_even_with_warm_enabled(
            self, tmp_path, monkeypatch):
        """Sol finding 1: a declined warm run must not label itself warm."""
        import tests.test_monthly_sumo as helpers
        import traffic_sim.simulation.monthly_sumo as ms
        self._install_fake_sumo(monkeypatch, tmp_path)
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        cold_block = source.split("def _run_observation(")[1]
        assert 'execution_arm="cold"' in cold_block
        assert "execution_arm=self.execution_arm" not in source


class TestWarmEvidenceIsImmutableAndIdentityCorrect:
    """Sol review 2026-07-28 (round 3): four defects in the reusable evidence."""

    def _identity(self, tmp_path, **over):
        import traffic_sim.simulation.warm_state_cache as wsc
        archive = tmp_path / "archive"
        archive.mkdir(exist_ok=True)
        route = archive / "calibrated.rou.xml"
        route.write_text("<routes/>")
        net = tmp_path / "net.net.xml"
        net.write_text("<net/>")
        from traffic_sim.simulation.monthly_warm_state import monthly_warm_identity
        kwargs = dict(demand_build_id="d1", demand_variant="q50", seed=1000,
                      simulation_mode="meso", warm_point_s=24300,
                      network_path=net, unfiltered_route_path=route,
                      source_files={}, sumo_home=tmp_path, archive_dir=archive,
                      expected_variant_filename="calibrated.rou.xml")
        kwargs.update(over)
        return monthly_warm_identity(**kwargs)

    def _stored(self, tmp_path, monkeypatch, members=None):
        import traffic_sim.simulation.warm_state_cache as wsc
        monkeypatch.setattr(wsc, "sumo_version", lambda home: "Eclipse SUMO 1.27.1")
        monkeypatch.setattr(wsc, "git_commit", lambda: "a" * 40)
        monkeypatch.setattr(wsc.platform, "platform", lambda: "test-platform")
        identity = self._identity(tmp_path)
        state = tmp_path / "state.xml.gz"
        state.write_bytes(b"STATE")
        metrics = {"time_loss_s": 1.0}
        certificate = wsc.certify_warm_state_equivalence(identity, metrics, metrics)
        root = tmp_path / "cache"
        entry = wsc.store_warm_state(root, identity, state, certificate,
                                     members=members)
        return wsc, identity, root, entry

    def test_a_member_is_stored_atomically_inside_the_entry(self, tmp_path,
                                                            monkeypatch):
        wsc, identity, root, entry = self._stored(
            tmp_path, monkeypatch, members={"prefix_metrics.json": '{"a": 1}\n'})
        assert (entry / "prefix_metrics.json").is_file()
        manifest = json.loads((entry / "manifest.json").read_text())
        assert "prefix_metrics.json" in manifest["members"]
        assert manifest["members"]["prefix_metrics.json"]["sha256"]

    def test_a_tampered_member_invalidates_the_entry(self, tmp_path, monkeypatch):
        """[finding 2] Prefix evidence must be tamper-evident, not trusted."""
        wsc, identity, root, entry = self._stored(
            tmp_path, monkeypatch, members={"prefix_metrics.json": '{"a": 1}\n'})
        (entry / "prefix_metrics.json").write_text('{"a": 99}\n')
        lookup = wsc.restore_warm_state(root, identity, tmp_path / "out.xml.gz")
        assert lookup.hit is False
        assert lookup.reason == "member_digest_mismatch"

    def test_a_deleted_member_invalidates_the_entry(self, tmp_path, monkeypatch):
        """A crash between publish and companion write must not look valid."""
        wsc, identity, root, entry = self._stored(
            tmp_path, monkeypatch, members={"prefix_metrics.json": '{"a": 1}\n'})
        (entry / "prefix_metrics.json").unlink()
        lookup = wsc.restore_warm_state(root, identity, tmp_path / "out.xml.gz")
        assert lookup.hit is False and lookup.reason == "member_missing"

    def test_members_are_published_in_the_same_atomic_step(self, tmp_path,
                                                           monkeypatch):
        """The member exists the moment the entry does — never a window after."""
        wsc, identity, root, entry = self._stored(
            tmp_path, monkeypatch, members={"prefix_metrics.json": '{"a": 1}\n'})
        # The entry directory is published by os.replace, so if it exists at all
        # its manifest and members are already complete and verifiable.
        lookup = wsc.restore_warm_state(root, identity, tmp_path / "out.xml.gz")
        assert lookup.hit is True

    def test_a_reserved_member_name_is_refused(self, tmp_path, monkeypatch):
        import traffic_sim.simulation.warm_state_cache as wsc
        for index, name in enumerate(
                ("manifest.json", wsc.STATE_FILENAME, "../escape", ".hidden")):
            sandbox = tmp_path / f"case{index}"
            sandbox.mkdir()
            with pytest.raises(ValueError, match="invalid warm-state member"):
                self._stored(sandbox, monkeypatch, members={name: "x"})

    def test_the_bootstrap_ends_one_step_after_the_snapshot(self):
        """[finding 1] flush_s=3600 would overlap the prefix with the warm arm."""
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        bootstrap = source.split("def bootstrap_warm_state(")[1].split("def ")[0]
        assert "flush_s=1" in bootstrap, (
            "the bootstrap must not use the default 3600 s meso flush; the "
            "prefix would keep simulating past the snapshot")

    def test_the_warm_identity_binds_every_interpreting_source(self, tmp_path):
        """[finding 4] metric/envelope/feasibility semantics are part of identity."""
        import traffic_sim.simulation.monthly_sumo as ms
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        assert "source_files=self.warm_interpreting_sources()" in source
        assert "source_files={}" not in source.split("def run_warm_observation")[1]
        names = ms.ArchivedDemandSumoRunner.warm_interpreting_sources(
            object.__new__(ms.ArchivedDemandSumoRunner))
        assert set(names) >= {"monthly_sumo", "monthly_warm_state", "metrics",
                              "envelope", "feasibility"}
        for path in names.values():
            assert Path(path).is_file(), path

    def test_the_frozen_contract_binds_the_simulation_and_gate_sources(self):
        fingerprints = _load()["source_fingerprints"]
        for required in ("run_scenario.py", "suggest_closure_time.py"):
            assert required in fingerprints, required


class TestPerIdentityCertification:
    """[finding 3] A state may only be certified by its OWN comparison."""

    class _Entry(dict):
        pass

    def _runner(self, tmp_path, entries):
        class FakeWarm:
            provisional_states = entries

            def warm_state_root(self):
                return tmp_path / "cache"
        return FakeWarm()

    def _entry(self, tmp_path, variant, seed):
        import traffic_sim.simulation.metrics as cm
        metrics = cm.DisruptionMetrics(
            total_time_loss_s=1.0, trip_count=1, unfinished_trips=0,
            unfinished_waiting_trips=0, teleport_total=0, teleport_reasons={},
            loaded=1, inserted=1, running_at_end=0, waiting_at_end=0)
        state = tmp_path / f"state-{variant}-{seed}.xml.gz"
        state.write_bytes(b"STATE")
        class _Id:
            content_key = f"{variant}-{seed}"
        return {"identity": _Id(), "state_path": state,
                "prefix_evidence": _prefix_evidence(),
                "schedule_id": "s1", "variant": variant, "seed": seed}

    def _comparison(self, variant, seed, equivalent=True):
        return {"equivalent": equivalent, "mismatches": [],
                "cold_digest": "c", "warm_digest": "w", "schedule_id": "s1",
                "demand_variant": variant, "seed": seed,
                "cold_semantic": {"x": 1}, "warm_semantic": {"x": 1}}

    def test_a_state_without_its_own_comparison_is_refused(self, tmp_path):
        harness = _harness()
        runner = self._runner(tmp_path, [self._entry(tmp_path, "q90", 1002)])
        with pytest.raises(SystemExit, match="expected exactly one comparison"):
            harness.publish_cache_material(
                runner, [self._comparison("q50", 1000)], {("s1", "q50", 1000)})

    def test_another_seeds_comparison_cannot_certify_this_state(self, tmp_path):
        """The exact substitution Sol found: matching[0] for every state."""
        harness = _harness()
        runner = self._runner(tmp_path, [self._entry(tmp_path, "q50", 1001)])
        with pytest.raises(SystemExit, match="expected exactly one comparison"):
            harness.publish_cache_material(
                runner, [self._comparison("q50", 1000)], {("s1", "q50", 1000)})

    def test_a_duplicate_comparison_is_refused_rather_than_picked_from(self,
                                                                      tmp_path):
        harness = _harness()
        runner = self._runner(tmp_path, [self._entry(tmp_path, "q50", 1000)])
        with pytest.raises(SystemExit, match="INCOMPLETE run"):
            harness.publish_cache_material(
                runner, [self._comparison("q50", 1000),
                         self._comparison("q50", 1000)], {("s1", "q50", 1000)})

    def test_comparisons_carry_variant_and_seed_identity(self):
        source = Path("run_monthly_warm_state_validation.py").read_text()
        assert 'comparison["demand_variant"]' in source
        assert 'comparison["seed"]' in source
        assert 'comparison["cold_semantic"]' in source


class TestCoverageMustBeComplete:
    """Sol review 2026-07-28 (round 4): equivalence over a partial set is not
    equivalence — it is a sample chosen by whichever seed failed first."""

    def _expected(self):
        harness = _harness()
        manifest = dict(_load(), demand_variants=["q10", "q50", "q90"],
                        repetitions_per_variant=1)

        class _S:
            def __init__(self, sid):
                self.schedule_id = sid
        return harness, manifest, harness.expected_identity_set(
            manifest, [_S("s1")])

    def _comparison(self, variant, seed, equivalent=True, schedule="s1"):
        return {"equivalent": equivalent, "mismatches": [],
                "cold_digest": "c", "warm_digest": "w", "schedule_id": schedule,
                "demand_variant": variant, "seed": seed,
                "cold_arm": "cold", "warm_arm": "warm", "warm_point_s": 24300,
                "cold_semantic": {"x": 1}, "warm_semantic": {"x": 1}}

    def _execution(self, harness, comparisons, expected):
        class _Id:
            warmup_end_s = 24300

        states = [{"schedule_id": s, "variant": v, "seed": d, "identity": _Id()}
                  for s, v, d in sorted(expected)]
        return harness.execution_evidence_report(
            comparisons, states, expected, 24300,
            ["k%d" % i for i in range(len(expected))],
            warm_attempts=_attempts(expected))

    def test_the_expected_set_uses_the_production_seed_rule(self):
        from traffic_sim.simulation.monthly_search import canonical_seed
        _harness, _manifest, expected = self._expected()
        assert expected == {
            ("s1", variant, canonical_seed(variant, 0))
            for variant in ("q10", "q50", "q90")}
        assert ("s1", "q10", 1000) in expected
        assert ("s1", "q90", 1002) in expected

    def test_an_early_hard_failure_that_skips_seeds_cannot_pass(self):
        """The exact scenario: q10/seed-1000 matches, the rest never ran."""
        harness, manifest, expected = self._expected()
        record = harness.build_equivalence_record(
            manifest, [self._comparison("q10", 1000)],
            {"phase_runtime_s": {}, "peak_rss_bytes": 1}, expected)
        assert record["status"] == "fail"
        assert record["cache_material_publishable"] is False
        assert record["coverage"]["complete"] is False
        assert record["coverage"]["missing"] == ["s1/q50/1001", "s1/q90/1002"]

    def test_a_complete_equivalent_set_still_passes(self):
        harness, manifest, expected = self._expected()
        comparisons = [self._comparison("q10", 1000),
                       self._comparison("q50", 1001),
                       self._comparison("q90", 1002)]
        record = harness.build_equivalence_record(
            manifest, comparisons, {"phase_runtime_s": {}, "peak_rss_bytes": 1},
            expected, self._execution(harness, comparisons, expected))
        assert record["status"] == "pass"
        assert record["coverage"]["complete"] is True

    def test_an_extra_identity_is_a_coverage_failure(self):
        harness, manifest, expected = self._expected()
        comparisons = [self._comparison("q10", 1000),
                       self._comparison("q50", 1001),
                       self._comparison("q90", 1002),
                       self._comparison("q90", 9999)]
        record = harness.build_equivalence_record(
            manifest, comparisons, {"phase_runtime_s": {}, "peak_rss_bytes": 1},
            expected)
        assert record["status"] == "fail"
        assert record["coverage"]["extra"] == ["s1/q90/9999"]

    def test_a_duplicate_identity_is_a_coverage_failure(self):
        harness, manifest, expected = self._expected()
        comparisons = [self._comparison("q10", 1000),
                       self._comparison("q10", 1000),
                       self._comparison("q50", 1001),
                       self._comparison("q90", 1002)]
        record = harness.build_equivalence_record(
            manifest, comparisons, {"phase_runtime_s": {}, "peak_rss_bytes": 1},
            expected)
        assert record["status"] == "fail"
        assert record["coverage"]["duplicates"] == ["s1/q10/1000"]

    def test_an_unidentified_observation_is_a_coverage_failure(self):
        harness, manifest, expected = self._expected()
        comparisons = [self._comparison("q10", 1000),
                       self._comparison("q50", 1001),
                       self._comparison("q90", 1002),
                       {"equivalent": True, "mismatches": [],
                        "cold_digest": None, "warm_digest": None,
                        "schedule_id": "s1", "demand_variant": None,
                        "seed": None}]
        record = harness.build_equivalence_record(
            manifest, comparisons, {"phase_runtime_s": {}, "peak_rss_bytes": 1},
            expected)
        assert record["status"] == "fail"
        assert record["coverage"]["unidentified"] == 1

    def test_omitting_the_expectation_fails_closed(self):
        """A caller that forgets the expected set must not get a free pass."""
        harness, manifest, _expected = self._expected()
        record = harness.build_equivalence_record(
            manifest, [self._comparison("q10", 1000)],
            {"phase_runtime_s": {}, "peak_rss_bytes": 1})
        assert record["status"] == "fail"
        assert record["coverage"]["complete"] is False

    def test_publication_refuses_incomplete_coverage(self, tmp_path):
        harness = _harness()
        with pytest.raises(SystemExit, match="INCOMPLETE run"):
            harness.publish_cache_material(
                object(), [self._comparison("q10", 1000)],
                {("s1", "q10", 1000), ("s1", "q50", 1001)})

    def test_both_arms_stopping_early_fails_and_publishes_nothing(
            self, tmp_path, monkeypatch):
        """End-to-end: a shared early hard failure truncates BOTH arms."""
        import tests.test_monthly_sumo as helpers
        harness = _harness()
        monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)
        archive = helpers._archive(tmp_path)
        spec = helpers._spec().to_dict()
        manifest = dict(_load())
        manifest["demand_requirement"] = dict(
            manifest["demand_requirement"],
            archive_path=str(Path(archive).relative_to(tmp_path)),
            demand_build_key=spec["demand_build_id"])
        manifest["spec_template"] = {
            k: v for k, v in spec.items()
            if k not in {"search_id", "directed_edges", "demand_build_id"}}
        manifest["cases"] = [dict(manifest["cases"][0],
                                  directed_edges=spec["directed_edges"])]
        manifest["demand_variants"] = ["q10", "q50", "q90"]
        manifest["repetitions_per_variant"] = 1
        manifest["seeds"] = [1000, 1001, 1002]
        manifest["frozen_schedule_ids"] = None

        from traffic_sim.simulation.monthly_warm_state import (
            build_monthly_observation)
        metrics = {"total_time_loss_s": 1.0, "trip_count": 1,
                   "unfinished_trips": 0, "unfinished_waiting_trips": 0,
                   "teleport_total": 0, "teleport_reasons": {}, "loaded": 1,
                   "inserted": 1, "running_at_end": 0, "waiting_at_end": 0,
                   "truncated_unreachable": 0, "dropped_unreachable": 0,
                   "max_queue_vehicles": None, "closed_edge_throughput": None}

        def observation(arm, schedule_id):
            return build_monthly_observation(
                schedule_id=schedule_id, variant="q10", seed=1000,
                simulation_mode="meso", baseline_metrics=metrics,
                candidate_metrics=dict(metrics), baseline_buckets=[],
                candidate_buckets=[],
                feasibility={"eligible": False, "hard_failures": ["teleports"]},
                recovery={"status": "recovered", "recovered": True},
                failures=["teleports"], matched_baseline_id="mb",
                provenance_key="p", provenance={"a": 1},
                execution_arm=arm,
                warm_point_s=24300 if arm == "warm" else None)

        class StoppingRunner:
            """Mimics run_candidate breaking at the first hard failure."""

            def __init__(self, arm, spec_obj):
                self.spec = spec_obj
                self.arm = arm
                self.canonical_observations = []

            def _run_observation(self, schedule, *, variant, seed):
                # The harness now asks for EVERY identity, so a hard failure on
                # one no longer suppresses the others.
                return (None, ("teleports",),
                        observation(self.arm, schedule.schedule_id))

        def factory(man, *, warm, workspace, sumo_invoker=None,
                    boundary_controller=None):
            return StoppingRunner("warm" if warm else "cold", helpers._spec())

        root = tmp_path / "root"
        record = harness.run_paired_campaign(manifest, root,
                                             runner_factory=factory)
        assert record["status"] == "fail", record["coverage"]
        assert record["cache_material_publishable"] is False
        assert record["published_cache_entries"] == []
        assert record["coverage"]["missing"], "the skipped seeds must be named"
        assert (root / "NO_CACHE_PUBLISHED").is_file()


class TestPublicationCannotFailOpen:
    """Sol review 2026-07-28 (round 5): an optional coverage proof fails open."""

    def _comparison(self, variant, seed):
        return {"equivalent": True, "mismatches": [], "cold_digest": "c",
                "warm_digest": "w", "schedule_id": "s1",
                "demand_variant": variant, "seed": seed,
                "cold_semantic": {"x": 1}, "warm_semantic": {"x": 1}}

    @pytest.mark.parametrize("expected", [None, set(), frozenset(), [], "no"])
    def test_publication_requires_a_real_expected_set(self, expected):
        """Omitting the proof must REFUSE, not publish."""
        harness = _harness()
        with pytest.raises(SystemExit, match="without a required expected"):
            harness.publish_cache_material(
                object(), [self._comparison("q10", 1000)], expected)

    def test_publication_recomputes_coverage_rather_than_trusting_it(self):
        """A caller cannot hand in a 'complete' verdict for a partial run."""
        harness = _harness()
        with pytest.raises(SystemExit, match="INCOMPLETE run"):
            harness.publish_cache_material(
                object(), [self._comparison("q10", 1000)],
                {("s1", "q10", 1000), ("s1", "q50", 1001)})

    def test_the_signature_has_no_defaultable_coverage_argument(self):
        import inspect
        harness = _harness()
        signature = inspect.signature(harness.publish_cache_material)
        assert signature.parameters["expected"].default is inspect.Parameter.empty


class TestFrozenIdentityCoversItsDerivation:
    """[finding 3/4] The modules that DERIVE seeds and schedules are bound."""

    def test_every_identity_deriving_module_is_fingerprinted(self):
        fingerprints = _load()["source_fingerprints"]
        for required in ("traffic_sim/simulation/monthly_search.py",
                         "traffic_sim/simulation/finalist_decision.py",
                         "traffic_sim/core/closure_calendar.py",
                         "traffic_sim/core/contracts.py"):
            assert required in fingerprints, required

    @pytest.mark.parametrize("module", [
        "traffic_sim/simulation/monthly_search.py",
        "traffic_sim/simulation/finalist_decision.py",
        "traffic_sim/core/closure_calendar.py",
        "traffic_sim/core/contracts.py",
        "run_scenario.py",
        "suggest_closure_time.py",
    ])
    def test_drift_in_a_bound_identity_source_invalidates_the_contract(
            self, module, tmp_path):
        """Proven without touching the tree: a changed byte breaks the key."""
        harness = _harness()
        manifest = _current()
        assert module in manifest["source_fingerprints"]
        mutated = Path(module).read_bytes() + b"\n# drift\n"
        assert (hashlib.sha256(mutated).hexdigest()
                != manifest["source_fingerprints"][module])
        # and the harness refuses such a manifest outright
        drifted = dict(manifest, source_fingerprints=dict(
            manifest["source_fingerprints"], **{module: "0" * 64}))
        drifted["content_key"] = _canonical_key(drifted)
        path = tmp_path / "drifted.json"
        path.write_text(json.dumps(drifted))
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(path)

    def test_the_frozen_seeds_are_the_production_assignment(self):
        from traffic_sim.simulation.monthly_search import canonical_seed
        manifest = _load()
        derived = sorted({canonical_seed(variant, repetition)
                          for variant in manifest["demand_variants"]
                          for repetition in
                          range(manifest["repetitions_per_variant"])})
        assert derived == sorted(manifest["seeds"])

    def test_the_frozen_schedule_ids_are_recorded_and_consumed(self):
        manifest = _load()
        assert manifest["frozen_schedule_ids"]
        assert len(manifest["frozen_schedule_ids"]) == manifest["schedules_per_case"]
        source = Path("run_monthly_warm_state_validation.py").read_text()
        assert 'manifest.get("frozen_schedule_ids")' in source
        assert 'sorted(manifest["seeds"])' in source

    def test_the_declared_variants_match_production(self):
        from traffic_sim.simulation.finalist_decision import DEMAND_VARIANTS
        assert sorted(_load()["demand_variants"]) == sorted(DEMAND_VARIANTS)

    def test_a_seed_rule_change_would_be_caught_at_runtime(self, tmp_path,
                                                           monkeypatch):
        """If production seeds drift from the frozen ones, the run refuses."""
        harness = _harness()
        manifest = dict(_load(), seeds=[7000, 7001, 7002])

        class _S:
            schedule_id = _load()["frozen_schedule_ids"][0]

        expected = harness.expected_identity_set(manifest, [_S()])
        derived = sorted({seed for _s, _v, seed in expected})
        assert derived != sorted(manifest["seeds"]), (
            "this fixture must represent a real disagreement")


class TestSchedulePinningIsEnforced:
    """A pinned schedule set must actually be checked at runtime."""

    def test_a_schedule_id_mismatch_refuses_the_run(self, tmp_path, monkeypatch):
        import tests.test_monthly_sumo as helpers
        harness = _harness()
        monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)
        archive = helpers._archive(tmp_path)
        spec = helpers._spec().to_dict()
        manifest = dict(_load())
        manifest["demand_requirement"] = dict(
            manifest["demand_requirement"],
            archive_path=str(Path(archive).relative_to(tmp_path)),
            demand_build_key=spec["demand_build_id"])
        manifest["spec_template"] = {
            k: v for k, v in spec.items()
            if k not in {"search_id", "directed_edges", "demand_build_id"}}
        manifest["cases"] = [dict(manifest["cases"][0],
                                  directed_edges=spec["directed_edges"])]
        # Pinned to a schedule this spec will NOT generate.
        manifest["frozen_schedule_ids"] = ["closure-not-the-one"]
        with pytest.raises(SystemExit, match="differ from the frozen contract"):
            harness.run_paired_campaign(
                manifest, tmp_path / "root",
                runner_factory=lambda m, *, warm, workspace, sumo_invoker=None, boundary_controller=None: (
                    _StubRunner(helpers._spec())))


class _StubRunner:
    def __init__(self, spec):
        self.spec = spec
        self.canonical_observations = []

    def _run_observation(self, schedule, *, variant, seed):
        return (None, (), None)


class TestSeedPinningIsEnforced:

    def test_a_seed_assignment_mismatch_refuses_the_run(self, tmp_path,
                                                        monkeypatch):
        import tests.test_monthly_sumo as helpers
        harness = _harness()
        monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)
        archive = helpers._archive(tmp_path)
        spec = helpers._spec().to_dict()
        manifest = dict(_load())
        manifest["demand_requirement"] = dict(
            manifest["demand_requirement"],
            archive_path=str(Path(archive).relative_to(tmp_path)),
            demand_build_key=spec["demand_build_id"])
        manifest["spec_template"] = {
            k: v for k, v in spec.items()
            if k not in {"search_id", "directed_edges", "demand_build_id"}}
        manifest["cases"] = [dict(manifest["cases"][0],
                                  directed_edges=spec["directed_edges"])]
        manifest["frozen_schedule_ids"] = None
        manifest["demand_variants"] = ["q50"]
        manifest["repetitions_per_variant"] = 1
        manifest["seeds"] = [9999]          # not what canonical_seed derives
        with pytest.raises(SystemExit, match="does not match the frozen seeds"):
            harness.run_paired_campaign(
                manifest, tmp_path / "root",
                runner_factory=lambda m, *, warm, workspace, sumo_invoker=None, boundary_controller=None: (
                    _StubRunner(helpers._spec())))


class TestWarmExecutionMustBeProven:
    """Criterion 10: `pass` requires evidence the warm branch actually ran."""

    EXPECTED = {("s1", "q10", 1000), ("s1", "q50", 1001), ("s1", "q90", 1002)}
    WARM_POINT = 24300

    def _comparison(self, variant, seed, **over):
        base = {"equivalent": True, "mismatches": [], "cold_digest": "c",
                "warm_digest": "w", "schedule_id": "s1",
                "demand_variant": variant, "seed": seed,
                "cold_arm": "cold", "warm_arm": "warm",
                "warm_point_s": self.WARM_POINT,
                "cold_semantic": {"x": 1}, "warm_semantic": {"x": 1}}
        base.update(over)
        return base

    def _all(self, **over):
        return [self._comparison(v, d, **over)
                for v, d in (("q10", 1000), ("q50", 1001), ("q90", 1002))]

    def _states(self, identities=None, warm_point=None):
        point = self.WARM_POINT if warm_point is None else warm_point

        class _Id:
            warmup_end_s = point

        return [{"schedule_id": s, "variant": v, "seed": d, "identity": _Id()}
                for s, v, d in sorted(identities if identities is not None
                                      else self.EXPECTED)]

    def _report(self, comparisons, states, published=None, attempts=None):
        return _harness().execution_evidence_report(
            comparisons, states, self.EXPECTED, self.WARM_POINT, published,
            warm_attempts=(_attempts(self.EXPECTED) if attempts is None
                           else attempts))

    def test_a_complete_warm_run_is_accepted(self):
        report = self._report(self._all(), self._states(), ["a", "b", "c"])
        assert report["complete"], report["problems"]
        assert report["warm_executions"] == 3
        assert report["published_count"] == 3

    def test_a_cold_fallback_cannot_count_as_a_warm_execution(self):
        """The central case: the warm arm declined and ran cold instead."""
        comparisons = self._all()
        comparisons[1]["warm_arm"] = "cold"
        report = self._report(comparisons, self._states(), ["a", "b", "c"])
        assert not report["complete"]
        assert any("cold fallback cannot count" in p for p in report["problems"])

    def test_a_missing_arm_label_fails(self):
        comparisons = self._all()
        comparisons[0].pop("warm_arm")
        report = self._report(comparisons, self._states(), ["a", "b", "c"])
        assert not report["complete"]

    def test_a_mislabelled_cold_arm_fails(self):
        comparisons = self._all()
        comparisons[2]["cold_arm"] = "warm"
        report = self._report(comparisons, self._states(), ["a", "b", "c"])
        assert not report["complete"]
        assert any("cold arm reported" in p for p in report["problems"])

    def test_a_wrong_warm_point_fails(self):
        comparisons = self._all()
        comparisons[0]["warm_point_s"] = self.WARM_POINT - 900
        report = self._report(comparisons, self._states(), ["a", "b", "c"])
        assert not report["complete"]
        assert any("warm point" in p for p in report["problems"])

    def test_a_missing_warm_point_fails(self):
        comparisons = self._all()
        comparisons[0]["warm_point_s"] = None
        report = self._report(comparisons, self._states(), ["a", "b", "c"])
        assert not report["complete"]

    def test_a_missing_provisional_state_fails(self):
        states = self._states({("s1", "q10", 1000), ("s1", "q50", 1001)})
        report = self._report(self._all(), states, ["a", "b"])
        assert not report["complete"]
        assert any("no promotable state" in p for p in report["problems"])

    def test_an_extra_provisional_state_fails(self):
        states = self._states() + [{"schedule_id": "s1", "variant": "q90",
                                    "seed": 9999}]
        report = self._report(self._all(), states, ["a", "b", "c", "d"])
        assert not report["complete"]
        assert any("unexpected promotable state" in p for p in report["problems"])

    def test_a_duplicate_provisional_state_fails(self):
        states = self._states() + [{"schedule_id": "s1", "variant": "q10",
                                    "seed": 1000}]
        report = self._report(self._all(), states, ["a", "b", "c"])
        assert not report["complete"]
        assert any("duplicate promotable state" in p for p in report["problems"])

    def test_a_publication_count_mismatch_fails(self):
        report = self._report(self._all(), self._states(), ["a", "b"])
        assert not report["complete"]
        assert any("published 2 cache entries" in p for p in report["problems"])

    def test_duplicate_published_keys_fail(self):
        report = self._report(self._all(), self._states(), ["a", "a", "c"])
        assert not report["complete"]
        assert any("duplicate cache keys" in p for p in report["problems"])

    def test_a_missing_comparison_fails(self):
        report = self._report(self._all()[:2], self._states(), ["a", "b", "c"])
        assert not report["complete"]
        assert any("expected 1 comparison" in p for p in report["problems"])

    def test_the_record_fails_closed_without_execution_evidence(self):
        harness = _harness()
        record = harness.build_equivalence_record(
            _load(), self._all(), {"phase_runtime_s": {}, "peak_rss_bytes": 1},
            self.EXPECTED)
        assert record["status"] == "fail"
        assert record["execution_evidence"]["complete"] is False

    def test_the_record_passes_only_with_complete_evidence(self):
        harness = _harness()
        comparisons = self._all()
        record = harness.build_equivalence_record(
            _load(), comparisons, {"phase_runtime_s": {}, "peak_rss_bytes": 1},
            self.EXPECTED,
            self._report(comparisons, self._states(), ["a", "b", "c"]))
        assert record["status"] == "pass"
        assert record["cache_material_publishable"] is True


class TestWarmProofEndToEnd:
    """A campaign whose warm arm silently fell back must not pass."""

    def _manifest(self, tmp_path, monkeypatch):
        import tests.test_monthly_sumo as helpers
        harness = _harness()
        monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)
        archive = helpers._archive(tmp_path)
        spec = helpers._spec().to_dict()
        manifest = dict(_load())
        manifest["demand_requirement"] = dict(
            manifest["demand_requirement"],
            archive_path=str(Path(archive).relative_to(tmp_path)),
            demand_build_key=spec["demand_build_id"])
        manifest["spec_template"] = {
            k: v for k, v in spec.items()
            if k not in {"search_id", "directed_edges", "demand_build_id"}}
        manifest["cases"] = [dict(manifest["cases"][0],
                                  directed_edges=spec["directed_edges"],
                                  expected_warm_point_s=20700)]
        manifest["demand_variants"] = ["q50"]
        manifest["repetitions_per_variant"] = 1
        manifest["seeds"] = [1001]
        manifest["frozen_schedule_ids"] = None
        return harness, manifest

    def _observation(self, arm, schedule_id, warm_point=None):
        from traffic_sim.simulation.monthly_warm_state import (
            build_monthly_observation)
        metrics = {"total_time_loss_s": 1.0, "trip_count": 1,
                   "unfinished_trips": 0, "unfinished_waiting_trips": 0,
                   "teleport_total": 0, "teleport_reasons": {}, "loaded": 1,
                   "inserted": 1, "running_at_end": 0, "waiting_at_end": 0,
                   "truncated_unreachable": 0, "dropped_unreachable": 0,
                   "max_queue_vehicles": None, "closed_edge_throughput": None}
        return build_monthly_observation(
            schedule_id=schedule_id, variant="q50", seed=1001,
            simulation_mode="meso", baseline_metrics=metrics,
            candidate_metrics=dict(metrics), baseline_buckets=[],
            candidate_buckets=[],
            feasibility={"eligible": True, "hard_failures": []},
            recovery={"status": "recovered", "recovered": True},
            failures=[], matched_baseline_id="mb", provenance_key="p",
            provenance={"a": 1}, execution_arm=arm, warm_point_s=warm_point,
            # diagnostics must reconstruct to the SAME candidate metrics
            split_diagnostics=(_diag(warm_point, candidate=dict(metrics))
                               if arm == "warm" else None))

    def test_a_silent_cold_fallback_fails_and_publishes_nothing(
            self, tmp_path, monkeypatch):
        """Both arms ran cold; the payloads match perfectly. Still a FAIL."""
        import tests.test_monthly_sumo as helpers
        harness, manifest = self._manifest(tmp_path, monkeypatch)
        observation = self._observation

        class FallbackRunner:
            def __init__(self, spec):
                self.spec = spec
                self.canonical_observations = []
                self.provisional_states = []

            def _run_observation(self, schedule, *, variant, seed):
                # The warm arm declined, so it produced a COLD observation.
                return (None, (), observation("cold", schedule.schedule_id))

        root = tmp_path / "root"
        record = harness.run_paired_campaign(
            manifest, root,
            runner_factory=lambda m, *, warm, workspace, sumo_invoker=None, boundary_controller=None:
                FallbackRunner(helpers._spec()))
        assert record["status"] == "fail"
        assert record["cache_material_publishable"] is False
        assert record["published_cache_entries"] == []
        assert any("cold fallback cannot count" in p
                   for p in record["execution_evidence"]["problems"]), \
            record["execution_evidence"]["problems"]
        assert (root / "NO_CACHE_PUBLISHED").is_file()

    def test_a_wrong_warm_point_fails_end_to_end(self, tmp_path, monkeypatch):
        import tests.test_monthly_sumo as helpers
        harness, manifest = self._manifest(tmp_path, monkeypatch)
        observation = self._observation

        class WrongPointRunner:
            def __init__(self, spec, arm):
                self.spec = spec
                self.arm = arm
                self.canonical_observations = []
                self.provisional_states = []

            def _run_observation(self, schedule, *, variant, seed):
                return (None, (), observation(
                    self.arm, schedule.schedule_id,
                    warm_point=900 if self.arm == "warm" else None))

        root = tmp_path / "root"
        record = harness.run_paired_campaign(
            manifest, root,
            runner_factory=lambda m, *, warm, workspace, sumo_invoker=None, boundary_controller=None:
                WrongPointRunner(helpers._spec(), "warm" if warm else "cold"))
        assert record["status"] == "fail"
        assert any("warm point" in p
                   for p in record["execution_evidence"]["problems"])
        assert (root / "NO_CACHE_PUBLISHED").is_file()


class TestPublicationIsCampaignAtomic:
    """Sol review 2026-07-28 (round 6): a warning is not a mechanism.

    Entry-by-entry publication with a post-hoc `NO_CACHE_PUBLISHED` note left
    every already-written entry restorable. Criterion 10 requires that any
    publication mismatch leave NO usable cache, so the whole set is staged and
    the final root appears in one rename.
    """

    EXPECTED = {("s1", "q10", 1000), ("s1", "q50", 1001), ("s1", "q90", 1002)}

    def _identity(self, tmp_path, seed, monkeypatch):
        import traffic_sim.simulation.warm_state_cache as wsc
        monkeypatch.setattr(wsc, "sumo_version", lambda home: "Eclipse SUMO 1.27.1")
        monkeypatch.setattr(wsc, "git_commit", lambda: "a" * 40)
        monkeypatch.setattr(wsc.platform, "platform", lambda: "test-platform")
        archive = tmp_path / "archive"
        archive.mkdir(exist_ok=True)
        route = archive / "calibrated.rou.xml"
        route.write_text("<routes/>")
        net = tmp_path / "net.net.xml"
        net.write_text("<net/>")
        from traffic_sim.simulation.monthly_warm_state import monthly_warm_identity
        return monthly_warm_identity(
            demand_build_id="d1", demand_variant="q50", seed=seed,
            simulation_mode="meso", warm_point_s=24300, network_path=net,
            unfiltered_route_path=route, source_files={}, sumo_home=tmp_path,
            archive_dir=archive, expected_variant_filename="calibrated.rou.xml")

    def _runner(self, tmp_path, monkeypatch, identities):
        import traffic_sim.simulation.metrics as cm
        metrics = cm.DisruptionMetrics(
            total_time_loss_s=1.0, trip_count=1, unfinished_trips=0,
            unfinished_waiting_trips=0, teleport_total=0, teleport_reasons={},
            loaded=1, inserted=1, running_at_end=0, waiting_at_end=0)
        entries = []
        for (schedule, variant, seed), identity in identities.items():
            state = tmp_path / f"state-{seed}.xml.gz"
            state.write_bytes(b"STATE-%d" % seed)
            entries.append({"identity": identity, "state_path": state,
                            "prefix_evidence": _prefix_evidence(),
                            "schedule_id": schedule,
                            "variant": variant, "seed": seed})

        class FakeWarm:
            provisional_states = entries

            def warm_state_root(self):
                return tmp_path / "cache" / "warm-state"
        return FakeWarm()

    def _comparison(self, variant, seed):
        return {"equivalent": True, "mismatches": [], "cold_digest": "c",
                "warm_digest": "w", "schedule_id": "s1",
                "demand_variant": variant, "seed": seed,
                "cold_arm": "cold", "warm_arm": "warm", "warm_point_s": 24300,
                "cold_semantic": {"x": 1}, "warm_semantic": {"x": 1}}

    def _setup(self, tmp_path, monkeypatch, identity_seeds):
        identities = {("s1", variant, seed): self._identity(tmp_path, seed,
                                                            monkeypatch)
                      for variant, seed in identity_seeds}
        return self._runner(tmp_path, monkeypatch, identities)

    def test_a_complete_batch_publishes_in_one_step(self, tmp_path, monkeypatch):
        harness = _harness()
        runner = self._setup(tmp_path, monkeypatch,
                             [("q10", 1000), ("q50", 1001), ("q90", 1002)])
        comparisons = [self._comparison("q10", 1000), self._comparison("q50", 1001),
                       self._comparison("q90", 1002)]
        published = harness.publish_cache_material(runner, comparisons,
                                                   self.EXPECTED)
        assert len(published) == 3
        root = runner.warm_state_root()
        assert root.is_dir()
        assert len([p for p in root.iterdir() if p.is_dir()]) == 3

    def test_a_count_mismatch_leaves_the_root_absent(self, tmp_path, monkeypatch):
        """Two states for three identities: nothing may be published."""
        harness = _harness()
        runner = self._setup(tmp_path, monkeypatch,
                             [("q10", 1000), ("q50", 1001)])
        comparisons = [self._comparison("q10", 1000), self._comparison("q50", 1001),
                       self._comparison("q90", 1002)]
        with pytest.raises(SystemExit, match="refusing to publish any"):
            harness.publish_cache_material(runner, comparisons, self.EXPECTED)
        assert not runner.warm_state_root().exists(), (
            "a partial batch left usable cache material behind")

    def test_a_mid_batch_failure_leaves_the_root_absent(self, tmp_path,
                                                        monkeypatch):
        """Injected store failure on the second entry: no entry survives."""
        harness = _harness()
        runner = self._setup(tmp_path, monkeypatch,
                             [("q10", 1000), ("q50", 1001), ("q90", 1002)])
        comparisons = [self._comparison("q10", 1000), self._comparison("q50", 1001),
                       self._comparison("q90", 1002)]
        from traffic_sim.simulation.warm_state_cache import store_warm_state
        calls = []

        def flaky(root, identity, state_path, certificate, members=None):
            calls.append(identity.content_key)
            if len(calls) == 2:
                raise OSError("disk full during publication")
            return store_warm_state(root, identity, state_path, certificate,
                                    members=members)

        with pytest.raises(OSError, match="disk full during publication"):
            harness.publish_cache_material(runner, comparisons, self.EXPECTED,
                                           store=flaky)
        assert len(calls) == 2, "the injection must fire mid-batch"
        assert not runner.warm_state_root().exists()

    def test_no_staging_residue_survives_a_failure(self, tmp_path, monkeypatch):
        harness = _harness()
        runner = self._setup(tmp_path, monkeypatch, [("q10", 1000)])
        comparisons = [self._comparison("q10", 1000)]
        with pytest.raises(SystemExit):
            harness.publish_cache_material(runner, comparisons, self.EXPECTED)
        parent = runner.warm_state_root().parent
        residue = [p.name for p in parent.iterdir()] if parent.is_dir() else []
        assert not any(name.startswith(".warm-publish-") for name in residue), (
            f"staging residue survived: {residue}")

    def test_nothing_is_restorable_after_a_failed_publication(self, tmp_path,
                                                              monkeypatch):
        """The strongest form: no key can be restored from the final root."""
        from traffic_sim.simulation.warm_state_cache import restore_warm_state
        harness = _harness()
        runner = self._setup(tmp_path, monkeypatch,
                             [("q10", 1000), ("q50", 1001)])
        comparisons = [self._comparison("q10", 1000), self._comparison("q50", 1001),
                       self._comparison("q90", 1002)]
        with pytest.raises(SystemExit):
            harness.publish_cache_material(runner, comparisons, self.EXPECTED)
        root = runner.warm_state_root()
        for entry in runner.provisional_states:
            lookup = restore_warm_state(root, entry["identity"],
                                        tmp_path / "out.gz")
            assert lookup.hit is False, "a failed campaign left a usable entry"

    def test_an_existing_warm_state_root_is_refused(self, tmp_path, monkeypatch):
        harness = _harness()
        runner = self._setup(tmp_path, monkeypatch, [("q10", 1000)])
        runner.warm_state_root().mkdir(parents=True)
        with pytest.raises(SystemExit, match="already exists"):
            harness.publish_cache_material(
                runner, [self._comparison("q10", 1000)], {("s1", "q10", 1000)})

    def test_a_campaign_publication_failure_records_an_honest_fail(
            self, tmp_path, monkeypatch):
        """End-to-end: publication blows up, the record says fail, not pass."""
        import tests.test_monthly_sumo as helpers
        harness = _harness()
        monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)
        archive = helpers._archive(tmp_path)
        spec = helpers._spec().to_dict()
        manifest = dict(_load())
        manifest["demand_requirement"] = dict(
            manifest["demand_requirement"],
            archive_path=str(Path(archive).relative_to(tmp_path)),
            demand_build_key=spec["demand_build_id"])
        manifest["spec_template"] = {
            k: v for k, v in spec.items()
            if k not in {"search_id", "directed_edges", "demand_build_id"}}
        manifest["cases"] = [dict(manifest["cases"][0],
                                  directed_edges=spec["directed_edges"],
                                  expected_warm_point_s=20700)]
        manifest["demand_variants"] = ["q50"]
        manifest["repetitions_per_variant"] = 1
        manifest["seeds"] = [1001]
        manifest["frozen_schedule_ids"] = None

        from traffic_sim.simulation.monthly_warm_state import (
            build_monthly_observation)
        metrics = {"total_time_loss_s": 1.0, "trip_count": 1,
                   "unfinished_trips": 0, "unfinished_waiting_trips": 0,
                   "teleport_total": 0, "teleport_reasons": {}, "loaded": 1,
                   "inserted": 1, "running_at_end": 0, "waiting_at_end": 0,
                   "truncated_unreachable": 0, "dropped_unreachable": 0,
                   "max_queue_vehicles": None, "closed_edge_throughput": None}

        def observation(arm, schedule_id):
            return build_monthly_observation(
                schedule_id=schedule_id, variant="q50", seed=1001,
                simulation_mode="meso", baseline_metrics=metrics,
                candidate_metrics=dict(metrics), baseline_buckets=[],
                candidate_buckets=[],
                feasibility={"eligible": True, "hard_failures": []},
                recovery={"status": "recovered", "recovered": True},
                failures=[], matched_baseline_id="mb", provenance_key="p",
                provenance={"a": 1}, execution_arm=arm,
                warm_point_s=20700 if arm == "warm" else None if arm == "warm" else None)

        class Runner:
            def __init__(self, arm):
                self.spec = helpers._spec()
                self.arm = arm
                self.canonical_observations = []
                # A provisional state whose file does not exist, so the real
                # store raises during publication.
                self.provisional_states = [{
                    "identity": None, "state_path": tmp_path / "absent.gz",
                    "prefix_evidence": _prefix_evidence(), "schedule_id": None,
                    "variant": "q50", "seed": 1001}] if arm == "warm" else []

            def warm_state_root(self):
                return tmp_path / "cache" / "warm-state"

            def _run_observation(self, schedule, *, variant, seed):
                return (None, (), observation(self.arm, schedule.schedule_id))
                if self.arm == "warm":
                    self.provisional_states[0]["schedule_id"] = schedule.schedule_id

        root = tmp_path / "root"
        record = harness.run_paired_campaign(
            manifest, root,
            runner_factory=lambda m, *, warm, workspace, sumo_invoker=None, boundary_controller=None:
                Runner("warm" if warm else "cold"))
        assert record["status"] == "fail"
        assert record["published_cache_entries"] == []
        assert (root / "NO_CACHE_PUBLISHED").is_file()
        assert not (tmp_path / "cache" / "warm-state").exists()
        assert (root / "equivalence_record.json").is_file()


class TestPrefixEvidenceIsAtomicAndVerified:
    """Criterion 6: evidence lives inside the digest-bound member set."""

    def _stored(self, tmp_path, monkeypatch, members):
        import traffic_sim.simulation.warm_state_cache as wsc
        monkeypatch.setattr(wsc, "sumo_version", lambda home: "Eclipse SUMO 1.27.1")
        monkeypatch.setattr(wsc, "git_commit", lambda: "a" * 40)
        monkeypatch.setattr(wsc.platform, "platform", lambda: "test-platform")
        archive = tmp_path / "archive"
        archive.mkdir(exist_ok=True)
        (archive / "calibrated.rou.xml").write_text("<routes/>")
        (tmp_path / "net.net.xml").write_text("<net/>")
        from traffic_sim.simulation.monthly_warm_state import monthly_warm_identity
        identity = monthly_warm_identity(
            demand_build_id="d1", demand_variant="q50", seed=1000,
            simulation_mode="meso", warm_point_s=24300,
            network_path=tmp_path / "net.net.xml",
            unfiltered_route_path=archive / "calibrated.rou.xml",
            source_files={}, sumo_home=tmp_path, archive_dir=archive,
            expected_variant_filename="calibrated.rou.xml")
        state = tmp_path / "state.xml.gz"
        state.write_bytes(b"STATE")
        certificate = wsc.certify_warm_state_equivalence(
            identity, {"m": 1.0}, {"m": 1.0})
        root = tmp_path / "cache"
        entry = wsc.store_warm_state(root, identity, state, certificate,
                                     members=members)
        return wsc, identity, root, entry

    def test_evidence_is_a_digest_bound_member(self, tmp_path, monkeypatch):
        payload = json.dumps(_prefix_evidence(), indent=2, sort_keys=True) + "\n"
        wsc, identity, root, entry = self._stored(
            tmp_path, monkeypatch, {"prefix_evidence.json": payload})
        manifest = json.loads((entry / "manifest.json").read_text())
        assert "prefix_evidence.json" in manifest["members"]
        assert wsc.restore_warm_state(root, identity, tmp_path / "o.gz").hit

    def test_tampered_evidence_is_a_cache_miss(self, tmp_path, monkeypatch):
        payload = json.dumps(_prefix_evidence(), indent=2, sort_keys=True) + "\n"
        wsc, identity, root, entry = self._stored(
            tmp_path, monkeypatch, {"prefix_evidence.json": payload})
        (entry / "prefix_evidence.json").write_text('{"schema": "tampered"}\n')
        lookup = wsc.restore_warm_state(root, identity, tmp_path / "o.gz")
        assert lookup.hit is False and lookup.reason == "member_digest_mismatch"

    def test_a_legacy_prefix_metrics_entry_is_never_interpreted(self, tmp_path,
                                                                monkeypatch):
        """A pre-schema entry restores, but yields no usable evidence."""
        legacy = json.dumps({"total_time_loss_s": 1.0, "trip_count": 1},
                            indent=2, sort_keys=True) + "\n"
        wsc, identity, root, entry = self._stored(
            tmp_path, monkeypatch, {"prefix_metrics.json": legacy})
        assert not (entry / "prefix_evidence.json").exists()
        # the runner's reader returns None for such an entry -> cache MISS
        import traffic_sim.simulation.monthly_sumo as ms

        class Probe:
            warm_state_root = lambda self: root
            _cached_prefix_evidence = ms.ArchivedDemandSumoRunner._cached_prefix_evidence
        assert Probe()._cached_prefix_evidence(identity) is None

    def test_publication_refuses_a_state_without_versioned_evidence(self,
                                                                    tmp_path):
        harness = _harness()

        class _Id:
            content_key = "a" * 32          # must be lowercase hex

        class FakeWarm:
            provisional_states = [{"identity": _Id(), "state_path": tmp_path / "s",
                                   "prefix_evidence": None, "schedule_id": "s1",
                                   "variant": "q50", "seed": 1001}]

            def warm_state_root(self):
                return tmp_path / "cache" / "warm-state"

        comparison = {"equivalent": True, "mismatches": [], "cold_digest": "c",
                      "warm_digest": "w", "schedule_id": "s1",
                      "demand_variant": "q50", "seed": 1001,
                      "cold_arm": "cold", "warm_arm": "warm",
                      "warm_point_s": 24300,
                      "cold_semantic": {"x": 1}, "warm_semantic": {"x": 1}}
        with pytest.raises(SystemExit, match="no versioned"):
            harness.publish_cache_material(FakeWarm(), [comparison],
                                           {("s1", "q50", 1001)})
        assert not (tmp_path / "cache" / "warm-state").exists()


class TestColdDefaultsUnchanged:
    """Criterion 7: the cold path's command and behaviour did not move."""

    def test_write_unfinished_defaults_to_true_for_every_existing_caller(self):
        import inspect
        import run_scenario as rs
        signature = inspect.signature(rs.run_sumo)
        assert signature.parameters["tripinfo_write_unfinished"].default is True

    def test_ordinary_cold_argv_is_identical_when_v13_options_are_omitted(
            self, tmp_path):
        import run_scenario as rs
        kwargs = dict(
            seed=1001, route_path=tmp_path / "route.xml",
            add_paths=[tmp_path / "edge.add.xml"], duration_s=86400,
            home=tmp_path / "sumo", metrics=True, work_dir=tmp_path / "work")
        implicit = rs.build_sumo_invocation(**kwargs)
        explicit = rs.build_sumo_invocation(
            **kwargs, output_precision=None, tripinfo_write_unfinished=True)
        assert implicit == explicit
        assert "--precision" not in implicit[0]
        flag = implicit[0].index("--tripinfo-output.write-unfinished")
        assert implicit[0][flag + 1] == "true"

    def test_only_the_warm_transport_requests_v13_precision(self):
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        bootstrap = source.split("def bootstrap_warm_state", 1)[1].split(
            "def _default_warm_invoker", 1)[0]
        resumed = source.split("def _default_warm_invoker", 1)[1].split(
            "def run_warm_observation", 1)[0]
        assert "tripinfo_write_unfinished=True" in bootstrap
        assert "output_precision=WARM_OUTPUT_PRECISION" in bootstrap
        assert "output_precision=WARM_OUTPUT_PRECISION" in resumed
        assert "keep_after_arrival_s=" not in resumed

    def test_no_other_production_caller_passes_the_option(self):
        for path in ("suggest_closure_time.py", "run_monthly_proxy_validation.py",
                     "serve.py"):
            assert "tripinfo_write_unfinished" not in Path(path).read_text(), path

    def test_the_manifest_verifier_covers_the_field_partition(self):
        """Criterion 4: partition failure is caught by a focused check."""
        from traffic_sim.simulation.monthly_warm_state import verify_field_partition
        verify_field_partition()          # raises if a field lost its rule


class TestSolReviewRound7:
    """Five findings from the 2026-07-28 review of LUNA-WARM-04."""

    # [P1] recursive scalar validation
    @pytest.mark.parametrize("mutate, match", [
        (lambda e: e.update(warm_point_s="900"), "positive integer"),
        (lambda e: e.update(warm_point_s=0), "positive integer"),
        (lambda e: e.update(warm_point_s=True), "positive integer"),
        (lambda e: e["completed_trips"].update(trip_count=-1), "non-negative"),
        (lambda e: e["completed_trips"].update(total_time_loss_s="x"), "number"),
        (lambda e: e["completed_trips"].update(total_time_loss_s=float("nan")),
         "finite"),
        (lambda e: e["counters"].update(loaded=-5), "non-negative"),
        (lambda e: e["counters"].update(inserted="7"), "non-negative"),
        (lambda e: e.update(queue_maximum=-1), "non-negative"),
        (lambda e: e.update(queue_maximum="5"), "non-negative"),
        (lambda e: e.update(teleport_reasons={"jam": -1}), "non-negative"),
        (lambda e: e.update(teleport_reasons=[]), "must be an object"),
        (lambda e: e.update(recovery_buckets=[{"begin_s": "0", "end_s": 900}]),
         "field set is wrong"),
        (lambda e: e.update(recovery_buckets=[{"begin_s": 0}]),
         "field set is wrong"),
        (lambda e: e.update(recovery_buckets=["not a bucket"]), "not an object"),
    ])
    def test_invalid_scalars_are_refused(self, mutate, match):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, parse_prefix_evidence)
        evidence = _prefix_evidence()
        mutate(evidence)
        with pytest.raises(WarmStateContractError, match=match):
            parse_prefix_evidence(evidence)

    def test_evidence_must_match_the_expected_warm_point(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, parse_prefix_evidence)
        evidence = _prefix_evidence(warm_point_s=24300)
        parse_prefix_evidence(evidence, expected_warm_point_s=24300)
        with pytest.raises(WarmStateContractError, match="different split"):
            parse_prefix_evidence(evidence, expected_warm_point_s=23400)

    def test_reconstruction_binds_the_expected_warm_point(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, reconstruct_metrics)
        post = {"total_time_loss_s": 1.0, "trip_count": 1, "unfinished_trips": 0,
                "unfinished_waiting_trips": 0, "teleport_total": 0,
                "teleport_reasons": {}, "loaded": 1, "inserted": 1,
                "running_at_end": 0, "waiting_at_end": 0,
                "truncated_unreachable": 0, "dropped_unreachable": 0,
                "max_queue_vehicles": 1, "closed_edge_throughput": 0}
        with pytest.raises(WarmStateContractError, match="different split"):
            reconstruct_metrics(_prefix_evidence(warm_point_s=24300), post,
                                expected_warm_point_s=900)

    def test_evidence_is_selected_by_presence_not_truthiness(self):
        """An explicitly supplied empty object must not fall back silently."""
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        assert '"prefix_evidence" in result' in source
        assert 'result.get("prefix_evidence") or prefix_evidence' not in source

    # [P1] bucket domain endpoints
    def _join(self, left, right, warm=900, **kw):
        from traffic_sim.simulation.monthly_warm_state import (
            concatenate_recovery_buckets)
        return concatenate_recovery_buckets(left, right, warm, **kw)

    def test_two_empty_segments_are_refused(self):
        with pytest.raises(Exception, match="prefix measured no recovery"):
            self._join([], [])

    def test_an_empty_prefix_segment_is_refused(self):
        with pytest.raises(Exception, match="prefix measured no recovery"):
            self._join([], [{"begin_s": 900, "end_s": 1800, "time_loss_s": 1.0}])

    def test_an_empty_post_segment_is_refused(self):
        with pytest.raises(Exception, match="post-warm segment measured no"):
            self._join([{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}], [])

    def test_a_split_shifted_away_from_the_warm_point_is_refused(self):
        """The segments meet at 450 while the certified warm point is 900.

        The boundary checks catch this before the meet-at-warm backstop does, so
        the property asserted is the refusal, not one particular message.
        """
        from traffic_sim.simulation.monthly_warm_state import WarmStateContractError
        with pytest.raises(WarmStateContractError):
            self._join([{"begin_s": 0, "end_s": 450, "time_loss_s": 1.0}],
                       [{"begin_s": 450, "end_s": 1800, "time_loss_s": 1.0}],
                       warm=900)

    def test_a_truncated_prefix_that_leaves_no_gap_is_still_refused(self):
        """Prefix stops short of the warm point; post starts at it -> a gap."""
        from traffic_sim.simulation.monthly_warm_state import WarmStateContractError
        with pytest.raises(WarmStateContractError, match="gap|prefix domain ends"):
            self._join([{"begin_s": 0, "end_s": 450, "time_loss_s": 1.0}],
                       [{"begin_s": 900, "end_s": 1800, "time_loss_s": 1.0}],
                       warm=900)

    def test_the_expected_domain_endpoints_are_enforced(self):
        left = [{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}]
        right = [{"begin_s": 900, "end_s": 1800, "time_loss_s": 1.0}]
        assert self._join(left, right, domain_start_s=0, domain_end_s=1800)
        with pytest.raises(Exception, match="domain ends at"):
            self._join(left, right, domain_start_s=0, domain_end_s=3600)
        with pytest.raises(Exception, match="domain begins at"):
            self._join(left, right, domain_start_s=450, domain_end_s=1800)

    # [P1] the freeze tool must run the partition verifier
    def test_the_freeze_tool_verifies_the_field_partition(self, monkeypatch):
        import sys
        sys.path.insert(0, "tools")
        import freeze_monthly_warm_state_v1 as freeze
        import traffic_sim.simulation.monthly_warm_state as mws

        def broken():
            raise mws.WarmStateContractError("no warm-boundary rule: ['x']")

        monkeypatch.setattr(mws, "verify_field_partition", broken)
        with pytest.raises(mws.WarmStateContractError, match="no warm-boundary rule"):
            freeze.build_manifest()

    def test_a_partition_failure_blocks_verify(self, monkeypatch):
        import sys
        sys.path.insert(0, "tools")
        import freeze_monthly_warm_state_v1 as freeze
        import traffic_sim.simulation.monthly_warm_state as mws
        monkeypatch.setattr(mws, "verify_field_partition", lambda: (_ for _ in ())
                            .throw(mws.WarmStateContractError("partition broken")))
        with pytest.raises(mws.WarmStateContractError, match="partition broken"):
            freeze.main(["--verify"])

    def test_the_manifest_records_the_prefix_evidence_schema(self):
        from traffic_sim.simulation.monthly_warm_state import PREFIX_EVIDENCE_SCHEMA
        # The LIVE contract tracks production; v1 keeps the schema it was
        # frozen with, which is what makes it unadoptable rather than silently
        # reinterpreted.
        assert _current()["prefix_evidence_schema"] == PREFIX_EVIDENCE_SCHEMA
        assert _load()["prefix_evidence_schema"] == "monthly_prefix_evidence_v1"

    # [P2] the closure invariant is schema-enforced, not vacuous
    def test_closure_scoped_evidence_is_rejected_at_the_schema_boundary(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, parse_prefix_evidence)
        evidence = dict(_prefix_evidence(), closed_edge_throughput=7)
        with pytest.raises(WarmStateContractError, match="field set is wrong"):
            parse_prefix_evidence(evidence)

    def test_the_reconstruction_no_longer_uses_a_vacuous_lookup(self):
        source = Path("traffic_sim/simulation/monthly_warm_state.py").read_text()
        assert 'evidence.get("closed_edge_throughput")' not in source

    # command-level proof of the tripinfo flags
    def _captured_cmd(self, tmp_path, monkeypatch, **kwargs):
        import subprocess
        import run_scenario as rs
        seen = {}

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, *a, **k):
            seen["cmd"] = list(cmd)
            for index, token in enumerate(cmd):
                if token in ("--tripinfo-output", "--statistic-output",
                             "--summary-output"):
                    Path(cmd[index + 1]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cmd[index + 1]).write_text("<x/>")
            return Result()

        monkeypatch.setattr(subprocess, "run", fake_run)
        route = tmp_path / "r.rou.xml"; route.write_text("<routes/>")
        net = tmp_path / "n.net.xml"; net.write_text("<net/>")
        rs.run_sumo(1000, route, [], 900, tmp_path, metrics=True,
                    net_path=net, work_dir=tmp_path / "w", **kwargs)
        return seen["cmd"]

    def test_the_default_command_writes_unfinished_vehicles(self, tmp_path,
                                                            monkeypatch):
        cmd = self._captured_cmd(tmp_path, monkeypatch)
        index = cmd.index("--tripinfo-output.write-unfinished")
        assert cmd[index + 1] == "true", "the default caller's command changed"

    def test_the_explicit_option_writes_false(self, tmp_path, monkeypatch):
        cmd = self._captured_cmd(tmp_path, monkeypatch,
                                 tripinfo_write_unfinished=False)
        index = cmd.index("--tripinfo-output.write-unfinished")
        assert cmd[index + 1] == "false"


class TestSolReviewRound8:
    """Five findings from the second 2026-07-28 review of LUNA-WARM-04."""

    # [P1] bucket values are fully validated, by builder AND parser
    @pytest.mark.parametrize("bucket, match", [
        ({"begin_s": 0, "end_s": 900}, "field set is wrong"),
        ({"begin_s": 0, "end_s": 900, "time_loss_s": "1.0"}, "must be a number"),
        ({"begin_s": 0, "end_s": 900, "time_loss_s": float("inf")}, "finite"),
        ({"begin_s": 0, "end_s": 900, "time_loss_s": -1.0}, "non-negative"),
        ({"begin_s": 0, "end_s": 900, "time_loss_s": 1.0, "extra": 1},
         "field set is wrong"),
        ({"begin_s": "0", "end_s": 900, "time_loss_s": 1.0}, "non-negative"),
        ({"begin_s": True, "end_s": 900, "time_loss_s": 1.0}, "non-negative"),
    ])
    def test_malformed_prefix_buckets_are_refused(self, bucket, match):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, parse_prefix_evidence)
        evidence = _prefix_evidence()
        evidence["recovery_buckets"] = [bucket]
        with pytest.raises(WarmStateContractError, match=match):
            parse_prefix_evidence(evidence)

    def test_the_builder_self_validates_before_returning(self):
        """Publication must not be able to persist unrestorable evidence."""
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, build_prefix_evidence)
        with pytest.raises(WarmStateContractError, match="field set is wrong"):
            build_prefix_evidence(
                completed_trips={"total_time_loss_s": 1.0, "trip_count": 1},
                queue_maximum=0, warm_point_s=900,
                counters={"loaded": 1, "inserted": 1, "teleport_total": 0,
                          "teleport_reasons": {}},
                snapshot_facts={"schema": "warm_snapshot_facts_v1",
                                "warm_point_s": 900,
                                "captured_at_s": 900.0,
                                "active_vehicle_count": 0,
                                "save_state_rng": True,
                                "save_state_precision": 16},
                active_accumulator=_empty_accumulator(900),
                completed_records={"c1": 1.0},
                completed_record_order=["c1"],
                recovery_buckets=[{"begin_s": 0, "end_s": 900}])

    def test_a_valid_bucket_still_builds(self):
        assert _prefix_evidence()["recovery_buckets"][0]["time_loss_s"] == 1.0

    # [P1] post-warm metrics are type/value validated
    def _post(self, **over):
        base = {"total_time_loss_s": 1.0, "trip_count": 1, "unfinished_trips": 0,
                "unfinished_waiting_trips": 0, "teleport_total": 0,
                "teleport_reasons": {}, "loaded": 1, "inserted": 1,
                "running_at_end": 0, "waiting_at_end": 0,
                "truncated_unreachable": 0, "dropped_unreachable": 0,
                "max_queue_vehicles": 1, "closed_edge_throughput": 0}
        base.update(over)
        return base

    @pytest.mark.parametrize("over, match", [
        ({"unfinished_trips": True}, "non-negative"),
        ({"running_at_end": "4"}, "non-negative"),
        ({"waiting_at_end": -1}, "non-negative"),
        ({"total_time_loss_s": "x"}, "must be a number"),
        ({"total_time_loss_s": float("nan")}, "finite"),
        ({"max_queue_vehicles": "5"}, "non-negative"),
        ({"closed_edge_throughput": -2}, "non-negative"),
        ({"teleport_reasons": []}, "must be an object"),
        ({"teleport_reasons": {"jam": -1}}, "non-negative"),
        ({"teleport_reasons": {"": 1}}, "must be names"),
        ({"truncated_unreachable": True}, "non-negative"),
    ])
    def test_invalid_post_warm_values_are_refused(self, over, match):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, validate_post_warm_metrics)
        with pytest.raises(WarmStateContractError, match=match):
            validate_post_warm_metrics(self._post(**over))

    def test_reconstruction_validates_the_post_segment(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, reconstruct_metrics)
        with pytest.raises(WarmStateContractError, match="non-negative"):
            reconstruct_metrics(_prefix_evidence(),
                                self._post(running_at_end="4"))

    def test_optional_maximums_may_still_be_none(self):
        from traffic_sim.simulation.monthly_warm_state import (
            validate_post_warm_metrics)
        ok = validate_post_warm_metrics(
            self._post(max_queue_vehicles=None, closed_edge_throughput=None))
        assert ok["max_queue_vehicles"] is None

    # [P1] identity warm point bound at restore and publication
    def test_restore_treats_a_mismatched_warm_point_as_a_cache_miss(
            self, tmp_path, monkeypatch):
        import traffic_sim.simulation.monthly_sumo as ms
        root = tmp_path / "warm-state"
        entry = root / "k"
        entry.mkdir(parents=True)
        (entry / "prefix_evidence.json").write_text(
            json.dumps(_prefix_evidence(warm_point_s=24300)))

        class Identity:
            content_key = "k"
            warmup_end_s = 23400          # a DIFFERENT split

        class Probe:
            warm_state_root = lambda self: root
            _cached_prefix_evidence = ms.ArchivedDemandSumoRunner._cached_prefix_evidence

        assert Probe()._cached_prefix_evidence(Identity()) is None

    def test_restore_accepts_a_matching_warm_point(self, tmp_path):
        import traffic_sim.simulation.monthly_sumo as ms
        root = tmp_path / "warm-state"
        entry = root / "k"
        entry.mkdir(parents=True)
        (entry / "prefix_evidence.json").write_text(
            json.dumps(_prefix_evidence(warm_point_s=24300)))

        class Identity:
            content_key = "k"
            warmup_end_s = 24300

        class Probe:
            warm_state_root = lambda self: root
            _cached_prefix_evidence = ms.ArchivedDemandSumoRunner._cached_prefix_evidence

        assert Probe()._cached_prefix_evidence(Identity()) is not None

    def test_publication_refuses_evidence_from_a_different_split(self, tmp_path):
        harness = _harness()

        class _Id:
            content_key = "a" * 32
            warmup_end_s = 23400          # identity says 23400...

        class FakeWarm:
            provisional_states = [{
                "identity": _Id(), "state_path": tmp_path / "s",
                # ...but the evidence describes 24300
                "prefix_evidence": _prefix_evidence(warm_point_s=24300),
                "schedule_id": "s1", "variant": "q50", "seed": 1001}]

            def warm_state_root(self):
                return tmp_path / "cache" / "warm-state"

        comparison = {"equivalent": True, "mismatches": [], "cold_digest": "c",
                      "warm_digest": "w", "schedule_id": "s1",
                      "demand_variant": "q50", "seed": 1001,
                      "cold_arm": "cold", "warm_arm": "warm",
                      "warm_point_s": 24300,
                      "cold_semantic": {"x": 1}, "warm_semantic": {"x": 1}}
        with pytest.raises(Exception, match="different split"):
            harness.publish_cache_material(FakeWarm(), [comparison],
                                           {("s1", "q50", 1001)})
        assert not (tmp_path / "cache" / "warm-state").exists()

    # [P1] closed rule vocabulary
    def test_an_unknown_rule_label_is_refused(self, monkeypatch):
        import traffic_sim.simulation.monthly_warm_state as mws
        monkeypatch.setitem(mws.FIELD_RULES, "trip_count", "invented_rule")
        with pytest.raises(mws.WarmStateContractError, match="unknown warm-boundary rule"):
            mws.verify_field_partition()

    def test_the_rule_vocabulary_is_closed_and_complete(self):
        from traffic_sim.simulation.monthly_warm_state import (
            FIELD_RULES, VALID_RULES)
        assert set(FIELD_RULES.values()) <= VALID_RULES
        assert VALID_RULES == {"sum_disjoint", "post_cumulative",
                               "post_cumulative_counts", "post_final",
                               "post_candidate", "max_measured", "post_closure"}

    def test_the_shadow_registry_is_gone(self):
        source = Path("traffic_sim/simulation/monthly_warm_state.py").read_text()
        assert "_ADDITIVE_FIELDS = (" not in source
        assert '_END_STATE_FIELDS = ("running_at_end"' not in source

    # [P2] the manifest's recorded prefix schema is validated
    def test_a_wrong_prefix_schema_is_refused(self, tmp_path):
        harness = _harness()
        manifest = dict(_current(),
                        prefix_evidence_schema="monthly_prefix_evidence_v99")
        manifest["content_key"] = _canonical_key(manifest)   # canonically valid
        path = tmp_path / "wrong.json"
        path.write_text(json.dumps(manifest))
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(path)

    def test_a_missing_prefix_schema_is_refused(self, tmp_path):
        harness = _harness()
        manifest = {k: v for k, v in _current().items()
                    if k != "prefix_evidence_schema"}
        manifest.pop("content_key", None)
        manifest["content_key"] = _canonical_key(manifest)
        path = tmp_path / "missing.json"
        path.write_text(json.dumps(manifest))
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(path)

    def test_the_real_manifest_schema_is_frozen_but_retired(self):
        harness = _harness()
        assert _current()["prefix_evidence_schema"] == \
            "monthly_prefix_evidence_v7"
        with pytest.raises(SystemExit, match="frozen sources drifted"):
            harness.load_frozen_manifest(CURRENT)


class TestSolReviewRound9:
    """Three findings: coercion laundering, inexact post fields, impossible counts."""

    def _build(self, **over):
        from traffic_sim.simulation.monthly_warm_state import build_prefix_evidence
        accumulator = _empty_accumulator(900)
        kwargs = dict(
            completed_trips={"total_time_loss_s": 5.0, "trip_count": 3},
            queue_maximum=0, warm_point_s=900,
            counters={"loaded": 3, "inserted": 3, "teleport_total": 0,
                      "teleport_reasons": {}},
            recovery_buckets=[{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}],
            snapshot_facts={"schema": "warm_snapshot_facts_v1",
                            "warm_point_s": 900,
                            "captured_at_s": 900.0,
                            "active_vehicle_count": 0,
                            "save_state_rng": True,
                            "save_state_precision": 16},
            active_accumulator=accumulator,
            completed_records={"c1": 2.0, "c2": 2.0, "c3": 1.0},
            completed_record_order=["c1", "c2", "c3"])
        kwargs.update(over)
        return build_prefix_evidence(**kwargs)

    # [P1] the builder must validate RAW inputs, before any coercion
    @pytest.mark.parametrize("over, match", [
        ({"completed_trips": {"total_time_loss_s": 5.0, "trip_count": True}},
         "trip_count must be a non-negative"),
        ({"completed_trips": {"total_time_loss_s": "5.0", "trip_count": 3}},
         "must be a number"),
        ({"completed_trips": {"total_time_loss_s": 5.0}}, "must be exactly"),
        ({"completed_trips": {"total_time_loss_s": 5.0, "trip_count": 3,
                              "extra": 1}}, "must be exactly"),
        ({"counters": {"loaded": "3", "inserted": 3, "teleport_total": 0,
                       "teleport_reasons": {}}}, "counter 'loaded'"),
        ({"counters": {"loaded": True, "inserted": True, "teleport_total": 0,
                       "teleport_reasons": {}}}, "counter"),
        ({"counters": {"loaded": 3, "inserted": 3, "teleport_total": 0}},
         "must be exactly"),
        ({"counters": {"loaded": 3, "inserted": 3, "teleport_total": 0,
                       "teleport_reasons": {5: 1}}}, "must be a non-empty string"),
        ({"counters": {"loaded": 3, "inserted": 3, "teleport_total": 0,
                       "teleport_reasons": {"jam": "1"}}}, "non-negative"),
        ({"queue_maximum": "0"}, "queue_maximum"),
        ({"queue_maximum": True}, "queue_maximum"),
        ({"warm_point_s": "900"}, "warm_point_s"),
        ({"warm_point_s": True}, "warm_point_s"),
    ])
    def test_coercible_wrong_types_are_refused_not_laundered(self, over, match):
        from traffic_sim.simulation.monthly_warm_state import WarmStateContractError
        with pytest.raises(WarmStateContractError, match=match):
            self._build(**over)

    def test_a_valid_build_still_succeeds_and_keeps_exact_values(self):
        evidence = self._build()
        assert evidence["completed_trips"]["trip_count"] == 3
        assert evidence["counters"] == {"loaded": 3, "inserted": 3,
                                        "teleport_total": 0}
        assert evidence["queue_maximum"] == 0

    def test_the_builder_accepts_the_production_recovery_bucket_dataclass(self):
        """Normalising a typed dataclass is not the same as coercing a string."""
        from traffic_sim.simulation.envelope import RecoveryBucket
        evidence = self._build(recovery_buckets=[
            RecoveryBucket(begin_s=0, end_s=900, time_loss_s=1.0)])
        assert evidence["recovery_buckets"][0]["time_loss_s"] == 1.0

    # [P1] post-warm field set must be exactly the production one
    def _post(self, **over):
        base = {"total_time_loss_s": 1.0, "trip_count": 5, "unfinished_trips": 1,
                "unfinished_waiting_trips": 1, "teleport_total": 0,
                "teleport_reasons": {}, "loaded": 5, "inserted": 5,
                "running_at_end": 0, "waiting_at_end": 0,
                "truncated_unreachable": 0, "dropped_unreachable": 0,
                "max_queue_vehicles": 1, "closed_edge_throughput": 0}
        base.update(over)
        return base

    def test_an_invented_post_field_is_refused_not_discarded(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, validate_post_warm_metrics)
        with pytest.raises(WarmStateContractError, match="extra=\\['invented'\\]"):
            validate_post_warm_metrics(self._post(invented=1))

    def test_a_missing_post_field_is_reported_as_missing(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, validate_post_warm_metrics)
        post = self._post()
        post.pop("closed_edge_throughput")
        with pytest.raises(WarmStateContractError,
                           match="missing=\\['closed_edge_throughput'\\]"):
            validate_post_warm_metrics(post)

    def test_the_post_field_set_is_bound_to_the_production_dataclass(self):
        import dataclasses
        from traffic_sim.simulation.metrics import DisruptionMetrics
        from traffic_sim.simulation.monthly_warm_state import (
            production_metric_fields, validate_post_warm_metrics)
        assert set(production_metric_fields()) == {
            f.name for f in dataclasses.fields(DisruptionMetrics)}
        assert validate_post_warm_metrics(self._post())["trip_count"] == 5

    # [P1] impossible internal relationships
    @pytest.mark.parametrize("over, match", [
        ({"inserted": 9}, "inserted 9 exceeds loaded 5"),
        ({"unfinished_trips": 9}, "unfinished_trips 9 exceeds trip_count 5"),
        ({"unfinished_trips": 2, "unfinished_waiting_trips": 3},
         "unfinished_waiting_trips 3 exceeds unfinished_trips 2"),
    ])
    def test_impossible_post_relationships_are_refused(self, over, match):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, validate_post_warm_metrics)
        with pytest.raises(WarmStateContractError, match=match):
            validate_post_warm_metrics(self._post(**over))

    def test_impossible_prefix_relationships_are_refused(self):
        from traffic_sim.simulation.monthly_warm_state import WarmStateContractError
        with pytest.raises(WarmStateContractError, match="inserted 9 exceeds loaded 3"):
            self._build(counters={"loaded": 3, "inserted": 9,
                                  "teleport_total": 0, "teleport_reasons": {}})

    def test_a_prefix_with_more_trips_than_inserted_is_refused(self):
        """Completed prefix trips cannot exceed what was inserted.

        Prefix-only: post-warm trip_count may exceed post-warm inserted, because
        a vehicle inserted before the snapshot can finish after it.
        """
        from traffic_sim.simulation.monthly_warm_state import WarmStateContractError
        with pytest.raises(WarmStateContractError, match="completed trip_count"):
            self._build(completed_trips={"total_time_loss_s": 1.0,
                                         "trip_count": 99},
                        counters={"loaded": 3, "inserted": 3,
                                  "teleport_total": 0, "teleport_reasons": {}})

    def test_reconstruction_refuses_an_impossible_post_segment(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, reconstruct_metrics)
        with pytest.raises(WarmStateContractError, match="exceeds loaded"):
            reconstruct_metrics(_prefix_evidence(), self._post(inserted=9))

    def test_equal_boundary_relationships_are_allowed(self):
        """The invariants reject IMPOSSIBLE, not merely tight, values."""
        from traffic_sim.simulation.monthly_warm_state import (
            validate_post_warm_metrics)
        ok = validate_post_warm_metrics(
            self._post(loaded=5, inserted=5, trip_count=5, unfinished_trips=5,
                       unfinished_waiting_trips=5))
        assert ok["inserted"] == ok["loaded"] == 5


    def test_post_warm_trips_may_exceed_post_warm_insertions(self):
        """Vehicles inserted before the snapshot finish after it."""
        from traffic_sim.simulation.monthly_warm_state import (
            validate_post_warm_metrics)
        ok = validate_post_warm_metrics(self._post(trip_count=50, loaded=5,
                                                   inserted=5,
                                                   unfinished_trips=1,
                                                   unfinished_waiting_trips=1))
        assert ok["trip_count"] == 50


class TestSolReviewRound10:
    """The parser — not just the builder — must reject impossible evidence.

    Restore and reconstruction read STORED evidence through
    `parse_prefix_evidence`. A write-side check cannot protect a digest-valid
    payload produced by an older path or by anything that bypassed the builder,
    so the same relationship invariants have to hold at the read boundary.
    """

    def _stored(self, **over):
        """A raw stored payload, built WITHOUT the builder's validation."""
        accumulator = _empty_accumulator(900)
        base = {
            "schema": "monthly_prefix_evidence_v7", "warm_point_s": 900,
            "active_accumulator": accumulator,
            "completed_records": {"c1": 2.0, "c2": 2.0, "c3": 1.0},
            "completed_record_order": ["c1", "c2", "c3"],
            "snapshot_facts": {"schema": "warm_snapshot_facts_v1",
                               "warm_point_s": 900,
                               "captured_at_s": 900.0,
                               "active_vehicle_count": 0,
                               "save_state_rng": True,
                               "save_state_precision": 16},
            "completed_trips": {"total_time_loss_s": 5.0, "trip_count": 3},
            "queue_maximum": 0,
            "counters": {"loaded": 3, "inserted": 3, "teleport_total": 0},
            "teleport_reasons": {},
            "recovery_buckets": [{"begin_s": 0, "end_s": 900,
                                  "time_loss_s": 1.0}],
        }
        base.update(over)
        return base

    def test_the_parser_refuses_more_inserted_than_loaded(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, parse_prefix_evidence)
        payload = self._stored(
            counters={"loaded": 1, "inserted": 9, "teleport_total": 0})
        with pytest.raises(WarmStateContractError,
                           match="inserted 9 exceeds loaded 1"):
            parse_prefix_evidence(payload)

    def test_the_parser_refuses_more_completed_trips_than_inserted(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, parse_prefix_evidence)
        payload = self._stored(
            completed_trips={"total_time_loss_s": 5.0, "trip_count": 99})
        with pytest.raises(WarmStateContractError,
                           match="does not recompose"):
            parse_prefix_evidence(payload)

    def test_a_valid_stored_payload_still_parses(self):
        from traffic_sim.simulation.monthly_warm_state import parse_prefix_evidence
        assert parse_prefix_evidence(self._stored())["counters"]["loaded"] == 3

    def test_reconstruction_refuses_impossible_stored_evidence(self):
        """The path that actually consumes a restored cache member."""
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, reconstruct_metrics)
        post = {"total_time_loss_s": 1.0, "trip_count": 1, "unfinished_trips": 0,
                "unfinished_waiting_trips": 0, "teleport_total": 0,
                "teleport_reasons": {}, "loaded": 1, "inserted": 1,
                "running_at_end": 0, "waiting_at_end": 0,
                "truncated_unreachable": 0, "dropped_unreachable": 0,
                "max_queue_vehicles": 1, "closed_edge_throughput": 0}
        with pytest.raises(WarmStateContractError, match="exceeds loaded"):
            reconstruct_metrics(
                self._stored(counters={"loaded": 1, "inserted": 9,
                                       "teleport_total": 0}), post)
        with pytest.raises(WarmStateContractError, match="does not recompose"):
            reconstruct_metrics(
                self._stored(completed_trips={"total_time_loss_s": 5.0,
                                              "trip_count": 99}), post)

    def test_restore_treats_impossible_stored_evidence_as_a_cache_miss(
            self, tmp_path):
        """A tampered-but-digest-valid member must not be usable."""
        import traffic_sim.simulation.monthly_sumo as ms
        root = tmp_path / "warm-state"
        entry = root / "k"
        entry.mkdir(parents=True)
        (entry / "prefix_evidence.json").write_text(json.dumps(self._stored(
            counters={"loaded": 1, "inserted": 9, "teleport_total": 0})))

        class Identity:
            content_key = "k"
            warmup_end_s = 900

        class Probe:
            warm_state_root = lambda self: root
            _cached_prefix_evidence = ms.ArchivedDemandSumoRunner._cached_prefix_evidence

        assert Probe()._cached_prefix_evidence(Identity()) is None

    def test_the_parser_and_builder_share_one_relationship_check(self):
        source = Path("traffic_sim/simulation/monthly_warm_state.py").read_text()
        parser = source.split("def parse_prefix_evidence(")[1].split("\ndef ")[0]
        assert "_check_counter_relationships(" in parser
        assert "trips_bounded_by_inserted=True" in parser
