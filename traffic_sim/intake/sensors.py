"""Validated sensor metadata for the data-intake boundary.

Direction semantics are data, not executable source.  Coordinates remain in
the delivered coordinate file because that file is the authoritative survey;
``load_registry(..., coordinates=...)`` merges them and validates the complete
record before calibration begins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

SEMANTICS = frozenset({"directional", "two_way_total"})
VERIFIED = "verified"
APPROVED_SNAP = "approved"
ACCEPTED_QUALITY = "accepted"
DEFAULT_MAX_SNAP_DISTANCE_M = 60.0

#: The only time semantics a directional reference may declare. The literal
#: exists so a future record cannot quietly claim per-slot measurement by
#: writing a different string: an unknown value is refused.
PERIOD_AGGREGATE = "period_aggregate_not_per_slot"

#: Tolerance when checking that a declared period reproduces its anchor.
#: The published D-factor is quoted to two significant figures, so anything
#: tighter would be asserting precision the source does not carry.
ANCHOR_TOLERANCE = 5e-4


@dataclass(frozen=True)
class DirectionalReference:
    """A published AGGREGATE direction split for a two-way station.

    Sensor 107 is the only station in the registry whose two-way total is
    divided into two Level-1 targets, so it is the only place a direction
    estimate directly negotiates with a measurement. Gothenburg publishes a
    D-factor for it — 3 400/3 100 of 6 500 for 2025, about 52/48, with
    2023-24 near 50/50 — and local evidence outranks a transferred model for
    the same aggregate quantity.

    What this record is NOT, and the reason every field below exists: it is
    not 96 measured quarter values. It constrains a mean over a declared
    period and nothing finer. `time_semantics` must say so literally, the
    period is explicit, the raw counts are kept rather than only their ratio,
    and `bearing_to_edge` records which directed edge each count belongs to —
    resolved from network geometry, because a reversed mapping would invert a
    constraint that everything downstream treats as anchored truth.

    A hardcoded 0.52 in `build_targets` would lose all five of those things,
    which is why the plan forbids it.
    """

    period_kind: str
    period_year: int
    period_start: str
    period_end: str
    aggregation: str
    raw_counts: Mapping[str, float]
    total: float
    bearing_to_edge: Mapping[str, str]
    edge_bearing_deg: Mapping[str, float]
    source: str
    verification: Mapping[str, Any]
    time_semantics: str = PERIOD_AGGREGATE
    source_detail: str = ""
    comparison_periods: tuple[Mapping[str, Any], ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if self.time_semantics != PERIOD_AGGREGATE:
            raise ValueError(
                f"directional_reference time_semantics must be "
                f"{PERIOD_AGGREGATE!r}; a period aggregate must never be "
                f"declared as per-slot measurement (got "
                f"{self.time_semantics!r})"
            )
        if not self.raw_counts:
            raise ValueError("directional_reference needs raw_counts")
        if len(self.raw_counts) != 2:
            raise ValueError(
                "directional_reference must carry exactly two directions, "
                f"got {sorted(self.raw_counts)}"
            )
        for bearing, count in self.raw_counts.items():
            if float(count) < 0:
                raise ValueError(f"raw count for {bearing} is negative")
        summed = float(sum(float(v) for v in self.raw_counts.values()))
        if abs(summed - float(self.total)) > 1e-9:
            raise ValueError(
                f"directional_reference raw counts sum to {summed:g} but "
                f"total says {float(self.total):g}"
            )
        if float(self.total) <= 0:
            raise ValueError("directional_reference total must be positive")
        if set(self.bearing_to_edge) != set(self.raw_counts):
            raise ValueError(
                "bearing_to_edge must name exactly the directions in "
                f"raw_counts: {sorted(self.raw_counts)} vs "
                f"{sorted(self.bearing_to_edge)}"
            )
        edges = list(self.bearing_to_edge.values())
        if len(set(edges)) != len(edges):
            raise ValueError(
                "bearing_to_edge maps two directions onto the same edge"
            )
        for name in ("period_start", "period_end"):
            try:
                date.fromisoformat(str(getattr(self, name)))
            except ValueError as exc:
                raise ValueError(
                    f"directional_reference {name} must be YYYY-MM-DD"
                ) from exc
        if date.fromisoformat(self.period_end) < \
                date.fromisoformat(self.period_start):
            raise ValueError("directional_reference period ends before it starts")
        if self.verification.get("status") != VERIFIED:
            raise ValueError(
                "directional_reference must be verified before it can anchor "
                "a calibration input"
            )

    def share_for_edge(self, edge_id: str) -> float:
        """The anchored aggregate share for one directed edge."""
        for bearing, mapped in self.bearing_to_edge.items():
            if mapped == edge_id:
                return float(self.raw_counts[bearing]) / float(self.total)
        raise KeyError(
            f"{edge_id} is not one of this reference's directed edges: "
            f"{sorted(self.bearing_to_edge.values())}"
        )

    def covers(self, day: str) -> bool:
        """Is this calendar day inside the reference's declared period?"""
        try:
            target = date.fromisoformat(str(day)[:10])
        except ValueError:
            return False
        return (date.fromisoformat(self.period_start) <= target
                <= date.fromisoformat(self.period_end))

    def as_dict(self) -> dict[str, Any]:
        return {
            "time_semantics": self.time_semantics,
            "aggregation": self.aggregation,
            "period": {
                "kind": self.period_kind,
                "year": self.period_year,
                "start": self.period_start,
                "end": self.period_end,
            },
            "raw_counts": dict(self.raw_counts),
            "total": self.total,
            "bearing_to_edge": dict(self.bearing_to_edge),
            "edge_bearing_deg": dict(self.edge_bearing_deg),
            "source": self.source,
            "source_detail": self.source_detail,
            "verification": dict(self.verification),
            "comparison_periods": [dict(p) for p in self.comparison_periods],
            "note": self.note,
        }


