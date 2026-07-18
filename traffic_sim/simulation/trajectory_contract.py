"""Browser-trajectory limits and fail-closed validation.

Scenario flow and sensor products always contain every simulated vehicle.
Only the optional browser animation is sampled for multi-day studies, because
shipping an unbounded week of per-edge timelines would make the UI artifact
grow linearly without limit.
"""
from __future__ import annotations

import math
from typing import Any


MULTIDAY_MAX_VEHICLES_PER_DAY = 10_000
MULTIDAY_MAX_ARTIFACT_BYTES = 96 * 1024 * 1024
SAMPLING_METHOD = "sha256_vehicle_id_per_day"


def _strict_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def validate_multiday_trajectory(payload: Any, *, days: int,
                                 expected_inserted: int | None = None
                                 ) -> list[str]:
    """Return errors for a bounded, real-vehicle multi-day browser product."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["fordonsfilen är inte ett JSON-objekt"]
    if not isinstance(days, int) or not 2 <= days <= 9:
        return ["fordonsvalideringen kräver 2–9 dagar"]

    inserted = _strict_nonnegative_int(payload.get("inserted_in_run"))
    selected = _strict_nonnegative_int(payload.get("n_vehicles"))
    if inserted is None or inserted <= 0:
        errors.append("fordonsfilen saknar positivt inserted_in_run")
    if selected is None or selected <= 0:
        errors.append("fordonsfilen saknar valda fordon")
    if (expected_inserted is not None and inserted is not None
            and inserted != expected_inserted):
        errors.append("fordonsfilens inserted_in_run matchar inte representativt frö")

    sampling = payload.get("sampling")
    if not isinstance(sampling, dict) or sampling.get("enabled") is not True:
        errors.append("flerdagsfordon saknar aktiverat, begränsat urval")
        return errors
    if sampling.get("method") != SAMPLING_METHOD:
        errors.append("flerdagsfordon har okänd urvalsmetod")
    cap = _strict_nonnegative_int(sampling.get("max_vehicles_per_day"))
    if cap is None or cap <= 0 or cap > MULTIDAY_MAX_VEHICLES_PER_DAY:
        errors.append("flerdagsfordon överskrider tillåtet urval per dag")

    rows = sampling.get("per_day")
    if not isinstance(rows, list) or len(rows) != days:
        errors.append("flerdagsfordon saknar urvalsbevis för varje dag")
        return errors
    row_selected: list[int] = []
    row_eligible: list[int] = []
    for expected_day, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get("day") != expected_day:
            errors.append(f"flerdagsfordon saknar urvalsbevis för dag {expected_day}")
            continue
        eligible = _strict_nonnegative_int(row.get("eligible"))
        chosen = _strict_nonnegative_int(row.get("selected"))
        if eligible is None or eligible <= 0 or chosen is None or chosen <= 0:
            errors.append(f"flerdagsfordon har tomt urval för dag {expected_day}")
            continue
        if chosen > eligible or (cap is not None and chosen > cap):
            errors.append(f"flerdagsfordon har ogiltig urvalsstorlek dag {expected_day}")
        row_eligible.append(eligible)
        row_selected.append(chosen)

    declared_selected = _strict_nonnegative_int(
        sampling.get("selected_vehicles"))
    declared_eligible = _strict_nonnegative_int(
        sampling.get("eligible_vehicles"))
    if selected is not None and sum(row_selected) != selected:
        errors.append("fordonsfilens dagliga urval matchar inte n_vehicles")
    if declared_selected is None or declared_selected != sum(row_selected):
        errors.append("fordonsfilens selected_vehicles är inkonsekvent")
    if declared_eligible is None or declared_eligible != sum(row_eligible):
        errors.append("fordonsfilens eligible_vehicles är inkonsekvent")

    vehicles = payload.get("vehicles")
    if not isinstance(vehicles, list):
        errors.append("fordonsfilen saknar vehicles-lista")
        return errors
    if selected is not None and len(vehicles) != selected:
        errors.append("fordonsfilens vehicles-lista matchar inte n_vehicles")

    actual_by_day = [0] * days
    previous_depart = -1
    unfinished = 0
    for index, vehicle in enumerate(vehicles):
        if not isinstance(vehicle, dict):
            errors.append(f"fordon {index + 1} är inte ett objekt")
            continue
        depart = _strict_nonnegative_int(vehicle.get("d"))
        edges, exits = vehicle.get("e"), vehicle.get("x")
        if depart is None or depart >= days * 86400:
            errors.append(f"fordon {index + 1} har avgång utanför perioden")
            continue
        if depart < previous_depart:
            errors.append("fordonsfilens avgångar är inte monotont sorterade")
        previous_depart = depart
        actual_by_day[depart // 86400] += 1
        if (not isinstance(edges, list) or not isinstance(exits, list)
                or not edges or len(edges) != len(exits)):
            errors.append(f"fordon {index + 1} har ogiltig kant-/tidsserie")
            continue
        previous_exit = depart
        for exit_time in exits:
            parsed_exit = _strict_nonnegative_int(exit_time)
            if parsed_exit is None or parsed_exit < previous_exit:
                errors.append(f"fordon {index + 1} har icke-monotona utfartstider")
                break
            previous_exit = parsed_exit
        if vehicle.get("u") == 1:
            unfinished += 1

    if row_selected and actual_by_day != row_selected:
        errors.append("fordonsfilens faktiska dagar matchar inte urvalsbeviset")
    declared_unfinished = _strict_nonnegative_int(payload.get("n_unfinished"))
    if declared_unfinished is None or declared_unfinished != unfinished:
        errors.append("fordonsfilens n_unfinished är inkonsekvent")
    if inserted and selected is not None:
        expected_share = round(selected / inserted, 4)
        try:
            actual_share = round(float(payload.get("displayed_share")), 4)
        except (TypeError, ValueError):
            actual_share = None
        if actual_share != expected_share:
            errors.append("fordonsfilens displayed_share är inkonsekvent")
    return errors
