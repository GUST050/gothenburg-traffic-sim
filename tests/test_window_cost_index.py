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


def _adoption_spec():
    """A real two-day search spec, so daily_unit_records is the real one."""
    from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
    return ClosureSearchSpec(
        search_id="wci-adoption",
        directed_edges=("edge-a",),
        demand_build_id="forecast-2027",
        source="forecast",
        permitted_date_start="2027-07-15",
        permitted_date_end="2027-07-16",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("06:00", "08:00"),
        allowed_weekdays=(3, 4),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        objective_profile="displaced_vehicles_and_detour_v1",
    )


def _adoption_index(spec, parent):
    """Index records keyed exactly as the real contract yields them."""
    records = {}
    for unit_id, _identity, build_schedule in builder.daily_unit_records(
            spec, parent):
        schedule = build_schedule()
        records[str(unit_id)] = {
            "schedule_id": schedule.schedule_id,
            "records": [{
                "demand_variant": variant,
                "vehicles_affected": 2,
                "vehicles_considered": 10,
                "vehicles_no_detour": 0,
                "added_metres_total": 100.0,
                "added_vehicle_hours": 0.25,
            } for variant in ("q10", "q50", "q90")],
        }
    return WindowCostIndex(bound_identity={"source": "digest-a"},
                           records=records)


def test_indexed_ledger_source_follows_the_real_daily_unit_records_contract():
    """`daily_unit_records` yields (unit_id, identity, build_schedule).

    The adoption path read the identity dict as if it were a ClosureSchedule,
    so the 2026-09-16 full-month run crashed with `AttributeError: 'dict'
    object has no attribute 'schedule_id'` after 8 h 41 m — with the oracle
    already proved and the index written, but ranking, winner and stop proof
    never compared.
    """
    spec = _adoption_spec()
    parents = tuple(builder.iter_closure_schedules(spec))
    assert parents, "the fixture spec must enumerate at least one parent"
    parent = parents[0]
    index = _adoption_index(spec, parent)
    source = builder._IndexedLedgerSource(spec, index)

    cost = source.parent_cost(parent)

    assert source.lookups == len(index.records)
    assert cost.candidate_id == parent.schedule_id
    assert set(cost.daily_unit_ids) == set(index.records)
    assert len(cost.per_variant) == 3


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


def _shared_archive_spec():
    """Several parents on one date: many daily units, ONE demand build key."""
    from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
    return ClosureSearchSpec(
        search_id="wci-read-counts",
        directed_edges=("edge-a",),
        demand_build_id="forecast-2027",
        source="forecast",
        permitted_date_start="2027-07-15",
        permitted_date_end="2027-07-15",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("06:00", "10:00"),
        allowed_weekdays=(3,),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        objective_profile="displaced_vehicles_and_detour_v1",
    )


class _StubProvider:
    """No routes, no network: this test measures archive resolution only."""

    inputs = None

    def __init__(self, spec, *, archive, network, cache):
        assert cache is None
        self.archive = archive

    def disruption(self, schedule):
        return tuple({
            "demand_variant": variant,
            "vehicles_affected": 1,
            "vehicles_considered": 5,
            "vehicles_no_detour": 0,
            "added_metres_total": 10.0,
            "added_vehicle_hours": 0.1,
        } for variant in ("q10", "q50", "q90"))

    def cache_identity(self, schedule):
        return {"schedule": schedule.schedule_id}

    def identity(self):
        return {"schema": "stub_provider_v1"}

    def timing_snapshot(self):
        return {"xml_parse": 0.0}


class _StubOracle:
    def __init__(self, provider_records):
        self._records = provider_records

    def load(self, identity):
        return self._records


