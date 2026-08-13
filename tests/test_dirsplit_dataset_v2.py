"""Step 1 acceptance: raw counts, real day types, blocks and missingness.

The plan's acceptance criteria for step 1 are:
  * raw counts and ``day_block_id`` survive to training;
  * no test dates influence fitted transformations;
  * weekend/off-hours are either genuinely supported or explicitly
    unsupported, with no silent weekday extrapolation left.

These tests use synthetic observations rather than the fetched Norwegian
volumes, which are gitignored and re-fetchable, so they run on a clean
checkout.
"""

from __future__ import annotations

import pytest

from dirsplit import dataset_schema as ds


def row(date="2025-09-01", hour=8, toward=300.0, away=200.0,
        coverage=100.0, station="S1", city="oslo", heading="Sentrum",
        **kwargs):
    return ds.make_row(
        station_id=station, city=city, heading=heading,
        timestamp=f"{date}T{hour:02d}:00:00",
        toward_count=toward, away_count=away, coverage_pct=coverage,
        **kwargs,
    )


class TestRawCountsSurvive:
    def test_counts_are_kept_not_only_the_share(self):
        r = row(toward=300, away=200)
        assert r.toward_count == 300
        assert r.away_count == 200
        assert r.total_count == 500
        assert r.share == pytest.approx(0.6)

    def test_a_thin_and_a_thick_observation_are_distinguishable(self):
        """3/5 and 300/500 have the same share but not the same evidence."""
        thin = row(toward=3, away=2)
        thick = row(toward=300, away=200)
        assert thin.share == thick.share
        assert thin.total_count != thick.total_count
        # and the thin one is marked unusable by the volume floor
        assert not thin.is_usable
        assert thin.missingness == ds.MissingnessReason.LOW_VOLUME
        assert thick.is_usable

    def test_share_is_none_when_nothing_was_counted(self):
        r = row(toward=0, away=0)
        assert r.share is None
        assert r.missingness == ds.MissingnessReason.ZERO_TOTAL


class TestDayTypeIsRealNotConstant:
    def test_weekend_is_derived_from_the_actual_date(self):
        # 2025-09-06 is a Saturday, 2025-09-01 a Monday.
        assert row(date="2025-09-06").is_weekend == 1
        assert row(date="2025-09-01").is_weekend == 0

    def test_weekday_field_must_agree_with_the_date(self):
        with pytest.raises(ds.DatasetError, match="disagrees"):
            ds.DirectionRow(
                station_id="S1", city="oslo", heading="H",
                local_date="2025-09-01", hour=8,
                weekday=5,                      # Monday is 0, not 5
                is_weekend=0, month=9,
                toward_count=300, away_count=200, total_count=500,
                share=0.6, coverage_pct=100.0,
                missingness="ok", time_resolution="hour",
                day_block_id=ds.day_block_id("oslo", "2025-09-01"),
                station_day_id=ds.station_day_id("S1", "2025-09-01"),
            )

    def test_is_weekend_must_agree_with_the_date(self):
        with pytest.raises(ds.DatasetError, match="is_weekend"):
            ds.DirectionRow(
                station_id="S1", city="oslo", heading="H",
                local_date="2025-09-01", hour=8, weekday=0,
                is_weekend=1,                   # Monday is not a weekend
                month=9, toward_count=300, away_count=200, total_count=500,
                share=0.6, coverage_pct=100.0, missingness="ok",
                time_resolution="hour",
                day_block_id=ds.day_block_id("oslo", "2025-09-01"),
                station_day_id=ds.station_day_id("S1", "2025-09-01"),
            )

    def test_month_must_agree_with_the_date(self):
        with pytest.raises(ds.DatasetError, match="month"):
            ds.DirectionRow(
                station_id="S1", city="oslo", heading="H",
                local_date="2025-09-01", hour=8, weekday=0, is_weekend=0,
                month=3, toward_count=300, away_count=200, total_count=500,
                share=0.6, coverage_pct=100.0, missingness="ok",
                time_resolution="hour",
                day_block_id=ds.day_block_id("oslo", "2025-09-01"),
                station_day_id=ds.station_day_id("S1", "2025-09-01"),
            )


class TestBlockKeys:
    def test_day_block_holds_a_city_date_together(self):
        a = row(station="S1", city="oslo", date="2025-09-01")
        b = row(station="S2", city="oslo", date="2025-09-01")
        c = row(station="S1", city="oslo", date="2025-09-02")
        assert a.day_block_id == b.day_block_id
        assert a.day_block_id != c.day_block_id

    def test_station_day_separates_stations_on_the_same_date(self):
        a = row(station="S1", date="2025-09-01")
        b = row(station="S2", date="2025-09-01")
        assert a.station_day_id != b.station_day_id

    def test_block_keys_must_match_their_components(self):
        with pytest.raises(ds.DatasetError, match="day_block_id"):
            ds.DirectionRow(
                station_id="S1", city="oslo", heading="H",
                local_date="2025-09-01", hour=8, weekday=0, is_weekend=0,
                month=9, toward_count=300, away_count=200, total_count=500,
                share=0.6, coverage_pct=100.0, missingness="ok",
                time_resolution="hour",
                day_block_id="wrong:key",
                station_day_id=ds.station_day_id("S1", "2025-09-01"),
            )


