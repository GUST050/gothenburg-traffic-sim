"""Tests for traffic_sim.simulation.closure_routing.

Root-cause fix (2026-08-29): every affected vehicle's route is rewritten
before SUMO runs, from its original origin to its original destination,
along the deterministic fastest legal path excluding every closed edge
active during its transit. Only a destination-closed or genuinely
unreachable trip is denied -- never truncated, never left waiting, never
teleported. See traffic_sim/simulation/closure_routing.py's module
docstring for the full four-part root-cause analysis.
"""
from __future__ import annotations

import json
import re

import pytest

import run_scenario
from traffic_sim.simulation import closure_routing as cr
from traffic_sim.simulation import disruption
from traffic_sim.simulation.disruption import DestinationAccessResolver


# ── A small deterministic test network ───────────────────────────────────
#
#   a_b -> b_c -> c_d           direct path, cost 10+10+10 = 30
#   a_b -> b_e -> e_c -> c_d    detour,       cost 10+15+15+10 = 50
#   p_q -> q_r -> r_s           single path, no alternative at all
#   w_x -> x_y -> y_z           dead end; y_z is itself the "destination"
#
ADJACENCY = {
    "a_b": ["b_c", "b_e"],
    "b_c": ["c_d"],
    "b_e": ["e_c"],
    "e_c": ["c_d"],
    "c_d": [],
    "p_q": ["q_r"],
    "q_r": ["r_s"],
    "r_s": [],
    "w_x": ["x_y"],
    "x_y": ["y_z"],
    "y_z": [],
}
COSTS = {
    "a_b": 10.0, "b_c": 10.0, "c_d": 10.0, "b_e": 15.0, "e_c": 15.0,
    "p_q": 10.0, "q_r": 10.0, "r_s": 10.0,
    "w_x": 10.0, "x_y": 10.0, "y_z": 10.0,
}


def write_routes(path, vehicles: list[str]) -> None:
    with open(path, "w") as f:
        f.write("<routes>\n")
        for line in vehicles:
            f.write(line + "\n")
        f.write("</routes>\n")


def vehicles_by_id(path) -> dict[str, str]:
    text = open(path).read()
    out = {}
    for m in re.finditer(r'<vehicle id="([^"]+)"[^>]*>.*?edges="([^"]*)"',
                         text, re.DOTALL):
        out[m.group(1)] = m.group(2)
    return out


def _write_destination_access_net(path) -> None:
    path.write_text(
        "<net>"
        '<edge id="origin"><lane id="origin_0" index="0" speed="10" '
        'length="10" shape="0,0 10,0"/></edge>'
        '<edge id="closed"><lane id="closed_0" index="0" speed="10" '
        'length="10" shape="10,0 20,0"/></edge>'
        '<edge id="near"><lane id="near_0" index="0" speed="5" '
        'length="10" shape="15,1 25,1"/></edge>'
        '<connection from="origin" to="closed"/>'
        '<connection from="origin" to="near"/>'
        "</net>", encoding="utf-8")


