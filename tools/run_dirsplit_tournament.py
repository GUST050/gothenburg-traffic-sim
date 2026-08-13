"""Run the step-2 model tournament and write a versioned report.

Two input modes, and the difference between them is important enough that the
report records which one was used:

``--table v2`` (preferred)
    `data/dirsplit/training_table_v2.csv` — one row per station-date-hour, so
    every fold kind is meaningful and the interval metrics measure what their
    names say.

``--table v1`` (fallback)
    `data/dirsplit/training_table.csv` — the legacy aggregate, one row per
    station-hour-daytype holding a MEAN share. The day-to-day variation has
    already been averaged away, so:
      * `blocked_date` folds are impossible and are skipped;
      * blocks are station x day-type rather than station x date;
      * coverage and interval scores describe spread BETWEEN aggregated
        station-hours, not between days, and must not be read as calibration
        evidence.
    It is still worth running, because the central-estimate question — does a
    complex model beat 50/50 out of sample at all — is answerable on it, and
    that question currently has a published answer this project disagrees
    with.

Neither mode is release evidence. Step 8 owns activation; this is the
measurement that tells step 2 which model to carry forward.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from dirsplit import evaluate as ev
from dirsplit import models as md
from dirsplit.config import DATA_DIR
from dirsplit.dataset_schema import DirectionRow, day_block_id, station_day_id

V1_TABLE = DATA_DIR / "training_table.csv"
V2_TABLE = DATA_DIR / "training_table_v2.csv"
OUT_PATH = DATA_DIR / "tournament_report_v1.json"

#: Reference dates used to give v1's aggregated rows a legal calendar field.
#: They are NOT observation dates — the v1 table has none — so they are chosen
#: to encode only the day type the row actually carries, and the report says
#: so. 2025-09-01 is a Monday, 2025-09-06 a Saturday.
_V1_WEEKDAY_DATE = "2025-09-01"
_V1_WEEKEND_DATE = "2025-09-06"

FEATURE_COLUMNS = [
    "hw_rank", "speed_kmh", "lanes", "oneway", "dist_centre_km", "radial_cos",
    "res_behind_km", "res_ahead_km", "res_asym", "major_behind_km",
    "major_ahead_km", "major_asym",
]
PROFILE_COLUMNS = ["am_pm_ratio", "peak_hour_am", "peak_hour_pm",
                   "weekend_ratio"]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_v1(path: Path = V1_TABLE) -> list[DirectionRow]:
    """Adapt the legacy aggregate into DirectionRow objects.

    One v1 row becomes one DirectionRow whose ``total_count`` is the stored
    ``mean_total_veh_h``. The block key becomes station x day-type, which is
    the finest honest grouping the aggregate supports: rows within a station
    and day type are correlated, and blocking on that keeps the bootstrap from
    treating them as independent.
    """
    rows: list[DirectionRow] = []
    with open(path) as handle:
        for record in csv.DictReader(handle):
            is_weekend = int(_float(record.get("is_weekend")))
            local_date = _V1_WEEKEND_DATE if is_weekend else _V1_WEEKDAY_DATE
            share = _float(record.get("share"))
            total = _float(record.get("mean_total_veh_h"))
            toward = share * total
            station = record["station_id"]
            city = record["city"]
            # The v1 table has no dates, so the finest correlation unit it can
            # express is station x day-type. Encoding the day type in the
            # placeholder date makes `station_day_id` come out as exactly that,
            # without weakening the schema's date/day-type consistency check.
            rows.append(DirectionRow(
                station_id=station,
                city=city,
                heading=record["heading"],
                local_date=local_date,
                hour=int(_float(record.get("hour"))),
                weekday=5 if is_weekend else 0,
                is_weekend=is_weekend,
                month=9,
                toward_count=toward,
                away_count=max(total - toward, 0.0),
                total_count=total,
                share=share,
                coverage_pct=100.0,
                missingness="ok",
                time_resolution="hour",
                day_block_id=day_block_id(city, local_date),
                station_day_id=station_day_id(station, local_date),
                features={name: _float(record.get(name))
                          for name in FEATURE_COLUMNS},
                profile={name: _float(record.get(name))
                         for name in PROFILE_COLUMNS},
            ))
    return rows


def load_v2(path: Path = V2_TABLE) -> list[DirectionRow]:
    rows: list[DirectionRow] = []
    with open(path) as handle:
        for record in csv.DictReader(handle):
            share_raw = record.get("share")
            rows.append(DirectionRow(
                station_id=record["station_id"],
                city=record["city"],
                heading=record["heading"],
                local_date=record["local_date"],
                hour=int(_float(record.get("hour"))),
                weekday=int(_float(record.get("weekday"))),
                is_weekend=int(_float(record.get("is_weekend"))),
                month=int(_float(record.get("month"))),
                toward_count=_float(record.get("toward_count")),
                away_count=_float(record.get("away_count")),
                total_count=_float(record.get("total_count")),
                share=(None if share_raw in (None, "") else _float(share_raw)),
                coverage_pct=_float(record.get("coverage_pct")),
                missingness=record.get("missingness", "ok"),
                time_resolution=record.get("time_resolution", "hour"),
                day_block_id=record["day_block_id"],
                station_day_id=record["station_day_id"],
                features={name: _float(record.get(name))
                          for name in FEATURE_COLUMNS},
                profile={name: _float(record.get(name))
                         for name in PROFILE_COLUMNS},
            ))
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", choices=("v1", "v2"), default="v2")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument(
        "--validation-out", type=Path, default=None,
        help="also write the plan's append-only benchmark artifact "
             "(validation/dirsplit_point_benchmark_v1.json)")
    parser.add_argument("--drop-oneway", action="store_true", default=True,
                        help="drop one-way stations, whose 0/1 shares poison "
                             "training (the v1 finding, reproduced)")
    args = parser.parse_args(argv)

    if args.table == "v2":
        if not V2_TABLE.exists():
            raise SystemExit(
                f"{V2_TABLE} not found — run `python3 -m dirsplit.dataset_v2` "
                "first, or pass --table v1 to score the legacy aggregate."
            )
        rows = load_v2()
        builders = None                       # every fold kind is meaningful
        caveats: list[str] = []
    else:
        if not V1_TABLE.exists():
            raise SystemExit(f"{V1_TABLE} not found")
        rows = load_v1()
        builders = {
            "leave_city_out": ev.leave_city_out,
            "leave_station_out": lambda r: ev.leave_station_out(r,
                                                                max_folds=40),
        }
        caveats = [
            "v1 aggregate: one row per station-hour-daytype holding a MEAN "
            "share, so day-to-day variation is already averaged away.",
            "blocked_date folds are skipped — the table carries no dates.",
            "blocks are station x day-type, not station x date.",
            "coverage / interval_score describe spread BETWEEN aggregated "
            "station-hours, NOT between days; they are not calibration "
            "evidence and must not be quoted as such.",
            "local_date/weekday/month are placeholders encoding only the "
            "row's day type.",
        ]

    if args.drop_oneway:
        before = len(rows)
        rows = [r for r in rows if _float(r.features.get("oneway")) < 0.5]
        dropped = before - len(rows)
    else:
        dropped = 0

    rows = md.orient_toward_centre(rows)

    candidates = md.default_candidates(FEATURE_COLUMNS + PROFILE_COLUMNS)
    report = ev.run_tournament(rows, candidates, fold_builders=builders)
    report["input_table"] = args.table
    report["caveats"] = caveats
    report["oneway_rows_dropped"] = dropped
    report["n_rows_scored"] = len(rows)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")

    if args.validation_out is not None:
        if args.validation_out.exists():
            raise SystemExit(
                f"{args.validation_out} already exists. Benchmark artifacts "
                "are append-only: publish a new version rather than "
                "overwriting recorded evidence."
            )
        args.validation_out.parent.mkdir(parents=True, exist_ok=True)
        args.validation_out.write_text(
            json.dumps(report, indent=1, sort_keys=True) + "\n")

    print(f"table            : {args.table}")
    for note in caveats:
        print(f"  CAVEAT         : {note}")
    print(f"rows scored      : {len(rows)} "
          f"({dropped} one-way rows dropped)")
    print(f"day blocks       : {report['n_day_blocks']}")
    print(f"selection rule   : {report['selection_rule']}")
    print(f"WINNER           : {report['selection']['winner']}")
    print(f"  reason         : {report['selection']['reason']}")
    print()
    for name, per_kind in sorted(report["reports"].items()):
        for kind, stats in sorted(per_kind.items()):
            group = stats.get("groups", {}).get("all", {})
            ci = group.get("paired_diff_ci95", [float("nan")] * 2)
            print(f"{name:26s} {kind:19s} "
                  f"MAE {stats['mae']:.4f} vs 50/50 {stats['mae_5050']:.4f} "
                  f"({stats['improvement_pct']:+.1f}%)  "
                  f"diff CI95 [{ci[0]:+.4f}, {ci[1]:+.4f}]")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
