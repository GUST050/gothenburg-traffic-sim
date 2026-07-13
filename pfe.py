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
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import hstack, identity, lil_matrix, vstack

EPS_PARSIMONY = 1e-3
MEAS_TOL_FRAC = 0.05      # hard band around measured counts
MEAS_TOL_MIN  = 2.0


@dataclass
class Candidate:
    depart: float
    edges: list[str]
    source_id: str = ""
    intent: dict = field(default_factory=dict)
    source_candidates: list["Candidate"] = field(default_factory=list, repr=False)


def load_candidates(path: Path) -> list[Candidate]:
    meta_path = path.with_name(path.name.replace(".rou.xml", ".meta.json"))
    metadata: dict[str, dict] = {}
    if meta_path.exists():
        with open(meta_path) as f:
            metadata = json.load(f).get("candidates", {})
    out = []
    for veh in ET.parse(path).getroot().iter("vehicle"):
        route = veh.find("route")
        source_id = veh.get("id") or ""
        out.append(Candidate(depart=float(veh.get("depart")),
                             edges=route.get("edges").split(),
                             source_id=source_id,
                             intent=dict(metadata.get(source_id, {}))))
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
    # groups (2026-07-12, DESTINATION_BIAS_RESEARCH §4A step 3): a band over
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

    # Confirm convergence rather than assume it — an empty level-1/level-2
    # intersection for some edge would otherwise silently return a vector
    # that doesn't actually satisfy the hard constraints.
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
) -> np.ndarray | None:
    """Repair a rounded route vector without weakening its constraints.

    The fast measurement-preserving rounding above is the normal path.  It
    can, however, move a shared route by one vehicle while closing a measured
    count and thereby breach a separate structural edge bound.  Only in that
    case solve a small integer reconciliation problem: minimise the number of
    changed route counts while keeping every measured count at its rounded
    target and every supplied bound in its integer-feasible interval.

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
    # A ±20 local window is deliberately wider than the single-vehicle
    # reconciliation nudges and avoids a route-count explosion in the repair.
    aeq = hstack((identity(n, format="csr"), -identity(n, format="csr"),
                  identity(n, format="csr")), format="csr")
    rows = [aeq]
    lower = [float(counts[j]) for j in active]
    upper = [float(counts[j]) for j in active]

    def add_index_constraint(js: list[int], lo: float, hi: float) -> None:
        row = lil_matrix((1, 3 * n), dtype=float)
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
        # The existing rounding contract is an integer measured total, not a
        # fractional target band. Preserve that exact behaviour in repair.
        target_i = float(int(round(target)))
        add_edge_constraint(edge, target_i, target_i)
    for edge, (lo, hi) in bounds.items():
        add_edge_constraint(edge, float(np.ceil(lo - 0.5)),
                            float(np.floor(hi + 0.5)))
    for js, lo, hi in groups:
        add_index_constraint(js, float(np.ceil(lo - 0.5)),
                             float(np.floor(hi + 0.5)))

    base = np.asarray([counts[j] for j in active], dtype=float)
    result = milp(
        c=np.r_[np.zeros(n), np.ones(2 * n)],
        integrality=np.r_[np.ones(n), np.zeros(2 * n)],
        bounds=Bounds(np.r_[np.maximum(0.0, base - 20.0), np.zeros(2 * n)],
                      np.r_[base + 20.0, np.full(2 * n, np.inf)]),
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
) -> tuple[np.ndarray | None, int]:
    """Solve one interval using calibrate()'s exact relaxation ladder.

    Returns (solution, rung) — rung is one of the RUNG_* constants above,
    telling the caller WHICH stage actually produced the solution (or
    RUNG_INFEASIBLE if none did), not just whether one exists.

    ``groups`` (structure preservation, 2026-07-12) are dropped at the same
    ladder stage as bounds (RUNG_RELAX_NOBND): both are plausibility
    constraints, strictly weaker than the measured counts — a group cap
    must never be the reason an interval's real sensor counts go unserved."""
    sol = solve_interval_entropy(shapes, targets, bounds, priors,
                                 route_cost=route_cost, groups=groups)
    if sol is not None:
        return sol, RUNG_CLEAN
    for rung, (tol_mult, use_bounds) in zip(
        (RUNG_RELAX_TOL2X, RUNG_RELAX_TOL4X, RUNG_RELAX_NOBND),
        ((2.0, True), (4.0, True), (4.0, False)),
    ):
        sol = solve_interval_entropy(
            shapes, targets, bounds if use_bounds else {}, priors,
            tol_mult=tol_mult, route_cost=route_cost,
            groups=groups if use_bounds else None)
        if sol is not None:
            return sol, rung
    # Bounds and structural caps have already been deliberately dropped at
    # RUNG_RELAX_NOBND. The LP backstop must preserve that counts-first
    # contract rather than making an otherwise feasible interval fail.
    sol = solve_interval(shapes, targets, {}, priors,
                         route_cost=route_cost, groups=None)
    return sol, (RUNG_LP_FALLBACK if sol is not None else RUNG_INFEASIBLE)


