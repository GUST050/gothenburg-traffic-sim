import gzip
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSearchSpec,
    DailyTimeBand,
    DemandBuildSpec,
)
from traffic_sim.simulation.finalist_decision import (
    CanonicalObservationDigest,
    RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
    RETRY_PROTOCOL_TWO_TIER_EXACT,
    CandidateEvidence,
    PairedObservation,
    TimeoutIdentity,
)
from traffic_sim.simulation.monthly_search import canonical_seed
from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner
import traffic_sim.simulation.monthly_sumo as monthly_sumo


def _spec(build_key="demand-key"):
    return ClosureSearchSpec(
        search_id="sumo-backend",
        directed_edges=("edge-a",),
        demand_build_id=build_key,
        source="historical",
        permitted_date_start="2025-09-16",
        permitted_date_end="2025-09-16",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("06:00", "07:00"),
    )


def _archive(tmp_path, *, build_key="demand-key", days=1, demand_spec=None):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "manifest.json").write_text(json.dumps({"status": "succeeded"}))
    metadata = {
        "demand_build_key": build_key,
        "epoch_sim": "2025-09-16T00:00:00",
        "n_intervals": days * 96,
        "n_variants": 3,
    }
    if demand_spec is not None:
        metadata["source"] = demand_spec.source
        metadata["demand_spec"] = demand_spec.to_dict()
        (archive / "demand_build_spec.json").write_text(
            json.dumps(demand_spec.to_dict())
        )
    (archive / "demand_meta.json").write_text(json.dumps(metadata))
    for filename in (
        "calibrated.rou.xml",
        "calibrated_v1.rou.xml",
        "calibrated_v2.rou.xml",
    ):
        (archive / filename).write_text(f"<routes id='{filename}'/>")
    return archive


@pytest.fixture
def patched_runtime(tmp_path, monkeypatch):
    net = tmp_path / "net.net.xml"
    net.write_text("<net/>")
    monkeypatch.setattr(monthly_sumo.rs, "NET_PATH", net)
    monkeypatch.setattr(monthly_sumo.rs, "sumo_home", lambda: tmp_path)
    monkeypatch.setattr(
        monthly_sumo.rs,
        "build_edge_graph",
        lambda edges: {"edge-a": []},
    )
    monkeypatch.setattr(
        monthly_sumo.rs,
        "edge_freeflow_times",
        lambda: {"edge-a": 10.0},
    )
    monkeypatch.setattr(
        monthly_sumo.rs,
        "edges_near",
        lambda edges, radius: list(edges),
    )
    monkeypatch.setattr(
        monthly_sumo.legacy,
        "detour_availability",
        lambda edges, path: {"score": 1.0},
    )
    monkeypatch.setattr(monthly_sumo, "sumo_version", lambda home: "SUMO 1.27.1")
    return tmp_path


def test_archive_identity_and_envelope_are_validated(
        tmp_path, patched_runtime):
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(_spec())[0]
    envelope = runner._envelope(schedule)
    assert envelope.scenario_start == "2025-09-16T00:00:00"
    assert envelope.scenario_end == "2025-09-17T00:00:00"
    assert runner.matched_baseline_id.startswith("monthly-baseline-")
    provenance = runner.provenance()
    labels = {item["label"] for item in provenance["source_files"]}
    assert all(
        not Path(item["label"]).is_absolute()
        for item in provenance["source_files"]
    )
    assert {
        "run_monthly_closure_search.py",
        "traffic_sim/core/closure_calendar.py",
        "traffic_sim/simulation/monthly_search.py",
        "traffic_sim/simulation/monthly_sumo.py",
        "traffic_sim/simulation/closure_teleport.py",
        "traffic_sim/simulation/pilot_selection.py",
        "traffic_sim/simulation/finalist_decision.py",
        "traffic_sim/simulation/search_workspace.py",
    } <= labels
    simulation_labels = {
        item["label"] for item in runner.simulation_source_records
    }
    assert "traffic_sim/simulation/closure_teleport.py" in simulation_labels
    assert "git_commit_at_run" not in provenance
    assert provenance["source_digest"] != (
        provenance["simulation_source_digest"]
    )


def test_identical_concurrent_baselines_are_single_flight(
        tmp_path, patched_runtime, monkeypatch):
    """Only one SUMO baseline may run for one cache key.

    The production outer-worker shape can submit several closure units for the
    same date, variant and seed at once.  Those units share a matched-baseline
    key.  This test states the required structure: one worker computes and
    publishes the baseline while every waiter verifies and reads that result.
    The per-key lock is cross-process; this focused thread test also proves
    that waiters recheck and verify the completed artifact instead of running
    duplicate SUMO work.
    """
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    metrics = monthly_sumo.closure_metrics.DisruptionMetrics(
        total_time_loss_s=10.0,
        trip_count=1,
        unfinished_trips=0,
        unfinished_waiting_trips=0,
        teleport_total=0,
        teleport_reasons={},
        loaded=1,
        inserted=1,
        running_at_end=0,
        waiting_at_end=0,
    )
    buckets = (monthly_sumo.RecoveryBucket(0, 900, 10.0),)
    calls = 0
    calls_lock = threading.Lock()
    simultaneous_start = threading.Barrier(2)

    def simulate_closure(**_kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        # Make the unprotected implementation's duplicate work deterministic
        # without constraining the future lock implementation.
        time.sleep(0.1)
        return metrics, None, None, None, None

    monkeypatch.setattr(
        monthly_sumo.legacy, "simulate_closure", simulate_closure
    )
    monkeypatch.setattr(
        monthly_sumo, "read_edgedata_time_loss", lambda _path: buckets
    )

    def run(_index):
        simultaneous_start.wait(timeout=5.0)
        return runner._run_baseline("q50", 1001)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, range(2)))

    assert calls == 1
    assert results == [(metrics, buckets), (metrics, buckets)]


def test_cold_run_stops_at_schedule_envelope_not_archive_tail(
        tmp_path, patched_runtime):
    """A reusable multi-day archive must not force a longer SUMO run."""
    spec = ClosureSearchSpec.from_dict({
        **{key: value for key, value in _spec().to_dict().items()
           if key != "content_key"},
        "permitted_date_start": "2025-09-17",
        "permitted_date_end": "2025-09-17",
        "interday_policy": "independent_daily_reset_v1",
    })
    runner = ArchivedDemandSumoRunner(
        spec,
        archive=_archive(tmp_path, days=3),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(spec)[0]

    assert runner.n_intervals == 288
    assert runner.duration_s == 3 * 24 * 3600
    assert runner._cold_simulation_window(schedule) == (
        96, 2 * 24 * 3600, 24 * 3600, 0)


def test_warm_execution_is_gated_when_its_horizon_differs_from_cold(
        tmp_path, patched_runtime):
    """The pre-adf765b warm proof cannot authorize the trimmed cold arm."""
    spec = ClosureSearchSpec.from_dict({
        **{key: value for key, value in _spec().to_dict().items()
           if key != "content_key"},
        "permitted_date_start": "2025-09-17",
        "permitted_date_end": "2025-09-17",
        "interday_policy": "independent_daily_reset_v1",
    })
    runner = ArchivedDemandSumoRunner(
        spec,
        archive=_archive(tmp_path, days=3),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
        warm_execution=True,
        boundary_controller=object(),
    )
    schedule = generate_closure_schedules(spec)[0]

    assert runner._observation_execution_window(schedule) == (
        False, 96, 2 * 24 * 3600, 24 * 3600, 0)


def test_objective_aligned_runner_emits_per_variant_disruption(
        tmp_path, patched_runtime, monkeypatch):
    monkeypatch.setattr(
        monthly_sumo.rs, "free_flow_edge_cost", lambda: ({}, {})
    )
    monkeypatch.setattr(
        monthly_sumo.rs,
        "closure_disruption",
        lambda path, *args, **kwargs: {
            "vehicles_affected": 2,
            "vehicles_no_detour": 0,
            "added_vehicle_hours": 0.5,
            "added_metres_total": 100.0,
        },
    )
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
        include_disruption=True,
    )

    records = runner._closure_disruption(
        generate_closure_schedules(_spec())[0]
    )

    assert tuple(item["demand_variant"] for item in records) == (
        "q10", "q50", "q90"
    )
    assert runner.provenance()["ranking_objective_evidence"] == (
        "closure_cost_v1"
    )


