"""Versioned contracts shared by normal, closure, and signal studies.

The simulation tools historically accepted overlapping sets of CLI flags.  A
validated :class:`ScenarioSpec` gives them one exact definition of date,
closures, seeds, variants, and analysis windows.  This module intentionally
uses only the Python standard library so every boundary can validate a spec
before loading SUMO or the demand/model stack.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 1
_EDGE_ID = re.compile(r"^[^\s/\\]+$")
_MODES = frozenset({"meso", "micro"})
_VARIANTS = frozenset({"q10", "q50", "q90", "edge_shares"})
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.+-]+$")
_CLOSURE_SOURCES = frozenset({"historical", "forecast"})
_DST_POLICIES = frozenset({"exclude_transition_dates"})
_DURATION_BASES = frozenset({"required_work_time"})
_WORK_TO_CLOSURE_ASSUMPTIONS = frozenset({"one_to_one"})
_POLICY_STATUSES = frozenset({"user_supplied_unverified"})
_INTERDAY_POLICIES = frozenset({
    "continuous_v1",
    "independent_daily_reset_v1",
})
_WORK_ALLOCATION_POLICIES = frozenset({
    "equal_daily_rounded_v1",
    "exact_balanced_daily_v1",
    "exact_equal_daily_v1",
})
_PERIOD_COMPARISON_POLICIES = frozenset({"none_v1", "rolling_period_v1"})
_CONTINUOUS_MAX_WORKDAYS = 21
_INDEPENDENT_MAX_WORKDAYS = 90
# "standard" (interactive Simulera datum) stays at the validated 7-day
# ceiling. "closure_envelope" covers a closure-search schedule plus its
# warm-up and recovery: a 21-consecutive-day closure (3 weeks) needs at
# most 23 demand days, so 24 gives one day of headroom. Closures beyond the
# golden 7-day continuity freeze run the same per-day integrity checks but
# are longer than the frozen validation — the search labels them
# accordingly and they cost proportionally more to build and simulate.
_DEMAND_PURPOSE_MAX_DAYS = {
    "standard": 7,
    "closure_envelope": 24,
}


def _date(value: str, label: str) -> str:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError(f"{label} must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a real calendar date") from exc
    return value


def _clock(value: str, label: str) -> str:
    if not isinstance(value, str) or not _CLOCK.fullmatch(value):
        raise ValueError(f"{label} must be HH:MM (24:00 is allowed only as an end)")
    return value


def _clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _datetime_clock(value: datetime) -> str:
    """Fast locale-independent HH:MM formatting for validated datetimes."""
    return f"{value.hour:02d}:{value.minute:02d}"


def _parse_time(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty ISO datetime")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        # Existing flow artifacts use local, timezone-less ISO epochs.  Keep
        # accepting them, but require every field in a spec to use the same
        # convention so comparisons remain deterministic.
        return parsed
    return parsed


def _ordered(start: str, end: str, label: str) -> None:
    _same_timezone_style([start, end], label)
    if _parse_time(start, f"{label}.start") >= _parse_time(end, f"{label}.end"):
        raise ValueError(f"{label}.start must be before {label}.end")


def _same_timezone_style(values: list[str], label: str) -> None:
    styles = {bool(_parse_time(value, label).tzinfo) for value in values}
    if len(styles) > 1:
        raise ValueError(f"{label} mixes timezone-aware and timezone-less datetimes")


def _edge(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or not _EDGE_ID.fullmatch(value):
        raise ValueError(f"{label} must be a non-empty edge ID without path separators")
    return value


def _string_list(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be a list of strings")
    try:
        result = tuple(str(value) for value in values)
    except TypeError as exc:
        raise ValueError(f"{label} must be a list of strings") from exc
    if any(not value or not value.strip() for value in result):
        raise ValueError(f"{label} cannot contain empty strings")
    return result


def _strict_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _content_key(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class AnalysisWindow:
    """Warm-up, measured interval, and drain for bounded studies."""

    warmup_s: int
    measure_start: str
    measure_end: str
    drain_s: int

    def __post_init__(self) -> None:
        if isinstance(self.warmup_s, bool) or self.warmup_s < 0:
            raise ValueError("analysis_window.warmup_s must be non-negative")
        if isinstance(self.drain_s, bool) or self.drain_s < 0:
            raise ValueError("analysis_window.drain_s must be non-negative")
        _ordered(self.measure_start, self.measure_end, "analysis_window")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AnalysisWindow":
        return cls(
            warmup_s=int(raw["warmup_s"]),
            measure_start=str(raw["measure_start"]),
            measure_end=str(raw["measure_end"]),
            drain_s=int(raw["drain_s"]),
        )


@dataclass(frozen=True)
class DemandBuildSpec:
    """Validated identity of one calibrated demand build.

    A demand build is an input to every later SUMO scenario.  Keeping its
    calendar range, source and effective calibration window in one contract
    prevents the API, CLI and metadata from silently describing different
    datasets.  ``build_key`` is content-addressed and deliberately excludes
    itself, so it is stable across machines and archive filenames.
    """

    start_date: str
    source: str = "historical"
    days: int = 1
    begin: str = "00:00"
    end: str = "24:00"
    structural_reference_date: str = "2025-09-16"
    purpose: str = "standard"

    def __post_init__(self) -> None:
        _date(self.start_date, "demand.start_date")
        _date(self.structural_reference_date,
              "demand.structural_reference_date")
        if self.source not in {"historical", "forecast"}:
            raise ValueError("demand.source must be historical or forecast")
        if self.purpose not in _DEMAND_PURPOSE_MAX_DAYS:
            raise ValueError(
                f"demand.purpose must be one of "
                f"{sorted(_DEMAND_PURPOSE_MAX_DAYS)}")
        maximum_days = _DEMAND_PURPOSE_MAX_DAYS[self.purpose]
        if (isinstance(self.days, bool) or not isinstance(self.days, int)
                or not (1 <= self.days <= maximum_days)):
            raise ValueError(
                f"demand.days must be an integer from 1 through {maximum_days} "
                f"for purpose={self.purpose}")
        _clock(self.begin, "demand.begin")
        _clock(self.end, "demand.end")
        if _clock_minutes(self.end) <= _clock_minutes(self.begin):
            raise ValueError("demand.end must be after demand.begin")
        # Multi-day builds are always continuous full calendar days in the
        # pipeline.  Requiring the effective window here prevents a caller
        # from recording 06:00-10:00 while the builder actually uses 00:00-24:00.
        if self.days > 1 and (self.begin != "00:00" or self.end != "24:00"):
            raise ValueError("multi-day demand builds must use begin=00:00 and end=24:00")

    @property
    def build_key(self) -> str:
        payload = {
            "start_date": self.start_date,
            "source": self.source,
            "days": self.days,
            "begin": self.begin,
            "end": self.end,
            "structural_reference_date": self.structural_reference_date,
        }
        # Keep every existing standard build key byte-for-byte stable.
        if self.purpose != "standard":
            payload["purpose"] = self.purpose
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DemandBuildSpec":
        if not isinstance(raw, Mapping):
            raise ValueError("demand_build_spec must be a JSON object")
        # ``date`` is accepted only as a migration alias for old API clients.
        start_date = raw.get("start_date", raw.get("date"))
        if start_date is None:
            raise ValueError("demand.start_date is required")
        raw_days = raw.get("days", 1)
        if isinstance(raw_days, bool):
            raise ValueError("demand.days must be an integer")
        try:
            parsed_days = int(raw_days)
        except (TypeError, ValueError) as exc:
            raise ValueError("demand.days must be an integer") from exc
        spec = cls(
            start_date=str(start_date),
            source=str(raw.get("source", "historical")),
            days=parsed_days,
            begin=str(raw.get("begin", "00:00")),
            end=str(raw.get("end", "24:00")),
            structural_reference_date=str(
                raw.get("structural_reference_date", "2025-09-16")),
            purpose=str(raw.get("purpose", "standard")),
        )
        supplied_key = raw.get("build_key")
        if supplied_key is not None and str(supplied_key) != spec.build_key:
            raise ValueError("demand.build_key does not match the spec contents")
        return spec

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "demand_build",
            "start_date": self.start_date,
            "source": self.source,
            "days": self.days,
            "begin": self.begin,
            "end": self.end,
            "structural_reference_date": self.structural_reference_date,
            "purpose": self.purpose,
            "build_key": self.build_key,
        }


@dataclass(frozen=True)
class DailyTimeBand:
    """Earliest start and latest end allowed for one daily work shift.

    ``latest_end`` earlier than ``earliest_start`` denotes a band that
    crosses midnight, for example 22:00--06:00.
    """

    earliest_start: str
    latest_end: str

    def __post_init__(self) -> None:
        _clock(self.earliest_start, "closure_search.permitted_daily_band.earliest_start")
        _clock(self.latest_end, "closure_search.permitted_daily_band.latest_end")
        if self.earliest_start == "24:00":
            raise ValueError("permitted_daily_band.earliest_start cannot be 24:00")
        if self.earliest_start == self.latest_end:
            raise ValueError("permitted_daily_band must have a non-zero duration")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DailyTimeBand":
        if not isinstance(raw, Mapping):
            raise ValueError("permitted_daily_band must be a JSON object")
        return cls(
            earliest_start=str(raw.get("earliest_start", "")),
            latest_end=str(raw.get("latest_end", "")),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "earliest_start": self.earliest_start,
            "latest_end": self.latest_end,
        }


@dataclass(frozen=True)
class ClosureSearchSpec:
    """Immutable user intent for a recurring road-work schedule search."""

    search_id: str
    directed_edges: tuple[str, ...]
    demand_build_id: str
    permitted_date_start: str
    permitted_date_end: str
    required_work_minutes: int
    max_consecutive_start_days: int
    permitted_daily_band: DailyTimeBand
    source: str = "forecast"
    timezone: str = "Europe/Stockholm"
    dst_policy: str = "exclude_transition_dates"
    allowed_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    blackout_dates: tuple[str, ...] = field(default_factory=tuple)
    same_daily_window: bool = True
    resolution_minutes: int = 15
    closure_type: str = "full"
    duration_basis: str = "required_work_time"
    work_to_closure_assumption: str = "one_to_one"
    objective_profile: str = "robust_time_loss"
    policy_status: str = "user_supplied_unverified"
    interday_policy: str = "continuous_v1"
    work_allocation_policy: str = "equal_daily_rounded_v1"
    period_comparison_policy: str = "none_v1"

    def __post_init__(self) -> None:
        if (not isinstance(self.search_id, str) or not self.search_id
                or not _SAFE_KEY.fullmatch(self.search_id)):
            raise ValueError("closure_search.search_id must be a safe non-empty key")
        edges = tuple(
            _edge(value, "closure_search.directed_edges")
            for value in _string_list(self.directed_edges,
                                      "closure_search.directed_edges")
        )
        if not edges:
            raise ValueError("closure_search.directed_edges cannot be empty")
        if len(set(edges)) != len(edges):
            raise ValueError("closure_search.directed_edges must be unique")
        object.__setattr__(self, "directed_edges", edges)

        if not isinstance(self.demand_build_id, str) or not self.demand_build_id.strip():
            raise ValueError("closure_search.demand_build_id must be non-empty")
        if self.source not in _CLOSURE_SOURCES:
            raise ValueError(
                f"closure_search.source must be one of {sorted(_CLOSURE_SOURCES)}")
        _date(self.permitted_date_start, "closure_search.permitted_date_start")
        _date(self.permitted_date_end, "closure_search.permitted_date_end")
        if self.permitted_date_start > self.permitted_date_end:
            raise ValueError(
                "closure_search.permitted_date_start must not be after permitted_date_end")

        if self.timezone != "Europe/Stockholm":
            raise ValueError("closure_search.timezone must be Europe/Stockholm")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("closure_search.timezone is unavailable") from exc
        if self.dst_policy not in _DST_POLICIES:
            raise ValueError(
                f"closure_search.dst_policy must be one of {sorted(_DST_POLICIES)}")

        required = _strict_int(
            self.required_work_minutes, "closure_search.required_work_minutes")
        if required <= 0:
            raise ValueError("closure_search.required_work_minutes must be positive")
        maximum_days = _strict_int(
            self.max_consecutive_start_days,
            "closure_search.max_consecutive_start_days",
        )
        if not 1 <= maximum_days <= _INDEPENDENT_MAX_WORKDAYS:
            raise ValueError(
                "closure_search.max_consecutive_start_days must be from 1 "
                f"through {_INDEPENDENT_MAX_WORKDAYS}")
        resolution = _strict_int(
            self.resolution_minutes, "closure_search.resolution_minutes")
        if resolution != 15:
            raise ValueError("closure_search.resolution_minutes must be 15")
        if not isinstance(self.permitted_daily_band, DailyTimeBand):
            raise ValueError(
                "closure_search.permitted_daily_band must be a DailyTimeBand")
        if (_clock_minutes(self.permitted_daily_band.earliest_start) % resolution
                or _clock_minutes(self.permitted_daily_band.latest_end) % resolution):
            raise ValueError(
                "closure_search.permitted_daily_band must align to 15 minutes")

        weekdays_raw = self.allowed_weekdays
        if isinstance(weekdays_raw, (str, bytes)):
            raise ValueError("closure_search.allowed_weekdays must be integer weekdays")
        try:
            weekdays = tuple(weekdays_raw)
        except TypeError as exc:
            raise ValueError(
                "closure_search.allowed_weekdays must be integer weekdays") from exc
        if not weekdays or any(
            isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6
            for day in weekdays
        ):
            raise ValueError(
                "closure_search.allowed_weekdays must contain values from 0 through 6")
        if len(set(weekdays)) != len(weekdays):
            raise ValueError("closure_search.allowed_weekdays must be unique")
        object.__setattr__(self, "allowed_weekdays", tuple(sorted(weekdays)))

        blackouts = tuple(sorted(
            _date(value, "closure_search.blackout_dates")
            for value in _string_list(
                self.blackout_dates, "closure_search.blackout_dates")
        ))
        if len(set(blackouts)) != len(blackouts):
            raise ValueError("closure_search.blackout_dates must be unique")
        object.__setattr__(self, "blackout_dates", blackouts)

        if self.same_daily_window is not True:
            raise ValueError("closure_search.same_daily_window must be true")
        if self.closure_type != "full":
            raise ValueError("closure_search.closure_type must be full")
        if self.duration_basis not in _DURATION_BASES:
            raise ValueError(
                f"closure_search.duration_basis must be one of {sorted(_DURATION_BASES)}")
        if self.work_to_closure_assumption not in _WORK_TO_CLOSURE_ASSUMPTIONS:
            raise ValueError(
                "closure_search.work_to_closure_assumption must be one_to_one")
        if not isinstance(self.objective_profile, str) or not self.objective_profile.strip():
            raise ValueError("closure_search.objective_profile must be non-empty")
        if self.policy_status not in _POLICY_STATUSES:
            raise ValueError(
                f"closure_search.policy_status must be one of {sorted(_POLICY_STATUSES)}")
        if self.interday_policy not in _INTERDAY_POLICIES:
            raise ValueError(
                "closure_search.interday_policy must be continuous_v1 or "
                "independent_daily_reset_v1")
        if self.work_allocation_policy not in _WORK_ALLOCATION_POLICIES:
            raise ValueError(
                "closure_search.work_allocation_policy must be "
                "equal_daily_rounded_v1, exact_balanced_daily_v1, or "
                "exact_equal_daily_v1")
        if self.period_comparison_policy not in _PERIOD_COMPARISON_POLICIES:
            raise ValueError(
                "closure_search.period_comparison_policy must be none_v1 "
                "or rolling_period_v1"
            )
        if (
            self.period_comparison_policy == "rolling_period_v1"
            and self.interday_policy != "independent_daily_reset_v1"
        ):
            raise ValueError(
                "rolling-period comparison requires independent_daily_reset_v1"
            )
        if (
            self.period_comparison_policy == "rolling_period_v1"
            and self.work_allocation_policy != "exact_equal_daily_v1"
        ):
            raise ValueError(
                "rolling-period comparison requires exact_equal_daily_v1 "
                "so every workday has the same start and end time"
            )
        if (
            self.work_allocation_policy in {
                "exact_balanced_daily_v1", "exact_equal_daily_v1"
            }
            and self.interday_policy != "independent_daily_reset_v1"
        ):
            raise ValueError(
                "exact work allocation requires the independent "
                "daily reset policy")
        if (
            self.work_allocation_policy in {
                "exact_balanced_daily_v1", "exact_equal_daily_v1"
            }
            and self.required_work_minutes % self.resolution_minutes
        ):
            raise ValueError(
                "exact work must align to the 15-minute resolution")
        if (
            maximum_days > _CONTINUOUS_MAX_WORKDAYS
            and self.interday_policy != "independent_daily_reset_v1"
        ):
            raise ValueError(
                "continuous closure_search.max_consecutive_start_days must "
                "be from 1 through 21; more than 21 workdays requires "
                "independent_daily_reset_v1"
            )
        if (
            maximum_days > _CONTINUOUS_MAX_WORKDAYS
            and self.work_allocation_policy != "exact_equal_daily_v1"
        ):
            raise ValueError(
                "more than 21 workdays requires exact_equal_daily_v1"
            )
        if self.interday_policy == "independent_daily_reset_v1":
            band_start = _clock_minutes(
                self.permitted_daily_band.earliest_start
            )
            band_end = _clock_minutes(self.permitted_daily_band.latest_end)
            if band_end <= band_start:
                raise ValueError(
                    "independent daily reset requires a same-day permitted band")

    def _content_payload(self) -> dict[str, Any]:
        payload = {
            "directed_edges": sorted(self.directed_edges),
            "demand_build_id": self.demand_build_id,
            "source": self.source,
            "permitted_date_start": self.permitted_date_start,
            "permitted_date_end": self.permitted_date_end,
            "timezone": self.timezone,
            "dst_policy": self.dst_policy,
            "required_work_minutes": self.required_work_minutes,
            "max_consecutive_start_days": self.max_consecutive_start_days,
            "permitted_daily_band": self.permitted_daily_band.to_dict(),
            "allowed_weekdays": list(self.allowed_weekdays),
            "blackout_dates": list(self.blackout_dates),
            "same_daily_window": self.same_daily_window,
            "resolution_minutes": self.resolution_minutes,
            "closure_type": self.closure_type,
            "duration_basis": self.duration_basis,
            "work_to_closure_assumption": self.work_to_closure_assumption,
            "objective_profile": self.objective_profile,
            "policy_status": self.policy_status,
        }
        # Preserve every legacy content key.  These fields were introduced as
        # explicit opt-ins; serializing their defaults would relabel all
        # frozen continuous-search evidence despite identical semantics.
        if self.interday_policy != "continuous_v1":
            payload["interday_policy"] = self.interday_policy
        if self.work_allocation_policy != "equal_daily_rounded_v1":
            payload["work_allocation_policy"] = self.work_allocation_policy
        if self.period_comparison_policy != "none_v1":
            payload["period_comparison_policy"] = self.period_comparison_policy
        return payload

    @property
    def content_key(self) -> str:
        return _content_key(self._content_payload())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ClosureSearchSpec":
        if not isinstance(raw, Mapping):
            raise ValueError("closure_search_spec must be a JSON object")
        spec = cls(
            search_id=str(raw.get("search_id", "")),
            directed_edges=raw.get("directed_edges", ()),
            demand_build_id=str(raw.get("demand_build_id", "")),
            source=str(raw.get("source", "forecast")),
            permitted_date_start=str(raw.get("permitted_date_start", "")),
            permitted_date_end=str(raw.get("permitted_date_end", "")),
            timezone=str(raw.get("timezone", "Europe/Stockholm")),
            dst_policy=str(raw.get("dst_policy", "exclude_transition_dates")),
            required_work_minutes=raw.get("required_work_minutes"),
            max_consecutive_start_days=raw.get("max_consecutive_start_days"),
            permitted_daily_band=DailyTimeBand.from_dict(
                raw.get("permitted_daily_band", {})),
            allowed_weekdays=raw.get(
                "allowed_weekdays", (0, 1, 2, 3, 4, 5, 6)),
            blackout_dates=raw.get("blackout_dates", ()),
            same_daily_window=raw.get("same_daily_window", True),
            resolution_minutes=raw.get("resolution_minutes", 15),
            closure_type=str(raw.get("closure_type", "full")),
            duration_basis=str(raw.get(
                "duration_basis", "required_work_time")),
            work_to_closure_assumption=str(raw.get(
                "work_to_closure_assumption", "one_to_one")),
            objective_profile=str(raw.get(
                "objective_profile", "robust_time_loss")),
            policy_status=str(raw.get(
                "policy_status", "user_supplied_unverified")),
            interday_policy=str(raw.get(
                "interday_policy", "continuous_v1")),
            work_allocation_policy=str(raw.get(
                "work_allocation_policy", "equal_daily_rounded_v1")),
            period_comparison_policy=str(raw.get(
                "period_comparison_policy", "none_v1")),
        )
        supplied_key = raw.get("content_key")
        if supplied_key is not None and str(supplied_key) != spec.content_key:
            raise ValueError(
                "closure_search.content_key does not match the spec contents")
        return spec

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "closure_search",
            "search_id": self.search_id,
            **self._content_payload(),
            "content_key": self.content_key,
        }


@dataclass(frozen=True)
class ClosureInterval:
    """One exact local-time work/closure interval generated for a schedule."""

    work_date: str
    start_time: str
    end_time: str

    def __post_init__(self) -> None:
        _date(self.work_date, "closure_interval.work_date")
        _ordered(self.start_time, self.end_time, "closure_interval")
        start = _parse_time(self.start_time, "closure_interval.start_time")
        end = _parse_time(self.end_time, "closure_interval.end_time")
        if start.tzinfo is not None or end.tzinfo is not None:
            raise ValueError(
                "closure_interval times must be timezone-less local datetimes")
        if start.date().isoformat() != self.work_date:
            raise ValueError(
                "closure_interval.start_time must start on work_date")
        if any((start.second, start.microsecond, end.second, end.microsecond)):
            raise ValueError("closure_interval times must align to whole minutes")

    @property
    def duration_minutes(self) -> int:
        start = _parse_time(self.start_time, "closure_interval.start_time")
        end = _parse_time(self.end_time, "closure_interval.end_time")
        seconds = int((end - start).total_seconds())
        if seconds % 60:
            raise ValueError("closure_interval duration must use whole minutes")
        return seconds // 60

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ClosureInterval":
        if not isinstance(raw, Mapping):
            raise ValueError("closure_interval must be a JSON object")
        return cls(
            work_date=str(raw.get("work_date", "")),
            start_time=str(raw.get("start_time", "")),
            end_time=str(raw.get("end_time", "")),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ClosureSchedule:
    """One fully enumerated same-time recurring closure candidate."""

    schedule_id: str
    search_content_key: str
    first_work_date: str
    day_count: int
    daily_start: str
    daily_end: str
    required_work_minutes: int
    scheduled_work_minutes: int
    actual_closed_minutes: int
    rounding_overshoot_minutes: int
    intervals: tuple[ClosureInterval, ...]
    allocation_policy: str = "equal_daily_rounded_v1"

    def __post_init__(self) -> None:
        if (not isinstance(self.schedule_id, str) or not self.schedule_id
                or not _SAFE_KEY.fullmatch(self.schedule_id)):
            raise ValueError("closure_schedule.schedule_id must be a safe key")
        if (not isinstance(self.search_content_key, str)
                or not re.fullmatch(r"[0-9a-f]{20}", self.search_content_key)):
            raise ValueError(
                "closure_schedule.search_content_key must be a 20-character hex key")
        _date(self.first_work_date, "closure_schedule.first_work_date")
        day_count = _strict_int(self.day_count, "closure_schedule.day_count")
        if day_count <= 0 or len(self.intervals) != day_count:
            raise ValueError(
                "closure_schedule.intervals must contain exactly day_count entries")
        _clock(self.daily_start, "closure_schedule.daily_start")
        _clock(self.daily_end, "closure_schedule.daily_end")
        if self.daily_start == "24:00":
            raise ValueError("closure_schedule.daily_start cannot be 24:00")
        if (_clock_minutes(self.daily_start) % 15
                or _clock_minutes(self.daily_end) % 15):
            raise ValueError(
                "closure_schedule daily times must align to 15 minutes")

        required = _strict_int(
            self.required_work_minutes, "closure_schedule.required_work_minutes")
        scheduled = _strict_int(
            self.scheduled_work_minutes, "closure_schedule.scheduled_work_minutes")
        closed = _strict_int(
            self.actual_closed_minutes, "closure_schedule.actual_closed_minutes")
        overshoot = _strict_int(
            self.rounding_overshoot_minutes,
            "closure_schedule.rounding_overshoot_minutes",
        )
        if required <= 0 or scheduled < required:
            raise ValueError(
                "closure_schedule scheduled work must satisfy required work")
        if closed != scheduled:
            raise ValueError(
                "closure_schedule one-to-one work and closure minutes must match")
        if overshoot != scheduled - required:
            raise ValueError(
                "closure_schedule rounding overshoot does not match scheduled work")
        if scheduled % 15 or closed % 15:
            raise ValueError(
                "closure_schedule work and closure must align to 15 minutes")

        if self.allocation_policy not in _WORK_ALLOCATION_POLICIES:
            raise ValueError("closure_schedule allocation policy is unsupported")
        identity = {
            "search_content_key": self.search_content_key,
            "first_work_date": self.first_work_date,
            "day_count": self.day_count,
            "daily_start": self.daily_start,
            "daily_end": self.daily_end,
            "scheduled_work_minutes": self.scheduled_work_minutes,
        }
        if self.allocation_policy != "equal_daily_rounded_v1":
            identity.update({
                "allocation_policy": self.allocation_policy,
                "interval_durations_minutes": [
                    item.duration_minutes for item in self.intervals
                ],
                "work_dates": [item.work_date for item in self.intervals],
            })
        expected_schedule_id = "closure-" + _content_key(identity)
        if self.schedule_id != expected_schedule_id:
            raise ValueError(
                "closure_schedule.schedule_id does not match the schedule contents")

        first = date.fromisoformat(self.first_work_date)
        previous_date = None
        durations: set[int] = set()
        duration_total = 0
        end_labels: list[str] = []
        for index, interval in enumerate(self.intervals):
            interval_date = date.fromisoformat(interval.work_date)
            if self.allocation_policy == "equal_daily_rounded_v1":
                expected_date = first.fromordinal(first.toordinal() + index)
                if interval_date != expected_date:
                    raise ValueError(
                        "closure_schedule work dates must be consecutive")
            elif (
                (index == 0 and interval_date != first)
                or (previous_date is not None and interval_date <= previous_date)
            ):
                raise ValueError(
                    "exact work dates must be strictly increasing "
                    "from first_work_date"
                )
            previous_date = interval_date
            start = _parse_time(
                interval.start_time, "closure_schedule.interval.start_time")
            end = _parse_time(
                interval.end_time, "closure_schedule.interval.end_time")
            if _datetime_clock(start) != self.daily_start:
                raise ValueError(
                    "closure_schedule intervals must use the same daily start")
            end_label = (
                "24:00"
                if end.date() > start.date()
                and _datetime_clock(end) == "00:00"
                else _datetime_clock(end)
            )
            if (
                self.allocation_policy == "equal_daily_rounded_v1"
                and end_label != self.daily_end
            ):
                raise ValueError(
                    "closure_schedule intervals must use the same daily end")
            duration = int((end - start).total_seconds()) // 60
            if duration % 15:
                raise ValueError(
                    "closure_schedule interval durations must align to 15 minutes")
            durations.add(duration)
            duration_total += duration
            end_labels.append(end_label)
        if duration_total != closed:
            raise ValueError(
                "closure_schedule intervals must match total closure minutes")
        if (
            self.allocation_policy == "equal_daily_rounded_v1"
            and len(durations) != 1
        ):
            raise ValueError(
                "closure_schedule intervals must have equal durations matching closure")
        if self.allocation_policy in {
            "exact_balanced_daily_v1", "exact_equal_daily_v1"
        }:
            if scheduled != required or overshoot != 0:
                raise ValueError(
                    "exact schedules cannot overshoot required work")
            if (
                self.allocation_policy == "exact_balanced_daily_v1"
                and max(durations) - min(durations) > 15
            ):
                raise ValueError(
                    "exact balanced daily durations may differ by at most 15 minutes")
            if (
                self.allocation_policy == "exact_equal_daily_v1"
                and len(durations) != 1
            ):
                raise ValueError(
                    "exact equal daily schedules require identical durations"
                )
            # Compare clock labels, not absolute datetimes, because intervals
            # span consecutive work dates. Independent-day schedules are
            # contractually same-day, so ordinary HH:MM ordering is sound.
            latest_label = max(end_labels)
            if latest_label != self.daily_end:
                raise ValueError(
                    "exact schedule daily_end must be its latest daily end")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ClosureSchedule":
        if not isinstance(raw, Mapping):
            raise ValueError("closure_schedule must be a JSON object")
        return cls(
            schedule_id=str(raw.get("schedule_id", "")),
            search_content_key=str(raw.get("search_content_key", "")),
            first_work_date=str(raw.get("first_work_date", "")),
            day_count=raw.get("day_count"),
            daily_start=str(raw.get("daily_start", "")),
            daily_end=str(raw.get("daily_end", "")),
            required_work_minutes=raw.get("required_work_minutes"),
            scheduled_work_minutes=raw.get("scheduled_work_minutes"),
            actual_closed_minutes=raw.get("actual_closed_minutes"),
            rounding_overshoot_minutes=raw.get(
                "rounding_overshoot_minutes"),
            intervals=tuple(ClosureInterval.from_dict(item)
                            for item in raw.get("intervals", ())),
            allocation_policy=str(raw.get(
                "allocation_policy", "equal_daily_rounded_v1")),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "closure_schedule",
            "schedule_id": self.schedule_id,
            "search_content_key": self.search_content_key,
            "first_work_date": self.first_work_date,
            "day_count": self.day_count,
            "daily_start": self.daily_start,
            "daily_end": self.daily_end,
            "required_work_minutes": self.required_work_minutes,
            "scheduled_work_minutes": self.scheduled_work_minutes,
            "actual_closed_minutes": self.actual_closed_minutes,
            "rounding_overshoot_minutes": self.rounding_overshoot_minutes,
            "intervals": [interval.to_dict() for interval in self.intervals],
        }
        if self.allocation_policy != "equal_daily_rounded_v1":
            payload["allocation_policy"] = self.allocation_policy
        return payload


@dataclass(frozen=True)
class ClosureSpec:
    """One directed edge closure with an explicit active interval."""

    edge_id: str
    start_time: str
    end_time: str
    closure_type: str = "full"
    permitted_access_exceptions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _edge(self.edge_id, "closure.edge_id")
        _ordered(self.start_time, self.end_time, "closure")
        if not isinstance(self.closure_type, str) or not self.closure_type.strip():
            raise ValueError("closure.closure_type must be non-empty")
        object.__setattr__(
            self, "permitted_access_exceptions",
            _string_list(self.permitted_access_exceptions,
                         "closure.permitted_access_exceptions"),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ClosureSpec":
        # Accept the old API's ``begin``/``end`` names during migration.
        return cls(
            edge_id=_edge(str(raw.get("edge_id", "")), "closure.edge_id"),
            start_time=str(raw.get("start_time", raw.get("begin", ""))),
            end_time=str(raw.get("end_time", raw.get("end", ""))),
            closure_type=str(raw.get("closure_type", "full")),
            permitted_access_exceptions=tuple(
                raw.get("permitted_access_exceptions", raw.get("exceptions", ()))
            ),
        )


@dataclass(frozen=True)
class ScenarioSpec:
    """Exact inputs shared by normal, closure, and signal study tools."""

    scenario_id: str
    demand_build_id: str
    network_build_id: str
    start_time: str
    end_time: str
    closures: tuple[ClosureSpec, ...] = field(default_factory=tuple)
    simulation_mode: str = "meso"
    seed_set: tuple[int, ...] = field(default_factory=lambda: (1000, 1001, 1002))
    demand_variant_mapping: tuple[tuple[int, str], ...] = field(
        default_factory=lambda: ((1000, "q50"), (1001, "q10"), (1002, "q90"))
    )
    objective_profile: str = "default"
    signal_plan_id: str | None = None
    analysis_window: AnalysisWindow | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id.strip():
            raise ValueError("scenario_id must be non-empty")
        for field_name in ("demand_build_id", "network_build_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        _ordered(self.start_time, self.end_time, "scenario")
        _same_timezone_style([self.start_time, self.end_time], "scenario")
        if self.simulation_mode not in _MODES:
            raise ValueError(f"simulation_mode must be one of {sorted(_MODES)}")
        if not self.seed_set or any(
            isinstance(seed, bool) or not isinstance(seed, int) or seed < 0
            for seed in self.seed_set
        ):
            raise ValueError("seed_set must contain unique non-negative integers")
        if len(set(self.seed_set)) != len(self.seed_set):
            raise ValueError("seed_set must contain unique values")
        mapping = tuple((int(seed), str(variant))
                        for seed, variant in self.demand_variant_mapping)
        if {seed for seed, _ in mapping} != set(self.seed_set):
            raise ValueError("demand_variant_mapping must cover every seed exactly once")
        if len(mapping) != len(set(seed for seed, _ in mapping)):
            raise ValueError("demand_variant_mapping contains duplicate seeds")
        if any(variant not in _VARIANTS for _, variant in mapping):
            raise ValueError(f"demand variants must be one of {sorted(_VARIANTS)}")
        if not isinstance(self.objective_profile, str) or not self.objective_profile.strip():
            raise ValueError("objective_profile must be non-empty")
        scenario_start = _parse_time(self.start_time, "scenario.start")
        scenario_end = _parse_time(self.end_time, "scenario.end")
        closures = tuple(self.closures)
        if any(not isinstance(closure, ClosureSpec) for closure in closures):
            raise ValueError("scenario.closures must contain ClosureSpec values")
        object.__setattr__(self, "closures", closures)
        intervals_by_edge: dict[str, list[tuple[datetime, datetime]]] = {}
        for closure in closures:
            _same_timezone_style(
                [self.start_time, self.end_time,
                 closure.start_time, closure.end_time],
                "scenario.closures",
            )
            closure_start = _parse_time(closure.start_time, "closure.start")
            closure_end = _parse_time(closure.end_time, "closure.end")
            if closure_start < scenario_start or closure_end > scenario_end:
                raise ValueError(
                    f"closure {closure.edge_id} must fit within the scenario window"
                )
            intervals_by_edge.setdefault(closure.edge_id, []).append(
                (closure_start, closure_end))
        for edge_id, intervals in intervals_by_edge.items():
            intervals.sort()
            if any(current_start < previous_end
                   for (_, previous_end), (current_start, _)
                   in zip(intervals, intervals[1:])):
                raise ValueError(
                    f"closure intervals for {edge_id} must not overlap")
        if self.analysis_window is not None:
            _same_timezone_style(
                [self.start_time, self.end_time,
                 self.analysis_window.measure_start,
                 self.analysis_window.measure_end],
                "scenario.analysis_window",
            )
            measure_start = _parse_time(self.analysis_window.measure_start,
                                        "analysis_window.measure_start")
            measure_end = _parse_time(self.analysis_window.measure_end,
                                      "analysis_window.measure_end")
            if measure_start < scenario_start or measure_end > scenario_end:
                raise ValueError("analysis_window must fit within the scenario window")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ScenarioSpec":
        mapping = raw.get("demand_variant_mapping")
        if isinstance(mapping, Mapping):
            mapping = tuple((int(seed), str(variant))
                            for seed, variant in mapping.items())
        elif mapping is None:
            mapping = ((1000, "q50"), (1001, "q10"), (1002, "q90"))
        else:
            mapping = tuple((int(seed), str(variant)) for seed, variant in mapping)
        return cls(
            scenario_id=str(raw.get("scenario_id", "")),
            demand_build_id=str(raw.get("demand_build_id", "")),
            network_build_id=str(raw.get("network_build_id", "")),
            start_time=str(raw.get("start_time", "")),
            end_time=str(raw.get("end_time", "")),
            closures=tuple(ClosureSpec.from_dict(item)
                           for item in raw.get("closures", ())),
            simulation_mode=str(raw.get("simulation_mode", "meso")),
            seed_set=tuple(int(seed) for seed in raw.get(
                "seed_set", (seed for seed, _ in mapping))),
            demand_variant_mapping=mapping,
            objective_profile=str(raw.get("objective_profile", "default")),
            signal_plan_id=(str(raw["signal_plan_id"])
                            if raw.get("signal_plan_id") is not None else None),
            analysis_window=(AnalysisWindow.from_dict(raw["analysis_window"])
                             if raw.get("analysis_window") is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schema_version"] = SCHEMA_VERSION
        result["demand_variant_mapping"] = {
            str(seed): variant for seed, variant in self.demand_variant_mapping
        }
        return result


@dataclass(frozen=True)
class DecisionResult:
    """Common result envelope for closure and signal decisions."""

    result_id: str
    scenario_id: str
    status: str
    provenance: str
    alternatives: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    objective_metrics: Mapping[str, Any] = field(default_factory=dict)
    uncertainty: Mapping[str, Any] = field(default_factory=dict)
    gates: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.result_id or not self.scenario_id:
            raise ValueError("result_id and scenario_id must be non-empty")
        if self.status not in {"recommended", "screening_only", "no_valid_option",
                               "inconclusive", "failed"}:
            raise ValueError("unsupported decision result status")
        if not self.provenance:
            raise ValueError("provenance must be explicit")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schema_version"] = SCHEMA_VERSION
        return result


def load_scenario_spec(path: Path) -> ScenarioSpec:
    """Load and validate one spec before a simulation process is started."""
    with Path(path).open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("unsupported ScenarioSpec schema_version")
    return ScenarioSpec.from_dict(raw)


def write_scenario_spec(path: Path, spec: ScenarioSpec) -> None:
    """Write a validated spec atomically so readers never see partial JSON."""
    spec = ScenarioSpec.from_dict(spec.to_dict())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(spec.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_demand_build_spec(path: Path) -> DemandBuildSpec:
    """Load and validate a demand contract before expensive calibration starts."""
    with Path(path).open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("unsupported DemandBuildSpec schema_version")
    if raw.get("kind", "demand_build") != "demand_build":
        raise ValueError("spec is not a demand_build contract")
    return DemandBuildSpec.from_dict(raw)


def write_demand_build_spec(path: Path, spec: DemandBuildSpec) -> None:
    """Write a validated demand contract atomically."""
    spec = DemandBuildSpec.from_dict(spec.to_dict())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(spec.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_closure_search_spec(path: Path) -> ClosureSearchSpec:
    """Load and validate one recurring closure-search contract."""
    with Path(path).open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("unsupported ClosureSearchSpec schema_version")
    if raw.get("kind", "closure_search") != "closure_search":
        raise ValueError("spec is not a closure_search contract")
    return ClosureSearchSpec.from_dict(raw)


def write_closure_search_spec(path: Path, spec: ClosureSearchSpec) -> None:
    """Write a validated closure-search contract atomically."""
    spec = ClosureSearchSpec.from_dict(spec.to_dict())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(spec.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
