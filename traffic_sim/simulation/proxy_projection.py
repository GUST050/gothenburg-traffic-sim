"""Project sparse sensor forecasts onto the structural 96-slot traffic field.

Agent 1 forecasts only the measured sensor edges.  The monthly screening
proxy must therefore not claim direct edge forecasts citywide.  This module
uses each forecast quarter's median station-level change from the fixed
structural reference date to scale one explicitly-labelled structural source:

``forecast_sensor`` > ``learned_direction_prior`` > ``corridor_prior`` >
``assignment_prior``.

The result remains a prior-driven projection away from sensors.  Missing
sensor quarters, structural values, or source edges remain ``None``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
_SEMANTICS = frozenset({"directional", "two_way_total"})
_SOURCES = (
    "forecast_sensor",
    "learned_direction_prior",
    "corridor_prior",
    "assignment_prior",
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _epoch(payload: Mapping[str, Any], label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(payload["epoch"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label}.epoch must be an ISO datetime") from exc
    if parsed.tzinfo is not None:
        raise ValueError(f"{label}.epoch must be timezone-less local time")
    return parsed


def _interval(payload: Mapping[str, Any], label: str) -> int:
    value = payload.get("interval_minutes")
    if isinstance(value, bool) or not isinstance(value, int) or value != 15:
        raise ValueError(f"{label}.interval_minutes must be 15")
    return value


def _index(epoch: datetime, at: datetime, interval_minutes: int) -> int:
    seconds = (at - epoch).total_seconds()
    width = interval_minutes * 60
    if seconds % width:
        raise ValueError("projection dates must align to 15-minute intervals")
    return int(seconds // width)


def _series_value(
    flows: Mapping[str, Sequence[Any]],
    edge: str,
    index: int,
) -> float | None:
    series = flows.get(edge)
    if series is None or index < 0 or index >= len(series):
        return None
    return _number(series[index])


def _share(
    shares: Mapping[str, Sequence[Any]],
    edge: str,
    slot: int,
    fallback: float,
) -> float | None:
    series = shares.get(edge)
    if series is None:
        return fallback
    if len(series) != 96:
        raise ValueError(f"direction share for {edge} must contain 96 values")
    value = _number(series[slot])
    if value is None or value > 1:
        return None
    return value


def _directional_station_values(
    flows: Mapping[str, Sequence[Any]],
    *,
    index: int,
    slot: int,
    sensor_edges: Mapping[str, Sequence[str]],
    sensor_semantics: Mapping[str, str],
    direction_shares: Mapping[str, Sequence[Any]],
) -> tuple[dict[str, float], dict[str, float]]:
    edge_values: dict[str, float] = {}
    station_values: dict[str, float] = {}
    for sensor_id, raw_edges in sorted(sensor_edges.items()):
        edges = tuple(raw_edges)
        semantics = sensor_semantics.get(sensor_id)
        if semantics not in _SEMANTICS:
            raise ValueError(
                f"sensor {sensor_id} has unsupported or missing measurement semantics"
            )
        values = {
            edge: _series_value(flows, edge, index)
            for edge in edges
        }
        if semantics == "directional":
            known = [value for value in values.values() if value is not None]
            if not known:
                continue
            for edge, value in values.items():
                if value is not None:
                    edge_values[edge] = value
            station_values[sensor_id] = sum(known)
            continue

        # A two-way total is intentionally duplicated on its approved directed
        # edges in flows.json.  Split one source total; never sum duplicates.
        totals = [value for value in values.values() if value is not None]
        if not totals:
            continue
        source_total = totals[0]
        if any(not math.isclose(value, source_total, abs_tol=1e-9)
               for value in totals[1:]):
            raise ValueError(
                f"two-way sensor {sensor_id} has inconsistent duplicated totals"
            )
        fallback = 1 / len(edges)
        resolved_shares = {
            edge: _share(direction_shares, edge, slot, fallback)
            for edge in edges
        }
        if any(value is None for value in resolved_shares.values()):
            continue
        share_total = sum(float(value) for value in resolved_shares.values())
        if share_total <= 0:
            continue
        for edge, value in resolved_shares.items():
            edge_values[edge] = source_total * float(value) / share_total
        station_values[sensor_id] = source_total
    return edge_values, station_values


def _structural_value(
    edge: str,
    slot: int,
    *,
    assignment_flows: Mapping[str, Sequence[Any]],
    learned_priors: Mapping[str, Mapping[str, Any]],
    corridor_priors: Mapping[str, Mapping[str, Any]],
    corridor_reference_index: int,
) -> tuple[float | None, str]:
    if edge in learned_priors:
        values = learned_priors[edge].get("prior", ())
        value = _number(values[slot]) if slot < len(values) else None
        return value, "learned_direction_prior"
    if edge in corridor_priors:
        values = corridor_priors[edge].get("prior", ())
        index = corridor_reference_index + slot
        value = _number(values[index]) if index < len(values) else None
        return value, "corridor_prior"
    if edge in assignment_flows:
        values = assignment_flows[edge]
        value = _number(values[slot]) if slot < len(values) else None
        return value, "assignment_prior"
    return None, "missing"


@dataclass(frozen=True)
class ProjectionResult:
    flow_epoch: str
    interval_minutes: int
    flows: Mapping[str, tuple[float | None, ...]]
    structural_sources: Mapping[str, str]
    scale_factors: tuple[float | None, ...]
    scale_station_counts: tuple[int, ...]
    total_sensor_stations: int
    minimum_scale_stations: int

    def __post_init__(self) -> None:
        lengths = {len(values) for values in self.flows.values()}
        lengths.add(len(self.scale_factors))
        lengths.add(len(self.scale_station_counts))
        if len(lengths) != 1:
            raise ValueError("projection arrays must have equal lengths")

    @property
    def n_intervals(self) -> int:
        return len(self.scale_factors)

    @property
    def minimum_forecast_station_coverage(self) -> float:
        if self.total_sensor_stations <= 0 or not self.scale_station_counts:
            return 0.0
        return min(self.scale_station_counts) / self.total_sensor_stations

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "monthly_proxy_projection",
            "flow_epoch": self.flow_epoch,
            "interval_minutes": self.interval_minutes,
            "n_intervals": self.n_intervals,
            "flows": {
                edge: list(values) for edge, values in sorted(self.flows.items())
            },
            "structural_sources": dict(sorted(self.structural_sources.items())),
            "scale_factors": list(self.scale_factors),
            "scale_station_counts": list(self.scale_station_counts),
            "total_sensor_stations": self.total_sensor_stations,
            "minimum_scale_stations": self.minimum_scale_stations,
            "minimum_forecast_station_coverage": round(
                self.minimum_forecast_station_coverage, 6
            ),
            "claim": (
                "direct forecast only on measured sensor edges; other edges are "
                "structural priors scaled by station-level forecast change"
            ),
        }


def project_forecast_flows(
    edge_ids: Sequence[str],
    *,
    projection_start: str,
    projection_end_exclusive: str,
    forecast_payload: Mapping[str, Any],
    reference_payload: Mapping[str, Any],
    sensor_edges: Mapping[str, Sequence[str]],
    sensor_semantics: Mapping[str, str],
    direction_shares: Mapping[str, Sequence[Any]],
    assignment_flows: Mapping[str, Sequence[Any]],
    learned_priors: Mapping[str, Mapping[str, Any]],
    corridor_payload: Mapping[str, Any],
    structural_reference_date: str,
    minimum_scale_stations: int = 3,
) -> ProjectionResult:
    """Project requested edges over ``[start, end)`` without inventing data."""
    if len(set(edge_ids)) != len(tuple(edge_ids)):
        raise ValueError("projection edge_ids must be unique")
    if (
        isinstance(minimum_scale_stations, bool)
        or not isinstance(minimum_scale_stations, int)
        or minimum_scale_stations <= 0
    ):
        raise ValueError("minimum_scale_stations must be positive")
    if minimum_scale_stations > len(sensor_edges):
        raise ValueError("minimum_scale_stations exceeds available sensors")

    forecast_epoch = _epoch(forecast_payload, "forecast")
    reference_epoch = _epoch(reference_payload, "reference")
    interval_minutes = _interval(forecast_payload, "forecast")
    if _interval(reference_payload, "reference") != interval_minutes:
        raise ValueError("forecast and reference intervals differ")
    corridor_epoch = _epoch(corridor_payload, "corridor")
    if _interval(corridor_payload, "corridor") != interval_minutes:
        raise ValueError("corridor and forecast intervals differ")

    try:
        start = datetime.fromisoformat(projection_start)
        end = datetime.fromisoformat(projection_end_exclusive)
        reference_start = datetime.fromisoformat(structural_reference_date)
    except ValueError as exc:
        raise ValueError("projection and reference dates must be ISO local time") from exc
    if start.tzinfo is not None or end.tzinfo is not None:
        raise ValueError("projection dates must be timezone-less local time")
    if start >= end:
        raise ValueError("projection_start must be before projection_end_exclusive")
    width = timedelta(minutes=interval_minutes)
    seconds = (end - start).total_seconds()
    if seconds % width.total_seconds():
        raise ValueError("projection range must align to 15 minutes")
    n_intervals = int(seconds // width.total_seconds())

    forecast_flows = forecast_payload.get("flows")
    reference_flows = reference_payload.get("flows")
    corridor_priors = corridor_payload.get("corridor_priors", {})
    if not isinstance(forecast_flows, Mapping) or not isinstance(
        reference_flows, Mapping
    ):
        raise ValueError("forecast and reference flows must be objects")
    if not isinstance(corridor_priors, Mapping):
        raise ValueError("corridor_priors must be an object")

    reference_index = _index(
        reference_epoch, reference_start, interval_minutes
    )
    corridor_reference_index = _index(
        corridor_epoch, reference_start, interval_minutes
    )
    scale_factors: list[float | None] = []
    scale_counts: list[int] = []
    direct_by_quarter: list[dict[str, float]] = []

    for offset in range(n_intervals):
        at = start + offset * width
        forecast_index = _index(forecast_epoch, at, interval_minutes)
        slot = (at.hour * 60 + at.minute) // interval_minutes
        direct, forecast_stations = _directional_station_values(
            forecast_flows,
            index=forecast_index,
            slot=slot,
            sensor_edges=sensor_edges,
            sensor_semantics=sensor_semantics,
            direction_shares=direction_shares,
        )
        _, reference_stations = _directional_station_values(
            reference_flows,
            index=reference_index + slot,
            slot=slot,
            sensor_edges=sensor_edges,
            sensor_semantics=sensor_semantics,
            direction_shares=direction_shares,
        )
        ratios = [
            forecast_stations[sensor_id] / reference
            for sensor_id, reference in reference_stations.items()
            if (
                reference > 0
                and sensor_id in forecast_stations
                and math.isfinite(forecast_stations[sensor_id] / reference)
            )
        ]
        scale_counts.append(len(ratios))
        scale_factors.append(
            median(ratios) if len(ratios) >= minimum_scale_stations else None
        )
        direct_by_quarter.append(direct)

    sources: dict[str, str] = {}
    projected: dict[str, tuple[float | None, ...]] = {}
    sensor_edge_set = {
        edge for edges in sensor_edges.values() for edge in edges
    }
    for edge in edge_ids:
        if edge in sensor_edge_set:
            sources[edge] = "forecast_sensor"
            projected[edge] = tuple(
                direct.get(edge) for direct in direct_by_quarter
            )
            continue
        values: list[float | None] = []
        source = "missing"
        for offset, scale in enumerate(scale_factors):
            at = start + offset * width
            slot = (at.hour * 60 + at.minute) // interval_minutes
            structural, current_source = _structural_value(
                edge,
                slot,
                assignment_flows=assignment_flows,
                learned_priors=learned_priors,
                corridor_priors=corridor_priors,
                corridor_reference_index=corridor_reference_index,
            )
            if source == "missing":
                source = current_source
            elif current_source != source:
                raise ValueError(
                    f"structural source for {edge} changes across slots"
                )
            values.append(
                structural * scale
                if structural is not None and scale is not None
                else None
            )
        sources[edge] = source
        projected[edge] = tuple(values)

    return ProjectionResult(
        flow_epoch=start.isoformat(timespec="seconds"),
        interval_minutes=interval_minutes,
        flows=projected,
        structural_sources=sources,
        scale_factors=tuple(scale_factors),
        scale_station_counts=tuple(scale_counts),
        total_sensor_stations=len(sensor_edges),
        minimum_scale_stations=minimum_scale_stations,
    )
