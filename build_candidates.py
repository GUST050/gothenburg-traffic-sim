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
    5.1–10 km 19 %, >10 km 41 % — a fit check on generated tour lengths
    (calibrate_theta.py, build_candidates.trip_length_fit — NOT force-
    matched, since the true OD isn't locally identifiable either); the fit
    check renormalizes RVU's three SHORT bins and compares only those.
    HARD CEILING, verified 2026-07-08 by direct measurement: this graph's
    own diameter — gate-to-gate (E-E) AND gate-to-interior (E-I/I-E) both
    — never exceeds ~7.8 km. An EARLIER version of this comment claimed
    E-E through-trips "absorb the long end of the distribution by
    construction" — that was never actually measured and is FALSE; E-E's
    own gate-to-gate span maxes out at the same ~7.8 km. RVU's 5.1-10km/
    >10km bins (51% of all real trips combined) describe a WHOLE-REGION
    survey including long regional commutes (e.g. Kungsbacka/Partille into
    the city) — most of a trip like that happens on roads outside this
    graph entirely; only the last couple of km inside the inner-city
    canvas is ever generated here. Comparing generated-tour length against
    RVU's full regional distribution is therefore comparing two different
    quantities (distance-within-this-graph vs. real door-to-door
    distance), not a mistuned parameter — no θ value can close this gap.
    E-I/I-E tours (added 2026-07-08 — previously documented here but never
    actually implemented; every tour silently collapsed to I-I only) are
    still a real, worthwhile realism improvement (actual cross-boundary
    commuter behaviour, not just closed internal loops) and measurably
    raise the 5.1-10km share (best found: ~1-4% I-I-only -> 8.4% E-I/I-E-
    only, cross_fraction=1.0, gravity_km=8) — genuine progress, just not
    enough to approach RVU's 19% for that bin, because of the ceiling above.
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
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import osmnx as ox
from shapely.geometry import Point, shape

from build_data import INNER_CITY_BBOX
from build_sumo_net import sumo_home
from dirsplit.geo import bearing_deg, is_ahead

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

# RVU Västra Götaland 2022-2023, fig. 11 (p.21), home leg excluded — the
# WEEKLY AVERAGE (43/33/24), not a weekday-specific figure (Fig.11's own
# caption has no day-type qualifier, and the underlying diary survey spans
# all 7 days). Added 2026-07-09: this was used as a FLAT constant regardless
# of day_kind (weekday/weekend/holiday) — meaning every simulated day, even
# a Sunday, silently drew purposes from a 7-day BLEND, overstating "arbete"
# on real weekends/holidays and understating it on real weekdays.
#
# PURPOSE_SHARES_WEEKDAY/WEEKEND below split this back into day-type-
# specific shares. RVU has no such split directly, so the SHAPE of the
# weekday->weekend shift is TRIANGULATED from TWO independent, verified
# external sources (2026-07-09 — checked against a second source after the
# first cut used NHTS alone):
#   NHTS 2017 (US, fhwa.dot.gov/policyinformation, "Travel Trips by
#   Purpose", home-based categories, non-home-based excluded — no analog
#   here): work 17%wd/6%we (ratio 0.353); shop+other ("service" analog —
#   includes family/personal-business/medical/other, matching RVU's own
#   service definition) 41%wd/46%we (ratio 1.122); social ("fritid" analog)
#   10%wd/17%we (ratio 1.700).
#   UK NTS 2019 (gov.uk, table NTSQ04007, car/van-driver trips, AM+PM rush
#   hour): commuting ratio 0.359 (independently matches NHTS's 0.353 almost
#   exactly); shopping+personal-business+escort ("service" analog) ratio
#   1.056 (close to NHTS's 1.122); leisure (visit friends + sport/
#   entertainment, holiday/day-trips excluded as out of scope for an urban
#   daily-tour model) ratio 2.251 (same direction as NHTS's fritid ratio,
#   larger magnitude — likely a wider category definition).
# Averaged ratio per category: arbete 0.356, service 1.089, fritid 1.976.
# These RATIOS (not either source's absolute levels — different survey/
# category methodology, and neither is Swedish) are applied to back out
# weekday/weekend shares CONSTRAINED so the ANNUAL average reproduces RVU's
# own real 43/33/24 exactly (solved numerically, scipy fsolve) — using the
# EXACT 2025 day-type composition (249 true-weekday days + 116 weekend-
# shaped days [104 real weekend + 12 weekday-that-are-holidays] out of 365,
# not a naive 5/7-2/7 week, since ~12 weekday holidays a year measurably
# shift the composition). The assumption is that the *proportional*
# weekday->weekend shift is broadly similar across (car-oriented, northern-
# European-or-comparable) cities — a much weaker, more defensible claim
# than importing either source's absolute levels would be — while the
# total stays anchored to what RVU actually measured in Västra Götaland.
# Holidays reuse the weekend split — the closest real analog available
# (same reasoning as departure-time shape's day_kind fallback), no data to
# split it further; this is exactly what the annual-composition weighting
# above already assumes.
PURPOSE_SHARES_WEEKDAY = {"arbete": 0.5277, "service": 0.3032, "fritid": 0.1691}
PURPOSE_SHARES_WEEKEND = {"arbete": 0.2204, "service": 0.3875, "fritid": 0.3921}


def purpose_shares_for(is_weekend: bool) -> dict[str, float]:
    return PURPOSE_SHARES_WEEKEND if is_weekend else PURPOSE_SHARES_WEEKDAY


# PURPOSE_HOURLY_WEEKDAY/WEEKEND (2026-07-09): purpose ALSO varies by hour,
# not just by day-type — "kl 8 nästan 100% jobb" (the original observation
# that prompted this whole line of work). Neither RVU nor either day-type
# source above has a joint purpose×hour table, so this is calibrated from a
# THIRD external source with genuine hourly granularity:
#   UK NTS table NTSQ03018 (gov.uk, "Trip purpose by trip start time for
#   car/van drivers only, Monday to Friday only", England 2015/2019) — a
#   real 24-hour × 8-purpose-category breakdown. Mapped to our 3 categories
#   (arbete = Commuting+Business+Education+Escort education; service =
#   Shopping+Other work/escort/personal business; fritid = Visiting
#   friends/entertainment/sport+Holiday/day trip/other) using each
#   category's own trip-volume total (the table gives, per purpose, what %
#   of THAT purpose's trips fall in each hour — converted to "what % of
#   THIS HOUR's trips are each purpose" via each purpose's absolute daily
#   volume, then renormalized per hour).
# WEEKDAY: this UK hourly SHAPE is rescaled (2 free constants, scipy
# fsolve) so that, weighted by daily_shape() — our own REAL measured
# Gothenburg hourly departure profile — it integrates to exactly
# PURPOSE_SHARES_WEEKDAY. Result: arbete peaks at 92.8% (05h, though volume
# there is tiny) / 85.9% at the real 07h commute peak; service peaks
# ~50-54% mid-morning/midday; fritid peaks ~35-39% in the evening (19-21h).
# WEEKEND: the UK table is Mon-Fri only — no full weekend/Saturday hourly
# table was found (checked NHTS, UK NTS ad-hoc series, DE/NO/DK/NL surveys,
# 2026-07-09). Naively rescaling the WEEKDAY hourly shape for weekends
# overshot badly against real anchors (UK NTS table NTSQ04007's weekend
# AM/PM-rush purpose mix: 17.9%/51.4%/30.6% arbete/service/fritid AM,
# 11.6%/44.6%/43.8% PM — a synchronised commute peak just doesn't exist on
# weekends to drive the same hour-to-hour swing). Tested a 3-parameter
# per-category dampening fit against those 2 real anchors (least squares)
# vs. a 1-parameter fit where arbete/service are held at their flat daily
# mean and ONLY fritid varies by hour: both converged to essentially the
# SAME residual (0.0289 vs 0.0287) — the extra 2 parameters bought nothing,
# so the simpler model is used. Story: weekend "arbete"/"service" trips
# aren't commute-synchronised so carry little real hour-of-day signal;
# "fritid" still concentrates in the evening (37-53%, vs 21-25% midday).
PURPOSE_HOURLY_WEEKDAY: list[tuple[float, float, float]] = [
    (0.729, 0.131, 0.140), (0.855, 0.067, 0.078), (0.910, 0.049, 0.041),
    (0.884, 0.075, 0.041), (0.912, 0.059, 0.029), (0.928, 0.046, 0.026),
    (0.887, 0.076, 0.037), (0.859, 0.113, 0.028), (0.808, 0.157, 0.035),
    (0.455, 0.387, 0.158), (0.257, 0.536, 0.207), (0.280, 0.521, 0.199),
    (0.340, 0.458, 0.202), (0.394, 0.417, 0.189), (0.505, 0.344, 0.151),
    (0.665, 0.232, 0.103), (0.619, 0.261, 0.120), (0.650, 0.234, 0.116),
    (0.470, 0.301, 0.229), (0.300, 0.360, 0.340), (0.331, 0.324, 0.345),
    (0.359, 0.254, 0.387), (0.459, 0.177, 0.364), (0.491, 0.181, 0.328),
]
PURPOSE_HOURLY_WEEKEND: list[tuple[float, float, float]] = [
    (0.222, 0.391, 0.387), (0.248, 0.437, 0.315), (0.272, 0.479, 0.249),
    (0.273, 0.479, 0.248), (0.283, 0.497, 0.220), (0.284, 0.499, 0.217),
    (0.276, 0.485, 0.239), (0.285, 0.501, 0.214), (0.280, 0.492, 0.228),
    (0.228, 0.401, 0.371), (0.219, 0.385, 0.396), (0.221, 0.389, 0.390),
    (0.218, 0.383, 0.399), (0.220, 0.387, 0.393), (0.229, 0.402, 0.369),
    (0.243, 0.428, 0.329), (0.237, 0.416, 0.347), (0.237, 0.417, 0.346),
    (0.204, 0.358, 0.438), (0.184, 0.323, 0.493), (0.181, 0.319, 0.500),
    (0.172, 0.302, 0.526), (0.171, 0.301, 0.528), (0.177, 0.311, 0.512),
]
PURPOSE_CATEGORIES = ("arbete", "service", "fritid")


def purpose_shares_for_hour(hour: int, is_weekend: bool) -> dict[str, float]:
    table = PURPOSE_HOURLY_WEEKEND if is_weekend else PURPOSE_HOURLY_WEEKDAY
    a, s, f = table[int(hour) % 24]
    return {"arbete": a, "service": s, "fritid": f}

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

# Gothenburg's latitude — 1° longitude is NOT 1° latitude in km here
# (cos(57.7°)≈0.535, so 1° lon ≈ 59.5 km vs 1° lat ≈ 110.5 km). Used by both
# activity_mass()'s metre-scale POI catchment and gravity_distance_km()'s
# km-scale OD distance; a bare degree-distance (no cos-correction) overstates
# east-west distance ~1.87x, biasing gravity decay against E-W trips relative
# to equally-far north-south ones — found 2026-07-06.
KLAT_M = 110_540.0
KLON_M = 111_320.0 * math.cos(math.radians(57.7))


def scalar(v):
    return v[0] if isinstance(v, list) else v


def gravity_distance_km(lats: np.ndarray, lons: np.ndarray,
                        lat0: float, lon0: float) -> np.ndarray:
    """Cos-corrected flat-earth distance (km) from (lat0,lon0) to each point
    — the approximation gravity models use at city scale."""
    return np.sqrt(((lats - lat0) * KLAT_M / 1000.0) ** 2
                  + ((lons - lon0) * KLON_M / 1000.0) ** 2)


# RVU Västra Götaland 2022-2023 (VGR Analys 2023:56), p.12 table 2 — ALL
# trips (home leg included), 0-1/1.1-5/5.1-10/>10 km = 9/31/19/41%. The >10
# km bin structurally cannot occur for a HOME-BASED TOUR whose both ends are
# inside this small inner-city canvas (through-trips carry the long end of
# the real distribution instead — see docstring above) — so the fit check
# renormalizes RVU's THREE short bins to sum to 1 and compares only those.
RVU_SHORT_BIN_EDGES_KM = (1.0, 5.0, 10.0)
_rvu_short_raw = (0.09, 0.31, 0.19)
RVU_SHORT_BIN_SHARES = tuple(v / sum(_rvu_short_raw) for v in _rvu_short_raw)


def trip_length_fit(lengths_km: list[float]) -> dict:
    """Bin generated home-based-tour lengths into RVU's short bins and score
    the L1 distance to RVU's real (renormalized) shares — actually
    implements the fit build_candidates.py's own docstring has claimed
    since 2026-07-05 ("replaced [GEH scoring] with a trip-length fit"),
    which calibrate_theta.py never did (confirmed 2026-07-08: it only ever
    scored by GEH, which saturates at 100% for every θ combination and
    carries no signal to pick between them)."""
    n = len(lengths_km)
    if n == 0:
        return {"shares": [0.0, 0.0, 0.0], "l1_distance": float("inf"), "n": 0}
    counts = [0, 0, 0]
    over_10km = 0
    for d in lengths_km:
        if d <= RVU_SHORT_BIN_EDGES_KM[0]:
            counts[0] += 1
        elif d <= RVU_SHORT_BIN_EDGES_KM[1]:
            counts[1] += 1
        elif d <= RVU_SHORT_BIN_EDGES_KM[2]:
            counts[2] += 1
        else:
            over_10km += 1
    n_short = n - over_10km
    shares = [c / n_short for c in counts] if n_short > 0 else [0.0, 0.0, 0.0]
    l1 = sum(abs(s - r) for s, r in zip(shares, RVU_SHORT_BIN_SHARES))
    return {"shares": [round(s, 4) for s in shares], "l1_distance": round(l1, 4),
           "n": n, "over_10km_pct": round(100 * over_10km / n, 1)}


def duarouter_weight_args(weight_file: str | None,
                          weight_period: float | None = None) -> list[str]:
    """duarouter CLI args to route by MEASURED travel time from a prior
    iteration's own edgeData/BPR output instead of free-flow cost — []
    (route by free-flow speed/length, duarouter's default) when None.
    weight_period tells duarouter the file has multiple time-varying
    <interval> blocks (e.g. hourly) rather than one flat average."""
    if not weight_file:
        return []
    args = ["--weight-files", weight_file, "--weight-attribute", "traveltime"]
    if weight_period:
        args += ["--weight-period", str(weight_period)]
    return args


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
    klat, klon = KLAT_M, KLON_M
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


def gate_latlon(G, gates: list[tuple[str, int]]) -> tuple[np.ndarray, np.ndarray]:
    """Midpoint lat/lon per gate edge — same u/v-midpoint convention as
    load_graph_edges(), needed to gravity-weight E-I/I-E cross-boundary
    tours by distance from the gate, not just by road class."""
    lats, lons = [], []
    for eid, _ in gates:
        u, v, k = map(int, eid.split("_"))
        lats.append((G.nodes[u]["y"] + G.nodes[v]["y"]) / 2)
        lons.append((G.nodes[u]["x"] + G.nodes[v]["x"]) / 2)
    return np.array(lats), np.array(lons)


def reverse_edge_id(eid: str) -> str:
    u, v, k = eid.split("_")
    return f"{v}_{u}_{k}"


def route_visits_a_node_twice(edges: list[str]) -> bool:
    """True if this edge sequence passes through the same intersection more
    than once — a strict superset of a literal U-turn (edge e immediately
    followed by reverse(e) means the node BETWEEN them repeats one step
    later; a route that leaves a node, loops round the block, and returns
    to it repeats the same node several edges later instead) — so this one
    check replaces the narrower adjacent-only version below."""
    if not edges:
        return False
    nodes = [edges[0].split("_")[0]] + [e.split("_")[1] for e in edges]
    return len(nodes) != len(set(nodes))


def drop_uturn_routes(path: Path) -> None:
    """Direction-aware gates + a stiff turnaround penalty (see main()) cut
    literal U-turns from ~80% to ~10% of via-forced candidates — not zero,
    because a straight-line gate filter is only an approximation of what the
    road network can actually deliver, and duarouter still minimises cost
    rather than forbidding turnarounds outright (its own --remove-loops flag
    doesn't catch this either — verified directly, see below). The PFE then
    makes it WORSE: its parsimony objective reuses whichever candidates
    touch a measured edge as heavily as needed to hit the hard count, so a
    residual U-turn rate in the pool becomes hugely overrepresented among
    actually-simulated vehicles at sensor edges (measured directly). Rather
    than chase the gate/penalty heuristics further, enforce the actual
    invariant directly.

    WIDENED 2026-07-10 (Gustav, watching the live simulation: "det ser ut
    som att vissa åker in i sensorn och åker tillbaka igen" — found to be
    real, not a rendering artifact): the ORIGINAL check here only caught a
    literal U-turn (edge e immediately followed by reverse(e)). Measured
    directly in a live candidate pool: 9.1% of candidates revisit some
    OTHER node a few edges later instead — duarouter's --weights.random-
    factor jitter finding a small, pointless loop around a block (leave a
    node, wander a handful of edges, come back to the SAME node, continue)
    that its own --remove-loops flag does not remove. One specific such
    loop, sitting on a sensor edge, was reused by PFE for ~100 of ~2100
    simulated vehicles in one run (repeated node check below now drops it
    from the pool before PFE ever sees it, same mechanism as the original
    literal-U-turn fix, since a literal U-turn is the special case where
    the repeated node is exactly one step back)."""
    tree = ET.parse(path)
    root = tree.getroot()
    dropped = 0
    for veh in list(root):
        route = veh.find("route")
        edges = route.get("edges").split()
        if route_visits_a_node_twice(edges):
            dropped += 1
            root.remove(veh)
    if dropped:
        tree.write(path)
    print(f"  dropped {dropped} candidates revisiting the same intersection "
          f"twice (a literal U-turn is the special case one step back)")


def drop_excessive_detours(path: Path, G, max_stretch: float) -> None:
    """Drop candidates whose OWN realized path costs more than max_stretch
    times the TRUE shortest path between their OWN entry and exit nodes —
    "enforce the actual invariant directly" (this file's own established
    fix pattern, see drop_uturn_routes above) applied to detours instead of
    loops, because it is THE SAME root cause: `--weights.random-factor`
    jitters every edge's cost independently and multiplicatively with no
    cap on how many edges on one path can get unluckily jittered in the
    same direction, so a long candidate's cumulative cost inflation is
    UNBOUNDED in the worst case — drop_uturn_routes already catches the
    most visible symptom (the jitter finding a pointless loop BACK to a
    node already visited), but a genuinely circuitous, still-simple path
    (no repeated node, so that check lets it through) is the exact same
    mechanism, just without a repeated node to key off. Gustav asked
    (2026-07-10) whether the candidate pool could contain "helt orimliga
    rutter" (completely unreasonable routes) — this was the honest answer:
    yes, nothing verified it, only drop_uturn_routes' narrower invariant
    did.

    max_stretch is deliberately the SAME --route-diversity value passed to
    duarouter's --weights.random-factor, not an arbitrary constant: that
    parameter IS the mechanism being checked, so the tolerance for how far
    a jittered path may stray from true-shortest should be exactly the
    tolerance we told duarouter to explore, not a second, disconnected
    magic number. Endpoints repeat heavily across the pool (e.g. all
    ~900 via-trips through one sensor share a handful of gate pairs), so
    true-shortest-path costs are cached per (entry, exit) pair — one
    Dijkstra call per DISTINCT pair, not per candidate."""
    import networkx as nx
    tree = ET.parse(path)
    root = tree.getroot()
    true_cost_cache: dict[tuple[int, int], float | None] = {}
    dropped = 0
    for veh in list(root):
        route = veh.find("route")
        edges = route.get("edges").split()
        entry = int(edges[0].split("_")[0])
        exit_ = int(edges[-1].split("_")[1])
        realized = sum(G[int(u)][int(v)][int(k)]["length"]
                       for u, v, k in (e.split("_") for e in edges))
        key = (entry, exit_)
        if key not in true_cost_cache:
            try:
                true_cost_cache[key] = nx.shortest_path_length(
                    G, entry, exit_, weight="length")
            except nx.NetworkXNoPath:
                true_cost_cache[key] = None
        true_cost = true_cost_cache[key]
        if true_cost is not None and realized > max_stretch * true_cost:
            dropped += 1
            root.remove(veh)
    if dropped:
        tree.write(path)
    print(f"  dropped {dropped} candidates whose own path cost more than "
          f"{max_stretch}x the true shortest path for their entry/exit "
          f"(the same random-factor jitter drop_uturn_routes catches when "
          f"it loops; this is the same mechanism without a repeated node)")


def report_sensor_cross_hits(routed_path: Path, measured: list[str]) -> dict:
    """Diagnostic only (not wired into PFE weighting this pass) — a
    vehicle whose route naturally crosses MULTIPLE sensors is extra
    valuable (cross-sensor reinforcement, Gustav's framing when this
    generation scheme was designed: as more sensors get added, more such
    vehicles appear automatically). Checked AFTER routing, not during
    generation — it reflects the vehicle's ACTUAL simulated route (a
    simple set-membership scan over its final edge list) rather than
    generation-time intent, and needs no Dijkstra call since the route is
    already concrete.

    Writes sumo/sensor_coverage_report.json: for each sensor, how many of
    its own candidates ALSO touch 1/2/3+ of the other 6 measured edges.
    Becomes materially more useful once more sensors are added (more
    overlap to find) — kept cheap and separate from generation now so it
    doesn't complicate the yield-sensitive sampling logic there."""
    root = ET.parse(routed_path).getroot()
    report: dict[str, dict[str, int]] = {
        m: {"total": 0, "cross_1": 0, "cross_2": 0, "cross_3plus": 0}
        for m in measured
    }
    for veh in root:
        route = veh.find("route")
        if route is None:
            continue
        edges = set(route.get("edges").split())
        hits = [m for m in measured if m in edges]
        for m in hits:
            other_hits = len(hits) - 1
            report[m]["total"] += 1
            if other_hits == 1:
                report[m]["cross_1"] += 1
            elif other_hits == 2:
                report[m]["cross_2"] += 1
            elif other_hits >= 3:
                report[m]["cross_3plus"] += 1
    with open(SUMO_DIR / "sensor_coverage_report.json", "w") as f:
        json.dump(report, f, indent=1)
    return report


def upstream_downstream_gates(
    G, m_edge: str, entries: list[tuple[str, int]], exits: list[tuple[str, int]],
) -> tuple[list[str], list[str]]:
    """Restrict entry/exit gates for a via-forced trip through m_edge to ones
    that make it a plausible THROUGH-MOVEMENT in m_edge's own travel
    direction, instead of an arbitrary detour.

    A gate picked with no regard for m_edge's location forces duarouter to
    solve entry -> m_edge -> exit as two unrelated shortest-path legs; when
    the via edge isn't on the way, the cheapest solution is often to drive to
    it and immediately take its antiparallel counterpart back the way it
    came — a literal U-turn (verified directly in sumo/candidates.rou.xml:
    edge e immediately followed by reverse(e) at the via point, for the
    majority of via-trips sampled). Filtering to gates roughly "behind"
    (entry) and "ahead" (exit) of m_edge's own bearing keeps every via-trip a
    genuine corridor movement — the same road-class gate weighting already
    used for real E-E through-trips, just localised to one edge's heading."""
    u, v, k = map(int, m_edge.split("_"))
    ulat, ulon = G.nodes[u]["y"], G.nodes[u]["x"]
    vlat, vlon = G.nodes[v]["y"], G.nodes[v]["x"]
    via_bearing = bearing_deg(ulat, ulon, vlat, vlon)
    mlat, mlon = (ulat + vlat) / 2, (ulon + vlon) / 2

    ins = [eid for eid, n in entries
           if is_ahead(bearing_deg(G.nodes[n]["y"], G.nodes[n]["x"], mlat, mlon),
                      via_bearing)]
    outs = [eid for eid, n in exits
           if is_ahead(bearing_deg(mlat, mlon, G.nodes[n]["y"], G.nodes[n]["x"]),
                      via_bearing)]
    return (ins or [eid for eid, _ in entries]), (outs or [eid for eid, _ in exits])


def via_naturally_on_path(G, entry_v: int, exit_u: int, m_u: int, m_v: int) -> bool:
    """True if the via edge (m_u -> m_v) lies consecutively on the shortest
    (by length) path from entry_v to exit_u — i.e. forcing the trip through
    it as a via constraint doesn't require a detour, since it's already on
    the way there."""
    import networkx as nx
    try:
        path = nx.shortest_path(G, entry_v, exit_u, weight="length")
    except nx.NetworkXNoPath:
        return False
    return any(path[i] == m_u and path[i + 1] == m_v for i in range(len(path) - 1))


def build_shortest_distance_matrix(G) -> tuple[np.ndarray, dict[int, int]]:
    """All-pairs shortest (by length) distance, computed ONCE per run via
    scipy.sparse.csgraph.dijkstra (scipy is already a project dependency —
    pfe.py, observability.py) — measured 1.39s / 92.6MB on the real
    3402-node production graph. This is what makes
    natural_far_end_weights() an O(1) array lookup per candidate instead
    of a real nx.shortest_path call (2.23ms measured) — the difference
    between bounded retries being free vs. costing real minutes per
    sensor, which is exactly the kind of cost this session already hit
    once with the (reverted) duarouter-retry approach to via-trip
    coverage.

    Parallel edges (MultiDiGraph) are collapsed to their MINIMUM length —
    for shortest-path purposes only the cheapest of several parallel
    edges between the same two nodes ever matters; scipy's sparse matrix
    constructor SUMS duplicate (row, col) entries by default, which would
    silently corrupt distances if left to it."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra

    nodes = list(G.nodes())
    node_idx = {n: i for i, n in enumerate(nodes)}
    best: dict[tuple[int, int], float] = {}
    for u, v, d in G.edges(data=True):
        key = (node_idx[u], node_idx[v])
        length = d.get("length", 1.0)
        if key not in best or length < best[key]:
            best[key] = length
    rows, cols, weights = zip(*((r, c, w) for (r, c), w in best.items()))
    adj = csr_matrix((weights, (rows, cols)), shape=(len(nodes), len(nodes)))
    D = dijkstra(adj, directed=True)
    return D, node_idx


def natural_far_end_weights(
    D: np.ndarray, node_idx: dict[int, int],
    anchor_v: int, m_u: int, m_v: int, m_length: float,
    candidate_u_idx: np.ndarray, base_weights: np.ndarray,
    tol_abs: float = 0.5, tol_rel: float = 1e-6,
) -> np.ndarray:
    """Vectorized generalization of via_naturally_on_path — same
    semantics (is routing anchor->m_u->m_v->candidate the true shortest
    anchor->candidate path), checked via distance ARITHMETIC on the
    precomputed all-pairs matrix instead of tracing one nx.shortest_path
    per candidate: anchor->candidate is a natural via-fit iff
    dist(anchor,m_u) + m_length + dist(m_v,candidate) == dist(anchor,candidate)
    (within tolerance). This masks (never reinterprets) whatever weighting
    a trip category already computes — base_weights passes through
    unchanged wherever the candidate is natural, and becomes 0 elsewhere,
    so gravity_km's existing calibration and meaning are untouched.

    candidate_u_idx must already be MATRIX indices (via node_idx), not
    raw graph node IDs — precompute this once per candidate pool outside
    any per-trip sampling loop; base_weights is aligned to it 1:1.

    Unreachable pairs (scipy dijkstra returns inf across disconnected
    components) are correctly treated as not-natural, never as a match."""
    a = node_idx[anchor_v]
    mu = node_idx[m_u]
    mv = node_idx[m_v]
    d_a_mu = D[a, mu]
    d_mv_far = D[mv, candidate_u_idx]
    d_direct = D[a, candidate_u_idx]
    via_dist = d_a_mu + m_length + d_mv_far
    finite = np.isfinite(d_direct) & np.isfinite(via_dist)
    tol = np.maximum(tol_abs, tol_rel * np.where(finite, d_direct, 0.0))
    # inf - inf (both anchor and via-route unreachable to a candidate) is a
    # real, expected case on a graph with disconnected components — numpy
    # eagerly computes via_dist - d_direct for the WHOLE array even inside
    # np.where, so it produces a nan there regardless; `finite` already
    # excludes that element from `natural` correctly, this just silences
    # the resulting (harmless) RuntimeWarning.
    with np.errstate(invalid="ignore"):
        natural = finite & (np.abs(via_dist - d_direct) <= tol)
    return np.where(natural, base_weights, 0.0)


def sample_anchor_and_far_end(
    rng: np.random.Generator, D: np.ndarray, node_idx: dict[int, int],
    anchor_node_ids: np.ndarray, anchor_weights: np.ndarray,
    far_u_idx: np.ndarray, far_base_weights: np.ndarray,
    m_u: int, m_v: int, m_length: float,
    max_anchor_redraws: int = 25,
) -> tuple[int, int] | None:
    """Draw an anchor from its EXISTING, unbiased weighting (anchor_weights
    — pH for homes, w_entry/w_exit for gates, unchanged from what each
    trip category already computes), then mask the far-end pool to the
    exact naturally-via-m_edge subset and sample the far end from THAT,
    still weighted by whatever the category already computes
    (far_base_weights, e.g. activity_mass[purpose]*exp(-d/gravity_km)).

    If a drawn anchor happens to have ZERO natural far ends (measured on
    the real graph: this varies 1.3%-87% depending on the sensor — some
    sensors are structurally poor fits for a given trip category), redraw
    the anchor — cheap, an array op, not a Dijkstra call — up to
    max_anchor_redraws times. Returns None (never a non-compliant pair)
    if exhausted; the caller counts/logs this rather than forcing a trip
    that wouldn't genuinely be anchored to the sensor.

    Returns (anchor_index, far_end_index) — BOTH as positions into
    anchor_node_ids and far_u_idx/far_base_weights respectively, not
    resolved node IDs: the caller usually needs the exact source edge
    (its id string, lat/lon, its OWN "u" node for a return leg, ...), and
    multiple edges can share the same node, so returning only a bare node
    value would make it ambiguous which specific edge was actually drawn.
    The caller looks up whatever metadata it associates with each
    position — this function works purely in distance/index space and has
    no knowledge of edge IDs, lat/lon, or purpose categories."""
    n_anchors = len(anchor_node_ids)
    total_anchor_w = anchor_weights.sum()
    if n_anchors == 0 or total_anchor_w <= 0:
        return None
    p_anchor = anchor_weights / total_anchor_w
    for _ in range(max_anchor_redraws):
        anchor_pos = rng.choice(n_anchors, p=p_anchor)
        anchor_v = int(anchor_node_ids[anchor_pos])
        if anchor_v not in node_idx:
            continue
        masked = natural_far_end_weights(D, node_idx, anchor_v, m_u, m_v,
                                         m_length, far_u_idx, far_base_weights)
        total = masked.sum()
        if total > 0:
            far_pos = rng.choice(len(far_u_idx), p=masked / total)
            return int(anchor_pos), int(far_pos)
    return None


def verified_via_gate_pairs(G, m_edge: str, in_ids: list[str], out_ids: list[str]
                            ) -> list[tuple[str, str]]:
    """Of all (entry, exit) gate pairs for a via-forced trip through
    m_edge, keep only those where m_edge genuinely lies on the shortest
    entry->exit path — so the SOURCE of a via-trip is a real, natural
    corridor movement, not just one that survives a post-hoc U-turn/loop
    filter.

    WHY THIS EXISTS (Gustav, watching the live simulation, 2026-07-10:
    "man ska väl inte behöva välja en sån här rutt... processen säger väl
    du kommer härifrån och du ska dit, ta den snabbaste vägen" — you
    shouldn't have to pick a route like that, the process says you come
    from here and go there, take the fastest way). He's right that a
    single genuine shortest-path computation with non-negative edge
    weights can NEVER revisit a node — Dijkstra/A* only ever return simple
    paths. The loops found (drop_uturn_routes' widened check, same day)
    aren't duarouter computing a bad shortest path; they're a via-forced
    trip being solved as TWO INDEPENDENT shortest-path legs (entry->via,
    via->exit) concatenated — each leg alone IS optimal, but the
    concatenation isn't, when the via point isn't actually on the way.
    upstream_downstream_gates()'s straight-line-bearing filter is a real
    improvement but only an approximation of what the ACTUAL road network
    connects without a detour (its own docstring says so) — this checks
    the real claim on the real graph instead of trusting the bearing
    proxy, so a bad via-trip is never GENERATED in the first place rather
    than generated and then discarded (which just shrinks that sensor's
    effective coverage pool by however many looped).
    Falls back to ALL candidate pairs if none verify (extremely rare, all
    gates on a genuinely awkward via edge) — drop_uturn_routes' node-repeat
    check remains the safety net either way."""
    u_m, v_m, _ = m_edge.split("_")
    u_m, v_m = int(u_m), int(v_m)
    good = []
    for e_in in in_ids:
        v_in = int(e_in.split("_")[1])
        for e_out in out_ids:
            u_out = int(e_out.split("_")[0])
            if via_naturally_on_path(G, v_in, u_out, u_m, v_m):
                good.append((e_in, e_out))
    return good or [(e_in, e_out) for e_in in in_ids for e_out in out_ids]


def daily_shape(is_weekend: bool = False) -> np.ndarray:
    """Our OWN measured departure-time distribution (normal_profile.json) —
    more local and finer-grained than RVU's regional bins, and consistent
    with them (both show the 7-8h / 16-17h peaks RVU reports on p.11).

    FIXED 2026-07-09: this unconditionally read the 'weekday' profile
    regardless of which --date was actually being calibrated — normal_
    profile.json has ALWAYS carried a separate, real 'weekend' profile
    (RVU's own Fig.1 confirms weekday and weekend departure-time shapes
    differ substantially: weekend starts later and peaks broader, ~16:00,
    vs weekday's sharp AM/PM peaks), it just was never read here."""
    with open("web/data/normal_profile.json") as f:
        profiles = json.load(f)["profiles"]
    key = "weekend" if is_weekend else "weekday"
    acc = np.zeros(24)
    for p in profiles.values():
        wd = p.get(key) or []
        for h in range(24):
            vals = [v for v in wd[h * 4:(h + 1) * 4] if v is not None]
            if vals:
                acc[h] += sum(vals) / len(vals)
    return acc / acc.sum()


REAL_DAY_SHAPE_WEIGHT = 0.7   # fixed shrinkage toward --real-day-shape-file's
                             # measured/forecast shape, applied even when
                             # that day's data is complete — hedges against
                             # ONE day's sampling noise across just 6-7
                             # sensors, same "always some shrinkage"
                             # principle as dirsplit's James-Stein lambda.


def blend_day_shape(real: np.ndarray, fallback: np.ndarray,
                    weight: float = REAL_DAY_SHAPE_WEIGHT) -> np.ndarray:
    """weight toward the real/forecast day's own measured shape, (1-weight)
    toward the smoothed weekday/weekend/holiday fallback."""
    blended = weight * real + (1 - weight) * fallback
    return blended / blended.sum()


def _natural_sensor_for_leg(D, node_idx, anchor_v: int, candidate_u: int,
                            m_info: dict[str, tuple[int, int, float]],
                            quota: dict[str, int]) -> str | None:
    """Which measured edge (if any) the anchor->candidate path naturally
    passes through — checked against ALL sensors (7 cheap O(1) lookups),
    preferring whichever is furthest behind its own quota if more than one
    fits. Used for a tour's RETURN leg, whose path is already fixed once
    the outbound leg's anchor+far-end are drawn — unlike the outbound
    leg's own draw (which only ever tries ONE sensor's naturalness mask at
    a time via sample_anchor_and_far_end), the return leg's fixed path
    might naturally fit a DIFFERENT sensor than the outbound leg did, or
    none at all (a real, expected case on a directed, asymmetric network
    — see generate_sensor_anchored_trips' own docstring)."""
    candidate_idx = np.array([node_idx[candidate_u]])
    for m_edge in sorted(m_info, key=lambda m: -quota[m]):
        m_u, m_v, m_length = m_info[m_edge]
        w = natural_far_end_weights(D, node_idx, anchor_v, m_u, m_v, m_length,
                                    candidate_idx, np.array([1.0]))
        if w[0] > 0:
            return m_edge
    return None


def generate_sensor_anchored_trips(
    rng: np.random.Generator, G, edges: list[dict],
    hmass: np.ndarray, amass: dict[str, np.ndarray],
    entries: list[tuple[str, int]], exits: list[tuple[str, int]],
    entry_ids: list[str], exit_ids: list[str],
    w_entry: np.ndarray, w_exit: np.ndarray,
    shape_hourly: np.ndarray, measured: list[str],
    n_total: int, through_fraction: float, cross_fraction: float,
    gravity_km: float, is_weekend: bool,
    min_per_sensor: int = 50, max_anchor_redraws: int = 25,
) -> tuple[list[tuple], list[float]]:
    """Replaces the old E-E/I-I/E-I/I-E blocks + separate via-trip
    coverage block with ONE principle: every generated trip must be
    traceable to at least one of the measured sensor edges (Gustav,
    2026-07-10 — every simulated vehicle should be explainable by real
    sensor data, not invented background demand), while each trip's own
    start/end are still drawn from the SAME realistic, city-wide
    population/POI/gravity weighting the old "regular" trips used — not
    narrowly local to a sensor.

    MECHANISM (validated against the real graph before being wired in —
    see the standalone diagnostic run this session): draw the anchor
    (home edge / gate) from its existing, UNCHANGED weighting, then mask
    the far-end candidate pool to the EXACT subset where routing
    anchor->sensor->far-end is genuinely the shortest anchor->far-end path
    (natural_far_end_weights, an exact vectorized generalization of
    via_naturally_on_path — not an approximation, so nothing generated
    ever needs to be discarded downstream for being an unnatural detour).
    A naive "free draw, then force+verify" approach was measured at
    0.65% yield on the real graph — far too low for practical retries;
    masking the pool this way instead of rejecting after the fact is what
    makes bounded retries (max_anchor_redraws) actually cheap (measured
    98.5-100% cumulative success per sensor at 25 retries, ~0.2-1ms/call).

    PAIRED TOURS keep their existing AM/PM structure (a real, valuable
    realism property), but do NOT require both legs through the SAME
    sensor — measured redraw rates vary 1.3%-87% across the real 7
    sensors (the network is directed and asymmetric), so that would fail
    constantly on a poor-fit sensor for no good reason. The outbound leg
    tries sensors in quota order (most-behind first) via
    sample_anchor_and_far_end; the return leg's path is then already
    fixed, so it's checked against all 7 sensors (_natural_sensor_for_leg)
    for whichever one it happens to naturally pass — if none, the whole
    tour (home+activity pair) is redrawn rather than emitting a
    non-compliant leg.

    E-E (gate-to-gate through trips) skips the distance matrix entirely —
    gate pools are tiny (28/24 on the real graph) so the EXISTING,
    already-validated upstream_downstream_gates + verified_via_gate_pairs
    mechanism is reused unchanged, just invoked per-sensor here instead of
    in a separate block.

    BUDGET: one shared n_total, split into an equal starting quota per
    sensor (a rigid per-sensor count would be wrong given the measured
    1.3%-87% connectivity spread — a structurally poor-fit sensor
    correctly ends up under its equal share, a well-connected one absorbs
    the overflow; forcing equal counts onto a poor fit would just mean
    reusing near-duplicate routes, exactly what pfe.py's Path-Size
    weighting already penalises). min_per_sensor is a safety-net floor
    only, not a target."""
    D, node_idx = build_shortest_distance_matrix(G)

    edge_ids  = [e["id"] for e in edges]
    edge_u_nodes = [e["u"] for e in edges]
    edge_v_nodes = [e["v"] for e in edges]
    edge_u_idx = np.array([node_idx[u] for u in edge_u_nodes])
    edge_lats  = np.array([e["lat"] for e in edges])
    edge_lons  = np.array([e["lon"] for e in edges])

    entry_v_nodes = [int(eid.split("_")[1]) for eid in entry_ids]
    entry_lats, entry_lons = gate_latlon(G, entries)
    exit_u_nodes = [int(eid.split("_")[0]) for eid in exit_ids]
    exit_v_nodes = [int(eid.split("_")[1]) for eid in exit_ids]
    exit_u_idx = np.array([node_idx[u] for u in exit_u_nodes])
    exit_lats, exit_lons = gate_latlon(G, exits)

    m_info: dict[str, tuple[int, int, float]] = {}
    good_pairs_ee: dict[str, list[tuple[str, str]]] = {}
    for m_edge in measured:
        u_s, v_s, k_s = m_edge.split("_")
        m_u, m_v = int(u_s), int(v_s)
        m_info[m_edge] = (m_u, m_v, G[m_u][m_v][int(k_s)]["length"])
        in_ids, out_ids = upstream_downstream_gates(G, m_edge, entries, exits)
        good_pairs_ee[m_edge] = verified_via_gate_pairs(G, m_edge, in_ids, out_ids)

    n_sensors = len(measured)
    initial_quota = {m: n_total // n_sensors + (1 if i < n_total % n_sensors else 0)
                     for i, m in enumerate(measured)}
    quota = dict(initial_quota)

    def by_quota():
        return sorted(measured, key=lambda m: -quota[m])

    trips: list[tuple] = []
    tour_lengths_km: list[float] = []
    n_dropped_no_sensor = 0

    def am_pm_hours():
        h_out = rng.choice(24, p=shape_hourly)
        pm_shape = shape_hourly * (np.arange(24) >= 12)
        pm_shape = pm_shape / pm_shape.sum() if pm_shape.sum() > 0 else shape_hourly
        h_ret = max(h_out + 1, rng.choice(24, p=pm_shape))
        return h_out, min(h_ret, 23)

    n_through     = int(n_total * through_fraction)
    n_tours_total = (n_total - n_through) // 2
    n_cross    = int(n_tours_total * cross_fraction)
    n_internal = n_tours_total - n_cross
    n_ei = n_cross // 2
    n_ie = n_cross - n_ei

    # ── E-E: through traffic, gate->gate — unchanged mechanism, invoked
    # per-sensor here instead of in a separate block ────────────────────
    hours = rng.choice(24, size=n_through, p=shape_hourly)
    for h in hours:
        for m_edge in by_quota():
            pairs = good_pairs_ee[m_edge]
            if not pairs:
                continue
            e_in, e_out = pairs[rng.integers(len(pairs))]
            trips.append(((h + rng.random()) * 3600, e_in, e_out, m_edge))
            quota[m_edge] -= 1
            break

    # ── I-I / E-I / I-E: paired tours, sensor-anchored per leg — the
    # outbound leg draws (anchor, far-end) fresh per attempt (gravity decay
    # depends on WHICH anchor gets drawn, so it's recomputed every retry,
    # not fixed once), trying sensors in quota order; the return leg's
    # path is then fixed, so it's checked against all 7 sensors for
    # whichever it naturally fits — possibly a DIFFERENT one, possibly
    # none (whole tour redrawn if so). ─────────────────────────────────
    def gravity_anchored_tour(
        anchor_v_nodes: list[int], anchor_u_nodes: list[int],
        anchor_weights: np.ndarray, anchor_lats: np.ndarray, anchor_lons: np.ndarray,
        anchor_ids: list[str],
        far_u_idx: np.ndarray, far_u_nodes: list[int], far_v_nodes: list[int],
        far_lats: np.ndarray, far_lons: np.ndarray, far_ids: list[str],
        far_base_weights: np.ndarray, h_out: int, h_ret: int,
    ) -> bool:
        """far_base_weights/h_out/h_ret are resolved ONCE by the caller
        (purpose is drawn from that SAME h_out, matching the original
        per-tour — not per-retry — semantics); gravity decay is still
        recomputed every anchor attempt below, since it genuinely depends
        on which anchor gets drawn."""
        nonlocal n_dropped_no_sensor
        anchor_p = anchor_weights / anchor_weights.sum()
        for m_edge in by_quota():
            m_u, m_v, m_length = m_info[m_edge]
            for _ in range(max_anchor_redraws):
                a_pos = int(rng.choice(len(anchor_v_nodes), p=anchor_p))
                anchor_v = anchor_v_nodes[a_pos]
                if anchor_v not in node_idx:
                    continue
                d_km = gravity_distance_km(far_lats, far_lons,
                                           anchor_lats[a_pos], anchor_lons[a_pos])
                far_w = far_base_weights * np.exp(-d_km / gravity_km)
                masked = natural_far_end_weights(D, node_idx, anchor_v, m_u, m_v,
                                                 m_length, far_u_idx, far_w)
                total = masked.sum()
                if total <= 0:
                    continue
                f_pos = int(rng.choice(len(far_u_idx), p=masked / total))
                return_sensor = _natural_sensor_for_leg(
                    D, node_idx, far_v_nodes[f_pos], anchor_u_nodes[a_pos],
                    m_info, quota)
                if return_sensor is None:
                    continue
                quota[m_edge] -= 1
                quota[return_sensor] -= 1
                tour_lengths_km.append(float(d_km[f_pos]))
                trips.append(((h_out + rng.random()) * 3600,
                              anchor_ids[a_pos], far_ids[f_pos], m_edge))
                trips.append(((h_ret + rng.random()) * 3600,
                              far_ids[f_pos], anchor_ids[a_pos], return_sensor))
                return True
        n_dropped_no_sensor += 1
        return False

    def draw_purpose_weights(h_out: int) -> np.ndarray:
        purpose_shares = purpose_shares_for_hour(int(h_out), is_weekend)
        purpose = rng.choice(PURPOSE_CATEGORIES, p=list(purpose_shares.values()))
        return amass[purpose]

    entry_u_nodes = [int(eid.split("_")[0]) for eid in entry_ids]

    # I-I: home -> activity, both internal
    for _ in range(n_internal):
        h_out, h_ret = am_pm_hours()
        gravity_anchored_tour(
            edge_v_nodes, edge_u_nodes, hmass, edge_lats, edge_lons, edge_ids,
            edge_u_idx, edge_u_nodes, edge_v_nodes, edge_lats, edge_lons, edge_ids,
            draw_purpose_weights(h_out), h_out, h_ret)

    # E-I: entry gate -> activity, exits via an independently-drawn gate
    for _ in range(n_ei):
        h_out, h_ret = am_pm_hours()
        gravity_anchored_tour(
            entry_v_nodes, entry_u_nodes, w_entry, entry_lats, entry_lons, entry_ids,
            edge_u_idx, edge_u_nodes, edge_v_nodes, edge_lats, edge_lons, edge_ids,
            draw_purpose_weights(h_out), h_out, h_ret)

    # I-E: internal home -> exit gate
    for _ in range(n_ie):
        h_out, h_ret = am_pm_hours()
        gravity_anchored_tour(
            edge_v_nodes, edge_u_nodes, hmass, edge_lats, edge_lons, edge_ids,
            exit_u_idx, exit_u_nodes, exit_v_nodes, exit_lats, exit_lons, exit_ids,
            w_exit, h_out, h_ret)

    print(f"sensor-anchored generation: dropped {n_dropped_no_sensor} tour "
          f"attempts where no sensor naturally fit either leg "
          f"(of {n_internal + n_ei + n_ie} attempted)")
    achieved = {m: initial_quota[m] - quota[m] for m in measured}
    short = {m: n for m, n in achieved.items() if n < min_per_sensor}
    if short:
        print(f"  WARNING: below --min-per-sensor ({min_per_sensor}): {short} "
              f"-- these sensors are structurally poor fits for the current "
              f"trip mix; consider a higher --n-total or investigate why")
    return trips, tour_lengths_km


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--through-fraction", type=float, default=0.5,
                    help="θ: share of trips that are E-E through traffic")
    ap.add_argument("--gravity-km", type=float, default=1.8,
                    help="θ: distance-deterrence scale (km) for tours")
    ap.add_argument("--cross-fraction", type=float, default=0.3,
                    help="θ: share of the (non-through) tour budget that is "
                        "E-I/I-E cross-boundary commuting (one end at a "
                        "gate, one end an internal home/activity edge) "
                        "rather than pure I-I (both ends internal). "
                        "Disclosed-unidentifiable neutral prior, same "
                        "status as through-fraction — no local cordon "
                        "count exists to calibrate a real split. Added "
                        "2026-07-08: E-I/I-E were documented in this "
                        "docstring's METHOD section from the start but "
                        "never actually implemented — every 'tour' was "
                        "silently I-I only (both ends drawn from `edges`, "
                        "never a gate), which structurally caps tour "
                        "length at the small inner-city canvas's own "
                        "diameter and cannot reach RVU's 5.1-10km trip "
                        "share (confirmed: best fit across gravity_km "
                        "1-15km still under 5%, vs RVU's 32%).")
    ap.add_argument("--is-weekend", action="store_true",
                    help="Use normal_profile.json's WEEKEND departure-time "
                        "shape instead of weekday (later start, broader "
                        "single ~16:00 peak vs weekday's sharp AM/PM peaks — "
                        "RVU Fig.1). Passed through from build_sumo_demand.py "
                        "based on the actual --date being calibrated — found "
                        "2026-07-08: this script had no notion of which "
                        "date/day-of-week it was building for at all, always "
                        "silently assuming an average weekday even when "
                        "calibrating a Saturday/Sunday.")
    ap.add_argument("--real-day-shape-file", default=None,
                    help="JSON array of 24 hourly shares — the ACTUAL "
                        "measured (or, for a forecast date, Agent 1's "
                        "forecast) departure-time shape for the EXACT "
                        "calendar day being calibrated, written by "
                        "build_sumo_demand.py's real_day_shape(). Preferred "
                        "over --is-weekend's bucket average where available "
                        "(blended with it, REAL_DAY_SHAPE_WEIGHT toward the "
                        "real day) — it directly reflects whatever actually "
                        "happened that specific date (a school-break Friday, "
                        "a snow day, a local event, ...) rather than an "
                        "assumption about it, with no holiday list to "
                        "maintain. Added 2026-07-09.")
    ap.add_argument("--n-total", type=int, default=12000)
    ap.add_argument("--min-per-sensor", type=int, default=50,
                    help="safety-net floor, not a target — every trip is "
                        "now sensor-anchored (generate_sensor_anchored_trips), "
                        "so n_total is split into an equal starting quota "
                        "per sensor; structurally poor-fit sensors correctly "
                        "end up under quota and well-connected ones absorb "
                        "the overflow (measured redraw-success rate on the "
                        "real graph varies 1.3%-100% by sensor). This floor "
                        "only guards against a sensor silently getting zero "
                        "coverage.")
    ap.add_argument("--route-diversity", type=float, default=2.0,
                    help="duarouter --weights.random-factor: per-trip edge-"
                        "weight jitter drawn from [1, X) so similar OD pairs "
                        "spread across several realistic routes instead of "
                        "collapsing onto one canonical shortest path — the "
                        "same failure mode assignment_priors.py's Dial-style "
                        "stochastic multipath was built to fix, applied here "
                        "natively via duarouter instead of a re-implemented "
                        "networkx shortest-path loop")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-suffix", default="",
                    help="internal use by calibrate_theta.py")
    ap.add_argument("--weight-file", default=None,
                    help="a SUMO meandata XML (e.g. a BPR estimate from a "
                        "prior iteration's own achieved flow) giving MEASURED "
                        "travel times to route by, instead of free-flow "
                        "speed/length. Used by build_sumo_demand.py's "
                        "congestion-feedback loop: routing on free-flow cost "
                        "alone means candidate routes never avoid streets "
                        "that are actually congested under the calibrated "
                        "demand.")
    ap.add_argument("--weight-period", type=float, default=None,
                    help="Aggregation period (s) of --weight-file's "
                        "<interval> blocks, if it has more than one (a "
                        "time-varying/per-hour weight file, not one flat "
                        "average for the whole window) — passed straight to "
                        "duarouter so trips are routed against the "
                        "congestion of the period they actually depart in.")
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
    shape_hourly = daily_shape(args.is_weekend)
    if args.real_day_shape_file:
        with open(args.real_day_shape_file) as f:
            real_shape = np.array(json.load(f))
        shape_hourly = blend_day_shape(real_shape, shape_hourly)
    print(f"{len(entries)} entry gates, {len(exits)} exit gates")

    if hmass.sum() == 0:
        sys.exit("home_mass is all-zero — check data_in/deso/ (run fetch_deso.py)")
    with open("web/data/flows.json") as f:
        measured = list(json.load(f)["flows"])

    trips, tour_lengths_km = generate_sensor_anchored_trips(
        rng, G, edges, hmass, amass, entries, exits, entry_ids, exit_ids,
        w_entry, w_exit, shape_hourly, measured,
        args.n_total, args.through_fraction, args.cross_fraction,
        args.gravity_km, args.is_weekend, args.min_per_sensor)

    n_through     = int(args.n_total * args.through_fraction)
    n_tours_total = (args.n_total - n_through) // 2
    n_cross    = int(n_tours_total * args.cross_fraction)
    n_internal = n_tours_total - n_cross
    n_ei = n_cross // 2
    n_ie = n_cross - n_ei

    trips.sort(key=lambda t: t[0])
    trips_path = SUMO_DIR / f"tours{args.out_suffix}.trips.xml"
    with open(trips_path, "w") as f:
        f.write("<routes>\n")
        for i, t in enumerate(trips):
            via = f' via="{t[3]}"' if len(t) > 3 else ""
            f.write(f'  <trip id="t{i}" depart="{t[0]:.1f}" '
                    f'from="{t[1]}" to="{t[2]}"{via}/>\n')
        f.write("</routes>\n")
    print(f"{len(trips)} trips written, all sensor-anchored — target mix "
          f"was {n_through} E-E through + {n_internal} I-I / {n_ei} E-I / "
          f"{n_ie} I-E paired tours (actual counts vary since some tour "
          f"attempts are dropped when no sensor naturally fits either leg)")

    fit = trip_length_fit(tour_lengths_km)
    fit["through_fraction"] = args.through_fraction
    fit["gravity_km"] = args.gravity_km
    with open(SUMO_DIR / f"trip_length_fit{args.out_suffix}.json", "w") as f:
        json.dump(fit, f, indent=1)
    print(f"  trip-length fit vs RVU short bins {RVU_SHORT_BIN_SHARES}: "
          f"generated {fit['shares']}  L1={fit['l1_distance']}  "
          f"(>10km: {fit.get('over_10km_pct', 0)}%)")

    home = sumo_home()
    out = SUMO_DIR / f"candidates{args.out_suffix}.rou.xml"
    weight_args = duarouter_weight_args(args.weight_file, args.weight_period)
    if args.weight_file:
        print(f"  routing by MEASURED travel time from {args.weight_file} "
              f"(congestion-feedback iteration)")
    res = subprocess.run(
        [str(home / "bin" / "duarouter"), "-n", str(NET_PATH),
         "--route-files", str(trips_path), "-o", str(out),
         *weight_args,
         "--weights.random-factor", str(args.route_diversity),
         # Default 5s barely discourages a literal U-turn at a via point when
         # it's duarouter's cheapest way to satisfy a forced via constraint
         # (verified: still ~14% of via-trips before this) — direction-aware
         # gate selection (above) handles the common case, this catches the
         # residual ones where a coarse straight-line gate filter still
         # picked a technically-"ahead" exit that the road network can't
         # reach without turning back.
         "--weights.turnaround-penalty", "300",
         "--seed", str(args.seed),
         "--ignore-errors", "--no-warnings", "--repair", "--remove-loops"],
        capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-1500:])
        sys.exit("duarouter failed")
    drop_uturn_routes(out)
    drop_excessive_detours(out, G, args.route_diversity)
    cross_report = report_sensor_cross_hits(out, measured)
    n = sum(1 for line in open(out) if "<vehicle" in line)
    print(f"Wrote {out}  ({n} routed candidates)")
    print(f"  sensor cross-hit diagnostic (sumo/sensor_coverage_report.json): "
          f"{ {m: r['total'] for m, r in cross_report.items()} }")


if __name__ == "__main__":
    main()
