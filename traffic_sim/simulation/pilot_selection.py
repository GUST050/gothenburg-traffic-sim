"""Fail-closed mesoscopic pilot selection for monthly closure searches.

The analytical proxy may order a broad shortlist, but it does not predict
SUMO delay.  This module is the next fidelity seam: it consumes a small,
matched set of mesoscopic observations for every broadly screened candidate
and retains candidates for the adaptive decision in ``finalist_decision``.

Pilot evidence can only eliminate work.  It never returns a winner and never
authorizes a global-best claim.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from statistics import mean
from typing import Sequence

from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    DEMAND_VARIANTS,
    PairedObservation,
    TimeoutIdentity,
)
from traffic_sim.simulation.closure_ranking import (
    ClosureCost,
    rank_closures,
    worst_variant_cost,
)


SCHEMA_VERSION = 1
PILOT_METHOD = "matched_worst_variant_pilot_v1"
CLOSURE_COST_PILOT_METHOD = "deterministic_closure_cost_pilot_v1"
RANKING_OBJECTIVES = frozenset({"legacy_time_loss_v1", "closure_cost_v1"})
_STATUSES = frozenset(
    {"ready", "incomplete", "no_viable", "capacity_exceeded", "inconclusive"}
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
class PilotPolicy:
    """Pre-registered pilot budget and conservative retention band."""

    retention_band_s: float
    repetitions_per_variant: int
    minimum_finalists: int
    maximum_finalists: int
    variants: tuple[str, ...] = DEMAND_VARIANTS

    def __post_init__(self) -> None:
        if _finite(self.retention_band_s, "retention_band_s") < 0:
            raise ValueError("retention_band_s cannot be negative")
        if (
            isinstance(self.repetitions_per_variant, bool)
            or self.repetitions_per_variant < 1
        ):
            raise ValueError("repetitions_per_variant must be positive")
        if (
            isinstance(self.minimum_finalists, bool)
            or self.minimum_finalists < 1
        ):
            raise ValueError("minimum_finalists must be positive")
        if (
            isinstance(self.maximum_finalists, bool)
            or self.maximum_finalists < self.minimum_finalists
        ):
            raise ValueError(
                "maximum_finalists must be at least minimum_finalists"
            )
        if (
            not self.variants
            or len(set(self.variants)) != len(self.variants)
            or any(variant not in DEMAND_VARIANTS for variant in self.variants)
        ):
            raise ValueError("variants must be unique q10/q50/q90 identities")


@dataclass(frozen=True)
class PilotCandidateStatistics:
    candidate_id: str
    eligible: bool
    hard_failures: tuple[str, ...]
    variant_mean_delta_s: tuple[tuple[str, float], ...]
    worst_variant: str | None
    worst_variant_delta_s: float | None
    complete: bool
    closure_cost: ClosureCost | None = None
    timeout_undecided: tuple[TimeoutIdentity, ...] = ()


@dataclass(frozen=True)
class PilotSelection:
    status: str
    selected_ids: tuple[str, ...]
    reason: str
    candidates: tuple[PilotCandidateStatistics, ...]
    missing_pairs: tuple[tuple[str, str, int], ...]
    policy: PilotPolicy
    method: str = PILOT_METHOD
    schema_version: int = SCHEMA_VERSION
    screening_only: bool = True
    global_best_claim_allowed: bool = False

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError("invalid pilot selection status")
        if self.status == "ready" and not self.selected_ids:
            raise ValueError("ready pilot selection requires finalists")
        if self.status != "ready" and self.selected_ids:
            raise ValueError("non-ready pilot selection cannot publish finalists")
        if self.status == "incomplete" and not self.missing_pairs:
            raise ValueError("incomplete pilot selection must disclose missing pairs")
        if self.status != "incomplete" and self.missing_pairs:
            raise ValueError("only incomplete pilot selection has missing pairs")

    def to_dict(self) -> dict:
        return asdict(self)


def _group_observations(
    candidate: CandidateEvidence,
    policy: PilotPolicy,
) -> tuple[
    dict[str, list[PairedObservation]],
    list[tuple[str, str, int]],
]:
    grouped = {variant: [] for variant in policy.variants}
    seen: set[tuple[str, int]] = set()
    for observation in candidate.observations:
        if observation.demand_variant not in grouped:
            raise ValueError(
                f"{candidate.candidate_id}: undeclared pilot demand variant"
            )
        identity = (observation.demand_variant, observation.seed)
        if identity in seen:
            raise ValueError(
                f"{candidate.candidate_id}: duplicate pilot variant/seed pair"
            )
        seen.add(identity)
        grouped[observation.demand_variant].append(observation)
    for values in grouped.values():
        values.sort(key=lambda observation: observation.seed)

    missing: list[tuple[str, str, int]] = []
    for variant, values in grouped.items():
        if len(values) > policy.repetitions_per_variant:
            raise ValueError(
                f"{candidate.candidate_id}: pilot has more than the frozen "
                f"{policy.repetitions_per_variant} repetitions for {variant}"
            )
        for repetition in range(len(values), policy.repetitions_per_variant):
            missing.append((candidate.candidate_id, variant, repetition))
    return grouped, missing


def _validate_shared_pairing(
    grouped_by_candidate: dict[str, dict[str, list[PairedObservation]]],
    policy: PilotPolicy,
) -> None:
    """Validate CRN pairing within each date/envelope baseline group."""
    reference: dict[
        tuple[str, str, int],
        tuple[int, str, float],
    ] = {}
    study_provenance: set[str] = set()
    for candidate_id, grouped in grouped_by_candidate.items():
        for variant in policy.variants:
            for repetition, observation in enumerate(grouped[variant]):
                study_provenance.add(observation.provenance_key)
                identity = (
                    observation.seed,
                    observation.provenance_key,
                    float(observation.baseline_time_loss_s),
                )
                pair = (
                    observation.matched_baseline_id,
                    variant,
                    repetition,
                )
                previous = reference.setdefault(pair, identity)
                if previous != identity:
                    raise ValueError(
                        "pilot candidates in the same envelope do not share "
                        f"the same matched baseline/provenance for {pair}; mismatch at "
                        f"{candidate_id}"
                    )
    if len(study_provenance) > 1:
        raise ValueError(
            "pilot candidates do not share compatible study provenance"
        )


def select_pilot_finalists(
    evidence: Sequence[CandidateEvidence],
    policy: PilotPolicy,
    *,
    ranking_objective: str = "legacy_time_loss_v1",
    practical_equivalence_vehicle_hours: float = 0.0,
) -> PilotSelection:
    """Retain robust pilot contenders without turning a pilot into a winner."""
    if not evidence:
        raise ValueError("pilot selection requires candidates")
    if ranking_objective not in RANKING_OBJECTIVES:
        raise ValueError(
            f"ranking_objective must be one of {sorted(RANKING_OBJECTIVES)}"
        )
    if (
        isinstance(practical_equivalence_vehicle_hours, bool)
        or not math.isfinite(practical_equivalence_vehicle_hours)
        or practical_equivalence_vehicle_hours < 0
    ):
        raise ValueError(
            "practical_equivalence_vehicle_hours must be finite and non-negative"
        )
    selection_method = (
        CLOSURE_COST_PILOT_METHOD
        if ranking_objective == "closure_cost_v1"
        else PILOT_METHOD
    )
    candidate_ids = [candidate.candidate_id for candidate in evidence]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("pilot candidate IDs must be unique")

    grouped_by_candidate: dict[str, dict[str, list[PairedObservation]]] = {}
    missing: list[tuple[str, str, int]] = []
    for candidate in evidence:
        if not candidate.eligible:
            continue
        grouped, candidate_missing = _group_observations(candidate, policy)
        grouped_by_candidate[candidate.candidate_id] = grouped
        missing.extend(candidate_missing)
    _validate_shared_pairing(grouped_by_candidate, policy)

    statistics = []
    for candidate in evidence:
        if not candidate.eligible:
            statistics.append(
                PilotCandidateStatistics(
                    candidate_id=candidate.candidate_id,
                    eligible=False,
                    hard_failures=tuple(sorted(set(candidate.hard_failures))),
                    variant_mean_delta_s=(),
                    worst_variant=None,
                    worst_variant_delta_s=None,
                    complete=False,
                    timeout_undecided=tuple(
                        sorted(set(candidate.timeout_undecided))
                    ),
                )
            )
            continue
        grouped = grouped_by_candidate[candidate.candidate_id]
        complete = all(
            len(grouped[variant]) == policy.repetitions_per_variant
            for variant in policy.variants
        )
        variant_means = tuple(
            (
                variant,
                mean(
                    observation.delta_time_loss_s
                    for observation in grouped[variant]
                ),
            )
            for variant in policy.variants
            if grouped[variant]
        )
        if complete:
            worst_variant, worst_delta = max(
                variant_means,
                key=lambda item: (
                    item[1],
                    policy.variants.index(item[0]),
                ),
            )
        else:
            worst_variant = None
            worst_delta = None
        statistics.append(
            PilotCandidateStatistics(
                candidate_id=candidate.candidate_id,
                eligible=True,
                hard_failures=(),
                variant_mean_delta_s=variant_means,
                worst_variant=worst_variant,
                worst_variant_delta_s=worst_delta,
                complete=complete,
            )
        )

    # An unresolved SUMO timeout is not a disqualification: it means the
    # search genuinely does not know whether that candidate would have been
    # viable, or whether its cost would have fallen inside the retention
    # band and changed which OTHER candidates get selected. Cost-order v5
    # showed exactly this go wrong — a candidate timed out in one arm and
    # not the other, and the two arms picked different finalists while both
    # silently proceeded as if the timed-out run had never happened. Rather
    # than try to prove after the fact whether a specific timeout could have
    # mattered (which would require bounding a cost the search never
    # measured), fail closed: any unresolved timeout anywhere in the
    # evidence set makes the whole decision explicitly inconclusive.
    undecided = tuple(sorted({
        identity
        for candidate in evidence
        for identity in candidate.timeout_undecided
    }))
    if undecided:
        return PilotSelection(
            status="inconclusive",
            selected_ids=(),
            reason=(
                "pilot evidence includes an unresolved SUMO timeout; the "
                "search cannot rule out that candidate changing the "
                "retained set"
            ),
            candidates=tuple(statistics),
            missing_pairs=(),
            policy=policy,
            method=selection_method,
        )

    viable = [candidate for candidate in statistics if candidate.eligible]
    if not viable:
        return PilotSelection(
            status="no_viable",
            selected_ids=(),
            reason="every pilot candidate failed a hard gate",
            candidates=tuple(statistics),
            missing_pairs=(),
            policy=policy,
            method=selection_method,
        )
    if missing:
        return PilotSelection(
            status="incomplete",
            selected_ids=(),
            reason="matched pilot repetitions are missing",
            candidates=tuple(statistics),
            missing_pairs=tuple(sorted(missing)),
            policy=policy,
            method=selection_method,
        )

    if ranking_objective == "closure_cost_v1":
        evidence_by_id = {
            candidate.candidate_id: candidate
            for candidate in evidence
            if candidate.eligible
        }
        missing_disruption = sorted(
            candidate_id
            for candidate_id, candidate in evidence_by_id.items()
            if not candidate.disruption
        )
        if missing_disruption:
            raise ValueError(
                "closure_cost_v1 requires disruption evidence for every "
                f"viable pilot candidate: missing={missing_disruption}"
            )
        all_costs_by_id = {
            candidate.candidate_id: worst_variant_cost(
                candidate.candidate_id, candidate.disruption
            )
            for candidate in evidence
            if candidate.disruption
        }
        costs = [
            all_costs_by_id[candidate_id]
            for candidate_id in evidence_by_id
        ]
        ordered, refused = rank_closures(costs)
        refused_ids = {cost.candidate_id for cost in refused}
        statistics = [
            replace(
                candidate,
                eligible=False,
                hard_failures=tuple(sorted({
                    *candidate.hard_failures,
                    "vehicles_no_detour",
                })),
                closure_cost=all_costs_by_id[candidate.candidate_id],
            )
            if candidate.candidate_id in refused_ids
            else replace(
                candidate,
                closure_cost=all_costs_by_id.get(candidate.candidate_id),
            )
            for candidate in statistics
        ]
        viable_by_id = {candidate.candidate_id: candidate for candidate in viable}
        ranked = [
            viable_by_id[cost.candidate_id]
            for cost in ordered
        ]
        if not ranked:
            return PilotSelection(
                status="no_viable",
                selected_ids=(),
                reason=(
                    "every pilot candidate leaves at least one destination "
                    "unreachable by car"
                ),
                candidates=tuple(statistics),
                missing_pairs=(),
                policy=policy,
                method=selection_method,
            )
        costs_by_id = {cost.candidate_id: cost for cost in ordered}
    else:
        costs_by_id = {}
        ranked = sorted(
            viable,
            key=lambda candidate: (
                float(candidate.worst_variant_delta_s),
                candidate.candidate_id,
            ),
        )
    minimum_count = min(policy.minimum_finalists, len(ranked))
    if ranking_objective == "closure_cost_v1":
        cutoff = costs_by_id[
            ranked[minimum_count - 1].candidate_id
        ].added_vehicle_hours
        retained = tuple(
            candidate.candidate_id
            for candidate in ranked
            if costs_by_id[candidate.candidate_id].added_vehicle_hours
            <= cutoff + practical_equivalence_vehicle_hours
        )
    else:
        cutoff = float(ranked[minimum_count - 1].worst_variant_delta_s)
        retained = tuple(
            candidate.candidate_id
            for candidate in ranked
            if float(candidate.worst_variant_delta_s)
            <= cutoff + policy.retention_band_s
        )
    if len(retained) > policy.maximum_finalists:
        return PilotSelection(
            status="capacity_exceeded",
            selected_ids=(),
            reason=(
                "more pilot contenders fall inside the frozen retention band "
                "than the finalist capacity permits"
            ),
            candidates=tuple(statistics),
            missing_pairs=(),
            policy=policy,
            method=selection_method,
        )
    return PilotSelection(
        status="ready",
        selected_ids=retained,
        reason=(
            "retained the frozen minimum plus every candidate inside the "
            + (
                "worst-variant closure-cost equivalence band"
                if ranking_objective == "closure_cost_v1"
                else "worst-variant pilot retention band"
            )
        ),
        candidates=tuple(statistics),
        missing_pairs=(),
        policy=policy,
        method=selection_method,
    )
