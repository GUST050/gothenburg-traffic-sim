from datetime import date
import itertools

import pytest

from traffic_sim.core.closure_calendar import (
    expand_schedule_closures,
    generate_closure_schedules,
    iter_closure_schedules,
    is_dst_transition_date,
    validate_connected_worksite,
)
from traffic_sim.core.contracts import (
    ClosureSchedule,
    ClosureSearchSpec,
    DailyTimeBand,
    load_closure_search_spec,
    write_closure_search_spec,
)


def _spec(**overrides):
    values = {
        "search_id": "work-april",
        "directed_edges": ("a_b_0",),
        "demand_build_id": "forecast-2027",
        "source": "forecast",
        "permitted_date_start": "2027-04-05",
        "permitted_date_end": "2027-04-09",
        "required_work_minutes": 30 * 60,
        "max_consecutive_start_days": 5,
        "permitted_daily_band": DailyTimeBand("08:00", "18:00"),
        "allowed_weekdays": (0, 1, 2, 3, 4),
    }
    values.update(overrides)
    return ClosureSearchSpec(**values)


def test_search_contract_round_trips_and_detects_tampering(tmp_path):
    spec = _spec(blackout_dates=("2027-04-07",))
    path = tmp_path / "closure-search.json"

    write_closure_search_spec(path, spec)

    assert load_closure_search_spec(path) == spec
    assert spec.to_dict()["kind"] == "closure_search"
    tampered = {**spec.to_dict(), "required_work_minutes": 60}
    with pytest.raises(ValueError, match="content_key"):
        ClosureSearchSpec.from_dict(tampered)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("directed_edges", "a_b_0", "list of strings"),
        ("allowed_weekdays", "01234", "integer weekdays"),
        ("blackout_dates", "2027-04-07", "list of strings"),
    ],
)
def test_search_json_list_fields_reject_bare_strings(field, value, error):
    payload = _spec().to_dict()
    payload.pop("content_key")
    payload[field] = value

    with pytest.raises(ValueError, match=error):
        ClosureSearchSpec.from_dict(payload)


def test_content_key_describes_intent_not_label_or_edge_order():
    first = _spec(
        search_id="first",
        directed_edges=("a_b_0", "b_a_0"),
    )
    second = _spec(
        search_id="second",
        directed_edges=("b_a_0", "a_b_0"),
    )

    assert first.content_key == second.content_key


def test_default_minimum_preserves_legacy_identity_and_exact_length_is_opt_in():
    legacy = _spec()
    explicit_default = _spec(min_consecutive_start_days=1)
    exact_five = _spec(min_consecutive_start_days=5)

    assert explicit_default.content_key == legacy.content_key
    assert "min_consecutive_start_days" not in legacy.to_dict()
    assert exact_five.to_dict()["min_consecutive_start_days"] == 5
    assert {item.day_count for item in generate_closure_schedules(exact_five)} == {5}


def test_minimum_days_cannot_exceed_maximum_days():
    with pytest.raises(ValueError, match="through max_consecutive"):
        _spec(min_consecutive_start_days=6)


def test_rolling_period_can_cross_week_and_month_boundary():
    common = {
        "permitted_date_start": "2027-07-30",  # Friday
        "permitted_date_end": "2027-08-02",    # Monday, next ISO week
        "required_work_minutes": 2 * 60,
        "max_consecutive_start_days": 2,
        "permitted_daily_band": DailyTimeBand("08:00", "09:00"),
        "allowed_weekdays": (0, 4),
        "interday_policy": "independent_daily_reset_v1",
        "work_allocation_policy": "exact_equal_daily_v1",
    }
    rolling = _spec(**common, period_comparison_policy="rolling_period_v1")

    schedules = generate_closure_schedules(rolling)
    assert len(schedules) == 1
    assert [item.work_date for item in schedules[0].intervals] == [
        "2027-07-30", "2027-08-02"
    ]
    assert rolling.to_dict()["period_comparison_policy"] == "rolling_period_v1"


def test_rolling_period_comparison_requires_independent_daily_evaluation():
    with pytest.raises(ValueError, match="independent_daily_reset_v1"):
        _spec(period_comparison_policy="rolling_period_v1")


