"""Unit tests for build_candidates.py's pure/testable pieces — synthetic
tiny graphs and temp files only, no real network/DeSO/OSM data needed.

Covers this session's U-turn fix directly: upstream_downstream_gates() and
drop_uturn_routes() are the two mechanisms that eliminated the literal
edge-then-its-reverse pattern verified in sumo/candidates.rou.xml (see
git history) — these tests pin that behaviour so it can't silently regress."""

import json
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


def test_generate_day_block_reuses_geometry_but_resamples_each_days_departures(monkeypatch):
    calls = []

    def fake_generate(rng, *args, **kwargs):
        calls.append(1)
        return [(100.0, "from", "to", "via")], [1.0], {},

    monkeypatch.setattr(bc, "generate_sensor_anchored_trips", fake_generate)
    structure = bc.CandidateStructure(
        G=None, edges=[], hmass=np.array([]), amass={}, entries=[], exits=[],
        entry_ids=[], exit_ids=[], w_entry=np.array([]), w_exit=np.array([]), measured=[])
    profile = np.zeros(24); profile[3] = 1.0
    first, _, _, templates = bc.generate_day_block(
        structure, profile, 0, "d0_", 42, 0, 1, .5, .3, 2.6, False, 1)
    second, lengths, _, _ = bc.generate_day_block(
        structure, profile, 86400, "d1_", 42, 1, 1, .5, .3, 2.6, False, 1,
        template_trips=templates)
    assert calls == [1]
    assert first[0][0] == "d0_0" and 10800 <= first[0][1] < 14400
    assert second[0][0] == "d1_0" and 97200 <= second[0][1] < 100800
    assert lengths == []


