"""Fas 0A acceptance: sensor 107's published anchor, bound correctly.

Plan acceptance for Fas 0A:
  * 107's two directions sum, every slot, to the measured two-way total;
  * the declared period reproduces 52/48 within rounding tolerance;
  * quarter values stay labelled estimated, not Level-1 direction
    measurements;
  * the five single-direction stations' Level-1 targets are byte-identical;
  * old golden and resume artifacts are unchanged.

The plan is explicit that a hardcoded 0.52 in `build_targets` is not
acceptable, because it would lose the source, the year, the direction mapping
and the time semantics. These tests hold each of those separately.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import pytest

from traffic_sim.intake import sensors as sr

REGISTRY = Path("data_in/sensors.json")
N_EDGE = "60786979_3575001205_0"
S_EDGE = "1455801464_18241874_0"
SINGLE_DIRECTION = ("1074", "1076", "133", "134", "2276")


@pytest.fixture(scope="module")
def registry():
    return sr.load_registry(REGISTRY)


@pytest.fixture(scope="module")
def reference(registry):
    ref = registry.directional_reference_for("107")
    assert ref is not None, "sensor 107 must carry a directional_reference"
    return ref


# ── provenance ────────────────────────────────────────────────────────────
class TestProvenanceIsBound:
    def test_the_raw_counts_are_kept_not_only_the_ratio(self, reference):
        assert reference.raw_counts == {"N": 3400.0, "S": 3100.0}
        assert reference.total == 6500.0

    def test_the_period_is_explicit(self, reference):
        assert reference.period_year == 2025
        assert reference.period_start == "2025-01-01"
        assert reference.period_end == "2025-12-31"
        assert reference.period_kind == "calendar_year"

    def test_the_source_is_named(self, reference):
        assert "trafikmangder" in reference.source.lower()
        assert reference.verification["status"] == "verified"

    def test_the_aggregation_is_named(self, reference):
        assert reference.aggregation == "annual_average_daily_two_way"

    def test_comparison_periods_record_the_earlier_years(self, reference):
        years = {p["year"] for p in reference.comparison_periods}
        assert years == {2023, 2024}


# ── orientation ───────────────────────────────────────────────────────────
class TestOrientation:
    def test_each_direction_maps_to_one_directed_edge(self, reference):
        assert reference.bearing_to_edge == {"N": N_EDGE, "S": S_EDGE}

    def test_the_mapping_matches_the_network_geometry(self, reference):
        """N/S came from the committed segment bearings, not an assumption."""
        assert reference.edge_bearing_deg[N_EDGE] == pytest.approx(352.1)
        assert reference.edge_bearing_deg[S_EDGE] == pytest.approx(174.4)
        # A directed pair must be near-antipodal.
        delta = abs(reference.edge_bearing_deg[N_EDGE]
                    - reference.edge_bearing_deg[S_EDGE])
        assert 170 <= min(delta, 360 - delta) <= 190

    def test_the_higher_count_goes_to_the_northbound_edge(self, reference):
        assert reference.share_for_edge(N_EDGE) > reference.share_for_edge(S_EDGE)

    def test_shares_reproduce_the_published_split(self, reference):
        assert reference.share_for_edge(N_EDGE) == pytest.approx(3400 / 6500)
        assert reference.share_for_edge(S_EDGE) == pytest.approx(3100 / 6500)
        assert round(reference.share_for_edge(N_EDGE), 2) == 0.52
        assert round(reference.share_for_edge(S_EDGE), 2) == 0.48

    def test_an_unknown_edge_is_refused(self, reference):
        with pytest.raises(KeyError, match="not one of this reference"):
            reference.share_for_edge("some_other_edge_0")


# ── period semantics ──────────────────────────────────────────────────────
class TestPeriodSemantics:
    def test_the_time_semantics_say_it_is_an_aggregate(self, reference):
        assert reference.time_semantics == sr.PERIOD_AGGREGATE
        assert "not_per_slot" in reference.time_semantics

    def test_it_covers_its_declared_year_only(self, reference):
        assert reference.covers("2025-01-01")
        assert reference.covers("2025-09-16")
        assert reference.covers("2025-12-31")
        assert not reference.covers("2024-12-31")
        assert not reference.covers("2026-01-01")
        assert not reference.covers("2027-05-03")

    def test_a_forecast_year_is_outside_the_anchor(self, reference):
        """2027 is Agent 1's forecast year; the 2025 anchor does not cover it."""
        assert not reference.covers("2027-09-14")

    def test_a_malformed_day_does_not_silently_match(self, reference):
        assert not reference.covers("not-a-date")


