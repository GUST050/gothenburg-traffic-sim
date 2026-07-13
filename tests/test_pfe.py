"""Unit tests for pfe.py — the level-4 reconciliation engine."""

import itertools
import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import pfe
from pfe import (Candidate, EPS_PARSIMONY, RUNG_CLEAN, RUNG_INFEASIBLE,
                 RUNG_LP_FALLBACK, calibrate, largest_remainder_round,
                 path_size_weights, solve_calibration_intervals,
                 solve_interval, solve_interval_entropy,
                 solve_interval_with_relaxation, write_calibration_report)


def cand(*edges):
    return Candidate(depart=0.0, edges=list(edges))


def served(x, cands, edge):
    return sum(xi for xi, c in zip(x, cands) if edge in c.edges)


class TestSolveIntervalEntropy:
    """The PRIMARY solver since 2026-07-10 (IPF/Bregman balancing — see the
    function's own docstring for the full history: an LP's linear
    parsimony objective was mathematically indifferent to concentrating
    flow onto one route vs spreading it, which a chain of patches
    (a share cap, then reweight-and-resolve, then MSA damping for THAT)
    kept fighting instead of fixing at the root). Mirrors TestSolveInterval
    below so the two solvers are held to the identical observable
    contract, plus tests for entropy's own defining property: dispersion."""

    def test_measured_count_is_hit(self):
        cands = [cand("a", "b"), cand("c")]
        x = solve_interval_entropy(cands, {"a": 100.0}, {}, {})
        assert served(x, cands, "a") == pytest.approx(100, rel=0.06)

    def test_prior_is_pulled_toward(self):
        cands = [cand("p"), cand("q")]
        x = solve_interval_entropy(cands, {}, {}, {"p": (40.0, 1.0)})
        assert served(x, cands, "p") == pytest.approx(40, abs=1)
        # no constraint touches q -> stays at its (zero) seed
        assert served(x, cands, "q") == pytest.approx(0, abs=1)

    def test_hard_bound_beats_prior(self):
        """Level 2 must dominate level 3: prior says 80, bound caps at 30."""
        cands = [cand("p")]
        x = solve_interval_entropy(cands, {}, {"p": (0.0, 30.0)}, {"p": (80.0, 1.0)})
        assert served(x, cands, "p") <= 30.0 + 1e-6

    def test_measurement_beats_prior_via_shared_route(self):
        """A route passing both a measured and a prior edge: the measured
        band is hard, the prior only pulls within it."""
        cands = [cand("m", "p")]
        x = solve_interval_entropy(cands, {"m": 50.0}, {}, {"p": (200.0, 1.0)})
        assert served(x, cands, "m") == pytest.approx(50, rel=0.06)

    def test_unserveable_measurement_is_dropped_not_fatal(self):
        """A count no candidate can serve must not kill the interval —
        the constraint is dropped and everything else is still served."""
        cands = [cand("other"), cand("p")]
        x = solve_interval_entropy(cands, {"m": 100.0}, {}, {"p": (40.0, 1.0)})
        assert x is not None
        assert served(x, cands, "p") == pytest.approx(40, abs=1)

    def test_lower_bound_forces_flow(self):
        cands = [cand("b")]
        x = solve_interval_entropy(cands, {}, {"b": (25.0, 500.0)}, {})
        assert served(x, cands, "b") >= 25.0 - 1e-6

    def test_conflicting_band_and_bound_is_infeasible(self):
        """Measured band [95,105] (target 100) can never intersect a
        bound capping the SAME edge at [0,30] -- IPF must recognise this
        rather than silently returning something out of range."""
        cands = [cand("a")]
        x = solve_interval_entropy(cands, {"a": 100.0}, {"a": (0.0, 30.0)}, {})
        assert x is None

    def test_disperses_across_equally_good_alternatives(self):
        """N identical routes touching the same edge, nothing else to
        differentiate them -- entropy maximisation's defining behaviour
        is to split the target EVENLY among them, not concentrate onto
        one (an LP with a flat linear objective has no such preference
        and can pick any vertex, including a fully concentrated one)."""
        cands = [cand("a"), cand("a"), cand("a"), cand("a")]
        x = solve_interval_entropy(cands, {"a": 100.0}, {}, {})
        assert x is not None
        assert np.std(x) < 1.0   # near-equal split, not concentrated

    def test_distinctive_route_preferred_over_heavily_overlapping_ones(self):
        """route_cost (PSL) still shapes the PRIOR -- a distinctive route
        (low cost) should end up with a bigger share than routes that
        overlap heavily with many others (high cost), even though all
        touch the same measured edge."""
        cands = [cand("a"), cand("a"), cand("a")]
        route_cost = np.array([EPS_PARSIMONY, 5 * EPS_PARSIMONY, 5 * EPS_PARSIMONY])
        x = solve_interval_entropy(cands, {"a": 100.0}, {}, {}, route_cost=route_cost)
        assert x[0] > x[1] == pytest.approx(x[2])

    def test_single_route_gets_the_whole_target_when_alone(self):
        """No alternatives to disperse onto -- there is nothing wrong with
        one route carrying the whole count when it's the only candidate."""
        cands = [cand("a")]
        x = solve_interval_entropy(cands, {"a": 100.0}, {}, {})
        assert served(x, cands, "a") == pytest.approx(100, rel=0.06)


