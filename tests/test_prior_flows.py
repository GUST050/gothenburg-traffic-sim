"""Unit tests for prior_flows.py's opposite_edge() — resolving the unmeasured
carriageway/direction a level-3 prior is written for. The rest of the module
(main()) needs a trained dirsplit model + real network/flow files and is
exercised only via the live pipeline, matching this project's existing
pattern for data-heavy orchestration scripts."""

import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from prior_flows import measured_edge_shares, opposite_edge


def node(lat, lon):
    return {"y": lat, "x": lon}


class TestOppositeEdgeSameWay:
    def test_simple_twoway_reverse_edge(self):
        """A single OSM way with edges in both directions (1->2 and 2->1) —
        the cheap G.has_edge(v, u) path, no distance search needed."""
        G = nx.MultiDiGraph()
        G.add_node(1, **node(57.700, 11.900))
        G.add_node(2, **node(57.701, 11.900))
        G.add_edge(1, 2, key=0)
        G.add_edge(2, 1, key=0)
        assert opposite_edge(G, "1_2_0", 57.7005, 11.900) == "2_1_0"

    def test_picks_lowest_key_when_multiple_reverse_edges(self):
        G = nx.MultiDiGraph()
        G.add_node(1, **node(57.700, 11.900))
        G.add_node(2, **node(57.701, 11.900))
        G.add_edge(1, 2, key=0)
        G.add_edge(2, 1, key=0)
        G.add_edge(2, 1, key=1)
        assert opposite_edge(G, "1_2_0", 57.7005, 11.900) == "2_1_0"


class TestOppositeEdgeDividedCarriageway:
    def test_falls_back_to_nearby_antiparallel_edge(self):
        """No direct reverse edge — a separate, nearby, oppositely-bearing
        edge (the other carriageway of a divided road) must be found."""
        G = nx.MultiDiGraph()
        G.add_node(1, **node(57.700, 11.900))
        G.add_node(2, **node(57.701, 11.900))      # edge 1->2 bears ~north
        G.add_node(3, **node(57.701, 11.9002))
        G.add_node(4, **node(57.700, 11.9002))      # edge 3->4 bears ~south
        G.add_edge(1, 2, key=0)
        G.add_edge(3, 4, key=0)
        sensor_lat, sensor_lon = 57.7005, 11.900
        assert opposite_edge(G, "1_2_0", sensor_lat, sensor_lon) == "3_4_0"

    def test_no_candidate_within_range_returns_none(self):
        """A one-way street with nothing running the other way nearby."""
        G = nx.MultiDiGraph()
        G.add_node(1, **node(57.700, 11.900))
        G.add_node(2, **node(57.701, 11.900))
        G.add_edge(1, 2, key=0)
        assert opposite_edge(G, "1_2_0", 57.7005, 11.900) is None

    def test_far_away_antiparallel_edge_is_ignored(self):
        """An oppositely-bearing edge that exists but is too far away (not
        the same physical carriageway) must not be matched."""
        G = nx.MultiDiGraph()
        G.add_node(1, **node(57.700, 11.900))
        G.add_node(2, **node(57.701, 11.900))
        G.add_node(3, **node(57.701, 12.100))   # ~12 km east — a different street
        G.add_node(4, **node(57.700, 12.100))
        G.add_edge(1, 2, key=0)
        G.add_edge(3, 4, key=0)
        assert opposite_edge(G, "1_2_0", 57.7005, 11.900) is None


class TestMeasuredEdgeShares:
    """The dirsplit model only ever predicts on the toward-centre edge of a
    pair (train.py trains on radial_cos > 0 rows exclusively) — e_meas's own
    share must be re-derived by orientation, not assumed to equal the raw
    model output. Found 2026-07-06: for 4 of 5 single-direction Gothenburg
    sensors, e_meas itself points AWAY from centre."""

    def test_measured_edge_is_toward_centre_passthrough(self):
        q10, q50, q90 = np.array([0.4]), np.array([0.5]), np.array([0.6])
        r10, r50, r90 = measured_edge_shares(0.6, -0.6, q10, q50, q90)
        assert r10 is q10 and r50 is q50 and r90 is q90

    def test_measured_edge_away_from_centre_complements_and_swaps_band(self):
        # canonical (toward-centre) edge's predicted share is 0.3-0.4-0.7
        q10, q50, q90 = np.array([0.3]), np.array([0.4]), np.array([0.7])
        r10, r50, r90 = measured_edge_shares(-0.6, 0.6, q10, q50, q90)
        # e_meas's own share is the complement, with q10/q90 swapped
        assert r10 == pytest.approx(1 - 0.7)
        assert r50 == pytest.approx(1 - 0.4)
        assert r90 == pytest.approx(1 - 0.3)

    def test_tie_treated_as_measured_toward_centre(self):
        q10, q50, q90 = np.array([0.4]), np.array([0.5]), np.array([0.6])
        r10, r50, r90 = measured_edge_shares(0.0, 0.0, q10, q50, q90)
        assert r10 is q10 and r50 is q50 and r90 is q90