def solve_interval_with_structure_guard(
    shapes: list[Candidate],
    targets: dict[str, float],
    bounds: dict[str, tuple[float, float]],
    priors: dict[str, tuple[float, float]],
    route_cost: np.ndarray | None = None,
    structure_groups: list[tuple[str, list[int], float]] | None = None,
) -> tuple[np.ndarray | None, int]:
    """Two-pass structure preservation around the relaxation ladder.

    Pass 1 solves with counts/bounds/priors only; if ANY structure group
    (near-sensor destinations, length bins — (name, member_indices,
    cap_share) tuples) exceeds its cap share of the interval's total,
    pass 2 re-solves with every group capped at cap_share × pass-1 total
    (the bands need ABSOLUTE ceilings, which only exist once a total is
    known). If pass 2 is infeasible, the pass-1 solution is kept — the
    structure caps must never cost an interval its real sensor counts.

    This is the ONE shared guard policy: build_sumo_demand's deployed
    pipeline and validate_sim's LOSO folds both delegate here, so LOSO can
    never silently calibrate under a different constraint set than the
    system that ships (the validated-vs-shipped mismatch class this
    project has already had to fix twice)."""
    sol, rung = solve_interval_with_relaxation(
        shapes, targets, bounds, priors, route_cost=route_cost)
    if sol is not None and structure_groups:
        total = float(sol.sum())
        violated = total > 0 and any(
            float(sol[members].sum()) > cap_share * total
            for _name, members, cap_share in structure_groups)
        if violated:
            capped_sol, capped_rung = solve_interval_with_relaxation(
                shapes, targets, bounds, priors, route_cost=route_cost,
                groups=[(members, 0.0, cap_share * total)
                        for _name, members, cap_share in structure_groups])
            if capped_sol is not None:
                sol, rung = capped_sol, capped_rung
    return sol, rung


def _purpose(source: Candidate) -> str:
    """Stable provenance category; legacy candidates remain explicit."""
    return str(source.intent.get("purpose", "unknown"))


def _purpose_targets_per_quarter(shapes: list[Candidate], nq: int) -> list[Counter]:
    """Candidate purpose mix at each original departure quarter.

    PFE intentionally shares route geometry across time to keep the solve
    small. Its selected vehicles must still inherit a purpose distribution
    compatible with the candidate generator's purpose x time x day-type
    demand. This captures that distribution before calibration, without
    introducing purpose-specific PFE variables.
    """
    targets = [Counter() for _ in range(nq)]
    for shape in shapes:
        for source in shape.source_candidates or [shape]:
            qi = int(source.depart // 900)
            if 0 <= qi < nq:
                targets[qi][_purpose(source)] += 1
    return targets


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


def allocate_interval_provenance(
    route_instances: list[Candidate], source_mix: Counter,
) -> tuple[list[Candidate], list[str], dict]:
    """Allocate the exact quarter mix and retain compatible provenance where possible.

    A selected shape can be repeated by PFE, but it may only receive a purpose
    that occurred among its own source candidates. Categories are allocated
    scarce-first to avoid consuming the few compatible shapes needed by a
    category. This is a small post-solve matching problem (O(vehicles x
    categories)), deliberately outside the PFE so calibration performance and
    route-count feasibility do not change.
    """
    if not route_instances:
        return [], [], {"target": {}, "achieved": {}, "incompatible": {}}
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
            "incompatible": {},
        }
    assigned: list[str | None] = [None] * len(route_instances)

    # Give categories with few compatible selected routes first claim.
    for category in sorted(target, key=lambda p: (
            sum(p in choices for choices in available), p)):
        compatible = [i for i, choices in enumerate(available)
                      if assigned[i] is None and category in choices]
        compatible.sort(key=lambda i: (len(available[i]),
                                        " ".join(route_instances[i].edges), i))
        for i in compatible[:target[category]]:
            assigned[i] = category

    # Preserve the target mix exactly. A purpose is an inferred behavioural
    # attribute, not a SUMO route constraint: when PFE selected no shape with
    # matching source provenance, keep the real selected route and disclose
    # the incompatibility instead of silently changing the quarter's purpose
    # mix. The traffic simulation itself is unchanged.
    achieved = Counter(category for category in assigned if category is not None)
    for i, choices in enumerate(available):
        if assigned[i] is not None:
            continue
        category = max(sorted(target), key=lambda p: (target[p] - achieved[p], p))
        assigned[i] = category
        achieved[category] += 1

    # Rotate only within the selected purpose, retaining OD/tour provenance
    # that genuinely belongs to the exact selected route shape.
    source_offsets = Counter()
    selected = []
    incompatible = Counter()
    for pool, category in zip(pools, assigned):
        matching = [source for source in pool if _purpose(source) == category]
        if matching:
            selected.append(matching[source_offsets[category] % len(matching)])
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
    }


