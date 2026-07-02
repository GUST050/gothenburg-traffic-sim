"""
Match Norwegian stations to OSM edges and resolve heading→bearing.

Usage:
  python3 -m dirsplit.match [--city trondheim]

Each station reports its two directions as PLACE NAMES ("Charlottenlund" /
"Skovgård"). To turn a heading into a travel bearing we:
  1. snap the station to its OSM edge (nearest_edges on the city drive graph)
     → the road's axis gives two candidate travel bearings (θ and θ+180),
  2. geocode both heading names (Nominatim via osmnx, cached + throttled)
     → bearing from the station toward each named place,
  3. assign each heading to the candidate bearing it is closest to.

CONSISTENCY REQUIREMENT: the two headings must resolve to OPPOSITE candidates
and each within 75° of its candidate. Stations that fail are flagged
unmatched and EXCLUDED from training — bad labels are worse than fewer
labels. Everything is stored for audit (geocoded points, angles, distances).

Writes data/dirsplit/stations_matched.json.
"""

from __future__ import annotations

import argparse
import json
import time

import osmnx as ox

from .config import GEOCODE_CACHE, STATIONS_OK, STATIONS_RAW
from .features import edge_bearing_from_graph, load_city_graph
from .geo import ang_diff_deg, bearing_deg, point_to_polyline_m

MAX_SNAP_M    = 60    # station must lie near its OSM edge
MAX_HEAD_DIFF = 75    # heading-geocode bearing vs road axis tolerance (deg)


def _load_cache() -> dict:
    if GEOCODE_CACHE.exists():
        with open(GEOCODE_CACHE) as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict) -> None:
    with open(GEOCODE_CACHE, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=0)


def geocode(place: str, city: str, cache: dict) -> tuple[float, float] | None:
    """Nominatim lookup '<place>, <city>, Norway' — cached, 1 req/s."""
    key = f"{place}|{city}"
    if key in cache:
        return tuple(cache[key]) if cache[key] else None
    try:
        time.sleep(1.0)   # Nominatim usage policy
        lat, lon = ox.geocode(f"{place}, {city}, Norway")
        cache[key] = [lat, lon]
        return (lat, lon)
    except Exception:
        try:
            time.sleep(1.0)
            lat, lon = ox.geocode(f"{place}, Norway")
            cache[key] = [lat, lon]
            return (lat, lon)
        except Exception:
            cache[key] = None
            return None


def match_station(st: dict, G, cache: dict) -> dict:
    """Return the station dict extended with match results + audit fields."""
    out = dict(st)
    u, v, k = ox.nearest_edges(G, X=st["lon"], Y=st["lat"])
    data = G.get_edge_data(u, v, k)
    # TRUE perpendicular distance to the edge geometry, computed ourselves —
    # midpoint distance wrongly rejects stations on long edges (tunnels), and
    # osmnx's return_dist unit depends on graph projection.
    if "geometry" in data:
        coords = list(data["geometry"].coords)
    else:
        coords = [(G.nodes[u]["x"], G.nodes[u]["y"]),
                  (G.nodes[v]["x"], G.nodes[v]["y"])]
    snap_m = point_to_polyline_m(st["lat"], st["lon"], coords)

    theta = edge_bearing_from_graph(G, u, v, k, data)
    candidates = {0: theta, 1: (theta + 180) % 360}

    out.update({
        "osm_edge": f"{u}_{v}_{k}",
        "snap_dist_m": round(snap_m, 1),
        "road_bearing": round(theta, 1),
        "matched": False, "reject_reason": None, "heading_bearings": {},
    })
    if snap_m > MAX_SNAP_M:
        out["reject_reason"] = f"snap {snap_m:.0f} m > {MAX_SNAP_M}"
        return out

    assignments: dict[str, int] = {}
    audit: dict[str, dict] = {}
    for heading in (st["heading_from"], st["heading_to"]):
        if not heading:
            out["reject_reason"] = "missing heading name"
            return out
        pt = geocode(heading, st["city"], cache)
        if pt is None:
            out["reject_reason"] = f"geocode failed: {heading}"
            return out
        b = bearing_deg(st["lat"], st["lon"], pt[0], pt[1])
        diffs = {i: ang_diff_deg(b, cand) for i, cand in candidates.items()}
        best = min(diffs, key=diffs.get)
        audit[heading] = {"geocoded": pt, "bearing_to_place": round(b, 1),
                          "diff_deg": round(diffs[best], 1)}
        if diffs[best] > MAX_HEAD_DIFF:
            out["reject_reason"] = (f"heading '{heading}' {diffs[best]:.0f}° "
                                    f"from road axis")
            out["heading_audit"] = audit
            return out
        assignments[heading] = best

    if len(set(assignments.values())) != 2:
        out["reject_reason"] = "both headings resolved to the same direction"
        out["heading_audit"] = audit
        return out

    out["matched"] = True
    out["heading_bearings"] = {
        h: round(candidates[i], 1) for h, i in assignments.items()
    }
    out["heading_audit"] = audit
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--city", default=None, help="limit to one city")
    args = p.parse_args()

    with open(STATIONS_RAW) as f:
        stations = json.load(f)
    if args.city:
        stations = [s for s in stations if s["city"] == args.city]

    cache = _load_cache()
    results: list[dict] = []
    # Resume: keep all previous results. --city means "redo that city".
    if STATIONS_OK.exists():
        with open(STATIONS_OK) as f:
            prev = json.load(f)
        results = [s for s in prev
                   if not (args.city and s["city"] == args.city)]

    by_city: dict[str, list] = {}
    for st in stations:
        by_city.setdefault(st["city"], []).append(st)

    # Skip stations already matched in a previous (possibly interrupted) run
    done_ids = {s["id"] for s in results}

    for city, sts in by_city.items():
        sts = [s for s in sts if s["id"] not in done_ids]
        if not sts:
            continue
        print(f"── {city} — {len(sts)} stations, loading OSM graph …")
        G = load_city_graph(city)
        ok = 0
        for st in sts:
            m = match_station(st, G, cache)
            results.append(m)
            if m["matched"]:
                ok += 1
            else:
                print(f"    ✗ {m['id']} {m['name'][:30]:<30} — {m['reject_reason']}")
        _save_cache(cache)
        # Save after EVERY city — an interrupted run loses at most one city,
        # and the rerun skips everything already done.
        with open(STATIONS_OK, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print(f"    {ok}/{len(sts)} matched — saved")
    n_ok = sum(1 for r in results if r["matched"])
    print(f"Wrote {STATIONS_OK}  ({n_ok}/{len(results)} matched)")


if __name__ == "__main__":
    main()
