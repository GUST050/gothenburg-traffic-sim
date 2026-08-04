"""Canonical full-day annual warm-state planning.

The plan is road-independent and performs no SUMO work.  It enumerates every
supported 15-minute independent daily closure interval, collapses them onto
the canonical demand archives used by production, and requests one candidate-
free prefix state per distinct archive/checkpoint/variant identity. Population
orders each archive/seed/variant chain by checkpoint so only the first state
replays from zero; later states extend the preceding candidate-free state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from traffic_sim.core.contracts import (
    ClosureInterval,
    ClosureSchedule,
    ClosureSearchSpec,
    DailyTimeBand,
)
from traffic_sim.simulation.envelope import (
    EnvelopePolicy,
    build_simulation_envelope,
    independent_daily_demand_spec,
)


PLAN_SCHEMA = "annual_warm_plan_v3"
LEDGER_SCHEMA = "annual_warm_ledger_v2"
PLAN_STATUS = "full_day_planned_unexecuted"
SEED_VARIANTS = ((1000, "q10"), (1001, "q50"), (1002, "q90"))
_CALENDAR_EDGE = "__annual_warm_calendar_only__"
_CALENDAR_DEMAND_ID = "annual-warm-demand-unresolved"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UNIT_STATES = frozenset({"pending", "running", "succeeded", "failed"})
_VALIDATED_PLAN_CACHE: dict[str, dict[str, Any]] = {}


def _canonical_key(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def _strict_keys(raw: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(raw) != expected:
        raise ValueError(
            f"{label} fields differ; missing={sorted(expected - set(raw))}, "
            f"unknown={sorted(set(raw) - expected)}"
        )


def _strict_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return value


def _real_date(value: str, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be a real YYYY-MM-DD date") from exc


@dataclass(frozen=True)
class AnnualWarmCoverageSpec:
    """The complete product-supported independent daily timing domain."""

    year: int
    source: str = "forecast"
    timezone: str = "Europe/Stockholm"
    dst_policy: str = "exclude_transition_dates"
    earliest_start: str = "00:00"
    latest_end: str = "24:00"
    resolution_minutes: int = 15
    maximum_daily_duration_minutes: int = 24 * 60
    baseline_trip_duration_p99_s: int = 3600
    structural_reference_date: str = "2025-09-16"

    def __post_init__(self) -> None:
        _strict_int(self.year, "annual_warm.year", minimum=2000)
        if self.year > 9998:
            raise ValueError("annual_warm.year must be at most 9998")
        _strict_int(
            self.maximum_daily_duration_minutes,
            "annual_warm.maximum_daily_duration_minutes",
            minimum=15,
        )
        _strict_int(
            self.baseline_trip_duration_p99_s,
            "annual_warm.baseline_trip_duration_p99_s",
            minimum=1,
        )
        _real_date(
            self.structural_reference_date,
            "annual_warm.structural_reference_date",
        )
        search = self.calendar_search()
        if (
            self.earliest_start != "00:00"
            or self.latest_end != "24:00"
            or self.resolution_minutes != 15
            or self.maximum_daily_duration_minutes != 1440
        ):
            raise ValueError(
                "annual warming must cover the complete 00:00–24:00 "
                "15-minute daily contract"
            )
        if search.interday_policy != "independent_daily_reset_v1":
            raise ValueError("annual warming must use independent daily reset")

    def calendar_search(self) -> ClosureSearchSpec:
        return ClosureSearchSpec(
            search_id=f"annual-full-day-warm-{self.year}",
            directed_edges=(_CALENDAR_EDGE,),
            demand_build_id=_CALENDAR_DEMAND_ID,
            permitted_date_start=f"{self.year:04d}-01-01",
            permitted_date_end=f"{self.year:04d}-12-31",
            required_work_minutes=self.resolution_minutes,
            max_consecutive_start_days=1,
            permitted_daily_band=DailyTimeBand(
                self.earliest_start, self.latest_end
            ),
            source=self.source,
            timezone=self.timezone,
            dst_policy=self.dst_policy,
            allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
            resolution_minutes=self.resolution_minutes,
            interday_policy="independent_daily_reset_v1",
            work_allocation_policy="exact_balanced_daily_v1",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "source": self.source,
            "timezone": self.timezone,
            "dst_policy": self.dst_policy,
            "earliest_start": self.earliest_start,
            "latest_end": self.latest_end,
            "resolution_minutes": self.resolution_minutes,
            "maximum_daily_duration_minutes": self.maximum_daily_duration_minutes,
            "baseline_trip_duration_p99_s": self.baseline_trip_duration_p99_s,
            "structural_reference_date": self.structural_reference_date,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AnnualWarmCoverageSpec":
        if not isinstance(raw, Mapping):
            raise ValueError("annual_warm.coverage_spec must be an object")
        expected = {
            "year", "source", "timezone", "dst_policy", "earliest_start",
            "latest_end", "resolution_minutes", "maximum_daily_duration_minutes",
            "baseline_trip_duration_p99_s", "structural_reference_date",
        }
        _strict_keys(raw, expected, "annual_warm.coverage_spec")
        return cls(**dict(raw))


def default_2027_coverage_spec() -> AnnualWarmCoverageSpec:
    return AnnualWarmCoverageSpec(year=2027)


def fingerprint_inputs(
    paths: Mapping[str, Path], *, base: Path | None = None
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for label, raw_path in sorted(paths.items()):
        if not isinstance(label, str) or not label:
            raise ValueError("fingerprint labels must be non-empty strings")
        recorded = Path(raw_path)
        path = recorded if base is None else Path(base) / recorded
        data = path.read_bytes()
        result[label] = {
            "path": recorded.as_posix(),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return result


def verify_fingerprint_inputs(
    recorded: Mapping[str, Any], *, base: Path
) -> None:
    """Fail if any source/input bound into a plan changed on disk."""
    checked = _validate_fingerprints(recorded)
    base = Path(base).resolve()
    paths: dict[str, Path] = {}
    for label, record in checked.items():
        relative = Path(record["path"])
        if relative.is_absolute():
            raise ValueError(f"annual plan fingerprint path is absolute: {label}")
        path = base / relative
        if path.is_symlink():
            raise ValueError(
                f"annual plan fingerprint input is unavailable: {label}")
        resolved = path.resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise ValueError(
                f"annual plan fingerprint escapes the repository: {label}") from exc
        if not resolved.is_file():
            raise ValueError(
                f"annual plan fingerprint input is unavailable: {label}")
        paths[label] = relative
    current = fingerprint_inputs(paths, base=base)
    drifted = sorted(
        label for label in checked if checked[label] != current[label])
    if drifted:
        raise ValueError(
            "annual plan bound inputs changed during population: "
            + ", ".join(drifted))


def _validate_fingerprints(raw: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError("annual_warm.input_fingerprints must be non-empty")
    result: dict[str, dict[str, Any]] = {}
    for label, record in sorted(raw.items()):
        if not isinstance(label, str) or not label or not isinstance(record, Mapping):
            raise ValueError("annual_warm.input_fingerprints is malformed")
        _strict_keys(record, {"path", "bytes", "sha256"}, f"fingerprint.{label}")
        if not isinstance(record["path"], str) or not record["path"]:
            raise ValueError(f"fingerprint.{label}.path must be non-empty")
        size = _strict_int(record["bytes"], f"fingerprint.{label}.bytes", minimum=0)
        digest = record["sha256"]
        if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            raise ValueError(f"fingerprint.{label}.sha256 must be lowercase SHA-256")
        result[label] = {"path": record["path"], "bytes": size, "sha256": digest}
    return result


def _validate_runtime_identity(raw: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ValueError("annual_warm.runtime_identity must be an object")
    expected = {"sumo_version", "platform", "simulation_mode", "metric_schema"}
    _strict_keys(raw, expected, "annual_warm.runtime_identity")
    result = {}
    for field in sorted(expected):
        value = raw[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"annual_warm.runtime_identity.{field} must be non-empty"
            )
        result[field] = value
    if result["simulation_mode"] != "meso":
        raise ValueError("annual warming runtime must use meso simulation")
    if result["metric_schema"] != "closure_decision_metrics_v1":
        raise ValueError("annual warming metric schema is unsupported")
    return result


def _clock(minutes: int) -> str:
    if minutes == 1440:
        return "24:00"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _daily_schedule(
    search: ClosureSearchSpec,
    work_date: date,
    start_minutes: int,
    end_minutes: int,
) -> ClosureSchedule:
    start = datetime.combine(work_date, datetime.min.time()) + timedelta(
        minutes=start_minutes
    )
    end = datetime.combine(work_date, datetime.min.time()) + timedelta(
        minutes=end_minutes
    )
    duration = end_minutes - start_minutes
    identity = {
        "search_content_key": search.content_key,
        "first_work_date": work_date.isoformat(),
        "day_count": 1,
        "daily_start": _clock(start_minutes),
        "daily_end": _clock(end_minutes),
        "scheduled_work_minutes": duration,
        "allocation_policy": "exact_balanced_daily_v1",
        "interval_durations_minutes": [duration],
        "work_dates": [work_date.isoformat()],
    }
    return ClosureSchedule(
        schedule_id="closure-" + _canonical_key(identity)[:20],
        search_content_key=search.content_key,
        first_work_date=work_date.isoformat(),
        day_count=1,
        daily_start=_clock(start_minutes),
        daily_end=_clock(end_minutes),
        required_work_minutes=duration,
        scheduled_work_minutes=duration,
        actual_closed_minutes=duration,
        rounding_overshoot_minutes=0,
        intervals=(ClosureInterval(
            work_date=work_date.isoformat(),
            start_time=start.isoformat(timespec="seconds"),
            end_time=end.isoformat(timespec="seconds"),
        ),),
        allocation_policy="exact_balanced_daily_v1",
    )


def _request_id(identity: Mapping[str, Any]) -> str:
    return "annual-request-" + _canonical_key(identity)[:20]


def _request_for_schedule(
    spec: AnnualWarmCoverageSpec,
    search: ClosureSearchSpec,
    schedule: ClosureSchedule,
) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope = build_simulation_envelope(
        search,
        schedule,
        baseline_trip_duration_p99_s=spec.baseline_trip_duration_p99_s,
        policy=EnvelopePolicy(),
    )
    demand = independent_daily_demand_spec(
        search,
        schedule,
        envelope,
        structural_reference_date=spec.structural_reference_date,
    )
    closure_start = datetime.fromisoformat(envelope.closure_start)
    archive_start = datetime.fromisoformat(f"{demand.start_date}T00:00:00")
    checkpoint_s = int((closure_start - archive_start).total_seconds()) - 900
    if checkpoint_s <= 0 or checkpoint_s % 900:
        raise ValueError("annual checkpoint is not a positive aligned instant")
    identity = {
        "demand_build_key": demand.build_key,
        "checkpoint_s": checkpoint_s,
        "work_date": schedule.first_work_date,
        "closure_start": schedule.intervals[0].start_time,
    }
    request = {
        "request_id": _request_id(identity),
        **identity,
        "checkpoint_time": (
            archive_start + timedelta(seconds=checkpoint_s)
        ).isoformat(timespec="seconds"),
        "compatible_interval_count": 1,
    }
    return request, demand.to_dict()


def build_annual_warm_plan(
    spec: AnnualWarmCoverageSpec,
    *,
    input_fingerprints: Mapping[str, Any],
    runtime_identity: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(spec, AnnualWarmCoverageSpec):
        raise TypeError("spec must be an AnnualWarmCoverageSpec")
    fingerprints = _validate_fingerprints(input_fingerprints)
    runtime = _validate_runtime_identity(runtime_identity)
    search = spec.calendar_search()
    resolution = spec.resolution_minutes
    start_day = date(spec.year, 1, 1)
    end_day = date(spec.year + 1, 1, 1)
    request_map: dict[tuple[str, int], dict[str, Any]] = {}
    demand_map: dict[str, dict[str, Any]] = {}
    unavailable: dict[str, int] = {}
    supported_intervals = 0
    possible_intervals = 0

    day = start_day
    while day < end_day:
        for start_minutes in range(0, 1440, resolution):
            maximum_end = min(
                1440,
                start_minutes + spec.maximum_daily_duration_minutes,
            )
            first_end = start_minutes + resolution
            # With a six-hour recovery cap, the midnight-rounded envelope end
            # has only two possible values for an interval confined to one
            # work date: end <= 18:00 and end > 18:00.  Evaluate one
            # representative per class and carry its exact interval count.
            end_groups = []
            early_last = min(maximum_end, 18 * 60)
            if first_end <= early_last:
                end_groups.append((first_end, (early_last - first_end) // resolution + 1))
            late_first = max(first_end, 18 * 60 + resolution)
            if late_first <= maximum_end:
                end_groups.append((late_first, (maximum_end - late_first) // resolution + 1))
            for end_minutes, interval_count in end_groups:
                possible_intervals += interval_count
                schedule = _daily_schedule(search, day, start_minutes, end_minutes)
                try:
                    request, demand = _request_for_schedule(spec, search, schedule)
                except ValueError as exc:
                    message = str(exc)
                    reason = (
                        "source_year_boundary"
                        if "outside the downloaded" in message
                        else "excluded_dst_envelope"
                        if "DST transition" in message
                        else "unsupported_envelope"
                    )
                    unavailable[reason] = unavailable.get(reason, 0) + interval_count
                    continue
                supported_intervals += interval_count
                request["compatible_interval_count"] = interval_count
                key = (request["demand_build_key"], request["checkpoint_s"])
                previous = request_map.get(key)
                if previous is None:
                    request_map[key] = request
                else:
                    if any(
                        previous[field] != request[field]
                        for field in (
                            "request_id", "work_date", "closure_start",
                            "checkpoint_time",
                        )
                    ):
                        raise ValueError("annual checkpoint request collision")
                    previous["compatible_interval_count"] += interval_count
                old_demand = demand_map.get(demand["build_key"])
                if old_demand is not None and old_demand != demand:
                    raise ValueError("annual demand build-key collision")
                demand_map[demand["build_key"]] = demand
        day += timedelta(days=1)

    requests = sorted(
        request_map.values(),
        key=lambda item: (
            item["work_date"], item["closure_start"],
            item["demand_build_key"], item["checkpoint_s"],
        ),
    )
    payload: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "status": PLAN_STATUS,
        "coverage_spec": spec.to_dict(),
        "input_fingerprints": fingerprints,
        "runtime_identity": runtime,
        "seed_variants": [
            {"seed": seed, "variant": variant}
            for seed, variant in SEED_VARIANTS
        ],
        "coverage": {
            "calendar_days": (end_day - start_day).days,
            "daily_clock_slots": 96,
            "possible_daily_intervals": possible_intervals,
            "supported_daily_intervals": supported_intervals,
            "unavailable_daily_intervals": possible_intervals - supported_intervals,
            "unavailable_reasons": dict(sorted(unavailable.items())),
            "checkpoint_requests": len(requests),
            "demand_builds": len(demand_map),
            "state_requests": len(requests) * len(SEED_VARIANTS),
        },
        "safety": {
            "road_scope": "none_calendar_and_demand_only",
            "interday_policy": "independent_daily_reset_v1",
            "checkpoint_semantics": (
                "one aligned interval before closure; each road must still "
                "derive a route-safe warm point and exact cache identity"
            ),
            "archive_semantics": (
                "canonical previous/current/next demand archive with exact "
                "source-year and DST fallbacks"
            ),
            "accuracy_authority": "unchanged full SUMO cold execution",
            "fallback": "cold on any miss, drift, unsafe boundary or failed proof",
            "activation": "none",
            "population_strategy": (
                "ordered_candidate_free_prefix_chain_v1; bootstrap the first "
                "checkpoint per demand/seed/variant and extend every later "
                "checkpoint with exact unfinished-tripinfo reconciliation"
            ),
        },
        "demand_build_specs": [demand_map[key] for key in sorted(demand_map)],
        "checkpoint_requests": requests,
    }
    payload["content_key"] = _canonical_key(payload)
    return payload


def validate_annual_warm_plan(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("annual warm plan must be an object")
    expected = {
        "schema", "status", "coverage_spec", "input_fingerprints",
        "runtime_identity", "seed_variants", "coverage", "safety", "demand_build_specs",
        "checkpoint_requests", "content_key",
    }
    _strict_keys(raw, expected, "annual warm plan")
    if raw["schema"] != PLAN_SCHEMA or raw["status"] != PLAN_STATUS:
        raise ValueError("annual warm plan schema or status is unsupported")
    supplied = raw["content_key"]
    if not isinstance(supplied, str) or supplied != _canonical_key(raw):
        raise ValueError("annual warm plan content_key does not match")
    cached = _VALIDATED_PLAN_CACHE.get(supplied)
    if cached is not None:
        if dict(raw) != cached:
            raise ValueError("annual warm plan content collides with cached plan")
        return deepcopy(cached)
    spec = AnnualWarmCoverageSpec.from_dict(raw["coverage_spec"])
    recomposed = build_annual_warm_plan(
        spec,
        input_fingerprints=raw["input_fingerprints"],
        runtime_identity=raw["runtime_identity"],
    )
    if dict(raw) != recomposed:
        raise ValueError("annual warm plan does not recompose exactly")
    if len(_VALIDATED_PLAN_CACHE) >= 4:
        _VALIDATED_PLAN_CACHE.pop(next(iter(_VALIDATED_PLAN_CACHE)))
    _VALIDATED_PLAN_CACHE[supplied] = deepcopy(recomposed)
    return deepcopy(recomposed)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_annual_warm_plan(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    return validate_annual_warm_plan(payload)


def write_annual_warm_plan(path: Path, payload: Mapping[str, Any]) -> None:
    validated = validate_annual_warm_plan(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(validated, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class AnnualWarmPlanIndex:
    """Resolve exact daily schedules to candidate-free checkpoint requests."""

    def __init__(self, plan: Mapping[str, Any]) -> None:
        checked = validate_annual_warm_plan(plan)
        self._plan = checked
        self._spec = AnnualWarmCoverageSpec.from_dict(checked["coverage_spec"])
        self._search = self._spec.calendar_search()
        self._demands = {
            item["build_key"]: item for item in checked["demand_build_specs"]
        }
        self._requests = {
            (item["demand_build_key"], item["checkpoint_s"]): item
            for item in checked["checkpoint_requests"]
        }

    @property
    def slot_count(self) -> int:
        return len(self._requests)

    @property
    def request_count(self) -> int:
        return len(self._requests)

    def resolve_schedule(self, schedule: ClosureSchedule) -> dict[str, Any] | None:
        if not isinstance(schedule, ClosureSchedule):
            raise TypeError("schedule must be a ClosureSchedule")
        if schedule.day_count != 1 or len(schedule.intervals) != 1:
            return None
        interval = schedule.intervals[0]
        work_day = date.fromisoformat(interval.work_date)
        if work_day.year != self._spec.year:
            return None
        start = datetime.fromisoformat(interval.start_time)
        end = datetime.fromisoformat(interval.end_time)
        midnight = datetime.combine(work_day, datetime.min.time())
        start_minutes = int((start - midnight).total_seconds() // 60)
        end_minutes = int((end - midnight).total_seconds() // 60)
        if end_minutes <= start_minutes:
            end_minutes += 1440
        if (
            start_minutes < 0 or start_minutes >= 1440
            or end_minutes > 1440
            or start_minutes % 15 or end_minutes % 15
        ):
            return None
        clone = _daily_schedule(
            self._search, work_day, start_minutes, end_minutes
        )
        try:
            request, demand = _request_for_schedule(
                self._spec, self._search, clone
            )
        except ValueError:
            return None
        stored = self._requests.get((demand["build_key"], request["checkpoint_s"]))
        if stored is None:
            return None
        return deepcopy({
            "plan_content_key": self._plan["content_key"],
            "request": stored,
            "demand_build_spec": self._demands[demand["build_key"]],
            "seed_variants": self._plan["seed_variants"],
            "safety": self._plan["safety"],
        })


def build_pending_ledger(plan: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_annual_warm_plan(plan)
    units = list(iter_work_units(checked))
    ledger: dict[str, Any] = {
        "schema": LEDGER_SCHEMA,
        "plan_content_key": checked["content_key"],
        "revision": 0,
        "work_units": units,
    }
    ledger["content_key"] = _canonical_key(ledger)
    return ledger


def _work_unit_from_checked(
    checked: Mapping[str, Any],
    request: Mapping[str, Any],
    seed_variant: Mapping[str, Any],
) -> dict[str, Any]:
    identity = {
        "plan_content_key": checked["content_key"],
        "request_id": request["request_id"],
        "demand_build_key": request["demand_build_key"],
        "checkpoint_s": request["checkpoint_s"],
        "seed": seed_variant["seed"],
        "variant": seed_variant["variant"],
    }
    return {
        "unit_id": "annual-unit-" + _canonical_key(identity)[:20],
        **identity,
        "state": "pending",
        "attempts": 0,
        "artifact_content_key": None,
        "error": None,
    }


def annual_work_unit(
    plan: Mapping[str, Any],
    request: Mapping[str, Any],
    seed_variant: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the immutable identity and pristine lifecycle for one unit."""
    checked = validate_annual_warm_plan(plan)
    stored_request = next(
        (
            item for item in checked["checkpoint_requests"]
            if item["request_id"] == request.get("request_id")
        ),
        None,
    )
    if stored_request is None or any(
        request.get(field) != stored_request[field]
        for field in ("demand_build_key", "checkpoint_s")
    ):
        raise ValueError("annual work unit request is not in the plan")
    stored_mapping = next(
        (
            item for item in checked["seed_variants"]
            if item == {
                "seed": seed_variant.get("seed"),
                "variant": seed_variant.get("variant"),
            }
        ),
        None,
    )
    if stored_mapping is None:
        raise ValueError("annual work unit seed variant is not in the plan")
    return _work_unit_from_checked(checked, stored_request, stored_mapping)