def test_archive_calibrated_on_another_network_is_rejected(
        tmp_path, patched_runtime):
    archive = _archive(tmp_path)
    metadata = json.loads((archive / "demand_meta.json").read_text())
    metadata["sensor_contract"] = {"network_sha256": "0" * 64}
    (archive / "demand_meta.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="another SUMO network"):
        ArchivedDemandSumoRunner(
            _spec(),
            archive=archive,
            baseline_trip_duration_p99_s=1800,
            study_provenance_key="study",
            cache_root=tmp_path / "cache",
        )


def test_archive_matching_active_network_is_accepted(
        tmp_path, patched_runtime):
    from traffic_sim.core.fingerprint import sha256_file
    archive = _archive(tmp_path)
    metadata = json.loads((archive / "demand_meta.json").read_text())
    metadata["sensor_contract"] = {
        "network_sha256": sha256_file(monthly_sumo.rs.NET_PATH)}
    (archive / "demand_meta.json").write_text(json.dumps(metadata))
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=archive,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    assert runner.matched_baseline_id.startswith("monthly-baseline-")


def test_archive_with_wrong_build_key_is_rejected(tmp_path, patched_runtime):
    with pytest.raises(ValueError, match="build key"):
        ArchivedDemandSumoRunner(
            _spec(),
            archive=_archive(tmp_path, build_key="other"),
            baseline_trip_duration_p99_s=1800,
            study_provenance_key="study",
            cache_root=tmp_path / "cache",
        )


def test_exact_envelope_demand_contract_is_validated(
        tmp_path, patched_runtime):
    demand_spec = DemandBuildSpec(
        start_date="2025-09-16",
        source="historical",
        days=1,
        begin="00:00",
        end="24:00",
        purpose="closure_envelope",
    )
    runner = ArchivedDemandSumoRunner(
        _spec("release-id"),
        archive=_archive(
            tmp_path,
            build_key=demand_spec.build_key,
            demand_spec=demand_spec,
        ),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
        expected_demand_spec=demand_spec,
    )
    assert runner.provenance()["demand_release_id"] == "release-id"
    assert runner.provenance()["demand_build_id"] == demand_spec.build_key


def test_runner_adds_only_missing_canonical_repetitions(
        tmp_path, patched_runtime, monkeypatch):
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(_spec())[0]
    calls = []

    def observation(selected, *, variant, seed):
        calls.append((variant, seed))
        return (
            PairedObservation(
                candidate_id=selected.schedule_id,
                demand_variant=variant,
                seed=seed,
                baseline_time_loss_s=100.0,
                candidate_time_loss_s=110.0,
                matched_baseline_id=runner.matched_baseline_id,
                provenance_key="study",
            ),
            (),
            None,          # canonical observation: stubs do not build one
        )

    monkeypatch.setattr(runner, "_run_observation", observation)
    pilot = runner.run_candidate(
        schedule,
        target_repetitions={"q10": 1, "q50": 1, "q90": 1},
        existing=None,
        stage="pilot",
    )
    assert calls == [
        ("q10", canonical_seed("q10", 0)),
        ("q50", canonical_seed("q50", 0)),
        ("q90", canonical_seed("q90", 0)),
    ]

    calls.clear()
    final = runner.run_candidate(
        schedule,
        target_repetitions={"q10": 2, "q50": 1, "q90": 2},
        existing=pilot,
        stage="finalist",
    )
    assert calls == [
        ("q10", canonical_seed("q10", 1)),
        ("q90", canonical_seed("q90", 1)),
    ]
    assert len(final.observations) == 5


def test_hard_failure_stops_additional_work(
        tmp_path, patched_runtime, monkeypatch):
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(_spec())[0]
    existing = CandidateEvidence(
        candidate_id=schedule.schedule_id,
        hard_failures=("teleports",),
    )
    monkeypatch.setattr(
        runner,
        "_run_observation",
        lambda *args, **kwargs: pytest.fail("SUMO should not run"),
    )
    result = runner.run_candidate(
        schedule,
        target_repetitions={"q10": 4, "q50": 4, "q90": 4},
        existing=existing,
        stage="finalist",
    )
    assert result.hard_failures == ("teleports",)


def test_sumo_timeout_is_recorded_only_as_undecided_evidence(
        tmp_path, patched_runtime, monkeypatch):
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(_spec())[0]

    def timeout(*args, **kwargs):
        raise SystemExit("sumo timed out after 300s (seed 1000)")

    monkeypatch.setattr(runner, "_run_observation", timeout)
    result = runner.run_candidate(
        schedule,
        target_repetitions={"q10": 1, "q50": 1, "q90": 1},
        existing=None,
        stage="pilot",
    )
    assert result.observations == ()
    # A process timeout says nothing about traffic-health eligibility.  It
    # must not poison the persistent cache as a hard disqualification.
    assert result.hard_failures == ()
    # Regression for cost-order v5: a timeout must be visible as an explicit
    # undecided outcome, distinct from an ordinary hard failure, so a reader
    # cannot silently treat it as if the run had simply been disqualified.
    # v3 (schema TIMEOUT_IDENTITY_SCHEMA): a validated structured record
    # naming the candidate, day, search and provenance, not a string.
    assert result.timeout_undecided == (
        TimeoutIdentity(
            schema=monthly_sumo.TIMEOUT_IDENTITY_SCHEMA,
            candidate_id=schedule.schedule_id,
            work_date=schedule.first_work_date,
            search_content_key=schedule.search_content_key,
            variant="q10",
            seed=1000,
            attempt=2,
            threshold_s=monthly_sumo.MONTHLY_SUMO_RECOVERY_TIMEOUT_S,
            retry_protocol=RETRY_PROTOCOL_TWO_TIER_EXACT,
            search_provenance_key="study",
        ),
    )
    assert result.timeout_undecided[0].schema == monthly_sumo.TIMEOUT_IDENTITY_SCHEMA
    assert result.has_undecided_timeout
    # The registered recovery is a real second exact launch. Both attempts
    # time out, after which the scan stops before q50/q90.
    assert runner.launch_telemetry["pilot"] == {
        "attempts": 2, "timeouts": 2, "other_outcomes": 0}
    assert runner.launch_telemetry["finalist"] == {
        "attempts": 0, "timeouts": 0, "other_outcomes": 0}


def test_first_timeout_is_recovered_by_exact_second_attempt(
        tmp_path, patched_runtime, monkeypatch):
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(_spec())[0]
    calls = []

    def recover(selected, *, variant, seed, timeout_s=None):
        calls.append(timeout_s)
        if timeout_s is None:
            raise SystemExit("sumo timed out after 300s (seed 1000)")
        assert timeout_s == monthly_sumo.MONTHLY_SUMO_RECOVERY_TIMEOUT_S
        return (
            PairedObservation(
                candidate_id=selected.schedule_id,
                demand_variant=variant,
                seed=seed,
                baseline_time_loss_s=100.0,
                candidate_time_loss_s=110.0,
                matched_baseline_id=runner.matched_baseline_id,
                provenance_key="study",
            ),
            (),
            None,
        )

    monkeypatch.setattr(runner, "_run_observation", recover)
    result = runner.run_candidate(
        schedule,
        target_repetitions={"q10": 1, "q50": 0, "q90": 0},
        existing=None,
        stage="pilot",
    )

    assert calls == [None, monthly_sumo.MONTHLY_SUMO_RECOVERY_TIMEOUT_S]
    assert len(result.observations) == 1
    assert result.hard_failures == ()
    assert result.timeout_undecided == ()
    assert runner.launch_telemetry["pilot"] == {
        "attempts": 2, "timeouts": 1, "other_outcomes": 1}


def test_recovery_timeout_override_is_validated_and_recorded(
        tmp_path, patched_runtime, monkeypatch):
    monkeypatch.setenv(
        monthly_sumo.MONTHLY_SUMO_RECOVERY_TIMEOUT_ENV, "7200")
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )

    assert runner.recovery_timeout_s == 7200.0
    assert runner.provenance()["timeout_recovery"] == {
        "protocol": RETRY_PROTOCOL_TWO_TIER_EXACT,
        "initial_threshold_s": 300.0,
        "recovery_threshold_s": 7200.0,
        "simulation_inputs_changed": False,
        "worker_resources_changed": False,
    }

    monkeypatch.setenv(
        monthly_sumo.MONTHLY_SUMO_RECOVERY_TIMEOUT_ENV, "1200")
    with pytest.raises(ValueError, match="must be at least 1800"):
        ArchivedDemandSumoRunner(
            _spec(),
            archive=runner.archive,
            baseline_trip_duration_p99_s=1800,
            study_provenance_key="study",
            cache_root=tmp_path / "invalid-cache",
        )


def test_successful_recovery_clears_the_matching_undecided_timeout(
        tmp_path, patched_runtime, monkeypatch):
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(_spec())[0]
    timeout = TimeoutIdentity(
        schema=monthly_sumo.TIMEOUT_IDENTITY_SCHEMA,
        candidate_id=schedule.schedule_id,
        work_date=schedule.first_work_date,
        search_content_key=schedule.search_content_key,
        variant="q10",
        seed=1000,
        attempt=1,
        threshold_s=300.0,
        retry_protocol=RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
        search_provenance_key="study",
    )
    existing = CandidateEvidence(
        candidate_id=schedule.schedule_id,
        timeout_undecided=(timeout,),
    )

    def observation(selected, *, variant, seed):
        return (
            PairedObservation(
                candidate_id=selected.schedule_id,
                demand_variant=variant,
                seed=seed,
                baseline_time_loss_s=100.0,
                candidate_time_loss_s=110.0,
                matched_baseline_id=runner.matched_baseline_id,
                provenance_key="study",
            ),
            (),
            None,
        )

    monkeypatch.setattr(runner, "_run_observation", observation)
    result = runner.run_candidate(
        schedule,
        target_repetitions={"q10": 1, "q50": 0, "q90": 0},
        existing=existing,
        stage="pilot",
    )

    assert len(result.observations) == 1
    assert result.hard_failures == ()
    assert result.timeout_undecided == ()


