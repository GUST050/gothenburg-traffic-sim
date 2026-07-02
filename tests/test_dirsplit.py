"""Unit tests for the dirsplit geometry — bad geometry means bad labels,
so these are the load-bearing tests of the direction-split subsystem."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from dirsplit.geo import (ang_diff_deg, bearing_deg, haversine_m, in_bbox,
                          is_ahead, radial_cos)


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


class TestSharedFeatureContract:
    def test_feature_names_stable(self):
        # Training data and target edges must produce the same vector layout.
        from dirsplit.features import FEATURE_NAMES
        assert FEATURE_NAMES[0] == "hw_rank"
        assert "radial_cos" in FEATURE_NAMES
        assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))
