"""Bounded NONZERO WindowCostIndex canary on an effect-eligible case.

The first current-code canary (``wci_current_code_canary_v1``) proved speed
and exactness only on the frozen profile spec, whose directed edge no vehicle
crosses. This canary takes the road the new automatic benchmark rule chooses
(``effect_eligibility_census_v1`` -> ``new_rule_choice[0]``), derives a
benchmark spec that differs from the frozen profile spec ONLY in its
``directed_edges`` (and therefore its search id and content key), and then:

1. computes an independent oracle for 1-3 build keys through the production
   per-file path (``ArchiveDisruptionProvider`` with a fresh
   ``DailyCostCache``), in its own process;
2. runs the unmodified production ``_raw_index_records`` for 1, 2 and 3 of
   those build keys, each in a fresh process, with only the unit population
   filtered at ``daily_unit_records`` (as in the first canary).

A real candidate spans five consecutive work dates, hence five build keys, so
no whole candidate fits in 1-3 keys. Costs are therefore compared per daily
unit, with the production reduction a candidate uses
(``parent_closure_cost`` and ``sum_daily_disruption`` over that unit's
records): candidate_id, cost, daily_unit_ids and per_variant must match the
oracle exactly.

Starts no SUMO, builds no demand or catalog, writes no index file, runs no
ledger, and writes only a fresh oracle cache and its own append-only evidence.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import wci_diag_common as common  # noqa: E402
from wci_current_code_canary_v1 import (  # noqa: E402
    _cpu,
    _timed_phase,
    _verified,
)

SCHEMA = "wci_effect_canary_v1"
KEY_COUNTS = (1, 2, 3)
PRODUCTION_SOURCES = (
    "tools/build_window_cost_index.py",
    "tools/cost_ordered_benchmark.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/effect_eligibility.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/window_cost_index.py",
    "traffic_sim/ops/io_phases.py",
    "run_scenario.py",
)
COST_FIELDS = ("added_vehicle_hours", "added_metres_total",
               "vehicles_affected", "vehicles_no_detour")


def derived_spec(spec, edge: str):
    """The frozen profile spec with only its directed edge replaced."""
    token = hashlib.sha256(edge.encode("utf-8")).hexdigest()[:10]
    return dataclasses.replace(
        spec, search_id=f"{spec.search_id}-effect-{token}",
        directed_edges=(edge,))


def _population(builder, bound, spec, wanted):
    """Production unit records of the wanted build keys, and their specs."""
    from traffic_sim.core.closure_calendar import iter_closure_schedules

    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=bound["runs_root"], build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="subhour-phase5-raw-index",
        qualified_demand_manifest=bound["qualified_manifest"])
    units: Dict[str, Any] = {}
    required: Dict[str, Any] = {}
    seen = set()
    for parent in iter_closure_schedules(spec):
        for unit_id, _identity, build in builder.daily_unit_records(
                spec, parent):
            unit_id = str(unit_id)
            if unit_id in seen:
                continue
            seen.add(unit_id)
            schedule = build()
            needed = resolver._required(schedule)
            if needed.build_key in wanted:
                units[unit_id] = (schedule, needed.build_key)
                required.setdefault(needed.build_key, needed)
    return units, required


def _unit_cost(schedule_id: str, unit_id: str, records):
    """The production candidate reduction applied to one unit's records."""
    from traffic_sim.simulation.deterministic_disruption import (
        parent_closure_cost, sum_daily_disruption)

    daily = [tuple(dict(item) for item in records)]
    return {
        "candidate_id": schedule_id,
        "cost": dataclasses.asdict(parent_closure_cost(schedule_id, daily)),
        "daily_unit_ids": [unit_id],
        "per_variant": [dict(item) for item in sum_daily_disruption(daily)],
    }


