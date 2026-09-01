from __future__ import annotations

import json

import pytest

from tools import build_window_cost_index as builder
from traffic_sim.simulation.window_cost_index import (
    WindowCostIndex,
    WindowCostIndexError,
    load_index,
)
from traffic_sim.simulation.disruption import build_parsed_window_cost_index


def _records(unit="unit-a", schedule="schedule-a"):
    return {
        unit: {
            "schedule_id": schedule,
            "records": [
                {"demand_variant": "q10", "value": 10},
                {"demand_variant": "q50", "value": 50},
                {"demand_variant": "q90", "value": 90},
            ],
        }
    }


def test_index_round_trips_and_requires_field_identical_oracle():
    index = WindowCostIndex(bound_identity={"source": "digest-a"},
                            records=_records(), preparation_time_s=1.25)
    restored = WindowCostIndex.from_dict(
        index.to_dict(), expected_identity={"source": "digest-a"},
        expected_daily_units=1, expected_variant_records=3)
    assert restored.lookup("unit-a", "schedule-a")[1]["value"] == 50
    comparison = restored.compare_oracle(_records())
    assert comparison["oracle_complete"] is True
    assert comparison["field_identical"] is True
    assert comparison["indexed_variant_records"] == 3


def test_index_rejects_partial_stale_and_swapped_state():
    with pytest.raises(WindowCostIndexError, match="q10/q50/q90"):
        WindowCostIndex(bound_identity={"source": "digest-a"}, records={
            "unit-a": {"schedule_id": "schedule-a", "records": [
                {"demand_variant": "q10"},
            ]}
        })
    index = WindowCostIndex(bound_identity={"source": "digest-a"},
                            records=_records())
    with pytest.raises(WindowCostIndexError, match="stale"):
        WindowCostIndex.from_dict(index.to_dict(),
                                  expected_identity={"source": "digest-b"})
    with pytest.raises(WindowCostIndexError, match="swapped"):
        index.lookup("unit-a", "another-schedule")


def test_index_detects_a_single_field_change_in_the_full_oracle():
    index = WindowCostIndex(bound_identity={"source": "digest-a"},
                            records=_records())
    oracle = _records()
    oracle["unit-a"]["records"][2]["value"] = 91
    comparison = index.compare_oracle(oracle)
    assert comparison["oracle_complete"] is True
    assert comparison["field_identical"] is False
    assert comparison["mismatch_count"] == 1


def test_load_index_validates_the_bound_identity_and_population(tmp_path):
    index = WindowCostIndex(bound_identity={"source": "digest-a"},
                            records=_records(), preparation_time_s=1.25)
    path = tmp_path / "window-cost-index.json"
    path.write_text(json.dumps(index.to_dict()), encoding="utf-8")

    restored = load_index(
        path,
        expected_identity={"source": "digest-a"},
        expected_daily_units=1,
        expected_variant_records=3,
    )
    assert restored.lookup("unit-a", "schedule-a")[0]["value"] == 10
    with pytest.raises(WindowCostIndexError, match="stale"):
        load_index(
            path,
            expected_identity={"source": "digest-b"},
            expected_daily_units=1,
            expected_variant_records=3,
        )


def test_structural_window_index_reuses_route_grouping_and_detours(monkeypatch):
    adjacency = {"A": ["B"], "B": ["C"], "C": []}
    edge_time = {"A": 1.0, "B": 1.0, "C": 1.0}
    edge_length = {"A": 10.0, "B": 10.0, "C": 10.0}
    calls = []

    from traffic_sim.simulation import disruption
    original = disruption.grouped_path_costs

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(disruption, "grouped_path_costs", counted)
    index = build_parsed_window_cost_index(
        ((("A", "B", "C"), 0.0),), {"B"}, edge_time, edge_length,
        adjacency=adjacency)
    first = index.disruption(({"begin_s": 0.0, "end_s": 1.5},))
    second = index.disruption(({"begin_s": 2.0, "end_s": 3.5},))

    assert first["vehicles_affected"] == 1
    assert second["vehicles_affected"] == 0
    assert len(calls) == 4  # baseline/detour x time/length, once at build


def test_index_rejects_non_finite_preparation_time():
    with pytest.raises(WindowCostIndexError, match="finite"):
        WindowCostIndex(bound_identity={"source": "digest-a"},
                        records=_records(), preparation_time_s=float("nan"))


def test_phase5_builder_rejects_tampered_profile_before_opening_inputs(
        tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text("{}", encoding="utf-8")
    profile = {
        "schema": "monthly_cost_ledger_profile_v1",
        "bindings": {
            "bound_spec": {"path": str(spec_path)},
            "bound_spec_sha256": builder.sha256_file(spec_path),
        },
    }
    profile["bindings"]["producer_source_manifest"] = (
        builder.producer_source_manifest())
    profile["bindings"]["producer_runtime_manifest"] = (
        builder.producer_runtime_manifest())
    policy_path = builder.ROOT / "validation" / "monthly_search_policy_v3.json"
    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy
    profile["bindings"]["policy"] = {
        "path": str(policy_path),
        "sha256": builder.sha256_file(policy_path),
        "content_key": MonthlySearchPolicy.from_dict(
            json.loads(policy_path.read_text(encoding="utf-8"))).content_key,
    }
    profile["content_key"] = builder._digest(profile)
    builder._validate_profile_binding(profile, tmp_path / "profile.json")

    profile["bindings"]["bound_spec_sha256"] = "0" * 64
    with pytest.raises(WindowCostIndexError, match="content key mismatch"):
        builder._validate_profile_binding(profile, tmp_path / "profile.json")


def test_raw_index_path_never_constructs_a_cache_backed_provider(
        tmp_path, monkeypatch):
    class Schedule:
        schedule_id = "schedule-a"

        def to_dict(self):
            return {"schedule_id": self.schedule_id}

    class Resolver:
        def __init__(self, *args, **kwargs):
            pass

        def _required(self, schedule):
            return schedule

    class Oracle:
        def load(self, identity):
            return _records()["unit-a"]["records"]

    seen = []

    class Provider:
        def __init__(self, spec, *, archive, network, cache):
            seen.append(cache)

        def disruption(self, schedule):
            return tuple(_records()["unit-a"]["records"])

        def cache_identity(self, schedule):
            return {"schedule": schedule.schedule_id}

        def timing_snapshot(self):
            return {"xml_parse": 1.0}

    monkeypatch.setattr(builder, "EXPECTED_DAILY_UNITS", 1)
    monkeypatch.setattr(builder, "iter_closure_schedules",
                        lambda spec: (Schedule(),))
    monkeypatch.setattr(builder, "daily_unit_records",
                        lambda spec, parent: (("unit-a", {"unit": "a"},
                                               lambda: Schedule()),))
    monkeypatch.setattr(builder, "MonthlyDemandResolverRunner", Resolver)
    monkeypatch.setattr(builder, "find_demand_archives",
                        lambda runs_root, required: [{"archive": tmp_path}])
    monkeypatch.setattr(builder, "ArchiveDisruptionProvider", Provider)
    monkeypatch.setattr(builder, "NetworkCostModel", lambda: object())

    indexed, oracle, measurement = builder._raw_index_records(
        object(), runs_root=tmp_path, oracle_cache=Oracle())
    assert seen == [None]
    assert indexed == oracle
    assert measurement["raw_input_algorithm"] == (
        "ArchiveDisruptionProvider(cache=None)")
