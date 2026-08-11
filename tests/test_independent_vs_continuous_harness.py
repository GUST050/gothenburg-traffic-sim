"""Stage 4: the harness must answer the frozen question without editing it.

Three things can go wrong with a measurement run against a pre-registration,
and none of them is a crash: the registration gets "corrected" to match what
happened, a case that could not run gets folded in with cases that merely
found nothing, or two arms get compared over candidates that are not the same
closure. Each is pinned here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.measure_independent_vs_continuous as harness

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = (
    ROOT / "validation" / "independent_vs_continuous_preregistration_v1.json")
# v1 is preserved on disk as the four-bucket historical record; the CURRENT
# outcome is v2, which adds `different_candidate_spaces` as its own category.
OUTCOME_V1 = ROOT / "validation" / "independent_vs_continuous_outcome_v1.json"
OUTCOME = ROOT / "validation" / "independent_vs_continuous_outcome_v2.json"


@pytest.fixture(scope="module")
def registration() -> dict:
    return harness.load_registration(REGISTRATION)


@pytest.fixture(scope="module")
def outcome() -> dict:
    return json.loads(OUTCOME.read_text(encoding="utf-8"))


def _case(registration: dict, case_id: str) -> dict:
    for case in registration["cases"]:
        if case["case_id"] == case_id:
            return case
    raise AssertionError(f"no such case: {case_id}")


class TestTheRegistrationIsReadOnly:
    def test_a_full_classification_pass_leaves_it_byte_identical(self, tmp_path):
        before = REGISTRATION.read_bytes()
        harness.main(["--classify-only", "--out", str(tmp_path / "out.json"),
                      "--runs-root", str(tmp_path)])
        assert REGISTRATION.read_bytes() == before

    def test_it_refuses_a_registration_that_is_not_frozen(self, tmp_path):
        payload = json.loads(REGISTRATION.read_text(encoding="utf-8"))
        payload["status"] = "amended_after_outcome"
        path = tmp_path / "thawed.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SystemExit, match="not frozen"):
            harness.load_registration(path)

    def test_it_refuses_a_spec_that_no_longer_rebuilds_to_its_key(
            self, registration):
        case = dict(_case(
            registration,
            "ivc-01d-weekdays-only-morning-central-high-demand-measured-2025"))
        drifted = dict(case["independent_spec"])
        drifted["content_key"] = "0" * 20
        case["independent_spec"] = drifted
        with pytest.raises(SystemExit, match="content key"):
            harness._spec_from_case(case, "independent_spec")

    def test_the_outcome_is_a_separate_record_naming_the_registration(
            self, outcome, registration):
        assert outcome["schema"] != registration["schema"]
        assert outcome["registration"]["input_content_key"] == (
            registration["input_content_key"])


class TestTheFourBucketsStaySeparate:
    def test_every_examined_case_lands_in_exactly_one_bucket(self, outcome):
        total = sum(outcome["bucket_counts"].values())
        assert total == outcome["examined_case_count"]
        assert total == outcome["registration"]["case_count"]
        seen = [item for value in outcome["buckets"].values() for item in value]
        assert len(seen) == len(set(seen))

    def test_a_measurable_case_with_no_measurement_is_blocked_not_measured(
            self, registration):
        """The one place a "0 saved" could quietly become a "0 found"."""
        built = harness.build_outcome(
            registration,
            [{"case_id": "a", "bucket": "measurable", "workday_count": 1},
             {"case_id": "b", "bucket": "measurable", "workday_count": 1}],
            runs_root=ROOT / "runs",
            measured={"a": {"comparison": {}}})
        assert built["buckets"]["measured"] == ["a"]
        assert built["buckets"]["blocked_missing_demand"] == ["b"]
        assert sum(built["bucket_counts"].values()) == 2

    def test_beyond_the_contract_ceiling_is_unsupported_not_blocked(
            self, outcome, registration):
        ceiling = registration["continuous_max_workdays"]
        for record in outcome["cases"]:
            if record["workday_count"] > ceiling:
                assert record["bucket"] == "unsupported_by_contract", (
                    "a case with no continuous counterfactual has nothing to "
                    "measure; it is not merely waiting for demand")
        assert outcome["bucket_counts"]["unsupported_by_contract"] > 0

    def test_an_overnight_band_is_unpairable_not_unsupported(self, outcome):
        overnight = [record for record in outcome["cases"]
                     if record["time_band"] == "overnight"
                     and record["workday_count"]
                     <= harness.load_registration(
                         REGISTRATION)["continuous_max_workdays"]]
        assert overnight
        for record in overnight:
            assert record["bucket"] == "unpairable"
            assert any("same-day" in reason
                       for reason in record["contract_reasons"])

    def test_blocked_names_the_archives_the_product_itself_looked_for(
            self, outcome):
        blocked = [record for record in outcome["cases"]
                   if record["bucket"] == "blocked_missing_demand"]
        if not blocked:
            pytest.skip("this environment has the calibrated archive library")
        for record in blocked:
            for arm in ("independent", "continuous"):
                report = record["demand"]["arms"][arm]
                assert report["required_build_count"] > 0
                assert report["missing_build_count"] > 0


class TestTheFifthCategory:
    """v2 reports `different_candidate_spaces` separately.

    In v1 it was a field on the record, so a case whose two arms search
    different spaces could be counted among the measured and its agreement read
    as agreement about the decision. It is a distinct kind of answer and now
    has a distinct name.
    """

    def test_every_case_carries_a_pairing_verdict(self, outcome):
        for record in outcome["cases"]:
            assert record["pairing"] in harness.PAIRINGS, record["case_id"]

    def test_the_pairing_counts_account_for_every_case(self, outcome):
        assert sum(outcome["pairing_counts"].values()) == (
            outcome["examined_case_count"])

    def test_a_blocked_case_still_reports_whether_its_spaces_differ(self,
                                                                    outcome):
        """The contract finding does not wait for demand to arrive."""
        blocked = [record for record in outcome["cases"]
                   if record["bucket"] == "blocked_missing_demand"]
        if not blocked:
            pytest.skip("nothing is blocked in this environment")
        assert all(record["pairing"] in {"identical_candidate_spaces",
                                         "different_candidate_spaces"}
                   for record in blocked)
        assert outcome["pairing_counts"]["different_candidate_spaces"] > 0, (
            "the differing-space finding must survive a fully blocked run")

    def test_a_measured_case_whose_spaces_differ_is_not_counted_as_measured(
            self, registration):
        built = harness.build_outcome(
            registration,
            [{"case_id": "clean", "bucket": "measurable", "workday_count": 1,
              "pairing": "identical_candidate_spaces"},
             {"case_id": "split", "bucket": "measurable", "workday_count": 1,
              "pairing": "different_candidate_spaces"}],
            runs_root=ROOT / "runs",
            measured={"clean": {"comparison": {}}, "split": {"comparison": {}}})
        assert built["buckets"]["measured"] == ["clean"]
        assert built["buckets"]["different_candidate_spaces"] == ["split"]

    def test_unpairable_and_unsupported_keep_their_own_pairing_names(self,
                                                                     outcome):
        for record in outcome["cases"]:
            if record["bucket"] in {"unpairable", "unsupported_by_contract"}:
                assert record["pairing"] == record["bucket"]


class TestCandidateCorrespondence:
    def test_identical_calendars_pair_every_candidate(self, registration):
        report = harness.candidate_correspondence(_case(
            registration,
            "ivc-01d-weekdays-only-morning-central-high-demand-measured-2025"))
        assert report["candidate_space_identical"] is True
        assert report["paired_candidate_count"] == (
            report["independent_candidate_count"])
        assert report["independent_only_count"] == 0
        assert report["continuous_only_count"] == 0

    def test_a_first_schedule_that_pairs_does_not_prove_the_spaces_match(
            self, registration):
        """The finding this harness exists to catch.

        The pre-registration declared these cases pairable on their FIRST
        schedule. They are not the same search.
        """
        report = harness.candidate_correspondence(_case(
            registration,
            "ivc-21d-all-days-midday-central-high-demand-forecast-2027"))
        assert report["candidate_space_identical"] is False
        assert report["continuous_only_count"] > 0
        profile = report["profile"]
        # Continuous rounds the daily shift UP, so it can serve the same work
        # requirement in FEWER days — schedules exact-equal daily allocation
        # cannot express at all.
        assert profile["independent"]["day_count_histogram"] == {"21": 150}
        assert set(profile["continuous"]["day_count_histogram"]) > {"21"}
        assert max(profile["continuous"]["scheduled_work_minutes"]) > (
            profile["continuous"]["required_work_minutes"])

    def test_a_weekend_straddle_exists_only_on_the_independent_axis(
            self, registration):
        report = harness.candidate_correspondence(_case(
            registration,
            "ivc-03d-weekdays-only-morning-central-high-demand-measured-2025"))
        assert report["independent_only_count"] > 0
        assert report["continuous_only_count"] == 0


class TestTheComparisonCannotOverclaim:
    def _correspondence(self, identical=True):
        pairs = [
            {"shape": {"work_dates": ["2025-09-01"], "daily_start": "06:00",
                       "daily_end": "10:00"},
             "independent_id": "i-1", "continuous_id": "c-1"},
            {"shape": {"work_dates": ["2025-09-02"], "daily_start": "06:00",
                       "daily_end": "10:00"},
             "independent_id": "i-2", "continuous_id": "c-2"},
        ]
        return {"pairs": pairs, "paired_candidate_count": len(pairs),
                "candidate_space_identical": identical}

    def _arm(self, ids, costs, *, winner, teleports=0, queue=10):
        return {
            "arm": "x", "wall_time_s": 1.0,
            "result": {"winner_id": winner, "tie_ids": [], "status": "ready",
                       "pilot_selection": {"candidates": [
                           {"candidate_id": item, "eligible": True,
                            "hard_failures": [],
                            "worst_variant_delta_s": 1.0,
                            "closure_cost": {
                                "added_vehicle_hours": cost,
                                "added_metres_total": cost * 1000,
                                "vehicles_affected": int(cost * 10),
                                "vehicles_no_detour": 0}}
                           for item, cost in zip(ids, costs)]}},
            "canonical_observations": [
                {"schedule_id": item,
                 "health": {"candidate": {"teleport_total": teleports}},
                 "candidate_metrics": {"max_queue_vehicles": queue,
                                       "waiting_at_end": 0},
                 "recovery": {"status": "recovered", "recovered_at_s": 100}}
                for item in ids
            ],
        }

    def _tolerances(self):
        return harness.load_registration(REGISTRATION)["tolerances"]

    def test_agreeing_arms_over_an_identical_space_are_low_risk(self):
        comparison = harness.compare_arms(
            {"case_id": "x"}, self._correspondence(),
            self._arm(["i-1", "i-2"], [1.0, 2.0], winner="i-1"),
            self._arm(["c-1", "c-2"], [1.0, 2.0], winner="c-1"),
            self._tolerances())
        assert comparison["risk_class"] == "low"
        assert comparison["ranking"]["material_rank_flip"] is False
        assert comparison["winner_and_ties"]["winner_agrees"] is True

    def test_a_rank_flip_raises_the_risk_class(self):
        comparison = harness.compare_arms(
            {"case_id": "x"}, self._correspondence(),
            self._arm(["i-1", "i-2"], [1.0, 2.0], winner="i-1"),
            self._arm(["c-1", "c-2"], [2.0, 1.0], winner="c-2"),
            self._tolerances())
        assert comparison["ranking"]["material_rank_flip"] is True
        assert comparison["risk_class"] == "high"

    def test_a_metric_outside_its_frozen_tolerance_raises_the_risk_class(self):
        comparison = harness.compare_arms(
            {"case_id": "x"}, self._correspondence(),
            self._arm(["i-1", "i-2"], [1.0, 2.0], winner="i-1"),
            self._arm(["c-1", "c-2"], [1.0, 2.0], winner="c-1", teleports=9),
            self._tolerances())
        assert comparison["tolerance_breaches"]
        assert comparison["risk_class"] == "high"

    def test_a_differing_candidate_space_can_never_be_low_risk(self):
        """Agreement over the shared subset says nothing about the rest."""
        comparison = harness.compare_arms(
            {"case_id": "x"}, self._correspondence(identical=False),
            self._arm(["i-1", "i-2"], [1.0, 2.0], winner="i-1"),
            self._arm(["c-1", "c-2"], [1.0, 2.0], winner="c-1"),
            self._tolerances())
        assert comparison["ranking"]["material_rank_flip"] is False
        assert comparison["candidate_space_identical"] is False
        assert comparison["risk_class"] == "high"

    def test_a_winner_the_other_arm_cannot_express_is_not_agreement(self):
        comparison = harness.compare_arms(
            {"case_id": "x"}, self._correspondence(),
            self._arm(["i-1", "i-2"], [1.0, 2.0], winner="i-1"),
            self._arm(["c-1", "c-2"], [1.0, 2.0], winner="c-99"),
            self._tolerances())
        assert comparison["winner_and_ties"]["winner_outside_paired_set"] is True
        assert comparison["winner_and_ties"]["winner_agrees"] is not True
        assert comparison["risk_class"] == "high"

    def test_every_pre_registered_metric_appears_in_the_comparison(self):
        comparison = harness.compare_arms(
            {"case_id": "x"}, self._correspondence(),
            self._arm(["i-1", "i-2"], [1.0, 2.0], winner="i-1"),
            self._arm(["c-1", "c-2"], [1.0, 2.0], winner="c-1"),
            self._tolerances())
        reported = set(comparison["per_candidate"][0]["metrics"])
        registration = harness.load_registration(REGISTRATION)
        for metric in registration["compared_metrics"]:
            if metric in ("ranking", "winner_and_ties"):
                assert metric in comparison
            else:
                assert metric in reported, metric


class TestTheOutcomeLicensesNothing:
    def test_it_activates_no_policy_and_permits_no_ui_claim(self, outcome):
        boundary = outcome["claim_boundary"]
        assert boundary["activates_policy_v3"] is False
        assert boundary["opens_global_best"] is False
        assert boundary["permits_ui_claim"] is False
        assert outcome["release_evidence"] is False

    def test_it_refuses_to_extrapolate_past_what_it_measured(self, outcome):
        assert outcome["claim_boundary"][
            "licenses_extrapolation_beyond_measured_workdays"] is False
        assert "does NOT license" in outcome["extrapolation_rule"]

    def test_the_frozen_tolerances_travel_with_it_unchanged(self, outcome,
                                                            registration):
        assert outcome["tolerances"] == registration["tolerances"]

    def test_it_will_not_silently_replace_an_existing_outcome(self, tmp_path):
        destination = tmp_path / "outcome.json"
        harness.main(["--classify-only", "--out", str(destination),
                      "--runs-root", str(tmp_path)])
        with pytest.raises(SystemExit, match="already exists"):
            harness.main(["--classify-only", "--out", str(destination),
                          "--runs-root", str(tmp_path)])
