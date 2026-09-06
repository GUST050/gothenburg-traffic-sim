"""Process-free contract and end-to-end tests for residual diagnostic v2."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from traffic_sim.simulation import warm_state_forensics as f


TOOL = Path("tools/diagnose_monthly_warm_state_residual_v2.py")


def _tool():
    spec = importlib.util.spec_from_file_location("warm_residual_diag_v2", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frozen_contract(module):
    """Load the immutable retired contract without re-keying current sources."""
    contract = json.loads(
        (module.ROOT / module.CONTRACT_PATH).read_text(encoding="utf-8"))
    assert module.canonical_key(contract) == contract["content_key"]
    return contract


def _record(loss, *, arrival):
    return {
        "timeLoss": float(loss), "depart": 0.0, "arrival": float(arrival),
        "duration": float(arrival), "routeLength": 100.0,
        "waitingTime": 0.0, "waitingCount": 0.0,
    }


def _write_tripinfo(path, records):
    rows = []
    for vehicle, record in records.items():
        attrs = " ".join([f'id="{vehicle}"'] + [
            f'{field}="{record[field]}"' for field in f.TRIP_FIELDS])
        rows.append(f"<tripinfo {attrs}/>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<tripinfos>" + "".join(rows) + "</tripinfos>")


class TestProcessFreeBoundary:

    def test_module_scope_has_no_runtime_process_or_socket_import(self):
        tree = ast.parse(TOOL.read_text())
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                top.add(node.module.split(".")[0])
        assert not top & {"traci", "libsumo", "socket", "subprocess",
                          "multiprocessing"}

    def test_gate_is_first_and_runtime_preflight_is_lazy(self):
        tree = ast.parse(TOOL.read_text())
        execute = next(node for node in tree.body
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "execute")
        assert "require_approval" in ast.dump(execute.body[0])
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "runtime_preflight"]
        assert len(calls) == 1
        parent = next(node for node in tree.body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "runtime_preflight")
        assert calls[0] in list(ast.walk(parent))

    def test_import_and_help_load_no_simulator(self):
        before = set(sys.modules)
        module = _tool()
        assert "traci" not in set(sys.modules) - before
        assert "libsumo" not in set(sys.modules) - before
        with pytest.raises(SystemExit) as stopped:
            module.main(["--help"])
        assert stopped.value.code == 0

    def test_bad_token_stops_before_live_inputs(self, monkeypatch):
        module = _tool()
        monkeypatch.setattr(module, "frozen_key", lambda: "k" * 64)
        calls = []
        monkeypatch.setattr(module, "verify_live_inputs",
                            lambda: calls.append("live"))
        with pytest.raises(module.ApprovalRequired):
            module.execute(None)
        with pytest.raises(module.ApprovalRequired):
            module.execute("wrong")
        assert calls == []


class TestFrozenContract:

    def test_contract_binds_v1_v9_and_boundary_rule(self):
        module = _tool()
        contract = _frozen_contract(module)
        assert contract["status"] == "frozen_unapproved_unexecuted"
        assert contract["supersedes"]["content_key"] == module.V1_CONTENT_KEY
        assert contract["supersedes"]["preserved_files_sha256"] == \
            module.V1_FILES
        assert contract["v9_physical_case"]["manifest_content_key"] == \
            module.v1.V9_CONTENT_KEY
        assert [item["demand_variant"] for item in contract["identities"]] == \
            ["q10", "q50", "q90"]
        assert {item["warm_point_s"] for item in contract["identities"]} == \
            {24300}
        assert "arrival <= warm_point_s" in \
            contract["classifier"]["boundary_rule"]
        assert module.canonical_key(contract) == contract["content_key"]
        assert not any(path.startswith("runs/")
                       for path in contract["source_fingerprints"])
        with pytest.raises(module.DiagnosticError, match="preserved v9 file drifted"):
            module.build_contract()

    def test_freeze_verify_and_no_clobber(self, tmp_path, monkeypatch):
        module = _tool()
        contract = _frozen_contract(module)
        monkeypatch.setattr(module, "build_contract", lambda: contract)
        path = tmp_path / "v2.json"
        key = module.freeze(path)
        assert len(key) == 64 and module.verify(path)
        before = path.read_bytes()
        with pytest.raises(module.DiagnosticError, match="overwrite"):
            module.freeze(path)
        assert path.read_bytes() == before

    def test_root_and_staging_check_creates_nothing(self, tmp_path,
                                                     monkeypatch):
        module = _tool()
        monkeypatch.setattr(module.v1, "ROOT", tmp_path)
        contract = {"execution_contract": {
            "output_root": "validation/outcome", "staging_suffix": ".partial"}}
        root, staging = module.require_absent_paths(contract)
        assert not root.exists() and not staging.exists()
        assert not (tmp_path / "validation").exists()


class _FakeRunner:
    def __init__(self, *, warm, workspace, observer, drift=False):
        self.warm = warm
        self.workspace = Path(workspace)
        self.observer = observer
        self.drift = drift
        self.spec = object()
        self.warm_attempts = []

    def _run_observation(self, schedule, *, variant, seed):
        cold = {
            "before": _record(1, arrival=24299),
            "boundary": _record(2, arrival=24300),
            "after": _record(3, arrival=24301),
        }
        if not self.warm:
            path = self.workspace / f"{variant}-cold.xml"
            _write_tripinfo(path, cold)
            self.observer.capture(
                schedule_id=schedule.schedule_id, demand_variant=variant,
                seed=seed, phase="cold_candidate", tripinfo_path=path,
                reported_total_s=6.0, reported_trip_count=3)
            return object(), (), {"execution_arm": "cold", "warm_point_s": None}
        prefix = {"before": cold["before"], "boundary": cold["boundary"]}
        resumed = {"after": _record(
            2.9 if self.drift and variant == "q10" else 3,
            arrival=24301)}
        for phase, records in (("completed_prefix", prefix),
                               ("resumed", resumed)):
            path = self.workspace / f"{variant}-{phase}.xml"
            _write_tripinfo(path, records)
            self.observer.capture(
                schedule_id=schedule.schedule_id, demand_variant=variant,
                seed=seed, phase=phase, tripinfo_path=path,
                reported_total_s=round(
                    sum(record["timeLoss"] for record in records.values()), 2),
                reported_trip_count=len(records))
        warm_total = sum(x["timeLoss"] for x in prefix.values()) + \
            sum(x["timeLoss"] for x in resumed.values())
        self.observer.capture_warm_summary(
            schedule_id=schedule.schedule_id, demand_variant=variant, seed=seed,
            reported_total_s=round(warm_total, 2), reported_trip_count=3)
        self.warm_attempts.append({
            "schema": "monthly_warm_attempt_v1",
            "schedule_id": schedule.schedule_id,
            "demand_variant": variant, "seed": seed,
            "outcome": "warm_executed",
            "events": [{"code": "warm_completed", "details": {}}],
        })
        return object(), (), {"execution_arm": "warm", "warm_point_s": 24300}


def _factory(*, drift=False):
    def build(_manifest, *, warm, workspace, forensic_observer, **_kwargs):
        return _FakeRunner(warm=warm, workspace=workspace,
                           observer=forensic_observer, drift=drift)
    return build


class TestTerminalArtifacts:

    def test_fake_full_path_is_boundary_aware_and_recomputable(self, tmp_path):
        module = _tool()
        contract = _frozen_contract(module)
        schedule = SimpleNamespace(
            schedule_id=contract["identities"][0]["schedule_id"])
        root = tmp_path / "outcome"
        result = module.run_diagnostic(
            contract, root, runner_factory=_factory(drift=True),
            schedule_factory=lambda _spec: [schedule])
        assert result["verdict"]["classification_counts"][
            f.TIME_LOSS_ONLY_DRIFT] == 1
        assert all(report["population"]["misplaced_in_prefix"] == []
                   for report in result["reports"])
        before = {path.name: path.read_bytes() for path in root.iterdir()}
        assert module.validate_success_artifact(
            root, contract["content_key"])["verdict"] == result["verdict"]
        assert {path.name: path.read_bytes() for path in root.iterdir()} == before

    def test_success_validator_rejects_tamper(self, tmp_path):
        module = _tool()
        contract = _frozen_contract(module)
        schedule = SimpleNamespace(
            schedule_id=contract["identities"][0]["schedule_id"])
        root = tmp_path / "outcome"
        module.run_diagnostic(
            contract, root, runner_factory=_factory(),
            schedule_factory=lambda _spec: [schedule])
        report = next(root.glob("report_*.json"))
        payload = json.loads(report.read_text())
        payload["aggregate_deltas"]["split_minus_cold_time_loss_s"] = 999
        report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        with pytest.raises(module.DiagnosticError):
            module.validate_success_artifact(root, contract["content_key"])

    def test_success_validator_rejects_symlink_member(self, tmp_path):
        module = _tool()
        contract = _frozen_contract(module)
        schedule = SimpleNamespace(
            schedule_id=contract["identities"][0]["schedule_id"])
        root = tmp_path / "outcome"
        module.run_diagnostic(
            contract, root, runner_factory=_factory(),
            schedule_factory=lambda _spec: [schedule])
        report = next(root.glob("report_*.json"))
        target = tmp_path / "outside.json"
        target.write_bytes(report.read_bytes())
        report.unlink()
        report.symlink_to(target)
        with pytest.raises(module.DiagnosticError, match="regular file"):
            module.validate_success_artifact(root, contract["content_key"])

    def test_failure_artifact_is_strict_and_recomputable(self, tmp_path):
        module = _tool()
        contract = _frozen_contract(module)
        key = contract["content_key"]
        root = tmp_path / "failure"
        members = {
            "contract.json": module._json_bytes(contract),
            "completed_arms.json": module._json_bytes({
                "schema": "monthly_warm_state_completed_arms_v2",
                "entries": []}),
            "error.json": module._json_bytes({
                "schema": module.FAILURE_SCHEMA,
                "diagnostic_id": module.DIAGNOSTIC_ID,
                "contract_content_key": key,
                "error_type": "RuntimeError", "error_message": "boom"}),
        }
        module._publish(
            root, members, module.FAILURE_NAMES,
            validator=lambda staged: module.validate_failure_artifact(staged, key))
        assert module.validate_failure_artifact(root, key)["error"][
            "error_message"] == "boom"
        (root / "extra.json").write_text("{}")
        with pytest.raises(module.DiagnosticError, match="allowlist"):
            module.validate_failure_artifact(root, key)
