"""
Contract tests — validate that network.geojson and flows.json satisfy the
flowAt(edgeId, quarterIndex) seam that the web renderer and future AI models
depend on.

These tests are skipped automatically if the output files haven't been built yet.
Run build_data.py first, then: python3 -m pytest tests/ -v
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from build_data import N_QUARTERS

DATA_DIR = Path(__file__).parent.parent / "web" / "data"
GEO_PATH  = DATA_DIR / "network.geojson"
FLOW_PATH = DATA_DIR / "flows.json"

needs_geo  = pytest.mark.skipif(not GEO_PATH.exists(),  reason="network.geojson not built")
needs_flow = pytest.mark.skipif(not FLOW_PATH.exists(), reason="flows.json not built")
needs_both = pytest.mark.skipif(
    not GEO_PATH.exists() or not FLOW_PATH.exists(),
    reason="output files not built",
)


@pytest.fixture(scope="module")
def geojson():
    with open(GEO_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def flows_data():
    with open(FLOW_PATH) as f:
        return json.load(f)


# ── network.geojson ───────────────────────────────────────────────────────────

@needs_geo
def test_geojson_root_type(geojson):
    assert geojson["type"] == "FeatureCollection"
    assert isinstance(geojson["features"], list)
    assert len(geojson["features"]) > 0, "GeoJSON has no features"


@needs_geo
def test_geojson_all_linestrings(geojson):
    for feat in geojson["features"]:
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "LineString", \
            f"Non-LineString feature: {feat['properties'].get('id')}"


@needs_geo
def test_geojson_required_properties(geojson):
    required = {"id", "sensor_id", "bidirectional", "level"}
    for feat in geojson["features"]:
        props = feat["properties"]
        missing = required - props.keys()
        assert not missing, f"Feature {props.get('id')} missing props: {missing}"


@needs_geo
def test_geojson_ids_unique(geojson):
    ids = [f["properties"]["id"] for f in geojson["features"]]
    assert len(ids) == len(set(ids)), "Duplicate edge IDs in network.geojson"


@needs_geo
def test_geojson_coordinates_are_wgs84(geojson):
    for feat in geojson["features"]:
        for lon, lat in feat["geometry"]["coordinates"]:
            assert 10 < lon < 14,  f"Longitude {lon} outside Göteborg range"
            assert 56 < lat < 60,  f"Latitude {lat} outside Göteborg range"


@needs_geo
def test_geojson_sensor_edges_have_known_sensors(geojson):
    expected_sensors = {"107", "133", "134", "1074", "1076", "2276"}
    for feat in geojson["features"]:
        sid = feat["properties"]["sensor_id"]
        if sid is not None:
            assert str(sid) in expected_sensors, \
                f"Unexpected sensor_id {sid!r} in GeoJSON"


@needs_geo
def test_geojson_no_synthetic_edges(geojson):
    """All edges should be real OSM edges — no synthetic geometry."""
    for feat in geojson["features"]:
        assert not feat["properties"].get("synthetic"), \
            f"Synthetic edge found: {feat['properties']['id']} — rebuild should use real OSM edges"


# ── flows.json ────────────────────────────────────────────────────────────────

@needs_flow
def test_flows_top_level_keys(flows_data):
    assert "epoch" in flows_data
    assert "interval_minutes" in flows_data
    assert "flows" in flows_data
    assert flows_data["epoch"] == "2025-01-01T00:00:00"
    assert flows_data["interval_minutes"] == 15


@needs_flow
def test_flows_array_lengths(flows_data):
    for edge_id, arr in flows_data["flows"].items():
        assert len(arr) == N_QUARTERS, \
            f"{edge_id}: expected {N_QUARTERS} slots, got {len(arr)}"


@needs_flow
def test_flows_values_are_int_or_null(flows_data):
    for edge_id, arr in flows_data["flows"].items():
        for qi, v in enumerate(arr):
            assert v is None or (isinstance(v, int) and v >= 0), \
                f"{edge_id}[{qi}] = {v!r} — must be non-negative int or null"


@needs_flow
def test_flows_not_all_null(flows_data):
    """Every edge in flows.json should have at least some real data."""
    for edge_id, arr in flows_data["flows"].items():
        non_null = sum(1 for v in arr if v is not None)
        assert non_null > 0, f"{edge_id}: entire flow array is null"


# ── Cross-file contract ───────────────────────────────────────────────────────

@needs_both
def test_every_flow_edge_exists_in_geojson(geojson, flows_data):
    geo_ids  = {f["properties"]["id"] for f in geojson["features"]}
    flow_ids = set(flows_data["flows"].keys())
    orphans  = flow_ids - geo_ids
    assert not orphans, \
        f"Edges in flows.json with no GeoJSON feature: {orphans}"


@needs_both
def test_every_sensor_edge_in_geojson_has_flows(geojson, flows_data):
    """Any GeoJSON edge that carries a sensor must be in flows.json."""
    flow_ids = set(flows_data["flows"].keys())
    for feat in geojson["features"]:
        props = feat["properties"]
        if props["sensor_id"] is not None:
            assert props["id"] in flow_ids, \
                f"Sensor edge {props['id']} is in GeoJSON but missing from flows.json"


@needs_both
def test_bidirectional_edges_have_consistent_data(geojson, flows_data):
    """Paired fwd/rev edges of a Total sensor must have the same total data fill rate."""
    from collections import defaultdict
    sensor_edges: dict[str, list[str]] = defaultdict(list)
    for feat in geojson["features"]:
        props = feat["properties"]
        if props["sensor_id"] and props["bidirectional"]:
            sensor_edges[props["sensor_id"]].append(props["id"])

    flows = flows_data["flows"]
    for sid, edge_ids in sensor_edges.items():
        if len(edge_ids) < 2:
            continue
        fills = [sum(1 for v in flows[eid] if v is not None)
                 for eid in edge_ids if eid in flows]
        if len(fills) >= 2:
            assert min(fills) == max(fills), \
                f"Sensor {sid}: bidirectional edges have different data fill counts {fills}"
