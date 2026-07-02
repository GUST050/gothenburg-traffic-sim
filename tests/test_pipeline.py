"""
Unit tests for build_data.py pure functions.

No OSM downloads, no real CSV files — all inputs are synthetic.
Run:  python3 -m pytest tests/ -v
"""

import math
import numbers
import sys
from pathlib import Path

import networkx as nx
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from build_data import (
    CARRIAGEWAY_M,
    EPOCH,
    INTERVAL,
    MANUAL_SNAPS,
    N_QUARTERS,
    _bidir_edges,
    _find_second_edge,
    bearing_rad,
    build_flows,
    build_qi_series,
    find_antiparallel_edge,
    haversine_m,
)


# ── Shared graph fixtures ──────────────────────────────────────────────────────

def make_divided_road() -> nx.MultiDiGraph:
    """
    Two parallel north-south carriageways (~18 m apart) plus one cross street.

    Nodes (lat=y, lon=x):
      1: 57.7000, 11.9850  — south, east carriageway
      2: 57.7020, 11.9850  — north, east carriageway
      3: 57.7020, 11.9847  — north, west carriageway
      4: 57.7000, 11.9847  — south, west carriageway
      5: 57.7010, 11.9860  — east end of cross street

    Edges:
      1→2  northbound (east carriageway)
      3→4  southbound (west carriageway, ~18 m west)
      2→5  cross street going east (should be ignored by antiparallel search)
    """
    G = nx.MultiDiGraph()
    G.add_node(1, y=57.7000, x=11.9850)
    G.add_node(2, y=57.7020, x=11.9850)
    G.add_node(3, y=57.7020, x=11.9847)
    G.add_node(4, y=57.7000, x=11.9847)
    G.add_node(5, y=57.7010, x=11.9860)
    G.add_edge(1, 2, key=0, length=222)   # northbound
    G.add_edge(3, 4, key=0, length=222)   # southbound
    G.add_edge(2, 5, key=0, length=100)   # cross street (eastbound)
    return G


def make_two_way_road() -> nx.MultiDiGraph:
    """Simple two-way road: OSM has both directed edges on the same way."""
    G = nx.MultiDiGraph()
    G.add_node(1, y=57.700, x=11.985)
    G.add_node(2, y=57.702, x=11.985)
    G.add_edge(1, 2, key=0, length=222)
    G.add_edge(2, 1, key=0, length=222)
    return G


def make_one_way_road() -> nx.MultiDiGraph:
    """Single directed edge — no reverse, no parallel opposite."""
    G = nx.MultiDiGraph()
    G.add_node(1, y=57.700, x=11.985)
    G.add_node(2, y=57.702, x=11.985)
    G.add_edge(1, 2, key=0, length=222)
    return G


# ── Quarter index ──────────────────────────────────────────────────────────────

class TestQI:
    def _qi(self, ts) -> object:
        return build_qi_series(pd.Series([ts])).iloc[0]

    def test_epoch_is_zero(self):
        assert self._qi(EPOCH) == 0

    def test_second_slot(self):
        assert self._qi(EPOCH + INTERVAL) == 1

    def test_last_slot(self):
        assert self._qi(EPOCH + INTERVAL * (N_QUARTERS - 1)) == N_QUARTERS - 1

    def test_one_past_last_is_none(self):
        assert self._qi(EPOCH + INTERVAL * N_QUARTERS) is None

    def test_before_epoch_is_none(self):
        assert self._qi(EPOCH - pd.Timedelta(seconds=1)) is None

    def test_nat_is_none(self):
        assert self._qi(pd.NaT) is None

    def test_vectorised_preserves_order(self):
        ts  = pd.Series([EPOCH + INTERVAL * i for i in range(10)])
        qis = build_qi_series(ts).tolist()
        assert qis == list(range(10))

    def test_n_quarters_covers_full_year(self):
        assert N_QUARTERS == 365 * 96   # 2025 is not a leap year

    def test_qi_is_integer_type(self):
        result = self._qi(EPOCH)
        assert result == 0
        assert isinstance(result, numbers.Integral)


# ── Flow building ──────────────────────────────────────────────────────────────

