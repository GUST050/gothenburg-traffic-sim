"""
Unit tests for suggest_closure_time.py (IMPROVEMENT_PLAN.md Phase C4).

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
    """IMPROVEMENT_PLAN.md's own C4 spec quotes '163 candidates for 6h over a week' as
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


class TestAlignedQuarters:
    def test_accepts_exact_quarter_hour_multiples(self):
        assert sct.aligned_quarters(0.25, "duration") == 1
        assert sct.aligned_quarters(1.5, "duration") == 6

    def test_rejects_fractional_bucket_and_nonfinite_values(self):
        with pytest.raises(ValueError, match="multiple of 0.25"):
            sct.aligned_quarters(1.1, "duration")
        with pytest.raises(ValueError, match="finite"):
            sct.aligned_quarters(float("nan"), "duration")


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
        scored, excluded = sct.proxy_scores(windows, ["closed"], ["corridor_hi"], flows, 4)
        assert excluded == 0
        ranked = sct.rank_candidates(scored)
        # Window 2 (quarters 2,3) has closed-edge flow 0 vs window 1's 10 —
        # it must rank strictly better (lower proxy_rank = better).
        by_begin = {w["begin_s"]: w for w in ranked}
        assert by_begin[1800]["proxy_rank"] < by_begin[0]["proxy_rank"]

    def test_never_produces_a_combined_flow_number(self):
        # The whole point of Borda ranking here is that no field looks like
        # a fabricated "predicted delay" quantity with real-world units.
        windows = [(0, 1800), (1800, 3600)]
        scored, _ = sct.proxy_scores(windows, ["closed"], ["corridor_hi"], self._flows(), 4)
        ranked = sct.rank_candidates(scored)
        for w in ranked:
            assert set(w) >= {"closed_edge_flow", "corridor_flow", "rank_closed_edge",
                              "rank_corridor", "combined_rank", "proxy_rank"}
            assert isinstance(w["proxy_rank"], int)

    def test_missing_corridor_edges_degrades_to_closed_edge_only_ranking(self):
        windows = [(0, 1800), (1800, 3600)]
        flows = self._flows()
        scored, _ = sct.proxy_scores(windows, ["closed"], [], flows, 4)
        assert all(w["corridor_flow"] is None for w in scored)
        ranked = sct.rank_candidates(scored)
        by_begin = {w["begin_s"]: w for w in ranked}
        assert by_begin[1800]["proxy_rank"] < by_begin[0]["proxy_rank"]

    def test_window_with_no_real_closed_edge_data_is_excluded_not_ranked_as_best(self):
        # A null-filled closed edge used to be coerced to 0.0 flow, i.e. the
        # ideal candidate — exactly backwards. It must now be dropped from
        # the candidate list entirely, not appear as the top-ranked window.
        windows = [(0, 1800), (1800, 3600)]
        flows = {
            "closed": np.array([10.0, 10.0, np.nan, np.nan]),   # missing in window 2
            "corridor_hi": np.array([20.0, 20.0, 20.0, 20.0]),
        }
        scored, excluded = sct.proxy_scores(windows, ["closed"], ["corridor_hi"], flows, 4)
        assert excluded == 1
        assert [w["begin_s"] for w in scored] == [0]

    def test_all_windows_excluded_when_closed_edge_entirely_missing(self):
        windows = [(0, 1800), (1800, 3600)]
        flows = {"closed": np.array([np.nan, np.nan, np.nan, np.nan]),
                "corridor_hi": np.array([20.0, 20.0, 20.0, 20.0])}
        scored, excluded = sct.proxy_scores(windows, ["closed"], ["corridor_hi"], flows, 4)
        assert excluded == 2
        assert scored == []

    def test_partial_corridor_missingness_still_scores_from_available_data(self):
        windows = [(0, 1800), (1800, 3600)]
        flows = {
            "closed": np.array([10.0, 10.0, 0.0, 0.0]),
            "corridor_hi": np.array([20.0, 20.0, np.nan, np.nan]),   # missing in window 2
        }
        scored, excluded = sct.proxy_scores(windows, ["closed"], ["corridor_hi"], flows, 4)
        assert excluded == 0
        by_begin = {w["begin_s"]: w for w in scored}
        assert by_begin[0]["corridor_flow"] == 20.0
        assert by_begin[1800]["corridor_flow"] is None   # no real corridor data this window

    def test_rank_candidates_handles_mixed_corridor_availability(self):
        # One window has corridor data, one doesn't -- must not crash, and
        # must still rank both windows using whatever signal is available.
        scored = [
            {"begin_s": 0, "end_s": 1800, "closed_edge_flow": 10.0, "corridor_flow": 20.0},
            {"begin_s": 1800, "end_s": 3600, "closed_edge_flow": 5.0, "corridor_flow": None},
        ]
        ranked = sct.rank_candidates(scored)
        by_begin = {w["begin_s"]: w for w in ranked}
        assert by_begin[1800]["rank_corridor"] is None
        # Lower closed-edge flow (5 < 10) with no corridor penalty/bonus
        # applied -> window 2 should still rank at least as well.
        assert by_begin[1800]["proxy_rank"] <= by_begin[0]["proxy_rank"]


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

    def test_truncated_dropped_use_max_not_mean_or_first_seed(self):
        # A seed with 0 dropped averaged against a seed with 1 dropped must
        # not round down to 0 and hide a real dropped vehicle from
        # is_disqualified() -- same reasoning as teleports using SUM, but
        # MAX here since a variant sampled by multiple seeds shouldn't have
        # its fixed count added again for every repeat.
        agg = sct.aggregate_seed_metrics(
            [self._metrics(100.0, trunc=5, drop=2), self._metrics(100.0, trunc=0, drop=0)])
        assert (agg.truncated_unreachable, agg.dropped_unreachable) == (5, 2)

    def test_identical_repeated_variant_is_not_double_counted(self):
        # Two seeds drawing the SAME variant (seeds > len(variants)) report
        # the same fixed truncation count -- MAX (not SUM) avoids counting
        # that variant's truncation twice just because it was sampled twice.
        agg = sct.aggregate_seed_metrics(
            [self._metrics(100.0, trunc=5, drop=2), self._metrics(100.0, trunc=5, drop=2)])
        assert (agg.truncated_unreachable, agg.dropped_unreachable) == (5, 2)

    def test_max_queue_takes_the_worst_seed(self):
        agg = sct.aggregate_seed_metrics(
            [self._metrics(100.0, queue=10), self._metrics(100.0, queue=30)])
        assert agg.max_queue_vehicles == 30


