#!/usr/bin/env python3
"""Freeze or run the approval-gated campaign-scale warm residual diagnostic.

Import, help, freeze and verify are process-free.  Execution is deliberately
unavailable without the exact frozen content key and performs every immutable
input/root check before importing the installed simulator runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.simulation.warm_state_forensics import (  # noqa: E402
    CLASSIFICATIONS, ForensicObservationError, ForensicObserver,
    build_forensic_report, build_global_verdict, canonical_digest,
    identity_label, validate_forensic_report, validate_raw_payload)


CONTRACT_PATH = Path("validation/monthly_warm_state_residual_contract_v1.json")
OUTPUT_ROOT = Path("validation/monthly_warm_state_residual_v1_outcome")
V9_MANIFEST = Path("validation/monthly_warm_state_manifest_v9.json")
V9_CONTENT_KEY = "cad9c072a0ca6f90b11bd6342a603337eeaceacc457cd0016a16a3d9fa04e7b2"
V9_SHA256 = "556e6a6fd489b4b7d0527970bb7d2bfa713b313cf0d261c4c0c26bf892afa8a2"

DIAGNOSTIC_ID = "monthly_warm_state_residual_v1"
SCHEMA = "monthly_warm_state_residual_contract_v1"
STATUS = "frozen_unapproved_unexecuted"
FAILURE_SCHEMA = "monthly_warm_state_residual_failure_v1"
FROZEN_AT = "2026-08-01"

BOUND_SOURCES = (
    "traffic_sim/simulation/warm_state_forensics.py",
    "traffic_sim/simulation/monthly_sumo.py",
    "traffic_sim/simulation/monthly_warm_state.py",
    "traffic_sim/simulation/warm_state_boundary.py",
    "traffic_sim/simulation/warm_state_cache.py",
    "traffic_sim/simulation/metrics.py",
    "traffic_sim/simulation/runtime.py",
    "run_monthly_warm_state_validation.py",
    "tools/diagnose_monthly_warm_state_residual.py",
    "tests/test_warm_state_forensics.py",
    "tests/test_monthly_warm_state_residual.py",
    "tests/test_monthly_warm_state.py",
    "tests/test_monthly_sumo.py",
)

IDENTITIES = (
    {"schedule_id": "closure-8bcf7829ae545dffd8ce",
     "demand_variant": "q10", "seed": 1000, "warm_point_s": 24300},
    {"schedule_id": "closure-8bcf7829ae545dffd8ce",
     "demand_variant": "q50", "seed": 1001, "warm_point_s": 24300},
    {"schedule_id": "closure-8bcf7829ae545dffd8ce",
     "demand_variant": "q90", "seed": 1002, "warm_point_s": 24300},
)


class DiagnosticError(SystemExit):
    """The immutable diagnostic boundary was not satisfied."""


class ApprovalRequired(DiagnosticError):
    """Execution was requested without the exact frozen key."""


def sha256_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_key(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items()
            if key != "content_key"}
    return canonical_digest(body)


def _load_json(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DiagnosticError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise DiagnosticError(f"{path} must contain an object")
    return value


def _load_v9_physical_contract() -> dict[str, Any]:
    path = ROOT / V9_MANIFEST
    if path.is_symlink() or not path.is_file():
        raise DiagnosticError(f"v9 manifest must be a regular file: {path}")
    if sha256_file(path) != V9_SHA256:
        raise DiagnosticError("the preserved v9 manifest bytes drifted")
    manifest = _load_json(path)
    if canonical_key(manifest) != manifest.get("content_key") or \
            manifest.get("content_key") != V9_CONTENT_KEY:
        raise DiagnosticError("the preserved v9 manifest identity is invalid")
    return manifest


def source_fingerprints() -> dict[str, str]:
    missing = [name for name in BOUND_SOURCES if not (ROOT / name).is_file()]
    if missing:
        raise DiagnosticError(f"bound sources are missing: {missing}")
    return {name: sha256_file(ROOT / name) for name in sorted(BOUND_SOURCES)}


def build_contract() -> dict[str, Any]:
    v9 = _load_v9_physical_contract()
    route_safety = v9["route_safety"]
    expected_points = {
        item["demand_variant"]: item["warm_point_s"] for item in IDENTITIES}
    actual_points = {variant: record["safe_warm_point_s"]
                     for variant, record in route_safety.items()}
    if actual_points != expected_points:
        raise DiagnosticError(
            f"v9 warm points differ from the diagnostic: {actual_points}")
    if v9.get("frozen_schedule_ids") != [IDENTITIES[0]["schedule_id"]]:
        raise DiagnosticError("v9 frozen schedule differs from the diagnostic")
    if v9.get("demand_variants") != ["q10", "q50", "q90"]:
        raise DiagnosticError("v9 demand variants differ from the diagnostic")
    if v9.get("seeds") != [1000, 1001, 1002]:
        raise DiagnosticError("v9 seeds differ from the diagnostic")
    network = v9["network_requirement"]
    if sha256_file(ROOT / network["path"]) != network["sha256"]:
        raise DiagnosticError("the tracked network differs from v9")

    contract = {
        "schema": SCHEMA,
        "diagnostic_id": DIAGNOSTIC_ID,
        "status": STATUS,
        "frozen_at": FROZEN_AT,
        "question": (
            "at which exact per-vehicle layer does the deterministic v9 "
            "warm-minus-cold time-loss residual first appear?"),
        "v9_physical_case": {
            "manifest": str(V9_MANIFEST),
            "manifest_sha256": V9_SHA256,
            "manifest_content_key": V9_CONTENT_KEY,
            "case": v9["cases"][0],
            "spec_template": v9["spec_template"],
            "baseline_trip_duration_p99_s":
                v9["baseline_trip_duration_p99_s"],
            "demand_requirement": v9["demand_requirement"],
            "archive_files_sha256": v9["archive_files_sha256"],
            "network_requirement": network,
            "route_safety": route_safety,
            "state_settings": v9["state_settings"],
        },
        "identities": [dict(item) for item in IDENTITIES],
        "ordered_arms": ["cold_candidate", "completed_prefix", "resumed"],
        "classifier": {
            "schema": "monthly_warm_state_forensic_report_v1",
            "classifications": list(CLASSIFICATIONS),
            "priority": [
                "population_partition_error",
                "aggregate_reporting_inconsistency",
                "exact_agreement_or_field_drift",
            ],
            "reporting_precision": 2,
            "exemplar_limit": 12,
            "raw_rule": (
                "every report and the global verdict must recompute from "
                "preserved raw vehicle records; aggregates are never authority"),
        },
        "execution_contract": {
            "approval": "exact token equal to content_key",
            "invocations": 1,
            "rerun": False,
            "resume": False,
            "timeout_s_per_arm": 600,
            "output_root": str(OUTPUT_ROOT),
            "staging_suffix": ".partial",
            "preflight_order": [
                "approval token", "live source/network/v9 bytes",
                "root and staging absence", "SUMO/TraCI origin and API",
                "five exact archive hashes", "single execution"],
            "cache_publication": False,
        },
        "terminal_contract": {
            "success": (
                "contract, execution ledger, three raw payloads, three reports, "
                "global verdict and digest manifest"),
            "failure": (
                "contract, completed-arm ledger, original error and digest "
                "manifest; never a success-shaped partial root"),
            "commit": "verify in staging and rename the root last",
            "no_clobber": True,
        },
        "source_fingerprints": source_fingerprints(),
        "claim_scope": (
            "diagnostic localization only; not equivalence, performance, "
            "warming readiness, cache adoption or release evidence"),
    }
    contract["content_key"] = canonical_key(contract)
    return contract


def contract_text() -> str:
    return json.dumps(build_contract(), indent=2, sort_keys=True) + "\n"


def freeze(path: Path | str | None = None) -> str:
    target = Path(path) if path is not None else ROOT / CONTRACT_PATH
    if target.exists():
        raise DiagnosticError(f"refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contract_text(), encoding="utf-8")
    return _load_json(target)["content_key"]


def verify(path: Path | str | None = None) -> bool:
    target = Path(path) if path is not None else ROOT / CONTRACT_PATH
    if target.is_symlink() or not target.is_file():
        raise DiagnosticError(f"frozen contract is missing: {target}")
    stored = target.read_text(encoding="utf-8")
    if stored != contract_text():
        return False
    payload = _load_json(target)
    return canonical_key(payload) == payload.get("content_key")


def frozen_key() -> str:
    contract = _load_json(ROOT / CONTRACT_PATH)
    if canonical_key(contract) != contract.get("content_key"):
        raise DiagnosticError("frozen contract key does not recompute")
    return contract["content_key"]


def require_approval(token: str | None) -> str:
    expected = frozen_key()
    if token is None:
        raise ApprovalRequired(
            f"execution requires fresh approval for content key {expected} "
            f"and root {OUTPUT_ROOT}")
    if token != expected:
        raise ApprovalRequired("approval token does not match the frozen key")
    return expected


def verify_live_inputs() -> dict[str, Any]:
    contract_path = ROOT / CONTRACT_PATH
    contract = _load_json(contract_path)
    if contract_path.read_text(encoding="utf-8") != contract_text():
        raise DiagnosticError("frozen contract or a bound live source drifted")
    if canonical_key(contract) != contract.get("content_key"):
        raise DiagnosticError("frozen contract key does not recompute")
    return contract


def require_absent_paths(contract: Mapping[str, Any]) -> tuple[Path, Path]:
    root = ROOT / contract["execution_contract"]["output_root"]
    staging = root.with_name(root.name +
                             contract["execution_contract"]["staging_suffix"])
    if root.exists() or staging.exists():
        raise DiagnosticError(
            f"refusing existing root/staging path: {root}, {staging}")
    return root, staging


def runtime_preflight(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Lazy installed-runtime and exact archived-demand preflight."""
    from traffic_sim.simulation.runtime import (
        resolve_traci, sumo_home, validate_traci_api)

    home = Path(sumo_home())
    binary = home / "bin" / "sumo"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise DiagnosticError(f"SUMO executable is unavailable: {binary}")
    traci = resolve_traci(home)
    traci_report = validate_traci_api(traci)

    physical = contract["v9_physical_case"]
    archive = ROOT / physical["demand_requirement"]["archive_path"]
    expected = physical["archive_files_sha256"]
    for name, digest in expected.items():
        path = archive / name
        if path.is_symlink() or not path.is_file() or sha256_file(path) != digest:
            raise DiagnosticError(f"archived demand member mismatch: {name}")
    metadata = _load_json(archive / "demand_meta.json")
    if metadata.get("demand_build_key") != \
            physical["demand_requirement"]["demand_build_key"]:
        raise DiagnosticError("archived demand build key differs")
    if int(metadata.get("n_intervals", -1)) != \
            int(physical["demand_requirement"]["n_intervals"]):
        raise DiagnosticError("archived demand interval count differs")
    return {"sumo_home": str(home), "sumo_binary": str(binary),
            "traci": traci_report, "archive": str(archive)}


