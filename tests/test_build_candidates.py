"""Unit tests for build_candidates.py's pure/testable pieces — synthetic
tiny graphs and temp files only, no real network/DeSO/OSM data needed.

Covers this session's U-turn fix directly: upstream_downstream_gates() and
drop_uturn_routes() are the two mechanisms that eliminated the literal
edge-then-its-reverse pattern verified in sumo/candidates.rou.xml (see
git history) — these tests pin that behaviour so it can't silently regress."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import networkx as nx
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import build_candidates as bc


def node(lat, lon):
    return {"y": lat, "x": lon}


def write_routes(path, vehicles):
    """vehicles: list of (id, list-of-edge-ids)."""
    root = ET.Element("routes")
    for vid, edges in vehicles:
        veh = ET.SubElement(root, "vehicle", id=vid, depart="0.0")
        ET.SubElement(veh, "route", edges=" ".join(edges))
    ET.ElementTree(root).write(path)


def read_vehicle_ids(path):
    return [veh.get("id") for veh in ET.parse(path).getroot().iter("vehicle")]


class TestReverseEdgeId:
    def test_swaps_endpoints_keeps_key(self):
        assert bc.reverse_edge_id("100_200_0") == "200_100_0"

    def test_double_reverse_is_identity(self):
        eid = "12345_6789_1"
        assert bc.reverse_edge_id(bc.reverse_edge_id(eid)) == eid


class TestDropUturnRoutes:
    def test_route_with_immediate_reversal_is_dropped(self, tmp_path):
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [
            ("clean", ["1_2_0", "2_3_0", "3_4_0"]),
            ("uturn", ["1_2_0", "2_1_0", "1_5_0"]),   # 2_1_0 reverses 1_2_0
        ])
        bc.drop_uturn_routes(path)
        assert read_vehicle_ids(path) == ["clean"]

    def test_no_uturns_leaves_file_untouched(self, tmp_path):
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("a", ["1_2_0", "2_3_0"]), ("b", ["5_6_0"])])
        mtime_before = path.stat().st_mtime_ns
        bc.drop_uturn_routes(path)
        assert read_vehicle_ids(path) == ["a", "b"]
        assert path.stat().st_mtime_ns == mtime_before   # write() skipped when dropped==0

    def test_uturn_deep_inside_a_long_route_is_still_caught(self, tmp_path):
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [
            ("longuturn", ["1_2_0", "2_3_0", "3_4_0", "4_3_0", "3_9_0"]),
        ])
        bc.drop_uturn_routes(path)
        assert read_vehicle_ids(path) == []


class TestUpstreamDownstreamGates:
    def make_via_edge_graph(self):
        """Sensor edge 10->11 bears due north. Two candidate entry gates
        (one behind it to the south — a genuine upstream approach, one
        beyond it to the north — already past the edge, the wrong side for
        an entry) and two candidate exit gates (one ahead to the north, one
        behind to the south)."""
        G = nx.MultiDiGraph()
        G.add_node(10, **node(57.700, 11.900))
        G.add_node(11, **node(57.701, 11.900))     # 10->11: due north
        G.add_edge(10, 11, key=0)

        G.add_node(1, **node(57.698, 11.900))        # south of the edge: behind
        G.add_node(2, **node(57.705, 11.900))        # north of the edge: already
                                                        # past it, wrong side
        entries = [("1_10_0", 1), ("2_10_0", 2)]

        G.add_node(20, **node(57.703, 11.900))       # north of the edge: ahead
        G.add_node(21, **node(57.698, 11.900))       # south of the edge: behind
        exits = [("11_20_0", 20), ("11_21_0", 21)]
        return G, entries, exits

    def test_entry_gate_behind_the_edge_is_kept(self):
        G, entries, exits = self.make_via_edge_graph()
        ins, _ = bc.upstream_downstream_gates(G, "10_11_0", entries, exits)
        assert "1_10_0" in ins

    def test_entry_gate_on_the_wrong_side_is_excluded(self):
        G, entries, exits = self.make_via_edge_graph()
        ins, _ = bc.upstream_downstream_gates(G, "10_11_0", entries, exits)
        assert "2_10_0" not in ins

    def test_exit_gate_ahead_of_the_edge_is_kept(self):
        G, entries, exits = self.make_via_edge_graph()
        _, outs = bc.upstream_downstream_gates(G, "10_11_0", entries, exits)
        assert "11_20_0" in outs

    def test_exit_gate_behind_the_edge_is_excluded(self):
        G, entries, exits = self.make_via_edge_graph()
        _, outs = bc.upstream_downstream_gates(G, "10_11_0", entries, exits)
        assert "11_21_0" not in outs

    def test_falls_back_to_full_pool_when_nothing_matches(self):
        """If every gate happens to be on the wrong side, degrade to the
        unrestricted pool rather than leaving a sensor edge with zero
        via-trip gates."""
        G = nx.MultiDiGraph()
        G.add_node(10, **node(57.700, 11.900))
        G.add_node(11, **node(57.701, 11.900))
        G.add_edge(10, 11, key=0)
        G.add_node(2, **node(57.705, 11.900))   # north of the edge — wrong side
        entries = [("2_10_0", 2)]               # no valid "behind" gate at all
        ins, _ = bc.upstream_downstream_gates(G, "10_11_0", entries, [])
        assert ins == ["2_10_0"]   # fallback: the full (unfiltered) entry list


class TestFindGates:
    def test_entry_and_exit_gates_by_degree(self):
        G = nx.MultiDiGraph()
        # 1 -> 2 -> 3: node 1 has no predecessor (entry gate on 1->2),
        # node 3 has no successor (exit gate on 2->3).
        G.add_edge(1, 2, key=0)
        G.add_edge(2, 3, key=0)
        entries, exits = bc.find_gates(G)
        assert entries == [("1_2_0", 1)]
        assert exits == [("2_3_0", 3)]

    def test_interior_edge_is_neither_entry_nor_exit(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0)
        G.add_edge(2, 3, key=0)
        G.add_edge(3, 1, key=0)   # closes the loop: every node has in+out
        entries, exits = bc.find_gates(G)
        assert entries == []
        assert exits == []


class TestGateWeights:
    def test_motorway_outweighs_residential(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, highway="motorway")
        G.add_edge(3, 4, key=0, highway="residential")
        w = bc.gate_weights(G, [("1_2_0", 1), ("3_4_0", 3)])
        assert w[0] > w[1]

    def test_weights_sum_to_one(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, highway="primary")
        G.add_edge(3, 4, key=0, highway="tertiary")
        w = bc.gate_weights(G, [("1_2_0", 1), ("3_4_0", 3)])
        assert w.sum() == pytest.approx(1.0)

    def test_unknown_highway_type_gets_default_weight_one(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, highway="cycleway")   # not in GATE_WEIGHT
        w = bc.gate_weights(G, [("1_2_0", 1)])
        assert w[0] == pytest.approx(1.0)


class TestDailyShape:
    def test_normalizes_to_one_and_matches_peak_hour(self, tmp_path, monkeypatch):
        profiles = {
            "edgeA": {"weekday": [0.0] * 96},
            "edgeB": {"weekday": [0.0] * 96},
        }
        # Put all traffic for both edges in hour 8 (slots 32-35).
        for e in profiles.values():
            for i in range(32, 36):
                e["weekday"][i] = 10.0
        (tmp_path / "web" / "data").mkdir(parents=True)
        import json
        (tmp_path / "web" / "data" / "normal_profile.json").write_text(
            json.dumps({"profiles": profiles}))

        monkeypatch.chdir(tmp_path)
        shape = bc.daily_shape()
        assert shape.sum() == pytest.approx(1.0)
        assert shape[8] == pytest.approx(1.0)
        assert shape[7] == pytest.approx(0.0)


class TestHomeMass:
    def test_population_distributed_by_residential_street_length(self, monkeypatch):
        """Two residential edges in one DeSO zone split its population
        proportionally to their length; a non-residential edge in the same
        zone gets none."""
        zones = [{
            "properties": {"desokod": "Z1"},
            "geometry": {"type": "Polygon",
                        "coordinates": [[[11.0, 57.0], [12.0, 57.0],
                                       [12.0, 58.0], [11.0, 58.0], [11.0, 57.0]]]},
        }]
        pop = {"Z1": 900}
        monkeypatch.setattr(bc, "ensure_deso", lambda: (zones, pop))

        edges = [
            {"id": "a", "lat": 57.5, "lon": 11.5, "hw": "residential", "len": 100.0},
            {"id": "b", "lat": 57.5, "lon": 11.5, "hw": "residential", "len": 200.0},
            {"id": "c", "lat": 57.5, "lon": 11.5, "hw": "primary", "len": 500.0},
        ]
        mass = bc.home_mass(edges)
        assert mass[2] == 0.0                       # non-residential: no home mass
        assert mass[0] + mass[1] == pytest.approx(900.0)
        assert mass[1] == pytest.approx(2 * mass[0])  # proportional to length

    def test_edge_outside_any_zone_gets_no_mass(self, monkeypatch):
        zones = [{
            "properties": {"desokod": "Z1"},
            "geometry": {"type": "Polygon",
                        "coordinates": [[[11.0, 57.0], [12.0, 57.0],
                                       [12.0, 58.0], [11.0, 58.0], [11.0, 57.0]]]},
        }]
        pop = {"Z1": 500}
        monkeypatch.setattr(bc, "ensure_deso", lambda: (zones, pop))
        edges = [{"id": "far", "lat": 0.0, "lon": 0.0, "hw": "residential", "len": 100.0}]
        mass = bc.home_mass(edges)
        assert mass[0] == 0.0
