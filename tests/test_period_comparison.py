from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
from traffic_sim.simulation.period_comparison import build_period_comparison


def _schedules():
    spec = ClosureSearchSpec(
        search_id="multi-month-periods",
        directed_edges=("edge-a",),
        demand_build_id="forecast-release",
        source="forecast",
        permitted_date_start="2027-07-30",
        permitted_date_end="2027-08-09",
        required_work_minutes=2 * 60,
        max_consecutive_start_days=2,
        permitted_daily_band=DailyTimeBand("08:00", "09:00"),
        allowed_weekdays=(0, 4),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        period_comparison_policy="rolling_period_v1",
    )
    schedules = generate_closure_schedules(spec)
    return {schedule.schedule_id: schedule for schedule in schedules}


def _stat(schedule, hours, *, eligible=True, complete=True):
    return {
        "candidate_id": schedule.schedule_id,
        "eligible": eligible,
        "complete": complete,
        "hard_failures": [] if eligible else ["vehicles_no_detour"],
        "closure_cost": {
            "candidate_id": schedule.schedule_id,
            "worst_variant": "q90",
            "added_vehicle_hours": hours,
            "added_metres_total": hours * 1000,
            "vehicles_affected": int(hours * 10),
            "vehicles_no_detour": 0 if eligible else 1,
        },
    }


def test_compares_rolling_periods_across_month_and_week_boundaries():
    schedules = _schedules()
    ordered = sorted(schedules.values(), key=lambda item: item.first_work_date)
    winner = ordered[1]
    result = build_period_comparison(
        schedules,
        [item.schedule_id for item in ordered],
        {"candidates": [
            _stat(ordered[0], 4.0),
            _stat(winner, 1.0),
            _stat(ordered[2], 2.0),
        ]},
        winner_id=winner.schedule_id,
        tie_ids=(),
        unavailable_count=0,
        objective_method="closure_cost_v1",
    )

    assert result["start_date_count"] == 3
    assert result["comparison_complete"] is True
    assert result["best_start_date"] == winner.first_work_date
    assert result["best_start_dates"] == [winner.first_work_date]
    first = result["periods"][0]
    assert first["start_date"] == "2027-07-30"
    assert first["period_end"] == "2027-08-02"
    assert first["workday_count"] == 2


def test_incomplete_period_evidence_is_explicit():
    schedules = _schedules()
    ordered = sorted(schedules.values(), key=lambda item: item.first_work_date)
    result = build_period_comparison(
        schedules,
        [item.schedule_id for item in ordered],
        {"candidates": [_stat(ordered[0], 1.0)]},
        winner_id=None,
        tie_ids=(),
        unavailable_count=0,
        objective_method="closure_cost_v1",
    )

    assert result["comparison_complete"] is False
    assert result["evaluated_count"] == 1
    assert [item["status"] for item in result["periods"]] == [
        "viable", "incomplete", "incomplete"
    ]


def test_cost_ordered_periods_show_all_prices_but_only_verified_prefix():
    schedules = _schedules()
    ordered = sorted(schedules.values(), key=lambda item: item.first_work_date)
    costs = [
        {
            "candidate_id": schedule.schedule_id,
            "cost": _stat(schedule, hours)["closure_cost"],
        }
        for schedule, hours in zip(ordered, (3.0, 1.0, 2.0))
    ]
    winner = ordered[1]
    runner_up = ordered[2]
    result = build_period_comparison(
        schedules,
        [item.schedule_id for item in ordered],
        {"candidates": [
            _stat(winner, 1.0),
            _stat(runner_up, 2.0),
        ]},
        winner_id=winner.schedule_id,
        tie_ids=(),
        unavailable_count=0,
        objective_method="closure_cost_v2",
        deterministic_costs=costs,
        execution_statuses={
            ordered[0].schedule_id: "not_run_decision_irrelevant",
            winner.schedule_id: "verified",
            runner_up.schedule_id: "verified",
        },
    )

    assert result["comparison_complete"] is False
    assert result["deterministic_comparison_complete"] is True
    assert result["deterministic_costed_count"] == 3
    assert result["sumo_verified_count"] == 2
    assert [item["best_cost"]["added_vehicle_hours"]
            for item in result["periods"]] == [3.0, 1.0, 2.0]
    assert [item["status"] for item in result["periods"]] == [
        "costed_not_run", "best_period", "sumo_verified"
    ]


def test_final_hard_failure_supersedes_earlier_viable_pilot():
    schedules = _schedules()
    first = sorted(schedules.values(), key=lambda item: item.first_work_date)[0]
    result = build_period_comparison(
        schedules,
        [first.schedule_id],
        {"candidates": [_stat(first, 1.0)]},
        winner_id=None,
        tie_ids=(),
        unavailable_count=0,
        objective_method="closure_cost_v1",
        final_decision={"candidates": [{
            **_stat(first, 1.0, eligible=False),
            "all_variants_ready": False,
        }]},
    )

    assert result["comparison_complete"] is True
    assert result["periods"][0]["status"] == "no_viable"
    assert result["periods"][0]["best_schedule"] is None
