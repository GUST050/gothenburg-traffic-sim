#!/usr/bin/env python3
"""One missing build key of the independent daily-cost cache, measured.

Serial, one worker, one fresh diagnostic cache root. The build key is chosen
deterministically: the median of the missing keys by route bytes, then by
build key. The driver

1. builds that batch with ``tools/build_daily_cost_cache.py``;
2. recomputes every daily unit again in a fresh provider with no cache and
   compares the records field by field;
3. runs the verification a second time and proves that every unit is reused
   and that no cost is computed;
4. records resources, the archive and catalog bytes before and after, SUMO
   processes and child processes;
5. estimates the remaining build keys from this measurement and the two
   earlier bounded canaries, as a range with its spread.

It starts no SUMO process, builds no demand, writes no index and never
touches the existing canary cache roots. The evidence is append-only and
diagnostic (``release_evidence: false``).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_daily_cache_canary_v1"
PRODUCTION_SOURCES = (
    "tools/build_daily_cost_cache.py",
    "tools/freeze_wci_month_case.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/monthly_demand.py",
    "run_scenario.py",
)
VARIANTS = ("calibrated.rou.xml", "calibrated_v1.rou.xml",
            "calibrated_v2.rou.xml")
EXPECTED_UNITS = 65


def _sumo_processes() -> int:
    listing = subprocess.run(["/bin/ps", "-Ao", "comm="], check=True,
                             capture_output=True, text=True).stdout
    return sum(1 for line in listing.splitlines()
               if Path(line.strip()).name.lower().startswith("sumo"))


def _children() -> List[int]:
    """Live children of this process, excluding the listing itself.

    The `ps` that answers the question is a child of this process while it
    runs, so counting it would always report one survivor.
    """
    listing = subprocess.run(["/bin/ps", "-Ao", "pid=,ppid=,comm="],
                             check=True, capture_output=True,
                             text=True).stdout
    children = []
    for row in listing.splitlines():
        parts = row.split(None, 2)
        if len(parts) != 3 or int(parts[1]) != os.getpid():
            continue
        if Path(parts[2].strip()).name in ("ps", "sysctl"):
            continue
        children.append(int(parts[0]))
    return children


def _bound_bytes(registration, build_key) -> Dict[str, str]:
    archive = Path(registration["archives"][build_key]["archive"])
    files = {name: common.file_sha256(archive / name)
             for name in ("demand_meta.json", *VARIANTS)}
    for pool, item in registration["catalogs"].items():
        path = Path(item["path"])
        files[f"catalog:{pool}"] = common.file_sha256(path)
        files[f"catalog-meta:{pool}"] = common.file_sha256(
            path.with_name("catalog.meta.json"))
    return files


def _canary_cache_state(cache_root: Path) -> str:
    """Every existing canary oracle record, by path and content."""
    paths = sorted(Path(cache_root).parent.glob(
        "effect-canary-oracle-*/**/*.json"))
    return common.digest({str(path): common.file_sha256(path)
                          for path in paths})


def choose_build_key(preflight: Dict[str, Any],
                     registration: Dict[str, Any]) -> Dict[str, Any]:
    """The median missing build key by route bytes, then by key."""
    missing = [key for key, item
               in preflight["cache"]["per_build_key"].items()
               if item["hits"] == 0]
    sizes = {key: sum((Path(registration["archives"][key]["archive"])
                       / name).stat().st_size for name in VARIANTS)
             for key in missing}
    ordered = sorted(missing, key=lambda key: (sizes[key], key))
    chosen = ordered[len(ordered) // 2]
    return {"rule": ("the median of the missing build keys by route bytes, "
                     "then by build key"),
            "missing_build_keys": len(missing),
            "chosen": chosen,
            "route_bytes": sizes[chosen],
            "route_bytes_span": {"low": sizes[ordered[0]],
                                 "high": sizes[ordered[-1]]},
            "order": ordered}


def _units(builder, registration, bound, build_key):
    from traffic_sim.core.contracts import ClosureSearchSpec

    spec = ClosureSearchSpec.from_dict(registration["spec"])
    grouped = builder.units_of(spec, bound["runs_root"],
                               bound["qualified_manifest"])
    return spec, grouped[build_key]


def _build(builder, registration, build_key, units, cache_root, status_path):
    """One serial batch, with its own status file."""
    status = builder.Status(status_path, requested=[build_key],
                            cache_root=cache_root,
                            registration=registration)
    status.update(state="running", daily_units_expected=len(units),
                  current_build_key=build_key, phase="computing")
    started = time.perf_counter()
    per_unit: List[float] = []
    last = [started]

    def on_unit(_unit_id, done):
        now = time.perf_counter()
        per_unit.append(now - last[0])
        last[0] = now
        status.update(phase=f"computing {done}/{len(units)}",
                      daily_units_done=done)

    marker = builder.build_batch(registration, build_key,
                                 cache_root=cache_root, units=units,
                                 on_unit=on_unit)
    status.update(state="done", phase="published",
                  completed_build_keys=[build_key],
                  output_content_keys={build_key: marker["content_key"]})
    return marker, {"wall_s": time.perf_counter() - started,
                    "per_unit_s": per_unit,
                    "status": json.loads(json.dumps(status.state,
                                                    default=str))}


def _fresh_check(spec, registration, build_key, units, cache_root):
    """Recompute every unit with no cache and compare with what was stored."""
    from traffic_sim.simulation.deterministic_disruption import (
        ArchiveDisruptionProvider, DailyCostCache, NetworkCostModel)

    archive = Path(registration["archives"][build_key]["archive"])
    provider = ArchiveDisruptionProvider(spec, archive=archive,
                                         network=NetworkCostModel(),
                                         cache=None)
    cache = DailyCostCache(Path(cache_root))
    started = time.perf_counter()
    mismatches = []
    checked = 0
    for unit_id, _identity, schedule in units:
        stored = cache.load(provider.cache_identity(schedule))
        fresh = [dict(item) for item in provider.disruption(schedule)]
        checked += 1
        if stored is None or [dict(item) for item in stored] != fresh:
            mismatches.append(str(unit_id))
    return {"units_checked": checked, "mismatches": mismatches,
            "identical": not mismatches,
            "wall_s": time.perf_counter() - started}


def _reuse_pass(builder, registration, build_key, units, cache_root,
                status_path):
    """A second run must reuse every unit and compute nothing."""
    from traffic_sim.simulation import deterministic_disruption as dd

    computed: List[str] = []
    production = dd.ArchiveDisruptionProvider._compute

    def spy(self, schedule):
        computed.append(schedule.schedule_id)
        return production(self, schedule)

    dd.ArchiveDisruptionProvider._compute = spy
    started = time.perf_counter()
    try:
        identity = builder.identity_of(registration, build_key)
        problems = builder.verify_batch(cache_root, identity, units)
        status = builder.Status(status_path, requested=[build_key],
                                cache_root=cache_root,
                                registration=registration)
        status.update(state="done", phase="reused",
                      cache_hits=len(units) if not problems else 0,
                      reused_build_keys=[build_key] if not problems else [])
    finally:
        dd.ArchiveDisruptionProvider._compute = production
    return {"verify_problems": problems,
            "cache_hits": len(units) if not problems else 0,
            "cost_computations": len(computed),
            "wall_s": time.perf_counter() - started}


def _remaining_estimate(per_unit: Sequence[float], canaries, remaining_keys,
                        units_per_key) -> Dict[str, Any]:
    """This batch and the earlier canaries, as a range with its spread."""
    rates = [{"source": "this batch",
              "per_unit_s": statistics.median(per_unit)}]
    for path in canaries:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        oracle = record["oracle"]
        rates.append({"source": str(path),
                      "per_unit_s": oracle["wall_s"] / oracle["daily_units"]})
    values = [item["per_unit_s"] for item in rates]
    units = remaining_keys * units_per_key
    return {
        "kind": "modelled from three measured per-unit rates",
        "rates": rates,
        "per_unit_s": {"low": min(values),
                       "median": statistics.median(values),
                       "high": max(values)},
        "this_batch_per_unit_s": {
            "median": statistics.median(per_unit),
            "mean": statistics.fmean(per_unit),
            "min": min(per_unit), "max": max(per_unit),
            "stdev": statistics.pstdev(per_unit)},
        "remaining_build_keys": remaining_keys,
        "remaining_daily_units": units,
        "serial_estimate_s": {"low": min(values) * units,
                              "median": statistics.median(values) * units,
                              "high": max(values) * units},
        "caveats": [
            "three rates, measured on different archives and machine "
            "performance states; the spread is the evidence, not the best "
            "value",
            "no parallelism is proposed or measured",
            "a month ledger run does more than the oracle, so this is a "
            "lower bound for that run",
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--oracle-canary", type=Path, action="append",
                        required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")
    if args.cache_root.exists() and any(args.cache_root.iterdir()):
        raise SystemExit(f"cache root must be fresh: {args.cache_root}")

    from tools import build_daily_cost_cache as builder
    from tools import build_window_cost_index as wci
    from tools import freeze_wci_month_case as month

    registration = json.loads(args.registration.read_text(encoding="utf-8"))
    problems = month.verify_registration(registration)
    if problems:
        raise SystemExit(f"registration does not verify: {problems}")
    preflight = json.loads(args.preflight.read_text(encoding="utf-8"))
    choice = choose_build_key(preflight, registration)
    build_key = choice["chosen"]

    sampler = common.Sampler()
    bound = wci._bound_inputs(args.profile)
    spec, units = _units(builder, registration, bound, build_key)
    cache_state = Path(bound["cache_root"])
    before = {"sumo": _sumo_processes(), "children": _children(),
              "bytes": _bound_bytes(registration, build_key),
              "canary_caches": _canary_cache_state(cache_state)}
    marker, build = _build(builder, registration, build_key, units,
                           args.cache_root, args.status_dir / "build.json")
    sampler.sample("built")
    fresh = _fresh_check(spec, registration, build_key, units,
                         args.cache_root)
    sampler.sample("fresh_checked")
    reuse = _reuse_pass(builder, registration, build_key, units,
                        args.cache_root, args.status_dir / "reuse.json")
    sampler.sample("reused")
    after = {"sumo": _sumo_processes(), "children": _children(),
             "bytes": _bound_bytes(registration, build_key),
             "canary_caches": _canary_cache_state(cache_state)}
    checks = {
        "all_units_built": (marker["daily_units"] == len(units)
                            == EXPECTED_UNITS),
        "fresh_check_identical": fresh["identical"],
        "second_run_reuses_every_unit": (not reuse["verify_problems"]
                                         and reuse["cache_hits"]
                                         == EXPECTED_UNITS),
        "second_run_computes_nothing": reuse["cost_computations"] == 0,
        "no_sumo_process": after["sumo"] <= before["sumo"] == 0,
        "no_child_processes_left": not after["children"],
        "archive_and_catalog_bytes_unchanged": (after["bytes"]
                                                == before["bytes"]),
        "existing_canary_caches_untouched": (after["canary_caches"]
                                             == before["canary_caches"]),
        "no_demand_archive_created": not list(
            Path(args.cache_root).glob("**/demand-*")),
    }
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "claim_boundary": ("one build key of the independent daily-cost "
                           "cache, serial and diagnostic; no month build"),
        "driver": common.driver_binding(Path(__file__)),
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "registration": {"path": str(args.registration),
                         "content_key": registration["content_key"],
                         "chosen_edge": registration["chosen_edge"]},
        "preflight": {"path": str(args.preflight),
                      "content_key": preflight["content_key"]},
        "build_key_choice": choice,
        "cache_root": str(args.cache_root),
        "batch": {"content_key": marker["content_key"],
                  "daily_units": marker["daily_units"],
                  "provider_identity_digest": marker[
                      "provider_identity_digest"],
                  "timing": marker["timing"], "peak": marker["peak"]},
        "build": {"wall_s": build["wall_s"],
                  "per_unit_s": build["per_unit_s"],
                  "status": build["status"]},
        "fresh_independent_check": fresh,
        "reuse_pass": reuse,
        "before": before, "after": after,
        "checks": checks,
        "remaining_estimate": _remaining_estimate(
            build["per_unit_s"], args.oracle_canary,
            choice["missing_build_keys"] - 1, len(units)),
        "memory": {"samples": sampler.samples},
    }
    record["output_sha256"] = common.digest({
        key: record[key] for key in ("batch", "fresh_independent_check",
                                     "reuse_pass", "checks",
                                     "remaining_estimate")})
    published = common.publish(args.out, record)
    print(json.dumps({
        "status": record["status"],
        "build_key": build_key,
        "daily_units": marker["daily_units"],
        "build_wall_s": round(build["wall_s"], 1),
        "per_unit_s_median": round(statistics.median(build["per_unit_s"]), 3),
        "peak": marker["peak"],
        "checks": checks,
        "remaining": record["remaining_estimate"]["serial_estimate_s"],
        "content_key": published["content_key"],
    }, indent=1))
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
