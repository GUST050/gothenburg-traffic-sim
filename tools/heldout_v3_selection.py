#!/usr/bin/env python3
"""Structural candidate edges for a held-out v3 case set.

Held-out v2 passed its gate on cases that could not fail it: re-measured with
the spread metric, six of its seven ranking cases had an objective spread of
EXACTLY 0.0 s across all their schedules, and the seventh spanned 71 s inside
a 300 s indifference band (validation/b_heldout_v3_campaign.json). Two design
choices caused that, and both are fixed here rather than argued away:

1. **The edges carried no traffic.** v2 picked each case's edge by hashing
   its ID — deterministic, but blind to whether closing it changes anything.
   Half the network's residential edges have a structural daily load under
   200 vehicles, so a closure there is invisible in a whole-network
   objective. Selection is therefore ranked by the gravity/assignment
   structural load (`sumo/assignment_priors.json`), which is derived from
   the network and the OD prior, NOT from the screening proxy this set has
   to judge, and not from any SUMO run.
2. **The bands never left the peak.** A 4 h closure searched inside a 6 h
   morning band can only ever be compared against near-identical mornings.
   Case bands here span peak and off-peak hours, so start time has something
   to be right or wrong about. (That part lives in the freezer, not here.)

Two structural exclusions keep a case from degenerating the other way, into
"every schedule disqualified":

* an edge whose closure strands its own downstream edges guarantees
  unreachable-vehicle failures at every start time — excluded by requiring
  each successor to keep another predecessor;
* an edge already used by v1 or v2, or carrying a sensor, is excluded as
  before (held-out means disjoint).

Nothing here consults SUMO or the proxy, so the selection is reproducible
from tracked inputs plus the structural prior, and is recorded with their
hashes by the freezer.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NETWORK = Path("web/data/network.geojson")
ASSIGNMENT_PRIORS = Path("sumo/assignment_priors.json")
MANIFESTS = (Path("validation/monthly_proxy_manifest.json"),
             Path("validation/monthly_proxy_manifest_v2.json"))

DENSITY_RADIUS_M = 250.0
SINGLE_DETOUR_BELOW = 45
DENSE_ALTERNATIVES_ABOVE = 75
MINIMUM_EDGE_LENGTH_M = 60.0
# Quarter-hour structural load at the busiest quarter of the day. Below this
# a 4 h closure moves a handful of vehicles and the objective cannot separate
# start times; it is a floor on "there is something to measure", not a claim
# about the true count.
DISCRIMINATING_PEAK_MIN = 200.0


def _midpoint(feature: dict) -> list[float]:
    coords = feature["geometry"]["coordinates"]
    return coords[len(coords) // 2]


def _distance_m(a, b) -> float:
    return math.hypot(
        (a[0] - b[0]) * math.cos(math.radians(57.7)) * 111320.0,
        (a[1] - b[1]) * 111320.0,
    )


def used_edges() -> set[str]:
    used: set[str] = set()
    for path in MANIFESTS:
        manifest = json.loads(path.read_text())
        for case in manifest["cases"]:
            spec = case.get("closure_search_spec") or {}
            used.update(case.get("directed_edges")
                        or spec.get("directed_edges") or [])
    return used


def stranding_edges(successors: dict[str, list[str]]) -> set[str]:
    """Edges whose removal leaves a successor with no way in.

    The 2026-07-09 closure-leak investigation found a node with exactly one
    incoming connection: closing that one edge cut off everything behind it,
    so every vehicle bound there is truncated no matter when the closure
    runs. Such a case is disqualified at every start time and carries no
    ranking information.
    """
    # SUMO's successor map also contains junction-internal edges (":..."),
    # and counting those as predecessors makes every edge look redundantly
    # reachable — the first version of this check reported zero stranding
    # edges on a network that has 1 731 singly-fed ones.
    predecessors: dict[str, set[str]] = {}
    for source, targets in successors.items():
        if source.startswith(":"):
            continue
        for target in targets:
            if target.startswith(":"):
                continue
            predecessors.setdefault(target, set()).add(source)
    return {
        source for source, targets in successors.items()
        if not source.startswith(":")
        and any(predecessors.get(target, {source}) <= {source}
                for target in targets if not target.startswith(":"))
    }


def load_candidates() -> list[dict]:
    """Every eligible edge with its strata and structural load."""
    import run_scenario as rs

    geo = json.loads(NETWORK.read_text())
    features = [feature for feature in geo["features"]
                if feature["geometry"]["type"] == "LineString"]
    priors = json.loads(ASSIGNMENT_PRIORS.read_text())["flows"]
    excluded = used_edges()
    stranded = stranding_edges(rs.build_edge_graph(set()))
    midpoints = [(f["properties"]["id"], _midpoint(f)) for f in features]

    def topology_label(mid) -> str:
        count = sum(1 for _, other in midpoints
                    if _distance_m(mid, other) < DENSITY_RADIUS_M)
        if count < SINGLE_DETOUR_BELOW:
            return "single_detour"
        if count > DENSE_ALTERNATIVES_ABOVE:
            return "dense_alternatives"
        return "multiple_detours"

    rows: list[dict] = []
    for feature in features:
        properties = feature["properties"]
        edge_id = properties["id"]
        if edge_id in excluded or properties.get("sensor_id"):
            continue
        if (properties.get("length_m") or 0) < MINIMUM_EDGE_LENGTH_M:
            continue
        load = priors.get(edge_id)
        if not load:
            continue
        highway = properties.get("highway")
        if isinstance(highway, list):
            highway = highway[0]
        distance = properties.get("dist_sensor_m")
        mid = _midpoint(feature)
        rows.append({
            "edge_id": edge_id,
            "road_class": highway,
            "sensor_distance_band": (
                "near_0_250m" if distance < 250
                else "medium_250_750m" if distance < 750
                else "far_over_750m"),
            "topology": topology_label(mid),
            "structural_daily_load": round(sum(load), 1),
            "structural_peak_quarter_load": round(max(load), 1),
            "strands_downstream": edge_id in stranded,
            "length_m": round(float(properties.get("length_m") or 0.0), 1),
        })
    return rows


def rank_for(rows: list[dict], *, road_class: str, band: str | None = None,
             topology: str | None = None, discriminating: bool = True,
             exclude: set[str] = frozenset()) -> list[dict]:
    """Candidates for one stratum, busiest structural load first.

    Ties broken by a stable hash so the choice is reproducible and not an
    artifact of file order.
    """
    pool = [
        row for row in rows
        if row["road_class"] == road_class
        and row["edge_id"] not in exclude
        and (band is None or row["sensor_distance_band"] == band)
        and (topology is None or row["topology"] == topology)
        and not (discriminating and row["strands_downstream"])
        and (not discriminating
             or row["structural_peak_quarter_load"] >= DISCRIMINATING_PEAK_MIN)
    ]
    return sorted(
        pool,
        key=lambda row: (-row["structural_peak_quarter_load"],
                         hashlib.sha256(row["edge_id"].encode()).hexdigest()),
    )


def input_hashes() -> dict[str, str]:
    from traffic_sim.core.fingerprint import sha256_file
    return {str(path): sha256_file(path)
            for path in (NETWORK, ASSIGNMENT_PRIORS, *MANIFESTS)}


def main() -> None:
    rows = load_candidates()
    print(f"{len(rows)} eligible edges "
          f"({sum(row['strands_downstream'] for row in rows)} would strand "
          f"downstream traffic and are excluded from discriminating cases)")
    for road_class in ("primary", "secondary", "tertiary", "residential",
                       "unclassified"):
        pool = rank_for(rows, road_class=road_class)
        head = ", ".join(
            f"{row['edge_id']} peak={row['structural_peak_quarter_load']:.0f}"
            f" {row['sensor_distance_band'].split('_')[0]}/{row['topology']}"
            for row in pool[:3])
        print(f"{road_class:14s} {len(pool):4d} discriminating-eligible  {head}")


if __name__ == "__main__":
    main()
