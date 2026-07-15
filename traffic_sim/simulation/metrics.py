"""SUMO disruption metrics shared by closure and signal experiments.

The score is deliberately independent of GEH: a sensor count can look fine
while vehicles spend much longer waiting.  ``timeLoss`` is therefore the
primary score, always accompanied by integrity guards.  A lower time loss is
not an improvement when the run teleported or dropped vehicles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence
import xml.etree.ElementTree as ET


def _number(element: ET.Element | None, name: str, default: float = 0.0) -> float:
    """Read a SUMO numeric attribute, tolerating absent optional attributes."""
    if element is None:
        return default
    try:
        return float(element.get(name, default))
    except (TypeError, ValueError):
        return default


def _integer(element: ET.Element | None, name: str, default: int = 0) -> int:
    return int(_number(element, name, default))


@dataclass(frozen=True)
class TripInfoMetrics:
    """Values directly summed from SUMO's per-vehicle tripinfo output."""

    trip_count: int
    total_time_loss_s: float
    unfinished_trips: int
    unfinished_waiting_trips: int


@dataclass(frozen=True)
class StatisticsMetrics:
    """End-of-run integrity information from SUMO's statistics output."""

    loaded: int
    inserted: int
    running_at_end: int
    waiting_at_end: int
    teleport_total: int
    teleport_reasons: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DisruptionMetrics:
    """Scorecard for one SUMO run (or an aggregation of comparable runs).

    ``max_queue_vehicles`` is diagnostic only. It is the largest network-wide
    ``halting`` count in SUMO's summary output, a coarse queue proxy.  SUMO's
    dedicated queue-output is experimental; this field must never be used as
    the ranking score.
    """

    total_time_loss_s: float
    trip_count: int
    unfinished_trips: int
    unfinished_waiting_trips: int
    teleport_total: int
    teleport_reasons: Mapping[str, int]
    loaded: int
    inserted: int
    running_at_end: int
    waiting_at_end: int
    truncated_unreachable: int = 0
    dropped_unreachable: int = 0
    max_queue_vehicles: int | None = None
    closed_edge_throughput: int | None = None

    @property
    def stranded_unreachable(self) -> int:
        """Pre-filtered no-detour vehicles: shortened plus never-departed."""
        return self.truncated_unreachable + self.dropped_unreachable

    @property
    def stranded_waiting(self) -> int:
        """Unfinished tripinfos that had accumulated waiting time at cutoff."""
        return self.unfinished_waiting_trips


@dataclass(frozen=True)
class MetricComparison:
    """Candidate minus baseline. Positive delta time loss is worse."""

    baseline: DisruptionMetrics
    candidate: DisruptionMetrics
    delta_time_loss_s: float
    delta_unfinished_trips: int
    delta_teleports: int
    delta_dropped_unreachable: int
    candidate_disqualified: bool
    disqualification_reasons: tuple[str, ...]


def read_tripinfo(path: Path) -> TripInfoMetrics:
    """Parse a SUMO ``--tripinfo-output`` XML file.

    With ``tripinfo-output.write-unfinished=true``, unfinished vehicles have
    no arrival or a negative arrival.  Their accumulated timeLoss is retained
    in the total, while their count remains visible as a guard metric.
    """
    trips = list(ET.parse(path).getroot().iter("tripinfo"))
    unfinished = []
    for trip in trips:
        arrival = trip.get("arrival")
        try:
            done = arrival is not None and float(arrival) >= 0
        except ValueError:
            done = False
        if not done:
            unfinished.append(trip)
    return TripInfoMetrics(
        trip_count=len(trips),
        total_time_loss_s=sum(_number(trip, "timeLoss") for trip in trips),
        unfinished_trips=len(unfinished),
        unfinished_waiting_trips=sum(_number(trip, "waitingTime") > 0
                                    for trip in unfinished),
    )


def read_statistics(path: Path) -> StatisticsMetrics:
    """Parse SUMO ``--statistic-output`` XML, including teleport reasons."""
    root = ET.parse(path).getroot()
    vehicles = root.find("vehicles")
    teleports = root.find("teleports")
    reasons = {}
    if teleports is not None:
        reasons = {key: _integer(teleports, key)
                   for key in teleports.attrib if key != "total"}
    return StatisticsMetrics(
        loaded=_integer(vehicles, "loaded"),
        inserted=_integer(vehicles, "inserted"),
        running_at_end=_integer(vehicles, "running"),
        waiting_at_end=_integer(vehicles, "waiting"),
        teleport_total=_integer(teleports, "total"),
        teleport_reasons=reasons,
    )


def read_closed_edge_throughput(path: Path, closed_edges: set[str]) -> int:
    """Sum edgeData ``entered`` values for the closed edge(s), if applicable."""
    return sum(_integer(edge, "entered")
               for edge in ET.parse(path).getroot().iter("edge")
               if edge.get("id") in closed_edges)


