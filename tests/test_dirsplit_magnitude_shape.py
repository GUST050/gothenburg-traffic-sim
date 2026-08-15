from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from tools import measure_dirsplit_magnitude_shape as magnitude


def split_fixture(left=0.4, right=0.6):
    return {
        "sensor": {
            "edge_shares": {"a": [left] * 96, "b": [right] * 96},
            "edge_shares_q10": {"a": [0.2] * 96, "b": [0.8] * 96},
            "edge_shares_q90": {"a": [0.7] * 96, "b": [0.3] * 96},
        }
    }


def test_split_validation_checks_every_arm_and_slot():
    result = magnitude.validate_direction_split(split_fixture())
    assert result == {
        "sensors": 1,
        "arms": ["q50", "q10_stress", "q90_stress"],
        "slots_checked": 288,
        "max_abs_pair_sum_deviation": 0.0,
        "passes_exact_pair_sum": True,
    }


@pytest.mark.parametrize("mutation", ["missing_edge", "wrong_length", "nan"])
def test_split_validation_fails_closed(mutation):
    data = split_fixture()
    block = data["sensor"]["edge_shares_q10"]
    if mutation == "missing_edge":
        block.pop("b")
    elif mutation == "wrong_length":
        block["a"] = [0.2] * 95
    elif mutation == "nan":
        block["a"][10] = float("nan")
    with pytest.raises(ValueError):
        magnitude.validate_direction_split(data)


def test_split_validation_exposes_noncomplementary_pair():
    data = split_fixture()
    data["sensor"]["edge_shares_q10"]["b"][10] = 0.7
    result = magnitude.validate_direction_split(data)
    assert not result["passes_exact_pair_sum"]
    assert result["max_abs_pair_sum_deviation"] == pytest.approx(0.1)


def test_measured_mass_deduplicates_two_way_total():
    flows = {
        "north": [10, 20],
        "south": [10, 20],
        "west": [7, None],
    }
    registry = [
        {"sensor_id": "total", "measurement_semantics": "two_way_total",
         "approved_edge_ids": ["north", "south"]},
        {"sensor_id": "direction", "measurement_semantics": "directional",
         "approved_edge_ids": ["west"]},
    ]
    result = magnitude.measured_mass(flows, registry)
    assert result["by_sensor"] == {"total": 30.0, "direction": 7.0}
    assert result["total"] == 37.0


def test_measured_mass_rejects_nonidentical_total_twins():
    flows = {"north": [10], "south": [9]}
    registry = [{
        "sensor_id": "total", "measurement_semantics": "two_way_total",
        "approved_edge_ids": ["north", "south"],
    }]
    with pytest.raises(ValueError, match="twins"):
        magnitude.measured_mass(flows, registry)


def test_moved_vehicles_repeats_96_slot_profile():
    flow = [100.0] * 192
    shares = [0.6] * 96
    assert magnitude.moved_vehicles(flow, shares) == pytest.approx(1920.0)
    assert magnitude.moved_vehicles(flow, shares, 0.55) == pytest.approx(960.0)


def test_weighted_share_ignores_missing_flow():
    shares = [0.4, 0.8] + [0.5] * 94
    assert magnitude.weighted_share([10.0, None], shares) == pytest.approx(0.4)


class Row:
    def __init__(self, station_id, share):
        self.station_id = station_id
        self.share = share


def test_level_shape_decomposition_is_exact():
    rows = [Row("a", 0.4), Row("a", 0.6),
            Row("b", 0.6), Row("b", 0.8)]
    level, shape, means = magnitude.level_shape_decomposition(rows)
    total = np.array([row.share - 0.5 for row in rows])
    assert means == {"a": pytest.approx(0.5), "b": pytest.approx(0.7)}
    assert np.allclose(level + shape, total)
    assert np.sum(level * shape) == pytest.approx(0.0)


