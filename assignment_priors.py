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
import math
import os
from pathlib import Path

import networkx as nx
import numpy as np
import osmnx as ox

from build_candidates import (PURPOSE_LENGTH_SCALE, PURPOSE_SHARES_WEEKDAY,
                              activity_location_fields, deterrence_weights,
                              find_gates, gate_latlon, gate_weights,
                              gravity_distance_km, home_location_field,
                              load_graph_edges, load_sumo_routing_data,
                              routable_graph)
from build_sumo_net import parse_speed_ms
from demand.intake import load_direction_split, load_sensor_edges
from traffic_sim.demand.cache import cache_key

GRAPH_PATH = Path("web/data/graph.graphml")
OUT_PATH   = Path("sumo/assignment_priors.json")
WEIGHT     = 0.15   # << direction/corridor priors' typical 1/band weight — stays weak
DEFAULT_N_SAMPLES = 40000
# Match build_sumo_demand.py / build_candidates.py.  The old 2.6 km default
# belonged to the pre-conditional sampler and silently made the assignment
# field describe a broader OD distribution than the candidate pool it bounds.
DEFAULT_GRAVITY_KM = 1.8
DEFAULT_THROUGH_FRACTION = 0.5
DEFAULT_SEED = 42
DEFAULT_N_VARIANTS = 10
DEFAULT_PERTURB_SIGMA = 0.35
DEFAULT_CROSS_FRACTION = 0.3
DEFAULT_GRAVITY_ALPHA = 1.5

# The prior is a derived artifact used to choose candidate support as well as
# to bound PFE.  A plain "file exists" check is unsafe: an older field can be
# structurally incompatible with today's network, endpoint data, or routing
# costs while looking syntactically valid.  Bump this whenever the loading
# contract itself changes.
ASSIGNMENT_PRIOR_SCHEMA_VERSION = 2


def assignment_prior_input_key(
    n_samples: int = DEFAULT_N_SAMPLES,
    gravity_km: float = DEFAULT_GRAVITY_KM,
    through_fraction: float = DEFAULT_THROUGH_FRACTION,
    cross_fraction: float = DEFAULT_CROSS_FRACTION,
    gravity_alpha: float = DEFAULT_GRAVITY_ALPHA,
    seed: int = DEFAULT_SEED,
    n_variants: int = DEFAULT_N_VARIANTS,
    perturb_sigma: float = DEFAULT_PERTURB_SIGMA,
) -> str:
    """Return the exact-input identity for the assignment-prior artifact.

    This intentionally fingerprints both the network and the endpoint fields
    used by the production candidate generator.  Missing optional open-data
    files are represented in the key too, so materialising an OSM fallback or
    later supplying official buildings causes one safe rebuild rather than a
    silent blend of old and new spatial assumptions.
    """
    return cache_key(
        {
            "assignment_prior_schema_version": ASSIGNMENT_PRIOR_SCHEMA_VERSION,
            "n_samples": n_samples,
            "gravity_km": gravity_km,
            "through_fraction": through_fraction,
            "cross_fraction": cross_fraction,
            "gravity_alpha": gravity_alpha,
            "seed": seed,
            "n_variants": n_variants,
            "perturb_sigma": perturb_sigma,
        },
        {
            "sumo_network": Path("sumo/net.net.xml"),
            "graph": Path("web/data/graph.graphml"),
            "map_network": Path("web/data/network.geojson"),
            "flows": Path("web/data/flows.json"),
            "normal_profile": Path("web/data/normal_profile.json"),
            "direction_split": Path("sumo/direction_split.json"),
            "observability": Path("web/data/observability.json"),
            "learned_priors": Path("sumo/prior_flows.json"),
            "population": Path("data_in/deso/population_2023.json"),
            "deso": Path("data_in/deso/deso_goteborg.geojson"),
            "official_buildings": Path("data_in/deso/buildings.geojson"),
            "osm_buildings": Path("data_in/deso/osm_buildings.geojson"),
            "poi": Path("data_in/deso/osm_pois.geojson"),
        },
        {
            "assignment_priors": Path("assignment_priors.py"),
            "candidate_generation": Path("build_candidates.py"),
            "endpoint_locations": Path("demand/locations.py"),
            "demand_intake": Path("demand/intake.py"),
            "candidate_cache": Path("traffic_sim/demand/cache.py"),
        },
    )


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


