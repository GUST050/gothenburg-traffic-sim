"""
PFE-lite — the level-4 reconciliation engine (Agent C's core).

Entropy-maximising route flow (Van Zuylen & Willumsen 1980) solved per
15-minute interval over the candidate route pool via Iterative
Proportional Fitting (IPF/Bregman/Sinkhorn balancing — see
solve_interval_entropy's docstring for the full derivation and history):

  variables    x_r ≥ 0            — how many vehicles use candidate route r
  HARD         |Σ_{r∋e} x_r − c_e| ≤ tol_e     for MEASURED edges e (level 1)
  HARD         L_e ≤ Σ_{r∋e} x_r ≤ U_e         for BOUNDED edges (level 2)
  SOFT         partial proportional pull toward PRIORS (level 3)
  MAX-ENTROPY  the flow distribution consistent with all of the above that
               adds the LEAST additional structure beyond the PSL-weighted
               prior — no route is used more than the constraints and its
               own prior weight actually justify.

REPLACED 2026-07-10 an LP (Bell & Shield 1996 lineage, `solve_interval`,
kept below as a battle-tested fallback only) whose linear parsimony
objective (minimise Σx_r) is mathematically indifferent between one route
carrying 100 vehicles and ten routes carrying 10 each — real concentration
this caused (found live, Gustav: "det ser ut som att vissa åker in i
sensorn och åker tillbaka igen" led to a whole chain of fixes) was patched
with a growing pile of tuned knobs (a route-share cap, then a reweight-
and-resolve loop, then an MSA damping fix for THAT, then a pass count)
before Gustav twice pushed back and asked for the actually-principled fix
instead of another knob. Entropy maximisation IS that fix, and (via IPF)
is also simpler and faster than what it replaced: no repeated LP solves.

ε_r is a per-route PATH SIZE weight (Ben-Akiva & Bierlaire 1999, Ramming
2002 link-count variant), not a flat constant: routes that overlap heavily
with many other candidates in the pool cost more, routes using largely
distinctive edges cost the base rate — used here as the IPF seed's PRIOR
distribution (inverted: a lower PSL cost means a more distinctive route,
which starts with more weight).

Route continuity makes conservation hold by construction, so the solution
is always network-consistent. Fractional route uses are rounded by
largest remainder.

Not a CLI — imported by build_sumo_demand.py (--engine pfe).
"""

from __future__ import annotations

import os
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import csr_matrix, hstack, identity, lil_matrix, vstack

import pfe_kernel

# Compiled IPF fast path (see pfe_kernel.py's bit-identity contract).
# PFE_PURE=1 forces the pure-Python reference loop; a numba-less install
# falls back automatically.
_KERNEL_ENABLED = (pfe_kernel.NUMBA_AVAILABLE
                   and os.environ.get("PFE_PURE") != "1")


def entropy_solver_backend() -> str:
    """Report the active IPF implementation for build telemetry and support."""
    return "numba" if _KERNEL_ENABLED else "python"

EPS_PARSIMONY = 1e-3
MEAS_TOL_FRAC = 0.05      # hard band around measured counts
MEAS_TOL_MIN  = 2.0


@dataclass
class Candidate:
    depart: float
    edges: list[str]
    source_id: str = ""
    intent: dict = field(default_factory=dict)
    # Shared, top-level candidate-meta pool document.  Every Candidate holds
    # the same reference, not a copy: a location field can contain thousands
    # of anonymous building/POI access points and must not be duplicated per
    # route in memory.
    location_pools: dict[str, list[dict]] = field(default_factory=dict, repr=False)
    source_candidates: list["Candidate"] = field(default_factory=list, repr=False)


def load_candidates(path: Path) -> list[Candidate]:
    meta_path = path.with_name(path.name.replace(".rou.xml", ".meta.json"))
    metadata: dict[str, dict] = {}
    location_pools: dict[str, list[dict]] = {}
    if meta_path.exists():
        with open(meta_path) as f:
            document = json.load(f)
        metadata = document.get("candidates", {})
        raw_pools = document.get("location_pools", {})
        if isinstance(raw_pools, dict):
            location_pools = raw_pools
        if not isinstance(metadata, dict):
            metadata = {}
    out = []
    for veh in ET.parse(path).getroot().iter("vehicle"):
        route = veh.find("route")
        source_id = veh.get("id") or ""
        out.append(Candidate(depart=float(veh.get("depart")),
                             edges=route.get("edges").split(),
                             source_id=source_id,
                             intent=dict(metadata.get(source_id, {})),
                             location_pools=location_pools))
    return out


# Path size is bounded below to keep the parsimony penalty a gentle
# tie-breaker (never more than ~7x the base rate) rather than something
# that could fight the hard/soft constraints above it in the hierarchy.
PATH_SIZE_FLOOR = 0.15


def path_size_weights(shapes: list[Candidate]) -> np.ndarray:
    """Per-route parsimony cost (Ben-Akiva & Bierlaire 1999 Path Size,
    Ramming 2002's link-count-based simplification — no route/link lengths
    needed, just how many OTHER candidates in the pool share each edge):

        PS_r = (1/|r|) · Σ_{e∈r} 1/N_e         N_e = #candidates using e

    PS_r → 1 for a route on largely distinctive edges, → small for one that
    overlaps heavily with many alternatives. Returned as EPS_PARSIMONY/PS_r
    so it drops straight into c_obj in place of the flat EPS_PARSIMONY."""
    edge_route_count: dict[str, int] = {}
    for cand in shapes:
        for e in set(cand.edges):
            edge_route_count[e] = edge_route_count.get(e, 0) + 1
    ps = np.ones(len(shapes))
    for i, cand in enumerate(shapes):
        if cand.edges:
            ps[i] = sum(1.0 / edge_route_count[e] for e in cand.edges) / len(cand.edges)
    return EPS_PARSIMONY / np.clip(ps, PATH_SIZE_FLOOR, 1.0)


def solve_interval(
    cands: list[Candidate],
    measured: dict[str, float],
    bounds: dict[str, tuple[float, float]],
    priors: dict[str, tuple[float, float]],   # edge → (target, weight)
    tol_mult: float = 1.0,
    route_cost: np.ndarray | None = None,     # per-route parsimony cost;
                                              # flat EPS_PARSIMONY if None
    groups: list[tuple[list[int], float, float]] | None = None,
) -> np.ndarray | None:
    """Return route-use vector for one interval, or None if infeasible.

    SUPERSEDED 2026-07-10 as calibrate()'s PRIMARY solver by
    solve_interval_entropy (IPF) — kept as calibrate()'s final relaxation-
    ladder rung, a battle-tested, complete LP solver for the rare case IPF
    doesn't converge for some edge-case constraint combination. Its own
    former route-concentration mitigation (a per-route share cap) is gone
    too: that was working around THIS function's linear parsimony
    objective specifically, which is no longer the primary path — as a
    rarely-invoked fallback, this only needs to find ANY feasible point,
    not a well-dispersed one."""
    n = len(cands)
    if n == 0:
        return None

    # Which constrained edges does each candidate touch?
    edges_of_interest = set(measured) | set(bounds) | set(priors)
    touch: dict[str, list[int]] = {e: [] for e in edges_of_interest}
    for j, cand in enumerate(cands):
        for e in cand.edges:
            if e in touch:
                touch[e].append(j)

    # A measured edge no candidate can serve cannot make the WHOLE interval
    # infeasible — drop that constraint (the count is unserveable with this
    # pool) and serve everything else.
    measured = {e: v for e, v in measured.items() if touch.get(e)}

    n_prior = len(priors)
    N = n + 2 * n_prior                     # x_r  +  (d⁺, d⁻) per prior
    c_obj = np.full(N, EPS_PARSIMONY)
    if route_cost is not None:
        c_obj[:n] = route_cost
    rows_ub, b_ub = [], []
    rows_eq, b_eq = [], []

    def row(indices, coeffs, extra=None):
        r = lil_matrix((1, N))
        for i, cf in zip(indices, coeffs):
            r[0, i] = cf
        if extra:
            for i, cf in extra:
                r[0, i] = cf
        return r

    # Level 1 — measured, hard band (tol widened by the relaxation ladder)
    for e, target in measured.items():
        js = touch[e]
        tol = max(MEAS_TOL_MIN, MEAS_TOL_FRAC * target) * tol_mult
        rows_ub.append(row(js, [1] * len(js)));  b_ub.append(target + tol)
        rows_ub.append(row(js, [-1] * len(js))); b_ub.append(-max(0.0, target - tol))

    # Level 2 — interval bounds, hard
    for e, (lo, hi) in bounds.items():
        js = touch.get(e, [])
        if not js:
            if lo > 1.0:
                return None
            continue
        rows_ub.append(row(js, [1] * len(js)));  b_ub.append(hi)
        if lo > 0:
            rows_ub.append(row(js, [-1] * len(js))); b_ub.append(-lo)

    # Level 2b — route-index groups (2026-07-12, structure preservation;
    # see solve_interval_entropy's groups comment) — same band shape as a
    # bound, just over an explicit index set instead of an edge's touch set.
    for js, lo, hi in (groups or []):
        if not js:
            continue
        rows_ub.append(row(js, [1] * len(js)));  b_ub.append(hi)
        if lo > 0:
            rows_ub.append(row(js, [-1] * len(js))); b_ub.append(-lo)

    # Level 3 — priors, soft L1 pull
    for pi, (e, (target, weight)) in enumerate(priors.items()):
        js = touch.get(e, [])
        i_pos, i_neg = n + 2 * pi, n + 2 * pi + 1
        c_obj[i_pos] = c_obj[i_neg] = weight
        rows_eq.append(row(js, [1] * len(js),
                           extra=[(i_pos, -1), (i_neg, 1)]))
        b_eq.append(target)

    res = linprog(
        c_obj,
        A_ub=vstack(rows_ub).tocsc() if rows_ub else None,
        b_ub=np.array(b_ub) if rows_ub else None,
        A_eq=vstack(rows_eq).tocsc() if rows_eq else None,
        b_eq=np.array(b_eq) if rows_eq else None,
        bounds=[(0, None)] * N, method="highs",
    )
    if not res.success:
        return None
    return res.x[:n]


IPF_MAX_ITERATIONS = 200


