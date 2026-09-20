"""Vehicles a closure catches mid-trip must be able to re-plan.

`closingReroute` only offers a new route to a vehicle that ENTERS a
rerouter edge while the closure is active. A vehicle that committed moments
before it opened, or that queues in spillback outside the 400 m ring, is
never told: it keeps its calibrated route, cannot enter the sealed edge and
— with teleporting disabled — waits at the barrier. Measured on the
fixture below: 3 589 s of time loss with a legal turn one junction away.

The policy is OFF by default because it changes what a closure costs, so
the tests pin both halves: that it does nothing unless asked, and that it
actually works when asked.
"""
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_scenario
from traffic_sim.simulation import closure_rerouting as cr



class TestThePolicyOptions:
    def test_off_emits_nothing_at_all(self):
        # A run that never opted in must keep a byte-identical command line.
        assert cr.sumo_arguments(None) == []

    def test_on_emits_a_period_and_the_congestion_guard(self):
        args = cr.sumo_arguments(60)
        assert args[:2] == ["--device.rerouting.period", "60"]
        assert "--device.rerouting.threshold.factor" in args

    def test_it_never_emits_the_options_that_silently_disable_it(self):
        """Measured trap: each of these makes the policy a no-op.

        `--device.rerouting.adaptation-weight 1` and
        `--device.rerouting.adaptation-interval 0` look like the obvious way
        to stop the router chasing congestion. Both were measured to stop it
        re-planning at all — the committed vehicle waited its full 3 589 s —
        so a run configured that way reports the policy as enabled while
        doing nothing. The guard is the threshold factor instead.
        """
        args = cr.sumo_arguments(60)
        assert "--device.rerouting.adaptation-weight" not in args
        assert "--device.rerouting.adaptation-interval" not in args
        # mode 8 "ignores temporary blockages", i.e. routes THROUGH a closure.
        assert "--device.rerouting.mode" not in args

    @pytest.mark.parametrize("bad", [0, -1, -60, True, 1.5, "60"])
    def test_a_period_that_means_never_is_refused(self, bad):
        with pytest.raises(cr.ClosureReroutingPolicyError):
            cr.normalize_period(bad)

    def test_the_record_states_that_it_moves_the_measurement(self):
        assert cr.policy_record(None)["changes_measured_closure_cost"] is False
        on = cr.policy_record(60)
        assert on["changes_measured_closure_cost"] is True
        assert "closed edge" in on["equipped_population"]
        assert cr.policy_label(None) == "disabled"


class TestOnlyTheAffectedVehiclesAreEquipped:
    """Granting the device globally is what C1 rejected: it changes route
    choice for the whole calibrated demand. Only vehicles whose own route
    uses a closed edge in the window, and that keep a detour, get it."""

    def _routes(self, tmp_path):
        path = tmp_path / "in.rou.xml"
        path.write_text(
            "<routes>\n"
            # crosses the closed edge and has a detour -> equip
            '  <vehicle id="detourable" depart="0">'
            '<route edges="lead closed dest"/></vehicle>\n'
            # never touches the closed edge -> untouched
            '  <vehicle id="elsewhere" depart="0">'
            '<route edges="lead other"/></vehicle>\n'
            "</routes>\n")
        return path

    def _net(self, tmp_path, monkeypatch):
        net = tmp_path / "net.net.xml"
        net.write_text('<net>\n  <connection from="lead" to="closed"/>\n'
                       '  <connection from="lead" to="bypass"/>\n'
                       '  <connection from="bypass" to="dest"/>\n'
                       '  <connection from="closed" to="dest"/>\n'
                       '  <connection from="lead" to="other"/>\n</net>')
        monkeypatch.setattr(run_scenario, "NET_PATH", net)
        return net

    def test_the_device_is_off_unless_asked(self, tmp_path, monkeypatch):
        self._net(tmp_path, monkeypatch)
        out = tmp_path / "out.rou.xml"
        run_scenario.truncate_stranded_vehicles(
            self._routes(tmp_path), ["closed"], out,
            run_scenario.build_edge_graph({"closed"}))
        assert "has.rerouting.device" not in out.read_text()

    def test_only_the_detourable_affected_vehicle_is_equipped(
            self, tmp_path, monkeypatch):
        self._net(tmp_path, monkeypatch)
        out = tmp_path / "out.rou.xml"
        equipped = []
        run_scenario.truncate_stranded_vehicles(
            self._routes(tmp_path), ["closed"], out,
            run_scenario.build_edge_graph({"closed"}),
            reroute_committed=True, equipped=equipped)

        assert equipped == ["detourable"]
        root = ET.parse(out).getroot()
        by_id = {v.get("id"): v for v in root.findall("vehicle")}
        assert [p.get("key") for p in by_id["detourable"].findall("param")] \
            == ["has.rerouting.device"]
        assert by_id["elsewhere"].findall("param") == []

    def test_equipping_is_idempotent(self, tmp_path, monkeypatch):
        self._net(tmp_path, monkeypatch)
        vehicle = ET.fromstring(
            '<vehicle id="v"><param key="has.rerouting.device" value="false"/>'
            '<route edges="lead closed dest"/></vehicle>')
        run_scenario.equip_rerouting_device(vehicle)
        run_scenario.equip_rerouting_device(vehicle)
        params = vehicle.findall("param")
        assert len(params) == 1 and params[0].get("value") == "true"


