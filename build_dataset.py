"""
Prepare model-ready training data from the raw feature matrix.

Run after build_features.py:
  python3 build_dataset.py

Reads (web/data/features/):
  flow_matrix.npz   — raw flow values (T × N), NaN for missing
  split.json        — chronological train/val/test boundaries

Writes (web/data/features/):
  imputed.npz       — missing values filled, same shape (T × N), no NaN
  scaler.json       — {"sensor_ids", "mean", "std"} — KEEP THIS for inference
  dataset_train.npz — see keys below
  dataset_val.npz   — same keys
  dataset_test.npz  — same keys
  baseline.json     — seasonal-naïve MAE per sensor in original vehicle units

Keys in every dataset_*.npz:
  X                — float32 (n_windows, K, N+7) normalised input windows
                     First N columns: z-scored flow values.
                     Last 7 columns: time features (hour_sin/cos, dow_sin/cos,
                     month_sin/cos, is_weekend) — already in [-1,1], not z-scored.
  y                — float32 (n_windows, H, N)  normalised flow target windows only
  y_mask           — bool    (n_windows, H, N)  True = real observation, False = imputed
                     Use this to exclude imputed targets from the loss function.
  sensor_ids       — str     (N,)               sensor ID for each flow column in y
                     MUST match the index/columns of adjacency.npz when building a GNN.
  qi_target_start  — int32   (n_windows,)        quarter-index of the first target step
                     Use EPOCH + qi * 15min to get the absolute datetime of each window.

Constants:
  K = 12   input steps  = 3 hours of history
  H = 4    output steps = 1 hour ahead prediction
  SEASONAL_LAG = 672  = 7 days × 96 quarters (used for baseline + imputation)
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys_path_root = Path(__file__).parent
import sys
sys.path.insert(0, str(sys_path_root))
from build_data import EPOCH, INTERVAL

FEATURES_DIR = Path("web/data/features")

WINDOW_K     = 12   # input steps  (3 hours)
WINDOW_H     = 4    # output steps (1 hour ahead)
SEASONAL_LAG = 672  # 1 week = 7 days × 96 slots/day
N_TIME_FEAT  = 7    # time features appended to flow inputs


# ── Time features ──────────────────────────────────────────────────────────────

def build_time_features(n: int) -> np.ndarray:
    """
    Build (n, 7) float32 array of cyclic time features for quarter indices 0…n-1.

    Features (all in [-1, 1] or [0, 1]):
      hour_sin, hour_cos   — position within the day (period 24 h)
      dow_sin,  dow_cos    — position within the week (period 7 days)
      month_sin, month_cos — position within the year (period 12 months)
      is_weekend           — 1.0 for Sat/Sun, 0.0 for Mon–Fri

    These are NEVER normalised — cyclic encodings are already bounded.
    The LSTM sees them alongside the z-scored flow values.
    """
    ts  = pd.date_range(start=EPOCH, periods=n, freq=INTERVAL)
    h   = ts.hour + ts.minute / 60          # 0–24
    dow = ts.dayofweek                      # 0=Mon … 6=Sun
    mon = ts.month                          # 1–12

    return np.column_stack([
        np.sin(2 * np.pi * h   / 24).astype(np.float32),
        np.cos(2 * np.pi * h   / 24).astype(np.float32),
        np.sin(2 * np.pi * dow /  7).astype(np.float32),
        np.cos(2 * np.pi * dow /  7).astype(np.float32),
        np.sin(2 * np.pi * (mon - 1) / 12).astype(np.float32),
        np.cos(2 * np.pi * (mon - 1) / 12).astype(np.float32),
        (dow >= 5).astype(np.float32),      # is_weekend
    ])


# ── Imputation ─────────────────────────────────────────────────────────────────

def impute(X: np.ndarray, train_end: int | None = None) -> np.ndarray:
    """
    Fill every NaN in X (shape T×N).  Returns same shape, no NaN.

    Two passes:
      1. Forward-fill — carry the last known value into the gap.
         Handles the common case: short outages mid-series.
      2. Column mean — for the leading gap (before a column's first
         observation), the only place NaN can survive pass 1.  A seasonal
         lookback would land inside the same leading gap, so it cannot help.
         Computed from X[0 : train_end+1] only when train_end is given, so
         that val/test statistics do not influence the fill value.

    Args:
        X         — (T, N) float32, NaN for missing
        train_end — last quarter-index of the training period (inclusive).
                    Pass this so pass 2 uses only training-period statistics.
    """
    if not np.isnan(X).any():
        return X.astype(np.float32)

    df = pd.DataFrame(X.astype(float))

    # Pass 1: forward-fill
    df = df.ffill()

    # Pass 2: column mean — training period only to avoid data leakage
    ref = df.iloc[: train_end + 1] if train_end is not None else df
    df  = df.fillna(ref.mean())

    result = df.to_numpy(dtype=np.float32)
    if np.isnan(result).any():
        raise ValueError("Imputation failed — entire column is NaN (sensor has zero observations)")
    return result


# ── Scaler ─────────────────────────────────────────────────────────────────────

def fit_scaler(X_train: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute per-sensor z-score parameters from TRAINING DATA ONLY.

    Using val/test statistics here would be data leakage: the model would
    have seen information about future traffic levels during training.
    Always call this with the training slice, then apply the result to all splits.
    """
    mean = X_train.mean(axis=0)          # (N,)
    std  = X_train.std(axis=0)           # (N,)
    std[std < 1e-8] = 1.0                # guard: sensor that never varies
    return mean.astype(np.float32), std.astype(np.float32)


