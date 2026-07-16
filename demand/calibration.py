"""Flat-parallel PFE calibration orchestration — split from
build_sumo_demand.py 2026-07-14 (IMPROVEMENT_PLAN.md H1).

Owns the fork-pool worker (_run_pfe_interval_job), the shared pool
globals the workers inherit (_PFE_PAR_*), the flat (variant × quarter)
solve (run_pfe_variants_flat_parallel), and the per-variant warning
printers. The globals live HERE — validate_sim keeps its own mirror for
LOSO folds, both delegating to pfe.solve_interval_with_structure_guard
(one shared guard policy).
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time
from pathlib import Path

import pfe
from demand.structure import (GEO_PATH, load_edge_geometry,
                              structure_groups_for_shapes)

# Inherited by fork into every pool worker; set before the pool starts,
# reset in the finally. Module-level init restores the None-check contract
# in _run_pfe_interval_job (the declarations were lost in an earlier
# in-file reorganisation — worked only because assignment always preceded
# the first call).
_PFE_PAR_SHAPES = None
_PFE_PAR_ROUTE_COST = None
_PFE_PAR_STRUCTURE_GROUPS = None
_PFE_PAR_VARIANT_INPUTS = None

def _run_pfe_interval_job(job: dict):
    """ProcessPool worker for one independent (variant, quarter) PFE solve.

    The shared shape pool, route-cost vector, and immutable variant inputs are
    inherited by fork, so large candidate/bounds payloads are not pickled once
    per quarter.

    Structure preservation is pfe.solve_interval_with_structure_guard's
    two-pass policy — shared with validate_sim's LOSO workers by design.
    """
    import pfe

    if (_PFE_PAR_SHAPES is None or _PFE_PAR_ROUTE_COST is None or
            _PFE_PAR_VARIANT_INPUTS is None):
        raise RuntimeError("PFE interval worker was not initialized")
    suffix, key, quarter = job
    data = _PFE_PAR_VARIANT_INPUTS[suffix]
    sol, rung = pfe.solve_interval_with_structure_guard(
        _PFE_PAR_SHAPES,
        data["targets"][quarter],
        data["bounds_pq"][quarter],
        data["priors_pq"][quarter],
        route_cost=_PFE_PAR_ROUTE_COST,
        structure_groups=_PFE_PAR_STRUCTURE_GROUPS,
    )
    return suffix, key, quarter, sol, rung


def run_pfe_variants_flat_parallel(cand_path: Path, variants: list[tuple[str, str]],
                                   variant_inputs: dict[str, dict],
                                   max_workers: int | None = None) -> dict[str, dict]:
    """Solve all final direction variants through one flat worker pool.

    This avoids nesting multiprocessing pools: the unit of parallel work is one
    15-minute interval, across all variants, and route files are written only
    after every solution has been collected in deterministic quarter order.
    """
    import pfe

    global _PFE_PAR_SHAPES, _PFE_PAR_ROUTE_COST, _PFE_PAR_STRUCTURE_GROUPS
    global _PFE_PAR_VARIANT_INPUTS
    phase_started = time.perf_counter()
    shapes, route_cost = pfe.prepare_calibration(cand_path)
    prepare_s = time.perf_counter() - phase_started
    print(f"  timing PFE prepare: {prepare_s:.1f}s")
    _PFE_PAR_SHAPES = shapes
    _PFE_PAR_ROUTE_COST = route_cost
    # Set BEFORE the pool forks so workers inherit it, like the shape pool.
    _PFE_PAR_STRUCTURE_GROUPS = structure_groups_for_shapes(shapes)
    # Set before fork.  Each task now carries only a small tuple instead of
    # repeatedly serializing the same per-quarter bounds/priors dictionaries.
    _PFE_PAR_VARIANT_INPUTS = variant_inputs
    try:
        tasks = []
        solutions = {}
        rungs = {}
        for suffix, key in variants:
            data = variant_inputs[suffix]
            nq = len(data["targets"])
            solutions[suffix] = [None] * nq
            rungs[suffix] = [pfe.RUNG_INFEASIBLE] * nq
            for i in range(nq):
                tasks.append((suffix, key, i))

        n_workers = min(max_workers or (os.cpu_count() or 1), len(tasks))
        print(f"  PFE final variants: solving {len(tasks)} independent "
              f"variant×quarter intervals in one pool ({n_workers} workers)")
        solve_started = time.perf_counter()
        with mp.get_context("fork").Pool(processes=n_workers) as pool:
            for suffix, _key, quarter, sol, rung in pool.imap_unordered(
                _run_pfe_interval_job, tasks
            ):
                solutions[suffix][quarter] = sol
                rungs[suffix][quarter] = rung
        solve_s = time.perf_counter() - solve_started
        print(f"  timing PFE interval solving: {solve_s:.1f}s")

        reports = {}
        for suffix, key in variants:
            data = variant_inputs[suffix]
            report_started = time.perf_counter()
            reports[suffix] = pfe.write_calibration_report(
                shapes, data["out_path"], data["targets"], solutions[suffix],
                data["hard_bounds_pq"], rungs[suffix], enforce_integer_bounds=True,
                structure_groups=[(members, cap_share) for _n, members, cap_share
                                  in (_PFE_PAR_STRUCTURE_GROUPS or [])],
                edge_length_m=load_edge_geometry()[2] if GEO_PATH.exists() else None)
            publish_s = time.perf_counter() - report_started
            reports[suffix]["timings_s"] = {
                "prepare_shared": round(prepare_s, 3),
                "interval_solving_shared": round(solve_s, 3),
                "route_publish": round(publish_s, 3),
            }
            print(f"  timing PFE route publish {key}: {publish_s:.1f}s")
            if not data.get("keep_achieved", False):
                reports[suffix] = {
                    k: v for k, v in reports[suffix].items() if k != "achieved"
                }
        return reports
    finally:
        _PFE_PAR_SHAPES = None
        _PFE_PAR_ROUTE_COST = None
        _PFE_PAR_STRUCTURE_GROUPS = None
        _PFE_PAR_VARIANT_INPUTS = None


def warn_unserviceable_measured_edges(report: dict, label: str) -> None:
    """Make missing candidate coverage visible without blocking a demand run.

    This intentionally follows the existing non-blocking GEH gate: a route
    pool defect must be unmistakable to the operator, while still preserving
    the usable output for the remaining measured edges.
    """
    edges = report.get("unserviceable_edges", [])
    if edges:
        print(f"  ⚠ UNSERVICEABLE MEASURED EDGES ({label}): "
              f"{', '.join(edges)} — no candidate route can serve these "
              "hard measurements; regenerate/fix the candidate pool.")


def warn_purpose_allocation_drift(report: dict, label: str) -> None:
    """Expose selected routes lacking same-purpose candidate provenance."""
    summary = report.get("purpose_allocation_summary", {})
    n = summary.get("quarters_with_incompatible_routes", 0)
    if n:
        print(f"  ⚠ PURPOSE-ROUTE COMPATIBILITY DRIFT ({label}): {n} quarter(s), "
              f"routes {summary.get('incompatible_routes_by_purpose', {})} — "
              "the purpose-time mix is exact, but those selected route shapes "
              "lack same-purpose candidate provenance")


def warn_bound_violations(report: dict, label: str) -> None:
    """Surface an unexpected violation of a Level-2 bound retained by PFE.

    Deployment repairs and gates these before publishing. The warning still
    matters for diagnostic-only callers such as LOSO validation, which retain
    their historical non-mutating behaviour.
    """
    violations = report.get("bound_violations", [])
    if violations:
        sample = ", ".join(
            f"{v['edge']}@q{v['quarter']} ({v['achieved']:.0f} vs "
            f"[{v['bound_lo']:.0f},{v['bound_hi']:.0f}])"
            for v in violations[:5])
        more = f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""
        print(f"  ⚠ BOUND VIOLATIONS FROM INTEGER ROUNDING ({label}): "
              f"{len(violations)} edge-quarters exceed their level-2 bound "
              f"after rounding — {sample}{more}.")


def warn_relaxed_bound_violations(report: dict, label: str) -> None:
    """Expose intentional counts-first structural relaxations honestly."""
    violations = report.get("relaxed_bound_violations", [])
    if violations:
        quarters = sorted({int(v["quarter"]) for v in violations})
        print(f"  ⚠ STRUCTURAL BOUNDS RELAXED ({label}): "
              f"{len(violations)} edge-quarter value(s) in quarters {quarters} — "
              "sensor constraints were retained; inspect confidence before use")