class TestRecommendationStatus:
    """Structural (not just prose) enforcement of 'never claim more than
    measured' (external review section 4): never returns "validated", even
    for a strong correlation, since the sample is always small and
    selection-biased by design (proxy top-k + controls, not stratified/
    held-out)."""

    def test_no_correlation_data_is_insufficient_evidence(self):
        assert sct.recommendation_status(None) == "insufficient_evidence"

    def test_strong_correlation_is_still_only_screening(self):
        status = sct.recommendation_status({"spearman_rho": 0.9, "p_value": 0.01, "n": 10})
        assert status == "screening_only_correlated"
        assert "validated" not in status

    def test_weak_correlation_is_flagged_distinctly(self):
        status = sct.recommendation_status({"spearman_rho": 0.1, "p_value": 0.8, "n": 5})
        assert status == "screening_only_weak_correlation"

    def test_boundary_rho_exactly_0_3_is_weak(self):
        status = sct.recommendation_status({"spearman_rho": 0.3, "p_value": 0.5, "n": 5})
        assert status == "screening_only_weak_correlation"


class TestClosureFeasibility:
    def _metrics(self, *, queue=10, trunc=0, unfinished=0):
        return cm.DisruptionMetrics(
            total_time_loss_s=100.0, trip_count=100,
            unfinished_trips=unfinished, unfinished_waiting_trips=0,
            teleport_total=0, teleport_reasons={}, loaded=100, inserted=100,
            running_at_end=0, waiting_at_end=0,
            truncated_unreachable=trunc, dropped_unreachable=0,
            max_queue_vehicles=queue)

    def test_requires_complete_detour_and_queue_evidence(self):
        result = sct.closure_feasibility(
            self._metrics(), self._metrics(queue=5),
            detour={"score": 1.0})
        assert result["eligible"] is True
        assert result["queue"]["delta"] == 5

    def test_partial_detour_and_truncation_are_hard_failures(self):
        result = sct.closure_feasibility(
            self._metrics(trunc=1), self._metrics(),
            detour={"score": 0.5})
        assert result["eligible"] is False
        assert "partial_detour_access" in result["hard_failures"]
        assert "truncated_unreachable_vehicles" in result["hard_failures"]

    def test_missing_queue_proxy_is_not_silently_ranked(self):
        result = sct.closure_feasibility(
            self._metrics(queue=None), self._metrics(queue=5),
            detour={"score": 1.0})
        assert result["eligible"] is False
        assert "queue_proxy_unmeasured" in result["hard_failures"]


