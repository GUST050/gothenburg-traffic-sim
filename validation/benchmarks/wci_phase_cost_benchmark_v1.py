"""Bounded WindowCostIndex phase-cost benchmark on a content-chosen sample.

Measures the remaining cost of the raw index path per archive, through the
production functions and without changing them:

* network load and verified archive inputs;
* ``xml_parse`` (``parse_route_vehicles``);
* ``route_vehicle_grouping`` (``ParsedWindowCostIndex`` construction:
  crossing filter plus closure offsets);
* ``window_aggregation`` for every daily unit the archive serves, split into
  the FIRST window per variant and the later ones.

``shortest_path_detour`` is not emitted on the index path: shortest paths are
resolved lazily and memoised inside ``window_aggregation``. The first-window
split is what makes that cache-filling cost visible.

Each chosen archive runs in a fresh process (cold) and is then repeated in
the same process (warm); both runs must produce identical output hashes.
Month-scale figures are MODELLED from the sample, never measured.

Starts no SUMO, builds no demand, writes no index, and writes only its own
append-only evidence file.
"""
import argparse
import hashlib
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PRODUCTION_SOURCES = (
    "tools/build_window_cost_index.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/window_cost_index.py",
)
VARIANTS = ("q10", "q50", "q90")
REQUIRED_SAVING_S = 20962.0
REMAINING_BUILD_S = 28282.0
BASELINE_S = 7320.348


def _digest(payload):
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _usage():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def _peak_rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value) * (1 if os.uname().sysname == "Darwin" else 1024)


def _units_for(profile, build_key):
    from tools import build_window_cost_index as builder
    bound = builder._bound_inputs(Path(profile))
    spec = bound["spec"]
    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=bound["runs_root"], build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="wci-phase-cost-benchmark")
    schedules = {}
    for parent in bound["parents"]:
        for unit_id, _identity, build in builder.daily_unit_records(spec, parent):
            unit_id = str(unit_id)
            if unit_id in schedules:
                continue
            schedule = build()
            if resolver._required(schedule).build_key == build_key:
                schedules[unit_id] = schedule
    return spec, schedules


def _measure_once(spec, archive, schedules, network):
    from tools import build_window_cost_index as builder
    from traffic_sim.simulation.disruption import (
        build_parsed_window_cost_index, parse_route_vehicles)
    from traffic_sim.simulation.deterministic_disruption import closure_seconds

    phases = {"xml_parse": 0.0, "route_vehicle_grouping": 0.0}

    def timing(name, elapsed):
        if name in phases:
            phases[name] += float(elapsed)

    wall_started, cpu_started = time.perf_counter(), _usage()
    inputs_started = time.perf_counter()
    provider = builder.ArchiveDisruptionProvider(
        spec, archive=archive, network=network, cache=None)
    inputs = provider.inputs
    inputs_s = time.perf_counter() - inputs_started

    counts = {"vehicles": 0, "unique_routes": 0, "unique_od": 0,
              "crossing_vehicles": 0}
    indexes = {}
    for variant, path in sorted(inputs.variant_paths.items()):
        parsed = parse_route_vehicles(path, timing=timing)
        counts["vehicles"] += len(parsed)
        counts["unique_routes"] += len({vehicle[0] for vehicle in parsed})
        counts["unique_od"] += len({(vehicle[0][0], vehicle[0][-1])
                                    for vehicle in parsed if vehicle[0]})
        index = build_parsed_window_cost_index(
            parsed, set(spec.directed_edges), network.edge_time,
            network.edge_len, adjacency=network.adjacency,
            destination_access=network.destination_access, timing=timing)
        # Measurement-only read of the crossing set the index built.
        counts["crossing_vehicles"] += len(index._crossing)
        indexes[variant] = index

    first_window_s = later_windows_s = 0.0
    outputs = {}
    windows = 0
    for position, unit_id in enumerate(sorted(schedules)):
        closures = closure_seconds(
            spec, schedules[unit_id], epoch=inputs.epoch,
            duration_s=inputs.duration_s)
        rows = []
        for variant in VARIANTS:
            started = time.perf_counter()
            rows.append(indexes[variant].disruption(closures))
            elapsed = time.perf_counter() - started
            if position == 0:
                first_window_s += elapsed
            else:
                later_windows_s += elapsed
            windows += 1
        outputs[unit_id] = rows

    return {
        "wall_s": time.perf_counter() - wall_started,
        "cpu_s": _usage() - cpu_started,
        "inputs_s": inputs_s,
        "xml_parse_s": phases["xml_parse"],
        "route_vehicle_grouping_s": phases["route_vehicle_grouping"],
        "first_window_s": first_window_s,
        "later_windows_s": later_windows_s,
        "windows": windows,
        **counts,
        "output_sha256": _digest(outputs),
    }


def worker(args):
    from traffic_sim.simulation.deterministic_disruption import NetworkCostModel

    spec, schedules = _units_for(args.profile, args.build_key)
    network_started, network_cpu = time.perf_counter(), _usage()
    network = NetworkCostModel()
    network_load = {"wall_s": time.perf_counter() - network_started,
                    "cpu_s": _usage() - network_cpu}
    archive = Path(args.archive)
    cold = _measure_once(spec, archive, schedules, network)
    warm = _measure_once(spec, archive, schedules, network)
    print(json.dumps({
        "build_key": args.build_key,
        "archive": str(archive),
        "daily_units": len(schedules),
        "network_load": network_load,
        "cold": cold,
        "warm": warm,
        "outputs_identical": cold["output_sha256"] == warm["output_sha256"],
        "peak_rss_bytes": _peak_rss_bytes(),
    }))


