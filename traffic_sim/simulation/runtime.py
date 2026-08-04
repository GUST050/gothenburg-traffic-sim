"""Lightweight runtime helpers shared by SUMO execution entry points.

Keep this module free of network-building dependencies.  Scenario and demand
commands only need to locate the eclipse-sumo installation; importing the
network builder would otherwise load OSMnx and PyProj before any work starts.

TRACI DISCOVERY (LUNA-WARM-15). The v6 campaign failed with
`No module named 'traci'` on all three identities: production did a bare
`import traci`, but the package ships INSIDE the SUMO installation at
`<sumo_home>/tools/traci` and is not on `sys.path`. Warming had therefore never
started once — every "warm" arm since the controller was introduced was a cold
fallback caused by an import error.

The fix lives here rather than in the controller so that one resolver serves the
controller, the campaign preflight and the tests, and so "which SUMO did we
resolve against" has a single answer.
"""

from __future__ import annotations

import os
from pathlib import Path

TRACI_PACKAGE = "traci"
TOOLS_DIRNAME = "tools"

# Every attribute production actually calls on a TraCI connection. Checking that
# the module imports is not enough: a package that resolves but lacks the API
# would fail later, inside an approved campaign, which is the expensive place to
# discover it.
REQUIRED_TRACI_API = ("init", "close", "simulationStep")
REQUIRED_TRACI_NAMESPACES = {
    "simulation": ("getTime", "saveState"),
    "vehicle": ("getIDList",),
}


class SumoRuntimeError(RuntimeError):
    """Raised when SUMO or TraCI cannot be located precisely. Never guessed at."""


def sumo_home(env: dict | None = None) -> Path:
    """The active SUMO installation root.

    `SUMO_HOME` wins when set and non-empty, so an operator can point at a
    specific installation deterministically; otherwise the installed
    `eclipse-sumo` package is used. An explicitly-set but unusable home is an
    ERROR, never a silent fallback to a different installation — falling back
    would resolve TraCI against one SUMO while running binaries from another.
    """
    environment = os.environ if env is None else env
    declared = (environment.get("SUMO_HOME") or "").strip()
    if declared:
        home = Path(declared)
        if not home.is_dir():
            raise SumoRuntimeError(
                f"SUMO_HOME is set to {declared!r}, which is not a directory; "
                f"refusing to fall back to a different installation")
        return home

    import importlib.util                              # noqa: PLC0415 — lazy
    spec = importlib.util.find_spec("sumo")            # pip install eclipse-sumo
    if spec is None or not spec.origin:
        raise SumoRuntimeError(
            "cannot locate SUMO: set SUMO_HOME or install eclipse-sumo")
    return Path(spec.origin).parent


def traci_tools_dir(home: Path | str) -> Path:
    """The directory holding the TraCI package for a given SUMO home."""
    tools = Path(home) / TOOLS_DIRNAME
    if not tools.is_dir():
        raise SumoRuntimeError(
            f"{tools} does not exist; this SUMO installation has no tools "
            f"directory, so its TraCI package cannot be located")
    if not (tools / TRACI_PACKAGE).is_dir():
        raise SumoRuntimeError(
            f"{tools / TRACI_PACKAGE} does not exist; TraCI is not present in "
            f"this SUMO installation")
    return tools


def resolve_traci(home: Path | str, *, modules=None):
    """Import TraCI from EXACTLY this SUMO home, or fail.

    Uses Python's real import machinery: `<home>/tools` goes on `sys.path` and
    STAYS there, which is what the approved LUNA-WARM-08 diagnostic did and what
    actually works against the installed package — TraCI imports siblings such
    as `sumolib` from the same directory, so removing the entry afterwards would
    break those later imports.

    The module that arrives is then PROVEN to come from that tree. A bare
    `import traci` — v6's bug — either finds nothing, or could find some
    unrelated package already on the path; either way the campaign would be
    resolving against a SUMO nobody chose.

    `modules` is an injection seam so tests can present an already-loaded module
    without touching the interpreter's own registry.
    """
    import sys                                         # noqa: PLC0415 — lazy
    loaded = sys.modules if modules is None else modules

    tools = traci_tools_dir(home)
    expected_root = (tools / TRACI_PACKAGE).resolve()

    existing = loaded.get(TRACI_PACKAGE)
    if existing is None:
        if str(tools) not in sys.path:
            sys.path.insert(0, str(tools))
        try:
            import importlib                           # noqa: PLC0415 — lazy
            module = importlib.import_module(TRACI_PACKAGE)
        except Exception as error:
            raise SumoRuntimeError(
                f"TraCI could not be imported from {tools}: {error}")
    else:
        module = existing

    origin = getattr(module, "__file__", None)
    if not origin:
        raise SumoRuntimeError(
            "the imported TraCI module reports no origin, so it cannot be "
            "proven to come from this SUMO installation")
    resolved = Path(origin).resolve()
    try:
        resolved.relative_to(expected_root)
    except ValueError:
        raise SumoRuntimeError(
            f"TraCI resolved to {resolved}, which is outside the active "
            f"installation's {expected_root}; refusing a module from a SUMO "
            f"nobody chose")
    return module


def validate_traci_api(module) -> dict:
    """Confirm the resolved module exposes everything production calls.

    Returns a bounded report of what was found. It reads attributes only — it
    makes no TraCI call, opens no socket and starts no process.
    """
    missing: list[str] = []
    for name in REQUIRED_TRACI_API:
        if not callable(getattr(module, name, None)):
            missing.append(name)
    for namespace, attributes in sorted(REQUIRED_TRACI_NAMESPACES.items()):
        target = getattr(module, namespace, None)
        if target is None:
            missing.append(namespace)
            continue
        for attribute in attributes:
            if not callable(getattr(target, attribute, None)):
                missing.append(f"{namespace}.{attribute}")
    if missing:
        raise SumoRuntimeError(
            f"the resolved TraCI module is missing required API: "
            f"{', '.join(sorted(missing))}")
    return {
        "origin": str(Path(getattr(module, "__file__")).resolve()),
        "required_api": list(REQUIRED_TRACI_API),
        "required_namespaces": {k: list(v) for k, v in
                                sorted(REQUIRED_TRACI_NAMESPACES.items())},
        "api_complete": True,
    }