def test_raw_index_records_builds_the_archive_index_once(tmp_path, monkeypatch):
    """Count the reads: the raw path re-resolved one archive per daily unit.

    The 2026-09-16 whole-month attempt spent 31,271.161 s in preparation
    alone, 4.27x the 7,320.348 s ledger baseline, and sampling showed the
    time going into JSON float parsing of the archives' large
    `demand_meta.json`. `find_demand_archives` was called once per daily unit
    with no shared index, so every call rebuilt the archive index from
    metadata content. The contract is one FULL validation per unique demand
    contract: all 30 of a month's archives are proved in full, once each,
    rather than the same 30 being re-proved for every time window.
    """
    from tests.test_monthly_demand import (_archive, _qualified_manifest_for,
                                           _with_catalog_keys)
    from traffic_sim.ops import io_phases
    from traffic_sim.simulation import monthly_demand

    spec = _shared_archive_spec()
    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=tmp_path, build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="subhour-phase5-raw-index")
    schedules = {}
    for parent in builder.iter_closure_schedules(spec):
        for unit_id, _identity, build_schedule in builder.daily_unit_records(
                spec, parent):
            schedules.setdefault(str(unit_id), build_schedule())
    required = {resolver._required(schedule).build_key: resolver._required(schedule)
                for schedule in schedules.values()}
    assert len(schedules) > 1 and len(required) == 1, (
        "the fixture must share one archive across several daily units")
    build_spec = next(iter(required.values()))

    archive = _archive(tmp_path, build_spec, "demand-shared",
                       finished_at="2027-01-01T00:00:00Z")
    _with_catalog_keys(archive, {"weekday": "wd-key"}, ["weekday"])
    record = monthly_demand.validate_demand_archive(archive, build_spec)
    manifest = _qualified_manifest_for(
        record,
        network_sha256=monthly_demand.sha256_file(
            monthly_demand._PHASE_D_DEFAULT_NET_PATH),
        catalog_keys={"weekday": "wd-key", "weekend": "we-key"})

    # Self-calibrating: measure ONE index build and ONE complete lookup
    # (validation plus the qualified-manifest catalog re-read) separately, so
    # the expectation below is arithmetic rather than a guessed constant.
    index_probe = io_phases.PhaseCollector()
    with io_phases.observe(index_probe):
        shared_index = monthly_demand._archives_for_build_key(tmp_path)
    index_build_reads = index_probe.report()["counters"]["archive_json_read"]

    lookup_probe = io_phases.PhaseCollector()
    with io_phases.observe(lookup_probe):
        monthly_demand.find_demand_archives(
            tmp_path, build_spec, qualified_manifest=manifest,
            _archive_index=shared_index)
    reads_per_lookup = lookup_probe.report()["counters"]["archive_json_read"]

    monkeypatch.setattr(builder, "EXPECTED_DAILY_UNITS", len(schedules))
    monkeypatch.setattr(builder, "ArchiveDisruptionProvider", _StubProvider)
    monkeypatch.setattr(builder, "NetworkCostModel", lambda: object())
    oracle = _StubOracle(_StubProvider(
        spec, archive=archive, network=None, cache=None).disruption(None))

    collector = io_phases.PhaseCollector()
    with io_phases.observe(collector):
        indexed, oracle_records, measurement = builder._raw_index_records(
            spec, runs_root=tmp_path, oracle_cache=oracle,
            qualified_demand_manifest=manifest)

    report = collector.report()
    counters = report["counters"]
    assert len(indexed) == len(schedules) == len(oracle_records)
    assert report["unique_counts"]["archive_validated_path"] == 1
    # The contract is per UNIQUE demand contract, not per time window: all 30
    # archives of a month are proved in full, once each, instead of the same
    # 30 being re-proved for each of 1,950 daily units.
    assert counters["archive_validate"] == len(required)
    assert counters["archive_validate"] < len(schedules)
    # The index is derived from metadata content ONCE for the whole operation.
    assert counters["archive_index_build"] == 1
    assert counters["archive_json_read"] == (
        reads_per_lookup * len(required) + index_build_reads)
    # Every daily unit is still bound to a verified archive descriptor.
    assert measurement["daily_units"] == len(schedules)
    assert len(measurement["provider_identities"]) == len(schedules)


