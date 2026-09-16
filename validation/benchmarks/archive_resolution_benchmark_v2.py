"""Archive-resolution benchmark v2: stratified counterfactual over all build keys.

Supersedes the v1 comparison, which extrapolated the old per-daily-unit
behaviour from the first 30 unit ids in sorted order. That sample need not
represent the month: build keys carry different archive sizes and different
numbers of daily units.

This driver measures, against the real archives and through the production
functions:

* the NEW path over the whole population -- one archive-index build and one
  full validation per unique build key;
* one timed full validation for EACH of the unique build keys, weighted by
  the number of daily units that actually use that key, which is what the old
  per-unit path paid for that key.

The counterfactual arm reuses the shared archive index. The old code rebuilt
that index inside every call, so those rebuilds are NOT in the counterfactual
total. The per-key timings are taken after the new path has read the same
archives, so the file-system cache is warm for them. Neither effect has a
bound that makes the ratio a guaranteed lower or upper bound, and the evidence
says so rather than claiming one.

Starts no SUMO, builds no demand and no index, and writes only its own
append-only evidence file.
"""
import argparse
import hashlib
import json
import os
import resource
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from traffic_sim.core.closure_calendar import iter_closure_schedules  # noqa: E402
from traffic_sim.core.contracts import ClosureSearchSpec  # noqa: E402
from traffic_sim.core.fingerprint import sha256_file  # noqa: E402
from traffic_sim.ops import io_phases  # noqa: E402
from traffic_sim.simulation.independent_daily import daily_unit_records  # noqa: E402
from traffic_sim.simulation.monthly_demand import (  # noqa: E402
    MonthlyDemandResolverRunner,
    _archives_for_build_key,
    find_demand_archives,
)

SCHEMA = "archive_resolution_benchmark_v2"


def _peak_rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value) * (1 if os.uname().sysname == "Darwin" else 1024)


