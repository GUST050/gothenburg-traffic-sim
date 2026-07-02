"""
Exploratory data analysis for the Gothenburg traffic dataset.

Run after build_data.py:
  python3 explore.py

Prints sensor statistics and saves plots to plots/.
No ML libraries required — reads flows.json and network.geojson directly.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

FLOWS_PATH = Path("web/data/flows.json")
GEO_PATH   = Path("web/data/network.geojson")
PLOTS_DIR  = Path("plots")

EPOCH        = pd.Timestamp("2025-01-01T00:00:00")
INTERVAL_MIN = 15


# ── Data loading ───────────────────────────────────────────────────────────────

def load_as_dataframe() -> tuple[pd.DataFrame, dict[str, str]]:
    """
    Returns (df, level_map).
    df: datetime index, columns = sensor_id (sorted), values = vehicles/15min (NaN for missing).
    level_map: {sensor_id: "S" | "Total"}
    """
    with open(FLOWS_PATH) as f:
        data = json.load(f)
    with open(GEO_PATH) as f:
        geo = json.load(f)

    sensor_to_edge: dict[str, str] = {}
    level_map: dict[str, str]      = {}
    for feat in geo["features"]:
        p   = feat["properties"]
        sid = p.get("sensor_id")
        if sid and sid not in sensor_to_edge:
            sensor_to_edge[sid] = p["id"]
            level_map[sid]      = p.get("level", "?")

    flows = data["flows"]
    n     = len(next(iter(flows.values())))
    ts    = pd.date_range(start=EPOCH, periods=n, freq=f"{INTERVAL_MIN}min")

    series: dict[str, list[float]] = {}
    for sid in sorted(sensor_to_edge):
        arr = flows.get(sensor_to_edge[sid], [None] * n)
        series[sid] = [float(v) if v is not None else float("nan") for v in arr]

    df            = pd.DataFrame(series, index=ts)
    df.index.name = "datetime"
    return df, level_map


# ── Console output ─────────────────────────────────────────────────────────────

def print_stats(df: pd.DataFrame, level_map: dict[str, str]) -> None:
    print("\n── Sensor statistics (vehicles / 15 min) ────────────────────────────────")
    print(f"{'Sensor':>8}  {'Level':>6}  {'Min':>5}  {'Max':>5}  {'Mean':>6}  {'Std':>6}  {'Missing':>9}")
    print("─" * 68)
    for sid in df.columns:
        col   = df[sid]
        miss  = col.isna().sum()
        pct   = 100 * miss / len(col)
        level = level_map.get(sid, "?")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            print(f"{sid:>8}  {level:>6}  {col.min():>5.0f}  {col.max():>5.0f}  "
                  f"{col.mean():>6.1f}  {col.std():>6.1f}  {miss:>5} ({pct:.1f}%)")
    print()


def print_1076_note(df: pd.DataFrame, level_map: dict[str, str]) -> None:
    s_sensors     = [s for s, lv in level_map.items() if lv == "S"  and s in df.columns]
    total_sensors = [s for s, lv in level_map.items() if lv == "Total" and s in df.columns]
    if not s_sensors or not total_sensors:
        return
    print("── Sensor 1076 note ─────────────────────────────────────────────────────")
    print(f"  {s_sensors} = single direction ('S').")
    print(f"  {total_sensors} = bidirectional sum ('Total').")
    ref_mean = df[total_sensors[0]].mean()
    for s in s_sensors:
        ratio = df[s].mean() / ref_mean if ref_mean else float("nan")
        print(f"  Mean {s} = {df[s].mean():.1f}  vs  {total_sensors[0]} = {ref_mean:.1f}  "
              f"(ratio ≈ {ratio:.2f})")
    print("  Z-score normalisation handles the scale difference in training.")
    print("  The adjacency weight between S and Total sensors does not capture")
    print("  the measurement type asymmetry — revisit when Felicia's directional")
    print("  re-export arrives.")
    print()


def print_correlations(df: pd.DataFrame) -> None:
    print("── Sensor correlations (Pearson, real observations only) ────────────────")
    corr = df.corr(min_periods=100)
    sids = df.columns.tolist()
    header = "        " + "".join(f"{s:>8}" for s in sids)
    print(header)
    print("─" * len(header))
    for sid in sids:
        row = "".join(f"{corr.loc[sid, s]:>8.3f}" for s in sids)
        print(f"{sid:>8}{row}")
    print()
    off_diag = corr.values[np.triu_indices(len(sids), k=1)]
    print(f"  Min off-diagonal: {off_diag.min():.3f}   Max: {off_diag.max():.3f}")
    print("  Sensors within the same cluster should be > 0.80.")
    print("  Cross-cluster correlations tell you how much spatial structure a GNN gains.")
    print()


def print_daily_profile(df: pd.DataFrame) -> None:
    print("── Average flow by hour of day (mean across all sensors) ────────────────")
    df2          = df.assign(hour=df.index.hour)
    hourly       = df2.groupby("hour")[df.columns].mean().mean(axis=1)
    peak         = int(hourly.idxmax())
    trough       = int(hourly.idxmin())
    bar_scale    = hourly.max() / 20
    for h, val in hourly.items():
        bar    = "█" * max(1, int(val / bar_scale))
        marker = " ← peak" if h == peak else (" ← trough" if h == trough else "")
        print(f"  {h:02d}:00  {val:6.1f}  {bar}{marker}")
    print()


def print_weekly_profile(df: pd.DataFrame) -> None:
    print("── Average flow by day of week (mean across all sensors) ────────────────")
    days   = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    df2    = df.assign(dow=df.index.dayofweek)
    daily  = df2.groupby("dow")[df.columns].mean().mean(axis=1)
    scale  = daily.max() / 20
    for dow, name in enumerate(days):
        val    = daily.get(dow, float("nan"))
        bar    = "█" * max(1, int(val / scale)) if not np.isnan(val) else "no data"
        print(f"  {name}  {val:6.1f}  {bar}")
    print()


# ── Plots ──────────────────────────────────────────────────────────────────────

def save_plots(df: pd.DataFrame) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plots.  pip install matplotlib")
        return

    PLOTS_DIR.mkdir(exist_ok=True)

    # 1. First week of raw flow
    week = df.iloc[: 96 * 7]
    fig, ax = plt.subplots(figsize=(14, 5))
    for sid in week.columns:
        ax.plot(week.index, week[sid], label=sid, alpha=0.85, linewidth=0.9)
    ax.set_title("Raw flow — first week of 2025 (vehicles / 15 min)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Vehicles / 15 min")
    ax.legend()
    fig.tight_layout()
    p = PLOTS_DIR / "week_jan01.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f"  Saved {p}")

    # 2. Average daily profile
    df2     = df.assign(slot=df.index.hour * 4 + df.index.minute // 15)
    profile = df2.groupby("slot")[df.columns].mean()
    fig, ax = plt.subplots(figsize=(10, 4))
    for sid in profile.columns:
        ax.plot(profile.index / 4, profile[sid], label=sid)
    ax.set_title("Average daily profile (vehicles / 15 min)")
    ax.set_xlabel("Hour of day")
    ax.set_ylabel("Vehicles / 15 min")
    ax.set_xticks(range(0, 25, 2))
    ax.legend()
    fig.tight_layout()
    p = PLOTS_DIR / "daily_profile.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f"  Saved {p}")

    # 3. Missing data heatmap (month × sensor)
    df_m  = df.assign(month=df.index.month)
    miss  = df_m.groupby("month")[df.columns].apply(lambda g: g.isna().mean() * 100)
    fig, ax = plt.subplots(figsize=(9, 4))
    im = ax.imshow(miss.T.to_numpy(), aspect="auto", cmap="Reds", vmin=0, vmax=10)
    ax.set_yticks(range(len(df.columns)))
    ax.set_yticklabels(df.columns)
    ax.set_xticks(range(12))
    ax.set_xticklabels(["Jan","Feb","Mar","Apr","May","Jun",
                         "Jul","Aug","Sep","Oct","Nov","Dec"])
    ax.set_title("Missing data % per month and sensor")
    fig.colorbar(im, ax=ax, label="%")
    fig.tight_layout()
    p = PLOTS_DIR / "missing_heatmap.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f"  Saved {p}")

    # 4. Sensor correlation matrix
    corr = df.corr(min_periods=100)
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(corr.to_numpy(), cmap="RdYlGn", vmin=-1, vmax=1)
    ax.set_xticks(range(len(df.columns)))
    ax.set_xticklabels(df.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(df.columns)))
    ax.set_yticklabels(df.columns)
    for i in range(len(df.columns)):
        for j in range(len(df.columns)):
            ax.text(j, i, f"{corr.iloc[i, j]:.2f}", ha="center", va="center", fontsize=8)
    ax.set_title("Sensor correlation matrix")
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    p = PLOTS_DIR / "correlation_matrix.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f"  Saved {p}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if not FLOWS_PATH.exists():
        print(f"ERROR: {FLOWS_PATH} not found.  Run build_data.py first.")
        return
    if not GEO_PATH.exists():
        print(f"ERROR: {GEO_PATH} not found.  Run build_data.py first.")
        return

    print("Loading flows.json and network.geojson …")
    df, level_map = load_as_dataframe()
    print(f"Loaded: {df.shape[0]:,} time steps × {df.shape[1]} sensors")
    print(f"Range:  {df.index[0]}  →  {df.index[-1]}")

    print_stats(df, level_map)
    print_1076_note(df, level_map)
    print_correlations(df)
    print_daily_profile(df)
    print_weekly_profile(df)

    print("── Plots ────────────────────────────────────────────────────────────────")
    save_plots(df)

    print()
    print("── What to look for ─────────────────────────────────────────────────────")
    print("  Sensor means: expect 20–300 vehicles / 15 min for urban roads.")
    print("  Correlations within a cluster: expect > 0.80.")
    print("  Correlations across clusters: could be lower — important for GNN design.")
    print("  Missing data: expect < 5% per sensor.  Spikes in one month = sensor outage.")
    print("  Daily profile: clear AM and PM peak = data is sensible.")
    print("  Weekly: weekday >> weekend = expected urban traffic pattern.")
    print()
    print("Next: python3 build_features.py")


if __name__ == "__main__":
    main()
