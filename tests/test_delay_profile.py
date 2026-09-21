"""delay_profile: the distribution behind the ranked closure cost.

The curve exists to show what `added_vehicle_hours` cannot — whether a
closure's cost is spread thin over many drivers or concentrated on a few.
That is only worth drawing if it describes the SAME vehicles the ranking
scored, so most of these tests are about agreement with the production
costing rather than about the histogram's own arithmetic.
"""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.analysis import delay_profile
from traffic_sim.analysis.delay_profile import (
    BINS,
    OVERFLOW_FROM_S,
    ZERO_BIN_INDEX,
    DelayProfileError,
    bin_index,
    delay_histogram,
    rank_periods,
    sum_histograms,
    verify_against_cost,
)
from traffic_sim.simulation import disruption as disruption_module
from traffic_sim.simulation.deterministic_disruption import sum_daily_disruption


# A -> B -> C -> D on a straight chain, plus a parallel bypass A -> P -> D.
# The chain costs 40 s; the bypass costs 80 s, so closing C adds exactly 40 s.
ADJ = {"A": ["B", "P"], "B": ["C"], "C": ["D"], "P": ["D"], "D": []}
TIME = {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0, "P": 60.0}
LEN = {"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0, "P": 900.0}


def _routes(tmp_path, vehicles, name="calibrated.rou.xml"):
    body = "".join(
        f'<vehicle id="{vid}" depart="{depart}">'
        f'<route edges="{" ".join(edges)}"/></vehicle>'
        for vid, depart, edges in vehicles)
    path = tmp_path / name
    path.write_text(f"<routes>{body}</routes>")
    return path


def _histogram(path, closed, closures=()):
    return delay_histogram(path, set(closed), list(closures), TIME, LEN,
                           adjacency=ADJ)


class TestBinPlacement:
    def test_zero_added_seconds_is_its_own_bucket(self):
        assert bin_index(0.0) == ZERO_BIN_INDEX

    def test_buckets_are_upper_inclusive(self):
        """15 s belongs to "0–15 s". An exclusive upper edge would put every
        round quarter-minute — the most common value on a 15 s grid — into the
        next bar, biasing the whole curve one bucket to the right."""
        assert bin_index(0.5) == 1
        assert bin_index(15.0) == 1
        assert bin_index(15.5) == 2
        assert bin_index(30.0) == 2

    def test_the_last_bar_is_an_overflow(self):
        assert bin_index(OVERFLOW_FROM_S) == len(BINS) - 2
        assert bin_index(OVERFLOW_FROM_S + 0.5) == len(BINS) - 1
        assert bin_index(86_400.0) == len(BINS) - 1

    def test_every_bucket_is_reachable_and_ordered(self):
        seen = {bin_index(value) for value in
                (0.0, 1.0, 16.0, 299.0, 300.0, 301.0)}
        assert seen <= set(range(len(BINS)))
        edges = [item["from_s"] for item in BINS[1:]]
        assert edges == sorted(edges)


class TestHistogramAgreesWithTheRankedCost:
    def test_the_detour_lands_in_the_bucket_its_seconds_name(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        histogram = _histogram(path, ["C"])
        # 80 s detour against a 40 s baseline: 40 s added -> "30-45 s".
        assert histogram.vehicles[bin_index(40.0)] == 1
        assert histogram.vehicles_in_bins == 1

    def test_multiplicity_is_carried_per_vehicle_not_per_od_pair(self, tmp_path):
        """Three vehicles sharing one origin-destination pair are three
        delayed drivers, not one. The pair is priced once; the bar counts
        three."""
        path = _routes(tmp_path, [
            (f"v{index}", 0.0, ["A", "B", "C", "D"]) for index in range(3)])
        histogram = _histogram(path, ["C"])
        assert histogram.vehicles[bin_index(40.0)] == 3

    def test_a_free_parallel_street_is_counted_as_zero_not_dropped(self, tmp_path):
        """The documented case behind this whole chart: an affected vehicle
        whose optimal detour costs nothing. It must appear in the zero bucket,
        because "affected but unharmed" is a finding, not an absence."""
        cheap_adjacency = {"A": ["B", "P"], "B": ["C"], "C": ["D"],
                           "P": ["D"], "D": []}
        cheap_time = dict(TIME, P=20.0)      # bypass now equals the chain
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        histogram = delay_histogram(
            path, {"C"}, [], cheap_time, LEN, adjacency=cheap_adjacency)
        assert histogram.report["vehicles_affected"] == 1
        assert histogram.vehicles[ZERO_BIN_INDEX] == 1

    def test_the_aggregate_is_the_production_report_byte_for_byte(self, tmp_path):
        """The histogram and the ranked total are priced from ONE set of
        shortest paths. If this ever diverges, the chart is describing a
        different computation than the number printed beside it."""
        path = _routes(tmp_path, [
            ("v0", 0.0, ["A", "B", "C", "D"]),
            ("v1", 0.0, ["A", "B", "C", "D"]),
            ("v2", 0.0, ["P", "D"]),
        ])
        # `run_scenario.closure_disruption` is a thin wrapper around this
        # same function; calling the module directly keeps this test
        # runnable without the scientific stack run_scenario imports.
        produced = disruption_module.closure_disruption(
            path, {"C"}, [], TIME, LEN, adjacency=ADJ)
        assert _histogram(path, ["C"]).report == produced

    def test_every_affected_vehicle_is_accounted_for(self, tmp_path):
        """No silent third category: the bars, the no-detour vehicles and the
        unpriced ones must add up to `vehicles_affected` exactly."""
        path = _routes(tmp_path, [
            ("v0", 0.0, ["A", "B", "C", "D"]),
            ("stranded", 0.0, ["C", "D"]),      # starts on the closed edge
        ])
        accounting = _histogram(path, ["C"]).accounting()
        assert accounting["vehicles_affected"] == (
            accounting["vehicles_in_bins"]
            + accounting["vehicles_no_detour"]
            + accounting["vehicles_unpriced"])
        assert accounting["vehicles_no_detour"] == 1

    def test_a_severed_destination_is_never_drawn_as_a_delay(self, tmp_path):
        """A driver who can no longer reach their destination is not "delayed
        by N seconds"; `closure_ranking` disqualifies on it. It must stay out
        of every bar, including the overflow one."""
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        histogram = delay_histogram(
            path, {"C", "P"}, [], TIME, LEN, adjacency=ADJ)
        assert histogram.vehicles_in_bins == 0
        assert histogram.report["vehicles_no_detour"] == 1


class TestSummingDays:
    def _summed(self, parts):
        return sum_histograms(parts)

    def test_bins_and_totals_add_across_daily_units(self, tmp_path):
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        one = _histogram(path, ["C"])
        summed = self._summed([one, one, one])
        assert summed["vehicles"][bin_index(40.0)] == 3
        assert summed["vehicles_affected"] == 3
        assert summed["daily_unit_count"] == 3

    def test_totals_round_exactly_as_the_production_daily_sum(self, tmp_path):
        """`sum_daily_disruption` is what the search itself uses to turn daily
        units into a parent cost. A different rounding here would make the
        verification below fail on correct data."""
        path = _routes(tmp_path, [("v", 0.0, ["A", "B", "C", "D"])])
        record = dict(_histogram(path, ["C"]).report)
        daily = [[dict(record, demand_variant=name)
                  for name in ("q10", "q50", "q90")] for _ in range(3)]
        production = {item["demand_variant"]: item
                      for item in sum_daily_disruption(daily)}
        mine = self._summed([_histogram(path, ["C"])] * 3)
        for field in ("vehicles_affected", "vehicles_considered",
                      "vehicles_no_detour", "added_vehicle_hours",
                      "added_metres_total"):
            assert mine[field] == production["q50"][field], field

    def test_a_schedule_with_no_days_is_refused(self):
        with pytest.raises(DelayProfileError):
            sum_histograms([])


class TestVerificationAgainstThePublishedCost:
    def _variant(self, hours, metres, affected):
        return {
            "vehicles": [0] * len(BINS),
            "added_vehicle_hours": hours,
            "added_metres_total": metres,
            "vehicles_affected": affected,
            "vehicles_no_detour": 0,
        }

    def test_a_matching_recomputation_passes(self):
        summed = {
            "q10": self._variant(1.0, 100.0, 10),
            "q50": self._variant(2.0, 300.0, 20),
            "q90": self._variant(1.5, 200.0, 15),
        }
        published = {"added_vehicle_hours": 2.0, "added_metres_total": 300.0,
                     "vehicles_affected": 20}
        assert verify_against_cost(
            summed, published, candidate_id="c")["matches"] is True

    def test_a_drifted_archive_is_refused_rather_than_drawn(self):
        """If the recomputation lands anywhere else, the archives, network or
        result have moved apart since the search. Drawing anyway would be a
        confident picture of a different run."""
        summed = {
            "q10": self._variant(1.0, 100.0, 10),
            "q50": self._variant(2.0, 300.0, 20),
            "q90": self._variant(1.5, 200.0, 15),
        }
        published = {"added_vehicle_hours": 9.9, "added_metres_total": 300.0,
                     "vehicles_affected": 20}
        with pytest.raises(DelayProfileError):
            verify_against_cost(summed, published, candidate_id="c")

    def test_the_worst_variant_is_taken_field_wise(self):
        """Matching `closure_ranking.worst_variant_cost`: the worst hours and
        the worst distance may come from different variants."""
        summed = {
            "q10": self._variant(5.0, 100.0, 10),
            "q50": self._variant(1.0, 900.0, 20),
            "q90": self._variant(1.5, 200.0, 30),
        }
        published = {"added_vehicle_hours": 5.0, "added_metres_total": 900.0,
                     "vehicles_affected": 30}
        assert verify_against_cost(
            summed, published, candidate_id="c")["matches"] is True


class TestChoosingTheBestDates:
    def _period(self, start_date, hours, metres=0.0, affected=0, schedule="s"):
        return {
            "start_date": start_date,
            "best_schedule": {"schedule_id": schedule},
            "best_cost": {
                "added_vehicle_hours": hours,
                "added_metres_total": metres,
                "vehicles_affected": affected,
            },
        }

    def test_ranking_is_by_cost_not_by_calendar_order(self):
        """`period_comparison` returns periods in DATE order with a status
        column, so "the two best dates" is never simply the top of that list."""
        periods = [
            self._period("2027-07-15", 4.0, schedule="a"),
            self._period("2027-07-16", 1.0, schedule="b"),
            self._period("2027-07-17", 2.0, schedule="c"),
        ]
        assert [item["start_date"] for item in rank_periods(periods)] == [
            "2027-07-16", "2027-07-17", "2027-07-15"]

    def test_distance_then_vehicle_count_break_ties(self):
        periods = [
            self._period("2027-07-15", 1.0, metres=900.0, schedule="a"),
            self._period("2027-07-16", 1.0, metres=100.0, schedule="b"),
            self._period("2027-07-17", 1.0, metres=100.0, affected=2,
                         schedule="c"),
        ]
        assert [item["start_date"] for item in rank_periods(periods)] == [
            "2027-07-16", "2027-07-17", "2027-07-15"]

    def test_a_period_with_no_viable_schedule_is_dropped_not_ranked_last(self):
        """"No viable schedule" is the absence of a score, not a bad one.
        Sorting it to the end would let it become "the second best date" in a
        search where only one period worked."""
        periods = [
            {"start_date": "2027-07-15", "best_schedule": None,
             "best_cost": None, "status": "no_viable"},
            self._period("2027-07-16", 1.0, schedule="b"),
        ]
        assert [item["start_date"] for item in rank_periods(periods)] == [
            "2027-07-16"]


class TestReuseOfTheProductionCosting:
    """This module deliberately calls `disruption`'s private helpers.

    Copying them would let the chart and the ranking drift apart, and making
    them public is not available: `deterministic_disruption.COSTING_SOURCES`
    hashes `disruption.py` into every finished search's daily-cost cache key,
    so editing that file would invalidate hours of completed work. The seam is
    therefore pinned here — a rename must fail loudly, not silently produce a
    second implementation.
    """

    def test_the_private_helpers_it_reuses_still_exist(self):
        assert callable(disruption_module._affected_od_counts)
        assert callable(disruption_module._report)
        assert callable(disruption_module.grouped_path_costs)
        assert hasattr(disruption_module, "SparsePathBatch")
        assert isinstance(disruption_module.SPARSE_ROUTING_MIN_PAIRS, int)

    def test_report_still_takes_the_four_price_tables_by_keyword(self):
        parameters = inspect.signature(disruption_module._report).parameters
        assert {"considered", "affected", "denied", "od_counts", "base_time",
                "base_length", "detour_time", "detour_length"} <= set(parameters)

    def test_the_analysis_package_is_not_a_hashed_costing_source(self):
        """A module that only READS costs must stay out of the cache key, or
        improving a chart would invalidate a search that took hours."""
        from traffic_sim.simulation.deterministic_disruption import (
            COSTING_SOURCES)
        assert not any(name.startswith("traffic_sim/analysis/")
                       for name in COSTING_SOURCES)
        assert delay_profile.__name__.startswith("traffic_sim.analysis.")
