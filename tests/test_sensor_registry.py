import json

import pytest

from traffic_sim.intake.sensors import load_registry


REGISTRY = "data_in/sensors.json"


def test_registry_contains_verified_direction_semantics():
    registry = load_registry(REGISTRY)
    assert registry["107"].level == "Total"
    assert registry["1074"].level == "V"
    assert registry["107"].catalogue_verification["status"] == "verified"


def test_unknown_sensor_fails_closed(tmp_path):
    registry = load_registry(REGISTRY)
    with pytest.raises(ValueError, match="absent from"):
        registry.validate_data_sensors(["not-a-real-sensor"],
                                       require_coordinates=False)


def test_coordinates_are_merged_from_delivery_and_validated(tmp_path):
    registry = load_registry(
        REGISTRY,
        coordinates={"107": (57.70, 11.98), "1074": (57.70, 11.99),
                     "1076": (57.70, 11.99), "133": (57.70, 11.99),
                     "134": (57.70, 11.99), "2276": (57.70, 11.99)},
    )
    registry.validate_data_sensors(["107", "1074"], require_coordinates=True)
    assert registry["107"].coordinates == (57.70, 11.98)


def test_duplicate_registry_ids_are_rejected(tmp_path):
    payload = json.loads(open(REGISTRY).read())
    payload["sensors"].append(dict(payload["sensors"][0]))
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicate sensor_id"):
        load_registry(path)


def test_pending_snap_cannot_constrain_calibration(tmp_path):
    payload = json.loads(open(REGISTRY).read())
    payload["sensors"][0]["snap_status"] = "pending"
    path = tmp_path / "pending.json"
    path.write_text(json.dumps(payload))
    registry = load_registry(path)
    with pytest.raises(ValueError, match="snaps must be approved"):
        registry.validate_data_sensors(["107"], require_coordinates=False)


def test_resolved_edges_must_match_reviewed_registry(tmp_path):
    registry = load_registry(REGISTRY)
    with pytest.raises(ValueError, match="resolved"):
        registry.validate_resolved_edges(
            {"107": ["wrong_edge"]}, sensor_ids=["107"])


def test_resolved_snap_distance_drift_is_rejected():
    registry = load_registry(REGISTRY)
    with pytest.raises(ValueError, match="distance drift"):
        registry.validate_resolved_edges(
            {"1074": ["60790252_60790253_0"]},
            resolved_distances_m={"1074": [40.0]}, sensor_ids=["1074"])
