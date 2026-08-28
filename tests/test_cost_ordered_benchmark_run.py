"""Stage 2: `--run` must actually run both arms and compare them.

Before this, `--run` printed instructions and exited — so the registration's
gates had never been applied to anything. These tests drive the real
orchestration end to end with the same in-memory arms the execution tests use,
which is what makes them runnable without the calibrated archive library while
still exercising the comparison, the gates, the stop-proof re-derivation, the
fault-injection probe and the outcome writer.

The one thing they deliberately do NOT fake is judgement: `compare_arms`,
`_stop_proof_valid`, `_gate_results` and `build_outcome` are the production
functions throughout.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import tools.cost_ordered_benchmark as bench
import tools.product_arm as pa
from tests.test_cost_ordered_execution import (
    FakeRunner,
    _ordered_prices,
    _policy,
    _screen_builder,
    _spec,
    FakeCostSource,
)
from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.simulation.finalist_decision import (
    RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
    TIMEOUT_IDENTITY_SCHEMA,
)

from tests._cost_ordered_benchmark_test_support import (
    run_registered_unisolated_for_tests as _run_registered_unisolated_for_tests,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_real_daily_results_cache_roots(tmp_path):
    """`run_benchmark` resolves `daily_cost_cache` against the real repo ROOT.

    `_registration` gives each test its own unique basename (keyed off
    `tmp_path.name`) so tests cannot collide with EACH OTHER, but the
    directories it and `_isolated_daily_results_cache_root` create still
    land on the real filesystem, outside `tmp_path` — pytest never cleans
    those up. Remove this test's own roots afterwards so a real developer
    checkout does not accumulate one throwaway directory per test run.
    """
    yield
    import shutil

    for path in (ROOT / "runs").glob(f"bench-daily-costs-{tmp_path.name}-*"):
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Point `product_arm` at the in-memory arms, keeping everything else real.

    `build_arm` is the only seam replaced. `run_arm` — the timing, the RSS
    accounting, the cleanup — and every comparison function stay production.
    """
    spec = _spec()
    _schedules, prices = _ordered_prices(spec)
    ids = [item.schedule_id for item in generate_closure_schedules(spec)]
    runners: dict[str, FakeRunner] = {}

    def fake_build_arm(spec_arg, *, cost_ordered, objective_method, **kwargs):
        runner = FakeRunner(prices=prices)
        runners[kwargs.get("study_provenance_key", "")] = runner
        source = FakeCostSource(prices) if cost_ordered else None
        return runner, _screen_builder(spec_arg, ids), source

    monkeypatch.setattr(pa, "build_arm", fake_build_arm)
    monkeypatch.setattr(bench.pa, "build_arm", fake_build_arm)
    return {"spec": spec, "ids": ids, "prices": prices, "runners": runners}


def _registration(tmp_path, spec, *, runs_root=None, outcome=None):
    """A registration bound only to inputs that really exist in this tree.

    `outcome` is threaded through because a registration now names the outcome
    the caller asked for, and a run must write exactly that file.
    """
    network_dir = tmp_path / "sumo"
    network_dir.mkdir(exist_ok=True)
    (network_dir / "net.net.xml").write_text("<net/>", encoding="utf-8")
    (network_dir / "network_metadata.json").write_text(
        "{}", encoding="utf-8")
    record = bench.build_registration(
        runs_root or tmp_path, data_root=tmp_path,
        outcome_path=outcome or (tmp_path / "outcome.json"))
    record["selected_case"] = {
        "search_id": spec.search_id,
        "search_content_key": spec.content_key,
        "spec": spec.to_dict(),
        "candidate_count": 45,
        "unique_daily_unit_count": 10,
        "work_dates": [],
        "work_dates_with_calibrated_archive": [],
    }
    record["status"] = "frozen_before_outcome"
    record.pop("blocked_by", None)
    record["archives"] = {}
    # `run_benchmark` resolves `daily_cost_cache` against the module-level
    # `ROOT` (the real repo root), not `data_root`/`tmp_path` — so a FIXED
    # name here would make every test in this file (and every past/future
    # pytest run) collide on the SAME real on-disk daily-results cache root.
    # `_isolated_daily_results_cache_root` now refuses a pre-existing
    # destination outright (see review-02 finding 4), which correctly turns
    # that old silent cross-test contamination into a hard failure — so the
    # fixture must give every test its own real, unique path instead of
    # relying on tolerance that no longer exists. `tmp_path.name` is unique
    # per test invocation, which is exactly the scoping needed here.
    record["output_roots"] = {
        "exhaustive": "runs/bench-exhaustive",
        "cost_ordered": "runs/bench-cost-ordered",
        "daily_cost_cache": f"runs/bench-daily-costs-{tmp_path.name}",
    }
    body = {key: value for key, value in record.items()
            if key not in {"content_key", "registered_at"}}
    record["content_key"] = bench._content_key(body)
    return record


class TestBindingsAreCheckedBeforeAnythingRuns:
    def test_an_intact_registration_reports_no_drift(self, tmp_path):
        record = _registration(tmp_path, _spec())
        assert bench.verify_bindings(record, tmp_path) == []

    def test_a_moved_source_is_named(self, tmp_path):
        record = _registration(tmp_path, _spec())
        record["sources"]["traffic_sim/simulation/monthly_search.py"] = "0" * 64
        drift = bench.verify_bindings(record, tmp_path)
        assert any("monthly_search.py" in item for item in drift)

    def test_a_moved_policy_is_named(self, tmp_path):
        record = _registration(tmp_path, _spec())
        record["policies"]["cost_ordered"]["sha256"] = "0" * 64
        assert any("policy changed" in item
                   for item in bench.verify_bindings(record, tmp_path))

    def test_a_missing_bound_route_is_named(self, tmp_path):
        record = _registration(tmp_path, _spec())
        record["archives"] = {"k": {
            "archive": str(tmp_path / "nope"),
            "demand_meta_sha256": "0" * 64,
            "routes": {"q50": {"path": str(tmp_path / "nope" / "a.rou.xml"),
                               "sha256": "0" * 64}}}}
        drift = bench.verify_bindings(record, tmp_path)
        assert any("bound route is missing" in item for item in drift)
        assert any("demand metadata is missing" in item for item in drift)

    def test_a_tampered_registration_is_named(self, tmp_path):
        record = _registration(tmp_path, _spec())
        record["hypothesis"] = "something else entirely"
        assert any("content key does not describe it" in item
                   for item in bench.verify_bindings(record, tmp_path))

    def test_run_refuses_drifted_bindings(self, tmp_path, wired):
        record = _registration(tmp_path, wired["spec"])
        record["sources"]["run_scenario.py"] = "0" * 64
        path = tmp_path / "registration.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(SystemExit, match="no longer describe this tree"):
            bench.main(["--run", "--registration", str(path),
                        "--runs-root", str(tmp_path),
                        "--workspace-root", str(tmp_path / "ws"),
                        "--out", str(tmp_path / "outcome.json")])


