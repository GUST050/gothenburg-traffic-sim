"""Fas 0A of the direction plan: sensor 107's published D-factor is local
evidence about a PERIOD, and must be bound as exactly that.

The failure this guards against is specific and seductive: a yearly 3 400/3 100
turned into 96 per-quarter "measurements", which would look like calibration
evidence while being an annual average copied 96 times.  The anchor therefore
moves only the LEVEL of the estimated per-slot profile, keeps its shape, and
leaves every single-direction station's level-1 target untouched.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sensor_registry import load_registry
from traffic_sim.intake.direction_anchor import (DEFAULT_CLAMP,
                                                 anchor_split_entry,
                                                 anchor_split_payload,
                                                 period_slot_weights,
                                                 shift_share, sigmoid,
                                                 solve_odds_shift,
                                                 weighted_mean_share)

REGISTRY = "data_in/sensors.json"
NORTH = "60786979_3575001205_0"
SOUTH = "1455801464_18241874_0"


def _reference(sensor_id: str = "107"):
    return load_registry(REGISTRY)[sensor_id].directional_reference


def _payload(north_profile, *, q10=None, q90=None):
    """A direction-split entry in the exact shape dirsplit/predict.py writes."""
    q10 = q10 if q10 is not None else [value - 0.05 for value in north_profile]
    q90 = q90 if q90 is not None else [value + 0.05 for value in north_profile]
    return {
        "107": {
            "method": "test",
            "edge_shares": {NORTH: list(north_profile),
                            SOUTH: [1 - value for value in north_profile]},
            "edge_shares_q10": {NORTH: list(q10),
                                SOUTH: [1 - value for value in q90]},
            "edge_shares_q90": {NORTH: list(q90),
                                SOUTH: [1 - value for value in q10]},
        }
    }


class TestPublishedReferenceIsBoundWithProvenance:
    def test_sensor_107_carries_its_raw_directional_counts(self):
        reference = _reference()
        assert [d.value for d in reference.directions] == [3400.0, 3100.0]
        assert [d.bearing for d in reference.directions] == ["N", "S"]
        assert reference.total == 6500.0

    def test_the_counts_are_mapped_to_the_reviewed_directed_edges(self):
        registry = load_registry(REGISTRY)
        reference = registry["107"].directional_reference
        assert set(reference.edge_ids) == set(registry["107"].approved_edge_ids)
        # North is the 3 400 direction; the bearings were checked against the
        # edge geometry (352 deg N / 174 deg S) when the record was written.
        assert reference.share(NORTH) == pytest.approx(3400 / 6500)
        assert reference.share(SOUTH) == pytest.approx(3100 / 6500)

    def test_the_period_is_declared_and_bounded(self):
        reference = _reference()
        assert reference.period_start == "2025-01-01"
        assert reference.period_end_exclusive == "2026-01-01"
        assert reference.covers(datetime(2025, 9, 16).date())
        assert not reference.covers(datetime(2026, 1, 1).date())

    def test_provenance_is_mandatory(self):
        reference = _reference()
        assert reference.source["name"]
        assert reference.source["retrieved"]
        assert reference.status == "verified"

    def test_only_the_two_way_station_has_a_reference(self):
        registry = load_registry(REGISTRY)
        assert list(registry.directional_references()) == ["107"]

    def test_the_registry_round_trips_the_reference(self):
        registry = load_registry(REGISTRY)
        restored = registry.as_dict()["sensors"][0]["directional_reference"]
        assert restored["directions"][0]["edge_id"] == NORTH
        assert restored["time_semantics"] == "period_aggregate"


class TestPerSlotSemanticsCannotBeFabricated:
    """The whole point of the field: a D-factor is not 96 measurements."""

    def _registry_with(self, tmp_path, mutate):
        payload = json.loads(Path(REGISTRY).read_text())
        record = next(s for s in payload["sensors"] if s["sensor_id"] == "107")
        mutate(record["directional_reference"])
        path = tmp_path / "sensors.json"
        path.write_text(json.dumps(payload))
        return path

    def test_per_slot_time_semantics_are_rejected(self, tmp_path):
        path = self._registry_with(
            tmp_path, lambda ref: ref.update(time_semantics="per_slot"))
        with pytest.raises(ValueError, match="time_semantics"):
            load_registry(path)

    def test_an_edge_outside_the_station_is_rejected(self, tmp_path):
        path = self._registry_with(
            tmp_path,
            lambda ref: ref["directions"][0].update(edge_id="not_this_station"))
        with pytest.raises(ValueError, match="reviewed directed edges"):
            load_registry(path)

    def test_a_reference_without_a_source_is_rejected(self, tmp_path):
        path = self._registry_with(tmp_path, lambda ref: ref.pop("source"))
        with pytest.raises(ValueError, match="named source"):
            load_registry(path)

    def test_a_one_sided_reference_is_rejected(self, tmp_path):
        path = self._registry_with(
            tmp_path, lambda ref: ref.__setitem__("directions",
                                                  ref["directions"][:1]))
        with pytest.raises(ValueError, match="exactly two directions"):
            load_registry(path)

    def test_the_anchor_report_labels_per_slot_values_as_estimated(self):
        payload = _payload([0.5] * 96)
        _anchored, outcomes = anchor_split_payload(
            payload, {"107": _reference()})
        assert outcomes[0].as_dict()["per_slot_semantics"] == "estimated"


class TestAnchoringMath:
    def test_shift_is_monotone_and_stays_inside_the_clamp(self):
        assert shift_share(0.5, 0.0) == pytest.approx(0.5)
        assert shift_share(0.5, 1.0) > 0.5
        assert shift_share(0.5, -1.0) < 0.5
        assert shift_share(0.5, 50.0) == DEFAULT_CLAMP[1]
        assert shift_share(0.5, -50.0) == DEFAULT_CLAMP[0]

    def test_the_complement_direction_moves_exactly_oppositely(self):
        # sigmoid(logit(1-x) - d) == 1 - sigmoid(logit(x) + d): this identity is
        # what keeps a pair summing to one after anchoring.
        for share in (0.2, 0.42, 0.5, 0.63):
            for delta in (-0.8, -0.1, 0.0, 0.3, 1.4):
                assert (shift_share(1 - share, -delta)
                        == pytest.approx(1 - shift_share(share, delta)))

    def test_solver_hits_the_target_weighted_mean(self):
        shares = [0.40 + 0.004 * slot for slot in range(96)]
        weights = [1.0 + slot for slot in range(96)]
        target = 3400 / 6500
        delta = solve_odds_shift(shares, weights, target)
        assert weighted_mean_share(shares, weights, delta) == pytest.approx(
            target, abs=1e-9)

    def test_uniform_weights_are_not_the_same_answer_as_flow_weights(self):
        """A D-factor is a volume ratio, so the weighting is not cosmetic."""
        shares = [0.3] * 48 + [0.7] * 48
        flow_weights = [1.0] * 48 + [9.0] * 48
        target = 0.5
        flow_delta = solve_odds_shift(shares, flow_weights, target)
        uniform_delta = solve_odds_shift(shares, [1.0] * 96, target)
        assert flow_delta != pytest.approx(uniform_delta)

    def test_sigmoid_is_stable_for_large_negative_input(self):
        assert sigmoid(-800) == pytest.approx(0.0)
        assert sigmoid(800) == pytest.approx(1.0)


class TestPeriodWeights:
    EPOCH = datetime(2025, 1, 1)

    def test_only_slots_inside_the_declared_period_are_counted(self):
        flows = {NORTH: [10.0] * 96 + [1000.0] * 96,
                 SOUTH: [10.0] * 96 + [1000.0] * 96}
        weights = period_slot_weights(
            flows, [NORTH, SOUTH], epoch=self.EPOCH, interval_minutes=15,
            period_start="2025-01-01", period_end_exclusive="2025-01-02",
            slots_per_day=96)
        assert weights == [10.0] * 96

    def test_a_duplicated_two_way_total_is_counted_once(self):
        """CLAUDE.md's reporting trap: both directed edges carry the SAME
        delivered two-way value, so summing them double-counts the station."""
        flows = {NORTH: [10.0] * 96, SOUTH: [10.0] * 96}
        weights = period_slot_weights(
            flows, [NORTH, SOUTH], epoch=self.EPOCH, interval_minutes=15,
            period_start="2025-01-01", period_end_exclusive="2025-01-02",
            slots_per_day=96)
        assert weights[0] == 10.0

    def test_genuinely_directional_series_are_summed(self):
        flows = {NORTH: [6.0] * 96, SOUTH: [4.0] * 96}
        weights = period_slot_weights(
            flows, [NORTH, SOUTH], epoch=self.EPOCH, interval_minutes=15,
            period_start="2025-01-01", period_end_exclusive="2025-01-02",
            slots_per_day=96)
        assert weights[0] == 10.0

    def test_missing_quarters_contribute_nothing(self):
        flows = {NORTH: [None] * 4 + [8.0] * 92, SOUTH: [None] * 4 + [8.0] * 92}
        weights = period_slot_weights(
            flows, [NORTH, SOUTH], epoch=self.EPOCH, interval_minutes=15,
            period_start="2025-01-01", period_end_exclusive="2025-01-02",
            slots_per_day=96)
        assert weights[:4] == [0.0] * 4
        assert weights[4] == 8.0


class TestAnchoringOneStation:
    WEIGHTS = [1.0] * 96

    def test_the_declared_period_reproduces_the_published_share(self):
        reference = _reference()
        entry, outcome = anchor_split_entry(
            _payload([0.5] * 96)["107"], reference, self.WEIGHTS,
            sensor_id="107", weight_source="uniform_per_slot")
        assert outcome.status == "anchored"
        assert outcome.achieved_share == pytest.approx(3400 / 6500,
                                                      abs=reference.tolerance)
        assert entry["directional_anchor"]["target_share"] == pytest.approx(
            3400 / 6500)

    def test_both_directions_still_sum_to_the_two_way_total_every_slot(self):
        entry, _outcome = anchor_split_entry(
            _payload([0.45 + 0.001 * slot for slot in range(96)])["107"],
            _reference(), self.WEIGHTS, sensor_id="107",
            weight_source="uniform_per_slot")
        central = entry["edge_shares"]
        for slot in range(96):
            assert central[NORTH][slot] + central[SOUTH][slot] == pytest.approx(1.0)

    def test_time_of_day_shape_survives_the_anchor(self):
        """Only the level moves: the profile's ordering and its log-odds
        differences are exactly preserved, because the shift is constant."""
        profile = [0.40, 0.55, 0.48, 0.61] * 24
        entry, _outcome = anchor_split_entry(
            _payload(profile)["107"], _reference(), self.WEIGHTS,
            sensor_id="107", weight_source="uniform_per_slot")
        anchored = entry["edge_shares"][NORTH]
        assert sorted(range(96), key=lambda i: profile[i]) == sorted(
            range(96), key=lambda i: anchored[i])
        from traffic_sim.intake.direction_anchor import logit
        gaps = {round(logit(anchored[i]) - logit(profile[i]), 9)
                for i in range(96)}
        assert len(gaps) == 1

    def test_the_stress_interval_keeps_its_width_in_log_odds(self):
        """q10/q90 are stress cases, not probability statements: the anchor
        re-levels them with the SAME shift instead of collapsing or widening
        them, exactly as predict.py re-centres them after shrinkage."""
        from traffic_sim.intake.direction_anchor import logit
        profile = [0.50] * 96
        payload = _payload(profile, q10=[0.44] * 96, q90=[0.57] * 96)
        entry, _outcome = anchor_split_entry(
            payload["107"], _reference(), self.WEIGHTS, sensor_id="107",
            weight_source="uniform_per_slot")
        before = logit(0.57) - logit(0.44)
        after = (logit(entry["edge_shares_q90"][NORTH][0])
                 - logit(entry["edge_shares_q10"][NORTH][0]))
        assert after == pytest.approx(before)

    def test_mirrored_quantile_relations_survive(self):
        """The artifact stores edge_shares_q10[other] = 1 - q90[reference];
        anchoring must not quietly break that relation."""
        payload = _payload([0.50] * 96, q10=[0.44] * 96, q90=[0.57] * 96)
        entry, _outcome = anchor_split_entry(
            payload["107"], _reference(), self.WEIGHTS, sensor_id="107",
            weight_source="uniform_per_slot")
        assert entry["edge_shares_q10"][SOUTH][0] == pytest.approx(
            1 - entry["edge_shares_q90"][NORTH][0])
        assert entry["edge_shares_q90"][SOUTH][0] == pytest.approx(
            1 - entry["edge_shares_q10"][NORTH][0])

    def test_an_unrelated_edge_in_the_same_entry_is_left_alone(self):
        payload = _payload([0.5] * 96)
        payload["107"]["edge_shares"]["some_other_edge"] = [0.31] * 96
        entry, _outcome = anchor_split_entry(
            payload["107"], _reference(), self.WEIGHTS, sensor_id="107",
            weight_source="uniform_per_slot")
        assert entry["edge_shares"]["some_other_edge"] == [0.31] * 96

    def test_a_split_missing_the_reference_edge_fails_closed(self):
        payload = _payload([0.5] * 96)
        del payload["107"]["edge_shares"][NORTH]
        with pytest.raises(ValueError, match="disagree about this station"):
            anchor_split_entry(payload["107"], _reference(), self.WEIGHTS,
                               sensor_id="107", weight_source="uniform_per_slot")

    def test_a_station_without_an_estimate_is_skipped_not_invented(self):
        _entry, outcome = anchor_split_entry(
            {"method": "test"}, _reference(), self.WEIGHTS, sensor_id="107",
            weight_source="uniform_per_slot")
        assert outcome.status == "skipped_no_estimate"

    def test_an_empty_measurement_period_fails_closed(self):
        with pytest.raises(ValueError, match="no measured volume"):
            anchor_split_entry(_payload([0.5] * 96)["107"], _reference(),
                               [0.0] * 96, sensor_id="107",
                               weight_source="measured_two_way_total_per_slot")


class TestAnchoringThePayload:
    def test_stations_without_local_evidence_pass_through_unchanged(self):
        payload = _payload([0.5] * 96)
        payload["1076"] = {"edge_shares": {"measured": [0.6] * 96,
                                           "opposite": [0.4] * 96}}
        anchored, _outcomes = anchor_split_payload(
            payload, {"107": _reference()})
        assert anchored["1076"] == payload["1076"]

    def test_measured_weights_are_used_when_flows_are_available(self):
        payload = _payload([0.5] * 96)
        flows = {NORTH: [5.0] * 96, SOUTH: [5.0] * 96}
        _anchored, outcomes = anchor_split_payload(
            payload, {"107": _reference()}, flows=flows,
            epoch=datetime(2025, 1, 1), interval_minutes=15)
        assert outcomes[0].weight_source == "measured_two_way_total_per_slot"
        assert outcomes[0].weighted_slots == 96

    def test_a_period_with_no_measurements_falls_back_and_says_so(self):
        payload = _payload([0.5] * 96)
        flows = {NORTH: [5.0] * 96, SOUTH: [5.0] * 96}
        _anchored, outcomes = anchor_split_payload(
            payload, {"107": _reference()}, flows=flows,
            epoch=datetime(2019, 1, 1), interval_minutes=15)
        assert outcomes[0].weight_source == "uniform_fallback_no_measured_period"
        assert outcomes[0].status == "anchored"

    def test_the_source_payload_is_never_mutated(self):
        payload = _payload([0.5] * 96)
        before = json.dumps(payload, sort_keys=True)
        anchor_split_payload(payload, {"107": _reference()})
        assert json.dumps(payload, sort_keys=True) == before


class TestLevelOneTargetsAfterAnchoring:
    """The five single-direction stations must be byte-identical, and the
    anchored two-way station must still split its measured total exactly."""

    def _install_split(self, tmp_path, monkeypatch, payload):
        import demand.intake as intake
        (tmp_path / "direction_split.json").write_text(json.dumps(payload))
        monkeypatch.setattr(intake, "SUMO_DIR", tmp_path)
        monkeypatch.setattr(intake, "MEASURED_FLOWS_PATH",
                            tmp_path / "no-flows.json")
        intake._ANCHORED_SPLIT_CACHE.clear()
        return intake

    def test_a_single_direction_target_is_untouched(self, tmp_path, monkeypatch):
        payload = _payload([0.5] * 96)
        payload["1076"] = {"edge_shares": {"measured": [0.62] * 96,
                                           "opposite": [0.38] * 96}}
        intake = self._install_split(tmp_path, monkeypatch, payload)
        targets = intake.build_targets({"measured": [50.0]},
                                       {"1076": ["measured"]}, 0, 1)
        assert targets[0]["measured"] == 50.0

    def test_the_two_way_total_is_split_by_the_anchored_share(self, tmp_path,
                                                              monkeypatch):
        intake = self._install_split(tmp_path, monkeypatch, _payload([0.5] * 96))
        targets = intake.build_targets(
            {NORTH: [100.0], SOUTH: [100.0]}, {"107": [NORTH, SOUTH]}, 0, 1)
        assert targets[0][NORTH] + targets[0][SOUTH] == pytest.approx(100.0)
        assert targets[0][NORTH] == pytest.approx(100 * 3400 / 6500, abs=0.3)

    def test_the_anchor_report_is_available_to_the_build(self, tmp_path,
                                                         monkeypatch):
        intake = self._install_split(tmp_path, monkeypatch, _payload([0.5] * 96))
        report = intake.direction_anchor_report()
        assert report and report[0]["sensor_id"] == "107"
        assert report[0]["status"] == "anchored"
        assert report[0]["reference_edge"] == NORTH

    def test_without_a_split_file_nothing_is_anchored_or_invented(self, tmp_path,
                                                                  monkeypatch):
        import demand.intake as intake
        monkeypatch.setattr(intake, "SUMO_DIR", tmp_path)
        intake._ANCHORED_SPLIT_CACHE.clear()
        assert intake.load_direction_split() == {}
        assert intake.direction_anchor_report() == []
        targets = intake.build_targets(
            {NORTH: [100.0], SOUTH: [100.0]}, {"107": [NORTH, SOUTH]}, 0, 1)
        assert targets[0][NORTH] == 50.0        # unchanged even-split fallback


class TestDemandSourceIdentity:
    def test_the_anchor_module_is_part_of_the_demand_fingerprint(self):
        from traffic_sim.demand.source_identity import demand_source_paths
        paths = demand_source_paths(Path(__file__).resolve().parents[1])
        assert paths["direction_anchor"].name == "direction_anchor.py"