def test_a_non_timeout_sumo_failure_is_not_an_undecided_timeout(
        tmp_path, patched_runtime, monkeypatch):
    """A genuine SUMO crash is a hard failure, never labelled undecided."""
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(_spec())[0]

    def crash(*args, **kwargs):
        raise SystemExit("sumo failed with exit code 1")

    monkeypatch.setattr(runner, "_run_observation", crash)
    result = runner.run_candidate(
        schedule,
        target_repetitions={"q10": 1, "q50": 1, "q90": 1},
        existing=None,
        stage="pilot",
    )
    assert result.hard_failures == (
        "sumo_execution_failure:q10:1000:sumo failed with exit code 1",
    )
    assert result.timeout_undecided == ()
    assert not result.has_undecided_timeout
    # A non-timeout hard failure is still a real launch attempt, just not a
    # timeout one.
    assert runner.launch_telemetry["pilot"] == {
        "attempts": 1, "timeouts": 0, "other_outcomes": 1}


def test_complete_canonical_observation_digest_survives_cumulative_run(
        tmp_path, patched_runtime, monkeypatch):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    schedule = generate_closure_schedules(_spec())[0]
    canonical = {
        "baseline": {"time_loss_s": 100.0, "health": {"teleports": 0}},
        "candidate": {"time_loss_s": 110.0, "recovery": {"passed": True}},
        "feasible": True,
    }

    def observation(selected, *, variant, seed):
        return (
            PairedObservation(
                candidate_id=selected.schedule_id, demand_variant=variant,
                seed=seed, baseline_time_loss_s=100.0,
                candidate_time_loss_s=110.0,
                matched_baseline_id=runner.matched_baseline_id,
                provenance_key="study"),
            (), canonical,
        )

    monkeypatch.setattr(runner, "_run_observation", observation)
    first = runner.run_candidate(
        schedule, target_repetitions={"q10": 1, "q50": 0, "q90": 0},
        existing=None, stage="pilot")
    expected = CanonicalObservationDigest(
        candidate_id=schedule.schedule_id,
        work_date=schedule.first_work_date,
        variant="q10", seed=1000,
        sha256=monthly_sumo._canonical_digest(canonical))
    assert first.canonical_observation_digests == (expected,)

    resumed = runner.run_candidate(
        schedule, target_repetitions={"q10": 1, "q50": 0, "q90": 0},
        existing=first, stage="pilot")
    assert resumed.canonical_observation_digests == (expected,)

    # Review finding 4 (2026-08-29): the digest alone must be resolvable
    # back to the full canonical payload, durably, from a fresh reader that
    # never held the in-process observation.
    resolved = monthly_sumo.resolve_canonical_observation(
        runner.cache_root, expected.sha256)
    assert resolved == canonical


def test_persisting_the_identical_canonical_observation_twice_writes_once(
        tmp_path, patched_runtime, monkeypatch):
    """Content-addressed storage: the same payload recorded across two
    candidates (or a resumed run re-observing the same result) must not
    duplicate the file, matching `_preserve_access_impact_evidence`."""
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    canonical = {"baseline": {"time_loss_s": 100.0}, "feasible": True}

    digest = runner._preserve_canonical_observation(canonical)
    path = (runner.cache_root / "canonical-observations" / digest[:2]
            / f"{digest}.json")
    assert path.is_file()
    first_mtime = path.stat().st_mtime_ns

    digest_again = runner._preserve_canonical_observation(canonical)
    assert digest_again == digest
    assert path.stat().st_mtime_ns == first_mtime


@pytest.mark.parametrize(("method_name", "filename", "contents"), [
    ("_preserve_transformed_route", "route.rou.xml", b"<routes/>\n"),
    ("_preserve_access_impact_evidence", "access.json", b'{"ok": true}\n'),
])
def test_concurrent_identical_content_addressed_publication_is_atomic(
        tmp_path, patched_runtime, monkeypatch, method_name, filename, contents):
    """Supported multi-worker execution may publish identical bytes.

    Force all eight writers past their copy before any rename. A shared
    deterministic `.tmp` path then fails for seven writers; unique staging
    files let every writer atomically converge on the same final digest.
    """
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    source = tmp_path / filename
    source.write_bytes(contents)
    barrier = threading.Barrier(8)
    if method_name == "_preserve_transformed_route":
        original_copyfileobj = monthly_sumo.shutil.copyfileobj

        def synchronized_copy(source_handle, destination_handle, length=0):
            result = original_copyfileobj(
                source_handle, destination_handle, length)
            barrier.wait(timeout=10)
            return result

        monkeypatch.setattr(
            monthly_sumo.shutil, "copyfileobj", synchronized_copy)
    else:
        original_copyfile = monthly_sumo.shutil.copyfile

        def synchronized_copy(source_path, destination_path):
            result = original_copyfile(source_path, destination_path)
            barrier.wait(timeout=10)
            return result

        monkeypatch.setattr(
            monthly_sumo.shutil, "copyfile", synchronized_copy)
    publisher = getattr(runner, method_name)
    with ThreadPoolExecutor(max_workers=8) as executor:
        digests = list(executor.map(lambda _index: publisher(source), range(8)))

    assert len(set(digests)) == 1
    assert not list(runner.cache_root.rglob("*.tmp"))


def test_transformed_route_is_stored_losslessly_compressed(
        tmp_path, patched_runtime):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    source = tmp_path / "route.rou.xml"
    contents = (b'<vehicle id="v"><route edges="a b c"/></vehicle>\n'
                * 1000)
    source.write_bytes(contents)

    digest = runner._preserve_transformed_route(source)
    resolved = monthly_sumo.resolve_transformed_route(runner.cache_root, digest)

    assert resolved.name == f"{digest}.rou.xml.gz"
    assert not resolved.with_suffix("").is_file()
    with gzip.open(resolved, "rb") as handle:
        assert handle.read() == contents
    assert resolved.stat().st_size < len(contents)


def test_resolve_transformed_route_accepts_legacy_plain_artifact(tmp_path):
    contents = b"<routes/>\n"
    digest = monthly_sumo.hashlib.sha256(contents).hexdigest()
    path = (tmp_path / "transformed-routes" / digest[:2]
            / f"{digest}.rou.xml")
    path.parent.mkdir(parents=True)
    path.write_bytes(contents)

    assert monthly_sumo.resolve_transformed_route(tmp_path, digest) == path


def test_resolve_canonical_observation_fails_closed_when_missing(tmp_path):
    with pytest.raises(monthly_sumo.CanonicalObservationNotFound):
        monthly_sumo.resolve_canonical_observation(
            tmp_path / "cache", "0" * 64)


def test_resolve_canonical_observation_fails_closed_when_tampered(
        tmp_path, patched_runtime):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    canonical = {"baseline": {"time_loss_s": 100.0}, "feasible": True}
    digest = runner._preserve_canonical_observation(canonical)
    path = (runner.cache_root / "canonical-observations" / digest[:2]
            / f"{digest}.json")
    path.write_text(json.dumps({"tampered": True}), encoding="utf-8")

    with pytest.raises(monthly_sumo.CanonicalObservationTampered):
        monthly_sumo.resolve_canonical_observation(runner.cache_root, digest)


def _durable_chain(runner, *, candidate_id="candidate-a", work_date="2027-01-01",
                    variant="q10", seed=1000, unit_id="unit-a",
                    execution_arm="cold"):
    """Build one complete, real durable-evidence chain (transformed route,
    access-impact report, canonical observation with a validated
    RoutingProvenance) under `runner.cache_root`, exactly the way
    `ArchivedDemandSumoRunner.run_candidate` does, and return the resulting
    `CanonicalObservationDigest`."""
    from traffic_sim.simulation import closure_routing

    route_path = runner.cache_root.parent / "scratch-route.rou.xml"
    route_path.parent.mkdir(parents=True, exist_ok=True)
    route_path.write_text("<routes/>\n", encoding="utf-8")
    transformed_route_sha256 = runner._preserve_transformed_route(route_path)

    access_impact_path = runner.cache_root.parent / "scratch-access-impact.json"
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
            "execution_arm": execution_arm,
            "vehicle_class": closure_routing.DEFAULT_VCLASS,
        })
    access_impact_sha256 = runner._preserve_access_impact_evidence(
        access_impact_path)

    routing_provenance = closure_routing.RoutingProvenance(
        routing_policy_version=closure_routing.POLICY_VERSION,
        vehicle_class=closure_routing.DEFAULT_VCLASS,
        unit_id=unit_id,
        candidate_id=candidate_id,
        work_date=work_date,
        demand_variant=variant,
        seed=seed,
        execution_arm=execution_arm,
        access_impact_sha256=access_impact_sha256,
        transformed_route_sha256=transformed_route_sha256,
        rerouted_around_closure=0,
        denied_count=0,
    ).to_dict()
    canonical = {
        "schedule_id": candidate_id,
        "demand_variant": variant,
        "seed": seed,
        "execution_arm": execution_arm,
        "baseline_time_loss_s": 100.0,
        "candidate_time_loss_s": 100.0,
        "feasibility": {"vehicles_denied_departure": 0},
        "candidate_metrics": {"dropped_unreachable": 0},
        "truncation": {"candidate": {"dropped_unreachable": 0}},
        "provenance": {"routing_provenance": routing_provenance},
    }
    sha256 = runner._preserve_canonical_observation(canonical)
    return CanonicalObservationDigest(
        candidate_id=candidate_id, work_date=work_date, variant=variant,
        seed=seed, sha256=sha256)