def _digest(payload):
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _resolve_once(runs_root, required, manifest, archive_index):
    collector = io_phases.PhaseCollector()
    started = time.perf_counter()
    with io_phases.observe(collector):
        matches = find_demand_archives(
            runs_root, required, qualified_manifest=manifest,
            _archive_index=archive_index)
    elapsed = time.perf_counter() - started
    if len(matches) != 1:
        raise SystemExit(
            f"build key {required.build_key} resolved to {len(matches)} archives")
    counters = collector.report()["counters"]
    return matches[0], elapsed, counters


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--qualified-demand-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"refusing to overwrite evidence: {args.out}")

    spec = ClosureSearchSpec.from_dict(
        json.loads(args.spec.read_text(encoding="utf-8")))
    manifest = json.loads(
        args.qualified_demand_manifest.read_text(encoding="utf-8"))
    resolver = MonthlyDemandResolverRunner(
        spec, runs_root=args.runs_root, build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="archive-resolution-benchmark-v2",
        qualified_demand_manifest=manifest)

    schedules = {}
    for parent in iter_closure_schedules(spec):
        for unit_id, _identity, build_schedule in daily_unit_records(spec, parent):
            if str(unit_id) not in schedules:
                schedules[str(unit_id)] = build_schedule()
    required_by_unit = {unit: resolver._required(schedule)
                        for unit, schedule in schedules.items()}
    units_per_key = Counter(req.build_key for req in required_by_unit.values())
    required_by_key = {}
    for required in required_by_unit.values():
        required_by_key.setdefault(required.build_key, required)

    # 1. The NEW path, measured directly over the whole population.
    new_collector = io_phases.PhaseCollector()
    new_started = time.perf_counter()
    with io_phases.observe(new_collector):
        archive_index = _archives_for_build_key(args.runs_root)
        selected = {}
        for build_key, required in sorted(required_by_key.items()):
            matches = find_demand_archives(
                args.runs_root, required, qualified_manifest=manifest,
                _archive_index=archive_index)
            if len(matches) != 1:
                raise SystemExit(
                    f"build key {build_key} resolved to {len(matches)} archives")
            selected[build_key] = matches[0]
    new_wall_s = time.perf_counter() - new_started
    new_report = new_collector.report()

    # 2. One timed full validation per build key, weighted by real usage.
    per_key = []
    for build_key, required in sorted(required_by_key.items()):
        record, elapsed, counters = _resolve_once(
            args.runs_root, required, manifest, archive_index)
        if record["archive"] != selected[build_key]["archive"]:
            raise SystemExit(f"build key {build_key} selected a different archive")
        per_key.append({
            "build_key": build_key,
            "archive": str(Path(record["archive"]).resolve()),
            "daily_units": units_per_key[build_key],
            "validation_wall_s": elapsed,
            "validations": counters.get("archive_validate", 0),
            "json_reads": counters.get("archive_json_read", 0),
        })

    archive_set = [{
        "build_key": build_key,
        "archive": str(Path(record["archive"]).resolve()),
        "archive_content_key": record.get("archive_content_key"),
        "archive_manifest_sha256": record.get("archive_manifest_sha256"),
    } for build_key, record in sorted(selected.items())]

    measured_validation_s = sum(item["validation_wall_s"] for item in per_key)
    weighted_wall_s = sum(item["validation_wall_s"] * item["daily_units"]
                          for item in per_key)
    weighted_reads = sum(item["json_reads"] * item["daily_units"]
                         for item in per_key)
    driver = Path(__file__).resolve()

    record = {
        "schema": SCHEMA,
        "kind": "archive_resolution_benchmark",
        "release_evidence": False,
        "supersedes_comparison_in": "validation/archive_resolution_benchmark_20260916-v1.json",
        "measured_against": "the real September demand archives, no fixtures",
        "driver": {
            "path": str(driver.relative_to(ROOT)),
            "sha256": sha256_file(driver),
        },
        "inputs": {
            "spec": str(args.spec),
            "spec_sha256": sha256_file(args.spec),
            "runs_root": str(args.runs_root),
            "qualified_demand_manifest": str(args.qualified_demand_manifest),
            "qualified_demand_manifest_sha256": sha256_file(
                args.qualified_demand_manifest),
        },
        "archive_set": {
            "build_keys_sorted": [item["build_key"] for item in archive_set],
            "archives": archive_set,
            "digest": _digest(archive_set),
        },
        "population": {
            "daily_units": len(schedules),
            "unique_build_keys": len(required_by_key),
            "unique_archives": len({item["archive"] for item in archive_set}),
            "daily_units_per_build_key": {
                "min": min(units_per_key.values()),
                "max": max(units_per_key.values()),
                "sum": sum(units_per_key.values()),
            },
        },
        "new_path_measured": {
            "wall_s": new_wall_s,
            "archive_index_build": new_report["counters"].get(
                "archive_index_build", 0),
            "archive_validate": new_report["counters"].get("archive_validate", 0),
            "archive_json_read": new_report["counters"].get(
                "archive_json_read", 0),
            "unique_validated_archives": new_report["unique_counts"].get(
                "archive_validated_path", 0),
        },
        "every_archive_fully_validated": (
            new_report["unique_counts"].get("archive_validated_path", 0)
            == len(required_by_key)),
        "per_key_validations_measured": {
            "count": len(per_key),
            "total_wall_s": measured_validation_s,
            "items": per_key,
        },
        "counterfactual_old_path": {
            "method": ("one measured full validation per build key, "
                       "multiplied by the daily units that use that key"),
            "weighted_validations": sum(item["daily_units"] for item in per_key),
            "weighted_wall_s": weighted_wall_s,
            "weighted_json_reads": weighted_reads,
            "excludes_repeated_index_builds": True,
            "measurement_order": ("new path first, then the per-key "
                                  "validations on a warm file-system cache"),
            "bound_claimed": None,
            "bound_note": ("neither the excluded index rebuilds nor the warm "
                           "cache give the ratio a mechanically guaranteed "
                           "direction, so no lower or upper bound is claimed"),
        },
        "net_saving_s": weighted_wall_s - new_wall_s,
        "ratio_counterfactual_to_new": (weighted_wall_s / new_wall_s
                                        if new_wall_s > 0 else None),
        "peak_rss_bytes": _peak_rss_bytes(),
    }
    record["content_key"] = _digest(record)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "population": record["population"],
        "new_path_measured": record["new_path_measured"],
        "every_archive_fully_validated": record["every_archive_fully_validated"],
        "per_key_total_wall_s": round(measured_validation_s, 3),
        "weighted_wall_s": round(weighted_wall_s, 1),
        "weighted_json_reads": weighted_reads,
        "net_saving_s": round(record["net_saving_s"], 1),
        "ratio": (None if record["ratio_counterfactual_to_new"] is None
                  else round(record["ratio_counterfactual_to_new"], 1)),
        "archive_set_digest": record["archive_set"]["digest"],
        "driver_sha256": record["driver"]["sha256"],
        "peak_rss_mb": round(record["peak_rss_bytes"] / 1e6, 1),
    }, indent=1))


if __name__ == "__main__":
    main()
