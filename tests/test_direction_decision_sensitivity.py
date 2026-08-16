"""Fas 0B of the direction plan: the matched-seed sensitivity study.

The study exists because demand-case identity and SUMO seed are entangled in
today's contracts (seed 1000 -> q50, 1001 -> q10, 1002 -> q90), so a three-seed
run cannot say which axis moved a closure decision.  These tests pin the parts
that decide something: the frozen matrix, the matched-pair requirement, the
deterministic Gate S rule and the append-only, non-release evidence.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import measure_direction_decision_sensitivity as study

REGISTRATION = Path("validation/direction_decision_sensitivity_registration_v1.json")


def _registration():
    return json.loads(REGISTRATION.read_text())


def _decision(costs, disqualified=None, *, shortlist_size=2, margin=5.0):
    return study.rank_within_case(costs, disqualified or {},
                                  shortlist_size=shortlist_size,
                                  winner_margin_pct=margin)


class TestTheStudyIsPreregistered:
    def test_the_frozen_registration_is_committed_and_valid(self):
        study.validate_registration(_registration())

    def test_it_is_diagnostic_not_release_evidence(self):
        assert _registration()["release_evidence"] is False

    def test_it_freezes_candidates_seeds_cases_window_and_tolerances(self):
        registration = _registration()
        assert registration["candidates"]
        assert registration["seeds"] == [1000, 1001, 1002, 1003]
        assert registration["stress_cases"] == ["q50", "q10", "q90"]
        assert registration["demand"]["window_start"] == "07:00"
        assert set(registration["tolerances"]) >= {
            "winner_margin_pct", "material_rank_correlation",
            "material_regret_pct", "min_pairs_per_cell"}

    def test_the_candidate_edges_exist_in_the_published_network(self):
        edges = {feature["properties"]["id"]
                 for feature in json.loads(
                     Path("web/data/network.geojson").read_text())["features"]
                 if feature["geometry"]["type"] == "LineString"}
        for candidate in _registration()["candidates"]:
            assert set(candidate["edges"]) <= edges, candidate["candidate_id"]

    def test_product_integration_is_explicitly_out_of_scope(self):
        forbidden = _registration()["forbidden"]
        assert "ScenarioSpec change" in forbidden
        assert "release promotion" in forbidden

    def test_a_registration_cannot_be_silently_redefined(self, tmp_path):
        path = tmp_path / "registration.json"
        payload = study.default_registration("2026-08-16")
        assert study.write_registration(path, payload) == "written"
        assert study.write_registration(path, payload) == "unchanged"
        changed = json.loads(json.dumps(payload))
        changed["seeds"] = [1, 2, 3]
        with pytest.raises(ValueError, match="may not be redefined"):
            study.write_registration(path, changed)

    def test_an_incomplete_registration_fails_closed(self):
        payload = study.default_registration("2026-08-16")
        payload.pop("tolerances")
        with pytest.raises(ValueError, match="missing field"):
            study.validate_registration(payload)

    def test_a_registration_claiming_release_evidence_is_rejected(self):
        payload = study.default_registration("2026-08-16")
        payload["release_evidence"] = True
        with pytest.raises(ValueError, match="release_evidence must be false"):
            study.validate_registration(payload)


class TestTheMatrixIsAFullCrossProduct:
    def test_every_candidate_runs_under_every_stress_case(self):
        registration = _registration()
        cells = study.enumerate_cells(registration)
        assert len(cells) == (len(registration["candidates"]) + 1) * len(
            registration["stress_cases"])
        for case in registration["stress_cases"]:
            assert any(cell.candidate_id == "baseline" and cell.stress_case == case
                       for cell in cells)

    def test_the_same_seed_list_is_used_under_every_stress_case(self):
        seeds = {cell.seeds for cell in study.enumerate_cells(_registration())}
        assert len(seeds) == 1


class TestMatchedPairsAreRequired:
    """The whole point: a candidate is compared with the baseline of the SAME
    stress case and the SAME seed, never against an independently aggregated
    mean."""

    def _runs(self, case, seeds, time_losses):
        from signal_optimize import SignalRun
        from traffic_sim.simulation.metrics import DisruptionMetrics
        return [SignalRun(seed=seed, demand_variant=case,
                          metrics=DisruptionMetrics(
                              total_time_loss_s=value, trip_count=100,
                              unfinished_trips=0, unfinished_waiting_trips=0,
                              teleport_total=0, teleport_reasons={},
                              loaded=100, inserted=100, running_at_end=0,
                              waiting_at_end=0))
                for seed, value in zip(seeds, time_losses)]

    def test_unmatched_seeds_are_refused(self):
        from signal_optimize import paired_comparison
        baseline = self._runs("q50", [1000, 1001], [10.0, 12.0])
        candidate = self._runs("q50", [1000, 1002], [11.0, 13.0])
        with pytest.raises(ValueError, match="identical seed/demand pairs"):
            paired_comparison(baseline, candidate)

    def test_a_stress_case_cannot_be_paired_with_another(self):
        from signal_optimize import paired_comparison
        baseline = self._runs("q50", [1000, 1001], [10.0, 12.0])
        candidate = self._runs("q10", [1000, 1001], [11.0, 13.0])
        with pytest.raises(ValueError, match="identical seed/demand pairs"):
            paired_comparison(baseline, candidate)

    def test_the_cell_cost_is_the_mean_matched_difference(self):
        from signal_optimize import paired_comparison
        baseline = self._runs("q50", [1000, 1001], [10.0, 20.0])
        candidate = self._runs("q50", [1000, 1001], [12.0, 26.0])
        paired = paired_comparison(baseline, candidate)
        assert study.cell_cost(paired) == pytest.approx(4.0)


class TestGateSIsDeterministic:
    TOLERANCES = {"winner_margin_pct": 5.0, "material_rank_correlation": 0.9,
                  "material_regret_pct": 10.0, "min_pairs_per_cell": 4}

    def test_identical_decisions_across_cases_give_no(self):
        costs = {"a": 100.0, "b": 200.0, "c": 300.0}
        decisions = {case: _decision(costs) for case in ("q10", "q50", "q90")}
        gate = study.classify_gate_s(decisions, self.TOLERANCES)
        assert gate["verdict"] == study.GATE_NO
        assert gate["changed_fields"] == []

    def test_a_changed_winner_outside_the_tie_band_gives_yes(self):
        decisions = {
            "q50": _decision({"a": 100.0, "b": 200.0}),
            "q90": _decision({"a": 300.0, "b": 200.0}),
        }
        gate = study.classify_gate_s(decisions, self.TOLERANCES)
        assert gate["verdict"] == study.GATE_YES
        assert "winner" in gate["changed_fields"]

    def test_a_winner_swap_inside_the_tie_band_is_not_material(self):
        decisions = {
            "q50": _decision({"a": 100.0, "b": 101.0}),
            "q90": _decision({"a": 101.0, "b": 100.0}),
        }
        gate = study.classify_gate_s(decisions, self.TOLERANCES)
        assert gate["verdict"] == study.GATE_NO

    def test_a_changed_hard_failure_gives_yes(self):
        decisions = {
            "q50": _decision({"a": 100.0, "b": 200.0}),
            "q90": _decision({"a": 100.0, "b": 200.0},
                             {"b": ["no detour available"]}),
        }
        gate = study.classify_gate_s(decisions, self.TOLERANCES)
        assert gate["verdict"] == study.GATE_YES
        assert "hard_failure" in gate["changed_fields"]
        assert "viable_set" in gate["changed_fields"]

    def test_a_reordered_shortlist_gives_yes(self):
        decisions = {
            "q50": _decision({"a": 100.0, "b": 200.0, "c": 300.0}),
            "q90": _decision({"a": 100.0, "b": 400.0, "c": 300.0}),
        }
        gate = study.classify_gate_s(decisions, self.TOLERANCES)
        assert gate["verdict"] == study.GATE_YES
        assert "shortlist" in gate["changed_fields"]

    def test_large_regret_alone_is_material(self):
        decisions = {
            "q50": _decision({"a": 100.0, "b": 105.0}),
            "q90": _decision({"a": 104.0, "b": 100.0}),
        }
        gate = study.classify_gate_s(
            decisions, {**self.TOLERANCES, "material_regret_pct": 1.0})
        assert gate["verdict"] == study.GATE_YES
        assert "max_regret" in gate["changed_fields"]

    def test_unhealthy_runs_can_never_read_as_no(self):
        costs = {"a": 100.0, "b": 200.0}
        decisions = {case: _decision(costs) for case in ("q50", "q90")}
        gate = study.classify_gate_s(decisions, self.TOLERANCES,
                                     health={"status": "timeout",
                                             "detail": "budget exhausted"})
        assert gate["verdict"] == study.GATE_INCONCLUSIVE

    def test_a_single_stress_case_cannot_decide_the_gate(self):
        gate = study.classify_gate_s({"q50": _decision({"a": 1.0})},
                                     self.TOLERANCES)
        assert gate["verdict"] == study.GATE_INCONCLUSIVE

    def test_the_verdict_is_reproducible_from_the_stored_decisions(self):
        decisions = {"q50": _decision({"a": 100.0, "b": 200.0}),
                     "q90": _decision({"a": 300.0, "b": 200.0})}
        first = study.classify_gate_s(decisions, self.TOLERANCES)
        replayed = study.classify_gate_s(
            json.loads(json.dumps(decisions)), self.TOLERANCES)
        assert first["verdict"] == replayed["verdict"]
        assert first["changed_fields"] == replayed["changed_fields"]


class TestRankCorrelation:
    def test_identical_orders_correlate_perfectly(self):
        assert study.spearman(["a", "b", "c"], ["a", "b", "c"]) == pytest.approx(1.0)

    def test_reversed_orders_anticorrelate(self):
        assert study.spearman(["a", "b", "c"], ["c", "b", "a"]) == pytest.approx(-1.0)

    def test_too_few_shared_candidates_is_undefined_not_zero(self):
        assert study.spearman(["a"], ["a"]) is None


class TestMeasuredRunControlFlow:
    """The control flow is exercised with a stub runner: no simulator needed
    to prove that cells, pairs and the gate are wired together correctly."""

    def _runner(self, table):
        from signal_optimize import SignalRun
        from traffic_sim.simulation.metrics import DisruptionMetrics

        def runner(*, candidate_id, edges, stress_case, seeds):
            base = table[(candidate_id, stress_case)]
            runs = [SignalRun(seed=seed, demand_variant=stress_case,
                              metrics=DisruptionMetrics(
                                  total_time_loss_s=base + index,
                                  trip_count=100, unfinished_trips=0,
                                  unfinished_waiting_trips=0, teleport_total=0,
                                  teleport_reasons={}, loaded=100, inserted=100,
                                  running_at_end=0, waiting_at_end=0))
                    for index, seed in enumerate(seeds)]
            return runs, []
        return runner

    def _registration(self, **overrides):
        registration = study.default_registration("2026-08-16")
        registration["candidates"] = [
            {"candidate_id": "a", "edges": ["e1"]},
            {"candidate_id": "b", "edges": ["e2"]},
        ]
        registration.update(overrides)
        return registration

    def test_a_direction_insensitive_matrix_reports_no(self):
        registration = self._registration()
        table = {}
        for case in registration["stress_cases"]:
            table[("baseline", case)] = 1000.0
            table[("a", case)] = 1100.0
            table[("b", case)] = 1300.0
        outcome = study.measure(registration, runner=self._runner(table))
        assert outcome["gate_s"]["verdict"] == study.GATE_NO
        assert outcome["release_evidence"] is False
        assert outcome["simulation_units"] == 3 * 3 * 4

    def test_a_direction_sensitive_matrix_reports_yes(self):
        registration = self._registration()
        table = {}
        for case in registration["stress_cases"]:
            table[("baseline", case)] = 1000.0
            table[("a", case)] = 1100.0
            table[("b", case)] = 1300.0
        table[("b", "q90")] = 1010.0          # b wins only under q90
        outcome = study.measure(registration, runner=self._runner(table))
        assert outcome["gate_s"]["verdict"] == study.GATE_YES
        assert "winner" in outcome["gate_s"]["changed_fields"]

    def test_too_few_seeds_per_cell_is_inconclusive_not_no(self):
        registration = self._registration()
        registration["seeds"] = [1000, 1001]
        table = {}
        for case in registration["stress_cases"]:
            table[("baseline", case)] = 1000.0
            table[("a", case)] = 1100.0
            table[("b", case)] = 1300.0
        outcome = study.measure(registration, runner=self._runner(table))
        assert outcome["gate_s"]["verdict"] == study.GATE_INCONCLUSIVE

    def test_a_failing_cell_is_inconclusive_not_no(self):
        registration = self._registration()

        def runner(*, candidate_id, edges, stress_case, seeds):
            raise RuntimeError("sumo crashed")

        outcome = study.measure(registration, runner=runner)
        assert outcome["gate_s"]["verdict"] == study.GATE_INCONCLUSIVE
        assert "sumo crashed" in outcome["gate_s"]["health"]["detail"]


class TestEvidenceIsAppendOnly:
    def test_records_accumulate_and_are_never_rewritten(self, tmp_path):
        path = tmp_path / "outcome.json"
        first = {"release_evidence": False, "gate_s": {"verdict": "INCONCLUSIVE"}}
        second = {"release_evidence": False, "gate_s": {"verdict": "NO"}}
        assert study.append_outcome(path, first) == 1
        assert study.append_outcome(path, second) == 2
        payload = json.loads(path.read_text())
        assert [record["gate_s"]["verdict"] for record in payload["records"]] == [
            "INCONCLUSIVE", "NO"]

    def test_an_outcome_claiming_release_evidence_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="release_evidence"):
            study.append_outcome(tmp_path / "outcome.json",
                                 {"release_evidence": True})


class TestEnvironmentFailsClosed:
    def test_a_missing_demand_build_cannot_produce_a_verdict(self, tmp_path,
                                                             monkeypatch):
        import run_scenario as rs
        monkeypatch.setattr(rs, "SUMO_DIR", tmp_path)
        with pytest.raises(study.EnvironmentUnavailable, match="no calibrated demand"):
            study._sumo_runner(_registration())

    def test_a_mismatched_demand_build_is_refused(self, tmp_path, monkeypatch):
        import run_scenario as rs
        monkeypatch.setattr(rs, "SUMO_DIR", tmp_path)
        (tmp_path / "demand_meta.json").write_text(json.dumps(
            {"date": "2027-09-14", "source": "forecast"}))
        with pytest.raises(study.EnvironmentUnavailable, match="frozen on"):
            study._sumo_runner(_registration())
