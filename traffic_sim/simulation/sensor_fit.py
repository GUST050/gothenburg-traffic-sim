"""Fail-closed validation of final SUMO sensor-output evidence.

The PFE proves that route files meet their frozen count constraints before
SUMO runs.  This module validates the separate, final claim: raw ``edgeData``
from the Monte Carlo ensemble still matches those frozen targets.  It is
shared by publication and the validation report so a green UI state and a
publishable release cannot use different definitions of a passing fit.
"""
from __future__ import annotations

import math
from typing import Any


# A final SUMO run is allowed no unbounded tail: every measured interval must
# meet the usual GEH<5 criterion.  The percentage is retained in the contract
# for readable reporting and a future explicitly-approved tolerance change.
MIN_GEH_LT_5_PCT = 99.0
MAX_GEH_EXCLUSIVE = 5.0


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _geh(simulated: float, target: float) -> float:
    return math.sqrt(2.0 * (simulated - target) ** 2 /
                     max(simulated + target, 1e-12))


def summarize_pairs(pairs: list[tuple[float, float]]) -> dict:
    """Summarize aligned raw-SUMO/target pairs under the release contract.

    The scenario producer uses this exact function when it writes its compact
    audit summary; the consumer below recomputes it from the detailed rows.
    Keeping one implementation prevents a future rounding or GEH-definition
    change from making a producer-generated audit fail its own publication
    gate.
    """
    values = [_geh(simulated, target) for simulated, target in pairs]
    result = {"available": bool(values), "edge_quarters": len(values)}
    if values:
        errors = [abs(simulated - target) for simulated, target in pairs]
        result.update({
            "geh_lt_5_pct": round(100.0 * sum(value < MAX_GEH_EXCLUSIVE
                                                for value in values) / len(values), 1),
            "max_geh": round(max(values), 3),
            "mean_abs_error": round(sum(errors) / len(errors), 6),
            "max_abs_error": round(max(errors), 6),
        })
    return result


def _pairs_from_rows(rows: Any, *, target_key: str, raw_key: str,
                     n_intervals: int, label: str) -> tuple[list[tuple[float, float]], list[str]]:
    errors: list[str] = []
    pairs: list[tuple[float, float]] = []
    if not isinstance(rows, list) or not rows:
        return pairs, [f"{label} saknar serier"]
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"{label} rad {row_index} är ogiltig")
            continue
        targets, raw = row.get(target_key), row.get(raw_key)
        if not isinstance(targets, list) or not isinstance(raw, list):
            errors.append(f"{label} rad {row_index} saknar rå- eller targetserie")
            continue
        if len(targets) != n_intervals or len(raw) != n_intervals:
            errors.append(f"{label} rad {row_index} har fel tidsseriebredd")
            continue
        for qi, (target, simulated) in enumerate(zip(targets, raw)):
            sim = _finite(simulated)
            if sim is None:
                errors.append(f"{label} rad {row_index} har ogiltigt råvärde i kvart {qi}")
                break
            if target is None:
                continue
            goal = _finite(target)
            if goal is None:
                errors.append(f"{label} rad {row_index} har ogiltigt target i kvart {qi}")
                break
            pairs.append((sim, goal))
    if not pairs and not errors:
        errors.append(f"{label} saknar mätbara target-intervall")
    return pairs, errors


def _summary_errors(declared: Any, actual: dict, *, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(declared, dict):
        return [f"{label} saknar deklarerad fit-sammanfattning"]
    if declared.get("available") is not True:
        errors.append(f"{label} är inte markerad som mätbar")
    if declared.get("edge_quarters") != actual["edge_quarters"]:
        errors.append(f"{label} har fel antal jämförda edge×kvartar")
    for key, digits in (("geh_lt_5_pct", 1), ("max_geh", 3),
                        ("mean_abs_error", 6), ("max_abs_error", 6)):
        value = _finite(declared.get(key))
        if value is None or round(value, digits) != actual[key]:
            errors.append(f"{label} har inkonsekvent {key}")
    return errors


def assess_output_fit(audit: Any, *, n_intervals: int) -> dict:
    """Recompute and validate the raw final-SUMO sensor fit.

    ``errors == []`` is the sole passing condition.  The declared compact
    summaries are checked against the detailed raw series instead of trusted,
    which catches stale, truncated, or manually mixed audit artifacts.
    """
    errors: list[str] = []
    if not isinstance(audit, dict):
        return {"errors": ["sensor-output-fit saknar audit"],
                "directions": None, "stations": None}
    if not isinstance(n_intervals, int) or n_intervals <= 0:
        return {"errors": ["sensor-output-fit saknar giltigt antal kvartar"],
                "directions": None, "stations": None}
    output_fit = audit.get("output_fit")
    if not isinstance(output_fit, dict):
        return {"errors": ["sensor-output-fit saknar slutlig SUMO-audit"],
                "directions": None, "stations": None}
    if output_fit.get("uses_raw_ensemble_mean") is not True:
        errors.append("sensor-output-fit måste använda rå edgeData före avrundning")

    direction_pairs, direction_errors = _pairs_from_rows(
        audit.get("directions"), target_key="target_mean",
        raw_key="simulated_mean_raw", n_intervals=n_intervals,
        label="riktad sensor-fit")
    station_pairs, station_errors = _pairs_from_rows(
        audit.get("stations"), target_key="target_mean",
        raw_key="simulated_mean_raw", n_intervals=n_intervals,
        label="fysisk stations-fit")
    errors.extend(direction_errors)
    errors.extend(station_errors)

    direction = summarize_pairs(direction_pairs)
    station = summarize_pairs(station_pairs)
    errors.extend(_summary_errors(output_fit.get("ensemble"), direction,
                                  label="riktad sensor-fit"))
    errors.extend(_summary_errors(output_fit.get("station_ensemble"), station,
                                  label="fysisk stations-fit"))
    for label, summary in (("riktad sensor-fit", direction),
                           ("fysisk stations-fit", station)):
        if not summary["available"]:
            continue
        if summary["geh_lt_5_pct"] < MIN_GEH_LT_5_PCT:
            errors.append(f"{label} når bara GEH<5 {summary['geh_lt_5_pct']}%")
        if summary["max_geh"] >= MAX_GEH_EXCLUSIVE:
            errors.append(f"{label} har GEH {summary['max_geh']} (kräver <5)")
    return {"errors": errors, "directions": direction, "stations": station}
