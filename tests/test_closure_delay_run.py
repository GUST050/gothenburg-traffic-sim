"""closure_delay_run: replaying a finished search as a distribution.

The curve is only worth drawing if it describes the run it is drawn beside,
so the tests here are about WHICH inputs get picked up: the archives the
search froze rather than today's defaults, the same daily decomposition the
ranking used, and a refusal when the totals no longer reproduce the ranked
cost. The histogram arithmetic itself is covered in test_delay_profile.py.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from traffic_sim.analysis import closure_delay_run as run
from traffic_sim.analysis.delay_profile import BINS, DelayProfileError, bin_index
from traffic_sim.core.contracts import (
    ClosureInterval,
    ClosureSchedule,
    ClosureSearchSpec,
    DailyTimeBand,
    _content_key,
)
from traffic_sim.simulation.deterministic_disruption import VARIANT_FILENAMES
from traffic_sim.simulation.envelope import (
    EnvelopePolicy,
    build_simulation_envelope,
    independent_daily_demand_spec,
)


# The same chain as test_delay_profile: A->B->C->D costs 40 s, the A->P->D
# bypass costs 80 s, so closing C adds exactly 40 s to every affected driver.
ADJ = {"A": ["B", "P"], "B": ["C"], "C": ["D"], "P": ["D"], "D": []}
TIME = {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0, "P": 60.0}
LEN = {"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0, "P": 900.0}

DATES = ("2027-07-15", "2027-07-16")


class FakeNetwork:
    """A cost model with no network file. Never parses 16 MB of XML."""

    network_path = Path("sumo/net.net.xml")
    network_sha256 = "net-sha"
    adjacency = ADJ
    edge_time = TIME
    edge_len = LEN

    def identity(self):
        return {"path": str(self.network_path), "sha256": self.network_sha256}


def _spec(**overrides) -> ClosureSearchSpec:
    values = {
        "search_id": "delay-profile",
        "directed_edges": ("C",),
        "demand_build_id": "demand-key",
        "source": "forecast",
        "permitted_date_start": DATES[0],
        "permitted_date_end": DATES[-1],
        "required_work_minutes": 120,
        "max_consecutive_start_days": 2,
        "permitted_daily_band": DailyTimeBand("06:00", "18:00"),
        "objective_profile": "displaced_vehicles_and_detour_v1",
        "interday_policy": "independent_daily_reset_v1",
    }
    values.update(overrides)
    return ClosureSearchSpec(**values)


def _schedule(spec, *, dates=DATES, start="06:00", end="07:00") -> ClosureSchedule:
    intervals = tuple(
        ClosureInterval(work_date=day,
                        start_time=f"{day}T{start}:00",
                        end_time=f"{day}T{end}:00")
        for day in dates)
    minutes = sum(item.duration_minutes for item in intervals)
    identity = {
        "search_content_key": spec.content_key,
        "first_work_date": dates[0],
        "day_count": len(dates),
        "daily_start": start,
        "daily_end": end,
        "scheduled_work_minutes": minutes,
    }
    return ClosureSchedule(
        schedule_id="closure-" + _content_key(identity),
        search_content_key=spec.content_key,
        first_work_date=dates[0],
        day_count=len(dates),
        daily_start=start,
        daily_end=end,
        required_work_minutes=minutes,
        scheduled_work_minutes=minutes,
        actual_closed_minutes=minutes,
        rounding_overshoot_minutes=0,
        intervals=intervals,
    )


def _routes(vehicles):
    return "<routes>" + "".join(
        f'<vehicle id="{vid}" depart="{depart}">'
        f'<route edges="{" ".join(edges)}"/></vehicle>'
        for vid, depart, edges in vehicles) + "</routes>"


def _archive(directory: Path, required, body: str) -> Path:
    """An archive shaped like a real one for the envelope that needs it."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "demand_meta.json").write_text(json.dumps({
        "demand_build_key": required.build_key,
        "epoch_sim": f"{required.start_date}T00:00:00",
        "n_intervals": required.days * 96,
        "n_variants": 3,
    }), encoding="utf-8")
    for filename in VARIANT_FILENAMES.values():
        (directory / filename).write_text(body, encoding="utf-8")
    return directory


