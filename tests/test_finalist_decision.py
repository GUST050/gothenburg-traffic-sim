import dataclasses
import json
from pathlib import Path

import pytest

from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    FinalistPolicy,
    PairedObservation,
    decide_finalists,
    paired_candidate_evidence,
)
from traffic_sim.simulation.micro_confirmation import (
    MicroContext,
    MicroResult,
    plan_micro_confirmation,
)


def _policy(**overrides):
    values = {
        "absolute_precision_floor_s": 10.0,
        "practical_equivalence_s": 20.0,
        "max_repetitions": 6,
    }
    values.update(overrides)
    return FinalistPolicy(**values)


def _candidate(
    candidate_id,
    deltas,
    *,
    failures=(),
    baseline=1000.0,
    provenance="study-one",
    matched_baseline="baseline-one",
    seed_start=1000,
):
    """Build variant -> deltas evidence with the same seed pairs."""
    observations = []
    for variant, values in deltas.items():
        for offset, delta in enumerate(values):
            observations.append(
                PairedObservation(
                    candidate_id=candidate_id,
                    demand_variant=variant,
                    seed=seed_start + offset,
                    baseline_time_loss_s=baseline,
                    candidate_time_loss_s=baseline + delta,
                    matched_baseline_id=matched_baseline,
                    provenance_key=provenance,
                )
            )
    return CandidateEvidence(
        candidate_id=candidate_id,
        observations=tuple(observations),
        hard_failures=tuple(failures),
    )


def _all_variants(q10, q50=None, q90=None):
    return {
        "q10": q10,
        "q50": q10 if q50 is None else q50,
        "q90": q10 if q90 is None else q90,
    }


class TestPairedCandidateEvidence:
    def test_pairs_by_variant_and_seed_not_list_position(self):
        baseline = [
            {"demand_variant": "q10", "seed": 10, "total_time_loss_s": 100},
            {"demand_variant": "q50", "seed": 20, "total_time_loss_s": 200},
            {"demand_variant": "q90", "seed": 30, "total_time_loss_s": 300},
        ]
        candidate = [
            {"demand_variant": "q90", "seed": 30, "total_time_loss_s": 303},
            {"demand_variant": "q10", "seed": 10, "total_time_loss_s": 101},
            {"demand_variant": "q50", "seed": 20, "total_time_loss_s": 202},
        ]
        evidence = paired_candidate_evidence(
            "candidate",
            baseline_records=baseline,
            candidate_records=candidate,
            matched_baseline_id="baseline",
            provenance_key="study",
        )
        assert [
            (
                observation.demand_variant,
                observation.seed,
                observation.delta_time_loss_s,
            )
            for observation in evidence.observations
        ] == [
            ("q10", 10, 1),
            ("q50", 20, 2),
            ("q90", 30, 3),
        ]

    def test_rejects_nonidentical_pair_sets(self):
        with pytest.raises(ValueError, match="replication identities differ"):
            paired_candidate_evidence(
                "candidate",
                baseline_records=[
                    {
                        "demand_variant": "q50",
                        "seed": 10,
                        "total_time_loss_s": 100,
                    }
                ],
                candidate_records=[
                    {
                        "demand_variant": "q50",
                        "seed": 11,
                        "total_time_loss_s": 100,
                    }
                ],
                matched_baseline_id="baseline",
                provenance_key="study",
            )

    def test_rejects_duplicate_pair_identity(self):
        duplicate = {
            "demand_variant": "q50",
            "seed": 10,
            "total_time_loss_s": 100,
        }
        with pytest.raises(ValueError, match="duplicate replication identity"):
            paired_candidate_evidence(
                "candidate",
                baseline_records=[duplicate, duplicate],
                candidate_records=[duplicate],
                matched_baseline_id="baseline",
                provenance_key="study",
            )


