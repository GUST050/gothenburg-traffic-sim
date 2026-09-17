#!/usr/bin/env python3
"""Ledger and daily-cost-cache preflight for the frozen month case.

Reads only. For the registered month case it derives every daily unit's
cache identity exactly as the production provider does, looks that identity
up in each candidate cache root, and reports hits and misses per build key.
A file that exists but does not describe the identity asked for, or that
cannot be read, is a miss and never a hit.

It also records what the guarded runner says about the existing month
profile, estimates the oracle cost of the missing units from the measured
bounded canaries, and lists the files an approved build would create. It
starts no build, no oracle, no SUMO process and writes nothing but its own
append-only evidence (``release_evidence: false``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_month_cache_preflight_v1"
PRODUCTION_SOURCES = (
    "tools/build_window_cost_index.py",
    "tools/closure_effect_eligibility.py",
    "tools/freeze_wci_month_case.py",
    "tools/guarded_wci_build.py",
    "tools/wci_guarded_child.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/window_cost_index.py",
)


def _units_by_key(builder, spec, bound) -> Dict[str, List[Any]]:
    """Every daily unit of the month, grouped by its demand build key."""
    from traffic_sim.core.closure_calendar import iter_closure_schedules

    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=bound["runs_root"], build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="wci-month-cache-preflight",
        qualified_demand_manifest=bound["qualified_manifest"])
    units: Dict[str, List[Any]] = {}
    seen = set()
    for parent in iter_closure_schedules(spec):
        for unit_id, _identity, build in builder.daily_unit_records(spec,
                                                                    parent):
            unit_id = str(unit_id)
            if unit_id in seen:
                continue
            seen.add(unit_id)
            schedule = build()
            units.setdefault(resolver._required(schedule).build_key,
                             []).append((unit_id, schedule))
    return units


def _lookup(caches, provider, schedule) -> Optional[str]:
    """The first cache root that really holds this unit's cost."""
    from traffic_sim.simulation.deterministic_disruption import (
        DisruptionError)

    identity = provider.cache_identity(schedule)
    for name, cache in caches.items():
        try:
            stored = cache.load(identity)
        except DisruptionError:
            continue  # a file that does not describe this identity is a miss
        if stored:
            return name
    return None


def _cache_state(builder, registration, bound, roots) -> Dict[str, Any]:
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation.deterministic_disruption import (
        ArchiveDisruptionProvider, DailyCostCache, NetworkCostModel)

    spec = ClosureSearchSpec.from_dict(registration["spec"])
    units = _units_by_key(builder, spec, bound)
    caches = {name: DailyCostCache(Path(path)) for name, path in roots.items()
              if Path(path).is_dir()}
    network = NetworkCostModel()
    per_key: Dict[str, Any] = {}
    hits_by_root: Dict[str, int] = {name: 0 for name in caches}
    for key, records in sorted(units.items()):
        archive = Path(registration["archives"][key]["archive"])
        provider = ArchiveDisruptionProvider(spec, archive=archive,
                                             network=network, cache=None)
        found = [_lookup(caches, provider, schedule)
                 for _unit, schedule in records]
        for name in found:
            if name is not None:
                hits_by_root[name] += 1
        per_key[key] = {
            "archive": str(archive),
            "daily_units": len(records),
            "hits": sum(1 for name in found if name is not None),
            "misses": sum(1 for name in found if name is None),
            "roots": sorted({name for name in found if name is not None}),
            "provider_identity_digest": common.digest(
                dict(provider.identity())),
        }
        del provider
    return {
        "spec_content_key": spec.content_key,
        "chosen_edge": registration["chosen_edge"],
        "roots": {name: str(path) for name, path in roots.items()},
        "roots_present": sorted(caches),
        "per_build_key": per_key,
        "totals": {
            "daily_units": sum(item["daily_units"]
                               for item in per_key.values()),
            "hits": sum(item["hits"] for item in per_key.values()),
            "misses": sum(item["misses"] for item in per_key.values()),
            "build_keys_complete": sum(1 for item in per_key.values()
                                       if item["misses"] == 0),
            "build_keys_empty": sum(1 for item in per_key.values()
                                    if item["hits"] == 0),
        },
        "hits_by_root": hits_by_root,
        "rule": ("a hit is a stored record whose own identity matches the "
                 "identity asked for; anything else is a miss"),
    }


