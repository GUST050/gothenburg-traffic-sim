"""Archive-resolution cost model with the archive-index rebuild measured.

The archive-resolution v2 benchmark weighted one validation per build key by
its daily units, but explicitly left out what the old code (``b5e1564``) also
did on every one of its 1,950 calls: ``find_demand_archives`` without
``_archive_index`` rebuilds the index with ``_archives_for_build_key``, which
reads every archive's ``demand_meta.json``. This driver measures both parts
against the real 30 archives, each in fresh processes, and models:

    old = 1950 x index_build + sum(units_for_key x validation_for_key)
    new =    1 x index_build + sum(validation_for_key)

The index and validation parts are reported separately and the totals are
labelled modelled. The old model is compared with the failed build's own
measurements; the residual is reported but attributed to no phase.

JSON reads and bytes come from the production ``io_phases`` instrumentation
(inert unless a collector is installed). The six resolution functions are
checked to be AST-identical to ``b5e1564``, so the timed code is the code
that ran.

Starts no SUMO, builds no demand, writes no index, and writes only its own
append-only evidence file.
"""
from __future__ import annotations

import argparse
import ast
import json
import resource
import statistics
import subprocess
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

SCHEMA = "archive_index_resolution_model_v1"
PROCESSES = 3
OLD_CODE = "b5e1564"
OLD_CALLS = 1950
RESOLUTION_MODULE = "traffic_sim/simulation/monthly_demand.py"
RESOLUTION_FUNCTIONS = (
    "_archives_for_build_key", "_read", "find_demand_archives",
    "validate_demand_archive", "qualified_manifest_archive_mismatch",
    "validate_qualified_demand_manifest_shape",
)
PRODUCTION_SOURCES = (
    RESOLUTION_MODULE,
    "tools/build_window_cost_index.py",
    "traffic_sim/ops/io_phases.py",
)
PREPARATION_SOURCE = (
    "runs/step5_code_approved_20260915b/wci-index/window-cost-index.json")
OLD_RUN = {
    "source": "runs/step5_code_approved_20260915b/wci.log (/usr/bin/time -l)",
    "real_s": 31273.03,
    "user_s": 30155.20,
    "sys_s": 1103.38,
}


def _rusage():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime, usage.ru_stime


def _timed(call):
    """Run ``call`` under an io_phases collector; wall, user, sys, I/O."""
    from traffic_sim.ops import io_phases

    collector = io_phases.PhaseCollector()
    user0, sys0 = _rusage()
    started = time.perf_counter()
    with io_phases.observe(collector):
        result = call()
    wall_s = time.perf_counter() - started
    user1, sys1 = _rusage()
    report = collector.report()
    json_phase = report["phases"].get("archive_json_read", {})
    return result, {
        "wall_s": wall_s,
        "user_s": user1 - user0,
        "sys_s": sys1 - sys0,
        "json_reads": report["counters"].get("archive_json_read", 0),
        "bytes_read": json_phase.get("bytes", {}).get("read", 0),
        "json_read_wall_s": json_phase.get("inclusive_s", 0.0),
        "counters": report["counters"],
    }


def index_worker(args) -> None:
    from traffic_sim.simulation.monthly_demand import _archives_for_build_key

    runs_root = Path(args.runs_root)
    index, timing = _timed(lambda: _archives_for_build_key(runs_root))
    print(json.dumps({
        **{k: v for k, v in timing.items() if k != "counters"},
        "archive_index_build": timing["counters"].get("archive_index_build", 0),
        "archive_dirs": len(list(runs_root.glob("demand-*"))),
        "index_digest": common.digest({key: [path.name for path in paths]
                                       for key, paths in index.items()}),
        "indexed_build_keys": len(index),
        **common.process_memory(),
    }))


def _required_by_key(profile: Path):
    from tools import build_window_cost_index as builder

    bound = builder._bound_inputs(profile)
    spec = bound["spec"]
    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=bound["runs_root"], build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="subhour-phase5-raw-index",
        qualified_demand_manifest=bound["qualified_manifest"])
    units: Dict[str, set] = {}
    required = {}
    for parent in bound["parents"]:
        for unit_id, _identity, build in builder.daily_unit_records(spec, parent):
            spec_for = resolver._required(build())
            required.setdefault(spec_for.build_key, spec_for)
            units.setdefault(spec_for.build_key, set()).add(str(unit_id))
    return bound, required, {key: len(value) for key, value in units.items()}