# ── fail-closed validation ────────────────────────────────────────────────
class TestFailClosedValidation:
    def base(self, **overrides):
        payload = dict(
            period_kind="calendar_year", period_year=2025,
            period_start="2025-01-01", period_end="2025-12-31",
            aggregation="annual_average_daily_two_way",
            raw_counts={"N": 3400.0, "S": 3100.0}, total=6500.0,
            bearing_to_edge={"N": N_EDGE, "S": S_EDGE},
            edge_bearing_deg={N_EDGE: 352.1, S_EDGE: 174.4},
            source="catalogue", verification={"status": "verified"},
        )
        payload.update(overrides)
        return sr.DirectionalReference(**payload)

    def test_a_valid_reference_builds(self):
        assert self.base().total == 6500.0

    def test_claiming_per_slot_semantics_is_refused(self):
        with pytest.raises(ValueError, match="never be declared as per-slot"):
            self.base(time_semantics="per_slot_measured")

    def test_counts_that_do_not_sum_to_the_total_are_refused(self):
        with pytest.raises(ValueError, match="raw counts sum to"):
            self.base(raw_counts={"N": 3400.0, "S": 3100.0}, total=7000.0)

    def test_a_negative_count_is_refused(self):
        with pytest.raises(ValueError, match="negative"):
            self.base(raw_counts={"N": -100.0, "S": 6600.0})

    def test_more_than_two_directions_is_refused(self):
        with pytest.raises(ValueError, match="exactly two directions"):
            self.base(raw_counts={"N": 2000.0, "S": 2000.0, "O": 2500.0},
                      total=6500.0)

    def test_a_bearing_map_that_disagrees_with_the_counts_is_refused(self):
        with pytest.raises(ValueError, match="must name exactly"):
            self.base(bearing_to_edge={"N": N_EDGE, "O": S_EDGE})

    def test_two_directions_on_one_edge_is_refused(self):
        with pytest.raises(ValueError, match="same edge"):
            self.base(bearing_to_edge={"N": N_EDGE, "S": N_EDGE})

    def test_an_unverified_reference_cannot_anchor(self):
        with pytest.raises(ValueError, match="must be verified"):
            self.base(verification={"status": "pending"})

    def test_a_reversed_period_is_refused(self):
        with pytest.raises(ValueError, match="ends before it starts"):
            self.base(period_start="2025-12-31", period_end="2025-01-01")

    def test_a_directional_sensor_cannot_carry_one(self):
        """A measured direction is already Level 1; there is nothing to anchor."""
        with pytest.raises(ValueError, match="nothing to anchor"):
            sr._directional_reference({"period": {}}, "1074", "directional")

    def test_a_reference_without_a_period_is_refused(self):
        with pytest.raises(ValueError, match="explicit period"):
            sr._directional_reference({"raw_counts": {}}, "107",
                                      "two_way_total")


