#!/usr/bin/env python3
"""Bounded A/B/B/A of the WindowCostIndex raw phase: retain vs stream.

Arm A is the retain loop exactly as committed in ``f5608a7``, run from a
clean worktree of that commit. Arm B is the streaming candidate in this
checkout. Both arms price the frozen nonzero canary spec
(``wci_effect_canary_spec_20260917-v3``) for 1, 2 and 3 build keys, in the
order A, B, B, A for every key count. Each run is a fresh process with its
own output directory, reading the same archives, the same catalogs and the
read-only oracle cache that canary v4 wrote.

The driver verifies before anything runs:

* canary v4 and its spec, and that the spec's inventory evidence and the
  canary bind the exact ``f5608a7`` sources and have not drifted;
* that arm A is a clean ``f5608a7`` checkout;
* that the arms differ only in the declared candidate files and share every
  costing source.

It compares every decision-bearing digest, measures raw wall/user/sys,
peak RSS and physical footprint, counters and variant parses, and publishes
append-only diagnostic evidence (``release_evidence: false``). It starts no
SUMO process, builds no demand, writes no index and runs no ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _code_root() -> Path:
    """The checkout a worker imports production code from."""
    if "--code-root" in sys.argv:
        return Path(sys.argv[sys.argv.index("--code-root") + 1]).resolve()
    return ROOT


CODE_ROOT = _code_root()


def _prefer_code_root() -> None:
    """Put the arm's checkout first; helper imports push this checkout."""
    for entry in (str(HERE), str(CODE_ROOT)):
        while entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)


_prefer_code_root()

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_stream_ab_v2"
BASE_COMMIT = "f5608a7deed3d6a343400039f8a6059e11bcc607"
KEY_COUNTS = (1, 2, 3)
ORDER = ("A", "B", "B", "A")
MONTH_KEYS = 30
FOOTPRINT_LIMIT_BYTES = 12 * 1024 ** 3
#: Files the stream candidate may change; everything else must be identical.
CANDIDATE_FILES = (
    "tools/build_window_cost_index.py",
    "traffic_sim/simulation/window_cost_index.py",
)
PRODUCTION_SOURCES = (
    "run_scenario.py",
    "tools/build_window_cost_index.py",
    "tools/closure_effect_eligibility.py",
    "tools/cost_ordered_benchmark.py",
    "tools/profile_monthly_cost_ledger.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/ops/io_phases.py",
    "traffic_sim/simulation/closure_ranking.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/metadata.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/window_cost_index.py",
)
COSTING_SOURCES = (
    "run_scenario.py",
    "traffic_sim/simulation/closure_ranking.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/metadata.py",
)
COST_FIELDS = ("added_vehicle_hours", "added_metres_total",
               "vehicles_affected", "vehicles_no_detour")
DECISION_FIELDS = ("records_digest", "records_bytes_sha256", "oracle_digest",
                   "identities_digest", "unit_digests", "unit_costs_digest",
                   "affected_vehicles_total", "affected_daily_units",
                   "positive_costs", "daily_units")


# -- worker -------------------------------------------------------------------

def _raw_run(args, builder, spec, bound, units, phases):
    """The arm's `_raw_index_records`, counting every variant parse."""
    from wci_effect_canary_v3 import _run_raw_index
    from traffic_sim.simulation.deterministic_disruption import DailyCostCache

    parses: Dict[str, int] = {}
    production_parse = builder.parse_route_vehicles

    def counted(path, **kwargs):
        parses[str(path)] = parses.get(str(path), 0) + 1
        return production_parse(path, **kwargs)

    builder.parse_route_vehicles = counted
    cache = DailyCostCache(Path(args.oracle_root))
    try:
        result, counters = _run_raw_index(
            builder, bound, spec, units, cache, phases)
    finally:
        builder.parse_route_vehicles = production_parse
    return result, counters, cache, {
        "files": len(parses), "total": sum(parses.values()),
        "max_per_file": max(parses.values(), default=0)}


def _decisions(builder, spec, records, oracle, identities, units, cache,
               wall_s):
    from wci_effect_canary_v3 import _unit_checks

    costs, cost_mismatches, identity_mismatches = _unit_checks(
        records, oracle, identities, units, cache)
    comparison = builder.WindowCostIndex(
        bound_identity={"schema": SCHEMA, "spec": spec.content_key},
        records=records, preparation_time_s=wall_s,
    ).compare_oracle(oracle)
    return {
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
        "records_digest": common.digest(records),
        "records_bytes_sha256": hashlib.sha256(
            json.dumps(records).encode("utf-8")).hexdigest(),
        "oracle_digest": common.digest(oracle),
        "identities_digest": common.digest(identities),
        "unit_costs_digest": common.digest(costs),
        "unit_digests": {unit_id: {
            "indexed": common.digest(records[unit_id]),
            "oracle": common.digest(oracle[unit_id]),
            "provider_identity": common.digest(identities[unit_id]),
        } for unit_id in sorted(records)},
    }