def test_station_block_bootstrap_is_deterministic():
    diff = np.array([-0.2, -0.1, 0.1, 0.2])
    stations = ["a", "a", "b", "b"]
    first = magnitude.station_block_bootstrap(
        diff, stations, samples=50, seed=17)
    second = magnitude.station_block_bootstrap(
        diff, stations, samples=50, seed=17)
    assert first == second
    assert first[0] <= first[1]


@pytest.fixture(scope="module")
def live_evidence():
    return magnitude.build_evidence()


def test_live_evidence_binds_current_date_level_dataset(live_evidence):
    dataset = live_evidence["dataset"]
    assert dataset["primary_rows"] == 247_464
    assert dataset["primary_usable_rows"] == 232_500
    assert dataset["aggregate_raw_rows"] == 15_346
    assert dataset["source_volume_files"] == 188


def test_live_evidence_keeps_1214_derived_rows_explicit(live_evidence):
    shape = live_evidence["shape_transfer"]
    assert shape["screened_rows"] == 3694
    assert shape["deployment_window_rows"] == 1214
    assert shape["stations"] == 81
    assert shape["source_role"].startswith("diagnostic aggregate")


def test_live_evidence_rejects_level_shape_candidate(live_evidence):
    shape = live_evidence["shape_transfer"]
    assert shape["decision"] == "NEGATIVE_EVIDENCE"
    assert not shape["hour_lookup_paired_station_block_bootstrap"][
        "significant_win"]
    assert shape["leave_city_out_mae"]["leave_city_hour_lookup_15_values"] \
        < shape["leave_city_out_mae"]["flat_no_shape"]
    assert shape["leave_city_out_mae"]["leave_city_hour_lookup_15_values"] \
        < shape["leave_city_out_mae"]["leave_city_lgbm_19_features"]


def test_live_level1_separates_transfer_shape_from_local_anchor(live_evidence):
    level1 = live_evidence["level1"]
    raw = level1["raw_transferred_policy_vs_flat_5050"]["q50"]
    active = level1["active_q50_with_local_anchor_vs_flat_5050"]
    shape = level1["active_q50_shape_vs_flat_same_local_level"]
    assert raw["pct_of_all_measured_mass"] < active[
        "pct_of_all_measured_mass"]
    assert shape["pct_of_all_measured_mass"] < active[
        "pct_of_all_measured_mass"]
    assert level1["active_volume_weighted_share_by_sensor"]["107"] \
        == pytest.approx(0.523, abs=0.001)


def test_live_split_is_complementary_for_all_sensors(live_evidence):
    pair_sum = live_evidence["pair_sum_validation"]
    assert pair_sum["sensors"] == 6
    assert pair_sum["slots_checked"] == 1728
    assert pair_sum["max_abs_pair_sum_deviation"] == 0.0
    assert pair_sum["passes_exact_pair_sum"]


def test_live_level23_is_pre_pfe_not_realised_traffic(live_evidence):
    level23 = live_evidence["level23"]
    assert len(level23["by_sensor"]) == 5
    assert level23["stress_envelope_low_annualised"] \
        < level23["implicit_opposite_q50_annualised"] \
        < level23["stress_envelope_high_annualised"]
    assert "not realised simulated traffic" in level23["authority"]


def test_live_content_key_and_input_hashes_are_verifiable(live_evidence):
    payload = dict(live_evidence)
    recorded = payload.pop("content_key")
    assert recorded == magnitude.content_digest(payload)
    for binding in live_evidence["inputs"].values():
        path = magnitude.ROOT / binding["path"]
        assert binding["sha256"] == magnitude.sha256_file(path)


def test_emit_evidence_round_trips(tmp_path, live_evidence):
    path = tmp_path / "evidence.json"
    magnitude.emit_evidence(path, live_evidence)
    loaded = json.loads(path.read_text())
    assert loaded == live_evidence
    assert loaded["release_evidence"] is False
    assert loaded["decision_authority"] == "diagnostic_only"
