"""
Export training artifacts for the Phase 3 forecast model.

Run after build_data.py (from the project root):
  python3 build_features.py

Reads:
  web/data/flows.json       — per-edge flow arrays (35 040 slots, full 2025)
  web/data/network.geojson  — sensor positions and edge metadata

Writes to web/data/features/:
  flow_matrix.csv   — (35 040 × n_sensors) flow counts, NaN for missing, datetime index
  flow_matrix.npz   — same data as numpy: X, mask, sensor_ids, timestamps
  adjacency.csv     — (n_sensors × n_sensors) Gaussian-kernel distance adjacency (W matrix)
  sensor_meta.csv   — sensor_id, lat, lon, level, primary_edge
  split.json        — train / val / test quarter-index boundaries

Why these formats?
  flow_matrix.npz is the primary input for GNN training libraries (DCRNN,
  Graph WaveNet, PyTorch Geometric Temporal).  The CSV is human-readable and
  easy to load in any tool.  adjacency.csv is the W matrix those papers expect.
  split.json keeps the split decision out of training code so it doesn't drift.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from build_data import EPOCH, INTERVAL, N_QUARTERS, haversine_m

GEO_PATH   = Path("web/data/network.geojson")
FLOWS_PATH = Path("web/data/flows.json")
OUT_DIR    = Path("web/data/features")

# Adjacency: sensor pairs further than this get A[i,j] = 0
ADJ_THRESHOLD_M = 2_000  # covers the whole Götaplatsen–Scandinavium corridor
# Gaussian kernel std; controls how quickly weight decays with distance
ADJ_SIGMA_M     = 500

# Time split — chronological, never shuffled (shuffling breaks temporal models)
# 80 % train · 10 % val · 10 % test
TRAIN_FRAC = 0.80
VAL_FRAC   = 0.10


# ── Builders ───────────────────────────────────────────────────────────────────

def build_sensor_meta(geojson: dict) -> pd.DataFrame:
    """
    One row per unique sensor. For Total sensors (two edges) only the first
    edge is used for the position — both edges are ~18 m apart on the same road.

    Returns columns: sensor_id, lat, lon, level, primary_edge.
    """
    seen: set[str] = set()
    rows: list[dict] = []

    for feat in geojson["features"]:
        p   = feat["properties"]
        sid = p.get("sensor_id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        coords = feat["geometry"]["coordinates"]
        # Average of the two endpoint nodes — consistent with edge_midpoint() in
        # build_data.py.  Long OSM edges have many intermediate vertices that are
        # not evenly distributed, so the middle-indexed vertex can be far from
        # the true geographic midpoint.
        lat = round((coords[0][1] + coords[-1][1]) / 2, 6)
        lon = round((coords[0][0] + coords[-1][0]) / 2, 6)
        rows.append({
            "sensor_id":    sid,
            "lat":          lat,
            "lon":          lon,
            "level":        p.get("level"),
            "primary_edge": p["id"],
        })

    if not rows:
        return pd.DataFrame(columns=["sensor_id", "lat", "lon", "level", "primary_edge"])
    return (
        pd.DataFrame(rows)
        .sort_values("sensor_id")
        .reset_index(drop=True)
    )


def build_flow_matrix(
    flows_data: dict,
    sensor_meta: pd.DataFrame,
) -> pd.DataFrame:
    """
    Return DataFrame of shape (N_QUARTERS, n_sensors).
    Index = UTC-naive 15-min datetime. Columns = sensor_id (sorted).
    Missing values are NaN (not 0 — 0 means zero vehicles, NaN means no data).

    Total sensors store the combined bidirectional count on both edges; we read
    from the primary edge so each sensor appears exactly once.
    """
    timestamps = pd.date_range(start=EPOCH, periods=N_QUARTERS, freq="15min")
    flows      = flows_data["flows"]
    data: dict[str, list[float]] = {}

    for row in sensor_meta.itertuples(index=False):
        arr = flows.get(row.primary_edge)
        if arr is None:
            data[row.sensor_id] = [float("nan")] * N_QUARTERS
        else:
            data[row.sensor_id] = [
                float(v) if v is not None else float("nan") for v in arr
            ]

    df            = pd.DataFrame(data, index=timestamps)
    df.index.name = "datetime"
    return df[sensor_meta["sensor_id"].tolist()]


def build_adjacency(sensor_meta: pd.DataFrame) -> pd.DataFrame:
    """
    Gaussian-kernel adjacency matrix W used by DCRNN / Graph WaveNet.

      W[i,j] = exp( -d(i,j)² / (2σ²) )   if d ≤ ADJ_THRESHOLD_M
      W[i,j] = 0                            otherwise
      W[i,i] = 0  (no self-loops; add identity inside your model if required)

    d(i,j) = haversine distance between sensor positions.  Road-network
    distance would be more accurate but requires running osmnx.  Geographic
    distance is a valid approximation when sensors sit on the same corridor.
    """
    sids = sensor_meta["sensor_id"].tolist()
    n    = len(sids)
    A    = np.zeros((n, n), dtype=np.float32)

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d = haversine_m(
                sensor_meta.iloc[i]["lat"], sensor_meta.iloc[i]["lon"],
                sensor_meta.iloc[j]["lat"], sensor_meta.iloc[j]["lon"],
            )
            if d <= ADJ_THRESHOLD_M:
                A[i, j] = math.exp(-(d ** 2) / (2 * ADJ_SIGMA_M ** 2))

    return pd.DataFrame(A, index=sids, columns=sids)


def build_normal_profile(flows_data: dict) -> dict:
    """
    Average September 2025 flow per edge, split into weekday (Mon–Fri) and
    weekend (Sat–Sun) profiles of 96 slots each.

    September is the undisturbed reference month: schools fully in session,
    no public holidays, stable weather.  The animation uses this to colour
    edges relative to normal rather than relative to the yearly maximum.
    """
    qi_sept_start = int((pd.Timestamp("2025-09-01")       - EPOCH) / INTERVAL)
    qi_sept_end   = int((pd.Timestamp("2025-09-30T23:45") - EPOCH) / INTERVAL)
    sept_ts = pd.date_range(start="2025-09-01",
                            periods=qi_sept_end - qi_sept_start + 1,
                            freq="15min")

    profiles: dict[str, dict] = {}

    # 07:00–08:45 = slots 28–35,  15:00–17:45 = slots 60–71
    RUSH_SLOTS = set(range(28, 36)) | set(range(60, 72))

    def avg_by_slot(vals_arr: np.ndarray, slot: np.ndarray, mask: np.ndarray) -> list:
        result = []
        for s in range(96):
            sel = vals_arr[(slot == s) & mask]
            sel = sel[~np.isnan(sel)]
            result.append(round(float(sel.mean()), 1) if len(sel) > 0 else None)
        return result

    def avg_by_slot_calm(vals_arr: np.ndarray, slot: np.ndarray, mask: np.ndarray) -> list:
        """September average excluding rush-hour slots — the 'calm traffic' reference."""
        result = []
        for s in range(96):
            if s in RUSH_SLOTS:
                result.append(None)
                continue
            sel = vals_arr[(slot == s) & mask]
            sel = sel[~np.isnan(sel)]
            result.append(round(float(sel.mean()), 1) if len(sel) > 0 else None)
        return result

    def daily_avg(profile: list) -> float | None:
        vals = [v for v in profile if v is not None]
        return round(float(np.mean(vals)), 1) if vals else None

    for edge_id, arr in flows_data["flows"].items():
        series = pd.Series(
            [float(v) if v is not None else float("nan")
             for v in arr[qi_sept_start : qi_sept_end + 1]],
            index=sept_ts,
        )
        vals_arr   = series.to_numpy()
        slot       = np.array(series.index.hour * 4 + series.index.minute // 15)
        is_weekday = np.array(series.index.dayofweek < 5)

        weekday_profile = avg_by_slot(vals_arr, slot, is_weekday)
        weekend_profile = avg_by_slot(vals_arr, slot, ~is_weekday)

        profiles[edge_id] = {
            "weekday":            weekday_profile,
            "weekend":            weekend_profile,
            "weekday_daily_avg":  daily_avg(weekday_profile),
            "weekend_daily_avg":  daily_avg(weekend_profile),
            "calm_weekday":       avg_by_slot_calm(vals_arr, slot, is_weekday),
            "calm_weekend":       avg_by_slot_calm(vals_arr, slot, ~is_weekday),
        }

    return {
        "description": "Average September 2025 flow — undisturbed normal reference",
        "source_month": "September",
        "source_year":  2025,
        "slots_per_day": 96,
        "interval_minutes": int(INTERVAL.total_seconds() / 60),
        "profiles": profiles,
    }


def build_split(n: int) -> dict:
    """
    Chronological train / val / test split.  Returns qi (quarter-index) start
    and end for each split, plus ISO datetime strings for readability.

    IMPORTANT: never shuffle a time series before splitting — the model must
    not see future data during training.
    """
    n_train = int(n * TRAIN_FRAC)
    n_val   = int(n * VAL_FRAC)

    boundaries = {
        "train": (0,                     n_train - 1),
        "val":   (n_train,               n_train + n_val - 1),
        "test":  (n_train + n_val,       n - 1),
    }

    result: dict[str, dict] = {}
    for name, (start, end) in boundaries.items():
        result[name] = {
            "qi_start":   start,
            "qi_end":     end,
            "n_quarters": end - start + 1,
            "dt_start":   str(EPOCH + INTERVAL * start),
            "dt_end":     str(EPOCH + INTERVAL * end),
        }
    return result


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading network.geojson …")
    with open(GEO_PATH) as f:
        geojson = json.load(f)

    print("Loading flows.json …")
    with open(FLOWS_PATH) as f:
        flows_data = json.load(f)

    if pd.Timestamp(flows_data.get("epoch", "")) != EPOCH:
        raise ValueError(
            f"flows.json epoch {flows_data.get('epoch')!r} does not match {str(EPOCH)!r}. "
            "Re-run build_data.py."
        )
    if flows_data.get("interval_minutes") != int(INTERVAL.total_seconds() / 60):
        raise ValueError(
            f"flows.json interval_minutes={flows_data.get('interval_minutes')} "
            f"!= {int(INTERVAL.total_seconds() / 60)}. Re-run build_data.py."
        )
    for edge_id, arr in flows_data["flows"].items():
        if len(arr) != N_QUARTERS:
            raise ValueError(
                f"Edge {edge_id!r} has {len(arr)} flow slots, expected {N_QUARTERS}. "
                "Re-run build_data.py."
            )

    # ── Sensor metadata ────────────────────────────────────────────────────────
    sensor_meta = build_sensor_meta(geojson)
    out_meta    = OUT_DIR / "sensor_meta.csv"
    sensor_meta.to_csv(out_meta, index=False)
    print(f"\nSensors ({len(sensor_meta)}):")
    print(sensor_meta.to_string(index=False))
    print(f"Wrote {out_meta}")

    level_counts = sensor_meta["level"].value_counts()
    if "S" in level_counts.index and "Total" in level_counts.index:
        s_sensors = sensor_meta[sensor_meta["level"] == "S"]["sensor_id"].tolist()
        print(f"\n  WARNING: sensor(s) {s_sensors} measure a single direction (level='S').")
        print(f"  All other sensors measure bidirectional totals (level='Total').")
        print(f"  Z-score normalisation handles the scale difference during training.")
        print(f"  Adjacency weights between S and Total sensors are distance-based and")
        print(f"  do not capture the measurement asymmetry — revisit when directional"  )
        print(f"  data arrives from Felicia.")

    # ── Flow matrix ────────────────────────────────────────────────────────────
    print("\nBuilding flow matrix …")
    flow_df   = build_flow_matrix(flows_data, sensor_meta)
    n_total   = flow_df.size
    n_missing = int(flow_df.isna().sum().sum())
    print(f"  Shape: {flow_df.shape}  —  {n_missing:,}/{n_total:,} missing ({100*n_missing/n_total:.2f}%)")
    for sid in flow_df.columns:
        col     = flow_df[sid]
        pct_mis = 100 * col.isna().sum() / len(col)
        print(f"    {sid}: min={col.min():.0f}  max={col.max():.0f}  "
              f"mean={col.mean():.1f}  missing={pct_mis:.1f}%")

    out_csv = OUT_DIR / "flow_matrix.csv"
    flow_df.to_csv(out_csv)
    print(f"Wrote {out_csv}  ({out_csv.stat().st_size // 1024} KB)")

    X    = flow_df.to_numpy(dtype=np.float32)
    mask = ~np.isnan(X)
    out_npz = OUT_DIR / "flow_matrix.npz"
    np.savez_compressed(
        out_npz,
        X          = X,
        mask       = mask,
        sensor_ids = np.array(flow_df.columns.tolist()),
        timestamps = np.array([str(ts) for ts in flow_df.index]),
    )
    print(f"Wrote {out_npz}  ({out_npz.stat().st_size // 1024} KB)")

    # ── Adjacency ──────────────────────────────────────────────────────────────
    print("\nBuilding adjacency matrix …")
    adj     = build_adjacency(sensor_meta)
    sids    = adj.index.tolist()
    adj_np  = adj.to_numpy(dtype=np.float32)

    out_adj = OUT_DIR / "adjacency.csv"
    adj.to_csv(out_adj, float_format="%.8f")
    print(adj.round(3).to_string())
    print(f"Wrote {out_adj}")

    # adjacency.npz: load with np.load(); W is the (N,N) weight matrix.
    # sensor_ids order here MUST match the column order in flow_matrix.npz
    # and dataset_*.npz — all three are sorted alphabetically by build_sensor_meta.
    out_adj_npz = OUT_DIR / "adjacency.npz"
    np.savez_compressed(
        out_adj_npz,
        W          = adj_np,
        sensor_ids = np.array(sids),
    )
    print(f"Wrote {out_adj_npz}")

    # ── Train / val / test split ───────────────────────────────────────────────
    split     = build_split(N_QUARTERS)
    out_split = OUT_DIR / "split.json"
    with open(out_split, "w") as f:
        json.dump(split, f, indent=2)
    print("\nSplit (chronological):")
    for name, s in split.items():
        print(f"  {name:5s}: {s['dt_start'][:10]} → {s['dt_end'][:10]}"
              f"  ({s['n_quarters']} quarters = {s['n_quarters']//96} days)")
    print(f"Wrote {out_split}")

    # ── Normal profile (September reference) ──────────────────────────────────
    print("\nBuilding normal profile (September 2025) …")
    normal = build_normal_profile(flows_data)
    out_normal = GEO_PATH.parent / "normal_profile.json"   # web/data/
    with open(out_normal, "w") as f:
        json.dump(normal, f)
    n_edges = len(normal["profiles"])
    print(f"  {n_edges} edges  ·  weekday + weekend profiles (96 slots each)")
    print(f"Wrote {out_normal}")


if __name__ == "__main__":
    main()