def prepare_calibration(candidates_path: Path) -> tuple[list[Candidate], np.ndarray]:
    """Load candidate routes once and build the shared shape pool."""
    cands = load_candidates(candidates_path)

    # Dedupe to distinct shapes — the LP/IPF variables.
    seen: dict[str, Candidate] = {}
    for cand in cands:
        key = " ".join(cand.edges)
        if key in seen:
            seen[key].source_candidates.append(cand)
        else:
            cand.source_candidates.append(cand)
            seen[key] = cand
    shapes = list(seen.values())
    print(f"  shape pool: {len(shapes)} distinct routes "
          f"(from {len(cands)} candidates)")
    return shapes, path_size_weights(shapes)


def solve_calibration_intervals(
    shapes: list[Candidate],
    route_cost: np.ndarray,
    targets_per_q: list[dict[str, float]],
    bounds_per_q: list[dict[str, tuple[float, float]]],
    priors_per_q: list[dict[str, tuple[float, float]]],
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
        solutions.append(sol)
        rungs.append(rung)
    return solutions, rungs


RUNG_NAMES = {
    RUNG_CLEAN: "clean", RUNG_RELAX_TOL2X: "relax_tol2x",
    RUNG_RELAX_TOL4X: "relax_tol4x", RUNG_RELAX_NOBND: "relax_no_bounds",
    RUNG_LP_FALLBACK: "lp_fallback", RUNG_INFEASIBLE: "infeasible",
}


def write_calibration_report(
    shapes: list[Candidate],
    out_path: Path,
    targets_per_q: list[dict[str, float]],
    solutions: list[np.ndarray | None],
    bounds_per_q: list[dict[str, tuple[float, float]]] | None = None,
    rungs: list[int] | None = None,
    enforce_integer_bounds: bool = False,
    structure_groups: list[tuple[list[int], float]] | None = None,
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
    structural hard_bounds_pq build_sumo_demand.py enforces) must get back
    exactly the pre-repair counts it always has, not silently-repaired
    ones — found in review 2026-07-12: the repair used to fire whenever
    bounds were merely supplied, which would have altered LOSO's published
    route counts (and therefore its GEH/recovery-ratio numbers) without
    validate_sim.py ever asking for that. When enforce_integer_bounds=True
    (build_sumo_demand.py's real deployment calls), the writer DOES run a
    small constrained integer repair before reporting a violation, and
    remains a publication gate: route XML is written to a sibling
    temporary path and atomically published only when the repaired
    integer counts respect every supplied bound."""
    nq = len(targets_per_q)
    infeasible = sum(sol is None for sol in solutions)
    achieved: dict[str, list[float]] = {}
    purpose_targets = _purpose_targets_per_quarter(shapes, nq)
    purpose_allocation: list[dict] = []
    vid = 0
    agents: list[dict] = []
    write_path = (out_path.with_suffix(out_path.suffix + ".tmp")
                  if enforce_integer_bounds else out_path)
    with open(write_path, "w") as f:
        f.write("<routes>\n")
        for i in range(nq):
            sol = solutions[i]
            if sol is None:
                continue
            counts = round_preserving_measured(sol, shapes, targets_per_q[i])
            repair_bounds = (bounds_per_q[i]
                             if bounds_per_q is not None and enforce_integer_bounds
                             else {})
            # structure_groups (2026-07-12, structure preservation): each
            # group cap needs an ABSOLUTE per-quarter ceiling, only known
            # once the quarter's integer total exists. Floor of 2 vehicles
            # keeps tiny (night) quarters integer-feasible — a 20-vehicle
            # quarter at a 7% cap cannot meaningfully hold "1.4 vehicles".
            # The repair is best-effort by design: if the MILP can't
            # reconcile the caps with the measured counts, the unrepaired
            # counts stand (the counts always win; the calibrated_structure
            # guard in demand_meta.json reports whatever the residual is).
            quarter_groups = None
            if structure_groups:
                q_total = float(counts.sum())
                quarter_groups = [
                    (members, 0.0, max(2.0, cap_share * q_total))
                    for members, cap_share in structure_groups]
            if repair_bounds or quarter_groups:
                repaired = repair_integer_bounds(
                    counts, shapes, targets_per_q[i], repair_bounds,
                    groups=quarter_groups)
                if repaired is not None:
                    counts = repaired
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
                for e in set(cand.edges):
                    achieved.setdefault(e, [0.0] * nq)
                    if k:
                        achieved[e][i] += float(k)
            keyed.sort(key=lambda item: item[0])
            route_instances: list[Candidate] = [c for _digest, c in keyed]
            sources, purposes, allocation = allocate_interval_provenance(
                route_instances, purpose_targets[i])
            purpose_allocation.append({"quarter": i, **allocation})
            n_departures = len(route_instances)
            for pos, (cand, source, purpose) in enumerate(
                    zip(route_instances, sources, purposes)):
                depart = i * 900 + (pos + 0.5) * 900 / max(1, n_departures)
                vehicle_id = f"pfe{vid}"
                edges_str = " ".join(cand.edges)
                f.write(f'  <vehicle id="{vehicle_id}" depart="{depart:.1f}">'
                        f'<route edges="{edges_str}"/></vehicle>\n')
                agents.append({
                    "vehicle_id": vehicle_id,
                    "candidate_id": source.source_id or None,
                    "purpose": purpose,
                    "purpose_route_compatible": _purpose(source) == purpose,
                    "tour_id": source.intent.get("tour_id"),
                    "leg": source.intent.get("leg"),
                    "origin_edge": source.intent.get("origin_edge", source.edges[0]),
                    "destination_edge": source.intent.get("destination_edge", source.edges[-1]),
                    "departure_s": round(depart, 1),
                })
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
    if bounds_per_q is not None:
        for i in range(nq):
            for e, (lo, hi) in bounds_per_q[i].items():
                v = achieved.get(e, [0.0] * nq)[i]
                if v < lo - 0.5 or v > hi + 0.5:   # tolerate rounding, not real breaches
                    bound_violations.append({
                        "edge": e, "quarter": i, "achieved": v,
                        "bound_lo": lo, "bound_hi": hi,
                    })
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
    for allocation in purpose_allocation:
        allocation_incompatible.update(allocation["incompatible"])
    report = {"vehicles": vid, "infeasible_intervals": infeasible,
              "geh_ok": geh_ok, "geh_total": geh_all,
              "geh_pct": round(100 * geh_ok / max(1, geh_all), 1),
              "achieved": achieved,
              "unserviceable_edges": unserviceable_edges,
              "bound_violations": bound_violations,
              "purpose_allocation": purpose_allocation,
              "purpose_allocation_summary": {
                  "quarters_with_incompatible_routes": sum(
                      bool(allocation["incompatible"]) for allocation in purpose_allocation),
                  "incompatible_routes_by_purpose": dict(
                      sorted(allocation_incompatible.items())),
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
    solutions, rungs = solve_calibration_intervals(
        shapes, route_cost, targets_per_q, bounds_per_q, priors_per_q)
    return write_calibration_report(shapes, out_path, targets_per_q, solutions,
                                    integer_bounds_per_q if integer_bounds_per_q is not None else bounds_per_q,
                                    rungs, enforce_integer_bounds)
