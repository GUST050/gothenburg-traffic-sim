"""Contract tests for validation-only joint controlled rounding."""

import numpy as np

from traffic_sim.confidence.controlled_rounding import (
    controlled_round_preserving_measured,
    integer_margin_feasibility,
    legacy_rounding_trace,
    minimal_inconsistent_integer_margins,
)
from traffic_sim.demand.pfe import Candidate, round_preserving_measured


def candidate(*edges):
    return Candidate(depart=0.0, edges=list(edges))


def test_joint_floor_ceil_rounding_preserves_overlapping_margins_and_total():
    shapes = [candidate("A"), candidate("A", "B"), candidate("B"), candidate("X")]
    solution = np.asarray([0.6, 0.4, 0.6, 0.4])

    counts, report = controlled_round_preserving_measured(
        solution, shapes, {"A": 1.0, "B": 1.0})

    assert report["method"] == "floor_ceil_l1"
    assert counts.sum() == round(solution.sum()) == 2
    assert counts[0] + counts[1] == 1
    assert counts[1] + counts[2] == 1
    assert report["measurement_residuals"] == {"A": 0, "B": 0}


def test_general_l1_fallback_moves_more_than_floor_ceil_when_required():
    shapes = [candidate("M"), candidate("X")]
    solution = np.asarray([0.0, 2.0])

    counts, report = controlled_round_preserving_measured(
        solution, shapes, {"M": 2.0})

    assert report["method"] == "general_l1"
    assert counts.tolist() == [2, 0]
    assert report["integer_total"] == 2
    assert report["measurement_residuals"] == {"M": 0}


def test_incompatible_measurement_and_total_gets_explicit_minimum_residual():
    shapes = [candidate("M"), candidate("X")]
    counts, report = controlled_round_preserving_measured(
        np.asarray([0.0, 2.0]), shapes, {"M": 3.0}, time_limit_s=2)

    assert counts.tolist() == [2, 0]
    assert report["method"] == "minimum_measurement_error_l1"
    assert report["minimum_max_abs_measurement_residual"] == 1.0
    assert report["minimum_sum_abs_measurement_residual"] == 1.0
    assert report["measurement_residuals"] == {"M": -1}


def test_incompatible_exact_margin_uses_declared_measurement_band_when_supplied():
    shapes = [candidate("M"), candidate("X")]
    counts, report = controlled_round_preserving_measured(
        np.asarray([0.0, 2.0]), shapes, {"M": 3.0},
        measurement_tol_mult=1.0)

    assert counts.tolist() == [2, 0]
    assert report["method"] == "measurement_band_lexicographic_l1"
    assert report["measurement_residuals"] == {"M": -1}
    assert report["permitted_integer_measurement_bounds"] == {"M": [1, 5]}


def test_legacy_trace_is_byte_equivalent_and_attributes_held_additions():
    shapes = [candidate("M", "H"), candidate("M", "K"), candidate("X")]
    # Largest-remainder gives its only extra unit to the unrelated X route;
    # the legacy measurement repair must therefore add a measured route.
    solution = np.asarray([0.2, 0.1, 1.7])
    measured = {"M": 1.0}

    counts, trace = legacy_rounding_trace(
        solution, shapes, measured, ["H"])

    assert np.array_equal(
        counts, round_preserving_measured(solution, shapes, measured))
    assert trace["measurement_residuals"] == {"M": 0}
    assert trace["correction_by_measured_edge"]["M"]["routes_added"] == 1


def test_integer_margin_conflict_isolated_and_declared_band_restores_feasibility():
    shapes = [candidate("M"), candidate("X")]
    solution = np.asarray([0.0, 2.0])
    measured = {"M": 3.0}

    exact = integer_margin_feasibility(solution, shapes, measured)
    within_band = integer_margin_feasibility(
        solution, shapes, measured, measurement_tol_mult=1.0)
    conflict = minimal_inconsistent_integer_margins(
        solution, shapes, measured)

    assert exact["feasible"] is False
    assert within_band["feasible"] is True
    assert conflict == ["M", "__interval_total__"]
