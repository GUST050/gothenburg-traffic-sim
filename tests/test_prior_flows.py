"""Level-3 priors read the deployed direction split; they never re-derive it.

Until 2026-08-16 prior_flows.py loaded the LightGBM package and re-ran the
prediction with its own re-orientation and shrinkage arithmetic — a second
implementation of the direction split that would have kept serving the retired
model after the central profile changed. It now reads the same artifact the
demand builder reads, so the published local anchor applies here too and there
is exactly one direction estimate in the program.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import prior_flows
from prior_flows import opposite_series, single_direction_pairs


class TestPairsComeFromTheValidatedRegistry:
    def test_every_single_direction_station_is_paired(self):
        pairs = single_direction_pairs()
        assert {sensor for sensor, _measured, _opposite in pairs} == {
            "1074", "1076", "133", "134", "2276"}

    def test_the_two_way_station_has_no_unmeasured_twin(self):
        assert "107" not in {sensor for sensor, _m, _o in single_direction_pairs()}

    def test_the_reviewed_opposite_edge_is_used_not_a_fresh_lookup(self):
        """1076's opposite carriageway sits 82.5 m away, outside build_data's
        80 m snap rule — only the reviewed record knows which edge it is."""
        pairs = {sensor: (measured, opposite)
                 for sensor, measured, opposite in single_direction_pairs()}
        assert pairs["1076"] == ("30420757_30421744_0",
                                 "559960490_1305379743_0")

    def test_a_station_without_a_registered_opposite_is_skipped(self, tmp_path):
        registry = tmp_path / "sensors.json"
        registry.write_text(json.dumps({"schema_version": 1, "sensors": [
            {"sensor_id": "999", "approved_edge_ids": ["m"]}]}))
        assert single_direction_pairs(registry) == []


class TestPriorArithmetic:
    def test_the_opposite_follows_from_the_measured_share(self):
        measured = np.array([100.0, 100.0])
        share = np.array([0.5, 0.8])
        assert opposite_series(measured, share) == [100.0, 25.0]

    def test_a_missing_quarter_stays_missing(self):
        values = opposite_series(np.array([np.nan]), np.array([0.5]))
        assert values == [None]

    def test_a_degenerate_share_cannot_produce_infinity(self):
        assert opposite_series(np.array([50.0]), np.array([0.0])) == [None]


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    out = tmp_path_factory.mktemp("priors") / "prior_flows.json"
    prior_flows.main(["--out", str(out)])
    return json.loads(out.read_text())


class TestTheWrittenPriors:
    def test_every_unmeasured_carriageway_gets_a_prior(self, written):
        assert len(written["edges"]) == 5

    def test_the_band_is_ordered_low_below_high(self, written):
        for record in written["edges"].values():
            pairs = [(low, high) for low, high in
                     zip(record["prior_low"], record["prior_high"])
                     if low is not None and high is not None]
            assert pairs
            assert all(low <= high for low, high in pairs)

    def test_the_prior_sits_inside_its_own_band(self, written):
        for record in written["edges"].values():
            triples = [(low, mid, high) for low, mid, high in
                       zip(record["prior_low"], record["prior"],
                           record["prior_high"])
                       if None not in (low, mid, high)]
            assert triples
            assert all(low <= mid <= high for low, mid, high in triples)

    def test_provenance_names_the_deployed_artifact(self, written):
        for record in written["edges"].values():
            assert "direction_split.json" in record["provenance"]
            assert "anchor" in record["provenance"]

    def test_it_carries_one_day_of_quarters(self, written):
        assert written["n_quarters"] == 96
        for record in written["edges"].values():
            assert len(record["prior"]) == 96


class TestNoSecondDirectionImplementation:
    def test_the_module_does_not_load_a_model_package(self):
        source = Path("prior_flows.py").read_text()
        assert "pickle" not in source
        assert "MODEL_PATH" not in source

    def test_it_uses_the_shared_loader(self):
        source = Path("prior_flows.py").read_text()
        assert "from demand.intake import load_direction_split" in source
