"""build_data.load_raw must not ingest the sensor-coordinates CSV as count
data when both live in the same data_in/ drop folder (found in a bug
review 2026-07-10, independently verified): the coordinates file has a
Matplats-like column but no level/datum/tid/count columns, so concatenating
it in produces rows with a sensor ID but null everything else, which can
silently corrupt sensor_level for a sensor not already covered by the
hardcoded SENSOR_MEASURED_DIRECTION overrides."""

import pandas as pd
import pytest

import build_data as bd


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
