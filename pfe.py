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

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import lil_matrix, vstack

EPS_PARSIMONY = 1e-3
MEAS_TOL_FRAC = 0.05      # hard band around measured counts
MEAS_TOL_MIN  = 2.0


@dataclass
class Candidate:
    depart: float
    edges: list[str]


def load_candidates(path: Path) -> list[Candidate]:
    out = []
    for veh in ET.parse(path).getroot().iter("vehicle"):
        route = veh.find("route")
        out.append(Candidate(depart=float(veh.get("depart")),
                             edges=route.get("edges").split()))
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


IPF_MAX_ITERATIONS = 200   # no early-exit convergence check (removed
                          # 2026-07-10 — see the burn-in/averaging comment
                          # below for why): each iteration is a handful of
                          # cheap vector ops, not an LP solve, so always
                          # running the full budget costs little and
                          # avoids having to detect convergence at all


def solve_interval_entropy(
    cands: list[Candidate],
    measured: dict[str, float],
    bounds: dict[str, tuple[float, float]],
    priors: dict[str, tuple[float, float]],
    route_cost: np.ndarray | None = None,
    tol_mult: float = 1.0,
    max_iterations: int = IPF_MAX_ITERATIONS,
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
    for it in range(max_iterations):
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
        for e, target in measured.items():
            js = touch[e]
            total = x[js].sum()
            if total <= 0:
                continue
            x[js] *= target / total

        # Level 2 — interval bounds, hard
        for e, (lo, hi) in bounds.items():
            js = touch.get(e, [])
            if not js:
                continue
            total = x[js].sum()
            if total <= 0:
                continue
            factor = lo / total if total < lo else (hi / total if total > hi else 1.0)
            if factor != 1.0:
                x[js] *= factor

        # Sample HERE, right after the hard correction — by construction
        # this point already satisfies every hard constraint just
        # enforced, so averaging these samples across iterations (below)
        # stays hard-feasible regardless of whether level 3 (next) ever
        # settles or keeps oscillating against a hard constraint sharing
        # its routes.
        if it >= burn_in:
            x_sum += x
            n_samples += 1

        # Level 3 — priors, soft partial pull (weight=0 -> no pull at all;
        # weight->inf -> a full rescale to the target, same as level 1/2).
        for e, (target, weight) in priors.items():
            js = touch.get(e, [])
            if not js or target <= 0 or weight <= 0:
                continue
            total = x[js].sum()
            if total <= 0:
                continue
            alpha = weight / (weight + 1.0)
            factor = 1.0 + alpha * (target / total - 1.0)
            if factor > 0 and factor != 1.0:
                x[js] *= factor

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


def calibrate(
    candidates_path: Path,
    out_path: Path,
    targets_per_q: list[dict[str, float]],          # measured, per quarter
    bounds_per_q: list[dict[str, tuple[float, float]]],
    priors_per_q: list[dict[str, tuple[float, float]]],
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
    cands = load_candidates(candidates_path)
    nq = len(targets_per_q)

    # Dedupe to distinct shapes — the LP variables
    seen: dict[str, Candidate] = {}
    for cand in cands:
        seen.setdefault(" ".join(cand.edges), cand)
    shapes = list(seen.values())
    print(f"  shape pool: {len(shapes)} distinct routes "
          f"(from {len(cands)} candidates)")
    route_cost = path_size_weights(shapes)

    solutions: list[np.ndarray | None] = []
    infeasible = 0
    for i in range(nq):
        # Relaxation ladder: exact → widened tolerances → without the
        # level-2 bounds → the original LP as a last resort. An interval
        # must never end up EMPTY just because one constraint combination
        # is unlucky.
        sol = solve_interval_entropy(shapes, targets_per_q[i],
                                    bounds_per_q[i], priors_per_q[i],
                                    route_cost=route_cost)
        if sol is None:
            for tol_mult, use_bounds in ((2.0, True), (4.0, True), (4.0, False)):
                sol = solve_interval_entropy(
                    shapes, targets_per_q[i],
                    bounds_per_q[i] if use_bounds else {},
                    priors_per_q[i], tol_mult=tol_mult, route_cost=route_cost)
                if sol is not None:
                    break
        if sol is None:
            sol = solve_interval(shapes, targets_per_q[i], bounds_per_q[i],
                                 priors_per_q[i], route_cost=route_cost)
        if sol is None:
            infeasible += 1
        solutions.append(sol)

    achieved: dict[str, list[float]] = {}
    vid = 0
    with open(out_path, "w") as f:
        f.write("<routes>\n")
        for i in range(nq):
            sol = solutions[i]
            if sol is None:
                continue
            counts = round_preserving_measured(sol, shapes, targets_per_q[i])
            # SUMO requires the route file sorted by depart — collect the
            # interval's departures first, then write in ascending order.
            departures: list[tuple[float, str]] = []
            for cand, k in zip(shapes, counts):
                for dup in range(int(k)):
                    depart = i * 900 + (dup + 0.5) * 900 / max(1, k)
                    departures.append((depart, " ".join(cand.edges)))
                for e in set(cand.edges):
                    achieved.setdefault(e, [0.0] * nq)
                    if k:
                        achieved[e][i] += float(k)
            for depart, edges_str in sorted(departures):
                f.write(f'  <vehicle id="pfe{vid}" depart="{depart:.1f}">'
                        f'<route edges="{edges_str}"/></vehicle>\n')
                vid += 1
        f.write("</routes>\n")

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
    return {"vehicles": vid, "infeasible_intervals": infeasible,
            "geh_ok": geh_ok, "geh_total": geh_all,
            "geh_pct": round(100 * geh_ok / max(1, geh_all), 1),
            "achieved": achieved}