def test_reused_purpose_templates_sample_hours_conditional_on_purpose():
    structure = bc.CandidateStructure(
        G=None, edges=[], hmass=np.array([]), amass={}, entries=[], exits=[],
        entry_ids=[], exit_ids=[], w_entry=np.array([]), w_exit=np.array([]), measured=[])
    # The profile treats 07:00 and 20:00 equally. Weekday work-purpose prior
    # strongly favours 07:00, so conditional resampling must retain that.
    profile = np.zeros(24); profile[7] = profile[20] = 0.5
    templates = [("from", "to", "via", "arbete", f"t{i}", "outbound", 7)
                 for i in range(2000)]

    block, _, _, _ = bc.generate_day_block(
        structure, profile, 0, "d0_", 42, 0, len(templates), .5, .3, 2.6,
        False, 1, template_trips=templates)
    hours = [int(trip[1] // 3600) for trip in block]

    assert hours.count(7) > 2 * hours.count(20)


def test_reused_paired_tour_keeps_return_after_outbound():
    structure = bc.CandidateStructure(
        G=None, edges=[], hmass=np.array([]), amass={}, entries=[], exits=[],
        entry_ids=[], exit_ids=[], w_entry=np.array([]), w_exit=np.array([]), measured=[])
    # Both hours are available. The paired sampler must still keep the
    # activity->home return after the home->activity outbound leg.
    profile = np.zeros(24); profile[7] = profile[18] = 0.5
    templates = [
        ("home", "activity", "via", "arbete", "ii-1", "outbound"),
        ("activity", "home", "via", "arbete", "ii-1", "return"),
    ]

    block, _, _, _ = bc.generate_day_block(
        structure, profile, 0, "d0_", 42, 0, len(templates), .5, .3, 2.6,
        False, 1, template_trips=templates)
    by_leg = {trip[7]: int(trip[1] // 3600) for trip in block}

    assert by_leg["return"] >= by_leg["outbound"]
    assert by_leg["return"] >= 12


class TestGateWeights:
    """Gate draws must follow expected approach flow (2026-07-17): the
    road-class-only proxy gave the busiest gate 0.37% of draws while
    calibration assigned it ~20% of quarter flow, leaving its main corridor
    ONE pool shape that the PFE stacked 1 486 vehicles/day onto."""

    def _graph(self):
        G = nx.MultiDiGraph()
        for n in (1, 2, 3, 4):
            G.add_node(n, y=57.7, x=11.97 + n * 0.001)
        G.add_edge(1, 2, key=0, highway="motorway")
        G.add_edge(2, 3, key=0, highway="motorway")
        G.add_edge(3, 4, key=0, highway="residential")
        return G

    GATES = [("1_2_0", 2), ("2_3_0", 3), ("3_4_0", 4)]

    def test_structural_load_drives_the_weights(self):
        # Two same-class motorway gates with very different structural
        # loads must no longer draw equally.
        w = bc.gate_weights(self._graph(), self.GATES,
                            {"1_2_0": 9000.0, "2_3_0": 450.0, "3_4_0": 550.0})
        assert w[0] > 5 * w[1]
        assert w.sum() == pytest.approx(1.0)

    def test_uncovered_gate_keeps_a_floor_share(self):
        w = bc.gate_weights(self._graph(), self.GATES,
                            {"1_2_0": 9000.0, "2_3_0": 1000.0})
        assert w[2] > 0   # unmapped gate stays drawable

    def test_sparse_field_falls_back_to_road_class(self):
        # Only 1 of 3 gates covered — below the coverage threshold.
        w = bc.gate_weights(self._graph(), self.GATES, {"1_2_0": 9000.0})
        w_class = bc.gate_weights(self._graph(), self.GATES, None)
        assert np.allclose(w, w_class)
        # class prior: motorway 8, motorway 8, residential default 1
        assert w_class[0] == pytest.approx(8 / 17)

    def test_missing_artifact_returns_empty_loads(self, tmp_path):
        assert bc.load_assignment_gate_loads(tmp_path / "absent.json") == {}
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert bc.load_assignment_gate_loads(bad) == {}

    def test_loads_sum_quarter_series_and_skip_nonpositive(self, tmp_path):
        p = tmp_path / "assignment_priors.json"
        p.write_text(json.dumps({"flows": {
            "a": [1.0, 2.0, 3.0], "b": 0.0, "c": [0, 0], "d": "junk"}}))
        assert bc.load_assignment_gate_loads(p) == {"a": 6.0}



class TestReverseEdgeId:
    def test_swaps_endpoints_keeps_key(self):
        assert bc.reverse_edge_id("100_200_0") == "200_100_0"

    def test_double_reverse_is_identity(self):
        eid = "12345_6789_1"
        assert bc.reverse_edge_id(bc.reverse_edge_id(eid)) == eid


class TestCandidateEndpointIntegrity:
    def test_load_sumo_routing_data_uses_lane_travel_time(self, tmp_path):
        net = tmp_path / "net.net.xml"
        net.write_text(
            '<net><edge id="1_2_0"><lane id="1_2_0_0" length="100" '
            'speed="10"/></edge><edge id=":internal" function="internal">'
            '<lane id=":internal_0" length="1" speed="1"/></edge></net>')

        edge_ids, costs = bc.load_sumo_routing_data(net)

        assert edge_ids == {"1_2_0"}
        assert costs == {"1_2_0": pytest.approx(10.0)}

    def test_load_graph_edges_skips_self_loops_and_non_sumo_edges(self):
        G = nx.MultiDiGraph()
        G.add_node(1, x=11.0, y=57.0)
        G.add_node(2, x=11.001, y=57.001)
        G.add_edge(1, 1, key=0, highway="residential", length=0.0)
        G.add_edge(1, 2, key=0, highway="residential", length=100.0)
        G.add_edge(2, 1, key=1, highway="tertiary", length=110.0)

        edges = bc.load_graph_edges(G, {"1_2_0"})

        assert [edge["id"] for edge in edges] == ["1_2_0"]

    def test_routable_graph_is_the_same_edge_universe_as_sumo(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=10.0)
        G.add_edge(2, 3, key=0, length=10.0)
        G.add_edge(3, 3, key=0, length=0.0)

        filtered = bc.routable_graph(G, {"1_2_0"})

        assert list(filtered.edges(keys=True)) == [(1, 2, 0)]

    def test_sensor_edges_are_not_endpoint_mass(self):
        edges = [
            {"id": "1_2_0"},
            {"id": "2_3_0"},
            {"id": "3_4_0"},
        ]
        hmass = np.array([2.0, 3.0, 4.0])
        amass = {
            "arbete": np.array([5.0, 6.0, 7.0]),
            "service": np.array([8.0, 9.0, 10.0]),
        }

        removed = bc.exclude_sensor_endpoint_mass(
            edges, hmass, amass, ["2_3_0"])

        assert removed == 1
        assert hmass.tolist() == [2.0, 0.0, 4.0]
        assert amass["arbete"].tolist() == [5.0, 0.0, 7.0]
        assert amass["service"].tolist() == [8.0, 0.0, 10.0]

    def test_repaired_routes_are_dropped_and_metadata_is_pruned(self, tmp_path):
        route_path = tmp_path / "candidates.rou.xml"
        write_routes(route_path, [
            ("good", ["1_2_0", "2_3_0", "3_4_0"]),
            # SUMO's repair can produce this shortened route when the
            # requested origin is a missing/self-loop edge.
            ("origin_changed", ["1_2_0", "2_3_0", "3_4_0"]),
            # A via edge removed by repair must not be accepted either.
            ("via_changed", ["1_2_0", "2_4_0"]),
            # A route ending at a measured edge is an observation endpoint,
            # never a synthetic destination.
            ("sensor_end", ["1_2_0", "2_3_0"]),
        ])
        meta_path = tmp_path / "candidates.meta.json"
        meta_path.write_text(json.dumps({
            "schema_version": 1,
            "candidates": {
                "good": {"origin_edge": "1_2_0",
                         "destination_edge": "3_4_0",
                         "via_edge": "2_3_0"},
                "origin_changed": {"origin_edge": "9_9_0",
                                    "destination_edge": "3_4_0",
                                    "via_edge": None},
                "via_changed": {"origin_edge": "1_2_0",
                                 "destination_edge": "2_4_0",
                                 "via_edge": "2_3_0"},
                "sensor_end": {"origin_edge": "1_2_0",
                                "destination_edge": "2_3_0",
                                "via_edge": None},
            },
        }))

        report = bc.validate_routed_candidates(
            route_path, meta_path, measured=["2_3_0"],
            sumo_edge_ids={"1_2_0", "2_3_0", "2_4_0", "3_4_0"})

        assert report["checked"] == 4
        assert report["kept"] == 1
        assert report["dropped"] == 3
        assert report["reasons"]["origin_mismatch"] == 1
        assert report["reasons"]["via_missing"] == 1
        assert report["reasons"]["sensor_endpoint"] == 1
        assert read_vehicle_ids(route_path) == ["good"]
        assert list(json.loads(meta_path.read_text())["candidates"]) == ["good"]

    def test_legacy_pool_without_metadata_still_rejects_sensor_endpoints(self, tmp_path):
        route_path = tmp_path / "legacy.rou.xml"
        write_routes(route_path, [
            ("sensor_start", ["2_3_0", "3_4_0"]),
            ("clean", ["1_2_0", "2_4_0"]),
        ])

        report = bc.validate_routed_candidates(
            route_path, measured=["2_3_0"],
            sumo_edge_ids={"1_2_0", "2_3_0", "2_4_0", "3_4_0"})

        assert report["kept"] == 1
        assert read_vehicle_ids(route_path) == ["clean"]

    def test_trip_xml_is_authoritative_even_when_sidecar_is_stale(self, tmp_path):
        route_path = tmp_path / "candidates.rou.xml"
        write_routes(route_path, [("t0", ["2_3_0", "3_4_0"])])
        trips_path = tmp_path / "tours.trips.xml"
        trips_path.write_text(
            '<routes><trip id="t0" depart="0" from="1_2_0" '
            'to="3_4_0"/></routes>')
        meta_path = tmp_path / "candidates.meta.json"
        meta_path.write_text(json.dumps({
            "candidates": {"t0": {"origin_edge": "2_3_0",
                                    "destination_edge": "3_4_0",
                                    "via_edge": None}}
        }))

        report = bc.validate_routed_candidates(
            route_path, meta_path, measured=[],
            sumo_edge_ids={"1_2_0", "2_3_0", "3_4_0"},
            intents_path=trips_path)

        assert report["kept"] == 0
        assert report["reasons"]["origin_mismatch"] == 1

    def test_kept_trip_rewrites_stale_sidecar_od_from_trip_xml(self, tmp_path):
        route_path = tmp_path / "candidates.rou.xml"
        write_routes(route_path, [("t0", ["1_2_0", "2_3_0", "3_4_0"])])
        trips_path = tmp_path / "tours.trips.xml"
        trips_path.write_text(
            '<routes><trip id="t0" depart="0" from="1_2_0" '
            'to="3_4_0" via="2_3_0"/></routes>')
        meta_path = tmp_path / "candidates.meta.json"
        meta_path.write_text(json.dumps({
            "candidates": {"t0": {"purpose": "arbete",
                                    "origin_edge": "stale",
                                    "destination_edge": "stale",
                                    "via_edge": None}}
        }))

        report = bc.validate_routed_candidates(
            route_path, meta_path, measured=[],
            sumo_edge_ids={"1_2_0", "2_3_0", "3_4_0"},
            intents_path=trips_path)

        assert report["kept"] == 1
        meta = json.loads(meta_path.read_text())["candidates"]["t0"]
        assert meta == {"purpose": "arbete", "origin_edge": "1_2_0",
                        "destination_edge": "3_4_0", "via_edge": "2_3_0"}

    def test_duplicate_output_and_missing_intent_are_reported(self, tmp_path):
        route_path = tmp_path / "candidates.rou.xml"
        write_routes(route_path, [
            ("t0", ["1_2_0", "2_3_0"]),
            ("t0", ["1_2_0", "2_3_0"]),
        ])
        trips_path = tmp_path / "tours.trips.xml"
        trips_path.write_text(
            '<routes><trip id="t0" depart="0" from="1_2_0" to="2_3_0"/>'
            '<trip id="t1" depart="1" from="1_2_0" to="2_3_0"/></routes>')

        report = bc.validate_routed_candidates(
            route_path, measured=[], sumo_edge_ids={"1_2_0", "2_3_0"},
            intents_path=trips_path)

        assert report["kept"] == 1
        assert report["missing_requested"] == 1
        assert report["reasons"]["duplicate_candidate_id"] == 1
        assert read_vehicle_ids(route_path) == ["t0"]

    def test_multiple_vias_must_appear_in_requested_order(self, tmp_path):
        route_path = tmp_path / "candidates.rou.xml"
        write_routes(route_path, [
            ("t0", ["1_2_0", "3_4_0", "2_3_0", "4_5_0"]),
        ])
        trips_path = tmp_path / "tours.trips.xml"
        trips_path.write_text(
            '<routes><trip id="t0" depart="0" from="1_2_0" to="4_5_0" '
            'via="2_3_0 3_4_0"/></routes>')

        report = bc.validate_routed_candidates(
            route_path, measured=[],
            sumo_edge_ids={"1_2_0", "2_3_0", "3_4_0", "4_5_0"},
            intents_path=trips_path)

        assert report["kept"] == 0
        assert report["reasons"]["via_missing"] == 1

    def test_preflight_rejects_invalid_or_sensor_trip_endpoints(self):
        intents = {
            "bad": {"origin_edge": "sensor", "destination_edge": "sensor",
                    "via_edge": "sensor"},
        }
        with pytest.raises(ValueError, match="sensor_endpoint"):
            bc.validate_trip_intents(intents, {"sensor"}, measured=["sensor"])


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

    def test_loop_around_the_block_with_no_literal_reversal_is_now_caught(self, tmp_path):
        """WIDENED 2026-07-10, found live: Gustav watched the simulation and
        saw vehicles enter a sensor edge and come back out — a real,
        confirmed pattern in sumo/calibrated.rou.xml (not a rendering
        artifact): node 2 revisited 7 edges later via a small pointless
        loop, no adjacent edge/reverse-edge pair anywhere in the route, so
        the OLD check missed it entirely."""
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [
            ("clean", ["1_2_0", "2_3_0", "3_4_0"]),
            ("loop", ["1_2_0", "2_8_0", "8_9_0", "9_2_0", "2_3_0"]),  # node 2 twice
        ])
        bc.drop_uturn_routes(path)
        assert read_vehicle_ids(path) == ["clean"]


class TestDropExcessiveDetours:
    """Added 2026-07-10 (Gustav asked directly: could the candidate pool
    contain "helt orimliga rutter", completely unreasonable routes? Honest
    answer: yes — drop_uturn_routes only catches the symptom where the
    random-factor jitter loops back to a repeated node; a genuinely
    circuitous but still-simple (no repeated node) path is the same root
    cause and was never checked). Mirrors drop_uturn_routes' own test
    style: small synthetic graphs, real Dijkstra reference costs."""

    def _graph(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=10.0)
        G.add_edge(2, 3, key=0, length=10.0)      # true shortest 1->3: 20 via 1_2_0 2_3_0
        G.add_edge(1, 4, key=0, length=10.0)
        G.add_edge(4, 5, key=0, length=10.0)
        G.add_edge(5, 3, key=0, length=100.0)     # detour 1->3 via 1_4_0 4_5_0 5_3_0: 120 (6x)
        return G

    def test_true_shortest_path_is_kept(self, tmp_path):
        G = self._graph()
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("shortest", ["1_2_0", "2_3_0"])])
        bc.drop_excessive_detours(path, G, max_stretch=2.0)
        assert read_vehicle_ids(path) == ["shortest"]

    def test_a_genuine_detour_is_dropped(self, tmp_path):
        G = self._graph()
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [
            ("shortest", ["1_2_0", "2_3_0"]),
            ("detour", ["1_4_0", "4_5_0", "5_3_0"]),   # 120 vs true 20 -> 6x, way over
        ])
        bc.drop_excessive_detours(path, G, max_stretch=2.0)
        assert read_vehicle_ids(path) == ["shortest"]

    def test_within_tolerance_is_kept(self, tmp_path):
        """A mildly longer alternative (1.5x, e.g. a real different-street
        option) must survive a max_stretch=2.0 gate -- this isn't meant to
        force everyone onto THE shortest path, only to catch genuinely
        unreasonable ones."""
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=10.0)
        G.add_edge(2, 3, key=0, length=10.0)     # true shortest: 20
        G.add_edge(1, 6, key=0, length=15.0)
        G.add_edge(6, 3, key=0, length=15.0)     # alternative: 30 (1.5x) -- reasonable
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("alt", ["1_6_0", "6_3_0"])])
        bc.drop_excessive_detours(path, G, max_stretch=2.0)
        assert read_vehicle_ids(path) == ["alt"]

    def test_no_detours_leaves_file_untouched(self, tmp_path):
        G = self._graph()
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("shortest", ["1_2_0", "2_3_0"])])
        mtime_before = path.stat().st_mtime_ns
        bc.drop_excessive_detours(path, G, max_stretch=2.0)
        assert path.stat().st_mtime_ns == mtime_before   # write() skipped when dropped==0

    def test_stretch_bound_is_tied_to_the_route_diversity_parameter(self, tmp_path):
        """max_stretch is passed straight through from --route-diversity
        (the same parameter driving duarouter's --weights.random-factor),
        not an unrelated constant -- a stricter caller-supplied bound must
        catch what a looser one lets through."""
        G = self._graph()
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("detour", ["1_4_0", "4_5_0", "5_3_0"])])   # 6x true shortest
        bc.drop_excessive_detours(path, G, max_stretch=10.0)   # loose: 6x survives
        assert read_vehicle_ids(path) == ["detour"]

    def test_shared_entry_nodes_use_one_batched_dijkstra_call(self, tmp_path, monkeypatch):
        """Endpoints repeat heavily in the real pool (e.g. ~900 via-trips
        per sensor sharing a handful of gate pairs) -- true-shortest-path
        costs must be computed via ONE batched scipy Dijkstra call across
        every distinct ENTRY node (each call returns distances to every
        other node at once), not one call per candidate or even per
        distinct (entry, exit) pair. (Was: one networkx shortest_path_
        length call per distinct pair -- superseded 2026-07-10 by the
        scipy-batched-by-source rewrite, ~28x faster on the real pool,
        verified bit-identical output first.)"""
        G = self._graph()
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [
            ("a", ["1_2_0", "2_3_0"]),
            ("b", ["1_2_0", "2_3_0"]),
            ("c", ["1_2_0", "2_3_0"]),
        ])
        # drop_excessive_detours does `from scipy.sparse.csgraph import
        # dijkstra as sp_dijkstra` freshly INSIDE the function on every
        # call, so patching must target the actual source module (scipy's
        # own `dijkstra` name) -- a local name binding at call time
        # re-reads whatever that module attribute currently is, but a
        # module-level attribute on build_candidates itself wouldn't be
        # touched by that import statement at all.
        calls = []
        import scipy.sparse.csgraph as csgraph_module
        real_fn = csgraph_module.dijkstra
        def counting_fn(*args, **kwargs):
            calls.append(1)
            return real_fn(*args, **kwargs)
        monkeypatch.setattr(csgraph_module, "dijkstra", counting_fn)
        bc.drop_excessive_detours(path, G, max_stretch=2.0)
        assert len(calls) == 1   # one batched call for all 3 candidates' shared entry
        assert read_vehicle_ids(path) == ["a", "b", "c"]   # all kept -- true shortest


