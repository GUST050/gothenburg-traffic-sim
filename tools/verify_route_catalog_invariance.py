#!/usr/bin/env python3
"""Run the isolated two-date canonical-template/routing invariance gate."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.core.fingerprint import sha256_file, sumo_version
from traffic_sim.demand import route_catalog
from traffic_sim.demand.catalog_qualification import semantic_route_digest
from traffic_sim.simulation.runtime import sumo_home


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-a", required=True)
    parser.add_argument("--date-b", required=True)
    parser.add_argument("--pool-key", choices=["weekday", "weekend"],
                        default="weekday")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n-total", type=int,
                        default=route_catalog.DEFAULT_INITIAL_N_TOTAL)
    parser.add_argument("--min-per-sensor", type=int, default=50)
    parser.add_argument("--through-fraction", type=float, default=0.5)
    parser.add_argument("--gravity-km", type=float, default=1.8)
    parser.add_argument("--gravity-alpha", type=float, default=1.5)
    parser.add_argument("--cross-fraction", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout-s", type=float, default=1200.0)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.date_a == args.date_b:
        parser.error("the two probe dates must differ")
    if args.n_total < 1 or args.min_per_sensor < 1 or args.timeout_s <= 0:
        parser.error("pool size, support floor and timeout must be positive")
    return args


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1, sort_keys=True))
    os.replace(temporary, path)


def main() -> int:
    args = parse_args()
    report = {
        "schema_version": 1,
        "kind": "route_catalog_two_date_invariance",
        "dates": [args.date_a, args.date_b],
        "pool_key": args.pool_key,
        "n_total": args.n_total,
        "execute": bool(args.execute),
    }
    if not args.execute:
        _atomic_json(args.out, report)
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0

    started = time.perf_counter()
    records = []
    with tempfile.TemporaryDirectory(prefix="catalog-invariance-") as raw:
        root = Path(raw)
        for label, date in (("a", args.date_a), ("b", args.date_b)):
            output_dir = root / label
            command = route_catalog.candidate_catalog_command(
                output_dir=output_dir, pool_key=args.pool_key,
                n_total=args.n_total,
                through_fraction=args.through_fraction,
                gravity_km=args.gravity_km,
                gravity_alpha=args.gravity_alpha,
                cross_fraction=args.cross_fraction,
                assignment_priors=Path("sumo/assignment_priors.json"),
                seed=args.seed, min_per_sensor=args.min_per_sensor,
                probe_date=date)
            arm_started = time.perf_counter()
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=args.timeout_s)
            if result.returncode != 0:
                raise RuntimeError(
                    f"catalog invariance build {label} failed: "
                    + (result.stderr or result.stdout)[-4000:])
            outputs = {
                "catalog.rou.xml": output_dir / "candidates.rou.xml",
                "catalog.meta.json": output_dir / "candidates.meta.json",
                "catalog.validation.json": output_dir / "sensor_coverage_report.json",
                "catalog.template.json": output_dir / "canonical_template_report.json",
            }
            validation = route_catalog.validate_catalog_artifacts(
                outputs, min_per_sensor=args.min_per_sensor)
            template = json.loads(outputs["catalog.template.json"].read_text())
            records.append({
                "label": label,
                "date": date,
                "wall_s": round(time.perf_counter() - arm_started, 3),
                "template_semantic_sha256": template["semantic_sha256"],
                "route_semantic_sha256": semantic_route_digest(
                    outputs["catalog.rou.xml"], outputs["catalog.meta.json"]),
                "artifact_sha256": {
                    name: sha256_file(path) for name, path in outputs.items()
                },
                "validation": validation,
            })
    template_equal = (records[0]["template_semantic_sha256"]
                      == records[1]["template_semantic_sha256"])
    routes_equal = (records[0]["route_semantic_sha256"]
                    == records[1]["route_semantic_sha256"])
    report.update({
        "status": "pass" if template_equal and routes_equal else "fail",
        "template_invariant": template_equal,
        "neutral_routes_invariant": routes_equal,
        "records": records,
        "elapsed_s": round(time.perf_counter() - started, 3),
        "environment": {
            "sumo_home": str(sumo_home()),
            "sumo_version": sumo_version(sumo_home()),
            "network_sha256": sha256_file(Path("sumo/net.net.xml")),
        },
    })
    _atomic_json(args.out, report)
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
