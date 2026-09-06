import pytest
import json
from datetime import datetime

from traffic_sim.simulation import deterministic_disruption
from traffic_sim.simulation import disruption

from traffic_sim.simulation import independent_daily as independent_daily_module
from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSchedule,
    ClosureSearchSpec,
    DailyTimeBand,
    write_closure_search_spec,
)
from traffic_sim.simulation.finalist_decision import (
    CanonicalObservationDigest,
    RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
    TIMEOUT_IDENTITY_SCHEMA,
    CandidateEvidence,
    PairedObservation,
    TimeoutIdentity,
)
from traffic_sim.simulation.independent_daily import (
    CompatibleDailyCacheImport,
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


def test_compatible_recovery_imports_completed_units_and_resumes_timeout(
        tmp_path):
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    parent = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    source_root = tmp_path / "source-cache"
    source_child = FakeDailyRunner()
    source = IndependentDailyRunner(
        spec, daily_runner=source_child, cache_root=source_root)
    source.prepare((parent,))
    units = [source._units[key] for key in sorted(source._units)]
    completed, timed_out = units
    source._save_cached(completed, source_child.run_candidate(
        completed.schedule,
        target_repetitions={"q10": 1, "q50": 1, "q90": 0},
        existing=None,
        stage="pilot",
    ))
    timeout_identity = TimeoutIdentity(
        schema=TIMEOUT_IDENTITY_SCHEMA,
        candidate_id=timed_out.schedule.schedule_id,
        work_date=timed_out.schedule.first_work_date,
        search_content_key=timed_out.schedule.search_content_key,
        variant="q50",
        seed=1001,
        attempt=1,
        threshold_s=300.0,
        retry_protocol=RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
        search_provenance_key="old-study",
    )
    source._save_cached(timed_out, CandidateEvidence(
        candidate_id=timed_out.schedule.schedule_id,
        observations=(PairedObservation(
            candidate_id=timed_out.schedule.schedule_id,
            demand_variant="q10",
            seed=1000,
            baseline_time_loss_s=1000.0,
            candidate_time_loss_s=1010.0,
            matched_baseline_id="baseline-2027-01-02",
            provenance_key=f"daily-{completed.schedule.first_work_date}",
        ),),
        hard_failures=(
            "sumo_execution_failure:q50:1001:sumo timed out after 300s "
            "(seed 1001)",
        ),
        timeout_undecided=(timeout_identity,),
    ))

    recovery = CompatibleDailyCacheImport(
        source_root=source_root.resolve(),
        source_search_id="old-search",
        source_search_content_key=spec.content_key,
        source_backend_artifact_sha256="a" * 64,
        source_unit_backend_digests=dict(source._unit_backend_digests),
    )
    # Same backend identity as the source: this is a same-code resume from
    # another workspace root, not a translation across a policy change.
    recovered_child = FakeDailyRunner()
    destination = tmp_path / "recovery-cache"
    runner = IndependentDailyRunner(
        spec,
        daily_runner=recovered_child,
        cache_root=destination,
        compatible_cache_import=recovery,
    )
    runner.prepare((parent,))

    manifest = json.loads(
        (destination / "compatible-import-old-search.json").read_text()
    )
    assert manifest["imported_completed_units"] == 1
    assert manifest["imported_partial_units"] == 1
    assert manifest["recovery_pending_units"] == 1
    assert manifest["skipped_empty_timeout_units"] == 0
    assert manifest["skipped_incompatible_backend_units"] == 0
    sanitized = runner._load_cached(timed_out, count=False)
    assert sanitized is not None
    assert len(sanitized.observations) == 1
    assert not sanitized.hard_failures
    assert not sanitized.timeout_undecided
    result = runner.run_candidate(
        parent,
        target_repetitions={"q10": 1, "q50": 1, "q90": 0},
        existing=None,
        stage="pilot",
    )
    assert len(result.observations) == 2
    assert len(recovered_child.calls) == 1
    assert recovered_child.calls[0][0] == timed_out.schedule.schedule_id


def test_compatible_recovery_rejects_units_with_a_different_backend_digest(
        tmp_path):
    """A routing-policy (or any other backend) change must never re-key old
    evidence under the new policy's cache lookup -- it must be treated as a
    normal cache miss instead, exactly the failure mode the review found:
    old candidate observations silently satisfying a new-policy lookup."""
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    parent = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    source_root = tmp_path / "source-cache"
    source_child = FakeDailyRunner()
    source = IndependentDailyRunner(
        spec, daily_runner=source_child, cache_root=source_root)
    source.prepare((parent,))
    units = [source._units[key] for key in sorted(source._units)]
    completed, _timed_out = units
    source._save_cached(completed, source_child.run_candidate(
        completed.schedule,
        target_repetitions={"q10": 1, "q50": 1, "q90": 0},
        existing=None,
        stage="pilot",
    ))

    recovery = CompatibleDailyCacheImport(
        source_root=source_root.resolve(),
        source_search_id="old-search",
        source_search_content_key=spec.content_key,
        source_backend_artifact_sha256="a" * 64,
        source_unit_backend_digests=dict(source._unit_backend_digests),
    )
    # A different backend -- e.g. a retired routing policy -- must produce a
    # different unit backend digest for every unit.
    recovered_child = FakeDailyRunner()
    recovered_child.provenance = lambda: {
        "kind": "fake-daily-sumo", "identity": "closure_origin_routing_v1"}
    destination = tmp_path / "recovery-cache"
    runner = IndependentDailyRunner(
        spec,
        daily_runner=recovered_child,
        cache_root=destination,
        compatible_cache_import=recovery,
    )
    runner.prepare((parent,))

    manifest = json.loads(
        (destination / "compatible-import-old-search.json").read_text()
    )
    assert manifest["imported_completed_units"] == 0
    assert manifest["imported_partial_units"] == 0
    assert manifest["retained_destination_units"] == 0
    assert manifest["available_completed_units"] == 0
    assert manifest["skipped_incompatible_backend_units"] == len(units)
    # No file was written under the new backend digest -- the unit is a
    # genuine cache miss, not a demoted/partial import.
    assert runner._load_cached(completed, count=False) is None


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


class FakeDailyRunnerWithLaunchTelemetry(FakeDailyRunner):
    """A `daily_runner` that exposes the optional exact-launch S0 hook."""

    def __init__(self, *, telemetry=None, raise_on_call=False):
        super().__init__()
        self._telemetry = telemetry or {
            "pilot": {"attempts": 3, "timeouts": 0, "other_outcomes": 3},
            "finalist": {"attempts": 1, "timeouts": 1, "other_outcomes": 0},
        }
        self._raise_on_call = raise_on_call

    def launch_telemetry_snapshot(self):
        if self._raise_on_call:
            raise RuntimeError("backend telemetry unavailable")
        return self._telemetry


def test_timing_snapshot_includes_exact_launch_telemetry_when_the_backend_has_it(
        tmp_path):
    spec = _spec()
    schedule = generate_closure_schedules(spec)[0]
    child = FakeDailyRunnerWithLaunchTelemetry()
    runner = IndependentDailyRunner(
        spec, daily_runner=child, cache_root=tmp_path / "daily-cache")
    runner.prepare((schedule,))
    runner.run_candidate(
        schedule, target_repetitions={"q10": 1, "q50": 1, "q90": 1},
        existing=None, stage="pilot")
    snapshot = runner.timing_snapshot()
    assert snapshot["exact_launch_telemetry"] == {
        "pilot": {"attempts": 3, "timeouts": 0, "other_outcomes": 3},
        "finalist": {"attempts": 1, "timeouts": 1, "other_outcomes": 0},
    }


def test_timing_snapshot_omits_the_key_without_the_optional_hook(tmp_path):
    """A backend without the hook (e.g. a legacy/fake runner) is tolerated."""
    spec = _spec()
    schedule = generate_closure_schedules(spec)[0]
    child = FakeDailyRunner()  # no launch_telemetry_snapshot method
    runner = IndependentDailyRunner(
        spec, daily_runner=child, cache_root=tmp_path / "daily-cache")
    runner.prepare((schedule,))
    runner.run_candidate(
        schedule, target_repetitions={"q10": 1, "q50": 1, "q90": 1},
        existing=None, stage="pilot")
    assert "exact_launch_telemetry" not in runner.timing_snapshot()


def test_a_broken_launch_telemetry_hook_fails_open(tmp_path):
    """Diagnostic-only: a raising hook must not break a real search."""
    spec = _spec()
    schedule = generate_closure_schedules(spec)[0]
    child = FakeDailyRunnerWithLaunchTelemetry(raise_on_call=True)
    runner = IndependentDailyRunner(
        spec, daily_runner=child, cache_root=tmp_path / "daily-cache")
    runner.prepare((schedule,))
    runner.run_candidate(
        schedule, target_repetitions={"q10": 1, "q50": 1, "q90": 1},
        existing=None, stage="pilot")
    assert "exact_launch_telemetry" not in runner.timing_snapshot()


def test_durable_launch_sidecar_counts_exception_termination_and_retry_once(
        tmp_path):
    """A final worker result is optional; launched attempts are not."""
    isolated = IsolatedDailySumoRunner(
        FakeDailyRunner(), unit_workers=1, worker_invoker=lambda _request: None)
    sidecar = tmp_path / "telemetry.ndjson"
    base = {
        "candidate_id": "candidate-a", "work_date": "2027-01-01",
        "stage": "pilot", "variant": "q50", "seed": 1000,
        "timed_out": False,
    }
    first_records = [
        {**base, "attempt": 1, "outcome": "in_progress"},
        {**base, "attempt": 1, "outcome": "unrecognized_exception"},
    ]
    sidecar.write_text(
        "".join(json.dumps(record) + "\n" for record in first_records),
        encoding="utf-8")
    isolated._merge_launch_sidecar(sidecar)

    # A recovery uses a fresh subprocess whose local attempt counter starts
    # at one again. The parent must rebind it to attempt two, even when that
    # retry is killed before publishing a final result.
    sidecar.write_text(
        json.dumps({**base, "attempt": 1, "outcome": "in_progress"}) + "\n",
        encoding="utf-8")

    isolated._merge_launch_sidecar(sidecar)

    assert isolated.launch_telemetry_snapshot()["pilot"] == {
        "attempts": 2, "timeouts": 0, "other_outcomes": 2}
    assert isolated.launch_records_snapshot() == [
        {**base, "attempt": 1, "outcome": "unrecognized_exception"},
        {**base, "attempt": 2, "outcome": "worker_terminated"},
    ]


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


def _disruption_record(variant, *, vehicles_affected=5, vehicles_no_detour=0,
                        added_vehicle_hours=1.0, added_metres_total=10.0):
    return {
        "demand_variant": variant,
        "vehicles_affected": vehicles_affected,
        "vehicles_considered": vehicles_affected,
        "vehicles_no_detour": vehicles_no_detour,
        "added_vehicle_hours": added_vehicle_hours,
        "added_metres_total": added_metres_total,
    }


def test_daily_timeout_is_undecided_not_silently_a_hard_failure_and_keeps_disruption():
    """Regression for the cost-order v5 root cause.

    v5's exhaustive arm silently dropped disruption on ANY hard failure
    (including a timeout) while the cost-ordered ledger kept it, so the two
    arms judged a timed-out candidate on different fields and picked
    different finalists. A timed-out daily unit must still surface
    deterministic disruption for the whole parent, AND must be visible as an
    explicit undecided timeout rather than an ordinary disqualification.
    """
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-02",
    )
    parent = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    units, _ = decompose_schedules(spec, (parent,))
    disruption = tuple(
        _disruption_record(variant) for variant in ("q10", "q50", "q90")
    )
    daily_timeout = TimeoutIdentity(
        schema=TIMEOUT_IDENTITY_SCHEMA,
        candidate_id=units[0].schedule.schedule_id,
        work_date=units[0].identity["work_date"],
        search_content_key=units[0].schedule.search_content_key,
        variant="q50",
        seed=1000,
        attempt=1,
        threshold_s=300.0,
        retry_protocol=RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
        search_provenance_key="study",
    )
    evidence = {
        units[0].unit_id: CandidateEvidence(
            candidate_id=units[0].schedule.schedule_id,
            disruption=disruption,
            timeout_undecided=(daily_timeout,),
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
            disruption=disruption,
        ),
    }

    combined = aggregate_daily_evidence(parent, units, evidence)

    assert combined.observations == ()
    assert combined.eligible
    assert combined.hard_failures == ()
    # The bug: this used to be (), losing the deterministic disruption a
    # timed-out unit still computed.
    assert combined.disruption != ()
    assert len(combined.disruption) == 3
    for record in combined.disruption:
        # Summed across both units, one per unit contributing the same
        # per-variant record.
        assert record["vehicles_affected"] == 10
        assert record["added_vehicle_hours"] == pytest.approx(2.0)
    # No date-prefixing here any more: the identity already names its own
    # work_date (see `monthly_sumo._timeout_identity`), so aggregation just
    # carries the SAME validated record through unchanged.
    assert combined.timeout_undecided == (daily_timeout,)
    assert combined.timeout_undecided[0].work_date == "2027-01-01"
    assert combined.has_undecided_timeout


