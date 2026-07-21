#!/usr/bin/env python3
"""Pre-warm the demand day library over a date horizon (speed plan stage C).

For every ISO week in the range this launches up to three whole-day window
builds through the ordinary tracked builder — Mon–Sun, Mon–Fri, Sat–Sun —
because those three windows populate every pool composition any later
consumer can ask for:

  * a multi-day envelope mixing day types reads (weekday, weekend) entries,
    which the Mon–Sun build stores for each of its dates;
  * a weekday-only envelope of ANY length — including a single day, and
    including "Byt dag" on the website — reads (weekday,) entries, which the
    Mon–Fri build stores (composition is the SET of day types in the window,
    so a 5-day weekday window and a 1-day Tuesday window share entries);
  * weekend-only consumers likewise read the Sat–Sun build's (weekend,)
    entries.

A week containing a holiday is only partially coverable this way (the
holiday makes the Mon–Fri window mixed); anything missed simply builds cold
later, at a few minutes per day — warming is coverage, never a correctness
requirement (the library rebuilds any missing or stale day by construction).

Resumable and interruptible anywhere: every build is a fresh subprocess of
the tracked builder, days already in the library are reused, and the live
release is snapshotted/restored around every build. Safe to stop with ^C and
rerun; completed weeks cost seconds on the rerun.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from traffic_sim.simulation.monthly_demand import (
    restore_live_demand_release,
    snapshot_live_demand_release,
)

WINDOW_SHAPES = (
    ("mixed", 0, 7),      # Mon–Sun: (weekday, weekend) entries for all 7 dates
    ("weekday", 0, 5),    # Mon–Fri: (weekday,) entries
    ("weekend", 5, 2),    # Sat–Sun: (weekend,) entries
)


def plan_weeks(first: pd.Timestamp, last: pd.Timestamp) -> list[dict]:
    """One work item per (ISO week, window shape), clipped to the range.

    Windows are clipped at the horizon edges so a range starting mid-week
    still warms its first days; a clipped window's composition may differ
    from the full shape's, which is fine — entries are keyed by what the
    window actually contains.
    """
    items: list[dict] = []
    monday = first - pd.Timedelta(days=int(first.dayofweek))
    while monday <= last:
        for label, offset, days in WINDOW_SHAPES:
            start = monday + pd.Timedelta(days=offset)
            end_exclusive = start + pd.Timedelta(days=days)
            clipped_start = max(start, first)
            clipped_end = min(end_exclusive, last + pd.Timedelta(days=1))
            clipped_days = int((clipped_end - clipped_start).days)
            if clipped_days < 1:
                continue
            items.append({
                "label": label,
                "start_date": clipped_start.strftime("%Y-%m-%d"),
                "days": clipped_days,
            })
        monday += pd.Timedelta(days=7)
    return items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", choices=("historical", "forecast"),
                        required=True)
    parser.add_argument("--from", dest="first", required=True,
                        help="First date to warm, YYYY-MM-DD.")
    parser.add_argument("--to", dest="last", required=True,
                        help="Last date to warm, inclusive.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan and exit without building.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    first, last = pd.Timestamp(args.first), pd.Timestamp(args.last)
    if last < first:
        raise SystemExit("--to must not precede --from")
    items = plan_weeks(first, last)
    total_days = sum(item["days"] for item in items)
    print(f"warming {first.date()}..{last.date()} ({args.source}): "
          f"{len(items)} window builds, {total_days} day-slots")
    if args.dry_run:
        for item in items:
            print(f"  {item['label']:8s} {item['start_date']} +{item['days']}d")
        return
    started_all = time.perf_counter()
    for index, item in enumerate(items, 1):
        snapshot = snapshot_live_demand_release()
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [sys.executable, "build_sumo_demand.py",
                 "--start-date", item["start_date"],
                 "--days", str(item["days"]),
                 "--begin", "00:00", "--end", "24:00",
                 "--source", args.source, "--keep-scenarios"],
                capture_output=True, text=True)
        finally:
            restore_live_demand_release(snapshot)
        wall = time.perf_counter() - started
        if completed.returncode != 0:
            print(completed.stdout[-2000:])
            print(completed.stderr[-1500:])
            raise SystemExit(
                f"warm build failed at {item['label']} {item['start_date']}")
        library = [line for line in completed.stdout.splitlines()
                   if "demand day library" in line]
        print(f"[{index}/{len(items)}] {item['label']:8s} "
              f"{item['start_date']} +{item['days']}d  {wall:6.0f}s  "
              f"{library[-1].strip() if library else ''}", flush=True)
    print(f"horizon warm in {(time.perf_counter() - started_all) / 3600:.1f} h")


if __name__ == "__main__":
    main()
