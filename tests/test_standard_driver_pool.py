import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from tools import departure_reconciliation as passage
from tools import standard_driver_pool as pool


def _route(path: Path, *, suffix: str = "") -> None:
    path.write_text(
        '<routes>\n'
        f'<vehicle id="v0{suffix}" depart="10.0"><route edges="a e0"/></vehicle>\n'
        f'<vehicle id="v1{suffix}" depart="30.0"><route edges="b e1"/></vehicle>\n'
        '</routes>\n')


def test_pool_identity_changes_with_picker_day_and_route(tmp_path):
    first = tmp_path / "first.rou.xml"
    second = tmp_path / "second.rou.xml"
    _route(first)
    _route(second, suffix="x")
    base = {"date": "2027-09-08", "source": "forecast", "days": 1,
            "build_id": "build-a", "demand_build_key": "demand-a"}

    identity = pool.pool_identity(first, base, (1000, 1001, 1002))
    another_day = pool.pool_identity(
        first, {**base, "date": "2027-09-09", "build_id": "build-b"},
        (1000, 1001, 1002))
    another_route = pool.pool_identity(second, base, (1000, 1001, 1002))

    assert identity["pool_key"] != another_day["pool_key"]
    assert identity["pool_key"] != another_route["pool_key"]
    assert identity["profile_key"] != another_day["profile_key"]


def test_profiles_are_reproducible_distinct_and_complete(tmp_path):
    route = tmp_path / "route.rou.xml"
    _route(route)
    vehicles = passage.read_route_vehicles(route)

    first = pool.build_driver_profiles(
        vehicles, pool_key="day-a", arms=(1000, 1001, 1002))
    second = pool.build_driver_profiles(
        vehicles, pool_key="day-a", arms=(1000, 1001, 1002))

    assert first == second
    assert set(first[1000]) == {"v0", "v1"}
    assert first[1000] != first[1001]
    assert all(0.2 <= factor <= 2.0
               for profile in first.values() for factor in profile.values())


def test_large_profile_matches_declared_distribution():
    vehicles = [
        passage.RouteVehicle(f"v{index}", float(index), ("e",))
        for index in range(20_000)
    ]
    profiles = pool.build_driver_profiles(
        vehicles, pool_key="large-day", arms=(1000, 1001, 1002))

    for profile in profiles.values():
        summary = pool.profile_summary(profile)
        pool.validate_profile_distribution(summary)
        assert abs(summary["mean"] - 1.0) < 0.005
        assert abs(summary["deviation"] - 0.1) < 0.005


def test_load_sensor_targets_keeps_demand_axis_separate():
    metadata = {
        "n_intervals": 2,
        "sensor_targets": {"variants": {
            "edge_shares": {"e": [1, 2]},
            "lower": {"e": [0, 1]},
        }},
    }

    targets, duration = pool.load_sensor_targets(metadata)

    assert targets == {"e": [1.0, 2.0]}
    assert duration == 1800


def test_profile_route_preserves_ids_routes_and_sorts_departures(tmp_path):
    source = tmp_path / "source.rou.xml"
    output = tmp_path / "output.rou.xml"
    _route(source)
    vehicles = passage.read_route_vehicles(source)
    profiles = {"v0": 0.9, "v1": 1.1}
    departures = {"v0": 40.0, "v1": 20.0}

    pool.write_profile_route(source, output, profiles, departures)
    pool.validate_profile_route(vehicles, output, profiles, departures)

    nodes = ET.parse(output).getroot().findall("vehicle")
    assert [node.get("id") for node in nodes] == ["v1", "v0"]
    assert [node.get("speedFactor") for node in nodes] == [
        "1.100000", "0.900000"]


def test_distributed_schedule_can_reorder_without_bunching():
    vehicles = [
        passage.RouteVehicle("v0", 100.0, ("a", "e")),
        passage.RouteVehicle("v1", 103.0, ("b", "e")),
        passage.RouteVehicle("v2", 106.0, ("c", "e")),
        passage.RouteVehicle("v3", 109.0, ("d",)),
    ]
    # All measured vehicles can safely pass in q0 across every profile arm.
    offsets = {vehicle.vehicle_id: [100.0, 120.0, 140.0]
               for vehicle in vehicles[:3]}

    schedule = pool.derive_distributed_departures(
        vehicles, offsets, duration_s=900, guard_s=60)
    summary = passage.departure_spacing_summary(sorted(schedule.values()))

    passage.validate_departure_dispersion(
        passage.departure_spacing_summary([100.0, 103.0, 106.0, 109.0]),
        summary)
    assert set(schedule) == {"v0", "v1", "v2", "v3"}
    assert sorted(schedule.values()) == [100.0, 103.0, 106.0, 109.0]


