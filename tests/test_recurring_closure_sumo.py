"""Small real-SUMO proof that recurring closure intervals reopen correctly."""

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import run_scenario
from traffic_sim.simulation.runtime import sumo_home


PROBE = Path("tools/c1_temporary_closure_probe")


def test_real_sumo_closes_reopens_and_closes_same_edge_again(tmp_path):
    binary = sumo_home() / "bin" / "sumo"
    network = PROBE / "net.net.xml"
    if not binary.is_file() or not network.is_file():
        pytest.skip("isolated SUMO closure probe is unavailable")

    routes = tmp_path / "recurring.rou.xml"
    routes.write_text("""<routes>
  <vType id="car" accel="2.6" decel="4.5" length="5" maxSpeed="10"/>
  <vehicle id="before" type="car" depart="0"><route edges="direct_in closed direct_out"/></vehicle>
  <vehicle id="during_1" type="car" depart="20"><route edges="direct_in closed direct_out"/></vehicle>
  <vehicle id="between" type="car" depart="120"><route edges="direct_in closed direct_out"/></vehicle>
  <vehicle id="during_2" type="car" depart="220"><route edges="direct_in closed direct_out"/></vehicle>
  <vehicle id="after" type="car" depart="320"><route edges="direct_in closed direct_out"/></vehicle>
</routes>""")
    additional = tmp_path / "recurring.add.xml"
    run_scenario.write_closure_additional(
        additional,
        [
            {"edge_id": "closed", "begin_s": 10, "end_s": 100},
            {"edge_id": "closed", "begin_s": 200, "end_s": 300},
        ],
        ["direct_in"],
    )
    vehroute = tmp_path / "vehroute.xml"

    completed = subprocess.run(
        [
            str(binary),
            "-n", str(network.resolve()),
            "-r", str(routes.resolve()),
            "-a", str(additional.resolve()),
            "--vehroute-output", str(vehroute.resolve()),
            "--vehroute-output.exit-times", "true",
            "--end", "500",
            "--no-step-log", "true",
            "--no-warnings", "true",
            # This isolated probe deliberately feeds a route that becomes
            # invalid under the runtime rerouter. Production invocations do
            # not carry this parser escape hatch.
            "--ignore-route-errors", "true",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    driven = {}
    for vehicle in ET.parse(vehroute).getroot().iter("vehicle"):
        routes_for_vehicle = vehicle.findall(".//route")
        assert routes_for_vehicle
        driven[vehicle.get("id")] = routes_for_vehicle[-1].get("edges").split()
    assert "closed" in driven["before"]
    assert "closed" not in driven["during_1"]
    assert "closed" in driven["between"]
    assert "closed" not in driven["during_2"]
    assert "closed" in driven["after"]