class TestSolveInterval:
    """solve_interval (LP, Bell & Shield 1996 lineage) — since 2026-07-10
    no longer calibrate()'s primary solver (see solve_interval_entropy),
    kept as its final relaxation-ladder rung: a battle-tested, complete
    solver for the rare case IPF doesn't converge. Same contract as
    solve_interval_entropy above, tested identically."""

    def test_measured_count_is_hit(self):
        cands = [cand("a", "b"), cand("c")]
        x = solve_interval(cands, {"a": 100.0}, {}, {})
        assert served(x, cands, "a") == pytest.approx(100, rel=0.06)

    def test_prior_is_pulled_toward(self):
        cands = [cand("p"), cand("q")]
        x = solve_interval(cands, {}, {}, {"p": (40.0, 1.0)})
        assert served(x, cands, "p") == pytest.approx(40, abs=1)
        # no constraint touches q → parsimony keeps it at zero
        assert served(x, cands, "q") == pytest.approx(0, abs=1)

    def test_hard_bound_beats_prior(self):
        """Level 2 must dominate level 3: prior says 80, bound caps at 30."""
        cands = [cand("p")]
        x = solve_interval(cands, {}, {"p": (0.0, 30.0)}, {"p": (80.0, 1.0)})
        assert served(x, cands, "p") <= 30.0 + 1e-6

    def test_measurement_beats_prior_via_shared_route(self):
        """A route passing both a measured and a prior edge: the measured
        band is hard, the prior only pulls within it."""
        cands = [cand("m", "p")]
        x = solve_interval(cands, {"m": 50.0}, {}, {"p": (200.0, 1.0)})
        assert served(x, cands, "m") == pytest.approx(50, rel=0.06)

    def test_unserveable_measurement_is_dropped_not_fatal(self):
        """A count no candidate can serve must not kill the interval —
        the constraint is dropped and everything else is still served."""
        cands = [cand("other"), cand("p")]
        x = solve_interval(cands, {"m": 100.0}, {}, {"p": (40.0, 1.0)})
        assert x is not None
        assert served(x, cands, "p") == pytest.approx(40, abs=1)

    def test_lower_bound_forces_flow(self):
        cands = [cand("b")]
        x = solve_interval(cands, {}, {"b": (25.0, 500.0)}, {})
        assert served(x, cands, "b") >= 25.0 - 1e-6


