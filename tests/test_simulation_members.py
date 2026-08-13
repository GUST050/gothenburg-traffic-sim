"""Step 6 acceptance: case ⊥ seed, matched pairs, no splicing, fail-closed.

Plan acceptance for step 6:
  * the same seed list is used for every case and for baseline/candidate;
  * tests can change the demand case while holding the seed fixed, and the
    other way round;
  * no reducer can take fields from different cases and build a false cost;
  * a paused or capped run cannot become a unique_winner.
"""

from __future__ import annotations

import pytest

from traffic_sim.core import simulation_members as sm


def member(path="sp_a", case="dc_a", seed=1000, arm="baseline"):
    return sm.SimulationMemberSpec(path, case, seed, arm)


def vector(m=None, *, time_loss=10.0, unfinished=0.0, fail=False,
           reason=None):
    return sm.CostVector(
        member=m or member(),
        values={"total_time_loss_s": time_loss,
                "unfinished_trips": unfinished},
        hard_failure=fail, failure_reason=reason,
    )


# ── orthogonality ─────────────────────────────────────────────────────────
class TestCaseAndSeedAreIndependent:
    def test_a_member_names_both_coordinates(self):
        m = member()
        assert m.demand_case_id == "dc_a"
        assert m.simulation_seed == 1000

    def test_the_seed_can_be_held_while_the_case_varies(self):
        a = member(case="dc_a", seed=1000)
        b = member(case="dc_b", seed=1000)
        assert a.simulation_seed == b.simulation_seed
        assert a.demand_case_id != b.demand_case_id
        assert a.member_id != b.member_id

    def test_the_case_can_be_held_while_the_seed_varies(self):
        a = member(case="dc_a", seed=1000)
        b = member(case="dc_a", seed=1001)
        assert a.demand_case_id == b.demand_case_id
        assert a.simulation_seed != b.simulation_seed

    def test_no_function_derives_a_case_from_a_seed(self):
        assert not hasattr(sm, "canonical_seed")
        assert not hasattr(sm, "variant_for_seed")
        assert not hasattr(sm, "case_for_seed")

    def test_the_legacy_coupling_still_exists_untouched(self):
        """Step 6 is additive: the frozen goldens keep their exact bytes."""
        from traffic_sim.simulation.monthly_search import canonical_seed
        assert canonical_seed("q10", 0) == 1000
        assert canonical_seed("q50", 0) == 1001


# ── plan construction ─────────────────────────────────────────────────────
class TestPlanUsesTheSameSeedsEverywhere:
    def test_the_plan_is_the_full_cross_product(self):
        plan = sm.build_plan(scenario_path_ids=["sp_a"],
                             demand_case_ids=["dc_a", "dc_b"],
                             seeds=[1000, 1001, 1002, 1003])
        assert len(plan) == 1 * 2 * 4 * 2      # paths x cases x seeds x arms
        sm.validate_plan(plan)

    def test_every_case_sees_the_same_seeds(self):
        plan = sm.build_plan(scenario_path_ids=["sp_a"],
                             demand_case_ids=["dc_a", "dc_b"],
                             seeds=[1000, 1001])
        by_case = {}
        for m in plan:
            by_case.setdefault(m.demand_case_id, set()).add(m.simulation_seed)
        assert by_case["dc_a"] == by_case["dc_b"] == {1000, 1001}

    def test_an_unbalanced_plan_is_refused(self):
        plan = [member(case="dc_a", seed=1000, arm="baseline"),
                member(case="dc_a", seed=1000, arm="candidate"),
                member(case="dc_b", seed=1001, arm="baseline"),
                member(case="dc_b", seed=1001, arm="candidate")]
        with pytest.raises(sm.PlanError, match="same seeds"):
            sm.validate_plan(plan)

    def test_a_missing_arm_is_refused(self):
        plan = [member(seed=1000, arm="baseline"),
                member(seed=1000, arm="candidate"),
                member(seed=1001, arm="baseline")]
        with pytest.raises(sm.PlanError, match="matched arm"):
            sm.validate_plan(plan)

    def test_duplicate_seeds_are_refused(self):
        with pytest.raises(sm.PlanError, match="unique"):
            sm.build_plan(scenario_path_ids=["sp_a"],
                          demand_case_ids=["dc_a"], seeds=[1000, 1000])

    def test_an_empty_axis_is_refused(self):
        with pytest.raises(sm.PlanError):
            sm.build_plan(scenario_path_ids=[], demand_case_ids=["dc_a"],
                          seeds=[1000])


