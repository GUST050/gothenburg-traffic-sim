"""
Unit tests for build_features.py pure functions.

No real files needed — all inputs are synthetic.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from build_data import EPOCH, INTERVAL, N_QUARTERS
from build_features import (
    ADJ_SIGMA_M,
    ADJ_THRESHOLD_M,
    TRAIN_FRAC,
    VAL_FRAC,
    build_adjacency,
    build_flow_matrix,
    build_sensor_meta,
    build_split,
)


# ── Minimal GeoJSON fixtures ───────────────────────────────────────────────────

def _make_geojson(*sensors) -> dict:
    """
    Build a minimal FeatureCollection.
    sensors: list of (sensor_id, level, coords) where coords = [[lon,lat], ...]
    """
    features = []
    for sid, level, coords in sensors:
        edge_id = f"u_{sid}_0"
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "id": edge_id, "sensor_id": sid,
                "level": level, "bidirectional": False,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _make_flows(edge_vals: dict[str, list]) -> dict:
    """edge_vals: { edge_id: [count|null, ...] }"""
    return {"epoch": "2025-01-01T00:00:00", "interval_minutes": 15, "flows": edge_vals}


# ── TestSensorMeta ─────────────────────────────────────────────────────────────

class TestSensorMeta:
    def test_returns_one_row_per_sensor(self):
        geo = _make_geojson(
            ("101", "S",     [[11.98, 57.70], [11.99, 57.71]]),
            ("102", "Total", [[11.97, 57.69], [11.98, 57.70]]),
            ("102", "Total", [[11.96, 57.69], [11.97, 57.70]]),  # second edge, same sensor
        )
        meta = build_sensor_meta(geo)
        assert len(meta) == 2

    def test_required_columns_present(self):
        geo  = _make_geojson(("101", "S", [[11.98, 57.70], [11.99, 57.71]]))
        meta = build_sensor_meta(geo)
        for col in ("sensor_id", "lat", "lon", "level", "primary_edge"):
            assert col in meta.columns

    def test_coordinates_use_endpoint_average(self):
        # Sensor position = average of the two endpoint nodes, NOT the vertex
        # at the middle index.  Long OSM edges have uneven vertex distributions
        # so the middle-indexed vertex can be far from the geographic midpoint.
        coords = [
            [11.980, 57.700],  # endpoint 0
            [11.981, 57.701],  # intermediate vertex — clustered near start
            [11.982, 57.702],  # intermediate vertex — clustered near start
            [11.990, 57.710],  # endpoint 1
        ]
        # middle-indexed vertex = coords[2] = [11.982, 57.702] — WRONG
        # endpoint average = ([11.980+11.990]/2, [57.700+57.710]/2) — CORRECT
        geo  = _make_geojson(("101", "S", coords))
        meta = build_sensor_meta(geo)
        assert meta.iloc[0]["lon"] == pytest.approx((coords[0][0] + coords[-1][0]) / 2, abs=1e-4)
        assert meta.iloc[0]["lat"] == pytest.approx((coords[0][1] + coords[-1][1]) / 2, abs=1e-4)

    def test_sorted_by_sensor_id(self):
        geo  = _make_geojson(
            ("200", "S", [[11.98, 57.70], [11.99, 57.71]]),
            ("100", "S", [[11.97, 57.69], [11.98, 57.70]]),
        )
        meta = build_sensor_meta(geo)
        assert list(meta["sensor_id"]) == ["100", "200"]

    def test_level_preserved(self):
        geo  = _make_geojson(("101", "Total", [[11.98, 57.70], [11.99, 57.71]]))
        meta = build_sensor_meta(geo)
        assert meta.iloc[0]["level"] == "Total"

    def test_primary_edge_id_matches_geojson(self):
        geo  = _make_geojson(("101", "S", [[11.98, 57.70], [11.99, 57.71]]))
        meta = build_sensor_meta(geo)
        assert meta.iloc[0]["primary_edge"] == "u_101_0"

    def test_non_sensor_features_skipped(self):
        geo = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[11.98, 57.70], [11.99, 57.71]]},
                "properties": {"id": "A_B_0", "sensor_id": None, "level": None, "bidirectional": False},
            }],
        }
        meta = build_sensor_meta(geo)
        assert len(meta) == 0


# ── TestFlowMatrix ─────────────────────────────────────────────────────────────

class TestFlowMatrix:
    def _meta(self, sid: str, primary_edge: str) -> pd.DataFrame:
        return pd.DataFrame([{
            "sensor_id": sid, "lat": 57.70, "lon": 11.98,
            "level": "S", "primary_edge": primary_edge,
        }])

    def test_shape(self):
        meta   = self._meta("101", "u_101_0")
        flows  = _make_flows({"u_101_0": [1] * N_QUARTERS})
        matrix = build_flow_matrix(flows, meta)
        assert matrix.shape == (N_QUARTERS, 1)

    def test_datetime_index_starts_at_epoch(self):
        meta   = self._meta("101", "u_101_0")
        flows  = _make_flows({"u_101_0": [0] * N_QUARTERS})
        matrix = build_flow_matrix(flows, meta)
        assert matrix.index[0] == pd.Timestamp(EPOCH)

    def test_datetime_index_15min_freq(self):
        meta   = self._meta("101", "u_101_0")
        flows  = _make_flows({"u_101_0": [0] * N_QUARTERS})
        matrix = build_flow_matrix(flows, meta)
        delta  = matrix.index[1] - matrix.index[0]
        assert delta == pd.Timedelta(minutes=15)

    def test_null_becomes_nan(self):
        meta  = self._meta("101", "u_101_0")
        arr   = [None, 42] + [1] * (N_QUARTERS - 2)
        flows = _make_flows({"u_101_0": arr})
        mat   = build_flow_matrix(flows, meta)
        assert math.isnan(mat.iloc[0, 0])
        assert mat.iloc[1, 0] == 42.0

    def test_missing_edge_gives_all_nan(self):
        meta  = self._meta("101", "u_101_0")
        flows = _make_flows({})   # edge not in flows.json
        mat   = build_flow_matrix(flows, meta)
        assert mat["101"].isna().all()

    def test_column_order_matches_sorted_sensor_ids(self):
        meta = pd.DataFrame([
            {"sensor_id": "200", "lat": 57.70, "lon": 11.98, "level": "S", "primary_edge": "u_200_0"},
            {"sensor_id": "100", "lat": 57.69, "lon": 11.97, "level": "S", "primary_edge": "u_100_0"},
        ])
        flows = _make_flows({
            "u_100_0": [10] * N_QUARTERS,
            "u_200_0": [20] * N_QUARTERS,
        })
        mat = build_flow_matrix(flows, meta)
        assert list(mat.columns) == ["200", "100"]  # matches meta order, not sorted

    def test_two_sensors_are_independent(self):
        meta = pd.DataFrame([
            {"sensor_id": "A", "lat": 57.70, "lon": 11.98, "level": "S", "primary_edge": "e_A"},
            {"sensor_id": "B", "lat": 57.69, "lon": 11.97, "level": "S", "primary_edge": "e_B"},
        ])
        flows = _make_flows({"e_A": [10] * N_QUARTERS, "e_B": [20] * N_QUARTERS})
        mat   = build_flow_matrix(flows, meta)
        assert mat["A"].iloc[0] == 10.0
        assert mat["B"].iloc[0] == 20.0


# ── TestAdjacency ─────────────────────────────────────────────────────────────

class TestAdjacency:
    def _meta_two(self, d_lon: float) -> pd.DataFrame:
        """Two sensors separated by d_lon degrees of longitude."""
        return pd.DataFrame([
            {"sensor_id": "A", "lat": 57.70, "lon": 11.98,          "level": "S", "primary_edge": "e_A"},
            {"sensor_id": "B", "lat": 57.70, "lon": 11.98 + d_lon,  "level": "S", "primary_edge": "e_B"},
        ])

    def test_diagonal_is_zero(self):
        meta = self._meta_two(0.001)
        adj  = build_adjacency(meta)
        assert adj.loc["A", "A"] == 0.0
        assert adj.loc["B", "B"] == 0.0

    def test_symmetric(self):
        meta = self._meta_two(0.005)
        adj  = build_adjacency(meta)
        assert adj.loc["A", "B"] == pytest.approx(adj.loc["B", "A"])

    def test_nearby_sensors_have_positive_weight(self):
        # 0.001° lon ≈ 59 m — well within ADJ_THRESHOLD_M
        meta = self._meta_two(0.001)
        adj  = build_adjacency(meta)
        assert adj.loc["A", "B"] > 0

    def test_far_sensors_have_zero_weight(self):
        # 0.05° lon ≈ 2967 m — beyond 2000 m threshold
        meta = self._meta_two(0.05)
        adj  = build_adjacency(meta)
        assert adj.loc["A", "B"] == 0.0

    def test_values_in_range(self):
        meta = self._meta_two(0.005)
        adj  = build_adjacency(meta)
        vals = adj.to_numpy()
        assert vals.min() >= 0.0
        assert vals.max() <= 1.0

    def test_closer_pair_has_higher_weight(self):
        meta_close = self._meta_two(0.001)
        meta_far   = self._meta_two(0.010)
        adj_close  = build_adjacency(meta_close)
        adj_far    = build_adjacency(meta_far)
        assert adj_close.loc["A", "B"] > adj_far.loc["A", "B"]

    def test_single_sensor_returns_zero_matrix(self):
        meta = pd.DataFrame([
            {"sensor_id": "A", "lat": 57.70, "lon": 11.98, "level": "S", "primary_edge": "e_A"},
        ])
        adj = build_adjacency(meta)
        assert adj.shape == (1, 1)
        assert adj.iloc[0, 0] == 0.0


# ── TestSplit ──────────────────────────────────────────────────────────────────

class TestSplit:
    def test_no_gaps_between_splits(self):
        s = build_split(N_QUARTERS)
        assert s["val"]["qi_start"]  == s["train"]["qi_end"]  + 1
        assert s["test"]["qi_start"] == s["val"]["qi_end"]    + 1

    def test_covers_all_quarters(self):
        s = build_split(N_QUARTERS)
        total = sum(v["n_quarters"] for v in s.values())
        assert total == N_QUARTERS

    def test_train_is_largest(self):
        s = build_split(N_QUARTERS)
        assert s["train"]["n_quarters"] > s["val"]["n_quarters"]
        assert s["train"]["n_quarters"] > s["test"]["n_quarters"]

    def test_chronological_order(self):
        s = build_split(N_QUARTERS)
        assert s["train"]["qi_end"] < s["val"]["qi_start"]
        assert s["val"]["qi_end"]   < s["test"]["qi_start"]

    def test_train_fraction_approx(self):
        s       = build_split(N_QUARTERS)
        actual  = s["train"]["n_quarters"] / N_QUARTERS
        assert actual == pytest.approx(TRAIN_FRAC, abs=0.01)

    def test_datetime_strings_parseable(self):
        s = build_split(N_QUARTERS)
        for split in s.values():
            pd.Timestamp(split["dt_start"])
            pd.Timestamp(split["dt_end"])

    def test_test_ends_at_last_quarter(self):
        s = build_split(N_QUARTERS)
        assert s["test"]["qi_end"] == N_QUARTERS - 1