def _directional_reference(raw: Any, sensor_id: str, semantics: str
                           ) -> DirectionalReference | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"sensor {sensor_id} directional_reference must be an object")
    if semantics != "two_way_total":
        raise ValueError(
            f"sensor {sensor_id} is {semantics}, so its measured direction is "
            "already a Level-1 target; a directional_reference would have "
            "nothing to anchor"
        )
    period = raw.get("period")
    if not isinstance(period, Mapping):
        raise ValueError(
            f"sensor {sensor_id} directional_reference needs an explicit period"
        )
    return DirectionalReference(
        period_kind=str(period.get("kind", "")),
        period_year=int(period.get("year", 0)),
        period_start=str(period.get("start", "")),
        period_end=str(period.get("end", "")),
        aggregation=str(raw.get("aggregation", "")),
        raw_counts={str(k): float(v)
                    for k, v in (raw.get("raw_counts") or {}).items()},
        total=float(raw.get("total", 0.0)),
        bearing_to_edge={str(k): str(v)
                         for k, v in (raw.get("bearing_to_edge") or {}).items()},
        edge_bearing_deg={str(k): float(v)
                          for k, v in (raw.get("edge_bearing_deg") or {}).items()},
        source=str(raw.get("source", "")),
        verification=dict(raw.get("verification") or {}),
        time_semantics=str(raw.get("time_semantics", "")),
        source_detail=str(raw.get("source_detail", "")),
        comparison_periods=tuple(dict(p) for p in
                                 (raw.get("comparison_periods") or ())),
        note=str(raw.get("note", "")),
    )