class TestRobustFinalistDecision:
    def test_unique_winner_requires_practical_and_statistical_separation(self):
        evidence = [
            _candidate("early", _all_variants([100.0] * 4)),
            _candidate("late", _all_variants([200.0] * 4)),
        ]
        result = decide_finalists(evidence, _policy())

        assert result.status == "unique_winner"
        assert result.winner_id == "early"
        assert result.tie_ids == ()
        assert result.next_runs == ()
        assert result.simultaneous_comparisons == 6
        payload = result.to_dict()
        json.dumps(payload, allow_nan=False)
        assert payload["policy"]["initial_repetitions"] == 4
        assert payload["policy"]["absolute_precision_floor_s"] == 10.0

    def test_real_gothenburg_smoke_record_remains_fail_closed(self):
        record = json.loads(
            Path(
                "validation/robust_closure_search_smoke_v1.json"
            ).read_text()
        )
        assert record["status"] == "passing"
        assert record["decision"]["status"] == "unique_winner"
        assert record["decision"]["matched_seed_count"] == 12
        assert record["decision"]["pairs_per_variant"] == 4
        assert record["decision"]["precision_met"] is True
        assert record["claim_boundary"]["best_result_available"] is True
        assert (
            record["claim_boundary"]["global_best_claim_allowed"]
            is False
        )

    def test_worst_variant_upper_bound_is_the_primary_objective(self):
        evidence = [
            _candidate(
                "fragile",
                _all_variants(
                    [5.0] * 4,
                    q50=[5.0] * 4,
                    q90=[300.0] * 4,
                ),
            ),
            _candidate("robust", _all_variants([100.0] * 4)),
        ]
        result = decide_finalists(evidence, _policy())
        stats = {item.candidate_id: item for item in result.candidates}

        assert result.status == "unique_winner"
        assert result.winner_id == "robust"
        assert stats["fragile"].worst_variant == "q90"
        assert stats["fragile"].robust_upper_95_s == 300.0

    def test_variants_are_not_pooled_to_manufacture_precision(self):
        result = decide_finalists(
            [
                _candidate(
                    "only-q50",
                    {"q50": [10.0, 11.0, 9.0, 10.0]},
                )
            ],
            _policy(),
        )

        assert result.status == "inconclusive"
        assert {(request.demand_variant, request.repetitions_to_add)
                for request in result.next_runs} == {
            ("q10", 4),
            ("q90", 4),
        }

    def test_practically_equivalent_overlapping_candidates_return_tie(self):
        result = decide_finalists(
            [
                _candidate("a", _all_variants([100.0] * 4)),
                _candidate("b", _all_variants([112.0] * 4)),
            ],
            _policy(practical_equivalence_s=15.0),
        )

        assert result.status == "tie"
        assert result.winner_id is None
        assert result.tie_ids == ("a", "b")

    def test_close_point_estimates_are_not_a_tie_when_bounds_are_too_wide(self):
        result = decide_finalists(
            [
                _candidate(
                    "a",
                    _all_variants([0.0, 200.0, 0.0, 200.0, 0.0, 200.0]),
                ),
                _candidate(
                    "b",
                    _all_variants([5.0, 205.0, 5.0, 205.0, 5.0, 205.0]),
                ),
            ],
            _policy(
                practical_equivalence_s=15.0,
                absolute_precision_floor_s=1.0,
            ),
        )

        assert result.status == "inconclusive"
        assert result.tie_ids == ()

    def test_unresolved_non_equivalent_comparison_is_inconclusive_at_cap(self):
        noisy_a = [0.0, 200.0, 0.0, 200.0, 0.0, 200.0]
        noisy_b = [40.0, 240.0, 40.0, 240.0, 40.0, 240.0]
        result = decide_finalists(
            [
                _candidate("a", _all_variants(noisy_a)),
                _candidate("b", _all_variants(noisy_b)),
            ],
            _policy(
                practical_equivalence_s=10.0,
                absolute_precision_floor_s=1.0,
            ),
        )

        assert result.status == "inconclusive"
        assert result.next_runs == ()
        assert "repetition cap" in result.reason

    def test_tie_does_not_hide_a_third_unresolved_non_equivalent_contender(self):
        result = decide_finalists(
            [
                _candidate("a", _all_variants([100.0] * 6)),
                _candidate("b", _all_variants([108.0] * 6)),
                _candidate(
                    "uncertain",
                    _all_variants([0.0, 300.0, 0.0, 300.0, 0.0, 300.0]),
                ),
            ],
            _policy(
                practical_equivalence_s=15.0,
                absolute_precision_floor_s=1.0,
            ),
        )

        assert result.status == "inconclusive"
        assert result.tie_ids == ()

    def test_missing_initial_repetitions_request_exact_additions(self):
        result = decide_finalists(
            [_candidate("a", _all_variants([10.0, 11.0]))],
            _policy(),
        )

        assert result.status == "inconclusive"
        assert len(result.next_runs) == 3
        assert all(request.repetitions_to_add == 2 for request in result.next_runs)
        assert all(request.completed_repetitions == 2 for request in result.next_runs)

    def test_no_viable_is_structural_and_never_ranks_low_delay(self):
        result = decide_finalists(
            [
                _candidate(
                    "artificially-fast",
                    _all_variants([-500.0] * 4),
                    failures=("dropped_unreachable_vehicles",),
                ),
                _candidate(
                    "leaking",
                    _all_variants([-1000.0] * 4),
                    failures=("active_closure_edge_throughput",),
                ),
            ],
            _policy(),
        )

        assert result.status == "no_viable"
        assert result.winner_id is None
        assert result.tie_ids == ()
        assert result.next_runs == ()
        assert all(not item.eligible for item in result.candidates)

    def test_hard_failed_candidate_is_removed_before_ranking(self):
        result = decide_finalists(
            [
                _candidate(
                    "invalid-fast",
                    _all_variants([-1000.0] * 4),
                    failures=("teleports",),
                ),
                _candidate("valid", _all_variants([100.0] * 4)),
            ],
            _policy(),
        )

        assert result.status == "unique_winner"
        assert result.winner_id == "valid"

    def test_different_date_envelopes_may_use_different_matched_baselines(self):
        result = decide_finalists(
            [
                _candidate("a", _all_variants([10.0] * 4)),
                _candidate(
                    "b",
                    _all_variants([100.0] * 4),
                    matched_baseline="different-date-baseline",
                    baseline=5000.0,
                ),
            ],
            _policy(),
        )
        assert result.status == "unique_winner"
        assert result.winner_id == "a"

    def test_same_envelope_baseline_value_must_match(self):
        with pytest.raises(ValueError, match="same envelope"):
            decide_finalists(
                [
                    _candidate("a", _all_variants([10.0] * 4)),
                    _candidate(
                        "b",
                        _all_variants([20.0] * 4),
                        baseline=5000.0,
                    ),
                ],
                _policy(),
            )

    def test_same_envelope_common_seed_prefix_must_match(self):
        with pytest.raises(ValueError, match="common seed"):
            decide_finalists(
                [
                    _candidate("a", _all_variants([10.0] * 4)),
                    _candidate(
                        "b",
                        _all_variants([20.0] * 4),
                        seed_start=2000,
                    ),
                ],
                _policy(),
            )

    def test_one_candidate_cannot_span_multiple_baseline_envelopes(self):
        candidate = _candidate("a", _all_variants([10.0] * 4))
        changed = dataclasses.replace(
            candidate.observations[-1],
            matched_baseline_id="another-envelope",
        )
        mixed = CandidateEvidence(
            candidate_id="a",
            observations=candidate.observations[:-1] + (changed,),
        )
        with pytest.raises(ValueError, match="multiple matched baseline"):
            decide_finalists([mixed], _policy())

    def test_cross_candidate_scenario_provenance_must_match(self):
        with pytest.raises(ValueError, match="provenance"):
            decide_finalists(
                [
                    _candidate("a", _all_variants([10.0] * 4)),
                    _candidate(
                        "b",
                        _all_variants([20.0] * 4),
                        provenance="different-study",
                    ),
                ],
                _policy(),
            )

    def test_duplicate_variant_seed_pair_is_rejected(self):
        candidate = _candidate("a", _all_variants([10.0] * 4))
        duplicate = CandidateEvidence(
            candidate_id="a",
            observations=candidate.observations + (candidate.observations[0],),
        )
        with pytest.raises(ValueError, match="duplicate variant/seed"):
            decide_finalists([duplicate], _policy())

    def test_microscopic_observation_cannot_enter_meso_ranking(self):
        with pytest.raises(ValueError, match="must be mesoscopic"):
            PairedObservation(
                candidate_id="a",
                demand_variant="q50",
                seed=1000,
                baseline_time_loss_s=100.0,
                candidate_time_loss_s=110.0,
                matched_baseline_id="base",
                provenance_key="study",
                simulation_mode="micro",
            )