def test_cached_timeout_is_terminal_for_the_frozen_no_retry_campaign(tmp_path):
    spec = _spec(
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-01",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("15:00", "16:00"),
    )
    parent = generate_closure_schedules(spec)[0]
    child = FakeDailyRunner()
    runner = IndependentDailyRunner(
        spec, daily_runner=child, cache_root=tmp_path / "cache"
    )
    runner.prepare((parent,))
    unit = next(iter(runner._units.values()))
    timeout = TimeoutIdentity(
        schema=TIMEOUT_IDENTITY_SCHEMA,
        candidate_id=unit.schedule.schedule_id,
        work_date=unit.identity["work_date"],
        search_content_key=unit.schedule.search_content_key,
        variant="q10",
        seed=1000,
        attempt=1,
        threshold_s=300.0,
        retry_protocol=RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
        search_provenance_key="study",
    )
    runner._save_cached(unit, CandidateEvidence(
        candidate_id=unit.schedule.schedule_id,
        timeout_undecided=(timeout,),
    ))

    result = runner.run_candidate(
        parent,
        target_repetitions={"q10": 1, "q50": 1, "q90": 1},
        existing=None,
        stage="pilot",
    )

    assert child.calls == []
    assert result.hard_failures == ()
    assert result.timeout_undecided == (timeout,)