@dataclass(frozen=True)
class SensorRecord:
    sensor_id: str
    active_from: str | None
    active_to: str | None
    measurement_semantics: str
    measured_bearing: str | None
    permitted_bearings: tuple[str, ...]
    coordinates: tuple[float, float] | None
    coordinate_reference_system: str
    source: str
    source_file: str
    snap_status: str
    approved_edge_ids: tuple[str, ...]
    snap_distance_m: float | None
    catalogue_verification: Mapping[str, Any]
    quality_status: str
    notes: str
    manual_snap: str | None
    directional_reference: DirectionalReference | None = None

    @property
    def level(self) -> str:
        if self.measurement_semantics == "two_way_total":
            return "Total"
        if not self.measured_bearing:
            raise ValueError(f"sensor {self.sensor_id} has no measured bearing")
        return self.measured_bearing


class SensorRegistry:
    def __init__(self, records: Mapping[str, SensorRecord], path: Path):
        self.records = dict(records)
        self.path = Path(path)

    def __contains__(self, sensor_id: str) -> bool:
        return str(sensor_id) in self.records

    def __getitem__(self, sensor_id: str) -> SensorRecord:
        return self.records[str(sensor_id)]

    def levels_for(self, sensor_ids: list[str] | set[str]) -> dict[str, str]:
        return {str(sensor_id): self[str(sensor_id)].level for sensor_id in sensor_ids}

    def manual_snaps(self) -> dict[str, str]:
        return {sid: record.manual_snap for sid, record in self.records.items()
                if record.manual_snap}

    def validate_data_sensors(
            self, sensor_ids: list[str] | set[str], *,
            require_coordinates: bool = True,
            study_start: str | None = None,
            study_end: str | None = None,
            max_snap_distance_m: float = DEFAULT_MAX_SNAP_DISTANCE_M,
    ) -> None:
        """Fail closed before station data can constrain calibration.

        Coordinates and catalogue semantics are necessary but not sufficient:
        a station must be active for the requested period and its directed
        network snap must have been reviewed.  The resolved edge IDs are
        checked separately after the current network has been snapped.
        """
        unknown = sorted({str(sid) for sid in sensor_ids} - self.records.keys())
        if unknown:
            raise ValueError(
                "sensor data contains IDs absent from the validated registry: "
                + ", ".join(unknown))
        unverified = sorted(
            str(sid) for sid in sensor_ids
            if self[str(sid)].catalogue_verification.get("status") != VERIFIED
        )
        if unverified:
            raise ValueError(
                "sensor catalogue verification is required before calibration: "
                + ", ".join(unverified))
        invalid_quality = sorted(
            str(sid) for sid in sensor_ids
            if self[str(sid)].quality_status != ACCEPTED_QUALITY
        )
        if invalid_quality:
            raise ValueError(
                "sensor quality must be accepted before calibration: "
                + ", ".join(invalid_quality))
        pending_snap = sorted(
            str(sid) for sid in sensor_ids
            if self[str(sid)].snap_status != APPROVED_SNAP
        )
        if pending_snap:
            raise ValueError(
                "sensor network snaps must be approved before calibration: "
                + ", ".join(pending_snap))
        invalid_snap = sorted(
            str(sid) for sid in sensor_ids
            if not self[str(sid)].approved_edge_ids
            or self[str(sid)].snap_distance_m is None
            or not math.isfinite(self[str(sid)].snap_distance_m)
            or self[str(sid)].snap_distance_m < 0
            or self[str(sid)].snap_distance_m > max_snap_distance_m
        )
        if invalid_snap:
            raise ValueError(
                f"sensor snaps must have approved edges and distance <= "
                f"{max_snap_distance_m:g} m: " + ", ".join(invalid_snap))
        if require_coordinates:
            missing = sorted(str(sid) for sid in sensor_ids
                             if self[str(sid)].coordinates is None)
            if missing:
                raise ValueError(
                    "sensor coordinates are missing from the delivered coordinate "
                    "file: " + ", ".join(missing))
        if study_start is not None or study_end is not None:
            try:
                start = date.fromisoformat(study_start) if study_start else None
                end = date.fromisoformat(study_end) if study_end else start
            except (TypeError, ValueError) as exc:
                raise ValueError("study dates must be YYYY-MM-DD") from exc
            if start and end and end < start:
                raise ValueError("study_end must not precede study_start")
            outside = []
            for sid in sensor_ids:
                record = self[str(sid)]
                active_from = (date.fromisoformat(record.active_from)
                               if record.active_from else None)
                active_to = (date.fromisoformat(record.active_to)
                             if record.active_to else None)
                if start and active_from and start < active_from:
                    outside.append(str(sid))
                elif end and active_to and end > active_to:
                    outside.append(str(sid))
            if outside:
                raise ValueError(
                    "sensor is inactive during the requested study period: "
                    + ", ".join(sorted(outside)))

    def validate_resolved_edges(
            self, resolved_edges: Mapping[str, Iterable[str]],
            *, max_snap_distance_m: float = DEFAULT_MAX_SNAP_DISTANCE_M,
            resolved_distances_m: Mapping[str, Iterable[float]] | None = None,
            sensor_ids: Iterable[str] | None = None,
    ) -> None:
        """Ensure the current network resolves exactly to reviewed edges."""
        errors = []
        ids = (str(sid) for sid in sensor_ids) if sensor_ids is not None else self.records
        for sid in ids:
            if sid not in self.records:
                errors.append(f"{sid}: not present in registry")
                continue
            record = self.records[sid]
            expected = set(record.approved_edge_ids)
            actual = {str(edge) for edge in resolved_edges.get(sid, ())}
            if actual != expected:
                errors.append(
                    f"{sid}: resolved {sorted(actual)} != approved {sorted(expected)}")
            if resolved_distances_m is not None:
                distances = [float(value) for value in
                             resolved_distances_m.get(sid, ())]
                if (not distances or any(not math.isfinite(value)
                                         or value > max_snap_distance_m
                                         for value in distances)):
                    errors.append(
                        f"{sid}: current snap exceeds {max_snap_distance_m:g} m")
                elif record.snap_distance_m is not None:
                    allowed_delta = max(5.0, 0.25 * record.snap_distance_m)
                    if any(abs(value - record.snap_distance_m) > allowed_delta
                           for value in distances):
                        errors.append(
                            f"{sid}: current snap distance drift exceeds "
                            f"{allowed_delta:g} m")
        if errors:
            raise ValueError("sensor network snap review failed: " + "; ".join(errors))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "sensors": [
                {
                    "sensor_id": record.sensor_id,
                    "active_from": record.active_from,
                    "active_to": record.active_to,
                    "measurement_semantics": record.measurement_semantics,
                    "measured_bearing": record.measured_bearing,
                    "permitted_bearings": list(record.permitted_bearings),
                    "coordinates": list(record.coordinates)
                    if record.coordinates is not None else None,
                    "coordinate_reference_system": record.coordinate_reference_system,
                    "source": record.source,
                    "source_file": record.source_file,
                    "snap_status": record.snap_status,
                    "approved_edge_ids": list(record.approved_edge_ids),
                    "snap_distance_m": record.snap_distance_m,
                    "catalogue_verification": dict(record.catalogue_verification),
                    "quality_status": record.quality_status,
                    "notes": record.notes,
                    "manual_snap": record.manual_snap,
                    **({"directional_reference":
                        record.directional_reference.as_dict()}
                       if record.directional_reference is not None else {}),
                }
                for record in self.records.values()
            ],
        }

    def directional_reference_for(self, sensor_id: str
                                  ) -> DirectionalReference | None:
        record = self.records.get(str(sensor_id))
        return record.directional_reference if record else None