def oracle_worker(args) -> None:
    from tools import build_window_cost_index as builder
    from traffic_sim.simulation.deterministic_disruption import (
        ArchiveDisruptionProvider, DailyCostCache, NetworkCostModel)

    started = time.perf_counter()
    bound = builder._bound_inputs(args.profile)
    spec = derived_spec(bound["spec"], args.edge)
    wanted = set(json.loads(args.build_keys))
    units, required = _population(builder, bound, spec, wanted)
    index = builder._archives_for_build_key(bound["runs_root"])
    network = NetworkCostModel()
    cache = DailyCostCache(Path(args.oracle_root))
    archives, identities, digests = {}, {}, {}
    for key in sorted(wanted):
        matches = builder.find_demand_archives(
            bound["runs_root"], required[key],
            qualified_manifest=bound["qualified_manifest"],
            _archive_index=index)
        archive = Path(matches[0]["archive"]).resolve()
        archives[key] = str(archive)
        provider = ArchiveDisruptionProvider(
            spec, archive=archive, network=network, cache=cache)
        identities[key] = common.digest(dict(provider.identity()))
        for unit_id in sorted(u for u, (_s, k) in units.items() if k == key):
            schedule = units[unit_id][0]
            digests[unit_id] = common.digest({
                "schedule_id": schedule.schedule_id,
                "records": [dict(item)
                            for item in provider.disruption(schedule)]})
    print(json.dumps({
        "spec": {"search_id": spec.search_id,
                 "content_key": spec.content_key,
                 "directed_edges": list(spec.directed_edges)},
        "archives": archives,
        "provider_identity_digests": identities,
        "oracle_unit_digests": digests,
        "daily_units": len(digests),
        "cache_files": len(list(Path(args.oracle_root).rglob("*.json"))),
        "wall_s": time.perf_counter() - started,
        **common.process_memory(),
    }))


def _run_raw_index(builder, bound, spec, units, cache, phases):
    """Unmodified production raw path over the filtered unit population."""
    from traffic_sim.ops import io_phases

    production_records = builder.daily_unit_records

    def filtered_records(spec_arg, parent):
        for record in production_records(spec_arg, parent):
            if str(record[0]) in units:
                yield record

    collector = io_phases.PhaseCollector()
    production_expected = builder.EXPECTED_DAILY_UNITS
    builder.daily_unit_records = filtered_records
    builder.EXPECTED_DAILY_UNITS = len(units)
    try:
        cpu, started = _cpu(), time.perf_counter()
        with io_phases.observe(collector):
            result = builder._raw_index_records(
                spec, runs_root=bound["runs_root"], oracle_cache=cache,
                qualified_demand_manifest=bound["qualified_manifest"])
        _timed_phase(phases, "raw_index_records", cpu, started)
    finally:
        builder.daily_unit_records = production_records
        builder.EXPECTED_DAILY_UNITS = production_expected
    return result, collector.report()["counters"]


def _unit_checks(records, oracle, identities, units, cache):
    costs, cost_mismatches, identity_mismatches = {}, [], []
    for unit_id in sorted(records):
        schedule = units[unit_id][0]
        indexed = _unit_cost(schedule.schedule_id, unit_id,
                             records[unit_id]["records"])
        if indexed != _unit_cost(schedule.schedule_id, unit_id,
                                 oracle[unit_id]["records"]):
            cost_mismatches.append(unit_id)
        costs[unit_id] = indexed
        stored = json.loads(cache.path_for(cache.key(
            {**identities[unit_id], "schedule": schedule.to_dict()}
        )).read_text(encoding="utf-8"))["identity"]
        stored.pop("schedule")
        if stored != identities[unit_id]:
            identity_mismatches.append(unit_id)
    return costs, cost_mismatches, identity_mismatches


def canary_worker(args) -> None:
    from tools import build_window_cost_index as builder
    from traffic_sim.simulation.deterministic_disruption import DailyCostCache

    sampler = common.Sampler()
    phases: Dict[str, Any] = {}
    cpu, started = _cpu(), time.perf_counter()
    bound = builder._bound_inputs(args.profile)
    spec = derived_spec(bound["spec"], args.edge)
    _timed_phase(phases, "bound_inputs", cpu, started)
    cpu, started = _cpu(), time.perf_counter()
    units, _required = _population(
        builder, bound, spec, set(json.loads(args.build_keys)))
    _timed_phase(phases, "filter_prepass_diagnostic", cpu, started)

    cache = DailyCostCache(Path(args.oracle_root))
    (records, oracle, raw), counters = _run_raw_index(
        builder, bound, spec, units, cache, phases)
    sampler.sample("raw_index_records")
    comparison = builder.WindowCostIndex(
        bound_identity={"schema": SCHEMA, "edge": args.edge},
        records=records,
        preparation_time_s=phases["raw_index_records"]["wall_s"],
    ).compare_oracle(oracle)
    identities = raw["provider_identities"]
    costs, cost_mismatches, identity_mismatches = _unit_checks(
        records, oracle, identities, units, cache)
    sampler.sample("end")
    print(json.dumps({
        "build_keys": json.loads(args.build_keys),
        "daily_units": len(records),
        "phases": phases,
        "production_timings": raw["timings"],
        "counters": counters,
        "oracle_comparison": {k: comparison[k] for k in (
            "oracle_complete", "field_identical", "mismatch_count",
            "indexed_variant_records")},
        "unit_cost_mismatches": cost_mismatches,
        "provider_identity_mismatches": identity_mismatches,
        "affected_vehicles_total": sum(
            int(item.get("vehicles_affected") or 0)
            for unit in records.values() for item in unit["records"]),
        "positive_costs": sum(
            1 for item in costs.values()
            if any(float(item["cost"][field]) > 0 for field in COST_FIELDS)),
        "unit_costs": costs,
        "records_digest": common.digest(records),
        "unit_digests": {unit_id: {
            "indexed": common.digest(records[unit_id]),
            "oracle": common.digest(oracle[unit_id]),
            "provider_identity": common.digest(identities[unit_id]),
        } for unit_id in sorted(records)},
        "samples": sampler.samples,
    }))


