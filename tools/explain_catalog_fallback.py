#!/usr/bin/env python3
"""Say WHICH identity component made the adopted route catalog go stale.

``build_sumo_demand.py`` prints ``adopted_key_stale_for_current_inputs`` and
falls back to the legacy candidate builder.  That reason is true but mute: it
does not say what moved, so the operator is left re-hashing the whole identity
inventory by hand.  Measured 2026-09-06, exactly four of twenty-three
components had drifted while production had been silently serving legacy.

This is read-only.  It builds nothing, adopts nothing and writes nothing.

    python3 tools/explain_catalog_fallback.py

The check deliberately lives outside ``build_sumo_demand.py``: that file is
itself bound into ``route_catalog.CATALOG_SOURCE_LABELS``, so editing it to
print this would invalidate every adopted catalog it was meant to explain.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.demand import route_catalog  # noqa: E402


POOL_KEYS = ("weekday", "weekend")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-root", type=Path,
                        default=route_catalog.DEFAULT_ROOT)
    parser.add_argument("--adoption", type=Path,
                        default=route_catalog.ADOPTION_PATH)
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text.")
    return parser.parse_args(argv)


def _build_defaults():
    """Read the generator defaults from the build's own parser, not a copy.

    A second copy of these numbers here would silently disagree with the
    build the day one of them changes, and this tool would then explain a
    catalog nobody actually asks for.
    """
    import build_sumo_demand as demand

    argv = sys.argv
    try:
        sys.argv = ["build_sumo_demand.py"]
        return demand, demand.parse_args()
    finally:
        sys.argv = argv


def explain(catalog_root: Path, adoption_path: Path) -> dict:
    """Report, per pool, whether the adopted key still serves current inputs."""
    demand, args = _build_defaults()
    adoption = route_catalog.adopted_catalog_config(path=adoption_path,
                                                    root=catalog_root)
    if adoption is None:
        return {
            "status": "no_valid_adoption",
            "detail": "adopted_catalog_config refused the record; the build "
                      "falls back with adoption_invalid_during_build",
            "pools": {},
        }
    pools = {}
    for pool_key in POOL_KEYS:
        adopted_key = adoption["catalog_keys"][pool_key]
        n_total = adoption["catalog_selected_n_total"][pool_key]
        config, inputs, sources = demand.candidate_identity_components(
            n_total=n_total,
            through_fraction=args.through_fraction,
            gravity_km=args.gravity_km,
            gravity_alpha=args.gravity_alpha,
            cross_fraction=args.cross_fraction,
            is_weekend=pool_key == "weekend",
            start_date=args.start_date,
            seed=args.seed,
            min_per_sensor=args.candidate_min_per_sensor,
            home=demand.sumo_home(),
            flows_path=demand.FLOWS_PATH,
        )
        pool_config = dict(config, is_weekend=pool_key == "weekend",
                           catalog_mode=True, n_total=n_total,
                           min_per_sensor=args.candidate_min_per_sensor)
        pool_inputs = dict(inputs, source_flow_edge_set=demand.FLOWS_PATH)
        payload = route_catalog.catalog_identity_payload(
            pool_config, pool_inputs, sources, pool_key=pool_key)
        current_key = route_catalog.catalog_key(
            pool_config, pool_inputs, sources, pool_key=pool_key)
        pools[pool_key] = {
            "adopted_key": adopted_key,
            "current_key": current_key,
            "serves_current_inputs": adopted_key == current_key,
            "drift": route_catalog.identity_drift(
                catalog_root, adopted_key, payload),
        }
    stale = sorted(key for key, row in pools.items()
                   if not row["serves_current_inputs"])
    return {
        "status": "stale" if stale else "serves_current_inputs",
        "stale_pools": stale,
        "pools": pools,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = explain(args.catalog_root, args.adoption)
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0 if report["status"] == "serves_current_inputs" else 1
    if report["status"] == "no_valid_adoption":
        print(f"no usable adoption record: {report['detail']}")
        return 1
    for pool_key, row in sorted(report["pools"].items()):
        if row["serves_current_inputs"]:
            print(f"{pool_key}: adopted catalog {row['adopted_key']} still "
                  "serves the current inputs")
            continue
        print(f"{pool_key}: adopted {row['adopted_key']} != current "
              f"{row['current_key']} — the build falls back to legacy")
        for component in row["drift"]:
            print(f"    drifted: {component}")
    return 0 if report["status"] == "serves_current_inputs" else 1


if __name__ == "__main__":
    raise SystemExit(main())
