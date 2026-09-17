"""Bounded NONZERO WindowCostIndex canary on a policy-selected canary spec.

The case comes from ``validation/wci_effect_canary_spec_*`` (schema
``wci_effect_canary_spec_v2``) written by ``closure_effect_census_v1``: the
frozen profile spec with only its directed edge replaced by the first
structural road that ``closure_effect_eligibility_v1`` found eligible. The
frozen spec is never edited. This driver:

1. verifies the canary spec (content keys, policy, source spec) and the
   inventory evidence it names: that evidence must still verify, i.e. no
   catalog pool or archive variant it read may have drifted;
2. computes an independent oracle for 1-3 build keys through the production
   per-file path (``ArchiveDisruptionProvider`` with a fresh
   ``DailyCostCache``), in its own process;
3. runs the unmodified production ``_raw_index_records`` for 1, 2 and 3 of
   those build keys, each in a fresh process, with only the unit population
   filtered at ``daily_unit_records``;
4. records the canary confirmation: affected vehicles, an affected daily
   unit and a cost above zero.

A candidate spans five build keys, so no whole candidate fits in 1-3 keys;
costs are compared per daily unit with the production candidate reduction.

Starts no SUMO, builds no demand or catalog, writes no index file, runs no
ledger, and writes only a fresh oracle cache and its own append-only evidence.
"""
from __future__ import annotations

import argparse
import dataclasses
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

SCHEMA = "wci_effect_canary_v3"
KEY_COUNTS = (1, 2, 3)
PRODUCTION_SOURCES = (
    "tools/build_window_cost_index.py",
    "tools/closure_effect_eligibility.py",
    "tools/cost_ordered_benchmark.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/window_cost_index.py",
    "traffic_sim/ops/io_phases.py",
    "run_scenario.py",
)
COST_FIELDS = ("added_vehicle_hours", "added_metres_total",
               "vehicles_affected", "vehicles_no_detour")


