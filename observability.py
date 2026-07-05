"""
Agent B — Mathematics: what the measurements imply, exactly.

Run after build_data.py:
  python3 observability.py

Level 2 of the estimation hierarchy (see ARCHITECTURE.md): before any
modelling, extract everything that follows MATHEMATICALLY from the measured
series via flow conservation (link-flow observability, Castillo et al. 2015).

Three products:

1. JUNCTION SOLVES — at a junction where all incident directed edges but one
   are known (measured or previously derived), the last one equals the
   balance. Iterated to a fixpoint: each new derivation can unlock the next
   junction. Only applied at nodes where ALL other legs are known — never at
   boundary stubs or nodes with several unknowns.

2. CORRIDOR ALARMS — two known edges connected by an unbranched chain must
   carry (nearly) the same flow; the residual is a data-quality alarm that
   fires at intake time, before bad data poisons calibration. Small
   residuals are expected (driveways, parking) — the tolerance is explicit.

3. CLASSIFICATION per directed edge, consumed by Agent C (constraints) and
   Agent F (provenance): measured / derived / unobserved (+ a capacity cap
   for everything, lanes × 1800 veh/h — the trivial upper bound; tighter
   LP intervals are Agent C's inequality inputs, planned next).

Writes web/data/observability.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import osmnx as ox

GRAPH_PATH = Path("web/data/graph.graphml")
GEO_PATH   = Path("web/data/network.geojson")
FLOWS_PATH = Path("web/data/flows.json")
OUT_PATH   = Path("web/data/observability.json")

# Conservation tolerance: |in − out| at a junction may legitimately be this
# fraction of local throughput (driveways, parking garages) plus a floor.
REL_TOL   = 0.15
ABS_TOL   = 8.0     # vehicles / 15 min

NEG_CLIP  = -5.0    # derived flows below this (veh/15min) trigger an alarm
CAP_VEH_Q = 1800 / 4   # per-lane capacity, vehicles per quarter


def _scalar(v):
    return v[0] if isinstance(v, list) else v


def load_network():
    G = ox.load_graphml(GRAPH_PATH)
    with open(FLOWS_PATH) as f:
        payload = json.load(f)
    flows = {e: np.array([v if v is not None else np.nan for v in arr],
                         dtype=float)
             for e, arr in payload["flows"].items()}
    return G, flows, payload["epoch"]


def edge_id(u, v, k) -> str:
    return f"{u}_{v}_{k}"


def node_edges(G, n) -> tuple[list[str], list[str]]:
    """(incoming edge ids, outgoing edge ids) at node n."""
    ins  = [edge_id(u, v, k) for u, v, k in G.in_edges(n, keys=True)]
    outs = [edge_id(u, v, k) for u, v, k in G.out_edges(n, keys=True)]
    return ins, outs


def junction_fixpoint(G, known: dict[str, np.ndarray],
                      n_slots: int) -> tuple[dict[str, np.ndarray], list[dict]]:
    """Derive unknown edges at junctions with exactly one unknown leg.
    Returns (derived series, alarms). Iterates until nothing new unlocks."""
    derived: dict[str, np.ndarray] = {}
    alarms: list[dict] = []

    def value_of(e):
        if e in known:
            return known[e]
        return derived.get(e)

    changed = True
    while changed:
        changed = False
        for n in G.nodes:
            ins, outs = node_edges(G, n)
            if not ins or not outs:
                continue   # boundary stub / dead end — not a conservation node
            legs = [(e, +1) for e in ins] + [(e, -1) for e in outs]
            unknown = [(e, s) for e, s in legs if value_of(e) is None]
            if len(unknown) != 1:
                continue
            e_u, sign_u = unknown[0]
            balance = np.zeros(n_slots)
            ok = np.ones(n_slots, dtype=bool)
            for e, s in legs:
                if e == e_u:
                    continue
                v = value_of(e)
                balance += s * np.nan_to_num(v)
                ok &= ~np.isnan(v)
            series = sign_u * -balance          # solve: sum(in) − sum(out) = 0
            series[~ok] = np.nan
            neg = series[ok]
            if len(neg) and np.nanmin(neg) < NEG_CLIP:
                alarms.append({
                    "type": "negative_derivation", "node": int(n), "edge": e_u,
                    "min_value": round(float(np.nanmin(neg)), 1),
                    "hint": "conservation at this junction contradicts the "
                            "measurements — check snapping/directions here",
                })
            derived[e_u] = np.clip(series, 0, None)
            changed = True
    return derived, alarms


def chain_successor(G, e: str, known_set: set[str]) -> str | None:
    """Follow an unbranched, unmeasured chain from edge e's head; return the
    next KNOWN edge if the chain reaches one, else None."""
    _, v, _ = e.split("_")
    node, hops = int(v), 0
    while hops < 25:
        ins, outs = node_edges(G, node)
        if len(outs) != 1 or len(ins) != 1:
            return None                      # branches → not a pure corridor
        nxt = outs[0]
        if nxt in known_set:
            return nxt
        node = int(nxt.split("_")[1])
        hops += 1
    return None


def corridor_alarms(G, known: dict[str, np.ndarray]) -> list[dict]:
    alarms = []
    known_set = set(known)
    for e in sorted(known_set):
        nxt = chain_successor(G, e, known_set)
        if nxt is None:
            continue
        a, b = known[e], known[nxt]
        ok = ~np.isnan(a) & ~np.isnan(b)
        if ok.sum() < max(24, 0.1 * len(a)):   # need a meaningful overlap
            continue
        resid = a[ok] - b[ok]
        tol = REL_TOL * np.maximum(a[ok], b[ok]) + ABS_TOL
        frac_bad = float((np.abs(resid) > tol).mean())
        alarms.append({
            "type": "corridor_consistency", "from_edge": e, "to_edge": nxt,
            "mean_residual": round(float(resid.mean()), 1),
            "frac_outside_tolerance": round(frac_bad, 3),
            "severity": "ALARM" if frac_bad > 0.25 else "ok",
        })
    return alarms


def lane_capacity(G) -> dict[str, float]:
    caps = {}
    for u, v, k, d in G.edges(keys=True, data=True):
        raw = _scalar(d.get("lanes"))
        try:
            lanes = max(1, int(str(raw).split(";")[0]))
        except (TypeError, ValueError):
            lanes = 1
        caps[edge_id(u, v, k)] = lanes * CAP_VEH_Q
    return caps


# ── Interval bounds (the level-2 product that bites at low sensor density) ────

def neighbour_targets(G, measured: set[str]) -> list[str]:
    """Unmeasured directed edges sharing a junction with a measured edge —
    the edges where conservation bounds are tight enough to matter."""
    nodes = set()
    for e in measured:
        u, v, _ = e.split("_")
        nodes.update((int(u), int(v)))
    targets = []
    for u, v, k in G.edges(keys=True):
        e = edge_id(u, v, k)
        if e not in measured and (u in nodes or v in nodes):
            targets.append(e)
    return sorted(targets)


def flow_bounds(G, measured_q: dict[str, float], caps: dict[str, float],
                targets: list[str]) -> dict[str, tuple[float, float]]:
    """Min/max feasible flow per target edge for ONE quarter, via LP:
    conservation (with slack for driveways/parking) at every internal node,
    measured edges fixed, 0 ≤ flow ≤ lane capacity."""
    from scipy.optimize import linprog
    from scipy.sparse import lil_matrix

    edges = [edge_id(u, v, k) for u, v, k in G.edges(keys=True)]
    idx = {e: i for i, e in enumerate(edges)}
    n = len(edges)

    # Conservation rows: sum(in) − sum(out) = slack, |slack| ≤ tol(node)
    rows, tols = [], []
    for node in G.nodes:
        ins, outs = node_edges(G, node)
        if not ins or not outs:
            continue
        r = lil_matrix((1, n))
        meas_here = 0.0
        for e in ins:
            r[0, idx[e]] = 1
            meas_here += measured_q.get(e, 0)
        for e in outs:
            r[0, idx[e]] = -1
            meas_here += measured_q.get(e, 0)
        rows.append(r)
        tols.append(ABS_TOL + REL_TOL * meas_here)

    from scipy.sparse import vstack
    A = vstack(rows).tocsc()
    tol = np.array(tols)
    A_ub = vstack([A, -A]).tocsc()          # |A x| ≤ tol
    b_ub = np.concatenate([tol, tol])

    lb = np.zeros(n)
    ub = np.array([caps.get(e, CAP_VEH_Q) for e in edges])
    for e, v in measured_q.items():
        if e in idx and not np.isnan(v):
            lb[idx[e]] = ub[idx[e]] = v
    var_bounds = list(zip(lb, ub))

    out: dict[str, tuple[float, float]] = {}
    for e in targets:
        c = np.zeros(n)
        c[idx[e]] = 1.0
        lo = linprog(c,  A_ub=A_ub, b_ub=b_ub, bounds=var_bounds, method="highs")
        hi = linprog(-c, A_ub=A_ub, b_ub=b_ub, bounds=var_bounds, method="highs")
        if lo.success and hi.success:
            out[e] = (max(0.0, float(lo.fun)), float(-hi.fun))
    return out


def compute_bounds_cli(date: str, begin: str, end: str) -> None:
    """CLI: per-quarter bounds for the neighbour targets over a window.
    Writes web/data/observability_bounds.json (consumed by Agent C)."""
    import pandas as pd
    G, measured, epoch = load_network()
    caps = lane_capacity(G)
    targets = neighbour_targets(G, set(measured))
    print(f"Bounds for {len(targets)} neighbour edges")

    t0 = pd.Timestamp(f"{date} {begin}")
    t1 = (t0.normalize() + pd.Timedelta(days=1)) if end == "24:00" \
        else pd.Timestamp(f"{date} {end}")
    q0 = int((t0 - pd.Timestamp(epoch)) / pd.Timedelta(minutes=15))
    nq = int((t1 - t0) / pd.Timedelta(minutes=15))

    bounds: dict[str, list] = {e: [] for e in targets}
    for i in range(nq):
        measured_q = {e: float(s[q0 + i]) for e, s in measured.items()
                      if not np.isnan(s[q0 + i])}
        bq = flow_bounds(G, measured_q, caps, targets)
        for e in targets:
            lo_hi = bq.get(e)
            bounds[e].append([round(lo_hi[0], 1), round(lo_hi[1], 1)]
                             if lo_hi else None)
        if i % 8 == 0:
            print(f"  quarter {i + 1}/{nq}")

    out = Path("web/data/observability_bounds.json")
    with open(out, "w") as f:
        json.dump({"date": date, "begin": begin, "end": end,
                   "epoch_window": str(t0), "n_quarters": nq,
                   "graph_edges": G.number_of_edges(),   # cache fingerprint
                   "bounds": bounds,
                   "note": "Per-quarter [min,max] feasible flow from "
                           "conservation + capacity — level-2 intervals."}, f)
    tight = sum(1 for e in targets
                for b in bounds[e] if b and b[1] - b[0] < CAP_VEH_Q * 0.5)
    print(f"Wrote {out}  ({tight} edge-quarters tighter than half capacity)")


def main() -> None:
    G, measured, epoch = load_network()
    n_slots = len(next(iter(measured.values())))
    print(f"Graph: {G.number_of_edges()} directed edges, "
          f"{len(measured)} measured")

    derived, neg_alarms = junction_fixpoint(G, measured, n_slots)
    print(f"Junction fixpoint: {len(derived)} edges derived exactly")
    for e in sorted(derived):
        s = derived[e]
        print(f"  derived {e}: daily mean "
              f"{np.nansum(s) / max(1, (~np.isnan(s)).sum() / 96):.0f} veh")

    alarms = neg_alarms + corridor_alarms(G, {**measured, **derived})
    for a in alarms:
        flag = "⚠" if a.get("severity") == "ALARM" or a["type"] == "negative_derivation" else "·"
        print(f"  {flag} {a}")

    caps = lane_capacity(G)
    classes = {}
    for u, v, k in G.edges(keys=True):
        e = edge_id(u, v, k)
        classes[e] = ("measured" if e in measured
                      else "derived" if e in derived
                      else "unobserved")

    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump({
            "epoch": epoch,
            "interval_minutes": 15,
            "classes": classes,
            "derived_flows": {e: [None if np.isnan(x) else round(float(x), 1)
                                  for x in s]
                              for e, s in derived.items()},
            "capacity_veh_per_quarter": {e: c for e, c in caps.items()},
            "alarms": alarms,
            "note": "Level-2 products (exact only). Derived = uniquely "
                    "implied by conservation; never a statistical guess.",
        }, f, separators=(",", ":"))
    n_cls = {c: sum(1 for x in classes.values() if x == c)
             for c in ("measured", "derived", "unobserved")}
    print(f"Wrote {OUT_PATH}  {n_cls}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bounds", action="store_true",
                    help="also compute per-quarter interval bounds (LP)")
    ap.add_argument("--date",  default="2025-09-16")
    ap.add_argument("--begin", default="00:00")
    ap.add_argument("--end",   default="24:00")
    args = ap.parse_args()
    main()
    if args.bounds:
        compute_bounds_cli(args.date, args.begin, args.end)
