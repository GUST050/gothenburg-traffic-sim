"""The map must still agree with the sensors it was calibrated to.

tools/check_map_matches_sensors.py walks the chain from the raw sensor
source to the integers the browser colours edges with. The arithmetic it
does is small and load-bearing, so it is tested here directly; the live
check itself runs against whatever is published, and is skipped when there
is nothing published to check.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import check_map_matches_sensors as check

LIVE = (Path("sumo/demand_meta.json"), Path("web/data/scenarios/baseline.json"),
        Path("web/data/scenarios/index.json"))


class TestTheComparison:
    def test_geh_is_zero_for_an_exact_match(self):
        assert check.geh(100.0, 100.0) == 0.0

    def test_two_measured_zeros_agree_rather_than_divide_by_zero(self):
        # An edge with no traffic and no expected traffic is agreement, not
        # an undefined comparison.
        assert check.geh(0.0, 0.0) == 0.0

    def test_geh_grows_with_disagreement_and_is_symmetric(self):
        assert check.geh(120.0, 100.0) == pytest.approx(1.907, abs=1e-3)
        assert check.geh(100.0, 120.0) == pytest.approx(1.907, abs=1e-3)
        assert check.geh(200.0, 100.0) > check.geh(120.0, 100.0)

    def test_the_gate_counts_pairs_under_the_threshold(self):
        stats = check.summarize([(100.0, 100.0), (200.0, 100.0)], 5.0)
        assert stats["n"] == 2
        assert stats["geh_ok_pct"] == 50.0
        assert stats["max_abs_error"] == 100.0

    def test_an_empty_comparison_reports_nothing_rather_than_success(self):
        # A check with no comparable pairs must not read as 100% agreement.
        assert check.summarize([], 5.0) == {"n": 0}

    def test_quarters_aggregate_to_whole_hours_only(self):
        assert check.aggregate([1.0] * 10, 4) == [4.0, 4.0]


class TestTheSourceRule:
    """Missing is not zero — the project's own evidence rule, in this check."""

    def test_a_missing_target_is_excluded_not_scored_as_zero(self, monkeypatch):
        targets = [10.0, None, 10.0]
        simulated = [10, 999, 10]
        pairs = [(float(simulated[i]), float(target))
                 for i, target in enumerate(targets) if target is not None]
        assert pairs == [(10.0, 10.0), (10.0, 10.0)]
        assert check.summarize(pairs, 5.0)["geh_ok_pct"] == 100.0


class TestTheAnimatedLayer:
    """The dots that drive along the map come from a different file than the
    colours, and must be counted the way edgeData counts."""

    @staticmethod
    def _traj(vehicles):
        return {"edges": ["a", "b", "c"], "vehicles": vehicles}

    def test_a_vehicle_arriving_from_another_edge_is_an_entry(self):
        # exits[0] = 100 s is when it left edge a and entered edge b.
        counts = check.count_trajectory_entries(
            self._traj([{"e": [0, 1], "x": [100, 200], "d": 0}]), {"b"}, 96)
        assert counts["b"] == {0: 1}

    def test_the_edge_a_vehicle_departs_on_is_not_an_entry(self):
        # SUMO calls that a departure, and the published flows count entries.
        # Counting it would inflate every street trips start on.
        counts = check.count_trajectory_entries(
            self._traj([{"e": [0, 1], "x": [100, 200], "d": 0}]), {"a"}, 96)
        assert counts["a"] == {}

    def test_entries_land_in_the_quarter_they_happen_in(self):
        counts = check.count_trajectory_entries(
            self._traj([{"e": [0, 1], "x": [1800, 2000], "d": 0},
                        {"e": [0, 1], "x": [1799, 2000], "d": 0}]), {"b"}, 96)
        assert counts["b"] == {1: 1, 2: 1}

    def test_an_entry_past_the_simulated_day_is_dropped(self):
        counts = check.count_trajectory_entries(
            self._traj([{"e": [0, 1], "x": [86_400, 86_500], "d": 0}]),
            {"b"}, 96)
        assert counts["b"] == {}

    def test_a_truncated_exit_list_cannot_raise(self):
        # Unfinished vehicles carry fewer exit times than edges.
        counts = check.count_trajectory_entries(
            self._traj([{"e": [0, 1, 2], "x": [100], "d": 0}]), {"b", "c"}, 96)
        assert counts == {"b": {0: 1}, "c": {}}


@pytest.mark.skipif(not all(path.is_file() for path in LIVE),
                    reason="nothing published to check")
class TestTheLivePublication:
    def test_the_published_map_agrees_with_the_sensors(self, capsys):
        # Explicit empty argv: argparse falls back to sys.argv otherwise, and
        # under pytest that is pytest's own command line.
        assert check.main([]) == 0, capsys.readouterr().out

    def test_every_sensor_edge_is_actually_drawn(self):
        from demand.intake import load_sensor_edges

        drawn = json.loads(
            Path("web/data/scenarios/baseline.json").read_text())["flows"]
        for sensor_id, edges in load_sensor_edges().items():
            for edge in edges:
                assert edge in drawn, (
                    f"station {sensor_id} edge {edge} is calibrated against a "
                    "measurement but is not on the map")
