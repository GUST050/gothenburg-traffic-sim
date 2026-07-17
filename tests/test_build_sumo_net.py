"""Unit tests for build_sumo_net.py's OSM-tag parsing (speed/lane fallbacks) —
the pure functions netconvert's plain-XML edges are built from."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from build_sumo_net import edge_length_m, parse_lanes, parse_speed_ms


class TestParseSpeedMs:
    def test_maxspeed_tag_wins(self):
        assert parse_speed_ms({"maxspeed": "50", "highway": "residential"}) \
            == pytest.approx(50 / 3.6)

    def test_maxspeed_as_scalar_list(self):
        # OSMnx sometimes stores tag values as single-element lists.
        assert parse_speed_ms({"maxspeed": ["70"]}) == pytest.approx(70 / 3.6)

    def test_semicolon_speed_uses_one_value_not_concatenated_digits(self):
        assert parse_speed_ms({"maxspeed": "30;50",
                               "highway": "residential"}) \
            == pytest.approx(30 / 3.6)

    def test_speed_units_are_converted(self):
        assert parse_speed_ms({"maxspeed": "30 mph"}) \
            == pytest.approx(30 * 1.609344 / 3.6)
        assert parse_speed_ms({"maxspeed": "10 m/s"}) \
            == pytest.approx(10.0)

    def test_implausible_speed_falls_back(self):
        assert parse_speed_ms({"maxspeed": "3050",
                               "highway": "residential"}) \
            == pytest.approx(30 / 3.6)

    def test_non_numeric_maxspeed_falls_back_to_highway_type(self):
        # e.g. "DE:zone30" or "signals" — no digits to parse.
        assert parse_speed_ms({"maxspeed": "signals", "highway": "residential"}) \
            == pytest.approx(30 / 3.6)

    def test_missing_maxspeed_uses_highway_default(self):
        assert parse_speed_ms({"highway": "motorway"}) == pytest.approx(100 / 3.6)

    def test_unknown_highway_falls_back_to_50(self):
        assert parse_speed_ms({"highway": "some_unmapped_type"}) == pytest.approx(50 / 3.6)

    def test_missing_everything_falls_back_to_50(self):
        assert parse_speed_ms({}) == pytest.approx(50 / 3.6)


class TestParseLanes:
    def test_twoway_lanes_are_halved(self):
        # OSM 'lanes' counts both directions on a two-way way.
        assert parse_lanes({"lanes": "4", "highway": "primary"}) == 2

    def test_oneway_lanes_not_halved(self):
        assert parse_lanes({"lanes": "2", "highway": "primary", "oneway": "yes"}) == 2

    def test_oneway_bool_true_not_halved(self):
        assert parse_lanes({"lanes": "3", "highway": "primary", "oneway": True}) == 3

    def test_oneway_as_scalar_list_not_halved(self):
        # osmnx graph simplification can merge several original OSM ways into
        # one edge, leaving a list-valued tag when they disagree — a
        # genuinely one-way street must not get its lane count halved just
        # because the tag came back as ["yes"] instead of "yes".
        assert parse_lanes({"lanes": "3", "highway": "primary", "oneway": ["yes"]}) == 3

    def test_odd_twoway_lane_count_floors_and_stays_at_least_one(self):
        assert parse_lanes({"lanes": "1", "highway": "primary"}) == 1

    def test_lane_count_capped_at_four(self):
        assert parse_lanes({"lanes": "20", "highway": "motorway", "oneway": "yes"}) == 4

    def test_lanes_as_scalar_list(self):
        assert parse_lanes({"lanes": ["4"], "highway": "primary"}) == 2

    def test_directional_lane_tag_is_used_when_total_is_missing(self):
        assert parse_lanes({"lanes:forward": "2",
                            "highway": "residential"}) == 2

    def test_directional_lane_tags_do_not_copy_one_side_over_total(self):
        assert parse_lanes({"lanes": "3", "lanes:forward": "2",
                            "lanes:backward": "1",
                            "highway": "primary"}) == 1

    def test_missing_lanes_uses_highway_default(self):
        assert parse_lanes({"highway": "secondary"}) == 1
        assert parse_lanes({"highway": "motorway"}) == 2

    def test_missing_everything_defaults_to_one(self):
        assert parse_lanes({}) == 1


class TestEdgeLengthM:
    """The explicit plain-XML length keeps SUMO's driven distance identical
    to the map even where netconvert's junction cutting would otherwise
    shrink a street (an 88 m street was simulated as a 0.20 m lane before
    this fix — vehicles crossed it instantly and the animation showed
    426 km/h)."""

    STRAIGHT_100M = [(0.0, 0.0), (100.0, 0.0)]

    def test_osm_length_tag_wins_over_chord(self):
        # A curved street: OSM arc length 118 m, straight chord 100 m.
        assert edge_length_m({"length": 118.0}, self.STRAIGHT_100M) \
            == pytest.approx(118.0)

    def test_osm_length_as_scalar_list(self):
        assert edge_length_m({"length": ["118.0"]}, self.STRAIGHT_100M) \
            == pytest.approx(118.0)

    def test_missing_length_uses_shape(self):
        assert edge_length_m({}, self.STRAIGHT_100M) == pytest.approx(100.0)

    def test_corrupt_length_falls_back_to_shape(self):
        assert edge_length_m({"length": "abc"}, self.STRAIGHT_100M) \
            == pytest.approx(100.0)

    def test_implausible_length_falls_back_to_shape(self):
        # A tag wildly off the geometry must not create a teleporting street.
        assert edge_length_m({"length": 5.0}, self.STRAIGHT_100M) \
            == pytest.approx(100.0)
        assert edge_length_m({"length": 5000.0}, self.STRAIGHT_100M) \
            == pytest.approx(100.0)

    def test_zero_geometry_still_positive(self):
        assert edge_length_m({}, [(3.0, 4.0), (3.0, 4.0)]) \
            == pytest.approx(0.1)
