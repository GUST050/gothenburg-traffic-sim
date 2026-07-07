"""
Fetch Norwegian station metadata and hourly directional volumes (open data).

Usage (from repo root):
  python3 -m dirsplit.fetch_norway --stations              # metadata for all cities
  python3 -m dirsplit.fetch_norway --volumes [--limit 20]  # resumable, skips done

Volumes: for each station we pick the newest PREFERRED_YEAR that lies inside
the station's dataTimeSpan, and download SAMPLE_ISO_WEEKS of hourly volumes
by direction. Output one CSV per station in data/dirsplit/volumes/ (long
format: from,heading,volume,coverage_pct) — rerunning skips existing files,
so an interrupted fetch just continues.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from zoneinfo import ZoneInfo

from . import api
from .config import (CITIES, DATA_DIR, PREFERRED_YEARS, SAMPLE_ISO_WEEKS,
                     STATIONS_RAW, VOLUMES_DIR)
from .geo import in_bbox

STATION_QUERY = """
{{
  trafficRegistrationPoints(searchQuery: {{
    trafficType: VEHICLE, registrationFrequency: CONTINUOUS, countyNumbers: [{county}]
  }}) {{
    id name
    location {{ coordinates {{ latLon {{ lat lon }} }} roadReference {{ shortForm }} }}
    direction {{ from to }}
    dataTimeSpan {{ firstData latestData {{ volumeByHour }} }}
  }}
}}
"""

VOLUME_QUERY = """
{{
  trafficData(trafficRegistrationPointId: "{sid}") {{
    volume {{
      byHour(from: "{t0}", to: "{t1}"{after}) {{
        edges {{ node {{
          from
          total {{ volumeNumbers {{ volume }} coverage {{ percentage }} }}
          byDirection {{ heading total {{ volumeNumbers {{ volume }} }} }}
        }} }}
        pageInfo {{ hasNextPage endCursor }}
      }}
    }}
  }}
}}
"""


def fetch_stations() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stations: list[dict] = []
    for city, cfg in CITIES.items():
        data = api.query(STATION_QUERY.format(county=cfg["county"]))
        n_city = 0
        for p in data["trafficRegistrationPoints"]:
            ll = p["location"]["coordinates"]["latLon"]
            if not in_bbox(ll["lat"], ll["lon"], cfg["bbox"]):
                continue
            span = p.get("dataTimeSpan") or {}
            latest = (span.get("latestData") or {}).get("volumeByHour")
            if not latest:
                continue   # no hourly volume data at all
            stations.append({
                "id": p["id"], "name": p["name"], "city": city,
                "lat": ll["lat"], "lon": ll["lon"],
                "road_ref": p["location"]["roadReference"]["shortForm"],
                "heading_from": (p.get("direction") or {}).get("from"),
                "heading_to":   (p.get("direction") or {}).get("to"),
                "first_data": span.get("firstData"),
                "latest_data": latest,
            })
            n_city += 1
        print(f"  {city:<10} {n_city} stations inside bbox")
    with open(STATIONS_RAW, "w") as f:
        json.dump(stations, f, ensure_ascii=False, indent=1)
    print(f"Wrote {STATIONS_RAW}  ({len(stations)} stations)")
    return stations


def pick_year(station: dict) -> int | None:
    """Newest preferred year fully covered by the station's data span."""
    try:
        first = int(station["first_data"][:4])
        last_dt = station["latest_data"]
        last = int(last_dt[:4])
        # require the year to be complete through November (week 45)
        if last_dt[5:7] < "12":
            last -= 1
    except (TypeError, KeyError):
        return None
    for y in PREFERRED_YEARS:
        if first <= y <= last:
            return y
    return None


def week_bounds(year: int, iso_week: int) -> tuple[str, str]:
    # Norway observes the same EU DST rule as CET/CEST — a hardcoded +01:00
    # is wrong for 3 of the 4 SAMPLE_ISO_WEEKS (20 mid-May, 36/37 early Sept
    # are CEST/+02:00; only 45 early Nov is CET/+01:00), pulling in the wrong
    # real hour at each sampled week's boundary. Found 2026-07-07.
    tz = ZoneInfo("Europe/Oslo")
    monday = dt.date.fromisocalendar(year, iso_week, 1)
    t0 = dt.datetime(monday.year, monday.month, monday.day, tzinfo=tz)
    t1 = t0 + dt.timedelta(days=7)
    return t0.isoformat(), t1.isoformat()


def fetch_station_volumes(station: dict, year: int) -> list[dict]:
    rows: list[dict] = []
    for week in SAMPLE_ISO_WEEKS:
        t0, t1 = week_bounds(year, week)
        after = ""
        while True:
            data = api.query(VOLUME_QUERY.format(
                sid=station["id"], t0=t0, t1=t1, after=after))
            conn = data["trafficData"]["volume"]["byHour"]
            for edge in conn["edges"]:
                node = edge["node"]
                total = (node.get("total") or {})
                cov = ((total.get("coverage") or {}).get("percentage"))
                for d in node.get("byDirection") or []:
                    vol = ((d.get("total") or {}).get("volumeNumbers") or {}).get("volume")
                    if vol is None:
                        continue
                    rows.append({
                        "from": node["from"], "heading": d["heading"],
                        "volume": vol, "coverage_pct": cov,
                    })
            info = conn["pageInfo"]
            if not info["hasNextPage"]:
                break
            after = f', after: "{info["endCursor"]}"'
    return rows


def fetch_volumes(limit: int | None) -> None:
    VOLUMES_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATIONS_RAW) as f:
        stations = json.load(f)

    done = skipped = 0
    for st in stations:
        if limit is not None and done >= limit:
            break
        out = VOLUMES_DIR / f"{st['id']}.csv"
        if out.exists():
            skipped += 1
            continue
        year = pick_year(st)
        if year is None:
            continue
        rows = fetch_station_volumes(st, year)
        if not rows:
            continue
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["from", "heading", "volume", "coverage_pct"])
            w.writeheader()
            w.writerows(rows)
        done += 1
        print(f"  {st['city']:<10} {st['id']}  {st['name'][:34]:<34} "
              f"year={year}  {len(rows):>5} rows")
    print(f"Fetched {done} stations ({skipped} already done)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--stations", action="store_true")
    p.add_argument("--volumes",  action="store_true")
    p.add_argument("--limit", type=int, default=None,
                   help="max NEW stations to fetch volumes for (default: all)")
    args = p.parse_args()
    if args.stations:
        fetch_stations()
    if args.volumes:
        fetch_volumes(args.limit)
    if not (args.stations or args.volumes):
        p.error("pass --stations and/or --volumes")


if __name__ == "__main__":
    main()