def active_closure_throughput(flows: Mapping[str, Sequence[float]],
                              closures: Sequence[Mapping[str, object]],
                              interval_s: int = 900) -> int | None:
    """Return entries on closed edges during fully-contained flow buckets.

    SUMO edgeData is aggregated by interval, so a bucket crossing a closure
    boundary cannot safely be assigned to before or after closure. Count only
    complete buckets inside ``[begin_s, end_s)``. This avoids falsely failing
    vehicles that entered before closure start while making measured active
    closure flow a hard integrity signal.
    """
    total = 0.0
    measured = False
    seen: set[tuple[str, int]] = set()
    for closure in closures:
        edge = closure.get("edge_id")
        begin = closure.get("begin_s")
        end = closure.get("end_s")
        if not isinstance(edge, str) or not isinstance(begin, (int, float)) or \
           not isinstance(end, (int, float)) or end <= begin:
            raise ValueError("closures require edge_id and increasing begin_s/end_s")
        first_full = int((begin + interval_s - 1) // interval_s)
        end_full = int(end // interval_s)
        series = flows.get(edge)
        if series is None:
            continue
        for quarter in range(first_full, min(end_full, len(series))):
            if (edge, quarter) not in seen:
                seen.add((edge, quarter))
                total += float(series[quarter])
                measured = True
    return int(round(total)) if measured else None


def read_summary_max_queue(path: Path) -> int:
    """Largest summary ``halting`` count, reported as a diagnostic proxy.

    This is deliberately not a primary metric and is not presented as SUMO's
    experimental per-lane queue-output.  It is nevertheless available from
    the opt-in summary file for every run, which gives batch tools a stable,
    conservative signal until a validated queue-output integration exists.
    """
    return max((_integer(step, "halting")
                for step in ET.parse(path).getroot().iter("step")), default=0)


def build_metrics(tripinfo_path: Path, statistics_path: Path, *,
                  truncated_unreachable: int = 0,
                  dropped_unreachable: int = 0,
                  summary_path: Path | None = None,
                  max_queue_vehicles: int | None = None,
                  closed_edge_throughput: int | None = None) -> DisruptionMetrics:
    """Combine SUMO files with optional experiment-specific guard inputs."""
    trips = read_tripinfo(tripinfo_path)
    stats = read_statistics(statistics_path)
    if summary_path is not None:
        if max_queue_vehicles is not None:
            raise ValueError("provide summary_path or max_queue_vehicles, not both")
        max_queue_vehicles = read_summary_max_queue(summary_path)
    return DisruptionMetrics(
        total_time_loss_s=trips.total_time_loss_s,
        trip_count=trips.trip_count,
        unfinished_trips=trips.unfinished_trips,
        unfinished_waiting_trips=trips.unfinished_waiting_trips,
        teleport_total=stats.teleport_total,
        teleport_reasons=stats.teleport_reasons,
        loaded=stats.loaded,
        inserted=stats.inserted,
        running_at_end=stats.running_at_end,
        waiting_at_end=stats.waiting_at_end,
        truncated_unreachable=truncated_unreachable,
        dropped_unreachable=dropped_unreachable,
        max_queue_vehicles=max_queue_vehicles,
        closed_edge_throughput=closed_edge_throughput,
    )


def closure_edge_leaked(throughput: int | None) -> bool:
    """True only when a closed edge was MEASURED to carry real traffic.

    None (never measured) and 0 (measured, genuinely clean) must both read
    as "not a leak" — the same "missing != zero" distinction this
    project's own conventions require elsewhere. Shared between
    disqualification_reasons() below and run_scenario.py's
    closure_integrity_status() (found independently defining this same
    threshold in review 2026-07-12) so a future tolerance change can never
    silently apply to only one of the two."""
    return throughput is not None and throughput > 0


def disqualification_reasons(metrics: DisruptionMetrics) -> tuple[str, ...]:
    """Return hard integrity failures; no time-loss trade-off is permitted."""
    reasons = []
    if metrics.teleport_total:
        reasons.append("teleports")
    if metrics.dropped_unreachable:
        reasons.append("dropped_unreachable_vehicles")
    if closure_edge_leaked(metrics.closed_edge_throughput):
        reasons.append("active_closure_edge_throughput")
    return tuple(reasons)


def is_disqualified(metrics: DisruptionMetrics) -> bool:
    """True if the candidate must not be ranked, regardless of timeLoss."""
    return bool(disqualification_reasons(metrics))


def compare_metrics(baseline: DisruptionMetrics,
                    candidate: DisruptionMetrics) -> MetricComparison:
    """Compare runs made with the same demand, seed set and demand variants."""
    reasons = disqualification_reasons(candidate)
    return MetricComparison(
        baseline=baseline,
        candidate=candidate,
        delta_time_loss_s=candidate.total_time_loss_s - baseline.total_time_loss_s,
        delta_unfinished_trips=candidate.unfinished_trips - baseline.unfinished_trips,
        delta_teleports=candidate.teleport_total - baseline.teleport_total,
        delta_dropped_unreachable=(candidate.dropped_unreachable -
                                   baseline.dropped_unreachable),
        candidate_disqualified=bool(reasons),
        disqualification_reasons=reasons,
    )
