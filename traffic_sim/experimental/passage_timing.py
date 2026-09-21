"""Departure-quarter-to-passage-quarter diagnostic (DYNAMIC-SENSOR-PASSAGE-2026-09-08).

``pfe.py`` accumulates ``achieved[edge][i] += 1.0`` for every unique edge on a
route whose departure was assigned to quarter ``i`` (see
``prepare_calibration``/the interval-solve loop around line 3123) — the
solver's own accounting of "how many vehicles crossed this edge in quarter
i" is therefore a DEPARTURE-quarter count, not a measured passage time. SUMO's
own edgeData ``entered`` counter and vehroute ``exitTimes`` record when a
vehicle actually enters/leaves an edge. For a short trip near its origin the
two coincide; for a longer trip, or a sensor edge far from where most
candidate routes depart, they can diverge by one or more 15-minute quarters.
This module measures that divergence. It does not change ``achieved``,
``pfe.py``'s targets, or any published artifact — it is read-only evidence
plus a small forward-projection prototype, exactly as scoped by
``docs/plans`` (IMPROVEMENT_PLAN.md "Research reassessment after user
challenge — 2026-09-08", priority 1).

Passage semantics: a vehicle's ENTRY time into an edge is used as its
"passage" instant, matching SUMO edgeData's own ``entered`` attribute (which
buckets a crossing by when the vehicle enters the edge during the interval,
not when it leaves). Entry to edge k is exit time of edge k-1. A vehicle
emitted on edge k == 0 is excluded because edgeData records that event as
``departed``, not ``entered``. Every interval is a half-open
``[q*900, (q+1)*900)`` second window, matching
``write_edgedata_additional``'s ``period="900"`` and
``run_scenario._EdgeDataTarget``'s ``int(begin // 900)`` bucketing exactly.
"""

from __future__ import annotations

import hashlib
import math
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

QUARTER_SECONDS = 900


def quarter_index(t_s: float, quarter_seconds: int = QUARTER_SECONDS) -> int:
    """Half-open ``[q*quarter_seconds, (q+1)*quarter_seconds)`` bucket index.

    ``math.floor`` (not ``int()``) so a hypothetical negative time floors
    toward the correct (more negative) bucket instead of truncating toward
    zero; this project's own simulation clock never produces one, but the
    boundary rule should not silently depend on the sign of the input.
    """
    return math.floor(t_s / quarter_seconds)


@dataclass(frozen=True)
class EdgePassage:
    """One measured crossing of a target edge by one calibrated vehicle.

    ``occurrence`` is the 0-based index of this visit among the vehicle's own
    repeated visits to ``edge_id`` (a route can revisit an edge). ``pfe.py``'s
    ``achieved[edge][i]`` uses ``set(cand.edges)`` — it counts a route once per
    UNIQUE edge regardless of how many times that edge is actually driven, so
    any ``occurrence >= 1`` here is real crossing mass the solver's own
    accounting has already collapsed away, a separate finding from timing lag.
    """

    vehicle_id: str
    edge_id: str
    occurrence: int
    departure_quarter: int
    depart_s: float
    entry_s: float
    passage_quarter: int

    @property
    def lag_quarters(self) -> int:
        return self.passage_quarter - self.departure_quarter

    @property
    def lag_s(self) -> float:
        return self.entry_s - self.depart_s


@dataclass(frozen=True)
class MissingPassage:
    """A target-edge visit whose entry time SUMO never recorded.

    SUMO's ``--vehroute-output.write-unfinished`` marks a vehicle still on an
    edge at run end with exit time ``-1`` for that edge and every edge after
    it (see ``run_scenario.parse_vehroute_file``). The edge the vehicle was
    actually driving on when the run ended still has a known ENTRY time from
    the previous edge's exit — see ``vehicle_target_passages`` — so only edges
    strictly after that one are reported here. The first route edge is an
    emission and is excluded from ``entered`` accounting altogether.
    """

    vehicle_id: str
    edge_id: str
    occurrence: int
    departure_quarter: int
    depart_s: float
    reason: str


MISSING_REASON_UNREACHED = "unfinished_evidence_ends_before_edge"


