"""
Unit tests for signal_regulation.py (IMPROVEMENT_PLAN.md Phase D6, partial).

Real end-to-end SUMO execution of the "reg" program is verified manually
against real demand (matching D1-D5's established style); these tests
cover the parts that must be correct independent of SUMO: the TSFS
2014:30 lookup tables/formula, phase-rebuilding logic, and the
net.net.xml -> link-geometry parser against small constructed fixtures.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import signal_regulation as sr


class TestSpeedBucketMs:
    def test_exact_table_values(self):
        assert sr.speed_bucket_ms(30) == 8.0
        assert sr.speed_bucket_ms(40) == 10.0
        assert sr.speed_bucket_ms(50) == 12.0
        assert sr.speed_bucket_ms(60) == 14.0
        assert sr.speed_bucket_ms(70) == 15.0

    def test_below_table_range_clamps_to_lowest_tier(self):
        assert sr.speed_bucket_ms(15) == 8.0

    def test_above_table_range_clamps_to_highest_tier(self):
        assert sr.speed_bucket_ms(90) == 15.0

    def test_nearest_tier_for_an_in_between_speed(self):
        assert sr.speed_bucket_ms(47) == 12.0   # nearer 50 than 40
        assert sr.speed_bucket_ms(44) == 10.0   # nearer 40 than 50


class TestYellowTimeS:
    def test_below_60_is_four_seconds(self):
        assert sr.yellow_time_s(50) == 4.0

    def test_at_60_is_five_seconds(self):
        assert sr.yellow_time_s(60) == 5.0

    def test_above_60_is_five_seconds(self):
        assert sr.yellow_time_s(70) == 5.0


class TestRebuildPhases:
    def test_green_phase_floored_to_minimum(self):
        phases = [(2.0, "Grr"), (3.0, "yrr"), (2.0, "rGG"), (3.0, "ryy")]
        out = sr.rebuild_phases(phases, {})
        assert out[0] == (sr.MIN_GREEN_S, "Grr")

    def test_green_phase_above_minimum_is_untouched(self):
        phases = [(42.0, "Grr"), (3.0, "yrr"), (10.0, "rGG"), (3.0, "ryy")]
        out = sr.rebuild_phases(phases, {})
        assert out[0] == (42.0, "Grr")

    def test_yellow_time_uses_worst_case_speed_among_clearing_links(self):
        # link 0 is 40 km/h (-> 4s), link... only one clearing link here
        phases = [(20.0, "Grr"), (3.0, "yrr"), (20.0, "rGG"), (3.0, "ryy")]
        link_geo = {0: {"speed_kmh": 70.0, "clear_dist_m": 0.0}}
        out = sr.rebuild_phases(phases, link_geo)
        yellow_phase = next(p for p in out if p[1] == "yrr")
        assert yellow_phase[0] == 5.0   # 70 km/h -> TSFS >=60 -> 5s

    def test_default_yellow_when_no_geometry_known(self):
        phases = [(20.0, "Grr"), (3.0, "yrr"), (20.0, "rGG"), (3.0, "ryy")]
        out = sr.rebuild_phases(phases, {})
        yellow_phase = next(p for p in out if p[1] == "yrr")
        assert yellow_phase[0] == 4.0   # fallback 50 km/h -> <60 -> 4s

    def test_redyellow_inserted_before_the_next_green_phase(self):
        phases = [(20.0, "Grr"), (3.0, "yrr"), (20.0, "rGG"), (3.0, "ryy")]
        out = sr.rebuild_phases(phases, {})
        states = [p[1] for p in out]
        # a red+yellow phase for the newly-green link(s) must appear
        # somewhere between the "yrr" yellow and the "rGG" green
        assert "uGG" in states or "uGr" in states or "urr" in states

    def test_redyellow_only_marks_newly_green_links(self):
        # link 1 goes r->G (newly green); link 2 was already G and stays
        # G across the transition (an unrelated continuously-flowing
        # movement) -- must NOT be forced red/uncomfortably interrupted.
        phases = [(20.0, "rGr"), (3.0, "rGy"), (20.0, "GGr")]
        out = sr.rebuild_phases(phases, {})
        # find the inserted red+yellow phase (contains a 'u')
        redyellow = next(p for p in out if "u" in p[1])
        assert redyellow[1][0] == "u"    # link 0: newly green -> warm-up
        assert redyellow[1][1] == "G"    # link 1: continuously green, untouched
        assert redyellow[1][2] == "r"    # link 2: was yellow, now cleared

    def test_all_red_inserted_when_clearance_time_exceeds_redyellow(self):
        # a long clearing distance at a slow speed bucket needs more than
        # 1.5s -- the excess must show as a genuine all-red phase BEFORE
        # the red+yellow warm-up, not silently absorbed into it.
        phases = [(20.0, "Grr"), (3.0, "yrr"), (20.0, "rGG"), (3.0, "ryy")]
        link_geo = {0: {"speed_kmh": 30.0, "clear_dist_m": 40.0}}
        out = sr.rebuild_phases(phases, link_geo)
        idx = out.index(next(p for p in out if p[1] == "yrr"))
        allred = out[idx + 1]
        redyellow = out[idx + 2]
        assert allred[1] == "rrr"          # pure all-red, no 'u' or 'G'
        assert redyellow[0] == sr.REDYELLOW_S
        # t_s = (40 + 6) / 8.0 = 5.75s; allred = round(5.75 - 1.5, 1) = 4.2s
        # (Python's round() ties-to-even: round(4.25, 1) -> 4.2)
        assert allred[0] == pytest.approx(4.2, abs=0.01)

    def test_no_all_red_phase_when_clearance_time_is_small(self):
        phases = [(20.0, "Grr"), (3.0, "yrr"), (20.0, "rGG"), (3.0, "ryy")]
        link_geo = {0: {"speed_kmh": 70.0, "clear_dist_m": 0.0}}
        out = sr.rebuild_phases(phases, link_geo)
        idx = out.index(next(p for p in out if p[1] == "yrr"))
        # next phase right after yellow must be the redyellow phase itself
        # (contains 'u'), not a separate all-red phase
        assert "u" in out[idx + 1][1]

    def test_non_green_non_yellow_phase_passed_through_unchanged(self):
        phases = [(20.0, "Grr"), (3.0, "yrr"), (5.0, "rrr"), (20.0, "rGG"), (3.0, "ryy")]
        out = sr.rebuild_phases(phases, {})
        assert (5.0, "rrr") in out

    def test_empty_phase_list_returns_empty(self):
        assert sr.rebuild_phases([], {}) == []


class TestParseTlsLinkGeometry:
    def _build_net(self, tmp_path) -> Path:
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <edge id="A_in" from="A" to="J">
    <lane id="A_in_0" index="0" speed="13.89" length="50.0"/>
  </edge>
  <edge id="J_out" from="J" to="B">
    <lane id="J_out_0" index="0" speed="13.89" length="50.0"/>
  </edge>
  <edge id=":J_0" function="internal">
    <lane id=":J_0_0" index="0" speed="13.89" length="18.4"/>
  </edge>
  <tlLogic id="J" type="static" programID="0">
    <phase duration="42" state="G"/>
    <phase duration="3" state="y"/>
  </tlLogic>
  <connection from="A_in" to="J_out" via=":J_0_0" tl="J" linkIndex="0"/>
</net>""")
        return net_path

    def test_extracts_speed_and_clear_distance_for_a_real_link(self, tmp_path):
        net_path = self._build_net(tmp_path)
        geo = sr.parse_tls_link_geometry(net_path)
        assert geo["J"][0]["speed_kmh"] == pytest.approx(13.89 * 3.6, abs=0.01)
        assert geo["J"][0]["clear_dist_m"] == 18.4

    def test_connections_without_tl_are_ignored(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <edge id="A_in" from="A" to="B">
    <lane id="A_in_0" index="0" speed="13.89" length="50.0"/>
  </edge>
  <connection from="A_in" to="B_out" linkIndex="0"/>
</net>""")
        geo = sr.parse_tls_link_geometry(net_path)
        assert geo == {}

    def test_multiple_connections_sharing_a_linkindex_take_conservative_values(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <edge id="A_in" from="A" to="J">
    <lane id="A_in_0" index="0" speed="10.0" length="50.0"/>
  </edge>
  <edge id="B_in" from="B" to="J">
    <lane id="B_in_0" index="0" speed="20.0" length="50.0"/>
  </edge>
  <edge id=":J_0" function="internal">
    <lane id=":J_0_0" index="0" speed="10.0" length="15.0"/>
  </edge>
  <edge id=":J_1" function="internal">
    <lane id=":J_1_0" index="0" speed="20.0" length="30.0"/>
  </edge>
  <tlLogic id="J" type="static" programID="0">
    <phase duration="42" state="G"/>
  </tlLogic>
  <connection from="A_in" to="X" via=":J_0_0" tl="J" linkIndex="0"/>
  <connection from="B_in" to="X" via=":J_1_0" tl="J" linkIndex="0"/>
</net>""")
        geo = sr.parse_tls_link_geometry(net_path)
        # min speed (conservative: slower clears more slowly) and max
        # distance (conservative: longer clearing distance) win
        assert geo["J"][0]["speed_kmh"] == pytest.approx(10.0 * 3.6, abs=0.01)
        assert geo["J"][0]["clear_dist_m"] == 30.0


