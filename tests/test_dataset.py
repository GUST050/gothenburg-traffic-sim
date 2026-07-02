"""
Unit tests for build_dataset.py pure functions.

No real files needed — all inputs are synthetic.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from build_dataset import (
    SEASONAL_LAG,
    WINDOW_H,
    WINDOW_K,
    denormalize,
    fit_scaler,
    impute,
    make_windows,
    normalize,
    seasonal_naive_mae,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _rng() -> np.random.Generator:
    return np.random.default_rng(42)


def _make_series(T: int, N: int, missing_frac: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (X, mask). X is float32 with NaN for missing slots."""
    rng  = _rng()
    X    = rng.uniform(10, 200, (T, N)).astype(np.float32)
    mask = np.ones((T, N), dtype=bool)
    if missing_frac > 0:
        drop = rng.random((T, N)) < missing_frac
        X[drop]    = np.nan
        mask[drop] = False
    return X, mask


# ── TestImpute ─────────────────────────────────────────────────────────────────

class TestImpute:
    def test_no_nan_after_impute(self):
        X, _ = _make_series(1_000, 3, missing_frac=0.05)
        result = impute(X)
        assert not np.isnan(result).any()

    def test_forward_fill_fills_gap(self):
        X = np.array([[1.0, 2.0], [np.nan, np.nan], [np.nan, np.nan], [4.0, 8.0]],
                     dtype=np.float32)
        result = impute(X)
        # Rows 1 and 2 should be filled with the value from row 0
        assert result[1, 0] == pytest.approx(1.0)
        assert result[2, 0] == pytest.approx(1.0)
        assert result[1, 1] == pytest.approx(2.0)

    def test_observed_values_unchanged(self):
        X, _ = _make_series(500, 2, missing_frac=0.1)
        X_orig = X.copy()
        result = impute(X)
        observed = ~np.isnan(X_orig)
        np.testing.assert_array_almost_equal(result[observed], X_orig[observed])

    def test_single_observation_fills_whole_column(self):
        # A column with one observation at t=0: ffill carries it to every slot.
        T = SEASONAL_LAG + 20
        X2 = np.full((T, 1), np.nan, dtype=np.float32)
        X2[0, 0] = 77.0
        result = impute(X2)
        assert not np.isnan(result).any()
        assert result[:, 0].min() == pytest.approx(77.0)

    def test_shape_preserved(self):
        X, _ = _make_series(200, 4, missing_frac=0.02)
        result = impute(X)
        assert result.shape == X.shape

    def test_dtype_is_float32(self):
        X, _ = _make_series(100, 2, missing_frac=0.01)
        result = impute(X)
        assert result.dtype == np.float32

    def test_all_missing_column_raises(self):
        # A column with no observations at all cannot be filled sensibly.
        X = np.full((100, 2), np.nan, dtype=np.float32)
        X[:, 0] = 5.0  # first column is fine
        with pytest.raises(ValueError, match="Imputation failed"):
            impute(X)

    def test_train_end_limits_fill_to_training_mean(self):
        # Pass 3 must use only training-period statistics, not full-series stats.
        # Arrange: training mean = 10, post-training mean = 200.
        # Slot 0 is NaN so only pass 3 can fill it.
        T, N = 200, 1
        X = np.full((T, N), np.nan, dtype=np.float32)
        X[1:100, 0]  = 10.0   # training period (train_end = 99)
        X[100:, 0]   = 200.0  # val/test period
        # Without train_end: fill ≈ mean of whole series ≈ (10*99 + 200*100)/199 ≈ 105
        result_full  = impute(X.copy())
        # With train_end=99: fill = mean of X[0:100] ≈ 10 (slot 0 was NaN, rest = 10)
        result_train = impute(X.copy(), train_end=99)
        assert result_train[0, 0] == pytest.approx(10.0, abs=0.5)
        assert result_full[0, 0]  != pytest.approx(10.0, abs=20.0)

    def test_train_end_none_falls_back_to_full_mean(self):
        X, _ = _make_series(200, 2, missing_frac=0.02)
        result_none = impute(X.copy())
        result_full = impute(X.copy(), train_end=None)
        np.testing.assert_array_equal(result_none, result_full)


# ── TestScaler ─────────────────────────────────────────────────────────────────

