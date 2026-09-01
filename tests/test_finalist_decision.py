import dataclasses
import json
from pathlib import Path

import pytest

from traffic_sim.simulation.finalist_decision import (
    RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
    RETRY_PROTOCOL_TWO_TIER_EXACT,
    TIMEOUT_IDENTITY_SCHEMA,
    CandidateEvidence,
    FinalistPolicy,
    PairedObservation,
    TimeoutIdentity,
    decide_finalists,
    paired_candidate_evidence,
)


def _timeout_identity(candidate_id="a", **overrides):
    values = {
        "schema": TIMEOUT_IDENTITY_SCHEMA,
        "candidate_id": candidate_id,
        "work_date": "2027-09-16",
        "search_content_key": "0" * 20,
        "variant": "q50",
        "seed": 1000,
        "attempt": 1,
        "threshold_s": 300.0,
        "retry_protocol": RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
        "search_provenance_key": "study-one",
    }
    values.update(overrides)
    return TimeoutIdentity(**values)
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


class TestUndecidedTimeoutEvidence:
    def test_defaults_to_no_undecided_timeout(self):
        evidence = CandidateEvidence(candidate_id="a")
        assert evidence.timeout_undecided == ()
        assert evidence.has_undecided_timeout is False

    def test_an_undecided_timeout_is_reported_without_faking_a_hard_failure(self):
        evidence = CandidateEvidence(
            candidate_id="a",
            timeout_undecided=(_timeout_identity("a"),),
        )
        assert evidence.has_undecided_timeout is True
        assert evidence.eligible is True
        assert evidence.hard_failures == ()

    def test_a_bare_string_timeout_entry_is_rejected(self):
        """The old v1/v2 wire format must fail closed, not be reinterpreted."""
        with pytest.raises(ValueError, match="TimeoutIdentity"):
            CandidateEvidence(
                candidate_id="a",
                timeout_undecided=("q50:1000:attempt1:threshold300s",),
            )


class TestTimeoutIdentity:
    def test_round_trips_through_to_dict_and_from_dict(self):
        identity = _timeout_identity("a")
        assert TimeoutIdentity.from_dict(identity.to_dict()) == identity

    def test_two_tier_terminal_attempt_round_trips(self):
        identity = _timeout_identity(
            "a",
            attempt=2,
            threshold_s=1800.0,
            retry_protocol=RETRY_PROTOCOL_TWO_TIER_EXACT,
        )
        assert TimeoutIdentity.from_dict(identity.to_dict()) == identity

    @pytest.mark.parametrize(("protocol", "attempt"), [
        (RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD, 2),
        (RETRY_PROTOCOL_TWO_TIER_EXACT, 1),
    ])
    def test_retry_protocol_rejects_impossible_attempt_number(
            self, protocol, attempt):
        with pytest.raises(ValueError, match="attempt"):
            _timeout_identity(
                "a", retry_protocol=protocol, attempt=attempt)

    def test_from_dict_rejects_a_bare_string(self):
        with pytest.raises(ValueError, match="not accepted"):
            TimeoutIdentity.from_dict("q50:1000:attempt1:threshold300s")

    def test_from_dict_rejects_a_record_missing_fields(self):
        raw = _timeout_identity("a").to_dict()
        del raw["search_provenance_key"]
        with pytest.raises(ValueError, match="missing fields"):
            TimeoutIdentity.from_dict(raw)

    @pytest.mark.parametrize(("field", "value", "message"), [
        ("seed", True, "seed must be a native integer"),
        ("seed", 1.9, "seed must be a native integer"),
        ("attempt", True, "attempt must be a native integer"),
        ("attempt", 1.9, "attempt must be a native integer"),
        ("threshold_s", "300", "threshold_s must be a native number"),
        ("retry_protocol", "retry_twice_v1", "retry_protocol is unsupported"),
        ("work_date", "2027-02-30", "ISO calendar date"),
        ("candidate_id", "", "non-empty native strings"),
        ("search_content_key", 123, "native string values"),
        ("search_provenance_key", "   ", "non-empty native strings"),
    ])
    def test_from_dict_rejects_malformed_native_fields(
            self, field, value, message):
        raw = _timeout_identity("a").to_dict()
        raw[field] = value
        with pytest.raises(ValueError, match=message):
            TimeoutIdentity.from_dict(raw)

    def test_from_dict_rejects_unknown_fields(self):
        raw = _timeout_identity("a").to_dict()
        raw["future_unvalidated_field"] = "must-not-be-ignored"
        with pytest.raises(ValueError, match="unknown fields"):
            TimeoutIdentity.from_dict(raw)

    def test_rejects_an_unknown_schema_tag(self):
        with pytest.raises(ValueError, match="unsupported timeout identity schema"):
            _timeout_identity("a", schema="timeout_v2")

    def test_rejects_a_non_q_variant(self):
        with pytest.raises(ValueError, match="variant"):
            _timeout_identity("a", variant="am_peak")

    def test_is_hashable_and_orderable(self):
        one = _timeout_identity("a", seed=1000)
        two = _timeout_identity("a", seed=1001)
        assert len({one, two}) == 2
        assert sorted([two, one]) == [one, two]


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

    def test_an_unresolved_timeout_makes_the_finalist_decision_inconclusive(self):
        """Regression for cost-order v5: a finalist that timed out during

        finalist-stage verification must not let the decision silently pick
        a winner from the remaining candidates as if the timed-out one had
        never existed.
        """
        timed_out = CandidateEvidence(
            candidate_id="timed-out",
            timeout_undecided=(_timeout_identity("timed-out"),),
        )
        result = decide_finalists(
            [
                timed_out,
                _candidate("valid", _all_variants([100.0] * 4)),
            ],
            _policy(),
        )
        assert result.status == "inconclusive"
        assert result.winner_id is None
        assert result.tie_ids == ()
        assert result.next_runs == ()
        assert "timeout" in result.reason.lower()

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