class TestMissingnessIsExplicitNotSilent:
    def test_low_coverage_is_kept_with_a_reason(self):
        r = row(coverage=42.0)
        assert not r.is_usable
        assert r.missingness == ds.MissingnessReason.LOW_COVERAGE

    def test_missing_direction_is_its_own_reason(self):
        r = row(heading_present=False)
        assert r.missingness == ds.MissingnessReason.MISSING_DIRECTION

    def test_classification_order_is_specific(self):
        # A missing direction outranks a low volume; a zero total outranks
        # low coverage. The first applicable reason is the specific one.
        assert ds.classify(heading_present=False, total_count=5,
                           coverage_pct=10) == "missing_direction"
        assert ds.classify(heading_present=True, total_count=0,
                           coverage_pct=10) == "zero_total"
        assert ds.classify(heading_present=True, total_count=10,
                           coverage_pct=10) == "low_coverage"
        assert ds.classify(heading_present=True, total_count=10,
                           coverage_pct=100) == "low_volume"
        assert ds.classify(heading_present=True, total_count=100,
                           coverage_pct=100) == "ok"

    def test_excluded_rows_are_counted_in_the_summary(self):
        rows = [row(), row(coverage=10.0), row(toward=1, away=1),
                row(heading_present=False)]
        report = ds.summarise(rows)
        assert report["n_rows"] == 4
        assert report["n_usable"] == 1
        assert report["by_missingness"]["low_coverage"] == 1
        assert report["by_missingness"]["missing_direction"] == 1


class TestTemporalSupportIsStated:
    def test_observed_hours_are_reported_per_day_type(self):
        rows = [row(date="2025-09-01", hour=h) for h in range(6, 21)]
        support = ds.temporal_support(rows)
        assert support["weekday_hours"] == list(range(6, 21))
        assert support["weekend_hours"] == []

    def test_unsupported_hours_are_named_rather_than_extrapolated(self):
        """The v1 defect, made visible.

        v1 trained weekdays 06-20 and predicted all 24 hours with
        is_weekend=0. Here the unsupported set is explicit, so step 3 can
        grade those cells as extrapolation instead of silently guessing.
        """
        rows = [row(date="2025-09-01", hour=h) for h in range(6, 21)]
        support = ds.temporal_support(rows)
        assert support["unsupported_weekday_hours"] == [
            0, 1, 2, 3, 4, 5, 21, 22, 23
        ]
        assert support["unsupported_weekend_hours"] == list(range(24))

    def test_unusable_rows_do_not_create_false_support(self):
        rows = [row(date="2025-09-01", hour=3, coverage=10.0)]
        support = ds.temporal_support(rows)
        assert 3 not in support["weekday_hours"]


class TestV2IsAStrictSupersetOfV1:
    def test_the_v1_aggregate_is_recoverable(self):
        rows = [
            row(date="2025-09-01", hour=8, toward=300, away=200),
            row(date="2025-09-02", hour=8, toward=400, away=200),
            row(date="2025-09-03", hour=8, toward=200, away=200),
        ]
        agg = ds.aggregate_like_v1(rows)
        assert len(agg) == 1
        cell = agg[0]
        assert cell["n_obs"] == 3
        # v1 stored the mean of the per-day shares
        expected = (0.6 + (400 / 600) + 0.5) / 3
        assert cell["share"] == pytest.approx(round(expected, 4))

    def test_the_daily_variation_v1_destroyed_is_still_present(self):
        rows = [
            row(date="2025-09-01", hour=8, toward=300, away=200),
            row(date="2025-09-02", hour=8, toward=400, away=200),
            row(date="2025-09-03", hour=8, toward=200, away=200),
        ]
        agg = ds.aggregate_like_v1(rows)
        assert len(agg) == 1                       # v1 sees one number
        shares = sorted(r.share for r in rows)     # v2 keeps three
        assert len(set(shares)) == 3
        assert max(shares) - min(shares) == pytest.approx(1 / 6, abs=1e-9)

    def test_aggregation_reproduces_v1_volume_floor_order(self):
        # v1 averaged first, then dropped the cell on the MEAN total.
        # Two days of 20 veh/h and one of 100 average to 46.7 -> kept,
        # even though two of the three days are individually below 30.
        rows = [
            row(date="2025-09-01", hour=8, toward=12, away=8),
            row(date="2025-09-02", hour=8, toward=12, away=8),
            row(date="2025-09-03", hour=8, toward=60, away=40),
        ]
        agg = ds.aggregate_like_v1(rows)
        assert len(agg) == 1
        assert agg[0]["mean_total_veh_h"] == pytest.approx(46.7, abs=0.1)

    def test_low_coverage_rows_are_excluded_from_the_aggregate(self):
        rows = [row(date="2025-09-01", hour=8, coverage=10.0)]
        assert ds.aggregate_like_v1(rows) == []


class TestTimeResolutionIsRecorded:
    def test_hourly_source_is_labelled_hourly(self):
        assert row().time_resolution == "hour"

    def test_an_unknown_resolution_is_refused(self):
        with pytest.raises(ds.DatasetError, match="time_resolution"):
            row(time_resolution="minute")

    def test_summary_reports_the_resolutions_present(self):
        assert ds.summarise([row()])["time_resolutions"] == ["hour"]
