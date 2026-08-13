"""Step 8 acceptance: freeze before running, fail closed, never fake a pass.

The plan's ordering rule: freeze the design, then run, then judge. A
threshold chosen after seeing the numbers is a description, not a gate.
Adoption needs TWO artifacts — a registration frozen before execution and an
outcome naming its exact key — because this repository already learned that
a lone gate record is self-certifying.
"""

from __future__ import annotations

import json

import pytest

from traffic_sim.simulation import uncertainty_gate as ug


def registration(**overrides):
    base = dict(
        campaign="test_campaign",
        model_candidates=("constant_5050",),
        fold_kinds=("leave_city_out",),
        metric_hierarchy=("mae",),
        nominal_coverage=0.8,
        coverage_tolerance=0.05,
        primary_groups=("all",),
        scenario_batches=(4, 8),
        max_compute_units=100,
        seed_start=1000,
        min_seeds=4,
        adaptive_repetition_rule="rule",
        ci_method="block bootstrap",
        bootstrap_seed=1,
        risk_measures=("weighted_expectation",),
        tie_rule="rule",
        safety_gates=("no_detour",),
        test_dates=(),
        test_edges=(),
        selection_rule="simplest_defensible_v1",
        outcome_blind_note="note",
        source_fingerprints={"a.py": "digest"},
    )
    base.update(overrides)
    return ug.Registration(**base)


def passing_metrics():
    return {name: (threshold if direction == "ge" else threshold)
            for name, (direction, threshold) in ug.GATE_THRESHOLDS.items()}


class TestRegistrationIsFrozen:
    def test_it_writes_and_carries_a_content_key(self, tmp_path):
        path = tmp_path / "reg.json"
        key = ug.freeze_registration(registration(), path)
        payload = json.loads(path.read_text())
        assert payload["content_key"] == key
        assert payload["schema_version"] == ug.REGISTRATION_SCHEMA

    def test_it_refuses_to_overwrite(self, tmp_path):
        path = tmp_path / "reg.json"
        ug.freeze_registration(registration(), path)
        with pytest.raises(ug.GateError, match="already exists"):
            ug.freeze_registration(registration(), path)

    def test_the_no_overwrite_guarantee_comes_from_the_primitive(self):
        """O_EXCL, not an existence check that a race could slip between."""
        import inspect
        source = inspect.getsource(ug.freeze_registration)
        assert "O_EXCL" in source

    def test_changing_any_parameter_changes_the_key(self):
        base = registration().content_key
        assert registration(max_compute_units=200).content_key != base
        assert registration(min_seeds=8).content_key != base
        assert registration(nominal_coverage=0.9).content_key != base

    def test_an_edited_registration_fails_verification(self, tmp_path):
        path = tmp_path / "reg.json"
        ug.freeze_registration(registration(), path)
        payload = json.loads(path.read_text())
        payload["max_compute_units"] = 999999
        path.write_text(json.dumps(payload))
        status = ug.verify_registration(path, root=tmp_path)
        assert not status["valid"]
        assert "edited after freezing" in status["reason"]

    def test_an_intact_registration_verifies(self, tmp_path):
        path = tmp_path / "reg.json"
        ug.freeze_registration(registration(source_fingerprints={}), path)
        status = ug.verify_registration(path, root=tmp_path)
        assert status["valid"]
        assert not status["source_drift"]

    def test_source_drift_is_reported(self, tmp_path):
        path = tmp_path / "reg.json"
        ug.freeze_registration(
            registration(source_fingerprints={"gone.py": "abc"}), path)
        status = ug.verify_registration(path, root=tmp_path)
        assert status["source_drift"]
        assert status["drifted_sources"] == ["gone.py"]

    def test_invalid_parameters_are_refused(self):
        with pytest.raises(ug.GateError, match="nominal_coverage"):
            registration(nominal_coverage=1.5)
        with pytest.raises(ug.GateError, match="max_compute_units"):
            registration(max_compute_units=0)
        with pytest.raises(ug.GateError, match="ascending"):
            registration(scenario_batches=(8, 4))
        with pytest.raises(ug.GateError, match="model candidate"):
            registration(model_candidates=())


