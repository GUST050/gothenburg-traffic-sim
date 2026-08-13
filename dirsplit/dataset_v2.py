"""Build `training_table_v2` — day-level direction observations (plan step 1).

Usage:
    python3 -m dirsplit.dataset_v2

Reads the same raw inputs as `dirsplit/dataset.py` (the fetched hourly
volumes and the matched-station registry) and writes
``data/dirsplit/training_table_v2.csv`` plus a summary report. The difference
is grain, not source: v1 collapses every observed day into a mean share per
station-hour-daytype; v2 keeps one row per station-date-hour-heading with the
raw counts.

Why that matters is argued in `dataset_schema`'s docstring. The short form:
the quantile models are supposed to describe how much a *future day* can
differ, and v1 deleted exactly that variation before training began.

This module deliberately does not train, filter for a model, or decide what
is usable. It records what was observed and why a row is or is not usable;
step 2 decides what to do with that.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import DATA_DIR, STATIONS_OK, VOLUMES_DIR
from .dataset import PROFILE_FEATURES, load_volumes, profile_features
from .dataset_schema import (SCHEMA_VERSION, DirectionRow, make_row,
                             summarise, temporal_support)
from .features import FEATURE_NAMES, edge_features, load_city_graph

OUT_PATH = DATA_DIR / "training_table_v2.csv"
REPORT_PATH = DATA_DIR / "dataset_v2_report.json"


def pair_by_timestamp(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Group raw volume rows by timestamp.

    Returns ``{timestamp: {"volumes": {heading: value}, "coverage": pct}}``.
    Coverage is per-timestamp in the source, so the last non-empty value wins
    — the same convention `dataset.hourly_shares` uses.
    """
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"volumes": {}, "coverage": 0.0}
    )
    for raw in rows:
        stamp = raw["from"]
        try:
            volume = float(raw["volume"])
        except (TypeError, ValueError):
            continue
        grouped[stamp]["volumes"][raw["heading"]] = volume
        try:
            grouped[stamp]["coverage"] = float(raw.get("coverage_pct") or 0)
        except (TypeError, ValueError):
            grouped[stamp]["coverage"] = 0.0
    return dict(grouped)


def station_rows(
    station: Mapping[str, Any],
    raw_rows: list[Mapping[str, Any]],
    profile: Mapping[str, float],
    graph_edge_data: Mapping[str, Any] | None,
) -> list[DirectionRow]:
    """Every (date, hour, heading) observation for one station.

    Both headings of a station are emitted for every timestamp, including the
    one that is absent from the source at that hour — that absence is a
    ``MISSING_DIRECTION`` row rather than a silent gap, so a consumer can
    count what it is excluding.
    """
    grouped = pair_by_timestamp(raw_rows)
    headings = dict(station.get("heading_bearings") or {})
    out: list[DirectionRow] = []

    for stamp in sorted(grouped):
        entry = grouped[stamp]
        volumes: dict[str, float] = entry["volumes"]
        coverage = float(entry["coverage"])
        try:
            datetime.fromisoformat(stamp)
        except ValueError:
            continue
        for heading, bearing in sorted(headings.items()):
            feats = edge_features(
                station["city"], station["lat"], station["lon"],
                bearing, graph_edge_data,
            )
            toward = float(volumes.get(heading, 0.0))
            away = float(sum(v for h, v in volumes.items() if h != heading))
            out.append(
                make_row(
                    station_id=str(station["id"]),
                    city=str(station["city"]),
                    heading=heading,
                    timestamp=stamp,
                    toward_count=toward,
                    away_count=away,
                    coverage_pct=coverage,
                    features=feats,
                    profile=profile,
                    time_resolution="hour",
                    heading_present=heading in volumes,
                )
            )
    return out


def build(stations_path: Path = STATIONS_OK,
          volumes_dir: Path = VOLUMES_DIR) -> list[DirectionRow]:
    """Build every v2 row from the matched stations with fetched volumes."""
    with open(stations_path) as handle:
        stations = [s for s in json.load(handle) if s.get("matched")]

    rows: list[DirectionRow] = []
    graphs: dict[str, Any] = {}
    for station in stations:
        raw = load_volumes(station["id"]) if volumes_dir == VOLUMES_DIR else None
        if raw is None:
            path = Path(volumes_dir) / f"{station['id']}.csv"
            if not path.exists():
                continue
            with open(path) as handle:
                raw = list(csv.DictReader(handle))
        if not raw:
            continue
        profile = profile_features(raw)
        if profile is None:
            continue
        city = station["city"]
        if city not in graphs:
            graphs[city] = load_city_graph(city)
        graph = graphs[city]
        try:
            u, v, k = map(int, station["osm_edge"].split("_"))
            edge_data = graph.get_edge_data(u, v, k)
        except (KeyError, ValueError, AttributeError):
            edge_data = None
        rows.extend(station_rows(station, raw, profile, edge_data))
    return rows


def write_csv(rows: list[DirectionRow], path: Path = OUT_PATH) -> None:
    if not rows:
        raise SystemExit("no rows to write — fetch volumes first "
                         "(make dirsplit-volumes)")
    header = [
        "station_id", "city", "heading", "local_date", "hour", "weekday",
        "is_weekend", "month", "is_holiday", "toward_count", "away_count",
        "total_count", "share", "coverage_pct", "missingness",
        "time_resolution", "day_block_id", "station_day_id",
    ] + FEATURE_NAMES + PROFILE_FEATURES
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row.flat())


def write_report(rows: list[DirectionRow], path: Path = REPORT_PATH) -> dict:
    report = {
        **summarise(rows),
        "temporal_support": temporal_support(rows),
        "note": (
            "v2 keeps one row per station-date-hour-heading with raw counts. "
            "The v1 table's mean share per station-hour-daytype is derivable "
            "from this via dataset_schema.aggregate_like_v1, so v2 is a "
            "strict superset; the reverse is not true."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    rows = build()
    write_csv(rows, args.out)
    report = write_report(rows, args.report)

    print(f"Wrote {args.out}  ({report['n_rows']} rows, "
          f"{report['n_usable']} usable, {report['n_stations']} stations, "
          f"{report['n_day_blocks']} city-date blocks)")
    print(f"  schema        : {SCHEMA_VERSION}")
    print(f"  missingness   : {report['by_missingness']}")
    support = report["temporal_support"]
    print(f"  weekday hours : {support['weekday_hours']}")
    print(f"  weekend hours : {support['weekend_hours']}")
    if support["unsupported_weekend_hours"]:
        print(f"  UNSUPPORTED weekend hours: "
              f"{support['unsupported_weekend_hours']}")


if __name__ == "__main__":
    main()
