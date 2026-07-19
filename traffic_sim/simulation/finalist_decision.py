"""Fail-closed robust decisions for matched SUMO closure finalists.

This module deliberately does not run SUMO.  It is the pure decision seam
between a simulation orchestrator and the eventual API/UI:

* q10/q50/q90 are kept as separate epistemic demand variants;
* every observation is an explicit candidate/baseline pair with the same
  seed and provenance;
* hard-gated candidates are removed before statistics are calculated;
* the score is the worst-variant upper simultaneous 95% confidence bound;
* an answer may be a unique winner, a practical tie, inconclusive, or no
  viable closure;
* microscopic evidence is a separate conditional confirmation and is never
  folded into the mesoscopic score.

The caller owns adaptive SUMO execution.  ``DecisionResult.next_runs`` says
exactly which candidate/variant pairs still need matched repetitions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import mean, median, stdev
from typing import Any, Mapping, Sequence

from scipy.stats import t as student_t


SCHEMA_VERSION = 1
DECISION_METHOD = "paired_worst_variant_ucb_v1"
DEMAND_VARIANTS = ("q10", "q50", "q90")
_DECISION_STATUSES = frozenset(
    {"unique_winner", "tie", "inconclusive", "no_viable"}
)


def _finite(value: float, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


@dataclass(frozen=True)
class FinalistPolicy:
    """Pre-registered repetition and decision tolerances.

    ``absolute_precision_floor_s`` and ``max_repetitions`` intentionally
    have no defaults.  The release process must freeze them from a named
    benchmark rather than silently inheriting a convenient value.
    ``relative_precision`` is the maximum confidence half-width as a share
    of the paired mean impact; the absolute floor handles impacts near zero.
    """

    absolute_precision_floor_s: float
    practical_equivalence_s: float
    max_repetitions: int
    initial_repetitions: int = 4
    confidence_level: float = 0.95
    relative_precision: float = 0.05
    variants: tuple[str, ...] = DEMAND_VARIANTS
    micro_finalist_limit: int = 3

    def __post_init__(self) -> None:
        if _finite(
            self.absolute_precision_floor_s, "absolute_precision_floor_s"
        ) <= 0:
            raise ValueError("absolute_precision_floor_s must be above zero")
        if _finite(self.practical_equivalence_s, "practical_equivalence_s") < 0:
            raise ValueError("practical_equivalence_s cannot be negative")
        if (
            isinstance(self.initial_repetitions, bool)
            or self.initial_repetitions < 2
        ):
            raise ValueError("initial_repetitions must be at least two")
        if (
            isinstance(self.max_repetitions, bool)
            or self.max_repetitions < self.initial_repetitions
        ):
            raise ValueError(
                "max_repetitions must be at least initial_repetitions"
            )
        if not 0 < _finite(self.confidence_level, "confidence_level") < 1:
            raise ValueError("confidence_level must be between zero and one")
        if not 0 < _finite(self.relative_precision, "relative_precision") < 1:
            raise ValueError("relative_precision must be between zero and one")
        if (
            not self.variants
            or len(set(self.variants)) != len(self.variants)
            or any(variant not in DEMAND_VARIANTS for variant in self.variants)
        ):
            raise ValueError("variants must be unique q10/q50/q90 identities")
        if (
            isinstance(self.micro_finalist_limit, bool)
            or not 2 <= self.micro_finalist_limit <= 3
        ):
            raise ValueError("micro_finalist_limit must be two or three")


@dataclass(frozen=True)
class PairedObservation:
    """One matched no-closure/candidate SUMO result."""

    candidate_id: str
    demand_variant: str
    seed: int
    baseline_time_loss_s: float
    candidate_time_loss_s: float
    matched_baseline_id: str
    provenance_key: str
    simulation_mode: str = "meso"

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if self.demand_variant not in DEMAND_VARIANTS:
            raise ValueError("demand_variant must be q10, q50, or q90")
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or self.seed < 0
        ):
            raise ValueError("seed must be a non-negative integer")
        _finite(self.baseline_time_loss_s, "baseline_time_loss_s")
        _finite(self.candidate_time_loss_s, "candidate_time_loss_s")
        if not self.matched_baseline_id or not self.provenance_key:
            raise ValueError("matched baseline and provenance are required")
        if self.simulation_mode != "meso":
            raise ValueError("finalist ranking observations must be mesoscopic")

    @property
    def delta_time_loss_s(self) -> float:
        return self.candidate_time_loss_s - self.baseline_time_loss_s


@dataclass(frozen=True)
class CandidateEvidence:
    candidate_id: str
    observations: tuple[PairedObservation, ...] = ()
    hard_failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if any(
            observation.candidate_id != self.candidate_id
            for observation in self.observations
        ):
            raise ValueError("observation candidate_id mismatch")
        if any(not reason for reason in self.hard_failures):
            raise ValueError("hard failure names cannot be empty")

    @property
    def eligible(self) -> bool:
        return not self.hard_failures


def paired_candidate_evidence(
    candidate_id: str,
    *,
    baseline_records: Sequence[Mapping[str, Any]],
    candidate_records: Sequence[Mapping[str, Any]],
    matched_baseline_id: str,
    provenance_key: str,
    hard_failures: Sequence[str] = (),
) -> CandidateEvidence:
    """Build explicit paired evidence from SUMO replication records.

    Ordering is irrelevant.  Both sides must contain exactly the same unique
    ``(demand_variant, seed)`` identities; positional zip/pooling is forbidden.
    """

    def index_records(
        records: Sequence[Mapping[str, Any]],
        label: str,
    ) -> dict[tuple[str, int], float]:
        indexed: dict[tuple[str, int], float] = {}
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError(f"{label} replication record must be an object")
            variant = str(record.get("demand_variant", ""))
            seed = record.get("seed")
            if variant not in DEMAND_VARIANTS:
                raise ValueError(
                    f"{label} replication demand_variant must be q10/q50/q90"
                )
            if (
                isinstance(seed, bool)
                or not isinstance(seed, int)
                or seed < 0
            ):
                raise ValueError(
                    f"{label} replication seed must be non-negative"
                )
            identity = (variant, seed)
            if identity in indexed:
                raise ValueError(
                    f"{label} has duplicate replication identity {identity}"
                )
            indexed[identity] = _finite(
                record.get("total_time_loss_s"),
                f"{label} total_time_loss_s",
            )
        return indexed

    baseline = index_records(baseline_records, "baseline")
    candidate = index_records(candidate_records, "candidate")
    if set(baseline) != set(candidate):
        missing_candidate = sorted(set(baseline) - set(candidate))
        missing_baseline = sorted(set(candidate) - set(baseline))
        raise ValueError(
            "candidate and baseline replication identities differ: "
            f"missing_candidate={missing_candidate}, "
            f"missing_baseline={missing_baseline}"
        )
    observations = tuple(
        PairedObservation(
            candidate_id=candidate_id,
            demand_variant=variant,
            seed=seed,
            baseline_time_loss_s=baseline[(variant, seed)],
            candidate_time_loss_s=candidate[(variant, seed)],
            matched_baseline_id=matched_baseline_id,
            provenance_key=provenance_key,
        )
        for variant, seed in sorted(
            baseline,
            key=lambda identity: (
                DEMAND_VARIANTS.index(identity[0]),
                identity[1],
            ),
        )
    )
    return CandidateEvidence(
        candidate_id=candidate_id,
        observations=observations,
        hard_failures=tuple(str(reason) for reason in hard_failures),
    )


@dataclass(frozen=True)
class VariantStatistics:
    demand_variant: str
    n_pairs: int
    seeds: tuple[int, ...]
    mean_delta_s: float | None
    median_delta_s: float | None
    lower_95_s: float | None
    upper_95_s: float | None
    half_width_s: float | None
    precision_target_s: float | None
    precision_met: bool
    repetition_cap_reached: bool


@dataclass(frozen=True)
class CandidateStatistics:
    candidate_id: str
    eligible: bool
    hard_failures: tuple[str, ...]
    variants: tuple[VariantStatistics, ...]
    robust_point_s: float | None
    robust_lower_95_s: float | None
    robust_upper_95_s: float | None
    worst_variant: str | None
    all_variants_ready: bool
    precision_met: bool
    provenance_key: str | None


@dataclass(frozen=True)
class RunRequest:
    candidate_id: str
    demand_variant: str
    repetitions_to_add: int
    completed_repetitions: int

    def __post_init__(self) -> None:
        if not self.candidate_id or self.demand_variant not in DEMAND_VARIANTS:
            raise ValueError("run request identity is invalid")
        if (
            isinstance(self.repetitions_to_add, bool)
            or self.repetitions_to_add < 1
            or isinstance(self.completed_repetitions, bool)
            or self.completed_repetitions < 0
        ):
            raise ValueError("run request repetition counts are invalid")


@dataclass(frozen=True)
class DecisionResult:
    status: str
    winner_id: str | None
    tie_ids: tuple[str, ...]
    reason: str
    candidates: tuple[CandidateStatistics, ...]
    next_runs: tuple[RunRequest, ...]
    policy: FinalistPolicy
    confidence_level: float
    simultaneous_comparisons: int
    method: str = DECISION_METHOD
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in _DECISION_STATUSES:
            raise ValueError("invalid decision status")
        if self.status == "unique_winner":
            if not self.winner_id or self.tie_ids:
                raise ValueError("unique_winner requires exactly one winner")
        elif self.status == "tie":
            if self.winner_id is not None or len(set(self.tie_ids)) < 2:
                raise ValueError("tie requires at least two unique candidates")
        elif self.winner_id is not None or self.tie_ids:
            raise ValueError(
                "inconclusive/no_viable cannot carry a winner or tie set"
            )
        if self.next_runs and self.status != "inconclusive":
            raise ValueError("only an inconclusive decision can request runs")
        if not 0 < self.confidence_level < 1:
            raise ValueError("decision confidence level is invalid")
        if self.confidence_level != self.policy.confidence_level:
            raise ValueError("decision confidence does not match its policy")
        if self.simultaneous_comparisons < 1:
            raise ValueError("simultaneous comparison count is invalid")

    def to_dict(self) -> dict:
        return asdict(self)


def _validate_candidate_observations(
    candidate: CandidateEvidence,
    policy: FinalistPolicy,
) -> dict[str, list[PairedObservation]]:
    grouped = {variant: [] for variant in policy.variants}
    seen: set[tuple[str, int]] = set()
    provenance_keys: set[str] = set()
    baseline_ids: set[str] = set()
    for observation in candidate.observations:
        if observation.demand_variant not in grouped:
            raise ValueError(
                f"{candidate.candidate_id}: undeclared demand variant "
                f"{observation.demand_variant}"
            )
        pair = (observation.demand_variant, observation.seed)
        if pair in seen:
            raise ValueError(
                f"{candidate.candidate_id}: duplicate variant/seed pair {pair}"
            )
        seen.add(pair)
        provenance_keys.add(observation.provenance_key)
        baseline_ids.add(observation.matched_baseline_id)
        grouped[observation.demand_variant].append(observation)
    if len(provenance_keys) > 1:
        raise ValueError(
            f"{candidate.candidate_id}: incompatible scenario provenance"
        )
    if len(baseline_ids) > 1:
        raise ValueError(
            f"{candidate.candidate_id}: candidate spans multiple matched "
            "baseline envelopes"
        )
    for observations in grouped.values():
        observations.sort(key=lambda item: item.seed)
    return grouped


def _cross_candidate_provenance(
    candidates: Sequence[CandidateEvidence],
    policy: FinalistPolicy,
) -> None:
    """Require one study provenance and exact baselines inside each envelope.

    A monthly search compares schedules on different calendar dates.  Those
    schedules necessarily have different no-closure traffic and therefore
    different matched baseline IDs and values.  Common-random-number pairing
    is required *within* an envelope/baseline group, not across unrelated
    dates.  The study provenance still has to be identical for every
    finalist so network, code, policy and demand-release semantics cannot be
    mixed.
    """
    grouped: dict[
        str,
        dict[str, dict[str, list[PairedObservation]]],
    ] = {}
    study_provenance: set[str] = set()
    for candidate in candidates:
        if not candidate.eligible:
            continue
        candidate_grouped = _validate_candidate_observations(candidate, policy)
        baseline_ids = {
            observation.matched_baseline_id
            for observation in candidate.observations
        }
        if not baseline_ids:
            continue
        baseline_id = next(iter(baseline_ids))
        grouped.setdefault(baseline_id, {})[
            candidate.candidate_id
        ] = candidate_grouped
        for observation in candidate.observations:
            study_provenance.add(observation.provenance_key)
    if len(study_provenance) > 1:
        raise ValueError("finalists do not share compatible scenario provenance")
    for baseline_id, by_candidate in grouped.items():
        reference: dict[
            tuple[str, int],
            tuple[int, str, float],
        ] = {}
        for candidate_id, candidate_grouped in by_candidate.items():
            for variant in policy.variants:
                for repetition, observation in enumerate(
                    candidate_grouped[variant]
                ):
                    pair = (variant, repetition)
                    identity = (
                        observation.seed,
                        observation.provenance_key,
                        observation.baseline_time_loss_s,
                    )
                    previous = reference.setdefault(pair, identity)
                    if previous != identity:
                        raise ValueError(
                            "finalists in the same envelope do not share "
                            "the same matched baseline/common seed "
                            f"for {(baseline_id, *pair)}; mismatch at "
                            f"{candidate_id}"
                        )


def _variant_statistics(
    demand_variant: str,
    observations: Sequence[PairedObservation],
    policy: FinalistPolicy,
    *,
    simultaneous_comparisons: int,
) -> VariantStatistics:
    n_pairs = len(observations)
    cap = n_pairs >= policy.max_repetitions
    if n_pairs < policy.initial_repetitions:
        return VariantStatistics(
            demand_variant=demand_variant,
            n_pairs=n_pairs,
            seeds=tuple(item.seed for item in observations),
            mean_delta_s=None,
            median_delta_s=None,
            lower_95_s=None,
            upper_95_s=None,
            half_width_s=None,
            precision_target_s=None,
            precision_met=False,
            repetition_cap_reached=cap,
        )

    deltas = [item.delta_time_loss_s for item in observations]
    average = mean(deltas)
    center = median(deltas)
    precision_target = max(
        policy.absolute_precision_floor_s,
        policy.relative_precision * abs(average),
    )
    if len(set(deltas)) == 1:
        half_width = 0.0
    else:
        # Bonferroni simultaneous intervals control family-wise coverage
        # across every eligible candidate × demand variant.  This fulfils
        # the plan's simultaneous-comparison requirement without repeated
        # unadjusted pairwise tests.
        family_alpha = 1.0 - policy.confidence_level
        point_alpha = family_alpha / max(1, simultaneous_comparisons)
        critical = float(
            student_t.ppf(1.0 - point_alpha / 2.0, n_pairs - 1)
        )
        half_width = critical * stdev(deltas) / math.sqrt(n_pairs)
    return VariantStatistics(
        demand_variant=demand_variant,
        n_pairs=n_pairs,
        seeds=tuple(item.seed for item in observations),
        mean_delta_s=average,
        median_delta_s=center,
        lower_95_s=average - half_width,
        upper_95_s=average + half_width,
        half_width_s=half_width,
        precision_target_s=precision_target,
        precision_met=half_width <= precision_target,
        repetition_cap_reached=cap,
    )


def _candidate_statistics(
    candidate: CandidateEvidence,
    policy: FinalistPolicy,
    *,
    simultaneous_comparisons: int,
) -> CandidateStatistics:
    if not candidate.eligible:
        return CandidateStatistics(
            candidate_id=candidate.candidate_id,
            eligible=False,
            hard_failures=tuple(sorted(set(candidate.hard_failures))),
            variants=(),
            robust_point_s=None,
            robust_lower_95_s=None,
            robust_upper_95_s=None,
            worst_variant=None,
            all_variants_ready=False,
            precision_met=False,
            provenance_key=None,
        )
    grouped = _validate_candidate_observations(candidate, policy)
    variants = tuple(
        _variant_statistics(
            variant,
            grouped[variant],
            policy,
            simultaneous_comparisons=simultaneous_comparisons,
        )
        for variant in policy.variants
    )
    ready = all(item.mean_delta_s is not None for item in variants)
    if not ready:
        robust_point = robust_lower = robust_upper = None
        worst_variant = None
    else:
        worst = max(
            variants,
            key=lambda item: (
                float(item.upper_95_s),
                policy.variants.index(item.demand_variant),
            ),
        )
        robust_point = max(float(item.mean_delta_s) for item in variants)
        robust_lower = max(float(item.lower_95_s) for item in variants)
        robust_upper = max(float(item.upper_95_s) for item in variants)
        worst_variant = worst.demand_variant
    return CandidateStatistics(
        candidate_id=candidate.candidate_id,
        eligible=True,
        hard_failures=(),
        variants=variants,
        robust_point_s=robust_point,
        robust_lower_95_s=robust_lower,
        robust_upper_95_s=robust_upper,
        worst_variant=worst_variant,
        all_variants_ready=ready,
        precision_met=ready and all(item.precision_met for item in variants),
        provenance_key=(
            candidate.observations[0].provenance_key
            if candidate.observations
            else None
        ),
    )


def _next_runs(
    candidates: Sequence[CandidateStatistics],
    policy: FinalistPolicy,
) -> tuple[RunRequest, ...]:
    requests = []
    for candidate in candidates:
        if not candidate.eligible:
            continue
        for variant in candidate.variants:
            if variant.precision_met or variant.repetition_cap_reached:
                continue
            target = (
                policy.initial_repetitions
                if variant.n_pairs < policy.initial_repetitions
                else min(policy.max_repetitions, variant.n_pairs + 1)
            )
            requests.append(
                RunRequest(
                    candidate_id=candidate.candidate_id,
                    demand_variant=variant.demand_variant,
                    repetitions_to_add=target - variant.n_pairs,
                    completed_repetitions=variant.n_pairs,
                )
            )
    return tuple(requests)


def decide_finalists(
    evidence: Sequence[CandidateEvidence],
    policy: FinalistPolicy,
) -> DecisionResult:
    """Return the robust mesoscopic decision without guessing through gaps."""
    if not evidence:
        raise ValueError("at least one candidate is required")
    ids = [candidate.candidate_id for candidate in evidence]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate IDs must be unique")
    eligible = [candidate for candidate in evidence if candidate.eligible]
    comparisons = max(1, len(eligible) * len(policy.variants))
    _cross_candidate_provenance(evidence, policy)
    statistics = tuple(
        _candidate_statistics(
            candidate,
            policy,
            simultaneous_comparisons=comparisons,
        )
        for candidate in evidence
    )
    viable = [candidate for candidate in statistics if candidate.eligible]
    if not viable:
        return DecisionResult(
            status="no_viable",
            winner_id=None,
            tie_ids=(),
            reason="every schedule failed at least one hard gate before ranking",
            candidates=statistics,
            next_runs=(),
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
        )

    requests = _next_runs(viable, policy)
    if any(not candidate.all_variants_ready for candidate in viable):
        return DecisionResult(
            status="inconclusive",
            winner_id=None,
            tie_ids=(),
            reason=(
                "fewer than the pre-registered matched repetitions are "
                "available for at least one demand variant"
            ),
            candidates=statistics,
            next_runs=requests,
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
        )

    ranked = sorted(
        viable,
        key=lambda candidate: (
            float(candidate.robust_upper_95_s),
            candidate.candidate_id,
        ),
    )
    best = ranked[0]
    if len(ranked) == 1:
        if best.precision_met or not requests:
            return DecisionResult(
                status="unique_winner",
                winner_id=best.candidate_id,
                tie_ids=(),
                reason="the only viable schedule has complete robust evidence",
                candidates=statistics,
                next_runs=(),
                policy=policy,
                confidence_level=policy.confidence_level,
                simultaneous_comparisons=comparisons,
            )
        return DecisionResult(
            status="inconclusive",
            winner_id=None,
            tie_ids=(),
            reason="the only viable schedule has not met the precision target",
            candidates=statistics,
            next_runs=requests,
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
        )

    statistically_better = all(
        float(best.robust_upper_95_s) + policy.practical_equivalence_s
        < float(other.robust_lower_95_s)
        for other in ranked[1:]
    )
    if statistically_better:
        return DecisionResult(
            status="unique_winner",
            winner_id=best.candidate_id,
            tie_ids=(),
            reason=(
                "one schedule is practically and statistically better under "
                "the worst-variant simultaneous 95% bound"
            ),
            candidates=statistics,
            next_runs=(),
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
        )

    unresolved_contenders = tuple(
        candidate
        for candidate in ranked
        if not (
            float(best.robust_upper_95_s) + policy.practical_equivalence_s
            < float(candidate.robust_lower_95_s)
        )
    )
    practical_ties = tuple(
        candidate.candidate_id
        for candidate in unresolved_contenders
        if max(
            abs(
                float(candidate.robust_upper_95_s)
                - float(best.robust_lower_95_s)
            ),
            abs(
                float(best.robust_upper_95_s)
                - float(candidate.robust_lower_95_s)
            ),
        )
        <= policy.practical_equivalence_s
    )
    at_cap_or_precise = all(
        candidate.precision_met
        or all(variant.repetition_cap_reached for variant in candidate.variants)
        for candidate in ranked
    )
    if (
        len(practical_ties) >= 2
        and len(practical_ties) == len(unresolved_contenders)
        and at_cap_or_precise
    ):
        return DecisionResult(
            status="tie",
            winner_id=None,
            tie_ids=practical_ties,
            reason=(
                "the schedules' simultaneous bounds are within the "
                "pre-registered practical equivalence tolerance"
            ),
            candidates=statistics,
            next_runs=(),
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
        )

    return DecisionResult(
        status="inconclusive",
        winner_id=None,
        tie_ids=(),
        reason=(
            "no schedule is both practically and statistically better; "
            + (
                "run the requested matched repetitions"
                if requests
                else "the repetition cap was reached with no clear winner"
            )
        ),
        candidates=statistics,
        next_runs=requests,
        policy=policy,
        confidence_level=policy.confidence_level,
        simultaneous_comparisons=comparisons,
    )