def vehicle_target_passages(
    vehicle_id: str,
    depart_s: float,
    edges: Sequence[str],
    exit_times: Sequence[float],
    target_edges: frozenset[str] | set[str],
    quarter_seconds: int = QUARTER_SECONDS,
) -> tuple[list[EdgePassage], list[MissingPassage]]:
    """Pure per-vehicle mapping: no file I/O, no hardcoded edge IDs.

    ``edges``/``exit_times`` are the FULL driven route and SUMO's own
    ``exitTimes`` for it, equal length, exactly as ``vehroute-output`` writes
    them (see ``run_scenario.final_route``). ``target_edges`` is supplied by
    the caller — this function has no opinion on which edges are sensors, so
    extending the sensor registry never touches this code.

    The first route edge is never returned: SUMO records a vehicle emitted on
    that edge as ``departed`` rather than ``entered``. A later revisit to the
    same target edge is returned normally.
    """
    if len(edges) != len(exit_times):
        raise ValueError("edges and exit_times must be the same length")
    departure_quarter = quarter_index(depart_s, quarter_seconds)
    passages: list[EdgePassage] = []
    missing: list[MissingPassage] = []
    occurrence_by_edge: dict[str, int] = {}
    entry_s = depart_s
    entry_known = True
    for edge_index, (edge_id, exit_s) in enumerate(zip(edges, exit_times)):
        occurrence = occurrence_by_edge.get(edge_id, 0)
        occurrence_by_edge[edge_id] = occurrence + 1
        if edge_id in target_edges and edge_index > 0:
            if entry_known:
                passages.append(EdgePassage(
                    vehicle_id=vehicle_id, edge_id=edge_id,
                    occurrence=occurrence,
                    departure_quarter=departure_quarter, depart_s=depart_s,
                    entry_s=entry_s,
                    passage_quarter=quarter_index(entry_s, quarter_seconds)))
            else:
                missing.append(MissingPassage(
                    vehicle_id=vehicle_id, edge_id=edge_id,
                    occurrence=occurrence,
                    departure_quarter=departure_quarter, depart_s=depart_s,
                    reason=MISSING_REASON_UNREACHED))
        if exit_s < 0:
            # SUMO never recorded leaving this edge: it is the vehicle's
            # last known position. For a non-initial target its own entry (just
            # recorded above) stays valid; every edge after it is unreachable
            # evidence, not a zero-lag guess.
            entry_known = False
        else:
            entry_s = exit_s
    return passages, missing


@dataclass(frozen=True)
class EdgeTimingSummary:
    """Aggregate departure->passage evidence for one target edge."""

    edge_id: str
    n_passages: int
    n_missing: int
    cross_quarter_fraction: float | None
    median_lag_quarters: float | None
    mean_lag_quarters: float | None
    median_lag_s: float | None
    out_of_window_passages: int
    lag_histogram: dict[int, int]
    missing_by_reason: dict[str, int]


def summarize_edge_timing(
    passages: Sequence[EdgePassage],
    missing: Sequence[MissingPassage],
    edge_id: str,
    n_intervals: int,
) -> EdgeTimingSummary:
    """Pure aggregation for one edge. ``n_intervals`` only affects which
    passages are flagged out-of-window; every passage is still counted."""
    own = [p for p in passages if p.edge_id == edge_id]
    own_missing = [m for m in missing if m.edge_id == edge_id]
    lag_hist: dict[int, int] = {}
    for p in own:
        lag_hist[p.lag_quarters] = lag_hist.get(p.lag_quarters, 0) + 1
    missing_by_reason: dict[str, int] = {}
    for m in own_missing:
        missing_by_reason[m.reason] = missing_by_reason.get(m.reason, 0) + 1
    out_of_window = sum(
        1 for p in own
        if not (0 <= p.departure_quarter < n_intervals)
        or not (0 <= p.passage_quarter < n_intervals))
    if own:
        lags_q = [p.lag_quarters for p in own]
        lags_s = [p.lag_s for p in own]
        cross_quarter_fraction = sum(1 for lag in lags_q if lag != 0) / len(own)
        median_lag_quarters = statistics.median(lags_q)
        mean_lag_quarters = statistics.fmean(lags_q)
        median_lag_s = statistics.median(lags_s)
    else:
        cross_quarter_fraction = None
        median_lag_quarters = None
        mean_lag_quarters = None
        median_lag_s = None
    return EdgeTimingSummary(
        edge_id=edge_id,
        n_passages=len(own),
        n_missing=len(own_missing),
        cross_quarter_fraction=cross_quarter_fraction,
        median_lag_quarters=median_lag_quarters,
        mean_lag_quarters=mean_lag_quarters,
        median_lag_s=median_lag_s,
        out_of_window_passages=out_of_window,
        lag_histogram=dict(sorted(lag_hist.items())),
        missing_by_reason=missing_by_reason,
    )