class TestPathSizeWeights:
    """Path Size (Ben-Akiva & Bierlaire 1999, Ramming 2002 link-count
    variant): without this, every candidate route satisfying the same
    counts is equally "free" in the objective, so PFE's choice among
    overlapping alternatives is an arbitrary LP-solver artifact rather than
    the real preference for a distinctive route. Added 2026-07-08."""

    def test_unique_route_gets_base_cost(self):
        w = path_size_weights([cand("a", "b")])
        assert w[0] == pytest.approx(EPS_PARSIMONY)

    def test_fully_overlapping_routes_cost_more(self):
        w = path_size_weights([cand("a", "b"), cand("a", "b")])
        assert w[0] == pytest.approx(2 * EPS_PARSIMONY)
        assert w[1] == pytest.approx(2 * EPS_PARSIMONY)

    def test_partial_overlap_is_intermediate(self):
        w_unique  = path_size_weights([cand("a", "b")])[0]
        w_overlap = path_size_weights([cand("a", "b"), cand("a", "b")])[0]
        w_partial = path_size_weights([cand("a", "b"), cand("a", "c")])[0]
        assert w_unique < w_partial < w_overlap

    def test_extreme_overlap_is_floored_not_unbounded(self):
        # 20 routes all sharing one edge -> raw path size 1/20 = 0.05,
        # clipped to the 0.15 floor rather than blowing the cost up ~20x.
        w = path_size_weights([cand("shared") for _ in range(20)])
        assert w[0] == pytest.approx(EPS_PARSIMONY / 0.15)


class TestRouteCostAffectsSolution:
    def test_cheaper_route_preferred_when_otherwise_tied(self):
        """Two routes touch only the same prior edge -- the prior
        constrains their SUM alone, so route_cost is the only thing that
        can decide the split. The cheaper (lower route_cost) route must
        carry the load."""
        cands = [cand("x"), cand("x")]
        route_cost = np.array([5 * EPS_PARSIMONY, EPS_PARSIMONY])
        x = solve_interval(cands, {}, {}, {"x": (10.0, 1.0)}, route_cost=route_cost)
        assert x[1] > x[0]
        assert served(x, cands, "x") == pytest.approx(10, rel=0.1)


class TestCalibrateGEH:
    def test_reports_measured_edges_without_candidate_coverage(self, tmp_path):
        cand_path = tmp_path / "candidates.rou.xml"
        cand_path.write_text(
            '<routes><vehicle id="0" depart="0.00">'
            '<route edges="other"/></vehicle></routes>'
        )
        report = calibrate(cand_path, tmp_path / "calibrated.rou.xml",
                           [{"missing": 10.0}], [{}], [{}])

        assert report["unserviceable_edges"] == ["missing"]

    def test_hour_with_null_first_quarter_still_counted(self, tmp_path):
        """A measured edge that's missing (null) only in an hour's FIRST
        quarter, but present in the other three, must still be checked for
        that hour — found 2026-07-06: the loop used to key off
        targets_per_q[i] alone and silently skipped the whole hour."""
        cand_path = tmp_path / "candidates.rou.xml"
        cand_path.write_text(
            '<routes><vehicle id="0" depart="0.00">'
            '<route edges="e"/></vehicle></routes>'
        )
        out_path = tmp_path / "calibrated.rou.xml"
        targets_per_q = [{}, {"e": 10.0}, {"e": 10.0}, {"e": 10.0}]
        bounds_per_q  = [{}, {}, {}, {}]
        priors_per_q  = [{}, {}, {}, {}]

        report = calibrate(cand_path, out_path, targets_per_q,
                           bounds_per_q, priors_per_q)

        assert report["geh_total"] == 1
        assert report["geh_ok"] == 1


class TestCalibrateDispersion:
    def _write_candidates(self, path, vehicle_edges):
        lines = ["<routes>"]
        for i, edges in enumerate(vehicle_edges):
            lines.append(f'<vehicle id="{i}" depart="0.00">'
                         f'<route edges="{edges}"/></vehicle>')
        lines.append("</routes>")
        path.write_text("\n".join(lines))

    def test_disperses_across_many_genuine_alternatives(self, tmp_path):
        """Several distinct shapes all touching edge 'a' -- calibrate()
        (via solve_interval_entropy) should spread the target across
        MULTIPLE of them, not concentrate onto one, with no tuning knob
        required (unlike the LP-reweighting apparatus this replaced)."""
        cand_path = tmp_path / "candidates.rou.xml"
        self._write_candidates(cand_path, ["a", "a b", "a c", "a d", "a e"])
        out_path = tmp_path / "calibrated.rou.xml"
        targets_per_q = [{"a": 200.0}]
        report = calibrate(cand_path, out_path, targets_per_q, [{}], [{}])
        assert report["achieved"]["a"][0] == pytest.approx(200, abs=10)

        import xml.etree.ElementTree as ET
        from collections import Counter
        edges = [v.find("route").get("edges") for v in ET.parse(out_path).getroot()]
        counts = Counter(edges)
        assert len(counts) > 1   # not all concentrated onto a single shape
        assert max(counts.values()) / sum(counts.values()) < 0.9


