"""
Agent 1 — Typical-day demand model (Holiday Baseline Adjustment).

Two-stage approach confirmed by electricity load forecasting literature
(ENTSO-E, Hyndman & Athanasopoulos FPP3, Taylor & Letham 2018):

  Stage 1 — Baseline model
    Train LightGBM on NON-holiday days only.
    7 features: hour, day-of-week, month. NO holiday flags.
    Learns: "what does normal traffic look like at this hour/weekday/month?"

  Stage 2 — Holiday factors
    factor(holiday_date, slot) = actual_2025(slot) / baseline_pred(slot)
    Stored per sensor (96 values per holiday day).
    Captures the holiday's deviation from its normal-day baseline.

For 2027 forecast:
    pred(holiday_2027, slot) = baseline_model(Friday-December, slot)
                              × factor(julafton_2025, slot)

Why this is correct:
  - Day-of-week adjustment is automatic: the baseline model knows 2027 Julafton
    is a Friday; it predicts what a typical Friday in December looks like.
  - Holiday deviation comes from actual observation: factor absorbs everything
    (early closure, low commuter demand, late-night events).
  - The collinearity problem is eliminated: named features confound "Christmas
    Eve" with "Wednesday-in-late-December" (single year of data). Ratio method
    separates these effects by construction.

Reads:
  web/data/features/flow_matrix.npz

Writes:
  web/data/agent1/models/sensor_<id>.pkl
  web/data/agent1/holiday_factors.json   — per-sensor 15-min factors
  web/data/agent1/results.json
  web/data/agent1/feature_names.json
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

FEATURES_DIR = Path("web/data/features")
OUT_DIR      = Path("web/data/agent1")
SEASONAL_LAG = 672   # 1 week = 7 × 96
EPOCH        = pd.Timestamp("2025-01-01T00:00:00")
INTERVAL     = pd.Timedelta(minutes=15)

# ── Holiday dates to EXCLUDE from training ────────────────────────────────────
# All official Swedish public holidays + Julafton + Nyårsafton.
# These are structurally different from normal days; including them contaminates
# the baseline model's weekday/weekend/seasonal signal.
HOLIDAY_DATES_2025: set[str] = {
    "2025-01-01",   # Nyårsdagen
    "2025-01-06",   # Trettondedag jul
    "2025-04-18",   # Långfredag
    "2025-04-20",   # Påskdagen
    "2025-04-21",   # Annandag påsk
    "2025-05-01",   # Första maj
    "2025-05-29",   # Kristi Himmelsfärdsdag
    "2025-06-06",   # Sveriges nationaldag
    "2025-06-08",   # Pingstdagen
    "2025-06-20",   # Midsommardagen
    "2025-11-01",   # Alla helgons dag
    "2025-12-24",   # Julafton  (not official but structurally different)
    "2025-12-25",   # Juldagen
    "2025-12-26",   # Annandag jul
    "2025-12-31",   # Nyårsafton  (not official but structurally different)
}

_HOLIDAY_DATE_OBJS = {pd.Timestamp(d).date() for d in HOLIDAY_DATES_2025}


# ── Features ──────────────────────────────────────────────────────────────────

def build_features(n: int, *, epoch: pd.Timestamp = EPOCH) -> pd.DataFrame:
    """
    Build (n, 7) baseline feature matrix — NO holiday flags.

    The baseline intentionally knows nothing about holidays; deviations are
    captured by holiday_factors.json at inference time.
    """
    ts = pd.date_range(start=epoch, periods=n, freq=INTERVAL)
    h  = ts.hour + ts.minute / 60
    dw = ts.dayofweek
    mo = ts.month
    return pd.DataFrame({
        "hour_sin":   np.sin(2 * np.pi * h  / 24),
        "hour_cos":   np.cos(2 * np.pi * h  / 24),
        "dow_sin":    np.sin(2 * np.pi * dw /  7),
        "dow_cos":    np.cos(2 * np.pi * dw /  7),
        "month_sin":  np.sin(2 * np.pi * (mo - 1) / 12),
        "month_cos":  np.cos(2 * np.pi * (mo - 1) / 12),
        "is_weekend": (dw >= 5).astype(float),
    })


def features_for_datetime(dt: pd.Timestamp) -> pd.DataFrame:
    """One-row feature DataFrame for a single datetime."""
    h  = dt.hour + dt.minute / 60
    dw = dt.dayofweek
    mo = dt.month
    return pd.DataFrame([{
        "hour_sin":   float(np.sin(2 * np.pi * h  / 24)),
        "hour_cos":   float(np.cos(2 * np.pi * h  / 24)),
        "dow_sin":    float(np.sin(2 * np.pi * dw /  7)),
        "dow_cos":    float(np.cos(2 * np.pi * dw /  7)),
        "month_sin":  float(np.sin(2 * np.pi * (mo - 1) / 12)),
        "month_cos":  float(np.cos(2 * np.pi * (mo - 1) / 12)),
        "is_weekend": float(dw >= 5),
    }])


# ── Imputation ────────────────────────────────────────────────────────────────

def impute_column(y: np.ndarray) -> np.ndarray:
    """Forward-fill, then column mean for the leading gap. Returns float64, no NaN.

    After ffill only NaN before the first observation can remain, and a
    seasonal lookback lands inside that same gap — so mean fill is the only
    step that can help there.
    """
    s = pd.Series(y.astype(float))
    s = s.ffill()
    s = s.fillna(s.mean())
    return s.to_numpy(dtype=np.float64)


# ── Validation ────────────────────────────────────────────────────────────────

def leave_weeks_out_mae(
    model_fn,
    X: pd.DataFrame,
    y: np.ndarray,
    orig_mask: np.ndarray,
    holiday_mask: np.ndarray,
    held_out_weeks: tuple[int, ...] = (8, 20, 32, 44),
) -> float:
    """
    4-fold CV: hold out one week per season.
    Train on non-holiday slots only. Evaluate on real non-holiday slots.
    """
    maes = []
    n = len(X)
    for w in held_out_weeks:
        start = w * 7 * 96
        end   = min(start + 7 * 96, n)
        if end <= start:
            continue
        val_idx   = np.arange(start, end)
        train_idx = np.concatenate([np.arange(0, start), np.arange(end, n)])

        # Train only on non-holiday slots
        train_ok = train_idx[~holiday_mask[train_idx]]
        mdl = model_fn()
        mdl.fit(X.iloc[train_ok], y[train_ok])

        # Evaluate on real non-holiday slots in held-out week
        val_ok = val_idx[orig_mask[val_idx] & ~holiday_mask[val_idx]]
        if len(val_ok):
            maes.append(float(np.mean(np.abs(mdl.predict(X.iloc[val_ok]) - y[val_ok]))))

    return float(np.mean(maes)) if maes else float("nan")


def seasonal_naive_mae(
    y: np.ndarray,
    orig_mask: np.ndarray,
    holiday_mask: np.ndarray,
) -> float:
    """Seasonal naïve on non-holiday slots only."""
    errors = []
    for t in range(SEASONAL_LAG, len(y)):
        if orig_mask[t] and orig_mask[t - SEASONAL_LAG] \
                and not holiday_mask[t] and not holiday_mask[t - SEASONAL_LAG]:
            errors.append(abs(float(y[t]) - float(y[t - SEASONAL_LAG])))
    return float(np.mean(errors)) if errors else float("nan")


# ── Holiday factor computation ────────────────────────────────────────────────

def compute_holiday_factors(
    models: dict[str, object],
    feat_df: pd.DataFrame,
    X_raw: np.ndarray,
    orig_mask: np.ndarray,
    sensor_ids: list[str],
    ts: pd.DatetimeIndex,
) -> dict:
    """
    For each holiday day and each sensor:
      factor[slot] = actual_2025[slot] / baseline_pred[slot]

    Slots with missing data get the cross-sensor average factor.
    Result shape: {date_str: {"sensor_id": [f0..f95], "avg": [f0..f95]}}
    """
    # Batch-predict baseline for all sensors
    preds_all = {sid: models[sid].predict(feat_df) for sid in sensor_ids}

    factors: dict[str, dict] = {}
    for date_str in sorted(HOLIDAY_DATES_2025):
        d = pd.Timestamp(date_str).date()
        slots = [i for i, t in enumerate(ts) if t.date() == d]
        if not slots:
            continue

        per_sensor: dict[str, list[float]] = {}
        for sid in sensor_ids:
            col = sensor_ids.index(sid)
            fs = []
            for i in slots:
                actual = float(X_raw[i, col]) if orig_mask[i, col] else float("nan")
                pred   = float(preds_all[sid][i])
                if np.isnan(actual) or pred < 1.0:
                    fs.append(float("nan"))
                else:
                    fs.append(actual / pred)
            per_sensor[sid] = fs

        # Cross-sensor average factor per slot (fallback for missing data)
        avg_factors = []
        for slot_i in range(len(slots)):
            vals = [per_sensor[sid][slot_i] for sid in sensor_ids
                    if not np.isnan(per_sensor[sid][slot_i])]
            avg_factors.append(float(np.mean(vals)) if vals else 1.0)

        # Fill NaN in per-sensor with average
        for sid in sensor_ids:
            per_sensor[sid] = [
                v if not np.isnan(v) else avg_factors[k]
                for k, v in enumerate(per_sensor[sid])
            ]

        factors[date_str] = {**per_sensor, "avg": avg_factors}

    return factors


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import lightgbm as lgb

    def make_lgb():
        return lgb.LGBMRegressor(
            n_estimators=500, learning_rate=0.05,
            max_depth=6, num_leaves=63,
            subsample=0.8, colsample_bytree=0.8,
            min_child_samples=20, random_state=42,
            n_jobs=-1, verbose=-1,
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "models").mkdir(exist_ok=True)

    print("Loading flow_matrix.npz …")
    raw        = np.load(FEATURES_DIR / "flow_matrix.npz", allow_pickle=True)
    X_raw      = raw["X"]
    orig_mask  = raw["mask"].astype(bool)
    sensor_ids = raw["sensor_ids"].tolist()
    T, N       = X_raw.shape
    ts         = pd.date_range(start=EPOCH, periods=T, freq=INTERVAL)
    print(f"  {T} slots × {N} sensors")

    print("Building baseline features (no holiday flags) …")
    feat_df    = build_features(T)
    feat_names = list(feat_df.columns)
    print(f"  {len(feat_names)} features: {feat_names}")

    # Holiday mask
    holiday_mask = np.array([t.date() in _HOLIDAY_DATE_OBJS for t in ts])
    n_hol = holiday_mask.sum()
    print(f"  Excluding {n_hol} holiday slots ({n_hol/T*100:.1f}%) from training")

    trained_models: dict[str, object] = {}
    all_results: dict[str, dict] = {}

    for sid, col_idx in zip(sensor_ids, range(N)):
        print(f"\n── Sensor {sid} ──────────────────────────────")
        y_raw = X_raw[:, col_idx]
        mask  = orig_mask[:, col_idx]
        y     = impute_column(y_raw)

        naive  = seasonal_naive_mae(y, mask, holiday_mask)
        cv_mae = leave_weeks_out_mae(make_lgb, feat_df, y, mask, holiday_mask)
        pct    = 100 * (1 - cv_mae / naive) if naive > 0 else 0
        print(f"  Seasonal naïve  MAE: {naive:.2f}  (non-holiday slots)")
        print(f"  LightGBM CV     MAE: {cv_mae:.2f}  ({pct:+.1f}% vs naïve)")

        # Final model: train on all non-holiday slots
        final = make_lgb()
        nonhol_idx = np.where(~holiday_mask)[0]
        final.fit(feat_df.iloc[nonhol_idx], y[nonhol_idx])
        trained_models[sid] = final

        out_path = OUT_DIR / "models" / f"sensor_{sid}.pkl"
        with open(out_path, "wb") as f:
            pickle.dump({"model": final, "sensor_id": sid, "model_name": "lightgbm_baseline"}, f)
        print(f"  Saved → {out_path.name}")

        all_results[sid] = {
            "model":       "lightgbm_baseline",
            "naive_mae":   round(naive, 2),
            "cv_mae":      round(cv_mae, 2),
            "improvement": round(pct, 1),
        }

    # ── Holiday factors ────────────────────────────────────────────────────────
    print("\nComputing holiday factors …")
    factors = compute_holiday_factors(
        trained_models, feat_df, X_raw, orig_mask, sensor_ids, ts
    )

    # Verify: print a few spot-checks
    print(f"  Computed factors for {len(factors)} holiday days")
    for date_str in ["2025-06-20", "2025-12-24", "2025-12-31", "2025-01-01"]:
        if date_str in factors:
            avg = factors[date_str]["avg"]
            slot_08 = 8 * 4   # 08:00
            slot_23 = 23 * 4  # 23:00
            print(f"  {date_str}  avg factor @ 08:00={avg[slot_08]:.2f}  @ 23:00={avg[slot_23]:.2f}")

    factors_path = OUT_DIR / "holiday_factors.json"
    with open(factors_path, "w") as f:
        # Round to 4 decimal places to keep file small
        compact = {
            date_str: {
                k: [round(v, 4) for v in vals]
                for k, vals in day_data.items()
            }
            for date_str, day_data in factors.items()
        }
        json.dump(compact, f, separators=(",", ":"))
    print(f"  Saved → {factors_path}  ({factors_path.stat().st_size/1024:.0f} KB)")

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n── Summary (non-holiday days only) ───────────────────────────────────")
    print(f"  {'Sensor':<8} {'Naïve MAE':>10} {'LGB MAE':>10} {'Förbättring':>13}")
    for sid, r in all_results.items():
        print(f"  {sid:<8} {r['naive_mae']:>10.2f} {r['cv_mae']:>10.2f} {r['improvement']:>+12.1f}%")

    with open(OUT_DIR / "results.json", "w") as f:
        json.dump({"sensor_ids": sensor_ids, "results": all_results}, f, indent=2)
    with open(OUT_DIR / "feature_names.json", "w") as f:
        json.dump(feat_names, f)

    print(f"\nModels: {OUT_DIR / 'models'}")
    print(f"Holiday factors: {factors_path}")


def load_agent1(model_dir: Path = OUT_DIR / "models") -> "Agent1":
    return Agent1(model_dir)


class Agent1:
    def __init__(self, model_dir: Path = OUT_DIR / "models"):
        self._models: dict[str, object] = {}
        for path in sorted(model_dir.glob("sensor_*.pkl")):
            with open(path, "rb") as f:
                pkg = pickle.load(f)
            self._models[pkg["sensor_id"]] = pkg["model"]
        if not self._models:
            raise FileNotFoundError(f"No models in {model_dir} — run train_agent1.py first")

    def predict(self, dt: pd.Timestamp) -> dict[str, float]:
        """Baseline prediction (no holiday adjustment)."""
        feat = features_for_datetime(dt)
        return {sid: max(0.0, float(m.predict(feat)[0]))
                for sid, m in self._models.items()}

    @property
    def sensor_ids(self) -> list[str]:
        return list(self._models.keys())


if __name__ == "__main__":
    main()