def iter_work_units(plan: Mapping[str, Any]):
    """Yield stable population identities without materializing a ledger."""
    checked = validate_annual_warm_plan(plan)
    for request in checked["checkpoint_requests"]:
        for mapping in checked["seed_variants"]:
            yield _work_unit_from_checked(checked, request, mapping)


def validate_annual_warm_ledger(
    raw: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    checked_plan = validate_annual_warm_plan(plan)
    if not isinstance(raw, Mapping):
        raise ValueError("annual warm ledger must be an object")
    _strict_keys(
        raw,
        {"schema", "plan_content_key", "revision", "work_units", "content_key"},
        "annual warm ledger",
    )
    if raw["schema"] != LEDGER_SCHEMA:
        raise ValueError("annual warm ledger schema is unsupported")
    if raw["plan_content_key"] != checked_plan["content_key"]:
        raise ValueError("annual warm ledger belongs to another plan")
    _strict_int(raw["revision"], "annual warm ledger revision", minimum=0)
    if raw["content_key"] != _canonical_key(raw):
        raise ValueError("annual warm ledger content_key does not match")
    units = raw["work_units"]
    if not isinstance(units, list):
        raise ValueError("annual warm ledger work_units must be a list")
    expected = build_pending_ledger(checked_plan)["work_units"]
    if len(units) != len(expected):
        raise ValueError("annual warm ledger work-unit count differs")
    fixed = (
        "unit_id", "plan_content_key", "request_id", "demand_build_key",
        "checkpoint_s", "seed", "variant",
    )
    seen: set[str] = set()
    for index, (unit, pristine) in enumerate(zip(units, expected)):
        if not isinstance(unit, Mapping):
            raise ValueError(f"annual warm ledger unit {index} must be an object")
        _strict_keys(unit, set(pristine), f"annual warm ledger unit {index}")
        if any(unit[field] != pristine[field] for field in fixed):
            raise ValueError(f"annual warm ledger unit {index} identity changed")
        if unit["unit_id"] in seen:
            raise ValueError("annual warm ledger contains duplicate unit IDs")
        seen.add(unit["unit_id"])
        state = unit["state"]
        attempts = _strict_int(
            unit["attempts"], f"annual warm ledger unit {index} attempts", minimum=0
        )
        artifact = unit["artifact_content_key"]
        error = unit["error"]
        if state not in _UNIT_STATES:
            raise ValueError(f"annual warm ledger unit {index} state is unsupported")
        if state == "pending" and (attempts or artifact is not None or error is not None):
            raise ValueError("pending annual warm unit has execution evidence")
        if state == "running" and (attempts < 1 or artifact is not None or error is not None):
            raise ValueError("running annual warm unit is inconsistent")
        if state == "succeeded" and (
            attempts < 1 or not isinstance(artifact, str)
            or not _HEX64.fullmatch(artifact) or error is not None
        ):
            raise ValueError("succeeded annual warm unit lacks exact artifact identity")
        if state == "failed" and (
            attempts < 1 or artifact is not None
            or not isinstance(error, str) or not error.strip()
        ):
            raise ValueError("failed annual warm unit lacks an error")
    return json.loads(json.dumps(raw))


def load_annual_warm_ledger(path: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    return validate_annual_warm_ledger(payload, plan)


def write_annual_warm_ledger(
    path: Path, ledger: Mapping[str, Any], plan: Mapping[str, Any]
) -> None:
    checked = validate_annual_warm_ledger(ledger, plan)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(checked, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def update_ledger_unit(
    ledger: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    unit_id: str,
    state: str,
    artifact_content_key: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    checked = validate_annual_warm_ledger(ledger, plan)
    if state not in _UNIT_STATES - {"pending"}:
        raise ValueError("ledger updates may target running, succeeded or failed")
    matches = [unit for unit in checked["work_units"] if unit["unit_id"] == unit_id]
    if len(matches) != 1:
        raise ValueError("ledger unit_id is unknown or duplicated")
    unit = matches[0]
    current = unit["state"]
    if state == "running":
        if artifact_content_key is not None or error is not None:
            raise ValueError("a running transition cannot carry outcome evidence")
        if current not in {"pending", "running", "failed"}:
            raise ValueError("only pending, interrupted or failed units may start")
        unit["attempts"] += 1
        unit["artifact_content_key"] = None
        unit["error"] = None
    elif state == "succeeded":
        if error is not None or current != "running":
            raise ValueError("only a running unit may succeed without an error")
        unit["artifact_content_key"] = artifact_content_key
        unit["error"] = None
    else:
        if artifact_content_key is not None or current != "running":
            raise ValueError("only a running unit may fail without an artifact")
        unit["artifact_content_key"] = None
        unit["error"] = error
    unit["state"] = state
    checked["revision"] += 1
    checked["content_key"] = _canonical_key(checked)
    return validate_annual_warm_ledger(checked, plan)
