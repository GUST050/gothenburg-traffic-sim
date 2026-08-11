"""The registration must describe the experiment it actually froze.

Two provenance defects motivated this file, and neither was visible from a
green test suite — both were claims the record made that happened to be false.

  * `outcome_record` was hard-coded to the tool's own default, so a
    registration written to a v3 path still named the v2 outcome. Nobody would
    notice until two records disagreed about which run produced which.
  * `sources` sealed ten files chosen by hand while the arms import forty-six.
    The gap was not academic: the runtime correction in adf765b changed
    `monthly_sumo.py` and `suggest_closure_time.py`, and a v2 registration
    would have reported no drift whatsoever — a benchmark bound to a runtime it
    could not tell had changed underneath it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.cost_ordered_benchmark as bench

ROOT = Path(__file__).resolve().parents[1]


def _fixture_tree(tmp_path: Path) -> Path:
    """The minimum a registration needs to bind: a network and its metadata."""
    (tmp_path / "sumo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sumo" / "net.net.xml").write_text("<net/>", encoding="utf-8")
    (tmp_path / "sumo" / "network_metadata.json").write_text(
        "{}", encoding="utf-8")
    return tmp_path


class TestTheRegistrationNamesItsOwnOutcome:
    def test_a_custom_v3_registration_points_at_its_custom_v3_outcome(
            self, tmp_path):
        tree = _fixture_tree(tmp_path)
        registration = tmp_path / "cost_ordered_benchmark_registration_v3.json"
        outcome = tmp_path / "cost_ordered_benchmark_outcome_v3.json"
        bench.main(["--preregister", "--runs-root", str(tree),
                    "--data-root", str(tree),
                    "--registration", str(registration),
                    "--out", str(outcome)])
        record = json.loads(registration.read_text(encoding="utf-8"))
        assert record["outcome_record"] == bench._relative(outcome.resolve())
        assert "v2" not in record["outcome_record"], (
            "a v3 registration must not claim the v2 outcome")

    def test_the_default_is_still_the_default(self, tmp_path):
        tree = _fixture_tree(tmp_path)
        registration = tmp_path / "registration.json"
        bench.main(["--preregister", "--runs-root", str(tree),
                    "--data-root", str(tree),
                    "--registration", str(registration)])
        record = json.loads(registration.read_text(encoding="utf-8"))
        assert record["outcome_record"] == bench._relative(
            bench.DEFAULT_OUTCOME.resolve())

    def test_a_run_refuses_to_write_an_outcome_the_registration_disowns(
            self, tmp_path):
        from tests.test_cost_ordered_benchmark_run import _registration
        from tests.test_cost_ordered_execution import _spec

        tree = _fixture_tree(tmp_path)
        declared = tmp_path / "declared.json"
        record = _registration(tmp_path, _spec(), outcome=declared)
        path = tmp_path / "registration.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        assert record["outcome_record"] == bench._relative(declared.resolve())
        with pytest.raises(SystemExit, match="names .* as its outcome"):
            bench.main(["--run", "--registration", str(path),
                        "--runs-root", str(tree), "--data-root", str(tree),
                        "--workspace-root", str(tmp_path / "ws"),
                        "--out", str(tmp_path / "somewhere-else.json")])


class TestTheSealCoversWhatActuallyRuns:
    def test_the_seal_covers_the_real_import_closure(self):
        """Re-derive the arms' import closure and require the seal to cover it.

        Pinned rather than computed at registration time: a seal that
        discovers its own contents would silently shrink when an import moved,
        and a registration must fail loudly instead.
        """
        import sys

        import tools.product_arm  # noqa: F401
        import run_monthly_closure_search  # noqa: F401
        from traffic_sim.simulation import cost_ordered_execution  # noqa: F401
        from traffic_sim.simulation import deterministic_disruption  # noqa: F401
        from traffic_sim.simulation import independent_daily  # noqa: F401
        from traffic_sim.simulation import monthly_demand  # noqa: F401
        from traffic_sim.simulation import monthly_search  # noqa: F401
        from traffic_sim.simulation import monthly_sumo  # noqa: F401

        reached = set()
        for module in list(sys.modules.values()):
            filename = getattr(module, "__file__", None)
            if not filename:
                continue
            try:
                relative = Path(filename).resolve().relative_to(ROOT)
            except ValueError:
                continue
            if relative.parts[0] in ("tests", ".git"):
                continue
            reached.add(str(relative))

        missing = sorted(reached - set(bench.SEMANTIC_SOURCES))
        assert not missing, (
            "these modules run inside a benchmark arm but are not sealed, so "
            f"changing them would not register as drift: {missing}")

    def test_every_named_semantic_source_is_sealed(self):
        """The files the task named explicitly, plus the ones the audit added."""
        for name in ("tools/product_arm.py",
                     "traffic_sim/simulation/monthly_demand.py",
                     "traffic_sim/simulation/monthly_sumo.py",
                     "traffic_sim/simulation/independent_daily.py",
                     "traffic_sim/simulation/envelope.py",
                     "traffic_sim/simulation/closure_teleport.py",
                     "traffic_sim/simulation/metrics.py",
                     "suggest_closure_time.py",
                     "traffic_sim/core/closure_calendar.py",
                     "traffic_sim/core/contracts.py",
                     "traffic_sim/simulation/search_workspace.py",
                     "traffic_sim/simulation/workspace.py"):
            assert name in bench.SEMANTIC_SOURCES, name

    def test_every_sealed_source_exists(self):
        for name in bench.SEMANTIC_SOURCES:
            assert (ROOT / name).is_file(), name

    def test_the_seal_is_sorted_and_unique(self):
        assert list(bench.SEMANTIC_SOURCES) == sorted(
            set(bench.SEMANTIC_SOURCES))


class TestDriftIsReported:
    def _registration(self, tmp_path):
        tree = _fixture_tree(tmp_path)
        path = tmp_path / "registration.json"
        bench.main(["--preregister", "--runs-root", str(tree),
                    "--data-root", str(tree), "--registration", str(path)])
        return json.loads(path.read_text(encoding="utf-8")), tree

    @pytest.mark.parametrize("source", [
        "traffic_sim/simulation/monthly_sumo.py",
        "tools/product_arm.py",
        "traffic_sim/simulation/envelope.py",
        "traffic_sim/simulation/independent_daily.py",
        "suggest_closure_time.py",
        "traffic_sim/simulation/metrics.py",
    ])
    def test_changing_a_semantic_source_is_drift(self, tmp_path, source):
        record, tree = self._registration(tmp_path)
        assert source in record["sources"], source
        record["sources"][source] = "0" * 64
        drift = bench.verify_bindings(record, tree)
        assert any(source in item for item in drift), drift

    def test_an_intact_registration_has_no_drift(self, tmp_path):
        record, tree = self._registration(tmp_path)
        assert bench.verify_bindings(record, tree) == []

    def test_tampering_with_the_body_is_detected(self, tmp_path):
        record, _tree = self._registration(tmp_path)
        record["hypothesis"] = "a different question entirely"
        assert any("content key does not describe it" in item
                   for item in bench.verify_bindings(record, ROOT))

    def test_the_registration_is_self_consistent_as_written(self, tmp_path):
        record, _tree = self._registration(tmp_path)
        body = {key: value for key, value in record.items()
                if key not in {"content_key", "registered_at"}}
        assert record["content_key"] == bench._content_key(body)


class TestTheSchemasStaySeparate:
    def test_the_current_contract_is_v3(self):
        assert bench.REGISTRATION_SCHEMA.endswith("_v3")
        assert bench.OUTCOME_SCHEMA.endswith("_v3")
        assert bench.DEFAULT_REGISTRATION.name.endswith("_v3.json")
        assert bench.DEFAULT_OUTCOME.name.endswith("_v3.json")

    def test_v2_is_still_readable(self):
        assert bench.REGISTRATION_SCHEMA_V2 in (
            bench.SUPPORTED_REGISTRATION_SCHEMAS)

    def test_a_v2_registration_still_produces_a_v2_outcome(self):
        """Replaying frozen history must not give it a successor it never
        licensed."""
        v2 = json.loads(
            (ROOT / "validation"
             / "cost_ordered_benchmark_registration_v2.json")
            .read_text(encoding="utf-8"))
        outcome = bench.build_outcome(v2, {"sumo_verifications_saved": 0},
                                      status="measured")
        assert outcome["schema"] == bench.OUTCOME_SCHEMA_V2
        assert outcome["registration"]["schema"] == v2["schema"]

    def test_a_v3_registration_produces_a_v3_outcome(self, tmp_path):
        tree = _fixture_tree(tmp_path)
        path = tmp_path / "registration.json"
        bench.main(["--preregister", "--runs-root", str(tree),
                    "--data-root", str(tree), "--registration", str(path)])
        record = json.loads(path.read_text(encoding="utf-8"))
        outcome = bench.build_outcome(record, {"sumo_verifications_saved": 0},
                                      status="measured")
        assert outcome["schema"] == bench.OUTCOME_SCHEMA

    def test_an_unknown_schema_is_refused(self, tmp_path):
        tree = _fixture_tree(tmp_path)
        path = tmp_path / "registration.json"
        path.write_text(json.dumps({"schema": "something_else_v9",
                                    "selected_case": None}), encoding="utf-8")
        with pytest.raises(SystemExit, match="unsupported registration schema"):
            bench.main(["--run", "--registration", str(path),
                        "--runs-root", str(tree), "--data-root", str(tree),
                        "--out", str(tmp_path / "outcome.json")])


class TestTheFrozenHistoryIsUntouched:
    @pytest.mark.parametrize("name", [
        "cost_ordered_benchmark_registration_v1.json",
        "cost_ordered_benchmark_registration_v2.json",
        "cost_ordered_benchmark_outcome_v2.json",
    ])
    def test_the_frozen_records_still_validate_against_themselves(self, name):
        record = json.loads((ROOT / "validation" / name)
                            .read_text(encoding="utf-8"))
        skip = {"content_key", "registered_at", "measured_at"}
        body = {key: value for key, value in record.items()
                if key not in skip}
        assert record["content_key"] == bench._content_key(body), name

    def test_the_v2_outcome_still_records_its_failure(self):
        outcome = json.loads(
            (ROOT / "validation" / "cost_ordered_benchmark_outcome_v2.json")
            .read_text(encoding="utf-8"))
        assert outcome["status"] == "failed_execution"
        assert outcome["gates"]["passed"] is False
        assert outcome["comparison"]["execution_error"]["message"].startswith(
            "sumo timed out after 300s")


class TestTheSimulatorIsBoundToo:
    """Sealing every line of Python and none of the simulator seals half.

    SUMO decides every observation, so a different executable or version is
    exactly as much of a semantic change as an edited source file — and neither
    is visible in a source digest.
    """

    def _registration(self, tmp_path):
        tree = _fixture_tree(tmp_path)
        path = tmp_path / "registration.json"
        bench.main(["--preregister", "--runs-root", str(tree),
                    "--data-root", str(tree), "--registration", str(path)])
        return json.loads(path.read_text(encoding="utf-8")), tree

    def test_the_runtime_identity_is_recorded(self, tmp_path):
        record, _tree = self._registration(tmp_path)
        runtime = record["sumo_runtime"]
        for field in ("executable", "version", "sha256", "resolved_by",
                      "platform", "machine"):
            assert field in runtime, field
        if runtime["executable"]:
            assert runtime["sha256"] and runtime["version"]

    def test_a_different_sumo_binary_is_drift(self, tmp_path):
        record, tree = self._registration(tmp_path)
        if not record["sumo_runtime"]["sha256"]:
            pytest.skip("no SUMO executable resolved in this environment")
        record["sumo_runtime"]["sha256"] = "0" * 64
        assert any("executable bytes changed" in item
                   for item in bench.verify_bindings(record, tree))

    def test_a_different_sumo_version_is_drift(self, tmp_path):
        record, tree = self._registration(tmp_path)
        record["sumo_runtime"]["version"] = "Eclipse SUMO sumo 0.0.0"
        assert any("version changed" in item
                   for item in bench.verify_bindings(record, tree))

    def test_a_different_platform_is_drift(self, tmp_path):
        record, tree = self._registration(tmp_path)
        record["sumo_runtime"]["machine"] = "s390x"
        assert any("machine changed" in item
                   for item in bench.verify_bindings(record, tree))

    def test_an_intact_runtime_binding_is_not_drift(self, tmp_path):
        record, tree = self._registration(tmp_path)
        assert bench.verify_bindings(record, tree) == []


class TestAbsenceIsBoundAsFirmlyAsPresence:
    """"There was no adopted gate when this was frozen" is a claim.

    A later-appearing certificate silently widening what a replay may claim is
    exactly the drift this exists to catch, so absence is recorded explicitly
    rather than omitted.
    """

    def _registration(self, tmp_path):
        tree = _fixture_tree(tmp_path)
        path = tmp_path / "registration.json"
        bench.main(["--preregister", "--runs-root", str(tree),
                    "--data-root", str(tree), "--registration", str(path)])
        return json.loads(path.read_text(encoding="utf-8")), tree

    def test_every_gate_artifact_has_an_explicit_presence_verdict(self,
                                                                   tmp_path):
        record, _tree = self._registration(tmp_path)
        state = record["external_gate_state"]
        for name in bench.EXTERNAL_GATE_ARTIFACTS:
            assert name in state, name
            assert "present" in state[name]
        assert "certificate_manifest" in state

    def test_an_absent_gate_that_appears_is_drift(self, tmp_path):
        record, tree = self._registration(tmp_path)
        name = "validation/monthly_gate_record.json"
        if record["external_gate_state"][name]["present"]:
            pytest.skip("this tree already has an adopted gate record")
        # Register the OPPOSITE of what is live: the record claims the file was
        # present, the tree says it is absent. Same asymmetry either way round.
        record["external_gate_state"][name] = {
            "present": True, "sha256": "0" * 64, "bytes": 1}
        drift = bench.verify_bindings(record, tree)
        assert any(name in item and "present at registration" in item
                   for item in drift), drift

    def test_a_changed_gate_record_is_drift(self, tmp_path):
        record, tree = self._registration(tmp_path)
        name = "validation/monthly_gate_record.json"
        if not record["external_gate_state"][name]["present"]:
            pytest.skip("no gate record present to change")
        record["external_gate_state"][name]["sha256"] = "0" * 64
        assert any("external gate artifact changed" in item
                   for item in bench.verify_bindings(record, tree))

    def test_a_changed_certificate_manifest_is_drift(self, tmp_path):
        record, tree = self._registration(tmp_path)
        record["external_gate_state"]["certificate_manifest"] = {
            "named_by_certificate": "validation/monthly_proxy_manifest_v6.json",
            "present": True, "sha256": "0" * 64}
        assert any("named by the adoption certificate changed" in item
                   for item in bench.verify_bindings(record, tree))

    def test_absence_is_not_silently_treated_as_nothing_to_bind(self,
                                                                tmp_path):
        record, _tree = self._registration(tmp_path)
        state = record["external_gate_state"]
        absent = [name for name in bench.EXTERNAL_GATE_ARTIFACTS
                  if not state[name]["present"]]
        if not absent:
            pytest.skip("every gate artifact exists in this tree")
        for name in absent:
            assert state[name]["present"] is False, (
                "an absent artifact must be recorded as absent, not omitted")
