"""Import one reviewed 2025 sensor delivery without rebuilding published data.

Usage: python3 tools/add_sensor.py --measurements new.csv \
    --coordinates positions.csv --record reviewed_sensor.json

The record must contain the full reviewed entry used by data_in/sensors.json.
The command validates inputs, adds the two CSVs and updates that registry; it
never runs ``make refresh`` or treats a delivered Level as direction evidence.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import date, datetime
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from traffic_sim.intake.sensors import load_registry  # noqa: E402


def _column_names(fieldnames: list[str] | None) -> dict[str, str]:
    if not fieldnames:
        raise ValueError("CSV-filen saknar rubriker")
    columns = {}
    for name in fieldnames:
        key = name.strip().lower().replace(" ", "").replace("_", "")
        if "matplats" in key or "mätplats" in key:
            columns["id"] = name
        elif key == "level":
            columns["level"] = name
        elif "datum" in key or key == "date":
            columns["date"] = name
        elif "kvart" in key or "tid" in key or "time" in key:
            columns["time"] = name
        elif "antal" in key or "count" in key or "flöde" in key or "flow" in key:
            columns["count"] = name
        elif "latx" in key:
            columns["northing"] = name
        elif "longy" in key:
            columns["easting"] = name
    return columns


def _csv_rows(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = _column_names(reader.fieldnames)
        rows = list(reader)
    if not rows or any(None in row for row in rows):
        raise ValueError(f"{path.name}: tom eller felaktig CSV")
    return columns, rows


def _measurements(path: Path) -> tuple[str, int, date, date]:
    columns, rows = _csv_rows(path)
    missing = {"id", "level", "date", "time", "count"} - columns.keys()
    if missing:
        raise ValueError(f"{path.name}: saknade mätkolumner: {sorted(missing)}")
    ids = {str(row[columns["id"]]).strip() for row in rows}
    if len(ids) != 1 or not next(iter(ids)):
        raise ValueError("Mätfilen måste innehålla exakt en sensor")
    sensor_id = next(iter(ids))
    seen = set()
    dates = []
    measured_rows = 0
    for row in rows:
        if not str(row[columns["level"]]).strip():
            raise ValueError("Level saknas i mätfilen")
        try:
            day = date.fromisoformat(row[columns["date"]].strip()[:10])
            time = datetime.strptime(
                row[columns["time"]].strip(),
                "%H:%M:%S" if row[columns["time"]].strip().count(":") == 2
                else "%H:%M",
            ).time()
        except (ValueError, AttributeError) as exc:
            raise ValueError("Ogiltigt Datum eller Kvart/Tid i mätfilen") from exc
        if day.year != 2025 or time.minute % 15 or time.second:
            raise ValueError("Mätningarna måste ligga i 2025 års 15-minutersintervall")
        key = (day, time)
        if key in seen:
            raise ValueError(f"Dubbelt mätintervall för sensor {sensor_id}: {key}")
        seen.add(key)
        dates.append(day)
        count = str(row[columns["count"]]).strip()
        if count:
            try:
                parsed = int(count)
            except ValueError as exc:
                raise ValueError(f"Ogiltigt Antal passager: {count}") from exc
            if parsed < 0:
                raise ValueError("Antal passager får inte vara negativt")
            measured_rows += 1
    if not measured_rows:
        raise ValueError("Mätfilen saknar giltiga passageräkningar")
    return sensor_id, len(rows), min(dates), max(dates)


def _coordinate(path: Path, sensor_id: str) -> tuple[str, str]:
    columns, rows = _csv_rows(path)
    missing = {"id", "northing", "easting"} - columns.keys()
    if missing:
        raise ValueError(f"{path.name}: saknade koordinatkolumner: {sorted(missing)}")
    matches = [row for row in rows if str(row[columns["id"]]).strip() == sensor_id]
    if len(matches) != 1:
        raise ValueError(f"Koordinatfilen måste ha exakt en rad för sensor {sensor_id}")
    row = matches[0]
    northing = str(row[columns["northing"]]).strip()
    easting = str(row[columns["easting"]]).strip()
    try:
        if not all(math.isfinite(float(value)) for value in (northing, easting)):
            raise ValueError
    except ValueError as exc:
        raise ValueError("Koordinaterna måste vara ändliga tal i EPSG:3007") from exc
    return northing, easting


@contextmanager
def _import_lock(data_dir: Path):
    """Serialize independent imports across threads and processes."""
    with (data_dir / ".sensor-import.lock").open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _register_sensor(sensor_id: str, row_count: int, first_day: date,
                     last_day: date, northing: str, easting: str,
                     record: dict, measurements: Path, data_dir: Path,
                     *, dry_run: bool) -> str:
    """Read the current registry after acquiring the lock, then publish once."""
    registry_path = data_dir / "sensors.json"
    registry = load_registry(registry_path)
    if sensor_id in registry:
        raise ValueError(f"Sensor {sensor_id} finns redan i registret")
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    payload["sensors"].append(record)
    target_counts = data_dir / f"sensor_{sensor_id}.csv"
    target_coords = data_dir / f"sensor_{sensor_id}_koordinater.csv"
    if any(char not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_" for char in sensor_id):
        raise ValueError("sensor_id innehåller otillåtna tecken för ett filnamn")
    if target_counts.exists() or target_coords.exists():
        raise FileExistsError(f"Importfiler för sensor {sensor_id} finns redan")

    with tempfile.TemporaryDirectory(dir=data_dir) as temp_name:
        candidate = Path(temp_name) / "sensors.json"
        candidate.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        new_registry = load_registry(candidate)
        new_registry.validate_data_sensors(
            [sensor_id], require_coordinates=False,
            study_start=first_day.isoformat(), study_end=last_day.isoformat())
        existing_edges = {
            edge for existing in registry.records.values()
            for edge in existing.approved_edge_ids
        }
        reused = sorted(existing_edges.intersection(
            new_registry[sensor_id].approved_edge_ids))
        if reused:
            raise ValueError(
                f"Sensor {sensor_id} återanvänder redan registrerade vägkanter: "
                + ", ".join(reused))
        if dry_run:
            return f"Sensor {sensor_id}: {row_count} rader validerade; ingen fil ändrad"
        counts_created = False
        coords_created = False
        try:
            with target_counts.open("xb") as out, measurements.open("rb") as source:
                counts_created = True
                shutil.copyfileobj(source, out)
            with target_coords.open("x", encoding="utf-8", newline="") as out:
                coords_created = True
                writer = csv.writer(out)
                writer.writerow(["Mätplats", "LatX", "LongY"])
                writer.writerow([sensor_id, northing, easting])
            os.replace(candidate, registry_path)
        except Exception:
            if counts_created:
                target_counts.unlink(missing_ok=True)
            if coords_created:
                target_coords.unlink(missing_ok=True)
            raise
    return f"Sensor {sensor_id} tillagd ({row_count} mätintervall). Kör make refresh när du vill bygga om."


def add_sensor(measurements: Path, coordinates: Path, record_path: Path,
               *, data_dir: Path = ROOT / "data_in", dry_run: bool = False) -> str:
    """Validate and stage one sensor; leave simulation/release artifacts untouched."""
    sensor_id, row_count, first_day, last_day = _measurements(measurements)
    northing, easting = _coordinate(coordinates, sensor_id)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or str(record.get("sensor_id", "")) != sensor_id:
        raise ValueError("Metadatafilens sensor_id måste matcha mätfilen")
    if record.get("coordinate_reference_system") != "EPSG:3007":
        raise ValueError("Metadatafilen måste ange EPSG:3007")
    if record.get("coordinates") is not None:
        raise ValueError("Koordinater ska komma från koordinatfilen, inte metadatafilen")
    verification = record.get("catalogue_verification")
    if not isinstance(verification, dict) or not verification.get("verifier"):
        raise ValueError("Kataloggranskningen måste ange verifierare")
    try:
        date.fromisoformat(verification["date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Kataloggranskningen måste ha ett giltigt datum") from exc
    if not str(record.get("source", "")).strip():
        raise ValueError("Sensorns källa måste anges")
    if not str(record.get("source_file", "")).strip():
        raise ValueError("Sensorns källfil måste anges")
    inputs = (sensor_id, row_count, first_day, last_day, northing, easting,
              record, measurements, data_dir)
    if dry_run:
        return _register_sensor(*inputs, dry_run=True)
    with _import_lock(data_dir):
        return _register_sensor(*inputs, dry_run=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--coordinates", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True,
                        help="Granskad sensorpost som JSON-objekt")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(add_sensor(args.measurements, args.coordinates, args.record,
                         dry_run=args.dry_run))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Import avbruten: {exc}\n")


if __name__ == "__main__":
    main()
