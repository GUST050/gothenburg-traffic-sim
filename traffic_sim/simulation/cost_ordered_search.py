"""Verify candidates in deterministic cost order, and stop when it is safe.

PR E / step 4 of
`docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`.

THE IDEA.  PR D made the ranking cost deterministic and process-free, so a
search can know every candidate's cost before simulating any of them. SUMO is
then needed only to QUALIFY or DISQUALIFY a candidate — it cannot change the
cost, and therefore cannot change the order. Verifying in cost order means the
run can stop as soon as no unexamined candidate could still enter today's
finalist set.

THE STOP RULE, and why it is exact:

  1. Deterministically disqualified candidates (`vehicles_no_detour > 0`) never
     reach SUMO. They cannot be retained under any policy.
  2. The rest are sorted by the UNCHANGED `ClosureCost.sort_key`.
  3. Candidates are verified one at a time in that order. A hard failure is not
     a stop — the scan continues past it.
  4. Once `minimum_finalists` verified candidates are pilot-viable, the cutoff
     is the k-th viable candidate's `added_vehicle_hours`, exactly as
     `pilot_selection` computes it.
  5. Verification continues while the next candidate's primary cost is
     `<= cutoff + practical_equivalence_vehicle_hours`.
  6. It stops only when the next unexamined candidate is STRICTLY above that
     band. Because the order is by `added_vehicle_hours` first, every remaining
     candidate is at least as expensive, so none of them can be retained, and
     none of them can move the cutoff — every cheaper candidate has already
     been examined.

A candidate exactly ON the boundary is INSIDE it: `<=`, not `<`. That is the
same comparison `pilot_selection` uses, and it is the difference between
reproducing the exhaustive finalist set and quietly dropping a tie.

WHAT THIS MODULE DOES NOT DO.  It never decides anything. The finalist set it
produces is handed to the unchanged `select_pilot_finalists`, which owns
`ready`, `capacity_exceeded`, `no_viable` and `incomplete` exactly as before —
so this module cannot silently truncate a finalist set, and a capacity overflow
still fails the way it does today.

SHADOW MODE.  Until the equivalence gate passes, this runs alongside the
exhaustive path and is compared to it; `compare_with_exhaustive` is the
comparison, and it checks status and `selected_ids`, which is what the plan's
gate names.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from traffic_sim.simulation.closure_ranking import ClosureCost, rank_closures
from traffic_sim.simulation.finalist_decision import CandidateEvidence
from traffic_sim.simulation.pilot_selection import (
    PilotPolicy,
    PilotSelection,
    select_pilot_finalists,
)

SCHEMA = "cost_ordered_closure_search_v1"
SCHEMA_VERSION = 1

#: Screening mode name. `independent-exhaustive` stays the reference.
SCREENING_MODE = "independent-cost-ordered-exact"

#: Marker put on a candidate the deterministic cost refuses before SUMO. It is
#: the same reason string `pilot_selection` attaches when it demotes a
#: no-detour candidate, so statistics read identically either way.
NO_DETOUR_FAILURE = "vehicles_no_detour"

_STOP_REASONS = frozenset({
    "band_exhausted",
    "search_space_exhausted",
    "no_viable_candidates",
})


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CostOrderedCandidate:
    """One parent schedule with its deterministic cost, before SUMO."""

    candidate_id: str
    cost: ClosureCost
    disruption: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.cost.candidate_id != self.candidate_id:
            raise ValueError("closure cost belongs to another candidate")


@dataclass
class CostOrderedState:
    """Serializable cursor, cutoff and verified set.

    Restart reads this and continues at the first unverified sorted candidate.
    `identity_key` binds everything a resume must not survive: the policy, the
    cost ledger, and the provider/route/network identity the costs came from.
    """

    identity_key: str
    order: tuple[str, ...]
    cursor: int = 0
    verified: tuple[str, ...] = ()
    viable: tuple[str, ...] = ()
    cutoff: float | None = None
    stop_reason: str | None = None
    schema: str = SCHEMA
    version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "version": self.version,
            "identity_key": self.identity_key,
            "order": list(self.order),
            "cursor": int(self.cursor),
            "verified": list(self.verified),
            "viable": list(self.viable),
            "cutoff": self.cutoff,
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CostOrderedState":
        if not isinstance(raw, Mapping):
            raise ValueError("cost-ordered state must be an object")
        if raw.get("schema") != SCHEMA or raw.get("version") != SCHEMA_VERSION:
            raise ValueError("cost-ordered state schema is unsupported")
        try:
            state = cls(
                identity_key=str(raw["identity_key"]),
                order=tuple(str(value) for value in raw["order"]),
                cursor=int(raw.get("cursor", 0)),
                verified=tuple(str(value) for value in raw.get("verified", ())),
                viable=tuple(str(value) for value in raw.get("viable", ())),
                cutoff=(None if raw.get("cutoff") is None
                        else float(raw["cutoff"])),
                stop_reason=(None if raw.get("stop_reason") is None
                             else str(raw["stop_reason"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"cost-ordered state is malformed: {error}") from error
        if state.cursor < 0 or state.cursor > len(state.order):
            raise ValueError("cost-ordered cursor is outside its order")
        if len(set(state.order)) != len(state.order):
            raise ValueError("cost-ordered order repeats a candidate")
        if not set(state.verified) <= set(state.order):
            raise ValueError("cost-ordered state verified an unknown candidate")
        if not set(state.viable) <= set(state.verified):
            raise ValueError("cost-ordered state marked an unverified candidate viable")
        if state.stop_reason is not None and state.stop_reason not in _STOP_REASONS:
            raise ValueError("cost-ordered stop reason is unrecognised")
        return state


@dataclass(frozen=True)
class CostOrderedResult:
    """One scan's outcome, plus the evidence that the stop was safe."""

    selection: PilotSelection
    state: CostOrderedState
    ordered_costs: tuple[ClosureCost, ...]
    disqualified: tuple[ClosureCost, ...]
    evidence: tuple[CandidateEvidence, ...]
    verified_count: int
    cache_hits: int
    stop_proof: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "version": SCHEMA_VERSION,
            "status": self.selection.status,
            "selected_ids": list(self.selection.selected_ids),
            "order": [cost.candidate_id for cost in self.ordered_costs],
            "disqualified": [
                {
                    "candidate_id": cost.candidate_id,
                    "vehicles_no_detour": int(cost.vehicles_no_detour),
                    "added_vehicle_hours": float(cost.added_vehicle_hours),
                }
                for cost in self.disqualified
            ],
            "verified_ids": list(self.state.verified),
            "verified_count": int(self.verified_count),
            "cache_hits": int(self.cache_hits),
            "cutoff": self.state.cutoff,
            "cursor": int(self.state.cursor),
            "stop_proof": dict(self.stop_proof),
        }


