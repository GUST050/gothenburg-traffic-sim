import json
from dataclasses import replace

import pytest

from traffic_sim.simulation.runtime import sumo_home
from traffic_sim.simulation.warm_state_cache import (
    STATE_FILENAME,
    certify_warm_state_equivalence,
    compare_decision_metrics,
    create_baseline_evidence_identity,
    create_warm_state_identity,
    load_state_arguments,
    restore_baseline_evidence,
    restore_warm_state,
    save_state_arguments,
    store_baseline_evidence,
    store_warm_state,
)


def _inputs(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    network = tmp_path / "net.net.xml"
    route = tmp_path / "demand.rou.xml"
    source = tmp_path / "runner.py"
    network.write_text("<net/>")
    route.write_text("<routes/>")
    source.write_text("VERSION = 1\n")
    return network, route, source


def _identity(tmp_path, **overrides):
    network, route, source = _inputs(tmp_path)
    values = {
        "demand_build_id": "demand-a",
        "demand_variant": "q50",
        "seed": 1000,
        "simulation_mode": "meso",
        "warmup_end_s": 3600,
        "network_path": network,
        "route_path": route,
        "source_files": {"runner": source},
        "sumo_home": sumo_home(),
    }
    values.update(overrides)
    return create_warm_state_identity(**values), network, route, source


def _certificate(identity, right=None):
    metrics = {
        "time_loss_s": 123.5,
        "throughput": 42,
        "recovery": {"status": "recovered", "time_s": 900},
    }
    return certify_warm_state_equivalence(
        identity, metrics, metrics if right is None else right)


def test_identity_covers_every_required_provenance_dimension(tmp_path):
    identity, network, route, source = _identity(tmp_path)

    route.write_text("<routes><vehicle id='changed'/></routes>")
    changed_route = create_warm_state_identity(
        demand_build_id="demand-a", demand_variant="q50", seed=1000,
        simulation_mode="meso", warmup_end_s=3600,
        network_path=network, route_path=route,
        source_files={"runner": source}, sumo_home=sumo_home())
    source.write_text("VERSION = 2\n")
    changed_code = create_warm_state_identity(
        demand_build_id="demand-a", demand_variant="q50", seed=1000,
        simulation_mode="meso", warmup_end_s=3600,
        network_path=network, route_path=route,
        source_files={"runner": source}, sumo_home=sumo_home())

    assert changed_route.content_key != identity.content_key
    assert changed_code.content_key != changed_route.content_key
    assert replace(identity, platform_id="other-platform").content_key != (
        identity.content_key)
    assert replace(identity, sumo_version="SUMO other").content_key != (
        identity.content_key)
    assert replace(identity, seed=1001).content_key != identity.content_key
    assert replace(identity, warmup_end_s=7200).content_key != (
        identity.content_key)


def test_cache_store_and_restore_are_atomic_and_content_addressed(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    state = tmp_path / "warm.xml.gz"
    state.write_bytes(b"sumo-state")
    cache = tmp_path / "cache"

    entry = store_warm_state(
        cache, identity, state, _certificate(identity))
    destination = tmp_path / "workspace" / "warm.xml.gz"
    lookup = restore_warm_state(cache, identity, destination)

    assert entry.name == identity.content_key
    assert (entry / STATE_FILENAME).read_bytes() == b"sumo-state"
    assert lookup.hit is True
    assert lookup.reason == "hit"
    assert destination.read_bytes() == b"sumo-state"
    # Storing the same immutable identity again is idempotent.
    assert store_warm_state(
        cache, identity, state, _certificate(identity)) == entry


def test_cache_rejects_semantic_mismatch_before_storing(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    state = tmp_path / "warm.xml.gz"
    state.write_bytes(b"sumo-state")
    mismatched = {
        "time_loss_s": 999.0,
        "throughput": 42,
        "recovery": {"status": "recovered", "time_s": 900},
    }
    certificate = _certificate(identity, mismatched)

    assert certificate.status == "fail"
    assert certificate.mismatch
    with pytest.raises(ValueError, match="equivalence"):
        store_warm_state(tmp_path / "cache", identity, state, certificate)


def test_warm_cache_rejects_equivalence_of_the_wrong_metric_schema(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    state = tmp_path / "warm.xml.gz"
    state.write_bytes(b"sumo-state")
    metrics = {"unrelated": 1}
    wrong_schema = compare_decision_metrics(
        identity, metrics, metrics, metric_schema="unrelated_metrics_v1")

    with pytest.raises(ValueError, match="equivalence"):
        store_warm_state(
            tmp_path / "cache", identity, state, wrong_schema)


def test_warm_cache_rejects_a_looser_global_tolerance(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    state = tmp_path / "warm.xml.gz"
    state.write_bytes(b"sumo-state")
    loose = compare_decision_metrics(
        identity,
        {"time_loss_s": 1.0},
        {"time_loss_s": 1.5},
        absolute_tolerance=1.0,
    )
    assert loose.status == "pass"

    with pytest.raises(ValueError, match="equivalence"):
        store_warm_state(tmp_path / "cache", identity, state, loose)


def test_cache_miss_never_overwrites_destination(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    destination = tmp_path / "existing.xml.gz"
    destination.write_bytes(b"keep-me")

    lookup = restore_warm_state(
        tmp_path / "empty-cache", identity, destination)

    assert lookup.hit is False
    assert lookup.reason == "manifest_missing_or_invalid"
    assert destination.read_bytes() == b"keep-me"


def test_tampered_state_or_manifest_is_invalidated(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    state = tmp_path / "warm.xml.gz"
    state.write_bytes(b"original")
    cache = tmp_path / "cache"
    entry = store_warm_state(cache, identity, state, _certificate(identity))

    (entry / STATE_FILENAME).write_bytes(b"tampered")
    lookup = restore_warm_state(
        cache, identity, tmp_path / "restored.xml.gz")
    assert lookup.hit is False
    assert lookup.reason == "state_digest_mismatch"

    # Repair the bytes, then falsify provenance in the manifest.
    (entry / STATE_FILENAME).write_bytes(b"original")
    manifest_path = entry / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["identity"]["demand_build_id"] = "other-demand"
    manifest_path.write_text(json.dumps(manifest))
    lookup = restore_warm_state(
        cache, identity, tmp_path / "restored.xml.gz")
    assert lookup.hit is False
    assert lookup.reason == "identity_mismatch"


def test_mismatched_runtime_identity_cannot_restore_an_existing_entry(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    state = tmp_path / "warm.xml.gz"
    state.write_bytes(b"state")
    cache = tmp_path / "cache"
    store_warm_state(cache, identity, state, _certificate(identity))
    other_platform = replace(identity, platform_id="different-platform")

    lookup = restore_warm_state(
        cache, other_platform, tmp_path / "restored.xml.gz")

    assert lookup.hit is False
    assert lookup.key != identity.content_key


def test_sumo_state_arguments_always_save_rng_and_high_precision(tmp_path):
    state = tmp_path / "state.xml.gz"
    save = save_state_arguments(state, warmup_end_s=3600)
    state.write_bytes(b"state")
    load = load_state_arguments(state)

    assert save == [
        "--save-state.times", "3600",
        "--save-state.files", str(state.resolve()),
        "--save-state.rng", "true",
        "--save-state.precision", "16",
    ]
    assert load == ["--load-state", str(state.resolve())]


def test_metric_comparison_supports_tolerance_but_not_schema_drift(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")

    within = compare_decision_metrics(
        identity, {"time_loss": 10.0}, {"time_loss": 10.00001},
        absolute_tolerance=0.001)
    changed_shape = compare_decision_metrics(
        identity, {"time_loss": 10.0}, {"delay": 10.0},
        absolute_tolerance=1.0)

    assert within.status == "pass"
    assert within.max_absolute_delta == pytest.approx(0.00001)
    assert changed_shape.status == "fail"
    assert "keys differ" in changed_shape.mismatch


def test_field_tolerance_cannot_mask_primary_metric_or_count_drift(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    expected = {
        "entered": 10,
        "time_loss_s": 5.0,
        "travel_time_s": 20.0,
    }
    travel_only = compare_decision_metrics(
        identity,
        expected,
        {**expected, "travel_time_s": 20.23},
        absolute_tolerance=0.0,
        field_tolerances={"travel_time_s": 1.0},
    )
    time_loss_changed = compare_decision_metrics(
        identity,
        expected,
        {**expected, "time_loss_s": 5.01},
        absolute_tolerance=0.0,
        field_tolerances={"travel_time_s": 1.0},
    )
    count_changed = compare_decision_metrics(
        identity,
        expected,
        {**expected, "entered": 11},
        absolute_tolerance=0.0,
        field_tolerances={"travel_time_s": 1.0},
    )

    assert travel_only.status == "pass"
    assert travel_only.field_tolerances == {"travel_time_s": 1.0}
    assert time_loss_changed.status == "fail"
    assert "time_loss_s" in time_loss_changed.mismatch
    assert count_changed.status == "fail"
    assert "entered" in count_changed.mismatch


def test_matched_baseline_cache_covers_window_edges_and_objective(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    certificate = _certificate(identity)
    baseline = create_baseline_evidence_identity(
        identity,
        analysis_start_s=3600,
        analysis_end_s=7200,
        affected_edges=("edge-b", "edge-a"),
        objective_profile="robust_time_loss",
    )
    metrics = {"time_loss_s": 12.5, "intervals": [{"begin_s": 3600}]}
    cache = tmp_path / "baseline-cache"

    store_baseline_evidence(cache, baseline, metrics, certificate)
    lookup = restore_baseline_evidence(cache, baseline)

    assert lookup.hit is True
    assert lookup.metrics == metrics
    assert baseline.affected_edges == ("edge-a", "edge-b")
    assert create_baseline_evidence_identity(
        identity,
        analysis_start_s=3600,
        analysis_end_s=8100,
        affected_edges=("edge-a", "edge-b"),
        objective_profile="robust_time_loss",
    ).content_key != baseline.content_key
    assert create_baseline_evidence_identity(
        identity,
        analysis_start_s=3600,
        analysis_end_s=7200,
        affected_edges=("edge-a", "edge-c"),
        objective_profile="robust_time_loss",
    ).content_key != baseline.content_key


def test_baseline_cache_rejects_wrong_or_failed_warm_proof(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    baseline = create_baseline_evidence_identity(
        identity,
        analysis_start_s=3600,
        analysis_end_s=7200,
        affected_edges=("edge-a",),
        objective_profile="robust_time_loss",
    )
    failed = _certificate(identity, {
        "time_loss_s": 999.0,
        "throughput": 42,
        "recovery": {"status": "recovered", "time_s": 900},
    })

    with pytest.raises(ValueError, match="equivalence"):
        store_baseline_evidence(
            tmp_path / "cache", baseline, {"time_loss_s": 1}, failed)


def test_tampered_baseline_metrics_are_invalidated(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    baseline = create_baseline_evidence_identity(
        identity,
        analysis_start_s=3600,
        analysis_end_s=7200,
        affected_edges=("edge-a",),
        objective_profile="robust_time_loss",
    )
    cache = tmp_path / "cache"
    entry = store_baseline_evidence(
        cache, baseline, {"time_loss_s": 1}, _certificate(identity))
    (entry / "metrics.json").write_text('{"time_loss_s":999}\n')

    lookup = restore_baseline_evidence(cache, baseline)

    assert lookup.hit is False
    assert lookup.reason == "metrics_digest_mismatch"


def test_same_baseline_identity_cannot_hide_different_metrics(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    baseline = create_baseline_evidence_identity(
        identity,
        analysis_start_s=3600,
        analysis_end_s=7200,
        affected_edges=("edge-a",),
        objective_profile="robust_time_loss",
    )
    cache = tmp_path / "cache"
    certificate = _certificate(identity)
    store_baseline_evidence(
        cache, baseline, {"time_loss_s": 1}, certificate)

    with pytest.raises(ValueError, match="semantic mismatch"):
        store_baseline_evidence(
            cache, baseline, {"time_loss_s": 2}, certificate)


def test_baseline_metric_schema_must_match_equivalence_certificate(tmp_path):
    identity, _, _, _ = _identity(tmp_path / "inputs")
    baseline = create_baseline_evidence_identity(
        identity,
        analysis_start_s=3600,
        analysis_end_s=7200,
        affected_edges=("edge-a",),
        objective_profile="robust_time_loss",
        metric_schema="another_schema",
    )

    with pytest.raises(ValueError, match="equivalence"):
        store_baseline_evidence(
            tmp_path / "cache", baseline, {"time_loss_s": 1},
            _certificate(identity))
