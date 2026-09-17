"""Retain and stream give byte-identical WindowCostIndex output WITH crossings.

The real September archives put no vehicle on the frozen spec's closure
edge, so a retain/stream comparison on them only exercises the empty path.
This fixture has crossing routes with a bypass, a severed destination and a
denied departure, and checks both modes of the diagnostic driver against
the independent per-file computation that produced the production oracle.
"""
from __future__ import annotations

import importlib.util
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest

from traffic_sim.simulation.disruption import (
    build_parsed_window_cost_index,
    closure_disruption,
    parse_route_vehicles,
)

DRIVER = (Path(__file__).resolve().parents[1]
          / "validation/benchmarks/wci_retain_stream_memory_v1.py")
CLOSED = "B"
ADJACENCY = {"A": ["B", "D"], "B": ["C"], "C": [], "D": ["C"],
             "E": ["B"]}
EDGE_TIME = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 5.0, "E": 1.0}
EDGE_LEN = {"A": 10.0, "B": 10.0, "C": 10.0, "D": 50.0, "E": 10.0}
# (route, depart): A-B-C has a bypass via D, E-B-C has none (severed),
# B-C starts on the closed edge (denied departure), A-D-C never crosses.
VEHICLES = {
    "archive-1": {
        "q10": [("A B C", 10.0), ("A D C", 20.0), ("E B C", 30.0)],
        "q50": [("A B C", 10.0), ("A B C", 150.0), ("B C", 40.0),
                ("A D C", 60.0)],
        "q90": [("A B C", 5.0), ("E B C", 120.0), ("B C", 130.0)],
    },
    "archive-2": {
        "q10": [("A B C", 110.0), ("A D C", 10.0)],
        "q50": [("E B C", 15.0), ("A B C", 105.0), ("A B C", 5000.0)],
        "q90": [("B C", 90.0), ("A B C", 95.0), ("A D C", 99.0)],
    },
}
WINDOWS = {"early": (0.0, 100.0), "late": (100.0, 200.0),
           "night": (9000.0, 9900.0)}


