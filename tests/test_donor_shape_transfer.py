"""Can a nearby counter's shape replace the pooled group curve? Measured, not assumed.

The study exists because two standard constructions answer the same question —
apply a group of continuous counters' temporal pattern, or borrow a nearby
permanent counter's — and Gothenburg has a candidate donor 239.9 m from sensor
107 at a bearing delta of 3.5 degrees. These tests pin the three traps the
measurement had to survive: an unoriented population collapses the group curve
to a flat 0.5 and hands the donor a win it never earned; reciprocal pairs count
one piece of evidence twice; and a bootstrap over a handful of pairs prints a
tight interval from almost nothing.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dirsplit.benchmark import MIN_INDEPENDENT_GROUPS
from tools import measure_donor_shape_transfer as study

ARTIFACT = Path("validation/donor_shape_transfer_v1.json")


@pytest.fixture(scope="module")
def report():
    return json.loads(ARTIFACT.read_text())


class TestGeometry:
    def test_bearing_delta_wraps_around_north(self):
        assert study.bearing_delta_deg(350.0, 10.0) == pytest.approx(20.0)
        assert study.bearing_delta_deg(10.0, 350.0) == pytest.approx(20.0)

    def test_distance_is_metric(self):
        # One degree of latitude is about 111 km.
        assert study.haversine_m(57.0, 11.0, 58.0, 11.0) == pytest.approx(111_000,
                                                                         rel=0.01)

    def test_road_identity_ignores_the_chainage(self):
        assert study.road_number("FV582 S1D1 m272") == "FV582"
        assert study.road_number("EV39 S79D10 m1192") == "EV39"
        assert study.road_number(None) is None

    def test_the_gothenburg_pair_falls_in_the_tightest_band(self, report):
        reference = report["geometry_rule"]["gothenburg_reference"]
        assert reference["distance_m"] <= study.DISTANCE_BANDS_M[0]
        assert reference["bearing_delta_deg"] <= study.MAX_BEARING_DELTA_DEG
        assert reference["band"] == "within_300m"


class TestRelevelling:
    def test_it_reproduces_the_target_anchor(self):
        shape = {hour: 0.40 + 0.01 * hour for hour in range(24)}
        hours = list(range(24))
        weights = [1.0 + hour for hour in hours]
        levelled = study.relevel(shape, hours, weights, 0.523)
        assert levelled is not None
        achieved = sum(w * v for v, w in zip(levelled, weights)) / sum(weights)
        assert achieved == pytest.approx(0.523, abs=1e-6)

    def test_a_curve_missing_an_hour_cannot_be_used(self):
        shape = {hour: 0.5 for hour in range(23)}
        assert study.relevel(shape, list(range(24)), [1.0] * 24, 0.5) is None

    def test_levelling_preserves_the_shape_ordering(self):
        shape = {hour: 0.40 + 0.01 * hour for hour in range(24)}
        levelled = study.relevel(shape, list(range(24)), [1.0] * 24, 0.60)
        assert levelled == sorted(levelled)


class TestTheThreeTraps:
    def test_the_group_curve_is_fitted_on_an_oriented_population(self):
        """Unoriented, a station's two mirrored headings cancel exactly and the
        pooled D-factor collapses to a flat 0.5 — which would make the group
        look worthless and the donor look brilliant."""
        source = Path("tools/measure_donor_shape_transfer.py").read_text()
        assert "orient_toward_centre(observations)" in source

    def test_each_station_pair_is_counted_once(self, report):
        for band in report["bands"].values():
            records = band.get("records")
            if not records:
                continue
            pairs = [frozenset({record["target"].split(":")[0],
                                record["donor"].split(":")[0]})
                     for record in records]
            assert len(pairs) == len(set(pairs))

    def test_a_station_is_never_its_own_donor(self, report):
        for band in report["bands"].values():
            for record in band.get("records", []):
                assert record["target"].split(":")[0] != record["donor"].split(":")[0]

    def test_robustness_needs_the_frozen_minimum_sample(self, report):
        for band in report["bands"].values():
            if not band.get("n_targets"):
                continue
            if band["n_targets"] < MIN_INDEPENDENT_GROUPS:
                assert band["robust"] is False
                assert band["sufficient_sample"] is False


class TestTheMeasuredOutcome:
    def test_it_is_diagnostic_only(self, report):
        assert report["release_evidence"] is False
        assert any("no gate" in note for note in report["limitations"])

    def test_the_band_matching_gothenburg_is_too_thin_to_decide(self, report):
        """Two independent pairs at the 107/1076 geometry: the +57% cannot be
        promoted, and the verdict must say so rather than print robustness."""
        band = report["bands"]["within_300m"]
        assert band["n_targets"] < MIN_INDEPENDENT_GROUPS
        assert any("INSUFFICIENT" in verdict and "within_300m" in verdict
                   for verdict in report["verdicts"])

    def test_no_band_lets_the_donor_claim_a_decided_win(self, report):
        assert not any("DONOR beats the group" in verdict
                       for verdict in report["verdicts"])

    def test_the_group_curve_still_beats_a_flat_anchor(self, report):
        """Shape helps; the open question is only which shape source."""
        for name, band in report["bands"].items():
            if not band.get("n_targets"):
                continue
            assert band["mae_b1_group_shape"] <= band["mae_b0_flat_anchor"], name
