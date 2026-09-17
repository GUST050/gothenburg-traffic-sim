"""The guarded WindowCostIndex runner: limits, stops and process hygiene.

Every limit is exercised through an injected clock, sampler and process
adapter, so no test allocates 12 GiB or waits for a real deadline. The
signal handling is exercised against small real child processes, because
that is the part no double can prove.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from tools import guarded_wci_build as guard

ROOT = Path(__file__).resolve().parents[1]
UNITS, VARIANTS, PARENTS = 1950, 5850, 1690


def telemetry(**overrides: Any) -> Dict[str, Any]:
    """A complete, healthy final telemetry document."""
    record = {
        "schema": "wci_guarded_child_telemetry_v1", "job": "build",
        "sequence": 7, "phase": "done", "error": None,
        "counters": {"archive_index_build": 1,
                     "archive_validate": {f"key-{n}": 1 for n in range(30)},
                     "variant_parses": {f"file-{n}": 1 for n in range(90)}},
        "population": {"daily_units": UNITS, "variant_records": VARIANTS,
                       "parents": PARENTS},
        "affected_vehicles_total": 866217,
        "oracle": {"complete": True, "identical": True},
        "provider_identity": {"units": UNITS, "complete": True},
        "ledger": {"identical": True, "status": "PASS"},
        "published": {"evidence_content_key": "e" * 64},
        "result": {"status": "PASS"},
    }
    record.update(overrides)
    return record


def sample(**overrides: Any) -> Dict[str, Any]:
    record = {"elapsed_s": 10.0, "raw_elapsed_s": 5.0,
              "memory": {"resident_bytes": 1, "footprint_bytes": 1,
                         "lifetime_max_footprint_bytes": 1},
              "memory_bytes": 1 << 30, "swap_used_bytes": 0,
              "swap_growth_bytes": 0, "sumo_processes": 0, "drift": []}
    record.update(overrides)
    return record


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.on_sleep = None

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(self)

    @staticmethod
    def wall() -> str:
        return "2026-09-17T00:00:00+00:00"


class FakeSampler:
    """Scripted system telemetry; any value may be an exception."""

    def __init__(self, *, memory=None, swap=0, sumo=0) -> None:
        self.memory_value = memory or {
            "resident_bytes": 1 << 20, "footprint_bytes": 1 << 20,
            "lifetime_max_footprint_bytes": 1 << 20, "pids": [1]}
        self.swap = swap
        self.sumo = sumo

    @staticmethod
    def _maybe(value):
        if isinstance(value, Exception):
            raise value
        return value

    def memory(self, _pgid):
        return self._maybe(self.memory_value)

    def swap_used_bytes(self):
        return self._maybe(self.swap)

    def sumo_processes(self):
        return self._maybe(self.sumo)

    @staticmethod
    def group_members(_pgid):
        return []


class FakeProcess:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.stdout = None


class FakeAdapter:
    """A child that lives for a scripted number of polls."""

    def __init__(self, *, polls: int = 2, returncode: int = 0,
                 group_alive: bool = False) -> None:
        self.remaining = polls
        self.returncode = returncode
        self.alive_after_exit = group_alive
        self.signals: List[int] = []
        self.started = None
        self.reaped = False

    def start(self, argv, *, cwd, env):
        self.started = {"argv": list(argv), "cwd": str(cwd), "env": dict(env)}
        return FakeProcess()

    def poll(self, _process):
        if self.remaining > 0:
            self.remaining -= 1
            return None
        return self.returncode

    def signal_group(self, _pgid, signum):
        self.signals.append(signum)
        self.alive_after_exit = False

    def group_alive(self, _pgid):
        return self.alive_after_exit

    def reap(self, _process, _timeout):
        self.reaped = True
        return self.returncode

    @staticmethod
    def output(_process):
        return ""


def build(tmp_path, *, adapter=None, sampler=None, clock=None, limits=None,
          expect=None, verify=None, telemetry_record=None, drift=None,
          cleanup=()):
    telemetry_path = tmp_path / "child-telemetry.json"
    if telemetry_record is not None:
        telemetry_path.write_text(json.dumps(telemetry_record),
                                  encoding="utf-8")
    return guard.Supervisor(
        argv=[sys.executable, "-c", "pass"], cwd=tmp_path,
        telemetry_path=telemetry_path, status_path=tmp_path / "status.json",
        limits=limits or guard.Limits(poll_s=1.0, term_grace_s=1.0,
                                      kill_grace_s=1.0),
        expect=expect or guard.Expectations(),
        drift=drift if drift is not None else guard.DriftChecker({}, {}),
        verify_outputs=verify or (lambda _telemetry: []),
        cleanup=cleanup, adapter=adapter or FakeAdapter(),
        sampler=sampler or FakeSampler(), clock=clock or FakeClock())


class TestPureLimits:
    def test_a_healthy_sample_breaks_nothing(self):
        assert guard.running_violation(telemetry(), sample(), guard.Limits(),
                                       guard.Expectations()) is None

    @pytest.mark.parametrize("patch,expected", [
        ({"elapsed_s": 7400.0}, "total_hard_limit"),
        ({"raw_elapsed_s": 1900.0}, "raw_phase_hard_limit"),
        ({"memory_bytes": 13 * guard.GIB}, "memory_hard_limit"),
        ({"swap_growth_bytes": 2 * guard.GIB}, "swap_growth_limit"),
        ({"sumo_processes": 1}, "sumo_process_detected"),
        ({"drift": ["file changed: x"]}, "input_drift"),
    ])
    def test_each_sample_limit_stops_the_run(self, patch, expected):
        assert guard.running_violation(telemetry(), sample(**patch),
                                       guard.Limits(),
                                       guard.Expectations()) == expected

    @pytest.mark.parametrize("counters,expected", [
        ({"archive_index_build": 2}, "archive_index_built_twice"),
        ({"archive_validate": {f"key-{n}": 1 for n in range(31)}},
         "too_many_validations"),
        ({"archive_validate": {"key-0": 2}}, "too_many_validations"),
        ({"variant_parses": {"file-0": 2}}, "variant_parsed_more_than_once"),
        ({"variant_parses": {f"file-{n}": 1 for n in range(91)}},
         "variant_parsed_more_than_once"),
    ])
    def test_each_counter_limit_stops_the_run(self, counters, expected):
        record = telemetry()
        record["counters"].update(counters)
        assert guard.running_violation(record, sample(), guard.Limits(),
                                       guard.Expectations()) == expected

    def test_a_population_larger_than_the_month_stops_the_run(self):
        record = telemetry(population={"daily_units": UNITS + 1})
        assert guard.running_violation(
            record, sample(), guard.Limits(),
            guard.Expectations()) == "population_too_large"

    def test_a_healthy_child_has_no_final_problem(self):
        assert guard.final_problems(telemetry(), guard.Expectations()) == []

    @pytest.mark.parametrize("patch,expected", [
        ({"phase": "raw_index_records"}, "child_did_not_finish"),
        ({"error": "ParseError"}, "child_did_not_finish"),
        ({"population": {"daily_units": 1949, "variant_records": VARIANTS,
                         "parents": PARENTS}}, "population_mismatch"),
        ({"population": {"daily_units": UNITS, "variant_records": VARIANTS,
                         "parents": 1689}}, "population_mismatch"),
        ({"affected_vehicles_total": 0}, "zero_priced_result"),
        ({"oracle": {"complete": True, "identical": False}},
         "oracle_mismatch"),
        ({"provider_identity": {"units": UNITS, "complete": False}},
         "provider_identity_mismatch"),
        ({"ledger": {"identical": False}}, "ledger_mismatch"),
        ({"ledger": {"identical": None}}, "ledger_mismatch"),
    ])
    def test_each_final_check_refuses_a_pass(self, patch, expected):
        assert expected in guard.final_problems(telemetry(**patch),
                                                guard.Expectations())

    @pytest.mark.parametrize("counters,expected", [
        ({"archive_index_build": 0}, "archive_index_build_count"),
        ({"archive_validate": {f"key-{n}": 1 for n in range(29)}},
         "validation_count"),
        ({"variant_parses": {f"file-{n}": 1 for n in range(89)}},
         "variant_parse_count"),
    ])
    def test_each_final_counter_refuses_a_pass(self, counters, expected):
        record = telemetry()
        record["counters"].update(counters)
        assert expected in guard.final_problems(record, guard.Expectations())


class TestSupervisedRun:
    def test_a_healthy_run_passes_and_writes_status(self, tmp_path):
        supervisor = build(tmp_path, telemetry_record=telemetry())
        outcome = supervisor.run(env={})
        assert outcome["state"] == "passed"
        status = json.loads((tmp_path / "status.json").read_text())
        assert status["state"] == "passed"
        assert status["counters"]["archive_index_build"] == 1
        assert status["population"]["daily_units"] == UNITS
        assert status["swap"]["before_bytes"] == 0
        assert [event["kind"] for event in status["events"]] == ["started"]

    def test_the_soft_raw_limit_warns_without_stopping(self, tmp_path):
        clock = FakeClock()
        raw = heartbeat(tmp_path, phase="raw_index_records")
        done = None
        supervisor = build(tmp_path, clock=clock,
                           adapter=FakeAdapter(polls=3),
                           limits=guard.Limits(poll_s=1100.0,
                                               telemetry_stale_s=1200.0))

        def beat(_clock):
            nonlocal done
            if done is None:  # the raw phase ends after the soft limit
                done = heartbeat(tmp_path)
            else:
                done()

        clock.on_sleep = beat
        outcome = supervisor.run(env={})
        assert outcome["state"] == "passed"
        assert outcome["status"]["warnings"] == ["raw_phase_soft_limit"]
        assert raw is not None

    def test_the_hard_raw_limit_stops_the_run(self, tmp_path):
        clock = FakeClock()
        clock.on_sleep = heartbeat(tmp_path, phase="raw_index_records")
        supervisor = build(tmp_path, clock=clock,
                           adapter=FakeAdapter(polls=5),
                           limits=guard.Limits(poll_s=1000.0,
                                               telemetry_stale_s=1100.0))
        outcome = supervisor.run(env={})
        assert (outcome["state"], outcome["reason"]) == (
            "stopped", "raw_phase_hard_limit")

    def test_the_total_limit_stops_the_run(self, tmp_path):
        clock = FakeClock()
        clock.on_sleep = heartbeat(tmp_path, phase="indexed_ledger")
        supervisor = build(tmp_path, clock=clock,
                           adapter=FakeAdapter(polls=20),
                           limits=guard.Limits(poll_s=1000.0,
                                               telemetry_stale_s=1100.0))
        outcome = supervisor.run(env={})
        assert (outcome["state"], outcome["reason"]) == ("stopped",
                                                         "total_hard_limit")

    def test_the_memory_limit_stops_the_run(self, tmp_path):
        sampler = FakeSampler(memory={"resident_bytes": 13 * guard.GIB,
                                      "footprint_bytes": 1, "pids": [1],
                                      "lifetime_max_footprint_bytes": 1})
        supervisor = build(tmp_path, sampler=sampler,
                           telemetry_record=telemetry(phase="persist_index"))
        outcome = supervisor.run(env={})
        assert (outcome["state"], outcome["reason"]) == ("stopped",
                                                         "memory_hard_limit")

    def test_the_swap_limit_stops_the_run(self, tmp_path):
        sampler = FakeSampler(swap=0)
        supervisor = build(tmp_path, sampler=sampler,
                           telemetry_record=telemetry(phase="persist_index"),
                           adapter=FakeAdapter(polls=3))
        original_preflight = supervisor.preflight

        def preflight_then_grow():
            problems = original_preflight()
            sampler.swap = 3 * guard.GIB
            return problems

        supervisor.preflight = preflight_then_grow
        outcome = supervisor.run(env={})
        assert (outcome["state"], outcome["reason"]) == ("stopped",
                                                         "swap_growth_limit")

    def test_a_sumo_process_before_the_start_refuses_the_run(self, tmp_path):
        supervisor = build(tmp_path, sampler=FakeSampler(sumo=1),
                           telemetry_record=telemetry())
        outcome = supervisor.run(env={})
        assert outcome["state"] == "failed"
        assert "sumo_process_detected" in outcome["problems"]

    def test_a_sumo_process_during_the_run_stops_it(self, tmp_path):
        sampler = FakeSampler(sumo=0)
        supervisor = build(tmp_path, sampler=sampler,
                           telemetry_record=telemetry(phase="persist_index"),
                           adapter=FakeAdapter(polls=3))
        original_preflight = supervisor.preflight

        def preflight_then_launch():
            problems = original_preflight()
            sampler.sumo = 1
            return problems

        supervisor.preflight = preflight_then_launch
        outcome = supervisor.run(env={})
        assert (outcome["state"], outcome["reason"]) == (
            "stopped", "sumo_process_detected")

    def test_missing_telemetry_stops_the_run_fail_closed(self, tmp_path):
        sampler = FakeSampler()
        sampler.swap = guard.TelemetryUnavailable("vm.swapusage: no")
        supervisor = build(tmp_path, sampler=sampler,
                           telemetry_record=telemetry())
        outcome = supervisor.run(env={})
        assert outcome["state"] == "failed"
        assert outcome["problems"][0].startswith("telemetry_unavailable")

    def test_unreadable_system_memory_stops_a_running_child(self, tmp_path):
        sampler = FakeSampler()
        supervisor = build(tmp_path, sampler=sampler,
                           telemetry_record=telemetry(phase="persist_index"),
                           adapter=FakeAdapter(polls=3))
        original_preflight = supervisor.preflight

        def preflight_then_break():
            problems = original_preflight()
            sampler.memory_value = guard.TelemetryUnavailable("proc_pid")
            return problems

        supervisor.preflight = preflight_then_break
        outcome = supervisor.run(env={})
        assert outcome["state"] == "stopped"
        assert outcome["reason"].startswith("telemetry_unavailable")

    def test_a_stale_child_heartbeat_stops_the_run(self, tmp_path):
        supervisor = build(tmp_path, clock=FakeClock(),
                           telemetry_record=telemetry(phase="persist_index"),
                           adapter=FakeAdapter(polls=5),
                           limits=guard.Limits(poll_s=30.0,
                                               telemetry_stale_s=10.0))
        outcome = supervisor.run(env={})
        assert outcome["state"] == "stopped"
        assert "stale" in outcome["reason"]

    def test_a_child_that_never_writes_telemetry_stops_the_run(self,
                                                               tmp_path):
        supervisor = build(tmp_path, clock=FakeClock(),
                           adapter=FakeAdapter(polls=5),
                           limits=guard.Limits(poll_s=30.0,
                                               telemetry_stale_s=45.0))
        outcome = supervisor.run(env={})
        assert outcome["state"] == "stopped"
        assert "stale" in outcome["reason"]

    def test_a_crashed_child_is_never_a_pass(self, tmp_path):
        supervisor = build(tmp_path, adapter=FakeAdapter(returncode=3),
                           telemetry_record=telemetry(
                               phase="failed",
                               error="WindowCostIndexError: boom"))
        outcome = supervisor.run(env={})
        assert outcome["state"] == "failed"
        assert "child_exit_3" in outcome["problems"]
        assert "child_did_not_finish" in outcome["problems"]

    def test_a_vanished_child_without_telemetry_is_never_a_pass(self,
                                                                tmp_path):
        supervisor = build(tmp_path, adapter=FakeAdapter(returncode=0))
        outcome = supervisor.run(env={})
        assert outcome["state"] == "failed"
        assert "child_vanished_without_telemetry" in outcome["problems"]

    def test_surviving_group_members_fail_the_run(self, tmp_path):
        adapter = FakeAdapter(group_alive=True)
        supervisor = build(tmp_path, adapter=adapter,
                           telemetry_record=telemetry())
        outcome = supervisor.run(env={})
        assert outcome["state"] == "failed"
        assert "orphaned_processes" in outcome["problems"]
        assert signal.SIGTERM in adapter.signals

    def test_an_interrupt_stops_the_run(self, tmp_path):
        clock = FakeClock()
        adapter = FakeAdapter(polls=10)
        supervisor = build(tmp_path, clock=clock, adapter=adapter,
                           telemetry_record=telemetry(phase="persist_index"))
        clock.on_sleep = lambda _clock: supervisor.interrupt()
        outcome = supervisor.run(env={})
        assert (outcome["state"], outcome["reason"]) == ("stopped",
                                                         "interrupted")
        assert signal.SIGTERM in adapter.signals
        assert outcome["stop"]["removed_partial_files"] == []

    def test_unpublished_partial_files_are_removed_on_stop(self, tmp_path):
        index_root = tmp_path / "index"
        index_root.mkdir()
        partial = index_root / ".window-cost-index.json.1.partial"
        partial.write_text("half", encoding="utf-8")
        keep = index_root / "window-cost-index.json"
        keep.write_text("{}", encoding="utf-8")
        clock = FakeClock()
        supervisor = build(tmp_path, clock=clock, adapter=FakeAdapter(
            polls=10), telemetry_record=telemetry(phase="persist_index"),
            cleanup=[index_root])
        clock.on_sleep = lambda _clock: supervisor.interrupt()
        outcome = supervisor.run(env={})
        assert outcome["stop"]["removed_partial_files"] == [str(partial)]
        assert not partial.exists() and keep.exists()

    def test_drift_stops_the_run(self, tmp_path):
        bound = tmp_path / "bound.txt"
        bound.write_text("original", encoding="utf-8")
        drift = guard.DriftChecker({str(bound): guard.sha256_path(bound)}, {})
        supervisor = build(tmp_path, drift=drift,
                           telemetry_record=telemetry(),
                           adapter=FakeAdapter(polls=3))
        bound.write_text("changed", encoding="utf-8")
        outcome = supervisor.run(env={})
        assert outcome["state"] == "failed"
        assert outcome["problems"][0].startswith("input_drift")

    def test_drift_after_the_child_exits_refuses_a_pass(self, tmp_path):
        bound = tmp_path / "bound.txt"
        bound.write_text("original", encoding="utf-8")
        drift = guard.DriftChecker({str(bound): guard.sha256_path(bound)}, {})

        def verify(_telemetry):
            bound.write_text("changed", encoding="utf-8")
            return []

        supervisor = build(tmp_path, drift=drift, verify=verify,
                           telemetry_record=telemetry(),
                           adapter=FakeAdapter(polls=0))
        outcome = supervisor.run(env={})
        assert outcome["state"] == "failed"
        assert any(item.startswith("input_drift")
                   for item in outcome["problems"])

    def test_unverified_outputs_refuse_a_pass(self, tmp_path):
        supervisor = build(tmp_path, telemetry_record=telemetry(),
                           verify=lambda _telemetry: ["index_not_published"])
        outcome = supervisor.run(env={})
        assert outcome["state"] == "failed"
        assert outcome["problems"] == ["index_not_published"]

    def test_every_status_write_is_atomic(self, tmp_path, monkeypatch):
        seen = []
        production = os.replace

        def recording(source, target):
            seen.append((Path(source).name, Path(target).name))
            return production(source, target)

        monkeypatch.setattr(os, "replace", recording)
        build(tmp_path, telemetry_record=telemetry()).run(env={})
        assert seen and all(pair == (".status.json.tmp", "status.json")
                            for pair in seen)


class TestArchiveDrift:
    def test_full_checks_hash_and_quick_checks_state(self, tmp_path):
        archive = tmp_path / "demand-a"
        archive.mkdir()
        (archive / "demand_meta.json").write_text("{}", encoding="utf-8")
        digest = guard.sha256_path(archive / "demand_meta.json")
        checker = guard.DriftChecker({}, {"key-a": {
            "archive": str(archive), "files": {"demand_meta.json": digest}}})
        assert checker.full() == [] and checker.quick() == []
        time.sleep(0.01)
        (archive / "demand_meta.json").write_text('{"x": 1}',
                                                  encoding="utf-8")
        assert checker.quick() == [
            f"archive file changed: {archive / 'demand_meta.json'}"]
        assert checker.full() == ["archive key-a changed: demand_meta.json"]


def heartbeat(tmp_path, **overrides):
    """A live child: every beat advances the telemetry sequence."""
    state = {"sequence": 10}

    def beat(_clock=None):
        state["sequence"] += 1
        (tmp_path / "child-telemetry.json").write_text(
            json.dumps(telemetry(sequence=state["sequence"], **overrides)),
            encoding="utf-8")

    beat()
    return beat


def _script(path: Path, body: str) -> List[str]:
    path.write_text(body, encoding="utf-8")
    return [sys.executable, str(path)]


class TestRealProcesses:
    """Signals and process groups, against small real children."""

    @staticmethod
    def _supervisor(tmp_path, argv, *, limits):
        sampler = guard.SystemSampler()
        return guard.Supervisor(
            argv=argv, cwd=ROOT,
            telemetry_path=tmp_path / "child-telemetry.json",
            status_path=tmp_path / "status.json", limits=limits,
            expect=guard.Expectations(), drift=guard.DriftChecker({}, {}),
            verify_outputs=lambda _telemetry: [],
            adapter=guard.ProcessAdapter(sampler), sampler=sampler,
            clock=guard.Clock())

    def test_a_hung_child_that_ignores_sigterm_is_killed(self, tmp_path):
        argv = _script(tmp_path / "hung.py", f"""
