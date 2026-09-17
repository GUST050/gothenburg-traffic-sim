"""WindowCostIndex raw phase: one archive at a time, result-neutral.

The retain loop parsed and indexed every archive before the unit loop, which
is modelled at 19.8-21.5 GB for the 30-archive month on a 24 GiB machine.
The production raw phase now streams: it resolves and validates every build
key once into small descriptors, then opens one archive at a time, computes
all of that archive's daily units and releases the archive before the next
one is opened. The retain loop stays only as a test oracle.

These tests use the real ``ArchiveDisruptionProvider``, the real route
parser and the real window-cost index over small route files; only network
loading and archive resolution are replaced by in-memory doubles.
"""
from __future__ import annotations

import hashlib
import json
import weakref
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import build_window_cost_index as builder
from traffic_sim.core.contracts import (
    ClosureSchedule,
    ClosureSearchSpec,
    DailyTimeBand,
)
from traffic_sim.simulation import deterministic_disruption as dd
from traffic_sim.simulation.disruption import closure_disruption
from traffic_sim.simulation.window_cost_index import (
    WindowCostIndex,
    WindowCostIndexError,
    write_index,
)

CLOSED = "edge-x"
DATES = ("2027-07-15", "2027-07-22", "2027-07-29")
ADJACENCY = {"edge-a": ["edge-x", "edge-y"], "edge-x": ["edge-b", "edge-d"],
             "edge-y": ["edge-b"], "edge-b": [], "edge-c": ["edge-x"],
             "edge-d": []}
#: detour via edge-y, severed destination edge-d, denied departure on edge-x
ROUTES = (("edge-a edge-x edge-b", 22000), ("edge-c edge-x edge-d", 22500),
          ("edge-x edge-b", 23000), ("edge-a edge-y edge-b", 22000))


def _spec(end=DATES[-1]):
    return ClosureSearchSpec(
        search_id="wci-streaming", directed_edges=(CLOSED,),
        demand_build_id="forecast-2027", source="forecast",
        permitted_date_start=DATES[0], permitted_date_end=end,
        required_work_minutes=60, max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("06:00", "08:00"),
        allowed_weekdays=(3,), interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        objective_profile="displaced_vehicles_and_detour_v1")