def _success_names(contract: Mapping[str, Any]) -> list[str]:
    names = ["contract.json", "execution_ledger.json", "verdict.json"]
    for identity in contract["identities"]:
        label = identity_label(identity)
        names.extend((f"raw_{label}.json", f"report_{label}.json"))
    return names


FAILURE_NAMES = ("contract.json", "completed_arms.json", "error.json")


def _publish(root: Path, members: Mapping[str, bytes],
             required: list[str] | tuple[str, ...], validator=None) \
        -> dict[str, str]:
    root = Path(root)
    staging = root.with_name(root.name + ".partial")
    if root.exists() or staging.exists():
        raise DiagnosticError("refusing to reuse root or staging path")
    required_set = set(required)
    if set(members) != required_set:
        raise DiagnosticError(
            f"terminal member set differs: {sorted(set(members))} != "
            f"{sorted(required_set)}")
    staging.mkdir(parents=True)
    try:
        digests = {}
        for name in sorted(members):
            payload = members[name]
            if not isinstance(payload, (bytes, bytearray)):
                raise DiagnosticError(f"member {name} is not bytes")
            (staging / name).write_bytes(bytes(payload))
            digests[name] = sha256_file(staging / name)
        manifest = json.dumps(digests, indent=2, sort_keys=True) + "\n"
        (staging / "members.json").write_bytes(manifest.encode("utf-8"))
        for name, digest in digests.items():
            if sha256_file(staging / name) != digest:
                raise DiagnosticError(f"staged digest changed: {name}")
        if {path.name for path in staging.iterdir()} != \
                required_set | {"members.json"}:
            raise DiagnosticError("staging contains an unexpected member")
        if validator is not None:
            validator(staging)
        staging.rename(root)
        return digests
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def _verify_member_manifest(root: Path, allowed: set[str]) -> dict[str, str]:
    present = {path.name for path in root.iterdir()}
    if present != allowed | {"members.json"}:
        raise DiagnosticError(
            f"terminal member allowlist differs: {sorted(present)}")
    digests = _load_json(root / "members.json")
    if set(digests) != allowed:
        raise DiagnosticError("digest manifest coverage differs")
    for name, digest in digests.items():
        if sha256_file(root / name) != digest:
            raise DiagnosticError(f"member digest mismatch: {name}")
    return digests