def test_rolling_period_requires_the_same_times_every_workday():
    with pytest.raises(ValueError, match="same start and end time"):
        _spec(
            interday_policy="independent_daily_reset_v1",
            work_allocation_policy="exact_balanced_daily_v1",
            period_comparison_policy="rolling_period_v1",
        )

    spec = _spec(
        permitted_date_start="2027-07-01",
        permitted_date_end="2027-07-04",
        required_work_minutes=3 * 60,
        max_consecutive_start_days=2,
        permitted_daily_band=DailyTimeBand("08:00", "12:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        period_comparison_policy="rolling_period_v1",
    )
    schedules = generate_closure_schedules(spec)

    assert {item.day_count for item in schedules} == {1, 2}
    for schedule in schedules:
        assert len({item.start_time[11:16] for item in schedule.intervals}) == 1
        assert len({item.end_time[11:16] for item in schedule.intervals}) == 1
        assert len({item.duration_minutes for item in schedule.intervals}) == 1


def test_rolling_period_searches_exact_times_inside_permitted_daily_band():
    spec = _spec(
        permitted_date_start="2027-07-01",
        permitted_date_end="2027-07-02",
        required_work_minutes=2 * (7 * 60 + 45),
        max_consecutive_start_days=2,
        permitted_daily_band=DailyTimeBand("06:00", "18:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        period_comparison_policy="rolling_period_v1",
    )

    matching = [
        schedule for schedule in generate_closure_schedules(spec)
        if schedule.day_count == 2
        and schedule.daily_start == "07:30"
        and schedule.daily_end == "15:15"
    ]
    assert len(matching) == 1
    assert {item.start_time[11:16] for item in matching[0].intervals} == {
        "07:30"
    }
    assert {item.end_time[11:16] for item in matching[0].intervals} == {
        "15:15"
    }


def test_eight_workday_period_crosses_month_boundary():
    spec = _spec(
        permitted_date_start="2027-07-26",
        permitted_date_end="2027-08-04",
        required_work_minutes=8 * 60,
        max_consecutive_start_days=8,
        permitted_daily_band=DailyTimeBand("08:00", "09:00"),
        allowed_weekdays=(0, 1, 2, 3, 4),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        period_comparison_policy="rolling_period_v1",
    )

    schedules = generate_closure_schedules(spec)
    assert len(schedules) == 1
    assert schedules[0].day_count == 8
    assert schedules[0].first_work_date == "2027-07-26"
    assert schedules[0].intervals[-1].work_date == "2027-08-04"


def test_thirty_workday_exact_equal_period_is_supported():
    spec = _spec(
        permitted_date_start="2027-07-01",
        permitted_date_end="2027-08-11",
        required_work_minutes=30 * 8 * 60,
        max_consecutive_start_days=30,
        permitted_daily_band=DailyTimeBand("08:00", "16:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        period_comparison_policy="rolling_period_v1",
    )

    schedules = generate_closure_schedules(spec)
    thirty_day = [item for item in schedules if item.day_count == 30]
    assert len(thirty_day) == 13
    assert all(item.actual_closed_minutes == 30 * 8 * 60
               for item in thirty_day)
    assert all({interval.duration_minutes for interval in item.intervals}
               == {8 * 60} for item in thirty_day)


def test_long_period_contract_is_bounded_and_requires_exact_equal_days():
    common = {
        "max_consecutive_start_days": 30,
        "interday_policy": "independent_daily_reset_v1",
        "period_comparison_policy": "rolling_period_v1",
    }
    with pytest.raises(ValueError, match="exact_equal_daily_v1"):
        _spec(**common, work_allocation_policy="exact_balanced_daily_v1")
    with pytest.raises(ValueError, match="1 through 90"):
        _spec(
            **{**common, "max_consecutive_start_days": 91},
            work_allocation_policy="exact_equal_daily_v1",
        )


def test_thirty_work_hours_generate_only_feasible_equal_daily_shifts():
    schedules = generate_closure_schedules(_spec())

    assert {schedule.day_count for schedule in schedules} == {3, 4, 5}
    assert all(schedule.actual_closed_minutes
               == schedule.scheduled_work_minutes for schedule in schedules)
    assert all(schedule.scheduled_work_minutes >= 30 * 60
               for schedule in schedules)
    assert {
        schedule.intervals[0].duration_minutes
        for schedule in schedules
        if schedule.day_count == 5
    } == {6 * 60}
    assert {
        schedule.intervals[0].duration_minutes
        for schedule in schedules
        if schedule.day_count == 4
    } == {7 * 60 + 30}
    assert {
        schedule.intervals[0].duration_minutes
        for schedule in schedules
        if schedule.day_count == 3
    } == {10 * 60}


def test_intervals_use_identical_clock_times_consecutive_dates_and_open_between():
    schedule = next(
        item for item in generate_closure_schedules(_spec())
        if item.day_count == 5
        and item.first_work_date == "2027-04-05"
        and item.daily_start == "08:00"
    )

    assert [interval.work_date for interval in schedule.intervals] == [
        "2027-04-05",
        "2027-04-06",
        "2027-04-07",
        "2027-04-08",
        "2027-04-09",
    ]
    assert all(interval.start_time.endswith("T08:00:00")
               for interval in schedule.intervals)
    assert all(interval.end_time.endswith("T14:00:00")
               for interval in schedule.intervals)
    assert all(
        left.end_time < right.start_time
        for left, right in zip(schedule.intervals, schedule.intervals[1:])
    )


def test_rounding_is_per_equal_daily_shift_and_is_reported():
    spec = _spec(
        permitted_date_start="2027-04-05",
        permitted_date_end="2027-04-06",
        required_work_minutes=31,
        max_consecutive_start_days=2,
        permitted_daily_band=DailyTimeBand("08:00", "09:00"),
    )

    schedule = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "08:00"
    )

    assert [item.duration_minutes for item in schedule.intervals] == [30, 30]
    assert schedule.scheduled_work_minutes == 60
    assert schedule.actual_closed_minutes == 60
    assert schedule.rounding_overshoot_minutes == 29


def test_independent_daily_exact_allocation_never_overshoots_work():
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-10",
        required_work_minutes=50 * 60,
        max_consecutive_start_days=10,
        permitted_daily_band=DailyTimeBand("15:00", "22:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_balanced_daily_v1",
    )

    schedules = generate_closure_schedules(spec)

    assert {item.day_count for item in schedules} == {8, 9, 10}
    assert all(item.scheduled_work_minutes == 50 * 60 for item in schedules)
    assert all(item.rounding_overshoot_minutes == 0 for item in schedules)
    nine_day = [
        item for item in schedules
        if item.day_count == 9 and item.first_work_date == "2027-01-01"
    ]
    assert len(nine_day) == 36 * 6
    assert {
        tuple(interval.duration_minutes for interval in item.intervals)
        for item in nine_day
    } == {
        tuple(345 if index in longer else 330 for index in range(9))
        for longer in itertools.combinations(range(9), 2)
    }
    assert all(item.daily_end == "20:45" for item in nine_day
               if item.daily_start == "15:00")
    assert ClosureSchedule.from_dict(nine_day[0].to_dict()) == nine_day[0]


def test_independent_daily_policy_rejects_overnight_or_unaligned_work():
    with pytest.raises(ValueError, match="same-day permitted band"):
        _spec(
            permitted_daily_band=DailyTimeBand("22:00", "06:00"),
            interday_policy="independent_daily_reset_v1",
        )
    with pytest.raises(ValueError, match="align to the 15-minute"):
        _spec(
            required_work_minutes=61,
            interday_policy="independent_daily_reset_v1",
            work_allocation_policy="exact_balanced_daily_v1",
        )


def test_independent_daily_policy_accepts_a_full_local_day_band():
    spec = _spec(
        permitted_daily_band=DailyTimeBand("00:00", "24:00"),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_balanced_daily_v1",
    )
    assert spec.permitted_daily_band.latest_end == "24:00"


def test_independent_daily_exact_allocation_skips_non_workdays():
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-15",
        required_work_minutes=50 * 60,
        max_consecutive_start_days=10,
        permitted_daily_band=DailyTimeBand("15:00", "22:00"),
        allowed_weekdays=(0, 1, 2, 3, 4),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_balanced_daily_v1",
    )

    schedule = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 10
        and item.first_work_date == "2027-01-01"
        and item.daily_start == "15:00"
    )

    assert [item.work_date for item in schedule.intervals] == [
        "2027-01-01",
        "2027-01-04",
        "2027-01-05",
        "2027-01-06",
        "2027-01-07",
        "2027-01-08",
        "2027-01-11",
        "2027-01-12",
        "2027-01-13",
        "2027-01-14",
    ]
    assert ClosureSchedule.from_dict(schedule.to_dict()) == schedule

    forged = schedule.to_dict()
    forged["intervals"][1]["work_date"] = "2027-01-03"
    forged["intervals"][1]["start_time"] = "2027-01-03T15:00:00"
    forged["intervals"][1]["end_time"] = "2027-01-03T20:00:00"
    with pytest.raises(ValueError, match="schedule contents"):
        ClosureSchedule.from_dict(forged)


