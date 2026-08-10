"""Preflight counts must equal what the real generator would enumerate.

A preflight that is merely close is worse than none: the user plans around a
number the search then contradicts. So almost every test here is DIFFERENTIAL —
it runs `generate_closure_schedules` and `decompose_schedules` for real and
demands equality, rather than asserting a hand-computed constant that could be
wrong in the same direction as the implementation.

Covered because each has bitten this calendar before: year boundaries, leap
days, DST transition dates, blackout dates, an overnight band that makes an
interval occupy two calendar dates, and the plan's own 07:30-15:15 closure
inside a 06:00-18:00 permitted band.
"""

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec
from traffic_sim.simulation.closure_preflight import (
    SIZE_LARGE_BUT_RUNNABLE, SIZE_NORMAL, SIZE_OVER_RESOURCE_BUDGET,
    UnsupportedPreflightSpec, eligible_dates, preflight)
from traffic_sim.simulation.independent_daily import decompose_schedules


def make_spec(**overrides) -> ClosureSearchSpec:
    """A rolling independent-day search; overrides keep each test one-line."""
    band = overrides.pop("band", ("06:00", "18:00"))
    payload = {
        "search_id": "preflight-case",
        "directed_edges": ["a_b_0"],
        "demand_build_id": "demand-key",
        "permitted_date_start": "2027-03-01",
        "permitted_date_end": "2027-03-31",
        "required_work_minutes": 480,
        "max_consecutive_start_days": 4,
        "permitted_daily_band": {"earliest_start": band[0],
                                 "latest_end": band[1]},
        "source": "forecast",
        "allowed_weekdays": [0, 1, 2, 3, 4],
        "interday_policy": "independent_daily_reset_v1",
        "work_allocation_policy": "exact_equal_daily_v1",
        "objective_profile": "displaced_vehicles_and_detour_v1",
    }
    payload.update(overrides)
    return ClosureSearchSpec.from_dict(payload)


def exhaustive_counts(spec: ClosureSearchSpec) -> dict:
    """Ground truth: what the production generator actually produces.

    Daily units exist only under `independent_daily_reset_v1`; the continuous
    policy has no decomposition, so that half of the comparison is skipped
    rather than faked.
    """
    schedules = generate_closure_schedules(spec)
    by_day_count: dict[int, int] = {}
    for schedule in schedules:
        by_day_count[schedule.day_count] = by_day_count.get(schedule.day_count, 0) + 1
    truth = {
        "parents": len(schedules),
        "by_day_count": by_day_count,
        "units": None,
        "unit_keys": None,
    }
    if spec.interday_policy == "independent_daily_reset_v1":
        units, _parents = decompose_schedules(spec, schedules)
        truth["units"] = len(units)
        truth["unit_keys"] = {
            (item.identity["work_date"], item.identity["start_time"],
             item.identity["end_time"], item.identity["duration_minutes"])
            for item in units
        }
    return truth


def assert_exact(spec: ClosureSearchSpec) -> dict:
    """Every reported count equals the enumerated truth. Returns the report."""
    truth = exhaustive_counts(spec)
    report = preflight(spec)
    assert report.parent_schedule_count == truth["parents"], "parent count"
    assert dict(report.parent_schedules_by_workday_count) == truth["by_day_count"], (
        "parent schedules grouped by workday count")
    assert sorted(report.valid_workday_counts) == sorted(truth["by_day_count"]), (
        "valid workday counts")
    if truth["units"] is not None:
        assert report.unique_daily_unit_count == truth["units"], "unique daily units"
    return report