def _disruption(hours=0.0, metres=0.0, affected=0, no_detour=0):
    return {"added_vehicle_hours": hours, "added_metres_total": metres,
            "vehicles_affected": affected, "vehicles_no_detour": no_detour}


def _costed(candidate_id, deltas, disruption, **kw):
    """Evidence that additionally carries per-variant disruption records."""
    base = _candidate(candidate_id, deltas, **kw)
    return dataclasses.replace(base, disruption=tuple(disruption))


class TestClosureObjectiveRanking:
    """The ranking objective is displaced vehicles and detour, not simulated
    delay. Measured 2026-08-05: delta_time_loss_s on this network is noise
    (a real closure gave +0.050 s and -0.100 s per arm; congestion feedback
    converged at 0.0% change), while disruption separates candidates by
    orders of magnitude."""

    def test_disruption_decides_even_when_time_loss_disagrees(self):
        # 'cheap' looks WORSE on time loss but costs far fewer vehicle-hours.
        cheap = _costed("cheap", _all_variants([80.0, 80.0, 80.0, 80.0]),
                        [_disruption(hours=0.2, metres=100, affected=4_371)])
        dear = _costed("dear", _all_variants([10.0, 10.0, 10.0, 10.0]),
                       [_disruption(hours=20.6, metres=41_000, affected=8_427)])
        result = decide_finalists([cheap, dear], _policy())
        assert result.winner_id == "cheap", (
            "ranking must follow disruption, not the time-loss bound")

    def test_a_schedule_that_strands_drivers_is_refused_not_ranked(self):
        severing = _costed("severing", _all_variants([1.0, 1.0, 1.0, 1.0]),
                           [_disruption(hours=0.0, affected=5, no_detour=287)])
        ok = _costed("ok", _all_variants([50.0, 50.0, 50.0, 50.0]),
                     [_disruption(hours=20.6, metres=41_000, affected=8_427)])
        result = decide_finalists([severing, ok], _policy())
        assert result.winner_id == "ok", (
            "an unreachable destination is not 'a bit more delay' and must "
            "not win on near-zero vehicle-hours")
        refused = next(
            item for item in result.candidates
            if item.candidate_id == "severing"
        )
        assert refused.eligible is False
        assert refused.hard_failures == ("vehicles_no_detour",)

    def test_all_severing_yields_no_viable(self):
        a = _costed("a", _all_variants([1.0] * 4),
                    [_disruption(no_detour=3)])
        b = _costed("b", _all_variants([1.0] * 4),
                    [_disruption(no_detour=9)])
        result = decide_finalists([a, b], _policy())
        assert result.status == "no_viable"
        assert result.winner_id is None

    def test_candidates_without_disruption_keep_the_legacy_key(self):
        """Pre-objective campaigns must still decide, not rank as costless."""
        low = _candidate("low", _all_variants([10.0, 10.0, 10.0, 10.0]))
        high = _candidate("high", _all_variants([90.0, 90.0, 90.0, 90.0]))
        result = decide_finalists([low, high], _policy())
        assert result.winner_id == "low"

    def test_mixed_evidence_falls_back_rather_than_mixing_scales(self):
        costed = _costed("costed", _all_variants([90.0] * 4),
                         [_disruption(hours=0.1, affected=10)])
        plain = _candidate("plain", _all_variants([10.0] * 4))
        result = decide_finalists([costed, plain], _policy())
        assert result.winner_id == "plain", (
            "vehicle-hours and seconds are different scales; a partially "
            "costed field must not be ranked as if they were comparable")

    def test_worst_variant_is_taken_across_direction_splits(self):
        a = _costed("a", _all_variants([1.0] * 4),
                    [_disruption(hours=1.0, affected=10),
                     _disruption(hours=9.0, affected=10),
                     _disruption(hours=2.0, affected=10)])
        b = _costed("b", _all_variants([1.0] * 4),
                    [_disruption(hours=5.0, affected=10)])
        result = decide_finalists([a, b], _policy())
        assert result.winner_id == "b", (
            "a must be judged on its worst variant (9.0), not its best")

    def test_exact_vehicle_hour_tie_uses_added_distance(self):
        short = _costed("short", _all_variants([1.0] * 4), [
            _disruption(hours=1.0, metres=100, affected=20)
        ])
        long = _costed("long", _all_variants([1.0] * 4), [
            _disruption(hours=1.0, metres=10_000, affected=2)
        ])

        result = decide_finalists(
            [long, short], _policy(), ranking_objective="closure_cost_v1"
        )

        assert result.status == "unique_winner"
        assert result.winner_id == "short"

    def test_explicit_closure_objective_rejects_mixed_evidence(self):
        costed = _costed("costed", _all_variants([90.0] * 4), [
            _disruption(hours=0.1, affected=10)
        ])
        plain = _candidate("plain", _all_variants([10.0] * 4))

        with pytest.raises(ValueError, match="requires disruption evidence"):
            decide_finalists(
                [costed, plain],
                _policy(),
                ranking_objective="closure_cost_v1",
            )