class TestCommonRandomNumbers:
    def test_a_matched_pair_shares_path_case_and_seed(self):
        plan = sm.build_plan(scenario_path_ids=["sp_a"],
                             demand_case_ids=["dc_a"], seeds=[1000, 1001])
        pairs = sm.matched_pairs(plan)
        assert len(pairs) == 2
        for base, cand in pairs:
            assert base.pair_key == cand.pair_key
            assert base.arm == "baseline" and cand.arm == "candidate"

    def test_unpaired_members_produce_no_pairs(self):
        plan = sm.build_plan(scenario_path_ids=["sp_a"],
                             demand_case_ids=["dc_a"], seeds=[1000],
                             paired=False)
        assert sm.matched_pairs(plan) == []

    def test_the_pair_key_excludes_the_arm(self):
        base = member(arm="baseline")
        assert base.pair_key == base.with_arm("candidate").pair_key


# ── plan invariant 4: no field-wise splicing ──────────────────────────────
class TestNoFieldWiseSplicing:
    def test_select_worst_returns_a_vector_it_was_given(self):
        a = vector(member(case="dc_a"), time_loss=10.0, unfinished=5.0)
        b = vector(member(case="dc_b"), time_loss=20.0, unfinished=1.0)
        worst = sm.select_worst([a, b], "total_time_loss_s")
        assert worst is b
        # The worst by time loss keeps ITS OWN unfinished count — it does not
        # inherit a's higher one.
        assert worst.get("unfinished_trips") == 1.0

    def test_the_result_is_traceable_to_one_member(self):
        a = vector(member(case="dc_a"), time_loss=10.0)
        b = vector(member(case="dc_b"), time_loss=20.0)
        worst = sm.select_worst([a, b], "total_time_loss_s")
        assert worst.member.demand_case_id == "dc_b"

    def test_vectors_must_agree_on_their_fields(self):
        a = vector()
        b = sm.CostVector(member=member(case="dc_b"),
                          values={"total_time_loss_s": 1.0})
        with pytest.raises(sm.PlanError, match="disagree on their fields"):
            sm.require_same_fields([a, b])

    def test_reading_an_absent_field_is_refused(self):
        v = vector()
        with pytest.raises(sm.PlanError, match="splice fields across worlds"):
            v.get("closed_edge_throughput")

    def test_hard_failures_are_removed_before_ranking(self):
        good = vector(member(case="dc_a"), time_loss=10.0)
        bad = vector(member(case="dc_b"), time_loss=999.0, fail=True,
                     reason="no detour")
        assert sm.select_worst([good, bad], "total_time_loss_s") is good

    def test_all_failures_cannot_be_ranked(self):
        bad = vector(fail=True, reason="severed")
        with pytest.raises(sm.PlanError, match="hard failure"):
            sm.select_worst([bad], "total_time_loss_s")

    def test_a_failure_must_name_its_reason(self):
        with pytest.raises(sm.PlanError, match="name its reason"):
            sm.CostVector(member=member(), values={"x": 1.0},
                          hard_failure=True)


class TestWeightedExpectation:
    def test_it_weights_by_case(self):
        a = vector(member(case="dc_a"), time_loss=0.0)
        b = vector(member(case="dc_b"), time_loss=10.0)
        result = sm.weighted_expectation([a, b], "total_time_loss_s",
                                         {"dc_a": 0.25, "dc_b": 0.75})
        assert result == pytest.approx(7.5)

    def test_a_stress_case_has_no_mass_and_is_excluded(self):
        a = vector(member(case="dc_a"), time_loss=10.0)
        stress = vector(member(case="dc_stress"), time_loss=1000.0)
        result = sm.weighted_expectation([a, stress], "total_time_loss_s",
                                         {"dc_a": 1.0})
        assert result == pytest.approx(10.0)

    def test_a_weighted_case_with_no_observation_is_refused(self):
        a = vector(member(case="dc_a"), time_loss=10.0)
        with pytest.raises(sm.PlanError, match="silently re-normalise"):
            sm.weighted_expectation([a], "total_time_loss_s",
                                    {"dc_a": 0.5, "dc_b": 0.5})


