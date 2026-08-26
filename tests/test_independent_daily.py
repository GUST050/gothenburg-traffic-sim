import pytest

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSchedule,
    ClosureSearchSpec,
    DailyTimeBand,
    write_closure_search_spec,
)
from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    PairedObservation,
)
from traffic_sim.simulation.independent_daily import (
    IndependentDailyRunner,
    IsolatedDailySumoRunner,
    aggregate_daily_evidence,
    decompose_schedules,
)
from traffic_sim.simulation.monthly_search import canonical_seed, run_monthly_search
from run_monthly_closure_search import (
    _independent_exhaustive_builder,
    _independent_exhaustive_preflight,
)


def _spec(**overrides):
    values = {
        "search_id": "independent-daily-test",
        "directed_edges": ("a_b_0",),
        "demand_build_id": "forecast-2027",
        "source": "forecast",
        "permitted_date_start": "2027-01-01",
        "permitted_date_end": "2027-01-04",
        "required_work_minutes": 4 * 60,
        "max_consecutive_start_days": 2,
        "permitted_daily_band": DailyTimeBand("15:00", "18:00"),
        "allowed_weekdays": (0, 1, 2, 3, 4, 5, 6),
        "interday_policy": "independent_daily_reset_v1",
        "work_allocation_policy": "exact_balanced_daily_v1",
    }
    values.update(overrides)
    return ClosureSearchSpec(**values)


class FakeDailyRunner:
    def __init__(self):
        self.calls = []
        self.prepared = ()

    def prepare(self, schedules):
        self.prepared = tuple(schedules)

    def provenance(self):
        return {"kind": "fake-daily-sumo", "identity": "v1"}

    def run_candidate(self, schedule, *, target_repetitions, existing, stage):
        self.calls.append((schedule.schedule_id, stage, existing is not None))
        observations = []
        date_number = int(schedule.first_work_date[-2:])
        duration = schedule.actual_closed_minutes
        for variant in ("q10", "q50", "q90"):
            for repetition in range(target_repetitions[variant]):
                seed = canonical_seed(variant, repetition)
                baseline = 1000.0 + date_number
                observations.append(PairedObservation(
                    candidate_id=schedule.schedule_id,
                    demand_variant=variant,
                    seed=seed,
                    baseline_time_loss_s=baseline,
                    candidate_time_loss_s=baseline + duration,
                    matched_baseline_id=f"baseline-{schedule.first_work_date}",
                    provenance_key=f"daily-{schedule.first_work_date}",
                ))
        return CandidateEvidence(
            candidate_id=schedule.schedule_id,
            observations=tuple(observations),
        )


def test_decomposition_deduplicates_units_shared_by_overlapping_schedules():
    spec = _spec()
    schedules = [
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    ]
    units, parents = decompose_schedules(spec, schedules)

    assert len(schedules) == 3
    assert len(units) == 4
    assert all(len(parents[item.schedule_id]) == 2 for item in schedules)
    middle = next(
        item for item in units if item.identity["work_date"] == "2027-01-02"
    )
    assert len(middle.parent_schedule_ids) == 2
    assert middle.identity["directed_edges"] == ["a_b_0"]


def test_decomposition_rejects_duplicate_parent_identity():
    spec = _spec()
    schedule = generate_closure_schedules(spec)[0]
    with pytest.raises(ValueError, match="repeats a parent"):
        decompose_schedules(spec, (schedule, schedule))


def test_daily_aggregation_sums_matched_pairs_before_decision():
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    parent = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    units, _ = decompose_schedules(spec, (parent,))
    evidence = {}
    for index, unit in enumerate(units):
        evidence[unit.unit_id] = CandidateEvidence(
            candidate_id=unit.schedule.schedule_id,
            observations=(PairedObservation(
                candidate_id=unit.schedule.schedule_id,
                demand_variant="q10",
                seed=1000,
                baseline_time_loss_s=10.0 + index,
                candidate_time_loss_s=15.0 + index,
                matched_baseline_id=f"baseline-{index}",
                provenance_key=f"provenance-{index}",
            ),),
        )

    combined = aggregate_daily_evidence(parent, units, evidence)

    assert combined.candidate_id == parent.schedule_id
    assert combined.observations[0].baseline_time_loss_s == 21.0
    assert combined.observations[0].candidate_time_loss_s == 31.0
    assert combined.observations[0].delta_time_loss_s == 10.0
    assert combined.observations[0].matched_baseline_id.startswith(
        "independent-daily-baseline-")


