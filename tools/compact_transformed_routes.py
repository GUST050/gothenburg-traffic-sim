#!/usr/bin/env python3
"""Losslessly compact durable monthly transformed-route evidence.

The store is content-addressed by the SHA-256 of the uncompressed XML.  This
tool therefore verifies each legacy ``.rou.xml`` file against its filename,
writes a deterministic gzip sibling, verifies the decompressed bytes against
the same digest, and only then removes the legacy file.  It is resumable and
safe to rerun: a verified gzip sibling is reused, while any invalid artifact
fails closed.

Run without ``--execute`` for a read-only inventory.  Do not execute while a
monthly search is actively publishing observations.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.fingerprint import sha256_file  # noqa: E402
from traffic_sim.simulation.monthly_sumo import (  # noqa: E402
    _atomic_gzip_content_addressed_copy,
    _sha256_gzip_payload,
)


DEFAULT_STORE = ROOT / "runs" / "closure-search-baselines" / "transformed-routes"
_LEGACY_NAME = re.compile(r"^([0-9a-f]{64})\.rou\.xml$")


def compact_store(
    store: Path,
    *,
    execute: bool,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> dict[str, int]:
    """Inventory or compact ``store`` and return exact byte/file totals."""
    totals = {
        "legacy_files": 0,
        "legacy_bytes": 0,
        "compressed_files_written": 0,
        "compressed_bytes": 0,
        "legacy_files_removed": 0,
        "bytes_reclaimed": 0,
    }
    for source in sorted(store.glob("*/*.rou.xml")):
        match = _LEGACY_NAME.fullmatch(source.name)
        if match is None or source.parent.name != source.name[:2]:
            raise ValueError(f"unexpected transformed-route path: {source}")
        expected = match.group(1)
        source_bytes = source.stat().st_size
        totals["legacy_files"] += 1
        totals["legacy_bytes"] += source_bytes
        if sha256_file(source) != expected:
            raise ValueError(
                f"legacy transformed route does not hash to its filename: {source}")
        if not execute:
            continue

        destination = source.with_name(source.name + ".gz")
        if destination.is_file():
            if _sha256_gzip_payload(destination) != expected:
                raise ValueError(
                    f"compressed transformed route is invalid: {destination}")
        else:
            _atomic_gzip_content_addressed_copy(
                source, destination, expected_sha256=expected)
            totals["compressed_files_written"] += 1
        compressed_bytes = destination.stat().st_size
        totals["compressed_bytes"] += compressed_bytes
        if _sha256_gzip_payload(destination) != expected:
            raise ValueError(
                f"compressed transformed route failed final verification: "
                f"{destination}")
        source.unlink()
        totals["legacy_files_removed"] += 1
        totals["bytes_reclaimed"] += source_bytes - compressed_bytes
        if progress is not None and totals["legacy_files_removed"] % 100 == 0:
            progress(dict(totals))
    return totals


def _format_gib(value: int) -> str:
    return f"{value / (1024 ** 3):.2f} GiB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument(
        "--execute", action="store_true",
        help="write verified gzip artifacts and remove verified legacy XML")
    args = parser.parse_args()
    if not args.store.is_dir():
        parser.error(f"store does not exist: {args.store}")

    def report_progress(current: dict[str, int]) -> None:
        print(
            f"progress: removed={current['legacy_files_removed']} "
            f"reclaimed={_format_gib(current['bytes_reclaimed'])}",
            flush=True,
        )

    totals = compact_store(
        args.store,
        execute=args.execute,
        progress=report_progress if args.execute else None,
    )
    mode = "compacted" if args.execute else "inventoried"
    print(
        f"{mode}: legacy_files={totals['legacy_files']} "
        f"legacy_bytes={_format_gib(totals['legacy_bytes'])} "
        f"written={totals['compressed_files_written']} "
        f"removed={totals['legacy_files_removed']} "
        f"reclaimed={_format_gib(totals['bytes_reclaimed'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
