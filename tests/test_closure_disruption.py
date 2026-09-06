"""closure_disruption: who a closure displaces, and by how much.

Regression cover for two defects found reviewing the 2026-08-05 implementation:
the strike scan stopped at the FIRST closed edge (so a route blocked by a
later edge inside the window was missed), and only the q50 variant was ever
measured (so the q10/q90 direction-split spread never reached the ranking).
"""
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_scenario
from traffic_sim.simulation import closure_routing
from traffic_sim.simulation import disruption
from traffic_sim.simulation.disruption import (
    DestinationAccess,
    DestinationAccessResolver,
)


# A -> B -> C -> D on a straight chain, plus a parallel bypass A -> P -> D.
ADJ = {"A": ["B", "P"], "B": ["C"], "C": ["D"], "P": ["D"], "D": []}
TIME = {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0, "P": 60.0}
LEN = {"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0, "P": 900.0}


def _routes(tmp_path, vehicles):
    """vehicles: list of (id, depart, [edges])."""
    body = "".join(
        f'<vehicle id="{vid}" depart="{depart}">'
        f'<route edges="{" ".join(edges)}"/></vehicle>'
        for vid, depart, edges in vehicles)
    path = tmp_path / "calibrated.rou.xml"
    path.write_text(f"<routes>{body}</routes>")
    return path


def _run(path, closed, closures=()):
    return run_scenario.closure_disruption(
        path, set(closed), list(closures), TIME, LEN, adj=ADJ)


def test_empty_closure_returns_before_building_adjacency(tmp_path, monkeypatch):
    path = _routes(tmp_path, [("v", 0.0, ["A", "B"])])
    monkeypatch.setattr(
        run_scenario, "build_edge_graph",
        lambda *_args, **_kwargs: pytest.fail("adjacency should not be built"))

    assert run_scenario.closure_disruption(
        path, set(), [], TIME, LEN) is None
    assert run_scenario.closure_disruption_across_variants(
        [path], set(), [], TIME, LEN) is None


class TestWindowedMultiEdgeClosure:
    def test_a_later_closed_edge_inside_the_window_still_counts(self, tmp_path):
        """B has reopened at transit, while C still cannot be proven safe."""
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        window = [
            {"edge_id": "B", "begin_s": 0.0, "end_s": 5.0},
            {"edge_id": "C", "begin_s": 15.0, "end_s": 30.0},
        ]
        report = _run(path, ["B", "C"], window)
        assert report["vehicles_affected"] == 1, (
            "a route blocked by a later closed edge inside the window must be "
            "counted; scanning only as far as the first closed edge misses it")

    def test_future_window_is_conservatively_applicable(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        window = [
            {"edge_id": edge, "begin_s": 500.0, "end_s": 600.0}
            for edge in ("B", "C")
        ]
        assert _run(path, ["B", "C"], window)["vehicles_affected"] == 1

    def test_already_ended_windows_do_not_count(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        window = [
            {"edge_id": edge, "begin_s": 0.0, "end_s": 5.0}
            for edge in ("B", "C")
        ]
        assert _run(path, ["B", "C"], window)["vehicles_affected"] == 0

    def test_costing_population_equals_the_route_writer_population(
            self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        window = [{"edge_id": "B", "begin_s": 500.0, "end_s": 600.0}]
        priced = _run(path, ["B"], window)
        written = closure_routing.rewrite_route_file(
            path, ["B"], tmp_path / "rewritten.rou.xml", ADJ,
            edge_travel_s=TIME, closures=window)

        assert priced["vehicles_affected"] == written.rerouted + written.denied
        assert priced["vehicles_no_detour"] == written.denied

    def test_no_window_means_the_whole_day(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        assert _run(path, ["C"])["vehicles_affected"] == 1


class TestSeverity:
    def test_destination_access_uses_lane_not_edge_centre_shape(self, tmp_path):
        network = tmp_path / "net.net.xml"
        network.write_text(
            "<net>"
            '<edge id="closed" shape="0,100 10,100">'
            '<lane id="closed_0" length="10" speed="10" '
            'shape="0,0 10,0"/></edge>'
            '<edge id="near" shape="0,100 10,100">'
            '<lane id="near_0" length="10" speed="10" '
            'shape="0,1 10,1"/></edge>'
            "</net>", encoding="utf-8")
        resolver = DestinationAccessResolver(
            network, permitted_edges={"near"}, radius_m=2.0)

        candidates = resolver.candidates(
            "closed", "5", frozenset({"closed"}))

        assert candidates == (DestinationAccess("near", 5.0, 1.0),)

    def test_access_distance_is_measured_after_endpoint_inset(self):
        geometry = disruption._EdgeGeometry(
            edge_id="edge", points=((0.0, 0.0), (10.0, 0.0)),
            shape_length_m=10.0, lane_length_m=10.0,
            bounds=(0.0, 0.0, 10.0, 0.0))

        position, distance = disruption._project_to_geometry(
            (0.0, 1.0), geometry)

        assert position == 2.0
        assert distance == pytest.approx(5 ** 0.5)

    def test_relocation_endpoint_adjustment_uses_both_edge_speeds(self):
        seconds, metres = disruption._relocation_endpoint_adjustment(
            "old", "8", DestinationAccess("new", 3.0, 1.0),
            {"old": 1.0, "new": 6.0},
            {"old": 10.0, "new": 30.0},
        )

        # Old tail: 2 m at 10 m/s. New tail: 27 m at 5 m/s.
        assert seconds == pytest.approx(0.2 - 5.4)
        assert metres == pytest.approx(2.0 - 27.0)

    @pytest.mark.parametrize(
        "raw,expected", [(None, 10.0), ("max", 10.0), ("-2", 8.0),
                         ("12", 10.0), ("-12", 0.0)])
    def test_arrival_position_matches_sumo_border_rules(self, raw, expected):
        assert disruption._normalised_arrival_position(raw, 10.0) == expected

    @staticmethod
    def _stub_pricer(base, detour):
        class _Pricer:
            edge_length = True

            @staticmethod
            def path_cost(origin, destination, banned):
                return detour if banned else base
        return _Pricer()

    def _severity(self, base, detour, endpoint_s=0.0, endpoint_m=0.0):
        movement = (("o", "old"), ("o", "new"), frozenset({"x"}), 1.0,
                    endpoint_s, endpoint_m)
        return disruption._report(
            considered=1, affected=1, denied=0, severed=0,
            od_counts=Counter({movement: 1}),
            pricer=self._stub_pricer(base, detour),
            assumed_delay_s=disruption.MAX_ASSUMED_CONGESTION_DELAY_S,
        )

    def test_added_time_survives_a_detour_that_is_shorter_but_slower(self):
        """The defect this replaces. Closing an edge can push a vehicle onto a
        route that is slower AND shorter -- measured at +7.0 s / -197.3 m on
        the tracked archive. Time and distance used to be zeroed TOGETHER
        whenever either was non-positive, so that real added time vanished."""
        report = self._severity(base=(100.0, 500.0), detour=(107.0, 480.0))

        # The report rounds vehicle-hours to four decimals.
        assert report["added_vehicle_hours"] == round(7.0 / 3600, 4)
        assert report["added_vehicle_hours"] > 0.0
        assert report["added_metres_total"] == 0.0

    def test_each_currency_is_floored_independently_at_zero(self):
        # No time added, 5 m added: both are reported as ADDED quantities and
        # the ranking contract forbids negative fields, so each floors alone.
        report = self._severity(base=(10.0, 10.0), detour=(11.0, 20.0),
                                endpoint_s=-2.0, endpoint_m=-5.0)

        assert report["added_vehicle_hours"] == 0.0
        assert report["added_metres_total"] == 5.0

    def test_detour_is_measured_against_the_optimal_route_not_the_taken_one(
            self, tmp_path):
        """Both sides must be optimal. The chain costs 40 s; the bypass costs
        A+P+D = 80 s, so closing C adds exactly 40 s."""
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        report = _run(path, ["C"])
        assert report["vehicles_affected"] == 1
        assert report["added_vehicle_hours"] == pytest.approx(40.0 / 3600, abs=1e-4)
        assert report["added_metres_total"] == pytest.approx(700.0)

    def test_a_severed_destination_is_counted_separately(self, tmp_path):
        """Closing both the chain and the bypass leaves D unreachable. That is
        not 'more delay' and must not land in the added-time total."""
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        report = _run(path, ["C", "P"])
        assert report["vehicles_no_detour"] == 1
        assert report["added_vehicle_hours"] == 0.0

    def test_unaffected_vehicles_are_counted_but_not_charged(self, tmp_path):
        path = _routes(tmp_path, [("hit", 0.0, ["A", "B", "C", "D"]),
                                  ("miss", 0.0, ["A", "P", "D"])])
        report = _run(path, ["C"])
        assert report["vehicles_considered"] == 2
        assert report["vehicles_affected"] == 1

    def test_closed_destination_is_priced_to_nearest_reachable_access(
            self, tmp_path):
        network = tmp_path / "net.net.xml"
        network.write_text(
            "<net>"
            '<edge id="origin"><lane id="origin_0" length="10" speed="10" '
            'shape="0,0 10,0"/></edge>'
            '<edge id="closed"><lane id="closed_0" length="10" speed="10" '
            'shape="10,0 20,0"/></edge>'
            '<edge id="near"><lane id="near_0" length="30" speed="10" '
            'shape="15,1 25,1"/></edge>'
            '<connection from="origin" to="closed"/>'
            '<connection from="origin" to="near"/>'
            "</net>", encoding="utf-8")
        route_path = tmp_path / "calibrated.rou.xml"
        route_path.write_text(
            '<routes><vehicle id="v" depart="0" arrivalPos="8">'
            '<route edges="origin closed"/></vehicle></routes>',
            encoding="utf-8")
        adjacency = {"origin": ["closed", "near"], "closed": [], "near": []}
        resolver = DestinationAccessResolver(
            network, permitted_edges=adjacency, radius_m=2.0)

        report = run_scenario.closure_disruption(
            route_path, {"closed"}, [],
            {"origin": 1.0, "closed": 1.0, "near": 3.0},
            {"origin": 10.0, "closed": 10.0, "near": 30.0},
            adj=adjacency, destination_access=resolver)

        assert report["vehicles_no_detour"] == 0
        assert report["vehicles_severed_destination"] == 0
        assert report["vehicles_destination_relocated"] == 1
        assert report["destination_relocation_metres_total"] == 1.0
        # Full-edge scoring would charge 2 s / 20 m. The replacement polyline
        # position scales to 9 m on its declared 30 m lane, so the actual
        # endpoint-aware delta is only 0.1 s / 1 m.
        assert report["added_vehicle_hours"] == pytest.approx(
            0.1 / 3600, abs=1e-4)
        assert report["added_metres_total"] == 1.0


class TestGuards:
    def test_no_closed_edges_yields_no_report(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B"])])
        assert _run(path, []) is None


class TestGroupedRoutingEquivalence:
    """The grouped implementation must remain identical to its old oracle."""

    @pytest.mark.parametrize(
        "closed,closures",
        [
            (["C"], []),
            (["C", "P"], []),
            (["A"], []),
            (["B", "C"], [
                {"edge_id": "B", "begin_s": 0.0, "end_s": 5.0},
                {"edge_id": "C", "begin_s": 15.0, "end_s": 30.0},
            ]),
            (["B", "C"], [
                {"edge_id": "B", "begin_s": 500.0, "end_s": 600.0},
                {"edge_id": "C", "begin_s": 500.0, "end_s": 600.0},
            ]),
        ],
    )
    def test_grouped_matches_the_retained_per_od_oracle(
            self, tmp_path, closed, closures):
        path = _routes(tmp_path, [
            ("first", 0.0, ["A", "B", "C", "D"]),
            ("same-od", 5.0, ["A", "B", "C", "D"]),
            ("bypass", 0.0, ["A", "P", "D"]),
            ("denied", 0.0, ["C", "D"]),
            ("short", 0.0, ["B", "C"]),
        ])
        grouped = run_scenario.closure_disruption(
            path, set(closed), closures, TIME, LEN, adj=ADJ)
        reference = run_scenario.reference_closure_disruption(
            path, set(closed), closures, TIME, LEN, adj=ADJ)
        assert grouped == reference

    def test_common_origins_share_traversals(self, monkeypatch):
        calls = 0
        original = run_scenario.disruption_analysis._shortest_path_costs

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(
            run_scenario.disruption_analysis, "_shortest_path_costs", counted)
        pairs = [("A", "C"), ("A", "D"), ("B", "D")]
        result = run_scenario.disruption_analysis.grouped_path_costs(
            pairs, ADJ, TIME, frozenset())

        assert calls == 2
        assert set(result) == set(pairs)

    @pytest.mark.parametrize("banned", [frozenset(), frozenset({"C"})])
    def test_sparse_batch_matches_grouped_python(self, banned):
        pairs = [("A", "C"), ("A", "D"), ("B", "D"), ("D", "D")]
        sparse = run_scenario.disruption_analysis.SparsePathBatch(
            pairs, ADJ).path_costs(TIME, banned)
        grouped = run_scenario.disruption_analysis.grouped_path_costs(
            pairs, ADJ, TIME, banned)

        assert sparse.keys() == grouped.keys()
        for pair in pairs:
            if grouped[pair] is None:
                assert sparse[pair] is None
            else:
                assert sparse[pair] == pytest.approx(grouped[pair], abs=1e-9)


class TestDeniedDeparture:
    """A vehicle whose OWN FIRST edge is closed cannot start at all.

    Found 2026-08-06 while reclassifying denied departures from an integrity
    failure into an impact: _cheapest() only bans an edge when stepping ONTO
    it, so a route FROM a closed origin still priced as reachable. The closure
    therefore scored as free (added_vehicle_hours 0.0, vehicles_no_detour 0),
    which would let a closure denying 85 departures rank best once the SUMO
    gate stopped catching it.
    """

    def test_a_vehicle_departing_on_the_closed_edge_has_no_detour(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        report = _run(path, ["A"])
        assert report["vehicles_affected"] == 1
        assert report["vehicles_no_detour"] == 1, (
            "a denied departure must disqualify the closure in the ranking, "
            "not price as a free detour")
        assert report["added_vehicle_hours"] == 0.0

    def test_a_mid_route_closure_with_a_detour_still_prices_normally(self, tmp_path):
        """The guard must not swallow ordinary reroutes."""
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        report = _run(path, ["C"])
        assert report["vehicles_no_detour"] == 0
        assert report["added_vehicle_hours"] > 0


class TestSeveranceSplit:
    """`vehicles_no_detour` conflated two different facts.

    Stage 4 of the closure-integrity plan needs only one of them: a destination
    that becomes unreachable is a TOPOLOGY fact about the edge, while a denied
    departure is access the closure removes (C1, 2026-08-06) and exists on
    every street with traffic on it. Gating a survivability rule on the total
    would refuse to close any real street.
    """

    def test_a_denied_departure_is_reported_separately(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["C", "D"])])   # starts ON C
        report = _run(path, ["C"])
        assert report["vehicles_denied_departure"] == 1
        assert report["vehicles_severed_destination"] == 0

    def test_a_severed_destination_is_reported_separately(self, tmp_path):
        """B is the only way into C, so closing B strands anyone bound for C."""
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C"])])
        report = _run(path, ["B"])
        assert report["vehicles_denied_departure"] == 0
        assert report["vehicles_severed_destination"] == 1

    def test_the_halves_sum_to_the_published_total(self, tmp_path):
        """`vehicles_no_detour` keeps its exact meaning: every existing
        consumer — closure_ranking's disqualifier, published scenario JSON,
        frozen campaign artifacts — must be unaffected by the split."""
        path = _routes(tmp_path, [
            ("denied", 0.0, ["B", "C"]),
            ("severed", 0.0, ["A", "B", "C"]),
            ("detoured", 0.0, ["A", "B", "C", "D"]),
        ])
        report = _run(path, ["B"])
        assert (report["vehicles_denied_departure"]
                + report["vehicles_severed_destination"]
                == report["vehicles_no_detour"] == 2)

    def test_across_variants_the_split_takes_the_worst_case(self, tmp_path):
        q50 = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        severe = tmp_path / "calibrated_v1.rou.xml"
        severe.write_text(
            '<routes><vehicle id="v" depart="0">'
            '<route edges="A B C"/></vehicle></routes>')
        report = run_scenario.closure_disruption_across_variants(
            [q50, severe], {"B"}, [], TIME, LEN, adj=ADJ)
        assert report["vehicles_severed_destination"] == 1
        assert report["vehicles_denied_departure"] == 0


class TestCostAndWriterAgree:
    """Review finding 3: the ledger must price the journey SUMO will run.

    The cost used to ban the whole closed-edge set for every vehicle while
    the route writer built a per-vehicle banned set by fixed point. With
    per-edge windows the two answered different questions, and the cost
    could call a vehicle stranded on a detour the writer was about to
    publish.
    """

    # C is shut while this vehicle passes; P's window has already ended by
    # the time the same vehicle would reach it, so the bypass is legal.
    CLOSURES = [
        {"edge_id": "C", "begin_s": 0, "end_s": 100},
        {"edge_id": "P", "begin_s": 0, "end_s": 5},
    ]

    def test_a_reopened_bypass_is_a_detour_not_a_severed_destination(
            self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])

        report = _run(path, ["C", "P"], self.CLOSURES)

        # Baseline B+C+D = 30 s; the reopened bypass P+D = 70 s.
        assert report["vehicles_affected"] == 1
        assert report["vehicles_no_detour"] == 0
        assert report["vehicles_severed_destination"] == 0
        assert report["added_vehicle_hours"] == round(40.0 / 3600, 4)

    def test_the_writer_publishes_exactly_the_route_the_cost_priced(
            self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        out = tmp_path / "out.rou.xml"

        result = closure_routing.rewrite_route_file(
            path, ["C", "P"], out, ADJ, edge_travel_s=TIME,
            closures=self.CLOSURES)

        assert (result.unaffected, result.rerouted, result.denied) == (0, 1, 0)
        assert 'edges="A P D"' in out.read_text()

        # ...and it is the same decision object, not merely the same answer.
        resolver = disruption.ClosureRouteResolver(
            ADJ, TIME, LEN, frozenset({"C", "P"}))
        outcome = resolver.resolve(
            ["A", "B", "C", "D"], 0.0, None, self.CLOSURES)
        assert outcome.route == ("A", "P", "D")
        assert outcome.banned == frozenset({"C"})
        assert outcome.applicable == frozenset({"C"})
        assert outcome.reason is None

    def test_the_whole_closed_set_would_have_stranded_this_vehicle(self):
        """Pins WHY the old cost disagreed: banning both edges leaves no path,
        which is exactly what it used to price."""
        assert disruption.shortest_path_edges(
            ADJ, TIME, "A", "D", frozenset({"C", "P"})) is None


class TestDestinationClosedDuringReplanning:
    """A closure the ORIGINAL route beat but the detour does not.

    Found in review of the v8 pass. The destination was only offered a nearby
    access when it was closed from the outset. If the forced detour arrived
    late enough for the destination's OWN window to open, the fixed point
    banned it and the trip was denied `no_legal_path` -- with a legal route to
    an open access sitting right there. Denying it erases the vehicle from
    every other edge on its route, which is precisely what truncation (not
    deletion) exists to prevent.
    """

    ADJ = {"O": ["X", "Y"], "X": ["DEST"], "Y": ["DEST", "NEAR"],
           "DEST": [], "NEAR": []}
    TIME = {"O": 1.0, "X": 1.0, "Y": 50.0, "DEST": 10.0, "NEAR": 1.0}
    LEN = {"O": 10.0, "X": 10.0, "Y": 500.0, "DEST": 100.0, "NEAR": 10.0}
    # X is shut throughout. DEST opens its window at t=40: the original route
    # reaches DEST at t=2 and beats it, the forced detour via Y arrives at
    # t=51 and does not.
    CLOSURES = [{"edge_id": "X", "begin_s": 0, "end_s": 100},
                {"edge_id": "DEST", "begin_s": 40, "end_s": 100}]

    @staticmethod
    def _network(tmp_path):
        net = tmp_path / "net.net.xml"
        net.write_text(
            "<net>"
            '<edge id="O"><lane id="O_0" length="10" speed="10" '
            'shape="0,0 10,0"/></edge>'
            '<edge id="X"><lane id="X_0" length="10" speed="10" '
            'shape="10,0 20,0"/></edge>'
            '<edge id="Y"><lane id="Y_0" length="500" speed="10" '
            'shape="10,0 10,500"/></edge>'
            '<edge id="DEST"><lane id="DEST_0" length="100" speed="10" '
            'shape="20,0 120,0"/></edge>'
            '<edge id="NEAR"><lane id="NEAR_0" length="10" speed="10" '
            'shape="120,1 130,1"/></edge>'
            "</net>", encoding="utf-8")
        return net

    def _resolver(self, tmp_path):
        access = DestinationAccessResolver(
            self._network(tmp_path), permitted_edges=self.ADJ, radius_m=50.0)
        return disruption.ClosureRouteResolver(
            self.ADJ, self.TIME, self.LEN, frozenset({"X", "DEST"}),
            destination_access=access, max_assumed_delay_s=0.0)

    def test_a_destination_closed_only_on_the_detour_is_relocated(
            self, tmp_path):
        outcome = self._resolver(tmp_path).resolve(
            ["O", "X", "DEST"], 0.0, "95", self.CLOSURES)

        # The closure on DEST was invisible on the original route...
        assert outcome.applicable == frozenset({"X"})
        # ...and discovered once the detour made the vehicle arrive later.
        assert outcome.banned == frozenset({"X", "DEST"})
        assert outcome.reason is None
        assert outcome.route == ("O", "Y", "NEAR")
        assert outcome.access.edge_id == "NEAR"

    def test_it_is_denied_only_when_nothing_near_the_destination_is_reachable(
            self, tmp_path):
        access = DestinationAccessResolver(
            self._network(tmp_path), permitted_edges=self.ADJ, radius_m=50.0)
        resolver = disruption.ClosureRouteResolver(
            # NEAR is unreachable from O once Y is gone from the graph.
            {"O": ["X", "Y"], "X": ["DEST"], "Y": ["DEST"], "DEST": [],
             "NEAR": []},
            self.TIME, self.LEN, frozenset({"X", "DEST"}),
            destination_access=access, max_assumed_delay_s=0.0)

        outcome = resolver.resolve(
            ["O", "X", "DEST"], 0.0, "95", self.CLOSURES)

        assert outcome.route is None
        # The destination is what is shut -- not a generic routing failure.
        assert outcome.reason == disruption.DESTINATION_CLOSED

    def test_the_route_writer_makes_the_same_decision(self, tmp_path):
        """Finding 3: the writer must not keep its own destination logic."""
        route = tmp_path / "in.rou.xml"
        route.write_text(
            '<routes><vehicle id="v0" depart="0" arrivalPos="95">'
            '<route edges="O X DEST"/></vehicle></routes>', encoding="utf-8")
        out = tmp_path / "out.rou.xml"
        access = DestinationAccessResolver(
            self._network(tmp_path), permitted_edges=self.ADJ, radius_m=50.0)

        result = closure_routing.rewrite_route_file(
            route, ["X", "DEST"], out, self.ADJ, edge_travel_s=self.TIME,
            closures=self.CLOSURES, destination_access=access,
            max_assumed_delay_s=0.0)

        assert (result.unaffected, result.rerouted, result.denied) == (0, 1, 0)
        assert 'edges="O Y NEAR"' in out.read_text()
        assert len(result.destination_relocations) == 1
        assert result.destination_relocations[0].replacement_destination == "NEAR"

    def test_the_writer_routes_through_the_shared_decision(
            self, tmp_path, monkeypatch):
        """Structural: one implementation, not two that happen to agree."""
        seen = []
        original = disruption.ClosureRouteResolver.resolve

        def spy(self, edges, depart_s, arrival_pos, closures):
            seen.append(tuple(edges))
            return original(self, edges, depart_s, arrival_pos, closures)

        monkeypatch.setattr(
            disruption.ClosureRouteResolver, "resolve", spy)
        route = tmp_path / "in.rou.xml"
        route.write_text(
            '<routes><vehicle id="v0" depart="0" arrivalPos="95">'
            '<route edges="O X DEST"/></vehicle></routes>', encoding="utf-8")
        access = DestinationAccessResolver(
            self._network(tmp_path), permitted_edges=self.ADJ, radius_m=50.0)

        closure_routing.rewrite_route_file(
            route, ["X", "DEST"], tmp_path / "out.rou.xml", self.ADJ,
            edge_travel_s=self.TIME, closures=self.CLOSURES,
            destination_access=access, max_assumed_delay_s=0.0)

        assert seen == [("O", "X", "DEST")]


class TestDeclaredCongestionBound:
    """Finding 2: the margin is an assumption, so it must be visible and
    variable -- never an invisible constant baked into a published number."""

    def test_every_cost_record_carries_the_bound_it_was_produced_under(
            self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])

        report = run_scenario.closure_disruption(
            path, {"C"}, [], TIME, LEN, adj=ADJ, max_assumed_delay_s=1234.0)

        assert report["assumed_congestion_delay_s"] == 1234.0

    def test_the_default_is_the_declared_constant(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])

        report = _run(path, ["C"])

        assert (report["assumed_congestion_delay_s"]
                == disruption.MAX_ASSUMED_CONGESTION_DELAY_S)

    def test_the_bound_changes_which_vehicles_a_window_reaches(self, tmp_path):
        # Free-flow arrival at C is 20 s; the window opens at 1000 s.
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        window = [{"edge_id": "C", "begin_s": 1000, "end_s": 2000}]

        tight = run_scenario.closure_disruption(
            path, {"C"}, window, TIME, LEN, adj=ADJ, max_assumed_delay_s=100.0)
        loose = run_scenario.closure_disruption(
            path, {"C"}, window, TIME, LEN, adj=ADJ,
            max_assumed_delay_s=3600.0)

        assert tight["vehicles_affected"] == 0
        assert loose["vehicles_affected"] == 1
        # A sensitivity run must be able to tell the two apart from the record
        # alone, without knowing which call produced it.
        assert (tight["assumed_congestion_delay_s"]
                != loose["assumed_congestion_delay_s"])
