"""The teleport policy reaches SUMO on the closure path, and nowhere else.

A policy that exists in a module and never reaches the process is the seam
failure this project has hit before: LUNA-WARM-08 built a boundary connector,
tested it, and wired it to nothing a real campaign would use. These tests
assert the DEFAULT behaviour of the production call sites rather than an
injected value.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_scenario as rs
import suggest_closure_time as sct
from traffic_sim.simulation import closure_teleport as ct


def _invocation(tmp_path, **kwargs):
    route = tmp_path / "calibrated.rou.xml"
    route.write_text("<routes/>")
    cmd, _paths, _cwd = rs.build_sumo_invocation(
        1000, route, [], 3600, Path("/nonexistent-sumo-home"),
        work_dir=tmp_path, **kwargs)
    return cmd


class TestInvocation:
    def test_omitting_the_policy_leaves_argv_untouched(self, tmp_path):
        """Every pre-existing caller passes nothing. If that emitted a flag,
        the warm/cold argv equivalence contract would break everywhere at
        once."""
        assert "--time-to-teleport" not in _invocation(tmp_path)

    def test_the_default_is_no_policy_not_the_closure_policy(self, tmp_path):
        """The builder must not decide the policy for its callers: a baseline
        run goes through the same builder and must keep SUMO's default."""
        import inspect
        signature = inspect.signature(rs.build_sumo_invocation)
        assert signature.parameters["time_to_teleport_s"].default is None

    def test_the_policy_becomes_a_sumo_option(self, tmp_path):
        cmd = _invocation(tmp_path, time_to_teleport_s=ct.CLOSURE_TIME_TO_TELEPORT_S)
        assert cmd[cmd.index("--time-to-teleport") + 1] == "-1"

    def test_an_unusable_policy_is_refused_before_sumo_starts(self, tmp_path):
        with pytest.raises(ct.ClosureTeleportPolicyError):
            _invocation(tmp_path, time_to_teleport_s=0)

    def test_run_sumo_forwards_the_policy(self, tmp_path, monkeypatch):
        seen = {}

        def fake_build(*args, **kwargs):
            seen.update(kwargs)
            return ["true"], {}, tmp_path

        monkeypatch.setattr(rs, "build_sumo_invocation", fake_build)
        monkeypatch.setattr(rs.subprocess, "run",
                            lambda *a, **k: type("P", (), {"returncode": 0})())
        route = tmp_path / "r.rou.xml"
        route.write_text("<routes/>")
        rs.run_sumo(1, route, [], 60, Path("/x"), time_to_teleport_s=-1)
        assert seen["time_to_teleport_s"] == -1

    def test_routing_experiment_flags_are_opt_in_and_precise(self, tmp_path):
        cmd = _invocation(tmp_path, rerouting_threads=2,
                          routing_algorithm="astar")
        assert cmd[cmd.index("--device.rerouting.threads") + 1] == "2"
        assert cmd[cmd.index("--routing-algorithm") + 1] == "astar"

    def test_invalid_routing_experiment_is_refused_before_sumo(self, tmp_path):
        with pytest.raises(ValueError, match="rerouting_threads"):
            _invocation(tmp_path, rerouting_threads=0)
        with pytest.raises(ValueError, match="rerouting_threads"):
            _invocation(tmp_path, rerouting_threads=1.5)
        with pytest.raises(ValueError, match="routing_algorithm"):
            _invocation(tmp_path, routing_algorithm="not-an-algorithm")

    def test_sumo_warnings_can_be_enabled_for_diagnosis(self, tmp_path):
        assert "--no-warnings" in _invocation(tmp_path)
        assert "--no-warnings" not in _invocation(
            tmp_path, suppress_warnings=False)

    def test_routing_experiment_cannot_publish_as_the_production_arm(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "argv", [
            "run_scenario.py", "--routing-algorithm", "astar",
        ])
        with pytest.raises(SystemExit):
            rs.parse_args()

        monkeypatch.setattr(sys, "argv", [
            "run_scenario.py", "--routing-algorithm", "astar",
            "--out-dir", str(tmp_path / "output"),
            "--timing-sidecar", str(tmp_path / "timing.json"),
        ])
        args = rs.parse_args()
        assert args.routing_algorithm == "astar"

    def test_non_default_rerouter_radius_is_an_isolated_experiment(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "argv", [
            "run_scenario.py", "--rerouter-radius-m", "1000",
        ])
        with pytest.raises(SystemExit):
            rs.parse_args()

        monkeypatch.setattr(sys, "argv", [
            "run_scenario.py", "--rerouter-radius-m", "1000",
            "--out-dir", str(tmp_path / "output"),
            "--timing-sidecar", str(tmp_path / "timing.json"),
        ])
        assert rs.parse_args().rerouter_radius_m == 1000


