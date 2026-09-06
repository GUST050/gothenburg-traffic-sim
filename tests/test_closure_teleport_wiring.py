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
    def test_transient_closure_route_errors_are_tolerated_for_post_run_gates(
            self, tmp_path):
        """Hard SUMO closings may raise at insertion before population and
        closed-edge-throughput checks can evaluate the completed run."""
        cmd = _invocation(tmp_path)
        assert cmd[cmd.index("--ignore-route-errors") + 1] == "true"

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
    """2026-08-29: the core monthly/closure pipeline (run_scenario.py,
    suggest_closure_time.py, monthly_sumo.py) migrated from the disabled-
    teleport policy to `closure_teleport.CLOSURE_ROUTING_TELEPORT_POLICY_S`
    (SUMO's own default), because `closure_routing.py` now rewrites every
    affected route around a closure before SUMO starts -- there is nothing
    left for a teleport to leak onto, so suppressing teleporting network-wide
    is no longer necessary and was the root cause of indefinite-gridlock
    wall-time timeouts. See closure_routing.py's module docstring."""

    def test_run_scenario_defaults_the_cli_to_the_closure_policy(self):
        source = Path(rs.__file__).read_text()
        assert "default=ct.CLOSURE_ROUTING_TELEPORT_POLICY_S" in source, (
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
                == ct.CLOSURE_ROUTING_TELEPORT_POLICY_S)

    def test_simulate_closure_withholds_the_policy_from_the_baseline_arm(self):
        source = Path(sct.__file__).read_text()
        assert ("seed_teleport_policy = time_to_teleport_s if close_edges "
                "else None") in source

    def test_warm_and_cold_closure_arms_share_one_policy_constant(self):
        """The warm arm is an OPTIMISATION over an equivalent cold arm. If only
        one of them applied a different teleport policy they would no longer
        be equivalent, and the paired validation would be comparing two
        different simulations."""
        warm = Path(
            rs.__file__).parent / "traffic_sim/simulation/monthly_sumo.py"
        if not warm.is_file():
            warm = Path(__file__).resolve().parents[1] / \
                "traffic_sim/simulation/monthly_sumo.py"
        source = warm.read_text()
        assert "closure_teleport.CLOSURE_ROUTING_TELEPORT_POLICY_S" in source
        assert "if closures else None" in source
        # The retired policy must not have simply moved rather than retired.
        assert "closure_teleport.CLOSURE_TIME_TO_TELEPORT_S" not in source

    def test_production_closure_routing_no_longer_calls_the_retired_truncator(self):
        """Root requirement: no production entry point may depend on the
        retired truncate-and-hope-the-runtime-rerouter-fixes-it path."""
        import inspect
        assert "truncate_stranded_vehicles" not in inspect.getsource(
            rs.prepare_variant_job)
        assert "truncate_stranded_vehicles" not in inspect.getsource(
            sct.simulate_closure)


class TestEveryClosureSimulatorAgrees:
    """One policy, or the paths disagree about what a closure is.

    KNOWN, DELIBERATE DIVERGENCE (2026-08-29): run_scenario's scenario path
    and the monthly campaign path through suggest_closure_time now rewrite
    every affected route around a closure before SUMO starts
    (`closure_routing.py`) and no longer need disabled teleporting to keep
    closed-edge throughput at zero -- see `TestClosurePathWiring`'s
    docstring. The D4 signal study (`signal_optimize.py`) and the persistent-
    SUMO benchmark (`tools/benchmark_persistent_sumo.py`) were NOT migrated
    in this pass (out of the named scope: monthly road-closure simulation
    timeouts) and still rely on the retired truncate-only preprocessor plus
    disabled teleporting to hold their own closed-edge throughput at zero.
    That combination is still internally self-consistent for those two
    tools, so it is left untouched rather than partially updated (removing
    only the disabled-teleport half without also replacing their route
    preparation would have been a real regression). Migrating them is
    tracked as remaining work, not silently assumed done.
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
    @staticmethod
    def _metrics_pair():
        from traffic_sim.simulation.metrics import DisruptionMetrics
        common = dict(total_time_loss_s=1.0, trip_count=5, unfinished_trips=0,
                      unfinished_waiting_trips=0, teleport_total=0,
                      teleport_reasons={}, loaded=5, inserted=5,
                      running_at_end=0, waiting_at_end=0,
                      max_queue_vehicles=3)
        return (DisruptionMetrics(closed_edge_throughput=0, **common),
                DisruptionMetrics(**common))

    def test_default_argument_still_describes_the_legacy_disabled_policy(self):
        """`closure_feasibility`'s own bare default is unchanged
        (`ct.CLOSURE_TIME_TO_TELEPORT_S`, historically -1/disabled) -- this
        pins that the function's DEFAULT ARGUMENT itself did not silently
        change. Production callers must not rely on it (see the next test):
        every real monthly_sumo.py call site now passes the policy actually
        supplied to SUMO explicitly (review finding 3, 2026-08-29)."""
        metrics, baseline = self._metrics_pair()
        result = sct.closure_feasibility(metrics, baseline)
        assert result["teleport_policy"]["teleport_count_is_informative"] is False
        assert result["teleport_policy"]["teleporting_enabled"] is False
        assert result["eligible"] is True

    def test_production_policy_reports_teleporting_enabled(self):
        """FIXED (review finding 3, 2026-08-29): every real closure SUMO run
        (cold via `simulate_closure`'s own default, warm via
        `_default_warm_invoker`'s explicit argument) is launched with
        `closure_teleport.CLOSURE_ROUTING_TELEPORT_POLICY_S` (SUMO's own
        default, i.e. teleporting ENABLED) once closure_routing has already
        rewritten every affected route around the closure. `monthly_sumo.py`
        must tell `closure_feasibility` this exact value rather than let it
        fall back to the legacy `-1` default and publish a false
        "teleporting disabled" provenance claim. Teleport and closed-edge-
        throughput hard-failure gates remain fully active either way --
        this only changes whether the ABSENCE of a teleport failure counts
        as evidence."""
        metrics, baseline = self._metrics_pair()
        result = sct.closure_feasibility(
            metrics, baseline,
            time_to_teleport_s=ct.CLOSURE_ROUTING_TELEPORT_POLICY_S)
        assert result["teleport_policy"]["teleport_count_is_informative"] is True
        assert result["teleport_policy"]["teleporting_enabled"] is True
        assert result["eligible"] is True

    def test_monthly_sumo_passes_the_actual_teleport_policy_to_feasibility(self):
        """Guards against the exact regression the review found: a call
        site that omits `time_to_teleport_s` and silently inherits the
        legacy `-1` default. Both production call sites now pass it
        explicitly, keyed off `self.close_edges` exactly like the real SUMO
        invocation is."""
        import traffic_sim.simulation.monthly_sumo as ms
        source = Path(ms.__file__).read_text()
        assert source.count(
            "time_to_teleport_s=(closure_teleport.CLOSURE_ROUTING_TELEPORT_"
            "POLICY_S\n                                     if self.close_"
            "edges else None)") == 2
