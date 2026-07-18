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
                     n_intervals: int, label: str,
                     aggregation_quarters: int = 1,
                     ) -> tuple[list[tuple[float, float]], list[str]]:
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
        for start in range(0, n_intervals, aggregation_quarters):
            end = min(start + aggregation_quarters, n_intervals)
            target_block = targets[start:end]
            if all(target is None for target in target_block):
                continue
            simulated_sum = 0.0
            target_sum = 0.0
            valid = True
            for qi in range(start, end):
                sim = _finite(raw[qi])
                if sim is None:
                    errors.append(
                        f"{label} rad {row_index} har ogiltigt råvärde i kvart {qi}")
                    valid = False
                    break
                simulated_sum += sim
                target = targets[qi]
                if target is None:
                    continue
                goal = _finite(target)
                if goal is None:
                    errors.append(
                        f"{label} rad {row_index} har ogiltigt target i kvart {qi}")
                    valid = False
                    break
                target_sum += goal
            if not valid:
                break
            pairs.append((simulated_sum, target_sum))
    if not pairs and not errors:
        errors.append(f"{label} saknar mätbara target-intervall")
    return pairs, errors


def summarize_rows(rows: Any, *, n_intervals: int,
                   aggregation_quarters: int) -> dict:
    """Summarize aligned raw/target rows using the declared time aggregate.

    Production GEH is hourly (four source quarters), matching PFE's standard
    calibration metric. The detailed 15-minute arrays remain in the audit so
    temporal spillover is inspectable instead of discarded.
    """
    pairs, errors = _pairs_from_rows(
        rows, target_key="target_mean", raw_key="simulated_mean_raw",
        n_intervals=n_intervals, label="sensor-output-fit",
        aggregation_quarters=aggregation_quarters)
    if errors:
        raise ValueError("; ".join(errors))
    return summarize_pairs(pairs)


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


def _daily_output_fit_errors(audit: dict, output_fit: dict, *,
                             n_intervals: int, days: int,
                             aggregation_quarters: int,
                             ) -> tuple[list[str], list[dict]]:
    """Recompute each full day's fit so one good date cannot hide another."""
    errors: list[str] = []
    actual_rows: list[dict] = []
    if n_intervals != days * 96:
        return (["sensor-output-fit för flera dagar kräver 96 kvartar per dag"],
                actual_rows)
    declared_rows = output_fit.get("per_day")
    if not isinstance(declared_rows, list) or len(declared_rows) != days:
        return (["sensor-output-fit saknar en deklarerad fit per dag"],
                actual_rows)

    for day_index in range(days):
        start, end = day_index * 96, (day_index + 1) * 96
        declared = declared_rows[day_index]
        if not isinstance(declared, dict) or declared.get("day") != day_index + 1:
            errors.append(
                f"sensor-output-fit saknar deklarerad dag {day_index + 1}")
            continue
        if (declared.get("quarter_start") != start
                or declared.get("quarter_end") != end):
            errors.append(
                f"sensor-output-fit har fel kvartgränser för dag {day_index + 1}")

        def window_pairs(rows: Any, label: str) -> tuple[list[tuple[float, float]], list[str]]:
            if not isinstance(rows, list):
                return [], [f"{label} saknar serier"]
            sliced = []
            for row in rows:
                if not isinstance(row, dict):
                    sliced.append(row)
                    continue
                sliced.append({
                    "target_mean": (
                        row.get("target_mean")[start:end]
                        if isinstance(row.get("target_mean"), list) else None),
                    "simulated_mean_raw": (
                        row.get("simulated_mean_raw")[start:end]
                        if isinstance(row.get("simulated_mean_raw"), list) else None),
                })
            return _pairs_from_rows(
                sliced, target_key="target_mean",
                raw_key="simulated_mean_raw", n_intervals=96, label=label,
                # Keep each date independent; 96 is divisible by the
                # production hourly aggregate, so no block crosses midnight.
                aggregation_quarters=aggregation_quarters)

        direction_pairs, direction_errors = window_pairs(
            audit.get("directions"),
            f"riktad sensor-fit dag {day_index + 1}")
        station_pairs, station_errors = window_pairs(
            audit.get("stations"),
            f"fysisk stations-fit dag {day_index + 1}")
        errors.extend(direction_errors)
        errors.extend(station_errors)
        direction = summarize_pairs(direction_pairs)
        station = summarize_pairs(station_pairs)
        errors.extend(_summary_errors(
            declared.get("ensemble"), direction,
            label=f"riktad sensor-fit dag {day_index + 1}"))
        errors.extend(_summary_errors(
            declared.get("station_ensemble"), station,
            label=f"fysisk stations-fit dag {day_index + 1}"))
        for label, summary in (
                (f"riktad sensor-fit dag {day_index + 1}", direction),
                (f"fysisk stations-fit dag {day_index + 1}", station)):
            if summary["available"]:
                if summary["geh_lt_5_pct"] < MIN_GEH_LT_5_PCT:
                    errors.append(
                        f"{label} når bara GEH<5 {summary['geh_lt_5_pct']}%")
                if summary["max_geh"] >= MAX_GEH_EXCLUSIVE:
                    errors.append(
                        f"{label} har GEH {summary['max_geh']} (kräver <5)")
        actual_rows.append({
            "day": day_index + 1,
            "quarter_start": start,
            "quarter_end": end,
            "directions": direction,
            "stations": station,
        })
    return errors, actual_rows