# ── anchoring: period mean, total preservation, rounding ──────────────────
class TestAnchoring:
    def test_the_period_mean_reproduces_the_anchor(self, reference):
        est = {N_EDGE: [0.5] * 96, S_EDGE: [0.5] * 96}
        out = sr.anchored_pair_shares(reference, est)
        assert statistics.fmean(out[N_EDGE]) == pytest.approx(
            3400 / 6500, abs=sr.ANCHOR_TOLERANCE)

    def test_the_anchor_holds_from_any_starting_estimate(self, reference):
        for start in (0.30, 0.45, 0.50, 0.60, 0.75):
            est = {N_EDGE: [start] * 96, S_EDGE: [1 - start] * 96}
            out = sr.anchored_pair_shares(reference, est)
            assert statistics.fmean(out[N_EDGE]) == pytest.approx(
                3400 / 6500, abs=sr.ANCHOR_TOLERANCE)

    def test_the_pair_sums_to_one_in_every_slot(self, reference):
        est = {N_EDGE: [0.3 + 0.004 * i for i in range(96)],
               S_EDGE: [0.7 - 0.004 * i for i in range(96)]}
        out = sr.anchored_pair_shares(reference, est)
        for slot in range(96):
            assert out[N_EDGE][slot] + out[S_EDGE][slot] == pytest.approx(
                1.0, abs=1e-12)

    def test_the_measured_two_way_total_is_preserved_exactly(self, reference):
        """The property that matters downstream: build_targets multiplies the
        measured count by each share, so the two targets must re-sum to it."""
        est = {N_EDGE: [0.44 + 0.002 * i for i in range(96)],
               S_EDGE: [0.56 - 0.002 * i for i in range(96)]}
        out = sr.anchored_pair_shares(reference, est)
        measured_total = 187.0
        for slot in range(96):
            north = measured_total * out[N_EDGE][slot]
            south = measured_total * out[S_EDGE][slot]
            assert north + south == pytest.approx(measured_total, abs=1e-9)

    def test_the_partner_is_derived_not_anchored_separately(self, reference):
        """Anchoring both members independently is what loses volume.

        The lead is the alphabetically first BEARING ("N" before "S"), a
        stated property of the reference, so the partner is exactly its
        complement.
        """
        est = {N_EDGE: [0.5] * 96, S_EDGE: [0.5] * 96}
        out = sr.anchored_pair_shares(reference, est)
        assert out[S_EDGE] == [1.0 - v for v in out[N_EDGE]]

    def test_the_lead_is_chosen_by_bearing_not_by_edge_id_order(self,
                                                                reference):
        """A re-snap that renames an edge must not swap lead and partner."""
        # "N" sorts before "S", while the N edge id sorts AFTER the S edge id,
        # so these two orderings genuinely disagree here.
        assert sorted(reference.bearing_to_edge) == ["N", "S"]
        assert sorted(reference.bearing_to_edge.values())[0] == S_EDGE
        est = {N_EDGE: [0.5] * 96, S_EDGE: [0.5] * 96}
        out = sr.anchored_pair_shares(reference, est)
        # The N series is the anchored one, so it is not an exact complement
        # of a complement; the S series is derived from it.
        assert out[S_EDGE] == [1.0 - v for v in out[N_EDGE]]

    def test_time_variation_shape_is_preserved(self, reference):
        """The offset is monotone, so the ordering of slots cannot change."""
        est = {N_EDGE: [0.30 + 0.004 * i for i in range(96)],
               S_EDGE: [0.70 - 0.004 * i for i in range(96)]}
        out = sr.anchored_pair_shares(reference, est)
        assert out[N_EDGE] == sorted(out[N_EDGE])
        assert all(a < b for a, b in zip(out[N_EDGE], out[N_EDGE][1:]))

    def test_a_flat_estimate_stays_flat(self, reference):
        est = {N_EDGE: [0.5] * 96, S_EDGE: [0.5] * 96}
        out = sr.anchored_pair_shares(reference, est)
        assert len(set(round(v, 12) for v in out[N_EDGE])) == 1

    def test_every_output_stays_inside_the_unit_interval(self, reference):
        est = {N_EDGE: [0.001] * 48 + [0.999] * 48,
               S_EDGE: [0.999] * 48 + [0.001] * 48}
        out = sr.anchored_pair_shares(reference, est)
        assert all(0.0 < v < 1.0 for v in out[N_EDGE])
        assert all(0.0 < v < 1.0 for v in out[S_EDGE])

    def test_volume_weights_are_honoured(self, reference):
        """An annual average daily D-factor is volume-weighted."""
        shares = [0.4] * 48 + [0.6] * 48
        weights = [1.0] * 48 + [9.0] * 48
        out = sr.anchor_period_mean(shares, 0.5230769, weights=weights)
        weighted = sum(w * v for w, v in zip(weights, out)) / sum(weights)
        assert weighted == pytest.approx(0.5230769, abs=sr.ANCHOR_TOLERANCE)

    def test_mismatched_weight_length_is_refused(self):
        with pytest.raises(ValueError, match="does not match"):
            sr.anchor_period_mean([0.5] * 96, 0.52, weights=[1.0] * 10)

    def test_zero_weight_mass_is_refused(self):
        with pytest.raises(ValueError, match="positive mass"):
            sr.anchor_period_mean([0.5] * 4, 0.52, weights=[0.0] * 4)

    def test_an_impossible_anchor_is_refused(self):
        with pytest.raises(ValueError, match="strictly in"):
            sr.anchor_period_mean([0.5] * 4, 1.0)

    def test_an_empty_series_is_refused(self):
        with pytest.raises(ValueError, match="empty share series"):
            sr.anchor_period_mean([], 0.52)

    def test_missing_pair_edges_are_refused(self, reference):
        with pytest.raises(ValueError, match="missing the reference"):
            sr.anchored_pair_shares(reference, {N_EDGE: [0.5] * 96})


