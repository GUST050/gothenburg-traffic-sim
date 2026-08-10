"""Does the Stage 3 policy actually stop the leak? Measured against real SUMO.

Everything else about stage 3 is argv-level and fake-driven. This project has
been burned by exactly that gap before — LUNA-WARM-08 built a boundary
connector, tested it, and wired it to nothing a real campaign would use — so
the mechanism gets one test that runs the simulator.

The fixture is `tools/c1_temporary_closure_probe`, an eight-edge synthetic
network whose lower chain `no_in -> no_closed -> no_out` has NO detour. That is
the shape the whole plan is about: `CLAUDE.md` records node 3575001205 with
exactly one incoming connection, so closing that edge structurally cut off
everything downstream, and SUMO's stuck-vehicle relocation then carried the
waiting vehicle straight through the closure.

SCOPE, stated honestly: eight edges and one vehicle. It demonstrates the
MECHANISM against the real simulator. It is not the Stage 3 gate, which is a
paired measurement on the deployed demand
(`tools/measure_closure_teleport_policy.py`), and it says nothing about how
many vehicles a real closure strands.

Skipped when SUMO is not installed, so the suite still runs without it.
"""
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from traffic_sim.simulation import closure_teleport as ct
from traffic_sim.simulation.metrics import active_closure_throughput

PROBE = ROOT / "tools" / "c1_temporary_closure_probe"
CLOSED_EDGE = "no_closed"
SIM_END_S = 1800
INTERVAL_S = 900

pytestmark = pytest.mark.skipif(
    shutil.which("sumo") is None or shutil.which("netconvert") is None,
    reason="SUMO is not installed")


def _build_network(work: Path) -> Path:
    net = work / "net.net.xml"
    subprocess.run(
        ["netconvert", "-n", str(PROBE / "net.nod.xml"),
         "-e", str(PROBE / "net.edg.xml"), "-o", str(net),
         "--no-warnings", "true"],
        check=True, capture_output=True)
    return net


def _write_additionals(work: Path) -> tuple[Path, Path]:
    """EdgeData over the whole run, and a closure that spans all of it.

    The closure must cover every measured bucket: `active_closure_throughput`
    counts only fully-contained 15-minute intervals, precisely so a bucket
    straddling the closure boundary cannot be blamed on the closure. A probe
    whose closure ends mid-run therefore measures nothing at all — which is
    itself worth stating, because a first cut of this test did exactly that and
    read a legitimate post-reopening entry as a leak.
    """
    edgedata = work / "ed.add.xml"
    edgedata.write_text(
        '<additional>\n'
        f'  <edgeData id="ed" file="edgedata.xml" period="{INTERVAL_S}" '
        f'begin="0" end="{SIM_END_S}" excludeEmpty="true"/>\n'
        '</additional>\n')
    closure = work / "closure.add.xml"
    closure.write_text(
        '<additional>\n'
        '  <rerouter id="closure" edges="no_in">\n'
        f'    <interval begin="0" end="{SIM_END_S}">\n'
        f'      <closingReroute id="{CLOSED_EDGE}" disallow="all"/>\n'
        '    </interval>\n'
        '  </rerouter>\n'
        '</additional>\n')
    return edgedata, closure


def _run(work: Path, label: str, time_to_teleport_s) -> dict:
    net = _build_network(work)
    edgedata, closure = _write_additionals(work)
    stats = work / f"stats_{label}.xml"
    tripinfo = work / f"trip_{label}.xml"
    subprocess.run(
        ["sumo", "-n", str(net), "-r", str(PROBE / "long.rou.xml"),
         "-a", f"{edgedata},{closure}",
         "--seed", "1000", "--begin", "0", "--end", str(SIM_END_S),
         "--no-step-log", "true", "--no-warnings", "true",
         "--ignore-route-errors", "true",
         "--mesosim", "true", "--meso-junction-control", "true",
         "--meso-junction-control.limited", "true",
         "--statistic-output", str(stats),
         "--tripinfo-output", str(tripinfo),
         "--tripinfo-output.write-unfinished", "true",
         *ct.sumo_arguments(time_to_teleport_s)],
        cwd=work, check=True, capture_output=True)

    quarters = [0.0] * (SIM_END_S // INTERVAL_S)
    for interval in ET.parse(work / "edgedata.xml").getroot().iter("interval"):
        index = int(float(interval.get("begin")) // INTERVAL_S)
        for edge in interval.iter("edge"):
            if edge.get("id") == CLOSED_EDGE and index < len(quarters):
                quarters[index] += float(edge.get("entered", 0))
    (work / "edgedata.xml").rename(work / f"edgedata_{label}.xml")

    teleports = ET.parse(stats).getroot().find("teleports")
    trips = list(ET.parse(tripinfo).getroot().iter("tripinfo"))
    return {
        "closed_edge_throughput": active_closure_throughput(
            {CLOSED_EDGE: quarters},
            [{"edge_id": CLOSED_EDGE, "begin_s": 0, "end_s": SIM_END_S}]),
        "teleport_total": int(teleports.get("total", 0)) if teleports is not None else 0,
        "unfinished_trips": sum(
            1 for trip in trips
            if trip.get("arrival") is None or float(trip.get("arrival", -1)) < 0),
        "dropped_unreachable": 0,      # nothing is filtered in this fixture
    }


@pytest.fixture(scope="module")
def arms(tmp_path_factory):
    """Both arms of the same closure, differing only in the teleport option."""
    return {
        "sumo_default": _run(tmp_path_factory.mktemp("default"),
                             "default", None),
        "policy": _run(tmp_path_factory.mktemp("policy"),
                       "policy", ct.CLOSURE_TIME_TO_TELEPORT_S),
    }


def test_the_default_arm_reproduces_the_leak(arms):
    """Without this, the other arm's zero would prove nothing: it would be a
    zero from a closure nothing tried to cross."""
    before = arms["sumo_default"]
    assert before["teleport_total"] == 1
    assert before["closed_edge_throughput"] == 1, (
        "the fixture must reproduce the leak SUMO's own docs describe: a "
        "gridlocked vehicle relocated along its own route, through the closure")
    assert before["unfinished_trips"] == 0, (
        "and it must look like a completed trip, which is what makes the leak "
        "invisible without this measurement")


def test_the_policy_arm_stops_the_leak(arms):
    after = arms["policy"]
    assert after["teleport_total"] == 0
    assert after["closed_edge_throughput"] == 0


def test_the_vehicle_becomes_a_trip_that_did_not_arrive(arms):
    """The plan's requirement in one assertion: a vehicle that cannot get
    through must show up as *not arriving*, never as *driving through*."""
    assert arms["sumo_default"]["unfinished_trips"] == 0
    assert arms["policy"]["unfinished_trips"] == 1


def test_the_stage_3_gate_passes_on_this_case(arms):
    """One vehicle, and it genuinely has no detour, so the budget is one."""
    gate = ct.evaluate_stage3_gate(before=arms["sumo_default"],
                                   after=arms["policy"],
                                   detour_less_vehicles=1)
    assert gate["passed"] is True
    assert gate["measured"]["stuck_growth"] == 1


def test_the_gate_would_fail_if_the_stuck_population_exceeded_its_budget(arms):
    """The budget is real: had this vehicle possessed a detour, the same
    outcome would be a failure, not a success."""
    gate = ct.evaluate_stage3_gate(before=arms["sumo_default"],
                                   after=arms["policy"],
                                   detour_less_vehicles=0)
    assert gate["passed"] is False
