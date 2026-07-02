"""
Integration smoke tests — verify the three build scripts produced sensible output.

These tests are SKIPPED if the pipeline has not been run yet.
They check real data shapes, alignments, and sanity ranges — things unit tests cannot catch.

Run order:
  python3 build_data.py
  python3 build_features.py
  python3 build_dataset.py
  pytest tests/test_pipeline_smoke.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from build_dataset import WINDOW_H, WINDOW_K
from build_data import N_QUARTERS

FEATURES_DIR = Path("web/data/features")

# ── Skip markers ───────────────────────────────────────────────────────────────

_need_flow_matrix = pytest.mark.skipif(
    not (FEATURES_DIR / "flow_matrix.npz").exists(),
    reason="flow_matrix.npz missing — run build_features.py first",
)
_need_adjacency = pytest.mark.skipif(
    not (FEATURES_DIR / "adjacency.npz").exists(),
    reason="adjacency.npz missing — run build_features.py first",
)
_need_datasets = pytest.mark.skipif(
    not (FEATURES_DIR / "dataset_train.npz").exists(),
    reason="dataset_train.npz missing — run build_dataset.py first",
)
_need_scaler = pytest.mark.skipif(
    not (FEATURES_DIR / "scaler.json").exists(),
    reason="scaler.json missing — run build_dataset.py first",
)
_need_imputed = pytest.mark.skipif(
    not (FEATURES_DIR / "imputed.npz").exists(),
    reason="imputed.npz missing — run build_dataset.py first",
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load(name: str) -> np.lib.npyio.NpzFile:
    return np.load(FEATURES_DIR / name, allow_pickle=True)


# ── Flow matrix ────────────────────────────────────────────────────────────────

@_need_flow_matrix
class TestFlowMatrix:
    def test_shape_is_n_quarters_by_n_sensors(self):
        X = _load("flow_matrix.npz")["X"]
        assert X.shape[0] == N_QUARTERS, (
            f"Expected {N_QUARTERS} time steps, got {X.shape[0]}. Re-run build_features.py."
        )
        assert X.shape[1] >= 1

    def test_mask_matches_nan_pattern(self):
        raw  = _load("flow_matrix.npz")
        X, mask = raw["X"], raw["mask"]
        assert np.all(mask == ~np.isnan(X)), (
            "mask and X are inconsistent.  Re-run build_features.py."
        )

    def test_missing_rate_below_5_percent(self):
        X    = _load("flow_matrix.npz")["X"]
        pct  = np.isnan(X).mean() * 100
        assert pct < 5.0, f"Overall missing rate {pct:.1f}% is suspiciously high."

    def test_no_column_is_entirely_missing(self):
        X    = _load("flow_matrix.npz")["X"]
        for i in range(X.shape[1]):
            assert not np.isnan(X[:, i]).all(), f"Column {i} is entirely NaN."

    def test_counts_are_non_negative(self):
        X = _load("flow_matrix.npz")["X"]
        observed = X[~np.isnan(X)]
        assert observed.min() >= 0, f"Negative count found: {observed.min()}"

    def test_sensor_ids_sorted_alphabetically(self):
        ids = _load("flow_matrix.npz")["sensor_ids"].tolist()
        assert ids == sorted(ids), f"sensor_ids not sorted: {ids}"


# ── Adjacency ──────────────────────────────────────────────────────────────────

@_need_adjacency
@_need_flow_matrix
class TestAdjacency:
    def test_sensor_ids_match_flow_matrix(self):
        flow_ids = _load("flow_matrix.npz")["sensor_ids"].tolist()
        adj_ids  = _load("adjacency.npz")["sensor_ids"].tolist()
        assert flow_ids == adj_ids, (
            f"sensor_ids mismatch:\n  flow_matrix: {flow_ids}\n  adjacency:   {adj_ids}"
        )

    def test_W_is_square_and_matches_n_sensors(self):
        raw = _load("adjacency.npz")
        W   = raw["W"]
        n   = len(raw["sensor_ids"])
        assert W.shape == (n, n), f"W shape {W.shape} != ({n}, {n})"

    def test_diagonal_is_zero(self):
        W = _load("adjacency.npz")["W"]
        assert np.all(np.diag(W) == 0.0), "Adjacency diagonal must be 0 (no self-loops)."

    def test_symmetric(self):
        W = _load("adjacency.npz")["W"]
        np.testing.assert_array_almost_equal(W, W.T, decimal=6)

    def test_values_in_range(self):
        W = _load("adjacency.npz")["W"]
        assert W.min() >= 0.0
        assert W.max() <= 1.0

    def test_at_least_one_nonzero_off_diagonal(self):
        W = _load("adjacency.npz")["W"]
        n = W.shape[0]
        mask = ~np.eye(n, dtype=bool)
        assert W[mask].max() > 0.0, (
            "All off-diagonal weights are 0 — sensors may be too far apart or "
            "coordinates are wrong."
        )


# ── Imputed matrix ─────────────────────────────────────────────────────────────

@_need_imputed
@_need_flow_matrix
class TestImputed:
    def test_no_nan_after_imputation(self):
        X_imp = _load("imputed.npz")["X"]
        assert not np.isnan(X_imp).any(), "imputed.npz still has NaN values."

    def test_shape_matches_flow_matrix(self):
        X_raw = _load("flow_matrix.npz")["X"]
        X_imp = _load("imputed.npz")["X"]
        assert X_raw.shape == X_imp.shape

    def test_observed_values_unchanged(self):
        raw   = _load("flow_matrix.npz")
        imp   = _load("imputed.npz")
        X_raw, X_imp, mask = raw["X"], imp["X"], raw["mask"]
        np.testing.assert_array_almost_equal(
            X_imp[mask], X_raw[mask], decimal=3,
            err_msg="Imputation changed observed values.",
        )

    def test_imputed_values_are_positive(self):
        raw      = _load("flow_matrix.npz")
        imp      = _load("imputed.npz")
        mask     = raw["mask"]
        imputed  = imp["X"][~mask]
        assert imputed.min() >= 0.0, f"Imputed negative value: {imputed.min()}"


# ── Scaler ─────────────────────────────────────────────────────────────────────

@_need_scaler
@_need_flow_matrix
class TestScaler:
    def _scaler(self) -> dict:
        with open(FEATURES_DIR / "scaler.json") as f:
            return json.load(f)

    def test_sensor_ids_match_flow_matrix(self):
        flow_ids   = _load("flow_matrix.npz")["sensor_ids"].tolist()
        scaler_ids = self._scaler()["sensor_ids"]
        assert scaler_ids == flow_ids

    def test_mean_is_plausible(self):
        for sid, mean in zip(self._scaler()["sensor_ids"], self._scaler()["mean"]):
            assert 1.0 < mean < 2_000.0, (
                f"Sensor {sid} training mean = {mean:.1f} veh/15min is implausible."
            )

    def test_std_is_positive(self):
        for sid, std in zip(self._scaler()["sensor_ids"], self._scaler()["std"]):
            assert std > 0.0, f"Sensor {sid} std = {std} — must be positive."

    def test_n_params_matches_n_sensors(self):
        s = self._scaler()
        n = len(s["sensor_ids"])
        assert len(s["mean"]) == n
        assert len(s["std"])  == n


# ── Training / val / test datasets ────────────────────────────────────────────

@_need_datasets
@_need_flow_matrix
@_need_adjacency
class TestDatasets:
    def _ds(self, name: str) -> np.lib.npyio.NpzFile:
        return _load(f"dataset_{name}.npz")

    def test_sensor_ids_align_across_all_files(self):
        """The column order in X/y must be identical across all files."""
        ref = _load("flow_matrix.npz")["sensor_ids"].tolist()
        for name in ("train", "val", "test"):
            ids = self._ds(name)["sensor_ids"].tolist()
            assert ids == ref, (
                f"dataset_{name}.npz sensor_ids {ids} != flow_matrix {ref}. "
                "If column order diverges, GNN adjacency rows/columns will be wrong."
            )
        adj_ids = _load("adjacency.npz")["sensor_ids"].tolist()
        assert adj_ids == ref, (
            f"adjacency.npz sensor_ids {adj_ids} != flow_matrix {ref}."
        )

    def test_window_shapes(self):
        for name in ("train", "val", "test"):
            d = self._ds(name)
            n = d["X"].shape[0]
            N = d["X"].shape[2]
            assert d["X"].shape     == (n, WINDOW_K, N), f"{name} X shape wrong"
            assert d["y"].shape     == (n, WINDOW_H, N), f"{name} y shape wrong"
            assert d["y_mask"].shape == (n, WINDOW_H, N), f"{name} y_mask shape wrong"

    def test_no_nan_in_X(self):
        for name in ("train", "val", "test"):
            d = self._ds(name)
            assert not np.isnan(d["X"]).any(), f"dataset_{name}.npz X has NaN."

    def test_no_nan_in_y(self):
        for name in ("train", "val", "test"):
            d = self._ds(name)
            assert not np.isnan(d["y"]).any(), f"dataset_{name}.npz y has NaN."

    def test_y_mask_is_bool(self):
        d = self._ds("train")
        assert d["y_mask"].dtype == bool

    def test_qi_target_start_is_consecutive(self):
        for name in ("train", "val", "test"):
            qi = self._ds(name)["qi_target_start"]
            diffs = np.diff(qi)
            assert np.all(diffs == 1), (
                f"dataset_{name}.npz qi_target_start is not consecutive — "
                "windowing produced gaps."
            )

    def test_train_is_largest_split(self):
        n_train = self._ds("train")["X"].shape[0]
        n_val   = self._ds("val")["X"].shape[0]
        n_test  = self._ds("test")["X"].shape[0]
        assert n_train > n_val,  f"train ({n_train}) ≤ val ({n_val})"
        assert n_train > n_test, f"train ({n_train}) ≤ test ({n_test})"

    def test_splits_do_not_overlap(self):
        tr_qi = self._ds("train")["qi_target_start"]
        va_qi = self._ds("val")["qi_target_start"]
        te_qi = self._ds("test")["qi_target_start"]
        assert tr_qi[-1] < va_qi[0],  "train and val overlap"
        assert va_qi[-1] < te_qi[0],  "val and test overlap"

    def test_x_values_are_normalised(self):
        d    = self._ds("train")
        X    = d["X"]
        mean = X.mean()
        std  = X.std()
        assert abs(mean) < 0.5, f"Training X mean ≈ {mean:.3f} — expected ≈ 0 after normalisation."
        assert 0.5 < std < 2.0, f"Training X std ≈ {std:.3f} — expected ≈ 1 after normalisation."


# ── Baseline ──────────────────────────────────────────────────────────────────

@_need_datasets
class TestBaseline:
    def _baseline(self) -> dict:
        with open(FEATURES_DIR / "baseline.json") as f:
            return json.load(f)

    def test_required_keys(self):
        b = self._baseline()
        assert "seasonal_naive" in b
        sn = b["seasonal_naive"]
        assert "mean_mae"              in sn
        assert "per_sensor_mae"        in sn
        assert "pct_real_observations" in sn

    def test_mean_mae_is_plausible(self):
        mean_mae = self._baseline()["seasonal_naive"]["mean_mae"]
        assert 0 < mean_mae < 500, (
            f"Baseline MAE = {mean_mae:.1f} is implausible. "
            "Expected 5–100 vehicles / 15 min for urban sensors."
        )

    def test_pct_real_observations_is_high(self):
        pct = self._baseline()["seasonal_naive"]["pct_real_observations"]
        assert pct > 90.0, (
            f"Only {pct:.1f}% of eval slots are real observations. "
            "If this is low, check sensor outages in Nov–Dec."
        )
