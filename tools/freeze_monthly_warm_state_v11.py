"""Freeze the exact selectively-reconciled warm-state contract v11.

Process-free: reads tracked sources and frozen validation manifests only. It
never imports TraCI/libsumo, opens a socket, starts a child process, or reads a
runs/outcome/cache path. v10 is preserved as rejected, unapproved and
unexecuted evidence; v11 inherits its physical case and changes only the
interpreter and measurement contract.
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
OUT = "validation/monthly_warm_state_manifest_v11.json"
PARENT = "validation/monthly_warm_state_manifest_v10.json"
PARENT_CONTENT_KEY = "796cc33e899e9e14038c467e3885635663b97160a15e21d76f8ebe7bb993c73c"
CAMPAIGN = "v11"
FROZEN_AT = "2026-08-02"
VERSIONED_SUITE = "tests/test_monthly_warm_state_v11_freeze.py"

REQUIRED_REGRESSIONS = [
    "tests/test_sumo_runtime.py",
    "tests/test_monthly_sumo.py",
    "tests/test_warm_state_boundary.py",
    "tests/test_monthly_warm_state.py",
    "tests/test_warm_state_cache.py",
    "tests/test_monthly_warm_state_freeze.py",
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
    "tools/freeze_monthly_warm_state_v11.py",
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
    recomputed = canonical_key(parent)
    if recomputed != parent.get("content_key") \
            or recomputed != PARENT_CONTENT_KEY:
        raise SystemExit("v10 parent content key does not recompute exactly")
    return parent


def verify_regression_binding() -> None:
    missing = sorted(set(REQUIRED_REGRESSIONS) - set(SOURCES))
    absent = sorted(name for name in SOURCES if not (ROOT / name).is_file())
    if missing:
        raise SystemExit(f"required regressions are not bound: {missing}")
    if absent:
        raise SystemExit(f"bound sources are missing: {absent}")


def verify_lifecycle_rules(suite: str = VERSIONED_SUITE) -> None:
    path = ROOT / suite
    if not path.is_file():
        raise SystemExit(f"versioned suite is missing: {suite}")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) \
                        and key.value.startswith("tests/") \
                        and key.value.endswith("_freeze.py"):
                    raise SystemExit(
                        f"versioned suite pins a predecessor test file: "
                        f"{key.value}")
        if isinstance(node, ast.Assert):
            rendered = ast.dump(node.test)
            if "DEFAULT_MANIFEST" in rendered and "MANIFEST" in rendered \
                    and "Eq()" in rendered:
                raise SystemExit(
                    "versioned suite asserts the harness default IS itself")


def build_manifest() -> dict:
    from traffic_sim.simulation.monthly_warm_state import PREFIX_EVIDENCE_SCHEMA
    from traffic_sim.simulation.warm_state_boundary import (
        FINAL_LEDGER_SCHEMA, RESTORE_AUDIT_SCHEMA,
        RESTORE_CORRECTION_SCHEMA, SAVE_LEDGER_SCHEMA, TRIPINFO_PRECISION)
    from traffic_sim.simulation.monthly_sumo import WARM_POST_FLUSH_S

    verify_regression_binding()
    verify_lifecycle_rules()
    parent = load_parent()
    manifest = json.loads(json.dumps(parent))
    manifest.pop("content_key", None)
    manifest.pop("source_fingerprints", None)
    manifest.update({
        "campaign_version": CAMPAIGN,
        "frozen_at": FROZEN_AT,
        "status": "frozen_unapproved_unexecuted",
        "prefix_evidence_schema": PREFIX_EVIDENCE_SCHEMA,
        "regression_binding": {
            "rule": ("every meaning-bearing source and regression for exact "
                     "selective reconciliation is fingerprinted"),
            "required": list(REQUIRED_REGRESSIONS),
            "enforced_at_freeze": True,
        },
    })
    manifest["cases"] = [dict(
        parent["cases"][0], case_id="warm-v11-paired-equivalence",
        rationale=("the exact v10 physical case, with v10 preserved and "
                   "rejected before execution; only the wired unrounded-final "
                   "reconciliation contract changes"))]
    terminal_time_s = 432000 + WARM_POST_FLUSH_S
    warm_point_s = manifest["cases"][0]["closure_bound_warm_point_s"]
    manifest["restore_correction"] = {
        "save_ledger_schema": SAVE_LEDGER_SCHEMA,
        "restore_audit_schema": RESTORE_AUDIT_SCHEMA,
        "final_ledger_schema": FINAL_LEDGER_SCHEMA,
        "correction_schema": RESTORE_CORRECTION_SCHEMA,
        "prefix_evidence_schema": PREFIX_EVIDENCE_SCHEMA,
        "rule": ("capture saved and restored unrounded accumulators at the "
                 "same warm instant; derive only positive measured deficits; "
                 "advance once to terminal time; for each deficit-bearing ID "
                 "round(final unrounded TraCI value + deficit) once at "
                 "production precision; leave every unaffected tripinfo "
                 "value unchanged"),
        "tripinfo_precision": TRIPINFO_PRECISION,
        "raw_outputs_rewritten": False,
        "nothing_is_inferred": True,
    }
    manifest["retention_contract"] = {
        "option": "--keep-after-arrival",
        "terminal_time_s": terminal_time_s,
        "warm_point_s": warm_point_s,
        "exact_retention_s": terminal_time_s - warm_point_s,
        "simulation_step_calls_after_restore": 1,
        "purpose": ("retain saved vehicles for unrounded terminal TraCI reads; "
                    "retention changes memory lifetime only, not movement or "
                    "raw output semantics"),
    }
    manifest["hypothesis_under_test"] = {
        "claim": ("the v9 residual is exactly closed by adding only each "
                  "measured restore deficit to that vehicle's unrounded final "
                  "accumulator before one production-precision rounding"),
        "status": "UNPROVEN — v11 has not been approved or executed",
        "refutation_condition": ("any semantic mismatch, missing retained ID, "
                                 "identity/time drift, abnormal exit or cleanup "
                                 "failure rejects the warm arm and publishes no "
                                 "cache"),
    }
    manifest["inherited_from"] = {
        "manifest": PARENT,
        "content_key": PARENT_CONTENT_KEY,
        "fields": ["route_safety", "archive_files_sha256",
                   "demand_requirement", "network_requirement",
                   "spec_template", "frozen_schedule_ids", "seeds"],
        "reason": ("unchanged physical case; no runs/archive/outcome access is "
                   "needed or permitted to succeed v10's interpreter"),
    }
    manifest["supersedes"] = {
        "campaign": "v10",
        "content_key": PARENT_CONTENT_KEY,
        "outcome": ("REJECTED, UNAPPROVED and UNEXECUTED: production never "
                    "called its resumed controller, cache hits lost the save "
                    "ledger, and its correction began from rounded tripinfo"),
    }
    history = dict(parent["execution_history"])
    history["note"] = (
        "v9 remains the only campaign in this family whose warm arm executed. "
        "v10 was rejected unapproved/unexecuted; v11 is frozen but unapproved "
        "and unexecuted. Warm equivalence remains unproven.")
    manifest["execution_history"] = history
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
                raise SystemExit(f"frozen artifact differs: {relative}")
    print(json.loads(artifacts[OUT])["content_key"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