def parse_vehroute_target_passages(
    vr_path: Path,
    target_edges: frozenset[str] | set[str],
    quarter_seconds: int = QUARTER_SECONDS,
) -> tuple[list[EdgePassage], list[MissingPassage], int]:
    """Stream a vehroute-output file and map every target-edge visit.

    Returns ``(passages, missing, n_vehicles_total)``. ``n_vehicles_total``
    counts every ``<vehicle>`` element in the file, whether or not it ever
    touches a target edge, so a caller can verify no vehicle silently
    disappeared from the diagnostic's accounting (mass conservation).

    ``iterparse`` with ``clear()`` mirrors ``run_scenario.parse_vehroute_file``
    — a whole-day vehroute file has tens of thousands of route elements and
    building a full ElementTree measurably dominates memory otherwise.
    """
    from run_scenario import final_route  # local import: keep pure functions above I/O-free

    n_vehicles_total = 0
    all_passages: list[EdgePassage] = []
    all_missing: list[MissingPassage] = []
    context = ET.iterparse(vr_path, events=("end",))
    for event, el in context:
        if el.tag != "vehicle":
            continue
        n_vehicles_total += 1
        route = final_route(el)
        if route is None:
            el.clear()
            continue
        edges_attr = route.get("edges")
        exits_attr = route.get("exitTimes")
        if not edges_attr or not exits_attr:
            el.clear()
            continue
        edges = edges_attr.split()
        exit_times = [float(t) for t in exits_attr.split()]
        depart_s = float(el.get("depart"))
        if len(edges) != len(exit_times):
            # Malformed/truncated record: report nothing for this vehicle
            # rather than guessing an alignment. n_vehicles_total already
            # counted it, so the mass-conservation check surfaces the gap.
            el.clear()
            continue
        passages, missing = vehicle_target_passages(
            el.get("id", ""), depart_s, edges, exit_times, target_edges,
            quarter_seconds)
        all_passages.extend(passages)
        all_missing.extend(missing)
        el.clear()
    return all_passages, all_missing, n_vehicles_total


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_departure_to_passage_report(
    vr_path: Path,
    target_edges: Mapping[str, str] | Iterable[str],
    n_intervals: int,
    quarter_seconds: int = QUARTER_SECONDS,
) -> dict:
    """Bound, hashed, reproducible report over one vehroute evidence file.

    ``target_edges`` may be a plain iterable of edge ids or a mapping of
    edge_id -> sensor_id (e.g. built from ``run_scenario.sensor_audit_edges``)
    so the report can label each edge with its station without this module
    ever hardcoding a sensor id itself.
    """
    if isinstance(target_edges, Mapping):
        edge_to_sensor = dict(target_edges)
    else:
        edge_to_sensor = {edge_id: None for edge_id in target_edges}
    edges_frozen = frozenset(edge_to_sensor)
    passages, missing, n_vehicles_total = parse_vehroute_target_passages(
        vr_path, edges_frozen, quarter_seconds)
    edges_section = []
    for edge_id in sorted(edges_frozen):
        summary = summarize_edge_timing(passages, missing, edge_id, n_intervals)
        edges_section.append({
            "edge_id": edge_id,
            "sensor_id": edge_to_sensor.get(edge_id),
            "n_passages": summary.n_passages,
            "n_missing": summary.n_missing,
            "cross_quarter_fraction": summary.cross_quarter_fraction,
            "median_lag_quarters": summary.median_lag_quarters,
            "mean_lag_quarters": summary.mean_lag_quarters,
            "median_lag_s": summary.median_lag_s,
            "out_of_window_passages": summary.out_of_window_passages,
            "lag_histogram": summary.lag_histogram,
            "missing_by_reason": summary.missing_by_reason,
        })
    return {
        "diagnostic": "departure_to_passage_timing",
        "vehroute_path": str(vr_path),
        "vehroute_sha256": file_sha256(vr_path),
        "n_intervals": n_intervals,
        "quarter_seconds": quarter_seconds,
        "n_vehicles_total": n_vehicles_total,
        "n_target_edges": len(edges_frozen),
        "n_target_edge_passages": len(passages),
        "n_target_edge_missing": len(missing),
        "edges": edges_section,
    }


