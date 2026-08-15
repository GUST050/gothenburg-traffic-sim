"""Date/window intake and measured-data loading — split from
build_sumo_demand.py 2026-07-14 (IMPROVEMENT_PLAN.md H1).

Owns: date-range validation, demand metadata, day-type classification
(2025 holiday calendar + 2027 mapping), real-day departure shapes,
multi-day block planning, sensor-edge/direction-split/target loading.
Patch SUMO_DIR/GEO_PATH HERE for these functions.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

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
                    n_variants: int, demand_spec: dict | None = None,
                    build_options: dict | None = None) -> dict:
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
        "note": (
            "Total sensor counts use the provenance-bound data-trained q50 "
            "direction split plus applicable local directional anchors; "
            "50/50 is only a missing-model fallback and q10/q90 remain "
            "explicit uncertainty variants."
            if direction_split.startswith("trained_q50") else
            "Total sensor counts use the Gate-M-selected maximum-entropy "
            "50/50 direction policy plus applicable local directional "
            "anchors; transferred-model q10/q90 values are diagnostic stress "
            "inputs only."
            if direction_split.startswith("constant_5050") else
            "Total sensor counts use the declared estimated direction split; "
            "direction is not measured in the delivered data."
        ),
    }
    if demand_spec is not None:
        meta["demand_spec"] = dict(demand_spec)
        meta["demand_build_key"] = demand_spec.get("build_key")
    if build_options is not None:
        meta["build_options"] = dict(build_options)
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


POOL_KEYS = ("weekday", "weekend")


def pool_key_for(day: pd.Timestamp) -> str:
    """The geometry-reuse class of one calendar day."""
    weekend, _kind = classify_day(day.strftime("%Y-%m-%d"), day.dayofweek)
    # Purpose logic is the same for weekend and holiday blocks, so that is
    # the safe geometry-reuse boundary.
    return "weekend" if weekend else "weekday"


def window_pool_composition(start: pd.Timestamp, days: int) -> tuple[str, ...]:
    """The set of day-type geometry pools a window's candidates draw from."""
    return tuple(sorted({
        pool_key_for(start + pd.Timedelta(days=offset)) for offset in range(days)
    }))


def day_pool_blocks(
    flows: dict[str, list],
    sensor_edges: dict[str, list[str]],
    day: pd.Timestamp,
    qi_start: int,
    composition: tuple[str, ...],
) -> list[dict]:
    """Candidate blocks for calibrating ONE calendar day inside a composition.

    Two things have to be true at once for a day calibrated alone to mean the
    same as that day inside a window (SPEED_ARCHITECTURE_PLAN stage B):

    * the PFE must see the same VARIABLE SET, which is the union of the
      day-type geometries the window draws from — the pool composition; and
    * the day's own quarters must receive only the day's own candidates,
      because the per-quarter purpose mix is read off candidate departures.

    So the day gets its real block at offset zero, and every other day type in
    the composition contributes one canonical template block parked a whole
    day BEYOND the calibrated window: far enough that
    ``_purpose_targets_per_quarter`` ignores it (it bins only quarters inside
    the window), close enough that it still carries geometry into the shape
    pool and its share into the structure guards.

    Exactly ONE block per day type is deliberate. Structure-guard caps are
    computed from each group's share of the candidate POOL
    (demand/structure.py), so repeating a day type once per calendar day —
    which is what a monolithic window does — makes a day's own guards depend
    on how many weekdays and weekend days happened to share its window. One
    block per type removes that: the guard reflects the day types in play,
    not the calendar arithmetic of the envelope.
    """
    from build_candidates import blend_day_shape, daily_shape

    own_key = pool_key_for(day)
    if own_key not in composition:
        raise ValueError(
            f"day {day:%Y-%m-%d} is {own_key} but the composition is "
            f"{composition}")
    weekend, kind = classify_day(day.strftime("%Y-%m-%d"), day.dayofweek)
    real = real_day_shape(flows, sensor_edges, qi_start)
    fallback = daily_shape(weekend)
    profile = blend_day_shape(real, fallback) if real is not None else fallback
    blocks = [{
        "profile": profile.tolist(), "offset_s": 0,
        "id_prefix": "d0_", "is_weekend": weekend,
        "date": day.strftime("%Y-%m-%d"), "pool_key": own_key,
    }]
    for index, key in enumerate(
            k for k in sorted(composition) if k != own_key):
        is_weekend = key == "weekend"
        blocks.append({
            "profile": daily_shape(is_weekend).tolist(),
            # One whole day past the calibrated 24 h window.
            "offset_s": (index + 1) * 86400,
            "id_prefix": f"pool{index}_", "is_weekend": is_weekend,
            # A canonical label, not a calendar date: this block exists to
            # carry a day type's geometry, so its draws must depend on the
            # day type alone.
            "date": f"pool-template:{key}", "pool_key": key,
            "shape_carrier": True,
        })
    origin = "real" if real is not None else "fallback"
    print(f"  day {day.strftime('%Y-%m-%d')} ({kind}): {origin} departure "
          f"shape, pool composition {'+'.join(sorted(composition))}")
    return blocks


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
            # The calendar date keys the block's departure draws, so the same
            # day yields the same candidates in every window it appears in
            # (SPEED_ARCHITECTURE_PLAN stage B).
            "date": day.strftime("%Y-%m-%d"),
            "pool_key": pool_key_for(day),
        })
        origin = "real" if real is not None else "fallback"
        print(f"  day {day.strftime('%Y-%m-%d')} ({kind}): {origin} departure shape")
    return blocks


