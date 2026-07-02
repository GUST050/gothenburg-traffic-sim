"""
Build the training table for the direction-split model.

Usage:
  python3 -m dirsplit.dataset

Joins, for every matched station with fetched volumes:
  - geometry/land-use features (dirsplit/features.py — same code path as
    the Gothenburg target edges)
  - profile-shape features computed from the station's own TOTAL volumes
    (normalized hour-of-day curve statistics — these exist for the
    Gothenburg sensors too, so they are legal inputs)
  - the LABEL: share of the total in this direction, per weekday hour
    (hour_of_week 0–167 collapsed to weekday hour 0–23 + is_weekend)

One row = (station, direction, hour-of-day, day-type) with the observed mean
share as target. Rows with low data coverage (<80%) or tiny totals (<30
veh/h summed over the averaged days) are dropped — a share of 3/5 vehicles
is noise, not signal.

Writes data/dirsplit/training_table.csv
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime

from .config import DATA_DIR, STATIONS_OK, VOLUMES_DIR
from .features import FEATURE_NAMES, edge_features, load_city_graph

OUT_PATH        = DATA_DIR / "training_table.csv"
MIN_COVERAGE    = 80.0
MIN_TOTAL_VEH_H = 30.0

PROFILE_FEATURES = ["am_pm_ratio", "peak_hour_am", "peak_hour_pm", "weekend_ratio"]


def load_volumes(station_id: str) -> list[dict] | None:
    path = VOLUMES_DIR / f"{station_id}.csv"
    if not path.exists():
        return None
    with open(path) as f:
        return list(csv.DictReader(f))


def profile_features(rows: list[dict]) -> dict[str, float] | None:
    """Shape statistics of the TOTAL (both directions) — computable for the
    Gothenburg sensors as well, hence valid model inputs."""
    tot_wd  = defaultdict(float)   # hour → summed weekday volume
    tot_we  = defaultdict(float)
    for r in rows:
        t = datetime.fromisoformat(r["from"])
        v = float(r["volume"])
        (tot_we if t.weekday() >= 5 else tot_wd)[t.hour] += v
    if not tot_wd:
        return None
    am = max(range(5, 13),  key=lambda h: tot_wd.get(h, 0))
    pm = max(range(13, 21), key=lambda h: tot_wd.get(h, 0))
    am_sum = sum(tot_wd.get(h, 0) for h in range(6, 10))
    pm_sum = sum(tot_wd.get(h, 0) for h in range(15, 19))
    wd_all = sum(tot_wd.values())
    we_all = sum(tot_we.values())
    return {
        "am_pm_ratio":   round(am_sum / pm_sum, 4) if pm_sum > 0 else 1.0,
        "peak_hour_am":  float(am),
        "peak_hour_pm":  float(pm),
        "weekend_ratio": round(we_all / wd_all, 4) if wd_all > 0 else 0.0,
    }


def hourly_shares(rows: list[dict], heading: str) -> list[dict]:
    """Mean share for this heading per (hour, day-type), coverage-filtered."""
    acc: dict[tuple[int, int], list[float]] = defaultdict(list)
    by_ts: dict[str, dict[str, float]] = defaultdict(dict)
    cov: dict[str, float] = {}
    for r in rows:
        by_ts[r["from"]][r["heading"]] = float(r["volume"])
        try:
            cov[r["from"]] = float(r["coverage_pct"] or 0)
        except ValueError:
            cov[r["from"]] = 0.0

    for ts, vols in by_ts.items():
        if cov.get(ts, 0) < MIN_COVERAGE or heading not in vols:
            continue
        total = sum(vols.values())
        if total <= 0:
            continue
        t = datetime.fromisoformat(ts)
        key = (t.hour, 1 if t.weekday() >= 5 else 0)
        acc[key].append((vols[heading] / total, total))

    out = []
    for (hour, is_we), pairs in sorted(acc.items()):
        mean_total = sum(t for _, t in pairs) / len(pairs)
        if mean_total < MIN_TOTAL_VEH_H:
            continue
        out.append({
            "hour": hour, "is_weekend": is_we,
            "share": round(sum(s for s, _ in pairs) / len(pairs), 4),
            "n_obs": len(pairs),
            "mean_total_veh_h": round(mean_total, 1),
        })
    return out


def main() -> None:
    with open(STATIONS_OK) as f:
        stations = [s for s in json.load(f) if s["matched"]]

    header = (["station_id", "city", "heading", "hour", "is_weekend",
               "share", "n_obs", "mean_total_veh_h"]
              + FEATURE_NAMES + PROFILE_FEATURES)
    n_rows = n_stations = 0

    with open(OUT_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for st in stations:
            rows = load_volumes(st["id"])
            if not rows:
                continue
            prof = profile_features(rows)
            if prof is None:
                continue
            G = load_city_graph(st["city"])
            u, v, k = map(int, st["osm_edge"].split("_"))
            data = G.get_edge_data(u, v, k)

            wrote = False
            for heading, bearing in st["heading_bearings"].items():
                feats = edge_features(st["city"], st["lat"], st["lon"],
                                      bearing, data)
                for share_row in hourly_shares(rows, heading):
                    w.writerow({
                        "station_id": st["id"], "city": st["city"],
                        "heading": heading, **share_row, **feats, **prof,
                    })
                    n_rows += 1
                    wrote = True
            n_stations += wrote

    print(f"Wrote {OUT_PATH}  ({n_rows} rows from {n_stations} stations)")


if __name__ == "__main__":
    main()
