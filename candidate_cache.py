"""Exact-input cache for immutable candidate-route artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import fcntl
from pathlib import Path
from typing import Mapping

from pipeline_fingerprint import fingerprint_files, sha256_file

SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("sumo") / "candidate_cache"


def cache_key(config: dict, inputs: Mapping[str, Path],
              source_files: Mapping[str, Path]) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "config": config,
        "inputs": fingerprint_files(inputs),
        "source_files": fingerprint_files(source_files),
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()[:24]


def _entry(root: Path, key: str) -> Path:
    if len(key) != 24 or any(ch not in "0123456789abcdef" for ch in key):
        raise ValueError("invalid candidate cache key")
    return Path(root) / key


def restore(root: Path, key: str, outputs: Mapping[str, Path]) -> bool:
    """Restore a complete, validated entry into the requested output paths."""
    entry = _entry(root, key)
    manifest_path = entry / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return False
    stored = manifest.get("outputs") or {}
    if set(stored) != set(outputs):
        return False
    for label, destination in outputs.items():
        source = entry / label
        digest = sha256_file(source)
        if digest is None or digest != stored[label].get("sha256"):
            return False
    for label, destination in outputs.items():
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".cache.tmp")
        shutil.copy2(entry / label, temporary)
        os.replace(temporary, destination)
    return True


def store(root: Path, key: str, outputs: Mapping[str, Path]) -> None:
    """Publish a complete cache entry atomically after validating outputs."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    entry = _entry(root, key)
    lock_path = root / f".{key}.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        temporary = root / f".{key}.tmp"
        shutil.rmtree(temporary, ignore_errors=True)
        temporary.mkdir(parents=True)
        manifest = {"schema_version": SCHEMA_VERSION, "outputs": {}}
        try:
            for label, source in outputs.items():
                source = Path(source)
                digest = sha256_file(source)
                if digest is None:
                    raise FileNotFoundError(source)
                shutil.copy2(source, temporary / label)
                manifest["outputs"][label] = {
                    "sha256": digest,
                    "bytes": source.stat().st_size,
                }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, indent=1, sort_keys=True))
            # A completed entry is immutable.  A concurrent builder that won
            # the race remains authoritative because its key and bytes are
            # identical.
            if not entry.exists():
                os.replace(temporary, entry)
            else:
                shutil.rmtree(temporary, ignore_errors=True)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