class TestDifferentialAgainstTheRealGenerator:
    def test_a_plain_weekday_month(self):
        assert_exact(make_spec())

    def test_the_plans_0730_to_1515_closure_in_an_0600_1800_band(self):
        """The plan names this case explicitly: 465 minutes of work per day
        inside a twelve-hour band, which puts the closure at 07:30-15:15."""
        spec = make_spec(required_work_minutes=465,
                         max_consecutive_start_days=1)
        report = assert_exact(spec)
        starts = {schedule.daily_start
                  for schedule in generate_closure_schedules(spec)}
        assert "07:30" in starts and report.parent_schedule_count > 0

    def test_a_single_workday(self):
        assert_exact(make_spec(max_consecutive_start_days=1))

    def test_every_weekday_allowed_including_weekends(self):
        assert_exact(make_spec(allowed_weekdays=[0, 1, 2, 3, 4, 5, 6]))

    def test_blackout_dates_split_the_eligible_run(self):
        """A blackout in the middle must break windows on BOTH sides, not just
        remove one date from a total."""
        spec = make_spec(blackout_dates=["2027-03-10", "2027-03-11"])
        report = assert_exact(spec)
        assert report.eligible_workday_count == len(eligible_dates(spec))

    def test_a_year_boundary(self):
        assert_exact(make_spec(permitted_date_start="2026-12-15",
                               permitted_date_end="2027-01-15",
                               source="historical"))

    def test_a_leap_day(self):
        assert_exact(make_spec(permitted_date_start="2028-02-20",
                               permitted_date_end="2028-03-05",
                               source="historical"))

    def test_a_dst_spring_transition(self):
        """Europe/Stockholm springs forward on 2027-03-28; the default policy
        excludes it, so it must break the run exactly as a blackout does."""
        spec = make_spec(permitted_date_start="2027-03-22",
                         permitted_date_end="2027-04-05",
                         allowed_weekdays=[0, 1, 2, 3, 4, 5, 6])
        assert date(2027, 3, 28) not in set(eligible_dates(spec))
        assert_exact(spec)

    def test_a_dst_autumn_transition(self):
        spec = make_spec(permitted_date_start="2027-10-25",
                         permitted_date_end="2027-11-05",
                         allowed_weekdays=[0, 1, 2, 3, 4, 5, 6])
        assert date(2027, 10, 31) not in set(eligible_dates(spec))
        assert_exact(spec)

    def test_an_overnight_band_makes_intervals_occupy_two_dates(self):
        """22:00-06:00 wraps midnight. A start of 23:00 with a four-hour shift
        occupies the FOLLOWING date too, which must itself be allowed."""
        spec = make_spec(band=("22:00", "06:00"), required_work_minutes=240,
                         max_consecutive_start_days=2,
                         work_allocation_policy="equal_daily_rounded_v1",
                         interday_policy="continuous_v1",
                         allowed_weekdays=[0, 1, 2, 3, 4])
        assert_exact(spec)

    def test_an_overnight_band_with_every_day_allowed(self):
        assert_exact(make_spec(band=("22:00", "06:00"),
                               required_work_minutes=240,
                               max_consecutive_start_days=3,
                               work_allocation_policy="equal_daily_rounded_v1",
                               interday_policy="continuous_v1",
                               allowed_weekdays=[0, 1, 2, 3, 4, 5, 6]))

    def test_a_work_time_that_does_not_divide_evenly(self):
        """exact_equal_daily_v1 refuses day counts that cannot split the work
        into equal 15-minute shifts; the preflight must refuse the same ones."""
        spec = make_spec(required_work_minutes=450,
                         max_consecutive_start_days=6)
        report = assert_exact(spec)
        assert 4 not in report.valid_workday_counts     # 30 blocks / 4 = 7 r2

    def test_a_contract_that_admits_no_schedule_at_all(self):
        """More work than the band can hold on any allowed day count."""
        report = assert_exact(make_spec(required_work_minutes=1200,
                                        max_consecutive_start_days=1))
        assert report.parent_schedule_count == 0
        assert any("no schedule" in note for note in report.notes)

    def test_the_calendar_consecutive_rounded_policy(self):
        """equal_daily_rounded_v1 walks CALENDAR-consecutive dates, so a
        weekend inside a window kills it rather than being skipped over."""
        assert_exact(make_spec(work_allocation_policy="equal_daily_rounded_v1",
                               interday_policy="continuous_v1",
                               max_consecutive_start_days=5))

    def test_rounded_policy_with_blackouts_and_weekends(self):
        assert_exact(make_spec(work_allocation_policy="equal_daily_rounded_v1",
                               interday_policy="continuous_v1",
                               allowed_weekdays=[0, 1, 2, 3, 4, 5, 6],
                               blackout_dates=["2027-03-12"],
                               max_consecutive_start_days=6))


