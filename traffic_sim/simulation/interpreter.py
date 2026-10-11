"""Which Python runs the simulation jobs, and whether it actually can.

THE FAILURE THIS EXISTS FOR (2026-10-10).  The server was started on
Homebrew's ``python3`` (3.13: no pandas, no SUMO).  serve.py launches every
job with ``sys.executable``, so every job inherited that interpreter;
``/api/ping`` answered "yes" because a server was listening, so the UI offered
"Byt dag"; and the job died at its first import.  The banner then showed the
raw traceback line with ANSI colour codes, because the desktop app exported
``FORCE_COLOR=1`` and Python 3.13 colours tracebacks even on a pipe when it is
set.  Nothing was wrong with the code that failed; the question "can THIS
interpreter simulate?" was simply never asked.

THE DESIGN, IN THREE LAYERS.

1. Pick the right interpreter.  ``relaunch_if_needed`` hands a server over to
   the project's ``.venv`` (one ``execve``, same PID, same arguments) ONLY
   when the interpreter it was started on cannot simulate and the venv can.
   An interpreter that works is never replaced: on the project owner's Mac
   the working environment was ``/usr/bin/python3`` 3.9 with SUMO 1.27.1,
   and every demand cache binds that exact runtime, so swapping a working
   interpreter for another would silently throw the caches away.  When
   neither can simulate it stays put: the static viewer runs on any Python.
2. Ask the question before the user waits.  ``probe_readiness`` runs the
   candidate interpreter in a child process and imports exactly the modules
   the jobs import (``JOB_ENTRY_MODULES``, pinned to serve.py by drift tests)
   and checks the SUMO binaries they execute.  The Python version is
   reported but never decides: a version rule once declared the working
   3.9 environment unusable, while the imports answer the real question
   (numba==0.60.0 on 3.13, for instance, fails its import).  The probe never imports the
   scientific stack into the server process itself, which must stay
   standard-library only (see ``TestStaticServingNeedsNoScientificStack``).
   ``ReadinessMonitor`` caches the verdict for ``/api/ping`` and re-checks an
   "unavailable" verdict at admission, so installing the packages takes
   effect without a restart.
3. Say it plainly when it still fails.  ``child_env`` switches Python's
   colours off in every child (``PYTHON_COLORS=0`` outranks ``NO_COLOR`` and
   ``FORCE_COLOR``), ``strip_ansi`` removes what other tools still emit, and
   ``job_failure_message`` turns a missing module into the fix.

ONLY A COMPLETED "ready" ADMITS (changed 2026-10-10 at the project owner's
request).  "unavailable" is a measured fact (a package, binary or locked
version is wrong).  "unknown" means the check itself could not finish (a
timeout, a crash, no report) and "checking" that it has not finished yet;
neither is ever reported or treated as ready.  The monitor re-asks at
admission, so a slow first check or a fix made since costs one more probe,
never a wrong answer.

WHAT "ready" MEANS.  Every job module imports; the three SUMO binaries exist,
RUN (``--version`` exits 0 — a mismatched sumo-data once let the file exist
while the binary aborted in dyld) and report the version
``requirements.lock`` pins; and every package in ``requirements.lock`` is
installed at exactly its locked version.  The Python version is reported but
never decides on its own; the lock decides.

WHY ``make setup`` IS THE ADVICE, NOT ``pip install``.  Homebrew's Pythons
are marked externally managed (PEP 668) and refuse ``pip install`` outright,
and ``numba==0.60.0`` publishes no wheel for Python 3.13.  The only install
that works is a virtual environment built from ``requirements.lock`` with
Python 3.12, which ``tools/setup_venv.py`` (``make setup``) builds.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

#: Python versions a NEW environment is built with (`make setup`): the pinned
#: requirements install there (numba==0.60.0 has no 3.13 wheel) and CI tests
#: them; start-wsl.sh must agree (drift-tested). Readiness never uses this —
#: an existing interpreter is judged by its imports alone.
SUPPORTED_PYTHON: tuple[tuple[int, int], ...] = ((3, 11), (3, 12))

#: The Python version `requirements.lock` is resolved for. `make setup` builds
#: .venv with exactly this minor version; the lock does not install on 3.11
#: (contourpy==1.4.0 requires 3.12), so CI installs it only on its 3.12 job.
LOCK_PYTHON: tuple[int, int] = (3, 12)
LOCK_FILENAME = "requirements.lock"

#: Every module serve.py executes as a job or imports to serve one. The probe
#: imports exactly these, so it answers the question the jobs will ask.
JOB_ENTRY_MODULES: tuple[str, ...] = (
    "build_sumo_demand",
    "run_scenario",
    "suggest_closure_time",
    "signal_optimize",
    "signal_closure_combine",
    "run_monthly_closure_search",
    "traffic_sim.analysis.closure_delay_run",
)

#: SUMO programs the jobs execute: scenarios run sumo, demand builds run
#: duarouter, signal optimisation runs netconvert.
SUMO_BINARIES: tuple[str, ...] = ("sumo", "duarouter", "netconvert")

VENV_DIRNAME = ".venv"
#: Untracked, one line: the interpreter THIS machine simulates with, when it
#: is not the project's .venv (`make setup USE=/path/to/python` writes it).
CONFIG_FILENAME = ".simulation-python"
RELAUNCH_GUARD_ENV = "TRAFFIC_SIM_RELAUNCHED"
SETUP_COMMAND = "make setup"
FIX_SENTENCE = (f"Kör `{SETUP_COMMAND}` i projektmappen och starta sedan om "
                "servern.")

PROBE_TIMEOUT_S = 120.0
STARTUP_PROBE_TIMEOUT_S = 30.0
#: Per SUMO binary inside the probe: `--version` must answer within this.
BINARY_CHECK_TIMEOUT_S = 30.0
MAX_SHOWN_LINE = 200
MAX_PROBLEM_LENGTH = 240
MAX_LISTED_DEVIATIONS = 3

_REPORT_MARKER = "@@traffic-sim-readiness@@"

# ECMA-48 escape sequences: CSI (colours, cursor), OSC (titles, links) and the
# two-byte forms. Wider than colours on purpose; none of it belongs in a banner.
_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_MISSING_MODULE_RE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")


# ── Layer 1: which interpreter ──────────────────────────────────────────────

def venv_python(root: Path) -> Path:
    """The project's virtual-environment interpreter (it may not exist)."""
    return Path(root) / VENV_DIRNAME / "bin" / "python"