def _required_for(spec, daily_schedule, *, p99=3600, policy=None):
    envelope = build_simulation_envelope(
        spec, daily_schedule,
        baseline_trip_duration_p99_s=p99,
        policy=policy or EnvelopePolicy())
    return independent_daily_demand_spec(spec, daily_schedule, envelope)


def _release(path: Path, spec, entries, *, p99=3600, policy=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "monthly_demand_release",
        "request": {
            "search_content_key": spec.content_key,
            "baseline_trip_duration_p99_s": p99,
            "envelope_policy": {
                key: value for key, value in
                (policy or EnvelopePolicy()).__dict__.items()},
        },
        "entries": entries,
    }), encoding="utf-8")
    return path


class TestTheDecompositionMatchesTheSearch:
    def test_an_independent_daily_parent_is_split_per_work_day(self):
        """Priced exactly as the ranking priced it: one archive per day,
        through the production decomposition rather than a second one."""
        spec = _spec()
        units = run.unit_schedules(spec, _schedule(spec))
        assert [item.first_work_date for item in units] == list(DATES)
        assert all(item.day_count == 1 for item in units)

    def test_a_continuous_schedule_is_priced_in_one_piece(self):
        spec = _spec(interday_policy="continuous_v1",
                     max_consecutive_start_days=2)
        parent = _schedule(spec)
        assert run.unit_schedules(spec, parent) == (parent,)


class TestArchivesComeFromTheSearchsOwnRelease:
    def test_the_envelope_basis_is_read_from_the_release_not_guessed(
            self, tmp_path):
        """`baseline_trip_duration_p99_s` and the envelope policy decide which
        archive a schedule resolves to. Taking today's defaults would silently
        chart different demand whenever either changes."""
        spec = _spec()
        _release(tmp_path / "releases" / "r.json", spec, [], p99=7200)
        index = run.load_archive_index(
            spec, release_root=tmp_path / "releases", runs_root=tmp_path / "runs")
        assert index.baseline_trip_duration_p99_s == 7200

    def test_a_release_for_another_search_is_ignored(self, tmp_path):
        spec = _spec()
        other = _spec(required_work_minutes=180)
        _release(tmp_path / "releases" / "other.json", other, [], p99=7200)
        index = run.load_archive_index(
            spec, release_root=tmp_path / "releases", runs_root=tmp_path / "runs")
        assert index.baseline_trip_duration_p99_s == (
            run.FALLBACK_BASELINE_TRIP_P99_S)
        assert index.origin == "archive_scan"

    def test_two_releases_that_disagree_are_refused(self, tmp_path):
        """Two bases mean two different sets of archives; no single curve
        describes the search, so it says so instead of picking one."""
        spec = _spec()
        unit = run.unit_schedules(spec, _schedule(spec))[0]
        required = _required_for(spec, unit)
        entry = {"archive": str(tmp_path / "a"),
                 "demand_build_spec": {"build_key": required.build_key}}
        _release(tmp_path / "releases" / "a.json", spec, [entry], p99=3600)
        _release(tmp_path / "releases" / "b.json", spec, [entry], p99=7200)
        with pytest.raises(DelayProfileError):
            run.load_archive_index(
                spec, release_root=tmp_path / "releases",
                runs_root=tmp_path / "runs")

    def test_a_pinned_archive_that_is_gone_is_reported_not_replaced(
            self, tmp_path):
        """Quietly falling back to a scan would chart whatever archive happens
        to be lying around today instead of the one the search used."""
        spec = _spec()
        unit = run.unit_schedules(spec, _schedule(spec))[0]
        required = _required_for(spec, unit)
        _release(tmp_path / "releases" / "a.json", spec, [{
            "archive": str(tmp_path / "missing"),
            "demand_build_spec": {"build_key": required.build_key},
        }])
        index = run.load_archive_index(
            spec, release_root=tmp_path / "releases", runs_root=tmp_path / "runs")
        with pytest.raises(DelayProfileError, match="gone"):
            run.resolve_archive(spec, unit, index, runs_root=tmp_path / "runs")

    def test_a_relative_archive_path_resolves_against_the_manifest(
            self, tmp_path):
        """Releases may be copied; a relative path must never resolve against
        whatever directory the server happens to have been started in."""
        spec = _spec()
        unit = run.unit_schedules(spec, _schedule(spec))[0]
        required = _required_for(spec, unit)
        releases = tmp_path / "releases"
        _archive(releases / "archive-1", required, _routes([]))
        _release(releases / "a.json", spec, [{
            "archive": "archive-1",
            "demand_build_spec": {"build_key": required.build_key},
        }])
        index = run.load_archive_index(
            spec, release_root=releases, runs_root=tmp_path / "runs")
        assert run.resolve_archive(
            spec, unit, index, runs_root=tmp_path / "runs"
        ) == (releases / "archive-1").resolve()


