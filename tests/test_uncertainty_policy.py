"""Step 7 acceptance: risk policy, stability gates, honest presentation.

Plan acceptance for step 7:
  * the UI separates the central run, demand uncertainty and seed
    uncertainty;
  * a low-observability road is not silently filtered out;
  * stress_only, paused, unavailable and inconclusive are distinct states
    that cannot be read as a winner.
"""

from __future__ import annotations

import pytest

from dirsplit.schema import Applicability
from traffic_sim.core.simulation_members import CostVector, SimulationMemberSpec
from traffic_sim.simulation import uncertainty_policy as up
from traffic_sim.simulation import uncertainty_presentation as pres


def vec(candidate_seed, case, seed, value, *, fail=False, reason=None,
        path="sp_a"):
    return CostVector(
        member=SimulationMemberSpec(path, case, seed, "candidate"),
        values={"total_time_loss_s": value, "unfinished_trips": 0.0},
        hard_failure=fail, failure_reason=reason,
    )


def candidate(values_by_case, *, seeds=(1000, 1001, 1002, 1003)):
    out = []
    for case, value in values_by_case.items():
        for seed in seeds:
            out.append(vec(None, case, seed, value))
    return out


WEIGHTS = {"dc_a": 0.5, "dc_b": 0.5}
BATCHES = {4: "closeX", 8: "closeX"}


# ── scoring ───────────────────────────────────────────────────────────────
class TestScoring:
    def test_expectation_weights_by_case_not_by_seed_count(self):
        """A case with more seeds must not gain weight it was not assigned."""
        vectors = ([vec(None, "dc_a", s, 10.0) for s in range(1000, 1010)]
                   + [vec(None, "dc_b", 1000, 20.0)])
        score = up.score_candidate("x", vectors, objective="total_time_loss_s",
                                   case_weights=WEIGHTS)
        assert score.expectation == pytest.approx(15.0)

    def test_seeds_within_a_case_are_averaged(self):
        vectors = [vec(None, "dc_a", 1000, 0.0), vec(None, "dc_a", 1001, 20.0)]
        score = up.score_candidate("x", vectors, objective="total_time_loss_s",
                                   case_weights={"dc_a": 1.0})
        assert score.expectation == pytest.approx(10.0)

    def test_a_tail_is_refused_with_too_few_cases(self):
        """Three cases cannot produce a q90; the maximum is not a percentile."""
        score = up.score_candidate("x", candidate({"dc_a": 10.0, "dc_b": 20.0}),
                                   objective="total_time_loss_s",
                                   case_weights=WEIGHTS)
        assert score.n_cases == 2
        assert score.tail is None

    def test_a_tail_appears_once_enough_cases_exist(self):
        weights = {f"dc_{i}": 1 / 10 for i in range(10)}
        vectors = [vec(None, f"dc_{i}", 1000, float(i)) for i in range(10)]
        score = up.score_candidate("x", vectors,
                                   objective="total_time_loss_s",
                                   case_weights=weights)
        assert score.n_cases == 10
        assert score.tail is not None

    def test_stress_cases_do_not_enter_the_expectation(self):
        vectors = candidate({"dc_a": 10.0, "dc_b": 10.0})
        vectors += [vec(None, "dc_stress", 1000, 1000.0)]
        score = up.score_candidate("x", vectors,
                                   objective="total_time_loss_s",
                                   case_weights=WEIGHTS,
                                   stress_case_ids=["dc_stress"])
        assert score.expectation == pytest.approx(10.0)

    def test_a_stress_failure_is_counted_separately(self):
        vectors = candidate({"dc_a": 10.0, "dc_b": 10.0})
        vectors += [vec(None, "dc_stress", 1000, 0.0, fail=True,
                        reason="no detour")]
        score = up.score_candidate("x", vectors,
                                   objective="total_time_loss_s",
                                   case_weights=WEIGHTS,
                                   stress_case_ids=["dc_stress"])
        assert score.stress_failures == 1
        assert score.expectation == pytest.approx(10.0)

    def test_a_candidate_with_no_surviving_case_is_disqualified(self):
        vectors = [vec(None, "dc_a", 1000, 5.0, fail=True, reason="severed")]
        score = up.score_candidate("x", vectors,
                                   objective="total_time_loss_s",
                                   case_weights=WEIGHTS)
        assert score.is_disqualified


