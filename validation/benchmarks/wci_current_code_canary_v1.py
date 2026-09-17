"""Bounded current-code canary of the WindowCostIndex raw path.

Runs the UNMODIFIED production ``tools/build_window_cost_index.
_raw_index_records`` for 1, 2 and 3 build keys, each in a fresh process.
Only two module attributes are substituted for the duration of the call:

* ``daily_unit_records`` yields the production records of the selected
  build keys only (the production generator, its identities and its
  schedule builders are passed through untouched);
* ``EXPECTED_DAILY_UNITS`` is set to that filtered population.

Everything else -- the shared archive index, per-build-key validation,
parsing, index construction, the unit loop and the oracle reads -- is the
production code. The filter's own pre-pass is timed separately as
diagnostic overhead.

Outputs are checked three ways: field-for-field against the bound
deterministic daily-cost cache (``WindowCostIndex.compare_oracle``), byte for
byte against the retain runs of ``wci_retain_stream_memory_v1`` for the same
build keys, and the 1- and 2-key runs against the 3-key run.

Starts no SUMO, builds no demand, writes no index file, runs no ledger, and
writes only its own append-only evidence file. Month-scale numbers are
modelled from the three runs, never measured.
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_current_code_canary_v1"
KEY_COUNTS = (1, 2, 3)
MONTH_KEYS = 30
PRODUCTION_SOURCES = (
    "tools/build_window_cost_index.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/window_cost_index.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/ops/io_phases.py",
)


def _cpu():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"user_s": usage.ru_utime, "sys_s": usage.ru_stime}


def _timed_phase(phases, name, cpu, started):
    phases[name] = {"wall_s": time.perf_counter() - started,
                    **{k: v - cpu[k] for k, v in _cpu().items()}}


def worker(args) -> None:
    from tools import build_window_cost_index as builder
    from traffic_sim.ops import io_phases
    from traffic_sim.simulation.deterministic_disruption import DailyCostCache

    sampler = common.Sampler()
    system_before = common.system_memory()
    wanted = set(json.loads(args.build_keys))
    phases: Dict[str, Any] = {}

    cpu, started = _cpu(), time.perf_counter()
    bound = builder._bound_inputs(args.profile)
    _timed_phase(phases, "bound_inputs", cpu, started)
    sampler.sample("bound")
    spec = bound["spec"]

    # Diagnostic pre-pass: which production units belong to the chosen keys.
    cpu, started = _cpu(), time.perf_counter()
    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=bound["runs_root"], build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="wci-current-code-canary-filter",
        qualified_demand_manifest=bound["qualified_manifest"])
    production_records = builder.daily_unit_records
    selected, skipped = set(), set()
    for parent in bound["parents"]:
        for unit_id, _identity, build in production_records(spec, parent):
            unit_id = str(unit_id)
            if unit_id in selected or unit_id in skipped:
                continue
            key = resolver._required(build()).build_key
            (selected if key in wanted else skipped).add(unit_id)
    _timed_phase(phases, "filter_prepass_diagnostic", cpu, started)
    sampler.sample("filtered")

    def filtered_records(spec_arg, parent):
        for record in production_records(spec_arg, parent):
            if str(record[0]) in selected:
                yield record

    collector = io_phases.PhaseCollector()
    production_expected = builder.EXPECTED_DAILY_UNITS
    builder.daily_unit_records = filtered_records
    builder.EXPECTED_DAILY_UNITS = len(selected)
    try:
        cpu, started = _cpu(), time.perf_counter()
        with io_phases.observe(collector):
            records, oracle, raw = builder._raw_index_records(
                spec, runs_root=bound["runs_root"],
                oracle_cache=DailyCostCache(bound["cache_root"]),
                qualified_demand_manifest=bound["qualified_manifest"])
        _timed_phase(phases, "raw_index_records", cpu, started)
    finally:
        builder.daily_unit_records = production_records
        builder.EXPECTED_DAILY_UNITS = production_expected
    sampler.sample("raw_index_records")

    index = builder.WindowCostIndex(
        bound_identity={"schema": SCHEMA, "build_keys": sorted(wanted)},
        records=records,
        preparation_time_s=phases["raw_index_records"]["wall_s"])
    comparison = index.compare_oracle(oracle)
    records_digest = common.digest(index.to_dict()["records"])
    sampler.sample("end")
    report = collector.report()
    json_phase = report["phases"].get("archive_json_read", {})
    identities = raw["provider_identities"]
    print(json.dumps({
        "build_keys": sorted(wanted),
        "daily_units": len(records),
        "phases": phases,
        "production_timings": raw["timings"],
        "structural_reuse": raw["structural_reuse"],
        "counters": report["counters"],
        "json_read": {"calls": json_phase.get("calls", 0),
                      "bytes": json_phase.get("bytes", {}),
                      "inclusive_s": json_phase.get("inclusive_s", 0.0)},
        "oracle_comparison": {k: comparison[k] for k in (
            "oracle_complete", "field_identical", "mismatch_count",
            "indexed_variant_records")},
        "affected_vehicles_total": sum(
            int(item.get("vehicles_affected") or 0)
            for unit in records.values() for item in unit["records"]),
        "records_digest": records_digest,
        "unit_digests": {unit_id: {
            "indexed": common.digest(records[unit_id]),
            "oracle": common.digest(oracle[unit_id]),
            "provider_identity": common.digest(identities[unit_id]),
        } for unit_id in sorted(records)},
        "samples": sampler.samples,
        "system_before": system_before,
        "system_after": common.system_memory(),
    }))


def _verified(path: Path) -> Dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    claimed = record.pop("content_key")
    if common.digest(record) != claimed:
        raise SystemExit(f"content key mismatch: {path}")
    record["content_key"] = claimed
    return record


def _compare(left, right):
    units = sorted(left)
    return {
        "units_compared": len(units),
        "population_matches": set(left) == set(right),
        **{f"{field}_identical": all(
            left[u][field] == right.get(u, {}).get(field) for u in units)
           for field in ("indexed", "oracle", "provider_identity")},
    }


def _fit(points):
    """Least-squares line through (keys, seconds)."""
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    slope = (sum((x - mean_x) * (y - mean_y) for x, y in points)
             / sum((x - mean_x) ** 2 for x, _ in points))
    return mean_y - slope * mean_x, slope


def _projection(by_count):
    raw = {n: by_count[n]["phases"]["raw_index_records"] for n in KEY_COUNTS}
    wall_points = [(n, raw[n]["wall_s"]) for n in KEY_COUNTS]
    cpu_points = [(n, raw[n]["user_s"] + raw[n]["sys_s"]) for n in KEY_COUNTS]
    intercept, slope = _fit(wall_points)
    cpu_intercept, cpu_slope = _fit(cpu_points)
    bound_s = sum(by_count[n]["phases"]["bound_inputs"]["wall_s"]
                  for n in KEY_COUNTS) / len(KEY_COUNTS)
    return {
        "kind": "modelled",
        "scope": ("_bound_inputs plus _raw_index_records only; oracle "
                  "comparison, index write/load and the indexed ledger are "
                  "not in it"),
        "basis": ("least-squares line through the three measured "
                  "_raw_index_records wall times against build keys, "
                  "evaluated at 30 keys, plus the mean _bound_inputs time"),
        "assumptions": [
            "cost is linear in build keys",
            ("no memory-pressure cost: the production retain loop holds "
             "every archive, modelled at about 19 GB footprint for 30 keys "
             "(wci_retain_stream_memory_v1); this canary does not reach it"),
        ],
        "points_wall_s": wall_points,
        "points_cpu_s": cpu_points,
        "intercept_wall_s": intercept,
        "per_key_wall_s": slope,
        "intercept_cpu_s": cpu_intercept,
        "per_key_cpu_s": cpu_slope,
        "raw_index_records_month_wall_s": intercept + MONTH_KEYS * slope,
        "bound_inputs_wall_s": bound_s,
        "total_month_wall_s": bound_s + intercept + MONTH_KEYS * slope,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--phase-cost-evidence", type=Path)
    parser.add_argument("--retain-evidence", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--build-keys")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return 0
    if args.out is None or args.out.exists():
        raise SystemExit(f"evidence path missing or already exists: {args.out}")
    if args.phase_cost_evidence is None or args.retain_evidence is None:
        raise SystemExit("--phase-cost-evidence and --retain-evidence needed")

    phase_cost = _verified(args.phase_cost_evidence)
    chosen = [item["build_key"] for item in phase_cost["selection"]["chosen"]]
    retain_record = _verified(args.retain_evidence)
    retain = {run["archive_count"]: run["unit_digests"]
              for run in retain_record["runs"] if run["mode"] == "retain"}
    base = [sys.executable, str(Path(__file__).resolve()), "--worker",
            "--profile", str(args.profile)]
    by_count = {}
    for count in KEY_COUNTS:
        started = time.perf_counter()
        run = common.run_worker(
            base + ["--build-keys", json.dumps(chosen[:count])])
        run["process_wall_s"] = time.perf_counter() - started
        by_count[count] = run
        print(json.dumps({"keys": count, "units": run["daily_units"],
                          "phases": run["phases"],
                          "oracle": run["oracle_comparison"]}), flush=True)

    cross_checks = {
        f"canary{n}_vs_retain{n}": _compare(by_count[n]["unit_digests"],
                                            retain[n])
        for n in KEY_COUNTS}
    cross_checks.update({
        f"canary{n}_within_canary3": _compare(
            by_count[n]["unit_digests"],
            {u: by_count[3]["unit_digests"][u]
             for u in by_count[n]["unit_digests"]})
        for n in (1, 2)})
    projection = _projection(by_count)
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "driver": common.driver_binding(Path(__file__)),
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "profile": str(args.profile),
        "profile_sha256": common.file_sha256(args.profile),
        "selection": {"source": str(args.phase_cost_evidence),
                      "source_content_key": phase_cost["content_key"],
                      "build_keys": chosen,
                      "rule": "the first N of the v1 min/median/max choice"},
        "retain_reference": {"source": str(args.retain_evidence),
                             "source_content_key":
                                 retain_record["content_key"]},
        "substitutions": {
            "daily_unit_records": ("production generator filtered to the "
                                   "selected build keys; records passed "
                                   "through unchanged"),
            "EXPECTED_DAILY_UNITS": "the filtered population",
        },
        "runs": [by_count[n] for n in KEY_COUNTS],
        "cross_checks": cross_checks,
        "projection": projection,
    }
    record["output_sha256"] = common.digest({
        "runs": {str(n): {"records_digest": by_count[n]["records_digest"],
                          "unit_digests": by_count[n]["unit_digests"],
                          "oracle": by_count[n]["oracle_comparison"],
                          "counters": by_count[n]["counters"]}
                 for n in KEY_COUNTS},
        "cross_checks": cross_checks,
    })
    published = common.publish(args.out, record)

    def phase(n, name):
        return {k: round(v, 2)
                for k, v in by_count[n]["phases"][name].items()}

    print(json.dumps({
        "runs": [{
            "keys": n, "units": by_count[n]["daily_units"],
            "bound_inputs": phase(n, "bound_inputs"),
            "filter_prepass_diagnostic": phase(n, "filter_prepass_diagnostic"),
            "raw_index_records": phase(n, "raw_index_records"),
            "counters": by_count[n]["counters"],
            "timings": {k: round(v, 3) for k, v in
                        by_count[n]["production_timings"].items()},
            "affected": by_count[n]["affected_vehicles_total"],
            "records_digest": by_count[n]["records_digest"][:12],
            "lifetime_max_footprint_mb": round(
                by_count[n]["samples"][-1][
                    "lifetime_max_phys_footprint_bytes"] / 1e6, 1),
        } for n in KEY_COUNTS],
        "cross_checks": cross_checks,
        "projection": {k: (round(v, 1) if isinstance(v, float) else v)
                       for k, v in projection.items()
                       if k not in ("assumptions", "basis", "scope")},
        "content_key": published["content_key"],
        "output_sha256": record["output_sha256"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