class TestBuildRegulationCompliantTls:
    def test_writes_one_program_per_real_tls_with_programid_reg(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <edge id="A_in" from="A" to="J">
    <lane id="A_in_0" index="0" speed="13.89" length="50.0"/>
  </edge>
  <edge id=":J_0" function="internal">
    <lane id=":J_0_0" index="0" speed="13.89" length="18.4"/>
  </edge>
  <tlLogic id="J1" type="static" programID="0">
    <phase duration="42" state="Grr"/>
    <phase duration="3" state="yrr"/>
    <phase duration="42" state="rGG"/>
    <phase duration="3" state="ryy"/>
  </tlLogic>
  <connection from="A_in" to="X" via=":J_0_0" tl="J1" linkIndex="0"/>
</net>""")
        out_path = tmp_path / "out.add.xml"
        n = sr.build_regulation_compliant_tls(net_path, out_path)
        assert n == 1
        root = ET.parse(out_path).getroot()
        tl = root.find("tlLogic")
        assert tl.get("id") == "J1"
        assert tl.get("programID") == "reg"
        assert tl.get("type") == "static"
        durations = [float(p.get("duration")) for p in tl.findall("phase")]
        assert sum(durations) > 90.0   # clearance/redyellow additions grow the cycle

    def test_junction_with_no_phases_is_skipped_not_crashed(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <tlLogic id="J1" type="static" programID="0"/>
</net>""")
        out_path = tmp_path / "out.add.xml"
        n = sr.build_regulation_compliant_tls(net_path, out_path)
        assert n == 0
        assert ET.parse(out_path).getroot().findall("tlLogic") == []