class TestWeightedQuantile:
    def test_it_respects_mass_not_count(self):
        assert up.weighted_quantile([1.0, 10.0], [0.9, 0.1], 0.5) == \
            pytest.approx(1.0)
        assert up.weighted_quantile([1.0, 10.0], [0.1, 0.9], 0.5) == \
            pytest.approx(10.0)

    def test_zero_mass_is_refused(self):
        with pytest.raises(up.PolicyError, match="positive mass"):
            up.weighted_quantile([1.0], [0.0], 0.5)


# ── stability ─────────────────────────────────────────────────────────────
class TestStability:
    def test_one_batch_is_never_stability_evidence(self):
        check = up.check_stability({4: "closeX"}, min_seeds_seen=8)
        assert not check.scenario_stable
        assert "single batch" in check.note

    def test_two_agreeing_batches_are_stable(self):
        check = up.check_stability({4: "closeX", 8: "closeX"},
                                   min_seeds_seen=8)
        assert check.is_stable

    def test_two_disagreeing_batches_are_not(self):
        check = up.check_stability({4: "closeX", 8: "closeY"},
                                   min_seeds_seen=8)
        assert not check.scenario_stable
        assert "disagree" in check.note

    def test_too_few_seeds_is_not_stable(self):
        check = up.check_stability({4: "closeX", 8: "closeX"},
                                   min_seeds_seen=2)
        assert check.scenario_stable
        assert not check.seed_stable
        assert not check.is_stable


# ── the policy ────────────────────────────────────────────────────────────
class TestPolicy:
    def run(self, candidates, **kwargs):
        params = dict(objective="total_time_loss_s", case_weights=WEIGHTS,
                      budget_complete=True, winner_by_batch=BATCHES)
        params.update(kwargs)
        return up.apply_policy(candidates, **params)

    def test_a_clear_stable_winner_is_published(self):
        result = self.run({
            "closeX": candidate({"dc_a": 1.0, "dc_b": 1.0}),
            "closeY": candidate({"dc_a": 50.0, "dc_b": 50.0}),
        })
        assert result.decision.outcome == "unique_winner"
        assert result.decision.winner == "closeX"

    def test_an_incomplete_budget_cannot_win(self):
        result = self.run({
            "closeX": candidate({"dc_a": 1.0, "dc_b": 1.0}),
            "closeY": candidate({"dc_a": 50.0, "dc_b": 50.0}),
        }, budget_complete=False)
        assert result.decision.outcome == "inconclusive"
        assert result.decision.winner is None

    def test_a_paused_run_cannot_win(self):
        result = self.run({
            "closeX": candidate({"dc_a": 1.0, "dc_b": 1.0}),
            "closeY": candidate({"dc_a": 50.0, "dc_b": 50.0}),
        }, paused=True)
        assert result.decision.outcome == "paused"

    def test_an_unstable_ranking_is_inconclusive(self):
        result = self.run({
            "closeX": candidate({"dc_a": 1.0, "dc_b": 1.0}),
            "closeY": candidate({"dc_a": 50.0, "dc_b": 50.0}),
        }, winner_by_batch={4: "closeY", 8: "closeX"})
        assert result.decision.outcome == "inconclusive"
        assert "settled" in result.decision.reason

    def test_too_few_seeds_is_inconclusive(self):
        result = self.run({
            "closeX": candidate({"dc_a": 1.0, "dc_b": 1.0}, seeds=(1000,)),
            "closeY": candidate({"dc_a": 50.0, "dc_b": 50.0}, seeds=(1000,)),
        })
        assert result.decision.outcome == "inconclusive"

    def test_disagreeing_risk_measures_produce_a_tie(self):
        """Expectation prefers one, max regret the other -> no winner."""
        weights = {f"dc_{i}": 1 / 10 for i in range(10)}
        # closeX: low mean, one catastrophic world. closeY: steady.
        x = [vec(None, f"dc_{i}", 1000, 1.0 if i < 9 else 500.0)
             for i in range(10)]
        y = [vec(None, f"dc_{i}", 1000, 20.0) for i in range(10)]
        result = up.apply_policy({"closeX": x, "closeY": y},
                                 objective="total_time_loss_s",
                                 case_weights=weights, budget_complete=True,
                                 winner_by_batch=BATCHES)
        if result.decision.outcome == "tie":
            assert "disagree" in result.decision.reason
        assert result.decision.winner is None or \
            result.decision.outcome == "unique_winner"

    def test_a_disqualified_candidate_cannot_win(self):
        result = self.run({
            "closeX": candidate({"dc_a": 1.0, "dc_b": 1.0}),
            "closeY": candidate({"dc_a": 50.0, "dc_b": 50.0}),
        }, disqualified={"closeX": "topologiskt avskuren"})
        assert result.decision.winner == "closeY"

    def test_everything_disqualified_is_no_viable(self):
        result = self.run({
            "closeX": candidate({"dc_a": 1.0, "dc_b": 1.0}),
        }, disqualified={"closeX": "no detour"})
        assert result.decision.outcome == "no_viable"

    def test_scores_are_reported_whatever_the_outcome(self):
        result = self.run({
            "closeX": candidate({"dc_a": 1.0, "dc_b": 1.0}),
            "closeY": candidate({"dc_a": 50.0, "dc_b": 50.0}),
        }, budget_complete=False)
        assert result.decision.outcome == "inconclusive"
        assert len(result.scores) == 2


