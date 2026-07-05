"""
Candidate-pool enrichment: guarantee route coverage THROUGH every measured
edge at every hour.

Run after randomTrips has produced sumo/trips.trips.xml:
  python3 enrich_candidates.py

A city-wide uniform pool spreads thin over 7 000 edges — the PFE then lacks
routes that can serve specific sensor counts (night intervals starve first).
This adds gate→gate trips forced VIA each measured edge, departure times
following the measured average daily shape, and reroutes EVERYTHING into one
candidates.rou.xml. The tour-based generator (Task 18) will supersede the
uniform base pool; the via-enrichment stays useful with any base pool.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import osmnx as ox

from build_sumo_net import sumo_home

SUMO_DIR = Path("sumo")
N_PER_EDGE = 900          # via-trips per measured edge over the day


def daily_shape() -> np.ndarray:
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
    rng = np.random.default_rng(7)
    G = ox.load_graphml("web/data/graph.graphml")
    entries, exits = [], []
    for u, v, k in G.edges(keys=True):
        if G.in_degree(u) == 0:
            entries.append(f"{u}_{v}_{k}")
        if G.out_degree(v) == 0:
            exits.append(f"{u}_{v}_{k}")

    with open("web/data/flows.json") as f:
        measured = list(json.load(f)["flows"])
    shape = daily_shape()
    print(f"{len(entries)} entry gates, {len(exits)} exit gates, "
          f"{len(measured)} measured edges")

    trips = []
    for e in measured:
        hours = rng.choice(24, size=N_PER_EDGE, p=shape)
        for h in hours:
            t = (h + rng.random()) * 3600
            trips.append((float(t), entries[rng.integers(len(entries))],
                          exits[rng.integers(len(exits))], e))
    trips.sort()

    via_path = SUMO_DIR / "via.trips.xml"
    with open(via_path, "w") as f:
        f.write("<routes>\n")
        for i, (t, fr, to, via) in enumerate(trips):
            f.write(f'  <trip id="v{i}" depart="{t:.1f}" from="{fr}" '
                    f'to="{to}" via="{via}"/>\n')
        f.write("</routes>\n")
    print(f"{len(trips)} via-trips written")

    home = sumo_home()
    res = subprocess.run(
        [str(home / "bin" / "duarouter"), "-n", str(SUMO_DIR / "net.net.xml"),
         "--route-files", f"{SUMO_DIR/'trips.trips.xml'},{via_path}",
         "-o", str(SUMO_DIR / "candidates.rou.xml"),
         "--ignore-errors", "--no-warnings", "--repair", "--remove-loops"],
        capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-1200:])
        sys.exit("duarouter failed")
    n = sum(1 for line in open(SUMO_DIR / "candidates.rou.xml")
            if "<vehicle" in line)
    print(f"Wrote sumo/candidates.rou.xml  ({n} candidates incl. enrichment)")


if __name__ == "__main__":
    main()
