"""Retain-all versus one-archive-at-a-time memory on the WindowCostIndex path.

Tests one hypothesis directly: the 2026-09-16 index build retained every
parsed archive and its index before the unit loop, and that retention is
what drove its memory. It does NOT by itself test whether memory explains
the build's 31,273 s; the evidence records what it can and cannot say.

Runs, each in its own fresh process, each computing exactly once (no warm
repetition inside a measured process):

* ``retain`` over 1, 2 and 3 archives -- the loop structure of
  ``tools/build_window_cost_index._raw_index_records``: parse and index every
  archive first, then evaluate every daily unit;
* ``stream`` over the same 3 archives -- evaluate one archive's units, then
  release that archive before the next is opened. This path exists only in
  this driver; production code is unchanged.

Current resident size and kernel footprint are sampled after every archive.
Both modes' index records, oracle records and provider identities are
compared byte for byte in canonical JSON.

Auxiliary, labelled separately: the build that ran on 2026-09-15/16 resolved
archives with ``find_demand_archives`` once per daily unit WITHOUT a shared
index, so each of its 1,950 calls rebuilt the archive index from every
archive's ``demand_meta.json``. The archive-resolution v2 benchmark excluded
exactly that cost. Three fresh processes each time one index build, so its
size can be set beside the memory hypothesis instead of assumed away.

Starts no SUMO, builds no demand, writes no index, and writes only its own
append-only evidence file.
"""
from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_retain_stream_memory_v1"
VARIANTS = ("q10", "q50", "q90")
PRODUCTION_SOURCES = (
    "tools/build_window_cost_index.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/window_cost_index.py",
    "traffic_sim/simulation/independent_daily.py",
)
MODES = ("retain", "stream")
RETAIN_COUNTS = (1, 2, 3)
STREAM_COUNT = 3
MONTH_ARCHIVES = 30
INDEX_REBUILD_PROCESSES = 3
FAILED_BUILD = {
    "source": "runs/step5_code_approved_20260915b/wci.log (/usr/bin/time -l)",
    "code": "b5e1564 (tools/build_window_cost_index.py main at line 509)",
    "real_s": 31273.03,
    "user_s": 30155.20,
    "sys_s": 1103.38,
    "maximum_resident_set_size_bytes": 6455951360,
    "peak_memory_footprint_bytes": 19578747112,
    "page_reclaims": 126945042,
    "page_faults": 2119,
    "daily_units": 1950,
}


# -- the measured computation (also driven by the synthetic test) ----------

def _evaluate_unit(unit_id: str, schedule: Any, provider: Any,
                   indexes: Mapping[str, Any], *,
                   closures_for: Callable[[Any, Any], Any],
                   oracle_load: Callable[[Mapping[str, Any]], Any],
                   indexed: Dict[str, Any], oracle: Dict[str, Any],
                   identities: Dict[str, Any]) -> None:
    """One daily unit, exactly as the production unit loop computes it."""
    identities[unit_id] = dict(provider.identity())
    closures = closures_for(schedule, provider.inputs)
    records = tuple({"demand_variant": variant,
                     **indexes[variant].disruption(closures)}
                    for variant in VARIANTS)
    indexed[unit_id] = {"schedule_id": schedule.schedule_id,
                        "records": [dict(item) for item in records]}
    expected = oracle_load(provider.cache_identity(schedule))
    if expected is None:
        raise RuntimeError(f"independent oracle is missing for {unit_id}")
    oracle[unit_id] = {"schedule_id": schedule.schedule_id,
                       "records": [dict(item) for item in expected]}


def _load_archive(archive: Any, *, make_provider, parse_routes, build_index):
    provider = make_provider(archive)
    parsed = {variant: parse_routes(path)
              for variant, path in provider.inputs.variant_paths.items()}
    indexes = {variant: build_index(parsed[variant]) for variant in VARIANTS}
    facts = {
        "vehicles": {v: len(parsed[v]) for v in VARIANTS},
        # Measurement-only read of the crossing set each index built.
        "crossing_vehicles": {v: len(indexes[v]._crossing) for v in VARIANTS},
    }
    return provider, parsed, indexes, facts


