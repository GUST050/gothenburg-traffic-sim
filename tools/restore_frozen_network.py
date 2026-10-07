#!/usr/bin/env python3
"""Restore the exact SUMO network the frozen validation evidence binds.

The frozen warm-state contracts record the sha256 of sumo/net.net.xml. A fresh
``make sumo-net`` rebuilds the same network from web/data/graph.graphml, but
netconvert stamps its generation time into the header, so the bytes - and that
hash - change on every build. A clean clone (CI, another computer) therefore
could never reproduce the evidence's network identity.

This tool restores the tracked copy only after checking that the fresh build
is the same network apart from that one header line and the platform's
formatting of signed zero. If the tracked graph or
netconvert ever produces a different network, it stops instead of quietly
substituting old bytes.

Usage (after ``make sumo-net``): python3 tools/restore_frozen_network.py
"""

from __future__ import annotations

import hashlib
import lzma
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FROZEN_ARCHIVE = ROOT / "data" / "frozen_network" / "net.net.xml.xz"
SUMO_NETWORK = ROOT / "sumo" / "net.net.xml"
FROZEN_NETWORK_SHA256 = (
    "68ecde399ee7177bf8b3c9839a959170cca5d979f68bf15ca9f1cf6599ad5240")
GENERATED_ON_PREFIX = b"<!-- generated on "
# netconvert 1.27.1 on macOS writes a coordinate that rounds to zero as -0.00
# where Linux writes 0.00. Measured 2026-10-07 on the Linux x86_64 build CI
# uses: the only difference in 152 904 lines. Signed zero equals zero, so it
# is not drift; any non-zero value keeps its sign.
NEGATIVE_ZERO = re.compile(rb"(?<![0-9.])-(0(?:\.0+)?)(?![0-9.])")
DIFF_EXAMPLES = 3
DIFF_EXCERPT_BYTES = 160


class FrozenNetworkError(RuntimeError):
    """The frozen network cannot be restored safely."""


def _comparable_lines(payload: bytes) -> list[bytes]:
    return [NEGATIVE_ZERO.sub(rb"\1", line)
            for line in payload.splitlines(keepends=True)
            if not line.startswith(GENERATED_ON_PREFIX)]


def _describe_difference(frozen: list[bytes], built: list[bytes]) -> str:
    """Summarise where two networks differ so drift can be diagnosed."""
    differing = [(number, old, new) for number, (old, new)
                 in enumerate(zip(frozen, built), start=1) if old != new]
    examples = "; ".join(
        f"line {number}: frozen {old[:DIFF_EXCERPT_BYTES]!r} "
        f"built {new[:DIFF_EXCERPT_BYTES]!r}"
        for number, old, new in differing[:DIFF_EXAMPLES])
    return (f"{len(differing)} differing lines, {len(frozen)} frozen vs "
            f"{len(built)} built lines (timestamp excluded); {examples}")


def restore(archive: Path, network: Path, *,
            expected_sha256: str = FROZEN_NETWORK_SHA256) -> None:
    """Replace a fresh build at ``network`` with the frozen bytes."""
    if not network.is_file():
        raise FrozenNetworkError(
            f"no fresh build at {network}; run `make sumo-net` first")
    try:
        frozen = lzma.decompress(archive.read_bytes())
    except (OSError, lzma.LZMAError) as exc:
        raise FrozenNetworkError(f"cannot read {archive}: {exc}") from exc
    actual = hashlib.sha256(frozen).hexdigest()
    if actual != expected_sha256:
        raise FrozenNetworkError(
            f"{archive} has sha256 {actual}, expected {expected_sha256}")
    built_lines = _comparable_lines(network.read_bytes())
    frozen_lines = _comparable_lines(frozen)
    if built_lines != frozen_lines:
        raise FrozenNetworkError(
            f"network drift: a fresh build of {network} differs from the "
            "frozen network beyond its generation timestamp; "
            + _describe_difference(frozen_lines, built_lines))
    with tempfile.NamedTemporaryFile(dir=network.parent, delete=False) as out:
        out.write(frozen)
    os.chmod(out.name, network.stat().st_mode & 0o777)
    os.replace(out.name, network)


def main() -> int:
    try:
        restore(FROZEN_ARCHIVE, SUMO_NETWORK)
    except FrozenNetworkError as exc:
        print(f"restore_frozen_network: {exc}", file=sys.stderr)
        return 1
    print(f"Restored {SUMO_NETWORK.relative_to(ROOT)} "
          f"(sha256 {FROZEN_NETWORK_SHA256[:12]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
