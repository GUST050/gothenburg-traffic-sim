"""Read-only preflight for the libsumo speed path (PR G). Installs nothing.

PR G would replace the per-observation `sumo` subprocess with an in-process
libsumo session. Whether that is even attemptable is a property of the
environment, and the honest way to record it is to LOOK — not to conclude
"libsumo is missing" from a failed import whose real cause was an unset
`SUMO_HOME`.

So this tool separates three things that all present as ModuleNotFoundError:

  1. the module is absent from `sys.path` only because SUMO's `tools`
     directory is not on it (true here for `sumolib` and `traci`, which the
     project's own `runtime.sumo_home()` resolves at use time);
  2. the distribution ships the C++ library but not the Python binding;
  3. SUMO itself is absent.

It writes a record and changes nothing. Installing a package, building a
binding, or setting `SUMO_HOME` for the session are all deliberate acts with
their own authority, and none of them is this tool's to take.

Run:  python3 tools/preflight_libsumo.py [--out PATH] [--stdout]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = "libsumo_preflight_v2"
DEFAULT_OUT = ROOT / "validation" / "libsumo_preflight_v2.json"

#: The modules the speed path would need, and why each matters.
MODULES = {
    "libsumo": "in-process SUMO; the whole point of PR G",
    "traci": "the socket fallback PR G would replace",
    "sumolib": "network/route helpers the product already uses",
}

BINARIES = ("sumo", "netconvert", "duarouter")


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _importable(name: str, *, extra_path: str | None = None) -> dict[str, Any]:
    """Is the module importable, and from where?

    Probed in a SUBPROCESS when an extra path is involved, so answering the
    question cannot leave this process with a mutated `sys.path` that later
    answers a different question.
    """
    if extra_path is None:
        spec = importlib.util.find_spec(name)
        return {"importable": spec is not None,
                "origin": getattr(spec, "origin", None)}
    probe = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.find_spec({name!r})\n"
        "print(json.dumps({'importable': spec is not None, "
        "'origin': getattr(spec, 'origin', None)}))\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [extra_path, environment.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    result = subprocess.run([sys.executable, "-c", probe], env=environment,
                            capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return {"importable": False, "origin": None,
                "probe_error": result.stderr.strip()[:400]}
    return json.loads(result.stdout)


def _sumo_home() -> dict[str, Any]:
    # NOTE: the ambient `SUMO_HOME` variable is reported under `environment`,
    # which the content key excludes. It is context for a reader, not part of
    # what the record claims — and binding it would make the key depend on
    # whatever any surrounding process happened to export, so the same machine
    # in the same state would fail to reproduce its own preflight.
    report: dict[str, Any] = {
        "resolved_by_project": None,
        "tools_dir": None,
        "tools_dir_exists": False,
        "error": None,
    }
    try:
        from traffic_sim.simulation.runtime import sumo_home

        resolved = Path(sumo_home())
    except Exception as error:                       # noqa: BLE001
        report["error"] = f"{type(error).__name__}: {error}"
        return report
    report["resolved_by_project"] = str(resolved)
    tools = resolved / "tools"
    report["tools_dir"] = str(tools)
    report["tools_dir_exists"] = tools.is_dir()
    return report


def _distribution() -> dict[str, Any]:
    """What the installed SUMO distribution actually ships.

    The distinction that matters: `libsumocpp.so` is the C++ library, and it
    is NOT the Python binding. A tree can contain every libsumo header and
    every shared object and still have no importable `libsumo` module.
    """
    report: dict[str, Any] = {"root": None, "libsumo_cpp_library": [],
                              "libsumo_headers": False,
                              "libsumo_python_binding": []}
    home = _sumo_home().get("resolved_by_project")
    if not home:
        return report
    root = Path(home)
    report["root"] = str(root)
    report["libsumo_cpp_library"] = sorted({
        str(path.relative_to(root))
        for pattern in ("libsumocpp*.so", "libsumocpp*.dylib",
                        "libsumocpp*.dll")
        for path in root.rglob(pattern)
    })
    report["libsumo_headers"] = (root / "include" / "libsumo").is_dir()
    report["libsumo_python_binding"] = sorted(
        str(path.relative_to(root))
        for pattern in ("_libsumo*.so", "_libsumo*.dylib",
                        "_libsumo*.pyd", "libsumo/__init__.py")
        for path in root.rglob(pattern))
    return report


def build_record() -> dict[str, Any]:
    home = _sumo_home()
    tools = home.get("tools_dir") if home.get("tools_dir_exists") else None
    modules = {
        name: {
            "why": reason,
            "on_default_path": _importable(name),
            "with_sumo_tools_on_path": (
                _importable(name, extra_path=tools) if tools else None),
        }
        for name, reason in MODULES.items()
    }
    binaries = {}
    resolved_home = Path(home["resolved_by_project"]) if (
        home.get("resolved_by_project")) else None
    for name in BINARIES:
        path = shutil.which(name)
        if path is None and resolved_home is not None:
            packaged = resolved_home / "bin" / name
            if packaged.is_file() and os.access(packaged, os.X_OK):
                path = str(packaged)
        version = None
        if path:
            try:
                completed = subprocess.run([path, "--version"],
                                           capture_output=True, text=True,
                                           timeout=60)
                version = (completed.stdout.splitlines() or [""])[0].strip()
            except (OSError, subprocess.SubprocessError):
                version = None
        binaries[name] = {"path": path, "version": version}

    distribution = _distribution()
    libsumo = modules["libsumo"]
    available = bool(libsumo["on_default_path"]["importable"]
                     or (libsumo["with_sumo_tools_on_path"] or {})
                     .get("importable"))
    if available:
        verdict = "available"
        blocker = None
    elif distribution["libsumo_cpp_library"] and not (
            distribution["libsumo_python_binding"]):
        verdict = "blocked_missing_python_binding"
        blocker = (
            "the installed distribution ships the libsumo C++ library "
            f"({', '.join(distribution['libsumo_cpp_library'])}) and its "
            "headers, but no Python binding module anywhere under the SUMO "
            "root. `pip install "
            "eclipse-sumo` therefore does NOT make libsumo importable; a build "
            "or wheel that includes the SWIG Python bindings would be required.")
    elif not any(item["path"] for item in binaries.values()):
        verdict = "blocked_no_sumo"
        blocker = (
            "no SUMO binaries are on PATH or under the project-resolved "
            "SUMO home")
    else:
        verdict = "blocked_missing_libsumo"
        blocker = "libsumo is not importable and no C++ library was found"

    record = {
        "schema": SCHEMA,
        "kind": "libsumo_preflight",
        "stage": "closure_search_scaling_plan_pr_g",
        "evidence_class": "environment_preflight",
        "release_evidence": False,
        "read_only": True,
        "installed_anything": False,
        "verdict": verdict,
        "blocker": blocker,
        "sumo_home": home,
        "distribution": distribution,
        "modules": modules,
        "binaries": binaries,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "executable": sys.executable,
            "SUMO_HOME": os.environ.get("SUMO_HOME"),
        },
        "consequences": {
            "pr_g_persistent_sumo": (
                "blocked" if verdict != "available" else "attemptable"),
            "seed_worker_budget_unchanged": True,
            "note": (
                "The subprocess path remains the only execution backend, so "
                "the approved seed-worker budget and every process/memory cap "
                "stand exactly as benchmarked. Nothing here licenses raising "
                "them: a speed path that does not exist cannot justify more "
                "workers."),
        },
        "claim_boundary": {
            "opens_no_gate": True,
            "activates_policy_v3": False,
            "reason": (
                "an environment fact about one optional speed path. It "
                "measures no closure, ranks nothing and licenses nothing."),
        },
    }
    record["content_key"] = hashlib.sha256(
        _canonical({key: value for key, value in record.items()
                    if key not in ("content_key", "environment")})
        .encode("utf-8")).hexdigest()
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    record = build_record()
    payload = json.dumps(record, indent=1, sort_keys=True) + "\n"
    if args.stdout:
        print(payload, end="")
        return 0
    destination = Path(args.out)
    if destination.exists() and not args.overwrite:
        raise SystemExit(f"{destination} already exists; pass --overwrite to "
                         f"replace it after a real environment change.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload, encoding="utf-8")
    print(f"wrote {destination} (verdict={record['verdict']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