class TestRounding:
    def test_totals_preserved(self):
        x = np.array([0.3, 0.3, 0.4, 2.0])
        r = largest_remainder_round(x)
        assert r.sum() == 3
        assert r[3] == 2

    def test_integer_input_unchanged(self):
        x = np.array([1.0, 2.0, 0.0])
        assert (largest_remainder_round(x) == [1, 2, 0]).all()


class TestRouteIndexGroups:
    """`groups` (2026-07-12, DESTINATION_BIAS_RESEARCH §4A step 3): a band
    over an explicit ROUTE-INDEX set, used to stop count-matching from
    loading near-sensor-ending routes far beyond their pool share (measured:
    19.4% of calibrated vehicles vs a ~2-4% pool/field share; the routes
    are 'free variables' for closing one sensor's band without touching any
    other's). Held to the same observable contract in BOTH solvers."""

    def test_entropy_group_cap_redirects_flow_to_uncapped_routes(self):
        # Both routes serve measured edge "m" (target 100); route 0 is in
        # the capped group (max 20), so route 1 must carry the rest.
        cands = [cand("m", "stub"), cand("m", "onward", "far")]
        x = solve_interval_entropy(cands, {"m": 100.0}, {}, {},
                                   groups=[([0], 0.0, 20.0)])
        assert x is not None
        assert x[0] <= 20.0 + 1e-6
        assert served(x, cands, "m") == pytest.approx(100, rel=0.06)

    def test_lp_group_cap_redirects_flow_to_uncapped_routes(self):
        cands = [cand("m", "stub"), cand("m", "onward", "far")]
        x = solve_interval(cands, {"m": 100.0}, {}, {},
                           groups=[([0], 0.0, 20.0)])
        assert x is not None
        assert x[0] <= 20.0 + 1e-6
        assert served(x, cands, "m") == pytest.approx(100, rel=0.06)

    def test_infeasible_group_cap_returns_none_not_a_violation(self):
        # EVERY route serving "m" is in the capped group -> target 100 and
        # cap 20 cannot coexist; the solver must say so, not silently
        # violate one of them (the relaxation ladder/two-pass fallback
        # above this layer decides what to drop).
        cands = [cand("m", "stub")]
        assert solve_interval_entropy(cands, {"m": 100.0}, {}, {},
                                      groups=[([0], 0.0, 20.0)]) is None

    def test_relaxation_ladder_drops_the_group_before_the_counts(self):
        # Same impossible combination through the ladder: the group cap is
        # a plausibility constraint and must be sacrificed (RUNG_RELAX_NOBND
        # stage) so the measured count still gets served.
        cands = [cand("m", "stub")]
        sol, rung = solve_interval_with_relaxation(
            cands, {"m": 100.0}, {}, {}, groups=[([0], 0.0, 20.0)])
        assert sol is not None
        assert served(sol, cands, "m") == pytest.approx(100, rel=0.06)
        assert rung != RUNG_INFEASIBLE

    def test_group_with_zero_lo_does_not_activate_its_members(self):
        # A pure ceiling must not, by itself, put flow on a route nothing
        # else asked for — same activation rule as a lo=0 bound.
        cands = [cand("m"), cand("unrelated")]
        x = solve_interval_entropy(cands, {"m": 50.0}, {}, {},
                                   groups=[([1], 0.0, 30.0)])
        assert x is not None
        assert x[1] == pytest.approx(0.0, abs=1e-9)

    def test_empty_group_is_ignored(self):
        cands = [cand("m")]
        x = solve_interval_entropy(cands, {"m": 50.0}, {}, {},
                                   groups=[([], 0.0, 1.0)])
        assert x is not None
        assert served(x, cands, "m") == pytest.approx(50, rel=0.06)

    def test_structure_guard_caps_a_violating_group_share(self):
        # solve_interval_with_structure_guard is THE shared two-pass policy
        # (deployed pipeline + LOSO both delegate here): pass 1 free, pass 2
        # re-solved with each group capped at cap_share x pass-1 total.
        cands = [cand("m", "stub"), cand("m", "onward", "far")]
        sol, rung = pfe.solve_interval_with_structure_guard(
            cands, {"m": 100.0}, {}, {},
            structure_groups=[("near_sensor_dest", [0], 0.2)])
        assert sol is not None
        assert sol[0] <= 0.2 * sol.sum() + 1.0
        assert served(sol, cands, "m") == pytest.approx(100, rel=0.06)

    def test_structure_guard_counts_win_over_impossible_cap(self):
        # Every route serving "m" is capped: the ladder drops the cap
        # (never the count) — the interval must still be served.
        cands = [cand("m", "stub")]
        sol, _rung = pfe.solve_interval_with_structure_guard(
            cands, {"m": 100.0}, {}, {},
            structure_groups=[("g", [0], 0.2)])
        assert sol is not None
        assert served(sol, cands, "m") == pytest.approx(100, rel=0.06)

    def test_structure_guard_without_groups_is_plain_relaxation(self):
        cands = [cand("m")]
        sol, rung = pfe.solve_interval_with_structure_guard(
            cands, {"m": 50.0}, {}, {}, structure_groups=None)
        ref, ref_rung = solve_interval_with_relaxation(cands, {"m": 50.0}, {}, {})
        assert rung == ref_rung
        assert sol == pytest.approx(ref)

    def test_integer_repair_enforces_a_group_cap_preserving_measured(self):
        # The rounding-stage leak this exists for: a rounded vector that
        # satisfies the measured count but puts too much of it on the
        # capped group must be repaired by shifting whole vehicles to
        # uncapped routes serving the SAME measured edge — never by
        # changing the measured total.
        from pfe import repair_integer_bounds
        cands = [cand("m", "stub"), cand("m", "onward")]
        counts = np.array([8, 2])   # group route carries 8 of 10
        repaired = repair_integer_bounds(
            counts, cands, {"m": 10.0}, {}, groups=[([0], 0.0, 3.0)])
        assert repaired is not None
        assert repaired[0] <= 3
        assert repaired.sum() == 10          # measured total preserved
        assert repaired[0] + repaired[1] == 10

    def test_integer_repair_group_infeasible_returns_none(self):
        from pfe import repair_integer_bounds
        cands = [cand("m", "stub")]          # only ONE route serves m
        counts = np.array([10])
        assert repair_integer_bounds(
            counts, cands, {"m": 10.0}, {}, groups=[([0], 0.0, 3.0)]) is None

    def test_integer_repair_no_groups_no_bounds_is_a_noop(self):
        from pfe import repair_integer_bounds
        cands = [cand("m")]
        counts = np.array([5])
        assert (repair_integer_bounds(counts, cands, {"m": 5.0}, {}) == counts).all()