def configured_python(root: Path) -> Path | None:
    """The interpreter named in ``CONFIG_FILENAME``, if the file names one."""
    try:
        lines = (Path(root) / CONFIG_FILENAME).read_text(
            encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.strip():
            path = Path(line.strip()).expanduser()
            return path if path.is_absolute() else Path(root) / path
    return None


def project_pythons(root: Path) -> tuple[Path, ...]:
    """Where this project's simulation interpreter may live, in order."""
    configured = configured_python(root)
    return tuple(dict.fromkeys(
        path for path in (configured, venv_python(root)) if path is not None))


Probe = Callable[[str, Path], "Readiness"]


def _startup_probe(python: str, root: Path) -> "Readiness":
    return probe_readiness(python, root, timeout=STARTUP_PROBE_TIMEOUT_S)


def relaunch_target(root: Path, *, current: str | None = None,
                    env: Mapping[str, str] | None = None,
                    probe: Probe | None = None) -> Path | None:
    """The interpreter a freshly started server should hand over to, if any.

    Only a measured "unavailable" moves the server; "ready" and an
    inconclusive "unknown" both stay put.
    """
    environment = os.environ if env is None else env
    if environment.get(RELAUNCH_GUARD_ENV):
        return None
    judge = probe or _startup_probe
    here = current or sys.executable
    if judge(here, root).status != "unavailable":
        return None
    for candidate in project_pythons(root):
        # os.access is False for a dangling symlink, which is what removing
        # the Homebrew Python a venv was built from leaves behind.
        if str(candidate) == str(here) or not os.access(candidate, os.X_OK):
            continue
        if judge(str(candidate), root).ready:
            return candidate
    return None


def relaunch_if_needed(root: Path, script: Path, argv: Sequence[str], *,
                       current: str | None = None,
                       env: Mapping[str, str] | None = None,
                       probe: Probe | None = None,
                       execve: Callable[..., object] = os.execve,
                       out: Callable[[str], object] = print) -> None:
    """Hand this process over to the project's interpreter when it is needed.

    Returns when no hand-over happens (or the exec itself failed, which is
    reported and survived: the static viewer works on any interpreter).
    """
    environment = dict(os.environ if env is None else env)
    target = relaunch_target(root, current=current, env=environment,
                             probe=probe)
    if target is None:
        return
    out(f"{current or sys.executable} kan inte köra simuleringen — startar "
        f"om med projektets Python: {target}")
    # execve discards unflushed buffers; on a pipe (make, the desktop app)
    # stdout is block-buffered, so the notice above would silently vanish.
    # (Either stream is None when the process was started detached.)
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.flush()
    try:
        execve(str(target), [str(target), str(script), *argv],
               {**environment, RELAUNCH_GUARD_ENV: "1"})
    except OSError as exc:
        out(f"Kunde inte byta till {target} ({exc}); fortsätter med "
            f"{sys.executable}. Simuleringen kontrolleras separat.")


def parse_lock(path: Path) -> dict[str, str]:
    """``name==version`` lines of a lock file; comments and blanks ignored."""
    pins: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, version = line.partition("==")
        if not sep or not name.strip() or not version.strip():
            raise ValueError(f"{path}: not an exact pin: {raw!r}")
        pins[name.strip()] = version.strip()
    return pins


# ── Layer 3: clean child output ─────────────────────────────────────────────

def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of ``env`` (default: this process) with Python colours off."""
    return {**(os.environ if env is None else env), "PYTHON_COLORS": "0"}


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def missing_packages_message(packages: Sequence[str]) -> str:
    names = ", ".join(repr(name) for name in packages)
    return f"Simuleringsmiljön saknar Python-paketet {names}. {FIX_SENTENCE}"


def _last_line(stderr: str) -> str:
    lines = [line.strip() for line in strip_ansi(stderr).splitlines()
             if line.strip()]
    return lines[-1] if lines else ""


def missing_module_fix(stderr: str) -> str | None:
    """The fix, if a job died because a Python package is missing."""
    missing = _MISSING_MODULE_RE.search(_last_line(stderr))
    if missing is None:
        return None
    return missing_packages_message((missing.group(1).split(".")[0],))


def job_failure_message(stderr: str, fallback: str, *,
                        max_length: int = MAX_SHOWN_LINE) -> str:
    """One readable line for the UI out of a failed job's stderr."""
    fix = missing_module_fix(stderr)
    if fix is not None:
        return fix
    last = _last_line(stderr)
    return last if last and len(last) < max_length else fallback


# ── Layer 2: readiness ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Readiness:
    """Verdict on one interpreter: checking | ready | unavailable | unknown."""

    status: str
    python: str = ""
    version: str = ""
    missing: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def message(self) -> str:
        if self.status == "ready":
            return f"Simuleringen är redo (Python {self.version})."
        if self.status == "checking":
            return "Kontrollerar simuleringsmiljön…"
        detail = "; ".join(self.problems) or "okänd orsak"
        if self.status == "unknown":
            return (f"Simuleringsmiljön kunde inte kontrolleras färdigt "
                    f"({detail}), så den räknas inte som redo. Försök igen om "
                    f"en stund; kvarstår felet, kör `{SETUP_COMMAND}`.")
        where = f" med {self.python}" if self.python else ""
        return f"Simuleringen kan inte köras{where}: {detail}. {FIX_SENTENCE}"

    def to_public(self) -> dict:
        return {"status": self.status, "python": self.python,
                "version": self.version, "missing": list(self.missing),
                "problems": list(self.problems), "message": self.message(),
                "fix": SETUP_COMMAND}


CHECKING = Readiness(status="checking")

# Runs INSIDE the interpreter being judged. Kept free of syntax newer than
# Python 3.6 so an old interpreter still reports its version instead of
# failing to parse the probe.
_PROBE_SCRIPT = r"""
import importlib, json, os, subprocess, sys
root, binaries, pins = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
timeout, modules = float(sys.argv[4]), sys.argv[5:]
sys.path.insert(0, root)
report = {"version": "%d.%d.%d" % tuple(sys.version_info[:3]),
          "missing": [], "import_errors": {}, "sumo_problem": None,
          "missing_binaries": [], "binaries": {}, "installed": {}}
try:
    from importlib import metadata
except ImportError:
    metadata = None
for dist in pins:
    try:
        report["installed"][dist] = metadata.version(dist) if metadata else None
    except Exception:
        report["installed"][dist] = None
for name in modules:
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as exc:
        top = (exc.name or name).split(".")[0]
        if top not in report["missing"]:
            report["missing"].append(top)
    except BaseException as exc:
        report["import_errors"][name] = "%s: %s" % (type(exc).__name__, exc)
try:
    from traffic_sim.simulation.runtime import sumo_home
    home = str(sumo_home())
    for binary in binaries:
        path = os.path.join(home, "bin", binary)
        if not os.access(path, os.X_OK):
            report["missing_binaries"].append(binary)
            continue
        # Existing is not running: a mismatched sumo-data once made the
        # binary abort in dyld while the file was present and executable.
        try:
            done = subprocess.run([path, "--version"], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  universal_newlines=True, timeout=timeout)
            lines = (done.stdout or "").strip().splitlines()
            report["binaries"][binary] = {
                "returncode": done.returncode,
                "first_line": lines[0] if lines else "",
                "stderr_tail": (done.stderr or "").strip()[-300:]}
        except BaseException as exc:
            report["binaries"][binary] = {
                "returncode": None, "first_line": "",
                "stderr_tail": "%s: %s" % (type(exc).__name__, exc)}
except BaseException as exc:
    report["sumo_problem"] = "%s: %s" % (type(exc).__name__, exc)
sys.stdout.write("\n" + MARKER + json.dumps(report) + "\n")
""".replace("MARKER", repr(_REPORT_MARKER))


def _parse_report(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_REPORT_MARKER):
            try:
                return json.loads(line[len(_REPORT_MARKER):])
            except ValueError:
                return None
    return None


def _clip(text: str) -> str:
    text = strip_ansi(text).strip()
    return text if len(text) <= MAX_PROBLEM_LENGTH else (
        text[:MAX_PROBLEM_LENGTH - 1] + "…")


def _binary_version(first_line: str) -> str:
    """'Eclipse SUMO sumo 1.27.1' -> '1.27.1' (the last dotted token)."""
    tokens = [t for t in first_line.split() if t[:1].isdigit() and "." in t]
    return tokens[-1] if tokens else ""


def _verdict(python: str, report: dict, pins: Mapping[str, str] | None,
             lock_problem: str | None = None) -> Readiness:
    version = str(report.get("version", ""))
    problems: list[str] = []
    if lock_problem:
        problems.append(lock_problem)
    installed = report.get("installed") or {}
    deviations = []
    for dist, wanted in sorted((pins or {}).items()):
        have = installed.get(dist)
        if have is None:
            deviations.append(f"{dist}=={wanted} enligt {LOCK_FILENAME} är "
                              "inte installerat")
        elif have != wanted:
            deviations.append(f"{dist} {have} installerat, {LOCK_FILENAME} "
                              f"kräver {wanted}")
    if len(deviations) > MAX_LISTED_DEVIATIONS:
        examples = "; ".join(deviations[:MAX_LISTED_DEVIATIONS])
        problems.append(f"{len(deviations)} paket avviker från {LOCK_FILENAME} "
                        f"(t.ex. {examples})")
    else:
        problems.extend(deviations)
    wanted_sumo = (pins or {}).get("eclipse-sumo")
    for binary, result in sorted((report.get("binaries") or {}).items()):
        if result.get("returncode") != 0:
            reason = (result.get("stderr_tail")
                      or f"avslutades med kod {result.get('returncode')}")
            problems.append(_clip(f"SUMO-programmet {binary} startar inte "
                                  f"({reason})"))
            continue
        found = _binary_version(str(result.get("first_line", "")))
        if not found:
            problems.append(f"SUMO-programmet {binary} angav ingen version")
        elif wanted_sumo and found != wanted_sumo:
            problems.append(f"SUMO-programmet {binary} är {found}, "
                            f"{LOCK_FILENAME} kräver {wanted_sumo}")
    missing = tuple(report.get("missing") or ())
    if missing:
        problems.append("saknade Python-paket: " + ", ".join(missing))
    for module, error in sorted((report.get("import_errors") or {}).items()):
        problems.append(_clip(f"{module} kan inte importeras: {error}"))
    if report.get("sumo_problem"):
        problems.append(_clip(f"SUMO hittades inte: {report['sumo_problem']}"))
    if report.get("missing_binaries"):
        problems.append("SUMO-program saknas: "
                        + ", ".join(report["missing_binaries"]))
    return Readiness(status="unavailable" if problems else "ready",
                     python=python, version=version, missing=missing,
                     problems=tuple(problems))


_LOCK_FROM_ROOT = object()


def probe_readiness(python: str | Path, root: Path, *,
                    modules: Sequence[str] = JOB_ENTRY_MODULES,
                    env: Mapping[str, str] | None = None,
                    timeout: float = PROBE_TIMEOUT_S,
                    lock: Path | None | object = _LOCK_FROM_ROOT) -> Readiness:
    """Judge ``python`` by importing what the jobs import, in a child.

    ``lock`` (default: ``root/requirements.lock``) names the exact package
    set the environment must match; ``None`` skips that comparison.
    """
    python = str(python)
    lock_path = Path(root) / LOCK_FILENAME if lock is _LOCK_FROM_ROOT else lock
    pins: dict[str, str] = {}
    lock_problem = None
    if lock_path is not None:
        try:
            pins = parse_lock(Path(lock_path))
        except (OSError, ValueError) as exc:
            lock_problem = _clip(f"{LOCK_FILENAME} kan inte läsas: {exc}")
    cmd = [python, "-c", _PROBE_SCRIPT, str(root), json.dumps(SUMO_BINARIES),
           json.dumps(pins), str(BINARY_CHECK_TIMEOUT_S), *modules]
    try:
        res = subprocess.run(cmd, cwd=str(root), env=child_env(env),
                             capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Readiness("unknown", python=python, problems=(
            f"kontrollen svarade inte inom {timeout:g} s",))
    except OSError as exc:
        return Readiness("unavailable", python=python, problems=(
            f"Python kunde inte startas ({exc.strerror or exc})",))
    report = _parse_report(res.stdout)
    if report is None:
        reason = job_failure_message(res.stderr,
                                     f"avslutades med kod {res.returncode}")
        return Readiness("unknown", python=python, problems=(
            _clip(f"kontrollen gav inget svar: {reason}"),))
    return _verdict(python, report, pins, lock_problem)


def _site_dirs() -> tuple[Path, ...]:
    import sysconfig                                   # noqa: PLC0415
    paths = sysconfig.get_paths()
    return tuple(dict.fromkeys(Path(paths[key]) for key in ("purelib", "platlib")))


def readiness_inputs(root: Path, *,
                     site_dirs: Sequence[Path] | None = None) -> str:
    """Fingerprint of what a readiness verdict depends on that can change
    while the server runs: the lock's bytes, and the installed packages (a
    site-packages directory's timestamp moves on every install, upgrade and
    removal). Cheap enough to compute on every ping."""
    digest = hashlib.sha256()
    try:
        digest.update((Path(root) / LOCK_FILENAME).read_bytes())
    except OSError:
        digest.update(b"<no lock>")
    for directory in (_site_dirs() if site_dirs is None else site_dirs):
        try:
            stamp = str(os.stat(directory).st_mtime_ns)
        except OSError:
            stamp = "missing"
        digest.update(f"\0{directory}\0{stamp}".encode())
    return digest.hexdigest()


class ReadinessMonitor:
    """The cached verdict the server shows and consults before a job.

    One probe runs at a time. ``for_admission`` waits for an in-flight probe,
    runs the first one itself if none has started, and re-runs an
    "unavailable" verdict so a fix takes effect without a restart.

    ``inputs`` (optional) fingerprints what the verdict depends on. A verdict
    whose inputs have changed since its probe began is stale (review
    2026-10-10: "ready" survived an incompatible lock change): ``current``
    then reports "checking" and refreshes in the background, and
    ``for_admission`` re-probes before answering.
    """

    def __init__(self, probe: Callable[[], Readiness],
                 inputs: Callable[[], str] | None = None):
        self._probe = probe
        self._inputs = inputs
        self._state_lock = threading.Lock()
        self._probe_lock = threading.Lock()
        self._finished = threading.Event()
        self._result: Readiness | None = None
        self._result_inputs: str | None = None
        self._thread: threading.Thread | None = None
        self._refresh: threading.Thread | None = None

    @classmethod
    def settled(cls, readiness: Readiness) -> "ReadinessMonitor":
        """A monitor that already knows its answer (and keeps giving it)."""
        monitor = cls(lambda: readiness)
        monitor._result = readiness
        monitor._finished.set()
        return monitor

    def _snapshot(self) -> str | None:
        return self._inputs() if self._inputs is not None else None

    def _run(self, on_result: Callable[[Readiness], object] | None = None
             ) -> Readiness:
        with self._probe_lock:
            # Taken BEFORE probing: a change during the probe leaves the
            # verdict stale rather than silently attributed to new inputs.
            snapshot = self._snapshot()
            try:
                result = self._probe()
            except Exception as exc:              # noqa: BLE001 — reported
                result = Readiness("unknown", problems=(
                    _clip(f"kontrollen kraschade: {type(exc).__name__}: {exc}"),))
            # A probe can complete successfully for inputs that changed
            # while it ran. Never admit work using that obsolete result.
            if self._snapshot() != snapshot:
                result = CHECKING
                # Keep it stale even if the original inputs are restored.
                snapshot = None
            with self._state_lock:
                self._result = result
                self._result_inputs = snapshot
            try:
                # Before signalling: whoever waits sees the report done too.
                if on_result is not None:
                    on_result(result)
            finally:
                self._finished.set()
            return result

    def start(self,
              on_result: Callable[[Readiness], object] | None = None) -> None:
        """Probe in the background; ``on_result`` receives the verdict."""
        with self._state_lock:
            if self._result is not None or self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, args=(on_result,),
                name="simulation-readiness", daemon=True)
            self._thread.start()

    def wait(self, timeout: float) -> bool:
        return self._finished.wait(timeout)

    def _stale(self) -> bool:
        if self._inputs is None:
            return False
        with self._state_lock:
            if self._result is None:
                return False
            recorded = self._result_inputs
        return self._snapshot() != recorded

    def _refresh_in_background(self) -> None:
        with self._state_lock:
            if self._refresh is not None and self._refresh.is_alive():
                return
            self._refresh = threading.Thread(
                target=self._run, name="simulation-readiness-refresh",
                daemon=True)
            self._refresh.start()

    def current(self) -> Readiness:
        if self._stale():
            self._refresh_in_background()
            return CHECKING
        with self._state_lock:
            return self._result or CHECKING

    def for_admission(self, timeout: float = PROBE_TIMEOUT_S) -> Readiness:
        with self._state_lock:
            started = self._thread is not None or self._result is not None
        if not started:
            return self._run()
        if not self._finished.wait(timeout):
            return CHECKING             # an unfinished check is not an answer
        if self._stale():
            return self._run()          # judged against the inputs as they are now
        with self._state_lock:
            verdict = self._result or CHECKING
        # Re-ask rather than trust a negative or an incomplete verdict: a fix
        # takes effect without a restart, and "unknown" never passes as ready.
        if verdict.status in ("unavailable", "unknown"):
            return self._run()
        return verdict