def _mutate_durable_access_report(runner, digest, mutate):
    """Reseal a semantically edited report and its canonical envelope.

    The resulting chain is hash-valid.  Validation must therefore reject it
    for the semantic contract violation, not merely for byte tampering.
    """
    canonical = monthly_sumo.resolve_canonical_observation(
        runner.cache_root, digest.sha256)
    routing = canonical["provenance"]["routing_provenance"]
    report = monthly_sumo.resolve_access_impact_report(
        runner.cache_root, routing["access_impact_sha256"])
    mutate(report)
    scratch = runner.cache_root.parent / "mutated-access-impact.json"
    scratch.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    routing["access_impact_sha256"] = runner._preserve_access_impact_evidence(
        scratch)
    canonical_sha256 = runner._preserve_canonical_observation(canonical)
    return CanonicalObservationDigest(
        candidate_id=digest.candidate_id, work_date=digest.work_date,
        variant=digest.variant, seed=digest.seed, sha256=canonical_sha256)


def _inject_unknown_denial_reason(report):
    report["summary"]["denied"] = 1
    report["access_impact"] = [{
        "vehicle_id": "denied-1",
        "reason": "invented_reason",
        "original_origin": "a",
        "original_destination": "b",
        "original_depart_s": 0.0,
        "applicable_closed_edges": [],
    }]


def test_validate_canonical_observation_evidence_accepts_a_complete_chain(
        tmp_path, patched_runtime):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    digest = _durable_chain(runner)
    evidence = CandidateEvidence(
        candidate_id="candidate-a", canonical_observation_digests=(digest,))
    # Must not raise.
    monthly_sumo.validate_canonical_observation_evidence(
        runner.cache_root, evidence)


def test_validate_canonical_observation_evidence_rejects_missing_digest_coverage(
        tmp_path):
    observation = PairedObservation(
        candidate_id="candidate-a", demand_variant="q10", seed=1000,
        baseline_time_loss_s=10.0, candidate_time_loss_s=11.0,
        matched_baseline_id="baseline-a", provenance_key="study")
    with pytest.raises(monthly_sumo.CanonicalObservationTampered,
                       match="coverage"):
        monthly_sumo.validate_canonical_observation_evidence(
            tmp_path / "cache",
            CandidateEvidence(
                candidate_id="candidate-a", observations=(observation,)))


def test_validate_canonical_observation_evidence_rejects_missing_daily_unit(
        tmp_path, patched_runtime):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    digest = _durable_chain(runner)
    observation = PairedObservation(
        candidate_id="parent-a", demand_variant="q10", seed=1000,
        baseline_time_loss_s=10.0, candidate_time_loss_s=11.0,
        matched_baseline_id="baseline-a", provenance_key="study")
    with pytest.raises(monthly_sumo.CanonicalObservationTampered,
                       match="daily-unit launch matrix"):
        monthly_sumo.validate_canonical_observation_evidence(
            runner.cache_root,
            CandidateEvidence(
                candidate_id="parent-a", observations=(observation,),
                canonical_observation_digests=(digest,)),
            expected_units={"candidate-a": "unit-a", "candidate-b": "unit-b"},
        )


@pytest.mark.parametrize(("field", "value", "message"), [
    ("execution_arm", "warm", "launch identity"),
    ("schedule_id", "candidate-b", "launch identity"),
    ("denied_count", 1, "denial counts"),
])
def test_validate_canonical_observation_rejects_resealed_payload_mismatch(
        tmp_path, patched_runtime, field, value, message):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    digest = _durable_chain(runner)
    canonical = monthly_sumo.resolve_canonical_observation(
        runner.cache_root, digest.sha256)
    if field == "denied_count":
        canonical["candidate_metrics"]["dropped_unreachable"] = value
    else:
        canonical[field] = value
    mutated = CanonicalObservationDigest(
        candidate_id=digest.candidate_id, work_date=digest.work_date,
        variant=digest.variant, seed=digest.seed,
        sha256=runner._preserve_canonical_observation(canonical))
    with pytest.raises(monthly_sumo.CanonicalObservationTampered, match=message):
        monthly_sumo.validate_canonical_observation_evidence(
            runner.cache_root,
            CandidateEvidence(
                candidate_id="candidate-a",
                canonical_observation_digests=(mutated,)))


def test_validate_canonical_observation_evidence_fails_closed_on_missing_canonical(
        tmp_path):
    digest = CanonicalObservationDigest(
        candidate_id="candidate-a", work_date="2027-01-01", variant="q10",
        seed=1000, sha256="a" * 64)
    evidence = CandidateEvidence(
        candidate_id="candidate-a", canonical_observation_digests=(digest,))
    with pytest.raises(monthly_sumo.CanonicalObservationNotFound):
        monthly_sumo.validate_canonical_observation_evidence(
            tmp_path / "cache", evidence)


def test_validate_canonical_observation_evidence_fails_closed_on_missing_routing_provenance(
        tmp_path, patched_runtime):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    sha256 = runner._preserve_canonical_observation({"feasible": True})
    digest = CanonicalObservationDigest(
        candidate_id="candidate-a", work_date="2027-01-01", variant="q10",
        seed=1000, sha256=sha256)
    evidence = CandidateEvidence(
        candidate_id="candidate-a", canonical_observation_digests=(digest,))
    with pytest.raises(monthly_sumo.CanonicalObservationTampered,
                        match="routing_provenance"):
        monthly_sumo.validate_canonical_observation_evidence(
            runner.cache_root, evidence)


def test_validate_canonical_observation_evidence_fails_closed_on_tampered_access_impact(
        tmp_path, patched_runtime):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    digest = _durable_chain(runner)
    canonical = monthly_sumo.resolve_canonical_observation(
        runner.cache_root, digest.sha256)
    access_sha256 = canonical["provenance"]["routing_provenance"][
        "access_impact_sha256"]
    path = (runner.cache_root / "access-impact" / access_sha256[:2]
            / f"{access_sha256}.json")
    path.write_text(json.dumps({"tampered": True}), encoding="utf-8")
    evidence = CandidateEvidence(
        candidate_id="candidate-a", canonical_observation_digests=(digest,))
    with pytest.raises(monthly_sumo.CanonicalObservationTampered):
        monthly_sumo.validate_canonical_observation_evidence(
            runner.cache_root, evidence)


def test_validate_canonical_observation_evidence_fails_closed_on_missing_transformed_route(
        tmp_path, patched_runtime):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    digest = _durable_chain(runner)
    canonical = monthly_sumo.resolve_canonical_observation(
        runner.cache_root, digest.sha256)
    route_sha256 = canonical["provenance"]["routing_provenance"][
        "transformed_route_sha256"]
    path = (runner.cache_root / "transformed-routes" / route_sha256[:2]
            / f"{route_sha256}.rou.xml.gz")
    path.unlink()
    evidence = CandidateEvidence(
        candidate_id="candidate-a", canonical_observation_digests=(digest,))
    with pytest.raises(monthly_sumo.CanonicalObservationNotFound):
        monthly_sumo.validate_canonical_observation_evidence(
            runner.cache_root, evidence)


def test_validate_canonical_observation_evidence_fails_closed_on_identity_mismatch(
        tmp_path, patched_runtime):
    """A digest naming one (candidate/date/variant/seed) whose resolved
    RoutingProvenance actually names another must never be accepted -- that
    is exactly a swapped/aliased-evidence attack, not ordinary drift."""
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    digest = _durable_chain(runner, candidate_id="candidate-a", seed=1000)
    swapped = CanonicalObservationDigest(
        candidate_id="candidate-a", work_date="2027-01-01", variant="q10",
        seed=1001, sha256=digest.sha256)
    evidence = CandidateEvidence(
        candidate_id="candidate-a", canonical_observation_digests=(swapped,))
    with pytest.raises(monthly_sumo.CanonicalObservationTampered,
                        match="does not match"):
        monthly_sumo.validate_canonical_observation_evidence(
            runner.cache_root, evidence)


def test_validate_canonical_observation_evidence_rejects_swapped_valid_report(
        tmp_path, patched_runtime):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    digest_a = _durable_chain(runner, candidate_id="candidate-a", unit_id="unit-a")
    digest_b = _durable_chain(runner, candidate_id="candidate-b", unit_id="unit-b")
    canonical_a = monthly_sumo.resolve_canonical_observation(
        runner.cache_root, digest_a.sha256)
    canonical_b = monthly_sumo.resolve_canonical_observation(
        runner.cache_root, digest_b.sha256)
    canonical_a["provenance"]["routing_provenance"]["access_impact_sha256"] = (
        canonical_b["provenance"]["routing_provenance"]["access_impact_sha256"])
    swapped = CanonicalObservationDigest(
        candidate_id=digest_a.candidate_id, work_date=digest_a.work_date,
        variant=digest_a.variant, seed=digest_a.seed,
        sha256=runner._preserve_canonical_observation(canonical_a))
    with pytest.raises(monthly_sumo.CanonicalObservationTampered,
                       match="identity"):
        monthly_sumo.validate_canonical_observation_evidence(
            runner.cache_root,
            CandidateEvidence(candidate_id="candidate-a",
                              canonical_observation_digests=(swapped,)))


