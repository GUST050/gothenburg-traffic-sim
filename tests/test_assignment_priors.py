"""Unit tests for assignment_priors.py's testable core: the Dial-style
stochastic-multipath variant builder and the robust scale calibration —
the two mechanisms behind the "three real bugs found and fixed" history in
this module's docstring (a single deterministic shortest path put zero load
on 6 of 7 sensor edges; least-squares scale fitting gave R² -4 to -8).
build_perturbed_variants()/robust_scale() were extracted from main() during
this test-writing pass with no logic change (verified: assignment_priors.py
still runs end-to-end and produces the same class of output)."""

import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from assignment_priors import (build_perturbed_variants, daily_shape,
                               fastest_parallel_edge_times, robust_scale,
                               add_path_load)


class TestFastestParallelEdgeTimes:
    """Found in a review 2026-07-10: collapsing parallel edges to one
    travel-time edge per (u, v) pair used to keep whichever edge the
    MultiDiGraph iterator visited LAST, not the fastest — a real case had
    a 0.49s edge silently discarded in favor of a 1.53s one purely due to
    iteration order."""

    def test_keeps_the_faster_of_two_parallel_edges_regardless_of_order(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=100.0, maxspeed="30")   # slow: ~12s
        G.add_edge(1, 2, key=1, length=100.0, maxspeed="50")   # fast: ~7.2s

        base_time, base_time_key = fastest_parallel_edge_times(G)

        assert base_time_key[(1, 2)] == 1
        assert base_time[(1, 2)] == pytest.approx(100.0 / (50 / 3.6))

    def test_order_of_edge_insertion_does_not_matter(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=100.0, maxspeed="50")   # fast, inserted FIRST
        G.add_edge(1, 2, key=1, length=100.0, maxspeed="30")   # slow, inserted SECOND

        base_time, base_time_key = fastest_parallel_edge_times(G)

        assert base_time_key[(1, 2)] == 0
        assert base_time[(1, 2)] == pytest.approx(100.0 / (50 / 3.6))

    def test_single_edge_pair_unaffected(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, length=200.0, maxspeed="40")

        base_time, base_time_key = fastest_parallel_edge_times(G)

        assert base_time_key[(1, 2)] == 0
        assert base_time[(1, 2)] == pytest.approx(200.0 / (40 / 3.6))


class TestBuildPerturbedVariants:
    def test_returns_requested_count(self):
        base_time = {(1, 2): 10.0, (2, 3): 5.0}
        rng = np.random.default_rng(0)
        variants = build_perturbed_variants(base_time, 5, 0.35, rng)
        assert len(variants) == 5
        assert all(isinstance(v, nx.DiGraph) for v in variants)

    def test_every_variant_has_all_base_edges(self):
        base_time = {(1, 2): 10.0, (2, 3): 5.0, (3, 4): 2.0}
        rng = np.random.default_rng(1)
        variants = build_perturbed_variants(base_time, 3, 0.35, rng)
        for v in variants:
            assert set(v.edges()) == {(1, 2), (2, 3), (3, 4)}

    def test_weights_are_positive_perturbations_of_base(self):
        base_time = {(1, 2): 10.0}
        rng = np.random.default_rng(2)
        variants = build_perturbed_variants(base_time, 20, 0.35, rng)
        weights = [v[1][2]["weight"] for v in variants]
        assert all(w > 0 for w in weights)
        # lognormal jitter around 1x: shouldn't collapse to exactly the base
        # value every time, or the whole point (route diversity) is lost.
        assert len(set(round(w, 6) for w in weights)) > 1

    def test_zero_sigma_reduces_to_the_base_graph(self):
        base_time = {(1, 2): 10.0, (2, 3): 4.0}
        rng = np.random.default_rng(3)
        variants = build_perturbed_variants(base_time, 4, 0.0, rng)
        for v in variants:
            assert v[1][2]["weight"] == pytest.approx(10.0)
            assert v[2][3]["weight"] == pytest.approx(4.0)

    def test_different_variants_can_change_shortest_path_choice(self):
        """The actual point of the mechanism: with enough jitter, two
        routes of similar cost should each win in at least one variant —
        otherwise all load still collapses onto one canonical path."""
        base_time = {(1, 2): 10.0, (2, 4): 10.0,   # route A: 1-2-4, cost 20
                     (1, 3): 10.5, (3, 4): 10.5}    # route B: 1-3-4, cost 21
        rng = np.random.default_rng(4)
        variants = build_perturbed_variants(base_time, 30, 0.35, rng)
        winners = set()
        for v in variants:
            path = nx.shortest_path(v, 1, 4, weight="weight")
            winners.add(tuple(path))
        assert len(winners) > 1


class TestPathLoading:
    def test_parallel_edges_load_only_the_key_used_by_routing_graph(self):
        graph = nx.MultiDiGraph()
        graph.add_edge("a", "b", key="first")
        graph.add_edge("a", "b", key="winning")
        load = {"a_b_first": 0.0, "a_b_winning": 0.0}

        # This mirrors compute_assignment_load: the final MultiDiGraph edge
        # encountered for (a, b) is the one represented in its DiGraph.
        base_time_key = {}
        for u, v, key in graph.edges(keys=True):
            base_time_key[(u, v)] = key
        add_path_load(load, ["a", "b"], base_time_key)

        assert load["a_b_winning"] == 1.0
        assert load["a_b_first"] == 0.0


class TestRobustScale:
    def test_perfect_linear_relationship(self):
        x_load = np.array([10.0, 20.0, 30.0])
        y_meas = np.array([50.0, 100.0, 150.0])   # exactly 5x
        scale, r2 = robust_scale(x_load, y_meas)
        assert scale == pytest.approx(5.0)
        assert r2 == pytest.approx(1.0)

    def test_one_outlier_does_not_dominate_the_median(self):
        # 4 points agree on scale=2, one wild outlier implies scale=100 —
        # this is exactly the "n=6-7, one noisy edge" scenario the
        # docstring says least-squares handled badly.
        x_load = np.array([10.0, 20.0, 30.0, 40.0, 5.0])
        y_meas = np.array([20.0, 40.0, 60.0, 80.0, 500.0])
        scale, _ = robust_scale(x_load, y_meas)
        assert scale == pytest.approx(2.0)

    def test_zero_load_is_guarded_against_division_by_zero(self):
        x_load = np.array([0.0, 10.0])
        y_meas = np.array([5.0, 20.0])
        scale, r2 = robust_scale(x_load, y_meas)
        assert np.isfinite(scale)
        assert np.isfinite(r2)


class TestDailyShape:
    def test_normalizes_to_one(self, tmp_path, monkeypatch):
        import json
        profiles = {"e1": {"weekday": [1.0] * 96}}
        (tmp_path / "web" / "data").mkdir(parents=True)
        (tmp_path / "web" / "data" / "normal_profile.json").write_text(
            json.dumps({"profiles": profiles}))
        monkeypatch.chdir(tmp_path)
        shape = daily_shape()
        assert shape.shape == (96,)
        assert shape.sum() == pytest.approx(1.0)

    def test_flat_profile_gives_uniform_shape(self, tmp_path, monkeypatch):
        import json
        profiles = {"e1": {"weekday": [3.0] * 96}}
        (tmp_path / "web" / "data").mkdir(parents=True)
        (tmp_path / "web" / "data" / "normal_profile.json").write_text(
            json.dumps({"profiles": profiles}))
        monkeypatch.chdir(tmp_path)
        shape = daily_shape()
        np.testing.assert_allclose(shape, np.full(96, 1 / 96))
