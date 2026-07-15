"""Content fingerprints for demand and scenario hand-offs.

The simulation products are only meaningful when their routes, network,
counts, priors, code, and SUMO version agree.  This module keeps that
contract small and dependency-free so it can be used by both CLI builders and
the web publication gate without importing the modelling stack.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Mapping

SCHEMA_VERSION = 1
_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str | None:
    """Return a file digest, or ``None`` when the required artifact is absent."""
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_files(paths: Mapping[str, Path]) -> dict[str, dict]:
    """Hash named artifacts while preserving missing-artifact information."""
    result: dict[str, dict] = {}
    for label, raw_path in sorted(paths.items()):
        path = Path(raw_path)
        digest = sha256_file(path)
        result[label] = {
            "sha256": digest,
            "bytes": path.stat().st_size if digest is not None else None,
        }
    return result


def git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def sumo_version(home: Path) -> str | None:
    binary = Path(home) / "bin" / "sumo"
    try:
        result = subprocess.run([str(binary), "--version"],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or result.stderr).strip() or None


def make_fingerprint(*, contract: dict, artifacts: Mapping[str, Path],
                     source_files: Mapping[str, Path],
                     sumo_home: Path | None = None) -> dict:
    """Create a reproducibility record and its stable build ID.

    Paths are labels only; absolute filesystem locations never enter the
    build ID.  Source-file hashes cover uncommitted local edits, while the Git
    commit and SUMO version make a published run auditable later.
    """
    record = {
        "schema_version": SCHEMA_VERSION,
        "contract": contract,
        "artifacts": fingerprint_files(artifacts),
        "source_files": fingerprint_files(source_files),
        "git_commit": git_commit(),
        "python": sys.version.split()[0],
        "sumo_version": sumo_version(sumo_home) if sumo_home else None,
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True).encode()
    record["build_id"] = hashlib.sha256(canonical).hexdigest()[:20]
    return record
