"""
Traffic assignment priors — the missing piece that explains why the grounded
OD method barely improved LOSO recovery (see the diagnosis in the commit
this file lands in).

WHY THIS EXISTS (the mechanism, confirmed empirically):
pfe.py's objective minimises Σ x_r · EPS_PARSIMONY — total vehicle count —
with NO offsetting pull on edges that carry no active hard constraint or
soft prior. A route only gets weight if it serves an active constraint.
build_candidates.py's population/POI grounding shapes WHICH routes exist in
the candidate pool, but does nothing to make the PFE actually USE routes
through an edge unless that edge (or one on the same route) is itself
constrained. This is why 107 recovered brilliantly in LOSO (it sits between
OTHER active constraints — the corridor-coupled 1076 — so realistic routes
through it get pulled along "for free") while isolated stations (1074,
1076, 134) collapsed toward zero: nothing pulls weight onto routes through
them once their OWN count is hidden.

THE FIX: the classic 4-step model's missing 4th step — ASSIGNMENT. Gravity-
distribute the SAME home/activity masses build_candidates.py uses across
many sampled OD pairs, route each via shortest path (edge-level origin/
destination sampling already gives natural route diversity — a lightweight
stand-in for Dial's stochastic multipath loading), and accumulate a loaded-
flow field that is NON-ZERO on every reachable edge — not just where a
random candidate happened to be drawn through it. This field becomes a
WEAK level-3 prior for every otherwise-unconstrained edge, replacing the
parsimony term's implicit "pull to zero" with "pull toward the gravity-
implied realistic level".

CALIBRATION, NOT TRANSFER (avoids the volume_priors.py mistake): earlier
this project tried LEARNING absolute volume levels from Norwegian stations
and rejected it — levels don't transfer across cities (leave-city-out error
4.5-14x). This is different: the assignment field is computed STRUCTURALLY
from Gothenburg's own population/POI/network (no cross-city ML), and only
ONE scale factor is fit, locally, by least squares against our own 6-7
measured edges. More sensors -> a better-constrained fit (and a natural
extension to per-road-class factors) -> this generalises by construction
as the city adds stations, unlike a learned model that would need retraining.

TWO BUGS FOUND AND FIXED DURING DEVELOPMENT (kept here as a record, since
both are easy to reintroduce by "simplifying"):
  1. First cut weighted shortest paths by physical LENGTH. Real route choice
     minimises TRAVEL TIME — length-weighting sends the assignment down
     slow residential shortcuts instead of the arterials sensors sit on.
     Fixed: weight = length / speed (from build_sumo_net's speed parser).
  2. Even with time-weighting, a single deterministic shortest path per OD
     pair gave essentially ZERO load on every sensor edge (checked
     directly: pure through-traffic assignment put 0 of 20 000 samples on
     6 of 7 sensor edges). All-or-nothing assignment collapses onto one
     canonical route and misses "good but not literally fastest"
     alternatives — exactly the failure mode Dial's stochastic multipath
     method exists to fix. Fixed: route each sample through one of several
     randomly-perturbed weight variants (edge times jittered ~lognormal),
     spreading load across realistic alternative routes instead of a
     single brittle shortest path.

Run (or via build_sumo_demand, which uses it by default):
  python3 assignment_priors.py [--n-samples 40000]

Writes sumo/assignment_priors.json — {edge: [96 scaled quarter-flows]} for
every graph edge not already measured, direction-paired, or corridor-coupled.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import networkx as nx
import numpy as np
import osmnx as ox

from build_sumo_demand import load_direction_split
from build_candidates import (PURPOSE_SHARES_WEEKDAY, activity_mass, find_gates,
                              gate_weights, gravity_distance_km, home_mass,
                              load_graph_edges)
from build_sumo_net import parse_speed_ms

GRAPH_PATH = Path("web/data/graph.graphml")
OUT_PATH   = Path("sumo/assignment_priors.json")
WEIGHT     = 0.15   # << direction/corridor priors' typical 1/band weight — stays weak


def build_perturbed_variants(
    base_time: dict[tuple[int, int], float], n_variants: int, sigma: float, rng,
) -> list[nx.DiGraph]:
    """Dial-style stochastic multipath stand-in (see module docstring, bug
    #2): a single deterministic shortest-path graph puts zero load on most
    sensor edges, since real route choice spreads over several "good but not
    literally fastest" alternatives. n_variants independently lognormal-
    jittered copies of the base travel-time graph let different samples take
    different plausible routes instead of collapsing onto one canonical
    path."""
    edge_keys = list(base_time)
    variants = []
    for _ in range(n_variants):
        noise = rng.lognormal(0, sigma, size=len(edge_keys))
        DGi = nx.DiGraph()
        for (u, v), n in zip(edge_keys, noise):
            DGi.add_edge(u, v, weight=base_time[(u, v)] * n)
        variants.append(DGi)
    return variants


def robust_scale(x_load: np.ndarray, y_meas: np.ndarray) -> tuple[float, float]:
    """Calibrate ONE global loading-unit -> veh/day scale factor by the
    median measured/load ratio (not least-squares — with only 6-7
    calibration points a single noisy edge can drag a least-squares fit
    badly; verified LS gave R² -4 to -8 across attempts, dominated by one or
    two high-leverage points). Returns (scale, R²) — R² is informational
    only, this is a WEAK prior (weight≈0.15) that needs the right order of
    magnitude and spatial shape, not a tight per-edge fit."""
    ratios = y_meas / np.maximum(x_load, 1e-9)
    scale = float(np.median(ratios))
    resid = y_meas - scale * x_load
    fit_r2 = 1 - (resid @ resid) / max(((y_meas - y_meas.mean()) @
                                        (y_meas - y_meas.mean())), 1e-9)
    return scale, float(fit_r2)


def daily_shape() -> np.ndarray:
    """96-quarter WEEKDAY-only shape (unlike build_candidates.daily_shape,
    a separate implementation at hourly resolution with an is_weekend
    param) — deliberately weekday-only, since this module is calibrated
    once against the fixed STRUCTURAL_REFERENCE_DATE (2025-09-16, a
    weekday), not per simulated date."""
    with open("web/data/normal_profile.json") as f:
        profiles = json.load(f)["profiles"]
    acc = np.zeros(96)
    for p in profiles.values():
        wd = p.get("weekday") or []
        if any(v is not None for v in wd):
            acc += [v or 0 for v in wd]
    return acc / acc.sum()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n-samples", type=int, default=40000,
                    help="OD pairs sampled for the stochastic loading")
    ap.add_argument("--gravity-km", type=float, default=2.6)
    ap.add_argument("--through-fraction", type=float, default=0.5,
                    help="same θ as build_candidates.py — E-E through trips "
                        "are a first-class part of the loaded field, not "
                        "just an afterthought (this omission was the actual "
                        "bug behind the first attempt's bad fit: Skånegatan/"
                        "Valhallagatan are through-corridors to Örgrytevägen/"
                        "Söderleden, not just home-activity destinations)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-variants", type=int, default=10,
                    help="number of randomly-perturbed weight graphs — the "
                        "lightweight stand-in for Dial's stochastic "
                        "multipath loading (see module docstring, bug #2)")
    ap.add_argument("--perturb-sigma", type=float, default=0.35,
                    help="lognormal sigma for per-edge travel-time jitter")
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    G = ox.load_graphml(GRAPH_PATH)
    base_time = {}
    for u, v, d in G.edges(data=True):
        speed = parse_speed_ms(d)
        base_time[(u, v)] = d.get("length", 1.0) / max(speed, 1.0)

    print(f"Building {args.n_variants} randomly-perturbed travel-time graphs "
          f"(stochastic multipath stand-in) …")
    variants = build_perturbed_variants(base_time, args.n_variants,
                                        args.perturb_sigma, rng)

    edges = load_graph_edges(G)
    edge_ids = [e["id"] for e in edges]
    print(f"{len(edges)} edges, computing home/activity mass …")
    hmass = home_mass(edges)
    amass = activity_mass(G, edges)
    if hmass.sum() == 0:
        raise SystemExit("home_mass is zero — run fetch_deso.py first")
    pH = hmass / hmass.sum()

    lats = np.array([e["lat"] for e in edges])
    lons = np.array([e["lon"] for e in edges])

    entries, exits = find_gates(G)
    w_entry = gate_weights(G, entries)
    w_exit  = gate_weights(G, exits)
    entry_nodes = [n for _, n in entries]
    exit_nodes  = [n for _, n in exits]

    n_through = int(args.n_samples * args.through_fraction)
    n_tours   = args.n_samples - n_through
    print(f"Sampling {n_through} through (E-E) + {n_tours} tour (E-I/I-E/I-I) "
          f"OD pairs and loading shortest paths (this is the assignment step) …")
    load = {eid: 0.0 for eid in edge_ids}
    n_ok = n_nopath = 0

    variant_idx = rng.integers(len(variants), size=n_through + n_tours)

    def route(u, v, vi):
        return nx.shortest_path(variants[variant_idx[vi]], u, v, weight="weight")

    # E-E: gate → gate, same road-class weighting as build_candidates.py
    gi = rng.choice(len(entries), size=n_through, p=w_entry)
    go = rng.choice(len(exits),  size=n_through, p=w_exit)
    vi = 0
    for i, o in zip(gi, go):
        try:
            path = route(entry_nodes[i], exit_nodes[o], vi)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            n_nopath += 1; vi += 1
            continue
        vi += 1
        n_ok += 1
        for a, b in zip(path, path[1:]):
            for k in G[a][b]:
                load[f"{a}_{b}_{k}"] = load.get(f"{a}_{b}_{k}", 0) + 1

    # E-I / I-E / I-I: home ↔ activity, gravity-weighted
    home_idx = rng.choice(len(edges), size=n_tours, p=pH)
    purposes = rng.choice(list(PURPOSE_SHARES_WEEKDAY), size=n_tours,
                          p=list(PURPOSE_SHARES_WEEKDAY.values()))
    for h_i, purpose in zip(home_idx, purposes):
        w = amass[purpose]
        if w.sum() == 0:
            vi += 1
            continue
        d_km = gravity_distance_km(lats, lons, lats[h_i], lons[h_i])
        wgt = w * np.exp(-d_km / args.gravity_km)
        wgt[h_i] = 0
        if wgt.sum() == 0:
            vi += 1
            continue
        a_i = rng.choice(len(edges), p=wgt / wgt.sum())

        try:
            path = route(edges[h_i]["v"], edges[a_i]["u"], vi)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            n_nopath += 1; vi += 1
            continue
        vi += 1
        n_ok += 1
        load[edges[h_i]["id"]] += 1
        for a, b in zip(path, path[1:]):
            for k in G[a][b]:
                load[f"{a}_{b}_{k}"] = load.get(f"{a}_{b}_{k}", 0) + 1
        load[edges[a_i]["id"]] += 1
    print(f"  {n_ok} pairs routed, {n_nopath} had no path (disconnected "
          f"fringe — expected at a clipped bbox)")

    # ── Calibrate ONE global scale factor against our own measured edges ──────
    with open("web/data/flows.json") as f:
        flows = json.load(f)["flows"]
    with open("web/data/network.geojson") as f:
        geo = json.load(f)
    level = {f["properties"]["id"]: f["properties"].get("level")
             for f in geo["features"] if f["properties"].get("sensor_id")}

    # Total (two-way) sensors: split the raw count by the ESTIMATED direction
    # share, same as build_sumo_demand.build_targets/write_counts — not a
    # blind 50/50, which was a real, avoidable divergence from that fix
    # (found 2026-07-06 alongside the sensor-107 reporting-artifact review).
    est_shares = load_direction_split()
    x_load, y_meas = [], []
    for eid, lv in level.items():
        arr = flows.get(eid)
        if not arr:
            continue
        if lv == "Total":
            shares = est_shares.get(eid)
            vals = [v * (shares[i % 96] if shares else 0.5)
                   for i, v in enumerate(arr) if v is not None]
        else:
            vals = [v for v in arr if v is not None]
        if not vals or load.get(eid, 0) == 0:
            continue
        daily_mean = sum(vals) / len(vals) * 96          # → veh/day equivalent
        x_load.append(load[eid])
        y_meas.append(daily_mean)
    x_load, y_meas = np.array(x_load), np.array(y_meas)
    scale, fit_r2 = robust_scale(x_load, y_meas)
    print(f"Scale factor (robust median ratio) on {len(x_load)} measured "
          f"edges: scale={scale:.3f} veh/day per loading-unit, "
          f"R²={fit_r2:.2f} (informational only — see docstring)")

    shape96 = daily_shape()
    covered = {eid for eid in level} | {
        eid for eid in json.load(open("sumo/prior_flows.json"))["edges"]
    }
    with open("web/data/observability.json") as f:
        covered |= set(json.load(f).get("corridor_priors", {}))

    out = {}
    for eid, ld in load.items():
        if ld <= 0 or eid in covered:
            continue
        daily = scale * ld
        out[eid] = [round(daily * s, 2) for s in shape96]

    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump({"weight": WEIGHT, "scale_veh_per_day": scale,
                   "fit_r2": round(fit_r2, 3), "n_samples": args.n_samples,
                   "flows": out}, f)
    print(f"Wrote {OUT_PATH}  ({len(out)} previously-unconstrained edges "
          f"now carry a weak gravity-assignment prior)")


if __name__ == "__main__":
    main()