def validation_worker(args) -> None:
    from traffic_sim.simulation.monthly_demand import (
        _archives_for_build_key, find_demand_archives)

    bound, required, units = _required_by_key(args.profile)
    # Built once, NOT part of the model: validation needs a shared index.
    index = _archives_for_build_key(bound["runs_root"])
    per_key = {}
    for build_key, spec_for in sorted(required.items()):
        matches, timing = _timed(lambda: find_demand_archives(
            bound["runs_root"], spec_for,
            qualified_manifest=bound["qualified_manifest"],
            _archive_index=index))
        if len(matches) != 1:
            raise SystemExit(f"{build_key} resolved to {len(matches)} archives")
        per_key[build_key] = {
            **{k: v for k, v in timing.items() if k != "counters"},
            "archive_validate": timing["counters"].get("archive_validate", 0),
            "archive": Path(matches[0]["archive"]).name,
            "archive_content_key": matches[0].get("archive_content_key"),
            "daily_units": units[build_key],
        }
    print(json.dumps({"per_key": per_key, **common.process_memory()}))


def _function_identity() -> Dict[str, Any]:
    def functions(source: str) -> Dict[str, str]:
        return {node.name: ast.dump(node) for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef)}

    old_source = subprocess.run(
        ["git", "show", f"{OLD_CODE}:{RESOLUTION_MODULE}"], cwd=str(ROOT),
        capture_output=True, text=True, check=True).stdout
    old = functions(old_source)
    new = functions((ROOT / RESOLUTION_MODULE).read_text(encoding="utf-8"))
    return {
        "old_code": OLD_CODE,
        "module": RESOLUTION_MODULE,
        "ast_identical": {name: (name in old and old.get(name) == new.get(name))
                          for name in RESOLUTION_FUNCTIONS},
        "scope": ("top-level functions of the module only; callees in other "
                  "modules are not compared"),
    }


def _model(index_rows: List[Dict[str, Any]],
           validation_rows: List[Dict[str, Any]]):
    per_key = {}
    for key in sorted(validation_rows[0]["per_key"]):
        samples = [row["per_key"][key] for row in validation_rows]
        per_key[key] = {
            "daily_units": samples[0]["daily_units"],
            "wall_s": [s["wall_s"] for s in samples],
            "cpu_s": [s["user_s"] + s["sys_s"] for s in samples],
            "median_wall_s": statistics.median(s["wall_s"] for s in samples),
            "median_cpu_s": statistics.median(
                s["user_s"] + s["sys_s"] for s in samples),
        }
    index_wall = statistics.median(r["wall_s"] for r in index_rows)
    index_cpu = statistics.median(r["user_s"] + r["sys_s"] for r in index_rows)

    def parts(index_calls, weighted, metric, index_value):
        validation = sum(
            (item["daily_units"] if weighted else 1) * item[metric]
            for item in per_key.values())
        index_part = index_calls * index_value
        return {"index_part_s": index_part, "validation_part_s": validation,
                "total_s": index_part + validation}

    return per_key, {
        "kind": "modelled",
        "old": {
            "formula": ("1950 x index_build + sum(units_for_key x "
                        "validation_for_key)"),
            "wall": parts(OLD_CALLS, True, "median_wall_s", index_wall),
            "cpu": parts(OLD_CALLS, True, "median_cpu_s", index_cpu),
        },
        "new": {
            "formula": "1 x index_build + sum(validation_for_key)",
            "wall": parts(1, False, "median_wall_s", index_wall),
            "cpu": parts(1, False, "median_cpu_s", index_cpu),
        },
        "units_total": sum(item["daily_units"] for item in per_key.values()),
        "index_build_median_wall_s": index_wall,
        "index_build_median_cpu_s": index_cpu,
    }


