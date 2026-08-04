#!/usr/bin/env python3
"""Freeze or run the boundary-aware campaign-scale residual diagnostic v2."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import diagnose_monthly_warm_state_residual as v1  # noqa: E402
from traffic_sim.simulation.warm_state_forensics import (  # noqa: E402
    CLASSIFICATIONS, PHASES, BoundaryAwareForensicObserver,
    ForensicObservationError, REPORT_SCHEMA_V2, build_forensic_report_v2,
    build_global_verdict_v2,
    canonical_digest, identity_label_v2, validate_forensic_report_v2,
    validate_raw_payload_v2)


CONTRACT_PATH = Path("validation/monthly_warm_state_residual_contract_v2.json")
OUTPUT_ROOT = Path("validation/monthly_warm_state_residual_v2_outcome")
DIAGNOSTIC_ID = "monthly_warm_state_residual_v2"
SCHEMA = "monthly_warm_state_residual_contract_v2"
STATUS = "frozen_unapproved_unexecuted"
FAILURE_SCHEMA = "monthly_warm_state_residual_failure_v2"
FROZEN_AT = "2026-08-01"

V1_CONTENT_KEY = "7a14758130f3dcbfd09f437bbf50c860992f7611dbbb9ad0362c0ecf82583831"
V1_FILES = {
    "tools/diagnose_monthly_warm_state_residual.py":
        "a982020883d9976ca752cf76b84fb1f56561e9a0c37690632c87c9f1d8305f31",
    "tests/test_warm_state_forensics.py":
        "0a41d2b6b330d89f87128e1c9959d514da78fb6078ecc2514b69e209661c649c",
    "tests/test_monthly_warm_state_residual.py":
        "c9a722c24444839f99a22acf45fc0eaaf430a8da004e4b9363af43e0c2d382db",
    "validation/monthly_warm_state_residual_contract_v1.json":
        "2f554ed73e654c82586025706b920fbf053f1415bd3e796e9523c6063b1cf007",
}
V9_FILES = {
    "tools/freeze_monthly_warm_state_v9.py":
        "23bfb8c0118bb1580f7c128411fa6e2471e6262086bbd9e5cb02758ff291ab4b",
    "tests/test_monthly_warm_state_v9_freeze.py":
        "997a93dd822d35eb8749263003f17a4f6eee809835c854822c53a1a2740fc813",
    "validation/monthly_warm_state_manifest_v9.json": v1.V9_SHA256,
}

BOUND_SOURCES = (
    "traffic_sim/simulation/warm_state_forensics.py",
    "traffic_sim/simulation/monthly_sumo.py",
    "traffic_sim/simulation/monthly_warm_state.py",
    "traffic_sim/simulation/warm_state_boundary.py",
    "traffic_sim/simulation/warm_state_cache.py",
    "traffic_sim/simulation/metrics.py",
    "traffic_sim/simulation/runtime.py",
    "run_monthly_warm_state_validation.py",
    "tools/diagnose_monthly_warm_state_residual_v2.py",
    "tests/test_warm_state_forensics_v2.py",
    "tests/test_monthly_warm_state_residual_v2.py",
    "tests/test_monthly_warm_state.py",
    "tests/test_monthly_sumo.py",
)

IDENTITIES = tuple(dict(item) for item in v1.IDENTITIES)
FAILURE_NAMES = ("contract.json", "completed_arms.json", "error.json")

DiagnosticError = v1.DiagnosticError
ApprovalRequired = v1.ApprovalRequired


def sha256_file(path: Path | str) -> str:
    return v1.sha256_file(path)


def canonical_key(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items()
            if key != "content_key"}
    return canonical_digest(body)


def _load_json(path: Path | str) -> dict[str, Any]:
    return v1._load_json(path)


def _verify_preserved(mapping: Mapping[str, str], label: str) -> None:
    for name, expected in mapping.items():
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or \
                sha256_file(path) != expected:
            raise DiagnosticError(f"preserved {label} file drifted: {name}")


def source_fingerprints() -> dict[str, str]:
    output = {}
    for name in sorted(BOUND_SOURCES):
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise DiagnosticError(f"bound v2 source is not a regular file: {name}")
        output[name] = sha256_file(path)
    return output


def build_contract() -> dict[str, Any]:
    _verify_preserved(V1_FILES, "v1")
    _verify_preserved(V9_FILES, "v9")
    parent = _load_json(ROOT / v1.CONTRACT_PATH)
    if canonical_key(parent) != parent.get("content_key") or \
            parent.get("content_key") != V1_CONTENT_KEY:
        raise DiagnosticError("preserved v1 contract identity is invalid")
    v9 = v1._load_v9_physical_contract()
    expected_points = {
        item["demand_variant"]: item["warm_point_s"] for item in IDENTITIES}
    actual_points = {variant: item["safe_warm_point_s"]
                     for variant, item in v9["route_safety"].items()}
    if actual_points != expected_points or \
            v9.get("frozen_schedule_ids") != [IDENTITIES[0]["schedule_id"]] or \
            v9.get("demand_variants") != ["q10", "q50", "q90"] or \
            v9.get("seeds") != [1000, 1001, 1002]:
        raise DiagnosticError("v9 physical identities differ from v2")
    network = v9["network_requirement"]
    network_path = ROOT / network["path"]
    if network_path.is_symlink() or not network_path.is_file() or \
            sha256_file(network_path) != network["sha256"]:
        raise DiagnosticError("tracked network differs from v9")

    contract = {
        "schema": SCHEMA,
        "diagnostic_id": DIAGNOSTIC_ID,
        "status": STATUS,
        "frozen_at": FROZEN_AT,
        "question": (
            "at which exact boundary-aware per-vehicle layer does the "
            "deterministic v9 warm-minus-cold residual first appear?"),
        "supersedes": {
            "content_key": V1_CONTENT_KEY,
            "preserved_files_sha256": dict(V1_FILES),
            "reason": (
                "v1 omitted warm_point_s from raw identity and could classify "
                "swapped prefix/resumed membership as exact agreement"),
        },
        "v9_physical_case": {
            "manifest": str(v1.V9_MANIFEST),
            "manifest_sha256": v1.V9_SHA256,
            "manifest_content_key": v1.V9_CONTENT_KEY,
            "preserved_files_sha256": dict(V9_FILES),
            "case": v9["cases"][0],
            "spec_template": v9["spec_template"],
            "baseline_trip_duration_p99_s":
                v9["baseline_trip_duration_p99_s"],
            "demand_requirement": v9["demand_requirement"],
            "archive_files_sha256": v9["archive_files_sha256"],
            "network_requirement": network,
            "route_safety": v9["route_safety"],
            "state_settings": v9["state_settings"],
        },
        "identities": [dict(item) for item in IDENTITIES],
        "ordered_arms": ["cold_candidate", "completed_prefix", "resumed"],
        "classifier": {
            "schema": REPORT_SCHEMA_V2,
            "classifications": list(CLASSIFICATIONS),
            "priority": [
                "population_partition_error",
                "aggregate_reporting_inconsistency",
                "exact_agreement_or_field_drift",
            ],
            "boundary_rule": (
                "cold arrival <= warm_point_s is completed_prefix; "
                "cold arrival > warm_point_s is resumed"),
            "explicit_aggregate_deltas": [
                "split_minus_cold_time_loss_s",
                "warm_minus_cold_time_loss_s",
            ],
            "reporting_precision": 2,
            "exemplar_limit": 12,
            "raw_rule": (
                "reports and verdict recompute from raw vehicle records; "
                "aggregate claims are never authority"),
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
                "approval token", "live source/network/v1/v9 bytes",
                "root and staging absence", "SUMO/TraCI origin and API",
                "five exact archive hashes", "single execution",
            ],
            "cache_publication": False,
        },
        "terminal_contract": {
            "success": (
                "contract, execution ledger, three v2 raw payloads, three v2 "
                "reports, global verdict and digest manifest"),
            "failure": (
                "contract, completed-arm ledger, original error and digest "
                "manifest; never a success-shaped partial root"),
            "member_type": "regular non-symlink files only",
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
    if target.exists() or target.is_symlink():
        raise DiagnosticError(f"refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contract_text(), encoding="utf-8")
    return _load_json(target)["content_key"]


def verify(path: Path | str | None = None) -> bool:
    target = Path(path) if path is not None else ROOT / CONTRACT_PATH
    if target.is_symlink() or not target.is_file():
        raise DiagnosticError(f"frozen v2 contract is missing: {target}")
    stored = target.read_text(encoding="utf-8")
    if stored != contract_text():
        return False
    payload = _load_json(target)
    return canonical_key(payload) == payload.get("content_key")


def frozen_key() -> str:
    contract = _load_json(ROOT / CONTRACT_PATH)
    if canonical_key(contract) != contract.get("content_key"):
        raise DiagnosticError("frozen v2 contract key does not recompute")
    return contract["content_key"]


def require_approval(token: str | None) -> str:
    expected = frozen_key()
    if token is None:
        raise ApprovalRequired(
            f"execution requires fresh approval for content key {expected} "
            f"and root {OUTPUT_ROOT}")
    if token != expected:
        raise ApprovalRequired("approval token does not match the frozen v2 key")
    return expected


def verify_live_inputs() -> dict[str, Any]:
    target = ROOT / CONTRACT_PATH
    contract = _load_json(target)
    if target.read_text(encoding="utf-8") != contract_text():
        raise DiagnosticError("frozen v2 contract or a bound source drifted")
    if canonical_key(contract) != contract.get("content_key"):
        raise DiagnosticError("frozen v2 contract key does not recompute")
    return contract


def require_absent_paths(contract: Mapping[str, Any]) -> tuple[Path, Path]:
    return v1.require_absent_paths(contract)


def runtime_preflight(contract: Mapping[str, Any]) -> dict[str, Any]:
    return v1.runtime_preflight(contract)


def _success_names(contract: Mapping[str, Any]) -> list[str]:
    names = ["contract.json", "execution_ledger.json", "verdict.json"]
    for identity in contract["identities"]:
        label = identity_label_v2(identity)
        names.extend((f"raw_{label}.json", f"report_{label}.json"))
    return names


def _json_bytes(value: Any) -> bytes:
    return v1._json_bytes(value)


def _regular_member_names(root: Path, allowed: set[str]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise DiagnosticError(f"terminal artifact is not a real directory: {root}")
    entries = list(root.iterdir())
    names = {path.name for path in entries}
    expected = allowed | {"members.json"}
    if names != expected:
        raise DiagnosticError(f"terminal member allowlist differs: {sorted(names)}")
    for path in entries:
        if path.is_symlink() or not path.is_file():
            raise DiagnosticError(f"terminal member is not a regular file: {path.name}")


def _verify_member_manifest(root: Path, allowed: set[str]) -> dict[str, str]:
    _regular_member_names(root, allowed)
    digests = _load_json(root / "members.json")
    if set(digests) != allowed:
        raise DiagnosticError("v2 digest manifest coverage differs")
    for name, digest in digests.items():
        if not isinstance(digest, str) or len(digest) != 64 or \
                sha256_file(root / name) != digest:
            raise DiagnosticError(f"v2 member digest mismatch: {name}")
    return digests


def _publish(root: Path, members: Mapping[str, bytes], required,
             validator=None) -> dict[str, str]:
    return v1._publish(root, members, required, validator=validator)


def validate_success_artifact(root: Path | str,
                              contract_content_key: str | None = None) -> dict:
    root = Path(root)
    contract_path = root / "contract.json"
    if root.is_symlink() or not root.is_dir() or contract_path.is_symlink() or \
            not contract_path.is_file():
        raise DiagnosticError("v2 success root/contract is not regular")
    embedded = _load_json(contract_path)
    expected_key = contract_content_key or frozen_key()
    if canonical_key(embedded) != embedded.get("content_key") or \
            embedded.get("content_key") != expected_key:
        raise DiagnosticError("embedded v2 contract identity differs")
    names = set(_success_names(embedded))
    _verify_member_manifest(root, names)
    reports = []
    for identity in embedded["identities"]:
        label = identity_label_v2(identity)
        raw = _load_json(root / f"raw_{label}.json")
        report = _load_json(root / f"report_{label}.json")
        validate_raw_payload_v2(raw)
        reports.append(validate_forensic_report_v2(report, raw))
    stored_verdict = _load_json(root / "verdict.json")
    rebuilt = build_global_verdict_v2(reports, embedded["identities"])
    if stored_verdict != rebuilt:
        raise DiagnosticError("v2 global verdict does not recompute")
    ledger = _load_json(root / "execution_ledger.json")
    if set(ledger) != {"schema", "contract_content_key",
                       "completed_identities", "warm_attempts",
                       "cache_published"} or \
            ledger.get("schema") != "monthly_warm_state_residual_execution_v2" or \
            ledger.get("contract_content_key") != expected_key or \
            ledger.get("cache_published") is not False:
        raise DiagnosticError("v2 execution ledger schema differs")
    labels = [identity_label_v2(item) for item in embedded["identities"]]
    if ledger.get("completed_identities") != labels:
        raise DiagnosticError("v2 execution identity coverage differs")
    attempts = ledger.get("warm_attempts")
    if not isinstance(attempts, list) or len(attempts) != len(labels) or \
            any(item.get("outcome") != "warm_executed" for item in attempts):
        raise DiagnosticError("v2 warm execution evidence is incomplete")
    attempt_labels = []
    for item in attempts:
        if not isinstance(item, Mapping) or \
                not isinstance(item.get("schedule_id"), str) or \
                not isinstance(item.get("demand_variant"), str) or \
                isinstance(item.get("seed"), bool) or \
                not isinstance(item.get("seed"), int):
            raise DiagnosticError("v2 warm attempt identity is malformed")
        attempt_labels.append(
            f"{item['schedule_id']}__{item['demand_variant']}__{item['seed']}")
    if attempt_labels != labels:
        raise DiagnosticError("v2 warm attempt identity coverage differs")
    return {"contract": embedded, "ledger": ledger,
            "reports": reports, "verdict": stored_verdict}


def validate_failure_artifact(root: Path | str,
                              contract_content_key: str | None = None) -> dict:
    root = Path(root)
    _verify_member_manifest(root, set(FAILURE_NAMES))
    embedded = _load_json(root / "contract.json")
    expected_key = contract_content_key or frozen_key()
    if canonical_key(embedded) != embedded.get("content_key") or \
            embedded.get("content_key") != expected_key:
        raise DiagnosticError("failure v2 contract identity differs")
    error = _load_json(root / "error.json")
    if set(error) != {"schema", "diagnostic_id", "contract_content_key",
                      "error_type", "error_message"} or \
            error.get("schema") != FAILURE_SCHEMA or \
            error.get("diagnostic_id") != DIAGNOSTIC_ID or \
            error.get("contract_content_key") != expected_key or \
            not error.get("error_type") or not error.get("error_message"):
        raise DiagnosticError("v2 failure error record is malformed")
    completed = _load_json(root / "completed_arms.json")
    if set(completed) != {"schema", "entries"} or \
            completed.get("schema") != "monthly_warm_state_completed_arms_v2" or \
            not isinstance(completed.get("entries"), list):
        raise DiagnosticError("v2 completed-arm ledger is malformed")
    expected = {identity_label_v2(item): item
                for item in embedded.get("identities", [])}
    observed = []
    for entry in completed["entries"]:
        if not isinstance(entry, Mapping) or set(entry) != {
                "identity", "captured_phases", "captured_summaries",
                "record_counts"}:
            raise DiagnosticError("v2 completed-arm entry is malformed")
        label = identity_label_v2(entry["identity"])
        if label not in expected or entry["identity"] != expected[label]:
            raise DiagnosticError("v2 completed-arm identity differs")
        phases = entry["captured_phases"]
        summaries = entry["captured_summaries"]
        counts = entry["record_counts"]
        if not isinstance(phases, list) or phases != sorted(set(phases)) or \
                not set(phases) <= set(PHASES) or \
                not isinstance(summaries, list) or \
                summaries != sorted(set(summaries)) or \
                not set(summaries) <= set(PHASES) | {"warm"} or \
                not isinstance(counts, Mapping) or set(counts) != set(phases) or \
                any(isinstance(value, bool) or not isinstance(value, int) or
                    value < 0 for value in counts.values()):
            raise DiagnosticError("v2 completed-arm capture evidence differs")
        observed.append(label)
    if len(observed) != len(set(observed)):
        raise DiagnosticError("v2 completed-arm identities are duplicated")
    return {"contract": embedded, "error": error, "completed": completed}


def run_diagnostic(contract: Mapping[str, Any], root: Path,
                   *, runner_factory=None, schedule_factory=None) -> dict[str, Any]:
    """Run the exact production case once, only after future full preflight."""
    from run_monthly_warm_state_validation import build_runner

    if schedule_factory is None:
        from traffic_sim.core.closure_calendar import generate_closure_schedules
        schedule_factory = generate_closure_schedules
    v9 = v1._load_v9_physical_contract()
    observer = BoundaryAwareForensicObserver(contract["identities"])
    factory = runner_factory or build_runner
    completed = []
    try:
        with tempfile.TemporaryDirectory(
                prefix="monthly-warm-residual-v2-") as scratch:
            workspace = Path(scratch)
            cold = factory(v9, warm=False, workspace=workspace / "cold",
                           forensic_observer=observer)
            warm = factory(v9, warm=True, workspace=workspace / "warm",
                           forensic_observer=observer)
            schedules = schedule_factory(cold.spec)[:1]
            if len(schedules) != 1 or schedules[0].schedule_id != \
                    contract["identities"][0]["schedule_id"]:
                raise DiagnosticError("generated schedule differs from v2")
            schedule = schedules[0]
            for identity in contract["identities"]:
                variant, seed = identity["demand_variant"], identity["seed"]
                _cold, _cold_failures, cold_canonical = cold._run_observation(
                    schedule, variant=variant, seed=seed)
                if cold_canonical is None or \
                        cold_canonical.get("execution_arm") != "cold":
                    raise DiagnosticError(f"v2 cold arm failed for {variant}/{seed}")
                _warm, _warm_failures, warm_canonical = warm._run_observation(
                    schedule, variant=variant, seed=seed)
                if warm_canonical is None or \
                        warm_canonical.get("execution_arm") != "warm" or \
                        warm_canonical.get("warm_point_s") != \
                        identity["warm_point_s"]:
                    raise DiagnosticError(f"v2 warm arm failed for {variant}/{seed}")
                completed.append(identity_label_v2(identity))

            raw_payloads = observer.raw_payloads(contract["identities"])
            reports = [build_forensic_report_v2(raw) for raw in raw_payloads]
            verdict = build_global_verdict_v2(reports, contract["identities"])
            ledger = {
                "schema": "monthly_warm_state_residual_execution_v2",
                "contract_content_key": contract["content_key"],
                "completed_identities": completed,
                "warm_attempts": list(warm.warm_attempts),
                "cache_published": False,
            }
            members = {
                "contract.json": _json_bytes(contract),
                "execution_ledger.json": _json_bytes(ledger),
                "verdict.json": _json_bytes(verdict),
            }
            for raw, report in zip(raw_payloads, reports):
                label = identity_label_v2(raw["identity"])
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
    try:
        return run_diagnostic(contract, root, runner_factory=runner_factory)
    except BaseException as error:
        if root.exists():
            raise
        ledger = getattr(error, "completed_ledger", [])
        entries = list(ledger) if isinstance(ledger, list) else []
        members = {
            "contract.json": _json_bytes(contract),
            "completed_arms.json": _json_bytes({
                "schema": "monthly_warm_state_completed_arms_v2",
                "entries": entries,
            }),
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
