"""Pure calendar enumeration for recurring road-work closure searches.

This module has no SUMO, filesystem, or web dependencies.  It converts one
validated :class:`ClosureSearchSpec` into exact, deterministic schedules that
every later layer can serialize, simulate, and display without reinterpreting
the user's work-time request.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from datetime import date, datetime, time, timedelta
from typing import Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from .contracts import (
    ClosureInterval,
    ClosureSchedule,
    ClosureSearchSpec,
    ClosureSpec,
)


def _clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _calendar_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def is_dst_transition_date(day: date, timezone: str) -> bool:
    """Return whether the UTC offset changes during this local calendar date."""
    zone = ZoneInfo(timezone)
    beginning = datetime.combine(day, time.min, tzinfo=zone)
    following = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return beginning.utcoffset() != following.utcoffset()


def _start_offsets(
    spec: ClosureSearchSpec,
    *,
    daily_duration_minutes: int,
) -> tuple[int, ...]:
    """Return start clocks in chronological order through the permitted band.

    For a 22:00--06:00 band the chronological offsets are 22:00...24:00...
    06:00, while returned values are normalized local clock minutes.  A
    02:00 start therefore remains 02:00 on its declared work date.
    """
    start = _clock_minutes(spec.permitted_daily_band.earliest_start)
    end = _clock_minutes(spec.permitted_daily_band.latest_end)
    if end <= start:
        end += 24 * 60
    last_start = end - daily_duration_minutes
    if last_start < start:
        return ()
    return tuple(
        offset % (24 * 60)
        for offset in range(start, last_start + 1, spec.resolution_minutes)
    )


def _occupied_dates(start: datetime, end: datetime) -> tuple[date, ...]:
    """Calendar dates touched by the half-open interval ``[start, end)``."""
    last_occupied = (end - timedelta(microseconds=1)).date()
    return tuple(
        start.date() + timedelta(days=offset)
        for offset in range((last_occupied - start.date()).days + 1)
    )


def _date_is_allowed(spec: ClosureSearchSpec, day: date) -> bool:
    if not (
        _calendar_date(spec.permitted_date_start)
        <= day
        <= _calendar_date(spec.permitted_date_end)
    ):
        return False
    if day.weekday() not in spec.allowed_weekdays:
        return False
    if day.isoformat() in spec.blackout_dates:
        return False
    if (
        spec.dst_policy == "exclude_transition_dates"
        and is_dst_transition_date(day, spec.timezone)
    ):
        return False
    return True


def _schedule_id(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"closure-{digest}"


def iter_closure_schedules(
    spec: ClosureSearchSpec,
) -> Iterator[ClosureSchedule]:
    """Yield every legal same-time recurring schedule for ``spec``, lazily.

    Required work is divided equally across ``n`` consecutive start dates and
    rounded upward to the 15-minute resolution separately for each possible
    day count.  Under the frozen version-1 assumption the scheduled work and
    closure durations are identical.

    PR C of `docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`.
    The six-month 360-hour case materialises 11,813 parent schedules at
    489.9 MiB before anything can look at the first one; streaming lets a
    consumer write each schedule to a ledger and release it.

    The enumeration body below is the ORIGINAL one, unchanged except that it
    yields where it used to append. That is deliberate rather than tidy: the
    schedule IDs, the interval order and the canonical order of the whole
    sequence are contract, so re-deriving them would risk exactly the drift
    this refactor must not cause. `generate_closure_schedules` is now a
    wrapper that materialises this iterator, so the two cannot diverge.

    The spec type check runs EAGERLY, before any generator is returned, so a
    bad argument still raises at the call site as it always did.
    """
    if not isinstance(spec, ClosureSearchSpec):
        raise TypeError("spec must be a ClosureSearchSpec")
    return _iter_closure_schedules(spec)


def _iter_closure_schedules(
    spec: ClosureSearchSpec,
) -> Iterator[ClosureSchedule]:
    permitted_start = _calendar_date(spec.permitted_date_start)
    permitted_end = _calendar_date(spec.permitted_date_end)
    search_content_key = spec.content_key
    blackout_dates = set(spec.blackout_dates)
    allowed_dates: set[date] = set()
    candidate_date = permitted_start
    while candidate_date <= permitted_end:
        if (
            candidate_date.weekday() in spec.allowed_weekdays
            and candidate_date.isoformat() not in blackout_dates
            and not (
                spec.dst_policy == "exclude_transition_dates"
                and is_dst_transition_date(candidate_date, spec.timezone)
            )
        ):
            allowed_dates.add(candidate_date)
        candidate_date += timedelta(days=1)
    eligible_dates = tuple(sorted(allowed_dates))
    eligible_date_index = {
        item: index for index, item in enumerate(eligible_dates)
    }

    first_work_date = permitted_start
    while first_work_date <= permitted_end:
        for day_count in range(
            spec.min_consecutive_start_days,
            spec.max_consecutive_start_days + 1,
        ):
            if spec.work_allocation_policy in {
                "exact_balanced_daily_v1", "exact_equal_daily_v1"
            }:
                first_index = eligible_date_index.get(first_work_date)
                if first_index is None:
                    continue
                work_dates = eligible_dates[
                    first_index:first_index + day_count
                ]
                if len(work_dates) != day_count:
                    break
            else:
                final_start_date = (
                    first_work_date + timedelta(days=day_count - 1)
                )
                if final_start_date > permitted_end:
                    break
                work_dates = tuple(
                    first_work_date + timedelta(days=offset)
                    for offset in range(day_count)
                )

            if spec.work_allocation_policy == "exact_equal_daily_v1":
                total_blocks = (
                    spec.required_work_minutes // spec.resolution_minutes
                )
                daily_blocks, remainder = divmod(total_blocks, day_count)
                if daily_blocks <= 0 or remainder:
                    continue
                daily_duration = daily_blocks * spec.resolution_minutes
                durations_by_pattern = ((daily_duration,) * day_count,)
            elif spec.work_allocation_policy == "exact_balanced_daily_v1":
                total_blocks = (
                    spec.required_work_minutes // spec.resolution_minutes
                )
                short_blocks, longer_days = divmod(total_blocks, day_count)
                if short_blocks <= 0:
                    continue
                pattern_count = math.comb(day_count, longer_days)
                if pattern_count > 4096:
                    raise ValueError(
                        "exact balanced work allocation creates "
                        f"{pattern_count} daily-duration patterns for "
                        f"{day_count} days; narrow max days or use an "
                        "explicit fixed allocation"
                    )
                patterns = tuple(
                    frozenset(indices)
                    for indices in itertools.combinations(
                        range(day_count), longer_days
                    )
                )
                # divmod(..., day_count) yields zero remainder for the common
                # exact case. itertools.combinations(..., 0) already returns
                # one empty allocation pattern.
                durations_by_pattern = tuple(
                    tuple(
                        (short_blocks + (index in longer))
                        * spec.resolution_minutes
                        for index in range(day_count)
                    )
                    for longer in patterns
                )
                daily_duration = max(durations_by_pattern[0])
            else:
                denominator = spec.resolution_minutes * day_count
                quarters_per_day = (
                    spec.required_work_minutes + denominator - 1
                ) // denominator
                daily_duration = quarters_per_day * spec.resolution_minutes
                durations_by_pattern = (
                    (daily_duration,) * day_count,
                )
            if daily_duration <= 0:
                continue
            # More than one 24-hour shift would leave no opening between
            # consecutive work passes, contrary to the version-1 contract.
            if day_count > 1 and daily_duration >= 24 * 60:
                continue

            for durations in durations_by_pattern:
                maximum_duration = max(durations)
                for start_minute in _start_offsets(
                    spec, daily_duration_minutes=maximum_duration
                ):
                    intervals: list[ClosureInterval] = []
                    valid = True
                    for work_date, duration in zip(work_dates, durations):
                        start_time = datetime.combine(
                            work_date,
                            time(
                                hour=start_minute // 60,
                                minute=start_minute % 60,
                            ),
                        )
                        end_time = start_time + timedelta(minutes=duration)
                        if not all(
                            occupied in allowed_dates
                            for occupied in _occupied_dates(start_time, end_time)
                        ):
                            valid = False
                            break
                        intervals.append(
                            ClosureInterval(
                                work_date=work_date.isoformat(),
                                start_time=start_time.isoformat(timespec="seconds"),
                                end_time=end_time.isoformat(timespec="seconds"),
                            )
                        )
                    if not valid:
                        continue

                    scheduled_work = sum(durations)
                    daily_start = (
                        f"{start_minute // 60:02d}:"
                        f"{start_minute % 60:02d}"
                    )
                    daily_end_minutes = (
                        start_minute + maximum_duration
                    ) % (24 * 60)
                    daily_end = (
                        "24:00"
                        if start_minute + maximum_duration == 24 * 60
                        else f"{daily_end_minutes // 60:02d}:"
                        f"{daily_end_minutes % 60:02d}"
                    )
                    identity = {
                        "search_content_key": search_content_key,
                        "first_work_date": first_work_date.isoformat(),
                        "day_count": day_count,
                        "daily_start": daily_start,
                        "daily_end": daily_end,
                        "scheduled_work_minutes": scheduled_work,
                    }
                    if spec.work_allocation_policy != "equal_daily_rounded_v1":
                        identity.update({
                            "allocation_policy": spec.work_allocation_policy,
                            "interval_durations_minutes": list(durations),
                            "work_dates": [
                                item.isoformat() for item in work_dates
                            ],
                        })
                    yield (
                        ClosureSchedule(
                            schedule_id=_schedule_id(identity),
                            search_content_key=search_content_key,
                            first_work_date=first_work_date.isoformat(),
                            day_count=day_count,
                            daily_start=daily_start,
                            daily_end=daily_end,
                            required_work_minutes=spec.required_work_minutes,
                            scheduled_work_minutes=scheduled_work,
                            actual_closed_minutes=scheduled_work,
                            rounding_overshoot_minutes=(
                                scheduled_work - spec.required_work_minutes
                            ),
                            intervals=tuple(intervals),
                            allocation_policy=spec.work_allocation_policy,
                        )
                    )
        first_work_date += timedelta(days=1)


def generate_closure_schedules(
    spec: ClosureSearchSpec,
) -> tuple[ClosureSchedule, ...]:
    """Materialise every legal schedule for ``spec``.

    Backward-compatible wrapper over `iter_closure_schedules`, kept because
    caches, frozen campaign tooling and every pre-PR-C caller expect a tuple.
    New execution paths should stream instead: this call is exactly the
    materialisation PR C exists to avoid on large searches.
    """
    return tuple(iter_closure_schedules(spec))


def expand_schedule_closures(
    spec: ClosureSearchSpec,
    schedule: ClosureSchedule,
) -> tuple[ClosureSpec, ...]:
    """Expand one schedule to exact per-edge intervals for ``ScenarioSpec``."""
    if schedule.search_content_key != spec.content_key:
        raise ValueError("closure schedule does not belong to this search spec")
    return tuple(
        ClosureSpec(
            edge_id=edge_id,
            start_time=interval.start_time,
            end_time=interval.end_time,
            closure_type=spec.closure_type,
        )
        for interval in schedule.intervals
        for edge_id in spec.directed_edges
    )


def validate_connected_worksite(
    directed_edges: Sequence[str],
    edge_endpoints: Mapping[str, tuple[str, str]],
) -> None:
    """Fail unless selected directed edges form one undirected component."""
    selected = tuple(directed_edges)
    if not selected:
        raise ValueError("worksite must contain at least one directed edge")
    missing = [edge_id for edge_id in selected if edge_id not in edge_endpoints]
    if missing:
        raise ValueError(
            "worksite edge endpoints are missing for: " + ", ".join(sorted(missing))
        )

    adjacency: dict[str, set[str]] = {}
    selected_nodes: set[str] = set()
    for edge_id in selected:
        endpoints = edge_endpoints[edge_id]
        if (
            not isinstance(endpoints, tuple)
            or len(endpoints) != 2
            or not all(isinstance(node, str) and node for node in endpoints)
        ):
            raise ValueError(f"worksite edge {edge_id} has invalid endpoints")
        left, right = endpoints
        selected_nodes.update((left, right))
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)

    pending = [next(iter(selected_nodes))]
    reached: set[str] = set()
    while pending:
        node = pending.pop()
        if node in reached:
            continue
        reached.add(node)
        pending.extend(adjacency.get(node, ()) - reached)
    if reached != selected_nodes:
        raise ValueError("selected directed edges must form one connected worksite")
