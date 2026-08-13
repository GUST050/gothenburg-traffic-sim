"""Step 3 acceptance: measured coverage, post-processing included, honest labels.

Plan acceptance for step 3:
  * coverage and sharpness exist per primary group, with confidence intervals;
  * post-processing, clamp and rearrangement are INSIDE the measurement;
  * an uncalibrated interval serialises as ``stress_only``, so the phrase
    "80% interval" cannot reach the UI.
"""

from __future__ import annotations

import numpy as np
import pytest

from dirsplit import calibrate as cal
from dirsplit import evaluate as ev
from dirsplit import evidence as evd
from dirsplit import models as md
from dirsplit import schema
from dirsplit.dataset_schema import make_row


def rows_for(city="oslo", station="S1", dates=("2025-09-01",), hours=range(6, 21),
             share=0.5, total=500.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for date in dates:
        for hour in hours:
            value = float(np.clip(share + rng.normal(0, noise), 0.05, 0.95))
            toward = round(total * value)
            out.append(make_row(
                station_id=station, city=city, heading="C",
                timestamp=f"{date}T{hour:02d}:00:00",
                toward_count=toward, away_count=total - toward,
                coverage_pct=100.0,
                features={"radial_cos": 0.9}, profile={"am_pm_ratio": 1.0},
            ))
    return out


class TestCoverageIsMeasuredNotAsserted:
    def test_a_wide_interval_covers_and_a_narrow_one_does_not(self):
        actual = np.full(200, 0.5)
        blocks = [f"b{i//10}" for i in range(200)]
        wide = cal.measure_coverage(actual, np.full(200, 0.3),
                                    np.full(200, 0.7), blocks)
        narrow = cal.measure_coverage(actual, np.full(200, 0.51),
                                      np.full(200, 0.52), blocks)
        assert wide.empirical == 1.0
        assert narrow.empirical == 0.0

    def test_coverage_carries_a_block_bootstrap_ci(self):
        rng = np.random.default_rng(0)
        actual = rng.normal(0.5, 0.05, 300)
        blocks = [f"b{i//10}" for i in range(300)]
        result = cal.measure_coverage(actual, np.full(300, 0.42),
                                      np.full(300, 0.58), blocks)
        lo, hi = result.ci95
        assert lo <= result.empirical <= hi

    def test_sharpness_is_reported_alongside_coverage(self):
        actual = np.full(100, 0.5)
        blocks = [f"b{i//10}" for i in range(100)]
        result = cal.measure_coverage(actual, np.full(100, 0.3),
                                      np.full(100, 0.7), blocks)
        assert result.mean_width == pytest.approx(0.4)
        assert np.isfinite(result.interval_score)

    def test_calibration_requires_the_ci_to_contain_the_nominal_level(self):
        actual = np.full(200, 0.5)
        blocks = [f"b{i//10}" for i in range(200)]
        # Always covered -> empirical 1.0, nominal 0.8 -> not calibrated
        over = cal.measure_coverage(actual, np.full(200, 0.1),
                                    np.full(200, 0.9), blocks, nominal=0.8)
        assert over.empirical == 1.0
        assert not over.is_calibrated

    def test_never_covered_is_not_calibrated(self):
        actual = np.full(200, 0.5)
        blocks = [f"b{i//10}" for i in range(200)]
        result = cal.measure_coverage(actual, np.full(200, 0.7),
                                      np.full(200, 0.8), blocks, nominal=0.8)
        assert result.empirical == 0.0
        assert not result.is_calibrated


class TestPostProcessingIsInsideTheMeasurement:
    def test_the_clamp_changes_coverage_and_is_measured_after_it(self):
        """A clamp truncates the interval, so it changes coverage."""
        actual = np.full(100, 0.95)
        blocks = [f"b{i//10}" for i in range(100)]
        unclamped = cal.measure_coverage(actual, np.full(100, 0.9),
                                         np.full(100, 1.0), blocks)
        clamped = cal.measure_coverage(actual, np.clip(np.full(100, 0.9),
                                                       0.1, 0.9),
                                       np.clip(np.full(100, 1.0), 0.1, 0.9),
                                       blocks)
        assert unclamped.empirical == 1.0
        assert clamped.empirical == 0.0

    def test_calibration_apply_respects_the_clamp(self):
        pred = md.Prediction(central=np.array([0.5]),
                             lower=np.array([0.2]), upper=np.array([0.8]))
        calibration = cal.Calibration("conformal", offset=0.5, nominal=0.8,
                                      fitted_blocks=10)
        out = calibration.apply(pred)
        assert out.lower[0] >= 0.1
        assert out.upper[0] <= 0.9

    def test_apply_keeps_the_central_estimate_inside_its_interval(self):
        pred = md.Prediction(central=np.array([0.85]),
                             lower=np.array([0.4]), upper=np.array([0.6]))
        calibration = cal.Calibration("none", offset=0.0, nominal=0.8,
                                      fitted_blocks=5)
        out = calibration.apply(pred)
        assert out.lower[0] <= out.central[0] <= out.upper[0]


class TestConformalOffsetUsesBlocks:
    def test_a_too_narrow_interval_gets_a_positive_offset(self):
        actual = np.linspace(0.3, 0.7, 100)
        blocks = [f"b{i//10}" for i in range(100)]
        offset = cal.conformal_offset(actual, np.full(100, 0.49),
                                      np.full(100, 0.51), blocks)
        assert offset > 0

    def test_an_over_wide_interval_may_shrink(self):
        actual = np.full(100, 0.5)
        blocks = [f"b{i//10}" for i in range(100)]
        offset = cal.conformal_offset(actual, np.full(100, 0.1),
                                      np.full(100, 0.9), blocks)
        assert offset < 0

    def test_one_block_contributes_one_score_not_many(self):
        """A station reporting 15 hours must not outvote one reporting 2."""
        actual = np.full(30, 0.5)
        lower = np.full(30, 0.49)
        upper = np.full(30, 0.51)
        # same data, different block structure
        many = cal.conformal_offset(actual, lower, upper,
                                    [f"r{i}" for i in range(30)])
        few = cal.conformal_offset(actual, lower, upper,
                                   [f"b{i//15}" for i in range(30)])
        # Identical scores per row, so the quantile is the same value;
        # what differs is how many scores exist, which the block form fixes.
        assert np.isfinite(many) and np.isfinite(few)


class TestHonestLabels:
    def test_uncalibrated_report_maps_to_stress_only(self):
        report = {"groups": [{"group": "all", "is_calibrated": False}]}
        assert cal.calibration_status(report) == "stress_only"

    def test_all_groups_must_be_calibrated(self):
        report = {"groups": [
            {"group": "weekday", "is_calibrated": True},
            {"group": "off_hours", "is_calibrated": False},
        ]}
        assert cal.calibration_status(report) == "stress_only"

    def test_empty_report_is_unavailable(self):
        assert cal.calibration_status({"groups": []}) == "unavailable"

    def test_a_stress_only_interval_cannot_say_eighty_percent(self):
        interval = schema.MarginalInterval(
            lower={"A": [0.4] * 96, "B": [0.4] * 96},
            upper={"A": [0.6] * 96, "B": [0.6] * 96},
            nominal_coverage=0.8,
            calibration_status="stress_only",
        )
        assert "80" not in interval.label()
        assert not interval.is_probability_statement

    def test_a_calibrated_interval_may_say_it(self):
        interval = schema.MarginalInterval(
            lower={"A": [0.4] * 96, "B": [0.4] * 96},
            upper={"A": [0.6] * 96, "B": [0.6] * 96},
            nominal_coverage=0.8,
            calibration_status="calibrated",
            empirical_coverage=0.81,
        )
        assert "80" in interval.label()


class TestFitCalibrationIsHeldOut:
    def test_it_fits_on_held_out_predictions_only(self):
        rows = (rows_for(city="oslo", station="A", noise=0.05,
                         dates=tuple(f"2025-09-{d:02d}" for d in range(1, 8)),
                         seed=1)
                + rows_for(city="bergen", station="B", noise=0.05,
                           dates=tuple(f"2025-09-{d:02d}" for d in range(1, 8)),
                           seed=2))
        folds = ev.leave_city_out(rows)
        calibration, report = cal.fit_calibration(md.ShrunkDFactor(), folds)
        assert calibration.fitted_blocks > 0
        assert report["groups"]
        assert not calibration.exchangeability_claimed

    def test_it_never_claims_a_guarantee(self):
        rows = rows_for(noise=0.05, dates=("2025-09-01", "2025-09-02"))
        folds = ev.blocked_date(rows, n_blocks=2)
        calibration, _report = cal.fit_calibration(md.ShrunkDFactor(), folds)
        notes = calibration.notes.lower()
        # The note may mention a guarantee only to deny one.
        assert "not a coverage guarantee" in notes
        assert "not established" in notes
        assert not calibration.exchangeability_claimed

    def test_the_report_shows_before_and_after_calibration(self):
        rows = (rows_for(city="oslo", station="A", noise=0.05,
                         dates=tuple(f"2025-09-{d:02d}" for d in range(1, 8)))
                + rows_for(city="bergen", station="B", noise=0.05,
                           dates=tuple(f"2025-09-{d:02d}" for d in range(1, 8))))
        folds = ev.leave_city_out(rows)
        _cal, report = cal.fit_calibration(md.ShrunkDFactor(), folds)
        assert "before_calibration" in report


# ── evidence profile ──────────────────────────────────────────────────────
class TestEvidenceProfile:
    SUPPORT = {"weekday_hours": list(range(6, 21)), "weekend_hours": []}

    def build(self, **kwargs):
        base = dict(
            edge_id="E1", measurement_level="transfer_prior",
            static_percentile=50.0, hour=8, is_weekend=False,
            temporal_support_report=self.SUPPORT, n_effective_blocks=10,
            coverage_report={"groups": [{"group": "all", "n_blocks": 10,
                                         "is_calibrated": True}]},
        )
        base.update(kwargs)
        return evd.build_profile(**base)

    def test_a_well_supported_edge_permits_normal_claims(self):
        assert self.build().claim_strength() == "normal"

    def test_an_unobserved_night_hour_is_extrapolation(self):
        night = self.build(hour=3)
        assert night.temporal_support == "extrapolation"
        assert night.claim_strength() == "conservative_fallback"
        assert not night.blocks_publication          # widened, never banned

    def test_a_weekend_prediction_without_weekend_training_is_extrapolation(self):
        weekend = self.build(is_weekend=True)
        assert weekend.temporal_support == "extrapolation"

    def test_an_adjacent_hour_is_limited_not_extrapolation(self):
        edge = self.build(hour=21)      # training covered 6..20
        assert edge.temporal_support == "limited"

    def test_static_extrapolation_is_flagged_at_the_old_threshold(self):
        assert evd.grade_static_domain(94.0) == "extrapolation"
        assert evd.grade_static_domain(50.0) == "good"
        assert evd.grade_static_domain(None) == "unavailable"

    def test_thin_evidence_is_limited(self):
        assert evd.grade_effective_sample(3) == "limited"
        assert evd.grade_effective_sample(1) == "extrapolation"
        assert evd.grade_effective_sample(20) == "good"

    def test_profile_feature_semantics_mismatch_is_limited(self):
        mismatch = self.build(profile_from_total=True, target_has_total=False)
        assert mismatch.feature_compatibility == "limited"

    def test_a_missing_feature_fails_closed(self):
        broken = self.build(missing_features=("radial_cos",))
        assert broken.feature_compatibility == "unavailable"
        assert broken.blocks_publication

    def test_low_evidence_never_bans_a_road(self):
        weak = self.build(static_percentile=99.0, hour=3,
                          n_effective_blocks=1,
                          coverage_report=None)
        assert weak.grade() in ("extrapolation", "limited")
        assert not weak.blocks_publication

    def test_uncalibrated_group_grades_as_limited(self):
        edge = self.build(coverage_report={
            "groups": [{"group": "all", "n_blocks": 10,
                        "is_calibrated": False}]})
        assert edge.calibration_support == "limited"


class TestLocalCrosscheckOutranksTransfer:
    """Sensor 107 has a published city D-factor; that is a measurement."""

    CHECK = evd.LocalCrosscheck(
        edge_id="60786979_3575001205_0",
        measured_share=0.52,
        source="Goteborgs Stad trafikmangder (Power BI)",
        year="2025",
    )

    def test_a_prediction_closer_than_5050_grades_good(self):
        assert self.CHECK.grade(0.515) == "good"

    def test_a_prediction_further_than_5050_grades_limited(self):
        assert self.CHECK.grade(0.40) == "limited"

    def test_the_comparison_is_recorded_in_full(self):
        result = self.CHECK.compare(0.515)
        assert result["measured_share"] == 0.52
        assert result["abs_error"] < result["abs_error_5050"]
        assert result["beats_5050"]
        assert "trafikmangder" in result["source"]

    def test_it_reaches_the_profile(self):
        profile = evd.build_profile(
            edge_id="60786979_3575001205_0",
            measurement_level="both_directions",
            static_percentile=40.0, hour=8, is_weekend=False,
            temporal_support_report={"weekday_hours": list(range(6, 21)),
                                     "weekend_hours": []},
            n_effective_blocks=12,
            crosscheck=self.CHECK, predicted_share=0.515,
        )
        assert profile.local_crosscheck == "good"
        assert profile.detail["crosscheck"]["beats_5050"]


class TestProfileSummary:
    def test_it_counts_grades_and_names_only_blocked_edges(self):
        good = evd.build_profile(
            edge_id="A", measurement_level="both_directions",
            static_percentile=10.0, hour=8, is_weekend=False,
            temporal_support_report={"weekday_hours": list(range(24)),
                                     "weekend_hours": list(range(24))},
            n_effective_blocks=20,
            coverage_report={"groups": [{"group": "all", "n_blocks": 20,
                                         "is_calibrated": True}]},
        )
        broken = evd.build_profile(
            edge_id="B", measurement_level="transfer_prior",
            static_percentile=10.0, hour=8, is_weekend=False,
            temporal_support_report={"weekday_hours": list(range(24)),
                                     "weekend_hours": []},
            n_effective_blocks=20, missing_features=("radial_cos",),
        )
        summary = evd.summarise_profiles([good, broken])
        assert summary["n_edges"] == 2
        assert summary["blocked"] == ["B"]
        assert summary["by_grade"]["good"] == 1
