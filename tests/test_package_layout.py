"""Repository layout contracts for the reusable implementation packages."""

import importlib

import pytest


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    [
        ("study_contracts", "traffic_sim.core.contracts"),
        ("pipeline_fingerprint", "traffic_sim.core.fingerprint"),
        ("sensor_registry", "traffic_sim.intake.sensors"),
        ("candidate_cache", "traffic_sim.demand.cache"),
        ("pfe", "traffic_sim.demand.pfe"),
        ("pfe_kernel", "traffic_sim.demand.pfe_kernel"),
        ("validate_sim", "traffic_sim.confidence.loso"),
        ("validation_report", "traffic_sim.confidence.report"),
        ("closure_metrics", "traffic_sim.simulation.metrics"),
        ("sumo_network_metadata", "traffic_sim.simulation.metadata"),
        ("sumo_runtime", "traffic_sim.simulation.runtime"),
        ("network_audit", "traffic_sim.simulation.network_audit"),
        ("release_registry", "traffic_sim.ops.releases"),
        ("runs", "traffic_sim.ops.runs"),
    ],
)
def test_root_import_is_the_canonical_module(legacy, canonical):
    assert importlib.import_module(legacy) is importlib.import_module(canonical)