@pytest.mark.parametrize(("field", "value"), [
    ("seed", True),
    ("attempt", 1.5),
    ("threshold_s", "300"),
    ("retry_protocol", "unknown_retry_v1"),
    ("work_date", "not-a-date"),
])
def test_daily_cache_deserialization_rejects_malformed_timeout_fields(
        field, value):
    """The daily cache must not normalize malformed timeout-v3 records."""
    raw_timeout = {
        "schema": TIMEOUT_IDENTITY_SCHEMA,
        "candidate_id": "candidate-a",
        "work_date": "2027-01-01",
        "search_content_key": "search-key",
        "variant": "q50",
        "seed": 1000,
        "attempt": 1,
        "threshold_s": 300.0,
        "retry_protocol": RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
        "search_provenance_key": "study",
    }
    raw_timeout[field] = value
    with pytest.raises(ValueError, match="timeout identity"):
        independent_daily_module._evidence_from_dict({
            "candidate_id": "candidate-a",
            "observations": [],
            "hard_failures": [],
            "disruption": [],
            "timeout_undecided": [raw_timeout],
            "canonical_observation_digests": [],
        })


def test_daily_cache_evidence_rejects_missing_timeout_population():
    raw = {
        "candidate_id": "candidate-a",
        "observations": [],
        "hard_failures": [],
        "disruption": [],
        "timeout_undecided": [],
        "canonical_observation_digests": [],
    }
    del raw["timeout_undecided"]
    with pytest.raises(ValueError, match="fields are invalid"):
        independent_daily_module._evidence_from_dict(raw)