class TestUnitIdentitiesMatch:
    def test_the_exact_unit_tuples_match_not_only_their_count(self):
        """A matching total with different tuples would still break the cache
        lookup this feeds, so identity is compared element by element."""
        from traffic_sim.simulation.closure_preflight import _unit_lookup_key
        spec = make_spec(max_consecutive_start_days=3)
        truth = exhaustive_counts(spec)
        # Recompute the preflight's own unit set through its public key helper.
        report = preflight(spec)
        assert report.unique_daily_unit_count == len(truth["unit_keys"])
        # Rebuild from the generator side and confirm the helper agrees on the
        # formatting the identity is keyed on.
        for work_date, start_time, end_time, duration in truth["unit_keys"]:
            minute = int(start_time[11:13]) * 60 + int(start_time[14:16])
            assert _unit_lookup_key(
                date.fromisoformat(work_date), minute, duration
            ) == (work_date, start_time, end_time, duration)


class TestPropertyRandomised:
    @pytest.mark.parametrize("seed", range(25))
    def test_preflight_equals_exhaustive_for_random_small_cases(self, seed):
        """The plan's property test: random small contracts, exact equality."""
        rng = random.Random(seed)
        start = date(2027, 1, 1) + timedelta(days=rng.randrange(0, 320))
        span = rng.randrange(3, 40)
        weekdays = sorted(rng.sample(range(7), rng.randrange(1, 8)))
        blackouts = []
        for _ in range(rng.randrange(0, 4)):
            offset = rng.randrange(0, span + 1)
            blackouts.append((start + timedelta(days=offset)).isoformat())
        # `exact_equal_daily_v1` implies the independent daily reset, whose
        # contract requires a SAME-DAY band. Overnight bands are randomised in
        # the rounded/continuous case below, where they are legal.
        band_start = rng.randrange(0, 18) * 60
        band_length = rng.randrange(4, 14) * 60
        band_end = min(band_start + band_length, 24 * 60 - 60)
        quarters = rng.randrange(1, 40)
        spec = make_spec(
            permitted_date_start=start.isoformat(),
            permitted_date_end=(start + timedelta(days=span)).isoformat(),
            required_work_minutes=quarters * 15,
            max_consecutive_start_days=rng.randrange(1, 7),
            allowed_weekdays=weekdays,
            blackout_dates=sorted(set(blackouts)),
            band=(f"{band_start // 60:02d}:{band_start % 60:02d}",
                  f"{band_end // 60:02d}:{band_end % 60:02d}"),
        )
        assert_exact(spec)

    @pytest.mark.parametrize("seed", range(15))
    def test_rounded_policy_random_cases(self, seed):
        """The continuous policy also admits OVERNIGHT bands, so this arm
        randomises them: an interval that wraps midnight must find the
        following calendar date allowed too."""
        rng = random.Random(1000 + seed)
        start = date(2025, 1, 1) + timedelta(days=rng.randrange(0, 320))
        span = rng.randrange(3, 30)
        band_start = rng.randrange(0, 24) * 60
        band_end = (band_start + rng.randrange(4, 14) * 60) % (24 * 60)
        spec = make_spec(
            source="historical",
            work_allocation_policy="equal_daily_rounded_v1",
            interday_policy="continuous_v1",
            permitted_date_start=start.isoformat(),
            permitted_date_end=(start + timedelta(days=span)).isoformat(),
            required_work_minutes=rng.randrange(1, 30) * 15,
            max_consecutive_start_days=rng.randrange(1, 6),
            allowed_weekdays=sorted(rng.sample(range(7), rng.randrange(1, 8))),
            band=(f"{band_start // 60:02d}:{band_start % 60:02d}",
                  f"{band_end // 60:02d}:{band_end % 60:02d}"),
        )
        assert_exact(spec)


