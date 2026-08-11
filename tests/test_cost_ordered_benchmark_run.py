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

ROOT = Path(__file__).resolve().parents[1]


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
    record["output_roots"] = {
        "exhaustive": "runs/bench-exhaustive",
        "cost_ordered": "runs/bench-cost-ordered",
        "daily_cost_cache": "runs/bench-daily-costs",
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


class TestFaultInjection:
    def test_an_interrupted_run_resumes_to_the_same_outcome(self, tmp_path,
                                                            wired):
        record = _registration(tmp_path, wired["spec"])
        executed = bench.run_benchmark(
            record, runs_root=tmp_path, release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws", data_root=tmp_path,
            fault_injection=True)
        restart = executed["comparison"]["restart"]
        assert restart["performed"] is True
        assert restart["interrupted_after_pilots"] >= 1
        assert executed["comparison"]["restart_equivalent"] is True, restart

    def test_skipping_the_probe_fails_the_restart_gate(self, tmp_path, wired):
        record = _registration(tmp_path, wired["spec"])
        executed = bench.run_benchmark(
            record, runs_root=tmp_path, release_root=tmp_path / "releases",
            workspace_root=tmp_path / "ws", data_root=tmp_path,
            fault_injection=False)
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
        code = bench.main(["--run", "--registration", str(path),
                           "--runs-root", str(tmp_path),
                           "--data-root", str(tmp_path),
                           "--release-root", str(tmp_path / "releases"),
                           "--workspace-root", str(tmp_path / "ws"),
                           "--out", str(out)])
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
        bench.main(["--run", "--registration", str(path),
                    "--runs-root", str(tmp_path),
                    "--data-root", str(tmp_path),
                    "--release-root", str(tmp_path / "releases"),
                    "--workspace-root", str(tmp_path / "ws"),
                    "--out", str(tmp_path / "outcome.json")])
        assert path.read_bytes() == before

    def test_an_outcome_is_not_silently_replaced(self, tmp_path, wired):
        record = _registration(tmp_path, wired["spec"])
        path = tmp_path / "registration.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        out = tmp_path / "outcome.json"
        argv = ["--run", "--registration", str(path),
                "--runs-root", str(tmp_path),
                "--data-root", str(tmp_path),
                "--release-root", str(tmp_path / "releases"),
                "--workspace-root", str(tmp_path / "ws"), "--out", str(out)]
        bench.main(argv)
        with pytest.raises(SystemExit, match="already exists"):
            bench.main(argv)