class TestReportSensorCrossHits:
    """Diagnostic-only (2026-07-10): a vehicle whose ACTUAL routed path
    naturally crosses multiple sensors is extra valuable (cross-sensor
    reinforcement) -- checked after routing via simple set membership,
    not during generation, so it reflects what really happened rather
    than generation-time intent."""

    @pytest.fixture(autouse=True)
    def _isolated_sumo_dir(self, tmp_path, monkeypatch):
        # report_sensor_cross_hits always writes its JSON report into
        # SUMO_DIR. The repo's sumo/ directory is a gitignored build product
        # that does not exist on a fresh clone, so without this redirect
        # every test here fails with FileNotFoundError on first run and
        # writes into the real working tree on later runs.
        monkeypatch.setattr(bc, "SUMO_DIR", tmp_path)

    def test_vehicle_touching_only_its_own_sensor_counts_as_cross_0(self, tmp_path):
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("v0", ["1_2_0", "2_3_0"])])
        report = bc.report_sensor_cross_hits(path, ["2_3_0"])
        assert report["2_3_0"]["total"] == 1
        assert report["2_3_0"]["cross_1"] == 0

    def test_vehicle_touching_two_sensors_counts_for_both(self, tmp_path):
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("v0", ["1_2_0", "2_3_0", "3_4_0"])])
        report = bc.report_sensor_cross_hits(path, ["1_2_0", "3_4_0"])
        assert report["1_2_0"]["total"] == 1
        assert report["1_2_0"]["cross_1"] == 1
        assert report["3_4_0"]["total"] == 1
        assert report["3_4_0"]["cross_1"] == 1

    def test_vehicle_touching_four_sensors_counts_as_cross_3plus(self, tmp_path):
        """cross_N buckets by OTHER sensors touched -- 4 sensors total
        means 3 others for each, landing in the 3+ bucket."""
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("v0", ["1_2_0", "2_3_0", "3_4_0", "4_5_0"])])
        sensors = ["1_2_0", "2_3_0", "3_4_0", "4_5_0"]
        report = bc.report_sensor_cross_hits(path, sensors)
        for m in sensors:
            assert report[m]["cross_3plus"] == 1

    def test_vehicle_touching_no_sensor_is_not_counted_anywhere(self, tmp_path):
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("v0", ["9_10_0"])])
        report = bc.report_sensor_cross_hits(path, ["1_2_0"])
        assert report["1_2_0"]["total"] == 0

    def test_writes_the_report_file(self, tmp_path):
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("v0", ["1_2_0"])])
        bc.report_sensor_cross_hits(path, ["1_2_0"])
        assert (tmp_path / "sensor_coverage_report.json").exists()


class TestRouteVisitsANodeTwice:
    def test_straight_route_has_no_repeat(self):
        assert not bc.route_visits_a_node_twice(["1_2_0", "2_3_0", "3_4_0"])

    def test_literal_uturn_is_a_repeat(self):
        assert bc.route_visits_a_node_twice(["1_2_0", "2_1_0", "1_5_0"])

    def test_loop_around_the_block_is_a_repeat(self):
        assert bc.route_visits_a_node_twice(["1_2_0", "2_8_0", "8_9_0", "9_2_0"])

    def test_empty_route_is_not_a_repeat(self):
        assert not bc.route_visits_a_node_twice([])

    def test_single_edge_is_not_a_repeat(self):
        assert not bc.route_visits_a_node_twice(["1_2_0"])


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


