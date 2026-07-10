"""Pure geometry helpers for dirsplit — kept dependency-free and unit-tested."""

from __future__ import annotations

import math


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Bearing (lat1,lon1)→(lat2,lon2) in degrees, 0=N 90=E, cos-lat corrected."""
    dlat = lat2 - lat1
    dlon = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.degrees(math.atan2(dlon, dlat)) % 360


def ang_diff_deg(a: float, b: float) -> float:
    """Smallest absolute angle between two bearings (0–180)."""
    return abs((a - b + 180) % 360 - 180)


def circular_mean_deg(a: float, b: float) -> float:
    """Mean of two bearings, correct across the 0/360 wraparound.

    A naive (a+b)/2 is wrong whenever the two bearings straddle north: for
    350 and 10 (both close to due north, ang_diff_deg only 20) it gives 180
    (due SOUTH) instead of ~0/360. Vector-averaging (mean sin, mean cos,
    atan2 back) handles wraparound correctly; found in a review 2026-07-10
    while auditing observability.py's corridor-coupling bearing average,
    which is the caller that needed this."""
    ra, rb = math.radians(a), math.radians(b)
    s = (math.sin(ra) + math.sin(rb)) / 2
    c = (math.cos(ra) + math.cos(rb)) / 2
    return math.degrees(math.atan2(s, c)) % 360


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def is_ahead(point_bearing: float, travel_bearing: float) -> bool:
    """True if a point (at bearing point_bearing from the edge midpoint) lies in
    the half-plane AHEAD of travel direction travel_bearing (±90°)."""
    return ang_diff_deg(point_bearing, travel_bearing) < 90


def radial_cos(travel_bearing: float, bearing_to_centre: float) -> float:
    """+1 = travelling straight toward the centre, -1 = straight away."""
    return math.cos(math.radians(travel_bearing - bearing_to_centre))


def in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    s, w, n, e = bbox
    return s <= lat <= n and w <= lon <= e


def point_to_polyline_m(lat: float, lon: float,
                        coords: list[tuple[float, float]]) -> float:
    """Min distance (m) from a point to a polyline of (lon, lat) pairs.

    Uses a local equirectangular frame around the point — accurate to well
    under a metre at city scale, and correct for long straight segments
    where vertex-only distance would overestimate badly (tunnels).
    """
    klat = 110_540.0
    klon = 111_320.0 * math.cos(math.radians(lat))

    def to_xy(p: tuple[float, float]) -> tuple[float, float]:
        return ((p[0] - lon) * klon, (p[1] - lat) * klat)

    best = float("inf")
    pts = [to_xy(p) for p in coords]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, (-(x1 * dx + y1 * dy)) / seg2))
        px, py = x1 + t * dx, y1 + t * dy
        best = min(best, math.hypot(px, py))
    return best
