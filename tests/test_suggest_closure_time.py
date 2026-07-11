"""
Unit tests for suggest_closure_time.py (PLAN.md Phase C4).

The heavy end (an actual SUMO simulate stage) is exercised manually against
real demand, not here — these tests cover the parts that must be correct
independent of SUMO: window generation, the topology-only detour diagnostic,
proxy ranking, candidate selection, and cross-seed metric aggregation.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import closure_metrics as cm
import run_scenario
import suggest_closure_time as sct


class TestGenerateWindows:
    """PLAN.md's own C4 spec quotes '163 candidates for 6h over a week' as
    its worked example — reproduced exactly here as the primary regression
    guard, plus the ARCHITECTURE's other quoted case (19 windows/1 day)."""

    def test_six_hours_over_one_week_gives_163_windows(self):
        windows = sct.generate_windows(6 * 3600, 7 * 86400, 3600)
        assert len(windows) == 163
        assert windows[0] == (0, 6 * 3600)
        assert windows[-1] == (7 * 86400 - 6 * 3600, 7 * 86400)

    def test_six_hours_over_one_day_gives_19_windows(self):
        windows = sct.generate_windows(6 * 3600, 86400, 3600)
        assert len(windows) == 19

    def test_duration_equal_to_total_gives_exactly_one_window(self):
        windows = sct.generate_windows(86400, 86400, 3600)
        assert windows == [(0, 86400)]

    def test_duration_longer_than_total_gives_no_windows(self):
        assert sct.generate_windows(2 * 86400, 86400, 3600) == []

    def test_zero_or_negative_duration_raises(self):
        with pytest.raises(ValueError):
            sct.generate_windows(0, 86400, 3600)


class TestWindowQuarters:
    def test_window_aligned_to_quarters(self):
        assert list(sct.window_quarters(0, 1800, 96)) == [0, 1]

    def test_window_partially_overlapping_a_quarter_includes_it(self):
        # 850-1000 overlaps quarter 0 ([0,900)) and quarter 1 ([900,1800)).
        assert list(sct.window_quarters(850, 1000, 96)) == [0, 1]

    def test_window_clamped_to_n_intervals(self):
        assert list(sct.window_quarters(95 * 900, 200 * 900, 96)) == [95]


class TestDetourAvailability:
    """Topology-only: build_edge_graph/reachable already have their own
    correctness tests (TestTruncateStrandedVehicles); this checks the
    aggregation over predecessor/successor SETS is right."""

    def test_full_detour_available(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <connection from="lead" to="closed"/>
  <connection from="closed" to="tail"/>
  <connection from="lead" to="detour"/>
  <connection from="detour" to="tail"/>
</net>""")
        diag = sct.detour_availability(["closed"], net_path)
        assert diag["predecessors"] == ["lead"]
        assert diag["successors"] == ["tail"]
        assert diag["score"] == 1.0

    def test_no_detour_at_all(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <connection from="lead" to="closed"/>
  <connection from="closed" to="tail"/>
</net>""")
        diag = sct.detour_availability(["closed"], net_path)
        assert diag["score"] == 0.0

    def test_dead_end_has_no_score(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <connection from="lead" to="closed"/>
</net>""")
        diag = sct.detour_availability(["closed"], net_path)
        assert diag["score"] is None

    def test_multi_edge_closure_unions_predecessors_and_successors(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <connection from="lead_a" to="closed_a"/>
  <connection from="closed_a" to="tail_a"/>
  <connection from="lead_b" to="closed_b"/>
  <connection from="closed_b" to="tail_b"/>
  <connection from="lead_a" to="tail_b"/>
</net>""")
        diag = sct.detour_availability(["closed_a", "closed_b"], net_path)
        assert set(diag["predecessors"]) == {"lead_a", "lead_b"}
        assert set(diag["successors"]) == {"tail_a", "tail_b"}
        # lead_a->tail_b works directly; the other 3 pairs have no route.
        assert diag["reachable_pairs"] == 1
        assert diag["total_pairs"] == 4


class TestProxyScoresAndRanking:
    def _flows(self, n=4):
        return {
            "closed": np.array([10.0, 10.0, 0.0, 0.0]),
            "corridor_hi": np.array([20.0, 20.0, 20.0, 20.0]),
            "corridor_lo": np.array([1.0, 1.0, 1.0, 1.0]),
        }

    def test_lower_flow_window_scores_better(self):
        windows = [(0, 1800), (1800, 3600)]   # quarters {0,1} vs {2,3}
        flows = self._flows()
        scored = sct.proxy_scores(windows, ["closed"], ["corridor_hi"], flows, 4)
        ranked = sct.rank_candidates(scored)
        # Window 2 (quarters 2,3) has closed-edge flow 0 vs window 1's 10 —
        # it must rank strictly better (lower proxy_rank = better).
        by_begin = {w["begin_s"]: w for w in ranked}
        assert by_begin[1800]["proxy_rank"] < by_begin[0]["proxy_rank"]

    def test_never_produces_a_combined_flow_number(self):
        # The whole point of Borda ranking here is that no field looks like
        # a fabricated "predicted delay" quantity with real-world units.
        windows = [(0, 1800), (1800, 3600)]
        scored = sct.proxy_scores(windows, ["closed"], ["corridor_hi"], self._flows(), 4)
        ranked = sct.rank_candidates(scored)
        for w in ranked:
            assert set(w) >= {"closed_edge_flow", "corridor_flow", "rank_closed_edge",
                              "rank_corridor", "combined_rank", "proxy_rank"}
            assert isinstance(w["proxy_rank"], int)

    def test_missing_corridor_edges_degrades_to_closed_edge_only_ranking(self):
        windows = [(0, 1800), (1800, 3600)]
        flows = self._flows()
        scored = sct.proxy_scores(windows, ["closed"], [], flows, 4)
        assert all(w["corridor_flow"] is None for w in scored)
        ranked = sct.rank_candidates(scored)
        by_begin = {w["begin_s"]: w for w in ranked}
        assert by_begin[1800]["proxy_rank"] < by_begin[0]["proxy_rank"]