class TestBoundViolationsFromRounding:
    """A measurement-first rounding nudge can breach a shared edge bound.

    The writer must repair the common, integer-feasible case and only reject
    publication when the integer constraints truly cannot coexist.
    """

    def test_repairs_a_real_bound_violation_from_rounding(self, tmp_path):
        # Route A touches only the measured edge M; route B touches BOTH M
        # and the separately-bounded (but unmeasured) edge U. M's target
        # (10) requires more integer vehicles than the continuous solution's
        # sum (7.4) already provides, forcing round_preserving_measured's
        # deficit-closing loop to increment B past U's own upper bound (3).
        shapes = [Candidate(depart=0.0, edges=["M"]),
                 Candidate(depart=0.0, edges=["M", "U"])]
        solutions = [np.array([5.1, 2.3])]
        targets_per_q = [{"M": 10.0}]
        bounds_per_q = [{"U": (0.0, 3.0)}]

        # enforce_integer_bounds=True: repair only fires for callers that
        # actually want enforcement (build_sumo_demand.py's real deployment
        # calls). A caller passing bounds diagnostically only
        # (enforce_integer_bounds=False, e.g. validate_sim.py's LOSO folds)
        # must get back the UNREPAIRED counts — see
        # test_diagnostic_only_bounds_are_reported_but_not_repaired below.
        report = write_calibration_report(
            shapes, tmp_path / "out.rou.xml", targets_per_q, solutions, bounds_per_q,
            enforce_integer_bounds=True)

        assert report["achieved"]["M"] == [10.0]
        assert report["achieved"]["U"] == [3.0]
        assert report["bound_violations"] == []

    def test_diagnostic_only_bounds_are_reported_but_not_repaired(self, tmp_path):
        # Same violation as test_repairs_a_real_bound_violation_from_rounding
        # above, but enforce_integer_bounds=False (the default, and
        # validate_sim.py's LOSO fold calibration's actual call signature):
        # the writer must report the violation honestly, NOT silently
        # rewrite the published route counts to hide it. Found in review
        # 2026-07-12: repair used to fire whenever bounds_per_q was merely
        # supplied, regardless of this flag, which would have silently
        # altered LOSO's published route counts (and therefore its GEH/
        # recovery-ratio numbers) even though validate_sim.py explicitly
        # opts out of enforcement.
        shapes = [Candidate(depart=0.0, edges=["M"]),
                 Candidate(depart=0.0, edges=["M", "U"])]
        solutions = [np.array([5.1, 2.3])]
        targets_per_q = [{"M": 10.0}]
        bounds_per_q = [{"U": (0.0, 3.0)}]

        report = write_calibration_report(
            shapes, tmp_path / "out.rou.xml", targets_per_q, solutions, bounds_per_q)

        assert report["achieved"]["M"] == [10.0]
        assert report["achieved"]["U"] == [4.0]   # UNREPAIRED — the real rounding result
        assert len(report["bound_violations"]) == 1
        assert report["bound_violations"][0]["edge"] == "U"

    def test_no_violations_when_rounding_stays_within_bounds(self, tmp_path):
        shapes = [Candidate(depart=0.0, edges=["M"])]
        solutions = [np.array([10.0])]
        targets_per_q = [{"M": 10.0}]
        bounds_per_q = [{"M": (0.0, 20.0)}]

        report = write_calibration_report(
            shapes, tmp_path / "out.rou.xml", targets_per_q, solutions, bounds_per_q)

        assert report["bound_violations"] == []

    def test_enforced_bound_violation_does_not_publish_route_file(self, tmp_path):
        # Every vehicle that can serve M also traverses U, so M=10 and
        # U<=3 have no integer-feasible reconciliation.
        shapes = [Candidate(depart=0.0, edges=["M", "U"])]
        out = tmp_path / "out.rou.xml"
        out.write_text("previous valid route")

        with pytest.raises(RuntimeError, match="no route file was published"):
            write_calibration_report(
                shapes, out, [{"M": 10.0}], [np.array([10.0])],
                [{"U": (0.0, 3.0)}], enforce_integer_bounds=True)

        assert out.read_text() == "previous valid route"
        assert not (tmp_path / "out.rou.xml.tmp").exists()

    def test_bounds_per_q_is_optional_and_defaults_to_no_check(self, tmp_path):
        shapes = [Candidate(depart=0.0, edges=["M"])]
        solutions = [np.array([10.0])]
        targets_per_q = [{"M": 10.0}]

        report = write_calibration_report(
            shapes, tmp_path / "out.rou.xml", targets_per_q, solutions)

        assert report["bound_violations"] == []


