"""Automatically chosen closure benchmarks must have an effect to measure.

The frozen September profile closed `26355153_26842525_0`, sensor 133's
unmeasured opposite carriageway. The survivability screen ranked it first
because it lies 11 m from a sensor, but no catalog route and no calibrated
vehicle uses it, so every candidate cost exactly zero. Structural
survivability and effect eligibility are two different properties, and the
benchmark's automatic road choice now requires both.

A closure a USER chooses is not subject to this contract: a directed edge
without traffic is a legitimate closure whose honest answer is zero, and a
closure covers both carriageways only when both are listed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

import tools.cost_ordered_benchmark as bench
from traffic_sim.core.closure_calendar import iter_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
from traffic_sim.simulation import effect_eligibility as effect
from traffic_sim.simulation.deterministic_disruption import (
    VARIANT_FILENAMES,
    closure_seconds,
    parent_closure_cost,
)
from traffic_sim.simulation.disruption import (
    build_parsed_window_cost_index,
    parse_route_vehicles,
)
from traffic_sim.simulation.independent_daily import daily_unit_records

ZERO_EDGE = "26355153_26842525_0"
MEASURED_REVERSE = "26842525_26355153_0"


def _screen_edges():
    screen = json.loads(bench.SURVIVABILITY_SCREEN.read_text(encoding="utf-8"))
    return [item["edge_id"] for item in screen["candidate_pool"]["edges"]]


def _routes_xml(routes, *, vehicles):
    if vehicles:
        body = "".join(
            f'<vehicle id="v{n}" depart="{n}.00"><route edges="{route}"/>'
            "</vehicle>" for n, route in enumerate(routes))
    else:
        body = "".join(f'<route id="r{n}" edges="{route}"/>'
                       for n, route in enumerate(routes))
    return f"<routes>{body}</routes>"


def _library(root: Path, *, carried, catalog_edges, dates=("2025-09-16",),
             pool_sha=None):
    """Archives whose vehicles cross ``carried`` and whose bound catalog pool
    lists ``catalog_edges``; nothing else the screen names is used."""
    catalog_root = root / "route_catalog"
    pool = catalog_root / "fixture-weekday"
    pool.mkdir(parents=True, exist_ok=True)
    catalog_text = _routes_xml([f"A {edge}" for edge in catalog_edges],
                               vehicles=False)
    (pool / "catalog.rou.xml").write_text(catalog_text, encoding="utf-8")
    sha = pool_sha or hashlib.sha256(catalog_text.encode()).hexdigest()
    runs = root / "runs"
    for work_date in dates:
        archive = runs / f"demand-{work_date.replace('-', '')}-historical"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / "demand_meta.json").write_text(json.dumps({
            "demand_build_key": f"key-{work_date}",
            "epoch_sim": f"{work_date}T00:00:00",
            "n_intervals": 96, "n_variants": 3,
            "demand_spec": {"start_date": work_date, "source": "historical",
                            "days": 1, "begin": "00:00", "end": "24:00",
                            "structural_reference_date": "2025-09-16",
                            "purpose": "standard"},
            "candidate_catalog": {
                "keys": {"weekday": "fixture-weekday"},
                "artifacts": {"weekday": {"routes_sha256": sha}}},
        }), encoding="utf-8")
        for filename in VARIANT_FILENAMES.values():
            (archive / filename).write_text(
                _routes_xml([f"A {edge}" for edge in carried], vehicles=True),
                encoding="utf-8")
    return runs, catalog_root


@pytest.fixture
def effect_sources(monkeypatch):
    """Network and catalog root for the benchmark's effect screen."""
    network = frozenset(_screen_edges()) | {"A"}

    def install(catalog_root):
        monkeypatch.setattr(bench, "_effect_sources",
                            lambda data_root: (network, Path(catalog_root)),
                            raising=False)
    return install


@pytest.fixture
def resolvable(monkeypatch):
    """Metadata-only fixtures stand in for full product archive validation."""
    def resolve(spec, runs_root, cache=None):
        return bench._archive_index(runs_root) or None
    monkeypatch.setattr(bench, "_resolved_archives_for_spec", resolve)


class TestTheGeometricRuleAloneIsBlindToEffect:
    def test_it_ranks_the_zero_catalog_edge_first(self, tmp_path):
        """The regression: survivability ranks an edge nothing uses first."""
        _runs, catalog_root = _library(
            tmp_path, carried=[MEASURED_REVERSE],
            catalog_edges=[MEASURED_REVERSE])
        assert bench.surviving_roads()[0]["edge_id"] == ZERO_EDGE
        routes = effect.edge_counts(
            catalog_root / "fixture-weekday" / "catalog.rou.xml", "route")
        assert routes.get(ZERO_EDGE, 0) == 0

    def test_the_structural_screen_itself_is_unchanged(self):
        screen = json.loads(bench.SURVIVABILITY_SCREEN.read_text(
            encoding="utf-8"))
        surviving = [item for item in screen["candidate_pool"]["edges"]
                     if item["survives_topology"]]
        surviving.sort(key=lambda item: (float(item["dist_sensor_m"]),
                                         str(item["edge_id"])))
        expected = [item["edge_id"]
                    for item in surviving[:bench.DISCOVERY_ROAD_LIMIT]]
        assert [item["edge_id"] for item in bench.surviving_roads()] == (
            expected)


