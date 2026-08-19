#!/usr/bin/env python3
"""Delete candidate-pool cache entries no current build can restore.

``sumo/candidate_cache`` is pure derived data: an entry is keyed on the full
fingerprint of the generator sources and of every input the pool is a
function of, and :func:`traffic_sim.demand.cache.restore` re-hashes each
stored artifact before using it. So a changed line in build_candidates.py
does not make an old entry WRONG — it makes it unreachable, forever, while
it keeps costing ~19 MB per day-slot. Warming a year leaves tens of
gigabytes of pools that no key can ever name again.

Deleting a cache entry can therefore only cost a rebuild, never change a
result. That is the whole safety argument, and it is why this tool exists
instead of a hand-written ``rm -rf``: it applies the rule consistently,
refuses to run while a demand build owns the shared workspace, and prints
what it would remove before it removes anything.

Which entries are unreachable cannot be answered by enumerating keys — a
key covers per-date artifacts (the day's measured shape, its pool blocks)
that only exist inside a build. Two rules are used instead:

  * an entry stored BEFORE the newest change to any file the key is hashed
    over cannot match a key computed from the files as they are now;
  * an entry whose stored pool is named by a day-library entry that IS
    still reachable is kept regardless, matched on the exact sha256 the
    day recorded — an exact, content-addressed override of the timestamp
    rule rather than a second guess about it.

    python3 tools/prune_candidate_cache.py            # report only
    python3 tools/prune_candidate_cache.py --yes      # delete

Run it after a code freeze and BEFORE warming a horizon, never during one.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demand.day_library import DEFAULT_ROOT as LIBRARY_ROOT  # noqa: E402
from traffic_sim.demand.build_lock import demand_build_lock  # noqa: E402
from traffic_sim.demand.cache import DEFAULT_ROOT as CACHE_ROOT  # noqa: E402

# Exactly what build_sumo_demand.generate_candidates hashes into a pool key,
# minus the two per-date artifacts written inside a build (real_day_shape,
# candidate_day_blocks) and the SUMO binary. Kept as one list so a reader can
# compare it against that call site directly.
KEY_SOURCES = (
    "build_candidates.py",
    "build_sumo_demand.py",
    "build_data.py",
    "dirsplit/geo.py",
    "demand/locations.py",
    "traffic_sim/demand/cache.py",
    "traffic_sim/intake/sensors.py",
    "traffic_sim/intake/direction_anchor.py",
    "traffic_sim/core/fingerprint.py",
)
KEY_INPUTS = (
    "sumo/net.net.xml",
    "web/data/graph.graphml",
    "web/data/network.geojson",
    "web/data/flows.json",
    "web/data/flows_forecast.json",
    "web/data/normal_profile.json",
    "data_in/sensors.json",
    "data_in/deso/population_2023.json",
    "data_in/deso/deso_goteborg.geojson",
    "data_in/deso/buildings.geojson",
    "data_in/deso/osm_buildings.geojson",
    "data_in/deso/osm_pois.geojson",
    "sumo/direction_split.json",
    "sumo/assignment_priors.json",
)


def newest_key_input_mtime(root: Path) -> tuple[float, str]:
    """When the pool-key inputs last changed, and which file changed last."""
    newest, owner = 0.0, "(none present)"
    for name in KEY_SOURCES + KEY_INPUTS:
        path = root / name
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if stamp > newest:
            newest, owner = stamp, name
    return newest, owner


def pools_named_by_live_days(library_root: Path) -> set[str]:
    """Pool digests recorded by day-library entries the current code can hit.

    Uses build_sumo_demand's own source inventory as the authority on what
    "current" means, so this tool cannot drift into a second opinion about
    which stored days are alive.
    """
    import build_sumo_demand

    current = build_sumo_demand.demand_day_source_hashes()
    live: set[str] = set()
    for manifest_path in Path(library_root).glob("*/*/manifest.json"):
        try:
            identity = json.loads(manifest_path.read_text())["identity"]
        except (OSError, ValueError, KeyError):
            continue
        if identity.get("source_hashes") != current:
            continue
        digest = identity.get("inputs", {}).get("candidate_pool")
        if isinstance(digest, str):
            live.add(digest)
    return live


def entry_pool_digest(entry: Path) -> str | None:
    """The sha256 this entry's manifest records for its candidate route file."""
    try:
        manifest = json.loads((entry / "manifest.json").read_text())
    except (OSError, ValueError):
        return None
    stored = manifest.get("outputs") or {}
    record = stored.get("candidates.rou.xml")
    return record.get("sha256") if isinstance(record, dict) else None


def directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true",
                        help="Actually delete what the report lists.")
    parser.add_argument("--keep-recent-hours", type=float, default=24.0,
                        help="Never touch an entry written this recently, so "
                             "a build in flight cannot lose its pool.")
    parser.add_argument("--cache-root", type=Path, default=ROOT / CACHE_ROOT)
    parser.add_argument("--library-root", type=Path, default=ROOT / LIBRARY_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_root = Path(args.cache_root)
    if not cache_root.is_dir():
        print(f"no candidate cache at {cache_root} — nothing to prune")
        return 0

    reference, owner = newest_key_input_mtime(ROOT)
    if reference == 0.0:
        print("none of the pool-key inputs could be read — refusing to guess "
              "which entries are unreachable")
        return 1
    protected_digests = pools_named_by_live_days(args.library_root)
    cutoff = min(reference, time.time() - args.keep_recent_hours * 3600.0)

    stale: list[tuple[Path, int]] = []
    kept = kept_by_digest = 0
    for entry in sorted(cache_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.stat().st_mtime >= cutoff:
            kept += 1
            continue
        digest = entry_pool_digest(entry)
        if digest is not None and digest in protected_digests:
            kept += 1
            kept_by_digest += 1
            continue
        stale.append((entry, directory_bytes(entry)))

    total_gb = sum(size for _entry, size in stale) / 1e9
    print(f"candidate pool cache: {cache_root}")
    print(f"  pool-key inputs last changed {time.strftime('%Y-%m-%d %H:%M', time.localtime(reference))}"
          f" ({owner})")
    print(f"  reachable-looking entries kept: {kept}"
          f" ({kept_by_digest} protected by a live day-library entry)")
    if not stale:
        print("  nothing unreachable — every stored pool can still be named")
        return 0
    print(f"  unreachable entries: {len(stale)}, {total_gb:.1f} GB")
    for entry, size in stale[:10]:
        stamp = time.strftime('%Y-%m-%d %H:%M', time.localtime(entry.stat().st_mtime))
        print(f"    {entry.name}  {size / 1e6:8.1f} MB  stored {stamp}")
    if len(stale) > 10:
        print(f"    … and {len(stale) - 10} more")
    if not args.yes:
        print("  rerun with --yes to delete them (a deleted pool is rebuilt "
              "on demand; only time is lost)")
        return 0

    # The lock the demand builders take before touching shared pool files.
    # Holding it here means no build can be between "restore" and "store"
    # while entries disappear underneath it.
    with demand_build_lock():
        removed = 0
        for entry, _size in stale:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
        for leftover in cache_root.glob(".*.lock"):
            if leftover.stat().st_mtime < cutoff:
                leftover.unlink(missing_ok=True)
        for leftover in cache_root.glob(".*.tmp"):
            if leftover.stat().st_mtime < cutoff:
                shutil.rmtree(leftover, ignore_errors=True)
    print(f"deleted {removed} unreachable pool(s), freeing {total_gb:.1f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