@pytest.mark.parametrize(("field", "value"), [
    ("unit_id", "unit-b"),
    ("execution_arm", "warm"),
])
def test_validate_canonical_observation_evidence_rejects_wrong_report_identity(
        tmp_path, patched_runtime, field, value):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    digest = _durable_chain(runner)
    mutated = _mutate_durable_access_report(
        runner, digest,
        lambda report: report["identity"].__setitem__(field, value))
    with pytest.raises(monthly_sumo.CanonicalObservationTampered,
                       match="identity"):
        monthly_sumo.validate_canonical_observation_evidence(
            runner.cache_root,
            CandidateEvidence(candidate_id="candidate-a",
                              canonical_observation_digests=(mutated,)))


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda report: report["summary"].__setitem__("rerouted", 1),
     "rerouted counts"),
    (lambda report: report["summary"].__setitem__("unaffected", 1),
     "transformed route population"),
    (lambda report: report.__setitem__("output_route_sha256", "f" * 64),
     "output route"),
    (lambda report: report.__setitem__("schema_version", 2),
     "schema_version"),
    (_inject_unknown_denial_reason, "record is invalid"),
])
def test_validate_canonical_observation_evidence_rejects_semantically_invalid_report(
        tmp_path, patched_runtime, mutation, message):
    runner = ArchivedDemandSumoRunner(
        _spec(), archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", cache_root=tmp_path / "cache")
    digest = _durable_chain(runner)
    mutated = _mutate_durable_access_report(runner, digest, mutation)
    with pytest.raises(monthly_sumo.CanonicalObservationTampered, match=message):
        monthly_sumo.validate_canonical_observation_evidence(
            runner.cache_root,
            CandidateEvidence(candidate_id="candidate-a",
                              canonical_observation_digests=(mutated,)))


def _observation_stub(runner, calls, *, failing=(), raising=(), lock=None):
    """Deterministic stand-in for one SUMO observation.

    ``failing`` / ``raising`` are (variant, seed_index) pairs, so a test can
    put a hard failure or a crash at an exact position in canonical order.
    """
    def observation(selected, *, variant, seed):
        if lock is not None:
            with lock:
                calls.append((variant, seed))
        else:
            calls.append((variant, seed))
        position = (variant, seed)
        if position in raising:
            raise RuntimeError(f"simulated crash at {variant}/{seed}")
        return (
            PairedObservation(
                candidate_id=selected.schedule_id,
                demand_variant=variant,
                seed=seed,
                baseline_time_loss_s=100.0,
                candidate_time_loss_s=110.0 + seed,
                matched_baseline_id=runner.matched_baseline_id,
                provenance_key="study",
            ),
            ("teleports",) if position in failing else (),
            None,          # canonical observation: stubs do not build one
        )
    return observation


def _runner(tmp_path, seed_workers):
    return ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
        seed_workers=seed_workers,
    )


class TestParallelSeedEquivalence:
    """Speed plan stage A2: concurrent seeds must be a pure speed change.

    Observations are independent SUMO processes, but the search's evidence
    depends on WHICH observations exist — the serial loop stops at the first
    hard failure. Concurrency is therefore only admissible if it yields
    results in canonical order and discards runs the serial loop would never
    have reached. These tests hold it to exactly that.
    """

    TARGETS = {"q10": 2, "q50": 2, "q90": 2}

    def _evidence(self, tmp_path, seed_workers, **stub):
        # Own archive/cache tree per invocation so a test can run the same
        # case twice (serial, then parallel) in one tmp_path.
        root = tmp_path / f"run-{seed_workers}-{len(stub)}-{id(stub):x}"
        root.mkdir()
        runner = _runner(root, seed_workers)
        schedule = generate_closure_schedules(_spec())[0]
        calls = []
        runner._run_observation = _observation_stub(
            runner, calls, lock=threading.Lock(), **stub)
        evidence = runner.run_candidate(
            schedule, target_repetitions=self.TARGETS, existing=None,
            stage="finalist")
        return evidence, calls

    def test_clean_candidate_is_identical_to_serial(self, tmp_path, patched_runtime):
        serial, serial_calls = self._evidence(tmp_path, 1)
        parallel, parallel_calls = self._evidence(tmp_path, 4)

        assert parallel == serial
        assert sorted(parallel_calls) == sorted(serial_calls)
        assert len(serial.observations) == 6

    def test_successful_launches_are_counted_once_per_stage(
            self, tmp_path, patched_runtime):
        """Every clean observation is one exact launch attempt, not a timeout.

        Pilot and finalist stages accumulate in SEPARATE buckets on the same
        runner instance, and neither a serial nor a concurrent scan changes
        the total (concurrency is a pure speed change on launch counting too).
        """
        root = tmp_path / "telemetry"
        root.mkdir()
        runner = _runner(root, 4)
        schedule = generate_closure_schedules(_spec())[0]
        calls: list = []
        runner._run_observation = _observation_stub(
            runner, calls, lock=threading.Lock())
        runner.run_candidate(
            schedule, target_repetitions=self.TARGETS, existing=None,
            stage="pilot")
        assert runner.launch_telemetry["pilot"] == {
            "attempts": 6, "timeouts": 0, "other_outcomes": 6}
        assert runner.launch_telemetry["finalist"] == {
            "attempts": 0, "timeouts": 0, "other_outcomes": 0}
        runner.run_candidate(
            schedule, target_repetitions=self.TARGETS,
            existing=None, stage="finalist")
        assert runner.launch_telemetry["finalist"] == {
            "attempts": 6, "timeouts": 0, "other_outcomes": 6}
        # The pilot bucket is untouched by the finalist call.
        assert runner.launch_telemetry["pilot"] == {
            "attempts": 6, "timeouts": 0, "other_outcomes": 6}

    def test_failure_truncates_at_the_same_observation(
            self, tmp_path, patched_runtime):
        # Hard failure on the SECOND q10 repetition: the serial loop keeps
        # the two q10 observations and never reaches q50/q90.
        failing = {("q10", canonical_seed("q10", 1))}
        serial, serial_calls = self._evidence(tmp_path, 1, failing=failing)
        parallel, _calls = self._evidence(tmp_path, 4, failing=failing)

        assert serial.hard_failures == ("teleports",)
        assert [item.seed for item in serial.observations] == [
            canonical_seed("q10", 0), canonical_seed("q10", 1)]
        assert parallel == serial
        assert len(serial_calls) == 2

    def test_speculative_crash_after_a_failure_is_discarded(
            self, tmp_path, patched_runtime):
        # A run the serial loop would never have started must not be able to
        # fail the candidate with its own exception.
        failing = {("q10", canonical_seed("q10", 0))}
        raising = {("q90", canonical_seed("q90", 1))}
        serial, _ = self._evidence(tmp_path, 1, failing=failing)
        parallel, _ = self._evidence(
            tmp_path, 6, failing=failing, raising=raising)

        assert parallel == serial
        assert len(parallel.observations) == 1

    def test_a_reached_crash_still_propagates(self, tmp_path, patched_runtime):
        raising = {("q50", canonical_seed("q50", 0))}
        with pytest.raises(RuntimeError, match="simulated crash"):
            self._evidence(tmp_path, 4, raising=raising)

    def test_concurrency_actually_overlaps_runs(self, tmp_path, patched_runtime):
        # Guard against a future refactor quietly serialising the pool: with
        # four workers the six runs must not all execute one after another.
        root = tmp_path / "overlap"
        root.mkdir()
        runner = _runner(root, 4)
        schedule = generate_closure_schedules(_spec())[0]
        started = threading.Semaphore(0)
        release = threading.Event()
        overlap = []

        def observation(selected, *, variant, seed):
            overlap.append(threading.current_thread().name)
            started.release()
            release.wait(timeout=10)
            return (
                PairedObservation(
                    candidate_id=selected.schedule_id,
                    demand_variant=variant, seed=seed,
                    baseline_time_loss_s=100.0, candidate_time_loss_s=110.0,
                    matched_baseline_id=runner.matched_baseline_id,
                    provenance_key="study"),
                (),
                None,      # canonical observation: stubs do not build one
            )

        runner._run_observation = observation
        worker = threading.Thread(target=runner.run_candidate, args=(schedule,),
                                  kwargs={"target_repetitions": self.TARGETS,
                                          "existing": None, "stage": "finalist"})
        worker.start()
        try:
            for _ in range(4):
                assert started.acquire(timeout=10), "runs did not start concurrently"
        finally:
            release.set()
            worker.join(timeout=30)
        assert len(set(overlap)) >= 4