def _coordinates(value: Any, sensor_id: str) -> tuple[float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"sensor {sensor_id} coordinates must be [lat, lon]")
    try:
        lat, lon = float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"sensor {sensor_id} coordinates are not numeric") from exc
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f"sensor {sensor_id} coordinates are out of range")
    return lat, lon


def _record(raw: Mapping[str, Any], coordinates: Mapping[str, tuple[float, float]] | None
            ) -> SensorRecord:
    sensor_id = str(raw.get("sensor_id", "")).strip()
    if not sensor_id:
        raise ValueError("sensor registry contains an empty sensor_id")
    semantics = str(raw.get("measurement_semantics", ""))
    if semantics not in SEMANTICS:
        raise ValueError(f"sensor {sensor_id} has unsupported measurement_semantics")
    bearing = raw.get("measured_bearing")
    bearing = str(bearing) if bearing is not None else None
    if semantics == "directional" and not bearing:
        raise ValueError(f"directional sensor {sensor_id} needs measured_bearing")
    if semantics == "two_way_total" and bearing:
        raise ValueError(f"two_way_total sensor {sensor_id} cannot have measured_bearing")
    verification = raw.get("catalogue_verification")
    if not isinstance(verification, Mapping):
        raise ValueError(f"sensor {sensor_id} needs catalogue_verification metadata")
    coord = _coordinates(raw.get("coordinates"), sensor_id)
    if coord is None and coordinates is not None:
        coord = coordinates.get(sensor_id)
    approved = raw.get("approved_edge_ids", ())
    if isinstance(approved, str):
        approved = (approved,)
    else:
        approved = tuple(str(value) for value in approved)
    permitted = raw.get("permitted_bearings", ())
    if isinstance(permitted, str):
        permitted = (permitted,)
    else:
        permitted = tuple(str(value) for value in permitted)
    snap_distance = raw.get("snap_distance_m")
    return SensorRecord(
        sensor_id=sensor_id,
        active_from=(str(raw["active_from"]) if raw.get("active_from") is not None else None),
        active_to=(str(raw["active_to"]) if raw.get("active_to") is not None else None),
        measurement_semantics=semantics,
        measured_bearing=bearing,
        permitted_bearings=permitted,
        coordinates=coord,
        coordinate_reference_system=str(raw.get("coordinate_reference_system", "")),
        source=str(raw.get("source", "")),
        source_file=str(raw.get("source_file", "")),
        snap_status=str(raw.get("snap_status", "pending")),
        approved_edge_ids=approved,
        snap_distance_m=(float(snap_distance) if snap_distance is not None else None),
        catalogue_verification=dict(verification),
        quality_status=str(raw.get("quality_status", "unknown")),
        notes=str(raw.get("notes", "")),
        manual_snap=(str(raw["manual_snap"]) if raw.get("manual_snap") else None),
        directional_reference=_directional_reference(
            raw.get("directional_reference"), sensor_id, semantics),
    )


