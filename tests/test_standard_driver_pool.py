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


def test_source_quarter_bound_applies_to_measured_and_unmeasured_routes():
    vehicles = [
        passage.RouteVehicle('measured', 901.0, ('a', 'e')),
        passage.RouteVehicle('other', 1799.0, ('b',)),
    ]
    windows = pool._departure_windows(
        vehicles, {'measured': [120.0]}, duration_s=1800, guard_s=60,
        preserve_source_quarter=True)
    assert all(9000 <= window.lower <= window.upper < 18000
               for window in windows)
    schedule = pool.derive_distributed_departures(
        vehicles, {'measured': [120.0]}, duration_s=1800, guard_s=60,
        preserve_source_quarter=True)
    assert all(900 <= value < 1800 for value in schedule.values())


def test_quarter_bound_refuses_travel_time_longer_than_window():
    vehicle = passage.RouteVehicle('v', 950.0, ('a', 'e'))
    with pytest.raises(pool.StandardDriverPoolError, match='no robust'):
        pool.derive_distributed_departures(
            [vehicle], {'v': [850.0]}, duration_s=1800, guard_s=60,
            preserve_source_quarter=True)


def test_quarter_trial_retains_evidence_and_checks_unseen_arms(tmp_path, monkeypatch):
    route = tmp_path / 'source.rou.xml'
    route.write_text('<routes><vType id="car"/><vehicle id="v" type="car" depart="100"><route edges="a e"/></vehicle></routes>')
    agents = tmp_path / 'agents.json'
    agents.write_text(json.dumps({'agents': [{'vehicle_id': 'v', 'departure_s': 100}]}))
    metadata = {'date': '2027-09-08', 'build_id': 'test', 'n_intervals': 1,
                'sensor_targets': {'variants': {'edge_shares': {'e': [1]}}}}
    output = tmp_path / 'output'
    calls = []

    def run_arm(**kwargs):
        calls.append(kwargs['arm'])
        kwargs['run_dir'].mkdir()
        (kwargs['run_dir'] / 'raw.xml').write_text('<evidence/>')
        return {'v': [10.]}, {'e': [1]}, str(kwargs['arm']), .01

    monkeypatch.setattr(pool, '_run_arm', run_arm)
    before = route.read_bytes(), agents.read_bytes()
    report = pool.reconcile_quarter_departures(
        route, agents, metadata, route, output, home=tmp_path)
    assert report['status'] == 'verified_exact_isolated'
    assert report['source_quarters_preserved'] is True
    assert calls == [1000, 1001, 1002, 2000, 2001, 2002]
    assert len(list(output.rglob('raw.xml'))) == 6
    assert (output / 'calibrated.agents.json').is_file()
    assert before == (route.read_bytes(), agents.read_bytes())


def test_quarter_trial_refuses_unseen_arm_failure(tmp_path, monkeypatch):
    route = tmp_path / 'source.rou.xml'
    route.write_text('<routes><vehicle id="v" depart="100"><route edges="a e"/></vehicle></routes>')
    agents = tmp_path / 'agents.json'
    agents.write_text(json.dumps({'agents': [{'vehicle_id': 'v', 'departure_s': 100}]}))
    metadata = {'date': '2027-09-08', 'build_id': 'test', 'n_intervals': 1,
                'sensor_targets': {'variants': {'edge_shares': {'e': [1]}}}}

    def run_arm(**kwargs):
        kwargs['run_dir'].mkdir()
        return {'v': [10.]}, {'e': [0 if kwargs['arm'] == 2000 else 1]}, str(kwargs['arm']), .01

    monkeypatch.setattr(pool, '_run_arm', run_arm)
    report = pool.reconcile_quarter_departures(
        route, agents, metadata, route, tmp_path / 'output', home=tmp_path)
    assert report['status'] == 'refused'
    assert 'held-out' in report['reason']
    assert not (tmp_path / 'output' / 'calibrated.agents.json').exists()


def test_quarter_assignment_minimizes_unnecessary_route_time_changes():
    windows = [pool.DepartureWindow('early', 1000, 0, 8000),
               pool.DepartureWindow('late', 7000, 0, 7500)]
    # Earliest deadline first swaps both routes even though their own slots work.
    assigned = pool.minimize_quarter_shifts(
        windows, {'early': 700., 'late': 100.})
    assert assigned == {'early': 100., 'late': 700.}


def test_minimum_shift_assignment_respects_passage_deadlines():
    windows = [pool.DepartureWindow('urgent', 7000, 0, 1500),
               pool.DepartureWindow('flexible', 1000, 0, 8000)]
    assigned = pool.minimize_quarter_shifts(
        windows, {'urgent': 100., 'flexible': 700.})
    assert assigned == {'urgent': 100., 'flexible': 700.}