class TestRelaxationRungTracking:
    """solve_interval_with_relaxation reports WHICH ladder stage produced a
    solution, and write_calibration_report rolls that up into a
    relaxation_summary diagnostic (2026-07-10) — a solver quietly living on
    the LP fallback every interval is a different health signal than one
    mostly converging clean, even when both hit 100% GEH."""

    def test_easy_interval_reports_clean_rung(self):
        shapes = [cand("A")]
        sol, rung = solve_interval_with_relaxation(
            shapes, {"A": 10.0}, {}, {})
        assert sol is not None
        assert rung == RUNG_CLEAN

    def test_infeasible_interval_reports_infeasible_rung(self):
        # A single candidate touching BOTH edges has only one degree of
        # freedom (its own vehicle count), so it cannot simultaneously hit
        # two different targets on the edges it shares — infeasible at
        # every rung, including the LP fallback.
        shapes = [cand("A", "B")]
        sol, rung = solve_interval_with_relaxation(
            shapes, {"A": 10.0, "B": 20.0}, {}, {})
        assert sol is None
        assert rung == RUNG_INFEASIBLE

    def test_solve_calibration_intervals_returns_rung_per_quarter(self):
        shapes = [cand("A"), cand("A", "B")]
        route_cost = path_size_weights(shapes)
        solutions, rungs = solve_calibration_intervals(
            shapes, route_cost,
            [{"A": 10.0}, {"A": 10.0, "B": 20.0}], [{}, {}], [{}, {}])
        assert len(solutions) == len(rungs) == 2
        assert rungs[0] == RUNG_CLEAN
        assert solutions[1] is None
        assert rungs[1] == RUNG_INFEASIBLE

    def test_write_calibration_report_summarizes_rungs(self, tmp_path):
        shapes = [cand("A")]
        solutions = [np.array([10.0]), None]
        targets_per_q = [{"A": 10.0}, {"B": 10.0}]
        rungs = [RUNG_CLEAN, RUNG_INFEASIBLE]

        report = write_calibration_report(
            shapes, tmp_path / "out.rou.xml", targets_per_q, solutions,
            rungs=rungs)

        assert report["relaxation_summary"] == {"clean": 1, "infeasible": 1}

    def test_relaxation_summary_absent_when_rungs_not_given(self, tmp_path):
        shapes = [cand("A")]
        solutions = [np.array([10.0])]
        targets_per_q = [{"A": 10.0}]

        report = write_calibration_report(
            shapes, tmp_path / "out.rou.xml", targets_per_q, solutions)

        assert "relaxation_summary" not in report


