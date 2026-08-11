"""The budget must live in the REAL search path, not beside it.

The first cut of `unit_budget.py` was a module with tests and no callers — the
scaffolding the plan explicitly warns against. These tests drive the product's
own screening builder and preflight, so a budget that stopped being consulted
would fail here rather than pass quietly.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from run_monthly_closure_search import (
    _independent_exhaustive_builder,
    _independent_exhaustive_preflight,
)
from traffic_sim.core.contracts import ClosureSearchSpec
from traffic_sim.simulation.unit_budget import (
    DailyUnitBudget,
    INCOMPLETE_STATUS,
    SIX_MONTH_DAILY_UNIT_BUDGET,
)

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "validation" / "closure_search_scaling_baseline_v1.json"


def _case(case_id: str) -> dict:
    cases = json.loads(BASELINE.read_text(encoding="utf-8"))["cases"]
    return [item for item in cases if item["case_id"] == case_id][0]["spec"]


def _spec_file(tmp_path: Path, case_id: str) -> Path:
    path = tmp_path / f"{case_id}.json"
    path.write_text(json.dumps(_case(case_id)), encoding="utf-8")
    return path


class TestTheLegacyBehaviourIsUntouched:
    def test_without_a_budget_the_cap_still_refuses(self, tmp_path):
        """Every existing search must behave exactly as before."""
        with pytest.raises(ValueError, match="above the explicit cap"):
            _independent_exhaustive_builder(
                _spec_file(tmp_path, "brute-force-multi-day"),
                maximum_candidates=1000, maximum_daily_units=2,
                baseline_trip_duration_p99_s=3600)

    def test_a_complete_run_is_still_labelled_exhaustive(self, tmp_path):
        payload = _independent_exhaustive_builder(
            _spec_file(tmp_path, "brute-force-multi-day"),
            maximum_candidates=1000, maximum_daily_units=10_000,
            baseline_trip_duration_p99_s=3600,
            budget=DailyUnitBudget(maximum_daily_units=10_000))
        assert payload["proxy_version"] == (
            "independent_daily_exhaustive_sumo_v1")
        assert payload["exhaustive"] is True
        assert payload["budget_state"]["complete"] is True


class TestABudgetPausesInsteadOfRefusing:
    def test_the_product_builder_pauses_with_a_resume_cursor(self, tmp_path):
        payload = _independent_exhaustive_builder(
            _spec_file(tmp_path, "brute-force-multi-day"),
            maximum_candidates=1000, maximum_daily_units=2,
            baseline_trip_duration_p99_s=3600,
            budget=DailyUnitBudget(maximum_daily_units=2))
        state = payload["budget_state"]
        assert state["status"] == INCOMPLETE_STATUS
        assert state["stopped_by"] == "maximum_daily_units"
        assert state["resume_after_parent_id"], (
            "a pause without a cursor cannot be resumed")
        assert state["resume_after_parent_id"].startswith("closure-")

    def test_a_paused_artifact_cannot_be_read_as_exhaustive(self, tmp_path):
        """The word itself is removed, not merely a flag flipped."""
        payload = _independent_exhaustive_builder(
            _spec_file(tmp_path, "brute-force-multi-day"),
            maximum_candidates=1000, maximum_daily_units=2,
            baseline_trip_duration_p99_s=3600,
            budget=DailyUnitBudget(maximum_daily_units=2))
        assert payload["exhaustive"] is False
        assert "exhaustive" not in payload["proxy_version"]
        assert payload["proxy_version"] == (
            "independent_daily_budget_stopped_v1")
        assert "INCOMPLETE" in payload["budget_message"]

    def test_the_pause_stops_at_a_parent_boundary(self, tmp_path):
        """A cursor mid-decomposition would resume into a half-built parent."""
        payload = _independent_exhaustive_builder(
            _spec_file(tmp_path, "brute-force-multi-day"),
            maximum_candidates=1000, maximum_daily_units=2,
            baseline_trip_duration_p99_s=3600,
            budget=DailyUnitBudget(maximum_daily_units=2))
        complete = _independent_exhaustive_builder(
            _spec_file(tmp_path, "brute-force-multi-day"),
            maximum_candidates=1000, maximum_daily_units=10_000,
            baseline_trip_duration_p99_s=3600)
        assert payload["budget_state"]["resume_after_parent_id"] in (
            complete["shortlist_schedule_ids"]
            if "shortlist_schedule_ids" in complete
            else [item["schedule_id"]
                  for item in complete["shortlist"]["entries"]])


class TestTheSixMonthCaseReachesTheProductGate:
    """The plan's final acceptance criterion, through the real preflight."""

    def test_the_legacy_gate_still_rejects_it(self):
        spec = ClosureSearchSpec.from_dict(_case("six-month-360h"))
        with pytest.raises(ValueError, match="23349 unique daily SUMO units"):
            _independent_exhaustive_preflight(
                spec, maximum_candidates=100_000,
                maximum_daily_units=10_000,
                baseline_trip_duration_p99_s=3600)

    def test_a_declared_budget_admits_it(self):
        spec = ClosureSearchSpec.from_dict(_case("six-month-360h"))
        report = _independent_exhaustive_preflight(
            spec,
            budget=DailyUnitBudget(
                maximum_daily_units=SIX_MONTH_DAILY_UNIT_BUDGET,
                maximum_parent_schedules=100_000),
            maximum_candidates=100_000,
            maximum_daily_units=SIX_MONTH_DAILY_UNIT_BUDGET,
            baseline_trip_duration_p99_s=3600)
        assert report.parent_schedule_count == 11_813
        assert report.unique_daily_unit_count == 23_349

    def test_the_parent_protection_is_not_relaxed_by_a_budget(self):
        """A budget replaces the unit cap, never the parent one."""
        spec = ClosureSearchSpec.from_dict(_case("six-month-360h"))
        with pytest.raises(ValueError, match="parent schedules"):
            _independent_exhaustive_preflight(
                spec,
                budget=DailyUnitBudget(
                    maximum_daily_units=SIX_MONTH_DAILY_UNIT_BUDGET),
                maximum_candidates=100,
                maximum_daily_units=SIX_MONTH_DAILY_UNIT_BUDGET,
                baseline_trip_duration_p99_s=3600)


