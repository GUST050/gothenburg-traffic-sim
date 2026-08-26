#!/usr/bin/env python3
"""Activate a route catalog only from an immutable passing qualification."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.demand import route_catalog


def adoption_payload(qualification: object, build: object, *,
                     qualification_sha256: str | None,
                     catalog_build_sha256: str | None,
                     qualification_path: str,
                     catalog_build_path: str,
                     catalog_root: Path) -> dict:
    """Cross-bind passing evidence, build report and stored catalog bytes."""
    if (not isinstance(qualification, dict)
            or qualification.get("verdict") != "adopt"
            or not isinstance(qualification.get("gates"), dict)
            or not qualification["gates"]
            or not all(value is True for value in qualification["gates"].values())):
        raise ValueError("qualification verdict and every gate must be adopt/true")
    binding = qualification.get("evidence_binding")
    if (not isinstance(binding, dict)
            or binding.get("catalog_build_sha256") != catalog_build_sha256):
        raise ValueError("qualification is not bound to this catalog build")
    for path_key, digest_key in (
            ("trials_path", "trials_sha256"),
            ("suite_gates_path", "suite_gates_sha256")):
        linked_path = binding.get(path_key)
        linked_digest = binding.get(digest_key)
        if (not isinstance(linked_path, str) or not linked_path
                or Path(linked_path).is_absolute()
                or not isinstance(linked_digest, str)
                or len(linked_digest) != 64
                or any(char not in "0123456789abcdef"
                       for char in linked_digest)):
            raise ValueError(f"qualification has invalid {path_key} binding")
    results = build.get("results") if isinstance(build, dict) else None
    if not isinstance(results, dict) or set(results) != {"weekday", "weekend"}:
        raise ValueError("catalog build report must contain weekday and weekend")
    catalog_keys: dict[str, str] = {}
    catalog_sizes: dict[str, int] = {}
    for pool, record in sorted(results.items()):
        key = record.get("key") if isinstance(record, dict) else None
        size = record.get("n_total") if isinstance(record, dict) else None
        if (not isinstance(key, str) or len(key) != 32
                or any(char not in "0123456789abcdef" for char in key)
                or isinstance(size, bool) or not isinstance(size, int)
                or size < 1):
            raise ValueError("catalog build report contains invalid pool/key/size")
        catalog_keys[pool] = key
        catalog_sizes[pool] = size
        if not route_catalog.catalog_entry_matches(
                catalog_root, pool=pool, key=key, n_total=size):
            raise ValueError(f"catalog entry {pool}/{key} is missing or invalid")
    if (binding.get("catalog_keys") != catalog_keys
            or binding.get("catalog_selected_n_total") != catalog_sizes):
        raise ValueError("qualification catalog identity differs from build report")
    if (not isinstance(qualification_sha256, str)
            or len(qualification_sha256) != 64
            or not isinstance(catalog_build_sha256, str)
            or len(catalog_build_sha256) != 64):
        raise ValueError("evidence files must have valid SHA-256 digests")
    if (not qualification_path or Path(qualification_path).is_absolute()
            or not catalog_build_path
            or Path(catalog_build_path).is_absolute()):
        raise ValueError("evidence paths must be project-relative")
    return {
        "schema_version": 3,
        "status": "adopt",
        "qualification_sha256": qualification_sha256,
        "catalog_build_sha256": catalog_build_sha256,
        "evidence": {
            "qualification": {
                "path": qualification_path,
                "sha256": qualification_sha256,
            },
            "catalog_build": {
                "path": catalog_build_path,
                "sha256": catalog_build_sha256,
            },
        },
        "catalog_keys": catalog_keys,
        "catalog_selected_n_total": catalog_sizes,
        "rollback": "python3 build_sumo_demand.py --candidate-source legacy ...",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--catalog-build", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=route_catalog.ADOPTION_PATH)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        qualification = json.loads(args.qualification.read_text())
        build = json.loads(args.catalog_build.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read adoption input: {exc}")
    catalog_root = Path(build.get("catalog_root", route_catalog.DEFAULT_ROOT)) \
        if isinstance(build, dict) else route_catalog.DEFAULT_ROOT
    try:
        project_root = Path(__file__).resolve().parents[1]
        qualification_path = str(args.qualification.resolve().relative_to(
            project_root))
        catalog_build_path = str(args.catalog_build.resolve().relative_to(
            project_root))
        binding = qualification.get("evidence_binding")
        for path_key, digest_key in (
                ("trials_path", "trials_sha256"),
                ("suite_gates_path", "suite_gates_sha256")):
            relative = binding.get(path_key) if isinstance(binding, dict) else None
            expected = binding.get(digest_key) if isinstance(binding, dict) else None
            linked = (project_root / relative).resolve() if isinstance(
                relative, str) else None
            if (linked is None or Path(relative).is_absolute()
                    or not linked.is_relative_to(project_root)
                    or sha256_file(linked) != expected):
                raise ValueError(
                    f"qualification linked evidence does not match {path_key}")
        payload = adoption_payload(
            qualification, build,
            qualification_sha256=sha256_file(args.qualification),
            catalog_build_sha256=sha256_file(args.catalog_build),
            qualification_path=qualification_path,
            catalog_build_path=catalog_build_path,
            catalog_root=catalog_root)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if not args.execute:
        print(json.dumps(payload, indent=1, sort_keys=True))
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(args.out.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1, sort_keys=True))
    os.replace(temporary, args.out)
    print(f"adopted route catalog default via {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