class _Network:
    """Stands in for the SUMO network; the costing code is real."""

    def __init__(self):
        self.adjacency = ADJACENCY
        self.edge_time = {edge: 10.0 for edge in ADJACENCY}
        self.edge_time["edge-y"] = 30.0
        self.edge_len = {edge: 100.0 for edge in ADJACENCY}
        self.destination_access = None

    def identity(self):
        return {"schema": "test-network-v1"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_archive(root: Path, work_date: str, scale: int) -> Path:
    archive = root / f"demand-{work_date}"
    archive.mkdir(parents=True)
    (archive / "demand_meta.json").write_text(json.dumps({
        "epoch_sim": f"{work_date}T00:00:00", "n_intervals": 96}),
        encoding="utf-8")
    for offset, name in enumerate(sorted(dd.VARIANT_FILENAMES.values())):
        vehicles = [
            f'<vehicle id="v{n}" depart="{depart + offset}">'
            f'<route edges="{route}"/></vehicle>'
            for n, (route, depart) in enumerate(ROUTES * scale)]
        (archive / name).write_text(
            "<routes>" + "".join(vehicles) + "</routes>", encoding="utf-8")
    return archive


class _Oracle:
    """Independent per-file oracle, keyed by the provider identity."""

    def __init__(self, spec):
        self.spec = spec
        self.network = _Network()

    def load(self, identity):
        schedule = ClosureSchedule.from_dict(identity["schedule"])
        archive = Path(identity["demand"]["archive"])
        inputs = dd.ArchiveInputs.from_archive(archive)
        closures = dd.closure_seconds(self.spec, schedule, epoch=inputs.epoch,
                                      duration_s=inputs.duration_s)
        return tuple({
            "demand_variant": variant,
            **closure_disruption(
                inputs.variant_paths[variant], {CLOSED}, closures,
                self.network.edge_time, self.network.edge_len,
                adjacency=self.network.adjacency)}
            for variant in ("q10", "q50", "q90"))


@pytest.fixture
def world(tmp_path, monkeypatch):
    spec = _spec()
    archives = {f"key-{day}": _write_archive(tmp_path / "runs", day, n + 1)
                for n, day in enumerate(DATES)}
    calls = SimpleNamespace(index=0, validate=[], outputs={})

    class Resolver:
        def __init__(self, *_args, **_kwargs):
            pass

        def _required(self, schedule):
            return SimpleNamespace(build_key=f"key-{schedule.first_work_date}")

    def archive_index(_runs_root):
        calls.index += 1
        return {"index": calls.index}

    def find(_runs_root, required, *, qualified_manifest, _archive_index):
        assert _archive_index == {"index": calls.index}, (
            "one archive index per operation")
        assert qualified_manifest == {"status": "PASS"}
        calls.validate.append(required.build_key)
        archive = archives[required.build_key]
        outputs = calls.outputs.get(required.build_key) or [
            {"name": path.name, "sha256": _sha(path),
             "bytes": path.stat().st_size}
            for path in sorted(archive.iterdir())]
        return [{"archive": str(archive), "outputs": outputs,
                 "archive_content_key": f"content-{required.build_key}"}]

    units = sum(1 for parent in builder.iter_closure_schedules(spec)
                for _ in builder.daily_unit_records(spec, parent))
    monkeypatch.setattr(builder, "MonthlyDemandResolverRunner", Resolver)
    monkeypatch.setattr(builder, "_archives_for_build_key", archive_index)
    monkeypatch.setattr(builder, "find_demand_archives", find)
    monkeypatch.setattr(builder, "NetworkCostModel", _Network)
    monkeypatch.setattr(builder, "EXPECTED_DAILY_UNITS", units)
    return SimpleNamespace(spec=spec, archives=archives, calls=calls,
                           runs=tmp_path / "runs", units=units)


def _run(world, raw=None):
    raw = raw or builder._raw_index_records
    return raw(world.spec, runs_root=world.runs,
               oracle_cache=_Oracle(world.spec),
               qualified_demand_manifest={"status": "PASS"})


def _bytes(value):
    return json.dumps(value, default=str).encode("utf-8")


class TestResultNeutrality:
    def test_stream_and_retain_are_byte_identical_on_a_nonzero_case(
            self, world):
        retained = _run(world, builder._retain_index_records)
        streamed = _run(world)
        assert _bytes(streamed[0]) == _bytes(retained[0])
        assert _bytes(streamed[1]) == _bytes(retained[1])
        assert _bytes(streamed[2]["provider_identities"]) == _bytes(
            retained[2]["provider_identities"])
        assert sum(item["vehicles_affected"]
                   for unit in streamed[0].values()
                   for item in unit["records"]) > 0

    def test_detour_severed_and_denied_departure_are_identical(self, world):
        retained = _run(world, builder._retain_index_records)[0]
        streamed = _run(world)[0]
        fields = ("added_vehicle_hours", "vehicles_severed_destination",
                  "vehicles_denied_departure")
        for field in fields:
            totals = [sum(item[field] for unit in records.values()
                          for item in unit["records"])
                      for records in (retained, streamed)]
            assert totals[0] == totals[1] and totals[0] > 0, field
        for unit_id, unit in streamed.items():
            assert unit == retained[unit_id]

    def test_indexed_records_match_the_independent_oracle(self, world):
        indexed, oracle, _measurement = _run(world)
        comparison = WindowCostIndex(
            bound_identity={"schema": "test"}, records=indexed,
            preparation_time_s=0.0).compare_oracle(oracle)
        assert comparison["oracle_complete"] and comparison["field_identical"]

    def test_the_order_does_not_depend_on_how_build_keys_arrive(
            self, world, monkeypatch):
        expected = _run(world)
        production = builder.daily_unit_records
        parents = builder.iter_closure_schedules
        monkeypatch.setattr(builder, "iter_closure_schedules",
                            lambda spec: tuple(reversed(tuple(parents(spec)))))
        monkeypatch.setattr(
            builder, "daily_unit_records",
            lambda spec, parent: tuple(reversed(tuple(
                production(spec, parent)))))
        shuffled = _run(world)
        assert _bytes(shuffled[0]) == _bytes(expected[0])
        assert _bytes(shuffled[1]) == _bytes(expected[1])
        assert _bytes(shuffled[2]["provider_identities"]) == _bytes(
            expected[2]["provider_identities"])

    def test_the_retain_loop_is_only_a_test_oracle(self, world):
        assert _run(world)[2]["raw_loop"] == "stream_one_archive_at_a_time"
        assert _run(world, builder._retain_index_records)[2]["raw_loop"] == (
            "retain_all_archives")


class TestOnePassPerArchive:
    def test_one_index_build_and_one_validation_per_build_key(self, world):
        _run(world)
        assert world.calls.index == 1
        assert sorted(world.calls.validate) == sorted(world.archives)

    def test_each_variant_is_parsed_at_most_once(self, world, monkeypatch):
        parsed = []
        production = builder.parse_route_vehicles

        def spy(path, **kwargs):
            parsed.append(Path(path))
            return production(path, **kwargs)

        monkeypatch.setattr(builder, "parse_route_vehicles", spy)
        _run(world)
        assert len(parsed) == len(set(parsed)) == 3 * len(world.archives)

    def test_an_archive_is_released_before_the_next_is_opened(
            self, world, monkeypatch):
        """Weak references, no gc.collect: reference counting must free it."""
        alive: dict[str, list] = {}
        production_parse = builder.parse_route_vehicles
        production_index = builder.build_parsed_window_cost_index
        production_provider = builder.ArchiveDisruptionProvider
        owner_of: dict[int, str] = {}

        class Holder(list):
            """Weak-referenceable owner of one parsed file."""

        def check(archive):
            leaked = sorted({name for name, refs in alive.items()
                             if name != archive
                             for ref in refs if ref() is not None})
            assert not leaked, f"objects of {leaked} are still held"

        def track(archive, value):
            alive.setdefault(archive, []).append(weakref.ref(value))
            owner_of[id(value)] = archive
            return value

        def parse(path, **kwargs):
            archive = Path(path).parent.name
            check(archive)
            return track(archive, Holder(production_parse(path, **kwargs)))

        def index(parsed, *args, adjacency, **kwargs):
            return track(owner_of[id(parsed)], production_index(
                parsed, *args, adjacency=adjacency, **kwargs))

        class Provider(production_provider):
            def __init__(self, spec, *, archive, **kwargs):
                check(Path(archive).name)
                super().__init__(spec, archive=archive, **kwargs)
                track(Path(archive).name, self)

        monkeypatch.setattr(builder, "parse_route_vehicles", parse)
        monkeypatch.setattr(builder, "build_parsed_window_cost_index", index)
        monkeypatch.setattr(builder, "ArchiveDisruptionProvider", Provider)
        _run(world)
        assert len(alive) == len(world.archives)
        assert all(ref() is None for refs in alive.values()
                   for ref in refs), "the last archive is released too"


def _small_index():
    from tests.test_window_cost_index import _records

    return WindowCostIndex(bound_identity={"schema": "test"},
                           records=_records(), preparation_time_s=0.0)


class TestFailClosed:
    def test_archive_drift_after_validation_is_refused(self, world):
        key = sorted(world.archives)[1]
        archive = world.archives[key]
        world.calls.outputs[key] = [
            {"name": path.name, "sha256": "0" * 64,
             "bytes": path.stat().st_size}
            for path in sorted(archive.iterdir())]
        with pytest.raises(WindowCostIndexError, match=key) as caught:
            _run(world)
        assert "open" in str(caught.value)

    def test_a_validation_failure_names_the_build_key(self, world,
                                                      monkeypatch):
        cause = ValueError("qualified_manifest_archive_mismatch")
        production = builder.find_demand_archives
        failing = sorted(world.archives)[2]

        def find(runs_root, required, **kwargs):
            if required.build_key == failing:
                raise cause
            return production(runs_root, required, **kwargs)

        monkeypatch.setattr(builder, "find_demand_archives", find)
        with pytest.raises(WindowCostIndexError) as caught:
            _run(world)
        assert failing in str(caught.value)
        assert "validate" in str(caught.value)
        assert caught.value.__cause__ is cause

    def test_a_parser_error_names_the_archive(self, world):
        key = sorted(world.archives)[1]
        (world.archives[key] / dd.VARIANT_FILENAMES["q90"]).write_text(
            "<routes><vehicle", encoding="utf-8")
        with pytest.raises(WindowCostIndexError) as caught:
            _run(world)
        assert key in str(caught.value) and "parse" in str(caught.value)
        assert caught.value.__cause__ is not None

    def test_costing_source_drift_during_the_operation_is_refused(
            self, world, monkeypatch):
        production = dd.costing_source_identity
        calls = []

        def drifting():
            calls.append(1)
            identity = dict(production())
            if len(calls) > 1:
                identity["run_scenario.py"] = "f" * 64
            return identity

        monkeypatch.setattr(dd, "costing_source_identity", drifting)
        with pytest.raises(WindowCostIndexError, match="source"):
            _run(world)

    def test_a_failed_index_write_leaves_no_file(self, tmp_path,
                                                 monkeypatch):
        import os

        index = _small_index()
        path = tmp_path / "index" / "window-cost-index.json"

        def broken(_fd):
            raise OSError("disk full")

        monkeypatch.setattr(os, "fsync", broken)
        with pytest.raises(OSError, match="disk full"):
            write_index(path, index)
        assert not path.exists()
        assert list(path.parent.iterdir()) == []

    def test_an_existing_index_is_never_replaced(self, tmp_path):
        index = _small_index()
        path = tmp_path / "window-cost-index.json"
        path.write_text("previous", encoding="utf-8")
        with pytest.raises(FileExistsError):
            write_index(path, index)
        assert path.read_text(encoding="utf-8") == "previous"
        assert [item.name for item in tmp_path.iterdir()] == [path.name]

    def test_a_raw_phase_failure_publishes_no_index(self, tmp_path,
                                                    monkeypatch):
        from tests.test_window_cost_index import _build_path_fixture

        profile_path, *_rest = _build_path_fixture(
            tmp_path, monkeypatch, baseline_time_s=1.0)

        def failing(*_args, **_kwargs):
            raise WindowCostIndexError("build key k: parse failed")

        monkeypatch.setattr(builder, "_raw_index_records", failing)
        index_out = tmp_path / "index-failed"
        evidence_out = tmp_path / "failed.json"
        with pytest.raises(WindowCostIndexError, match="parse"):
            builder.build_from_profile(
                profile_path, index_out=index_out,
                evidence_out=evidence_out, evidence_id="failed")
        assert not index_out.exists() and not evidence_out.exists()


def test_the_fixture_closes_the_edge_its_routes_cross():
    spec = _spec(end=DATES[0])
    parent = next(iter(builder.iter_closure_schedules(spec)))
    schedule = next(iter(builder.daily_unit_records(spec, parent)))[2]()
    closures = dd.closure_seconds(
        spec, schedule, epoch=datetime(2027, 7, 15), duration_s=96 * 900)
    assert closures and all(item["edge_id"] == CLOSED for item in closures)


# --------------------------------------------------------------------------
# Review of f5608a7..d7983b8: publication must be atomic and drift-checked.
# --------------------------------------------------------------------------

class TestPublication:
    def test_evidence_publication_is_atomic_and_never_replaces(
            self, tmp_path, monkeypatch):
        import os

        path = tmp_path / "evidence.json"

        def broken(_fd):
            raise OSError("disk full")

        monkeypatch.setattr(os, "fsync", broken)
        with pytest.raises(OSError, match="disk full"):
            builder._publish(path, {"status": "PASS"})
        assert list(tmp_path.iterdir()) == []
        monkeypatch.undo()
        path.write_text("previous", encoding="utf-8")
        with pytest.raises(FileExistsError):
            builder._publish(path, {"status": "PASS"})
        assert path.read_text(encoding="utf-8") == "previous"
        assert [item.name for item in tmp_path.iterdir()] == [path.name]

    def test_the_directory_entry_is_made_durable(self, tmp_path,
                                                 monkeypatch):
        import os
        import stat

        synced = []
        production = os.fsync

        def recording(fd):
            synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            return production(fd)

        monkeypatch.setattr(os, "fsync", recording)
        write_index(tmp_path / "window-cost-index.json", _small_index())
        builder._publish(tmp_path / "evidence.json", {"status": "PASS"})
        assert synced == [False, True, False, True]

    def test_the_raw_phase_binds_what_it_read(self, world):
        measurement = _run(world)[2]
        assert measurement["costing_sources"] == dict(
            dd.costing_source_identity())
        bindings = measurement["archive_bindings"]
        assert sorted(bindings) == sorted(world.archives)
        for key, binding in bindings.items():
            archive = world.archives[key]
            assert binding["archive"] == str(archive.resolve())
            assert binding["content_key"] == f"content-{key}"
            assert binding["files"] == {
                name: _sha(archive / name)
                for name in ["demand_meta.json",
                             *sorted(dd.VARIANT_FILENAMES.values())]}

    def test_archive_drift_before_publication_is_refused(self, world):
        measurement = _run(world)[2]
        builder._verify_raw_inputs_unchanged(measurement)
        key = sorted(world.archives)[0]
        (world.archives[key] / dd.VARIANT_FILENAMES["q50"]).write_text(
            "<routes/>", encoding="utf-8")
        with pytest.raises(WindowCostIndexError, match=f"{key}.*publication"):
            builder._verify_raw_inputs_unchanged(measurement)

    def test_source_drift_before_publication_is_refused(
            self, world, monkeypatch):
        measurement = _run(world)[2]
        production = dd.costing_source_identity
        monkeypatch.setattr(dd, "costing_source_identity", lambda: {
            **production(), "run_scenario.py": "f" * 64})
        with pytest.raises(WindowCostIndexError, match="source"):
            builder._verify_raw_inputs_unchanged(measurement)

    def test_publication_is_preceded_by_a_drift_check(self, tmp_path,
                                                      monkeypatch):
        from tests.test_window_cost_index import _build_path_fixture

        profile_path, *_rest = _build_path_fixture(
            tmp_path, monkeypatch, baseline_time_s=1.0)
        events = []
        production_write = builder.write_index
        production_publish = builder._publish
        monkeypatch.setattr(
            builder, "_verify_before_publication",
            lambda *args, **kwargs: events.append("verify"))
        monkeypatch.setattr(builder, "write_index", lambda *args: (
            events.append("write_index"), production_write(*args))[1])
        monkeypatch.setattr(builder, "_publish", lambda *args: (
            events.append("publish"), production_publish(*args))[1])
        builder.build_from_profile(
            profile_path, index_out=tmp_path / "index",
            evidence_out=tmp_path / "evidence.json", evidence_id="order")
        assert events == ["verify", "write_index", "verify", "publish"]

    def test_drift_before_publication_leaves_no_index_or_evidence(
            self, tmp_path, monkeypatch):
        from tests.test_window_cost_index import _build_path_fixture

        profile_path, *_rest = _build_path_fixture(
            tmp_path, monkeypatch, baseline_time_s=1.0)

        def drifted(*_args, **_kwargs):
            raise WindowCostIndexError("archive drifted before publication")

        monkeypatch.setattr(builder, "_verify_before_publication", drifted)
        index_out = tmp_path / "index"
        evidence_out = tmp_path / "evidence.json"
        with pytest.raises(WindowCostIndexError, match="publication"):
            builder.build_from_profile(
                profile_path, index_out=index_out,
                evidence_out=evidence_out, evidence_id="drift")
        assert not evidence_out.exists()
        assert not (index_out / "window-cost-index.json").exists()
