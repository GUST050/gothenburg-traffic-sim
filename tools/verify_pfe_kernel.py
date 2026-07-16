"""Bitwise verification of the compiled IPF kernel — pfe_kernel.py's proof.

Loads the REAL candidate pool, real targets, real bounds/priors and real
structure groups, solves every quarter through BOTH paths (compiled kernel
vs the pure-Python reference, selected via PFE_PURE), and requires the
outputs to be EXACTLY equal — same None-pattern, same bits in every float
(np.array_equal, no tolerance). Approximate equality is not accepted:
the kernel's contract is identical operations in identical order.

Run after any edit to the IPF loop or the kernel:
  python3 tools/verify_pfe_kernel.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def flow_path_for_metadata(meta: dict) -> Path:
    """Return the exact target-flow product used by the demand build."""
    source = meta.get("source")
    if source not in {"historical", "forecast"}:
        raise RuntimeError(f"demand_meta.json has invalid source {source!r}")
    name = "flows_forecast.json" if source == "forecast" else "flows.json"
    return ROOT / "web" / "data" / name


def build_real_problem():
    import pfe
    from demand.intake import build_targets, load_sensor_edges
    from demand.structure import structure_groups_for_shapes
    from demand.priors import (build_interval_constraints,
                               ensure_assignment_priors, ensure_observability,
                               structural_bounds_and_priors)

    shapes, route_cost = pfe.prepare_calibration(
        ROOT / "sumo" / "candidates.rou.xml")
    meta = json.load(open(ROOT / "sumo" / "demand_meta.json"))
    qi_start, n_intervals = meta["qi_start"], meta["n_intervals"]
    with open(flow_path_for_metadata(meta)) as f:
        flows = json.load(f)["flows"]
    targets = build_targets(flows, load_sensor_edges(), qi_start, n_intervals)
    groups = structure_groups_for_shapes(shapes)

    # The metadata is the demand builder's actual date/window contract.  A
    # multi-day build intentionally has no legacy begin/end fields, but its
    # structural inputs are whole-day by construction.
    begin = meta.get("begin", "00:00")
    end = meta.get("end", "24:00")
    bounds_data, priors_data = structural_bounds_and_priors(begin, end)
    corridor = ensure_observability().get("corridor_priors", {})
    assign = ensure_assignment_priors()
    bounds_per_q, priors_per_q, _hard_bounds_per_q = build_interval_constraints(
        n_intervals, qi_start, bounds_data, priors_data, corridor, assign)
    return shapes, route_cost, targets, groups, bounds_per_q, priors_per_q


def solve_all(pure: bool):
    """Solve every quarter in-process; PFE_PURE decides the path at import."""
    import importlib
    import os
    os.environ["PFE_PURE"] = "1" if pure else "0"
    import pfe
    importlib.reload(pfe)
    (shapes, route_cost, targets, groups,
     bounds_per_q, priors_per_q) = build_real_problem()
    out = []
    t0 = time.perf_counter()
    for q, (target, bq, pq) in enumerate(zip(targets, bounds_per_q, priors_per_q)):
        sol, rung = pfe.solve_interval_with_structure_guard(
            shapes, target, bq, pq, route_cost=route_cost,
            structure_groups=groups)
        out.append((sol, rung))
    return out, time.perf_counter() - t0


def main() -> None:
    # The pure reference runs in a SUBPROCESS so module state can't leak
    # between paths; results cross the boundary via .npy files.
    if len(sys.argv) > 1 and sys.argv[1] == "--pure-worker":
        sols, elapsed = solve_all(pure=True)
        payload = {"rungs": [r for _s, r in sols], "elapsed": elapsed}
        np.savez(ROOT / "sumo" / "_verify_pure.npz",
                 **{f"q{i}": (s if s is not None else np.array([]))
                    for i, (s, _r) in enumerate(sols)})
        (ROOT / "sumo" / "_verify_pure.json").write_text(json.dumps(payload))
        return

    print("Pure-Python reference (subprocess, PFE_PURE=1) …")
    pure_npz = ROOT / "sumo" / "_verify_pure.npz"
    pure_json = ROOT / "sumo" / "_verify_pure.json"
    try:
        subprocess.run([sys.executable, __file__, "--pure-worker"], check=True)
        ref = np.load(pure_npz)
        ref_meta = json.loads(pure_json.read_text())

        print("Compiled kernel path …")
        fast, fast_elapsed = solve_all(pure=False)

        n_equal = n_none = 0
        for q, (sol, rung) in enumerate(fast):
            ref_sol = ref[f"q{q}"]
            ref_rung = ref_meta["rungs"][q]
            assert rung == ref_rung, f"q{q}: rung {rung} != {ref_rung}"
            if sol is None:
                assert ref_sol.size == 0, f"q{q}: fast None, pure has a solution"
                n_none += 1
                continue
            assert ref_sol.size, f"q{q}: pure None, fast has a solution"
            assert np.array_equal(sol, ref_sol), (
                f"q{q}: NOT bitwise equal — max abs diff "
                f"{np.max(np.abs(sol - ref_sol)):.3e}")
            n_equal += 1
        print(f"BITWISE EQUAL on {n_equal} solved quarters "
              f"({n_none} infeasible in both), rungs identical.")
        print(f"wall time: pure {ref_meta['elapsed']:.1f}s → "
              f"kernel {fast_elapsed:.1f}s "
              f"({ref_meta['elapsed'] / max(fast_elapsed, 1e-9):.1f}x, "
              f"single-threaded, incl. one-time JIT compile)")
    finally:
        pure_npz.unlink(missing_ok=True)
        pure_json.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
