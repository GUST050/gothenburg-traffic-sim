#!/usr/bin/env python3
"""Atomically adopt the passing v16 warm states into the product cache.

Only cache entries carrying the exact v16 passing equivalence certificates are
eligible. Every entry is restored through the production cache validator before
anything appears at the destination. Existing destinations are never merged or
overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from traffic_sim.simulation.monthly_sumo import DEFAULT_BASELINE_CACHE
from traffic_sim.simulation.warm_state_cache import (
    WarmStateIdentity,
    restore_warm_state,
)


CAMPAIGN_KEY = "53ea67be4a683eb78522c3decbc7e0b9c19a642e5a03f44c3ab9120c06e36ef0"
DEFAULT_CAMPAIGN_ROOT = (
    Path("runs") / "monthly-warm-state-validation" / CAMPAIGN_KEY
)
DEFAULT_DESTINATION = Path(DEFAULT_BASELINE_CACHE).parent / "warm-state"
DEFAULT_RECORD = Path("validation") / "monthly_warm_state_v16_adoption.json"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_key(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "content_key"}
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_campaign(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = Path(root)
    record_path = root / "equivalence_record.json"
    record = _read(record_path)
    if (
        record.get("kind") != "monthly_warm_state_equivalence_record"
        or record.get("manifest_content_key") != CAMPAIGN_KEY
        or record.get("status") != "pass"
        or record.get("mismatch_count") != 0
        or record.get("cache_material_publishable") is not True
        or _canonical_key(record) != record.get("content_key")
    ):
        raise ValueError("v16 campaign is not a canonical passing record")
    published = record.get("published_cache_entries")
    if (
        not isinstance(published, list)
        or len(published) != 3
        or len(set(published)) != 3
        or any(not isinstance(key, str) or len(key) != 32 for key in published)
    ):
        raise ValueError("v16 campaign does not publish exactly three identities")

    source = root / "warm" / "warm-state"
    manifests: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="warm-v16-adoption-check-") as raw:
        scratch = Path(raw)
        for key in sorted(published):
            entry = source / key
            if entry.is_symlink() or not entry.is_dir():
                raise ValueError(f"published warm entry is missing: {key}")
            manifest_path = entry / "manifest.json"
            manifest = _read(manifest_path)
            identity = WarmStateIdentity.from_dict(manifest.get("identity", {}))
            if identity.content_key != key or manifest.get("key") != key:
                raise ValueError(f"warm entry identity disagrees with path: {key}")
            lookup = restore_warm_state(
                source, identity, scratch / f"{key}.state.xml.gz"
            )
            if not lookup.hit:
                raise ValueError(
                    f"warm entry {key} does not restore: {lookup.reason}"
                )
            manifests.append({
                "identity_key": key,
                "manifest_sha256": _sha256(manifest_path),
                "state_sha256": manifest["state"]["sha256"],
                "equivalence_sha256": hashlib.sha256(json.dumps(
                    manifest["equivalence"],
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")).hexdigest(),
            })
    return record, manifests


def adopt(root: Path, destination: Path, record_path: Path) -> dict[str, Any]:
    root = Path(root)
    destination = Path(destination)
    record_path = Path(record_path)
    campaign, entries = validate_campaign(root)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(
            f"refusing to merge or overwrite product warm cache: {destination}"
        )
    if record_path.exists() or record_path.is_symlink():
        raise FileExistsError(f"refusing to overwrite adoption record: {record_path}")

    source = root / "warm" / "warm-state"
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(
        prefix=".warm-v16-adopt-", dir=str(destination.parent)
    ))
    try:
        for entry in entries:
            key = entry["identity_key"]
            source_entry = source / key
            members = {path.name for path in source_entry.iterdir()}
            expected_members = {
                "manifest.json", "prefix_evidence.json", "state.xml.gz"
            }
            if members != expected_members or any(
                path.is_symlink() or not path.is_file()
                for path in source_entry.iterdir()
            ):
                raise ValueError(
                    f"warm entry {key} has an unexpected member set")
            staged_entry = staging / key
            staged_entry.mkdir()
            for name in sorted(expected_members):
                shutil.copy2(source_entry / name, staged_entry / name)
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    adoption = {
        "schema_version": 1,
        "kind": "monthly_warm_state_adoption",
        "campaign_content_key": CAMPAIGN_KEY,
        "campaign_record_content_key": campaign["content_key"],
        "campaign_record_sha256": _sha256(root / "equivalence_record.json"),
        "destination": str(destination),
        "entry_count": len(entries),
        "entries": entries,
        "activation": {
            "monthly_command_default": "warm",
            "cold_escape_hatch": "--cold-execution",
            "seed_workers": 1,
            "cache_miss": "provisional_bootstrap",
            "unusable_warm_attempt": "cold_fallback",
        },
    }
    adoption["content_key"] = _canonical_key(adoption)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = record_path.with_name(record_path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(adoption, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, record_path)
    finally:
        temporary.unlink(missing_ok=True)
    return adoption


def verify(root: Path, destination: Path, record_path: Path) -> dict[str, Any]:
    campaign, entries = validate_campaign(root)
    adoption = _read(record_path)
    if _canonical_key(adoption) != adoption.get("content_key"):
        raise ValueError("adoption record content key does not recompute")
    if (
        adoption.get("campaign_content_key") != CAMPAIGN_KEY
        or adoption.get("campaign_record_content_key") != campaign["content_key"]
        or adoption.get("entries") != entries
        or adoption.get("destination") != str(Path(destination))
    ):
        raise ValueError("adoption record disagrees with the passing campaign")
    expected = {entry["identity_key"] for entry in entries}
    actual = {
        path.name for path in Path(destination).iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    if actual != expected:
        raise ValueError(
            f"product warm cache identity set differs: {sorted(actual)}"
        )
    for entry in entries:
        key = entry["identity_key"]
        manifest_path = Path(destination) / key / "manifest.json"
        manifest = _read(manifest_path)
        if _sha256(manifest_path) != entry["manifest_sha256"]:
            raise ValueError(f"adopted manifest changed: {key}")
        identity = WarmStateIdentity.from_dict(manifest["identity"])
        with tempfile.TemporaryDirectory(prefix="warm-v16-verify-") as raw:
            lookup = restore_warm_state(
                Path(destination), identity, Path(raw) / "state.xml.gz"
            )
            if not lookup.hit:
                raise ValueError(f"adopted entry {key} is invalid: {lookup.reason}")
    return adoption


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, default=DEFAULT_CAMPAIGN_ROOT)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    result = (
        verify(args.campaign_root, args.destination, args.record)
        if args.verify
        else adopt(args.campaign_root, args.destination, args.record)
    )
    print(
        f"warm-state adoption ok: {result['content_key']} | "
        f"entries={result['entry_count']} | destination={result['destination']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