def solve_interval_entropy(
    cands: list[Candidate],
    measured: dict[str, float],
    bounds: dict[str, tuple[float, float]],
    priors: dict[str, tuple[float, float]],
    route_cost: np.ndarray | None = None,
    tol_mult: float = 1.0,
    max_iterations: int = IPF_MAX_ITERATIONS,
    groups: list[tuple[list[int], float, float]] | None = None,
) -> np.ndarray | None:
    """Entropy-maximising route flow via Iterative Proportional Fitting
    (Bregman/Sinkhorn balancing) — 2026-07-10, replacing solve_interval's
    LP + the whole MAX_ROUTE_SHARE/DISPERSION_PASSES apparatus above, after
    Gustav pushed back twice on tacking on more knobs ("kan du inte göra en
    bättre lösning som är mer robust istället för att lägga till
    constraints") and asked why the pool's 3435 sensor-touching shapes
    weren't being used ("det känns som att det ska finnas väldigt mycket
    fler rutter"). Both were right: solve_interval's linear parsimony
    objective (minimise Σx_r) has NO preference for spreading flow — it's
    mathematically indifferent between one route carrying 100 vehicles and
    ten routes carrying 10 each, so ties get broken by whatever the LP
    solver's vertex-selection happens to prefer, which is where the
    concentration came from. Fixing that by penalising concentration after
    the fact (MAX_ROUTE_SHARE, then reweight_for_dispersion + MSA damping)
    was a growing pile of tuned knobs (a share cap, a beta, a pass count)
    layered on top of the WRONG objective, and even then had a real failure
    mode found by testing at scale: too many dispersion passes eventually
    collapsed GEH from 100% to 7% (the reweighting pressure overwhelmed the
    relaxation ladder). The textbook fix was identified earlier the same
    day (Van Zuylen & Willumsen 1980, already in this project's own
    references): maximise entropy -Σx_r·ln(x_r) subject to the same
    constraints, whose Lagrangian has the closed form x_r = A_r·Π_{e∈r}
    exp(λ_e) — a route's flow is a PRODUCT of per-edge multipliers, not a
    single LP vertex choice. Solving for those multipliers via alternating
    proportional rescaling (IPF/Sinkhorn/Bregman balancing — the same
    classical technique behind Fratar/Furness trip-distribution growth
    factors and RAS matrix-balancing, in continuous use since the 1940s;
    von Neumann's alternating-projections theorem guarantees convergence
    to a point in the intersection of the constraint sets when one exists)
    is: seed x_r from a prior (route_cost, inverted — a Path-Size-Logit-
    weighted starting distribution, so structurally distinctive routes
    still start preferred over heavily-overlapping ones, same spirit as
    before), then repeatedly rescale every constrained edge's touching
    routes toward its target/band, cycling until stable. No LP solve at
    all after the seed — each pass is a handful of vector sums and
    multiplies, not a HiGHS call, so it is both MORE principled (the actual
    max-entropy solution, not an LP proxy for it) and far CHEAPER (no
    per-pass solver overhead) than the machinery it replaces.

    HONEST LIMITS: hard bands/bounds are enforced by construction (each
    touching edge is rescaled to lie in-range every pass; a final check
    below confirms convergence rather than assuming it) — if the
    intersection of level-1 and level-2 ranges for some edge is empty, or
    a measured edge has no candidate at all, this returns None exactly
    like solve_interval did, and the SAME relaxation ladder in calibrate()
    still applies (widen tolerance, drop bounds, in that order) since
    those are properties of the CONSTRAINTS, not the solving method."""
    n = len(cands)
    if n == 0:
        return None

    touch: dict[str, list[int]] = {}
    for j, cand in enumerate(cands):
        for e in set(cand.edges):
            if e in measured or e in bounds or e in priors:
                touch.setdefault(e, []).append(j)

    measured = {e: v for e, v in measured.items() if touch.get(e)}
    for e, (lo, hi) in bounds.items():
        if not touch.get(e) and lo > 1.0:
            return None

    # Seed: PSL-informed prior (inverse route_cost — a distinctive route
    # starts preferred over a heavily-overlapping one), restricted to
    # routes ACTUALLY REQUIRED to be nonzero (everything else stays at
    # exactly 0 — true parsimony, matching the LP it replaced, not just a
    # small-but-nonzero placeholder).
    #
    # FOUND 2026-07-10 (live run: 49714 vehicles where ~3000 were
    # expected, root-caused in two layers, both from a live-data stress
    # test the small unit tests never exercised):
    #   (1) 1/route_cost alone gives ABSOLUTE seed values of 150-1000
    #       (route_cost is EPS_PARSIMONY/PS_r, PS_r in [0.15,1]) — fixed
    #       by SEED_SCALE below, well under any realistic constraint size.
    #   (2) The deeper issue: build_sumo_demand.py's gravity-assignment
    #       field adds a wide BOUND (max(5, 5x), commonly 50-250) on
    #       essentially EVERY edge in the network as a plausibility cap —
    #       not a requirement (lo=0 always for it). Treating "touches a
    #       bound" as reason enough to seed a route active made nearly
    #       all 7271 candidates "relevant", each contributing SOME nonzero
    #       flow that no real measured count ever asked for. The ORIGINAL
    #       LP's parsimony term actively REWARDS zero for anything not
    #       specifically required; a wide upper-only bound gives IPF no
    #       such pull — nothing but the seed decides whether an
    #       incidental route participates at all. FIX: only seed routes
    #       touching a MEASURED edge, a PRIOR, or a bound that genuinely
    #       REQUIRES flow (lo>0) — a pure ceiling (lo=0) is enforced
    #       exactly as before (still clips anything that ends up there),
    #       it just isn't, by itself, a reason to activate a route.
    SEED_SCALE = 1e-3
    relevant = np.zeros(n, dtype=bool)
    for e, js in touch.items():
        requires_flow = e in measured or e in priors or bounds.get(e, (0, 0))[0] > 0
        if requires_flow:
            for j in js:
                relevant[j] = True
    if groups:
        # Same activation rule as bounds: only a group that REQUIRES flow
        # (lo>0) is a reason to seed its members active.
        for js, lo, _hi in groups:
            if lo > 0:
                for j in js:
                    relevant[j] = True
    if route_cost is not None:
        prior = SEED_SCALE / np.clip(route_cost, 1e-12, None)
    else:
        prior = np.full(n, SEED_SCALE)
    x = np.where(relevant, np.maximum(prior, 1e-9), 0.0)

    # FOUND 2026-07-10 (unit tests, not guessed): a level-3 prior pulling
    # toward a target that CONFLICTS with a hard constraint sharing its
    # routes (e.g. the only route touching prior edge 'p' is also pinned
    # by a measured band on edge 'm') oscillates FOREVER under naive
    # alternating projection — level 1/2 rescale back into range, the
    # prior's pull immediately overshoots back out, repeat. An iteration-
    # decayed prior step (1/(it+1)) breaks the cycle but creates a WORSE
    # bug: it also slows down the ORDINARY, non-conflicting case (a lone
    # prior with nothing to fight), converging to 38.4 instead of 40
    # within the iteration budget — decaying by iteration count penalises
    # every prior, not just the ones actually in conflict.
    # THE FIX: sample x right after the HARD (level 1/2) correction each
    # iteration — a point that, by construction, ALREADY satisfies every
    # hard constraint just enforced — then average that sample across
    # iterations (skipping an initial burn-in). Convex combinations of
    # hard-feasible points stay hard-feasible (same argument used for MSA
    # averaging in build_sumo_demand.py's congestion-feedback loop and
    # calibrate()'s superseded dispersion mechanism), so the averaged
    # result is hard-feasible regardless of whether the prior settles or
    # keeps oscillating — and when it settles (the ordinary case), the
    # average of a constant sequence is just that constant, so ordinary
    # priors still converge to their real target, undamped.
    burn_in = max_iterations // 5
    x_sum = np.zeros(n)
    n_samples = 0

    # PERFORMANCE (2026-07-10, profiled on a realistic ~6500-bound-edge
    # interval: 1.3M numpy fancy-index sum() calls dominated the runtime —
    # 4x speedup measured, verified bit-close (<1e-13 abs diff, pure
    # float-summation-order noise) against the numpy version on real
    # feasible/infeasible cases before adopting). Each edge here is
    # typically touched by only a handful of routes (median well under
    # 20), so `x[js].sum()`/`x[js] *= factor` pay numpy's per-call fancy-
    # indexing + ufunc-dispatch overhead for an array far too small to
    # amortize it. The fix is NOT to vectorize across edges — this loop is
    # Gauss-Seidel (each edge's correction sees the PREVIOUS edge's
    # already-updated values within the same pass, not the pass-start
    # values), and jointly vectorizing every edge's correction at once
    # would silently change that to a Jacobi-style update — a real
    # algorithmic change with different convergence behaviour, exactly the
    # kind of retuning this function's own history (see the comments
    # throughout) warns is expensive to get subtly wrong. Instead: convert
    # x to a plain Python list once per iteration and do the small per-
    # edge sums/rescales as pure-Python loops (measured/bounds/priors are
    # precomputed as plain lists once, outside the iteration loop, instead
    # of re-deriving them from dict.items()/.get() every pass) — same
    # sequential update order, same arithmetic, just without numpy's
    # per-call overhead on tiny arrays. x_sum stays a numpy vector
    # addition over the FULL array, which is exactly where numpy DOES pay
    # off (one call, thousands of elements) — untouched.
    measured_items = [(touch[e], target) for e, target in measured.items()]
    bounds_items = [(touch.get(e, []), lo, hi) for e, (lo, hi) in bounds.items()
                    if touch.get(e)]
    # groups (2026-07-12; see IMPROVEMENT_PLAN.md's destination-integrity
    # rules): a band over
    # an arbitrary ROUTE-INDEX set instead of an edge's touching set —
    # structurally identical to a bound, enforced by the exact same
    # rescale-into-band correction below. Used for structure preservation:
    # e.g. "routes ENDING within 200 m of a sensor may carry at most
    # cap·total vehicles this quarter", which count-matching alone would
    # otherwise violate freely (such routes are 'free variables' for
    # closing one sensor's band without touching any other sensor's).
    # Like a pure-ceiling bound (lo=0), a group does NOT activate its
    # member routes by itself — it only clips what lands there.
    if groups:
        bounds_items = bounds_items + [
            (list(js), lo, hi) for js, lo, hi in groups if js]
    priors_items = [(touch.get(e, []), target, weight)
                    for e, (target, weight) in priors.items()
                    if touch.get(e) and target > 0 and weight > 0]

    # COMPILED FAST PATH (2026-07-16, pfe_kernel.py): the loop below was
    # measured at 91-96% of the whole demand build (Phase I). The numba
    # kernel executes the IDENTICAL operations in the IDENTICAL order —
    # bitwise-equal output, proven on the real network across all 96
    # quarters by tools/verify_pfe_kernel.py and pinned by
    # tests/test_pfe_kernel.py + the H2 benchmark fingerprint. PFE_PURE=1
    # forces this pure-Python reference path (verification, debugging,
    # or a numba-less install). ANY edit to the loop below must be
    # mirrored in pfe_kernel.ipf_iterations and re-verified bitwise.
    if _KERNEL_ENABLED:
        m_indptr, m_idx, m_target = pfe_kernel.csr_items(measured_items, 1)
        b_indptr, b_idx, b_lo, b_hi = pfe_kernel.csr_items(bounds_items, 2)
        p_indptr, p_idx, p_target, p_weight = pfe_kernel.csr_items(
            priors_items, 2)
        x = pfe_kernel.ipf_iterations(
            x.astype(np.float64, copy=True),
            m_indptr, m_idx, m_target,
            b_indptr, b_idx, b_lo, b_hi,
            p_indptr, p_idx, p_target, p_weight,
            burn_in, max_iterations)
        return _check_entropy_solution(x, touch, measured, bounds, groups,
                                       tol_mult)

    for it in range(max_iterations):
        x_list = x.tolist()

        # Level 1 — measured, hard band (tol widened by the relaxation
        # ladder). ALWAYS pulls to the EXACT target, not just the nearest
        # band edge when outside it. FOUND 2026-07-10 (live run: GEH<5
        # collapsed to 64/93/36% from the LP's 100%, and 2000+ iterations
        # of the "lazy" version never closed the gap on any of them,
        # ruling out a simple convergence-budget issue): with hundreds of
        # routes sharing each measured edge AND overlapping bounds, a
        # correction that does NOTHING once inside the band gives too
        # weak a pull — the system settled into a STABLE fixed point where
        # every measured edge sat just OUTSIDE its own band by a similar
        # small margin, not oscillating (more iterations didn't change
        # it), just short of the true intersection a feasible point (the
        # LP proves one exists). Pulling to the exact target every
        # iteration (not just the boundary) gives a strictly stronger,
        # monotonically-corrective force that reaches the true
        # intersection instead of stalling near it — verified directly:
        # this alone took this exact scenario from 0/7 to 7/7 edges
        # in-band after the same 200 iterations.
        for js, target in measured_items:
            total = 0.0
            for j in js:
                total += x_list[j]
            if total <= 0:
                continue
            factor = target / total
            for j in js:
                x_list[j] *= factor

        # Level 2 — interval bounds, hard
        for js, lo, hi in bounds_items:
            total = 0.0
            for j in js:
                total += x_list[j]
            if total <= 0:
                continue
            factor = lo / total if total < lo else (hi / total if total > hi else 1.0)
            if factor != 1.0:
                for j in js:
                    x_list[j] *= factor

        # Sample HERE, right after the hard correction — by construction
        # this point already satisfies every hard constraint just
        # enforced, so averaging these samples across iterations (below)
        # stays hard-feasible regardless of whether level 3 (next) ever
        # settles or keeps oscillating against a hard constraint sharing
        # its routes.
        x = np.array(x_list)
        if it >= burn_in:
            x_sum += x
            n_samples += 1


        # Level 3 — priors, soft partial pull (weight=0 -> no pull at all;
        # weight->inf -> a full rescale to the target, same as level 1/2).
        for js, target, weight in priors_items:
            total = 0.0
            for j in js:
                total += x_list[j]
            if total <= 0:
                continue
            alpha = weight / (weight + 1.0)
            factor = 1.0 + alpha * (target / total - 1.0)
            if factor > 0 and factor != 1.0:
                for j in js:
                    x_list[j] *= factor
        x = np.array(x_list)

    if n_samples > 0:
        x = x_sum / n_samples

    return _check_entropy_solution(x, touch, measured, bounds, groups,
                                   tol_mult)


