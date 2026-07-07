"""Unit tests for pfe.py — the level-4 reconciliation engine."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from pfe import (Candidate, EPS_PARSIMONY, calibrate, largest_remainder_round,
                 path_size_weights, solve_interval, solve_interval_entropy)


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
