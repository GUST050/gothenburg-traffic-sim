#!/usr/bin/env python3
"""Freeze or verify the process-free full-year population readiness record."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.plan_annual_warming import DEFAULT_OUTPUT, build_default_plan  # noqa: E402
from tools.populate_annual_warming import (  # noqa: E402
    DEFAULT_ROOT_PARENT,
    PLAN_FILENAME,
)
from tools.record_annual_warm_preflight import validate_report  # noqa: E402
from traffic_sim.core.fingerprint import sha256_file  # noqa: E402
from traffic_sim.simulation.annual_warm_plan import (  # noqa: E402
    load_annual_warm_ledger,
    load_annual_warm_plan,
)
from traffic_sim.simulation.annual_warm_population import (  # noqa: E402
    population_summary,
    validate_unit_artifact,
)
from traffic_sim.simulation.annual_warm_store import (  # noqa: E402
    AnnualWarmArtifactStore,
)


OUTPUT = Path("validation/annual_warm_readiness_v1.json")
STORAGE_PILOT = Path("validation/annual_warm_storage_pilot_v1.json")
PREFLIGHT = Path("validation/annual_warm_preflight_v1.json")
POPULATION_PILOT_ROOT = Path("validation/annual_warm_population_pilot_v1")
LEDGER_FILENAME = "ledger.json"  # Historical v1 root only.
SOURCE_PATHS = (
    "traffic_sim/simulation/annual_warm_plan.py",
    "traffic_sim/simulation/annual_warm_store.py",
    "traffic_sim/simulation/annual_warm_population.py",
    "tools/plan_annual_warming.py",
    "tools/populate_annual_warming.py",
    "tools/pilot_annual_warm_storage.py",
    "tools/record_annual_warm_preflight.py",
    "tools/freeze_annual_warm_readiness.py",
    "tests/test_annual_warm_plan.py",
    "tests/test_annual_warm_store.py",
    "tests/test_annual_warm_population.py",
    "tests/test_populate_annual_warming.py",
    "tests/test_annual_warm_readiness.py",
)


def _key(payload: Mapping[str, Any]) -> str:
    body = {name: value for name, value in payload.items() if name != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def _load_keyed(path: Path, schema: str) -> dict[str, Any]:
    payload = json.loads((ROOT / path).read_text(encoding="utf-8"))
    if payload.get("schema") != schema or payload.get("content_key") != _key(payload):
        raise ValueError(f"keyed readiness input differs: {path}")
    return payload


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _population_record(
    root: Path,
    plan: Mapping[str, Any],
    *,
    expected_succeeded: int,
) -> dict[str, Any]:
    stored_plan = load_annual_warm_plan(root / PLAN_FILENAME)
    if stored_plan != plan:
        raise ValueError(f"population root plan differs: {root}")
    ledger = load_annual_warm_ledger(root / LEDGER_FILENAME, plan)
    summary = population_summary(ledger, plan)
    if summary["succeeded"] != expected_succeeded \
            or summary["failed"] != 0 or summary["running"] != 0:
        raise ValueError(f"population root state differs: {root}")
    store = AnnualWarmArtifactStore(root / "store")
    artifacts = []
    for unit in ledger["work_units"]:
        if unit["state"] != "succeeded":
            continue
        artifact = store.load_artifact(unit["unit_id"])
        validate_unit_artifact(plan, unit["unit_id"], artifact)
        if artifact["content_key"] != unit["artifact_content_key"]:
            raise ValueError("population ledger artifact key differs")
        artifacts.append({
            "unit_id": unit["unit_id"],
            "artifact_content_key": artifact["content_key"],
            "first_work_date": artifact["metadata"]["slot"]["first_work_date"],
            "checkpoint_s": artifact["metadata"]["checkpoint_s"],
            "variant": artifact["metadata"]["variant"],
            "seed": artifact["metadata"]["seed"],
        })
    physical_bytes = sum(
        path.stat().st_size
        for path in (root / "store" / "blobs").rglob("*")
        if path.is_file()
    )
    return {
        "root": root.relative_to(ROOT).as_posix(),
        "plan_sha256": sha256_file(root / PLAN_FILENAME),
        "ledger_sha256": sha256_file(root / LEDGER_FILENAME),
        "summary": summary,
        "artifacts": sorted(artifacts, key=lambda item: item["unit_id"]),
        "physical_blob_bytes": physical_bytes,
    }


def build_readiness_manifest() -> dict[str, Any]:
    plan_path = ROOT / DEFAULT_OUTPUT
    plan = load_annual_warm_plan(plan_path)
    if plan != build_default_plan():
        raise ValueError("tracked annual plan differs from current sources")
    storage = _load_keyed(STORAGE_PILOT, "annual_warm_storage_pilot_v1")
    if storage.get("status") != "pass" \
            or storage.get("plan_content_key") != plan["content_key"] \
            or storage.get("restore_digest_mismatches") != 0:
        raise ValueError("storage pilot does not pass for the current plan")
    preflight = validate_report(
        json.loads((ROOT / PREFLIGHT).read_text(encoding="utf-8")), plan
    )
    pilot = _population_record(
        ROOT / POPULATION_PILOT_ROOT, plan, expected_succeeded=3
    )
    exact_pilot_set = {
        (item["first_work_date"], item["checkpoint_s"],
         item["variant"], item["seed"])
        for item in pilot["artifacts"]
    }
    if exact_pilot_set != {
        ("2027-07-15", 24300, "q10", 1000),
        ("2027-07-15", 24300, "q50", 1001),
        ("2027-07-15", 24300, "q90", 1002),
    }:
        raise ValueError("population pilot identity set differs")
    production_root = ROOT / DEFAULT_ROOT_PARENT / plan["content_key"]
    initialized = _population_record(
        production_root, plan, expected_succeeded=0
    )
    if initialized["summary"]["pending"] != plan["coverage"]["state_requests"]:
        raise ValueError("initialized production root is not wholly pending")

    source_fingerprints = {
        relative: sha256_file(ROOT / relative) for relative in SOURCE_PATHS
    }
    if any(value is None for value in source_fingerprints.values()):
        raise ValueError("annual readiness source file is missing")
    key = plan["content_key"]
    manifest: dict[str, Any] = {
        "schema": "annual_warm_readiness_v1",
        "status": "ready_for_full_population",
        "recorded_date": "2026-08-03",
        "plan": {
            "path": DEFAULT_OUTPUT.as_posix(),
            "content_key": key,
            "sha256": sha256_file(plan_path),
            "coverage": plan["coverage"],
            "seed_variants": plan["seed_variants"],
        },
        "execution_boundary": {
            "population_only": True,
            "product_activation": False,
            "release_or_deployment": False,
            "full_sumo_remains_accuracy_authority": True,
            "annual_artifacts_are_candidate_free_and_uncertified_for_product_reuse": True,
            "two_dst_transition_dates_excluded": ["2027-03-28", "2027-10-31"],
        },
        "preflight": {
            "path": PREFLIGHT.as_posix(),
            "content_key": preflight["content_key"],
            "approved_state_workers": preflight["approved_state_workers"],
            "network_sha256": preflight["network_sha256"],
            "sumo_version_first_line": preflight["sumo_version"].splitlines()[0],
            "traci_origin": preflight["traci"]["origin"],
            "localhost_ipv4_tcp_bind": preflight["localhost_ipv4_tcp_bind"],
            "disk_gate_passed": preflight["disk_gate_passed"],
        },
        "storage_pilot": {
            "path": STORAGE_PILOT.as_posix(),
            "content_key": storage["content_key"],
            "artifact_count": storage["artifact_count"],
            "unique_original_bytes": storage["unique_original_bytes"],
            "physical_stored_bytes": storage["physical_stored_bytes"],
            "physical_ratio": storage["physical_ratio"],
        },
        "population_pilot": pilot,
        "initialized_population": initialized,
        "commands": {
            "status": (
                "python3 tools/populate_annual_warming.py --status"
            ),
            "preflight": (
                "python3 tools/populate_annual_warming.py --preflight "
                "--state-workers 3"
            ),
            "execute_full": (
                "python3 tools/populate_annual_warming.py --execute "
                f"--plan-key {key} --state-workers 3"
            ),
            "resume_after_interruption": (
                "rerun execute_full unchanged; succeeded units are skipped, "
                "running/failed units retry with incremented attempts"
            ),
        },
        "source_fingerprints": source_fingerprints,
        "focused_check": (
            "python3 -m pytest -q tests/test_annual_warm_plan.py "
            "tests/test_annual_warm_store.py "
            "tests/test_annual_warm_population.py "
            "tests/test_populate_annual_warming.py "
            "tests/test_annual_warm_readiness.py"
        ),
    }
    manifest["content_key"] = _key(manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    expected = build_readiness_manifest()
    if args.write:
        _atomic(output, expected)
    else:
        stored = json.loads(output.read_text(encoding="utf-8"))
        if stored != expected or stored.get("content_key") != _key(stored):
            raise SystemExit("annual warm readiness manifest differs")
    print(json.dumps({
        "content_key": expected["content_key"],
        "plan_content_key": expected["plan"]["content_key"],
        "status": expected["status"],
        "pilot_succeeded": expected["population_pilot"]["summary"]["succeeded"],
        "population_pending": expected["initialized_population"]["summary"]["pending"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