class TestEnforceTimingMinimums:
    def test_preserves_optimizer_program_id_and_enforces_safety_minimums(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <edge id="A" from="a" to="J"><lane id="A_0" speed="13.89" length="50"/></edge>
  <edge id=":J" function="internal"><lane id=":J_0" speed="13.89" length="20"/></edge>
  <connection from="A" to="B" via=":J_0" tl="J" linkIndex="0"/>
</net>""")
        proposed = tmp_path / "proposal.add.xml"
        proposed.write_text("""<additional><tlLogic id="J" type="static" programID="a" offset="7">
  <phase duration="2" state="Grr"/><phase duration="3" state="yrr"/>
  <phase duration="2" state="rGG"/><phase duration="3" state="ryy"/>
</tlLogic></additional>""")
        out = tmp_path / "safe.add.xml"

        assert sr.enforce_timing_minimums(net_path, proposed, out) == 1
        tl = ET.parse(out).getroot().find("tlLogic")
        phases = [(float(p.get("duration")), p.get("state")) for p in tl.findall("phase")]
        assert tl.get("programID") == "a"
        assert tl.get("offset") == "7"
        assert any(duration == sr.MIN_GREEN_S and state == "Grr" for duration, state in phases)
        assert any(duration == sr.yellow_time_s(50) and state == "yrr" for duration, state in phases)
        assert any(duration == sr.REDYELLOW_S and "u" in state for duration, state in phases)


class TestTimingCertificate:
    def test_certificate_passes_generated_regulation_plan(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <edge id="A" from="a" to="J"><lane id="A_0" speed="13.89" length="50"/></edge>
  <edge id=":J" function="internal"><lane id=":J_0" speed="13.89" length="20"/></edge>
  <connection from="A" to="B" via=":J_0" tl="J" linkIndex="0"/>
  <tlLogic id="J" type="static" programID="0">
    <phase duration="20" state="G"/><phase duration="3" state="y"/>
  </tlLogic>
</net>""")
        plan = tmp_path / "plan.add.xml"
        sr.build_regulation_compliant_tls(net_path, plan)
        certificate = sr.timing_certificate(net_path, plan)
        assert certificate["status"] == "pass"
        assert certificate["signals_checked"] == 1

    def test_certificate_fails_short_yellow_phase(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <edge id="A" from="a" to="J"><lane id="A_0" speed="13.89" length="50"/></edge>
  <tlLogic id="J" type="static" programID="0">
    <phase duration="20" state="G"/><phase duration="3" state="y"/>
  </tlLogic>
</net>""")
        plan = tmp_path / "plan.add.xml"
        plan.write_text("""<additional><tlLogic id="J" type="static" programID="x">
          <phase duration="20" state="G"/><phase duration="3" state="y"/>
        </tlLogic></additional>""")
        certificate = sr.timing_certificate(net_path, plan)
        assert certificate["status"] == "fail"
        assert any("gul fas" in error for error in certificate["errors"])

    def test_signal_plan_artifact_contains_mapping_and_phase_states(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <edge id="A" from="a" to="J"><lane id="A_0" speed="13.89" length="50"/></edge>
  <edge id=":J" function="internal"><lane id=":J_0" speed="13.89" length="20"/></edge>
  <connection from="A" to="B" via=":J_0" tl="J" linkIndex="0"/>
  <tlLogic id="J" type="static" programID="0">
    <phase duration="20" state="G"/><phase duration="4" state="y"/>
  </tlLogic>
</net>""")
        out = tmp_path / "plan.json"
        payload = sr.write_signal_plan_artifact(net_path, net_path, out)
        assert payload["kind"] == "SignalPlan"
        assert payload["controllers"][0]["links"]["0"]["from_edge"] == "A"
        assert payload["controllers"][0]["phases"][0]["green_links"] == [0]
        assert out.exists()


