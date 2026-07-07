"""
Export Agent 1 predictions for 2027 → web/data/flows_forecast.json

Holiday Baseline Adjustment method:
  - Non-holiday days:  prediction = baseline_model(2027 date/time)
  - Holiday days:      prediction = baseline_model(2027 date/time)
                                  × holiday_factor(matching 2025 holiday, slot)
                                  × dow_correction(slot)  [only when needed]

The factor absorbs the full holiday deviation (low morning, NYE midnight spike,
etc.) from actual 2025 observations.

CORRECTED 2026-07-09 (previously claimed here since inception: "the baseline
automatically handles the day-of-week shift between years ... no manual
corrections needed — factors capture everything" — FALSE, found while testing
real_day_shape() against all 30 2025/2027 holidays): the stored factor is
actual_2025[slot] / baseline(SOURCE holiday's OWN actual day-of-week, slot) —
only valid to reapply unchanged when the mapped 2027 date shares that same
day-of-week. When it doesn't (Första maj: Thursday 2025 -> Saturday 2027), the
factor was calibrated against the wrong baseline shape and badly distorts the
result (2027-05-01 forecast peaked at 01:00, near-zero at 06-08). dow_correction
(dow_correction_ratios() below) re-derives the factor relative to the TARGET's
own day-of-week instead, and is a no-op (ratio 1.0) whenever the two dates
already share a day-of-week — the majority case, unaffected by this fix.

Reads:
  web/data/agent1/models/sensor_*.pkl
  web/data/agent1/holiday_factors.json
  web/data/network.geojson

Writes:
  web/data/flows_forecast.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from train_agent1 import Agent1, build_features, load_agent1

GEOJSON_PATH   = Path("web/data/network.geojson")
OUT_PATH       = Path("web/data/flows_forecast.json")
FACTORS_PATH   = Path("web/data/agent1/holiday_factors.json")
EPOCH_2027     = pd.Timestamp("2027-01-01T00:00:00")
T              = 365 * 96   # 35 040 quarter-hour slots

# Maps each 2027 holiday date → the corresponding 2025 holiday date.
# "Corresponding" means same holiday TYPE (same ecclesiastical event),
# not same calendar date. This lets the factor capture the holiday's
# traffic profile while the baseline handles day-of-week correctly.
HOLIDAY_MAPPING_2027_TO_2025: dict[str, str] = {
    "2027-01-01": "2025-01-01",   # Nyårsdagen
    "2027-01-06": "2025-01-06",   # Trettondedag jul
    "2027-03-26": "2025-04-18",   # Långfredag        (Easter cycle)
    "2027-03-28": "2025-04-20",   # Påskdagen
    "2027-03-29": "2025-04-21",   # Annandag påsk
    "2027-05-01": "2025-05-01",   # Första maj
    "2027-05-06": "2025-05-29",   # Kristi Himmelsfärdsdag
    "2027-05-16": "2025-06-08",   # Pingstdagen
    "2027-06-06": "2025-06-06",   # Sveriges nationaldag
    "2027-06-26": "2025-06-20",   # Midsommardagen
    "2027-11-06": "2025-11-01",   # Alla helgons dag
    "2027-12-24": "2025-12-24",   # Julafton
    "2027-12-25": "2025-12-25",   # Juldagen
    "2027-12-26": "2025-12-26",   # Annandag jul
    "2027-12-31": "2025-12-31",   # Nyårsafton
}

# Precompute: date object → 2025 date string (for fast per-slot lookup)
_DATE_TO_FACTOR_KEY: dict = {
    pd.Timestamp(d27).date(): d25
    for d27, d25 in HOLIDAY_MAPPING_2027_TO_2025.items()
}


def dow_correction_ratios(agent1: Agent1, source_date: str, target_date: str
                         ) -> dict[str, list[float]]:
    """Per-sensor, per-slot multiplicative correction:
    baseline(source's own day-of-week, slot) / baseline(target's day-of-
    week, slot). holiday_factors.json's stored factor was computed as
    actual_2025[slot] / baseline(SOURCE's own actual day-of-week, slot) —
    correct for a real holiday, but only valid to reapply as-is when the
    TARGET year's mapped date falls on the SAME day-of-week. When it
    doesn't (e.g. Första maj: Thursday in 2025, Saturday in 2027), the
    factor was calibrated against the wrong baseline shape entirely.

    FOUND 2026-07-09 (testing real_day_shape() against all 30 2025/2027
    holidays): 2027-05-01 forecast peaked at 01:00 and dropped to near-
    zero at 06-08 — because 2025-05-01's factor (2-4x overnight, since a
    normal THURSDAY baseline is near-zero then; 0.17-0.34x at the 6-9
    commute hours, since a normal Thursday commutes) got multiplied onto
    a SATURDAY baseline that's already only modestly low overnight and
    already has no commute peak to suppress — double-distorting both ends.

    Returns {} (meaning: no correction, ratio 1.0) when the two dates
    share a day-of-week — the common case (this file's own docstring had
    claimed since inception 'the baseline automatically handles the
    day-of-week shift... no manual corrections needed', which is simply
    false whenever the mapped dates don't share a day-of-week; this file
    is the actual mechanism that has to do that work)."""
    source_dow = pd.Timestamp(source_date).dayofweek
    target_dow = pd.Timestamp(target_date).dayofweek
    if source_dow == target_dow:
        return {}
    month = pd.Timestamp(source_date).month
    ratios: dict[str, list[float]] = {sid: [] for sid in agent1.sensor_ids}
    for slot in range(96):
        hour = slot * 0.25
        source_pred = agent1.predict_synthetic(hour, source_dow, month)
        target_pred = agent1.predict_synthetic(hour, target_dow, month)
        for sid in agent1.sensor_ids:
            tp = target_pred[sid]
            ratios[sid].append(source_pred[sid] / tp if tp >= 1.0 else 1.0)
    return ratios


def apply_holiday_factors(
    ts: pd.DatetimeIndex,
    baseline: np.ndarray,
    factors: dict,
    sensor_id: str,
    dow_corrections: dict[str, dict[str, list[float]]] | None = None,
) -> np.ndarray:
    """
    Multiply baseline by holiday factor for each slot that falls on a holiday.
    Uses per-sensor factors when available, falls back to cross-sensor average.

    dow_corrections: {2025_source_date: {sensor_id: [96 ratios]}} from
    dow_correction_ratios(), applied on top of the stored factor whenever
    the mapped target date's day-of-week differs from the source's.
    """
    out = baseline.copy()
    for i, t in enumerate(ts):
        factor_key = _DATE_TO_FACTOR_KEY.get(t.date())
        if factor_key is None:
            continue   # non-holiday slot — use baseline as-is

        day_factors = factors.get(factor_key)
        if day_factors is None:
            continue   # holiday not in factors (shouldn't happen)

        slot_idx = t.hour * 4 + t.minute // 15   # 0–95
        # Per-sensor preferred; fall back to cross-sensor average
        per_sensor = day_factors.get(sensor_id) or day_factors.get("avg")
        if per_sensor and slot_idx < len(per_sensor):
            factor = per_sensor[slot_idx]
            if dow_corrections:
                day_corr = dow_corrections.get(factor_key)
                if day_corr:
                    corr = day_corr.get(sensor_id)
                    if corr:
                        factor *= corr[slot_idx]
            out[i] *= factor

    return out


def main() -> None:
    ts     = pd.date_range(start=EPOCH_2027, periods=T, freq="15min")
    agent1 = load_agent1()
    print(f"Loaded Agent 1 — sensors: {agent1.sensor_ids}")

    print("Loading holiday factors …")
    with open(FACTORS_PATH) as f:
        factors: dict = json.load(f)
    print(f"  {len(factors)} holiday days loaded")

    print("Computing day-of-week corrections for mismatched holiday mappings …")
    dow_corrections: dict[str, dict[str, list[float]]] = {}
    for d27, d25 in HOLIDAY_MAPPING_2027_TO_2025.items():
        ratios = dow_correction_ratios(agent1, d25, d27)
        if ratios:
            dow_corrections[d25] = ratios
            print(f"  {d25} ({pd.Timestamp(d25).day_name()}) -> {d27} "
                  f"({pd.Timestamp(d27).day_name()}): correction applied")
    if not dow_corrections:
        print("  none needed — every mapping shares a day-of-week")

    print("Building 2027 baseline features (no holiday flags) …")
    feat_df = build_features(T, epoch=EPOCH_2027)

    print("Predicting …")
    sensor_preds: dict[str, list[int]] = {}
    for sid, model in agent1._models.items():
        baseline   = model.predict(feat_df)
        adjusted   = apply_holiday_factors(ts, baseline, factors, sid, dow_corrections)
        sensor_preds[sid] = [max(0, round(float(v))) for v in adjusted]

    # ── Spot-check ────────────────────────────────────────────────────────────
    sid_check = "107"
    if sid_check in agent1._models:
        model_107 = agent1._models[sid_check]
        raw_107   = model_107.predict(feat_df)
        adj_107   = apply_holiday_factors(ts, raw_107, factors, sid_check, dow_corrections)

        checks = [
            ("Julafton     Dec 24 10:00", "2027-12-24 10:00"),
            ("Juldag       Dec 25 12:00", "2027-12-25 12:00"),
            ("Nyårsafton   Dec 31 22:00", "2027-12-31 22:00"),
            ("Nyårsafton   Dec 31 23:00", "2027-12-31 23:00"),
            ("Nyårsafton   Dec 31 23:15", "2027-12-31 23:15"),
            ("Nyårsafton   Dec 31 23:30", "2027-12-31 23:30"),
            ("Nyårsafton   Dec 31 23:45", "2027-12-31 23:45"),
            ("Nyårsdagen   Jan  1 00:00", "2027-01-01 00:00"),
            ("Nyårsdagen   Jan  1 00:15", "2027-01-01 00:15"),
            ("Nyårsdagen   Jan  1 00:30", "2027-01-01 00:30"),
            ("Nyårsdagen   Jan  1 00:45", "2027-01-01 00:45"),
            ("Nyårsdagen   Jan  1 07:30", "2027-01-01 07:30"),
            ("Midsommar    Jun 26 14:00", "2027-06-26 14:00"),
            ("Nationaldag  Jun  6 17:00", "2027-06-06 17:00"),
            ("Påskdag      Mar 28 12:00", "2027-03-28 12:00"),
            ("Första maj   Maj  1 12:00", "2027-05-01 12:00"),
            ("Normal mån   Mar  1 08:00", "2027-03-01 08:00"),
            ("Normal fre   Apr  2 17:00", "2027-04-02 17:00"),
        ]
        print(f"\n  Sensor 107 spot-check:")
        print(f"  {'Tidpunkt':<30}  {'Baseline':>8}  {'Justerad':>8}  {'Faktor':>7}")
        for label, t_str in checks:
            idx    = ts.get_loc(pd.Timestamp(t_str))
            base   = raw_107[idx]
            adj    = adj_107[idx]
            factor = adj / base if base > 0 else 1.0
            print(f"  {label:<30}  {base:>8.1f}  {adj:>8.1f}   ×{factor:.3f}")

    # ── Write output ──────────────────────────────────────────────────────────
    with open(GEOJSON_PATH) as f:
        geojson = json.load(f)

    sensor_to_edges: dict[str, list[str]] = {}
    for feat in geojson["features"]:
        if feat["geometry"]["type"] != "LineString":
            continue
        props = feat["properties"]
        sid = props.get("sensor_id")
        eid = props.get("id")
        if sid and eid:
            sensor_to_edges.setdefault(str(sid), []).append(eid)

    flows: dict[str, list[int]] = {}
    for sid, edges in sensor_to_edges.items():
        if sid not in sensor_preds:
            print(f"  WARNING: no model for sensor {sid} — skipping")
            continue
        for eid in edges:
            flows[eid] = sensor_preds[sid]

    payload = {
        "epoch":            EPOCH_2027.isoformat(),
        "interval_minutes": 15,
        "generated_at":     pd.Timestamp.now().isoformat(timespec="seconds"),
        "flows":            flows,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"\nWrote {OUT_PATH}  ({size_kb:.0f} KB, {len(flows)} edges × {T} slots)")


if __name__ == "__main__":
    main()
