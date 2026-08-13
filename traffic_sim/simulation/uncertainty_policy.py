"""Versioned risk and stability policy for finalist decisions (plan step 7).

The plan's Phase C rule, stated as code: reduce each scenario path to a
COMPLETE cost vector first, then apply risk measures to those vectors — never
assemble a synthetic worst world field by field. If different risk measures
prefer different candidates, the honest answer is ``tie`` or ``inconclusive``
until a business risk policy has been chosen and pre-registered. The code
must not hide that choice inside arbitrary weights.

Four quantities, each answering a different question:

``expectation``
    Weighted mean over probabilistic cases. The primary score.

``tail``
    Weighted upper quantile — what a bad-but-plausible day costs. Reported
    only when enough probabilistic cases exist to estimate it; with three
    cases a "q90" is the maximum wearing a percentile's name, so the policy
    refuses to compute one rather than publishing a reassuring fiction.

``max_regret``
    Robustness across worlds, computed within shared worlds only.

``stability``
    Does the answer stop moving as scenarios and seeds are added? Kaut &
    Wallace's point: a scenario ensemble is judged through the decision it
    supports, so an unstable ranking is not a result yet.

Stress cases never enter ``expectation`` or ``tail``. They feed a separate
robustness gate, because giving an unvalidated extreme a probability weight
is exactly how a stress test turns into a false forecast.

This module is pure policy — no SUMO, no I/O — so it is cheap to test and
cannot silently acquire a dependency on a running simulation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from traffic_sim.core.simulation_members import (CostVector, DecisionResult,
                                                 PlanError, decide,
                                                 max_regret,
                                                 require_same_fields,
                                                 weighted_expectation)

POLICY_VERSION = "closure_uncertainty_policy_v1"

#: Pre-registered scenario batch sizes. The search grows the ensemble in
#: these steps and checks whether the answer has stopped moving.
SCENARIO_BATCHES: tuple[int, ...] = (4, 8, 16)

#: Minimum seeds per case in the first finalist batch (FHWA asks for several
#: replications per alternative before any variation claim).
MIN_SEEDS = 4

#: Probabilistic cases required before a tail measure is meaningful.
MIN_CASES_FOR_TAIL = 8

#: Fraction of the objective's spread within which two candidates tie.
DEFAULT_TIE_FRACTION = 0.02


class PolicyError(ValueError):
    """Raised when a decision would violate the policy contract."""


@dataclass(frozen=True)
class CandidateScore:
    """Every risk measure for one candidate, plus what it is missing."""

    candidate: str
    expectation: float
    tail: float | None
    max_regret: float | None
    n_cases: int
    n_seeds: int
    hard_failures: int
    stress_failures: int = 0
    disqualified_reason: str | None = None

    @property
    def is_disqualified(self) -> bool:
        return self.disqualified_reason is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "expectation": _round(self.expectation),
            "tail": _round(self.tail),
            "max_regret": _round(self.max_regret),
            "n_cases": self.n_cases,
            "n_seeds": self.n_seeds,
            "hard_failures": self.hard_failures,
            "stress_failures": self.stress_failures,
            "disqualified_reason": self.disqualified_reason,
        }


def _round(value: float | None) -> float | None:
    if value is None or not isinstance(value, (int, float)):
        return None
    return None if not math.isfinite(float(value)) else round(float(value), 6)


def weighted_quantile(values: Sequence[float], weights: Sequence[float],
                      quantile: float) -> float:
    """Weighted quantile by cumulative mass, values ascending."""
    if not values:
        raise PolicyError("no values for a weighted quantile")
    pairs = sorted(zip(values, weights))
    total = sum(w for _v, w in pairs)
    if total <= 0:
        raise PolicyError("weights must carry positive mass")
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= quantile * total:
            return float(value)
    return float(pairs[-1][0])


def score_candidate(
    candidate: str,
    vectors: Sequence[CostVector],
    *,
    objective: str,
    case_weights: Mapping[str, float],
    stress_case_ids: Sequence[str] = (),
    tail_quantile: float = 0.9,
) -> CandidateScore:
    """Reduce one candidate's complete cost vectors to its risk measures."""
    if not vectors:
        raise PolicyError(f"candidate {candidate} has no observations")
    require_same_fields(vectors)

    stress = set(stress_case_ids)
    hard = [v for v in vectors if v.hard_failure]
    live = [v for v in vectors if not v.hard_failure]
    stress_failures = sum(1 for v in hard if v.member.demand_case_id in stress)

    probabilistic = [v for v in live if v.member.demand_case_id in case_weights]
    if not probabilistic:
        return CandidateScore(
            candidate=candidate, expectation=float("inf"), tail=None,
            max_regret=None, n_cases=0,
            n_seeds=len({v.member.simulation_seed for v in vectors}),
            hard_failures=len(hard), stress_failures=stress_failures,
            disqualified_reason="no surviving probabilistic observation",
        )

    # Average over seeds within a case first, so a case with more seeds does
    # not gain weight it was never assigned.
    by_case: dict[str, list[float]] = {}
    for vector in probabilistic:
        by_case.setdefault(vector.member.demand_case_id, []).append(
            vector.get(objective)
        )
    case_means = {case: sum(values) / len(values)
                  for case, values in by_case.items()}

    present_weights = {case: case_weights[case] for case in case_means}
    expectation = sum(present_weights[c] * m for c, m in case_means.items()) \
        / sum(present_weights.values())

    tail: float | None = None
    if len(case_means) >= MIN_CASES_FOR_TAIL:
        tail = weighted_quantile(list(case_means.values()),
                                 [present_weights[c] for c in case_means],
                                 tail_quantile)

    return CandidateScore(
        candidate=candidate,
        expectation=float(expectation),
        tail=tail,
        max_regret=None,                # filled in by the cross-candidate pass
        n_cases=len(case_means),
        n_seeds=len({v.member.simulation_seed for v in probabilistic}),
        hard_failures=len(hard),
        stress_failures=stress_failures,
    )


