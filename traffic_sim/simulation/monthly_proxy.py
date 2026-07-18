"""Deterministic screening proxy for recurring monthly closure schedules.

This module is deliberately independent of SUMO and the web application.
It ranks already-enumerated :class:`~traffic_sim.core.contracts.ClosureSchedule`
objects from projected 15-minute edge flows and transparent detour-capacity
features.  Every output is a rank or an input feature; no value is presented
as predicted delay.

Missing data is never converted to zero.  A schedule is scoreable only when
all closed-edge quarters are known and at least one plausible detour path can
be evaluated.  Evidence limitations remain separate from the traffic rank so
they can enlarge the SUMO shortlist or withhold a recommendation.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from traffic_sim.core.contracts import ClosureSchedule


SCHEMA_VERSION = 1
PROXY_VERSION = "monthly_proxy_v1"
SHORTLIST_VERSION = "stratified_shortlist_v2"
_STRUCTURAL_SOURCES = frozenset({
    "forecast_sensor",
    "learned_direction_prior",
    "corridor_prior",
    "assignment_prior",
})


def _finite_nonnegative(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= fraction <= 1:
        raise ValueError("percentile fraction must be from zero through one")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _average_ranks(values: Sequence[float]) -> list[float]:
    """Return zero-based ascending ranks with average tie handling."""
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average = (cursor + end - 1) / 2
        for position in range(cursor, end):
            ranks[order[position]] = average
        cursor = end
    return ranks


def _canonical_key(payload: Mapping[str, Any], *, length: int = 20) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]


@dataclass(frozen=True)
class DetourPath:
    """One topology-valid alternative around the closed worksite."""

    path_id: str
    edges: tuple[str, ...]
    freeflow_s: float
    boundary_pair: tuple[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.path_id, str) or not self.path_id:
            raise ValueError("detour path_id must be non-empty")
        if not self.edges or any(not edge for edge in self.edges):
            raise ValueError("detour path must contain edge IDs")
        if len(set(self.edges)) != len(self.edges):
            raise ValueError("detour path cannot contain a loop")
        if (
            isinstance(self.freeflow_s, bool)
            or not math.isfinite(self.freeflow_s)
            or self.freeflow_s <= 0
        ):
            raise ValueError("detour freeflow_s must be finite and positive")
        if len(self.boundary_pair) != 2 or not all(self.boundary_pair):
            raise ValueError("detour boundary_pair must contain two edge IDs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "edges": list(self.edges),
            "freeflow_s": self.freeflow_s,
            "boundary_pair": list(self.boundary_pair),
        }


@dataclass(frozen=True)
class ShortlistPolicy:
    """Deterministic stratification and evidence-expansion policy."""

    best_overall: int = 24
    best_per_day_count: int = 2
    best_per_date_block: int = 1
    date_block_days: int = 7
    validation_quantiles: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)
    low_support_multiplier: float = 2.0
    unavailable_multiplier: float = 2.5
    bounded_exhaustive_limit: int = 120
    maximum_shortlist: int = 240

    def __post_init__(self) -> None:
        for name in (
            "best_overall",
            "best_per_day_count",
            "best_per_date_block",
            "date_block_days",
            "bounded_exhaustive_limit",
            "maximum_shortlist",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"shortlist policy {name} must be positive")
        if (
            not math.isfinite(self.low_support_multiplier)
            or self.low_support_multiplier < 1
            or not math.isfinite(self.unavailable_multiplier)
            or self.unavailable_multiplier < 1
        ):
            raise ValueError("shortlist expansion multipliers must be at least one")
        if (
            not self.validation_quantiles
            or any(not 0 <= value <= 1 for value in self.validation_quantiles)
        ):
            raise ValueError("validation quantiles must be from zero through one")


def _shortest_path(
    start: str,
    target: str,
    successors: Mapping[str, Sequence[str]],
    edge_cost_s: Mapping[str, float],
    closed: frozenset[str],
    penalties: Mapping[str, float],
) -> tuple[tuple[str, ...], float] | None:
    if start in closed or target in closed:
        return None
    start_cost = _finite_nonnegative(edge_cost_s.get(start))
    if start_cost is None or start_cost <= 0:
        return None
    queue: list[tuple[float, tuple[str, ...], str]] = [
        (start_cost, (start,), start)
    ]
    best: dict[str, float] = {start: start_cost}
    while queue:
        cost, path, edge = heapq.heappop(queue)
        if cost > best.get(edge, math.inf):
            continue
        if edge == target:
            return path, cost
        for successor in sorted(successors.get(edge, ())):
            if successor in closed or successor in path:
                continue
            raw_cost = _finite_nonnegative(edge_cost_s.get(successor))
            if raw_cost is None or raw_cost <= 0:
                continue
            next_cost = cost + raw_cost * float(penalties.get(successor, 1.0))
            if next_cost + 1e-12 < best.get(successor, math.inf):
                best[successor] = next_cost
                heapq.heappush(
                    queue, (next_cost, path + (successor,), successor)
                )
    return None


def plausible_detour_paths(
    closed_edges: Sequence[str],
    successors: Mapping[str, Sequence[str]],
    edge_cost_s: Mapping[str, float],
    *,
    max_paths_per_pair: int = 3,
    max_total_paths: int = 24,
    maximum_cost_factor: float = 1.8,
    reuse_penalty: float = 2.5,
) -> tuple[DetourPath, ...]:
    """Build deterministic free-flow alternatives around a closed edge set.

    The first path for each predecessor/successor boundary pair is shortest
    by edge free-flow time.  Later alternatives penalize already-used
    interior edges, providing a lightweight multi-path corridor rather than
    pretending all diverted traffic follows one canonical route.  Paths more
    than ``maximum_cost_factor`` times the first alternative are discarded.
    """
    closed = frozenset(closed_edges)
    if not closed:
        raise ValueError("closed_edges cannot be empty")
    if max_paths_per_pair <= 0 or max_total_paths <= 0:
        raise ValueError("detour path limits must be positive")
    if maximum_cost_factor < 1 or reuse_penalty <= 1:
        raise ValueError("detour factors are invalid")

    predecessors = {
        edge
        for edge, following in successors.items()
        if edge not in closed and any(item in closed for item in following)
    }
    boundary_successors = {
        item
        for edge in closed
        for item in successors.get(edge, ())
        if item not in closed
    }
    paths: list[DetourPath] = []
    for predecessor in sorted(predecessors):
        for successor in sorted(boundary_successors):
            penalties: dict[str, float] = {}
            seen: set[tuple[str, ...]] = set()
            shortest_cost: float | None = None
            for _ in range(max_paths_per_pair):
                found = _shortest_path(
                    predecessor,
                    successor,
                    successors,
                    edge_cost_s,
                    closed,
                    penalties,
                )
                if found is None:
                    break
                edges, cost = found
                if edges in seen:
                    break
                seen.add(edges)
                if shortest_cost is None:
                    shortest_cost = cost
                elif cost > shortest_cost * maximum_cost_factor:
                    break
                payload = {
                    "boundary_pair": [predecessor, successor],
                    "edges": list(edges),
                }
                paths.append(
                    DetourPath(
                        path_id=f"detour-{_canonical_key(payload)}",
                        edges=edges,
                        freeflow_s=round(cost, 6),
                        boundary_pair=(predecessor, successor),
                    )
                )
                for edge in edges[1:-1]:
                    penalties[edge] = penalties.get(edge, 1.0) * reuse_penalty

    paths.sort(
        key=lambda path: (
            path.boundary_pair,
            path.freeflow_s,
            path.edges,
        )
    )
    return tuple(paths[:max_total_paths])


def schedule_quarter_indices(
    schedule: ClosureSchedule,
    *,
    flow_epoch: datetime,
    interval_minutes: int,
    n_intervals: int,
) -> tuple[int, ...]:
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    interval = timedelta(minutes=interval_minutes)
    quarters: list[int] = []
    for closure in schedule.intervals:
        start = datetime.fromisoformat(closure.start_time)
        end = datetime.fromisoformat(closure.end_time)
        start_offset = (start - flow_epoch).total_seconds()
        end_offset = (end - flow_epoch).total_seconds()
        width_s = interval.total_seconds()
        if start_offset % width_s or end_offset % width_s:
            raise ValueError(
                f"schedule {schedule.schedule_id} is not aligned to flow intervals"
            )
        first = int(start_offset // width_s)
        last = int(end_offset // width_s)
        if first < 0 or last > n_intervals:
            raise ValueError(
                f"schedule {schedule.schedule_id} lies outside projected flows"
            )
        quarters.extend(range(first, last))
    return tuple(quarters)


def _support_level(
    *,
    scoreable: bool,
    structural_coverage: float,
    detour_coverage: float,
    forecast_station_coverage: float,
    spatial_confidence_coverage: float,
    minimum_confidence: float | None,
    road_domain_status: str,
) -> tuple[str, tuple[str, ...]]:
    reasons: list[str] = []
    if not scoreable:
        reasons.append("unscoreable_missing_traffic_or_detour")
        return "unavailable", tuple(reasons)
    if structural_coverage < 1:
        reasons.append("incomplete_structural_field")
    if detour_coverage < 1:
        reasons.append("incomplete_detour_field")
    if forecast_station_coverage < 1:
        reasons.append("incomplete_forecast_station_support")
    if minimum_confidence is None:
        reasons.append("missing_spatial_confidence")
    elif minimum_confidence < 0.2:
        reasons.append("low_spatial_confidence")
    elif minimum_confidence < 0.6:
        reasons.append("moderate_spatial_confidence")
    if road_domain_status == "out_of_domain":
        reasons.append("out_of_domain_road_features")
    elif road_domain_status == "not_evaluated":
        reasons.append("road_feature_domain_not_evaluated")
    if spatial_confidence_coverage < 1:
        reasons.append("incomplete_spatial_confidence")

    if (
        structural_coverage < 0.8
        or detour_coverage < 0.8
        or forecast_station_coverage < 0.5
        or spatial_confidence_coverage < 0.8
        or minimum_confidence is None
        or (minimum_confidence is not None and minimum_confidence < 0.2)
        or road_domain_status != "in_domain"
    ):
        return "low", tuple(reasons)
    if reasons:
        return "medium", tuple(reasons)
    return "high", ()


def score_schedule(
    schedule: ClosureSchedule,
    *,
    closed_edges: Sequence[str],
    projected_flows: Mapping[str, Sequence[float | None]],
    flow_epoch: str,
    interval_minutes: int,
    detour_paths: Sequence[DetourPath],
    capacity_veh_per_interval: Mapping[str, float],
    structural_sources: Mapping[str, str],
    spatial_confidence: Mapping[str, float | None],
    forecast_station_coverage: float = 1.0,
    road_domain_status: str = "in_domain",
) -> dict[str, Any]:
    """Calculate transparent traffic features for one recurring schedule."""
    if not closed_edges:
        raise ValueError("closed_edges cannot be empty")
    if (
        not math.isfinite(forecast_station_coverage)
        or not 0 <= forecast_station_coverage <= 1
    ):
        raise ValueError("forecast_station_coverage must be from zero through one")
    if road_domain_status not in {
        "in_domain", "out_of_domain", "not_evaluated"
    }:
        raise ValueError("road_domain_status is invalid")
    lengths = {len(series) for series in projected_flows.values()}
    if len(lengths) > 1:
        raise ValueError("all projected flow series must have the same length")
    n_intervals = next(iter(lengths), 0)
    if n_intervals <= 0:
        raise ValueError("projected flow series cannot be empty")
    epoch = datetime.fromisoformat(flow_epoch.replace("Z", "+00:00"))
    if epoch.tzinfo is not None:
        raise ValueError("monthly proxy requires timezone-less local flow epoch")
    quarters = schedule_quarter_indices(
        schedule,
        flow_epoch=epoch,
        interval_minutes=interval_minutes,
        n_intervals=n_intervals,
    )

    closed_exposure = 0.0
    closed_quarters_known = 0
    existing_utilizations: list[float] = []
    post_diversion_utilizations: list[float] = []
    available_path_quarters = 0
    total_path_quarters = len(quarters) * len(detour_paths)

    for quarter in quarters:
        closed_values = [
            _finite_nonnegative(projected_flows.get(edge, ())[quarter])
            if quarter < len(projected_flows.get(edge, ()))
            else None
            for edge in closed_edges
        ]
        if not closed_values or any(value is None for value in closed_values):
            continue
        closed_flow = sum(value for value in closed_values if value is not None)
        closed_exposure += closed_flow
        closed_quarters_known += 1

        usable: list[tuple[DetourPath, float]] = []
        for path in detour_paths:
            utilizations: list[float] = []
            for edge in path.edges:
                series = projected_flows.get(edge)
                flow = (
                    _finite_nonnegative(series[quarter])
                    if series is not None and quarter < len(series)
                    else None
                )
                capacity = _finite_nonnegative(
                    capacity_veh_per_interval.get(edge)
                )
                if flow is None or capacity is None or capacity <= 0:
                    utilizations = []
                    break
                utilizations.append(flow / capacity)
            if utilizations:
                available_path_quarters += 1
                usable.append((path, max(utilizations)))
        if not usable:
            continue

        inverse_cost = [1 / path.freeflow_s for path, _ in usable]
        total_weight = sum(inverse_cost)
        for (path, existing), raw_weight in zip(usable, inverse_cost):
            share = raw_weight / total_weight
            diverted = closed_flow * share
            path_after: list[float] = []
            for edge in path.edges:
                flow = float(projected_flows[edge][quarter])  # proven above
                capacity = float(capacity_veh_per_interval[edge])
                path_after.append((flow + diverted) / capacity)
            existing_utilizations.append(existing)
            post_diversion_utilizations.append(max(path_after))

    closed_coverage = (
        closed_quarters_known / len(quarters) if quarters else 0.0
    )
    detour_coverage = (
        available_path_quarters / total_path_quarters
        if total_path_quarters
        else 0.0
    )
    relevant_edges = set(closed_edges)
    relevant_edges.update(edge for path in detour_paths for edge in path.edges)
    structural_coverage = (
        sum(
            structural_sources.get(edge) in _STRUCTURAL_SOURCES
            for edge in relevant_edges
        )
        / len(relevant_edges)
        if relevant_edges
        else 0.0
    )
    confidences = [
        parsed
        for edge in relevant_edges
        if (parsed := _finite_nonnegative(spatial_confidence.get(edge))) is not None
    ]
    minimum_confidence = min(confidences) if confidences else None
    confidence_coverage = (
        len(confidences) / len(relevant_edges) if relevant_edges else 0.0
    )

    scoreable = bool(
        quarters
        and closed_coverage == 1.0
        and detour_paths
        and post_diversion_utilizations
    )
    support, support_reasons = _support_level(
        scoreable=scoreable,
        structural_coverage=structural_coverage,
        detour_coverage=detour_coverage,
        forecast_station_coverage=forecast_station_coverage,
        spatial_confidence_coverage=confidence_coverage,
        minimum_confidence=minimum_confidence,
        road_domain_status=road_domain_status,
    )
    features: dict[str, float | None] = {
        "closed_edge_vehicle_exposure": (
            round(closed_exposure, 6) if closed_coverage == 1.0 else None
        ),
        "detour_existing_utilization_p90": (
            round(_percentile(existing_utilizations, 0.9), 6)
            if existing_utilizations
            else None
        ),
        "detour_reserve_p10": (
            round(
                1 - _percentile(existing_utilizations, 0.9),
                6,
            )
            if existing_utilizations
            else None
        ),
        "detour_post_diversion_utilization_p90": (
            round(_percentile(post_diversion_utilizations, 0.9), 6)
            if post_diversion_utilizations
            else None
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "proxy_version": PROXY_VERSION,
        "schedule_id": schedule.schedule_id,
        "first_work_date": schedule.first_work_date,
        "day_count": schedule.day_count,
        "daily_start": schedule.daily_start,
        "daily_end": schedule.daily_end,
        "features": features,
        "coverage": {
            "closed_edge_quarters": round(closed_coverage, 6),
            "detour_path_quarters": round(detour_coverage, 6),
            "structural_field_edges": round(structural_coverage, 6),
            "forecast_sensor_stations": round(forecast_station_coverage, 6),
            "spatial_confidence_edges": round(confidence_coverage, 6),
        },
        "evidence": {
            "support_level": support,
            "reasons": list(support_reasons),
            "minimum_spatial_confidence": minimum_confidence,
            "road_domain_status": road_domain_status,
        },
        "scoreable": scoreable,
        "screening_only": True,
    }


def rank_schedules(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rank scoreable proxy candidates without manufacturing physical units."""
    scoreable = [
        dict(candidate)
        for candidate in candidates
        if candidate.get("scoreable") is True
    ]
    feature_names = (
        "closed_edge_vehicle_exposure",
        "detour_existing_utilization_p90",
        "detour_post_diversion_utilization_p90",
    )
    if not scoreable:
        return []
    feature_ranks: dict[str, list[float]] = {}
    for name in feature_names:
        values = [candidate.get("features", {}).get(name) for candidate in scoreable]
        if any(_finite_nonnegative(value) is None for value in values):
            raise ValueError(f"scoreable candidate is missing proxy feature {name}")
        feature_ranks[name] = _average_ranks([float(value) for value in values])

    for index, candidate in enumerate(scoreable):
        ranks = {
            name: round(feature_ranks[name][index], 6)
            for name in feature_names
        }
        candidate["feature_ranks"] = ranks
        candidate["combined_rank_score"] = round(
            sum(ranks.values()) / len(ranks), 6
        )
    scoreable.sort(
        key=lambda candidate: (
            candidate["combined_rank_score"],
            candidate["schedule_id"],
        )
    )
    for proxy_rank, candidate in enumerate(scoreable):
        candidate["proxy_rank"] = proxy_rank
    return scoreable


