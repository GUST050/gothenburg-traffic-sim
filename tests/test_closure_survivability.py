"""Stage 4's precondition: does an edge survive its own closure?

The rule exists because v9 failed `ranking_case_fraction` with three of five
cases producing zero eligible schedules — selection had proved the edges
carried demand but never asked whether closing them strands anyone.

The property under test that matters most is the SEPARATION C1 forced: a
denied departure is access the closure removes and must not disqualify, while
a severed destination is a topology fact and must. Conflating them is what made
every busy street permanently uncloseable.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.simulation import closure_survivability as csv
from traffic_sim.simulation.heldout_selection import (
    VARIANT_OF, CanonicalBindingError, stream_closure_exposure)


# A small grid with a real alternative everywhere except one spur:
#
#   A -> B -> C -> D        B and P both reach C and D, so neither is a
#   A -> P -> C -> D        bottleneck; C additionally feeds the spur S,
#   B -> D,  P -> D         whose ONLY way in is C.
#   C -> S
#
# Closing B or P leaves every destination reachable. Closing C severs S
# structurally — the Skanegatan/Engelbrektsgatan shape recorded in CLAUDE.md,
# where one node had exactly one incoming connection in the whole network —
# while every vehicle bound for D still gets there. That separation is what
# lets a test distinguish topological severance from demand severance.
CONNECTIONS = {
    "A": ["B", "P"],
    "B": ["C", "D"],
    "P": ["C", "D"],
    "C": ["D", "S"],
    "D": [],
    "S": [],
}


def build_adjacency(banned):
    return {source: [t for t in targets if t not in banned]
            for source, targets in CONNECTIONS.items() if source not in banned}


def reachable(adj, start, goal, banned):
    if start in banned or goal in banned:
        return False
    if start == goal:
        return True
    seen, stack = {start}, [start]
    while stack:
        for nxt in adj.get(stack.pop(), ()):
            if nxt == goal:
                return True
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def _topology(adjacency=None):
    """(collapsed, predecessors) for the one-hop cut-off question."""
    adjacency = build_adjacency(set()) if adjacency is None else adjacency
    collapsed = csv.collapse_internal(adjacency)
    return collapsed, csv.real_predecessors(collapsed)


def _routes(directory, name, vehicles):
    body = "".join(
        f'<vehicle id="{vid}" depart="{depart}">'
        f'<route edges="{" ".join(edges)}"/></vehicle>\n'
        for vid, depart, edges in vehicles)
    path = Path(directory) / name
    path.write_text(f"<routes>\n{body}</routes>\n")
    return path


def _evaluate(tmp_path, edge, vehicles, *, variants=None):
    variants = variants or {"calibrated.rou.xml": vehicles}
    exposure = {}
    for filename, rows in variants.items():
        path = _routes(tmp_path, filename, rows)
        found = stream_closure_exposure(path, {edge})
        # Keyed by variant exactly as evaluate_pool does, so the helper cannot
        # pass on a shape production would never produce.
        exposure[VARIANT_OF[filename]] = found.get(edge, csv._EMPTY_EXPOSURE)
    return csv.evaluate_edge(
        edge, exposure_by_variant=exposure,
        adjacency_with=build_adjacency(set()), reachable=reachable)


class TestSeparationOfAccessFromTopology:
    def test_denied_departures_are_counted_but_do_not_refuse(self, tmp_path):
        """Every street with departures on it has some. Gating on them would
        refuse to close any real street — exactly what C1 fixed on
        2026-08-06."""
        report = _evaluate(tmp_path, "B", [
            ("starts_here", 0.0, ["B", "C", "D"]),
            ("passes", 0.0, ["A", "B", "C", "D"]),
        ])
        assert report.denied_departures == 1
        assert report.severed_destination == 0
        assert report.survives is True

    def test_a_severed_destination_refuses(self, tmp_path):
        """Closing C leaves no way from B to S at all."""
        report = _evaluate(tmp_path, "C", [("v", 0.0, ["A", "B", "C", "S"])])
        assert report.severed_destination == 1
        assert report.survives is False

    def test_a_real_detour_survives(self, tmp_path):
        """A -> P -> D bypasses B entirely, so nobody is stranded."""
        report = _evaluate(tmp_path, "B", [("v", 0.0, ["A", "B", "C", "D"])])
        assert report.severed_destination == 0
        assert report.survives is True


class TestStructuralSeverance:
    def test_a_successor_with_no_other_way_in_refuses_the_edge(self, tmp_path):
        """S is reachable only through C. That is true whatever today's demand
        happens to drive, so a demand-only check would miss it on any quarter
        where nothing went there."""
        report = _evaluate(tmp_path, "C", [("v", 0.0, ["A", "B", "C", "D"])])
        assert report.successors_cut_off == ("S",)
        assert report.survives is False
        assert report.severed_destination == 0, (
            "no vehicle in this demand was severed; the refusal comes from "
            "topology alone, which is the point")

    def test_a_successor_with_another_way_in_is_not_cut_off(self):
        assert csv.successors_cut_off("B", *_topology()) == ()

    def test_an_edge_with_no_successors_cuts_nothing_off(self):
        assert csv.successors_cut_off("D", *_topology()) == ()


class TestVariantReduction:
    def test_the_worst_variant_decides(self, tmp_path):
        """q10/q50/q90 are three plausible directional assignments; an edge
        that severs someone under ANY of them has not survived."""
        report = _evaluate(tmp_path, "C", vehicles=None, variants={
            "calibrated.rou.xml": [("a", 0.0, ["A", "B", "C", "D"])],
            "calibrated_v1.rou.xml": [("b", 0.0, ["A", "B", "C", "D"])],
            # only q90 sends anyone to the spur
            "calibrated_v2.rou.xml": [("c", 0.0, ["A", "B", "C", "S"])],
        })
        assert report.per_variant["q50"]["severed_destination"] == 0
        assert report.per_variant["q90"]["severed_destination"] == 1
        assert report.severed_destination == 1
        assert report.survives is False

    def test_no_variant_at_all_is_an_error_not_a_pass(self):
        with pytest.raises(ValueError):
            csv.evaluate_edge("B", exposure_by_variant={},
                              adjacency_with=build_adjacency(set()),
                              reachable=reachable)


class TestExposureStreaming:
    def test_the_approach_is_the_edge_before_the_closure_not_the_origin(self, tmp_path):
        """The 2026-07-09 review's finding: two vehicles sharing an origin can
        be on different routes, one already committed to a dead branch. The
        rerouter re-plans from where the vehicle IS."""
        path = _routes(tmp_path, "r.rou.xml", [
            ("v", 0.0, ["A", "B", "C", "D"]),
        ])
        found = stream_closure_exposure(path, {"C"})
        assert list(found["C"]["resume_pairs"]) == [("B", "D")]

    def test_only_the_first_occurrence_of_a_repeated_edge_counts(self, tmp_path):
        """The closure stops the vehicle at the earliest occurrence; pricing a
        later one would price a leg it never reaches."""
        path = _routes(tmp_path, "r.rou.xml", [
            ("v", 0.0, ["A", "B", "C", "B", "D"]),
        ])
        found = stream_closure_exposure(path, {"B"})
        assert found["B"]["vehicles"] == 1
        assert list(found["B"]["resume_pairs"]) == [("A", "D")]

    def test_repeated_pairs_are_counted_not_deduplicated(self, tmp_path):
        path = _routes(tmp_path, "r.rou.xml", [
            ("v1", 0.0, ["A", "B", "C", "D"]),
            ("v2", 60.0, ["A", "B", "C", "D"]),
        ])
        found = stream_closure_exposure(path, {"C"})
        assert found["C"]["resume_pairs"][("B", "D")] == 2

    def test_an_unsupported_construct_is_refused_not_skipped(self, tmp_path):
        """A vehicle that silently failed to parse would look like an edge with
        nothing to sever — the most dangerous possible failure for this rule."""
        path = Path(tmp_path) / "r.rou.xml"
        path.write_text('<routes>\n<flow id="f" begin="0"/>\n'
                        '<vehicle id="v" depart="0">'
                        '<route edges="A B"/></vehicle>\n</routes>\n')
        with pytest.raises(CanonicalBindingError):
            stream_closure_exposure(path, {"B"})

    def test_an_empty_route_file_is_refused(self, tmp_path):
        path = Path(tmp_path) / "r.rou.xml"
        path.write_text("<routes>\n</routes>\n")
        with pytest.raises(CanonicalBindingError):
            stream_closure_exposure(path, {"B"})


class TestPool:
    def test_a_missing_archive_file_fails_closed(self, tmp_path):
        _routes(tmp_path, "calibrated.rou.xml", [("v", 0.0, ["A", "B"])])
        with pytest.raises(FileNotFoundError):
            csv.evaluate_pool(["B"], archive_path=tmp_path,
                              build_adjacency=build_adjacency,
                              reachable=reachable)

    def test_every_candidate_is_evaluated_from_one_pass(self, tmp_path):
        rows = [("v1", 0.0, ["A", "B", "C", "D"]),
                ("v2", 30.0, ["A", "B", "C", "S"])]
        for name in ("calibrated.rou.xml", "calibrated_v1.rou.xml",
                     "calibrated_v2.rou.xml"):
            _routes(tmp_path, name, rows)
        reports = csv.evaluate_pool(["B", "C"], archive_path=tmp_path,
                                    build_adjacency=build_adjacency,
                                    reachable=reachable)
        assert set(reports) == {"B", "C"}
        assert reports["B"].survives is True
        assert reports["C"].survives is False

    def test_a_candidate_no_vehicle_touches_still_gets_a_verdict(self, tmp_path):
        """Absent exposure is zero exposure, not a missing answer — an edge
        nothing drives can still cut a successor off."""
        for name in ("calibrated.rou.xml", "calibrated_v1.rou.xml",
                     "calibrated_v2.rou.xml"):
            _routes(tmp_path, name, [("v", 0.0, ["A", "P", "D"])])
        reports = csv.evaluate_pool(["C"], archive_path=tmp_path,
                                    build_adjacency=build_adjacency,
                                    reachable=reachable)
        assert reports["C"].vehicles_crossing == 0
        assert reports["C"].successors_cut_off == ("S",)
        assert reports["C"].survives is False


class TestSerialization:
    def test_the_report_names_its_rule_version(self, tmp_path):
        report = _evaluate(tmp_path, "B", [("v", 0.0, ["A", "B", "C", "D"])])
        payload = report.to_dict()
        assert payload["rule_version"] == csv.RULE_VERSION
        assert payload["survives_own_closure"] is True
        assert "vehicles_denied_departure" in payload
        assert "vehicles_severed_destination" in payload


class TestDerivedClosedGraph:
    """The closed graph is derived from the unclosed one, not rebuilt.

    Rebuilding calls `run_scenario.build_edge_graph`, which re-reads and
    re-hashes the whole SUMO network every time — sixty candidates would hash a
    multi-megabyte file sixty times to remove one edge each. The derivation
    must therefore reproduce that builder's output exactly, including its
    omission rule, or the two graphs quietly diverge.
    """

    def test_it_matches_the_reference_builder(self):
        for banned in ({"B"}, {"C"}, {"D"}, {"B", "C"}, set()):
            assert csv.without_edges(build_adjacency(set()), banned) == {
                source: targets
                for source, targets in build_adjacency(banned).items()
                if targets}, banned

    def test_a_source_whose_every_target_is_banned_is_omitted(self):
        """`build_edge_graph` creates the key only inside its per-target test,
        so an all-banned source disappears rather than keeping an empty list."""
        adjacency = {"only": ["gone"], "kept": ["gone", "stays"]}
        assert csv.without_edges(adjacency, {"gone"}) == {"kept": ["stays"]}

    def test_a_banned_source_is_dropped_entirely(self):
        assert csv.without_edges({"a": ["b"], "b": ["c"]}, {"a"}) == {"b": ["c"]}


class TestInternalJunctionEdges:
    """SUMO stores a real connection A->B as A->:j_0->B.

    The FIRST version of this check ran the one-hop question against that raw
    graph and always answered "fine": remove A and the internal edge still
    points at B, so B looked reachable when nothing could get to it. It
    reported zero cut-off successors for 60786979_3575001205_0 — the exact edge
    CLAUDE.md documents as severing 63 edges because node 3575001205 has one
    incoming connection in the whole network. A check that cannot see its own
    motivating case is inert, and on the deployed network 20,522 of 27,623
    sources are internal, so it was inert everywhere.
    """

    # only_way_in -> :j_0 -> downstream, plus an unrelated pair
    RAW = {
        "only_way_in": [":j_0"],
        ":j_0": ["downstream"],
        "downstream": [],
        "other": [":j_1"],
        ":j_1": ["shared"],
        "second_way_in": [":j_2"],
        ":j_2": ["shared"],
        "shared": [],
    }

    def test_collapsing_resolves_internal_hops(self):
        collapsed = csv.collapse_internal(self.RAW)
        assert collapsed["only_way_in"] == ["downstream"]
        assert collapsed["other"] == ["shared"]
        assert not any(edge.startswith(":") for edge in collapsed)

    def test_the_sole_predecessor_cuts_its_successor_off(self):
        collapsed = csv.collapse_internal(self.RAW)
        predecessors = csv.real_predecessors(collapsed)
        assert csv.successors_cut_off("only_way_in", collapsed,
                                      predecessors) == ("downstream",)

    def test_a_successor_with_two_real_predecessors_survives(self):
        collapsed = csv.collapse_internal(self.RAW)
        predecessors = csv.real_predecessors(collapsed)
        assert csv.successors_cut_off("other", collapsed, predecessors) == ()

    def test_the_raw_graph_would_have_answered_wrongly(self):
        """The defect itself, pinned: asked on the uncollapsed graph, the
        internal edge keeps the successor alive and the check says nothing is
        cut off."""
        without = csv.without_edges(self.RAW, {"only_way_in"})
        still_reachable = {t for s, ts in without.items() for t in ts}
        assert "downstream" in still_reachable, (
            "this is the wrong answer the collapse exists to prevent")

    def test_a_chain_of_internal_edges_resolves(self):
        raw = {"a": [":j_0"], ":j_0": [":j_1"], ":j_1": ["b"], "b": []}
        assert csv.collapse_internal(raw)["a"] == ["b"]