def test_daily_cache_round_trip_preserves_canonical_observation_digest(tmp_path):
    spec = _spec()
    schedule = generate_closure_schedules(spec)[0]
    first = IndependentDailyRunner(
        spec, daily_runner=FakeDailyRunner(), cache_root=tmp_path / "cache")
    first.prepare((schedule,))
    unit = next(iter(first._units.values()))
    digest = CanonicalObservationDigest(
        candidate_id=unit.schedule.schedule_id,
        work_date=unit.identity["work_date"], variant="q10", seed=1000,
        sha256="b" * 64)
    first._save_cached(unit, CandidateEvidence(
        candidate_id=unit.schedule.schedule_id,
        canonical_observation_digests=(digest,)))

    resumed = IndependentDailyRunner(
        spec, daily_runner=FakeDailyRunner(), cache_root=tmp_path / "cache")
    resumed.prepare((schedule,))
    resumed_unit = next(iter(resumed._units.values()))
    loaded = resumed._load_cached(resumed_unit)
    assert loaded is not None
    assert loaded.canonical_observation_digests == (digest,)


class FakeDailyRunnerWithCacheRoot(FakeDailyRunner):
    """A `daily_runner` double that exposes the durable-evidence
    `cache_root` a real `ArchivedDemandSumoRunner`/`IsolatedDailySumoRunner`
    always has, so `_load_cached`/`_save_cached` actually run the
    end-to-end routing-evidence validation (review finding 1)."""

    def __init__(self, cache_root):
        super().__init__()
        self.cache_root = cache_root