def validate_success_artifact(root: Path | str,
                              contract_content_key: str | None = None) -> dict:
    root = Path(root)
    if not root.is_dir():
        raise DiagnosticError(f"success artifact is absent: {root}")
    embedded = _load_json(root / "contract.json")
    expected_key = contract_content_key or frozen_key()
    if canonical_key(embedded) != embedded.get("content_key") or \
            embedded.get("content_key") != expected_key:
        raise DiagnosticError("embedded contract identity differs")
    names = set(_success_names(embedded))
    _verify_member_manifest(root, names)
    reports = []
    for identity in embedded["identities"]:
        label = identity_label(identity)
        raw = _load_json(root / f"raw_{label}.json")
        report = _load_json(root / f"report_{label}.json")
        validate_raw_payload(raw)
        reports.append(validate_forensic_report(report, raw))
    stored_verdict = _load_json(root / "verdict.json")
    rebuilt_verdict = build_global_verdict(reports, embedded["identities"])
    if stored_verdict != rebuilt_verdict:
        raise DiagnosticError("global verdict does not recompute")
    ledger = _load_json(root / "execution_ledger.json")
    if ledger.get("schema") != "monthly_warm_state_residual_execution_v1":
        raise DiagnosticError("execution ledger schema differs")
    if ledger.get("contract_content_key") != expected_key:
        raise DiagnosticError("execution ledger contract key differs")
    labels = [identity_label(item) for item in embedded["identities"]]
    if ledger.get("completed_identities") != labels:
        raise DiagnosticError("execution identity coverage differs")
    attempts = ledger.get("warm_attempts")
    if not isinstance(attempts, list) or len(attempts) != len(labels) or \
            any(item.get("outcome") != "warm_executed" for item in attempts):
        raise DiagnosticError("warm execution evidence is incomplete")
    return {"contract": embedded, "ledger": ledger,
            "reports": reports, "verdict": stored_verdict}


