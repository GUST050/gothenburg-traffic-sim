"""Fas 1 of the direction plan: keep the variation, then let the simplest
model that survives leakage-free validation win.

Two failures are guarded here.  The first is silent aggregation: the deployed
quantiles were learned from station-hour MEANS, so they describe spread between
aggregated cells rather than the day-to-day variation a future day has — the v2
table has to keep raw counts, dates and day blocks instead.  The second is a
tournament that flatters a model: folds must refit everything inside the
training fold, uncertainty must be bootstrapped over independent groups, and
Gate M must be able to answer BASELINE or INCONCLUSIVE.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dirsplit import benchmark
from dirsplit.dataset import (MISSING_LOW_COVERAGE, MISSING_LOW_VOLUME,
                              MISSING_NO_OPPOSITE, directional_profile_features,
                              hourly_shares, profile_features, raw_observations)


def _volume_rows(*, days=("2025-09-01", "2025-09-02"), hours=range(6, 10),
                 north=60.0, south=40.0, coverage="100"):
    rows = []
    for day in days:
        for hour in hours:
            stamp = f"{day}T{hour:02d}:00:00+02:00"
            rows.append({"from": stamp, "heading": "N", "volume": str(north),
                         "coverage_pct": coverage})
            rows.append({"from": stamp, "heading": "S", "volume": str(south),
                         "coverage_pct": coverage})
    return rows


class TestRawObservationsKeepWhatAggregationDestroys:
    def test_one_row_per_station_date_hour_heading(self):
        rows = raw_observations(_volume_rows(), "N", city="oslo", station_id="A")
        assert len(rows) == 2 * 4
        assert {row["local_date"] for row in rows} == {"2025-09-01", "2025-09-02"}

    def test_raw_counts_and_the_hour_total_survive(self):
        row = raw_observations(_volume_rows(), "N", city="oslo", station_id="A")[0]
        assert row["toward_count"] == 60.0
        assert row["away_count"] == 40.0
        assert row["total_count"] == 100.0
        assert row["share"] == pytest.approx(0.6)

    def test_a_day_block_groups_simultaneous_observations(self):
        a = raw_observations(_volume_rows(), "N", city="oslo", station_id="A")
        b = raw_observations(_volume_rows(), "N", city="oslo", station_id="B")
        assert a[0]["day_block_id"] == b[0]["day_block_id"] == "oslo:2025-09-01"
        assert a[0]["station_day_id"] != b[0]["station_day_id"]

    def test_calendar_context_is_recorded(self):
        rows = raw_observations(
            _volume_rows(days=("2025-09-06",)), "N", city="oslo", station_id="A")
        assert rows[0]["is_weekend"] == 1        # 2025-09-06 is a Saturday
        assert rows[0]["month"] == 9
        assert rows[0]["weekday"] == 5

    def test_unusable_hours_are_kept_with_an_explicit_reason(self):
        rows = raw_observations(_volume_rows(coverage="10"), "N", city="oslo",
                                station_id="A")
        assert all(row["included"] == 0 for row in rows)
        assert {row["missing_reason"] for row in rows} == {MISSING_LOW_COVERAGE}

    def test_a_missing_opposite_heading_is_named_not_dropped(self):
        rows = [row for row in _volume_rows() if row["heading"] == "N"]
        observations = raw_observations(rows, "N", city="oslo", station_id="A")
        assert observations
        assert {row["missing_reason"] for row in observations} == {
            MISSING_NO_OPPOSITE}

    def test_a_tiny_hourly_volume_is_declared_noise_not_deleted(self):
        rows = raw_observations(_volume_rows(north=3, south=2), "N", city="oslo",
                                station_id="A")
        assert {row["missing_reason"] for row in rows} == {MISSING_LOW_VOLUME}
        assert all(row["included"] == 0 for row in rows)


class TestFeatureSemanticsStaySeparable:
    def test_total_and_directional_profiles_are_different_quantities(self):
        rows = _volume_rows(hours=range(6, 20), north=80, south=20)
        total = profile_features(rows)
        directional = directional_profile_features(rows, "N")
        assert total is not None and directional is not None
        # Same statistic names, different inputs: they may only ever be stored
        # under different column names.
        assert set(total) == set(directional)

    def test_a_single_direction_station_still_gets_directional_features(self):
        rows = [row for row in _volume_rows(hours=range(6, 20))
                if row["heading"] == "N"]
        assert directional_profile_features(rows, "N") is not None
        assert directional_profile_features(rows, "S") is None

    def test_the_legacy_aggregate_still_behaves_as_before(self):
        aggregated = hourly_shares(_volume_rows(hours=range(6, 10)), "N")
        assert len(aggregated) == 4
        assert aggregated[0]["share"] == pytest.approx(0.6)
        assert aggregated[0]["n_obs"] == 2


def _observation(**kwargs):
    defaults = dict(station_id="A", city="oslo", heading="N", hour=8,
                    is_weekend=0, share=0.55, weight=100.0,
                    features={"radial_cos": 1.0}, day_block_id="oslo:2025-09-01",
                    toward_count=55.0, total_count=100.0)
    defaults.update(kwargs)
    return benchmark.Observation(**defaults)


class TestPopulationAndOrientation:
    def test_mirrored_duplicate_directions_are_collapsed(self):
        toward = _observation(features={"radial_cos": 0.6})
        away = _observation(heading="S", share=0.45,
                            features={"radial_cos": -0.6})
        assert benchmark.orient_toward_centre([toward, away]) == [toward]

    def test_effectively_one_way_stations_leave_the_population(self):
        two_way = [_observation(station_id="A", hour=hour) for hour in range(7, 12)]
        one_way = [_observation(station_id="B", hour=hour, share=0.97)
                   for hour in range(7, 12)]
        kept, dropped = benchmark.screen_two_way_stations(two_way + one_way)
        assert {obs.station_id for obs in kept} == {"A"}
        assert dropped == ["B:N"]

    def test_the_screen_uses_weekday_daytime_hours_only(self):
        night = [_observation(station_id="C", hour=hour, share=0.97)
                 for hour in (2, 3, 4)]
        kept, dropped = benchmark.screen_two_way_stations(night)
        assert dropped == []                    # no evidence in the screen band
        assert len(kept) == 3


class TestFoldsAreBlockedAndDisjoint:
    def _population(self):
        return [
            _observation(station_id="A", city="oslo", day_block_id="oslo:2025-09-01"),
            _observation(station_id="B", city="oslo", day_block_id="oslo:2025-09-02"),
            _observation(station_id="C", city="bergen", day_block_id="bergen:2025-09-01"),
            _observation(station_id="D", city="bergen", day_block_id="bergen:2025-09-03"),
        ]

    def test_leave_city_out_holds_out_a_whole_city(self):
        population = self._population()
        folds = benchmark.leave_city_out(population)
        assert len(folds) == 2
        for name, train, test in folds:
            assert not set(train) & set(test)
            assert len({population[i].city for i in test}) == 1

    def test_leave_station_out_holds_out_a_whole_station(self):
        population = self._population()
        for _name, train, test in benchmark.leave_station_out(population):
            held = {population[i].station_id for i in test}
            assert not held & {population[i].station_id for i in train}

    def test_blocked_date_keeps_a_day_whole_across_stations(self):
        population = [
            _observation(station_id=station, city="oslo",
                         day_block_id=f"oslo:2025-09-0{day}")
            for day in range(1, 7) for station in ("A", "B")]
        folds = benchmark.blocked_date(population, n_blocks=3)
        assert folds
        for _name, train, test in folds:
            held_dates = {population[i].day_block_id for i in test}
            assert not held_dates & {population[i].day_block_id for i in train}

    def test_without_dates_there_is_no_blocked_date_fold(self):
        population = [_observation(day_block_id=None) for _ in range(10)]
        assert benchmark.blocked_date(population) == []


class TestModelsFitOnlyOnTheTrainingFold:
    def test_a_model_never_sees_the_held_out_rows(self):
        seen: list[int] = []

        class Spy(benchmark.Model):
            name = "spy"

            def fit(self, train):
                seen.append(len(train))
                self.value = float(np.mean([obs.share for obs in train]))
                return self

            def predict(self, test):
                return np.full(len(test), self.value)

        population = [_observation(station_id=str(index), share=0.5 + index / 100,
                                   city="oslo" if index < 5 else "bergen")
                      for index in range(10)]
        folds = benchmark.leave_city_out(population)
        result = benchmark.run_family("leave_city_out", folds, population, [Spy()],
                                      factory=lambda template: Spy())
        assert seen == [5, 5]
        assert result["models"]["spy"]["pooled"]["n"] == 10

    def test_the_shrunk_dfactor_learns_only_from_its_training_rows(self):
        train = [_observation(hour=8, share=0.7) for _ in range(50)]
        model = benchmark.ShrunkDFactor(prior_strength=1.0).fit(train)
        assert model.predict([_observation(hour=8)])[0] > 0.6
        # An hour with no training evidence falls back to the pooled deviation,
        # never to a memorised test value.
        assert model.predict([_observation(hour=3)])[0] == pytest.approx(
            0.5 + model.global_deviation)

    def test_the_count_model_recovers_a_known_share(self):
        pytest.importorskip("scipy")
        train = [_observation(share=0.62, toward_count=620.0, total_count=1000.0,
                              station_id=str(index), hour=8 + index % 4)
                 for index in range(40)]
        model = benchmark.BetaBinomial(["radial_cos"]).fit(train)
        assert model.predict([_observation(hour=8)])[0] == pytest.approx(0.62,
                                                                        abs=0.05)


class TestUncertaintyIsBootstrappedOverIndependentGroups:
    def test_the_group_is_the_day_block_when_dates_exist(self):
        assert benchmark.group_key(_observation()) == "oslo:2025-09-01"

    def test_the_group_falls_back_to_the_station_direction(self):
        assert benchmark.group_key(_observation(day_block_id=None)) == "A:N"

    def test_paired_differences_are_per_group(self):
        population = [_observation(day_block_id="d1", share=0.6),
                      _observation(day_block_id="d2", share=0.4)]
        differences = benchmark.paired_group_differences(population,
                                                         np.array([0.6, 0.6]))
        assert differences["d1"] == pytest.approx(-0.1)
        assert differences["d2"] == pytest.approx(0.1)

    def test_a_single_group_cannot_produce_an_interval(self):
        assert benchmark.bootstrap_interval([0.1]) is None

    def test_the_interval_brackets_a_consistent_improvement(self):
        low, high = benchmark.bootstrap_interval([-0.02] * 20)
        assert low == pytest.approx(-0.02) and high == pytest.approx(-0.02)

    def test_unsupported_hours_are_scored_separately(self):
        population = [_observation(hour=8), _observation(hour=3)]
        result = benchmark.score(population, np.array([0.5, 0.5]))
        assert result["n_unsupported_hours"] == 1
        assert result["mae_supported_hours"] is not None


class TestGateM:
    def _family(self, name, improvement, ci, *, model="lightgbm_similarity",
                by_city=None):
        return {
            "family": name, "n_folds": 2,
            "models": {model: {"folds": [],
                               "pooled": {"improvement_pct": improvement,
                                          "bootstrap_ci": ci, "mae": 0.05,
                                          "baseline_mae": 0.06},
                               "by_city": by_city or {}}},
        }

    def test_no_improvement_is_a_complete_baseline_verdict(self):
        families = {"leave_city_out": self._family("leave_city_out", 0.4,
                                                   [-0.001, 0.002]),
                    "blocked_date": self._family("blocked_date", 1.0,
                                                 [-0.002, 0.003])}
        gate = benchmark.classify_gate_m(families, day_blocks_available=True,
                                         n_independent_groups=40)
        assert gate["verdict"] == benchmark.GATE_BASELINE
        assert gate["winners"] == []

    def test_a_robust_material_win_in_every_family_is_a_model_verdict(self):
        families = {"leave_city_out": self._family("leave_city_out", 12.0,
                                                   [-0.02, -0.01]),
                    "blocked_date": self._family("blocked_date", 9.0,
                                                 [-0.015, -0.004])}
        gate = benchmark.classify_gate_m(families, day_blocks_available=True,
                                         n_independent_groups=40)
        assert gate["verdict"] == benchmark.GATE_MODEL
        assert gate["winners"] == ["lightgbm_similarity"]

    def test_an_interval_touching_zero_is_not_robust(self):
        families = {"leave_city_out": self._family("leave_city_out", 12.0,
                                                   [-0.02, 0.001])}
        gate = benchmark.classify_gate_m(families, day_blocks_available=True,
                                         n_independent_groups=40)
        assert gate["verdict"] == benchmark.GATE_BASELINE

    def test_material_harm_in_a_main_group_blocks_a_win(self):
        families = {"leave_city_out": self._family(
            "leave_city_out", 12.0, [-0.02, -0.01],
            by_city={"bergen": {"improvement_pct": -20.0}})}
        gate = benchmark.classify_gate_m(families, day_blocks_available=True,
                                         n_independent_groups=40)
        assert gate["verdict"] == benchmark.GATE_BASELINE
        assert gate["per_model"]["lightgbm_similarity"]["material_harm_groups"]

    def test_missing_day_blocks_can_never_read_as_baseline(self):
        families = {"leave_city_out": self._family("leave_city_out", 0.1,
                                                   [-0.001, 0.001])}
        gate = benchmark.classify_gate_m(families, day_blocks_available=False,
                                         n_independent_groups=40)
        assert gate["verdict"] == benchmark.GATE_INCONCLUSIVE
        assert any("day block" in reason for reason in gate["reasons"])

    def test_too_few_independent_groups_is_inconclusive(self):
        families = {"leave_city_out": self._family("leave_city_out", 20.0,
                                                   [-0.03, -0.02])}
        gate = benchmark.classify_gate_m(families, day_blocks_available=True,
                                         n_independent_groups=3)
        assert gate["verdict"] == benchmark.GATE_INCONCLUSIVE

    def test_the_frozen_rule_is_recorded_with_the_verdict(self):
        gate = benchmark.classify_gate_m({}, day_blocks_available=True,
                                         n_independent_groups=40)
        assert gate["rule"]["min_improvement_pct"] == benchmark.MIN_IMPROVEMENT_PCT
        assert gate["rule"]["bootstrap_seed"] == benchmark.BOOTSTRAP_SEED


class TestObservabilityV2:
    """Evidence has several dimensions, and none of them may ban a road."""

    def _registry_record(self, sensor_id):
        payload = json.loads(Path("data_in/sensors.json").read_text())
        return next(row for row in payload["sensors"]
                    if row["sensor_id"] == sensor_id)

    def test_a_two_way_station_reports_an_estimated_split(self):
        from dirsplit import coverage
        level = coverage.measurement_level(self._registry_record("107"),
                                           "60786979_3575001205_0")
        assert level["level"] == "two_way_total_split_estimated"
        assert level["period_anchor"]["period"] == "2025"

    def test_a_measured_carriageway_is_distinguished_from_its_opposite(self):
        from dirsplit import coverage
        record = self._registry_record("1076")
        measured = coverage.measurement_level(record, "30420757_30421744_0")
        opposite = coverage.measurement_level(record, "559960490_1305379743_0")
        assert measured["level"] == "direction_measured"
        assert opposite["level"] == "opposite_carriageway_estimated"

    def test_off_hours_and_weekends_are_declared_extrapolation(self):
        from dirsplit import coverage
        support = coverage.temporal_support(Path("does-not-exist.json"))
        assert support["trained_day_types"] == ["weekday"]
        assert support["trained_hours"] == [6, 20]
        assert "weekend" in support["detail"]

    def test_the_interval_is_never_called_calibrated(self):
        from dirsplit import coverage
        assert coverage.calibration_support()["interval_calibration"] == "unmeasured"

    def test_low_evidence_never_excludes_a_road(self):
        from dirsplit import coverage
        profile = coverage.evidence_profile("60790252_60790253_0", "1074",
                                            static_status="EXTRAPOLATION")
        assert profile["evidence"] == coverage.EVIDENCE_EXTRAPOLATION
        assert profile["excludes_road"] is False
        assert "inconclusive" in profile["usage"]

    def test_every_dimension_is_reported_separately(self):
        from dirsplit import coverage
        profile = coverage.evidence_profile("60786979_3575001205_0", "107",
                                            static_status="OK")
        assert set(profile["dimensions"]) == {
            "measurement_level", "static_domain", "temporal_support",
            "feature_compatibility", "effective_sample_size",
            "calibration_support", "local_crosscheck"}

    def test_the_local_crosscheck_states_its_period_semantics(self):
        from dirsplit import coverage
        crosscheck = coverage.local_crosscheck(self._registry_record("107"))
        assert crosscheck["available"] is True
        assert crosscheck["time_semantics"] == "period_aggregate"
        assert crosscheck["shares"]["60786979_3575001205_0"] == pytest.approx(
            3400 / 6500, abs=1e-4)

    def test_a_station_without_local_evidence_says_so(self):
        from dirsplit import coverage
        assert coverage.local_crosscheck(
            self._registry_record("133"))["available"] is False

    def test_the_committed_report_carries_the_profile(self):
        report = json.loads(Path("data/dirsplit/coverage_report.json").read_text())
        observability = report["observability"]
        assert observability["schema_version"] == "dirsplit_observability_v2"
        assert all(edge["excludes_road"] is False
                   for edge in observability["edges"])


class TestTheCommittedBenchmarkArtifact:
    ARTIFACT = Path("validation/dirsplit_point_benchmark_v1.json")

    def test_it_exists_and_is_diagnostic(self):
        report = json.loads(self.ARTIFACT.read_text())
        assert report["schema_version"] == benchmark.SCHEMA_VERSION
        assert report["release_evidence"] is False

    def test_it_states_which_table_it_measured(self):
        report = json.loads(self.ARTIFACT.read_text())
        assert report["table"] in {"v2", "legacy"}
        assert report["population"]["definition"]

    def test_a_legacy_run_cannot_decide_gate_m(self):
        report = json.loads(self.ARTIFACT.read_text())
        if report["table"] == "legacy":
            assert report["gate_m"]["verdict"] == benchmark.GATE_INCONCLUSIVE
