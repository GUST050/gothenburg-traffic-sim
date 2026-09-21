"""How many drivers a closure delays, and by how much — as a distribution.

WHY THIS EXISTS (2026-09-21). The ranking objective already knows the answer
per vehicle. `disruption._report` prices every affected origin-destination
pair with and without the closure, builds `added_seconds` — one entry per
affected vehicle — and then collapses that list into a sum
(`added_vehicle_hours`), a median, and throws the list away.

Both survivors hide the shape. `run_scenario.closure_disruption` states the
problem in its own docstring: "The MEDIAN is often zero even for a heavily
used edge, because a dense grid gives most drivers a free parallel street; the
cost lands on a tail, which only the total captures." A total says how much
delay exists. It cannot say whether that is ten thousand drivers losing four
seconds each or two hundred losing three minutes — which is the difference
between a closure nobody notices and one that produces complaints. The
distribution is the only form that separates them, and it is already computed.

WHAT THE SECONDS MEAN. The same thing they mean in the ranking, because they
are literally the same numbers: for each affected vehicle, the cheapest legal
free-flow path from its origin to its destination WITH the closure, minus the
cheapest one WITHOUT it. Not SUMO time loss. That distinction is not a
limitation being apologised for — it is the measured finding that produced
this objective (`closure_ranking`): on this network simulated time loss moved
+0.050 s and -0.100 s per vehicle across arms, and a closure cannot REDUCE
time loss, so both are noise around zero. There is no congestion for a closure
to disturb, because only sensor-crossing traffic is simulated and the network
runs at free flow. A histogram of that noise would be a picture of nothing.

WHY IT IS NOT IN THE EVIDENCE THE SEARCH PUBLISHES.
`deterministic_disruption.COSTING_SOURCES` hashes `disruption.py`,
`closure_ranking.py`, `deterministic_disruption.py` and `run_scenario.py` into
every daily-cost cache key, so editing any of them to emit one more field
would invalidate every cached cost a finished search owns. This module
therefore recomputes from the same archives instead, and proves it landed on
the same population: `verify_against_cost` refuses to publish a curve whose
totals do not reproduce the candidate's published closure cost exactly.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from traffic_sim.simulation import disruption as _disruption
from traffic_sim.simulation.closure_ranking import worst_variant_cost


#: Contract version of the published profile artifact.
SCHEMA_VERSION = 1

#: What one bucket counts. Frozen, because a curve whose bins move between two
#: builds cannot be compared with an earlier one even though both look like a
#: distribution of the same quantity.
MEASURE = "deterministic_detour_seconds_v1"

#: Bucket width and the last bucket that gets its own bar. Fifteen seconds is
#: not arbitrary: it is the resolution everything else in this project uses
#: (the flow buckets, the closure interval grid), and it is fine enough that
#: a typical inner-city detour lands in the middle of the axis rather than in
#: an overflow bar.
BIN_WIDTH_S = 15
BIN_COUNT = 20
OVERFLOW_FROM_S = BIN_WIDTH_S * BIN_COUNT      # 300 s

#: Vehicles whose optimal detour costs exactly nothing get their own bucket
#: instead of being folded into the first bar. They are a real and separately
#: meaningful population — the drivers the dense grid rescues for free — and
#: burying them in "0–15 s" would make the first bar the biggest one on the
#: chart for a reason that has nothing to do with delay.
ZERO_BIN_INDEX = 0

DEMAND_VARIANTS = ("q10", "q50", "q90")

#: The candidate ordering the project already uses (`period_comparison`
#: and `closure_ranking` agree on it): added vehicle-hours first, then extra
#: distance, then how many drivers were disturbed, then the id for stability.
#: Re-stated here rather than imported because `period_comparison._cost_key`
#: is private and this module must not become a second implementation of the
#: RANKING — it only needs to read the ordering a finished search produced.
def cost_sort_key(cost: Mapping[str, Any], candidate_id: str) -> tuple:
    return (
        float(cost["added_vehicle_hours"]),
        float(cost["added_metres_total"]),
        int(cost["vehicles_affected"]),
        str(candidate_id),
    )


class DelayProfileError(Exception):
    """A profile could not be produced, or would not describe what it claims."""


def bin_definitions() -> tuple[dict[str, Any], ...]:
    """Every bucket, in axis order, with the labels the UI prints."""
    bins: list[dict[str, Any]] = [{
        "index": ZERO_BIN_INDEX,
        "from_s": 0,
        "to_s": 0,
        "label": "0 s",
        "description": "ingen extra restid — omvägen kostar inget",
    }]
    for step in range(BIN_COUNT):
        low = step * BIN_WIDTH_S
        high = low + BIN_WIDTH_S
        bins.append({
            "index": len(bins),
            "from_s": low,
            "to_s": high,
            "label": f"{low}–{high} s",
            "description": f"mer än {low} s och högst {high} s extra restid",
        })
    bins.append({
        "index": len(bins),
        "from_s": OVERFLOW_FROM_S,
        "to_s": None,
        "label": f"> {OVERFLOW_FROM_S // 60} min",
        "description": f"mer än {OVERFLOW_FROM_S} s extra restid",
    })
    return tuple(bins)


BINS = bin_definitions()


def bin_index(added_seconds: float) -> int:
    """Which bucket one vehicle's added seconds belongs to.

    ``added_seconds`` is clamped at zero by the costing itself (a detour can
    never be cheaper than the unrestricted optimum over the same graph), so
    an exact zero is a real category rather than a rounding artifact.
    """
    if added_seconds <= 0.0:
        return ZERO_BIN_INDEX
    if added_seconds > OVERFLOW_FROM_S:
        return len(BINS) - 1
    # Upper-inclusive: 15.0 s belongs to "0–15 s", not to "15–30 s".
    step = int((added_seconds - 1e-9) // BIN_WIDTH_S)
    return min(step + 1, len(BINS) - 2)


@dataclass(frozen=True)
class DelayHistogram:
    """One route file's added-time distribution, plus its own audit trail."""

    #: Vehicle counts, one per entry of `BINS`, in axis order.
    vehicles: tuple[int, ...]
    #: The aggregate record `disruption._report` builds from the SAME prices.
    #: Published beside the curve so a reader can check the two agree without
    #: rerunning anything.
    report: Mapping[str, Any]
    #: Affected vehicles whose ORIGIN-destination pair has no priced baseline
    #: path at all. `_report` drops these silently (`if baseline_s is None:
    #: continue`), so they appear in `vehicles_affected` while contributing to
    #: neither the delay total nor `vehicles_no_detour`. Counted here so the
    #: parts add up to the whole and the gap is visible instead of implied.
    vehicles_unpriced: int

    @property
    def vehicles_in_bins(self) -> int:
        return sum(self.vehicles)

    def accounting(self) -> dict[str, int]:
        """Where every affected vehicle went. These must sum to the whole."""
        return {
            "vehicles_affected": int(self.report["vehicles_affected"]),
            "vehicles_in_bins": self.vehicles_in_bins,
            "vehicles_no_detour": int(self.report["vehicles_no_detour"]),
            "vehicles_unpriced": int(self.vehicles_unpriced),
        }


