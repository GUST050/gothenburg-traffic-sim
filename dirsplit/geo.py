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
