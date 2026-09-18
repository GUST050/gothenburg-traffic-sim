"""The cost ledger prices each daily unit once, per archive, then aggregates.

The month ledger used to ask `IndependentDailyCostSource` for a provider per
DAILY UNIT and keep every one of them for the life of the run: 1,950 live
providers, and three XML parses per unit because no provider ever served a
second one. Measured 2026-09-18, that cost about 635 MB per unit once the
providers retained their parsed routes, and the bounded canary had to abort
at unit 5 of 65.

The fix is two phases. Phase one prices every distinct daily unit exactly
once, grouped by archive, with ONE provider per archive that parses q10,
q50 and q90 once and is destroyed before the next archive opens. Phase two
aggregates the parents from the small frozen unit records.

These tests use real archives and the real provider, so the parse counts
here are real parses rather than a stand-in. What is synthetic is only the
parent shape: `daily_units_for` is a constructor argument, so the tests
build overlapping parents deliberately to prove reuse.
"""
from __future__ import annotations

import gc
import subprocess
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_wci_streaming import (
    DATES,
    _Network,
    _spec,
    _write_archive,
)
from traffic_sim.core.closure_calendar import iter_closure_schedules
from traffic_sim.simulation import cost_ordered_execution as coe
from traffic_sim.simulation import deterministic_disruption as dd
from traffic_sim.simulation.independent_daily import daily_unit_records

UNITS_PER_PARENT = 3


@pytest.fixture
def world(tmp_path):
    """Three archives, their daily units, and overlapping parents."""
    spec = _spec(end=DATES[-1])
    archives = {date: _write_archive(tmp_path / "runs", date, 1)
                for date in DATES}
    network = _Network()
    cache_root = tmp_path / "cache"

    ordered: list[tuple[str, object]] = []
    seen: set[str] = set()
    for parent in iter_closure_schedules(spec):
        for unit_id, _identity, build in daily_unit_records(spec, parent):
            if str(unit_id) in seen:
                continue
            seen.add(str(unit_id))
            ordered.append((str(unit_id), build()))
    assert len(ordered) >= 6, "the fixture needs several daily units"

    # Overlapping parents: a sliding window, so most units serve two parents
    # and the second use must come from the frozen record.
    parents = []
    for start in range(0, len(ordered) - UNITS_PER_PARENT + 1):
        parents.append(SimpleNamespace(
            schedule_id=f"parent-{start}",
            units=tuple(ordered[start:start + UNITS_PER_PARENT])))

    def daily_units_for(parent):
        return list(parent.units)

    def scope_of(schedule):
        return str(archives[schedule.first_work_date])

    built: list[weakref.ref] = []

    def provider_for(schedule, cache_root=cache_root):
        provider = dd.ArchiveDisruptionProvider(
            spec, archive=archives[schedule.first_work_date],
            network=network, cache=dd.DailyCostCache(cache_root),
            reuse_parsed_routes=True)
        built.append(weakref.ref(provider))
        return provider

    return SimpleNamespace(
        spec=spec, archives=archives, network=network,
        cache_root=cache_root, units=ordered, parents=parents,
        daily_units_for=daily_units_for, scope_of=scope_of,
        provider_for=provider_for, built=built,
        scopes=sorted({scope_of(schedule) for _unit, schedule in ordered}))


def _source(world, *, scoped=True, window_cost_index=None, cache_root=None):
    root = world.cache_root if cache_root is None else cache_root
    return coe.IndependentDailyCostSource(
        world.spec,
        daily_units_for=world.daily_units_for,
        provider_for=lambda schedule: world.provider_for(schedule,
                                                         cache_root=root),
        cache=dd.DailyCostCache(root),
        window_cost_index=window_cost_index,
        objective_method="closure_cost_v1",
        provider_scope_key_for=world.scope_of if scoped else None)


def _parse_spy(monkeypatch):
    from traffic_sim.simulation import disruption

    parses: list[str] = []
    production = disruption.parse_route_vehicles

    def spy(path, **kwargs):
        parses.append(str(path))
        return production(path, **kwargs)

    monkeypatch.setattr(disruption, "parse_route_vehicles", spy)
    return parses


def _compute_spy(monkeypatch):
    calls: list[str] = []
    production = dd.ArchiveDisruptionProvider._compute

    def spy(self, schedule):
        calls.append(schedule.schedule_id)
        return production(self, schedule)

    monkeypatch.setattr(dd.ArchiveDisruptionProvider, "_compute", spy)
    return calls