# --------------------------------------------------------------------------
# Sparse dynamic-assignment prototype (Work item 4).
#
# A "kernel" is a sparse, empirically observed mapping from a departure
# quarter to a probability distribution over lag (passage_quarter -
# departure_quarter), for ONE edge. It is estimated from real EdgePassage
# evidence, or supplied directly for a synthetic round-trip test. Projecting
# a departure-count vector through a kernel answers: "if these vehicles
# departed as counted, when would they actually be observed passing this
# edge?" — the question pfe.py's achieved[edge][i] currently answers
# incorrectly for any lag != 0. This NEVER writes back into pfe.py or any
# published target; it exists to measure whether the lag is big enough to
# matter before anyone proposes changing the solver.
# --------------------------------------------------------------------------


def build_lag_kernel(
    passages: Sequence[EdgePassage],
    edge_id: str,
) -> dict[int, dict[int, float]]:
    """Empirical departure_quarter -> {lag_quarters: probability} for one edge.

    Sparse by construction: only (departure_quarter, lag) pairs actually seen
    in ``passages`` appear. A departure quarter with zero observed crossings
    of ``edge_id`` is simply absent from the kernel — see
    ``project_departure_to_passage`` for how that is surfaced, never guessed.
    """
    counts: dict[int, dict[int, int]] = {}
    for p in passages:
        if p.edge_id != edge_id:
            continue
        by_lag = counts.setdefault(p.departure_quarter, {})
        by_lag[p.lag_quarters] = by_lag.get(p.lag_quarters, 0) + 1
    kernel: dict[int, dict[int, float]] = {}
    for dep_q, by_lag in counts.items():
        total = sum(by_lag.values())
        kernel[dep_q] = {lag: n / total for lag, n in by_lag.items()}
    return kernel


@dataclass(frozen=True)
class ProjectionResult:
    """Forward projection of a departure-count vector through a lag kernel.

    ``passage_counts`` has the same length as the input departure vector.
    ``unprojectable_mass`` is vehicles departing a quarter the kernel has no
    observed lag distribution for (never given an invented lag=0).
    ``out_of_window_mass`` is vehicles whose projected passage quarter falls
    outside ``[0, n_intervals)`` (a real tail/warm-up effect, not a bug).
    ``total_input`` == sum(passage_counts) + unprojectable_mass +
    out_of_window_mass, to any floating tolerance — this is the mass-
    conservation identity a caller should check.
    """

    passage_counts: tuple[float, ...]
    unprojectable_mass: float
    out_of_window_mass: float
    total_input: float


def project_departure_to_passage(
    departure_counts: Sequence[float],
    kernel: Mapping[int, Mapping[int, float]],
    n_intervals: int,
) -> ProjectionResult:
    passage = [0.0] * n_intervals
    unprojectable = 0.0
    out_of_window = 0.0
    total_input = 0.0
    for dep_q, count in enumerate(departure_counts):
        total_input += count
        if count <= 0:
            continue
        dist = kernel.get(dep_q)
        if not dist:
            unprojectable += count
            continue
        for lag, p in dist.items():
            pass_q = dep_q + lag
            mass = count * p
            if 0 <= pass_q < n_intervals:
                passage[pass_q] += mass
            else:
                out_of_window += mass
    return ProjectionResult(
        passage_counts=tuple(passage),
        unprojectable_mass=unprojectable,
        out_of_window_mass=out_of_window,
        total_input=total_input,
    )
