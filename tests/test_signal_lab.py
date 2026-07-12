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


def write_net(path: Path, tl_logics_xml: str) -> None:
    path.write_text(f"<net>{tl_logics_xml}</net>")


class TestTlsPlanDiff:
    """PLAN.md D5: per-junction cycle/split/offset diff, baseline
    net.net.xml vs the tlsCycleAdaptation/tlsCoordinator additional
    files — pure XML parsing/diffing, no SUMO invocation."""

    def test_shorter_optimized_cycle_and_a_real_offset(self, tmp_path):
        net = tmp_path / "net.net.xml"
        write_net(net, """
        <tlLogic id="J1" type="static" programID="0" offset="0">
          <phase duration="42" state="GGrr"/>
          <phase duration="3" state="yyrr"/>
          <phase duration="42" state="rrGG"/>
          <phase duration="3" state="rryy"/>
        </tlLogic>""")
        adapted = tmp_path / "adapted.add.xml"
        write_net(adapted, """
        <tlLogic id="J1" type="static" programID="a" offset="0">
          <phase duration="12" state="GGrr"/>
          <phase duration="4" state="yyrr"/>
          <phase duration="4" state="rrGG"/>
          <phase duration="4" state="rryy"/>
        </tlLogic>""")
        coordinated = tmp_path / "coordinated.add.xml"
        write_net(coordinated, """
        <tlLogic id="J1" programID="a" offset="12.5"/>""")

        diffs = signal_lab.tls_plan_diff(net, adapted, coordinated)
        assert len(diffs) == 1
        d = diffs[0]
        assert d["tls_id"] == "J1"
        assert d["cycle_before_s"] == 90.0
        assert d["cycle_after_s"] == 24.0
        assert d["cycle_delta_pct"] == pytest.approx(-73.3, abs=0.1)
        assert d["offset_before_s"] == 0.0
        assert d["offset_after_s"] == 12.5   # coordinated's offset, not adapted's 0
        assert d["n_phases_before"] == d["n_phases_after"] == 4
        assert d["max_split_change_pct"] is not None

    def test_no_coordinated_file_keeps_adapted_offset(self, tmp_path):
        net = tmp_path / "net.net.xml"
        write_net(net, """
        <tlLogic id="J1" type="static" programID="0" offset="0">
          <phase duration="45" state="GG"/>
          <phase duration="45" state="rr"/>
        </tlLogic>""")
        adapted = tmp_path / "adapted.add.xml"
        write_net(adapted, """
        <tlLogic id="J1" type="static" programID="a" offset="7">
          <phase duration="20" state="GG"/>
          <phase duration="20" state="rr"/>
        </tlLogic>""")

        diffs = signal_lab.tls_plan_diff(net, adapted, coordinated_path=None)
        assert diffs[0]["offset_after_s"] == 7.0

    def test_mismatched_phase_count_reports_no_split_comparison(self, tmp_path):
        net = tmp_path / "net.net.xml"
        write_net(net, """
        <tlLogic id="J1" type="static" programID="0" offset="0">
          <phase duration="42" state="GGrr"/>
          <phase duration="3" state="yyrr"/>
          <phase duration="42" state="rrGG"/>
          <phase duration="3" state="rryy"/>
        </tlLogic>""")
        adapted = tmp_path / "adapted.add.xml"
        write_net(adapted, """
        <tlLogic id="J1" type="static" programID="a" offset="0">
          <phase duration="30" state="GGrr"/>
          <phase duration="30" state="rrGG"/>
        </tlLogic>""")

        diffs = signal_lab.tls_plan_diff(net, adapted)
        assert diffs[0]["n_phases_before"] == 4
        assert diffs[0]["n_phases_after"] == 2
        assert diffs[0]["max_split_change_pct"] is None

    def test_junction_only_in_baseline_is_skipped_not_crashed(self, tmp_path):
        net = tmp_path / "net.net.xml"
        write_net(net, """
        <tlLogic id="J1" type="static" programID="0" offset="0">
          <phase duration="45" state="GG"/>
          <phase duration="45" state="rr"/>
        </tlLogic>
        <tlLogic id="J2" type="static" programID="0" offset="0">
          <phase duration="45" state="GG"/>
          <phase duration="45" state="rr"/>
        </tlLogic>""")
        adapted = tmp_path / "adapted.add.xml"
        write_net(adapted, """
        <tlLogic id="J1" type="static" programID="a" offset="0">
          <phase duration="20" state="GG"/>
          <phase duration="20" state="rr"/>
        </tlLogic>""")

        diffs = signal_lab.tls_plan_diff(net, adapted)
        assert len(diffs) == 1
        assert diffs[0]["tls_id"] == "J1"

    def test_coordinated_offset_for_an_unmatched_junction_is_ignored(self, tmp_path):
        net = tmp_path / "net.net.xml"
        write_net(net, """
        <tlLogic id="J1" type="static" programID="0" offset="0">
          <phase duration="45" state="GG"/>
          <phase duration="45" state="rr"/>
        </tlLogic>""")
        adapted = tmp_path / "adapted.add.xml"
        write_net(adapted, """
        <tlLogic id="J1" type="static" programID="a" offset="0">
          <phase duration="20" state="GG"/>
          <phase duration="20" state="rr"/>
        </tlLogic>""")
        coordinated = tmp_path / "coordinated.add.xml"
        write_net(coordinated, """
        <tlLogic id="J99" programID="a" offset="99"/>""")

        diffs = signal_lab.tls_plan_diff(net, adapted, coordinated)
        assert diffs[0]["offset_after_s"] == 0.0

    def test_results_are_sorted_by_tls_id(self, tmp_path):
        net = tmp_path / "net.net.xml"
        write_net(net, """
        <tlLogic id="Z9" type="static" programID="0" offset="0">
          <phase duration="45" state="GG"/><phase duration="45" state="rr"/>
        </tlLogic>
        <tlLogic id="A1" type="static" programID="0" offset="0">
          <phase duration="45" state="GG"/><phase duration="45" state="rr"/>
        </tlLogic>""")
        adapted = tmp_path / "adapted.add.xml"
        write_net(adapted, """
        <tlLogic id="Z9" type="static" programID="a" offset="0">
          <phase duration="20" state="GG"/><phase duration="20" state="rr"/>
        </tlLogic>
        <tlLogic id="A1" type="static" programID="a" offset="0">
          <phase duration="20" state="GG"/><phase duration="20" state="rr"/>
        </tlLogic>""")

        diffs = signal_lab.tls_plan_diff(net, adapted)
        assert [d["tls_id"] for d in diffs] == ["A1", "Z9"]


class TestTlsTimingSchedule:
    def test_reports_green_red_yellow_and_coordinated_offset_per_link(self, tmp_path):
        adapted = tmp_path / "adapted.add.xml"
        write_net(adapted, """
        <tlLogic id="J1" type="static" programID="a" offset="0">
          <phase duration="10" state="Gr"/>
          <phase duration="4" state="yr"/>
          <phase duration="8" state="rG"/>
          <phase duration="4" state="ry"/>
        </tlLogic>""")
        coordinated = tmp_path / "coordinated.add.xml"
        write_net(coordinated, '<tlLogic id="J1" programID="a" offset="6"/>')

        rows = signal_lab.tls_timing_schedule(adapted, coordinated)
        assert rows[0] == {"tls_id": "J1", "link_index": 0, "cycle_s": 26.0,
                           "offset_s": 6.0, "all_red_s": 0.0, "green_s": 10.0,
                           "yellow_s": 4.0, "redyellow_s": 0.0, "red_s": 12.0}
        assert rows[1]["green_s"] == 8.0
        assert rows[1]["red_s"] == 14.0


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
