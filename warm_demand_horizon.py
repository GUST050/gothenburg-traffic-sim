#!/usr/bin/env python3
"""Pre-warm the demand day library over a date horizon (speed plan stage C).

A consumer's window is calibrated per calendar day, and a day's entry is keyed
by the POOL COMPOSITION of the window it was calibrated in — the set of
day-type geometries the candidate pool draws from (weekday, weekend, or both;
a holiday behaves as a weekend). Any window of any length therefore asks for
one of exactly three compositions, and a date is fully warm once it holds two
entries: its OWN single type, and the mixed one.

So the plan is built from what has to be covered rather than from a fixed
weekly shape:

  * one window per maximal run of consecutive same-type days — this is what
    gives a weekday its ``(weekday,)`` entry and a holiday or weekend day its
    ``(weekend,)`` entry, and it keeps holding across holidays, where a fixed
    Mon–Fri window silently becomes mixed and leaves the week's weekdays
    without a single-type entry at all (30 such dates in 2027);
  * weekly Mon–Sun windows for the ``(weekday, weekend)`` entries, plus a
    window anchored at each end of the horizon so clipped edge weeks are
    covered too (3 such dates at the start of 2027).

Anything a plan cannot cover is REPORTED, never assumed: a horizon too short
to contain a mixed window has no mixed entries, and the run says so. Warming
is coverage, never a correctness requirement — an uncovered day simply builds
cold when it is first needed, at a few minutes, because the library rebuilds
any missing or stale day by construction.

Warming's product is the day library. Each window build also writes a full
uncompressed archive of itself under ``runs/demand-*`` (~100 MB per day-slot,
~75 GB over a year) under a build key no consumer ever looks up — a warm
window is not a window anyone asks for, only the days inside it are. Those
archives are therefore discarded as each build completes unless
``--keep-archives`` says otherwise; per-day provenance lives in the library
manifests (fit report, source hashes) and the per-window build logs.

Built to survive a year-long run: every build is a fresh subprocess of the
tracked builder writing to its own log, the live release is snapshotted and
restored around each one, a failed window is recorded and the run CONTINUES
(a single bad day must not abandon a horizon), disk headroom is checked before
each build, and an interrupted run resumes from its progress file, skipping
only work whose code fingerprint and stored dates still match.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from demand.day_library import DEFAULT_ROOT as LIBRARY_ROOT
from demand.intake import pool_key_for
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation.monthly_demand import (
    recover_live_demand_release,
    restore_live_demand_release,
    snapshot_live_demand_release,
)
from traffic_sim.simulation.workspace import WorkspaceLock

# The exact files a stored day's identity is hashed over (build_sumo_demand's
# DayIdentity.source_hashes). A resumed run may skip an item only while these
# are unchanged — otherwise the entries it recorded can no longer be hit.
IDENTITY_SOURCE_FILES = {
    "build_candidates": Path("build_candidates.py"),
    "build_sumo_demand": Path("build_sumo_demand.py"),
    "pfe": Path("traffic_sim/demand/pfe.py"),
    "pfe_kernel": Path("traffic_sim/demand/pfe_kernel.py"),
    "demand/calibration.py": Path("demand/calibration.py"),
    "demand/structure.py": Path("demand/structure.py"),
    "demand/locations.py": Path("demand/locations.py"),
}

FLOWS_PATH = {"historical": Path("web/data/flows.json"),
              "forecast": Path("web/data/flows_forecast.json")}

# What a warmed day-slot ACTUALLY costs on disk, measured on this machine
# (2026-07-22, three real windows of 4, 7 and 3 days). Warming writes three
# things, not one, and only the first is the product:
#
#   day library entry   13.1 MB  gzipped route/agent files + fit reports
#   run archive        101.8 MB  runs/demand-*/ — the tracked builder copies
#                                every product of every build, uncompressed
#   candidate pool      19.3 MB  sumo/candidate_cache/ — what makes a warm
#                                rebuild seconds instead of minutes
#
# The first projection here counted only the library and was 8x optimistic:
# a 2027 horizon is ~99 GB, not ~12 GB. The archive is the only redundant
# part (the library holds the same demand, gzipped, and a window is
# reassembled from it in seconds) — hence --discard-archives.
LIBRARY_MB_PER_DAY_SLOT = 13.1
ARCHIVE_MB_PER_DAY_SLOT = 101.8
CANDIDATE_POOL_MB_PER_DAY_SLOT = 19.3
DAY_SLOT_ESTIMATE_MB = (LIBRARY_MB_PER_DAY_SLOT + ARCHIVE_MB_PER_DAY_SLOT
                        + CANDIDATE_POOL_MB_PER_DAY_SLOT)
DEFAULT_MIN_FREE_GB = 15.0
PROGRESS_SCHEMA_VERSION = 1


def day_type(day: pd.Timestamp) -> str:
    """``weekday`` or ``weekend`` — the geometry pool this date draws from."""
    return pool_key_for(day)


def composition_of(start: pd.Timestamp, days: int) -> tuple[str, ...]:
    return tuple(sorted({day_type(start + pd.Timedelta(days=offset))
                         for offset in range(days)}))


def _item(start: pd.Timestamp, days: int) -> dict:
    composition = composition_of(start, days)
    return {
        "label": "+".join(composition),
        "start_date": start.strftime("%Y-%m-%d"),
        "days": days,
        "composition": list(composition),
    }


def same_type_runs(first: pd.Timestamp, last: pd.Timestamp) -> list[dict]:
    """One window per maximal run of consecutive same-type days."""
    items: list[dict] = []
    run_start, day = first, first
    while day <= last:
        following = day + pd.Timedelta(days=1)
        ends = following > last or day_type(following) != day_type(run_start)
        if ends:
            items.append(_item(run_start, int((day - run_start).days) + 1))
            run_start = following
        day = following
    return items


def mixed_windows(first: pd.Timestamp, last: pd.Timestamp) -> list[dict]:
    """Windows that give every date its ``(weekday, weekend)`` entry.

    Whole ISO weeks inside the horizon, plus one window anchored at each
    horizon edge so a range starting or ending mid-week is covered as well.
    Windows that turn out not to be mixed (a horizon of only weekdays, say)
    are dropped: they would duplicate a same-type run.
    """
    span = int((last - first).days) + 1
    candidates: list[tuple[pd.Timestamp, int]] = []
    monday = first - pd.Timedelta(days=int(first.dayofweek))
    while monday <= last:
        if monday >= first and monday + pd.Timedelta(days=6) <= last:
            candidates.append((monday, 7))
        monday += pd.Timedelta(days=7)
    edge_days = min(7, span)
    candidates.append((first, edge_days))
    candidates.append((last - pd.Timedelta(days=edge_days - 1), edge_days))
    items: list[dict] = []
    for start, days in candidates:
        if len(composition_of(start, days)) < 2:
            continue
        items.append(_item(start, days))
    return items


def plan_windows(first: pd.Timestamp, last: pd.Timestamp) -> list[dict]:
    """Every window build needed to warm ``[first, last]``, in calendar order."""
    items = same_type_runs(first, last) + mixed_windows(first, last)
    unique: dict[tuple[str, int], dict] = {}
    for item in items:
        unique.setdefault((item["start_date"], item["days"]), item)
    return sorted(unique.values(), key=lambda i: (i["start_date"], i["days"]))


def coverage(items: list[dict]) -> dict[str, set[tuple[str, ...]]]:
    """Which compositions each date ends up holding an entry under."""
    covered: dict[str, set[tuple[str, ...]]] = {}
    for item in items:
        start = pd.Timestamp(item["start_date"])
        composition = tuple(item["composition"])
        for offset in range(item["days"]):
            date = (start + pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
            covered.setdefault(date, set()).add(composition)
    return covered


def uncovered(items: list[dict], first: pd.Timestamp,
              last: pd.Timestamp) -> list[dict]:
    """Dates whose own-type or mixed entry this plan does not produce."""
    covered = coverage(items)
    gaps: list[dict] = []
    for day in pd.date_range(first, last, freq="D"):
        date = day.strftime("%Y-%m-%d")
        have = covered.get(date, set())
        missing = [composition
                   for composition in ((day_type(day),), ("weekday", "weekend"))
                   if composition not in have]
        if missing:
            gaps.append({"date": date,
                         "missing": ["+".join(c) for c in missing]})
    return gaps


def source_fingerprint() -> dict[str, str | None]:
    return {name: sha256_file(path)
            for name, path in sorted(IDENTITY_SOURCE_FILES.items())}


def library_has_dates(item: dict, root: Path = LIBRARY_ROOT) -> bool:
    """Cheap check that a recorded item's OWN entries still exist.

    Not a byte verification — ``DayLibrary.get`` does that when a build asks
    for an entry. This stops a progress file from claiming a horizon is warm
    after the library was pruned or deleted underneath it, and it checks the
    entry for THIS window's composition rather than merely that the date has
    some entry: a date usually holds two, and the one this item produced is
    the one that would otherwise be silently missing.
    """
    start = pd.Timestamp(item["start_date"])
    composition = list(item.get("composition")
                       or composition_of(start, item["days"]))
    for offset in range(item["days"]):
        date = (start + pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
        directory = Path(root) / date
        if not directory.is_dir():
            return False
        found = False
        for manifest_path in directory.glob("*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (manifest.get("identity") or {}).get(
                    "pool_composition") == composition:
                found = True
                break
        if not found:
            return False
    return True


def _demand_archives(root: Path = Path("runs")) -> set[Path]:
    return {path for path in Path(root).glob("demand-*") if path.is_dir()}


def discard_new_archives(before: set[Path]) -> int:
    """Delete the run archive this build just wrote, and report its bytes.

    The tracked builder copies every product of every build into runs/ —
    ~100 MB per day-slot, uncompressed, which is a full duplicate of demand
    the library already stores gzipped and can reassemble in seconds. Only
    directories that appeared during THIS build are touched: anything that
    existed before the build is another run's evidence and is never removed.
    """
    freed = 0
    for directory in sorted(_demand_archives() - before):
        freed += sum(path.stat().st_size
                     for path in directory.rglob("*") if path.is_file())
        shutil.rmtree(directory, ignore_errors=True)
    return freed


def stale_entries(root: Path = LIBRARY_ROOT) -> list[dict]:
    """Stored days that the current code can never hit again.

    A day's identity hashes the sources that produced it, so after any change
    to the solver or the generator its old entries are unreachable by
    construction — correct, and invisible: they simply sit on disk. Warming a
    year is ~12 GB, so a couple of superseded generations is the difference
    between a horizon fitting and not. An entry counts as stale only when its
    manifest names a DIFFERENT hash for a source we can still read; anything
    unparseable is left alone rather than guessed at.
    """
    current = source_fingerprint()
    stale: list[dict] = []
    for manifest_path in sorted(Path(root).glob("*/*/manifest.json")):
        manifest = _read_json(manifest_path)
        recorded = (manifest.get("identity") or {}).get("source_hashes")
        if not isinstance(recorded, dict) or not recorded:
            continue
        differing = sorted(
            name for name, digest in recorded.items()
            if current.get(name) is not None and current[name] != digest)
        if not differing:
            continue
        directory = manifest_path.parent
        size = sum(path.stat().st_size
                   for path in directory.rglob("*") if path.is_file())
        stale.append({"path": directory, "bytes": size,
                      "date": manifest.get("identity", {}).get("date"),
                      "superseded": differing})
    return stale


def _free_gb(path: Path = Path(".")) -> float:
    return shutil.disk_usage(path).free / 1e9


def _hms(seconds: float) -> str:
    seconds = int(max(0.0, seconds))
    return f"{seconds // 3600:d}h{(seconds % 3600) // 60:02d}m"


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def _source_year(source: str) -> int:
    """The single calendar year the chosen flow source can be built for."""
    path = FLOWS_PATH[source]
    if not path.is_file():
        raise SystemExit(f"missing flow source {path} — build it first "
                         f"(make all) before warming a {source} horizon")
    payload = _read_json(path)
    epoch = payload.get("epoch")
    if not isinstance(epoch, str):
        raise SystemExit(f"{path} has no epoch; cannot tell which year it covers")
    return pd.Timestamp(epoch).year


def item_command(item: dict, source: str) -> list[str]:
    return [sys.executable, "build_sumo_demand.py",
            "--start-date", item["start_date"], "--days", str(item["days"]),
            "--begin", "00:00", "--end", "24:00",
            "--source", source, "--keep-scenarios"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", choices=("historical", "forecast"))
    parser.add_argument("--from", dest="first",
                        help="First date to warm, YYYY-MM-DD.")
    parser.add_argument("--to", dest="last",
                        help="Last date to warm, inclusive.")
    parser.add_argument("--prune", action="store_true",
                        help="Report stored days no current build can hit "
                             "(their code fingerprint is superseded); with "
                             "--yes, delete them.")
    parser.add_argument("--yes", action="store_true",
                        help="Actually delete what --prune reports.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan and its coverage, build nothing.")
    parser.add_argument("--log-dir", type=Path, default=None,
                        help="Where per-window logs and progress.json go "
                             "(default runs/warm-horizon/<source>_<from>_<to>).")
    parser.add_argument("--no-resume", action="store_true",
                        help="Rebuild every window even if progress.json "
                             "already records it done.")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="Abort at the first failed window instead of "
                             "recording it and carrying on.")
    parser.add_argument("--lock-timeout", type=float, default=3600.0,
                        help="Seconds to wait for the shared demand workspace "
                             "(the web app's simulations hold it too) before "
                             "stopping, resumably.")
    parser.add_argument("--keep-archives", action="store_true",
                        help="Keep each build's runs/demand-* archive. Off by "
                             "default: warming's product is the day library, "
                             "and a warm window's archive carries a build key "
                             "no consumer ever looks up while costing ~100 MB "
                             "per day-slot (~75 GB over a year).")
    parser.add_argument("--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB,
                        help="Stop cleanly before a build that would leave "
                             "less than this much disk free.")
    return parser.parse_args()


def prune(delete: bool) -> None:
    entries = stale_entries()
    total = sum(entry["bytes"] for entry in entries) / 1e9
    if not entries:
        print("demand day library: nothing stale — every stored day is "
              "still reachable by the current code")
        return
    print(f"demand day library: {len(entries)} stored day(s), {total:.1f} GB, "
          f"superseded by code changes")
    for entry in entries[:10]:
        print(f"  {entry['date']}  {entry['bytes'] / 1e6:7.1f} MB  "
              f"superseded: {', '.join(entry['superseded'])}")
    print("  (entries built on another branch or checkout look superseded "
          "too — they are rebuilt if you switch back)")
    if not delete:
        print("  rerun with --prune --yes to delete them")
        return
    for entry in entries:
        shutil.rmtree(entry["path"], ignore_errors=True)
    print(f"deleted {len(entries)} stale day(s), freeing {total:.1f} GB")


def main() -> None:
    args = parse_args()
    if args.prune:
        prune(args.yes)
        return
    if not (args.source and args.first and args.last):
        raise SystemExit("--source, --from and --to are required to warm "
                         "(or use --prune on its own)")
    try:
        first, last = pd.Timestamp(args.first), pd.Timestamp(args.last)
    except ValueError as error:
        raise SystemExit(f"--from/--to must be YYYY-MM-DD: {error}") from None
    if last < first:
        raise SystemExit("--to must not precede --from")
    year = _source_year(args.source)
    if first.year != year or last.year != year:
        raise SystemExit(
            f"{args.source} flows cover {year}; a horizon must lie inside "
            f"that single year (got {first.date()}..{last.date()})")

    items = plan_windows(first, last)
    total_slots = sum(item["days"] for item in items)
    gaps = uncovered(items, first, last)
    per_slot = DAY_SLOT_ESTIMATE_MB - (
        0.0 if args.keep_archives else ARCHIVE_MB_PER_DAY_SLOT)
    projected_gb = total_slots * per_slot / 1000.0
    # flush everything: a year-long run is normally watched through a
    # redirected log, where buffered output can sit invisible for an hour.
    print(f"warming {first.date()}..{last.date()} ({args.source}): "
          f"{len(items)} window builds, {total_slots} day-slots, "
          f"~{projected_gb:.0f} GB projected, {_free_gb():.0f} GB free",
          flush=True)
    if args.keep_archives:
        print(f"  of that, ~{total_slots * ARCHIVE_MB_PER_DAY_SLOT / 1000:.0f}"
              f" GB is per-build run archives (runs/demand-*) you asked to "
              f"keep — the day library holds the same demand gzipped",
              flush=True)
    else:
        print(f"  each build's own run archive is discarded once its days are "
              f"stored (~{total_slots * ARCHIVE_MB_PER_DAY_SLOT / 1000:.0f} GB "
              f"not written; --keep-archives to retain them). Per-day "
              f"provenance stays in the library manifests and the build logs.",
              flush=True)
    if projected_gb > _free_gb() - args.min_free_gb:
        print(f"  WARNING: that does not fit above the "
              f"{args.min_free_gb:.0f} GB floor — the run will stop partway "
              f"and can be resumed after freeing space", flush=True)
    if gaps:
        print(f"  NOT covered by this plan: {len(gaps)} date/composition "
              f"gap(s) — those windows build cold when first asked for; a "
              f"horizon of at least a full week covers the mixed one",
              flush=True)
        for gap in gaps[:5]:
            print(f"    {gap['date']}: no {', '.join(gap['missing'])} entry",
                  flush=True)
    if args.dry_run:
        for item in items:
            print(f"  {item['label']:16s} {item['start_date']} "
                  f"+{item['days']}d")
        return

    # A 30 h background job is exactly what gets killed by a laptop
    # shutdown, an OOM or an impatient `kill`. The per-item restore lives in
    # a finally block, which SIGKILL skips and SIGTERM would skip too - so
    # recover any live release a previous run left snapshotted, and turn
    # SIGTERM into the same orderly stop as ^C.
    recovered = recover_live_demand_release()
    if recovered is not None:
        print(f"  restored the live demand release left behind by a killed "
              f"run ({len(recovered.get('entries', []))} products, "
              f"{len(recovered.get('trees', []))} directories)", flush=True)

    def _stop(signum, _frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, _stop)

    log_dir = Path(args.log_dir) if args.log_dir else (
        Path("runs") / "warm-horizon"
        / f"{args.source}_{first.date()}_{last.date()}")
    log_dir.mkdir(parents=True, exist_ok=True)
    progress_path = log_dir / "progress.json"
    fingerprint = source_fingerprint()
    progress = _read_json(progress_path)
    if (args.no_resume
            or progress.get("schema_version") != PROGRESS_SCHEMA_VERSION
            or progress.get("source_fingerprint") != fingerprint):
        if progress and not args.no_resume:
            print("  progress file predates the current demand code — "
                  "rebuilding every window", flush=True)
        progress = {"schema_version": PROGRESS_SCHEMA_VERSION,
                    "source": args.source,
                    "source_fingerprint": fingerprint,
                    "items": {}}
    done: dict[str, dict] = progress.setdefault("items", {})

    built = skipped = failed = 0
    freed_bytes = 0            # run-archive bytes reclaimed by --discard-archives
    slots_built = 0            # day-slots this run actually put through a build
    build_wall = 0.0           # wall time those slots cost
    pending_slots = total_slots
    started_all = time.perf_counter()
    stopped_early = None
    try:
        for index, item in enumerate(items, 1):
            key = f"{item['start_date']}+{item['days']}"
            record = done.get(key)
            if (record and record.get("status") == "ok"
                    and library_has_dates(item)):
                skipped += 1
                pending_slots -= item["days"]
                continue
            free = _free_gb()
            if free < args.min_free_gb:
                stopped_early = (f"only {free:.1f} GB free, below the "
                                 f"{args.min_free_gb:.0f} GB floor")
                break
            log_path = log_dir / f"{index:03d}_{item['label']}_" \
                                 f"{item['start_date']}_{item['days']}d.log"
            # The web app writes the same sumo/ and web/data files. Take the
            # shared workspace lock for the build and give it back between
            # builds, so a browser action waits at most one window rather
            # than interleaving its output with this run's.
            workspace = WorkspaceLock(f"warm_demand_horizon {os.getpid()}")
            if not workspace.acquire(timeout=args.lock_timeout, poll_s=5.0):
                holder = workspace.holder_description()
                print(f"[{index}/{len(items)}] waiting gave up: {holder}",
                      flush=True)
                stopped_early = f"workspace held by another job — {holder}"
                break
            snapshot = snapshot_live_demand_release()
            archives_before = _demand_archives()
            started = time.perf_counter()
            try:
                with open(log_path, "w") as log:
                    completed = subprocess.run(item_command(item, args.source),
                                               stdout=log, stderr=subprocess.STDOUT)
                if not args.keep_archives:
                    freed_bytes += discard_new_archives(archives_before)
            finally:
                restore_live_demand_release(snapshot)
                workspace.release()
            wall = time.perf_counter() - started
            slots_built += item["days"]
            pending_slots -= item["days"]
            build_wall += wall
            lines = log_path.read_text(errors="replace").splitlines()
            library = [line for line in lines if "demand day library" in line]
            done[key] = {
                "status": "ok" if completed.returncode == 0 else "failed",
                "returncode": completed.returncode,
                "wall_s": round(wall, 1),
                "days": item["days"],
                "label": item["label"],
                "log": str(log_path),
                "library": library[-1].strip() if library else "",
            }
            _write_json(progress_path, progress)
            # ETA from what building actually costs per day-slot in this run,
            # not from wall time (which counts resumed items as free).
            eta = build_wall / max(1, slots_built) * max(0, pending_slots)
            if completed.returncode == 0:
                built += 1
                print(f"[{index}/{len(items)}] {item['label']:16s} "
                      f"{item['start_date']} +{item['days']}d  {wall:6.0f}s  "
                      f"{done[key]['library']}  (eta {_hms(eta)})", flush=True)
            else:
                failed += 1
                tail = "\n    ".join(lines[-6:])
                print(f"[{index}/{len(items)}] FAILED {item['label']} "
                      f"{item['start_date']} +{item['days']}d "
                      f"(exit {completed.returncode}) — see {log_path}\n"
                      f"    {tail}", flush=True)
                if args.stop_on_error:
                    stopped_early = "stopping at the first failure as asked"
                    break
    except KeyboardInterrupt:
        stopped_early = "interrupted"
    finally:
        _write_json(progress_path, progress)

    total = time.perf_counter() - started_all
    if freed_bytes:
        print(f"  discarded {freed_bytes / 1e9:.1f} GB of this run's own "
              f"build archives")
    print(f"warm horizon: {built} built, {skipped} already warm, "
          f"{failed} failed, {len(items) - built - skipped - failed} not "
          f"reached, in {_hms(total)}")
    print(f"  logs and progress: {log_dir}")
    if stopped_early:
        print(f"  stopped early: {stopped_early} — rerun the same command to "
              f"continue where it left off")
    if failed or stopped_early:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
