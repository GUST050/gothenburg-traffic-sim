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

from collections import Counter
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
_PFE_PAR_PURPOSE_MIXES = None


def apply_activity_purpose_margin(
    source_mixes: list[Counter],
    activity_shares_by_quarter: list[dict[str, float]] | None,
) -> list[Counter]:
    """Restore the behavioural activity mix after route-pool filtering.

    ``duarouter`` endpoint repair and the subsequent loop/detour filters are
    deliberately allowed to reject invalid routes. Those rejections are not
    purpose-neutral, though: an activity category with longer or less direct
    routes can lose more source candidates. Using the survivor histogram as
    the PFE purpose target would therefore silently turn a routing-validity
    filter into a behavioural-purpose model.

    Preserve every non-activity category (through/external/legacy) exactly as
    it survives in each quarter, because this project has no independent
    evidence to replace those margins. Reallocate only the existing
    activity-tour count across the supplied, date-aware ``P(purpose | hour)``
    shares. This changes neither route geometry nor the available number of
    activity candidates, and costs only a few integer allocations before the
    existing parallel interval solve.
    """
    try:
        return pfe.apply_category_margin(source_mixes, activity_shares_by_quarter)
    except ValueError as exc:
        # Keep the public error terminology behavioural at this seam, while
        # PFE's generic helper remains usable for non-purpose categories.
        raise ValueError(str(exc).replace("category shares", "activity purpose shares")) from exc


def _agent_path_for(route_path: Path) -> Path:
    """Return the provenance sidecar emitted beside one route XML file."""
    return route_path.with_name(route_path.name.replace(".rou.xml", ".agents.json"))


def _staged_route_path(route_path: Path) -> Path:
    """A sibling staging name that retains the route/agent name convention."""
    return route_path.with_name(route_path.name + ".staged")


def _report_is_publishable(report: dict) -> bool:
    """Demand variants are one contract: publish none unless all are valid."""
    return (
        report.get("infeasible_intervals", 0) == 0
        and float(report.get("geh_pct") or 0.0) >= 100.0
        and not report.get("bound_violations")
        and not report.get("unserviceable_edges")
    )

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
            _PFE_PAR_VARIANT_INPUTS is None or _PFE_PAR_PURPOSE_MIXES is None):
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
        purpose_mix=_PFE_PAR_PURPOSE_MIXES[suffix][quarter],
    )
    return suffix, key, quarter, sol, rung