class TestScaler:
    def test_mean_matches_training_mean(self):
        X, _ = _make_series(500, 3)
        mean, _ = fit_scaler(X)
        np.testing.assert_array_almost_equal(mean, X.mean(axis=0), decimal=4)

    def test_std_matches_training_std(self):
        X, _ = _make_series(500, 3)
        _, std = fit_scaler(X)
        np.testing.assert_array_almost_equal(std, X.std(axis=0), decimal=4)

    def test_zero_std_becomes_one(self):
        # A sensor with a constant value has std=0 before the guard.
        # fit_scaler clamps it to 1 so normalization doesn't divide by zero.
        X = np.ones((100, 2), dtype=np.float32) * 5.0
        _, std = fit_scaler(X)
        assert std[0] == pytest.approx(1.0)
        assert std[1] == pytest.approx(1.0)

    def test_normalize_train_has_zero_mean(self):
        X, _ = _make_series(1_000, 3)
        mean, std = fit_scaler(X)
        X_norm = normalize(X, mean, std)
        assert X_norm.mean(axis=0) == pytest.approx(np.zeros(3), abs=1e-4)

    def test_normalize_train_has_unit_std(self):
        X, _ = _make_series(1_000, 3)
        mean, std = fit_scaler(X)
        X_norm = normalize(X, mean, std)
        assert X_norm.std(axis=0) == pytest.approx(np.ones(3), abs=1e-4)

    def test_denormalize_roundtrip(self):
        X, _ = _make_series(500, 4)
        mean, std = fit_scaler(X)
        X_norm     = normalize(X, mean, std)
        X_recovered = denormalize(X_norm, mean, std)
        np.testing.assert_array_almost_equal(X, X_recovered, decimal=4)

    def test_normalize_does_not_modify_input(self):
        X, _ = _make_series(200, 2)
        X_copy = X.copy()
        mean, std = fit_scaler(X)
        normalize(X, mean, std)
        np.testing.assert_array_equal(X, X_copy)


# ── TestWindows ────────────────────────────────────────────────────────────────

class TestWindows:
    def _data(self, T: int = 200, N: int = 3) -> tuple[np.ndarray, np.ndarray]:
        X, mask = _make_series(T, N)
        return X, mask

    def test_input_shape(self):
        T, N = 200, 3
        K, H = WINDOW_K, WINDOW_H
        X, mask = self._data(T, N)
        inp, _, _ = make_windows(X, mask, K, H)
        assert inp.shape == (T - K - H + 1, K, N)

    def test_target_shape(self):
        T, N = 200, 3
        K, H = WINDOW_K, WINDOW_H
        X, mask = self._data(T, N)
        _, tgt, _ = make_windows(X, mask, K, H)
        assert tgt.shape == (T - K - H + 1, H, N)

    def test_target_mask_shape(self):
        T, N = 200, 3
        K, H = WINDOW_K, WINDOW_H
        X, mask = self._data(T, N)
        _, _, tgt_mask = make_windows(X, mask, K, H)
        assert tgt_mask.shape == (T - K - H + 1, H, N)

    def test_inputs_and_targets_are_contiguous(self):
        T, N = 50, 2
        K, H = 3, 2
        X    = np.arange(T * N, dtype=np.float32).reshape(T, N)
        mask = np.ones((T, N), dtype=bool)
        inp, tgt, _ = make_windows(X, mask, K, H)
        # Window 0: input = rows 0,1,2  target = rows 3,4
        np.testing.assert_array_equal(inp[0],  X[0:3])
        np.testing.assert_array_equal(tgt[0],  X[3:5])
        # Window 1: input = rows 1,2,3  target = rows 4,5
        np.testing.assert_array_equal(inp[1],  X[1:4])
        np.testing.assert_array_equal(tgt[1],  X[4:6])

    def test_target_mask_matches_original_mask(self):
        T, N = 50, 2
        K, H = 3, 2
        X, mask = _make_series(T, N, missing_frac=0.1)
        _, _, tgt_mask = make_windows(X, mask, K, H)
        # Window i, step j should reflect mask[i+K+j]
        for i in range(5):
            for j in range(H):
                assert tgt_mask[i, j, 0] == mask[i + K + j, 0]

    def test_custom_K_H(self):
        T, N = 100, 2
        K, H = 6, 3
        X, mask = self._data(T, N)
        inp, tgt, _ = make_windows(X, mask, K, H)
        assert inp.shape == (T - K - H + 1, K, N)
        assert tgt.shape == (T - K - H + 1, H, N)

    def test_single_sensor(self):
        T, N = 100, 1
        K, H = WINDOW_K, WINDOW_H
        X, mask = self._data(T, N)
        inp, tgt, _ = make_windows(X, mask, K, H)
        assert inp.shape[2] == 1
        assert tgt.shape[2] == 1

    def test_raises_when_series_too_short(self):
        # T < K + H  → n = T - K - H + 1 ≤ 0
        K, H = WINDOW_K, WINDOW_H
        T    = K + H - 1  # one step too short
        X, mask = _make_series(T, 2)
        with pytest.raises(ValueError, match="too short"):
            make_windows(X, mask, K, H)

    def test_exactly_one_window_when_T_equals_K_plus_H(self):
        K, H = 3, 2
        T    = K + H  # exactly one window fits
        X, mask = _make_series(T, 2)
        inp, tgt, _ = make_windows(X, mask, K, H)
        assert inp.shape[0] == 1

    def test_qi_target_start_formula(self):
        # For a slice starting at slice_start, window i has its first target at
        # absolute qi = slice_start + i + K.
        K, H         = 3, 2
        slice_start  = 10
        T_full       = 50
        T_slice      = T_full - slice_start
        X_full, mask_full = _make_series(T_full, 2)
        X_sl    = X_full[slice_start:]
        mask_sl = mask_full[slice_start:]
        inp, tgt, _ = make_windows(X_sl, mask_sl, K, H)
        n = inp.shape[0]
        qi_target_start = np.arange(n, dtype=np.int32) + slice_start + K
        # Verify: tgt[i] == X_full[qi_target_start[i] : qi_target_start[i] + H]
        for i in range(min(5, n)):
            qi = int(qi_target_start[i])
            np.testing.assert_array_equal(tgt[i], X_full[qi : qi + H])


