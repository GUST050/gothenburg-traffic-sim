"""Unit tests for the dirsplit geometry — bad geometry means bad labels,
so these are the load-bearing tests of the direction-split subsystem."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from dirsplit.geo import (ang_diff_deg, bearing_deg, circular_mean_deg,
                          haversine_m, in_bbox, is_ahead, point_to_polyline_m,
                          radial_cos)


class TestBearing:
    def test_north(self):
        assert bearing_deg(57.0, 11.0, 58.0, 11.0) == pytest.approx(0.0, abs=0.1)

    def test_east(self):
        assert bearing_deg(57.0, 11.0, 57.0, 12.0) == pytest.approx(90.0, abs=0.1)

    def test_south(self):
        assert bearing_deg(58.0, 11.0, 57.0, 11.0) == pytest.approx(180.0, abs=0.1)

    def test_west(self):
        assert bearing_deg(57.0, 12.0, 57.0, 11.0) == pytest.approx(270.0, abs=0.1)

    def test_coslat_correction_at_high_latitude(self):
        # At 60°N one degree of longitude is only ~cos(60°)=0.5 degrees of
        # latitude. A displacement of (1° lat, 2° lon) is therefore a true
        # 45° bearing — the uncorrected atan2 would give ~63°.
        b = bearing_deg(60.0, 10.0, 61.0, 12.0)
        assert b == pytest.approx(45.0, abs=1.5)


class TestAngles:
    def test_diff_wraps(self):
        assert ang_diff_deg(350, 10) == pytest.approx(20)
        assert ang_diff_deg(10, 350) == pytest.approx(20)

    def test_opposite(self):
        assert ang_diff_deg(0, 180) == pytest.approx(180)


class TestCircularMean:
    """Found in a review 2026-07-10: observability.py's corridor_priors()
    averaged two sensor bearings with naive (b1+b2)/2, which is wrong
    whenever they straddle 0/360 — 350 and 10 (both near due north,
    ang_diff_deg only 20) averaged to 180 (due SOUTH), the exact opposite
    direction. circular_mean_deg fixes this via vector averaging."""

    def test_matches_naive_average_away_from_wraparound(self):
        assert circular_mean_deg(80, 100) == pytest.approx(90, abs=0.01)

    def test_correct_across_the_north_wraparound(self):
        # 0 and 360 are the same bearing — compare via the circular-aware
        # ang_diff_deg, not a raw pytest.approx(0), which 360.0 would fail.
        assert ang_diff_deg(circular_mean_deg(350, 10), 0) == pytest.approx(0, abs=0.01)
        assert ang_diff_deg(circular_mean_deg(10, 350), 0) == pytest.approx(0, abs=0.01)

    def test_naive_average_would_have_been_backwards(self):
        naive = (350 + 10) / 2
        assert naive == pytest.approx(180)   # the bug this replaces
        assert ang_diff_deg(circular_mean_deg(350, 10), naive) == pytest.approx(180)


class TestHalfPlane:
    def test_ahead_straight(self):
        assert is_ahead(0, 0)

    def test_behind(self):
        assert not is_ahead(180, 0)

    def test_boundary_left_right(self):
        assert is_ahead(89, 0)
        assert not is_ahead(91, 0)


class TestRadialCos:
    def test_toward_centre(self):
        assert radial_cos(45, 45) == pytest.approx(1.0)

    def test_away_from_centre(self):
        assert radial_cos(225, 45) == pytest.approx(-1.0)

    def test_perpendicular(self):
        assert radial_cos(135, 45) == pytest.approx(0.0, abs=1e-9)


class TestMisc:
    def test_haversine_known_distance(self):
        # 1 degree of latitude ≈ 111.2 km
        assert haversine_m(57.0, 11.0, 58.0, 11.0) == pytest.approx(111_200, rel=0.01)

    def test_bbox(self):
        assert in_bbox(63.4, 10.4, (63.35, 10.25, 63.46, 10.55))
        assert not in_bbox(63.5, 10.4, (63.35, 10.25, 63.46, 10.55))


class TestPointToPolyline:
    def test_point_on_line(self):
        line = [(11.0, 57.0), (11.01, 57.0)]   # (lon, lat), ~600 m east-west
        assert point_to_polyline_m(57.0, 11.005, line) == pytest.approx(0.0, abs=0.5)

    def test_point_beside_long_segment_midpoint(self):
        # Station 100 m north of the middle of a 2 km segment: vertex-based
        # distance would report ~1000 m; segment projection must give ~100 m.
        line = [(11.0, 57.0), (11.033, 57.0)]
        d = point_to_polyline_m(57.0009, 11.0165, line)
        assert d == pytest.approx(100.0, rel=0.05)

    def test_point_past_endpoint(self):
        # Beyond the end, distance is to the endpoint, never negative-t
        line = [(11.0, 57.0), (11.001, 57.0)]
        d = point_to_polyline_m(57.0, 11.003, line)
        assert d == pytest.approx(haversine_m(57.0, 11.003, 57.0, 11.001), rel=0.02)


class TestSharedFeatureContract:
    def test_feature_names_stable(self):
        # Training data and target edges must produce the same vector layout.
        from dirsplit.features import FEATURE_NAMES
        assert FEATURE_NAMES[0] == "hw_rank"
        assert "radial_cos" in FEATURE_NAMES
        assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))