class TestViaNaturallyOnPath:
    """Found 2026-07-10 (Gustav, watching the live simulation, saw vehicles
    enter a sensor and drive straight back out — real, confirmed via
    sumo/calibrated.rou.xml, not a rendering artifact): a via-forced trip
    is solved by duarouter as TWO INDEPENDENT shortest-path legs (entry->
    via, via->exit) concatenated. Each leg alone is genuinely optimal —
    Dijkstra/A* with non-negative weights can never revisit a node — but
    the CONCATENATION isn't, when the via point isn't actually on the
    natural entry->exit path. This checks the real claim on the real graph
    instead of trusting upstream_downstream_gates' straight-line-bearing
    proxy."""

    def test_via_edge_on_the_direct_chain_is_detected(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)    # entry: 1_10_0, entry_v=10
        G.add_edge(10, 11, key=0, length=10.0)   # via: 10_11_0
        G.add_edge(11, 20, key=0, length=10.0)   # exit: 11_20_0, exit_u=11
        assert bc.via_naturally_on_path(G, entry_v=10, exit_u=11, m_u=10, m_v=11)

    def test_via_edge_bypassed_by_a_shorter_route_is_not_on_path(self):
        """A much shorter alternative (10->99->11) exists, so the shortest
        path from 10 to 11 skips the via edge entirely -- forcing the trip
        through it anyway is exactly what creates the backtrack."""
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(10, 11, key=0, length=1000.0)   # the "official" via edge -- long
        G.add_edge(11, 20, key=0, length=10.0)
        G.add_edge(10, 99, key=0, length=1.0)      # much shorter bypass
        G.add_edge(99, 11, key=0, length=1.0)
        assert not bc.via_naturally_on_path(G, entry_v=10, exit_u=11, m_u=10, m_v=11)

    def test_no_path_at_all_returns_false_not_an_exception(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(11, 20, key=0, length=10.0)   # disconnected from node 10
        assert not bc.via_naturally_on_path(G, entry_v=10, exit_u=11, m_u=10, m_v=11)


class TestBuildShortestDistanceMatrix:
    def test_direct_edge_distance(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=10.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        assert D[node_idx[1], node_idx[2]] == pytest.approx(10.0)

    def test_multi_hop_distance_sums_edges(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=10.0)
        G.add_edge(2, 3, key=0, length=5.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        assert D[node_idx[1], node_idx[3]] == pytest.approx(15.0)

    def test_unreachable_pair_is_infinite(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=10.0)
        G.add_edge(3, 4, key=0, length=10.0)   # disconnected component
        D, node_idx = bc.build_shortest_distance_matrix(G)
        assert D[node_idx[1], node_idx[3]] == np.inf

    def test_parallel_edges_take_the_minimum_not_the_sum(self):
        """scipy's sparse constructor sums duplicate (row,col) entries by
        default -- this would silently corrupt distances (10+2=12 instead
        of the correct min=2) if not explicitly deduplicated."""
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=10.0)
        G.add_edge(1, 2, key=1, length=2.0)    # a shorter parallel edge
        D, node_idx = bc.build_shortest_distance_matrix(G)
        assert D[node_idx[1], node_idx[2]] == pytest.approx(2.0)

    def test_directed_asymmetry_is_preserved(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=10.0)   # only one direction
        D, node_idx = bc.build_shortest_distance_matrix(G)
        assert D[node_idx[1], node_idx[2]] == pytest.approx(10.0)
        assert D[node_idx[2], node_idx[1]] == np.inf

    def test_explicit_sumo_costs_override_physical_length(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=100.0)
        G.add_edge(2, 3, key=0, length=100.0)
        costs = {"1_2_0": 2.0, "2_3_0": 3.0}

        D, node_idx = bc.build_shortest_distance_matrix(G, costs)

        assert D[node_idx[1], node_idx[3]] == pytest.approx(5.0)


class TestSumoCostNaturalness:
    def test_via_naturalness_uses_sumo_travel_time_when_supplied(self):
        G = nx.MultiDiGraph()
        # The via road is physically long but fast; the bypass is physically
        # short but slow. SUMO chooses the former, while a length-only mask
        # would incorrectly exclude it.
        G.add_edge(2, 3, key=0, length=100.0)
        G.add_edge(2, 4, key=0, length=1.0)
        G.add_edge(4, 3, key=0, length=1.0)
        costs = {"2_3_0": 1.0, "2_4_0": 10.0, "4_3_0": 10.0}

        assert not bc.via_naturally_on_path(G, 2, 3, 2, 3, "2_3_0")
        assert bc.via_naturally_on_path(
            G, 2, 3, 2, 3, "2_3_0", edge_costs=costs)


class TestNaturalFarEndWeights:
    """Cross-checked against via_naturally_on_path's own reference graphs
    (TestViaNaturallyOnPath above) -- the vectorized distance-arithmetic
    check must agree with the scalar path-trace check on every case, since
    natural_far_end_weights is meant to be an exact generalization, not an
    approximation."""

    def test_agrees_with_via_naturally_on_path_when_on_direct_chain(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(10, 11, key=0, length=10.0)
        G.add_edge(11, 20, key=0, length=10.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        w = bc.natural_far_end_weights(
            D, node_idx, anchor_v=10, m_u=10, m_v=11, m_length=10.0,
            candidate_u_idx=np.array([node_idx[11]]), base_weights=np.array([3.0]))
        assert w[0] == pytest.approx(3.0)   # natural -> base weight passes through
        assert bc.via_naturally_on_path(G, entry_v=10, exit_u=11, m_u=10, m_v=11)

    def test_agrees_with_via_naturally_on_path_when_bypassed(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(10, 11, key=0, length=1000.0)
        G.add_edge(11, 20, key=0, length=10.0)
        G.add_edge(10, 99, key=0, length=1.0)
        G.add_edge(99, 11, key=0, length=1.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        w = bc.natural_far_end_weights(
            D, node_idx, anchor_v=10, m_u=10, m_v=11, m_length=1000.0,
            candidate_u_idx=np.array([node_idx[11]]), base_weights=np.array([3.0]))
        assert w[0] == pytest.approx(0.0)   # not natural -> masked to zero
        assert not bc.via_naturally_on_path(G, entry_v=10, exit_u=11, m_u=10, m_v=11)

    def test_agrees_with_via_naturally_on_path_when_unreachable(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(11, 20, key=0, length=10.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        w = bc.natural_far_end_weights(
            D, node_idx, anchor_v=10, m_u=10, m_v=11, m_length=10.0,
            candidate_u_idx=np.array([node_idx[11]]), base_weights=np.array([3.0]))
        assert w[0] == pytest.approx(0.0)
        assert not bc.via_naturally_on_path(G, entry_v=10, exit_u=11, m_u=10, m_v=11)


class TestBoundedDetourNaturalness:
    """2026-07-17 semantics change: naturalness = bounded detour, not exact
    shortest path.  The exact rule (±0.5 s) left 6 of 7 real sensors with
    ZERO verified through gate pairs — all through candidates entered the
    city at 2 street cuts and exited at 1 — and confined tour destinations
    to the shadow cone just behind each sensor.  A sensor must explain any
    movement whose REALISTIC route (within max(45 s, 20% of direct)) passes
    it, which is stochastic-multipath route choice, the same model this
    project already validated for assignment priors."""

    def _bypass_graph(self, via_cost, bypass_cost):
        # 10 -> 11 directly via the sensor edge (via_cost), or around
        # through 99 (bypass_cost total).  Direct shortest = min of the two.
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(10, 11, key=0, length=via_cost)
        G.add_edge(11, 20, key=0, length=10.0)
        G.add_edge(10, 99, key=0, length=bypass_cost / 2)
        G.add_edge(99, 11, key=0, length=bypass_cost / 2)
        return G

    def test_modest_detour_within_absolute_floor_is_natural(self):
        # Sensor route costs 140, bypass 100: +40 detour <= 45 s floor.
        # The old exact rule rejected this; a real driver would not.
        G = self._bypass_graph(via_cost=140.0, bypass_cost=100.0)
        assert bc.via_naturally_on_path(G, entry_v=10, exit_u=11, m_u=10, m_v=11)

    def test_detour_beyond_both_tolerances_is_rejected(self):
        # +100 detour on a 100-cost trip: over the 45 s floor and over 20%.
        G = self._bypass_graph(via_cost=200.0, bypass_cost=100.0)
        assert not bc.via_naturally_on_path(G, entry_v=10, exit_u=11, m_u=10, m_v=11)

    def test_relative_tolerance_scales_with_trip_length(self):
        # Long trip: direct 1000, via 1150 -> 15% < 20% relative: natural.
        G = self._bypass_graph(via_cost=1150.0, bypass_cost=1000.0)
        assert bc.via_naturally_on_path(G, entry_v=10, exit_u=11, m_u=10, m_v=11)
        # via 1300 -> 30% > 20%: rejected.
        G = self._bypass_graph(via_cost=1300.0, bypass_cost=1000.0)
        assert not bc.via_naturally_on_path(G, entry_v=10, exit_u=11, m_u=10, m_v=11)

    def test_far_end_weights_admit_bounded_detour_vectorized(self):
        G = self._bypass_graph(via_cost=140.0, bypass_cost=100.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        w = bc.natural_far_end_weights(
            D, node_idx, anchor_v=10, m_u=10, m_v=11, m_length=140.0,
            candidate_u_idx=np.array([node_idx[11]]),
            base_weights=np.array([3.0]))
        assert w[0] == pytest.approx(3.0)

    def test_sensor_masks_admit_bounded_detour(self):
        G = self._bypass_graph(via_cost=140.0, bypass_cost=100.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        masks = bc.natural_sensor_masks(
            D, node_idx, anchor_v=10, m_info={"m": (10, 11, 140.0)},
            dest_u_idx=np.array([node_idx[11]]))
        assert masks["m"][0]

    def test_origin_weights_admit_bounded_detour(self):
        G = self._bypass_graph(via_cost=140.0, bypass_cost=100.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        w = bc.natural_origin_weights(
            D, node_idx, candidate_v_idx=np.array([node_idx[10]]),
            m_u=10, m_v=11, m_length=140.0, dest_u=11,
            base_weights=np.array([2.0]))
        assert w[0] == pytest.approx(2.0)

    def test_vectorizes_over_multiple_candidates_independently(self):
        """One candidate naturally fits, another doesn't -- each must be
        masked independently, not all-or-nothing."""
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(10, 11, key=0, length=10.0)   # via edge
        G.add_edge(11, 20, key=0, length=10.0)   # natural candidate: node 20
        G.add_edge(1, 30, key=0, length=1.0)     # a much shorter, unrelated path to 30
        D, node_idx = bc.build_shortest_distance_matrix(G)
        candidate_idx = np.array([node_idx[20], node_idx[30]])
        w = bc.natural_far_end_weights(
            D, node_idx, anchor_v=1, m_u=10, m_v=11, m_length=10.0,
            candidate_u_idx=candidate_idx, base_weights=np.array([5.0, 7.0]))
        assert w[0] == pytest.approx(5.0)    # node 20: natural via the sensor
        assert w[1] == pytest.approx(0.0)    # node 30: shorter direct path exists

    def test_zero_base_weight_stays_zero_even_when_natural(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(10, 11, key=0, length=10.0)
        G.add_edge(11, 20, key=0, length=10.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        w = bc.natural_far_end_weights(
            D, node_idx, anchor_v=10, m_u=10, m_v=11, m_length=10.0,
            candidate_u_idx=np.array([node_idx[11]]), base_weights=np.array([0.0]))
        assert w[0] == pytest.approx(0.0)

    def test_rejects_a_natural_mask_that_requires_reversing_the_origin_edge(self):
        """The base distance equality can be true while the shortest leg
        starts by returning to the origin edge's u-node.  That is exactly
        the route SUMO later removes, changing the visible spawn edge; the
        endpoint-aware guard must reject it before routing."""
        G = nx.MultiDiGraph()
        G.add_edge(0, 1, key=0, length=1.0)   # already-traversed origin
        G.add_edge(1, 0, key=0, length=1.0)   # reverse trap
        G.add_edge(0, 2, key=0, length=1.0)
        G.add_edge(2, 3, key=0, length=1.0)   # via edge
        G.add_edge(3, 4, key=0, length=1.0)   # destination
        D, node_idx = bc.build_shortest_distance_matrix(G)

        unguarded = bc.natural_far_end_weights(
            D, node_idx, anchor_v=1, m_u=2, m_v=3, m_length=1.0,
            candidate_u_idx=np.array([node_idx[3]]),
            base_weights=np.array([3.0]))
        guarded = bc.natural_far_end_weights(
            D, node_idx, anchor_v=1, m_u=2, m_v=3, m_length=1.0,
            candidate_u_idx=np.array([node_idx[3]]),
            base_weights=np.array([3.0]), anchor_u=0,
            candidate_v_idx=np.array([node_idx[4]]))

        assert unguarded[0] == pytest.approx(3.0)
        assert guarded[0] == pytest.approx(0.0)


class TestNaturalOriginWeights:
    """Mirror of natural_far_end_weights -- there the ORIGIN is fixed and
    DESTINATION candidates are masked; here the DESTINATION is fixed and
    ORIGIN candidates are masked. Added 2026-07-10 after finding a real
    bug: I-E's return leg needs to draw a FRESH entry gate as its ORIGIN
    (reaching the fixed home edge as destination) -- natural_far_end_weights
    can't express that (it only ever varies the destination side)."""

    def test_agrees_with_natural_far_end_weights_by_symmetry(self):
        """For a single fixed pair, natural_origin_weights(candidate=X,
        dest=Y) must agree with natural_far_end_weights(anchor=X,
        candidate=Y) -- same underlying invariant, just built to vary the
        opposite side."""
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(10, 11, key=0, length=10.0)
        G.add_edge(11, 20, key=0, length=10.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        w_far = bc.natural_far_end_weights(
            D, node_idx, anchor_v=10, m_u=10, m_v=11, m_length=10.0,
            candidate_u_idx=np.array([node_idx[11]]), base_weights=np.array([5.0]))
        w_origin = bc.natural_origin_weights(
            D, node_idx, candidate_v_idx=np.array([node_idx[10]]),
            m_u=10, m_v=11, m_length=10.0, dest_u=11, base_weights=np.array([5.0]))
        assert w_far[0] == pytest.approx(w_origin[0])

    def test_bypassed_route_is_masked_to_zero(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(10, 11, key=0, length=1000.0)
        G.add_edge(11, 20, key=0, length=10.0)
        G.add_edge(10, 99, key=0, length=1.0)
        G.add_edge(99, 11, key=0, length=1.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        w = bc.natural_origin_weights(
            D, node_idx, candidate_v_idx=np.array([node_idx[10]]),
            m_u=10, m_v=11, m_length=1000.0, dest_u=11, base_weights=np.array([3.0]))
        assert w[0] == pytest.approx(0.0)

    def test_vectorizes_over_multiple_origin_candidates_independently(self):
        G = nx.MultiDiGraph()
        G.add_edge(10, 11, key=0, length=10.0)   # via edge
        G.add_edge(11, 20, key=0, length=10.0)   # dest
        G.add_edge(30, 20, key=0, length=1.0)    # a much shorter, unrelated direct path to dest
        D, node_idx = bc.build_shortest_distance_matrix(G)
        candidate_idx = np.array([node_idx[10], node_idx[30]])
        w = bc.natural_origin_weights(
            D, node_idx, candidate_v_idx=candidate_idx,
            m_u=10, m_v=11, m_length=10.0, dest_u=20, base_weights=np.array([5.0, 7.0]))
        assert w[0] == pytest.approx(5.0)   # node 10: natural via the sensor
        assert w[1] == pytest.approx(0.0)   # node 30: shorter direct path exists

    def test_unreachable_pair_is_masked_to_zero(self):
        G = nx.MultiDiGraph()
        G.add_edge(10, 11, key=0, length=10.0)
        G.add_edge(30, 40, key=0, length=10.0)   # disconnected from 10/11
        D, node_idx = bc.build_shortest_distance_matrix(G)
        w = bc.natural_origin_weights(
            D, node_idx, candidate_v_idx=np.array([node_idx[30]]),
            m_u=10, m_v=11, m_length=10.0, dest_u=11, base_weights=np.array([3.0]))
        assert w[0] == pytest.approx(0.0)


class TestGenerateSensorAnchoredTripsEIandIE:
    """Regression test for a real, confirmed, SHIPPED bug (found 2026-07-10
    during a full-structure review Gustav asked for, after the previous
    session's rewrite had already been committed and pushed): E-I and I-E
    were COMPLETELY non-functional -- isolating their generation on the
    real graph (cross_fraction=1.0, through_fraction=0.0) produced
    1000/1000 dropped tour attempts, silently, with no exception and no
    warning distinguishing it from ordinary sensor-fit variance. Root
    cause: the return leg reused the OUTBOUND anchor's own gate edge as
    the return destination/origin for every category, but E-I's anchor is
    an ENTRY gate (its own start node has in-degree 0 -- nothing inside
    the graph can ever route back TO it) and I-E's far end is an EXIT
    gate (its own end node has out-degree 0 -- nothing can ever continue
    FROM it), so the return-leg naturalness check was asking for
    something structurally impossible and always returned None.

    This was caught ONLY by manually running ad hoc diagnostic scripts
    against the real graph -- no permanent test exercised
    generate_sensor_anchored_trips itself. This test fixture is a small,
    deterministic "diamond corridor" graph built so this bug (and its
    fix) has a permanent, fast, no-real-data-needed regression guard:

        1 --entry--> 2 --sensorA--> 3 --home/activity--> 4 --sensorB--> 5 --exit--> 6

    Node 1 has in-degree 0 (the only entry gate); node 6 has out-degree 0
    (the only exit gate) -- matching find_gates()'s own convention. hmass/
    amass put ALL weight on the home/activity edge (3_4_0) so the anchor/
    far-end draw is deterministic, not just probable."""

    def _build_graph(self):
        G = nx.MultiDiGraph()
        coords = {1: (0.0, 0.0), 2: (0.0, 0.01), 3: (0.0, 0.02),
                 4: (0.0, 0.03), 5: (0.0, 0.04), 6: (0.0, 0.05)}
        for n, (lat, lon) in coords.items():
            G.add_node(n, y=lat, x=lon)
        G.add_edge(1, 2, key=0, length=10.0)   # entry gate (1_2_0)
        G.add_edge(2, 3, key=0, length=10.0)   # sensorA (2_3_0)
        G.add_edge(3, 4, key=0, length=10.0)   # home/activity (3_4_0)
        G.add_edge(4, 5, key=0, length=10.0)   # sensorB (4_5_0)
        G.add_edge(5, 6, key=0, length=10.0)   # exit gate (5_6_0)
        return G

    def _setup(self):
        G = self._build_graph()
        edges = [
            {"id": "1_2_0", "u": 1, "v": 2, "lat": 0.0, "lon": 0.005, "hw": "", "len": 10.0},
            {"id": "2_3_0", "u": 2, "v": 3, "lat": 0.0, "lon": 0.015, "hw": "", "len": 10.0},
            {"id": "3_4_0", "u": 3, "v": 4, "lat": 0.0, "lon": 0.025, "hw": "", "len": 10.0},
            {"id": "4_5_0", "u": 4, "v": 5, "lat": 0.0, "lon": 0.035, "hw": "", "len": 10.0},
            {"id": "5_6_0", "u": 5, "v": 6, "lat": 0.0, "lon": 0.045, "hw": "", "len": 10.0},
        ]
        # all weight on the home/activity edge (index 2) -- deterministic draw
        hmass = np.array([0.0, 0.0, 1.0, 0.0, 0.0])
        amass = {p: np.array([0.0, 0.0, 1.0, 0.0, 0.0]) for p in bc.PURPOSE_CATEGORIES}
        entries = [("1_2_0", 1)]
        exits = [("5_6_0", 6)]
        entry_ids = ["1_2_0"]
        exit_ids = ["5_6_0"]
        w_entry = np.array([1.0])
        w_exit = np.array([1.0])
        shape_hourly = np.full(24, 1 / 24)
        measured = ["2_3_0", "4_5_0"]
        return G, edges, hmass, amass, entries, exits, entry_ids, exit_ids, w_entry, w_exit, shape_hourly, measured

    def test_ei_return_leg_goes_to_the_exit_not_back_to_the_entry(self):
        (G, edges, hmass, amass, entries, exits, entry_ids, exit_ids,
         w_entry, w_exit, shape_hourly, measured) = self._setup()
        rng = np.random.default_rng(0)
        # n_total=8, through_fraction=0, cross_fraction=1.0 -> n_ei=2, n_ie=2, n_internal=0
        trips, lengths, short = bc.generate_sensor_anchored_trips(
            rng, G, edges, hmass, amass, entries, exits, entry_ids, exit_ids,
            w_entry, w_exit, shape_hourly, measured,
            n_total=8, through_fraction=0.0, cross_fraction=1.0,
            gravity_km=5.0, is_weekend=False, min_per_sensor=0)
        assert len(trips) > 0, "E-I/I-E generation must not silently produce zero trips"
        # every trip must be sensor-anchored (via tag present)
        assert all(len(t) > 3 for t in trips)
        # NO trip may end AT the entry gate or start FROM the exit gate --
        # the exact structural-impossibility bug this test guards against
        for t in trips:
            assert t[2] != "1_2_0", "a trip ended at the entry gate -- unreachable from inside the graph"
            assert t[1] != "5_6_0", "a trip started from the exit gate -- can't continue past it"

    def test_ei_outbound_uses_entry_and_activity_return_uses_exit(self):
        (G, edges, hmass, amass, entries, exits, entry_ids, exit_ids,
         w_entry, w_exit, shape_hourly, measured) = self._setup()
        rng = np.random.default_rng(1)
        trips, lengths, short = bc.generate_sensor_anchored_trips(
            rng, G, edges, hmass, amass, entries, exits, entry_ids, exit_ids,
            w_entry, w_exit, shape_hourly, measured,
            n_total=8, through_fraction=0.0, cross_fraction=1.0,
            gravity_km=5.0, is_weekend=False, min_per_sensor=0)
        # find an E-I outbound leg (from=entry) and confirm its paired
        # return leg (to=activity's far end from THIS trip) lands on the exit
        outbound = [t for t in trips if t[1] == "1_2_0"]
        assert len(outbound) > 0, "no E-I outbound leg was generated at all"
        for t in outbound:
            assert t[2] == "3_4_0"   # deterministic: only the activity edge has weight

    def test_ie_return_leg_comes_from_the_entry_not_the_exit(self):
        (G, edges, hmass, amass, entries, exits, entry_ids, exit_ids,
         w_entry, w_exit, shape_hourly, measured) = self._setup()
        rng = np.random.default_rng(2)
        trips, lengths, short = bc.generate_sensor_anchored_trips(
            rng, G, edges, hmass, amass, entries, exits, entry_ids, exit_ids,
            w_entry, w_exit, shape_hourly, measured,
            n_total=8, through_fraction=0.0, cross_fraction=1.0,
            gravity_km=5.0, is_weekend=False, min_per_sensor=0)
        # find an I-E outbound leg (from=home/activity, to=exit)
        outbound = [t for t in trips if t[1] == "3_4_0" and t[2] == "5_6_0"]
        assert len(outbound) > 0, "no I-E outbound leg was generated at all"
        # its return leg must depart from the ENTRY gate, arriving at home
        return_legs = [t for t in trips if t[2] == "3_4_0" and t[1] == "1_2_0"]
        assert len(return_legs) > 0, "I-E return leg never used the entry gate as origin"

    def test_no_tours_are_silently_dropped_on_this_well_connected_graph(self):
        """On a graph where BOTH sensors are genuinely reachable for both
        legs, the drop rate must be 0 -- this is what "1000/1000 dropped"
        looked like before the fix, and 0/N is what it must look like now."""
        (G, edges, hmass, amass, entries, exits, entry_ids, exit_ids,
         w_entry, w_exit, shape_hourly, measured) = self._setup()
        rng = np.random.default_rng(3)
        trips, lengths, short = bc.generate_sensor_anchored_trips(
            rng, G, edges, hmass, amass, entries, exits, entry_ids, exit_ids,
            w_entry, w_exit, shape_hourly, measured,
            n_total=40, through_fraction=0.0, cross_fraction=1.0,
            gravity_km=5.0, is_weekend=False, min_per_sensor=0)
        # n_total=40 -> n_tours_total=20, n_cross=20, n_ei=10, n_ie=10 -> 20 legs each way = 40 legs total
        assert len(trips) == 40

    def test_conditioned_cache_is_result_neutral(self):
        args = self._setup()

        uncached = bc.generate_sensor_anchored_trips(
            np.random.default_rng(4), *args,
            n_total=40, through_fraction=0.0, cross_fraction=1.0,
            gravity_km=5.0, is_weekend=False, min_per_sensor=0,
            cache_conditioned_fields=False)
        cached = bc.generate_sensor_anchored_trips(
            np.random.default_rng(4), *args,
            n_total=40, through_fraction=0.0, cross_fraction=1.0,
            gravity_km=5.0, is_weekend=False, min_per_sensor=0,
            cache_conditioned_fields=True)

        assert cached == uncached


class TestConditionedMaskCache:
    def test_packed_membership_preserves_sensor_attribution(self):
        measured = [f"s{i}" for i in range(9)]
        masks = {
            edge_id: np.array([i == 0, i == 8, True])
            for i, edge_id in enumerate(measured)
        }

        membership, union = bc.pack_sensor_membership(masks, measured)

        assert membership.shape == (2, 3)
        assert union.tolist() == [True, True, True]
        assert bc.sensors_at_membership(membership, measured, 0) == ["s0"]
        assert bc.sensors_at_membership(membership, measured, 1) == ["s8"]
        assert bc.sensors_at_membership(membership, measured, 2) == measured

    def test_lru_cache_stays_inside_its_byte_budget(self):
        cache = bc.ConditionedMaskCache(max_bytes=4)
        membership = np.zeros((1, 2), dtype=np.uint8)
        union = np.zeros(2, dtype=bool)

        cache.put(1, membership, union)
        cache.put(2, membership.copy(), union.copy())

        assert cache.bytes_used == 4
        assert cache.get(1) is None
        assert cache.get(2) is not None


class TestVerifiedViaGatePairs:
    def test_keeps_only_pairs_where_via_is_naturally_on_the_path(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)      # good entry: 1_10_0
        G.add_edge(2, 30, key=0, length=10.0)      # bad entry: 2_30_0 (no path to via)
        G.add_edge(10, 11, key=0, length=10.0)     # via: 10_11_0
        G.add_edge(11, 20, key=0, length=10.0)     # good exit: 11_20_0

        pairs = bc.verified_via_gate_pairs(G, "10_11_0", ["1_10_0", "2_30_0"], ["11_20_0"])
        assert pairs == [("1_10_0", "11_20_0")]

    def test_returns_no_pairs_when_none_are_natural(self):
        """A measured edge with no natural gate movement is an explicit
        observability limitation, never a reason to manufacture a detour."""
        G = nx.MultiDiGraph()
        G.add_edge(1, 10, key=0, length=10.0)
        G.add_edge(10, 11, key=0, length=1000.0)
        G.add_edge(11, 20, key=0, length=10.0)
        G.add_edge(10, 99, key=0, length=1.0)     # bypass -- via never verifies
        G.add_edge(99, 11, key=0, length=1.0)

        pairs = bc.verified_via_gate_pairs(G, "10_11_0", ["1_10_0"], ["11_20_0"])
        assert pairs == []


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


class TestGateLatLon:
    """E-I/I-E cross-boundary tours (added 2026-07-08) need gate
    coordinates to gravity-weight the internal endpoint by distance from
    the gate — same midpoint convention as load_graph_edges()."""

    def test_midpoint_of_edge_endpoints(self):
        G = nx.MultiDiGraph()
        G.add_node(1, y=57.700, x=11.900)
        G.add_node(2, y=57.702, x=11.904)
        G.add_edge(1, 2, key=0)
        lats, lons = bc.gate_latlon(G, [("1_2_0", 1)])
        assert lats[0] == pytest.approx((57.700 + 57.702) / 2)
        assert lons[0] == pytest.approx((11.900 + 11.904) / 2)

    def test_multiple_gates_preserve_order(self):
        G = nx.MultiDiGraph()
        G.add_node(1, y=57.70, x=11.90)
        G.add_node(2, y=57.71, x=11.91)
        G.add_node(3, y=57.80, x=12.00)
        G.add_node(4, y=57.81, x=12.01)
        G.add_edge(1, 2, key=0)
        G.add_edge(3, 4, key=0)
        lats, lons = bc.gate_latlon(G, [("1_2_0", 1), ("3_4_0", 3)])
        assert len(lats) == 2
        assert lats[1] > lats[0]   # second gate is further north


class TestPurposeSharesForDayType:
    """Found 2026-07-10: PURPOSE_SHARES was a single flat 43/33/24 constant
    (RVU's own WEEKLY average, no day-type qualifier in Fig.11's caption)
    applied on EVERY simulated day regardless of weekday/weekend/holiday —
    silently overstating 'arbete' on real weekends/holidays. Split into
    PURPOSE_SHARES_WEEKDAY/WEEKEND using NHTS 2017's verified weekday/
    weekend purpose-shift RATIOS (home-based work/shop+other/social),
    solved so the 5-weekday/2-weekend weekly average reproduces RVU's own
    43/33/24 exactly — the total stays anchored to the real Swedish
    measurement, only the day-type split is externally informed."""

    def test_weekday_shares_sum_to_one(self):
        assert sum(bc.PURPOSE_SHARES_WEEKDAY.values()) == pytest.approx(1.0)

    def test_weekend_shares_sum_to_one(self):
        assert sum(bc.PURPOSE_SHARES_WEEKEND.values()) == pytest.approx(1.0)

    def test_weekend_has_far_less_arbete_than_weekday(self):
        assert bc.PURPOSE_SHARES_WEEKEND["arbete"] < bc.PURPOSE_SHARES_WEEKDAY["arbete"]

    def test_weekend_has_more_fritid_than_weekday(self):
        assert bc.PURPOSE_SHARES_WEEKEND["fritid"] > bc.PURPOSE_SHARES_WEEKDAY["fritid"]

    def test_annual_average_reproduces_rvu_reported_total(self):
        """The load-bearing constraint: the EXACT 2025 annual day-type mix
        (249 true-weekday days + 116 weekend-shaped days [104 real weekend
        + 12 weekday-that-are-holidays] out of 365 — NOT a naive 5/7-2/7
        week, since ~12 weekday holidays/year measurably shift the
        composition) must reproduce RVU's actual measured 43/33/24 for
        each category — if someone tweaks either dict later without
        re-solving this, the anchor to real data silently breaks."""
        f_weekday, f_weekend_shaped = 249 / 365, 116 / 365
        rvu_annual_avg = {"arbete": 0.43, "service": 0.33, "fritid": 0.24}
        for cat, target in rvu_annual_avg.items():
            wd = bc.PURPOSE_SHARES_WEEKDAY[cat]
            we = bc.PURPOSE_SHARES_WEEKEND[cat]
            avg = f_weekday * wd + f_weekend_shaped * we
            assert avg == pytest.approx(target, abs=0.005)

    def test_purpose_shares_for_selects_correct_profile(self):
        assert bc.purpose_shares_for(is_weekend=False) == bc.PURPOSE_SHARES_WEEKDAY
        assert bc.purpose_shares_for(is_weekend=True) == bc.PURPOSE_SHARES_WEEKEND


class TestPurposeHourlyTables:
    """Purpose ALSO varies by hour, not just day-type — "kl 8 nästan 100%
    jobb" was the original observation. Calibrated from a THIRD external
    source with genuine hourly granularity (UK NTS table NTSQ03018,
    weekday) rescaled against our own real measured Gothenburg hourly
    shape; weekend uses a simpler model (only fritid varies by hour, since
    a 3-parameter fit against real weekend rush-hour anchors bought
    essentially nothing over a 1-parameter one). Added 2026-07-10."""

    def test_every_weekday_hour_sums_to_one(self):
        for h, row in enumerate(bc.PURPOSE_HOURLY_WEEKDAY):
            assert sum(row) == pytest.approx(1.0, abs=1e-6), f"hour {h}"

    def test_every_weekend_hour_sums_to_one(self):
        for h, row in enumerate(bc.PURPOSE_HOURLY_WEEKEND):
            assert sum(row) == pytest.approx(1.0, abs=1e-6), f"hour {h}"

    def test_weekday_morning_commute_peak_is_arbete_dominant(self):
        shares = bc.purpose_shares_for_hour(8, is_weekend=False)
        assert shares["arbete"] > 0.7

    def test_weekday_midday_is_service_dominant(self):
        shares = bc.purpose_shares_for_hour(11, is_weekend=False)
        assert shares["service"] > shares["arbete"]
        assert shares["service"] > shares["fritid"]

    def test_weekday_evening_has_more_fritid_than_morning_commute(self):
        morning = bc.purpose_shares_for_hour(8, is_weekend=False)
        evening = bc.purpose_shares_for_hour(20, is_weekend=False)
        assert evening["fritid"] > morning["fritid"]

    def test_weekend_arbete_share_stays_far_below_weekday_commute_peak(self):
        weekday_peak = bc.purpose_shares_for_hour(8, is_weekend=False)["arbete"]
        weekend_same_hour = bc.purpose_shares_for_hour(8, is_weekend=True)["arbete"]
        assert weekend_same_hour < weekday_peak / 2

    def test_weekend_fritid_rises_toward_evening(self):
        midday = bc.purpose_shares_for_hour(12, is_weekend=True)["fritid"]
        evening = bc.purpose_shares_for_hour(20, is_weekend=True)["fritid"]
        assert evening > midday

    def test_hour_wraps_modulo_24(self):
        assert bc.purpose_shares_for_hour(24, is_weekend=False) == \
            bc.purpose_shares_for_hour(0, is_weekend=False)

    def test_weekday_weighted_by_real_shape_reproduces_daily_average(self):
        """Load-bearing: the hourly table isn't just plausible-looking —
        weighted by our own real measured hourly shape, it must integrate
        back to PURPOSE_SHARES_WEEKDAY exactly (how it was solved)."""
        shape = bc.daily_shape(is_weekend=False)
        totals = {c: 0.0 for c in bc.PURPOSE_CATEGORIES}
        for h in range(24):
            shares = bc.purpose_shares_for_hour(h, is_weekend=False)
            for c in bc.PURPOSE_CATEGORIES:
                totals[c] += shape[h] * shares[c]
        for c in bc.PURPOSE_CATEGORIES:
            assert totals[c] == pytest.approx(bc.PURPOSE_SHARES_WEEKDAY[c], abs=0.01)

    def test_weekend_weighted_by_real_shape_reproduces_daily_average(self):
        shape = bc.daily_shape(is_weekend=True)
        totals = {c: 0.0 for c in bc.PURPOSE_CATEGORIES}
        for h in range(24):
            shares = bc.purpose_shares_for_hour(h, is_weekend=True)
            for c in bc.PURPOSE_CATEGORIES:
                totals[c] += shape[h] * shares[c]
        for c in bc.PURPOSE_CATEGORIES:
            assert totals[c] == pytest.approx(bc.PURPOSE_SHARES_WEEKEND[c], abs=0.01)


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

    def test_is_weekend_reads_the_weekend_profile_not_weekday(self, tmp_path, monkeypatch):
        """Found 2026-07-08: this always read 'weekday' regardless of which
        --date was actually being calibrated, even for a real Saturday/
        Sunday — normal_profile.json has carried a real 'weekend' profile
        (different shape: later start, broader single afternoon peak) the
        whole time, it just was never read."""
        profiles = {
            "edgeA": {"weekday": [0.0] * 96, "weekend": [0.0] * 96},
        }
        for i in range(32, 36):
            profiles["edgeA"]["weekday"][i] = 10.0   # weekday: hour 8 peak
        for i in range(64, 68):
            profiles["edgeA"]["weekend"][i] = 10.0   # weekend: hour 16 peak
        (tmp_path / "web" / "data").mkdir(parents=True)
        import json
        (tmp_path / "web" / "data" / "normal_profile.json").write_text(
            json.dumps({"profiles": profiles}))

        monkeypatch.chdir(tmp_path)
        weekday_shape = bc.daily_shape(is_weekend=False)
        weekend_shape = bc.daily_shape(is_weekend=True)
        assert weekday_shape[8] == pytest.approx(1.0)
        assert weekend_shape[16] == pytest.approx(1.0)
        assert weekend_shape[8] == pytest.approx(0.0)


class TestBlendDayShape:
    """--real-day-shape-file's measured/forecast shape, shrunk toward the
    weekday/weekend/holiday fallback rather than fully trusted (hedges
    against one day's sampling noise across just 6-7 sensors). Added
    2026-07-10."""

    def test_blend_weights_toward_real_by_default(self):
        real = np.zeros(24); real[10] = 1.0
        fallback = np.zeros(24); fallback[10] = 1.0
        blended = bc.blend_day_shape(real, fallback)
        assert blended[10] == pytest.approx(1.0)

    def test_real_and_fallback_disagreement_is_a_genuine_blend(self):
        real = np.zeros(24); real[10] = 1.0
        fallback = np.zeros(24); fallback[18] = 1.0
        blended = bc.blend_day_shape(real, fallback, weight=0.7)
        assert blended[10] == pytest.approx(0.7)
        assert blended[18] == pytest.approx(0.3)

    def test_result_sums_to_one(self):
        real = np.array([1.0, 2.0] + [0.0] * 22)
        fallback = np.array([0.0, 1.0, 1.0] + [0.0] * 21)
        blended = bc.blend_day_shape(real, fallback)
        assert blended.sum() == pytest.approx(1.0)


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


class TestDuarouterWeightArgs:
    """Congestion-feedback loop (build_sumo_demand.py --congestion-iterations):
    candidates must route by free-flow cost when no feedback yet exists, and
    by the prior iteration's MEASURED travel time once it does."""

    def test_no_weight_file_means_free_flow_routing(self):
        assert bc.duarouter_weight_args(None) == []

    def test_weight_file_adds_traveltime_attribute_args(self):
        args = bc.duarouter_weight_args("sumo/feedback_edgedata_0.xml")
        assert args == ["--weight-files", "sumo/feedback_edgedata_0.xml",
                        "--weight-attribute", "traveltime"]

    def test_weight_period_passed_through_for_time_varying_files(self):
        args = bc.duarouter_weight_args("sumo/feedback_weights_0.xml", 3600.0)
        assert args == ["--weight-files", "sumo/feedback_weights_0.xml",
                        "--weight-attribute", "traveltime",
                        "--weight-period", "3600.0"]


class TestGravityDistanceKm:
    """A bare degree-distance treats 1° lat and 1° lon as equal in km, which
    is wrong at Gothenburg's latitude (cos(57.7°)≈0.535) and overstates
    east-west distance ~1.87x relative to north-south — found 2026-07-06,
    biasing gravity-weighted OD generation and the assignment-prior field
    against E-W trips."""

    def test_one_degree_north_south_is_about_110_km(self):
        d = bc.gravity_distance_km(np.array([58.7]), np.array([11.5]), 57.7, 11.5)
        assert d[0] == pytest.approx(110.54, rel=0.01)

    def test_one_degree_east_west_is_cos_corrected_not_110_km(self):
        d = bc.gravity_distance_km(np.array([57.7]), np.array([12.5]), 57.7, 11.5)
        assert d[0] == pytest.approx(111.32 * np.cos(np.radians(57.7)), rel=0.01)
        assert d[0] < 70.0   # NOT ~111 km, the bare-degree bug's answer

    def test_equal_lat_lon_offsets_give_unequal_km_distance(self):
        # Same 0.1 degree offset in each axis must NOT produce equal km
        # distances (that would mean the cos-correction silently vanished).
        d_ns = bc.gravity_distance_km(np.array([57.8]), np.array([11.5]), 57.7, 11.5)
        d_ew = bc.gravity_distance_km(np.array([57.7]), np.array([11.6]), 57.7, 11.5)
        assert d_ns[0] > d_ew[0]


class TestPurposeLengthScale:
    """Per-purpose kernel-scale table (2026-07-13, §4A step 1) — national
    RVU (Trafikanalys Resvanor i Sverige 2023, Tabell 3, car mode) purpose
    ratios shrunk 50% toward 1 (partial pooling, disclosed)."""

    def test_covers_every_purpose_category(self):
        for p in bc.PURPOSE_CATEGORIES:
            assert p in bc.PURPOSE_LENGTH_SCALE

    def test_ordering_matches_the_national_survey(self):
        # fritid trips are the longest, work trips the shortest — Tabell 3's
        # car-mode ordering (56 > 30 > 24 km) must survive the shrinkage.
        s = bc.PURPOSE_LENGTH_SCALE
        assert s["fritid"] > s["service"] > s["arbete"]

    def test_shrinkage_stays_well_inside_the_raw_national_ratios(self):
        # 50% pooling toward 1 (then weekday-mix normalization): no scale
        # may reach the raw national ratio — that would mean the shrinkage
        # silently vanished and the wide-CI national values were taken at
        # face value.
        assert bc._PURPOSE_RAW_RATIO["arbete"] < bc.PURPOSE_LENGTH_SCALE["arbete"] < 1.0
        assert bc.PURPOSE_LENGTH_SCALE["fritid"] < bc._PURPOSE_RAW_RATIO["fritid"]

    def test_average_weekday_mix_weighted_mean_is_exactly_one(self):
        # The normalization contract: under the average WEEKDAY purpose
        # mix, the scales redistribute length among purposes without
        # changing the aggregate weekday calibration at all.
        mean = sum(bc._WEEKDAY_AVG_MIX[p] * bc.PURPOSE_LENGTH_SCALE[p]
                   for p in bc.PURPOSE_CATEGORIES)
        assert mean == pytest.approx(1.0, abs=1e-9)

    def test_weekend_mix_gives_longer_trips_by_design(self):
        # Leisure dominates weekends and leisure trips are the long ones —
        # the survey-implied day-type signal must survive normalization,
        # not be flattened away.
        we = {"arbete": 0.232, "service": 0.408, "fritid": 0.361}
        mean = sum(we[p] * bc.PURPOSE_LENGTH_SCALE[p] for p in bc.PURPOSE_CATEGORIES)
        assert 1.05 < mean < 1.2

    def test_unknown_purpose_falls_back_to_unscaled(self):
        # I-E trips carry purpose="external" (no survey row) — callers use
        # .get(purpose, 1.0), so absence must not KeyError anywhere.
        assert "external" not in bc.PURPOSE_LENGTH_SCALE


class TestDeterrenceWeights:
    """Tanner/gamma kernel (2026-07-12; see IMPROVEMENT_PLAN.md): the
    pure negative exponential's mode at d=0, combined with the naturalness
    mask only admitting destinations beyond the sensor, made 'the edge
    immediately past the sensor' the modal trip destination."""

    def test_alpha_zero_is_exactly_the_old_exponential(self):
        d = np.array([0.0, 0.5, 1.8, 4.0, 9.0])
        old = np.exp(-d / 1.8)
        new = bc.deterrence_weights(d, 1.8, alpha=0.0)
        assert np.allclose(new, old)

    def test_positive_alpha_gives_zero_weight_at_zero_distance(self):
        d = np.array([0.0, 1.0])
        w = bc.deterrence_weights(d, 1.8, alpha=1.5)
        assert w[0] == 0.0
        assert w[1] > 0.0

    def test_mode_sits_at_alpha_times_beta(self):
        # f(d) = (d/b)^a exp(-d/b) has its maximum at d = a*b.
        d = np.linspace(0.01, 15.0, 3000)
        w = bc.deterrence_weights(d, 1.8, alpha=1.5)
        assert d[np.argmax(w)] == pytest.approx(1.5 * 1.8, abs=0.05)

    def test_no_nan_or_inf_in_output(self):
        d = np.array([0.0, 1e-12, 1.0, 1e6])
        w = bc.deterrence_weights(d, 1.8, alpha=2.0)
        assert np.isfinite(w).all()


class TestNaturalSensorMasks:
    """Per-sensor naturalness masks for one anchor — the building block of
    the conditional joint sampling that replaced per-sensor pre-commitment
    (2026-07-12). Must agree exactly with natural_far_end_weights."""

    def test_agrees_with_natural_far_end_weights_per_sensor(self):
        # Line graph: n0 -> n1 -> n2 -> n3 -> n4, sensor edge = n1->n2.
        import networkx as nx
        G = nx.MultiDiGraph()
        for u, v in [(0, 1), (1, 2), (2, 3), (3, 4)]:
            G.add_edge(u, v, key=0, length=100.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        dest_u_idx = np.array([node_idx[2], node_idx[3], node_idx[0]])
        m_info = {"1_2_0": (1, 2, 100.0)}

        masks = bc.natural_sensor_masks(D, node_idx, 1, m_info, dest_u_idx)
        base = np.ones(3)
        expected = bc.natural_far_end_weights(
            D, node_idx, 1, 1, 2, 100.0, dest_u_idx, base) > 0
        assert (masks["1_2_0"] == expected).all()
        # Sanity: from node 1, destinations starting at n2/n3 are reached
        # naturally THROUGH the sensor edge; n0 is behind it (unreachable
        # on this directed line graph).
        assert masks["1_2_0"][0] and masks["1_2_0"][1]
        assert not masks["1_2_0"][2]

    def test_union_over_sensors_is_or_of_individual_masks(self):
        import networkx as nx
        G = nx.MultiDiGraph()
        # Diamond: 0->1->3 and 0->2->3, two "sensors" on the two branches.
        for u, v in [(0, 1), (1, 3), (0, 2), (2, 3)]:
            G.add_edge(u, v, key=0, length=100.0)
        D, node_idx = bc.build_shortest_distance_matrix(G)
        dest_u_idx = np.array([node_idx[3], node_idx[1], node_idx[2]])
        m_info = {"0_1_0": (0, 1, 100.0), "0_2_0": (0, 2, 100.0)}

        masks = bc.natural_sensor_masks(D, node_idx, 0, m_info, dest_u_idx)
        union = masks["0_1_0"] | masks["0_2_0"]
        # dest at node 1 is only natural via sensor 0->1; node 2 only via
        # 0->2; node 3 via either — the union covers all three.
        assert masks["0_1_0"][1] and not masks["0_2_0"][1]
        assert masks["0_2_0"][2] and not masks["0_1_0"][2]
        assert union.all()


class TestDestinationSensorProximity:
    """The permanent guard metric for the destination-clustering bug."""

    def test_counts_destinations_within_radius(self):
        edge_latlon = {
            "sensor": (57.70, 11.97),
            "next_door": (57.7001, 11.9701),   # ~15 m away
            "far": (57.75, 12.05),             # km away
        }
        result = bc.destination_sensor_proximity(
            ["next_door", "far", "far"], edge_latlon, ["sensor"], radius_m=200.0)
        assert result["n"] == 3
        assert result["pct_within"] == pytest.approx(33.3, abs=0.1)
        # baseline: 2 of 3 edges in the network are within 200 m (the
        # sensor itself + next_door)
        assert result["baseline_pct_within"] == pytest.approx(66.7, abs=0.1)

    def test_no_sensors_degrades_to_none_not_crash(self):
        result = bc.destination_sensor_proximity(
            ["a"], {"a": (57.7, 11.97)}, [], radius_m=200.0)
        assert result["pct_within"] is None

    def test_unknown_destination_edges_are_excluded_from_n(self):
        edge_latlon = {"sensor": (57.70, 11.97), "known": (57.71, 11.98)}
        result = bc.destination_sensor_proximity(
            ["known", "not_in_network"], edge_latlon, ["sensor"])
        assert result["n"] == 1


class TestTripLengthFit:
    """RVU Västra Götaland's trip-length bins (p.12 table 2: 0-1/1.1-5/
    5.1-10/>10 km = 9/31/19/41%) were documented since 2026-07-05 as having
    replaced GEH-only scoring in calibrate_theta.py's theta search — but
    calibrate_theta.py never actually implemented that fit (confirmed
    2026-07-08 by reading its committed history: only ever GEH-based).
    This is the first real implementation."""

    def test_all_trips_in_shortest_bin_gives_shares_1_0_0(self):
        fit = bc.trip_length_fit([0.5, 0.8, 0.3])
        assert fit["shares"] == [1.0, 0.0, 0.0]
        assert fit["over_10km_pct"] == 0.0

    def test_matches_rvu_exactly_gives_zero_l1_distance(self):
        # RVU short-bin shares renormalized: 9/59, 31/59, 19/59
        lengths = [0.5] * 9 + [3.0] * 31 + [7.0] * 19   # exact real proportions
        fit = bc.trip_length_fit(lengths)
        assert fit["l1_distance"] == pytest.approx(0.0, abs=1e-6)

    def test_over_10km_trips_excluded_from_share_denominator(self):
        # 10 trips at 0.5km (short bin) + 10 trips at 50km (excluded) ->
        # shares among the SHORT bins only must still be [1.0, 0.0, 0.0],
        # not diluted by the long trips that can't occur locally.
        fit = bc.trip_length_fit([0.5] * 10 + [50.0] * 10)
        assert fit["shares"] == [1.0, 0.0, 0.0]
        assert fit["over_10km_pct"] == 50.0

    def test_empty_input_does_not_crash(self):
        fit = bc.trip_length_fit([])
        assert fit["l1_distance"] == float("inf")
        assert fit["n"] == 0

    def test_rvu_short_bin_shares_sum_to_one(self):
        assert sum(bc.RVU_SHORT_BIN_SHARES) == pytest.approx(1.0)
