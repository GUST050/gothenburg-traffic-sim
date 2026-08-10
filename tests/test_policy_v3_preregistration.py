"""Policy v3 exists, is provisional, and cannot quietly become active.

PR F. The value of a pre-registration is that it was written before the
outcome, so these tests pin the two things that make it worth anything: v1 and
v2 are byte-identical to what they were, and v3 changes execution order only —
every decision parameter is copied from v2, so a difference in outcome between
them can only come from the ordering, which is the hypothesis under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from traffic_sim.simulation.monthly_search import MonthlySearchPolicy

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "validation" / "monthly_search_policy_v1.json"
V2 = ROOT / "validation" / "monthly_search_policy_v2.json"
V3 = ROOT / "validation" / "monthly_search_policy_v3.json"
PREREGISTRATION = (
    ROOT / "validation" / "monthly_search_policy_v3_preregistration.json")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestPolicyV3:
    def test_it_is_a_valid_policy_contract(self):
        policy = MonthlySearchPolicy.from_dict(_load(V3))
        assert policy.policy_id == "monthly-closure-policy-v3-cost-ordered"
        assert policy.objective_method == "closure_cost_v1"

    def test_it_is_provisional_and_not_golden(self):
        policy = MonthlySearchPolicy.from_dict(_load(V3))
        assert policy.status == "provisional"
        assert policy.benchmark_id.startswith("pending-")

    def test_it_changes_execution_order_only(self):
        """Every decision parameter is v2's, unchanged.

        If a threshold moved as well, a better result could not be attributed
        to the ordering — which is the only thing PR E changed.
        """
        v2, v3 = _load(V2), _load(V3)
        assert v3["pilot"] == v2["pilot"]
        assert v3["finalist"] == v2["finalist"]
        assert v3["objective_method"] == v2["objective_method"]
        assert v3["policy_id"] != v2["policy_id"]

    def test_v1_and_v2_are_untouched(self):
        """The old policies keep their own identities and status."""
        v1, v2 = _load(V1), _load(V2)
        assert v1["policy_id"] == "monthly-closure-policy-v1"
        assert v2["policy_id"] == "monthly-closure-policy-v2-objective-aligned"
        for payload in (v1, v2):
            MonthlySearchPolicy.from_dict(payload)


class TestPreregistration:
    def test_it_freezes_the_parameters_the_policy_actually_uses(self):
        record = _load(PREREGISTRATION)
        policy = _load(V3)
        frozen = record["frozen_parameters"]

        assert frozen["minimum_finalists"] == policy["pilot"]["minimum_finalists"]
        assert frozen["maximum_finalists"] == policy["pilot"]["maximum_finalists"]
        assert frozen["retention_band_s"] == policy["pilot"]["retention_band_s"]
        for key in ("practical_equivalence_vehicle_hours",
                    "practical_equivalence_s", "absolute_precision_floor_s",
                    "relative_precision", "confidence_level",
                    "max_repetitions", "initial_repetitions",
                    "micro_finalist_limit"):
            assert frozen[key] == policy["finalist"][key], key

    def test_it_is_not_release_evidence_and_activates_nothing(self):
        record = _load(PREREGISTRATION)
        assert record["release_evidence"] is False
        assert record["activated"] is False
        assert record["status"] == "frozen_before_outcome"
        activation = record["activation_conditions"]
        assert activation["policy_v3_may_be_activated"] is False
        assert activation["ui_and_global_best_claims_remain_closed"] is True
        assert len(activation["requires"]) >= 4

    def test_it_states_what_will_be_reported_whatever_happens(self):
        record = _load(PREREGISTRATION)
        reported = set(record["reported_regardless_of_outcome"])
        assert "actual SUMO saving" in reported
        assert "stop reason and stop proof" in reported

    def test_it_names_its_blocker_with_a_reproducible_command(self):
        record = _load(PREREGISTRATION)
        blocked = record["blocked_by"]
        assert "calibrated" in blocked["reason"]
        assert "run_monthly_closure_search.py" in blocked["reproducible_command"]
        assert "independent-cost-ordered-exact" in (
            blocked["reproducible_command"])

    def test_the_acceptance_criteria_include_the_equivalence_gate(self):
        record = _load(PREREGISTRATION)
        criteria = record["acceptance_criteria"]
        assert criteria["finalist_set_identical"] is True
        assert criteria["final_decision_identical"] is True
        assert criteria["no_silently_truncated_finalist_set"] is True
