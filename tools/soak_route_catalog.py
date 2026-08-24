#!/usr/bin/env python3
"""Run the post-adoption seven-date catalog soak and one legacy rollback."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.benchmark_route_catalog import load_suite_gate_record, run_arm

from traffic_sim.demand import route_catalog
from traffic_sim.demand.build_lock import demand_build_lock
from traffic_sim.demand.catalog_qualification import REQUIRED_HARD_GATES
from traffic_sim.simulation.monthly_demand import (
    LIVE_DEMAND_RELEASE_PRODUCTS,
    restore_live_demand_release,
    snapshot_live_demand_release,
)


SOAK_FIXTURES = (
    {"name": "weekday-a", "date": "2027-09-08", "days": 1},
    {"name": "weekday-b", "date": "2027-09-09", "days": 1},
    {"name": "weekday-c", "date": "2027-09-10", "days": 1},
    {"name": "weekend-a", "date": "2027-09-11", "days": 1},
    {"name": "weekend-b", "date": "2027-09-18", "days": 1},
    {"name": "holiday", "date": "2027-05-13", "days": 1},
    {"name": "mixed", "date": "2027-09-12", "days": 2},
)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1, sort_keys=True))
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-root", type=Path, required=True)
    parser.add_argument("--suite-gates", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=1800.0)
    parser.add_argument("--n-total", type=int, default=6000,
                        help="Matched candidate request used by soak/rollback")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.timeout_s <= 0:
        parser.error("--timeout-s must be positive")
    if args.n_total < 1:
        parser.error("--n-total must be positive")
    try:
        suite = load_suite_gate_record(args.suite_gates)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if any(suite.get(gate) is not True for gate in REQUIRED_HARD_GATES):
        parser.error("--suite-gates must mark every hard gate true")
    plan = {
        "schema_version": 1,
        "kind": "route_catalog_post_adoption_soak",
        "execute": bool(args.execute),
        "candidate_n_total": args.n_total,
        "fixtures": list(SOAK_FIXTURES),
        "catalog_runs": [],
        "rollback": None,
    }
    if not args.execute:
        print(json.dumps(plan, indent=1, sort_keys=True))
        return 0
    adoption = route_catalog.adopted_catalog_config(root=args.catalog_root)
    if adoption is None:
        parser.error("the verified catalog must be adopted before the soak")
    if set(adoption["catalog_selected_n_total"].values()) != {args.n_total}:
        parser.error("--n-total must equal both adopted catalog sizes")

    extra_products = tuple(LIVE_DEMAND_RELEASE_PRODUCTS) + tuple(
        Path("sumo") / f"catalog_{pool}.{suffix}"
        for pool in ("weekday", "weekend")
        for suffix in ("rou.xml", "meta.json", "validation.json", "template.json")
    ) + (Path("sumo") / "assignment_priors.json",)
    with demand_build_lock():
        snapshot = snapshot_live_demand_release(products=extra_products)
        try:
            for index, fixture in enumerate(SOAK_FIXTURES):
                with tempfile.TemporaryDirectory(
                        prefix=f"catalog-soak-{index}-") as raw:
                    plan["catalog_runs"].append({
                        "fixture": fixture,
                        "result": run_arm(
                            arm="catalog", fixture=fixture,
                            scratch=Path(raw), catalog_root=args.catalog_root,
                            suite=suite, timeout_s=args.timeout_s,
                            n_total=args.n_total),
                    })
            with tempfile.TemporaryDirectory(
                    prefix="catalog-soak-rollback-") as raw:
                plan["rollback"] = run_arm(
                    arm="legacy", fixture=SOAK_FIXTURES[0], scratch=Path(raw),
                    catalog_root=args.catalog_root, suite=suite,
                    timeout_s=args.timeout_s, n_total=args.n_total)
        finally:
            restore_live_demand_release(snapshot)
    all_results = [row["result"] for row in plan["catalog_runs"]]
    all_results.append(plan["rollback"])
    plan["status"] = "pass" if all(
        all(result["hard_gates"].values()) for result in all_results
    ) else "fail"
    _atomic_json(args.out, plan)
    print(json.dumps(plan, indent=1, sort_keys=True))
    return 0 if plan["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