def _date_block(first_date: str, origin: str, width_days: int) -> int:
    day = datetime.strptime(first_date, "%Y-%m-%d").date()
    beginning = datetime.strptime(origin, "%Y-%m-%d").date()
    return (day - beginning).days // width_days


def stratified_shortlist(
    ranked: Sequence[Mapping[str, Any]],
    *,
    all_candidate_count: int,
    unavailable_candidate_count: int,
    unavailable_candidates: Sequence[Mapping[str, Any]] = (),
    policy: ShortlistPolicy = ShortlistPolicy(),
) -> dict[str, Any]:
    """Select a deterministic, evidence-aware SUMO shortlist.

    Adjacent starts on one date cannot consume the entire shortlist: the
    selection includes global leaders, leaders for every feasible day count,
    leaders from every date block, and deterministic rank-quantile controls.
    """
    ranked_copy = [dict(candidate) for candidate in ranked]
    unavailable_copy = [dict(candidate) for candidate in unavailable_candidates]
    if (
        all_candidate_count != len(ranked_copy) + unavailable_candidate_count
        or unavailable_candidate_count < 0
        or len(unavailable_copy) > unavailable_candidate_count
    ):
        raise ValueError("candidate counts are inconsistent")
    if all_candidate_count <= policy.bounded_exhaustive_limit:
        if unavailable_candidate_count and not unavailable_copy:
            raise ValueError(
                "bounded exhaustive fallback requires every unavailable candidate"
            )
        entries = [
            {
                "schedule_id": str(candidate["schedule_id"]),
                "proxy_rank": candidate.get("proxy_rank"),
                "day_count": candidate["day_count"],
                "first_work_date": candidate["first_work_date"],
                "selection_reasons": ["bounded_exhaustive_fallback"],
            }
            for candidate in sorted(
                [*ranked_copy, *unavailable_copy],
                key=lambda candidate: (
                    candidate.get("proxy_rank") is None,
                    candidate.get("proxy_rank", math.inf),
                    str(candidate["schedule_id"]),
                ),
            )
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "proxy_version": PROXY_VERSION,
            "shortlist_version": SHORTLIST_VERSION,
            "selection_mode": "bounded_exhaustive_fallback",
            "entries": entries,
            "scoreable_candidate_count": len(ranked_copy),
            "unavailable_candidate_count": unavailable_candidate_count,
            "unavailable_selected_count": len(unavailable_copy),
            "candidate_coverage_complete": len(entries) == all_candidate_count,
            "shortlist_fraction": 1.0,
            "withhold_recommendation": bool(unavailable_candidate_count),
            "withhold_reasons": (
                ["unscoreable_legal_candidates"]
                if unavailable_candidate_count
                else []
            ),
            "screening_only": True,
        }
    if not ranked_copy:
        selected_unavailable = sorted(
            unavailable_copy,
            key=lambda candidate: str(candidate["schedule_id"]),
        )[:policy.maximum_shortlist]
        entries = [
            {
                "schedule_id": str(candidate["schedule_id"]),
                "proxy_rank": None,
                "day_count": candidate["day_count"],
                "first_work_date": candidate["first_work_date"],
                "selection_reasons": ["unscoreable_sumo_control"],
            }
            for candidate in selected_unavailable
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "proxy_version": PROXY_VERSION,
            "shortlist_version": SHORTLIST_VERSION,
            "selection_mode": "no_scoreable_candidates",
            "entries": entries,
            "scoreable_candidate_count": 0,
            "unavailable_candidate_count": unavailable_candidate_count,
            "unavailable_selected_count": len(selected_unavailable),
            "candidate_coverage_complete": len(entries) == all_candidate_count,
            "shortlist_fraction": round(
                len(entries) / all_candidate_count, 6
            ),
            "withhold_recommendation": True,
            "withhold_reasons": [
                "no_scoreable_candidates",
                *(
                    ["unscoreable_candidates_exceed_shortlist_capacity"]
                    if len(selected_unavailable) < unavailable_candidate_count
                    else []
                ),
            ],
            "screening_only": True,
        }

    support_levels = {
        str(candidate.get("evidence", {}).get("support_level", "unavailable"))
        for candidate in ranked_copy
    }
    expansion = 1.0
    withhold_reasons: list[str] = []
    if "low" in support_levels:
        expansion = max(expansion, policy.low_support_multiplier)
        withhold_reasons.append("low_proxy_support")
    if unavailable_candidate_count:
        expansion = max(expansion, policy.unavailable_multiplier)
        withhold_reasons.append("unscoreable_legal_candidates")

    mode = "stratified_proxy"
    selected_reasons: dict[str, set[str]] = {}
    strata_capacity_exceeded = False

    def choose(candidate: Mapping[str, Any], reason: str) -> None:
        selected_reasons.setdefault(
            str(candidate["schedule_id"]), set()
        ).add(reason)

    overall_n = min(
        len(ranked_copy),
        max(
            policy.best_overall,
            int(math.ceil(policy.best_overall * expansion)),
        ),
    )
    for candidate in ranked_copy[:overall_n]:
        choose(candidate, "best_overall")

    by_day_count: dict[int, list[Mapping[str, Any]]] = {}
    for candidate in ranked_copy:
        by_day_count.setdefault(int(candidate["day_count"]), []).append(candidate)
    for day_count, group in sorted(by_day_count.items()):
        for candidate in group[: policy.best_per_day_count]:
            choose(candidate, f"best_day_count:{day_count}")

    origin = min(str(candidate["first_work_date"]) for candidate in ranked_copy)
    by_block: dict[int, list[Mapping[str, Any]]] = {}
    for candidate in ranked_copy:
        block = _date_block(
            str(candidate["first_work_date"]),
            origin,
            policy.date_block_days,
        )
        by_block.setdefault(block, []).append(candidate)
    for block, group in sorted(by_block.items()):
        for candidate in group[: policy.best_per_date_block]:
            choose(candidate, f"best_date_block:{block}")

    for fraction in policy.validation_quantiles:
        position = round(fraction * (len(ranked_copy) - 1))
        choose(
            ranked_copy[position],
            f"validation_control_q{fraction:.2f}",
        )

    if len(selected_reasons) > policy.maximum_shortlist:
        # Preserve every non-overall stratum/control first, then fill by
        # proxy rank.  This cap can never let adjacent global leaders
        # erase the required diversity.
        protected = {
            schedule_id
            for schedule_id, reasons in selected_reasons.items()
            if any(reason != "best_overall" for reason in reasons)
        }
        if len(protected) > policy.maximum_shortlist:
            strata_capacity_exceeded = True
            keep = set([
                str(candidate["schedule_id"])
                for candidate in ranked_copy
                if str(candidate["schedule_id"]) in protected
            ][:policy.maximum_shortlist])
        else:
            keep = set(protected)
        for candidate in ranked_copy:
            if len(keep) >= policy.maximum_shortlist:
                break
            schedule_id = str(candidate["schedule_id"])
            if schedule_id in selected_reasons:
                keep.add(schedule_id)
        selected_reasons = {
            key: value
            for key, value in selected_reasons.items()
            if key in keep
        }

    entries = []
    for candidate in ranked_copy:
        schedule_id = str(candidate["schedule_id"])
        if schedule_id not in selected_reasons:
            continue
        entries.append({
            "schedule_id": schedule_id,
            "proxy_rank": candidate["proxy_rank"],
            "day_count": candidate["day_count"],
            "first_work_date": candidate["first_work_date"],
            "selection_reasons": sorted(selected_reasons[schedule_id]),
        })

    remaining_capacity = max(0, policy.maximum_shortlist - len(entries))
    selected_unavailable = sorted(
        unavailable_copy,
        key=lambda candidate: str(candidate["schedule_id"]),
    )[:remaining_capacity]
    entries.extend(
        {
            "schedule_id": str(candidate["schedule_id"]),
            "proxy_rank": None,
            "day_count": candidate["day_count"],
            "first_work_date": candidate["first_work_date"],
            "selection_reasons": ["unscoreable_sumo_control"],
        }
        for candidate in selected_unavailable
    )
    if len(selected_unavailable) < unavailable_candidate_count:
        withhold_reasons.append(
            "unscoreable_candidates_exceed_shortlist_capacity"
        )
    if strata_capacity_exceeded:
        withhold_reasons.append("required_strata_exceed_shortlist_capacity")

    return {
        "schema_version": SCHEMA_VERSION,
        "proxy_version": PROXY_VERSION,
        "shortlist_version": SHORTLIST_VERSION,
        "selection_mode": mode,
        "entries": entries,
        "scoreable_candidate_count": len(ranked_copy),
        "unavailable_candidate_count": unavailable_candidate_count,
        "unavailable_selected_count": len(selected_unavailable),
        "candidate_coverage_complete": len(entries) == all_candidate_count,
        "shortlist_fraction": round(len(entries) / all_candidate_count, 6),
        "withhold_recommendation": bool(withhold_reasons),
        "withhold_reasons": withhold_reasons,
        "screening_only": True,
    }