class TestTheProcessGateIsMeasuredInItsOwnProcess:
    """A 64 MiB gate asserted inside pytest measures pytest.

    The earlier test allowed 4x headroom precisely because the suite's own
    imports had already spent the budget. Measuring in a FRESH interpreter is
    the only way the number means what it says.
    """

    def test_streaming_the_360_hour_case_stays_under_64_mib(self):
        probe = (
            "import json, resource, sys\n"
            "sys.path.insert(0, %r)\n"
            "from traffic_sim.core.contracts import ClosureSearchSpec\n"
            "from traffic_sim.core.closure_calendar import iter_closure_schedules\n"
            "from traffic_sim.simulation.independent_daily import daily_unit_records\n"
            "cases = json.load(open(%r))['cases']\n"
            "spec = ClosureSearchSpec.from_dict([c for c in cases "
            "if c['case_id'] == 'six-month-360h'][0]['spec'])\n"
            "units = set(); parents = 0\n"
            "for s in iter_closure_schedules(spec):\n"
            "    parents += 1\n"
            "    for uid, _i, _b in daily_unit_records(spec, s):\n"
            "        units.add(uid)\n"
            # `ru_maxrss` SURVIVES fork+exec on Linux, so a child forked from
            # a loaded pytest reports the PARENT's high-water mark (~147 MiB
            # against ~26 MiB standalone). /proc/self/status VmHWM is this
            # process's own peak. The project learned this once before; the
            # first version of this test forgot it.
            "peak = None\n"
            "try:\n"
            "    for line in open('/proc/self/status'):\n"
            "        if line.startswith('VmHWM:'):\n"
            "            peak = int(line.split()[1]) * 1024\n"
            "except OSError:\n"
            "    peak = None\n"
            "if peak is None:\n"
            "    scale = 1 if sys.platform == 'darwin' else 1024\n"
            "    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale\n"
            "print(json.dumps({'parents': parents, 'units': len(units), "
            "'peak_rss_bytes': peak}))\n"
        ) % (str(ROOT), str(BASELINE))
        # A CLEAN environment, not merely a new process. Run from inside a
        # loaded pytest session the child reported ~147 MiB against ~26 MiB
        # standalone — it was measuring what it inherited from its parent, not
        # what the enumeration costs. `env -i` equivalence is the difference
        # between a gate and a coincidence.
        import os

        completed = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            timeout=1800, cwd=str(ROOT),
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                 "HOME": os.environ.get("HOME", "/tmp")})
        assert completed.returncode == 0, completed.stderr[-500:]
        measured = json.loads(completed.stdout)
        assert (measured["parents"], measured["units"]) == (11_813, 23_349)
        assert measured["peak_rss_bytes"] < 64 * 1024 * 1024, (
            "the streaming enumeration must fit the frozen 64 MiB process "
            f"gate; measured {measured['peak_rss_bytes'] / 1048576:.1f} MiB")