# ── TestSeasonalNaive ──────────────────────────────────────────────────────────

class TestSeasonalNaive:
    def _perfect_periodic(self, T: int, N: int) -> tuple[np.ndarray, np.ndarray]:
        """Signal that is perfectly periodic with period SEASONAL_LAG."""
        t    = np.arange(T)[:, None]
        X    = (50 + 20 * np.sin(2 * np.pi * t / SEASONAL_LAG)).astype(np.float32)
        X    = np.broadcast_to(X, (T, N)).copy()
        mask = np.ones((T, N), dtype=bool)
        return X, mask

    def test_perfect_periodic_has_zero_mae(self):
        T = SEASONAL_LAG * 3
        N = 3
        X, mask = self._perfect_periodic(T, N)
        result = seasonal_naive_mae(X, mask, eval_start_qi=SEASONAL_LAG)
        assert result["mean_mae"] == pytest.approx(0.0, abs=1e-4)

    def test_returns_per_sensor_and_mean(self):
        T = SEASONAL_LAG + 200
        N = 3
        X, mask = _make_series(T, N)
        result = seasonal_naive_mae(X, mask, eval_start_qi=SEASONAL_LAG)
        assert "per_sensor_mae" in result
        assert "mean_mae" in result
        assert len(result["per_sensor_mae"]) == N

    def test_mean_equals_average_of_per_sensor(self):
        T = SEASONAL_LAG + 300
        N = 4
        X, mask = _make_series(T, N)
        result = seasonal_naive_mae(X, mask, eval_start_qi=SEASONAL_LAG)
        assert result["mean_mae"] == pytest.approx(
            np.mean(result["per_sensor_mae"]), abs=1e-3
        )

    def test_mae_is_positive(self):
        T = SEASONAL_LAG + 100
        N = 2
        X, mask = _make_series(T, N)
        result = seasonal_naive_mae(X, mask, eval_start_qi=SEASONAL_LAG)
        assert result["mean_mae"] >= 0

    def test_missing_eval_slots_excluded(self):
        T = SEASONAL_LAG + 200
        N = 2
        X, mask = _make_series(T, N)
        # Mark the entire eval period as missing for sensor 1
        mask[SEASONAL_LAG:, 1] = False
        result = seasonal_naive_mae(X, mask, eval_start_qi=SEASONAL_LAG)
        # per_sensor_mae[1] should be NaN since no valid pairs exist for sensor 1
        assert np.isnan(result["per_sensor_mae"][1])

    def test_eval_start_too_early_raises(self):
        T = SEASONAL_LAG + 100
        N = 2
        X, mask = _make_series(T, N)
        with pytest.raises(ValueError, match="SEASONAL_LAG"):
            seasonal_naive_mae(X, mask, eval_start_qi=SEASONAL_LAG - 1)

    def test_constant_offset_reflected_in_mae(self):
        T  = SEASONAL_LAG + 300
        N  = 1
        X, mask = self._perfect_periodic(T, N)
        # Add a constant shift of 10 vehicles starting from eval_start
        X[SEASONAL_LAG:] += 10
        result = seasonal_naive_mae(X, mask, eval_start_qi=SEASONAL_LAG)
        assert result["mean_mae"] == pytest.approx(10.0, abs=0.5)

    def test_returns_pct_real_observations(self):
        T = SEASONAL_LAG + 200
        N = 2
        X, mask = _make_series(T, N)
        result = seasonal_naive_mae(X, mask, eval_start_qi=SEASONAL_LAG)
        assert "pct_real_observations" in result
        pct = result["pct_real_observations"]
        assert 0.0 <= pct <= 100.0

    def test_pct_real_observations_is_100_when_no_missing(self):
        T = SEASONAL_LAG + 100
        N = 2
        X, mask = _make_series(T, N, missing_frac=0.0)
        result = seasonal_naive_mae(X, mask, eval_start_qi=SEASONAL_LAG)
        assert result["pct_real_observations"] == pytest.approx(100.0)