class TestOneProviderPerArchive:
    def test_every_unit_of_one_archive_shares_one_provider(self, world,
                                                           monkeypatch):
        parses = _parse_spy(monkeypatch)
        source = _source(world)
        source.prepare_units(world.parents)
        first = world.scopes[0]
        mine = [item for item in parses if item.startswith(first)]
        assert len(mine) == 3, (
            "one archive is read once per variant, however many daily units "
            f"it serves: {len(mine)}")
        assert len(world.built) == len(world.scopes), (
            "exactly one provider per archive")

    def test_three_archives_give_three_providers_and_nine_parses(
            self, world, monkeypatch):
        parses = _parse_spy(monkeypatch)
        source = _source(world)
        source.prepare_units(world.parents)
        assert len(world.scopes) == 3
        assert len(world.built) == 3
        assert len(parses) == 9 == len(set(parses))

    def test_only_one_route_owning_provider_is_alive_at_a_time(self, world):
        source = _source(world)
        live: list[int] = []
        production = dd.ArchiveDisruptionProvider.__init__

        def counting(self, spec, **kwargs):
            gc.collect()
            live.append(sum(1 for ref in world.built if ref() is not None))
            production(self, spec, **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(dd.ArchiveDisruptionProvider, "__init__", counting)
            source.prepare_units(world.parents)
        assert live == [0, 0, 0], (
            "the previous archive's provider must be gone before the next "
            f"one opens: {live}")

    def test_no_provider_survives_the_prepare_phase(self, world):
        source = _source(world)
        source.prepare_units(world.parents)
        gc.collect()
        assert [ref for ref in world.built if ref() is not None] == []
        assert source.live_provider_count() == 0, (
            "providers must not accumulate in an unbounded dictionary")


class TestEveryUnitIsPricedOnce:
    def test_each_distinct_unit_is_computed_exactly_once(self, world,
                                                         monkeypatch):
        calls = _compute_spy(monkeypatch)
        source = _source(world)
        source.prepare_units(world.parents)
        assert len(calls) == len(world.units) == len(set(calls))

    def test_overlapping_parents_reuse_the_frozen_record(self, world,
                                                         monkeypatch):
        source = _source(world)
        source.prepare_units(world.parents)
        calls = _compute_spy(monkeypatch)
        parses = _parse_spy(monkeypatch)
        for parent in world.parents:
            source.parent_cost(parent)
        assert calls == [], "aggregation must not price anything again"
        assert parses == [], "aggregation must not read a route file again"


class TestTheAnswerIsUnchanged:
    """Prepared and unprepared must agree, byte for byte."""

    @staticmethod
    def _ledger(world, *, prepared):
        """Each arm gets its own COLD cache root.

        Sharing one would let whichever arm runs second find the other's
        entries on disk, and the disk counters — which reach the ledger's
        content key — would differ for a reason that has nothing to do with
        the two phases.
        """
        root = world.cache_root.parent / (
            "cache-fast" if prepared else "cache-lazy")
        source = _source(world, scoped=prepared, cache_root=root)
        if prepared:
            source.prepare_units(world.parents)
        return source, coe.build_cost_ledger(world.spec, world.parents,
                                             source)

    def test_parent_costs_and_per_variant_are_identical(self, world):
        _lazy, lazy = self._ledger(world, prepared=False)
        _fast, fast = self._ledger(world, prepared=True)
        assert [item.to_dict() for item in lazy.costs] == [
            item.to_dict() for item in fast.costs]

    def test_the_deciding_ledger_content_is_identical(self, world):
        _lazy, lazy = self._ledger(world, prepared=False)
        _fast, fast = self._ledger(world, prepared=True)
        assert lazy.to_dict() == fast.to_dict()

    def test_provider_identity_and_cache_keys_are_identical(self, world):
        lazy_source, lazy = self._ledger(world, prepared=False)
        fast_source, fast = self._ledger(world, prepared=True)
        assert dict(lazy_source.identity()) == dict(fast_source.identity())
        assert lazy.provider_identity == fast.provider_identity
        cache = dd.DailyCostCache(world.cache_root.parent / "cache-fast")
        probe = dd.ArchiveDisruptionProvider(
            world.spec, archive=world.archives[DATES[0]],
            network=world.network, cache=None)
        for _unit, schedule in world.units:
            if schedule.first_work_date != DATES[0]:
                continue
            assert cache.load(probe.cache_identity(schedule)) is not None, (
                "the two-phase path must write the same cache identities")

    def test_the_cache_telemetry_keeps_its_meaning(self, world):
        lazy_source, lazy = self._ledger(world, prepared=False)
        fast_source, fast = self._ledger(world, prepared=True)
        assert (dict(lazy_source.cache_snapshot())
                == dict(fast_source.cache_snapshot()))
        assert (dict(lazy_source.population_snapshot())
                == dict(fast_source.population_snapshot()))
        assert lazy.cache_hits == fast.cache_hits
        assert lazy.cache_misses == fast.cache_misses
        assert fast.cache_misses == len(world.units), (
            "a unit's first appearance in the parent loop is still a miss "
            "even though the computing happened in prepare")


class TestFailClosed:
    def test_a_unit_id_that_changes_its_schedule_is_refused(self, world):
        first_id, first_schedule = world.units[0]
        other = next(schedule for _unit, schedule in world.units[1:]
                     if schedule.schedule_id != first_schedule.schedule_id)
        clash = SimpleNamespace(schedule_id="parent-clash",
                                units=((first_id, other),))
        source = _source(world)
        with pytest.raises(ValueError, match="daily unit"):
            source.prepare_units(list(world.parents) + [clash])

    def test_archive_drift_during_a_scope_refuses_the_result(self, world):
        source = _source(world)
        victim = world.archives[DATES[0]] / dd.VARIANT_FILENAMES["q50"]
        production = dd.ArchiveDisruptionProvider.disruption
        seen: list[str] = []

        def drifting(self, schedule):
            if not seen:
                seen.append(schedule.schedule_id)
                victim.write_text("<routes/>", encoding="utf-8")
            return production(self, schedule)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(dd.ArchiveDisruptionProvider, "disruption",
                          drifting)
            with pytest.raises(dd.DisruptionError):
                source.prepare_units(world.parents)

    def test_a_failed_prepare_leaves_nothing_reusable(self, world):
        source = _source(world)
        production = dd.ArchiveDisruptionProvider.disruption
        seen: list[str] = []

        def exploding(self, schedule):
            if len(seen) >= 2:
                raise RuntimeError("worker died")
            seen.append(schedule.schedule_id)
            return production(self, schedule)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(dd.ArchiveDisruptionProvider, "disruption",
                          exploding)
            raised = False
            try:
                source.prepare_units(world.parents)
            except RuntimeError:
                # Deliberately NOT pytest.raises: it keeps the traceback,
                # whose frame holds the provider as `self`, so the liveness
                # assertion below would be measuring the test harness.
                raised = True
        assert raised
        gc.collect()
        assert [ref for ref in world.built if ref() is not None] == []
        assert not source.is_prepared, (
            "a half-finished prepare must never be treated as complete")
        with pytest.raises(RuntimeError, match="prepare"):
            source.frozen_unit_records()


class TestTheIndexPathIsUntouched:
    def test_the_window_cost_index_path_builds_no_provider(self, world,
                                                           monkeypatch):
        records = tuple({"demand_variant": variant, "vehicles_affected": 0,
                         "vehicles_considered": 0, "vehicles_no_detour": 0,
                         "added_vehicle_hours": 0.0,
                         "added_metres_total": 0.0}
                        for variant in ("q10", "q50", "q90"))
        index = SimpleNamespace(
            bound_identity={"search_content_key": world.spec.content_key,
                            "provider_identity": {"schema": "fake"}},
            lookup=lambda unit_id, schedule_id: records)
        parses = _parse_spy(monkeypatch)
        source = _source(world, window_cost_index=index)
        source.prepare_units(world.parents)
        assert parses == [], "the index path must read no route file"
        assert world.built == [], "the index path must build no provider"


class TestNoProcessIsStarted:
    def test_preparing_starts_no_sumo_or_demand_process(self, world,
                                                        monkeypatch):
        def refuse(*_args, **_kwargs):
            raise AssertionError("cost preparation must start no process")

        monkeypatch.setattr(subprocess, "Popen", refuse)
        source = _source(world)
        source.prepare_units(world.parents)
        assert not list(Path(world.cache_root).glob("**/demand-*"))
