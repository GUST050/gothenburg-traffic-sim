"""Would a measured two-way SUM constraint let the PFE choose the direction?

Evidence for the "let entropy choose" question raised against
``docs/reviews/DIRSPLIT_PLAN_RESEARCH_REVIEW_2026-08-16.md`` Option A.

    python3 -m tools.research_direction_sum_constraint            # parts A + B
    python3 -m tools.research_direction_sum_constraint --quick    # fewer OD samples

Part A (seconds, no external data) drives ``solve_interval_entropy`` directly
on a two-carriageway toy network to establish what a group/sum constraint
actually does to the split. Verified to give identical numbers on both the
compiled numba kernel and the ``PFE_PURE=1`` reference path, consistent with
the bit-identity contract pinned by ``tests/test_pfe_kernel.py``.

Part B (minutes, needs ``web/data/graph.graphml``) probes how directionally
balanced the route pool at sensor 107 is, by gravity-sampling OD pairs and
routing them over perturbed travel times. It is a deliberately simple stand-in
for ``assignment_priors.compute_assignment_load`` — which needs the gitignored
``sumo/net.net.xml`` — so read it for the SENSITIVITY it shows, not as a
replacement for the production prior.

Findings both parts support:

  1. A group constraint rescales every member route by the SAME factor
     (``pfe.py``: groups are appended to ``bounds_items``, whose correction is
     ``for j in js: x_list[j] *= factor``). A constraint on the SUM therefore
     carries no information about the split, and the seed's ratio passes
     through untouched. Entropy does not choose; the candidate pool does.
  2. At sensor 107 that pool ratio is not a usable estimator — it moves ~35
     percentage points on the stochastic-multipath sigma alone, against a real
     physical range of roughly 0.44-0.60.
  3. A two-sided level-2 band does not express "unknown" either: with the
     small IPF seed both carriageways are pushed to their LOWER bounds and
     then rescaled by the group, so the result is a deterministic
     ``lo_A / (lo_A + lo_B)`` — a disguised point estimate, independent of the
     pool. Direction uncertainty has to live ACROSS demand variants, which is
     what the existing q-variant architecture already does.
"""

from __future__ import annotations

import argparse
import math
import random

import numpy as np

# 107's two approved edges. A is northbound = toward centre (bearing 352.1 deg).
EDGE_A = "60786979_3575001205_0"
EDGE_B = "1455801464_18241874_0"

TOTAL = 100.0
# Honest 80% half-width measured in the review (residual spread 0.193 wide),
# centred on the validated tidal curve's 07:00 value.
CURVE_0700 = 0.545
HALF_WIDTH = 0.0965

DEFAULT_SPEED_KMH = {
    "motorway": 100, "motorway_link": 60, "trunk": 80, "trunk_link": 50,
    "primary": 60, "primary_link": 50, "secondary": 50, "secondary_link": 50,
    "tertiary": 50, "tertiary_link": 50, "residential": 30,
    "living_street": 20, "unclassified": 40,
}


def _rule(title: str) -> None:
    print(f"\n{'=' * 76}\n{title}\n{'=' * 76}")


# ── Part A — what a sum constraint does ─────────────────────────────────────
def _pool(n_a: int, n_b: int):
    from traffic_sim.demand.pfe import Candidate
    return ([Candidate(depart=0.0, edges=[f"uA{i}", "A", f"dA{i}"]) for i in range(n_a)]
            + [Candidate(depart=0.0, edges=[f"uB{i}", "B", f"dB{i}"]) for i in range(n_b)])


def _split(x, n_a: int, n: int) -> float:
    a = sum(x[j] for j in range(n_a))
    b = sum(x[j] for j in range(n_a, n))
    return a / (a + b) if (a + b) else float("nan")


def part_a() -> None:
    from traffic_sim.demand.pfe import solve_interval_entropy

    _rule("A1 — a SUM constraint inherits the seed's ratio; it does not choose")
    print(f"{'pool composition':<24}{'sum only':>11}{'imposed 52/48':>15}"
          f"{'sum, seed 3:1 to A':>21}")
    for n_a, n_b in [(10, 10), (10, 5), (10, 20), (30, 6), (4, 40)]:
        cands = _pool(n_a, n_b)
        n = len(cands)
        idx = list(range(n))
        s_sum = _split(solve_interval_entropy(
            cands, {}, {}, {}, groups=[(idx, TOTAL, TOTAL)]), n_a, n)
        s_pt = _split(solve_interval_entropy(
            cands, {"A": 52.0, "B": 48.0}, {}, {}), n_a, n)
        rc = np.array([1 / 3.0] * n_a + [1.0] * n_b)
        s_seed = _split(solve_interval_entropy(
            cands, {}, {}, {}, route_cost=rc, groups=[(idx, TOTAL, TOTAL)]), n_a, n)
        print(f"{n_a:>3} on A, {n_b:>3} on B{'':<7}{s_sum:>11.3f}{s_pt:>15.3f}"
              f"{s_seed:>21.3f}")
    print("\n  'sum only' equals n_A/(n_A+n_B) exactly. The measurement pins the")
    print("  total; nothing pins the split, so the pool decides it.")

    _rule("A2 — a two-sided level-2 band is a disguised point estimate")
    print(f"{'band on A':<20}{'lo_A/(lo_A+lo_B)':>19}   split for pools 10/10, 10/20, 30/6")
    bands = [(CURVE_0700 - HALF_WIDTH, CURVE_0700 + HALF_WIDTH),
             (0.40, 0.60), (0.45, 0.55), (0.50, 0.70), (0.35, 0.45)]
    for lo, hi in bands:
        out = []
        for n_a, n_b in [(10, 10), (10, 20), (30, 6)]:
            cands = _pool(n_a, n_b)
            n = len(cands)
            x = solve_interval_entropy(
                cands, {},
                {"A": (lo * TOTAL, hi * TOTAL), "B": ((1 - hi) * TOTAL, (1 - lo) * TOTAL)},
                {}, groups=[(list(range(n)), TOTAL, TOTAL)])
            out.append(_split(x, n_a, n) if x is not None else float("nan"))
        implied = lo / (lo + (1 - hi))
        print(f"[{lo:.3f},{hi:.3f}]{'':<7}{implied:>19.3f}   "
              + "  ".join(f"{v:.3f}" for v in out))
    print("\n  The answer is identical across pools — good — but it sits at the")
    print("  band's LOWER corners, not its centre. Widening a single solve's")
    print("  bounds does not represent uncertainty; it just moves the point.")


