"""Regression cases reproduced during the 2026-09-08 research review."""
import numpy as np
import pandas as pd
import pytest

from traffic_sim.simulation.sensor_fit import assess_passage_accuracy
from traffic_sim.confidence import report
from train_agent1 import leave_weeks_out_mae


def test_hourly_error_cannot_hide_behind_balanced_daily_volume():
    result = assess_passage_accuracy({'directions': [{
        'target_mean': [100.0] * 96,
        'simulated_mean_raw': ([140.0] * 4 + [60.0] * 4) * 12,
    }]}, n_intervals=96)
    assert result['geh_pct'] == 100.0  # quarter diagnostic remains available
    assert result['ok'] is False
    assert result['hourly_geh_pct'] == 0.0
    assert result['hourly_geh_cells'] == 24


@pytest.mark.parametrize('values', [[100.0], [100.0, float('nan'), 100.0, 100.0],
                                   [100.0, -1.0, 100.0, 100.0]])
def test_incomplete_or_invalid_passages_do_not_pass(values):
    result = assess_passage_accuracy({'directions': [{
        'target_mean': [100.0] * 4, 'simulated_mean_raw': values,
    }]}, n_intervals=4)
    assert result['ok'] is False


def test_forecast_fold_never_trains_on_future_or_imputed_targets():
    n = 3 * 7 * 96
    X = pd.DataFrame({'time': np.arange(n)})
    y = np.ones(n)
    mask = np.ones(n, dtype=bool)
    mask[10] = False
    y[10] = 999.0  # a prefilled missing target must still be excluded
    trained = []

    class Model:
        def fit(self, features, targets):
            trained.append(features.index.to_numpy())
            assert 999.0 not in targets
        def predict(self, features):
            return np.ones(len(features))

    assert leave_weeks_out_mae(Model, X, y, mask, np.zeros(n, dtype=bool),
                              held_out_weeks=(1,)) == 0.0
    assert np.max(trained[0]) < 7 * 96
    assert 10 not in trained[0]


def test_spatial_loso_with_missing_identity_is_not_current():
    result = report._held_out_section({'stations': {'s': {'edges': {
        'e': {'ratio': 1.0}}}}})
    assert result['status'] == 'missing'
    assert 'median_ratio' not in result


@pytest.mark.parametrize('n', [None, '4', 4.0, True, -1, 0, 3])
def test_invalid_interval_count_fails_closed(n):
    assert assess_passage_accuracy({'directions': [{
        'target_mean': [100.0] * 4, 'simulated_mean_raw': [100.0] * 4,
    }]}, n_intervals=n)['ok'] is False


def test_forecast_methods_use_identical_observed_pairs():
    from train_agent1 import rolling_origin_scores
    week = 7 * 96
    y = np.concatenate([np.ones(week), np.full(week, 3.0)])
    observed = np.ones(len(y), dtype=bool)
    observed[0] = False
    y[week] = 10000.0  # excluded from both scores: missing seasonal reference

    class Model:
        def fit(self, features, targets):
            pass
        def predict(self, features):
            return np.ones(len(features))

    scores = rolling_origin_scores(Model, pd.DataFrame({'t': np.arange(len(y))}),
                                   y, observed, np.zeros(len(y), dtype=bool), (1,))
    assert scores['evaluation_points'] == week - 1
    assert scores['model_mae'] == scores['naive_mae'] == 2.0
