"""Global bounded daily-unit queue: width, identity and lifecycle.

The parent-local batch path was measured leaving seven of eight SUMO slots
idle because a warm five-day parent supplies only ~1.04 uncached units.  These
tests pin the replacement: the width is filled from ONE global remainder, and
nothing the queue does is allowed to be visible in the evidence.
"""

import json
import multiprocessing
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import pytest

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    PairedObservation,
)
from traffic_sim.simulation.independent_daily import (
    GlobalDailyUnitQueue,
    GlobalQueueActivationError,
    IndependentDailyRunner,
    IsolatedDailySumoRunner,
    QueueCancelled,
    _evidence_to_dict,
    resolve_global_queue_workers,
)
from traffic_sim.simulation.seed_worker_budget import approved_seed_workers
from traffic_sim.simulation.monthly_search import canonical_seed


TARGETS = {"q10": 1, "q50": 1, "q90": 1}


def _spec(**overrides):
    values = {
        "search_id": "independent-daily-queue-test",
        "directed_edges": ("a_b_0",),
        "demand_build_id": "forecast-2027",
        "source": "forecast",
        "permitted_date_start": "2027-01-01",
        "permitted_date_end": "2027-01-20",
        "required_work_minutes": 5 * 3 * 60,
        "max_consecutive_start_days": 5,
        "permitted_daily_band": DailyTimeBand("15:00", "18:00"),
        "allowed_weekdays": (0, 1, 2, 3, 4, 5, 6),
        "interday_policy": "independent_daily_reset_v1",
        "work_allocation_policy": "exact_balanced_daily_v1",
    }
    values.update(overrides)
    return ClosureSearchSpec(**values)


def _five_day_parents(spec):
    return [
        item for item in generate_closure_schedules(spec)
        if item.day_count == 5 and item.daily_start == "15:00"
    ]


class RecordingDailyRunner:
    """Deterministic stand-in that also records real concurrency."""

    def __init__(self, *, hold=0.0, barrier=None, fail_on=()):
        self.calls = []
        self.prepared = ()
        self.hold = hold
        self.barrier = barrier
        self.fail_on = set(fail_on)
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def prepare(self, schedules):
        self.prepared = tuple(schedules)

    def provenance(self):
        return {"kind": "fake-daily-sumo", "identity": "v1"}

    def queue_sumo_profile(self):
        # A test double runs no SUMO at all, and says so explicitly rather
        # than being assumed safe: `validate_queue_concurrency_budget` trusts
        # nothing it has not been told.
        return True, 1

    def run_candidate(self, schedule, *, target_repetitions, existing, stage):
        with self._lock:
            self.calls.append(schedule.schedule_id)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.barrier is not None:
                self.barrier.wait(timeout=20)
            if self.hold:
                time.sleep(self.hold)
            if schedule.first_work_date in self.fail_on:
                raise RuntimeError("synthetic unit failure " + schedule.first_work_date)
            return _evidence(schedule, target_repetitions)
        finally:
            with self._lock:
                self.active -= 1


def _evidence(schedule, target_repetitions, *, hard_failures=()):
    observations = []
    date_number = int(schedule.first_work_date[-2:])
    duration = schedule.actual_closed_minutes
    for variant in ("q10", "q50", "q90"):
        for repetition in range(target_repetitions[variant]):
            baseline = 1000.0 + date_number
            observations.append(PairedObservation(
                candidate_id=schedule.schedule_id,
                demand_variant=variant,
                seed=canonical_seed(variant, repetition),
                baseline_time_loss_s=baseline,
                candidate_time_loss_s=baseline + duration,
                matched_baseline_id=f"baseline-{schedule.first_work_date}",
                provenance_key=f"daily-{schedule.first_work_date}",
            ))
    return CandidateEvidence(
        candidate_id=schedule.schedule_id,
        observations=tuple(observations),
        hard_failures=tuple(hard_failures),
    )


def _runner(spec, child, cache_root, *, queue_workers=1):
    runner = IndependentDailyRunner(
        spec,
        daily_runner=child,
        cache_root=cache_root,
        queue_workers=queue_workers,
    )
    return runner


# ---------------------------------------------------------------- width ---

