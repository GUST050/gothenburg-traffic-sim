import pytest

from traffic_sim.simulation.finalist_decision import (
    RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
    TIMEOUT_IDENTITY_SCHEMA,
    CandidateEvidence,
    PairedObservation,
    TimeoutIdentity,
)
from traffic_sim.simulation.pilot_selection import (
    PilotPolicy,
    select_pilot_finalists,
)


def _policy(**overrides):
    values = {
        "retention_band_s": 10.0,
        "repetitions_per_variant": 1,
        "minimum_finalists": 2,
        "maximum_finalists": 4,
    }
    values.update(overrides)
    return PilotPolicy(**values)


def _candidate(
    candidate_id,
    deltas=(10.0, 20.0, 30.0),
    *,
    seeds=(1000, 1001, 1002),
    provenance="study-one",
    baseline_id="baseline-one",
    baseline=100.0,
    failures=(),
):
    if failures:
        return CandidateEvidence(
            candidate_id=candidate_id,
            hard_failures=tuple(failures),
        )
    observations = tuple(
        PairedObservation(
            candidate_id=candidate_id,
            demand_variant=variant,
            seed=seed,
            baseline_time_loss_s=baseline,
            candidate_time_loss_s=baseline + delta,
            matched_baseline_id=baseline_id,
            provenance_key=provenance,
        )
        for variant, seed, delta in zip(
            ("q10", "q50", "q90"),
            seeds,
            deltas,
        )
    )
    return CandidateEvidence(
        candidate_id=candidate_id,
        observations=observations,
    )


def _costed(
    candidate_id, deltas, *, hours, metres=0, affected=0, no_detour=0
):
    candidate = _candidate(candidate_id, deltas)
    return CandidateEvidence(
        candidate_id=candidate.candidate_id,
        observations=candidate.observations,
        disruption=({
            "added_vehicle_hours": hours,
            "added_metres_total": metres,
            "vehicles_affected": affected,
            "vehicles_no_detour": no_detour,
        },),
    )


def test_retains_minimum_and_candidates_inside_worst_variant_band():
    result = select_pilot_finalists(
        [
            _candidate("a", (1, 2, 10)),
            _candidate("b", (2, 3, 20)),
            _candidate("c", (3, 4, 25)),
            _candidate("d", (4, 5, 31)),
        ],
        _policy(),
    )
    assert result.status == "ready"
    assert result.selected_ids == ("a", "b", "c")
    assert result.screening_only is True
    assert result.global_best_claim_allowed is False


def test_pilot_score_is_worst_demand_variant_not_pooled_average():
    result = select_pilot_finalists(
        [
            _candidate("stable", (20, 20, 20)),
            _candidate("fragile", (-100, -100, 40)),
        ],
        _policy(retention_band_s=0, minimum_finalists=1),
    )
    assert result.selected_ids == ("stable",)
    fragile = next(
        item for item in result.candidates if item.candidate_id == "fragile"
    )
    assert fragile.worst_variant == "q90"
    assert fragile.worst_variant_delta_s == 40


def test_closure_cost_objective_prevents_pilot_from_discarding_true_best():
    result = select_pilot_finalists(
        [
            _costed("cheap", (100, 100, 100), hours=1.0),
            _costed("dear", (1, 1, 1), hours=10.0),
        ],
        _policy(retention_band_s=0, minimum_finalists=1),
        ranking_objective="closure_cost_v1",
    )

    assert result.selected_ids == ("cheap",)
    assert result.method == "deterministic_closure_cost_pilot_v1"


def test_explicit_closure_cost_objective_fails_closed_without_disruption():
    with pytest.raises(ValueError, match="requires disruption evidence"):
        select_pilot_finalists(
            [_candidate("plain")],
            _policy(minimum_finalists=1),
            ranking_objective="closure_cost_v1",
        )


def test_closure_cost_pilot_marks_no_detour_candidate_ineligible():
    result = select_pilot_finalists(
        [
            _costed(
                "severing", (1, 1, 1), hours=0, no_detour=1
            ),
            _costed("ok", (100, 100, 100), hours=10),
        ],
        _policy(minimum_finalists=1),
        ranking_objective="closure_cost_v1",
    )

    assert result.selected_ids == ("ok",)
    refused = next(
        item for item in result.candidates
        if item.candidate_id == "severing"
    )
    assert refused.eligible is False
    assert refused.hard_failures == ("vehicles_no_detour",)
    assert refused.closure_cost is not None
    assert refused.closure_cost.vehicles_no_detour == 1
    accepted = next(
        item for item in result.candidates if item.candidate_id == "ok"
    )
    assert accepted.closure_cost is not None
    assert accepted.closure_cost.added_vehicle_hours == 10


def test_missing_variant_is_incomplete_and_requests_exact_pair():
    candidate = _candidate("a")
    candidate = CandidateEvidence(
        candidate_id="a",
        observations=candidate.observations[:-1],
    )
    result = select_pilot_finalists([candidate], _policy())
    assert result.status == "incomplete"
    assert result.selected_ids == ()
    assert result.missing_pairs == (("a", "q90", 0),)


