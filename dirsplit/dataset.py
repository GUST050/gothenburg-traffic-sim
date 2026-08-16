"""
Build the training tables for the direction-split model.

Usage:
  python3 -m dirsplit.dataset

TWO tables are written, and the difference between them is the point.

``training_table_v2.csv`` (PRIMARY, one row per station x local date x hour x
heading) keeps the raw observation: both directions' counts, the hour's total,
the observed share, the local calendar context, the source's own coverage
percentage, an explicit missingness reason when a row cannot carry a label, and
a ``day_block_id`` that keeps simultaneously observed hours together.  Those
last two are what a validation design needs: real day-to-day variation, and
blocks that can be held out whole so correlated hours never straddle a fold.

``training_table.csv`` (DIAGNOSTIC, the legacy aggregate) collapses everything
to one mean share per station, heading, hour and day type.  That table is what
the deployed q10/q50/q90 models were learned from, which is exactly why the
quantiles describe spread BETWEEN aggregated station-hours rather than the
day-to-day variation a future day actually has.  It is retained for comparison
and for the existing training path; new work reads v2.

Feature semantics are kept separable rather than merged:

* ``profile_total_*`` — shape statistics of BOTH directions summed.  This is
  what the legacy table's ``am_pm_ratio``/``peak_hour_*``/``weekend_ratio``
  columns have always meant.
* ``profile_dir_*``   — the same statistics computed from THIS heading alone.

Five of six Gothenburg stations measure one carriageway, so only the second
form can be computed there with identical meaning.  Emitting both under
different names lets the model tournament decide which inference path wins
instead of silently substituting one for the other under a shared column name.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime

from .config import DATA_DIR, STATIONS_OK, VOLUMES_DIR
from .features import FEATURE_NAMES, edge_features, load_city_graph

OUT_PATH        = DATA_DIR / "training_table.csv"          # legacy aggregate
OUT_PATH_V2     = DATA_DIR / "training_table_v2.csv"       # raw observations
META_PATH_V2    = DATA_DIR / "training_table_v2.meta.json"
MIN_COVERAGE    = 80.0
MIN_TOTAL_VEH_H = 30.0

PROFILE_FEATURES = ["am_pm_ratio", "peak_hour_am", "peak_hour_pm", "weekend_ratio"]
PROFILE_STATS = ["am_pm_ratio", "peak_hour_am", "peak_hour_pm", "weekend_ratio"]
PROFILE_TOTAL_FEATURES = [f"profile_total_{name}" for name in PROFILE_STATS]
PROFILE_DIR_FEATURES = [f"profile_dir_{name}" for name in PROFILE_STATS]

#: Why a raw row carries no usable label.  Rows are KEPT with the reason
#: attached — a dropped row cannot be counted, and the count is the diagnostic.
MISSING_LOW_COVERAGE = "low_coverage"
MISSING_NO_OPPOSITE = "missing_opposite_heading"
MISSING_ZERO_TOTAL = "zero_total"
MISSING_LOW_VOLUME = "low_hourly_volume"

V2_COLUMNS = (
    ["station_id", "city", "heading", "local_datetime", "local_date", "hour",
     "weekday", "is_weekend", "month", "iso_week", "day_block_id",
     "station_day_id", "toward_count", "away_count", "total_count", "share",
     "coverage_pct", "included", "missing_reason"]
    + FEATURE_NAMES + PROFILE_TOTAL_FEATURES + PROFILE_DIR_FEATURES)


def load_volumes(station_id: str) -> list[dict] | None:
    path = VOLUMES_DIR / f"{station_id}.csv"
    if not path.exists():
        return None
    with open(path) as f:
        return list(csv.DictReader(f))


def _shape_stats(weekday_hourly: dict[int, float],
                 weekend_hourly: dict[int, float]) -> dict[str, float]:
    """The four profile statistics from an hour-of-day volume curve."""
    if not weekday_hourly:
        return {}
    am = max(range(5, 13), key=lambda h: weekday_hourly.get(h, 0))
    pm = max(range(13, 21), key=lambda h: weekday_hourly.get(h, 0))
    am_sum = sum(weekday_hourly.get(h, 0) for h in range(6, 10))
    pm_sum = sum(weekday_hourly.get(h, 0) for h in range(15, 19))
    wd_all = sum(weekday_hourly.values())
    we_all = sum(weekend_hourly.values())
    return {
        "am_pm_ratio":   round(am_sum / pm_sum, 4) if pm_sum > 0 else 1.0,
        "peak_hour_am":  float(am),
        "peak_hour_pm":  float(pm),
        # 0.3 matches predict.py's sensor_profile_features fallback for the
        # same degenerate case (weekday data present but sums to zero).
        "weekend_ratio": round(we_all / wd_all, 4) if wd_all > 0 else 0.3,
    }


def profile_features(rows: list[dict]) -> dict[str, float] | None:
    """Shape statistics of the TOTAL (both directions) — the legacy meaning."""
    tot_wd: dict[int, float] = defaultdict(float)
    tot_we: dict[int, float] = defaultdict(float)
    for r in rows:
        t = datetime.fromisoformat(r["from"])
        v = float(r["volume"])
        (tot_we if t.weekday() >= 5 else tot_wd)[t.hour] += v
    return _shape_stats(tot_wd, tot_we) or None


def directional_profile_features(rows: list[dict], heading: str
                                 ) -> dict[str, float] | None:
    """The same statistics from ONE heading — computable at a single-direction
    station, so training and Gothenburg use identical inputs on that path."""
    tot_wd: dict[int, float] = defaultdict(float)
    tot_we: dict[int, float] = defaultdict(float)
    for r in rows:
        if r["heading"] != heading:
            continue
        t = datetime.fromisoformat(r["from"])
        v = float(r["volume"])
        (tot_we if t.weekday() >= 5 else tot_wd)[t.hour] += v
    return _shape_stats(tot_wd, tot_we) or None


def hourly_shares(rows: list[dict], heading: str) -> list[dict]:
    """Mean share for this heading per (hour, day-type), coverage-filtered.

    The legacy aggregate.  Kept for the diagnostic table and the existing
    training path; the raw grain lives in :func:`raw_observations`.
    """
    acc: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
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


def raw_observations(rows: list[dict], heading: str, *, city: str,
                     station_id: str) -> list[dict]:
    """One record per observed hour for this heading, missingness included.

    Every hour the source reported is emitted.  ``included`` says whether the
    row carries a usable label; ``missing_reason`` says why not.  Nothing is
    silently discarded, because "how much of the data could not be used, and
    where" is itself a result the applicability report has to state.
    """
    by_ts: dict[str, dict[str, float]] = defaultdict(dict)
    cov: dict[str, float] = {}
    for r in rows:
        by_ts[r["from"]][r["heading"]] = float(r["volume"])
        try:
            cov[r["from"]] = float(r["coverage_pct"] or 0)
        except (TypeError, ValueError):
            cov[r["from"]] = 0.0

    out = []
    for ts in sorted(by_ts):
        vols = by_ts[ts]
        if heading not in vols:
            continue
        stamp = datetime.fromisoformat(ts)
        toward = vols[heading]
        total = sum(vols.values())
        away = total - toward
        coverage = cov.get(ts, 0.0)
        reason = ""
        if len(vols) < 2:
            reason = MISSING_NO_OPPOSITE
        elif coverage < MIN_COVERAGE:
            reason = MISSING_LOW_COVERAGE
        elif total <= 0:
            reason = MISSING_ZERO_TOTAL
        elif total < MIN_TOTAL_VEH_H:
            # A share of three vehicles out of five is noise, not signal — but
            # it is DECLARED noise here rather than an invisible deletion.
            reason = MISSING_LOW_VOLUME
        local_date = stamp.strftime("%Y-%m-%d")
        out.append({
            "station_id": station_id, "city": city, "heading": heading,
            "local_datetime": ts, "local_date": local_date, "hour": stamp.hour,
            "weekday": stamp.weekday(), "is_weekend": int(stamp.weekday() >= 5),
            "month": stamp.month, "iso_week": stamp.isocalendar().week,
            # Simultaneous observations across stations in one city share a
            # block, so a held-out block removes a whole day everywhere at once.
            "day_block_id": f"{city}:{local_date}",
            "station_day_id": f"{station_id}:{local_date}",
            "toward_count": toward, "away_count": away, "total_count": total,
            "share": round(toward / total, 6) if total > 0 else "",
            "coverage_pct": coverage,
            "included": int(not reason), "missing_reason": reason,
        })
    return out


def main() -> None:
    with open(STATIONS_OK) as f:
        stations = [s for s in json.load(f) if s["matched"]]

    legacy_header = (["station_id", "city", "heading", "hour", "is_weekend",
                      "share", "n_obs", "mean_total_veh_h"]
                     + FEATURE_NAMES + PROFILE_FEATURES)
    n_rows = n_stations = 0
    n_v2 = n_v2_included = 0
    missing_counts: dict[str, int] = defaultdict(int)
    day_blocks: set[str] = set()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="") as legacy_file, \
            open(OUT_PATH_V2, "w", newline="") as raw_file:
        legacy = csv.DictWriter(legacy_file, fieldnames=legacy_header)
        legacy.writeheader()
        raw = csv.DictWriter(raw_file, fieldnames=V2_COLUMNS)
        raw.writeheader()
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
                    legacy.writerow({
                        "station_id": st["id"], "city": st["city"],
                        "heading": heading, **share_row, **feats, **prof,
                    })
                    n_rows += 1
                    wrote = True
                dir_prof = directional_profile_features(rows, heading) or {}
                profile_columns = {
                    **{f"profile_total_{name}": prof[name] for name in PROFILE_STATS},
                    **{f"profile_dir_{name}": dir_prof.get(name, "")
                       for name in PROFILE_STATS},
                }
                for record in raw_observations(rows, heading, city=st["city"],
                                               station_id=st["id"]):
                    raw.writerow({**record, **feats, **profile_columns})
                    n_v2 += 1
                    n_v2_included += record["included"]
                    if record["missing_reason"]:
                        missing_counts[record["missing_reason"]] += 1
                    day_blocks.add(record["day_block_id"])
            n_stations += wrote

    meta = {
        "schema_version": "dirsplit_training_table_v2",
        "grain": "station_id x local_date x hour x heading",
        "rows": n_v2,
        "rows_with_label": n_v2_included,
        "independent_day_blocks": len(day_blocks),
        "missing_reasons": dict(sorted(missing_counts.items())),
        "min_coverage_pct": MIN_COVERAGE,
        "min_total_veh_h": MIN_TOTAL_VEH_H,
        "source_resolution": "hourly",
        "resolution_note": ("The source publishes hourly volumes. Any 15-minute "
                            "profile derived downstream is an interpolation, and "
                            "must be labelled hour-resolved; this table never "
                            "claims quarter-hour observations."),
        "holiday_support": ("not encoded: no verified Norwegian holiday calendar "
                            "is bundled with this repository, so a holiday flag "
                            "would be a guess rather than a source fact"),
        "legacy_table": {
            "path": str(OUT_PATH), "rows": n_rows,
            "role": ("diagnostic only — station-hour means, the aggregation the "
                     "deployed q10/q50/q90 models were trained on"),
        },
    }
    with open(META_PATH_V2, "w") as f:
        json.dump(meta, f, indent=1)

    print(f"Wrote {OUT_PATH_V2}  ({n_v2} raw rows, {n_v2_included} labelled, "
          f"{len(day_blocks)} day blocks)")
    if missing_counts:
        print("  unusable rows: " + ", ".join(
            f"{reason}={count}" for reason, count in sorted(missing_counts.items())))
    print(f"Wrote {OUT_PATH}  ({n_rows} aggregated rows from {n_stations} "
          f"stations, diagnostic)")
    print(f"Wrote {META_PATH_V2}")


if __name__ == "__main__":
    main()
