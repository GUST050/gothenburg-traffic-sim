"""The closure search must not pay for SciPy before it needs a t-quantile.

PR C's memory gate is a claim about a PROCESS, so the thing that has to be
policed is what the process imports. `finalist_decision` reaches for
`scipy.stats.t` in exactly one place — a confidence half-width — but it used to
import it at module scope, and `independent_daily` imports `finalist_decision`,
so enumeration, preflight, ledger writing and cost ordering all paid 81.7 MiB
for a distribution they never evaluate.

These tests run in REAL child interpreters. An in-process check cannot see the
fault: by the time the test module is collected, pytest has already imported
half the tree, and `sys.modules` would report SciPy loaded by something else
entirely.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Ceiling for a child that has imported the whole search path and done real
#: work. Deliberately generous — this is a regression guard against re-adding a
#: heavy module-scope import, not a benchmark. The plan's exit gate is measured
#: by `tools/benchmark_closure_streaming.py` on comparable hardware.
IMPORT_ONLY_CEILING_BYTES = 48 * 1024 * 1024


def _child(body: str) -> dict:
    program = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "def _peak():\n"
        "    try:\n"
        "        for line in open('/proc/self/status', encoding='utf-8'):\n"
        "            if line.startswith('VmHWM:'):\n"
        "                return int(line.split()[1]) * 1024\n"
        "    except OSError:\n"
        "        pass\n"
        "    import resource\n"
        "    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "    return int(value if sys.platform == 'darwin' else value * 1024)\n"
        + body
        + "\nprint(json.dumps({'scipy': 'scipy' in sys.modules,"
          " 'numpy': 'numpy' in sys.modules, 'peak_rss_bytes': _peak()}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True,
        cwd=ROOT, timeout=600)
    assert completed.returncode == 0, completed.stderr[-3000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


class TestTheSearchPathStaysScipyFree:
    def test_importing_the_whole_search_path_does_not_load_scipy(self):
        result = _child(
            "import traffic_sim.core.closure_calendar\n"
            "import traffic_sim.simulation.closure_preflight\n"
            "import traffic_sim.simulation.closure_ledgers\n"
            "import traffic_sim.simulation.independent_daily\n"
            "import traffic_sim.simulation.monthly_search\n"
            "import traffic_sim.simulation.finalist_decision\n"
        )
        assert result["scipy"] is False
        assert result["numpy"] is False
        assert result["peak_rss_bytes"] < IMPORT_ONLY_CEILING_BYTES

    def test_streaming_a_real_search_into_ledgers_does_not_load_scipy(
            self, tmp_path):
        spec = json.dumps(_SPEC_PAYLOAD)
        result = _child(
            "from traffic_sim.core.contracts import ClosureSearchSpec\n"
            "from traffic_sim.core.closure_calendar import "
            "iter_closure_schedules\n"
            "from traffic_sim.simulation.closure_ledgers import write_ledgers\n"
            "from traffic_sim.simulation.independent_daily import "
            "daily_unit_records\n"
            "from traffic_sim.simulation.closure_preflight import preflight\n"
            f"spec = ClosureSearchSpec.from_dict(json.loads({spec!r}))\n"
            "report = preflight(spec)\n"
            f"manifest = write_ledgers({str(tmp_path)!r}, spec,"
            " iter_closure_schedules(spec), unit_records=daily_unit_records,"
            " provenance={'test': 1})\n"
            "assert manifest.parent_count == report.parent_schedule_count\n"
            "assert manifest.unique_unit_count =="
            " report.unique_daily_unit_count\n"
        )
        assert result["scipy"] is False, (
            "streaming a search loaded SciPy; a heavy module-scope import came "
            "back")
        assert result["peak_rss_bytes"] < IMPORT_ONLY_CEILING_BYTES

    def test_the_t_quantile_still_loads_scipy_where_it_is_used(self):
        """The lazy import must still be a real import, not a substitution."""
        result = _child(
            "from traffic_sim.simulation.finalist_decision import "
            "_student_t_ppf\n"
            "value = _student_t_ppf(0.975, 4)\n"
            "assert abs(value - 2.7764451051977996) < 1e-12, value\n"
        )
        assert result["scipy"] is True


#: A small independent-daily search: real contract, cheap to enumerate.
_SPEC_PAYLOAD = {
    "kind": "closure_search",
    "schema_version": 1,
    "search_id": "import-cost-probe",
    "directed_edges": ["a_b_0"],
    "demand_build_id": "forecast-2027",
    "permitted_date_start": "2027-01-04",
    "permitted_date_end": "2027-01-15",
    "required_work_minutes": 8 * 60,
    "max_consecutive_start_days": 3,
    "permitted_daily_band": {"earliest_start": "06:00", "latest_end": "18:00"},
    "source": "forecast",
    "timezone": "Europe/Stockholm",
    "dst_policy": "exclude_transition_dates",
    "allowed_weekdays": [0, 1, 2, 3, 4],
    "blackout_dates": [],
    "same_daily_window": True,
    "resolution_minutes": 15,
    "closure_type": "full",
    "duration_basis": "required_work_time",
    "work_to_closure_assumption": "one_to_one",
    "objective_profile": "robust_time_loss",
    "policy_status": "user_supplied_unverified",
    "interday_policy": "independent_daily_reset_v1",
    "work_allocation_policy": "exact_equal_daily_v1",
    "period_comparison_policy": "rolling_period_v1",
}


def test_the_probe_spec_is_a_valid_contract():
    from traffic_sim.core.contracts import ClosureSearchSpec

    ClosureSearchSpec.from_dict(dict(_SPEC_PAYLOAD))