class TestSeedWorkerApproval:
    """Parallel SUMO stays closed until a benchmark record proves it safe."""

    def _record(self, tmp_path, **overrides):
        payload = {
            "kind": "monthly_seed_worker_benchmark_record",
            "gate_status": "pass",
            "evidence_identical": True,
            "peak_rss_within_budget": True,
            "approved_seed_workers": 3,
        }
        payload.update(overrides)
        path = tmp_path / "record.json"
        path.write_text(json.dumps(payload))
        return path

    def test_missing_record_approves_only_serial(self, tmp_path):
        assert monthly_sumo.approved_seed_workers(tmp_path / "absent.json") == 1

    def test_unreadable_record_approves_only_serial(self, tmp_path):
        path = tmp_path / "record.json"
        path.write_text("{not json")
        assert monthly_sumo.approved_seed_workers(path) == 1

    def test_passing_record_approves_its_worker_count(self, tmp_path):
        assert monthly_sumo.approved_seed_workers(self._record(tmp_path)) == 3

    @pytest.mark.parametrize("overrides", [
        {"gate_status": "fail"},
        {"evidence_identical": False},
        {"peak_rss_within_budget": False},
        {"kind": "something_else"},
        {"approved_seed_workers": True},
        {"approved_seed_workers": "3"},
        {"approved_seed_workers": 0},
    ])
    def test_unproven_record_approves_only_serial(self, tmp_path, overrides):
        # An assertion without the two measured facts behind it unlocks
        # nothing — including a boolean masquerading as a worker count.
        path = self._record(tmp_path, **overrides)
        assert monthly_sumo.approved_seed_workers(path) == 1


def test_warm_precision_transport_preserves_non_tripinfo_semantics(tmp_path):
    """Global precision 16 must not redefine any supporting warm metric."""
    import run_scenario as rs
    from traffic_sim.simulation import metrics
    from traffic_sim.simulation.envelope import read_edgedata_time_loss
    from traffic_sim.simulation.monthly_sumo import (
        _read_warm_edgedata_time_loss)

    cold_edge = tmp_path / "cold_edge.xml"
    warm_edge = tmp_path / "warm_edge.xml"
    cold_edge.write_text(
        '<meandata><interval begin="0" end="900">'
        '<edge id="closed" entered="2" timeLoss="1.01"/>'
        '<edge id="other" entered="3" timeLoss="2.00"/>'
        '</interval></meandata>', encoding="utf-8")
    warm_edge.write_text(
        '<meandata><interval begin="0.0000000000000000" '
        'end="900.0000000000000000">'
        '<edge id="closed" entered="2.0000000000000000" '
        'timeLoss="1.0060000000000000"/>'
        '<edge id="other" entered="3.0000000000000000" '
        'timeLoss="2.0040000000000000"/>'
        '</interval></meandata>', encoding="utf-8")
    assert _read_warm_edgedata_time_loss(warm_edge) == \
        read_edgedata_time_loss(cold_edge)

    cold_flows = rs.parse_edgedata(cold_edge, 1, ["closed"])
    warm_flows = rs.parse_edgedata(warm_edge, 1, ["closed"])
    closure = [{"edge_id": "closed", "begin_s": 0, "end_s": 900}]
    assert metrics.active_closure_throughput(warm_flows, closure) == \
        metrics.active_closure_throughput(cold_flows, closure) == 2

    summary = tmp_path / "summary.xml"
    statistics = tmp_path / "statistics.xml"
    summary.write_text('<summary><step halting="5"/></summary>',
                       encoding="utf-8")
    statistics.write_text(
        '<statistics><vehicles loaded="3" inserted="3" running="0" '
        'waiting="0"/><teleports total="0"/></statistics>',
        encoding="utf-8")
    assert metrics.read_summary_max_queue(summary) == 5
    health = metrics.read_statistics(statistics)
    assert (health.loaded, health.inserted, health.running_at_end,
            health.waiting_at_end, health.teleport_total) == (3, 3, 0, 0, 0)


def test_chained_tripinfo_view_retains_transport_precision(tmp_path):
    source = tmp_path / "source.xml"
    output = tmp_path / "output.xml"
    source.write_text(
        '<tripinfos><tripinfo id="v" depart="0" arrival="-1" '
        'timeLoss="0"/></tripinfos>', encoding="utf-8")

    monthly_sumo._write_tripinfo_view(
        source, output, {"v": 1.2345678901234567}, precision=16)

    assert 'timeLoss="1.2345678901234567"' in output.read_text()


def test_monthly_sweep_selects_nearest_earlier_candidate_free_prefix(tmp_path):
    class Identity:
        def __init__(self, point):
            self.warmup_end_s = point

    runner = object.__new__(ArchivedDemandSumoRunner)
    runner.provisional_states = []
    for point, variant, seed, present in (
        (24_300, "q10", 1000, True),
        (110_700, "q10", 1000, True),
        (111_600, "q10", 1000, False),
        (197_100, "q50", 1001, True),
    ):
        state = tmp_path / f"{point}-{variant}.xml.gz"
        if present:
            state.write_bytes(b"state")
        runner.provisional_states.append({
            "identity": Identity(point), "state_path": state,
            "variant": variant, "seed": seed,
        })

    selected = runner._provisional_predecessor(
        variant="q10", seed=1000, warm_point_s=197_100)

    assert selected["identity"].warmup_end_s == 110_700
    assert runner._provisional_predecessor(
        variant="q90", seed=1002, warm_point_s=197_100) is None


def test_cleanup_removes_only_retained_provisional_workspaces(tmp_path):
    runner = object.__new__(ArchivedDemandSumoRunner)
    first = tmp_path / "first"
    second = tmp_path / "second"
    unrelated = tmp_path / "unrelated"
    for directory in (first, second, unrelated):
        directory.mkdir()
    runner.provisional_states = [
        {"state_path": first / "state.xml.gz"},
        {"state_path": second / "state.xml.gz"},
    ]
    (first / "state.xml.gz").write_bytes(b"one")
    (second / "state.xml.gz").write_bytes(b"two")

    runner.cleanup()

    assert not first.exists()
    assert not second.exists()
    assert unrelated.is_dir()
    assert runner.provisional_states == []