def _comparison(model: Dict[str, Any]) -> Dict[str, Any]:
    preparation = json.loads((ROOT / PREPARATION_SOURCE).read_text(
        encoding="utf-8"))["preparation_time_s"]
    old_wall = model["old"]["wall"]["total_s"]
    old_cpu = model["old"]["cpu"]["total_s"]
    observed_cpu = OLD_RUN["user_s"] + OLD_RUN["sys_s"]
    return {
        "observed": {**OLD_RUN, "user_plus_sys_s": observed_cpu,
                     "raw_index_records_preparation_s": preparation,
                     "preparation_source": PREPARATION_SOURCE},
        "modelled_old_wall_over_real": old_wall / OLD_RUN["real_s"],
        "modelled_old_wall_over_preparation": old_wall / preparation,
        "modelled_old_cpu_over_user_plus_sys": old_cpu / observed_cpu,
        "modelled_old_cpu_over_user": old_cpu / OLD_RUN["user_s"],
        "index_part_wall_over_real": (
            model["old"]["wall"]["index_part_s"] / OLD_RUN["real_s"]),
        "validation_part_wall_over_real": (
            model["old"]["wall"]["validation_part_s"] / OLD_RUN["real_s"]),
        "residual_wall_s": OLD_RUN["real_s"] - old_wall,
        "residual_cpu_s": observed_cpu - old_cpu,
        "residual_attribution": (
            "none: a negative residual means the model over-predicts; a "
            "positive one is not assigned to any phase without measurement"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", choices=("index", "validation"))
    parser.add_argument("--runs-root")
    args = parser.parse_args()
    if args.worker == "index":
        index_worker(args)
        return 0
    if args.worker == "validation":
        validation_worker(args)
        return 0
    if args.out is None or args.out.exists():
        raise SystemExit(f"evidence path missing or already exists: {args.out}")

    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    runs_root = str(profile["runs_root"])
    base = [sys.executable, str(Path(__file__).resolve()),
            "--profile", str(args.profile)]
    system_before = common.system_memory()
    index_rows = [common.run_worker(base + ["--worker", "index",
                                            "--runs-root", runs_root])
                  for _ in range(PROCESSES)]
    validation_rows = [common.run_worker(base + ["--worker", "validation"])
                       for _ in range(PROCESSES)]
    if len({row["index_digest"] for row in index_rows}) != 1:
        raise SystemExit("the three index builds disagree")
    per_key, model = _model(index_rows, validation_rows)
    if model["units_total"] != OLD_CALLS:
        raise SystemExit(f"population is {model['units_total']} units")
    comparison = _comparison(model)
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "driver": common.driver_binding(Path(__file__)),
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "function_identity": _function_identity(),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "profile": str(args.profile),
        "profile_sha256": common.file_sha256(args.profile),
        "qualified_demand_manifest": profile["qualified_demand_manifest"],
        "runs_root": runs_root,
        "method": {
            "index_build": ("one _archives_for_build_key call per fresh "
                            "process, under an io_phases collector"),
            "validation": ("one find_demand_archives call per build key with "
                           "a shared index, per fresh process; the shared "
                           "index is built first and is not timed into the "
                           "model"),
            "file_cache": ("not dropped (that needs root); the archives had "
                           "been read earlier today, and the old build ran "
                           "1,949 of its 1,950 calls on a warm cache"),
            "collector_overhead": ("the io_phases collector adds a counter "
                                   "and timer around each JSON read; the "
                                   "old build ran without one"),
        },
        "processes": PROCESSES,
        "index_builds": index_rows,
        "validation_runs": validation_rows,
        "per_key": per_key,
        "model": model,
        "comparison_with_old_run": comparison,
        "system_before": system_before,
        "system_after": common.system_memory(),
    }
    record["output_sha256"] = common.digest({
        "index_digest": index_rows[0]["index_digest"],
        "index_counts": [{k: row[k] for k in (
            "json_reads", "bytes_read", "archive_dirs", "indexed_build_keys",
            "archive_index_build")} for row in index_rows],
        "keys": {key: {k: validation_rows[0]["per_key"][key][k] for k in (
            "archive", "archive_content_key", "daily_units", "json_reads",
            "bytes_read", "archive_validate")} for key in per_key},
        "function_identity": record["function_identity"],
    })
    published = common.publish(args.out, record)

    def rounded(value):
        return round(value, 4) if isinstance(value, float) else value

    print(json.dumps({
        "index_builds": [{k: rounded(r[k]) for k in (
            "wall_s", "user_s", "sys_s", "json_reads", "bytes_read",
            "json_read_wall_s", "max_rss_bytes",
            "lifetime_max_phys_footprint_bytes")} for r in index_rows],
        "model": {side: {metric: {k: round(v, 1) for k, v in values.items()}
                         for metric, values in model[side].items()
                         if metric != "formula"}
                  for side in ("old", "new")},
        "comparison": {k: rounded(v) for k, v in comparison.items()
                       if k != "observed"},
        "function_identity": record["function_identity"]["ast_identical"],
        "content_key": published["content_key"],
        "output_sha256": record["output_sha256"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