def _sumo_processes() -> int:
    listing = subprocess.run(["ps", "-Ao", "comm="], capture_output=True,
                             text=True, check=True).stdout.splitlines()
    return sum(1 for line in listing
               if Path(line.strip()).name.startswith("sumo"))


def _runs_listing(runs_root: Path) -> Dict[str, Any]:
    names = sorted(path.name for path in Path(runs_root).iterdir())
    return {"entries": len(names), "digest": common.digest(names),
            "demand_archives": sum(1 for n in names
                                   if n.startswith("demand-"))}


def _choose_edge(census_path: Path) -> Dict[str, Any]:
    census = _verified(census_path)
    stale = [path for path, sha in census["production_sources"].items()
             if common.file_sha256(ROOT / path) != sha]
    if stale:
        raise SystemExit(f"effect census binds stale sources: {stale}")
    edge = census["summary"]["new_rule_choice"][0]
    row = next(item for item in census["candidates"]
               if item["edge_id"] == edge)
    if not (row["structurally_survivable"] and row["pre_canary_eligible"]):
        raise SystemExit(f"{edge} is not pre-canary eligible")
    return {"edge": edge, "row": row,
            "census_content_key": census["content_key"]}


def _checks(runs: Dict[int, Dict[str, Any]],
            oracle: Mapping[str, Any]) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    for count, run in runs.items():
        counters = run["counters"]
        checks[f"keys_{count}"] = {
            "oracle_complete_and_identical": (
                run["oracle_comparison"]["oracle_complete"]
                and run["oracle_comparison"]["field_identical"]),
            "unit_costs_identical": not run["unit_cost_mismatches"],
            "provider_identities_identical": (
                not run["provider_identity_mismatches"]),
            "affected_vehicles_total_positive": (
                run["affected_vehicles_total"] > 0),
            "positive_cost_present": run["positive_costs"] > 0,
            "one_archive_index_build": counters.get(
                "archive_index_build") == 1,
            "one_validation_per_build_key": counters.get(
                "archive_validate") == count,
            "oracle_digests_match_oracle_worker": all(
                digests["oracle"] == oracle["oracle_unit_digests"].get(unit)
                for unit, digests in run["unit_digests"].items()),
        }
    for count in (1, 2):
        checks[f"keys_{count}_within_keys_3"] = {"identical": all(
            runs[count]["unit_digests"][unit] == runs[3]["unit_digests"][unit]
            for unit in runs[count]["unit_digests"])}
    return checks


