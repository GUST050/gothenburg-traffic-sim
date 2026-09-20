#!/usr/bin/env python3
"""Guarded runner for a WindowCostIndex build: supervise, stop, prove.

The runner never computes a cost. It starts ``tools/wci_guarded_child.py``
in its own process group and watches it against fixed limits:

* raw phase soft 1,090 s (warning) and hard 1,820 s, whole run 7,320.348 s;
* resident size or physical footprint of the whole group at most 12 GiB,
  and system swap growth at most 1 GiB;
* exactly one archive-index build, one validation per build key and one
  parse per variant file;
* the declared population, no SUMO process, a month that is not priced
  entirely at zero, no source/archive/catalog drift, and an identical
  oracle, provider identity and ledger before PASS.

Telemetry that cannot be read stops the run (fail closed). A stop writes
the reason and the last known measurements, sends SIGTERM to the whole
process group, waits a bounded time, uses SIGKILL only if members remain,
waits until the group is empty, removes only this run's unpublished
temporary files and publishes append-only stop evidence. A child that
disappears is never a PASS.

Modes: ``build`` supervises the production month build of a registered
case; ``canary`` supervises a bounded diagnostic canary under its own,
explicitly diagnostic budget and never produces release evidence.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import dataclasses
import datetime
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
from traffic_sim.simulation.window_cost_index import (  # noqa: E402
    publish_new_file,
)

SCHEMA = "wci_guarded_run_v1"
STATUS_SCHEMA = "wci_guarded_status_v1"
GIB = 1024 ** 3
STATES = ("preflight", "running", "stopping", "stopped", "failed", "passed")
CHILD = ROOT / "tools" / "wci_guarded_child.py"
#: Sources whose bytes the run depends on; any change is drift.
RUNTIME_SOURCES = (
    "run_scenario.py",
    "tools/build_window_cost_index.py",
    "tools/closure_effect_eligibility.py",
    "tools/guarded_wci_build.py",
    "tools/wci_guarded_child.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/simulation/closure_ranking.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/metadata.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/window_cost_index.py",
)


class TelemetryUnavailable(RuntimeError):
    """Mandatory telemetry could not be read; the run must stop."""


@dataclasses.dataclass(frozen=True)
class Limits:
    raw_soft_s: float = 1090.0
    raw_hard_s: float = 1820.0
    total_hard_s: float = 7320.348
    memory_hard_bytes: int = 12 * GIB
    swap_growth_hard_bytes: int = 1 * GIB
    telemetry_stale_s: float = 60.0
    poll_s: float = 2.0
    term_grace_s: float = 20.0
    kill_grace_s: float = 10.0
    drift_every_s: float = 30.0


@dataclasses.dataclass(frozen=True)
class Expectations:
    build_keys: int = 30
    daily_units: int = 1950
    variant_records: int = 5850
    parents: Optional[int] = 1690
    variant_files: int = 90
    ledger_required: bool = True


PRODUCTION_LIMITS = Limits()


# -- pure evaluation ----------------------------------------------------------

def counters_view(telemetry: Mapping[str, Any]) -> Dict[str, Any]:
    counters = telemetry.get("counters") or {}
    validations = counters.get("archive_validate") or {}
    parses = counters.get("variant_parses") or {}
    return {
        "archive_index_build": int(counters.get("archive_index_build", 0)),
        "validated_build_keys": len(validations),
        "max_validations_per_key": max(validations.values(), default=0),
        "parsed_variant_files": len(parses),
        "max_parses_per_file": max(parses.values(), default=0),
    }


def running_violation(telemetry: Mapping[str, Any], sample: Mapping[str, Any],
                      limits: Limits,
                      expect: Expectations) -> Optional[str]:
    """The first limit a running child has broken, or ``None``."""
    view = counters_view(telemetry)
    raw_elapsed = sample.get("raw_elapsed_s")
    checks = (
        (sample["elapsed_s"] > limits.total_hard_s, "total_hard_limit"),
        (raw_elapsed is not None and raw_elapsed > limits.raw_hard_s,
         "raw_phase_hard_limit"),
        (sample["memory_bytes"] > limits.memory_hard_bytes,
         "memory_hard_limit"),
        (sample["swap_growth_bytes"] > limits.swap_growth_hard_bytes,
         "swap_growth_limit"),
        (sample["sumo_processes"] > 0, "sumo_process_detected"),
        (view["archive_index_build"] > 1, "archive_index_built_twice"),
        (view["max_validations_per_key"] > 1
         or view["validated_build_keys"] > expect.build_keys,
         "too_many_validations"),
        (view["max_parses_per_file"] > 1
         or view["parsed_variant_files"] > expect.variant_files,
         "variant_parsed_more_than_once"),
        (((telemetry.get("population") or {}).get("daily_units") or 0)
         > expect.daily_units, "population_too_large"),
        (bool(sample.get("drift")), "input_drift"),
    )
    return next((reason for broken, reason in checks if broken), None)


def final_problems(telemetry: Mapping[str, Any],
                   expect: Expectations) -> List[str]:
    """Everything that stops an exited child from being a PASS."""
    problems: List[str] = []
    view = counters_view(telemetry)
    if telemetry.get("error") or telemetry.get("phase") != "done":
        problems.append("child_did_not_finish")
    if view["archive_index_build"] != 1:
        problems.append("archive_index_build_count")
    if (view["validated_build_keys"] != expect.build_keys
            or view["max_validations_per_key"] != 1):
        problems.append("validation_count")
    if (view["parsed_variant_files"] != expect.variant_files
            or view["max_parses_per_file"] != 1):
        problems.append("variant_parse_count")
    population = telemetry.get("population") or {}
    wanted = {"daily_units": expect.daily_units,
              "variant_records": expect.variant_records}
    if expect.parents is not None:
        wanted["parents"] = expect.parents
    if any(population.get(key) != value for key, value in wanted.items()):
        problems.append("population_mismatch")
    if not (telemetry.get("affected_vehicles_total") or 0) > 0:
        problems.append("zero_priced_result")
    oracle = telemetry.get("oracle") or {}
    if not (oracle.get("complete") and oracle.get("identical")):
        problems.append("oracle_mismatch")
    provider = telemetry.get("provider_identity") or {}
    if not (provider.get("complete")
            and provider.get("units") == expect.daily_units):
        problems.append("provider_identity_mismatch")
    ledger = telemetry.get("ledger") or {}
    if expect.ledger_required and ledger.get("identical") is not True:
        problems.append("ledger_mismatch")
    return problems


# -- system adapters ----------------------------------------------------------

class _Rusage(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (f"f{index}", ctypes.c_uint64) for index in range(36)]


_RESIDENT, _FOOTPRINT, _LIFETIME_MAX = 6, 7, 28


class SystemSampler:
    """Memory of a process group, system swap and SUMO processes (macOS)."""

    def __init__(self) -> None:
        path = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
        try:
            self._libproc = ctypes.CDLL(path, use_errno=True)
        except OSError as error:
            raise TelemetryUnavailable(f"libproc: {error}") from error

    @staticmethod
    def _ps() -> List[List[str]]:
        try:
            output = subprocess.run(
                ["/bin/ps", "-Ao", "pid=,pgid=,stat=,rss=,comm="],
                capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.SubprocessError) as error:
            raise TelemetryUnavailable(f"ps: {error}") from error
        rows = [line.split(None, 4) for line in output.splitlines()]
        return [row for row in rows if len(row) == 5]

    def group_members(self, pgid: int) -> List[int]:
        return [int(row[0]) for row in self._ps()
                if int(row[1]) == pgid and not row[2].startswith("Z")]

    def memory(self, pgid: int) -> Dict[str, Any]:
        rows = [row for row in self._ps()
                if int(row[1]) == pgid and not row[2].startswith("Z")]
        resident = footprint = lifetime = 0
        for row in rows:
            info = _Rusage()
            if self._libproc.proc_pid_rusage(int(row[0]), 4,
                                             ctypes.byref(info)) != 0:
                continue  # the process exited between listing and sampling
            values = [getattr(info, f"f{index}") for index in range(36)]
            resident += int(values[_RESIDENT])
            footprint += int(values[_FOOTPRINT])
            lifetime = max(lifetime, int(values[_LIFETIME_MAX]))
        return {"pids": [int(row[0]) for row in rows],
                "resident_bytes": resident, "footprint_bytes": footprint,
                "lifetime_max_footprint_bytes": lifetime}

    @staticmethod
    def swap_used_bytes() -> int:
        try:
            text = subprocess.run(["/usr/sbin/sysctl", "-n", "vm.swapusage"],
                                  capture_output=True, text=True,
                                  check=True).stdout
        except (OSError, subprocess.SubprocessError) as error:
            raise TelemetryUnavailable(f"vm.swapusage: {error}") from error
        match = re.search(r"used\s*=\s*([0-9.]+)M", text)
        if match is None:
            raise TelemetryUnavailable(f"vm.swapusage unreadable: {text!r}")
        return int(float(match.group(1)) * 1024 * 1024)

    def sumo_processes(self) -> int:
        return sum(1 for row in self._ps()
                   if Path(row[4]).name.lower().startswith("sumo"))


class ProcessAdapter:
    """Start and signal one child in its own process group."""

    def __init__(self, sampler: SystemSampler) -> None:
        self._sampler = sampler

    @staticmethod
    def start(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]):
        return subprocess.Popen(  # pylint: disable=consider-using-with
            list(argv), cwd=str(cwd), env=dict(env), start_new_session=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    @staticmethod
    def poll(process) -> Optional[int]:
        return process.poll()

    @staticmethod
    def signal_group(pgid: int, signum: int) -> None:
        try:
            os.killpg(pgid, signum)
        except ProcessLookupError:
            pass

    def group_alive(self, pgid: int) -> bool:
        return bool(self._sampler.group_members(pgid))

    @staticmethod
    def reap(process, timeout: float) -> Optional[int]:
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    @staticmethod
    def output(process) -> str:
        if process.stdout is None or process.stdout.closed:
            return ""
        return process.stdout.read().decode("utf-8", "replace")[-8000:]


class Clock:
    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)

    @staticmethod
    def wall() -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()


# -- drift --------------------------------------------------------------------

def sha256_path(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


class DriftChecker:
    """Content drift of bound files; cheap checks while running.

    ``files`` are rehashed on every check. ``archives`` are rehashed in full
    at preflight and before PASS; while running only their size, mtime and
    inode are compared with what preflight proved, which can only detect a
    change and never lets anything through.
    """

    def __init__(self, files: Mapping[str, Any],
                 archives: Mapping[str, Mapping[str, Any]]) -> None:
        self.files = dict(files)
        self.archives = {key: dict(value) for key, value in archives.items()}
        self._states: Dict[str, Any] = {}
        self.identity = hashlib.sha256(json.dumps(
            {"files": self.files, "archives": self.archives},
            sort_keys=True).encode("utf-8")).hexdigest()

    def _file_problems(self) -> List[str]:
        return [f"file changed: {path}" for path, digest
                in sorted(self.files.items())
                if sha256_path(Path(path)) != digest]

    def full(self) -> List[str]:
        problems = self._file_problems()
        for key, binding in sorted(self.archives.items()):
            for name, digest in sorted(binding["files"].items()):
                path = Path(binding["archive"]) / name
                if sha256_path(path) != digest:
                    problems.append(f"archive {key} changed: {name}")
                else:
                    stat = path.stat()
                    self._states[str(path)] = [stat.st_size, stat.st_mtime_ns,
                                               stat.st_ino]
        return problems

    def quick(self) -> List[str]:
        problems = self._file_problems()
        for path, state in sorted(self._states.items()):
            try:
                stat = Path(path).stat()
            except OSError:
                problems.append(f"archive file missing: {path}")
                continue
            if [stat.st_size, stat.st_mtime_ns, stat.st_ino] != state:
                problems.append(f"archive file changed: {path}")
        return problems


# -- supervisor ---------------------------------------------------------------

class Supervisor:
    """One supervised child from preflight to a terminal state."""

    def __init__(self, *, argv: Sequence[str], cwd: Path,
                 telemetry_path: Path, status_path: Path,
                 limits: Limits, expect: Expectations,
                 drift: DriftChecker,
                 verify_outputs: Callable[[Mapping[str, Any]], List[str]],
                 cleanup: Sequence[Path] = (),
                 adapter=None, sampler=None, clock=None,
                 mode: str = "build") -> None:
        self.argv = list(argv)
        self.cwd = Path(cwd)
        self.telemetry_path = Path(telemetry_path)
        self.status_path = Path(status_path)
        self.limits = limits
        self.expect = expect
        self.drift = drift
        self.verify_outputs = verify_outputs
        self.cleanup = [Path(path) for path in cleanup]
        self.sampler = sampler if sampler is not None else SystemSampler()
        self.adapter = (adapter if adapter is not None
                        else ProcessAdapter(self.sampler))
        self.clock = clock if clock is not None else Clock()
        self.mode = mode
        self.interrupted = False
        self.process = None
        self.status: Dict[str, Any] = {
            "schema": STATUS_SCHEMA, "mode": mode, "state": "preflight",
            "sequence": 0, "started_at": self.clock.wall(),
            "elapsed_s": 0.0, "phase": None, "pid": None, "pgid": None,
            "warnings": [], "events": [], "drift": None, "memory": None,
            "swap": {"before_bytes": None, "now_bytes": None,
                     "growth_bytes": None},
            "memory_peak": {"bytes": 0, "at_elapsed_s": None,
                            "sample": None},
            "counters": None, "population": None,
            "affected_vehicles_total": None, "sumo_processes": None,
            "oracle": None, "provider_identity": None, "ledger": None,
            "published": None, "stop": None, "reason": None,
        }
        self._started = self.clock.monotonic()
        self._telemetry: Dict[str, Any] = {}
        self._last_sequence = None
        self._last_advance = self._started
        self._raw_started = None
        self._raw_finished = None
        self._next_drift = 0.0

    # status ------------------------------------------------------------------
    def _elapsed(self) -> float:
        return self.clock.monotonic() - self._started

    def _event(self, kind: str, **detail: Any) -> None:
        self.status["events"].append(
            {"kind": kind, "elapsed_s": round(self._elapsed(), 3), **detail})

    def write_status(self, **fields: Any) -> None:
        self.status.update(fields)
        self.status["sequence"] += 1
        self.status["elapsed_s"] = round(self._elapsed(), 3)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_name(f".{self.status_path.name}.tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.status, handle, indent=1, sort_keys=True,
                      default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.status_path)

    # sampling ----------------------------------------------------------------
    def _read_telemetry(self) -> Dict[str, Any]:
        try:
            text = self.telemetry_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise TelemetryUnavailable(f"telemetry: {error}") from error
        try:
            return json.loads(text)
        except ValueError as error:
            raise TelemetryUnavailable(
                f"telemetry unreadable: {error}") from error

    def _observe_phase(self, phase: Optional[str], now: float) -> None:
        if phase == "raw_index_records" and self._raw_started is None:
            self._raw_started = now
        elif (self._raw_started is not None and self._raw_finished is None
              and phase not in (None, "raw_index_records")):
            self._raw_finished = now

    def _raw_elapsed(self, now: float) -> Optional[float]:
        if self._raw_started is None:
            return None
        return (self._raw_finished or now) - self._raw_started

    def sample(self, pgid: int) -> Dict[str, Any]:
        """One round of mandatory telemetry; raises when any is missing."""
        now = self.clock.monotonic()
        telemetry = self._read_telemetry()
        if telemetry:
            self._telemetry = telemetry
            if telemetry.get("sequence") != self._last_sequence:
                self._last_sequence = telemetry.get("sequence")
                self._last_advance = now
        if now - self._last_advance > self.limits.telemetry_stale_s:
            raise TelemetryUnavailable("child telemetry is stale")
        self._observe_phase(self._telemetry.get("phase"), now)
        memory = self.sampler.memory(pgid)
        swap = self.sampler.swap_used_bytes()
        sumo = self.sampler.sumo_processes()
        drift = None
        if now - self._started >= self._next_drift:
            drift = self.drift.quick()
            self._next_drift = (now - self._started
                                + self.limits.drift_every_s)
            self.status["drift"] = {
                "checked_elapsed_s": round(now - self._started, 3),
                "problems": drift, "identity": self.drift.identity,
                "kind": "quick"}
        raw_elapsed = self._raw_elapsed(now)
        if (raw_elapsed is not None and raw_elapsed > self.limits.raw_soft_s
                and "raw_phase_soft_limit" not in self.status["warnings"]):
            self.status["warnings"].append("raw_phase_soft_limit")
            self._event("warning", reason="raw_phase_soft_limit")
        return {
            "elapsed_s": now - self._started,
            "raw_elapsed_s": raw_elapsed,
            "memory": memory,
            "memory_bytes": max(memory["resident_bytes"],
                                memory["footprint_bytes"],
                                memory["lifetime_max_footprint_bytes"]),
            "swap_used_bytes": swap,
            "swap_growth_bytes": swap - self.status["swap"]["before_bytes"],
            "sumo_processes": sumo,
            "drift": drift,
        }

    def _record(self, sample: Mapping[str, Any]) -> None:
        telemetry = self._telemetry
        # The last sample is taken after the child has exited, so the peak
        # must be kept: it is the evidence for the memory limit.
        if sample["memory_bytes"] > self.status["memory_peak"]["bytes"]:
            self.status["memory_peak"] = {
                "bytes": sample["memory_bytes"],
                "at_elapsed_s": round(sample["elapsed_s"], 3),
                "sample": dict(sample["memory"])}
        self.status["swap"].update(
            now_bytes=sample["swap_used_bytes"],
            growth_bytes=sample["swap_growth_bytes"])
        self.write_status(
            phase=telemetry.get("phase"),
            memory=dict(sample["memory"],
                        checked_bytes=sample["memory_bytes"]),
            raw_elapsed_s=sample["raw_elapsed_s"],
            counters=counters_view(telemetry),
            population=telemetry.get("population"),
            affected_vehicles_total=telemetry.get("affected_vehicles_total"),
            sumo_processes=sample["sumo_processes"],
            oracle=telemetry.get("oracle"),
            provider_identity=telemetry.get("provider_identity"),
            ledger=telemetry.get("ledger"))

    # stopping ----------------------------------------------------------------
    def stop(self, pgid: int, reason: str) -> Dict[str, Any]:
        self._event("stop", reason=reason)
        self.write_status(state="stopping", reason=reason)
        record: Dict[str, Any] = {"reason": reason, "sigterm": True,
                                  "sigkill": False}
        self.adapter.signal_group(pgid, signal.SIGTERM)
        record["group_empty_after_term"] = self._wait_empty(
            pgid, self.limits.term_grace_s)
        if not record["group_empty_after_term"]:
            record["sigkill"] = True
            self.adapter.signal_group(pgid, signal.SIGKILL)
        record["group_empty"] = self._wait_empty(
            pgid, self.limits.kill_grace_s)
        record["returncode"] = self.adapter.reap(
            self.process, self.limits.kill_grace_s)
        record["removed_partial_files"] = self._remove_partials()
        return record

    def _wait_empty(self, pgid: int, grace: float) -> bool:
        deadline = self.clock.monotonic() + grace
        while True:
            self.adapter.poll(self.process)
            if not self.adapter.group_alive(pgid):
                return True
            if self.clock.monotonic() >= deadline:
                return False
            self.clock.sleep(min(0.2, grace))

    def _remove_partials(self) -> List[str]:
        removed = []
        for root in self.cleanup:
            directory = root if root.is_dir() else root.parent
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob(".*.partial")):
                if root.is_dir() or path.name.startswith(f".{root.name}."):
                    path.unlink()
                    removed.append(str(path))
        return removed

    # the run -----------------------------------------------------------------
    def preflight(self) -> List[str]:
        problems = []
        try:
            before = self.sampler.swap_used_bytes()
            sumo = self.sampler.sumo_processes()
        except TelemetryUnavailable as error:
            return [f"telemetry_unavailable: {error}"]
        self.status["swap"].update(before_bytes=before, now_bytes=before,
                                   growth_bytes=0)
        if sumo:
            problems.append("sumo_process_detected")
        drift = self.drift.full()
        self.status["drift"] = {"checked_elapsed_s": 0.0, "problems": drift,
                                "identity": self.drift.identity,
                                "kind": "full"}
        problems.extend(f"input_drift: {item}" for item in drift)
        return problems

    def interrupt(self) -> None:
        self.interrupted = True

    def run(self, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
        problems = self.preflight()
        if problems:
            self.write_status(state="failed", reason="preflight_failed")
            return self._outcome("failed", "preflight_failed",
                                 problems=problems)
        self.process = self.adapter.start(
            self.argv, cwd=self.cwd,
            env=dict(env if env is not None else os.environ))
        pgid = self.process.pid
        self._event("started", pid=pgid)
        self.write_status(state="running", pid=self.process.pid, pgid=pgid)
        reason = None
        returncode = None
        try:
            while True:
                if self.interrupted:
                    reason = "interrupted"
                    break
                returncode = self.adapter.poll(self.process)
                try:
                    sample = self.sample(pgid)
                except TelemetryUnavailable as error:
                    reason = f"telemetry_unavailable: {error}"
                    break
                self._record(sample)
                if returncode is not None:
                    break
                reason = running_violation(self._telemetry, sample,
                                           self.limits, self.expect)
                if reason:
                    break
                self.clock.sleep(self.limits.poll_s)
            if reason is None:
                return self._finish(pgid, returncode)
        except KeyboardInterrupt:
            # The runner's own failures must never leave the build running:
            # stop the group, keep the reason, and publish it.
            reason = "interrupted"
        except BaseException as error:  # noqa: BLE001 - re-raised as a stop
            reason = f"runner_error: {type(error).__name__}: {error}"
        record = self.stop(pgid, reason)
        self.write_status(state="stopped", stop=record)
        return self._outcome("stopped", reason, stop=record)

    def _finish(self, pgid: int, returncode: int) -> Dict[str, Any]:
        # Grandchildren must not outlive the build.
        orphans = self.adapter.group_alive(pgid)
        record = self.stop(pgid, "orphaned_processes") if orphans else None
        self._telemetry = self._read_telemetry() or self._telemetry
        problems = []
        if returncode != 0:
            problems.append(f"child_exit_{returncode}")
        if not self._telemetry:
            problems.append("child_vanished_without_telemetry")
        problems.extend(final_problems(self._telemetry, self.expect))
        if orphans:
            problems.append("orphaned_processes")
        if not problems:
            problems.extend(self.verify_outputs(self._telemetry))
        if not problems:
            drift = self.drift.full()
            self.status["drift"] = {
                "checked_elapsed_s": round(self._elapsed(), 3),
                "problems": drift, "identity": self.drift.identity,
                "kind": "full"}
            problems.extend(f"input_drift: {item}" for item in drift)
        state = "failed" if problems else "passed"
        self._record_final()
        self.write_status(state=state,
                          reason=problems[0] if problems else None,
                          stop=record)
        return self._outcome(state, problems[0] if problems else None,
                             problems=problems, stop=record,
                             returncode=returncode)

    def _record_final(self) -> None:
        telemetry = self._telemetry
        self.status.update(
            phase=telemetry.get("phase"),
            counters=counters_view(telemetry),
            population=telemetry.get("population"),
            affected_vehicles_total=telemetry.get("affected_vehicles_total"),
            oracle=telemetry.get("oracle"),
            provider_identity=telemetry.get("provider_identity"),
            ledger=telemetry.get("ledger"),
            published=telemetry.get("published"))

    def _outcome(self, state: str, reason: Optional[str],
                 **extra: Any) -> Dict[str, Any]:
        output = ""
        if self.process is not None and self.adapter.poll(
                self.process) is not None:
            output = self.adapter.output(self.process)
        return {"state": state, "reason": reason,
                "status": json.loads(json.dumps(self.status, default=str)),
                "telemetry": self._telemetry,
                "child_output_tail": output, **extra}


# -- production / diagnostic wiring --------------------------------------------

def _file_bindings(paths: Sequence[str]) -> Dict[str, str]:
    return {str(ROOT / path): sha256_path(ROOT / path) or ""
            for path in paths}


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_outputs_verifier(index_path: Path, evidence_path: Path):
    """PASS needs a published, self-consistent index and build evidence."""
    from tools.build_window_cost_index import _digest  # noqa: PLC0415

    def verify(telemetry: Mapping[str, Any]) -> List[str]:
        if not Path(index_path).is_file():
            return ["index_not_published"]
        if not Path(evidence_path).is_file():
            return ["build_evidence_not_published"]
        problems = []
        evidence = _load_json(evidence_path)
        body = {key: value for key, value in evidence.items()
                if key != "content_key"}
        if _digest(body) != evidence.get("content_key"):
            problems.append("build_evidence_content_key")
        if evidence.get("status") != "PASS":
            problems.append(f"build_status_{evidence.get('status')}")
        index = _load_json(index_path)
        if index.get("content_key") != evidence.get("index_content_key"):
            problems.append("index_not_the_evidenced_index")
        published = telemetry.get("published") or {}
        if published.get("evidence_content_key") != evidence.get(
                "content_key"):
            problems.append("telemetry_names_other_evidence")
        return problems

    return verify


def canary_outputs_verifier(forbidden: Sequence[Path]):
    """A diagnostic canary must publish nothing."""
    def verify(_telemetry: Mapping[str, Any]) -> List[str]:
        return [f"canary_published_{path}" for path in forbidden
                if Path(path).exists()]
    return verify


def _publish_evidence(path: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    body = json.loads(json.dumps(record, sort_keys=True, default=str))
    body["content_key"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode(
            "utf-8")).hexdigest()
    publish_new_file(Path(path),
                     (json.dumps(body, indent=1, sort_keys=True)
                      + "\n").encode("utf-8"),
                     label="guarded run evidence")
    return body


def _fresh(paths: Sequence[Optional[Path]]) -> List[str]:
    problems = []
    for path in paths:
        if path is None:
            continue
        if path.exists() and (path.is_file() or any(path.iterdir())):
            problems.append(f"output already exists: {path}")
    return problems


def production_setup(args) -> Dict[str, Any]:
    """Preflight of a registered month build; refuses before any start."""
    from tools import freeze_wci_month_case as month  # noqa: PLC0415

    record = _load_json(args.registration)
    problems = month.verify_registration(record)
    profile = _load_json(args.profile)
    bound = profile.get("bound_spec") or {}
    if bound.get("search_content_key") != record.get("spec_content_key"):
        problems.append(
            f"profile binds another spec: {bound.get('search_content_key')} "
            f"!= {record.get('spec_content_key')}")
    # The registration's own population is the truth for THIS month; a
    # fixed default only ever matches the one month it was measured
    # against, so a new month's registration must describe itself.
    registration_population = record.get("population") or {}
    build_keys = int(registration_population.get("build_keys", 0))
    expect = Expectations(
        build_keys=build_keys,
        daily_units=int(registration_population.get("daily_units", 0)),
        variant_records=int(registration_population.get("variant_records", 0)),
        parents=int(registration_population.get("parents", 0)),
        variant_files=3 * build_keys,
    )
    population = profile.get("population") or {}
    if (population.get("daily_units"), population.get("parents")) != (
            expect.daily_units, expect.parents):
        problems.append(
            f"profile population {population or None} does not match the "
            f"registration's population {registration_population or None}")
    problems.extend(_fresh([args.index_out, args.build_evidence_out]))
    files = {**month.drift_files(record), **_file_bindings(RUNTIME_SOURCES),
             str(Path(args.profile).resolve()): sha256_path(args.profile)}
    return {
        "problems": problems,
        "argv": [sys.executable, str(CHILD), "--job", "build",
                 "--telemetry", str((args.status_dir
                                     / "child-telemetry.json").resolve()),
                 "--profile", str(Path(args.profile).resolve()),
                 "--index-out", str(Path(args.index_out).resolve()),
                 "--evidence-out", str(Path(args.build_evidence_out)
                                       .resolve()),
                 "--evidence-id", args.evidence_id or "guarded-build"],
        "limits": PRODUCTION_LIMITS,
        "expect": expect,
        "drift": DriftChecker(files, month.drift_archives(record)),
        "verify": build_outputs_verifier(
            Path(args.index_out) / "window-cost-index.json",
            args.build_evidence_out),
        "cleanup": [Path(args.index_out), Path(args.build_evidence_out)],
        "bindings": {"registration": {
            "path": str(args.registration),
            "sha256": sha256_path(args.registration),
            "content_key": record.get("content_key"),
            "chosen_edge": record.get("chosen_edge")}},
        "diagnostic": False,
    }


def canary_setup(args) -> Dict[str, Any]:
    """A bounded diagnostic canary under an explicitly diagnostic budget."""
    spec = _load_json(args.canary_spec)
    inventory = _load_json(args.inventory)
    keys = json.loads(args.build_keys)
    archives = {}
    for key in keys:
        record = inventory["inventory"]["archives"][key]
        variants = record["variants"]
        archives[key] = {
            "archive": record["archive"],
            "files": {"demand_meta.json": record["demand_meta_sha256"],
                      "calibrated.rou.xml": variants["q50"][
                          "validated_sha256"],
                      "calibrated_v1.rou.xml": variants["q10"][
                          "validated_sha256"],
                      "calibrated_v2.rou.xml": variants["q90"][
                          "validated_sha256"]}}
    files = dict(_file_bindings(RUNTIME_SOURCES))
    for catalog in inventory["inventory"]["catalogs"].values():
        files[catalog["path"]] = catalog["sha256"]
        files[str(Path(catalog["path"]).with_name("catalog.meta.json"))] = (
            catalog["metadata_sha256"])
    files[str(Path(args.canary_spec).resolve())] = sha256_path(
        args.canary_spec)
    files[str(Path(args.profile).resolve())] = sha256_path(args.profile)
    units = 65 * len(keys)
    problems = ([] if spec.get("inventory_content_key")
                == inventory.get("inventory_content_key")
                else ["canary spec names another inventory"])
    return {
        "problems": problems,
        "argv": [sys.executable, str(CHILD), "--job", "canary",
                 "--telemetry", str((args.status_dir
                                     / "child-telemetry.json").resolve()),
                 "--profile", str(Path(args.profile).resolve()),
                 "--canary-spec", str(Path(args.canary_spec).resolve()),
                 "--oracle-root", str(Path(args.oracle_root).resolve()),
                 "--build-keys", args.build_keys],
        "limits": Limits(**json.loads(args.diagnostic_budget)),
        "expect": Expectations(build_keys=len(keys), daily_units=units,
                               variant_records=3 * units, parents=None,
                               variant_files=3 * len(keys),
                               ledger_required=False),
        "drift": DriftChecker(files, archives),
        "verify": canary_outputs_verifier([]),
        "cleanup": [],
        "bindings": {"canary_spec": {"path": str(args.canary_spec),
                                     "content_key": spec.get("content_key")},
                     "inventory": {"path": str(args.inventory),
                                   "content_key": inventory.get(
                                       "content_key")},
                     "oracle_root": str(args.oracle_root),
                     "build_keys": keys},
        "diagnostic": True,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("build", "canary"), required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--index-out", type=Path)
    parser.add_argument("--build-evidence-out", type=Path)
    parser.add_argument("--evidence-id")
    parser.add_argument("--canary-spec", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--oracle-root", type=Path)
    parser.add_argument("--build-keys")
    parser.add_argument("--diagnostic-budget")
    args = parser.parse_args(argv)
    if args.evidence_out.exists():
        raise SystemExit(f"evidence already exists: {args.evidence_out}")
    if args.status_dir.exists() and any(args.status_dir.iterdir()):
        raise SystemExit(f"status directory is not fresh: {args.status_dir}")
    args.status_dir.mkdir(parents=True, exist_ok=True)
    setup = (production_setup(args) if args.mode == "build"
             else canary_setup(args))
    supervisor = Supervisor(
        argv=setup["argv"], cwd=ROOT,
        telemetry_path=args.status_dir / "child-telemetry.json",
        status_path=args.status_dir / "status.json",
        limits=setup["limits"], expect=setup["expect"],
        drift=setup["drift"], verify_outputs=setup["verify"],
        cleanup=setup["cleanup"], mode=args.mode)
    if setup["problems"]:
        supervisor.write_status(state="failed", reason="preflight_refused")
        outcome = {"state": "failed", "reason": "preflight_refused",
                   "problems": setup["problems"],
                   "status": json.loads(json.dumps(supervisor.status,
                                                   default=str))}
    else:
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_args: supervisor.interrupt())
        outcome = supervisor.run()
    record = _publish_evidence(args.evidence_out, {
        "schema": SCHEMA,
        "release_evidence": False,
        "diagnostic": setup["diagnostic"],
        "mode": args.mode,
        "limits": dataclasses.asdict(setup["limits"]),
        "expectations": dataclasses.asdict(setup["expect"]),
        "bindings": setup["bindings"],
        "drift_identity": setup["drift"].identity,
        "argv": setup["argv"],
        "runner_sources": _file_bindings(RUNTIME_SOURCES),
        "outcome": outcome,
    })
    print(json.dumps({"state": outcome["state"],
                      "reason": outcome.get("reason"),
                      "problems": outcome.get("problems"),
                      "evidence": str(args.evidence_out),
                      "content_key": record["content_key"]}, indent=1))
    return 0 if outcome["state"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
