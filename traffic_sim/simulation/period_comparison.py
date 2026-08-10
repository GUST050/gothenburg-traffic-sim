"""Compact rolling-period comparison for long-range closure searches."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from traffic_sim.core.contracts import ClosureSchedule


SCHEMA_VERSION = 1


def _cost_key(cost: Mapping[str, Any], candidate_id: str) -> tuple:
    return (
        float(cost["added_vehicle_hours"]),
        float(cost["added_metres_total"]),
        int(cost["vehicles_affected"]),
        candidate_id,
    )


def build_period_comparison(
    schedules: Mapping[str, ClosureSchedule],
    shortlist_ids: Sequence[str],
    pilot_selection: Mapping[str, Any],
    *,
    winner_id: str | None,
    tie_ids: Sequence[str],
    unavailable_count: int,
    objective_method: str,
    final_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep one best viable schedule per rolling start date.

    A period is the schedule itself, not an ISO week. It may span any number
    of permitted workdays and may cross week, month, or year boundaries.
    Grouping by start date keeps a multi-month API result compact while the
    immutable workspace retains every evaluated schedule.
    """
    statistics = {
        str(item.get("candidate_id")): item
        for item in pilot_selection.get("candidates", ())
        if isinstance(item, Mapping)
    }
    # Finalist evidence supersedes pilot evidence for the candidates that were
    # rerun. This matters when an additional seed discovers a hard failure;
    # the compact period table must never keep showing the earlier pilot as
    # viable.
    for item in (final_decision or {}).get("candidates", ()):
        if not isinstance(item, Mapping) or not item.get("candidate_id"):
            continue
        normalized = dict(item)
        normalized["complete"] = bool(
            item.get("all_variants_ready")
            or isinstance(item.get("closure_cost"), Mapping)
            or item.get("hard_failures")
        )
        statistics[str(item["candidate_id"])] = normalized
    grouped: dict[str, list[str]] = {}
    for candidate_id in shortlist_ids:
        schedule = schedules[candidate_id]
        grouped.setdefault(schedule.first_work_date, []).append(candidate_id)

    ties = set(tie_ids)
    periods: list[dict[str, Any]] = []
    for start_date in sorted(grouped):
        candidate_ids = tuple(grouped[start_date])
        evaluated = [
            statistics[candidate_id]
            for candidate_id in candidate_ids
            if candidate_id in statistics
        ]
        viable = [
            item for item in evaluated
            if item.get("eligible") is True
            and item.get("complete") is True
            and isinstance(item.get("closure_cost"), Mapping)
            and int(item["closure_cost"].get("vehicles_no_detour", 0)) == 0
        ]
        viable.sort(key=lambda item: _cost_key(
            item["closure_cost"], str(item["candidate_id"])
        ))
        best = viable[0] if viable else None
        best_schedule = (
            schedules[str(best["candidate_id"])] if best is not None else None
        )
        contains_winner = winner_id in candidate_ids if winner_id else False
        contains_tie = bool(ties.intersection(candidate_ids))
        if contains_winner:
            status = "best_period"
        elif contains_tie:
            status = "tied_best_period"
        elif viable:
            status = "viable"
        elif len(evaluated) == len(candidate_ids):
            status = "no_viable"
        else:
            status = "incomplete"
        periods.append({
            "start_date": start_date,
            "status": status,
            "candidate_count": len(candidate_ids),
            "evaluated_count": len(evaluated),
            "viable_count": len(viable),
            "hard_failed_count": sum(
                bool(item.get("hard_failures")) for item in evaluated
            ),
            "best_schedule": (
                best_schedule.to_dict() if best_schedule is not None else None
            ),
            "best_cost": dict(best["closure_cost"]) if best is not None else None,
            "period_end": (
                best_schedule.intervals[-1].work_date
                if best_schedule is not None else None
            ),
            "workday_count": (
                best_schedule.day_count if best_schedule is not None else None
            ),
            "contains_final_winner": contains_winner,
        })

    best_period = next(
        (item for item in periods if item["contains_final_winner"]), None
    )
    best_start_dates = [
        item["start_date"] for item in periods
        if item["status"] in {"best_period", "tied_best_period"}
    ]
    evaluated_count = sum(item["evaluated_count"] for item in periods)
    comparison_complete = (
        objective_method == "closure_cost_v1"
        and unavailable_count == 0
        and evaluated_count == len(shortlist_ids)
        and all(
            item.get("complete") is True or bool(item.get("hard_failures"))
            for item in statistics.values()
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "rolling_closure_period_comparison",
        "objective_method": objective_method,
        "comparison_complete": comparison_complete,
        "start_date_count": len(periods),
        "candidate_count": len(shortlist_ids),
        "evaluated_count": evaluated_count,
        "unavailable_count": unavailable_count,
        "best_start_date": (
            best_period["start_date"] if best_period is not None else None
        ),
        "best_start_dates": best_start_dates,
        "best_schedule_id": winner_id,
        "periods": periods,
    }