class TestCalibratedAgentProvenance:
    def test_calibrated_vehicles_keep_candidate_purpose_and_od(self, tmp_path):
        work = Candidate(
            depart=0.0, edges=["O", "M", "D1"], source_id="work-1",
            intent={"purpose": "work", "tour_id": "tour-1", "leg": "outbound",
                    "origin_edge": "O", "destination_edge": "D1"})
        service = Candidate(
            depart=0.0, edges=["O", "M", "D1"], source_id="service-1",
            intent={"purpose": "service", "tour_id": "tour-2", "leg": "outbound",
                    "origin_edge": "O", "destination_edge": "D1"})
        shape = Candidate(depart=0.0, edges=["O", "M", "D1"],
                          source_candidates=[work, service])
        out = tmp_path / "calibrated.rou.xml"

        write_calibration_report([shape], out, [{"M": 4.0}], [np.array([4.0])])

        agents = json.loads((tmp_path / "calibrated.agents.json").read_text())["agents"]
        assert len(agents) == 4
        assert {a["purpose"] for a in agents} == {"work", "service"}
        assert {a["origin_edge"] for a in agents} == {"O"}
        assert {a["destination_edge"] for a in agents} == {"D1"}
        assert len({a["departure_s"] for a in agents}) == 4

    def test_purpose_mix_is_preserved_for_the_calibrated_quarter(self, tmp_path):
        work = Candidate(depart=0.0, edges=["O", "M", "D"], source_id="w",
                         intent={"purpose": "arbete"})
        leisure = Candidate(depart=0.0, edges=["O", "M", "D"], source_id="f",
                            intent={"purpose": "fritid"})
        shape = Candidate(depart=0.0, edges=["O", "M", "D"],
                          source_candidates=[work, leisure])
        out = tmp_path / "calibrated.rou.xml"

        report = write_calibration_report(
            [shape], out, [{"M": 10.0}], [np.array([10.0])])

        agents = json.loads((tmp_path / "calibrated.agents.json").read_text())["agents"]
        assert {a["purpose"] for a in agents} == {"arbete", "fritid"}
        assert sum(a["purpose"] == "arbete" for a in agents) == 5
        assert sum(a["purpose"] == "fritid" for a in agents) == 5
        assert report["purpose_allocation"][0]["incompatible"] == {}

    def test_duplicate_route_copies_are_spread_not_platooned(self, tmp_path):
        # Regression (found reviewing 62a1584): the departure-order hash key
        # dropped the per-duplicate component, so all k copies of one shape
        # shared a key and the stable sort parked them in consecutive slots —
        # an identical-route platoon. Copies must interleave with other routes.
        shape_a = Candidate(depart=0.0, edges=["O", "M", "A"],
                            source_candidates=[Candidate(
                                0.0, ["O", "M", "A"], source_id="a",
                                intent={"purpose": "arbete"})])
        shape_b = Candidate(depart=0.0, edges=["O", "M", "B"],
                            source_candidates=[Candidate(
                                0.0, ["O", "M", "B"], source_id="b",
                                intent={"purpose": "arbete"})])
        out = tmp_path / "calibrated.rou.xml"

        write_calibration_report([shape_a, shape_b], out, [{"M": 16.0}],
                                 [np.array([8.0, 8.0])])

        routes_in_depart_order = re.findall(r'<route edges="([^"]+)"', out.read_text())
        assert sorted(routes_in_depart_order) == ["O M A"] * 8 + ["O M B"] * 8
        longest_run = max(len(list(g)) for _r, g in
                          itertools.groupby(routes_in_depart_order))
        assert longest_run < 8, (
            f"identical route occupies {longest_run} consecutive departure "
            f"slots — duplicates are platooning instead of interleaving")


