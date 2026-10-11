"""`make setup` and the launch paths all land on an interpreter that can simulate.

Regression for 2026-10-10: `make serve`, start.command and every Makefile
recipe ran bare `python3` (Homebrew 3.13 on the project owner's Mac). That
interpreter refuses `pip install` (PEP 668) and has no numba==0.60.0 wheel,
so the only working setup is a 3.11/3.12 virtual environment.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tools import setup_venv
from traffic_sim.simulation import interpreter

REPO = Path(__file__).resolve().parent.parent
THIS = sys.version_info[:2]


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# ── Choosing the base interpreter ───────────────────────────────────────────

class TestFindSupportedPython:
    def test_the_first_supported_candidate_wins(self):
        paths = {"python3.12": "/opt/homebrew/bin/python3.12",
                 "python3.11": "/usr/local/bin/python3.11",
                 "python3": "/opt/homebrew/bin/python3"}
        versions = {"/opt/homebrew/bin/python3.12": (3, 12),
                    "/usr/local/bin/python3.11": (3, 11),
                    "/opt/homebrew/bin/python3": (3, 13)}
        assert setup_venv.find_supported_python(
            which=paths.get, version_of=versions.get) == "/opt/homebrew/bin/python3.12"

    def test_an_unsupported_python3_is_never_chosen(self):
        """Homebrew's python3 is 3.13 here: numba==0.60.0 cannot install."""
        assert setup_venv.find_supported_python(
            which={"python3": "/opt/homebrew/bin/python3"}.get,
            version_of=lambda _p: (3, 13)) is None

    def test_interpreter_version_reads_a_real_interpreter(self):
        assert setup_venv.interpreter_version(sys.executable) == THIS

    def test_interpreter_version_of_nothing_is_none(self, tmp_path):
        assert setup_venv.interpreter_version(str(tmp_path / "nope")) is None


# ── Is the existing .venv good enough? ──────────────────────────────────────

class TestVenvIsUsable:
    def test_a_missing_venv_is_not_usable(self, tmp_path):
        assert not setup_venv.venv_is_usable(tmp_path, supported=(THIS,))

    def test_a_dangling_venv_is_not_usable(self, tmp_path):
        venv_python = interpreter.venv_python(tmp_path)
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(tmp_path / "removed-python3.12")
        assert not setup_venv.venv_is_usable(tmp_path, supported=(THIS,))

    def test_a_venv_on_an_unsupported_python_is_not_usable(self, tmp_path):
        venv_python = interpreter.venv_python(tmp_path)
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(sys.executable)
        assert not setup_venv.venv_is_usable(tmp_path, supported=((2, 7),))

    def test_a_working_supported_venv_is_usable(self, tmp_path):
        venv_python = interpreter.venv_python(tmp_path)
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(sys.executable)
        assert setup_venv.venv_is_usable(tmp_path, supported=(THIS,))


class TestRequirementsStamp:
    def test_install_is_needed_without_a_stamp(self, tmp_path):
        assert setup_venv.needs_install(tmp_path / "stamp", "abc")

    def test_install_is_needed_when_requirements_changed(self, tmp_path):
        (tmp_path / "stamp").write_text("old\n")
        assert setup_venv.needs_install(tmp_path / "stamp", "new")

    def test_install_is_skipped_when_nothing_changed(self, tmp_path):
        (tmp_path / "stamp").write_text("same\n")
        assert not setup_venv.needs_install(tmp_path / "stamp", "same")


# ── The whole run, with the external commands recorded ──────────────────────

READY = interpreter.Readiness("ready", python="/x/.venv/bin/python",
                              version="3.12.15")
UNAVAILABLE = interpreter.Readiness(
    "unavailable", python="/x/.venv/bin/python", missing=("pandas",),
    problems=("saknade Python-paket: pandas",))


