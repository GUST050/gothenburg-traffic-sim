"""The closure ranking key: lexicographic, disqualifying, deterministic."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.simulation.closure_ranking import (
    ClosureCost, q50_variant_cost, rank_closures, reduce_variant_cost,
    worst_variant_cost)


def rec(hours=0.0, metres=0.0, affected=0, no_detour=0):
    return {"added_vehicle_hours": hours, "added_metres_total": metres,
            "vehicles_affected": affected, "vehicles_no_detour": no_detour}


def cost(cid, hours=0.0, metres=0.0, affected=0, no_detour=0):
    return ClosureCost(cid, hours, metres, affected, no_detour)


class TestRankingOrder:
    def test_vehicle_hours_decides_first(self):
        """The headline term: it already combines how many and how much."""
        a = cost("a", hours=20.6, metres=10, affected=10)
        b = cost("b", hours=0.2, metres=999_999, affected=99_999)
        viable, _ = rank_closures([a, b])
        assert [c.candidate_id for c in viable] == ["b", "a"]

    def test_distance_breaks_a_tie_in_hours(self):
        a = cost("a", hours=5.0, metres=800_000, affected=10)
        b = cost("b", hours=5.0, metres=1_000, affected=9_000)
        viable, _ = rank_closures([a, b])
        assert [c.candidate_id for c in viable] == ["b", "a"], (
            "equal vehicle-hours must fall through to distance, not to count")

    def test_affected_count_breaks_a_tie_in_hours_and_distance(self):
        a = cost("a", hours=5.0, metres=1_000, affected=8_000)
        b = cost("b", hours=5.0, metres=1_000, affected=12)
        viable, _ = rank_closures([a, b])
        assert [c.candidate_id for c in viable] == ["b", "a"]

    def test_order_is_deterministic_for_identical_costs(self):
        a = cost("zebra", hours=1.0, metres=1.0, affected=1)
        b = cost("alpha", hours=1.0, metres=1.0, affected=1)
        viable, _ = rank_closures([a, b])
        assert [c.candidate_id for c in viable] == ["alpha", "zebra"]


class TestDisqualification:
    def test_a_severed_destination_is_not_ranked_against_viable_ones(self):
        """Stranding a driver is not 'a bit more delay' — it is a different
        outcome, so it must not win by having low vehicle-hours."""
        severed = cost("severed", hours=0.0, metres=0.0, affected=5,
                       no_detour=287)
        ok = cost("ok", hours=20.6, metres=800_000, affected=8_427)
        viable, refused = rank_closures([severed, ok])
        assert [c.candidate_id for c in viable] == ["ok"]
        assert [c.candidate_id for c in refused] == ["severed"]

    def test_refused_are_ordered_worst_first(self):
        a = cost("a", no_detour=5)
        b = cost("b", no_detour=300)
        _, refused = rank_closures([a, b])
        assert [c.candidate_id for c in refused] == ["b", "a"]


class TestWorstVariant:
    def test_worst_is_taken_field_wise_across_variants(self):
        """q10/q50/q90 are three plausible directional assignments; a schedule
        is only as good as its least favourable outcome on each axis."""
        c = worst_variant_cost("x", [
            rec(hours=1.0, metres=500, affected=100, no_detour=0),
            rec(hours=3.0, metres=100, affected=90, no_detour=2),
            rec(hours=2.0, metres=900, affected=110, no_detour=0),
        ])
        assert c.added_vehicle_hours == 3.0
        assert c.added_metres_total == 900
        assert c.vehicles_affected == 110
        assert c.vehicles_no_detour == 2
        assert c.disqualified

    def test_a_missing_field_is_refused_not_defaulted(self):
        bad = {"added_vehicle_hours": 1.0, "added_metres_total": 1.0}
        with pytest.raises(ValueError, match="missing required field"):
            worst_variant_cost("x", [bad])

    def test_negative_and_non_numeric_are_refused(self):
        with pytest.raises(ValueError, match="must not be negative"):
            worst_variant_cost("x", [rec(hours=-1.0)])
        with pytest.raises(ValueError, match="must be numeric"):
            worst_variant_cost("x", [{**rec(), "vehicles_affected": "many"}])

    def test_no_variants_is_refused(self):
        with pytest.raises(ValueError, match="at least one variant"):
            worst_variant_cost("x", [])


class TestQ50Variant:
    def test_q50_drives_cost_but_reachability_remains_all_variant(self):
        records = [
            {"demand_variant": "q10", **rec(
                hours=1.0, metres=10, affected=10, no_detour=0)},
            {"demand_variant": "q50", **rec(
                hours=2.0, metres=20, affected=20, no_detour=0)},
            {"demand_variant": "q90", **rec(
                hours=9.0, metres=90, affected=90, no_detour=1)},
        ]
        c = q50_variant_cost("x", records)
        assert c.added_vehicle_hours == 2.0
        assert c.added_metres_total == 20
        assert c.vehicles_affected == 20
        assert c.vehicles_no_detour == 1
        assert c.disqualified

    def test_q50_must_be_present_exactly_once(self):
        with pytest.raises(ValueError, match="exactly one q50"):
            q50_variant_cost("x", [
                {"demand_variant": "q10", **rec()},
                {"demand_variant": "q90", **rec()},
            ])

    def test_versioned_reducer_keeps_v1_and_v2_distinct(self):
        records = [
            {"demand_variant": "q10", **rec(hours=1.0)},
            {"demand_variant": "q50", **rec(hours=2.0)},
            {"demand_variant": "q90", **rec(hours=3.0)},
        ]
        assert reduce_variant_cost("x", records, "closure_cost_v1").added_vehicle_hours == 3.0
        assert reduce_variant_cost("x", records, "closure_cost_v2").added_vehicle_hours == 2.0


class TestAgainstMeasuredEdges:
    """Ordering on the real 2025-09-16 numbers measured across the busiest
    edges — the case the old time-loss key could not separate at all."""

    def test_real_edges_order_as_measured(self):
        costs = [
            cost("1305379743_1305379746_0", hours=20.6, metres=41_000, affected=8_427),
            cost("1453741774_18241874_0", hours=16.1, metres=815_361, affected=5_652),
            cost("18241874_9439210780_0", hours=0.0, metres=0, affected=4_371),
            cost("30420757_30421744_0", hours=0.2, metres=100, affected=4_844,
                 no_detour=287),
        ]
        viable, refused = rank_closures(costs)
        assert [c.candidate_id for c in viable] == [
            "18241874_9439210780_0",
            "1453741774_18241874_0",
            "1305379743_1305379746_0",
        ]
        assert [c.candidate_id for c in refused] == ["30420757_30421744_0"], (
            "the edge that strands 287 vehicles must be refused, not ranked "
            "best on its near-zero vehicle-hours")