def worker(args) -> None:
    from wci_effect_canary_v3 import _cpu, _population, _timed_phase
    _prefer_code_root()
    from tools import build_window_cost_index as builder
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation import deterministic_disruption as dd

    imported = {"builder": builder.__file__,
                "costing_root": str(dd._project_root())}
    if not all(Path(path).resolve().is_relative_to(CODE_ROOT)
               for path in imported.values()):
        raise SystemExit(f"worker imported code outside {CODE_ROOT}")
    spec_record = json.loads(Path(args.canary_spec).read_text("utf-8"))
    spec = ClosureSearchSpec.from_dict(spec_record["spec"])
    if spec.content_key != spec_record["spec_content_key"]:
        raise SystemExit("canary spec content key drifted")

    sampler = common.Sampler()
    phases: Dict[str, Any] = {}
    cpu, started = _cpu(), time.perf_counter()
    bound = builder._bound_inputs(Path(args.profile))
    _timed_phase(phases, "bound_inputs", cpu, started)
    cpu, started = _cpu(), time.perf_counter()
    units, _required = _population(
        builder, bound, spec, set(json.loads(args.build_keys)))
    _timed_phase(phases, "population_prepass", cpu, started)
    before = sampler.sample("before_raw")
    (records, oracle, raw), counters, cache, parses = _raw_run(
        args, builder, spec, bound, units, phases)
    after = sampler.sample("after_raw")
    Path(args.out_dir).mkdir(parents=True, exist_ok=False)
    result = {
        "arm_root": str(CODE_ROOT),
        "imported": imported,
        "raw_loop": raw.get("raw_loop", "retain_all_archives (f5608a7)"),
        "build_keys": json.loads(args.build_keys),
        "daily_units": len(records),
        "phases": phases,
        "production_timings": raw["timings"],
        "counters": counters,
        "variant_parses": parses,
        "memory": {
            "before_raw": before,
            "after_raw": after,
            "raw_footprint_growth_bytes": (
                after["lifetime_max_phys_footprint_bytes"]
                - before["phys_footprint_bytes"]),
        },
        **_decisions(builder, spec, records, oracle,
                     raw["provider_identities"], units, cache,
                     phases["raw_index_records"]["wall_s"]),
    }
    (Path(args.out_dir) / "worker.json").write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps(result))


# -- orchestration ------------------------------------------------------------

def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def _sha_at(commit: str, path: str) -> str:
    blob = subprocess.run(["git", "-C", str(ROOT), "show",
                           f"{commit}:{path}"], capture_output=True,
                          check=False)
    return (hashlib.sha256(blob.stdout).hexdigest()
            if blob.returncode == 0 else "")


def _verify_frozen_chain(canary_path: Path, spec_path: Path):
    """Canary v4 -> spec v3 -> inventory v2, all bound to f5608a7."""
    import tools.closure_effect_eligibility as cee
    from wci_current_code_canary_v1 import _verified

    canary = _verified(canary_path)
    spec_record = _verified(spec_path)
    if canary.get("status") != "PASS":
        raise SystemExit("the frozen canary did not pass")
    if canary["canary_spec"]["content_key"] != spec_record["content_key"]:
        raise SystemExit("canary and spec do not belong together")
    if cee.policy_of(spec_record) != cee.POLICY:
        raise SystemExit("spec was not selected by the effect policy")
    evidence = _verified(Path(spec_record["inventory_evidence"]["path"]))
    if evidence["content_key"] != spec_record["inventory_evidence"][
            "content_key"]:
        raise SystemExit("spec names different inventory evidence")
    bound = {**evidence["production_sources"], **canary["production_sources"]}
    unbound = sorted(name for name, sha in bound.items()
                     if _sha_at(BASE_COMMIT, name) != sha)
    if unbound:
        raise SystemExit(f"frozen chain is not bound to f5608a7: {unbound}")
    drift = cee.verify_inventory(evidence["inventory"])
    if drift:
        raise SystemExit(f"inventory evidence drifted: {drift}")
    return canary, spec_record, evidence