class FakeRun:
    """Records commands; `-m venv` creates a runnable venv python."""

    def __init__(self, root):
        self.root = root
        self.calls = []

    def __call__(self, cmd, **kw):
        cmd = [str(part) for part in cmd]
        self.calls.append(cmd)
        if cmd[1:3] == ["-m", "venv"]:
            target = Path(cmd[-1]) / "bin" / "python"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.symlink_to(sys.executable)
        stdout = ("pandas==3.0.6\nnumpy==2.0.2\n"
                  if "freeze" in cmd else "")
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    def ran(self, *fragment):
        return any(call[1:1 + len(fragment)] == list(fragment)
                   for call in self.calls)


class TestMain:
    def _root(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("pandas>=2.0\n")
        (tmp_path / "requirements.lock").write_text("pandas==3.0.6\n")
        return tmp_path

    def _ready_venv(self, root):
        venv_python = interpreter.venv_python(root)
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(sys.executable)
        setup_venv.stamp_path(root).write_text(
            setup_venv.requirements_digest(root / "requirements.lock") + "\n")
        return venv_python

    def test_a_fresh_checkout_gets_exactly_the_locked_environment(
            self, tmp_path, capsys):
        root = self._root(tmp_path)
        run = FakeRun(root)
        code = setup_venv.main(
            [], root=root, run=run, base_python="/opt/homebrew/bin/python3.12",
            supported=(THIS,), probe=lambda py, r: READY)
        venv_python = str(interpreter.venv_python(root))
        assert code == 0
        assert run.calls[0] == ["/opt/homebrew/bin/python3.12", "-m", "venv",
                                "--clear", str(root / ".venv")]
        # Exactly the lock: no resolution, nothing extra, then verified.
        assert [venv_python, "-m", "pip", "install", "--no-deps", "-r",
                str(root / "requirements.lock")] in run.calls
        assert [venv_python, "-m", "pip", "check"] in run.calls
        assert setup_venv.stamp_path(root).read_text().strip() == \
            setup_venv.requirements_digest(root / "requirements.lock")
        assert interpreter.configured_python(root) == interpreter.venv_python(root)
        assert "redo" in capsys.readouterr().out

    def test_an_up_to_date_ready_venv_installs_nothing(self, tmp_path):
        root = self._root(tmp_path)
        self._ready_venv(root)
        run = FakeRun(root)
        code = setup_venv.main([], root=root, run=run, supported=(THIS,),
                               probe=lambda py, r: READY)
        assert code == 0
        assert run.calls == []
        assert interpreter.configured_python(root) == interpreter.venv_python(root)

    def test_a_changed_lock_rebuilds_the_venv_from_scratch(self, tmp_path):
        """--clear: a package removed from the lock must not linger."""
        root = self._root(tmp_path)
        self._ready_venv(root)
        (root / "requirements.lock").write_text("pandas==3.0.7\n")
        run = FakeRun(root)
        code = setup_venv.main([], root=root, run=run, supported=(THIS,),
                               base_python="/opt/homebrew/bin/python3.12",
                               probe=lambda py, r: READY)
        assert code == 0
        assert run.ran("-m", "venv", "--clear")
        assert run.ran("-m", "pip", "install", "--no-deps")

    def test_a_broken_venv_with_a_current_stamp_is_repaired(self, tmp_path, capsys):
        """The fix the server names must work: a package deleted by hand
        leaves the stamp current, so the stamp alone cannot be trusted."""
        root = self._root(tmp_path)
        self._ready_venv(root)
        verdicts = iter([UNAVAILABLE, READY])
        run = FakeRun(root)
        code = setup_venv.main([], root=root, run=run, supported=(THIS,),
                               base_python="/opt/homebrew/bin/python3.12",
                               probe=lambda py, r: next(verdicts))
        assert code == 0
        assert run.ran("-m", "venv", "--clear")
        assert run.ran("-m", "pip", "install", "--no-deps")
        assert "pandas" in capsys.readouterr().out

    def test_a_repair_that_does_not_help_is_a_failing_exit(self, tmp_path, capsys):
        root = self._root(tmp_path)
        self._ready_venv(root)
        code = setup_venv.main([], root=root, run=FakeRun(root),
                               supported=(THIS,),
                               base_python="/opt/homebrew/bin/python3.12",
                               probe=lambda py, r: UNAVAILABLE)
        assert code == 1
        assert "pandas" in capsys.readouterr().out

    def test_no_lock_python_says_how_to_get_one(self, tmp_path, capsys):
        root = self._root(tmp_path)
        code = setup_venv.main([], root=root, run=FakeRun(root),
                               base_python=None, supported=((3, 12),),
                               find=lambda: None)
        assert code == 2
        assert "brew install python@3.12" in capsys.readouterr().out


class TestLock:
    def test_the_lock_is_resolved_fresh_and_written_with_a_header(self, tmp_path):
        root = tmp_path
        (root / "requirements.txt").write_text("pandas>=2.0\n")
        (root / "requirements-dev.txt").write_text("pylint>=3.3,<4\n")
        run = FakeRun(root)
        code = setup_venv.main(["--lock"], root=root, run=run,
                               base_python="/opt/homebrew/bin/python3.12",
                               supported=(THIS,))
        assert code == 0
        installs = [c for c in run.calls if c[1:4] == ["-m", "pip", "install"]]
        assert any("requirements.txt" in " ".join(c)
                   and "requirements-dev.txt" in " ".join(c) for c in installs)
        assert run.ran("-m", "pip", "check")
        lock = (root / "requirements.lock").read_text()
        assert lock.startswith("#")
        assert interpreter.parse_lock(root / "requirements.lock") == {
            "numpy": "2.0.2", "pandas": "3.0.6"}
        # The live venv is never touched while locking.
        assert not (root / ".venv").exists()


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_the_committed_lock_pins_every_requirement_exactly():
    pins = interpreter.parse_lock(REPO / "requirements.lock")
    locked = {_normalize(name) for name in pins}
    for path in ("requirements.txt", "requirements-dev.txt"):
        for raw in (REPO / path).read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                name = re.split(r"[<>=!~\[ ;]", line, maxsplit=1)[0]
                assert _normalize(name) in locked, (path, name)
    assert pins["eclipse-sumo"] == pins["sumo-data"] == "1.27.1"


def test_make_lock_regenerates_the_lock():
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"(?m)^lock:\n\tpython3 tools/setup_venv.py --lock$", makefile)


