"""Review regressions for cost-first execution (stage 1 re-review).

Each test here corresponds to a defect found by reviewing commits 16046fe
through fe8b3d4. They are kept apart from `test_cost_ordered_execution.py`
because their value is the specific hole they close, and that is worth being
able to read in one place.

The unifying lesson is that all three defects hid behind a fixture that was
politer than the real runner. `FakeRunner` declared
`compact_pilot_artifacts = False`; the deployed `IndependentDailyRunner`
declares True, and cost-ordered execution is only ever used with it.
"""

from __future__ import annotations

import json

import pytest

from traffic_sim.simulation import cost_ordered_execution as coe
from traffic_sim.simulation.independent_daily import IndependentDailyRunner
from tests.test_cost_ordered_execution import (
    FakeRunner,
    _ordered_prices,
    _policy,
    _run,
    _spec,
)


class _Boom(RuntimeError):
    """A crash that is not an expected control-flow exception."""


def _compact_runner(*, prices, crash_after=None):
    """A runner faithful to the one the product actually uses."""

    class CompactRunner(FakeRunner):
        compact_pilot_artifacts = True

        def run_candidate(self, schedule, **kwargs):
            if crash_after is not None and len(self.simulated) >= crash_after:
                raise _Boom("killed mid-pilot")
            return super().run_candidate(schedule, **kwargs)

    return CompactRunner(prices=prices)


def _workspace_dir(root, spec):
    return root / spec.search_id


