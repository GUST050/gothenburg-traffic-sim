"""PR H's pre-registration must be frozen, honest and self-refuting.

A pre-registration is only worth something if it was written before the result
and cannot be quietly widened afterwards. These tests pin that: the tolerances
exist and are numbers, the cases are real contracts, an unpairable case says
why, and the record does not claim to have measured anything.

The most useful thing the record found is a CONTRACT fact, not a traffic one:
`ClosureSearchSpec` refuses a continuous closure longer than 21 workdays, so
above that there is no counterfactual to compare against at all. That is
recorded, and it is why the plan's "do not extrapolate 21 days to 90" rule has
teeth.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from traffic_sim.core.contracts import ClosureSearchSpec

import tools.preregister_independent_vs_continuous as prereg

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "validation" / "independent_vs_continuous_preregistration_v1.json"


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


class TestTheRecordIsFrozenAndDiagnostic:
    def test_it_measures_nothing_and_opens_no_gate(self, record):
        assert record["evidence_class"] == "preregistration"
        assert record["release_evidence"] is False
        assert record["status"] == "frozen_before_outcome"
        assert record["claim_boundary"]["opens_no_gate"] is True

    def test_it_recomputes_from_the_tool(self, record):
        rebuilt = prereg.build_record()
        assert rebuilt["input_content_key"] == record["input_content_key"]
        assert rebuilt["case_count"] == record["case_count"]
        assert rebuilt["pairable_case_count"] == record["pairable_case_count"]

    def test_the_tool_refuses_to_overwrite_a_frozen_record(self, tmp_path):
        destination = tmp_path / "record.json"
        prereg.main(["--out", str(destination)])
        with pytest.raises(SystemExit, match="frozen"):
            prereg.main(["--out", str(destination)])

    def test_every_tolerance_is_a_number_fixed_in_advance(self, record):
        tolerances = record["tolerances"]
        for key, value in tolerances.items():
            if key == "note":
                continue
            assert isinstance(value, (int, float)), key
        assert tolerances["vehicles_no_detour_absolute"] == 0
        assert tolerances["teleports_absolute"] == 0

    def test_it_compares_everything_the_plan_names(self, record):
        compared = set(record["compared_metrics"])
        for metric in ("ranking", "added_vehicle_hours", "queue_and_halting",
                       "recovery", "teleports", "vehicles_no_detour",
                       "winner_and_ties"):
            assert metric in compared, metric

    def test_both_risk_classes_are_defined_before_the_outcome(self, record):
        assert set(record["risk_classes"]) == {"low", "high"}
        assert "withheld" in record["risk_classes"]["high"]


class TestTheCaseSet:
    def test_it_covers_the_plans_workday_counts(self, record):
        counts = set(record["workday_counts"])
        assert {1, 3, 7, 14, 21} <= counts
        assert max(counts) == 90

    def test_it_varies_road_time_band_and_demand(self, record):
        cases = record["cases"]
        assert len({case["road"] for case in cases}) >= 2
        assert len({case["time_band"] for case in cases}) >= 3
        assert len({case["demand_level"] for case in cases}) == 2

    def test_every_spec_it_publishes_is_a_valid_contract(self, record):
        checked = 0
        for case in record["cases"]:
            for key in ("independent_spec", "continuous_spec"):
                if key in case:
                    ClosureSearchSpec.from_dict(dict(case[key]))
                    checked += 1
        assert checked > 50

    def test_a_pairable_case_really_is_the_same_closure(self, record):
        pairable = [case for case in record["cases"] if case["pairable"]]
        assert pairable
        for case in pairable:
            paired = case["paired_schedule"]
            assert len(paired["work_dates"]) == case["workday_count"]
            assert paired["daily_start"] < paired["daily_end"]
            assert case["unpairable_reasons"] == []

    def test_an_unpairable_case_says_why(self, record):
        unpairable = [case for case in record["cases"]
                      if not case["pairable"]]
        assert unpairable
        for case in unpairable:
            assert case["unpairable_reasons"], case["case_id"]


class TestTheContractCeiling:
    def test_above_21_workdays_there_is_no_continuous_arm(self, record):
        assert record["continuous_max_workdays"] == 21
        long_cases = [case for case in record["cases"]
                      if case["workday_count"] > 21]
        assert long_cases
        for case in long_cases:
            assert case["continuous_expressible"] is False
            assert case["pairable"] is False
            assert "continuous_spec" not in case

    def test_the_finding_is_stated_as_a_contract_fact(self, record):
        finding = record["contract_finding"]
        assert "by contract" in finding
        assert "not for want of" in finding

    def test_extrapolation_is_refused_in_writing(self, record):
        rule = record["extrapolation_rule"]
        assert "does NOT license" in rule
        assert "90" in rule and "21" in rule

    def test_an_overnight_band_cannot_be_paired(self, record):
        overnight = [case for case in record["cases"]
                     if case["time_band"] == "overnight"]
        assert overnight
        assert all(case["pairable"] is False for case in overnight)


class TestItNamesItsBlocker:
    def test_it_says_what_is_missing_and_how_to_get_it(self, record):
        blocked = record["blocked_by"]
        assert "calibrated" in blocked["reason"]
        assert "make demand" in blocked["reproducible_command"]
        assert "run_monthly_closure_search.py" in blocked["reproducible_command"]

    def test_it_does_not_claim_a_measured_result(self, record):
        text = json.dumps(record)
        assert "rank_flips_observed" not in text
        assert "risk_class_assigned" not in text
        assert record.get("results") is None
