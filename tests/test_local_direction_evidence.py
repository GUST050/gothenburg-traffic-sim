"""The local-corridor diagnostic: what Gothenburg's own sensors can and cannot
say about a TOT sensor's intraday direction split.

Two failure modes are worth guarding against, and they pull in opposite
directions.  The first is dismissing local evidence that genuinely exists — the
five single-direction stations DO measure a carriageway every 15 minutes, and
one of them sits on the same street as the only two-way station.  The second is
the seductive shortcut: reading ``neighbour / two_way_total`` AS the direction
share.  That ratio is not a share, because the neighbour stands at a different
point on the street with intersections in between, and these tests pin the
measurement that proves it rather than leaving it as an argument.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.measure_local_direction_evidence import (
    DAY_TYPES, EdgeGeometry, _node_of, _street_key, find_corridor_donors,
    hourly_from_slots, load_network, measure_donor, measure_node_conservation,
    slot_profile)
from traffic_sim.intake.sensors import load_registry

REGISTRY = "data_in/sensors.json"
NORTH = "60786979_3575001205_0"     # sensor 107, bearing ~352
SOUTH = "1455801464_18241874_0"     # sensor 107, bearing ~174
DONOR = "30420757_30421744_0"       # sensor 1076, southbound Skanegatan
EPOCH = datetime(2025, 1, 1)
WEEKDAYS = DAY_TYPES["weekday"]


def _registry():
    return load_registry(REGISTRY)


def _reference(sensor_id: str = "107"):
    return _registry()[sensor_id].directional_reference


class TestSlotProfileMissingness:
    """A gap must never become a zero — the project's standing data rule."""

    def test_a_slot_with_no_observation_is_none_not_zero(self):
        flows = {"e": [None] * 96}
        means, counts = slot_profile(
            flows, ["e"], epoch=EPOCH, interval_minutes=15,
            period_start="2025-01-01", period_end_exclusive="2025-01-02",
            weekdays=WEEKDAYS)
        assert means[0] is None
        assert counts[0] == 0

    def test_a_partially_missing_group_is_skipped_entirely(self):
        # One edge reported, the other did not: summing would silently publish
        # half a junction as if it were the whole one.
        flows = {"a": [10.0] + [None] * 95, "b": [None] * 96}
        means, _ = slot_profile(
            flows, ["a", "b"], epoch=EPOCH, interval_minutes=15,
            period_start="2025-01-01", period_end_exclusive="2025-01-02",
            weekdays=WEEKDAYS)
        assert means[0] is None

    def test_a_two_way_total_on_both_edges_is_counted_once(self):
        # CLAUDE.md's reporting trap: a Total station carries the SAME value on
        # both directed edges, so summing doubles it.
        same = [40.0] + [None] * 95
        means, _ = slot_profile(
            {NORTH: list(same), SOUTH: list(same)}, [NORTH, SOUTH],
            epoch=EPOCH, interval_minutes=15, period_start="2025-01-01",
            period_end_exclusive="2025-01-02", weekdays=WEEKDAYS)
        assert means[0] == 40.0

    def test_genuinely_differing_series_are_summed(self):
        means, _ = slot_profile(
            {"a": [30.0] + [None] * 95, "b": [12.0] + [None] * 95}, ["a", "b"],
            epoch=EPOCH, interval_minutes=15, period_start="2025-01-01",
            period_end_exclusive="2025-01-02", weekdays=WEEKDAYS)
        assert means[0] == 42.0

    def test_the_weekday_filter_excludes_the_other_day_type(self):
        # 2025-01-04 is a Saturday; a weekday request must not see it.
        flows = {"e": [None] * (3 * 96) + [99.0] + [None] * 95}
        weekday, _ = slot_profile(
            flows, ["e"], epoch=EPOCH, interval_minutes=15,
            period_start="2025-01-01", period_end_exclusive="2025-01-08",
            weekdays=DAY_TYPES["weekday"])
        weekend, _ = slot_profile(
            flows, ["e"], epoch=EPOCH, interval_minutes=15,
            period_start="2025-01-01", period_end_exclusive="2025-01-08",
            weekdays=DAY_TYPES["weekend"])
        assert weekday[0] is None
        assert weekend[0] == 99.0

    def test_observations_outside_the_declared_period_are_ignored(self):
        flows = {"e": [50.0] + [None] * 95}
        means, _ = slot_profile(
            flows, ["e"], epoch=EPOCH, interval_minutes=15,
            period_start="2025-06-01", period_end_exclusive="2025-07-01",
            weekdays=WEEKDAYS)
        assert means[0] is None


