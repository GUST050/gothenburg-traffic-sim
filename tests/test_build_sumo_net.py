"""Unit tests for build_sumo_net.py's OSM-tag parsing (speed/lane fallbacks) —
the pure functions netconvert's plain-XML edges are built from."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from build_sumo_net import parse_lanes, parse_speed_ms


class TestParseSpeedMs:
    def test_maxspeed_tag_wins(self):
        assert parse_speed_ms({"maxspeed": "50", "highway": "residential"}) \
            == pytest.approx(50 / 3.6)

    def test_maxspeed_as_scalar_list(self):
        # OSMnx sometimes stores tag values as single-element lists.
        assert parse_speed_ms({"maxspeed": ["70"]}) == pytest.approx(70 / 3.6)

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

    def test_odd_twoway_lane_count_floors_and_stays_at_least_one(self):
        assert parse_lanes({"lanes": "1", "highway": "primary"}) == 1

    def test_lane_count_capped_at_four(self):
        assert parse_lanes({"lanes": "20", "highway": "motorway", "oneway": "yes"}) == 4

    def test_lanes_as_scalar_list(self):
        assert parse_lanes({"lanes": ["4"], "highway": "primary"}) == 2

    def test_missing_lanes_uses_highway_default(self):
        assert parse_lanes({"highway": "secondary"}) == 1
        assert parse_lanes({"highway": "motorway"}) == 2

    def test_missing_everything_defaults_to_one(self):
        assert parse_lanes({}) == 1
