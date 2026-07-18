"""Unit tests for validate_sim.py's LOSO corridor-prior wiring.

FIXED 2026-07-09 (found while auditing the whole codebase): corridor_priors
("sensors helping each other" — same-direction station pairs linked by a
short path bound the edges between them, observability.corridor_priors)
were computed and used by the real, deployed build_sumo_demand.py pipeline,
but never wired into this validation script at all — every LOSO figure on
record understated the deployed system's actual recovery. The mechanism
itself was already fully general (scans every PAIR of measured sensors, no
hardcoded IDs), so this was a validation-accuracy gap, not a scalability
one. corridor_priors_for_fold() is the extracted, testable leakage-
exclusion logic: a corridor prior anchored (from OR to) on the held-out
station must be dropped from that station's own fold."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import validate_sim
from validate_sim import (corridor_priors_for_fold, demand_through_share_target,
                          require_historical_demand)


def make_corridor(from_edge, to_edge, prior, band):
    return {"mid_edge": {"from_sensor_edge": from_edge, "to_sensor_edge": to_edge,
                         "prior": prior, "band": band}}


class TestCorridorPriorsForFold:
    def test_included_when_neither_anchor_is_held_out(self):
        corridor = make_corridor("eA", "eB", [10.0], [2.0])
        edge_to_sensor = {"eA": "107", "eB": "1076"}
        out = corridor_priors_for_fold(corridor, edge_to_sensor, held="133", qi=0)
        assert out == {"mid_edge": (10.0, 0.5)}

    def test_excluded_when_from_edge_belongs_to_held_out_station(self):
        corridor = make_corridor("eA", "eB", [10.0], [2.0])
        edge_to_sensor = {"eA": "107", "eB": "1076"}
        out = corridor_priors_for_fold(corridor, edge_to_sensor, held="107", qi=0)
        assert out == {}

    def test_excluded_when_to_edge_belongs_to_held_out_station(self):
        corridor = make_corridor("eA", "eB", [10.0], [2.0])
        edge_to_sensor = {"eA": "107", "eB": "1076"}
        out = corridor_priors_for_fold(corridor, edge_to_sensor, held="1076", qi=0)
        assert out == {}

    def test_null_value_at_this_quarter_is_skipped(self):
        corridor = make_corridor("eA", "eB", [None], [2.0])
        edge_to_sensor = {"eA": "107", "eB": "1076"}
        out = corridor_priors_for_fold(corridor, edge_to_sensor, held="133", qi=0)
        assert out == {}

    def test_quarter_index_beyond_array_length_is_skipped(self):
        corridor = make_corridor("eA", "eB", [10.0], [2.0])
        edge_to_sensor = {"eA": "107", "eB": "1076"}
        out = corridor_priors_for_fold(corridor, edge_to_sensor, held="133", qi=5)
        assert out == {}

    def test_weight_is_inverse_of_band_floored_at_one(self):
        """A near-zero band (two near-identical sensor readings) must not
        blow the weight up unboundedly -- floored the same way
        build_sumo_demand.py's own corridor application is."""
        corridor = make_corridor("eA", "eB", [10.0], [0.1])
        edge_to_sensor = {"eA": "107", "eB": "1076"}
        out = corridor_priors_for_fold(corridor, edge_to_sensor, held="133", qi=0)
        assert out["mid_edge"] == pytest.approx((10.0, 1.0))

    def test_missing_band_falls_back_to_default_of_8(self):
        corridor = make_corridor("eA", "eB", [10.0], [None])
        edge_to_sensor = {"eA": "107", "eB": "1076"}
        out = corridor_priors_for_fold(corridor, edge_to_sensor, held="133", qi=0)
        assert out["mid_edge"] == pytest.approx((10.0, 1.0 / 8.0))

    def test_multiple_corridor_edges_independently_filtered(self):
        corridor = {
            "mid1": {"from_sensor_edge": "eA", "to_sensor_edge": "eB",
                     "prior": [10.0], "band": [2.0]},
            "mid2": {"from_sensor_edge": "eC", "to_sensor_edge": "eD",
                     "prior": [20.0], "band": [4.0]},
        }
        edge_to_sensor = {"eA": "107", "eB": "1076", "eC": "133", "eD": "134"}
        out = corridor_priors_for_fold(corridor, edge_to_sensor, held="107", qi=0)
        assert "mid1" not in out   # anchored on held-out 107
        assert out["mid2"] == pytest.approx((20.0, 0.25))


class TestHistoricalDemandGuard:
    def test_single_day_window_keeps_its_begin_and_end(self):
        start, end = require_historical_demand({
            "source": "historical", "date": "2025-09-16",
            "start_date": "2025-09-16", "end_date_exclusive": "2025-09-17",
            "days": 1, "begin": "06:00", "end": "10:00",
        })
        assert str(start) == "2025-09-16 06:00:00"
        assert str(end) == "2025-09-16 10:00:00"

    def test_supported_multi_day_historical_metadata_is_accepted(self):
        start, end = require_historical_demand({
            "source": "historical", "start_date": "2025-09-16",
            "end_date_exclusive": "2025-09-18", "days": 2,
        })
        assert str(start.date()) == "2025-09-16"
        assert str(end.date()) == "2025-09-18"

    def test_cross_year_multi_day_metadata_is_rejected_clearly(self):
        with pytest.raises(SystemExit, match="helt inom 2025"):
            require_historical_demand({
                "source": "historical", "start_date": "2025-12-31",
                "end_date_exclusive": "2026-01-02", "days": 2,
            })

    def test_forecast_demand_exits_before_assignment_or_sumo(self, monkeypatch):
        meta = {"source": "forecast", "date": "2027-09-16"}
        monkeypatch.setattr(validate_sim, "load_inputs",
                            lambda: ({}, meta, {}, {}))
        monkeypatch.setattr(
            validate_sim, "compute_assignment_load",
            lambda: pytest.fail("LOSO started assignment loading for forecast demand"),
        )
        monkeypatch.setattr(sys, "argv", ["validate_sim.py"])

        with pytest.raises(SystemExit, match="HISTORISK demand"):
            validate_sim.main()


class TestDemandThroughShareTarget:
    def test_reads_shipped_build_option(self):
        assert demand_through_share_target({
            "build_options": {"through_share_target": 0.25},
        }) == pytest.approx(0.25)

    def test_legacy_build_without_option_keeps_emergent_mix(self):
        assert demand_through_share_target({"build_options": {}}) is None

    def test_invalid_metadata_fails_closed(self):
        with pytest.raises(SystemExit, match="through_share_target"):
            demand_through_share_target({
                "build_options": {"through_share_target": 1.0},
            })


class TestRunMeso:
    def test_uses_production_limited_junction_control(self, tmp_path, monkeypatch):
        sumo_dir = tmp_path / "sumo"
        sumo_dir.mkdir()
        calls = []
        monkeypatch.setattr(validate_sim, "SUMO_DIR", sumo_dir)
        monkeypatch.setattr(validate_sim, "sumo_home", lambda: tmp_path / "sumo-home")
        monkeypatch.setattr(validate_sim.subprocess, "run",
                            lambda cmd, **kwargs: calls.append(cmd))

        validate_sim.run_meso(sumo_dir / "routes.xml", sumo_dir / "edge.xml", 900)

        cmd = calls[0]
        assert cmd[cmd.index("--meso-junction-control") + 1] == "true"
        assert cmd[cmd.index("--meso-junction-control.limited") + 1] == "true"