def test_month_end_may_be_crossed_only_when_both_dates_are_permitted():
    two_dates = _spec(
        permitted_date_start="2027-01-31",
        permitted_date_end="2027-02-01",
        required_work_minutes=4 * 60,
        max_consecutive_start_days=2,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )
    one_date = _spec(
        permitted_date_start="2027-01-31",
        permitted_date_end="2027-01-31",
        required_work_minutes=4 * 60,
        max_consecutive_start_days=2,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )

    assert any(schedule.day_count == 2
               for schedule in generate_closure_schedules(two_dates))
    assert not any(schedule.day_count == 2
                   for schedule in generate_closure_schedules(one_date))


def test_leap_day_is_a_real_consecutive_work_date():
    spec = _spec(
        permitted_date_start="2028-02-28",
        permitted_date_end="2028-02-29",
        required_work_minutes=4 * 60,
        max_consecutive_start_days=2,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )

    schedule = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2
    )

    assert [item.work_date for item in schedule.intervals] == [
        "2028-02-28", "2028-02-29"]


def test_weekday_or_blackout_inside_sequence_invalidates_whole_candidate():
    base = dict(
        permitted_date_start="2027-04-05",
        permitted_date_end="2027-04-07",
        required_work_minutes=6 * 60,
        max_consecutive_start_days=3,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
        allowed_weekdays=(0, 1, 2, 3, 4),
    )
    permitted = _spec(**base)
    blocked = _spec(**base, blackout_dates=("2027-04-06",))

    assert any(item.day_count == 3
               for item in generate_closure_schedules(permitted))
    assert generate_closure_schedules(blocked) == ()