class TestUseAnExistingInterpreter:
    """`make setup USE=/usr/bin/python3`: adopt a working environment
    instead of building a new one (whose caches would all be cold)."""

    def _root(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("pandas>=2.0\n")
        return tmp_path

    def test_a_ready_interpreter_is_remembered(self, tmp_path, capsys):
        root = self._root(tmp_path)
        calls = []
        code = setup_venv.main(
            ["--use", "/usr/bin/python3"], root=root,
            run=lambda cmd, **kw: calls.append(cmd),
            probe=lambda py, r: interpreter.Readiness(
                "ready", python=str(py), version="3.9.6"))
        assert code == 0
        assert calls == []                       # nothing built or installed
        assert interpreter.configured_python(root) == Path("/usr/bin/python3")
        assert "redo" in capsys.readouterr().out

    def test_an_interpreter_that_cannot_simulate_is_not_remembered(
            self, tmp_path, capsys):
        root = self._root(tmp_path)
        code = setup_venv.main(
            ["--use", "/opt/homebrew/bin/python3"], root=root,
            run=lambda *a, **k: None,
            probe=lambda py, r: interpreter.Readiness(
                "unavailable", python=str(py), missing=("pandas",),
                problems=("saknade Python-paket: pandas",)))
        assert code == 1
        assert interpreter.configured_python(root) is None
        assert "pandas" in capsys.readouterr().out

    def test_plain_setup_keeps_a_working_configured_interpreter(
            self, tmp_path, capsys):
        root = self._root(tmp_path)
        (root / interpreter.CONFIG_FILENAME).write_text("/usr/bin/python3\n")
        calls = []
        code = setup_venv.main(
            [], root=root, run=lambda cmd, **kw: calls.append(cmd),
            probe=lambda py, r: interpreter.Readiness(
                "ready", python=str(py), version="3.9.6"))
        assert code == 0
        assert calls == []
        assert "/usr/bin/python3" in capsys.readouterr().out


def test_sumo_is_pinned_together_with_its_data_package():
    """eclipse-sumo 1.28.0 breaks demand calibration ("route-time projection
    does not reconstruct raw entered cells", measured 2026-10-10), and
    eclipse-sumo depends on sumo-data WITHOUT a version: pinning one alone
    left sumo-data 1.28 beside sumo 1.27.1 and the binary crashed in dyld."""
    pins = dict(re.findall(r"(?m)^(eclipse-sumo|sumo-data)==([0-9.]+)\s*$",
                           (REPO / "requirements.txt").read_text()))
    assert set(pins) == {"eclipse-sumo", "sumo-data"}
    assert pins["eclipse-sumo"] == pins["sumo-data"] == "1.27.1"


def test_the_setup_tool_runs_on_an_old_bootstrap_python():
    """`make setup` starts on whatever python3 exists, even macOS's 3.9."""
    for path in (REPO / "tools" / "setup_venv.py",
                 REPO / "traffic_sim" / "simulation" / "interpreter.py"):
        source = path.read_text(encoding="utf-8")
        assert "from __future__ import annotations" in source, path.name
        ast.parse(source, filename=path.name, feature_version=(3, 9))


# ── The launch paths ────────────────────────────────────────────────────────

def _recipes(makefile: str) -> list[str]:
    return [line for line in makefile.splitlines() if line.startswith("\t")]


def test_no_make_recipe_runs_a_bare_python3_except_setup():
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    offenders = [line for line in _recipes(makefile)
                 if re.match(r"\t-?python3 ", line)
                 and "tools/setup_venv.py" not in line]
    assert offenders == []


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize("with_venv,override,expected", [
    (True, None, ".venv/bin/python serve.py"),
    (False, None, "python3 serve.py"),
    (True, "python3", "python3 serve.py"),   # CI passes PYTHON explicitly
])
def test_make_serve_uses_the_project_venv_when_it_exists(
        tmp_path, with_venv, override, expected):
    shutil.copy(REPO / "Makefile", tmp_path / "Makefile")
    if with_venv:
        _executable(tmp_path / ".venv" / "bin" / "python", "#!/bin/sh\n")
    cmd = ["make", "-n", "serve"] + ([f"PYTHON={override}"] if override else [])
    out = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True,
                         env={**os.environ, "MAKEFLAGS": ""}, check=True).stdout
    assert out.strip().splitlines()[-1].startswith(expected)


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_make_prefers_the_configured_interpreter_over_the_venv(tmp_path):
    shutil.copy(REPO / "Makefile", tmp_path / "Makefile")
    _executable(tmp_path / ".venv" / "bin" / "python", "#!/bin/sh\n")
    (tmp_path / interpreter.CONFIG_FILENAME).write_text("/usr/bin/python3\n")
    out = subprocess.run(["make", "-n", "serve"], cwd=tmp_path,
                         capture_output=True, text=True,
                         env={**os.environ, "MAKEFLAGS": ""}, check=True).stdout
    assert out.strip().splitlines()[-1].startswith("/usr/bin/python3 serve.py")


