"""
Train the direction-split model with leave-city-out validation.

Usage (after dataset.py):
  python3 -m dirsplit.train

Model: LightGBM regression, target = direction share (0–1) per
(station-direction, hour, day-type). Inputs = the shared edge features +
profile-shape features + hour/day-type — everything computable for the
Gothenburg sensor edges too.

Validation: LEAVE-CITY-OUT — train on three cities, predict the fourth.
This directly measures what we will do to Gothenburg (predict an unseen
city), so the reported MAE is an honest transfer error, not a fit score.
Baseline to beat: always predicting 0.5.

Writes:
  data/dirsplit/model.pkl          — final model trained on ALL cities
  data/dirsplit/train_report.json  — per-city MAE, baseline, feature importance
"""

from __future__ import annotations

import csv
import json
import pickle

import numpy as np

from .config import DATA_DIR
from .dataset import OUT_PATH as TABLE_PATH
from .dataset import PROFILE_FEATURES
from .features import FEATURE_NAMES

MODEL_PATH  = DATA_DIR / "model.pkl"
REPORT_PATH = DATA_DIR / "train_report.json"

INPUT_COLS = FEATURE_NAMES + PROFILE_FEATURES + ["hour_sin", "hour_cos", "is_weekend"]


def load_table() -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray]:
    """Returns (X, y, cities, sample_weight). Weight = sqrt(n_obs).

    Training-set filters (the target problem is TWO-WAY city streets in
    commute hours — the training set must look like the problem):
      - drop STATIONS that are effectively one-way (any direction's mean
        daytime share > 0.85): ramps and split carriageways have shares
        near 0/1 and teach nothing about splitting a two-way total
      - weekdays only, 06–20 — where the commute signal lives; night
        shares are noise even after the volume filter
    """
    with open(TABLE_PATH) as f:
        rows = list(csv.DictReader(f))

    # Station-level one-way screen on daytime weekday shares
    from collections import defaultdict
    day_shares = defaultdict(list)
    for r in rows:
        if r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20:
            day_shares[(r["station_id"], r["heading"])].append(float(r["share"]))
    oneway_stations = {
        sid for (sid, _), shares in day_shares.items()
        if shares and (np.mean(shares) > 0.85 or np.mean(shares) < 0.15)
    }

    X_rows, y, cities, w = [], [], [], []
    for r in rows:
        if r["station_id"] in oneway_stations:
            continue
        if r["is_weekend"] != "0" or not (6 <= int(r["hour"]) <= 20):
            continue
        hour = float(r["hour"])
        feats = ([float(r[c]) for c in FEATURE_NAMES]
                 + [float(r[c]) for c in PROFILE_FEATURES]
                 + [np.sin(2 * np.pi * hour / 24),
                    np.cos(2 * np.pi * hour / 24),
                    float(r["is_weekend"])])
        X_rows.append(feats)
        y.append(float(r["share"]))
        cities.append(r["city"])
        w.append(float(r["n_obs"]) ** 0.5)
    print(f"Filter: {len(oneway_stations)} one-way-ish stations dropped, "
          f"{len(y)} rows kept (weekday 06–20)")
    return (np.array(X_rows), np.array(y), cities, np.array(w))


def make_model():
    import lightgbm as lgb
    return lgb.LGBMRegressor(
        n_estimators=600, learning_rate=0.04,
        max_depth=6, num_leaves=63,
        subsample=0.8, colsample_bytree=0.8,
        min_child_samples=20, random_state=42,
        n_jobs=-1, verbose=-1,
    )


def main() -> None:
    X, y, cities, w = load_table()
    city_set = sorted(set(cities))
    print(f"{len(y)} rows, {len(city_set)} cities: {city_set}")
    if len(city_set) < 2:
        print("WARNING: <2 cities — leave-city-out impossible; fit score only.")

    report: dict = {"n_rows": len(y), "cities": {}, "input_cols": INPUT_COLS}

    # ── Leave-city-out ─────────────────────────────────────────────────────────
    for held in city_set:
        tr = np.array([c != held for c in cities])
        te = ~tr
        if tr.sum() == 0 or te.sum() == 0:
            continue
        mdl = make_model()
        mdl.fit(X[tr], y[tr], sample_weight=w[tr])
        pred = np.clip(mdl.predict(X[te]), 0.0, 1.0)
        mae      = float(np.mean(np.abs(pred - y[te])))
        base_mae = float(np.mean(np.abs(0.5 - y[te])))
        report["cities"][held] = {
            "n_test": int(te.sum()),
            "mae": round(mae, 4),
            "baseline_50_50_mae": round(base_mae, 4),
            "improvement_pct": round(100 * (1 - mae / base_mae), 1) if base_mae else 0,
        }
        print(f"  hold out {held:<10}  MAE {mae:.4f}  (50/50-baseline {base_mae:.4f}, "
              f"{report['cities'][held]['improvement_pct']:+.1f}%)")

    # ── Final model on all cities ──────────────────────────────────────────────
    final = make_model()
    final.fit(X, y, sample_weight=w)
    importance = sorted(
        zip(INPUT_COLS, final.feature_importances_.tolist()),
        key=lambda t: -t[1],
    )
    report["feature_importance"] = importance
    print("\nTop features:", [f"{n}:{v}" for n, v in importance[:6]])

    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": final, "input_cols": INPUT_COLS}, f)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=1)
    print(f"Wrote {MODEL_PATH} + {REPORT_PATH}")


if __name__ == "__main__":
    main()