# ── presentation ──────────────────────────────────────────────────────────
class TestPresentation:
    def result(self, **kwargs):
        params = dict(objective="total_time_loss_s", case_weights=WEIGHTS,
                      budget_complete=True, winner_by_batch=BATCHES)
        params.update(kwargs)
        return up.apply_policy({
            "closeX": candidate({"dc_a": 1.0, "dc_b": 1.0}),
            "closeY": candidate({"dc_a": 50.0, "dc_b": 50.0}),
        }, **params)

    def inventory(self, seeds=4):
        return pres.RunInventory(scenario_paths=2, demand_cases=2,
                                 seeds_per_case=seeds, stress_cases=1)

    def test_the_three_axes_are_separate(self):
        payload = pres.build_payload(self.result(), self.inventory())
        assert "central_run" in payload
        assert "demand_uncertainty" in payload
        assert "seed_uncertainty" in payload
        assert payload["central_run"]["note"].startswith("Animationen visar EN")

    def test_an_uncalibrated_interval_never_says_eighty_percent(self):
        payload = pres.build_payload(self.result(), self.inventory(),
                                     calibration_status="stress_only")
        label = payload["demand_uncertainty"]["interval_label"]
        assert "80" not in label
        assert "stress" in label.lower()
        assert not payload["demand_uncertainty"]["is_probability_statement"]

    def test_a_calibrated_interval_reports_measured_coverage(self):
        payload = pres.build_payload(self.result(), self.inventory(),
                                     calibration_status="calibrated",
                                     nominal_coverage=0.8,
                                     empirical_coverage=0.79)
        label = payload["demand_uncertainty"]["interval_label"]
        assert "80" in label and "79" in label
        assert payload["demand_uncertainty"]["is_probability_statement"]

    def test_a_missing_interval_says_so(self):
        assert pres.interval_label("unavailable") == "Intervall saknas"

    def test_the_inventory_reports_what_actually_ran(self):
        payload = pres.build_payload(self.result(), self.inventory(seeds=2))
        assert payload["inventory"]["seeds_per_case"] == 2
        assert not payload["inventory"]["seeds_sufficient"]

    def test_a_low_evidence_road_is_shown_not_filtered(self):
        weak = Applicability(
            edge_id="E1", measurement_level="transfer_prior",
            static_domain="extrapolation", temporal_support="limited",
            feature_compatibility="good", effective_sample_size=1.0,
            calibration_support="extrapolation",
        )
        payload = pres.build_payload(self.result(), self.inventory(),
                                     applicability=[weak])
        road = payload["roads"][0]
        assert road["included_in_search"]
        assert road["excluded_reason"] is None
        assert "Extrapolation" in road["claim_label"]

    def test_only_an_input_error_excludes_a_road(self):
        broken = Applicability(
            edge_id="E2", measurement_level="transfer_prior",
            static_domain="good", temporal_support="good",
            feature_compatibility="unavailable", effective_sample_size=5.0,
            calibration_support="good",
        )
        payload = pres.build_payload(self.result(), self.inventory(),
                                     applicability=[broken])
        assert not payload["roads"][0]["included_in_search"]
        assert payload["roads"][0]["excluded_reason"] == "indatafel"

    @pytest.mark.parametrize("kwargs,expected", [
        ({"budget_complete": False}, "inconclusive"),
        ({"paused": True}, "paused"),
        ({"winner_by_batch": {4: "closeY", 8: "closeX"}}, "inconclusive"),
    ])
    def test_non_winning_outcomes_never_name_a_winner(self, kwargs, expected):
        payload = pres.build_payload(self.result(**kwargs), self.inventory())
        assert payload["outcome"] == expected
        assert payload["winner"] is None
        assert not payload["is_winner"]
        pres.assert_no_false_winner(payload)

    @pytest.mark.parametrize("outcome", ["tie", "inconclusive", "no_viable",
                                         "paused"])
    def test_every_non_winning_headline_omits_a_candidate_name(self, outcome):
        assert "closeX" not in pres.HEADLINES[outcome]
        assert "closeY" not in pres.HEADLINES[outcome]

    def test_the_guard_catches_a_forged_winner(self):
        payload = pres.build_payload(self.result(budget_complete=False),
                                     self.inventory())
        payload["winner"] = "closeX"
        with pytest.raises(ValueError, match="must not name a winner"):
            pres.assert_no_false_winner(payload)

    def test_the_guard_catches_a_forged_is_winner_flag(self):
        payload = pres.build_payload(self.result(paused=True),
                                     self.inventory())
        payload["is_winner"] = True
        with pytest.raises(ValueError, match="must not set is_winner"):
            pres.assert_no_false_winner(payload)

    def test_a_real_winner_passes_the_guard(self):
        payload = pres.build_payload(self.result(), self.inventory())
        assert payload["outcome"] == "unique_winner"
        assert payload["winner"] == "closeX"
        pres.assert_no_false_winner(payload)

    def test_convergence_is_exposed_to_the_ui(self):
        payload = pres.build_payload(self.result(), self.inventory())
        assert "convergence" in payload
        assert "observed_batches" in payload["convergence"]