def _prices(
    od_counts: Counter,
    adjacency: Mapping[str, Sequence[str]],
    edge_time: Mapping[str, float],
    edge_length: Mapping[str, float],
    closed: frozenset[str],
):
    """The four cost tables, priced through the production router.

    Both the histogram and the aggregate record are built from these same four
    tables. Pricing once is not only faster — it is what makes the two
    numerically inseparable, so the curve cannot drift from the total it is
    drawn beside.
    """
    pairs = tuple(od_counts)
    if len(pairs) >= _disruption.SPARSE_ROUTING_MIN_PAIRS:
        price = _disruption.SparsePathBatch(pairs, adjacency).path_costs
    else:
        def price(costs, banned):
            return _disruption.grouped_path_costs(
                pairs, adjacency, costs, banned)
    return (
        price(edge_time, frozenset()),
        price(edge_length, frozenset()),
        price(edge_time, closed),
        price(edge_length, closed),
    )


def delay_histogram(
    route_path: Path,
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Mapping[str, float],
    edge_length: Mapping[str, float],
    *,
    adjacency: Mapping[str, Sequence[str]],
) -> DelayHistogram:
    """One demand variant's added-time distribution for one closure.

    Deliberately built on `disruption`'s own private helpers rather than a
    copy of them. A second implementation of "which vehicles does this closure
    strike, and what does their detour cost" is exactly how a chart starts
    disagreeing with the ranking it illustrates — and editing `disruption.py`
    to make them public is not available, because its bytes are hashed into
    every finished search's daily-cost cache key.
    """
    route_path = Path(route_path)
    if not closed_edges:
        raise DelayProfileError("a delay profile needs at least one closed edge")
    if not route_path.is_file():
        raise DelayProfileError(f"route file is missing: {route_path}")

    considered, affected, denied, od_counts = _disruption._affected_od_counts(
        route_path, set(closed_edges), closures, edge_time)
    closed = frozenset(closed_edges)
    base_time, base_length, detour_time, detour_length = _prices(
        od_counts, adjacency, edge_time, edge_length, closed)

    vehicles = [0] * len(BINS)
    unpriced = 0
    for pair, multiplicity in od_counts.items():
        baseline_s = base_time[pair]
        detour_s = detour_time[pair]
        if baseline_s is None:
            unpriced += multiplicity
            continue
        if detour_s is None:
            continue            # severed: counted by _report as no_detour
        vehicles[bin_index(max(detour_s - baseline_s, 0.0))] += multiplicity

    report = _disruption._report(
        considered=considered,
        affected=affected,
        denied=denied,
        od_counts=od_counts,
        base_time=base_time,
        base_length=base_length,
        detour_time=detour_time,
        detour_length=detour_length,
    )
    histogram = DelayHistogram(
        vehicles=tuple(vehicles), report=report, vehicles_unpriced=unpriced)
    accounting = histogram.accounting()
    if (accounting["vehicles_in_bins"] + accounting["vehicles_no_detour"]
            + accounting["vehicles_unpriced"]) != accounting["vehicles_affected"]:
        raise DelayProfileError(
            f"delay histogram does not account for every affected vehicle: "
            f"{accounting}")
    return histogram