def plan_order(
    candidates: Sequence[CostOrderedCandidate],
) -> tuple[tuple[ClosureCost, ...], tuple[ClosureCost, ...]]:
    """Split into (viable in cost order, deterministically disqualified).

    Uses the unchanged `rank_closures`, so the order here IS the order the
    exhaustive path ranks by.
    """
    ids = [item.candidate_id for item in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("cost-ordered candidates must be unique")
    return rank_closures(item.cost for item in candidates)


def identity_key(
    *,
    search_content_key: str,
    policy: PilotPolicy,
    practical_equivalence_vehicle_hours: float,
    provider_identity: Mapping[str, Any],
    ordered_costs: Sequence[ClosureCost],
) -> str:
    """What a resume must not survive.

    The cost ledger is included as its actual numbers, not merely as an ID
    list: a resumed scan whose costs moved would continue from a cursor that
    means nothing, and the stop proof would be about a different search.
    """
    return _digest({
        "schema": SCHEMA,
        "version": SCHEMA_VERSION,
        "search_content_key": search_content_key,
        "policy": {
            "minimum_finalists": policy.minimum_finalists,
            "maximum_finalists": policy.maximum_finalists,
            "repetitions_per_variant": policy.repetitions_per_variant,
            "retention_band_s": policy.retention_band_s,
            "variants": list(policy.variants),
        },
        "practical_equivalence_vehicle_hours": float(
            practical_equivalence_vehicle_hours),
        "provider_identity": dict(provider_identity),
        "cost_ledger": [
            {
                "candidate_id": cost.candidate_id,
                "added_vehicle_hours": float(cost.added_vehicle_hours),
                "added_metres_total": float(cost.added_metres_total),
                "vehicles_affected": int(cost.vehicles_affected),
                "vehicles_no_detour": int(cost.vehicles_no_detour),
            }
            for cost in ordered_costs
        ],
    })


def run_cost_ordered_search(
    candidates: Sequence[CostOrderedCandidate],
    policy: PilotPolicy,
    *,
    verify: Callable[[str], CandidateEvidence],
    search_content_key: str,
    provider_identity: Mapping[str, Any],
    practical_equivalence_vehicle_hours: float = 0.0,
    state: CostOrderedState | None = None,
    verified_evidence: Mapping[str, CandidateEvidence] | None = None,
    progress: Callable[[Mapping[str, Any]], None] | None = None,
) -> CostOrderedResult:
    """Verify in cost order, stop when nothing unexamined can be retained.

    `verify` runs the pilot for one candidate. Anything it can satisfy from a
    cache is still a verification — the plan counts a cache hit as verified,
    because the evidence exists and is identical.

    `state` resumes a previous scan; `verified_evidence` carries the evidence
    that scan already produced. A resume whose `identity_key` no longer matches
    is refused rather than continued.
    """
    if not candidates:
        raise ValueError("cost-ordered search requires candidates")
    if (
        isinstance(practical_equivalence_vehicle_hours, bool)
        or float(practical_equivalence_vehicle_hours) < 0
    ):
        raise ValueError(
            "practical_equivalence_vehicle_hours must be non-negative")

    ordered, disqualified = plan_order(candidates)
    key = identity_key(
        search_content_key=search_content_key,
        policy=policy,
        practical_equivalence_vehicle_hours=practical_equivalence_vehicle_hours,
        provider_identity=provider_identity,
        ordered_costs=ordered,
    )
    order = tuple(cost.candidate_id for cost in ordered)
    by_id = {item.candidate_id: item for item in candidates}
    cost_by_id = {cost.candidate_id: cost for cost in ordered}

    if state is not None:
        if state.identity_key != key:
            raise ValueError(
                "cost-ordered resume is invalid: the policy, cost ledger or "
                "provider identity changed since the cursor was written")
        if state.order != order:
            raise ValueError(
                "cost-ordered resume is invalid: the sorted order changed")
        cursor = state.cursor
        verified = list(state.verified)
        viable = list(state.viable)
    else:
        cursor = 0
        verified = []
        viable = []

    evidence_by_id: dict[str, CandidateEvidence] = dict(verified_evidence or {})
    missing = [item for item in verified if item not in evidence_by_id]
    if missing:
        raise ValueError(
            f"cost-ordered resume is missing evidence for {len(missing)} "
            f"already-verified candidate(s), first {missing[0]}")

    cache_hits = 0
    stop_reason = "search_space_exhausted"
    cutoff: float | None = None

    def band() -> float | None:
        if len(viable) < policy.minimum_finalists:
            return None
        kth = cost_by_id[viable[policy.minimum_finalists - 1]]
        return float(kth.added_vehicle_hours) + float(
            practical_equivalence_vehicle_hours)

    # Rebuild the cutoff a resumed scan already earned.
    if len(viable) >= policy.minimum_finalists:
        cutoff = float(
            cost_by_id[viable[policy.minimum_finalists - 1]].added_vehicle_hours
        )

    if not order:
        stop_reason = "no_viable_candidates"

    while cursor < len(order):
        candidate_id = order[cursor]
        limit = band()
        if limit is not None and float(
                cost_by_id[candidate_id].added_vehicle_hours) > limit:
            stop_reason = "band_exhausted"
            break
        result = verify(candidate_id)
        if not isinstance(result, CandidateEvidence):
            raise ValueError("cost-ordered verification must return evidence")
        if result.candidate_id != candidate_id:
            raise ValueError("cost-ordered verification returned another candidate")
        if getattr(result, "from_cache", False):
            cache_hits += 1
        evidence_by_id[candidate_id] = result
        verified.append(candidate_id)
        if result.eligible:
            viable.append(candidate_id)
            if (cutoff is None
                    and len(viable) >= policy.minimum_finalists):
                cutoff = float(
                    cost_by_id[
                        viable[policy.minimum_finalists - 1]
                    ].added_vehicle_hours)
        cursor += 1
        if progress is not None:
            progress({
                "verified": len(verified),
                "viable": len(viable),
                "total": len(order),
                "cutoff": cutoff,
                "cache_hits": cache_hits,
            })

    final_state = CostOrderedState(
        identity_key=key,
        order=order,
        cursor=cursor,
        verified=tuple(verified),
        viable=tuple(viable),
        cutoff=cutoff,
        stop_reason=stop_reason,
    )

    # The unchanged selector owns the decision. Deterministically disqualified
    # candidates are handed over marked exactly as `pilot_selection` marks
    # them, so `no_viable` still happens when nothing can win.
    selection_evidence: list[CandidateEvidence] = [
        evidence_by_id[candidate_id] for candidate_id in verified
    ]
    for cost in disqualified:
        source = by_id[cost.candidate_id]
        selection_evidence.append(CandidateEvidence(
            candidate_id=cost.candidate_id,
            observations=(),
            hard_failures=(NO_DETOUR_FAILURE,),
            disruption=tuple(source.disruption),
        ))
    if not selection_evidence:
        raise ValueError("cost-ordered search produced no evidence")

    selection = select_pilot_finalists(
        selection_evidence,
        policy,
        ranking_objective="closure_cost_v1",
        practical_equivalence_vehicle_hours=(
            practical_equivalence_vehicle_hours),
    )

    return CostOrderedResult(
        selection=selection,
        state=final_state,
        ordered_costs=ordered,
        disqualified=disqualified,
        evidence=tuple(selection_evidence),
        verified_count=len(verified),
        cache_hits=cache_hits,
        stop_proof=stop_proof(
            ordered=ordered,
            state=final_state,
            policy=policy,
            practical_equivalence_vehicle_hours=(
                practical_equivalence_vehicle_hours),
        ),
    )


def stop_proof(
    *,
    ordered: Sequence[ClosureCost],
    state: CostOrderedState,
    policy: PilotPolicy,
    practical_equivalence_vehicle_hours: float,
) -> dict[str, Any]:
    """Machine-readable: why no unexamined candidate could be selected.

    A human-readable sentence is not enough here. The whole claim of the
    cost-ordered mode is that skipping work changed nothing, and that claim has
    to be checkable by something other than trust — so the record states the
    band, the first unexamined candidate, and its cost.
    """
    cost_by_id = {cost.candidate_id: cost for cost in ordered}
    remaining = list(state.order[state.cursor:])
    first_unexamined = remaining[0] if remaining else None
    band = (
        None if state.cutoff is None
        else float(state.cutoff) + float(practical_equivalence_vehicle_hours)
    )
    proof = {
        "schema": SCHEMA,
        "stop_reason": state.stop_reason,
        "minimum_finalists": policy.minimum_finalists,
        "practical_equivalence_vehicle_hours": float(
            practical_equivalence_vehicle_hours),
        "cutoff_added_vehicle_hours": state.cutoff,
        "selection_band_added_vehicle_hours": band,
        "examined": int(state.cursor),
        "total_ordered": len(state.order),
        "unexamined": len(remaining),
        "first_unexamined_candidate_id": first_unexamined,
        "first_unexamined_added_vehicle_hours": (
            None if first_unexamined is None
            else float(cost_by_id[first_unexamined].added_vehicle_hours)
        ),
    }
    if state.stop_reason == "band_exhausted":
        proof["argument"] = (
            "the order is by added_vehicle_hours first, so every unexamined "
            "candidate costs at least as much as the first one, which is "
            "strictly above the selection band; none can be retained, and "
            "none can move the cutoff because every cheaper candidate was "
            "already verified")
    elif state.stop_reason == "search_space_exhausted":
        proof["argument"] = (
            "every candidate that survived the deterministic no-detour gate "
            "was verified, so the finalist set is the exhaustive one")
    else:
        proof["argument"] = (
            "no candidate survived the deterministic no-detour gate, so there "
            "was nothing to verify")
    return proof


def replay_shadow(
    candidates: Sequence[CostOrderedCandidate],
    policy: PilotPolicy,
    evidence_by_id: Mapping[str, CandidateEvidence],
    *,
    search_content_key: str,
    provider_identity: Mapping[str, Any],
    practical_equivalence_vehicle_hours: float = 0.0,
) -> CostOrderedResult:
    """Run the scan against evidence an exhaustive run already produced.

    This is how shadow mode works without spending a second campaign: the
    exhaustive run simulated everything, so the cost-ordered scan can be
    replayed against that evidence and its answer compared. It asks for a
    SUBSET, and if it ever asked for a candidate the exhaustive run did not
    verify, that would falsify the whole ordering argument — so the replay
    raises instead of quietly filling the gap.
    """
    known = dict(evidence_by_id)

    def verify(candidate_id: str) -> CandidateEvidence:
        try:
            return known[candidate_id]
        except KeyError:
            raise ValueError(
                f"shadow replay needs evidence the exhaustive run does not "
                f"have for {candidate_id}; the cost order and the reference "
                f"run disagree about what was simulated") from None

    return run_cost_ordered_search(
        candidates, policy,
        verify=verify,
        search_content_key=search_content_key,
        provider_identity=provider_identity,
        practical_equivalence_vehicle_hours=(
            practical_equivalence_vehicle_hours),
    )


def shadow_from_pilot_selection(
    pilot_selection: Mapping[str, Any],
    policy: PilotPolicy,
    *,
    search_content_key: str,
    provider_identity: Mapping[str, Any],
    practical_equivalence_vehicle_hours: float = 0.0,
) -> dict[str, Any]:
    """Replay the cost-ordered scan from a completed run's pilot statistics.

    DIAGNOSTIC REPLAY, not release evidence, and deliberately explicit about
    what it replays: the independent path publishes one `pilot-selection.json`
    rather than a per-candidate evidence artifact, so this reconstructs each
    candidate's DECISION INPUTS — eligibility, completeness and the recorded
    `closure_cost` — rather than raw SUMO output. Under `closure_cost_v1` those
    are exactly the inputs `select_pilot_finalists` consumes, so the selection
    it produces is the selection the run made; it is not a re-simulation and
    must never be presented as one.
    """
    from traffic_sim.simulation.finalist_decision import PairedObservation

    statistics = list(pilot_selection.get("candidates", ()))
    if not statistics:
        raise ValueError("pilot selection has no candidate statistics")

    candidates: list[CostOrderedCandidate] = []
    evidence: dict[str, CandidateEvidence] = {}
    for item in statistics:
        candidate_id = str(item.get("candidate_id", ""))
        raw_cost = item.get("closure_cost")
        if not candidate_id or not isinstance(raw_cost, Mapping):
            raise ValueError(
                "cost-ordered shadow needs a recorded closure cost for every "
                f"candidate; {candidate_id or '<unnamed>'} has none")
        cost = ClosureCost(
            candidate_id=candidate_id,
            added_vehicle_hours=float(raw_cost["added_vehicle_hours"]),
            added_metres_total=float(raw_cost["added_metres_total"]),
            vehicles_affected=int(raw_cost["vehicles_affected"]),
            vehicles_no_detour=int(raw_cost["vehicles_no_detour"]),
        )
        # Three records whose field-wise worst reproduces the recorded cost
        # exactly, so `select_pilot_finalists` recomputes the same ClosureCost.
        records = tuple({
            "demand_variant": variant,
            "vehicles_affected": cost.vehicles_affected,
            "vehicles_considered": 0,
            "vehicles_no_detour": cost.vehicles_no_detour,
            "added_vehicle_hours": cost.added_vehicle_hours,
            "added_metres_total": cost.added_metres_total,
        } for variant in ("q10", "q50", "q90"))
        candidates.append(CostOrderedCandidate(
            candidate_id=candidate_id, cost=cost, disruption=records))

        failures = tuple(str(value) for value in item.get("hard_failures", ()))
        eligible = bool(item.get("eligible")) and not failures
        observations = ()
        if eligible:
            delta = item.get("worst_variant_delta_s")
            observations = tuple(
                PairedObservation(
                    candidate_id=candidate_id,
                    demand_variant=variant,
                    seed=1000 + repetition * 3 + index,
                    baseline_time_loss_s=0.0,
                    candidate_time_loss_s=float(delta or 0.0),
                    matched_baseline_id="replayed-baseline",
                    provenance_key="replayed-provenance",
                )
                for repetition in range(policy.repetitions_per_variant)
                for index, variant in enumerate(policy.variants)
            )
        evidence[candidate_id] = CandidateEvidence(
            candidate_id=candidate_id,
            observations=observations,
            hard_failures=failures if not eligible else (),
            disruption=records,
        )

    result = replay_shadow(
        candidates, policy, evidence,
        search_content_key=search_content_key,
        provider_identity=provider_identity,
        practical_equivalence_vehicle_hours=(
            practical_equivalence_vehicle_hours),
    )
    reference = select_pilot_finalists(
        [evidence[item.candidate_id] for item in candidates],
        policy,
        ranking_objective="closure_cost_v1",
        practical_equivalence_vehicle_hours=(
            practical_equivalence_vehicle_hours),
    )
    comparison = compare_with_exhaustive(result, reference)
    comparison["evidence_class"] = "diagnostic_shadow_replay"
    comparison["release_evidence"] = False
    comparison["replay_basis"] = (
        "recorded pilot-selection statistics: eligibility, completeness and "
        "closure cost. Not a re-simulation.")
    comparison["recorded_status"] = str(pilot_selection.get("status", ""))
    comparison["recorded_selected_ids"] = [
        str(value) for value in pilot_selection.get("selected_ids", ())]
    comparison["matches_recorded_run"] = (
        comparison["cost_ordered_status"] == comparison["recorded_status"]
        and comparison["cost_ordered_selected_ids"]
        == comparison["recorded_selected_ids"])
    return comparison


def compare_with_exhaustive(
    cost_ordered: CostOrderedResult,
    exhaustive: PilotSelection,
) -> dict[str, Any]:
    """Shadow-mode comparison against the reference path.

    Compares what the plan's exit gate names — pilot status and `selected_ids`
    — and reports the work saved. `reason` and the per-candidate statistics
    legitimately differ: the cost-ordered scan never simulated the candidates
    it proved could not be selected, so it cannot have statistics for them.
    That is the saving, not a discrepancy.
    """
    same_status = cost_ordered.selection.status == exhaustive.status
    same_ids = (tuple(cost_ordered.selection.selected_ids)
                == tuple(exhaustive.selected_ids))
    return {
        "schema": SCHEMA,
        "equivalent": bool(same_status and same_ids),
        "status_matches": bool(same_status),
        "selected_ids_match": bool(same_ids),
        "cost_ordered_status": cost_ordered.selection.status,
        "exhaustive_status": exhaustive.status,
        "cost_ordered_selected_ids": list(cost_ordered.selection.selected_ids),
        "exhaustive_selected_ids": list(exhaustive.selected_ids),
        "verified_candidates": cost_ordered.verified_count,
        "ordered_candidates": len(cost_ordered.ordered_costs),
        "deterministically_disqualified": len(cost_ordered.disqualified),
        "sumo_verifications_saved": (
            len(cost_ordered.ordered_costs) - cost_ordered.verified_count),
        "cache_hits": cost_ordered.cache_hits,
        "stop_proof": dict(cost_ordered.stop_proof),
        "comparison_note": (
            "status and selected_ids are the gate; per-candidate statistics "
            "differ by construction because unverified candidates have none"),
    }
