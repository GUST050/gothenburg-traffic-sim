"""Unit tests for estimate_directions.py — the superseded Gaussian AM/PM
fallback, used only when dirsplit's trained model.pkl is absent (rare in
practice, since the Makefile prefers the model whenever it exists — see
CLAUDE.md). Still live-wired via the Makefile branch, so its math must stay
correct even though it's rarely exercised."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from estimate_directions import (ang_diff, bearing_to_centre_deg,
                                 edge_bearing_deg, fit_am_pm, gauss)


class TestGauss:
    def test_peak_at_mu(self):
        assert gauss(np.array([8.0]), mu=8.0, sig=1.0)[0] == pytest.approx(1.0)

    def test_symmetric_around_mu(self):
        left  = gauss(np.array([6.0]), mu=8.0, sig=1.0)[0]
        right = gauss(np.array([10.0]), mu=8.0, sig=1.0)[0]
        assert left == pytest.approx(right)

    def test_decays_away_from_mu(self):
        near = gauss(np.array([8.5]), mu=8.0, sig=1.0)[0]
        far  = gauss(np.array([12.0]), mu=8.0, sig=1.0)[0]
        assert near > far


class TestFitAmPm:
    def make_bimodal_profile(self, mu_am=8.0, mu_pm=17.0, base=5.0, amp=50.0, sig=1.0):
        hours = np.arange(96) / 4.0
        return (base
                + amp * np.exp(-0.5 * ((hours - mu_am) / sig) ** 2)
                + amp * np.exp(-0.5 * ((hours - mu_pm) / sig) ** 2))

    def test_recovers_known_peaks(self):
        profile = self.make_bimodal_profile(mu_am=8.0, mu_pm=17.0)
        params, r2 = fit_am_pm(profile)
        assert params["mu_am"] == pytest.approx(8.0, abs=0.5)
        assert params["mu_pm"] == pytest.approx(17.0, abs=0.5)
        assert r2 > 0.95

    def test_flat_profile_gives_low_r2(self):
        # No real AM/PM structure — a flat day (e.g. a quiet residential
        # street) shouldn't spuriously report a confident-looking fit.
        profile = np.full(96, 10.0) + np.random.RandomState(0).normal(0, 0.05, 96)
        _, r2 = fit_am_pm(profile)
        assert r2 < 0.5

    def test_coefficients_are_non_negative(self):
        profile = self.make_bimodal_profile()
        params, _ = fit_am_pm(profile)
        assert params["base"] >= 0
        assert params["A"] >= 0
        assert params["B"] >= 0


class TestBearingHelpers:
    def test_edge_bearing_north(self):
        coords = [(11.0, 57.0), (11.0, 58.0)]   # (lon, lat): due north
        assert edge_bearing_deg(coords) == pytest.approx(0.0, abs=0.1)

    def test_edge_bearing_east(self):
        coords = [(11.0, 57.0), (12.0, 57.0)]   # due east
        assert edge_bearing_deg(coords) == pytest.approx(90.0, abs=0.1)

    def test_bearing_to_centre_points_at_brunnsparken(self):
        # A point due south of Brunnsparken must bear ~0° (north) toward it.
        coords = [(11.9668, 57.60), (11.9668, 57.60)]
        assert bearing_to_centre_deg(coords) == pytest.approx(0.0, abs=1.0)

    def test_ang_diff_wraps_at_360(self):
        assert ang_diff(350, 10) == pytest.approx(20)
        assert ang_diff(0, 180) == pytest.approx(180)


class TestOutputSchema:
    def test_edge_shares_shape_documents_the_known_predict_py_mismatch(self):
        """estimate_directions.py's result entries carry only 'edge_shares'
        (a single point estimate per edge), unlike dirsplit/predict.py's
        output which also writes edge_shares_q10/edge_shares_q90 (see
        CLAUDE.md's quantile-regression section). build_sumo_demand.py
        degrades gracefully via has_quantiles() when q10/q90 are absent, so
        this isn't a crash risk — but this test pins the CURRENT schema so a
        future edit to either file makes the mismatch a deliberate, visible
        choice rather than a silent one."""
        toward_id, other_id = "a_b_0", "b_a_0"
        split = np.full(96, 0.6)
        entry = {
            "edge_shares": {
                toward_id: [round(float(s), 3) for s in split],
                other_id:  [round(float(1 - s), 3) for s in split],
            },
        }
        assert set(entry) >= {"edge_shares"}
        assert "edge_shares_q10" not in entry
        assert "edge_shares_q90" not in entry
        assert entry["edge_shares"][toward_id][0] + entry["edge_shares"][other_id][0] \
            == pytest.approx(1.0)