def test_mismatched_common_random_number_pair_is_rejected():
    with pytest.raises(ValueError, match="same envelope"):
        select_pilot_finalists(
            [
                _candidate("a"),
                _candidate("b", seeds=(1000, 1001, 9002)),
            ],
            _policy(),
        )


def test_different_date_envelopes_may_use_different_matched_baselines():
    result = select_pilot_finalists(
        [
            _candidate("a"),
            _candidate(
                "b",
                baseline_id="baseline-other-date",
                baseline=500.0,
            ),
        ],
        _policy(),
    )
    assert result.status == "ready"
    assert result.selected_ids == ("a", "b")


def test_different_study_provenance_is_rejected_across_envelopes():
    with pytest.raises(ValueError, match="study provenance"):
        select_pilot_finalists(
            [
                _candidate("a"),
                _candidate(
                    "b",
                    baseline_id="baseline-other-date",
                    provenance="different-study",
                ),
            ],
            _policy(),
        )


def test_more_observations_than_frozen_pilot_budget_is_rejected():
    candidate = _candidate("a")
    extra = PairedObservation(
        candidate_id="a",
        demand_variant="q10",
        seed=2000,
        baseline_time_loss_s=100,
        candidate_time_loss_s=110,
        matched_baseline_id="baseline-one",
        provenance_key="study-one",
    )
    with pytest.raises(ValueError, match="more than the frozen"):
        select_pilot_finalists(
            [
                CandidateEvidence(
                    candidate_id="a",
                    observations=candidate.observations + (extra,),
                )
            ],
            _policy(),
        )


def test_capacity_exceeded_fails_closed_instead_of_arbitrary_truncation():
    result = select_pilot_finalists(
        [
            _candidate("a", (1, 1, 1)),
            _candidate("b", (2, 2, 2)),
            _candidate("c", (3, 3, 3)),
        ],
        _policy(
            retention_band_s=100,
            minimum_finalists=1,
            maximum_finalists=2,
        ),
    )
    assert result.status == "capacity_exceeded"
    assert result.selected_ids == ()


def test_hard_failed_candidates_are_removed_before_pilot_ranking():
    result = select_pilot_finalists(
        [
            _candidate("bad", failures=("teleport_detected",)),
            _candidate("good"),
        ],
        _policy(minimum_finalists=1),
    )
    assert result.status == "ready"
    assert result.selected_ids == ("good",)


def test_every_hard_failed_candidate_returns_no_viable():
    result = select_pilot_finalists(
        [
            _candidate("a", failures=("access_loss",)),
            _candidate("b", failures=("teleport_detected",)),
        ],
        _policy(),
    )
    assert result.status == "no_viable"
    assert result.selected_ids == ()


def _timed_out(candidate_id, *, message="sumo timed out after 300s (seed 1000)"):
    return CandidateEvidence(
        candidate_id=candidate_id,
        hard_failures=(f"sumo_execution_failure:q50:1000:{message}",),
        timeout_undecided=(
            TimeoutIdentity(
                schema=TIMEOUT_IDENTITY_SCHEMA,
                candidate_id=candidate_id,
                work_date="2027-09-16",
                search_content_key="0" * 20,
                variant="q50",
                seed=1000,
                attempt=1,
                threshold_s=300.0,
                retry_protocol=RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
                search_provenance_key="study-one",
            ),
        ),
    )


def test_an_unresolved_timeout_anywhere_makes_the_pilot_inconclusive():
    """Regression for cost-order v5: a timed-out candidate must never be

    silently treated as if it had never existed. Even though it is
    ineligible (like any hard failure), its presence means the search
    cannot prove the retained set is what it would have been had the run
    completed, so the whole decision must say so rather than quietly
    proceeding to pick finalists from the other candidates.
    """
    result = select_pilot_finalists(
        [
            _timed_out("timeout-candidate"),
            _candidate("a"),
            _candidate("b"),
            _candidate("c"),
        ],
        _policy(minimum_finalists=1),
    )
    assert result.status == "inconclusive"
    assert result.selected_ids == ()
    assert "timeout" in result.reason.lower()
    statistics_by_id = {item.candidate_id: item for item in result.candidates}
    published = statistics_by_id["timeout-candidate"].timeout_undecided
    assert len(published) == 1
    assert published[0].candidate_id == "timeout-candidate"
    assert published[0].variant == "q50"
    assert published[0].seed == 1000


def test_an_unresolved_timeout_outranks_capacity_and_completeness_status():
    """The fail-closed rule fires before any other status determination."""
    result = select_pilot_finalists(
        [_timed_out("only-candidate")],
        _policy(),
    )
    assert result.status == "inconclusive"


def test_ordinary_hard_failures_without_a_timeout_are_unaffected():
    """A genuine disqualification (no timeout involved) still proceeds

    exactly as before — the new gate only fires on an unresolved timeout,
    never on an ordinary SUMO failure or deterministic disqualification.
    """
    result = select_pilot_finalists(
        [
            _candidate("bad", failures=("teleport_detected",)),
            _candidate("good"),
        ],
        _policy(minimum_finalists=1),
    )
    assert result.status == "ready"
    assert result.selected_ids == ("good",)