def _arm_sources(arm_a: Path) -> Dict[str, Any]:
    if _git(arm_a, "rev-parse", "HEAD").strip() != BASE_COMMIT:
        raise SystemExit(f"arm A is not {BASE_COMMIT}")
    if _git(arm_a, "status", "--porcelain", "--untracked-files=no").strip():
        raise SystemExit("arm A has tracked changes")
    arms = {name: {path: common.file_sha256(root / path)
                   for path in PRODUCTION_SOURCES}
            for name, root in (("A", arm_a), ("B", ROOT))}
    differing = sorted(path for path in PRODUCTION_SOURCES
                       if arms["A"][path] != arms["B"][path])
    if not set(differing) <= set(CANDIDATE_FILES):
        raise SystemExit(f"arms differ outside the candidate: {differing}")
    if any(arms["A"][path] != arms["B"][path] for path in COSTING_SOURCES):
        raise SystemExit("arms differ in a costing source")
    if any(arms["A"][path] != _sha_at(BASE_COMMIT, path)
           for path in PRODUCTION_SOURCES):
        raise SystemExit("arm A does not hold the f5608a7 sources")
    if (arm_a / "sumo").resolve() != (ROOT / "sumo").resolve():
        raise SystemExit("arm A does not read this checkout's network")
    return {"A": arms["A"], "B": arms["B"], "differing": differing,
            "arm_a_root": str(arm_a),
            "network_root": str((ROOT / "sumo").resolve())}


def _run(arm_root: Path, argv: List[str]) -> Dict[str, Any]:
    completed = subprocess.run(
        argv, capture_output=True, text=True, cwd=str(arm_root),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"worker failed ({completed.returncode}):\n"
                           f"{completed.stderr[-4000:]}")
    lines = [row for row in completed.stdout.splitlines()
             if row.startswith("{")]
    return json.loads(lines[-1])


def _summary(run: Mapping[str, Any]) -> Dict[str, Any]:
    raw = run["phases"]["raw_index_records"]
    after = run["memory"]["after_raw"]
    return {
        "raw_wall_s": raw["wall_s"], "raw_user_s": raw["user_s"],
        "raw_sys_s": raw["sys_s"],
        "max_rss_bytes": after["max_rss_bytes"],
        "lifetime_max_phys_footprint_bytes": (
            after["lifetime_max_phys_footprint_bytes"]),
        "raw_footprint_growth_bytes": (
            run["memory"]["raw_footprint_growth_bytes"]),
        "counters": run["counters"],
        "variant_parses": run["variant_parses"],
        "production_timings": run["production_timings"],
        "raw_loop": run["raw_loop"],
        **{field: run[field] for field in DECISION_FIELDS
           if field != "unit_digests"},
    }


