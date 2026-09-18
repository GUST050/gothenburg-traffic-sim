#!/usr/bin/env python3
"""Bounded memory and scaling canary for the month ledger's costing path.

The parse-reuse candidate buys 41.9% of the time in the daily-cost cache
builder but raises peak RSS about 73% for one build key. The builder gives
ONE provider 65 daily units, so three parsed route files are amortised
across all of them. The month ledger does not: it asks
``IndependentDailyCostSource`` for a provider per DAILY UNIT and keeps
every one of them in ``_providers`` for the life of the run. Whether the
candidate is safe at month scale therefore cannot be inferred from the
builder's numbers; it has to be measured on the ledger's own code path.

This canary prices 1, 2 and 3 build keys' daily units through the real
``IndependentDailyCostSource``, the real
``MonthlyDemandResolverRunner.deterministic_disruption_provider`` factory
and the real ``DailyCostCache`` — the same objects
``tools/profile_monthly_cost_ledger.py`` uses. One deviation, stated
plainly: every parent of the frozen month spans exactly five build keys, so
a 1/2/3-key bound is unreachable through real parents; the unit enumeration
is bounded instead, and nothing else is replaced.

It refuses to exhaust the machine: a footprint limit stops the run and the
stop becomes the result. Everything is published diagnostic-only.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import re
import resource
import subprocess
import sys
import time
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_profile_memory_canary_v1"
GIB = 1024 ** 3
#: Stop before the machine starts swapping for us.
DEFAULT_FOOTPRINT_LIMIT = 3 * GIB
ARM_SOURCES = (
    "run_scenario.py",
    "tools/build_daily_cost_cache.py",
    "traffic_sim/simulation/cost_ordered_execution.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
)


def _swap_used_bytes() -> Optional[int]:
    try:
        text = subprocess.run(["/usr/sbin/sysctl", "-n", "vm.swapusage"],
                              capture_output=True, text=True,
                              check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"used\s*=\s*([0-9.]+)M", text)
    return int(float(match.group(1)) * 1024 * 1024) if match else None


def _children() -> List[str]:
    try:
        text = subprocess.run(["/bin/ps", "-eo", "pid=,ppid=,comm="],
                              capture_output=True, text=True,
                              check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    mine = os.getpid()
    found = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        _pid, ppid, comm = parts
        if ppid.strip() == str(mine) and Path(comm.strip()).name not in (
                "ps", "sysctl"):
            found.append(comm.strip())
    return found


def _footprint() -> int:
    return int(common.process_memory()["lifetime_max_phys_footprint_bytes"])


def _rss() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def _cpu_s() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def chosen_keys(registration: Any) -> Dict[str, Any]:
    """Small, median and large by route bytes, deterministically."""
    from traffic_sim.simulation.deterministic_disruption import (
        VARIANT_FILENAMES)

    sized = []
    for key, item in registration["archives"].items():
        archive = Path(item["archive"])
        total = sum((archive / name).stat().st_size
                    for name in VARIANT_FILENAMES.values())
        sized.append((total, key))
    sized.sort()
    return {"small": sized[0][1],
            "median": sized[len(sized) // 2][1],
            "large": sized[-1][1],
            "route_bytes": {key: size for size, key in sized}}


def _instrument(counters: Dict[str, Any]) -> None:
    from traffic_sim.simulation import deterministic_disruption as dd
    from traffic_sim.simulation import disruption

    parse_production = disruption.parse_route_vehicles
    compute_production = dd.ArchiveDisruptionProvider._compute
    init_production = dd.ArchiveDisruptionProvider.__init__

    def parse_spy(path, **kwargs):
        counters["parses"].append(Path(path).name)
        try:
            counters["parsed_bytes"] += Path(path).stat().st_size
        except OSError:
            pass
        return parse_production(path, **kwargs)

    def compute_spy(self, schedule):
        counters["cost_computations"].append(schedule.schedule_id)
        return compute_production(self, schedule)

    def init_spy(self, spec, **kwargs):
        init_production(self, spec, **kwargs)
        counters["providers"].append(weakref.ref(self))

    disruption.parse_route_vehicles = parse_spy
    dd.ArchiveDisruptionProvider._compute = compute_spy
    dd.ArchiveDisruptionProvider.__init__ = init_spy


def _verify_raw_inputs(registration: Any, order: Sequence[str]
                       ) -> Dict[str, Any]:
    from traffic_sim.simulation.deterministic_disruption import (
        VARIANT_FILENAMES)

    verified = {}
    for key in order:
        item = registration["archives"][key]
        archive = Path(item["archive"])
        checks = {"demand_meta.json": item["demand_meta_sha256"]}
        for variant, name in VARIANT_FILENAMES.items():
            checks[name] = item["variants"][variant]
        moved = [name for name, digest in checks.items()
                 if common.file_sha256(archive / name) != digest]
        if moved:
            raise SystemExit(f"{key}: raw inputs moved: {moved}")
        verified[key] = {"archive": str(archive), "files": checks}
    return verified


def run(args: argparse.Namespace) -> int:
    """Price N build keys' daily units through the real ledger objects."""
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation.cost_ordered_execution import (
        IndependentDailyCostSource)
    from traffic_sim.simulation.deterministic_disruption import (
        DailyCostCache, NetworkCostModel, costing_source_identity)
    from traffic_sim.simulation.independent_daily import daily_unit_records
    from traffic_sim.simulation.monthly_demand import (
        MonthlyDemandResolverRunner)

    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")
    for root in (args.cache_root, args.release_root):
        if root.exists():
            raise SystemExit(f"the canary needs a fresh root: {root}")

    registration = json.loads(args.registration.read_text(encoding="utf-8"))
    keys = chosen_keys(registration)
    order = [keys["small"], keys["median"], keys["large"]][:args.keys]
    spec = ClosureSearchSpec.from_dict(registration["spec"])
    manifest_ref = registration["qualified_demand_manifest"]
    manifest_path = Path(manifest_ref["path"])
    if common.file_sha256(manifest_path) != manifest_ref["sha256"]:
        raise SystemExit("the qualified demand manifest moved")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runs_root = Path(registration["archives"][order[0]]["archive"]).parent
    inputs_verified = _verify_raw_inputs(registration, order)

    resolver = MonthlyDemandResolverRunner(
        spec, runs_root=runs_root, build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="profile-memory-canary",
        qualified_demand_manifest=manifest,
        release_root=args.release_root)

    units_by_key: Dict[str, List[Any]] = {key: [] for key in order}
    seen = set()
    wanted = set(order)
    for parent in iter_closure_schedules(spec):
        for unit_id, _identity, build in daily_unit_records(spec, parent):
            if str(unit_id) in seen:
                continue
            schedule = build()
            key = resolver._required(schedule).build_key
            if key in wanted:
                seen.add(str(unit_id))
                units_by_key[key].append((str(unit_id), schedule))
    ordered_units = [unit for key in order for unit in units_by_key[key]]
    resolver.prepare([schedule for _unit, schedule in ordered_units])

    network = NetworkCostModel()
    cache = DailyCostCache(args.cache_root)
    counters: Dict[str, Any] = {"parses": [], "parsed_bytes": 0,
                                "cost_computations": [], "providers": []}
    _instrument(counters)
    source = IndependentDailyCostSource(
        spec,
        daily_units_for=lambda parent: list(ordered_units),
        provider_for=lambda schedule:
            resolver.deterministic_disruption_provider(
                schedule, cache=cache, network=network),
        cache=cache, objective_method="closure_cost_v1")

    swap_before = _swap_used_bytes()
    per_key: Dict[str, Any] = {}
    outputs: Dict[str, str] = {}
    stopped = None
    run_started = time.perf_counter()
    for key in order:
        marks: Dict[str, Any] = {"units_done": 0, "footprint_bytes": [],
                                 "providers_held": []}
        key_started = time.perf_counter()
        cpu_before = _cpu_s()
        parses_before = len(counters["parses"])
        computed_before = len(counters["cost_computations"])
        for unit_id, schedule in units_by_key[key]:
            provider = source._provider(schedule)
            records = provider.disruption(schedule)
            outputs[unit_id] = common.digest(
                [dict(item) for item in records])
            marks["units_done"] += 1
            footprint = _footprint()
            marks["footprint_bytes"].append(footprint)
            marks["providers_held"].append(len(source._providers))
            if footprint > args.footprint_limit_bytes:
                stopped = {
                    "build_key": key,
                    "units_done": marks["units_done"],
                    "units_expected": len(units_by_key[key]),
                    "footprint_bytes": footprint,
                    "limit_bytes": args.footprint_limit_bytes,
                    "reason": ("footprint limit reached; the canary stops "
                               "rather than drive the machine into swap"),
                }
                break
        marks["wall_s"] = time.perf_counter() - key_started
        marks["cpu_s"] = _cpu_s() - cpu_before
        marks["xml_parses"] = len(counters["parses"]) - parses_before
        marks["cost_computations"] = (len(counters["cost_computations"])
                                      - computed_before)
        marks["route_bytes"] = keys["route_bytes"][key]
        marks["peak_footprint_bytes"] = max(marks["footprint_bytes"] or [0])
        marks["peak_rss_bytes"] = _rss()
        marks["providers_held_at_end"] = len(source._providers)
        gc.collect()
        marks["providers_alive_after_gc"] = sum(
            1 for ref in counters["providers"] if ref() is not None)
        per_key[key] = marks
        if stopped is not None:
            break

    swap_after = _swap_used_bytes()
    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "production_chain_rebuild_required": True,
        "claim_boundary": (
            "one process, one worker, no parallelism; it measures how the "
            "month ledger's own costing objects retain memory as build keys "
            "accumulate, and nothing else. It builds no profile, starts no "
            "month cache build and adopts nothing"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "label": args.label,
        "code_identity": {
            "costing_sources": dict(costing_source_identity()),
            "arm_sources": {name: common.file_sha256(ROOT / name)
                            for name in ARM_SOURCES},
        },
        "design": {
            "real_objects": [
                "traffic_sim.simulation.cost_ordered_execution."
                "IndependentDailyCostSource",
                "MonthlyDemandResolverRunner."
                "deterministic_disruption_provider",
                "traffic_sim.simulation.deterministic_disruption."
                "DailyCostCache",
                "NetworkCostModel",
            ],
            "deviation": (
                "every parent of the frozen month spans exactly five build "
                "keys, so no subset of real parents is bounded to 1, 2 or 3 "
                "keys. The daily-unit enumeration is bounded instead; the "
                "provider factory, the cost source and its retention are "
                "the production ones"),
            "workers": 1,
            "sequential": True,
            "workload_source": {
                "registration": str(args.registration),
                "sha256": common.file_sha256(args.registration),
                "role": ("documented source of the workload choice only; "
                         "not reported valid under this code's source "
                         "identity"),
            },
            "key_choice_rule": ("small, median and large by route bytes "
                                "over the 30 registered archives"),
            "keys": {name: keys[name]
                     for name in ("small", "median", "large")},
            "order": order,
            "raw_inputs_verified": inputs_verified,
        },
        "per_build_key": per_key,
        "totals": {
            "wall_s": time.perf_counter() - run_started,
            "xml_parses": len(counters["parses"]),
            "xml_bytes_read": counters["parsed_bytes"],
            "cost_computations": len(counters["cost_computations"]),
            "distinct_cost_computations": len(
                set(counters["cost_computations"])),
            "providers_created": len(counters["providers"]),
            "providers_held_at_end": len(source._providers),
            "units_priced": len(outputs),
        },
        "memory": {
            "peak_footprint_bytes": _footprint(),
            "peak_rss_bytes": _rss(),
            "swap": {"before_bytes": swap_before, "after_bytes": swap_after,
                     "growth_bytes": (None if swap_before is None
                                      or swap_after is None
                                      else swap_after - swap_before)},
            "evidence_note": (
                "RSS is reported but is never the proof on its own: CPython "
                "can hold freed pages. The reachability evidence is "
                "providers_held and providers_alive_after_gc, which count "
                "the live owners of the parsed routes"),
        },
        "outputs": {"unit_digests": outputs,
                    "digest_of_digests": common.digest(
                        {key: outputs[key] for key in sorted(outputs)})},
        "stopped": stopped,
        "surviving_children": _children(),
    }
    published = common.publish(args.out, record)
    print(json.dumps({
        "label": args.label, "keys": args.keys, "stopped": stopped,
        "per_build_key": {
            key: {"units_done": item["units_done"],
                  "wall_s": round(item["wall_s"], 1),
                  "xml_parses": item["xml_parses"],
                  "peak_footprint_mb": round(
                      item["peak_footprint_bytes"] / 1e6, 1),
                  "providers_held_at_end": item["providers_held_at_end"],
                  "providers_alive_after_gc": item[
                      "providers_alive_after_gc"]}
            for key, item in per_key.items()},
        "totals": record["totals"],
        "content_key": published["content_key"]}, indent=1))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--keys", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--footprint-limit-bytes", type=int,
                        default=DEFAULT_FOOTPRINT_LIMIT)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