def load_canary_spec(path: Path):
    """The canary spec, refused unless every binding still holds."""
    from tools import closure_effect_eligibility as cee
    from traffic_sim.core.contracts import ClosureSearchSpec

    record = _verified(path)
    if record.get("schema") != "wci_effect_canary_spec_v2":
        raise SystemExit("unexpected canary spec schema")
    if cee.policy_of(record) != cee.POLICY:
        raise SystemExit("canary spec was not selected by the effect policy")
    spec = ClosureSearchSpec.from_dict(record["spec"])
    if spec.content_key != record["spec_content_key"]:
        raise SystemExit("canary spec content key drifted")
    source = record["source_spec"]
    if common.file_sha256(ROOT / source["path"]) != source["sha256"]:
        raise SystemExit("the source spec changed after the canary spec")
    evidence = _verified(Path(record["inventory_evidence"]["path"]))
    if (evidence["content_key"] != record["inventory_evidence"]["content_key"]
            or evidence["inventory_content_key"]
            != record["inventory_content_key"]):
        raise SystemExit("canary spec names different inventory evidence")
    stale = [name for name, sha in evidence["production_sources"].items()
             if common.file_sha256(ROOT / name) != sha]
    if stale:
        raise SystemExit(f"inventory evidence binds stale sources: {stale}")
    drift = cee.verify_inventory(evidence["inventory"])
    if drift:
        raise SystemExit(f"inventory evidence is no longer valid: {drift}")
    chosen = record["chosen_verdict"]
    if not (chosen["effect_eligible"] and chosen["structurally_survivable"]
            and tuple(spec.directed_edges) == (record["chosen_edge"],)
            and evidence["eligible_in_order"][:1] == [record["chosen_edge"]]):
        raise SystemExit("canary spec does not name the policy's choice")
    return spec, record


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
    spec, _record = load_canary_spec(args.canary_spec)
    wanted = set(json.loads(args.build_keys))
    units, required = _population(builder, bound, spec, wanted)
    index = builder._archives_for_build_key(bound["runs_root"])
    network = NetworkCostModel()
    cache = DailyCostCache(Path(args.oracle_root))
    archives, identities, digests = {}, {}, {}
    for key in sorted(wanted):
        match = builder.find_demand_archives(
            bound["runs_root"], required[key],
            qualified_manifest=bound["qualified_manifest"],
            _archive_index=index)[0]
        archive = Path(match["archive"]).resolve()
        archives[key] = {"archive": str(archive),
                         "archive_content_key": match["archive_content_key"],
                         "outputs": match["outputs"]}
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
    spec, _record = load_canary_spec(args.canary_spec)
    _timed_phase(phases, "bound_inputs", cpu, started)
    cpu, started = _cpu(), time.perf_counter()
    units, _required = _population(
        builder, bound, spec, set(json.loads(args.build_keys)))
    _timed_phase(phases, "filter_prepass_diagnostic", cpu, started)

    cache = DailyCostCache(Path(args.oracle_root))
    (records, oracle, raw), counters = _run_raw_index(
        builder, bound, spec, units, cache, phases)
    comparison = builder.WindowCostIndex(
        bound_identity={"schema": SCHEMA, "spec": spec.content_key},
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
        "affected_daily_units": sum(
            1 for unit in records.values()
            if any(int(item.get("vehicles_affected") or 0) > 0
                   for item in unit["records"])),
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
            "affected_daily_unit_present": run["affected_daily_units"] > 0,
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


def _effect_assessment(spec_record, run):
    """The policy verdict plus the bounded canary's confirmation."""
    confirmation = {
        "affected_vehicles_total_positive": run["affected_vehicles_total"] > 0,
        "affected_daily_unit_present": run["affected_daily_units"] > 0,
        "positive_cost_present": run["positive_costs"] > 0,
    }
    return {
        "policy": spec_record["case_selection_policy"],
        "edge_id": spec_record["chosen_edge"],
        "effect_eligible": bool(
            spec_record["chosen_verdict"]["effect_eligible"]),
        "reason_codes": spec_record["chosen_verdict"]["reason_codes"],
        "canary_confirmation": confirmation,
        "canary_confirmed": all(confirmation.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--canary-spec", type=Path, required=True)
    parser.add_argument("--phase-cost-evidence", type=Path)
    parser.add_argument("--oracle-root", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", choices=("oracle", "canary"))
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
    if args.phase_cost_evidence is None:
        raise SystemExit("--phase-cost-evidence is required")

    from tools import build_window_cost_index as builder

    spec, spec_record = load_canary_spec(args.canary_spec)
    phase_cost = _verified(args.phase_cost_evidence)
    keys = [item["build_key"] for item in phase_cost["selection"]["chosen"]]
    bound = builder._bound_inputs(args.profile)
    runs_root = Path(bound["runs_root"])
    sumo_before, runs_before = _sumo_processes(), _runs_listing(runs_root)
    base = [sys.executable, str(Path(__file__).resolve()),
            "--profile", str(args.profile),
            "--canary-spec", str(args.canary_spec),
            "--oracle-root", str(args.oracle_root)]
    oracle = common.run_worker(base + ["--worker", "oracle",
                                       "--build-keys", json.dumps(keys)])
    print(json.dumps({"oracle_units": oracle["daily_units"],
                      "oracle_wall_s": round(oracle["wall_s"], 1)}),
          flush=True)
    runs = {}
    for count in KEY_COUNTS:
        runs[count] = common.run_worker(base + [
            "--worker", "canary", "--build-keys", json.dumps(keys[:count])])
        print(json.dumps({key: runs[count][key] for key in (
            "daily_units", "affected_vehicles_total", "affected_daily_units",
            "positive_costs", "phases", "counters")}), flush=True)
    checks = _checks(runs, oracle)
    sumo_after, runs_after = _sumo_processes(), _runs_listing(runs_root)
    checks["execution_boundary"] = {
        "no_sumo_process_started": sumo_after <= sumo_before,
        "no_demand_archive_created": runs_after == runs_before,
    }
    assessment = _effect_assessment(spec_record, runs[3])
    passed = (all(all(group.values()) for group in checks.values())
              and assessment["effect_eligible"]
              and assessment["canary_confirmed"])
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
        "profile": {"path": str(args.profile),
                    "sha256": common.file_sha256(args.profile)},
        "canary_spec": {"path": str(args.canary_spec),
                        "content_key": spec_record["content_key"],
                        "policy": spec_record["case_selection_policy"],
                        "inventory_evidence": spec_record[
                            "inventory_evidence"],
                        "spec_content_key": spec.content_key,
                        "search_id": spec.search_id,
                        "directed_edges": list(spec.directed_edges)},
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
        "sumo_processes": {"before": sumo_before, "after": sumo_after},
        "runs_root_listing": {"before": runs_before, "after": runs_after},
    }
    record["output_sha256"] = common.digest({
        "canary_spec": record["canary_spec"],
        "oracle_unit_digests": oracle["oracle_unit_digests"],
        "runs": {str(count): {k: runs[count][k] for k in (
            "records_digest", "unit_digests", "unit_costs", "counters",
            "oracle_comparison", "affected_vehicles_total",
            "affected_daily_units", "positive_costs")}
            for count in KEY_COUNTS},
        "contract_checks": checks,
        "effect_assessment": assessment,
    })
    published = common.publish(args.out, record)
    print(json.dumps({
        "status": record["status"],
        "canary_spec": record["canary_spec"],
        "checks": checks,
        "effect_assessment": assessment,
        "runs": [{"keys": count, "units": runs[count]["daily_units"],
                  "affected": runs[count]["affected_vehicles_total"],
                  "affected_units": runs[count]["affected_daily_units"],
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
