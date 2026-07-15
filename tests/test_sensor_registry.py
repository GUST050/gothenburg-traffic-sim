import json

import pytest

from sensor_registry import load_registry


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
