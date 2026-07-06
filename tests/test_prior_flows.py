"""Unit tests for prior_flows.py's opposite_edge() — resolving the unmeasured
carriageway/direction a level-3 prior is written for. The rest of the module
(main()) needs a trained dirsplit model + real network/flow files and is
exercised only via the live pipeline, matching this project's existing
pattern for data-heavy orchestration scripts."""

import sys
from pathlib import Path

import networkx as nx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from prior_flows import opposite_edge


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
