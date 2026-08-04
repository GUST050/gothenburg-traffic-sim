#!/usr/bin/env python3
"""Byte-exact compression/restore pilot over the three certified v16 entries.

This is storage evidence only.  It runs no SUMO or TraCI, creates no product
cache, and does not claim that the historical five-day artifacts are annual
plan units.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.fingerprint import sha256_file  # noqa: E402
from traffic_sim.simulation.annual_warm_plan import (  # noqa: E402
    load_annual_warm_plan,
)
from traffic_sim.simulation.annual_warm_store import (  # noqa: E402
    AnnualWarmArtifactStore,
)
from traffic_sim.simulation.warm_state_cache import (  # noqa: E402
    WarmStateIdentity,
    restore_warm_state,
)


DEFAULT_OUTPUT = Path("validation/annual_warm_storage_pilot_v1.json")
WARM_ROOT = Path("runs/warm-state")
DEMAND_ARCHIVE = Path("runs/demand-20260721-222017-41bc682a-bbe1")
VARIANT_FILENAMES = {
    "q10": "calibrated_v1.rou.xml",
    "q50": "calibrated.rou.xml",
    "q90": "calibrated_v2.rou.xml",
}


def _canonical_key(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_pilot_report(
    *,
    plan_path: Path = ROOT / "validation/annual_warm_plan_2027.json",
    warm_root: Path = ROOT / WARM_ROOT,
    demand_archive: Path = ROOT / DEMAND_ARCHIVE,
) -> dict[str, Any]:
    plan = load_annual_warm_plan(plan_path)
    entries = sorted(Path(warm_root).glob("*/manifest.json"))
    if len(entries) != 3:
        raise ValueError("storage pilot requires exactly three certified v16 entries")
    required_demand = (
        "demand_meta.json", "demand_build_spec.json", "manifest.json"
    )
    if any(not (demand_archive / name).is_file() for name in required_demand):
        raise ValueError("storage pilot demand archive is incomplete")

    with tempfile.TemporaryDirectory(prefix="annual-warm-storage-pilot-") as raw:
        temporary = Path(raw)
        store = AnnualWarmArtifactStore(temporary / "store")
        artifacts = []
        unique_content: dict[str, int] = {}
        for manifest_path in entries:
            source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            identity = WarmStateIdentity.from_dict(source_manifest["identity"])
            restored_state = temporary / "source" / identity.content_key / "state.xml.gz"
            lookup = restore_warm_state(warm_root, identity, restored_state)
            if not lookup.hit:
                raise ValueError(
                    f"certified source entry failed validation: {identity.content_key}"
                )
            variant = identity.demand_variant
            unit_id = f"storage-pilot-{identity.content_key}"
            artifact = store.pack_artifact(
                plan_content_key=plan["content_key"],
                unit_id=unit_id,
                members={
                    "state.xml.gz": restored_state,
                    "prefix_evidence.json": manifest_path.parent / "prefix_evidence.json",
                    "route.rou.xml": demand_archive / VARIANT_FILENAMES[variant],
                    "demand_meta.json": demand_archive / "demand_meta.json",
                    "demand_build_spec.json": demand_archive / "demand_build_spec.json",
                    "demand_manifest.json": demand_archive / "manifest.json",
                },
                metadata={
                    "purpose": "storage_pilot_only",
                    "source_warm_key": identity.content_key,
                    "variant": variant,
                    "seed": identity.seed,
                    "warmup_end_s": identity.warmup_end_s,
                    "source_manifest_sha256": sha256_file(manifest_path),
                },
            )
            restored = store.restore_artifact(unit_id, temporary / "restore" / unit_id)
            for name, record in artifact["members"].items():
                if sha256_file(restored[name]) != record["content_sha256"]:
                    raise ValueError(f"pilot restore differs for {unit_id}/{name}")
                unique_content[record["content_sha256"]] = record["content_bytes"]
            artifacts.append({
                "unit_id": unit_id,
                "artifact_content_key": artifact["content_key"],
                "source_warm_key": identity.content_key,
                "variant": variant,
                "seed": identity.seed,
                "logical_content_bytes": sum(
                    item["content_bytes"] for item in artifact["members"].values()
                ),
            })
        blob_files = [path for path in (store.root / "blobs").rglob("*")
                      if path.is_file()]
        physical_bytes = sum(path.stat().st_size for path in blob_files)
        unique_bytes = sum(unique_content.values())
        report: dict[str, Any] = {
            "schema": "annual_warm_storage_pilot_v1",
            "status": "pass",
            "claim": "storage_compression_and_restore_only",
            "plan_content_key": plan["content_key"],
            "source_warm_root": WARM_ROOT.as_posix(),
            "source_demand_archive": DEMAND_ARCHIVE.as_posix(),
            "artifact_count": len(artifacts),
            "artifacts": sorted(artifacts, key=lambda item: item["unit_id"]),
            "unique_original_bytes": unique_bytes,
            "physical_stored_bytes": physical_bytes,
            "physical_ratio": physical_bytes / unique_bytes,
            "blob_count": len(blob_files),
            "restore_digest_mismatches": 0,
            "sumo_executed": False,
            "traci_imported_or_connected": False,
            "persistent_store_created": False,
        }
        report["content_key"] = _canonical_key(report)
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    report = build_pilot_report()
    if args.write:
        _atomic_json(output, report)
    else:
        stored = json.loads(output.read_text(encoding="utf-8"))
        if stored != report or stored.get("content_key") != _canonical_key(stored):
            raise SystemExit("annual warm storage pilot report differs")
    print(json.dumps({
        "content_key": report["content_key"],
        "artifact_count": report["artifact_count"],
        "unique_original_bytes": report["unique_original_bytes"],
        "physical_stored_bytes": report["physical_stored_bytes"],
        "physical_ratio": report["physical_ratio"],
        "status": report["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
