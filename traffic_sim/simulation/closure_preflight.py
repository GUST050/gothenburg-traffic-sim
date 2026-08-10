"""Exact, read-only size estimate for a rolling closure search.

Step 1 / PR B of `docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`.

WHY THIS EXISTS.  A valid six-month, 360-hour search enumerates 11,813 parent
schedules and 23,349 unique daily units and is then refused by the 10,000-unit
cap — after the user has already waited for the enumeration.  The refusal is
correct and fail-closed, but it arrives late and says nothing the user can act
on.  This module answers the same question BEFORE anything starts, exactly, and
without materializing a single :class:`ClosureSchedule`.

HOW IT COUNTS WITHOUT ENUMERATING.  `generate_closure_schedules` walks
(first work date x day count x start minute) and keeps a schedule when every
calendar date its intervals occupy is allowed.  Under a single-duration
allocation policy the validity of a window depends only on which eligible dates
are USABLE at that start minute, so the count of valid windows of length ``n``
is a run-length question:

    a maximal run of ``L`` consecutive usable dates contains
    ``max(0, L - n + 1)`` windows of length ``n``.

That replaces an O(schedules x day_count) object walk with an O(day counts x
start minutes x eligible dates) integer scan.  On the plan's own six-month
cases that is roughly forty (day-count, start) pairs over ~130 eligible dates.

EXACTNESS IS THE WHOLE POINT.  A preflight that is merely close would be worse
than none: the user would plan around a number the search then contradicts.
Every count here is differential-tested against the real generator and the real
decomposition in `tests/test_closure_preflight.py`, including year boundaries,
leap years, DST transitions, blackout dates and an overnight band.

WHAT IT DELIBERATELY DOES NOT DO.  It builds no demand, starts no SUMO, writes
no artifact and creates no job.  It cannot therefore resolve the daily backend
identity that the result cache is keyed on — that identity only exists after
`MonthlyDemandResolverRunner.prepare()` has resolved (and possibly built) the
demand archive.  Cache figures are consequently reported as ``cache_unknown``
unless the caller supplies already-known identities, and "unknown" is reported
as such rather than guessed as a miss.  A false "0 cached" would make every
estimate look worse than reality; a false "all cached" would make it look
better.  Neither is acceptable, so the third state is explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from traffic_sim.core.closure_calendar import is_dst_transition_date
from traffic_sim.core.contracts import ClosureSearchSpec
from traffic_sim.simulation.envelope import (
    MAX_CLOSURE_ENVELOPE_DAYS, EnvelopePolicy)

#: Versioned contract name. Any change to a reported quantity's meaning must
#: bump this, because callers persist and compare these records.
PREFLIGHT_SCHEMA = "closure_search_preflight_v1"

#: Allocation policies whose day durations form ONE pattern per day count, so
#: the run-length identity above holds exactly. `exact_balanced_daily_v1`
#: creates up to 4096 distinct duration patterns whose validity varies by
#: position inside the window; counting it exactly is a different algorithm and
#: is deliberately refused rather than approximated.
SUPPORTED_ALLOCATION_POLICIES = frozenset({
    "exact_equal_daily_v1",
    "equal_daily_rounded_v1",
})

MINUTES_PER_DAY = 24 * 60

#: The hard caps `run_monthly_closure_search.py` enforces. Preflight REPORTS
#: against them and never changes them: PR B is explicitly forbidden from
#: raising or bypassing the limit merely because an estimate now exists.
DEFAULT_PARENT_SCHEDULE_LIMIT = 100_000
DEFAULT_DAILY_UNIT_LIMIT = 10_000

#: A search inside the caps but past half of one of them is "large but
#: runnable". Stated as a rule rather than a tuned constant: half of a hard cap
#: is the point at which a modest widening of the date range can push the
#: search over. The plan's runnable 720 h case (5,676 units) lands here, which
#: is the intended reading — it runs, and the user should know it is big.
LARGE_FRACTION_OF_LIMIT = 0.5

SIZE_NORMAL = "normal"
SIZE_LARGE_BUT_RUNNABLE = "large_but_runnable"
SIZE_OVER_RESOURCE_BUDGET = "over_resource_budget"


class UnsupportedPreflightSpec(ValueError):
    """Raised when exact counting is not implemented for this contract.

    Deliberately an error rather than an approximate answer. The endpoint
    turns it into a 4xx with the reason; nothing downstream ever sees a number
    that was estimated when it claimed to be exact.
    """


@dataclass(frozen=True)
class ClosureSearchPreflight:
    """Exact size of one closure search, computed without enumerating it."""

    schema: str
    search_id: str
    search_content_key: str
    permitted_date_start: str
    permitted_date_end: str
    eligible_workday_count: int
    valid_workday_counts: tuple[int, ...]
    parent_schedules_by_workday_count: Mapping[int, int]
    parent_schedule_count: int
    unique_daily_unit_count: int
    units_outside_demand_year: int
    executable_daily_unit_count: int
    cache_hits: int
    cache_misses: int
    cache_unknown: int
    cache_basis: str
    estimated_sumo_daily_units: int
    estimation_basis: str
    parent_schedule_limit: int
    daily_unit_limit: int
    size_class: str
    limit_reasons: tuple[str, ...]
    demand_year: int
    counting_method: str
    exact: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "exact": self.exact,
            "search_id": self.search_id,
            "search_content_key": self.search_content_key,
            "permitted_date_start": self.permitted_date_start,
            "permitted_date_end": self.permitted_date_end,
            "eligible_workday_count": self.eligible_workday_count,
            "valid_workday_counts": list(self.valid_workday_counts),
            "parent_schedules_by_workday_count": {
                str(key): value
                for key, value in sorted(
                    self.parent_schedules_by_workday_count.items())
            },
            "parent_schedule_count": self.parent_schedule_count,
            "unique_daily_unit_count": self.unique_daily_unit_count,
            "units_outside_demand_year": self.units_outside_demand_year,
            "executable_daily_unit_count": self.executable_daily_unit_count,
            "cache": {
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "unknown": self.cache_unknown,
                "basis": self.cache_basis,
            },
            "estimated_sumo_daily_units": self.estimated_sumo_daily_units,
            "estimation_basis": self.estimation_basis,
            "limits": {
                "parent_schedule_limit": self.parent_schedule_limit,
                "daily_unit_limit": self.daily_unit_limit,
            },
            "size_class": self.size_class,
            "limit_reasons": list(self.limit_reasons),
            "demand_year": self.demand_year,
            "counting_method": self.counting_method,
            "notes": list(self.notes),
        }


def _clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _calendar_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def eligible_dates(spec: ClosureSearchSpec) -> tuple[date, ...]:
    """Dates the calendar contract allows work to START on.

    Byte-for-byte the same rule `generate_closure_schedules` applies when it
    builds `allowed_dates`: inside the permitted range, an allowed weekday, not
    a blackout date and — under the default DST policy — not a transition date.
    """
    first = _calendar_date(spec.permitted_date_start)
    last = _calendar_date(spec.permitted_date_end)
    blackout = set(spec.blackout_dates)
    allowed_weekdays = set(spec.allowed_weekdays)
    exclude_dst = spec.dst_policy == "exclude_transition_dates"
    out: list[date] = []
    day = first
    while day <= last:
        if (
            day.weekday() in allowed_weekdays
            and day.isoformat() not in blackout
            and not (exclude_dst and is_dst_transition_date(day, spec.timezone))
        ):
            out.append(day)
        day += timedelta(days=1)
    return tuple(out)


def _start_offsets(spec: ClosureSearchSpec, duration_minutes: int) -> tuple[int, ...]:
    """Mirror of `closure_calendar._start_offsets`, including the wrap rule."""
    start = _clock_minutes(spec.permitted_daily_band.earliest_start)
    end = _clock_minutes(spec.permitted_daily_band.latest_end)
    if end <= start:
        end += MINUTES_PER_DAY
    last_start = end - duration_minutes
    if last_start < start:
        return ()
    return tuple(
        offset % MINUTES_PER_DAY
        for offset in range(start, last_start + 1, spec.resolution_minutes)
    )


def _daily_duration(spec: ClosureSearchSpec, day_count: int) -> int | None:
    """Per-day closure minutes for one day count, or None when illegal.

    Reproduces the allocation arithmetic of `generate_closure_schedules`
    exactly, including the two rejections it performs: a non-integral equal
    split, and a multi-day schedule whose daily shift would fill a whole day
    and leave no opening between passes.
    """
    if spec.work_allocation_policy == "exact_equal_daily_v1":
        total_blocks = spec.required_work_minutes // spec.resolution_minutes
        daily_blocks, remainder = divmod(total_blocks, day_count)
        if daily_blocks <= 0 or remainder:
            return None
        duration = daily_blocks * spec.resolution_minutes
    else:                                       # equal_daily_rounded_v1
        denominator = spec.resolution_minutes * day_count
        quarters = (spec.required_work_minutes + denominator - 1) // denominator
        duration = quarters * spec.resolution_minutes
    if duration <= 0:
        return None
    if day_count > 1 and duration >= MINUTES_PER_DAY:
        return None
    return duration


def _extra_occupied_days(start_minute: int, duration_minutes: int) -> int:
    """Calendar days after the work date that one interval also occupies.

    `_occupied_dates` uses the half-open interval, so a closure ending exactly
    at midnight does NOT occupy the following date. Integer arithmetic with the
    ``- 1`` reproduces that boundary rather than approximating it.
    """
    return (start_minute + duration_minutes - 1) // MINUTES_PER_DAY


def _usable_flags(dates: Sequence[date], allowed: frozenset[date],
                  extra_days: int) -> list[bool]:
    """Which candidate dates can carry an interval spilling `extra_days` on."""
    if extra_days == 0:
        return [True] * len(dates)
    return [
        all((day + timedelta(days=offset)) in allowed
            for offset in range(1, extra_days + 1))
        for day in dates
    ]


def _run_lengths(flags: Sequence[bool]) -> list[tuple[int, int]]:
    """Maximal runs of True as ``(start_index, length)`` pairs."""
    runs: list[tuple[int, int]] = []
    index = 0
    total = len(flags)
    while index < total:
        if not flags[index]:
            index += 1
            continue
        start = index
        while index < total and flags[index]:
            index += 1
        runs.append((start, index - start))
    return runs


def _calendar_window_dates(spec: ClosureSearchSpec) -> tuple[date, ...]:
    """Every calendar date the permitted range covers, allowed or not.

    `equal_daily_rounded_v1` walks calendar-consecutive dates rather than
    eligible-index-consecutive ones, so its windows live on this axis.
    """
    first = _calendar_date(spec.permitted_date_start)
    last = _calendar_date(spec.permitted_date_end)
    return tuple(first + timedelta(days=offset)
                 for offset in range((last - first).days + 1))


@dataclass(frozen=True)
class _EnvelopeShape:
    """Date-relative envelope extent shared by every unit of one group.

    Warm-up and recovery are fixed offsets from the closure clock, so two units
    that differ only in their work DATE have identically shaped envelopes. The
    shape is therefore computed once per (start minute, duration) group and the
    per-unit check becomes two date comparisons plus a DST scan.
    """

    days_before: int
    days_after: int
    demand_days: int
    valid: bool


def _envelope_shape(start_minute: int, duration_minutes: int,
                    *, baseline_trip_duration_p99_s: int,
                    policy: EnvelopePolicy) -> _EnvelopeShape:
    """Mirror `build_simulation_envelope` for one single-interval unit."""
    anchor = date(2001, 1, 8)          # arbitrary reference; offsets are exact
    closure_start = datetime.combine(anchor, time.min) + timedelta(
        minutes=start_minute)
    closure_end = closure_start + timedelta(minutes=duration_minutes)
    warmup_required = max(
        policy.minimum_warmup_s,
        -(-(baseline_trip_duration_p99_s + policy.warmup_margin_s)
          // policy.interval_s) * policy.interval_s,
    )
    scenario_start = closure_start - timedelta(seconds=warmup_required)
    scenario_start = datetime.combine(scenario_start.date(), time.min)
    scenario_end = closure_end + timedelta(seconds=policy.requested_recovery_cap_s)
    if scenario_end != datetime.combine(scenario_end.date(), time.min):
        scenario_end = datetime.combine(
            scenario_end.date() + timedelta(days=1), time.min)
    demand_days = (scenario_end.date() - scenario_start.date()).days
    return _EnvelopeShape(
        days_before=(anchor - scenario_start.date()).days,
        days_after=(scenario_end.date() - anchor).days,
        demand_days=demand_days,
        valid=1 <= demand_days <= MAX_CLOSURE_ENVELOPE_DAYS,
    )


def _demand_year(spec: ClosureSearchSpec) -> int:
    """The downloaded demand year, exactly as the search runner derives it."""
    return 2027 if spec.source == "forecast" else 2025


def _dst_transition_dates(spec: ClosureSearchSpec, first: date,
                          last: date) -> frozenset[date]:
    """Transition dates in a bounded window, computed once and reused."""
    if spec.dst_policy != "exclude_transition_dates":
        return frozenset()
    out = set()
    day = first
    while day <= last:
        if is_dst_transition_date(day, spec.timezone):
            out.add(day)
        day += timedelta(days=1)
    return frozenset(out)


def preflight(
    spec: ClosureSearchSpec,
    *,
    baseline_trip_duration_p99_s: int = 3600,
    envelope_policy: EnvelopePolicy | None = None,
    parent_schedule_limit: int = DEFAULT_PARENT_SCHEDULE_LIMIT,
    daily_unit_limit: int = DEFAULT_DAILY_UNIT_LIMIT,
    cache_root: Path | None = None,
    unit_cache_keys: Mapping[tuple[str, str, str, int], str] | None = None,
) -> ClosureSearchPreflight:
    """Exactly size one search without building a single ClosureSchedule.

    ``unit_cache_keys`` maps ``(work_date, start_time, end_time, duration)`` to
    the content-addressed cache key a prepared runner would use. It is supplied
    only when a caller already knows the daily backend identity; the read-only
    API never does, and then every unit is reported as ``cache_unknown``.
    """
    if not isinstance(spec, ClosureSearchSpec):
        raise TypeError("spec must be a ClosureSearchSpec")
    if spec.work_allocation_policy not in SUPPORTED_ALLOCATION_POLICIES:
        raise UnsupportedPreflightSpec(
            f"exact preflight is not implemented for work allocation policy "
            f"{spec.work_allocation_policy!r}; supported policies are "
            f"{sorted(SUPPORTED_ALLOCATION_POLICIES)}")
    policy = envelope_policy or EnvelopePolicy()

    allowed = frozenset(eligible_dates(spec))
    eligible = tuple(sorted(allowed))
    exact_index_axis = spec.work_allocation_policy == "exact_equal_daily_v1"
    # `exact_equal_daily_v1` advances through consecutive ELIGIBLE dates, so a
    # period may cross weekends, months and years. `equal_daily_rounded_v1`
    # advances through consecutive CALENDAR dates and simply fails on a date
    # that is not allowed. The two therefore count on different axes.
    axis: tuple[date, ...] = eligible if exact_index_axis else _calendar_window_dates(spec)
    axis_allowed = [day in allowed for day in axis]

    parents_by_day_count: dict[int, int] = {}
    # (work_date, start_minute, duration) — the exact tuple the daily unit
    # identity is keyed on once the constant spec fields are factored out.
    unique_units: set[tuple[date, int, int]] = set()

    for day_count in range(1, spec.max_consecutive_start_days + 1):
        duration = _daily_duration(spec, day_count)
        if duration is None:
            continue
        parents_for_count = 0
        for start_minute in _start_offsets(spec, duration):
            extra_days = _extra_occupied_days(start_minute, duration)
            usable = _usable_flags(axis, allowed, extra_days)
            if not exact_index_axis:
                # On the calendar axis a window position must ALSO be an
                # allowed date; on the eligible axis that holds by construction.
                usable = [flag and allowed_flag
                          for flag, allowed_flag in zip(usable, axis_allowed)]
            for run_start, run_length in _run_lengths(usable):
                if run_length < day_count:
                    continue
                windows = run_length - day_count + 1
                parents_for_count += windows
                # Every date in a run of at least `day_count` belongs to some
                # window, so the whole run contributes units at this start.
                for offset in range(run_length):
                    unique_units.add(
                        (axis[run_start + offset], start_minute, duration))
        if parents_for_count:
            parents_by_day_count[day_count] = parents_for_count

    parent_count = sum(parents_by_day_count.values())

    # ── warm-up / recovery against the downloaded demand year ────────────────
    demand_year = _demand_year(spec)
    source_start = date(demand_year, 1, 1)
    source_end = date(demand_year + 1, 1, 1)
    shapes: dict[tuple[int, int], _EnvelopeShape] = {}
    unavailable_units: set[tuple[date, int, int]] = set()
    if unique_units:
        first_unit_date = min(day for day, _s, _d in unique_units)
        last_unit_date = max(day for day, _s, _d in unique_units)
        transitions = _dst_transition_dates(
            spec,
            first_unit_date - timedelta(days=MAX_CLOSURE_ENVELOPE_DAYS),
            last_unit_date + timedelta(days=MAX_CLOSURE_ENVELOPE_DAYS),
        )
    else:
        transitions = frozenset()
    for work_date, start_minute, duration in unique_units:
        key = (start_minute, duration)
        shape = shapes.get(key)
        if shape is None:
            shape = shapes[key] = _envelope_shape(
                start_minute, duration,
                baseline_trip_duration_p99_s=baseline_trip_duration_p99_s,
                policy=policy)
        envelope_start = work_date - timedelta(days=shape.days_before)
        envelope_end = work_date + timedelta(days=shape.days_after)
        if not shape.valid:
            unavailable_units.add((work_date, start_minute, duration))
            continue
        if envelope_start < source_start or envelope_end > source_end:
            unavailable_units.add((work_date, start_minute, duration))
            continue
        # The envelope must not span an excluded DST transition date either;
        # `build_simulation_envelope` raises on that, which the search records
        # as an unavailable unit exactly like an out-of-year envelope.
        if transitions and any(
            (envelope_start + timedelta(days=offset)) in transitions
            for offset in range((envelope_end - envelope_start).days)
        ):
            unavailable_units.add((work_date, start_minute, duration))

    unit_count = len(unique_units)
    outside_year = len(unavailable_units)
    executable_units = unique_units - unavailable_units
    executable = len(executable_units)

    # ── cache ────────────────────────────────────────────────────────────────
    hits = misses = 0
    unknown = executable
    cache_basis = (
        "cache_unknown: the daily backend identity is only defined after the "
        "demand resolver has prepared an archive, which a read-only preflight "
        "must not do")
    if cache_root is not None and unit_cache_keys is not None:
        root = Path(cache_root)
        hits = misses = unknown = 0
        # Cache state is meaningful only for units the real runner can execute.
        # Counting an out-of-year unit as a hit could otherwise subtract it
        # twice and make the estimated SUMO workload negative or too small.
        for work_date, start_minute, duration in sorted(executable_units):
            identity = _unit_lookup_key(work_date, start_minute, duration)
            key = unit_cache_keys.get(identity)
            if key is None:
                unknown += 1
                continue
            if (root / key[:2] / f"{key}.json").is_file():
                hits += 1
            else:
                misses += 1
        cache_basis = (
            "existence of the content-addressed daily cache entry; an entry "
            "that fails integrity validation at run time becomes a miss")

    estimated = executable - hits
    estimation_basis = (
        "one SUMO daily unit per executable unique unit that is not already "
        "cached; each unit still runs the policy's own seed and demand-variant "
        "repetitions, which a read-only preflight cannot resolve")

    # ── size classification against the UNCHANGED hard caps ──────────────────
    reasons: list[str] = []
    if parent_count > parent_schedule_limit:
        reasons.append(
            f"parent schedules {parent_count} exceed the cap {parent_schedule_limit}")
    if unit_count > daily_unit_limit:
        reasons.append(
            f"unique daily units {unit_count} exceed the cap {daily_unit_limit}")
    if reasons:
        size_class = SIZE_OVER_RESOURCE_BUDGET
    elif (unit_count >= daily_unit_limit * LARGE_FRACTION_OF_LIMIT
            or parent_count >= parent_schedule_limit * LARGE_FRACTION_OF_LIMIT):
        size_class = SIZE_LARGE_BUT_RUNNABLE
    else:
        size_class = SIZE_NORMAL

    notes: list[str] = []
    if outside_year:
        notes.append(
            f"{outside_year} daily unit(s) need warm-up or recovery outside the "
            f"downloaded {demand_year} {spec.source} demand year and cannot run")
    if not parent_count:
        notes.append(
            "no schedule satisfies this contract; check the permitted band, "
            "requested work time and workday cap")

    return ClosureSearchPreflight(
        schema=PREFLIGHT_SCHEMA,
        search_id=spec.search_id,
        search_content_key=spec.content_key,
        permitted_date_start=spec.permitted_date_start,
        permitted_date_end=spec.permitted_date_end,
        eligible_workday_count=len(eligible),
        valid_workday_counts=tuple(sorted(parents_by_day_count)),
        parent_schedules_by_workday_count=dict(sorted(parents_by_day_count.items())),
        parent_schedule_count=parent_count,
        unique_daily_unit_count=unit_count,
        units_outside_demand_year=outside_year,
        executable_daily_unit_count=executable,
        cache_hits=hits,
        cache_misses=misses,
        cache_unknown=unknown,
        cache_basis=cache_basis,
        estimated_sumo_daily_units=estimated,
        estimation_basis=estimation_basis,
        parent_schedule_limit=parent_schedule_limit,
        daily_unit_limit=daily_unit_limit,
        size_class=size_class,
        limit_reasons=tuple(reasons),
        demand_year=demand_year,
        counting_method="run_length_windows_v1",
        notes=tuple(notes),
    )


def _unit_lookup_key(work_date: date, start_minute: int,
                     duration: int) -> tuple[str, str, str, int]:
    """The (work_date, start_time, end_time, duration) a daily unit is keyed on.

    Built with the same `datetime.combine` + `timedelta` arithmetic
    `generate_closure_schedules` uses, so a caller can match these against real
    `ClosureInterval` values without reformatting anything.
    """
    start = datetime.combine(work_date, time.min) + timedelta(minutes=start_minute)
    end = start + timedelta(minutes=duration)
    return (
        work_date.isoformat(),
        start.isoformat(timespec="seconds"),
        end.isoformat(timespec="seconds"),
        duration,
    )


def unit_lookup_keys(preflight_units: Iterable[tuple[date, int, int]]
                     ) -> tuple[tuple[str, str, str, int], ...]:
    """Public helper mirroring `_unit_lookup_key` over many units."""
    return tuple(_unit_lookup_key(*item) for item in preflight_units)
