import xml.etree.ElementTree as ET

import pytest

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSearchSpec,
    DailyTimeBand,
    DemandBuildSpec,
)
from traffic_sim.simulation.envelope import (
    EnvelopePolicy,
    build_simulation_envelope,
    envelope_demand_spec,
    envelope_scenario_spec,
    evaluate_recovery,
    read_edgedata_time_loss,
)
from traffic_sim.simulation.trajectory_contract import (
    validate_multiday_trajectory,
)
from tests.test_trajectory_contract import valid_payload


def _search(**overrides):
    values = {
        "search_id": "continuous-work",
        "directed_edges": ("a_b_0", "b_a_0"),
        "demand_build_id": "forecast-demand",
        "source": "forecast",
        "permitted_date_start": "2027-04-05",
        "permitted_date_end": "2027-04-06",
        "required_work_minutes": 4 * 60,
        "max_consecutive_start_days": 2,
        "permitted_daily_band": DailyTimeBand("08:00", "10:00"),
        "allowed_weekdays": (0, 1, 2, 3, 4, 5, 6),
    }
    values.update(overrides)
    return ClosureSearchSpec(**values)


def _schedule(search, day_count):
    return next(
        item for item in generate_closure_schedules(search)
        if item.day_count == day_count
        and item.first_work_date == search.permitted_date_start
        and item.daily_start == search.permitted_daily_band.earliest_start
    )


def test_two_day_envelope_is_continuous_and_projects_to_scenario_contract():
    search = _search()
    schedule = _schedule(search, 2)

    envelope = build_simulation_envelope(
        search, schedule, baseline_trip_duration_p99_s=1800)
    demand = envelope_demand_spec(search, envelope)
    scenario = envelope_scenario_spec(
        search,
        schedule,
        envelope,
        demand_build_id="built-demand",
        network_build_id="network",
    )

    assert envelope.scenario_start == "2027-04-05T00:00:00"
    assert envelope.scenario_end == "2027-04-07T00:00:00"
    assert envelope.closure_start == "2027-04-05T08:00:00"
    assert envelope.closure_end == "2027-04-06T10:00:00"
    assert envelope.demand_days == 2
    assert envelope.warmup_actual_s == 8 * 3600
    assert envelope.recovery_cap_actual_s == 14 * 3600
    assert demand.purpose == "closure_envelope"
    assert demand.days == 2
    assert len(scenario.closures) == 4
    assert scenario.analysis_window == envelope.analysis_window
    assert all(
        left.end_time < right.start_time
        for left, right in zip(
            scenario.closures[::2], scenario.closures[2::2])
    )


def test_seven_workdays_can_stay_inside_seven_calendar_demand_days():
    search = _search(
        permitted_date_start="2027-04-05",
        permitted_date_end="2027-04-11",
        required_work_minutes=42 * 60,
        max_consecutive_start_days=7,
        permitted_daily_band=DailyTimeBand("08:00", "14:00"),
    )

    envelope = build_simulation_envelope(
        search, _schedule(search, 7), baseline_trip_duration_p99_s=1800)

    assert envelope.demand_days == 7
    assert envelope.closure_start == "2027-04-05T08:00:00"
    assert envelope.closure_end == "2027-04-11T14:00:00"


def test_long_daily_shifts_can_require_validated_nine_day_envelope():
    search = _search(
        permitted_date_start="2027-01-02",
        permitted_date_end="2027-01-08",
        required_work_minutes=7 * 23 * 60,
        max_consecutive_start_days=7,
        permitted_daily_band=DailyTimeBand("01:00", "24:00"),
    )

    envelope = build_simulation_envelope(
        search, _schedule(search, 7), baseline_trip_duration_p99_s=3600)
    demand = envelope_demand_spec(search, envelope)

    assert envelope.scenario_start == "2027-01-01T00:00:00"
    assert envelope.scenario_end == "2027-01-10T00:00:00"
    assert envelope.demand_days == 9
    assert demand.days == 9
    assert demand.purpose == "closure_envelope"


def test_envelope_fails_closed_beyond_validated_nine_days():
    search = _search(
        permitted_date_start="2027-01-10",
        permitted_date_end="2027-01-16",
        required_work_minutes=42 * 60,
        max_consecutive_start_days=7,
        permitted_daily_band=DailyTimeBand("08:00", "14:00"),
    )

    with pytest.raises(ValueError, match="validated maximum is 9"):
        build_simulation_envelope(
            search,
            _schedule(search, 7),
            baseline_trip_duration_p99_s=4 * 24 * 3600,
        )


