#!/usr/bin/env python3
"""Measure the two-phase cost ledger against the per-unit one it replaces.

`wci_profile_memory_canary_v1` measured the old shape: a provider per daily
unit, never evicted, three XML parses per unit. This driver measures the
same real objects through the real `build_cost_ledger` entry point, so it
runs unchanged on both arms — at aa0ab8b the source has no `prepare_units`
and prices lazily; on the candidate it prepares by archive first.

One deviation is stated rather than hidden: every parent of the frozen
month spans exactly five build keys, so no subset of real parents is
bounded to 1-3 keys. The canary supplies its own parents over the chosen
keys' daily units; the cost source, the provider factory, the cache and the
ledger are the production ones.

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
import tracemalloc
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_two_phase_canary_v1"
GIB = 1024 ** 3
DEFAULT_FOOTPRINT_LIMIT = 3 * GIB
ARM_SOURCES = (
    "run_monthly_closure_search.py",
    "run_scenario.py",
    "traffic_sim/simulation/cost_ordered_execution.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/monthly_demand.py",
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
    from traffic_sim.simulation.deterministic_disruption import (
        VARIANT_FILENAMES)

    sized = []
    for key, item in registration["archives"].items():
        archive = Path(item["archive"])
        sized.append((sum((archive / name).stat().st_size
                          for name in VARIANT_FILENAMES.values()), key))
    sized.sort()
    return {"small": sized[0][1], "median": sized[len(sized) // 2][1],
            "large": sized[-1][1],
            "route_bytes": {key: size for size, key in sized}}


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


def _instrument(counters: Dict[str, Any], limit: int) -> None:
    """Count parses and computations, and sample memory at each seam."""
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
        footprint = _footprint()
        counters["unit_footprint"].append(footprint)
        if tracemalloc.is_tracing():
            counters["unit_traced_bytes"].append(
                tracemalloc.get_traced_memory()[0])
        if footprint > limit:
            raise MemoryError(
                f"footprint {footprint} passed the canary limit {limit} "
                f"after {len(counters['cost_computations'])} daily units")
        return compute_production(self, schedule)

    def init_spy(self, spec, **kwargs):
        gc.collect()
        counters["providers_alive_when_opening"].append(
            sum(1 for ref in counters["providers"] if ref() is not None))
        counters["footprint_at_provider_open"].append(_footprint())
        init_production(self, spec, **kwargs)
        counters["providers"].append(weakref.ref(self))

    disruption.parse_route_vehicles = parse_spy
    dd.ArchiveDisruptionProvider._compute = compute_spy
    dd.ArchiveDisruptionProvider.__init__ = init_spy


def run(args: argparse.Namespace) -> int:
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation import cost_ordered_execution as coe
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
    if args.only_median:
        order = [keys["median"]]
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
        study_provenance_key="two-phase-cost-ledger-canary",
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

    # One synthetic parent per build key, in the deterministic key order.
    # The two-phase source regroups by archive itself; the lazy arm walks
    # them in the same order, so both arms do the same work.
    parents = [SimpleNamespace(schedule_id=f"canary-parent-{key}",
                               units=tuple(units_by_key[key]))
               for key in order]

    network = NetworkCostModel()
    cache = DailyCostCache(args.cache_root)
    archive_for = resolver.archive_for
    counters: Dict[str, Any] = {
        "parses": [], "parsed_bytes": 0, "cost_computations": [],
        "providers": [], "providers_alive_when_opening": [],
        "footprint_at_provider_open": [], "unit_footprint": [],
        "unit_traced_bytes": [], "traced_after_key": []}
    if args.trace_python_heap:
        # tracemalloc measures bytes Python is actually HOLDING, which is
        # the only way to tell retention from the allocator's high-water
        # mark: `lifetime_max_phys_footprint` never goes down, so it cannot
        # answer "is the finished key still in memory?". It costs roughly a
        # factor of two in speed, so this run measures memory, not time.
        tracemalloc.start()
    _instrument(counters, args.footprint_limit_bytes)

    source_arguments = getattr(
        coe.IndependentDailyCostSource.__init__, "__code__",
        SimpleNamespace(co_varnames=())).co_varnames
    kwargs: Dict[str, Any] = {}
    if "provider_scope_key_for" in source_arguments:
        kwargs["provider_scope_key_for"] = (
            lambda schedule: str(Path(archive_for(schedule)).resolve()))
    reuse = "reuse_parsed_routes" in getattr(
        resolver.deterministic_disruption_provider, "__code__",
        SimpleNamespace(co_varnames=())).co_varnames

    def provider_for(schedule):
        if reuse:
            return resolver.deterministic_disruption_provider(
                schedule, cache=cache, network=network,
                reuse_parsed_routes=True)
        return resolver.deterministic_disruption_provider(
            schedule, cache=cache, network=network)

    source = coe.IndependentDailyCostSource(
        spec, daily_units_for=lambda parent: list(parent.units),
        provider_for=provider_for, cache=cache,
        objective_method="closure_cost_v1", **kwargs)

    swap_before = _swap_used_bytes()
    started = time.perf_counter()
    cpu_before = _cpu_s()
    stopped = None
    ledger = None
    try:
        ledger = coe.build_cost_ledger(spec, parents, source)
    except MemoryError as error:
        stopped = {"reason": str(error),
                   "units_done": len(counters["cost_computations"]),
                   "limit_bytes": args.footprint_limit_bytes}
    wall = time.perf_counter() - started
    cpu = _cpu_s() - cpu_before
    swap_after = _swap_used_bytes()
    gc.collect()
    traced_at_end = (tracemalloc.get_traced_memory()
                     if tracemalloc.is_tracing() else (None, None))

    done = 0
    per_key = {}
    for key in order:
        count = len(units_by_key[key])
        window = counters["unit_footprint"][done:done + count]
        traced = counters["unit_traced_bytes"][done:done + count]
        per_key[key] = {
            "daily_units": count,
            "route_bytes": keys["route_bytes"][key],
            "units_measured": len(window),
            "peak_footprint_bytes": max(window) if window else None,
            "footprint_after_last_unit_bytes": window[-1] if window else None,
            "python_heap_bytes_before_first_unit": (
                traced[0] if traced else None),
            "python_heap_bytes_before_last_unit": (
                traced[-1] if traced else None),
            "python_heap_peak_bytes": max(traced) if traced else None,
        }
        done += count

    outputs = {}
    if ledger is not None:
        for parent_cost in ledger.costs:
            outputs[parent_cost.candidate_id] = common.digest(
                parent_cost.to_dict())
    frozen_digest = None
    if getattr(source, "is_prepared", False):
        frozen_digest = common.digest({
            str(unit): common.digest([dict(item)
                                      for item in value["records"]])
            for unit, value in source.frozen_unit_records().items()})

    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "production_chain_rebuild_required": True,
        "claim_boundary": (
            "one process, one worker, no parallelism. It measures the real "
            "build_cost_ledger over a bounded set of build keys and nothing "
            "else; it builds no profile and adopts nothing"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "label": args.label,
        "two_phase_available": bool(kwargs),
        "code_identity": {
            "costing_sources": dict(costing_source_identity()),
            "arm_sources": {name: common.file_sha256(ROOT / name)
                            for name in ARM_SOURCES},
        },
        "design": {
            "entry_point": ("traffic_sim.simulation.cost_ordered_execution."
                            "build_cost_ledger"),
            "deviation": (
                "every parent of the frozen month spans exactly five build "
                "keys, so the canary supplies one parent per chosen key "
                "instead; the source, provider factory, cache and ledger "
                "are the production ones"),
            "workers": 1,
            "sequential": True,
            "key_choice_rule": ("small, median and large by route bytes "
                                "over the 30 registered archives"),
            "keys": {name: keys[name]
                     for name in ("small", "median", "large")},
            "order": order,
            "raw_inputs_verified": inputs_verified,
            "workload_source": {
                "registration": str(args.registration),
                "sha256": common.file_sha256(args.registration),
                "role": ("documented source of the workload choice only; "
                         "not reported valid under this code's source "
                         "identity"),
            },
        },
        "per_build_key": per_key,
        "totals": {
            "wall_s": wall,
            "cpu_s": cpu,
            "xml_parses": len(counters["parses"]),
            "xml_parses_by_file": {
                name: counters["parses"].count(name)
                for name in sorted(set(counters["parses"]))},
            "xml_bytes_read": counters["parsed_bytes"],
            "cost_computations": len(counters["cost_computations"]),
            "distinct_cost_computations": len(
                set(counters["cost_computations"])),
            "daily_units_requested": len(ordered_units),
            "providers_created": len(counters["providers"]),
        },
        "provider_lifetime": {
            "alive_when_each_provider_opened": counters[
                "providers_alive_when_opening"],
            "alive_after_the_run": sum(
                1 for ref in counters["providers"] if ref() is not None),
            "live_provider_count": (
                source.live_provider_count()
                if hasattr(source, "live_provider_count") else None),
            "evidence_note": (
                "reachability, not RSS: each entry counts providers whose "
                "weakref is still alive at that moment, after gc.collect()"),
        },
        "memory": {
            "peak_footprint_bytes": _footprint(),
            "peak_rss_bytes": _rss(),
            "footprint_at_each_provider_open": counters[
                "footprint_at_provider_open"],
            "swap": {"before_bytes": swap_before, "after_bytes": swap_after,
                     "growth_bytes": (None if swap_before is None
                                      or swap_after is None
                                      else swap_after - swap_before)},
            "python_heap_bytes_at_end": traced_at_end[0],
            "python_heap_peak_bytes": traced_at_end[1],
            "python_heap_note": (
                "tracemalloc's CURRENT figure is what answers the retention "
                "question: the lifetime physical footprint is a high-water "
                "mark that never falls, so it cannot distinguish a finished "
                "key still being held from pages the allocator simply has "
                "not returned"),
        },
        "outputs": {
            "parent_cost_digests": outputs,
            "digest_of_parent_costs": common.digest(
                {key: outputs[key] for key in sorted(outputs)}),
            "frozen_unit_digest": frozen_digest,
            "ledger": (ledger.to_dict() if ledger is not None else None),
        },
        "stopped": stopped,
        "surviving_children": _children(),
    }
    published = common.publish(args.out, record)
    print(json.dumps({
        "label": args.label, "keys": args.keys,
        "two_phase": record["two_phase_available"],
        "stopped": stopped,
        "wall_s": round(wall, 1), "cpu_s": round(cpu, 1),
        "xml_parses": record["totals"]["xml_parses"],
        "cost_computations": record["totals"]["cost_computations"],
        "providers_created": record["totals"]["providers_created"],
        "alive_when_opening": counters["providers_alive_when_opening"],
        "alive_after": record["provider_lifetime"]["alive_after_the_run"],
        "peak_footprint_mb": round(
            record["memory"]["peak_footprint_bytes"] / 1e6, 1),
        "per_key_peak_mb": {
            key: (None if item["peak_footprint_bytes"] is None
                  else round(item["peak_footprint_bytes"] / 1e6, 1))
            for key, item in per_key.items()},
        "digest_of_parent_costs": record["outputs"][
            "digest_of_parent_costs"],
        "content_key": published["content_key"]}, indent=1))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--keys", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--only-median", action="store_true",
                        help="use the median key alone, for the A/B arms")
    parser.add_argument("--label", required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--footprint-limit-bytes", type=int,
                        default=DEFAULT_FOOTPRINT_LIMIT)
    parser.add_argument("--trace-python-heap", action="store_true",
                        help="measure retention with tracemalloc; roughly "
                             "halves the speed, so never mix with a timing "
                             "run")
    parser.add_argument("--out", type=Path, required=True)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