import json, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open({str(tmp_path / 'child-telemetry.json')!r}, 'w') as handle:
    handle.write(json.dumps({{"sequence": 1, "phase": "raw_index_records"}}))
time.sleep(120)
""")
        supervisor = self._supervisor(
            tmp_path, argv,
            limits=guard.Limits(poll_s=0.2, memory_hard_bytes=1,
                                term_grace_s=1.0, kill_grace_s=5.0))
        outcome = supervisor.run()
        assert outcome["state"] == "stopped"
        assert outcome["stop"]["sigkill"] is True
        assert outcome["stop"]["group_empty"] is True
        assert supervisor.sampler.group_members(
            outcome["status"]["pgid"]) == []

    def test_no_grandchild_survives_a_stop(self, tmp_path):
        argv = _script(tmp_path / "parent.py", f"""
import json, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c',
    'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
    'time.sleep(120)'])
with open({str(tmp_path / 'child-telemetry.json')!r}, 'w') as handle:
    handle.write(json.dumps({{"sequence": 1, "phase": "raw_index_records",
                              "grandchild": child.pid}}))
time.sleep(120)
""")
        supervisor = self._supervisor(
            tmp_path, argv,
            limits=guard.Limits(poll_s=0.2, memory_hard_bytes=1,
                                term_grace_s=1.0, kill_grace_s=5.0))
        outcome = supervisor.run()
        assert outcome["state"] == "stopped"
        assert supervisor.sampler.group_members(
            outcome["status"]["pgid"]) == []
        grandchild = json.loads(
            (tmp_path / "child-telemetry.json").read_text())["grandchild"]
        with pytest.raises(OSError):
            os.kill(grandchild, 0)

    def test_a_child_that_exits_is_reaped_and_judged(self, tmp_path):
        argv = _script(tmp_path / "quick.py", f"""
