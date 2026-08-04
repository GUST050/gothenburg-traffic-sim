"""Freeze the source-corrected mesoscopic warm-state contract v13.

Process-free. Reads only tracked sources and the frozen v12 manifest; never
imports TraCI/libsumo, starts a process, opens a socket, or reads runs/outcomes.
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

OUT = "validation/monthly_warm_state_manifest_v13.json"
PARENT = "validation/monthly_warm_state_manifest_v12.json"
PARENT_CONTENT_KEY = "f8b03c614b8704eebb128e4f76cef67a0fc2bc871e870ae18fefb0ad08d71a0c"
CAMPAIGN = "v13"
FROZEN_AT = "2026-08-03"
VERSIONED_SUITE = "tests/test_monthly_warm_state_v13_freeze.py"

REQUIRED_REGRESSIONS = [
    "tests/test_sumo_runtime.py",
    "tests/test_monthly_sumo.py",
    "tests/test_warm_state_boundary.py",
    "tests/test_monthly_warm_state.py",
    "tests/test_warm_state_cache.py",
    "tests/test_monthly_warm_state_freeze.py",
    "tests/test_monthly_warm_state_v12_freeze.py",
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
    "tools/freeze_monthly_warm_state_v13.py",
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
    parent = json.loads((ROOT / PARENT).read_text(encoding="utf-8"))
    recomputed = canonical_key(parent)
    if recomputed != parent.get("content_key") \
            or recomputed != PARENT_CONTENT_KEY:
        raise SystemExit("v12 parent content key does not recompute exactly")
    return parent


def verify_inputs() -> None:
    missing = sorted(name for name in SOURCES if not (ROOT / name).is_file())
    if missing:
        raise SystemExit(f"bound v13 sources are missing: {missing}")
    tree = ast.parse((ROOT / VERSIONED_SUITE).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) \
                        and key.value.startswith("tests/") \
                        and key.value.endswith("_freeze.py"):
                    raise SystemExit(
                        f"v13 suite pins a mutable predecessor test: {key.value}")


def build_manifest() -> dict:
    from traffic_sim.simulation.monthly_warm_state import PREFIX_EVIDENCE_SCHEMA
    from traffic_sim.simulation.warm_state_boundary import (
        BOUNDARY_ACTIVE_SCHEMA, MESO_RECONCILIATION_SCHEMA,
        PREFIX_ACCUMULATOR_SCHEMA, TRIPINFO_PRECISION, WARM_OUTPUT_PRECISION)
    from traffic_sim.simulation.warm_state_cache import WARM_STATE_SCHEMA_VERSION

    verify_inputs()
    parent = load_parent()
    manifest = json.loads(json.dumps(parent))
    manifest.pop("content_key", None)
    manifest.pop("source_fingerprints", None)
    for obsolete in ("accounting", "hypothesis", "restore_correction",
                     "retention_contract", "v11_review"):
        manifest.pop(obsolete, None)
    manifest.update({
        "campaign_version": CAMPAIGN,
        "frozen_at": FROZEN_AT,
        "status": "frozen_unapproved_unexecuted",
        "prefix_evidence_schema": PREFIX_EVIDENCE_SCHEMA,
        "cache_schema_version": WARM_STATE_SCHEMA_VERSION,
        "regression_binding": {
            "rule": ("every source and regression interpreting the mesoscopic "
                     "prefix accumulator and paired verdict is fingerprinted"),
            "required": list(REQUIRED_REGRESSIONS),
            "enforced_at_freeze": True,
        },
    })
    manifest["cases"] = [dict(
        parent["cases"][0], case_id="warm-v13-paired-equivalence",
        rationale=("the exact executed v12 physical cases, schedules, variants "
                   "and seeds; only the source-established mesoscopic "
                   "accumulator transport and cache schema change"))]
    manifest["source_established_defect"] = {
        "mode": "meso",
        "tripinfo_field": "MSDevice_Tripinfo::myMesoTimeLoss",
        "tripinfo_output_rule": ("generateOutput uses myMesoTimeLoss when "
                                 "MSGlobals::gUseMesoSim"),
        "state_omission": ("MSDevice_Tripinfo::saveState/loadState serialize "
                           "other tripinfo fields but omit myMesoTimeLoss"),
        "traci_mismatch": ("MEVehicle::getTimeLoss returns getWaitingTime; it "
                           "is not the mesoscopic tripinfo accumulator"),
        "official_sources": [
            "https://raw.githubusercontent.com/eclipse-sumo/sumo/main/src/microsim/devices/MSDevice_Tripinfo.cpp",
            "https://raw.githubusercontent.com/eclipse-sumo/sumo/main/src/mesosim/MEVehicle.h",
            "https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html",
        ],
    }
    manifest["meso_accumulator_reconstruction"] = {
        "boundary_active_schema": BOUNDARY_ACTIVE_SCHEMA,
        "prefix_accumulator_schema": PREFIX_ACCUMULATOR_SCHEMA,
        "reconciliation_schema": MESO_RECONCILIATION_SCHEMA,
        "prefix_output": {"write_unfinished": True,
                          "precision": WARM_OUTPUT_PRECISION},
        "resumed_output": {"precision": WARM_OUTPUT_PRECISION},
        "production_tripinfo_precision": TRIPINFO_PRECISION,
        "active_population_digest_validation": (
            "recompute warm_boundary_active_v1 from the ledger warm point and "
            "sorted entry IDs on every read; an outer self-hash is insufficient"),
        "completed_record_order": (
            "persist SUMO prefix XML order separately from the canonical ID map "
            "and continue that Python accumulator across resumed XML order"),
        "rule": ("capture exact active IDs on the state-writing connection; "
                 "partition normally-finalized unfinished prefix tripinfo; "
                 "add each active vehicle's private prefix accumulator to its "
                 "resumed value; round each whole vehicle once; continue the "
                 "cold XML-order sum without segment regrouping; reject any "
                 "population overlap, omission, duplicate or malformed value"),
        "traci_time_loss_used": False,
        "tolerance": 0.0,
        "raw_sumo_outputs_rewritten": False,
    }
    manifest["resumed_execution_contract"] = {
        "simulation_step_calls": 1,
        "terminal_time_s": parent["retention_contract"]["terminal_time_s"],
        "warm_point_s": parent["retention_contract"]["warm_point_s"],
        "keep_after_arrival": False,
        "reason": ("v13 reads no terminal TraCI vehicle value; retaining every "
                   "arrived vehicle would add memory/runtime without evidence"),
    }
    manifest["hypothesis_under_test"] = {
        "claim": ("unfinished prefix tripinfo carries exactly the private meso "
                  "accumulator omitted by state load, so per-vehicle join and "
                  "single rounding reproduce cold production semantics"),
        "status": "UNPROVEN — v13 is unapproved and unexecuted",
        "refutation_condition": ("any semantic mismatch, incomplete warm "
                                 "coverage, malformed population, abnormal exit "
                                 "or failed performance gate rejects v13 and "
                                 "publishes no cache"),
    }
    manifest["v12_review"] = {
        "campaign": "v12",
        "content_key": PARENT_CONTENT_KEY,
        "disposition": "executed_refuted_no_cache",
        "residual_cold_minus_warm_s": [7.730000004, 80.620000002,
                                       138.970000003],
        "finding": ("TraCI save/restore ledgers were equal but final mesoscopic "
                    "tripinfo differed because they observed waiting time, not "
                    "the private accumulator SUMO omitted from saved state"),
        "cache_published": False,
        "bytes_preserved": True,
    }
    manifest["inherited_from"] = {
        "manifest": PARENT,
        "content_key": PARENT_CONTENT_KEY,
        "fields": ["route_safety", "archive_files_sha256",
                   "demand_requirement", "network_requirement",
                   "spec_template", "frozen_schedule_ids", "seeds",
                   "demand_variants", "comparison_policy"],
        "reason": ("physical cases are unchanged; v13 corrects only how the "
                   "lost mesoscopic accumulator crosses the state boundary"),
    }
    manifest["supersedes"] = {
        "campaign": "v12",
        "content_key": PARENT_CONTENT_KEY,
        "outcome": ("EXECUTED and REFUTED with complete warm coverage, exact "
                    "residuals and NO_CACHE_PUBLISHED"),
    }
    history = dict(parent["execution_history"])
    history["note"] = (
        "v12 executed all three identities and refuted its TraCI-deficit "
        "mechanism. v13 is frozen unapproved/unexecuted from official source "
        "semantics; warming remains OFF until a fresh paired campaign passes.")
    manifest["execution_history"] = history
    manifest["warming_default"] = (
        "OFF — v13 is unapproved/unexecuted; cache publication requires exact "
        "paired equivalence and the frozen performance gate")
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