class TestHourlyCollapse:
    def test_missingness_survives_the_collapse(self):
        values = [None] * 4 + [2.0, 2.0, 2.0, 2.0] + [None] * 88
        hourly = hourly_from_slots(values, 4)
        assert hourly[0] is None
        assert hourly[1] == 2.0

    def test_a_partly_present_hour_uses_what_is_present(self):
        values = [4.0, None, None, None] + [None] * 92
        assert hourly_from_slots(values, 4)[0] == 4.0


class TestDonorDiscoveryIsDataDriven:
    """No sensor id may be hardcoded — a future TOT sensor must be covered."""

    def test_sensor_1076_is_found_as_a_corridor_donor_for_107(self):
        registry, network = _registry(), load_network()
        donors = find_corridor_donors(registry, network, "107", _reference())
        assert [d["donor_edge_id"] for d in donors] == [DONOR]
        donor = donors[0]
        # It aligns with the SOUTHBOUND carriageway, not the northbound one.
        assert donor["aligned_tot_edge_id"] == SOUTH
        assert donor["bearing_delta_deg"] < 10
        assert 200 < donor["distance_m"] < 300

    def test_the_two_way_station_is_never_its_own_donor(self):
        registry, network = _registry(), load_network()
        donors = find_corridor_donors(registry, network, "107", _reference())
        assert all(d["donor_sensor_id"] != "107" for d in donors)

    def test_a_two_way_delivery_cannot_be_a_donor(self):
        # A two-way total is not a directional observation, so it may never be
        # offered as evidence about one carriageway.
        registry, network = _registry(), load_network()
        donors = find_corridor_donors(registry, network, "107", _reference())
        for donor in donors:
            assert registry[donor["donor_sensor_id"]].measurement_semantics \
                == "directional"

    def test_a_distance_threshold_of_zero_finds_nothing(self):
        registry, network = _registry(), load_network()
        assert find_corridor_donors(registry, network, "107", _reference(),
                                    max_distance_m=0.0) == []

    def test_a_bearing_threshold_of_zero_finds_nothing(self):
        registry, network = _registry(), load_network()
        assert find_corridor_donors(registry, network, "107", _reference(),
                                    max_bearing_delta_deg=0.0) == []

    def test_donors_must_share_the_street_name(self):
        registry = _registry()
        network = dict(load_network())
        moved = network[DONOR]
        network[DONOR] = EdgeGeometry(
            edge_id=moved.edge_id, street="Someothergatan",
            bearing_deg=moved.bearing_deg, mid_lat=moved.mid_lat,
            mid_lon=moved.mid_lon)
        assert find_corridor_donors(registry, network, "107",
                                    _reference()) == []

    def test_a_list_valued_osm_street_name_still_matches(self):
        assert _street_key(["Skanegatan", "E6"]) == "skanegatan"
        assert _street_key(None) is None
        assert _street_key("  ") is None