import json
with open({str(tmp_path / 'child-telemetry.json')!r}, 'w') as handle:
    handle.write({json.dumps(json.dumps(telemetry()))})
""")
        supervisor = self._supervisor(tmp_path, argv,
                                      limits=guard.Limits(poll_s=0.2))
        outcome = supervisor.run()
        assert outcome["state"] == "passed"
        assert outcome["returncode"] == 0
        assert supervisor.sampler.group_members(
            outcome["status"]["pgid"]) == []


class TestEvidence:
    def test_run_evidence_is_atomic_and_append_only(self, tmp_path):
        path = tmp_path / "run.json"
        record = guard._publish_evidence(
            path, {"schema": guard.SCHEMA, "outcome": {"state": "passed"}})
        stored = json.loads(path.read_text())
        assert stored == record and stored["content_key"]
        with pytest.raises(FileExistsError):
            guard._publish_evidence(path, {"schema": guard.SCHEMA})
        assert json.loads(path.read_text()) == stored

    def test_a_canary_that_published_anything_fails(self, tmp_path):
        forbidden = tmp_path / "index.json"
        verify = guard.canary_outputs_verifier([forbidden])
        assert verify({}) == []
        forbidden.write_text("{}", encoding="utf-8")
        assert verify({}) == [f"canary_published_{forbidden}"]

    def test_build_outputs_must_be_the_evidenced_ones(self, tmp_path):
        from tools.build_window_cost_index import _digest

        index = tmp_path / "window-cost-index.json"
        evidence = tmp_path / "evidence.json"
        verify = guard.build_outputs_verifier(index, evidence)
        assert verify({}) == ["index_not_published"]
        index.write_text(json.dumps({"content_key": "i" * 64}),
                         encoding="utf-8")
        assert verify({}) == ["build_evidence_not_published"]
        body = {"status": "PASS", "index_content_key": "i" * 64}
        evidence.write_text(json.dumps({**body, "content_key": _digest(body)}),
                            encoding="utf-8")
        assert verify({"published": {
            "evidence_content_key": _digest(body)}}) == []
        assert "telemetry_names_other_evidence" in verify(
            {"published": {"evidence_content_key": "z" * 64}})


class TestPeakMemory:
    """The last sample is taken after the child exits, so it reads zero."""

    class DecayingSampler(FakeSampler):
        def __init__(self, values):
            super().__init__()
            self.values = list(values)

        def memory(self, _pgid):
            value = self.values.pop(0) if self.values else 0
            return {"resident_bytes": value, "footprint_bytes": 0,
                    "lifetime_max_footprint_bytes": 0, "pids": []}

    def test_the_peak_survives_a_child_that_has_exited(self, tmp_path):
        sampler = self.DecayingSampler([3 * guard.GIB, 7 * guard.GIB, 0])
        supervisor = build(tmp_path, sampler=sampler,
                           adapter=FakeAdapter(polls=2),
                           telemetry_record=telemetry())
        outcome = supervisor.run(env={})
        assert outcome["state"] == "passed"
        peak = outcome["status"]["memory_peak"]
        assert peak["bytes"] == 7 * guard.GIB
        assert peak["at_elapsed_s"] >= 0
        assert outcome["status"]["memory"]["checked_bytes"] == 0


class TestTheRunnerSurvivesItsOwnFaults:
    """An error inside the runner must never leave a child running."""

    class BrokenSampler(FakeSampler):
        def memory(self, _pgid):
            raise RuntimeError("proc listing exploded")

    def test_an_unexpected_runner_error_stops_the_child(self, tmp_path):
        adapter = FakeAdapter(polls=5)
        supervisor = build(tmp_path, sampler=self.BrokenSampler(),
                           adapter=adapter, telemetry_record=telemetry())
        outcome = supervisor.run(env={})
        assert outcome["state"] in ("stopped", "failed")
        assert "runner_error" in outcome["reason"]
        assert "proc listing exploded" in outcome["reason"]
        assert signal.SIGTERM in adapter.signals
        assert adapter.reaped is True

    @pytest.mark.parametrize("make", [
        lambda tmp_path: build(tmp_path, telemetry_record=telemetry()),
        lambda tmp_path: build(tmp_path, adapter=FakeAdapter(returncode=2),
                               telemetry_record=telemetry()),
        lambda tmp_path: build(tmp_path, sampler=FakeSampler(sumo=1),
                               telemetry_record=telemetry()),
    ], ids=["passed", "child-exit", "preflight"])
    def test_the_status_never_stays_running(self, tmp_path, make):
        supervisor = make(tmp_path)
        supervisor.run(env={})
        status = json.loads((tmp_path / "status.json").read_text())
        assert status["state"] in ("passed", "failed", "stopped")

    def test_a_runner_error_still_leaves_a_terminal_status(self, tmp_path):
        supervisor = build(tmp_path, sampler=self.BrokenSampler(),
                           adapter=FakeAdapter(polls=5),
                           telemetry_record=telemetry())
        supervisor.run(env={})
        status = json.loads((tmp_path / "status.json").read_text())
        assert status["state"] == "stopped"
        assert status["stop"]["reason"].startswith("runner_error")
