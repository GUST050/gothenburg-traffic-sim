"""
Unit tests for signal_closure_combine.py (PLAN.md Phase D4).

The heavy end (actually closing a road, running micro SUMO twice, and
optimizing signals against the extracted routes) is exercised manually
against real demand, not here — mirrors signal_lab.py's (D1)/
signal_optimize.py's (D2)/suggest_closure_time.py's (C4) test style. These
tests cover the two pure-logic pieces this module adds: pulling the
actually-driven route out of a --vehroute-output file (the "last route in
routeDistribution wins" rule, verified 2026-07-11 against 139 real rerouted
vehicles), and comparing two such extractions for route-choice stability.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import signal_closure_combine as scc


def write_vehroute(path: Path, xml: str) -> None:
    path.write_text(xml)


class TestExtractFinalRoutes:
    def test_rerouted_vehicle_takes_the_last_route_in_the_distribution(self, tmp_path):
        vr = tmp_path / "vehroute.xml"
        write_vehroute(vr, """<routes>
  <vehicle id="0" depart="100.00">
    <routeDistribution>
      <route replacedOnEdge="a" reason="rerouter" replacedAtTime="150.00"
             probability="0" edges="a b c"/>
      <route cost="10" exitTimes="100 110 120" edges="a d e"/>
    </routeDistribution>
  </vehicle>
</routes>""")
        out = tmp_path / "extracted.rou.xml"
        n = scc.extract_final_routes(vr, out)
        assert n == 1
        root = ET.parse(out).getroot()
        veh = root.find("vehicle")
        assert veh.get("id") == "0"
        assert veh.get("depart") == "100.00"
        assert veh.find("route").get("edges") == "a d e"
        # superseded plan and its markers must not leak into the extracted file
        assert veh.find("routeDistribution") is None

    def test_non_rerouted_vehicle_keeps_its_direct_route(self, tmp_path):
        vr = tmp_path / "vehroute.xml"
        write_vehroute(vr, """<routes>
  <vehicle id="1" depart="200.00">
    <route cost="5" exitTimes="200 210" edges="f g h"/>
  </vehicle>
</routes>""")
        out = tmp_path / "extracted.rou.xml"
        n = scc.extract_final_routes(vr, out)
        assert n == 1
        veh = ET.parse(out).getroot().find("vehicle")
        assert veh.find("route").get("edges") == "f g h"

    def test_mixed_file_extracts_every_vehicle_correctly(self, tmp_path):
        vr = tmp_path / "vehroute.xml"
        write_vehroute(vr, """<routes>
  <vehicle id="0" depart="100.00">
    <routeDistribution>
      <route replacedOnEdge="a" reason="rerouter" probability="0" edges="a b c"/>
      <route exitTimes="100 110" edges="a d e"/>
    </routeDistribution>
  </vehicle>
  <vehicle id="1" depart="200.00">
    <route exitTimes="200 210" edges="f g h"/>
  </vehicle>
</routes>""")
        out = tmp_path / "extracted.rou.xml"
        n = scc.extract_final_routes(vr, out)
        assert n == 2
        ids = {veh.get("id"): veh.find("route").get("edges")
               for veh in ET.parse(out).getroot().findall("vehicle")}
        assert ids == {"0": "a d e", "1": "f g h"}

    def test_vehicle_with_no_route_at_all_is_skipped_not_crashed(self, tmp_path):
        vr = tmp_path / "vehroute.xml"
        write_vehroute(vr, """<routes>
  <vehicle id="0" depart="100.00"/>
</routes>""")
        out = tmp_path / "extracted.rou.xml"
        n = scc.extract_final_routes(vr, out)
        assert n == 0
        assert ET.parse(out).getroot().findall("vehicle") == []


class TestRouteStability:
    def test_identical_routes_in_both_passes_score_1_0(self, tmp_path):
        vr1 = tmp_path / "vr1.xml"
        vr2 = tmp_path / "vr2.xml"
        write_vehroute(vr1, """<routes>
  <vehicle id="0" depart="100.00"><route exitTimes="100 110" edges="a b c"/></vehicle>
  <vehicle id="1" depart="200.00"><route exitTimes="200 210" edges="d e f"/></vehicle>
</routes>""")
        write_vehroute(vr2, """<routes>
  <vehicle id="0" depart="100.00"><route exitTimes="100 108" edges="a b c"/></vehicle>
  <vehicle id="1" depart="200.00"><route exitTimes="200 209" edges="d e f"/></vehicle>
</routes>""")
        result = scc.route_stability(vr1, vr2)
        assert result["n_common_vehicles"] == 2
        assert result["n_identical_routes"] == 2
        assert result["fraction_identical"] == 1.0

    def test_a_changed_route_lowers_the_fraction(self, tmp_path):
        vr1 = tmp_path / "vr1.xml"
        vr2 = tmp_path / "vr2.xml"
        write_vehroute(vr1, """<routes>
  <vehicle id="0" depart="100.00"><route exitTimes="100 110" edges="a b c"/></vehicle>
  <vehicle id="1" depart="200.00"><route exitTimes="200 210" edges="d e f"/></vehicle>
</routes>""")
        write_vehroute(vr2, """<routes>
  <vehicle id="0" depart="100.00"><route exitTimes="100 110" edges="a b c"/></vehicle>
  <vehicle id="1" depart="200.00"><route exitTimes="200 205" edges="d x f"/></vehicle>
</routes>""")
        result = scc.route_stability(vr1, vr2)
        assert result["n_common_vehicles"] == 2
        assert result["n_identical_routes"] == 1
        assert result["fraction_identical"] == 0.5

    def test_uses_the_final_route_of_a_routedistribution_on_both_sides(self, tmp_path):
        vr1 = tmp_path / "vr1.xml"
        vr2 = tmp_path / "vr2.xml"
        write_vehroute(vr1, """<routes>
  <vehicle id="0" depart="100.00">
    <routeDistribution>
      <route replacedOnEdge="a" reason="rerouter" probability="0" edges="a b c"/>
      <route exitTimes="100 110" edges="a d e"/>
    </routeDistribution>
  </vehicle>
</routes>""")
        write_vehroute(vr2, """<routes>
  <vehicle id="0" depart="100.00"><route exitTimes="100 109" edges="a d e"/></vehicle>
</routes>""")
        result = scc.route_stability(vr1, vr2)
        assert result["fraction_identical"] == 1.0

    def test_no_common_vehicle_ids_reports_not_measurable(self, tmp_path):
        vr1 = tmp_path / "vr1.xml"
        vr2 = tmp_path / "vr2.xml"
        write_vehroute(vr1, """<routes>
  <vehicle id="0" depart="100.00"><route exitTimes="100 110" edges="a b c"/></vehicle>
</routes>""")
        write_vehroute(vr2, """<routes>
  <vehicle id="99" depart="900.00"><route exitTimes="900 910" edges="x y z"/></vehicle>
</routes>""")
        result = scc.route_stability(vr1, vr2)
        assert result["n_common_vehicles"] == 0
        assert result["fraction_identical"] is None
        assert "note" in result