def validate_failure_artifact(root: Path | str,
                              contract_content_key: str | None = None) -> dict:
    root = Path(root)
    if not root.is_dir():
        raise DiagnosticError(f"failure artifact is absent: {root}")
    _verify_member_manifest(root, set(FAILURE_NAMES))
    embedded = _load_json(root / "contract.json")
    expected_key = contract_content_key or frozen_key()
    if canonical_key(embedded) != embedded.get("content_key") or \
            embedded.get("content_key") != expected_key:
        raise DiagnosticError("failure contract identity differs")
    error = _load_json(root / "error.json")
    if set(error) != {"schema", "diagnostic_id", "contract_content_key",
                      "error_type", "error_message"} or \
            error.get("schema") != FAILURE_SCHEMA or \
            error.get("diagnostic_id") != DIAGNOSTIC_ID or \
            error.get("contract_content_key") != expected_key or \
            not error.get("error_type") or not error.get("error_message"):
        raise DiagnosticError("failure error record is malformed")
    completed = _load_json(root / "completed_arms.json")
    if completed.get("schema") != "monthly_warm_state_completed_arms_v1" or \
            not isinstance(completed.get("entries"), list):
        raise DiagnosticError("completed-arm ledger is malformed")
    return {"contract": embedded, "error": error, "completed": completed}


