"""Date/window intake and measured-data loading — split from
build_sumo_demand.py 2026-07-14 (IMPROVEMENT_PLAN.md H1).

Owns: date-range validation, demand metadata, day-type classification
(2025 holiday calendar + 2027 mapping), real-day departure shapes,
multi-day block planning, sensor-edge/direction-split/target loading.
Patch SUMO_DIR/GEO_PATH HERE for these functions.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from train_agent1 import HOLIDAY_DATES_2025
from build_agent1_flows import HOLIDAY_MAPPING_2027_TO_2025

GEO_PATH = Path("web/data/network.geojson")
SUMO_DIR = Path("sumo")
INTERVAL = pd.Timedelta(minutes=15)

def validate_date_range(start_date: str, days: int, source_year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return [start, end) after requiring the whole calendar range in one year."""
    if days < 1:
        raise ValueError("--days must be at least 1")
    try:
        start = pd.Timestamp(start_date)
    except (TypeError, ValueError):
        raise ValueError(f"--start-date must be YYYY-MM-DD, got {start_date!r}") from None
    if start.strftime("%Y-%m-%d") != start_date:
        raise ValueError(f"--start-date must be YYYY-MM-DD, got {start_date!r}")
    end_exclusive = start + pd.Timedelta(days=days)
    year_end = pd.Timestamp(year=source_year + 1, month=1, day=1)
    if start.year != source_year or end_exclusive > year_end:
        raise ValueError(
            f"date range {start.date()} through {(end_exclusive - pd.Timedelta(days=1)).date()} "
            f"crosses or lies outside the {source_year} source year")
    return start, end_exclusive


def demand_metadata(*, start_date: str, days: int, source: str, begin: str,
                    end: str, qi_start: int, n_intervals: int,
                    epoch_sim: pd.Timestamp, direction_split: str,
                    n_variants: int) -> dict:
    """Demand metadata contract; B2 will make multi-day calibration consume it."""
    start, end_exclusive = validate_date_range(start_date, days, epoch_sim.year)
    meta = {
        "start_date": start.strftime("%Y-%m-%d"),
        "days": days,
        "end_date_exclusive": end_exclusive.strftime("%Y-%m-%d"),
        "day_boundaries_s": [day * 86400 for day in range(days + 1)],
        "day_kinds": [classify_day(day.strftime("%Y-%m-%d"), day.dayofweek)[1]
                      for day in pd.date_range(start, periods=days, freq="D")],
        "source": source,
        "qi_start": qi_start,
        "n_intervals": n_intervals,
        # ISO with 'T' — Safari/Firefox reject "YYYY-MM-DD HH:MM" in new Date()
        "epoch_sim": epoch_sim.isoformat(),
        "direction_split": direction_split,
        "n_variants": n_variants,
        "note": "Total sensor counts split over the two directed edges using "
                "the estimated time-of-day split (estimate_directions.py); "
                "direction is not measured in the delivered data.",
    }
    # Legacy consumers deliberately retain their exact single-day fields.
    if days == 1:
        meta.update({"date": start_date, "begin": begin, "end": end})
    return meta


# Structure metrics/gates — moved to demand/structure.py (H1, 2026-07-14).
# Re-exported for existing callers; GEO_PATH and the geometry cache now
# live in demand.structure — monkeypatch THERE.
from demand.structure import (DEST_GROUP_CAP_MULT, LENGTH_BIN_EDGES_KM,
                              PURPOSE_LENGTH_MIN_N, STRUCTURE_FLAG_MULT,
                              calibrated_agent_summary,
                              calibrated_structure_report,
                              load_edge_geometry, purpose_lengths_km,
                              structure_groups_for_shapes)


def classify_day(date_str: str, dayofweek: int) -> tuple[bool, str]:
    """(use_weekend_shape, day_kind) for build_candidates.py's departure-
    time profile choice. A holiday on a weekday (Midsommarafton, Juldagen,
    ...) has nothing like a normal commute peak either — normal_profile.json
    has no separate holiday shape to read, so 'weekend' (later start, no
    sharp AM/PM peaks) is the closest real analog available, reusing Agent
    1's own HOLIDAY_DATES_2025/HOLIDAY_MAPPING_2027_TO_2025 rather than
    re-deciding what a holiday is a second time. Found 2026-07-09: the first
    weekday/weekend fix didn't check this, so a holiday Tuesday would still
    get the sharp commute shape. dayofweek: pandas convention, Mon=0..Sun=6."""
    is_weekend = dayofweek >= 5
    is_holiday = date_str in HOLIDAY_DATES_2025 or date_str in HOLIDAY_MAPPING_2027_TO_2025
    if is_weekend:
        return True, "weekend"
    if is_holiday:
        return True, "holiday"
    return False, "weekday"


REAL_DAY_SHAPE_MIN_VALID_HOURS = 18   # of 24 — below this, the day's own
                                     # data is too gappy to trust at all


