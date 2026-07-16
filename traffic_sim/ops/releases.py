"""Immutable golden-release registry built on top of the ``runs/`` registry.

The normal run registry records how a job was produced.  A release is a
small, named set of *already produced* artifacts (for example the normal,
closure, and signal golden cases) that can be activated or rolled back with
one atomic pointer flip.  Files are copied into the release directory and
their SHA-256 is recorded, so later mutation of a shared web/sumo file cannot
silently change what the release means.

This module deliberately does not decide whether a case is healthy.  Callers
must pass their validation/gate record; ``activate_release`` only checks file
integrity and that the manifest is marked ``validated``.  That keeps release
publication separate from simulation code and makes an incomplete job
impossible to publish accidentally.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Releases extend the immutable run registry instead of creating a second
# top-level artifact tree.  Callers may still pass an explicit root in tests
# or for an isolated staging area.
RELEASES_DIR = Path("runs") / "releases"
SCHEMA_VERSION = 1
GOLDEN_CASES = ("normal", "closure", "signal")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit() -> str | None:
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def _safe_release_id(release_id: str) -> str:
    if not release_id or release_id in {".", ".."} or Path(release_id).name != release_id:
        raise ValueError("release_id must be a non-empty single path component")
    return release_id


def release_path(release_id: str, root: Path = RELEASES_DIR) -> Path:
    return Path(root) / _safe_release_id(release_id)


def create_release(release_id: str, cases: dict[str, Path], *,
                   validation: dict | None = None,
                   provenance: dict | None = None,
                   root: Path = RELEASES_DIR) -> dict:
    """Stage an immutable release from finished artifacts.

    ``cases`` maps a stable case name (``normal``, ``closure``, ``signal``)
    to an existing artifact.  The returned manifest is initially ``staged``;
    set ``validated`` only after the caller has run all golden health gates.
    Existing release IDs are rejected instead of overwritten.
    """
    rid = _safe_release_id(release_id)
    directory = release_path(rid, root)
    if directory.exists():
        raise FileExistsError(f"release already exists: {rid}")
    if not cases:
        raise ValueError("at least one release case is required")
    if len(set(cases)) != len(cases):
        raise ValueError("release case names must be unique")

    directory.mkdir(parents=True)
    entries = {}
    try:
        for case, source in cases.items():
            if not case or Path(case).name != case:
                raise ValueError(f"invalid case name: {case!r}")
            src = Path(source)
            if not src.is_file():
                raise FileNotFoundError(src)
            dest = directory / src.name
            if dest.exists():
                raise ValueError(f"case artifacts have duplicate filename: {src.name}")
            shutil.copy2(src, dest)
            entries[case] = {
                "file": dest.name,
                "source_path": str(src),
                "bytes": dest.stat().st_size,
                "sha256": sha256_file(dest),
            }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "release_id": rid,
            "status": "staged",
            "created_at": _now(),
            "git_commit": _git_commit(),
            "python": sys.version.split()[0],
            "cases": entries,
            "validation": validation or {},
            "provenance": provenance or {},
        }
        _atomic_json(directory / "manifest.json", manifest)
        return manifest
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def load_release(release_id: str, *, root: Path = RELEASES_DIR) -> dict:
    path = release_path(release_id, root) / "manifest.json"
    with open(path) as f:
        return json.load(f)


def validate_release(release_id: str, *, root: Path = RELEASES_DIR) -> list[str]:
    """Return integrity errors; an empty list means files are intact."""
    manifest = load_release(release_id, root=root)
    errors = []
    directory = release_path(release_id, root)
    for case, entry in manifest.get("cases", {}).items():
        path = directory / entry.get("file", "")
        if not path.is_file():
            errors.append(f"{case}: missing {entry.get('file')!r}")
            continue
        if path.stat().st_size != entry.get("bytes"):
            errors.append(f"{case}: size changed")
        if sha256_file(path) != entry.get("sha256"):
            errors.append(f"{case}: sha256 changed")
    return errors


def validate_golden_release(release_id: str, *, root: Path = RELEASES_DIR,
                            required_cases: tuple[str, ...] = GOLDEN_CASES,
                            require_case_pass: bool = True) -> list[str]:
    """Validate the complete reference-release contract.

    ``validate_release`` proves only file integrity.  A golden release also
    needs all three benchmark cases and a per-case validation record.  This
    helper is intentionally separate so ordinary ad-hoc releases remain
    supported while the named reference pointer cannot silently omit a
    closure or signal case.
    """
    manifest = load_release(release_id, root=root)
    errors = validate_release(release_id, root=root)
    cases = manifest.get("cases") or {}
    missing = [case for case in required_cases if case not in cases]
    errors.extend(f"missing golden case: {case}" for case in missing)
    validation = manifest.get("validation") or {}
    case_validation = validation.get("cases") if isinstance(validation, dict) else None
    if require_case_pass:
        if not isinstance(case_validation, dict):
            errors.append("golden release lacks per-case validation records")
        else:
            for case in required_cases:
                if case not in case_validation:
                    errors.append(f"golden case lacks validation: {case}")
                elif case_validation[case].get("status") != "pass":
                    errors.append(f"golden case is not passing: {case}")
    return errors


def activate_release(release_id: str, *, root: Path = RELEASES_DIR) -> dict:
    """Activate a validated release, retaining the previous pointer for rollback."""
    manifest = load_release(release_id, root=root)
    errors = validate_release(release_id, root=root)
    if errors:
        raise ValueError("release integrity failed: " + "; ".join(errors))
    if manifest.get("status") != "validated":
        raise ValueError("release must be marked validated before activation")
    pointer = Path(root) / "latest.json"
    previous = None
    if pointer.exists():
        with open(pointer) as f:
            previous = json.load(f).get("release_id")
    _atomic_json(pointer, {"release_id": release_id, "previous_release_id": previous,
                           "activated_at": _now()})
    return manifest


def activate_golden_release(release_id: str, *, root: Path = RELEASES_DIR) -> dict:
    """Activate only a complete normal/closure/signal golden release."""
    errors = validate_golden_release(release_id, root=root)
    if errors:
        raise ValueError("golden release validation failed: " + "; ".join(errors))
    return activate_release(release_id, root=root)


def mark_validated(release_id: str, validation: dict, *,
                   root: Path = RELEASES_DIR) -> dict:
    """Atomically mark a staged release as validated after external gates pass."""
    manifest = load_release(release_id, root=root)
    if validate_release(release_id, root=root):
        raise ValueError("cannot validate release: integrity check failed")
    manifest["status"] = "validated"
    manifest["validated_at"] = _now()
    manifest["validation"] = validation
    _atomic_json(release_path(release_id, root) / "manifest.json", manifest)
    return manifest


def active_release(*, root: Path = RELEASES_DIR) -> dict | None:
    pointer = Path(root) / "latest.json"
    if not pointer.exists():
        return None
    with open(pointer) as f:
        return json.load(f)


def rollback_release(*, root: Path = RELEASES_DIR) -> dict:
    pointer = active_release(root=root)
    if not pointer or not pointer.get("previous_release_id"):
        raise ValueError("no previous release is available for rollback")
    return activate_release(pointer["previous_release_id"], root=root)