class TestTheDonorLevelIsNotAShare:
    """The measurement that kills the shortcut, pinned as a regression."""

    def test_the_donor_ratio_is_materially_above_the_published_share(self):
        registry, network = _registry(), load_network()
        reference = _reference()
        donor = find_corridor_donors(registry, network, "107", reference)[0]
        from traffic_sim.intake.direction_anchor import load_measurements
        flows, epoch, interval = load_measurements(Path("web/data/flows.json"))
        result = measure_donor(donor, reference, flows, epoch=epoch,
                               interval_minutes=interval, day_type="weekday",
                               weekdays=WEEKDAYS)
        published = result["published_share_of_aligned_direction"]
        assert published == pytest.approx(3100 / 6500, abs=1e-6)
        # 1076 carries substantially MORE southbound traffic than 107's
        # southbound share of its own total, because it stands elsewhere.
        assert result["flow_weighted_ratio_mean"] > published + 0.15
        assert result["location_bias_pp"] > 15.0
        assert result["level_is_usable_as_share"] is False

    def test_the_donor_carries_real_intraday_structure(self):
        # The opportunity, not just the obstacle: far more movement than the
        # 7.7 pp the transferred Norwegian curve produces.
        registry, network = _registry(), load_network()
        reference = _reference()
        donor = find_corridor_donors(registry, network, "107", reference)[0]
        from traffic_sim.intake.direction_anchor import load_measurements
        flows, epoch, interval = load_measurements(Path("web/data/flows.json"))
        result = measure_donor(donor, reference, flows, epoch=epoch,
                               interval_minutes=interval, day_type="weekday",
                               weekdays=WEEKDAYS)
        assert result["hourly_ratio_span_pp"] > 15.0

    def test_the_normalised_shape_has_the_level_divided_out(self):
        registry, network = _registry(), load_network()
        reference = _reference()
        donor = find_corridor_donors(registry, network, "107", reference)[0]
        from traffic_sim.intake.direction_anchor import load_measurements
        flows, epoch, interval = load_measurements(Path("web/data/flows.json"))
        result = measure_donor(donor, reference, flows, epoch=epoch,
                               interval_minutes=interval, day_type="weekday",
                               weekdays=WEEKDAYS)
        shape = [v for v in result["normalised_hourly_shape"] if v is not None]
        # A shape is dimensionless and straddles 1.0; a level does not.
        assert min(shape) < 1.0 < max(shape)


class TestNodeConservation:
    def test_the_node_the_three_sensors_share_is_found(self):
        from traffic_sim.intake.direction_anchor import load_measurements
        flows, epoch, interval = load_measurements(Path("web/data/flows.json"))
        nodes = measure_node_conservation(
            _registry(), flows, epoch=epoch, interval_minutes=interval,
            period_start="2025-01-01", period_end_exclusive="2026-01-01",
            day_type="weekday", weekdays=WEEKDAYS)
        by_id = {node["node_id"]: node for node in nodes}
        assert "26355153" in by_id
        node = by_id["26355153"]
        assert node["measured_inflow_edges"] == ["26842525_26355153_0"]
        assert node["measured_outflow_edges"] == ["26355153_91615277_0",
                                                  "26355153_96523321_0"]

    def test_the_node_is_open_so_conservation_cannot_pin_the_split(self):
        from traffic_sim.intake.direction_anchor import load_measurements
        flows, epoch, interval = load_measurements(Path("web/data/flows.json"))
        nodes = measure_node_conservation(
            _registry(), flows, epoch=epoch, interval_minutes=interval,
            period_start="2025-01-01", period_end_exclusive="2026-01-01",
            day_type="weekday", weekdays=WEEKDAYS)
        node = {n["node_id"]: n for n in nodes}["26355153"]
        # Roughly half the outflow arrives on legs nobody measures. That slack
        # is exactly what a free solver could absorb error into.
        assert 0.3 < node["unmeasured_share_of_outflow"] < 0.7
        assert node["hourly_span_pp"] > 10.0

    def test_only_directional_deliveries_enter_the_balance(self):
        from traffic_sim.intake.direction_anchor import load_measurements
        flows, epoch, interval = load_measurements(Path("web/data/flows.json"))
        nodes = measure_node_conservation(
            _registry(), flows, epoch=epoch, interval_minutes=interval,
            period_start="2025-01-01", period_end_exclusive="2026-01-01",
            day_type="weekday", weekdays=WEEKDAYS)
        for node in nodes:
            for edge in (list(node["measured_inflow_edges"])
                         + list(node["measured_outflow_edges"])):
                assert edge not in (NORTH, SOUTH)

    def test_node_ids_are_parsed_from_the_directed_edge_id(self):
        assert _node_of("26842525_26355153_0") == ("26842525", "26355153")
        assert _node_of("nonsense") is None


class TestArtifactContract:
    def test_the_record_declares_itself_non_release_evidence(self):
        from tools.measure_local_direction_evidence import measure
        record = measure()
        assert record["release_evidence"] is False
        assert record["schema_version"] == "local_direction_evidence_v1"
        assert record["limitations"]
        json.dumps(record)          # must round-trip for the append-only file