def _driver():
    spec = importlib.util.spec_from_file_location(
        "wci_retain_stream_memory_v1", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Schedule:
    def __init__(self, schedule_id, begin_s, end_s):
        self.schedule_id = schedule_id
        self.begin_s, self.end_s = begin_s, end_s

    def to_dict(self):
        return {"schedule_id": self.schedule_id,
                "begin_s": self.begin_s, "end_s": self.end_s}


class _Provider:
    def __init__(self, name, paths):
        self.name = name
        self.inputs = SimpleNamespace(variant_paths=dict(paths), epoch=None,
                                      duration_s=None)

    def identity(self):
        return {"archive": self.name,
                "variants": {variant: path.name for variant, path
                             in sorted(self.inputs.variant_paths.items())}}

    def cache_identity(self, schedule):
        return {**self.identity(), "schedule": schedule.to_dict()}


def _closures(begin_s, end_s):
    return [{"edge_id": CLOSED, "begin_s": begin_s, "end_s": end_s}]


@pytest.fixture
def world(tmp_path):
    paths = {}
    for archive, variants in VEHICLES.items():
        root = tmp_path / archive
        root.mkdir()
        paths[archive] = {}
        for variant, vehicles in variants.items():
            rows = "".join(
                f'<vehicle id="v{n}" depart="{depart:.2f}">'
                f'<route edges="{edges}"/></vehicle>'
                for n, (edges, depart) in enumerate(vehicles))
            path = root / f"{variant}.rou.xml"
            path.write_text(f"<routes>{rows}</routes>", encoding="utf-8")
            paths[archive][variant] = path
    units = {archive: {f"{archive}:{name}": _Schedule(
                 f"{archive}-{name}", *window)
             for name, window in WINDOWS.items()}
             for archive in VEHICLES}

    def oracle_load(identity):
        schedule = identity["schedule"]
        return tuple(
            {"demand_variant": variant,
             **closure_disruption(
                 path, {CLOSED},
                 _closures(schedule["begin_s"], schedule["end_s"]),
                 EDGE_TIME, EDGE_LEN, adjacency=ADJACENCY)}
            for variant, path in sorted(paths[identity["archive"]].items()))

    return SimpleNamespace(paths=paths, units=units, oracle_load=oracle_load)


def _compute(driver, mode, world, *, make_provider=None, build_index=None):
    return driver.compute_units(
        mode, list(VEHICLES), world.units,
        make_provider=make_provider or (
            lambda archive: _Provider(archive, world.paths[archive])),
        parse_routes=parse_route_vehicles,
        build_index=build_index or (
            lambda parsed: build_parsed_window_cost_index(
                parsed, {CLOSED}, EDGE_TIME, EDGE_LEN,
                adjacency=ADJACENCY)),
        closures_for=lambda schedule, _inputs: _closures(
            schedule.begin_s, schedule.end_s),
        oracle_load=world.oracle_load)


def _records(result):
    return [record for unit in result["indexed"].values()
            for record in unit["records"]]


def test_retain_and_stream_are_byte_identical_with_crossing_traffic(world):
    driver = _driver()
    common = driver.common

    retained = _compute(driver, "retain", world)
    streamed = _compute(driver, "stream", world)

    assert driver.unit_digests(retained) == driver.unit_digests(streamed)
    for key in ("indexed", "oracle", "provider_identities", "archive_facts"):
        assert (common.canonical_bytes(retained[key])
                == common.canonical_bytes(streamed[key]))
    # The comparison is only meaningful because the fixture is not empty.
    records = _records(retained)
    crossing_in_fixture = sum(
        1 for variants in VEHICLES.values() for vehicles in variants.values()
        for edges, _depart in vehicles if CLOSED in edges.split())
    assert crossing_in_fixture > 0
    assert sum(sum(facts["crossing_vehicles"].values())
               for facts in retained["archive_facts"].values()
               ) == crossing_in_fixture
    assert sum(r["vehicles_affected"] for r in records) > 0
    assert sum(r["vehicles_no_detour"] for r in records) > 0
    assert sum(r["vehicles_denied_departure"] for r in records) > 0
    assert sum(r["added_metres_total"] for r in records) > 0


def test_both_modes_equal_the_independent_per_file_oracle(world):
    driver = _driver()
    for mode in driver.MODES:
        result = _compute(driver, mode, world)
        assert result["indexed"] == result["oracle"], mode
        by_unit = {unit: [r["vehicles_affected"] for r in value["records"]]
                   for unit, value in result["indexed"].items()}
        # A window that overlaps no crossing prices nothing.
        assert by_unit["archive-1:night"] == [0, 0, 0]
        assert any(sum(counts) for counts in by_unit.values())


def test_stream_releases_each_archive_before_opening_the_next(world):
    driver = _driver()
    for mode, expect_alive in (("retain", True), ("stream", False)):
        providers, indexes, observed = [], [], []

        def make_provider(archive):
            observed.append((any(ref() is not None for ref in providers),
                             any(ref() is not None for ref in indexes)))
            provider = _Provider(archive, world.paths[archive])
            providers.append(weakref.ref(provider))
            return provider

        def build_index(parsed):
            index = build_parsed_window_cost_index(
                parsed, {CLOSED}, EDGE_TIME, EDGE_LEN, adjacency=ADJACENCY)
            indexes.append(weakref.ref(index))
            return index

        _compute(driver, mode, world, make_provider=make_provider,
                 build_index=build_index)
        # The first archive opens with nothing held; the second shows
        # whether the first one's provider and indexes were released.
        assert observed[0] == (False, False)
        assert observed[1] == (expect_alive, expect_alive), mode


def test_a_missing_oracle_record_fails_closed(world):
    driver = _driver()
    world.oracle_load = lambda identity: None
    for mode in driver.MODES:
        with pytest.raises(RuntimeError, match="oracle is missing"):
            _compute(driver, mode, world)


def test_an_unknown_mode_is_rejected(world):
    driver = _driver()
    with pytest.raises(ValueError, match="unknown mode"):
        _compute(driver, "partial", world)


def test_unavailable_system_telemetry_is_reported_not_raised(monkeypatch):
    """A sandboxed sysctl must not suppress the scientific result."""
    common = _driver().common

    def unavailable(name):
        raise OSError(f"blocked: {name}")

    monkeypatch.setattr(common, "_sysctl", unavailable)
    monkeypatch.setattr(common, "_vm_stat",
                        lambda: (_ for _ in ()).throw(OSError("blocked")))

    memory = common.system_memory()
    runtime = common.runtime_manifest()

    assert memory["telemetry_complete"] is False
    assert memory["swap_used_mb"] is None
    assert memory["compressor_occupied_bytes"] is None
    assert memory["telemetry_errors"]
    assert runtime["hw_memsize_bytes"] is None
    assert runtime["hw_ncpu"] == common.os.cpu_count()
    assert runtime["telemetry_errors"]