def test_start_command_serves_on_the_configured_interpreter(tmp_path):
    sandbox = _launcher_sandbox(tmp_path)
    configured = _executable(tmp_path / "old-python",
                             "#!/bin/sh\necho CONFIGURED-SERVED \"$@\"\n")
    _executable(sandbox / ".venv" / "bin" / "python",
                "#!/bin/sh\necho VENV-SERVED \"$@\"\n")
    (sandbox / interpreter.CONFIG_FILENAME).write_text(f"{configured}\n")
    out = subprocess.run(["sh", "start.command"], cwd=sandbox, input="",
                         capture_output=True, text=True, timeout=30).stdout
    assert "CONFIGURED-SERVED serve.py" in out
    assert "saknas" not in out


def test_the_configuration_file_is_never_committed():
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", interpreter.CONFIG_FILENAME], cwd=REPO)
    assert ignored.returncode == 0


def test_start_command_prefers_the_venv_and_offers_setup():
    script = (REPO / "start.command").read_text(encoding="utf-8")
    assert subprocess.run(["sh", "-n", str(REPO / "start.command")]).returncode == 0
    assert ".venv/bin/python" in script
    assert "tools/setup_venv.py" in script
    assert os.access(REPO / "start.command", os.X_OK)


def _launcher_sandbox(tmp_path: Path) -> Path:
    shutil.copy(REPO / "start.command", tmp_path / "start.command")
    (tmp_path / "start.command").chmod(0o755)
    (tmp_path / "web" / "data").mkdir(parents=True)
    (tmp_path / "web" / "data" / "network.geojson").write_text("{}")
    (tmp_path / "serve.py").write_text("print('BARE-PYTHON-SERVED')\n")
    return tmp_path