@pytest.mark.skipif(shutil.which("sumo") is None or
                    shutil.which("netconvert") is None,
                    reason="SUMO is not installed")
class TestAgainstRealSumo:
    """The mechanism, against the simulator rather than against argv.

    This project has been burned by a policy that was tested at argv level
    and wired to nothing a real campaign used, so the behaviour gets one
    test that runs SUMO. The corridor built below has a detour leaving the
    junction at the END of `near`, so a vehicle committed to `near` when the
    closure opens has a legal turn available and still waits without the
    policy. It is purpose-built rather than the c1 fixture, whose approach
    is 50 m long — there the window in which a vehicle can be committed
    BEFORE the closure and still arrive AFTER it is five seconds wide.
    """

    CLOSURE_BEGIN_S = 900
    CLOSURE_END_S = 4500

    def _build(self, work):
        (work / "nodes.xml").write_text(
            '<nodes>\n'
            '  <node id="a" x="0" y="0"/>    <node id="b" x="600" y="0"/>\n'
            '  <node id="c" x="900" y="0"/>  <node id="d" x="1000" y="0"/>\n'
            '  <node id="e" x="1300" y="0"/> <node id="f" x="950" y="400"/>\n'
            '</nodes>\n')
        (work / "edges.xml").write_text(
            '<edges>\n'
            '  <edge id="far"    from="a" to="b" numLanes="1" speed="10"/>\n'
            '  <edge id="near"   from="b" to="c" numLanes="1" speed="10"/>\n'
            '  <edge id="closed" from="c" to="d" numLanes="1" speed="10"/>\n'
            '  <edge id="out"    from="d" to="e" numLanes="1" speed="10"/>\n'
            '  <edge id="det_1"  from="c" to="f" numLanes="1" speed="10"/>\n'
            '  <edge id="det_2"  from="f" to="d" numLanes="1" speed="10"/>\n'
            '</edges>\n')
        net = work / "net.net.xml"
        subprocess.run(["netconvert", "-n", str(work / "nodes.xml"),
                        "-e", str(work / "edges.xml"), "-o", str(net),
                        "--no-turnarounds", "true", "--no-warnings", "true"],
                       check=True, capture_output=True)
        # The rerouter reaches `near` and the closed edge — the 400 m ring
        # this project deploys — not `far`, where the vehicle is when the
        # closure opens.
        (work / "closure.add.xml").write_text(
            '<additional>\n  <rerouter id="closure" edges="near closed">\n'
            f'    <interval begin="{self.CLOSURE_BEGIN_S}" '
            f'end="{self.CLOSURE_END_S}">\n'
            '      <closingReroute id="closed" disallow="all"/>\n'
            "    </interval>\n  </rerouter>\n</additional>\n")
        return net

    def _route(self, work, *, equipped):
        # depart 820: it enters `near` — its last rerouter edge — at 880,
        # twenty seconds BEFORE the closure opens at 900, so the rerouter
        # never fires for it. Its turn at the end of `near` stays legal.
        param = ('<param key="has.rerouting.device" value="true"/>'
                 if equipped else "")
        path = work / f"{'on' if equipped else 'off'}.rou.xml"
        path.write_text(
            '<routes>\n  <vType id="car" accel="2.6" decel="4.5" length="5"/>\n'
            f'  <vehicle id="committed" type="car" depart="820">{param}'
            '<route edges="far near closed out"/></vehicle>\n'
            "</routes>\n")
        return path

    def _run(self, work, net, route, period_s):
        out = work / f"tripinfo_{period_s}.xml"
        cmd = ["sumo", "-n", str(net), "-r", str(route),
               "-a", str(work / "closure.add.xml"),
               "--mesosim", "true", "--begin", "0", "--end", "9000",
               "--no-step-log", "true", "--no-warnings", "true",
               "--ignore-route-errors", "true", "--time-to-teleport", "-1",
               "--tripinfo-output", str(out), *cr.sumo_arguments(period_s)]
        subprocess.run(cmd, check=True, capture_output=True, cwd=str(work))
        trip = next(ET.parse(out).getroot().iter("tripinfo"))
        return float(trip.get("timeLoss"))

    def test_without_the_policy_the_committed_vehicle_waits_out_the_closure(
            self, tmp_path):
        net = self._build(tmp_path)
        waited = self._run(tmp_path, net, self._route(tmp_path, equipped=False),
                           None)
        # It sits at the barrier until the closure lifts, so its time loss is
        # most of the closure itself rather than a detour's extra minutes.
        assert waited > 0.5 * (self.CLOSURE_END_S - self.CLOSURE_BEGIN_S)

    def test_with_the_policy_it_takes_the_turn_instead(self, tmp_path):
        net = self._build(tmp_path)
        waited = self._run(tmp_path, net, self._route(tmp_path, equipped=False),
                           None)
        diverted = self._run(tmp_path, net, self._route(tmp_path, equipped=True),
                             cr.DEFAULT_PERIOD_S)
        assert diverted < 0.1 * waited, (
            f"equipped vehicle still waited: {diverted}s against {waited}s")
