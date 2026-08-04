"""Continuous simulation envelopes and post-closure recovery evidence.

The calendar search decides *when the road is closed*.  This module decides
how much normal traffic must be simulated before and after those recurring
intervals, and evaluates recovery against the matched no-closure edgeData
series.  It deliberately uses per-interval ``timeLoss`` rather than SUMO's
coarse network-wide halting count.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from traffic_sim.core.closure_calendar import (
    expand_schedule_closures,
    is_dst_transition_date,
)
from traffic_sim.core.contracts import (
    AnalysisWindow,
    ClosureSchedule,
    ClosureSearchSpec,
    DemandBuildSpec,
    ScenarioSpec,
)


# A closure schedule plus warm-up and recovery must fit one demand archive.
# 24 covers a 21-consecutive-day (3-week) closure; kept in step with
# contracts._DEMAND_PURPOSE_MAX_DAYS["closure_envelope"].
MAX_CLOSURE_ENVELOPE_DAYS = 24


@dataclass(frozen=True)
class EnvelopePolicy:
    """Versioned bounds for warm-up and matched-baseline recovery."""

    interval_s: int = 900
    minimum_warmup_s: int = 3600
    warmup_margin_s: int = 900
    requested_recovery_cap_s: int = 6 * 3600
    recovery_sustain_s: int = 1800
    recovery_relative_tolerance: float = 0.10
    recovery_absolute_time_loss_tolerance_s: float = 60.0

    def __post_init__(self) -> None:
        integer_fields = (
            "interval_s",
            "minimum_warmup_s",
            "warmup_margin_s",
            "requested_recovery_cap_s",
            "recovery_sustain_s",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"envelope_policy.{name} must be a positive integer")
        if self.interval_s != 900:
            raise ValueError("envelope_policy.interval_s must be 900")
        if self.recovery_sustain_s % self.interval_s:
            raise ValueError(
                "envelope_policy.recovery_sustain_s must align to interval_s")
        if (
            not math.isfinite(self.recovery_relative_tolerance)
            or self.recovery_relative_tolerance < 0
        ):
            raise ValueError(
                "envelope_policy.recovery_relative_tolerance must be finite "
                "and non-negative")
        if (
            not math.isfinite(self.recovery_absolute_time_loss_tolerance_s)
            or self.recovery_absolute_time_loss_tolerance_s < 0
        ):
            raise ValueError(
                "envelope_policy absolute time-loss tolerance must be finite "
                "and non-negative")


@dataclass(frozen=True)
class SimulationEnvelope:
    """Exact continuous demand/scenario range around one closure schedule."""

    schedule_id: str
    timezone: str
    scenario_start: str
    scenario_end: str
    closure_start: str
    closure_end: str
    demand_days: int
    warmup_required_s: int
    warmup_actual_s: int
    recovery_cap_actual_s: int
    recovery_sustain_s: int
    interval_s: int

    def __post_init__(self) -> None:
        if self.timezone != "Europe/Stockholm":
            raise ValueError("simulation_envelope.timezone must be Europe/Stockholm")
        values = {
            name: datetime.fromisoformat(getattr(self, name))
            for name in (
                "scenario_start", "scenario_end",
                "closure_start", "closure_end",
            )
        }
        if any(value.tzinfo is not None for value in values.values()):
            raise ValueError(
                "simulation_envelope uses timezone-less local datetimes")
        if not (
            values["scenario_start"]
            < values["closure_start"]
            < values["closure_end"]
            < values["scenario_end"]
        ):
            raise ValueError(
                "simulation_envelope times must be strictly ordered")
        if (
            values["scenario_start"].time() != time.min
            or values["scenario_end"].time() != time.min
        ):
            raise ValueError(
                "simulation_envelope scenario boundaries must be local midnight")
        actual_days = (
            values["scenario_end"].date() - values["scenario_start"].date()
        ).days
        if (
            isinstance(self.demand_days, bool)
            or not isinstance(self.demand_days, int)
            or self.demand_days != actual_days
            or not 1 <= self.demand_days <= MAX_CLOSURE_ENVELOPE_DAYS
        ):
            raise ValueError(
                "simulation_envelope demand_days must match a "
                f"1–{MAX_CLOSURE_ENVELOPE_DAYS} day range")
        expected_warmup = int(
            (values["closure_start"] - values["scenario_start"]).total_seconds())
        expected_recovery = int(
            (values["scenario_end"] - values["closure_end"]).total_seconds())
        if self.warmup_actual_s != expected_warmup:
            raise ValueError("simulation_envelope warmup_actual_s is inconsistent")
        if self.warmup_actual_s < self.warmup_required_s:
            raise ValueError("simulation_envelope does not satisfy required warm-up")
        if self.recovery_cap_actual_s != expected_recovery:
            raise ValueError(
                "simulation_envelope recovery_cap_actual_s is inconsistent")
        if (
            self.interval_s != 900
            or self.recovery_sustain_s <= 0
            or self.recovery_sustain_s % self.interval_s
        ):
            raise ValueError(
                "simulation_envelope recovery must use aligned 15-minute buckets")

    @property
    def analysis_window(self) -> AnalysisWindow:
        return AnalysisWindow(
            warmup_s=self.warmup_actual_s,
            measure_start=self.closure_start,
            measure_end=self.closure_end,
            drain_s=self.recovery_cap_actual_s,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _floor_midnight(value: datetime) -> datetime:
    return datetime.combine(value.date(), time.min)


def _ceil_midnight(value: datetime) -> datetime:
    beginning = _floor_midnight(value)
    return beginning if value == beginning else beginning + timedelta(days=1)


def _ceil_multiple(value: int, unit: int) -> int:
    return ((value + unit - 1) // unit) * unit


def build_simulation_envelope(
    spec: ClosureSearchSpec,
    schedule: ClosureSchedule,
    *,
    baseline_trip_duration_p99_s: int,
    policy: EnvelopePolicy = EnvelopePolicy(),
) -> SimulationEnvelope:
    """Create a full-day demand envelope with derived warm-up and drain cap."""
    if schedule.search_content_key != spec.content_key:
        raise ValueError("closure schedule does not belong to this search spec")
    if (
        isinstance(baseline_trip_duration_p99_s, bool)
        or not isinstance(baseline_trip_duration_p99_s, int)
        or baseline_trip_duration_p99_s <= 0
    ):
        raise ValueError(
            "baseline_trip_duration_p99_s must be a positive integer")

    starts = [datetime.fromisoformat(item.start_time)
              for item in schedule.intervals]
    ends = [datetime.fromisoformat(item.end_time)
            for item in schedule.intervals]
    closure_start, closure_end = min(starts), max(ends)
    warmup_required = max(
        policy.minimum_warmup_s,
        _ceil_multiple(
            baseline_trip_duration_p99_s + policy.warmup_margin_s,
            policy.interval_s,
        ),
    )
    scenario_start = _floor_midnight(
        closure_start - timedelta(seconds=warmup_required))
    scenario_end = _ceil_midnight(
        closure_end + timedelta(seconds=policy.requested_recovery_cap_s))
    demand_days = (scenario_end.date() - scenario_start.date()).days
    if demand_days > MAX_CLOSURE_ENVELOPE_DAYS:
        raise ValueError(
            f"closure schedule needs a {demand_days}-day simulation envelope; "
            f"the validated maximum is {MAX_CLOSURE_ENVELOPE_DAYS}")

    day = scenario_start.date()
    while day < scenario_end.date():
        if (
            spec.dst_policy == "exclude_transition_dates"
            and is_dst_transition_date(day, spec.timezone)
        ):
            raise ValueError(
                f"simulation envelope crosses excluded DST transition date {day}")
        day += timedelta(days=1)

    return SimulationEnvelope(
        schedule_id=schedule.schedule_id,
        timezone=spec.timezone,
        scenario_start=scenario_start.isoformat(timespec="seconds"),
        scenario_end=scenario_end.isoformat(timespec="seconds"),
        closure_start=closure_start.isoformat(timespec="seconds"),
        closure_end=closure_end.isoformat(timespec="seconds"),
        demand_days=demand_days,
        warmup_required_s=warmup_required,
        warmup_actual_s=int((closure_start - scenario_start).total_seconds()),
        recovery_cap_actual_s=int((scenario_end - closure_end).total_seconds()),
        recovery_sustain_s=policy.recovery_sustain_s,
        interval_s=policy.interval_s,
    )


def envelope_demand_spec(
    search: ClosureSearchSpec,
    envelope: SimulationEnvelope,
    *,
    structural_reference_date: str = "2025-09-16",
) -> DemandBuildSpec:
    """Demand contract for an isolated closure envelope (up to 3 weeks)."""
    return DemandBuildSpec(
        start_date=envelope.scenario_start[:10],
        source=search.source,
        days=envelope.demand_days,
        begin="00:00",
        end="24:00",
        structural_reference_date=structural_reference_date,
        purpose="closure_envelope",
    )


def independent_daily_demand_spec(
    search: ClosureSearchSpec,
    schedule: ClosureSchedule,
    envelope: SimulationEnvelope,
    *,
    structural_reference_date: str = "2025-09-16",
) -> DemandBuildSpec:
    """Return the canonical reusable archive for one independent work day.

    Ordinary dates use the three-day ``previous/current/next`` archive.  That
    single route identity contains every legal full-day closure envelope for
    the work date, so changing only the closure start/end no longer triggers a
    fresh demand calibration.  At source-year and excluded-DST boundaries the
    canonical range may be unavailable; there we retain the exact minimal
    envelope rather than inventing demand or unnecessarily rejecting a valid
    schedule.
    """
    if search.interday_policy != "independent_daily_reset_v1":
        raise ValueError(
            "canonical daily demand requires independent_daily_reset_v1"
        )
    if schedule.day_count != 1 or len(schedule.intervals) != 1:
        raise ValueError("canonical daily demand requires one daily interval")
    if schedule.schedule_id != envelope.schedule_id:
        raise ValueError("daily demand envelope belongs to another schedule")

    work_date = date.fromisoformat(schedule.intervals[0].work_date)
    source_year = 2027 if search.source == "forecast" else 2025
    source_start = date(source_year, 1, 1)
    source_end = date(source_year + 1, 1, 1)
    envelope_start = date.fromisoformat(envelope.scenario_start[:10])
    envelope_end = date.fromisoformat(envelope.scenario_end[:10])
    if envelope_start < source_start or envelope_end > source_end:
        raise ValueError(
            "exact independent daily envelope lies outside the downloaded "
            f"{source_year} {search.source} demand year"
        )

    canonical_start = max(source_start, work_date - timedelta(days=1))
    canonical_end = min(source_end, work_date + timedelta(days=2))
    canonical_dates = (
        canonical_start + timedelta(days=offset)
        for offset in range((canonical_end - canonical_start).days)
    )
    canonical_crosses_dst = any(
        search.dst_policy == "exclude_transition_dates"
        and is_dst_transition_date(day, search.timezone)
        for day in canonical_dates
    )
    if (
        canonical_start <= envelope_start
        and canonical_end >= envelope_end
        and not canonical_crosses_dst
    ):
        return DemandBuildSpec(
            start_date=canonical_start.isoformat(),
            source=search.source,
            days=(canonical_end - canonical_start).days,
            begin="00:00",
            end="24:00",
            structural_reference_date=structural_reference_date,
            purpose="closure_envelope",
        )
    return envelope_demand_spec(
        search,
        envelope,
        structural_reference_date=structural_reference_date,
    )


def envelope_scenario_spec(
    search: ClosureSearchSpec,
    schedule: ClosureSchedule,
    envelope: SimulationEnvelope,
    *,
    demand_build_id: str,
    network_build_id: str,
    seed_set: tuple[int, ...] = (1000, 1001, 1002),
    demand_variant_mapping: tuple[tuple[int, str], ...] = (
        (1000, "q50"), (1001, "q10"), (1002, "q90")),
) -> ScenarioSpec:
    """Project one generated schedule into the existing SUMO ScenarioSpec."""
    if schedule.schedule_id != envelope.schedule_id:
        raise ValueError("simulation envelope belongs to another schedule")
    return ScenarioSpec(
        scenario_id=schedule.schedule_id,
        demand_build_id=demand_build_id,
        network_build_id=network_build_id,
        start_time=envelope.scenario_start,
        end_time=envelope.scenario_end,
        closures=expand_schedule_closures(search, schedule),
        simulation_mode="meso",
        seed_set=seed_set,
        demand_variant_mapping=demand_variant_mapping,
        objective_profile=search.objective_profile,
        analysis_window=envelope.analysis_window,
    )


@dataclass(frozen=True)
class RecoveryBucket:
    begin_s: int
    end_s: int
    time_loss_s: float


@dataclass(frozen=True)
class RecoveryResult:
    status: str
    recovered: bool
    recovered_at_s: int | None
    recovery_time_s: int | None
    evaluated_buckets: int
    required_consecutive_buckets: int
    max_excess_time_loss_s: float | None

    def __post_init__(self) -> None:
        if self.status not in {
            "recovered", "congestion_not_dissipated", "insufficient_evidence"
        }:
            raise ValueError("unsupported recovery status")
        if self.recovered != (self.status == "recovered"):
            raise ValueError("recovery status and recovered flag disagree")

    def to_dict(self) -> dict:
        return asdict(self)


def read_edgedata_time_loss(
    path: Path,
    *,
    affected_edges: Iterable[str] | None = None,
) -> tuple[RecoveryBucket, ...]:
    """Aggregate SUMO edgeData timeLoss per interval for the affected area."""
    selected = set(affected_edges) if affected_edges is not None else None
    root = ET.parse(path).getroot()
    buckets: list[RecoveryBucket] = []
    for interval in root.iter("interval"):
        try:
            begin = float(interval.get("begin"))
            end = float(interval.get("end"))
        except (TypeError, ValueError) as exc:
            raise ValueError("edgeData interval has invalid begin/end") from exc
        if (
            not math.isfinite(begin)
            or not math.isfinite(end)
            or not begin.is_integer()
            or not end.is_integer()
            or end <= begin
        ):
            raise ValueError("edgeData interval has invalid begin/end")
        total = 0.0
        for edge in interval.findall("edge"):
            if selected is not None and edge.get("id") not in selected:
                continue
            try:
                value = float(edge.get("timeLoss", "0"))
            except (TypeError, ValueError) as exc:
                raise ValueError("edgeData edge has invalid timeLoss") from exc
            if not math.isfinite(value) or value < 0:
                raise ValueError("edgeData edge has invalid timeLoss")
            total += value
        buckets.append(RecoveryBucket(int(begin), int(end), total))
    return tuple(buckets)


def evaluate_recovery(
    baseline: Sequence[RecoveryBucket],
    candidate: Sequence[RecoveryBucket],
    *,
    closure_end_s: int,
    recovery_cap_end_s: int,
    policy: EnvelopePolicy = EnvelopePolicy(),
) -> RecoveryResult:
    """Find sustained return to the matched baseline after the final shift."""
    if (
        isinstance(closure_end_s, bool)
        or not isinstance(closure_end_s, int)
        or isinstance(recovery_cap_end_s, bool)
        or not isinstance(recovery_cap_end_s, int)
        or recovery_cap_end_s <= closure_end_s
    ):
        raise ValueError("recovery bounds must be increasing integer seconds")
    baseline_by_interval = {
        (item.begin_s, item.end_s): item for item in baseline}
    candidate_by_interval = {
        (item.begin_s, item.end_s): item for item in candidate}
    first_begin = _ceil_multiple(closure_end_s, policy.interval_s)
    required = policy.recovery_sustain_s // policy.interval_s
    consecutive = 0
    evaluated = 0
    max_excess: float | None = None

    for begin in range(
        first_begin,
        recovery_cap_end_s - policy.interval_s + 1,
        policy.interval_s,
    ):
        key = (begin, begin + policy.interval_s)
        baseline_bucket = baseline_by_interval.get(key)
        candidate_bucket = candidate_by_interval.get(key)
        if baseline_bucket is None or candidate_bucket is None:
            return RecoveryResult(
                status="insufficient_evidence",
                recovered=False,
                recovered_at_s=None,
                recovery_time_s=None,
                evaluated_buckets=evaluated,
                required_consecutive_buckets=required,
                max_excess_time_loss_s=max_excess,
            )
        evaluated += 1
        threshold = (
            baseline_bucket.time_loss_s
            * (1 + policy.recovery_relative_tolerance)
            + policy.recovery_absolute_time_loss_tolerance_s
        )
        excess = candidate_bucket.time_loss_s - threshold
        max_excess = excess if max_excess is None else max(max_excess, excess)
        consecutive = consecutive + 1 if excess <= 0 else 0
        if consecutive >= required:
            recovered_at = begin + policy.interval_s
            return RecoveryResult(
                status="recovered",
                recovered=True,
                recovered_at_s=recovered_at,
                recovery_time_s=recovered_at - closure_end_s,
                evaluated_buckets=evaluated,
                required_consecutive_buckets=required,
                max_excess_time_loss_s=max_excess,
            )
    return RecoveryResult(
        status="congestion_not_dissipated",
        recovered=False,
        recovered_at_s=None,
        recovery_time_s=None,
        evaluated_buckets=evaluated,
        required_consecutive_buckets=required,
        max_excess_time_loss_s=max_excess,
    )
