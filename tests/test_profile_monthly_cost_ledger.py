"""Phase 4 producer binding and append-only publication contracts."""

import json
from pathlib import Path

import pytest

from tools import profile_monthly_cost_ledger as profile


def _stable_binding(monkeypatch, tmp_path: Path):
    spec = tmp_path / "spec.json"
    spec.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(profile, "producer_source_manifest",
                        lambda: {"producer.py": "source-a"})
    monkeypatch.setattr(profile, "producer_runtime_manifest",
                        lambda: {"python": "runtime-a"})
    return profile.capture_producer_binding(
        bound_spec={"path": str(spec), "search_content_key": "spec-a"})


def test_binding_rejects_sources_changed_during_a_profile(monkeypatch, tmp_path):
    binding = _stable_binding(monkeypatch, tmp_path)
    monkeypatch.setattr(profile, "producer_source_manifest",
                        lambda: {"producer.py": "source-b"})

    with pytest.raises(RuntimeError, match="sources changed during profiling"):
        profile.validate_producer_binding(binding)


def test_binding_rejects_a_bound_spec_changed_during_a_profile(
        monkeypatch, tmp_path):
    binding = _stable_binding(monkeypatch, tmp_path)
    Path(binding["bound_spec"]["path"]).write_text(
        '{"changed": true}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="bound spec changed"):
        profile.validate_producer_binding(binding)


def test_publish_rechecks_binding_and_leaves_no_misbound_evidence(
        monkeypatch, tmp_path):
    binding = _stable_binding(monkeypatch, tmp_path)
    destination = tmp_path / "profile.json"
    monkeypatch.setattr(profile, "producer_source_manifest",
                        lambda: {"producer.py": "source-b"})

    with pytest.raises(RuntimeError, match="sources changed during profiling"):
        profile._publish_evidence(destination, {"bindings": binding})
    assert not destination.exists()


def test_publish_is_atomic_and_refuses_to_overwrite(monkeypatch, tmp_path):
    binding = _stable_binding(monkeypatch, tmp_path)
    destination = tmp_path / "profile.json"
    record = {"bindings": binding, "status": "PASS"}

    profile._publish_evidence(destination, record)
    first = destination.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        profile._publish_evidence(destination, {**record, "status": "changed"})

    assert destination.read_bytes() == first
    assert json.loads(first)["status"] == "PASS"


def test_resolver_measurement_is_scoped_and_restores_production_methods():
    from traffic_sim.ops import io_phases
    from traffic_sim.simulation import disruption

    original_init = disruption.ClosureRouteResolver.__init__
    original_resolve = disruption.ClosureRouteResolver.resolve
    collector = io_phases.PhaseCollector()
    adjacency = {"a": ("b",), "b": ()}

    with profile._observe_resolver_activity(collector):
        resolver = disruption.ClosureRouteResolver(
            adjacency, {"a": 1.0, "b": 1.0}, None, frozenset({"b"}))
        resolver.resolve(("a", "b"), 0.0, None, None)

    report = collector.report()
    assert report["counters"]["closure_resolver_instances"] == 1
    assert report["counters"]["closure_resolve_calls"] == 1
    assert report["unique_counts"]["closure_route_edges"] == 1
    assert disruption.ClosureRouteResolver.__init__ is original_init
    assert disruption.ClosureRouteResolver.resolve is original_resolve


def test_resolver_measurement_restores_methods_after_failure():
    from traffic_sim.ops import io_phases
    from traffic_sim.simulation import disruption

    original_init = disruption.ClosureRouteResolver.__init__
    collector = io_phases.PhaseCollector()

    with pytest.raises(RuntimeError, match="profile failed"):
        with profile._observe_resolver_activity(collector):
            raise RuntimeError("profile failed")

    assert disruption.ClosureRouteResolver.__init__ is original_init