class TestBuildFlows:
    def _raw(self, sensor_id: str, qi: int, count) -> pd.DataFrame:
        return pd.DataFrame({
            "matplats": [sensor_id],
            "ts":       [EPOCH + INTERVAL * qi],
            "count":    [count],
        })

    def test_value_placed_at_correct_slot(self):
        flows = build_flows(self._raw("101", 0, 42), {"edge_A": "101"})
        assert flows["edge_A"][0] == 42

    def test_all_other_slots_are_none(self):
        flows = build_flows(self._raw("101", 5, 10), {"edge_A": "101"})
        assert all(v is None for i, v in enumerate(flows["edge_A"]) if i != 5)

    def test_missing_count_is_none_not_zero(self):
        flows = build_flows(self._raw("101", 0, float("nan")), {"edge_A": "101"})
        assert flows["edge_A"][0] is None

    def test_total_sensor_writes_same_value_to_both_edges(self):
        edge_sensor = {"fwd": "101", "rev": "101"}
        flows = build_flows(self._raw("101", 3, 77), edge_sensor)
        assert flows["fwd"][3] == 77
        assert flows["rev"][3] == 77

    def test_unknown_sensor_is_silently_ignored(self):
        flows = build_flows(self._raw("999", 0, 50), {"edge_A": "101"})
        assert all(v is None for v in flows["edge_A"])

    def test_output_array_length_is_n_quarters(self):
        flows = build_flows(self._raw("101", 0, 1), {"edge_A": "101"})
        assert len(flows["edge_A"]) == N_QUARTERS

    def test_two_sensors_are_independent(self):
        raw = pd.DataFrame({
            "matplats": ["101", "102"],
            "ts":       [EPOCH, EPOCH + INTERVAL],
            "count":    [10, 20],
        })
        flows = build_flows(raw, {"edge_A": "101", "edge_B": "102"})
        assert flows["edge_A"][0] == 10
        assert flows["edge_A"][1] is None
        assert flows["edge_B"][0] is None
        assert flows["edge_B"][1] == 20


# ── Haversine ──────────────────────────────────────────────────────────────────

class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_m(57.7, 11.9, 57.7, 11.9) == pytest.approx(0, abs=0.01)

    def test_one_degree_latitude(self):
        assert 110_900 < haversine_m(0, 0, 1, 0) < 111_500

    def test_one_degree_longitude_at_equator(self):
        assert 110_900 < haversine_m(0, 0, 0, 1) < 111_500

    def test_gothenburg_corridor(self):
        d = haversine_m(57.6956, 11.9839, 57.6966, 11.9890)
        assert 250 < d < 600

    def test_symmetric(self):
        d1 = haversine_m(57.6, 11.9, 57.7, 12.0)
        d2 = haversine_m(57.7, 12.0, 57.6, 11.9)
        assert d1 == pytest.approx(d2, rel=1e-9)


# ── Bearing ────────────────────────────────────────────────────────────────────

class TestBearing:
    def test_north_is_zero(self):
        assert bearing_rad(0, 0, 1, 0) == pytest.approx(0)

    def test_south_is_pi(self):
        assert abs(bearing_rad(1, 0, 0, 0)) == pytest.approx(math.pi)

    def test_east_is_half_pi(self):
        assert bearing_rad(0, 0, 0, 1) == pytest.approx(math.pi / 2)

    def test_west_is_negative_half_pi(self):
        assert bearing_rad(0, 1, 0, 0) == pytest.approx(-math.pi / 2)

    def test_north_and_south_are_antiparallel(self):
        b_north = bearing_rad(0, 0, 1, 0)
        b_south = bearing_rad(1, 0, 0, 0)
        diff = abs(b_north - b_south)
        assert diff == pytest.approx(math.pi)

    def test_east_and_west_are_antiparallel(self):
        b_east = bearing_rad(0, 0, 0, 1)
        b_west = bearing_rad(0, 1, 0, 0)
        diff = abs(b_east - b_west)
        assert diff == pytest.approx(math.pi)


# ── Antiparallel edge search ───────────────────────────────────────────────────

