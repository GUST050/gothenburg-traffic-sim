"""Validated sensor metadata for the data-intake boundary.

Direction semantics are data, not executable source.  Coordinates remain in
the delivered coordinate file because that file is the authoritative survey;
``load_registry(..., coordinates=...)`` merges them and validates the complete
record before calibration begins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SEMANTICS = frozenset({"directional", "two_way_total"})
VERIFIED = "verified"


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

    def validate_data_sensors(self, sensor_ids: list[str] | set[str],
                              *, require_coordinates: bool = True) -> None:
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
        if require_coordinates:
            missing = sorted(str(sid) for sid in sensor_ids
                             if self[str(sid)].coordinates is None)
            if missing:
                raise ValueError(
                    "sensor coordinates are missing from the delivered coordinate "
                    "file: " + ", ".join(missing))

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
                }
                for record in self.records.values()
            ],
        }


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