def _resume_fixture(tmp_path, monkeypatch, *, ledger_key_drift=False,
                    preparation_time_s=31271.161, source_drift=True):
    """A tiny but complete Phase 4/5 fixture: spec, ledger, index, profile.

    Everything the resume path must verify is real here -- the index file and
    its content key, the population, the bound identity, the baseline ledger
    and the adoption replay. Only the archive/oracle seams are stubbed, since
    this test is about proving an EXISTING index rather than building one.
    """
    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy

    spec = _shared_archive_spec()
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")
    parents = tuple(builder.iter_closure_schedules(spec))
    records = {}
    for parent in parents:
        for unit_id, _identity, build_schedule in builder.daily_unit_records(
                spec, parent):
            records.setdefault(str(unit_id), {
                "schedule_id": build_schedule().schedule_id,
                "records": [{
                    "demand_variant": variant,
                    "vehicles_affected": 1,
                    "vehicles_considered": 5,
                    "vehicles_no_detour": 0,
                    "added_metres_total": 10.0,
                    "added_vehicle_hours": 0.1,
                } for variant in ("q10", "q50", "q90")],
            })
    monkeypatch.setattr(builder, "EXPECTED_DAILY_UNITS", len(records))
    monkeypatch.setattr(builder, "EXPECTED_VARIANT_RECORDS", len(records) * 3)
    monkeypatch.setattr(builder, "EXPECTED_PARENTS", len(parents))

    class _BaselineSource:
        """The unindexed path: same numbers, independently aggregated."""

        def identity(self):
            return {"schema": "baseline_source_v1"}

        def parent_cost(self, parent):
            daily, unit_ids = [], []
            for unit_id, _identity, build in builder.daily_unit_records(
                    spec, parent):
                daily.append(tuple(dict(item) for item
                                   in records[str(unit_id)]["records"]))
                unit_ids.append(str(unit_id))
            return builder.ParentCost(
                candidate_id=parent.schedule_id,
                cost=builder.parent_closure_cost(parent.schedule_id, daily),
                per_variant=builder.sum_daily_disruption(daily),
                daily_unit_ids=tuple(unit_ids))

    baseline = builder.build_cost_ledger(spec, parents, _BaselineSource())
    ledger_dict = baseline.to_dict()
    (tmp_path / "cost-ledger.json").write_text(
        json.dumps(ledger_dict), encoding="utf-8")

    policy_path = builder.ROOT / "validation" / "monthly_search_policy_v3.json"
    qualified_path = tmp_path / "qualified.json"
    qualified = {"status": "PASS", "content_key": "q" * 64,
                 "evidence_id": "qualified-fixture"}
    qualified_path.write_text(json.dumps(qualified), encoding="utf-8")
    monkeypatch.setattr(builder, "validate_qualified_demand_manifest_shape",
                        lambda manifest: None)

    profile = {
        "schema": "monthly_cost_ledger_profile_v1",
        "wall_time_s": 1000.0,
        "ledger_content_key": ledger_dict["content_key"],
        "phase_5_decision": "TRIGGERED",
        "population_complete": True,
        "phase_timing_complete": True,
        "sumo_zero_launch_gate": True,
        "runs_root": str(tmp_path),
        "cache": {"root": str(tmp_path)},
        "bound_spec": {"path": str(spec_path),
                       "search_content_key": spec.content_key},
        "qualified_demand_manifest": {
            "path": str(qualified_path),
            "sha256": builder.sha256_file(qualified_path),
            "content_key": qualified["content_key"],
            "evidence_id": qualified["evidence_id"]},
        "bindings": {
            "bound_spec": {"path": str(spec_path),
                           "search_content_key": spec.content_key},
            "bound_spec_sha256": builder.sha256_file(spec_path),
            "producer_source_manifest": builder.producer_source_manifest(),
            "producer_runtime_manifest": builder.producer_runtime_manifest(),
            "policy": {
                "path": str(policy_path),
                "sha256": builder.sha256_file(policy_path),
                "content_key": MonthlySearchPolicy.from_dict(json.loads(
                    policy_path.read_text(encoding="utf-8"))).content_key},
        },
    }
    profile["content_key"] = builder._digest(profile)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    bound_identity = {
        "schema": "subhour-phase5-window-cost-index-bound-v1",
        "search_content_key": spec.content_key,
        "ledger_content_key": ("drifted" if ledger_key_drift
                               else ledger_dict["content_key"]),
        "provider_identity": ledger_dict.get("provider_identity", {}),
        "source_profile_content_key": profile["content_key"],
        "qualified_demand_manifest": dict(profile["qualified_demand_manifest"]),
        "policy_content_key": profile["bindings"]["policy"]["content_key"],
        "producer_source_manifest": builder.producer_source_manifest(),
        "producer_runtime_manifest": builder.producer_runtime_manifest(),
        "raw_input_algorithm": "ArchiveDisruptionProvider(cache=None)",
        "raw_input_sources": {
            relative: ("0" * 64 if source_drift
                       else builder.sha256_file(builder.ROOT / relative))
            for relative in ("tools/build_window_cost_index.py",
                             "traffic_sim/simulation/window_cost_index.py")
        },
        "provider_identities": {unit: {"schema": "stub_provider_v1"}
                                for unit in records},
    }
    index = WindowCostIndex(bound_identity=bound_identity, records=records,
                            preparation_time_s=preparation_time_s)
    index_path = tmp_path / "window-cost-index.json"
    index_path.write_text(json.dumps(index.to_dict()), encoding="utf-8")

    class _ResumeOracle:
        def __init__(self, root):
            self.root = root

        def load(self, identity):
            return tuple(dict(item) for item
                         in records[identity["unit_id"]]["records"])

    class _ResumeProviders:
        """Stands in for archive resolution; identity keys stay per unit."""

        def __init__(self, spec, *, runs_root, qualified_demand_manifest):
            self.archives = set()

        def identity_for(self, unit_id, identity, schedule):
            assert schedule.schedule_id, "the built schedule reaches the oracle"
            return {"unit_id": str(unit_id)}

        def provider_identity_for(self, unit_id, identity, schedule):
            return {"schema": "stub_provider_v1"}

    monkeypatch.setattr(builder, "DailyCostCache", _ResumeOracle)
    monkeypatch.setattr(builder, "_ResumeOracleProviders", _ResumeProviders)
    return profile_path, index_path, ledger_dict, len(records), len(parents)