def _fit(points: Sequence[tuple]) -> Dict[str, float]:
    xs = [point[0] for point in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(point[1] for point in points) / len(points)
    slope = (sum((x - mean_x) * (y - mean_y) for x, y in points)
             / sum((x - mean_x) ** 2 for x in xs))
    intercept = mean_y - slope * mean_x
    return {"intercept": intercept, "slope": slope,
            "at_30": intercept + slope * MONTH_KEYS}


def _checks(count: int, sequence, oracle_digests) -> Dict[str, bool]:
    first = sequence[0]["run"]
    runs = [item["run"] for item in sequence]
    return {
        "decisions_identical_across_all_four_runs": all(
            run[field] == first[field]
            for run in runs for field in DECISION_FIELDS),
        "oracle_matches_canary_v4": all(
            digests["oracle"] == oracle_digests.get(unit)
            for run in runs for unit, digests in run["unit_digests"].items()),
        "oracle_complete_and_identical": all(
            run["oracle_comparison"]["oracle_complete"]
            and run["oracle_comparison"]["field_identical"] for run in runs),
        "no_cost_or_identity_mismatch": all(
            not run["unit_cost_mismatches"]
            and not run["provider_identity_mismatches"] for run in runs),
        "nonzero": first["affected_vehicles_total"] > 0,
        "one_index_build": all(
            run["counters"].get("archive_index_build") == 1 for run in runs),
        "one_validation_per_key": all(
            run["counters"].get("archive_validate") == count
            for run in runs),
        "each_variant_parsed_once": all(
            run["variant_parses"]["max_per_file"] == 1
            and run["variant_parses"]["files"] == 3 * count
            for run in runs),
    }


def _analysis(runs: Dict[int, List[Dict[str, Any]]],
              oracle_digests: Mapping[str, str]) -> Dict[str, Any]:
    def series(arm, field):
        return {count: [item["summary"][field] for item in runs[count]
                        if item["arm"] == arm] for count in KEY_COUNTS}

    footprint = {arm: series(arm, "lifetime_max_phys_footprint_bytes")
                 for arm in "AB"}
    wall = {arm: series(arm, "raw_wall_s") for arm in "AB"}
    fits = {arm: _fit([(count, value) for count in KEY_COUNTS
                       for value in footprint[arm][count]])
            for arm in "AB"}
    growth = {arm: max(footprint[arm][3]) / min(footprint[arm][1])
              for arm in "AB"}
    reduction = {count: 1 - max(footprint["B"][count])
                 / min(footprint["A"][count]) for count in KEY_COUNTS}
    stream_high = (max(footprint["B"][3])
                   + max(0.0, fits["B"]["slope"]) * (MONTH_KEYS - 3))
    overlap_free = {count: (max(wall["B"][count]) < min(wall["A"][count])
                            or max(wall["A"][count]) < min(wall["B"][count]))
                    for count in KEY_COUNTS}
    ratio = {count: sum(wall["B"][count]) / sum(wall["A"][count])
             for count in KEY_COUNTS}
    checks = {str(count): _checks(count, runs[count], oracle_digests)
              for count in KEY_COUNTS}
    return {
        "checks": checks,
        "footprint_bytes": footprint,
        "max_rss_bytes": {arm: series(arm, "max_rss_bytes") for arm in "AB"},
        "raw_wall_s": wall,
        "raw_user_s": {arm: series(arm, "raw_user_s") for arm in "AB"},
        "raw_sys_s": {arm: series(arm, "raw_sys_s") for arm in "AB"},
        "footprint_reduction_vs_retain": reduction,
        "footprint_growth_1_to_3": growth,
        "footprint_fit": fits,
        "month_footprint_model": {
            "kind": "modelled",
            "retain_linear_at_30_bytes": fits["A"]["at_30"],
            "stream_linear_at_30_bytes": fits["B"]["at_30"],
            "stream_high_at_30_bytes": stream_high,
            "stream_high_basis": ("largest 3-key stream footprint plus the "
                                  "fitted per-key growth, floored at zero, "
                                  "for 27 more keys"),
            "limit_bytes": FOOTPRINT_LIMIT_BYTES,
            "margin_rule": "at most 75 % of the limit",
        },
        "time": {
            "raw_wall_ratio_b_over_a": ratio,
            "overlap_free_by_count": overlap_free,
            "time_win_proven": all(overlap_free[count] and ratio[count] < 1
                                   for count in KEY_COUNTS),
        },
        "gates": {
            "exact": all(all(group.values()) for group in checks.values()),
            # One key holds one archive in both loops by construction, so
            # the reduction is judged where retention differs (2 and 3
            # keys); at one key the stream may only be no worse.
            "memory_clearly_lower": (
                all(reduction[count] >= 0.25 for count in (2, 3))
                and reduction[1] >= -0.05),
            "memory_sublinear": growth["B"] < growth["A"],
            "month_model_under_limit_with_margin": (
                stream_high <= 0.75 * FOOTPRINT_LIMIT_BYTES),
            "no_clear_time_regression": all(
                not (overlap_free[count] and ratio[count] > 1.10)
                for count in KEY_COUNTS),
        },
    }


def _oracle_state(root: Path) -> Dict[str, Any]:
    files = sorted(root.rglob("*.json"))
    return {"files": len(files), "digest": common.digest(
        {str(path): common.file_sha256(path) for path in files})}


def _run_sequence(args, arm_a: Path, keys: List[str]):
    runs: Dict[int, List[Dict[str, Any]]] = {}
    for count in KEY_COUNTS:
        runs[count] = []
        for position, arm in enumerate(ORDER):
            root = arm_a if arm == "A" else ROOT
            out_dir = args.out_root / f"k{count}-{position}-{arm}"
            run = _run(root, [
                sys.executable, str(Path(__file__).resolve()), "--worker",
                "--code-root", str(root),
                "--profile", str(args.profile.resolve()),
                "--canary-spec", str(args.canary_spec.resolve()),
                "--oracle-root", str(args.oracle_root.resolve()),
                "--build-keys", json.dumps(keys[:count]),
                "--out-dir", str(out_dir.resolve())])
            runs[count].append({"arm": arm, "position": position,
                                "out_dir": str(out_dir), "run": run,
                                "summary": _summary(run)})
            print(json.dumps({
                "keys": count, "arm": arm,
                "raw_wall_s": round(
                    run["phases"]["raw_index_records"]["wall_s"], 2),
                "footprint_mb": round(run["memory"]["after_raw"][
                    "lifetime_max_phys_footprint_bytes"] / 1e6, 1),
                "records": run["records_digest"][:12]}), flush=True)
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--canary-spec", type=Path, required=True)
    parser.add_argument("--canary", type=Path)
    parser.add_argument("--oracle-root", type=Path, required=True)
    parser.add_argument("--arm-a-root", type=Path)
    parser.add_argument("--out-root", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--code-root", type=Path)
    parser.add_argument("--build-keys")
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return 0
    if args.out is None or args.out.exists():
        raise SystemExit(f"evidence path missing or exists: {args.out}")
    if args.out_root is None or args.out_root.exists():
        raise SystemExit(f"output root missing or exists: {args.out_root}")
    from wci_effect_canary_v3 import _runs_listing, _sumo_processes

    canary, spec_record, evidence = _verify_frozen_chain(
        args.canary, args.canary_spec)
    arm_a = args.arm_a_root.resolve()
    sources = _arm_sources(arm_a)
    keys = canary["runs"][-1]["build_keys"]
    runs_root = Path(canary["oracle"]["archives"][keys[0]]["archive"]).parent
    oracle_before = _oracle_state(args.oracle_root)
    sumo_before, runs_before = _sumo_processes(), _runs_listing(runs_root)
    runs = _run_sequence(args, arm_a, keys)
    sumo_after, runs_after = _sumo_processes(), _runs_listing(runs_root)
    analysis = _analysis(runs, canary["oracle"]["oracle_unit_digests"])
    boundary = {
        "no_sumo_process_started": sumo_after <= sumo_before,
        "no_demand_archive_created": runs_after == runs_before,
        "oracle_cache_unchanged": (
            _oracle_state(args.oracle_root) == oracle_before),
    }
    status = ("PASS" if all(analysis["gates"].values())
              and all(boundary.values()) else "FAIL")
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "status": status,
        "claim_boundary": ("bounded 1-3 build-key raw phase only; no full "
                           "WindowCostIndex build, time or adoption"),
        "gate_revision": ("v1 (wci_stream_ab_20260917-v1) required a 25 % "
                          "footprint reduction at one build key too, where "
                          "both loops hold one archive by construction; it "
                          "failed only that gate. v2 judges the reduction "
                          "at 2 and 3 keys and requires one key to be no "
                          "worse than 5 %."),
        "driver": common.driver_binding(Path(__file__)),
        "helpers": common.source_bindings((
            "validation/benchmarks/wci_current_code_canary_v1.py",
            "validation/benchmarks/wci_effect_canary_v3.py")),
        "arm_sources": sources,
        "base_commit": BASE_COMMIT,
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "frozen_inputs": {
            "canary": {"path": str(args.canary),
                       "content_key": canary["content_key"]},
            "canary_spec": {"path": str(args.canary_spec),
                            "content_key": spec_record["content_key"],
                            "spec_content_key": spec_record[
                                "spec_content_key"]},
            "inventory_evidence": {
                "path": spec_record["inventory_evidence"]["path"],
                "content_key": evidence["content_key"]},
            "profile": {"path": str(args.profile),
                        "sha256": common.file_sha256(args.profile)},
            "oracle_root": str(args.oracle_root),
            "oracle_cache": oracle_before,
        },
        "order": list(ORDER),
        "build_keys": keys,
        "runs": {str(count): [{key: item[key] for key in (
            "arm", "position", "out_dir", "summary")} for item in sequence]
            for count, sequence in runs.items()},
        "unit_digests": {str(count): runs[count][0]["run"]["unit_digests"]
                         for count in KEY_COUNTS},
        "analysis": analysis,
        "execution_boundary": boundary,
        "sumo_processes": {"before": sumo_before, "after": sumo_after},
    }
    record["output_sha256"] = common.digest({
        key: record[key] for key in ("status", "runs", "unit_digests",
                                     "analysis", "execution_boundary")})
    published = common.publish(args.out, record)
    print(json.dumps({"status": status, "gates": analysis["gates"],
                      "boundary": boundary,
                      "content_key": published["content_key"],
                      "output_sha256": published["output_sha256"]},
                     indent=1))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