def test_swedish_dst_transition_dates_are_excluded():
    assert is_dst_transition_date(
        date(2027, 3, 28), "Europe/Stockholm") is True
    assert is_dst_transition_date(
        date(2027, 3, 29), "Europe/Stockholm") is False
    spec = _spec(
        permitted_date_start="2027-03-27",
        permitted_date_end="2027-03-29",
        required_work_minutes=2 * 60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )

    schedules = generate_closure_schedules(spec)

    assert {item.first_work_date for item in schedules} == {
        "2027-03-27", "2027-03-29"}


def test_overnight_shift_must_fit_range_and_every_touched_date():
    spec = _spec(
        permitted_date_start="2027-05-01",
        permitted_date_end="2027-05-02",
        required_work_minutes=8 * 60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("22:00", "06:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )

    schedules = generate_closure_schedules(spec)

    assert len(schedules) == 1
    assert schedules[0].first_work_date == "2027-05-01"
    assert schedules[0].daily_start == "22:00"
    assert schedules[0].daily_end == "06:00"
    assert schedules[0].intervals[0].end_time == "2027-05-02T06:00:00"

    blocked = _spec(
        permitted_date_start="2027-05-01",
        permitted_date_end="2027-05-02",
        required_work_minutes=8 * 60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("22:00", "06:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
        blackout_dates=("2027-05-02",),
    )
    assert generate_closure_schedules(blocked) == ()


def test_early_morning_start_is_valid_inside_overnight_band():
    spec = _spec(
        permitted_date_start="2027-05-03",
        permitted_date_end="2027-05-03",
        required_work_minutes=4 * 60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("22:00", "06:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )

    schedule = next(
        item for item in generate_closure_schedules(spec)
        if item.daily_start == "02:00"
    )

    assert schedule.daily_end == "06:00"
    assert schedule.intervals[0].start_time == "2027-05-03T02:00:00"
    assert schedule.intervals[0].end_time == "2027-05-03T06:00:00"


def _whole_days(first: str, last: str, day_count: int):
    """A spec whose total work exactly fills `day_count` calendar days."""
    return _spec(
        permitted_date_start=first,
        permitted_date_end=last,
        required_work_minutes=day_count * 24 * 60,
        min_consecutive_start_days=day_count,
        max_consecutive_start_days=day_count,
        permitted_daily_band=DailyTimeBand("00:00", "24:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )


def test_a_single_whole_day_closes_the_whole_calendar_day():
    schedule = generate_closure_schedules(
        _whole_days("2027-05-03", "2027-05-03", 1))[0]

    assert schedule.daily_start == "00:00"
    assert schedule.daily_end == "24:00"
    assert schedule.actual_closed_minutes == 24 * 60


def test_whole_days_run_back_to_back_as_one_continuous_closure():
    """A whole day means the road is shut for that entire calendar day, so
    consecutive whole days are ONE continuous closure. The absence of an
    opening between them is the request, not a contract breach -- the old
    rule refused these outright, leaving 'seven whole days' expressible only
    as seven 23:45 shifts, which is a different closure with six openings."""
    schedules = generate_closure_schedules(
        _whole_days("2027-05-03", "2027-05-09", 7))

    assert len(schedules) == 1
    schedule = schedules[0]
    assert schedule.day_count == 7
    assert schedule.actual_closed_minutes == 7 * 24 * 60
    assert len(schedule.intervals) == 7
    # Each interval covers one whole calendar day...
    assert [item.work_date for item in schedule.intervals] == [
        f"2027-05-{day:02d}" for day in range(3, 10)]
    # ...and every day's end is the next day's start: no gap, no overlap.
    ends = [item.end_time for item in schedule.intervals[:-1]]
    starts = [item.start_time for item in schedule.intervals[1:]]
    assert ends == starts
    assert schedule.intervals[0].start_time == "2027-05-03T00:00:00"
    assert schedule.intervals[-1].end_time == "2027-05-10T00:00:00"


def test_a_whole_day_closure_has_no_start_time_to_choose():
    """The hours inside a fully closed day carry no decision, so the search
    must not spend candidates enumerating them."""
    schedules = generate_closure_schedules(
        _whole_days("2027-05-03", "2027-05-09", 7))

    assert {(item.daily_start, item.daily_end) for item in schedules} == {
        ("00:00", "24:00")}


def test_a_shift_longer_than_a_day_is_still_impossible():
    # 30 h of work that must fit in ONE day cannot be scheduled at all; the
    # guard states this rather than leaving it to band arithmetic.
    impossible = _spec(
        permitted_date_start="2027-05-03",
        permitted_date_end="2027-05-03",
        required_work_minutes=30 * 60,
        min_consecutive_start_days=1,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("00:00", "24:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )

    assert generate_closure_schedules(impossible) == ()


@pytest.mark.parametrize(
    "override, error",
    [
        ({"directed_edges": ("a_b_0", "a_b_0")}, "unique"),
        ({"source": "invented"}, "source"),
        ({"timezone": "UTC"}, "Europe/Stockholm"),
        ({"required_work_minutes": True}, "integer"),
        ({"required_work_minutes": 0}, "positive"),
        ({"max_consecutive_start_days": 22}, "1 through 21"),
        ({"allowed_weekdays": ()}, "0 through 6"),
        ({"same_daily_window": False}, "must be true"),
        ({"resolution_minutes": 5}, "must be 15"),
        ({"closure_type": "lane"}, "must be full"),
        ({"work_to_closure_assumption": "productivity"}, "one_to_one"),
        ({
            "permitted_daily_band": DailyTimeBand("08:10", "18:00")
        }, "align to 15"),
    ],
)
def test_search_contract_rejects_unsupported_or_ambiguous_inputs(
    override, error
):
    with pytest.raises(ValueError, match=error):
        _spec(**override)


def test_zero_length_daily_band_is_rejected():
    with pytest.raises(ValueError, match="non-zero"):
        DailyTimeBand("08:00", "08:00")


def test_schedule_round_trip_and_exact_per_edge_expansion():
    spec = _spec(
        directed_edges=("a_b_0", "b_a_0"),
        permitted_date_start="2027-05-03",
        permitted_date_end="2027-05-03",
        required_work_minutes=2 * 60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )
    schedule = generate_closure_schedules(spec)[0]

    assert ClosureSchedule.from_dict(schedule.to_dict()) == schedule
    closures = expand_schedule_closures(spec, schedule)
    assert [item.edge_id for item in closures] == ["a_b_0", "b_a_0"]
    assert {item.start_time for item in closures} == {
        "2027-05-03T08:00:00"}
    assert {item.end_time for item in closures} == {
        "2027-05-03T10:00:00"}

    other = _spec(search_id="other", required_work_minutes=3 * 60)
    with pytest.raises(ValueError, match="does not belong"):
        expand_schedule_closures(other, schedule)


def test_loaded_schedule_rejects_tampered_identity_or_alignment():
    spec = _spec(
        permitted_date_start="2027-05-03",
        permitted_date_end="2027-05-03",
        required_work_minutes=2 * 60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )
    schedule = generate_closure_schedules(spec)[0]
    identity_tamper = {**schedule.to_dict(), "schedule_id": "closure-deadbeef"}
    alignment_tamper = {**schedule.to_dict(), "daily_start": "08:05"}

    with pytest.raises(ValueError, match="schedule_id"):
        ClosureSchedule.from_dict(identity_tamper)
    with pytest.raises(ValueError, match="align to 15"):
        ClosureSchedule.from_dict(alignment_tamper)


def test_worksite_connectivity_ignores_edge_direction():
    endpoints = {
        "a_b_0": ("a", "b"),
        "c_b_0": ("c", "b"),
        "x_y_0": ("x", "y"),
    }

    validate_connected_worksite(("a_b_0", "c_b_0"), endpoints)
    with pytest.raises(ValueError, match="connected"):
        validate_connected_worksite(("a_b_0", "x_y_0"), endpoints)
    with pytest.raises(ValueError, match="missing"):
        validate_connected_worksite(("unknown",), endpoints)


# ---------------------------------------------------------------------------
# PR C: the streaming calendar API must be the SAME enumeration, not a similar
# one. Everything downstream — schedule IDs, workspace ledgers, daily-unit
# cache keys — is content-addressed off these bytes, so "equivalent" is not
# good enough; it has to be identical.
# ---------------------------------------------------------------------------

#: sha256 over the canonical JSON of every schedule's ``to_dict()``, measured
#: with the PRE-PR-C implementation (commit 01a0b16, before
#: ``iter_closure_schedules`` existed). These pin the enumeration itself, so a
#: future refactor of the generator cannot quietly renumber a search.
_FROZEN_ENUMERATIONS = {
    "rounded": (662,
                "af4f60e8d4ddd5ab30d88abf21d24a3b8595617c52288b5930f70df84ae9dbe1"),
    "exact_equal": (467,
                    "2eea6ec72bfdf049ed8ffa2b5779f6e0c254fd97ca261cd5b965af13446f275a"),
    "overnight": (496,
                  "fe85e1516b3f6b3458b0f6e379f814d5e5c412f2667f2fc761d6670d535ff5d0"),
    "blackout_dst": (377,
                     "8d49b7186dad4ca5ad3d5f39febd9045b909ab9e29cc05ac16b43c1c2be88e00"),
    "exact_0730": (144,
                   "b6194855f8aa31d997ddc99bf136f6b6a60ead894538e743284ad107ddea7cd2"),
}


def _frozen_spec(name):
    base = dict(
        search_id="digest-case",
        permitted_date_start="2027-04-05",
        permitted_date_end="2027-04-16",
        required_work_minutes=8 * 60,
        max_consecutive_start_days=3,
        permitted_daily_band=DailyTimeBand("06:00", "18:00"),
        allowed_weekdays=(0, 1, 2, 3, 4),
    )
    if name == "exact_equal":
        base.update(interday_policy="independent_daily_reset_v1",
                    work_allocation_policy="exact_equal_daily_v1",
                    period_comparison_policy="rolling_period_v1")
    elif name == "overnight":
        base.update(permitted_daily_band=DailyTimeBand("20:00", "06:00"),
                    required_work_minutes=6 * 60,
                    max_consecutive_start_days=2,
                    allowed_weekdays=(0, 1, 2, 3, 4, 5, 6))
    elif name == "blackout_dst":
        base.update(permitted_date_start="2027-10-24",
                    permitted_date_end="2027-11-02",
                    blackout_dates=("2027-10-28",),
                    allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
                    max_consecutive_start_days=3)
    elif name == "exact_0730":
        base.update(required_work_minutes=int(7.75 * 60) * 3,
                    max_consecutive_start_days=3,
                    interday_policy="independent_daily_reset_v1",
                    work_allocation_policy="exact_equal_daily_v1")
    return _spec(**base)


@pytest.mark.parametrize("name", sorted(_FROZEN_ENUMERATIONS))
def test_streaming_enumeration_is_byte_identical_to_the_frozen_generator(name):
    import hashlib
    import json

    expected_count, expected_digest = _FROZEN_ENUMERATIONS[name]
    spec = _frozen_spec(name)

    materialized = generate_closure_schedules(spec)
    streamed = tuple(iter_closure_schedules(spec))

    assert streamed == materialized
    assert [item.schedule_id for item in streamed] == [
        item.schedule_id for item in materialized
    ]
    assert len(streamed) == expected_count
    payload = json.dumps([item.to_dict() for item in streamed],
                         sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == expected_digest


def test_iteration_is_lazy_and_the_wrapper_is_the_only_materialisation(
    monkeypatch,
):
    """A prefix must cost a prefix.

    Counting constructed schedules rather than timing or measuring memory: it
    is exact, and it is the property that actually matters — a lazy API that
    still enumerated everything before yielding the first item would pass a
    timing test on a fast machine and fail the memory gate on a real search.
    """
    import itertools

    from traffic_sim.core import closure_calendar as calendar

    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-02-26",
        required_work_minutes=200 * 60,
        max_consecutive_start_days=30,
        permitted_daily_band=DailyTimeBand("06:00", "18:00"),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        period_comparison_policy="rolling_period_v1",
    )

    built: list[int] = []
    real = calendar.ClosureSchedule

    def counting(*args, **kwargs):
        built.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(calendar, "ClosureSchedule", counting)

    stream = calendar.iter_closure_schedules(spec)
    assert not isinstance(stream, (list, tuple))
    prefix = tuple(itertools.islice(stream, 5))
    assert len(prefix) == 5
    assert len(built) == 5, (
        f"a five-schedule prefix built {len(built)} schedules; the API is "
        "not lazy")

    built.clear()
    everything = calendar.generate_closure_schedules(spec)
    assert len(everything) > 400
    assert len(built) == len(everything)
    assert prefix == everything[:5]


def test_streaming_wrapper_still_rejects_a_bad_spec_at_the_call_site():
    """A generator that defers its argument check reports the error nowhere.

    The type check has to run before the generator is returned, or a caller
    passing the wrong object sees the failure at first iteration — often in a
    different function entirely.
    """
    with pytest.raises(TypeError, match="ClosureSearchSpec"):
        iter_closure_schedules({"search_id": "not-a-spec"})
    with pytest.raises(TypeError, match="ClosureSearchSpec"):
        generate_closure_schedules({"search_id": "not-a-spec"})


def test_streaming_keeps_the_0730_case_periods_and_identical_daily_times():
    """The plan's worked example, read off the streaming API.

    07:30-15:15 inside a 06:00-18:00 band, three workdays, one exact equal
    daily shift; the period may cross a weekend and every selected workday
    uses the same start and end clock time.
    """
    spec = _spec(
        permitted_date_start="2027-04-05",
        permitted_date_end="2027-04-16",
        required_work_minutes=int(7.75 * 60) * 3,
        max_consecutive_start_days=3,
        permitted_daily_band=DailyTimeBand("06:00", "18:00"),
        allowed_weekdays=(0, 1, 2, 3, 4),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
    )

    streamed = list(iter_closure_schedules(spec))
    example = [
        item for item in streamed
        if item.daily_start == "07:30" and item.daily_end == "15:15"
        and item.first_work_date == "2027-04-08"
    ]
    assert example, "the plan's 07:30-15:15 case must remain enumerable"
    schedule = example[0]
    assert schedule.day_count == 3
    dates = [item.work_date for item in schedule.intervals]
    # Thursday, Friday, then Monday: a rolling period crossing a weekend.
    assert dates == ["2027-04-08", "2027-04-09", "2027-04-12"]
    assert {item.start_time[11:] for item in schedule.intervals} == {"07:30:00"}
    assert {item.end_time[11:] for item in schedule.intervals} == {"15:15:00"}

    for item in streamed:
        assert {value.start_time[11:] for value in item.intervals} == {
            item.daily_start + ":00"
        }

    across_months = list(iter_closure_schedules(_spec(
        permitted_date_start="2027-04-26",
        permitted_date_end="2027-05-07",
        required_work_minutes=int(7.75 * 60) * 4,
        max_consecutive_start_days=4,
        permitted_daily_band=DailyTimeBand("06:00", "18:00"),
        allowed_weekdays=(0, 1, 2, 3, 4),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
    )))
    crossing = [
        item for item in across_months
        if item.intervals[0].work_date[:7] != item.intervals[-1].work_date[:7]
    ]
    assert crossing, "streamed periods must still cross a month boundary"
    assert any(
        item.intervals[0].work_date[:4] == item.intervals[-1].work_date[:4]
        for item in crossing
    )
