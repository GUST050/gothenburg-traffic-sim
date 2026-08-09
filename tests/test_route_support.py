"""Unit tests for the LOSO route- and movement-support diagnostics.

Built on a tiny synthetic network and candidate pool, so every expected value
can be derived by hand.  The audit's whole purpose is to decide which
treatment the project spends a 2 h six-seed campaign on, which makes a silent
counting error here more expensive than a wrong number in a report.
"""

import json
from pathlib import Path

import pytest

from traffic_sim.confidence import route_support as rs

# A four-leg junction "J" with two approaches and two exits, plus stub edges
# so routes have a beginning and an end away from the node.
NET = """<net>
  <edge id="in1_J_0" from="in1" to="J" priority="1" length="100.00">
    <lane id="in1_J_0_0" index="0" speed="10.00" length="100.00"/>
  </edge>
  <edge id="in2_J_0" from="in2" to="J" priority="1" length="100.00">
    <lane id="in2_J_0_0" index="0" speed="10.00" length="100.00"/>
  </edge>
  <edge id="J_out1_0" from="J" to="out1" priority="1" length="200.00">
    <lane id="J_out1_0_0" index="0" speed="10.00" length="200.00"/>
  </edge>
  <edge id="J_out2_0" from="J" to="out2" priority="1" length="400.00">
    <lane id="J_out2_0_0" index="0" speed="10.00" length="400.00"/>
  </edge>
  <edge id="src_in1_0" from="src" to="in1" priority="1" length="100.00">
    <lane id="src_in1_0_0" index="0" speed="10.00" length="100.00"/>
  </edge>
  <edge id="src_in2_0" from="src" to="in2" priority="1" length="300.00">
    <lane id="src_in2_0_0" index="0" speed="10.00" length="300.00"/>
  </edge>
  <edge id="internal_x" function="internal" from="J" to="J" priority="1" length="1.00">
    <lane id="internal_x_0" index="0" speed="10.00" length="1.00"/>
  </edge>
  <connection from="src_in1_0" to="in1_J_0"/>
  <connection from="src_in2_0" to="in2_J_0"/>
  <connection from="in1_J_0" to="J_out1_0"/>
  <connection from="in1_J_0" to="J_out2_0"/>
  <connection from="in2_J_0" to="J_out1_0"/>
  <connection from="in2_J_0" to="J_out2_0"/>
</net>
"""

ROUTES = [
    # id, edges, purpose, tour, leg, orphan
    ("c1", "src_in1_0 in1_J_0 J_out1_0", "arbete", "t1", "outbound", False),
    ("c2", "src_in1_0 in1_J_0 J_out1_0", "arbete", "t2", "outbound", False),
    ("c3", "src_in1_0 in1_J_0 J_out2_0", "arbete", "t3", "outbound", True),
    ("c4", "src_in2_0 in2_J_0 J_out1_0", "through", "t4-through", "through", False),
    # Same geometry as c1 but a different purpose: two PFE variables, not one.
    ("c5", "src_in1_0 in1_J_0 J_out1_0", "fritid", "t5", "return", False),
]


@pytest.fixture()
def pool(tmp_path: Path) -> tuple[Path, Path]:
    vehicles = "".join(
        f'<vehicle id="{cid}" depart="{index * 900}.0">'
        f'<route edges="{edges}"/></vehicle>'
        for index, (cid, edges, *_rest) in enumerate(ROUTES)
    )
    rou = tmp_path / "candidates.rou.xml"
    rou.write_text(f"<routes>{vehicles}</routes>")
    meta = tmp_path / "candidates.meta.json"
    meta.write_text(json.dumps({
        "schema_version": 2,
        "candidates": {
            cid: {
                "purpose": purpose,
                "tour_id": tour,
                "leg": leg,
                "origin_edge": edges.split()[0],
                "destination_edge": edges.split()[-1],
                "tour_partner_dropped": orphan,
            }
            for cid, edges, purpose, tour, leg, orphan in ROUTES
        },
    }))
    return rou, meta


@pytest.fixture()
def network(tmp_path: Path) -> rs.Network:
    path = tmp_path / "net.net.xml"
    path.write_text(NET)
    return rs.load_network(path)


def test_network_skips_internal_edges_and_reads_costs(network):
    assert "internal_x" not in network.edge_cost_s
    assert network.edge_cost_s["J_out1_0"] == pytest.approx(20.0)
    assert network.incoming("J") == ("in1_J_0", "in2_J_0")
    assert network.outgoing("J") == ("J_out1_0", "J_out2_0")


def test_legal_movements_are_the_connection_graph(network):
    movements = rs.legal_movements(network, "J")
    assert {(m["from_edge"], m["to_edge"]) for m in movements} == {
        ("in1_J_0", "J_out1_0"), ("in1_J_0", "J_out2_0"),
        ("in2_J_0", "J_out1_0"), ("in2_J_0", "J_out2_0"),
    }
    assert not any(movement["u_turn"] for movement in movements)