def run_diagnostic(contract: Mapping[str, Any], root: Path,
                   *, runner_factory=None, schedule_factory=None) -> dict[str, Any]:
    """Run the exact production case once. Called only after full preflight."""
    from run_monthly_warm_state_validation import build_runner

    if schedule_factory is None:
        from traffic_sim.core.closure_calendar import generate_closure_schedules
        schedule_factory = generate_closure_schedules

    v9 = _load_v9_physical_contract()
    observer = ForensicObserver()
    factory = runner_factory or build_runner
    completed: list[str] = []
    try:
        with tempfile.TemporaryDirectory(
                prefix="monthly-warm-residual-") as scratch:
            workspace = Path(scratch)
            cold = factory(v9, warm=False, workspace=workspace / "cold",
                           forensic_observer=observer)
            warm = factory(v9, warm=True, workspace=workspace / "warm",
                           forensic_observer=observer)
            schedules = schedule_factory(cold.spec)[:1]
            if len(schedules) != 1 or schedules[0].schedule_id != \
                    contract["identities"][0]["schedule_id"]:
                raise DiagnosticError(
                    "generated schedule differs from the contract")
            schedule = schedules[0]
            for identity in contract["identities"]:
                variant, seed = identity["demand_variant"], identity["seed"]
                _cold_observation, _cold_failures, cold_canonical = \
                    cold._run_observation(schedule, variant=variant, seed=seed)
                if cold_canonical is None or \
                        cold_canonical.get("execution_arm") != "cold":
                    raise DiagnosticError(
                        f"cold arm failed for {variant}/{seed}")
                _warm_observation, _warm_failures, warm_canonical = \
                    warm._run_observation(schedule, variant=variant, seed=seed)
                if warm_canonical is None or \
                        warm_canonical.get("execution_arm") != "warm":
                    raise DiagnosticError(
                        f"warm arm failed for {variant}/{seed}")
                if warm_canonical.get("warm_point_s") != \
                        identity["warm_point_s"]:
                    raise DiagnosticError(
                        f"warm point differs for {variant}/{seed}")
                completed.append(identity_label(identity))

            raw_payloads = observer.raw_payloads(contract["identities"])
            reports = [build_forensic_report(raw) for raw in raw_payloads]
            verdict = build_global_verdict(reports, contract["identities"])
            ledger = {
                "schema": "monthly_warm_state_residual_execution_v1",
                "contract_content_key": contract["content_key"],
                "completed_identities": completed,
                "warm_attempts": list(warm.warm_attempts),
                "cache_published": False,
            }
            members: dict[str, bytes] = {
                "contract.json": _json_bytes(contract),
                "execution_ledger.json": _json_bytes(ledger),
                "verdict.json": _json_bytes(verdict),
            }
            for raw, report in zip(raw_payloads, reports):
                label = identity_label(raw["identity"])
                members[f"raw_{label}.json"] = _json_bytes(raw)
                members[f"report_{label}.json"] = _json_bytes(report)
            _publish(
                root, members, _success_names(contract),
                validator=lambda staged: validate_success_artifact(
                    staged, contract["content_key"]))
            return validate_success_artifact(root, contract["content_key"])
    except BaseException as error:
        try:
            error.completed_ledger = observer.completed_ledger()
        except Exception:
            pass
        raise


def execute(approval_token: str | None = None, *, runner_factory=None) -> dict:
    key = require_approval(approval_token)
    contract = verify_live_inputs()
    root, _staging = require_absent_paths(contract)
    runtime_preflight(contract)
    observer_ledger: list[dict[str, Any]] = []
    try:
        return run_diagnostic(contract, root, runner_factory=runner_factory)
    except BaseException as error:
        if root.exists():
            raise
        # A future injected runner may expose a ledger on the exception; never
        # replace the original error if it does not.
        if isinstance(getattr(error, "completed_ledger", None), list):
            observer_ledger = list(error.completed_ledger)
        members = {
            "contract.json": _json_bytes(contract),
            "completed_arms.json": _json_bytes({
                "schema": "monthly_warm_state_completed_arms_v1",
                "entries": observer_ledger}),
            "error.json": _json_bytes({
                "schema": FAILURE_SCHEMA,
                "diagnostic_id": DIAGNOSTIC_ID,
                "contract_content_key": key,
                "error_type": type(error).__name__,
                "error_message": str(error),
            }),
        }
        _publish(
            root, members, FAILURE_NAMES,
            validator=lambda staged: validate_failure_artifact(staged, key))
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--execute", metavar="APPROVAL_TOKEN")
    args = parser.parse_args(argv)
    chosen = [args.freeze, args.verify, args.execute is not None]
    if sum(bool(item) for item in chosen) != 1:
        raise DiagnosticError("pass exactly one of --freeze, --verify, --execute")
    if args.freeze:
        key = freeze()
        print(f"{CONTRACT_PATH}: {key}")
        print("status: frozen_unapproved_unexecuted")
        return 0
    if args.verify:
        ok = verify()
        print("reproduces byte-for-byte:", ok)
        return 0 if ok else 1
    try:
        result = execute(args.execute)
    except (DiagnosticError, ForensicObservationError) as error:
        print(f"diagnostic failed: {error}")
        return 1
    print("diagnostic evidence complete:",
          result["verdict"]["classification_counts"])
    print("output root:", OUTPUT_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
