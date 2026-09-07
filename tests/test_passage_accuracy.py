"""Accuracy of SUMO's realised sensor passages, judged by a published rule.

The report used to score this section on exact integer equality: every
directed sensor x 15 minutes had to match its rounded target to the vehicle.
On a real build that scored 100 of 672 and read as a near-total failure. It
was not one. Measured on the same artifact: the signed error is unbiased
(260 cells over, 266 under), daily volume per direction lands within 0.22%,
and GEH is below 5 on every one of the 672 quarter cells with a median of
0.31.

Exactness collapses with COUNT, not with accuracy — targets of 0-3 vehicles
matched 52.9% of the time and targets of 30-100 matched 4.0% — because a
0.2% relative error on 3,491 vehicles is still 8 vehicles, and "exact" means
zero. Half the shortfall was not even about the model: the score compared the
MEAN of three seeds against an integer, and that mean is not an integer in
341 of 672 cells, so it cannot match however good the run is.

These tests pin the replacement: DfT TAG Unit M3.1 Table 2, the standard the
repository already applies to held-out link flows in tools/validate_dmrb.py.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.simulation import sensor_fit
from tools import validate_dmrb


def _direction(target, simulated, seeds=None):
    return {
        "sensor_id": "107",
        "direction": "S",
        "target_mean": list(target),
        "simulated_mean_raw": list(simulated),
        "seed_runs": [{"seed": 1000 + i, "simulated_raw": list(s)}
                      for i, s in enumerate(seeds or [simulated])],
    }


def _audit(*directions):
    return {"directions": list(directions)}


class TestPublishedThresholds:
    def test_the_criteria_come_from_the_standard_the_repo_already_applies(self):
        """Both numbers are TAG M3.1's, not fitted to any of our builds.

        validate_dmrb.py carries them for hourly held-out flows; sensor_fit
        carries them for realised passages. Two copies of a published
        constant are fine; two DIFFERENT values would mean the project had
        quietly adopted two standards.
        """
        assert sensor_fit.PASSAGE_GEH_GUIDELINE_PCT == validate_dmrb.GUIDELINE_PCT
        assert (sensor_fit.PASSAGE_VOLUME_LIMIT_PCT
                == validate_dmrb.AGGREGATE_VOLUME_ERROR_LIMIT_PCT)
        assert sensor_fit.PASSAGE_GEH_LIMIT == 5.0


class TestPassageAccuracy:
    def test_a_perfect_run_passes_and_reports_no_error(self):
        target = [40.0] * 8
        result = sensor_fit.assess_passage_accuracy(
            _audit(_direction(target, target)), n_intervals=8)

        assert result["ok"] is True
        assert result["geh_within"] == result["geh_cells"] == 8
        assert result["geh_max"] == pytest.approx(0.0)
        assert result["volume_max_abs_pct"] == pytest.approx(0.0)
        assert result["exact_ensemble"] == 8

    def test_an_accurate_run_passes_even_though_almost_nothing_is_exact(self):
        """The case the old score called a failure.

        Every cell is off by one vehicle in fifty — comfortably inside TAG —
        yet not one cell matches its integer target.
        """
        target = [50.0] * 8
        simulated = [51.0] * 8
        result = sensor_fit.assess_passage_accuracy(
            _audit(_direction(target, simulated)), n_intervals=8)

        assert result["exact_ensemble"] == 0
        assert result["geh_max"] < sensor_fit.PASSAGE_GEH_LIMIT
        assert result["volume_max_abs_pct"] == pytest.approx(2.0)
        assert result["ok"] is True, (
            "a 2% run must not be reported as a failed passage test")

    def test_a_volume_leak_fails_even_when_every_quarter_looks_fine(self):
        """Losing a tenth of the day's vehicles is a real defect."""
        target = [100.0] * 8
        simulated = [88.0] * 8
        result = sensor_fit.assess_passage_accuracy(
            _audit(_direction(target, simulated)), n_intervals=8)

        assert result["volume_max_abs_pct"] == pytest.approx(12.0)
        assert result["ok"] is False

    def test_scattered_quarters_fail_when_too_few_are_within_geh(self):
        """Volume can balance while the profile is wrong; GEH catches that."""
        target = [100.0] * 8
        simulated = [200.0, 0.0] * 4          # same daily total, wrong shape
        result = sensor_fit.assess_passage_accuracy(
            _audit(_direction(target, simulated)), n_intervals=8)

        assert result["volume_max_abs_pct"] == pytest.approx(0.0)
        assert result["geh_pct"] < sensor_fit.PASSAGE_GEH_GUIDELINE_PCT
        assert result["ok"] is False

    def test_the_ensemble_rounding_artifact_is_reported_not_hidden(self):
        """Three integers averaging to 45.33 cannot match an integer target.

        The count is published so a reader can see how much of any exact
        shortfall is arithmetic rather than error.
        """
        target = [45.0] * 4
        seeds = [[45.0] * 4, [45.0] * 4, [46.0] * 4]
        ensemble = [45 + 1 / 3] * 4
        result = sensor_fit.assess_passage_accuracy(
            _audit(_direction(target, ensemble, seeds)), n_intervals=4)

        assert result["non_integer_ensemble_cells"] == 4
        assert result["exact_ensemble"] == 0
        assert result["exact_any_seed"] == 4, (
            "two of three seeds hit the target exactly in every cell")

    def test_relative_error_ignores_cells_too_small_to_be_meaningful(self):
        """A one-vehicle miss on a target of one is 100% and says nothing."""
        target = [1.0, 1.0, 60.0, 60.0]
        simulated = [2.0, 0.0, 63.0, 57.0]
        result = sensor_fit.assess_passage_accuracy(
            _audit(_direction(target, simulated)), n_intervals=4)

        assert result["relative_error_cells"] == 2
        assert result["relative_error_median_pct"] == pytest.approx(5.0)

    def test_an_empty_audit_is_reported_missing_rather_than_perfect(self):
        result = sensor_fit.assess_passage_accuracy(_audit(), n_intervals=8)
        assert result["ok"] is False
        assert result["geh_cells"] == 0
