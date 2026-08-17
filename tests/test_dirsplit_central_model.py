"""The deployed central profile is the model that won the tournament.

The direction split shipped to the demand builder used to be a per-sensor
similarity-weighted LightGBM. Measured on identical leakage-free folds, that
family does not beat 50/50 (its raw form is worse), while an hour x day-type
D-factor partially pooled toward 0.5 — with no street features at all — does.
These tests pin the consequences: the deployed path fits the SAME class the
benchmark scored, keeps pair and quantile structure intact, orients each pair
from published geometry, and still lets the published local anchor land on top.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dirsplit import predict
from dirsplit.benchmark import ShrunkDFactor

NORTH = "60786979_3575001205_0"
SOUTH = "1455801464_18241874_0"


@pytest.fixture(scope="module")
def profile():
    return predict.fit_dfactor_profile()


@pytest.fixture(scope="module")
def network():
    return json.loads(Path("web/data/network.geojson").read_text())


class TestTheDeployedModelIsTheMeasuredWinner:
    def test_it_fits_the_benchmarked_class_not_a_reimplementation(self, monkeypatch):
        """What ships must be what was scored, or the evidence means nothing."""
        fitted: list[str] = []
        original = ShrunkDFactor.fit

        def spy(self, train):
            fitted.append(type(self).__name__)
            return original(self, train)

        monkeypatch.setattr(ShrunkDFactor, "fit", spy)
        predict.fit_dfactor_profile()
        assert fitted and set(fitted) == {"ShrunkDFactor"}

    def test_it_uses_no_street_features(self, profile):
        assert "no street features" in profile["provenance"]["description"]

    def test_provenance_names_its_training_data(self, profile):
        provenance = profile["provenance"]
        assert provenance["training_table"].endswith("training_table.csv")
        assert provenance["training_table_digest"]
        assert provenance["training_rows"] > 0
        assert provenance["training_stations"] > 0

    def test_the_one_way_screen_is_recorded(self, profile):
        # The deployed fit must use the same population the tournament scored.
        assert profile["provenance"]["one_way_station_directions_dropped"] == 97

    def test_hour_support_is_published_so_thin_hours_are_visible(self, profile):
        support = profile["provenance"]["hour_support"]
        assert len(support) == 24
        assert support[8] > support[3]        # night hours are thinner, and said so


class TestTheCentralCurveIsAPlausibleDFactor:
    def test_it_covers_every_hour(self, profile):
        assert len(profile["central"]) == 24

    def test_the_morning_leans_toward_the_centre_and_the_afternoon_away(self, profile):
        assert profile["central"][7] > 0.5
        assert profile["central"][16] < profile["central"][7]

    def test_the_asymmetry_stays_mild(self, profile):
        """Central urban streets are near 50/50; an 80/20 answer would be the
        old Gaussian estimate's known over-attribution, not a measurement."""
        assert max(abs(value - 0.5) for value in profile["central"]) < 0.15

    def test_quantiles_never_cross(self, profile):
        assert np.all(profile["q10"] <= profile["central"] + 1e-9)
        assert np.all(profile["central"] <= profile["q90"] + 1e-9)

    def test_everything_stays_inside_the_declared_clamp(self, profile):
        for key in ("central", "q10", "q90"):
            assert np.all(profile[key] >= predict.CLAMP[0])
            assert np.all(profile[key] <= predict.CLAMP[1])

    def test_the_band_is_labelled_uncalibrated_for_the_target_city(self, profile):
        provenance = profile["provenance"]
        assert provenance["band"] == "leave_city_out_residual_q10_q90"
        assert "uncalibrated" in provenance["band_calibration"]


class TestOrientationComesFromPublishedGeometry:
    def test_it_reproduces_the_graph_derived_radial_cos(self, network):
        """The geometry shortcut must agree with dirsplit/features.py, or the
        deployed path would orient pairs differently from the trained model."""
        graph_values = {edge["edge"]: edge["features"]["radial_cos"]
                        for edge in json.loads(
                            Path("data/dirsplit/coverage_report.json").read_text()
                        )["edges"]}
        geometry = {feature["properties"]["id"]: feature["geometry"]["coordinates"]
                    for feature in network["features"]
                    if feature["geometry"]["type"] == "LineString"}
        checked = 0
        for edge_id, expected in graph_values.items():
            actual = predict.geometry_orientation({"id": edge_id}, geometry[edge_id])
            assert actual == pytest.approx(expected, abs=1e-3), edge_id
            checked += 1
        assert checked >= 7

    def test_the_two_sides_of_a_pair_are_opposites(self, network):
        geometry = {feature["properties"]["id"]: feature["geometry"]["coordinates"]
                    for feature in network["features"]
                    if feature["geometry"]["type"] == "LineString"}
        north = predict.geometry_orientation({"id": NORTH}, geometry[NORTH])
        south = predict.geometry_orientation({"id": SOUTH}, geometry[SOUTH])
        assert north > 0 > south