def load_registry(path: Path, *, coordinates: Mapping[str, tuple[float, float]] | None = None,
                  require_coordinates: bool = False) -> SensorRegistry:
    path = Path(path)
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported sensor registry schema_version")
    raw_records = payload.get("sensors")
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("sensor registry must contain a non-empty sensors list")
    records: dict[str, SensorRecord] = {}
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            raise ValueError("sensor registry entries must be objects")
        record = _record(raw, coordinates)
        if record.sensor_id in records:
            raise ValueError(f"duplicate sensor_id in registry: {record.sensor_id}")
        records[record.sensor_id] = record
    registry = SensorRegistry(records, path)
    if require_coordinates:
        registry.validate_data_sensors(list(records), require_coordinates=True)
    return registry


# ──────────────────────────────────────────────────────────────────────────
# anchoring an estimated per-slot split to a published period aggregate
# ──────────────────────────────────────────────────────────────────────────
def _logit(value: float) -> float:
    clamped = min(max(float(value), 1e-9), 1 - 1e-9)
    return math.log(clamped / (1 - clamped))


def _expit(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def anchor_period_mean(
    shares: Iterable[float],
    anchor: float,
    *,
    weights: Iterable[float] | None = None,
    tolerance: float = ANCHOR_TOLERANCE,
    max_iterations: int = 200,
) -> list[float]:
    """Shift an estimated per-slot share series so its period mean equals
    ``anchor``, preserving the shape of the time variation.

    The published D-factor constrains an aggregate, so the correct operation
    is a single offset applied to the whole series — not a replacement of
    every slot by the anchor, which would invent 96 measurements the city
    never published, and not a linear rescale, which can push a share out of
    (0, 1).

    The offset is additive on the LOGIT scale and solved by bisection, which
    gives three properties this needs:

    * every output stays strictly inside (0, 1), so the partner direction
      derived as ``1 - s`` is always valid;
    * the ordering and relative shape of the slots is preserved exactly (the
      transform is strictly monotone), so whatever time variation the model
      supports survives;
    * the declared period reproduces the anchor to ``tolerance``.

    ``weights`` should be the per-slot traffic volumes when they are known,
    because an annual average daily D-factor is a volume-weighted quantity
    and an unweighted slot mean is a different number. Omitting them falls
    back to uniform weights, which is an approximation the caller is
    responsible for declaring.
    """
    series = [float(value) for value in shares]
    if not series:
        raise ValueError("cannot anchor an empty share series")
    if not 0.0 < float(anchor) < 1.0:
        raise ValueError(f"anchor must lie strictly in (0, 1), got {anchor!r}")

    if weights is None:
        weight_list = [1.0] * len(series)
    else:
        weight_list = [float(value) for value in weights]
        if len(weight_list) != len(series):
            raise ValueError(
                f"weights length {len(weight_list)} does not match shares "
                f"length {len(series)}")
        if any(value < 0 for value in weight_list):
            raise ValueError("weights must be non-negative")
    total_weight = sum(weight_list)
    if total_weight <= 0:
        raise ValueError("weights must carry positive mass")

    def weighted_mean(offset: float) -> float:
        return sum(w * _expit(_logit(s) + offset)
                   for s, w in zip(series, weight_list)) / total_weight

    # The mean is strictly increasing in the offset and spans (0, 1), so a
    # bracket always exists; widen until it does rather than assuming one.
    low, high = -40.0, 40.0
    if weighted_mean(low) > anchor or weighted_mean(high) < anchor:
        raise ValueError(
            "the requested anchor is unreachable from this share series")
    for _ in range(max_iterations):
        mid = (low + high) / 2
        value = weighted_mean(mid)
        if abs(value - anchor) <= tolerance:
            break
        if value < anchor:
            low = mid
        else:
            high = mid
    else:
        mid = (low + high) / 2

    return [_expit(_logit(s) + mid) for s in series]


def anchored_pair_shares(
    reference: DirectionalReference,
    estimated: Mapping[str, Iterable[float]],
    *,
    weights: Iterable[float] | None = None,
    tolerance: float = ANCHOR_TOLERANCE,
) -> dict[str, list[float]]:
    """Anchor one directed pair's estimated shares to a published reference.

    The lead edge is anchored and the partner is DERIVED as ``1 - s``, never
    anchored independently. That is what keeps the pair summing to exactly
    1.0 in every slot, so the measured two-way total is preserved when
    `build_targets` multiplies each share by it. Anchoring both members
    separately is the construction that loses or invents volume.
    """
    # The lead is chosen by sorted BEARING NAME, not by edge-id string order.
    # Numerically either choice gives the same result, because the anchor is
    # complementary: mean(1 - s) == 1 - mean(s). But the choice must be a
    # stated property of the reference rather than an accident of how the OSM
    # node ids happen to sort, or a re-snap that renames an edge could
    # silently swap which direction is anchored and which is derived.
    ordered_bearings = sorted(reference.bearing_to_edge)
    lead = reference.bearing_to_edge[ordered_bearings[0]]
    partner = reference.bearing_to_edge[ordered_bearings[1]]

    missing = [edge for edge in (lead, partner) if edge not in estimated]
    if missing:
        raise ValueError(
            f"estimated shares are missing the reference's edges: {missing}")
    anchored = anchor_period_mean(
        estimated[lead], reference.share_for_edge(lead),
        weights=weights, tolerance=tolerance,
    )
    return {lead: anchored, partner: [1.0 - value for value in anchored]}