class TestClosedDestinationNearbyAccess:
    def test_closed_destination_moves_to_nearest_open_arrival_position(
            self, tmp_path):
        network = tmp_path / "net.net.xml"
        _write_destination_access_net(network)
        resolver = DestinationAccessResolver(
            network, permitted_edges={"origin", "closed", "near"},
            radius_m=2.0)
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0" arrivalPos="8.00">'
            '<route edges="origin closed"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"

        result = cr.rewrite_route_file(
            route_path, ["closed"], out_path,
            {"origin": ["closed", "near"], "closed": [], "near": []},
            edge_travel_s={"origin": 1.0, "closed": 1.0, "near": 2.0},
            destination_access=resolver)

        assert (result.unaffected, result.rerouted, result.denied) == (0, 1, 0)
        assert vehicles_by_id(out_path)["v0"] == "origin near"
        assert 'arrivalPos="3.00"' in out_path.read_text()
        assert result.destination_relocations[0].to_dict() == {
            "vehicle_id": "v0",
            "original_origin": "origin",
            "original_destination": "closed",
            "replacement_destination": "near",
            "original_depart_s": 0.0,
            "original_arrival_pos": "8.00",
            "replacement_arrival_pos": 3.0,
            "access_distance_m": 1.0,
            "applicable_closed_edges": ["closed"],
        }

    def test_relocation_validator_recomputes_position_and_distance(
            self, tmp_path):
        network = tmp_path / "net.net.xml"
        _write_destination_access_net(network)
        resolver = DestinationAccessResolver(
            network, permitted_edges={"origin", "closed", "near"},
            radius_m=2.0)
        source = tmp_path / "in.rou.xml"
        output = tmp_path / "out.rou.xml"
        report_path = tmp_path / "access.json"
        write_routes(source, [
            '  <vehicle id="v0" depart="0" arrivalPos="8.00">'
            '<route edges="origin closed"/></vehicle>',
        ])
        result = cr.rewrite_route_file(
            source, ["closed"], output,
            {"origin": ["closed", "near"], "closed": [], "near": []},
            edge_travel_s={"origin": 1.0, "closed": 1.0, "near": 2.0},
            destination_access=resolver)
        identity = {
            "unit_id": "unit", "candidate_id": "candidate",
            "work_date": "2027-09-01", "demand_variant": "q90",
            "seed": 1002, "execution_arm": "cold",
            "vehicle_class": cr.DEFAULT_VCLASS,
        }
        cr.write_access_impact_report(
            report_path, result=result, close_edges=["closed"], closures=None,
            source_route_path=source, out_route_path=output,
            network_path=network, identity=identity)
        report = json.loads(report_path.read_text())

        def provenance(payload):
            return cr.RoutingProvenance(
                routing_policy_version=cr.POLICY_VERSION,
                access_impact_sha256="a" * 64,
                access_impact_semantic_sha256=(
                    cr.access_impact_semantic_sha256(payload)),
                transformed_route_sha256=payload["output_route_sha256"],
                rerouted_around_closure=1, denied_count=0, **identity)

        cr.validate_access_impact_report(
            report, provenance(report), transformed_route_path=output,
            network_path=network)

        report["destination_relocations"][0]["replacement_arrival_pos"] = 11.0
        with pytest.raises(cr.ClosureRoutingError, match="outside its edge"):
            cr.validate_access_impact_report(
                report, provenance(report), transformed_route_path=output,
                network_path=network)

        report["destination_relocations"][0]["replacement_arrival_pos"] = 3.0
        report["destination_relocations"][0]["access_distance_m"] = 0.0
        with pytest.raises(cr.ClosureRoutingError, match="disagrees with network"):
            cr.validate_access_impact_report(
                report, provenance(report), transformed_route_path=output,
                network_path=network)

    def test_closed_destination_without_nearby_access_remains_denied(
            self, tmp_path):
        network = tmp_path / "net.net.xml"
        _write_destination_access_net(network)
        resolver = DestinationAccessResolver(
            network, permitted_edges={"origin", "closed", "near"},
            radius_m=0.5)
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0" arrivalPos="8.00">'
            '<route edges="origin closed"/></vehicle>',
        ])

        result = cr.rewrite_route_file(
            route_path, ["closed"], tmp_path / "out.rou.xml",
            {"origin": ["closed", "near"], "closed": [], "near": []},
            edge_travel_s={"origin": 1.0, "closed": 1.0, "near": 2.0},
            destination_access=resolver)

        assert (result.rerouted, result.denied) == (0, 1)
        assert result.access_impact[0].reason == cr.DESTINATION_CLOSED
        assert result.destination_relocations == ()


