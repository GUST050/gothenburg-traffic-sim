"""Independent-day execution for long recurring closure schedules.

The user-facing schedule remains one candidate, but its daily intervals are
executed as immutable one-day units.  Units shared by overlapping schedules
are cached once, and matched observations are summed by exact
``(demand_variant, seed)`` identity before the robust finalist decision sees
them.  This module never claims continuous inter-day traffic equivalence: the
chosen ``independent_daily_reset_v1`` policy is recorded in every identity and
backend provenance record.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from functools import partial
from typing import Any, Callable, Mapping, Sequence

from traffic_sim.core.contracts import (
    ClosureInterval,
    ClosureSchedule,
    ClosureSearchSpec,
)
from traffic_sim.simulation.finalist_decision import (
    CanonicalObservationDigest,
    CandidateEvidence,
    DEMAND_VARIANTS,
    PairedObservation,
    TimeoutIdentity,
)
from traffic_sim.simulation.envelope import EnvelopePolicy
from traffic_sim.simulation.seed_worker_budget import (
    SEED_WORKER_BENCHMARK_RECORD,
    approved_seed_workers,
)
from traffic_sim.storage.singleflight import content_key_lock


#: v4: v3 made `timeout_undecided` entries validated `TimeoutIdentity`
#: records; v4 retains canonical-observation digests and requires the exact
#: evidence/cache envelopes. Older cache entries fail closed rather than being
#: silently reinterpreted or rewritten.
CACHE_SCHEMA = "independent_daily_evidence_cache_v4"
BACKEND_KIND = "independent_daily_reset_backend_v1"
ISOLATED_BACKEND_KIND = "isolated_daily_sumo_backend_v1"
# Keep the production six-hour recovery cap. Independent reset is permitted
# only BETWEEN separately evaluated work days; it must not make an evening
# closure look better by truncating its own recovery tail. This means a daily
# unit may correctly resolve a two-date archive.
INDEPENDENT_DAILY_ENVELOPE_POLICY = EnvelopePolicy()


# ---------------------------------------------------------------------------
# Global daily-unit queue activation seam
#
# The queue is a SCHEDULER choice: it changes which order units are produced
# in and how many run at once, never what a unit computes.  It must therefore
# be switched on from a file that is NOT bound into the daily-unit cache
# identity, or turning it on would orphan every cached unit.
#
# Measured 2026-08-27: `monthly_sumo.py` hashes NINETEEN source files into
# `source_digest`, and `run_monthly_closure_search.py` is one of them.  That
# digest travels in the backend provenance the unit cache key hashes
# (`_candidate_backend_identity` keeps it; `_stable_backend_identity` only
# drops the four release/search labels).  Adding a single CLI flag to the CLI
# moved `source_digest` from
#   c0bbfc3202bf30c0b1be52dbd5060da3fc7d77e9681466adec7cd2e7ffb0efb0  to
#   8b040d909753823756a10a459186f1e83140e41656c173febd72e351b15bf6d6,
# which would have invalidated all 1 083 cached units of campaign
# ui-monthly-13lhsoy-5d.  `independent_daily.py` is NOT in that set, so the
# switch lives here instead and the CLI stays byte-identical.
QUEUE_WORKERS_ENV = "TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS"
QUEUE_SCREENING_ENV = "TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_SCREENING"
# Global lookahead produces units nobody has asked for yet.  Under exhaustive
# screening that is free - every candidate is verified anyway, so the work is
# only reordered.  Under `independent-cost-ordered-exact` it is NOT free: that
# mode's whole claim is that a stop proof let it SKIP candidates, and a queue
# chewing through the remainder in the background would simulate exactly the
# work the proof says was avoided, making the recorded saving false.  The
# queue is therefore permitted for one declared screening mode only.
QUEUE_SUPPORTED_SCREENING = "independent-exhaustive"
# The ONLY stage whose lookahead may range over the whole prepared shortlist.
# `monthly_search.py` runs exactly two: "pilot", which under exhaustive
# screening verifies every prepared unit, and "finalist", which promotes a
# short list and asks it for more repetitions.  Sweeping the world at
# finalist coverage would simulate ~1 950 units nobody selected.
QUEUE_LOOKAHEAD_STAGE = "pilot"
# How long interpreter shutdown waits for a unit that is already running.
# Long enough to reap an isolated worker that is finishing, short enough that
# a wedged one cannot hold the process open.
QUEUE_SHUTDOWN_GRACE_S = 30.0


class GlobalQueueActivationError(RuntimeError):
    """The global queue was requested in a way that is not safe to honour."""


def _declared_screening_mode(argv: Sequence[str] | None) -> str | None:
    """The `--screening-mode` value on a command line, if it carries one."""
    if not argv:
        return None
    values = list(argv)
    for index, item in enumerate(values):
        if item == "--screening-mode" and index + 1 < len(values):
            return values[index + 1]
        if item.startswith("--screening-mode="):
            return item.split("=", 1)[1]
    return None


def resolve_global_queue_workers(
    environ: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
) -> int:
    """Resolve the opt-in global queue width, failing closed.

    Returns ``1`` - the historical parent-local path, bit-for-bit unchanged -
    unless BOTH variables are set and agree with the actual command line:

    ``TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS``
        a positive integer width.  It is also the SUMO ceiling: the queue
        runs exactly this many puller threads, each of which runs at most one
        isolated worker subprocess, each of which runs SUMO with
        ``seed_workers=1``.
    ``TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_SCREENING``
        must be ``independent-exhaustive``.  Requiring the operator to name
        the mode is what keeps the lookahead from silently invalidating a
        cost-ordered stop proof.

    A malformed width, an unrecognised screening declaration, or a
    declaration that contradicts ``--screening-mode`` on the live command line
    raises instead of quietly falling back, because a silent fallback would
    look exactly like the bug this queue exists to fix.
    """
    environ = os.environ if environ is None else environ
    raw = str(environ.get(QUEUE_WORKERS_ENV, "")).strip()
    if not raw:
        return 1
    try:
        workers = int(raw)
    except ValueError:
        raise GlobalQueueActivationError(
            f"{QUEUE_WORKERS_ENV}={raw!r} is not an integer"
        ) from None
    if workers < 1:
        raise GlobalQueueActivationError(
            f"{QUEUE_WORKERS_ENV}={raw!r} must be a positive integer"
        )
    if workers == 1:
        return 1
    declared = str(environ.get(QUEUE_SCREENING_ENV, "")).strip()
    if not declared:
        raise GlobalQueueActivationError(
            f"{QUEUE_WORKERS_ENV}={workers} also requires "
            f"{QUEUE_SCREENING_ENV}={QUEUE_SUPPORTED_SCREENING}"
        )
    if declared != QUEUE_SUPPORTED_SCREENING:
        raise GlobalQueueActivationError(
            f"the global daily queue supports "
            f"{QUEUE_SUPPORTED_SCREENING!r} only, not {declared!r}: global "
            "lookahead would simulate work a cost-ordered stop proof claims "
            "to have skipped"
        )
    actual = _declared_screening_mode(
        sys.argv if argv is None else argv
    )
    if actual is not None and actual != declared:
        raise GlobalQueueActivationError(
            f"{QUEUE_SCREENING_ENV}={declared!r} contradicts the command "
            f"line's --screening-mode={actual!r}"
        )
    approved = approved_seed_workers()
    if workers > approved:
        raise GlobalQueueActivationError(
            f"{QUEUE_WORKERS_ENV}={workers} exceeds the {approved} concurrent "
            "SUMO workers approved by the recorded resource benchmark "
            f"({SEED_WORKER_BENCHMARK_RECORD}); the queue width IS the SUMO "
            "ceiling, so it may never be declared above the approval"
        )
    return workers


def daily_runner_sumo_profile(daily_runner: Any) -> tuple[bool, int, int | None]:
    """What ONE ``run_candidate`` call on ``daily_runner`` may start.

    Returns ``(process_isolated, sumo_per_call, declared_unit_workers)``.

    The queue calls ``run_candidate`` directly from every puller thread, so
    the honest ceiling is ``queue width x sumo_per_call`` - not the width
    alone.  Two facts make that distinction load-bearing rather than
    theoretical:

    * ``run_monthly_closure_search.py`` only wraps the production runner in
      ``IsolatedDailySumoRunner`` when ``--daily-workers > 1``.  At
      ``--daily-workers 1`` the daily runner IS the production
      ``MonthlySumoRunner``, whose ``WarmPrefixController`` owns one global
      TraCI connection; pulling it from eight threads is precisely the
      sharing that process isolation exists to prevent.
    * the CLI accepts ``--daily-workers 1 --seed-workers 8`` (product 8, at
      the declared slot budget).  An eight-wide queue over an eight-seed
      runner is 64 concurrent SUMO processes while every existing check
      still reads as satisfied.

    A test double that runs no SUMO at all declares itself with
    ``queue_sumo_profile()``; nothing else is trusted to be safe by default.
    """
    declared = getattr(daily_runner, "queue_sumo_profile", None)
    if callable(declared):
        isolated, per_call = declared()
        return bool(isolated), int(per_call), getattr(
            daily_runner, "unit_workers", None
        )
    if isinstance(daily_runner, IsolatedDailySumoRunner):
        delegate = daily_runner.delegate
        per_call = getattr(delegate, "seed_workers", 1)
        try:
            per_call = int(per_call)
        except (TypeError, ValueError):
            per_call = 1
        return True, max(per_call, 1), int(daily_runner.unit_workers)
    per_call = getattr(daily_runner, "seed_workers", 1)
    try:
        per_call = int(per_call)
    except (TypeError, ValueError):
        per_call = 1
    return False, max(per_call, 1), getattr(daily_runner, "unit_workers", None)


def validate_queue_concurrency_budget(
    daily_runner: Any, queue_workers: int
) -> None:
    """Refuse a queue width the approved SUMO budget does not cover.

    Fails closed on every axis the CLI cannot see: process isolation, the
    per-call SUMO fan-out, the declared isolated-runner width, and the
    absolute benchmark approval.
    """
    if queue_workers <= 1:
        return
    isolated, per_call, declared_unit_workers = daily_runner_sumo_profile(
        daily_runner
    )
    if not isolated:
        raise GlobalQueueActivationError(
            f"a global queue of width {queue_workers} requires a "
            "process-isolated daily runner; the production runner owns one "
            "global TraCI connection and cannot be pulled from several "
            "threads (start the search with --daily-workers "
            f"{queue_workers})"
        )
    if per_call != 1:
        raise GlobalQueueActivationError(
            f"a global queue of width {queue_workers} over a daily runner "
            f"that starts {per_call} SUMO processes per unit would run "
            f"{queue_workers * per_call} concurrent SUMO processes; the queue "
            "width is the ceiling, so the isolated runner must use "
            "--seed-workers 1"
        )
    if (
        declared_unit_workers is not None
        and queue_workers > int(declared_unit_workers)
    ):
        raise GlobalQueueActivationError(
            f"{QUEUE_WORKERS_ENV}={queue_workers} exceeds the "
            f"{declared_unit_workers} isolated daily workers the run "
            "declared; the queue replaces that dimension, so the width must "
            "stay inside the budget --max-active-sumo-slots already validated"
        )
    approved = approved_seed_workers()
    if queue_workers > approved:
        raise GlobalQueueActivationError(
            f"{QUEUE_WORKERS_ENV}={queue_workers} exceeds the {approved} "
            "concurrent SUMO workers approved by the recorded resource "
            f"benchmark ({SEED_WORKER_BENCHMARK_RECORD})"
        )


def _canonical_digest(value: Any, *, length: int = 64) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _end_label(interval: ClosureInterval) -> str:
    from datetime import datetime

    start = datetime.fromisoformat(interval.start_time)
    end = datetime.fromisoformat(interval.end_time)
    if end.date() > start.date() and end.strftime("%H:%M") == "00:00":
        return "24:00"
    return end.strftime("%H:%M")


def _daily_schedule_id(
    search_content_key: str,
    interval: ClosureInterval,
) -> str:
    identity = {
        "search_content_key": search_content_key,
        "first_work_date": interval.work_date,
        "day_count": 1,
        "daily_start": interval.start_time[11:16],
        "daily_end": _end_label(interval),
        "scheduled_work_minutes": interval.duration_minutes,
    }
    return "closure-" + _canonical_digest(identity, length=20)


@dataclass(frozen=True)
class DailyClosureUnit:
    """One reusable daily closure input belonging to a parent search."""

    unit_id: str
    schedule: ClosureSchedule
    parent_schedule_ids: tuple[str, ...]
    identity: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.unit_id != "daily-unit-" + _canonical_digest(self.identity, length=24):
            raise ValueError("daily unit ID does not match its identity")
        if self.schedule.day_count != 1 or len(self.schedule.intervals) != 1:
            raise ValueError("daily unit schedule must contain exactly one interval")
        if not self.parent_schedule_ids or any(
            not value for value in self.parent_schedule_ids
        ):
            raise ValueError("daily unit must belong to at least one parent schedule")

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "identity": dict(self.identity),
            "parent_schedule_ids": list(self.parent_schedule_ids),
            "schedule": self.schedule.to_dict(),
        }


@dataclass(frozen=True)
class StreamingDailyUnit:
    """A daily unit read from a ledger, carrying NO parent list.

    PR C. `DailyClosureUnit` stores every parent that references a unit, which
    is the reverse graph the streaming path exists to remove: its size grows
    with parents x days rather than with either. The forward relationship lives
    once in `parent_units.ndjson` instead.

    Execution never needed the reverse direction — `run_candidate` reads
    `unit_id`, `identity` and `schedule` only — so this type carries exactly
    those and keeps the same content-addressed ID guard, which is what makes a
    streamed unit hit the SAME v1 cache entry.
    """

    unit_id: str
    schedule: ClosureSchedule
    identity: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.unit_id != "daily-unit-" + _canonical_digest(self.identity,
                                                             length=24):
            raise ValueError("daily unit ID does not match its identity")
        if self.schedule.day_count != 1 or len(self.schedule.intervals) != 1:
            raise ValueError("daily unit schedule must contain exactly one interval")


def daily_unit_identity(
    spec: ClosureSearchSpec,
    interval: ClosureInterval,
) -> dict[str, Any]:
    if spec.interday_policy != "independent_daily_reset_v1":
        raise ValueError("daily units require independent_daily_reset_v1")
    return {
        "schema": "independent_daily_closure_unit_v1",
        "interday_policy": spec.interday_policy,
        "directed_edges": sorted(spec.directed_edges),
        "source": spec.source,
        "timezone": spec.timezone,
        "dst_policy": spec.dst_policy,
        "closure_type": spec.closure_type,
        "objective_profile": spec.objective_profile,
        "work_date": interval.work_date,
        "start_time": interval.start_time,
        "end_time": interval.end_time,
        "duration_minutes": interval.duration_minutes,
    }


def daily_unit_schedule(
    spec: ClosureSearchSpec,
    interval: ClosureInterval,
) -> ClosureSchedule:
    """The one-day `ClosureSchedule` a daily unit executes."""
    return ClosureSchedule(
        schedule_id=_daily_schedule_id(spec.content_key, interval),
        search_content_key=spec.content_key,
        first_work_date=interval.work_date,
        day_count=1,
        daily_start=interval.start_time[11:16],
        daily_end=_end_label(interval),
        required_work_minutes=interval.duration_minutes,
        scheduled_work_minutes=interval.duration_minutes,
        actual_closed_minutes=interval.duration_minutes,
        rounding_overshoot_minutes=0,
        intervals=(interval,),
    )


def daily_unit_records(
    spec: ClosureSearchSpec,
    parent: ClosureSchedule,
) -> tuple[tuple[str, dict[str, Any], Callable[[], ClosureSchedule]], ...]:
    """One parent's ordered ``(unit_id, identity, build_schedule)`` triples.

    Factored out of `decompose_schedules` for PR C so the streaming ledger
    path and the v1 materialising path compute unit identity through ONE
    implementation. Two implementations of a content-addressed ID is how a
    cache silently stops hitting: the streaming path would write units the
    v1 cache could never find, and nothing would report it — every unit would
    simply look uncached and be simulated again.

    The schedule is DEFERRED behind `build_schedule`, and that is not a style
    choice. A parent contributes one record per interval, but only a UNIQUE
    unit needs a schedule object: on the plan's 720-hour case that is 171,880
    records against 5,676 units, and building eagerly cost 85 µs each against
    11 µs for the identity — five times the whole measured decomposition.
    Every caller therefore calls it exactly where it deduplicates.

    Order is the parent's own interval order, which is what the parent-to-unit
    relationship ledger records and what aggregation replays.
    """
    records = []
    for interval in parent.intervals:
        identity = daily_unit_identity(spec, interval)
        unit_id = "daily-unit-" + _canonical_digest(identity, length=24)
        records.append(
            (unit_id, identity, partial(daily_unit_schedule, spec, interval)))
    return tuple(records)


def decompose_schedules(
    spec: ClosureSearchSpec,
    schedules: Sequence[ClosureSchedule],
) -> tuple[tuple[DailyClosureUnit, ...], dict[str, tuple[str, ...]]]:
    """Return deduplicated units and parent-to-unit identities."""
    if spec.interday_policy != "independent_daily_reset_v1":
        raise ValueError("search is not configured for independent daily reset")
    parents: dict[str, list[str]] = {}
    records: dict[str, dict[str, Any]] = {}
    for parent in schedules:
        if parent.schedule_id in parents:
            raise ValueError("independent shortlist repeats a parent schedule")
        if parent.search_content_key != spec.content_key:
            raise ValueError("parent schedule belongs to another search")
        unit_ids: list[str] = []
        for (unit_id, identity, build), interval in zip(
            daily_unit_records(spec, parent), parent.intervals
        ):
            unit_ids.append(unit_id)
            record = records.setdefault(
                unit_id,
                {"identity": identity, "interval": interval,
                 "build": build, "parents": []},
            )
            if record["identity"] != identity:
                raise ValueError("daily unit digest collision")
            record["parents"].append(parent.schedule_id)
        if len(set(unit_ids)) != len(unit_ids):
            raise ValueError("independent parent repeats a daily unit")
        parents[parent.schedule_id] = unit_ids

    units: list[DailyClosureUnit] = []
    for unit_id in sorted(records):
        record = records[unit_id]
        # Built ONCE per unique unit, exactly as before PR C.
        daily = record["build"]()
        units.append(DailyClosureUnit(
            unit_id=unit_id,
            schedule=daily,
            parent_schedule_ids=tuple(sorted(set(record["parents"]))),
            identity=record["identity"],
        ))
    return tuple(units), {
        parent_id: tuple(unit_ids)
        for parent_id, unit_ids in parents.items()
    }


def aggregate_daily_evidence(
    parent: ClosureSchedule,
    units: Sequence[DailyClosureUnit],
    evidence_by_unit: Mapping[str, CandidateEvidence],
    *,
    aggregate_provenance_key: str | None = None,
) -> CandidateEvidence:
    """Sum matched daily observations before robust decision-making."""
    if not units:
        raise ValueError("a parent schedule must contain daily units")
    failures: set[str] = set()
    timeouts: set[TimeoutIdentity] = set()
    canonical_digests: set[CanonicalObservationDigest] = set()
    indexed: dict[str, dict[tuple[str, int], PairedObservation]] = {}
    for unit in units:
        evidence = evidence_by_unit.get(unit.unit_id)
        if evidence is None:
            raise ValueError(f"daily evidence is missing for {unit.unit_id}")
        if evidence.candidate_id != unit.schedule.schedule_id:
            raise ValueError("daily evidence candidate identity differs")
        failures.update(
            f"{unit.identity['work_date']}:{reason}"
            for reason in evidence.hard_failures
        )
        # Unlike `hard_failures` (plain strings that still need a date
        # prefix to stay unique across units), a `TimeoutIdentity` already
        # names its own `work_date` at the point it was created — see
        # `monthly_sumo._timeout_identity`. Merge the records directly, and
        # verify each one actually names THIS unit's own day, schedule and
        # search rather than trusting it silently: a mismatch here would mean
        # a timeout is being attributed to the wrong daily unit or search.
        for identity in evidence.timeout_undecided:
            if identity.work_date != unit.identity["work_date"]:
                raise ValueError(
                    f"timeout identity work_date {identity.work_date!r} does "
                    f"not match daily unit {unit.unit_id}'s own work_date "
                    f"{unit.identity['work_date']!r}"
                )
            if identity.candidate_id != unit.schedule.schedule_id:
                raise ValueError(
                    "timeout identity candidate_id does not match daily unit "
                    f"{unit.unit_id}'s own schedule "
                    f"{unit.schedule.schedule_id!r}"
                )
            if identity.search_content_key != unit.schedule.search_content_key:
                raise ValueError(
                    "timeout identity search_content_key does not match "
                    f"daily unit {unit.unit_id}'s own search "
                    f"{unit.schedule.search_content_key!r}"
                )
            timeouts.add(identity)
        for identity in evidence.canonical_observation_digests:
            if identity.work_date != unit.identity["work_date"]:
                raise ValueError(
                    "canonical observation digest belongs to another daily unit"
                )
            if identity.candidate_id != unit.schedule.schedule_id:
                raise ValueError(
                    "canonical observation digest candidate_id does not match "
                    f"daily unit {unit.unit_id}'s own schedule "
                    f"{unit.schedule.schedule_id!r}"
                )
            canonical_digests.add(identity)
        observations: dict[tuple[str, int], PairedObservation] = {}
        for item in evidence.observations:
            key = (item.demand_variant, item.seed)
            if key in observations:
                raise ValueError("daily evidence repeats a variant/seed")
            observations[key] = item
        indexed[unit.unit_id] = observations

    # Disruption is a deterministic, process-free function of the schedule
    # (see monthly_sumo.py's _closure_disruption): every daily unit carries
    # it whether or not that unit's SUMO run succeeded. Compute the combined
    # record BEFORE the failure branch below, so a failed (including a timed
    # out, undecided) parent still publishes the same deterministic
    # disruption a viable one would — this is the fix for the defect found
    # in cost-order v5, where the exhaustive path silently dropped
    # disruption on any hard failure while the cost-ordered ledger did not,
    # making the two arms judge a timed-out candidate on different fields.
    disruption_by_unit: list[dict[str, Mapping[str, Any]]] = []
    disruption_presence: set[bool] = set()
    for unit in units:
        records = evidence_by_unit[unit.unit_id].disruption
        disruption_presence.add(bool(records))
        indexed_records: dict[str, Mapping[str, Any]] = {}
        for record in records:
            variant = str(record.get("demand_variant", ""))
            if variant not in DEMAND_VARIANTS or variant in indexed_records:
                raise ValueError(
                    "daily disruption evidence must contain one unique record "
                    "for each q10/q50/q90 variant"
                )
            indexed_records[variant] = record
        if records and set(indexed_records) != set(DEMAND_VARIANTS):
            raise ValueError(
                "daily disruption evidence lacks q10/q50/q90 coverage"
            )
        disruption_by_unit.append(indexed_records)
    if len(disruption_presence) > 1:
        raise ValueError(
            "daily disruption evidence is present for only part of a schedule"
        )

    combined_disruption: list[Mapping[str, Any]] = []
    if disruption_presence == {True}:
        for variant in DEMAND_VARIANTS:
            records = [item[variant] for item in disruption_by_unit]
            combined_disruption.append({
                "demand_variant": variant,
                "vehicles_affected": sum(
                    int(item["vehicles_affected"]) for item in records
                ),
                "vehicles_considered": sum(
                    int(item.get("vehicles_considered", 0)) for item in records
                ),
                "vehicles_no_detour": sum(
                    int(item["vehicles_no_detour"]) for item in records
                ),
                "added_vehicle_hours": round(sum(
                    float(item["added_vehicle_hours"]) for item in records
                ), 4),
                "added_metres_total": round(sum(
                    float(item["added_metres_total"]) for item in records
                ), 1),
                "basis": (
                    "sum of independent daily calibrated-route closure costs"
                ),
                "reduction": "sum across independent daily reset units",
            })

    # A hard failure is already a terminal, fail-closed result for the
    # parent's OBSERVATIONS: do not require the failed daily unit to
    # fabricate a complete replication matrix merely so the additive path
    # can continue.  Namespacing the reason by work date keeps one cached
    # daily failure useful (and diagnosable) across every parent schedule
    # that includes that unit.  Disruption is unaffected by this branch —
    # it was computed above from every unit unconditionally, so a failed
    # parent still reports the same deterministic numbers a viable one
    # would, instead of forcing a reader (or an equivalence gate comparing
    # this path against cost_ordered_execution.reconcile_disruption's
    # ledger fallback) to treat a failure as "no evidence at all".
    if failures:
        return CandidateEvidence(
            candidate_id=parent.schedule_id,
            observations=(),
            hard_failures=tuple(sorted(failures)),
            disruption=tuple(combined_disruption),
            timeout_undecided=tuple(sorted(timeouts)),
            canonical_observation_digests=tuple(sorted(canonical_digests)),
        )

    expected = set(next(iter(indexed.values())))
    for unit_id, observations in indexed.items():
        if set(observations) != expected:
            raise ValueError(
                f"daily evidence variant/seed coverage differs for {unit_id}")

    combined: list[PairedObservation] = []
    for variant, seed in sorted(
        expected,
        key=lambda item: (DEMAND_VARIANTS.index(item[0]), item[1]),
    ):
        daily = [indexed[unit.unit_id][(variant, seed)] for unit in units]
        baseline_ids = [item.matched_baseline_id for item in daily]
        provenance = [item.provenance_key for item in daily]
        combined.append(PairedObservation(
            candidate_id=parent.schedule_id,
            demand_variant=variant,
            seed=seed,
            baseline_time_loss_s=sum(item.baseline_time_loss_s for item in daily),
            candidate_time_loss_s=sum(item.candidate_time_loss_s for item in daily),
            matched_baseline_id=(
                "independent-daily-baseline-"
                + _canonical_digest(baseline_ids, length=24)
            ),
            provenance_key=(
                aggregate_provenance_key
                if aggregate_provenance_key is not None
                else "independent-daily-provenance-"
                + _canonical_digest(provenance, length=24)
            ),
        ))
    return CandidateEvidence(
        candidate_id=parent.schedule_id,
        observations=tuple(combined),
        hard_failures=tuple(sorted(failures)),
        disruption=tuple(combined_disruption),
        timeout_undecided=tuple(sorted(timeouts)),
        canonical_observation_digests=tuple(sorted(canonical_digests)),
    )


def _evidence_to_dict(evidence: CandidateEvidence) -> dict[str, Any]:
    import dataclasses

    return {
        "candidate_id": evidence.candidate_id,
        "observations": [dataclasses.asdict(item) for item in evidence.observations],
        "hard_failures": list(evidence.hard_failures),
        "disruption": [dict(item) for item in evidence.disruption],
        "timeout_undecided": [
            item.to_dict() for item in evidence.timeout_undecided
        ],
        "canonical_observation_digests": [
            item.to_dict() for item in evidence.canonical_observation_digests
        ],
    }


def _evidence_from_dict(raw: Mapping[str, Any]) -> CandidateEvidence:
    if not isinstance(raw, Mapping):
        raise ValueError("daily candidate evidence must be an object")
    expected = {
        "candidate_id", "observations", "hard_failures", "disruption",
        "timeout_undecided", "canonical_observation_digests",
    }
    if set(raw) != expected:
        raise ValueError("daily candidate evidence fields are invalid")
    if not isinstance(raw["candidate_id"], str):
        raise ValueError("daily candidate evidence candidate_id is invalid")
    for field in expected - {"candidate_id"}:
        if not isinstance(raw[field], list):
            raise ValueError(f"daily candidate evidence {field} must be a list")
    return CandidateEvidence(
        candidate_id=raw["candidate_id"],
        observations=tuple(
            PairedObservation(**dict(item))
            for item in raw["observations"]
        ),
        hard_failures=tuple(str(item) for item in raw["hard_failures"]),
        disruption=tuple(dict(item) for item in raw["disruption"]),
        timeout_undecided=tuple(
            TimeoutIdentity.from_dict(item)
            for item in raw["timeout_undecided"]
        ),
        canonical_observation_digests=tuple(
            CanonicalObservationDigest.from_dict(item)
            for item in raw["canonical_observation_digests"]
        ),
    )


def _evidence_from_worker_result(raw: Any) -> CandidateEvidence:
    """Validate the complete current worker-result envelope fail closed."""
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema") != "independent_daily_worker_result_v3"
        or set(raw) != {
            "schema", "evidence", "launch_telemetry", "launch_records"}
        or not isinstance(raw["launch_telemetry"], Mapping)
        or not isinstance(raw["launch_records"], list)
    ):
        raise ValueError("isolated daily worker result is malformed")
    return _evidence_from_dict(raw["evidence"])


class IsolatedDailySumoRunner:
    """Run daily SUMO units in separate interpreters and TraCI connections.

    A ``WarmPrefixController`` owns one global TraCI connection inside its
    interpreter. Threads therefore cannot safely share one production runner.
    Process isolation gives every daily unit its own connection while retaining
    the resource-benchmarked worker ceiling.
    """

    def __init__(
        self,
        delegate: Any,
        *,
        unit_workers: int,
        worker_invoker=None,
    ) -> None:
        if (
            isinstance(unit_workers, bool)
            or not isinstance(unit_workers, int)
            or unit_workers < 1
        ):
            raise ValueError("daily unit_workers must be a positive integer")
        self.delegate = delegate
        self.unit_workers = unit_workers
        self._worker_invoker = worker_invoker
        worker = Path(__file__).with_name("independent_daily_worker.py")
        self._worker_source_sha256 = hashlib.sha256(worker.read_bytes()).hexdigest()
        # Real SUMO launches happen inside the isolated subprocess, not this
        # process, so they only reach this counter if the worker's result
        # payload carries them back (see `_default_worker_invoker` and
        # `independent_daily_worker.execute_request`). A caller-supplied
        # `worker_invoker` fake that omits `launch_telemetry` simply leaves
        # this at zero rather than fabricating a count — tests exercising
        # this class do not need to launch real SUMO to stay correct.
        self._launch_telemetry: dict[str, dict[str, int]] = {
            "pilot": {"attempts": 0, "timeouts": 0, "other_outcomes": 0},
            "finalist": {"attempts": 0, "timeouts": 0, "other_outcomes": 0},
        }
        # Identity-bearing companion, merged from every worker result the
        # same way as the aggregate counters above.
        self._launch_records: list[dict[str, Any]] = []
        self._launch_telemetry_lock = threading.Lock()

    def _merge_launch_telemetry(self, raw: Any) -> None:
        if not isinstance(raw, Mapping):
            return
        with self._launch_telemetry_lock:
            for stage, counts in raw.items():
                if stage not in self._launch_telemetry or not isinstance(
                        counts, Mapping):
                    continue
                bucket = self._launch_telemetry[stage]
                for key in ("attempts", "timeouts", "other_outcomes"):
                    bucket[key] += int(counts.get(key, 0))

    def _merge_launch_records(self, raw: Any) -> None:
        if not isinstance(raw, list):
            return
        with self._launch_telemetry_lock:
            for record in raw:
                if isinstance(record, Mapping):
                    self._launch_records.append(dict(record))

    def _merge_launch_sidecar(self, sidecar_path: Path) -> None:
        """Recover launch records from the durable per-attempt sidecar file.

        This is the ONLY telemetry source the isolated worker path uses: the
        sidecar is written record-by-record, fsync'd, as each real SUMO
        attempt happens inside the subprocess — before the worker's final
        result JSON exists — so it is the one transport that survives an
        unrecognized exception or an outright kill of the worker process.
        Reading it after every subprocess exit, success or failure, closes
        the gap where such an attempt disappeared from exact-attempt
        accounting entirely.
        """
        if not sidecar_path.is_file():
            return
        records_by_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
        for line in sidecar_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if isinstance(record, Mapping):
                normalized = dict(record)
                identity = tuple(normalized.get(key) for key in (
                    "candidate_id", "work_date", "stage", "variant",
                    "seed", "attempt"))
                if any(value is None for value in identity):
                    raise ValueError("launch sidecar record identity is incomplete")
                records_by_identity[identity] = normalized
        records = list(records_by_identity.values())
        for record in records:
            if record.get("outcome") == "in_progress":
                record["outcome"] = "worker_terminated"
        if not records:
            return
        # Each subprocess-local runner numbers its first attempt as 1. A
        # parent retry starts a fresh subprocess and would otherwise publish
        # another attempt 1, collapsing two real launches to one identity.
        # Rebind each local sequence after the attempts already recovered by
        # this parent, while holding the same lock as snapshots/other merges.
        with self._launch_telemetry_lock:
            prior_max: dict[tuple[Any, ...], int] = {}
            for existing in self._launch_records:
                key = tuple(existing.get(field) for field in (
                    "candidate_id", "work_date", "stage", "variant", "seed"))
                prior_max[key] = max(
                    prior_max.get(key, 0), int(existing.get("attempt", 0)))
            local_bases: dict[tuple[Any, ...], int] = {}
            for record in records:
                stage = record.get("stage")
                if stage not in self._launch_telemetry:
                    raise ValueError(f"launch sidecar stage is invalid: {stage!r}")
                key = tuple(record.get(field) for field in (
                    "candidate_id", "work_date", "stage", "variant", "seed"))
                base = local_bases.setdefault(key, prior_max.get(key, 0))
                record["attempt"] = base + int(record["attempt"])
                bucket = self._launch_telemetry[stage]
                bucket["attempts"] += 1
                bucket["timeouts" if record.get("timed_out")
                       else "other_outcomes"] += 1
                self._launch_records.append(record)

    def launch_telemetry_snapshot(self) -> dict[str, dict[str, int]]:
        with self._launch_telemetry_lock:
            return {
                stage: dict(counts)
                for stage, counts in self._launch_telemetry.items()
            }

    def launch_records_snapshot(self) -> list[dict[str, Any]]:
        with self._launch_telemetry_lock:
            return [dict(record) for record in self._launch_records]

    def prepare(self, schedules: Sequence[ClosureSchedule]) -> None:
        self.delegate.prepare(schedules)

    def cleanup(self) -> None:
        cleanup = getattr(self.delegate, "cleanup", None)
        if callable(cleanup):
            cleanup()

    def candidate_provenance(
        self, schedule: ClosureSchedule
    ) -> Mapping[str, Any]:
        child = getattr(self.delegate, "candidate_provenance", None)
        raw = child(schedule) if callable(child) else self.delegate.provenance()
        ignored = {
            "search_content_key",
            "study_provenance_key",
            "demand_release_id",
            "demand_release",
        }
        return {
            "kind": ISOLATED_BACKEND_KIND,
            "worker_source_sha256": self._worker_source_sha256,
            "child": {
                key: value for key, value in dict(raw).items()
                if key not in ignored
            },
        }

    def provenance(self) -> Mapping[str, Any]:
        return {
            "schema_version": 1,
            "kind": ISOLATED_BACKEND_KIND,
            "unit_workers": self.unit_workers,
            "worker_source_sha256": self._worker_source_sha256,
            "child": dict(self.delegate.provenance()),
        }

    def _request(
        self,
        schedule: ClosureSchedule,
        *,
        target_repetitions: Mapping[str, int],
        existing: CandidateEvidence | None,
        stage: str,
    ) -> dict[str, Any]:
        contract = getattr(self.delegate, "candidate_execution_contract", None)
        if not callable(contract):
            raise ValueError(
                "isolated execution requires a candidate execution contract"
            )
        return {
            "schema": "independent_daily_worker_request_v1",
            "execution": dict(contract(schedule)),
            "schedule": schedule.to_dict(),
            "target_repetitions": {
                variant: int(target_repetitions[variant])
                for variant in DEMAND_VARIANTS
            },
            "existing": (
                None if existing is None else _evidence_to_dict(existing)
            ),
            "stage": stage,
        }

    def _default_worker_invoker(self, request: Mapping[str, Any]) -> CandidateEvidence:
        with tempfile.TemporaryDirectory(prefix="independent-daily-worker-") as raw:
            root = Path(raw)
            request_path = root / "request.json"
            result_path = root / "result.json"
            sidecar_path = root / "telemetry.ndjson"
            _atomic_json(request_path, request)
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "traffic_sim.simulation.independent_daily_worker",
                        "--request",
                        str(request_path),
                        "--result",
                        str(result_path),
                        "--telemetry-sidecar",
                        str(sidecar_path),
                    ],
                    cwd=Path(__file__).resolve().parents[2],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
            finally:
                # The sidecar is the durable transport: it is written
                # record-by-record inside the subprocess as each real SUMO
                # attempt happens, so it holds every attempt that occurred
                # even when the subprocess never reaches its final result
                # write (an unrecognized exception, a signal, an OOM kill).
                # Merging it in a `finally` — ahead of the returncode check
                # below — is what stops such attempts from disappearing
                # from exact-attempt accounting entirely.
                self._merge_launch_sidecar(sidecar_path)
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[-2000:]
                raise RuntimeError(
                    "isolated daily SUMO worker exited "
                    f"{completed.returncode}: {detail}"
                )
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            return _evidence_from_worker_result(payload)

    def run_candidate(
        self,
        schedule: ClosureSchedule,
        *,
        target_repetitions: Mapping[str, int],
        existing: CandidateEvidence | None,
        stage: str,
    ) -> CandidateEvidence:
        request = self._request(
            schedule,
            target_repetitions=target_repetitions,
            existing=existing,
            stage=stage,
        )
        invoke = self._worker_invoker or self._default_worker_invoker
        return invoke(request)

    def run_candidate_batch(
        self,
        requests: Sequence[
            tuple[
                ClosureSchedule,
                Mapping[str, int],
                CandidateEvidence | None,
                str,
            ]
        ],
    ) -> dict[str, CandidateEvidence]:
        requests = tuple(requests)
        if not requests:
            return {}
        if len({item[0].schedule_id for item in requests}) != len(requests):
            raise ValueError("isolated daily batch repeats a schedule")

        def execute(item):
            schedule, targets, existing, stage = item
            return schedule.schedule_id, self.run_candidate(
                schedule,
                target_repetitions=targets,
                existing=existing,
                stage=stage,
            )

        with ThreadPoolExecutor(
            max_workers=min(self.unit_workers, len(requests))
        ) as executor:
            results = tuple(executor.map(execute, requests))
        return dict(results)


class QueueCancelled(RuntimeError):
    """Raised to a waiter when the global unit queue was shut down."""


class GlobalDailyUnitQueue:
    """Saturate the unit-worker width from ONE global pool of missing units.

    The parent-local batch is the measured bottleneck.  A five-day parent
    supplies at most five daily units, and once a campaign is warm nearly all
    of them are cache hits: production measured 3 229 hits against 851 misses
    over 816 parents, i.e. ~1.04 genuinely new units per parent.  Handing that
    to an eight-wide pool leaves seven slots idle, which is exactly what the
    live process table showed (one worker, one SUMO, 20/20 samples).

    This queue inverts the relationship.  Missing units are enumerated ONCE
    across the whole shortlist and served by a fixed set of ``workers`` puller
    threads, so the width is filled from the global remainder instead of from
    whichever parent happens to be current.  A parent that needs a unit marks
    it urgent and waits only for its own units; everything else is lookahead
    that lands in the shared content-addressed cache for later parents.

    What deliberately does NOT change: the unit is executed by the same
    isolated one-shot worker with the same schedule, the same target
    repetitions and the same canonical seeds, and it is published under the
    same content key.  Completion order is therefore invisible in the result -
    a parent reads its units back from the cache in its own canonical order.
    """

    def __init__(
        self,
        unit_ids: Sequence[str],
        *,
        workers: int,
        execute: Callable[[str], None],
    ) -> None:
        if (
            isinstance(workers, bool)
            or not isinstance(workers, int)
            or workers < 1
        ):
            raise ValueError("global daily queue workers must be a positive integer")
        ordered = list(dict.fromkeys(str(value) for value in unit_ids))
        self._execute = execute
        self.workers = workers
        self._lock = threading.Lock()
        self._ready = threading.Condition(self._lock)
        # Canonical remainder order.  Urgent work jumps ahead of it, but two
        # units that nobody is waiting for are always taken in this order, so
        # a resumed run schedules the same lookahead as a fresh one.
        self._pending: list[str] = ordered
        self._pending_set: set[str] = set(ordered)
        self._urgent: list[str] = []
        self._inflight: set[str] = set()
        self._done: set[str] = set()
        self._errors: dict[str, BaseException] = {}
        self._stopped = False
        self._active = 0
        self._started_at = time.monotonic()
        self._stats = {
            "queue_total": len(ordered),
            "queue_completed": 0,
            "queue_failed": 0,
            "queue_max_active_workers": 0,
        }
        # Exactly ``workers`` threads exist, so at most ``workers`` isolated
        # worker subprocesses - and therefore at most ``workers`` SUMO
        # processes - can be alive at once.  This is the concurrency ceiling
        # itself, not a limit checked after the fact.  It is also the
        # backpressure: pullers take one unit at a time, so the number of
        # outstanding work items never exceeds the width.
        #
        # The pullers are DAEMON threads deliberately.  A puller parked in
        # `self._ready.wait()` is not waiting on any queue the interpreter
        # knows how to drain, so as a non-daemon thread it made shutdown
        # unreachable: `threading._shutdown()` joins non-daemon threads
        # BEFORE atexit handlers run, so neither `concurrent.futures`' exit
        # hook nor one of our own could ever wake it.  Measured directly on
        # the pre-fix code: an owner that skipped `cleanup()` hung the
        # interpreter forever.  Daemon threads plus the shutdown hook below
        # keep the orderly path orderly - `stop()` still joins, so a unit in
        # flight is still reaped - while making the disorderly path exit.
        self._pumps = [
            threading.Thread(
                target=self._pump,
                name=f"daily-unit-{index}",
                daemon=True,
            )
            for index in range(workers)
        ]
        for pump in self._pumps:
            pump.start()
        # Runs during `threading._shutdown()`, i.e. early enough to matter.
        # It asks the pullers to retire and waits a BOUNDED time for the unit
        # in flight, so a normal exit still reaps its SUMO child instead of
        # abandoning it, and a wedged one still exits.
        self._shutdown_hook = self._shutdown_stop
        register = getattr(threading, "_register_atexit", None)
        if callable(register):
            register(self._shutdown_hook)

    # -- scheduling ------------------------------------------------------
    def _take_locked(self) -> str | None:
        while self._urgent:
            unit_id = self._urgent.pop(0)
            if unit_id in self._pending_set:
                self._pending_set.discard(unit_id)
                self._pending.remove(unit_id)
                return unit_id
        while self._pending:
            unit_id = self._pending.pop(0)
            if unit_id in self._pending_set:
                self._pending_set.discard(unit_id)
                return unit_id
        return None

    def _has_work_locked(self) -> bool:
        return bool(self._pending_set)

    def _pump(self) -> None:
        while True:
            with self._ready:
                while not self._stopped and not self._has_work_locked():
                    self._ready.wait()
                if self._stopped:
                    return
                unit_id = self._take_locked()
                if unit_id is None:
                    continue
                self._inflight.add(unit_id)
                self._active += 1
                if self._active > self._stats["queue_max_active_workers"]:
                    self._stats["queue_max_active_workers"] = self._active
            error: BaseException | None = None
            try:
                self._execute(unit_id)
            except BaseException as exc:  # noqa: BLE001 - delivered to waiter
                error = exc
            with self._ready:
                self._active -= 1
                self._inflight.discard(unit_id)
                self._done.add(unit_id)
                if error is not None:
                    self._errors[unit_id] = error
                    self._stats["queue_failed"] += 1
                else:
                    self._stats["queue_completed"] += 1
                self._ready.notify_all()

    # -- public API ------------------------------------------------------
    def require(self, unit_ids: Sequence[str]) -> None:
        """Block until every requested unit has been produced.

        Requested units are promoted ahead of the lookahead remainder so a
        waiting parent is never stuck behind work nobody needs yet.
        """
        wanted = [str(value) for value in unit_ids]
        if not wanted:
            return
        with self._ready:
            if self._stopped:
                raise QueueCancelled("global daily unit queue is stopped")
            for unit_id in reversed(wanted):
                if unit_id in self._pending_set and unit_id not in self._urgent:
                    self._urgent.insert(0, unit_id)
            self._ready.notify_all()
            for unit_id in wanted:
                while unit_id not in self._done:
                    if self._stopped:
                        raise QueueCancelled(
                            "global daily unit queue was stopped while waiting "
                            f"for {unit_id}"
                        )
                    if (
                        unit_id not in self._pending_set
                        and unit_id not in self._inflight
                    ):
                        # Not queued, not running and not finished: this unit
                        # is not part of the queue's remainder.  Fail loudly
                        # rather than wait forever.
                        raise KeyError(
                            f"global daily unit queue does not hold {unit_id}"
                        )
                    self._ready.wait(timeout=1.0)
                error = self._errors.pop(unit_id, None)
                if error is not None:
                    # Popping keeps the queue reusable: a resumed or retried
                    # run re-executes this unit instead of replaying a stale
                    # failure.  Units already produced stay produced.
                    self._done.discard(unit_id)
                    self._pending_set.add(unit_id)
                    self._pending.insert(0, unit_id)
                    raise error

    def add(self, unit_ids: Sequence[str]) -> None:
        """Append newly-missing units to the remainder (idempotent)."""
        with self._ready:
            if self._stopped:
                raise QueueCancelled("global daily unit queue is stopped")
            added = 0
            for unit_id in (str(value) for value in unit_ids):
                if (
                    unit_id in self._pending_set
                    or unit_id in self._inflight
                    or unit_id in self._done
                ):
                    continue
                self._pending.append(unit_id)
                self._pending_set.add(unit_id)
                added += 1
            if added:
                self._stats["queue_total"] += added
                self._ready.notify_all()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            completed = self._stats["queue_completed"]
            running = self._active
            pending = len(self._pending_set)
            elapsed = time.monotonic() - self._started_at
            rate = (completed / elapsed) if completed and elapsed > 0 else 0.0
            snapshot = dict(self._stats)
        snapshot["queue_running"] = running
        snapshot["queue_pending"] = pending
        snapshot["queue_workers"] = self.workers
        snapshot["queue_units_per_hour"] = round(rate * 3600.0, 3)
        snapshot["queue_eta_seconds"] = (
            round((pending + running) / rate, 1) if rate > 0 else None
        )
        return snapshot

    def _request_stop(self) -> bool:
        """Tell every puller to retire.  True when this call did it."""
        with self._ready:
            if self._stopped:
                self._ready.notify_all()
                return False
            self._stopped = True
            self._pending.clear()
            self._pending_set.clear()
            self._urgent.clear()
            self._ready.notify_all()
        return True

    def stop(self, *, wait: bool = True) -> None:
        """Cancel queued work and reap the pullers.

        Units already published stay published; a unit interrupted mid-flight
        publishes nothing, because publication is the last step inside its
        single-flight lock.  ``flock`` is released by the kernel even if a
        worker dies, so a cancelled run never strands a content key.
        """
        started_stop = self._request_stop()
        self._shutdown_hook = None
        if not started_stop:
            return
        # Threads block in ``subprocess.run`` until their unit finishes, so
        # shutdown waits for real reaping instead of leaving orphan SUMO
        # children behind.
        if wait:
            self._join_pumps(None)

    def _join_pumps(self, timeout: float | None) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        for pump in self._pumps:
            if deadline is None:
                pump.join()
            else:
                pump.join(max(0.0, deadline - time.monotonic()))

    def _shutdown_stop(self) -> None:
        """Interpreter shutdown: retire the pullers, reap what is in flight.

        Bounded on purpose.  Waiting forever here would reintroduce the hang
        this hook exists to remove, and a unit that is still running after the
        grace period publishes nothing anyway - publication is the last step
        inside its single-flight lock.
        """
        if self._shutdown_hook is None:
            return
        self._shutdown_hook = None
        self._request_stop()
        self._join_pumps(QUEUE_SHUTDOWN_GRACE_S)


class IndependentDailyRunner:
    """CandidateRunner adapter with reusable persistent daily evidence."""

    # Parent pilot evidence is a deterministic sum of immutable daily cache
    # entries and is fully represented by the pilot-selection statistics.
    # The generic engine may therefore avoid tens of thousands of redundant
    # per-parent JSON files; finalist evidence remains published normally.
    compact_pilot_artifacts = True

    def __init__(
        self,
        spec: ClosureSearchSpec,
        *,
        daily_runner: Any,
        cache_root: Path,
        queue_workers: int | None = None,
    ) -> None:
        if spec.interday_policy != "independent_daily_reset_v1":
            raise ValueError("independent daily runner requires its policy")
        if queue_workers is None:
            # Not passed: read the opt-in seam.  This is how production turns
            # the queue on, because the only production construction site is
            # `run_monthly_closure_search.py`, which is hashed into the daily
            # unit cache identity and therefore must not change.
            queue_workers = resolve_global_queue_workers()
        if (
            isinstance(queue_workers, bool)
            or not isinstance(queue_workers, int)
            or queue_workers < 1
        ):
            raise ValueError("queue_workers must be a positive integer")
        # The width IS the SUMO ceiling, so it is checked against the real
        # runner before any unit exists - not asserted afterwards from a
        # sample of the process table.
        validate_queue_concurrency_budget(daily_runner, queue_workers)
        self.spec = ClosureSearchSpec.from_dict(spec.to_dict())
        self.daily_runner = daily_runner
        self.cache_root = Path(cache_root)
        # ``1`` keeps the historical parent-local batch path untouched, so the
        # global queue is opt-in and directly comparable against it.
        self.queue_workers = queue_workers
        self._queue: GlobalDailyUnitQueue | None = None
        self._queue_targets: tuple[tuple[str, int], ...] | None = None
        self._queue_stage: str | None = None
        # Survives shutdown so an end-of-run progress write still reports what
        # the queue actually did instead of silently dropping the fields.
        self._queue_final_stats: dict[str, Any] = {}
        # The queue mutates diagnostic counters and the in-memory evidence map
        # from several puller threads at once.
        self._state_lock = threading.RLock()
        # Serialises queue construction/retirement only.  Never taken by a
        # puller thread, which is what makes the ordering above safe.
        self._queue_build_lock = threading.Lock()
        self._units: dict[str, DailyClosureUnit] = {}
        self._parents: dict[str, tuple[str, ...]] = {}
        self._backend_digest: str | None = None
        self._unit_backend_digests: dict[str, str] = {}
        self._memory_evidence: dict[str, CandidateEvidence] = {}
        # Diagnostic only: never enters cache identity or CandidateEvidence.
        # These counters make S0 able to distinguish filesystem verification,
        # worker execution and publication without changing evidence bytes.
        self._timing = {
            "cache_hits": 0,
            "cache_misses": 0,
            "cache_corrupt": 0,
            "cache_verify_seconds": 0.0,
            "cache_write_seconds": 0.0,
            "worker_seconds": 0.0,
            "units_simulated": 0,
            "queue_singleflight_skips": 0,
            # A successful `_save_cached` write — distinct from
            # `cache_write_seconds`, which only ever measured how long a
            # write took, never how many actually happened.
            "cache_publications": 0,
        }
        # Identity-bearing companion to the aggregate counters above: one
        # record per real daily-result cache lookup/publication, naming the
        # daily unit and the event kind. The aggregate counts alone cannot
        # support a true cache-event POPULATION comparison (which unit was
        # hit, missed or published, not just how many) between the
        # cost-ordered and ordered-exhaustive arms.
        self._cache_event_records: list[dict[str, Any]] = []
        self._prepared_parent_ids: tuple[str, ...] | None = None

    def _bump(self, key: str, value: Any = 1) -> None:
        with self._state_lock:
            self._timing[key] += value

    def _record_cache_event(self, unit_id: str, event: str) -> None:
        with self._state_lock:
            self._cache_event_records.append({
                "unit_id": str(unit_id),
                "event": event,
            })

    def timing_snapshot(self) -> dict[str, Any]:
        """Return result-neutral S0 telemetry accumulated by this runner.

        With the global queue active ``worker_seconds`` is the SUM of unit
        execution time across puller threads, so ``worker_seconds`` divided by
        active wall time is the achieved width - the number the parent-local
        path could never lift above ~1.

        Read ``cache_hits``/``cache_misses`` as PARENT-FACING lookups, which is
        what they have always been.  Under the queue a parent legitimately hits
        the cache almost every time, because the queue produced its units
        moments earlier, so a warm-looking ``cache_misses: 0`` next to a
        nonzero ``units_simulated`` is correct rather than contradictory:
        ``units_simulated`` is the count of units this process actually ran.
        The queue's own lookups (remainder enumeration and the post-lock
        recheck) are deliberately uncounted - they are not a parent asking for
        evidence, and counting them would redefine a published diagnostic.
        """
        with self._state_lock:
            snapshot = {
                key: (round(value, 6) if isinstance(value, float) else value)
                for key, value in self._timing.items()
            }
            queue = self._queue
            snapshot.update(self._queue_final_stats)
            snapshot["cache_event_records"] = [
                dict(record) for record in self._cache_event_records
            ]
        if queue is not None:
            snapshot.update(queue.stats())
        # Exact-launch telemetry (real SUMO (variant, seed) attempts, split
        # by pilot/finalist stage and by timeout vs. any other outcome) is
        # owned by the SUMO backend, not this class — it is pulled fresh on
        # every snapshot rather than mirrored into ``self._timing`` so it can
        # never drift from the backend's own counters. A backend without this
        # hook (any fake/legacy runner) simply omits the key, exactly like
        # the other optional diagnostics this method already tolerates.
        launch_telemetry = getattr(
            self.daily_runner, "launch_telemetry_snapshot", None)
        if callable(launch_telemetry):
            try:
                snapshot["exact_launch_telemetry"] = launch_telemetry()
            except Exception:  # diagnostic hook: fail open, never break a run
                pass
        launch_records = getattr(
            self.daily_runner, "launch_records_snapshot", None)
        if callable(launch_records):
            try:
                snapshot["exact_launch_records"] = launch_records()
            except Exception:  # diagnostic hook: fail open, never break a run
                pass
        return snapshot

    def _record_corrupt_cache_miss(
        self, unit_id: str, *, count: bool = True
    ) -> None:
        """One corrupt lookup is both a miss and a diagnostic corruption."""
        with self._state_lock:
            self._timing["cache_corrupt"] += 1
            if count:
                self._timing["cache_misses"] += 1
        self._record_cache_event(unit_id, "corrupt")
        if count:
            self._record_cache_event(unit_id, "miss")

    def cleanup(self) -> None:
        # Same lock order as `_ensure_queue`, so cleanup cannot race a
        # retarget into leaving a live queue behind after shutdown, and
        # `stop()` is still called with no state lock held.
        with self._queue_build_lock:
            with self._state_lock:
                queue, self._queue = self._queue, None
                self._queue_targets = None
                self._queue_stage = None
                if queue is not None:
                    self._queue_final_stats = queue.stats()
            try:
                if queue is not None:
                    queue.stop()
                    with self._state_lock:
                        self._queue_final_stats = queue.stats()
            finally:
                cleanup = getattr(self.daily_runner, "cleanup", None)
                if callable(cleanup):
                    cleanup()

    @staticmethod
    def _stable_backend_identity(provenance: Mapping[str, Any]) -> dict[str, Any]:
        """Discard orchestration labels that do not change a SUMO result.

        Search IDs, study IDs and release-manifest composition change when a
        user widens a month range or changes the requested total work.  They
        must not invalidate an otherwise identical road/date/time simulation.
        Exact demand bytes, source bytes, runtime, network and metric semantics
        remain bound by the child backend record.
        """
        ignored = {
            "search_content_key",
            "study_provenance_key",
            "demand_release_id",
            "demand_release",
        }
        return {
            key: value for key, value in provenance.items()
            if key not in ignored
        }

    def _candidate_backend_identity(
        self, unit: DailyClosureUnit
    ) -> dict[str, Any]:
        candidate = getattr(self.daily_runner, "candidate_provenance", None)
        raw = (
            candidate(unit.schedule)
            if callable(candidate)
            else self.daily_runner.provenance()
        )
        if not isinstance(raw, Mapping) or not raw:
            raise ValueError("daily backend provenance must be a non-empty object")
        # Canonical JSON conversion also rejects non-finite or unserializable
        # provenance before any cache path can be derived from it.
        checked = json.loads(json.dumps(
            raw, sort_keys=True, separators=(",", ":"), allow_nan=False
        ))
        return self._stable_backend_identity(checked)

    def prepare(self, schedules: Sequence[ClosureSchedule]) -> None:
        schedules = tuple(schedules)
        parent_ids = tuple(item.schedule_id for item in schedules)
        if self._prepared_parent_ids is not None:
            if parent_ids != self._prepared_parent_ids:
                raise ValueError("independent runner was prepared for another shortlist")
            return
        units, parents = decompose_schedules(self.spec, schedules)
        self.daily_runner.prepare([item.schedule for item in units])
        unit_backend_digests = {
            item.unit_id: _canonical_digest(
                self._candidate_backend_identity(item)
            )
            for item in units
        }
        self._backend_digest = _canonical_digest({
            "kind": BACKEND_KIND,
            "unit_backends": unit_backend_digests,
        })
        self._unit_backend_digests = unit_backend_digests
        self._units = {item.unit_id: item for item in units}
        self._parents = parents
        self._prepared_parent_ids = parent_ids

    def prepare_from_ledgers(
        self,
        directory: Path,
        parent_schedule_ids: Sequence[str],
    ) -> None:
        """Prepare from streaming ledgers instead of a parent schedule tuple.

        PR C's execution seam. `prepare` takes every parent as an object and
        rebuilds the unit set — and the reverse unit->parents graph — in
        memory. This reads the already-published ledgers instead: unique units
        come from `units.ndjson`, and the forward parent->units relationship
        from `parent_units.ndjson`, so nothing reconstructs the graph.

        Only the shortlisted parents' relationships are retained, and only the
        units those parents actually reference. That is the minimum index the
        pilot and finalist phases need to run a candidate.

        Unit IDs are the SAME content-addressed IDs `decompose_schedules`
        produces (both go through `daily_unit_records`), so a search that
        switches to the streaming path still hits every v1 cache entry.
        """
        from traffic_sim.simulation import closure_ledgers  # noqa: PLC0415

        parent_ids = tuple(parent_schedule_ids)
        if self._prepared_parent_ids is not None:
            if parent_ids != self._prepared_parent_ids:
                raise ValueError(
                    "independent runner was prepared for another shortlist")
            return
        wanted = set(parent_ids)
        if len(wanted) != len(parent_ids):
            raise ValueError("independent shortlist repeats a parent schedule")

        parents: dict[str, tuple[str, ...]] = {}
        needed: set[str] = set()
        for row in closure_ledgers.iter_parent_unit_rows(directory):
            parent_id = str(row["parent_schedule_id"])
            if parent_id not in wanted:
                continue
            unit_ids = tuple(str(value) for value in row["unit_ids"])
            if len(set(unit_ids)) != len(unit_ids):
                raise ValueError("independent parent repeats a daily unit")
            parents[parent_id] = unit_ids
            needed.update(unit_ids)
        missing = sorted(wanted - set(parents))
        if missing:
            raise ValueError(
                f"parent-unit ledger does not cover {len(missing)} shortlisted "
                f"schedule(s), first {missing[0]}")

        units: dict[str, StreamingDailyUnit] = {}
        for row in closure_ledgers.iter_unit_rows(directory):
            unit_id = str(row["unit_id"])
            if unit_id not in needed:
                continue
            units[unit_id] = StreamingDailyUnit(
                unit_id=unit_id,
                schedule=ClosureSchedule.from_dict(row["schedule"]),
                identity=dict(row["identity"]),
            )
        absent = sorted(needed - set(units))
        if absent:
            raise ValueError(
                f"unit ledger is missing {len(absent)} referenced unit(s), "
                f"first {absent[0]}")

        ordered = [units[unit_id] for unit_id in sorted(units)]
        self.daily_runner.prepare([item.schedule for item in ordered])
        unit_backend_digests = {
            item.unit_id: _canonical_digest(
                self._candidate_backend_identity(item)
            )
            for item in ordered
        }
        self._backend_digest = _canonical_digest({
            "kind": BACKEND_KIND,
            "unit_backends": unit_backend_digests,
        })
        self._unit_backend_digests = unit_backend_digests
        self._units = {item.unit_id: item for item in ordered}
        self._parents = parents
        self._prepared_parent_ids = parent_ids

    def daily_units_for(
        self, parent: ClosureSchedule
    ) -> tuple[tuple[str, ClosureSchedule], ...]:
        """This parent's ordered ``(unit_id, daily schedule)`` pairs.

        The seam cost-first execution needs: a parent's deterministic price is
        the sum of its daily units' prices, and the units are the reusable
        thing — the same day appears in many overlapping parents, so pricing is
        cached per unit and a later parent covering those days is usually free.
        """
        if self._prepared_parent_ids is None:
            raise RuntimeError("independent daily runner is not prepared")
        unit_ids = self._parents.get(parent.schedule_id)
        if unit_ids is None:
            raise ValueError("candidate was not part of the prepared shortlist")
        return tuple(
            (unit_id, self._units[unit_id].schedule) for unit_id in unit_ids)

    def provenance(self) -> Mapping[str, Any]:
        if self._backend_digest is None:
            raise RuntimeError("independent daily runner must be prepared")
        return {
            "schema_version": 1,
            "kind": BACKEND_KIND,
            "simulation_mode": "meso",
            "interday_policy": self.spec.interday_policy,
            "work_allocation_policy": self.spec.work_allocation_policy,
            "daily_unit_count": len(self._units),
            "daily_backend_digest": self._backend_digest,
            "unit_backend_digests": dict(sorted(
                self._unit_backend_digests.items()
            )),
        }

    def _cache_path(self, unit: DailyClosureUnit) -> Path:
        if self._backend_digest is None:
            raise RuntimeError("independent daily runner must be prepared")
        backend_digest = self._unit_backend_digests.get(unit.unit_id)
        if backend_digest is None:
            raise RuntimeError("daily unit has no prepared backend identity")
        key = _canonical_digest({
            "unit": unit.identity,
            "unit_backend_digest": backend_digest,
        })
        return self.cache_root / key[:2] / f"{key}.json"

    def _load_cached(
        self, unit: DailyClosureUnit, *, count: bool = True
    ) -> CandidateEvidence | None:
        """Read verified evidence for ``unit``.

        ``count=False`` performs the identical verification without touching
        the hit/miss counters.  The global queue needs two extra reads per
        unit - one to enumerate the remainder, one to re-check after taking
        the single-flight lock - and neither is a parent asking for evidence,
        so counting them would silently redefine the published diagnostics.
        """
        started = time.perf_counter()
        remembered = self._memory_evidence.get(unit.unit_id)
        if remembered is not None:
            if count:
                self._bump("cache_hits")
                self._record_cache_event(unit.unit_id, "hit")
            self._bump("cache_verify_seconds", time.perf_counter() - started)
            return remembered
        path = self._cache_path(unit)
        if not path.is_file():
            if count:
                self._bump("cache_misses")
                self._record_cache_event(unit.unit_id, "miss")
            self._bump("cache_verify_seconds", time.perf_counter() - started)
            return None
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
            )
            if not isinstance(payload, Mapping):
                self._record_corrupt_cache_miss(unit.unit_id, count=count)
                return None
            body = {key: value for key, value in payload.items()
                    if key != "content_key"}
            if (
                set(payload) != {
                    "schema", "unit", "unit_backend_digest", "evidence",
                    "content_key"}
                or payload.get("schema") != CACHE_SCHEMA
                or payload.get("unit") != {
                    "unit_id": unit.unit_id,
                    "identity": dict(unit.identity),
                }
                or payload.get("unit_backend_digest")
                != self._unit_backend_digests.get(unit.unit_id)
                or payload.get("content_key") != _canonical_digest(body)
            ):
                self._record_corrupt_cache_miss(unit.unit_id, count=count)
                return None
            stored = _evidence_from_dict(payload["evidence"])
            if (
                stored.candidate_id != unit.unit_id
                or any(
                    item.candidate_id != unit.unit_id
                    for item in stored.observations
                )
                or any(
                    item.candidate_id != unit.schedule.schedule_id
                    or item.search_content_key
                    != unit.schedule.search_content_key
                    for item in stored.timeout_undecided
                )
                or any(
                    item.candidate_id != unit.schedule.schedule_id
                    for item in stored.canonical_observation_digests
                )
            ):
                self._record_corrupt_cache_miss(unit.unit_id, count=count)
                return None
            rebound = CandidateEvidence(
                candidate_id=unit.schedule.schedule_id,
                observations=tuple(PairedObservation(
                    candidate_id=unit.schedule.schedule_id,
                    demand_variant=item.demand_variant,
                    seed=item.seed,
                    baseline_time_loss_s=item.baseline_time_loss_s,
                    candidate_time_loss_s=item.candidate_time_loss_s,
                    matched_baseline_id=item.matched_baseline_id,
                    provenance_key=item.provenance_key,
                ) for item in stored.observations),
                hard_failures=stored.hard_failures,
                disruption=stored.disruption,
                timeout_undecided=stored.timeout_undecided,
                canonical_observation_digests=(
                    stored.canonical_observation_digests),
            )
            with self._state_lock:
                self._memory_evidence[unit.unit_id] = rebound
                if count:
                    self._timing["cache_hits"] += 1
            if count:
                self._record_cache_event(unit.unit_id, "hit")
            return rebound
        except (
            OSError, UnicodeError, AttributeError, ValueError, TypeError,
            KeyError, json.JSONDecodeError,
        ):
            self._record_corrupt_cache_miss(unit.unit_id, count=count)
            return None
        finally:
            self._bump("cache_verify_seconds", time.perf_counter() - started)

    def _save_cached(
        self,
        unit: DailyClosureUnit,
        evidence: CandidateEvidence,
    ) -> None:
        if (
            evidence.candidate_id != unit.schedule.schedule_id
            or any(
                item.candidate_id != unit.schedule.schedule_id
                for item in evidence.observations
            )
            or any(
                item.candidate_id != unit.schedule.schedule_id
                or item.search_content_key != unit.schedule.search_content_key
                for item in evidence.timeout_undecided
            )
            or any(
                item.candidate_id != unit.schedule.schedule_id
                for item in evidence.canonical_observation_digests
            )
        ):
            raise ValueError("daily backend returned evidence for another unit")
        normalized = CandidateEvidence(
            candidate_id=unit.unit_id,
            observations=tuple(PairedObservation(
                candidate_id=unit.unit_id,
                demand_variant=item.demand_variant,
                seed=item.seed,
                baseline_time_loss_s=item.baseline_time_loss_s,
                candidate_time_loss_s=item.candidate_time_loss_s,
                matched_baseline_id=item.matched_baseline_id,
                provenance_key=item.provenance_key,
            ) for item in evidence.observations),
            hard_failures=evidence.hard_failures,
            disruption=evidence.disruption,
            timeout_undecided=evidence.timeout_undecided,
            canonical_observation_digests=(
                evidence.canonical_observation_digests),
        )
        payload = {
            "schema": CACHE_SCHEMA,
            # Parent schedule membership is intentionally excluded. The same
            # date/road/window unit must remain reusable across overlapping
            # searches with different shortlist composition.
            "unit": {
                "unit_id": unit.unit_id,
                "identity": dict(unit.identity),
            },
            "unit_backend_digest": self._unit_backend_digests[unit.unit_id],
            "evidence": _evidence_to_dict(normalized),
        }
        payload["content_key"] = _canonical_digest(payload)
        started = time.perf_counter()
        _atomic_json(self._cache_path(unit), payload)
        self._bump("cache_write_seconds", time.perf_counter() - started)
        self._bump("cache_publications")
        self._record_cache_event(unit.unit_id, "publication")
        with self._state_lock:
            self._memory_evidence[unit.unit_id] = evidence

    def _is_covered(
        self,
        evidence: CandidateEvidence | None,
        targets: Mapping[str, int],
    ) -> bool:
        """A unit is done when it hard-failed or already meets every target."""
        if evidence is None:
            return False
        if evidence.hard_failures:
            # A valid hard failure is a real, cacheable outcome, not an error.
            return True
        coverage = self._coverage(evidence)
        return all(
            coverage[variant] >= targets[variant] for variant in DEMAND_VARIANTS
        )

    def _cache_key(self, unit: DailyClosureUnit) -> str:
        return self._cache_path(unit).stem

    def _produce_unit(
        self,
        unit_id: str,
        targets: Mapping[str, int],
        stage: str,
    ) -> None:
        """Execute one daily unit exactly once and publish it atomically.

        Ordering inside the single-flight lock is what makes this safe to run
        from many threads and many processes at the same time:

        1. take the cross-process ``flock`` for this unit's content key;
        2. RE-READ the cache, because another producer may have published
           while this caller was waiting for the lock;
        3. only then execute, and publish atomically as the last step.

        Step 2 is the reason a race costs a filesystem read rather than a
        duplicate SUMO run, and step 3 is why an interrupted unit leaves no
        entry: ``_atomic_json`` writes a temporary file and ``os.replace``s it.
        """
        unit = self._units[unit_id]
        key = self._cache_key(unit)
        with content_key_lock(self.cache_root, key):
            cached = self._load_cached(unit, count=False)
            if self._is_covered(cached, targets):
                self._bump("queue_singleflight_skips")
                return
            started = time.perf_counter()
            evidence = self.daily_runner.run_candidate(
                unit.schedule,
                target_repetitions=targets,
                existing=cached,
                stage=stage,
            )
            self._bump("worker_seconds", time.perf_counter() - started)
            self._bump("units_simulated")
            self._save_cached(unit, evidence)

    def _missing_unit_ids(self, targets: Mapping[str, int]) -> list[str]:
        """Every prepared unit that still needs work, in canonical unit order.

        Canonical order makes the lookahead deterministic: two runs of the
        same shortlist schedule the same remainder, so a resumed run continues
        the same sweep instead of re-deciding it.
        """
        missing: list[str] = []
        for unit_id in sorted(self._units):
            unit = self._units[unit_id]
            if not self._is_covered(self._load_cached(unit, count=False), targets):
                missing.append(unit_id)
        return missing

    def _ensure_queue(
        self,
        targets: Mapping[str, int],
        stage: str,
        *,
        scope: Sequence[str] | None = None,
    ) -> GlobalDailyUnitQueue:
        """Start (or retarget) the one global queue for these exact targets.

        Coverage participates in the work identity.  A finalist round asks for
        MORE repetitions than the pilot, and a queue built for pilot coverage
        would otherwise report those units complete and hand back pilot-only
        evidence.  Different targets therefore retire the queue and rebuild it
        from a fresh coverage scan.

        ``stage`` is part of the signature too.  The production backend only
        validates it, so today a stale stage changes nothing - but the stage
        is baked into the executor closure, so keying on coverage alone would
        quietly replay "pilot" for a finalist round whenever the two happen to
        ask for the same repetitions.  That is a trap for the next backend
        that gives the label meaning, and it costs one extra retarget to
        avoid.

        ``scope`` is what keeps the lookahead honest.  ``None`` means the
        whole prepared remainder, which is only ever correct for the
        exhaustive pilot sweep, where every prepared unit is verified anyway
        and the queue merely reorders work the run had already committed to.
        A finalist round has NOT committed to that: the policy promotes at
        most a handful of parents and asks them for more repetitions, so a
        global rebuild at finalist coverage would upgrade all 1 950 prepared
        units - hours of SUMO nobody asked for, and an adaptive bump from 4
        to 12 repetitions would order it again.  Every non-pilot caller
        therefore passes an explicit unit scope.
        """
        signature = (
            str(stage),
            tuple((variant, int(targets[variant])) for variant in DEMAND_VARIANTS),
            scope is None,
        )
        # Lock ORDER is always _queue_build_lock -> _state_lock, and puller
        # threads take _state_lock ONLY.  Retiring a queue therefore never
        # holds a lock a pump needs.  Doing it the obvious way instead -
        # calling `queue.stop()` inside `_state_lock` - deadlocks on the
        # first finalist retarget: `stop()` joins the pullers, and a puller
        # inside `_produce_unit` blocks on `_bump`/`_load_cached`, which want
        # the very lock the retargeting thread is holding.
        with self._queue_build_lock:
            with self._state_lock:
                queue = self._queue
                if queue is not None and self._queue_targets == signature:
                    return queue
                retired, self._queue = queue, None
                self._queue_targets = None
                self._queue_stage = None
            if retired is not None:
                retired.stop()
                with self._state_lock:
                    self._queue_final_stats = retired.stats()
            # Also outside the state lock: the global scan rescans the whole
            # prepared unit set on disk and would otherwise stall progress
            # reporting.
            missing = (
                self._missing_unit_ids(targets)
                if scope is None
                else list(dict.fromkeys(str(value) for value in scope))
            )
            queue = GlobalDailyUnitQueue(
                missing,
                workers=self.queue_workers,
                execute=partial(self._produce_unit, targets=targets, stage=stage),
            )
            with self._state_lock:
                self._queue = queue
                self._queue_targets = signature
                self._queue_stage = stage
            return queue

    @staticmethod
    def _coverage(evidence: CandidateEvidence | None) -> dict[str, int]:
        result = {variant: 0 for variant in DEMAND_VARIANTS}
        if evidence is None:
            return result
        for variant in DEMAND_VARIANTS:
            result[variant] = sum(
                item.demand_variant == variant for item in evidence.observations
            )
        return result

    @staticmethod
    def _trim_to_targets(
        evidence: CandidateEvidence,
        targets: Mapping[str, int],
    ) -> CandidateEvidence:
        selected: list[PairedObservation] = []
        for variant in DEMAND_VARIANTS:
            observations = sorted(
                (
                    item for item in evidence.observations
                    if item.demand_variant == variant
                ),
                key=lambda item: item.seed,
            )
            target = targets[variant]
            if len(observations) < target:
                raise ValueError(
                    f"daily evidence lacks {variant} target coverage"
                )
            selected.extend(observations[:target])
        selected_identities = {
            (item.demand_variant, item.seed) for item in selected
        }
        return CandidateEvidence(
            candidate_id=evidence.candidate_id,
            observations=tuple(selected),
            hard_failures=evidence.hard_failures,
            disruption=evidence.disruption,
            timeout_undecided=evidence.timeout_undecided,
            canonical_observation_digests=(
                tuple(item for item in evidence.canonical_observation_digests
                      if (item.variant, item.seed) in selected_identities)),
        )

    def run_candidate(
        self,
        schedule: ClosureSchedule,
        *,
        target_repetitions: Mapping[str, int],
        existing: CandidateEvidence | None,
        stage: str,
    ) -> CandidateEvidence:
        if self._prepared_parent_ids is None:
            raise RuntimeError("independent daily runner is not prepared")
        unit_ids = self._parents.get(schedule.schedule_id)
        if unit_ids is None:
            raise ValueError("candidate was not part of the prepared shortlist")
        if existing is not None and (
            existing.hard_failures
            or all(
                self._coverage(existing)[variant]
                >= target_repetitions[variant]
                for variant in DEMAND_VARIANTS
            )
        ):
            return existing

        evidence_by_unit: dict[str, CandidateEvidence] = {}
        units = [self._units[unit_id] for unit_id in unit_ids]
        if self.queue_workers > 1:
            # Global path: this parent's own missing units are promoted to the
            # front of ONE shared remainder and awaited here, while the same
            # puller threads keep the remaining width busy with lookahead for
            # later parents.  Everything below then runs unchanged against a
            # cache that already holds this parent's units, so the parent's
            # result is assembled in its own canonical order and cannot depend
            # on which unit happened to finish first.
            needed = [
                unit.unit_id
                for unit in units
                if not self._is_covered(
                    self._load_cached(unit, count=False), target_repetitions
                )
            ]
            if needed:
                # Lookahead is global for the exhaustive pilot sweep only.
                # Any other stage gets a queue scoped to the units it asked
                # for, so a finalist round never upgrades the whole prepared
                # shortlist to finalist coverage.
                queue = self._ensure_queue(
                    target_repetitions,
                    stage,
                    scope=None if stage == QUEUE_LOOKAHEAD_STAGE else needed,
                )
                queue.add(needed)
                queue.require(needed)
        pending: list[
            tuple[
                ClosureSchedule,
                Mapping[str, int],
                CandidateEvidence | None,
                str,
            ]
        ] = []
        cached_by_schedule: dict[str, CandidateEvidence] = {}
        for unit in units:
            cached = self._load_cached(unit)
            coverage = self._coverage(cached)
            if cached is not None and (
                cached.hard_failures
                or all(
                    coverage[variant] >= target_repetitions[variant]
                    for variant in DEMAND_VARIANTS
                )
            ):
                cached_by_schedule[unit.schedule.schedule_id] = cached
            else:
                pending.append((
                    unit.schedule,
                    target_repetitions,
                    cached,
                    stage,
                ))

        batch = getattr(self.daily_runner, "run_candidate_batch", None)
        updated_by_schedule = {}
        if pending:
            started = time.perf_counter()
            if callable(batch):
                updated_by_schedule = batch(pending)
            else:
                updated_by_schedule = {
                    daily_schedule.schedule_id: self.daily_runner.run_candidate(
                        daily_schedule,
                        target_repetitions=targets,
                        existing=cached,
                        stage=pending_stage,
                    )
                    for daily_schedule, targets, cached, pending_stage in pending
                }
            self._timing["worker_seconds"] += time.perf_counter() - started
            self._timing["units_simulated"] += len(pending)
        for unit in units:
            updated = cached_by_schedule.get(unit.schedule.schedule_id)
            if updated is None:
                updated = updated_by_schedule.get(unit.schedule.schedule_id)
                if updated is None:
                    raise ValueError(
                        "daily batch omitted " + unit.schedule.schedule_id
                    )
                self._save_cached(unit, updated)
            evidence_by_unit[unit.unit_id] = (
                updated
                if updated.hard_failures
                else self._trim_to_targets(updated, target_repetitions)
            )
        if self._backend_digest is None:
            raise RuntimeError("independent backend identity is unavailable")
        return aggregate_daily_evidence(
            schedule,
            units,
            evidence_by_unit,
            aggregate_provenance_key=(
                "independent-daily-study-" + self._backend_digest[:24]
            ),
        )
