"""The independent daily-cost cache: one build key at a time, resumable.

The fixtures are small real archives and catalogs, priced by the production
per-file provider, so reuse, drift and resume are decided by bytes. Nothing
here reads a WindowCostIndex: the cache must stay an independent oracle for
the index it will later be compared against.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.build_daily_cost_cache as builder
import tools.closure_effect_eligibility as cee
from tests.test_closure_effect_eligibility import _pool
from tests.test_wci_streaming import (
    CLOSED,
    DATES,
    _Network,
    _sha,
    _spec,
    _write_archive,
)
from traffic_sim.simulation import deterministic_disruption as dd

ROOT = Path(__file__).resolve().parents[1]
KEY_A, KEY_B = "key-a", "key-b"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two build keys, each with its own small archive and the catalogs."""
    catalog_root = tmp_path / "catalog"
    pools = {"weekday": _pool(catalog_root, "pool-wd", [f"A {CLOSED} B"]),
             "weekend": _pool(catalog_root, "pool-we", [f"A {CLOSED} B"])}
    archives = {KEY_A: _write_archive(tmp_path / "runs", DATES[0], 1),
                KEY_B: _write_archive(tmp_path / "runs", DATES[1], 1)}
    spec = _spec(end=DATES[1])
    registration = {
        "content_key": "r" * 64,
        "case_selection_policy": cee.POLICY,
        "chosen_edge": CLOSED,
        "spec": spec.to_dict(),
        "spec_content_key": spec.content_key,
        "archives": {
            key: {"archive": str(path),
                  "archive_content_key": f"content-{key}",
                  "demand_meta_sha256": _sha(path / "demand_meta.json"),
                  "variants": {
                      "q10": _sha(path / dd.VARIANT_FILENAMES["q10"]),
                      "q50": _sha(path / dd.VARIANT_FILENAMES["q50"]),
                      "q90": _sha(path / dd.VARIANT_FILENAMES["q90"])}}
            for key, path in archives.items()},
        "catalogs": {
            pool: {"catalog_key": key,
                   "path": str(catalog_root / key / "catalog.rou.xml"),
                   "routes_sha256": routes, "metadata_sha256": metadata}
            for pool, (key, routes, metadata) in pools.items()},
    }
    network = _Network()

    def units_of(spec_arg, _runs_root, _manifest):
        grouped = {}
        for parent in builder.iter_closure_schedules(spec_arg):
            for unit_id, identity, build in builder.daily_unit_records(
                    spec_arg, parent):
                schedule = build()
                key = KEY_A if schedule.first_work_date == DATES[0] else KEY_B
                if any(str(unit_id) == str(item[0])
                       for item in grouped.get(key, ())):
                    continue
                grouped.setdefault(key, []).append(
                    (str(unit_id), identity, schedule))
        return grouped

    monkeypatch.setattr(builder, "units_of", units_of)
    monkeypatch.setattr(builder, "NetworkCostModel", lambda: network)
    return SimpleNamespace(registration=registration, archives=archives,
                           catalogs=catalog_root, spec=spec,
                           cache=tmp_path / "cache", network=network,
                           status_path=tmp_path / "status.json",
                           units=units_of(spec, None, None))


def _status(world, path=None):
    return builder.Status(path or world.status_path,
                          requested=[KEY_A, KEY_B], cache_root=world.cache,
                          registration=world.registration)


def _run(world, keys=(KEY_A,), status=None):
    return builder.run(world.registration, build_keys=list(keys),
                       cache_root=world.cache, runs_root=Path("unused"),
                       qualified_manifest={}, network=world.network,
                       status=status or _status(world))


def _cost_calls(monkeypatch):
    """Count real cost computations, not cache reads."""
    calls = []
    production = dd.ArchiveDisruptionProvider._compute

    def spy(self, schedule):
        calls.append(schedule.schedule_id)
        return production(self, schedule)

    monkeypatch.setattr(dd.ArchiveDisruptionProvider, "_compute", spy)
    return calls