# ── the anchor is NOT 96 measurements ─────────────────────────────────────
class TestAnchorIsNotAQuarterTimeSeries:
    def test_the_registry_stores_no_per_slot_series(self):
        raw = json.loads(REGISTRY.read_text())
        record = [s for s in raw["sensors"] if s["sensor_id"] == "107"][0]
        payload = json.dumps(record["directional_reference"])
        # A 96-length array anywhere in the reference would be the failure.
        for value in record["directional_reference"].values():
            assert not (isinstance(value, list) and len(value) == 96)
        assert "0.52" not in payload, (
            "the reference must carry raw counts and a period, not a "
            "pre-divided per-slot constant")

    def test_the_record_still_says_the_split_is_estimated(self, registry):
        assert "estimated" in registry["107"].notes.lower()

    def test_the_station_semantics_are_still_two_way_total(self, registry):
        assert registry["107"].measurement_semantics == "two_way_total"
        assert registry["107"].level == "Total"

    def test_anchoring_changes_the_mean_but_keeps_96_estimated_values(
            self, reference):
        est = {N_EDGE: [0.50] * 96, S_EDGE: [0.50] * 96}
        out = sr.anchored_pair_shares(reference, est)
        assert len(out[N_EDGE]) == 96
        # Every slot moved by the SAME offset, so no slot is a measurement.
        offsets = {round(a - b, 9) for a, b in zip(out[N_EDGE], est[N_EDGE])}
        assert len(offsets) == 1


# ── the other five stations are untouched ─────────────────────────────────
class TestSingleDirectionStationsAreUnaffected:
    def test_none_of_them_carries_a_directional_reference(self, registry):
        for sensor_id in SINGLE_DIRECTION:
            assert registry.directional_reference_for(sensor_id) is None

    def test_their_semantics_and_bearings_are_unchanged(self, registry):
        expected = {"1074": "V", "1076": "S", "133": "V",
                    "134": "SO", "2276": "V"}
        for sensor_id, bearing in expected.items():
            record = registry[sensor_id]
            assert record.measurement_semantics == "directional"
            assert record.measured_bearing == bearing
            assert record.level == bearing

    def test_anchoring_leaves_their_shares_byte_identical(self):
        """Only a station WITH a reference can be touched by this path."""
        from demand.intake import apply_directional_anchors

        shares = {
            N_EDGE: [0.5] * 96, S_EDGE: [0.5] * 96,
            "60790252_60790253_0": [1.0] * 96,
            "30420757_30421744_0": [1.0] * 96,
            "26842525_26355153_0": [1.0] * 96,
            "26355153_96523321_0": [1.0] * 96,
            "26355153_91615277_0": [1.0] * 96,
        }
        before = {k: list(v) for k, v in shares.items()}
        after = apply_directional_anchors(shares, "2025-09-16", REGISTRY)
        for edge in ("60790252_60790253_0", "30420757_30421744_0",
                     "26842525_26355153_0", "26355153_96523321_0",
                     "26355153_91615277_0"):
            assert after[edge] == before[edge]
        # and 107's pair DID move
        assert after[N_EDGE] != before[N_EDGE]

    def test_a_day_outside_the_period_anchors_nothing(self):
        from demand.intake import apply_directional_anchors

        shares = {N_EDGE: [0.5] * 96, S_EDGE: [0.5] * 96}
        after = apply_directional_anchors(shares, "2027-05-03", REGISTRY)
        assert after[N_EDGE] == shares[N_EDGE]