def compute_units(
    mode: str,
    archive_order: Sequence[Any],
    units_by_archive: Mapping[Any, Mapping[str, Any]],
    *,
    make_provider: Callable[[Any], Any],
    parse_routes: Callable[[Path], Any],
    build_index: Callable[[Any], Any],
    closures_for: Callable[[Any, Any], Any],
    oracle_load: Callable[[Mapping[str, Any]], Any],
    sample: Callable[..., Any] = lambda label, **extra: None,
) -> Dict[str, Any]:
    """Index records, oracle records and provider identities for all units.

    ``retain`` reproduces the production loop structure; ``stream`` holds at
    most one archive's parsed routes and indexes at a time.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    indexed: Dict[str, Any] = {}
    oracle: Dict[str, Any] = {}
    identities: Dict[str, Any] = {}
    facts: Dict[str, Any] = {}
    loader = dict(make_provider=make_provider, parse_routes=parse_routes,
                  build_index=build_index)
    evaluate = dict(closures_for=closures_for, oracle_load=oracle_load,
                    indexed=indexed, oracle=oracle, identities=identities)
    owner = {unit_id: archive for archive in archive_order
             for unit_id in units_by_archive[archive]}
    if mode == "retain":
        provider_by_archive, parsed_by_archive, index_by_archive = {}, {}, {}
        for position, archive in enumerate(archive_order, start=1):
            (provider_by_archive[archive], parsed_by_archive[archive],
             index_by_archive[archive], facts[str(archive)]) = _load_archive(
                archive, **loader)
            sample(f"retain_loaded_archive_{position}")
        for unit_id in sorted(owner):
            archive = owner[unit_id]
            _evaluate_unit(unit_id, units_by_archive[archive][unit_id],
                           provider_by_archive[archive],
                           index_by_archive[archive], **evaluate)
        sample("retain_units_evaluated")
    else:
        for position, archive in enumerate(archive_order, start=1):
            provider, parsed, indexes, facts[str(archive)] = _load_archive(
                archive, **loader)
            sample(f"stream_loaded_archive_{position}")
            for unit_id in sorted(units_by_archive[archive]):
                _evaluate_unit(unit_id, units_by_archive[archive][unit_id],
                               provider, indexes, **evaluate)
            del provider, parsed, indexes
            collected = gc.collect()
            sample(f"stream_released_archive_{position}",
                   gc_collected=collected)
    return {"indexed": indexed, "oracle": oracle,
            "provider_identities": identities, "archive_facts": facts}


def unit_digests(result: Mapping[str, Any]) -> Dict[str, Dict[str, str]]:
    return {unit_id: {
        "indexed": common.digest(result["indexed"][unit_id]),
        "oracle": common.digest(result["oracle"][unit_id]),
        "provider_identity": common.digest(
            result["provider_identities"][unit_id]),
    } for unit_id in sorted(result["indexed"])}


# -- real-archive worker -----------------------------------------------------

def _real_inputs(profile: Path, chosen: Sequence[Mapping[str, str]]):
    from tools import build_window_cost_index as builder

    bound = builder._bound_inputs(profile)
    spec = bound["spec"]
    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=bound["runs_root"], build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="subhour-phase5-raw-index",
        qualified_demand_manifest=bound["qualified_manifest"])
    wanted = {item["build_key"] for item in chosen}
    collected: Dict[str, Any] = {}
    required_by_key: Dict[str, Any] = {}
    for parent in bound["parents"]:
        for unit_id, identity, build in builder.daily_unit_records(spec, parent):
            unit_id = str(unit_id)
            schedule = build()
            existing = collected.get(unit_id)
            if existing is not None:
                if (existing[0] != identity
                        or existing[1].to_dict() != schedule.to_dict()):
                    raise RuntimeError(f"daily unit collision for {unit_id}")
                continue
            required = resolver._required(schedule)
            if required.build_key in wanted:
                collected[unit_id] = (identity, schedule, required.build_key)
                required_by_key.setdefault(required.build_key, required)
    index = builder._archives_for_build_key(bound["runs_root"])
    units_by_archive: Dict[Path, Dict[str, Any]] = {}
    order = []
    for item in chosen:
        matches = builder.find_demand_archives(
            bound["runs_root"], required_by_key[item["build_key"]],
            qualified_manifest=bound["qualified_manifest"],
            _archive_index=index)
        archive = Path(matches[0]["archive"]).resolve()
        if str(archive) != item["archive"]:
            raise RuntimeError(f"{item['build_key']} resolved to {archive}, "
                               f"expected {item['archive']}")
        order.append(archive)
        units_by_archive[archive] = {
            unit_id: schedule
            for unit_id, (_identity, schedule, key) in collected.items()
            if key == item["build_key"]}
    return bound, spec, order, units_by_archive


def worker(args) -> None:
    from tools import build_window_cost_index as builder
    from traffic_sim.simulation.deterministic_disruption import (
        ArchiveDisruptionProvider, DailyCostCache, NetworkCostModel,
        closure_seconds)
    from traffic_sim.simulation.disruption import (
        build_parsed_window_cost_index, parse_route_vehicles)

    sampler = common.Sampler()
    system_before = common.system_memory()
    sampler.sample("imported")
    chosen = json.loads(args.chosen)
    bound, spec, order, units_by_archive = _real_inputs(args.profile, chosen)
    sampler.sample("prepared")
    network = NetworkCostModel()
    sampler.sample("network_loaded")
    closed = set(spec.directed_edges)
    cache = DailyCostCache(bound["cache_root"])

    result = compute_units(
        args.mode, order, units_by_archive,
        make_provider=lambda archive: ArchiveDisruptionProvider(
            spec, archive=archive, network=network, cache=None),
        parse_routes=parse_route_vehicles,
        build_index=lambda parsed: build_parsed_window_cost_index(
            parsed, closed, network.edge_time, network.edge_len,
            adjacency=network.adjacency,
            destination_access=network.destination_access),
        closures_for=lambda schedule, inputs: closure_seconds(
            spec, schedule, epoch=inputs.epoch, duration_s=inputs.duration_s),
        oracle_load=cache.load,
        sample=sampler.sample)
    sampler.sample("end")
    comparison = builder.WindowCostIndex(
        bound_identity={"diagnostic": SCHEMA}, records=result["indexed"],
        preparation_time_s=0.0).compare_oracle(result["oracle"])
    print(json.dumps({
        "mode": args.mode,
        "archive_count": len(order),
        "archives": [str(path) for path in order],
        "daily_units": len(result["indexed"]),
        "samples": sampler.samples,
        "system_before": system_before,
        "system_after": common.system_memory(),
        "archive_facts": result["archive_facts"],
        "unit_digests": unit_digests(result),
        "indexed_matches_oracle": {
            k: comparison[k] for k in ("oracle_complete", "field_identical",
                                       "mismatch_count")},
        "affected_vehicles_total": sum(
            int(record.get("vehicles_affected") or 0)
            for unit in result["indexed"].values()
            for record in unit["records"]),
    }))


def index_rebuild_worker(args) -> None:
    from traffic_sim.simulation.monthly_demand import _archives_for_build_key

    sampler = common.Sampler()
    runs_root = Path(args.runs_root)
    started = time.perf_counter()
    cpu = common.cpu_seconds()
    index = _archives_for_build_key(runs_root)
    wall_s = time.perf_counter() - started
    sampler.sample("index_built")
    print(json.dumps({
        "wall_s": wall_s,
        "cpu_s": common.cpu_seconds() - cpu,
        "archive_dirs": len(list(runs_root.glob("demand-*"))),
        "indexed_build_keys": len(index),
        "samples": sampler.samples,
    }))


# -- orchestration -------------------------------------------------------------

def _chosen_from_phase_cost(path: Path):
    record = json.loads(path.read_text(encoding="utf-8"))
    claimed = record.pop("content_key")
    if common.digest(record) != claimed:
        raise SystemExit(f"phase-cost evidence content key mismatch: {path}")
    chosen = [{"build_key": item["build_key"], "archive": item["archive"],
               "archive_content_key": item["archive_content_key"],
               "route_bytes": item["route_bytes"]}
              for item in record["selection"]["chosen"]]
    return claimed, chosen


def _run(argv):
    started = time.perf_counter()
    payload = common.run_worker(argv)
    payload["process_wall_s"] = time.perf_counter() - started
    return payload


def _labelled(run, prefix):
    return [s for s in run["samples"] if s["label"].startswith(prefix)]


def _summary(run):
    samples = {s["label"]: s for s in run["samples"]}
    loaded = _labelled(run, ("retain_loaded", "stream_loaded"))
    released = _labelled(run, "stream_released")
    end, base = samples["end"], samples["network_loaded"]

    def megabytes(rows, key):
        return [round(row[key] / 1e6, 1) for row in rows]

    return {
        "mode": run["mode"],
        "archives": run["archive_count"],
        "daily_units": run["daily_units"],
        "baseline_resident_mb": round(base["resident_bytes"] / 1e6, 1),
        "baseline_footprint_mb": round(base["phys_footprint_bytes"] / 1e6, 1),
        "resident_after_each_load_mb": megabytes(loaded, "resident_bytes"),
        "footprint_after_each_load_mb": megabytes(
            loaded, "phys_footprint_bytes"),
        "resident_after_each_release_mb": megabytes(
            released, "resident_bytes"),
        "footprint_after_each_release_mb": megabytes(
            released, "phys_footprint_bytes"),
        "gc_collected_after_release": [s.get("gc_collected")
                                       for s in released],
        "max_rss_mb": round(end["max_rss_bytes"] / 1e6, 1),
        "lifetime_max_footprint_mb": round(
            end["lifetime_max_phys_footprint_bytes"] / 1e6, 1),
        "wall_s": round(end["wall_s"], 2),
        "cpu_s": round(end["cpu_s"], 2),
        "compute_wall_s": round(end["wall_s"] - base["wall_s"], 2),
        "compute_cpu_s": round(end["cpu_s"] - base["cpu_s"], 2),
        "swap_used_delta_mb_system_wide": round(
            run["system_after"]["swap_used_mb"]
            - run["system_before"]["swap_used_mb"], 2),
        "major_faults": end["major_faults"],
        "pageins": end["pageins"],
        "crossing_vehicles": sum(
            sum(facts["crossing_vehicles"].values())
            for facts in run["archive_facts"].values()),
        "affected_vehicles_total": run["affected_vehicles_total"],
        "indexed_matches_oracle": run["indexed_matches_oracle"],
    }


def _compare(left, right, units=None):
    """Byte comparison of per-unit digests; ``units`` limits it to a subset."""
    full = units is None
    units = sorted(left["unit_digests"] if full else units)
    fields = ("indexed", "oracle", "provider_identity")
    mismatches = {
        field: [u for u in units
                if left["unit_digests"][u][field]
                != right["unit_digests"].get(u, {}).get(field)]
        for field in fields}
    population = (set(left["unit_digests"]) == set(right["unit_digests"])
                  if full else set(units) <= set(right["unit_digests"]))
    return {
        "units_compared": len(units),
        "population_matches": population,
        **{f"{field}_identical": not mismatches[field] for field in fields},
        "mismatches": mismatches,
        "digest": common.digest({u: left["unit_digests"][u] for u in units}),
    }


def _per_archive_growth(run):
    base = {s["label"]: s for s in run["samples"]}["network_loaded"]
    points = [base] + _labelled(run, "retain_loaded")
    return {key: [b[key] - a[key] for a, b in zip(points, points[1:])]
            for key in ("resident_bytes", "phys_footprint_bytes")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--phase-cost-evidence", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", choices=("units", "index-rebuild"))
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--chosen")
    parser.add_argument("--runs-root")
    args = parser.parse_args()
    if args.worker == "units":
        worker(args)
        return 0
    if args.worker == "index-rebuild":
        index_rebuild_worker(args)
        return 0
    if args.out is None or args.out.exists():
        raise SystemExit(f"evidence path missing or already exists: {args.out}")
    if args.phase_cost_evidence is None:
        raise SystemExit("--phase-cost-evidence is required")

    selection_key, chosen = _chosen_from_phase_cost(args.phase_cost_evidence)
    base = [sys.executable, str(Path(__file__).resolve()),
            "--profile", str(args.profile)]
    plan = [("retain", count) for count in RETAIN_COUNTS]
    plan.append(("stream", STREAM_COUNT))
    runs = []
    for mode, count in plan:
        run = _run(base + ["--worker", "units", "--mode", mode,
                           "--chosen", json.dumps(chosen[:count])])
        runs.append(run)
        print(json.dumps(_summary(run)), flush=True)

    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    rebuilds = [_run(base + ["--worker", "index-rebuild",
                             "--runs-root", str(profile["runs_root"])])
                for _ in range(INDEX_REBUILD_PROCESSES)]
    rebuild_median_s = statistics.median(r["wall_s"] for r in rebuilds)

    by_key = {(r["mode"], r["archive_count"]): r for r in runs}
    retain_three, stream_three = by_key[("retain", 3)], by_key[("stream", 3)]
    comparisons = {
        "retain3_vs_stream3": _compare(retain_three, stream_three),
        **{f"retain{n}_within_retain3": _compare(
            by_key[("retain", n)], retain_three,
            units=list(by_key[("retain", n)]["unit_digests"]))
           for n in (1, 2)},
    }
    summaries = [_summary(run) for run in runs]
    growth = _per_archive_growth(retain_three)
    footprint_growth = statistics.mean(growth["phys_footprint_bytes"])
    retain_base = {s["label"]: s for s in retain_three["samples"]}[
        "network_loaded"]
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "hypothesis": ("retaining all parsed archives and their indexes "
                       "before the unit loop is what grows the index "
                       "build's memory; releasing one archive at a time "
                       "removes that growth without changing any output"),
        "driver": common.driver_binding(Path(__file__)),
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "profile": str(args.profile),
        "profile_sha256": common.file_sha256(args.profile),
        "archive_selection": {
            "source": str(args.phase_cost_evidence),
            "source_content_key": selection_key,
            "rule": ("the v1 phase-cost selection (min, median, max by route "
                     "bytes), in that order; retain-N uses the first N"),
            "chosen": chosen,
            "digest": common.digest(chosen),
        },
        "method": {
            "one_cold_computation_per_process": True,
            "retain": ("loop structure of _raw_index_records: every archive "
                       "parsed and indexed first, then every unit"),
            "stream": ("per archive: parse, index, evaluate its units, del, "
                       "gc.collect(); diagnostic only"),
            "memory_quantities": {
                "resident_bytes": "proc_pid_rusage ri_resident_size, current",
                "phys_footprint_bytes":
                    "proc_pid_rusage ri_phys_footprint, current",
                "max_rss_bytes": "getrusage ru_maxrss, bytes on macOS",
                "lifetime_max_phys_footprint_bytes":
                    "proc_pid_rusage ri_lifetime_max_phys_footprint",
                "swap_used_mb": "sysctl vm.swapusage used, SYSTEM-WIDE",
            },
        },
        "runs": runs,
        "summaries": summaries,
        "comparisons": comparisons,
        "per_archive_growth_retain3": growth,
        "failed_build_reference": {
            **FAILED_BUILD,
            "note": ("maximum resident set size and peak memory footprint "
                     "are different quantities: resident pages in RAM "
                     "versus the kernel footprint ledger, which also "
                     "counts compressed and swapped anonymous memory. "
                     "Neither is evidence for the other. user_s is "
                     "96.4% of real_s: the process was on CPU in user "
                     "space almost the whole time."),
        },
        "footprint_projection": {
            "kind": "modelled",
            "basis": ("network-loaded footprint of retain-3 plus the mean "
                      "per-archive footprint growth of retain-3, times the "
                      "30 archives the month's units resolve to; the unit "
                      "loop's own records are ignored"),
            "per_archive_footprint_bytes": footprint_growth,
            "archives": MONTH_ARCHIVES,
            "projected_bytes": (retain_base["phys_footprint_bytes"]
                                + MONTH_ARCHIVES * footprint_growth),
            "compare_with": "peak_memory_footprint_bytes of the failed build",
        },
        "index_rebuild": {
            "runs": rebuilds,
            "median_wall_s": rebuild_median_s,
            "median_cpu_s": statistics.median(r["cpu_s"] for r in rebuilds),
            "old_path_calls": FAILED_BUILD["daily_units"],
            "modelled_old_path_rebuild_s": (
                rebuild_median_s * FAILED_BUILD["daily_units"]),
            "kind": "modelled",
            "basis": ("the b5e1564 unit loop called find_demand_archives "
                      "without _archive_index once per daily unit, and "
                      "each such call runs _archives_for_build_key; the "
                      "median of three single-build processes times 1,950"),
        },
    }
    record["output_sha256"] = common.digest({
        "comparisons": comparisons,
        "unit_digests": {f"{r['mode']}-{r['archive_count']}": r["unit_digests"]
                         for r in runs},
        "archive_facts": {f"{r['mode']}-{r['archive_count']}":
                          r["archive_facts"] for r in runs},
    })
    published = common.publish(args.out, record)
    print(json.dumps({
        "summaries": summaries,
        "comparisons": {k: {kk: vv for kk, vv in v.items()
                            if kk != "mismatches"}
                        for k, v in comparisons.items()},
        "per_archive_growth_mb": {
            k: [round(x / 1e6, 1) for x in v] for k, v in growth.items()},
        "footprint_projection_gb": round(
            record["footprint_projection"]["projected_bytes"] / 1e9, 2),
        "index_rebuild_s": [round(r["wall_s"], 2) for r in rebuilds],
        "modelled_old_path_rebuild_s": round(
            record["index_rebuild"]["modelled_old_path_rebuild_s"], 0),
        "content_key": published["content_key"],
        "output_sha256": record["output_sha256"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