def _check_entropy_solution(x, touch, measured, bounds, groups, tol_mult):
    """Confirm convergence rather than assume it — an empty level-1/level-2
    intersection for some edge would otherwise silently return a vector
    that doesn't actually satisfy the hard constraints. Shared verbatim by
    the pure-Python and compiled paths (it runs on the FINAL x, so path
    equality upstream makes its verdicts identical)."""
    for e, target in measured.items():
        js = touch[e]
        tol = max(MEAS_TOL_MIN, MEAS_TOL_FRAC * target) * tol_mult
        total = x[js].sum()
        if not (target - tol - 1e-6 <= total <= target + tol + 1e-6):
            return None
    for e, (lo, hi) in bounds.items():
        js = touch.get(e, [])
        if js:
            total = x[js].sum()
            if not (lo - 1e-6 <= total <= hi + 1e-6):
                return None
    if groups:
        for js, lo, hi in groups:
            if js:
                total = x[js].sum()
                if not (lo - 1e-6 <= total <= hi + 1e-6):
                    return None

    return x


def largest_remainder_round(x: np.ndarray) -> np.ndarray:
    base = np.floor(x)
    remainder = x - base
    n_extra = int(round(x.sum() - base.sum()))
    if n_extra > 0:
        base[np.argsort(-remainder)[:n_extra]] += 1
    return base.astype(int)


def round_preserving_measured(
    x: np.ndarray,
    shapes: list[Candidate],
    measured: dict[str, float],
) -> np.ndarray:
    """Round the continuous solution to integer vehicle counts, but
    round EACH measured edge's own group of routes to hit ITS OWN target
    — not just largest_remainder_round's single global pass, which only
    guarantees the TOTAL across the whole pool is preserved.

    FOUND 2026-07-10 (live run: GEH<5 collapsed to 28.6%/28.6%/50% even
    though the raw, unrounded IPF solution matched every measured edge to
    within 0.1-0.4 vehicles): largest_remainder_round decides which
    routes round up vs down based on their fractional remainder ACROSS
    THE WHOLE POOL, with no awareness of which measured edge a route
    belongs to. That was invisible under the OLD LP's typical solution
    shape (a handful of routes each carrying many vehicles, so rounding
    error is a tiny fraction of any edge's total) but became catastrophic
    once entropy maximisation started genuinely dispersing flow across
    hundreds of small-valued (2-4 vehicle) routes per edge: target=185,
    raw=185.0 (correct!), globally-rounded=59.0 — the routes touching
    THIS edge mostly lost their global rounding lottery to routes
    touching OTHER edges. Plain per-route rounding (np.round) is even
    worse (0% GEH) since most small values round straight to zero.

    Starts from a single global largest_remainder_round (a reasonable,
    total-preserving initial guess), then repeatedly nudges INDIVIDUAL
    vehicle counts by ±1 to close each measured edge's remaining gap —
    surgical single-vehicle adjustments, not "claim this route's whole
    count for my edge alone".

    FOUND 2026-07-10: an earlier version of this function processed
    measured edges one at a time, giving each one's target FIRST CLAIM on
    its still-undecided routes (locking each route's count in once ANY
    edge had claimed it). That failed hard on a real edge whose ENTIRE
    1043-route touching-set overlapped with at least one OTHER measured
    edge (a corridor edge between the two sensor clusters, 100% shared,
    zero routes serving it exclusively): whichever edge in the shared
    group got processed LAST found nothing left to claim, landing at 2
    vehicles against a target of 185 (GEH 18.9, worse than doing nothing).
    No processing ORDER fixes this when overlap is total — some edge is
    always last. Small, targeted ±1 adjustments instead of locking whole
    groups mean one edge's correction only nudges its neighbours by the
    same handful of vehicles it moved, which a later pass (or the same
    edge revisited) can correct right back — so multiple passes converge
    toward a joint compromise instead of one edge winning everything."""
    counts = largest_remainder_round(x)
    edge_routes: dict[str, list[int]] = {}
    n_measured_touched: dict[int, int] = {}
    for j, cand in enumerate(shapes):
        touched = set(cand.edges) & measured.keys()
        if touched:
            n_measured_touched[j] = len(touched)
            for e in touched:
                edge_routes.setdefault(e, []).append(j)

    # FOUND 2026-07-10 (live run: two adjacent measured edges stuck
    # oscillating +-38 vehicles forever, never settling, EVEN THOUGH 941
    # of edge A's 1043 routes and 148 of edge B's don't touch the other
    # edge at all — plenty of independent capacity to satisfy both):
    # sorting adjustment candidates by the continuous-vs-rounded gap
    # ALONE doesn't distinguish a route exclusive to this edge from one
    # shared with another measured edge, so the sort could keep landing
    # on the ~100 SHARED routes, correcting one edge by breaking the
    # other, forever. Preferring the LEAST-shared routes first for every
    # adjustment (falls back to shared ones only once exclusive capacity
    # runs out) uses the independent capacity that's actually there
    # instead of fighting over the same handful of shared routes.
    for _ in range(4):   # a few passes let edges sharing routes settle
        moved = False
        for e, target in measured.items():
            js = edge_routes.get(e, [])
            if not js:
                continue
            current = sum(counts[j] for j in js)
            deficit = int(round(target - current))
            if deficit == 0:
                continue
            moved = True
            if deficit > 0:
                order = sorted(js, key=lambda j: (n_measured_touched[j], counts[j] - x[j]))
                for j in order[:deficit]:
                    counts[j] += 1
            else:
                order = sorted((j for j in js if counts[j] > 0),
                              key=lambda j: (n_measured_touched[j], x[j] - counts[j]))
                for j in order[:-deficit]:
                    counts[j] -= 1
        if not moved:
            break
    return counts


def repair_integer_bounds(
    counts: np.ndarray,
    shapes: list[Candidate],
    measured: dict[str, float],
    bounds: dict[str, tuple[float, float]],
    groups: list[tuple[list[int], float, float]] | None = None,
    measurement_tol_mult: float | None = None,
) -> np.ndarray | None:
    """Repair a rounded route vector without weakening its constraints.

    The fast measurement-preserving rounding above is the normal path.  It
    can, however, move a shared route by one vehicle while closing a measured
    count and thereby breach a separate structural edge bound.  Only in that
    case solve a small integer reconciliation problem: minimise the number of
    changed route counts while keeping every supplied bound in its
    integer-feasible interval.

    ``measurement_tol_mult`` makes the discrete stage obey the relaxation
    rung that produced the continuous solution. ``None`` preserves the
    historical exact-rounded-target behaviour. A numeric multiplier instead
    uses the same Level-1 measurement band as the solver. The initial vector
    still targets the exact sensor value, so this wider band is used only when
    an exact integer target cannot coexist with the retained Level-2 bounds.
    Reimposing an exact value after the continuous solver intentionally used a
    wider valid band is internally inconsistent and can make a feasible
    calibration look impossible at publication time.

    ``groups`` (2026-07-12, structure preservation — see solve_interval_
    entropy's groups comment): the same band shape over an explicit
    route-index set. Needed HERE, at the integer stage, because rounding
    can undo a continuously-satisfied group cap wholesale: with thousands
    of tiny fractional route-uses, largest-remainder rounding hands whole
    vehicles to the individually-largest values — which are exactly the
    near-sensor-ending routes the continuous cap squeezed into fewer,
    relatively larger shares (measured: 95 of 96 quarters violated the
    published cap despite every continuous solution honouring it).

    Returning ``None`` means the discrete problem is genuinely infeasible or
    did not finish in its bounded time; callers must keep the publication gate
    closed in that case rather than silently emitting an invalid route file.
    """
    groups = [g for g in (groups or []) if g[0]]
    if not bounds and not groups:
        return counts

    constrained = set(measured) | set(bounds)
    touch: dict[str, list[int]] = {e: [] for e in constrained}
    for j, cand in enumerate(shapes):
        for e in set(cand.edges) & constrained:
            touch[e].append(j)

    def total(e: str) -> int:
        return int(sum(counts[j] for j in touch.get(e, [])))

    violating = [
        e for e, (lo, hi) in bounds.items()
        if total(e) < np.ceil(lo - 0.5) or total(e) > np.floor(hi + 0.5)
    ]
    group_violating = [
        (js, lo, hi) for js, lo, hi in groups
        if not (np.ceil(lo - 0.5) <= sum(counts[j] for j in js) <= np.floor(hi + 0.5))
    ]
    if not violating and not group_violating:
        return counts

    active = sorted({j for e in constrained for j in touch.get(e, [])}
                    | {j for js, _lo, _hi in groups for j in js})
    if not active:
        return None
    active_index = {j: k for k, j in enumerate(active)}
    n = len(active)

    # z are integer route counts. p/n are continuous absolute-deviation
    # auxiliaries so the solver preserves the fast round wherever possible.
    # When the continuous solve used a measurement band, two more variables
    # per sensor represent absolute deviation from the exact rounded target.
    # Their large objective weight keeps exact sensor counts first whenever
    # they coexist with the retained structural constraints; only a genuinely
    # impossible exact integer target uses the solver-authorised band.
    measurement_items = list(measured.items()) if measurement_tol_mult is not None else []
    measurement_index = {edge: k for k, (edge, _target) in enumerate(measurement_items)}
    n_measurement_deviations = 2 * len(measurement_items)
    n_vars = 3 * n + n_measurement_deviations
    # A ±20 local window is deliberately wider than the single-vehicle
    # reconciliation nudges and avoids a route-count explosion in the repair.
    aeq = hstack((identity(n, format="csr"), -identity(n, format="csr"),
                  identity(n, format="csr"),
                  csr_matrix((n, n_measurement_deviations))), format="csr")
    rows = [aeq]
    lower = [float(counts[j]) for j in active]
    upper = [float(counts[j]) for j in active]

    def add_index_constraint(js: list[int], lo: float, hi: float) -> None:
        row = lil_matrix((1, n_vars), dtype=float)
        for j in js:
            k = active_index.get(j)
            if k is not None:
                row[0, k] = 1.0
        rows.append(row.tocsr())
        lower.append(lo)
        upper.append(hi)

    def add_edge_constraint(edge: str, lo: float, hi: float) -> None:
        add_index_constraint(touch.get(edge, []), lo, hi)

    for edge, target in measured.items():
        if measurement_tol_mult is None:
            # The historical contract for direct callers and optional
            # structure-cap repair: preserve the rounded sensor total exactly.
            target_i = float(int(round(target)))
            add_edge_constraint(edge, target_i, target_i)
            continue
        tol = max(MEAS_TOL_MIN, MEAS_TOL_FRAC * target) * measurement_tol_mult
        # An integer count represents a continuous value rounded to the
        # nearest vehicle. Permit the same half-vehicle boundary treatment as
        # Level-2 constraints below, while never allowing negative flow.
        lo = float(max(0, int(np.ceil(target - tol - 0.5))))
        hi = float(max(lo, int(np.floor(target + tol + 0.5))))
        add_edge_constraint(edge, lo, hi)
        # total - above_target + below_target = rounded target
        # (both auxiliaries are non-negative), allowing the objective to
        # prefer the exact target lexicographically over route-count changes.
        row = lil_matrix((1, n_vars), dtype=float)
        for j in touch.get(edge, []):
            k = active_index.get(j)
            if k is not None:
                row[0, k] = 1.0
        d = 3 * n + 2 * measurement_index[edge]
        row[0, d] = -1.0
        row[0, d + 1] = 1.0
        target_i = float(int(round(target)))
        rows.append(row.tocsr())
        lower.append(target_i)
        upper.append(target_i)
    for edge, (lo, hi) in bounds.items():
        add_edge_constraint(edge, float(np.ceil(lo - 0.5)),
                            float(np.floor(hi + 0.5)))
    for js, lo, hi in groups:
        add_index_constraint(js, float(np.ceil(lo - 0.5)),
                             float(np.floor(hi + 0.5)))

    base = np.asarray([counts[j] for j in active], dtype=float)
    # One unit of sensor miss must always lose to any practical number of
    # local route-count nudges. This is still one linear MILP (not a slower
    # two-pass solve) and has only two extra continuous variables per sensor.
    sensor_miss_penalty = 1_000_000.0
    result = milp(
        c=np.r_[np.zeros(n), np.ones(2 * n),
                np.full(n_measurement_deviations, sensor_miss_penalty)],
        integrality=np.r_[np.ones(n), np.zeros(2 * n + n_measurement_deviations)],
        bounds=Bounds(np.r_[np.maximum(0.0, base - 20.0),
                            np.zeros(2 * n + n_measurement_deviations)],
                      np.r_[base + 20.0,
                            np.full(2 * n + n_measurement_deviations, np.inf)]),
        constraints=LinearConstraint(vstack(rows, format="csr"), lower, upper),
        options={"time_limit": 20.0},
    )
    if not result.success or result.x is None:
        return None
    repaired = np.rint(result.x[:n]).astype(int)
    out = counts.copy()
    out[active] = repaired
    return out