def _record(args, choice, phase_cost, keys, oracle, runs, checks,
            assessment, observed):
    passed = (all(all(group.values()) for group in checks.values())
              and assessment["effect_eligible"])
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "status": "PASS" if passed else "FAIL",
        "driver": common.driver_binding(Path(__file__)),
        "canary_helper": {
            "path": "validation/benchmarks/wci_current_code_canary_v1.py",
            "sha256": common.file_sha256(
                HERE / "wci_current_code_canary_v1.py")},
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "profile": str(args.profile),
        "profile_sha256": common.file_sha256(args.profile),
        "case_choice": {
            "rule": ("first road of benchmark_roads over the 30 qualified "
                     "archives"),
            "effect_census": str(args.effect_census),
            "effect_census_content_key": choice["census_content_key"],
            "edge": choice["edge"],
            "census_row": choice["row"],
        },
        "derived_spec": oracle["spec"],
        "derived_spec_note": ("identical to the frozen profile spec except "
                              "directed_edges, search_id and content_key"),
        "build_keys": {"source": str(args.phase_cost_evidence),
                       "source_content_key": phase_cost["content_key"],
                       "keys": keys},
        "oracle": {"root": str(args.oracle_root),
                   "method": ("ArchiveDisruptionProvider with a fresh "
                              "DailyCostCache: per-file closure_disruption, "
                              "independent of the parsed index"),
                   **oracle},
        "cost_granularity": ("per daily unit with the production candidate "
                             "reduction; whole candidates span five build "
                             "keys and do not fit in 1-3"),
        "runs": [runs[count] for count in KEY_COUNTS],
        "contract_checks": checks,
        "effect_assessment": assessment,
        **observed,
    }
    record["output_sha256"] = common.digest({
        "derived_spec": oracle["spec"],
        "oracle_unit_digests": oracle["oracle_unit_digests"],
        "runs": {str(count): {k: runs[count][k] for k in (
            "records_digest", "unit_digests", "unit_costs", "counters",
            "oracle_comparison", "affected_vehicles_total",
            "positive_costs")} for count in KEY_COUNTS},
        "contract_checks": checks,
        "effect_assessment": assessment,
    })
    return record, passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--effect-census", type=Path)
    parser.add_argument("--phase-cost-evidence", type=Path)
    parser.add_argument("--oracle-root", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", choices=("oracle", "canary"))
    parser.add_argument("--edge")
    parser.add_argument("--build-keys")
    args = parser.parse_args()
    if args.worker == "oracle":
        oracle_worker(args)
        return 0
    if args.worker == "canary":
        canary_worker(args)
        return 0
    if args.out is None or args.out.exists():
        raise SystemExit(f"evidence path missing or already exists: {args.out}")
    if args.oracle_root.exists():
        raise SystemExit(f"oracle root must be fresh: {args.oracle_root}")
    if args.effect_census is None or args.phase_cost_evidence is None:
        raise SystemExit("--effect-census and --phase-cost-evidence needed")

    from traffic_sim.simulation import effect_eligibility as effect
    from traffic_sim.simulation.metadata import network_edge_ids

    choice = _choose_edge(args.effect_census)
    phase_cost = _verified(args.phase_cost_evidence)
    keys = [item["build_key"] for item in phase_cost["selection"]["chosen"]]
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    runs_root = Path(profile["runs_root"])
    sumo_before, runs_before = _sumo_processes(), _runs_listing(runs_root)
    base = [sys.executable, str(Path(__file__).resolve()),
            "--profile", str(args.profile),
            "--oracle-root", str(args.oracle_root),
            "--edge", choice["edge"]]
    oracle = common.run_worker(base + ["--worker", "oracle",
                                       "--build-keys", json.dumps(keys)])
    print(json.dumps({"oracle_units": oracle["daily_units"],
                      "oracle_wall_s": round(oracle["wall_s"], 1)}),
          flush=True)
    runs = {}
    for count in KEY_COUNTS:
        runs[count] = common.run_worker(base + [
            "--worker", "canary", "--build-keys", json.dumps(keys[:count])])
        print(json.dumps({"keys": count, "units": runs[count]["daily_units"],
                          "affected": runs[count]["affected_vehicles_total"],
                          "positive_costs": runs[count]["positive_costs"],
                          "phases": runs[count]["phases"]}), flush=True)
    sumo_after, runs_after = _sumo_processes(), _runs_listing(runs_root)
    checks = _checks(runs, oracle)
    checks["execution_boundary"] = {
        "no_sumo_process_started": sumo_after <= sumo_before,
        "no_demand_archive_created": runs_after == runs_before,
    }
    evidence = effect.collect_evidence(
        [choice["edge"]],
        network_edges=network_edge_ids(ROOT / "sumo" / "net.net.xml"),
        archives=[Path(path) for path in oracle["archives"].values()],
        catalog_root=ROOT / "sumo" / "route_catalog")[choice["edge"]]
    assessment = effect.assess(evidence.with_canary(
        affected_vehicles_total=runs[3]["affected_vehicles_total"],
        positive_costs=runs[3]["positive_costs"])).to_dict()
    record, passed = _record(
        args, choice, phase_cost, keys, oracle, runs, checks, assessment,
        {"sumo_processes": {"before": sumo_before, "after": sumo_after},
         "runs_root_listing": {"before": runs_before, "after": runs_after}})
    published = common.publish(args.out, record)
    print(json.dumps({
        "status": record["status"],
        "edge": choice["edge"],
        "derived_spec": oracle["spec"],
        "checks": checks,
        "effect_assessment": {k: assessment[k] for k in (
            "pre_canary_eligible", "effect_eligible", "reasons")},
        "runs": [{"keys": count, "units": runs[count]["daily_units"],
                  "affected": runs[count]["affected_vehicles_total"],
                  "positive_costs": runs[count]["positive_costs"],
                  "raw_index_records_s": round(
                      runs[count]["phases"]["raw_index_records"]["wall_s"],
                      2),
                  "counters": runs[count]["counters"]}
                 for count in KEY_COUNTS],
        "oracle_wall_s": round(oracle["wall_s"], 1),
        "content_key": published["content_key"],
        "output_sha256": record["output_sha256"],
    }, indent=1))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