def _select(profile):
    """Content-bound choice: min, median and max archive by route bytes."""
    from tools import build_window_cost_index as builder
    from traffic_sim.simulation.deterministic_disruption import ArchiveInputs

    bound = builder._bound_inputs(Path(profile))
    spec = bound["spec"]
    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=bound["runs_root"], build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="wci-phase-cost-benchmark")
    required = {}
    for parent in bound["parents"]:
        for _unit, _identity, build in builder.daily_unit_records(spec, parent):
            spec_for = resolver._required(build())
            required.setdefault(spec_for.build_key, spec_for)
    index = builder._archives_for_build_key(bound["runs_root"])
    candidates = []
    for build_key, spec_for in sorted(required.items()):
        matches = builder.find_demand_archives(
            bound["runs_root"], spec_for,
            qualified_manifest=bound["qualified_manifest"],
            _archive_index=index)
        archive = Path(matches[0]["archive"]).resolve()
        inputs = ArchiveInputs.from_archive(archive)
        route_bytes = sum(Path(path).stat().st_size
                          for path in inputs.variant_paths.values())
        candidates.append({
            "build_key": build_key,
            "archive": str(archive),
            "archive_content_key": matches[0].get("archive_content_key"),
            "route_bytes": route_bytes,
        })
    ordered = sorted(candidates, key=lambda item: (item["route_bytes"],
                                                   item["build_key"]))
    chosen = [ordered[0], ordered[len(ordered) // 2], ordered[-1]]
    return bound, candidates, chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--build-key")
    parser.add_argument("--archive")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if args.out is None or args.out.exists():
        raise SystemExit(f"evidence path missing or already exists: {args.out}")

    selection_started = time.perf_counter()
    _bound, candidates, chosen = _select(args.profile)
    selection_s = time.perf_counter() - selection_started

    results = []
    for item in chosen:
        completed = subprocess.run(
            [sys.executable, __file__, "--worker", "--profile", str(args.profile),
             "--build-key", item["build_key"], "--archive", item["archive"]],
            capture_output=True, text=True, check=True, cwd=str(ROOT),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        line = [row for row in completed.stdout.splitlines()
                if row.startswith("{")][-1]
        results.append({**json.loads(line), "route_bytes": item["route_bytes"]})

    def mean(field):
        return statistics.mean(result["cold"][field] for result in results)

    per_archive_cold_s = statistics.mean(
        result["network_load"]["wall_s"] + result["cold"]["wall_s"]
        for result in results)
    month = {
        "kind": "modelled",
        "basis": ("mean cold per-archive cost over the three sampled archives "
                  "times the 30 archives of the month; network load is paid "
                  "once per process in production, so counting it per archive "
                  "overstates the month"),
        "archives": len(candidates),
        "xml_parse_s": mean("xml_parse_s") * len(candidates),
        "route_vehicle_grouping_s": mean("route_vehicle_grouping_s") * len(candidates),
        "first_window_s": mean("first_window_s") * len(candidates),
        "later_windows_s": mean("later_windows_s") * len(candidates),
        "inputs_s": mean("inputs_s") * len(candidates),
        "total_including_network_per_archive_s": per_archive_cold_s * len(candidates),
    }
    phase_month = {key: month[key] for key in (
        "xml_parse_s", "route_vehicle_grouping_s", "first_window_s",
        "later_windows_s", "inputs_s")}
    largest_phase, largest_s = max(phase_month.items(), key=lambda kv: kv[1])
    record = {
        "schema": "wci_phase_cost_benchmark_v1",
        "release_evidence": False,
        "driver": {"path": str(Path(__file__).resolve().relative_to(ROOT)),
                   "sha256": hashlib.sha256(
                       Path(__file__).read_bytes()).hexdigest()},
        "production_sources": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in PRODUCTION_SOURCES},
        "profile": str(args.profile),
        "selection": {
            "rule": ("sort the 30 qualified archives by the summed bytes of "
                     "their three variant route files (ties by build key) and "
                     "take the minimum, the median (index 15) and the maximum"),
            "candidates": candidates,
            "chosen": chosen,
            "digest": _digest({"candidates": candidates, "chosen": chosen}),
            "selection_wall_s": selection_s,
        },
        "archives": results,
        "all_outputs_identical_cold_vs_warm": all(
            result["outputs_identical"] for result in results),
        "month_model": month,
        "targets": {
            "baseline_s": BASELINE_S,
            "remaining_build_s": REMAINING_BUILD_S,
            "required_saving_s": REQUIRED_SAVING_S,
        },
        "largest_modelled_phase": {"phase": largest_phase, "month_s": largest_s},
        "largest_phase_can_cover_required_saving": largest_s >= REQUIRED_SAVING_S,
    }
    record["content_key"] = _digest(record)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "chosen_route_bytes": [item["route_bytes"] for item in chosen],
        "per_archive": [{
            "build_key": r["build_key"][:10],
            "units": r["daily_units"],
            "network_load_s": round(r["network_load"]["wall_s"], 2),
            "cold": {k: (round(v, 3) if isinstance(v, float) else v)
                     for k, v in r["cold"].items() if k != "output_sha256"},
            "warm_wall_s": round(r["warm"]["wall_s"], 3),
            "identical": r["outputs_identical"],
            "peak_rss_mb": round(r["peak_rss_bytes"] / 1e6, 1),
        } for r in results],
        "month_model": {k: (round(v, 1) if isinstance(v, float) else v)
                        for k, v in month.items() if k != "basis"},
        "largest_modelled_phase": record["largest_modelled_phase"],
        "can_cover_required_saving": record["largest_phase_can_cover_required_saving"],
    }, indent=1))


if __name__ == "__main__":
    main()
