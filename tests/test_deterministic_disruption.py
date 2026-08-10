"""PR D: deterministic closure cost, computed without a simulator.

The point of moving this computation before SUMO is that it can then decide
what to simulate. That only holds if the number it produces is the SAME number
the post-SUMO path produced — so the tests here are mostly about identity:

  * field-wise identical records between the process-free provider and the
    SUMO runner's own disruption;
  * per-variant summation BEFORE the field-wise worst reduction;
  * a cache key that hits when a date range widens around the same daily unit
    and misses when a route, network, variant or line of costing code moves;
  * `vehicles_no_detour` disqualifying rather than becoming a large cost.

Route files here are small hand-written fixtures, not calibrated demand. They
exercise every branch of the contract; they are NOT the plan's named
equivalence benchmark, which needs the real q10/q50/q90 archives.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from traffic_sim.core.contracts import (
    ClosureInterval,
    ClosureSchedule,
    ClosureSearchSpec,
    DailyTimeBand,
)
from traffic_sim.simulation import deterministic_disruption as dd
from traffic_sim.simulation.closure_ranking import ClosureCost, rank_closures


EPOCH = "2025-09-16T00:00:00"


def _spec(**overrides) -> ClosureSearchSpec:
    values = {
        "search_id": "deterministic-cost",
        "directed_edges": ("edge-a",),
        "demand_build_id": "demand-key",
        "source": "historical",
        "permitted_date_start": "2025-09-16",
        "permitted_date_end": "2025-09-16",
        "required_work_minutes": 60,
        "max_consecutive_start_days": 1,
        "permitted_daily_band": DailyTimeBand("06:00", "07:00"),
        "objective_profile": "displaced_vehicles_and_detour_v1",
    }
    values.update(overrides)
    return ClosureSearchSpec(**values)


def _schedule(spec: ClosureSearchSpec, *, date="2025-09-16",
              start="06:00", end="07:00") -> ClosureSchedule:
    from traffic_sim.core.contracts import _content_key

    interval = ClosureInterval(
        work_date=date,
        start_time=f"{date}T{start}:00",
        end_time=f"{date}T{end}:00",
    )
    minutes = interval.duration_minutes
    identity = {
        "search_content_key": spec.content_key,
        "first_work_date": date,
        "day_count": 1,
        "daily_start": start,
        "daily_end": end,
        "scheduled_work_minutes": minutes,
    }
    return ClosureSchedule(
        schedule_id="closure-" + _content_key(identity),
        search_content_key=spec.content_key,
        first_work_date=date,
        day_count=1,
        daily_start=start,
        daily_end=end,
        required_work_minutes=minutes,
        scheduled_work_minutes=minutes,
        actual_closed_minutes=minutes,
        rounding_overshoot_minutes=0,
        intervals=(interval,),
    )


def _archive(root: Path, *, variants=None, intervals=96) -> Path:
    """One immutable-looking archive with three distinguishable route files."""
    archive = Path(root)
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "demand_meta.json").write_text(json.dumps({
        "demand_build_key": "demand-key",
        "epoch_sim": EPOCH,
        "n_intervals": intervals,
        "n_variants": 3,
    }), encoding="utf-8")
    bodies = variants or {
        "q50": "<routes id='q50'/>",
        "q10": "<routes id='q10'/>",
        "q90": "<routes id='q90'/>",
    }
    for variant, filename in dd.VARIANT_FILENAMES.items():
        (archive / filename).write_text(bodies[variant], encoding="utf-8")
    return archive


class FakeNetwork:
    """A network model with no network. Never parses 16 MB of XML."""

    def __init__(self, sha256="net-sha"):
        self.network_path = Path("sumo/net.net.xml")
        self.network_sha256 = sha256
        self.adjacency = {}
        self.edge_time = {}
        self.edge_len = {}

    def identity(self):
        return {"path": str(self.network_path), "sha256": self.network_sha256}


def _fake_disruption(monkeypatch, table):
    """Route `run_scenario.closure_disruption` to a table keyed by route file."""
    calls: list[tuple] = []

    def fake(route_path, closed, closures, edge_time, edge_len, adj=None):
        calls.append((Path(route_path).name, tuple(sorted(closed)),
                      tuple((c["edge_id"], c["begin_s"], c["end_s"])
                            for c in closures)))
        return dict(table[Path(route_path).name])

    import run_scenario as rs
    monkeypatch.setattr(rs, "closure_disruption", fake)
    return calls


def _record(*, affected=10, hours=1.5, metres=2000.0, no_detour=0,
            considered=100):
    return {
        "vehicles_affected": affected,
        "vehicles_considered": considered,
        "vehicles_no_detour": no_detour,
        "added_vehicle_hours": hours,
        "added_metres_total": metres,
    }


def _table(**by_variant):
    defaults = {"q50": _record(), "q10": _record(hours=1.0),
                "q90": _record(hours=2.0)}
    defaults.update(by_variant)
    return {dd.VARIANT_FILENAMES[v]: r for v, r in defaults.items()}


# --------------------------------------------------------------------------
# The provider is process-free.
# --------------------------------------------------------------------------


class TestProcessFree:
    def test_the_protocol_exposes_no_simulation_handle(self):
        members = set(dir(dd.DeterministicDisruptionProvider))
        assert {"identity", "disruption"} <= members
        for name in members:
            assert "traci" not in name.lower()
            assert "sumo" not in name.lower()

    def test_computing_a_cost_starts_no_process(self, tmp_path, monkeypatch):
        import subprocess

        def refuse(*args, **kwargs):
            raise AssertionError("deterministic cost must not start a process")

        monkeypatch.setattr(subprocess, "run", refuse)
        monkeypatch.setattr(subprocess, "Popen", refuse)
        _fake_disruption(monkeypatch, _table())

        spec = _spec()
        provider = dd.ArchiveDisruptionProvider(
            spec, archive=_archive(tmp_path / "a"), network=FakeNetwork())
        records = provider.disruption(_schedule(spec))
        assert [item["demand_variant"] for item in records] == list(
            dd.DEMAND_VARIANTS)

    def test_importing_the_module_does_not_load_the_simulation_stack(self):
        """Cost ordering imports this; it must stay as light as preflight."""
        import subprocess
        import sys

        program = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
            "import traffic_sim.simulation.deterministic_disruption\n"
            "print(json.dumps({'scipy': 'scipy' in sys.modules,"
            " 'pandas': 'pandas' in sys.modules,"
            " 'run_scenario': 'run_scenario' in sys.modules}))\n"
        )
        completed = subprocess.run([sys.executable, "-c", program],
                                   capture_output=True, text=True, timeout=300,
                                   cwd=Path(__file__).resolve().parents[1])
        assert completed.returncode == 0, completed.stderr[-2000:]
        loaded = json.loads(completed.stdout.strip().splitlines()[-1])
        assert loaded == {"scipy": False, "pandas": False,
                          "run_scenario": False}


# --------------------------------------------------------------------------
# Identity between the pre-SUMO and post-SUMO paths.
# --------------------------------------------------------------------------


class TestEquivalenceWithTheSumoRunnerPath:
    def test_the_runner_delegates_to_the_same_provider(self, monkeypatch):
        """The SUMO runner's own disruption IS the provider's output.

        Equivalence is not asserted by comparing two implementations; there is
        one implementation, and this pins that arrangement so a future edit
        cannot quietly fork it.
        """
        import inspect

        from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

        source = inspect.getsource(ArchivedDemandSumoRunner._closure_disruption)
        assert "deterministic_disruption_provider()" in source
        assert "rs.closure_disruption" not in source

    def test_the_runner_and_the_provider_produce_identical_records(
            self, tmp_path, monkeypatch):
        """Behavioural equivalence, not just a shared call site.

        The SUMO runner is constructed for real (construction is process-free)
        with disruption enabled, and its records are compared field for field
        against a provider built independently from the same archive.
        """
        import run_scenario as rs
        from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

        monkeypatch.setattr(rs, "build_edge_graph", lambda closed: {})
        monkeypatch.setattr(rs, "free_flow_edge_cost", lambda: ({}, {}))
        monkeypatch.setattr(rs, "edges_near", lambda edges, radius: [])
        monkeypatch.setattr(rs, "edge_freeflow_times", lambda: {})
        import suggest_closure_time as legacy
        monkeypatch.setattr(legacy, "detour_availability",
                            lambda edges, net: {})
        _fake_disruption(monkeypatch, _table())

        spec = _spec()
        archive = _archive(tmp_path / "a")
        runner = ArchivedDemandSumoRunner(
            spec,
            archive=archive,
            baseline_trip_duration_p99_s=1800,
            study_provenance_key="study",
            cache_root=tmp_path / "cache",
            include_disruption=True,
        )
        schedule = _schedule(spec)

        from_runner = runner._closure_disruption(schedule)
        from_provider = dd.ArchiveDisruptionProvider(
            spec, archive=archive, network=FakeNetwork()).disruption(schedule)

        assert len(from_runner) == len(from_provider) == 3
        for produced, wanted in zip(from_runner, from_provider):
            assert dict(produced) == dict(wanted)

    def test_closure_windows_use_one_shared_conversion(self, monkeypatch):
        import inspect

        from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

        source = inspect.getsource(ArchivedDemandSumoRunner._closure_seconds)
        assert "_deterministic_closure_seconds" in source

    def test_the_windows_match_the_archive_epoch(self, tmp_path, monkeypatch):
        calls = _fake_disruption(monkeypatch, _table())
        spec = _spec()
        provider = dd.ArchiveDisruptionProvider(
            spec, archive=_archive(tmp_path / "a"), network=FakeNetwork())
        provider.disruption(_schedule(spec, start="06:00", end="07:00"))

        assert calls
        _name, closed, windows = calls[0]
        assert closed == ("edge-a",)
        assert windows == (("edge-a", 6 * 3600, 7 * 3600),)

    def test_a_closure_outside_the_archive_fails_closed(
            self, tmp_path, monkeypatch):
        _fake_disruption(monkeypatch, _table())
        spec = _spec(permitted_date_start="2025-09-17",
                     permitted_date_end="2025-09-17")
        provider = dd.ArchiveDisruptionProvider(
            spec, archive=_archive(tmp_path / "a", intervals=96),
            network=FakeNetwork())
        with pytest.raises(ValueError, match="outside demand archive"):
            provider.disruption(_schedule(spec, date="2025-09-17"))


# --------------------------------------------------------------------------
# Aggregation order.
# --------------------------------------------------------------------------


class TestAggregationOrder:
    def test_variants_are_summed_before_the_worst_reduction(self):
        """Reducing first and summing after would invent a mixed world.

        Day one is worst under q10, day two under q90. Summing per variant
        first gives 3+3 = 6 for either; reducing first would give 4+4 = 8,
        which is a schedule whose direction split changed overnight.
        """
        day_one = [
            {"demand_variant": "q10", **_record(hours=3.0, metres=10.0,
                                                affected=3)},
            {"demand_variant": "q50", **_record(hours=2.0, metres=10.0,
                                                affected=2)},
            {"demand_variant": "q90", **_record(hours=1.0, metres=10.0,
                                                affected=1)},
        ]
        day_two = [
            {"demand_variant": "q10", **_record(hours=1.0, metres=10.0,
                                                affected=1)},
            {"demand_variant": "q50", **_record(hours=2.0, metres=10.0,
                                                affected=2)},
            {"demand_variant": "q90", **_record(hours=3.0, metres=10.0,
                                                affected=3)},
        ]

        combined = dd.sum_daily_disruption([day_one, day_two])
        by_variant = {item["demand_variant"]: item for item in combined}
        assert by_variant["q10"]["added_vehicle_hours"] == 4.0
        assert by_variant["q50"]["added_vehicle_hours"] == 4.0
        assert by_variant["q90"]["added_vehicle_hours"] == 4.0

        cost = dd.parent_closure_cost("closure-x", [day_one, day_two])
        assert cost.added_vehicle_hours == 4.0
        assert cost.added_vehicle_hours != 6.0

    def test_it_matches_the_post_sumo_daily_aggregation_field_for_field(self):
        """The pre-SUMO sum must equal what `aggregate_daily_evidence` sums.

        Two real daily units from a real decomposition, each carrying its own
        q10/q50/q90 records, reduced both ways.
        """
        from traffic_sim.core.closure_calendar import generate_closure_schedules
        from traffic_sim.simulation.finalist_decision import (
            CandidateEvidence,
            PairedObservation,
        )
        from traffic_sim.simulation.independent_daily import (
            aggregate_daily_evidence,
            decompose_schedules,
        )

        spec = _spec(
            permitted_date_start="2025-09-16",
            permitted_date_end="2025-09-17",
            required_work_minutes=120,
            max_consecutive_start_days=2,
            permitted_daily_band=DailyTimeBand("06:00", "08:00"),
            allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
            interday_policy="independent_daily_reset_v1",
            work_allocation_policy="exact_equal_daily_v1",
        )
        parent = next(item for item in generate_closure_schedules(spec)
                      if item.day_count == 2)
        units, parents = decompose_schedules(spec, [parent])
        ordered = [next(unit for unit in units if unit.unit_id == unit_id)
                   for unit_id in parents[parent.schedule_id]]
        assert len(ordered) == 2

        days = []
        evidence = {}
        for index, unit in enumerate(ordered):
            records = [
                {"demand_variant": variant,
                 **_record(hours=1.25 + index + offset,
                           metres=100.0 * (index + 1),
                           affected=index + 1, considered=50)}
                for offset, variant in enumerate(dd.DEMAND_VARIANTS)
            ]
            days.append(records)
            evidence[unit.unit_id] = CandidateEvidence(
                candidate_id=unit.schedule.schedule_id,
                observations=(PairedObservation(
                    candidate_id=unit.schedule.schedule_id,
                    demand_variant="q50",
                    seed=1000,
                    baseline_time_loss_s=1.0,
                    candidate_time_loss_s=2.0,
                    matched_baseline_id="baseline",
                    provenance_key="provenance",
                ),),
                disruption=tuple(records),
            )

        aggregated = aggregate_daily_evidence(parent, ordered, evidence)
        expected = dd.sum_daily_disruption(days)

        assert len(aggregated.disruption) == len(expected) == 3
        for produced, wanted in zip(aggregated.disruption, expected):
            for field in ("demand_variant", "vehicles_affected",
                          "vehicles_considered", "vehicles_no_detour",
                          "added_vehicle_hours", "added_metres_total",
                          "basis", "reduction"):
                assert produced[field] == wanted[field], field

    def test_an_incomplete_variant_set_fails_closed(self):
        with pytest.raises(dd.DisruptionUnavailable, match="q10/q50/q90"):
            dd.sum_daily_disruption([[
                {"demand_variant": "q50", **_record()},
            ]])

    def test_no_daily_units_fails_closed(self):
        with pytest.raises(dd.DisruptionUnavailable):
            dd.sum_daily_disruption([])


# --------------------------------------------------------------------------
# Disqualification.
# --------------------------------------------------------------------------


class TestNoDetourDisqualifies:
    def test_it_is_never_turned_into_a_large_cost(self, tmp_path, monkeypatch):
        _fake_disruption(monkeypatch, _table(
            q90=_record(no_detour=3, hours=0.1)))
        spec = _spec()
        provider = dd.ArchiveDisruptionProvider(
            spec, archive=_archive(tmp_path / "a"), network=FakeNetwork())
        cost = provider.closure_cost(_schedule(spec))

        assert cost.disqualified is True
        assert cost.vehicles_no_detour == 3
        # The cheap hours are still reported; they are simply not what
        # decides. q90 is the disqualifying variant AND the cheapest one, which
        # is exactly the case a blended score would hide.
        assert cost.added_vehicle_hours == 1.5

        ranked, refused = rank_closures([cost])
        assert ranked == ()
        assert refused == (cost,)

    def test_the_refusal_keeps_its_full_evidence(self):
        cost = ClosureCost(
            candidate_id="closure-x",
            added_vehicle_hours=1.0,
            added_metres_total=2.0,
            vehicles_affected=3,
            vehicles_no_detour=4,
        )
        combined = [{"demand_variant": v, **_record(no_detour=4)}
                    for v in dd.DEMAND_VARIANTS]
        evidence = dd.disqualification_evidence(cost, combined)

        assert evidence["reason"] == "deterministic_no_detour"
        assert evidence["decided_before_sumo"] is True
        assert evidence["vehicles_no_detour"] == 4
        assert len(evidence["per_variant"]) == 3
        assert evidence["schema"] == dd.DISRUPTION_SCHEMA


# --------------------------------------------------------------------------
# The daily-cost cache.
# --------------------------------------------------------------------------


class TestDailyCostCache:
    def _provider(self, tmp_path, monkeypatch, *, archive_dir="a",
                  network=None, table=None):
        _fake_disruption(monkeypatch, table or _table())
        spec = _spec()
        cache = dd.DailyCostCache(tmp_path / "cache")
        provider = dd.ArchiveDisruptionProvider(
            spec,
            archive=_archive(tmp_path / archive_dir),
            network=network or FakeNetwork(),
            cache=cache,
        )
        return spec, provider, cache

    def test_a_second_provider_reuses_the_stored_cost(
            self, tmp_path, monkeypatch):
        spec, provider, cache = self._provider(tmp_path, monkeypatch)
        first = provider.disruption(_schedule(spec))

        calls = _fake_disruption(monkeypatch, _table())
        again = dd.ArchiveDisruptionProvider(
            spec, archive=tmp_path / "a", network=FakeNetwork(), cache=cache)
        second = again.disruption(_schedule(spec))

        assert second == first
        assert calls == [], "a cache hit must not recompute"

    def test_widening_the_date_range_still_hits_the_same_unit(
            self, tmp_path, monkeypatch):
        """The cache is keyed on the unit, not on the search that asked."""
        spec, provider, cache = self._provider(tmp_path, monkeypatch)
        schedule = _schedule(spec)
        provider.disruption(schedule)

        wider = _spec(permitted_date_end="2025-09-30",
                      max_consecutive_start_days=1)
        assert wider.content_key != spec.content_key
        identity_a = provider.cache_identity(schedule)
        wider_provider = dd.ArchiveDisruptionProvider(
            wider, archive=tmp_path / "a", network=FakeNetwork(), cache=cache)
        identity_b = wider_provider.identity()

        # The provider identity — edges, source, demand, network, code — is
        # what the unit's cost depends on, and it is unchanged by the range.
        assert identity_b == {k: v for k, v in identity_a.items()
                              if k != "schedule"}

    def test_a_changed_route_file_misses(self, tmp_path, monkeypatch):
        spec, provider, cache = self._provider(tmp_path, monkeypatch)
        schedule = _schedule(spec)
        before = provider.cache_identity(schedule)

        (tmp_path / "a" / dd.VARIANT_FILENAMES["q10"]).write_text(
            "<routes id='q10-changed'/>", encoding="utf-8")
        after = dd.ArchiveDisruptionProvider(
            spec, archive=tmp_path / "a", network=FakeNetwork(),
            cache=cache).cache_identity(schedule)

        assert cache.key(before) != cache.key(after)

    def test_a_changed_network_misses(self, tmp_path, monkeypatch):
        spec, provider, cache = self._provider(tmp_path, monkeypatch)
        schedule = _schedule(spec)
        before = provider.cache_identity(schedule)
        after = dd.ArchiveDisruptionProvider(
            spec, archive=tmp_path / "a", network=FakeNetwork("other-net"),
            cache=cache).cache_identity(schedule)

        assert cache.key(before) != cache.key(after)

    def test_changed_costing_code_misses(self, tmp_path, monkeypatch):
        spec, provider, cache = self._provider(tmp_path, monkeypatch)
        schedule = _schedule(spec)
        before = provider.cache_identity(schedule)

        monkeypatch.setattr(dd, "costing_source_identity",
                            lambda: {"run_scenario.py": "different"})
        after = dd.ArchiveDisruptionProvider(
            spec, archive=tmp_path / "a", network=FakeNetwork(),
            cache=cache).cache_identity(schedule)

        assert cache.key(before) != cache.key(after)

    def test_a_different_daily_window_misses(self, tmp_path, monkeypatch):
        spec, provider, cache = self._provider(tmp_path, monkeypatch)
        early = provider.cache_identity(_schedule(spec, start="06:00",
                                                  end="07:00"))
        late = provider.cache_identity(_schedule(spec, start="08:00",
                                                 end="09:00"))
        assert cache.key(early) != cache.key(late)

    def test_a_record_stored_under_another_identity_fails_closed(
            self, tmp_path, monkeypatch):
        spec, provider, cache = self._provider(tmp_path, monkeypatch)
        schedule = _schedule(spec)
        identity = provider.cache_identity(schedule)
        provider.disruption(schedule)

        path = cache.path_for(cache.key(identity))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["identity"]["network"]["sha256"] = "tampered"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(dd.DailyCostCacheCorrupt, match="identity"):
            cache.load(identity)

    def test_an_unreadable_record_fails_closed(self, tmp_path, monkeypatch):
        spec, provider, cache = self._provider(tmp_path, monkeypatch)
        schedule = _schedule(spec)
        identity = provider.cache_identity(schedule)
        provider.disruption(schedule)
        cache.path_for(cache.key(identity)).write_text("{", encoding="utf-8")

        with pytest.raises(dd.DailyCostCacheCorrupt, match="unreadable"):
            cache.load(identity)

    def test_a_partial_variant_set_in_the_cache_fails_closed(
            self, tmp_path, monkeypatch):
        spec, provider, cache = self._provider(tmp_path, monkeypatch)
        schedule = _schedule(spec)
        identity = provider.cache_identity(schedule)
        provider.disruption(schedule)

        path = cache.path_for(cache.key(identity))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["disruption"] = payload["disruption"][:2]
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(dd.DailyCostCacheCorrupt, match="q10/q50/q90"):
            cache.load(identity)

    def test_storing_leaves_no_partial_file(self, tmp_path, monkeypatch):
        spec, provider, cache = self._provider(tmp_path, monkeypatch)
        provider.disruption(_schedule(spec))
        assert not list((tmp_path / "cache").rglob("*.partial"))


class TestArchiveResolution:
    def test_a_missing_variant_route_fails_closed(self, tmp_path):
        archive = _archive(tmp_path / "a")
        (archive / dd.VARIANT_FILENAMES["q90"]).unlink()
        with pytest.raises(dd.DisruptionUnavailable, match="q90 route file"):
            dd.ArchiveInputs.from_archive(archive)

    def test_missing_metadata_fails_closed(self, tmp_path):
        archive = _archive(tmp_path / "a")
        (archive / "demand_meta.json").unlink()
        with pytest.raises(dd.DisruptionUnavailable, match="demand_meta"):
            dd.ArchiveInputs.from_archive(archive)

    def test_a_schedule_from_another_search_is_refused(
            self, tmp_path, monkeypatch):
        _fake_disruption(monkeypatch, _table())
        spec = _spec()
        other = _spec(required_work_minutes=30,
                      permitted_daily_band=DailyTimeBand("06:00", "07:00"))
        provider = dd.ArchiveDisruptionProvider(
            spec, archive=_archive(tmp_path / "a"), network=FakeNetwork())
        with pytest.raises(dd.DisruptionUnavailable, match="does not belong"):
            provider.disruption(_schedule(other, end="06:30"))