class TestConditionalMicroscopicConfirmation:
    def _decision(self, *, tie=False):
        second = 112.0 if tie else 200.0
        return decide_finalists(
            [
                _candidate("a", _all_variants([100.0] * 4)),
                _candidate("b", _all_variants([second] * 4)),
            ],
            _policy(practical_equivalence_s=15.0),
        )

    def test_no_trigger_keeps_micro_bounded_and_not_required(self):
        confirmation = plan_micro_confirmation(
            self._decision(),
            {},
            micro_available=True,
        )

        assert confirmation.status == "not_required"
        assert confirmation.recommendation_allowed is True
        assert confirmation.queue_detail_status == "mesoscopic_diagnostic_only"

    def test_near_signal_without_micro_is_explicitly_not_assessed(self):
        confirmation = plan_micro_confirmation(
            self._decision(),
            {"a": MicroContext(candidate_id="a", near_signal=True)},
            micro_available=False,
        )

        assert confirmation.status == "queue_detail_not_assessed"
        assert confirmation.trigger_reasons == ("near_signal",)
        assert confirmation.recommendation_allowed is False
        assert confirmation.missing_candidate_ids == ("a", "b")

    def test_tie_itself_triggers_top_finalist_confirmation(self):
        confirmation = plan_micro_confirmation(
            self._decision(tie=True),
            {},
            micro_available=True,
        )

        assert confirmation.status == "required"
        assert "mesoscopic_candidates_too_close" in confirmation.trigger_reasons
        assert confirmation.candidate_ids == ("a", "b")

    def test_healthy_micro_results_confirm_without_changing_meso_winner(self):
        decision = self._decision()
        confirmation = plan_micro_confirmation(
            decision,
            {"a": MicroContext(candidate_id="a", known_bottleneck=True)},
            micro_available=True,
            results=[
                MicroResult("a", "study-one", True, True, True, True),
                MicroResult("b", "study-one", True, True, True, True),
            ],
        )

        assert decision.winner_id == "a"
        assert confirmation.status == "confirmed"
        assert confirmation.recommendation_allowed is True
        assert confirmation.failed_candidate_ids == ()

    def test_spillback_failure_suppresses_recommendation(self):
        confirmation = plan_micro_confirmation(
            self._decision(),
            {"a": MicroContext(candidate_id="a", near_roundabout=True)},
            micro_available=True,
            results=[
                MicroResult("a", "study-one", True, True, True, False),
                MicroResult("b", "study-one", True, True, True, True),
            ],
        )

        assert confirmation.status == "failed"
        assert confirmation.recommendation_allowed is False
        assert confirmation.failed_candidate_ids == ("a",)

    def test_unassessed_spillback_remains_explicit(self):
        confirmation = plan_micro_confirmation(
            self._decision(),
            {"a": MicroContext(candidate_id="a", near_signal=True)},
            micro_available=True,
            results=[
                MicroResult("a", "study-one", True, True, False, None),
                MicroResult("b", "study-one", True, True, True, True),
            ],
        )

        assert confirmation.status == "queue_detail_not_assessed"
        assert confirmation.queue_detail_status == "queue_detail_not_assessed"
        assert confirmation.recommendation_allowed is False
        assert confirmation.missing_candidate_ids == ("a",)

    def test_micro_result_must_match_meso_provenance(self):
        with pytest.raises(ValueError, match="does not match mesoscopic"):
            plan_micro_confirmation(
                self._decision(),
                {"a": MicroContext(candidate_id="a", near_signal=True)},
                micro_available=True,
                results=[
                    MicroResult("a", "other-study", True, True, True, True),
                    MicroResult("b", "other-study", True, True, True, True),
                ],
            )

    def test_stale_micro_result_for_non_finalist_is_rejected(self):
        with pytest.raises(ValueError, match="selected finalist"):
            plan_micro_confirmation(
                self._decision(),
                {"a": MicroContext(candidate_id="a", near_signal=True)},
                micro_available=True,
                results=[
                    MicroResult("stale", "study-one", True, True, True, True),
                ],
            )