def _real_durable_digest(cache_root, *, candidate_id, unit_id="unit-a",
                          work_date="2027-01-01", variant="q10", seed=1000):
    """Persist one complete, real durable-evidence chain under `cache_root`
    using the actual production preservation code, and return its digest."""
    from traffic_sim.simulation import closure_routing
    from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

    archive = cache_root.parent / "archive"
    if not archive.is_dir():
        archive.mkdir(parents=True)
        (archive / "manifest.json").write_text(
            json.dumps({"status": "succeeded"}))
        (archive / "demand_meta.json").write_text(json.dumps({
            "demand_build_key": "demand-key",
            "epoch_sim": "2025-09-16T00:00:00",
            "n_intervals": 96, "n_variants": 3,
        }))
        for filename in (
            "calibrated.rou.xml", "calibrated_v1.rou.xml",
            "calibrated_v2.rou.xml",
        ):
            (archive / filename).write_text("<routes/>")

    from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
    spec = ClosureSearchSpec(
        search_id="fixture", directed_edges=("edge-a",),
        demand_build_id="demand-key", source="historical",
        permitted_date_start=work_date, permitted_date_end=work_date,
        required_work_minutes=60, max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("06:00", "07:00"))
    runner = ArchivedDemandSumoRunner(
        spec, archive=archive, baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=cache_root)

    route_path = cache_root.parent / f"scratch-route-{seed}.rou.xml"
    route_path.write_text("<routes/>\n", encoding="utf-8")
    transformed_route_sha256 = runner._preserve_transformed_route(route_path)

    access_impact_path = cache_root.parent / f"scratch-access-{seed}.json"
    closure_routing.write_access_impact_report(
        access_impact_path,
        result=closure_routing.ClosureRoutingResult(
            unaffected=0, rerouted=0, denied=0, access_impact=()),
        close_edges=[], closures=None, source_route_path=route_path,
        out_route_path=route_path,
        identity={
            "unit_id": unit_id,
            "candidate_id": candidate_id,
            "work_date": work_date,
            "demand_variant": variant,
            "seed": seed,
            "execution_arm": "cold",
            "vehicle_class": closure_routing.DEFAULT_VCLASS,
        })
    access_impact_sha256 = runner._preserve_access_impact_evidence(
        access_impact_path)

    routing_provenance = closure_routing.RoutingProvenance(
        routing_policy_version=closure_routing.POLICY_VERSION,
        vehicle_class=closure_routing.DEFAULT_VCLASS,
        unit_id=unit_id, candidate_id=candidate_id, work_date=work_date,
        demand_variant=variant, seed=seed, execution_arm="cold",
        access_impact_sha256=access_impact_sha256,
        access_impact_semantic_sha256=(
            closure_routing.access_impact_semantic_sha256(
                json.loads(access_impact_path.read_text(encoding="utf-8")))),
        transformed_route_sha256=transformed_route_sha256,
        rerouted_around_closure=0, denied_count=0,
    ).to_dict()
    sha256 = runner._preserve_canonical_observation({
        "schedule_id": candidate_id,
        "demand_variant": variant,
        "seed": seed,
        "execution_arm": "cold",
        "feasibility": {"vehicles_denied_departure": 0},
        "candidate_metrics": {"dropped_unreachable": 0},
        "truncation": {"candidate": {"dropped_unreachable": 0}},
        "provenance": {"routing_provenance": routing_provenance},
    })
    return CanonicalObservationDigest(
        candidate_id=candidate_id, work_date=work_date, variant=variant,
        seed=seed, sha256=sha256)


