#!/usr/bin/env python3
"""Decision record for the month's independent daily-cost cache.

Reads the month registration, the cache preflight, the measured one-build-key
canary and the WindowCostIndex decision, verifies every content key, and
writes down what a serial cache build would cost, what it would create, how
it restarts and what would have to hold before it may start. It computes no
cost, starts no build and proposes no parallelism.

Append-only and diagnostic (``release_evidence: false``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_month_cache_decision_v1"
DECISION = "DO_NOT_START_MONTH_CACHE_BUILD"
GIB = 1024 ** 3
PRODUCTION_SOURCES = (
    "tools/build_daily_cost_cache.py",
    "tools/freeze_wci_month_case.py",
    "tools/guarded_wci_build.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
)
#: What the oracle path is and costs per build key, pinned by tests.
ORACLE_PATH = {
    "producer": ("tools/build_daily_cost_cache.py calls the production "
                 "ArchiveDisruptionProvider.disruption with a DailyCostCache; "
                 "the same call wrote canary v4's oracle cache from "
                 "validation/benchmarks/wci_effect_canary_v3.py"),
    "identity_inputs": [
        "schema, interday policy, closure type, directed edges, source, "
        "timezone and DST policy of the search spec",
        "the archive: resolved path, demand_meta SHA-256, epoch, duration "
        "and the q10/q50/q90 SHA-256",
        "the network: resolved path and SHA-256",
        "the five costing sources (run_scenario.py, disruption.py, "
        "deterministic_disruption.py, closure_ranking.py, metadata.py)",
        "the daily unit's own schedule, as its canonical dictionary",
    ],
    "independence": ("every daily unit is computed on its own: closure "
                     "windows, then run_scenario.closure_disruption per "
                     "variant, straight from the archive's route XML. No "
                     "WindowCostIndex record is read, so the result stays an "
                     "independent oracle for that index"),
    "shared_within_a_build_key": [
        "the ArchiveInputs hashes, computed once when the provider opens the "
        "archive",
        "the NetworkCostModel, built once per run",
        "nothing else: the provider's memo is keyed by schedule id, so two "
        "daily units never share a computation",
    ],
    "per_build_key_counts": {
        "providers_constructed": 1,
        "archive_files_hashed": 4,
        "resolver_calls": "one per daily unit, once per run when grouping",
        "cost_computations": 65,
        "route_parses": 195,
        "same_variant_reread": ("yes: each of the three variants is parsed "
                                "once per daily unit, 65 times per build key"),
    },
    "verification_before_publication": [
        "every stored record is reloaded through a fresh DailyCostCache and "
        "compared with what was computed",
        "the batch marker is published atomically and no-clobber only after "
        "that comparison",
        "a build key counts as complete only when its marker, its identity "
        "and every unit digest verify again",
    ],
    "pinned_by_tests": [
        "tests/test_daily_cost_cache.py::TestTheOraclePath::"
        "test_each_unit_reparses_every_variant",
        "tests/test_daily_cost_cache.py::TestTheOraclePath::"
        "test_the_provider_is_built_once_per_build_key",
        "tests/test_daily_cost_cache.py::TestOneCompleteBatch::"
        "test_no_window_cost_index_is_read",
    ],
}
RESTART_SEMANTICS = [
    "a build key is a batch: nothing is reusable until its marker is "
    "published",
    "an interrupted batch leaves content-addressed unit records and no "
    "marker, so the build key is a miss and is recomputed; the units that "
    "already exist are reused inside that recompute",
    "the marker binds the registration content key, the policy, the chosen "
    "edge, the spec, the archive and its four file hashes, both catalogs and "
    "the costing and builder sources; any change makes the batch a miss",
    "one flock per build key, so two processes cannot publish the same batch",
    "no global cache and no trust in a path, a size or an mtime",
]


def _load(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """A published record, refused unless its content key describes it."""
    from tools.closure_effect_eligibility import _digest as policy_digest

    record = json.loads(Path(path).read_text(encoding="utf-8"))
    body = {key: value for key, value in record.items()
            if key != "content_key"}
    if record.get("content_key") not in (common.digest(body),
                                         policy_digest(body)):
        raise SystemExit(f"{path}: content key does not describe it")
    return record, {"path": str(path), "sha256": common.file_sha256(path),
                    "content_key": record.get("content_key")}


def _disk(cache_root: Path, units_in_batch: int,
          month_units: int) -> Dict[str, Any]:
    files = list(Path(cache_root).rglob("*.json"))
    total = sum(path.stat().st_size for path in files)
    per_unit = total / units_in_batch if units_in_batch else 0
    return {"measured_batch": {"files": len(files), "bytes": total,
                               "bytes_per_daily_unit": per_unit},
            "month_estimate_bytes": per_unit * month_units,
            "basis": "the measured diagnostic batch, scaled by daily units"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--daily-cache-canary", type=Path, required=True)
    parser.add_argument("--wci-decision", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")

    registration, registration_ref = _load(args.registration)
    preflight, preflight_ref = _load(args.preflight)
    canary, canary_ref = _load(args.daily_cache_canary)
    wci_decision, wci_ref = _load(args.wci_decision)
    if canary.get("status") != "PASS":
        raise SystemExit("the measured build key did not pass")

    totals = preflight["cache"]["totals"]
    estimate = canary["remaining_estimate"]
    batch = canary["batch"]
    units_per_key = batch["daily_units"]
    missing_keys = canary["build_key_choice"]["missing_build_keys"]
    scale = (missing_keys * units_per_key) / estimate["remaining_daily_units"]
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "decision": DECISION,
        "claim_boundary": ("a decision record; it computes no cost, starts "
                           "no build and proposes no parallelism"),
        "driver": common.driver_binding(Path(__file__)),
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "inputs": {"registration": registration_ref,
                   "preflight": preflight_ref,
                   "daily_cache_canary": canary_ref,
                   "wci_full_build_decision": wci_ref},
        "case": {"chosen_edge": registration["chosen_edge"],
                 "spec_content_key": registration["spec_content_key"],
                 "policy": registration["case_selection_policy"],
                 "population": registration["population"]},
        "verified_coverage": {
            "daily_units": totals["daily_units"],
            "cached": totals["hits"],
            "missing": totals["misses"],
            "build_keys_complete": totals["build_keys_complete"],
            "build_keys_empty": totals["build_keys_empty"],
            "source_of_the_hits": preflight["cache"]["hits_by_root"],
            "note": ("the hits are canary v4's oracle root; the month "
                     "profile's own cache holds none of this edge"),
        },
        "diagnostic_batch": {
            "separate_from_production_coverage": True,
            "cache_root": canary["cache_root"],
            "build_key": canary["build_key_choice"]["chosen"],
            "selection_rule": canary["build_key_choice"]["rule"],
            "daily_units": units_per_key,
            "batch_content_key": batch["content_key"],
            "wall_s": canary["build"]["wall_s"],
            "per_unit_s": estimate["this_batch_per_unit_s"],
            "fresh_independent_check": canary["fresh_independent_check"],
            "reuse_pass": canary["reuse_pass"],
            "checks": canary["checks"],
        },
        "oracle_path": ORACLE_PATH,
        "serial_estimates": {
            "all_missing_build_keys": {
                "build_keys": missing_keys,
                "daily_units": missing_keys * units_per_key,
                "seconds": {key: value * scale for key, value
                            in estimate["serial_estimate_s"].items()},
            },
            "after_this_verified_batch": {
                "build_keys": estimate["remaining_build_keys"],
                "daily_units": estimate["remaining_daily_units"],
                "seconds": estimate["serial_estimate_s"],
            },
            "rates_per_unit_s": estimate["per_unit_s"],
            "rate_sources": estimate["rates"],
            "caveats": estimate["caveats"],
        },
        "resources": {
            "measured_batch_peak": batch["peak"],
            "disk": _disk(args.cache_root, units_per_key,
                          registration["population"]["daily_units"]),
            "swap_growth_observed_bytes": canary["build"]["status"]["swap"][
                "growth_bytes"],
        },
        "restart_semantics": RESTART_SEMANTICS,
        "future_command": {
            "one_build_key": [
                "python3", "tools/build_daily_cost_cache.py",
                "--registration", str(args.registration),
                "--profile",
                "runs/step5_code_approved_20260915b/profile/profile.json",
                "--cache-root", "<fresh cache root>",
                "--build-key", "<build key>",
                "--status", "<status dir>/build.json",
            ],
            "serial_month": ("the same command with one --build-key per "
                             "missing key, in one process, one key at a "
                             "time; a resume repeats it and reuses every "
                             "verified batch"),
            "not_proposed": ("parallelism, and any write to the production "
                             "cache root, which the tool refuses without "
                             "--allow-production-cache"),
        },
        "recommended_budget": {
            "per_build_key_wall_s": {
                "expected": canary["build"]["wall_s"],
                "hard": round(3 * canary["build"]["wall_s"]),
            },
            "serial_month_wall_s": {
                "expected": estimate["serial_estimate_s"]["median"],
                "hard": round(2 * estimate["serial_estimate_s"]["high"]),
            },
            "memory_hard_bytes": 4 * GIB,
            "swap_growth_hard_bytes": 1 * GIB,
            "stop_conditions": [
                "any batch whose stored units do not match what was computed",
                "registration, archive, catalog, costing or builder drift",
                "a SUMO process or a new demand archive",
                "memory or swap past the limits above",
                "a build key that takes more than its hard wall time",
            ],
        },
        "still_missing_before_a_guarded_full_wci_build": [
            "a complete, verified daily-cost cache for the chosen edge",
            "a month ledger profile bound to that case, which is the "
            "baseline the index must beat",
            "then the guarded build under the budget in "
            f"{Path(wci_ref['path']).name}",
        ],
        "wci_decision": {"decision": wci_decision.get("decision"),
                         "content_key": wci_decision.get("content_key")},
    }
    record["output_sha256"] = common.digest({
        key: record[key] for key in (
            "decision", "verified_coverage", "diagnostic_batch",
            "serial_estimates", "resources", "recommended_budget")})
    published = common.publish(args.out, record)
    print(json.dumps({
        "decision": DECISION,
        "verified_coverage": record["verified_coverage"],
        "diagnostic_batch_wall_s": record["diagnostic_batch"]["wall_s"],
        "serial_estimates": record["serial_estimates"],
        "disk_month_bytes": record["resources"]["disk"][
            "month_estimate_bytes"],
        "content_key": published["content_key"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