def test_daily_aggregation_sums_closure_cost_by_demand_variant():
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    parent = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    units, _ = decompose_schedules(spec, (parent,))
    evidence = {}
    for index, unit in enumerate(units, start=1):
        records = tuple({
            "demand_variant": variant,
            "vehicles_affected": index,
            "vehicles_no_detour": 0,
            "added_vehicle_hours": index / 10,
            "added_metres_total": index * 100,
        } for variant in ("q10", "q50", "q90"))
        evidence[unit.unit_id] = CandidateEvidence(
            candidate_id=unit.schedule.schedule_id,
            observations=(PairedObservation(
                candidate_id=unit.schedule.schedule_id,
                demand_variant="q10",
                seed=1000,
                baseline_time_loss_s=10,
                candidate_time_loss_s=11,
                matched_baseline_id=f"baseline-{index}",
                provenance_key=f"provenance-{index}",
            ),),
            disruption=records,
        )

    combined = aggregate_daily_evidence(parent, units, evidence)

    assert len(combined.disruption) == 3
    assert combined.disruption[0]["demand_variant"] == "q10"
    assert combined.disruption[0]["added_vehicle_hours"] == 0.3
    assert combined.disruption[0]["added_metres_total"] == 300.0


def test_runner_persists_daily_units_and_reuses_them_across_schedules(tmp_path):
    spec = _spec()
    schedules = [
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    ]
    child = FakeDailyRunner()
    runner = IndependentDailyRunner(
        spec,
        daily_runner=child,
        cache_root=tmp_path / "daily-cache",
    )
    runner.prepare(schedules)
    targets = {"q10": 1, "q50": 1, "q90": 1}

    first = runner.run_candidate(
        schedules[0], target_repetitions=targets, existing=None, stage="pilot")
    second = runner.run_candidate(
        schedules[1], target_repetitions=targets, existing=None, stage="pilot")

    assert len(first.observations) == 3
    assert len(second.observations) == 3
    # Two units for the first schedule; the overlapping middle date is loaded
    # from cache, so only one additional child run is required for the second.
    assert len(child.calls) == 3
    assert child.calls[-1][2] is False
    assert len(tuple((tmp_path / "daily-cache").glob("*/*.json"))) == 3


