"""Conditional bounded microscopic confirmation for closure finalists.

Microscopic evidence is deliberately separate from the mesoscopic robust
ranking in ``finalist_decision``.  It can confirm or suppress a recommendation
when junction/queue detail matters, but it can never be folded into the
mesoscopic time-loss score.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from traffic_sim.simulation.finalist_decision import DecisionResult


_MICRO_STATUSES = frozenset(
    {
        "not_required",
        "required",
        "confirmed",
        "failed",
        "queue_detail_not_assessed",
    }
)


@dataclass(frozen=True)
class MicroContext:
    candidate_id: str
    near_signal: bool = False
    near_roundabout: bool = False
    known_bottleneck: bool = False
    halting_diagnostic_high: bool = False

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("microscopic context candidate_id is required")
        if any(
            not isinstance(value, bool)
            for value in (
                self.near_signal,
                self.near_roundabout,
                self.known_bottleneck,
                self.halting_diagnostic_high,
            )
        ):
            raise ValueError("microscopic context flags must be boolean")


@dataclass(frozen=True)
class MicroResult:
    candidate_id: str
    provenance_key: str
    health_passed: bool
    access_passed: bool
    spillback_assessed: bool
    spillback_passed: bool | None

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.provenance_key:
            raise ValueError("microscopic result identity is required")
        if any(
            not isinstance(value, bool)
            for value in (
                self.health_passed,
                self.access_passed,
                self.spillback_assessed,
            )
        ):
            raise ValueError("microscopic result flags must be boolean")
        if self.spillback_passed is not None and not isinstance(
            self.spillback_passed, bool
        ):
            raise ValueError("spillback_passed must be boolean or null")
        if self.spillback_assessed != (self.spillback_passed is not None):
            raise ValueError(
                "spillback_passed must be present exactly when assessed"
            )


@dataclass(frozen=True)
class MicroConfirmation:
    status: str
    candidate_ids: tuple[str, ...]
    trigger_reasons: tuple[str, ...]
    queue_detail_status: str
    recommendation_allowed: bool
    failed_candidate_ids: tuple[str, ...] = ()
    missing_candidate_ids: tuple[str, ...] = ()
    simulation_mode: str = "micro"

    def __post_init__(self) -> None:
        if self.status not in _MICRO_STATUSES:
            raise ValueError("invalid microscopic confirmation status")
        if self.simulation_mode != "micro":
            raise ValueError("microscopic confirmation mode must be micro")
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("microscopic finalist IDs must be unique")
        if not set(self.failed_candidate_ids) <= set(self.candidate_ids):
            raise ValueError("failed microscopic candidate is not a finalist")
        if not set(self.missing_candidate_ids) <= set(self.candidate_ids):
            raise ValueError("missing microscopic candidate is not a finalist")
        if self.recommendation_allowed and self.status not in {
            "not_required",
            "confirmed",
        }:
            raise ValueError(
                "an incomplete/failed microscopic confirmation cannot recommend"
            )

    def to_dict(self) -> dict:
        return asdict(self)


def plan_micro_confirmation(
    decision: DecisionResult,
    contexts: Mapping[str, MicroContext],
    *,
    micro_available: bool,
    results: Sequence[MicroResult] = (),
    finalist_limit: int = 3,
) -> MicroConfirmation:
    """Plan/evaluate bounded micro evidence without changing the meso score."""
    if not 2 <= finalist_limit <= 3:
        raise ValueError("finalist_limit must be two or three")
    ranked = sorted(
        (
            candidate
            for candidate in decision.candidates
            if candidate.eligible and candidate.robust_upper_95_s is not None
        ),
        key=lambda candidate: (
            float(candidate.robust_upper_95_s),
            candidate.candidate_id,
        ),
    )
    finalist_ids = tuple(
        candidate.candidate_id for candidate in ranked[:finalist_limit]
    )
    reasons: set[str] = set()
    for candidate_id in finalist_ids:
        context = contexts.get(candidate_id)
        if context is None:
            continue
        if context.candidate_id != candidate_id:
            raise ValueError("microscopic context candidate_id mismatch")
        if context.near_signal:
            reasons.add("near_signal")
        if context.near_roundabout:
            reasons.add("near_roundabout")
        if context.known_bottleneck:
            reasons.add("known_bottleneck")
        if context.halting_diagnostic_high:
            reasons.add("halting_diagnostic_high")
    if decision.status in {"tie", "inconclusive"} and len(finalist_ids) >= 2:
        reasons.add("mesoscopic_candidates_too_close")

    recommendation_base = decision.status in {"unique_winner", "tie"}
    if not reasons or not finalist_ids:
        return MicroConfirmation(
            status="not_required",
            candidate_ids=finalist_ids,
            trigger_reasons=(),
            queue_detail_status="mesoscopic_diagnostic_only",
            recommendation_allowed=recommendation_base,
        )
    if not micro_available:
        return MicroConfirmation(
            status="queue_detail_not_assessed",
            candidate_ids=finalist_ids,
            trigger_reasons=tuple(sorted(reasons)),
            queue_detail_status="queue_detail_not_assessed",
            recommendation_allowed=False,
            missing_candidate_ids=finalist_ids,
        )

    by_id: dict[str, MicroResult] = {}
    provenance_keys: set[str] = set()
    expected_provenance = {
        candidate.candidate_id: candidate.provenance_key
        for candidate in ranked
        if candidate.candidate_id in finalist_ids
    }
    for result in results:
        if result.candidate_id not in finalist_ids:
            raise ValueError("microscopic result is not for a selected finalist")
        if result.candidate_id in by_id:
            raise ValueError("duplicate microscopic result")
        by_id[result.candidate_id] = result
        provenance_keys.add(result.provenance_key)
        expected = expected_provenance.get(result.candidate_id)
        if expected is not None and result.provenance_key != expected:
            raise ValueError(
                "microscopic result does not match mesoscopic provenance"
            )
    if len(provenance_keys) > 1:
        raise ValueError("microscopic finalist provenance is incompatible")
    missing = tuple(
        candidate_id for candidate_id in finalist_ids if candidate_id not in by_id
    )
    if missing:
        return MicroConfirmation(
            status="required",
            candidate_ids=finalist_ids,
            trigger_reasons=tuple(sorted(reasons)),
            queue_detail_status="pending",
            recommendation_allowed=False,
            missing_candidate_ids=missing,
        )
    failed = tuple(
        candidate_id
        for candidate_id in finalist_ids
        if (
            not by_id[candidate_id].health_passed
            or not by_id[candidate_id].access_passed
            or (
                by_id[candidate_id].spillback_assessed
                and not by_id[candidate_id].spillback_passed
            )
        )
    )
    unassessed = tuple(
        candidate_id
        for candidate_id in finalist_ids
        if not by_id[candidate_id].spillback_assessed
    )
    if failed:
        return MicroConfirmation(
            status="failed",
            candidate_ids=finalist_ids,
            trigger_reasons=tuple(sorted(reasons)),
            queue_detail_status=(
                "queue_detail_not_assessed" if unassessed else "assessed"
            ),
            recommendation_allowed=False,
            failed_candidate_ids=failed,
        )
    return MicroConfirmation(
        status=(
            "queue_detail_not_assessed" if unassessed else "confirmed"
        ),
        candidate_ids=finalist_ids,
        trigger_reasons=tuple(sorted(reasons)),
        queue_detail_status=(
            "queue_detail_not_assessed" if unassessed else "assessed"
        ),
        recommendation_allowed=recommendation_base and not unassessed,
        missing_candidate_ids=unassessed,
    )