class TestFastestClosureExcludingRerouting:
    def test_detourable_vehicle_is_rerouted_to_original_destination(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"

        result = cr.rewrite_route_file(
            route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)

        assert (result.unaffected, result.rerouted, result.denied) == (0, 1, 0)
        vehicles = vehicles_by_id(out_path)
        assert vehicles["v0"] == "a_b b_e e_c c_d"

    def test_deterministic_tie_break_by_edge_id(self, tmp_path):
        # Two equal-cost detours from f: f_g1 and f_g2, both leading to h.
        adj = {
            "f": ["f_g2", "f_g1", "closed"],
            "f_g1": ["h"], "f_g2": ["h"], "closed": ["h"], "h": [],
        }
        costs = {"f_g1": 5.0, "f_g2": 5.0, "closed": 5.0, "h": 5.0}
        path = cr.disruption_analysis.shortest_path_edges(
            adj, costs, "f", "h", frozenset({"closed"}))
        assert path == ["f", "f_g1", "h"]  # lexicographically smaller wins
        # Repeatable across calls (no hidden nondeterminism e.g. set order).
        for _ in range(5):
            assert cr.disruption_analysis.shortest_path_edges(
                adj, costs, "f", "h", frozenset({"closed"})) == path


class TestDenial:
    def test_destination_on_closed_edge_is_denied_not_truncated(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="w_x x_y y_z"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"

        result = cr.rewrite_route_file(
            route_path, ["y_z"], out_path, ADJACENCY, edge_travel_s=COSTS)

        assert (result.unaffected, result.rerouted, result.denied) == (0, 0, 1)
        assert "v0" not in vehicles_by_id(out_path)
        record = result.access_impact[0]
        assert record.reason == cr.DESTINATION_CLOSED
        assert record.original_origin == "w_x"
        assert record.original_destination == "y_z"
        assert record.applicable_closed_edges == ("y_z",)

    def test_genuinely_unreachable_destination_is_denied(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="p_q q_r r_s"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"

        result = cr.rewrite_route_file(
            route_path, ["q_r"], out_path, ADJACENCY, edge_travel_s=COSTS)

        assert (result.unaffected, result.rerouted, result.denied) == (0, 0, 1)
        assert "v0" not in vehicles_by_id(out_path)
        record = result.access_impact[0]
        assert record.reason == cr.NO_LEGAL_PATH
        assert record.original_origin == "p_q"
        assert record.original_destination == "r_s"

    def test_closed_origin_is_no_legal_path_not_a_silent_departure(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="p_q q_r r_s"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        result = cr.rewrite_route_file(
            route_path, ["p_q"], out_path, ADJACENCY, edge_travel_s=COSTS)
        assert result.denied == 1
        assert result.access_impact[0].reason == cr.NO_LEGAL_PATH


class TestWindowedDestinationDenial:
    """Review finding 2026-08-29: destination_closed used to fire on bare
    membership in close_edges_set, denying a trip whose destination closure
    window does not even apply to it (already over, or not yet reachable
    within the window per the timing invariant). It must only fire when the
    destination's OWN closure window is applicable to that trip.
    """

    def test_destination_denied_when_its_window_is_active(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="w_x x_y y_z"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        # y_z's window (0-100) has not ended by the free-flow arrival
        # estimate, so it is applicable -- correctly denied.
        result = cr.rewrite_route_file(
            route_path, ["y_z"], out_path, ADJACENCY, edge_travel_s=COSTS,
            closures=[{"edge_id": "y_z", "begin_s": 0, "end_s": 100}])
        assert (result.unaffected, result.rerouted, result.denied) == (0, 0, 1)
        assert result.access_impact[0].reason == cr.DESTINATION_CLOSED

    def test_destination_preserved_when_its_window_has_already_ended(
        self, tmp_path,
    ):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="w_x x_y y_z"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        # y_z's window (0-5) has already ended by the free-flow arrival
        # (depart 0 + cost(w_x)=10 + cost(x_y)=10 = 20 >= 5) -- provably
        # safe, so this vehicle is genuinely unaffected and must be
        # preserved byte-for-byte, not denied.
        result = cr.rewrite_route_file(
            route_path, ["y_z"], out_path, ADJACENCY, edge_travel_s=COSTS,
            closures=[{"edge_id": "y_z", "begin_s": 0, "end_s": 5}])
        assert (result.unaffected, result.rerouted, result.denied) == (1, 0, 0)
        assert vehicles_by_id(out_path)["v0"] == "w_x x_y y_z"


class TestUnaffectedRoutesArePreservedExactly:
    def test_untouched_vehicle_fragment_is_byte_identical(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        odd_fragment = (
            '  <vehicle id="keep_me" depart="12.34" departPos="5.00" '
            'arrivalPos="99.90">\n'
            '    <route edges="p_q q_r r_s"/>\n  </vehicle>')
        write_routes(route_path, [
            odd_fragment,
            '  <vehicle id="v0" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"

        cr.rewrite_route_file(
            route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)

        out_text = out_path.read_text()
        assert odd_fragment in out_text

    def test_route_before_a_window_that_has_not_yet_ended_is_still_rerouted(
        self, tmp_path,
    ):
        # b_c is closed 1000-2000s; this vehicle's FREE-FLOW arrival is at
        # t=10s, comfortably before the window opens. Congestion delay has
        # no proven upper bound, so arriving early under free-flow
        # conditions is not proof the vehicle clears the edge before the
        # window (still 990s away) actually opens -- it must be rerouted,
        # not left alone. See closure_routing._closures_overlapping.
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        closures = [{"edge_id": "b_c", "begin_s": 1000, "end_s": 2000}]

        result = cr.rewrite_route_file(
            route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS,
            closures=closures)

        assert (result.unaffected, result.rerouted, result.denied) == (0, 1, 0)
        assert vehicles_by_id(out_path)["v0"] == "a_b b_e e_c c_d"

    def test_route_after_a_window_that_has_already_ended_is_left_untouched(
        self, tmp_path,
    ):
        # b_c was closed 0-5s and has already reopened; this vehicle's
        # free-flow arrival at b_c is t=10s -- provably after the window,
        # since real transit is never FASTER than free flow (only ever
        # slower or equal), so the real arrival can only be later still.
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        closures = [{"edge_id": "b_c", "begin_s": 0, "end_s": 5}]

        result = cr.rewrite_route_file(
            route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS,
            closures=closures)

        assert (result.unaffected, result.rerouted, result.denied) == (1, 0, 0)
        assert vehicles_by_id(out_path)["v0"] == "a_b b_c c_d"

    def test_no_affected_vehicles_still_writes_output(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        result = cr.rewrite_route_file(
            route_path, ["nonexistent_edge"], out_path, ADJACENCY,
            edge_travel_s=COSTS)
        assert (result.unaffected, result.rerouted, result.denied) == (1, 0, 0)
        assert set(vehicles_by_id(out_path)) == {"v0"}


class TestWindowedFixedPoint:
    def test_banned_set_grows_until_the_detour_stabilizes(self, tmp_path):
        # a_b -> b_c (closed the whole run) OR a_b -> b_e -> e_c (e_c closed
        # only during a LATER window that the direct detour's own arrival
        # time falls inside) -> c_d. The planner must discover e_c's
        # applicability only after routing through it, then re-route again.
        adj = {
            "a_b": ["b_c", "b_e"],
            "b_c": ["c_d"],
            "b_e": ["e_c", "b_f"],
            "e_c": ["c_d"],
            "b_f": ["f_c"],
            "f_c": ["c_d"],
            "c_d": [],
        }
        costs = {"b_c": 5.0, "b_e": 5.0, "e_c": 5.0, "b_f": 20.0, "f_c": 20.0,
                 "c_d": 5.0}
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        closures = [
            {"edge_id": "b_c", "begin_s": 0, "end_s": 100000},
            # e_c's window covers the arrival time the FIRST detour (via
            # e_c, arriving at depart+cost(a_b)+cost(b_e)) produces, so it
            # must be excluded on the second iteration.
            {"edge_id": "e_c", "begin_s": 0, "end_s": 100000},
        ]

        result = cr.rewrite_route_file(
            route_path, ["b_c", "e_c"], out_path, adj, edge_travel_s=costs,
            closures=closures)

        assert result.rerouted == 1
        assert vehicles_by_id(out_path)["v0"] == "a_b b_e b_f f_c c_d"


class TestClosureTimingInvariant:
    """The predicate bounds occupancy on BOTH sides.

    Real transit is never faster than free flow, so
    `depart_s + free_flow_elapsed` is a true LOWER BOUND on occupancy: a
    window is provably missed once that bound has reached its end. The 900s
    additive margin removed in 2026-08-29 was unsound as an upper bound, but
    the rule that replaced it supplied no upper bound at all, which is not
    conservative -- it is vacuous. It asserted that a vehicle passing at
    00:30 might still occupy the edge at 22:00, so a window's cost grew with
    its END time rather than with the traffic inside it (measured: a
    22:00-24:00 shift scored 3025 vehicles where 72 cross it).

    `MAX_ASSUMED_CONGESTION_DELAY_S` now supplies the upper bound as a
    DECLARED modelling constant rather than a derived one, and `begin_s` is
    read. Occupancy lies in `[lower, lower + max_assumed_delay_s]`, and a
    window applies exactly when that interval overlaps `[begin_s, end_s)`.
    See the constant's own docstring in disruption.py for the argument.
    """

    def test_a_window_entirely_in_the_future_is_still_applicable(self):
        # The window (15-100) hasn't even opened yet at the free-flow
        # arrival estimate (10s); a margin-based rule would have called
        # this safe, but nothing bounds how much later real transit could
        # push the actual arrival, so it must be applicable.
        applicable = cr._closures_overlapping(
            ["a_b", "b_c", "c_d"], depart_s=0.0,
            closures=[{"edge_id": "b_c", "begin_s": 15, "end_s": 100}],
            close_edges_set=frozenset({"b_c"}), edge_travel_s=COSTS)
        assert applicable == frozenset({"b_c"})

    def test_a_window_that_has_already_ended_is_provably_not_applicable(
        self,
    ):
        # Free-flow arrival at b_c is 10; the window already ended at 5.
        # Real transit is never faster than free flow, so the true arrival
        # can only be >= 10, i.e. also after the window ended -- provably
        # safe, no margin needed.
        applicable = cr._closures_overlapping(
            ["a_b", "b_c", "c_d"], depart_s=0.0,
            closures=[{"edge_id": "b_c", "begin_s": 0, "end_s": 5}],
            close_edges_set=frozenset({"b_c"}), edge_travel_s=COSTS)
        assert applicable == frozenset()

    def test_a_window_beyond_the_declared_delay_bound_is_not_applicable(self):
        # Free-flow arrival at b_c is 10s and the window opens at 5000s.
        # Reaching it would take 4990s of congestion delay on one inner-city
        # edge, well past the declared one-hour bound, so this vehicle is
        # ahead of the roadworks rather than blocked by them.
        applicable = cr._closures_overlapping(
            ["a_b", "b_c", "c_d"], depart_s=0.0,
            closures=[{"edge_id": "b_c", "begin_s": 5000, "end_s": 6000}],
            close_edges_set=frozenset({"b_c"}), edge_travel_s=COSTS)
        assert applicable == frozenset()

    def test_the_declared_bound_is_exactly_what_separates_the_two_cases(self):
        # Same vehicle, same window, only the declared bound moves. This is
        # the knob the classification turns on -- nothing infers it from data.
        window = [{"edge_id": "b_c", "begin_s": 5000, "end_s": 6000}]
        events = (("b_c", 10.0),)
        assert disruption.applicable_closed_edges_from_events(
            events, window, frozenset({"b_c"}),
            max_assumed_delay_s=4989.0) == frozenset()
        assert disruption.applicable_closed_edges_from_events(
            events, window, frozenset({"b_c"}),
            max_assumed_delay_s=4990.0) == frozenset({"b_c"})

    def test_a_closure_start_time_changes_which_vehicles_are_blocked(self):
        # The defect this replaced: begin_s was never read, so two closures
        # sharing an end time blocked identical traffic no matter when they
        # started.
        late = cr._closures_overlapping(
            ["a_b", "b_c", "c_d"], depart_s=0.0,
            closures=[{"edge_id": "b_c", "begin_s": 90000, "end_s": 100000}],
            close_edges_set=frozenset({"b_c"}), edge_travel_s=COSTS)
        early = cr._closures_overlapping(
            ["a_b", "b_c", "c_d"], depart_s=0.0,
            closures=[{"edge_id": "b_c", "begin_s": 0, "end_s": 100000}],
            close_edges_set=frozenset({"b_c"}), edge_travel_s=COSTS)
        assert late == frozenset()
        assert early == frozenset({"b_c"})

    def test_a_record_without_begin_s_keeps_whole_run_semantics(self):
        # Legacy records carry only end_s; those must still mean "closed from
        # the start of the run", not "closed at an unknown time".
        applicable = cr._closures_overlapping(
            ["a_b", "b_c", "c_d"], depart_s=0.0,
            closures=[{"edge_id": "b_c", "end_s": 100000}],
            close_edges_set=frozenset({"b_c"}), edge_travel_s=COSTS)
        assert applicable == frozenset({"b_c"})

    def test_end_to_end_reroute_for_a_not_yet_open_window(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        result = cr.rewrite_route_file(
            route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS,
            closures=[{"edge_id": "b_c", "begin_s": 15, "end_s": 100}])
        assert (result.unaffected, result.rerouted, result.denied) == (0, 1, 0)
        assert vehicles_by_id(out_path)["v0"] == "a_b b_e e_c c_d"


class TestSumoPopulationIdentity:
    def test_exact_transformed_population_passes(self):
        cr.require_sumo_population_identity(
            7, loaded=7, inserted=7, trip_count=7, context="candidate/q90")

    @pytest.mark.parametrize(
        "loaded,inserted,trip_count", [(6, 7, 7), (7, 6, 7), (7, 7, 6)])
    def test_any_population_loss_is_a_hard_failure(
            self, loaded, inserted, trip_count):
        with pytest.raises(cr.ClosureRoutingError, match="population=7"):
            cr.require_sumo_population_identity(
                7, loaded=loaded, inserted=inserted, trip_count=trip_count,
                context="candidate/q90")


class TestFailsClosedOnUnsupportedShapes:
    def test_multiple_route_children_raise(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0">'
            '<route edges="a_b b_c c_d"/><route edges="a_b b_e e_c c_d"/>'
            '</vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        with pytest.raises(cr.ClosureRoutingError):
            cr.rewrite_route_file(
                route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)

    def test_missing_id_raises(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        with pytest.raises(cr.ClosureRoutingError):
            cr.rewrite_route_file(
                route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)

    def test_rewrite_requires_at_least_one_closed_edge(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        with pytest.raises(cr.ClosureRoutingError):
            cr.rewrite_route_file(
                route_path, [], tmp_path / "out.rou.xml", ADJACENCY,
                edge_travel_s=COSTS)


class TestSingleVehicleCategoryLegality:
    """Finding 3 (review repair batch, 2026-08-29): this project models
    exactly one vehicle category. `closure_routing` must not invent a
    per-trip vClass/vType feature, but it must fail closed rather than
    silently route a vehicle whose declared type this policy cannot prove
    is that one category. See `_check_vehicle_class`'s docstring for why
    this only ever fires on input this pipeline has never produced."""

    def test_vehicle_with_no_type_attribute_routes_normally(self, tmp_path):
        # The overwhelming production case: no <vType>, no type= at all --
        # SUMO's own implicit default vType, whose vClass is DEFAULT_VCLASS.
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        result = cr.rewrite_route_file(
            route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)
        assert result.rerouted == 1

    def test_vehicle_type_referencing_a_passenger_vtype_routes_normally(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vType id="car" vClass="passenger"/>',
            '  <vehicle id="v0" type="car" depart="0">'
            '<route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        result = cr.rewrite_route_file(
            route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)
        assert result.rerouted == 1

    def test_vehicle_type_with_no_declared_vclass_defaults_to_passenger(self, tmp_path):
        # SUMO's own default for an undeclared vClass on a <vType> IS
        # DEFAULT_VCLASS -- a bare <vType id="car"/> is compatible.
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vType id="car"/>',
            '  <vehicle id="v0" type="car" depart="0">'
            '<route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        result = cr.rewrite_route_file(
            route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)
        assert result.rerouted == 1

    def test_vehicle_type_referencing_an_incompatible_vclass_fails_closed(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vType id="bike" vClass="bicycle"/>',
            '  <vehicle id="v0" type="bike" depart="0">'
            '<route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        with pytest.raises(cr.ClosureRoutingError, match="bike"):
            cr.rewrite_route_file(
                route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)

    def test_vehicle_type_referencing_an_undeclared_vtype_fails_closed(self, tmp_path):
        # No <vType id="ghost"> exists anywhere in the file -- this policy
        # cannot prove legality for a type it never saw declared, so it
        # must not fall back to "assume it's fine".
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" type="ghost" depart="0">'
            '<route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        with pytest.raises(cr.ClosureRoutingError, match="ghost"):
            cr.rewrite_route_file(
                route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)

    def test_incompatible_vehicle_fails_closed_even_when_unaffected_by_closure(
            self, tmp_path):
        # Fail-closed applies to every vehicle fragment in the file, not
        # just the ones this closure would otherwise touch -- an
        # unprovable declaration must never be silently accepted just
        # because its own route happens not to cross a closed edge.
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vType id="bike" vClass="bicycle"/>',
            '  <vehicle id="v0" type="bike" depart="0">'
            '<route edges="p_q q_r r_s"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        with pytest.raises(cr.ClosureRoutingError):
            cr.rewrite_route_file(
                route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)

    def test_build_edge_graph_excludes_a_lane_restricted_to_another_class(
            self, monkeypatch, tmp_path):
        """End-to-end: `run_scenario.build_edge_graph` (the adjacency every
        production caller hands to closure_routing) must not offer a
        detour through a lane the single modeled vClass may not use."""
        import run_scenario
        net_path = tmp_path / "net.net.xml"
        net_path.write_text(
            '<net>'
            '<edge id="a_b" from="0" to="1" length="10" speed="5">'
            '<lane id="a_b_0" index="0" length="10" speed="5" shape="0,0 10,0"/>'
            '</edge>'
            '<edge id="b_c" from="1" to="2" length="10" speed="5">'
            '<lane id="b_c_0" index="0" length="10" speed="5" shape="10,0 20,0"/>'
            '</edge>'
            '<edge id="b_bike" from="1" to="3" length="10" speed="5">'
            '<lane id="b_bike_0" index="0" length="10" speed="5" '
            'shape="10,0 10,10" disallow="passenger"/>'
            '</edge>'
            '<connection from="a_b" to="b_c" fromLane="0" toLane="0"/>'
            '<connection from="a_b" to="b_bike" fromLane="0" toLane="0"/>'
            '</net>')
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        adj = run_scenario.build_edge_graph(set())
        assert adj["a_b"] == ["b_c"]


class TestRoutingProvenance:
    """Finding 4 (review repair batch, 2026-08-29): binds unit/schedule
    identity, date, variant, seed, the single-category vehicle class, the
    routing policy version, and the resolvable access-report digest into
    one validated, tamper-checked record instead of a free-form dict."""

    VALID = dict(
        routing_policy_version=cr.POLICY_VERSION,
        vehicle_class=cr.DEFAULT_VCLASS,
        unit_id="daily-unit-24737391111be0e137537df7",
        candidate_id="ui-monthly-12hg8f3",
        work_date="2026-08-01",
        demand_variant="q50",
        seed=1000,
        execution_arm="cold",
        access_impact_sha256="a" * 64,
        access_impact_semantic_sha256="c" * 64,
        transformed_route_sha256="b" * 64,
        rerouted_around_closure=3,
        denied_count=1,
    )

    def test_round_trips_through_to_dict_from_dict(self):
        record = cr.RoutingProvenance(**self.VALID)
        assert cr.RoutingProvenance.from_dict(record.to_dict()) == record

    def test_stale_policy_version_is_tampering_and_is_rejected(self):
        tampered = dict(self.VALID, routing_policy_version="closure_origin_routing_v2")
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance(**tampered)

    def test_wrong_vehicle_class_is_rejected(self):
        # This project models exactly one vehicle category; a record
        # claiming another class cannot be a routing_provenance this
        # policy actually produced.
        tampered = dict(self.VALID, vehicle_class="bicycle")
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance(**tampered)

    def test_negative_denied_count_is_rejected(self):
        tampered = dict(self.VALID, denied_count=-1)
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance(**tampered)

    def test_invalid_execution_arm_is_rejected(self):
        tampered = dict(self.VALID, execution_arm="lukewarm")
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance(**tampered)

    def test_missing_unit_id_is_rejected(self):
        tampered = dict(self.VALID, unit_id="")
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance(**tampered)

    def test_null_access_impact_digest_is_rejected(self):
        # v3 permitted `None` here; v4 requires it -- see the POLICY_VERSION
        # v3->v4 note (review finding 1).
        tampered = dict(self.VALID, access_impact_sha256=None)
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance(**tampered)

    def test_null_transformed_route_digest_is_rejected(self):
        tampered = dict(self.VALID, transformed_route_sha256=None)
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance(**tampered)

    def test_non_hex_access_impact_digest_is_rejected(self):
        # Length-64 alone is not proof of a real digest -- must be lowercase
        # hex specifically (review finding 1: "not merely checked for
        # length").
        tampered = dict(self.VALID, access_impact_sha256="z" * 64)
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance(**tampered)

    def test_uppercase_hex_digest_is_rejected(self):
        tampered = dict(self.VALID, transformed_route_sha256="B" * 64)
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance(**tampered)

    def test_short_digest_is_rejected(self):
        tampered = dict(self.VALID, access_impact_sha256="a" * 63)
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance(**tampered)

    def test_legacy_dict_missing_new_fields_is_incompatible(self):
        # A pre-finding-4 routing_provenance dict (just policy_version,
        # access_impact_sha256, rerouted_around_closure) must never satisfy
        # the current schema's lookup.
        legacy = {
            "routing_policy_version": cr.POLICY_VERSION,
            "access_impact_sha256": "a" * 64,
            "rerouted_around_closure": 3,
        }
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance.from_dict(legacy)

    def test_unexpected_extra_field_is_incompatible(self):
        extra = dict(self.VALID, unexpected_field="x")
        with pytest.raises(cr.ClosureRoutingError):
            cr.RoutingProvenance.from_dict(extra)


class TestAccessImpactEvidence:
    def test_report_has_stable_provenance_fields(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="w_x x_y y_z"/></vehicle>',
            '  <vehicle id="v1" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        report_path = tmp_path / "report.json"

        cr.prepare_route_file(
            route_path, ["y_z", "b_c"], out_path, ADJACENCY,
            edge_travel_s=COSTS, access_impact_path=report_path)

        payload = json.loads(report_path.read_text())
        assert payload["kind"] == cr.ACCESS_IMPACT_DIAGNOSTIC_SCHEMA
        assert payload["schema_version"] == (
            cr.ACCESS_IMPACT_DIAGNOSTIC_SCHEMA_VERSION)
        assert payload["policy_version"] == cr.POLICY_VERSION
        assert payload["summary"] == {
            "unaffected": 0,
            "rerouted": 1,
            "destination_relocated": 0,
            "denied": 1,
        }
        assert payload["destination_relocations"] == []
        assert len(payload["access_impact"]) == 1
        record = payload["access_impact"][0]
        assert record["vehicle_id"] == "v0"
        assert record["reason"] == "destination_closed"
        assert record["original_origin"] == "w_x"
        assert record["original_destination"] == "y_z"
        assert re.fullmatch(r"[0-9a-f]{64}", payload["source_route_sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", payload["output_route_sha256"])

    def test_identity_is_bound_verbatim_into_the_evidence_file(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="w_x x_y y_z"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        report_path = tmp_path / "report.json"
        identity = {
            "candidate_id": "sched-abc123",
            "demand_variant": "q50",
            "seed": 1000,
            "work_date": "2027-09-14",
            "execution_arm": "cold",
        }

        cr.prepare_route_file(
            route_path, ["y_z"], out_path, ADJACENCY,
            edge_travel_s=COSTS, access_impact_path=report_path,
            identity=identity)

        payload = json.loads(report_path.read_text())
        assert payload["identity"] == identity
        assert payload["kind"] == cr.ACCESS_IMPACT_DIAGNOSTIC_SCHEMA

    def test_complete_monthly_identity_uses_strict_durable_schema(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="w_x x_y y_z"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        report_path = tmp_path / "report.json"
        identity = {
            "unit_id": "unit-a", "candidate_id": "candidate-a",
            "work_date": "2027-09-14", "demand_variant": "q50",
            "seed": 1000, "execution_arm": "cold",
            "vehicle_class": cr.DEFAULT_VCLASS,
        }

        cr.prepare_route_file(
            route_path, ["y_z"], out_path, ADJACENCY,
            edge_travel_s=COSTS, access_impact_path=report_path,
            identity=identity)

        payload = json.loads(report_path.read_text())
        assert payload["kind"] == cr.ACCESS_IMPACT_SCHEMA
        assert payload["schema_version"] == cr.ACCESS_IMPACT_SCHEMA_VERSION
        assert payload["identity"] == identity

    def test_no_identity_writes_none_not_a_missing_key(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="w_x x_y y_z"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"
        report_path = tmp_path / "report.json"

        cr.prepare_route_file(
            route_path, ["y_z"], out_path, ADJACENCY,
            edge_travel_s=COSTS, access_impact_path=report_path)

        payload = json.loads(report_path.read_text())
        assert payload["identity"] is None

    def test_old_policy_versions_can_never_satisfy_the_current_lookup(self):
        # The routing rule has changed repeatedly (destination-window
        # awareness, the timing invariant, single-vClass permission
        # filtering and fail-closed vehicle types, destination access, and
        # now the two-sided occupancy bound that reads begin_s), so an
        # evidence/cache reader keyed on ANY old version string must never
        # match current output.
        for superseded in range(1, 8):
            assert cr.POLICY_VERSION != f"closure_origin_routing_v{superseded}"
        assert cr.POLICY_VERSION == "closure_origin_routing_v8"

    def test_denied_count_matches_access_impact_length(self):
        with pytest.raises(cr.ClosureRoutingError):
            cr.ClosureRoutingResult(
                unaffected=0, rerouted=0, denied=1, access_impact=())

    def test_rerouted_count_matches_rerouted_vehicle_id_length(self):
        with pytest.raises(cr.ClosureRoutingError):
            cr.ClosureRoutingResult(
                unaffected=0, rerouted=1, denied=0, access_impact=(),
                rerouted_vehicle_ids=())


class TestProductionWiringUsesTheNewPolicy:
    """Guards the root requirement: production closure execution must no
    longer depend on the retired truncate_stranded_vehicles path."""

    def test_run_scenario_source_does_not_call_the_retired_function_in_prepare(self):
        import inspect
        source = inspect.getsource(run_scenario.prepare_variant_job)
        assert "truncate_stranded_vehicles" not in source
        assert "reroute_closure_affected_vehicles" in source

    def test_reroute_wrapper_delegates_to_closure_routing(self, tmp_path):
        route_path = tmp_path / "in.rou.xml"
        write_routes(route_path, [
            '  <vehicle id="v0" depart="0"><route edges="a_b b_c c_d"/></vehicle>',
        ])
        out_path = tmp_path / "out.rou.xml"

        rerouted, denied = run_scenario.reroute_closure_affected_vehicles(
            route_path, ["b_c"], out_path, ADJACENCY, edge_travel_s=COSTS)

        assert (rerouted, denied) == (1, 0)
        assert vehicles_by_id(out_path)["v0"] == "a_b b_e e_c c_d"

    def test_suggest_closure_time_source_does_not_call_the_retired_function(self):
        import inspect
        import suggest_closure_time
        source = inspect.getsource(suggest_closure_time.simulate_closure)
        assert "truncate_stranded_vehicles" not in source
        assert "reroute_closure_affected_vehicles" in source

    def test_monthly_sumo_source_does_not_call_the_retired_function(self):
        import inspect
        from traffic_sim.simulation import monthly_sumo
        source = inspect.getsource(monthly_sumo.ArchivedDemandSumoRunner)
        assert "rs.truncate_stranded_vehicles" not in source
        assert "rs.reroute_closure_affected_vehicles" in source


class TestTeleportPolicyNoLongerDependsOnDisabledTeleport:
    def test_closure_default_cli_teleport_is_no_longer_minus_one(self):
        from traffic_sim.simulation import closure_teleport as ct
        assert ct.CLOSURE_ROUTING_TELEPORT_POLICY_S is None
        # The legacy constant is retained (named, documented) but is no
        # longer the production default -- see CLI parsing below.
        assert ct.CLOSURE_TIME_TO_TELEPORT_S == -1

    def test_simulate_closure_default_no_longer_disables_teleport(self):
        import inspect
        import suggest_closure_time
        sig = inspect.signature(suggest_closure_time.simulate_closure)
        assert sig.parameters["time_to_teleport_s"].default is None