class TestMaxRegret:
    def test_regret_is_computed_within_a_shared_world(self):
        world_a = member(case="dc_a", seed=1000)
        world_b = member(case="dc_b", seed=1000)
        result = sm.max_regret({
            "closeX": [vector(world_a, time_loss=10.0),
                       vector(world_b, time_loss=30.0)],
            "closeY": [vector(world_a, time_loss=12.0),
                       vector(world_b, time_loss=20.0)],
        }, "total_time_loss_s")
        # In world A closeX is best (regret 0 vs 2); in world B closeY is.
        assert result["closeX"] == pytest.approx(10.0)
        assert result["closeY"] == pytest.approx(2.0)

    def test_a_world_with_one_candidate_yields_no_regret(self):
        result = sm.max_regret(
            {"closeX": [vector(member(case="dc_a"), time_loss=10.0)]},
            "total_time_loss_s")
        assert result["closeX"] != result["closeX"]      # NaN


# ── plan invariant 6: fail-closed decisions ───────────────────────────────
class TestDecisionIsFailClosed:
    def test_a_clear_best_wins(self):
        result = sm.decide(scores={"a": 1.0, "b": 5.0}, budget_complete=True)
        assert result.outcome == "unique_winner"
        assert result.winner == "a"

    def test_an_incomplete_budget_can_never_win(self):
        result = sm.decide(scores={"a": 1.0, "b": 5.0}, budget_complete=False)
        assert result.outcome == "inconclusive"
        assert result.winner is None

    def test_a_paused_run_can_never_win(self):
        result = sm.decide(scores={"a": 1.0, "b": 5.0}, budget_complete=True,
                           paused=True)
        assert result.outcome == "paused"
        assert result.winner is None

    def test_pause_outranks_a_complete_budget(self):
        """The budget check must not be reachable around a pause."""
        result = sm.decide(scores={"a": 1.0}, budget_complete=True, paused=True)
        assert result.outcome == "paused"

    def test_a_near_tie_is_a_tie(self):
        result = sm.decide(scores={"a": 1.0, "b": 1.005},
                           budget_complete=True, tie_threshold=0.01)
        assert result.outcome == "tie"
        assert result.winner is None

    def test_everything_disqualified_is_no_viable(self):
        result = sm.decide(scores={"a": 1.0}, budget_complete=True,
                           disqualified={"a": "severed"})
        assert result.outcome == "no_viable"
        assert result.winner is None

    def test_a_disqualified_candidate_cannot_win(self):
        result = sm.decide(scores={"a": 0.1, "b": 5.0}, budget_complete=True,
                           disqualified={"a": "no detour"})
        assert result.outcome == "unique_winner"
        assert result.winner == "b"

    def test_only_a_unique_winner_may_name_a_winner(self):
        with pytest.raises(sm.PlanError, match="must not name a winner"):
            sm.DecisionResult("tie", "a", "reason")

    def test_a_unique_winner_must_name_one(self):
        with pytest.raises(sm.PlanError, match="must name a winner"):
            sm.DecisionResult("unique_winner", None, "reason")

    def test_an_unknown_outcome_is_refused(self):
        with pytest.raises(sm.PlanError, match="outcome must be"):
            sm.DecisionResult("looks_fine", None, "reason")

    @pytest.mark.parametrize("outcome", ["tie", "inconclusive", "no_viable",
                                         "paused"])
    def test_no_non_winning_outcome_reads_as_a_winner(self, outcome):
        result = sm.DecisionResult(outcome, None, "reason")
        assert result.winner is None
        assert result.outcome != "unique_winner"
