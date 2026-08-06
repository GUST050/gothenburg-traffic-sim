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
import resource
import sys
import time
from pathlib import Path

import numpy as np

from traffic_sim.demand import pfe
from demand.structure import (GEO_PATH, load_edge_geometry,
                              structure_groups_for_shapes)

# Inherited by fork into every pool worker; set before the pool starts,
# reset in the finally. Module-level init restores the None-check contract
# in _run_pfe_interval_job (the declarations were lost in an earlier
# in-file reorganisation — worked only because assignment always preceded
# the first call).
_PFE_PAR_SHAPES = None
_PFE_PAR_ROUTE_COST = None
_PFE_PAR_TOUCH_INDEX = None
_PFE_PAR_STRUCTURE_GROUPS = None
_PFE_PAR_VARIANT_INPUTS = None
_PFE_PAR_PURPOSE_MIXES = None
_PFE_PAR_SOLUTIONS = None
_PFE_PAR_RUNGS = None
_PFE_PAR_STAGED_OUTPUTS = None
_PFE_PAR_PREPARE_S = None
_PFE_PAR_SOLVE_S = None
_PFE_PAR_COUNTS = None
_PFE_PAR_COUNTS_S = None
_PFE_PAR_DAY_QUARTERS = None


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
    from traffic_sim.demand import pfe

    if (_PFE_PAR_SHAPES is None or _PFE_PAR_ROUTE_COST is None or
            _PFE_PAR_TOUCH_INDEX is None or
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
        touch_index=_PFE_PAR_TOUCH_INDEX,
    )
    return suffix, key, quarter, sol, rung


def _variant_quarter_purpose_mix(suffix: str, quarter: int):
    """The exact purpose mix write_calibration_report would use for a quarter.

    Kept in ONE place so the parallel counts phase and the writer can never
    derive different arguments for the same quarter.
    """
    mixes = _PFE_PAR_PURPOSE_MIXES[suffix]
    return mixes[quarter] if quarter < len(mixes) else Counter()


def _run_pfe_counts_job(job: tuple[str, int]):
    """ProcessPool worker for one quarter's integer repair (stage A1).

    The repair/purpose-reconciliation MILPs are per-quarter pure functions
    (pfe.quarter_publish_counts), so they parallelize exactly like the
    interval solves. The sequential remainder of publishing — endpoint draw
    ordinals, vehicle numbering, XML order — stays in the writer.

    Counts are returned SPARSE (nonzero indices + their values). A dense
    vector per (variant, quarter) would be hundreds of megabytes on a
    multi-day build for a vector that is almost entirely zero; taking the
    values through a nonzero mask reproduces the original array exactly,
    dtype included.
    """
    from traffic_sim.demand import pfe
    import numpy as np

    suffix, quarter = job
    data = _PFE_PAR_VARIANT_INPUTS[suffix]
    sol = _PFE_PAR_SOLUTIONS[suffix][quarter]
    if sol is None:
        return suffix, quarter, None
    purpose_mix = _variant_quarter_purpose_mix(suffix, quarter)
    bounds_pq = data["hard_bounds_pq"]
    counts, margin_enforced = pfe.quarter_publish_counts(
        _PFE_PAR_SHAPES, sol, data["targets"][quarter],
        bounds_pq[quarter] if bounds_pq is not None else None,
        _PFE_PAR_RUNGS[suffix][quarter],
        purpose_mix, bool(purpose_mix), True,
        [(members, cap_share)
         for _name, members, cap_share in (_PFE_PAR_STRUCTURE_GROUPS or [])],
    )
    idx = np.nonzero(counts)[0]
    return suffix, quarter, (idx.astype(np.int64), counts[idx],
                             counts.dtype.str, len(counts), margin_enforced)