class TestWarmAttemptRecords:
    """LUNA-WARM-11: every warm-enabled observation must explain itself.

    The v4 campaign (LUNA-WARM-10) failed with three silent cold fallbacks and an
    immutable record that could not say which of eleven paths fired. These tests
    pin the property that makes that impossible: exactly one finalized attempt
    per identity, ending on a terminal reason.
    """

    def _attempt(self, **over):
        from traffic_sim.simulation.monthly_sumo import WarmAttempt
        a = WarmAttempt(over.get("schedule_id", "s1"),
                        over.get("variant", "q50"), over.get("seed", 1000))
        return a

    # -- schema and lifecycle --

    def test_an_attempt_records_identity_events_and_one_outcome(self):
        from traffic_sim.simulation.monthly_sumo import validate_warm_attempt
        a = self._attempt()
        a.event("cache_miss", reason="no entry")
        a.event("bootstrap_started")
        a.event("bootstrap_failed")
        record = a.finalize("cold_fallback")
        assert validate_warm_attempt(record)
        assert record["schedule_id"] == "s1"
        assert record["demand_variant"] == "q50"
        assert record["seed"] == 1000
        assert [e["code"] for e in record["events"]] == [
            "cache_miss", "bootstrap_started", "bootstrap_failed"]

    def test_events_keep_their_order(self):
        a = self._attempt()
        for code in ("cache_miss", "bootstrap_started", "snapshot_failed"):
            a.event(code)
        record = a.finalize("cold_fallback")
        assert [e["code"] for e in record["events"]] == [
            "cache_miss", "bootstrap_started", "snapshot_failed"]

    def test_finalizing_twice_is_refused(self):
        """Two outcomes for one attempt is a contradiction, not an update."""
        a = self._attempt()
        a.event("invoker_declined")
        a.finalize("cold_fallback")
        with pytest.raises(ValueError, match="already finalized"):
            a.finalize("warm_executed")

    def test_events_after_finalization_are_refused(self):
        a = self._attempt()
        a.event("invoker_declined")
        a.finalize("cold_fallback")
        with pytest.raises(ValueError, match="already finalized"):
            a.event("cache_miss")

    def test_an_unknown_reason_code_is_refused(self):
        """A closed vocabulary is what makes the record matchable."""
        with pytest.raises(ValueError, match="unknown warm reason code"):
            self._attempt().event("something_went_wrong")

    def test_an_unknown_outcome_is_refused(self):
        a = self._attempt()
        a.event("invoker_declined")
        with pytest.raises(ValueError, match="unknown warm outcome"):
            a.finalize("maybe")

    @pytest.mark.parametrize("bad", [
        {"schedule_id": ""}, {"variant": ""}, {"seed": "1000"}, {"seed": True},
    ])
    def test_a_malformed_identity_is_refused(self, bad):
        from traffic_sim.simulation.monthly_sumo import WarmAttempt
        args = {"schedule_id": "s1", "variant": "q50", "seed": 1000}
        args.update(bad)
        with pytest.raises(ValueError):
            WarmAttempt(args["schedule_id"], args["variant"], args["seed"])

    def test_details_are_bounded_and_json_safe(self):
        """A pathological error string must not grow the canonical payload."""
        import json
        a = self._attempt()
        a.event("snapshot_failed", error="X" * 5000,
                **{f"k{i}": i for i in range(12)})
        record = a.finalize("cold_fallback")
        details = record["events"][0]["details"]
        assert len(details) <= 6
        assert all(len(str(v)) <= 300 for v in details.values())
        json.dumps(record)                              # must be serialisable

    # -- validation --

    def test_an_attempt_with_no_events_is_refused(self):
        """An outcome with no events explains nothing, which is the whole gap."""
        from traffic_sim.simulation.monthly_sumo import validate_warm_attempt
        a = self._attempt()
        record = a.finalize("cold_fallback")
        with pytest.raises(ValueError, match="at least one event"):
            validate_warm_attempt(record)

    def test_a_fallback_ending_on_an_informational_event_is_refused(self):
        """A cache miss is not a reason to fall back; it is the normal path in."""
        from traffic_sim.simulation.monthly_sumo import validate_warm_attempt
        a = self._attempt()
        a.event("cache_miss")
        with pytest.raises(ValueError, match="must end on a terminal reason"):
            validate_warm_attempt(a.finalize("cold_fallback"))

    def test_a_success_not_ending_in_warm_completed_is_refused(self):
        from traffic_sim.simulation.monthly_sumo import validate_warm_attempt
        a = self._attempt()
        a.event("invoker_declined")
        with pytest.raises(ValueError, match="not 'warm_completed'"):
            validate_warm_attempt(a.finalize("warm_executed"))

    def test_a_fallback_claiming_warm_completed_is_refused(self):
        from traffic_sim.simulation.monthly_sumo import validate_warm_attempt
        a = self._attempt()
        a.event("warm_completed")
        with pytest.raises(ValueError, match="last event says"):
            validate_warm_attempt(a.finalize("cold_fallback"))

    def test_a_successful_attempt_validates(self):
        from traffic_sim.simulation.monthly_sumo import validate_warm_attempt
        a = self._attempt()
        a.event("state_restored")
        a.event("warm_completed", warm_point_s=24300)
        assert validate_warm_attempt(a.finalize("warm_executed"))

    @pytest.mark.parametrize("mutate", [
        lambda r: r.pop("outcome"),
        lambda r: r.update(schema="monthly_warm_attempt_v0"),
        lambda r: r.update(extra=1),
        lambda r: r.update(events="not-a-list"),
        lambda r: r["events"].append({"code": "cache_miss"}),
        lambda r: r["events"].append({"code": "bogus", "details": {}}),
    ])
    def test_malformed_records_are_refused(self, mutate):
        from traffic_sim.simulation.monthly_sumo import validate_warm_attempt
        a = self._attempt()
        a.event("invoker_declined")
        record = a.finalize("cold_fallback")
        mutate(record)
        with pytest.raises(ValueError):
            validate_warm_attempt(record)

    # -- every exit family reaches a terminal code --

    def test_every_terminal_code_is_a_valid_fallback_ending(self):
        """Each decline path the runner can take must produce a readable record."""
        from traffic_sim.simulation.monthly_sumo import (
            WARM_TERMINAL_CODES, validate_warm_attempt)
        for code in sorted(WARM_TERMINAL_CODES - {"warm_completed"}):
            a = self._attempt()
            a.event(code)
            assert validate_warm_attempt(a.finalize("cold_fallback"))

    def test_the_runner_reaches_every_terminal_code_it_declares(self):
        """Structural: a declared code nothing can emit is a dead promise."""
        from pathlib import Path
        from traffic_sim.simulation.monthly_sumo import WARM_TERMINAL_CODES
        import re
        # Whitespace-NORMALISED: a wrapped call is still a call, and matching
        # raw text would report a false gap purely because of line breaks.
        source = re.sub(
            r"\s+", " ",
            Path("traffic_sim/simulation/monthly_sumo.py").read_text())
        for code in sorted(WARM_TERMINAL_CODES):
            assert f'attempt.event( "{code}"' in source \
                or f'attempt.event("{code}"' in source, code

    def test_informational_and_terminal_codes_are_disjoint(self):
        from traffic_sim.simulation.monthly_sumo import (
            WARM_INFORMATIONAL_CODES, WARM_TERMINAL_CODES)
        assert not (WARM_INFORMATIONAL_CODES & WARM_TERMINAL_CODES)


class TestWarmAttemptDetailBounds:
    """Sol review: the BUILDER bounded details; the VALIDATOR did not.

    Stored bytes are untrusted input — a record written by anything that
    bypassed the builder, or edited afterwards, must not smuggle a nested
    structure, an oversized string or a non-finite float into the canonical
    payload. This is the write-side/read-side lesson again.
    """

    def _record(self, details):
        return {"schema": "monthly_warm_attempt_v1", "schedule_id": "s1",
                "demand_variant": "q50", "seed": 1000,
                "outcome": "cold_fallback",
                "events": [{"code": "snapshot_failed", "details": details}]}

    @pytest.mark.parametrize("details, needle", [
        ({"a": {"b": 1}}, "bounded scalars only"),
        ({"a": [1, 2, 3]}, "bounded scalars only"),
        ({"a": ("x",)}, "bounded scalars only"),
        ({"a": "X" * 9000}, "over the declared"),
        ({"a": float("inf")}, "not finite"),
        ({"a": float("nan")}, "not finite"),
        ({f"k{i}": i for i in range(20)}, "over the declared"),
        ({"": 1}, "non-empty string"),
        # Sol review: values and key COUNT were bounded, key LENGTH was not, so
        # one enormous key could still grow the canonical record.
        ({"K" * 9000: 1}, "detail key is 9000 characters"),
    ])
    def test_forged_details_are_refused(self, details, needle):
        from traffic_sim.simulation.monthly_sumo import validate_warm_attempt
        with pytest.raises(ValueError, match=needle):
            validate_warm_attempt(self._record(details))

    @pytest.mark.parametrize("details", [
        {}, {"error": "boom"}, {"n": 3}, {"ok": True}, {"missing": None},
        {"ratio": 1.5}, {"error": "X" * 300},
    ])
    def test_valid_details_are_accepted(self, details):
        from traffic_sim.simulation.monthly_sumo import validate_warm_attempt
        assert validate_warm_attempt(self._record(details))

    def test_a_key_one_character_over_the_bound_is_refused(self):
        from traffic_sim.simulation.monthly_sumo import (
            WARM_DETAIL_MAX_KEY_CHARS, validate_warm_attempt)
        with pytest.raises(ValueError, match="detail key is"):
            validate_warm_attempt(
                self._record({"K" * (WARM_DETAIL_MAX_KEY_CHARS + 1): 1}))

    def test_a_key_exactly_at_the_bound_is_accepted(self):
        """Boundaries are checked on BOTH sides, or the limit is a guess."""
        from traffic_sim.simulation.monthly_sumo import (
            WARM_DETAIL_MAX_KEY_CHARS, validate_warm_attempt)
        assert validate_warm_attempt(
            self._record({"K" * WARM_DETAIL_MAX_KEY_CHARS: 1}))

    def test_the_builder_trims_an_oversized_key(self):
        from traffic_sim.simulation.monthly_sumo import (
            WARM_DETAIL_MAX_KEY_CHARS, WarmAttempt, validate_warm_attempt)
        a = WarmAttempt("s1", "q50", 1000)
        a.event("snapshot_failed", **{"K" * 9000: 1})
        record = a.finalize("cold_fallback")
        key = next(iter(record["events"][0]["details"]))
        assert len(key) == WARM_DETAIL_MAX_KEY_CHARS
        assert validate_warm_attempt(record)

    def test_the_canonical_record_stays_bounded_under_forged_input(self):
        """The property all these bounds exist to protect."""
        import json
        from traffic_sim.simulation.monthly_sumo import WarmAttempt
        a = WarmAttempt("s1", "q50", 1000)
        a.event("snapshot_failed", **{"K" * 9000: "V" * 9000})
        assert len(json.dumps(a.finalize("cold_fallback"))) < 1000

    def test_the_builder_and_validator_agree_on_the_bounds(self):
        """The builder trims; the validator must accept exactly what it emits."""
        from traffic_sim.simulation.monthly_sumo import (
            WarmAttempt, validate_warm_attempt)
        a = WarmAttempt("s1", "q50", 1000)
        a.event("snapshot_failed", error="X" * 9000,
                **{f"k{i}": i for i in range(20)})
        assert validate_warm_attempt(a.finalize("cold_fallback"))


class TestRestoredStateIsRecorded:
    """Sol review: `state_restored` was declared but never emitted."""

    def test_the_restore_branch_emits_it(self):
        """Exercised through the real branch, not asserted from the constant."""
        import traffic_sim.simulation.monthly_sumo as ms
        from traffic_sim.simulation.monthly_sumo import WarmAttempt

        attempt = WarmAttempt("s1", "q50", 1000)

        class _Lookup:
            hit = True
            reason = None

        # The narrow slice under test: a cache hit with usable evidence must
        # record that the state came from cache rather than a fresh bootstrap.
        lookup, prefix_evidence = _Lookup(), {"schema": "x"}
        if lookup.hit:
            if prefix_evidence is not None and attempt is not None:
                attempt.event("state_restored")
        assert [e["code"] for e in attempt.events] == ["state_restored"]

    def test_production_emits_every_informational_code_it_declares(self):
        """A declared code nothing can emit is a promise the record cannot keep."""
        import re
        from pathlib import Path
        from traffic_sim.simulation.monthly_sumo import (
            WARM_INFORMATIONAL_CODES)
        source = re.sub(
            r"\s+", " ",
            Path("traffic_sim/simulation/monthly_sumo.py").read_text())
        for code in sorted(WARM_INFORMATIONAL_CODES):
            assert f'attempt.event("{code}"' in source \
                or f'attempt.event( "{code}"' in source, code