def test_resume_proves_an_existing_index_without_rebuilding_it(
        tmp_path, monkeypatch):
    """Prove the written index instead of paying 8 h 41 m to rebuild it.

    The stored preparation time is the honest cost of this index and must
    still be counted: a cheap resume replay may not rewrite history so that
    WindowCostIndex looks fast.
    """
    profile_path, index_path, ledger, units, parents = _resume_fixture(
        tmp_path, monkeypatch)
    evidence_out = tmp_path / "resume.json"

    record = builder.prove_existing_index(
        profile_path, index_path=index_path, evidence_out=evidence_out,
        evidence_id="resume-fixture")

    assert record["status"] == "NOT_ADOPTED"
    assert record["population"] == {
        "daily_units": units, "daily_variant_records": units * 3,
        "parent_schedules": parents}
    assert record["oracle"]["field_identical"] is True
    assert record["ledger_identical"] is True
    # Lookups count parent-to-unit relationships, not variant records: the
    # old invariant compared them against 5,850 and would have failed the
    # build path at the end of an eight-hour run.
    assert record["adoption"]["daily_unit_lookups"] == sum(
        len(item["daily_unit_ids"]) for item in ledger["costs"])
    assert record["adoption"]["distinct_daily_units"] == units
    # The ledger content key also digests diagnostic cache counters, which
    # legitimately differ between an indexed source and the baseline source.
    # The contract is the DECISION-bearing content in canonical order.
    assert record["ledger_comparison"]["decision_fields_identical"] is True
    assert record["ledger_comparison"]["compared_fields"] == [
        "candidate_id", "cost", "daily_unit_ids", "per_variant"]
    assert "cache_fields_identical" in record["ledger_comparison"]
    assert record["stored_preparation_time_s"] == 31271.161
    assert record["cold_index_end_to_end_time_s"] > 31271.161
    assert record["cold_benefit_s"] < 0
    assert record["rebuilt"] is False
    assert json.loads(evidence_out.read_text())["content_key"] == \
        record["content_key"]