def sum_histograms(parts: Sequence[DelayHistogram]) -> dict[str, Any]:
    """Add one variant's daily units into the parent schedule's own curve.

    Bin-wise addition is the distributional form of what
    `deterministic_disruption.sum_daily_disruption` does to the aggregates:
    sum the days FIRST, within one direction-split variant, and only reduce
    across variants afterwards. Reducing first and summing after would let a
    schedule take one day from q10 and another from q90 and add them —
    describing a world where the direction split changed overnight.
    """
    if not parts:
        raise DelayProfileError("a schedule must have at least one daily unit")
    vehicles = [0] * len(BINS)
    for part in parts:
        for index, count in enumerate(part.vehicles):
            vehicles[index] += count
    return {
        "vehicles": vehicles,
        "vehicles_affected": sum(
            int(part.report["vehicles_affected"]) for part in parts),
        "vehicles_considered": sum(
            int(part.report["vehicles_considered"]) for part in parts),
        "vehicles_no_detour": sum(
            int(part.report["vehicles_no_detour"]) for part in parts),
        "vehicles_unpriced": sum(part.vehicles_unpriced for part in parts),
        "added_vehicle_hours": round(sum(
            float(part.report["added_vehicle_hours"]) for part in parts), 4),
        "added_metres_total": round(sum(
            float(part.report["added_metres_total"]) for part in parts), 1),
        "daily_unit_count": len(parts),
    }


def verify_against_cost(
    summed_by_variant: Mapping[str, Mapping[str, Any]],
    published_cost: Mapping[str, Any],
    *,
    candidate_id: str,
) -> dict[str, Any]:
    """Refuse a curve that does not reproduce the ranked cost it illustrates.

    The published `closure_cost` is the field-wise worst variant of the summed
    daily records. Reducing this module's own per-variant sums the same way
    must land on the same three numbers. When it does not, the archives, the
    network or the search result have moved apart, and a chart drawn anyway
    would be a confident picture of the wrong run.
    """
    recomputed = worst_variant_cost(candidate_id, [
        {"demand_variant": variant, **dict(values)}
        for variant, values in sorted(summed_by_variant.items())
    ])
    expected = {
        "added_vehicle_hours": float(published_cost["added_vehicle_hours"]),
        "added_metres_total": float(published_cost["added_metres_total"]),
        "vehicles_affected": int(published_cost["vehicles_affected"]),
    }
    measured = {
        "added_vehicle_hours": float(recomputed.added_vehicle_hours),
        "added_metres_total": float(recomputed.added_metres_total),
        "vehicles_affected": int(recomputed.vehicles_affected),
    }
    if measured != expected:
        raise DelayProfileError(
            f"recomputed closure cost does not match the published one for "
            f"{candidate_id}: published={expected} recomputed={measured}")
    return {"published": expected, "recomputed": measured, "matches": True}


def rank_periods(periods: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Viable periods, best first, by the search's own ordering.

    `period_comparison` returns periods in CALENDAR order with a status
    column, so "the two best dates" is not the top of that list — it is the
    two lowest costs in it. Periods without a viable schedule have no cost and
    are not rankable; they are dropped rather than sorted to the end, because
    "no viable schedule" is not a worse score, it is the absence of one.
    """
    rankable = [
        period for period in periods
        if isinstance(period.get("best_cost"), Mapping)
        and isinstance(period.get("best_schedule"), Mapping)
    ]
    rankable.sort(key=lambda period: cost_sort_key(
        period["best_cost"], str(period["best_schedule"].get("schedule_id", ""))))
    return tuple(rankable)


def rank_schedules(
    shortlisted: Sequence[Mapping[str, Any]],
    statistics: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[dict[str, Any], Mapping[str, Any]], ...]:
    """The same ordering for a search that reports schedules, not periods.

    Only candidates that survived every hard gate are rankable: a schedule
    that stranded vehicles is disqualified, not merely expensive, and a curve
    drawn for it would invite exactly the comparison the gate exists to
    prevent.
    """
    rankable: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
    for schedule in shortlisted:
        record = statistics.get(str(schedule.get("schedule_id", "")))
        if not isinstance(record, Mapping) or record.get("hard_failures"):
            continue
        cost = record.get("closure_cost")
        if not isinstance(cost, Mapping):
            continue
        if int(cost.get("vehicles_no_detour", 0)) != 0:
            continue
        rankable.append((dict(schedule), cost))
    rankable.sort(key=lambda item: cost_sort_key(
        item[1], str(item[0].get("schedule_id", ""))))
    return tuple(rankable)