class TestEvaluationIsFailClosed:
    def frozen(self, tmp_path, **kwargs):
        path = tmp_path / "reg.json"
        key = ug.freeze_registration(
            registration(source_fingerprints={}, **kwargs), path)
        return path, key

    def test_no_registration_is_unregistered_not_a_pass(self, tmp_path):
        result = ug.evaluate_outcome(tmp_path / "missing.json", None,
                                     root=tmp_path)
        assert not result["activated"]
        assert result["status"] == "unregistered"

    def test_no_outcome_is_unexecuted_not_a_pass(self, tmp_path):
        path, _key = self.frozen(tmp_path)
        result = ug.evaluate_outcome(path, None, root=tmp_path)
        assert not result["activated"]
        assert result["status"] == "unexecuted"
        assert "not a pass" in result["detail"]["note"]

    def test_an_edited_registration_cannot_activate(self, tmp_path):
        path, key = self.frozen(tmp_path)
        payload = json.loads(path.read_text())
        payload["max_compute_units"] = 999999
        path.write_text(json.dumps(payload))
        outcome = ug.GateOutcome("test_campaign", key, True, passing_metrics())
        result = ug.evaluate_outcome(path, outcome, root=tmp_path)
        assert not result["activated"]
        assert result["status"] == "registration_invalid"

    def test_an_outcome_naming_another_registration_is_refused(self, tmp_path):
        path, _key = self.frozen(tmp_path)
        outcome = ug.GateOutcome("test_campaign", "some-other-key", True,
                                 passing_metrics())
        result = ug.evaluate_outcome(path, outcome, root=tmp_path)
        assert not result["activated"]
        assert result["status"] == "registration_mismatch"

    def test_an_unexecuted_outcome_cannot_activate(self, tmp_path):
        path, key = self.frozen(tmp_path)
        outcome = ug.GateOutcome("test_campaign", key, False,
                                 passing_metrics())
        result = ug.evaluate_outcome(path, outcome, root=tmp_path)
        assert not result["activated"]
        assert result["status"] == "unexecuted"

    def test_source_drift_blocks_activation(self, tmp_path):
        path = tmp_path / "reg.json"
        key = ug.freeze_registration(
            registration(source_fingerprints={"gone.py": "abc"}), path)
        outcome = ug.GateOutcome("test_campaign", key, True, passing_metrics())
        result = ug.evaluate_outcome(path, outcome, root=tmp_path)
        assert not result["activated"]
        assert result["status"] == "source_drift"

    def test_a_missing_metric_fails_rather_than_defaulting(self, tmp_path):
        path, key = self.frozen(tmp_path)
        metrics = passing_metrics()
        metrics.pop("joint_variogram_gain")
        outcome = ug.GateOutcome("test_campaign", key, True, metrics)
        result = ug.evaluate_outcome(path, outcome, root=tmp_path)
        assert not result["activated"]
        assert result["checks"]["joint_variogram_gain"]["reason"] == "missing"

    def test_one_failed_threshold_fails_the_whole_gate(self, tmp_path):
        path, key = self.frozen(tmp_path)
        metrics = passing_metrics()
        metrics["interval_coverage_error"] = 0.5      # must be <= 0.05
        outcome = ug.GateOutcome("test_campaign", key, True, metrics)
        result = ug.evaluate_outcome(path, outcome, root=tmp_path)
        assert not result["activated"]
        assert result["status"] == "failed"

    def test_a_complete_passing_outcome_activates(self, tmp_path):
        path, key = self.frozen(tmp_path)
        outcome = ug.GateOutcome("test_campaign", key, True, passing_metrics())
        result = ug.evaluate_outcome(path, outcome, root=tmp_path)
        assert result["activated"]
        assert result["status"] == "passed"

    def test_no_status_other_than_passed_can_read_as_activation(self, tmp_path):
        path, key = self.frozen(tmp_path)
        for outcome in (None,
                        ug.GateOutcome("c", "wrong", True, passing_metrics()),
                        ug.GateOutcome("c", key, False, passing_metrics())):
            result = ug.evaluate_outcome(path, outcome, root=tmp_path)
            assert result["activated"] is False


class TestNoOutcomeCanBeSynthesised:
    def test_the_module_offers_no_outcome_factory(self):
        for name in dir(ug):
            assert not name.startswith("make_outcome")
            assert not name.startswith("simulate_")
            assert not name.startswith("fake_")

    def test_gate_thresholds_live_in_code_not_in_data(self):
        """A passing outcome cannot be manufactured by editing a data file."""
        assert isinstance(ug.GATE_THRESHOLDS, dict)
        assert "interval_coverage_error" in ug.GATE_THRESHOLDS
        direction, threshold = ug.GATE_THRESHOLDS["interval_coverage_error"]
        assert direction == "le" and threshold == pytest.approx(0.05)


class TestTheDefaultRegistration:
    def test_it_builds_and_names_every_frozen_field(self):
        reg = ug.build_default_registration()
        assert reg.campaign == "closure_uncertainty_shadow_v1"
        assert "constant_5050" in reg.model_candidates
        assert reg.min_seeds >= 4
        assert reg.selection_rule == "simplest_defensible_v1"

    def test_test_dates_and_edges_are_deliberately_empty(self):
        """Outcome-blind selection has NOT been performed."""
        reg = ug.build_default_registration()
        assert reg.test_dates == ()
        assert reg.test_edges == ()
        assert "outcome-blind" in reg.outcome_blind_note
        assert "void the pre-registration" in reg.outcome_blind_note

    def test_it_binds_its_own_regression_tests(self):
        """The v7 lesson: fingerprints omitting the tests are worthless."""
        reg = ug.build_default_registration()
        bound = set(reg.source_fingerprints)
        assert "tests/test_simulation_members.py" in bound
        assert "tests/test_uncertainty_policy.py" in bound
        assert "tests/test_dirsplit_schema.py" in bound

    def test_it_binds_the_implementation_modules(self):
        reg = ug.build_default_registration()
        bound = set(reg.source_fingerprints)
        for name in ("dirsplit/models.py", "dirsplit/scenarios.py",
                     "traffic_sim/core/simulation_members.py",
                     "traffic_sim/simulation/uncertainty_policy.py"):
            assert name in bound
            assert reg.source_fingerprints[name] is not None

    def test_the_frozen_artifact_is_present_and_unexecuted(self):
        """The registration in validation/ exists and has no outcome."""
        from pathlib import Path
        path = Path("validation/closure_uncertainty_shadow_registration_v1.json")
        if not path.is_file():
            pytest.skip("registration artifact not present in this checkout")
        status = ug.verify_registration(path)
        assert status["present"]
        assert status["valid"], status["reason"]
        outcome_path = Path(
            "validation/closure_uncertainty_shadow_outcome_v1.json")
        assert not outcome_path.exists(), (
            "an outcome artifact exists but the campaign was never run in "
            "this environment; outcomes must never be fabricated"
        )