def add_path_load(
    load: dict[str, float], path: list, base_time_key: dict[tuple, object]
) -> None:
    """Add one routed path to the specific parallel edges used for routing.

    ``build_perturbed_variants`` is deliberately a simple DiGraph, so each
    node pair has exactly one selected travel-time edge.  Preserve that edge
    key from the original MultiDiGraph rather than loading every parallel
    edge between the same two nodes.
    """
    for u, v in zip(path, path[1:]):
        key = base_time_key[(u, v)]
        edge_id = f"{u}_{v}_{key}"
        load[edge_id] = load.get(edge_id, 0) + 1


def fastest_parallel_edge_times(
    G, edge_costs: dict[str, float] | None = None,
) -> tuple[dict[tuple, float], dict[tuple, object]]:
    """Collapse a MultiDiGraph to one travel-time edge per (u, v) node pair.

    A (u, v) pair with parallel edges (52 in this graph, e.g. a short slip
    lane alongside a through lane) collapses to ONE travel-time edge by
    design (add_path_load's docstring: the perturbed routing graphs are
    simple DiGraphs, one edge per pair). Found in a review 2026-07-10:
    keeping whichever edge the iterator happened to visit LAST silently
    discarded the faster parallel edge in a real case (0.49s kept-vs-
    discarded 1.53s, a 3x difference) — a rational driver, and any
    shortest-path routing, picks the FASTER physical link, not an
    arbitrary one. Keeps the minimum (fastest) travel time among parallel
    edges instead.

    ``edge_costs`` is SUMO's published lane-level free-flow travel-time table.
    Production must prefer it over GraphML tags: the candidate generator and
    duarouter use the SUMO network, so a GraphML-only speed assumption would
    load a route the actual simulator does not consider cheapest.  The
    GraphML fallback remains for tiny synthetic tests and legacy callers.
    """
    base_time: dict[tuple, float] = {}
    base_time_key: dict[tuple, object] = {}
    for u, v, k, d in G.edges(keys=True, data=True):
        edge_id = f"{u}_{v}_{k}"
        if edge_costs is None:
            speed = parse_speed_ms(d)
            t = d.get("length", 1.0) / max(speed, 1.0)
        else:
            try:
                t = float(edge_costs[edge_id])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"missing or invalid SUMO routing cost for {edge_id}") from exc
            if not math.isfinite(t) or t <= 0:
                raise ValueError(
                    f"missing or invalid SUMO routing cost for {edge_id}")
        if (u, v) not in base_time or t < base_time[(u, v)]:
            base_time[(u, v)] = t
            base_time_key[(u, v)] = k
    return base_time, base_time_key