def assess_output_fit(audit: Any, *, n_intervals: int,
                      days: int = 1) -> dict:
    """Recompute and validate the raw final-SUMO sensor fit.

    ``errors == []`` is the sole passing condition.  The declared compact
    summaries are checked against the detailed raw series instead of trusted,
    which catches stale, truncated, or manually mixed audit artifacts.
    """
    errors: list[str] = []
    if not isinstance(audit, dict):
        return {"errors": ["sensor-output-fit saknar audit"],
                "directions": None, "stations": None, "per_day": []}
    if not isinstance(n_intervals, int) or n_intervals <= 0:
        return {"errors": ["sensor-output-fit saknar giltigt antal kvartar"],
                "directions": None, "stations": None, "per_day": []}
    if not isinstance(days, int) or days <= 0:
        return {"errors": ["sensor-output-fit saknar giltigt antal dagar"],
                "directions": None, "stations": None, "per_day": []}
    output_fit = audit.get("output_fit")
    if not isinstance(output_fit, dict):
        return {"errors": ["sensor-output-fit saknar slutlig SUMO-audit"],
                "directions": None, "stations": None, "per_day": []}
    if output_fit.get("uses_raw_ensemble_mean") is not True:
        errors.append("sensor-output-fit måste använda rå edgeData före avrundning")
    try:
        aggregation_quarters = int(
            output_fit.get("aggregation_quarters", 1))
    except (TypeError, ValueError):
        aggregation_quarters = 0
    if aggregation_quarters not in {1, 4}:
        errors.append("sensor-output-fit har ogiltig tidsaggregering")
        aggregation_quarters = 1
    if days > 1 and aggregation_quarters != 4:
        errors.append(
            "sensor-output-fit för flera dagar måste deklarera timvis GEH")

    direction_pairs, direction_errors = _pairs_from_rows(
        audit.get("directions"), target_key="target_mean",
        raw_key="simulated_mean_raw", n_intervals=n_intervals,
        label="riktad sensor-fit",
        aggregation_quarters=aggregation_quarters)
    station_pairs, station_errors = _pairs_from_rows(
        audit.get("stations"), target_key="target_mean",
        raw_key="simulated_mean_raw", n_intervals=n_intervals,
        label="fysisk stations-fit",
        aggregation_quarters=aggregation_quarters)
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
    per_day = []
    if days > 1:
        daily_errors, per_day = _daily_output_fit_errors(
            audit, output_fit, n_intervals=n_intervals, days=days,
            aggregation_quarters=aggregation_quarters)
        errors.extend(daily_errors)
    return {"errors": errors, "directions": direction, "stations": station,
            "per_day": per_day}