def test_cache_reuses_exact_unit_across_different_parent_searches(tmp_path):
    first_spec = _spec(
        search_id="first-parent",
        permitted_date_end="2027-01-02",
    )
    second_spec = _spec(
        search_id="second-parent",
        permitted_date_end="2027-01-03",
    )
    first_schedule = next(
        item for item in generate_closure_schedules(first_spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    second_schedule = next(
        item for item in generate_closure_schedules(second_spec)
        if item.day_count == 2 and item.daily_start == "15:00"
        and item.first_work_date == "2027-01-01"
    )
    targets = {"q10": 1, "q50": 1, "q90": 1}

    first_child = FakeDailyRunner()
    first = IndependentDailyRunner(
        first_spec,
        daily_runner=first_child,
        cache_root=tmp_path / "daily-cache",
    )
    first.prepare((first_schedule,))
    first.run_candidate(
        first_schedule,
        target_repetitions=targets,
        existing=None,
        stage="pilot",
    )

    second_child = FakeDailyRunner()
    second = IndependentDailyRunner(
        second_spec,
        daily_runner=second_child,
        cache_root=tmp_path / "daily-cache",
    )
    second.prepare((second_schedule,))
    evidence = second.run_candidate(
        second_schedule,
        target_repetitions=targets,
        existing=None,
        stage="pilot",
    )

    assert len(first_child.calls) == 2
    assert second_child.calls == []
    assert evidence.candidate_id == second_schedule.schedule_id
    assert all(
        item.candidate_id == second_schedule.schedule_id
        for item in evidence.observations
    )


def test_larger_cached_replication_set_can_serve_smaller_request(tmp_path):
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    schedule = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    first_child = FakeDailyRunner()
    first = IndependentDailyRunner(
        spec, daily_runner=first_child, cache_root=tmp_path / "daily-cache"
    )
    first.prepare((schedule,))
    first.run_candidate(
        schedule,
        target_repetitions={"q10": 2, "q50": 2, "q90": 2},
        existing=None,
        stage="finalist",
    )

    second_child = FakeDailyRunner()
    second = IndependentDailyRunner(
        spec, daily_runner=second_child, cache_root=tmp_path / "daily-cache"
    )
    second.prepare((schedule,))
    evidence = second.run_candidate(
        schedule,
        target_repetitions={"q10": 1, "q50": 1, "q90": 1},
        existing=None,
        stage="pilot",
    )

    assert second_child.calls == []
    assert len(evidence.observations) == 3
    timing = second.timing_snapshot()
    assert timing["cache_hits"] == 2
    assert timing["units_simulated"] == 0
    assert timing["worker_seconds"] == 0.0


def test_non_object_cache_is_a_safe_miss_and_is_repaired(tmp_path):
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    schedule = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    targets = {"q10": 1, "q50": 1, "q90": 1}
    first = IndependentDailyRunner(
        spec, daily_runner=FakeDailyRunner(), cache_root=tmp_path / "cache"
    )
    first.prepare((schedule,))
    first.run_candidate(
        schedule, target_repetitions=targets, existing=None, stage="pilot"
    )
    damaged = sorted((tmp_path / "cache").glob("*/*.json"))[0]
    damaged.write_text("[]", encoding="utf-8")

    child = FakeDailyRunner()
    resumed = IndependentDailyRunner(
        spec, daily_runner=child, cache_root=tmp_path / "cache"
    )
    resumed.prepare((schedule,))
    resumed.run_candidate(
        schedule, target_repetitions=targets, existing=None, stage="pilot"
    )
    assert len(child.calls) == 1
    assert damaged.read_text(encoding="utf-8").lstrip().startswith("{")
    timing = resumed.timing_snapshot()
    assert timing["cache_corrupt"] == 1
    assert timing["cache_hits"] == 1
    assert timing["cache_misses"] == 1
    assert timing["cache_hits"] + timing["cache_misses"] == 2
    assert timing["units_simulated"] == 1


def test_isolated_runner_batches_units_and_keeps_exact_evidence():
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    parent = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    units, _ = decompose_schedules(spec, (parent,))

    class Delegate(FakeDailyRunner):
        def candidate_execution_contract(self, schedule):
            return {"schedule_id": schedule.schedule_id}

        def candidate_provenance(self, schedule):
            return {
                "kind": "fake-daily-sumo",
                "search_content_key": spec.content_key,
                "archive_digest": schedule.first_work_date,
            }

    delegate = Delegate()
    calls = []

    def invoke(request):
        schedule = ClosureSchedule.from_dict(request["schedule"])
        calls.append(schedule.schedule_id)
        return delegate.run_candidate(
            schedule,
            target_repetitions=request["target_repetitions"],
            existing=None,
            stage=request["stage"],
        )

    isolated = IsolatedDailySumoRunner(
        delegate, unit_workers=2, worker_invoker=invoke
    )
    isolated.prepare([unit.schedule for unit in units])
    result = isolated.run_candidate_batch([
        (
            unit.schedule,
            {"q10": 1, "q50": 1, "q90": 1},
            None,
            "pilot",
        )
        for unit in units
    ])

    assert set(result) == {unit.schedule.schedule_id for unit in units}
    assert set(calls) == set(result)
    assert all(len(evidence.observations) == 3 for evidence in result.values())


def test_aggregation_rejects_different_seed_coverage():
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    parent = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    units, _ = decompose_schedules(spec, (parent,))
    evidence = {
        units[0].unit_id: CandidateEvidence(
            candidate_id=units[0].schedule.schedule_id,
            observations=(),
        ),
        units[1].unit_id: CandidateEvidence(
            candidate_id=units[1].schedule.schedule_id,
            observations=(PairedObservation(
                candidate_id=units[1].schedule.schedule_id,
                demand_variant="q10",
                seed=1000,
                baseline_time_loss_s=1,
                candidate_time_loss_s=2,
                matched_baseline_id="b",
                provenance_key="p",
            ),),
        ),
    }

    with pytest.raises(ValueError, match="coverage differs"):
        aggregate_daily_evidence(parent, units, evidence)


def test_daily_hard_failure_disqualifies_parent_without_seed_fabrication():
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    parent = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    units, _ = decompose_schedules(spec, (parent,))
    evidence = {
        units[0].unit_id: CandidateEvidence(
            candidate_id=units[0].schedule.schedule_id,
            hard_failures=("no_viable_detour",),
        ),
        units[1].unit_id: CandidateEvidence(
            candidate_id=units[1].schedule.schedule_id,
            observations=(PairedObservation(
                candidate_id=units[1].schedule.schedule_id,
                demand_variant="q10",
                seed=1000,
                baseline_time_loss_s=1,
                candidate_time_loss_s=2,
                matched_baseline_id="b",
                provenance_key="p",
            ),),
        ),
    }

    combined = aggregate_daily_evidence(parent, units, evidence)

    assert combined.observations == ()
    assert combined.hard_failures == (
        "2027-01-01:no_viable_detour",
    )


def test_cached_daily_hard_failure_is_terminal_and_reused(tmp_path):
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    schedule = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )

    class FailingDailyRunner(FakeDailyRunner):
        def run_candidate(
            self, schedule, *, target_repetitions, existing, stage
        ):
            self.calls.append((schedule.schedule_id, stage, existing is not None))
            return CandidateEvidence(
                candidate_id=schedule.schedule_id,
                hard_failures=("no_viable_detour",),
            )

    targets = {"q10": 1, "q50": 1, "q90": 1}
    first_child = FailingDailyRunner()
    first = IndependentDailyRunner(
        spec, daily_runner=first_child, cache_root=tmp_path / "daily-cache"
    )
    first.prepare((schedule,))
    first_result = first.run_candidate(
        schedule,
        target_repetitions=targets,
        existing=None,
        stage="pilot",
    )

    second_child = FailingDailyRunner()
    second = IndependentDailyRunner(
        spec, daily_runner=second_child, cache_root=tmp_path / "daily-cache"
    )
    second.prepare((schedule,))
    second_result = second.run_candidate(
        schedule,
        target_repetitions=targets,
        existing=None,
        stage="pilot",
    )

    assert len(first_child.calls) == 2
    assert second_child.calls == []
    assert first_result.hard_failures == second_result.hard_failures
    assert len(second_result.hard_failures) == 2


def test_independent_exhaustive_screening_binds_unique_daily_work(tmp_path):
    spec = _spec()
    path = tmp_path / "spec.json"
    write_closure_search_spec(path, spec)

    artifact = _independent_exhaustive_builder(
        path,
        maximum_candidates=100,
        maximum_daily_units=100,
        baseline_trip_duration_p99_s=1800,
    )

    assert artifact["proxy_version"] == \
        "independent_daily_exhaustive_sumo_v1"
    assert artifact["candidate_count"] == len(generate_closure_schedules(spec))
    assert artifact["shortlist"]["selection_complete"] is True
    assert len(artifact["shortlist"]["entries"]) == artifact["candidate_count"]
    assert artifact["independent_daily_execution"] == {
        "interday_policy": "independent_daily_reset_v1",
        "work_allocation_policy": "exact_balanced_daily_v1",
        "unique_daily_unit_count": 20,
        "executable_daily_unit_count": 20,
        "unavailable_daily_unit_count": 0,
        "maximum_daily_units": 100,
    }

    with pytest.raises(ValueError, match="unique daily SUMO units"):
        _independent_exhaustive_builder(
            path,
            maximum_candidates=100,
            maximum_daily_units=19,
            baseline_trip_duration_p99_s=1800,
        )


def test_independent_exhaustive_preflight_rejects_before_enumeration():
    spec = _spec(work_allocation_policy="exact_equal_daily_v1")

    with pytest.raises(ValueError, match="preflight counted.*parent schedules"):
        _independent_exhaustive_preflight(
            spec,
            maximum_candidates=1,
            maximum_daily_units=100,
            baseline_trip_duration_p99_s=1800,
        )

    with pytest.raises(ValueError, match="preflight counted.*daily SUMO units"):
        _independent_exhaustive_preflight(
            spec,
            maximum_candidates=100,
            maximum_daily_units=1,
            baseline_trip_duration_p99_s=1800,
        )


def test_independent_cli_rejects_before_network_or_search_workspace(monkeypatch):
    """The product path must use preflight before candidate-ledger creation.

    Strengthened alongside the import split: the refusal now has to happen
    before the demand workspace lock is taken AND before the SUMO-side stack is
    imported at all, so an over-budget search costs a preflight rather than
    ~110 MiB of numpy/pandas/SciPy and a wait for a lock a real build may hold.
    """
    from types import SimpleNamespace

    import run_monthly_closure_search as command
    from tests.test_monthly_search import _policy

    spec = _spec(work_allocation_policy="exact_equal_daily_v1")
    args = SimpleNamespace(
        baseline_trip_duration_p99_s=1800,
        bounded_exhaustive_cap=12,
        independent_exhaustive_candidate_cap=1,
        independent_exhaustive_daily_cap=100,
        daily_unit_budget=None,
        daily_unit_total_cap=100_000,
        seed_workers=1,
        daily_workers=1,
        max_active_sumo_slots=8,
        warm_execution=False,
        workspace_wait_s=0,
        screening_mode="independent-exhaustive",
        spec="unused-spec.json",
        policy="unused-policy.json",
    )

    class Lock:
        released = False

        def __init__(self, _owner):
            pass

        def acquire(self, **_kwargs):
            return True

        def release(self):
            Lock.released = True

    acquired: list[bool] = []
    Lock.acquire = lambda self, **_kwargs: (acquired.append(True) or True)

    monkeypatch.setattr(command, "parse_args", lambda: args)
    monkeypatch.setattr(command, "approved_seed_workers", lambda: 1)
    monkeypatch.setattr(command, "WorkspaceLock", Lock)
    monkeypatch.setattr(command, "load_closure_search_spec", lambda _path: spec)
    monkeypatch.setattr(command, "_read", lambda _path: _policy().to_dict())
    monkeypatch.setattr(
        command,
        "sha256_file",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("network identity must not run before preflight")),
    )
    monkeypatch.setattr(
        command,
        "_simulation_backends",
        lambda: (_ for _ in ()).throw(
            AssertionError("the SUMO stack must not be imported before "
                           "preflight")),
    )

    with pytest.raises(SystemExit, match="preflight counted.*parent schedules"):
        command.main()
    assert acquired == [], "the demand lock must not be taken for a refusal"
    assert Lock.released is False


