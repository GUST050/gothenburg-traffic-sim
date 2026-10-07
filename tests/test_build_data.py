"""build_data.load_raw must not ingest the sensor-coordinates CSV as count
data when both live in the same data_in/ drop folder (found in a bug
review 2026-07-10, independently verified): the coordinates file has a
Matplats-like column but no level/datum/tid/count columns, so concatenating
it in produces rows with a sensor ID but null everything else, which can
silently corrupt sensor_level for a sensor not already covered by the
hardcoded SENSOR_MEASURED_DIRECTION overrides."""

import json
from pathlib import Path

import pandas as pd
import pytest

import build_data as bd
from traffic_sim.intake.sensors import load_registry


def _write_sensor_csv(path):
    path.write_text(
        "Mätplats,Level,Datum,Tid,Antal passager\n"
        "107,Total,2025-09-16,00:00,12\n"
        "107,Total,2025-09-16,00:15,7\n"
    )


def _write_coords_csv(path):
    path.write_text(
        "Mätplats,LatX,LongY\n"
        "107,6398000,320000\n"
    )


def test_load_raw_excludes_the_coordinates_file_when_given_its_path(tmp_path):
    _write_sensor_csv(tmp_path / "sensor_data.csv")
    coords = tmp_path / "koordinater.csv"
    _write_coords_csv(coords)

    raw = bd.load_raw(tmp_path, coords)

    assert len(raw) == 2
    assert raw["count"].notna().all()
    assert raw["level"].notna().all()


def test_load_raw_without_a_coords_path_still_ingests_everything_in_dir(tmp_path):
    """Documents the pre-fix behaviour when coords_path is omitted (e.g. a
    caller that doesn't know it) — not a bug in load_raw itself, just why
    main() must always pass coords_path through."""
    _write_sensor_csv(tmp_path / "sensor_data.csv")
    _write_coords_csv(tmp_path / "koordinater.csv")

    raw = bd.load_raw(tmp_path)

    assert raw["count"].isna().any()


def test_additional_sensor_delivery_preserves_original_rows(tmp_path):
    original = tmp_path / "original"
    added = tmp_path / "added"
    original.mkdir()
    added.mkdir()
    _write_sensor_csv(original / "old.csv")
    (added / "new.csv").write_text(
        "Mätplats,Level,Datum,Tid,Antal passager\n"
        "9999,V,2025-09-16,00:00,4\n")
    _write_coords_csv(added / "sensor_9999_koordinater.csv")

    raw = bd.load_raw([original, added], [added / "sensor_9999_koordinater.csv"])

    assert set(raw["matplats"]) == {"107", "9999"}
    assert len(raw) == 3


def test_different_header_case_across_deliveries_has_one_canonical_level(tmp_path):
    original = tmp_path / "original"
    added = tmp_path / "added"
    original.mkdir()
    added.mkdir()
    (original / "old.csv").write_text(
        "Mätplats,level,date,Kvart,Antal passager\n"
        "107,Total,2025-09-16,00:00,12\n")
    (added / "new.csv").write_text(
        "Mätplats,Level,Datum,Tid,Antal passager\n"
        "9999,V,2025-09-16,00:00,4\n")

    raw = bd.load_raw([original, added])

    assert raw.columns.is_unique
    assert raw["level"].tolist() == ["Total", "V"]
    assert raw["count"].tolist() == [12, 4]


def test_mixed_quarter_time_precision_across_deliveries_is_parsed(tmp_path):
    original = tmp_path / "original"
    added = tmp_path / "added"
    original.mkdir()
    added.mkdir()
    (original / "old.csv").write_text(
        "Mätplats,level,date,Kvart,Antal passager\n"
        "107,Total,2025-09-16,00:00:00,12\n")
    (added / "new.csv").write_text(
        "Mätplats,Level,Datum,Tid,Antal passager\n"
        "9999,V,2025-09-16,00:15,4\n")

    raw = bd.load_raw([original, added])

    assert raw["ts"].notna().all()
    assert raw["ts"].dt.strftime("%H:%M").tolist() == ["00:00", "00:15"]


def test_overlapping_deliveries_fail_instead_of_double_counting(tmp_path):
    original = tmp_path / "original"
    added = tmp_path / "added"
    original.mkdir()
    added.mkdir()
    _write_sensor_csv(original / "old.csv")
    _write_sensor_csv(added / "overlap.csv")

    with pytest.raises(ValueError, match="Duplicate sensor interval"):
        bd.load_raw([original, added])


def test_coordinate_addition_rejects_conflicting_existing_station(tmp_path):
    original = tmp_path / "original.csv"
    added = tmp_path / "added.csv"
    _write_coords_csv(original)
    added.write_text("Mätplats,LatX,LongY\n107,6399000,320000\n")

    with pytest.raises(ValueError, match="conflicting coordinates"):
        bd.load_coords([original, added])


def test_default_sources_include_original_and_imported_files(tmp_path, monkeypatch):
    original = tmp_path / "original"
    added = tmp_path / "data_in"
    original.mkdir()
    added.mkdir()
    (added / "sensor_9999.csv").write_text("placeholder")
    old_coords = tmp_path / "old_coordinates.csv"
    old_coords.write_text("placeholder")
    new_coords = added / "sensor_9999_koordinater.csv"
    new_coords.write_text("placeholder")
    monkeypatch.setattr(bd, "DEFAULT_DATA_DIR", str(original))
    monkeypatch.setattr(bd, "DEFAULT_COORDS", str(old_coords))
    monkeypatch.setattr(bd, "DROP_DIR", added)

    assert bd.discover_data_dirs() == [original, added]
    assert bd.discover_coords() == [old_coords, new_coords]


def test_new_sensor_is_checked_against_its_own_active_period(tmp_path):
    payload = json.loads(Path("data_in/sensors.json").read_text())
    new = dict(payload["sensors"][1])
    new.update(sensor_id="9999", active_from="2025-09-01")
    payload["sensors"].append(new)
    path = tmp_path / "sensors.json"
    path.write_text(json.dumps(payload))
    registry = load_registry(path, coordinates={
        "107": (57.7, 11.98), "9999": (57.7, 11.99),
    })
    raw = pd.DataFrame({
        "matplats": ["107", "9999"],
        "ts": pd.to_datetime(["2025-01-01", "2025-09-16"]),
    })

    bd.validate_sensor_periods(raw, registry, ["107", "9999"])

    raw.loc[1, "ts"] = pd.Timestamp("2025-08-31")
    with pytest.raises(ValueError, match="inactive"):
        bd.validate_sensor_periods(raw, registry, ["107", "9999"])
