"""Tests for anonymous building/POI endpoint spatialisation."""

from pathlib import Path

import networkx as nx
import numpy as np
from shapely.geometry import shape

from demand import locations as loc


def _zones():
    return [
        {"properties": {"desokod": "A"}, "geometry": {
            "type": "Polygon", "coordinates": [[[11.0, 57.0], [11.5, 57.0],
                                                      [11.5, 57.5], [11.0, 57.5],
                                                      [11.0, 57.0]]]}},
        {"properties": {"desokod": "B"}, "geometry": {
            "type": "Polygon", "coordinates": [[[11.5, 57.0], [12.0, 57.0],
                                                      [12.0, 57.5], [11.5, 57.5],
                                                      [11.5, 57.0]]]}}
    ]


def _edges():
    return [
        {"id": "a", "lat": 57.2, "lon": 11.2, "hw": "residential", "len": 100.0},
        {"id": "b", "lat": 57.2, "lon": 11.7, "hw": "residential", "len": 200.0},
    ]


def test_home_field_preserves_each_deso_total_with_building_accesses(monkeypatch, tmp_path):
    features = [
        {"id": "home-a", "properties": {"building": "apartments", "building:levels": "4"},
         "geometry": {"type": "Polygon", "coordinates": [[[11.19, 57.19], [11.21, 57.19],
                                                                  [11.21, 57.21], [11.19, 57.21],
                                                                  [11.19, 57.19]]]}},
        {"id": "home-b", "properties": {"building": "house"},
         "geometry": {"type": "Polygon", "coordinates": [[[11.69, 57.19], [11.71, 57.19],
                                                                  [11.71, 57.21], [11.69, 57.21],
                                                                  [11.69, 57.19]]]}},
    ]
    monkeypatch.setattr(loc, "ensure_building_features",
                        lambda *_args: (features, "osm_buildings"))

    def fake_map(_graph, records, _valid, **_kwargs):
        mapped = []
        for record in records:
            mapped.append({**record, "edge_id": "a" if record["zone"] == "A" else "b",
                           "pos_m": 25.0, "access_distance_m": 3.0})
        return mapped, 0

    monkeypatch.setattr(loc, "map_locations_to_edges", fake_map)
    field = loc.build_home_field(
        nx.MultiDiGraph(), _edges(), _zones(), {"A": 100, "B": 200},
        tmp_path, (57.0, 11.0, 57.5, 12.0), {"residential"})

    assert field.mass.tolist() == [100.0, 200.0]
    assert field.report["zones_building"] == 2
    assert field.report["zones_road_fallback"] == 0
    assert field.report["unallocated_population"] == 0.0
    assert field.pools_by_edge["a"][0]["id"] == "home-a"


def test_home_field_falls_back_only_for_uncovered_deso(monkeypatch, tmp_path):
    features = [
        {"id": "home-a", "properties": {"building": "house"},
         "geometry": {"type": "Polygon", "coordinates": [[[11.19, 57.19], [11.21, 57.19],
                                                                  [11.21, 57.21], [11.19, 57.21],
                                                                  [11.19, 57.19]]]}},
    ]
    monkeypatch.setattr(loc, "ensure_building_features",
                        lambda *_args: (features, "osm_buildings"))
    monkeypatch.setattr(
        loc, "map_locations_to_edges",
        lambda _graph, records, _valid, **_kwargs: (
            [{**records[0], "edge_id": "a", "pos_m": 30.0,
              "access_distance_m": 2.0}], 0))

    field = loc.build_home_field(
        nx.MultiDiGraph(), _edges(), _zones(), {"A": 100, "B": 200},
        tmp_path, (57.0, 11.0, 57.5, 12.0), {"residential"})

    # A uses its building. B has no usable footprint, so exactly its own
    # DeSO total retains the legacy residential-road fallback, not A's.
    assert np.allclose(field.mass, [100.0, 200.0])
    assert field.report["zones_building"] == 1
    assert field.report["zones_road_fallback"] == 1