def _publish_worker_budget(variant_count: int) -> int:
    """Publish-pool size bounded by real memory headroom, never optimism.

    Each forked publisher can end up holding a full copy of the parent's
    shape/solution state, so grant one worker per parent-sized slice of
    60% of machine RAM (the rest is headroom for the OS, SUMO and page
    cache). Any measurement failure returns 1 — the always-safe serial
    path this function exists to guard.
    """
    if variant_count <= 1:
        return 1
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform != "darwin":
            rss *= 1024   # Linux reports KiB; macOS reports bytes
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return 1
    if rss <= 0 or total <= 0:
        return 1
    headroom = int(total * 0.6) - rss
    if headroom < rss:
        return 1
    return max(1, min(variant_count, headroom // rss))


def _write_pfe_variant_report_job(job: tuple[str, str]):
    """Publish one already-solved uncertainty variant in a fork worker.

    q50/q10/q90 have disjoint output files and immutable inherited inputs.
    Writing them serially consumed more wall time than the interval solver on
    multi-day builds (five-day measurement: 1 813 s publish vs 1 403 s solve).
    Forking only this independent final phase preserves byte order inside each
    file while avoiding copies of the large solution/shape payload.
    """
    suffix, key = job
    if any(value is None for value in (
            _PFE_PAR_SHAPES, _PFE_PAR_VARIANT_INPUTS, _PFE_PAR_PURPOSE_MIXES,
            _PFE_PAR_SOLUTIONS, _PFE_PAR_RUNGS, _PFE_PAR_STAGED_OUTPUTS,
            _PFE_PAR_PREPARE_S, _PFE_PAR_SOLVE_S)):
        raise RuntimeError("PFE publish worker was not initialized")
    data = _PFE_PAR_VARIANT_INPUTS[suffix]
    started = time.perf_counter()
    report = pfe.write_calibration_report(
        _PFE_PAR_SHAPES, _PFE_PAR_STAGED_OUTPUTS[suffix],
        data["targets"], _PFE_PAR_SOLUTIONS[suffix],
        data["hard_bounds_pq"], _PFE_PAR_RUNGS[suffix],
        enforce_integer_bounds=True,
        structure_groups=[
            (members, cap_share)
            for _name, members, cap_share in (_PFE_PAR_STRUCTURE_GROUPS or [])
        ],
        edge_length_m=(
            load_edge_geometry()[2] if GEO_PATH.exists() else None),
        purpose_mixes_per_q=_PFE_PAR_PURPOSE_MIXES[suffix],
        day_quarters=_PFE_PAR_DAY_QUARTERS,
        precomputed_counts=(
            _PFE_PAR_COUNTS[suffix] if _PFE_PAR_COUNTS is not None else None),
    )
    publish_s = time.perf_counter() - started
    report["timings_s"] = {
        "prepare_shared": round(_PFE_PAR_PREPARE_S, 3),
        "interval_solving_shared": round(_PFE_PAR_SOLVE_S, 3),
        "integer_repair_shared": (round(_PFE_PAR_COUNTS_S, 3)
                                  if _PFE_PAR_COUNTS_S is not None else None),
        "route_publish": round(publish_s, 3),
    }
    return suffix, key, report, publish_s


def run_pfe_variants_flat_parallel(cand_path: Path, variants: list[tuple[str, str]],
                                   variant_inputs: dict[str, dict],
                                   max_workers: int | None = None,
                                   purpose_departure_offset_s: float = 0.0,
                                   activity_purpose_shares_by_quarter:
                                   list[dict[str, float]] | None = None,
                                   through_share_target: float | None = None,
                                   day_quarters: int | None = None) -> dict[str, dict]:
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
    from traffic_sim.demand import pfe

    global _PFE_PAR_SHAPES, _PFE_PAR_ROUTE_COST, _PFE_PAR_TOUCH_INDEX
    global _PFE_PAR_STRUCTURE_GROUPS
    global _PFE_PAR_VARIANT_INPUTS, _PFE_PAR_PURPOSE_MIXES
    global _PFE_PAR_SOLUTIONS, _PFE_PAR_RUNGS, _PFE_PAR_STAGED_OUTPUTS
    global _PFE_PAR_PREPARE_S, _PFE_PAR_SOLVE_S
    global _PFE_PAR_COUNTS, _PFE_PAR_COUNTS_S, _PFE_PAR_DAY_QUARTERS
    phase_started = time.perf_counter()
    shapes, route_cost = pfe.prepare_calibration(cand_path)
    prepare_s = time.perf_counter() - phase_started
    print(f"  timing PFE prepare: {prepare_s:.1f}s")
    _PFE_PAR_DAY_QUARTERS = day_quarters
    _PFE_PAR_SHAPES = shapes
    _PFE_PAR_ROUTE_COST = route_cost
    _PFE_PAR_TOUCH_INDEX = pfe.build_touch_index(shapes)
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
        # Through-share target LAST: the activity margin restores
        # P(purpose | hour) among activity classes, then the target sets
        # the through level while preserving that relative activity mix
        # (see pfe.apply_through_share_target for the validation record).
        _PFE_PAR_PURPOSE_MIXES[suffix] = pfe.apply_through_share_target(
            apply_activity_purpose_margin(
                source_mixes, activity_purpose_shares_by_quarter),
            through_share_target)
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
        counts_by_variant = {}
        for suffix, key in variants:
            data = variant_inputs[suffix]
            nq = len(data["targets"])
            solutions[suffix] = [None] * nq
            rungs[suffix] = [pfe.RUNG_INFEASIBLE] * nq
            counts_by_variant[suffix] = [None] * nq
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

        _PFE_PAR_SOLUTIONS = solutions
        _PFE_PAR_RUNGS = rungs
        _PFE_PAR_STAGED_OUTPUTS = staged_outputs
        _PFE_PAR_PREPARE_S = prepare_s
        _PFE_PAR_SOLVE_S = solve_s

        # Stage A1: the integer repair + purpose reconciliation MILPs are the
        # bulk of publishing and are per-quarter pure (pfe.quarter_publish_
        # counts). Solve them in the same flat (variant x quarter) shape as
        # the interval solves — one non-nested pool, results reassembled in
        # deterministic order — so the writer that follows only has to do the
        # genuinely sequential work. Results are IDENTICAL either way: the
        # writer computes any quarter left unfilled inline exactly as before.
        counts_started = time.perf_counter()
        with mp.get_context("fork").Pool(processes=n_workers) as pool:
            for suffix, quarter, packed in pool.imap_unordered(
                _run_pfe_counts_job,
                [(suffix, quarter) for suffix, _key, quarter in tasks]
            ):
                if packed is None:
                    continue
                idx, vals, dtype, length, margin_enforced = packed
                dense = np.zeros(length, dtype=np.dtype(dtype))
                dense[idx] = vals
                counts_by_variant[suffix][quarter] = (dense, margin_enforced)
        counts_s = time.perf_counter() - counts_started
        print(f"  timing PFE integer repair: {counts_s:.1f}s")
        _PFE_PAR_COUNTS = counts_by_variant
        _PFE_PAR_COUNTS_S = counts_s
        reports = {}
        # Publishing materializes large XML + agent JSON artifacts. Forking
        # writers gradually duplicates the parent's shape/solution state
        # (refcount touches defeat copy-on-write), which can thrash or
        # deadlock under memory pressure — the reason this once ran
        # unconditionally serially. But serial publishing is the DOMINANT
        # cost on long closure-envelope builds (measured 2026-07-21 on a
        # real 11-day build: 3 x ~37 min = 110 min of a 154-min build), so
        # the safe form is a MEMORY-GATED pool: fork extra publishers only
        # when the machine can hold that many full copies of the parent
        # with headroom; otherwise fall back to the proven serial path.
        # Byte-identity is by construction — the same worker function
        # writes the same distinct staged files in either mode.
        publish_workers = _publish_worker_budget(len(variants))
        if publish_workers > 1:
            print(f"  PFE publishing {len(variants)} variants in parallel "
                  f"({publish_workers} workers)")
            with mp.get_context("fork").Pool(processes=publish_workers) as pool:
                published = pool.map(_write_pfe_variant_report_job, variants)
        else:
            published = [
                _write_pfe_variant_report_job(variant) for variant in variants
            ]
        published_by_suffix = {suffix: (key, report, publish_s)
                               for suffix, key, report, publish_s in published}
        for suffix, key in variants:
            published_key, report, publish_s = published_by_suffix[suffix]
            if published_key != key:
                raise RuntimeError("PFE publish worker returned wrong variant")
            reports[suffix] = report
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
        _PFE_PAR_SOLUTIONS = None
        _PFE_PAR_RUNGS = None
        _PFE_PAR_COUNTS = None
        _PFE_PAR_COUNTS_S = None
        _PFE_PAR_STAGED_OUTPUTS = None
        _PFE_PAR_PREPARE_S = None
        _PFE_PAR_SOLVE_S = None
        for staged in staged_outputs.values():
            staged.unlink(missing_ok=True)
            _agent_path_for(staged).unlink(missing_ok=True)
            staged.with_suffix(staged.suffix + ".tmp").unlink(missing_ok=True)
        _PFE_PAR_SHAPES = None
        _PFE_PAR_ROUTE_COST = None
        _PFE_PAR_TOUCH_INDEX = None
        _PFE_PAR_STRUCTURE_GROUPS = None
        _PFE_PAR_VARIANT_INPUTS = None
        _PFE_PAR_PURPOSE_MIXES = None
        _PFE_PAR_DAY_QUARTERS = None


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


#: Ladder rungs that solved against a WIDENED measurement band, and the
#: multiplier each applied. Kept as names so this stays readable against a
#: stored report without importing the solver's integer constants.
_WIDENED_BAND_RUNGS = {"relax_tol2x": 2, "relax_tol4x": 4, "relax_no_bounds": 4}


def warn_relaxed_bound_violations(report: dict, label: str) -> None:
    """Expose intentional counts-first structural relaxations honestly.

    This used to end with the flat claim "sensor constraints were retained".
    That was true only in the sense that the constraint rows were still
    present: rungs that drop the Level-2 bounds also solve at ``tol_mult``
    4.0, so the measured counts were retained against a band four times
    wider. On this network GEH<5 cannot show that — the widest 4x band tops
    out at GEH 3.81, and no measured edge has ever exceeded 203 vehicles in
    a quarter — so if this warning does not say it, nothing does.
    """
    violations = report.get("relaxed_bound_violations", [])
    if not violations:
        return
    quarters = sorted({int(v["quarter"]) for v in violations})
    summary = report.get("relaxation_summary") or {}
    widened = {name: int(summary[name]) for name in _WIDENED_BAND_RUNGS
               if summary.get(name)}
    if widened:
        detail = ", ".join(
            f"{count} at x{_WIDENED_BAND_RUNGS[name]} ({name})"
            for name, count in sorted(widened.items()))
        band = (f"; {sum(widened.values())} interval(s) also solved against a "
                f"WIDENED measurement band — {detail}. GEH<5 cannot detect "
                f"this at these volumes")
    else:
        band = ("; every interval kept the unwidened measurement band "
                "(tol x1)")
    print(f"  ⚠ STRUCTURAL BOUNDS RELAXED ({label}): "
          f"{len(violations)} edge-quarter value(s) in quarters {quarters}"
          f"{band} — inspect confidence before use")