def compute_assignment_load(
    n_samples: int = DEFAULT_N_SAMPLES,
    gravity_km: float = DEFAULT_GRAVITY_KM,
    through_fraction: float = DEFAULT_THROUGH_FRACTION,
    cross_fraction: float = DEFAULT_CROSS_FRACTION,
    gravity_alpha: float = DEFAULT_GRAVITY_ALPHA,
    seed: int = DEFAULT_SEED,
    n_variants: int = DEFAULT_N_VARIANTS,
    perturb_sigma: float = DEFAULT_PERTURB_SIGMA,
) -> dict[str, float]:
    """Compute the structural gravity/stochastic-multipath edge load.

    This intentionally depends only on population/activity/gates/network
    structure, not measured sensor *counts*. LOSO can therefore run it once
    and recalibrate only the measured scale factor per fold.

    Its OD classes, endpoint fields and deterrence kernel deliberately match
    the production candidate generator.  Earlier code pre-dated the real
    building/POI access fields and cross-boundary paired tours: it loaded
    only one home->activity leg on the raw GraphML graph.  That made the
    supposedly structural prior disagree with the route population it both
    bounds and later helps weight at the gates.
    """
    if n_samples <= 0:
        raise ValueError("n_samples must be positive")
    if gravity_km <= 0:
        raise ValueError("gravity_km must be positive")
    if not 0 <= through_fraction <= 1:
        raise ValueError("through_fraction must be within [0, 1]")
    if not 0 <= cross_fraction <= 1:
        raise ValueError("cross_fraction must be within [0, 1]")
    if gravity_alpha < 0:
        raise ValueError("gravity_alpha must be non-negative")
    if n_variants < 1:
        raise ValueError("n_variants must be at least one")

    rng = np.random.default_rng(seed)

    raw_graph = ox.load_graphml(GRAPH_PATH)
    sumo_edge_ids, routing_costs = load_sumo_routing_data()
    G = routable_graph(raw_graph, sumo_edge_ids)
    base_time, base_time_key = fastest_parallel_edge_times(G, routing_costs)

    print(f"Building {n_variants} randomly-perturbed travel-time graphs "
          f"(stochastic multipath stand-in) …")
    variants = build_perturbed_variants(base_time, n_variants,
                                        perturb_sigma, rng)

    edges = load_graph_edges(G, sumo_edge_ids)
    edge_ids = [e["id"] for e in edges]
    measured = [edge_id for edge_ids_at_sensor in load_sensor_edges().values()
                for edge_id in edge_ids_at_sensor]
    missing_sensor_edges = sorted(set(measured) - set(edge_ids))
    if missing_sensor_edges:
        raise ValueError("measured sensor edges missing from SUMO routing graph: "
                         + ", ".join(missing_sensor_edges))

    print(f"{len(edges)} SUMO-routable edges, computing home/activity mass …")
    home_field = home_location_field(G, edges, measured)
    activity_fields = activity_location_fields(G, edges, measured)
    hmass = home_field.mass
    amass = {purpose: field.mass for purpose, field in activity_fields.items()}
    if hmass.sum() == 0:
        raise SystemExit("home location mass is zero — run fetch_deso.py first")
    missing_purposes = [purpose for purpose in PURPOSE_SHARES_WEEKDAY
                        if amass.get(purpose) is None or amass[purpose].sum() <= 0]
    if missing_purposes:
        raise SystemExit("activity location mass is zero for: "
                         + ", ".join(missing_purposes))
    pH = hmass / hmass.sum()

    lats = np.array([e["lat"] for e in edges])
    lons = np.array([e["lon"] for e in edges])

    entries, exits = find_gates(G, sumo_edge_ids)
    # Gates are synthetic endpoints too.  Match the candidate invariant that
    # a counter link is evidence, never an invented home/activity/gate address.
    measured_set = set(measured)
    entries = [(edge_id, node_id) for edge_id, node_id in entries
               if edge_id not in measured_set]
    exits = [(edge_id, node_id) for edge_id, node_id in exits
             if edge_id not in measured_set]
    if not entries or not exits:
        raise ValueError("sensor-edge exclusion removed every assignment gate")
    w_entry = gate_weights(G, entries)
    w_exit  = gate_weights(G, exits)
    entry_ids = [edge_id for edge_id, _ in entries]
    exit_ids = [edge_id for edge_id, _ in exits]
    entry_u_nodes = [int(edge_id.split("_")[0]) for edge_id in entry_ids]
    exit_v_nodes = [int(edge_id.split("_")[1]) for edge_id in exit_ids]
    entry_lats, entry_lons = gate_latlon(G, entries)
    exit_lats, exit_lons = gate_latlon(G, exits)

    # ``n_samples`` counts directed route legs, just as the candidate pool
    # does.  Pairing therefore changes OD composition without changing the
    # number of shortest-path computations or the assignment runtime.
    requested_through = int(n_samples * through_fraction)
    n_tours = (n_samples - requested_through) // 2
    n_through = n_samples - 2 * n_tours
    n_cross = int(n_tours * cross_fraction)
    n_internal = n_tours - n_cross
    n_ei = n_cross // 2
    n_ie = n_cross - n_ei
    print(f"Sampling {n_through} E-E + {n_internal} I-I + {n_ei} E-I + "
          f"{n_ie} I-E paired tours ({n_samples} directed legs) and loading "
          "shortest paths …")
    load = {eid: 0.0 for eid in edge_ids}
    n_ok = n_nopath = 0

    variant_idx = rng.integers(len(variants), size=n_samples)
    vi = 0

    def load_leg(origin_edge: str, destination_edge: str,
                 start_node: int, end_node: int,
                 *, origin_in_path: bool, destination_in_path: bool) -> None:
        """Load one candidate-equivalent directed leg into the field."""
        nonlocal vi, n_ok, n_nopath
        try:
            path = nx.shortest_path(variants[variant_idx[vi]], start_node,
                                    end_node, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            n_nopath += 1
        else:
            n_ok += 1
            if not origin_in_path:
                load[origin_edge] += 1
            add_path_load(load, path, base_time_key)
            if not destination_in_path:
                load[destination_edge] += 1
        finally:
            vi += 1

    def choose_activity(origin_lat: float, origin_lon: float,
                        purpose: str, *, exclude_index: int | None = None) -> int | None:
        d_km = gravity_distance_km(lats, lons, origin_lat, origin_lon)
        weights = amass[purpose] * deterrence_weights(
            d_km, gravity_km * PURPOSE_LENGTH_SCALE[purpose], gravity_alpha)
        if exclude_index is not None:
            weights = weights.copy()
            weights[exclude_index] = 0.0
        total = weights.sum()
        return (int(rng.choice(len(edges), p=weights / total))
                if total > 0 else None)

    def choose_gate(weights: np.ndarray, gate_lats: np.ndarray,
                    gate_lons: np.ndarray, origin_lat: float,
                    origin_lon: float, *, distance_weighted: bool) -> int | None:
        draw_weights = weights
        if distance_weighted:
            d_km = gravity_distance_km(gate_lats, gate_lons, origin_lat, origin_lon)
            draw_weights = weights * deterrence_weights(
                d_km, gravity_km, gravity_alpha)
        total = draw_weights.sum()
        return (int(rng.choice(len(draw_weights), p=draw_weights / total))
                if total > 0 else None)

    # E-E: gate -> gate.  Starting at the entry's u-node and ending at the
    # exit's v-node makes both boundary edges part of the routed path.
    gi = rng.choice(len(entries), size=n_through, p=w_entry)
    go = rng.choice(len(exits),  size=n_through, p=w_exit)
    for i, o in zip(gi, go):
        load_leg(entry_ids[i], exit_ids[o], entry_u_nodes[i], exit_v_nodes[o],
                 origin_in_path=True, destination_in_path=True)

    # I-I: home -> purpose-specific activity -> same home.  The reverse leg
    # is essential on a directed graph; omitting it was the old field's most
    # consequential directional distortion.
    for _ in range(n_internal):
        h_i = int(rng.choice(len(edges), p=pH))
        purpose = str(rng.choice(list(PURPOSE_SHARES_WEEKDAY),
                                 p=list(PURPOSE_SHARES_WEEKDAY.values())))
        a_i = choose_activity(lats[h_i], lons[h_i], purpose, exclude_index=h_i)
        if a_i is None:
            continue
        load_leg(edges[h_i]["id"], edges[a_i]["id"],
                 edges[h_i]["v"], edges[a_i]["u"],
                 origin_in_path=False, destination_in_path=False)
        load_leg(edges[a_i]["id"], edges[h_i]["id"],
                 edges[a_i]["v"], edges[h_i]["u"],
                 origin_in_path=False, destination_in_path=False)

    # E-I: entry gate -> activity, then activity -> independently sampled
    # exit gate.  This is the same physical tour class as build_candidates.
    for _ in range(n_ei):
        g_in = int(rng.choice(len(entries), p=w_entry))
        purpose = str(rng.choice(list(PURPOSE_SHARES_WEEKDAY),
                                 p=list(PURPOSE_SHARES_WEEKDAY.values())))
        a_i = choose_activity(entry_lats[g_in], entry_lons[g_in], purpose)
        g_out = (choose_gate(w_exit, exit_lats, exit_lons,
                             entry_lats[g_in], entry_lons[g_in],
                             distance_weighted=False)
                 if a_i is not None else None)
        if a_i is None or g_out is None:
            continue
        load_leg(entry_ids[g_in], edges[a_i]["id"],
                 entry_u_nodes[g_in], edges[a_i]["u"],
                 origin_in_path=True, destination_in_path=False)
        load_leg(edges[a_i]["id"], exit_ids[g_out],
                 edges[a_i]["v"], exit_v_nodes[g_out],
                 origin_in_path=False, destination_in_path=True)

    # I-E: home -> exit gate, then independently sampled entry gate -> home.
    # These external legs deliberately retain their own category, as in the
    # candidate generator; no activity-purpose label is fabricated for them.
    for _ in range(n_ie):
        h_i = int(rng.choice(len(edges), p=pH))
        g_out = choose_gate(w_exit, exit_lats, exit_lons, lats[h_i], lons[h_i],
                            distance_weighted=True)
        g_in = int(rng.choice(len(entries), p=w_entry)) if g_out is not None else None
        if g_out is None or g_in is None:
            continue
        load_leg(edges[h_i]["id"], exit_ids[g_out],
                 edges[h_i]["v"], exit_v_nodes[g_out],
                 origin_in_path=False, destination_in_path=True)
        load_leg(entry_ids[g_in], edges[h_i]["id"],
                 entry_u_nodes[g_in], edges[h_i]["u"],
                 origin_in_path=True, destination_in_path=False)

    print(f"  {n_ok}/{n_samples} directed legs routed, {n_nopath} had no path (disconnected "
          f"fringe — expected at a clipped bbox)")
    return load


def sensor_edge_metadata() -> tuple[dict[str, str | None], dict[str, str]]:
    """Return measured edge metadata keyed by edge id.

    level is needed for Total-sensor direction splitting; edge_to_sensor is
    needed by LOSO to exclude every edge belonging to the held-out station.
    """
    with open("web/data/network.geojson") as f:
        geo = json.load(f)
    level = {}
    edge_to_sensor = {}
    for feat in geo["features"]:
        props = feat["properties"]
        sensor_id = props.get("sensor_id")
        if not sensor_id:
            continue
        eid = props["id"]
        level[eid] = props.get("level")
        edge_to_sensor[eid] = str(sensor_id)
    return level, edge_to_sensor


def calibrate_assignment_priors(
    load: dict[str, float],
    n_samples: int,
    exclude_sensor: str | None = None,
    write_path: Path | None = OUT_PATH,
    input_key: str | None = None,
) -> dict:
    """Scale structural assignment load to measured flows and format priors.

    exclude_sensor removes all measured edges belonging to that sensor from
    the scale fit. Passing None preserves the production behavior.
    """
    with open("web/data/flows.json") as f:
        flows = json.load(f)["flows"]
    level, edge_to_sensor = sensor_edge_metadata()

    # Total (two-way) sensors: split the raw count by the ESTIMATED direction
    # share, same as build_sumo_demand.build_targets/write_counts — not a
    # blind 50/50, which was a real, avoidable divergence from that fix
    # (found 2026-07-06 alongside the sensor-107 reporting-artifact review).
    est_shares = load_direction_split()
    x_load, y_meas = [], []
    for eid, lv in level.items():
        if exclude_sensor is not None and edge_to_sensor.get(eid) == str(exclude_sensor):
            continue
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
    if x_load.size == 0:
        raise RuntimeError(
            "assignment loading did not reach any measured edge; refusing to "
            "publish a prior with an undefined scale")
    scale, fit_r2 = robust_scale(x_load, y_meas)
    if not math.isfinite(scale):
        raise RuntimeError(
            "assignment-prior scale is not finite; refusing to publish it")
    suffix = f" excluding sensor {exclude_sensor}" if exclude_sensor is not None else ""
    print(f"Scale factor (robust median ratio) on {len(x_load)} measured "
          f"edges{suffix}: scale={scale:.3f} veh/day per loading-unit, "
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

    data = {"weight": WEIGHT, "scale_veh_per_day": scale,
            "fit_r2": round(fit_r2, 3), "n_samples": n_samples,
            "flows": out}
    if input_key is not None:
        data.update({"schema_version": ASSIGNMENT_PRIOR_SCHEMA_VERSION,
                     "input_key": input_key})
    if write_path is not None:
        write_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = write_path.with_name(
            f".{write_path.name}.{os.getpid()}.tmp")
        try:
            with open(temporary, "w") as f:
                json.dump(data, f)
            os.replace(temporary, write_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        print(f"Wrote {write_path}  ({len(out)} previously-unconstrained edges "
              f"now carry a weak gravity-assignment prior)")
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n-samples", type=int, default=DEFAULT_N_SAMPLES,
                    help="OD pairs sampled for the stochastic loading")
    ap.add_argument("--gravity-km", type=float, default=DEFAULT_GRAVITY_KM)
    ap.add_argument("--through-fraction", type=float, default=DEFAULT_THROUGH_FRACTION,
                    help="same θ as build_candidates.py — E-E through trips "
                        "are a first-class part of the loaded field, not "
                        "just an afterthought (this omission was the actual "
                        "bug behind the first attempt's bad fit: Skånegatan/"
                        "Valhallagatan are through-corridors to Örgrytevägen/"
                        "Söderleden, not just home-activity destinations)")
    ap.add_argument("--cross-fraction", type=float, default=DEFAULT_CROSS_FRACTION,
                    help="share of paired tour templates using a study-boundary "
                    "gate (E-I/I-E), matching build_sumo_demand.py's candidate "
                    "generator default")
    ap.add_argument("--gravity-alpha", type=float, default=DEFAULT_GRAVITY_ALPHA,
                    help="Tanner/gamma deterrence shape matching build_candidates.py")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--n-variants", type=int, default=DEFAULT_N_VARIANTS,
                    help="number of randomly-perturbed weight graphs — the "
                        "lightweight stand-in for Dial's stochastic "
                        "multipath loading (see module docstring, bug #2)")
    ap.add_argument("--perturb-sigma", type=float, default=DEFAULT_PERTURB_SIGMA,
                    help="lognormal sigma for per-edge travel-time jitter")
    args = ap.parse_args()
    load = compute_assignment_load(
        n_samples=args.n_samples,
        gravity_km=args.gravity_km,
        through_fraction=args.through_fraction,
        cross_fraction=args.cross_fraction,
        gravity_alpha=args.gravity_alpha,
        seed=args.seed,
        n_variants=args.n_variants,
        perturb_sigma=args.perturb_sigma,
    )
    # Compute after the endpoint fields have had a chance to materialise an
    # OSM fallback.  The written key must describe what was actually used,
    # not a pre-build view in which that input happened to be absent.
    input_key = assignment_prior_input_key(
        n_samples=args.n_samples,
        gravity_km=args.gravity_km,
        through_fraction=args.through_fraction,
        cross_fraction=args.cross_fraction,
        gravity_alpha=args.gravity_alpha,
        seed=args.seed,
        n_variants=args.n_variants,
        perturb_sigma=args.perturb_sigma,
    )
    calibrate_assignment_priors(load, args.n_samples, input_key=input_key)


if __name__ == "__main__":
    main()
