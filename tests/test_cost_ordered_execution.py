"""Stage 1: the product must cost first and simulate only the boundary.

The distinction this file exists to defend is the whole point of the stage. A
post-hoc replay proves the answer WOULD have been the same; it saves nothing,
because everything was already simulated. Real cost-first execution has to
price every candidate, then never call the runner for the candidates it proved
cannot be selected — and it has to survive being killed in the middle.

So the tests assert on the RUNNER CALL LOG, not only on the answer: which
candidates SUMO actually saw, in what order, and how many times across a crash
and resume.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from traffic_sim.core.contracts import (
    ClosureSearchSpec,
    DailyTimeBand,
)
from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.simulation.closure_ranking import ClosureCost
from traffic_sim.simulation import cost_ordered_execution as coe
from traffic_sim.simulation.finalist_decision import (
    RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
    TIMEOUT_IDENTITY_SCHEMA,
    CandidateEvidence,
    PairedObservation,
    TimeoutIdentity,
)
from traffic_sim.simulation.monthly_search import (
    MonthlySearchPolicy,
    run_monthly_search,
)
from traffic_sim.simulation.pilot_selection import PilotPolicy
from traffic_sim.simulation.search_workspace import load_search_workspace


def _spec(**overrides) -> ClosureSearchSpec:
    values = {
        "search_id": "cost-first",
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


def _policy(minimum=2, maximum=12) -> MonthlySearchPolicy:
    return MonthlySearchPolicy.from_dict({
        "schema_version": 1,
        "kind": "monthly_closure_search_policy",
        "policy_id": "cost-first-test",
        "benchmark_id": "pending",
        "status": "provisional",
        "objective_method": "closure_cost_v1",
        "pilot": {
            "retention_band_s": 300.0,
            "repetitions_per_variant": 1,
            "minimum_finalists": minimum,
            "maximum_finalists": maximum,
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


def _variant_records(hours, *, no_detour=0):
    return tuple({
        "demand_variant": variant,
        "vehicles_affected": int(hours * 10),
        "vehicles_considered": 1000,
        "vehicles_no_detour": int(no_detour),
        "added_vehicle_hours": float(hours),
        "added_metres_total": float(hours * 100.0),
    } for variant in ("q10", "q50", "q90"))


class FakeCostSource:
    """Prices a parent from a table. Records every pricing call."""

    def __init__(self, prices, *, no_detour=()):
        self.prices = dict(prices)
        self.no_detour = set(no_detour)
        self.calls: list[str] = []
        self.cache_hits = 0
        self.computed_units = 0

    def identity(self):
        return {"schema": "fake_cost_source_v1", "network": {"sha256": "net"}}

    def parent_cost(self, parent):
        self.calls.append(parent.schedule_id)
        self.computed_units += 1
        hours = self.prices[parent.schedule_id]
        detour = 1 if parent.schedule_id in self.no_detour else 0
        records = _variant_records(hours, no_detour=detour)
        return coe.ParentCost(
            candidate_id=parent.schedule_id,
            cost=ClosureCost(
                candidate_id=parent.schedule_id,
                added_vehicle_hours=float(hours),
                added_metres_total=float(hours * 100.0),
                vehicles_affected=int(hours * 10),
                vehicles_no_detour=detour,
            ),
            per_variant=records,
            daily_unit_ids=(f"daily-{parent.schedule_id}",),
        )


class FakeRunner:
    """A SUMO backend that records exactly which candidates it simulated.

    Returns disruption like the real `ArchivedDemandSumoRunner` does, so the
    execution path's pre-SUMO/post-SUMO reconciliation is exercised rather than
    bypassed.
    """

    compact_pilot_artifacts = False

    def __init__(self, *, failures=(), prices=None, no_detour=()):
        self.simulated: list[str] = []
        self.failures = set(failures)
        self.prices = dict(prices or {})
        self.no_detour = set(no_detour)
        self.prepared: tuple = ()
        self.by_stage: dict[str, list[str]] = {}
        self._exact_launch_telemetry: dict[str, dict[str, int]] = {}

    @property
    def piloted(self) -> list[str]:
        """Pilot-stage simulations — where cost ordering does its saving.

        Finalist rounds re-run the SELECTED few for precision; that is the
        unchanged adaptive stage and is not what the boundary bounds.
        """
        return list(self.by_stage.get("pilot", []))

    def prepare(self, schedules):
        self.prepared = tuple(item.schedule_id for item in schedules)

    def provenance(self):
        return {"schema_version": 1, "kind": "fake-backend",
                "simulation_mode": "meso"}

    def run_candidate(self, schedule, *, target_repetitions, existing, stage):
        self.simulated.append(schedule.schedule_id)
        self.by_stage.setdefault(stage, []).append(schedule.schedule_id)
        if schedule.schedule_id in self.failures:
            self._bump_exact_attempt(stage, launched=0)
            return CandidateEvidence(
                candidate_id=schedule.schedule_id,
                hard_failures=("no_viable_detour",),
                disruption=self._disruption(schedule.schedule_id),
            )
        observations = []
        for variant in ("q10", "q50", "q90"):
            for repetition in range(target_repetitions[variant]):
                seed = 1000 + repetition * 3 + ("q10", "q50", "q90").index(variant)
                observations.append(PairedObservation(
                    candidate_id=schedule.schedule_id,
                    demand_variant=variant,
                    seed=seed,
                    baseline_time_loss_s=100.0,
                    candidate_time_loss_s=110.0,
                    matched_baseline_id="baseline",
                    provenance_key="provenance",
                ))
        self._bump_exact_attempt(stage, launched=len(observations))
        return CandidateEvidence(
            candidate_id=schedule.schedule_id,
            observations=tuple(observations),
            disruption=self._disruption(schedule.schedule_id),
        )

    def _disruption(self, candidate_id):
        if candidate_id not in self.prices:
            return ()
        return _variant_records(
            self.prices[candidate_id],
            no_detour=1 if candidate_id in self.no_detour else 0,
        )

    def _bump_exact_attempt(self, stage: str, *, launched: int) -> None:
        """Mirror of `ArchivedDemandSumoRunner.launch_telemetry` for tests.

        One real (variant, seed) SUMO launch per generated observation; a
        candidate that hard-fails before any SUMO call launches zero. Optional
        — `timing_snapshot` below is the only thing that reads this.
        """
        bucket = self._exact_launch_telemetry.setdefault(
            stage, {"attempts": 0, "timeouts": 0, "other_outcomes": 0})
        bucket["attempts"] += launched
        bucket["other_outcomes"] += launched

    def timing_snapshot(self):
        """Optional S0 hook: exact-launch telemetry only, nothing else."""
        return {"exact_launch_telemetry": {
            stage: dict(counts)
            for stage, counts in self._exact_launch_telemetry.items()}}


def _screen_builder(spec, ids):
    def build(path):
        return {
            "schema_version": 1,
            "kind": "monthly_closure_proxy_screening",
            "proxy_version": "independent_daily_exhaustive_sumo_v1",
            "search": spec.to_dict(),
            "candidate_count": len(ids),
            "scoreable_candidate_count": len(ids),
            "unavailable_candidates": [],
            "ranked_candidates": [],
            "shortlist": {
                "version": "independent_daily_exhaustive_sumo_v1",
                "selection_complete": True,
                "entries": [
                    {"schedule_id": value, "selection_reasons": ["exhaustive"],
                     "proxy_rank": None}
                    for value in ids
                ],
            },
        }

    return build


def _ordered_prices(spec):
    """Distinct ascending prices, keyed by the real enumerated schedule IDs."""
    schedules = generate_closure_schedules(spec)
    assert len(schedules) >= 6, len(schedules)
    return schedules, {
        schedule.schedule_id: float(index + 1)
        for index, schedule in enumerate(schedules)
    }


def _run(spec, policy, root, *, prices, runner=None, no_detour=(),
         failures=()):
    schedules = generate_closure_schedules(spec)
    ids = [item.schedule_id for item in schedules]
    source = FakeCostSource(prices, no_detour=no_detour)
    runner = runner or FakeRunner(failures=failures, prices=prices,
                                  no_detour=no_detour)
    result = run_monthly_search(
        spec, policy,
        runner=runner,
        screen_builder=_screen_builder(spec, ids),
        root=root,
        cost_source=source,
    )
    return result, runner, source, ids


def _priced(candidate_id="closure-a"):
    cost = ClosureCost(
        candidate_id=candidate_id,
        added_vehicle_hours=12.0,
        added_metres_total=500.0,
        vehicles_affected=8,
        vehicles_no_detour=0,
    )
    per_variant = tuple({
        "demand_variant": variant,
        "vehicles_affected": 8,
        "vehicles_considered": 8,
        "vehicles_no_detour": 0,
        "added_vehicle_hours": 12.0,
        "added_metres_total": 500.0,
    } for variant in ("q10", "q50", "q90"))
    return coe.ParentCost(
        candidate_id=candidate_id, cost=cost, per_variant=per_variant)


class TestReconcileDisruption:
    """Regression coverage for the timeout_undecided passthrough.

    cost-order v5's ledger fallback already restored disruption on a runner
    that returned none; it must also carry the runner's undecided-timeout
    identity forward rather than losing it, or the search-level fail-closed
    gate in pilot_selection could not see it.
    """

    def test_ledger_fallback_preserves_an_undecided_timeout(self):
        priced = _priced()
        identity = TimeoutIdentity(
            schema=TIMEOUT_IDENTITY_SCHEMA,
            candidate_id="closure-a",
            work_date="2027-09-16",
            search_content_key="0123456789abcdef0123",
            variant="q50",
            seed=1000,
            attempt=1,
            threshold_s=300.0,
            retry_protocol=RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
            search_provenance_key="provenance",
        )
        evidence = CandidateEvidence(
            candidate_id="closure-a",
            timeout_undecided=(identity,),
        )
        reconciled = coe.reconcile_disruption(evidence, priced)
        assert reconciled.disruption == priced.per_variant
        assert reconciled.timeout_undecided == (identity,)
        assert reconciled.has_undecided_timeout

    def test_field_identical_disruption_returns_the_original_evidence(self):
        priced = _priced()
        evidence = CandidateEvidence(
            candidate_id="closure-a",
            disruption=priced.per_variant,
            timeout_undecided=(),
        )
        reconciled = coe.reconcile_disruption(evidence, priced)
        assert reconciled is evidence
        assert reconciled.timeout_undecided == ()

    def test_all_variant_field_mismatches_are_reported_in_stable_order(self):
        priced = _priced()
        produced = []
        for record in reversed(priced.per_variant):
            changed = dict(record)
            changed["vehicles_affected"] += 1
            changed["added_metres_total"] += 2
            produced.append(changed)
        evidence = CandidateEvidence(
            candidate_id="closure-a", disruption=tuple(produced))

        with pytest.raises(ValueError) as caught:
            coe.reconcile_disruption(evidence, priced)

        message = str(caught.value)
        expected = [
            f"{variant}.{field}"
            for variant in ("q10", "q50", "q90")
            for field in ("vehicles_affected", "added_metres_total")
        ]
        assert all(item in message for item in expected)
        assert [message.index(item) for item in expected] == sorted(
            message.index(item) for item in expected)


class TestRealCostFirstExecution:
    def test_sumo_runs_only_for_the_boundary_set(self, tmp_path):
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        result, runner, source, ids = _run(
            spec, _policy(minimum=2), tmp_path, prices=prices)

        # Every candidate was PRICED; only the boundary set was SIMULATED.
        assert len(source.calls) == len(ids)
        assert len(runner.piloted) == 2, runner.piloted
        assert runner.piloted == sorted(
            runner.piloted,
            key=lambda value: prices[value]), "SUMO ran out of cost order"
        assert set(result["selected_schedules"][0].keys())

    def test_it_does_not_run_an_exhaustive_pass_first(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        _result, runner, _source, ids = _run(
            spec, _policy(minimum=2), tmp_path, prices=prices)

        assert len(runner.piloted) < len(ids), (
            "cost-ordered execution piloted everything; it is still a "
            "post-hoc replay rather than a saving")
        assert len(set(runner.piloted)) == len(runner.piloted), (
            "a candidate was piloted twice")

    def test_no_detour_candidates_are_never_simulated(self, tmp_path):
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        cheapest = schedules[0].schedule_id
        _result, runner, _source, _ids = _run(
            spec, _policy(minimum=2), tmp_path, prices=prices,
            no_detour=(cheapest,))

        assert cheapest not in runner.piloted

    def test_hard_failures_continue_the_scan(self, tmp_path):
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        failures = {schedules[0].schedule_id, schedules[1].schedule_id}
        result, runner, _source, _ids = _run(
            spec, _policy(minimum=2), tmp_path, prices=prices,
            failures=failures)

        assert set(failures) <= set(runner.piloted)
        assert len(runner.piloted) == 4
        assert result["status"] in {"unique_winner", "tie", "inconclusive"}

    def test_the_selector_still_owns_capacity_exceeded(self, tmp_path):
        spec = _spec()
        schedules, _prices = _ordered_prices(spec)
        flat = {item.schedule_id: 1.0 for item in schedules}
        result, _runner, _source, _ids = _run(
            spec, _policy(minimum=2, maximum=2), tmp_path, prices=flat)

        assert result["pilot_selection"]["status"] == "capacity_exceeded"
        assert result["pilot_selection"]["selected_ids"] == []

    def test_all_no_detour_gives_no_viable_without_any_simulation(
            self, tmp_path):
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        result, runner, _source, _ids = _run(
            spec, _policy(minimum=2), tmp_path, prices=prices,
            no_detour=[item.schedule_id for item in schedules])

        assert runner.piloted == []
        assert result["pilot_selection"]["status"] == "no_viable"


class TestDurableState:
    def test_the_cost_ledger_and_cursors_are_published(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        _run(spec, _policy(minimum=2), tmp_path, prices=prices)

        workspace = load_search_workspace(tmp_path / spec.search_id)
        kinds = [item["kind"] for item in workspace.manifest["artifacts"]]
        assert kinds.count(coe.COST_LEDGER_KIND) == 1
        assert kinds.count(coe.CURSOR_KIND) == 2, (
            "one durable cursor per verification")
        for record in workspace.manifest["artifacts"]:
            if record["kind"] in {coe.COST_LEDGER_KIND, coe.CURSOR_KIND}:
                assert record["provenance"]["release_evidence"] is False

    def test_the_ledger_round_trips_and_detects_tampering(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        _run(spec, _policy(minimum=2), tmp_path, prices=prices)

        workspace = load_search_workspace(tmp_path / spec.search_id)
        record = next(item for item in workspace.manifest["artifacts"]
                      if item["kind"] == coe.COST_LEDGER_KIND)
        payload = json.loads(
            (workspace.directory / record["path"]).read_text())
        ledger = coe.CostLedger.from_dict(payload)
        assert ledger.to_dict()["content_key"] == payload["content_key"]

        payload["costs"][0]["cost"]["added_vehicle_hours"] += 1.0
        with pytest.raises(ValueError, match="content key"):
            coe.CostLedger.from_dict(payload)

    def test_the_cursor_matches_its_verified_prefix(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        _run(spec, _policy(minimum=2), tmp_path, prices=prices)

        workspace = load_search_workspace(tmp_path / spec.search_id)
        cursors = [item for item in workspace.manifest["artifacts"]
                   if item["kind"] == coe.CURSOR_KIND]
        for index, record in enumerate(cursors, start=1):
            payload = json.loads(
                (workspace.directory / record["path"]).read_text())
            cursor = coe.ExecutionCursor.from_dict(payload)
            assert cursor.state.cursor == index
            assert len(cursor.state.verified) == index
            assert (tuple(cursor.state.order[:index])
                    == tuple(cursor.state.verified))


class TestCrashRecovery:
    class _Boom(RuntimeError):
        pass

    def _crashing_runner(self, after, *, prices=None):
        outer = self

        class CrashingRunner(FakeRunner):
            def run_candidate(self, schedule, **kwargs):
                if len(self.simulated) >= after:
                    raise outer._Boom("killed mid-pilot")
                return super().run_candidate(schedule, **kwargs)

        return CrashingRunner(prices=prices)

    def test_a_crash_resumes_at_the_exact_next_candidate(self, tmp_path):
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        policy = _policy(minimum=3)

        crashing = self._crashing_runner(after=2, prices=prices)
        with pytest.raises(self._Boom):
            _run(spec, policy, tmp_path, prices=prices, runner=crashing)
        assert len(crashing.simulated) == 2

        resumed = FakeRunner(prices=prices)
        result, _runner, _source, _ids = _run(
            spec, policy, tmp_path, prices=prices, runner=resumed)

        # The resume continues at the exact next candidate: it re-pilots
        # nothing the crash already published, and it does pick up the work
        # the crash had not reached.
        assert set(resumed.piloted).isdisjoint(crashing.piloted), (
            "a resume re-piloted candidates whose evidence was published")
        assert resumed.piloted, "the resume did no remaining work"
        assert result["pilot_selection"]["status"] in {
            "ready", "capacity_exceeded"}

    def test_the_resumed_answer_equals_the_uninterrupted_one(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        policy = _policy(minimum=3)

        clean_root = tmp_path / "clean"
        clean, clean_runner, _s, _ids = _run(
            spec, policy, clean_root, prices=prices)

        crash_root = tmp_path / "crash"
        crashing = self._crashing_runner(after=1, prices=prices)
        with pytest.raises(self._Boom):
            _run(spec, policy, crash_root, prices=prices, runner=crashing)
        resumed, resumed_runner, _s2, _ids2 = _run(
            spec, policy, crash_root, prices=prices)

        assert resumed["pilot_selection"]["selected_ids"] == (
            clean["pilot_selection"]["selected_ids"])
        assert resumed["status"] == clean["status"]
        assert resumed["winner_id"] == clean["winner_id"]
        total_piloted = len(crashing.piloted) + len(resumed_runner.piloted)
        assert total_piloted == len(clean_runner.piloted), (
            "the interrupted run piloted a different number of candidates")


class TestResumeInvalidation:
    def _ledger(self, spec, prices, ids):
        source = FakeCostSource(prices)
        schedules = {item.schedule_id: item
                     for item in generate_closure_schedules(spec)}
        return coe.build_cost_ledger(
            spec, [schedules[value] for value in ids], source)

    def test_a_moved_cost_ledger_refuses_the_cursor(self, tmp_path):
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        ids = [item.schedule_id for item in schedules]
        ledger = self._ledger(spec, prices, ids)
        policy = _policy(minimum=2).pilot

        cursor = coe.ExecutionCursor(
            bound_identity=coe.bound_identity(
                ledger, policy, practical_equivalence_vehicle_hours=0.0),
            ledger_content_key=ledger.to_dict()["content_key"],
            state=coe.CostOrderedState(
                identity_key=coe.bound_identity(
                    ledger, policy,
                    practical_equivalence_vehicle_hours=0.0),
                order=tuple(sorted(ids, key=lambda v: prices[v])),
            ),
        )
        moved_prices = dict(prices)
        moved_prices[ids[0]] = 99.0
        moved = self._ledger(spec, moved_prices, ids)

        with pytest.raises(ValueError, match="resume is invalid"):
            coe.run_cost_ordered_execution(
                spec, moved, policy,
                verify=lambda _cid: pytest.fail("must not verify"),
                cursor=cursor,
            )

    def test_a_moved_policy_refuses_the_cursor(self, tmp_path):
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        ids = [item.schedule_id for item in schedules]
        ledger = self._ledger(spec, prices, ids)
        policy = _policy(minimum=2).pilot
        cursor = coe.ExecutionCursor(
            bound_identity=coe.bound_identity(
                ledger, policy, practical_equivalence_vehicle_hours=0.0),
            ledger_content_key=ledger.to_dict()["content_key"],
            state=coe.CostOrderedState(
                identity_key="stale", order=tuple(ids)),
        )
        with pytest.raises(ValueError, match="resume is invalid"):
            coe.run_cost_ordered_execution(
                spec, ledger, _policy(minimum=3).pilot,
                verify=lambda _cid: pytest.fail("must not verify"),
                cursor=cursor,
            )

    def test_resume_identity_uses_cost_order_not_calendar_order(self):
        """A real cursor must be accepted when ledger order is not cost order.

        The v3 real benchmark exposed this: the durable wrapper hashed the
        calendar-ordered ledger while the state machine hashed its sorted,
        no-detour-filtered scan.  The first run worked because it had no state;
        the first resume then rejected its own cursor.
        """
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        parents = list(reversed(schedules[:4]))
        ledger = coe.build_cost_ledger(
            spec,
            parents,
            FakeCostSource(prices, no_detour=(parents[1].schedule_id,)),
        )
        policy = _policy(minimum=2).pilot
        saved = {}
        evidence = {}

        class Interrupted(RuntimeError):
            pass

        def verify(candidate_id):
            item = CandidateEvidence(
                candidate_id=candidate_id,
                observations=tuple(PairedObservation(
                    candidate_id=candidate_id,
                    demand_variant=variant,
                    seed=1000 + index,
                    baseline_time_loss_s=1.0,
                    candidate_time_loss_s=2.0,
                    matched_baseline_id="b",
                    provenance_key="p",
                ) for index, variant in enumerate(("q10", "q50", "q90"))),
                disruption=_variant_records(prices[candidate_id]),
            )
            evidence[candidate_id] = item
            return item

        def checkpoint(cursor, _candidate_id, _item):
            saved["cursor"] = cursor
            raise Interrupted

        with pytest.raises(Interrupted):
            coe.run_cost_ordered_execution(
                spec, ledger, policy, verify=verify, checkpoint=checkpoint)

        resumed = coe.run_cost_ordered_execution(
            spec,
            ledger,
            policy,
            verify=verify,
            cursor=saved["cursor"],
            verified_evidence=evidence,
        )
        assert resumed.state.cursor == len(resumed.state.verified)
        assert set(resumed.state.verified).isdisjoint(
            {parents[1].schedule_id})

    def test_a_ledger_from_another_search_is_refused(self, tmp_path):
        spec = _spec()
        other = _spec(required_work_minutes=2 * 60)
        schedules, prices = _ordered_prices(spec)
        ids = [item.schedule_id for item in schedules]
        ledger = self._ledger(spec, prices, ids)

        with pytest.raises(ValueError, match="another search"):
            coe.run_cost_ordered_execution(
                other, ledger, _policy().pilot,
                verify=lambda _cid: pytest.fail("must not verify"),
            )

    def test_a_resume_missing_published_evidence_fails_closed(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        policy = _policy(minimum=3)

        crashing = TestCrashRecovery()._crashing_runner(after=2, prices=prices)
        with pytest.raises(RuntimeError):
            _run(spec, policy, tmp_path, prices=prices, runner=crashing)

        # Remove one published pilot artifact but keep the cursor claiming it.
        workspace = load_search_workspace(tmp_path / spec.search_id,
                                          verify=False)
        record = next(item for item in workspace.manifest["artifacts"]
                      if item["kind"] == "monthly_pilot_candidate")
        workspace.manifest["artifacts"].remove(record)
        (workspace.directory / record["path"]).unlink()
        workspace._flush()

        with pytest.raises(ValueError, match="evidence is missing"):
            _run(spec, policy, tmp_path, prices=prices)


class TestProgressReporting:
    def test_the_real_phases_are_reported(self, tmp_path):
        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        seen: list[tuple[str, dict]] = []

        from traffic_sim.simulation import search_workspace as sw
        original = sw.SearchWorkspace.update_progress

        def recording(self, phase, **kwargs):
            seen.append((phase, dict(kwargs.get("detail") or {})))
            return original(self, phase, **kwargs)

        sw.SearchWorkspace.update_progress = recording
        try:
            _run(spec, _policy(minimum=2), tmp_path, prices=prices)
        finally:
            sw.SearchWorkspace.update_progress = original

        phases = [phase for phase, _detail in seen]
        for phase in ("cost_units", "cost_parents", "health_scan", "pilot"):
            assert phase in phases, phase

        health = next(detail for phase, detail in seen
                      if phase == "health_scan")
        assert "deterministically_disqualified" in health
        pilot = [detail for phase, detail in seen if phase == "pilot"
                 and "verified" in detail]
        assert pilot and pilot[-1]["verified"] >= 1

    def test_every_reported_phase_is_declared(self, tmp_path):
        from traffic_sim.simulation.monthly_search import PROGRESS_PHASES

        spec = _spec()
        _schedules, prices = _ordered_prices(spec)
        seen: list[str] = []

        from traffic_sim.simulation import search_workspace as sw
        original = sw.SearchWorkspace.update_progress

        def recording(self, phase, **kwargs):
            seen.append(phase)
            return original(self, phase, **kwargs)

        sw.SearchWorkspace.update_progress = recording
        try:
            _run(spec, _policy(minimum=2), tmp_path, prices=prices)
        finally:
            sw.SearchWorkspace.update_progress = original

        assert set(seen) <= set(PROGRESS_PHASES), (
            set(seen) - set(PROGRESS_PHASES))


class TestObjectiveGuard:
    def test_the_legacy_objective_cannot_be_cost_ordered(self, tmp_path):
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        payload = _policy().to_dict()
        payload["objective_method"] = "legacy_time_loss_v1"
        payload.pop("content_key", None)
        policy = MonthlySearchPolicy.from_dict(payload)
        with pytest.raises(ValueError, match="closure_cost_v1"):
            _run(spec, policy, tmp_path, prices=prices)


class TestExecutionRecord:
    def test_it_reports_the_saving_as_a_measured_pair(self, tmp_path):
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        ids = [item.schedule_id for item in schedules]
        source = FakeCostSource(prices)
        ledger = coe.build_cost_ledger(spec, schedules, source)
        policy = _policy(minimum=2).pilot

        simulated: list[str] = []

        def verify(candidate_id):
            simulated.append(candidate_id)
            return CandidateEvidence(
                candidate_id=candidate_id,
                observations=tuple(PairedObservation(
                    candidate_id=candidate_id,
                    demand_variant=variant,
                    seed=1000 + index,
                    baseline_time_loss_s=1.0,
                    candidate_time_loss_s=2.0,
                    matched_baseline_id="b",
                    provenance_key="p",
                ) for index, variant in enumerate(("q10", "q50", "q90"))),
                disruption=_variant_records(prices[candidate_id]),
            )

        result = coe.run_cost_ordered_execution(
            spec, ledger, policy, verify=verify)
        record = coe.execution_record(
            spec, ledger, result,
            exhaustive_candidate_count=len(ids),
            wall_time_s=1.25,
            peak_rss_bytes=12345,
        )

        assert record["release_evidence"] is False
        assert record["evidence_class"] == "product_execution"
        assert record["cost_ordered_sumo_candidates"] == len(simulated)
        assert record["exhaustive_sumo_candidates"] == len(ids)
        assert record["sumo_verifications_saved"] == (
            len(ids) - len(simulated))
        assert record["sumo_verifications_saved"] > 0
        assert record["stop_proof"]["stop_reason"] == "band_exhausted"
        assert record["wall_time_s"] == 1.25
        assert record["peak_rss_bytes"] == 12345
        json.dumps(record)


class TestTheDurableCursorMirrorsTheScan:
    """The cursor is reconstructed, so it must be proved to match.

    `cost_ordered_search.py` is bound by the frozen golden record and stays
    byte-identical, so the durable position is mirrored in the execution layer
    from the same inputs and the same rule. A mirror is only safe if divergence
    is detected, which is what these tests pin.
    """

    def test_the_mirror_matches_the_scan_on_every_candidate(self, tmp_path):
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        ids = [item.schedule_id for item in schedules]
        source = FakeCostSource(prices)
        ledger = coe.build_cost_ledger(spec, schedules, source)
        policy = _policy(minimum=3).pilot

        seen: list[tuple[int, tuple[str, ...]]] = []

        def checkpoint(cursor, candidate_id, evidence):
            seen.append((cursor.state.cursor, tuple(cursor.state.verified)))

        def verify(candidate_id):
            return CandidateEvidence(
                candidate_id=candidate_id,
                observations=tuple(PairedObservation(
                    candidate_id=candidate_id,
                    demand_variant=variant,
                    seed=1000 + index,
                    baseline_time_loss_s=1.0,
                    candidate_time_loss_s=2.0,
                    matched_baseline_id="b",
                    provenance_key="p",
                ) for index, variant in enumerate(("q10", "q50", "q90"))),
                disruption=_variant_records(prices[candidate_id]),
            )

        result = coe.run_cost_ordered_execution(
            spec, ledger, policy, verify=verify, checkpoint=checkpoint)

        assert seen
        for index, (cursor_value, verified) in enumerate(seen, start=1):
            assert cursor_value == index
            assert verified == tuple(result.state.verified[:index])
        assert seen[-1][1] == tuple(result.state.verified)

    def test_a_diverging_mirror_is_refused(self, tmp_path, monkeypatch):
        """If the scan and the mirror ever disagree, the run must stop."""
        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        source = FakeCostSource(prices)
        ledger = coe.build_cost_ledger(spec, schedules, source)
        policy = _policy(minimum=2).pilot

        real = coe.run_cost_ordered_search

        def sabotaged(*args, **kwargs):
            outcome = real(*args, **kwargs)
            # Pretend the scan verified one more candidate than the mirror saw.
            state = outcome.state
            broken = coe.CostOrderedState(
                identity_key=state.identity_key,
                order=state.order,
                cursor=state.cursor,
                verified=state.verified + ("ghost-candidate",),
                viable=state.viable,
                cutoff=state.cutoff,
                stop_reason=state.stop_reason,
            )
            return type(outcome)(
                selection=outcome.selection,
                state=broken,
                ordered_costs=outcome.ordered_costs,
                disqualified=outcome.disqualified,
                evidence=outcome.evidence,
                verified_count=outcome.verified_count,
                cache_hits=outcome.cache_hits,
                stop_proof=outcome.stop_proof,
            )

        monkeypatch.setattr(coe, "run_cost_ordered_search", sabotaged)

        def verify(candidate_id):
            return CandidateEvidence(
                candidate_id=candidate_id,
                observations=tuple(PairedObservation(
                    candidate_id=candidate_id,
                    demand_variant=variant,
                    seed=1000 + index,
                    baseline_time_loss_s=1.0,
                    candidate_time_loss_s=2.0,
                    matched_baseline_id="b",
                    provenance_key="p",
                ) for index, variant in enumerate(("q10", "q50", "q90"))),
                disruption=_variant_records(prices[candidate_id]),
            )

        with pytest.raises(ValueError, match="diverged"):
            coe.run_cost_ordered_execution(
                spec, ledger, policy, verify=verify)
