"""Focused tests for the DYNAMIC-SENSOR-PASSAGE-2026-09-08 diagnostic.

Pinned per the ACTIVE_TASK acceptance criteria: a trip crossing sensors in
different quarters, exact half-open interval boundaries, repeated edge
visits, warm-up/tail, missing/truncated evidence, sensor-registry extension
without hardcoded IDs, and synthetic-demand recovery across multiple sensors
and quarters.
"""

import pytest

from traffic_sim.experimental.passage_timing import (
    EdgePassage,
    MISSING_REASON_UNREACHED,
    build_departure_to_passage_report,
    build_lag_kernel,
    parse_vehroute_target_passages,
    project_departure_to_passage,
    quarter_index,
    summarize_edge_timing,
    vehicle_target_passages,
)

VEHROUTE_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<routes>\n'
)
VEHROUTE_FOOTER = '</routes>\n'


def write_vehroute(tmp_path, body):
    path = tmp_path / "vehroutes.xml"
    path.write_text(VEHROUTE_HEADER + body + VEHROUTE_FOOTER)
    return path


# --- half-open interval boundaries -----------------------------------------

def test_quarter_index_is_half_open():
    assert quarter_index(0.0) == 0
    assert quarter_index(899.999) == 0
    assert quarter_index(900.0) == 1
    assert quarter_index(1799.999) == 1
    assert quarter_index(1800.0) == 2


def test_quarter_index_respects_custom_width():
    assert quarter_index(59.999, quarter_seconds=60) == 0
    assert quarter_index(60.0, quarter_seconds=60) == 1


# --- a trip crossing sensors in different quarters --------------------------

def test_trip_crossing_two_sensors_in_different_quarters():
    # Departs in quarter 0 (t=100s), reaches a sensor edge shortly after
    # (still quarter 0), then a distant second sensor edge two quarters later.
    edges = ["origin", "near_sensor", "mid", "far_sensor", "dest"]
    exit_times = [150.0, 400.0, 1200.0, 2000.0, 2100.0]
    target_edges = {"near_sensor", "far_sensor"}
    passages, missing = vehicle_target_passages(
        "v1", 100.0, edges, exit_times, target_edges)
    assert missing == []
    by_edge = {p.edge_id: p for p in passages}
    assert by_edge["near_sensor"].departure_quarter == 0
    assert by_edge["near_sensor"].passage_quarter == 0
    assert by_edge["near_sensor"].lag_quarters == 0
    # entry to far_sensor is the exit time of "mid" (1200.0) -> quarter 1
    assert by_edge["far_sensor"].departure_quarter == 0
    assert by_edge["far_sensor"].passage_quarter == 1
    assert by_edge["far_sensor"].lag_quarters == 1
    assert by_edge["far_sensor"].entry_s == 1200.0


def test_first_edge_departure_is_not_counted_as_edgedata_entered():
    passages, missing = vehicle_target_passages(
        "v1", 42.0, ["sensor_at_origin"], [50.0], {"sensor_at_origin"})
    assert missing == []
    assert passages == []


# --- repeated edge visits ---------------------------------------------------

def test_repeated_edge_visit_records_both_occurrences():
    # A loop route: the vehicle drives the same sensor edge twice.
    edges = ["a", "sensor", "b", "c", "sensor", "d"]
    exit_times = [10.0, 20.0, 950.0, 1000.0, 1850.0, 1900.0]
    passages, missing = vehicle_target_passages(
        "v1", 0.0, edges, exit_times, {"sensor"})
    assert missing == []
    assert len(passages) == 2
    first, second = passages
    assert first.occurrence == 0
    assert second.occurrence == 1
    # First visit: entry = exit of "a" = 10.0 -> quarter 0.
    assert first.entry_s == 10.0
    assert first.passage_quarter == 0
    # Second visit: entry = exit of "c" = 1000.0 -> quarter 1.
    assert second.entry_s == 1000.0
    assert second.passage_quarter == 1


def test_pfe_achieved_undercounts_repeated_visits_relative_to_passages():
    """achieved[edge][i] uses set(cand.edges): one route -> at most one
    count per unique edge, however many times it is actually driven. This
    pins that documented discrepancy against this module's own counting."""
    edges = ["a", "sensor", "b", "sensor"]
    exit_times = [5.0, 10.0, 20.0, 30.0]
    passages, _ = vehicle_target_passages(
        "v1", 0.0, edges, exit_times, {"sensor"})
    achieved_style_count = len(set(e for e in edges if e == "sensor") & {"sensor"})
    assert achieved_style_count == 1
    assert len(passages) == 2


# --- missing / truncated evidence ------------------------------------------

