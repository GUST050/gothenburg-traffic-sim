"""
Feature computation for directed edges — ONE code path for Norwegian training
stations and Gothenburg target edges. Adding a sensor later requires nothing
here: features are derived from coordinates + bearing + an OSM graph.

Features per DIRECTED edge:
  hw_rank        — ordinal road class (0=motorway … 7=living_street)
  speed_kmh      — maxspeed with class fallback
  lanes          — per-direction lanes with class fallback
  oneway         — 1.0/0.0
  dist_centre_km — distance edge→city centre
  radial_cos     — +1 travelling toward centre, −1 away (THE key feature)
  res_behind_km / res_ahead_km — residential street length (km) within
                   LANDUSE_RADIUS_M in the half-disc behind vs ahead of travel.
                   Residential street density is the population proxy: homes
                   behind you and destinations ahead → AM peak in this
                   direction. (Upgrade path: GHS-POP raster.)
  res_asym       — (behind − ahead) / (behind + ahead), 0 when both empty
  major_ahead_km / major_behind_km — primary/secondary/trunk length (activity
                   proxy), and major_asym analogous.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import osmnx as ox

from .config import (CITIES, DEFAULT_SPEED_KMH, HIGHWAY_RANK,
                     LANDUSE_RADIUS_M, TARGET_CENTRE)
from .geo import ang_diff_deg, bearing_deg, haversine_m, is_ahead, radial_cos

RESIDENTIAL = {"residential", "living_street"}
MAJOR       = {"trunk", "primary", "secondary"}

FEATURE_NAMES = [
    "hw_rank", "speed_kmh", "lanes", "oneway",
    "dist_centre_km", "radial_cos",
    "res_behind_km", "res_ahead_km", "res_asym",
    "major_behind_km", "major_ahead_km", "major_asym",
]


def _scalar(v):
    return v[0] if isinstance(v, list) else v


GRAPHS_DIR = Path("data/dirsplit/graphs")

# Fallback Overpass mirrors — the main instance rate-limits aggressively
OVERPASS_URLS = [
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.private.coffee/api",
]


@lru_cache(maxsize=8)
def load_city_graph(city: str):
    """Drive graph for a training city — downloaded ONCE, persisted as
    graphml so all reruns are offline. Target city uses the project's own
    frozen snapshot (web/data/graph.graphml)."""
    ox.settings.use_cache = True
    ox.settings.cache_folder = "cache"
    if city == "goteborg":
        return ox.load_graphml(Path("web/data/graph.graphml"))

    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    snap = GRAPHS_DIR / f"{city}.graphml"
    if snap.exists():
        return ox.load_graphml(snap)

    s, w, n, e = CITIES[city]["bbox"]
    last_err: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            ox.settings.overpass_url = url
            G = ox.graph_from_bbox(bbox=(w, s, e, n), network_type="drive",
                                   simplify=True)
            ox.save_graphml(G, snap)
            print(f"    saved {snap} ({G.number_of_edges()} edges, via {url})")
            return G
        except Exception as err:
            print(f"    Overpass {url} failed: {err} — trying next mirror")
            last_err = err
    raise RuntimeError(f"all Overpass mirrors failed for {city}: {last_err}")


def city_centre(city: str) -> tuple[float, float]:
    return TARGET_CENTRE if city == "goteborg" else CITIES[city]["centre"]


@lru_cache(maxsize=8)
def _edge_midpoints(city: str) -> list[tuple[float, float, str, float]]:
    """(lat, lon, highway, length_m) midpoint summary of every edge — the
    spatial index used for the land-use half-disc counts."""
    G = load_city_graph(city)
    out = []
    for u, v, k, d in G.edges(keys=True, data=True):
        lat = (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2
        lon = (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2
        hw = str(_scalar(d.get("highway", "")) or "")
        out.append((lat, lon, hw, float(d.get("length", 0.0))))
    return out


def parse_speed(data: dict, hw: str) -> float:
    raw = _scalar(data.get("maxspeed"))
    if raw is not None:
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        if digits:
            return float(digits)
    return float(DEFAULT_SPEED_KMH.get(hw, 50))


def parse_lanes(data: dict, hw: str) -> float:
    raw = _scalar(data.get("lanes"))
    if raw is not None:
        digits = "".join(ch for ch in str(raw).split(";")[0] if ch.isdigit())
        if digits:
            total = int(digits)
            oneway = data.get("oneway") in (True, "True", "true", "yes")
            return float(min(total if oneway else max(1, total // 2), 4))
    return float(2 if HIGHWAY_RANK.get(hw, 5) <= 2 else 1)


def halfdisc_lengths(city: str, lat: float, lon: float,
                     travel_bearing: float) -> dict[str, float]:
    """Residential/major street length (km) behind vs ahead of travel."""
    acc = {"res_behind": 0.0, "res_ahead": 0.0,
           "major_behind": 0.0, "major_ahead": 0.0}
    for elat, elon, hw, length in _edge_midpoints(city):
        # cheap prefilter before haversine
        if abs(elat - lat) > 0.02 or abs(elon - lon) > 0.04:
            continue
        if haversine_m(lat, lon, elat, elon) > LANDUSE_RADIUS_M:
            continue
        group = "res" if hw in RESIDENTIAL else "major" if hw in MAJOR else None
        if group is None:
            continue
        side = "ahead" if is_ahead(bearing_deg(lat, lon, elat, elon),
                                   travel_bearing) else "behind"
        acc[f"{group}_{side}"] += length / 1000.0
    return acc


def _asym(behind: float, ahead: float) -> float:
    total = behind + ahead
    return (behind - ahead) / total if total > 0 else 0.0


def edge_features(city: str, lat: float, lon: float, travel_bearing: float,
                  edge_data: dict) -> dict[str, float]:
    """The one shared feature function. edge_data = OSM edge attribute dict."""
    hw = str(_scalar(edge_data.get("highway", "")) or "")
    clat, clon = city_centre(city)

    disc = halfdisc_lengths(city, lat, lon, travel_bearing)

    return {
        "hw_rank":        float(HIGHWAY_RANK.get(hw, 5)),
        "speed_kmh":      parse_speed(edge_data, hw),
        "lanes":          parse_lanes(edge_data, hw),
        "oneway":         1.0 if edge_data.get("oneway") in (True, "True", "true", "yes") else 0.0,
        "dist_centre_km": haversine_m(lat, lon, clat, clon) / 1000.0,
        "radial_cos":     radial_cos(travel_bearing,
                                     bearing_deg(lat, lon, clat, clon)),
        "res_behind_km":   round(disc["res_behind"], 3),
        "res_ahead_km":    round(disc["res_ahead"], 3),
        "res_asym":        round(_asym(disc["res_behind"], disc["res_ahead"]), 4),
        "major_behind_km": round(disc["major_behind"], 3),
        "major_ahead_km":  round(disc["major_ahead"], 3),
        "major_asym":      round(_asym(disc["major_behind"], disc["major_ahead"]), 4),
    }


def edge_bearing_from_graph(G, u: int, v: int, k: int, data: dict) -> float:
    """Travel bearing of directed edge u→v, using full geometry when present."""
    if "geometry" in data:
        coords = list(data["geometry"].coords)          # (lon, lat)
        (lon1, lat1), (lon2, lat2) = coords[0], coords[-1]
    else:
        lat1, lon1 = G.nodes[u]["y"], G.nodes[u]["x"]
        lat2, lon2 = G.nodes[v]["y"], G.nodes[v]["x"]
    return bearing_deg(lat1, lon1, lat2, lon2)
