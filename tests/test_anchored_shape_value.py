"""Does the transferred shape earn its place once the level is known locally?

Gate M asked whether the direction model beats 50/50.  That is not the question
the deployed system poses, because the deployed system already has sensor 107's
published level.  These tests pin the comparison that does match it — flat
anchor versus anchored shape — and the two results that make it interesting:
the combination beats either half, and the raw transferred profile on its own is
WORSE than simply using the right level flat.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dirsplit.benchmark import (Observation, ShrunkDFactor, load_legacy,
                                orient_toward_centre, restrict_to_weekdays,
                                screen_two_way_stations)
from tools.measure_anchored_shape_value import (MIN_HOURS_PER_STATION,
                                                evaluate_station, measure,
                                                weighted_mae)
from traffic_sim.intake.direction_anchor import DEFAULT_CLAMP


def _observations():
    observations = orient_toward_centre(load_legacy())
    observations, _ = screen_two_way_stations(observations)
    return restrict_to_weekdays(observations)


def _station(share_by_hour, weight_by_hour=None, *, hours=None):
    """A synthetic held-out station with a controllable profile."""
    hours = hours if hours is not None else range(len(share_by_hour))
    return [Observation(
        station_id="s", city="testcity", heading="toward", hour=hour,
        is_weekend=0, share=share, weight=(weight_by_hour[i]
                                           if weight_by_hour else 1.0),
        features={"radial_cos": 1.0}) for i, (hour, share)
        in enumerate(zip(hours, share_by_hour))]


class TestWeightedMae:
    def test_a_perfect_prediction_scores_zero(self):
        assert weighted_mae([0.5, 0.6], [0.5, 0.6], [1.0, 1.0]) == 0.0

    def test_flow_weighting_favours_the_busy_slot(self):
        # Same absolute errors, but the big error sits on the quiet slot.
        light = weighted_mae([0.5, 0.5], [0.5, 0.9], [100.0, 1.0])
        heavy = weighted_mae([0.5, 0.5], [0.9, 0.5], [100.0, 1.0])
        assert light < heavy

    def test_zero_total_flow_is_refused(self):
        with pytest.raises(ValueError):
            weighted_mae([0.5], [0.5], [0.0])


class TestStationScoring:
    def test_a_station_with_too_few_hours_is_skipped(self):
        rows = _station([0.5] * (MIN_HOURS_PER_STATION - 1))
        assert evaluate_station(rows, ShrunkDFactor().fit(rows)) is None

    def test_the_flat_candidate_is_exactly_the_anchor(self):
        # A genuinely flat truth must be scored perfectly by the flat candidate.
        rows = _station([0.55] * 24)
        result = evaluate_station(rows, ShrunkDFactor().fit(rows))
        assert result["anchor"] == pytest.approx(0.55)
        assert result["mae_b0_flat_anchor"] == pytest.approx(0.0, abs=1e-12)

    def test_the_shift_reproduces_the_anchor(self):
        # The whole point of the re-levelling: whatever the shape, its
        # flow-weighted mean must land on the station's own anchor.
        rows = _station([0.40 + 0.01 * hour for hour in range(24)])
        result = evaluate_station(rows, ShrunkDFactor().fit(_observations()))
        assert result["anchor_achieved_by_shift"] == pytest.approx(
            result["anchor"], abs=1e-6)

    def test_a_flow_weighted_anchor_is_not_the_unweighted_mean(self):
        shares = [0.9] + [0.4] * 23
        weights = [1.0] + [100.0] * 23
        result = evaluate_station(
            _station(shares, weights), ShrunkDFactor().fit(_observations()))
        # The one busy-free 0.9 hour must barely move the anchor.
        assert result["anchor"] < 0.45

    def test_predictions_stay_inside_the_declared_clamp(self):
        rows = _station([0.12] * 24)
        result = evaluate_station(rows, ShrunkDFactor().fit(_observations()))
        low, high = DEFAULT_CLAMP
        assert low <= result["anchor_achieved_by_shift"] <= high


@pytest.fixture(scope="module")
def record():
    """The real measurement, computed once for the whole module."""
    return measure()


class TestTheMeasuredAnswer:
    """The headline results, pinned so a refit cannot silently reverse them."""

    def test_both_fold_families_are_scored(self, record):
        assert set(record["families"]) == {"leave_city_out",
                                           "leave_station_out"}
        for family in record["families"].values():
            assert family["n_stations"] > 0

    def test_the_anchored_shape_beats_the_flat_anchor(self, record):
        for name, family in record["families"].items():
            assert family["mae_b1_anchored_shape"] < \
                family["mae_b0_flat_anchor"], name
            assert family["shape_improvement_pct"] > 0.0, name

    def test_the_unanchored_shape_is_worse_than_a_flat_anchor(self, record):
        # The anchor is not decoration: without it the transferred profile is
        # beaten by simply using the right level with no shape at all.
        for name, family in record["families"].items():
            assert family["mae_b1u_unanchored_shape"] > \
                family["mae_b0_flat_anchor"], name

    def test_leave_city_out_is_both_material_and_robust(self, record):
        family = record["families"]["leave_city_out"]
        assert family["material"] is True
        assert family["robust"] is True
        assert family["bootstrap_ci"][1] < 0

    def test_the_new_street_fold_is_material_but_not_robust(self, record):
        # Honest limit: on a genuinely new street the improvement is real in
        # size but not statistically separable at 25 stations. Gothenburg is
        # this case, so the caveat must not be lost.
        family = record["families"]["leave_station_out"]
        assert family["material"] is True
        assert family["robust"] is False

    def test_the_transferred_shape_is_under_amplified(self, record):
        # Real stations swing several times more than the transferred profile
        # does. That gap is the daily-split plan's amplitude question.
        for name, family in record["families"].items():
            assert family["mean_truth_span_pp"] > \
                2 * family["mean_anchored_shape_span_pp"], name

    def test_the_record_is_not_release_evidence(self, record):
        assert record["release_evidence"] is False
        assert record["schema_version"] == "anchored_shape_value_v1"
        assert record["limitations"]
        json.dumps(record)