def test_start_command_serves_on_the_venv_when_it_exists(tmp_path):
    sandbox = _launcher_sandbox(tmp_path)
    _executable(sandbox / ".venv" / "bin" / "python",
                "#!/bin/sh\necho VENV-SERVED \"$@\"\n")
    out = subprocess.run(["sh", "start.command", "--no-open"], cwd=sandbox,
                         input="", capture_output=True, text=True,
                         timeout=30).stdout
    assert "VENV-SERVED serve.py --no-open" in out
    assert "saknas" not in out


def test_start_command_without_a_venv_asks_then_still_serves_the_map(tmp_path):
    sandbox = _launcher_sandbox(tmp_path)
    out = subprocess.run(["sh", "start.command"], cwd=sandbox, input="n\n",
                         capture_output=True, text=True, timeout=30).stdout
    assert "Simuleringsmiljön (.venv) saknas" in out
    assert "make setup" in out
    assert "BARE-PYTHON-SERVED" in out


# ── start-wsl.sh, run for real against fake interpreters ───────────────────

_FAKE_PY312 = """#!/bin/sh
echo "$0 $*" >> "$LOG"
case "$*" in
  *version_info*) exit 0 ;;
  *sched_getaffinity*) echo 4; exit 0 ;;
  "-m venv"*)
    for last; do :; done
    case "$*" in *--clear*) rm -rf "$last" ;; esac
    mkdir -p "$last/bin"
    cp "$FAKE_VENV_PY" "$last/bin/python"; chmod +x "$last/bin/python"
    exit 0 ;;
esac
exit 0
"""

_FAKE_VENV_PY312 = """#!/bin/sh
echo "venv312 $*" >> "$LOG"
case "$*" in
  *version_info*) exit 0 ;;
  "-") cat > /dev/null; echo "Using SUMO: fake"; exit 0 ;;
  serve.py*) echo "SERVED-BY-312 $*"; exit 0 ;;
esac
exit 0
"""

_FAKE_VENV_PY311 = """#!/bin/sh
echo "venv311 $*" >> "$LOG"
case "$*" in
  *version_info*) exit 1 ;;
  *"pip install"*) echo "ERROR: contourpy==1.4.0 requires Python >=3.12" >&2; exit 1 ;;
  serve.py*) echo "SERVED-BY-311 $*"; exit 0 ;;
esac
exit 0
"""


def _wsl_sandbox(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copy(REPO / "start-wsl.sh", root / "start-wsl.sh")
    shutil.copy(REPO / "requirements.lock", root / "requirements.lock")
    for name in ("net.net.xml", "network_metadata.json", "direction_split.json"):
        (root / "sumo").mkdir(exist_ok=True)
        (root / "sumo" / name).write_text("x")
    fakebin = tmp_path / "fakebin"
    _executable(fakebin / "uname", "#!/bin/sh\necho 5.15.153.1-microsoft-standard-WSL2\n")
    _executable(fakebin / "sha256sum", '#!/bin/sh\nexec shasum -a 256 "$@"\n')
    _executable(fakebin / "python3", _FAKE_PY312)
    template = _executable(tmp_path / "venv312-template", _FAKE_VENV_PY312)
    env = {**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}",
           "LOG": str(tmp_path / "calls.log"), "FAKE_VENV_PY": str(template)}
    return root, env


