from datetime import date

import pytest

from traffic_sim.core.closure_calendar import (
    expand_schedule_closures,
    generate_closure_schedules,
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


def test_full_day_is_allowed_once_but_not_as_back_to_back_shifts():
    one_day = _spec(
        permitted_date_start="2027-05-03",
        permitted_date_end="2027-05-03",
        required_work_minutes=24 * 60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("00:00", "24:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )
    two_days = _spec(
        permitted_date_start="2027-05-03",
        permitted_date_end="2027-05-04",
        required_work_minutes=48 * 60,
        max_consecutive_start_days=2,
        permitted_daily_band=DailyTimeBand("00:00", "24:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
    )

    schedule = generate_closure_schedules(one_day)[0]
    assert schedule.daily_start == "00:00"
    assert schedule.daily_end == "24:00"
    assert schedule.actual_closed_minutes == 24 * 60
    assert generate_closure_schedules(two_days) == ()


@pytest.mark.parametrize(
    "override, error",
    [
        ({"directed_edges": ("a_b_0", "a_b_0")}, "unique"),
        ({"source": "invented"}, "source"),
        ({"timezone": "UTC"}, "Europe/Stockholm"),
        ({"required_work_minutes": True}, "integer"),
        ({"required_work_minutes": 0}, "positive"),
        ({"max_consecutive_start_days": 8}, "1 through 7"),
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