class TestTheBenchmarkRuleRequiresAnEffect:
    def test_the_zero_edge_is_rejected_with_named_reasons(
            self, tmp_path, effect_sources):
        runs, catalog_root = _library(
            tmp_path, carried=[MEASURED_REVERSE],
            catalog_edges=[MEASURED_REVERSE])
        effect_sources(catalog_root)
        screen = {item["edge_id"]: item for item in bench.road_screen(runs)}
        zero = screen[ZERO_EDGE]
        assert zero["structurally_survivable"] is True
        assert zero["effect"]["pre_canary_eligible"] is False
        assert zero["effect"]["effect_eligible"] is False
        assert zero["effect"]["reasons"][:2] == [
            effect.NO_CATALOG_ROUTE, effect.NO_ARCHIVE_VEHICLE]
        chosen = [road["edge_id"] for road in bench.benchmark_roads(runs)]
        assert ZERO_EDGE not in chosen

    def test_a_carried_edge_is_chosen_instead(self, tmp_path,
                                              effect_sources):
        runs, catalog_root = _library(
            tmp_path, carried=[MEASURED_REVERSE],
            catalog_edges=[MEASURED_REVERSE])
        effect_sources(catalog_root)
        assert [road["edge_id"] for road in bench.benchmark_roads(runs)] == [
            MEASURED_REVERSE]
        edges = {spec.directed_edges for spec in bench.discovered_specs(runs)}
        assert edges == {(MEASURED_REVERSE,)}

    def test_discovery_never_builds_a_case_on_the_zero_edge(
            self, tmp_path, effect_sources):
        runs, catalog_root = _library(
            tmp_path, carried=[MEASURED_REVERSE, "A"],
            catalog_edges=[MEASURED_REVERSE])
        effect_sources(catalog_root)
        specs = bench.discovered_specs(runs)
        assert specs
        for spec in specs:
            assert ZERO_EDGE not in spec.directed_edges

    def test_a_catalog_route_without_real_vehicles_is_not_enough(
            self, tmp_path, effect_sources):
        runs, catalog_root = _library(
            tmp_path, carried=[], catalog_edges=[MEASURED_REVERSE])
        effect_sources(catalog_root)
        screen = {item["edge_id"]: item for item in bench.road_screen(runs)}
        assert screen[MEASURED_REVERSE]["effect"]["reasons"] == [
            effect.NO_ARCHIVE_VEHICLE, effect.CANARY_NOT_RUN]
        assert bench.benchmark_roads(runs) == []

    def test_a_tampered_catalog_pool_fails_closed(self, tmp_path,
                                                  effect_sources):
        runs, catalog_root = _library(
            tmp_path, carried=[MEASURED_REVERSE],
            catalog_edges=[MEASURED_REVERSE], pool_sha="0" * 64)
        effect_sources(catalog_root)
        screen = {item["edge_id"]: item for item in bench.road_screen(runs)}
        assert effect.CATALOG_POOL_MISMATCH in (
            screen[MEASURED_REVERSE]["effect"]["reasons"])
        assert bench.benchmark_roads(runs) == []

    def test_the_selection_record_names_the_rule_and_the_rejections(
            self, tmp_path, effect_sources, resolvable):
        runs, catalog_root = _library(
            tmp_path, carried=[MEASURED_REVERSE],
            catalog_edges=[MEASURED_REVERSE])
        effect_sources(catalog_root)
        selection = bench.select_case(runs, from_archives=True)
        assert selection["road_selection_rule"] == bench.ROAD_SELECTION_RULE
        rejected = {item["edge_id"]: item["reasons"]
                    for item in selection["road_screen"]
                    if not item["pre_canary_eligible"]}
        assert effect.NO_CATALOG_ROUTE in rejected[ZERO_EDGE]
        assert selection["selected"]["spec"]["directed_edges"] == [
            MEASURED_REVERSE]


