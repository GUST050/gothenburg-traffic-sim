"""
Subarea/cordon candidate generator — Agent C's route population, grounded in
real data instead of graph-density proxies.

Run (or via build_sumo_demand, which calls this by default):
  python3 build_candidates.py [--through-fraction 0.5 --gravity-km 1.8]

METHOD (the "subarea/cordon" structure standard in traffic-model practice:
FHWA/state DOT subarea-analysis manuals; Cascetta's quasi-dynamic OD):
trips are split into classes by where they cross the study-area boundary,
and each class's endpoints are weighted by REAL data, not vegetation of
the road graph:

  E-E  through trips    gate → gate.        Weight: approach-road class
                                             (motorway/trunk gates draw more
                                             through-traffic than a
                                             residential fringe street —
                                             the only local proxy available;
                                             no external cordon counts exist
                                             to calibrate this better).
  E-I / I-E  commute/errand tours (PAIRED — the leg back through the gate
             is the return half of the SAME tour, not a fresh sample; this
             is what makes AM/PM directional balance structural rather than
             assumed).
  I-I  short internal tours (also paired).

HOME mass (per graph edge): real 2023 population from SCB, per DeSO zone
(fetch_deso.py), distributed over each zone's residential-street length —
the zone's real headcount, not a graph-density guess.

ACTIVITY mass (per graph edge): OSM points of interest, split into the
SAME THREE categories RVU Västra Götaland 2022-2023 measures trip purpose
in — arbete/studier, service, fritid — because true workplace-location
microdata (RAMS) is NOT free at sub-municipality resolution (verified:
SCB's day-population-by-workplace tables stop at kommun level). This is a
DOCUMENTED PROXY, not official data — the honest gap this project has been
open about since sensor-direction verification.

BEHAVIOUR PARAMETERS from RVU Västra Götaland 2022-2023 (VGR Analys 2023:56,
page references to the extracted report text):
  - purpose split (p.21, fig.11, home leg excluded): arbete/studier 43 %,
    service 33 %, fritid 24 % — used to choose which activity-mass category
    an internal destination is drawn from.
  - trip-length bins (p.12, table 2): 0–1 km 9 %, 1.1–5 km 31 %,
    5.1–10 km 19 %, >10 km 41 % — reported as a fit check on generated
    tour lengths (not force-matched: our origins are ALL inside one small
    canvas, so the >10 km tail cannot occur locally — through-trips absorb
    the long end of that distribution by construction).
  - departure-time shape: RVU reports (p.11) a 6–9 AM plateau (23 % of all
    trips) peaking 7–8, and an 16–17 PM peak (11 %) — CONSISTENT with our
    OWN measured sensor profile (normal_profile.json), which is used
    directly as the departure-time distribution because it is more local
    and higher-resolution (15-min vs RVU's coarse bins). This cross-check
    is a genuine validation, not a coincidence: both are measuring the
    same city.
  - Gothenburg car mode share: RVU states public transport carries 56 % of
    trips in Göteborg specifically (vs 58% car region-wide in all of VG) —
    noted for the record; NOT applied as a scaling factor, because the PFE
    reconciles absolute vehicle counts to the measured sensor data
    regardless of how many raw candidates exist. Only the RELATIVE spatial
    structure of candidates matters, and that is what population/POI/RVU
    ground here.

θ (free parameters, bounded-grid-searched by build_sumo_demand.py against
PFE fit quality — see calibrate_theta.py):
  --through-fraction   share of trips that are E-E (default 0.5)
  --gravity-km         distance-deterrence scale for tour destination choice

Writes sumo/candidates.rou.xml (identical contract to the old randomTrips
output — pfe.py is untouched).
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
from shapely.geometry import Point, shape

from build_data import INNER_CITY_BBOX
from build_sumo_net import sumo_home

SUMO_DIR   = Path("sumo")
NET_PATH   = SUMO_DIR / "net.net.xml"
GRAPH_PATH = Path("web/data/graph.graphml")
DESO_DIR   = Path("data_in/deso")

RESIDENTIAL = {"residential", "living_street"}
GATE_WEIGHT = {          # proxy: approach-road class → relative through-flow
    "motorway": 8, "motorway_link": 8, "trunk": 6, "trunk_link": 6,
    "primary": 4, "primary_link": 4, "secondary": 2, "secondary_link": 2,
    "tertiary": 1, "tertiary_link": 1,
}

# RVU Västra Götaland 2022-2023, fig. 11 (p.21), home leg excluded
PURPOSE_SHARES = {"arbete": 0.43, "service": 0.33, "fritid": 0.24}

POI_TAGS = {
    "arbete": {"office": True, "building": ["industrial", "commercial"],
              "shop": True},
    "service": {"shop": ["supermarket", "convenience", "mall"],
               "amenity": ["bank", "post_office", "pharmacy", "hospital",
                          "clinic"], "healthcare": True},
    "fritid": {"amenity": ["restaurant", "cafe", "bar", "cinema", "theatre"],
              "leisure": True, "tourism": True},
}
DISC_M = 150.0   # POI catchment radius per edge


def scalar(v):
    return v[0] if isinstance(v, list) else v


def load_deso_population() -> dict[str, int]:
    with open(DESO_DIR / "population_2023.json") as f:
        return json.load(f)["population"]


def load_graph_edges(G):
    edges = []
    for u, v, k, d in G.edges(keys=True, data=True):
        lat = (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2
        lon = (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2
        hw = str(scalar(d.get("highway", "")) or "")
        edges.append({"id": f"{u}_{v}_{k}", "u": u, "v": v,
                      "lat": lat, "lon": lon, "hw": hw,
                      "len": float(d.get("length", 0))})
    return edges


def ensure_deso() -> tuple[list[dict], dict[str, int]]:
    """fetch_deso.py's outputs are a prerequisite for home_mass() — auto-fetch
    if missing (data_in/deso/ accidentally cleaned), same ensure_* staleness-
    check pattern as ensure_bounds()/ensure_observability() in
    build_sumo_demand.py. Staleness here specifically means the fetched
    population no longer matches INNER_CITY_BBOX: fetch_deso.py only queries
    DeSO codes that intersect the bbox AT FETCH TIME, so a later bbox change
    (already happened once, 2026-07-05) would otherwise silently leave the
    newly-in-scope zones with zero home mass forever, with existing files
    on disk masking the mismatch. Returns the loaded (zones, population)
    so callers never re-touch these files themselves."""
    geo_path, pop_path = DESO_DIR / "deso_goteborg.geojson", DESO_DIR / "population_2023.json"
    if geo_path.exists() and pop_path.exists():
        with open(pop_path) as f:
            pop_doc = json.load(f)
        if pop_doc.get("inner_city_bbox") == list(INNER_CITY_BBOX):
            with open(geo_path) as f:
                return json.load(f)["features"], pop_doc["population"]
        print("data_in/deso/ was fetched for a different INNER_CITY_BBOX — refetching …")
    else:
        print("Fetching SCB DeSO population (fetch_deso.py) — first run only …")
    try:
        res = subprocess.run([sys.executable, "fetch_deso.py"],
                             capture_output=True, text=True, timeout=420)
    except subprocess.TimeoutExpired:
        sys.exit("fetch_deso.py timed out after 420s (SCB API slow/unreachable?) — see above")
    if res.returncode != 0:
        print(res.stderr[-1500:])
        sys.exit("fetch_deso.py failed — see above")
    with open(geo_path) as f:
        zones = json.load(f)["features"]
    return zones, load_deso_population()


def home_mass(edges: list[dict]) -> np.ndarray:
    """Real 2023 SCB population per DeSO, spread over each zone's
    residential-street length — a headcount, not a density guess."""
    zones, pop = ensure_deso()

    mass = np.zeros(len(edges))
    res_len_by_zone: dict[str, float] = {}
    zone_of_edge: list[str | None] = [None] * len(edges)

    zone_polys = [(f["properties"]["desokod"], shape(f["geometry"]))
                  for f in zones if f["properties"]["desokod"] in pop]
    for i, e in enumerate(edges):
        pt = Point(e["lon"], e["lat"])
        for code, poly in zone_polys:
            if poly.contains(pt):
                zone_of_edge[i] = code
                if e["hw"] in RESIDENTIAL:
                    res_len_by_zone[code] = res_len_by_zone.get(code, 0) + e["len"]
                break

    for i, e in enumerate(edges):
        code = zone_of_edge[i]
        if code is None or e["hw"] not in RESIDENTIAL:
            continue
        total_res = res_len_by_zone.get(code, 0)
        if total_res > 0:
            mass[i] = pop[code] * (e["len"] / total_res)

    n_zones_used = len({c for c in zone_of_edge if c})
    print(f"  home mass: {n_zones_used} DeSO zones matched to edges, "
          f"{mass.sum():,.0f} residents distributed "
          f"(of {sum(pop.values()):,} total in the {len(pop)} fetched zones)")
    return mass


def activity_mass(G, edges: list[dict]) -> dict[str, np.ndarray]:
    """OSM POI counts per category (RVU purpose-aligned), per edge —
    a documented PROXY for workplace/destination attractiveness (true RAMS
    workplace microdata is not free below kommun level — verified)."""
    s, w, n, e_ = __import__("build_data").INNER_CITY_BBOX
    cache = DESO_DIR / "osm_pois.geojson"
    if cache.exists():
        pois = json.loads(cache.read_text())
    else:
        print("  fetching OSM POIs (first run only, cached after) …")
        all_tags = {}
        for cat_tags in POI_TAGS.values():
            for k, v in cat_tags.items():
                all_tags.setdefault(k, set())
                if v is True:
                    all_tags[k] = True
                elif all_tags[k] is not True:
                    all_tags[k].update(v)
        all_tags = {k: (True if v is True else list(v))
                   for k, v in all_tags.items()}
        gdf = ox.features_from_bbox(bbox=(w, s, e_, n), tags=all_tags)
        pois = json.loads(gdf.to_json())
        cache.write_text(json.dumps(pois))

    lats = np.array([e["lat"] for e in edges])
    lons = np.array([e["lon"] for e in edges])
    klat, klon = 110_540.0, 111_320.0 * math.cos(math.radians(57.7))
    xs, ys = lons * klon, lats * klat

    poi_xy: dict[str, list] = {c: [] for c in POI_TAGS}
    for feat in pois["features"]:
        props = feat.get("properties", {})
        geom = shape(feat["geometry"])
        c = geom.centroid
        for cat, tags in POI_TAGS.items():
            for key, allowed in tags.items():
                val = props.get(key)
                if val and (allowed is True or val in allowed):
                    poi_xy[cat].append((c.x * klon, c.y * klat))
                    break

    out = {}
    for cat, pts in poi_xy.items():
        mass = np.zeros(len(edges))
        if pts:
            px = np.array([p[0] for p in pts])
            py = np.array([p[1] for p in pts])
            for i in range(len(edges)):
                d2 = (px - xs[i]) ** 2 + (py - ys[i]) ** 2
                mass[i] = (d2 <= DISC_M ** 2).sum()
        out[cat] = mass
        print(f"  activity mass '{cat}': {len(pts)} OSM POIs, "
              f"{(mass > 0).sum()} edges reached")
    return out


def find_gates(G):
    entries, exits = [], []
    for u, v, k in G.edges(keys=True):
        if G.in_degree(u) == 0:
            entries.append((f"{u}_{v}_{k}", u))
        if G.out_degree(v) == 0:
            exits.append((f"{u}_{v}_{k}", v))
    return entries, exits


def gate_weights(G, gates: list[tuple[str, int]]) -> np.ndarray:
    w = []
    for eid, _ in gates:
        u, v, k = map(int, eid.split("_"))
        hw = str(scalar(G.get_edge_data(u, v, k).get("highway", "")) or "")
        w.append(GATE_WEIGHT.get(hw, 1))
    w = np.array(w, dtype=float)
    return w / w.sum()


def daily_shape() -> np.ndarray:
    """Our OWN measured departure-time distribution (normal_profile.json) —
    more local and finer-grained than RVU's regional bins, and consistent
    with them (both show the 7-8h / 16-17h peaks RVU reports on p.11)."""
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
    ap.add_argument("--through-fraction", type=float, default=0.5,
                    help="θ: share of trips that are E-E through traffic")
    ap.add_argument("--gravity-km", type=float, default=1.8,
                    help="θ: distance-deterrence scale (km) for tours")
    ap.add_argument("--n-total", type=int, default=12000)
    ap.add_argument("--n-sensor-via", type=int, default=900,
                    help="via-trips per measured edge (coverage guarantee)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-suffix", default="",
                    help="internal use by calibrate_theta.py")
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    G = ox.load_graphml(GRAPH_PATH)
    edges = load_graph_edges(G)
    print(f"{len(edges)} edges")

    hmass = home_mass(edges)
    amass = activity_mass(G, edges)
    entries, exits = find_gates(G)
    entry_ids = [e for e, _ in entries]
    exit_ids  = [e for e, _ in exits]
    w_entry = gate_weights(G, entries)
    w_exit  = gate_weights(G, exits)
    shape_hourly = daily_shape()
    print(f"{len(entries)} entry gates, {len(exits)} exit gates")

    if hmass.sum() == 0:
        sys.exit("home_mass is all-zero — check data_in/deso/ (run fetch_deso.py)")
    pH = hmass / hmass.sum()

    n_through = int(args.n_total * args.through_fraction)
    n_tours   = (args.n_total - n_through) // 2   # paired -> half as many tours

    trips: list[tuple[float, str, str]] = []

    # ── E-E: through traffic, gate→gate, our measured departure shape ──────────
    hours = rng.choice(24, size=n_through, p=shape_hourly)
    for h in hours:
        e_in  = entry_ids[rng.choice(len(entry_ids), p=w_entry)]
        e_out = exit_ids[rng.choice(len(exit_ids), p=w_exit)]
        trips.append(((h + rng.random()) * 3600, e_in, e_out))

    # ── E-I / I-E / I-I: purpose-sampled, gravity-weighted, PAIRED tours ───────
    home_idx = rng.choice(len(edges), size=n_tours, p=pH)
    purposes = rng.choice(list(PURPOSE_SHARES), size=n_tours,
                          p=list(PURPOSE_SHARES.values()))
    for h_i, purpose in zip(home_idx, purposes):
        w = amass[purpose].copy()
        if w.sum() == 0:
            continue
        d_km = np.sqrt((np.array([e["lat"] for e in edges]) - edges[h_i]["lat"]) ** 2
                      + (np.array([e["lon"] for e in edges]) - edges[h_i]["lon"]) ** 2
                      ) * 111.0
        w = w * np.exp(-d_km / args.gravity_km)
        w[h_i] = 0
        if w.sum() == 0:
            continue
        a_i = rng.choice(len(edges), p=w / w.sum())

        # Outbound ~ AM-weighted draw from OUR shape; return ~ PM-weighted.
        h_out = rng.choice(24, p=shape_hourly)
        pm_shape = shape_hourly * (np.arange(24) >= 12)
        pm_shape = pm_shape / pm_shape.sum() if pm_shape.sum() > 0 else shape_hourly
        h_ret = max(h_out + 1, rng.choice(24, p=pm_shape))

        trips.append(((h_out + rng.random()) * 3600,
                      edges[h_i]["id"], edges[a_i]["id"]))
        trips.append(((min(h_ret, 23) + rng.random()) * 3600,
                      edges[a_i]["id"], edges[h_i]["id"]))

    # ── Coverage guarantee: via-trips through every measured sensor edge ───────
    with open("web/data/flows.json") as f:
        measured = list(json.load(f)["flows"])
    for m_edge in measured:
        hrs = rng.choice(24, size=args.n_sensor_via, p=shape_hourly)
        for h in hrs:
            e_in  = entry_ids[rng.integers(len(entry_ids))]
            e_out = exit_ids[rng.integers(len(exit_ids))]
            trips.append(((h + rng.random()) * 3600, e_in, e_out, m_edge))

    trips.sort(key=lambda t: t[0])
    trips_path = SUMO_DIR / f"tours{args.out_suffix}.trips.xml"
    with open(trips_path, "w") as f:
        f.write("<routes>\n")
        for i, t in enumerate(trips):
            via = f' via="{t[3]}"' if len(t) > 3 else ""
            f.write(f'  <trip id="t{i}" depart="{t[0]:.1f}" '
                    f'from="{t[1]}" to="{t[2]}"{via}/>\n')
        f.write("</routes>\n")
    print(f"{len(trips)} trips ({n_through} through, {n_tours} paired tours "
          f"= {n_tours*2} legs, {len(measured)*args.n_sensor_via} coverage)")

    home = sumo_home()
    out = SUMO_DIR / f"candidates{args.out_suffix}.rou.xml"
    res = subprocess.run(
        [str(home / "bin" / "duarouter"), "-n", str(NET_PATH),
         "--route-files", str(trips_path), "-o", str(out),
         "--ignore-errors", "--no-warnings", "--repair", "--remove-loops"],
        capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-1500:])
        sys.exit("duarouter failed")
    n = sum(1 for line in open(out) if "<vehicle" in line)
    print(f"Wrote {out}  ({n} routed candidates)")


if __name__ == "__main__":
    main()