def real_day_shape(flows: dict[str, list], sensor_edges: dict[str, list[str]],
                   qi_start: int) -> np.ndarray | None:
    """The REAL (or, for --source forecast, Agent 1's forecast) departure-
    time shape measured at the 6 sensors on the EXACT calendar day being
    simulated — not a bucket average. Directly captures whatever actually
    happened that day (a holiday, a school break that isn't a public
    holiday, a snow day, a local event, ...) without needing a maintained
    holiday list or any day-type classification at all: the real data
    already IS the classification. Falls back to None (caller blends with
    classify_day()'s smoothed average, or uses it outright) if too much of
    the day is missing to trust a single day's measurement.

    qi_start may point anywhere inside the target day (e.g. a 06:00-10:00
    window's start) — this always pulls the FULL 96-quarter day containing
    it, since departure-time shape must cover all 24 hours regardless of
    the calibration window."""
    day_qi_start = qi_start - (qi_start % 96)
    hourly = np.zeros(24)
    valid_hours = np.zeros(24, dtype=bool)
    for edges in sensor_edges.values():
        # A two-way Total sensor is exported onto both directed edges with
        # the same summed count. Count that physical station once. If a future
        # directional delivery contains genuinely different edge values, sum
        # the directions instead of silently discarding one of them.
        arrays = [flows.get(edge, []) for edge in edges]
        duplicate_total = len(arrays) > 1 and all(
            np.array_equal(arrays[index], arrays[0])
            for index in range(1, len(arrays))
        )
        for h in range(24):
            quarter_values: list[float] = []
            for qi in range(day_qi_start + h * 4, day_qi_start + h * 4 + 4):
                values = [arr[qi] for arr in arrays
                          if qi < len(arr) and arr[qi] is not None]
                if not values:
                    continue
                if duplicate_total:
                    quarter_values.append(float(values[0]))
                else:
                    quarter_values.append(float(sum(values)))
            if quarter_values:
                hourly[h] += sum(quarter_values) / len(quarter_values)
                valid_hours[h] = True
    if valid_hours.sum() < REAL_DAY_SHAPE_MIN_VALID_HOURS or hourly.sum() <= 0:
        return None
    return hourly / hourly.sum()


def multi_day_blocks(flows: dict[str, list], sensor_edges: dict[str, list[str]],
                     start: pd.Timestamp, days: int, qi_start: int) -> list[dict]:
    """Candidate-generator blocks with each calendar day's own profile.

    Geometry is pooled by the generator's actual behavioural day type, while
    profiles are intentionally not pooled: every block retains its exact-day
    measured/forecast departure shape.
    """
    from build_candidates import blend_day_shape, daily_shape

    blocks = []
    for day_index in range(days):
        day = start + pd.Timedelta(days=day_index)
        weekend, kind = classify_day(day.strftime("%Y-%m-%d"), day.dayofweek)
        real = real_day_shape(flows, sensor_edges, qi_start + day_index * 96)
        fallback = daily_shape(weekend)
        profile = blend_day_shape(real, fallback) if real is not None else fallback
        blocks.append({
            "profile": profile.tolist(), "offset_s": day_index * 86400,
            "id_prefix": f"d{day_index}_", "is_weekend": weekend,
            # Purpose logic is the same for weekend and holiday blocks, so
            # that is the safe geometry-reuse boundary.
            "pool_key": "weekend" if weekend else "weekday",
        })
        origin = "real" if real is not None else "fallback"
        print(f"  day {day.strftime('%Y-%m-%d')} ({kind}): {origin} departure shape")
    return blocks


def load_sensor_edges() -> dict[str, list[str]]:
    """{sensor_id: [edge_id, ...]} from network.geojson (1 or 2 edges)."""
    with open(GEO_PATH) as f:
        geo = json.load(f)
    result: dict[str, list[str]] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        if p.get("sensor_id"):
            result.setdefault(str(p["sensor_id"]), []).append(p["id"])
    return result


def load_direction_split(key: str = "edge_shares") -> dict[str, list[float]]:
    """{edge_id: [96 shares]} from the estimated split file, {} if not built.

    key selects the quantile: "edge_shares" (q50 point estimate) or
    "edge_shares_q10"/"edge_shares_q90" (interval bounds from
    dirsplit/predict.py — used to build demand VARIANTS so Monte Carlo
    includes direction uncertainty)."""
    path = SUMO_DIR / "direction_split.json"
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    shares: dict[str, list[float]] = {}
    for d in data.values():
        shares.update(d.get(key) or d["edge_shares"])
    return shares


def has_split_quantiles() -> bool:
    path = SUMO_DIR / "direction_split.json"
    if not path.exists():
        return False
    with open(path) as f:
        data = json.load(f)
    return any("edge_shares_q10" in d for d in data.values())


def build_targets(
    flows: dict[str, list],
    sensor_edges: dict[str, list[str]],
    qi_start: int,
    n_intervals: int,
    split_key: str = "edge_shares",
) -> list[dict[str, float]]:
    """Per-quarter measured targets {edge: count} — the level-1 constraints."""
    est_shares = load_direction_split(split_key)
    out: list[dict[str, float]] = []
    for i in range(n_intervals):
        qi, slot = qi_start + i, (qi_start + i) % 96
        t: dict[str, float] = {}
        for edges in sensor_edges.values():
            for edge_id in edges:
                share = est_shares.get(edge_id, [1.0 / len(edges)] * 96)[slot]
                v = flows.get(edge_id, [None])[qi] if qi < len(flows.get(edge_id, [])) else None
                if v is not None:
                    t[edge_id] = v * share
        out.append(t)
    return out


# Bounds/priors intake — moved to demand/priors.py (H1, 2026-07-14).
# Patch subprocess/GEO_PATH on demand.priors for these functions.
from demand.priors import (ensure_assignment_priors, ensure_bounds,
                           ensure_observability, ensure_priors,
                           structural_bounds_and_priors)