def test_daily_cache_round_trip_validates_a_real_durable_evidence_chain(
        tmp_path):
    """The happy path: a cache hit whose canonical/routing/access-impact/
    transformed-route chain is real and untampered must still resolve --
    the new validation must not reject legitimate evidence."""
    spec = _spec()
    schedule = generate_closure_schedules(spec)[0]
    cache_root = tmp_path / "backend-cache"
    first = IndependentDailyRunner(
        spec, daily_runner=FakeDailyRunnerWithCacheRoot(cache_root),
        cache_root=tmp_path / "daily-cache")
    first.prepare((schedule,))
    unit = next(iter(first._units.values()))
    digest = _real_durable_digest(
        cache_root, candidate_id=unit.schedule.schedule_id,
        unit_id=unit.unit_id, work_date=unit.identity["work_date"])
    first._save_cached(unit, CandidateEvidence(
        candidate_id=unit.schedule.schedule_id,
        canonical_observation_digests=(digest,)))

    resumed = IndependentDailyRunner(
        spec, daily_runner=FakeDailyRunnerWithCacheRoot(cache_root),
        cache_root=tmp_path / "daily-cache")
    resumed.prepare((schedule,))
    resumed_unit = next(iter(resumed._units.values()))
    loaded = resumed._load_cached(resumed_unit)
    assert loaded is not None
    assert loaded.canonical_observation_digests == (digest,)


def test_save_cached_fails_closed_when_evidence_is_not_durable(tmp_path):
    """Review finding 1: fresh evidence must be validated on WRITE, not
    just on reload -- a backend that returns a digest naming no real
    durable artifact must never be allowed to poison the cache."""
    spec = _spec()
    schedule = generate_closure_schedules(spec)[0]
    cache_root = tmp_path / "backend-cache"
    runner = IndependentDailyRunner(
        spec, daily_runner=FakeDailyRunnerWithCacheRoot(cache_root),
        cache_root=tmp_path / "daily-cache")
    runner.prepare((schedule,))
    unit = next(iter(runner._units.values()))
    phantom_digest = CanonicalObservationDigest(
        candidate_id=unit.schedule.schedule_id,
        work_date=unit.identity["work_date"], variant="q10", seed=1000,
        sha256="c" * 64)
    with pytest.raises(Exception):
        runner._save_cached(unit, CandidateEvidence(
            candidate_id=unit.schedule.schedule_id,
            canonical_observation_digests=(phantom_digest,)))
    # And nothing was written to the daily cache as a result.
    assert not (tmp_path / "daily-cache").exists() or not any(
        (tmp_path / "daily-cache").rglob("*.json"))


def test_load_cached_fails_closed_when_durable_evidence_is_tampered(
        tmp_path):
    """Review finding 1: a cache hit whose backing canonical observation was
    tampered with after the fact must be treated as corrupt, not returned as
    valid evidence."""
    spec = _spec()
    schedule = generate_closure_schedules(spec)[0]
    cache_root = tmp_path / "backend-cache"
    first = IndependentDailyRunner(
        spec, daily_runner=FakeDailyRunnerWithCacheRoot(cache_root),
        cache_root=tmp_path / "daily-cache")
    first.prepare((schedule,))
    unit = next(iter(first._units.values()))
    digest = _real_durable_digest(
        cache_root, candidate_id=unit.schedule.schedule_id,
        unit_id=unit.unit_id, work_date=unit.identity["work_date"])
    first._save_cached(unit, CandidateEvidence(
        candidate_id=unit.schedule.schedule_id,
        canonical_observation_digests=(digest,)))
    canonical_path = (
        cache_root / "canonical-observations" / digest.sha256[:2]
        / f"{digest.sha256}.json")
    canonical_path.write_text(json.dumps({"tampered": True}), encoding="utf-8")

    resumed = IndependentDailyRunner(
        spec, daily_runner=FakeDailyRunnerWithCacheRoot(cache_root),
        cache_root=tmp_path / "daily-cache")
    resumed.prepare((schedule,))
    resumed_unit = next(iter(resumed._units.values()))
    before = resumed.timing_snapshot()["cache_corrupt"]
    loaded = resumed._load_cached(resumed_unit)
    assert loaded is None
    assert resumed.timing_snapshot()["cache_corrupt"] == before + 1