def production_outside(spec: ClosureSearchSpec) -> int:
    """Units the REAL envelope builder puts outside the downloaded year."""
    from traffic_sim.simulation.envelope import build_simulation_envelope
    from traffic_sim.simulation.independent_daily import (
        INDEPENDENT_DAILY_ENVELOPE_POLICY)
    year = 2027 if spec.source == "forecast" else 2025
    units, _ = decompose_schedules(spec, generate_closure_schedules(spec))
    outside = 0
    for unit in units:
        try:
            envelope = build_simulation_envelope(
                spec, unit.schedule, baseline_trip_duration_p99_s=3600,
                policy=INDEPENDENT_DAILY_ENVELOPE_POLICY)
        except ValueError:
            outside += 1
            continue
        if (date.fromisoformat(envelope.scenario_start[:10]) < date(year, 1, 1)
                or date.fromisoformat(envelope.scenario_end[:10])
                > date(year + 1, 1, 1)):
            outside += 1
    return outside


class TestDemandYearEnvelope:
    def test_units_needing_recovery_past_new_year_are_reported(self):
        """A late closure on 31 December drains six hours into 1 January and
        then rounds up to 2 January, which the 2027 demand does not cover."""
        spec = make_spec(permitted_date_start="2027-12-27",
                         permitted_date_end="2027-12-31",
                         band=("18:00", "23:00"),
                         required_work_minutes=240,
                         allowed_weekdays=[0, 1, 2, 3, 4, 5, 6],
                         max_consecutive_start_days=1)
        report = preflight(spec)
        assert report.units_outside_demand_year > 0
        assert report.units_outside_demand_year == production_outside(spec)
        assert report.executable_daily_unit_count == (
            report.unique_daily_unit_count - report.units_outside_demand_year)
        assert any("demand year" in note for note in report.notes)

    def test_units_needing_warmup_before_new_year_are_reported(self):
        """A midnight start reaches back past 1 January for its warm-up."""
        spec = make_spec(permitted_date_start="2027-01-01",
                         permitted_date_end="2027-01-03",
                         band=("00:00", "12:00"),
                         required_work_minutes=240,
                         allowed_weekdays=[0, 1, 2, 3, 4, 5, 6],
                         max_consecutive_start_days=1)
        report = preflight(spec)
        assert report.units_outside_demand_year > 0
        assert report.units_outside_demand_year == production_outside(spec)

    def test_a_mid_year_search_has_every_unit_executable(self):
        report = preflight(make_spec())
        assert report.units_outside_demand_year == 0
        assert report.executable_daily_unit_count == report.unique_daily_unit_count

    def test_the_envelope_classification_matches_production(self):
        """Differential against `build_simulation_envelope` itself, so the
        date-relative shortcut cannot drift from the real envelope builder."""
        from traffic_sim.simulation.envelope import build_simulation_envelope
        from traffic_sim.simulation.independent_daily import (
            INDEPENDENT_DAILY_ENVELOPE_POLICY)
        spec = make_spec(permitted_date_start="2027-12-20",
                         permitted_date_end="2027-12-31",
                         allowed_weekdays=[0, 1, 2, 3, 4, 5, 6],
                         max_consecutive_start_days=1)
        units, _ = decompose_schedules(spec, generate_closure_schedules(spec))
        outside = 0
        for unit in units:
            try:
                envelope = build_simulation_envelope(
                    spec, unit.schedule,
                    baseline_trip_duration_p99_s=3600,
                    policy=INDEPENDENT_DAILY_ENVELOPE_POLICY)
            except ValueError:
                outside += 1
                continue
            start = date.fromisoformat(envelope.scenario_start[:10])
            end = date.fromisoformat(envelope.scenario_end[:10])
            if start < date(2027, 1, 1) or end > date(2028, 1, 1):
                outside += 1
        assert preflight(spec).units_outside_demand_year == outside


