"""Unit tests for Agent 1 (train_agent1.py + build_agent1_flows.py) — the
Holiday Baseline Adjustment forecast model. No lightgbm/real data required:
train_agent1.py only imports lightgbm inside main(), so the pure feature/
factor/validation functions are testable in isolation with a trivial fake
model standing in for LGBMRegressor."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from build_agent1_flows import (HOLIDAY_MAPPING_2027_TO_2025,
                                apply_holiday_factors, dow_correction_ratios)
from train_agent1 import (build_features, compute_holiday_factors,
                          features_for_datetime, impute_column,
                          leave_weeks_out_mae, seasonal_naive_mae)


class ConstantMeanModel:
    """Stand-in for LGBMRegressor: predicts the training mean, ignoring X —
    enough to exercise the CV/factor plumbing without a real ML dependency."""
    def fit(self, X, y):
        self.value = float(np.mean(y))
        return self

    def predict(self, X):
        return np.full(len(X), self.value)


class TestBuildFeatures:
    def test_shape_and_columns(self):
        df = build_features(96)
        assert len(df) == 96
        assert list(df.columns) == [
            "hour_sin", "hour_cos", "dow_sin", "dow_cos",
            "month_sin", "month_cos", "is_weekend",
        ]

    def test_no_holiday_column(self):
        # The whole point of HBA: the baseline model must never see a
        # holiday flag, or Julafton's day-of-week/holiday effects confound.
        df = build_features(96)
        assert not any("holiday" in c for c in df.columns)

    def test_epoch_2025_starts_wednesday(self):
        df = build_features(1, epoch=pd.Timestamp("2025-01-01"))
        # Wednesday = dow 2 -> dow_sin = sin(2*pi*2/7)
        assert df["dow_sin"].iloc[0] == pytest.approx(np.sin(2 * np.pi * 2 / 7))

    def test_is_weekend_flags_saturday(self):
        df = build_features(4 * 24, epoch=pd.Timestamp("2025-01-04T00:00:00"))  # a Saturday
        assert df["is_weekend"].iloc[0] == 1.0

    def test_features_for_datetime_matches_build_features_row(self):
        dt = pd.Timestamp("2025-03-17T14:30:00")   # arbitrary weekday afternoon
        row = features_for_datetime(dt)
        n = int((dt - pd.Timestamp("2025-01-01")) / pd.Timedelta(minutes=15))
        bulk = build_features(n + 1).iloc[[n]].reset_index(drop=True)
        pd.testing.assert_frame_equal(row.reset_index(drop=True), bulk, check_dtype=False)


class TestImputeColumn:
    def test_forward_fill_gap(self):
        y = np.array([1.0, np.nan, np.nan, 4.0])
        out = impute_column(y)
        np.testing.assert_allclose(out, [1.0, 1.0, 1.0, 4.0])

    def test_leading_gap_uses_mean_of_rest(self):
        y = np.array([np.nan, np.nan, 2.0, 4.0])
        out = impute_column(y)
        # ffill can't fill a leading NaN; both leading slots get filled with
        # the mean of what ffill produced (2.0 twice, then 2.0, 4.0 -> mean 2.5)
        assert not np.isnan(out).any()
        assert out[0] == pytest.approx(out[1])

    def test_all_nan_column_stays_nan(self):
        # Documents a real edge case: ffill+mean can't invent data for a
        # sensor with zero observations — mean() of an all-NaN series is
        # itself NaN, so fillna is a no-op. Not a bug to paper over here;
        # a genuinely dataless sensor should surface as NaN, not a guess.
        y = np.array([np.nan] * 5)
        out = impute_column(y)
        assert np.isnan(out).all()


class TestSeasonalNaiveMae:
    def test_known_constant_offset(self):
        n = 672 * 2   # two seasonal lags' worth
        y = np.zeros(n)
        y[672:] = 5.0   # everything one week later is exactly +5
        mask = np.ones(n, dtype=bool)
        holiday = np.zeros(n, dtype=bool)
        assert seasonal_naive_mae(y, mask, holiday) == pytest.approx(5.0)

    def test_holiday_slots_excluded(self):
        n = 672 * 2
        y = np.zeros(n)
        y[672:] = 5.0
        mask = np.ones(n, dtype=bool)
        holiday = np.zeros(n, dtype=bool)
        holiday[672:] = True   # exclude every comparison -> no data left
        assert np.isnan(seasonal_naive_mae(y, mask, holiday))


class TestLeaveWeeksOutMae:
    def test_recovers_known_gap_between_weeks(self):
        n = 2 * 7 * 96
        y = np.concatenate([np.zeros(7 * 96), np.full(7 * 96, 100.0)])
        X = pd.DataFrame({"x": np.zeros(n)})
        mask = np.ones(n, dtype=bool)
        holiday = np.zeros(n, dtype=bool)
        mae = leave_weeks_out_mae(ConstantMeanModel, X, y, mask, holiday,
                                  held_out_weeks=(0,))
        # Trained on week 1 (all 100s) -> predicts 100 -> held-out week 0
        # (all 0s) is off by exactly 100 everywhere.
        assert mae == pytest.approx(100.0)


class TestComputeHolidayFactors:
    def test_factor_is_actual_over_predicted(self):
        ts = pd.date_range("2025-06-19", periods=3 * 96, freq="15min")
        sensor_ids = ["S1"]
        X_raw = np.full((len(ts), 1), 10.0)
        holiday_start = 96   # 2025-06-20 = Midsommardagen, in HOLIDAY_DATES_2025
        X_raw[holiday_start:holiday_start + 96, 0] = 20.0   # double on the holiday
        orig_mask = np.ones_like(X_raw, dtype=bool)
        feat_df = pd.DataFrame({"x": np.zeros(len(ts))})
        models = {"S1": ConstantMeanModel()}
        models["S1"].value = 10.0   # fixed baseline prediction

        factors = compute_holiday_factors(models, feat_df, X_raw, orig_mask,
                                          sensor_ids, ts)
        assert "2025-06-20" in factors
        np.testing.assert_allclose(factors["2025-06-20"]["S1"], 2.0)
        np.testing.assert_allclose(factors["2025-06-20"]["avg"], 2.0)

    def test_missing_sensor_slot_falls_back_to_cross_sensor_average(self):
        ts = pd.date_range("2025-06-19", periods=3 * 96, freq="15min")
        sensor_ids = ["S1", "S2"]
        X_raw = np.full((len(ts), 2), 10.0)
        h = 96
        X_raw[h:h + 96, 0] = 20.0   # S1: factor 2.0 all day
        X_raw[h:h + 96, 1] = 30.0   # S2: factor 3.0 all day
        orig_mask = np.ones_like(X_raw, dtype=bool)
        orig_mask[h, 1] = False   # S2's first holiday slot is missing data
        feat_df = pd.DataFrame({"x": np.zeros(len(ts))})
        models = {"S1": ConstantMeanModel(), "S2": ConstantMeanModel()}
        models["S1"].value = 10.0
        models["S2"].value = 10.0

        factors = compute_holiday_factors(models, feat_df, X_raw, orig_mask,
                                          sensor_ids, ts)
        day = factors["2025-06-20"]
        # avg at slot 0 only sees S1 (2.0), since S2 is missing there
        assert day["avg"][0] == pytest.approx(2.0)
        # S2's missing slot 0 is back-filled with that average, not left NaN
        assert day["S2"][0] == pytest.approx(2.0)
        # S2's other slots keep their real factor
        assert day["S2"][1] == pytest.approx(3.0)


class TestApplyHolidayFactors:
    def test_multiplies_only_on_mapped_holiday_slots(self):
        ts = pd.date_range("2027-01-01", periods=96, freq="15min")   # Nyårsdagen 2027
        baseline = np.full(96, 10.0)
        factors = {"2025-01-01": {"S1": [2.0] * 96, "avg": [2.0] * 96}}
        out = apply_holiday_factors(ts, baseline, factors, "S1")
        np.testing.assert_allclose(out, 20.0)

    def test_non_holiday_day_is_unchanged(self):
        ts = pd.date_range("2027-03-01", periods=96, freq="15min")   # ordinary Monday
        baseline = np.full(96, 10.0)
        factors = {"2025-01-01": {"S1": [2.0] * 96, "avg": [2.0] * 96}}
        out = apply_holiday_factors(ts, baseline, factors, "S1")
        np.testing.assert_allclose(out, baseline)

    def test_falls_back_to_avg_when_sensor_missing(self):
        ts = pd.date_range("2027-01-01", periods=96, freq="15min")
        baseline = np.full(96, 10.0)
        factors = {"2025-01-01": {"avg": [1.5] * 96}}   # no per-sensor entry
        out = apply_holiday_factors(ts, baseline, factors, "S1")
        np.testing.assert_allclose(out, 15.0)

    def test_does_not_mutate_input_baseline(self):
        ts = pd.date_range("2027-01-01", periods=96, freq="15min")
        baseline = np.full(96, 10.0)
        factors = {"2025-01-01": {"S1": [2.0] * 96, "avg": [2.0] * 96}}
        apply_holiday_factors(ts, baseline, factors, "S1")
        np.testing.assert_allclose(baseline, 10.0)

    def test_dow_correction_is_applied_on_top_of_the_stored_factor(self):
        ts = pd.date_range("2027-01-01", periods=96, freq="15min")
        baseline = np.full(96, 10.0)
        factors = {"2025-01-01": {"S1": [2.0] * 96, "avg": [2.0] * 96}}
        dow_corrections = {"2025-01-01": {"S1": [0.25] * 96}}
        out = apply_holiday_factors(ts, baseline, factors, "S1", dow_corrections)
        np.testing.assert_allclose(out, 10.0 * 2.0 * 0.25)

    def test_no_correction_entry_for_date_leaves_factor_unchanged(self):
        """dow_corrections only has entries for MISMATCHED mappings —
        a date absent from it (dow already matches) must behave exactly
        like passing no dow_corrections at all."""
        ts = pd.date_range("2027-01-01", periods=96, freq="15min")
        baseline = np.full(96, 10.0)
        factors = {"2025-01-01": {"S1": [2.0] * 96, "avg": [2.0] * 96}}
        out = apply_holiday_factors(ts, baseline, factors, "S1", dow_corrections={})
        np.testing.assert_allclose(out, 20.0)


class FakeAgent1DowAware:
    """predict_synthetic returns a value depending only on day-of-week —
    enough to test dow_correction_ratios() without a real trained model."""
    def __init__(self, value_by_dow: dict[int, float]):
        self.value_by_dow = value_by_dow

    @property
    def sensor_ids(self):
        return ["S1"]

    def predict_synthetic(self, hour, dayofweek, month):
        return {"S1": self.value_by_dow[dayofweek]}


class TestDowCorrectionRatios:
    """Found 2026-07-09 testing real_day_shape() against all 30 2025/2027
    holidays: 2027-05-01 (Saturday) forecast from 2025-05-01 (Thursday)'s
    factor peaked at 01:00, near-zero at 06-08 — the factor was calibrated
    relative to a Thursday's baseline shape and misapplied to a Saturday's."""

    def test_matching_dow_needs_no_correction(self):
        agent = FakeAgent1DowAware({3: 10.0})
        # 2025-01-02 and 2027-01-07 are both Thursdays.
        ratios = dow_correction_ratios(agent, "2025-01-02", "2027-01-07")
        assert ratios == {}

    def test_mismatched_dow_ratio_is_source_over_target(self):
        # 2025-05-01 is a Thursday (dow=3), 2027-05-01 is a Saturday (dow=5).
        agent = FakeAgent1DowAware({3: 10.0, 5: 40.0})
        ratios = dow_correction_ratios(agent, "2025-05-01", "2027-05-01")
        assert ratios["S1"] == pytest.approx([0.25] * 96)

    def test_near_zero_target_baseline_avoids_blowup(self):
        agent = FakeAgent1DowAware({3: 10.0, 5: 0.5})   # target pred < 1.0
        ratios = dow_correction_ratios(agent, "2025-05-01", "2027-05-01")
        assert ratios["S1"] == pytest.approx([1.0] * 96)


class TestHolidayMapping:
    def test_all_dates_parse(self):
        for d27, d25 in HOLIDAY_MAPPING_2027_TO_2025.items():
            pd.Timestamp(d27)
            pd.Timestamp(d25)

    def test_christmas_eve_maps_to_itself(self):
        assert HOLIDAY_MAPPING_2027_TO_2025["2027-12-24"] == "2025-12-24"

    def test_easter_cycle_maps_to_2025_easter(self):
        # 2027 Good Friday -> 2025 Good Friday, not the same calendar date.
        assert HOLIDAY_MAPPING_2027_TO_2025["2027-03-26"] == "2025-04-18"

    def test_every_2025_target_is_a_real_holiday_date(self):
        from train_agent1 import HOLIDAY_DATES_2025
        for d25 in HOLIDAY_MAPPING_2027_TO_2025.values():
            assert d25 in HOLIDAY_DATES_2025
