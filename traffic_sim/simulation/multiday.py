"""Validation helpers for continuous multi-day SUMO runs.

SUMO's summary output is intentionally used instead of tripinfo here.  It is
small, periodic, and carries cumulative vehicle counters, so a seven-day run
does not incur the large per-vehicle artifact and parsing cost of a metrics
run.  The helpers are pure JSON/XML adapters; they do not decide whether a
scenario is useful, only whether its continuity evidence is complete.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def _number(attrs: dict[str, str], name: str) -> float | None:
    raw = attrs.get(name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parse_summary(path: Path, *, day_boundaries_s: list[int], days: int) -> dict:
    """Convert cumulative SUMO summary steps into one record per calendar day.

    A day record is a delta of cumulative counters between its boundaries,
    plus the queue snapshot at the end boundary.  SUMO may omit a boundary
    step, so the nearest step at or before each boundary is used and recorded;
    missing or out-of-order boundaries make the result incomplete rather than
    silently inventing zeroes.
    """
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "complete": False,
                "reason": "summary output saknas", "days": []}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {"schema_version": SCHEMA_VERSION, "complete": False,
                "reason": "summary output är ogiltig XML", "days": []}

    steps: list[dict[str, Any]] = []
    for element in root.findall("step"):
        time = _number(element.attrib, "time")
        if time is None:
            continue
        steps.append({"time_s": time, **{
            key: _number(element.attrib, key)
            for key in ("loaded", "inserted", "teleports", "collisions",
                        "running", "waiting", "halting")
        }})
    steps.sort(key=lambda item: item["time_s"])
    boundaries = [int(value) for value in day_boundaries_s]
    reasons: list[str] = []
    if len(boundaries) != days + 1 or boundaries[0] != 0:
        reasons.append("day_boundaries_s har fel längd eller börjar inte vid 0")
    if any(b <= a for a, b in zip(boundaries, boundaries[1:])):
        reasons.append("day_boundaries_s är inte strikt växande")

    zero = {"time_s": 0.0, **{key: 0.0 for key in
                              ("loaded", "inserted", "teleports", "collisions",
                               "running", "waiting", "halting")}}

    def at_or_before(boundary: int) -> dict | None:
        found = None
        for step in steps:
            if step["time_s"] <= boundary + 1e-6:
                found = step
            else:
                break
        return found

    day_rows: list[dict] = []
    for day in range(days):
        start_s, end_s = boundaries[day], boundaries[day + 1]
        before, after = at_or_before(start_s), at_or_before(end_s)
        # SUMO omits empty leading periods.  A zero baseline at t=0 is exact;
        # any later missing boundary is a continuity error.
        if before is None and start_s == 0:
            before = zero
        if before is None or after is None:
            reasons.append(f"summary saknar steg för dag {day + 1}")
            continue
        if after["time_s"] < before["time_s"]:
            reasons.append(f"summary har omvänd tidsordning för dag {day + 1}")
            continue
        row: dict[str, Any] = {
            "day": day + 1,
            "begin_s": start_s,
            "end_s": end_s,
            "summary_begin_s": round(before["time_s"], 3),
            "summary_end_s": round(after["time_s"], 3),
            "boundary_lag_s": round(end_s - after["time_s"], 3),
        }
        for key in ("loaded", "inserted", "teleports", "collisions"):
            a, b = before.get(key), after.get(key)
            row[f"{key}_delta"] = (int(round(b - a))
                                    if a is not None and b is not None and b >= a
                                    else None)
        for key in ("running", "waiting", "halting"):
            value = after.get(key)
            row[f"{key}_at_boundary"] = (int(round(value))
                                          if value is not None and value >= 0
                                          else None)
        day_rows.append(row)

    complete = not reasons and len(day_rows) == days
    return {"schema_version": SCHEMA_VERSION, "complete": complete,
            "days": day_rows, "reasons": reasons,
            "summary_steps": len(steps),
            "day_boundaries_s": boundaries}


def validate(payload: dict, *, days: int, day_boundaries_s: list[int]) -> list[str]:
    """Return fail-closed errors for a published multi-day artifact."""
    errors: list[str] = []
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("multi_day_validation schema saknas eller är fel")
        return errors
    if payload.get("complete") is not True:
        errors.extend(str(reason) for reason in payload.get("reasons", [])
                      if reason)
        if not errors:
            errors.append("multi_day_validation är inte komplett")
    if payload.get("day_boundaries_s") != [int(v) for v in day_boundaries_s]:
        errors.append("multi_day_validation har andra daggränser än demand")
    rows = payload.get("per_day")
    if not isinstance(rows, list) or len(rows) != days:
        errors.append("multi_day_validation saknar en aggregerad post per dag")
        return errors
    for expected, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get("day") != expected:
            errors.append(f"multi_day_validation saknar dag {expected}")
            continue
        for key in ("seed_count", "loaded_delta_min", "inserted_delta_min",
                    "teleports_delta_max"):
            if row.get(key) is None:
                errors.append(f"multi_day_validation saknar {key} för dag {expected}")
    seeds = payload.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        errors.append("multi_day_validation saknar fröbevis")
    elif payload.get("seed_count") != len(seeds):
        errors.append("multi_day_validation har fel antal fröbevis")
    return errors


def aggregate(seed_payloads: list[dict], *, days: int,
              day_boundaries_s: list[int]) -> dict:
    """Build a compact gate record from every seed's daily summary."""
    valid = [item for item in seed_payloads
             if isinstance(item, dict) and item.get("complete") is True]
    rows: list[dict] = []
    reasons: list[str] = []
    for day in range(1, days + 1):
        day_rows = [item["days"][day - 1] for item in valid
                    if len(item.get("days", [])) >= day]
        if len(day_rows) != len(seed_payloads):
            reasons.append(f"dag {day} saknar komplett sammanfattning för ett frö")
        def values(key: str) -> list[int]:
            return [int(row[key]) for row in day_rows if row.get(key) is not None]
        loaded, inserted, teleports = values("loaded_delta"), values("inserted_delta"), values("teleports_delta")
        rows.append({
            "day": day,
            "seed_count": len(day_rows),
            "loaded_delta_min": min(loaded) if loaded else None,
            "inserted_delta_min": min(inserted) if inserted else None,
            "teleports_delta_max": max(teleports) if teleports else None,
            "queue_at_boundary_max": max(
                [int(row.get("waiting_at_boundary", 0) or 0) for row in day_rows],
                default=None),
        })
    complete = not reasons and len(valid) == len(seed_payloads) and len(rows) == days
    return {
        "schema_version": SCHEMA_VERSION,
        "days": days,
        "day_boundaries_s": [int(value) for value in day_boundaries_s],
        "complete": complete,
        "seed_count": len(seed_payloads),
        "reasons": reasons,
        "seeds": seed_payloads,
        "per_day": rows,
    }