def test_unfinished_vehicle_before_target_edge_is_reported_missing():
    # The vehicle never left "b" (exit -1); "sensor" (after b) is unreachable
    # evidence, not a zero-lag guess.
    edges = ["a", "b", "sensor"]
    exit_times = [10.0, -1.0, -1.0]
    passages, missing = vehicle_target_passages(
        "v1", 0.0, edges, exit_times, {"sensor"})
    assert passages == []
    assert len(missing) == 1
    assert missing[0].edge_id == "sensor"
    assert missing[0].reason == MISSING_REASON_UNREACHED


def test_unfinished_vehicle_still_yields_entry_for_the_edge_it_is_on():
    # The target edge itself is the one the vehicle never left: its ENTRY
    # time is still known (exit of the previous edge), so it is a passage,
    # not a missing record.
    edges = ["a", "sensor"]
    exit_times = [500.0, -1.0]
    passages, missing = vehicle_target_passages(
        "v1", 0.0, edges, exit_times, {"sensor"})
    assert missing == []
    assert len(passages) == 1
    assert passages[0].entry_s == 500.0


def test_mismatched_edges_and_exit_times_length_raises():
    with pytest.raises(ValueError):
        vehicle_target_passages("v1", 0.0, ["a", "b"], [10.0], {"a"})


# --- warm-up / tail (out-of-window) -----------------------------------------

def test_out_of_window_passage_is_counted_not_dropped():
    n_intervals = 2  # window covers quarters 0-1 (0..1800s)
    edges = ["a", "tail_sensor"]
    # Entry to "tail_sensor" is "a"'s exit time (2000.0 -> quarter 2), past
    # the nominal window -- not "tail_sensor"'s own exit time.
    exit_times = [2000.0, 2100.0]
    passages, missing = vehicle_target_passages(
        "v1", 0.0, edges, exit_times, {"tail_sensor"})
    assert missing == []
    summary = summarize_edge_timing(passages, missing, "tail_sensor", n_intervals)
    assert summary.n_passages == 1
    assert summary.out_of_window_passages == 1


def test_in_window_passage_is_not_flagged_out_of_window():
    n_intervals = 96
    passages, missing = vehicle_target_passages(
        "v1", 100.0, ["origin", "sensor"], [150.0, 200.0], {"sensor"})
    summary = summarize_edge_timing(passages, missing, "sensor", n_intervals)
    assert summary.out_of_window_passages == 0


# --- sensor registry extension without hardcoded IDs ------------------------

def test_arbitrary_new_sensor_edge_id_works_without_code_changes():
    # A brand new edge id never referenced anywhere in this module or its
    # tests -- proves the mapping has no hardcoded sensor identity.
    novel_edge = "brand_new_station_edge_added_2099"
    passages, missing = vehicle_target_passages(
        "v1", 0.0, ["a", novel_edge], [10.0, 20.0], {novel_edge})
    assert missing == []
    assert passages[0].edge_id == novel_edge


def test_report_labels_edges_from_a_caller_supplied_mapping(tmp_path):
    body = (
        '<vehicle id="v1" depart="0.00">'
        '<route edges="novel_edge_a" exitTimes="30.00"/>'
        '</vehicle>\n'
    )
    vr_path = write_vehroute(tmp_path, body)
    report = build_departure_to_passage_report(
        vr_path, {"novel_edge_a": "station_99"}, n_intervals=96)
    assert report["edges"][0]["edge_id"] == "novel_edge_a"
    assert report["edges"][0]["sensor_id"] == "station_99"


# --- vehroute I/O + mass conservation ---------------------------------------

def test_parse_vehroute_counts_every_vehicle_including_non_crossing(tmp_path):
    body = (
        '<vehicle id="v1" depart="0.00">'
        '<route edges="a b" exitTimes="10.00 20.00"/></vehicle>\n'
        '<vehicle id="v2" depart="50.00">'
        '<route edges="origin sensor" exitTimes="55.00 60.00"/></vehicle>\n'
    )
    vr_path = write_vehroute(tmp_path, body)
    passages, missing, n_total = parse_vehroute_target_passages(
        vr_path, frozenset({"sensor"}))
    assert n_total == 2
    assert len(passages) == 1
    assert passages[0].vehicle_id == "v2"
    assert missing == []


def test_parse_vehroute_uses_final_route_of_a_routedistribution(tmp_path):
    # A rerouted vehicle: the FINAL <route> in <routeDistribution> is what
    # was actually driven (mirrors run_scenario.final_route's own contract).
    body = (
        '<vehicle id="v1" depart="0.00">'
        '<routeDistribution>'
        '<route edges="a old_sensor" exitTimes="10.00 20.00"/>'
        '<route edges="a new_sensor" exitTimes="10.00 30.00"/>'
        '</routeDistribution></vehicle>\n'
    )
    vr_path = write_vehroute(tmp_path, body)
    passages, missing, n_total = parse_vehroute_target_passages(
        vr_path, frozenset({"old_sensor", "new_sensor"}))
    assert n_total == 1
    edge_ids = {p.edge_id for p in passages}
    assert edge_ids == {"new_sensor"}


