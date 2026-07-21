"""Unit tests for pfe.py — the level-4 reconciliation engine."""

import itertools
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import pfe
from pfe import (Candidate, EPS_PARSIMONY, RUNG_CLEAN, RUNG_INFEASIBLE,
                 RUNG_LP_FALLBACK, RUNG_RELAX_TOL4X, calibrate,
                 largest_remainder_round, path_size_weights,
                 solve_calibration_intervals, solve_interval,
                 solve_interval_entropy, solve_interval_with_relaxation,
                 solve_interval_with_structure_guard, write_calibration_report)


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

    def test_activity_margin_survives_candidate_filtering(self, tmp_path):
        """An independently supplied activity margin must override only the
        activity split in the surviving candidate histogram. This is also the
        path used by optional congestion-feedback iterations."""
        cand_path = tmp_path / "candidates.rou.xml"
        cand_path.write_text(
            "<routes>"
            '<vehicle id="t0" depart="0"><route edges="Ot M Dt"/></vehicle>'
            '<vehicle id="t1" depart="0"><route edges="Ot M Dt"/></vehicle>'
            '<vehicle id="w0" depart="0"><route edges="Ow M Dw"/></vehicle>'
            '<vehicle id="w1" depart="0"><route edges="Ow M Dw"/></vehicle>'
            '<vehicle id="w2" depart="0"><route edges="Ow M Dw"/></vehicle>'
            '<vehicle id="w3" depart="0"><route edges="Ow M Dw"/></vehicle>'
            '<vehicle id="f0" depart="0"><route edges="Of M Df"/></vehicle>'
            "</routes>")
        (tmp_path / "candidates.meta.json").write_text(json.dumps({
            "candidates": {
                "t0": {"purpose": "through"}, "t1": {"purpose": "through"},
                "w0": {"purpose": "arbete"}, "w1": {"purpose": "arbete"},
                "w2": {"purpose": "arbete"}, "w3": {"purpose": "arbete"},
                "f0": {"purpose": "fritid"},
            }
        }))

        out_path = tmp_path / "calibrated.rou.xml"
        report = calibrate(
            cand_path, out_path, [{"M": 70.0}], [{}], [{}],
            activity_purpose_shares_by_quarter=[{"arbete": 0.2, "fritid": 0.8}])
        agents = json.loads((tmp_path / "calibrated.agents.json").read_text())["agents"]

        assert report["achieved"]["M"] == [70.0]
        assert Counter(agent["purpose"] for agent in agents) == Counter({
            "through": 20, "arbete": 10, "fritid": 40,
        })

    def test_through_target_preserves_total_and_external_margin(self):
        source = Counter({
            "through": 59, "external": 6,
            "arbete": 20, "service": 10, "fritid": 5,
        })

        result = pfe.apply_through_share_target([source], 0.25)[0]

        assert sum(result.values()) == pytest.approx(100)
        assert result["through"] / sum(result.values()) == pytest.approx(0.25)
        assert result["external"] == pytest.approx(6)
        assert result["arbete"] / result["service"] == pytest.approx(2)
        assert result["service"] / result["fritid"] == pytest.approx(2)

    def test_through_target_rejects_invalid_enabled_value(self):
        with pytest.raises(ValueError, match=r"within \(0, 1\)"):
            pfe.apply_through_share_target([Counter({"arbete": 1})], 1.0)


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
    """`groups` (2026-07-12; see IMPROVEMENT_PLAN.md): a band
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

    def test_structure_guard_does_not_drop_bound_for_optional_cap(self):
        # The unconstrained pass satisfies both M=100 and U<=50. Enforcing
        # B's 20% group cap would need to drop U's bound; the guard must keep
        # the stronger first solution rather than accept RUNG_RELAX_NOBND.
        cands = [cand("m", "u"), cand("m")]
        sol, rung = solve_interval_with_structure_guard(
            cands, {"m": 100.0}, {"u": (0.0, 50.0)}, {},
            structure_groups=[("optional_cap", [1], 0.2)])

        assert sol is not None
        assert rung == RUNG_CLEAN
        assert served(sol, cands, "m") == pytest.approx(100, rel=0.06)
        assert served(sol, cands, "u") <= 50.0 + 1e-6

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

    def test_repair_uses_the_solver_band_only_when_exact_is_infeasible(self):
        # The continuous solution may legitimately use RUNG_RELAX_TOL4X.
        # Reimposing M=10 exactly at the integer stage would conflict with
        # U<=6, but M=6 lies within the same allowed measurement band.
        from pfe import repair_integer_bounds
        cands = [cand("M", "U")]
        repaired = repair_integer_bounds(
            np.array([10]), cands, {"M": 10.0}, {"U": (0.0, 6.0)},
            measurement_tol_mult=4.0)

        assert repaired is not None
        assert repaired.tolist() == [6]

    def test_writer_obeys_relaxed_measurement_band_at_integer_stage(self, tmp_path):
        # Integration guard for the live failure class: the continuous value
        # is valid under the solver's q4 tolerance and the post-rounding MILP
        # must preserve the bound instead of rejecting publication.
        shapes = [Candidate(depart=0.0, edges=["M", "U"])]
        out = tmp_path / "out.rou.xml"
        report = write_calibration_report(
            shapes, out, [{"M": 10.0}], [np.array([6.0])],
            [{"U": (0.0, 6.0)}], [RUNG_RELAX_TOL4X],
            enforce_integer_bounds=True)

        assert out.exists()
        assert report["achieved"]["M"] == [6.0]
        assert report["achieved"]["U"] == [6.0]
        assert report["bound_violations"] == []

    def test_optional_structure_repair_uses_the_solver_measurement_band(
            self, tmp_path, monkeypatch):
        """A relaxed interval must not regain an exact-count constraint.

        The live q79 build was count-valid on the tol4 rung, but its optional
        short-trip repair called the integer solver without that rung.  The
        repair therefore reimposed exact rounded sensor totals, immediately
        reported infeasible, and retained 24 short trips against a cap of 2.
        """
        shapes = [Candidate(depart=0.0, edges=["M", "SHORT"]),
                  Candidate(depart=0.0, edges=["M", "LONG"])]
        calls = []

        def repair_with_relaxed_band(counts, _shapes, _targets, _bounds,
                                     groups=None, measurement_tol_mult=None):
            calls.append(measurement_tol_mult)
            if measurement_tol_mult != 4.0:
                return None
            return np.array([2, 8])

        monkeypatch.setattr(pfe, "repair_integer_bounds",
                            repair_with_relaxed_band)
        out = tmp_path / "out.rou.xml"
        write_calibration_report(
            shapes, out, [{"M": 10.0}], [np.array([8.0, 2.0])],
            bounds_per_q=[{}], rungs=[RUNG_RELAX_TOL4X],
            enforce_integer_bounds=True,
            structure_groups=[([0], 0.2)])

        assert calls == [4.0]
        route_counts = Counter(
            vehicle.find("route").get("edges")
            for vehicle in ET.parse(out).getroot().iter("vehicle"))
        assert route_counts == {"M SHORT": 2, "M LONG": 8}

    def test_optional_structure_cap_never_blocks_hard_bound_repair(self, tmp_path):
        # This is the production failure mode from 2027-02-26 q94 in small
        # form.  The rounded sensor count M is exact, but it puts seven
        # vehicles on U (cap six).  Shifting one vehicle to the M-only route
        # repairs U.  The optional group caps that M-only route at two, so
        # the combined group+bound MILP is impossible.  The group must not
        # prevent publication of the valid sensor+hard-bound solution.
        shapes = [Candidate(depart=0.0, edges=["M", "U"]),
                  Candidate(depart=0.0, edges=["M"])]
        report = write_calibration_report(
            shapes, tmp_path / "out.rou.xml", [{"M": 10.0}],
            [np.array([7.0, 3.0])], [{"U": (0.0, 6.0)}],
            enforce_integer_bounds=True,
            structure_groups=[([1], 0.1)])

        assert report["achieved"]["M"] == [10.0]
        assert report["achieved"]["U"] == [6.0]
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

    def test_repeated_candidate_draws_distinct_anonymous_endpoint_positions(self, tmp_path):
        """A PFE-repeated route must not visually restart at one junction.

        The route shape stays identical (so counts and routing are unchanged),
        but each emitted vehicle is assigned a low-discrepancy draw from the
        candidate's physical home access pool.
        """
        pools = {
            "home:O": [
                {"id": "h1", "p": 12.0, "w": 1.0, "kind": "home"},
                {"id": "h2", "p": 46.0, "w": 1.0, "kind": "home"},
            ]
        }
        source = Candidate(
            depart=0.0, edges=["O", "M", "D"], source_id="source-1",
            intent={"purpose": "arbete", "origin_edge": "O",
                    "destination_edge": "D", "origin_location_pool": "home:O"},
            location_pools=pools)
        shape = Candidate(depart=0.0, edges=["O", "M", "D"],
                          source_candidates=[source])
        out = tmp_path / "calibrated.rou.xml"

        write_calibration_report([shape], out, [{"M": 4.0}], [np.array([4.0])])

        positions = re.findall(r'departPos="([0-9.]+)"', out.read_text())
        assert len(positions) == 4
        assert set(positions) == {"12.00", "46.00"}
        agents = json.loads((tmp_path / "calibrated.agents.json").read_text())["agents"]
        assert {agent["origin_location_id"] for agent in agents} == {"h1", "h2"}


class TestOriginDistributionGuard:
    def test_origin_groups_do_not_depend_on_map_geometry(self, monkeypatch, tmp_path):
        """A missing reporting GeoJSON must not disable endpoint protection."""
        from demand import structure
        monkeypatch.setattr(structure, "GEO_PATH", tmp_path / "missing.geojson")
        monkeypatch.setattr(structure, "_EDGE_GEOMETRY_CACHE", None)
        shapes = []
        for origin in ("O", "P", "Q", "R"):
            source = Candidate(0.0, [origin, "M"], source_id=f"source-{origin}",
                               intent={"origin_edge": origin})
            shapes.append(Candidate(0.0, [origin, "M"], source_candidates=[source]))

        groups = structure.structure_groups_for_shapes(shapes)

        assert groups == [
            ("origin_edge:O", [0], 0.75),
            ("origin_edge:P", [1], 0.75),
            ("origin_edge:Q", [2], 0.75),
            ("origin_edge:R", [3], 0.75),
        ]


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

    def test_purpose_lands_on_the_route_its_generation_favoured(self):
        # Regression (measured 2026-07-13): fritid candidates are the LONG
        # trips, but compatibility-only assignment let fritid stamps land on
        # short arbete-dominated shapes — calibrated P(length|purpose) came
        # out inverted vs the pool. Each category must prefer the routes
        # whose own source pool it dominates.
        def source(route, purpose, source_id):
            return Candidate(0.0, route, source_id=source_id,
                             intent={"purpose": purpose})

        long_route = ["O", "M", "FAR1", "FAR2"]
        short_route = ["O", "M", "NEAR"]
        mostly_fritid_long = Candidate(0.0, long_route, source_candidates=[
            source(long_route, "fritid", "f1"),
            source(long_route, "fritid", "f2"),
            source(long_route, "arbete", "a1"),
        ])
        mostly_arbete_short = Candidate(0.0, short_route, source_candidates=[
            source(short_route, "arbete", "a2"),
            source(short_route, "arbete", "a3"),
            source(short_route, "fritid", "f3"),
        ])

        selected, purposes, report = pfe.allocate_interval_provenance(
            [mostly_fritid_long, mostly_arbete_short],
            {"arbete": 1, "fritid": 1})

        assert purposes[0] == "fritid" and purposes[1] == "arbete", (
            "fritid must be stamped on the fritid-dominated (long) route, "
            f"got {purposes}")
        assert [s.intent["purpose"] for s in selected] == purposes
        assert report["incompatible"] == {}

    def test_length_aware_mode_gives_long_routes_to_long_categories(self):
        # Measured 2026-07-13 (2nd round): compatibility-first assignment
        # stamped fritid on the SHORT tail of its compatible supply (median
        # 1.44 km vs pool 3.1 km) because PFE's purpose-blind selection is
        # what picks the geometry. Length-aware mode must hand the longest
        # instances to the categories whose candidates are longest.
        def source(route, purpose, source_id):
            return Candidate(0.0, route, source_id=source_id,
                             intent={"purpose": purpose})

        long_r, short_r = ["O", "M", "F1", "F2"], ["O", "M", "N"]
        both_long = Candidate(0.0, long_r, source_candidates=[
            source(long_r, "arbete", "a1"), source(long_r, "fritid", "f1")])
        both_short = Candidate(0.0, short_r, source_candidates=[
            source(short_r, "arbete", "a2"), source(short_r, "fritid", "f2")])

        selected, purposes, report = pfe.allocate_interval_provenance(
            [both_short, both_long], {"arbete": 1, "fritid": 1},
            lengths_km=[1.2, 4.0],
            category_length_km={"arbete": 2.9, "fritid": 3.1})

        assert purposes == ["arbete", "fritid"], (
            f"long instance must be fritid (longer category mean), got {purposes}")
        assert [s.intent["purpose"] for s in selected] == purposes
        assert report["incompatible"] == {}
        assert report["achieved"] == {"arbete": 1, "fritid": 1}

    def test_length_aware_mode_discloses_forced_incompatibility(self):
        work = Candidate(0.0, ["O", "M", "D"], source_candidates=[
            Candidate(0.0, ["O", "M", "D"], source_id="w",
                      intent={"purpose": "arbete"})])
        selected, purposes, report = pfe.allocate_interval_provenance(
            [work, work], {"arbete": 1, "fritid": 1},
            lengths_km=[2.0, 2.0],
            category_length_km={"arbete": 2.9, "fritid": 3.1})
        assert sorted(purposes) == ["arbete", "fritid"]
        assert report["incompatible"] == {"fritid": 1}

    def test_same_measured_signature_can_replace_uncompatible_leg(self):
        work_route = ["O", "M", "D_work"]
        leisure_route = ["O", "M", "D_leisure"]
        work = Candidate(0.0, work_route, source_id="work",
                         intent={"purpose": "arbete"})
        leisure = Candidate(0.0, leisure_route, source_id="leisure",
                            intent={"purpose": "fritid"})
        shape_work = Candidate(0.0, work_route, source_candidates=[work])
        shape_leisure = Candidate(0.0, leisure_route,
                                  source_candidates=[leisure])
        replacements = pfe.purpose_replacement_index(
            [shape_work, shape_leisure], {"M"})
        selected, purposes, report = pfe.allocate_interval_provenance(
            [shape_work, shape_work], {"arbete": 1, "fritid": 1},
            purpose_replacements=replacements, measured_edges={"M"})

        assert purposes == ["arbete", "fritid"]
        assert selected[1].edges == leisure_route
        assert report["incompatible"] == {}
        assert report["replaced_routes"] == 1

    def test_report_replacement_preserves_measured_count(self, tmp_path):
        work_route = ["O", "M", "D_work"]
        leisure_route = ["O", "M", "D_leisure"]
        work = Candidate(0.0, work_route, source_id="work",
                         intent={"purpose": "arbete"})
        leisure = Candidate(0.0, leisure_route, source_id="leisure",
                            intent={"purpose": "fritid"})
        shapes = [Candidate(0.0, work_route, source_candidates=[work]),
                  Candidate(0.0, leisure_route, source_candidates=[leisure])]
        out = tmp_path / "calibrated.rou.xml"
        report = pfe.write_calibration_report(
            shapes, out, [{"M": 2.0}], [np.array([2.0, 0.0])],
            edge_length_m={"O": 10.0, "M": 10.0, "D_work": 10.0,
                           "D_leisure": 10.0})
        root = ET.parse(out).getroot()
        routes = [v.find("route").get("edges") for v in root.findall("vehicle")]
        assert sum("M" in route.split() for route in routes) == 2
        assert any("D_leisure" in route for route in routes)
        assert report["purpose_allocation_summary"]["replaced_routes"] == 1

    def test_purpose_replacement_cannot_bypass_an_unmeasured_hard_bound(self, tmp_path):
        # The selected work shape and the leisure alternative both cross M,
        # but only the latter enters D_leisure.  A provenance replacement used
        # to happen AFTER ``achieved`` was counted, so D_leisure<=0 passed on
        # paper while the published XML contained leisure routes.
        work_route = ["O", "M", "D_work"]
        leisure_route = ["O", "M", "D_leisure"]
        work = Candidate(0.0, work_route, source_id="work",
                         intent={"purpose": "arbete"})
        leisure = Candidate(0.0, leisure_route, source_id="leisure",
                            intent={"purpose": "fritid"})
        shapes = [Candidate(0.0, work_route, source_candidates=[work]),
                  Candidate(0.0, leisure_route, source_candidates=[leisure])]
        out = tmp_path / "calibrated.rou.xml"

        report = pfe.write_calibration_report(
            shapes, out, [{"M": 2.0}], [np.array([2.0, 0.0])],
            [{"D_leisure": (0.0, 0.0)}],
            enforce_integer_bounds=True)

        routes = [vehicle.find("route").get("edges")
                  for vehicle in ET.parse(out).getroot().findall("vehicle")]
        assert all("D_leisure" not in route.split() for route in routes)
        assert report["achieved"].get("D_leisure", [0.0]) == [0.0]
        assert report["bound_violations"] == []
        assert report["purpose_allocation_summary"]["replaced_routes"] == 0

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


class TestPurposeStratifiedCalibration:
    """A calibrated agent must retain the OD/purpose that generated it.

    These regressions cover the defect found in the 2026-07-17 baseline:
    the purpose-blind PFE chose home/POI routes and a post-solve allocator
    then labelled 3,344 of them as ``through`` to satisfy a display mix.
    """

    @staticmethod
    def _shape(edges, purpose, source_id):
        source = Candidate(0.0, edges, source_id=source_id,
                           intent={"purpose": purpose})
        return Candidate(0.0, edges, source_candidates=[source])

    def test_solver_selects_the_required_provenance_before_publication(self, tmp_path):
        through = self._shape(["O_t", "M", "D_t"], "through", "t")
        work = self._shape(["O_w", "M", "D_w"], "arbete", "w")
        shapes = [through, work]
        mix = {"through": 1, "arbete": 1}

        sol, rung = pfe.solve_interval_with_structure_guard(
            shapes, {"M": 2.0}, {}, {}, purpose_mix=mix)

        assert sol is not None
        assert rung == RUNG_CLEAN
        out = tmp_path / "calibrated.rou.xml"
        report = pfe.write_calibration_report(
            shapes, out, [{"M": 2.0}], [sol], rungs=[rung],
            purpose_mixes_per_q=[mix])
        agents = json.loads((tmp_path / "calibrated.agents.json").read_text())["agents"]

        assert sorted(agent["purpose"] for agent in agents) == ["arbete", "through"]
        assert all(agent["purpose_route_compatible"] for agent in agents)
        assert report["purpose_allocation_summary"]["quarters_with_incompatible_routes"] == 0

    def test_missing_purpose_candidate_is_an_explicit_error(self):
        work = self._shape(["O", "M", "D"], "arbete", "w")

        with pytest.raises(ValueError, match="fritid"):
            pfe.purpose_quota_groups([work], {"fritid": 1}, 1)

    def test_signature_coverage_audit_reports_missing_purpose_without_fabrication(self):
        work = self._shape(["O", "M", "D"], "arbete", "w")
        leisure = self._shape(["O", "M", "D"], "fritid", "f")
        index = pfe.purpose_replacement_index([work, leisure], {"M"})
        assert pfe.purpose_signature_coverage(index, ["arbete", "fritid"])["missing_by_purpose"] == {
            "arbete": 0, "fritid": 0}
        partial = pfe.purpose_replacement_index([work], {"M"})
        assert pfe.purpose_signature_coverage(partial, ["arbete", "fritid"])["missing_by_purpose"]["fritid"] == 1

    def test_prepare_keeps_same_geometry_in_separate_purpose_variables(self, tmp_path):
        routes = tmp_path / "candidates.rou.xml"
        routes.write_text(
            '<routes>'
            '<vehicle id="w" depart="0"><route edges="O M D"/></vehicle>'
            '<vehicle id="f" depart="0"><route edges="O M D"/></vehicle>'
            '</routes>')
        (tmp_path / "candidates.meta.json").write_text(json.dumps({
            "candidates": {
                "w": {"purpose": "arbete"},
                "f": {"purpose": "fritid"},
            }
        }))

        shapes, _route_cost = pfe.prepare_calibration(routes)

        assert len(shapes) == 2
        assert {next(iter(pfe._shape_purposes(shape))) for shape in shapes} == {
            "arbete", "fritid"}

    def test_purpose_mix_uses_the_calibrated_window_clock(self):
        # Candidate departures are absolute seconds into the behavioural day.
        # A 06:00 target quarter must not read the midnight candidate mix.
        night = self._shape(["N"], "through", "night")
        day = self._shape(["D"], "arbete", "day")
        night.source_candidates[0].depart = 0.0
        day.source_candidates[0].depart = 6 * 3600.0

        mix = pfe._purpose_targets_per_quarter(
            [night, day], 1, departure_offset_s=6 * 3600.0)

        assert [dict(q) for q in mix] == [{"arbete": 1}]

    def test_impossible_purpose_margin_keeps_counts_and_real_provenance(self, tmp_path):
        # The measured W count requires one work route, while the generated
        # purpose prior asks for two through trips. The old strict-margin
        # change turned this into an infeasible quarter. Counts are observed
        # and must win, but neither selected route may be relabelled.
        through = self._shape(["O_t", "M", "D_t"], "through", "t")
        work = self._shape(["O_w", "M", "W", "D_w"], "arbete", "w")
        shapes = [through, work]
        mix = {"through": 20}

        sol, rung = pfe.solve_interval_with_structure_guard(
            shapes, {"M": 20.0, "W": 10.0}, {}, {}, purpose_mix=mix)

        assert sol is not None
        out = tmp_path / "calibrated.rou.xml"
        report = pfe.write_calibration_report(
            shapes, out, [{"M": 20.0, "W": 10.0}], [sol], rungs=[rung],
            purpose_mixes_per_q=[mix])
        agents = json.loads((tmp_path / "calibrated.agents.json").read_text())["agents"]

        assert report["achieved"]["M"] == [20.0]
        assert report["achieved"]["W"] == [10.0]
        assert all(agent["purpose_route_compatible"] for agent in agents)
        summary = report["purpose_allocation_summary"]
        assert summary["quarters_with_incompatible_routes"] == 0
        assert summary["quarters_with_relaxed_mix"] == 1
        assert summary["mix_shortfall_by_purpose"] == {"through": 10}
        assert summary["mix_excess_by_purpose"] == {"arbete": 10}

    def test_integer_writer_retries_purpose_margin_from_count_valid_vector(
            self, tmp_path, monkeypatch):
        """A failed joint repair is not proof that the margin is infeasible.

        The full-day baseline found four quarters where the local integer
        repair failed from the directly rounded entropy vector but succeeded
        after count/bound reconciliation supplied a better starting point.
        """
        through = self._shape(["M"], "through", "t")
        work = self._shape(["M"], "arbete", "w")
        shapes = [through, work]
        mix = {"through": 10, "arbete": 10}
        calls = []

        def staged_repair(counts, _shapes, _targets, _bounds, groups=None,
                          measurement_tol_mult=None):
            calls.append(bool(groups))
            if len(calls) == 1:
                return None                 # initial joint repair
            if len(calls) == 2:
                return np.array([20, 0])    # count/bound-valid warm start
            if len(calls) == 3:
                return None                 # first purpose warm-start retry
            return np.array([10, 10])       # structure then final purpose retry

        monkeypatch.setattr(pfe, "repair_integer_bounds", staged_repair)
        out = tmp_path / "calibrated.rou.xml"
        report = pfe.write_calibration_report(
            shapes, out, [{"M": 20.0}], [np.array([10.0, 10.0])],
            bounds_per_q=[{}], rungs=[RUNG_CLEAN],
            enforce_integer_bounds=True, purpose_mixes_per_q=[mix],
            structure_groups=[([0], 0.5)])

        assert calls == [True, False, True, True, True]
        assert report["purpose_allocation_summary"][
            "quarters_with_relaxed_mix"] == 0
        agents = json.loads(
            (tmp_path / "calibrated.agents.json").read_text())["agents"]
        assert Counter(agent["purpose"] for agent in agents) == {
            "arbete": 10, "through": 10}

    def test_infeasible_solution_does_not_touch_existing_route_file(self, tmp_path):
        out = tmp_path / "calibrated.rou.xml"
        out.write_text("previous valid route")

        with pytest.raises(RuntimeError, match="infeasible interval"):
            pfe.write_calibration_report(
                [self._shape(["M"], "through", "t")], out,
                [{"M": 1.0}], [None], enforce_integer_bounds=True)

        assert out.read_text() == "previous valid route"


class TestPrecomputedQuarterCounts:
    """Stage A1 (SPEED_ARCHITECTURE_PLAN 2026-07-21): the per-quarter integer
    repair is a pure function that may run in a fork pool, while the writer
    stays serial because vehicle ids and endpoint draw ordinals accumulate
    across quarters.

    The guarantee that makes it safe is byte identity: publishing with
    precomputed counts must produce exactly the artifacts the inline path
    produces. These tests hold the two paths to that, including through the
    sparse transport the parallel orchestration actually uses.
    """

    @staticmethod
    def _shape(edges, purpose, source_id):
        source = Candidate(0.0, edges, source_id=source_id,
                           intent={"purpose": purpose})
        return Candidate(0.0, edges, source_candidates=[source])

    def _case(self):
        # A measured edge shared by two purposes, plus a separately bounded
        # edge so round_preserving_measured's nudges actually trigger the
        # constrained integer repair rather than a trivial rounding.
        shapes = [
            self._shape(["O_t", "M", "D_t"], "through", "t"),
            self._shape(["O_w", "M", "U", "D_w"], "arbete", "w"),
            self._shape(["O_f", "M", "U", "D_f"], "fritid", "f"),
        ]
        targets = [{"M": 9.0 + q} for q in range(6)]
        bounds = [{"U": (0.0, 4.0)} for _ in range(6)]
        mixes = [Counter({"through": 4 + q, "arbete": 3, "fritid": 2})
                 for q in range(6)]
        solutions, rungs = [], []
        for q in range(6):
            sol, rung = pfe.solve_interval_with_structure_guard(
                shapes, targets[q], bounds[q], {}, purpose_mix=mixes[q])
            solutions.append(sol)
            rungs.append(rung)
        assert all(sol is not None for sol in solutions)
        return shapes, targets, bounds, mixes, solutions, rungs

    def _write(self, tmp_path, name, precomputed=None):
        shapes, targets, bounds, mixes, solutions, rungs = self._case()
        out = tmp_path / name
        report = write_calibration_report(
            shapes, out, targets, solutions, bounds, rungs,
            enforce_integer_bounds=True, purpose_mixes_per_q=mixes,
            precomputed_counts=precomputed)
        agents = out.with_name(out.name.replace(".rou.xml", ".agents.json"))
        return report, out.read_bytes(), agents.read_bytes()

    def _precompute(self, transport):
        shapes, targets, bounds, mixes, solutions, rungs = self._case()
        precomputed = []
        for q, sol in enumerate(solutions):
            counts, enforced = pfe.quarter_publish_counts(
                shapes, sol, targets[q], bounds[q], rungs[q],
                mixes[q], True, True, None)
            precomputed.append(transport(counts, enforced))
        return precomputed

    def test_precomputed_counts_publish_identical_bytes(self, tmp_path):
        inline_report, inline_xml, inline_agents = self._write(
            tmp_path, "inline.rou.xml")
        precomputed = self._precompute(lambda counts, enforced: (counts, enforced))
        pre_report, pre_xml, pre_agents = self._write(
            tmp_path, "precomputed.rou.xml", precomputed)

        assert pre_xml == inline_xml
        assert pre_agents == inline_agents
        assert pre_report == inline_report
        assert inline_report["vehicles"] > 0

    def test_sparse_transport_round_trip_is_exact(self, tmp_path):
        # The orchestration ships only the nonzero entries between processes
        # (a dense vector per variant-quarter would be hundreds of MB on a
        # multi-day build). Reconstruction must be exact, dtype included.
        def through_sparse(counts, enforced):
            idx = np.nonzero(counts)[0]
            packed = (idx.astype(np.int64), counts[idx], counts.dtype.str,
                      len(counts))
            rebuilt = np.zeros(packed[3], dtype=np.dtype(packed[2]))
            rebuilt[packed[0]] = packed[1]
            assert rebuilt.dtype == counts.dtype
            assert np.array_equal(rebuilt, counts)
            return rebuilt, enforced

        _inline_report, inline_xml, inline_agents = self._write(
            tmp_path, "inline2.rou.xml")
        _report, xml, agents = self._write(
            tmp_path, "sparse.rou.xml", self._precompute(through_sparse))

        assert xml == inline_xml
        assert agents == inline_agents

    def test_partially_precomputed_quarters_fall_back_inline(self, tmp_path):
        # Fail-closed shape: a missing entry is recomputed, never skipped.
        precomputed = self._precompute(lambda counts, enforced: (counts, enforced))
        precomputed[2] = None
        precomputed[4] = None
        _inline_report, inline_xml, inline_agents = self._write(
            tmp_path, "inline3.rou.xml")
        _report, xml, agents = self._write(tmp_path, "mixed.rou.xml", precomputed)

        assert xml == inline_xml
        assert agents == inline_agents


class TestPerDayEndpointDrawOrdinals:
    """Stage B: endpoint draw ordinals restart at each calendar-day boundary.

    Carrying them across midnight made day N of a window depend on days
    1..N-1, so the same date published different vehicles in different
    windows — the last coupling that stops a day being built once and reused.
    """

    @staticmethod
    def _shapes():
        pools = {"home:E": [{"id": f"loc{i}", "kind": "building",
                             "p": 10.0 * i, "w": 1.0}
                            for i in range(8)]}
        source = Candidate(0.0, ["E"], source_id="home:E",
                           intent={"purpose": "arbete",
                                   "origin_location_pool": "home:E",
                                   "destination_location_pool": "home:E"},
                           location_pools=pools)
        return [Candidate(0.0, ["E"], location_pools=pools,
                          source_candidates=[source])]

    def _positions(self, tmp_path, name, nq, day_quarters):
        shapes = self._shapes()
        out = tmp_path / name
        write_calibration_report(
            shapes, out, [{"E": 2.0} for _ in range(nq)],
            [np.array([2.0]) for _ in range(nq)], day_quarters=day_quarters)
        agents = json.loads(
            out.with_name(out.name.replace(".rou.xml", ".agents.json")).read_text()
        )["agents"]
        return [agent.get("origin_position_m") for agent in agents]

    def test_second_day_repeats_the_first_days_draw_sequence(self, tmp_path):
        two_days = self._positions(tmp_path, "two.rou.xml", 192, 96)
        one_day = self._positions(tmp_path, "one.rou.xml", 96, 96)

        assert two_days[:len(one_day)] == one_day
        # The whole point: day two is the same sequence again, not a
        # continuation of day one's counters.
        assert two_days[len(one_day):] == one_day

    def test_vehicle_order_within_a_quarter_is_day_local(self, tmp_path):
        # Departure order inside a quarter is a deterministic scramble. Keyed
        # by the ABSOLUTE quarter it would differ between "day 2 of a window"
        # and "the same date built alone"; keyed within the day it matches.
        shapes = self._shapes()
        out = tmp_path / "order.rou.xml"
        nq = 192
        write_calibration_report(
            shapes, out, [{"E": 3.0} for _ in range(nq)],
            [np.array([3.0]) for _ in range(nq)], day_quarters=96)
        departs = [float(veh.get("depart"))
                   for veh in ET.parse(out).getroot().iter("vehicle")]
        first_day = [d for d in departs if d < 86400.0]
        second_day = [d - 86400.0 for d in departs if d >= 86400.0]

        assert second_day == pytest.approx(first_day)

    def test_no_day_structure_keeps_the_continuous_sequence(self, tmp_path):
        continuous = self._positions(tmp_path, "cont.rou.xml", 192, None)
        one_day = self._positions(tmp_path, "cont_one.rou.xml", 96, None)

        assert continuous[:len(one_day)] == one_day
        assert continuous[len(one_day):] != one_day