class TestProfilingACandidate:
    def _profile(self, tmp_path, *, depart_clock_s, published, dates=DATES):
        """One chain vehicle per work day, departing at a given wall clock.

        The departure is expressed as a clock time on the WORK DATE and
        converted per archive, because an independent daily unit resolves a
        previous/current/next archive whose epoch is the day before: a raw
        offset would silently land on the wrong day and count nothing.
        """
        spec = _spec()
        parent = _schedule(spec, dates=dates)
        releases = tmp_path / "releases"
        entries = []
        for index, unit in enumerate(run.unit_schedules(spec, parent)):
            required = _required_for(spec, unit)
            offset = (date.fromisoformat(unit.first_work_date)
                      - date.fromisoformat(required.start_date)).days * 86_400
            archive = _archive(
                releases / f"archive-{index}", required,
                _routes([("v", offset + depart_clock_s, ["A", "B", "C", "D"])]))
            entries.append({"archive": str(archive),
                            "demand_build_spec": {
                                "build_key": required.build_key}})
        _release(releases / "release.json", spec, entries)
        archive_index = run.load_archive_index(
            spec, release_root=releases, runs_root=tmp_path / "runs")
        entry, step = run.profile_candidate(
            spec, parent, published,
            index=archive_index, network=FakeNetwork(),
            runs_root=tmp_path / "runs")
        return entry, step

    def test_each_day_contributes_its_own_vehicles_to_the_curve(self, tmp_path):
        """Two work days, one delayed driver each, 40 s apiece: the parent's
        curve carries two vehicles in the 30-45 s bar and 2/3600 h of delay."""
        # 40 s and 700 m added per driver (400 m chain -> 1100 m bypass),
        # one driver on each of the two work days.
        published = {"added_vehicle_hours": round(80 / 3600, 4),
                     "added_metres_total": 1400.0, "vehicles_affected": 2}
        entry, step = self._profile(
            tmp_path, depart_clock_s=21_900, published=published)
        q50 = entry["variants"]["q50"]
        assert q50["daily_unit_count"] == 2
        assert q50["vehicles"][bin_index(40.0)] == 2
        assert entry["cost_verification"]["matches"] is True
        assert step == 2 * len(("q10", "q50", "q90"))

    def test_the_curve_is_refused_when_it_misses_the_ranked_cost(self, tmp_path):
        """The published cost is the one shown in the table. A curve that
        cannot reproduce it is describing other demand, another network or an
        older result — all of which must surface, not be drawn over."""
        with pytest.raises(DelayProfileError, match="does not match"):
            self._profile(
                tmp_path, depart_clock_s=21_900,
                published={"added_vehicle_hours": 99.0,
                           "added_metres_total": 1400.0,
                           "vehicles_affected": 2})

    def test_a_vehicle_outside_the_closure_window_is_not_counted(self, tmp_path):
        """The closure runs 06:00-07:00 on each day; a 12:00 departure passes
        the edge while it is open and belongs on no bar at all."""
        entry, _step = self._profile(
            tmp_path, depart_clock_s=43_200,
            published={"added_vehicle_hours": 0.0, "added_metres_total": 0.0,
                       "vehicles_affected": 0})
        assert entry["variants"]["q50"]["vehicles"] == [0] * len(BINS)