class TestTheContractItself:
    @staticmethod
    def _evidence(**changes):
        base = dict(edge_id="e", in_network=True, archives=1,
                    pool_problems=(), catalog_routes={"weekday:k": 2},
                    archive_vehicles={"q10": 1, "q50": 1, "q90": 1},
                    archives_with_vehicle=1)
        base.update(changes)
        return effect.EdgeEffectEvidence(**base)

    def test_pre_canary_success_is_not_yet_effect_eligible(self):
        verdict = effect.assess(self._evidence())
        assert verdict.pre_canary_eligible is True
        assert verdict.effect_eligible is False
        assert verdict.reasons == (effect.CANARY_NOT_RUN,)

    def test_a_canary_with_effect_completes_eligibility(self):
        verdict = effect.assess(self._evidence().with_canary(
            affected_vehicles_total=3, positive_costs=1))
        assert verdict.effect_eligible is True
        assert verdict.reasons == ()

    def test_a_canary_without_effect_is_named(self):
        verdict = effect.assess(self._evidence().with_canary(
            affected_vehicles_total=0, positive_costs=0))
        assert verdict.effect_eligible is False
        assert verdict.reasons == (effect.CANARY_NO_AFFECTED_VEHICLE,
                                   effect.CANARY_NO_POSITIVE_COST)

    def test_an_edge_outside_the_network_is_named(self):
        verdict = effect.assess(self._evidence(in_network=False))
        assert verdict.reasons[0] == effect.EDGE_NOT_IN_NETWORK

    def test_an_unbound_catalog_pool_is_named(self, tmp_path):
        runs, _catalog = _library(tmp_path, carried=["X"],
                                  catalog_edges=["X"])
        archive = next(runs.glob("demand-*"))
        meta_path = archive / "demand_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.pop("candidate_catalog")
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        evidence = effect.collect_evidence(
            ["X"], network_edges={"X"}, archives=[archive],
            catalog_root=tmp_path / "route_catalog")["X"]
        verdict = effect.assess(evidence)
        assert effect.CATALOG_POOL_UNBOUND in verdict.reasons
        assert effect.NO_CATALOG_ROUTE in verdict.reasons

    def test_vehicles_are_counted_per_edge_and_variant(self, tmp_path):
        runs, catalog_root = _library(tmp_path, carried=["X", "X", "Y"],
                                      catalog_edges=["X"])
        evidence = effect.collect_evidence(
            ["X", "Y", "Z"], network_edges={"X", "Y", "Z"},
            archives=sorted(runs.glob("demand-*")),
            catalog_root=catalog_root)
        assert evidence["X"].archive_vehicles == {"q10": 2, "q50": 2,
                                                  "q90": 2}
        assert evidence["X"].catalog_routes == {"weekday:fixture-weekday": 1}
        assert evidence["Y"].catalog_routes == {"weekday:fixture-weekday": 0}
        assert evidence["Z"].archives_with_vehicle == 0


def _user_spec(edges):
    return ClosureSearchSpec(
        search_id="user-directed-closure",
        directed_edges=tuple(edges),
        demand_build_id="forecast-2027",
        source="forecast",
        permitted_date_start="2027-07-15",
        permitted_date_end="2027-07-15",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("06:00", "08:00"),
        allowed_weekdays=(3,),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        objective_profile="displaced_vehicles_and_detour_v1",
    )


def _closures(spec):
    parent = next(iter(iter_closure_schedules(spec)))
    records = list(daily_unit_records(spec, parent))
    assert len(records) == 1, "a one-day spec has exactly one daily unit"
    build = records[0][2]
    return closure_seconds(spec, build(),
                           epoch=datetime(2027, 7, 14, 0, 0),
                           duration_s=3 * 86400)


class TestUserClosuresKeepTheirMeaning:
    def test_a_zero_traffic_directed_edge_is_kept_and_prices_zero(
            self, tmp_path):
        """No effect gate applies to a user's own closure."""
        spec = ClosureSearchSpec.from_dict(_user_spec([ZERO_EDGE]).to_dict())
        assert spec.directed_edges == (ZERO_EDGE,)
        closures = _closures(spec)
        assert {item["edge_id"] for item in closures} == {ZERO_EDGE}

        routes = tmp_path / "routes.rou.xml"
        routes.write_text(_routes_xml(
            [f"A {MEASURED_REVERSE}"] * 3, vehicles=True), encoding="utf-8")
        index = build_parsed_window_cost_index(
            parse_route_vehicles(routes), {ZERO_EDGE},
            {"A": 1.0, MEASURED_REVERSE: 1.0, ZERO_EDGE: 1.0},
            {"A": 10.0, MEASURED_REVERSE: 10.0, ZERO_EDGE: 10.0},
            adjacency={"A": [MEASURED_REVERSE], MEASURED_REVERSE: [ZERO_EDGE],
                       ZERO_EDGE: []})
        record = index.disruption(closures)
        assert record["vehicles_affected"] == 0
        assert record["vehicles_considered"] == 3
        cost = parent_closure_cost("user-candidate", [[
            {"demand_variant": variant, **record}
            for variant in ("q10", "q50", "q90")]])
        assert cost.vehicles_affected == 0
        assert cost.added_vehicle_hours == 0

    def test_one_listed_direction_never_closes_its_reverse(self):
        closures = _closures(_user_spec([ZERO_EDGE]))
        assert MEASURED_REVERSE not in {item["edge_id"] for item in closures}

    def test_both_directions_close_only_when_both_are_listed(self):
        closures = _closures(_user_spec([ZERO_EDGE, MEASURED_REVERSE]))
        assert {item["edge_id"] for item in closures} == {
            ZERO_EDGE, MEASURED_REVERSE}
        windows = {}
        for item in closures:
            windows.setdefault(item["edge_id"], set()).add(
                (item["begin_s"], item["end_s"]))
        assert windows[ZERO_EDGE] == windows[MEASURED_REVERSE]
