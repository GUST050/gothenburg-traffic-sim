"""Which Python runs the simulation jobs, and can it actually run them.

Regression for a real failure (2026-10-10): the server ran on Homebrew's
python3 (3.13, no pandas, no SUMO). serve.py starts every job with
sys.executable, /api/ping said "yes" because the server answered, so the UI
offered "Byt dag" and every job died instantly with a ModuleNotFoundError,
shown in the banner as a raw ANSI-coloured traceback (FORCE_COLOR=1 was
inherited and Python 3.13 honours it on a pipe).
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

import pytest

from traffic_sim.simulation import interpreter as interp

REPO = Path(__file__).resolve().parent.parent
THIS_PYTHON = sys.version_info[:2]


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _project_with_venv(tmp_path: Path) -> Path:
    """A project root whose .venv/bin/python is a real, runnable Python."""
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(sys.executable)
    return tmp_path


def _fake_sumo_home(tmp_path: Path, binaries=interp.SUMO_BINARIES,
                    version="1.27.1") -> Path:
    home = tmp_path / "sumo_home"
    for name in binaries:
        _executable(home / "bin" / name,
                    f"#!/bin/sh\necho 'Eclipse SUMO {name} {version}'\n")
    return home


def _lock(tmp_path: Path, pins: dict) -> Path:
    path = tmp_path / "requirements.lock"
    path.write_text("# test lock\n" + "".join(
        f"{name}=={version}\n" for name, version in pins.items()))
    return path


def _installed(dist: str) -> str:
    from importlib import metadata
    return metadata.version(dist)


# ── Relaunch: hand over only when the current interpreter cannot simulate ──

def _verdicts(mapping, default="unavailable"):
    """A probe stub: interpreter path -> readiness status."""
    def probe(python, _root):
        return interp.Readiness(mapping.get(str(python), default),
                                python=str(python))
    return probe


class TestRelaunchTarget:
    def test_a_broken_interpreter_hands_over_to_a_ready_venv(self, tmp_path):
        root = _project_with_venv(tmp_path)
        venv = str(root / ".venv" / "bin" / "python")
        target = interp.relaunch_target(
            root, current="/opt/homebrew/bin/python3", env={},
            probe=_verdicts({venv: "ready"}))
        assert target == root / ".venv" / "bin" / "python"

    def test_an_interpreter_that_can_simulate_is_never_replaced(self, tmp_path):
        """Regression 2026-10-10: the project's working environment was
        /usr/bin/python3 3.9 with its own caches; a .venv existing must not
        hijack it into a different runtime."""
        root = _project_with_venv(tmp_path)
        venv = str(root / ".venv" / "bin" / "python")
        assert interp.relaunch_target(
            root, current="/usr/bin/python3", env={},
            probe=_verdicts({"/usr/bin/python3": "ready", venv: "ready"})) is None

    def test_an_inconclusive_check_stays_put(self, tmp_path):
        root = _project_with_venv(tmp_path)
        venv = str(root / ".venv" / "bin" / "python")
        assert interp.relaunch_target(
            root, current="/usr/bin/python3", env={},
            probe=_verdicts({"/usr/bin/python3": "unknown", venv: "ready"})) is None

    def test_no_venv_means_no_relaunch(self, tmp_path):
        assert interp.relaunch_target(
            tmp_path, current="/usr/bin/python3", env={},
            probe=_verdicts({})) is None

    def test_a_venv_that_cannot_simulate_either_is_not_used(self, tmp_path):
        root = _project_with_venv(tmp_path)
        assert interp.relaunch_target(
            root, current="/usr/bin/python3", env={},
            probe=_verdicts({})) is None

    def test_a_dangling_venv_symlink_is_not_even_probed(self, tmp_path):
        """What a Homebrew upgrade that removes python@3.12 leaves behind."""
        venv_python = tmp_path / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(tmp_path / "removed-python3.12")
        probed = []

        def probe(python, root):
            probed.append(str(python))
            return interp.Readiness("ready" if python != "/usr/bin/python3"
                                    else "unavailable")
        assert interp.relaunch_target(
            tmp_path, current="/usr/bin/python3", env={}, probe=probe) is None
        assert probed == ["/usr/bin/python3"]

    def test_the_guard_variable_prevents_a_relaunch_loop(self, tmp_path):
        root = _project_with_venv(tmp_path)
        assert interp.relaunch_target(
            root, current="/usr/bin/python3",
            env={interp.RELAUNCH_GUARD_ENV: "1"},
            probe=_verdicts({})) is None


class TestConfiguredInterpreter:
    """A machine whose working environment is not a .venv says so once, in
    the untracked file `.simulation-python` (2026-10-10: /usr/bin/python3
    3.9 + SUMO 1.27.1, which every cache on that machine binds)."""

    def test_no_file_means_no_configured_interpreter(self, tmp_path):
        assert interp.configured_python(tmp_path) is None

    def test_a_relative_path_is_relative_to_the_project_root(self, tmp_path):
        (tmp_path / interp.CONFIG_FILENAME).write_text(".venv/bin/python\n")
        assert interp.configured_python(tmp_path) == (
            tmp_path / ".venv" / "bin" / "python")

    def test_the_first_non_empty_line_names_the_interpreter(self, tmp_path):
        (tmp_path / interp.CONFIG_FILENAME).write_text("\n  /usr/bin/python3  \n")
        assert interp.configured_python(tmp_path) == Path("/usr/bin/python3")

    def test_the_configured_interpreter_is_tried_before_the_venv(self, tmp_path):
        root = _project_with_venv(tmp_path)
        (root / interp.CONFIG_FILENAME).write_text("/usr/bin/python3\n")
        assert interp.project_pythons(root) == (
            Path("/usr/bin/python3"), root / ".venv" / "bin" / "python")

    def test_a_broken_start_hands_over_to_the_configured_interpreter(self, tmp_path):
        root = _project_with_venv(tmp_path)
        configured = _executable(tmp_path / "old-python", "#!/bin/sh\n")
        (root / interp.CONFIG_FILENAME).write_text(f"{configured}\n")
        venv = str(root / ".venv" / "bin" / "python")
        target = interp.relaunch_target(
            root, current="/opt/homebrew/bin/python3", env={},
            probe=_verdicts({str(configured): "ready", venv: "ready"}))
        assert target == configured

    def test_a_configured_interpreter_that_cannot_simulate_falls_through(
            self, tmp_path):
        root = _project_with_venv(tmp_path)
        configured = _executable(tmp_path / "old-python", "#!/bin/sh\n")
        (root / interp.CONFIG_FILENAME).write_text(f"{configured}\n")
        venv = str(root / ".venv" / "bin" / "python")
        assert interp.relaunch_target(
            root, current="/opt/homebrew/bin/python3", env={},
            probe=_verdicts({venv: "ready"})) == root / ".venv" / "bin" / "python"


class TestRelaunchIfNeeded:
    def _ready_venv(self, root):
        return _verdicts({str(root / ".venv" / "bin" / "python"): "ready"})

    def test_the_same_script_and_arguments_are_handed_to_the_venv(self, tmp_path):
        root = _project_with_venv(tmp_path)
        calls, said = [], []
        interp.relaunch_if_needed(
            root, root / "serve.py", ["--port", "8765"],
            current="/opt/homebrew/bin/python3", env={"PATH": "/bin"},
            probe=self._ready_venv(root),
            execve=lambda *a: calls.append(a), out=said.append)
        venv_python = str(root / ".venv" / "bin" / "python")
        assert calls == [(venv_python,
                          [venv_python, str(root / "serve.py"), "--port", "8765"],
                          {"PATH": "/bin", interp.RELAUNCH_GUARD_ENV: "1"})]
        assert any(".venv" in line for line in said)

    def test_output_is_flushed_before_the_process_image_is_replaced(
            self, tmp_path, monkeypatch):
        """execve discards unflushed buffers: with stdout on a pipe (make,
        the desktop app) the hand-over notice vanished. Found end to end."""
        import io
        root = _project_with_venv(tmp_path)
        buffered = io.StringIO()
        flushed_at_exec = []
        monkeypatch.setattr(sys, "stdout", buffered)
        real_flush = buffered.flush
        state = {"flushed": False}

        def flush():
            state["flushed"] = True
            real_flush()

        buffered.flush = flush
        interp.relaunch_if_needed(
            root, root / "serve.py", [], current="/usr/bin/python3", env={},
            probe=self._ready_venv(root),
            execve=lambda *a: flushed_at_exec.append(state["flushed"]))
        assert flushed_at_exec == [True]
        assert ".venv" in buffered.getvalue()

    def test_a_failed_exec_keeps_serving_on_the_current_interpreter(self, tmp_path):
        root = _project_with_venv(tmp_path)
        said = []

        def refuse(*_):
            raise PermissionError(13, "Permission denied")

        interp.relaunch_if_needed(
            root, root / "serve.py", [], current="/usr/bin/python3", env={},
            probe=self._ready_venv(root), execve=refuse, out=said.append)
        assert any("Permission denied" in line for line in said)

    def test_nothing_happens_without_a_target(self, tmp_path):
        calls = []
        interp.relaunch_if_needed(
            tmp_path, tmp_path / "serve.py", [], current="/usr/bin/python3",
            env={}, probe=_verdicts({}), execve=lambda *a: calls.append(a),
            out=lambda _line: None)
        assert calls == []


# ── Child processes never emit colour escapes into captured output ──────────

class TestChildEnv:
    def test_python_colours_are_switched_off_without_touching_the_input(self):
        inherited = {"FORCE_COLOR": "1", "PATH": "/bin"}
        env = interp.child_env(inherited)
        # PYTHON_COLORS outranks NO_COLOR and FORCE_COLOR (Python docs, 3.13+).
        assert env["PYTHON_COLORS"] == "0"
        assert env["PATH"] == "/bin"
        assert "PYTHON_COLORS" not in inherited

    def test_ansi_sequences_are_stripped(self):
        coloured = ("\x1b[1;35mModuleNotFoundError\x1b[0m: "
                    "\x1b[35mNo module named 'pandas'\x1b[0m")
        assert (interp.strip_ansi(coloured)
                == "ModuleNotFoundError: No module named 'pandas'")


# ── What a failed job tells the user ────────────────────────────────────────

class TestJobFailureMessage:
    def test_a_missing_package_names_itself_and_the_fix(self):
        stderr = ("Traceback (most recent call last):\n"
                  "  File \"build_sumo_demand.py\", line 3, in <module>\n"
                  "\x1b[1;35mModuleNotFoundError\x1b[0m: "
                  "\x1b[35mNo module named 'pandas'\x1b[0m\n")
        message = interp.job_failure_message(stderr, "fallback")
        assert "\x1b" not in message
        assert "'pandas'" in message
        assert interp.SETUP_COMMAND in message

    def test_a_short_last_line_is_shown_without_escapes(self):
        stderr = "warming up\n\x1b[31mValueError: datumet saknas\x1b[0m\n"
        assert (interp.job_failure_message(stderr, "fallback")
                == "ValueError: datumet saknas")

    def test_empty_stderr_falls_back(self):
        assert interp.job_failure_message("  \n", "fallback") == "fallback"

    def test_an_overlong_last_line_falls_back(self):
        assert interp.job_failure_message("x" * 500, "fallback") == "fallback"


# ── Readiness: can this interpreter run every job? ──────────────────────────

class TestProbeReadiness:
    def _probe(self, tmp_path, modules, *, sumo_home=None,
               python=sys.executable, timeout=60.0, lock=None):
        env = {"PATH": os.environ.get("PATH", ""),
               "PYTHONPATH": str(tmp_path / "mods"),
               "SUMO_HOME": str(sumo_home or _fake_sumo_home(tmp_path))}
        return interp.probe_readiness(
            python, REPO, modules=modules, env=env, timeout=timeout,
            lock=lock)

    def test_everything_importable_with_sumo_present_is_ready(self, tmp_path):
        readiness = self._probe(tmp_path, ("json", "sqlite3"))
        assert readiness.status == "ready", readiness
        assert readiness.ready
        assert readiness.version.startswith(f"{THIS_PYTHON[0]}.{THIS_PYTHON[1]}")

    def test_a_missing_package_makes_simulation_unavailable(self, tmp_path):
        readiness = self._probe(tmp_path, ("json", "definitely_missing_pkg_xyz"))
        assert readiness.status == "unavailable"
        assert readiness.missing == ("definitely_missing_pkg_xyz",)
        assert "definitely_missing_pkg_xyz" in readiness.message()
        assert interp.SETUP_COMMAND in readiness.message()

    def test_an_import_that_fails_for_another_reason_is_reported(self, tmp_path):
        """lightgbm without libomp raises OSError, not ModuleNotFoundError."""
        _executable(tmp_path / "mods" / "broken_native.py",
                    "raise OSError('Library not loaded: libomp.dylib')\n")
        readiness = self._probe(tmp_path, ("json", "broken_native"))
        assert readiness.status == "unavailable"
        assert any("broken_native" in p and "libomp" in p
                   for p in readiness.problems)

    def test_a_missing_sumo_binary_makes_simulation_unavailable(self, tmp_path):
        home = _fake_sumo_home(tmp_path, binaries=("sumo", "netconvert"))
        readiness = self._probe(tmp_path, ("json",), sumo_home=home)
        assert readiness.status == "unavailable"
        assert any("duarouter" in p for p in readiness.problems)

    def test_a_sumo_binary_that_exists_but_cannot_run_is_not_ready(self, tmp_path):
        """Regression 2026-10-10: sumo-data 1.28 beside sumo 1.27.1 made the
        binary abort in dyld while the file was present and executable."""
        home = _fake_sumo_home(tmp_path)
        _executable(home / "bin" / "sumo", "#!/bin/sh\n"
                    "echo 'dyld: Library not loaded: libproj.25.dylib' >&2\n"
                    "exit 134\n")
        readiness = self._probe(tmp_path, ("json",), sumo_home=home)
        assert readiness.status == "unavailable"
        assert any("sumo" in p and "libproj" in p for p in readiness.problems)

    def test_a_sumo_version_other_than_the_locked_one_is_not_ready(self, tmp_path):
        """SUMO 1.28.0 breaks demand calibration; the lock names 1.27.1."""
        lock = _lock(tmp_path, {"eclipse-sumo": "1.27.1"})
        home = _fake_sumo_home(tmp_path, version="1.28.0")
        readiness = self._probe(tmp_path, ("json",), sumo_home=home, lock=lock)
        assert readiness.status == "unavailable"
        assert any("1.28.0" in p and "1.27.1" in p for p in readiness.problems)

    def test_an_environment_that_matches_the_lock_is_ready(self, tmp_path):
        lock = _lock(tmp_path, {"pytest": _installed("pytest"),
                                "eclipse-sumo": "1.27.1"})
        readiness = self._probe(tmp_path, ("json",), lock=lock)
        # eclipse-sumo is only compared when it is installed as a package in
        # this interpreter; the fake SUMO home carries the binary version.
        if readiness.status != "ready":
            assert all("eclipse-sumo" in p for p in readiness.problems), readiness
        else:
            assert readiness.ready

    def test_a_package_version_other_than_the_locked_one_is_not_ready(
            self, tmp_path):
        lock = _lock(tmp_path, {"pytest": "0.0.1"})
        readiness = self._probe(tmp_path, ("json",), lock=lock)
        assert readiness.status == "unavailable"
        assert any("pytest" in p and "0.0.1" in p for p in readiness.problems)
        assert interp.SETUP_COMMAND in readiness.message()

    def test_many_deviations_are_summarised_not_listed(self, tmp_path):
        """An old environment can differ in dozens of packages; the banner
        must stay readable and still name examples and the fix."""
        pins = {f"definitely-missing-dist-{i}": "1.0" for i in range(12)}
        readiness = self._probe(tmp_path, ("json",), lock=_lock(tmp_path, pins))
        assert readiness.status == "unavailable"
        lock_problems = [p for p in readiness.problems if "requirements.lock" in p]
        assert len(lock_problems) == 1
        assert "12 paket" in lock_problems[0]
        assert len(readiness.message()) < 600

    def test_a_locked_package_that_is_not_installed_is_not_ready(self, tmp_path):
        lock = _lock(tmp_path, {"definitely-missing-dist-xyz": "1.0"})
        readiness = self._probe(tmp_path, ("json",), lock=lock)
        assert readiness.status == "unavailable"
        assert any("definitely-missing-dist-xyz" in p for p in readiness.problems)

    def test_a_missing_lock_file_is_not_ready(self, tmp_path):
        readiness = self._probe(tmp_path, ("json",),
                                lock=tmp_path / "requirements.lock")
        assert readiness.status == "unavailable"
        assert any("requirements.lock" in p for p in readiness.problems)

    def test_the_version_is_reported_but_never_decides(self, tmp_path):
        """Regression 2026-10-10: the project's long-standing working
        environment is Python 3.9. A version rule declared it unusable;
        only what the jobs actually import (and SUMO) decides."""
        readiness = self._probe(tmp_path, ("json",))
        assert readiness.status == "ready"
        assert readiness.version == "%d.%d.%d" % sys.version_info[:3]

    def test_a_python_that_does_not_exist_is_unavailable(self, tmp_path):
        readiness = self._probe(tmp_path, ("json",),
                                python=tmp_path / "no-such-python")
        assert readiness.status == "unavailable"

    def test_a_probe_that_hangs_is_inconclusive_not_a_verdict(self, tmp_path):
        slow = _executable(tmp_path / "slow-python", "#!/bin/sh\nsleep 30\n")
        readiness = self._probe(tmp_path, ("json",), python=slow, timeout=0.5)
        assert readiness.status == "unknown"
        assert not readiness.ready

    def test_public_form_carries_what_the_ui_needs(self, tmp_path):
        public = self._probe(tmp_path, ("definitely_missing_pkg_xyz",)).to_public()
        assert public["status"] == "unavailable"
        assert public["missing"] == ["definitely_missing_pkg_xyz"]
        assert public["fix"] == interp.SETUP_COMMAND
        assert "\x1b" not in public["message"]


# ── The monitor the server consults before admitting a job ──────────────────

def _readiness(status, missing=()):
    return interp.Readiness(status=status, python="/x/python", version="3.12.0",
                            missing=tuple(missing),
                            problems=tuple(f"saknar {m}" for m in missing))


class TestReadinessMonitor:
    def test_before_any_probe_the_state_is_checking(self):
        monitor = interp.ReadinessMonitor(lambda: _readiness("ready"))
        assert monitor.current().status == "checking"

    def test_admission_runs_the_first_probe_when_none_has_run(self):
        monitor = interp.ReadinessMonitor(lambda: _readiness("ready"))
        assert monitor.for_admission(timeout=10).status == "ready"

    def test_an_unavailable_verdict_is_rechecked_at_admission(self):
        """Installing the packages must not require a server restart."""
        verdicts = iter([_readiness("unavailable", ["pandas"]),
                         _readiness("ready")])
        monitor = interp.ReadinessMonitor(lambda: next(verdicts))
        monitor.start()
        monitor.wait(timeout=10)
        assert monitor.current().status == "unavailable"
        assert monitor.for_admission(timeout=10).status == "ready"
        assert monitor.current().status == "ready"

    def test_a_crashing_probe_is_inconclusive(self):
        def boom():
            raise RuntimeError("probe exploded")
        monitor = interp.ReadinessMonitor(boom)
        assert monitor.for_admission(timeout=10).status == "unknown"

    def test_an_inconclusive_verdict_is_rechecked_at_admission(self):
        """An incomplete check is never taken as an answer: admission asks
        again rather than trusting or ignoring "unknown"."""
        verdicts = iter([_readiness("unknown"), _readiness("ready")])
        monitor = interp.ReadinessMonitor(lambda: next(verdicts))
        monitor.start()
        monitor.wait(timeout=10)
        assert monitor.current().status == "unknown"
        assert monitor.for_admission(timeout=10).status == "ready"

    def test_a_check_that_never_finishes_is_not_an_answer(self):
        import threading
        gate = threading.Event()
        monitor = interp.ReadinessMonitor(lambda: gate.wait(5) and _readiness("ready"))
        monitor.start()
        try:
            assert monitor.for_admission(timeout=0.2).status == "checking"
        finally:
            gate.set()

    def test_the_background_verdict_is_handed_to_the_caller(self):
        """The terminal shows the verdict without anyone polling for it."""
        seen = []
        monitor = interp.ReadinessMonitor(lambda: _readiness("ready"))
        monitor.start(on_result=seen.append)
        assert monitor.wait(timeout=10)
        assert [r.status for r in seen] == ["ready"]

    def test_a_ready_verdict_goes_stale_when_its_inputs_change(self):
        """Review 2026-10-10 (P2): the server kept saying "ready" after an
        incompatible lock change. A verdict is only as current as its inputs."""
        import time
        state = {"lock": "A"}
        monitor = interp.ReadinessMonitor(
            lambda: _readiness("ready" if state["lock"] == "A" else "unavailable",
                               [] if state["lock"] == "A" else ["pandas"]),
            inputs=lambda: state["lock"])
        assert monitor.for_admission(timeout=10).status == "ready"
        state["lock"] = "B"
        # Ping must not keep reporting the old verdict...
        assert monitor.current().status == "checking"
        # ...and a job must be judged against the new inputs, synchronously.
        assert monitor.for_admission(timeout=10).status == "unavailable"
        deadline = time.time() + 10
        while monitor.current().status == "checking" and time.time() < deadline:
            time.sleep(0.02)
        assert monitor.current().status == "unavailable"

    def test_a_stale_verdict_is_refreshed_in_the_background_once(self):
        import threading, time
        calls = []
        gate = threading.Event()
        state = {"lock": "A"}

        def probe():
            calls.append(state["lock"])
            if state["lock"] == "B":
                gate.wait(5)
            return _readiness("ready")

        monitor = interp.ReadinessMonitor(probe, inputs=lambda: state["lock"])
        monitor.for_admission(timeout=10)
        state["lock"] = "B"
        for _ in range(5):                       # many pings, one refresh
            assert monitor.current().status == "checking"
        gate.set()
        deadline = time.time() + 10
        while monitor.current().status == "checking" and time.time() < deadline:
            time.sleep(0.02)
        assert calls == ["A", "B"]
        assert monitor.current().status == "ready"

    def test_readiness_inputs_follow_the_lock_and_the_installed_packages(
            self, tmp_path):
        site = tmp_path / "site-packages"
        site.mkdir()
        (tmp_path / "requirements.lock").write_text("pandas==3.0.6\n")
        first = interp.readiness_inputs(tmp_path, site_dirs=(site,))
        assert interp.readiness_inputs(tmp_path, site_dirs=(site,)) == first
        (tmp_path / "requirements.lock").write_text("pandas==3.0.7\n")
        second = interp.readiness_inputs(tmp_path, site_dirs=(site,))
        assert second != first
        import os, time
        later = time.time_ns() + 5_000_000_000
        (site / "pandas-3.0.7.dist-info").mkdir()
        os.utime(site, ns=(later, later))
        assert interp.readiness_inputs(tmp_path, site_dirs=(site,)) != second

    def test_a_settled_monitor_never_probes(self):
        monitor = interp.ReadinessMonitor.settled(_readiness("ready"))
        monitor.start()
        assert monitor.current().status == "ready"
        assert monitor.for_admission(timeout=1).status == "ready"

    @pytest.mark.parametrize("prior", [None, "ready", "unavailable", "unknown"])
    def test_admission_refuses_inputs_changed_during_the_probe(self, tmp_path, prior):
        """A result for the old lock must never admit work on the new lock."""
        lock = _lock(tmp_path, {"pandas": "3.0.6"})
        changing = False

        def probe():
            if changing:
                lock.write_text("pandas==0.0.1\n", encoding="utf-8")
            return _readiness(prior if prior and not changing else "ready")

        monitor = interp.ReadinessMonitor(
            probe, inputs=lambda: interp.readiness_inputs(tmp_path, site_dirs=()))
        if prior is not None:
            monitor.for_admission(timeout=1)
        if prior == "ready":
            lock.write_text("pandas==3.0.5\n", encoding="utf-8")
        changing = True
        assert not monitor.for_admission(timeout=1).ready

    def test_restoring_inputs_after_an_invalidated_probe_rechecks(self, tmp_path):
        lock = _lock(tmp_path, {"pandas": "3.0.6"})
        original = lock.read_bytes()
        calls = 0

        def probe():
            nonlocal calls
            calls += 1
            if calls == 1:
                lock.write_text("pandas==0.0.1\n", encoding="utf-8")
            return _readiness("ready")

        monitor = interp.ReadinessMonitor(
            probe, inputs=lambda: interp.readiness_inputs(tmp_path, site_dirs=()))
        assert monitor.for_admission(timeout=1).status == "checking"
        lock.write_bytes(original)
        assert monitor.for_admission(timeout=1).ready
        assert calls == 2


# ── Drift guards: the probe asks exactly what the jobs need ─────────────────

def test_every_job_serve_launches_is_probed():
    source = (REPO / "serve.py").read_text(encoding="utf-8")
    launched = set(re.findall(r'sys\.executable,\s*"(\w+)\.py"', source))
    assert launched, "serve.py no longer launches jobs via sys.executable?"
    assert launched <= set(interp.JOB_ENTRY_MODULES)


def test_the_in_process_simulation_imports_are_probed():
    source = (REPO / "serve.py").read_text(encoding="utf-8")
    assert "from traffic_sim.analysis.closure_delay_run import" in source
    assert "traffic_sim.analysis.closure_delay_run" in interp.JOB_ENTRY_MODULES
    assert "from signal_optimize import" in source
    assert "signal_optimize" in interp.JOB_ENTRY_MODULES


def test_every_probed_module_exists():
    for module in interp.JOB_ENTRY_MODULES:
        path = REPO / (module.replace(".", "/") + ".py")
        assert path.is_file(), module


def test_sumo_binaries_match_what_jobs_execute():
    used = set()
    for name in ("run_scenario.py", "build_sumo_demand.py", "signal_optimize.py"):
        used |= set(re.findall(r'"bin" / "(\w+)"',
                               (REPO / name).read_text(encoding="utf-8")))
    assert used == set(interp.SUMO_BINARIES)


def test_the_lock_python_is_supported_and_installed_from_the_lock_in_ci():
    assert interp.LOCK_PYTHON in interp.SUPPORTED_PYTHON
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "requirements.lock" in ci


def test_supported_versions_agree_with_ci_and_the_wsl_launcher():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    matrix = re.search(r'python-version: \[([^\]]+)\]', ci).group(1)
    ci_versions = {tuple(int(p) for p in v.strip(' "').split("."))
                   for v in matrix.split(",")}
    assert ci_versions == set(interp.SUPPORTED_PYTHON)


def test_the_wsl_launcher_installs_the_same_lock_with_the_lock_python():
    """The readiness check requires the lock, so a launcher that resolved
    requirements.txt itself would build an environment that is never ready."""
    wsl = (REPO / "start-wsl.sh").read_text(encoding="utf-8")
    assert ("sys.version_info[:2] == %r" % (interp.LOCK_PYTHON,)) in wsl
    assert "pip install --no-deps -r requirements.lock" in wsl
    assert "pip check" in wsl
    assert "sha256sum requirements.lock" in wsl
    assert "-r requirements.txt" not in wsl
