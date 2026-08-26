import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from tools import departure_reconciliation as dr


def _route(path: Path) -> None:
    path.write_text(
        '<routes>\n'
        '  <vehicle id="v0" depart="100.0"><route edges="a e"/></vehicle>\n'
        '  <vehicle id="v1" depart="850.0"><route edges="b e"/></vehicle>\n'
        '</routes>\n')


def _agents(path: Path) -> None:
    path.write_text(json.dumps({
        "schema_version": 1,
        "agents": [
            {"vehicle_id": "v0", "departure_s": 100.0, "purpose": "work"},
            {"vehicle_id": "v1", "departure_s": 850.0, "purpose": "through"},
        ],
    }, separators=(",", ":")))


def _stats(path: Path) -> None:
    path.write_text(
        '<statistics><vehicles loaded="2" inserted="2" running="0" '
        'waiting="0"/><teleports total="0"/><safety collisions="0"/>'
        '</statistics>')


def test_validate_route_targets_is_exact_and_fail_closed(tmp_path):
    route = tmp_path / "route.rou.xml"
    _route(route)
    vehicles = dr.read_route_vehicles(route)

    dr.validate_route_targets(vehicles, {"e": [2]}, n_intervals=1)
    with pytest.raises(dr.DepartureReconciliationError,
                       match="source route is not exact"):
        dr.validate_route_targets(vehicles, {"e": [1]}, n_intervals=1)


def test_route_target_validation_is_generic_at_fifty_sensors():
    vehicles = [
        dr.RouteVehicle(f"v{index}", float(index + 1), (f"e{index}",))
        for index in range(50)
    ]
    targets = {
        f"e{index}": [1.0] + [0.0] * 95
        for index in range(50)
    }

    dr.validate_route_targets(vehicles, targets, n_intervals=96)


def test_parse_passage_evidence_uses_sensor_entry_not_route_exit(tmp_path):
    route = tmp_path / "route.rou.xml"
    _route(route)
    evidence = tmp_path / "vehroute.xml"
    evidence.write_text(
        '<routes>'
        '<vehicle id="v0" depart="101" speedFactor="0.9">'
        '<route edges="a e" exitTimes="151 201"/></vehicle>'
        '<vehicle id="v1" depart="852" speedFactor="1.1">'
        '<route edges="b e" exitTimes="902 952"/></vehicle>'
        '</routes>')

    offsets, digest = dr.parse_passage_evidence(
        evidence, dr.read_route_vehicles(route), {"e"})

    # e is route index 1, so its entry is the previous edge's exit time.
    assert offsets == {"v0": [51.0], "v1": [52.0]}
    assert len(digest) == 64


def test_monotone_projection_keeps_order_and_guard():
    vehicles = [
        dr.RouteVehicle("v0", 100.0, ("a", "e")),
        dr.RouteVehicle("v1", 850.0, ("b", "e")),
        dr.RouteVehicle("v2", 910.0, ("c", "e")),
    ]
    schedule = dr.derive_monotone_departures(
        vehicles,
        {"v0": [0.0], "v1": [100.0], "v2": [100.0]},
        duration_s=1800,
        guard_s=60,
    )

    assert schedule["v0"] == 100.0
    assert schedule["v1"] == 740.0
    assert schedule["v2"] == 910.0
    assert list(schedule.values()) == sorted(schedule.values())


def test_departure_dispersion_gate_rejects_artificial_convoy():
    source = dr.departure_spacing_summary([0.0, 3.0, 6.0, 9.0, 12.0])
    convoy = dr.departure_spacing_summary([0.0, 0.1, 0.2, 0.3, 0.4])

    with pytest.raises(dr.DepartureReconciliationError,
                       match="departure dispersion gate failed"):
        dr.validate_departure_dispersion(source, convoy)


def test_rewrite_changes_only_departure_text_and_preserves_order(tmp_path):
    source = tmp_path / "source.rou.xml"
    output = tmp_path / "output.rou.xml"
    _route(source)

    dr.rewrite_departures_preserving_order(
        source, output, {"v0": 90.0, "v1": 740.0})

    assert output.read_text() == source.read_text().replace(
        'depart="100.0"', 'depart="90.0"').replace(
            'depart="850.0"', 'depart="740.0"')
    assert [vehicle.vehicle_id for vehicle in dr.read_route_vehicles(output)] == [
        "v0", "v1"]


def _fake_sumo(monkeypatch, *, exact=True):
    def run(command, *, cwd, sumo_home):
        del sumo_home
        command = list(command)
        stats = Path(command[command.index("--statistic-output") + 1])
        _stats(stats)
        if "--vehroute-output" in command:
            seed = int(command[command.index("--seed") + 1])
            vehroute = Path(command[command.index("--vehroute-output") + 1])
            vehroute.write_text(
                '<routes>'
                f'<vehicle id="v0" depart="100" speedFactor="0.{seed % 10}">'
                '<route edges="a e" exitTimes="150 200"/></vehicle>'
                f'<vehicle id="v1" depart="950" speedFactor="1.{seed % 10}">'
                '<route edges="b e" exitTimes="1000 1050"/></vehicle>'
                '</routes>')
        else:
            entered = 2 if exact else 1
            (Path(cwd) / "edge.xml").write_text(
                '<meandata><interval begin="0" end="900">'
                f'<edge id="e" entered="{entered}"/>'
                '</interval></meandata>')

    monkeypatch.setattr(dr, "_run_sumo", run)


def test_full_reconciliation_preserves_population_routes_and_updates_agents(
        tmp_path, monkeypatch):
    route = tmp_path / "calibrated.rou.xml"
    agents = tmp_path / "calibrated.agents.json"
    net = tmp_path / "net.net.xml"
    _route(route)
    _agents(agents)
    net.write_text("<net/>")
    _fake_sumo(monkeypatch)

    report = dr.reconcile_sensor_passage_times(
        route, agents, {"e": [2]}, duration_s=900,
        net_path=net, sumo_home=tmp_path,
        seeds=(1000, 1001, 1002), guard_s=60)

    rewritten = dr.read_route_vehicles(route)
    assert [(row.vehicle_id, row.edges) for row in rewritten] == [
        ("v0", ("a", "e")), ("v1", ("b", "e"))]
    assert [row.depart_s for row in rewritten] == [100.0, 690.0]
    assert [row["departure_s"] for row in
            json.loads(agents.read_text())["agents"]] == [100.0, 690.0]
    assert report["status"] == "exact"
    assert report["vehicles"] == 2
    assert report["vehicles_added"] == 0
    assert report["vehicles_removed"] == 0
    assert report["routes_changed"] == 0
    assert report["exact_per_seed"] == 1
    assert len(set(report["seed_profile_digests"])) == 3


def test_failed_verification_leaves_route_and_agents_unchanged(
        tmp_path, monkeypatch):
    route = tmp_path / "calibrated.rou.xml"
    agents = tmp_path / "calibrated.agents.json"
    net = tmp_path / "net.net.xml"
    _route(route)
    _agents(agents)
    net.write_text("<net/>")
    before_route = route.read_bytes()
    before_agents = agents.read_bytes()
    _fake_sumo(monkeypatch, exact=False)

    with pytest.raises(dr.DepartureReconciliationError,
                       match="reconciled SUMO output is not exact"):
        dr.reconcile_sensor_passage_times(
            route, agents, {"e": [2]}, duration_s=900,
            net_path=net, sumo_home=tmp_path,
            seeds=(1000, 1001, 1002), guard_s=60)

    assert route.read_bytes() == before_route
    assert agents.read_bytes() == before_agents
