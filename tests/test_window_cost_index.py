from __future__ import annotations

import json

import pytest

from tools import build_window_cost_index as builder
from traffic_sim.simulation.window_cost_index import (
    WindowCostIndex,
    WindowCostIndexError,
    load_index,
)
from traffic_sim.simulation.disruption import (
    DestinationAccessResolver,
    build_parsed_window_cost_index,
)


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
    original = disruption.shortest_path_edges

    def counted(*args, **kwargs):
        calls.append(args[2:5])
        return original(*args, **kwargs)

    monkeypatch.setattr(disruption, "shortest_path_edges", counted)
    index = build_parsed_window_cost_index(
        ((("A", "B", "C"), 0.0),), {"B"}, edge_time, edge_length,
        adjacency=adjacency)
    first = index.disruption(({
        "edge_id": "B", "begin_s": 0.0, "end_s": 1.5},))
    after_first = len(calls)
    second = index.disruption(({
        "edge_id": "B", "begin_s": 2.0, "end_s": 3.5},))
    ended = index.disruption(({
        "edge_id": "B", "begin_s": 0.0, "end_s": 1.0},))

    assert first["vehicles_affected"] == 1
    assert second["vehicles_affected"] == 1
    assert ended["vehicles_affected"] == 0
    # Routing is the expensive work, memoised by (origin, destination,
    # banned). This fixture has no bypass, so the first window pays for the
    # one failed detour attempt and never needs a baseline (a severed vehicle
    # is not a delayed one)...
    assert after_first == 1
    assert len(calls) == len(set(calls))
    # ...and every later window over the same archive reuses both, which is
    # the property that makes a whole-month ledger affordable.
    assert len(calls) == after_first


def test_structural_window_index_relocates_a_closed_destination(tmp_path):
    network = tmp_path / "net.net.xml"
    network.write_text(
        "<net>"
        '<edge id="origin"><lane id="origin_0" length="10" speed="10" '
        'shape="0,0 10,0"/></edge>'
        '<edge id="closed"><lane id="closed_0" length="10" speed="10" '
        'shape="10,0 20,0"/></edge>'
        '<edge id="near"><lane id="near_0" length="30" speed="10" '
        'shape="15,1 25,1"/></edge>'
        '<connection from="origin" to="closed"/>'
        '<connection from="origin" to="near"/>'
        "</net>", encoding="utf-8")
    adjacency = {"origin": ["closed", "near"], "closed": [], "near": []}
    resolver = DestinationAccessResolver(
        network, permitted_edges=adjacency, radius_m=2.0)
    index = build_parsed_window_cost_index(
        ((('origin', 'closed'), 0.0, '8'),), {"closed"},
        {"origin": 1.0, "closed": 1.0, "near": 3.0},
        {"origin": 10.0, "closed": 10.0, "near": 30.0},
        adjacency=adjacency, destination_access=resolver)

    report = index.disruption(())

    assert report["vehicles_no_detour"] == 0
    assert report["vehicles_destination_relocated"] == 1
    assert report["added_metres_total"] == 1.0


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
                        lambda runs_root, required, **_kwargs: [{"archive": tmp_path}])
    monkeypatch.setattr(builder, "ArchiveDisruptionProvider", Provider)
    monkeypatch.setattr(builder, "NetworkCostModel", lambda: object())

    qualified = {"content_key": "qualified"}
    indexed, oracle, measurement = builder._raw_index_records(
        object(), runs_root=tmp_path, oracle_cache=Oracle(),
        qualified_demand_manifest=qualified)
    assert seen == [None]
    assert indexed == oracle
    assert measurement["raw_input_algorithm"] == (
        "ArchiveDisruptionProvider(cache=None)")
