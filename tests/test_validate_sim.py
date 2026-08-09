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

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import validate_sim
from validate_sim import (assignment_edges_released_for_fold,
                          compute_assignment_load_for_fold,
                          controlled_publication_counts,
                          corridor_priors_for_fold, demand_through_share_target,
                          held_integer_publication_bounds,
                          held_out_evaluation_series,
                          include_held_assignment_flows,
                          require_historical_demand, resolve_evaluation_window,
                          select_station_folds)
from traffic_sim.demand import pfe
from traffic_sim.demand.pfe import Candidate


def make_corridor(from_edge, to_edge, prior, band):
    return {"mid_edge": {"from_sensor_edge": from_edge, "to_sensor_edge": to_edge,
                         "prior": prior, "band": band}}


class TestSelectStationFolds:
    def test_subset_keeps_deterministic_order(self):
        sensors = {"107": ["a", "b"], "134": ["c"], "2276": ["d"]}
        assert select_station_folds(sensors, ["2276", "107"]) == ["107", "2276"]

    def test_default_selects_all(self):
        assert select_station_folds({"b": [], "a": []}, None) == ["a", "b"]

    def test_unknown_station_fails_closed(self):
        with pytest.raises(ValueError, match="unknown diagnostic station"):
            select_station_folds({"107": ["a"]}, ["999"])

    def test_duplicate_station_fails_closed(self):
        with pytest.raises(ValueError, match="duplicates"):
            select_station_folds({"107": ["a"]}, ["107", "107"])


class TestHeldIntegerPublicationBounds:
    def test_selects_only_held_assignment_edges(self):
        bounds = [
            {"held": (0.0, 5.0), "other": (0.0, 99.0)},
            {"held": (0.0, 7.0)},
        ]

        selected = held_integer_publication_bounds(bounds, ["held"])

        assert selected == [
            {"held": (0.0, 5.0)},
            {"held": (0.0, 7.0)},
        ]
        assert "other" in bounds[0]

    def test_returns_none_when_assignment_has_no_held_ceiling(self):
        assert held_integer_publication_bounds(
            [{"other": (0.0, 99.0)}, {}], ["held"]) is None


class TestControlledPublicationCounts:
    def test_uses_joint_rounder_and_always_restores_production_helper(self):
        shapes = [
            Candidate(0.0, ["A"]),
            Candidate(0.0, ["A", "B"]),
            Candidate(0.0, ["B"]),
            Candidate(0.0, ["X"]),
        ]
        original = pfe.round_preserving_measured

        precomputed, report = controlled_publication_counts(
            shapes,
            [np.asarray([0.6, 0.4, 0.6, 0.4])],
            [{"A": 1.0, "B": 1.0}],
            [{}],
            [pfe.RUNG_CLEAN],
            [{}],
            enforce_integer_bounds=False,
            structure_groups=[],
        )

        counts, purpose_enforced = precomputed[0]
        assert counts.sum() == 2
        assert counts[0] + counts[1] == 1
        assert counts[1] + counts[2] == 1
        assert purpose_enforced is False
        assert report["method_counts"] == {"production_joint_projection": 1}
        assert report["evidence_class"] == "production_joint_projection"
        assert report["max_abs_active_measurement_residual"] == 0
        assert pfe.round_preserving_measured is original


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