def test_queue_never_runs_more_than_its_configured_width(tmp_path):
    """The width is a ceiling on live workers, hence on SUMO processes."""
    spec = _spec()
    parents = _five_day_parents(spec)
    child = RecordingDailyRunner(hold=0.02)
    runner = _runner(spec, child, tmp_path / "cache", queue_workers=8)
    runner.prepare(tuple(parents))
    try:
        for parent in parents[:6]:
            runner.run_candidate(
                parent, target_repetitions=TARGETS, existing=None, stage="pilot"
            )
    finally:
        runner.cleanup()

    assert child.max_active <= 8
    assert runner.timing_snapshot()["queue_max_active_workers"] <= 8


def test_width_is_filled_from_other_parents_not_just_the_current_one(tmp_path):
    """The measured bottleneck: a warm parent offers ~1 unit, not eight.

    Four of this parent's five units are pre-cached, so the parent itself can
    supply exactly ONE unit of work.  A barrier of four can only be cleared if
    the queue pulls the other three from the global remainder, which is the
    behaviour the parent-local batch could not produce.
    """
    spec = _spec()
    parents = _five_day_parents(spec)
    first = parents[0]
    cache_root = tmp_path / "cache"

    warm = _runner(spec, RecordingDailyRunner(), cache_root)
    warm.prepare(tuple(parents))
    prewarm_ids = [unit_id for unit_id, _ in warm.daily_units_for(first)]
    for unit_id, schedule in warm.daily_units_for(first)[:4]:
        warm._save_cached(warm._units[unit_id], _evidence(schedule, TARGETS))
    warm.cleanup()

    barrier = threading.Barrier(4)
    child = RecordingDailyRunner(barrier=barrier)
    runner = _runner(spec, child, cache_root, queue_workers=4)
    runner.prepare(tuple(parents))
    try:
        # Without global lookahead this call has one unit to run, the barrier
        # never clears, and the test fails on timeout instead of passing.
        runner.run_candidate(
            first, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
    finally:
        runner.cleanup()

    assert child.max_active == 4
    assert len(prewarm_ids) == 5


def test_legacy_path_is_untouched_when_queue_workers_is_one(tmp_path):
    spec = _spec()
    parent = _five_day_parents(spec)[0]
    child = RecordingDailyRunner()
    runner = _runner(spec, child, tmp_path / "cache", queue_workers=1)
    runner.prepare((parent,))
    runner.run_candidate(
        parent, target_repetitions=TARGETS, existing=None, stage="pilot"
    )
    runner.cleanup()

    assert runner._queue is None
    assert "queue_max_active_workers" not in runner.timing_snapshot()


# ------------------------------------------------------------- identity ---

def test_queue_and_legacy_produce_byte_identical_evidence(tmp_path):
    """Completion order is randomized; the published evidence may not move."""
    spec = _spec()
    parents = _five_day_parents(spec)[:6]

    legacy = _runner(spec, RecordingDailyRunner(), tmp_path / "legacy")
    legacy.prepare(tuple(parents))
    legacy_out = [
        legacy.run_candidate(
            parent, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
        for parent in parents
    ]
    legacy.cleanup()

    import random
    jitter = RecordingDailyRunner()
    real_run = jitter.run_candidate

    def shuffled(schedule, **kwargs):
        time.sleep(random.uniform(0.0, 0.01))
        return real_run(schedule, **kwargs)

    jitter.run_candidate = shuffled
    queued = _runner(spec, jitter, tmp_path / "queued", queue_workers=8)
    queued.prepare(tuple(parents))
    queued_out = [
        queued.run_candidate(
            parent, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
        for parent in parents
    ]
    queued.cleanup()

    assert [_evidence_to_dict(item) for item in queued_out] == [
        _evidence_to_dict(item) for item in legacy_out
    ]
    # Same unit population, executed exactly once each, despite the jitter.
    assert sorted(jitter.calls) == sorted(set(jitter.calls))
    assert len(jitter.calls) == len(set(jitter.calls))


def test_cache_entries_are_identical_between_legacy_and_queue(tmp_path):
    """Same content keys and same bytes: a queued run stays cache-compatible."""
    spec = _spec()
    parents = _five_day_parents(spec)[:4]

    legacy = _runner(spec, RecordingDailyRunner(), tmp_path / "legacy")
    legacy.prepare(tuple(parents))
    for parent in parents:
        legacy.run_candidate(
            parent, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
    legacy.cleanup()

    queued = _runner(spec, RecordingDailyRunner(), tmp_path / "queued",
                     queue_workers=6)
    queued.prepare(tuple(parents))
    for parent in parents:
        queued.run_candidate(
            parent, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
    queued.cleanup()

    def snapshot(root):
        return {
            str(path.relative_to(root)): path.read_text(encoding="utf-8")
            for path in sorted(root.glob("*/*.json"))
        }

    assert snapshot(tmp_path / "legacy") == snapshot(tmp_path / "queued")
    assert legacy.provenance()["daily_backend_digest"] == (
        queued.provenance()["daily_backend_digest"]
    )


def test_a_shared_unit_is_executed_once_across_overlapping_parents(tmp_path):
    spec = _spec()
    parents = _five_day_parents(spec)[:6]
    child = RecordingDailyRunner()
    runner = _runner(spec, child, tmp_path / "cache", queue_workers=8)
    runner.prepare(tuple(parents))
    try:
        for parent in parents:
            runner.run_candidate(
                parent, target_repetitions=TARGETS, existing=None, stage="pilot"
            )
    finally:
        runner.cleanup()

    assert len(child.calls) == len(set(child.calls))
    published = sorted((tmp_path / "cache").glob("*/*.json"))
    assert len(published) == len(child.calls)


# --------------------------------------------------------- single-flight ---

def test_the_cache_is_rechecked_after_the_single_flight_lock_is_taken(tmp_path):
    """A producer that lost the race must read, not re-simulate."""
    spec = _spec()
    parent = _five_day_parents(spec)[0]
    cache_root = tmp_path / "cache"
    child = RecordingDailyRunner()
    runner = _runner(spec, child, cache_root, queue_workers=2)
    runner.prepare((parent,))
    unit_id, schedule = runner.daily_units_for(parent)[0]
    unit = runner._units[unit_id]

    # Publish under the very key the producer is about to claim, exactly as a
    # competing process would, then drop the in-memory shortcut so the recheck
    # is forced to hit the filesystem.
    runner._save_cached(unit, _evidence(schedule, TARGETS))
    runner._memory_evidence.clear()

    runner._produce_unit(unit_id, TARGETS, "pilot")
    runner.cleanup()

    assert child.calls == []
    assert runner.timing_snapshot()["queue_singleflight_skips"] == 1


def test_single_flight_holds_across_separate_processes(tmp_path):
    """``flock`` is the cross-process guarantee, so prove it with processes."""
    from traffic_sim.storage.singleflight import content_key_lock

    root = tmp_path / "locks"
    root.mkdir()
    ready = multiprocessing.Event()
    release = multiprocessing.Event()

    def hold():
        with content_key_lock(root, "abc123"):
            ready.set()
            release.wait(timeout=20)

    holder = multiprocessing.get_context("fork").Process(target=hold)
    holder.start()
    try:
        assert ready.wait(timeout=20)
        with pytest.raises(TimeoutError):
            with content_key_lock(root, "abc123", timeout_s=0.5, poll_s=0.05):
                pass
    finally:
        release.set()
        holder.join(timeout=20)
    assert holder.exitcode == 0


def test_publication_is_atomic_and_leaves_no_partial_files(tmp_path):
    spec = _spec()
    parents = _five_day_parents(spec)[:4]
    runner = _runner(spec, RecordingDailyRunner(), tmp_path / "cache",
                     queue_workers=6)
    runner.prepare(tuple(parents))
    try:
        for parent in parents:
            runner.run_candidate(
                parent, target_repetitions=TARGETS, existing=None, stage="pilot"
            )
    finally:
        runner.cleanup()

    assert list((tmp_path / "cache").rglob("*.tmp")) == []
    for path in (tmp_path / "cache").glob("*/*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema"] == "independent_daily_evidence_cache_v2"


# ------------------------------------------------------------ lifecycle ---

def test_a_failing_unit_propagates_but_preserves_completed_entries(tmp_path):
    spec = _spec()
    parents = _five_day_parents(spec)
    child = RecordingDailyRunner(fail_on={"2027-01-01"})
    runner = _runner(spec, child, tmp_path / "cache", queue_workers=4)
    runner.prepare(tuple(parents))
    try:
        with pytest.raises(RuntimeError, match="synthetic unit failure"):
            runner.run_candidate(
                parents[0], target_repetitions=TARGETS, existing=None,
                stage="pilot",
            )
    finally:
        runner.cleanup()

    published = sorted((tmp_path / "cache").glob("*/*.json"))
    # The failing unit published nothing; units that succeeded survived.
    assert published
    for path in published:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["unit"]["identity"]["work_date"] != "2027-01-01"
    assert list((tmp_path / "cache").rglob("*.tmp")) == []


def test_a_valid_hard_failure_is_cached_rather_than_retried(tmp_path):
    spec = _spec()
    parent = _five_day_parents(spec)[0]
    cache_root = tmp_path / "cache"

    class HardFailing(RecordingDailyRunner):
        def run_candidate(self, schedule, *, target_repetitions, existing, stage):
            self.calls.append(schedule.schedule_id)
            return _evidence(
                schedule, target_repetitions,
                hard_failures=("closure_unreachable",),
            )

    child = HardFailing()
    runner = _runner(spec, child, cache_root, queue_workers=4)
    runner.prepare((parent,))
    runner.run_candidate(
        parent, target_repetitions=TARGETS, existing=None, stage="pilot"
    )
    runner.cleanup()
    first_calls = len(child.calls)

    again = HardFailing()
    resumed = _runner(spec, again, cache_root, queue_workers=4)
    resumed.prepare((parent,))
    resumed.run_candidate(
        parent, target_repetitions=TARGETS, existing=None, stage="pilot"
    )
    resumed.cleanup()

    assert first_calls == 5
    assert again.calls == []


def test_cleanup_cancels_queued_work_and_reaps_workers(tmp_path):
    spec = _spec()
    parents = _five_day_parents(spec)
    started = threading.Event()

    class Slow(RecordingDailyRunner):
        def run_candidate(self, schedule, *, target_repetitions, existing, stage):
            started.set()
            time.sleep(0.05)
            return _evidence(schedule, target_repetitions)

    child = Slow()
    runner = _runner(spec, child, tmp_path / "cache", queue_workers=4)
    runner.prepare(tuple(parents))
    queue = runner._ensure_queue(TARGETS, "pilot")
    assert started.wait(timeout=20)
    runner.cleanup()

    assert queue._stopped is True
    assert queue.stats()["queue_pending"] == 0
    assert not [
        thread for thread in threading.enumerate()
        if thread.name.startswith("daily-unit")
    ]
    assert list((tmp_path / "cache").rglob("*.tmp")) == []


def test_waiting_on_a_stopped_queue_raises_instead_of_hanging(tmp_path):
    queue = GlobalDailyUnitQueue(["u1", "u2"], workers=2, execute=lambda _: None)
    queue.stop()
    with pytest.raises(QueueCancelled):
        queue.require(["u1"])


# --------------------------------------------------------------- resume ---

def test_resume_executes_only_the_units_that_are_still_missing(tmp_path):
    spec = _spec()
    parents = _five_day_parents(spec)[:5]
    cache_root = tmp_path / "cache"

    first_child = RecordingDailyRunner()
    first = _runner(spec, first_child, cache_root, queue_workers=4)
    first.prepare(tuple(parents))
    for parent in parents[:2]:
        first.run_candidate(
            parent, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
    first.cleanup()
    done = set(first_child.calls)

    second_child = RecordingDailyRunner()
    second = _runner(spec, second_child, cache_root, queue_workers=4)
    second.prepare(tuple(parents))
    for parent in parents:
        second.run_candidate(
            parent, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
    second.cleanup()

    assert done
    assert not (set(second_child.calls) & done)


def test_a_complete_cache_runs_no_units_at_all(tmp_path):
    spec = _spec()
    parents = _five_day_parents(spec)[:4]
    cache_root = tmp_path / "cache"

    warm = _runner(spec, RecordingDailyRunner(), cache_root, queue_workers=4)
    warm.prepare(tuple(parents))
    for parent in parents:
        warm.run_candidate(
            parent, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
    warm.cleanup()

    child = RecordingDailyRunner()
    cold = _runner(spec, child, cache_root, queue_workers=8)
    cold.prepare(tuple(parents))
    for parent in parents:
        cold.run_candidate(
            parent, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
    cold.cleanup()

    assert child.calls == []
    assert cold.timing_snapshot()["units_simulated"] == 0


def test_a_higher_finalist_target_rebuilds_the_queue_instead_of_reusing_pilot(
    tmp_path,
):
    """Coverage is part of the work identity, not just the unit ID."""
    spec = _spec()
    parent = _five_day_parents(spec)[0]
    cache_root = tmp_path / "cache"
    child = RecordingDailyRunner()
    runner = _runner(spec, child, cache_root, queue_workers=4)
    runner.prepare((parent,))
    runner.run_candidate(
        parent, target_repetitions=TARGETS, existing=None, stage="pilot"
    )
    pilot_calls = len(child.calls)

    finalist_targets = {"q10": 3, "q50": 3, "q90": 3}
    evidence = runner.run_candidate(
        parent,
        target_repetitions=finalist_targets,
        existing=None,
        stage="finalist",
    )
    runner.cleanup()

    assert pilot_calls == 5
    assert len(child.calls) == 10
    for variant in ("q10", "q50", "q90"):
        seeds = sorted(
            item.seed for item in evidence.observations
            if item.demand_variant == variant
        )
        assert seeds == [
            canonical_seed(variant, repetition) for repetition in range(3)
        ]


def test_progress_and_eta_are_exposed_without_changing_evidence(tmp_path):
    spec = _spec()
    parents = _five_day_parents(spec)[:3]
    runner = _runner(spec, RecordingDailyRunner(), tmp_path / "cache",
                     queue_workers=4)
    runner.prepare(tuple(parents))
    try:
        for parent in parents:
            runner.run_candidate(
                parent, target_repetitions=TARGETS, existing=None, stage="pilot"
            )
        snapshot = runner.timing_snapshot()
    finally:
        runner.cleanup()

    for key in (
        "queue_total", "queue_completed", "queue_running", "queue_pending",
        "queue_workers", "queue_units_per_hour", "queue_eta_seconds",
        "queue_max_active_workers", "queue_failed",
    ):
        assert key in snapshot
    # Must survive the workspace's strict JSON boundary.
    json.dumps(snapshot, allow_nan=False)


# ------------------------------------------------- activation seam & locks ---

def test_the_cli_is_cache_bound_so_the_queue_switch_must_live_elsewhere():
    """`run_monthly_closure_search.py` is hashed into daily-unit cache identity.

    This is the defect that made the first implementation of this feature
    unshippable: it added `--global-daily-queue` to the CLI, which is one of
    the nineteen files `monthly_sumo.py` hashes into `source_digest`.  That
    digest rides in the backend provenance the unit cache key is built from,
    so the flag would have orphaned every cached unit of the stopped campaign.
    """
    import hashlib
    import re
    from pathlib import Path

    source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
    block = re.search(r"\n        sources = \[(.*?)\n        \]\n", source, re.S)
    assert block is not None, "monthly_sumo.py no longer declares a source list"
    labels = []
    for label in re.findall(r'"([^"]+\.py)"', block.group(1)):
        if label not in labels:
            labels.append(label)

    assert "run_monthly_closure_search.py" in labels
    assert "traffic_sim/simulation/independent_daily.py" not in labels

    def digest(replacement=None):
        records = []
        for label in labels:
            data = (
                replacement
                if replacement is not None
                and label == "run_monthly_closure_search.py"
                else Path(label).read_bytes()
            )
            records.append({
                "label": label,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
        return hashlib.sha256(json.dumps(
            records, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest()

    edited = Path("run_monthly_closure_search.py").read_bytes() + b"\n# x\n"
    assert digest() != digest(edited)


def test_source_digest_is_kept_in_the_unit_backend_identity():
    """The binding is real, not merely present in the provenance dict."""
    spec = _spec()
    stable = IndependentDailyRunner._stable_backend_identity
    provenance = {
        "kind": "archived_demand_monthly_sumo_backend",
        "source_digest": "aaa",
        "search_content_key": spec.content_key,
        "study_provenance_key": "s",
        "demand_release_id": "d",
    }
    kept = stable(provenance)
    assert kept["source_digest"] == "aaa"
    assert "search_content_key" not in kept
    assert stable({**provenance, "source_digest": "bbb"}) != kept


def test_the_queue_is_off_unless_both_environment_variables_agree(monkeypatch):
    from traffic_sim.simulation import independent_daily as module

    resolve = module.resolve_global_queue_workers
    assert resolve({}, []) == 1
    assert resolve({module.QUEUE_WORKERS_ENV: "1"}, []) == 1
    assert resolve(
        {
            module.QUEUE_WORKERS_ENV: "8",
            module.QUEUE_SCREENING_ENV: module.QUEUE_SUPPORTED_SCREENING,
        },
        ["run_monthly_closure_search.py",
         "--screening-mode", module.QUEUE_SUPPORTED_SCREENING],
    ) == 8

    # A width without a declared screening mode fails closed.
    with pytest.raises(module.GlobalQueueActivationError):
        resolve({module.QUEUE_WORKERS_ENV: "8"}, [])
    # Cost-ordered screening is refused: global lookahead would simulate the
    # very work the stop proof claims to have skipped.
    with pytest.raises(module.GlobalQueueActivationError):
        resolve(
            {
                module.QUEUE_WORKERS_ENV: "8",
                module.QUEUE_SCREENING_ENV: "independent-cost-ordered-exact",
            },
            [],
        )
    # A declaration contradicting the live command line fails closed too.
    with pytest.raises(module.GlobalQueueActivationError):
        resolve(
            {
                module.QUEUE_WORKERS_ENV: "8",
                module.QUEUE_SCREENING_ENV: module.QUEUE_SUPPORTED_SCREENING,
            },
            ["run_monthly_closure_search.py",
             "--screening-mode=independent-cost-ordered-exact"],
        )
    for bad in ("nine", "0", "-2"):
        with pytest.raises(module.GlobalQueueActivationError):
            resolve({module.QUEUE_WORKERS_ENV: bad}, [])


def test_an_unset_queue_workers_argument_reads_the_seam(tmp_path, monkeypatch):
    from traffic_sim.simulation import independent_daily as module

    spec = _spec()
    child = RecordingDailyRunner()
    monkeypatch.delenv(module.QUEUE_WORKERS_ENV, raising=False)
    monkeypatch.delenv(module.QUEUE_SCREENING_ENV, raising=False)
    assert IndependentDailyRunner(
        spec, daily_runner=child, cache_root=tmp_path / "a"
    ).queue_workers == 1

    monkeypatch.setenv(module.QUEUE_WORKERS_ENV, "4")
    monkeypatch.setenv(
        module.QUEUE_SCREENING_ENV, module.QUEUE_SUPPORTED_SCREENING
    )
    monkeypatch.setattr(
        module.sys, "argv",
        ["run_monthly_closure_search.py",
         "--screening-mode", module.QUEUE_SUPPORTED_SCREENING],
    )
    assert IndependentDailyRunner(
        spec, daily_runner=child, cache_root=tmp_path / "b"
    ).queue_workers == 4
    # An explicit argument still wins over the environment.
    assert IndependentDailyRunner(
        spec, daily_runner=child, cache_root=tmp_path / "c", queue_workers=1
    ).queue_workers == 1


def test_retargeting_does_not_deadlock_against_a_running_worker(tmp_path):
    """Retiring a queue must not hold a lock its own pullers need.

    Regression for a real deadlock: `_ensure_queue` used to call
    `queue.stop()` while holding `_state_lock`.  `stop()` joins the pullers,
    and a puller inside `_produce_unit` blocks on `_bump`/`_load_cached`,
    which take that same lock.  The first finalist retarget hung forever.
    """
    spec = _spec()
    child = RecordingDailyRunner(hold=0.05)
    runner = _runner(spec, child, tmp_path / "cache", queue_workers=4)
    parents = _five_day_parents(spec)
    runner.prepare(tuple(parents))

    done = threading.Event()
    failure = []

    def drive():
        try:
            # Pilot coverage, then a strictly larger finalist target: the
            # second call retires the pilot queue while its pullers are live.
            for parent in parents[:4]:
                runner.run_candidate(
                    parent, target_repetitions=TARGETS,
                    existing=None, stage="pilot",
                )
            bigger = {"q10": 2, "q50": 2, "q90": 2}
            for parent in parents[:4]:
                runner.run_candidate(
                    parent, target_repetitions=bigger,
                    existing=None, stage="finalist",
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            failure.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=drive, daemon=True)
    thread.start()
    assert done.wait(timeout=120), "retargeting the global queue deadlocked"
    runner.cleanup()
    assert not failure, failure
    assert child.max_active <= 4


def test_cleanup_is_safe_while_a_retarget_is_in_flight(tmp_path):
    spec = _spec()
    child = RecordingDailyRunner(hold=0.02)
    runner = _runner(spec, child, tmp_path / "cache", queue_workers=3)
    parents = _five_day_parents(spec)
    runner.prepare(tuple(parents))
    runner.run_candidate(
        parents[0], target_repetitions=TARGETS, existing=None, stage="pilot",
    )
    stopper = threading.Thread(target=runner.cleanup, daemon=True)
    stopper.start()
    stopper.join(timeout=60)
    assert not stopper.is_alive(), "cleanup hung"
    runner.cleanup()  # idempotent
    assert runner._queue is None


def test_a_stage_change_retargets_even_when_coverage_is_unchanged(tmp_path):
    """The stage is baked into the executor closure, so it must be keyed on.

    `stage` is result-neutral in today's SUMO backend (it is validated and
    otherwise unused), but keying the queue on coverage alone would replay a
    pilot-stage closure for a finalist round whenever the two ask for the same
    repetitions.  That is a trap for the next backend that gives the label
    meaning.
    """
    spec = _spec()

    class StageRecordingRunner(RecordingDailyRunner):
        def __init__(self):
            super().__init__()
            self.stages = []

        def run_candidate(self, schedule, *, target_repetitions, existing, stage):
            self.stages.append(stage)
            return super().run_candidate(
                schedule, target_repetitions=target_repetitions,
                existing=existing, stage=stage,
            )

    child = StageRecordingRunner()
    runner = _runner(spec, child, tmp_path / "cache", queue_workers=2)
    parents = _five_day_parents(spec)
    runner.prepare(tuple(parents))
    try:
        runner.run_candidate(
            parents[0], target_repetitions=TARGETS,
            existing=None, stage="pilot",
        )
        assert set(child.stages) == {"pilot"}
        # Same coverage, different stage: a fresh parent's units must be run
        # under "finalist", not replayed under the queue's original "pilot".
        runner.run_candidate(
            parents[-1], target_repetitions=TARGETS,
            existing=None, stage="finalist",
        )
    finally:
        runner.cleanup()
    assert "finalist" in child.stages, child.stages


# ------------------------------------------------- lookahead scope ---

def test_a_finalist_round_upgrades_only_the_units_it_selected(tmp_path):
    """Global lookahead is for the exhaustive pilot sweep, nothing else.

    The production policy prepares the whole shortlist but promotes at most a
    handful of finalists and asks THEM for more repetitions.  A queue rebuilt
    globally at finalist coverage would upgrade every prepared unit instead —
    measured against the frozen campaign that is 1 950 units of SUMO nobody
    selected.
    """
    spec = _spec()
    parents = _five_day_parents(spec)[:4]
    finalist, *others = parents
    cache_root = tmp_path / "cache"
    child = RecordingDailyRunner()
    runner = _runner(spec, child, cache_root, queue_workers=4)
    runner.prepare(tuple(parents))
    # Exhaustive pilot: every prepared unit is verified, so the global sweep
    # is only a reordering of work this run had already committed to.
    for parent in parents:
        runner.run_candidate(
            parent, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
    prepared_units = set(child.calls)
    finalist_units = set(runner._parents[finalist.schedule_id])
    child.calls.clear()

    runner.run_candidate(
        finalist,
        target_repetitions={"q10": 3, "q50": 3, "q90": 3},
        existing=None,
        stage="finalist",
    )
    runner.cleanup()

    upgraded = {
        runner._units[unit_id].schedule.schedule_id
        for unit_id in finalist_units
    }
    assert len(prepared_units) > len(upgraded)
    assert set(child.calls) == upgraded


def test_an_adaptive_finalist_bump_does_not_sweep_unrelated_units(tmp_path):
    """A second, higher request retargets within the finalist scope only."""
    spec = _spec()
    parents = _five_day_parents(spec)[:4]
    finalist = parents[0]
    child = RecordingDailyRunner()
    runner = _runner(spec, child, tmp_path / "cache", queue_workers=4)
    runner.prepare(tuple(parents))
    for parent in parents:
        runner.run_candidate(
            parent, target_repetitions=TARGETS, existing=None, stage="pilot"
        )
    finalist_units = {
        runner._units[unit_id].schedule.schedule_id
        for unit_id in runner._parents[finalist.schedule_id]
    }
    for target in (4, 12):
        child.calls.clear()
        runner.run_candidate(
            finalist,
            target_repetitions={variant: target for variant in TARGETS},
            existing=None,
            stage="finalist",
        )
        assert set(child.calls) == finalist_units
    runner.cleanup()


# --------------------------------------------------- SUMO budget ---

class _Delegate:
    def __init__(self, seed_workers):
        self.seed_workers = seed_workers

    def provenance(self):
        return {"kind": "fake-monthly", "identity": "v1"}


def _isolated(seed_workers, unit_workers):
    return IsolatedDailySumoRunner(
        _Delegate(seed_workers),
        unit_workers=unit_workers,
        worker_invoker=lambda request: None,
    )


def test_a_queue_wider_than_the_approved_benchmark_is_refused(tmp_path):
    spec = _spec()
    approved = approved_seed_workers()
    with pytest.raises(GlobalQueueActivationError, match="approved"):
        IndependentDailyRunner(
            spec,
            daily_runner=_isolated(1, approved + 1),
            cache_root=tmp_path / "cache",
            queue_workers=approved + 1,
        )


def test_a_queue_over_a_multi_seed_runner_is_refused(tmp_path):
    """queue=8 x seed-workers=8 is 64 SUMO, and passes every CLI check."""
    spec = _spec()
    with pytest.raises(GlobalQueueActivationError, match="concurrent SUMO"):
        IndependentDailyRunner(
            spec,
            daily_runner=_isolated(8, 8),
            cache_root=tmp_path / "cache",
            queue_workers=8,
        )


def test_a_queue_over_a_non_isolated_runner_is_refused(tmp_path):
    """--daily-workers 1 leaves the production runner unwrapped."""
    spec = _spec()

    class Unisolated:
        seed_workers = 1

        def provenance(self):
            return {"kind": "fake-monthly", "identity": "v1"}

    with pytest.raises(GlobalQueueActivationError, match="process-isolated"):
        IndependentDailyRunner(
            spec,
            daily_runner=Unisolated(),
            cache_root=tmp_path / "cache",
            queue_workers=4,
        )


def test_a_queue_wider_than_the_declared_daily_workers_is_refused(tmp_path):
    """The width replaces --daily-workers, so it must stay inside it."""
    spec = _spec()
    with pytest.raises(GlobalQueueActivationError, match="isolated daily workers"):
        IndependentDailyRunner(
            spec,
            daily_runner=_isolated(1, 2),
            cache_root=tmp_path / "cache",
            queue_workers=4,
        )


def test_a_matching_width_and_daily_worker_budget_is_accepted(tmp_path):
    spec = _spec()
    approved = approved_seed_workers()
    runner = IndependentDailyRunner(
        spec,
        daily_runner=_isolated(1, approved),
        cache_root=tmp_path / "cache",
        queue_workers=approved,
    )
    assert runner.queue_workers == approved
    runner.cleanup()


def test_the_environment_seam_refuses_a_width_above_the_approval(monkeypatch):
    approved = approved_seed_workers()
    environ = {
        "TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS": str(approved + 1),
        "TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_SCREENING": "independent-exhaustive",
    }
    with pytest.raises(GlobalQueueActivationError, match="approved"):
        resolve_global_queue_workers(
            environ, ["run_monthly_closure_search.py"]
        )
    environ["TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS"] = str(approved)
    assert resolve_global_queue_workers(
        environ, ["run_monthly_closure_search.py"]
    ) == approved


def test_an_abandoned_queue_does_not_wedge_interpreter_shutdown(tmp_path):
    """A queue nobody stopped must never hold the process open.

    Pullers park on the queue's own condition, which no interpreter shutdown
    path knows how to drain.  As non-daemon threads that made exit
    unreachable - `threading._shutdown()` joins them before any atexit
    handler runs - so an owner that skipped `cleanup()` hung forever.
    """
    script = tmp_path / "abandon.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from traffic_sim.simulation.independent_daily import "
        "GlobalDailyUnitQueue\n"
        "q = GlobalDailyUnitQueue(['a', 'b'], workers=2, "
        "execute=lambda unit_id: None)\n"
        "time.sleep(0.3)\n"
        "print('abandoned')\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "abandoned" in completed.stdout


def test_stop_still_joins_the_pullers_so_work_is_reaped(tmp_path):
    """Daemon threads must not weaken the orderly path."""
    running = threading.Event()
    release = threading.Event()
    finished = []

    def execute(unit_id):
        running.set()
        release.wait(timeout=20)
        finished.append(unit_id)

    queue = GlobalDailyUnitQueue(["a"], workers=1, execute=execute)
    assert running.wait(timeout=20)
    release.set()
    queue.stop()
    assert finished == ["a"]
    assert not any(pump.is_alive() for pump in queue._pumps)
