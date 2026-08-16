#!/usr/bin/env python3
"""Fail closed on new or silently changed large repository artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


POLICY_PATH = Path(__file__).with_name("repo_hygiene_allowlist.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_files(root: Path) -> list[str]:
    """Return tracked plus unignored untracked files, relative to ``root``."""
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return sorted(
        item.decode("utf-8", errors="surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    )


def validate_repository(root: Path, policy_path: Path) -> tuple[list[str], int]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1:
        return ["unsupported repo hygiene schema_version"], 0

    threshold = policy.get("max_tracked_file_bytes")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        return ["max_tracked_file_bytes must be a positive integer"], 0

    allowlist = policy.get("legacy_large_files")
    if not isinstance(allowlist, dict):
        return ["legacy_large_files must be an object"], 0

    errors: list[str] = []
    large_count = 0
    repository_paths = set(repository_files(root))

    for relative, expected in sorted(allowlist.items()):
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            errors.append(f"unsafe large-file exception path: {relative}")
            continue
        if not isinstance(expected, dict):
            errors.append(f"large-file exception must be an object: {relative}")
            continue
        expected_size = expected.get("bytes")
        expected_digest = expected.get("sha256")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size < threshold
        ):
            errors.append(f"invalid exception size for {relative}")
        if (
            not isinstance(expected_digest, str)
            or len(expected_digest) != 64
            or set(expected_digest) - set("0123456789abcdef")
        ):
            errors.append(f"invalid exception sha256 for {relative}")

    for relative in sorted(repository_paths):
        path = root / relative
        if path.is_symlink() or not path.is_file():
            continue
        size = path.stat().st_size
        expected = allowlist.get(relative)
        if isinstance(expected, dict):
            large_count += 1
            expected_size = expected.get("bytes")
            expected_digest = expected.get("sha256")
            if size != expected_size:
                errors.append(
                    f"allowlisted artifact size drift: {relative}: "
                    f"expected {expected_size}, found {size}"
                )
                continue
            digest = sha256_file(path)
            if digest != expected_digest:
                errors.append(
                    f"allowlisted artifact digest drift: {relative}: "
                    f"expected {expected_digest}, found {digest}"
                )
            continue
        if size >= threshold:
            errors.append(
                f"new large file: {relative} ({size} bytes); use external "
                "artifact storage or review a pinned exception"
            )

    for relative in sorted(set(allowlist) - repository_paths):
        errors.append(f"stale large-file exception: {relative} is absent")

    return errors, large_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root (default: inferred from this script)",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=POLICY_PATH,
        help="large-file policy JSON",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    policy_path = args.policy
    if not policy_path.is_absolute():
        policy_path = (root / policy_path).resolve()
    try:
        errors, large_count = validate_repository(root, policy_path)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"repo hygiene check could not run: {exc}", file=sys.stderr)
        return 2
    if errors:
        print("repository hygiene failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"repository hygiene passed ({large_count} pinned large files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
