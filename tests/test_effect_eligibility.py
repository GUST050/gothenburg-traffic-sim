"""Automatically chosen performance cases must have an effect to measure.

The frozen September profile closed `26355153_26842525_0`, sensor 133's
unmeasured opposite carriageway. It survives its own closure, but no catalog
route and no calibrated vehicle uses it, so every candidate cost exactly
zero. Two properties are therefore kept apart:

* structural survivability, which general discovery keeps using unchanged;
* effect eligibility (`effect_eligibility_v2`), which only automatic
  WCI/benchmark/performance selection requires.

A closure a USER chooses is never subject to effect eligibility: a directed
edge without traffic is a legitimate closure whose honest answer is zero, and
a closure covers both carriageways only when both are listed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

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
ALL_VARIANTS = {"q10": ["A X"], "q50": ["A X"], "q90": ["A X"]}


def _vehicles_xml(routes):
    return "<routes>" + "".join(
        f'<vehicle id="v{n}" depart="{n}.00"><route edges="{route}"/>'
        "</vehicle>" for n, route in enumerate(routes)) + "</routes>"


def _catalog(root: Path, key: str, routes) -> str:
    """Write a catalog pool in the real layout; return its SHA-256."""
    text = "<routes>" + "".join(
        f'<vehicle id="c{n}" depart="0"><route edges="{route}"/></vehicle>'
        for n, route in enumerate(routes)) + "</routes>"
    path = root / key / "catalog.rou.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _archive(root: Path, name: str, *, variants, pools, work_date=None):
    """``variants``: variant -> routes; ``pools``: pool -> (key, sha256)."""
    archive = root / name
    archive.mkdir(parents=True, exist_ok=True)
    meta = {"demand_build_key": f"key-{name}", "n_variants": 3}
    if work_date:
        meta.update({
            "epoch_sim": f"{work_date}T00:00:00", "n_intervals": 96,
            "demand_spec": {"start_date": work_date, "source": "historical",
                            "days": 1, "begin": "00:00", "end": "24:00",
                            "structural_reference_date": "2025-09-16",
                            "purpose": "standard"}})
    if pools is not None:
        meta["candidate_catalog"] = {
            "keys": {pool: key for pool, (key, _sha) in pools.items()},
            "artifacts": {pool: {"routes_sha256": sha}
                          for pool, (_key, sha) in pools.items()}}
    (archive / "demand_meta.json").write_text(json.dumps(meta),
                                              encoding="utf-8")
    for variant, routes in variants.items():
        (archive / VARIANT_FILENAMES[variant]).write_text(
            _vehicles_xml(routes), encoding="utf-8")
    return effect.ArchiveRef(build_key=f"key-{name}", archive=archive,
                             content_key=f"content-{name}")


@pytest.fixture
def world(tmp_path):
    catalog_root = tmp_path / "catalog"
    pools = {"weekday": ("pool-wd", _catalog(catalog_root, "pool-wd",
                                             ["A X", "A Y"])),
             "weekend": ("pool-we", _catalog(catalog_root, "pool-we",
                                             ["A X"]))}
    refs = [
        _archive(tmp_path, "a1", variants=ALL_VARIANTS, pools=pools),
        _archive(tmp_path, "a2", variants={"q10": ["A Y"], "q50": ["A"],
                                           "q90": ["A X", "A X"]},
                 pools={"weekday": pools["weekday"]}),
    ]
    return SimpleNamespace(catalog_root=catalog_root, pools=pools,
                           refs=refs, root=tmp_path)


def _verdict(world, edge, refs=None, **kwargs):
    refs = world.refs if refs is None else refs
    inventory = effect.build_inventory(refs, [edge, "X", "Y", "Z"],
                                       catalog_root=world.catalog_root)
    return effect.assess(
        edge, inventory, network_edges={"A", "X", "Y", "Z"},
        daily_units={ref.build_key: 65 for ref in refs}, **kwargs)


def _count_stream_reads(monkeypatch):
    calls = []
    original = effect._stream_counts

    def counted(*args):
        calls.append(args[0])
        return original(*args)

    monkeypatch.setattr(effect, "_stream_counts", counted)
    return calls


class TestThePredicate:
    def test_a_carried_edge_passes_the_pre_canary_stage(self, world):
        verdict = _verdict(world, "X")
        assert verdict.pre_canary_eligible is True
        assert verdict.effect_eligible is False
        assert verdict.reasons == (effect.CANARY_NOT_RUN,)
        assert verdict.summary["catalog_routes"] == {"weekday": 1,
                                                     "weekend": 1}
        assert verdict.summary["vehicles"] == {"q10": 1, "q50": 1, "q90": 3}
        assert verdict.summary["archives_with_traffic"] == 2
        assert verdict.summary["daily_units_with_traffic"] == 130

    def test_a_canary_with_effect_completes_eligibility(self, world):
        verdict = _verdict(world, "X", canary=effect.CanaryResult(
            affected_vehicles_total=4, affected_daily_units=1,
            positive_costs=1))
        assert verdict.effect_eligible is True
        assert verdict.reasons == ()

    def test_a_canary_without_effect_names_every_gap(self, world):
        verdict = _verdict(world, "X", canary=effect.CanaryResult(0, 0, 0))
        assert verdict.effect_eligible is False
        assert verdict.reasons == (effect.CANARY_NO_AFFECTED_VEHICLE,
                                   effect.CANARY_NO_AFFECTED_UNIT,
                                   effect.CANARY_NO_POSITIVE_COST)

    def test_an_unused_edge_is_rejected_per_pool_and_variant(self, world):
        verdict = _verdict(world, "Z")
        assert verdict.reasons == (
            "no_catalog_route:weekday", "no_catalog_route:weekend",
            "no_crossing_vehicle:q10", "no_crossing_vehicle:q50",
            "no_crossing_vehicle:q90",
            effect.NO_DAILY_UNIT_WITH_TRAFFIC, effect.CANARY_NOT_RUN)

    def test_every_pool_the_archives_use_needs_a_route(self, world):
        verdict = _verdict(world, "Y")
        assert "no_catalog_route:weekend" in verdict.reasons
        assert "no_catalog_route:weekday" not in verdict.reasons
        assert verdict.pre_canary_eligible is False

    def test_every_variant_needs_a_crossing_vehicle(self, world):
        verdict = _verdict(world, "Y")
        assert verdict.summary["vehicles"] == {"q10": 1, "q50": 0, "q90": 0}
        assert "no_crossing_vehicle:q50" in verdict.reasons
        assert "no_crossing_vehicle:q90" in verdict.reasons

    def test_a_missing_variant_is_a_rejection_not_a_zero(self, world):
        ref = _archive(world.root, "a3", variants={"q10": ["A X"],
                                                   "q50": ["A X"]},
                       pools={"weekday": world.pools["weekday"]})
        verdict = _verdict(world, "X", refs=[ref])
        assert "archive_variant_missing:q90" in verdict.reasons
        assert verdict.pre_canary_eligible is False

    def test_an_unbound_catalog_pool_is_a_rejection(self, world):
        ref = _archive(world.root, "a4", variants=ALL_VARIANTS, pools=None)
        verdict = _verdict(world, "X", refs=[ref])
        assert effect.POOL_UNBOUND in verdict.reasons

    def test_a_missing_or_tampered_pool_file_is_a_rejection(self, world):
        missing = _archive(world.root, "a5", variants=ALL_VARIANTS,
                           pools={"weekday": ("absent", "0" * 64)})
        tampered = _archive(world.root, "a6", variants=ALL_VARIANTS,
                            pools={"weekday": ("pool-wd", "0" * 64)})
        assert "catalog_pool_file_missing:weekday" in _verdict(
            world, "X", refs=[missing]).reasons
        assert "catalog_pool_sha256_mismatch:weekday" in _verdict(
            world, "X", refs=[tampered]).reasons

    def test_one_pool_name_with_two_catalogs_is_a_rejection(self, world):
        other = _archive(world.root, "a7", variants=ALL_VARIANTS,
                         pools={"weekday": world.pools["weekend"]})
        verdict = _verdict(world, "X", refs=[world.refs[0], other])
        assert "catalog_pool_key_conflict:weekday" in verdict.reasons

    def test_pool_names_come_from_metadata_not_file_names(self, world):
        ref = _archive(world.root, "a8", variants=ALL_VARIANTS,
                       pools={"holiday": world.pools["weekend"]})
        verdict = _verdict(world, "X", refs=[ref])
        assert verdict.summary["catalog_routes"] == {"holiday": 1}

    def test_a_resolver_bound_variant_hash_must_match(self, world):
        ref = world.refs[0]
        bound = effect.ArchiveRef(ref.build_key, ref.archive, ref.content_key,
                                  route_sha256={"q50": "0" * 64})
        verdict = _verdict(world, "X", refs=[bound])
        assert "archive_variant_sha256_mismatch:q50" in verdict.reasons

    def test_a_vehicle_without_an_embedded_route_is_a_rejection(self, world):
        ref = _archive(world.root, "a9", variants=ALL_VARIANTS,
                       pools={"weekday": world.pools["weekday"]})
        (ref.archive / VARIANT_FILENAMES["q10"]).write_text(
            '<routes><vehicle id="v" depart="0" route="r1"/></routes>',
            encoding="utf-8")
        verdict = _verdict(world, "X", refs=[ref])
        assert ("archive_variant_vehicle_without_embedded_route:q10"
                in verdict.reasons)

    def test_a_subset_of_build_keys_is_judged_on_its_own(self, world):
        verdict = _verdict(world, "Y", build_keys=["key-a1"])
        assert verdict.summary["archives"] == 1
        assert "no_crossing_vehicle:q10" in verdict.reasons

    def test_each_file_is_read_once_for_any_number_of_edges(
            self, world, monkeypatch):
        calls = _count_stream_reads(monkeypatch)
        edges = [f"E{n}" for n in range(6)] + ["X", "Y"]
        inventory = effect.build_inventory(world.refs, edges,
                                           catalog_root=world.catalog_root)
        assert len(calls) == len(set(calls)) == 3 * 2 + 2
        assert set(inventory.file_reads.values()) == {1}
        assert inventory.vehicles["X"]["q90"] == {"key-a1": 1, "key-a2": 2}
        for edge in edges:
            effect.assess(edge, inventory, network_edges=set(edges),
                          daily_units={"key-a1": 1, "key-a2": 1})
        assert len(calls) == 8, "assessment must not reread the archives"

    def test_the_inventory_binds_content_keys_pools_and_hashes(self, world):
        inventory = effect.build_inventory(world.refs, ["X"],
                                           catalog_root=world.catalog_root)
        record = inventory.to_dict()
        assert record["archives"]["key-a1"]["content_key"] == "content-a1"
        assert record["archives"]["key-a2"]["pools"] == {
            "weekday": {"key": "pool-wd",
                        "declared_sha256": world.pools["weekday"][1]}}
        assert record["catalogs"]["pool-we"]["sha256"] == (
            world.pools["weekend"][1])
        assert all(len(item["sha256"]) == 64 for item in
                   record["archives"]["key-a1"]["variants"].values())


def _screen_edges():
    screen = json.loads(bench.SURVIVABILITY_SCREEN.read_text(encoding="utf-8"))
    return [item["edge_id"] for item in screen["candidate_pool"]["edges"]]


@pytest.fixture
def library(tmp_path, monkeypatch):
    """One synthetic day whose routes use only the measured reverse edge."""
    catalog_root = tmp_path / "catalog"
    pool = ("pool-wd", _catalog(catalog_root, "pool-wd",
                                [f"A {MEASURED_REVERSE}"]))
    runs = tmp_path / "runs"
    archive = _archive(
        runs, "demand-20250916-historical",
        variants={variant: [f"A {MEASURED_REVERSE}"]
                  for variant in VARIANT_FILENAMES},
        pools={"weekday": pool}, work_date="2025-09-16").archive
    monkeypatch.setattr(bench, "_effect_sources", lambda data_root: (
        frozenset(_screen_edges()) | {"A"}, catalog_root))

    def resolve(spec, runs_root, cache=None):
        # Every build key the real resolver derives maps to the one day.
        return {key: {"archive": str(archive)}
                for key in bench._daily_units_by_build_key(spec, runs_root)}
    monkeypatch.setattr(bench, "_resolved_archives_for_spec", resolve)
    return runs


class TestTheTwoLevelsStayApart:
    def test_the_geometric_rule_ranks_the_zero_edge_first(self):
        assert bench.surviving_roads()[0]["edge_id"] == ZERO_EDGE

    def test_the_structural_screen_itself_is_unchanged(self):
        screen = json.loads(bench.SURVIVABILITY_SCREEN.read_text(
            encoding="utf-8"))
        surviving = sorted(
            (item for item in screen["candidate_pool"]["edges"]
             if item["survives_topology"]),
            key=lambda item: (float(item["dist_sensor_m"]),
                              str(item["edge_id"])))
        assert [item["edge_id"] for item in bench.surviving_roads()] == [
            item["edge_id"] for item in surviving[:bench.DISCOVERY_ROAD_LIMIT]]

    def test_general_discovery_still_offers_the_zero_edge(self, library):
        edges = {spec.directed_edges[0]
                 for spec in bench.discovered_specs(library)}
        assert ZERO_EDGE in edges and MEASURED_REVERSE in edges


class TestAutomaticBenchmarkSelection:
    def test_it_never_selects_a_case_without_effect(self, library):
        selection = bench.select_case(library, from_archives=True)
        assert selection["effect_rule"] == bench.EFFECT_SELECTION_RULE
        chosen = selection["selected"]
        assert chosen is not None
        assert chosen["spec"]["directed_edges"] == [MEASURED_REVERSE]
        assert chosen["effect"]["pre_canary_eligible"] is True

    def test_it_records_why_a_structural_case_was_rejected(self, library):
        selection = bench.select_case(library, from_archives=True)
        zero = [item for item in selection["evaluated"]
                if item["spec"]["directed_edges"] == [ZERO_EDGE]]
        assert zero
        for item in zero:
            assert item["structurally_eligible"] is True
            assert item["effect_eligible_pre_canary"] is False
            assert item["eligible"] is False
            assert "no_catalog_route:weekday" in item["effect"]["reasons"]
            assert "no_crossing_vehicle:q50" in item["effect"]["reasons"]

    def test_the_archives_are_read_once_for_every_case(self, library,
                                                       monkeypatch):
        calls = _count_stream_reads(monkeypatch)
        selection = bench.select_case(library, from_archives=True)
        assert len(selection["evaluated"]) > 1
        assert len(calls) == len(set(calls)) == 3 + 1


class TestSubhourAutomaticSelection:
    def test_its_inventory_keeps_only_effect_eligible_cases(
            self, tmp_path, monkeypatch):
        import tools.subhour_cost_ordered_benchmark as subhour

        specs = [_user_spec([ZERO_EDGE]), _user_spec([MEASURED_REVERSE])]
        monkeypatch.setattr(subhour.base, "discovered_specs",
                            lambda runs_root: tuple(specs))
        monkeypatch.setattr(subhour.base, "_structural_profile",
                            lambda spec: {"candidate_count": 6,
                                          "unique_daily_unit_count": 1,
                                          "work_dates": ["2027-07-15"]})
        monkeypatch.setattr(subhour, "_spec_has_metadata_archives",
                            lambda *args: True)
        monkeypatch.setattr(subhour, "_effect_archives",
                            lambda spec, runs_root, manifest: {
                                "k": {"archive": str(tmp_path)}})
        seen = []

        def screen(cases, **_kwargs):
            seen.append([spec.directed_edges for spec, _archives in cases])
            return ({spec.content_key: {
                "pre_canary_eligible": spec.directed_edges == (
                    MEASURED_REVERSE,),
                "reasons": [], "edges": []} for spec, _ in cases}, None)

        monkeypatch.setattr(subhour.base, "effect_screen_cases", screen)
        items = subhour._metadata_inventory(tmp_path)
        assert seen == [[(ZERO_EDGE,), (MEASURED_REVERSE,)]], (
            "one screen call covers every case")
        assert [item["spec"]["directed_edges"] for item in items] == [
            [MEASURED_REVERSE]]
        assert items[0]["effect_eligible_pre_canary"] is True


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
        routes.write_text(_vehicles_xml([f"A {MEASURED_REVERSE}"] * 3),
                          encoding="utf-8")
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