# ── legacy path is unchanged ──────────────────────────────────────────────
class TestLegacyPathUnchanged:
    def test_load_direction_split_without_a_day_is_the_old_behaviour(self,
                                                                     monkeypatch):
        import demand.intake as intake

        calls = []

        def fake_anchor(shares, day, registry_path=None):
            calls.append(day)
            return shares

        monkeypatch.setattr(intake, "apply_directional_anchors", fake_anchor)
        monkeypatch.setattr(intake, "SUMO_DIR", Path("nonexistent-dir"))
        assert intake.load_direction_split() == {}
        assert calls == []

    def test_build_targets_signature_still_defaults_to_no_anchor(self):
        import inspect

        from demand.intake import load_direction_split

        signature = inspect.signature(load_direction_split)
        assert signature.parameters["anchor_day"].default is None

    def test_the_registry_round_trips_through_as_dict(self, registry):
        payload = registry.as_dict()
        record = [s for s in payload["sensors"] if s["sensor_id"] == "107"][0]
        assert "directional_reference" in record
        assert record["directional_reference"]["raw_counts"] == {
            "N": 3400.0, "S": 3100.0}
        for sensor_id in SINGLE_DIRECTION:
            other = [s for s in payload["sensors"]
                     if s["sensor_id"] == sensor_id][0]
            assert "directional_reference" not in other


# ── the anchor reaches the real demand path ───────────────────────────────
class TestTheAnchorIsWiredIntoProduction:
    """Review finding 3: anchor_day existed but no production caller passed it.

    build_targets called load_direction_split(split_key) with no day, so the
    52/48 anchor affected no real demand build at all.
    """

    FLOWS = {N_EDGE: [200.0] * 96, S_EDGE: [200.0] * 96}
    SENSORS = {"107": [N_EDGE, S_EDGE]}

    def test_build_targets_accepts_a_calendar_day(self):
        import inspect
        from demand.intake import build_targets

        assert "anchor_day" in inspect.signature(build_targets).parameters

    def test_every_production_call_site_passes_it(self):
        source = Path("build_sumo_demand.py").read_text()
        assert source.count("anchor_day=anchor_day") == 3
        # and none of them still calls build_targets without it
        import re
        calls = re.findall(r"build_targets\(([^)]*)\)", source, re.S)
        assert calls, "no build_targets call found"
        for call in calls:
            assert "anchor_day" in call

    def test_an_anchored_day_moves_the_targets(self, monkeypatch):
        import demand.intake as intake
        from demand.intake import build_targets

        monkeypatch.setattr(
            intake, "load_direction_split",
            lambda key="edge_shares", anchor_day=None: {
                N_EDGE: [0.5] * 96, S_EDGE: [0.5] * 96})
        plain = build_targets(self.FLOWS, self.SENSORS, 0, 4)
        anchored = build_targets(self.FLOWS, self.SENSORS, 0, 4,
                                 anchor_day="2025-09-16")
        assert plain[0][N_EDGE] == pytest.approx(100.0)
        assert anchored[0][N_EDGE] > plain[0][N_EDGE]

    def test_the_measured_total_survives_anchoring(self, monkeypatch):
        import demand.intake as intake
        from demand.intake import build_targets

        monkeypatch.setattr(
            intake, "load_direction_split",
            lambda key="edge_shares", anchor_day=None: {
                N_EDGE: [0.5] * 96, S_EDGE: [0.5] * 96})
        anchored = build_targets(self.FLOWS, self.SENSORS, 0, 8,
                                 anchor_day="2025-09-16")
        for quarter in anchored:
            assert quarter[N_EDGE] + quarter[S_EDGE] == pytest.approx(200.0)

    def test_a_day_outside_the_period_is_left_unanchored(self, monkeypatch):
        import demand.intake as intake
        from demand.intake import build_targets

        monkeypatch.setattr(
            intake, "load_direction_split",
            lambda key="edge_shares", anchor_day=None: {
                N_EDGE: [0.5] * 96, S_EDGE: [0.5] * 96})
        plain = build_targets(self.FLOWS, self.SENSORS, 0, 4)
        forecast = build_targets(self.FLOWS, self.SENSORS, 0, 4,
                                 anchor_day="2027-09-14")
        assert forecast == plain

    def test_the_refusal_for_a_forecast_day_is_reported(self):
        from demand.intake import describe_directional_anchor

        assert describe_directional_anchor("2025-09-16") == {
            "anchored_sensors": ["107"], "out_of_period_sensors": []}
        assert describe_directional_anchor("2027-09-14") == {
            "anchored_sensors": [], "out_of_period_sensors": ["107"]}