def test_impossible_spacing_fails_closed():
    windows = [
        pool.DepartureWindow("v0", 0, 0, 0),
        pool.DepartureWindow("v1", 1, 1, 1),
    ]

    with pytest.raises(pool.StandardDriverPoolError,
                       match="cannot meet spacing"):
        pool._schedule_with_gap(windows, gap_ticks=2)


def test_build_is_isolated_and_requires_exact_sumo_evidence(
        tmp_path, monkeypatch):
    route = tmp_path / "calibrated.rou.xml"
    agents = tmp_path / "calibrated.agents.json"
    metadata = tmp_path / "demand_meta.json"
    net = tmp_path / "net.net.xml"
    output_root = tmp_path / "pools"
    _route(route)
    agents.write_text(json.dumps({"schema_version": 1, "agents": [
        {"vehicle_id": "v0", "departure_s": 10.0},
        {"vehicle_id": "v1", "departure_s": 30.0},
    ]}))
    metadata.write_text(json.dumps({
        "date": "2027-09-08", "source": "forecast", "days": 1,
        "build_id": "build-a", "demand_build_key": "demand-a",
        "n_intervals": 1,
        "sensor_targets": {"variants": {
            "edge_shares": {"e0": [1], "e1": [1]},
        }},
    }))
    net.write_text("<net/>")
    before_route = route.read_bytes()
    before_agents = agents.read_bytes()

    def fake_run_arm(**kwargs):
        arm_route = passage.read_route_vehicles(kwargs["route_path"])
        return (
            {vehicle.vehicle_id: [5.0] for vehicle in arm_route},
            {"e0": [1], "e1": [1]},
            f"digest-{kwargs['arm']}",
            0.01,
        )

    monkeypatch.setattr(pool, "_run_arm", fake_run_arm)

    result = pool.build_standard_driver_pool(
        route, agents, metadata, net, output_root,
        arms=(1000, 1001, 1002), home=tmp_path)

    manifest = json.loads((result / "manifest.json").read_text())
    assert manifest["status"] == "verified_exact_isolated"
    assert manifest["active_production"] is False
    assert manifest["vehicles_added"] == 0
    assert manifest["routes_changed"] == 0
    assert len(list(result.glob("calibrated.driver-*.rou.xml"))) == 3
    assert route.read_bytes() == before_route
    assert agents.read_bytes() == before_agents


def test_failed_pool_leaves_no_result_and_no_source_change(tmp_path, monkeypatch):
    route = tmp_path / "calibrated.rou.xml"
    agents = tmp_path / "calibrated.agents.json"
    metadata = tmp_path / "demand_meta.json"
    net = tmp_path / "net.net.xml"
    output_root = tmp_path / "pools"
    _route(route)
    agents.write_text(json.dumps({"schema_version": 1, "agents": [
        {"vehicle_id": "v0", "departure_s": 10.0},
        {"vehicle_id": "v1", "departure_s": 30.0},
    ]}))
    metadata.write_text(json.dumps({
        "date": "2027-09-08", "source": "forecast", "days": 1,
        "build_id": "build-a", "n_intervals": 1,
        "sensor_targets": {"variants": {
            "edge_shares": {"e0": [1], "e1": [1]},
        }},
    }))
    net.write_text("<net/>")
    before = route.read_bytes(), agents.read_bytes()

    def never_exact(**kwargs):
        arm_route = passage.read_route_vehicles(kwargs["route_path"])
        return (
            {vehicle.vehicle_id: [5.0] for vehicle in arm_route},
            {"e0": [0], "e1": [0]},
            f"digest-{kwargs['arm']}",
            0.01,
        )

    monkeypatch.setattr(pool, "_run_arm", never_exact)
    monkeypatch.setattr(
        pool, "derive_distributed_departures",
        lambda original, offsets, **kwargs: {
            vehicle.vehicle_id: vehicle.depart_s + 0.1 for vehicle in original})

    with pytest.raises(pool.StandardDriverPoolError,
                       match="stopped changing|not exact"):
        pool.build_standard_driver_pool(
            route, agents, metadata, net, output_root,
            arms=(1000, 1001, 1002), max_iterations=2, home=tmp_path)

    assert (route.read_bytes(), agents.read_bytes()) == before
    assert not any(output_root.rglob("manifest.json"))
