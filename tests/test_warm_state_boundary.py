"""Exact-time snapshot and meso-accumulator accounting (LUNA-WARM-25).

Process-free by construction: every simulator interaction goes through an
injected fake, and guard tests assert no real `traci` import, no socket, no
subprocess and no `runs/` access.

The central property is identity-exhaustive reconstruction from SUMO's private
mesoscopic tripinfo accumulator. TraCI waiting time is not accepted as that
accumulator; unfinished prefix tripinfo and resumed tripinfo are joined before
one production-precision round per whole vehicle.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import tokenize
from pathlib import Path

import pytest

from traffic_sim.simulation.warm_state_boundary import (
    MESO_TIMELOSS_PRECISION,
    SNAPSHOT_FACTS_SCHEMA, SNAPSHOT_PRECISION_FLAG, SNAPSHOT_RNG_FLAG,
    TRIPINFO_PRECISION, WARM_OUTPUT_PRECISION, BoundaryLedgerError,
    WarmPrefixController, aggregate_split_time_loss, build_prefix_accumulator,
    capture_boundary_active, capture_snapshot_facts, check_time_loss,
    normalize_time_loss, parse_tripinfo_time_loss,
    reconcile_resumed_tripinfo, snapshot_digest, snapshot_settings_arguments,
    validate_prefix_accumulator, validate_resumed_command,
    validate_snapshot_command,
    validate_snapshot_facts)
from traffic_sim.simulation.warm_state_cache import (
    STATE_PRECISION, STATE_RNG_SAVED)

MODULE = Path("traffic_sim/simulation/warm_state_boundary.py")


# ── Shared fixture builders, imported by the other warm-state suites ─────────

def snapshot_facts(point: int = 900, *, active: int = 0, **over) -> dict:
    base = {"schema": SNAPSHOT_FACTS_SCHEMA, "warm_point_s": point,
            "captured_at_s": float(point), "active_vehicle_count": active,
            "save_state_rng": True, "save_state_precision": STATE_PRECISION}
    base.update(over)
    return base


def aggregation(*, total_time_loss_s: float, prefix_trips: int,
                resumed_trips: int, precision: int = TRIPINFO_PRECISION) -> dict:
    """An aggregation summary consistent with the surrounding fixture.

    `boundary_offset_applied` is always False: production refuses True, because
    the refuted v3 rule must be visibly absent rather than merely unmentioned.
    """
    return {
        "total_time_loss_s": total_time_loss_s,
        "trip_count": prefix_trips + resumed_trips,
        "completed_prefix_trips": prefix_trips,
        "resumed_trips": resumed_trips,
        "boundary_offset_applied": False,
        "tripinfo_precision": precision,
    }


def bounded_sections(point: int, *, total_time_loss_s: float,
                     prefix_trips: int, resumed_trips: int,
                     active: int = 0) -> dict:
    """Both bounded diagnostic fields at once, mutually consistent."""
    return {
        "snapshot_facts": snapshot_facts(point, active=active),
        "prefix_aggregation": aggregation(
            total_time_loss_s=total_time_loss_s, prefix_trips=prefix_trips,
            resumed_trips=resumed_trips),
    }


def split_aggregation(prefix_evidence, post_warm) -> dict:
    """The aggregation a split implies: simply the two segment aggregates."""
    prefix = prefix_evidence["completed_trips"]
    return aggregate_split_time_loss(
        completed_prefix_total=prefix["total_time_loss_s"],
        completed_prefix_trips=prefix["trip_count"],
        resumed_total=post_warm["total_time_loss_s"],
        resumed_trips=post_warm["trip_count"])


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeConnection:
    """An injected stand-in for a TraCI connection. Imports nothing."""

    def __init__(self, now, active, time_loss=None):
        self._now = now
        self._active = list(active)
        self.time_loss = dict(time_loss or {})
        outer = self

        class _Simulation:
            @staticmethod
            def getTime():
                return outer._now

            @staticmethod
            def getDeltaT():
                return 1.0

            @staticmethod
            def saveState(path):
                outer.saved = (path, outer._now)

        class _Vehicle:
            @staticmethod
            def getIDList():
                return list(outer._active)

            @staticmethod
            def getTimeLoss(vehicle):
                # LUNA-WARM-23: the prefix phase now also reads each active
                # vehicle's accumulated time loss, from this same connection at
                # this same instant.
                return outer.time_loss.get(vehicle, 0.0)

        self.saved = None
        self.simulation = _Simulation()
        self.vehicle = _Vehicle()


class FakeTraci(FakeConnection):
    """Adds the stepping surface the controller drives.

    `simulationStep(target)` advances TO the requested target, like the real
    call, so the controller's bounded chunked loop converges. `step_to` caps how
    far this simulation is willing to go, which is how a run that ends early —
    landing short of the warm point — is modelled.
    """

    def __init__(self, *, active=(), step_to=900.0, start=0.0, steps_stall=False):
        super().__init__(start, active)
        self._step_to = step_to
        self._steps_stall = steps_stall
        self.closed = False
        self.port = None
        self.step_targets = []

    def init(self, port):
        self.port = port

    def simulationStep(self, target):
        self.step_targets.append(target)
        if self._steps_stall:
            return                                    # never advances
        self._now = min(float(target), self._step_to)

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, returncode=0, poll_result=None, wait_raises=False,
                 stderr_text=""):
        self.waited = False
        self.killed = False
        self.returncode = returncode
        self._poll_result = poll_result
        self._wait_raises = wait_raises
        self.stderr_text = stderr_text

    def poll(self):
        return self._poll_result

    def wait(self, timeout=None):
        self.waited = True
        if self._wait_raises and timeout is not None:
            self._wait_raises = False
            raise RuntimeError("wait timed out")
        return self.returncode

    def kill(self):
        self.killed = True
        self._poll_result = self.returncode


def _clock(values):
    """A monotonic clock that returns each value in turn, then holds the last."""
    remaining = list(values)

    def tick():
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]
    return tick


def _controller(traci, process=None, **over):
    process = process or FakeProcess()

    def launcher(cmd, *, cwd, home, port, stderr_path):
        Path(stderr_path).write_text(process.stderr_text, encoding="utf-8")
        return process

    return WarmPrefixController(traci_module=traci, launcher=launcher,
                                port=12345, sleep=lambda s: None,
                                **over), process


def _cmd(*extra):
    return ["sumo", "-n", "net.xml",
            "--tripinfo-output.write-unfinished", "true",
            "--precision", str(WARM_OUTPUT_PRECISION),
            *snapshot_settings_arguments(), *extra]


# ── The refuted rule is gone ─────────────────────────────────────────────────

class TestNoBoundaryOffsetIsEverApplied:
    """Criteria 1 and 6. v3 added a per-vehicle offset; LUNA-WARM-08 measured
    that the resumed tripinfo already carries the accumulator, so it double
    counted."""

    def test_the_objective_is_the_sum_of_the_two_aggregates(self):
        result = aggregate_split_time_loss(
            completed_prefix_total=100.0, completed_prefix_trips=3,
            resumed_total=50.0, resumed_trips=2)
        assert result["total_time_loss_s"] == 150.0
        assert result["trip_count"] == 5

    def test_the_old_double_count_is_reproduced_and_not_performed(self):
        """The exact v3 arithmetic, kept as a guard on the reasoning.

        A vehicle in flight at the snapshot had accrued 30 s and finished with
        150 s total. Its resumed tripinfo reports the whole 150 s, so v3's
        `resumed + ledger_offset` produced 180 s. v4 reports 150 s.
        """
        resumed_total = 150.0               # the full trip, accumulator preserved
        v3_ledger_offset = 30.0
        assert resumed_total + v3_ledger_offset == 180.0

        result = aggregate_split_time_loss(
            completed_prefix_total=0.0, completed_prefix_trips=0,
            resumed_total=resumed_total, resumed_trips=1)
        assert result["total_time_loss_s"] == 150.0
        assert result["boundary_offset_applied"] is False

    def test_the_summary_records_that_no_offset_was_applied(self):
        """A reviewer should not have to prove a negative by reading code."""
        result = aggregate_split_time_loss(
            completed_prefix_total=1.0, completed_prefix_trips=1,
            resumed_total=2.0, resumed_trips=1)
        assert result["boundary_offset_applied"] is False

    @pytest.mark.parametrize("prefix,post", [
        (0.0, 0.0), (1.005, 1.005), (0.005, 0.005), (12345.67, 8765.43),
    ])
    def test_aggregation_is_exact_with_no_half_rounding(self, prefix, post):
        """Aggregates are whole values, so nothing is rounded twice — the
        residual v3 could never eliminate does not arise here."""
        result = aggregate_split_time_loss(
            completed_prefix_total=prefix, completed_prefix_trips=1,
            resumed_total=post, resumed_trips=1)
        assert result["total_time_loss_s"] == normalize_time_loss(
            normalize_time_loss(prefix) + normalize_time_loss(post))

    @pytest.mark.parametrize("bad", [-1, True, 1.5, "2", None])
    def test_invalid_trip_counts_are_refused(self, bad):
        with pytest.raises(BoundaryLedgerError):
            aggregate_split_time_loss(
                completed_prefix_total=1.0, completed_prefix_trips=bad,
                resumed_total=1.0, resumed_trips=1)


# ── The snapshot command ─────────────────────────────────────────────────────

class TestSnapshotCommand:
    """Criterion 3. v3's identity recorded RNG and 16-digit precision while its
    command applied neither, so the state was not what the key described."""

    def test_the_arguments_come_from_the_cache_constants(self):
        argv = snapshot_settings_arguments()
        assert argv == [SNAPSHOT_RNG_FLAG, "true",
                        SNAPSHOT_PRECISION_FLAG, str(STATE_PRECISION)]
        assert STATE_RNG_SAVED is True

    def test_a_valid_command_reports_the_settings_it_carries(self):
        assert validate_snapshot_command(_cmd()) == {
            "save_state_rng": True, "save_state_precision": STATE_PRECISION}

    def test_an_omitted_setting_is_refused(self):
        with pytest.raises(BoundaryLedgerError, match="exactly one"):
            validate_snapshot_command(["sumo", "-n", "net.xml"])

    def test_a_duplicated_setting_is_refused(self):
        """SUMO takes the LAST occurrence, so a duplicate silently lets whichever
        copy came last decide the state's fidelity."""
        with pytest.raises(BoundaryLedgerError, match="exactly one"):
            validate_snapshot_command(_cmd(*snapshot_settings_arguments()))

    def test_a_wrong_value_is_refused(self):
        with pytest.raises(BoundaryLedgerError, match="cache identity records"):
            validate_snapshot_command(
                ["sumo", SNAPSHOT_RNG_FLAG, "false",
                 SNAPSHOT_PRECISION_FLAG, str(STATE_PRECISION)])

    def test_a_lowered_precision_is_refused(self):
        with pytest.raises(BoundaryLedgerError, match="cache identity records"):
            validate_snapshot_command(
                ["sumo", SNAPSHOT_RNG_FLAG, "true",
                 SNAPSHOT_PRECISION_FLAG, "2"])

    def test_a_flag_without_a_value_is_refused(self):
        with pytest.raises(BoundaryLedgerError, match="no value"):
            validate_snapshot_command(
                ["sumo", SNAPSHOT_PRECISION_FLAG, str(STATE_PRECISION),
                 SNAPSHOT_RNG_FLAG])

    def test_the_wrong_transport_precision_is_refused(self):
        with pytest.raises(BoundaryLedgerError, match="exactly one"):
            validate_snapshot_command(_cmd("--precision", "6"))

    def test_completed_only_prefix_output_is_refused(self):
        cmd = _cmd()
        cmd[cmd.index("--tripinfo-output.write-unfinished") + 1] = "false"
        with pytest.raises(BoundaryLedgerError, match="must be 'true'"):
            validate_snapshot_command(cmd)

    def test_completed_only_resumed_output_is_refused(self):
        cmd = _cmd()
        cmd[cmd.index("--tripinfo-output.write-unfinished") + 1] = "false"
        with pytest.raises(BoundaryLedgerError, match="must be 'true'"):
            validate_resumed_command(
                cmd, warm_point_s=900, terminal_time_s=1800)