def test_independent_screening_excludes_only_out_of_year_envelopes(tmp_path):
    spec = _spec(
        permitted_date_start="2027-12-30",
        permitted_date_end="2027-12-31",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("20:00", "23:00"),
        allowed_weekdays=(3, 4),
    )
    path = tmp_path / "year-end.json"
    write_closure_search_spec(path, spec)

    artifact = _independent_exhaustive_builder(
        path,
        maximum_candidates=100,
        maximum_daily_units=100,
        baseline_trip_duration_p99_s=1800,
    )

    selected = {
        item["schedule_id"] for item in artifact["shortlist"]["entries"]
    }
    generated = generate_closure_schedules(spec)
    assert len(generated) == 18
    assert artifact["candidate_count"] == 18
    assert artifact["scoreable_candidate_count"] == 9
    assert len(artifact["unavailable_candidates"]) == 9
    assert all(
        item.schedule_id in selected
        for item in generated
        if item.first_work_date == "2027-12-30"
    )
    assert all(
        item.schedule_id not in selected
        for item in generated
        if item.first_work_date == "2027-12-31"
    )

    from traffic_sim.simulation.monthly_search import _claim_boundary
    boundary = _claim_boundary(
        artifact,
        "unique_winner",
        heldout_gate={
            "proxy_version": "monthly_proxy_v1",
            "heldout_set": "independent-v1",
            "interday_policy": "independent_daily_reset_v1",
        },
    )
    assert boundary["ui_exposure_allowed"] is True
    assert boundary["global_best_claim_allowed"] is False
    assert boundary["best_result_scope"] == \
        "sumo_verified_independent_daily_available_schedules"
    assert "9 unavailable schedule(s)" in boundary["reason"]