class TestTheDeployedRunnerCanActuallyResume:
    """DEFECT: every resume of a real cost-ordered search failed closed.

    `IndependentDailyRunner` declares compact pilot artifacts, so no per-parent
    evidence was published. The cursor still recorded each verification, and
    the resume then demanded evidence that had deliberately never been written,
    raising "evidence is missing" on every restart. The durability machinery
    was void in the only configuration that uses it.
    """

    def test_the_fixture_matches_the_runner_the_product_uses(self):
        assert IndependentDailyRunner.compact_pilot_artifacts is True, (
            "if this ever becomes False the defect below stops being "
            "reachable and these tests stop testing anything")

    def test_a_crashed_compact_search_resumes(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        policy = _policy(minimum=3)

        crashing = _compact_runner(prices=prices, crash_after=2)
        with pytest.raises(_Boom):
            _run(spec, policy, tmp_path, prices=prices, runner=crashing)
        assert len(crashing.simulated) == 2

        resumed = _compact_runner(prices=prices)
        result, _runner, _source, _ids = _run(
            spec, policy, tmp_path, prices=prices, runner=resumed)

        assert result["status"]
        assert set(resumed.piloted).isdisjoint(crashing.piloted), (
            "the resume re-simulated work the crash had already published")

    def test_the_resumed_compact_answer_equals_the_uninterrupted_one(
            self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        policy = _policy(minimum=3)

        clean, clean_runner, _s, _i = _run(
            spec, policy, tmp_path / "clean", prices=prices,
            runner=_compact_runner(prices=prices))

        crash_root = tmp_path / "crash"
        crashing = _compact_runner(prices=prices, crash_after=1)
        with pytest.raises(_Boom):
            _run(spec, policy, crash_root, prices=prices, runner=crashing)
        resumed, resumed_runner, _s2, _i2 = _run(
            spec, policy, crash_root, prices=prices,
            runner=_compact_runner(prices=prices))

        assert resumed["status"] == clean["status"]
        assert resumed["winner_id"] == clean["winner_id"]
        assert resumed["pilot_selection"]["selected_ids"] == (
            clean["pilot_selection"]["selected_ids"])
        assert (len(crashing.piloted) + len(resumed_runner.piloted)
                == len(clean_runner.piloted))

    def test_the_exhaustive_path_still_compacts(self, tmp_path):
        """The saving compaction exists for is untouched where it applies."""
        from traffic_sim.simulation.monthly_search import run_monthly_search
        from tests.test_cost_ordered_execution import _screen_builder
        from traffic_sim.core.closure_calendar import generate_closure_schedules

        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        ids = [item.schedule_id
               for item in generate_closure_schedules(spec)]
        runner = _compact_runner(prices=prices)
        run_monthly_search(
            spec, _policy(minimum=2), runner=runner,
            screen_builder=_screen_builder(spec, ids), root=tmp_path)

        published = [
            record for record
            in json.loads((_workspace_dir(tmp_path, spec) / "manifest.json")
                          .read_text(encoding="utf-8"))["artifacts"]
            if record["kind"] == "monthly_pilot_candidate"]
        assert published == [], (
            "an exhaustive compact pilot must still write no per-parent files")


class TestTheSavingIsDurablyRecorded:
    """DEFECT: `execution_record` was dead code.

    `_cost_ordered_pilot` returned the result and `run_monthly_search` dropped
    it on the floor. Nothing in the workspace or in result.json distinguished a
    cost-ordered run from an exhaustive one, and the stop proof — the whole
    argument that the unexamined candidates could not have won — existed only
    in memory.
    """

    def test_the_workspace_carries_the_execution_record(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        _result, runner, _source, ids = _run(
            spec, _policy(minimum=2), tmp_path, prices=prices)

        manifest = json.loads(
            (_workspace_dir(tmp_path, spec) / "manifest.json")
            .read_text(encoding="utf-8"))
        records = [item for item in manifest["artifacts"]
                   if item["kind"] == coe.EXECUTION_KIND]
        assert len(records) == 1
        payload = json.loads(
            (_workspace_dir(tmp_path, spec) / records[0]["path"])
            .read_text(encoding="utf-8"))
        assert payload["release_evidence"] is False
        assert payload["exhaustive_sumo_candidates"] == len(ids)
        assert payload["cost_ordered_sumo_candidates"] == len(runner.piloted)
        assert payload["sumo_verifications_saved"] == (
            len(ids) - len(runner.piloted)) > 0
        assert payload["stop_proof"], "the stop proof must be durable"

    def test_the_result_says_which_execution_path_ran(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        result, runner, _source, ids = _run(
            spec, _policy(minimum=2), tmp_path, prices=prices)

        summary = result["cost_ordered_execution"]
        assert summary is not None
        assert summary["cost_ordered_sumo_candidates"] == len(runner.piloted)
        assert summary["exhaustive_sumo_candidates"] == len(ids)

    def test_an_exhaustive_run_says_so_by_omission(self, tmp_path):
        from traffic_sim.simulation.monthly_search import run_monthly_search
        from tests.test_cost_ordered_execution import _screen_builder
        from traffic_sim.core.closure_calendar import generate_closure_schedules

        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        ids = [item.schedule_id for item in generate_closure_schedules(spec)]
        result = run_monthly_search(
            spec, _policy(minimum=2), runner=FakeRunner(prices=prices),
            screen_builder=_screen_builder(spec, ids), root=tmp_path)
        assert result["cost_ordered_execution"] is None

    def test_a_re_entered_pilot_must_report_the_same_saving(self, tmp_path):
        """A resume that changed its mind about the saving is a contradiction."""
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        policy = _policy(minimum=3)

        crashing = _compact_runner(prices=prices, crash_after=2)
        with pytest.raises(_Boom):
            _run(spec, policy, tmp_path, prices=prices, runner=crashing)
        first, _r, _s, _i = _run(spec, policy, tmp_path, prices=prices,
                                 runner=_compact_runner(prices=prices))

        workspace = _workspace_dir(tmp_path, spec)
        record = [item for item
                  in json.loads((workspace / "manifest.json")
                                .read_text(encoding="utf-8"))["artifacts"]
                  if item["kind"] == coe.EXECUTION_KIND]
        assert len(record) == 1, "the record must be published exactly once"
        stored = json.loads((workspace / record[0]["path"])
                            .read_text(encoding="utf-8"))
        assert stored == first["cost_ordered_execution"]
        assert "wall_time_s" not in stored, (
            "timing in the record would make a resume look non-deterministic")


class TestAnOrphanCursorIsRefusedNotSteppedOver:
    """NOT a defect, recorded because the review first thought it was.

    The worry: `publish_artifact` copies the cursor file and THEN appends its
    manifest record, so a process killed between the two leaves an orphan the
    manifest does not mention. Deriving the next cursor index from manifest
    records alone would reuse that name, and publication refuses to clobber —
    seemingly stranding the search with FileExistsError forever.

    It cannot happen. `verify_search_workspace` refuses any file under
    `artifacts/` that is not in the ledger, so such a workspace fails integrity
    on LOAD, naming the orphan, long before any cursor index is computed. That
    is the stricter and better behaviour: a workspace whose contents nobody can
    account for should not be silently resumed. This test exists so a future
    reader does not re-derive the same wrong fix.
    """

    def test_the_workspace_refuses_to_load_naming_the_orphan(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        policy = _policy(minimum=3)

        crashing = _compact_runner(prices=prices, crash_after=2)
        with pytest.raises(_Boom):
            _run(spec, policy, tmp_path, prices=prices, runner=crashing)

        workspace = _workspace_dir(tmp_path, spec)
        cursors = [item for item
                   in json.loads((workspace / "manifest.json")
                                 .read_text(encoding="utf-8"))["artifacts"]
                   if item["kind"] == coe.CURSOR_KIND]
        assert cursors, "the crash should have checkpointed something"

        # Exactly what a kill between copy and manifest flush leaves behind.
        orphan = (workspace / "artifacts"
                  / f"cost-ordered-cursor-{len(cursors):05d}.json")
        orphan.write_text(json.dumps({"orphan": True}), encoding="utf-8")

        with pytest.raises(ValueError, match="not in the ledger"):
            _run(spec, policy, tmp_path, prices=prices,
                 runner=_compact_runner(prices=prices))
        assert orphan.is_file(), "the evidence of the crash must be preserved"

    def test_removing_the_orphan_makes_the_search_resumable_again(self,
                                                                  tmp_path):
        """The refusal is recoverable by a human who looks, which is the point."""
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        policy = _policy(minimum=3)

        crashing = _compact_runner(prices=prices, crash_after=2)
        with pytest.raises(_Boom):
            _run(spec, policy, tmp_path, prices=prices, runner=crashing)

        workspace = _workspace_dir(tmp_path, spec)
        cursors = [item for item
                   in json.loads((workspace / "manifest.json")
                                 .read_text(encoding="utf-8"))["artifacts"]
                   if item["kind"] == coe.CURSOR_KIND]
        orphan = (workspace / "artifacts"
                  / f"cost-ordered-cursor-{len(cursors):05d}.json")
        orphan.write_text(json.dumps({"orphan": True}), encoding="utf-8")
        orphan.unlink()

        resumed, _runner, _source, _ids = _run(
            spec, policy, tmp_path, prices=prices,
            runner=_compact_runner(prices=prices))
        assert resumed["status"]