class TestClosurePathWiring:
    def test_run_scenario_defaults_the_cli_to_the_closure_policy(self):
        source = Path(rs.__file__).read_text()
        assert "default=ct.CLOSURE_TIME_TO_TELEPORT_S" in source, (
            "--time-to-teleport must default to the deployed closure policy; "
            "a flag nobody passes changes nothing")

    def test_run_scenario_only_applies_the_policy_when_something_is_closed(self):
        source = Path(rs.__file__).read_text()
        assert ("teleport_policy_s = args.time_to_teleport if close_edges "
                "else None") in source, (
            "a baseline run must keep SUMO's default: its teleports are the "
            "congestion signal the health gate reads")

    def test_simulate_closure_defaults_to_the_policy(self):
        import inspect
        signature = inspect.signature(sct.simulate_closure)
        assert (signature.parameters["time_to_teleport_s"].default
                == ct.CLOSURE_TIME_TO_TELEPORT_S)

    def test_simulate_closure_withholds_the_policy_from_the_baseline_arm(self):
        source = Path(sct.__file__).read_text()
        assert ("seed_teleport_policy = time_to_teleport_s if close_edges "
                "else None") in source

    def test_warm_and_cold_closure_arms_share_one_policy_constant(self):
        """The warm arm is an OPTIMISATION over an equivalent cold arm. If only
        one of them stopped teleporting they would no longer be equivalent, and
        the paired validation would be comparing two different simulations."""
        warm = Path(
            rs.__file__).parent / "traffic_sim/simulation/monthly_sumo.py"
        if not warm.is_file():
            warm = Path(__file__).resolve().parents[1] / \
                "traffic_sim/simulation/monthly_sumo.py"
        source = warm.read_text()
        assert "closure_teleport.CLOSURE_TIME_TO_TELEPORT_S" in source
        assert "if closures else None" in source


class TestEveryClosureSimulatorAgrees:
    """One policy, or the paths disagree about what a closure is.

    Three places simulate a closure: run_scenario's scenario path, the campaign
    path through suggest_closure_time, and the D4 signal study. If only some of
    them stopped teleporting, `disqualification_reasons()` would be judging
    different simulations depending on which tool produced the run.
    """

    def test_the_signal_study_applies_the_policy_to_a_real_closure(self):
        import signal_optimize
        source = Path(signal_optimize.__file__).read_text()
        assert ("condition_teleport_policy = (ct.CLOSURE_TIME_TO_TELEPORT_S\n"
                "                                 if closed_edges else None)"
                ) in source
        assert "time_to_teleport_s=condition_teleport_policy" in source

    def test_the_persistent_sumo_benchmark_mirrors_production(self, tmp_path):
        """The benchmark's claim is that it reproduces the real production
        artifact. A closure query that still teleported would be benchmarking a
        simulation production no longer performs."""
        from tools import benchmark_persistent_sumo as harness
        route = tmp_path / "r.rou.xml"
        route.write_text("<routes/>")
        common = dict(seed=1, route_path=route, add_paths=[route],
                      edgedata_out=tmp_path / "ed.xml",
                      statistics_out=tmp_path / "st.xml",
                      vehroute_out=None, traci_server=False)
        baseline = harness.build_sumo_args(**common, closed_edges=[])
        closure = harness.build_sumo_args(**common, closed_edges=["a_b_0"])
        assert "--time-to-teleport" not in baseline
        assert closure[closure.index("--time-to-teleport") + 1] == "-1"

    def test_the_benchmark_attaches_the_policy_only_to_a_closure_payload(self):
        from tools import benchmark_persistent_sumo as harness
        source = Path(harness.__file__).read_text()
        assert "_closure_teleport_policy() if closed_edges else None" in source


class TestFeasibilityReporting:
    def test_feasibility_records_the_policy_beside_the_hard_failures(self):
        """With teleporting off, the ABSENCE of a "teleports" failure is not
        evidence. Recording the policy is what stops a vacuous check reading as
        a passed one."""
        from traffic_sim.simulation.metrics import DisruptionMetrics
        common = dict(total_time_loss_s=1.0, trip_count=5, unfinished_trips=0,
                      unfinished_waiting_trips=0, teleport_total=0,
                      teleport_reasons={}, loaded=5, inserted=5,
                      running_at_end=0, waiting_at_end=0,
                      max_queue_vehicles=3)
        metrics = DisruptionMetrics(closed_edge_throughput=0, **common)
        result = sct.closure_feasibility(metrics, DisruptionMetrics(**common))
        assert result["teleport_policy"]["teleport_count_is_informative"] is False
        assert result["eligible"] is True