def _run_wsl(root, env):
    done = subprocess.run(["bash", "start-wsl.sh"], cwd=root, env=env,
                          capture_output=True, text=True, timeout=60)
    log = Path(env["LOG"]).read_text() if Path(env["LOG"]).exists() else ""
    return done, log


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_wsl_start_rebuilds_an_existing_python_311_venv(tmp_path):
    """Review 2026-10-10 (P2): a .venv from an older 3.11 run was kept and the
    3.12-only lock was installed into it."""
    root, env = _wsl_sandbox(tmp_path)
    _executable(root / ".venv" / "bin" / "python", _FAKE_VENV_PY311)
    done, log = _run_wsl(root, env)
    assert done.returncode == 0, done.stderr
    assert f"-m venv --clear {root}/.venv" in log
    assert "venv311 -m pip install" not in log
    assert "venv312 -m pip install --no-deps -r requirements.lock" in log
    assert "venv312 -m pip check" in log
    assert "SERVED-BY-312" in done.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_wsl_start_rebuilds_from_scratch_when_the_lock_changes(tmp_path):
    root, env = _wsl_sandbox(tmp_path)
    _run_wsl(root, env)                                     # first install
    Path(env["LOG"]).unlink()
    (root / "requirements.lock").write_text(
        (root / "requirements.lock").read_text() + "# changed\n")
    done, log = _run_wsl(root, env)
    assert done.returncode == 0, done.stderr
    assert "-m venv --clear" in log
    assert "SERVED-BY-312" in done.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_wsl_start_reuses_a_current_312_venv(tmp_path):
    root, env = _wsl_sandbox(tmp_path)
    _run_wsl(root, env)
    Path(env["LOG"]).unlink()
    done, log = _run_wsl(root, env)
    assert done.returncode == 0, done.stderr
    assert "-m venv" not in log
    assert "pip install" not in log
    assert "SERVED-BY-312" in done.stdout


def test_start_command_falls_back_to_the_venv_when_the_configured_python_is_gone(
        tmp_path):
    """Review 2026-10-10 (P2): a stale `.simulation-python` (a removed
    interpreter) stopped start.command although .venv worked."""
    sandbox = _launcher_sandbox(tmp_path)
    _executable(sandbox / ".venv" / "bin" / "python",
                "#!/bin/sh\necho VENV-SERVED \"$@\"\n")
    (sandbox / interpreter.CONFIG_FILENAME).write_text(
        f"{tmp_path / 'removed-python3.9'}\n")
    done = subprocess.run(["sh", "start.command"], cwd=sandbox, input="",
                          capture_output=True, text=True, timeout=30)
    assert "VENV-SERVED serve.py" in done.stdout
    assert "removed-python3.9" in done.stdout + done.stderr   # says why


def test_start_command_accepts_a_relative_configured_python(tmp_path):
    sandbox = _launcher_sandbox(tmp_path)
    _executable(sandbox / ".venv" / "bin" / "python",
                "#!/bin/sh\necho VENV-SERVED \"$@\"\n")
    (sandbox / interpreter.CONFIG_FILENAME).write_text(".venv/bin/python\n")
    done = subprocess.run(["sh", "start.command"], cwd=sandbox, input="",
                          capture_output=True, text=True, timeout=30)
    assert "VENV-SERVED serve.py" in done.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_make_ignores_a_configured_python_that_is_gone(tmp_path):
    shutil.copy(REPO / "Makefile", tmp_path / "Makefile")
    _executable(tmp_path / ".venv" / "bin" / "python", "#!/bin/sh\n")
    (tmp_path / interpreter.CONFIG_FILENAME).write_text(
        f"{tmp_path / 'removed-python3.9'}\n")
    out = subprocess.run(["make", "-n", "serve"], cwd=tmp_path,
                         capture_output=True, text=True,
                         env={**os.environ, "MAKEFLAGS": ""}, check=True).stdout
    assert out.strip().splitlines()[-1].startswith(".venv/bin/python serve.py")