def test_u_turn_is_flagged_not_dropped(tmp_path):
    path = tmp_path / "net.net.xml"
    path.write_text(NET.replace(
        '<connection from="in1_J_0" to="J_out1_0"/>',
        '<connection from="in1_J_0" to="J_out1_0"/>'
        '<edge id="J_in1_0" from="J" to="in1" priority="1" length="100.00">'
        '<lane id="J_in1_0_0" index="0" speed="10.00" length="100.00"/></edge>'
        '<connection from="in1_J_0" to="J_in1_0"/>'))
    network = rs.load_network(path)
    movements = rs.legal_movements(network, "J")
    u_turns = [m for m in movements if m["u_turn"]]
    assert [(m["from_edge"], m["to_edge"]) for m in u_turns] == [
        ("in1_J_0", "J_in1_0")]


def test_dedup_keeps_one_variable_per_route_and_purpose(pool):
    rou, meta = pool
    legs = rs.load_pool(rou, meta)
    assert len(legs) == 5
    variables = rs.dedup_variables(legs)
    # c1 and c2 collapse; c5 shares their geometry but not their purpose.
    assert len(variables) == 4
    merged = next(v for v in variables
                  if v.purpose == "arbete" and v.edges[-1] == "J_out1_0")
    assert [leg.candidate_id for leg in merged.legs] == ["c1", "c2"]


def test_missing_provenance_is_refused(tmp_path):
    rou = tmp_path / "candidates.rou.xml"
    rou.write_text('<routes><vehicle id="ghost" depart="0.0">'
                   '<route edges="src_in1_0 in1_J_0"/></vehicle></routes>')
    meta = tmp_path / "candidates.meta.json"
    meta.write_text(json.dumps({"schema_version": 2, "candidates": {}}))
    with pytest.raises(ValueError, match="no pool provenance"):
        rs.load_pool(rou, meta)


def test_effective_alternatives_counts_distinct_choice():
    assert rs.effective_alternatives([0.5]) == pytest.approx(1.0)
    assert rs.effective_alternatives([0.5, 0.5]) == pytest.approx(2.0)
    assert rs.effective_alternatives([0.9, 0.1]) < 2.0
    # A dominated near-copy must not count as a full second option.
    assert rs.effective_alternatives([1.0, 0.01]) < 1.2
    assert rs.effective_alternatives([]) == 0.0


def test_shortest_cost_uses_the_connection_graph(network):
    model = rs.CostModel(network)
    # src -> in1 -> out1 costs 10 + 10 + 20 s.
    assert model.shortest_cost_s("src_in1_0", "J_out1_0") == pytest.approx(40.0)
    assert model.stretch(
        ["src_in1_0", "in1_J_0", "J_out1_0"]) == pytest.approx(1.0)
    # An unreachable destination has no defined stretch rather than a fake one.
    assert model.shortest_cost_s("J_out1_0", "src_in1_0") is None
    assert model.stretch(["J_out1_0", "src_in1_0"]) is None


def test_location_support_aggregates_a_two_way_total_once(pool):
    rou, meta = pool
    variables = rs.dedup_variables(rs.load_pool(rou, meta))
    total = rs.Location("T", "two_way_total", ("J_out1_0", "J_out2_0"))
    other = rs.Location("D", "directional", ("in2_J_0",))
    support = rs.location_support(total, variables, [total, other])
    # Every route crosses one of the two components, so all four variables.
    assert support["variables"] == 4
    assert support["raw_candidate_legs"] == 5
    assert set(support["directional_components"]) == {"J_out1_0", "J_out2_0"}
    assert support["directional_components"]["J_out2_0"]["variables"] == 1
    # Only the through route also crosses the other station.
    assert support["variables_shared_with_station"] == {"D": 1}
    assert support["variables_crossing_no_other_station"] == 3
    assert support["orphan_tour_legs"] == 1


def test_location_support_reports_single_route_od_groups(pool):
    rou, meta = pool
    variables = rs.dedup_variables(rs.load_pool(rou, meta))
    location = rs.Location("A", "directional", ("J_out1_0",))
    support = rs.location_support(location, variables, [location])
    # src_in1_0 -> J_out1_0 holds two variables (arbete, fritid);
    # src_in2_0 -> J_out1_0 holds one.
    assert support["od_groups"] == 2
    assert support["od_groups_with_single_route"] == 1
    assert support["alternatives_per_od_group"]["max"] == 2
    assert support["variables_crossing_no_other_station"] == 3
    assert support["gate_pair_composition"] == {
        "src_in2_0->J_out1_0": 1}


def test_movement_support_counts_variables_and_legs(pool, network):
    rou, meta = pool
    variables = rs.dedup_variables(rs.load_pool(rou, meta))
    movements = rs.legal_movements(network, "J")
    location = rs.Location("A", "directional", ("J_out1_0",))
    rows = {(row["from_edge"], row["to_edge"]): row
            for row in rs.movement_support(movements, variables, [location])}
    assert rows[("in1_J_0", "J_out1_0")]["variables"] == 2
    assert rows[("in1_J_0", "J_out1_0")]["raw_candidate_legs"] == 3
    assert rows[("in1_J_0", "J_out2_0")]["variables"] == 1
    # A legal movement with no candidate is reported as zero, not omitted.
    assert rows[("in2_J_0", "J_out2_0")]["variables"] == 0
    assert rows[("in1_J_0", "J_out1_0")][
        "measured_stations_on_the_same_routes"] == {"A": 2}