def run_pfe_variants_flat_parallel(cand_path: Path, variants: list[tuple[str, str]],
                                   variant_inputs: dict[str, dict],
                                   max_workers: int | None = None,
                                   purpose_departure_offset_s: float = 0.0,
                                   activity_purpose_shares_by_quarter:
                                   list[dict[str, float]] | None = None) -> dict[str, dict]:
    """Solve all final direction variants through one flat worker pool.

    This avoids nesting multiprocessing pools: the unit of parallel work is one
    15-minute interval, across all variants, and route files are written only
    after every solution has been collected in deterministic quarter order.
    ``purpose_departure_offset_s`` aligns absolute candidate departure times
    with target-quarter zero for a sub-day demand window. When supplied,
    ``activity_purpose_shares_by_quarter`` restores the documented
    activity-purpose margin after route validity filtering while retaining
    the survivor mix for through/external categories.
    """
    import pfe

    global _PFE_PAR_SHAPES, _PFE_PAR_ROUTE_COST, _PFE_PAR_STRUCTURE_GROUPS
    global _PFE_PAR_VARIANT_INPUTS, _PFE_PAR_PURPOSE_MIXES
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
    _PFE_PAR_PURPOSE_MIXES = {}
    for suffix, _key in variants:
        source_mixes = pfe._purpose_targets_per_quarter(
            shapes, len(variant_inputs[suffix]["targets"]),
            purpose_departure_offset_s)
        _PFE_PAR_PURPOSE_MIXES[suffix] = apply_activity_purpose_margin(
            source_mixes, activity_purpose_shares_by_quarter)
    staged_outputs = {
        suffix: _staged_route_path(Path(variant_inputs[suffix]["out_path"]))
        for suffix, _key in variants
    }
    for staged in staged_outputs.values():
        staged.unlink(missing_ok=True)
        _agent_path_for(staged).unlink(missing_ok=True)
        staged.with_suffix(staged.suffix + ".tmp").unlink(missing_ok=True)
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
                shapes, staged_outputs[suffix], data["targets"], solutions[suffix],
                data["hard_bounds_pq"], rungs[suffix], enforce_integer_bounds=True,
                structure_groups=[(members, cap_share) for _n, members, cap_share
                                  in (_PFE_PAR_STRUCTURE_GROUPS or [])],
                edge_length_m=load_edge_geometry()[2] if GEO_PATH.exists() else None,
                purpose_mixes_per_q=_PFE_PAR_PURPOSE_MIXES[suffix])
            publish_s = time.perf_counter() - report_started
            reports[suffix]["timings_s"] = {
                "prepare_shared": round(prepare_s, 3),
                "interval_solving_shared": round(solve_s, 3),
                "route_publish": round(publish_s, 3),
            }
            print(f"  timing PFE route publish {key}: {publish_s:.1f}s")
        rejected = [
            key for suffix, key in variants
            if not _report_is_publishable(reports[suffix])
        ]
        if rejected:
            raise RuntimeError(
                "PFE publication gate rejected variant(s) "
                f"{', '.join(rejected)}; no route variants were published")
        # The direction variants form one demand contract. Publishing q50
        # immediately and failing q10/q90 later left a hybrid set whose
        # metadata and scenario ensemble described different builds. Stage
        # every XML/agent pair, validate all of them, then flip the complete
        # set in deterministic order.
        for suffix, _key in variants:
            staged = staged_outputs[suffix]
            final = Path(variant_inputs[suffix]["out_path"])
            os.replace(staged, final)
            os.replace(_agent_path_for(staged), _agent_path_for(final))
        for suffix, _key in variants:
            data = variant_inputs[suffix]
            if not data.get("keep_achieved", False):
                reports[suffix] = {
                    k: v for k, v in reports[suffix].items() if k != "achieved"
                }
        return reports
    finally:
        for staged in staged_outputs.values():
            staged.unlink(missing_ok=True)
            _agent_path_for(staged).unlink(missing_ok=True)
            staged.with_suffix(staged.suffix + ".tmp").unlink(missing_ok=True)
        _PFE_PAR_SHAPES = None
        _PFE_PAR_ROUTE_COST = None
        _PFE_PAR_STRUCTURE_GROUPS = None
        _PFE_PAR_VARIANT_INPUTS = None
        _PFE_PAR_PURPOSE_MIXES = None


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
    """Expose provenance errors separately from an honest mix relaxation."""
    summary = report.get("purpose_allocation_summary", {})
    n = summary.get("quarters_with_incompatible_routes", 0)
    replaced = summary.get("replaced_routes", 0)
    if n:
        print(f"  ⚠ PURPOSE-ROUTE COMPATIBILITY DRIFT ({label}): {n} quarter(s), "
              f"routes {summary.get('incompatible_routes_by_purpose', {})} — "
              f"{replaced} route(s) were safely replaced where measured-edge "
              "signatures allowed; remaining routes lack same-purpose "
              "candidate provenance")
    relaxed = summary.get("quarters_with_relaxed_mix", 0)
    if relaxed:
        print(f"  ⚠ PURPOSE MIX RELAXED ({label}): {relaxed} quarter(s), "
              f"{summary.get('mix_reallocation_vehicles', 0)} vehicle(s) "
              "differ from the generated purpose prior so measured sensor "
              "counts can remain exact; every published route still retains "
              "its own source provenance")


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
