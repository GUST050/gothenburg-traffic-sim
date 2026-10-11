"""Create or repair the project's .venv so the simulation can run.

Usage (from the repository root):
  make setup                         build or repair .venv from requirements.lock
  make lock                          re-resolve the requirement files into the lock
  make setup USE=/path/to/python     adopt an interpreter that already passes

THE LOCK (2026-10-10).  ``requirements.lock`` pins every package — indirect
dependencies included — to an exact version, resolved for Python 3.12 from
requirements.txt + requirements-dev.txt in a fresh venv (``make lock``).
``make setup`` builds .venv with Python 3.12 and installs exactly that set
(``--no-deps``, then ``pip check``), rebuilding from scratch whenever the lock
changes so nothing stale lingers, and repairing the venv when the readiness
check fails even though the stamp is current. The readiness check itself
compares the environment against the lock, so an environment that deviates
from it is never reported as ready.

ADOPTING AN EXISTING INTERPRETER (2026-10-10).  A machine may already have a
working environment outside .venv — the project owner's Mac simulates with
``/usr/bin/python3`` 3.9 + SUMO 1.27.1, and every demand cache and the adopted
route catalog bind that exact runtime. Building a new environment there would
throw all of it away, so ``--use`` checks the interpreter with the same
readiness probe the server uses and, only if it can simulate, records it in
the untracked ``.simulation-python``; the server, ``make`` and start.command
read that file first. A plain ``make setup`` keeps a configured interpreter
that still works and builds nothing.

WHY A TOOL AND NOT A README LINE.  On macOS the obvious commands fail in ways
that look like project bugs (found 2026-10-10): Homebrew's ``python3`` is 3.13,
for which ``numba==0.60.0`` publishes no wheel, and Homebrew marks its Pythons
externally managed (PEP 668), so ``pip install -r requirements.txt`` is refused
outright. This tool picks an interpreter the requirements actually install on
(``interpreter.SUPPORTED_PYTHON``), builds or repairs ``.venv`` with it,
installs only when ``requirements.txt`` changed, and finishes with the same
readiness check the server uses — so "setup succeeded" means "the server's
jobs can run", not merely "pip exited 0".

It runs on whatever ``python3`` starts it (macOS's own 3.9 included), so it is
standard-library only and avoids syntax newer than 3.9.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.simulation import interpreter  # noqa: E402 — needs ROOT on path

#: Tried in order; the first whose version is the lock's Python is used.
CANDIDATES: tuple[str, ...] = (
    "python%d.%d" % interpreter.LOCK_PYTHON, "python3")
STAMP_NAME = ".requirements.sha256"
VERSION_TIMEOUT_S = 15
LOCK_SOURCES = ("requirements.txt", "requirements-dev.txt")
LOCK_HEADER = """\
# Exact package set for the project environment (.venv), every indirect
# dependency included. Resolved for Python {major}.{minor} from {sources}
# in a fresh virtual environment. Do not edit by hand: change the source
# files and run `make lock`. `make setup` installs exactly this set
# (--no-deps, then `pip check`) and the server refuses to simulate on an
# environment that deviates from it.
"""


def interpreter_version(python: str) -> Optional[tuple[int, int]]:
    try:
        res = subprocess.run(
            [python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=VERSION_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    try:
        major, minor = res.stdout.strip().split(".")
        return int(major), int(minor)
    except ValueError:
        return None


def find_supported_python(
        candidates: Sequence[str] = CANDIDATES,
        which: Callable[[str], Optional[str]] = shutil.which,
        version_of: Callable[[str], Optional[tuple[int, int]]] = interpreter_version,
        supported: Sequence[tuple[int, int]] = (interpreter.LOCK_PYTHON,),
) -> Optional[str]:
    for name in candidates:
        path = which(name)
        if path and version_of(path) in {tuple(v) for v in supported}:
            return path
    return None


def venv_is_usable(root: Path, supported: Sequence[tuple[int, int]]
                   = (interpreter.LOCK_PYTHON,)) -> bool:
    python = interpreter.venv_python(root)
    if not python.exists():          # False for a dangling symlink too
        return False
    return interpreter_version(str(python)) in {tuple(v) for v in supported}


def stamp_path(root: Path) -> Path:
    return Path(root) / interpreter.VENV_DIRNAME / STAMP_NAME


def requirements_digest(requirements: Path) -> str:
    return hashlib.sha256(Path(requirements).read_bytes()).hexdigest()


def needs_install(stamp: Path, digest: str) -> bool:
    try:
        return Path(stamp).read_text(encoding="utf-8").strip() != digest
    except OSError:
        return True


def _no_python_message(supported: Sequence[tuple[int, int]]) -> str:
    versions = " eller ".join(f"{a}.{b}" for a, b in supported)
    newest = "%d.%d" % tuple(max(supported))
    return (f"Hittar ingen Python {versions} — {interpreter.LOCK_FILENAME} är "
            f"låst för den versionen.\n"
            f"Installera med:  brew install python@{newest}\n"
            f"och kör sedan:   make setup")


def _remember(root: Path, python: str) -> None:
    (root / interpreter.CONFIG_FILENAME).write_text(python + "\n",
                                                    encoding="utf-8")


def _adopt(root: Path, python: str,
           probe: Callable[[Path, Path], interpreter.Readiness]) -> int:
    readiness = probe(Path(python), root)
    print(readiness.message())
    if not readiness.ready:
        print(f"{python} sparas inte som projektets Python.")
        return 1
    _remember(root, python)
    print(f"Sparat i {interpreter.CONFIG_FILENAME}: servern, make och "
          f"start.command använder nu {python}.")
    return 0


def write_lock(root: Path, python: str, run: Callable[..., object]) -> int:
    """Resolve the requirement files in a throwaway venv and pin the result."""
    sources = [root / name for name in LOCK_SOURCES if (root / name).exists()]
    with tempfile.TemporaryDirectory(prefix="traffic-sim-lock-") as tmp:
        env_dir = Path(tmp) / "env"
        lock_python = str(env_dir / "bin" / "python")
        run([python, "-m", "venv", str(env_dir)], check=True)
        install = [lock_python, "-m", "pip", "install"]
        for source in sources:
            install += ["-r", str(source)]
        print(f"Löser {', '.join(s.name for s in sources)} med {python} …",
              flush=True)
        run(install, check=True)
        run([lock_python, "-m", "pip", "check"], check=True)
        frozen = run([lock_python, "-m", "pip", "freeze", "--all",
                      "--exclude", "pip"], check=True, capture_output=True,
                     text=True).stdout
    pins = sorted((line.strip() for line in frozen.splitlines()
                   if line.strip() and not line.startswith("#")),
                  key=str.lower)
    major, minor = interpreter.LOCK_PYTHON
    header = LOCK_HEADER.format(major=major, minor=minor,
                                sources=" + ".join(s.name for s in sources))
    lock = root / interpreter.LOCK_FILENAME
    lock.write_text(header + "\n".join(pins) + "\n", encoding="utf-8")
    print(f"Skrev {lock.name}: {len(pins)} paket.")
    return 0


def _build_venv(root: Path, chosen: str, run: Callable[..., object]) -> None:
    """A fresh venv holding exactly the lock: nothing older, nothing extra."""
    venv_dir = root / interpreter.VENV_DIRNAME
    venv_python = str(interpreter.venv_python(root))
    lock = root / interpreter.LOCK_FILENAME
    action = "Bygger om" if venv_dir.exists() else "Skapar"
    print(f"{action} {venv_dir} med {chosen} från {lock.name} (första "
          "gången tar det några minuter) …", flush=True)
    # --clear: rebuilt, never patched — a package dropped from the lock must
    # not linger, and a venv on a removed base Python must not survive.
    run([chosen, "-m", "venv", "--clear", str(venv_dir)], check=True)
    run([venv_python, "-m", "pip", "install", "--no-deps", "-r", str(lock)],
        check=True)
    run([venv_python, "-m", "pip", "check"], check=True)
    stamp_path(root).write_text(requirements_digest(lock) + "\n",
                                encoding="utf-8")


def main(argv: Sequence[str] = (), *, root: Path = ROOT,
         run: Callable[..., object] = subprocess.run,
         base_python: Optional[str] = None,
         find: Optional[Callable[[], Optional[str]]] = None,
         supported: Sequence[tuple[int, int]] = (interpreter.LOCK_PYTHON,),
         probe: Callable[[Path, Path], interpreter.Readiness]
         = interpreter.probe_readiness) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reinstall", action="store_true",
                        help="bygg om .venv från requirements.lock även om "
                             "inget ändrats")
    parser.add_argument("--use", metavar="PYTHON",
                        help="använd en befintlig Python som redan kan "
                             "simulera i stället för att bygga .venv")
    parser.add_argument("--lock", action="store_true",
                        help="lös requirements-filerna på nytt och skriv "
                             "requirements.lock")
    args = parser.parse_args(list(argv))

    root = Path(root)

    def choose() -> Optional[str]:
        return base_python or (find or (
            lambda: find_supported_python(supported=supported)))()

    if args.lock:
        chosen = choose()
        if chosen is None:
            print(_no_python_message(supported))
            return 2
        return write_lock(root, chosen, run)
    if args.use:
        return _adopt(root, args.use, probe)

    venv_python = interpreter.venv_python(root)
    configured = interpreter.configured_python(root)
    if configured is not None and configured != venv_python:
        readiness = probe(configured, root)
        if readiness.ready:
            print(f"Använder {configured} (från {interpreter.CONFIG_FILENAME}). "
                  f"{readiness.message()}")
            return 0
        print(f"{configured} (från {interpreter.CONFIG_FILENAME}) kan inte "
              f"simulera: {readiness.message()}\nAnvänder .venv i stället.")

    lock = root / interpreter.LOCK_FILENAME
    rebuild = (args.reinstall or not venv_is_usable(root, supported)
               or needs_install(stamp_path(root), requirements_digest(lock)))
    if not rebuild:
        readiness = probe(venv_python, root)
        if not readiness.ready:
            # The stamp only says what was installed, not what is there now:
            # a package deleted by hand leaves it current. Repair instead.
            print(f"{readiness.message()}\nReparerar .venv.")
            rebuild = True
    if rebuild:
        chosen = choose()
        if chosen is None:
            print(_no_python_message(supported))
            return 2
        _build_venv(root, chosen, run)
        readiness = probe(venv_python, root)
    print(readiness.message())
    if not readiness.ready:
        return 1
    _remember(root, str(interpreter.VENV_DIRNAME + "/bin/python"))
    print(f"Klart: servern, make och start.command använder {venv_python}. "
          "Starta appen med:  make serve")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except subprocess.CalledProcessError as exc:
        print(f"Ett steg misslyckades (kod {exc.returncode}): "
              f"{' '.join(map(str, exc.cmd))}")
        sys.exit(exc.returncode or 1)
