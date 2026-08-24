"""closure_disruption: who a closure displaces, and by how much.

Regression cover for two defects found reviewing the 2026-08-05 implementation:
the strike scan stopped at the FIRST closed edge (so a route blocked by a
later edge inside the window was missed), and only the q50 variant was ever
measured (so the q10/q90 direction-split spread never reached the ranking).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_scenario


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
        """The vehicle passes B at t=10 (outside the window) and C at t=20
        (inside it). Stopping the scan at B loses the vehicle entirely."""
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        window = [{"begin_s": 15.0, "end_s": 30.0}]
        report = _run(path, ["B", "C"], window)
        assert report["vehicles_affected"] == 1, (
            "a route blocked by a later closed edge inside the window must be "
            "counted; scanning only as far as the first closed edge misses it")

    def test_a_closure_wholly_outside_the_window_does_not_count(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        window = [{"begin_s": 500.0, "end_s": 600.0}]
        assert _run(path, ["B", "C"], window)["vehicles_affected"] == 0

    def test_no_window_means_the_whole_day(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        assert _run(path, ["C"])["vehicles_affected"] == 1


class TestSeverity:
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
            (["B", "C"], [{"begin_s": 15.0, "end_s": 30.0}]),
            (["B", "C"], [{"begin_s": 500.0, "end_s": 600.0}]),
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