class TestOneCompleteBatch:
    def test_a_batch_prices_and_verifies_every_unit(self, world, monkeypatch):
        calls = _cost_calls(monkeypatch)
        result = _run(world)[KEY_A]
        expected = len(world.units[KEY_A])
        assert expected >= 4, "the fixture must have several daily units"
        assert result["daily_units"] == expected == len(calls)
        marker = json.loads(builder.marker_path(world.cache, KEY_A)
                            .read_text(encoding="utf-8"))
        assert marker["daily_units"] == expected == len(marker["units"])
        assert all(item["cache_key"] and item["digest"]
                   for item in marker["units"])
        assert builder.verify_batch(
            world.cache, builder.identity_of(world.registration, KEY_A),
            world.units[KEY_A]) == []

    def test_the_second_run_reuses_every_unit_without_computing(
            self, world, monkeypatch):
        _run(world)
        calls = _cost_calls(monkeypatch)
        status = _status(world)
        result = _run(world, status=status)[KEY_A]
        assert result == {"reused": True,
                          "daily_units": len(world.units[KEY_A])}
        assert calls == [], "a verified batch is never recomputed"
        assert status.state["cache_hits"] == len(world.units[KEY_A])
        assert status.state["cache_misses"] == 0
        assert status.state["reused_build_keys"] == [KEY_A]

    def test_a_batch_is_published_no_clobber(self, world):
        _run(world)
        with pytest.raises(FileExistsError):
            builder.build_batch(world.registration, KEY_A,
                                cache_root=world.cache,
                                units=world.units[KEY_A],
                                network=world.network)

    def test_the_cached_records_equal_a_fresh_independent_computation(
            self, world):
        _run(world)
        cache = dd.DailyCostCache(world.cache)
        fresh = dd.ArchiveDisruptionProvider(
            world.spec, archive=world.archives[KEY_A],
            network=world.network, cache=None)
        for _unit, _identity, schedule in world.units[KEY_A]:
            stored = cache.load(fresh.cache_identity(schedule))
            assert stored is not None
            assert [dict(item) for item in stored] == [
                dict(item) for item in fresh.disruption(schedule)]

    def test_no_window_cost_index_is_read(self, world, monkeypatch):
        from traffic_sim.simulation import window_cost_index

        def refuse(*_args, **_kwargs):
            raise AssertionError("the oracle must not read an index")

        monkeypatch.setattr(window_cost_index, "load_index", refuse)
        monkeypatch.setattr(window_cost_index, "WindowCostIndex", refuse)
        assert _run(world)[KEY_A]["daily_units"] == len(world.units[KEY_A])

    def test_no_process_is_started_while_pricing(self, world, monkeypatch):
        monkeypatch.setattr(builder, "_swap_used_bytes", lambda: None)

        def refuse(*_args, **_kwargs):
            raise AssertionError("no SUMO or demand build may start")

        monkeypatch.setattr(subprocess, "Popen", refuse)
        assert _run(world)[KEY_A]["daily_units"] == len(world.units[KEY_A])
        assert not list(Path(world.cache).glob("**/demand-*"))


class TestResume:
    @staticmethod
    def _interrupt_after(monkeypatch, stop_after, error):
        production = dd.ArchiveDisruptionProvider.disruption
        seen = []

        def interrupting(self, schedule):
            if len(seen) >= stop_after:
                raise error
            seen.append(schedule.schedule_id)
            return production(self, schedule)

        monkeypatch.setattr(dd.ArchiveDisruptionProvider, "disruption",
                            interrupting)
        return seen

    @pytest.mark.parametrize("error", [
        RuntimeError("child crashed"), KeyboardInterrupt()],
        ids=["crash", "ctrl-c"])
    def test_an_interrupted_batch_publishes_nothing(self, world, monkeypatch,
                                                    error):
        self._interrupt_after(monkeypatch, 2, error)
        with pytest.raises(type(error)):
            _run(world)
        assert not builder.marker_path(world.cache, KEY_A).exists()
        assert builder.verify_batch(
            world.cache, builder.identity_of(world.registration, KEY_A),
            world.units[KEY_A]) == ["no batch marker"]

    def test_resume_recomputes_the_incomplete_build_key(self, world,
                                                        monkeypatch):
        production = dd.ArchiveDisruptionProvider.disruption
        self._interrupt_after(monkeypatch, 2, RuntimeError("child crashed"))
        with pytest.raises(RuntimeError):
            _run(world)
        monkeypatch.setattr(dd.ArchiveDisruptionProvider, "disruption",
                            production)
        calls = _cost_calls(monkeypatch)
        status = _status(world)
        result = _run(world, status=status)[KEY_A]
        assert result["reused"] is False
        assert result["daily_units"] == len(world.units[KEY_A])
        assert len(calls) == len(world.units[KEY_A]) - 2, (
            "units already stored are reused; the missing ones are computed")
        assert status.state["recompute_reasons"][KEY_A] == ["no batch marker"]
        assert builder.verify_batch(
            world.cache, builder.identity_of(world.registration, KEY_A),
            world.units[KEY_A]) == []

    def test_a_status_file_survives_an_interrupt(self, world, monkeypatch):
        self._interrupt_after(monkeypatch, 1, RuntimeError("child crashed"))
        status = _status(world)
        with pytest.raises(RuntimeError):
            _run(world, status=status)
        stored = json.loads(world.status_path.read_text(encoding="utf-8"))
        assert stored["current_build_key"] == KEY_A
        assert stored["phase"].startswith("computing")
        assert stored["completed_build_keys"] == []
        assert stored["daily_units_expected"] == len(world.units[KEY_A])
        assert stored["peak"]["max_rss_bytes"] > 0