# ── Part B — how balanced is the real pool? ─────────────────────────────────
def part_b(n_od: int) -> None:
    import networkx as nx
    import osmnx as ox

    _rule("B — directional balance of the route pool at sensor 107")
    G = ox.load_graphml("web/data/graph.graphml")

    def scalar(v):
        return v[0] if isinstance(v, list) else v

    D = nx.DiGraph()
    for u, v, k, d in G.edges(keys=True, data=True):
        hw = str(scalar(d.get("highway", "")) or "")
        try:
            raw = scalar(d.get("maxspeed"))
            speed = float(str(raw).split()[0]) if raw else DEFAULT_SPEED_KMH.get(hw, 40)
        except (ValueError, AttributeError, IndexError):
            speed = DEFAULT_SPEED_KMH.get(hw, 40)
        length = float(d.get("length", 1.0))
        seconds = length / max(speed, 5) * 3.6
        if not D.has_edge(u, v) or seconds < D[u][v]["t"]:
            D.add_edge(u, v, t=seconds, eid=f"{u}_{v}_{k}")
    print(f"  {D.number_of_nodes()} nodes, {D.number_of_edges()} routable edges")

    nodes = list(D.nodes)
    xy = {n: (float(G.nodes[n]["x"]), float(G.nodes[n]["y"])) for n in nodes}

    def km(a, b):
        (x1, y1), (x2, y2) = xy[a], xy[b]
        return math.hypot((x2 - x1) * math.cos(math.radians((y1 + y2) / 2)) * 111.32,
                          (y2 - y1) * 110.57)

    rng = random.Random(11)
    n_variants, gravity_km = 3, 2.2

    def probe(sigma: float, tag: str):
        hit_a = hit_b = 0
        for vi in range(n_variants):
            vr = np.random.default_rng(100 + vi)
            H = nx.DiGraph()
            for u, v, d in D.edges(data=True):
                factor = 1.0 if sigma == 0 else float(np.exp(vr.normal(0, sigma)))
                H.add_edge(u, v, w=d["t"] * factor)
            done = 0
            while done < n_od // n_variants:
                o, dest = rng.choice(nodes), rng.choice(nodes)
                dist = km(o, dest)
                if dist < 0.3 or rng.random() > math.exp(-dist / gravity_km):
                    continue
                try:
                    path = nx.shortest_path(H, o, dest, weight="w")
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                done += 1
                eids = {D[path[i]][path[i + 1]]["eid"] for i in range(len(path) - 1)}
                hit_a += EDGE_A in eids
                hit_b += EDGE_B in eids
        total = hit_a + hit_b
        if not total:
            print(f"  {tag:<34} no hits")
            return None
        share = hit_a / total
        ci = 1.96 * math.sqrt(share * (1 - share) / total)
        print(f"  {tag:<34} A {hit_a:5d}  B {hit_b:5d}   split {share:.3f} "
              f"+/- {ci:.3f}")
        return share

    print(f"\n  gravity-sampled OD, {n_od} routed pairs, deterrence {gravity_km} km:")
    vals = []
    for sigma in (0.0, 0.10, 0.15, 0.20, 0.30, 0.45):
        tag = "shortest path (sigma 0)" if sigma == 0 else f"stochastic multipath sigma {sigma:.2f}"
        vals.append(probe(sigma, tag))
    got = [v for v in vals if v is not None]
    if got:
        print(f"\n  RANGE across the sigma knob alone: {min(got):.3f} to {max(got):.3f}"
              f"  = {100 * (max(got) - min(got)):.0f} percentage points")
    print(f"\n  city's published 2025 D-factor at 107   0.523")
    print(f"  validated tidal curve, flow-weighted    0.494")
    print(f"  physically plausible range (F2/F3)      0.44 - 0.60")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true",
                    help="4k OD samples instead of 24k (wider CIs, same story)")
    ap.add_argument("--skip-pool", action="store_true",
                    help="run only Part A (no graph load)")
    args = ap.parse_args()
    part_a()
    if not args.skip_pool:
        part_b(4000 if args.quick else 24000)


if __name__ == "__main__":
    main()
