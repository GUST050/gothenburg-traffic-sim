from __future__ import annotations

import math
import csv
import hashlib
import json

import pytest

from dirsplit import dataset
from dirsplit import train
from dirsplit import evaluate
from dirsplit.features import FEATURE_NAMES


def feature_row(radial_cos: float) -> dict[str, float]:
    values = {name: 0.0 for name in FEATURE_NAMES}
    values["radial_cos"] = radial_cos
    return values


def source(timestamp: str, heading: str, volume: object,
           coverage: object = 100.0) -> dict[str, object]:
    return {"from": timestamp, "heading": heading, "volume": volume,
            "coverage_pct": coverage}


def build(rows):
    return dataset.daily_direction_rows(
        rows, ["toward", "away"], station_id="station-1", city="Oslo",
        features_by_heading={"toward": feature_row(0.8),
                             "away": feature_row(-0.8)},
        profile={name: 0.0 for name in dataset.PROFILE_FEATURES})


def test_raw_simultaneous_counts_and_day_block_are_preserved():
    rows = build([
        source("2025-09-01T06:00:00+02:00", "toward", 60),
        source("2025-09-01T06:00:00+02:00", "away", 40),
    ])
    assert len(rows) == 2
    assert {row["day_block_id"] for row in rows} == {"station-1|2025-09-01"}
    assert {row["local_timestamp"] for row in rows} == {
        "2025-09-01T06:00:00+02:00"}
    by_role = {row["direction_role"]: row for row in rows}
    assert by_role["toward"]["toward_count"] == 60
    assert by_role["toward"]["away_count"] == 40
    assert by_role["toward"]["total_count"] == 100
    assert math.isclose(by_role["toward"]["share"], 0.6)
    assert math.isclose(by_role["away"]["share"], 0.4)
    assert all(row["usable"] == 1 for row in rows)


def test_missing_direction_is_retained_but_not_fabricated():
    rows = build([
        source("2025-09-01T06:00:00+02:00", "toward", 60),
    ])
    assert len(rows) == 2
    assert all(row["usable"] == 0 for row in rows)
    assert all("missing_direction:away" in row["missingness_reason"]
               for row in rows)
    assert all(row["share"] == "" for row in rows)
    assert all(row["total_count"] == "" for row in rows)


def test_low_coverage_and_nonfinite_volume_fail_closed():
    low = build([
        source("2025-09-01T06:00:00+02:00", "toward", 60, 79.9),
        source("2025-09-01T06:00:00+02:00", "away", 40, 79.9),
    ])
    assert all(row["usable"] == 0 for row in low)
    assert all(row["missingness_reason"] == "coverage_below_80" for row in low)

    invalid = build([
        source("2025-09-01T06:00:00+02:00", "toward", float("nan")),
        source("2025-09-01T06:00:00+02:00", "away", 40),
    ])
    assert all(row["usable"] == 0 for row in invalid)
    assert all("invalid_volume" in row["missingness_reason"]
               for row in invalid)


def test_local_calendar_fields_follow_the_offset_timestamp():
    rows = build([
        source("2025-10-26T02:00:00+02:00", "toward", 55),
        source("2025-10-26T02:00:00+02:00", "away", 45),
    ])
    assert {row["local_date"] for row in rows} == {"2025-10-26"}
    assert {row["hour"] for row in rows} == {2}
    assert {row["weekday"] for row in rows} == {6}
    assert {row["is_weekend"] for row in rows} == {1}
    assert all(row["is_holiday"] == "" for row in rows)


def test_product_trainer_ignores_audit_rows_and_weights_raw_counts(
        tmp_path, monkeypatch):
    path = tmp_path / "training.csv"
    header = ["station_id", "city", "heading", "hour", "is_weekend",
              "share", "n_obs", "total_count", "usable"] \
        + list(FEATURE_NAMES) + list(dataset.PROFILE_FEATURES)
    static = feature_row(0.8)
    profile = {name: 0.0 for name in dataset.PROFILE_FEATURES}
    records = [
        {"station_id": "S1", "city": "Oslo", "heading": "toward",
         "hour": 8, "is_weekend": 0, "share": 0.6, "n_obs": 1,
         "total_count": 100, "usable": 1, **static, **profile},
        {"station_id": "S1", "city": "Oslo", "heading": "toward",
         "hour": 9, "is_weekend": 0, "share": "", "n_obs": 1,
         "total_count": "", "usable": 0, **static, **profile},
    ]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(records)
    monkeypatch.setattr(train, "TABLE_PATH", path)
    matrix, labels, _cities, _keys, weights = train.load_table()
    assert matrix.shape[0] == labels.shape[0] == weights.shape[0] == 1
    assert labels.tolist() == [0.6]
    assert weights.tolist() == [10.0]
    assert matrix.shape[1] == len(FEATURE_NAMES) + 3
    assert not any(name in train.INPUT_COLS
                   for name in dataset.PROFILE_FEATURES)


def test_product_trainer_shrinkage_is_finite_and_fail_closed():
    lam, metrics = train.calibrate_shrinkage(
        [0.6, 0.4, 0.55], [0.58, 0.43, 0.52])
    assert 0.0 <= lam <= 1.0
    assert all(math.isfinite(value) for value in metrics.values())

    with pytest.raises(ValueError, match="finite"):
        train.calibrate_shrinkage([0.6, float("nan")], [0.55, 0.45])


def test_model_package_must_match_its_report_and_dataset(
        tmp_path, monkeypatch):
    report = {"protocol": train.TRAIN_PROTOCOL, "metric": 1}
    report["content_key"] = evaluate.content_digest(report)
    report_path = tmp_path / "train_report.json"
    report_path.write_text(json.dumps(report))
    dataset_binding = {"protocol": dataset.DATASET_PROTOCOL, "rows": 10}
    monkeypatch.setattr(
        evaluate, "validate_dataset_provenance", lambda _path: dataset_binding)
    package = {
        "protocol": train.TRAIN_PROTOCOL,
        "training_report_content_key": report["content_key"],
        "training_dataset": dataset_binding,
    }
    assert train.validate_model_package(
        package, report_path=report_path,
        table_path=tmp_path / "training.csv")["dataset"] == dataset_binding

    package["training_report_content_key"] = "stale"
    with pytest.raises(ValueError, match="do not match"):
        train.validate_model_package(
            package, report_path=report_path,
            table_path=tmp_path / "training.csv")


def test_gate_m_binds_the_table_manifest_and_live_raw_source(tmp_path):
    table = tmp_path / "training_table.csv"
    table.write_text("header\n")
    raw = tmp_path / "source.csv"
    raw.write_text("raw\n")
    sources = [{"path": str(raw),
                "sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}]
    meta = {
        "protocol": dataset.DATASET_PROTOCOL,
        "primary_table_sha256": hashlib.sha256(table.read_bytes()).hexdigest(),
        "source_volume_files": sources,
        "source_content_key": hashlib.sha256(json.dumps(
            sources, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest(),
        "rows": 1, "usable_rows": 1, "day_blocks": 1,
    }
    (tmp_path / dataset.META_PATH.name).write_text(json.dumps(meta))
    bound = evaluate.validate_dataset_provenance(table)
    assert bound["protocol"] == dataset.DATASET_PROTOCOL
    assert bound["source_volume_files"] == 1

    raw.write_text("drift\n")
    try:
        evaluate.validate_dataset_provenance(table)
    except ValueError as error:
        assert "raw volume provenance drifted" in str(error)
    else:
        raise AssertionError("raw source drift must fail closed")