def activity_purpose_shares_for_window(
    start: pd.Timestamp, n_intervals: int,
) -> list[dict[str, float]]:
    """Expand the documented activity-purpose model over a PFE window.

    Route repair/filtering can be purpose-selective. The PFE therefore takes
    ``P(purpose | hour, day type)`` from the calendar window itself, rather
    than inferring it from whichever valid routes happen to survive. The
    candidate generator remains the single source of truth for those shares;
    this helper only expands them over a continuous window, including a
    weekend or holiday boundary.
    """
    if n_intervals < 0:
        raise ValueError("number of purpose-share intervals must be non-negative")
    from build_candidates import purpose_shares_for_hour

    result: list[dict[str, float]] = []
    for quarter in range(n_intervals):
        at = start + quarter * INTERVAL
        is_weekend, _kind = classify_day(at.strftime("%Y-%m-%d"), at.dayofweek)
        result.append(dict(purpose_shares_for_hour(at.hour, is_weekend)))
    return result


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


SENSOR_REGISTRY_PATH = Path("data_in/sensors.json")


def _validated_direction_share_block(
        data: Any, key: str, *, path: Path,
) -> dict[str, list[float]]:
    """Flatten one complete, complementary direction arm.

    Diagnostic q arms are evidence inputs, not best-effort hints. A partial
    file previously fell back to q50 independently per station, so an
    explicitly requested q10/q90 run could silently be a hybrid stress case.
    Validate the exact two-edge, 96-slot contract before returning anything.
    """
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path} must contain a non-empty sensor object")

    flattened: dict[str, list[float]] = {}
    for sensor_id, record in sorted(data.items(), key=lambda item: str(item[0])):
        if not isinstance(record, dict):
            raise ValueError(
                f"{path} sensor {sensor_id!r} must contain an object")
        central = record.get("edge_shares")
        block = record.get(key)
        if not isinstance(central, dict) or len(central) != 2:
            raise ValueError(
                f"{path} sensor {sensor_id!r} needs exactly two central edges")
        if not isinstance(block, dict) or len(block) != 2:
            raise ValueError(
                f"{path} sensor {sensor_id!r} is missing a complete {key} arm")
        if set(block) != set(central):
            raise ValueError(
                f"{path} sensor {sensor_id!r} {key} edges differ from edge_shares")

        series_by_edge: dict[str, list[float]] = {}
        for edge_id, raw_series in block.items():
            if edge_id in flattened:
                raise ValueError(
                    f"{path} edge {edge_id!r} appears under multiple sensors")
            if not isinstance(raw_series, list) or len(raw_series) != 96:
                raise ValueError(
                    f"{path} sensor {sensor_id!r} edge {edge_id!r} {key} "
                    "must contain exactly 96 slots")
            values: list[float] = []
            for slot, raw_value in enumerate(raw_series):
                if isinstance(raw_value, bool):
                    raise ValueError(
                        f"{path} {sensor_id!r}/{edge_id}/{key}[{slot}] is boolean")
                try:
                    value = float(raw_value)
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"{path} {sensor_id!r}/{edge_id}/{key}[{slot}] is not numeric") \
                        from error
                if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                    raise ValueError(
                        f"{path} {sensor_id!r}/{edge_id}/{key}[{slot}] "
                        "must be finite and within [0, 1]")
                values.append(value)
            series_by_edge[str(edge_id)] = values

        left, right = series_by_edge.values()
        for slot, (a, b) in enumerate(zip(left, right)):
            if not math.isclose(a + b, 1.0, rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(
                    f"{path} sensor {sensor_id!r} {key}[{slot}] does not sum to 1")
        flattened.update(series_by_edge)
    return flattened


def _unsupported_day_fallback(data: Any, key: str) -> Any:
    """Maximum-entropy point plus broad interval for untrained weekends."""
    if not isinstance(data, dict):
        return data
    output = {}
    for sensor_id, record in data.items():
        if not isinstance(record, dict):
            output[sensor_id] = record
            continue
        central = record.get("edge_shares")
        if not isinstance(central, dict) or len(central) != 2:
            output[sensor_id] = record
            continue
        left, right = central
        if key == "edge_shares_q10":
            values = (0.1, 0.9)
        elif key == "edge_shares_q90":
            values = (0.9, 0.1)
        else:
            values = (0.5, 0.5)
        updated = dict(record)
        updated[key] = {
            left: [values[0]] * 96,
            right: [values[1]] * 96,
        }
        output[sensor_id] = updated
    return output


def load_direction_split(key: str = "edge_shares",
                         anchor_day: str | None = None,
                         anchor_flows: Mapping[str, list] | None = None,
                         anchor_epoch: Any = None,
                         ) -> dict[str, list[float]]:
    """{edge_id: [96 shares]} from the registered direction policy.

    ``edge_shares`` is the trained q50 point estimate, followed by any
    applicable Level-1 local anchor (currently sensor 107's published 52/48
    D-factor). 50/50 is the fallback only when no trained split exists.

    The explicit diagnostic keys select the legacy model stress cases:
    "edge_shares_q10"/"edge_shares_q90" (interval bounds from
    dirsplit/predict.py). They are retained for registered sensitivity work,
    but normal demand builds do not request them.

    ``anchor_day`` (Fas 0A) opts into the published local anchor. When a
    calendar day is supplied, any two-way station whose registry record
    carries a verified ``directional_reference`` covering that day has its
    estimated per-slot shares shifted so the declared period reproduces the
    published aggregate — sensor 107's 3 400/3 100, about 52/48, for 2025.

    Local evidence outranks a transferred model for the same aggregate
    quantity, but only for that quantity: the shift is a single offset over
    the whole series, so the per-slot values remain ESTIMATED and the time
    variation the model supports is preserved. Nothing here turns an annual
    D-factor into 96 measurements.

    ``anchor_flows``/``anchor_epoch`` are the measured-flow series and its
    epoch (typically the SAME ``flows``/epoch the caller is already using to
    build Level-1 targets). When given, the annual mean is matched
    VOLUME-WEIGHTED — a busy morning slot counts for more than an empty
    small-hours slot, which is what an annual average daily D-factor actually
    is — instead of an unweighted mean of 96 slot values. Omitting them falls
    back to uniform weights.

    ``anchor_day=None`` omits calendar-specific anchors and returns the
    trained central q50 policy for ``edge_shares``."""
    path = SUMO_DIR / "direction_split.json"
    if not path.exists() and anchor_day is None:
        return {}
    shares: dict[str, list[float]] = {}
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid direction split JSON: {path}") from error
        if anchor_day is not None and pd.Timestamp(anchor_day).dayofweek >= 5:
            # The model is trained on weekdays 06-20. Weekend point estimates
            # therefore fall back instead of silently replaying a weekday
            # pattern; q arms widen to the declared [0.1, 0.9] support.
            data = _unsupported_day_fallback(data, key)
        shares = _validated_direction_share_block(data, key, path=path)
    if anchor_day is not None and key == "edge_shares":
        # The anchor is LOCAL evidence and must not be conditional on
        # whether the transferred Norwegian model happens to be built: a
        # checkout without direction_split.json would otherwise fall back to
        # a flat 50/50 at sensor 107 while the city has published 52/48.
        # apply_directional_anchors seeds a flat base for any reference pair
        # the split file does not cover, so the anchor becomes the only
        # direction information rather than being dropped.
        shares = apply_directional_anchors(
            shares, anchor_day, flows=anchor_flows, epoch=anchor_epoch)
    return shares


def sensor_period_weights(reference, flows: Mapping[str, list],
                          epoch: Any = None) -> list[float] | None:
    """96-slot volume weights for a ``directional_reference``'s period.

    An annual average daily D-factor is a volume-weighted quantity, so the
    weight profile is derived from the SAME measured two-way total the
    reference's own raw counts came from. Per CLAUDE.md's "reporting trap",
    both directed edges of a two-way sensor carry the identical total each
    slot, so either bearing's series gives the same profile.

    Buckets every quarter by time-of-day (``qi % 96``) and averages across
    whichever days in ``flows`` fall inside the reference's declared period
    (``epoch`` locates each quarter on the calendar; without it every quarter
    in ``flows`` is used as-is, which is correct for this project's own
    full-year ``flows.json``). Returns ``None`` when nothing usable is found,
    so the caller can fall back to ``anchor_period_mean``'s uniform default.
    """
    edge_id = next(iter(reference.bearing_to_edge.values()), None)
    series = flows.get(edge_id) if edge_id else None
    if not series:
        return None
    epoch_ts = pd.Timestamp(epoch) if epoch is not None else None
    sums = [0.0] * 96
    counts = [0] * 96
    for qi, value in enumerate(series):
        if value is None:
            continue
        if epoch_ts is not None:
            day = (epoch_ts + qi * INTERVAL).strftime("%Y-%m-%d")
            if not reference.covers(day):
                continue
        slot = qi % 96
        sums[slot] += float(value)
        counts[slot] += 1
    if not any(counts):
        return None
    return [sums[i] / counts[i] if counts[i] else 0.0 for i in range(96)]


def apply_directional_anchors(shares: dict[str, list[float]], day: str,
                              registry_path: Path | None = None,
                              flows: Mapping[str, list] | None = None,
                              epoch: Any = None,
                              ) -> dict[str, list[float]]:
    """Anchor every two-way pair whose reference covers ``day``.

    Stations without a ``directional_reference`` are returned untouched, so
    the five single-direction sensors — whose measured direction is already a
    Level-1 target with nothing to anchor — cannot be affected by this path
    at all.

    A reference pair the caller did not supply is seeded with a FLAT even
    base and then anchored, rather than skipped. That is the case where no
    transferred model exists for the pair: skipping would leave sensor 107
    on a 50/50 fallback while the city has published 52/48, which is the
    published local measurement losing to a guess. The result carries no
    time variation, because none was measured — only the period level is
    local evidence.

    ``flows``/``epoch``, when supplied, derive volume weights
    (``sensor_period_weights``) so the period mean is matched the way an
    annual D-factor actually is — volume-weighted, not an unweighted mean of
    96 slot values. Omitting them keeps the previous uniform-weight
    behaviour.
    """
    from traffic_sim.intake.sensors import anchored_pair_shares, load_registry

    path = registry_path or SENSOR_REGISTRY_PATH
    if not path.exists():
        return shares
    registry = load_registry(path)
    anchored = dict(shares)
    for sensor_id, record in registry.records.items():
        reference = record.directional_reference
        if reference is None or not reference.covers(day):
            continue
        edges = sorted(reference.bearing_to_edge.values())
        base = {edge: list(anchored.get(edge) or [0.5] * 96) for edge in edges}
        weights = (sensor_period_weights(reference, flows, epoch)
                  if flows is not None else None)
        anchored.update(anchored_pair_shares(reference, base, weights=weights))
    return anchored


def has_split_quantiles() -> bool:
    path = SUMO_DIR / "direction_split.json"
    if not path.exists():
        return False
    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid direction split JSON: {path}") from error
    _validated_direction_share_block(data, "edge_shares_q10", path=path)
    _validated_direction_share_block(data, "edge_shares_q90", path=path)
    return True


def build_targets(
    flows: dict[str, list],
    sensor_edges: dict[str, list[str]],
    qi_start: int,
    n_intervals: int,
    split_key: str = "edge_shares",
    anchor_day: str | None = None,
    anchor_epoch: Any = None,
) -> list[dict[str, float]]:
    """Per-quarter measured targets {edge: count} — the level-1 constraints.

    ``anchor_day`` (Fas 0A) wires sensor 107's published local 52/48 anchor
    into the real Level-1 target path: when the build's own calendar date is
    passed here, any two-way station with a verified ``directional_reference``
    covering that day has its estimated split shifted to reproduce the
    published aggregate (see ``load_direction_split``/
    ``apply_directional_anchors``). ``flows`` doubles as the volume-weighting
    source — for a historical build it already IS the full 2025
    ``flows.json`` the reference's own raw counts came from. A 2027 forecast
    date is correctly refused: the reference's declared period is calendar
    year 2025, so ``anchor_day=None`` is only needed for callers that must
    reproduce the previous byte-identical behaviour (audits, LOSO folds).

    A multi-day build passes its START date. That is sufficient rather than
    approximate: the anchor constrains a PERIOD (calendar year 2025), and
    ``validate_date_range`` already forbids a range from leaving its source
    year, so every day of the build falls inside the same declared period.
    """
    est_shares = load_direction_split(
        split_key, anchor_day=anchor_day, anchor_flows=flows,
        anchor_epoch=anchor_epoch)
    out: list[dict[str, float]] = []
    for i in range(n_intervals):
        qi, slot = qi_start + i, (qi_start + i) % 96
        t: dict[str, float] = {}
        for edges in sensor_edges.values():
            for edge_id in edges:
                # Split ONLY a two-way total. A single-direction station
                # already measures one carriageway, so its value IS that
                # direction's count and must enter level 1 untouched.
                #
                # This guard became load-bearing on 2026-08-06, when the
                # direction model started predicting both carriageways at
                # every station. Its shares are pair-normalised, so a measured
                # edge suddenly resolved to ~0.5 here and a measured 50 would
                # have been calibrated as 25 -- silently, at 100% GEH against
                # the halved target. The estimated opposite carriageway is not
                # a level-1 target at all; it enters as a level-2 bound.
                share = (est_shares.get(edge_id, [1.0 / len(edges)] * 96)[slot]
                         if len(edges) > 1 else 1.0)
                v = flows.get(edge_id, [None])[qi] if qi < len(flows.get(edge_id, [])) else None
                if v is not None:
                    t[edge_id] = v * share
        out.append(t)
    return out


def target_series(targets_per_q: list[dict[str, float]]) -> dict[str, list[float | None]]:
    """Transpose per-quarter Level-1 targets into per-edge audit series.

    Missing measurements stay ``None``.  They must never become zero in an
    audit artifact, because zero means an observed zero-flow interval while
    ``None`` means the source did not constrain that edge-quarter at all.
    """
    edges = sorted({edge for targets in targets_per_q for edge in targets})
    return {
        edge: [targets.get(edge) for targets in targets_per_q]
        for edge in edges
    }


def observed_sensor_series(
    flows: dict[str, list], sensor_edges: dict[str, list[str]],
    qi_start: int, n_intervals: int,
) -> dict[str, list[float | None]]:
    """Freeze the source value used for each directed sensor edge.

    For a station delivered as a two-way ``Total``, the same physical-station
    value deliberately appears under both directed edges.  Consumers must use
    the network metadata to label it as a total, rather than misrepresenting
    it as a measured directional count.  Keeping this alongside the derived
    direction targets makes the distinction auditable after inputs change.
    """
    result: dict[str, list[float | None]] = {}
    for edges in sensor_edges.values():
        for edge_id in edges:
            values = flows.get(edge_id, [])
            result[edge_id] = [
                values[qi_start + offset] if qi_start + offset < len(values)
                else None
                for offset in range(n_intervals)
            ]
    return result


# Bounds/priors intake — moved to demand/priors.py (H1, 2026-07-14).
# Patch subprocess/GEO_PATH on demand.priors for these functions.
from demand.priors import (ensure_assignment_priors, ensure_bounds,
                           ensure_observability, ensure_priors,
                           structural_bounds_and_priors)