class TestDeltaTimeLossInterval:
    """IMPROVEMENT_PLAN.md C5 explicitly wants 'median ΔtimeLoss + seed interval' in the
    UI, which the mean-only closure_metrics.MetricComparison throws away —
    this fills that gap without changing the frozen DisruptionMetrics shape."""

    def test_median_and_interval_uses_paired_baseline_seeds(self):
        # Paired deltas are 0, 10, 20.
        result = sct.delta_time_loss_interval([90.0, 110.0, 130.0],
                                              [90.0, 100.0, 110.0])
        assert result["median_s"] == 10.0
        assert result["min_s"] == 0.0
        assert result["max_s"] == 20.0
        assert result["n_seeds"] == 3

    def test_single_seed_all_three_equal(self):
        result = sct.delta_time_loss_interval([150.0], [100.0])
        assert result == {"median_s": 50.0, "min_s": 50.0, "max_s": 50.0, "n_seeds": 1}

    def test_even_seed_count_averages_the_two_middle_values(self):
        result = sct.delta_time_loss_interval([80.0, 100.0, 120.0, 140.0],
                                               [100.0, 100.0, 100.0, 100.0])
        # deltas: -20, 0, 20, 40 -> median of the two middle (0, 20) = 10
        assert result["median_s"] == 10.0

    def test_unpaired_seed_counts_are_rejected(self):
        with pytest.raises(ValueError, match="equal non-zero length"):
            sct.delta_time_loss_interval([80.0], [100.0, 110.0])


def _write_tiny_metrics_fixtures(sumo_dir, stem):
    """Minimal-but-valid tripinfo/statistics/summary XML — empty is fine,
    closure_metrics.py's readers tolerate absent children (_number/_integer
    default gracefully when an element is None)."""
    tripinfo = sumo_dir / f"{stem}_tripinfo.xml"
    statistics = sumo_dir / f"{stem}_statistics.xml"
    summary = sumo_dir / f"{stem}_summary.xml"
    tripinfo.write_text("<tripinfos></tripinfos>")
    statistics.write_text("<statistics></statistics>")
    summary.write_text("<summary></summary>")
    return {"tripinfo": tripinfo, "statistics": statistics, "summary": summary}


class TestSimulateClosureVariantAttribution:
    """simulate_closure's per-seed truncated/dropped attribution (fixed in
    review 2026-07-11): each seed must carry only the truncation count of
    the SPECIFIC variant it actually ran, not a sum across every variant."""

    def _setup_variants(self, tmp_path, monkeypatch, n_vehicles):
        # A tiny net where "closed" has no detour at all (lead->closed->
        # destination is the only path) -- mirrors
        # TestTimeWindowedClosures' established fixture pattern in
        # test_scenario.py exactly.
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <connection from="lead" to="closed"/>
  <connection from="closed" to="destination"/>
