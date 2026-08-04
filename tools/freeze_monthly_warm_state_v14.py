"""Freeze warm-state v14 with a fail-first localhost-bind capability gate.

Process-free. Reads tracked sources and frozen v13 only; never opens a socket,
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

OUT = "validation/monthly_warm_state_manifest_v14.json"
PARENT = "validation/monthly_warm_state_manifest_v13.json"
PARENT_CONTENT_KEY = "0c8d42eb828c24e398acc3b642b4750c732addc2321db0e85935d015fe9eac77"
PARENT_TOOL_SHA256 = "9bfae44e3ff31825ef72640c8597f3477b3a4a025d13955865ed317d46325de0"
PARENT_MANIFEST_SHA256 = "589670db1c462930be7a862a2fe0cc4fad694af61bffb765c1ed1fc2a768f76d"
CAMPAIGN = "v14"
FROZEN_AT = "2026-08-03"
VERSIONED_SUITE = "tests/test_monthly_warm_state_v14_freeze.py"

REQUIRED_REGRESSIONS = [
    "tests/test_sumo_runtime.py",
    "tests/test_monthly_sumo.py",
    "tests/test_warm_state_boundary.py",
    "tests/test_monthly_warm_state.py",
    "tests/test_warm_state_cache.py",
    "tests/test_monthly_warm_state_freeze.py",
    "tests/test_monthly_warm_state_v12_freeze.py",
    "tests/test_monthly_warm_state_v13_freeze.py",
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
    "tools/freeze_monthly_warm_state_v14.py",
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
        raise SystemExit("v13 parent content key does not recompute exactly")
    if sha256_file(ROOT / "tools/freeze_monthly_warm_state_v13.py") \
            != PARENT_TOOL_SHA256 or sha256_file(path) != PARENT_MANIFEST_SHA256:
        raise SystemExit("frozen v13 tool/manifest bytes changed")
    return parent


def verify_inputs() -> None:
    missing = sorted(name for name in SOURCES if not (ROOT / name).is_file())
    if missing:
        raise SystemExit(f"bound v14 sources are missing: {missing}")
    tree = ast.parse((ROOT / VERSIONED_SUITE).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) \
                        and key.value.startswith("tests/") \
                        and key.value.endswith("_freeze.py"):
                    raise SystemExit(
                        f"v14 suite pins a mutable predecessor test: {key.value}")


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
            "rule": ("every source and regression interpreting the bind "
                     "preflight, mesoscopic reconstruction or paired verdict "
                     "is fingerprinted"),
            "required": list(REQUIRED_REGRESSIONS),
            "enforced_at_freeze": True,
        },
    })
    manifest["cases"] = [dict(
        parent["cases"][0],
        case_id="warm-v14-paired-equivalence",
        rationale=("the exact v13 physical case, schedules, variants, seeds "
                   "and semantic mechanism; only fail-first environment "
                   "capability and lifecycle bindings change"))]
    manifest["localhost_bind_preflight"] = {
        "required": True,
        "ordering": ["approval_token", "production_traci_origin_api",
                     "ipv4_tcp_loopback_bind", "keyed_root_absence",
                     "paired_campaign"],
        "family": "AF_INET",
        "socket_type": "SOCK_STREAM",
        "address": ["127.0.0.1", 0],
        "on_failure": ("fail before keyed-root inspection/creation and before "
                       "campaign execution"),
        "execution_requirement": ("the approved command must run in an "
                                  "environment permitting localhost TCP bind"),
    }
    manifest["v13_review"] = {
        "campaign": "v13",
        "content_key": PARENT_CONTENT_KEY,
        "disposition": "executed_environment_blocked_no_cache",
        "required_identities": 3,
        "semantic_mismatches": 0,
        "valid_warm_executions": 0,
        "permission_denied_cold_fallbacks": 3,
        "failure": "PermissionError: [Errno 1] Operation not permitted",
        "failure_location": "WarmPrefixController._free_port before prefix launch",
        "cache_published": False,
        "claim": ("environmental failure only; the fallback pairs prove "
                  "neither warm equivalence nor warm performance"),
        "bytes_preserved": True,
    }
    manifest["inherited_from"] = {
        "manifest": PARENT,
        "content_key": PARENT_CONTENT_KEY,
        "fields": ["route_safety", "archive_files_sha256",
                   "demand_requirement", "network_requirement",
                   "spec_template", "frozen_schedule_ids", "seeds",
                   "demand_variants", "comparison_policy",
                   "meso_accumulator_reconstruction",
                   "resumed_execution_contract", "warm_attempt_contract"],
        "reason": ("v14 preserves the complete v13 experiment and semantic "
                   "mechanism while preventing another environment-blocked "
                   "one-time root"),
    }
    manifest["supersedes"] = {
        "campaign": "v13",
        "content_key": PARENT_CONTENT_KEY,
        "outcome": ("EXECUTED but environment-blocked before warm prefix; "
                    "zero valid warm executions and NO_CACHE_PUBLISHED"),
    }
    hypothesis = dict(parent["hypothesis_under_test"])
    hypothesis["status"] = "UNPROVEN — v14 is unapproved and unexecuted"
    manifest["hypothesis_under_test"] = hypothesis
    history = dict(parent["execution_history"])
    history["note"] = (
        "v13 attempted all three identities but its environment denied the "
        "localhost bind before every warm prefix, so no warm evidence or cache "
        "exists. v14 adds a pre-root bind gate; warming remains OFF until a "
        "socket-capable approved paired campaign passes.")
    manifest["execution_history"] = history
    manifest["warming_default"] = (
        "OFF — v14 is unapproved/unexecuted; a socket-capable approved paired "
        "campaign must pass exact equivalence before cache publication")
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