class TestSelectCandidates:
    def _ranked(self, n):
        # combined_rank == proxy_rank == index, so rank order is predictable.
        return [{"begin_s": i * 3600, "closed_edge_flow": float(i),
                 "combined_rank": float(i), "proxy_rank": i} for i in range(n)]

    def test_top_k_selected(self):
        ranked = self._ranked(10)
        chosen = sct.select_candidates(ranked, top_k=3, extra_bad=0)
        assert {c["proxy_rank"] for c in chosen} >= {0, 1, 2}

    def test_worst_windows_included_as_controls(self):
        ranked = self._ranked(10)
        chosen = sct.select_candidates(ranked, top_k=2, extra_bad=2)
        chosen_ranks = {c["proxy_rank"] for c in chosen}
        assert {8, 9} <= chosen_ranks   # the two worst by combined_rank

    def test_low_traffic_control_deduplicated_against_top_k(self):
        ranked = self._ranked(5)
        # Here rank 0 IS also the lowest closed_edge_flow, so it must not
        # appear twice.
        chosen = sct.select_candidates(ranked, top_k=1, extra_bad=0)
        begins = [c["begin_s"] for c in chosen]
        assert len(begins) == len(set(begins))


class TestAggregateSeedMetrics:
    def _metrics(self, time_loss, teleports=0, trunc=0, drop=0, queue=None):
        return cm.DisruptionMetrics(
            total_time_loss_s=time_loss, trip_count=100,
            unfinished_trips=2, unfinished_waiting_trips=1,
            teleport_total=teleports, teleport_reasons={},
            loaded=100, inserted=100, running_at_end=0, waiting_at_end=0,
            truncated_unreachable=trunc, dropped_unreachable=drop,
            max_queue_vehicles=queue)

    def test_time_loss_is_averaged_not_summed(self):
        agg = sct.aggregate_seed_metrics([self._metrics(100.0), self._metrics(200.0)])
        assert agg.total_time_loss_s == 150.0

    def test_teleports_are_summed_not_averaged(self):
        # A teleport in ANY seed is a real integrity problem to surface,
        # not something that should be diluted by averaging with a clean
        # seed.
        agg = sct.aggregate_seed_metrics(
            [self._metrics(100.0, teleports=1), self._metrics(100.0, teleports=0)])
        assert agg.teleport_total == 1

    def test_truncated_dropped_taken_from_first_seed_not_summed(self):
        # These are computed once per variant (shared across seeds that draw
        # it), not independently per seed -- summing would double-count.
        agg = sct.aggregate_seed_metrics(
            [self._metrics(100.0, trunc=5, drop=2), self._metrics(100.0, trunc=5, drop=2)])
        assert (agg.truncated_unreachable, agg.dropped_unreachable) == (5, 2)

    def test_max_queue_takes_the_worst_seed(self):
        agg = sct.aggregate_seed_metrics(
            [self._metrics(100.0, queue=10), self._metrics(100.0, queue=30)])
        assert agg.max_queue_vehicles == 30


class TestLoadBaselineFlows:
    def test_signature_mismatch_exits(self, tmp_path, monkeypatch):
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({
            "n_quarters": 96,
            "scenario": {"demand_signature": "old_sig"},
            "flows": {},
        }))
        monkeypatch.setattr(sct, "BASELINE_SCENARIO", baseline)
        with pytest.raises(SystemExit):
            sct.load_baseline_flows("new_sig", 96)

    def test_missing_file_exits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sct, "BASELINE_SCENARIO", tmp_path / "nope.json")
        with pytest.raises(SystemExit):
            sct.load_baseline_flows("sig", 96)

    def test_null_flow_entries_become_zero(self, tmp_path, monkeypatch):
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({
            "n_quarters": 2,
            "scenario": {"demand_signature": "sig"},
            "flows": {"e1": [1, None]},
        }))
        monkeypatch.setattr(sct, "BASELINE_SCENARIO", baseline)
        flows = sct.load_baseline_flows("sig", 2)
        assert list(flows["e1"]) == [1.0, 0.0]

    def test_quarter_count_mismatch_exits(self, tmp_path, monkeypatch):
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({
            "n_quarters": 96,
            "scenario": {"demand_signature": "sig"},
            "flows": {},
        }))
        monkeypatch.setattr(sct, "BASELINE_SCENARIO", baseline)
        with pytest.raises(SystemExit):
            sct.load_baseline_flows("sig", 192)
