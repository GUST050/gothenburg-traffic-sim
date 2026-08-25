#!/usr/bin/env python3
"""Evaluate a frozen paired route-catalog benchmark and write its verdict."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.demand.catalog_qualification import qualify_catalog_trials
from tools.benchmark_route_catalog import load_suite_gate_record


def _build_contract(build: object) -> tuple[dict[str, str], dict[str, int]]:
    results = build.get("results") if isinstance(build, dict) else None
    if not isinstance(results, dict) or set(results) != {"weekday", "weekend"}:
        raise ValueError("catalog build must contain weekday and weekend results")
    keys: dict[str, str] = {}
    sizes: dict[str, int] = {}
    for pool, record in results.items():
        key = record.get("key") if isinstance(record, dict) else None
        size = record.get("n_total") if isinstance(record, dict) else None
        if (not isinstance(key, str) or len(key) != 32
                or any(char not in "0123456789abcdef" for char in key)
                or isinstance(size, bool) or not isinstance(size, int)
                or size < 1):
            raise ValueError(f"catalog build has invalid {pool} key/size")
        keys[pool] = key
        sizes[pool] = size
    return keys, sizes


def _validate_trial_binding(trials: list, keys: dict[str, str],
                            sizes: dict[str, int]) -> int:
    if not trials:
        raise ValueError("qualification requires at least one paired trial")
    requested_sizes = set()
    for index, trial in enumerate(trials, 1):
        if not isinstance(trial, dict):
            raise ValueError(f"trial {index} is not an object")
        arm_sizes = set()
        for arm in ("legacy", "catalog"):
            record = trial.get(arm)
            size = (record.get("candidate_n_total")
                    if isinstance(record, dict) else None)
            if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                raise ValueError(f"trial {index} {arm} lacks candidate_n_total")
            arm_sizes.add(size)
        if len(arm_sizes) != 1:
            raise ValueError(f"trial {index} compares different candidate sizes")
        requested = arm_sizes.pop()
        requested_sizes.add(requested)
        catalog = trial["catalog"]
        trial_keys = catalog.get("catalog_keys")
        trial_sizes = catalog.get("catalog_selected_n_total")
        if (not isinstance(trial_keys, dict) or not trial_keys
                or not isinstance(trial_sizes, dict)
                or set(trial_keys) != set(trial_sizes)
                or any(pool not in keys or trial_keys[pool] != keys[pool]
                       or trial_sizes[pool] != sizes[pool]
                       for pool in trial_keys)):
            raise ValueError(
                f"trial {index} is not bound to the supplied catalog build")
        if any(value != requested for value in trial_sizes.values()):
            raise ValueError(
                f"trial {index} catalog sizing differs from its legacy arm")
    if len(requested_sizes) != 1:
        raise ValueError("qualification trials use different candidate sizes")
    return requested_sizes.pop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=Path, required=True,
                        help="JSON array, or object containing a trials array")
    parser.add_argument("--catalog-build", type=Path, required=True,
                        help="Report written by build_route_catalog.py")
    parser.add_argument("--suite-gates", type=Path, required=True,
                        help="Focused once-per-campaign suite evidence")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        trial_payload = json.loads(args.trials.read_text())
        build = json.loads(args.catalog_build.read_text())
        suite_gates = load_suite_gate_record(args.suite_gates)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        parser.error(f"cannot read qualification input: {exc}")
    trials = (trial_payload.get("trials")
              if isinstance(trial_payload, dict) else trial_payload)
    if not isinstance(trials, list):
        parser.error("--trials must contain a JSON array")
    build_s = build.get("elapsed_s") if isinstance(build, dict) else None
    if not isinstance(build_s, (int, float)) or build_s < 0:
        parser.error("--catalog-build has no valid elapsed_s")
    try:
        catalog_keys, catalog_sizes = _build_contract(build)
        candidate_n_total = _validate_trial_binding(
            trials, catalog_keys, catalog_sizes)
    except ValueError as exc:
        parser.error(str(exc))
    report = qualify_catalog_trials(
        trials, catalog_build_s=float(build_s), suite_gates=suite_gates)
    report["evidence_binding"] = {
        "trials_path": str(args.trials),
        "trials_sha256": sha256_file(args.trials),
        "catalog_build_path": str(args.catalog_build),
        "catalog_build_sha256": sha256_file(args.catalog_build),
        "suite_gates_path": str(args.suite_gates),
        "suite_gates_sha256": sha256_file(args.suite_gates),
        "catalog_keys": catalog_keys,
        "catalog_selected_n_total": catalog_sizes,
        "candidate_n_total": candidate_n_total,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(args.out.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=1, sort_keys=True))
    os.replace(temporary, args.out)
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report["verdict"] == "adopt" else 2


if __name__ == "__main__":
    raise SystemExit(main())