def test_identifiability_is_exact_and_drops_with_a_held_station(network):
    movements = rs.legal_movements(network, "J")
    legs = list(network.incoming("J")) + list(network.outgoing("J"))
    measured = {"in1_J_0": "S1", "J_out1_0": "S2"}
    full = rs.junction_identifiability(movements, measured, legs)
    assert full["movements"] == 4
    assert full["rank"] == 2 and full["nullity"] == 2
    # Four legs on a 2x2 junction give only three independent equations.
    assert full["rank_if_every_leg_were_measured"] == 3
    held = rs.junction_identifiability(movements, measured, legs, held="S2")
    assert held["rank"] == 1 and held["nullity"] == 3
    gains = {row["leg"]: row["rank_gain"] for row in held["unmeasured_leg_rank_gain"]}
    assert gains["in2_J_0"] == 1
    # The held station's own leg is the observation the fold is missing.
    assert gains["J_out1_0"] == 1


def test_constraint_coverage_flags_an_edge_with_only_a_soft_prior():
    priors = {"in2_J_0": {"sensor": "S2", "prior": [10.0, None, 20.0],
                          "prior_low": [5.0, None, 10.0],
                          "prior_high": [15.0, None, 30.0]}}
    assignment = {"J_out2_0": [1.0, 2.0, 3.0]}
    rows = {row["edge"]: row for row in rs.constraint_coverage(
        ["in1_J_0", "in2_J_0", "J_out2_0"], priors, assignment,
        {"in1_J_0": "S1"})}
    assert rows["in1_J_0"]["constraint_classes"] == ["measurement"]
    # Carrying a prior excludes the edge from the hard ceiling, so it has no
    # upper bound at all — the condition the audit exists to surface.
    assert rows["in2_J_0"]["constraint_classes"] == ["direction_prior"]
    assert rows["in2_J_0"]["has_hard_upper_bound"] is False
    assert rows["in2_J_0"]["direction_prior_daily_total"] == 30.0
    assert rows["J_out2_0"]["has_hard_upper_bound"] is True
    # max(5, 5x) per quarter: the 5-vehicle floor dominates the first slot.
    assert rows["J_out2_0"]["assignment_ceiling_daily_total"] == pytest.approx(30.0)


def test_selected_movement_flows_bucket_by_departure_quarter(tmp_path, network):
    rou = tmp_path / "fold.rou.xml"
    rou.write_text(
        '<routes>'
        '<vehicle id="v0" depart="10.0"><route edges="in1_J_0 J_out1_0"/></vehicle>'
        '<vehicle id="v1" depart="1000.0"><route edges="in1_J_0 J_out1_0"/></vehicle>'
        '<vehicle id="v2" depart="1000.0"><route edges="in2_J_0 J_out2_0"/></vehicle>'
        '</routes>')
    movements = rs.legal_movements(network, "J")
    legs = list(network.incoming("J")) + list(network.outgoing("J"))
    flows = rs.selected_movement_flows(rou, movements, legs, n_quarters=4)
    assert flows["vehicles"] == 3
    assert flows["by_movement"]["in1_J_0->J_out1_0"] == [1, 1, 0, 0]
    assert flows["by_movement"]["in2_J_0->J_out2_0"] == [0, 1, 0, 0]
    assert flows["by_leg"]["J_out1_0"] == [1, 1, 0, 0]


def test_simulated_edge_flows_sum_entered_per_quarter(tmp_path):
    path = tmp_path / "ed.xml"
    path.write_text(
        '<meandata>'
        '<interval begin="0.00" end="900.00"><edge id="a" entered="4"/></interval>'
        '<interval begin="900.00" end="1800.00"><edge id="a" entered="6"/>'
        '<edge id="b" entered="1"/></interval>'
        '</meandata>')
    flows = rs.simulated_edge_flows(path, ["a", "b"], n_quarters=3)
    assert flows["a"] == [4.0, 6.0, 0.0]
    assert flows["b"] == [0.0, 1.0, 0.0]


def test_across_seed_stability_reports_disagreement():
    support = {
        "1": {"A": {"h1", "h2"}, "B": {"h9"}},
        "2": {"A": {"h1", "h3"}, "B": {"h9"}},
    }
    stability = rs.across_seed_stability(support)
    assert stability["A"]["pairwise_jaccard"]["median"] == pytest.approx(
        1 / 3, abs=1e-4)
    assert stability["B"]["pairwise_jaccard"]["median"] == pytest.approx(1.0)
    assert stability["A"]["crossing_variables_by_seed"] == {"1": 2, "2": 2}
    assert stability["A"]["crossing_variables_range_ratio"] == 1.0
