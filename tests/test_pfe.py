"""Unit tests for the PFE-lite LP (pfe.py) — the level-4 reconciliation."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from pfe import Candidate, calibrate, largest_remainder_round, solve_interval


def cand(*edges):
    return Candidate(depart=0.0, edges=list(edges))


def served(x, cands, edge):
    return sum(xi for xi, c in zip(x, cands) if edge in c.edges)


class TestSolveInterval:
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


class TestRounding:
    def test_totals_preserved(self):
        x = np.array([0.3, 0.3, 0.4, 2.0])
        r = largest_remainder_round(x)
        assert r.sum() == 3
        assert r[3] == 2

    def test_integer_input_unchanged(self):
        x = np.array([1.0, 2.0, 0.0])
        assert (largest_remainder_round(x) == [1, 2, 0]).all()