def normalize(X: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    return ((X - mean) / std).astype(np.float32)


def denormalize(X_norm: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """Reverse z-score normalization — use this to convert model output back to vehicle counts."""
    return (X_norm * std + mean).astype(np.float32)


# ── Windowing ──────────────────────────────────────────────────────────────────

def make_windows(
    X_norm: np.ndarray,
    orig_mask: np.ndarray,
    K: int = WINDOW_K,
    H: int = WINDOW_H,
    *,
    X_inp: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Slide a window of length K+H over the time axis.

    Args:
        X_norm    — normalised flow data (T, N) — used as TARGET
        orig_mask — True where the original observation was real (T, N)
        K         — number of input steps
        H         — number of target steps
        X_inp     — if provided, used as INPUT instead of X_norm.
                    Shape (T, N_inp) where N_inp ≥ N (e.g. flows + time features).
                    Targets always come from X_norm (flows only).

    Returns:
        inputs      (n_windows, K, N_inp) — model input: K steps of history
        targets     (n_windows, H, N)     — model target: H steps of flows
        target_mask (n_windows, H, N)     — True where target is a real observation
    """
    X_input = X_inp if X_inp is not None else X_norm
    T = X_norm.shape[0]
    if X_input.shape[0] != T:
        raise ValueError(
            f"X_inp has {X_input.shape[0]} rows but X_norm has {T}."
        )
    n = T - K - H + 1
    if n <= 0:
        raise ValueError(
            f"Series too short for windowing: T={T} requires at least K+H={K + H} steps. "
            "Reduce WINDOW_K or WINDOW_H."
        )
    t_in  = np.arange(n)[:, None] + np.arange(K)[None, :]      # (n, K)
    t_out = np.arange(n)[:, None] + K + np.arange(H)[None, :]  # (n, H)

    inputs      = X_input[t_in]     # (n, K, N_inp)
    targets     = X_norm[t_out]     # (n, H, N)
    target_mask = orig_mask[t_out]  # (n, H, N) bool

    return inputs, targets, target_mask


# ── Seasonal naïve baseline ────────────────────────────────────────────────────

def seasonal_naive_mae(
    X_orig: np.ndarray,
    orig_mask: np.ndarray,
    eval_start_qi: int,
) -> dict:
    """
    Evaluate the seasonal-naïve baseline on X_orig[eval_start_qi:].

    Prediction at time t = observation at time t - SEASONAL_LAG.
    MAE is computed only on real observations (orig_mask == True).
    Returns vehicle counts (not normalised) so the number is meaningful.

    This is your minimum bar: any trained model must beat this MAE
    or the added complexity is not justified.
    """
    X_eval    = X_orig[eval_start_qi:]
    mask_eval = orig_mask[eval_start_qi:]

    lag_start = eval_start_qi - SEASONAL_LAG
    if lag_start < 0:
        raise ValueError(
            f"eval_start_qi={eval_start_qi} < SEASONAL_LAG={SEASONAL_LAG}. "
            "Evaluation period must start at least 1 week into the data."
        )
    X_lag = X_orig[lag_start : lag_start + len(X_eval)]

    # Only count slots where both target and lag source are real observations
    valid = mask_eval & orig_mask[lag_start : lag_start + len(X_eval)]

    abs_err = np.abs(X_eval - X_lag)
    abs_err[~valid] = np.nan

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # nanmean of all-NaN → NaN, expected
        per_sensor = np.nanmean(abs_err, axis=0).tolist()   # (N,) — NaN if no valid pairs
        overall    = float(np.nanmean(abs_err))

    n_valid = int(valid.sum())
    n_total = int(valid.size)
    pct_real = round(100 * n_valid / n_total, 1) if n_total > 0 else 0.0

    return {"per_sensor_mae": per_sensor, "mean_mae": overall, "pct_real_observations": pct_real}


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Load raw features ──────────────────────────────────────────────────────
    print("Loading flow_matrix.npz …")
    raw        = np.load(FEATURES_DIR / "flow_matrix.npz", allow_pickle=True)
    X_raw      = raw["X"]             # (T, N) float32, NaN for missing
    orig_mask  = raw["mask"]          # (T, N) bool
    sensor_ids = raw["sensor_ids"].tolist()

    # Sanity checks before doing any work
    _N_QUARTERS = 365 * 96  # 35 040 — 2025 is not a leap year
    if X_raw.shape[0] != _N_QUARTERS:
        raise ValueError(
            f"flow_matrix.npz has {X_raw.shape[0]} time steps, expected {_N_QUARTERS}. "
            "Re-run build_features.py."
        )
    if not np.all(orig_mask == ~np.isnan(X_raw)):
        raise ValueError(
            "flow_matrix.npz: mask and X are inconsistent. Re-run build_features.py."
        )

    print("Loading split.json …")
    with open(FEATURES_DIR / "split.json") as f:
        split = json.load(f)
    tr_s, tr_e = split["train"]["qi_start"], split["train"]["qi_end"]
    va_s, va_e = split["val"]["qi_start"],   split["val"]["qi_end"]
    te_s, te_e = split["test"]["qi_start"],  split["test"]["qi_end"]

    # ── Impute missing values ──────────────────────────────────────────────────
    print("\nImputing missing values …")
    n_missing = int(np.isnan(X_raw).sum())
    print(f"  {n_missing:,} missing slots before imputation")
    X_imp = impute(X_raw, train_end=tr_e)   # pass 3 uses training mean only
    print(f"  {int(np.isnan(X_imp).sum())} missing slots after imputation")

    np.savez_compressed(FEATURES_DIR / "imputed.npz",
                        X=X_imp, mask=orig_mask,
                        sensor_ids=np.array(sensor_ids))
    print(f"Wrote imputed.npz")

    # ── Fit scaler on TRAINING data only ──────────────────────────────────────
    print("\nFitting scaler on training data …")
    X_train_raw = X_imp[tr_s : tr_e + 1]
    mean, std   = fit_scaler(X_train_raw)

    print("  Sensor means (vehicles/15min):", dict(zip(sensor_ids, mean.round(1).tolist())))
    print("  Sensor stds:", dict(zip(sensor_ids, std.round(1).tolist())))

    scaler = {"sensor_ids": sensor_ids, "mean": mean.tolist(), "std": std.tolist()}
    with open(FEATURES_DIR / "scaler.json", "w") as f:
        json.dump(scaler, f, indent=2)
    print("Wrote scaler.json  ← keep this — needed to convert predictions back to vehicle counts")

    # ── Normalize full series with training stats ──────────────────────────────
    X_norm = normalize(X_imp, mean, std)

    # Quick sanity check: training mean should be ≈0, std ≈1
    train_norm_mean = X_norm[tr_s : tr_e + 1].mean(axis=0)
    train_norm_std  = X_norm[tr_s : tr_e + 1].std(axis=0)
    print(f"\n  Normalisation check (training slice):")
    print(f"    mean ≈ {train_norm_mean.round(4)}  (should be ≈0)")
    print(f"    std  ≈ {train_norm_std.round(4)}   (should be ≈1)")

    # ── Time features ──────────────────────────────────────────────────────────
    # 7 cyclic features appended to flow inputs so the model sees time-of-day,
    # day-of-week, month, and weekend flag.  Already in [-1, 1] — not normalised.
    # Targets remain flow-only (N columns) — we predict vehicles, not time.
    T = X_norm.shape[0]
    print(f"\nBuilding time features ({N_TIME_FEAT} features × {T} steps) …")
    X_time = build_time_features(T)  # (T, 7)
    X_inp  = np.concatenate([X_norm, X_time], axis=1)  # (T, N + 7)
    print(f"  Input shape: {X_inp.shape}  (flow={X_norm.shape[1]}  time={N_TIME_FEAT})")

    # ── Create sliding-window datasets ────────────────────────────────────────
    # Val and test slices start K steps BEFORE their split boundary so the
    # first window has a full K-step input.  All window TARGETS land within
    # the split period — no target ever crosses the split boundary.
    # X (input windows): shape (n, K, N+7) — flows + time features
    # y (target windows): shape (n, H, N)  — flows only
    print("\nCreating sliding-window datasets …")

    splits_def = {
        "train": (tr_s,              tr_e + 1),
        "val":   (va_s - WINDOW_K,   va_e + 1),
        "test":  (te_s - WINDOW_K,   te_e + 1),
    }

    sensor_ids_arr = np.array(sensor_ids)

    for name, (slice_start, slice_end) in splits_def.items():
        X_sl     = X_norm[slice_start : slice_end]   # flow targets
        X_inp_sl = X_inp[slice_start : slice_end]    # flow + time inputs
        mask_sl  = orig_mask[slice_start : slice_end]
        inp, tgt, tgt_mask = make_windows(X_sl, mask_sl, X_inp=X_inp_sl)

        # qi of the first target step for each window.
        # Formula: the slice starts at slice_start; within the slice, window i
        # has its input at [i, i+K) and its target at [i+K, i+K+H).
        # In absolute qi terms: target starts at slice_start + i + K.
        n_windows       = inp.shape[0]
        qi_target_start = (
            np.arange(n_windows, dtype=np.int32) + slice_start + WINDOW_K
        )

        out_path = FEATURES_DIR / f"dataset_{name}.npz"
        np.savez_compressed(
            out_path,
            X               = inp,
            y               = tgt,
            y_mask          = tgt_mask,
            sensor_ids      = sensor_ids_arr,
            qi_target_start = qi_target_start,
        )
        coverage = tgt_mask.mean()
        print(f"  {name:5s}: {n_windows:6,} windows  "
              f"X={inp.shape}  y={tgt.shape}  "
              f"[{inp.shape[2] - N_TIME_FEAT} flows + {N_TIME_FEAT} time features]  "
              f"real-observation coverage={coverage:.1%}")

    # ── Seasonal naïve baseline ────────────────────────────────────────────────
    print("\nEvaluating seasonal-naïve baseline on val + test …")
    baseline_mae = seasonal_naive_mae(X_imp, orig_mask, eval_start_qi=va_s)
    print(f"  Mean MAE: {baseline_mae['mean_mae']:.2f} vehicles/15min")
    print("  Per sensor:", dict(zip(sensor_ids,
                                    [round(v, 2) for v in baseline_mae["per_sensor_mae"]])))
    print("  Any model you train MUST beat this number to be useful.")

    with open(FEATURES_DIR / "baseline.json", "w") as f:
        json.dump({"seasonal_naive": baseline_mae, "sensor_ids": sensor_ids,
                   "seasonal_lag_quarters": SEASONAL_LAG,
                   "eval_from": split["val"]["dt_start"]}, f, indent=2)
    print("Wrote baseline.json")

    print("\nDone. Files in", FEATURES_DIR, ":")
    for p in sorted(FEATURES_DIR.glob("*")):
        print(f"  {p.name:<30s} {p.stat().st_size // 1024:>5} KB")


if __name__ == "__main__":
    main()