class TestProvenanceAllocation:
    def test_scarce_purpose_keeps_a_compatible_route_instance(self):
        def source(route, purpose, source_id):
            return Candidate(0.0, route, source_id=source_id,
                             intent={"purpose": purpose})

        flexible = Candidate(0.0, ["O", "M", "A"], source_candidates=[
            source(["O", "M", "A"], "arbete", "a"),
            source(["O", "M", "A"], "service", "s"),
        ])
        leisure_only = Candidate(0.0, ["O", "M", "B"], source_candidates=[
            source(["O", "M", "B"], "fritid", "f"),
        ])

        selected, purposes, report = pfe.allocate_interval_provenance(
            [flexible, flexible, flexible, leisure_only],
            {"arbete": 2, "service": 1, "fritid": 1},
        )

        assert purposes.count("arbete") == 2
        assert purposes.count("service") == 1
        assert purposes.count("fritid") == 1
        assert [s.intent["purpose"] for s in selected] == purposes
        assert report["incompatible"] == {}

    def test_keeps_time_purpose_target_when_selected_routes_lack_provenance(self):
        work = Candidate(0.0, ["O", "M", "D"], source_id="w",
                         intent={"purpose": "arbete"})
        shape = Candidate(0.0, ["O", "M", "D"], source_candidates=[work])

        selected, purposes, report = pfe.allocate_interval_provenance(
            [shape, shape], {"arbete": 1, "fritid": 1})

        assert purposes == ["arbete", "fritid"]
        assert [s.intent["purpose"] for s in selected] == ["arbete", "arbete"]
        assert report["incompatible"] == {"fritid": 1}

    def test_lp_fallback_drops_groups_after_the_counts_first_rung(self, monkeypatch):
        # Force the final fallback. The measured count is feasible only after
        # the impossible group cap has been discarded.
        monkeypatch.setattr(pfe, "solve_interval_entropy", lambda *args, **kwargs: None)
        sol, rung = pfe.solve_interval_with_relaxation(
            [cand("M")], {"M": 10.0}, {}, {}, groups=[([0], 0.0, 3.0)])

        assert sol is not None
        # The continuous solver respects its standard ±max(2, 5%) measured
        # band; integer publication closes this to the exact target.
        assert served(sol, [cand("M")], "M") >= 8.0
        assert rung == RUNG_LP_FALLBACK
