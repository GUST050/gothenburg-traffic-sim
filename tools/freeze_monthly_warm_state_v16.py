"""Freeze warm-state v16 with SUMO-compatible decimal normalization.

Process-free: reads tracked sources and frozen v15 only. It never imports a
simulator client, opens a socket, starts a process, or reads campaign roots.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = "validation/monthly_warm_state_manifest_v16.json"
PARENT = "validation/monthly_warm_state_manifest_v15.json"
PARENT_CONTENT_KEY = "b8af525102482bf29a4e64bf80d962ee6e6014c37f9d07ae04ee082112910700"
PARENT_TOOL_SHA256 = "9bdb13eb3a2855acdfe62bedb611b90cc37e965ca5cfda2ec289141b2ddb8e9c"
PARENT_MANIFEST_SHA256 = "e55c0c75f304f5b4be9d74ee569b0de4bb940dd3234b733bbf796e74ec65b238"
CAMPAIGN = "v16"
FROZEN_AT = "2026-08-03"
VERSIONED_SUITE = "tests/test_monthly_warm_state_v16_freeze.py"

REQUIRED_REGRESSIONS = [
    "tests/test_sumo_runtime.py",
    "tests/test_monthly_sumo.py",
    "tests/test_warm_state_boundary.py",
    "tests/test_monthly_warm_state.py",
    "tests/test_warm_state_cache.py",
    "tests/test_monthly_warm_state_freeze.py",
    "tests/test_monthly_warm_state_v15_freeze.py",
    VERSIONED_SUITE,
]
SOURCES = [
    "traffic_sim/simulation/monthly_warm_state.py",
    "traffic_sim/simulation/warm_state_cache.py",
    "traffic_sim/simulation/warm_state_boundary.py",
    "traffic_sim/simulation/monthly_sumo.py",
    "traffic_sim/simulation/envelope.py",
    "traffic_sim/simulation/metrics.py",
    "traffic_sim/simulation/monthly_search.py",
    "traffic_sim/simulation/finalist_decision.py",
    "traffic_sim/simulation/runtime.py",
    "traffic_sim/core/closure_calendar.py",
    "traffic_sim/core/contracts.py",
    "run_scenario.py",
    "suggest_closure_time.py",
    "run_monthly_warm_state_validation.py",
    "tools/freeze_monthly_warm_state_v16.py",
    *REQUIRED_REGRESSIONS,
]


def canonical_key(payload) -> str:
    body = {key: value for key, value in payload.items()
            if key != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_parent() -> dict:
    path = ROOT / PARENT
    parent = json.loads(path.read_text(encoding="utf-8"))
    if canonical_key(parent) != parent.get("content_key") or \
            parent.get("content_key") != PARENT_CONTENT_KEY:
        raise SystemExit("v15 parent content key does not recompute exactly")
    if sha256_file(ROOT / "tools/freeze_monthly_warm_state_v15.py") != \
            PARENT_TOOL_SHA256 or sha256_file(path) != PARENT_MANIFEST_SHA256:
        raise SystemExit("frozen v15 tool/manifest bytes changed")
    return parent


def verify_inputs() -> None:
    missing = sorted(name for name in SOURCES if not (ROOT / name).is_file())
    if missing:
        raise SystemExit(f"bound v16 sources are missing: {missing}")
    tree = ast.parse((ROOT / VERSIONED_SUITE).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) \
                        and key.value.startswith("tests/") \
                        and key.value.endswith("_freeze.py"):
                    raise SystemExit(
                        f"v16 suite pins a mutable predecessor test: {key.value}")


def build_manifest() -> dict:
    verify_inputs()
    parent = load_parent()
    manifest = json.loads(json.dumps(parent))
    manifest.pop("content_key", None)
    manifest.pop("source_fingerprints", None)
    manifest.update({
        "campaign_version": CAMPAIGN,
        "frozen_at": FROZEN_AT,
        "status": "frozen_unapproved_unexecuted",
        "regression_binding": {
            "rule": ("every source and regression interpreting SUMO decimal "
                     "normalization, mesoscopic reconstruction or the paired "
                     "verdict is fingerprinted"),
            "required": list(REQUIRED_REGRESSIONS),
            "enforced_at_freeze": True,
        },
    })
    manifest["cases"] = [dict(
        parent["cases"][0], case_id="warm-v16-paired-equivalence",
        rationale=("the exact v15 physical case, schedules, variants and "
                   "seeds; removes the refuted event lookahead and changes "
                   "only whole-vehicle decimal normalization"))]
    manifest["meso_accumulator_reconstruction"] = {
        "active_population_digest_validation": (
            "recompute warm_boundary_active_v3 from the exact warm point and "
            "sorted entry IDs on every read"),
        "boundary_active_schema": "warm_boundary_active_v3",
        "completed_record_order": (
            "persist SUMO prefix XML order separately from the canonical ID "
            "map and continue that accumulator across resumed XML order"),
        "normalization": {
            "input": "whole-vehicle high-precision SUMO timeLoss",
            "precision": 2,
            "rule": "Decimal(str(value)).quantize(0.01, ROUND_HALF_UP)",
            "python_builtin_round_used": False,
            "evidence": ("q10 v15 forensics found 2173 timeLoss-only values "
                         "each exactly 0.01 low; total -21.73 s"),
        },
        "prefix_accumulator_schema": "warm_prefix_meso_accumulator_v3",
        "prefix_output": {"precision": 16, "write_unfinished": True},
        "production_tripinfo_precision": 2,
        "raw_sumo_outputs_rewritten": False,
        "reconciliation_schema": "warm_meso_reconciliation_v2",
        "resumed_output": {"precision": 16},
        "rule": ("capture exact active IDs and save at the exact warm point; "
                 "partition immediate unfinished output by identity; join each "
                 "active prefix accumulator to its resumed value; normalize "
                 "every whole vehicle once with SUMO decimal half-up; reject "
                 "population overlap, omission, duplicate or malformed data"),
        "tolerance": 0.0,
        "traci_time_loss_used": False,
        "post_save_simulation_step": False,
    }
    manifest["v15_review"] = {
        "campaign": "v15",
        "content_key": PARENT_CONTENT_KEY,
        "disposition": "executed_semantic_mismatch_no_cache",
        "required_identities": 3,
        "valid_warm_executions": 3,
        "semantic_mismatches": 3,
        "only_mismatched_field": "candidate.metrics.total_time_loss_s",
        "cold_minus_warm_s": {"q10": 21.73, "q50": 22.01, "q90": 24.94},
        "cache_published": False,
        "lookahead_result": ("event-dependent: changed q50 by 1.09 s and "
                             "changed neither q10 nor q90; refuted"),
        "q10_forensics": {
            "population_partition_exact": True,
            "time_loss_mismatch_vehicle_count": 2173,
            "per_vehicle_delta_s": -0.01,
            "sum_delta_s": -21.73,
            "diagnostic_only": True,
        },
        "bytes_preserved": True,
    }
    manifest["inherited_from"] = {
        "manifest": PARENT,
        "content_key": PARENT_CONTENT_KEY,
        "fields": ["route_safety", "archive_files_sha256",
                   "demand_requirement", "network_requirement",
                   "spec_template", "frozen_schedule_ids", "seeds",
                   "demand_variants", "comparison_policy",
                   "resumed_execution_contract", "warm_attempt_contract",
                   "localhost_bind_preflight"],
        "reason": ("v16 preserves the full v15 physical experiment and "
                   "safety gates while correcting the localized formatter "
                   "semantic"),
    }
    manifest["supersedes"] = {
        "campaign": "v15", "content_key": PARENT_CONTENT_KEY,
        "outcome": ("EXECUTED with three exact time-loss mismatches and "
                    "NO_CACHE_PUBLISHED"),
    }
    manifest["hypothesis_under_test"] = {
        "claim": ("SUMO-compatible half-up normalization of each joined "
                  "whole-vehicle millisecond value eliminates the exact "
                  "one-cent-per-tie residual without changing traffic state"),
        "refutation_condition": ("any semantic mismatch, missing identity, "
                                 "abnormal exit or failed performance gate "
                                 "rejects v16 and publishes no cache"),
        "status": "UNPROVEN — v16 is unapproved and unexecuted",
    }
    manifest["execution_history"] = {
        "warm_executions_to_date": 9,
        "note": ("v15 completed three genuine warm executions and a bounded "
                 "q10 forensic pair localized its full 21.73-second residual "
                 "to 2173 one-cent rounding ties. v16 remains OFF until a "
                 "fresh paired campaign passes every exact gate."),
    }
    manifest["warming_default"] = (
        "OFF — v16 is unapproved/unexecuted; a fresh paired campaign must pass "
        "exact equivalence before cache publication or product activation")
    manifest["source_fingerprints"] = {
        name: sha256_file(ROOT / name) for name in sorted(set(SOURCES))}
    manifest["content_key"] = canonical_key(manifest)
    return manifest


def build_artifacts() -> dict[str, str]:
    return {OUT: json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n"}


def publish(artifacts, root=ROOT) -> None:
    root = Path(root)
    existing = [name for name in artifacts if (root / name).exists()]
    if existing:
        raise SystemExit(f"refusing to overwrite existing artifacts: {existing}")
    with tempfile.TemporaryDirectory() as temporary:
        staged = {}
        for relative, content in artifacts.items():
            path = Path(temporary) / Path(relative).name
            path.write_text(content, encoding="utf-8")
            staged[relative] = path
        created = []
        try:
            for relative, source in staged.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, target)
                created.append(target)
        except BaseException:
            for target in created:
                target.unlink(missing_ok=True)
            raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if args.write == args.verify:
        raise SystemExit("select exactly one of --write or --verify")
    artifacts = build_artifacts()
    if args.write:
        publish(artifacts)
    else:
        for relative, expected in artifacts.items():
            path = ROOT / relative
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                raise SystemExit(f"frozen artifact does not recompose: {relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