# solve_interval_with_relaxation's rung markers — which stage of the
# relaxation ladder actually produced a solution, so calibration reports
# can show a real convergence diagnostic (2026-07-10, found in a review:
# the ladder ran silently, with no visibility into how often quarters
# needed it) instead of only a pass/fail infeasible_intervals count.
RUNG_CLEAN        = 0   # first solve_interval_entropy call succeeded
RUNG_RELAX_TOL2X  = 1   # tol_mult=2.0, bounds kept
RUNG_RELAX_TOL4X  = 2   # tol_mult=4.0, bounds kept
RUNG_RELAX_NOBND  = 3   # tol_mult=4.0, bounds dropped
RUNG_LP_FALLBACK  = 4   # solve_interval (LP), the final rung
RUNG_INFEASIBLE   = -1  # no rung produced a solution


def solve_interval_with_relaxation(
    shapes: list[Candidate],
    targets: dict[str, float],
    bounds: dict[str, tuple[float, float]],
    priors: dict[str, tuple[float, float]],
    route_cost: np.ndarray | None = None,
    groups: list[tuple[list[int], float, float]] | None = None,
    required_groups: list[tuple[list[int], float, float]] | None = None,
    allow_structural_relaxation: bool = True,
) -> tuple[np.ndarray | None, int]:
    """Solve one interval using calibrate()'s exact relaxation ladder.

    Returns (solution, rung) — rung is one of the RUNG_* constants above,
    telling the caller WHICH stage actually produced the solution (or
    RUNG_INFEASIBLE if none did), not just whether one exists.

    ``groups`` (structure preservation, 2026-07-12) are dropped at the same
    ladder stage as bounds (RUNG_RELAX_NOBND): both are plausibility
    constraints, strictly weaker than the measured counts — a group cap
    must never be the reason an interval's real sensor counts go unserved.

    ``required_groups`` are different: they encode a route's immutable
    provenance class (for example ``arbete`` versus ``genomfart``), not an
    optional spatial-shape preference.  They remain active at every rung so
    the solver never reaches an apparently valid count fit by publishing a
    route with a fabricated purpose label.

    ``allow_structural_relaxation=False`` is used by the outer structure
    guard after it already found a solution with stronger constraints. It
    prevents an optional cap from winning only by discarding those existing
    Level-2 bounds (or by taking the LP no-bounds fallback); the caller then
    keeps its earlier, stronger solution instead."""
    required_groups = [group for group in (required_groups or []) if group[0]]
    groups = [group for group in (groups or []) if group[0]]

    def active_groups(include_structural: bool) -> list[tuple[list[int], float, float]]:
        return required_groups + (groups if include_structural else [])

    sol = solve_interval_entropy(shapes, targets, bounds, priors,
                                 route_cost=route_cost,
                                 groups=active_groups(True))
    if sol is not None:
        return sol, RUNG_CLEAN
    for rung, (tol_mult, use_bounds) in zip(
        (RUNG_RELAX_TOL2X, RUNG_RELAX_TOL4X, RUNG_RELAX_NOBND),
        ((2.0, True), (4.0, True), (4.0, False)),
    ):
        if not use_bounds and not allow_structural_relaxation:
            break
        sol = solve_interval_entropy(
            shapes, targets, bounds if use_bounds else {}, priors,
            tol_mult=tol_mult, route_cost=route_cost,
            groups=active_groups(use_bounds))
        if sol is not None:
            return sol, rung
    if not allow_structural_relaxation:
        return None, RUNG_INFEASIBLE
    # Bounds and structural caps have already been deliberately dropped at
    # RUNG_RELAX_NOBND. The LP backstop must preserve that counts-first
    # contract rather than making an otherwise feasible interval fail.
    sol = solve_interval(shapes, targets, {}, priors,
                         route_cost=route_cost,
                         groups=active_groups(False))
    return sol, (RUNG_LP_FALLBACK if sol is not None else RUNG_INFEASIBLE)


def _shape_purposes(shape: Candidate) -> set[str]:
    """Return the provenance classes represented by one route variable."""
    sources = shape.source_candidates or [shape]
    return {_purpose(source) for source in sources}


def purpose_quota_groups(
    shapes: list[Candidate], source_mix: Counter | dict[str, int], n_vehicles: int,
) -> tuple[Counter, list[tuple[list[int], float, float]]]:
    """Build exact per-purpose route-use groups for one interval.

    Candidate routes are purpose-stratified in :func:`prepare_calibration`,
    so every shape represents exactly one immutable OD/purpose provenance.
    The five resulting groups partition the route variables.  Constraining
    each group to its scaled candidate mix therefore gives the PFE a real
    purpose margin without adding an artificial route or relabelling a
    selected home/POI trip as through traffic after the solve.

    A positive target with no compatible shape is an invalid candidate pool,
    not an excuse to fabricate a label.  Raising here makes that defect
    explicit before publication.
    """
    target = _integer_mix_targets(Counter(source_mix), n_vehicles)
    categories = sorted(set(target) | {
        purpose for shape in shapes for purpose in _shape_purposes(shape)
    })
    groups: list[tuple[list[int], float, float]] = []
    for category in categories:
        members = [i for i, shape in enumerate(shapes)
                   if _shape_purposes(shape) == {category}]
        quota = int(target.get(category, 0))
        if quota > 0 and not members:
            raise ValueError(
                f"candidate pool has no route with purpose provenance {category!r}")
        if members:
            groups.append((members, float(quota), float(quota)))
    return target, groups


def purpose_mix_is_exact(
    shapes: list[Candidate],
    counts: np.ndarray,
    source_mix: Counter | dict[str, int],
) -> bool:
    """Whether a route-use vector already has its requested purpose margin.

    Purpose shares are a behavioural prior, whereas measured sensor counts
    are the calibration contract.  The caller uses this predicate to tell a
    successful exact-purpose solve apart from the counts-first fallback: the
    latter must keep every selected route's real provenance, but must not be
    forced through an impossible integer purpose reconciliation at publish
    time.
    """
    _target, groups = purpose_quota_groups(
        shapes, source_mix, int(round(float(counts.sum()))))
    return all(abs(float(counts[members].sum()) - lo) <= 0.25
               and abs(float(counts[members].sum()) - hi) <= 0.25
               for members, lo, hi in groups)


def solve_interval_with_structure_guard(
    shapes: list[Candidate],
    targets: dict[str, float],
    bounds: dict[str, tuple[float, float]],
    priors: dict[str, tuple[float, float]],
    route_cost: np.ndarray | None = None,
    structure_groups: list[tuple[str, list[int], float]] | None = None,
    purpose_mix: Counter | dict[str, int] | None = None,
) -> tuple[np.ndarray | None, int]:
    """Two-pass structure preservation around the relaxation ladder.

    Pass 1 solves with counts/bounds/priors only. Any structure group
    (near-sensor destinations, length bins, endpoint-origin distribution —
    ``(name, member_indices, cap_share)`` tuples) exceeding its cap is added
    to a small active set and re-solved with an absolute ``cap_share ×
    pass-1-total`` ceiling. A cap may shift flow into a second group, so the
    active set is checked again for a bounded number of passes. If a capped
    solve is infeasible, or would require a weaker relaxation rung than the
    existing solution, the latter is kept — an optional structure cap must
    never cost an interval its sensor fit or retained Level-2 bounds.

    This is the ONE shared guard policy: build_sumo_demand's deployed
    pipeline and validate_sim's LOSO folds both delegate here, so LOSO can
    never silently calibrate under a different constraint set than the
    system that ships (the validated-vs-shipped mismatch class this
    project has already had to fix twice)."""
    sol, rung = solve_interval_with_relaxation(
        shapes, targets, bounds, priors, route_cost=route_cost)
    purpose_groups: list[tuple[list[int], float, float]] = []
    if sol is not None and purpose_mix:
        # The count-only solution reveals the interval's vehicle total.  Use
        # that total only to scale the independently generated purpose mix;
        # the second solve then enforces the exact integer margin while still
        # fitting the same measured counts and bounds.  This is a small,
        # fixed five-group re-solve, not a purpose-specific expansion of every
        # sensor constraint.
        _target, purpose_groups = purpose_quota_groups(
            shapes, purpose_mix, int(round(float(sol.sum()))))
        purpose_sol, purpose_rung = solve_interval_with_relaxation(
            shapes, targets, bounds, priors, route_cost=route_cost,
            required_groups=purpose_groups)
        if purpose_sol is not None:
            sol, rung = purpose_sol, purpose_rung
        else:
            # Counts are observed; the purpose mix is a generated behavioural
            # prior.  Never throw away a valid sensor fit merely because an
            # exact purpose margin conflicts with it.  The writer preserves
            # the selected routes' immutable source provenance and records
            # the resulting mix deviation instead of fabricating a label.
            purpose_groups = []
    if sol is not None and structure_groups:
        total = float(sol.sum())
        # Most origin-access groups are already below their cap. Passing all
        # of them into every 15-minute IPF solve would add hundreds of
        # needless tiny reductions and make a realism guard a calibration
        # bottleneck. Activate only groups the current solution violates.
        # A capped group can shift flow into a second group, so repeat the
        # small active-set solve until no new cap is exceeded. The reference
        # total stays the first count-feasible solution, matching the original
        # two-pass cap definition and avoiding a moving target.
        active: dict[str, tuple[list[int], float]] = {}
        for _pass in range(4):
            newly_violated = [
                (name, members, cap_share)
                for name, members, cap_share in structure_groups
                if (total > 0 and name not in active
                    and float(sol[members].sum()) > cap_share * total)
            ]
            if not newly_violated:
                break
            active.update({name: (members, cap_share)
                           for name, members, cap_share in newly_violated})
            # If the unguarded solution still retained Level-2 bounds, do not
            # allow this optional cap to get accepted by dropping them. The
            # old path did exactly that: a cap could force RUNG_RELAX_NOBND,
            # then the writer quite correctly rejected the resulting bound
            # breach many minutes after the actual decision was made.
            allow_structural_relaxation = rung >= RUNG_RELAX_NOBND
            capped_sol, capped_rung = solve_interval_with_relaxation(
                shapes, targets, bounds, priors, route_cost=route_cost,
                groups=[(members, 0.0, cap_share * total)
                        for members, cap_share in active.values()],
                required_groups=purpose_groups,
                allow_structural_relaxation=allow_structural_relaxation)
            if capped_sol is None or capped_rung > rung:
                break       # counts-first fallback: retain last valid solution
            sol, rung = capped_sol, capped_rung
    return sol, rung


