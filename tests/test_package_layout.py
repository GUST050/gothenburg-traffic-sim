"""Repository layout contracts for the reusable implementation packages.

The root used to carry one six-line compatibility shim per migrated module
(``sys.modules[__name__] = _implementation``). The migration into
``traffic_sim/`` is finished, so those shims are gone and every import names
its real module. These tests pin both halves of that outcome: the canonical
modules exist, and the legacy root names do not come back.
"""

import importlib
import importlib.util
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


@pytest.mark.parametrize("canonical", sorted(set(RETIRED_SHIMS.values())))
def test_canonical_module_imports(canonical):
    assert importlib.import_module(canonical) is not None


@pytest.mark.parametrize("legacy", sorted(RETIRED_SHIMS))
def test_retired_shim_is_not_reintroduced(legacy):
    """The root must not regrow a module that only forwards to the package."""
    assert not (ROOT / f"{legacy}.py").exists(), (
        f"{legacy}.py is back in the repo root; import "
        f"{RETIRED_SHIMS[legacy]} directly instead of adding a shim"
    )


def test_no_module_still_imports_a_retired_shim():
    """No source file may import the retired root names."""
    offenders = []
    for path in ROOT.rglob("*.py"):
        if path.parts[len(ROOT.parts)] in {".git", "docs"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            for legacy in RETIRED_SHIMS:
                if stripped.startswith(f"import {legacy}") or stripped.startswith(
                    f"from {legacy} import"
                ):
                    offenders.append(f"{path.relative_to(ROOT)}: {stripped}")
    assert not offenders, "retired shim imports still present:\n" + "\n".join(offenders)


def test_validation_entry_points_stay_importable():
    """``validate_sim``/``validation_report`` remain root CLI entry points.

    They are named by the Makefile (``make validate-temporal``) and imported by
    ``serve.py``, ``run_scenario.py`` and ``build_sumo_demand.py``, the last of
    which is a sealed demand source. They are deliberately NOT retired.
    """
    for name in ("validate_sim", "validation_report"):
        assert (ROOT / f"{name}.py").exists()
        assert importlib.import_module(name) is not None