class TestFoldAssignmentField:
    def test_structural_load_sees_held_station_as_unmeasured(self, monkeypatch):
        original = validate_sim.assignment_prior_model.load_sensor_edges
        seen = []

        def fake_compute(**config):
            seen.append(validate_sim.assignment_prior_model.load_sensor_edges())
            assert config == {"seed": 7}
            return {"held_edge": 2.0}

        monkeypatch.setattr(validate_sim, "compute_assignment_load", fake_compute)
        result = compute_assignment_load_for_fold(
            "held", {"kept": ["kept_edge"], "held": ["held_edge"]},
            {"seed": 7})

        assert result == {"held_edge": 2.0}
        assert seen == [{"kept": ["kept_edge"]}]
        assert validate_sim.assignment_prior_model.load_sensor_edges is original

    def test_structural_load_override_restores_after_failure(self, monkeypatch):
        original = validate_sim.assignment_prior_model.load_sensor_edges
        monkeypatch.setattr(
            validate_sim, "compute_assignment_load",
            lambda **_config: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            compute_assignment_load_for_fold(
                "held", {"kept": ["kept_edge"], "held": ["held_edge"]})
        assert validate_sim.assignment_prior_model.load_sensor_edges is original

    def test_held_edge_is_formatted_like_an_unmeasured_edge(self, monkeypatch):
        monkeypatch.setattr(
            validate_sim.assignment_prior_model, "daily_shape",
            lambda: [0.25, 0.75],
        )
        result = include_held_assignment_flows(
            {"weight": 0.15, "scale_veh_per_day": 10.0,
             "flows": {"ordinary": [1.0, 2.0]}},
            {"held_edge": 2.0, "zero_edge": 0.0},
            ["held_edge", "zero_edge"],
        )

        assert result["flows"]["ordinary"] == [1.0, 2.0]
        assert result["flows"]["held_edge"] == [5.0, 15.0]
        assert "zero_edge" not in result["flows"]

    def test_all_constraints_derived_from_held_sensor_are_released(self):
        released = assignment_edges_released_for_fold(
            "held", ["measured"],
            {
                "reverse": {"sensor": "held"},
                "other_reverse": {"sensor": "other"},
            },
            {
                "held_corridor": {
                    "from_sensor_edge": "measured",
                    "to_sensor_edge": "other_measured",
                },
                "other_corridor": {
                    "from_sensor_edge": "other_measured",
                    "to_sensor_edge": "third_measured",
                },
            },
            {
                "measured": "held",
                "other_measured": "other",
                "third_measured": "third",
            },
        )
        assert released == ["held_corridor", "measured", "reverse"]


class TestHeldOutMeasurementSemantics:
    def test_two_way_total_is_measured_once_and_simulated_directions_are_summed(self):
        key, measured, simulated = held_out_evaluation_series(
            {"east": [10, None, 30], "west": [10, None, 30]},
            {"east": [4, 5, 12], "west": [6, 7, 18]},
            ["east", "west"],
            "two_way_total",
        )

        assert key == "station_total"
        assert measured.tolist()[:1] == [10.0]
        assert np.isnan(measured[1])
        assert measured[2] == 30.0
        assert simulated.tolist() == [10.0, 12.0, 30.0]

    def test_two_way_total_rejects_nonduplicated_source_values(self):
        with pytest.raises(ValueError, match="source values differ"):
            held_out_evaluation_series(
                {"east": [10, 20], "west": [11, 20]},
                {"east": [4, 8], "west": [6, 12]},
                ["east", "west"],
                "two_way_total",
            )

    def test_directional_sensor_remains_an_edge_level_comparison(self):
        key, measured, simulated = held_out_evaluation_series(
            {"north": [10, 20]}, {"north": [9, 22]},
            ["north"], "directional")

        assert key == "north"
        assert measured.tolist() == [10.0, 20.0]
        assert simulated.tolist() == [9.0, 22.0]


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


class TestTemporalHoldoutWindow:
    META = {
        "source": "historical", "date": "2025-09-16",
        "start_date": "2025-09-16", "end_date_exclusive": "2025-09-17",
        "days": 1, "begin": "00:00", "end": "24:00",
    }

    def test_distinct_same_class_day_moves_window_without_changing_duration(self):
        ref_start, ref_end, start, end, mode = resolve_evaluation_window(
            self.META, "2025-09-17")

        assert str(ref_start) == "2025-09-16 00:00:00"
        assert str(ref_end) == "2025-09-17 00:00:00"
        assert str(start) == "2025-09-17 00:00:00"
        assert str(end) == "2025-09-18 00:00:00"
        assert mode == "temporal_holdout"

    def test_default_preserves_same_window_loso(self):
        ref_start, ref_end, start, end, mode = resolve_evaluation_window(
            self.META, None)

        assert (start, end) == (ref_start, ref_end)
        assert mode == "same_window_loso"

    def test_reference_day_is_not_a_temporal_holdout(self):
        with pytest.raises(SystemExit, match="annan dag"):
            resolve_evaluation_window(self.META, "2025-09-16")

    def test_different_day_class_is_rejected(self):
        with pytest.raises(SystemExit, match="samma dagtyp"):
            resolve_evaluation_window(self.META, "2025-09-20")

    def test_malformed_date_is_rejected(self):
        with pytest.raises(SystemExit, match="YYYY-MM-DD"):
            resolve_evaluation_window(self.META, "17-09-2025")


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