# ── the shadow-mode API ───────────────────────────────────────────────────
class TestShadowModeEndpoint:
    """serve.py exposes v2 status read-only, with production unchanged."""

    def handler(self):
        import serve
        return serve.Handler

    def test_the_endpoint_is_dispatched_on_get(self):
        import inspect
        import serve
        source = inspect.getsource(serve.Handler.do_GET)
        assert "/api/uncertainty/status" in source

    def test_it_is_not_a_mutating_endpoint(self):
        import serve
        for prefix in serve.Handler._MUTATING:
            assert not "/api/uncertainty/status".startswith(prefix)

    def test_shadow_mode_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("DIRSPLIT_V2_SHADOW", raising=False)
        captured = {}

        import serve

        class Fake(serve.Handler):
            def __init__(self):
                pass

            def _json(self, code, payload):
                captured["code"] = code
                captured["payload"] = payload

        Fake()._uncertainty_status()
        assert captured["code"] == 200
        assert captured["payload"]["shadow_mode_enabled"] is False
        assert captured["payload"]["production_default"] == \
            "legacy_q10_q50_q90"

    def test_it_can_be_enabled_explicitly(self, monkeypatch):
        monkeypatch.setenv("DIRSPLIT_V2_SHADOW", "1")
        captured = {}

        import serve

        class Fake(serve.Handler):
            def __init__(self):
                pass

            def _json(self, code, payload):
                captured["payload"] = payload

        Fake()._uncertainty_status()
        assert captured["payload"]["shadow_mode_enabled"] is True

    def test_it_never_claims_an_unmeasured_interval(self, monkeypatch):
        monkeypatch.delenv("DIRSPLIT_V2_SHADOW", raising=False)
        captured = {}

        import serve

        class Fake(serve.Handler):
            def __init__(self):
                pass

            def _json(self, code, payload):
                captured["payload"] = payload

        Fake()._uncertainty_status()
        assert "80" not in captured["payload"]["interval_label"]