def _purpose(source: Candidate) -> str:
    """Stable provenance category; legacy candidates remain explicit."""
    return str(source.intent.get("purpose", "unknown"))


def _purpose_targets_per_quarter(
    shapes: list[Candidate],
    nq: int,
    departure_offset_s: float = 0.0,
) -> list[Counter]:
    """Candidate purpose mix at each calibrated departure quarter.

    PFE intentionally shares route geometry across time to keep the solve
    small. Its selected vehicles must still inherit a purpose distribution
    compatible with the candidate generator's purpose x time x day-type
    demand. Candidate XML departures are absolute seconds from the start of
    the generated behavioural day, while a PFE target window may start later
    (for example 06:00). ``departure_offset_s`` maps that absolute candidate
    clock onto target quarter zero. Without it, a 06:00–10:00 build silently
    used the 00:00–04:00 purpose mix, which produced implausible all-through
    or all-work quarters despite an otherwise correct sensor fit.
    """
    targets = [Counter() for _ in range(nq)]
    for shape in shapes:
        for source in shape.source_candidates or [shape]:
            qi = int((source.depart - departure_offset_s) // 900)
            if 0 <= qi < nq:
                targets[qi][_purpose(source)] += 1
    return targets


def purpose_length_means(shapes: list[Candidate],
                         shape_km: list[float]) -> dict[str, float]:
    """Mean route length per purpose over ALL source candidates.

    This is the generation-side P(length | purpose) signal (fritid long,
    arbete short — RVU Tabell 3 via PURPOSE_LENGTH_SCALE) that a purpose-
    blind count calibration cannot see: PFE picks whichever geometry closes
    the bands, so the purposes stamped on its selection must be steered
    back toward each purpose's own length profile."""
    tot: Counter = Counter()
    weighted: dict[str, float] = {}
    for shape, km in zip(shapes, shape_km):
        for source in shape.source_candidates or [shape]:
            p = _purpose(source)
            tot[p] += 1
            weighted[p] = weighted.get(p, 0.0) + km
    return {p: weighted[p] / tot[p] for p in tot}


def _integer_mix_targets(source_mix: Counter, n: int) -> Counter:
    """Scale a candidate category mix to n vehicles by largest remainder."""
    if n <= 0 or not source_mix:
        return Counter()
    total = sum(source_mix.values())
    raw = {category: n * count / total for category, count in source_mix.items()}
    out = Counter({category: int(np.floor(value)) for category, value in raw.items()})
    for category, _value in sorted(raw.items(), key=lambda item: (
            -(item[1] - np.floor(item[1])), item[0]))[:n - sum(out.values())]:
        out[category] += 1
    return out


def apply_category_margin(
    source_mixes: list[Counter],
    category_shares_by_quarter: list[dict[str, float]] | None,
) -> list[Counter]:
    """Replace one conditional category margin without changing other classes.

    Candidate-route validity filters can reject categories at different rates.
    A caller with an independently specified conditional category model can
    restore that model here while retaining the surviving count of the chosen
    categories and every other category in the source mix. The latter is
    important when no external evidence justifies changing through/external
    traffic or legacy categories.
    """
    if category_shares_by_quarter is None:
        return [Counter(mix) for mix in source_mixes]
    if len(category_shares_by_quarter) != len(source_mixes):
        raise ValueError("category shares must cover every PFE quarter")

    corrected: list[Counter] = []
    for quarter, (source, raw_shares) in enumerate(
            zip(source_mixes, category_shares_by_quarter)):
        if not isinstance(raw_shares, dict) or not raw_shares:
            raise ValueError(f"invalid category shares for quarter {quarter}")
        shares = {str(category): float(value)
                  for category, value in raw_shares.items()}
        mass = sum(shares.values())
        if (not math.isfinite(mass) or mass <= 0
                or any(not math.isfinite(value) or value < 0
                       for value in shares.values())):
            raise ValueError(f"invalid category shares for quarter {quarter}")

        category_total = sum(int(source.get(category, 0)) for category in shares)
        if category_total <= 0:
            corrected.append(Counter(source))
            continue
        out = Counter({category: count for category, count in source.items()
                       if category not in shares})
        out.update(_integer_mix_targets(Counter(shares), category_total))
        corrected.append(out)
    return corrected


def apply_through_share_target(
    source_mixes: list[Counter],
    theta: float | None,
) -> list[Counter]:
    """Pin each quarter's through share to an externally chosen level.

    WHY (2026-07-17/18, IMPROVEMENT_PLAN register C9): the calibrated
    through share is UNIDENTIFIABLE from the sensor counts (measured:
    GEH<5 stayed 100.0% while the share moved freely) and is otherwise an
    emergent artifact of candidate filtering (~59%), far above every
    measured city-cordon reference (9-29%).  A swept, enforced level was
    judged by held-out sensors instead: θ=0.25 improved LOSO median
    1.71→0.994 and typical error 1.82×→1.555× on 2025-09-16 and replicated
    on the never-used-for-selection 2025-09-17 (1.63→1.111,
    1.79×→1.545×).
    The level is therefore PRIOR-ANCHORED (measured external range) +
    HELD-OUT-SELECTED + SECOND-DAY-CONFIRMED — never a measured
    Gothenburg value; a cordon count remains the decisive evidence.

    Mechanics: through gets θ×total, external keeps its absolute count,
    activity categories share the remainder in their existing relative
    (hour-of-day) mix.  A quarter with no activity support keeps its
    source mix — fabricating quota for categories the pool cannot serve
    would make :func:`purpose_quota_groups` raise.  ``theta`` None/<=0
    disables the target (the emergent behaviour, kept for comparison
    builds)."""
    if theta is None or theta <= 0:
        return [Counter(mix) for mix in source_mixes]
    if not math.isfinite(theta) or theta >= 1:
        raise ValueError("through share target must be within (0, 1)")
    out: list[Counter] = []
    for mix in source_mixes:
        tot = float(sum(mix.values()))
        acts = {k: float(v) for k, v in mix.items()
                if k not in ("through", "external")}
        a_tot = sum(acts.values())
        if tot <= 0 or a_tot <= 0:
            out.append(Counter(mix))
            continue
        ext = float(mix.get("external", 0))
        act_target = max(tot - theta * tot - ext, 0.0)
        new = Counter({"through": theta * tot})
        if ext:
            new["external"] = ext
        for k, v in acts.items():
            new[k] = v / a_tot * act_target
        out.append(new)
    return out


def purpose_replacement_index(
    shapes: list[Candidate], measured_edges: set[str],
) -> dict[tuple[str, ...], dict[str, list[tuple[Candidate, Candidate]]]]:
    """Index alternatives that preserve every constraint-protected edge.

    PFE is purpose-blind by design, so a selected route shape can have no
    source candidate for a purpose required in that departure quarter.  An
    alternative with the same protected-edge signature keeps every count that
    the final integer gate relies on unchanged while allowing its
    unconstrained origin/destination leg to carry the correct purpose
    provenance. ``measured_edges`` retains its historical parameter name for
    callers, but receives the union of measured and hard-bounded edges from
    ``write_calibration_report``. Only candidates already present in the
    generated pool are eligible; this never invents a route.
    """
    index: dict[tuple[str, ...], dict[str, list[tuple[Candidate, Candidate]]]] = {}
    if not measured_edges:
        return index
    for shape in shapes:
        signature = tuple(sorted(edge for edge in shape.edges
                                 if edge in measured_edges))
        if not signature:
            continue
        sources = shape.source_candidates or [shape]
        for source in sources:
            purpose = _purpose(source)
            choices = index.setdefault(signature, {}).setdefault(purpose, [])
            if not any(existing[0].edges == shape.edges for existing in choices):
                choices.append((shape, source))
    return index


def allocate_interval_provenance(
    route_instances: list[Candidate], source_mix: Counter,
    lengths_km: list[float] | None = None,
    category_length_km: dict[str, float] | None = None,
    *,
    purpose_replacements: dict[tuple[str, ...],
                               dict[str, list[tuple[Candidate, Candidate]]]] | None = None,
    measured_edges: set[str] | None = None,
) -> tuple[list[Candidate], list[str], dict]:
    """Allocate the exact quarter mix and retain compatible provenance where possible.

    A selected shape can be repeated by PFE, but it may only receive a purpose
    that occurred among its own source candidates. This is a small post-solve
    matching problem (O(vehicles x categories)), deliberately outside the PFE
    so calibration performance and route-count feasibility do not change.

    LENGTH-AWARE MODE (2026-07-13, when lengths_km + category_length_km are
    given): compatibility alone measurably INVERTED P(length | purpose) —
    PFE's purpose-blind selection loads the short tail of fritid-provenance
    geometry, and since fritid's quarter target exceeds its compatible
    supply, compatibility-first assignment stamped fritid on 1.4 km-median
    routes (pool: 3.1 km). Fix: instances are assigned LONGEST-FIRST to the
    remaining category with the greatest mean candidate length, preferring
    compatible categories per instance, falling back across compatibility
    (disclosed as before) only when no compatible category still needs
    vehicles. Quarter purpose counts stay exact; routes are untouched.
    """
    if not route_instances:
        return [], [], {"target": {}, "achieved": {}, "incompatible": {},
                         "replaced_routes": 0}
    pools = [shape.source_candidates or [shape] for shape in route_instances]
    available = [{_purpose(source) for source in pool} for pool in pools]
    target = _integer_mix_targets(source_mix, len(route_instances))
    if not target:
        # A sparse candidate pool can have no original departures in a
        # calibrated quarter. Retain compatible provenance rather than
        # inventing a time-specific target that the input did not provide.
        selected = [pool[i % len(pool)] for i, pool in enumerate(pools)]
        categories = [_purpose(source) for source in selected]
        return selected, categories, {
            "target": {}, "achieved": dict(sorted(Counter(categories).items())),
            "incompatible": {}, "replaced_routes": 0,
        }
    assigned: list[str | None] = [None] * len(route_instances)
    pool_share = [
        {c: sum(_purpose(s) == c for s in pool) / len(pool) for c in choices}
        for pool, choices in zip(pools, available)]

    if lengths_km is not None and category_length_km is not None:
        # Length-aware greedy (see docstring): longest instances feed the
        # longest-mean categories, so each purpose's calibrated length
        # profile tracks its own candidates' profile.
        remaining = {c: k for c, k in target.items() if k > 0}
        order = sorted(range(len(route_instances)),
                       key=lambda i: (-lengths_km[i],
                                      " ".join(route_instances[i].edges), i))
        for i in order:
            open_cats = sorted(remaining)
            compat = [c for c in open_cats if c in available[i]]
            pick_from = compat or open_cats
            category = max(pick_from, key=lambda c: (
                category_length_km.get(c, 0.0), pool_share[i].get(c, 0.0), c))
            assigned[i] = category
            remaining[category] -= 1
            if not remaining[category]:
                del remaining[category]
    else:
        # Legacy compatibility-first path (callers without geometry).
        # Give categories with few compatible selected routes first claim.
        for category in sorted(target, key=lambda p: (
                sum(p in choices for choices in available), p)):
            compatible = [i for i, choices in enumerate(available)
                          if assigned[i] is None and category in choices]
            compatible.sort(key=lambda i: (-pool_share[i][category],
                                           len(available[i]),
                                           " ".join(route_instances[i].edges), i))
            for i in compatible[:target[category]]:
                assigned[i] = category

        # Preserve the target mix exactly. A purpose is an inferred
        # behavioural attribute, not a SUMO route constraint: when PFE
        # selected no shape with matching source provenance, keep the real
        # selected route and disclose the incompatibility instead of
        # silently changing the quarter's purpose mix.
        achieved = Counter(c for c in assigned if c is not None)
        for i, choices in enumerate(available):
            if assigned[i] is not None:
                continue
            category = max(sorted(target),
                           key=lambda p: (target[p] - achieved[p], p))
            assigned[i] = category
            achieved[category] += 1

    # Rotate only within the selected purpose, retaining OD/tour provenance
    # that genuinely belongs to the exact selected route shape.
    source_offsets = Counter()
    selected = []
    incompatible = Counter()
    replaced = 0
    for i, (pool, category) in enumerate(zip(pools, assigned)):
        matching = [source for source in pool if _purpose(source) == category]
        if matching:
            selected.append(matching[source_offsets[category] % len(matching)])
        else:
            replacement = None
            if purpose_replacements and measured_edges:
                signature = tuple(sorted(edge for edge in route_instances[i].edges
                                         if edge in measured_edges))
                choices = purpose_replacements.get(signature, {}).get(category, [])
                if choices:
                    replacement = choices[source_offsets[("replacement", category)]
                                          % len(choices)]
            if replacement is not None:
                replacement_shape, replacement_source = replacement
                # The alternative has the same measured-edge signature, so
                # changing this route cannot change any calibrated sensor
                # count.  The output writer consumes the mutated instance.
                route_instances[i] = replacement_shape
                selected.append(replacement_source)
                source_offsets[("replacement", category)] += 1
                replaced += 1
            else:
                selected.append(pool[source_offsets["fallback"] % len(pool)])
                source_offsets["fallback"] += 1
                incompatible[category] += 1
        source_offsets[category] += 1
    achieved = Counter(assigned)
    return selected, [str(category) for category in assigned], {
        "target": dict(sorted(target.items())),
        "achieved": dict(sorted(achieved.items())),
        "incompatible": dict(sorted(incompatible.items())),
        "replaced_routes": replaced,
    }


def allocate_strict_interval_provenance(
    route_instances: list[Candidate], source_mix: Counter | dict[str, int],
) -> tuple[list[Candidate], list[str], dict]:
    """Retain immutable candidate provenance after purpose-stratified PFE.

    The production path uses this after purpose-stratified calibration. It
    never rewrites a route, never changes an OD endpoint, and never uses a
    mismatching source merely to make a displayed label look balanced.  An
    exact purpose margin is preferred but can be infeasible beside the hard
    sensor counts; in that counts-first fallback, ``mix_deviation`` records
    the honest difference from the generated prior while route compatibility
    remains exact.
    """
    target = _integer_mix_targets(Counter(source_mix), len(route_instances))
    offsets: Counter = Counter()
    selected: list[Candidate] = []
    purposes: list[str] = []
    for shape in route_instances:
        categories = _shape_purposes(shape)
        if len(categories) != 1:
            raise RuntimeError(
                "strict provenance requires purpose-stratified route shapes")
        category = next(iter(categories))
        matching = [source for source in (shape.source_candidates or [shape])
                    if _purpose(source) == category]
        if not matching:
            raise RuntimeError(
                f"route shape has no source for its provenance {category!r}")
        selected.append(matching[offsets[category] % len(matching)])
        offsets[category] += 1
        purposes.append(category)

    achieved = Counter(purposes)
    mismatch = {
        category: int(target.get(category, 0) - achieved.get(category, 0))
        for category in sorted(set(target) | set(achieved))
        if target.get(category, 0) != achieved.get(category, 0)
    }
    return selected, purposes, {
        "target": dict(sorted(target.items())),
        "achieved": dict(sorted(achieved.items())),
        # Kept separately: a mix deviation is not a route/provenance error.
        # Every route above was selected from a matching-purpose source.
        "incompatible": {},
        "mix_deviation": mismatch,
        "mix_exact": not mismatch,
        "replaced_routes": 0,
    }


def prepare_calibration(candidates_path: Path) -> tuple[list[Candidate], np.ndarray]:
    """Load candidates once and build the shared purpose-stratified pool.

    A route's geometry may legitimately occur in more than one generated
    purpose class.  Geometry-only deduplication used to merge those sources
    into one PFE variable, then a later allocation step could stamp that
    variable with whichever category the quarter happened to need.  Keeping
    one variable per ``(geometry, provenance purpose)`` is only a tiny pool
    expansion on the real build (29 mixed geometries) but makes the selected
    route's purpose an invariant rather than a post-hoc label.
    """
    cands = load_candidates(candidates_path)

    # Dedupe to distinct geometry × purpose shapes — the LP/IPF variables.
    seen: dict[tuple[str, str], Candidate] = {}
    for cand in cands:
        key = (" ".join(cand.edges), _purpose(cand))
        if key in seen:
            seen[key].source_candidates.append(cand)
        else:
            cand.source_candidates.append(cand)
            seen[key] = cand
    shapes = list(seen.values())
    print(f"  shape pool: {len(shapes)} distinct route×purpose variables "
          f"(from {len(cands)} candidates)")
    # Warm the compiled IPF kernel in THIS (parent) process: the flat
    # worker pools fork after prepare_calibration, so a warmed kernel is
    # inherited by every worker instead of being JIT-compiled once per
    # worker (numba's cache=True softens but does not fully remove that
    # first-run cost).
    if _KERNEL_ENABLED:
        pfe_kernel.warmup()
        print("  PFE IPF backend: numba (compiled)")
    else:
        print("  WARNING: PFE IPF backend: pure Python "
              "(install requirements or unset PFE_PURE=1 for normal speed)")
    return shapes, path_size_weights(shapes)


def solve_calibration_intervals(
    shapes: list[Candidate],
    route_cost: np.ndarray,
    targets_per_q: list[dict[str, float]],
    bounds_per_q: list[dict[str, tuple[float, float]]],
    priors_per_q: list[dict[str, tuple[float, float]]],
    purpose_mixes_per_q: list[Counter | dict[str, int]] | None = None,
) -> tuple[list[np.ndarray | None], list[int]]:
    """Sequentially solve every interval for one variant.

    Returns (solutions, rungs) — rungs are the RUNG_* constant each
    interval actually converged at, for write_calibration_report's
    relaxation_summary diagnostic."""
    solutions: list[np.ndarray | None] = []
    rungs: list[int] = []
    for i in range(len(targets_per_q)):
        sol, rung = solve_interval_with_relaxation(
            shapes, targets_per_q[i], bounds_per_q[i], priors_per_q[i],
            route_cost=route_cost)
        if (sol is not None and purpose_mixes_per_q is not None
                and purpose_mixes_per_q[i]):
            # The flat production worker uses the same two-stage procedure:
            # first establish how many vehicles are needed for the sensor
            # constraints, then enforce that interval's immutable
            # route-provenance margin.
            _target, purpose_groups = purpose_quota_groups(
                shapes, purpose_mixes_per_q[i], int(round(float(sol.sum()))))
            purpose_sol, purpose_rung = solve_interval_with_relaxation(
                shapes, targets_per_q[i], bounds_per_q[i], priors_per_q[i],
                route_cost=route_cost, required_groups=purpose_groups)
            if purpose_sol is not None:
                sol, rung = purpose_sol, purpose_rung
        solutions.append(sol)
        rungs.append(rung)
    return solutions, rungs


def _draw_endpoint_location(source: Candidate, side: str, ordinal: int) -> dict | None:
    """Draw a deterministic, low-discrepancy anonymous endpoint location.

    PFE may use one candidate shape many times.  Reusing the exact sampled
    building/POI in every copy would turn a legitimate aggregate flow into a
    visible convoy at one junction.  The candidate sidecar instead supplies a
    weighted pool on the same physical access edge; a golden-ratio sequence
    spreads repeated copies over that pool in proportion to capacity, without
    touching route choice, counts, or simulation speed.
    """
    pool_key = source.intent.get(f"{side}_location_pool")
    if not isinstance(pool_key, str):
        return None
    raw_pool = source.location_pools.get(pool_key, [])
    if not isinstance(raw_pool, list):
        return None
    pool = []
    for item in raw_pool:
        try:
            position = float(item.get("p"))
            weight = float(item.get("w", 1.0))
        except (AttributeError, TypeError, ValueError):
            continue
        if math.isfinite(position) and math.isfinite(weight) and weight > 0:
            pool.append((item, position, weight))
    if not pool:
        return None
    total = sum(weight for _item, _position, weight in pool)
    # Candidate ID chooses a reproducible phase.  The ordinal then advances
    # by an irrational stride, which covers a small pool evenly instead of
    # hash-with-replacement repeatedly landing on the same building.
    source_key = source.source_id or " ".join(source.edges)
    digest = hashlib.sha1(f"{source_key}:{side}".encode()).digest()
    phase = int.from_bytes(digest[:8], "big") / 2**64
    unit = (phase + ordinal * 0.6180339887498949) % 1.0
    target = unit * total
    cumulative = 0.0
    for item, position, weight in pool:
        cumulative += weight
        if target <= cumulative:
            return {"id": str(item.get("id", pool_key)),
                    "kind": str(item.get("kind", side)),
                    "position_m": round(position, 2), "pool": pool_key}
    item, position, _weight = pool[-1]
    return {"id": str(item.get("id", pool_key)),
            "kind": str(item.get("kind", side)),
            "position_m": round(position, 2), "pool": pool_key}


RUNG_NAMES = {
    RUNG_CLEAN: "clean", RUNG_RELAX_TOL2X: "relax_tol2x",
    RUNG_RELAX_TOL4X: "relax_tol4x", RUNG_RELAX_NOBND: "relax_no_bounds",
    RUNG_LP_FALLBACK: "lp_fallback", RUNG_INFEASIBLE: "infeasible",
}


def _rung_measurement_tol_mult(rung: int | None) -> float:
    """Return the Level-1 tolerance multiplier used by a solved interval."""
    return {
        RUNG_CLEAN: 1.0,
        RUNG_RELAX_TOL2X: 2.0,
        RUNG_RELAX_TOL4X: 4.0,
        RUNG_RELAX_NOBND: 4.0,
        # solve_interval() is invoked with its default tolerance on the LP
        # backstop, after Level-2 bounds have already been dropped.
        RUNG_LP_FALLBACK: 1.0,
    }.get(rung, 1.0)


def _rung_keeps_structural_bounds(rung: int | None) -> bool:
    """Whether this rung still solved with its supplied Level-2 bounds."""
    return rung not in (RUNG_RELAX_NOBND, RUNG_LP_FALLBACK, RUNG_INFEASIBLE)


def write_calibration_report(
    shapes: list[Candidate],
    out_path: Path,
    targets_per_q: list[dict[str, float]],
    solutions: list[np.ndarray | None],
    bounds_per_q: list[dict[str, tuple[float, float]]] | None = None,
    rungs: list[int] | None = None,
    enforce_integer_bounds: bool = False,
    structure_groups: list[tuple[list[int], float]] | None = None,
    edge_length_m: dict[str, float] | None = None,
    purpose_mixes_per_q: list[Counter | dict[str, int]] | None = None,
) -> dict:
    """Write .rou.xml and compute the same fit report calibrate() returns.

    bounds_per_q is optional. The continuous
    LP/entropy solution respects bounds by construction, but
    round_preserving_measured()'s integer ±1 nudges (needed to hit a
    measured edge's EXACT target) have no visibility into bounds at all —
    a route shared between a measured edge and a separately-bounded edge
    can be nudged in a way that pushes the bounded edge's rounded total
    outside its own [lower, upper].

    The repair itself is gated on ``enforce_integer_bounds``, not merely on
    ``bounds_per_q is not None``: a caller passing bounds for DIAGNOSTIC
    reporting only (enforce_integer_bounds=False — validate_sim.py's LOSO
    fold calibration, which explicitly opts out of enforcement because
    those bounds are wide assignment-prior bounds, not the narrow
    structural bounds build_sumo_demand.py enforces) must get back
    exactly the pre-repair counts it always has, not silently-repaired
    ones — found in review 2026-07-12: the repair used to fire whenever
    bounds were merely supplied, which would have altered LOSO's published
    route counts (and therefore its GEH/recovery-ratio numbers) without
    validate_sim.py ever asking for that. When enforce_integer_bounds=True
    (build_sumo_demand.py's real deployment calls), the writer DOES run a
    small constrained integer repair before reporting a violation, and
    remains a publication gate: route XML is written to a sibling temporary
    path and atomically published only when the repaired integer counts
    respect every bound retained by that interval's solver rung. Bounds
    deliberately dropped by the counts-first relaxation ladder are reported
    separately as structural-relaxation diagnostics, not mislabeled as a
    rounding failure."""
    nq = len(targets_per_q)
    infeasible = sum(sol is None for sol in solutions)
    if enforce_integer_bounds and infeasible:
        # Do this before opening the staging route file. A partially solved
        # interval set is not a scenario and must never overwrite the last
        # valid demand artefact merely because the remaining intervals could
        # be rendered to XML.
        raise RuntimeError(
            f"PFE left {infeasible} infeasible interval(s); no route file was published")
    achieved: dict[str, list[float]] = {}
    purpose_targets = (purpose_mixes_per_q if purpose_mixes_per_q is not None
                       else _purpose_targets_per_quarter(shapes, nq))
    # Length-aware purpose allocation (see allocate_interval_provenance):
    # needs each shape's route length and each purpose's candidate mean.
    shape_km: list[float] | None = None
    category_length_km: dict[str, float] | None = None
    shape_km_by_id: dict[int, float] = {}
    if edge_length_m is not None:
        shape_km = [sum(edge_length_m.get(e, 0.0) for e in s.edges) / 1000.0
                    for s in shapes]
        category_length_km = purpose_length_means(shapes, shape_km)
        shape_km_by_id = {id(s): km for s, km in zip(shapes, shape_km)}
    measured_edges = {edge for targets in targets_per_q for edge in targets}
    # Purpose provenance may replace a selected route after integer repair.
    # Preserve not only the measured signature but every edge subject to a
    # hard bound; otherwise the pre-replacement ``achieved`` accounting can
    # pass while the route XML written to SUMO violates an unmeasured bound.
    protected_edges = set(measured_edges)
    if bounds_per_q is not None:
        protected_edges.update(
            edge for bounds in bounds_per_q for edge in bounds
        )
    replacement_index = purpose_replacement_index(shapes, protected_edges)
    purpose_allocation: list[dict] = []
    vid = 0
    agents: list[dict] = []
    # Counts are deliberately shared across every quarter: a candidate used
    # repeatedly at 08:00 and again at 17:00 should keep traversing its
    # weighted endpoint pool instead of restarting the same sequence.
    location_draw_ordinals: Counter = Counter()
    write_path = (out_path.with_suffix(out_path.suffix + ".tmp")
                  if enforce_integer_bounds else out_path)
    with open(write_path, "w") as f:
        f.write("<routes>\n")
        for i in range(nq):
            sol = solutions[i]
            if sol is None:
                continue
            counts = round_preserving_measured(sol, shapes, targets_per_q[i])
            purpose_mix = purpose_targets[i] if i < len(purpose_targets) else Counter()
            strict_purpose = purpose_mixes_per_q is not None and bool(purpose_mix)
            purpose_groups: list[tuple[list[int], float, float]] = []
            # Only enforce an exact margin during integer repair when the
            # continuous solver actually found one.  Otherwise this is the
            # deliberate counts-first fallback: the routes keep their true
            # source purposes and the resulting prior deviation is disclosed
            # below, instead of rejecting a valid measured-count solution.
            purpose_margin_enforced = (
                strict_purpose and purpose_mix_is_exact(shapes, sol, purpose_mix))
            if purpose_margin_enforced:
                _purpose_target, purpose_groups = purpose_quota_groups(
                    shapes, purpose_mix, int(counts.sum()))
            requested_purpose_groups = list(purpose_groups)
            rung = rungs[i] if rungs is not None and i < len(rungs) else None
            repair_bounds = (
                bounds_per_q[i]
                if (bounds_per_q is not None and enforce_integer_bounds
                    and _rung_keeps_structural_bounds(rung))
                else {}
            )
            # structure_groups (2026-07-12, structure preservation): each
            # group cap needs an ABSOLUTE per-quarter ceiling, only known
            # once the quarter's integer total exists. Floor of 2 vehicles
            # keeps tiny (night) quarters integer-feasible — a 20-vehicle
            # quarter at a 7% cap cannot meaningfully hold "1.4 vehicles".
            # The repair is best-effort by design: a structure cap must never
            # block a repair that is needed for a sensor count or a level-2
            # bound.  Keep the last vector that satisfies those stronger
            # constraints if a later optional-cap repair cannot coexist with
            # them; calibrated_structure_report exposes the residual.
            def overflowing_groups(current: np.ndarray) -> list[tuple[list[int], float, float]]:
                if not structure_groups:
                    return []
                q_total = float(current.sum())
                # Exactly mirrors the solver's absolute cap/floor, but only
                # forwards groups which need an integer repair. Origin-edge
                # groups can number in the hundreds; adding every satisfied
                # one to the MILP would make publication slower for no gain.
                return [
                    (members, 0.0, max(2.0, cap_share * q_total))
                    for members, cap_share in structure_groups
                    if float(current[members].sum()) > max(2.0, cap_share * q_total)
                ]

            def apply_structure_repairs(
                current: np.ndarray,
                active_purpose_groups: list[tuple[list[int], float, float]],
            ) -> np.ndarray:
                """Best-effort structure repair from the current warm start."""
                for _repair_pass in range(3):
                    quarter_groups = overflowing_groups(current)
                    if not quarter_groups:
                        break
                    repaired_structure = repair_integer_bounds(
                        current, shapes, targets_per_q[i], repair_bounds,
                        groups=active_purpose_groups + quarter_groups)
                    if repaired_structure is None:
                        break
                    current = repaired_structure
                return current

            # First repair the non-negotiable constraints on their own.  An
            # earlier implementation mixed optional structure groups into
            # this MILP.  If a cap could not coexist with a valid hard-bound
            # repair, the combined problem reported infeasible and left the
            # actual bound violation in place.  That is backwards: groups
            # are explicitly counts-first best-effort constraints.
            hard_repair_ok = True
            if repair_bounds or purpose_groups:
                repaired = repair_integer_bounds(
                    counts, shapes, targets_per_q[i], repair_bounds,
                    groups=purpose_groups,
                    measurement_tol_mult=_rung_measurement_tol_mult(rung))
                if repaired is None:
                    hard_repair_ok = False
                else:
                    counts = repaired
            if not hard_repair_ok and purpose_groups:
                # A joint repair starts from the directly rounded entropy
                # solution.  On a large, dispersed route pool that vector can
                # be a poor MILP neighbourhood even when an exact integer
                # purpose margin exists (the live 2025 baseline exposed four
                # such quarters).  First establish a count/bound-valid integer
                # vector, then retry the same exact purpose groups from that
                # much better starting point.  This is not a relaxed model:
                # both passes retain every non-negotiable constraint.
                exact_purpose_groups = purpose_groups
                repaired = repair_integer_bounds(
                    counts, shapes, targets_per_q[i], repair_bounds,
                    measurement_tol_mult=_rung_measurement_tol_mult(rung))
                hard_repair_ok = repaired is not None
                if repaired is not None:
                    counts = repaired
                    purpose_repaired = repair_integer_bounds(
                        counts, shapes, targets_per_q[i], repair_bounds,
                        groups=exact_purpose_groups,
                        measurement_tol_mult=_rung_measurement_tol_mult(rung))
                    if purpose_repaired is not None:
                        counts = purpose_repaired
                    else:
                        purpose_groups = []
                        purpose_margin_enforced = False
                else:
                    purpose_groups = []
                    purpose_margin_enforced = False

            # Then improve only overflowing groups.  Keep repair_bounds in
            # the optional pass so moving a vehicle to satisfy a cap cannot
            # re-break a hard edge, but retain the already hard-valid vector
            # when the optional cap is infeasible.  A repaired group may push
            # another group over its cap, hence the bounded active-set loop.
            if hard_repair_ok:
                counts = apply_structure_repairs(counts, purpose_groups)

            # The optional structure pass can itself provide the feasible
            # integer neighbourhood the exact purpose repair needed.  The
            # audited baseline did exactly that: all four relaxed quarters
            # became purpose-feasible when retried from their finally
            # published count/structure-valid vectors.  Make that last
            # opportunity explicit before declaring a real mix relaxation.
            if (hard_repair_ok and requested_purpose_groups
                    and not purpose_margin_enforced):
                _purpose_target, final_purpose_groups = purpose_quota_groups(
                    shapes, purpose_mix, int(counts.sum()))
                purpose_repaired = repair_integer_bounds(
                    counts, shapes, targets_per_q[i], repair_bounds,
                    groups=final_purpose_groups,
                    measurement_tol_mult=_rung_measurement_tol_mult(rung))
                if purpose_repaired is not None:
                    counts = purpose_repaired
                    purpose_groups = final_purpose_groups
                    purpose_margin_enforced = True
                    # Purpose reconciliation can move routes back into an
                    # optional structure group. Reapply the same best-effort
                    # guard while retaining the now-exact purpose groups.
                    counts = apply_structure_repairs(counts, purpose_groups)
            # Spread ALL vehicles in this quarter across its full 15-minute
            # interval. The old per-route schedule put every one-vehicle
            # route at exactly :07:30, so thousands of independent routes
            # entered SUMO as a visible convoy. Counts and route choices stay
            # unchanged; only departure time is stratified globally.
            # Hash order is deterministic and avoids grouping equal routes
            # together, while positions remain uniformly spaced. The dup
            # index MUST be in the key: shapes are unique by edge string
            # (prepare_calibration dedups on it), so without it all k
            # copies of a shape share one key and the stable sort parks
            # them in consecutive departure slots — an identical-route
            # platoon, the very artifact this ordering exists to prevent.
            keyed: list[tuple[bytes, Candidate]] = []
            for cand, k in zip(shapes, counts):
                edges_str = " ".join(cand.edges)
                keyed.extend(
                    (hashlib.sha1(f"{i}:{edges_str}:{dup}".encode()).digest(),
                     cand)
                    for dup in range(int(k)))
            keyed.sort(key=lambda item: item[0])
            route_instances: list[Candidate] = [c for _digest, c in keyed]
            instance_km = ([shape_km_by_id[id(c)] for c in route_instances]
                           if edge_length_m is not None else None)
            if strict_purpose:
                sources, purposes, allocation = allocate_strict_interval_provenance(
                    route_instances, purpose_mix)
                allocation["margin_enforced"] = purpose_margin_enforced
            else:
                sources, purposes, allocation = allocate_interval_provenance(
                    route_instances, purpose_targets[i],
                    lengths_km=instance_km,
                    category_length_km=category_length_km,
                    purpose_replacements=replacement_index,
                    measured_edges=protected_edges)
            purpose_allocation.append({"quarter": i, **allocation})
            # ``allocate_interval_provenance`` can substitute a compatible
            # route shape. Count the FINAL route instances, not the shapes
            # chosen by the integer solver, so every report/GEH/bound check
            # proves the exact XML published below.
            for cand in route_instances:
                for edge in set(cand.edges):
                    achieved.setdefault(edge, [0.0] * nq)
                    achieved[edge][i] += 1.0
            n_departures = len(route_instances)
            for pos, (cand, source, purpose) in enumerate(
                    zip(route_instances, sources, purposes)):
                depart = i * 900 + (pos + 0.5) * 900 / max(1, n_departures)
                vehicle_id = f"pfe{vid}"
                edges_str = " ".join(cand.edges)
                source_key = source.source_id or edges_str
                origin_stream = (source_key, "origin")
                destination_stream = (source_key, "destination")
                origin_location = _draw_endpoint_location(
                    source, "origin", location_draw_ordinals[origin_stream])
                destination_location = _draw_endpoint_location(
                    source, "destination", location_draw_ordinals[destination_stream])
                location_draw_ordinals[origin_stream] += 1
                location_draw_ordinals[destination_stream] += 1
                endpoint_attrs = ""
                if origin_location is not None:
                    endpoint_attrs += f' departPos="{origin_location["position_m"]:.2f}"'
                if destination_location is not None:
                    endpoint_attrs += f' arrivalPos="{destination_location["position_m"]:.2f}"'
                f.write(f'  <vehicle id="{vehicle_id}" depart="{depart:.1f}"{endpoint_attrs}>'
                        f'<route edges="{edges_str}"/></vehicle>\n')
                agent = {
                    "vehicle_id": vehicle_id,
                    "candidate_id": source.source_id or None,
                    "purpose": purpose,
                    "purpose_route_compatible": _purpose(source) == purpose,
                    "tour_id": source.intent.get("tour_id"),
                    "leg": source.intent.get("leg"),
                    "origin_edge": source.intent.get("origin_edge", source.edges[0]),
                    "destination_edge": source.intent.get("destination_edge", source.edges[-1]),
                    "departure_s": round(depart, 1),
                }
                if origin_location is not None:
                    agent.update({
                        "origin_location_id": origin_location["id"],
                        "origin_location_kind": origin_location["kind"],
                        "origin_position_m": origin_location["position_m"],
                    })
                if destination_location is not None:
                    agent.update({
                        "destination_location_id": destination_location["id"],
                        "destination_location_kind": destination_location["kind"],
                        "destination_position_m": destination_location["position_m"],
                    })
                agents.append(agent)
                vid += 1
        f.write("</routes>\n")

    # A missing candidate is not a valid reason to quietly treat a measured
    # count as unconstrained. The solvers still omit such an impossible row
    # so other edges in the interval can be served, but expose it prominently
    # to the demand pipeline instead of letting GEH alone hide the condition.
    candidate_edges = {e for cand in shapes for e in cand.edges}
    unserviceable_edges = sorted({
        e for targets in targets_per_q for e in targets if e not in candidate_edges
    })

    bound_violations: list[dict] = []
    relaxed_bound_violations: list[dict] = []
    if bounds_per_q is not None:
        for i in range(nq):
            rung = rungs[i] if rungs is not None and i < len(rungs) else None
            for e, (lo, hi) in bounds_per_q[i].items():
                v = achieved.get(e, [0.0] * nq)[i]
                if v < lo - 0.5 or v > hi + 0.5:   # tolerate rounding, not real breaches
                    entry = {
                        "edge": e, "quarter": i, "achieved": v,
                        "bound_lo": lo, "bound_hi": hi,
                    }
                    if _rung_keeps_structural_bounds(rung):
                        bound_violations.append(entry)
                    else:
                        relaxed_bound_violations.append(entry)
    if enforce_integer_bounds and bound_violations:
        write_path.unlink(missing_ok=True)
        first = bound_violations[0]
        raise RuntimeError(
            "integer route rounding violates a hard bound; no route file was "
            f"published ({first['edge']}@q{first['quarter']}: "
            f"{first['achieved']} outside [{first['bound_lo']}, {first['bound_hi']}])"
        )
    if enforce_integer_bounds:
        os.replace(write_path, out_path)

    # Publish agent provenance only after the route file passed the same
    # validation gate. Otherwise a rejected calibration could leave a fresh
    # looking purpose/OD file beside an older valid route file.
    agent_path = out_path.with_name(out_path.name.replace(".rou.xml", ".agents.json"))
    agent_write = agent_path.with_suffix(agent_path.suffix + ".tmp")
    with open(agent_write, "w") as f:
        json.dump({"schema_version": 1, "agents": agents}, f,
                  separators=(",", ":"))
    os.replace(agent_write, agent_path)

    # GEH on hourly aggregates at measured edges — the standard fit metric.
    # An edge counts for an hour if ANY of its 4 quarters has a measurement —
    # checking only targets_per_q[i] (the hour's first quarter) would silently
    # skip the whole hour whenever just that one quarter happens to be null.
    geh_ok = geh_all = 0
    for i in range(0, nq - 3, 4):
        edges_in_hour: set[str] = set()
        for j in range(i, i + 4):
            edges_in_hour.update(targets_per_q[j])
        for e in edges_in_hour:
            m = sum(achieved.get(e, [0.0] * nq)[i:i + 4])
            c = sum(targets_per_q[j].get(e, 0.0) for j in range(i, i + 4))
            if m + c > 0:
                geh = float(np.sqrt(2 * (m - c) ** 2 / (m + c)))
                geh_all += 1
                geh_ok += geh < 5
    allocation_incompatible = Counter()
    mix_shortfall = Counter()
    mix_excess = Counter()
    mix_reallocation_vehicles = 0
    relaxed_mix_quarters = 0
    replaced_routes = 0
    for allocation in purpose_allocation:
        allocation_incompatible.update(allocation.get("incompatible", {}))
        deviation = allocation.get("mix_deviation", {})
        if deviation:
            relaxed_mix_quarters += 1
            for purpose, delta in deviation.items():
                if delta > 0:
                    mix_shortfall[purpose] += int(delta)
                elif delta < 0:
                    mix_excess[purpose] += int(-delta)
            # The positive and negative totals are equal per quarter. Count
            # one vehicle once, rather than reporting the L1 distance twice.
            mix_reallocation_vehicles += sum(abs(int(delta))
                                           for delta in deviation.values()) // 2
        replaced_routes += int(allocation.get("replaced_routes", 0))
    report = {"vehicles": vid, "infeasible_intervals": infeasible,
              "geh_ok": geh_ok, "geh_total": geh_all,
              "geh_pct": round(100 * geh_ok / max(1, geh_all), 1),
              "achieved": achieved,
              "unserviceable_edges": unserviceable_edges,
              "bound_violations": bound_violations,
              "relaxed_bound_violations": relaxed_bound_violations,
              "purpose_allocation": purpose_allocation,
              "purpose_allocation_summary": {
                  "quarters_with_incompatible_routes": sum(
                      bool(allocation["incompatible"]) for allocation in purpose_allocation),
                  "incompatible_routes_by_purpose": dict(
                      sorted(allocation_incompatible.items())),
                  "quarters_with_relaxed_mix": relaxed_mix_quarters,
                  "mix_shortfall_by_purpose": dict(sorted(mix_shortfall.items())),
                  "mix_excess_by_purpose": dict(sorted(mix_excess.items())),
                  "mix_reallocation_vehicles": mix_reallocation_vehicles,
                  "replaced_routes": replaced_routes,
              }}
    if rungs is not None:
        # Which relaxation-ladder rung each interval actually converged at —
        # a solver that's quietly living on RUNG_LP_FALLBACK every interval
        # is a different health signal than one mostly hitting RUNG_CLEAN,
        # even when both report 100% GEH<5 (found while auditing the
        # relaxation ladder, 2026-07-10).
        counts = Counter(rungs)
        report["relaxation_summary"] = {
            RUNG_NAMES[rung]: counts[rung] for rung in RUNG_NAMES if counts[rung]
        }
    return report


def calibrate(
    candidates_path: Path,
    out_path: Path,
    targets_per_q: list[dict[str, float]],          # measured, per quarter
    bounds_per_q: list[dict[str, tuple[float, float]]],
    priors_per_q: list[dict[str, tuple[float, float]]],
    enforce_integer_bounds: bool = False,
    integer_bounds_per_q: list[dict[str, tuple[float, float]]] | None = None,
    purpose_departure_offset_s: float = 0.0,
    activity_purpose_shares_by_quarter: list[dict[str, float]] | None = None,
    through_share_target: float | None = None,
) -> dict:
    """Solve all intervals; write a .rou.xml; return a fit report.

    SHARED SHAPE POOL: a route's geometry is drivable at any hour, so every
    interval solves over ALL distinct candidate shapes of the day (departure
    times are assigned when a shape is chosen). Bucketing candidates by
    their original depart time starved sparse quarters of shape diversity —
    2–3 overlapping corridor routes per sensor made the LP infeasible.

    Each interval is solved by solve_interval_entropy() (IPF/Bregman
    balancing — see its own docstring for why this replaced an LP +
    reweighting apparatus that grew out of chasing the same underlying
    issue with the wrong tool). solve_interval (the original LP) is kept
    as the RELAXATION LADDER's final rung — a battle-tested, complete
    solver as backstop, in the rare case IPF's iteration budget doesn't
    converge for some edge-case constraint combination."""
    shapes, route_cost = prepare_calibration(candidates_path)
    source_purpose_mixes = _purpose_targets_per_quarter(
        shapes, len(targets_per_q), purpose_departure_offset_s)
    purpose_mixes = apply_through_share_target(
        apply_category_margin(
            source_purpose_mixes, activity_purpose_shares_by_quarter),
        through_share_target)
    solutions, rungs = solve_calibration_intervals(
        shapes, route_cost, targets_per_q, bounds_per_q, priors_per_q,
        purpose_mixes)
    return write_calibration_report(shapes, out_path, targets_per_q, solutions,
                                    integer_bounds_per_q if integer_bounds_per_q is not None else bounds_per_q,
                                    rungs, enforce_integer_bounds,
                                    purpose_mixes_per_q=purpose_mixes)
