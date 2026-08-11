"""Phase 4: the daily-unit limit becomes a measured budget, not a constant.

The plan's final acceptance criterion is a six-month 360-hour search — 11,813
parents, 23,349 unique daily units — legal under the calendar contract and
rejected for one reason only: a hard-coded 10,000. Measured here, streaming
that whole enumeration costs 14.3 s and 25.6 MiB peak RSS, well inside the
64 MiB process gate. The enumeration was never memory-bound.

The danger in fixing that is raising the number and calling it solved, which
just moves the arbitrary threshold. These tests pin the three properties that
make a budget different from a bigger cap: it is declared and measured, it
PAUSES rather than raising or truncating, and what it produces can never be
mistaken for an exhaustive search.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from traffic_sim.simulation.unit_budget import (
    COMPLETE_STATUS,
    INCOMPLETE_STATUS,
    LEGACY_DAILY_UNIT_LIMIT,
    SIX_MONTH_DAILY_UNIT_BUDGET,
    BudgetState,
    DailyUnitBudget,
    describe,
    exceeded,
)

ROOT = Path(__file__).resolve().parents[1]


class TestTheDefaultChangesNothing:
    def test_the_default_budget_is_the_legacy_cap(self):
        """No existing search may change behaviour by accident."""
        assert DailyUnitBudget().maximum_daily_units == LEGACY_DAILY_UNIT_LIMIT
        assert LEGACY_DAILY_UNIT_LIMIT == 10_000

    def test_the_parent_protection_is_retained_unchanged(self):
        assert DailyUnitBudget().maximum_parent_schedules == 100_000

    def test_a_budget_must_be_positive_integers(self):
        for field in ("maximum_daily_units", "maximum_peak_rss_bytes",
                      "maximum_ledger_bytes", "maximum_parent_schedules"):
            with pytest.raises(ValueError, match=field):
                DailyUnitBudget(**{field: 0})

    def test_a_budget_round_trips_and_is_content_addressed(self):
        budget = DailyUnitBudget(maximum_daily_units=SIX_MONTH_DAILY_UNIT_BUDGET)
        assert DailyUnitBudget.from_dict(budget.to_dict()) == budget
        assert budget.content_key != DailyUnitBudget().content_key

    def test_an_unknown_schema_is_refused(self):
        payload = DailyUnitBudget().to_dict()
        payload["schema"] = "something_else"
        with pytest.raises(ValueError, match="schema is unsupported"):
            DailyUnitBudget.from_dict(payload)


class TestExceededNamesOneCauseDeterministically:
    def test_inside_every_line_is_none(self):
        assert exceeded(DailyUnitBudget(), daily_units=1, ledger_bytes=1,
                        peak_rss_bytes=1) is None

    def test_units_are_reported_first_when_two_lines_cross_together(self):
        """Two runs crossing the same two lines must blame the same one."""
        budget = DailyUnitBudget(maximum_daily_units=1,
                                 maximum_ledger_bytes=1)
        assert exceeded(budget, daily_units=2, ledger_bytes=2) == (
            "maximum_daily_units")

    def test_each_line_can_be_the_cause_on_its_own(self):
        budget = DailyUnitBudget(maximum_daily_units=10,
                                 maximum_ledger_bytes=10,
                                 maximum_peak_rss_bytes=10)
        assert exceeded(budget, daily_units=1, ledger_bytes=11) == (
            "maximum_ledger_bytes")
        assert exceeded(budget, daily_units=1, peak_rss_bytes=11) == (
            "maximum_peak_rss_bytes")


class TestAnIncompleteSearchCanNeverPassAsExhaustive:
    def test_the_status_is_not_a_variant_of_ready(self):
        assert INCOMPLETE_STATUS == "incomplete_budget_stopped"
        assert "ready" not in INCOMPLETE_STATUS
        assert INCOMPLETE_STATUS != COMPLETE_STATUS

    def test_a_stopped_state_reports_itself_incomplete(self):
        state = BudgetState(daily_units=10_001, parent_schedules=4_000,
                            status=INCOMPLETE_STATUS,
                            stopped_by="maximum_daily_units",
                            resume_after_parent_id="closure-abc")
        assert state.complete is False
        payload = state.to_dict()
        assert payload["complete"] is False
        assert payload["status"] == INCOMPLETE_STATUS
        assert payload["resume_after_parent_id"] == "closure-abc"

    def test_the_description_says_so_in_words(self):
        state = BudgetState(daily_units=10_001, parent_schedules=4_000,
                            status=INCOMPLETE_STATUS,
                            stopped_by="maximum_daily_units")
        text = describe(state, DailyUnitBudget())
        assert "INCOMPLETE" in text
        assert "not an exhaustive search" in text
        assert "10000" in text.replace(",", "")

    def test_a_complete_state_says_what_it_covered(self):
        state = BudgetState(daily_units=23_349, parent_schedules=11_813)
        assert state.complete is True
        text = describe(state, DailyUnitBudget(
            maximum_daily_units=SIX_MONTH_DAILY_UNIT_BUDGET))
        assert "23349" in text.replace(",", "")
        assert "INCOMPLETE" not in text


class TestTheSixMonthCaseIsNoLongerRejectedByAConstant:
    """The plan's final acceptance criterion, measured rather than asserted."""

    def _spec(self, case_id):
        from traffic_sim.core.contracts import ClosureSearchSpec

        baseline = json.loads(
            (ROOT / "validation" / "closure_search_scaling_baseline_v1.json")
            .read_text(encoding="utf-8"))
        case = [item for item in baseline["cases"]
                if item["case_id"] == case_id][0]
        return ClosureSearchSpec.from_dict(case["spec"])

    def _enumerate(self, spec):
        from traffic_sim.core.closure_calendar import iter_closure_schedules
        from traffic_sim.simulation.independent_daily import daily_unit_records

        parents = 0
        units: set[str] = set()
        for schedule in iter_closure_schedules(spec):
            parents += 1
            for unit_id, _identity, _build in daily_unit_records(spec, schedule):
                units.add(unit_id)
        return parents, units

    def test_the_360_hour_case_fits_a_declared_budget(self):
        parents, units = self._enumerate(self._spec("six-month-360h"))
        assert (parents, len(units)) == (11_813, 23_349), (
            "the plan's frozen numbers must still reproduce")
        legacy = DailyUnitBudget()
        assert exceeded(legacy, daily_units=len(units)) == (
            "maximum_daily_units"), "the legacy cap must still refuse it"
        declared = DailyUnitBudget(
            maximum_daily_units=SIX_MONTH_DAILY_UNIT_BUDGET)
        assert exceeded(declared, daily_units=len(units)) is None, (
            "a declared budget that admits the case must admit it")

    def test_the_720_hour_case_remains_valid_under_the_legacy_budget(self):
        parents, units = self._enumerate(self._spec("six-month-720h"))
        assert (parents, len(units)) == (2_186, 5_676)
        assert exceeded(DailyUnitBudget(), daily_units=len(units)) is None

    # The 64 MiB process gate is measured in a FRESH interpreter by
    # tests/test_unit_budget_integration.py. Asserting it here would measure
    # pytest's own imports, which is why this file once allowed 4x headroom —
    # a ceiling of 256 MiB does not test a 64 MiB gate.

    def test_semantic_unit_ids_are_stable_across_two_enumerations(self):
        """A budget must not change WHICH units a search would produce."""
        spec = self._spec("brute-force-multi-day")
        first_parents, first_units = self._enumerate(spec)
        second_parents, second_units = self._enumerate(spec)
        assert first_parents == second_parents
        assert first_units == second_units
