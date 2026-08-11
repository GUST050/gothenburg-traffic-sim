"""Stage 2: the benchmark must be selected before the outcome, or it is worth nothing.

The failure this file guards against is not a bug — it is a temptation. A
benchmark selected after seeing which case makes the new path look good proves
nothing, and a threshold moved after the run proves less. So the tests pin the
two properties that make the registration meaningful: selection consults only
structural facts, and the run writes a SEPARATE record rather than editing the
registration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.cost_ordered_benchmark as bench

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "validation" / "cost_ordered_benchmark_registration_v1.json"


@pytest.fixture(scope="module")
def registration() -> dict:
    return json.loads(REGISTRATION.read_text(encoding="utf-8"))


class TestSelectionIsStructural:
    def test_selection_never_runs_a_search_or_prices_a_candidate(
            self, monkeypatch, tmp_path):
        """The selector must not be able to see an outcome, even by accident."""
        from traffic_sim.simulation import deterministic_disruption as dd

        def refuse(*args, **kwargs):
            raise AssertionError("selection consulted a cost or an outcome")

        monkeypatch.setattr(dd, "parent_closure_cost", refuse)
        monkeypatch.setattr(dd.ArchiveDisruptionProvider, "disruption", refuse)

        selection = bench.select_case(tmp_path)
        assert selection["selected"] is None
        assert "no outcome" in selection["selection_rule"]

    def test_it_evaluates_every_case_on_knowable_properties(self, tmp_path):
        selection = bench.select_case(tmp_path)
        assert len(selection["evaluated"]) >= 4
        for item in selection["evaluated"]:
            assert item["candidate_count"] > 0
            assert item["unique_daily_unit_count"] > 0
            assert isinstance(item["work_dates"], list)
            # Nothing outcome-shaped may appear.
            assert "winner" not in item
            assert "cost" not in item
            assert "selected_ids" not in item

    def test_the_cases_are_large_enough_to_discriminate(self, tmp_path):
        """A case with too few candidates could not fail the gate."""
        selection = bench.select_case(tmp_path)
        for item in selection["evaluated"]:
            assert item["candidate_count"] >= bench.MINIMUM_STRUCTURAL_CANDIDATES

    def test_an_archive_missing_any_variant_is_not_counted(self, tmp_path):
        archive = tmp_path / "demand-partial"
        archive.mkdir()
        (archive / "demand_meta.json").write_text(json.dumps({
            "demand_build_key": "k", "epoch_sim": "2025-03-03T00:00:00",
            "n_intervals": 96, "n_variants": 3,
        }), encoding="utf-8")
        for filename in ("calibrated.rou.xml", "calibrated_v1.rou.xml"):
            (archive / filename).write_text("<routes/>", encoding="utf-8")

        assert bench._archive_index(tmp_path) == {}, (
            "a two-variant archive is not the case the policy describes")

    def test_a_complete_archive_is_indexed_with_its_digests(self, tmp_path):
        archive = tmp_path / "demand-complete"
        archive.mkdir()
        (archive / "demand_meta.json").write_text(json.dumps({
            "demand_build_key": "key-1", "epoch_sim": "2025-03-03T00:00:00",
            "n_intervals": 96, "n_variants": 3,
        }), encoding="utf-8")
        for filename in bench.VARIANT_FILENAMES.values():
            (archive / filename).write_text(f"<routes id='{filename}'/>",
                                            encoding="utf-8")

        index = bench._archive_index(tmp_path)
        assert set(index) == {"key-1"}
        routes = index["key-1"]["routes"]
        assert set(routes) == {"q10", "q50", "q90"}
        digests = {item["sha256"] for item in routes.values()}
        assert len(digests) == 3, "each variant must be bound separately"


class TestTheRegistration:
    def test_it_is_frozen_and_activates_nothing(self, registration):
        assert registration["evidence_class"] == "preregistration"
        assert registration["release_evidence"] is False
        boundary = registration["claim_boundary"]
        assert boundary["activates_policy_v3"] is False
        assert boundary["opens_global_best"] is False
        assert boundary["permits_ui_claim"] is False

    def test_it_binds_every_input_the_stage_requires(self, registration):
        for key in ("policies", "sources", "network", "network_metadata",
                    "demand_variants", "resource_caps", "seeds",
                    "output_roots", "comparison_metrics", "gate_thresholds"):
            assert key in registration, key
        assert set(registration["policies"]) == {"exhaustive", "cost_ordered"}
        assert registration["network"]["sha256"]
        assert registration["resource_caps"]["maximum_daily_units"] == 10_000

    def test_the_thresholds_are_fixed_in_advance(self, registration):
        thresholds = registration["gate_thresholds"]
        assert thresholds["sumo_verifications_saved_minimum"] >= 1
        assert thresholds["selected_ids_identical"] is True
        assert thresholds["final_decision_identical"] is True
        assert thresholds["resource_cap_regression_allowed"] is False

    def test_its_content_key_still_describes_its_own_body(self, registration):
        """v1 is FROZEN historical evidence, so it is checked against itself.

        It binds source digests, and those sources have since moved — the
        stage-1 review changed `monthly_search.py`. Requiring a frozen record to
        recompute from a moving tree would force a choice between editing
        history and a permanently red test. Self-consistency is the property
        that actually matters for a preserved artifact; "recomputes from the
        tool" belongs to the CURRENT registration, where it is meaningful.
        """
        body = {key: value for key, value in registration.items()
                if key not in {"content_key", "registered_at"}}
        assert registration["content_key"] == bench._content_key(body)

    def test_it_reports_its_blocker_honestly(self, registration):
        """This environment has no calibrated archives; say so, do not guess."""
        if registration["selected_case"] is not None:
            pytest.skip("a case was selectable in this environment")
        assert registration["status"] == "blocked_no_structurally_eligible_case"
        blocked = registration["blocked_by"]
        assert blocked["archives_available"] == 0
        assert "cost_ordered_benchmark.py --preregister" in (
            blocked["reproducible_command"])

    def test_the_tool_refuses_to_silently_overwrite(self, tmp_path):
        destination = tmp_path / "registration.json"
        bench.main(["--preregister", "--registration", str(destination),
                    "--runs-root", str(tmp_path)])
        with pytest.raises(SystemExit, match="frozen"):
            bench.main(["--preregister", "--registration", str(destination),
                        "--runs-root", str(tmp_path)])

    def test_a_run_without_a_selected_case_refuses(self, tmp_path):
        destination = tmp_path / "registration.json"
        bench.main(["--preregister", "--registration", str(destination),
                    "--runs-root", str(tmp_path)])
        with pytest.raises(SystemExit, match="nothing to run"):
            bench.main(["--run", "--registration", str(destination)])


class TestTheOutcomeIsSeparate:
    def _comparison(self, **overrides):
        comparison = {
            "candidate_costs_field_identical": True,
            "hard_failures_identical": True,
            "health_classifications_identical": True,
            "status_identical": True,
            "selected_ids_identical": True,
            "final_decision_identical": True,
            "sumo_verifications_saved": 7,
            "stop_proof_valid": True,
            "cache_hits_consistent": True,
            "restart_equivalent": True,
            "no_resource_cap_regression": True,
        }
        comparison.update(overrides)
        return comparison

    def test_a_passing_outcome_still_does_not_activate_policy_v3(
            self, registration):
        outcome = bench.build_outcome(
            registration, self._comparison(), status="measured")
        assert outcome["gates"]["passed"] is True
        assert outcome["claim_boundary"]["activates_policy_v3"] is False, (
            "a benchmark alone must not activate the policy")
        assert outcome["claim_boundary"]["opens_global_best"] is False
        assert "held-out" in outcome["claim_boundary"]["reason"]

    def test_zero_saving_fails_the_gate(self, registration):
        outcome = bench.build_outcome(
            registration, self._comparison(sumo_verifications_saved=0),
            status="measured")
        assert outcome["gates"]["checks"]["sumo_verifications_saved"] is False
        assert outcome["gates"]["passed"] is False

    def test_a_different_selected_set_fails_the_gate(self, registration):
        outcome = bench.build_outcome(
            registration, self._comparison(selected_ids_identical=False),
            status="measured")
        assert outcome["gates"]["passed"] is False

    def test_an_inconclusive_run_is_recorded_as_it_happened(self,
                                                            registration):
        outcome = bench.build_outcome(
            registration,
            self._comparison(sumo_verifications_saved=0,
                             note="only one health-viable candidate"),
            status="inconclusive")
        assert outcome["status"] == "inconclusive"
        assert outcome["gates"]["passed"] is False
        assert outcome["comparison"]["note"]

    def test_the_outcome_names_its_registration_without_editing_it(
            self, registration):
        before = json.dumps(registration, sort_keys=True)
        outcome = bench.build_outcome(
            registration, self._comparison(), status="measured")
        assert json.dumps(registration, sort_keys=True) == before
        assert outcome["registration"]["content_key"] == (
            registration["content_key"])
        assert outcome["schema"] != registration["schema"]

    def test_the_thresholds_travel_with_the_outcome(self, registration):
        outcome = bench.build_outcome(
            registration, self._comparison(), status="measured")
        assert outcome["gates"]["thresholds"] == bench.GATE_THRESHOLDS
