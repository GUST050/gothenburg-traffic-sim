"""Anonymous spatial endpoint fields for the SUMO candidate generator.

The traffic counters constrain *flows*, not individual homes or workplaces.
SCB's DeSO population is therefore the authoritative aggregate constraint;
this module only disaggregates that population to plausible, anonymous
building access locations before routes are generated.  It deliberately does
not use address registers, residents, or any person-level data.

Why this exists instead of spreading population over road length:

* a road is a transport link, not a residence;
* a building footprint gives a materially better within-DeSO location prior;
* each sampled trip can start at a position along an access link, rather than
  at the same upstream junction; and
* the field is cached, so it adds no runtime cost to SUMO or web playback.

An official ``data_in/deso/buildings.geojson`` is preferred when available.
Until then an automatically cached OSM footprint extract is a documented
open-data fallback.  Zones without usable residential footprints retain the
previous residential-road allocation as a visible, per-zone fallback rather
than silently losing their SCB population total.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import networkx as nx
import numpy as np
import osmnx as ox
from shapely.geometry import Point, shape
from shapely.strtree import STRtree


KLAT_M = 110_540.0
KLON_M = 111_320.0 * math.cos(math.radians(57.7))
MAX_ACCESS_DISTANCE_M = 180.0

# OSM building values that are strong evidence of a residential use.  The
# list is intentionally conservative; generic ``building=yes`` is used only
# for a zone with no strongly-classified residential footprint after clear
# non-residential uses have been excluded.
RESIDENTIAL_BUILDINGS = {
    "apartments", "bungalow", "cabin", "detached", "dormitory", "house",
    "residential", "semidetached_house", "static_caravan", "terrace",
}
NON_RESIDENTIAL_BUILDINGS = {
    "agricultural", "bakehouse", "barn", "bridge", "carport", "church",
    "civic", "commercial", "construction", "farm", "garages", "garage",
    "grandstand", "greenhouse", "hangar", "hospital", "hotel", "industrial",
    "kindergarten", "kiosk", "manufacture", "office", "parking", "public",
    "retail", "roof", "ruins", "school", "service", "shed", "sports_centre",
    "stadium", "stable", "storage_tank", "supermarket", "train_station",
    "transformer_tower", "transportation", "university", "warehouse",
    "yes:commercial",
}
RESIDENTIAL_TERMS = ("bostad", "bostads", "residential", "apartment", "house")
NON_RESIDENTIAL_TERMS = (
    "commercial", "industrial", "office", "retail", "warehouse", "school",
    "hospital", "garage", "church", "transport", "sport", "shop", "service",
)


@dataclass
class EndpointField:
    """Mass and physical access pools for one endpoint class.

    ``mass`` remains the compact edge-indexed vector used by the existing
    candidate sampler.  ``pools_by_edge`` keeps the disaggregated anonymous
    locations for the selected route's actual departure/arrival position.
    """

    mass: np.ndarray
    pools_by_edge: dict[str, list[dict[str, Any]]]
    report: dict[str, Any]


def _feature_collection(path: Path) -> list[dict[str, Any]]:
    with Path(path).open() as handle:
        doc = json.load(handle)
    features = doc.get("features")
    if not isinstance(features, list):
        raise ValueError(f"{path} is not a GeoJSON FeatureCollection")
    return features


def _first_coordinate(value: Any) -> tuple[float, float] | None:
    """Return the first numeric GeoJSON coordinate pair in a nested value."""
    if isinstance(value, (list, tuple)):
        if len(value) >= 2:
            try:
                lon, lat = float(value[0]), float(value[1])
            except (TypeError, ValueError):
                lon = lat = float("nan")
            if math.isfinite(lon) and math.isfinite(lat):
                return lon, lat
        for child in value:
            found = _first_coordinate(child)
            if found is not None:
                return found
    return None


def _validate_wgs84_features(features: list[dict[str, Any]], path: Path) -> None:
    """Reject projected GeoJSON before it silently becomes a road fallback.

    GeoJSON coordinates are longitude/latitude by contract.  A common Swedish
    delivery mistake is to supply SWEREF metre coordinates, which otherwise
    produces no useful DeSO joins and makes it look as though there simply are
    no buildings.  Detect that class of input immediately; an axis-order swap
    is also caught for Gothenburg's latitude/longitude range.
    """
    for feature in features:
        geometry = feature.get("geometry") or {}
        coordinate = _first_coordinate(geometry.get("coordinates"))
        if coordinate is None:
            continue
        lon, lat = coordinate
        if abs(lon) > 180 or abs(lat) > 90:
            raise ValueError(
                f"{path} is not WGS84 GeoJSON (found coordinate {lon}, {lat}); "
                "reproject the building file to EPSG:4326 before use")
        # The supplied building extract is for Gothenburg.  Coordinates in
        # this common swapped order are still numerically valid WGS84, so the
        # generic [-180,180]/[-90,90] check alone cannot diagnose them.
        if 55.0 <= lon <= 60.0 and 10.0 <= lat <= 14.0:
            raise ValueError(
                f"{path} appears to use latitude,longitude coordinate order; "
                "GeoJSON must use longitude,latitude in EPSG:4326")
        return


def _write_feature_collection(path: Path, features: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as handle:
        json.dump({"type": "FeatureCollection", "features": features}, handle,
                  separators=(",", ":"))
    os.replace(temporary, path)


def ensure_building_features(data_dir: Path, bbox: tuple[float, float, float, float]
                             ) -> tuple[list[dict[str, Any]], str]:
    """Load official building footprints or fetch/cache the OSM fallback.

    ``bbox`` is ``(south, west, north, east)`` as in ``build_data``.  OSMnx
    accepts ``(left, bottom, right, top)``.  A failed first OSM fetch is not
    fatal: callers report and use the existing road-length fallback, keeping
    the program usable offline while making the degraded source explicit.
    """
    official = Path(data_dir) / "buildings.geojson"
    osm_cache = Path(data_dir) / "osm_buildings.geojson"
    if official.exists():
        features = _feature_collection(official)
        _validate_wgs84_features(features, official)
        return features, "official_buildings"
    if osm_cache.exists():
        features = _feature_collection(osm_cache)
        _validate_wgs84_features(features, osm_cache)
        return features, "osm_buildings"

    south, west, north, east = bbox
    try:
        print("  fetching OSM building footprints (first run only, cached after) …")
        gdf = ox.features_from_bbox(
            bbox=(west, south, east, north), tags={"building": True})
        doc = json.loads(gdf.to_json())
        features = doc.get("features", [])
        if not features:
            raise RuntimeError("OSM returned no building footprints")
        _validate_wgs84_features(features, osm_cache)
        _write_feature_collection(osm_cache, features)
        return features, "osm_buildings"
    except Exception as exc:  # network/cache fallback is intentional
        print(f"  WARNING home locations: OSM building fetch failed ({exc}); "
              "using residential-road fallback for this build")
        return [], "road_length_fallback"


def _normalise_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def building_tier(properties: Mapping[str, Any]) -> str | None:
    """Return ``strong``, ``generic`` or ``None`` for a building feature.

    Swedish official deliveries may expose a purpose under a different field
    name than OSM.  Inspect every string-like attribute for the small, clear
    set of residential/non-residential terms while treating an untyped
    footprint as generic rather than pretending it is known residential.
    """
    building = _normalise_text(properties.get("building"))
    all_text = " ".join(_normalise_text(v) for v in properties.values()
                         if isinstance(v, (str, int, float)))
    # A tagged POI/land-use building is much more informative than an
    # untyped ``building=yes`` footprint.  Do this before accepting generic
    # geometry so shops, restaurants, schools and offices cannot absorb a
    # zone's synthetic residents merely because their building tag is sparse.
    if any(properties.get(key) for key in
           ("shop", "office", "industrial", "healthcare", "leisure", "tourism")):
        return None
    amenity = _normalise_text(properties.get("amenity"))
    if amenity and amenity not in {"social_facility", "shelter"}:
        return None
    if building in RESIDENTIAL_BUILDINGS or any(term in all_text
                                                for term in RESIDENTIAL_TERMS):
        return "strong"
    if (building in NON_RESIDENTIAL_BUILDINGS
            or any(term in all_text for term in NON_RESIDENTIAL_TERMS)):
        return None
    if building in {"", "yes", "building", "unknown", "unclassified"}:
        return "generic"
    # Unknown named OSM values are more often building types than uses.  Keep
    # them as generic only when they are not clearly non-residential above.
    return "generic"


def _parse_levels(properties: Mapping[str, Any], tier: str) -> float:
    raw = properties.get("building:levels", properties.get("levels"))
    try:
        value = float(str(raw).replace(",", ".").split(";")[0])
        if math.isfinite(value) and value > 0:
            return min(value, 80.0)
    except (TypeError, ValueError):
        pass
    kind = _normalise_text(properties.get("building"))
    if kind in {"apartments", "dormitory"}:
        return 4.0
    if kind in {"house", "detached", "bungalow", "cabin", "terrace",
                "semidetached_house"}:
        return 1.0
    return 2.0 if tier == "strong" else 1.0


def _footprint_capacity(geom, properties: Mapping[str, Any], tier: str) -> float:
    centre_lat = float(geom.centroid.y)
    area_m2 = abs(float(geom.area)) * KLAT_M * 111_320.0 * max(
        math.cos(math.radians(centre_lat)), 0.1)
    # A small footprint can still hold a household; area is a capacity proxy,
    # never an asserted number of residents.  The floor also avoids zeroing
    # valid narrow row houses because of geometry simplification.
    return max(20.0, area_m2) * _parse_levels(properties, tier)


def _zone_lookup(zones: list[dict[str, Any]], population: Mapping[str, int]
                 ) -> tuple[list[str], list[Any], STRtree]:
    entries = []
    for feature in zones:
        code = feature.get("properties", {}).get("desokod")
        geometry = feature.get("geometry")
        if code in population and geometry:
            entries.append((str(code), shape(geometry)))
    codes = [code for code, _ in entries]
    geometries = [geom for _, geom in entries]
    if not geometries:
        raise ValueError("no populated DeSO geometries available")
    return codes, geometries, STRtree(geometries)


def _tree_indices(tree: STRtree, geometries: list[Any], point: Point) -> Iterable[int]:
    """Yield STRtree result indices across Shapely 1.x/2.x conventions."""
    for item in tree.query(point):
        if isinstance(item, (int, np.integer)):
            yield int(item)
        else:  # Shapely 1.x returns geometry objects
            for index, geom in enumerate(geometries):
                if geom.equals(item):
                    yield index
                    break


def zone_for_point(point: Point, codes: list[str], geometries: list[Any],
                   tree: STRtree) -> str | None:
    for index in _tree_indices(tree, geometries, point):
        # covers includes a footprint centroid that lies exactly on a DeSO
        # border.  The boundary case must not make a real building vanish.
        if geometries[index].covers(point):
            return codes[index]
    return None


def edge_zones(edges: list[dict[str, Any]], zones: list[dict[str, Any]],
               population: Mapping[str, int]) -> list[str | None]:
    codes, geometries, tree = _zone_lookup(zones, population)
    result: list[str | None] = []
    for edge in edges:
        result.append(zone_for_point(Point(edge["lon"], edge["lat"]), codes,
                                     geometries, tree))
    return result


def road_length_fallback(
    edges: list[dict[str, Any]], edge_zone: list[str | None],
    population: Mapping[str, int], residential: set[str],
    excluded_edge_ids: set[str] | None = None,
) -> tuple[np.ndarray, dict[str, float], set[str]]:
    """Road-based fallback that preserves a zone total when OSM is sparse.

    Residential roads remain the first choice.  A populated DeSO can however
    lack that exact OSM highway tag while still contain a usable local street;
    in that case distribute over all non-sensor drivable edges in the zone
    rather than silently dropping its residents.  This is a source-labelled
    fallback, never presented as a building-derived location.
    """
    blocked = set(excluded_edge_ids or ())
    residential_totals: dict[str, float] = defaultdict(float)
    all_road_totals: dict[str, float] = defaultdict(float)
    for edge, zone in zip(edges, edge_zone):
        if zone is None or str(edge.get("id")) in blocked:
            continue
        length = float(edge.get("len", 0.0))
        all_road_totals[zone] += length
        if edge.get("hw") in residential:
            residential_totals[zone] += length
    totals = {
        zone: (residential_totals.get(zone, 0.0) or all_road_totals.get(zone, 0.0))
        for zone in population
    }
    all_road_zones = {zone for zone in population
                      if residential_totals.get(zone, 0.0) <= 0
                      and all_road_totals.get(zone, 0.0) > 0}
    mass = np.zeros(len(edges))
    for index, (edge, zone) in enumerate(zip(edges, edge_zone)):
        total = totals.get(zone or "", 0.0)
        if (zone is None or str(edge.get("id")) in blocked or total <= 0
                or (zone not in all_road_zones and edge.get("hw") not in residential)):
            continue
        mass[index] = float(population[zone]) * float(edge["len"]) / total
    return mass, dict(totals), all_road_zones


def _edge_id(edge_tuple: tuple[Any, Any, Any]) -> str:
    return "_".join(str(v) for v in edge_tuple)


def _edge_position_and_distance(G: nx.MultiDiGraph, edge_tuple: tuple[Any, Any, Any],
                                lon: float, lat: float) -> tuple[float, float]:
    """Project a point onto the directed edge in local metres.

    Returns ``(metres from the edge's start, perpendicular access distance)``.
    The actual edge polyline is used when available; using a start/end chord
    would visibly place locations on the wrong part of curved inner-city
    streets.
    """
    u, v, k = edge_tuple
    data = G.get_edge_data(u, v, k) or {}
    geom = data.get("geometry")
    if geom is not None and hasattr(geom, "coords"):
        coords = list(geom.coords)
    else:
        coords = [(float(G.nodes[u]["x"]), float(G.nodes[u]["y"])),
                  (float(G.nodes[v]["x"]), float(G.nodes[v]["y"]))]
    if len(coords) < 2:
        raise ValueError(f"edge {_edge_id(edge_tuple)} has invalid geometry")

    px, py = lon * KLON_M, lat * KLAT_M
    walked = 0.0
    best_distance = float("inf")
    best_position = 0.0
    for (ax_deg, ay_deg), (bx_deg, by_deg) in zip(coords, coords[1:]):
        ax, ay = float(ax_deg) * KLON_M, float(ay_deg) * KLAT_M
        bx, by = float(bx_deg) * KLON_M, float(by_deg) * KLAT_M
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (length * length)))
        qx, qy = ax + t * dx, ay + t * dy
        distance = math.hypot(px - qx, py - qy)
        if distance < best_distance:
            best_distance = distance
            best_position = walked + t * length
        walked += length
    if not math.isfinite(best_distance):
        raise ValueError(f"edge {_edge_id(edge_tuple)} has zero geometry")
    # Do not create a vehicle exactly at a junction.  A 2m inset is below the
    # geometry precision here yet avoids invalid/no-space insertion at nodes.
    if walked > 4.0:
        best_position = min(max(2.0, best_position), walked - 2.0)
    else:
        best_position = walked / 2.0
    return best_position, best_distance


def map_locations_to_edges(
    G: nx.MultiDiGraph, locations: list[dict[str, Any]], valid_edge_ids: set[str],
    excluded_edge_ids: set[str] | None = None,
    max_access_distance_m: float = MAX_ACCESS_DISTANCE_M,
) -> tuple[list[dict[str, Any]], int]:
    """Map anonymous points to their nearest usable directed road edge.

    Mapping is vectorised through OSMnx's edge spatial index.  The measured
    edge itself is removed from the access graph, but no arbitrary distance
    buffer around a sensor is applied: a genuine building beside a counter is
    still a legitimate origin.  A far-away snap is rejected because it is an
    input/topology error, not a plausible driveway.
    """
    if not locations:
        return [], 0
    blocked = set(excluded_edge_ids or ())
    access_graph = G.copy()
    remove = []
    for u, v, k in access_graph.edges(keys=True):
        if _edge_id((u, v, k)) in blocked:
            remove.append((u, v, k))
    access_graph.remove_edges_from(remove)
    if access_graph.number_of_edges() == 0:
        raise ValueError("no usable road edges remain for endpoint access")

    try:
        nearest = ox.nearest_edges(
            access_graph,
            X=[float(item["lon"]) for item in locations],
            Y=[float(item["lat"]) for item in locations],
        )
    except Exception as exc:
        raise RuntimeError(f"could not map endpoint locations to roads: {exc}") from exc
    nearest_list = list(nearest) if isinstance(nearest, np.ndarray) else [nearest]
    if len(nearest_list) != len(locations):
        raise RuntimeError("nearest-edge mapper returned an unexpected result length")

    mapped: list[dict[str, Any]] = []
    rejected = 0
    for item, raw_edge in zip(locations, nearest_list):
        edge_tuple = tuple(raw_edge)
        edge_id = _edge_id(edge_tuple)
        if edge_id not in valid_edge_ids or edge_id in blocked:
            rejected += 1
            continue
        try:
            position_m, distance_m = _edge_position_and_distance(
                G, edge_tuple, float(item["lon"]), float(item["lat"]))
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        if distance_m > max_access_distance_m:
            rejected += 1
            continue
        mapped.append({**item, "edge_id": edge_id,
                       "pos_m": round(position_m, 2),
                       "access_distance_m": round(distance_m, 2)})
    return mapped, rejected


def _building_records(features: list[dict[str, Any]], zones: list[dict[str, Any]],
                      population: Mapping[str, int]) -> tuple[dict[str, list[dict[str, Any]]],
                                                             dict[str, list[dict[str, Any]]], int]:
    codes, geometries, tree = _zone_lookup(zones, population)
    strong: dict[str, list[dict[str, Any]]] = defaultdict(list)
    generic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid = 0
    for index, feature in enumerate(features):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry:
            invalid += 1
            continue
        try:
            geom = shape(geometry)
            if geom.is_empty or geom.geom_type not in {"Polygon", "MultiPolygon"}:
                invalid += 1
                continue
            # ``centroid`` may fall in a courtyard/hole or outside a concave
            # footprint.  A representative point is guaranteed to be inside
            # the building geometry, so both the DeSO join and road access
            # snap retain a real physical location.
            centre = geom.representative_point()
        except Exception:
            invalid += 1
            continue
        zone = zone_for_point(centre, codes, geometries, tree)
        if zone is None:
            continue
        tier = building_tier(props)
        if tier is None:
            continue
        record = {
            "id": str(feature.get("id") or props.get("id") or f"building-{index}"),
            "lon": float(centre.x), "lat": float(centre.y), "zone": zone,
            "weight": _footprint_capacity(geom, props, tier), "kind": "home",
        }
        (strong if tier == "strong" else generic)[zone].append(record)
    return strong, generic, invalid


def build_home_field(
    G: nx.MultiDiGraph, edges: list[dict[str, Any]], zones: list[dict[str, Any]],
    population: Mapping[str, int], data_dir: Path,
    bbox: tuple[float, float, float, float], residential: set[str],
    excluded_edge_ids: set[str] | None = None,
) -> EndpointField:
    """Create an anonymous home-location field conserving every routable DeSO.

    Strongly residential footprints take precedence in a zone.  Generic
    footprints are a carefully disclosed fallback only if no strong one is
    available there.  Any still-uncovered zone gets the previous road-length
    allocation, preserving both availability and the exact DeSO headcount.
    """
    features, source = ensure_building_features(data_dir, bbox)
    edge_zone = edge_zones(edges, zones, population)
    legacy_mass, road_length, all_road_fallback_zones = road_length_fallback(
        edges, edge_zone, population, residential,
        excluded_edge_ids=excluded_edge_ids)
    zones_with_road_access = {zone for zone, total in road_length.items()
                              if total > 0}
    if not features:
        zones_without_routable_access = sorted(set(population) - zones_with_road_access)
        population_without_routable_access = float(
            sum(population[zone] for zone in zones_without_routable_access))
        report = {
            "source": source, "method": "residential_road_fallback",
            "zones_total": len(population), "zones_building": 0,
            # Only zones represented by a usable edge actually receive the
            # road fallback.  Keeping the disconnected zones separate makes
            # this report an accounting check instead of a misleading claim
            # that all DeSO residents were routed into the inner-city graph.
            "zones_road_fallback": len(zones_with_road_access),
            "population_building": 0.0, "population_road_fallback": float(legacy_mass.sum()),
            "locations_mapped": 0, "locations_rejected": 0,
            "unallocated_population": round(float(sum(population.values()) - legacy_mass.sum()), 3),
            "zones_without_routable_access": zones_without_routable_access,
            "population_without_routable_access": round(population_without_routable_access, 3),
            "road_fallback_all_road_zones": len(all_road_fallback_zones),
        }
        return EndpointField(legacy_mass, {}, report)

    strong, generic, invalid = _building_records(features, zones, population)
    selected_by_zone: dict[str, list[dict[str, Any]]] = {}
    for zone in population:
        selected_by_zone[zone] = strong.get(zone) or generic.get(zone) or []
    selected = [record for records in selected_by_zone.values() for record in records]
    valid_ids = {str(edge["id"]) for edge in edges}
    mapped, rejected = map_locations_to_edges(
        G, selected, valid_ids, excluded_edge_ids=excluded_edge_ids)

    mapped_by_zone: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in mapped:
        mapped_by_zone[str(record["zone"])].append(record)
    edge_index = {str(edge["id"]): index for index, edge in enumerate(edges)}
    mass = np.zeros(len(edges))
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    building_zones: set[str] = set()
    for zone, records in mapped_by_zone.items():
        total_weight = sum(float(record["weight"]) for record in records)
        if total_weight <= 0 or zone not in population:
            continue
        building_zones.add(zone)
        for record in records:
            edge_id = str(record["edge_id"])
            mass[edge_index[edge_id]] += float(population[zone]) * float(record["weight"]) / total_weight
            pools[edge_id].append({
                "id": str(record["id"]), "p": float(record["pos_m"]),
                "w": float(record["weight"]), "kind": "home",
            })

    fallback_zones = set(population) - building_zones
    for index, zone in enumerate(edge_zone):
        if zone in fallback_zones:
            mass[index] += legacy_mass[index]
    zones_without_routable_access = sorted(fallback_zones - zones_with_road_access)
    road_fallback_zones = fallback_zones & zones_with_road_access
    population_without_routable_access = float(
        sum(population[zone] for zone in zones_without_routable_access))
    allocated = float(mass.sum())
    report = {
        "source": source,
        "method": "building_footprints_with_per_zone_road_fallback",
        "zones_total": len(population),
        "zones_building": len(building_zones),
        "zones_road_fallback": len(road_fallback_zones),
        "population_building": round(float(sum(population[z] for z in building_zones)), 3),
        "population_road_fallback": round(
            float(sum(population[z] for z in road_fallback_zones)), 3),
        "locations_selected": len(selected),
        "locations_mapped": len(mapped),
        "locations_rejected": rejected,
        "locations_invalid": invalid,
        "access_edges": len(pools),
        "unallocated_population": round(float(sum(population.values()) - allocated), 3),
        "zones_without_routable_access": zones_without_routable_access,
        "population_without_routable_access": round(population_without_routable_access, 3),
        "road_length_zones_available": len(road_length),
        "road_fallback_all_road_zones": len(fallback_zones & all_road_fallback_zones),
    }
    return EndpointField(mass, dict(pools), report)


def ensure_poi_features(data_dir: Path, bbox: tuple[float, float, float, float],
                        poi_tags: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Load/fetch the existing OSM POI cache without changing its contract."""
    cache = Path(data_dir) / "osm_pois.geojson"
    if cache.exists():
        return _feature_collection(cache)
    print("  fetching OSM POIs (first run only, cached after) …")
    all_tags: dict[str, Any] = {}
    for category_tags in poi_tags.values():
        for key, values in category_tags.items():
            existing = all_tags.get(key)
            if values is True or existing is True:
                all_tags[key] = True
            else:
                all_tags[key] = set(existing or set()) | set(values)
    request_tags = {key: (True if values is True else sorted(values))
                    for key, values in all_tags.items()}
    south, west, north, east = bbox
    gdf = ox.features_from_bbox(bbox=(west, south, east, north), tags=request_tags)
    doc = json.loads(gdf.to_json())
    features = doc.get("features", [])
    _write_feature_collection(cache, features)
    return features


def _matches_category(properties: Mapping[str, Any], tags: Mapping[str, Any]) -> bool:
    for key, allowed in tags.items():
        value = properties.get(key)
        if value and (allowed is True or value in allowed):
            return True
    return False


def build_activity_fields(
    G: nx.MultiDiGraph, edges: list[dict[str, Any]], data_dir: Path,
    bbox: tuple[float, float, float, float], poi_tags: Mapping[str, Mapping[str, Any]],
    excluded_edge_ids: set[str] | None = None,
) -> dict[str, EndpointField]:
    """Map each OSM POI to one physical access edge, rather than a 150m halo.

    The old broad catchment gave one POI positive destination mass on every
    nearby edge, which overweights a junction and cannot provide a physical
    departure position for the return leg.  A nearest-access assignment is
    both more faithful and asymptotically faster.
    """
    features = ensure_poi_features(data_dir, bbox, poi_tags)
    raw: list[dict[str, Any]] = []
    for index, feature in enumerate(features):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry:
            continue
        try:
            # POIs can be mapped as complex areas (for example a mall or a
            # park), where a centroid need not be inside the area.  Use a
            # guaranteed interior point for the physical access assignment.
            centre = shape(geometry).representative_point()
        except Exception:
            continue
        for category, tags in poi_tags.items():
            if _matches_category(props, tags):
                raw.append({
                    "id": str(feature.get("id") or props.get("id") or f"poi-{index}"),
                    "lon": float(centre.x), "lat": float(centre.y),
                    "kind": category, "category": category, "weight": 1.0,
                })

    valid_ids = {str(edge["id"]) for edge in edges}
    mapped, rejected = map_locations_to_edges(
        G, raw, valid_ids, excluded_edge_ids=excluded_edge_ids)
    edge_index = {str(edge["id"]): index for index, edge in enumerate(edges)}
    fields: dict[str, EndpointField] = {}
    for category in poi_tags:
        mass = np.zeros(len(edges))
        pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
        category_input = sum(1 for record in raw if record["category"] == category)
        category_records = [record for record in mapped if record["category"] == category]
        for record in category_records:
            edge_id = str(record["edge_id"])
            mass[edge_index[edge_id]] += 1.0
            pools[edge_id].append({
                "id": str(record["id"]), "p": float(record["pos_m"]),
                "w": 1.0, "kind": category,
            })
        fields[category] = EndpointField(
            mass, dict(pools), {
                "source": "osm_pois", "method": "poi_nearest_road_access",
                "locations_input": category_input,
                "locations_mapped": len(category_records),
                # ``map_locations_to_edges`` receives all three categories in
                # one vectorised spatial query.  Publish the category's own
                # difference, not the global rejection total three times.
                "locations_rejected": category_input - len(category_records),
                "access_edges": len(pools),
            })
    return fields
