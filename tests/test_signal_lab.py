"""
Unit tests for signal_lab.py (PLAN.md Phase D1).

The heavy end (an actual micro SUMO run) is exercised manually against real
demand, not here — these tests cover the parts that must be correct
independent of SUMO: window-offset parsing and the fingerprint helpers.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import signal_lab


class TestWindowOffsetsS:
    def test_default_seven_to_nine_on_a_midnight_epoch(self):
        begin_s, end_s = signal_lab.window_offsets_s(
            "2025-09-16T00:00:00", "07:00", "09:00")
        assert (begin_s, end_s) == (7 * 3600, 9 * 3600)

    def test_offsets_relative_to_a_non_midnight_epoch(self):
        # epoch_sim itself is 06:00 (matches the demand-morning target) —
        # a 07:00-09:00 window is still 1h-3h after THAT epoch, not 7h-9h.
        begin_s, end_s = signal_lab.window_offsets_s(
            "2025-09-16T06:00:00", "07:00", "09:00")
        assert (begin_s, end_s) == (1 * 3600, 3 * 3600)

    def test_window_end_before_start_raises(self):
        with pytest.raises(ValueError):
            signal_lab.window_offsets_s("2025-09-16T00:00:00", "09:00", "07:00")

    def test_window_end_equal_start_raises(self):
        with pytest.raises(ValueError):
            signal_lab.window_offsets_s("2025-09-16T00:00:00", "08:00", "08:00")

    def test_sub_hour_window(self):
        begin_s, end_s = signal_lab.window_offsets_s(
            "2025-09-16T00:00:00", "07:00", "07:30")
        assert (begin_s, end_s) == (7 * 3600, 7 * 3600 + 1800)


class TestNetFingerprint:
    def test_same_bytes_same_fingerprint(self, tmp_path):
        p1 = tmp_path / "a.net.xml"
        p2 = tmp_path / "b.net.xml"
        p1.write_bytes(b"<net>same</net>")
        p2.write_bytes(b"<net>same</net>")
        assert signal_lab.net_fingerprint(p1) == signal_lab.net_fingerprint(p2)

    def test_different_bytes_different_fingerprint(self, tmp_path):
        p1 = tmp_path / "a.net.xml"
        p2 = tmp_path / "b.net.xml"
        p1.write_bytes(b"<net>one</net>")
        p2.write_bytes(b"<net>two</net>")
        assert signal_lab.net_fingerprint(p1) != signal_lab.net_fingerprint(p2)


class TestSumoVersion:
    def test_unreachable_binary_returns_unknown_not_raises(self, tmp_path):
        # home points nowhere real -- must degrade gracefully, never crash
        # a run just because version reporting failed.
        assert signal_lab.sumo_version(tmp_path) == "unknown"


class TestMainWindowValidation:
    """main()'s guard against a window outside the calibrated demand
    period — exercised through window_offsets_s + the same bound check
    main() applies, without needing a real demand_meta.json/SUMO."""

    def test_window_fitting_inside_total_duration_is_valid(self):
        begin_s, end_s = signal_lab.window_offsets_s(
            "2025-09-16T00:00:00", "07:00", "09:00")
        total_duration_s = 96 * 900   # whole day
        assert 0 <= begin_s < end_s <= total_duration_s

    def test_window_past_total_duration_is_invalid(self):
        begin_s, end_s = signal_lab.window_offsets_s(
            "2025-09-16T00:00:00", "23:00", "23:59")
        total_duration_s = 16 * 900   # the small 4h demand-morning window
        assert not (0 <= begin_s < end_s <= total_duration_s)