# ── Snapshot facts ───────────────────────────────────────────────────────────

class TestSnapshotFacts:

    def test_capture_records_the_instant_and_a_bounded_count(self):
        facts = capture_snapshot_facts(
            FakeConnection(900, ["a", "b", "c"]), warm_point_s=900)
        assert facts["captured_at_s"] == 900.0
        assert facts["active_vehicle_count"] == 3
        assert "active" not in facts, "the vehicle map must never be carried"

    def test_the_payload_does_not_grow_with_traffic(self):
        small = capture_snapshot_facts(
            FakeConnection(900, [f"v{i}" for i in range(10)]), warm_point_s=900)
        large = capture_snapshot_facts(
            FakeConnection(900, [f"v{i}" for i in range(10000)]),
            warm_point_s=900)
        assert set(small) == set(large)
        assert len(json.dumps(large)) - len(json.dumps(small)) <= 3

    def test_capture_off_the_warm_point_is_fatal(self):
        with pytest.raises(BoundaryLedgerError, match="different instant"):
            capture_snapshot_facts(FakeConnection(901, []), warm_point_s=900)

    def test_a_duplicate_active_vehicle_is_refused(self):
        with pytest.raises(BoundaryLedgerError, match="duplicate active"):
            capture_snapshot_facts(FakeConnection(900, ["a", "a"]),
                                   warm_point_s=900)

    def test_a_valid_fact_set_round_trips(self):
        assert validate_snapshot_facts(snapshot_facts())["warm_point_s"] == 900

    def test_a_legacy_schema_is_never_reinterpreted(self):
        with pytest.raises(BoundaryLedgerError, match="unsupported snapshot"):
            validate_snapshot_facts(
                snapshot_facts(schema="warm_boundary_active_ledger_v1"))

    def test_facts_from_a_different_split_are_refused(self):
        with pytest.raises(BoundaryLedgerError, match="not the expected"):
            validate_snapshot_facts(snapshot_facts(900),
                                    expected_warm_point_s=1800)

    def test_a_capture_time_that_disagrees_with_its_own_point_is_refused(self):
        with pytest.raises(BoundaryLedgerError,
                           match="not at its own warm point"):
            validate_snapshot_facts(snapshot_facts(900, captured_at_s=899.0))

    def test_facts_claiming_no_rng_are_refused(self):
        with pytest.raises(BoundaryLedgerError, match="did not save the RNG"):
            validate_snapshot_facts(snapshot_facts(save_state_rng=False))

    def test_facts_with_a_mismatched_precision_are_refused(self):
        with pytest.raises(BoundaryLedgerError, match="does not match"):
            validate_snapshot_facts(snapshot_facts(save_state_precision=2))

    def test_an_extra_field_is_refused(self):
        with pytest.raises(BoundaryLedgerError, match="field set is wrong"):
            validate_snapshot_facts(dict(snapshot_facts(), extra=1))

    def test_the_digest_is_order_stable_and_content_sensitive(self):
        assert snapshot_digest(snapshot_facts()) == \
            snapshot_digest(dict(reversed(list(snapshot_facts().items()))))
        assert snapshot_digest(snapshot_facts()) != \
            snapshot_digest(snapshot_facts(active=1))