def test_build_report_is_reproducible_and_hashes_input(tmp_path):
    body = (
        '<vehicle id="v1" depart="0.00">'
        '<route edges="origin sensor" exitTimes="10.00 30.00"/></vehicle>\n'
    )
    vr_path = write_vehroute(tmp_path, body)
    report_a = build_departure_to_passage_report(vr_path, {"sensor"}, 96)
    report_b = build_departure_to_passage_report(vr_path, {"sensor"}, 96)
    assert report_a == report_b
    assert report_a["n_vehicles_total"] == 1
    assert report_a["n_target_edge_passages"] == 1


# --- sparse dynamic-assignment prototype: synthetic recovery ---------------

def _synthetic_passages_for_kernel(edge_id, departure_counts, lag_shares):
    """Build EdgePassage records that EXACTLY realise a known kernel: for
    each departure quarter, split its count across the given (lag, share)
    pairs by simple proportional rounding on whole-count buckets so the
    empirical kernel this produces matches ``lag_shares`` exactly."""
    passages = []
    vid = 0
    for dep_q, count in enumerate(departure_counts):
        remaining = count
        lags = list(lag_shares.items())
        for i, (lag, share) in enumerate(lags):
            n = count * share if i < len(lags) - 1 else remaining
            n = round(n)
            remaining -= n
            for _ in range(n):
                depart_s = dep_q * 900 + 1.0
                entry_s = depart_s + lag * 900
                passages.append(EdgePassage(
                    vehicle_id=f"v{vid}", edge_id=edge_id, occurrence=0,
                    departure_quarter=dep_q, depart_s=depart_s,
                    entry_s=entry_s, passage_quarter=dep_q + lag))
                vid += 1
    return passages


def test_recovers_known_synthetic_demand_across_multiple_sensors_and_quarters():
    scenarios = {
        "sensor_a": ({0: 100, 1: 200, 2: 50}, {1: 1.0}),
        "sensor_b": ({3: 80, 4: 40}, {0: 0.7, 2: 0.3}),
    }
    n_intervals = 10
    for edge_id, (departure_counts_by_q, lag_shares) in scenarios.items():
        departure_counts = [0.0] * n_intervals
        for q, n in departure_counts_by_q.items():
            departure_counts[q] = float(n)
        synthetic_passages = _synthetic_passages_for_kernel(
            edge_id, departure_counts, lag_shares)
        kernel = build_lag_kernel(synthetic_passages, edge_id)
        result = project_departure_to_passage(
            departure_counts, kernel, n_intervals)
        expected = [0.0] * n_intervals
        for q, n in departure_counts_by_q.items():
            for lag, share in lag_shares.items():
                pass_q = q + lag
                if 0 <= pass_q < n_intervals:
                    expected[pass_q] += n * share
        for got, want in zip(result.passage_counts, expected):
            assert got == pytest.approx(want, abs=1e-9)
        assert result.unprojectable_mass == pytest.approx(0.0, abs=1e-9)


def test_projection_reports_unprojectable_mass_for_unobserved_departure_quarter():
    kernel = build_lag_kernel(
        _synthetic_passages_for_kernel("sensor", [10.0, 0.0], {0: 1.0}),
        "sensor")
    # Quarter 1 has demand but no observed lag distribution for "sensor".
    result = project_departure_to_passage([10.0, 5.0], kernel, n_intervals=4)
    assert result.unprojectable_mass == pytest.approx(5.0)
    assert sum(result.passage_counts) == pytest.approx(10.0)


def test_projection_reports_out_of_window_mass_for_tail_lag():
    kernel = {0: {5: 1.0}}  # lag pushes past a tiny window
    result = project_departure_to_passage([10.0], kernel, n_intervals=2)
    assert result.out_of_window_mass == pytest.approx(10.0)
    assert sum(result.passage_counts) == pytest.approx(0.0)


def test_mass_conservation_identity_holds():
    kernel = {0: {0: 0.5, 1: 0.3, 10: 0.2}}  # 10 is a deliberate tail lag
    result = project_departure_to_passage([40.0], kernel, n_intervals=3)
    total = (sum(result.passage_counts) + result.unprojectable_mass
             + result.out_of_window_mass)
    assert total == pytest.approx(result.total_input)
    assert result.total_input == pytest.approx(40.0)