def _build_path_fixture(tmp_path, monkeypatch, *, baseline_time_s,
                        drift_the_ledger=False):
    """Drive `build_from_profile` over a small but real population.

    `_bound_inputs` and `_raw_index_records` are the two seams stubbed here:
    everything the assertions are about -- index construction, the oracle
    comparison, persistence, the adoption replay, the ledger comparison and
    publication -- runs for real.
    """
    spec = _shared_archive_spec()
    parents = tuple(builder.iter_closure_schedules(spec))
    records = {}
    for parent in parents:
        for unit_id, _identity, build_schedule in builder.daily_unit_records(
                spec, parent):
            records.setdefault(str(unit_id), {
                "schedule_id": build_schedule().schedule_id,
                "records": [{
                    "demand_variant": variant,
                    "vehicles_affected": 1,
                    "vehicles_considered": 5,
                    "vehicles_no_detour": 0,
                    "added_metres_total": 10.0,
                    "added_vehicle_hours": 0.1,
                } for variant in ("q10", "q50", "q90")],
            })
    monkeypatch.setattr(builder, "EXPECTED_DAILY_UNITS", len(records))
    monkeypatch.setattr(builder, "EXPECTED_VARIANT_RECORDS", len(records) * 3)
    monkeypatch.setattr(builder, "EXPECTED_PARENTS", len(parents))

    class _BaselineSource:
        def identity(self):
            return {"schema": "baseline_source_v1"}

        def parent_cost(self, parent):
            daily, unit_ids = [], []
            for unit_id, _identity, _build in builder.daily_unit_records(
                    spec, parent):
                daily.append(tuple(dict(item) for item
                                   in records[str(unit_id)]["records"]))
                unit_ids.append(str(unit_id))
            return builder.ParentCost(
                candidate_id=parent.schedule_id,
                cost=builder.parent_closure_cost(parent.schedule_id, daily),
                per_variant=builder.sum_daily_disruption(daily),
                daily_unit_ids=tuple(unit_ids))

    ledger = builder.build_cost_ledger(spec, parents, _BaselineSource()).to_dict()
    if drift_the_ledger:
        # One parent priced differently: the index would then describe a
        # different month than the baseline it claims to accelerate.
        ledger["costs"][0]["cost"]["added_vehicle_hours"] += 1.0

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps({"content_key": "p" * 64}),
                            encoding="utf-8")
    bound = {
        "profile": {
            "content_key": "p" * 64,
            "ledger_content_key": ledger["content_key"],
            "bindings": {"policy": {"content_key": "policy-key"},
                         "producer_source_manifest": {},
                         "producer_runtime_manifest": {}},
        },
        "profile_path": profile_path,
        "qualified_ref": {"path": "q.json", "sha256": "q" * 64,
                          "content_key": "q" * 64, "evidence_id": "qualified"},
        "qualified_manifest": {"status": "PASS"},
        "bound_spec": {"path": str(tmp_path / "spec.json"),
                       "search_content_key": spec.content_key},
        "spec": spec,
        "ledger": ledger,
        "parent_unit_ids": set(records),
        "cache_root": tmp_path,
        "runs_root": tmp_path,
        "parents": parents,
        "baseline_time_s": baseline_time_s,
        "spec_population": builder.population_of(spec),
    }
    monkeypatch.setattr(builder, "_bound_inputs", lambda _path: bound)
    monkeypatch.setattr(
        builder, "_raw_index_records",
        lambda *_args, **_kwargs: (
            records,
            {unit: {"schedule_id": value["schedule_id"],
                    "records": [dict(item) for item in value["records"]]}
             for unit, value in records.items()},
            {"raw_input_algorithm": "ArchiveDisruptionProvider(cache=None)",
             "provider_identities": {unit: {"schema": "stub"}
                                     for unit in records},
             "daily_units": len(records),
             "daily_variant_records": len(records) * 3,
             "timings": {}}))
    monkeypatch.setattr(builder, "DailyCostCache", lambda root: object())
    # The stubbed bound inputs name no real profile or archives, so the
    # pre-publication drift check (tested on real files in
    # tests/test_wci_streaming.py) is recorded rather than run here.
    monkeypatch.setattr(builder, "_verify_before_publication",
                        lambda *_args, **_kwargs: None)
    return profile_path, ledger, len(records), len(parents)


