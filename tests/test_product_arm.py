"""Coverage for the isolated-process arm execution added to repair the

cost-order v5 equivalence failure. v5 ran both benchmark arms sequentially
in ONE Python process with no per-arm resource isolation, which the frozen
evidence and docs/plans/ROAD_CLOSURE_SIMULATION_SPEED_PLAN_2026-08-21.md's
S3A section name as one of the concrete defects behind the arm-order-
dependent timeout classification. These tests exercise the isolation
mechanics directly: they do not require SUMO or real demand, because the
validation this module adds runs before either arm would touch either.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
from traffic_sim.simulation.monthly_search import MonthlySearchPolicy

import tools.product_arm as pa


def _spec(**overrides) -> ClosureSearchSpec:
    values = {
        "search_id": "product-arm-test",
        "directed_edges": ("a_b_0",),
        "demand_build_id": "forecast-2027",
        "source": "forecast",
        "permitted_date_start": "2027-01-04",
        "permitted_date_end": "2027-01-08",
        "required_work_minutes": 4 * 60,
        "max_consecutive_start_days": 1,
        "permitted_daily_band": DailyTimeBand("06:00", "12:00"),
        "allowed_weekdays": (0, 1, 2, 3, 4),
        "interday_policy": "independent_daily_reset_v1",
        "work_allocation_policy": "exact_equal_daily_v1",
        "objective_profile": "displaced_vehicles_and_detour_v1",
    }
    values.update(overrides)
    return ClosureSearchSpec(**values)


def _policy() -> MonthlySearchPolicy:
    return MonthlySearchPolicy.from_dict({
        "schema_version": 1,
        "kind": "monthly_closure_search_policy",
        "policy_id": "product-arm-test",
        "benchmark_id": "pending",
        "status": "provisional",
        "objective_method": "closure_cost_v1",
        "pilot": {
            "retention_band_s": 300.0,
            "repetitions_per_variant": 1,
            "minimum_finalists": 1,
            "maximum_finalists": 4,
            "variants": ["q10", "q50", "q90"],
        },
        "finalist": {
            "absolute_precision_floor_s": 600.0,
            "practical_equivalence_s": 300.0,
            "practical_equivalence_vehicle_hours": 0.0,
            "max_repetitions": 6,
            "initial_repetitions": 2,
            "confidence_level": 0.95,
            "relative_precision": 0.05,
            "variants": ["q10", "q50", "q90"],
            "micro_finalist_limit": 3,
        },
    })


class TestFrozenBenchmarkResourceShape:
    def test_defaults_are_exactly_one(self):
        assert pa.BENCHMARK_DAILY_WORKERS == 1
        assert pa.BENCHMARK_SEED_WORKERS == 1
        assert pa.BENCHMARK_MAX_ACTIVE_SUMO_SLOTS == 1


class TestBuildArmValidatesTheResourceBudget:
    def test_zero_daily_workers_is_rejected(self):
        with pytest.raises(ValueError, match="positive integers"):
            pa.build_arm(
                _spec(),
                cost_ordered=False,
                runs_root=Path("/nonexistent/runs"),
                release_root=Path("/nonexistent/release"),
                daily_cost_cache=Path("/nonexistent/cache.json"),
                study_provenance_key="study",
                objective_method="closure_cost_v1",
                daily_workers=0,
            )

    def test_workers_exceeding_the_active_slot_budget_is_rejected(self):
        with pytest.raises(ValueError, match="max_active_sumo_slots"):
            pa.build_arm(
                _spec(),
                cost_ordered=False,
                runs_root=Path("/nonexistent/runs"),
                release_root=Path("/nonexistent/release"),
                daily_cost_cache=Path("/nonexistent/cache.json"),
                study_provenance_key="study",
                objective_method="closure_cost_v1",
                daily_workers=2,
                seed_workers=2,
                max_active_sumo_slots=1,
            )

    def test_one_by_one_by_one_is_accepted(self, tmp_path):
        runner, screen_builder, cost_source = pa.build_arm(
            _spec(),
            cost_ordered=False,
            runs_root=tmp_path / "runs",
            release_root=tmp_path / "release",
            daily_cost_cache=tmp_path / "cache.json",
            study_provenance_key="study",
            objective_method="closure_cost_v1",
            daily_workers=1,
            seed_workers=1,
            max_active_sumo_slots=1,
        )
        assert runner is not None
        assert callable(screen_builder)
        assert cost_source is None  # only built for cost_ordered=True


class TestOrderedExhaustiveArmShape:
    """The reference arm that isolates early stopping as the only variable."""

    def test_disable_early_stop_requires_cost_ordered(self):
        with pytest.raises(ValueError, match="only has meaning"):
            pa.build_arm(
                _spec(),
                cost_ordered=False,
                runs_root=Path("/nonexistent/runs"),
                release_root=Path("/nonexistent/release"),
                daily_cost_cache=Path("/nonexistent/cache.json"),
                study_provenance_key="study",
                objective_method="closure_cost_v1",
                disable_early_stop=True,
            )

    def test_it_is_accepted_alongside_cost_ordered(self, tmp_path):
        runner, screen_builder, cost_source = pa.build_arm(
            _spec(),
            cost_ordered=True,
            runs_root=tmp_path / "runs",
            release_root=tmp_path / "release",
            daily_cost_cache=tmp_path / "cache.json",
            study_provenance_key="study",
            objective_method="closure_cost_v1",
            disable_early_stop=True,
        )
        assert runner is not None
        assert cost_source is not None


class TestDailyResultsCacheRootIsolation:
    """A benchmark comparing two arms must never let them share real SUMO
    evidence — see tools.cost_ordered_benchmark._isolated_daily_results_cache_root.
    """

    def test_defaults_to_the_daily_cost_cache_sibling(self, tmp_path):
        runner, _, _ = pa.build_arm(
            _spec(), cost_ordered=False,
            runs_root=tmp_path / "runs", release_root=tmp_path / "release",
            daily_cost_cache=tmp_path / "costs" / "cache",
            study_provenance_key="study", objective_method="closure_cost_v1",
        )
        assert runner.cache_root == tmp_path / "costs" / "daily-results"

    def test_an_explicit_root_overrides_the_default(self, tmp_path):
        explicit = tmp_path / "isolated" / "arm-a"
        runner, _, _ = pa.build_arm(
            _spec(), cost_ordered=False,
            runs_root=tmp_path / "runs", release_root=tmp_path / "release",
            daily_cost_cache=tmp_path / "costs" / "cache",
            study_provenance_key="study", objective_method="closure_cost_v1",
            daily_results_cache_root=explicit,
        )
        assert runner.cache_root == explicit


class TestKillProcessGroup:
    def test_reaps_a_real_sleeping_process_group(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        try:
            assert process.poll() is None
            pa._kill_process_group(process.pid, grace_s=2.0)
            process.wait(timeout=5.0)
            assert process.returncode is not None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5.0)

    def test_an_already_dead_process_group_is_a_silent_no_op(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "pass"], start_new_session=True)
        process.wait(timeout=5.0)
        # Should not raise even though the group is long gone.
        pa._kill_process_group(process.pid, grace_s=0.1)


class TestRunArmIsolated:
    def test_an_invalid_resource_shape_fails_closed_through_the_subprocess(
            self, tmp_path):
        """End-to-end proof the isolation plumbing works.

        spawn + chdir + spec/policy JSON round-trip + build_arm's guard +
        the result-file handoff all have to work correctly for this to
        surface as a RuntimeError naming the real defect, rather than a
        hang, a silent success, or a lost payload.
        """
        with pytest.raises(RuntimeError, match="daily_workers"):
            pa.run_arm_isolated(
                _spec(),
                _policy(),
                cost_ordered=False,
                workspace_root=tmp_path / "workspace",
                runs_root=tmp_path / "runs",
                release_root=tmp_path / "release",
                daily_cost_cache=tmp_path / "cache.json",
                study_provenance_key="study",
                data_root=tmp_path,
                daily_workers=0,
                timeout_s=120.0,
            )

    def test_a_process_that_cannot_finish_in_time_is_reaped(self, tmp_path):
        # daily_workers=2 * seed_workers=2 > slots=1 fails fast inside
        # build_arm, so this exercises the timeout/reap path deterministically
        # via an unreasonably small timeout rather than a real hang: even the
        # fastest possible spawn (fresh interpreter + reimport) cannot beat a
        # near-zero deadline, but the process is still real and still owned.
        with pytest.raises(TimeoutError, match="reaped"):
            pa.run_arm_isolated(
                _spec(),
                _policy(),
                cost_ordered=False,
                workspace_root=tmp_path / "workspace",
                runs_root=tmp_path / "runs",
                release_root=tmp_path / "release",
                daily_cost_cache=tmp_path / "cache.json",
                study_provenance_key="study",
                data_root=tmp_path,
                timeout_s=0.001,
                reap_grace_s=1.0,
            )


class TestProcessTreeRSSSampling:
    """Simultaneous process-tree RSS, added to fix under-reporting of the
    8 GiB memory gate: `resource.getrusage` reports `max(self, one reaped
    child)`, never the SUM of what is alive at the same instant.
    """

    def test_a_lone_sleeping_process_is_measured(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            start_new_session=True,
        )
        try:
            rss = pa._process_tree_rss_bytes(process.pid)
            assert rss > 0
            assert process.pid in pa._process_group_pids(process.pid)
        finally:
            process.kill()
            process.wait(timeout=5.0)

    def test_a_dead_group_reports_zero_and_no_pids(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "pass"], start_new_session=True)
        process.wait(timeout=5.0)
        assert pa._process_tree_rss_bytes(process.pid) == 0
        assert pa._process_group_pids(process.pid) == []

    def test_the_sampler_captures_a_process_that_exits_between_polls(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            start_new_session=True,
        )
        sampler = pa.ProcessTreeRSSSampler(process.pid, interval_s=0.05).start()
        try:
            process.wait(timeout=5.0)
        finally:
            peak = sampler.stop()
        assert peak > 0


class TestProcessCensusFailureIsNeverReadAsEmpty:
    """A failed `ps` invocation must surface as UNKNOWN, never as zero RSS
    or zero survivors — see `pa.ProcessCensusUnavailable`.
    """

    def test_a_nonzero_ps_exit_raises_rather_than_returning_empty(
            self, monkeypatch):
        class FakeCompleted:
            returncode = 1
            stdout = ""
            stderr = "ps: permission denied"

        monkeypatch.setattr(
            pa.subprocess, "run", lambda *a, **k: FakeCompleted())
        with pytest.raises(pa.ProcessCensusUnavailable, match="exited 1"):
            pa._process_group_snapshot()

    def test_an_unrunnable_ps_raises_rather_than_returning_empty(
            self, monkeypatch):
        def boom(*a, **k):
            raise OSError("ps not found")

        monkeypatch.setattr(pa.subprocess, "run", boom)
        with pytest.raises(pa.ProcessCensusUnavailable):
            pa._process_group_snapshot()

    def test_sampler_refuses_to_report_a_peak_after_a_lost_census(
            self, monkeypatch):
        real_run = pa.subprocess.run
        calls = {"n": 0}

        def flaky_run(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated ps failure")
            return real_run(*args, **kwargs)

        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            start_new_session=True,
        )
        try:
            monkeypatch.setattr(pa.subprocess, "run", flaky_run)
            sampler = pa.ProcessTreeRSSSampler(
                process.pid, interval_s=0.05).start()
            time.sleep(0.3)
            with pytest.raises(pa.ProcessCensusUnavailable):
                sampler.stop()
        finally:
            process.kill()
            process.wait(timeout=5.0)

    def test_reaping_fails_closed_when_the_census_is_unavailable(
            self, monkeypatch):
        def boom(*a, **k):
            raise OSError("ps not found")

        monkeypatch.setattr(pa.subprocess, "run", boom)
        with pytest.raises(pa.ProcessCensusUnavailable):
            pa._ensure_process_group_reaped(123456, 0.1)


class TestEnsureProcessGroupReaped:
    def test_an_empty_group_needs_no_reaping(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "pass"], start_new_session=True)
        process.wait(timeout=5.0)
        assert pa._ensure_process_group_reaped(process.pid, 1.0) == []

    def test_a_survivor_is_reported_and_reaped(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        # The verified post-termination census polls `ps` until the group is
        # actually gone from the kernel's table, which for a direct child
        # requires someone to `wait()` on it (otherwise it lingers as a
        # zombie and still shows up in `ps`) — exactly what `run_arm_isolated`
        # itself always does concurrently with reaping in production.
        reaper = threading.Thread(target=process.wait, daemon=True)
        reaper.start()
        try:
            survivors = pa._ensure_process_group_reaped(process.pid, 2.0)
            assert process.pid in survivors
            reaper.join(timeout=5.0)
            assert process.poll() is not None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5.0)