@dataclass(frozen=True)
class StabilityCheck:
    """Did the answer stop moving as the ensemble grew?"""

    scenario_stable: bool
    seed_stable: bool
    observed_batches: tuple[int, ...]
    winner_by_batch: Mapping[int, str | None]
    note: str = ""

    @property
    def is_stable(self) -> bool:
        return self.scenario_stable and self.seed_stable

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario_stable": self.scenario_stable,
            "seed_stable": self.seed_stable,
            "observed_batches": list(self.observed_batches),
            "winner_by_batch": {str(k): v
                                for k, v in self.winner_by_batch.items()},
            "note": self.note,
        }


def check_stability(winner_by_batch: Mapping[int, str | None],
                    *, min_seeds_seen: int) -> StabilityCheck:
    """Stable when the last two observed batches agree on the winner.

    One batch is never stability evidence: a single observation cannot show
    that a quantity has stopped changing.
    """
    batches = tuple(sorted(winner_by_batch))
    if len(batches) < 2:
        return StabilityCheck(
            scenario_stable=False, seed_stable=min_seeds_seen >= MIN_SEEDS,
            observed_batches=batches, winner_by_batch=dict(winner_by_batch),
            note=("fewer than two scenario batches were observed; a single "
                  "batch cannot show that the ranking has settled"),
        )
    last, previous = batches[-1], batches[-2]
    scenario_stable = (winner_by_batch[last] is not None
                       and winner_by_batch[last] == winner_by_batch[previous])
    return StabilityCheck(
        scenario_stable=scenario_stable,
        seed_stable=min_seeds_seen >= MIN_SEEDS,
        observed_batches=batches,
        winner_by_batch=dict(winner_by_batch),
        note=("" if scenario_stable else
              f"batch {previous} and batch {last} disagree on the winner"),
    )


@dataclass(frozen=True)
class PolicyResult:
    decision: DecisionResult
    scores: tuple[CandidateScore, ...]
    stability: StabilityCheck
    agreeing_measures: Mapping[str, str | None]
    policy_version: str = POLICY_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "outcome": self.decision.outcome,
            "winner": self.decision.winner,
            "reason": self.decision.reason,
            "scores": [s.to_json() for s in self.scores],
            "stability": self.stability.to_json(),
            "agreeing_measures": dict(self.agreeing_measures),
        }