def test_build_path_publishes_a_negative_benefit_instead_of_raising(
        tmp_path, monkeypatch):
    """An expensive, correct run must not throw its finding away.

    The 2026-09-16 attempt measured 31,271.161 s of preparation against a
    7,320.348 s baseline. That is a result, not an error: it belongs in
    append-only evidence, marked NOT_ADOPTED and never activated.
    """
    profile_path, _ledger, units, parents = _build_path_fixture(
        tmp_path, monkeypatch, baseline_time_s=0.000001)
    evidence_out = tmp_path / "negative.json"

    record = builder.build_from_profile(
        profile_path, index_out=tmp_path / "index-negative",
        evidence_out=evidence_out, evidence_id="build-negative")

    assert record["status"] == "NOT_ADOPTED"
    assert record["adopted"] is False
    assert record["cold_benefit_proven"] is False
    assert record["cold_benefit_s"] < 0
    assert record["population"] == {
        "daily_units": units, "daily_variant_records": units * 3,
        "parent_schedules": parents}
    published = json.loads(evidence_out.read_text())
    assert published["status"] == "NOT_ADOPTED"
    assert published["content_key"] == record["content_key"]


def test_build_path_passes_only_when_the_decision_ledger_matches(
        tmp_path, monkeypatch):
    profile_path, _ledger, _units, _parents = _build_path_fixture(
        tmp_path, monkeypatch, baseline_time_s=3600.0)

    record = builder.build_from_profile(
        profile_path, index_out=tmp_path / "index-clean",
        evidence_out=tmp_path / "clean.json", evidence_id="build-clean")

    assert record["cold_benefit_s"] > 0
    assert record["ledger_identical"] is True
    assert record["ledger_comparison"]["compared_fields"] == [
        "candidate_id", "cost", "daily_unit_ids", "per_variant"]
    assert record["status"] == "PASS"


def test_build_path_never_passes_a_ledger_that_prices_differently(
        tmp_path, monkeypatch):
    """A faster index that prices a different month is not an improvement."""
    profile_path, _ledger, _units, _parents = _build_path_fixture(
        tmp_path, monkeypatch, baseline_time_s=3600.0, drift_the_ledger=True)

    record = builder.build_from_profile(
        profile_path, index_out=tmp_path / "index-drifted",
        evidence_out=tmp_path / "drifted.json", evidence_id="build-drifted")

    assert record["cold_benefit_s"] > 0
    assert record["ledger_identical"] is False
    assert record["status"] == "NOT_ADOPTED"


def test_resume_providers_validate_each_build_key_once(tmp_path, monkeypatch):
    """Resume resolves archives per demand contract, not per time window.

    `_archive_for` ran a full `find_demand_archives` validation for every
    daily unit, so a month re-proved the same 30 archives 1,950 times.
    """
    from tests.test_monthly_demand import (_archive, _qualified_manifest_for,
                                           _with_catalog_keys)
    from traffic_sim.ops import io_phases
    from traffic_sim.simulation import monthly_demand

    spec = _shared_archive_spec()
    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=tmp_path, build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="subhour-phase5-resume-oracle")
    schedules = {}
    for parent in builder.iter_closure_schedules(spec):
        for unit_id, _identity, build_schedule in builder.daily_unit_records(
                spec, parent):
            schedules.setdefault(str(unit_id), build_schedule())
    build_spec = resolver._required(next(iter(schedules.values())))
    archive = _archive(tmp_path, build_spec, "demand-shared-resume",
                       finished_at="2027-01-01T00:00:00Z")
    _with_catalog_keys(archive, {"weekday": "wd-key"}, ["weekday"])
    record = monthly_demand.validate_demand_archive(archive, build_spec)
    manifest = _qualified_manifest_for(
        record,
        network_sha256=monthly_demand.sha256_file(
            monthly_demand._PHASE_D_DEFAULT_NET_PATH),
        catalog_keys={"weekday": "wd-key", "weekend": "we-key"})

    providers = builder._ResumeOracleProviders(
        spec, runs_root=tmp_path, qualified_demand_manifest=manifest)
    collector = io_phases.PhaseCollector()
    with io_phases.observe(collector):
        resolved = {unit: providers._archive_for(schedule)
                    for unit, schedule in schedules.items()}

    counters = collector.report()["counters"]
    assert len(schedules) > 1
    assert set(resolved.values()) == {archive.resolve()}
    assert counters["archive_validate"] == 1
    # The index is derived once in the constructor, never per lookup.
    assert counters.get("archive_index_build", 0) == 0


