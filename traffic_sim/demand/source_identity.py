"""Canonical source identity for calibrated demand archives.

The calendar-only ``DemandBuildSpec`` deliberately stays stable across code
changes. Reuse safety therefore belongs at the archive boundary: builders and
consumers share this exact inventory and reject archives produced by any other
source bytes.
"""

from __future__ import annotations

from pathlib import Path

from traffic_sim.core.fingerprint import fingerprint_files


_FIXED_SOURCES = {
    "build_sumo_demand": "build_sumo_demand.py",
    "build_data": "build_data.py",
    "sensor_registry": "traffic_sim/intake/sensors.py",
    "sensor_registry_data": "data_in/sensors.json",
    # The local D-factor anchor re-levels the direction split at load time, so
    # its bytes change level-1 targets without the split FILE changing at all.
    "direction_anchor": "traffic_sim/intake/direction_anchor.py",
    "build_candidates": "build_candidates.py",
    "build_sumo_net": "build_sumo_net.py",
    "pfe": "traffic_sim/demand/pfe.py",
    "calibrated_provenance": "traffic_sim/demand/provenance.py",
    "pfe_kernel": "traffic_sim/demand/pfe_kernel.py",
    "candidate_cache": "traffic_sim/demand/cache.py",
    "pipeline_fingerprint": "traffic_sim/core/fingerprint.py",
    "assignment_priors": "assignment_priors.py",
    "prior_flows": "prior_flows.py",
    "observability": "observability.py",
    "demand_source_identity": "traffic_sim/demand/source_identity.py",
}


def demand_source_paths(root: Path) -> dict[str, Path]:
    """Return the complete deterministic demand-producing source inventory."""
    root = Path(root)
    result = {
        label: root / relative for label, relative in _FIXED_SOURCES.items()
    }
    recorded_paths = {path.resolve() for path in result.values()}
    for package in ("demand", "traffic_sim/demand"):
        for module_path in sorted((root / package).glob("*.py")):
            # Preserve the short, established labels above while ensuring a
            # newly added helper cannot silently escape archive provenance.
            if module_path.resolve() in recorded_paths:
                continue
            result[f"{package}/{module_path.name}"] = module_path
            recorded_paths.add(module_path.resolve())
    return result


def demand_source_fingerprints(root: Path) -> dict[str, dict]:
    """Hash the current canonical demand source inventory."""
    return fingerprint_files(demand_source_paths(root))