@pytest.fixture(scope="module")
def written(network):
    return predict.predict_dfactor(predict.collect_pairs(network), network,
                                   verbose=False)


class TestTheWrittenArtifact:
    @pytest.fixture()
    def result(self, written):
        return written

    def test_every_station_gets_both_directions(self, result):
        assert set(result) == {"107", "1074", "1076", "133", "134", "2276"}
        for entry in result.values():
            assert len(entry["edge_shares"]) == 2

    def test_each_pair_sums_to_one_in_the_central_profile(self, result):
        for entry in result.values():
            values = list(entry["edge_shares"].values())
            for slot in range(96):
                assert values[0][slot] + values[1][slot] == pytest.approx(1.0,
                                                                         abs=1e-9)

    def test_the_profile_has_one_value_per_quarter(self, result):
        for entry in result.values():
            for key in ("edge_shares", "edge_shares_q10", "edge_shares_q90"):
                assert all(len(values) == 96 for values in entry[key].values())

    def test_the_mirrored_quantile_layout_is_preserved(self, result):
        entry = result["107"]
        toward = max(entry["edge_shares"], key=lambda e: entry["edge_shares"][e][28])
        away = next(e for e in entry["edge_shares"] if e != toward)
        assert entry["edge_shares_q10"][away][0] == pytest.approx(
            1 - entry["edge_shares_q90"][toward][0])

    def test_sensor_107_is_oriented_northbound_toward_the_centre(self, result):
        entry = result["107"]
        assert entry["edge_shares"][NORTH][28] > entry["edge_shares"][SOUTH][28]

    def test_the_per_slot_values_are_still_declared_estimates(self, result):
        for entry in result.values():
            assert entry["note"].startswith("ESTIMATED")
            assert "UNCALIBRATED" in entry["note"]

    def test_the_central_model_is_named_in_the_artifact(self, result):
        assert result["107"]["central_model"]["model"] == "shrunk_dfactor"

    def test_the_day_type_it_represents_is_stated(self, result):
        assert result["107"]["day_type"] == "weekday"


class TestTheLocalAnchorStillLandsOnTop:
    def test_the_published_period_share_is_reproduced(self, tmp_path, network,
                                                      monkeypatch):
        import demand.intake as intake
        from traffic_sim.intake.direction_anchor import load_measurements

        result = predict.predict_dfactor(predict.collect_pairs(network), network,
                                         verbose=False)
        (tmp_path / "direction_split.json").write_text(json.dumps(result))
        monkeypatch.setattr(intake, "SUMO_DIR", tmp_path)
        intake._ANCHORED_SPLIT_CACHE.clear()
        _shares, report = intake.load_anchored_direction_split()
        anchor = next(entry for entry in report if entry["sensor_id"] == "107")
        assert anchor["status"] == "anchored"
        assert anchor["achieved_share"] == pytest.approx(3400 / 6500, abs=0.002)
        assert load_measurements(intake.MEASURED_FLOWS_PATH)[2] == 15

    def test_the_anchor_moves_the_level_not_the_shape(self, tmp_path, network,
                                                     monkeypatch):
        import demand.intake as intake

        result = predict.predict_dfactor(predict.collect_pairs(network), network,
                                         verbose=False)
        before = list(result["107"]["edge_shares"][NORTH])
        (tmp_path / "direction_split.json").write_text(json.dumps(result))
        monkeypatch.setattr(intake, "SUMO_DIR", tmp_path)
        intake._ANCHORED_SPLIT_CACHE.clear()
        after = intake.load_direction_split()[NORTH]
        assert sorted(range(96), key=lambda i: before[i]) == sorted(
            range(96), key=lambda i: after[i])
        assert after[28] > before[28]          # the level moved up to 52/48


class TestThereIsOnlyOneDeployedPath:
    """The superseded LightGBM backend was removed, not merely defaulted away —
    a dormant second implementation is how two direction estimates drift apart."""

    def test_the_retired_backend_is_gone(self):
        assert not hasattr(predict, "predict_lightgbm")
        assert not Path("dirsplit/train.py").exists()
        assert not Path("data/dirsplit/model.pkl").exists()

    def test_the_gaussian_fallback_is_gone(self):
        assert not Path("estimate_directions.py").exists()

    def test_the_deployed_path_needs_no_model_package(self):
        source = Path("dirsplit/predict.py").read_text()
        assert "pickle" not in source
        assert "load_city_graph" not in source

    def test_running_it_writes_the_dfactor_profile(self, tmp_path):
        out = tmp_path / "split.json"
        predict.main(["--out", str(out)])
        written = json.loads(out.read_text())
        assert written["107"]["central_model"]["model"] == "shrunk_dfactor"