def test_resume_rejects_a_provider_identity_that_no_longer_matches(
        tmp_path, monkeypatch):
    """A matching key SET is not a matching identity.

    The archive, network and costing sources reconstructed today must be the
    ones the index recorded, or its oracle records describe another world.
    """
    profile_path, index_path, _ledger, _units, _parents = _resume_fixture(
        tmp_path, monkeypatch)

    class _DriftedProviders:
        def __init__(self, spec, *, runs_root, qualified_demand_manifest):
            pass

        def identity_for(self, unit_id, identity, schedule):
            return {"unit_id": str(unit_id)}

        def provider_identity_for(self, unit_id, identity, schedule):
            return {"schema": "stub_provider_v1",
                    "network": {"sha256": "f" * 64}}

    monkeypatch.setattr(builder, "_ResumeOracleProviders", _DriftedProviders)

    with pytest.raises(WindowCostIndexError, match="provider identity"):
        builder.prove_existing_index(
            profile_path, index_path=index_path,
            evidence_out=tmp_path / "identity-drift.json",
            evidence_id="resume-identity-drift")


def test_builder_source_drift_blocks_adoption_despite_a_positive_benefit(
        tmp_path, monkeypatch):
    """An index written by other builder bytes may not be adopted.

    Today's replay cannot vouch for yesterday's builder, so drift is
    disqualifying rather than merely reportable.
    """
    profile_path, index_path, _ledger, _units, _parents = _resume_fixture(
        tmp_path, monkeypatch, preparation_time_s=1.0, source_drift=True)

    record = builder.prove_existing_index(
        profile_path, index_path=index_path,
        evidence_out=tmp_path / "drift.json", evidence_id="resume-drift-gate")

    assert record["cold_benefit_s"] > 0
    assert record["ledger_identical"] is True
    assert record["builder_source_drift"]
    assert record["status"] == "NOT_ADOPTED"
    assert record["adopted"] is False


def test_a_clean_index_with_a_positive_benefit_reports_adoptable(
        tmp_path, monkeypatch):
    """The gate must be able to say yes, or it proves nothing when it says no."""
    profile_path, index_path, _ledger, _units, _parents = _resume_fixture(
        tmp_path, monkeypatch, preparation_time_s=1.0, source_drift=False)

    record = builder.prove_existing_index(
        profile_path, index_path=index_path,
        evidence_out=tmp_path / "clean.json", evidence_id="resume-clean")

    assert record["cold_benefit_s"] > 0
    assert record["builder_source_drift"] == {}
    assert record["status"] == "ADOPTABLE"
    # Reporting adoptability is not activation.
    assert record["adopted"] is False


def test_resume_refuses_an_index_bound_to_another_ledger(tmp_path, monkeypatch):
    profile_path, index_path, _ledger, _units, _parents = _resume_fixture(
        tmp_path, monkeypatch, ledger_key_drift=True)

    with pytest.raises(WindowCostIndexError, match="ledger"):
        builder.prove_existing_index(
            profile_path, index_path=index_path,
            evidence_out=tmp_path / "unused.json",
            evidence_id="resume-drift")


def test_raw_index_path_never_constructs_a_cache_backed_provider(
        tmp_path, monkeypatch):
    class Schedule:
        schedule_id = "schedule-a"
        # This double stands in for both the schedule and the DemandBuildSpec
        # the resolver returns, and archive resolution now deduplicates on the
        # demand contract's build key.
        build_key = "build-key-a"

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