def apply_policy(
    vectors_by_candidate: Mapping[str, Sequence[CostVector]],
    *,
    objective: str,
    case_weights: Mapping[str, float],
    stress_case_ids: Sequence[str] = (),
    disqualified: Mapping[str, str] | None = None,
    budget_complete: bool,
    paused: bool = False,
    winner_by_batch: Mapping[int, str | None] | None = None,
    tie_fraction: float = DEFAULT_TIE_FRACTION,
) -> PolicyResult:
    """The whole decision, fail-closed.

    Order matters and is deliberate. Scores are computed first so they can be
    reported whatever the outcome, then the gates run: a paused or incomplete
    run cannot publish a winner regardless of how clear the scores look, and
    disagreement between risk measures downgrades a would-be winner to a tie
    rather than silently picking the measure that happens to decide.
    """
    if not vectors_by_candidate:
        raise PolicyError("no candidates to score")

    scores: list[CandidateScore] = []
    for candidate, vectors in sorted(vectors_by_candidate.items()):
        scores.append(score_candidate(
            candidate, vectors, objective=objective,
            case_weights=case_weights, stress_case_ids=stress_case_ids,
        ))

    regrets = max_regret(
        {name: list(v) for name, v in vectors_by_candidate.items()}, objective
    )
    scores = [
        CandidateScore(
            candidate=s.candidate, expectation=s.expectation, tail=s.tail,
            max_regret=(None if not math.isfinite(regrets.get(s.candidate,
                                                              float("nan")))
                        else regrets[s.candidate]),
            n_cases=s.n_cases, n_seeds=s.n_seeds,
            hard_failures=s.hard_failures,
            stress_failures=s.stress_failures,
            disqualified_reason=s.disqualified_reason,
        )
        for s in scores
    ]

    disqualified = dict(disqualified or {})
    for score in scores:
        if score.is_disqualified:
            disqualified.setdefault(score.candidate,
                                    score.disqualified_reason or "disqualified")

    stability = check_stability(
        winner_by_batch or {},
        min_seeds_seen=min((s.n_seeds for s in scores), default=0),
    )

    live = {s.candidate: s.expectation for s in scores
            if s.candidate not in disqualified}
    spread = (max(live.values()) - min(live.values())) if len(live) > 1 else 0.0
    threshold = abs(spread) * tie_fraction

    decision = decide(scores=live, budget_complete=budget_complete,
                      paused=paused, disqualified=disqualified,
                      tie_threshold=threshold)

    agreeing = _preferred_by_measure(scores, disqualified)
    if decision.outcome == "unique_winner":
        chosen = {name for name in agreeing.values() if name is not None}
        if len(chosen) > 1:
            decision = DecisionResult(
                "tie", None,
                "risk measures disagree: "
                + ", ".join(f"{measure}->{name}"
                            for measure, name in sorted(agreeing.items())
                            if name is not None)
                + "; a business risk policy must be pre-registered before one "
                  "of them may decide",
                {"scores": dict(live), "disqualified": disqualified},
            )
        elif not stability.is_stable:
            decision = DecisionResult(
                "inconclusive", None,
                "the ranking has not demonstrably settled: "
                + (stability.note or
                   f"fewer than {MIN_SEEDS} seeds per case were observed"),
                {"scores": dict(live), "disqualified": disqualified},
            )

    return PolicyResult(decision=decision, scores=tuple(scores),
                        stability=stability, agreeing_measures=agreeing)


def _preferred_by_measure(scores: Sequence[CandidateScore],
                          disqualified: Mapping[str, str]
                          ) -> dict[str, str | None]:
    """Which candidate each risk measure prefers. Lower is better."""
    live = [s for s in scores if s.candidate not in disqualified]
    if not live:
        return {"expectation": None, "tail": None, "max_regret": None}

    def best(attr: str) -> str | None:
        usable = [s for s in live
                  if getattr(s, attr) is not None
                  and math.isfinite(float(getattr(s, attr)))]
        if not usable:
            return None
        return min(usable, key=lambda s: float(getattr(s, attr))).candidate

    return {"expectation": best("expectation"), "tail": best("tail"),
            "max_regret": best("max_regret")}


__all__ = [
    "POLICY_VERSION", "SCENARIO_BATCHES", "MIN_SEEDS", "MIN_CASES_FOR_TAIL",
    "PolicyError", "CandidateScore", "StabilityCheck", "PolicyResult",
    "score_candidate", "check_stability", "apply_policy", "weighted_quantile",
]
