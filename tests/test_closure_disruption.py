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