def _oracle_cost(canaries: Sequence[Path], missing: int) -> Dict[str, Any]:
    """What the missing units would cost, from measured canaries only."""
    measured = []
    for path in canaries:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        oracle = record["oracle"]
        measured.append({"path": str(path),
                         "content_key": record["content_key"],
                         "daily_units": oracle["daily_units"],
                         "wall_s": oracle["wall_s"],
                         "per_unit_s": oracle["wall_s"]
                         / oracle["daily_units"]})
    rates = [item["per_unit_s"] for item in measured]
    return {
        "kind": "modelled from measured per-unit oracle time",
        "measured": measured,
        "missing_units": missing,
        "per_unit_s": {"low": min(rates), "high": max(rates)},
        "missing_units_s": {"low": min(rates) * missing,
                            "high": max(rates) * missing},
        "assumptions": [
            "the measured oracle covered 195 units of 3 archives, about "
            "10 % of the month's route bytes; per-unit cost is assumed to "
            "hold for the other archives",
            "the two rates differ about twofold because the machine's "
            "performance state differed; a budget must assume the slower",
            "a month ledger run also writes the cache and does other work, "
            "so this is a lower bound on that run, not its duration",
        ],
    }


def _runner_preflight(registration_path: Path, profile: Path,
                      scratch: Path) -> Dict[str, Any]:
    """What the guarded runner says about the existing month profile."""
    from tools import guarded_wci_build as guard

    setup = guard.production_setup(SimpleNamespace(
        registration=registration_path, profile=profile,
        index_out=scratch / "index",
        build_evidence_out=scratch / "build-evidence.json",
        status_dir=scratch / "status", evidence_id="preflight-probe"))
    return {"problems": setup["problems"],
            "would_start": not setup["problems"],
            "limits": {key: getattr(setup["limits"], key) for key in
                       ("raw_soft_s", "raw_hard_s", "total_hard_s",
                        "memory_hard_bytes", "swap_growth_hard_bytes")},
            "expectations": {key: getattr(setup["expect"], key) for key in
                             ("build_keys", "daily_units", "variant_records",
                              "parents", "variant_files")},
            "argv": setup["argv"]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--cache-root", action="append", default=[],
                        metavar="NAME=PATH", required=True)
    parser.add_argument("--oracle-canary", type=Path, action="append",
                        required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")

    from tools import build_window_cost_index as builder
    from tools import freeze_wci_month_case as month

    registration = json.loads(args.registration.read_text(encoding="utf-8"))
    problems = month.verify_registration(registration)
    if problems:
        raise SystemExit("the month registration does not verify: "
                         f"{problems}")
    roots = dict(item.split("=", 1) for item in args.cache_root)
    sampler = common.Sampler()
    bound = builder._bound_inputs(args.profile)
    state = _cache_state(builder, registration, bound, roots)
    sampler.sample("cache_inspected")
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "claim_boundary": ("a read-only preflight; it builds nothing and "
                           "adopts nothing"),
        "driver": common.driver_binding(Path(__file__)),
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "registration": {"path": str(args.registration),
                         "sha256": common.file_sha256(args.registration),
                         "content_key": registration["content_key"],
                         "chosen_edge": registration["chosen_edge"],
                         "spec_content_key": registration[
                             "spec_content_key"],
                         "population": registration["population"]},
        "inspected_profile": {
            "path": str(args.profile),
            "sha256": common.file_sha256(args.profile),
            "bound_spec": json.loads(args.profile.read_text(
                encoding="utf-8"))["bound_spec"]},
        "cache": state,
        "oracle_cost_model": _oracle_cost(args.oracle_canary,
                                          state["totals"]["misses"]),
        "runner_preflight": _runner_preflight(
            args.registration, args.profile, args.out.parent / ".preflight"),
        "files_a_future_approved_build_would_create": {
            "prerequisite_month_ledger": [
                "runs/<id>/profile/profile.json (the baseline the index "
                "must beat)",
                "runs/<id>/profile-daily-cost-cache/: one bound oracle "
                "record per daily unit (1,950 for this month)",
                "runs/<id>/profile-daily-results/ and profile-releases/",
            ],
            "guarded_build": [
                "<index-root>/window-cost-index.json",
                "validation/<build evidence>.json "
                "(subhour_phase5_window_cost_index_evidence_v1)",
                "<status-dir>/status.json and child-telemetry.json",
                "validation/<runner evidence>.json (wci_guarded_run_v1)",
            ],
        },
        "memory": {"samples": sampler.samples},
    }
    record["output_sha256"] = common.digest({
        key: record[key] for key in ("cache", "oracle_cost_model",
                                     "runner_preflight")})
    published = common.publish(args.out, record)
    print(json.dumps({
        "chosen_edge": registration["chosen_edge"],
        "totals": state["totals"],
        "hits_by_root": state["hits_by_root"],
        "oracle_missing_units_s": record["oracle_cost_model"][
            "missing_units_s"],
        "runner_would_start": record["runner_preflight"]["would_start"],
        "runner_problems": record["runner_preflight"]["problems"],
        "content_key": published["content_key"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