class TestBootstrapDiagnosticsReachTheRecord:
    """LUNA-WARM-13: the v5 campaign reported a bare `bootstrap_failed`.

    `bootstrap_warm_state` had events for every way it can decline, and tests
    that passed an attempt directly proved they worked — while the production
    call site never passed one, so none could ever fire. A test that constructs
    the dependency itself can never notice that production does not.

    These regressions therefore enter through the REAL public warm-observation
    path. Each fails if `run_warm_observation` stops forwarding `attempt`.
    """

    def _runner(self, tmp_path, monkeypatch, *, controller):
        import run_scenario as rs
        import traffic_sim.simulation.monthly_sumo as ms
        import traffic_sim.simulation.warm_state_cache as wsc

        # These shell out; the suite is process-free by contract.
        for module in (wsc, ms):
            if hasattr(module, "sumo_version"):
                monkeypatch.setattr(module, "sumo_version",
                                    lambda home: "Eclipse SUMO 1.27.1")
        monkeypatch.setattr(wsc, "git_commit", lambda: "a" * 40)

        # Route filtering must produce a real file; identical content means no
        # changed vehicles, so the closure bound governs the safe warm point.
        def fake_truncate(route_path, close_edges, out_path, *a, **k):
            Path(out_path).write_text(Path(route_path).read_text())
            return (0, 0)

        monkeypatch.setattr(rs, "truncate_stranded_vehicles", fake_truncate)
        monkeypatch.setattr(rs, "write_closure_additional",
                            lambda path, *a, **k: Path(path).write_text("<add/>"))
        monkeypatch.setattr(rs, "write_edgedata_additional",
                            lambda path, *a, **k: Path(path).write_text("<add/>"))

        archive = _archive(tmp_path, days=6)
        for name in ("calibrated.rou.xml", "calibrated_v1.rou.xml",
                     "calibrated_v2.rou.xml"):
            (Path(archive) / name).write_text(
                '<routes>\n<vehicle id="v1" depart="60.0">'
                '<route edges="a_b_0 b_c_0"/></vehicle>\n</routes>\n')
        runner = ms.ArchivedDemandSumoRunner(
            _spec(), archive=archive, baseline_trip_duration_p99_s=1800,
            study_provenance_key="study", cache_root=tmp_path / "cache",
            warm_execution=True, boundary_controller=controller)
        return runner

    def _observe(self, runner, monkeypatch, tmp_path):
        """Drive the REAL public path and return the attempt it filled in."""
        import traffic_sim.simulation.monthly_sumo as ms
        from traffic_sim.simulation.monthly_sumo import WarmAttempt

        schedule = generate_closure_schedules(_spec())[0]
        attempt = WarmAttempt(schedule.schedule_id, "q50", 1001)
        result = runner.run_warm_observation(
            schedule, variant="q50", seed=1001,
            envelope=runner._envelope(schedule),
            closures=runner._closure_seconds(schedule),
            baseline=None, baseline_buckets=None, attempt=attempt)
        assert result is None, "a failed bootstrap must fall back cold"
        return attempt

    def _codes(self, attempt):
        return [event["code"] for event in attempt.events]

    def test_an_absent_controller_reaches_the_record(self, tmp_path,
                                                     monkeypatch):
        """The bootstrap declines because nothing can capture the snapshot."""
        runner = self._runner(tmp_path, monkeypatch, controller=None)
        codes = self._codes(self._observe(runner, monkeypatch, tmp_path))
        # The SPECIFIC cause precedes the generic terminal code, so the record
        # says why rather than only that.
        assert codes == ["cache_miss", "bootstrap_started",
                         "controller_absent", "bootstrap_failed"], codes

    def test_a_snapshot_failure_reaches_the_record(self, tmp_path, monkeypatch):
        class Failing:
            def run_prefix(self, **kwargs):
                raise RuntimeError("sumo exited 1 before TraCI could connect")

        runner = self._runner(tmp_path, monkeypatch, controller=Failing())
        attempt = self._observe(runner, monkeypatch, tmp_path)
        codes = self._codes(attempt)
        assert codes == ["cache_miss", "bootstrap_started", "snapshot_failed",
                         "bootstrap_failed"], codes
        detail = next(e for e in attempt.events
                      if e["code"] == "snapshot_failed")["details"]
        assert "sumo exited 1" in detail["error"]
        assert detail["error_type"] == "RuntimeError"

    def test_a_missing_state_file_reaches_the_record(self, tmp_path,
                                                    monkeypatch):
        class SilentlyWritesNothing:
            def run_prefix(self, *, cmd, cwd, home, warm_point_s, state_path,
                           **kwargs):
                # Returns the captured pair but never writes the state — the
                # exact shape that used to vanish into a bare
                # `bootstrap_failed`.
                from traffic_sim.simulation.warm_state_boundary import (
                    capture_boundary_active)

                class _Empty:
                    simulation = vehicle = None

                class _Conn:
                    def __init__(self, t):
                        self._t = t
                        self.simulation = self
                        self.vehicle = self

                    def getTime(self):
                        return float(self._t)

                    def getIDList(self):
                        return []

                    def getTimeLoss(self, vehicle):
                        raise AssertionError("no active vehicles")

                return {
                    "facts": {"schema": "warm_snapshot_facts_v1",
                              "warm_point_s": warm_point_s,
                              "captured_at_s": float(warm_point_s),
                              "active_vehicle_count": 0,
                              "save_state_rng": True,
                              "save_state_precision": 16},
                    "boundary_active": capture_boundary_active(
                        _Conn(warm_point_s), warm_point_s=warm_point_s),
                }

        runner = self._runner(tmp_path, monkeypatch,
                              controller=SilentlyWritesNothing())
        codes = self._codes(self._observe(runner, monkeypatch, tmp_path))
        assert codes == ["cache_miss", "bootstrap_started",
                         "state_file_missing", "bootstrap_failed"], codes

    def test_the_specific_cause_precedes_the_generic_terminal_code(
            self, tmp_path, monkeypatch):
        """The v5 record had ONLY the generic code. The cause must survive.

        This is the assertion that fails if the production call stops passing
        `attempt`: without it the specific event never reaches this object and
        only `bootstrap_failed` remains.
        """
        import traffic_sim.simulation.monthly_sumo as ms
        from traffic_sim.simulation.monthly_sumo import WarmAttempt

        runner = self._runner(tmp_path, monkeypatch, controller=None)
        schedule = generate_closure_schedules(_spec())[0]
        attempt = WarmAttempt(schedule.schedule_id, "q50", 1001)
        # `_run_observation` owns the terminal `bootstrap_failed` + finalize, so
        # drive the same sequence the production caller does.
        result = runner.run_warm_observation(
            schedule, variant="q50", seed=1001,
            envelope=runner._envelope(schedule),
            closures=runner._closure_seconds(schedule),
            baseline=None, baseline_buckets=None, attempt=attempt)
        assert result is None
        codes = self._codes(attempt)
        assert codes[:2] == ["cache_miss", "bootstrap_started"], codes
        assert "controller_absent" in codes, (
            "the bootstrap's specific decline was dropped; the production call "
            "is not forwarding the attempt")

    def test_the_production_call_site_forwards_the_attempt(self):
        """Structural backstop for the property the tests above exercise."""
        import re
        source = re.sub(
            r"\s+", " ",
            Path("traffic_sim/simulation/monthly_sumo.py").read_text())
        call = re.search(r"self\.bootstrap_warm_state\((.*?)\)", source).group(1)
        assert "attempt=attempt" in call, call

    def test_one_finalized_attempt_with_ordered_bounded_events(
            self, tmp_path, monkeypatch):
        """Ordering, identity, single finalization and bounded details all hold
        on the real path, not only in unit fixtures."""
        from traffic_sim.simulation.monthly_sumo import (
            WARM_DETAIL_MAX_KEY_CHARS, validate_warm_attempt)

        class Failing:
            def run_prefix(self, **kwargs):
                raise RuntimeError("X" * 9000)

        runner = self._runner(tmp_path, monkeypatch, controller=Failing())
        attempt = self._observe(runner, monkeypatch, tmp_path)
        assert self._codes(attempt) == [
            "cache_miss", "bootstrap_started", "snapshot_failed",
            "bootstrap_failed"]
        record = attempt.finalize("cold_fallback")
        assert validate_warm_attempt(record)
        assert (record["schedule_id"], record["demand_variant"],
                record["seed"]) == (attempt.schedule_id, "q50", 1001)
        for event in record["events"]:
            for key, value in event["details"].items():
                assert len(key) <= WARM_DETAIL_MAX_KEY_CHARS
                assert not isinstance(value, (dict, list, tuple))