def test_worker_result_rejects_missing_timeout_and_unknown_envelope_fields():
    result = {
        "schema": "independent_daily_worker_result_v3",
        "evidence": {
            "candidate_id": "candidate-a", "observations": [],
            "hard_failures": [], "disruption": [], "timeout_undecided": [],
            "canonical_observation_digests": [],
        },
        "launch_telemetry": {},
        "launch_records": [],
    }
    del result["evidence"]["timeout_undecided"]
    with pytest.raises(ValueError, match="fields are invalid"):
        independent_daily_module._evidence_from_worker_result(result)

    result["evidence"]["timeout_undecided"] = []
    result["unknown"] = True
    with pytest.raises(ValueError, match="worker result is malformed"):
        independent_daily_module._evidence_from_worker_result(result)


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
        window_cost_index=None,
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
        MONTHLY_DAILY_WORKERS,
        MONTHLY_DAILY_UNIT_BUDGET,
        MONTHLY_MAX_ACTIVE_SUMO_SLOTS,
        MONTHLY_PARENT_SCHEDULE_CAP,
        MONTHLY_SEED_WORKERS,
        MONTHLY_TOTAL_DAILY_UNIT_CAP,
        monthly_screening_cli_args,
    )

    assert monthly_screening_cli_args(_spec()) == [
        "--screening-mode",
        "independent-exhaustive",
        "--daily-workers",
        str(MONTHLY_DAILY_WORKERS),
        "--seed-workers",
        str(MONTHLY_SEED_WORKERS),
        "--max-active-sumo-slots",
        str(MONTHLY_MAX_ACTIVE_SUMO_SLOTS),
        "--daily-unit-budget",
        str(MONTHLY_DAILY_UNIT_BUDGET),
        "--daily-unit-total-cap",
        str(MONTHLY_TOTAL_DAILY_UNIT_CAP),
        "--independent-exhaustive-candidate-cap",
        str(MONTHLY_PARENT_SCHEDULE_CAP),
    ]


class TestWholeDayClosures:
    """"Seven whole days" must decompose into seven fully-closed days.

    A whole day means the road is shut for that entire calendar day, so the
    hours inside it carry no decision. The calendar used to refuse
    back-to-back whole days, leaving the request expressible only as seven
    23:45 shifts -- a different closure, with six nightly openings in it.
    """

    @staticmethod
    def _seven_whole_days():
        return _spec(
            permitted_date_start="2027-05-03",
            permitted_date_end="2027-05-09",
            required_work_minutes=7 * 24 * 60,
            min_consecutive_start_days=7,
            max_consecutive_start_days=7,
            permitted_daily_band=DailyTimeBand("00:00", "24:00"),
            work_allocation_policy="exact_equal_daily_v1",
        )

    def test_it_becomes_seven_distinct_fully_closed_daily_units(self):
        spec = self._seven_whole_days()
        parent = generate_closure_schedules(spec)[0]

        records = independent_daily_module.daily_unit_records(spec, parent)

        assert len(records) == 7
        assert len({unit_id for unit_id, _identity, _build in records}) == 7
        for _unit_id, identity, build in records:
            schedule = build()
            assert (schedule.daily_start, schedule.daily_end) == (
                "00:00", "24:00")
            assert schedule.actual_closed_minutes == 24 * 60
            assert identity["work_date"] == schedule.first_work_date

    def test_each_day_is_shut_from_first_second_to_last(self):
        spec = self._seven_whole_days()
        parent = generate_closure_schedules(spec)[0]

        for _unit_id, _identity, build in independent_daily_module.daily_unit_records(
                spec, parent):
            schedule = build()
            closures = deterministic_disruption.closure_seconds(
                spec, schedule,
                epoch=datetime.fromisoformat(schedule.intervals[0].start_time),
                duration_s=86400)
            assert [(item["begin_s"], item["end_s"]) for item in closures] == [
                (0, 86400)]

    def test_a_whole_day_closure_does_not_depend_on_the_congestion_bound(self):
        """The declared congestion-delay bound only ever decides whether a
        vehicle arriving BEFORE a window should still be counted. A whole-day
        closure has no before, so this class of schedule is immune to the one
        unvalidated assumption in the timing rule."""
        events = ((("a_b_0"), occupancy) for occupancy in (0.0, 43200.0))
        window = [{"edge_id": "a_b_0", "begin_s": 0, "end_s": 86400}]

        for edge, occupancy in list(events):
            for bound in (0.0, 900.0, 3600.0, 7200.0):
                assert disruption.applicable_closed_edges_from_events(
                    ((edge, occupancy),), window, frozenset({edge}),
                    max_assumed_delay_s=bound) == frozenset({edge})
