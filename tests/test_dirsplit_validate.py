"""Unit tests for the direction-split validation harness.

These cover the pure numeric core only — the leave-city-out run needs lightgbm
and the OSM graph and is exercised by running `python3 -m dirsplit.validate`,
whose output is checked into `validation/`.

The load-bearing test is `TestNestedLambda::test_scored_city_never_informs_its_own_lambda`:
the whole point of this module is that the published figure fits the shrinkage
coefficient on the rows it then scores, and a regression there would silently
restore the very leak the harness exists to measure.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from dirsplit.validate import (apply_deployment_interval,
                               bootstrap_nested_delta, fit_lambda,
                               interval_coverage, nested_lambda_scores,
                               summarize_orientation)


class TestFitLambda:
    def test_recovers_a_known_slope(self):
        dp = np.array([0.2, -0.1, 0.3, -0.4])
        assert fit_lambda(dp, 0.5 * dp) == pytest.approx(0.5)

    def test_perfect_prediction_gives_one(self):
        dp = np.array([0.2, -0.1, 0.3])
        assert fit_lambda(dp, dp) == pytest.approx(1.0)

    def test_anticorrelated_clips_to_zero(self):
        # A model pointing the wrong way collapses to 50/50 rather than
        # being inverted into a negative-weight predictor.
        dp = np.array([0.2, -0.1, 0.3])
        assert fit_lambda(dp, -dp) == 0.0

    def test_overshooting_prediction_shrinks_below_one(self):
        dp = np.array([0.4, -0.4, 0.2])
        assert 0.0 < fit_lambda(dp, 0.25 * dp) < 1.0

    def test_zero_prediction_does_not_divide_by_zero(self):
        assert fit_lambda(np.zeros(4), np.array([0.1, -0.1, 0.2, 0.0])) == 0.0


class TestDeploymentInterval:
    """predict.py clamps, applies a crossing guard, then re-centres."""

    def test_shrinkage_preserves_width(self):
        q10, q50, q90 = (np.array([0.55]), np.array([0.65]), np.array([0.75]))
        lo, _mid, hi = apply_deployment_interval(q10, q50, q90, 0.3)
        assert (hi - lo)[0] == pytest.approx(0.20)

    def test_shrinkage_pulls_centre_toward_half(self):
        lo, mid, hi = apply_deployment_interval(
            np.array([0.55]), np.array([0.65]), np.array([0.75]), 0.3)
        assert mid[0] == pytest.approx(0.5 + 0.3 * (0.65 - 0.5))
        assert lo[0] < mid[0] < hi[0]

    def test_lambda_one_is_a_no_op_recentring(self):
        args = (np.array([0.55]), np.array([0.65]), np.array([0.75]))
        lo, mid, hi = apply_deployment_interval(*args, 1.0)
        assert (lo[0], mid[0], hi[0]) == pytest.approx((0.55, 0.65, 0.75))

    def test_crossing_guard_repairs_inverted_quantiles(self):
        # q10 above q90 must not produce an inverted (negative-width) band.
        lo, mid, hi = apply_deployment_interval(
            np.array([0.80]), np.array([0.60]), np.array([0.40]), 1.0)
        assert lo[0] == pytest.approx(0.40)
        assert hi[0] == pytest.approx(0.80)
        assert lo[0] <= mid[0] <= hi[0]

    def test_clamp_is_applied(self):
        lo, _mid, hi = apply_deployment_interval(
            np.array([0.01]), np.array([0.5]), np.array([0.99]), 1.0)
        assert lo[0] == pytest.approx(0.1)
        assert hi[0] == pytest.approx(0.9)


class TestIntervalCoverage:
    def test_counts_endpoints_as_inside(self):
        lo, hi = np.array([0.2, 0.2]), np.array([0.8, 0.8])
        assert interval_coverage(lo, hi, np.array([0.2, 0.8])) == 1.0

    def test_counts_outside(self):
        lo, hi = np.array([0.2, 0.2, 0.2]), np.array([0.8, 0.8, 0.8])
        assert interval_coverage(lo, hi, np.array([0.1, 0.5, 0.9])) == pytest.approx(1 / 3)

    def test_empty_is_nan_not_zero(self):
        # An empty fold must not read as "0% coverage" in a gate.
        assert np.isnan(interval_coverage(np.array([]), np.array([]), np.array([])))


class TestNestedLambda:
    @staticmethod
    def _by_city(rng=None):
        rng = rng or np.random.default_rng(7)
        out = {}
        for i, city in enumerate(["a", "b", "c", "d"]):
            dp = rng.normal(0, 0.1, 40)
            # City "d" has an inverted relationship, so a lambda fitted with
            # it included differs materially from one fitted without it.
            sign = -1.0 if city == "d" else 1.0
            out[city] = {"dp": dp, "dy": sign * 0.4 * dp}
        return out

    def test_scored_city_never_informs_its_own_lambda(self):
        """The leak the harness exists to close.

        Scoring city X must use a lambda fitted only on the others. Replacing
        X's own rows with garbage must therefore not change the lambda that
        scores the OTHER cities — but it does change X's own.
        """
        base = self._by_city()
        result = nested_lambda_scores(base)
        lam_by_city = {r["city"]: r["lambda_fitted_on_other_cities"]
                       for r in result["per_city"]}

        tampered = {c: dict(v) for c, v in base.items()}
        tampered["a"] = {"dp": base["a"]["dp"], "dy": -3.0 * base["a"]["dy"]}
        after = nested_lambda_scores(tampered)
        lam_after = {r["city"]: r["lambda_fitted_on_other_cities"]
                     for r in after["per_city"]}

        # a's own lambda is fitted on b, c, d — untouched.
        assert lam_after["a"] == lam_by_city["a"]
        # Every other city's lambda saw a's rows, so it moved.
        assert any(lam_after[c] != lam_by_city[c] for c in ("b", "c", "d"))

    def test_pooled_row_count_matches_the_inputs(self):
        base = self._by_city()
        result = nested_lambda_scores(base)
        assert sum(r["n_rows"] for r in result["per_city"]) == 160

    def test_needs_at_least_two_cities(self):
        with pytest.raises(ValueError):
            nested_lambda_scores({"only": {"dp": np.array([0.1]),
                                           "dy": np.array([0.05])}})

    def test_reports_improvement_against_the_5050_baseline(self):
        rng = np.random.default_rng(3)
        dp = rng.normal(0, 0.1, 60)
        by_city = {c: {"dp": dp, "dy": 0.5 * dp} for c in ("a", "b", "c")}
        result = nested_lambda_scores(by_city)
        # A consistent, learnable relationship must beat writing 0.5.
        assert result["pooled_impr_pct"] > 0


class TestBootstrap:
    def test_ci_brackets_the_point_estimate(self):
        rng = np.random.default_rng(11)
        stations = [f"s{i}" for i in range(12)]
        station_city = {s: ("a" if i % 2 else "b")
                        for i, s in enumerate(stations)}
        dp = {s: rng.normal(0, 0.1, 10) for s in stations}
        dy = {s: 0.4 * dp[s] for s in stations}
        out = bootstrap_nested_delta(stations, station_city, dp, dy,
                                     n_boot=100, seed=1)
        lo, hi = out["delta_ci95"]
        assert lo <= out["delta_point"] <= hi
        assert out["n_stations"] == 12

    def test_single_city_sample_is_skipped_not_crashed(self):
        # A bootstrap draw that happens to contain one city cannot fit an
        # out-of-city lambda; it must be dropped, not raise.
        stations = ["s0", "s1"]
        station_city = {"s0": "a", "s1": "a"}
        dp = {s: np.array([0.1, 0.2]) for s in stations}
        dy = {s: np.array([0.05, 0.1]) for s in stations}
        out = bootstrap_nested_delta(stations, station_city, dp, dy,
                                     n_boot=5, seed=1)
        assert out["delta_point"] is None
        assert out["n_boot_effective"] == 0


class TestOrientation:
    def test_flags_targets_below_the_training_support(self):
        out = summarize_orientation({"134": -0.997, "107": 0.611}, 0.001)
        assert out["n_outside_training_support"] == 1
        assert out["sensors_outside"] == ["134"]

    def test_all_inside_is_clean(self):
        out = summarize_orientation({"107": 0.611, "1074": 0.798}, 0.001)
        assert out["n_outside_training_support"] == 0
        assert out["sensors_outside"] == []

    def test_every_sensor_is_reported_not_only_the_flagged_ones(self):
        out = summarize_orientation({"a": -0.5, "b": 0.7, "c": 0.2}, 0.001)
        assert {s["sensor"] for s in out["sensors"]} == {"a", "b", "c"}
