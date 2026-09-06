"""Rank closure schedules by what they cost the people driving.

WHY THIS EXISTS (2026-08-05). The monthly search ranked finalists on
``robust_upper_95_s`` — a confidence bound on simulated ``delta_time_loss_s``.
Measured on this network, that key carries almost no signal:

  * a real closure produced +0.050 s and -0.100 s per vehicle across arms, and
    a closure cannot REDUCE time loss, so both are noise around zero;
  * congestion feedback converged at 0.0% change, i.e. there is no congestion
    for a closure to disturb, because only sensor-crossing traffic is
    simulated and the network runs at free flow.

The quantities that DO separate candidates, measured across the 20 busiest
edges on 2025-09-16: affected vehicles 0-8,427, added vehicle-hours 0.0-20.6,
median detour 0-226 m, stranded vehicles 0-287.

RANKING RULE (lexicographic, no invented weights):

  1. added_vehicle_hours  — the headline. It already combines both halves:
     the count is how many terms the sum has, the severity is each term. This
     is the standard transport-economics measure.
  2. added_metres_total   — distance is a SECOND currency. Converting it into
     hours needs a value-of-time per km, which would be a policy weight we do
     not have, so it breaks ties instead of being blended in.
  3. vehicles_affected    — breaks remaining ties toward disturbing fewer
     people for the same total cost.

Vehicles left with no route at all are a DISQUALIFIER, not a term: a driver
who can no longer reach their destination is not "delayed by N seconds", and
averaging that into a score hides it.

The inputs are deterministic (demand-side, no seeds), so unlike the time-loss
key there is no Monte Carlo spread to average.  ``closure_cost_v1`` retained
the historical field-wise worst reduction.  Product policy
``closure_cost_v2`` instead ranks the central q50 directional assignment;
q10/q90 remain sensitivity evidence.  Reachability is deliberately still
checked across every variant, because changing the cost estimate must not make
it permissible to strand a driver in another plausible assignment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

#: Keys a disruption record must carry to be rankable.
REQUIRED_FIELDS = (
    "vehicles_affected",
    "added_vehicle_hours",
    "added_metres_total",
    "vehicles_no_detour",
)

LEGACY_WORST_COST_OBJECTIVE = "closure_cost_v1"
Q50_COST_OBJECTIVE = "closure_cost_v2"
CLOSURE_COST_OBJECTIVES = frozenset({
    LEGACY_WORST_COST_OBJECTIVE,
    Q50_COST_OBJECTIVE,
})


@dataclass(frozen=True)
class ClosureCost:
    """One candidate's cost, already reduced across direction-split variants."""

    candidate_id: str
    added_vehicle_hours: float
    added_metres_total: float
    vehicles_affected: int
    vehicles_no_detour: int

    @property
    def disqualified(self) -> bool:
        """True when the closure leaves at least one destination unreachable."""
        return self.vehicles_no_detour > 0

    @property
    def sort_key(self) -> tuple:
        """Lexicographic key — see the module docstring for why, not a blend."""
        return (
            float(self.added_vehicle_hours),
            float(self.added_metres_total),
            int(self.vehicles_affected),
            self.candidate_id,
        )


def _validate(record: Mapping) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in record]
    if missing:
        raise ValueError(
            f"disruption record is missing required field(s): {sorted(missing)}")
    for field in REQUIRED_FIELDS:
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"disruption field {field!r} must be numeric")
        if value < 0:
            raise ValueError(f"disruption field {field!r} must not be negative")


def worst_variant_cost(candidate_id: str,
                       variant_records: Sequence[Mapping]) -> ClosureCost:
    """Reduce per-variant disruption records to the worst case.

    Worst is taken FIELD-WISE rather than by picking a single variant: the
    q10/q50/q90 splits are three plausible directional assignments, and a
    schedule is only as good as its least favourable outcome on each axis.
    """
    if not variant_records:
        raise ValueError("at least one variant record is required")
    for record in variant_records:
        _validate(record)
    return ClosureCost(
        candidate_id=candidate_id,
        added_vehicle_hours=max(float(r["added_vehicle_hours"])
                                for r in variant_records),
        added_metres_total=max(float(r["added_metres_total"])
                               for r in variant_records),
        vehicles_affected=max(int(r["vehicles_affected"])
                              for r in variant_records),
        vehicles_no_detour=max(int(r["vehicles_no_detour"])
                               for r in variant_records),
    )


def q50_variant_cost(candidate_id: str,
                     variant_records: Sequence[Mapping]) -> ClosureCost:
    """Use q50 for ranking fields while retaining all-variant reachability.

    The q50 record is the central directional assignment and therefore the
    product's point estimate.  ``vehicles_no_detour`` is not a cost estimate:
    it is a hard safety/feasibility condition, so its maximum is retained over
    q10/q50/q90.
    """
    if not variant_records:
        raise ValueError("at least one variant record is required")
    for record in variant_records:
        _validate(record)
    q50_records = [record for record in variant_records
                   if record.get("demand_variant") == "q50"]
    if len(q50_records) != 1:
        raise ValueError(
            "q50 cost requires exactly one q50 demand-variant record")
    q50 = q50_records[0]
    return ClosureCost(
        candidate_id=candidate_id,
        added_vehicle_hours=float(q50["added_vehicle_hours"]),
        added_metres_total=float(q50["added_metres_total"]),
        vehicles_affected=int(q50["vehicles_affected"]),
        vehicles_no_detour=max(int(r["vehicles_no_detour"])
                               for r in variant_records),
    )


def reduce_variant_cost(candidate_id: str,
                        variant_records: Sequence[Mapping],
                        objective_method: str) -> ClosureCost:
    """Apply the explicitly versioned cross-variant cost policy."""
    if objective_method == LEGACY_WORST_COST_OBJECTIVE:
        return worst_variant_cost(candidate_id, variant_records)
    if objective_method == Q50_COST_OBJECTIVE:
        return q50_variant_cost(candidate_id, variant_records)
    raise ValueError(
        f"unsupported closure-cost objective {objective_method!r}")


def rank_closures(costs: Iterable[ClosureCost]) -> tuple[
        tuple[ClosureCost, ...], tuple[ClosureCost, ...]]:
    """Return (ranked_viable, disqualified), each in deterministic order.

    Disqualified candidates are never ranked against viable ones — a schedule
    that severs someone's destination is not comparable to one that merely
    lengthens journeys.
    """
    costs = list(costs)
    viable = sorted((c for c in costs if not c.disqualified),
                    key=lambda c: c.sort_key)
    refused = sorted((c for c in costs if c.disqualified),
                     key=lambda c: (-c.vehicles_no_detour, c.candidate_id))
    return tuple(viable), tuple(refused)
