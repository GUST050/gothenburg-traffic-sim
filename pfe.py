"""
PFE-lite — the level-4 reconciliation engine (Agent C's core).

A Path-Flow-Estimator-style LP (Bell & Shield 1996 lineage) solved per
15-minute interval over the candidate route pool:

  variables    x_r ≥ 0            — how many vehicles use candidate route r
  HARD         |Σ_{r∋e} x_r − c_e| ≤ tol_e     for MEASURED edges e (level 1)
  HARD         L_e ≤ Σ_{r∋e} x_r ≤ U_e         for BOUNDED edges (level 2)
  SOFT         minimise Σ_p w_p·|Σ_{r∋p} x_r − P_p|   toward PRIORS (level 3)
               + ε·Σ_r x_r        (parsimony: no unwarranted traffic)

Route continuity makes conservation hold by construction, so the solution
is always network-consistent. Solved with scipy HiGHS; fractional route
uses are rounded by largest remainder.

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


def solve_interval(
    cands: list[Candidate],
    measured: dict[str, float],
    bounds: dict[str, tuple[float, float]],
    priors: dict[str, tuple[float, float]],   # edge → (target, weight)
    tol_mult: float = 1.0,
) -> np.ndarray | None:
    """Return route-use vector for one interval, or None if infeasible."""
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


def largest_remainder_round(x: np.ndarray) -> np.ndarray:
    base = np.floor(x)
    remainder = x - base
    n_extra = int(round(x.sum() - base.sum()))
    if n_extra > 0:
        base[np.argsort(-remainder)[:n_extra]] += 1
    return base.astype(int)


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
    2–3 overlapping corridor routes per sensor made the LP infeasible."""
    cands = load_candidates(candidates_path)
    nq = len(targets_per_q)

    # Dedupe to distinct shapes — the LP variables
    seen: dict[str, Candidate] = {}
    for cand in cands:
        seen.setdefault(" ".join(cand.edges), cand)
    shapes = list(seen.values())
    print(f"  shape pool: {len(shapes)} distinct routes "
          f"(from {len(cands)} candidates)")

    achieved: dict[str, list[float]] = {}
    vid = 0
    infeasible = 0
    with open(out_path, "w") as f:
        f.write("<routes>\n")
        for i in range(nq):
            # Relaxation ladder: exact → widened tolerances → without the
            # level-2 bounds. An interval must never end up EMPTY just
            # because one constraint combination is unlucky.
            sol = solve_interval(shapes, targets_per_q[i],
                                 bounds_per_q[i], priors_per_q[i])
            if sol is None:
                for tol_mult, use_bounds in ((2.0, True), (4.0, True),
                                             (4.0, False)):
                    sol = solve_interval(
                        shapes, targets_per_q[i],
                        bounds_per_q[i] if use_bounds else {},
                        priors_per_q[i], tol_mult=tol_mult)
                    if sol is not None:
                        break
            if sol is None:
                infeasible += 1
                continue
            counts = largest_remainder_round(sol)
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

    # GEH on hourly aggregates at measured edges — the standard fit metric
    geh_ok = geh_all = 0
    for i in range(0, nq - 3, 4):
        for e in targets_per_q[i]:
            m = sum(achieved.get(e, [0.0] * nq)[i:i + 4])
            c = sum(targets_per_q[j].get(e, 0.0) for j in range(i, i + 4))
            if m + c > 0:
                geh = float(np.sqrt(2 * (m - c) ** 2 / (m + c)))
                geh_all += 1
                geh_ok += geh < 5
    return {"vehicles": vid, "infeasible_intervals": infeasible,
            "geh_ok": geh_ok, "geh_total": geh_all,
            "geh_pct": round(100 * geh_ok / max(1, geh_all), 1)}