class TestFindAntiparallel:
    """
    Uses make_divided_road(): northbound edge 1→2 on the east carriageway,
    southbound 3→4 on the west carriageway ~18 m away.
    Sensor sits between them at (57.7010, 11.98485).
    """

    def test_finds_southbound_carriageway(self):
        G      = make_divided_road()
        result = find_antiparallel_edge(G, 1, 2, 0, 57.7010, 11.98485)
        assert result == (3, 4, 0)

    def test_ignores_cross_street(self):
        G      = make_divided_road()
        result = find_antiparallel_edge(G, 1, 2, 0, 57.7010, 11.98485)
        assert result != (2, 5, 0)

    def test_returns_none_when_threshold_too_tight(self):
        G      = make_divided_road()
        # Carriageway is ~9 m from sensor; threshold of 5 m cannot reach it
        result = find_antiparallel_edge(G, 1, 2, 0, 57.7010, 11.98485, max_dist_m=5)
        assert result is None

    def test_returns_none_on_single_road(self):
        G      = make_one_way_road()
        result = find_antiparallel_edge(G, 1, 2, 0, 57.701, 11.985)
        assert result is None

    def test_does_not_return_the_queried_edge_itself(self):
        G      = make_two_way_road()
        result = find_antiparallel_edge(G, 1, 2, 0, 57.701, 11.985)
        assert result != (1, 2, 0)

    def test_angle_threshold_filters_perpendicular_roads(self):
        # A road going east should not be returned as antiparallel to a northbound road
        G = nx.MultiDiGraph()
        G.add_node(1, y=57.700, x=11.985)
        G.add_node(2, y=57.702, x=11.985)   # northbound destination
        G.add_node(3, y=57.701, x=11.984)   # west end of perpendicular road
        G.add_node(4, y=57.701, x=11.986)   # east end of perpendicular road
        G.add_edge(1, 2, key=0, length=222)  # northbound
        G.add_edge(3, 4, key=0, length=100)  # eastbound — perpendicular, must be ignored
        result = find_antiparallel_edge(G, 1, 2, 0, 57.701, 11.985, max_dist_m=200)
        assert result is None


# ── Second-edge resolution ─────────────────────────────────────────────────────

class TestFindSecondEdge:
    def test_uses_osm_reverse_when_available(self):
        G   = make_two_way_road()
        rev = _find_second_edge(G, 1, 2, 0, "101", 57.701, 11.985)
        assert rev == "2_1_0"

    def test_finds_opposite_carriageway_when_no_osm_reverse(self):
        G   = make_divided_road()
        rev = _find_second_edge(G, 1, 2, 0, "101", 57.7010, 11.98485)
        assert rev == "3_4_0"

    def test_raises_when_no_opposite_found(self):
        G = make_one_way_road()
        with pytest.raises(ValueError, match="no reverse or antiparallel"):
            _find_second_edge(G, 1, 2, 0, "101", 57.701, 11.985)

    def test_osm_reverse_takes_priority_over_carriageway(self):
        # If G.has_edge(v, u) is True, we use it even when an antiparallel also exists
        G = make_divided_road()
        G.add_edge(2, 1, key=0, length=222)   # add explicit OSM reverse to northbound road
        rev = _find_second_edge(G, 1, 2, 0, "101", 57.7010, 11.98485)
        assert rev == "2_1_0"


# ── Bidirectional edge set ─────────────────────────────────────────────────────

class TestBidirEdges:
    def test_sensor_with_two_edges_is_bidirectional(self):
        edge_sensor = {"A_B_0": "101", "B_A_0": "101"}
        bidir = _bidir_edges(edge_sensor)
        assert "A_B_0" in bidir
        assert "B_A_0" in bidir

    def test_sensor_with_one_edge_is_not_bidirectional(self):
        edge_sensor = {"A_B_0": "101"}
        bidir = _bidir_edges(edge_sensor)
        assert "A_B_0" not in bidir

    def test_two_sensors_handled_independently(self):
        edge_sensor = {
            "A_B_0": "101",  # sensor 101 — one edge only
            "C_D_0": "102",  # sensor 102 — two edges
            "D_C_0": "102",
        }
        bidir = _bidir_edges(edge_sensor)
        assert "A_B_0" not in bidir
        assert "C_D_0" in bidir
        assert "D_C_0" in bidir

    def test_empty_input(self):
        assert _bidir_edges({}) == set()


# ── MANUAL_SNAPS structure ─────────────────────────────────────────────────────

class TestManualSnaps:
    def test_all_values_are_plain_strings(self):
        for sensor_id, snap in MANUAL_SNAPS.items():
            assert isinstance(snap, str), \
                f"MANUAL_SNAPS[{sensor_id!r}] must be a plain edge-id string, not {type(snap)}"

    def test_all_edge_ids_have_valid_format(self):
        for sensor_id, snap in MANUAL_SNAPS.items():
            parts = snap.split("_")
            assert len(parts) == 3, \
                f"MANUAL_SNAPS[{sensor_id!r}] = {snap!r}: expected u_v_k format"
            assert all(p.isdigit() for p in parts), \
                f"MANUAL_SNAPS[{sensor_id!r}] = {snap!r}: all parts must be numeric"