class TestBothArmsActuallyRun:
    def _run(self, tmp_path, wired, **kwargs):
        record = _registration(tmp_path, wired["spec"])
        # isolate_arms=False: the `wired` fixture fakes SUMO via an
        # in-process build_arm monkeypatch, which a real isolated
        # subprocess would never see. Isolation itself is covered by
        # tests/test_product_arm.py.
        kwargs.setdefault("isolate_arms", False)
        return bench.run_benchmark(
            record, runs_root=tmp_path,
            release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws", data_root=tmp_path, **kwargs)

    def test_the_cost_ordered_arm_simulates_strictly_fewer(self, tmp_path,
                                                            wired):
        executed = self._run(tmp_path, wired, fault_injection=False)
        comparison = executed["comparison"]
        assert comparison["cost_ordered_sumo_candidates"] < (
            comparison["exhaustive_sumo_candidates"])
        assert comparison["sumo_verifications_saved"] > 0

    def test_the_registered_data_root_controls_cwd_and_the_shared_lock(
            self, tmp_path, wired, monkeypatch):
        from traffic_sim.simulation import workspace as workspace_module

        paths = []
        original = workspace_module.WorkspaceLock

        class RecordingLock(original):
            def __init__(self, owner, **kwargs):
                super().__init__(owner, **kwargs)
                paths.append(self.path)

        monkeypatch.setattr(workspace_module, "WorkspaceLock", RecordingLock)
        before = Path.cwd()
        self._run(tmp_path, wired, fault_injection=False)
        assert Path.cwd() == before
        assert paths == [tmp_path / "runs" / ".demand-workspace.lock"]

    def test_the_two_arms_agree_on_everything_that_matters(self, tmp_path,
                                                            wired):
        comparison = self._run(tmp_path, wired,
                               fault_injection=False)["comparison"]
        assert comparison["candidate_costs_field_identical"] is True
        assert comparison["hard_failures_identical"] is True
        assert comparison["health_classifications_identical"] is True
        assert comparison["status_identical"] is True
        assert comparison["selected_ids_identical"] is True
        assert comparison["final_decision_identical"] is True
        assert comparison["timeout_outcomes_identical"] is True
        assert comparison["ledger_population_complete"] is True

    def test_both_arms_run_under_one_semantic_study_provenance_key(
            self, tmp_path, wired):
        """The arm label must live outside semantic evidence.

        `PairedObservation.provenance_key` is stamped from
        `study_provenance_key` and travels inside cached, comparable
        evidence — so if the two arms were built with different values (as
        this benchmark used to, via an f-string suffixed with the arm name),
        their evidence would differ by a label baked into content even when
        the underlying simulation was identical. Both arms must share the
        one frozen `bench.BENCHMARK_STUDY_PROVENANCE_KEY`; which arm ran is
        an orchestration fact carried elsewhere (`run_arm`'s own `"arm"`
        key), never inside the evidence.
        """
        self._run(tmp_path, wired, fault_injection=False)
        seen_keys = {
            key for key in wired["runners"]
            if key  # the fixture's dict is also seeded by other fixtures
        }
        assert seen_keys == {bench.BENCHMARK_STUDY_PROVENANCE_KEY}

    def test_the_cost_gate_covers_every_candidate_not_just_the_simulated_few(
            self, tmp_path, wired):
        """Otherwise the field-by-field gate is green by vacuity.

        The cost-ordered arm has pilot statistics only for the candidates it
        simulated — two of forty-five. Comparing those alone would check the
        two the arms trivially agree on. The ledger priced all of them before
        any SUMO ran, and that is what the exhaustive arm must reproduce.
        """
        comparison = self._run(tmp_path, wired,
                               fault_injection=False)["comparison"]
        assert comparison["ledger_compared_candidate_count"] == len(
            wired["ids"])
        assert comparison["ledger_compared_candidate_count"] > (
            comparison["compared_candidate_count"] * 4)
        assert comparison["ledger_costs_field_identical"] is True

    def test_a_ledger_price_the_exhaustive_arm_contradicts_fails_the_gate(
            self, tmp_path, wired, monkeypatch):
        executed = self._run(tmp_path, wired, fault_injection=False)
        real = bench._ledger_costs

        def poisoned(arm):
            costs = dict(real(arm))
            first = sorted(costs)[0]
            costs[first] = dict(costs[first])
            costs[first]["added_vehicle_hours"] = -12345.0
            return costs

        monkeypatch.setattr(bench, "_ledger_costs", poisoned)
        comparison = bench.compare_arms(executed["arms"]["exhaustive"],
                                        executed["arms"]["cost_ordered"])
        assert comparison["ledger_costs_field_identical"] is False
        assert comparison["candidate_costs_field_identical"] is False
        assert bench._gate_results(comparison)["passed"] is False

    def test_a_timeout_only_one_arm_saw_fails_the_gate(self, tmp_path, wired,
                                                        monkeypatch):
        """A timeout_undecided is undecided evidence, not a benign diff.

        Two arms disagreeing about whether a candidate hit the frozen SUMO
        timeout means they cannot agree the candidate was ever DECIDED —
        that must never be silently dropped from the comparison, matching
        `finalist_decision.CandidateEvidence.timeout_undecided`'s contract.
        """
        executed = self._run(tmp_path, wired, fault_injection=False)
        real = bench._candidate_costs

        def poisoned(arm_or_result):
            costs = {key: dict(value)
                     for key, value in real(arm_or_result).items()}
            if arm_or_result is executed["arms"]["cost_ordered"] and costs:
                first = sorted(costs)[0]
                costs[first]["timeout_undecided"] = [
                    "q50:1000:attempt1:threshold300s"]
            return costs

        monkeypatch.setattr(bench, "_candidate_costs", poisoned)
        comparison = bench.compare_arms(executed["arms"]["exhaustive"],
                                        executed["arms"]["cost_ordered"])
        assert comparison["timeout_outcomes_identical"] is False
        assert comparison["timeout_outcome_mismatches"]
        assert bench._gate_results(comparison)["passed"] is False

    def test_a_ledger_missing_a_simulated_candidate_fails_completeness(
            self, tmp_path, wired, monkeypatch):
        """The ledger prices every candidate BEFORE any SUMO run, so its
        population must equal the exhaustive arm's — a candidate the ledger
        silently never priced is exactly the gap an intersection-only
        comparison would hide.
        """
        executed = self._run(tmp_path, wired, fault_injection=False)
        real = bench._ledger_costs

        def truncated(arm):
            costs = dict(real(arm))
            first = sorted(costs)[0]
            del costs[first]
            return costs

        monkeypatch.setattr(bench, "_ledger_costs", truncated)
        comparison = bench.compare_arms(executed["arms"]["exhaustive"],
                                        executed["arms"]["cost_ordered"])
        assert comparison["ledger_population_complete"] is False
        assert comparison["left_only_vs_ledger_candidates"]
        assert bench._gate_results(comparison)["passed"] is False

    def test_each_arm_gets_its_own_workspace(self, tmp_path, wired):
        self._run(tmp_path, wired, fault_injection=False)
        roots = sorted(item.name for item in (tmp_path / "ws").iterdir())
        assert "bench-exhaustive" in roots and "bench-cost-ordered" in roots, (
            "one shared root would let the second arm resume the first's "
            "evidence and compare the run with itself")

    def test_the_stop_proof_is_rederived_not_trusted(self, tmp_path, wired):
        comparison = self._run(tmp_path, wired,
                               fault_injection=False)["comparison"]
        check = comparison["stop_proof_check"]
        assert comparison["stop_proof_valid"] is True
        assert check["stop_reason"] in {"band_exhausted",
                                        "search_space_exhausted"}
        if check["stop_reason"] == "band_exhausted":
            assert (check["first_unexamined_added_vehicle_hours"]
                    > check["selection_band_added_vehicle_hours"])

    def test_resources_and_cursor_are_recorded(self, tmp_path, wired):
        comparison = self._run(tmp_path, wired,
                               fault_injection=False)["comparison"]
        assert comparison["verified_prefix"]
        assert comparison["cursor"]["cursor"] == len(
            comparison["verified_prefix"])
        assert comparison["wall_time_s"]["cost_ordered"] >= 0
        assert comparison["peak_rss_bytes"]["cost_ordered"] > 0
        assert comparison["no_resource_cap_regression"] is True
        assert comparison["resource_caps"]["maximum_daily_units"] == 10_000


class TestTheStopProofCheckCanFail:
    """The re-derivation must be able to say no, or it is decoration."""

    def test_a_candidate_inside_the_band_invalidates_the_proof(self):
        check = bench._stop_proof_valid({
            "cost_ordered_sumo_candidates": 2,
            "stop_proof": {
                "stop_reason": "band_exhausted",
                "selection_band_added_vehicle_hours": 10.0,
                "first_unexamined_added_vehicle_hours": 10.0,
            }})
        assert check["valid"] is False
        assert "INSIDE the band" in check["reason"]

    def test_claimed_exhaustion_with_leftovers_is_refused(self):
        check = bench._stop_proof_valid({
            "cost_ordered_sumo_candidates": 2,
            "stop_proof": {"stop_reason": "search_space_exhausted",
                           "examined": 2, "total_ordered": 9, "unexamined": 7}})
        assert check["valid"] is False

    def test_a_missing_execution_record_is_not_a_valid_proof(self):
        assert bench._stop_proof_valid(None)["valid"] is False
        assert bench._stop_proof_valid({"stop_proof": {}})["valid"] is False


class TestTheStopProofIsIndependentlyRecomputed:
    """Every bound field is checked against ANOTHER published artifact.

    `_stop_proof_valid(execution, arm=...)` must not merely re-trust the
    proof's own self-reported numbers — a proof edited in isolation is
    exactly the failure mode a proof exists to rule out. Each test here
    tampers with exactly one bound field on a REAL published proof and
    checks it is caught against the cursor/ledger/candidate-evidence it did
    not come from.
    """

    def _run(self, tmp_path, wired, **kwargs):
        record = _registration(tmp_path, wired["spec"])
        kwargs.setdefault("isolate_arms", False)
        return bench.run_benchmark(
            record, runs_root=tmp_path,
            release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws", data_root=tmp_path, **kwargs)

    def _cost_ordered_arm_and_execution(self, tmp_path, wired):
        executed = self._run(tmp_path, wired, fault_injection=False)
        arm = executed["arms"]["cost_ordered"]
        execution = arm["result"]["cost_ordered_execution"]
        return arm, execution

    def test_a_genuine_proof_passes_the_independent_recomputation(
            self, tmp_path, wired):
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        check = bench._stop_proof_valid(execution, arm=arm)
        assert check["valid"] is True
        assert check["independent_recomputation"]["valid"] is True
        assert check["independent_recomputation"]["problems"] == []

    def test_every_proof_field_is_recomputed_after_execution_is_resealed(
            self, tmp_path, wired):
        """A matching self-digest must not make any forged proof field valid."""
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        proof = execution["stop_proof"]
        mutations = {
            "schema": "forged-schema",
            "stop_reason": (
                "search_space_exhausted"
                if proof["stop_reason"] != "search_space_exhausted"
                else "band_exhausted"),
            "minimum_finalists": int(proof["minimum_finalists"]) + 1,
            "practical_equivalence_vehicle_hours": (
                float(proof["practical_equivalence_vehicle_hours"]) + 1.0),
            "cutoff_added_vehicle_hours": (
                0.0 if proof["cutoff_added_vehicle_hours"] is None
                else float(proof["cutoff_added_vehicle_hours"]) + 1.0),
            "selection_band_added_vehicle_hours": (
                0.0 if proof["selection_band_added_vehicle_hours"] is None
                else float(proof["selection_band_added_vehicle_hours"]) + 1.0),
            "examined": int(proof["examined"]) + 1,
            "total_ordered": int(proof["total_ordered"]) + 1,
            "unexamined": int(proof["unexamined"]) + 1,
            "first_unexamined_candidate_id": "forged-candidate",
            "first_unexamined_added_vehicle_hours": (
                0.0 if proof["first_unexamined_added_vehicle_hours"] is None
                else float(proof["first_unexamined_added_vehicle_hours"]) + 1.0),
            "identity_key": "forged-identity",
            "disable_early_stop": not proof["disable_early_stop"],
            "undecided_candidate_ids": ["forged-candidate"],
            "verified_prefix_digest": "forged-prefix-digest",
            "evidence_digest": "forged-evidence-digest",
            "argument": "forged argument",
        }
        for field, forged_value in mutations.items():
            tampered = copy.deepcopy(execution)
            tampered["stop_proof"][field] = forged_value
            body = {key: value for key, value in tampered.items()
                    if key != "content_key"}
            tampered["content_key"] = bench._content_key(body)
            check = bench._stop_proof_valid(tampered, arm=arm)
            assert check["valid"] is False, field
            problems = check["independent_recomputation"]["problems"]
            assert any(field in problem for problem in problems), (
                field, problems)

    @pytest.mark.parametrize("location", ["proof", "cursor", "execution"])
    def test_stop_reason_and_execution_mode_tampering_fail_after_reseal(
            self, tmp_path, wired, location):
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        tampered = copy.deepcopy(execution)
        if location == "proof":
            current = tampered["stop_proof"]["stop_reason"]
            tampered["stop_proof"]["stop_reason"] = (
                "band_exhausted" if current == "search_space_exhausted"
                else "search_space_exhausted")
        elif location == "cursor":
            current = tampered["cursor"]["stop_reason"]
            tampered["cursor"]["stop_reason"] = (
                "band_exhausted" if current == "search_space_exhausted"
                else "search_space_exhausted")
        else:
            tampered["disable_early_stop"] = not tampered["disable_early_stop"]
        body = {key: value for key, value in tampered.items()
                if key != "content_key"}
        tampered["content_key"] = bench._content_key(body)
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        problems = check["independent_recomputation"]["problems"]
        expected = "stop_reason" if location != "execution" else "disable_early_stop"
        assert any(expected in problem for problem in problems), problems

    def test_a_tampered_verified_prefix_digest_is_caught(
            self, tmp_path, wired):
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        tampered = copy.deepcopy(execution)
        tampered["stop_proof"]["verified_prefix_digest"] = "not-the-real-digest"
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        assert any("verified_prefix_digest" in problem
                   for problem in check["independent_recomputation"]["problems"])

    def test_a_tampered_evidence_digest_is_caught(self, tmp_path, wired):
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        tampered = copy.deepcopy(execution)
        tampered["stop_proof"]["evidence_digest"] = "not-the-real-digest"
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        assert any("evidence_digest" in problem
                   for problem in check["independent_recomputation"]["problems"])

    @pytest.mark.parametrize("evidence_class", [
        "observation", "disruption", "hard_failure", "timeout", "provenance",
    ])
    def test_full_published_decision_evidence_tampering_is_caught(
            self, tmp_path, wired, evidence_class):
        """Reseal the artifact ledger so only the stop-proof binding catches it."""
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        workspace = Path(arm["workspace"])
        manifest_path = workspace / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        verified = execution["cursor"]["verified"]
        record = next(
            item for item in manifest["artifacts"]
            if item.get("kind") == "monthly_pilot_candidate"
            and item.get("provenance", {}).get("candidate_id") in verified)
        path = workspace / record["path"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        if evidence_class == "observation":
            payload["observations"][0]["baseline_time_loss_s"] += 1.0
        elif evidence_class == "disruption":
            payload["disruption"][0]["added_vehicle_hours"] += 1.0
        elif evidence_class == "hard_failure":
            payload["hard_failures"].append("tampered_failure")
        elif evidence_class == "timeout":
            payload["timeout_undecided"].append({
                "schema": TIMEOUT_IDENTITY_SCHEMA,
                "candidate_id": payload["candidate_id"],
                "work_date": "2027-01-01",
                "search_content_key": arm["search_content_key"],
                "variant": "q50", "seed": 1000, "attempt": 1,
                "threshold_s": 300.0,
                "retry_protocol": (
                    RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD),
                "search_provenance_key": "benchmark-shared-semantic-v1",
            })
        else:
            payload["observations"][0]["provenance_key"] = "forged"
        raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        path.write_bytes(raw)
        record["sha256"] = hashlib.sha256(raw).hexdigest()
        record["bytes"] = len(raw)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

        check = bench._stop_proof_valid(execution, arm=arm)
        assert check["valid"] is False
        assert any("evidence_digest" in problem
                   for problem in check["independent_recomputation"]["problems"])

    def test_a_dropped_undecided_candidate_is_caught(self, tmp_path, wired):
        """cost-order v5's exact failure mode: a timeout missing from the proof."""
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        verified = execution["cursor"]["verified"]
        assert verified, "fixture must verify at least one candidate"
        tampered = copy.deepcopy(execution)
        # Claim a candidate the real evidence never marked undecided.
        tampered["stop_proof"]["undecided_candidate_ids"] = [verified[0]]
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        assert any(
            "undecided_candidate_ids" in problem
            for problem in check["independent_recomputation"]["problems"])

    def test_a_tampered_first_unexamined_cost_is_caught_against_the_ledger(
            self, tmp_path, wired):
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        if execution["stop_proof"]["stop_reason"] != "band_exhausted":
            pytest.skip("fixture did not produce a band stop")
        tampered = copy.deepcopy(execution)
        tampered["stop_proof"]["first_unexamined_added_vehicle_hours"] = (
            float(tampered["stop_proof"]
                 ["first_unexamined_added_vehicle_hours"]) + 999.0)
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        assert any(
            "first_unexamined_added_vehicle_hours" in problem
            for problem in check["independent_recomputation"]["problems"])

    def test_without_an_arm_the_independent_checks_are_skipped(
            self, tmp_path, wired):
        """Backward-compatible: existing callers passing no arm are unaffected."""
        _arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        check = bench._stop_proof_valid(execution)
        assert "independent_recomputation" not in check

    def test_a_tampered_execution_content_key_is_caught(self, tmp_path, wired):
        """`stop_proof`/`cursor` edited without the record's own digest following."""
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        tampered = copy.deepcopy(execution)
        tampered["cursor"]["cutoff"] = (
            (tampered["cursor"].get("cutoff") or 0.0) + 999.0)
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        assert any(
            "content_key" in problem
            for problem in check["independent_recomputation"]["problems"])

    def test_a_missing_undecided_candidate_ids_key_is_caught(
            self, tmp_path, wired):
        """An ABSENT binding must fail exactly like a wrong one, not pass by default."""
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        tampered = copy.deepcopy(execution)
        del tampered["stop_proof"]["undecided_candidate_ids"]
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        assert any(
            "missing undecided_candidate_ids" in problem
            for problem in check["independent_recomputation"]["problems"])

    def test_a_missing_identity_key_is_caught(self, tmp_path, wired):
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        tampered = copy.deepcopy(execution)
        del tampered["stop_proof"]["identity_key"]
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        assert any(
            "missing identity_key" in problem
            for problem in check["independent_recomputation"]["problems"])

    def test_a_tampered_identity_key_is_caught(self, tmp_path, wired):
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        tampered = copy.deepcopy(execution)
        tampered["stop_proof"]["identity_key"] = "not-the-real-identity-key"
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        assert any(
            "identity_key does not match" in problem
            for problem in check["independent_recomputation"]["problems"])
        assert (check["independent_recomputation"]["recomputed_identity_key"]
                != "not-the-real-identity-key")

    def test_a_tampered_provider_identity_is_caught(self, tmp_path, wired):
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        tampered = copy.deepcopy(execution)
        tampered["provider_identity"] = {"schema": "forged"}
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        assert any(
            "provider_identity" in problem
            for problem in check["independent_recomputation"]["problems"])

    def test_a_viable_eligible_mismatch_is_caught(self, tmp_path, wired):
        """The digest's own reduction of the evidence must be faithful, not just consistent."""
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        verified = execution["cursor"]["verified"]
        assert verified, "fixture must verify at least one candidate"
        tampered = copy.deepcopy(execution)
        target = verified[0]
        viable = set(tampered["cursor"]["viable"])
        if target in viable:
            viable.discard(target)
        else:
            viable.add(target)
        tampered["cursor"]["viable"] = sorted(viable)
        check = bench._stop_proof_valid(tampered, arm=arm)
        assert check["valid"] is False
        assert any(
            "does not match its published eligible" in problem
            for problem in check["independent_recomputation"]["problems"])

    def test_no_published_ledger_or_policy_fails_closed(self, tmp_path, wired):
        """Without the artifacts identity_key is recomputed from, the check refuses."""
        arm, execution = self._cost_ordered_arm_and_execution(tmp_path, wired)
        crippled = dict(arm)
        crippled["workspace"] = str(tmp_path / "does-not-exist")
        check = bench._stop_proof_valid(execution, arm=crippled)
        assert check["valid"] is False
        problems = check["independent_recomputation"]["problems"]
        assert any("cost ledger" in problem for problem in problems)
        assert any("pilot policy" in problem for problem in problems)


class TestFaultInjection:
    def test_an_interrupted_run_resumes_to_the_same_outcome(self, tmp_path,
                                                            wired):
        record = _registration(tmp_path, wired["spec"])
        executed = bench.run_benchmark(
            record, runs_root=tmp_path, release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws", data_root=tmp_path,
            fault_injection=True, isolate_arms=False)
        restart = executed["comparison"]["restart"]
        assert restart["performed"] is True
        assert restart["interrupted_after_pilots"] >= 1
        assert executed["comparison"]["restart_equivalent"] is True, restart

    def test_the_restart_probe_reuses_its_own_isolated_cache_root(
            self, tmp_path, wired, monkeypatch):
        """The probe's interrupted attempt and its resume share ONE root,

        distinct from both main arms' — never the shared unisolated source,
        and never a second independent call into
        `_isolated_daily_results_cache_root` for an already-created
        destination (see review-02 finding 4).
        """
        seen_roots: list[Path | None] = []
        real_build_arm = pa.build_arm
        real_run_arm = pa.run_arm

        def recording_build_arm(*args, **kwargs):
            if kwargs.get("study_provenance_key") == (
                    "cost-ordered-benchmark-restart"):
                seen_roots.append(kwargs.get("daily_results_cache_root"))
            return real_build_arm(*args, **kwargs)

        def recording_run_arm(*args, **kwargs):
            if kwargs.get("study_provenance_key") == (
                    "cost-ordered-benchmark-restart"):
                seen_roots.append(kwargs.get("daily_results_cache_root"))
            return real_run_arm(*args, **kwargs)

        monkeypatch.setattr(bench.pa, "build_arm", recording_build_arm)
        monkeypatch.setattr(bench.pa, "run_arm", recording_run_arm)
        record = _registration(tmp_path, wired["spec"])
        executed = bench.run_benchmark(
            record, runs_root=tmp_path, release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws", data_root=tmp_path,
            fault_injection=True, isolate_arms=False)
        assert executed["comparison"]["restart"]["performed"] is True
        # `pa.run_arm` calls `pa.build_arm` internally, so the resumed
        # attempt is observed twice (once via each wrapper) in addition to
        # the interrupted attempt — three observations, all the SAME root.
        assert len(seen_roots) == 3
        assert seen_roots[0] is not None
        assert len(set(map(str, seen_roots))) == 1
        main_roots = {
            arm["daily_results_cache_root"]
            for arm in executed["arms"].values()
        }
        assert str(seen_roots[0]) not in main_roots
        assert "restart_probe" in str(seen_roots[0])

    def test_skipping_the_probe_fails_the_restart_gate(self, tmp_path, wired):
        record = _registration(tmp_path, wired["spec"])
        executed = bench.run_benchmark(
            record, runs_root=tmp_path, release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws", data_root=tmp_path,
            fault_injection=False, isolate_arms=False)
        comparison = executed["comparison"]
        assert comparison["restart_equivalent"] is False
        assert bench._gate_results(comparison)["checks"][
            "restart_equivalent"] is False, (
            "an unexercised restart must not pass by absence")


class TestTheOutcome:
    def test_an_execution_failure_is_published_as_a_failed_outcome(
            self, tmp_path, wired, monkeypatch):
        record = _registration(tmp_path, wired["spec"])
        path = tmp_path / "registration.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        out = tmp_path / "outcome.json"

        def fail(*args, **kwargs):
            raise RuntimeError("sumo timed out after 300s (seed 1000)")

        monkeypatch.setattr(bench, "run_benchmark", fail)
        code = bench.main([
            "--run", "--registration", str(path),
            "--runs-root", str(tmp_path), "--data-root", str(tmp_path),
            "--workspace-root", str(tmp_path / "ws"), "--out", str(out),
        ])
        outcome = json.loads(out.read_text(encoding="utf-8"))
        assert code == 5
        assert outcome["status"] == "failed_execution"
        assert outcome["gates"]["passed"] is False
        assert outcome["comparison"]["execution_error"]["type"] == (
            "RuntimeError")
        assert "timed out after 300s" in outcome["comparison"][
            "execution_error"]["message"]
        assert outcome["release_evidence"] is False

    def test_a_full_pass_writes_an_outcome_and_still_activates_nothing(
            self, tmp_path, wired):
        record = _registration(tmp_path, wired["spec"])
        path = tmp_path / "registration.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        out = tmp_path / "outcome.json"
        # `wired` fakes SUMO via an in-process build_arm monkeypatch, which a
        # real isolated subprocess would not see. `bench._run_registered`
        # (main()'s own registration-loading/publication body) always
        # isolates and has no escape hatch, so this drives the test-only
        # twin instead of going through main()/the command line.
        code = _run_registered_unisolated_for_tests(
            registration_path=path,
            runs_root=tmp_path,
            data_root=tmp_path,
            release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws",
            out=out,
            overwrite=False,
            stdout=False,
            fault_injection=True,
            allow_drift=False,
        )
        outcome = json.loads(out.read_text(encoding="utf-8"))
        assert code == 0, outcome["gates"]["checks"]
        assert outcome["gates"]["passed"] is True
        assert outcome["release_evidence"] is False
        assert outcome["claim_boundary"]["activates_policy_v3"] is False, (
            "a passing benchmark alone must not activate the policy")
        assert outcome["claim_boundary"]["opens_global_best"] is False
        assert outcome["registration"]["content_key"] == record["content_key"]

    def test_the_registration_is_not_touched_by_a_run(self, tmp_path, wired):
        record = _registration(tmp_path, wired["spec"])
        path = tmp_path / "registration.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        before = path.read_bytes()
        _run_registered_unisolated_for_tests(
            registration_path=path,
            runs_root=tmp_path,
            data_root=tmp_path,
            release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws",
            out=tmp_path / "outcome.json",
            overwrite=False,
            stdout=False,
            fault_injection=True,
            allow_drift=False,
        )
        assert path.read_bytes() == before

    def test_an_outcome_is_not_silently_replaced(self, tmp_path, wired):
        record = _registration(tmp_path, wired["spec"])
        path = tmp_path / "registration.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        out = tmp_path / "outcome.json"
        kwargs = dict(
            registration_path=path,
            runs_root=tmp_path,
            data_root=tmp_path,
            release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws",
            out=out,
            overwrite=False,
            stdout=False,
            fault_injection=True,
            allow_drift=False,
        )
        _run_registered_unisolated_for_tests(**kwargs)
        with pytest.raises(SystemExit, match="already exists"):
            _run_registered_unisolated_for_tests(**kwargs)


class TestIsolationCannotBeDisabledFromTheCommandLine:
    """A registered/frozen run must always isolate arms.

    v5's timeout classification crossed between two arms sharing one
    process; the fix was `run_arm_isolated`, and a `--no-isolate-arms` CLI
    flag would just reopen the same hole from the command line. It must not
    exist as parseable argv, and `main()`'s own --run body always isolates.
    """

    def test_the_cli_flag_does_not_exist(self):
        with pytest.raises(SystemExit):
            bench.main(["--run", "--registration", "x.json",
                       "--no-isolate-arms"])

    def test_main_always_isolates(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run_registered(**kwargs):
            seen.update(kwargs)
            return 0

        monkeypatch.setattr(bench, "_run_registered", fake_run_registered)
        registration = tmp_path / "registration.json"
        registration.write_text("{}", encoding="utf-8")
        bench.main(["--run", "--registration", str(registration),
                   "--runs-root", str(tmp_path),
                   "--workspace-root", str(tmp_path / "ws"),
                   "--out", str(tmp_path / "outcome.json")])
        assert "isolate_arms" not in seen, (
            "main() must rely on _run_registered's isolate_arms=True "
            "default, never pass a value it could set to False")


class TestOrderedExhaustiveComparison:
    """The apples-to-apples reference: same ledger and order, no early stop."""

    def _run(self, tmp_path, wired, **kwargs):
        kwargs.setdefault("isolate_arms", False)
        return bench.run_ordered_exhaustive_comparison(
            wired["spec"], _policy(),
            runs_root=tmp_path,
            release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws",
            daily_cost_cache=tmp_path / "daily-costs",
            data_root=tmp_path,
            **kwargs,
        )

    def test_the_ordered_exhaustive_arm_simulates_strictly_more(
            self, tmp_path, wired):
        executed = self._run(tmp_path, wired)
        comparison = executed["comparison"]
        assert comparison["cost_ordered_sumo_candidates"] < (
            comparison["ordered_exhaustive_sumo_candidates"])
        assert comparison["sumo_verifications_saved"] > 0
        assert comparison["release_evidence"] is False

    def test_both_arms_reach_the_same_final_decision(self, tmp_path, wired):
        comparison = self._run(tmp_path, wired)["comparison"]
        assert comparison["final_decision_identical"] is True
        assert comparison["candidate_costs_field_identical"] is True

    def test_missing_expected_candidate_stage_fails_semantic_completeness(
            self, tmp_path, wired, monkeypatch):
        executed = self._run(tmp_path, wired)
        real = bench._candidate_semantic_evidence

        def missing_stage(arm):
            evidence = copy.deepcopy(real(arm))
            if arm is executed["arms"]["cost_ordered"]:
                candidate_id = next(iter(evidence))
                evidence[candidate_id].pop("pilot", None)
            return evidence

        monkeypatch.setattr(bench, "_candidate_semantic_evidence", missing_stage)
        comparison = bench.compare_ordered_exhaustive(
            executed["arms"]["cost_ordered"],
            executed["arms"]["ordered_exhaustive"])
        assert comparison["semantic_evidence_identical"] is False
        assert comparison["semantic_population_problems"]
        assert comparison["semantic_comparison_complete"] is False

    def test_canonical_observation_digest_difference_fails_semantic_gate(
            self, tmp_path, wired, monkeypatch):
        executed = self._run(tmp_path, wired)
        real = bench._candidate_semantic_evidence

        def with_digests(arm):
            evidence = copy.deepcopy(real(arm))
            for candidate_id, stages in evidence.items():
                for stage in stages.values():
                    stage["canonical_observation_digests"] = [{
                        "candidate_id": candidate_id,
                        "work_date": "2027-01-01",
                        "variant": observation["demand_variant"],
                        "seed": observation["seed"],
                        "sha256": "a" * 64,
                    } for observation in stage["observations"]]
            if arm is executed["arms"]["cost_ordered"]:
                first = next(
                    digest
                    for stages in evidence.values()
                    for stage in stages.values()
                    for digest in stage["canonical_observation_digests"]
                )
                first["sha256"] = "b" * 64
            return evidence

        monkeypatch.setattr(bench, "_candidate_semantic_evidence", with_digests)
        comparison = bench.compare_ordered_exhaustive(
            executed["arms"]["cost_ordered"],
            executed["arms"]["ordered_exhaustive"])
        assert comparison["semantic_evidence_identical"] is False
        assert comparison["semantic_evidence_mismatches"]

    def test_malformed_candidate_artifact_fails_closed(
            self, tmp_path, wired):
        executed = self._run(tmp_path, wired)
        arm = executed["arms"]["cost_ordered"]
        workspace = Path(arm["workspace"])
        manifest_path = workspace / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = next(item for item in manifest["artifacts"]
                      if item.get("kind") == "monthly_pilot_candidate")
        path = workspace / record["path"]
        raw = b"[]\n"
        path.write_bytes(raw)
        record["sha256"] = hashlib.sha256(raw).hexdigest()
        record["bytes"] = len(raw)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

        with pytest.raises(ValueError, match="artifact must be an object"):
            bench._candidate_semantic_evidence(arm)

    def test_cache_event_identities_not_just_counts_are_compared(
            self, tmp_path, wired):
        executed = self._run(tmp_path, wired)
        cost_ordered = copy.deepcopy(executed["arms"]["cost_ordered"])
        ordered_exhaustive = copy.deepcopy(
            executed["arms"]["ordered_exhaustive"])
        cost_ordered["daily_results_cache_event_records"] = [
            {"unit_id": "cost-only-unit", "event": "miss"}]
        ordered_exhaustive["daily_results_cache_event_records"] = [
            {"unit_id": "exhaustive-only-unit", "event": "miss"}]
        for arm in (cost_ordered, ordered_exhaustive):
            arm["daily_results_cache_events"] = {
                "cache_hits": 0, "cache_misses": 1,
                "cache_corrupt": 0, "cache_publications": 0}

        comparison = bench.compare_ordered_exhaustive(
            cost_ordered, ordered_exhaustive)
        assert comparison["daily_results_cache_events_valid"] is False
        assert comparison["daily_results_cache_event_problems"]
        assert comparison["semantic_comparison_complete"] is False

    def test_cache_event_aggregates_are_recomputed_from_identity_records(
            self, tmp_path, wired):
        executed = self._run(tmp_path, wired)
        cost_ordered = copy.deepcopy(executed["arms"]["cost_ordered"])
        for arm in (cost_ordered, executed["arms"]["ordered_exhaustive"]):
            arm["daily_results_cache_event_records"] = [
                {"unit_id": "unit-a", "event": "miss"}]
            arm["daily_results_cache_events"] = {
                "cache_hits": 0, "cache_misses": 1,
                "cache_corrupt": 0, "cache_publications": 0}
        cost_ordered["daily_results_cache_events"]["cache_misses"] = 99

        comparison = bench.compare_ordered_exhaustive(
            cost_ordered, executed["arms"]["ordered_exhaustive"])
        assert comparison["daily_results_cache_events_valid"] is False
        assert any("disagree" in problem for problem in comparison[
            "daily_results_cache_event_problems"])
        assert comparison["semantic_comparison_complete"] is False

    def test_malformed_exact_launch_field_fails_closed(
            self, tmp_path, wired):
        del tmp_path, wired
        record = {
            "candidate_id": "candidate-a", "work_date": "2027-01-01",
            "stage": "pilot", "variant": "q50", "seed": 1000,
            "attempt": 1, "timed_out": False, "outcome": "success"}
        telemetry = {
            "pilot": {"attempts": 1, "timeouts": 0, "other_outcomes": 1},
            "finalist": {"attempts": 0, "timeouts": 0, "other_outcomes": 0},
        }
        result = {"cost_ordered_execution": {
            "cursor": {"verified": ["candidate-a"]}}}
        cost_ordered = {
            "exact_launch_records": [copy.deepcopy(record)],
            "exact_launch_telemetry": copy.deepcopy(telemetry),
            "result": copy.deepcopy(result),
        }
        exhaustive = copy.deepcopy(cost_ordered)
        cost_ordered["exact_launch_records"][0]["timed_out"] = 1

        with pytest.raises(ValueError, match="timed_out must be boolean"):
            bench._exact_attempt_population_check(
                cost_ordered, exhaustive)

    def test_unexplained_retry_for_shared_candidate_fails_attempt_gate(
            self, tmp_path, wired):
        del tmp_path, wired
        shared_record = {
            "candidate_id": "candidate-a", "work_date": "2027-01-01",
            "stage": "pilot", "variant": "q50", "seed": 1000,
            "attempt": 1, "timed_out": False, "outcome": "success"}
        telemetry = {
            "pilot": {"attempts": 1, "timeouts": 0, "other_outcomes": 1},
            "finalist": {"attempts": 0, "timeouts": 0, "other_outcomes": 0},
        }
        result = {"cost_ordered_execution": {
            "cursor": {"verified": ["candidate-a"]}}}
        cost_ordered = {
            "exact_launch_records": [copy.deepcopy(shared_record)],
            "exact_launch_telemetry": copy.deepcopy(telemetry),
            "result": copy.deepcopy(result),
        }
        exhaustive = copy.deepcopy(cost_ordered)
        retry = copy.deepcopy(shared_record)
        retry["attempt"] = 2
        exhaustive["exact_launch_records"].append(retry)
        counters = exhaustive["exact_launch_telemetry"][retry["stage"]]
        counters["attempts"] += 1
        counters["timeouts" if retry["timed_out"]
                 else "other_outcomes"] += 1

        check = bench._exact_attempt_population_check(
            cost_ordered, exhaustive)
        assert check["valid"] is False
        assert check["unexplained_ordered_exhaustive_launches"]

    def test_resealed_extra_canonical_digest_fails_semantic_gate(
            self, tmp_path, wired):
        executed = self._run(tmp_path, wired)
        arm = executed["arms"]["cost_ordered"]
        workspace = Path(arm["workspace"])
        manifest_path = workspace / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for record in manifest["artifacts"]:
            if record.get("kind") != "monthly_pilot_candidate":
                continue
            path = workspace / record["path"]
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["canonical_observation_digests"].append({
                "candidate_id": "unassociated-daily-candidate",
                "work_date": "2099-01-01", "variant": "q50",
                "seed": 999999, "sha256": "a" * 64,
            })
            raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
            path.write_bytes(raw)
            record["sha256"] = hashlib.sha256(raw).hexdigest()
            record["bytes"] = len(raw)
            break
        else:
            pytest.fail("fixture published no canonical observation digest")
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

        comparison = bench.compare_ordered_exhaustive(
            arm, executed["arms"]["ordered_exhaustive"])
        assert comparison["semantic_evidence_identical"] is False
        assert any("not bijective" in problem for problem in comparison[
            "semantic_population_problems"])
        assert comparison["semantic_comparison_complete"] is False

    def test_complete_robust_decision_payload_is_compared(
            self, tmp_path, wired):
        executed = self._run(tmp_path, wired)
        cost_ordered = copy.deepcopy(executed["arms"]["cost_ordered"])
        cost_ordered["result"]["robust_decision"][
            "tampered_semantic_field"] = "forged"

        comparison = bench.compare_ordered_exhaustive(
            cost_ordered, executed["arms"]["ordered_exhaustive"])
        assert comparison["final_decision_identical"] is False
        assert comparison["semantic_comparison_complete"] is False

    def test_exact_attempts_are_counted_from_launch_telemetry_not_pilot_count(
            self, tmp_path, wired):
        """The real gate: real (variant, seed) SUMO launches, not candidates.

        `FakeRunner.timing_snapshot` reports one attempt per generated
        observation, mirroring `ArchivedDemandSumoRunner.launch_telemetry`'s
        real launch seam. The ordered-exhaustive arm verifies strictly more
        candidates than the cost-ordered arm, so it must also report strictly
        more exact attempts, and the reduction fraction between them must be
        computed from that telemetry rather than left `None`.
        """
        comparison = self._run(tmp_path, wired)["comparison"]
        exact = comparison["exact_attempts"]
        assert exact["cost_ordered"] is not None
        assert exact["ordered_exhaustive"] is not None
        assert exact["cost_ordered"] < exact["ordered_exhaustive"]
        assert comparison["exact_attempts_reduction_fraction"] is not None
        assert comparison["exact_attempts_reduction_fraction"] > 0
        # Same value under the pre-existing field name, so nothing that read
        # the old key before this pass silently starts reading None.
        assert (comparison["attempts_reduction_fraction"]
                == comparison["exact_attempts_reduction_fraction"])

    def test_awake_active_time_is_published_on_the_declared_basis(
            self, tmp_path, wired):
        comparison = self._run(tmp_path, wired)["comparison"]
        active = comparison["active_elapsed_s"]
        assert active["cost_ordered"] is not None
        assert active["ordered_exhaustive"] is not None
        assert comparison["active_elapsed_basis"] == "awake_monotonic_segments_v1"
        assert comparison["awake_active_time_reduction_fraction"] is not None

    def test_a_backend_without_the_telemetry_hook_reports_none_not_zero(self):
        """A missing optional hook must read as 'no data', never as '0%'."""
        assert bench._total_exact_attempts({}) is None
        assert bench._total_exact_attempts(
            {"exact_launch_telemetry": {}}) is None
        comparison = bench.compare_ordered_exhaustive(
            {"result": {}, "wall_time_s": 1.0, "peak_rss_bytes": 0},
            {"result": {}, "wall_time_s": 1.0, "peak_rss_bytes": 0},
        )
        assert comparison["exact_attempts"] == {
            "cost_ordered": None, "ordered_exhaustive": None}
        # No data: the 30% gate must not silently read this as satisfied.
        assert comparison["exact_attempts_reduction_meets_30_percent"] is False

    def test_each_arm_gets_its_own_daily_results_cache(self, tmp_path, wired):
        executed = self._run(tmp_path, wired)
        snapshots = executed["comparison"]["daily_results_cache_snapshots"]
        assert snapshots["cost_ordered"]["root"] != (
            snapshots["ordered_exhaustive"]["root"])

    def test_a_shared_cache_root_is_refused(self, tmp_path, wired, monkeypatch):
        monkeypatch.setattr(
            bench, "_isolated_daily_results_cache_root",
            lambda cache, arm, *, bound_source: (tmp_path / "shared", "digest"))
        with pytest.raises(RuntimeError, match="same daily-results cache"):
            self._run(tmp_path, wired)

    def test_counterbalance_reverses_which_arm_runs_first(self, tmp_path,
                                                           wired):
        order_seen: list[str] = []
        real_run = pa.run_arm

        def recording_run_arm(*args, **kwargs):
            result = real_run(*args, **kwargs)
            order_seen.append(result["arm"])
            return result

        import tools.cost_ordered_benchmark as bench_module
        original = bench_module.pa.run_arm
        bench_module.pa.run_arm = recording_run_arm
        try:
            self._run(tmp_path, wired, counterbalance=True)
        finally:
            bench_module.pa.run_arm = original
        assert order_seen == ["ordered_exhaustive", "cost_ordered"]