class TestChoosingWhatToDraw:
    def _period(self, start_date, hours, schedule_id):
        return {
            "start_date": start_date,
            "status": "viable",
            "best_schedule": {"schedule_id": schedule_id},
            "best_cost": {"added_vehicle_hours": hours,
                          "added_metres_total": 0.0, "vehicles_affected": 1},
        }

    def test_the_two_best_dates_are_the_two_cheapest_not_the_two_earliest(self):
        spec = _spec()
        schedules = {
            day: _schedule(spec, dates=(day,), start=start, end=end)
            for day, start, end in (
                (DATES[0], "06:00", "08:00"), (DATES[1], "06:00", "08:00"))
        }
        periods = [
            {**self._period(DATES[0], 9.0, schedules[DATES[0]].schedule_id),
             "best_schedule": schedules[DATES[0]].to_dict()},
            {**self._period(DATES[1], 1.0, schedules[DATES[1]].schedule_id),
             "best_schedule": schedules[DATES[1]].to_dict()},
        ]
        result = {"period_comparison": {"periods": periods}}
        chosen = run.select_candidates(result, top_n=2)
        assert [schedule.first_work_date for schedule, _c, _m in chosen] == [
            DATES[1], DATES[0]]

    def test_a_search_without_periods_falls_back_to_its_schedules(self):
        spec = _spec()
        schedule = _schedule(spec, dates=(DATES[0],), start="06:00", end="08:00")
        result = {
            "shortlisted_schedules": [schedule.to_dict()],
            "winner_id": schedule.schedule_id,
            "robust_decision": {"candidates": [{
                "candidate_id": schedule.schedule_id,
                "closure_cost": {"added_vehicle_hours": 1.0,
                                 "added_metres_total": 0.0,
                                 "vehicles_affected": 3,
                                 "vehicles_no_detour": 0},
            }]},
        }
        chosen = run.select_candidates(result, top_n=2)
        assert len(chosen) == 1
        assert chosen[0][2]["contains_final_winner"] is True

    def test_a_disqualified_schedule_is_never_offered_as_a_best_date(self):
        """A candidate that stranded vehicles is refused, not expensive.
        Charting it invites exactly the comparison the gate exists to stop."""
        spec = _spec()
        schedule = _schedule(spec, dates=(DATES[0],), start="06:00", end="08:00")
        result = {
            "shortlisted_schedules": [schedule.to_dict()],
            "robust_decision": {"candidates": [{
                "candidate_id": schedule.schedule_id,
                "hard_failures": ["vehicles_no_detour"],
                "closure_cost": {"added_vehicle_hours": 0.1,
                                 "added_metres_total": 0.0,
                                 "vehicles_affected": 1,
                                 "vehicles_no_detour": 7},
            }]},
        }
        assert run.select_candidates(result, top_n=2) == ()


class TestPublishedProfileIdentity:
    def _payload(self, **overrides):
        payload = {
            "kind": "closure_delay_profile",
            "schema_version": run.SCHEMA_VERSION
            if hasattr(run, "SCHEMA_VERSION") else 1,
            "measure": "deterministic_detour_seconds_v1",
            "search_id": "abc",
            "search_content_key": "0123456789abcdef0123",
        }
        payload.update(overrides)
        return payload

    def test_a_profile_from_another_search_is_not_accepted(self):
        payload = self._payload()
        assert run.profile_matches_search(
            payload, "abc", "0123456789abcdef0123") is True
        assert run.profile_matches_search(
            payload, "def", "0123456789abcdef0123") is False
        assert run.profile_matches_search(payload, "abc", "other") is False

    def test_a_profile_of_another_measure_is_not_accepted(self):
        """A future SUMO-based profile would answer a different question with
        the same shape; the reader must not mistake one for the other."""
        assert run.profile_matches_search(
            self._payload(measure="sumo_time_loss_v1"),
            "abc", "0123456789abcdef0123") is False

    def test_writing_and_reading_round_trips(self, tmp_path):
        payload = self._payload()
        run.write_delay_profile(tmp_path, payload)
        assert run.read_delay_profile(tmp_path) == payload

    def test_a_missing_profile_reads_as_none(self, tmp_path):
        assert run.read_delay_profile(tmp_path) is None
