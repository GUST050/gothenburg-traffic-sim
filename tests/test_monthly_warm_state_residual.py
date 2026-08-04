"""Process-free contract and end-to-end tests for the residual localizer."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from traffic_sim.simulation import warm_state_forensics as f


TOOL = Path("tools/diagnose_monthly_warm_state_residual.py")


def _tool():
    spec = importlib.util.spec_from_file_location("warm_residual_diag", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(loss, *, duration=10.0):
    return {"timeLoss": float(loss), "depart": 0.0, "arrival": duration,
            "duration": float(duration), "routeLength": 100.0,
            "waitingTime": 0.0, "waitingCount": 0.0}


def _write_tripinfo(path, records):
    rows = []
    for vehicle, record in records.items():
        attrs = " ".join([f'id="{vehicle}"'] +
                         [f'{key}="{value}"' for key, value in record.items()])
        rows.append(f"<tripinfo {attrs}/>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<tripinfos>\n" + "\n".join(rows) + "\n</tripinfos>\n")


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

    def test_runtime_names_are_lazy_and_the_gate_is_first(self):
        tree = ast.parse(TOOL.read_text())
        execute = next(node for node in tree.body
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "execute")
        body = list(execute.body)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)):
            body = body[1:]
        assert "require_approval" in ast.dump(body[0])
        runtime = next(node for node in tree.body
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "runtime_preflight")
        assert "resolve_traci" in ast.dump(runtime)
        assert "resolve_traci" not in "".join(
            ast.dump(node) for node in tree.body if node is not runtime)

    def test_import_and_help_load_no_simulator(self):
        before = set(sys.modules)
        module = _tool()
        assert "traci" not in set(sys.modules) - before
        assert "libsumo" not in set(sys.modules) - before
        with pytest.raises(SystemExit) as stopped:
            module.main(["--help"])
        assert stopped.value.code == 0

    def test_wrong_or_missing_token_stops_before_live_inputs(self, monkeypatch):
        module = _tool()
        monkeypatch.setattr(module, "frozen_key", lambda: "k" * 64)
        called = []
        monkeypatch.setattr(module, "verify_live_inputs",
                            lambda: called.append("live"))
        with pytest.raises(module.ApprovalRequired):
            module.execute(None)
        with pytest.raises(module.ApprovalRequired):
            module.execute("wrong")
        assert called == []


class TestObserverIntegration:

    def test_observer_is_opt_in_at_both_production_boundaries(self):
        from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner
        import run_monthly_warm_state_validation as harness

        runner_parameter = inspect.signature(
            ArchivedDemandSumoRunner).parameters["forensic_observer"]
        harness_parameter = inspect.signature(
            harness.build_runner).parameters["forensic_observer"]
        assert runner_parameter.default is None
        assert harness_parameter.default is None
        assert "forensic_observer=forensic_observer" in inspect.getsource(
            harness.build_runner)

    def test_observer_failures_escape_the_normal_warm_fallback(self):
        from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

        source = inspect.getsource(ArchivedDemandSumoRunner._run_observation)
        tree = ast.parse(inspect.cleandoc(source))
        handlers = [node for node in ast.walk(tree)
                    if isinstance(node, ast.ExceptHandler)]
        forensic_handlers = [node for node in handlers
                             if getattr(node.type, "id", None) ==
                             "ForensicObservationError"]
        assert forensic_handlers
        assert all(len(node.body) == 1 and isinstance(node.body[0], ast.Raise)
                   for node in forensic_handlers)

    def test_default_off_capture_helpers_return_before_observer_access(self):
        from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

        for name in ("_capture_forensic", "_capture_warm_forensic_summary"):
            source = textwrap.dedent(inspect.getsource(
                getattr(ArchivedDemandSumoRunner, name)))
            tree = ast.parse(source)
            function = tree.body[0]
            first = function.body[1] if isinstance(function.body[0], ast.Expr) \
                else function.body[0]
            assert isinstance(first, ast.If)
            assert "self._forensic_observer is None" in ast.unparse(first.test)
            assert isinstance(first.body[0], ast.Return)


class TestFrozenContract:

    def test_build_binds_v9_physical_case_without_reading_runs(self):
        module = _tool()
        contract = module.build_contract()
        assert contract["status"] == "frozen_unapproved_unexecuted"
        assert contract["v9_physical_case"]["manifest_content_key"] == \
            module.V9_CONTENT_KEY
        assert [item["demand_variant"] for item in contract["identities"]] == \
            ["q10", "q50", "q90"]
        assert {item["warm_point_s"] for item in contract["identities"]} == \
            {24300}
        assert module.canonical_key(contract) == contract["content_key"]
        assert not any(path.startswith("runs/")
                       for path in contract["source_fingerprints"])

    def test_freeze_verify_and_no_clobber(self, tmp_path):
        module = _tool()
        path = tmp_path / "contract.json"
        key = module.freeze(path)
        assert len(key) == 64 and module.verify(path)
        before = path.read_bytes()
        with pytest.raises(module.DiagnosticError, match="overwrite"):
            module.freeze(path)
        assert path.read_bytes() == before

    def test_output_and_staging_are_only_named_not_created(self, tmp_path,
                                                            monkeypatch):
        module = _tool()
        monkeypatch.setattr(module, "ROOT", tmp_path)
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

    def _capture(self, schedule, variant, seed):
        cold = {"a": _record(1), "b": _record(2), "c": _record(3)}
        if not self.warm:
            path = self.workspace / f"{variant}-cold.xml"
            _write_tripinfo(path, cold)
            self.observer.capture(
                schedule_id=schedule.schedule_id, demand_variant=variant,
                seed=seed, phase="cold_candidate", tripinfo_path=path,
                reported_total_s=6.0, reported_trip_count=3)
            return {"execution_arm": "cold", "warm_point_s": None}
        prefix = {"a": cold["a"]}
        resumed = {"b": _record(1.9 if self.drift and variant == "q10" else 2),
                   "c": cold["c"]}
        for phase, records in (("completed_prefix", prefix),
                               ("resumed", resumed)):
            path = self.workspace / f"{variant}-{phase}.xml"
            _write_tripinfo(path, records)
            self.observer.capture(
                schedule_id=schedule.schedule_id, demand_variant=variant,
                seed=seed, phase=phase, tripinfo_path=path,
                reported_total_s=sum(x["timeLoss"] for x in records.values()),
                reported_trip_count=len(records))
        warm_total = sum(x["timeLoss"] for x in prefix.values()) + \
            sum(x["timeLoss"] for x in resumed.values())
        self.observer.capture_warm_summary(
            schedule_id=schedule.schedule_id, demand_variant=variant, seed=seed,
            reported_total_s=round(warm_total, 2), reported_trip_count=3)
        self.warm_attempts.append({
            "schema": "monthly_warm_attempt_v1",
            "schedule_id": schedule.schedule_id, "demand_variant": variant,
            "seed": seed, "outcome": "warm_executed",
            "events": [{"code": "warm_completed", "details": {}}],
        })
        return {"execution_arm": "warm", "warm_point_s": 24300}

    def _run_observation(self, schedule, *, variant, seed):
        return object(), (), self._capture(schedule, variant, seed)


def _factory(*, drift=False):
    def build(_manifest, *, warm, workspace, forensic_observer, **_kwargs):
        return _FakeRunner(warm=warm, workspace=workspace,
                           observer=forensic_observer, drift=drift)
    return build


class TestTerminalArtifacts:

    def test_fake_full_path_publishes_recomputable_diagnostic(self, tmp_path):
        module = _tool()
        contract = module.build_contract()
        root = tmp_path / "outcome"
        schedule = SimpleNamespace(
            schedule_id=contract["identities"][0]["schedule_id"])
        result = module.run_diagnostic(
            contract, root, runner_factory=_factory(drift=True),
            schedule_factory=lambda _spec: [schedule])
        assert result["verdict"]["classification_counts"][
            f.TIME_LOSS_ONLY_DRIFT] == 1
        assert result["verdict"]["all_exact"] is False
        before = {path.name: path.read_bytes() for path in root.iterdir()}
        assert module.validate_success_artifact(
            root, contract["content_key"])["verdict"] == result["verdict"]
        assert {path.name: path.read_bytes() for path in root.iterdir()} == before

    def test_success_validator_rejects_tampered_report(self, tmp_path):
        module = _tool()
        contract = module.build_contract()
        root = tmp_path / "outcome"
        schedule = SimpleNamespace(
            schedule_id=contract["identities"][0]["schedule_id"])
        module.run_diagnostic(
            contract, root, runner_factory=_factory(),
            schedule_factory=lambda _spec: [schedule])
        report = next(root.glob("report_*.json"))
        payload = json.loads(report.read_text())
        payload["classification"] = f.TIME_LOSS_ONLY_DRIFT
        report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        with pytest.raises(module.DiagnosticError):
            module.validate_success_artifact(root, contract["content_key"])

    def test_failure_artifact_allowlist_and_validator(self, tmp_path):
        module = _tool()
        contract = module.build_contract()
        root = tmp_path / "failure"
        key = contract["content_key"]
        members = {
            "contract.json": module._json_bytes(contract),
            "completed_arms.json": module._json_bytes({
                "schema": "monthly_warm_state_completed_arms_v1",
                "entries": [{"identity": contract["identities"][0],
                             "captured_phases": ["cold_candidate"]}]}),
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
        (root / "smuggled.json").write_text("{}")
        with pytest.raises(module.DiagnosticError, match="allowlist"):
            module.validate_failure_artifact(root, key)

    def test_publication_refuses_existing_root(self, tmp_path):
        module = _tool()
        root = tmp_path / "occupied"
        root.mkdir()
        with pytest.raises(module.DiagnosticError, match="reuse"):
            module._publish(root, {}, ())