def test_independent_exhaustive_search_is_ui_visible_without_proxy_claim(
    tmp_path,
):
    from tests.test_monthly_search import _policy

    spec = _spec(search_id="independent-exhaustive-integration")
    child = FakeDailyRunner()
    runner = IndependentDailyRunner(
        spec,
        daily_runner=child,
        cache_root=tmp_path / "daily-cache",
    )
    result = run_monthly_search(
        spec,
        _policy(),
        runner=runner,
        screen_builder=lambda path: _independent_exhaustive_builder(
            path,
            maximum_candidates=100,
            maximum_daily_units=100,
            baseline_trip_duration_p99_s=1800,
        ),
        root=tmp_path / "search",
    )

    boundary = result["claim_boundary"]
    assert boundary["ui_exposure_allowed"] is True
    assert boundary["global_best_claim_allowed"] is False
    assert result["screening"]["candidate_count"] == 15
    assert list((tmp_path / "search").rglob("pilot/*.json")) == []
    assert len(list((tmp_path / "search").rglob("pilot-selection.json"))) == 1
    assert result["pilot_selection"]["candidate_count"] == 15
    assert len(result["pilot_selection"]["candidates"]) <= 12
    assert len(result["shortlisted_schedules"]) <= 12

    from traffic_sim.simulation.monthly_search import _claim_boundary
    unique = _claim_boundary(
        {"proxy_version": "independent_daily_exhaustive_sumo_v1"},
        "unique_winner",
        heldout_gate=None,
    )
    assert unique["best_result_scope"] == \
        "sumo_verified_independent_daily_exhaustive"
    legacy_gate = _claim_boundary(
        {"proxy_version": "independent_daily_exhaustive_sumo_v1"},
        "unique_winner",
        heldout_gate={
            "proxy_version": "monthly_proxy_v1",
            "heldout_set": "legacy-continuous",
        },
    )
    assert legacy_gate["global_best_claim_allowed"] is False

    provisional = _claim_boundary(
        {"proxy_version": "independent_daily_exhaustive_sumo_v1"},
        "unique_winner",
        heldout_gate={
            "interday_policy": "independent_daily_reset_v1",
            "heldout_set": "future-week-gate",
        },
        policy_status="provisional",
        objective_method="closure_cost_v1",
    )
    assert provisional["ui_exposure_allowed"] is True
    assert provisional["global_best_claim_allowed"] is False
    assert "golden-frozen" in provisional["reason"]


def test_server_routes_independent_search_to_exact_exhaustive_mode():
    from serve import (
        MONTHLY_DAILY_UNIT_BUDGET,
        MONTHLY_PARENT_SCHEDULE_CAP,
        MONTHLY_TOTAL_DAILY_UNIT_CAP,
        monthly_screening_cli_args,
    )

    assert monthly_screening_cli_args(_spec()) == [
        "--screening-mode",
        "independent-exhaustive",
        "--daily-unit-budget",
        str(MONTHLY_DAILY_UNIT_BUDGET),
        "--daily-unit-total-cap",
        str(MONTHLY_TOTAL_DAILY_UNIT_CAP),
        "--independent-exhaustive-candidate-cap",
        str(MONTHLY_PARENT_SCHEDULE_CAP),
    ]
