"""Deterministic PFE benchmark fixture — IMPROVEMENT_PLAN.md H2 (audit P1-3).

Builds a fully seeded, realistic-shaped calibration problem (no network
files, no I/O), runs the exact deployed solve path
(pfe.solve_interval_with_structure_guard per quarter +
pfe.write_calibration_report with structure groups and integer bounds),
and emits a metrics fingerprint:

  vehicles, geh_pct, infeasible, rung distribution, per-group calibrated
  share, purpose mix, capped-group residual

`python3 tools/pfe_benchmark.py` prints the metrics;
`--write-baseline` stores them in tests/data/pfe_benchmark_baseline.json.
tests/test_pfe_benchmark.py re-runs the fixture and asserts the metrics
stay within tolerance of the stored baseline — so the NEXT "performance
optimization" or refactor of the solver cannot silently change results
(the exact regression class the 2026-07-13 review caught by hand in the
departure-platoon bug).

The problem is sized to solve in a few seconds (pytest-friendly) while
exercising every mechanism: hard count targets, wide bounds, priors,
structure-group caps that BIND (planted demand overloads the capped
group), integer repair, purpose×time allocation.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from traffic_sim.demand import pfe
from traffic_sim.demand.pfe import Candidate

BASELINE_PATH = (Path(__file__).resolve().parent.parent
                 / "tests" / "data" / "pfe_benchmark_baseline.json")

N_QUARTERS = 12          # one 3-hour window — enough to exercise time mixing
N_SHAPES = 120
MEASURED_EDGES = ("m1", "m2", "m3")
PURPOSES = ("arbete", "service", "fritid")


def build_problem(seed: int = 7):
    """A seeded synthetic problem shaped like the real one.

    Shapes are short edge sequences that each cross exactly one measured
    edge (like sensor-anchored candidates); a planted "true" selection
    generates integer targets per quarter, so a perfect solver CAN reach
    GEH 100%. A subset of shapes ends immediately after its measured edge
    (the near-sensor class) and the planted demand overloads it, so the
    structure-group cap genuinely binds.
    """
    rng = np.random.default_rng(seed)
    shapes: list[Candidate] = []
    near_group: list[int] = []
    for i in range(N_SHAPES):
        m = MEASURED_EDGES[i % len(MEASURED_EDGES)]
        near = (i % 5 == 0)                # 20% near-sensor-ending shapes
        lead = [f"a{i}", f"b{i}"]
        tail = [] if near else [f"c{i}", f"d{i}", f"e{i}"]
        edges = lead + [m] + tail
        purpose = PURPOSES[i % len(PURPOSES)]
        depart = float(rng.integers(0, N_QUARTERS) * 900 + rng.integers(0, 900))
        src = Candidate(depart=depart, edges=list(edges),
                        source_id=f"cand-{i}",
                        intent={"purpose": purpose,
                                "origin_edge": edges[0],
                                "destination_edge": edges[-1]})
        shape = Candidate(depart=depart, edges=list(edges),
                          source_candidates=[src])
        shapes.append(shape)
        if near:
            near_group.append(i)

    # Planted demand: draw a "true" vehicle count per shape per quarter,
    # biased ONTO the near group (so the cap must actually intervene).
    targets_per_q: list[dict[str, float]] = []
    for q in range(N_QUARTERS):
        totals = Counter()
        for i, shape in enumerate(shapes):
            lam = 2.0 if i in set(near_group) else 0.8
            k = int(rng.poisson(lam))
            if k:
                m = shape.edges[2]      # the measured edge by construction
                totals[m] += k
        targets_per_q.append({m: float(totals.get(m, 0.0))
                              for m in MEASURED_EDGES})

    bounds_per_q = [{} for _ in range(N_QUARTERS)]
    priors_per_q = [{} for _ in range(N_QUARTERS)]
    # Cap the near group at 25% of each quarter's total — below the
    # planted ~40% share, so the two-pass guard has real work.
    structure_groups = [("near_sensor_dest", near_group, 0.25)]
    return shapes, targets_per_q, bounds_per_q, priors_per_q, structure_groups


def run_benchmark(seed: int = 7) -> dict:
    (shapes, targets_per_q, bounds_per_q, priors_per_q,
     structure_groups) = build_problem(seed)
    route_cost = pfe.path_size_weights(shapes)

    solutions, rungs = [], []
    for q in range(N_QUARTERS):
        sol, rung = pfe.solve_interval_with_structure_guard(
            shapes, targets_per_q[q], bounds_per_q[q], priors_per_q[q],
            route_cost=route_cost, structure_groups=structure_groups)
        solutions.append(sol)
        rungs.append(rung)

    edge_len = {e: 500.0 for s in shapes for e in s.edges}
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "bench.rou.xml"
        report = pfe.write_calibration_report(
            shapes, out, targets_per_q, solutions,
            bounds_per_q, rungs, enforce_integer_bounds=True,
            structure_groups=[(members, cap) for _n, members, cap
                              in structure_groups],
            edge_length_m=edge_len)
        agents = json.loads(
            (Path(td) / "bench.agents.json").read_text())["agents"]

    near_set = set(structure_groups[0][1])
    purposes = Counter()
    n_near = 0
    for a in agents:
        purposes[a["purpose"]] += 1
        cand = a.get("candidate_id") or ""
        if cand.startswith("cand-") and int(cand[5:]) in near_set:
            n_near += 1
    total_veh = report["vehicles"]
    metrics = {
        "vehicles": total_veh,
        "geh_pct": report["geh_pct"],
        "infeasible": report["infeasible_intervals"],
        # str keys so the in-memory dict round-trips JSON identically
        "rungs": {str(k): v for k, v in sorted(Counter(rungs).items())},
        # THE point of the fixture: planted demand puts ~40% on the capped
        # group; the deployed guard must hold the calibrated share near the
        # 25% cap (2-vehicle integer floor gives small quarters slack).
        "near_group_share": round(n_near / max(1, total_veh), 4),
        "purpose_mix": {p: round(purposes[p] / max(1, total_veh), 4)
                        for p in sorted(purposes)},
        "bound_violations": len(report.get("bound_violations", [])),
        "quarters_with_incompatible_purposes":
            report.get("purpose_allocation_summary", {})
                  .get("quarters_with_incompatible_routes", 0),
    }
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write-baseline", action="store_true",
                    help="store the metrics as the new checked-in baseline "
                         "(do this ONLY for an intentional, reviewed "
                         "behaviour change)")
    args = ap.parse_args()
    metrics = run_benchmark()
    print(json.dumps(metrics, indent=1))
    if args.write_baseline:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(BASELINE_PATH, "w") as f:
            json.dump(metrics, f, indent=1)
        print(f"baseline written → {BASELINE_PATH}")


if __name__ == "__main__":
    main()
