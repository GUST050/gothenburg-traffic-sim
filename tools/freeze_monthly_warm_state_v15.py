"""Freeze warm-state v15 with SUMO-native boundary transport.

Process-free. Reads tracked sources and frozen v14 only; never opens a socket,
imports TraCI/libsumo, starts a process, or reads runs/outcomes/caches.
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

OUT = "validation/monthly_warm_state_manifest_v15.json"
PARENT = "validation/monthly_warm_state_manifest_v14.json"
PARENT_CONTENT_KEY = "76ebb43577b9ef91a4e2c5b8504ee11bab960f560fa64f3fca4d0c1a27fd082c"
PARENT_TOOL_SHA256 = "414968d0e24efa9a9706cb6946d901a4b0405db44ff950cea53877de66c07707"
PARENT_MANIFEST_SHA256 = "d2236864b8e30a7e1d56c074871e2fda5fc879b79fa11dcf890732ab376dbe24"
CAMPAIGN = "v15"
FROZEN_AT = "2026-08-03"
VERSIONED_SUITE = "tests/test_monthly_warm_state_v15_freeze.py"

REQUIRED_REGRESSIONS = [
    "tests/test_sumo_runtime.py",
    "tests/test_monthly_sumo.py",
    "tests/test_warm_state_boundary.py",
    "tests/test_monthly_warm_state.py",
    "tests/test_warm_state_cache.py",
    "tests/test_monthly_warm_state_freeze.py",
    "tests/test_monthly_warm_state_v14_freeze.py",
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
    "tools/freeze_monthly_warm_state_v15.py",
    *REQUIRED_REGRESSIONS,
]


def canonical_key(payload) -> str:
    body = {key: value for key, value in payload.items()
            if key != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_parent() -> dict:
    path = ROOT / PARENT
    parent = json.loads(path.read_text(encoding="utf-8"))
    if canonical_key(parent) != parent.get("content_key") \
            or parent.get("content_key") != PARENT_CONTENT_KEY:
        raise SystemExit("v14 parent content key does not recompute exactly")
    if sha256_file(ROOT / "tools/freeze_monthly_warm_state_v14.py") \
            != PARENT_TOOL_SHA256 or sha256_file(path) != PARENT_MANIFEST_SHA256:
        raise SystemExit("frozen v14 tool/manifest bytes changed")
    return parent


def verify_inputs() -> None:
    missing = sorted(name for name in SOURCES if not (ROOT / name).is_file())
    if missing:
        raise SystemExit(f"bound v15 sources are missing: {missing}")
    tree = ast.parse((ROOT / VERSIONED_SUITE).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) \
                        and key.value.startswith("tests/") \
                        and key.value.endswith("_freeze.py"):
                    raise SystemExit(
                        f"v15 suite pins a mutable predecessor test: {key.value}")


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
            "rule": ("every source and regression interpreting boundary "
                     "transport, mesoscopic reconstruction or paired verdict "
                     "is fingerprinted"),
            "required": list(REQUIRED_REGRESSIONS),
            "enforced_at_freeze": True,
        },
    })
    manifest["cases"] = [dict(
        parent["cases"][0],
        case_id="warm-v15-paired-equivalence",
        rationale=("the exact v14 physical case, schedules, variants and "
                   "seeds; only the source-derived post-save boundary "
                   "transport and its evidence schemas change"))]
    manifest["meso_accumulator_reconstruction"] = {
        "active_population_digest_validation": (
            "recompute warm_boundary_active_v2 from warm point, native "
            "transport step and sorted entry IDs on every read"),
        "boundary_active_schema": "warm_boundary_active_v2",
        "completed_record_order": (
            "persist SUMO prefix XML order separately from the canonical ID "
            "map and continue that Python accumulator across resumed XML order"),
        "prefix_accumulator_schema": "warm_prefix_meso_accumulator_v2",
        "prefix_output": {"precision": 16, "write_unfinished": True},
        "production_tripinfo_precision": 2,
        "raw_sumo_outputs_rewritten": False,
        "reconciliation_schema": "warm_meso_reconciliation_v2",
        "resumed_output": {"precision": 16},
        "rule": ("capture exact active IDs and save state at the warm point; "
                 "advance only the prefix process by exactly one native SUMO "
                 "step so SUMO materializes boundary transitions in private "
                 "Tripinfo accumulators; partition by captured identity; join "
                 "each active prefix accumulator to its resumed value and "
                 "round each whole vehicle once; reject incomplete, late, "
                 "overlapping, duplicate or malformed evidence"),
        "tolerance": 0.0,
        "traci_time_loss_used": False,
        "transport_flush": {
            "duration": "exact traci.simulation.getDeltaT()",
            "saved_state_time": "unchanged exact warm point",
            "prefix_process_end": "warm point plus one native step",
            "post_boundary_records": "excluded unless captured active",
        },
    }
    manifest["v14_review"] = {
        "campaign": "v14",
        "content_key": PARENT_CONTENT_KEY,
        "disposition": "executed_semantic_mismatch_no_cache",
        "required_identities": 3,
        "valid_warm_executions": 3,
        "semantic_mismatches": 3,
        "only_mismatched_field": "candidate.metrics.total_time_loss_s",
        "cold_minus_warm_s": {"q10": 21.73, "q50": 23.10, "q90": 24.94},
        "cache_published": False,
        "claim": ("v14 proved the warm runtime path and all non-time-loss "
                  "semantics, but refuted exact time-loss equivalence"),
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
        "reason": ("v15 preserves v14's complete physical experiment and "
                   "safety gates while testing one source-derived boundary "
                   "transport change"),
    }
    manifest["supersedes"] = {
        "campaign": "v14",
        "content_key": PARENT_CONTENT_KEY,
        "outcome": ("EXECUTED with three exact time-loss mismatches and "
                    "NO_CACHE_PUBLISHED"),
    }
    manifest["hypothesis_under_test"] = {
        "claim": ("one native post-save prefix step lets SUMO materialize the "
                  "single private mesoscopic Tripinfo boundary contribution "
                  "omitted by save/load, restoring exact cold/warm equality"),
        "refutation_condition": ("any semantic mismatch, missing identity, "
                                 "abnormal exit or failed performance gate "
                                 "rejects v15 and publishes no cache"),
        "status": "UNPROVEN — v15 is unapproved and unexecuted",
    }
    manifest["execution_history"] = {
        "warm_executions_to_date": 6,
        "note": ("v14 completed three genuine warm executions. Their only "
                 "semantic mismatch was candidate time loss; v15 remains OFF "
                 "until a fresh paired campaign passes every exact gate."),
    }
    manifest["warming_default"] = (
        "OFF — v15 is unapproved/unexecuted; a fresh paired campaign must pass "
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
        for relative, text in artifacts.items():
            path = Path(temporary) / Path(relative).name
            path.write_text(text, encoding="utf-8")
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
