"""A new file import must preserve the reviewed sensor gate and old inputs."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

import pytest

from tools import add_sensor as importer
from tools.add_sensor import add_sensor


def _inputs(tmp_path, sensor_id="9999"):
    data_dir = tmp_path / "data_in"
    data_dir.mkdir(exist_ok=True)
    registry = json.loads(Path("data_in/sensors.json").read_text())
    if not (data_dir / "sensors.json").exists():
        (data_dir / "sensors.json").write_text(json.dumps(registry))
    measurements = tmp_path / f"counts_{sensor_id}.csv"
    measurements.write_text(
        "Mätplats,level,date,Kvart,Antal passager\n"
        f"{sensor_id},Total,2025-09-16 00:00:00,00:00:00,12\n"
        f"{sensor_id},Total,2025-09-16 00:00:00,00:15:00,7\n")
    coords = tmp_path / f"positions_{sensor_id}.csv"
    coords.write_text(f"Mätplats,LatX,LongY\n{sensor_id},6398000,320000\n")
    record = dict(registry["sensors"][1])
    record.update(sensor_id=sensor_id, coordinates=None,
                  source_file=coords.name, active_from="2025-01-01",
                  approved_edge_ids=[f"new_{sensor_id}_edge"])
    record_path = tmp_path / f"record_{sensor_id}.json"
    record_path.write_text(json.dumps(record))
    return data_dir, measurements, coords, record_path


def test_add_sensor_validates_then_adds_files_and_registry(tmp_path):
    data_dir, measurements, coords, record_path = _inputs(tmp_path)
    previous = (data_dir / "sensors.json").read_bytes()

    add_sensor(measurements, coords, record_path, data_dir=data_dir, dry_run=True)
    assert (data_dir / "sensors.json").read_bytes() == previous
    assert not (data_dir / "sensor_9999.csv").exists()

    add_sensor(measurements, coords, record_path, data_dir=data_dir)
    payload = json.loads((data_dir / "sensors.json").read_text())
    assert len(payload["sensors"]) == 7
    assert payload["sensors"][-1]["sensor_id"] == "9999"
    assert (data_dir / "sensor_9999.csv").read_text() == measurements.read_text()
    assert "9999,6398000,320000" in (
        data_dir / "sensor_9999_koordinater.csv").read_text()


def test_pending_sensor_cannot_be_imported(tmp_path):
    data_dir, measurements, coords, record_path = _inputs(tmp_path)
    record = json.loads(record_path.read_text())
    record["snap_status"] = "pending"
    record_path.write_text(json.dumps(record))
    previous = (data_dir / "sensors.json").read_bytes()

    with pytest.raises(ValueError, match="snaps must be approved"):
        add_sensor(measurements, coords, record_path, data_dir=data_dir)

    assert (data_dir / "sensors.json").read_bytes() == previous
    assert not (data_dir / "sensor_9999.csv").exists()


def test_duplicate_measurement_interval_is_rejected(tmp_path):
    data_dir, measurements, coords, record_path = _inputs(tmp_path)
    measurements.write_text(measurements.read_text().replace("00:15:00", "00:00:00"))

    with pytest.raises(ValueError, match="Dubbelt mätintervall"):
        add_sensor(measurements, coords, record_path, data_dir=data_dir)


def test_missing_counts_do_not_create_a_sensor(tmp_path):
    data_dir, measurements, coords, record_path = _inputs(tmp_path)
    measurements.write_text(measurements.read_text().replace(",12\n", ",\n")
                            .replace(",7\n", ",\n"))

    with pytest.raises(ValueError, match="saknar giltiga passageräkningar"):
        add_sensor(measurements, coords, record_path, data_dir=data_dir)
    assert not (data_dir / "sensor_9999.csv").exists()


def test_import_rejects_an_existing_sensor_edge(tmp_path):
    data_dir, measurements, coords, record_path = _inputs(tmp_path)
    record = json.loads(record_path.read_text())
    record["approved_edge_ids"] = ["60790252_60790253_0"]
    record_path.write_text(json.dumps(record))

    with pytest.raises(ValueError, match="återanvänder redan registrerade"):
        add_sensor(measurements, coords, record_path, data_dir=data_dir)
    assert not (data_dir / "sensor_9999.csv").exists()


def test_concurrent_imports_keep_both_registry_entries(tmp_path, monkeypatch):
    first = _inputs(tmp_path, "FAKE_A")
    second = _inputs(tmp_path, "FAKE_B")
    original_load = importer.load_registry

    def slow_load(path, **kwargs):
        loaded = original_load(path, **kwargs)
        if Path(path) == first[0] / "sensors.json":
            time.sleep(0.05)
        return loaded

    monkeypatch.setattr(importer, "load_registry", slow_load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(add_sensor, counts, coords, record,
                               data_dir=data_dir)
                   for data_dir, counts, coords, record in (first, second)]
        for future in futures:
            future.result()

    payload = json.loads((first[0] / "sensors.json").read_text())
    assert {entry["sensor_id"] for entry in payload["sensors"]} >= {
        "FAKE_A", "FAKE_B"}
