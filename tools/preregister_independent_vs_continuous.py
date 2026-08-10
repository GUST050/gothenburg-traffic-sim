"""Freeze the independent-reset vs continuous comparison BEFORE any result.

PR H / step 7 of
`docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`.

WHY A PRE-REGISTRATION IS THE DELIVERABLE.  The question — does executing a
multi-day closure as independent daily units rank schedules the same way a
continuous envelope does — is exactly the kind that a chosen tolerance can
answer either way after the fact. So the cases, the paired comparison, the
metrics and the tolerances are frozen here, before any of them is measured, and
the record says plainly which ones the contracts allow to be paired at all.

WHAT IT CANNOT DO.  It runs no SUMO. Producing the comparison needs a
calibrated q10/q50/q90 demand archive per envelope; this tool builds and
validates the case set and writes the frozen record, and names the exact
command that would measure it.

CONTRACT REALITY, recorded rather than papered over.  The two policies do not
accept the same specs:

  * `independent_daily_reset_v1` requires `exact_equal_daily_v1` and a
    same-day band — it cannot express an overnight closure;
  * `continuous_v1` uses `equal_daily_rounded_v1`, walks CONSECUTIVE CALENDAR
    dates rather than consecutive eligible ones, and rounds each daily shift up
    to the 15-minute resolution.

A case is therefore only PAIRABLE when both policies accept it and produce a
schedule with the same work dates and the same daily clock times. The record
marks every case with `pairable` and the reason, so the comparison can never
silently compare two different closures.

Run:  python3 tools/preregister_independent_vs_continuous.py [--out PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.closure_calendar import iter_closure_schedules  # noqa: E402
from traffic_sim.core.contracts import (  # noqa: E402
    ClosureSearchSpec,
    DailyTimeBand,
)

DEFAULT_OUT = (
    ROOT / "validation" / "independent_vs_continuous_preregistration_v1.json")
SCHEMA = "independent_vs_continuous_preregistration_v1"

#: Workday counts the plan names, plus the long tail it explicitly refuses to
#: extrapolate into.
WORKDAY_COUNTS = (1, 3, 7, 14, 21, 45, 90)

#: Time-of-day bands. Each is a same-day band so the independent policy can
#: express it; an overnight band is recorded as unpairable rather than dropped.
TIME_BANDS = {
    "morning": ("06:00", "10:00"),
    "midday": ("10:00", "15:00"),
    "evening": ("15:00", "20:00"),
    "overnight": ("20:00", "06:00"),
}

#: Roads. The central one is the documented Skånegatan case whose closure is
#: known to cut off a small pocket; the peripheral one is a quieter street with
#: parallel alternatives. Both are real edges in the committed network.
ROADS = {
    "central_high_demand": ("60786979_3575001205_0",),
    "peripheral_low_demand": ("25382461_1362065332_0",),
}

#: Which weekdays the closure may use. This axis exists because the two
#: policies walk DIFFERENT date axes: independent steps through consecutive
#: ELIGIBLE dates, continuous through consecutive CALENDAR dates. With weekends
#: excluded, a continuous run of 7 workdays does not exist at all, so a paired
#: comparison beyond a working week is only expressible when every day is
#: allowed. Recorded as an axis rather than silently chosen.
WEEKDAY_POLICIES = {
    "weekdays_only": (0, 1, 2, 3, 4),
    "all_days": (0, 1, 2, 3, 4, 5, 6),
}

#: The contract's own ceiling for a continuous closure. Above this there is no
#: continuous counterfactual to compare against — not for want of demand, but
#: because `ClosureSearchSpec` refuses it. See `_CONTINUOUS_MAX_WORKDAYS`.
CONTINUOUS_MAX_WORKDAYS = 21

#: Demand levels, expressed as the source year the archive is built from.
DEMAND_LEVELS = {
    "measured_2025": "historical",
    "forecast_2027": "forecast",
}

#: Tolerances, FROZEN before any outcome is inspected.
TOLERANCES = {
    "added_vehicle_hours_relative": 0.05,
    "added_metres_total_relative": 0.05,
    "vehicles_affected_relative": 0.05,
    "queue_and_halting_relative": 0.10,
    "recovery_seconds_absolute": 300.0,
    "teleports_absolute": 0,
    "vehicles_no_detour_absolute": 0,
    "rank_flip_material_if_top_k": 3,
    "note": (
        "A rank flip is MATERIAL when it changes which schedule is inside the "
        "top 3 or changes the winner. A flip deeper in the order is recorded "
        "but does not by itself raise the risk class."),
}

#: What the comparison reports for every case, whatever it finds.
COMPARED_METRICS = (
    "ranking",
    "added_vehicle_hours",
    "added_metres_total",
    "vehicles_affected",
    "queue_and_halting",
    "recovery",
    "teleports",
    "vehicles_no_detour",
    "winner_and_ties",
)

RISK_CLASSES = {
    "low": (
        "no material rank flip and every metric inside its frozen tolerance: "
        "independent daily execution is permitted for this case class"),
    "high": (
        "a material rank flip or a metric outside tolerance: the finalists "
        "must be re-checked with a continuous envelope, or the recommendation "
        "is withheld for this case class"),
}


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _content_key(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _spec(*, case_id: str, edges: tuple[str, ...], workdays: int,
          band: tuple[str, str], source: str, interday: str,
          allocation: str, weekdays: tuple[int, ...]) -> ClosureSearchSpec:
    start = date(2027, 1, 4) if source == "forecast" else date(2025, 9, 1)
    # Enough calendar room for the workday count under either date axis.
    end = start + timedelta(days=max(6, workdays * 2 + 7))
    hours_per_day = 4
    return ClosureSearchSpec(
        search_id=case_id,
        directed_edges=edges,
        demand_build_id=f"{source}-{start.year}",
        source=source,
        permitted_date_start=start.isoformat(),
        permitted_date_end=end.isoformat(),
        required_work_minutes=hours_per_day * 60 * workdays,
        max_consecutive_start_days=workdays,
        permitted_daily_band=DailyTimeBand(*band),
        allowed_weekdays=weekdays,
        interday_policy=interday,
        work_allocation_policy=allocation,
        objective_profile="displaced_vehicles_and_detour_v1",
        period_comparison_policy=(
            "rolling_period_v1" if interday == "independent_daily_reset_v1"
            else "none_v1"),
    )


def _first_schedule(spec: ClosureSearchSpec, workdays: int):
    for schedule in iter_closure_schedules(spec):
        if schedule.day_count == workdays:
            return schedule
    return None


def build_case(case_id: str, *, road: str, workdays: int, band_name: str,
               demand: str, weekday_policy: str) -> dict[str, Any]:
    """One frozen case, with an honest verdict on whether it can be paired."""
    edges = ROADS[road]
    band = TIME_BANDS[band_name]
    source = DEMAND_LEVELS[demand]
    weekdays = WEEKDAY_POLICIES[weekday_policy]

    record: dict[str, Any] = {
        "case_id": case_id,
        "road": road,
        "directed_edges": list(edges),
        "workday_count": workdays,
        "time_band": band_name,
        "band": {"earliest_start": band[0], "latest_end": band[1]},
        "demand_level": demand,
        "source": source,
        "weekday_policy": weekday_policy,
        "allowed_weekdays": list(weekdays),
        "continuous_expressible": workdays <= CONTINUOUS_MAX_WORKDAYS,
    }

    independent = continuous = None
    reasons: list[str] = []
    try:
        independent = _spec(
            case_id=f"{case_id}-independent", edges=edges, workdays=workdays,
            band=band, source=source,
            interday="independent_daily_reset_v1",
            allocation="exact_equal_daily_v1", weekdays=weekdays)
    except ValueError as error:
        reasons.append(f"independent policy refuses this case: {error}")
    try:
        continuous = _spec(
            case_id=f"{case_id}-continuous", edges=edges, workdays=workdays,
            band=band, source=source,
            interday="continuous_v1",
            allocation="equal_daily_rounded_v1", weekdays=weekdays)
    except ValueError as error:
        reasons.append(f"continuous policy refuses this case: {error}")

    schedules = {}
    if independent is not None and continuous is not None:
        for name, spec in (("independent", independent),
                           ("continuous", continuous)):
            schedule = _first_schedule(spec, workdays)
            if schedule is None:
                reasons.append(
                    f"{name} policy enumerates no {workdays}-workday schedule")
            else:
                schedules[name] = schedule

    pairable = len(schedules) == 2 and not reasons
    if pairable:
        left, right = schedules["independent"], schedules["continuous"]
        same_dates = ([item.work_date for item in left.intervals]
                      == [item.work_date for item in right.intervals])
        same_clock = (left.daily_start == right.daily_start
                      and left.daily_end == right.daily_end)
        if not same_dates:
            reasons.append(
                "the two policies select different work dates: independent "
                "walks consecutive ELIGIBLE dates, continuous walks "
                "consecutive CALENDAR dates")
            pairable = False
        if not same_clock:
            reasons.append(
                "the two policies produce different daily clock times, so the "
                "closures are not the same closure")
            pairable = False
        if pairable:
            record["paired_schedule"] = {
                "first_work_date": left.first_work_date,
                "daily_start": left.daily_start,
                "daily_end": left.daily_end,
                "work_dates": [item.work_date for item in left.intervals],
                "scheduled_work_minutes": left.scheduled_work_minutes,
            }

    record["pairable"] = bool(pairable)
    record["unpairable_reasons"] = reasons
    if independent is not None:
        record["independent_spec"] = independent.to_dict()
    if continuous is not None:
        record["continuous_spec"] = continuous.to_dict()
    return record


def build_cases() -> tuple[dict[str, Any], ...]:
    """The frozen case set: one axis varied at a time around a documented centre."""
    cases: list[dict[str, Any]] = []
    for workdays in WORKDAY_COUNTS:
        for weekday_policy in WEEKDAY_POLICIES:
            for band_name in TIME_BANDS:
                for road in ROADS:
                    for demand in DEMAND_LEVELS:
                        # A full cross product would be hundreds of SUMO
                        # campaigns. Varying one axis at a time around a
                        # documented centre is what makes a difference
                        # attributable to that axis.
                        centre = (band_name == "morning"
                                  and road == "central_high_demand"
                                  and demand == "forecast_2027")
                        vary_band = (road == "central_high_demand"
                                     and demand == "forecast_2027")
                        vary_road = (band_name == "morning"
                                     and demand == "forecast_2027")
                        vary_demand = (band_name == "morning"
                                       and road == "central_high_demand")
                        if not (centre or vary_band or vary_road
                                or vary_demand):
                            continue
                        case_id = (
                            f"ivc-{workdays:02d}d-{weekday_policy}-{band_name}"
                            f"-{road}-{demand}").replace("_", "-")
                        cases.append(build_case(
                            case_id, road=road, workdays=workdays,
                            band_name=band_name, demand=demand,
                            weekday_policy=weekday_policy))
    return tuple(cases)


def build_record() -> dict[str, Any]:
    cases = build_cases()
    pairable = [case for case in cases if case["pairable"]]
    record = {
        "schema": SCHEMA,
        "kind": "independent_vs_continuous_preregistration",
        "stage": "closure_search_scaling_plan_pr_h",
        "evidence_class": "preregistration",
        "release_evidence": False,
        "registered_at": "2026-08-10",
        "status": "frozen_before_outcome",
        "question": (
            "Does executing a multi-day closure as independent daily reset "
            "units rank schedules the same way a continuous envelope does, "
            "for 1 to 90 workdays?"),
        "tolerances": dict(TOLERANCES),
        "compared_metrics": list(COMPARED_METRICS),
        "risk_classes": dict(RISK_CLASSES),
        "extrapolation_rule": (
            "Evidence from 21 workdays does NOT license a claim about 90. "
            "Each workday-count band carries its own risk class, and a band "
            "with no measurement carries no claim."),
        "claim_boundary": {
            "opens_no_gate": True,
            "reason": (
                "a frozen question and its tolerances; it measures nothing "
                "and licenses nothing"),
        },
        "case_count": len(cases),
        "pairable_case_count": len(pairable),
        "workday_counts": list(WORKDAY_COUNTS),
        "continuous_max_workdays": CONTINUOUS_MAX_WORKDAYS,
        "contract_finding": (
            "ClosureSearchSpec refuses a continuous closure longer than "
            f"{CONTINUOUS_MAX_WORKDAYS} workdays, so above that there is no "
            "continuous counterfactual to compare against — not for want of "
            "demand, but by contract. Every case above it is recorded as "
            "independent-only, and no 1-21 day result may be extrapolated "
            "into that range."),
        "cases": list(cases),
        "blocked_by": {
            "reason": (
                "the comparison needs one calibrated q10/q50/q90 demand "
                "archive per envelope, on both date axes; no calibrated demand "
                "exists in this environment"),
            "reproducible_command": (
                "make demand && python3 tools/"
                "preregister_independent_vs_continuous.py --out "
                "validation/independent_vs_continuous_preregistration_v1.json "
                "&& python3 run_monthly_closure_search.py --spec <case "
                "independent_spec> --policy validation/"
                "monthly_search_policy_v2.json --screening-mode "
                "independent-exhaustive"),
        },
    }
    record["input_content_key"] = _content_key({
        "schema": SCHEMA,
        "tolerances": TOLERANCES,
        "compared_metrics": list(COMPARED_METRICS),
        "cases": [
            {key: case[key] for key in ("case_id", "road", "workday_count",
                                        "time_band", "demand_level",
                                        "weekday_policy")}
            for case in cases
        ],
    })
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), metavar="PATH")
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    record = build_record()
    payload = json.dumps(record, indent=1, sort_keys=True) + "\n"
    if args.stdout:
        print(payload, end="")
        return 0
    destination = Path(args.out)
    if destination.exists() and not args.overwrite:
        raise SystemExit(
            f"{destination} already exists; a pre-registration is frozen. "
            f"Pass --overwrite only to correct it BEFORE any outcome exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload, encoding="utf-8")
    print(f"wrote {destination} "
          f"({record['pairable_case_count']}/{record['case_count']} pairable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
