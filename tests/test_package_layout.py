"""Repository layout contracts for the reusable implementation packages.

The root used to carry one six-line compatibility shim per migrated module
(``sys.modules[__name__] = _implementation``). The migration into
``traffic_sim/`` is finished, so those shims are gone and every import names
its real module. These tests pin both halves of that outcome: the canonical
modules exist, and the legacy root names do not come back.

``validate_sim.py`` and ``validation_report.py`` are deliberately NOT retired.
They still use the same ``sys.modules`` rebind, because they are dual-purpose:
``make validate-temporal`` runs them as commands, and ``build_sumo_demand.py``
(a sealed demand source), ``run_scenario.py`` and ``serve.py`` import them and
reach through to the implementation's attributes. That rebind is load-bearing,
so it is asserted here rather than assumed.
"""

import importlib
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent

# The retired root shims, mapped to the module that replaced each one. Kept as
# data so a reintroduced shim fails loudly instead of quietly working again.
RETIRED_SHIMS = {
    "study_contracts": "traffic_sim.core.contracts",
    "pipeline_fingerprint": "traffic_sim.core.fingerprint",
    "sensor_registry": "traffic_sim.intake.sensors",
    "candidate_cache": "traffic_sim.demand.cache",
    "pfe": "traffic_sim.demand.pfe",
    "pfe_kernel": "traffic_sim.demand.pfe_kernel",
    "closure_metrics": "traffic_sim.simulation.metrics",
    "sumo_network_metadata": "traffic_sim.simulation.metadata",
    "sumo_runtime": "traffic_sim.simulation.runtime",
    "network_audit": "traffic_sim.simulation.network_audit",
    "release_registry": "traffic_sim.ops.releases",
    "runs": "traffic_sim.ops.runs",
}

# The root CLI wrappers that still rebind sys.modules on purpose.
CLI_WRAPPERS = {
    "validate_sim": "traffic_sim.confidence.loso",
    "validation_report": "traffic_sim.confidence.report",
}

# Tracked source roots. The scan must not descend into sumo/, runs/, cache/ or
# a developer virtualenv: those are gitignored, can reach tens of GB, and a
# third-party file there must never fail this repository's contract test.
SOURCE_DIRS = ("traffic_sim", "signals", "demand", "dirsplit", "tools", "tests")


def _source_files():
    yield from sorted(ROOT.glob("*.py"))
    for name in SOURCE_DIRS:
        yield from sorted((ROOT / name).rglob("*.py"))


@pytest.mark.parametrize("canonical", sorted(set(RETIRED_SHIMS.values())))
def test_canonical_module_imports(canonical):
    """The replacement module must import (raises ModuleNotFoundError if not)."""
    assert importlib.import_module(canonical).__name__ == canonical


@pytest.mark.parametrize("legacy", sorted(RETIRED_SHIMS))
def test_retired_shim_is_not_reintroduced(legacy):
    """The root must not regrow a module that only forwards to the package."""
    assert not (ROOT / f"{legacy}.py").exists(), (
        f"{legacy}.py is back in the repo root; import "
        f"{RETIRED_SHIMS[legacy]} directly instead of adding a shim"
    )


def test_no_module_still_imports_a_retired_shim():
    """No source file may import the retired root names.

    Matched on a token boundary so a future ``runs_registry`` or
    ``pfe_benchmark`` is not mistaken for ``runs`` or ``pfe``.
    """
    pattern = re.compile(
        r"^\s*(?:import|from)\s+(%s)\b" % "|".join(map(re.escape, RETIRED_SHIMS))
    )
    offenders = []
    for path in _source_files():
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if pattern.match(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, "retired shim imports still present:\n" + "\n".join(offenders)


@pytest.mark.parametrize(("wrapper", "canonical"), sorted(CLI_WRAPPERS.items()))
def test_cli_wrapper_rebinds_to_its_implementation(wrapper, canonical):
    """Importing the wrapper must yield the implementation module itself.

    ``build_sumo_demand.py`` calls ``validation_report.write_report()`` and
    reads ``validation_report.OUT_PATH``; ``serve.py`` and ``run_scenario.py``
    do the same. If the rebind breaks, ``import validation_report`` still
    returns a module and only fails later with AttributeError, so identity is
    the assertion that actually protects those call sites.
    """
    assert (ROOT / f"{wrapper}.py").exists()
    assert importlib.import_module(wrapper) is importlib.import_module(canonical)