# ── The controller ───────────────────────────────────────────────────────────

class TestControllerLifecycle:
    """Criterion 4: exact-time snapshot, bounded lifecycle, observed exit."""

    def test_the_snapshot_happens_at_the_step_the_facts_claim(self, tmp_path):
        traci = FakeTraci(active=["a"], step_to=900.0)
        controller, _ = _controller(traci)
        captured = controller.run_prefix(
            cmd=_cmd(), cwd=tmp_path, home=tmp_path, warm_point_s=900,
            state_path=tmp_path / "s.gz")
        facts = captured["facts"]
        saved_path, saved_time = traci.saved
        assert saved_path == str(tmp_path / "s.gz")
        assert saved_time == facts["captured_at_s"] == 900.0
        assert facts["save_state_precision"] == STATE_PRECISION
        assert captured["boundary_active"]["vehicle_ids"] == ["a"]

    def test_a_command_without_the_settings_is_refused_before_launch(
            self, tmp_path):
        traci = FakeTraci(step_to=900.0)
        controller, process = _controller(traci)
        with pytest.raises(BoundaryLedgerError, match="exactly one"):
            controller.run_prefix(
                cmd=["sumo", "-n", "net.xml"], cwd=tmp_path, home=tmp_path,
                warm_point_s=900, state_path=tmp_path / "s.gz")
        assert not process.waited, "nothing should have been launched"

    def test_landing_off_the_warm_point_saves_nothing(self, tmp_path):
        """A simulation that will not reach the warm point must save nothing."""
        traci = FakeTraci(step_to=899.0)
        controller, process = _controller(traci)
        with pytest.raises(BoundaryLedgerError, match="did not advance"):
            controller.run_prefix(
                cmd=_cmd(), cwd=tmp_path, home=tmp_path, warm_point_s=900,
                state_path=tmp_path / "s.gz")
        assert traci.saved is None
        assert traci.closed and process.waited, "SUMO must never be orphaned"

    def test_a_nonzero_exit_after_a_normal_completion_fails_closed(self, tmp_path):
        traci = FakeTraci(active=["a"], step_to=900.0)
        controller, _ = _controller(traci, FakeProcess(
            returncode=3, stderr_text="Error: could not write state"))
        with pytest.raises(BoundaryLedgerError, match="return code 3"):
            controller.run_prefix(
                cmd=_cmd(), cwd=tmp_path, home=tmp_path, warm_point_s=900,
                state_path=tmp_path / "s.gz")

    def test_a_sumo_that_died_before_connecting_is_reported_not_hung(
            self, tmp_path):
        traci = FakeTraci(step_to=900.0)
        controller, _ = _controller(traci, FakeProcess(
            returncode=1, poll_result=1, stderr_text="Error: unknown option"))
        with pytest.raises(BoundaryLedgerError, match="unknown option"):
            controller.run_prefix(
                cmd=_cmd(), cwd=tmp_path, home=tmp_path, warm_point_s=900,
                state_path=tmp_path / "s.gz", connect_attempts=1)

    def test_a_wait_timeout_kills_reaps_and_fails(self, tmp_path):
        traci = FakeTraci(active=["a"], step_to=900.0)
        stuck = FakeProcess(returncode=0, wait_raises=True)
        controller, _ = _controller(traci, stuck)
        with pytest.raises(BoundaryLedgerError, match="killed during cleanup"):
            controller.run_prefix(
                cmd=_cmd(), cwd=tmp_path, home=tmp_path, warm_point_s=900,
                state_path=tmp_path / "s.gz")
        assert stuck.killed and stuck.waited

    def test_a_body_failure_is_not_masked_by_the_exit_code_check(self, tmp_path):
        """The primary error must survive cleanup, not be replaced by a
        complaint about a process we already knew had failed."""
        traci = FakeTraci(step_to=899.0)
        controller, process = _controller(traci, FakeProcess(returncode=9))
        with pytest.raises(BoundaryLedgerError, match="did not advance"):
            controller.run_prefix(
                cmd=_cmd(), cwd=tmp_path, home=tmp_path, warm_point_s=900,
                state_path=tmp_path / "s.gz")
        assert process.waited

    def test_the_step_loop_is_chunked_not_one_blocking_jump(self, tmp_path):
        """Sol review: advancing in one blocking call cannot be interrupted, so
        a deadline tested only afterwards bounds nothing. The budget is enforced
        BETWEEN bounded chunks instead."""
        traci = FakeTraci(active=["a"], step_to=900.0)
        controller, _ = _controller(traci)
        controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                              warm_point_s=900, state_path=tmp_path / "s.gz",
                              step_chunk_s=100.0)
        assert len(traci.step_targets) == 9, traci.step_targets
        assert traci.step_targets[-1] == 900.0, "snapshot must land exactly"
        assert all(t <= 900.0 for t in traci.step_targets)

    def test_an_exhausted_budget_before_connect_never_connects(self, tmp_path):
        """Checked BEFORE the blocking call: a deadline tested afterwards has
        already let the call hang."""
        traci = FakeTraci(active=["a"], step_to=900.0)
        controller, _ = _controller(traci, clock=_clock([0.0, 10_000.0]))
        with pytest.raises(BoundaryLedgerError, match="before TraCI could be"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert traci.port is None, "init must not have been called"

    def test_an_exhausted_budget_mid_simulation_stops_short_and_saves_nothing(
            self, tmp_path):
        """The budget is enforced between chunks, so a slow run is caught while
        it is still running rather than after it has already overrun."""
        traci = FakeTraci(active=["a"], step_to=900.0)
        # Time advances only once stepping has begun, so the run is caught while
        # it is still short of the warm point.
        controller, _ = _controller(
            traci, clock=_clock([0.0, 1.0, 2.0, 10_000.0]))
        with pytest.raises(BoundaryLedgerError, match="exceeded its time budget"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz",
                                  step_chunk_s=100.0)
        assert traci.saved is None

    def test_a_simulation_that_never_advances_is_refused(self, tmp_path):
        """Otherwise the chunk loop would spin until the budget expired."""
        traci = FakeTraci(active=["a"], step_to=900.0, steps_stall=True)
        controller, _ = _controller(traci)
        with pytest.raises(BoundaryLedgerError, match="did not advance"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert traci.saved is None

    def test_a_call_that_has_already_blocked_is_interrupted_by_the_kill(
            self, tmp_path):
        """Sol review: a deadline checked around a call cannot interrupt it once
        it has blocked. The only thing we own that can release it is the SUMO
        PROCESS, so the watchdog kills it.

        The fake blocks inside `init` on a real Event that only `kill()` sets —
        which is exactly how a real blocked socket call unwinds when the peer
        dies. No subprocess, no socket, no simulator.
        """
        import threading

        released = threading.Event()
        entered = threading.Event()

        class BlockingTraci(FakeTraci):
            def init(self, port):
                entered.set()
                # Blocks until the process is killed, like a real pending call.
                released.wait(timeout=10)
                raise RuntimeError("connection closed by peer")

        class KillReleasesProcess(FakeProcess):
            def kill(self):
                super().kill()
                released.set()

        traci = BlockingTraci(active=["a"], step_to=900.0)
        process = KillReleasesProcess(returncode=-9)
        controller, _ = _controller(traci, process, run_budget_s=0.05)
        with pytest.raises(BoundaryLedgerError, match="blocking TraCI call"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert entered.is_set(), "the phase never reached the blocking call"
        assert process.killed, "the blocked call was never released"
        assert process.waited, "the process was never reaped"
        assert traci.saved is None, "a timed-out run must save no state"

    def test_a_block_in_the_middle_of_stepping_is_also_interrupted(self, tmp_path):
        """The bound covers the WHOLE phase, not just the connect."""
        import threading

        released = threading.Event()

        class BlockingStep(FakeTraci):
            def simulationStep(self, target):
                released.wait(timeout=10)
                raise RuntimeError("connection closed by peer")

        class KillReleasesProcess(FakeProcess):
            def kill(self):
                super().kill()
                released.set()

        traci = BlockingStep(active=["a"], step_to=900.0)
        process = KillReleasesProcess(returncode=-9)
        controller, _ = _controller(traci, process, run_budget_s=0.05)
        with pytest.raises(BoundaryLedgerError, match="blocking TraCI call"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert process.killed and process.waited
        assert traci.saved is None

    def test_a_block_during_save_state_is_interrupted(self, tmp_path):
        """Including the snapshot itself: `saveState` can block too."""
        import threading

        released = threading.Event()

        class BlockingSave(FakeTraci):
            def __init__(self, **kw):
                super().__init__(**kw)
                outer = self

                class _Sim:
                    @staticmethod
                    def getTime():
                        return outer._now

                    @staticmethod
                    def saveState(path):
                        released.wait(timeout=10)
                        raise RuntimeError("connection closed by peer")

                self.simulation = _Sim()

        class KillReleasesProcess(FakeProcess):
            def kill(self):
                super().kill()
                released.set()

        traci = BlockingSave(active=["a"], step_to=900.0)
        process = KillReleasesProcess(returncode=-9)
        controller, _ = _controller(traci, process, run_budget_s=0.05)
        with pytest.raises(BoundaryLedgerError, match="blocking TraCI call"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert process.killed and process.waited

    def test_a_worker_that_never_unwinds_is_reported_not_waited_on(
            self, tmp_path):
        """Killing the process usually releases the call. If it does not, the
        grace period is bounded and the run still fails rather than hanging."""
        import threading

        never = threading.Event()

        class NeverReleases(FakeTraci):
            def init(self, port):
                never.wait(timeout=10)

        traci = NeverReleases(active=["a"], step_to=900.0)
        controller, process = _controller(
            traci, FakeProcess(returncode=-9), run_budget_s=0.05)
        with pytest.raises(BoundaryLedgerError, match="did not unwind"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz",
                                  kill_grace_s=0.05)
        assert process.killed and process.waited
        never.set()                                    # let the worker go

    def test_a_timeout_is_reported_even_though_the_phase_also_raised(
            self, tmp_path):
        """The kill makes the blocked call raise, so BOTH exist. The timeout is
        the honest cause, and the induced connection error must not stand in for
        it."""
        import threading

        released = threading.Event()

        class BlockingTraci(FakeTraci):
            def init(self, port):
                released.wait(timeout=10)
                raise RuntimeError("connection closed by peer")

        class KillReleasesProcess(FakeProcess):
            def kill(self):
                super().kill()
                released.set()

        controller, process = _controller(
            BlockingTraci(active=["a"], step_to=900.0),
            KillReleasesProcess(returncode=-9), run_budget_s=0.05)
        with pytest.raises(BoundaryLedgerError) as caught:
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert "blocking TraCI call" in str(caught.value)
        assert "closed by peer" not in str(caught.value)

    def test_a_blocked_close_is_inside_the_bound_and_released_by_the_kill(
            self, tmp_path):
        """Sol review: `close()` is itself a TraCI call. Leaving it in the main
        thread's `finally` put one blocking call outside every timeout."""
        import threading

        released = threading.Event()
        entered = threading.Event()

        class BlockingClose(FakeTraci):
            def close(self):
                entered.set()
                released.wait(timeout=10)
                self.closed = True
                raise RuntimeError("connection closed by peer")

        class KillReleasesProcess(FakeProcess):
            def kill(self):
                super().kill()
                released.set()

        traci = BlockingClose(active=["a"], step_to=900.0)
        process = KillReleasesProcess(returncode=-9)
        controller, _ = _controller(traci, process, run_budget_s=0.05)
        with pytest.raises(BoundaryLedgerError, match="blocking TraCI call"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert entered.is_set(), "close was never reached"
        assert process.killed, "the blocked close was never released"
        assert process.waited, "the process was never reaped"

    def test_close_runs_on_the_worker_and_never_on_the_main_thread(
            self, tmp_path):
        """One thread owns the connection. A concurrent main-thread close would
        race a worker that may still be using it."""
        import threading

        calls = []

        class RecordingClose(FakeTraci):
            def close(self):
                calls.append(threading.current_thread().name)
                self.closed = True

        traci = RecordingClose(active=["a"], step_to=900.0)
        controller, _ = _controller(traci)
        controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                              warm_point_s=900, state_path=tmp_path / "s.gz")
        assert len(calls) == 1, f"close must happen exactly once, got {calls}"
        assert calls[0] != threading.current_thread().name, (
            "close ran on the main thread; it belongs to the bounded worker")

    def test_a_close_failure_never_masks_the_primary_error(self, tmp_path):
        """The body's real failure must survive a close that also fails."""
        class ExplodingClose(FakeTraci):
            def close(self):
                raise RuntimeError("close exploded")

        traci = ExplodingClose(active=["a"], step_to=899.0)   # body stalls short
        controller, process = _controller(traci)
        with pytest.raises(BoundaryLedgerError, match="did not advance"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert process.waited, "the process must still be reaped"

    def test_a_close_failure_alone_does_not_fail_a_good_run(self, tmp_path):
        """A connection that dies while being closed says nothing about the
        snapshot, which was already written and verified."""
        class ExplodingClose(FakeTraci):
            def close(self):
                raise RuntimeError("close exploded")

        traci = ExplodingClose(active=["a"], step_to=900.0)
        controller, _ = _controller(traci)
        captured = controller.run_prefix(
            cmd=_cmd(), cwd=tmp_path, home=tmp_path, warm_point_s=900,
            state_path=tmp_path / "s.gz")
        facts = captured["facts"]
        assert facts["captured_at_s"] == 900.0
        assert traci.saved is not None

    def test_a_worker_stuck_in_close_is_reported_not_waited_on(self, tmp_path):
        """If the kill does not release close either, the grace is still bounded
        and the main thread never touches the connection."""
        import threading

        never = threading.Event()

        class NeverCloses(FakeTraci):
            def close(self):
                never.wait(timeout=10)

        traci = NeverCloses(active=["a"], step_to=900.0)
        controller, process = _controller(
            traci, FakeProcess(returncode=-9), run_budget_s=0.05)
        with pytest.raises(BoundaryLedgerError, match="did not unwind"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz",
                                  kill_grace_s=0.05)
        assert process.killed and process.waited
        never.set()                                    # let the worker go

    def test_the_post_kill_wait_is_bounded(self, tmp_path):
        """Sol review: an unbounded second wait after kill() turns cleanup into
        the very hang the timeout exists to prevent."""
        recorded = []

        class StubbornProcess(FakeProcess):
            def wait(self, timeout=None):
                recorded.append(timeout)
                self.waited = True
                raise RuntimeError("wait timed out")

        traci = FakeTraci(active=["a"], step_to=900.0)
        stubborn = StubbornProcess()
        controller, _ = _controller(traci, stubborn)
        with pytest.raises(BoundaryLedgerError, match="could not be cleaned up"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert stubborn.killed
        assert len(recorded) == 2, "both waits must run"
        assert all(t is not None for t in recorded), (
            f"every wait must be bounded, got timeouts {recorded}")

    def test_a_cleanup_failure_never_masks_the_primary_error(self, tmp_path):
        """The body's real failure must survive cleanup. Reporting a cleanup
        complaint instead would hide why the run actually failed."""
        class ExplodingProcess(FakeProcess):
            def wait(self, timeout=None):
                self.waited = True
                raise RuntimeError("wait exploded")

            def kill(self):
                raise RuntimeError("kill exploded")

        traci = FakeTraci(active=["a"], step_to=899.0)   # body fails: stalls short
        controller, _ = _controller(traci, ExplodingProcess())
        with pytest.raises(BoundaryLedgerError, match="did not advance"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")

    def test_an_unreadable_stderr_log_does_not_replace_the_real_error(
            self, tmp_path):
        traci = FakeTraci(active=["a"], step_to=900.0)
        controller, _ = _controller(traci, FakeProcess(returncode=4))

        def unreadable(path, limit=1500):
            raise OSError("log is gone")

        controller._stderr_tail = unreadable
        with pytest.raises(BoundaryLedgerError, match="return code 4"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=tmp_path,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")

    def test_stderr_is_captured_from_a_file_not_a_pipe(self):
        """An unread pipe deadlocks a process that outfills the buffer."""
        source = MODULE.read_text()
        assert "stderr=handle" in source
        assert "stderr=subprocess.PIPE" not in source


# ── Diagnostics kept for humans, not for the objective ──────────────────────

class TestTripinfoParsingIsDiagnosticOnly:

    def test_records_are_keyed_by_vehicle_identity(self, tmp_path):
        path = tmp_path / "t.xml"
        path.write_text('<tripinfos>\n<tripinfo id="v1" timeLoss="1.50"/>\n'
                        '<tripinfo id="v2" timeLoss="2.25"/>\n</tripinfos>\n')
        assert parse_tripinfo_time_loss(path) == {"v1": 1.5, "v2": 2.25}

    def test_the_container_tag_is_not_read_as_a_record(self, tmp_path):
        path = tmp_path / "t.xml"
        path.write_text('<tripinfos>\n<tripinfo id="v1" timeLoss="1.00"/>\n'
                        '</tripinfos>\n')
        assert parse_tripinfo_time_loss(path) == {"v1": 1.0}

    def test_a_duplicate_identity_is_refused(self, tmp_path):
        path = tmp_path / "t.xml"
        path.write_text('<tripinfos>\n<tripinfo id="v" timeLoss="1.0"/>\n'
                        '<tripinfo id="v" timeLoss="2.0"/>\n</tripinfos>\n')
        with pytest.raises(BoundaryLedgerError, match="duplicate tripinfo"):
            parse_tripinfo_time_loss(path)

    def test_a_malformed_record_is_fatal(self, tmp_path):
        path = tmp_path / "t.xml"
        path.write_text('<tripinfos>\n<tripinfo id="v"/>\n</tripinfos>\n')
        with pytest.raises(BoundaryLedgerError, match="lacks id/timeLoss"):
            parse_tripinfo_time_loss(path)

    def test_production_uses_the_v13_reconciler(self):
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        assert "reconcile_resumed_tripinfo" in source
        assert "apply_restore_deficits" not in source


class TestMesoAccumulatorReconstruction:

    @staticmethod
    def _xml(path, records):
        body = "".join(
            f'<tripinfo id="{identifier}" depart="{depart}" arrival="{arrival}" '
            f'timeLoss="{loss}"/>'
            for identifier, depart, arrival, loss in records)
        path.write_text(f"<tripinfos>{body}</tripinfos>", encoding="utf-8")

    def test_prefix_partitions_completed_and_active_exactly(self, tmp_path):
        active = capture_boundary_active(
            FakeConnection(900, ["active"]), warm_point_s=900)
        path = tmp_path / "prefix.xml"
        self._xml(path, [("done", 100, 800, 1.006),
                         ("active", 200, -1, 2.004)])
        result = build_prefix_accumulator(path, active)
        assert result["completed_records"] == {"done": 1.01}
        assert result["completed_trips"] == {
            "total_time_loss_s": 1.01, "trip_count": 1}
        assert result["active_accumulator"]["entries"] == {"active": 2.004}

    def test_active_output_must_still_be_unfinished_without_a_step(self,
                                                                   tmp_path):
        active = capture_boundary_active(
            FakeConnection(900, ["active"]), warm_point_s=900)
        path = tmp_path / "prefix.xml"
        self._xml(path, [("active", 200, 900, 2.0)])
        with pytest.raises(BoundaryLedgerError,
                           match="finalized without a step"):
            build_prefix_accumulator(path, active)

    def test_missing_or_extra_active_record_is_refused(self, tmp_path):
        active = capture_boundary_active(
            FakeConnection(900, ["active"]), warm_point_s=900)
        path = tmp_path / "prefix.xml"
        self._xml(path, [("other", 200, -1, 0.0)])
        with pytest.raises(BoundaryLedgerError, match="missing=.*active"):
            build_prefix_accumulator(path, active)

    def test_whole_vehicle_is_rounded_once_after_join(self, tmp_path):
        active = capture_boundary_active(
            FakeConnection(900, ["active"]), warm_point_s=900)
        prefix = tmp_path / "prefix.xml"
        self._xml(prefix, [("done", 100, 800, 1.006),
                           ("active", 200, -1, 1.005)])
        partition = build_prefix_accumulator(prefix, active)
        resumed = tmp_path / "resumed.xml"
        self._xml(resumed, [("active", 200, 1200, 1.005),
                            ("future", 1000, 1300, 0.004)])
        result = reconcile_resumed_tripinfo(
            resumed, partition["active_accumulator"],
            completed_vehicle_ids=sorted(partition["completed_records"]),
            completed_prefix_total=partition["completed_trips"][
                "total_time_loss_s"])
        assert result["records"] == {"active": 2.01, "future": 0.0}
        assert result["corrected_total_time_loss_s"] == 2.01

    def test_reconciliation_uses_sumo_half_up_for_active_and_future_ties(
            self, tmp_path):
        active = capture_boundary_active(
            FakeConnection(900, ["active"]), warm_point_s=900)
        prefix = tmp_path / "prefix.xml"
        self._xml(prefix, [("active", 200, -1, 0.005)])
        partition = build_prefix_accumulator(prefix, active)
        resumed = tmp_path / "resumed.xml"
        self._xml(resumed, [("active", 200, 1200, 1.0),
                            ("future", 1000, 1300, 36.555)])
        result = reconcile_resumed_tripinfo(
            resumed, partition["active_accumulator"],
            completed_prefix_total=0.0)
        assert result["records"] == {"active": 1.01, "future": 36.56}
        assert result["corrected_total_time_loss_s"] == 37.57

    def test_completed_resumed_overlap_and_missing_active_fail(self, tmp_path):
        active = capture_boundary_active(
            FakeConnection(900, ["active"]), warm_point_s=900)
        prefix = tmp_path / "prefix.xml"
        self._xml(prefix, [("done", 100, 800, 1.0),
                           ("active", 200, -1, 2.0)])
        partition = build_prefix_accumulator(prefix, active)
        overlap = tmp_path / "overlap.xml"
        self._xml(overlap, [("done", 100, 1000, 1.0),
                            ("active", 200, 1100, 2.0)])
        with pytest.raises(BoundaryLedgerError, match="both completed"):
            reconcile_resumed_tripinfo(
                overlap, partition["active_accumulator"],
                completed_vehicle_ids=["done"], completed_prefix_total=1.0)
        missing = tmp_path / "missing.xml"
        self._xml(missing, [("future", 1000, 1100, 1.0)])
        with pytest.raises(BoundaryLedgerError, match="missing boundary"):
            reconcile_resumed_tripinfo(
                missing, partition["active_accumulator"],
                completed_vehicle_ids=["done"], completed_prefix_total=1.0)

    def test_unknown_pre_boundary_and_late_active_records_fail(self, tmp_path):
        active = capture_boundary_active(
            FakeConnection(900, ["active"]), warm_point_s=900)
        prefix = tmp_path / "prefix.xml"
        self._xml(prefix, [("active", 200, -1, 2.0)])
        ledger = build_prefix_accumulator(prefix, active)["active_accumulator"]

        unknown = tmp_path / "unknown.xml"
        self._xml(unknown, [("active", 200, 1100, 1.0),
                            ("missed", 300, 1200, 1.0)])
        with pytest.raises(BoundaryLedgerError, match="unknown pre-boundary"):
            reconcile_resumed_tripinfo(
                unknown, ledger, completed_prefix_total=0.0)

        late = tmp_path / "late.xml"
        self._xml(late, [("active", 901, 1100, 1.0)])
        with pytest.raises(BoundaryLedgerError, match="depart after"):
            reconcile_resumed_tripinfo(late, ledger, completed_prefix_total=0.0)

    def test_active_population_digest_is_recomputed_not_merely_well_formed(
            self, tmp_path):
        active = capture_boundary_active(
            FakeConnection(900, ["active"]), warm_point_s=900)
        prefix = tmp_path / "prefix.xml"
        self._xml(prefix, [("active", 200, -1, 2.0)])
        ledger = build_prefix_accumulator(prefix, active)["active_accumulator"]
        ledger["active_population_digest"] = "a" * 64
        body = {key: value for key, value in ledger.items() if key != "digest"}
        ledger["digest"] = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False).encode()).hexdigest()
        with pytest.raises(BoundaryLedgerError,
                           match="does not match accumulator identities"):
            validate_prefix_accumulator(ledger)

    def test_prefix_xml_order_survives_canonical_record_storage(self, tmp_path):
        active = capture_boundary_active(
            FakeConnection(900, []), warm_point_s=900)
        prefix = tmp_path / "prefix.xml"
        self._xml(prefix, [("c", 0, 1, 0.01), ("b", 0, 2, 0.01),
                           ("a", 0, 3, 0.07)])
        partition = build_prefix_accumulator(prefix, active)
        assert partition["completed_record_order"] == ["c", "b", "a"]
        assert partition["completed_trips"]["total_time_loss_s"] == \
            0.09000000000000001

    def test_resumed_sum_continues_the_prefix_accumulator_state(self, tmp_path):
        active = capture_boundary_active(
            FakeConnection(900, []), warm_point_s=900)
        prefix = tmp_path / "prefix.xml"
        self._xml(prefix, [("prefix", 0, 1, 0.01)])
        partition = build_prefix_accumulator(prefix, active)
        resumed = tmp_path / "resumed.xml"
        self._xml(resumed, [("post-a", 901, 902, 0.01),
                            ("post-b", 901, 903, 0.07)])
        correction = reconcile_resumed_tripinfo(
            resumed, partition["active_accumulator"],
            completed_vehicle_ids=["prefix"],
            completed_prefix_total=partition["completed_trips"][
                "total_time_loss_s"])
        assert correction["corrected_total_time_loss_s"] == 0.08
        assert correction["combined_total_time_loss_s"] == \
            0.09000000000000001

    def test_native_millisecond_grid_does_not_drift_across_96_links(
            self, tmp_path):
        """Every chained XML handoff preserves SUMOTime's native grid.

        This is the production chain depth.  Keeping the internal 16-digit
        float representation at every handoff accumulated binary noise and
        eventually changed a final two-decimal half-up result by one cent.
        """
        first_point = 900
        active = capture_boundary_active(
            FakeConnection(first_point, ["active"]),
            warm_point_s=first_point)
        prefix = tmp_path / "prefix.xml"
        self._xml(prefix, [("active", 0, -1, 0.001)])
        ledger = build_prefix_accumulator(prefix, active)[
            "active_accumulator"]

        for link in range(2, 97):
            resumed = tmp_path / f"resumed-{link}.xml"
            self._xml(resumed, [("active", 0, -1, 0.001)])
            correction = reconcile_resumed_tripinfo(
                resumed, ledger, completed_prefix_total=0.0,
                precision=MESO_TIMELOSS_PRECISION)
            expected = normalize_time_loss(
                link * 0.001, MESO_TIMELOSS_PRECISION)
            assert correction["records"] == {"active": expected}

            point = link * 900
            corrected = tmp_path / f"corrected-{link}.xml"
            self._xml(corrected, [("active", 0, -1, expected)])
            boundary = capture_boundary_active(
                FakeConnection(point, ["active"]), warm_point_s=point)
            ledger = build_prefix_accumulator(corrected, boundary)[
                "active_accumulator"]

        assert ledger["entries"] == {"active": 0.096}
        terminal = tmp_path / "terminal.xml"
        self._xml(terminal, [("active", 0, 86_401, 0.004)])
        final = reconcile_resumed_tripinfo(
            terminal, ledger, completed_prefix_total=0.0)
        assert final["records"] == {"active": 0.1}

    def test_production_consumes_the_continued_total_not_grouped_segments(self):
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        assert 'whole_run_continued_total = correction_summary[' in source
        assert 'continued_total=whole_run_continued_total' in source
        assert 'resumed_corrected_total = correction_summary[' in source


class TestNumberGuards:

    @pytest.mark.parametrize("value", [True, "1.0", None, float("nan"),
                                       float("inf"), -0.5])
    def test_invalid_values_are_refused(self, value):
        with pytest.raises(BoundaryLedgerError):
            check_time_loss(value)

    def test_a_valid_value_is_rounded_not_truncated(self):
        assert normalize_time_loss(1.006) == 1.01

    @pytest.mark.parametrize("raw,expected", [
        (36.555, 36.56), (151.545, 151.55), (1.005, 1.01),
        (2.675, 2.68), (0.004, 0.0),
    ])
    def test_sumo_decimal_half_ties_round_up(self, raw, expected):
        assert normalize_time_loss(raw) == expected


# ── Process-free guards ──────────────────────────────────────────────────────

def _code_only(path):
    """Executable source with comments and docstrings stripped."""
    text = Path(path).read_text()
    tree = ast.parse(text)
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and \
                isinstance(body[0].value.value, str):
            docstrings.add((body[0].value.lineno, body[0].value.col_offset))
    kept = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and token.start in docstrings:
            continue
        kept.append(token.string)
    return "\n".join(kept)


class TestProcessFree:

    def test_no_simulator_is_imported_at_module_scope(self):
        """The controller imports traci, subprocess and socket LAZILY inside its
        methods — that is the design: constructing one starts nothing."""
        tree = ast.parse(MODULE.read_text())
        modules = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                modules |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])
        assert not modules & {"traci", "libsumo", "socket", "subprocess",
                              "multiprocessing", "http", "urllib"}, \
            sorted(modules)

    def test_constructing_the_controller_imports_no_simulator(self):
        import sys
        WarmPrefixController()
        assert "traci" not in sys.modules and "libsumo" not in sys.modules

    def test_this_suite_imports_no_real_simulator(self):
        import sys
        assert "traci" not in sys.modules and "libsumo" not in sys.modules

    @pytest.mark.parametrize("path", [
        "traffic_sim/simulation/warm_state_boundary.py",
        "tests/test_warm_state_boundary.py",
    ])
    def test_no_forbidden_location_is_named_in_code(self, path):
        code = _code_only(path)
        for needle in ["run" + "s/", "sumo" + "-gui", "check_" + "output"]:
            assert needle not in code, (path, needle)


class TestTraciResolutionInTheController:
    """LUNA-WARM-15 criterion 2.

    v6's controller did a bare `import traci` and failed with
    `No module named 'traci'` AFTER launching SUMO three times. Resolution now
    happens through the shared production resolver, against the exact home
    `run_prefix` was given, and BEFORE anything is launched.
    """

    def _fake_home(self, tmp_path, *, package=True):
        home = tmp_path / "sumo"
        (home / "tools").mkdir(parents=True)
        if package:
            pkg = home / "tools" / "traci"
            pkg.mkdir()
            pkg.joinpath("__init__.py").write_text(
                "def init(port=None): pass\n"
                "def close(): pass\n"
                "def simulationStep(t=None): pass\n")
        return home

    def test_an_unresolvable_traci_fails_before_any_process(self, tmp_path):
        """The exact v6 shape, now caught for free: no launcher, no port, no
        SUMO — an environment that cannot warm costs nothing."""
        launched = []

        def launcher(cmd, *, cwd, home, port, stderr_path):
            launched.append(cmd)
            raise AssertionError("nothing may be launched")

        controller = WarmPrefixController(launcher=launcher, port=12345)
        home = self._fake_home(tmp_path, package=False)
        with pytest.raises(Exception, match="TraCI is not present"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=home,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert launched == [], "a process was started despite unusable TraCI"

    def test_resolution_uses_the_home_passed_to_run_prefix(self, tmp_path,
                                                           monkeypatch):
        """Not a global, not an ambient default: the home this call names."""
        import traffic_sim.simulation.runtime as runtime
        seen = []

        def fake_resolve(home, **kwargs):
            seen.append(Path(home))
            raise runtime.SumoRuntimeError("stop here")

        monkeypatch.setattr(runtime, "resolve_traci", fake_resolve)
        home = self._fake_home(tmp_path)
        controller = WarmPrefixController(
            launcher=lambda *a, **k: pytest.fail("must not launch"), port=1)
        with pytest.raises(Exception, match="stop here"):
            controller.run_prefix(cmd=_cmd(), cwd=tmp_path, home=home,
                                  warm_point_s=900,
                                  state_path=tmp_path / "s.gz")
        assert seen == [home]

    def test_the_injected_module_seam_still_bypasses_resolution(self, tmp_path,
                                                                monkeypatch):
        """Process-free tests must keep working without any SUMO on disk."""
        import traffic_sim.simulation.runtime as runtime
        monkeypatch.setattr(runtime, "resolve_traci", lambda *a, **k:
                            pytest.fail("an injected module must not resolve"))
        traci = FakeTraci(active=["a"], step_to=900.0)
        controller, _ = _controller(traci)
        captured = controller.run_prefix(
            cmd=_cmd(), cwd=tmp_path, home=tmp_path / "irrelevant",
            warm_point_s=900, state_path=tmp_path / "s.gz")
        assert captured["facts"]["captured_at_s"] == 900.0
        assert captured["boundary_active"]["warm_point_s"] == 900
        assert captured["boundary_active"]["vehicle_ids"] == ["a"]

    def test_production_contains_no_bare_traci_import(self):
        """v6's defect, asserted gone at the source level."""
        import ast
        source = MODULE.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert not any(a.name == "traci" for a in node.names)
        assert "resolve_traci" in source


class _FakeConn:
    """A TraCI stand-in: no socket, no process, no simulator import."""

    def __init__(self, time_s, entries):
        self._time, self._entries = time_s, dict(entries)
        self.simulation = self
        self.vehicle = self

    def getTime(self):
        return self._time

    def getIDList(self):
        return list(self._entries)

    def getTimeLoss(self, vehicle):
        return self._entries[vehicle]


def _ledger(entries, *, time_s=24300.0, warm=24300):
    from traffic_sim.simulation.warm_state_boundary import capture_save_ledger
    return capture_save_ledger(_FakeConn(time_s, entries), warm_point_s=warm)


# The exact LUNA-WARM-22 measurement, as fixtures. Each identity's affected
# vehicles and their deltas are the ones the campaign actually produced, so the
# arithmetic here reproduces the campaign residual rather than a stand-in.
WARM22 = {
    "q10": {"saved": {"pfe1048": 7.85, "pfe1041": 3.22, "pfe1037": 1.83,
                      "pfe1045": 13.52, "pfe1027": 0.09},
            "restored": {"pfe1048": 4.85, "pfe1041": 1.02, "pfe1037": 0.46,
                         "pfe1045": 12.45, "pfe1027": 0.0},
            "in_flight": 44, "affected": 5, "residual": 7.73},
    "q50": {"saved": {"pfe1098": 62.54, "pfe1090": 7.0, "pfe1130": 5.45,
                      "pfe1146": 4.61},
            "restored": {"pfe1098": 7.47, "pfe1090": 0.0, "pfe1130": 0.0,
                         "pfe1146": 0.0},
            "in_flight": 50, "affected": 4, "residual": 80.62},
    "q90": {"saved": {"pfe1206": 41.1, "pfe1208": 22.34, "pfe1230": 19.0,
                      "pfe1220": 17.0},
            "restored": {"pfe1206": 0.0, "pfe1208": 2.46, "pfe1230": 0.0,
                         "pfe1220": 0.0},
            "in_flight": 51, "affected": 4, "residual": 138.97},
}


class TestSelectiveRestoreCorrection:
    """LUNA-WARM-23: correct only what was MEASURED to be lost.

    LUNA-WARM-22 established that most active accumulators survive the save/load
    boundary and a measured minority lose some or all of theirs. Neither a
    blanket offset nor a no-offset rule is sound, so nothing here is inferred.
    """

    def _audit(self, saved, restored, *, filler=0):
        from traffic_sim.simulation.warm_state_boundary import build_restore_audit
        base = {f"keep{i}": 1.5 for i in range(filler)}
        return build_restore_audit(_ledger({**base, **saved}),
                                   _ledger({**base, **restored}),
                                   expected_warm_point_s=24300)

    def test_the_q10_pattern_reproduces_its_measured_residual(self):
        case = WARM22["q10"]
        audit = self._audit(case["saved"], case["restored"],
                            filler=case["in_flight"] - case["affected"])
        assert audit["affected_count"] == case["affected"]
        assert audit["preserved_count"] == case["in_flight"] - case["affected"]
        assert audit["deficit_total_s"] == case["residual"]

    def test_a_fully_lost_accumulator_is_corrected_in_full(self):
        """Most affected vehicles lose EVERYTHING, not a fraction."""
        audit = self._audit({"v": 41.1}, {"v": 0.0})
        assert audit["deficits"]["v"] == 41.1

    def test_a_partially_lost_accumulator_is_corrected_by_the_difference(self):
        audit = self._audit({"v": 62.54}, {"v": 7.47})
        assert round(audit["deficits"]["v"], 2) == 55.07

    def test_preserved_vehicles_get_no_deficit_at_all(self):
        """The whole point of selective: an untouched accumulator stays
        untouched. A blanket offset would double count these."""
        audit = self._audit({"a": 5.0, "b": 9.0}, {"a": 5.0, "b": 9.0})
        assert audit["deficits"] == {}
        assert audit["affected_count"] == 0
        assert audit["preserved_count"] == 2

    def test_a_mixed_population_corrects_only_the_losers(self):
        audit = self._audit({"lost": 4.0, "part": 6.0, "kept": 8.0},
                            {"lost": 0.0, "part": 2.5, "kept": 8.0})
        assert sorted(audit["deficits"]) == ["lost", "part"]
        assert audit["preserved_count"] == 1
        assert audit["deficit_total_s"] == 7.5

    def test_a_restored_increase_is_refused_not_clamped(self):
        """The boundary cannot create delay; hiding that would be worse than
        failing."""
        import pytest
        from traffic_sim.simulation.warm_state_boundary import BoundaryLedgerError
        with pytest.raises(BoundaryLedgerError, match="MORE accumulated"):
            self._audit({"v": 1.0}, {"v": 2.0})

    def test_missing_or_extra_identity_coverage_is_refused(self):
        import pytest
        from traffic_sim.simulation.warm_state_boundary import BoundaryLedgerError
        with pytest.raises(BoundaryLedgerError, match="same vehicles"):
            self._audit({"a": 1.0, "b": 2.0}, {"a": 1.0})
        with pytest.raises(BoundaryLedgerError, match="same vehicles"):
            self._audit({"a": 1.0}, {"a": 1.0, "b": 2.0})

    def test_a_wrong_instant_ledger_is_refused(self):
        import pytest
        from traffic_sim.simulation.warm_state_boundary import (
            BoundaryLedgerError, capture_save_ledger)
        with pytest.raises(BoundaryLedgerError, match="not the warm point"):
            capture_save_ledger(_FakeConn(24299.0, {"v": 1.0}),
                                warm_point_s=24300)

    def test_a_tampered_ledger_is_refused(self):
        import pytest
        from traffic_sim.simulation.warm_state_boundary import (
            BoundaryLedgerError, validate_save_ledger)
        ledger = _ledger({"v": 1.0})
        ledger["entries"]["v"] = 99.0
        with pytest.raises(BoundaryLedgerError, match="digest does not recompute"):
            validate_save_ledger(ledger)

    def test_a_legacy_schema_is_never_interpreted(self):
        import pytest
        from traffic_sim.simulation.warm_state_boundary import (
            BoundaryLedgerError, validate_save_ledger)
        ledger = dict(_ledger({"v": 1.0}), schema="warm_save_ledger_v0")
        with pytest.raises(BoundaryLedgerError, match="unsupported ledger schema"):
            validate_save_ledger(ledger)

    def test_a_non_finite_or_negative_value_is_refused(self):
        import pytest
        from traffic_sim.simulation.warm_state_boundary import BoundaryLedgerError
        with pytest.raises(BoundaryLedgerError, match="non-finite"):
            _ledger({"v": float("nan")})
        with pytest.raises(BoundaryLedgerError, match="negative"):
            _ledger({"v": -1.0})

    def test_a_tampered_audit_is_refused(self):
        import pytest
        from traffic_sim.simulation.warm_state_boundary import (
            BoundaryLedgerError, validate_restore_audit)
        audit = self._audit({"v": 5.0}, {"v": 1.0})
        audit["deficits"]["v"] = 99.0
        with pytest.raises(BoundaryLedgerError, match="does not recompute"):
            validate_restore_audit(audit)


class TestDeficitApplication:
    """Criterion 4: applied ONCE, to the matching whole record, at production
    precision, leaving preserved vehicles alone."""

    def _audit(self, saved, restored):
        from traffic_sim.simulation.warm_state_boundary import build_restore_audit
        return build_restore_audit(_ledger(saved), _ledger(restored),
                                   expected_warm_point_s=24300)

    def _final(self, audit, entries):
        from traffic_sim.simulation.warm_state_boundary import capture_final_ledger
        return capture_final_ledger(
            _FakeConn(90000.0, entries), audit["deficits"],
            warm_point_s=24300, terminal_time_s=90000,
            audit_digest=audit["digest"])

    def test_each_deficit_is_applied_exactly_once(self):
        from traffic_sim.simulation.warm_state_boundary import (
            apply_restore_deficits)
        audit = self._audit({"v": 5.0, "w": 2.0}, {"v": 1.0, "w": 2.0})
        out = apply_restore_deficits(
            {"v": 10.0, "w": 20.0}, self._final(audit, {"v": 10.0}), audit)
        assert out["records"]["v"] == 14.0          # 10 + (5 - 1), once
        assert out["applied_count"] == 1
        assert out["deficit_total_s"] == 4.0

    def test_a_preserved_vehicle_is_returned_unchanged(self):
        from traffic_sim.simulation.warm_state_boundary import (
            apply_restore_deficits)
        audit = self._audit({"v": 5.0, "w": 2.0}, {"v": 1.0, "w": 2.0})
        out = apply_restore_deficits(
            {"v": 10.0, "w": 20.0}, self._final(audit, {"v": 10.0}), audit)
        assert out["records"]["w"] == 20.0
        assert out["unchanged_count"] == 1

    def test_a_deficit_without_a_final_record_is_refused(self):
        """Dropping it silently would reintroduce the very loss being repaired."""
        import pytest
        from traffic_sim.simulation.warm_state_boundary import (
            BoundaryLedgerError, apply_restore_deficits)
        audit = self._audit({"v": 5.0}, {"v": 1.0})
        with pytest.raises(BoundaryLedgerError, match="no resumed tripinfo"):
            apply_restore_deficits(
                {"other": 1.0}, self._final(audit, {"v": 1.0}), audit)

    def test_the_result_normalizes_once_at_production_precision(self):
        from traffic_sim.simulation.warm_state_boundary import (
            TRIPINFO_PRECISION, apply_restore_deficits)
        audit = self._audit({"v": 0.002}, {"v": 0.0})
        out = apply_restore_deficits(
            {"v": 1.00}, self._final(audit, {"v": 1.004}), audit)
        assert out["records"]["v"] == round(1.004 + 0.002,
                                               TRIPINFO_PRECISION) == 1.01
        assert round(1.00 + 0.002, TRIPINFO_PRECISION) == 1.0

    def test_reapplication_is_idempotent_from_the_bound_final_ledger(self):
        """The corrected raw value can never become the next numeric base."""
        from traffic_sim.simulation.warm_state_boundary import (
            apply_restore_deficits)
        audit = self._audit({"v": 9.0}, {"v": 0.0})
        final = self._final(audit, {"v": 1.0})
        once = apply_restore_deficits({"v": 1.0}, final, audit)["records"]["v"]
        twice = apply_restore_deficits(
            {"v": once}, final, audit)["records"]["v"]
        assert once == 10.0 and twice == 10.0
        # Both derive from the same bound unrounded final value, so a caller
        # cannot accidentally compound the deficit.
        assert apply_restore_deficits(
            {"v": 1.0}, final, audit)["applied_count"] == 1

    def test_a_non_finite_resumed_record_is_refused(self):
        import pytest
        from traffic_sim.simulation.warm_state_boundary import (
            BoundaryLedgerError, apply_restore_deficits)
        audit = self._audit({"v": 5.0}, {"v": 1.0})
        with pytest.raises(BoundaryLedgerError, match="not finite"):
            apply_restore_deficits(
                {"v": float("inf")}, self._final(audit, {"v": 1.0}), audit)