def test_home_field_does_not_report_an_unroutable_deso_as_fallback(monkeypatch, tmp_path):
    """A DeSO outside the graph must be disclosed, not placed on a local road."""
    features = [
        {"id": "home-a", "properties": {"building": "house"},
         "geometry": {"type": "Polygon", "coordinates": [[[11.19, 57.19], [11.21, 57.19],
                                                                  [11.21, 57.21], [11.19, 57.21],
                                                                  [11.19, 57.19]]]}}
    ]
    monkeypatch.setattr(loc, "ensure_building_features",
                        lambda *_args: (features, "osm_buildings"))
    monkeypatch.setattr(
        loc, "map_locations_to_edges",
        lambda _graph, records, _valid, **_kwargs: (
            [{**records[0], "edge_id": "a", "pos_m": 30.0,
              "access_distance_m": 2.0}], 0))

    field = loc.build_home_field(
        nx.MultiDiGraph(), _edges()[:1], _zones(), {"A": 100, "B": 200},
        tmp_path, (57.0, 11.0, 57.5, 12.0), {"residential"})

    assert field.mass.tolist() == [100.0]
    assert field.report["zones_road_fallback"] == 0
    assert field.report["population_road_fallback"] == 0.0
    assert field.report["zones_without_routable_access"] == ["B"]
    assert field.report["population_without_routable_access"] == 200.0
    assert field.report["unallocated_population"] == 200.0


def test_building_classification_rejects_tagged_commercial_poi():
    assert loc.building_tier({"building": "yes", "shop": "supermarket"}) is None
    assert loc.building_tier({"building": "hotel"}) is None
    assert loc.building_tier({"building": "apartments"}) == "strong"
    assert loc.building_tier({"building": "yes"}) == "generic"


def test_building_access_point_stays_inside_a_polygon_with_a_courtyard(monkeypatch):
    """Do not use a centroid that could land inside a building's courtyard."""
    geometry = {
        "type": "Polygon", "coordinates": [
            [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]],
            [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]],
        ]
    }
    footprint = shape(geometry)
    observed = []

    def zone_if_inside(point, *_args):
        observed.append(point)
        return "A" if footprint.covers(point) else None

    monkeypatch.setattr(loc, "zone_for_point", zone_if_inside)
    zones = [{"properties": {"desokod": "A"}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]]}}]
    strong, generic, invalid = loc._building_records(
        [{"id": "courtyard", "properties": {"building": "apartments"},
          "geometry": geometry}], zones, {"A": 1})

    assert len(strong["A"]) == 1
    assert not generic
    assert invalid == 0
    assert footprint.covers(observed[0])


def test_building_file_coordinate_guard_rejects_sweref_and_swapped_wgs84(tmp_path):
    projected = [{"geometry": {"coordinates": [[[320000, 6400000],
                                                    [320010, 6400000]]]}}]
    swapped = [{"geometry": {"coordinates": [[[57.7, 11.9],
                                                  [57.7, 11.91]]]}}]

    with np.testing.assert_raises_regex(ValueError, "not WGS84"):
        loc._validate_wgs84_features(projected, tmp_path / "buildings.geojson")
    with np.testing.assert_raises_regex(ValueError, "latitude,longitude"):
        loc._validate_wgs84_features(swapped, tmp_path / "buildings.geojson")


def test_activity_report_counts_rejections_per_purpose(monkeypatch, tmp_path):
    features = [
        {"id": "work", "properties": {"amenity": "school"},
         "geometry": {"type": "Point", "coordinates": [11.2, 57.2]}},
        {"id": "shop", "properties": {"shop": "supermarket"},
         "geometry": {"type": "Point", "coordinates": [11.3, 57.2]}},
    ]
    tags = {"arbete": {"amenity": {"school"}}, "service": {"shop": True}}
    monkeypatch.setattr(loc, "ensure_poi_features", lambda *_args: features)
    monkeypatch.setattr(
        loc, "map_locations_to_edges",
        lambda _graph, records, _valid, **_kwargs: (
            [{**records[0], "edge_id": "a", "pos_m": 12.0,
              "access_distance_m": 2.0}], 1))

    fields = loc.build_activity_fields(
        nx.MultiDiGraph(), _edges(), tmp_path, (57.0, 11.0, 57.5, 12.0), tags)

    assert fields["arbete"].report["locations_rejected"] == 0
    assert fields["service"].report["locations_rejected"] == 1