class TestJunctionGreenAllocation:
    def test_joint_budget_moves_green_from_low_to_high_demand_phase(self):
        phases = [(10.0, "Grr"), (4.0, "yrr"),
                  (10.0, "rGG"), (4.0, "ryy")]
        allocated = sr.allocate_junction_green_times(phases, {0: 90.0, 1: 5.0, 2: 5.0})
        # The total junction cycle is preserved, and phase 0 receives more
        # green only because the other green group receives less.
        assert sum(duration for duration, _ in allocated) == pytest.approx(28.0)
        assert allocated[0][0] > allocated[2][0] >= sr.MIN_GREEN_S

    def test_no_demand_falls_back_to_equal_green_allocation(self):
        phases = [(12.0, "Grr"), (4.0, "yrr"),
                  (8.0, "rGG"), (4.0, "ryy")]
        allocated = sr.allocate_junction_green_times(phases, {})
        assert allocated[0][0] == allocated[2][0] == 10.0

    def test_route_demands_follow_controlled_movements(self, tmp_path):
        net = tmp_path / "net.net.xml"
        net.write_text("""<net>
  <connection from="A" to="B" tl="J" linkIndex="0"/>
  <connection from="A" to="C" tl="J" linkIndex="1"/>
</net>""")
        routes = tmp_path / "demand.rou.xml"
        routes.write_text("""<routes>
  <vehicle id="a" depart="0"><route edges="A B"/></vehicle>
  <vehicle id="b" depart="0"><route edges="A B"/></vehicle>
  <vehicle id="c" depart="0"><route edges="A C"/></vehicle>
</routes>""")
        demands = sr.route_link_demands(routes, sr.parse_tls_movements(net))
        assert demands == {"J": {0: 2.0, 1: 1.0}}

    def test_no_window_counts_every_vehicle_in_the_file(self, tmp_path):
        # Default (begin_s=end_s=None) preserves the prior whole-file
        # behaviour for any caller that genuinely wants it.
        net = tmp_path / "net.net.xml"
        net.write_text('<net><connection from="A" to="B" tl="J" linkIndex="0"/></net>')
        routes = tmp_path / "demand.rou.xml"
        routes.write_text("""<routes>
  <vehicle id="morning" depart="1000"><route edges="A B"/></vehicle>
  <vehicle id="evening" depart="50000"><route edges="A B"/></vehicle>
</routes>""")
        demands = sr.route_link_demands(routes, sr.parse_tls_movements(net))
        assert demands == {"J": {0: 2.0}}

    def test_window_excludes_vehicles_departing_outside_it(self, tmp_path):
        # Found in review 2026-07-12: without a window, the demand-weighted
        # TLS condition allocated green time from the FULL demand file
        # (typically a whole day) rather than the analysis window it was
        # being optimized for and compared against.
        net = tmp_path / "net.net.xml"
        net.write_text('<net><connection from="A" to="B" tl="J" linkIndex="0"/></net>')
        routes = tmp_path / "demand.rou.xml"
        routes.write_text("""<routes>
  <vehicle id="before" depart="1000"><route edges="A B"/></vehicle>
  <vehicle id="during1" depart="25300"><route edges="A B"/></vehicle>
  <vehicle id="during2" depart="30000"><route edges="A B"/></vehicle>
  <vehicle id="after" depart="50000"><route edges="A B"/></vehicle>
</routes>""")
        demands = sr.route_link_demands(routes, sr.parse_tls_movements(net),
                                        begin_s=25200, end_s=32400)
        assert demands == {"J": {0: 2.0}}   # only during1/during2 counted

    def test_window_boundaries_are_half_open(self, tmp_path):
        net = tmp_path / "net.net.xml"
        net.write_text('<net><connection from="A" to="B" tl="J" linkIndex="0"/></net>')
        routes = tmp_path / "demand.rou.xml"
        routes.write_text("""<routes>
  <vehicle id="at_begin" depart="100"><route edges="A B"/></vehicle>
  <vehicle id="at_end" depart="200"><route edges="A B"/></vehicle>
</routes>""")
        demands = sr.route_link_demands(routes, sr.parse_tls_movements(net),
                                        begin_s=100, end_s=200)
        assert demands == {"J": {0: 1.0}}   # [begin_s, end_s) -- at_begin in, at_end out

    def test_unparseable_depart_is_skipped_not_crashed(self, tmp_path):
        net = tmp_path / "net.net.xml"
        net.write_text('<net><connection from="A" to="B" tl="J" linkIndex="0"/></net>')
        routes = tmp_path / "demand.rou.xml"
        routes.write_text("""<routes>
  <vehicle id="triggered" depart="triggered"><route edges="A B"/></vehicle>
  <vehicle id="normal" depart="25300"><route edges="A B"/></vehicle>
</routes>""")
        demands = sr.route_link_demands(routes, sr.parse_tls_movements(net),
                                        begin_s=25200, end_s=32400)
        assert demands == {"J": {0: 1.0}}   # only "normal" counted
