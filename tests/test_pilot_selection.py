import pytest

from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    PairedObservation,
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
            baseline_time_loss_s=100.0,
            candidate_time_loss_s=100.0 + delta,
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
    with pytest.raises(ValueError, match="same matched baseline/provenance"):
        select_pilot_finalists(
            [
                _candidate("a"),
                _candidate("b", seeds=(1000, 1001, 9002)),
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

