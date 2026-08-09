"""Regression tests for validation-only LOSO picker decomposition."""

import numpy as np
import pytest

from traffic_sim.confidence import picker_diagnostics as pd
from traffic_sim.demand.pfe import Candidate, EPS_PARSIMONY


def shape(edges, purpose):
    source = Candidate(0.0, list(edges), intent={"purpose": purpose})
    return Candidate(0.0, list(edges), source_candidates=[source])


def build(solutions=None):
    shapes = [
        shape(["O1", "IN", "OUT", "H", "D1"], "arbete"),
        shape(["O2", "IN", "ALT", "D2"], "fritid"),
        shape(["O3", "X", "D3"], "through"),
    ]
    return pd.build_fold_diagnostics(
        shapes,
        np.array([EPS_PARSIMONY / 0.5, EPS_PARSIMONY, EPS_PARSIMONY]),
        [{"M": 10.0}],
        [{"H": (0.0, 8.0), "X": (0.0, 100.0)}],
        [{"P": (5.0, 0.5)}],
        solutions if solutions is not None else [np.array([7.95, 2.05, 0.0])],
        [0],
        [("near", [0], 0.8), ("far", [2], 0.5)],
        [{"arbete": 8, "fritid": 2}],
        held_station="107",
        held_component_edges=["H"],
        assignment_ceiling_edges=["H", "MISSING"],
        legal_movements=[{"from_edge": "IN", "to_edge": "OUT"}],
        source={"candidate_pool_sha256": "abc"},
    )


def test_decomposes_selected_flow_without_mutating_solution():
    solution = np.array([7.95, 2.05, 0.0])
    before = solution.copy()
    report = build([solution])

    assert np.array_equal(solution, before)
    quarter = report["quarters"][0]
    assert quarter["positive_routes"] == 2
    assert quarter["held_location_selected_flow"] == pytest.approx(7.95)
    assert quarter["held_location_flow_contribution"]["by_purpose"] == {
        "arbete": 7.95}
    assert quarter["held_location_flow_contribution"]["by_movement"] == {
        "IN->OUT": 7.95}
    selected = quarter["selected_routes"][0]
    assert selected["path_size_seed_weight"] == pytest.approx(0.5)
    assert selected["touches_held_location"] is True


def test_reports_binding_and_missing_assignment_ceilings():
    report = build()
    rows = {row["edge"]: row for row in
            report["quarters"][0]["assignment_ceiling_utilisation"]}

    assert rows["H"]["present"] is True
    assert rows["H"]["binding"] is True
    assert rows["H"]["utilisation"] == pytest.approx(7.95 / 8.0)
    assert rows["MISSING"] == {
        "edge": "MISSING", "present": False, "routes_touched": 0,
        "achieved_flow": 0.0}
    assert report["summary"]["assignment_ceiling_binding_quarters"] == {"H": 1}


def test_reports_guard_binding_rung_and_semantics():
    report = build()
    groups = {row["name"]: row for row in report["quarters"][0]["structure_groups"]}

    assert groups["near"]["binding"] is True
    assert groups["far"]["binding"] is False
    assert report["summary"]["relaxation_rung_counts"] == {"clean": 1}
    assert report["field_semantics"]["targets"] == "measurement"
    assert report["solution_stage"] == "continuous_pre_integer_publication"


def test_rejects_inconsistent_quarter_arrays():
    with pytest.raises(ValueError, match="quarter arrays"):
        pd.build_fold_diagnostics(
            [], np.array([]), [{}], [], [{}], [np.array([])], [0], [], [{}],
            held_station="x", held_component_edges=[], assignment_ceiling_edges=[],
            legal_movements=[], source={})
