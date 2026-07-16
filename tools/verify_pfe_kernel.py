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


def build_real_problem():
    import pfe
    from demand.intake import build_targets, load_sensor_edges
    from demand.structure import structure_groups_for_shapes
    from demand.priors import (ensure_bounds, ensure_observability,
                               ensure_assignment_priors, ensure_priors,
                               STRUCTURAL_REFERENCE_DATE)

    shapes, route_cost = pfe.prepare_calibration(
        ROOT / "sumo" / "candidates.rou.xml")
    meta = json.load(open(ROOT / "sumo" / "demand_meta.json"))
    qi_start, n_intervals = meta["qi_start"], meta["n_intervals"]
    flows = json.load(open(ROOT / "web" / "data" / "flows.json"))["flows"]
    targets = build_targets(flows, load_sensor_edges(), qi_start, n_intervals)
    groups = structure_groups_for_shapes(shapes)

    bounds_data = ensure_bounds(STRUCTURAL_REFERENCE_DATE, "00:00", "24:00")
    corridor = ensure_observability().get("corridor_priors", {})
    priors_data = ensure_priors(STRUCTURAL_REFERENCE_DATE)
    assign = ensure_assignment_priors()
    assign_w, assign_flows = assign.get("weight", 0.0), assign.get("flows", {})

    def bq_pq(i):
        hard_bq = {}
        for e, arr in bounds_data["bounds"].items():
            s = i % 96
            if s < len(arr) and arr[s]:
                hard_bq[e] = (arr[s][0], arr[s][1])
        bq = dict(hard_bq)
        pq = {}
        slot = (qi_start + i) % 96
        for e, d in priors_data.get("edges", {}).items():
            val = d["prior"][slot]
            if val is None:
                continue
            lo = d["prior_low"][slot] or 0.0
            hi = d["prior_high"][slot] or val
            pq[e] = (float(val), 1.0 / max(1.0, hi - lo))
        for e, d in corridor.items():
            qi = qi_start + i
            if qi >= len(d["prior"]) or d["prior"][qi] is None:
                continue
            band = d["band"][qi] or 8.0
            pq[e] = (float(d["prior"][qi]), 1.0 / max(1.0, band))
        if assign_w > 0:
            for e, series in assign_flows.items():
                if e in pq or slot >= len(series) or series[slot] is None:
                    continue
                bq[e] = (0.0, max(5.0, 5.0 * series[slot]))
        return bq, pq

    return shapes, route_cost, targets, groups, bq_pq, n_intervals


def solve_all(pure: bool):
    """Solve every quarter in-process; PFE_PURE decides the path at import."""
    import importlib
    import os
    os.environ["PFE_PURE"] = "1" if pure else "0"
    import pfe
    importlib.reload(pfe)
    (shapes, route_cost, targets, groups, bq_pq,
     n_intervals) = build_real_problem()
    out = []
    t0 = time.perf_counter()
    for q in range(n_intervals):
        bq, pq = bq_pq(q)
        sol, rung = pfe.solve_interval_with_structure_guard(
            shapes, targets[q], bq, pq, route_cost=route_cost,
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
    subprocess.run([sys.executable, __file__, "--pure-worker"], check=True)
    ref = np.load(ROOT / "sumo" / "_verify_pure.npz")
    ref_meta = json.loads((ROOT / "sumo" / "_verify_pure.json").read_text())

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
    (ROOT / "sumo" / "_verify_pure.npz").unlink()
    (ROOT / "sumo" / "_verify_pure.json").unlink()


if __name__ == "__main__":
    main()