class TestCacheReporting:
    def test_a_read_only_preflight_reports_unknown_not_a_false_miss(self):
        """The daily backend identity only exists after the demand resolver has
        prepared an archive. Calling every unit a miss would understate the
        cache; calling them hits would overstate it."""
        report = preflight(make_spec())
        assert report.cache_unknown == report.executable_daily_unit_count
        assert report.cache_hits == 0 and report.cache_misses == 0
        assert "cache_unknown" in report.cache_basis

    def test_known_identities_produce_exact_hits_and_misses(self, tmp_path):
        from traffic_sim.simulation.closure_preflight import _unit_lookup_key
        spec = make_spec(max_consecutive_start_days=1,
                         permitted_date_start="2027-03-01",
                         permitted_date_end="2027-03-03")
        units, _ = decompose_schedules(spec, generate_closure_schedules(spec))
        keys = {}
        for index, unit in enumerate(units):
            identity = unit.identity
            key = f"{index:064x}"
            keys[(identity["work_date"], identity["start_time"],
                  identity["end_time"], identity["duration_minutes"])] = key
            if index % 2 == 0:                       # half the units are cached
                entry = tmp_path / key[:2] / f"{key}.json"
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text("{}")
        report = preflight(spec, cache_root=tmp_path, unit_cache_keys=keys)
        assert report.cache_unknown == 0
        assert report.cache_hits + report.cache_misses == len(units)
        assert report.cache_hits == len(range(0, len(units), 2))
        assert report.estimated_sumo_daily_units == (
            report.executable_daily_unit_count - report.cache_hits)
        assert _unit_lookup_key(date(2027, 3, 1), 360, 480)[0] == "2027-03-01"

    def test_estimated_workload_names_its_basis(self):
        report = preflight(make_spec())
        assert "seed and demand-variant repetitions" in report.estimation_basis


class TestSizeClassification:
    def test_a_small_search_is_normal(self):
        assert preflight(make_spec()).size_class == SIZE_NORMAL

    def test_past_half_a_cap_is_large_but_runnable(self):
        units = preflight(make_spec()).unique_daily_unit_count
        # A cap this search fits inside, but by less than a factor of two.
        report = preflight(make_spec(), daily_unit_limit=int(units * 1.5))
        assert report.size_class == SIZE_LARGE_BUT_RUNNABLE

    def test_over_a_cap_is_over_budget_and_says_which(self):
        report = preflight(make_spec(), daily_unit_limit=1)
        assert report.size_class == SIZE_OVER_RESOURCE_BUDGET
        assert any("unique daily units" in reason
                   for reason in report.limit_reasons)

    def test_the_default_caps_are_the_production_caps(self):
        """PR B must not raise or bypass the existing limit."""
        report = preflight(make_spec())
        assert report.daily_unit_limit == 10_000
        assert report.parent_schedule_limit == 100_000


class TestContract:
    def test_the_record_is_versioned_and_serializable(self):
        payload = preflight(make_spec()).to_dict()
        assert payload["schema"] == "closure_search_preflight_v1"
        assert payload["exact"] is True
        assert set(payload["cache"]) == {"hits", "misses", "unknown", "basis"}
        assert payload["counting_method"] == "run_length_windows_v1"
        import json
        json.dumps(payload)                       # must round-trip as JSON

    def test_an_unsupported_allocation_policy_is_refused_not_approximated(self):
        spec = make_spec(work_allocation_policy="exact_balanced_daily_v1")
        with pytest.raises(UnsupportedPreflightSpec):
            preflight(spec)

    def test_a_non_spec_argument_is_refused(self):
        with pytest.raises(TypeError):
            preflight({"search_id": "x"})


class TestNoSideEffects:
    def test_preflight_writes_nothing(self, tmp_path, monkeypatch):
        """The whole contract is read-only. Anything that opened a file for
        writing during a preflight would break it."""
        monkeypatch.chdir(tmp_path)
        opened: list[str] = []
        real_open = open

        def watched(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                opened.append(f"{file}:{mode}")
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", watched)
        preflight(make_spec())
        assert opened == []
        assert list(tmp_path.iterdir()) == []