def test_envelope_rejects_dst_transition_even_outside_work_interval():
    search = _search(
        permitted_date_start="2027-03-27",
        permitted_date_end="2027-03-27",
        required_work_minutes=2 * 60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
    )
    policy = EnvelopePolicy(requested_recovery_cap_s=24 * 3600)

    with pytest.raises(ValueError, match="DST transition"):
        build_simulation_envelope(
            search,
            _schedule(search, 1),
            baseline_trip_duration_p99_s=1800,
            policy=policy,
        )


def test_standard_demand_stays_at_seven_days_but_envelope_allows_nine():
    with pytest.raises(ValueError, match="1 through 7"):
        DemandBuildSpec(start_date="2027-01-01", source="forecast", days=8)
    assert DemandBuildSpec(
        start_date="2027-01-01",
        source="forecast",
        days=9,
        purpose="closure_envelope",
    ).days == 9
    with pytest.raises(ValueError, match="1 through 9"):
        DemandBuildSpec(
            start_date="2027-01-01",
            source="forecast",
            days=10,
            purpose="closure_envelope",
        )


def _write_edgedata(path, values):
    root = ET.Element("meandata")
    for index, (baseline_value, other_value) in enumerate(values):
        interval = ET.SubElement(
            root, "interval",
            begin=str(index * 900), end=str((index + 1) * 900))
        ET.SubElement(
            interval, "edge", id="affected",
            timeLoss=str(baseline_value))
        ET.SubElement(
            interval, "edge", id="other", timeLoss=str(other_value))
    ET.ElementTree(root).write(path, encoding="unicode")


def test_recovery_uses_affected_edge_time_loss_and_requires_sustained_match(
        tmp_path):
    baseline_path = tmp_path / "baseline.xml"
    candidate_path = tmp_path / "candidate.xml"
    _write_edgedata(
        baseline_path,
        [(100, 1000), (100, 1000), (100, 1000), (100, 1000), (100, 1000)])
    _write_edgedata(
        candidate_path,
        [(100, 1), (300, 1), (180, 1), (100, 1), (105, 1)])
    policy = EnvelopePolicy(
        recovery_relative_tolerance=0.10,
        recovery_absolute_time_loss_tolerance_s=0,
    )

    result = evaluate_recovery(
        read_edgedata_time_loss(
            baseline_path, affected_edges={"affected"}),
        read_edgedata_time_loss(
            candidate_path, affected_edges={"affected"}),
        closure_end_s=900,
        recovery_cap_end_s=4500,
        policy=policy,
    )

    assert result.status == "recovered"
    assert result.recovered_at_s == 4500
    assert result.recovery_time_s == 3600
    assert result.required_consecutive_buckets == 2


def test_recovery_fails_when_congestion_does_not_dissipate(tmp_path):
    baseline_path = tmp_path / "baseline.xml"
    candidate_path = tmp_path / "candidate.xml"
    _write_edgedata(baseline_path, [(10, 0)] * 4)
    _write_edgedata(candidate_path, [(100, 0)] * 4)
    policy = EnvelopePolicy(
        recovery_relative_tolerance=0,
        recovery_absolute_time_loss_tolerance_s=0,
    )

    result = evaluate_recovery(
        read_edgedata_time_loss(baseline_path),
        read_edgedata_time_loss(candidate_path),
        closure_end_s=0,
        recovery_cap_end_s=3600,
        policy=policy,
    )

    assert result.status == "congestion_not_dissipated"
    assert result.recovered is False


def test_recovery_fails_closed_when_a_matched_bucket_is_missing(tmp_path):
    baseline_path = tmp_path / "baseline.xml"
    candidate_path = tmp_path / "candidate.xml"
    _write_edgedata(baseline_path, [(10, 0)] * 4)
    # First bucket is still congested, second is one good bucket, and the
    # third bucket needed for the sustained two-bucket proof is absent.
    _write_edgedata(candidate_path, [(1000, 0), (10, 0)])

    result = evaluate_recovery(
        read_edgedata_time_loss(baseline_path),
        read_edgedata_time_loss(candidate_path),
        closure_end_s=0,
        recovery_cap_end_s=3600,
    )

    assert result.status == "insufficient_evidence"
    assert result.recovered is False


def test_nine_day_trajectory_contract_remains_bounded():
    assert validate_multiday_trajectory(
        valid_payload(days=9), days=9, expected_inserted=9) == []