class TestInvalidation:
    @staticmethod
    def _verify(world, key=KEY_A):
        return builder.verify_batch(
            world.cache, builder.identity_of(world.registration, key),
            world.units[key])

    def test_a_corrupt_cached_unit_invalidates_the_batch(self, world):
        _run(world)
        marker = json.loads(builder.marker_path(world.cache, KEY_A)
                            .read_text(encoding="utf-8"))
        victim = dd.DailyCostCache(world.cache).path_for(
            marker["units"][0]["cache_key"])
        payload = json.loads(victim.read_text(encoding="utf-8"))
        payload["disruption"][0]["vehicles_affected"] += 1
        victim.write_text(json.dumps(payload), encoding="utf-8")
        assert self._verify(world) == [
            "cached unit does not match the batch: "
            f"{marker['units'][0]['unit_id']}"]

    def test_a_missing_cached_unit_invalidates_the_batch(self, world):
        _run(world)
        marker = json.loads(builder.marker_path(world.cache, KEY_A)
                            .read_text(encoding="utf-8"))
        dd.DailyCostCache(world.cache).path_for(
            marker["units"][0]["cache_key"]).unlink()
        assert self._verify(world)[0].startswith("missing cached unit")

    def test_archive_drift_invalidates_only_its_own_batch(self, world):
        _run(world, keys=(KEY_A, KEY_B))
        assert self._verify(world, KEY_A) == []
        assert self._verify(world, KEY_B) == []
        (world.archives[KEY_A] / dd.VARIANT_FILENAMES["q50"]).write_text(
            "<routes/>", encoding="utf-8")
        assert self._verify(world, KEY_A) == [
            "archive file changed: calibrated.rou.xml"]
        assert self._verify(world, KEY_B) == [], (
            "another build key is untouched by local drift")

    def test_catalog_drift_invalidates_the_batch(self, world):
        _run(world)
        catalog = Path(world.registration["catalogs"]["weekday"]["path"])
        catalog.write_text("<routes/>", encoding="utf-8")
        assert self._verify(world) == ["catalog changed: weekday"]

    def test_source_drift_invalidates_the_batch(self, world, monkeypatch):
        _run(world)
        identity = builder.identity_of(world.registration, KEY_A)
        monkeypatch.setattr(builder, "costing_source_identity",
                            lambda: {"run_scenario.py": "0" * 64})
        assert builder.content_drift(identity) == ["costing source changed"]
        assert self._verify(world) == ["batch identity changed"], (
            "the batch is refused either way")

    def test_tool_drift_invalidates_the_batch(self, world):
        _run(world)
        drifted = builder.dataclasses.replace(
            builder.identity_of(world.registration, KEY_A),
            tool_sources={"tools/build_daily_cost_cache.py": "0" * 64})
        assert builder.content_drift(drifted) == [
            "cache builder source changed"]

    @pytest.mark.parametrize("field,value", [
        ("case_selection_policy", "structural_survivability_v1"),
        ("chosen_edge", "other-edge"),
        ("content_key", "z" * 64),
    ])
    def test_registration_drift_invalidates_the_batch(self, world, field,
                                                      value):
        _run(world)
        world.registration[field] = value
        assert self._verify(world) == ["batch identity changed"]


class TestSingleFlight:
    def test_two_publishers_cannot_share_one_build_key(self, world):
        with builder.BatchLock(world.cache, KEY_A) as acquired:
            assert acquired
            status = _status(world)
            assert _run(world, status=status)[KEY_A] == {"reused": False,
                                                         "busy": True}
            assert status.state["failed_build_keys"] == [KEY_A]
        assert not builder.marker_path(world.cache, KEY_A).exists()
        assert _run(world)[KEY_A]["reused"] is False

    def test_the_lock_is_released_after_a_batch(self, world):
        _run(world)
        with builder.BatchLock(world.cache, KEY_A) as acquired:
            assert acquired


class TestTheProductionCacheIsProtected:
    def test_the_default_refuses_the_production_cache_root(self, tmp_path):
        with pytest.raises(SystemExit, match="production"):
            builder.main([
                "--registration", str(tmp_path / "r.json"),
                "--profile", str(tmp_path / "p.json"),
                "--cache-root",
                "runs/step5_code_approved_20260915b/profile-daily-cost-cache",
                "--build-key", "k", "--status", str(tmp_path / "s.json")])


class TestTheOraclePath:
    """What one batch actually costs, and that it stays independent."""

    def test_each_unit_reparses_every_variant(self, world, monkeypatch):
        from traffic_sim import simulation
        from traffic_sim.simulation import disruption

        parses = []
        production = disruption.parse_route_vehicles

        def spy(path, **kwargs):
            parses.append(str(path))
            return production(path, **kwargs)

        monkeypatch.setattr(disruption, "parse_route_vehicles", spy)
        assert simulation is not None
        _run(world)
        units = len(world.units[KEY_A])
        assert len(parses) == 3 * units, (
            "the per-file oracle parses each variant once per daily unit")
        assert len(set(parses)) == 3, "the same three files, over and over"

    def test_the_provider_is_built_once_per_build_key(self, world,
                                                      monkeypatch):
        built = []
        production = dd.ArchiveDisruptionProvider.__init__

        def spy(self, spec, **kwargs):
            built.append(str(kwargs["archive"]))
            production(self, spec, **kwargs)

        monkeypatch.setattr(dd.ArchiveDisruptionProvider, "__init__", spy)
        _run(world, keys=(KEY_A, KEY_B))
        assert len(built) == 2 == len(set(built))