class TestTheAnchorIsVolumeWeighted:
    """A published annual average DAILY split is a volume-weighted quantity.

    Matching it against the unweighted mean of 96 slot shares lets a quiet
    03:00 slot count as much as the morning peak, which is a different
    number.
    """

    SENSORS = {"107": [N_EDGE, S_EDGE]}

    def peaky_flows(self):
        # 12 busy slots, 84 near-empty ones
        series = [500.0 if 28 <= i < 40 else 5.0 for i in range(96)]
        return {N_EDGE: list(series), S_EDGE: list(series)}

    def test_weights_come_from_the_whole_day_not_the_window(self):
        from demand.intake import day_two_way_weights

        weights = day_two_way_weights(self.peaky_flows(), self.SENSORS, 32)
        assert len(weights["107"]) == 96
        assert weights["107"][30] == pytest.approx(500.0)
        assert weights["107"][0] == pytest.approx(5.0)

    def test_a_missing_quarter_contributes_zero_weight(self):
        from demand.intake import day_two_way_weights

        flows = {N_EDGE: [None] * 96, S_EDGE: [None] * 96}
        flows[N_EDGE][10] = 100.0
        weights = day_two_way_weights(flows, self.SENSORS, 0)
        assert weights["107"][10] == pytest.approx(100.0)
        assert weights["107"][0] == pytest.approx(0.0)

    def test_single_direction_sensors_get_no_weights(self):
        from demand.intake import day_two_way_weights

        weights = day_two_way_weights(
            {"60790252_60790253_0": [10.0] * 96},
            {"1074": ["60790252_60790253_0"]}, 0)
        assert weights == {}

    def test_the_weighted_anchor_differs_from_the_unweighted_one(self,
                                                                 monkeypatch):
        """If they were the same, the weighting would be doing nothing."""
        import demand.intake as intake
        from demand.intake import apply_directional_anchors, day_two_way_weights

        shares = {N_EDGE: [0.30] * 48 + [0.70] * 48,
                  S_EDGE: [0.70] * 48 + [0.30] * 48}
        flows = {N_EDGE: [1.0] * 48 + [999.0] * 48,
                 S_EDGE: [1.0] * 48 + [999.0] * 48}
        weights = day_two_way_weights(flows, self.SENSORS, 0)
        unweighted = apply_directional_anchors(
            dict(shares), "2025-09-16", REGISTRY)
        weighted = apply_directional_anchors(
            dict(shares), "2025-09-16", REGISTRY, weights=weights)
        assert unweighted[N_EDGE] != weighted[N_EDGE]

    def test_the_weighted_mean_reproduces_the_anchor(self):
        from demand.intake import apply_directional_anchors, day_two_way_weights

        shares = {N_EDGE: [0.30] * 48 + [0.70] * 48,
                  S_EDGE: [0.70] * 48 + [0.30] * 48}
        flows = {N_EDGE: [1.0] * 48 + [999.0] * 48,
                 S_EDGE: [1.0] * 48 + [999.0] * 48}
        weights = day_two_way_weights(flows, self.SENSORS, 0)
        out = apply_directional_anchors(dict(shares), "2025-09-16", REGISTRY,
                                        weights=weights)
        total = sum(weights["107"])
        weighted_mean = sum(w * v for w, v in zip(weights["107"], out[N_EDGE])) \
            / total
        assert weighted_mean == pytest.approx(3400 / 6500,
                                              abs=sr.ANCHOR_TOLERANCE)

    def test_a_day_with_no_measurements_falls_back_without_dividing_by_zero(self):
        from demand.intake import apply_directional_anchors

        shares = {N_EDGE: [0.5] * 96, S_EDGE: [0.5] * 96}
        out = apply_directional_anchors(dict(shares), "2025-09-16", REGISTRY,
                                        weights={"107": [0.0] * 96})
        assert all(0.0 < v < 1.0 for v in out[N_EDGE])
