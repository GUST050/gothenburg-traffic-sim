"""
Subarea/cordon candidate generator — Agent C's route population, grounded in
real data instead of graph-density proxies.

Run (or via build_sumo_demand, which calls this by default):
  python3 build_candidates.py [--through-fraction 0.15 --gravity-km 1.8]

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
(fetch_deso.py), spatialised to anonymous residential-building footprints
and their nearest usable road access.  Building footprint area and levels are
only a within-zone capacity proxy; every DeSO with routable access retains its
real aggregate total.  OSM footprints are cached open-data fallback until an
official ``data_in/deso/buildings.geojson`` is supplied; zones outside the
routable inner-city graph are reported rather than invented as local homes.

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
  --through-fraction   share of trips that are E-E (default 0.5; supply-tuned — see main())
  --gravity-km         distance-deterrence scale for tour destination choice

Writes sumo/candidates.rou.xml (identical contract to the old randomTrips
output — pfe.py is untouched).
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
import json
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import osmnx as ox
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from build_data import INNER_CITY_BBOX
from traffic_sim.simulation.runtime import sumo_home
from dirsplit.geo import bearing_deg, is_ahead
from demand.locations import (EndpointField, build_activity_fields,
                              build_home_field)

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

# Bounded-detour naturalness (2026-07-17): a sensor "explains" a movement
# when routing via it costs at most this much extra over the direct
# shortest path — max(VIA_DETOUR_ABS_S, VIA_DETOUR_FRAC × direct) seconds.
# Replaces the exact-shortest-path criterion (±0.5 s), which collapsed the
# through pool to 2 gate pairs city-wide and confined tour destinations to
# the shadow cone just behind each sensor — see
# via_is_natural_in_cost_matrix's docstring for the measurements.
VIA_DETOUR_ABS_S = 45.0
VIA_DETOUR_FRAC = 0.20

# Keep the conditioned-destination acceleration bounded: cache pressure must
# not become OS swapping on a smaller computer or as sensors are added.
CONDITIONED_MASK_CACHE_MAX_BYTES = 96 * 1024 * 1024


class ConditionedMaskCache:
    """Bounded LRU cache for compact per-anchor sensor membership fields."""

    def __init__(self, max_bytes: int = CONDITIONED_MASK_CACHE_MAX_BYTES):
        self.max_bytes = max_bytes
        self.bytes_used = 0
        self._entries: OrderedDict[int, tuple[np.ndarray, np.ndarray]] = OrderedDict()

    def get(self, anchor_pos: int) -> tuple[np.ndarray, np.ndarray] | None:
        entry = self._entries.get(anchor_pos)
        if entry is not None:
            self._entries.move_to_end(anchor_pos)
        return entry

    def put(self, anchor_pos: int, membership: np.ndarray,
            union: np.ndarray) -> None:
        size = int(membership.nbytes + union.nbytes)
        if size > self.max_bytes:
            return
        previous = self._entries.pop(anchor_pos, None)
        if previous is not None:
            self.bytes_used -= previous[0].nbytes + previous[1].nbytes
        while self._entries and self.bytes_used + size > self.max_bytes:
            _, evicted = self._entries.popitem(last=False)
            self.bytes_used -= evicted[0].nbytes + evicted[1].nbytes
        self._entries[anchor_pos] = (membership, union)
        self.bytes_used += size

# RVU Västra Götaland 2022-2023, fig. 11 (p.21), home leg excluded — the
# WEEKLY AVERAGE (43/33/24), not a weekday-specific figure (Fig.11's own
# caption has no day-type qualifier, and the underlying diary survey spans
# all 7 days). Added 2026-07-10: this was used as a FLAT constant regardless
# of day_kind (weekday/weekend/holiday) — meaning every simulated day, even
# a Sunday, silently drew purposes from a 7-day BLEND, overstating "arbete"
# on real weekends/holidays and understating it on real weekdays.
#
# PURPOSE_SHARES_WEEKDAY/WEEKEND below split this back into day-type-
# specific shares. RVU has no such split directly, so the SHAPE of the
# weekday->weekend shift is TRIANGULATED from TWO independent, verified
# external sources (2026-07-10 — checked against a second source after the
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


# PURPOSE_HOURLY_WEEKDAY/WEEKEND (2026-07-10): purpose ALSO varies by hour,
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
# 2026-07-10). Naively rescaling the WEEKDAY hourly shape for weekends
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

# Per-purpose trip-length SCALE on the deterrence kernel's β (2026-07-13;
# see IMPROVEMENT_PLAN.md's demand/destination integrity section).
# SOURCE: Trafikanalys "Resvanor i Sverige 2023" (official national
# RVU statistics, published Excel at trafa.se/globalassets/statistik/
# resvanor/2023/resvanor-i-sverige-2023.xlsx), Tabell 3 "Färdlängd
# (kilometer) per huvudresa ... efter huvudsakligt ärende och huvudsakligt
# färdsätt", CAR mode, year 2023:
#     arbete/tjänste/skola 24 km (±3)   -> ratio to all-purposes 24/37 = 0.65
#     service och inköp    30 km (±8)   -> 30/37 = 0.81
#     fritid               56 km (±9)   -> 56/37 = 1.51
#     samtliga             37 km (±4)
# National ABSOLUTE distances do not transfer to a ~7.8 km inner-city
# canvas (they include rural/intercity travel) — only the between-purpose
# RATIOS are used, and even those are SHRUNK 50% toward 1 (partial pooling,
# the research doc's own prescription for exactly this situation, and the
# same disclosed-shrinkage pattern dirsplit already uses for its national-
# to-local transfer; the wide CIs — service ±27% — are why the shrinkage is
# this conservative). The shrunk ratios are then NORMALIZED so the
# TRAFFIC-WEIGHTED weekday purpose mix gets a weighted mean scale of exactly
# 1.0 — the weekday aggregate calibration (gravity_km, RVU aggregate bins)
# is preserved and purposes only redistribute length AMONG themselves:
# fritid longer, arbete/service shorter, matching both the
# national table's ordering and VGR Analys 2023:56's local Tabell 3
# (avstånd till arbete/skola). Under the WEEKEND mix the mean scale comes
# out ~1.11 — deliberately NOT normalized away: leisure dominates weekends
# and leisure trips are the long ones, so slightly longer weekend trips is
# the survey-implied day-type signal, arriving through
# the joint P(length | purpose) × P(purpose | hour, day type).
# "external" (I-E exit-gate trips) has no survey purpose row — scale 1.0.
_PURPOSE_RAW_RATIO = {"arbete": 24 / 37, "service": 30 / 37, "fritid": 56 / 37}
_PURPOSE_SHRUNK = {p: 1.0 + 0.5 * (r - 1.0) for p, r in _PURPOSE_RAW_RATIO.items()}


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


def deterrence_weights(d_km: np.ndarray, gravity_km: float,
                       alpha: float = 0.0) -> np.ndarray:
    """Tanner/gamma trip-distance deterrence: (d/β)^α · exp(−d/β).

    alpha=0 reproduces the previous pure negative exponential EXACTLY
    (x^0 = 1). alpha>0 makes the kernel rise from zero to a mode at
    α·β km before decaying — the standard "combined" deterrence function
    of the gravity-model literature (Tanner; Wilson-family models; see
    IMPROVEMENT_PLAN.md for sources and the measured
    failure that motivated this).

    WHY (found 2026-07-12, Gustav watching the simulation: "many cars end
    their trip at the sensor or just next to it"): the sensor-anchoring
    mask (natural_far_end_weights) only admits destinations path-wise
    BEYOND the sensor, and a monotone-DECREASING exponential then always
    prefers the closest admitted destination — the edge immediately past
    the sensor. Measured on the real deployed artifacts: 36.5% of all
    simulated vehicles ended within 200 m of a sensor (random-edge
    baseline: 2.1%). Real trip-length distributions are unimodal with a
    mode of a few km (RVU Västra Götaland: only 9% of trips under 1 km),
    which no scale choice of a pure exponential can reproduce — the SHAPE
    was wrong, not the calibration (build_candidates' own history: "best
    fit across gravity_km 1-15km still under 5%" for RVU's 5.1-10 km bin).

    d=0 gets weight 0 for alpha>0 (0^α), which is desirable: a "trip" to
    the anchor's own edge is not a car trip."""
    with np.errstate(divide="ignore", invalid="ignore"):
        w = np.power(d_km / gravity_km, alpha) * np.exp(-d_km / gravity_km)
    return np.where(np.isfinite(w), w, 0.0)


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


# ONE definition of "near a sensor" for the whole destination-bias guard
# chain: the PFE structure cap (build_sumo_demand.structure_groups_for_shapes)
# and the drift metric below MUST use the same radius, or the enforced cap
# and the measured flag silently decohere.
NEAR_SENSOR_RADIUS_M = 200.0
# A routed pool that loses most of its requested trips is not a safe supply
# for PFE: it would force the solver to reuse a handful of shapes and recreate
# the same artificial sensor cluster through a different mechanism.
MIN_ROUTED_CANDIDATE_FRACTION = 0.75


def destination_sensor_proximity(dest_edge_ids: list[str],
                                 edge_latlon: dict[str, tuple[float, float]],
                                 sensor_edge_ids: list[str],
                                 radius_m: float = NEAR_SENSOR_RADIUS_M) -> dict:
    """Share of trips whose DESTINATION edge lies within radius_m of the
    nearest measured sensor edge, plus the all-edges baseline share for
    context (how much of the network is simply near a sensor anyway).

    THE guard metric for the 2026-07-12 destination-clustering bug ("many
    cars end their trip at the sensor or just next to it") — measured then
    at 36.5% of simulated vehicles within 200 m vs a 2.1% random-edge
    baseline. Sensor-anchored demand legitimately concentrates TRAFFIC near
    sensors; trips should not disproportionately END there. Edge positions
    are midpoints; distances use the same cos-corrected flat-earth maths as
    gravity_distance_km."""
    sensor_pts = [edge_latlon[s] for s in sensor_edge_ids if s in edge_latlon]
    if not sensor_pts:
        return {"radius_m": radius_m, "pct_within": None, "baseline_pct_within": None,
                "n": len(dest_edge_ids)}
    s_lats = np.array([p[0] for p in sensor_pts])
    s_lons = np.array([p[1] for p in sensor_pts])

    def near(lat: float, lon: float) -> bool:
        d_km = gravity_distance_km(s_lats, s_lons, lat, lon)
        return bool(d_km.min() * 1000.0 <= radius_m)

    n_near = sum(1 for e in dest_edge_ids
                 if e in edge_latlon and near(*edge_latlon[e]))
    n_known = sum(1 for e in dest_edge_ids if e in edge_latlon)
    all_near = sum(1 for ll in edge_latlon.values() if near(*ll))
    return {
        "radius_m": radius_m,
        "pct_within": round(100 * n_near / n_known, 1) if n_known else None,
        "baseline_pct_within": round(100 * all_near / len(edge_latlon), 1),
        "n": n_known,
    }


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


def load_sumo_routing_data(
    path: Path = NET_PATH,
) -> tuple[set[str], dict[str, float]]:
    """Return SUMO's routable edge IDs and free-flow travel-time costs.

    The GraphML snapshot intentionally contains a few OSM artefacts that
    ``build_sumo_net.py`` cannot emit (most notably self-loops).  Candidate
    origins and destinations must be drawn from the same edge universe as
    duarouter; otherwise SUMO's ``--repair`` can silently replace an invalid
    endpoint with a nearby valid edge, which changes the intended OD pair.
    Internal junction edges are excluded because they are implementation
    details, not routable demand edges.

    The cost is the minimum lane-level ``length / speed`` time for each
    edge.  It is intentionally parsed from the published SUMO network, not
    reconstructed from GraphML tags: this makes candidate naturalness and
    detour checks use the same free-flow metric as duarouter.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"SUMO network not found at {path}; run build_sumo_net.py first")
    root = ET.parse(path).getroot()
    edge_ids: set[str] = set()
    travel_times: dict[str, float] = {}
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if (edge_id and not edge_id.startswith(":")
                and edge.get("function") != "internal"):
            lane_times = []
            for lane in edge.findall("lane"):
                try:
                    length = float(lane.get("length", ""))
                    speed = float(lane.get("speed", ""))
                except ValueError:
                    continue
                if math.isfinite(length) and math.isfinite(speed) and length > 0 and speed > 0:
                    lane_times.append(length / speed)
            if not lane_times:
                raise ValueError(
                    f"SUMO edge {edge_id} has no valid lane travel time")
            edge_ids.add(edge_id)
            travel_times[edge_id] = min(lane_times)
    if not edge_ids:
        raise ValueError(f"SUMO network {path} contains no routable edges")
    return edge_ids, travel_times


def load_sumo_edge_ids(path: Path = NET_PATH) -> set[str]:
    """Backward-compatible edge-ID view of :func:`load_sumo_routing_data`."""
    return load_sumo_routing_data(path)[0]


def load_graph_edges(G, routable_edge_ids: set[str] | None = None):
    """Build candidate edge records, restricted to SUMO-routable edges.

    ``build_sumo_net.py`` deliberately skips ``u == v`` self-loops.  Skip
    them here even when a caller does not provide the parsed SUMO edge set so
    no synthetic endpoint can ever be a graph artefact.  When the set is
    supplied, it is the final authority and also removes any other GraphML
    edge that netconvert did not publish.
    """
    edges = []
    for u, v, k, d in G.edges(keys=True, data=True):
        edge_id = f"{u}_{v}_{k}"
        if u == v or (routable_edge_ids is not None
                      and edge_id not in routable_edge_ids):
            continue
        lat = (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2
        lon = (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2
        hw = str(scalar(d.get("highway", "")) or "")
        edges.append({"id": edge_id, "u": u, "v": v,
                      "lat": lat, "lon": lon, "hw": hw,
                      "len": float(d.get("length", 0))})
    return edges


def routable_graph(G, routable_edge_ids: set[str]):
    """Return a copy containing exactly the edges published in SUMO.

    Filtering only the endpoint arrays is insufficient: shortest-path masks,
    gate pairing and detour checks also read the graph.  Keeping one canonical
    graph for all of those operations prevents a GraphML-only shortcut from
    influencing demand that SUMO cannot actually route.
    """
    H = G.copy()
    remove = [
        (u, v, k) for u, v, k in H.edges(keys=True)
        if u == v or f"{u}_{v}_{k}" not in routable_edge_ids
    ]
    H.remove_edges_from(remove)
    if H.number_of_edges() == 0:
        raise ValueError("SUMO edge filter removed every graph edge")
    return H


def routing_cost(edge_id: str, edge_data: dict,
                 edge_costs: dict[str, float] | None = None) -> float:
    """Return one additive routing cost, failing closed on bad SUMO data.

    ``None`` retains the length-based behaviour used by small synthetic unit
    graphs and compatibility callers.  Production candidate generation passes
    SUMO's published free-flow travel-time table, so no GraphML-only speed
    assumption can make a route look natural when duarouter considers it a
    detour (or the reverse).
    """
    raw = edge_data.get("length", 1.0) if edge_costs is None else edge_costs.get(edge_id)
    if raw is None:
        raise ValueError(f"missing SUMO routing cost for edge {edge_id}")
    try:
        cost = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid routing cost for edge {edge_id}: {raw!r}") from exc
    if not math.isfinite(cost) or cost <= 0:
        raise ValueError(f"non-positive routing cost for edge {edge_id}: {cost!r}")
    return cost


def routing_cost_for_edge_id(G, edge_id: str,
                             edge_costs: dict[str, float] | None = None) -> float:
    """Resolve one stable ``u_v_key`` edge ID to its routing cost."""
    try:
        u, v, k = (int(part) for part in edge_id.split("_"))
        edge_data = G[u][v][k]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"edge {edge_id!r} is absent from routing graph") from exc
    return routing_cost(edge_id, edge_data, edge_costs)


def exclude_sensor_endpoint_mass(
    edges: list[dict], hmass: np.ndarray,
    amass: dict[str, np.ndarray], measured: list[str],
) -> int:
    """Remove measured edges from synthetic origin/destination mass.

    A sensor is an observation constraint, not a population residence or an
    activity destination. Sensor edges remain in ``edges`` for geometry,
    routing and calibration; only endpoint probability mass is removed.
    """
    sensor_ids = set(measured)
    mask = np.fromiter((edge["id"] in sensor_ids for edge in edges),
                       dtype=bool, count=len(edges))
    if not mask.any():
        return 0
    if hmass.shape != (len(edges),):
        raise ValueError("home mass shape does not match candidate edges")
    hmass[mask] = 0.0
    for category, mass in amass.items():
        if mass.shape != (len(edges),):
            raise ValueError(
                f"activity mass '{category}' shape does not match candidate edges")
        mass[mask] = 0.0
    return int(mask.sum())


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

    zone_entries = [(f["properties"]["desokod"], shape(f["geometry"]))
                    for f in zones if f["properties"]["desokod"] in pop]
    zone_codes = [c for c, _ in zone_entries]
    zone_geoms = [p for _, p in zone_entries]
    # STRtree: bounding-box pre-filter before the exact .contains() check —
    # PROFILED 2026-07-10: 534k point-in-polygon tests against all 116
    # zones (linear scan) took 2.8s; querying the tree's small candidate
    # set per point first cuts that to 0.27s (10x), with mathematically
    # IDENTICAL output (verified bit-for-bit against the linear-scan
    # version on the real network before adopting this) since the tree
    # only narrows which polygons get the same exact .contains() test, it
    # never changes the test's result.
    zone_tree = STRtree(zone_geoms)
    for i, e in enumerate(edges):
        pt = Point(e["lon"], e["lat"])
        for idx in zone_tree.query(pt):
            if zone_geoms[idx].contains(pt):
                code = zone_codes[idx]
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


def home_location_field(G, edges: list[dict], measured: list[str]) -> EndpointField:
    """Build DeSO-conserving anonymous home locations for route endpoints.

    ``home_mass`` remains the small, pure legacy helper used by unit tests and
    by the documented fallback inside :mod:`demand.locations`.  Production
    candidate generation uses this building-based field instead: DeSO totals
    stay unchanged, but their within-zone distribution is over anonymous
    residential footprints rather than road length.
    """
    zones, population = ensure_deso()
    field = build_home_field(
        G, edges, zones, population, DESO_DIR, INNER_CITY_BBOX, RESIDENTIAL,
        excluded_edge_ids=set(measured))
    report = field.report
    print("  home locations: "
          f"{report['source']}, {report['zones_building']}/"
          f"{report['zones_total']} DeSO zones from building footprints, "
          f"{report['zones_road_fallback']} road-length fallback; "
          f"{field.mass.sum():,.0f} residents allocated across "
          f"{len(field.pools_by_edge)} access edges")
    if report.get("population_without_routable_access", 0.0) > 0.01:
        print("  WARNING home locations: "
              f"{report['population_without_routable_access']:,.0f} residents in "
              f"{len(report.get('zones_without_routable_access', []))} DeSO zone(s) "
              "have no edge in the inner-city graph; they are not fabricated as "
              "interior homes")
    return field


def activity_location_fields(G, edges: list[dict], measured: list[str]
                             ) -> dict[str, EndpointField]:
    """Return purpose-specific POI access fields, one endpoint per POI.

    This replaces the previous 150m multi-edge halo.  The new mapping both
    gives return trips a real POI departure position and prevents a single
    POI near a sensor from becoming mass on every adjacent link.
    """
    fields = build_activity_fields(
        G, edges, DESO_DIR, INNER_CITY_BBOX, POI_TAGS,
        excluded_edge_ids=set(measured))
    for category, field in fields.items():
        report = field.report
        print(f"  activity locations '{category}': "
              f"{report['locations_mapped']}/{report['locations_input']} OSM POIs "
              f"on {report['access_edges']} access edges")
    return fields


def find_gates(G, routable_edge_ids: set[str] | None = None):
    entries, exits = [], []
    if routable_edge_ids is None:
        in_degree = {node: G.in_degree(node) for node in G.nodes}
        out_degree = {node: G.out_degree(node) for node in G.nodes}
    else:
        # Degree must be computed in the same published SUMO edge universe.
        # A skipped self-loop otherwise makes a true boundary node look
        # connected and removes its only valid entry/exit gate.
        in_degree = {node: 0 for node in G.nodes}
        out_degree = {node: 0 for node in G.nodes}
        for u, v, k in G.edges(keys=True):
            edge_id = f"{u}_{v}_{k}"
            if u != v and edge_id in routable_edge_ids:
                out_degree[u] += 1
                in_degree[v] += 1
    for u, v, k in G.edges(keys=True):
        edge_id = f"{u}_{v}_{k}"
        if u == v or (routable_edge_ids is not None
                      and edge_id not in routable_edge_ids):
            continue
        if in_degree[u] == 0:
            entries.append((edge_id, u))
        if out_degree[v] == 0:
            exits.append((edge_id, v))
    return entries, exits


def gate_weights(G, gates: list[tuple[str, int]],
                 edge_daily_load: dict[str, float] | None = None) -> np.ndarray:
    """Relative draw probability per gate.

    Preferred source: the gravity/Dial structural assignment field
    (assignment_priors.py) evaluated on the gate edges — how much flow the
    network's own geometry and activity distribution says each approach
    carries.  Weights are normalised, so the field's measured-data scale
    factor cancels and only the measurement-independent structure remains
    (LOSO-safe).

    WHY (measured 2026-07-17): the old road-class proxy gave every motorway
    gate the same weight, so the busiest approach (Boråsleden) received
    0.37% of candidate draws while calibration assigned it 19-21% of quarter
    flow.  Its main corridor ended up with ONE pool shape, onto which the
    PFE stacked 1 486 vehicles/day — visible in the browser as convoys of
    identical trips.  Draw density must follow expected flow, or the pool
    cannot offer the calibration enough distinct routes where they matter.

    The class prior remains as fallback (missing/degenerate field), and a
    5%-of-mean floor keeps every mapped gate drawable so a low-load gate is
    still represented in the pool.
    """
    class_w = []
    for eid, _ in gates:
        u, v, k = map(int, eid.split("_"))
        hw = str(scalar(G.get_edge_data(u, v, k).get("highway", "")) or "")
        class_w.append(GATE_WEIGHT.get(hw, 1))
    class_w = np.array(class_w, dtype=float)
    if edge_daily_load:
        load = np.array([float(edge_daily_load.get(eid, 0.0))
                         for eid, _ in gates])
        covered = load > 0
        if covered.sum() >= max(2, len(gates) // 2):
            floor = 0.05 * load[covered].mean()
            w = np.maximum(load, floor)
            return w / w.sum()
    return class_w / class_w.sum()


def load_assignment_gate_loads(path: Path) -> dict[str, float]:
    """Per-edge daily structural load from the assignment-prior artifact.

    Returns {} when the artifact is absent or unreadable — gate_weights then
    falls back to the road-class prior, so a --no-assignment-prior or legacy
    build keeps working unchanged."""
    try:
        with open(path) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    loads: dict[str, float] = {}
    for eid, series in (payload.get("flows") or {}).items():
        try:
            total = float(sum(series)) if isinstance(series, list) \
                else float(series)
        except (TypeError, ValueError):
            continue
        if total > 0:
            loads[eid] = total
    return loads


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


def load_trip_intents(path: Path) -> dict[str, dict[str, str | None]]:
    """Load the original candidate requests, which are routing authority."""
    if not path.exists():
        raise FileNotFoundError(path)
    intents: dict[str, dict[str, str | None]] = {}
    for trip in ET.parse(path).getroot().findall("trip"):
        trip_id = trip.get("id")
        if not trip_id or trip_id in intents:
            raise ValueError(f"invalid or duplicate trip id in {path}: {trip_id!r}")
        intents[trip_id] = {
            "origin_edge": trip.get("from"),
            "destination_edge": trip.get("to"),
            "via_edge": trip.get("via") or None,
        }
    return intents


def via_edges(via: str | None) -> tuple[str, ...]:
    """Normalise SUMO's whitespace-separated optional ``via`` edge list."""
    return tuple(via.split()) if via else ()


def route_visits_vias_in_order(route_edges: list[str], vias: tuple[str, ...]) -> bool:
    """Whether a concrete route contains every requested via edge in order."""
    pos = 0
    for edge_id in route_edges:
        if pos < len(vias) and edge_id == vias[pos]:
            pos += 1
    return pos == len(vias)


def validate_trip_intents(
    intents: dict[str, dict[str, str | None]], sumo_edge_ids: set[str],
    measured: list[str] | tuple[str, ...] = (),
) -> None:
    """Reject malformed candidate requests before duarouter can repair them.

    A generated candidate must have valid, distinct non-sensor endpoints.
    This is intentionally stricter than duarouter: its repair behaviour is
    useful for diagnostics but must never substitute a new OD pair for PFE.
    ``via`` may contain multiple edges, but it cannot duplicate or overlap an
    endpoint because that would encode a loop by construction.
    """
    sensors = set(measured)
    errors: list[str] = []
    for trip_id, intent in intents.items():
        origin = intent.get("origin_edge")
        destination = intent.get("destination_edge")
        vias = via_edges(intent.get("via_edge"))
        reasons = []
        if not origin:
            reasons.append("missing_origin")
        if not destination:
            reasons.append("missing_destination")
        if origin and origin not in sumo_edge_ids:
            reasons.append("origin_not_sumo")
        if destination and destination not in sumo_edge_ids:
            reasons.append("destination_not_sumo")
        unknown_vias = [edge for edge in vias if edge not in sumo_edge_ids]
        if unknown_vias:
            reasons.append("via_not_sumo")
        if origin and destination and origin == destination:
            reasons.append("same_origin_destination")
        if origin in sensors or destination in sensors:
            reasons.append("sensor_endpoint")
        if ((origin and origin in vias)
                or (destination and destination in vias)):
            reasons.append("via_overlaps_endpoint")
        if len(set(vias)) != len(vias):
            reasons.append("duplicate_via")
        if reasons:
            errors.append(f"{trip_id}: {'/'.join(reasons)}")
    if errors:
        preview = "; ".join(errors[:5])
        more = f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""
        raise ValueError(f"invalid candidate trip intents: {preview}{more}")


def validate_routed_candidates(
    routed_path: Path,
    metadata_path: Path | None = None,
    measured: list[str] | tuple[str, ...] = (),
    sumo_edge_ids: set[str] | None = None,
    intents_path: Path | None = None,
) -> dict:
    """Reject routes whose realised endpoints differ from their intent.

    ``duarouter --repair`` is useful for recovering minor connectivity
    issues, but it is not allowed to mutate an OD candidate silently.  The
    original ``<trip>`` file is the authoritative request; compare its
    origin, destination and via edge to the route SUMO actually wrote before
    PFE can amplify the pool.  The metadata sidecar is only an audit/fallback
    for legacy callers.  The same pass rejects sensor endpoints and edges
    absent from the published SUMO network.

    Legacy random pools have no trip-intent file or sidecar metadata.  They
    still receive the SUMO-edge and sensor-endpoint checks.  The sidecar is
    pruned to the routes that survived so later reports describe the exact
    candidate population rather than stale trip intents.
    """
    if not routed_path.exists():
        raise FileNotFoundError(routed_path)

    metadata: dict[str, dict] = {}
    metadata_doc: dict | None = None
    if metadata_path is not None and metadata_path.exists():
        with open(metadata_path) as f:
            metadata_doc = json.load(f)
        raw = metadata_doc.get("candidates", {})
        if isinstance(raw, dict):
            metadata = raw

    # Prefer the actual trip XML over derived metadata.  This makes endpoint
    # validation fail closed if a sidecar is stale, truncated or accidentally
    # omitted a candidate record.
    intents: dict[str, dict[str, str | None]] = {}
    if intents_path is not None:
        intents = load_trip_intents(intents_path)
    elif metadata_doc is not None and isinstance(metadata_doc.get("candidates"), dict):
        intents = {
            str(vehicle_id): {
                "origin_edge": intent.get("origin_edge"),
                "destination_edge": intent.get("destination_edge"),
                "via_edge": intent.get("via_edge"),
            }
            for vehicle_id, intent in metadata.items()
            if isinstance(intent, dict)
        }

    sensor_ids = set(measured)
    root = ET.parse(routed_path).getroot()
    kept_ids: set[str] = set()
    seen_vehicle_ids: set[str] = set()
    reasons: dict[str, int] = {}
    dropped = 0

    def add_reason(name: str) -> None:
        reasons[name] = reasons.get(name, 0) + 1

    for vehicle in list(root.findall("vehicle")):
        vehicle_id = vehicle.get("id", "")
        route = vehicle.find("route")
        route_edges = (route.get("edges", "").split()
                       if route is not None else [])
        route_reasons: list[str] = []

        if vehicle_id in seen_vehicle_ids:
            # Keep at most one realisation of an input trip. A duplicated
            # vehicle would otherwise become a second PFE candidate with the
            # same provenance and silently double its available shape.
            route_reasons.append("duplicate_candidate_id")
        seen_vehicle_ids.add(vehicle_id)

        if not route_edges:
            route_reasons.append("missing_route")
        if sumo_edge_ids is not None:
            unknown = [edge for edge in route_edges
                       if edge not in sumo_edge_ids]
            if unknown:
                route_reasons.append("unknown_sumo_edge")

        intent = intents.get(vehicle_id)
        if intents_path is not None and intent is None:
            route_reasons.append("unknown_candidate_id")
        if intent is not None:
            expected = {
                "origin_edge": intent.get("origin_edge"),
                "destination_edge": intent.get("destination_edge"),
                "via_edge": intent.get("via_edge"),
            }
            for key in ("origin_edge", "destination_edge", "via_edge"):
                edge_id = expected[key]
                if (sumo_edge_ids is not None and edge_id
                        and edge_id not in sumo_edge_ids):
                    route_reasons.append(f"declared_{key}_not_sumo")
            if route_edges and expected["origin_edge"] != route_edges[0]:
                route_reasons.append("origin_mismatch")
            if route_edges and expected["destination_edge"] != route_edges[-1]:
                route_reasons.append("destination_mismatch")
            if not route_visits_vias_in_order(
                    route_edges, via_edges(expected["via_edge"])):
                route_reasons.append("via_missing")

        if route_edges and (route_edges[0] in sensor_ids
                            or route_edges[-1] in sensor_ids):
            route_reasons.append("sensor_endpoint")

        if route_reasons:
            dropped += 1
            for reason in set(route_reasons):
                add_reason(reason)
            root.remove(vehicle)
        else:
            kept_ids.add(vehicle_id)

    if dropped:
        ET.ElementTree(root).write(routed_path)

    # Keep the audit sidecar aligned with the actual XML after duarouter has
    # dropped/repair-filtered trips.  This is deliberately best-effort for
    # legacy pools, which do not have a metadata file at all.
    if metadata_doc is not None and metadata_path is not None:
        canonical_metadata: dict[str, dict] = {}
        for vehicle_id in sorted(kept_ids):
            stored = metadata.get(vehicle_id, {})
            record = dict(stored) if isinstance(stored, dict) else {}
            # The XML request is the authority for OD/via. PFE propagates
            # this sidecar into calibrated agent provenance, so retaining a
            # stale sidecar here would make later reporting contradict the
            # concrete route even when endpoint validation passed.
            if vehicle_id in intents:
                record.update(intents[vehicle_id])
            canonical_metadata[vehicle_id] = record
        metadata_doc["candidates"] = canonical_metadata
        with open(metadata_path, "w") as f:
            json.dump(metadata_doc, f, separators=(",", ":"))

    missing_requested = (len(set(intents) - seen_vehicle_ids)
                         if intents_path is not None else 0)
    return {
        "requested": len(intents) if intents_path is not None else None,
        "checked": len(kept_ids) + dropped,
        "kept": len(kept_ids),
        "dropped": dropped,
        "missing_requested": missing_requested,
        "reasons": reasons,
    }


def prune_candidate_metadata(metadata_path: Path | None, routed_path: Path) -> None:
    """Keep the candidate sidecar aligned after later loop/detour filters."""
    if metadata_path is None or not metadata_path.exists():
        return
    with open(metadata_path) as f:
        document = json.load(f)
    candidates = document.get("candidates")
    if not isinstance(candidates, dict):
        return
    route_ids = {
        vehicle.get("id") for vehicle in ET.parse(routed_path).getroot().findall("vehicle")
    }
    document["candidates"] = {
        vehicle_id: intent for vehicle_id, intent in candidates.items()
        if vehicle_id in route_ids
    }
    with open(metadata_path, "w") as f:
        json.dump(document, f, separators=(",", ":"))


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


def drop_excessive_detours(path: Path, G, max_stretch: float,
                           edge_costs: dict[str, float] | None = None) -> None:
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
    true-shortest-path costs are batched: one scipy Dijkstra call per
    DISTINCT ENTRY node (returning distances to every other node at once),
    not one networkx call per distinct (entry, exit) pair.

    PERFORMANCE (2026-07-10, profiled on a real 12k-candidate run:
    networkx's per-pair shortest_path_length was the single largest
    Python-side cost, 30s of an 84s total run): batching by source through
    scipy.sparse.csgraph.dijkstra measured ~28x faster on the real graph
    (16.2s -> 0.58s for the same 4232 distinct pairs) with ZERO
    discrepancies against the networkx version — but only after fixing a
    real bug found while building this: naively building the sparse
    matrix from every (u, v, routing-cost) edge triple lets scipy's csr_matrix
    SUM duplicate (u, v) entries for the graph's 52 parallel-edge node
    pairs, corrupting distances for any path touching one. Fixed by
    keeping the MINIMUM travel-time cost per (u, v) pair before building
    the matrix — the same "shortest path routing should use the faster
    link, not an arbitrary or summed one" principle as
    assignment_priors.py's fastest_parallel_edge_times. The local helper
    remains separate because this module reads the published SUMO network,
    which is the route generator's authoritative cost source."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra as sp_dijkstra

    tree = ET.parse(path)
    root = tree.getroot()
    vehicles = list(root)

    # Pass 1: collect each vehicle's (entry, exit, realized cost) without
    # mutating the tree yet — needed before batching Dijkstra by entry node.
    info: list[tuple[object, int, int, float]] = []
    entries_needed: set[int] = set()
    for veh in vehicles:
        route = veh.find("route")
        edges = route.get("edges").split()
        entry = int(edges[0].split("_")[0])
        exit_ = int(edges[-1].split("_")[1])
        realized = sum(
            routing_cost_for_edge_id(G, edge_id, edge_costs)
            for edge_id in edges)
        info.append((veh, entry, exit_, realized))
        entries_needed.add(entry)

    nodes = list(G.nodes())
    node_idx = {n: i for i, n in enumerate(nodes)}
    min_len: dict[tuple[int, int], float] = {}
    for u, v, k, d in G.edges(keys=True, data=True):
        key = (node_idx[u], node_idx[v])
        w = routing_cost(f"{u}_{v}_{k}", d, edge_costs)
        if key not in min_len or w < min_len[key]:
            min_len[key] = w
    rows = [k[0] for k in min_len]
    cols = [k[1] for k in min_len]
    weights = [min_len[k] for k in min_len]
    mat = csr_matrix((weights, (rows, cols)), shape=(len(nodes), len(nodes)))

    sources = sorted(entries_needed)
    src_row = {s: i for i, s in enumerate(sources)}
    dist = sp_dijkstra(mat, directed=True,
                       indices=[node_idx[s] for s in sources])

    dropped = 0
    for veh, entry, exit_, realized in info:
        d = dist[src_row[entry], node_idx[exit_]]
        true_cost = None if np.isinf(d) else float(d)
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


def via_is_natural_in_cost_matrix(
    D: np.ndarray, node_idx: dict[int, int], entry_v: int, exit_u: int,
    m_u: int, m_v: int, via_cost: float,
    detour_abs_s: float = VIA_DETOUR_ABS_S,
    detour_frac: float = VIA_DETOUR_FRAC,
) -> bool:
    """Whether a forced via edge is a REALISTIC route choice for the trip.

    SEMANTICS CHANGED 2026-07-17 (root cause of the endpoint-accuracy
    defect Gustav reported): the old criterion demanded the via edge lie on
    the EXACT shortest path (±0.5 s).  Measured on the real network, that
    left 6 of 7 sensor edges with ZERO verified through gate pairs — all
    6 000 through candidates (76% of calibrated traffic) entered at 2
    street cuts and exited at 1, and the tour destination masks admitted
    only the shadow cone immediately behind each sensor (destinations
    piling up at the sensor, whole districts with zero endpoints).  Real
    route choice is stochastic-multipath — the same finding this project
    already validated for assignment_priors.py ("plain shortest-path
    collapses onto one canonical route").  A sensor therefore explains a
    movement when driving via it costs at most a bounded detour:
    ``via - direct <= max(detour_abs_s, detour_frac * direct)``.
    At the measured 3-7 min gate-to-gate trips the defaults admit 45-90 s
    detours — far below drop_excessive_detours' 2.0x pathological-route
    cutoff, which (with the node-revisit guards) still excludes the
    U-turn class this check originally protected against.  Measured pair
    admission per sensor at these defaults: 18-58 (union 265) vs 0-2
    (union 2) under the exact rule."""
    try:
        direct = D[node_idx[entry_v], node_idx[exit_u]]
        via = (D[node_idx[entry_v], node_idx[m_u]] + via_cost
               + D[node_idx[m_v], node_idx[exit_u]])
    except KeyError:
        return False
    if not math.isfinite(direct) or not math.isfinite(via):
        return False
    tolerance = max(detour_abs_s, detour_frac * direct)
    return via - direct <= tolerance


def via_naturally_on_path(
    G, entry_v: int, exit_u: int, m_u: int, m_v: int,
    m_edge_id: str | None = None,
    edge_costs: dict[str, float] | None = None,
) -> bool:
    """True when forcing a via edge leaves the fastest route unchanged.

    Production callers provide SUMO free-flow edge costs, so this agrees with
    duarouter's initial routing metric. The optional default remains graph
    length for compact synthetic tests and compatibility callers.
    """
    if m_edge_id is None:
        candidates = [
            routing_cost(f"{m_u}_{m_v}_{k}", d, edge_costs)
            for k, d in G.get_edge_data(m_u, m_v, default={}).items()
        ]
        if not candidates:
            return False
        via_cost = min(candidates)
    else:
        via_cost = routing_cost_for_edge_id(G, m_edge_id, edge_costs)
    D, node_idx = build_shortest_distance_matrix(G, edge_costs)
    return via_is_natural_in_cost_matrix(
        D, node_idx, entry_v, exit_u, m_u, m_v, via_cost)


def build_shortest_distance_matrix(
    G, edge_costs: dict[str, float] | None = None,
) -> tuple[np.ndarray, dict[int, int]]:
    """All-pairs shortest routing cost, computed ONCE per run via
    scipy.sparse.csgraph.dijkstra (scipy is already a project dependency —
    pfe.py, observability.py) — measured 1.39s / 92.6MB on the real
    3402-node production graph. This is what makes
    natural_far_end_weights() an O(1) array lookup per candidate instead
    of a real nx.shortest_path call (2.23ms measured) — the difference
    between bounded retries being free vs. costing real minutes per
    sensor, which is exactly the kind of cost this session already hit
    once with the (reverted) duarouter-retry approach to via-trip
    coverage.

    Parallel edges (MultiDiGraph) are collapsed to their MINIMUM routing cost —
    for shortest-path purposes only the cheapest of several parallel
    edges between the same two nodes ever matters; scipy's sparse matrix
    constructor SUMS duplicate (row, col) entries by default, which would
    silently corrupt distances if left to it."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra

    nodes = list(G.nodes())
    node_idx = {n: i for i, n in enumerate(nodes)}
    best: dict[tuple[int, int], float] = {}
    for u, v, k, d in G.edges(keys=True, data=True):
        key = (node_idx[u], node_idx[v])
        edge_id = f"{u}_{v}_{k}"
        cost = routing_cost(edge_id, d, edge_costs)
        if key not in best or cost < best[key]:
            best[key] = cost
    rows, cols, weights = zip(*((r, c, w) for (r, c), w in best.items()))
    adj = csr_matrix((weights, (rows, cols)), shape=(len(nodes), len(nodes)))
    D = dijkstra(adj, directed=True)
    return D, node_idx


def shortest_paths_use_node(
    D: np.ndarray, start_idx: np.ndarray | int, through_idx: int,
    end_idx: np.ndarray | int, tol_abs: float = 0.5,
    tol_rel: float = 1e-6,
) -> np.ndarray:
    """Conservative mask for shortest paths that can pass through a node.

    Equality is intentional: if a node lies on *any* shortest path, it is
    unsafe as a forbidden turn-around point when demand must preserve an
    already-traversed endpoint edge.  Excluding ties is preferable to letting
    SUMO's route randomisation choose a path that immediately reverses that
    edge and is later repaired away.
    """
    starts = np.asarray(start_idx, dtype=int)
    ends = np.asarray(end_idx, dtype=int)
    direct = D[starts, ends]
    via = D[starts, through_idx] + D[through_idx, ends]
    finite = np.isfinite(direct) & np.isfinite(via)
    tol = np.maximum(tol_abs, tol_rel * np.where(finite, direct, 0.0))
    with np.errstate(invalid="ignore"):
        return finite & (np.abs(via - direct) <= tol)


def natural_far_end_weights(
    D: np.ndarray, node_idx: dict[int, int],
    anchor_v: int, m_u: int, m_v: int, m_length: float,
    candidate_u_idx: np.ndarray, base_weights: np.ndarray,
    detour_abs_s: float = VIA_DETOUR_ABS_S,
    detour_frac: float = VIA_DETOUR_FRAC,
    anchor_u: int | None = None,
    candidate_v_idx: np.ndarray | None = None,
) -> np.ndarray:
    """Vectorized generalization of via_naturally_on_path — same
    bounded-detour semantics (is routing anchor->m_u->m_v->candidate a
    REALISTIC route for anchor->candidate, i.e. within
    max(detour_abs_s, detour_frac × direct) of the shortest path — see
    via_is_natural_in_cost_matrix's docstring for the 2026-07-17 semantics
    change and its measurements), checked via distance ARITHMETIC on the
    precomputed all-pairs matrix instead of tracing one nx.shortest_path
    per candidate. This masks (never reinterprets) whatever weighting
    a trip category already computes — base_weights passes through
    unchanged wherever the candidate is natural, and becomes 0 elsewhere,
    so gravity_km's existing calibration and meaning are untouched.
    The bounded detour is what keeps the admitted set from collapsing to
    the shadow cone just behind the sensor (the measured near-sensor
    destination pile-up under the old exact-shortest-path rule).

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
    tol = np.maximum(detour_abs_s,
                     detour_frac * np.where(finite, d_direct, 0.0))
    # inf - inf (both anchor and via-route unreachable to a candidate) is a
    # real, expected case on a graph with disconnected components — numpy
    # eagerly computes via_dist - d_direct for the WHOLE array even inside
    # np.where, so it produces a nan there regardless; `finite` already
    # excludes that element from `natural` correctly, this just silences
    # the resulting (harmless) RuntimeWarning.
    with np.errstate(invalid="ignore"):
        natural = finite & (via_dist - d_direct <= tol)
    if anchor_u is not None:
        # The fixed origin edge has already been traversed.  If a shortest
        # anchor->via or via->destination leg can revisit its start node, the
        # concatenated route would need to reverse that edge.
        avoid = shortest_paths_use_node(
            D, a, node_idx[anchor_u], np.array([node_idx[m_u]])
        )[0]
        if avoid:
            natural[:] = False
        else:
            natural &= ~shortest_paths_use_node(
                D, node_idx[m_v], node_idx[anchor_u], candidate_u_idx)
    if candidate_v_idx is not None:
        # Symmetric guard for a destination edge: the path to its entry node
        # must not already reach its exit node and then come back.
        natural &= ~shortest_paths_use_node(
            D, node_idx[m_v], candidate_v_idx, candidate_u_idx)
    return np.where(natural, base_weights, 0.0)


def natural_sensor_masks(
    D: np.ndarray, node_idx: dict[int, int], anchor_v: int,
    m_info: dict[str, tuple[int, int, float]],
    dest_u_idx: np.ndarray,
    detour_abs_s: float = VIA_DETOUR_ABS_S,
    detour_frac: float = VIA_DETOUR_FRAC,
    anchor_u: int | None = None,
    dest_v_idx: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """For ONE anchor: per-sensor boolean masks over the destination pool —
    natural_m(dest) = "routing anchor→sensor m→dest is a realistic route
    for anchor→dest" under the bounded-detour rule (see
    via_is_natural_in_cost_matrix's 2026-07-17 semantics change) — the
    same arithmetic as natural_far_end_weights, returned per sensor so the
    caller can take both the UNION (does any sensor lie naturally on the
    way?) and the attribution (which ones?).

    Built for the 2026-07-12 destination-clustering fix (see the
    demand/destination integrity section of IMPROVEMENT_PLAN.md): instead of
    forcing every anchor draw through one pre-chosen sensor and
    renormalizing within whatever that sensor's mask happens to admit
    (which lets an anchor whose only admissible destinations are next to
    the sensor emit exactly those with probability 1 — the measured
    dominant near-sensor bias), the tour sampler now draws destinations
    from the city-wide activity field CONDITIONED on the union mask, and
    anchors are accepted in proportion to their sensor-passing mass
    (rejection sampling of the joint), which is the exact conditional
    P(anchor, dest | natural route passes ≥1 sensor)."""
    a = node_idx[anchor_v]
    d_direct = D[a, dest_u_idx]
    finite_direct = np.isfinite(d_direct)
    tol = np.maximum(detour_abs_s,
                     detour_frac * np.where(finite_direct, d_direct, 0.0))
    masks: dict[str, np.ndarray] = {}
    for m_edge, (m_u, m_v, m_length) in m_info.items():
        via = D[a, node_idx[m_u]] + m_length + D[node_idx[m_v], dest_u_idx]
        finite = finite_direct & np.isfinite(via)
        with np.errstate(invalid="ignore"):
            masks[m_edge] = finite & (via - d_direct <= tol)
        if anchor_u is not None:
            pre_uses_anchor = shortest_paths_use_node(
                D, a, node_idx[anchor_u], np.array([node_idx[m_u]])
            )[0]
            if pre_uses_anchor:
                masks[m_edge][:] = False
            else:
                masks[m_edge] &= ~shortest_paths_use_node(
                    D, node_idx[m_v], node_idx[anchor_u], dest_u_idx)
        if dest_v_idx is not None:
            masks[m_edge] &= ~shortest_paths_use_node(
                D, node_idx[m_v], dest_v_idx, dest_u_idx)
    return masks


def pack_sensor_membership(masks: dict[str, np.ndarray], measured: list[str]
                           ) -> tuple[np.ndarray, np.ndarray]:
    """Losslessly pack per-sensor masks for the conditioned-anchor cache.

    The acceptance test needs the union only, while attribution after a
    successful draw needs the individual sensor membership.  Packing preserves
    every bit but avoids retaining one full bool vector per sensor and anchor.
    """
    matrix = np.stack([masks[edge_id] for edge_id in measured], axis=0)
    membership = np.packbits(matrix, axis=0, bitorder="little")
    return membership, np.any(membership, axis=0)


def sensors_at_membership(membership: np.ndarray, measured: list[str],
                          pos: int) -> list[str]:
    """Return the measured edges whose packed naturalness bit is set at pos."""
    return [edge_id for i, edge_id in enumerate(measured)
            if int(membership[i // 8, pos]) & (1 << (i % 8))]


def natural_origin_weights(
    D: np.ndarray, node_idx: dict[int, int],
    candidate_v_idx: np.ndarray, m_u: int, m_v: int, m_length: float,
    dest_u: int, base_weights: np.ndarray,
    detour_abs_s: float = VIA_DETOUR_ABS_S,
    detour_frac: float = VIA_DETOUR_FRAC,
    candidate_u_idx: np.ndarray | None = None,
    dest_v: int | None = None,
) -> np.ndarray:
    """Mirror of natural_far_end_weights — there the ORIGIN was fixed and
    many DESTINATION candidates were masked; here the DESTINATION
    (dest_u) is fixed and many ORIGIN candidates are masked instead
    (candidate->m_u->m_v->dest_u must be within the bounded detour of the
    shortest candidate->dest_u path — see via_is_natural_in_cost_matrix's
    2026-07-17 semantics change).

    FOUND 2026-07-10 (Gustav asked for a thorough review of the new
    sensor-anchored design; confirmed by isolating E-I/I-E generation on
    the real graph: cross_fraction=1.0 alone produced 1000/1000 dropped
    tour attempts — a complete, silent failure, not the "expected
    network-asymmetry dropout" this was first, wrongly, reported as):
    an E-I tour's return leg needs to reach an INDEPENDENTLY-drawn exit
    gate from the (fixed) activity edge — natural_far_end_weights handles
    that directly, since the fixed point IS the origin there. But an I-E
    tour's return leg needs to depart from an INDEPENDENTLY-drawn entry
    gate and reach the (fixed) home edge — the fixed point there is the
    DESTINATION, and natural_far_end_weights has no way to vary the
    ORIGIN instead. Needed as its own function (not just swapped
    arguments) because the distance-matrix indexing direction is
    different: D[origin, dest], never symmetric on a directed graph.

    candidate_v_idx must be each candidate edge's OWN "v" node (matrix
    index) — the node reached immediately after traversing that edge,
    i.e. the natural point to measure ONWARD travel from, mirroring how
    natural_far_end_weights' candidates are keyed by their "u" node (the
    point BEFORE entering that edge, natural for measuring travel TO)."""
    mu = node_idx[m_u]
    mv = node_idx[m_v]
    du = node_idx[dest_u]
    d_candidate_mu = D[candidate_v_idx, mu]
    d_mv_dest = D[mv, du]
    d_direct = D[candidate_v_idx, du]
    via_dist = d_candidate_mu + m_length + d_mv_dest
    finite = np.isfinite(d_direct) & np.isfinite(via_dist)
    tol = np.maximum(detour_abs_s,
                     detour_frac * np.where(finite, d_direct, 0.0))
    with np.errstate(invalid="ignore"):
        natural = finite & (via_dist - d_direct <= tol)
    if candidate_u_idx is not None:
        # Each candidate origin edge has already been traversed before its
        # v-node.  Reject a shortest leg that returns to that edge's u-node.
        natural &= ~shortest_paths_use_node(
            D, candidate_v_idx, candidate_u_idx, np.full(len(candidate_v_idx), mu)
        )
    if dest_v is not None:
        # The fixed destination edge must be the final edge, not a return
        # from its v-node back to its u-node.
        natural &= ~shortest_paths_use_node(
            D, np.full(len(candidate_v_idx), mv), node_idx[dest_v],
            np.full(len(candidate_v_idx), du)
        )
    return np.where(natural, base_weights, 0.0)


def verified_via_gate_pairs(
    G, m_edge: str, in_ids: list[str], out_ids: list[str],
    *, D: np.ndarray | None = None, node_idx: dict[int, int] | None = None,
    edge_costs: dict[str, float] | None = None,
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
    A sensor with no natural entry/exit pair returns an empty set. It is a
    real observability limitation, not permission to fabricate a forced
    detour: the normal quota machinery lets better-connected sensors absorb
    the supply, and the final coverage gate fails clearly if any measured
    edge is left without a candidate.

    ``D``/``node_idx`` are optionally supplied from the generator's single
    all-pairs solve. Reusing them is both exact and much faster than running
    a separate NetworkX shortest-path search for every gate pair."""
    u_m, v_m, _ = m_edge.split("_")
    u_m, v_m = int(u_m), int(v_m)
    if D is None or node_idx is None:
        D, node_idx = build_shortest_distance_matrix(G, edge_costs)
    via_cost = routing_cost_for_edge_id(G, m_edge, edge_costs)
    good = []
    for e_in in in_ids:
        v_in = int(e_in.split("_")[1])
        for e_out in out_ids:
            u_out = int(e_out.split("_")[0])
            if via_is_natural_in_cost_matrix(
                    D, node_idx, v_in, u_out, u_m, v_m, via_cost):
                good.append((e_in, e_out))
    return good


def daily_shape(is_weekend: bool = False) -> np.ndarray:
    """Our OWN measured departure-time distribution (normal_profile.json) —
    more local and finer-grained than RVU's regional bins, and consistent
    with them (both show the 7-8h / 16-17h peaks RVU reports on p.11).

    FIXED 2026-07-10: this unconditionally read the 'weekday' profile
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


# Use the actual traffic-weighted weekday mix, not the arithmetic mean of
# hourly shares. The latter silently changes the aggregate deterrence scale
# whenever rush-hour volume differs from off-peak volume.
_weekday_shape = daily_shape(False)
_weekday_mix_values = np.asarray(PURPOSE_HOURLY_WEEKDAY).T @ _weekday_shape
_WEEKDAY_AVG_MIX = dict(zip(PURPOSE_CATEGORIES, _weekday_mix_values))
_WEEKDAY_MEAN_SCALE = sum(_WEEKDAY_AVG_MIX[p] * _PURPOSE_SHRUNK[p]
                          for p in _PURPOSE_SHRUNK)
PURPOSE_LENGTH_SCALE = {p: s / _WEEKDAY_MEAN_SCALE
                        for p, s in _PURPOSE_SHRUNK.items()}


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
                            quota: dict[str, int], anchor_u: int | None = None,
                            candidate_v: int | None = None) -> str | None:
    """Which measured edge (if any) the anchor->candidate path naturally
    passes through — checked against ALL sensors (7 cheap O(1) lookups),
    preferring whichever is furthest behind its own quota if more than one
    fits. Used ONLY by I-I's return leg (generate_sensor_anchored_trips):
    both of I-I's endpoints are ordinary internal edges, so the return leg
    can reuse BOTH fixed edges from the outbound draw as-is — no new gate
    to sample, just a check of which sensor (if any) naturally connects
    them, which may be a DIFFERENT sensor than the outbound leg used, or
    none at all (a real, expected case on a directed, asymmetric network).
    E-I/I-E's return legs are NOT interchangeable with this: one of their
    two endpoints is a gate edge that can never legitimately be reused as
    a return endpoint (an entry gate's own start node has in-degree 0, an
    exit gate's own end node has out-degree 0), so those legs need a
    FRESH gate draw instead (natural_far_end_weights / natural_origin_weights
    directly, inlined in generate_sensor_anchored_trips) — this function
    would silently return None for them essentially always, which is
    exactly the bug found and fixed 2026-07-10 (E-I/I-E were completely
    non-functional before that fix, confirmed via isolated generation:
    1000/1000 dropped)."""
    candidate_idx = np.array([node_idx[candidate_u]])
    for m_edge in sorted(m_info, key=lambda m: -quota[m]):
        m_u, m_v, m_length = m_info[m_edge]
        w = natural_far_end_weights(D, node_idx, anchor_v, m_u, m_v, m_length,
                                    candidate_idx, np.array([1.0]),
                                    anchor_u=anchor_u,
                                    candidate_v_idx=(
                                        np.array([node_idx[candidate_v]])
                                        if candidate_v is not None else None))
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
    gravity_alpha: float = 0.0,
    routing_costs: dict[str, float] | None = None,
    cache_conditioned_fields: bool = True,
) -> tuple[list[tuple], list[float], dict[str, int]]:
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
    tries sensors in quota order (most-behind first), drawing a fresh
    anchor+far-end pair per retry (gravity decay depends on WHICH anchor
    gets drawn, so the deterrence weights are recomputed every attempt —
    see draw_conditioned_outbound). What the return leg needs depends on whether its
    OWN endpoints are ordinary internal edges (reachable/continuable from
    any direction) or gate edges (structurally one-directional — see the
    FOUND 2026-07-10 note at the top of the actual I-I/E-I/I-E code below
    for the real bug this fixed): I-I's return leg reuses BOTH fixed
    edges, just checking which sensor (if any) naturally connects them
    (_natural_sensor_for_leg); E-I/I-E's return leg reuses only ONE fixed
    edge and must draw a FRESH, independent gate for the other end,
    masked to the natural subset. If no sensor and no valid return-leg
    draw exists, the whole tour attempt is abandoned (not compensated
    with an extra attempt elsewhere) rather than emitting a non-compliant
    leg.

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
    D, node_idx = build_shortest_distance_matrix(G, routing_costs)

    edge_ids  = [e["id"] for e in edges]
    edge_u_nodes = [e["u"] for e in edges]
    edge_v_nodes = [e["v"] for e in edges]
    edge_u_idx = np.array([node_idx[u] for u in edge_u_nodes])
    edge_v_idx = np.array([node_idx[v] for v in edge_v_nodes])
    edge_lats  = np.array([e["lat"] for e in edges])
    edge_lons  = np.array([e["lon"] for e in edges])

    entry_u_nodes = [int(eid.split("_")[0]) for eid in entry_ids]
    entry_v_nodes = [int(eid.split("_")[1]) for eid in entry_ids]
    entry_u_idx = np.array([node_idx[u] for u in entry_u_nodes])
    entry_v_idx = np.array([node_idx[v] for v in entry_v_nodes])
    entry_lats, entry_lons = gate_latlon(G, entries)
    exit_u_nodes = [int(eid.split("_")[0]) for eid in exit_ids]
    exit_v_nodes = [int(eid.split("_")[1]) for eid in exit_ids]
    exit_u_idx = np.array([node_idx[u] for u in exit_u_nodes])
    exit_v_idx = np.array([node_idx[v] for v in exit_v_nodes])
    exit_lats, exit_lons = gate_latlon(G, exits)

    m_info: dict[str, tuple[int, int, float]] = {}
    good_pairs_ee: dict[str, list[tuple[str, str]]] = {}
    for m_edge in measured:
        u_s, v_s, k_s = m_edge.split("_")
        m_u, m_v = int(u_s), int(v_s)
        m_info[m_edge] = (
            m_u, m_v, routing_cost_for_edge_id(G, m_edge, routing_costs))
        in_ids, out_ids = upstream_downstream_gates(G, m_edge, entries, exits)
        good_pairs_ee[m_edge] = verified_via_gate_pairs(
            G, m_edge, in_ids, out_ids, D=D, node_idx=node_idx,
            edge_costs=routing_costs)

    # E-E through pairs stay UNIFORM over the verified pair list — a
    # deliberate, MEASURED decision (2026-07-17): weighting pairs by the
    # product of the two gates' structural loads was implemented, built and
    # measured, and it made both the worst-shape concentration (636 →
    # 1 465 veh/day on one route) and the structure drift (near-sensor
    # destinations 18.5 → 25.2%) WORSE.  The candidate pool is a SUPPORT
    # SET for the PFE, which reweights route flows freely — pool value lies
    # in covering distinct (entry, exit) pairs, not in matching their
    # frequencies, and uniform drawing maximises that coverage.  (Tour
    # ANCHOR draws are different: they feed rejection sampling, where draw
    # density limits which corridors exist in the pool at all — those DO
    # use the structural gate weights, see gate_weights.)

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
    for trip_no, h in enumerate(hours):
        for m_edge in by_quota():
            pairs = good_pairs_ee[m_edge]
            if not pairs:
                continue
            e_in, e_out = pairs[rng.integers(len(pairs))]
            trips.append(((h + rng.random()) * 3600, e_in, e_out, m_edge,
                          "through", f"ee-{trip_no}", "through"))
            quota[m_edge] -= 1
            break

    # ── I-I / E-I / I-E: paired tours, sensor-anchored per leg. ─────────
    #
    # FOUND 2026-07-10 (Gustav asked for a thorough review of the whole
    # new design; confirmed by isolating generation on the real graph —
    # cross_fraction=1.0/through_fraction=0.0 alone produced 1000/1000
    # dropped tour attempts, a SILENT, COMPLETE FAILURE, not the "expected
    # network-asymmetry dropout" this was first, wrongly, reported as): an
    # earlier version of this code reused the OUTBOUND anchor's own edge
    # id as the return leg's destination/origin for EVERY category,
    # including E-I and I-E. That's only valid for I-I, where BOTH ends
    # are ordinary internal edges, freely reachable/continuable from any
    # direction. E-I's anchor is an ENTRY gate — find_gates() defines
    # these as edges whose OWN START node has in-degree 0, meaning NOTHING
    # inside the graph can ever route back TO it; I-E's far end is an
    # EXIT gate — its OWN END node has out-degree 0, meaning nothing can
    # ever continue FROM it. Asking _natural_sensor_for_leg to check a
    # path TO an entry gate or FROM an exit gate is asking for something
    # structurally impossible, so it silently returned None every time —
    # E-I/I-E were completely non-functional despite the target quota
    # printed for them.
    #
    # FIX: E-I's return leg keeps the (fixed) activity edge as its origin
    # but draws a FRESH, independent exit gate as its destination — masked
    # to the natural subset via natural_far_end_weights (same fixed-origin
    # shape it was built for). I-E's return leg keeps the (fixed) home
    # edge as its destination but draws a FRESH, independent entry gate as
    # its origin — needs natural_origin_weights instead (the mirror image:
    # fixed DESTINATION, masked ORIGIN pool), since natural_far_end_weights
    # has no way to vary the origin side. Matches the ORIGINAL (pre-
    # sensor-anchoring) E-I/I-E code's own return-leg semantics (an
    # independently-drawn gate, not the same one reused) — this design
    # only adds the sensor-naturalness mask on top of that existing shape,
    # it doesn't change which edge plays which role.
    def draw_purpose_weights(h_out: int) -> tuple[str, np.ndarray]:
        purpose_shares = purpose_shares_for_hour(int(h_out), is_weekend)
        purpose = rng.choice(PURPOSE_CATEGORIES, p=list(purpose_shares.values()))
        return purpose, amass[purpose]

    home_anchor_p = hmass / hmass.sum()
    entry_anchor_p = w_entry / w_entry.sum()

    # ── Conditional joint sampling (REDESIGNED 2026-07-12; see the
    # demand/destination integrity section of IMPROVEMENT_PLAN.md and
    # natural_sensor_masks' docstring). Each tour's outbound leg now:
    #   1. draws its anchor from the UNCONDITIONED anchor field,
    #   2. computes city-wide destination weights (activity mass ×
    #      deterrence kernel) over the whole pool,
    #   3. masks them by the UNION of all sensors' naturalness, and
    #   4. ACCEPTS the anchor with probability (sensor-passing mass /
    #      total mass) — rejection sampling that yields exactly
    #      P(anchor, dest | natural route passes ≥1 sensor).
    # The PREVIOUS design pre-committed each draw to ONE sensor (quota
    # order) and renormalized within that sensor's admitted set — so an
    # anchor whose only admissible destinations sat right next to the
    # sensor emitted exactly those with probability 1. Measured: ~10-12%
    # of generated destinations within 200 m of a sensor (all-edges
    # baseline 1.8%), nearly FLAT across the entire deterrence-kernel
    # (α, β) grid, and a within-anchor importance division (Frejinger-
    # style q-correction) only moved it 11.1%→10.4% — per-anchor
    # renormalization itself was the bias, so the fix is to stop
    # renormalizing and condition the joint instead.
    # The sensor a trip is ATTRIBUTED to (its via= edge + quota
    # bookkeeping) is chosen among the sensors its route naturally
    # passes — most-behind quota first, the same balancing spirit as
    # before — AFTER the draw, so attribution can no longer distort the
    # OD itself. E-E through trips keep the per-sensor quota mechanism
    # unchanged: they are the guaranteed-coverage backbone, and their
    # gate endpoints sit on the canvas boundary where the near-sensor
    # mechanism cannot apply.
    n_accept_tries = max_anchor_redraws * 8   # rejection tries are cheap and
                                              # MUST be plentiful: a low
                                              # P(pass any sensor) is handled
                                              # by retrying, not by biasing

    def attribute_sensor(masks_or_membership, pos: int) -> str:
        if isinstance(masks_or_membership, dict):
            passed = [m for m in measured if masks_or_membership[m][pos]]
        else:
            passed = sensors_at_membership(masks_or_membership, measured, pos)
        return max(passed, key=lambda m: quota[m])

    # PER-TRY CACHES (2026-07-16, result-neutral speedup): profiling the
    # 305 s generation stage showed 96% of it inside the rejection loop —
    # 265 576 tries recomputing natural_sensor_masks (235 s) and the
    # deterrence field (19 s) for anchors drawn from a FIXED weighted pool
    # (~40 repeats per anchor on average; the H0 reuse review predicted
    # this exact cache as "the lever if it grows"). Every cached value is
    # a DETERMINISTIC function of its key, and the cache adds/removes NO
    # rng calls — the random stream, and therefore every drawn trip, is
    # byte-identical (proven: same-seed tours.trips.xml md5-equal
    # before/after). Caches are created fresh per category call site so
    # pool identity can never be confused. Each entry stores a packed sensor
    # membership field and union, not seven full bool arrays, and is bounded
    # by ConditionedMaskCache's LRU memory budget.
    def draw_conditioned_outbound(anchor_p, anchor_u_nodes, anchor_v_nodes,
                                  anchor_lats,
                                  anchor_lons, dest_lats, dest_lons,
                                  dest_u_idx_a, dest_v_idx_a, base_w, beta_km,
                                  mask_cache, scalar_cache):
        """ONE rejection-sampling try of the conditioned outbound leg — the
        statistical core of the destination-bias fix, shared by the I-I,
        E-I and I-E loops so the three tour categories can never drift onto
        different joint distributions. Returns (a_pos, f_pos, d_km_f,
        masks) on acceptance, None to retry (rejection IS the
        conditioning: P(anchor, dest | natural route passes ≥1 sensor))."""
        a_pos = int(rng.choice(len(anchor_v_nodes), p=anchor_p))
        anchor_v = anchor_v_nodes[a_pos]
        if anchor_v not in node_idx:
            return None

        cached = mask_cache.get(a_pos) if cache_conditioned_fields else None
        if cached is None:
            masks = natural_sensor_masks(
                D, node_idx, anchor_v, m_info, dest_u_idx_a,
                anchor_u=anchor_u_nodes[a_pos], dest_v_idx=dest_v_idx_a)
            membership, union = pack_sensor_membership(masks, measured)
            if cache_conditioned_fields:
                mask_cache.put(a_pos, membership, union)
        else:
            membership, union = cached

        # The ACCEPT test needs only two scalars, both fully determined by
        # (anchor, beta, base_w) — the full destination field is rebuilt
        # only for the ~2% of tries that are accepted. id(base_w) is in the
        # key because base_w is the per-purpose activity-mass array
        # (draw_purpose_weights returns the same object per purpose):
        # keying on beta alone would silently corrupt the cache if two
        # purposes ever shared a length scale.
        key = (a_pos, beta_km, id(base_w))
        scalars = scalar_cache.get(key) if cache_conditioned_fields else None
        if scalars is None:
            d_km = gravity_distance_km(dest_lats, dest_lons,
                                       anchor_lats[a_pos], anchor_lons[a_pos])
            far_w = base_w * deterrence_weights(d_km, beta_km, gravity_alpha)
            total_w = far_w.sum()
            masked = np.where(union, far_w, 0.0)
            z = masked.sum()
            if cache_conditioned_fields:
                scalar_cache[key] = (total_w, z)
        else:
            total_w, z = scalars
            d_km = masked = None

        if total_w <= 0:
            return None
        if z <= 0 or rng.random() >= z / total_w:
            return None
        if masked is None:   # accepted on a cache hit — build the field now
            d_km = gravity_distance_km(dest_lats, dest_lons,
                                       anchor_lats[a_pos], anchor_lons[a_pos])
            far_w = base_w * deterrence_weights(d_km, beta_km, gravity_alpha)
            masked = np.where(union, far_w, 0.0)
        f_pos = int(rng.choice(len(dest_u_idx_a), p=masked / z))
        return a_pos, f_pos, float(d_km[f_pos]), membership

    # I-I: home -> activity -> home. Both ends are ordinary internal
    # edges, so the return leg simply reuses both fixed edges — only
    # needs to verify SOME sensor naturally connects them (no new draw).
    ii_masks, ii_scalars = ConditionedMaskCache(), {}
    for tour_no in range(n_internal):
        h_out, h_ret = am_pm_hours()
        purpose, far_base = draw_purpose_weights(h_out)
        succeeded = False
        for _ in range(n_accept_tries):
            drawn = draw_conditioned_outbound(
                home_anchor_p, edge_u_nodes, edge_v_nodes, edge_lats, edge_lons,
                edge_lats, edge_lons, edge_u_idx, edge_v_idx, far_base,
                gravity_km * PURPOSE_LENGTH_SCALE.get(purpose, 1.0),
                ii_masks, ii_scalars)
            if drawn is None:
                continue   # anchor rejected — this IS the conditioning
            a_pos, f_pos, d_km_f, membership = drawn
            return_sensor = _natural_sensor_for_leg(
                D, node_idx, edge_v_nodes[f_pos], edge_u_nodes[a_pos],
                m_info, quota, anchor_u=edge_u_nodes[f_pos],
                candidate_v=edge_v_nodes[a_pos])
            if return_sensor is None:
                continue
            m_edge = attribute_sensor(membership, f_pos)
            quota[m_edge] -= 1
            quota[return_sensor] -= 1
            tour_lengths_km.append(d_km_f)
            trips.append(((h_out + rng.random()) * 3600,
                          edge_ids[a_pos], edge_ids[f_pos], m_edge,
                          purpose, f"ii-{tour_no}", "outbound"))
            trips.append(((h_ret + rng.random()) * 3600,
                          edge_ids[f_pos], edge_ids[a_pos], return_sensor,
                          purpose, f"ii-{tour_no}", "return"))
            succeeded = True
            break
        if not succeeded:
            n_dropped_no_sensor += 1

    # E-I: entry gate -> activity, return leg to an INDEPENDENTLY-drawn
    # exit gate (never back through the entry gate — see note above).
    ei_masks, ei_scalars = ConditionedMaskCache(), {}
    for tour_no in range(n_ei):
        h_out, h_ret = am_pm_hours()
        purpose, far_base = draw_purpose_weights(h_out)
        succeeded = False
        for _ in range(n_accept_tries):
            drawn = draw_conditioned_outbound(
                entry_anchor_p, entry_u_nodes, entry_v_nodes, entry_lats,
                entry_lons, edge_lats, edge_lons, edge_u_idx, edge_v_idx, far_base,
                gravity_km * PURPOSE_LENGTH_SCALE.get(purpose, 1.0),
                ei_masks, ei_scalars)
            if drawn is None:
                continue
            a_pos, f_pos, d_km_f, membership = drawn
            # Return leg: fixed activity origin -> fresh exit gate, union
            # over sensors (no per-sensor pre-commitment here either).
            ret_masks = natural_sensor_masks(
                D, node_idx, edge_v_nodes[f_pos], m_info, exit_u_idx,
                anchor_u=edge_u_nodes[f_pos], dest_v_idx=exit_v_idx)
            ret_w = np.where(np.logical_or.reduce([ret_masks[m] for m in measured]),
                             w_exit, 0.0)
            rz = ret_w.sum()
            if rz <= 0:
                continue   # no sensor-passing way out from this activity — retry
            g_pos = int(rng.choice(len(exit_u_idx), p=ret_w / rz))
            m_edge = attribute_sensor(membership, f_pos)
            return_m_edge = attribute_sensor(ret_masks, g_pos)
            quota[m_edge] -= 1
            quota[return_m_edge] -= 1
            tour_lengths_km.append(d_km_f)
            trips.append(((h_out + rng.random()) * 3600,
                          entry_ids[a_pos], edge_ids[f_pos], m_edge,
                          purpose, f"ei-{tour_no}", "inbound"))
            trips.append(((h_ret + rng.random()) * 3600,
                          edge_ids[f_pos], exit_ids[g_pos], return_m_edge,
                          purpose, f"ei-{tour_no}", "outbound"))
            succeeded = True
            break
        if not succeeded:
            n_dropped_no_sensor += 1

    # I-E: internal home -> exit gate, return leg FROM an INDEPENDENTLY-
    # drawn entry gate (never from the same exit gate — see note above).
    ie_masks, ie_scalars = ConditionedMaskCache(), {}
    for tour_no in range(n_ie):
        h_out, h_ret = am_pm_hours()
        succeeded = False
        for _ in range(n_accept_tries):
            drawn = draw_conditioned_outbound(
                home_anchor_p, edge_u_nodes, edge_v_nodes, edge_lats, edge_lons,
                exit_lats, exit_lons, exit_u_idx, exit_v_idx, w_exit,
                gravity_km * PURPOSE_LENGTH_SCALE.get("external", 1.0),
                ie_masks, ie_scalars)
            if drawn is None:
                continue
            a_pos, f_pos, d_km_f, membership = drawn
            # Return leg: fresh entry gate -> fixed home destination —
            # union of the per-sensor natural_origin_weights maskings
            # (elementwise max is the union: same base weights, 0/w each).
            ret_w_by_m = {m: natural_origin_weights(
                D, node_idx, entry_v_idx, *m_info[m],
                edge_u_nodes[a_pos], w_entry,
                candidate_u_idx=entry_u_idx,
                dest_v=edge_v_nodes[a_pos]) for m in measured}
            ret_w = np.maximum.reduce(list(ret_w_by_m.values()))
            rz = ret_w.sum()
            if rz <= 0:
                continue
            g_pos = int(rng.choice(len(entry_v_idx), p=ret_w / rz))
            # Positive weight == the natural route passes that sensor, so
            # the weight dict doubles as attribute_sensor's masks.
            return_m_edge = attribute_sensor(ret_w_by_m, g_pos)
            m_edge = attribute_sensor(membership, f_pos)
            quota[m_edge] -= 1
            quota[return_m_edge] -= 1
            tour_lengths_km.append(d_km_f)
            trips.append(((h_out + rng.random()) * 3600,
                          edge_ids[a_pos], exit_ids[f_pos], m_edge,
                          "external", f"ie-{tour_no}", "outbound"))
            trips.append(((h_ret + rng.random()) * 3600,
                          entry_ids[g_pos], edge_ids[a_pos], return_m_edge,
                          "external", f"ie-{tour_no}", "inbound"))
            succeeded = True
            break
        if not succeeded:
            n_dropped_no_sensor += 1

    print(f"sensor-anchored generation: dropped {n_dropped_no_sensor} tour "
          f"attempts where no sensor naturally fit either leg "
          f"(of {n_internal + n_ei + n_ie} attempted)")
    achieved = {m: initial_quota[m] - quota[m] for m in measured}
    short = {m: n for m, n in achieved.items() if n < min_per_sensor}
    if short:
        print(f"  WARNING: below --min-per-sensor ({min_per_sensor}): {short} "
              f"-- these sensors are structurally poor fits for the current "
              f"trip mix; consider a higher --n-total or investigate why")
    return trips, tour_lengths_km, short


@dataclass
class CandidateStructure:
    """The expensive, date-invariant inputs shared by day blocks."""
    G: object
    edges: list[dict]
    hmass: np.ndarray
    amass: dict[str, np.ndarray]
    entries: list[tuple[str, int]]
    exits: list[tuple[str, int]]
    entry_ids: list[str]
    exit_ids: list[str]
    w_entry: np.ndarray
    w_exit: np.ndarray
    measured: list[str]
    routing_costs: dict[str, float] | None = None


def build_location_pool_document(home: EndpointField,
                                 activities: dict[str, EndpointField]) -> dict[str, list[dict]]:
    """Compact shared endpoint-location pools for PFE provenance.

    Pools live once at the top of ``candidates.meta.json`` instead of being
    copied into every candidate.  PFE samples from the selected candidate's
    pool for each repeated vehicle, so a calibrated route can represent
    several anonymous homes/POIs on its access edge rather than one frozen
    road-start coordinate.
    """
    pools: dict[str, list[dict]] = {}
    for edge_id, locations in home.pools_by_edge.items():
        pools[f"home:{edge_id}"] = locations
    for category, field in activities.items():
        for edge_id, locations in field.pools_by_edge.items():
            pools[f"{category}:{edge_id}"] = locations
    return pools


def _tour_kind(tour_id: str) -> str:
    """Extract the stable E-E/I-I/E-I/I-E kind after multi-day ID prefixing."""
    for kind in ("ee", "ii", "ei", "ie"):
        if f"{kind}-" in str(tour_id):
            return kind
    return ""


def endpoint_pool_keys(tour_id: str, leg: str, purpose: str,
                       origin_edge: str, destination_edge: str,
                       pools: dict[str, list[dict]]) -> tuple[str | None, str | None]:
    """Return the physical endpoint pools implied by one paired-tour leg.

    Gates deliberately have no synthetic address: their endpoint is the
    simulation boundary.  Internal home/activity endpoints do, and the same
    tour uses the same pool class in both directions.  The exact individual
    is intentionally re-sampled by PFE for each emitted vehicle.
    """
    kind = _tour_kind(tour_id)
    origin_key = destination_key = None
    if kind == "ii":
        if leg == "outbound":
            origin_key, destination_key = f"home:{origin_edge}", f"{purpose}:{destination_edge}"
        elif leg == "return":
            origin_key, destination_key = f"{purpose}:{origin_edge}", f"home:{destination_edge}"
    elif kind == "ei":
        if leg == "inbound":
            destination_key = f"{purpose}:{destination_edge}"
        elif leg == "outbound":
            origin_key = f"{purpose}:{origin_edge}"
    elif kind == "ie":
        if leg == "outbound":
            origin_key = f"home:{origin_edge}"
        elif leg == "inbound":
            destination_key = f"home:{destination_edge}"
    return (origin_key if origin_key in pools else None,
            destination_key if destination_key in pools else None)


def generate_day_block(
    structure: CandidateStructure, profile: np.ndarray, offset_s: float,
    id_prefix: str, seed: int, day_index: int, n_total: int,
    through_fraction: float, cross_fraction: float, gravity_km: float,
    is_weekend: bool, min_per_sensor: int, template_trips: list[tuple] | None = None,
    gravity_alpha: float = 0.0,
) -> tuple[list[tuple], list[float], dict[str, int], list[tuple]]:
    """Generate one calendar-day candidate block.

    A first block for a day type creates the route geometry. Later blocks of
    the same type reuse that geometry but draw fresh departures from their own
    exact-day profile. This keeps the PFE shape pool bounded while preserving
    every day's measured/forecast departure-time signal.
    """
    rng = np.random.default_rng(seed + day_index)
    if template_trips is None:
        raw_trips, lengths, short = generate_sensor_anchored_trips(
            rng, structure.G, structure.edges, structure.hmass, structure.amass,
            structure.entries, structure.exits, structure.entry_ids, structure.exit_ids,
            structure.w_entry, structure.w_exit, profile, structure.measured,
            n_total, through_fraction, cross_fraction, gravity_km, is_weekend,
            min_per_sensor, gravity_alpha=gravity_alpha,
            routing_costs=structure.routing_costs)
        template_trips = [
            (t[1], t[2], t[3],
             t[4] if len(t) > 4 else "unknown",
             t[5] if len(t) > 5 else f"legacy-{i}",
             t[6] if len(t) > 6 else "unknown")
            for i, t in enumerate(raw_trips)
        ]
    else:
        # Reusing geometry is deliberate: diversity, not a linearly growing
        # number of near-duplicate routes, is what the calibration needs.
        raw_trips = []
        lengths = []
        short = {}

    # Geometry reuse must not sever a route's purpose from its departure
    # period. Draw h conditional on the template's already-sampled purpose:
    # P(h | p, day) ∝ P(h | day) P(p | h, day). Critically, resample the
    # TWO legs of a paired tour together. The old per-route loop could place
    # its return before its own outbound leg on a reused multi-day template,
    # destroying the AM/PM directional structure that the original generator
    # deliberately created. Reuse still avoids any extra routing work.
    def departure_weights(purpose: str, *, pm_only: bool = False) -> np.ndarray:
        weights = np.asarray(profile, dtype=float).copy()
        if purpose in PURPOSE_CATEGORIES:
            weights *= np.array([
                purpose_shares_for_hour(hour, is_weekend)[purpose]
                for hour in range(24)
            ])
        if pm_only:
            weights *= np.arange(24) >= 12
        if weights.sum() <= 0:
            weights = np.asarray(profile, dtype=float).copy()
            if pm_only:
                weights *= np.arange(24) >= 12
        return weights / weights.sum() if weights.sum() > 0 else np.full(24, 1 / 24)

    def is_return_leg(tour_id: str, leg: str) -> bool:
        kind = _tour_kind(tour_id)
        return ((kind == "ii" and leg == "return")
                or (kind == "ei" and leg == "outbound")
                or (kind == "ie" and leg == "inbound"))

    hours = np.empty(len(template_trips), dtype=int)
    by_tour: dict[str, list[int]] = {}
    for i, template in enumerate(template_trips):
        by_tour.setdefault(str(template[4]), []).append(i)
    for tour_id, indices in by_tour.items():
        purpose = str(template_trips[indices[0]][3])
        return_indices = [i for i in indices if is_return_leg(
            str(template_trips[i][4]), str(template_trips[i][5]))]
        outbound_indices = [i for i in indices if i not in return_indices]
        if not return_indices or not outbound_indices:
            # E-E through trips have only one leg. A malformed legacy
            # template is handled the same safe way rather than guessing a
            # synthetic return relationship.
            for i in indices:
                hours[i] = int(rng.choice(24, p=departure_weights(
                    str(template_trips[i][3]))))
            continue
        h_out = int(rng.choice(24, p=departure_weights(purpose)))
        pm_weights = departure_weights(purpose, pm_only=True)
        h_ret = max(h_out + 1, int(rng.choice(24, p=pm_weights)))
        h_ret = min(h_ret, 23)
        for i in outbound_indices:
            hours[i] = h_out
        for i in return_indices:
            hours[i] = h_ret
    block = [
        (f"{id_prefix}{i}", offset_s + (hour + rng.random()) * 3600,
         from_edge, to_edge, via, purpose, f"{id_prefix}{tour_id}", leg)
        for i, (template, hour)
        in enumerate(zip(template_trips, hours))
        for from_edge, to_edge, via, purpose, tour_id, leg in [template[:6]]
    ]
    return block, lengths, short, template_trips


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--through-fraction", type=float, default=0.5,
                    help="θ: share of the candidate POOL that is E-E "
                        "through traffic. INVESTIGATED 2026-07-13 (Gustav "
                        "challenged the 0.5 neutral prior; research + a "
                        "3-point empirical sweep followed). Measured "
                        "area-wide through-shares exist: Stockholm inner "
                        "city (Trafikverket 2017:123, ANPR at every "
                        "betalstation, Oct 2015) — 28 500 genomfartsresor/"
                        "day vs 331 500 cordon passages = ~9.4%% of "
                        "boundary-touching trips, at the same ~35 km² "
                        "scale as this canvas (bbox ~37.5 km²); small "
                        "German cores via Kennzeichenerfassung: Eisenach "
                        "25%%, Feuchtwangen Altstadt 18%%. BUT setting the "
                        "pool to those shares (0.15, and midpoint 0.30/"
                        "0.65) degraded EVERY structural gate "
                        "monotonically on full same-day builds — "
                        "near-sensor destinations 7.5→10.4→11.9%%, RVU "
                        "length L1 0.40→0.57→0.61, onward-after-last-"
                        "sensor median 2902→2738→2401 m, purpose-length "
                        "ordering flag firing — while GEH stayed 100%% "
                        "throughout. Two reasons, both real: (1) this "
                        "pool is CONDITIONED on crossing a sensor on a "
                        "major arterial, where through traffic "
                        "legitimately concentrates — the unconditional "
                        "area-wide share is a different quantity and NOT "
                        "this parameter's target (a category error to "
                        "equate them); (2) the pool doubles as the "
                        "calibration's route-shape SUPPLY — E-E candidates "
                        "are the long routes, and starving them forces "
                        "PFE onto short/near-sensor shapes, the original "
                        "destination-bias failure class. 0.5 therefore "
                        "stands as the supply-tuned value; the sourced "
                        "area-wide composition is reporting context, and "
                        "agent_demand.purpose_counts in demand_meta.json "
                        "discloses each build's calibrated through share.")
    ap.add_argument("--gravity-km", type=float, default=1.8,
                    help="θ: distance-deterrence scale β (km) for tours")
    ap.add_argument("--gravity-alpha", type=float, default=1.5,
                    help="θ: Tanner/gamma deterrence shape α — weight is "
                        "(d/β)^α·exp(−d/β), so 0 is the previous pure "
                        "negative exponential and α>0 puts the kernel's "
                        "mode at α·β km instead of at d=0. Added 2026-07-12 "
                        "after measuring that the monotone-decreasing "
                        "exponential, combined with the sensor-anchoring "
                        "mask (which only admits destinations path-wise "
                        "BEYOND the sensor), made 'the edge immediately "
                        "past the sensor' the modal destination — 36.5%% of "
                        "simulated vehicles ended within 200 m of a sensor "
                        "vs a 2.1%% random-edge baseline. See "
                        "IMPROVEMENT_PLAN.md and "
                        "deterrence_weights().")
    ap.add_argument("--fit-only", action="store_true",
                    help="Stop after generating trips and writing "
                        "trip_length_fit<suffix>.json — skips duarouter "
                        "routing entirely. For kernel/θ calibration sweeps "
                        "(tools/fit_deterrence_kernel.py), where only the "
                        "fit metrics are needed per parameter combination.")
    ap.add_argument("--cross-fraction", type=float, default=0.3,
                    help="θ: share of the (non-through) tour budget that is "
                        "E-I/I-E cross-boundary commuting (one end at a "
                        "gate, one end an internal home/activity edge) "
                        "rather than pure I-I (both ends internal). Same "
                        "2026-07-13 investigation as --through-fraction "
                        "(see there): Stockholm ANPR says boundary-"
                        "crossing dominates through ~10:1 AREA-WIDE, but "
                        "the pool is sensor-conditioned supply, and the "
                        "0.15/0.70 and 0.30/0.65 re-mixes degraded every "
                        "structural gate; 0.3 stands as the supply-tuned "
                        "value with the area-wide composition kept as "
                        "reporting context. Added "
                        "2026-07-08: E-I/I-E were documented in this "
                        "docstring's METHOD section from the start but "
                        "never actually implemented — every 'tour' was "
                        "silently I-I only (both ends drawn from `edges`, "
                        "never a gate), which structurally caps tour "
                        "length at the small inner-city canvas's own "
                        "diameter and cannot reach RVU's 5.1-10km trip "
                        "share (confirmed: best fit across gravity_km "
                        "1-15km still under 5%%, vs RVU's 32%%).")
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
                        "maintain. Added 2026-07-10.")
    ap.add_argument("--day-blocks-file", default=None,
                    help="JSON multi-day blocks from build_sumo_demand.py; "
                         "each gives its own profile, offset and ID prefix. "
                         "Omit it to retain the one-day CLI unchanged.")
    ap.add_argument("--n-total", type=int, default=12000)
    ap.add_argument("--assignment-priors", default=str(
        SUMO_DIR / "assignment_priors.json"),
        help="Gravity/Dial assignment-prior artifact; its per-edge "
             "structural load weights the gate draws so candidate density "
             "follows expected approach flow (road-class fallback when "
             "absent)")
    ap.add_argument("--min-per-sensor", type=int, default=50,
                    help="safety-net floor, not a target — every trip is "
                        "now sensor-anchored (generate_sensor_anchored_trips), "
                        "so n_total is split into an equal starting quota "
                        "per sensor; structurally poor-fit sensors correctly "
                        "end up under quota and well-connected ones absorb "
                        "the overflow (measured redraw-success rate on the "
                        "real graph varies 1.3%%-100%% by sensor). This floor "
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
    try:
        sumo_edge_ids, routing_costs = load_sumo_routing_data(NET_PATH)
    except (FileNotFoundError, ET.ParseError, ValueError) as exc:
        sys.exit(str(exc))
    with open("web/data/flows.json") as f:
        measured = list(json.load(f)["flows"])
    missing_sensor_edges = sorted(set(measured) - sumo_edge_ids)
    if missing_sensor_edges:
        sys.exit("measured sensor edges missing from SUMO net: "
                 + ", ".join(missing_sensor_edges))

    routing_G = routable_graph(G, sumo_edge_ids)
    edges = load_graph_edges(routing_G)
    print(f"{len(edges)} SUMO-routable edges")
    graph_edge_ids = {edge["id"] for edge in edges}
    missing_graph_sensor_edges = sorted(set(measured) - graph_edge_ids)
    if missing_graph_sensor_edges:
        sys.exit("measured sensor edges missing from GraphML/SUMO graph: "
                 + ", ".join(missing_graph_sensor_edges))

    # Endpoints are now rooted in anonymous building/POI access locations.
    # A real home near a sensor remains eligible; only the counter link itself
    # is excluded as an OD endpoint.  Spatial concentration is handled by the
    # distribution-preservation guard in demand/structure.py, not by erasing
    # a legitimate local neighbourhood with an arbitrary distance buffer.
    home_field = home_location_field(routing_G, edges, measured)
    activity_fields = activity_location_fields(routing_G, edges, measured)
    hmass = home_field.mass
    amass = {category: field.mass for category, field in activity_fields.items()}
    n_sensor_mass = exclude_sensor_endpoint_mass(edges, hmass, amass, measured)
    print(f"  excluded {n_sensor_mass} literal sensor edges from synthetic "
          "origin/destination mass")
    entries, exits = find_gates(routing_G)
    # Gate endpoints are synthetic OD endpoints too.  They normally sit at
    # the study-area boundary, but applying the same invariant makes a future
    # sensor close to an approach road safe without a special-case code path.
    entries = [(edge_id, node_id) for edge_id, node_id in entries
               if edge_id not in measured]
    exits = [(edge_id, node_id) for edge_id, node_id in exits
             if edge_id not in measured]
    entry_ids = [e for e, _ in entries]
    exit_ids  = [e for e, _ in exits]
    if not entry_ids or not exit_ids:
        sys.exit("literal sensor-edge exclusion removed every entry or exit gate; "
                 "expand the simulation boundary or review the sensor registry")
    gate_loads = load_assignment_gate_loads(Path(args.assignment_priors))
    w_entry = gate_weights(routing_G, entries, gate_loads)
    w_exit  = gate_weights(routing_G, exits, gate_loads)
    if gate_loads:
        covered = sum(1 for e in entry_ids + exit_ids if gate_loads.get(e))
        print(f"  gate weighting: structural assignment load "
              f"({covered}/{len(entry_ids) + len(exit_ids)} gates covered)")
    else:
        print("  gate weighting: road-class prior (no assignment field)")
    shape_hourly = daily_shape(args.is_weekend)
    if args.real_day_shape_file:
        with open(args.real_day_shape_file) as f:
            real_shape = np.array(json.load(f))
        shape_hourly = blend_day_shape(real_shape, shape_hourly)
    print(f"{len(entries)} entry gates, {len(exits)} exit gates")

    if hmass.sum() == 0:
        sys.exit("home_mass is all-zero — check data_in/deso/ (run fetch_deso.py)")
    empty_activity = sorted(name for name, mass in amass.items() if mass.sum() <= 0)
    if empty_activity:
        sys.exit("POI access mapping produced no activity mass for: "
                 + ", ".join(empty_activity))
    multi_day_trips = None
    n_day_blocks = 1
    if args.day_blocks_file:
        with open(args.day_blocks_file) as f:
            blocks = json.load(f)
        if not blocks:
            sys.exit("--day-blocks-file must contain at least one block")
        n_day_blocks = len(blocks)
        structure = CandidateStructure(
            G=routing_G, edges=edges, hmass=hmass, amass=amass, entries=entries,
            exits=exits, entry_ids=entry_ids, exit_ids=exit_ids,
            w_entry=w_entry, w_exit=w_exit, measured=measured,
            routing_costs=routing_costs)
        templates: dict[str, list[tuple]] = {}
        multi_day_trips, tour_lengths_km, short_quota = [], [], {}
        for day_index, block_spec in enumerate(blocks):
            profile = np.asarray(block_spec["profile"], dtype=float)
            if profile.shape != (24,) or profile.sum() <= 0:
                sys.exit(f"invalid 24-hour profile in day block {day_index}")
            profile /= profile.sum()
            pool_key = str(block_spec.get(
                "pool_key", "weekend" if block_spec.get("is_weekend") else "weekday"))
            reused = pool_key in templates
            block, lengths, short, template = generate_day_block(
                structure, profile, float(block_spec["offset_s"]),
                str(block_spec["id_prefix"]), args.seed, day_index, args.n_total,
                args.through_fraction, args.cross_fraction, args.gravity_km,
                bool(block_spec.get("is_weekend", False)), args.min_per_sensor,
                templates.get(pool_key), gravity_alpha=args.gravity_alpha)
            templates.setdefault(pool_key, template)
            multi_day_trips.extend(block)
            tour_lengths_km.extend(lengths)
            short_quota.update(short)
            print(f"  day block {day_index}: {len(block)} trips, {pool_key} pool "
                  f"({'reused geometry' if reused else 'new geometry'})")
        trips = []
    else:
        trips, tour_lengths_km, short_quota = generate_sensor_anchored_trips(
            rng, routing_G, edges, hmass, amass, entries, exits, entry_ids, exit_ids,
            w_entry, w_exit, shape_hourly, measured,
            args.n_total, args.through_fraction, args.cross_fraction,
            args.gravity_km, args.is_weekend, args.min_per_sensor,
            gravity_alpha=args.gravity_alpha, routing_costs=routing_costs)

    n_through     = int(args.n_total * args.through_fraction)
    n_tours_total = (args.n_total - n_through) // 2
    n_cross    = int(n_tours_total * args.cross_fraction)
    n_internal = n_tours_total - n_cross
    n_ei = n_cross // 2
    n_ie = n_cross - n_ei
    target_per_day = n_through + 2 * n_tours_total
    target_candidate_count = target_per_day * n_day_blocks

    trips.sort(key=lambda t: t[0])
    if multi_day_trips is not None:
        multi_day_trips.sort(key=lambda t: t[1])
    trips_path = SUMO_DIR / f"tours{args.out_suffix}.trips.xml"
    candidate_meta: dict[str, dict] = {}
    location_pools = build_location_pool_document(home_field, activity_fields)
    location_report = {
        "schema_version": 1,
        "home": home_field.report,
        "activities": {category: field.report
                       for category, field in activity_fields.items()},
        "location_pools": len(location_pools),
    }
    with open(SUMO_DIR / f"endpoint_location_report{args.out_suffix}.json", "w") as f:
        json.dump(location_report, f, indent=1, sort_keys=True)
    with open(trips_path, "w") as f:
        f.write("<routes>\n")
        if multi_day_trips is None:
            for i, t in enumerate(trips):
                trip_id = f"t{i}"
                via = f' via="{t[3]}"' if t[3] else ""
                f.write(f'  <trip id="{trip_id}" depart="{t[0]:.1f}" '
                        f'from="{t[1]}" to="{t[2]}"{via}/>\n')
                origin_pool, destination_pool = endpoint_pool_keys(
                    t[5], t[6], t[4], t[1], t[2], location_pools)
                record = {
                    "purpose": t[4], "tour_id": t[5], "leg": t[6],
                    "origin_edge": t[1], "destination_edge": t[2],
                    "via_edge": t[3] or None, "candidate_depart_s": round(t[0], 3),
                }
                if origin_pool is not None:
                    record["origin_location_pool"] = origin_pool
                if destination_pool is not None:
                    record["destination_location_pool"] = destination_pool
                candidate_meta[trip_id] = record
        else:
            for (trip_id, depart, from_edge, to_edge, via_edge,
                 purpose, tour_id, leg) in multi_day_trips:
                via = f' via="{via_edge}"' if via_edge else ""
                f.write(f'  <trip id="{trip_id}" depart="{depart:.1f}" '
                        f'from="{from_edge}" to="{to_edge}"{via}/>\n')
                origin_pool, destination_pool = endpoint_pool_keys(
                    tour_id, leg, purpose, from_edge, to_edge, location_pools)
                record = {
                    "purpose": purpose, "tour_id": tour_id, "leg": leg,
                    "origin_edge": from_edge, "destination_edge": to_edge,
                    "via_edge": via_edge or None, "candidate_depart_s": round(depart, 3),
                }
                if origin_pool is not None:
                    record["origin_location_pool"] = origin_pool
                if destination_pool is not None:
                    record["destination_location_pool"] = destination_pool
                candidate_meta[trip_id] = record
        f.write("</routes>\n")
    n_written = len(multi_day_trips) if multi_day_trips is not None else len(trips)
    print(f"{n_written} trips written, all sensor-anchored — target mix "
          f"was {n_through} E-E through + {n_internal} I-I / {n_ei} E-I / "
          f"{n_ie} I-E paired tours (actual counts vary since some tour "
          f"attempts are dropped when no sensor naturally fits either leg)")
    if target_candidate_count:
        generated_fraction = n_written / target_candidate_count
        print(f"  generated candidate supply: {generated_fraction:.1%} "
              f"({n_written}/{target_candidate_count})")
        if generated_fraction < MIN_ROUTED_CANDIDATE_FRACTION:
            sys.exit(
                f"generated candidate supply {generated_fraction:.1%} is below the "
                f"{MIN_ROUTED_CANDIDATE_FRACTION:.0%} safety floor; refusing to "
                "route a collapsed demand pool")
    try:
        validate_trip_intents(
            load_trip_intents(trips_path), sumo_edge_ids, measured)
    except ValueError as exc:
        sys.exit(str(exc))

    fit = trip_length_fit(tour_lengths_km)
    fit["through_fraction"] = args.through_fraction
    fit["gravity_km"] = args.gravity_km
    fit["gravity_alpha"] = args.gravity_alpha
    # Destination-clustering guard (2026-07-12, "many cars end their trip at
    # the sensor or just next to it") — measured on the generated pool here,
    # and again on the CALIBRATED output in build_sumo_demand.py, so both
    # stages of the original bug stay visible as numbers.
    edge_latlon = {e["id"]: (e["lat"], e["lon"]) for e in edges}
    dest_edges = ([t[2] for t in trips] if multi_day_trips is None
                  else [t[3] for t in multi_day_trips])
    fit["dest_sensor_proximity"] = destination_sensor_proximity(
        dest_edges, edge_latlon, measured)
    with open(SUMO_DIR / f"trip_length_fit{args.out_suffix}.json", "w") as f:
        json.dump(fit, f, indent=1)
    print(f"  trip-length fit vs RVU short bins {RVU_SHORT_BIN_SHARES}: "
          f"generated {fit['shares']}  L1={fit['l1_distance']}  "
          f"(>10km: {fit.get('over_10km_pct', 0)}%)")
    prox = fit["dest_sensor_proximity"]
    print(f"  destinations within {prox['radius_m']:.0f} m of a sensor: "
          f"{prox['pct_within']}%  (all-edges baseline {prox['baseline_pct_within']}%)")
    if args.fit_only:
        print("  --fit-only: skipping duarouter routing")
        return

    home = sumo_home()
    out = SUMO_DIR / f"candidates{args.out_suffix}.rou.xml"
    meta_out = SUMO_DIR / f"candidates{args.out_suffix}.meta.json"
    with open(meta_out, "w") as f:
        json.dump({"schema_version": 2, "location_pools": location_pools,
                   "candidates": candidate_meta}, f,
                  separators=(",", ":"))
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
         # SUMO may remove an internal loop for performance and route
         # validity.  The original trip XML is checked immediately after
         # routing, so any such repair that changes origin, destination or
         # via is rejected instead of being silently accepted.
         "--ignore-errors", "--no-warnings", "--repair", "--remove-loops"],
        capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        print(res.stderr[-1500:])
        sys.exit("duarouter failed")
    integrity = validate_routed_candidates(
        out, meta_out, measured=measured, sumo_edge_ids=sumo_edge_ids,
        intents_path=trips_path)
    print(f"  route integrity: checked {integrity['checked']}, kept "
        f"{integrity['kept']}, dropped {integrity['dropped']} "
          f"{integrity['reasons']}, missing requested "
          f"{integrity['missing_requested']}")
    if integrity["kept"] == 0:
        sys.exit("duarouter produced no valid candidates after endpoint "
                 "integrity checks")
    requested = integrity.get("requested")
    if requested:
        retention = integrity["kept"] / requested
        print(f"  candidate retention: {retention:.1%} ({integrity['kept']}/"
              f"{requested})")
        if retention < MIN_ROUTED_CANDIDATE_FRACTION:
            sys.exit(
                f"candidate retention {retention:.1%} is below the "
                f"{MIN_ROUTED_CANDIDATE_FRACTION:.0%} safety floor; "
                "refusing to calibrate a collapsed route pool")
    drop_uturn_routes(out)
    drop_excessive_detours(out, routing_G, args.route_diversity, routing_costs)
    prune_candidate_metadata(meta_out, out)
    final_count = sum(1 for _ in ET.parse(out).getroot().iter("vehicle"))
    if requested:
        final_retention = final_count / requested
        if final_retention < MIN_ROUTED_CANDIDATE_FRACTION:
            sys.exit(
                f"final candidate retention {final_retention:.1%} is below "
                f"the {MIN_ROUTED_CANDIDATE_FRACTION:.0%} safety floor after "
                "loop/detour filtering")
    if target_candidate_count:
        final_pool_fraction = final_count / target_candidate_count
        if final_pool_fraction < MIN_ROUTED_CANDIDATE_FRACTION:
            sys.exit(
                f"final candidate supply {final_pool_fraction:.1%} is below the "
                f"{MIN_ROUTED_CANDIDATE_FRACTION:.0%} safety floor relative to "
                "the requested pool")
    cross_report = report_sensor_cross_hits(out, measured)
    uncovered = sorted(m for m, report in cross_report.items()
                       if report["total"] == 0)
    if uncovered:
        sys.exit("candidate pool has no valid route through measured edge(s): "
                 + ", ".join(uncovered))
    print(f"Wrote {out}  ({final_count} routed candidates)")
    print(f"  sensor cross-hit diagnostic (sumo/sensor_coverage_report.json): "
          f"{ {m: r['total'] for m, r in cross_report.items()} }")
    if short_quota:
        # Reprinted here, as the LAST thing main() prints — FOUND
        # 2026-07-10: build_sumo_demand.py's subprocess caller only shows
        # res.stdout[-1200:] (a preview, not the full log), and this
        # warning's first print (inside generate_sensor_anchored_trips,
        # near the START of this run's output) measured well outside that
        # window in a real run (1372 chars of further output followed it,
        # over the 1200-char budget) — a real per-sensor coverage
        # shortfall would have been silently invisible in the normal
        # pipeline, never just theoretically possible.
        print(f"  WARNING: below --min-per-sensor ({args.min_per_sensor}): "
              f"{short_quota} -- see the full log above for detail")


if __name__ == "__main__":
    main()
