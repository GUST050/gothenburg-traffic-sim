#!/usr/bin/env python3
"""Build verified weekday/weekend route catalogs without publishing demand."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_candidates
import build_sumo_demand as demand
from demand.priors import ensure_assignment_priors
from traffic_sim.core.fingerprint import sha256_file, sumo_version
from traffic_sim.demand import route_catalog
from traffic_sim.simulation.runtime import sumo_home


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-key", choices=["weekday", "weekend", "both"],
                        default="both")
    parser.add_argument("--catalog-root", type=Path,
                        default=route_catalog.DEFAULT_ROOT)
    parser.add_argument("--report", type=Path, default=None,
                        help="Qualification/build report path (required with --execute)")
    parser.add_argument("--n-total", type=int,
                        default=route_catalog.DEFAULT_INITIAL_N_TOTAL)
    parser.add_argument("--sizing-attempts", type=int, default=3)
    parser.add_argument("--sizing-growth", type=float, default=1.5)
    parser.add_argument("--min-per-sensor", type=int, default=50)
    parser.add_argument("--through-fraction", type=float, default=0.5)
    parser.add_argument("--gravity-km", type=float, default=1.8)
    parser.add_argument("--gravity-alpha", type=float, default=1.5)
    parser.add_argument("--cross-fraction", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout-s", type=float, default=1200.0)
    parser.add_argument("--execute", action="store_true",
                        help="Actually build missing catalogs; otherwise print the plan")
    args = parser.parse_args()
    if args.n_total < 1 or args.sizing_attempts < 1:
        parser.error("--n-total and --sizing-attempts must be positive")
    if args.min_per_sensor < 1 or args.timeout_s <= 0:
        parser.error("--min-per-sensor and --timeout-s must be positive")
    route_catalog.catalog_size_attempts(
        args.n_total, attempts=args.sizing_attempts,
        growth=args.sizing_growth)
    if args.execute and args.report is None:
        parser.error("--execute requires --report")
    return args


def _atomic_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1, sort_keys=True))
    os.replace(temporary, path)


def main() -> int:
    args = parse_args()
    pools = ("weekday", "weekend") if args.pool_key == "both" else (args.pool_key,)
    sizes = route_catalog.catalog_size_attempts(
        args.n_total, attempts=args.sizing_attempts,
        growth=args.sizing_growth)
    plan = {
        "schema_version": 1,
        "kind": "route_catalog_build",
        "execute": bool(args.execute),
        "pools": list(pools),
        "catalog_root": str(args.catalog_root),
        "size_attempts": list(sizes),
        "min_per_sensor": args.min_per_sensor,
    }
    if not args.execute:
        print(json.dumps(plan, indent=1, sort_keys=True))
        return 0

    ensure_assignment_priors(
        gravity_km=args.gravity_km,
        through_fraction=args.through_fraction,
        cross_fraction=args.cross_fraction,
        gravity_alpha=args.gravity_alpha,
        seed=args.seed)
    home = sumo_home()
    started = time.perf_counter()
    results = {}
    with tempfile.TemporaryDirectory(prefix="route-catalog-materialize-") as tmp:
        materialized = Path(tmp)
        for pool in pools:
            base_config, inputs, sources = demand.candidate_identity_components(
                n_total=args.n_total,
                through_fraction=args.through_fraction,
                gravity_km=args.gravity_km,
                gravity_alpha=args.gravity_alpha,
                cross_fraction=args.cross_fraction,
                is_weekend=pool == "weekend",
                start_date="catalog",
                seed=args.seed,
                home=home,
                flows_path=demand.FLOWS_PATH)
            base_config["catalog_mode"] = True
            catalog_sources = {**sources, **{
                f"source:{name}": path
                for name, path in demand.SOURCE_FILES.items()}}
            destinations = {
                label: materialized / pool / label
                for label in route_catalog.OUTPUTS
            }

            def command_for(n_total: int, output_dir: Path,
                            *, pool_key=pool) -> list[str]:
                return route_catalog.candidate_catalog_command(
                    output_dir=output_dir, pool_key=pool_key,
                    n_total=n_total,
                    through_fraction=args.through_fraction,
                    gravity_km=args.gravity_km,
                    gravity_alpha=args.gravity_alpha,
                    cross_fraction=args.cross_fraction,
                    assignment_priors=(demand.SUMO_DIR / "assignment_priors.json"),
                    seed=args.seed, min_per_sensor=args.min_per_sensor)

            results[pool] = route_catalog.ensure_sized_catalog(
                root=args.catalog_root, pool_key=pool,
                base_config=base_config, inputs=inputs,
                source_files=catalog_sources, destinations=destinations,
                command_for=command_for, start_n_total=args.n_total,
                min_per_sensor=args.min_per_sensor,
                attempts=args.sizing_attempts, growth=args.sizing_growth,
                timeout_s=args.timeout_s)
            results[pool]["artifact_digests"] = {
                label: sha256_file(path) for label, path in destinations.items()
            }

    plan.update({
        "status": "succeeded",
        "elapsed_s": round(time.perf_counter() - started, 3),
        "results": results,
        "environment": {
            "sumo_home": str(home),
            "sumo_version": sumo_version(home),
            "network_sha256": sha256_file(demand.NET_PATH),
            "python_runtime": demand.runtime_package_identity((
                "networkx", "numpy", "osmnx", "shapely")),
            "route_diversity": build_candidates.DEFAULT_ROUTE_DIVERSITY,
        },
    })
    _atomic_json(args.report, plan)
    print(json.dumps(plan, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
