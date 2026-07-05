"""
Tour-based gravity candidate generator — the level-3 prior on WHERE PEOPLE GO.

Run (or via build_sumo_demand, default engine):
  python3 build_candidates.py [--n-tours 4000 --n-through 6000 --n-local 2000]

Replaces uniform randomTrips with a purposeful trip population:

  COMMUTE TOURS (paired!) — a home end is drawn ∝ residential density, an
  activity end ∝ activity mass (major streets + centrality), with a gravity
  deterrence on distance. Each tour generates BOTH legs: home→activity in
  the morning peak and activity→home in the afternoon. This is what makes
  "in through the sensor in the AM, out through it in the PM" structural:
  the PFE scales tours whose two legs cross the sensor in opposite
  directions at the right hours.

  THROUGH TRIPS — gate-to-gate across the area (entry/exit stub edges),
  departure rate following the measured average daily shape. Sampled
  symmetrically so both directions of every corridor are available.

  LOCAL ERRANDS — short tours around midday/evening.

Masses come from the road graph itself (residential-street density as the
home proxy, major-street density + centre proximity as the activity proxy)
— the same features validated in dirsplit. No levels are asserted anywhere:
this shapes WHICH routes exist; the PFE decides HOW MANY vehicles, bound by
measurements. Routing via duarouter; output contract identical to
randomTrips (candidates.rou.xml), so pfe.py is untouched.

Writes sumo/candidates.rou.xml (+ sumo/tours.trips.xml intermediate).
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import osmnx as ox

from build_sumo_net import sumo_home

SUMO_DIR   = Path("sumo")
NET_PATH   = SUMO_DIR / "net.net.xml"
GRAPH_PATH = Path("web/data/graph.graphml")

RESIDENTIAL = {"residential", "living_street"}
MAJOR       = {"trunk", "primary", "secondary", "tertiary"}
DISC_M      = 700.0          # mass neighbourhood
GRAVITY_KM  = 1.8            # deterrence scale exp(-d/GRAVITY_KM)
AM_PEAK_H, AM_SD = 7.9, 1.3
PM_PEAK_H, PM_SD = 16.4, 1.5


def scalar(v):
    return v[0] if isinstance(v, list) else v


def load_graph_edges():
    G = ox.load_graphml(GRAPH_PATH)
    edges = []
    for u, v, k, d in G.edges(keys=True, data=True):
        lat = (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2
        lon = (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2
        hw = str(scalar(d.get("highway", "")) or "")
        edges.append({"id": f"{u}_{v}_{k}", "u": u, "v": v,
                      "lat": lat, "lon": lon, "hw": hw,
                      "len": float(d.get("length", 0))})
    return G, edges


def edge_masses(edges, centre=(57.7068, 11.9668)):
    """Home mass H and activity mass A per edge, from graph structure."""
    lats = np.array([e["lat"] for e in edges])
    lons = np.array([e["lon"] for e in edges])
    klat, klon = 110_540.0, 111_320.0 * math.cos(math.radians(57.7))
    xs, ys = lons * klon, lats * klat

    res_len = np.array([e["len"] if e["hw"] in RESIDENTIAL else 0 for e in edges])
    maj_len = np.array([e["len"] if e["hw"] in MAJOR else 0 for e in edges])

    H = np.zeros(len(edges))
    A = np.zeros(len(edges))
    for i in range(len(edges)):
        d2 = (xs - xs[i]) ** 2 + (ys - ys[i]) ** 2
        near = d2 <= DISC_M ** 2
        H[i] = res_len[near].sum()
        A[i] = maj_len[near].sum()
    dc = np.sqrt((lats - centre[0]) ** 2 * klat**2
                 + (lons - centre[1]) ** 2 * klon**2)
    A = A * (1.0 + 2.0 * np.exp(-dc / 1500.0))   # centre pull for activities
    return H, A, xs, ys


def find_gates(G):
    """Entry edges (source node has no incoming) and exit edges (target node
    has no outgoing) — where through traffic enters/leaves the study area."""
    entries, exits = [], []
    for u, v, k in G.edges(keys=True):
        if G.in_degree(u) == 0:
            entries.append(f"{u}_{v}_{k}")
        if G.out_degree(v) == 0:
            exits.append(f"{u}_{v}_{k}")
    return entries, exits


def daily_shape() -> np.ndarray:
    """Hourly departure weights for through traffic — the measured average
    weekday shape across our stations (normal_profile.json)."""
    with open("web/data/normal_profile.json") as f:
        profiles = json.load(f)["profiles"]
    acc = np.zeros(24)
    for p in profiles.values():
        wd = p.get("weekday") or []
        for h in range(24):
            vals = [v for v in wd[h * 4:(h + 1) * 4] if v is not None]
            if vals:
                acc[h] += sum(vals) / len(vals)
    return acc / acc.sum()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n-tours",   type=int, default=4000)
    ap.add_argument("--n-through", type=int, default=6000)
    ap.add_argument("--n-local",   type=int, default=2000)
    ap.add_argument("--seed",      type=int, default=42)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    G, edges = load_graph_edges()
    H, A, xs, ys = edge_masses(edges)
    entries, exits = find_gates(G)
    shape = daily_shape()
    print(f"{len(edges)} edges, {len(entries)} entry gates, "
          f"{len(exits)} exit gates")

    pH = H / H.sum()
    trips: list[tuple[float, str, str]] = []   # (depart_s, from_edge, to_edge)

    # ── Commute tours: home→activity AM, activity→home PM (PAIRED) ────────────
    homes = rng.choice(len(edges), size=args.n_tours, p=pH)
    for h_i in homes:
        d_km = np.sqrt((xs - xs[h_i]) ** 2 + (ys - ys[h_i]) ** 2) / 1000.0
        w = A * np.exp(-d_km / GRAVITY_KM)
        w[h_i] = 0
        a_i = rng.choice(len(edges), p=w / w.sum())
        t_am = float(np.clip(rng.normal(AM_PEAK_H, AM_SD), 5.0, 12.0)) * 3600
        t_pm = float(np.clip(rng.normal(PM_PEAK_H, PM_SD), 12.5, 22.0)) * 3600
        trips.append((t_am, edges[h_i]["id"], edges[a_i]["id"]))
        trips.append((t_pm, edges[a_i]["id"], edges[h_i]["id"]))

    # ── Through trips: gate→gate, departures following the measured shape ─────
    hours = rng.choice(24, size=args.n_through, p=shape)
    for h in hours:
        e_in  = entries[rng.integers(len(entries))]
        e_out = exits[rng.integers(len(exits))]
        t = (h + rng.random()) * 3600
        trips.append((float(t), e_in, e_out))

    # ── Local errands: short tours, midday/evening ─────────────────────────────
    origins = rng.choice(len(edges), size=args.n_local, p=pH)
    for o_i in origins:
        d_km = np.sqrt((xs - xs[o_i]) ** 2 + (ys - ys[o_i]) ** 2) / 1000.0
        w = (A + H) * np.exp(-d_km / 0.9)
        w[o_i] = 0
        d_i = rng.choice(len(edges), p=w / w.sum())
        t0 = float(np.clip(rng.normal(13.0, 3.5), 9.0, 21.0)) * 3600
        trips.append((t0, edges[o_i]["id"], edges[d_i]["id"]))
        trips.append((t0 + rng.uniform(1200, 5400), edges[d_i]["id"], edges[o_i]["id"]))

    trips.sort()
    trips_path = SUMO_DIR / "tours.trips.xml"
    with open(trips_path, "w") as f:
        f.write("<routes>\n")
        for i, (t, fr, to) in enumerate(trips):
            f.write(f'  <trip id="t{i}" depart="{t:.1f}" from="{fr}" to="{to}"/>\n')
        f.write("</routes>\n")
    print(f"{len(trips)} trips ({args.n_tours} commute tours paired, "
          f"{args.n_through} through, {args.n_local} local tours)")

    home = sumo_home()
    out = SUMO_DIR / "candidates.rou.xml"
    res = subprocess.run(
        [str(home / "bin" / "duarouter"),
         "-n", str(NET_PATH), "--route-files", str(trips_path),
         "-o", str(out), "--ignore-errors", "--no-warnings",
         "--repair", "--remove-loops"],
        capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-1500:])
        sys.exit("duarouter failed")
    n = sum(1 for line in open(out) if "<vehicle" in line)
    print(f"Wrote {out}  ({n} routed candidates)")


if __name__ == "__main__":
    main()