</net>""")
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)
        monkeypatch.setattr(sct.rs, "SUMO_DIR", tmp_path)
        # edges_near() (called inside simulate_closure to size the rerouter)
        # reads plain.edg.xml directly -- a minimal shape-only file with no
        # edges near the closure is fine for this test's purposes.
        (tmp_path / "plain.edg.xml").write_text('<edges>\n'
            '  <edge id="lead" from="lead_n" to="closed_n" shape="0,0 1,1"/>\n'
            '  <edge id="closed" from="closed_n" to="dest_n" shape="1,1 2,2"/>\n'
            '</edges>')

        # variant i gets n_vehicles[i] vehicles routed straight through the
        # closed edge with no detour -> truncate_stranded_vehicles finds
        # exactly n_vehicles[i] truncated for that variant. Distinct counts
        # per variant are the point: it's how the tests below can tell
        # "this seed's own variant's count" apart from "the sum across
        # every variant" (the old bug).
        paths = []
        names = ["calibrated.rou.xml", "calibrated_v1.rou.xml", "calibrated_v2.rou.xml"]
        for i, n in enumerate(n_vehicles):
            vp = tmp_path / names[i]
            vehicles = "\n".join(
                f'  <vehicle id="v{i}_{j}" depart="0">'
                f'<route edges="lead closed destination"/></vehicle>'
                for j in range(n))
            vp.write_text(f"<routes>\n{vehicles}\n</routes>")
            paths.append(vp)
        return paths

    def test_each_seed_carries_only_its_own_variant_truncation(self, tmp_path, monkeypatch):
        # variant 0 -> 2 truncated, variant 1 -> 1 truncated. The old bug
        # summed these (3) and reported that combined total for EVERY seed;
        # each seed must now see only its own variant's count.
        vp0, vp1 = self._setup_variants(tmp_path, monkeypatch, [2, 1])
        adj = run_scenario.build_edge_graph({"closed"})

        seen_seeds = []

        def fake_run_sumo(seed, route_path, add_paths, duration_s, home, **kw):
            seen_seeds.append(seed)
            return _write_tiny_metrics_fixtures(tmp_path, f"seed{seed}")

        monkeypatch.setattr(sct.rs, "run_sumo", fake_run_sumo)

        scratch = []
        metrics, n_trunc, n_drop, _ = sct.simulate_closure(
            name="w0", closures=[{"edge_id": "closed", "begin_s": 0, "end_s": 400}],
            close_edges=["closed"], variants=[vp0, vp1], seeds=2, n_intervals=4,
            duration_s=900, home=tmp_path, micro=True, adj=adj,
            freeflow={"lead": 10.0}, scratch=scratch)

        # max(2, 1) = 2, NOT 3 (the old sum-across-all-variants bug would
        # have broadcast 3 to every seed's aggregate).
        assert metrics.truncated_unreachable == 2
        assert n_trunc == 3   # candidate-level total: both variants WERE used
        assert n_drop == 0
        assert seen_seeds == [1000, 1001]

    def test_candidate_level_total_excludes_unused_variants(self, tmp_path, monkeypatch):
        # 3 variants (2, 1, 5 truncated respectively) but only 2 seeds, so
        # variant 2 (5 truncated) is NEVER actually simulated. The old bug
        # summed truncation across ALL variants regardless of whether they
        # were used; the candidate-level total must reflect only the
        # variants THIS run actually drew.
        vp0, vp1, vp2 = self._setup_variants(tmp_path, monkeypatch, [2, 1, 5])
        adj = run_scenario.build_edge_graph({"closed"})

        def fake_run_sumo(seed, route_path, add_paths, duration_s, home, **kw):
            return _write_tiny_metrics_fixtures(tmp_path, f"seed{seed}")

        monkeypatch.setattr(sct.rs, "run_sumo", fake_run_sumo)

        scratch = []
        _, n_trunc, n_drop, _ = sct.simulate_closure(
            name="w0", closures=[{"edge_id": "closed", "begin_s": 0, "end_s": 400}],
            close_edges=["closed"], variants=[vp0, vp1, vp2], seeds=2, n_intervals=4,
            duration_s=900, home=tmp_path, micro=True, adj=adj,
            freeflow={"lead": 10.0}, scratch=scratch)

        assert n_trunc == 3   # 2 + 1 (variant 2's 5 excluded -- never sampled)

    def test_repeated_variant_is_not_double_counted_at_candidate_level(self, tmp_path, monkeypatch):
        # 3 seeds cycling over 2 variants (seed 2 repeats variant 0) must
        # not double-count variant 0's fixed truncation just because a
        # third seed happened to draw it again.
        vp0, vp1 = self._setup_variants(tmp_path, monkeypatch, [2, 1])
        adj = run_scenario.build_edge_graph({"closed"})

        def fake_run_sumo(seed, route_path, add_paths, duration_s, home, **kw):
            return _write_tiny_metrics_fixtures(tmp_path, f"seed{seed}")

        monkeypatch.setattr(sct.rs, "run_sumo", fake_run_sumo)

        scratch = []
        _, n_trunc, n_drop, _ = sct.simulate_closure(
            name="w0", closures=[{"edge_id": "closed", "begin_s": 0, "end_s": 400}],
            close_edges=["closed"], variants=[vp0, vp1], seeds=3, n_intervals=4,
            duration_s=900, home=tmp_path, micro=True, adj=adj,
            freeflow={"lead": 10.0}, scratch=scratch)

        assert n_trunc == 3   # 2 + 1, NOT 2 + 2 + 1 (variant 0 sampled twice)


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

    def test_null_flow_entries_are_preserved_as_missing_not_zero(self, tmp_path, monkeypatch):
        # CLAUDE.md's contract: null means missing, never a known zero. An
        # earlier version of this function coerced null to 0.0, which would
        # score an edge nobody has real data for as the ideal (lowest-
        # traffic) window to close it — found in review 2026-07-11.
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({
            "n_quarters": 2,
            "scenario": {"demand_signature": "sig"},
            "flows": {"e1": [1, None]},
        }))
        monkeypatch.setattr(sct, "BASELINE_SCENARIO", baseline)
        flows = sct.load_baseline_flows("sig", 2)
        assert flows["e1"][0] == 1.0
        assert np.isnan(flows["e1"][1])

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
