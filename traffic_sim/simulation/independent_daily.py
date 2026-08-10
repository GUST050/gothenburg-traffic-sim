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
    CandidateEvidence,
    DEMAND_VARIANTS,
    PairedObservation,
)
from traffic_sim.simulation.envelope import EnvelopePolicy


CACHE_SCHEMA = "independent_daily_evidence_cache_v2"
BACKEND_KIND = "independent_daily_reset_backend_v1"
ISOLATED_BACKEND_KIND = "isolated_daily_sumo_backend_v1"
# Keep the production six-hour recovery cap. Independent reset is permitted
# only BETWEEN separately evaluated work days; it must not make an evening
# closure look better by truncating its own recovery tail. This means a daily
# unit may correctly resolve a two-date archive.
INDEPENDENT_DAILY_ENVELOPE_POLICY = EnvelopePolicy()


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
        observations: dict[tuple[str, int], PairedObservation] = {}
        for item in evidence.observations:
            key = (item.demand_variant, item.seed)
            if key in observations:
                raise ValueError("daily evidence repeats a variant/seed")
            observations[key] = item
        indexed[unit.unit_id] = observations

    # A hard failure is already a terminal, fail-closed result for the parent.
    # Do not require the failed daily unit to fabricate a complete replication
    # matrix merely so the additive path can continue.  Namespacing the reason
    # by work date keeps one cached daily failure useful (and diagnosable)
    # across every parent schedule that includes that unit.
    if failures:
        return CandidateEvidence(
            candidate_id=parent.schedule_id,
            observations=(),
            hard_failures=tuple(sorted(failures)),
        )

    expected = set(next(iter(indexed.values())))
    for unit_id, observations in indexed.items():
        if set(observations) != expected:
            raise ValueError(
                f"daily evidence variant/seed coverage differs for {unit_id}")

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
    )


def _evidence_to_dict(evidence: CandidateEvidence) -> dict[str, Any]:
    import dataclasses

    return {
        "candidate_id": evidence.candidate_id,
        "observations": [dataclasses.asdict(item) for item in evidence.observations],
        "hard_failures": list(evidence.hard_failures),
        "disruption": [dict(item) for item in evidence.disruption],
    }


def _evidence_from_dict(raw: Mapping[str, Any]) -> CandidateEvidence:
    return CandidateEvidence(
        candidate_id=str(raw.get("candidate_id", "")),
        observations=tuple(
            PairedObservation(**dict(item))
            for item in raw.get("observations", ())
        ),
        hard_failures=tuple(str(item) for item in raw.get("hard_failures", ())),
        disruption=tuple(dict(item) for item in raw.get("disruption", ())),
    )


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

    @staticmethod
    def _default_worker_invoker(request: Mapping[str, Any]) -> CandidateEvidence:
        with tempfile.TemporaryDirectory(prefix="independent-daily-worker-") as raw:
            root = Path(raw)
            request_path = root / "request.json"
            result_path = root / "result.json"
            _atomic_json(request_path, request)
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "traffic_sim.simulation.independent_daily_worker",
                    "--request",
                    str(request_path),
                    "--result",
                    str(result_path),
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[-2000:]
                raise RuntimeError(
                    "isolated daily SUMO worker exited "
                    f"{completed.returncode}: {detail}"
                )
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, Mapping)
                or payload.get("schema")
                != "independent_daily_worker_result_v1"
            ):
                raise ValueError("isolated daily worker result is malformed")
            return _evidence_from_dict(payload["evidence"])

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
    ) -> None:
        if spec.interday_policy != "independent_daily_reset_v1":
            raise ValueError("independent daily runner requires its policy")
        self.spec = ClosureSearchSpec.from_dict(spec.to_dict())
        self.daily_runner = daily_runner
        self.cache_root = Path(cache_root)
        self._units: dict[str, DailyClosureUnit] = {}
        self._parents: dict[str, tuple[str, ...]] = {}
        self._backend_digest: str | None = None
        self._unit_backend_digests: dict[str, str] = {}
        self._memory_evidence: dict[str, CandidateEvidence] = {}
        self._prepared_parent_ids: tuple[str, ...] | None = None

    def cleanup(self) -> None:
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

    def _load_cached(self, unit: DailyClosureUnit) -> CandidateEvidence | None:
        remembered = self._memory_evidence.get(unit.unit_id)
        if remembered is not None:
            return remembered
        path = self._cache_path(unit)
        if not path.is_file():
            return None
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
            )
            if not isinstance(payload, Mapping):
                return None
            body = {key: value for key, value in payload.items()
                    if key != "content_key"}
            if (
                payload.get("schema") != CACHE_SCHEMA
                or payload.get("unit") != {
                    "unit_id": unit.unit_id,
                    "identity": dict(unit.identity),
                }
                or payload.get("unit_backend_digest")
                != self._unit_backend_digests.get(unit.unit_id)
                or payload.get("content_key") != _canonical_digest(body)
            ):
                return None
            stored = _evidence_from_dict(payload["evidence"])
            if stored.candidate_id != unit.unit_id or any(
                item.candidate_id != unit.unit_id
                for item in stored.observations
            ):
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
            )
            self._memory_evidence[unit.unit_id] = rebound
            return rebound
        except (
            OSError, UnicodeError, AttributeError, ValueError, TypeError,
            KeyError, json.JSONDecodeError,
        ):
            return None

    def _save_cached(
        self,
        unit: DailyClosureUnit,
        evidence: CandidateEvidence,
    ) -> None:
        if evidence.candidate_id != unit.schedule.schedule_id or any(
            item.candidate_id != unit.schedule.schedule_id
            for item in evidence.observations
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
        _atomic_json(self._cache_path(unit), payload)
        self._memory_evidence[unit.unit_id] = evidence

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
        return CandidateEvidence(
            candidate_id=evidence.candidate_id,
            observations=tuple(selected),
            hard_failures=evidence.hard_failures,
            disruption=evidence.disruption,
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
        if pending and callable(batch):
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
