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
  - the LABEL and raw counts for one local station-date-hour-direction cell.

The primary v2 table preserves local dates and simultaneous direction counts;
that is what makes a leakage-free blocked-date test possible. Invalid cells
are retained with ``usable=0`` and an explicit missingness reason, then refused
by training/evaluation. A separate aggregate is written for diagnostics only.

Writes data/dirsplit/training_table.csv
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR, STATIONS_OK, VOLUMES_DIR
from .features import FEATURE_NAMES, edge_features, load_city_graph

OUT_PATH        = DATA_DIR / "training_table.csv"
AGGREGATED_PATH = DATA_DIR / "training_table_aggregated.csv"
META_PATH       = DATA_DIR / "training_table_meta.json"
DATASET_PROTOCOL = "dirsplit_training_table_v2"
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
        # 0.3 matches predict.py's sensor_profile_features fallback for the
        # same degenerate case (weekday data present but sums to zero) —
        # was 0.0 here, an unexplained inconsistency between the two
        # otherwise-parallel functions. Found 2026-07-07.
        "weekend_ratio": round(we_all / wd_all, 4) if wd_all > 0 else 0.3,
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


def daily_direction_rows(rows: list[dict], headings: list[str], *,
                         station_id: str, city: str,
                         features_by_heading: dict[str, dict[str, float]],
                         profile: dict[str, float]) -> list[dict]:
    """Preserve one source timestamp and both simultaneous counts.

    ``toward_count`` and ``away_count`` use the radial-to-city orientation,
    while ``share`` remains the share of the row's own heading. This keeps the
    two raw counts available to a binomial model without changing the deployed
    trainer's toward-centre row convention.
    """
    if len(headings) != 2:
        raise ValueError(
            f"station {station_id} needs exactly two headings, got {headings}")
    radial_index = FEATURE_NAMES.index("radial_cos")
    toward = max(headings,
                 key=lambda heading: features_by_heading[heading][
                     FEATURE_NAMES[radial_index]])
    away = headings[0] if headings[1] == toward else headings[1]

    by_ts: dict[str, dict[str, dict]] = defaultdict(dict)
    for raw in rows:
        heading = raw.get("heading")
        if heading in headings:
            by_ts[raw["from"]][heading] = raw

    output: list[dict] = []
    for timestamp, records in sorted(by_ts.items()):
        instant = datetime.fromisoformat(timestamp)
        missing = [heading for heading in headings if heading not in records]
        coverage_values: list[float] = []
        for record in records.values():
            try:
                coverage_values.append(float(record.get("coverage_pct") or 0))
            except (TypeError, ValueError):
                coverage_values.append(0.0)
        coverage = min(coverage_values, default=0.0)

        volumes: dict[str, float] = {}
        invalid_volume = False
        for heading, record in records.items():
            try:
                value = float(record["volume"])
            except (KeyError, TypeError, ValueError):
                invalid_volume = True
                continue
            if not math.isfinite(value) or value < 0:
                invalid_volume = True
            volumes[heading] = value

        total = sum(volumes.values()) if not missing and not invalid_volume \
            else None
        reasons: list[str] = []
        if missing:
            reasons.append("missing_direction:" + ",".join(sorted(missing)))
        if invalid_volume:
            reasons.append("invalid_volume")
        if coverage < MIN_COVERAGE:
            reasons.append("coverage_below_80")
        if total is not None and total <= 0:
            reasons.append("nonpositive_total")
        usable = not reasons

        for heading in headings:
            own = volumes.get(heading)
            output.append({
                "station_id": station_id,
                "city": city,
                "heading": heading,
                "direction_role": "toward" if heading == toward else "away",
                "local_timestamp": timestamp,
                "local_date": instant.date().isoformat(),
                "day_block_id": f"{station_id}|{instant.date().isoformat()}",
                "weekday": instant.weekday(),
                "hour": instant.hour,
                "is_weekend": int(instant.weekday() >= 5),
                "month": instant.month,
                # The source carries no Norwegian holiday flag. Blank is an
                # explicit unknown, not a fabricated weekday assumption.
                "is_holiday": "",
                "toward_count": volumes.get(toward, ""),
                "away_count": volumes.get(away, ""),
                "total_count": total if total is not None else "",
                "share": (own / total if usable and own is not None and total
                          else ""),
                "coverage_pct": coverage,
                "usable": int(usable),
                "missingness_reason": ";".join(reasons),
                "n_obs": 1,
                "mean_total_veh_h": total if total is not None else "",
                **features_by_heading[heading],
                **profile,
            })
    return output


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv_atomic(path: Path, header: list[str], rows: list[dict]) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    with open(partial, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header,
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(partial, path)


def main() -> None:
    with open(STATIONS_OK) as f:
        stations = [s for s in json.load(f) if s["matched"]]

    raw_header = [
        "station_id", "city", "heading", "direction_role",
        "local_timestamp", "local_date", "day_block_id", "weekday", "hour",
        "is_weekend", "month", "is_holiday", "toward_count", "away_count",
        "total_count", "share", "coverage_pct", "usable",
        "missingness_reason", "n_obs", "mean_total_veh_h",
    ] + FEATURE_NAMES + PROFILE_FEATURES
    aggregate_header = (["station_id", "city", "heading", "hour",
                         "is_weekend", "share", "n_obs",
                         "mean_total_veh_h"] + FEATURE_NAMES + PROFILE_FEATURES)
    raw_output: list[dict] = []
    aggregate_output: list[dict] = []
    volume_paths: list[Path] = []
    n_stations = 0

    for station in stations:
        rows = load_volumes(station["id"])
        if not rows:
            continue
        profile = profile_features(rows)
        if profile is None:
            continue
        graph = load_city_graph(station["city"])
        u, v, k = map(int, station["osm_edge"].split("_"))
        edge = graph.get_edge_data(u, v, k)
        features_by_heading = {
            heading: edge_features(station["city"], station["lat"],
                                   station["lon"], bearing, edge)
            for heading, bearing in station["heading_bearings"].items()
        }
        headings = list(station["heading_bearings"])
        raw_output.extend(daily_direction_rows(
            rows, headings, station_id=station["id"], city=station["city"],
            features_by_heading=features_by_heading, profile=profile))
        for heading in headings:
            for share_row in hourly_shares(rows, heading):
                aggregate_output.append({
                    "station_id": station["id"], "city": station["city"],
                    "heading": heading, **share_row,
                    **features_by_heading[heading], **profile,
                })
        volume_paths.append(VOLUMES_DIR / f"{station['id']}.csv")
        n_stations += 1

    _write_csv_atomic(OUT_PATH, raw_header, raw_output)
    _write_csv_atomic(AGGREGATED_PATH, aggregate_header, aggregate_output)
    source_records = [{"path": str(path), "sha256": _sha256(path)}
                      for path in sorted(volume_paths)]
    meta = {
        "protocol": DATASET_PROTOCOL,
        "primary_table": str(OUT_PATH),
        "primary_table_sha256": _sha256(OUT_PATH),
        "aggregate_diagnostic": str(AGGREGATED_PATH),
        "aggregate_diagnostic_sha256": _sha256(AGGREGATED_PATH),
        "rows": len(raw_output),
        "usable_rows": sum(int(row["usable"]) for row in raw_output),
        "stations": n_stations,
        "day_blocks": len({row["day_block_id"] for row in raw_output}),
        "source_volume_files": source_records,
        "source_content_key": hashlib.sha256(json.dumps(
            source_records, sort_keys=True, separators=(",", ":"))
            .encode("utf-8")).hexdigest(),
        "dataset_module_sha256": _sha256(Path(__file__).resolve()),
        "note": "The aggregate is diagnostic only; training and Gate M read the date-level primary table.",
    }
    partial_meta = META_PATH.with_suffix(META_PATH.suffix + ".partial")
    partial_meta.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    os.replace(partial_meta, META_PATH)
    print(f"Wrote {OUT_PATH} ({len(raw_output)} rows, "
          f"{meta['usable_rows']} usable, {n_stations} stations)")
    print(f"Wrote diagnostic aggregate {AGGREGATED_PATH} "
          f"({len(aggregate_output)} rows)")


if __name__ == "__main__":
    main()
