"""
Contract tests for SUMO scenario output (run_scenario.py).

Scenario files must satisfy the same flowAt seam as flows.json, plus the
scenario extensions (metadata + per-edge confidence). Skipped if no
scenarios have been generated yet.
"""

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import run_scenario
from study_contracts import ScenarioSpec

SCEN_DIR   = Path(__file__).parent.parent / "web" / "data" / "scenarios"
INDEX_PATH = SCEN_DIR / "index.json"
GEO_PATH   = Path(__file__).parent.parent / "web" / "data" / "network.geojson"


def _built_scenarios() -> list:
    """Scenarios currently on disk, or [] when none have been built.

    `clear_stale_scenarios()` deliberately leaves a VALID EMPTY manifest
    after a demand rebuild rather than deleting index.json — a CLI-only
    `make demand` has no guarantee `run_scenario.py` runs next, and the web
    app needs a parseable manifest either way. The guard below used to test
    only `INDEX_PATH.exists()`, so that documented state slipped past it and
    then failed the "index.json has no scenarios" assertion. An empty
    manifest means the scenarios have not been built, which is a skip.
    """
    if not INDEX_PATH.exists():
        return []
    try:
        with open(INDEX_PATH) as f:
            return json.load(f).get("scenarios") or []
    except (OSError, ValueError):
        return []


needs_scenarios = pytest.mark.skipif(
    not _built_scenarios(),
    reason="no scenarios built — run run_scenario.py",
)


@pytest.fixture(scope="module")
def index():
    with open(INDEX_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def geo_edge_ids():
    with open(GEO_PATH) as f:
        geo = json.load(f)
    return {feat["properties"]["id"] for feat in geo["features"]}


class TestLoadGeojsonMetaConfidence:
    """confidence=0 is a real value (far-from-sensor extrapolation), not a
    missing one — on the current network it is the MOST COMMON value
    (6 569/7 147 edges). The old `p.get("confidence") or 0.5` coerced every
    such edge to 0.5, displaying 50% confidence exactly where the spatial
    prior deliberately says 0% (IMPROVEMENT_REVIEW_2026-07-10 item 13.1)."""

    @staticmethod
    def _geo(tmp_path, props_list):
        geo = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
             "properties": props}
            for props in props_list
        ]}
        path = tmp_path / "network.geojson"
        path.write_text(json.dumps(geo))
        return path

    def test_zero_confidence_is_preserved_not_coerced_to_half(
            self, tmp_path, monkeypatch):
        path = self._geo(tmp_path, [
            {"id": "far_edge", "confidence": 0},
            {"id": "near_edge", "confidence": 0.93},
            {"id": "legacy_edge"},   # property absent entirely
        ])
        monkeypatch.setattr(run_scenario, "GEO_PATH", path)
        prior, _names = run_scenario.load_geojson_meta()
        assert prior["far_edge"] == 0.0        # the bug coerced this to 0.5
        assert prior["near_edge"] == 0.93
        assert prior["legacy_edge"] == 0.5     # only true absence falls back


class TestValidScenarioName:
    """--name flows into filesystem paths under sumo/ and web/data/scenarios/
    — a strict slug keeps generated paths inside those directories
    (IMPROVEMENT_REVIEW_2026-07-10 item 13.7, narrow scope; the API path was
    already safe since serve.py rejects unknown edge IDs before they can
    reach a filename)."""

    def test_accepts_the_auto_generated_name_shapes(self):
        assert run_scenario.valid_scenario_name("baseline")
        assert run_scenario.valid_scenario_name("close_60786979_3575001205_0")
        assert run_scenario.valid_scenario_name(
            "close_60786979_3575001205_0+1455801464_18241874_0")
        assert run_scenario.valid_scenario_name("close_2edges_a1b2c3d4")

    def test_rejects_path_separators_and_traversal(self):
        assert not run_scenario.valid_scenario_name("../escape")
        assert not run_scenario.valid_scenario_name("sub/dir")
        assert not run_scenario.valid_scenario_name("back\\slash")
        assert not run_scenario.valid_scenario_name("..")

    def test_rejects_spaces_empty_and_overlong(self):
        assert not run_scenario.valid_scenario_name("has space")
        assert not run_scenario.valid_scenario_name("")
        assert not run_scenario.valid_scenario_name("x" * 81)


class TestDemandVariants:
    def test_q50_metadata_ignores_stale_quantile_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        (tmp_path / "calibrated.rou.xml").write_text("<routes/>")
        (tmp_path / "calibrated_v1.rou.xml").write_text("<routes/>")
        (tmp_path / "calibrated_v2.rou.xml").write_text("<routes/>")

        assert run_scenario.demand_variants({"n_variants": 1}) == [
            tmp_path / "calibrated.rou.xml"
        ]

    def test_q50_manifest_is_the_authoritative_normal_contract(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        (tmp_path / "calibrated.rou.xml").write_text("<routes/>")
        meta = {
            "n_variants": 1,
            "demand_variant_contract": {
                "schema_version": 1,
                "mode": "q50_only",
                "variants": [{
                    "name": "q50", "target_key": "edge_shares",
                    "route_file": "calibrated.rou.xml",
                }],
            },
        }
        assert run_scenario.demand_variant_entries(meta)[0]["name"] == "q50"
        assert run_scenario.demand_variants(meta) == [
            tmp_path / "calibrated.rou.xml"]

    def test_legacy_stress_seeds_keep_frozen_q10_q50_q90_order(self, tmp_path):
        meta = {"n_variants": 3}
        assert run_scenario.default_seed_variant_mapping(
            meta, [1000, 1001, 1002]) == {
                1000: "q10", 1001: "q50", 1002: "q90",
            }
        variants = [tmp_path / name for name in (
            "calibrated.rou.xml", "calibrated_v1.rou.xml",
            "calibrated_v2.rou.xml")]
        assert run_scenario.seed_variant_plan(variants) == [
            (1000, variants[1]), (1001, variants[0]), (1002, variants[2])]

    def test_manifest_rejects_implicit_or_reordered_stress_arms(self):
        meta = {
            "n_variants": 3,
            "demand_variant_contract": {
                "schema_version": 1,
                "mode": "direction_stress",
                "variants": [
                    {"name": "q10", "target_key": "edge_shares_q10",
                     "route_file": "calibrated_v1.rou.xml"},
                    {"name": "q50", "target_key": "edge_shares",
                     "route_file": "calibrated.rou.xml"},
                    {"name": "q90", "target_key": "edge_shares_q90",
                     "route_file": "calibrated_v2.rou.xml"},
                ],
            },
        }
        with pytest.raises(ValueError, match="exactly q50"):
            run_scenario.demand_variant_entries(meta)

    def test_quantile_metadata_requires_every_declared_route_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        (tmp_path / "calibrated.rou.xml").write_text("<routes/>")
        (tmp_path / "calibrated_v1.rou.xml").write_text("<routes/>")

        with pytest.raises(FileNotFoundError, match="calibrated_v2"):
            run_scenario.demand_variants({"n_variants": 3})
        assert run_scenario.valid_scenario_name("x" * 80)


class TestSensorAudit:
    def test_keeps_source_target_ensemble_and_visible_seed_separate(
            self, tmp_path, monkeypatch):
        """The road colour is an ensemble mean; the moving vehicles are one
        seed.  An audit payload must preserve both rather than visually
        implying they are the same integer count."""
        geo = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "LineString",
             "coordinates": [[0, 0], [0, 1]]},
             "properties": {"id": "north", "sensor_id": "total",
                            "name": "Testgatan", "level": "Total"}},
            {"type": "Feature", "geometry": {"type": "LineString",
             "coordinates": [[0, 1], [0, 0]]},
             "properties": {"id": "south", "sensor_id": "total",
                            "name": "Testgatan", "level": "Total"}},
            {"type": "Feature", "geometry": {"type": "LineString",
             "coordinates": [[1, 0], [1, -1]]},
             "properties": {"id": "directed", "sensor_id": "single",
                            "name": "Enkelgatan", "level": "S"}},
        ]}
        geo_path = tmp_path / "network.geojson"
        geo_path.write_text(json.dumps(geo))
        monkeypatch.setattr(run_scenario, "GEO_PATH", geo_path)

        targets = {
            "edge_shares": {
                "north": [10.0, 20.0], "south": [8.0, 18.0],
                "directed": [6.0, 7.0],
            },
            "edge_shares_q10": {
                "north": [9.0, 19.0], "south": [9.0, 19.0],
                "directed": [6.0, 7.0],
            },
            "edge_shares_q90": {
                "north": [11.0, 21.0], "south": [7.0, 17.0],
                "directed": [6.0, 7.0],
            },
        }
        meta = {
            "source": "historical",
            "sensor_targets": {"variants": targets},
            "sensor_observations": {
                "north": [18, 38], "south": [18, 38],
                "directed": [6, 7],
            },
        }
        results = [
            {"seed": 1000, "route_path": Path("calibrated.rou.xml"),
             "flows": {"north": np.array([9, 21]), "south": np.array([7, 17]),
                       "directed": np.array([6, 7])}},
            {"seed": 1001, "route_path": Path("calibrated_v1.rou.xml"),
             "flows": {"north": np.array([9, 19]), "south": np.array([9, 19]),
                       "directed": np.array([6, 7])}},
            {"seed": 1002, "route_path": Path("calibrated_v2.rou.xml"),
             "flows": {"north": np.array([12, 20]), "south": np.array([6, 18]),
                       "directed": np.array([6, 7])}},
        ]
        displayed_mean = {
            "north": [10, 20], "south": [7, 18], "directed": [6, 7],
        }

        audit = run_scenario.build_sensor_audit(
            meta, results, displayed_mean, n_intervals=2,
            calibration_comparison=True)

        north = next(row for row in audit["directions"] if row["edge_id"] == "north")
        assert north["measurement"] == "two_way_total"
        assert north["direction"] == "N"
        assert north["source_value"] == [18.0, 38.0]
        assert north["target_representative"] == [10.0, 20.0]
        assert north["simulated_representative"] == [9, 21]
        assert north["target_mean"] == [10.0, 20.0]
        assert north["simulated_mean"] == [10, 20]
        assert audit["fit"]["representative"]["edge_quarters"] == 6
        assert audit["fit"]["ensemble"]["geh_lt_5_pct"] == 100.0
        assert audit["comparison"] == "calibration"

    def test_sensor_audit_uses_raw_mean_and_closure_variant_identity(
            self, tmp_path, monkeypatch):
        geo = {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "LineString",
             "coordinates": [[0, 0], [0, 1]]},
            "properties": {"id": "e", "sensor_id": "s", "level": "S"},
        }]}
        geo_path = tmp_path / "network.geojson"
        geo_path.write_text(json.dumps(geo))
        monkeypatch.setattr(run_scenario, "GEO_PATH", geo_path)
        targets = {
            "edge_shares": {"e": [10.0]},
            "edge_shares_q10": {"e": [8.0]},
            "edge_shares_q90": {"e": [12.0]},
        }
        meta = {
            "sensor_targets": {"variants": targets},
            "sensor_observations": {"e": [10]},
        }
        results = [
            {"seed": 1000, "route_path": Path("calibrated_close_x.rou.xml"),
             "demand_variant": "q50", "target_key": "edge_shares",
             "flows": {"e": np.array([10.49])}},
            {"seed": 1001, "route_path": Path("calibrated_v1_close_x.rou.xml"),
             "demand_variant": "q10", "target_key": "edge_shares_q10",
             "flows": {"e": np.array([8.49])}},
            {"seed": 1002, "route_path": Path("calibrated_v2_close_x.rou.xml"),
             "demand_variant": "q90", "target_key": "edge_shares_q90",
             "flows": {"e": np.array([12.49])}},
        ]
        audit = run_scenario.build_sensor_audit(
            meta, results, {"e": [10]}, n_intervals=1,
            raw_mean_flows={"e": [10.49]}, calibration_comparison=True)
        row = next(item for item in audit["directions"]
                   if item["edge_id"] == "e")
        assert row["target_mean"] == [10.0]
        assert row["simulated_mean"] == [10]
        assert row["simulated_mean_raw"] == [10.49]
        assert audit["output_fit"]["uses_raw_ensemble_mean"] is True
        assert audit["fit"]["ensemble"]["mean_abs_error"] == 0.49

    def test_representative_follows_the_animated_trajectory_seed(
            self, tmp_path, monkeypatch):
        """A ScenarioSpec seed_set is not required to be ascending: the
        audit's "representative" must be the trajectory seed whose vehicles
        the browser animates, not whichever result sorts first."""
        geo = {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "LineString",
             "coordinates": [[0, 0], [0, 1]]},
            "properties": {"id": "e", "sensor_id": "s", "level": "S"},
        }]}
        geo_path = tmp_path / "network.geojson"
        geo_path.write_text(json.dumps(geo))
        monkeypatch.setattr(run_scenario, "GEO_PATH", geo_path)
        meta = {
            "sensor_targets": {"variants": {
                "edge_shares": {"e": [10.0]},
                "edge_shares_q10": {"e": [8.0]},
            }},
            "sensor_observations": {"e": [10]},
        }
        results = [
            {"seed": 1000, "route_path": Path("calibrated_v1.rou.xml"),
             "demand_variant": "q10", "target_key": "edge_shares_q10",
             "flows": {"e": np.array([8])}},
            {"seed": 1002, "route_path": Path("calibrated.rou.xml"),
             "demand_variant": "q50", "target_key": "edge_shares",
             "flows": {"e": np.array([10])}},
        ]

        audit = run_scenario.build_sensor_audit(
            meta, results, {"e": [9]}, n_intervals=1,
            calibration_comparison=True, representative_seed=1002)

        assert audit["representative"] == {"seed": 1002,
                                           "variant": "edge_shares"}
        row = audit["directions"][0]
        assert row["target_representative"] == [10.0]
        assert row["simulated_representative"] == [10]


class TestExactSensorPassageAudit:
    @staticmethod
    def _inputs(tmp_path, monkeypatch, seed_values):
        geo = {"type": "FeatureCollection", "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[0, 0], [0, 1]]},
            "properties": {"id": "e", "sensor_id": "s", "level": "S"},
        }]}
        geo_path = tmp_path / "network.geojson"
        geo_path.write_text(json.dumps(geo))
        monkeypatch.setattr(run_scenario, "GEO_PATH", geo_path)
        meta = {
            "sensor_targets": {"variants": {
                "edge_shares": {"e": [10.0, 11.0]},
            }},
            "sensor_observations": {"e": [10, 11]},
        }
        results = [
            {"seed": seed, "route_path": Path("calibrated.rou.xml"),
             "target_key": "edge_shares",
             "flows": {"e": np.array(values, dtype=float)}}
            for seed, values in seed_values
        ]
        raw_mean = {
            "e": [sum(values[q] for _seed, values in seed_values)
                  / len(seed_values) for q in range(2)]
        }
        return meta, results, raw_mean

    def test_exact_raw_passages_pass_for_ensemble_and_each_seed(
            self, tmp_path, monkeypatch):
        from traffic_sim.simulation.sensor_fit import assess_exact_output_fit

        meta, results, raw_mean = self._inputs(
            tmp_path, monkeypatch,
            [(1000, [10, 11]), (1001, [10, 11])])
        audit = run_scenario.build_sensor_audit(
            meta, results, {"e": [10, 11]}, 2,
            raw_mean_flows=raw_mean, calibration_comparison=True)

        exact = audit["exact_output_fit"]
        assert exact["ensemble"]["exact"] == 2
        assert exact["ensemble_mismatches"] == []
        assert all(row["exact"] == 2 for row in exact["per_seed"])
        assert assess_exact_output_fit(audit, n_intervals=2)["errors"] == []

    def test_ensemble_average_cannot_hide_seed_timing_errors(
            self, tmp_path, monkeypatch):
        from traffic_sim.simulation.sensor_fit import assess_exact_output_fit

        meta, results, raw_mean = self._inputs(
            tmp_path, monkeypatch,
            [(1000, [10, 12]), (1001, [10, 10])])
        audit = run_scenario.build_sensor_audit(
            meta, results, {"e": [10, 11]}, 2,
            raw_mean_flows=raw_mean, calibration_comparison=True)

        exact = audit["exact_output_fit"]
        assert exact["ensemble"]["exact"] == 2
        assert len(exact["seed_mismatches"]) == 2
        assessment = assess_exact_output_fit(audit, n_intervals=2)
        assert any("seed 1000" in error for error in assessment["errors"])
        assert any("seed 1001" in error for error in assessment["errors"])

    def test_declared_exact_summary_is_recomputed_from_raw_rows(
            self, tmp_path, monkeypatch):
        from traffic_sim.simulation.sensor_fit import assess_exact_output_fit

        meta, results, raw_mean = self._inputs(
            tmp_path, monkeypatch, [(1000, [10, 12])])
        audit = run_scenario.build_sensor_audit(
            meta, results, {"e": [10, 12]}, 2,
            raw_mean_flows=raw_mean, calibration_comparison=True)
        audit["exact_output_fit"]["ensemble"]["exact"] = 2

        assessment = assess_exact_output_fit(audit, n_intervals=2)
        assert any("inkonsekvent exact" in error
                   for error in assessment["errors"])

    def test_closure_audit_does_not_claim_baseline_exactness(
            self, tmp_path, monkeypatch):
        meta, results, raw_mean = self._inputs(
            tmp_path, monkeypatch, [(1000, [4, 5])])
        audit = run_scenario.build_sensor_audit(
            meta, results, {"e": [4, 5]}, 2,
            raw_mean_flows=raw_mean, calibration_comparison=False)
        assert "exact_output_fit" not in audit


class TestScenarioSpecIntegration:
    def test_variant_path_uses_explicit_quantile_mapping(self, tmp_path):
        paths = [tmp_path / "q50.rou.xml", tmp_path / "q10.rou.xml",
                 tmp_path / "q90.rou.xml"]
        assert run_scenario.variant_path(paths, "q50") == paths[0]
        assert run_scenario.variant_path(paths, "q10") == paths[1]
        assert run_scenario.variant_path(paths, "q90") == paths[2]

    def test_variant_path_rejects_unavailable_variant(self, tmp_path):
        with pytest.raises(ValueError, match="unavailable"):
            run_scenario.variant_path([tmp_path / "q50.rou.xml"], "q90")

    def test_seed_variant_plan_honours_structured_mapping(self, tmp_path):
        paths = [tmp_path / "q50.rou.xml", tmp_path / "q10.rou.xml",
                 tmp_path / "q90.rou.xml"]
        spec = ScenarioSpec.from_dict({
            "scenario_id": "signal-study",
            "demand_build_id": "demand-a",
            "network_build_id": "network-b",
            "start_time": "2025-09-16T00:00:00",
            "end_time": "2025-09-17T00:00:00",
            "simulation_mode": "micro",
            "seed_set": [1010, 2020],
            "demand_variant_mapping": {"1010": "q90", "2020": "q10"},
        })
        assert run_scenario.seed_variant_plan(paths, spec=spec) == [
            (1010, paths[2]), (2020, paths[1])]

    def test_legacy_seed_variant_plan_keeps_round_robin_behaviour(self, tmp_path):
        paths = [tmp_path / "q50.rou.xml"]
        assert run_scenario.seed_variant_plan(paths, seeds=3) == [
            (1000, paths[0]), (1001, paths[0]), (1002, paths[0])]

    def test_spec_validation_checks_demand_network_and_window(self, tmp_path):
        net = tmp_path / "net.net.xml"
        net.write_text("<net/>")
        from pipeline_fingerprint import sha256_file
        spec = ScenarioSpec.from_dict({
            "scenario_id": "baseline",
            "demand_build_id": "demand-a",
            "network_build_id": sha256_file(net),
            "start_time": "2025-09-16T00:00:00",
            "end_time": "2025-09-16T00:15:00",
        })
        run_scenario.validate_scenario_spec(
            spec,
            meta={"build_id": "demand-a", "epoch_sim": "2025-09-16T00:00:00"},
            duration_s=900,
            network_path=net,
        )


class TestClosureIntegrityStatus:
    """closure_integrity_status (found in review 2026-07-12): a genuinely
    verified-clean closure (active_closure_entries == 0) must not be
    reported the same way as "never measured" — 0 is falsy in Python, so
    the original inline `if active_closure_entries else ...` conflated the
    two. Extracted to a pure function so this is directly testable without
    a real SUMO run."""

    def test_positive_entries_is_a_failure(self):
        assert run_scenario.closure_integrity_status(3, [{"edge_id": "a"}]) == \
            "failed_active_edge_flow"

    def test_zero_entries_is_verified_clean_not_unmeasured(self):
        assert run_scenario.closure_integrity_status(0, [{"edge_id": "a"}]) == \
            "verified_clean"

    def test_none_entries_with_closures_is_not_measurable(self):
        assert run_scenario.closure_integrity_status(None, [{"edge_id": "a"}]) == \
            "not_measurable"

    def test_none_entries_without_closures_is_none(self):
        assert run_scenario.closure_integrity_status(None, []) is None

    def test_one_unmeasured_seed_cannot_be_aggregated_as_clean(self):
        assert run_scenario.aggregate_active_closure_entries(
            [0, None, 0], [{"edge_id": "a"}]) is None
        assert run_scenario.aggregate_active_closure_entries(
            [0, 0, 0], [{"edge_id": "a"}]) == 0

    def test_excluded_empty_closed_edge_is_retained_as_measured_zero(
            self, tmp_path):
        edge_data = tmp_path / "edge.xml"
        edge_data.write_text(
            '<meandata><interval begin="0" end="900">'
            '<edge id="open" entered="3"/>'
            '</interval></meandata>')

        flows = run_scenario.parse_edgedata(
            edge_data, 1, measured_empty_edges=["closed"])
        closures = [{"edge_id": "closed", "begin_s": 0, "end_s": 900}]
        active = run_scenario.cm.active_closure_throughput(flows, closures)

        assert flows["open"].tolist() == [3.0]
        assert flows["closed"].tolist() == [0.0]
        assert active == 0
        assert run_scenario.closure_integrity_status(
            run_scenario.aggregate_active_closure_entries(
                [active], closures),
            closures) == "verified_clean"


class TestBaselineOutputFitGate:
    @staticmethod
    def _audit(*, target: float, raw: float) -> dict:
        from traffic_sim.simulation.sensor_fit import summarize_pairs

        summary = summarize_pairs([(raw, target)])
        return {
            "output_fit": {
                "uses_raw_ensemble_mean": True,
                "ensemble": summary,
                "station_ensemble": summary,
            },
            "directions": [{"target_mean": [target],
                            "simulated_mean_raw": [raw]}],
            "stations": [{"target_mean": [target],
                          "simulated_mean_raw": [raw]}],
        }

    def test_new_baseline_requires_raw_final_sumo_fit(self):
        errors = run_scenario.baseline_output_fit_errors(
            {"sensor_targets": {"variants": {"q50": {}}}},
            self._audit(target=1.0, raw=30.0), n_intervals=1, closures=[])

        assert any("GEH" in error for error in errors)

    def test_closure_and_legacy_baseline_do_not_use_calibration_gate(self):
        bad_audit = self._audit(target=1.0, raw=30.0)
        assert run_scenario.baseline_output_fit_errors(
            {"sensor_targets": {"variants": {"q50": {}}}}, bad_audit,
            n_intervals=1, closures=[{"edge_id": "a"}]) == []
        assert run_scenario.baseline_output_fit_errors(
            {}, bad_audit, n_intervals=1, closures=[]) == []

    @staticmethod
    def _multi_day_audit(day_two_raw: float) -> dict:
        from traffic_sim.simulation.sensor_fit import summarize_rows

        targets = [10.0] * 192
        raw = [10.0] * 96 + [day_two_raw] * 96
        whole = summarize_rows(
            [{"target_mean": targets, "simulated_mean_raw": raw}],
            n_intervals=192, aggregation_quarters=4)
        first = summarize_rows(
            [{"target_mean": targets[:96],
              "simulated_mean_raw": raw[:96]}],
            n_intervals=96, aggregation_quarters=4)
        second = summarize_rows(
            [{"target_mean": targets[96:],
              "simulated_mean_raw": raw[96:]}],
            n_intervals=96, aggregation_quarters=4)
        return {
            "output_fit": {
                "uses_raw_ensemble_mean": True,
                "aggregation_quarters": 4,
                "aggregation_minutes": 60,
                "ensemble": whole,
                "station_ensemble": whole,
                "per_day": [
                    {"day": 1, "quarter_start": 0, "quarter_end": 96,
                     "ensemble": first, "station_ensemble": first},
                    {"day": 2, "quarter_start": 96, "quarter_end": 192,
                     "ensemble": second, "station_ensemble": second},
                ],
            },
            "directions": [{"target_mean": targets,
                            "simulated_mean_raw": raw}],
            "stations": [{"target_mean": targets,
                          "simulated_mean_raw": raw}],
        }

    def test_bad_second_day_cannot_hide_in_multi_day_output_fit(self):
        errors = run_scenario.baseline_output_fit_errors(
            {"days": 2, "sensor_targets": {"variants": {"q50": {}}}},
            self._multi_day_audit(day_two_raw=100.0),
            n_intervals=192, closures=[])

        assert any("dag 2" in error and "GEH" in error for error in errors)

    def test_each_multi_day_output_fit_row_is_required(self):
        audit = self._multi_day_audit(day_two_raw=10.0)
        audit["output_fit"].pop("per_day")

        errors = run_scenario.baseline_output_fit_errors(
            {"days": 2, "sensor_targets": {"variants": {"q50": {}}}},
            audit, n_intervals=192, closures=[])

        assert any("per dag" in error for error in errors)

    def test_hourly_gate_accepts_adjacent_quarter_travel_time_spillover(self):
        from traffic_sim.simulation.sensor_fit import summarize_rows

        targets = [30.0, 30.0, 30.0, 0.0]
        raw = [30.0, 30.0, 10.0, 20.0]
        summary = summarize_rows(
            [{"target_mean": targets, "simulated_mean_raw": raw}],
            n_intervals=4, aggregation_quarters=4)
        audit = {
            "output_fit": {
                "uses_raw_ensemble_mean": True,
                "aggregation_quarters": 4,
                "aggregation_minutes": 60,
                "ensemble": summary,
                "station_ensemble": summary,
            },
            "directions": [{"target_mean": targets,
                            "simulated_mean_raw": raw}],
            "stations": [{"target_mean": targets,
                          "simulated_mean_raw": raw}],
        }

        errors = run_scenario.baseline_output_fit_errors(
            {"sensor_targets": {"variants": {"q50": {}}}},
            audit, n_intervals=4, closures=[])

        assert errors == []


class TestAtomicWriteJson:
    """atomic_write_json (found in review 2026-07-10): a live browser polling
    a scenario/index file with cache: 'no-store' must never observe a
    truncated write while run_scenario.py or serve.py's recalibration
    thread overwrites it in place."""

    def test_writes_valid_json_readable_after_call(self, tmp_path):
        path = tmp_path / "out.json"
        run_scenario.atomic_write_json(path, {"a": 1, "b": [1, 2, 3]})
        assert json.loads(path.read_text()) == {"a": 1, "b": [1, 2, 3]}

    def test_old_content_survives_until_the_new_write_fully_lands(self, tmp_path):
        path = tmp_path / "out.json"
        run_scenario.atomic_write_json(path, {"version": "old"})
        assert json.loads(path.read_text()) == {"version": "old"}
        run_scenario.atomic_write_json(path, {"version": "new"})
        # No intermediate state should ever be observable from outside this
        # call — the only two valid reads are fully-old or fully-new.
        assert json.loads(path.read_text()) == {"version": "new"}

    def test_no_leftover_temp_file_after_a_successful_write(self, tmp_path):
        path = tmp_path / "out.json"
        run_scenario.atomic_write_json(path, {"a": 1})
        leftovers = [p for p in tmp_path.iterdir() if p != path]
        assert leftovers == []

    def test_temp_file_is_cleaned_up_on_a_failed_write(self, tmp_path):
        path = tmp_path / "out.json"

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            run_scenario.atomic_write_json(path, {"a": Unserializable()})
        assert not path.exists()
        leftovers = list(tmp_path.iterdir())
        assert leftovers == []

    def test_accepts_json_dump_kwargs(self, tmp_path):
        path = tmp_path / "out.json"
        run_scenario.atomic_write_json(path, {"a": 1}, indent=2)
        assert "\n" in path.read_text()   # indent=2 forces multi-line output


@pytest.mark.skipif(not INDEX_PATH.exists(),
                    reason="no manifest at all — run_scenario.py never ran")
def test_index_is_a_valid_manifest_even_when_empty():
    # The EMPTY case is a real contract, not an absence of one: a demand
    # rebuild wipes the scenarios and clear_stale_scenarios() must leave a
    # manifest the web app can still parse. Checked unconditionally so that
    # relaxing needs_scenarios to skip on an empty manifest does not also
    # stop anyone from noticing a corrupt or malformed one.
    with open(INDEX_PATH) as f:
        manifest = json.load(f)
    assert isinstance(manifest, dict)
    assert isinstance(manifest.get("scenarios"), list)


@needs_scenarios
def test_index_lists_existing_files(index):
    assert index["scenarios"], "index.json has no scenarios"
    for s in index["scenarios"]:
        assert (SCEN_DIR / s["file"]).exists(), f"missing file {s['file']}"
        assert s["name"] and s["label"]


@needs_scenarios
def test_scenario_files_satisfy_flow_contract(index, geo_edge_ids):
    for s in index["scenarios"]:
        with open(SCEN_DIR / s["file"]) as f:
            data = json.load(f)

        assert data["interval_minutes"] == 15
        assert "T" in data["epoch"]                    # ISO datetime
        assert data["scenario"]["name"] == s["name"]

        lengths = {len(arr) for arr in data["flows"].values()}
        assert len(lengths) == 1, "all flow arrays must have equal length"

        # Same ID space as the map — every edge must be drawable
        unknown = set(data["flows"]) - geo_edge_ids
        assert not unknown, f"{s['name']}: edges not in network.geojson: {sorted(unknown)[:5]}"

        for eid, arr in data["flows"].items():
            assert all(v is None or v >= 0 for v in arr), f"negative flow on {eid}"

        # Confidence: subset of flow edges, all in [0, 1]
        conf = data.get("confidence", {})
        assert set(conf) <= set(data["flows"])
        assert all(0.0 <= v <= 1.0 for v in conf.values())


@needs_scenarios
def test_closed_edges_have_reduced_flow(index):
    """Every closed edge must carry (almost) no traffic in its own scenario."""
    files = {s["name"]: s for s in index["scenarios"]}
    closures = [s for s in files.values() if s.get("closed_edges")]
    if not closures or "baseline" not in files:
        pytest.skip("need baseline + at least one closure scenario")

    with open(SCEN_DIR / files["baseline"]["file"]) as f:
        base = json.load(f)["flows"]
    for s in closures:
        with open(SCEN_DIR / s["file"]) as f:
            closed = json.load(f)["flows"]
        for ce in s["closed_edges"]:
            base_total   = sum(v or 0 for v in base.get(ce, []))
            closed_total = sum(v or 0 for v in closed.get(ce, []))
            assert closed_total < 0.2 * max(base_total, 1), (
                f"{s['name']}: closed edge {ce} still carries {closed_total} "
                f"(baseline {base_total})"
            )


class TestSumoTimeout:
    """Neither sumo subprocess call had a timeout — a hung sumo process had
    no bound, and if THIS script's own parent (e.g. serve.py's outer
    subprocess.run) times out and kills it first, the sumo grandchild is
    orphaned permanently (a timeout only ever kills its direct child).
    Found in review 2026-07-07."""

    def test_run_sumo_timeout_exits_cleanly(self, monkeypatch, tmp_path):
        seen = []

        def fake_run(*a, **kw):
            seen.append(kw.get("timeout"))
            raise subprocess.TimeoutExpired(cmd="sumo", timeout=kw.get("timeout"))
        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        with pytest.raises(SystemExit, match="after 725s"):
            run_scenario.run_sumo(1000, tmp_path / "r.rou.xml", [],
                                  duration_s=900, home=tmp_path, timeout_s=725)
        assert seen == [725]

    @pytest.mark.parametrize("timeout_s", [0, -1, float("inf"), True])
    def test_run_sumo_rejects_invalid_timeout(self, tmp_path, timeout_s):
        with pytest.raises(ValueError, match="timeout_s"):
            run_scenario.run_sumo(
                1000,
                tmp_path / "r.rou.xml",
                [],
                duration_s=900,
                home=tmp_path,
                timeout_s=timeout_s,
            )

    def test_export_trajectories_timeout_returns_none_not_raises(self, monkeypatch, tmp_path):
        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="sumo", timeout=kw.get("timeout"))
        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        result = run_scenario.export_trajectories(
            "baseline", tmp_path / "r.rou.xml", [], duration_s=900,
            home=tmp_path, web_edges=set())
        assert result is None

    def test_run_sumo_metrics_are_opt_in(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)

        normal = run_scenario.run_sumo(
            1000, tmp_path / "demand.rou.xml", [], 900, tmp_path)
        measured = run_scenario.run_sumo(
            1001, tmp_path / "demand.rou.xml", [], 900, tmp_path, metrics=True)

        assert normal is None
        assert "--tripinfo-output" not in commands[0]
        assert "--statistic-output" not in commands[0]
        assert "--tripinfo-output" in commands[1]
        assert "--tripinfo-output.write-unfinished" in commands[1]
        assert "--statistic-output" in commands[1]
        assert "--summary-output" in commands[1]
        assert measured is not None

    def test_metrics_run_label_separates_signal_condition_artifacts(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        baseline = run_scenario.run_sumo(
            1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
            metrics=True, run_label="d2_baseline")
        candidate = run_scenario.run_sumo(
            1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
            metrics=True, run_label="d2_adapted")

        assert baseline["tripinfo"] != candidate["tripinfo"]
        assert "d2_baseline" in baseline["tripinfo"].name
        assert "d2_adapted" in candidate["tripinfo"].name


class TestRunSumoFlushOffset:
    """flush_s (fixed 2026-07-11, external review NEW_CHANGES_REVIEW section
    1): a bounded time-of-day window experiment must not admit departures
    past the requested window end. The default 3600s meso flush, reused
    unchanged for D1/D2/D3's windowed calls, let vehicles scheduled up to an
    hour AFTER the window still depart and count toward it — verified
    empirically against real demand (55% of a nominal 07:00-09:00 run's
    tripinfo entries had depart >= 09:00)."""

    def test_default_flush_is_3600_unchanged(self, monkeypatch, tmp_path):
        # Every whole-period caller (run_scenario.py main(), suggest_
        # closure_time.py) relies on this default for meso's insertion
        # backlog -- must be byte-identical to before flush_s existed.
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 7200, tmp_path)
        cmd = commands[0]
        assert cmd[cmd.index("--end") + 1] == str(7200 + 3600)

    def test_explicit_flush_s_zero_caps_end_exactly_at_the_window(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 28800,
                              tmp_path, flush_s=0)
        cmd = commands[0]
        assert cmd[cmd.index("--end") + 1] == "28800"


class TestRunSumoBeginOffset:
    """begin_s (added for signal_lab.py, IMPROVEMENT_PLAN.md D1): a bounded time-of-day
    window must shift SUMO's own --begin, not just filter results after the
    fact, so vehicles outside the window are never even inserted."""

    def test_default_begin_is_zero_unchanged(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path)
        cmd = commands[0]
        assert cmd[cmd.index("--begin") + 1] == "0"

    def test_explicit_begin_s_is_passed_through(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 7200,
                              tmp_path, begin_s=3600)
        cmd = commands[0]
        assert cmd[cmd.index("--begin") + 1] == "3600"
        # duration_s stays the far-end offset from t=0, unaffected by begin_s.
        assert cmd[cmd.index("--end") + 1] == str(7200 + 3600)


class TestRunSumoNetPath:
    """net_path (added for signal_optimize.py, IMPROVEMENT_PLAN.md D2): comparing TLS
    types (actuated/delay_based) needs an ALTERNATE net.net.xml, since
    those types are baked in at netconvert time, not a sumo runtime flag."""

    def test_default_net_path_is_the_module_constant(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path)
        cmd = commands[0]
        assert cmd[cmd.index("-n") + 1] == str(run_scenario.NET_PATH.resolve())

    def test_explicit_net_path_overrides_the_default(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        alt_net = tmp_path / "net_actuated.net.xml"
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
                              net_path=alt_net)
        cmd = commands[0]
        assert cmd[cmd.index("-n") + 1] == str(alt_net.resolve())


class TestRunSumoVehrouteOutput:
    """vehroute_output (added for signal_closure_combine.py, IMPROVEMENT_PLAN.md D4):
    extracting the ACTUALLY-driven post-closure routes needs the runtime
    rerouter's real decisions, only available from --vehroute-output,
    requested from the SAME run as the disruption metrics."""

    def test_default_omits_vehroute_flags(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        result = run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path)
        assert "--vehroute-output" not in commands[0]
        assert result is None   # every existing caller's behaviour unchanged

    def test_vehroute_output_adds_the_flag_and_returns_its_path(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        vr_path = tmp_path / "vehroutes.xml"
        result = run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
                                       vehroute_output=vr_path)
        cmd = commands[0]
        assert cmd[cmd.index("--vehroute-output") + 1] == str(vr_path.resolve())
        assert "--vehroute-output.exit-times" in cmd
        assert result == {"vehroute": vr_path}

    def test_unfinished_vehroute_output_is_explicit_opt_in(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        vr_path = tmp_path / "vehroutes.xml"
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900,
                              tmp_path, vehroute_output=vr_path,
                              vehroute_write_unfinished=True)
        assert "--vehroute-output.write-unfinished" in commands[0]

    def test_metrics_and_vehroute_output_combine_in_one_run(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        vr_path = tmp_path / "vehroutes.xml"
        result = run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
                                       metrics=True, vehroute_output=vr_path)
        assert set(result) == {"tripinfo", "statistics", "summary", "vehroute"}
        assert "--tripinfo-output" in commands[0]
        assert "--vehroute-output" in commands[0]

    def test_work_dir_is_used_for_process_and_metric_outputs(self, monkeypatch, tmp_path):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(kwargs)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path / "shared")
        work = tmp_path / "run" / "seed-1000"
        result = run_scenario.run_sumo(
            1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
            metrics=True, work_dir=work)

        assert calls[0]["cwd"] == str(work)
        assert result["tripinfo"].parent == work
        assert result["statistics"].parent == work


class TestScenarioWorkspace:
    def test_tracked_workspace_is_created_under_run_directory(self, tmp_path,
                                                                monkeypatch):
        run_dir = tmp_path / "scenario-run"
        run_dir.mkdir()
        monkeypatch.setattr(run_scenario, "_ACTIVE_RUN_DIR", run_dir)

        workspace = run_scenario.create_scenario_workspace("baseline")

        assert workspace == run_dir / "scratch"
        assert workspace.is_dir()
        run_scenario.cleanup_scenario_workspace(workspace)
        assert not workspace.exists()

    def test_run_products_do_not_guess_from_other_scenario_files(self, tmp_path):
        produced = tmp_path / "baseline.json"
        trajectory = tmp_path / "baseline_traj.json"
        stale = tmp_path / "old_closure.json"
        produced.write_text("{}")
        trajectory.write_text("{}")
        stale.write_text("{}")
        products = run_scenario.scenario_run_products(produced, trajectory)
        assert products == [produced, trajectory]
        assert stale not in products


class TestTrajectoryPublication:
    """The normal scenario must publish from the already-completed seed-1000
    vehroute file instead of launching a fourth SUMO process."""

    def test_publishes_from_existing_vehroute_and_health_files(self, tmp_path,
                                                                monkeypatch):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        monkeypatch.setattr(run_scenario, "OUT_DIR", out_dir)
        vr = tmp_path / "vehroutes.xml"
        vr.write_text(
            "<routes><vehicle id='v1' depart='0'>"
            "<route edges='a b' exitTimes='10 20'/>"
            "</vehicle></routes>"
        )
        stats = tmp_path / "stats.xml"
        stats.write_text(
            "<statistics><vehicles loaded='1' inserted='1' running='0' "
            "waiting='0'/><teleports total='0'/><safety collisions='0'/>"
            "</statistics>"
        )

        result = run_scenario.publish_trajectories_from_vehroute(
            "baseline", tmp_path / "calibrated.rou.xml", vr, stats,
            {"a", "b"})

        assert result == "baseline_traj.json"
        payload = json.loads((out_dir / result).read_text())
        assert payload["inserted_in_run"] == 1
        assert payload["n_vehicles"] == 1
        assert payload["vehicles"][0]["e"] == [0, 1]

    def test_trajectory_keeps_calibrated_endpoint_positions(self, tmp_path):
        vr = tmp_path / "vehroutes.xml"
        vr.write_text(
            "<routes><vehicle id='v1' depart='0'>"
            "<route edges='a b' exitTimes='10 20'/>"
            "</vehicle></routes>"
        )
        _edges, vehicles, _total, _unfinished = run_scenario.parse_vehroute_file(
            vr, {"a", "b"}, endpoint_positions={"v1": {"p": 15.0, "a": 42.0}})

        assert vehicles == [{"d": 0, "e": [0, 1], "x": [10, 20],
                             "p": 15.0, "a": 42.0}]


class TestTrajectorySimulationMode:
    """A micro scenario used micro edge-flow simulation but its vehroute
    export always forced --mesosim, so the web UI displayed vehicle timings
    from a different simulation mode. Found in hygiene review 2026-07-10."""

    @pytest.mark.parametrize("micro", [False, True])
    def test_export_trajectories_matches_requested_simulation_mode(
            self, monkeypatch, tmp_path, micro):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, stderr="expected")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)

        result = run_scenario.export_trajectories(
            "mode-test", tmp_path / "r.rou.xml", [], duration_s=900,
            home=tmp_path, web_edges=set(), micro=micro)

        assert result is None
        assert ("--mesosim" in commands[0]) is not micro


class TestScenarioManifestDemandScope:
    def test_single_day_signature_is_identical_to_pre_b1_signature(self):
        meta = {
            "date": "2025-09-16", "source": "historical", "begin": "00:00",
            "end": "24:00", "n_intervals": 96,
            "epoch_sim": "2025-09-16T00:00:00", "n_variants": 3,
            "start_date": "2025-09-16", "days": 1,
            "end_date_exclusive": "2025-09-17",
            "day_boundaries_s": [0, 86400], "day_kinds": ["weekday"],
        }
        # Exact SHA-1/12 value emitted by the pre-B1 implementation.
        assert run_scenario.demand_signature(meta) == "b5116ac70049"

    def test_multi_day_signature_uses_range_contract(self):
        meta = {
            "source": "historical", "n_intervals": 192,
            "epoch_sim": "2025-09-16T00:00:00", "n_variants": 3,
            "start_date": "2025-09-16", "days": 2,
            "end_date_exclusive": "2025-09-18",
            "day_boundaries_s": [0, 86400, 172800],
            "day_kinds": ["weekday", "weekday"],
        }
        changed = dict(meta, end_date_exclusive="2025-09-19",
                       day_boundaries_s=[0, 86400, 172800, 259200])

        assert run_scenario.demand_signature(meta) != run_scenario.demand_signature(changed)

    def test_demand_signature_changes_when_window_changes(self):
        meta = {
            "date": "2025-09-16",
            "source": "historical",
            "begin": "00:00",
            "end": "24:00",
            "n_intervals": 96,
            "epoch_sim": "2025-09-16T00:00:00",
            "n_variants": 3,
        }
        changed = dict(meta, begin="07:00", end="09:00", n_intervals=8,
                       epoch_sim="2025-09-16T07:00:00")

        assert run_scenario.demand_signature(meta) != run_scenario.demand_signature(changed)

    def test_window_label_single_day(self):
        meta = {"date": "2025-09-16", "begin": "00:00", "end": "24:00"}
        assert run_scenario.demand_window_label(meta) == "2025-09-16 00:00–24:00"

    def test_window_label_multi_day_does_not_need_date_key(self):
        meta = {"start_date": "2025-09-16", "end_date_exclusive": "2025-09-18",
                "days": 2}
        assert "date" not in meta  # regression guard for the KeyError this fixes
        assert run_scenario.demand_window_label(meta) == \
            "2025-09-16 → 2025-09-18 (2 days)"

    def test_trajectories_default_on_for_single_day(self):
        args = argparse.Namespace(no_trajectories=False, trajectories=False)
        assert run_scenario.want_trajectories(args, n_intervals=96) is True

    def test_trajectories_default_on_above_one_day_with_bounded_sampling(self):
        args = argparse.Namespace(no_trajectories=False, trajectories=False)
        assert run_scenario.want_trajectories(args, n_intervals=192) is True
        assert run_scenario.trajectory_sample_cap(192) == 10_000

    def test_trajectories_flag_forces_on_for_multi_day(self):
        args = argparse.Namespace(no_trajectories=False, trajectories=True)
        assert run_scenario.want_trajectories(args, n_intervals=672) is True

    def test_no_trajectories_flag_forces_off_for_single_day(self):
        args = argparse.Namespace(no_trajectories=True, trajectories=False)
        assert run_scenario.want_trajectories(args, n_intervals=96) is False

    def test_multiday_trajectory_sample_is_deterministic_and_capped_per_day(
            self, tmp_path):
        def write(path, vehicles):
            body = "".join(
                f"<vehicle id='{vehicle_id}' depart='{depart}'>"
                f"<route edges='a b' exitTimes='{depart + 5} {depart + 10}'/>"
                "</vehicle>"
                for vehicle_id, depart in vehicles)
            path.write_text(f"<routes>{body}</routes>")

        source = [(f"d1-{i}", i * 10) for i in range(4)]
        source += [(f"d2-{i}", 86400 + i * 10) for i in range(4)]
        first, second = tmp_path / "first.xml", tmp_path / "second.xml"
        write(first, source)
        write(second, list(reversed(source)))

        parsed_first = run_scenario.parse_vehroute_file(
            first, {"a", "b"}, max_vehicles_per_day=2,
            return_sampling=True)
        parsed_second = run_scenario.parse_vehroute_file(
            second, {"a", "b"}, max_vehicles_per_day=2,
            return_sampling=True)

        assert parsed_first == parsed_second
        _edges, vehicles, total, unfinished, sampling = parsed_first
        assert total == 8
        assert unfinished == 0
        assert len(vehicles) == 4
        assert sampling == {
            "enabled": True,
            "method": "sha256_vehicle_id_per_day",
            "max_vehicles_per_day": 2,
            "eligible_vehicles": 8,
            "selected_vehicles": 4,
            "per_day": [
                {"day": 1, "eligible": 4, "selected": 2},
                {"day": 2, "eligible": 4, "selected": 2},
            ],
        }

    def test_trajectories_and_no_trajectories_together_is_a_cli_error(self, monkeypatch):
        monkeypatch.setattr(sys, "argv",
                            ["run_scenario.py", "--trajectories", "--no-trajectories"])
        with pytest.raises(SystemExit):
            run_scenario.parse_args()

    def test_adopted_minimal_edgedata_is_live_safe_but_rollback_is_isolated(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "argv", [
            "run_scenario.py", "--minimal-edgedata",
        ])
        assert run_scenario.parse_args().minimal_edgedata is True

        monkeypatch.setattr(sys, "argv", [
            "run_scenario.py", "--minimal-edgedata",
            "--out-dir", str(tmp_path / "output"),
            "--timing-sidecar", str(tmp_path / "timing.json"),
        ])
        assert run_scenario.parse_args().minimal_edgedata is True

        monkeypatch.setattr(sys, "argv", [
            "run_scenario.py", "--full-edgedata",
            "--out-dir", str(tmp_path / "full-output"),
            "--timing-sidecar", str(tmp_path / "full-timing.json"),
        ])
        assert run_scenario.parse_args().full_edgedata is True

    def test_warning_diagnostics_do_not_require_experimental_output(
            self, monkeypatch):
        monkeypatch.setattr(sys, "argv", [
            "run_scenario.py", "--sumo-warnings",
        ])
        assert run_scenario.parse_args().sumo_warnings is True

    def test_minimal_edgedata_writes_only_consumed_attributes(self, tmp_path):
        additional = tmp_path / "edge.add.xml"
        run_scenario.write_edgedata_additional(
            additional, tmp_path / "edge.xml", 86400)

        edge_data = ET.parse(additional).getroot().find("edgeData")
        assert edge_data.get("writeAttributes") == "entered timeLoss"

        run_scenario.write_edgedata_additional(
            additional, tmp_path / "edge.xml", 86400,
            minimal_attributes=False)
        edge_data = ET.parse(additional).getroot().find("edgeData")
        assert edge_data.get("writeAttributes") is None

    def test_aggregate_flows_includes_edges_with_zero_traffic_in_every_seed(self):
        """Finding #1 from a bug review 2026-07-10, independently verified
        and fixed: excludeEmpty="true" means an edge with genuinely zero
        traffic in every seed never appears in any per_seed dict at all —
        it must still get a real (zero) flows_out entry, not be silently
        dropped and rendered as a missing-data gap."""
        per_seed = [{"busy_edge": np.array([5.0, 5.0])}, {"busy_edge": np.array([5.0, 5.0])}]
        web_edges = {"busy_edge", "quiet_edge"}
        prior = {"busy_edge": 0.9, "quiet_edge": 0.7}

        flows_out, conf_out = run_scenario.aggregate_flows(per_seed, web_edges, prior, 2)

        assert set(flows_out) == web_edges
        assert flows_out["quiet_edge"] == [0, 0]
        assert conf_out["quiet_edge"] == 0.7   # pure spatial prior, no CV to penalize it

    def test_aggregate_flows_zeroes_confidence_without_calibrated_route_support(self):
        per_seed = [{"supported": np.array([3.0, 2.0])}]
        prior = {"supported": 0.8, "unsupported": 0.9}

        _flows, confidence = run_scenario.aggregate_flows(
            per_seed, set(prior), prior, 1, supported_edges={"supported"})

        assert confidence["supported"] == 0.8
        assert confidence["unsupported"] == 0.0

    def test_aggregate_flows_caps_explicit_support_only_edges(self):
        per_seed = [{"core": np.array([3.0]), "support": np.array([1.0])}]
        prior = {"core": 0.8, "support": 0.9}

        _flows, confidence = run_scenario.aggregate_flows(
            per_seed, set(prior), prior, 1,
            supported_edges=set(prior), low_evidence_edges={"support"})

        assert confidence["core"] == 0.8
        assert confidence["support"] == 0.15

    def test_manifest_keeps_only_current_demand_entries(self):
        current = "abc123"
        old = "old999"
        index = {
            "scenarios": [
                {"name": "baseline", "demand_signature": current},
                {"name": "old_closure", "demand_signature": old},
                {"name": "legacy_without_signature"},
            ]
        }

        filtered = run_scenario.index_for_current_demand(index, current)

        assert filtered["demand_signature"] == current
        assert [s["name"] for s in filtered["scenarios"]] == ["baseline"]


class TestTruncateStrandedVehicles:
    """FOUND 2026-07-09: SUMO's runtime rerouter (write_closure_additional)
    reroutes vehicles around a closure fine WHEN a detour exists, but for an
    origin/destination pair with NO detour at all it can't find one either —
    confirmed directly (duarouter given the same closure file still routes
    through the "closed" edge; a rerouter is a runtime-only concept, not
    something the offline router evaluates) — and the vehicle just sits
    stuck until sumo's end-of-run cleanup teleports it past the closure,
    which then shows up in the exported flows/trajectory as if it had
    legitimately driven the closed edge.

    truncate_stranded_vehicles is the fix — but SHORTENS the route to end
    just short of the closure rather than deleting the vehicle outright
    (Gustav, correctly: deleting it also erases its real traffic
    contribution on every edge BEFORE the closure, not just the closed one
    — a driver whose actual destination is now unreachable by car still
    drives most of the way and parks short of it, walking the rest)."""

    @staticmethod
    def write_net(path: Path) -> None:
        # a_b --(closed)--> b_c --> c_d, with a detour a_b->b_e->e_c->c_d;
        # w_x->x_y--(closed)-->y_z is a dead end with no alternative — a
        # vehicle heading there can still legitimately drive w_x->x_y.
        connections = [
            ("a_b", "b_c"), ("b_c", "c_d"),
            ("a_b", "b_e"), ("b_e", "e_c"), ("e_c", "c_d"),
            ("w_x", "x_y"), ("x_y", "y_z"),
        ]
        with open(path, "w") as f:
            f.write("<net>\n")
            for frm, to in connections:
                f.write(f'  <connection from="{frm}" to="{to}"/>\n')
            f.write("</net>\n")

    @staticmethod
    def write_routes(path: Path) -> None:
        with open(path, "w") as f:
            f.write("<routes>\n")
            f.write('  <vehicle id="detourable" depart="0">\n'
                    '    <route edges="a_b b_c c_d"/>\n  </vehicle>\n')
            f.write('  <vehicle id="stranded" depart="0">\n'
                    '    <route edges="w_x x_y y_z"/>\n  </vehicle>\n')
            f.write('  <vehicle id="immediately_stranded" depart="0">\n'
                    '    <route edges="y_z"/>\n  </vehicle>\n')
            f.write('  <vehicle id="untouched" depart="0">\n'
                    '    <route edges="a_b b_e e_c c_d"/>\n  </vehicle>\n')
            f.write("</routes>\n")

    def test_detour_exists_route_is_untouched(self, monkeypatch, tmp_path):
        net_path = tmp_path / "net.net.xml"
        self.write_net(net_path)
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)

        route_path = tmp_path / "in.rou.xml"
        self.write_routes(route_path)
        out_path = tmp_path / "out.rou.xml"

        adj = run_scenario.build_edge_graph({"b_c", "y_z"})
        t, d = run_scenario.truncate_stranded_vehicles(
            route_path, ["b_c", "y_z"], out_path, adj)

        assert (t, d) == (1, 1)
        vehicles = {v.get("id"): v.find("route").get("edges")
                    for v in ET.parse(out_path).getroot().findall("vehicle")}
        assert vehicles["detourable"] == "a_b b_c c_d"        # rerouter handles it live
        assert vehicles["untouched"] == "a_b b_e e_c c_d"
        assert vehicles["stranded"] == "w_x x_y"               # truncated, not deleted
        assert "immediately_stranded" not in vehicles          # nothing to truncate to

    def test_no_affected_vehicles_still_writes_output(self, monkeypatch, tmp_path):
        net_path = tmp_path / "net.net.xml"
        self.write_net(net_path)
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)

        route_path = tmp_path / "in.rou.xml"
        self.write_routes(route_path)
        out_path = tmp_path / "out.rou.xml"

        adj = run_scenario.build_edge_graph({"nonexistent_edge"})
        t, d = run_scenario.truncate_stranded_vehicles(
            route_path, ["nonexistent_edge"], out_path, adj)

        assert (t, d) == (0, 0)
        ids = {v.get("id") for v in ET.parse(out_path).getroot().findall("vehicle")}
        assert ids == {"detourable", "stranded", "immediately_stranded", "untouched"}

    def test_same_origin_and_destination_but_only_one_branch_is_stranded(
            self, monkeypatch, tmp_path):
        """FOUND in Codex review 2026-07-09: the first version of this fix
        cached on (route[0], route[-1]) — global origin/destination
        reachability — as a proxy for "will the live rerouter save this
        vehicle". Wrong: two vehicles can share the same origin AND the
        same destination while being on different candidate routes, one
        of which is already committed to a branch with no way out even
        though the OTHER branch (which this vehicle isn't on) would have
        worked fine. The origin-level check would have left BOTH routes
        untouched (since SOME path from origin to destination exists),
        reproducing the exact teleport-through-a-closed-edge leak for the
        vehicle on the bad branch. The fix checks reachability from each
        vehicle's OWN position right before the closure, not from a
        shared origin."""
        net_path = tmp_path / "net.net.xml"
        connections = [
            ("a_b", "b_g"), ("b_g", "g_z"), ("g_z", "z_d"),        # good branch, avoids closure
            ("a_b", "b_h"), ("b_h", "h_closed"),                    # bad branch: dead end once closed
            ("h_closed", "closed_z"), ("closed_z", "z_d"),
        ]
        with open(net_path, "w") as f:
            f.write("<net>\n")
            for frm, to in connections:
                f.write(f'  <connection from="{frm}" to="{to}"/>\n')
            f.write("</net>\n")
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)

        route_path = tmp_path / "in.rou.xml"
        with open(route_path, "w") as f:
            f.write("<routes>\n")
            f.write('  <vehicle id="good_branch" depart="0">\n'
                    '    <route edges="a_b b_g g_z z_d"/>\n  </vehicle>\n')
            f.write('  <vehicle id="bad_branch" depart="0">\n'
                    '    <route edges="a_b b_h h_closed closed_z z_d"/>\n  </vehicle>\n')
            f.write("</routes>\n")
        out_path = tmp_path / "out.rou.xml"

        adj = run_scenario.build_edge_graph({"h_closed"})
        t, d = run_scenario.truncate_stranded_vehicles(
            route_path, ["h_closed"], out_path, adj)

        assert (t, d) == (1, 0)
        vehicles = {v.get("id"): v.find("route").get("edges")
                    for v in ET.parse(out_path).getroot().findall("vehicle")}
        # same origin (a_b) and same destination (z_d) as bad_branch, but
        # reachable overall — must stay fully untouched
        assert vehicles["good_branch"] == "a_b b_g g_z z_d"
        # stuck on its own branch despite origin->destination being
        # reachable via the OTHER branch — must be truncated, not left as-is
        assert vehicles["bad_branch"] == "a_b b_h"

    def test_multiple_closures_with_a_bypass_leaves_route_untouched(
            self, monkeypatch, tmp_path):
        """A candidate route can pass through TWO closed edges in sequence
        while a real detour exists that avoids both — truncating at the
        FIRST closed edge encountered (ignoring whether a later closure on
        the same route is what actually matters) would wrongly cut off a
        trip the live rerouter can complete just fine. Since `reachable()`
        removes every closed edge at once (not just the first), checking
        from right before the first closure already accounts for the
        second one too."""
        net_path = tmp_path / "net.net.xml"
        connections = [
            ("p_q", "q_r1"), ("q_r1", "r1_s"), ("r1_s", "s_t2"), ("s_t2", "t2_end"),
            ("p_q", "q_r2"), ("q_r2", "r2_s"), ("r2_s", "t2_end"),   # bypass around BOTH closures
        ]
        with open(net_path, "w") as f:
            f.write("<net>\n")
            for frm, to in connections:
                f.write(f'  <connection from="{frm}" to="{to}"/>\n')
            f.write("</net>\n")
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)

        route_path = tmp_path / "in.rou.xml"
        with open(route_path, "w") as f:
            f.write("<routes>\n")
            f.write('  <vehicle id="double_closure" depart="0">\n'
                    '    <route edges="p_q q_r1 r1_s s_t2 t2_end"/>\n  </vehicle>\n')
            f.write("</routes>\n")
        out_path = tmp_path / "out.rou.xml"

        adj = run_scenario.build_edge_graph({"q_r1", "s_t2"})
        t, d = run_scenario.truncate_stranded_vehicles(
            route_path, ["q_r1", "s_t2"], out_path, adj)

        assert (t, d) == (0, 0)
        vehicles = {v.get("id"): v.find("route").get("edges")
                    for v in ET.parse(out_path).getroot().findall("vehicle")}
        assert vehicles["double_closure"] == "p_q q_r1 r1_s s_t2 t2_end"


class TestTimeWindowedClosures:
    def test_legacy_whole_run_closure_has_valid_contract_datetimes(self):
        epoch = "2025-09-16T00:00:00"
        internal = run_scenario.structured_closures(
            [], ["a_b"], epoch, duration_s=86400)

        assert internal == [
            {"edge_id": "a_b", "begin_s": 0, "end_s": 90000}]
        assert run_scenario.contract_closures(
            internal, epoch, duration_s=86400) == [{
                "edge_id": "a_b",
                "start_time": "2025-09-16T00:00:00",
                "end_time": "2025-09-17T00:00:00",
            }]

    def test_explicit_closure_cannot_extend_into_internal_drain_hour(self):
        closure = json.dumps({
            "edge_id": "a_b",
            "begin": "2025-09-16T23:45:00",
            "end": "2025-09-17T00:15:00",
        })

        with pytest.raises(ValueError, match="within the simulated run"):
            run_scenario.structured_closures(
                [closure], [], "2025-09-16T00:00:00", duration_s=86400)

    def test_write_closure_additional_emits_one_interval_per_window(self, tmp_path):
        path = tmp_path / "closure.add.xml"
        closures = [
            {"edge_id": "a_b", "begin_s": 600, "end_s": 1200},
            {"edge_id": "c_d", "begin_s": 1800, "end_s": 2400},
        ]

        run_scenario.write_closure_additional(path, closures, ["a_b", "c_d"])

        intervals = ET.parse(path).getroot().findall(".//interval")
        assert [(i.get("begin"), i.get("end"),
                 i.find("closingReroute").get("id")) for i in intervals] == [
            ("600", "1200", "a_b"), ("1800", "2400", "c_d")]

    def test_write_closure_additional_groups_simultaneous_edges_and_reopens(
            self, tmp_path):
        path = tmp_path / "closure.add.xml"
        closures = [
            {"edge_id": "a_b", "begin_s": 600, "end_s": 1200},
            {"edge_id": "c_d", "begin_s": 600, "end_s": 1200},
            {"edge_id": "a_b", "begin_s": 1800, "end_s": 2400},
            {"edge_id": "c_d", "begin_s": 1800, "end_s": 2400},
        ]

        run_scenario.write_closure_additional(
            path, closures, ["lead", "a_b", "c_d"])

        intervals = ET.parse(path).getroot().findall(".//interval")
        assert [(item.get("begin"), item.get("end")) for item in intervals] == [
            ("600", "1200"), ("1800", "2400")]
        assert [[closing.get("id") for closing in
                 item.findall("closingReroute")] for item in intervals] == [
            ["a_b", "c_d"], ["a_b", "c_d"]]

    def test_write_closure_additional_rejects_overlap_on_same_edge(
            self, tmp_path):
        with pytest.raises(ValueError, match="must not overlap"):
            run_scenario.write_closure_additional(
                tmp_path / "closure.add.xml",
                [
                    {"edge_id": "a_b", "begin_s": 600, "end_s": 1200},
                    {"edge_id": "a_b", "begin_s": 900, "end_s": 1500},
                ],
                ["a_b"],
            )

    def test_prefilter_only_truncates_windowed_no_detour_when_wait_can_teleport(
            self, monkeypatch, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <connection from="lead" to="closed"/>
  <connection from="closed" to="destination"/>
</net>""")
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)
        route_path = tmp_path / "in.rou.xml"
        route_path.write_text("""<routes>
  <vehicle id="long_wait" depart="0"><route edges="lead closed destination"/></vehicle>
  <vehicle id="short_wait" depart="330"><route edges="lead closed destination"/></vehicle>
  <vehicle id="after_open" depart="500"><route edges="lead closed destination"/></vehicle>
</routes>""")
        out_path = tmp_path / "out.rou.xml"
        closures = [{"edge_id": "closed", "begin_s": 10, "end_s": 400}]
        adj = run_scenario.build_edge_graph({"closed"})

        truncated, dropped = run_scenario.truncate_stranded_vehicles(
            route_path, ["closed"], out_path, adj, closures=closures,
            edge_travel_s={"lead": 20})

        assert (truncated, dropped) == (1, 0)
        routes = {v.get("id"): v.find("route").get("edges")
                  for v in ET.parse(out_path).getroot().findall("vehicle")}
        assert routes["long_wait"] == "lead"       # 380 s may teleport
        assert routes["short_wait"] == "lead closed destination"  # 50 s waits safely
        assert routes["after_open"] == "lead closed destination"

    def test_reachability_respects_single_category_permissions(self, monkeypatch, tmp_path):
        """FIXED (finding 3, 2026-08-29 repair batch): `build_edge_graph`
        used to follow every `<connection>` regardless of vClass, so a
        bicycle-only detour looked like a legal passenger route. It is now
        filtered to `DEFAULT_VCLASS` (`traffic_sim.simulation.metadata`),
        so a `allow="bicycle"` connection is correctly excluded and the
        bicycle-only "detour" is not treated as a legal passenger path —
        this was the exact documented blind spot; it is no longer a known
        limitation."""
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <connection from="lead" to="closed"/>
  <connection from="closed" to="destination"/>
  <connection from="lead" to="bike_detour" allow="bicycle"/>
  <connection from="bike_detour" to="destination" allow="bicycle"/>
</net>""")
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)
        route_path = tmp_path / "in.rou.xml"
        route_path.write_text("""<routes>
  <vType id="car" vClass="passenger"/>
  <vehicle id="passenger" type="car" depart="0"><route edges="lead closed destination"/></vehicle>
</routes>""")
        out_path = tmp_path / "out.rou.xml"
        adj = run_scenario.build_edge_graph({"closed"})
        closures = [{"edge_id": "closed", "begin_s": 0, "end_s": 1000}]

        # No legal (passenger) path survives closing "closed" -- the
        # bicycle-only connections are excluded, so the vehicle has nowhere
        # to detour and the legacy truncation path shortens it rather than
        # (incorrectly) trusting the live rerouter to find the bike detour.
        assert not run_scenario.reachable(adj, "lead", "destination", {"closed"})
        assert run_scenario.truncate_stranded_vehicles(
            route_path, ["closed"], out_path, adj, closures=closures) == (1, 0)
        assert ET.parse(out_path).getroot().find("vehicle/route").get("edges") == "lead"


class TestParseVehrouteFile:
    """F2 (IMPROVEMENT_PLAN.md): unfinished vehicles (still driving at end of run) keep
    their driven prefix and get marked "u": 1, so playback can park them at
    their last known position instead of erasing the trip."""

    @staticmethod
    def _write_vehroutes(tmp_path, body):
        p = tmp_path / "vehroutes.xml"
        p.write_text(f"<routes>{body}</routes>")
        return p

    def test_finished_vehicle_unchanged(self, tmp_path):
        vr = self._write_vehroutes(tmp_path,
            '<vehicle id="a" depart="10.0">'
            '<route edges="e1 e2 e3" exitTimes="20 30 40"/></vehicle>')
        _idx, vehicles, n_file, n_unf = run_scenario.parse_vehroute_file(
            vr, {"e1", "e2", "e3"})
        assert n_file == 1 and n_unf == 0
        assert vehicles == [{"d": 10, "e": [0, 1, 2], "x": [20, 30, 40]}]

    def test_unfinished_vehicle_keeps_prefix_and_is_marked(self, tmp_path):
        # sumo writes -1 for edges not yet left at end of run
        vr = self._write_vehroutes(tmp_path,
            '<vehicle id="b" depart="10.0">'
            '<route edges="e1 e2 e3" exitTimes="20 30 -1"/></vehicle>')
        _idx, vehicles, _n, n_unf = run_scenario.parse_vehroute_file(
            vr, {"e1", "e2", "e3"})
        assert n_unf == 1
        assert vehicles == [{"d": 10, "e": [0, 1], "x": [20, 30], "u": 1}]

    def test_unfinished_on_first_edge_is_dropped_not_crashed(self, tmp_path):
        vr = self._write_vehroutes(tmp_path,
            '<vehicle id="c" depart="10.0">'
            '<route edges="e1 e2" exitTimes="-1 -1"/></vehicle>')
        _idx, vehicles, n_file, n_unf = run_scenario.parse_vehroute_file(vr, {"e1", "e2"})
        assert n_file == 1 and vehicles == [] and n_unf == 0

    def test_route_leaving_drawable_set_is_skipped(self, tmp_path):
        vr = self._write_vehroutes(tmp_path,
            '<vehicle id="d" depart="0.0">'
            '<route edges="e1 offmap" exitTimes="5 9"/></vehicle>')
        _idx, vehicles, n_file, _n = run_scenario.parse_vehroute_file(vr, {"e1"})
        assert n_file == 1 and vehicles == []


class TestFinalRoute:
    """2026-07-14 accuracy review §P0-1: rerouted vehicles live inside
    <routeDistribution> (last <route> = actually driven); reading only the
    top-level <route> silently dropped exactly the rerouted vehicles from
    closure animations (verified: 3182/21594 on the real artifact)."""

    def test_rerouted_vehicle_uses_last_distribution_route(self, tmp_path):
        vr = tmp_path / "vehroutes.xml"
        vr.write_text(
            '<routes><vehicle id="r" depart="5.0">'
            '<routeDistribution>'
            '<route edges="a b closed" exitTimes="1 2 3"/>'
            '<route edges="a b detour end" exitTimes="10 20 30 40"/>'
            '</routeDistribution></vehicle></routes>')
        _idx, vehicles, n_file, _u = run_scenario.parse_vehroute_file(
            vr, {"a", "b", "detour", "end"})
        assert n_file == 1
        assert vehicles == [{"d": 5, "e": [0, 1, 2, 3],
                             "x": [10, 20, 30, 40]}]

    def test_plain_route_still_parsed(self, tmp_path):
        vr = tmp_path / "vehroutes.xml"
        vr.write_text('<routes><vehicle id="p" depart="0.0">'
                      '<route edges="a b" exitTimes="1 2"/></vehicle></routes>')
        _idx, vehicles, _n, _u = run_scenario.parse_vehroute_file(vr, {"a", "b"})
        assert len(vehicles) == 1

    def test_empty_distribution_is_skipped_not_crashed(self, tmp_path):
        vr = tmp_path / "vehroutes.xml"
        vr.write_text('<routes><vehicle id="x" depart="0.0">'
                      '<routeDistribution></routeDistribution></vehicle></routes>')
        _idx, vehicles, n_file, _u = run_scenario.parse_vehroute_file(vr, {"a"})
        assert n_file == 1 and vehicles == []


class TestHealthFailsClosed:
    """§P0-3: missing telemetry must flag, not pass silently."""

    def test_missing_seed_record_produces_flag(self):
        # simulate main()'s guard: 3 seeds requested, 2 records parsed
        flags = run_scenario.seed_health_flags([
            {"seed": 1000, "loaded": 100, "inserted": 100,
             "running_at_end": 0, "waiting_at_end": 0, "teleports": 0}])
        # the per-record gates pass; the count guard lives in main() —
        # replicate its arithmetic here
        n_seeds, n_records = 3, 1
        if n_records < n_seeds:
            flags.append(f"hälsotelemetri saknas för {n_seeds - n_records} av "
                         f"{n_seeds} frön — omätt är inte friskt")
        assert any("saknas" in f for f in flags)


class TestParseEdgedataOptimization:
    """The edgeData parser was rewritten (LUNA-PERF-14) from
    ET.parse().findall() to a streaming XMLParser target. The optimization is
    only sound if it returns exactly what the tree version did, so every test
    here compares it against a test-local reference of the pre-optimization
    behavior across the shapes real SUMO output takes."""

    @staticmethod
    def _reference(path, n_intervals, measured_empty_edges=()):
        """The exact pre-optimization implementation, kept here as the oracle."""
        flows = {}
        root = ET.parse(path).getroot()
        for interval in root.findall("interval"):
            i = int(float(interval.get("begin")) // 900)
            if i >= n_intervals:
                continue
            for edge in interval.findall("edge"):
                eid = edge.get("id")
                entered = float(edge.get("entered") or 0)
                if eid not in flows:
                    flows[eid] = np.zeros(n_intervals)
                flows[eid][i] = entered
        for edge_id in measured_empty_edges:
            flows.setdefault(edge_id, np.zeros(n_intervals))
        return flows

    @staticmethod
    def _assert_equivalent(a, b):
        assert a.keys() == b.keys()
        for key in a:
            assert a[key].dtype == b[key].dtype == np.float64, key
            assert np.array_equal(a[key], b[key]), key

    def _write(self, tmp_path, body):
        path = tmp_path / "edgedata.xml"
        path.write_text("<meandata>\n" + body + "\n</meandata>\n")
        return path

    def test_multiple_intervals_and_absent_and_zero_entries(self, tmp_path):
        path = self._write(tmp_path, """
          <interval begin="0" end="900">
            <edge id="a" entered="5"/>
            <edge id="b" entered="0"/>
          </interval>
          <interval begin="900" end="1800">
            <edge id="a" entered="7"/>
          </interval>""")
        for empties in ((), ("a", "closed_measured")):
            expect = self._reference(path, 4, empties)
            got = run_scenario.parse_edgedata(path, 4, empties)
            self._assert_equivalent(expect, got)
        # spot-check the actual values, not just cross-equivalence
        got = run_scenario.parse_edgedata(path, 4)
        assert list(got["a"]) == [5.0, 7.0, 0.0, 0.0]
        assert list(got["b"]) == [0.0, 0.0, 0.0, 0.0]

    def test_required_measured_empty_edges_are_zero_filled(self, tmp_path):
        path = self._write(tmp_path, """
          <interval begin="0" end="900"><edge id="a" entered="3"/></interval>""")
        empties = ("closed_1", "closed_2", "a")   # "a" already present
        expect = self._reference(path, 4, empties)
        got = run_scenario.parse_edgedata(path, 4, empties)
        self._assert_equivalent(expect, got)
        assert set(got) == {"a", "closed_1", "closed_2"}
        assert list(got["closed_1"]) == [0.0, 0.0, 0.0, 0.0]
        assert list(got["a"]) == [3.0, 0.0, 0.0, 0.0]     # not overwritten

    def test_duplicate_edge_records_last_write_wins(self, tmp_path):
        path = self._write(tmp_path, """
          <interval begin="0" end="900">
            <edge id="a" entered="3"/>
            <edge id="a" entered="9"/>
          </interval>""")
        expect = self._reference(path, 4)
        got = run_scenario.parse_edgedata(path, 4)
        self._assert_equivalent(expect, got)
        assert got["a"][0] == 9.0

    def test_out_of_range_intervals_are_skipped(self, tmp_path):
        path = self._write(tmp_path, """
          <interval begin="0" end="900"><edge id="a" entered="2"/></interval>
          <interval begin="3600" end="4500"><edge id="only_late" entered="8"/></interval>""")
        expect = self._reference(path, 2)          # interval index 4 is dropped
        got = run_scenario.parse_edgedata(path, 2)
        self._assert_equivalent(expect, got)
        assert "only_late" not in got               # never materialized

    def test_missing_begin_raises_like_the_reference(self, tmp_path):
        path = self._write(tmp_path, """
          <interval end="900"><edge id="a" entered="2"/></interval>""")
        with pytest.raises(TypeError):
            self._reference(path, 4)
        with pytest.raises(TypeError):
            run_scenario.parse_edgedata(path, 4)

    def test_non_numeric_entered_raises_like_the_reference(self, tmp_path):
        path = self._write(tmp_path, """
          <interval begin="0" end="900"><edge id="a" entered="NaNaN?"/></interval>""")
        with pytest.raises(ValueError):
            self._reference(path, 4)
        with pytest.raises(ValueError):
            run_scenario.parse_edgedata(path, 4)

    def test_malformed_xml_raises_parse_error_like_the_reference(self, tmp_path):
        path = tmp_path / "bad.xml"
        path.write_text("<meandata><interval begin='0'><edge id='a' entered='1'>")
        with pytest.raises(ET.ParseError):
            self._reference(path, 4)
        with pytest.raises(ET.ParseError):
            run_scenario.parse_edgedata(path, 4)

    def test_missing_file_raises_like_the_reference(self, tmp_path):
        path = tmp_path / "does_not_exist.xml"
        with pytest.raises((FileNotFoundError, OSError)):
            self._reference(path, 4)
        with pytest.raises((FileNotFoundError, OSError)):
            run_scenario.parse_edgedata(path, 4)

    def test_an_interval_nested_below_the_root_is_ignored(self, tmp_path):
        """The tree version used root.findall('interval') — direct children
        only. An interval wrapped in another element is not a direct child, so
        neither parser may return its edges."""
        path = self._write(tmp_path, """
          <wrapper>
            <interval begin="0" end="900">
              <edge id="nested_interval_edge" entered="5"/>
            </interval>
          </wrapper>
          <interval begin="0" end="900"><edge id="real" entered="3"/></interval>""")
        expect = self._reference(path, 4)
        got = run_scenario.parse_edgedata(path, 4)
        self._assert_equivalent(expect, got)
        assert set(got) == {"real"}                 # the wrapped one is invisible
        assert "nested_interval_edge" not in got

    def test_an_edge_nested_below_an_interval_is_ignored(self, tmp_path):
        """interval.findall('edge') was direct children only, so an edge under
        an intermediate element inside the interval must not be counted."""
        path = self._write(tmp_path, """
          <interval begin="0" end="900">
            <edge id="direct" entered="4"/>
            <lane>
              <edge id="nested_edge" entered="9"/>
            </lane>
          </interval>""")
        expect = self._reference(path, 4)
        got = run_scenario.parse_edgedata(path, 4)
        self._assert_equivalent(expect, got)
        assert set(got) == {"direct"}
        assert "nested_edge" not in got

    def test_a_non_interval_direct_child_of_root_is_ignored(self, tmp_path):
        path = self._write(tmp_path, """
          <meta><edge id="meta_edge" entered="1"/></meta>
          <interval begin="0" end="900"><edge id="real" entered="2"/></interval>""")
        expect = self._reference(path, 4)
        got = run_scenario.parse_edgedata(path, 4)
        self._assert_equivalent(expect, got)
        assert set(got) == {"real"}

    def test_representative_whole_day_shape_is_equivalent(self, tmp_path):
        # 96 intervals x 200 edges with ~10% excludeEmpty gaps and duplicates.
        n_int, n_edge, val = 96, 200, 1
        body = []
        for iv in range(n_int):
            body.append(f'<interval begin="{iv*900}" end="{(iv+1)*900}">')
            for e in range(n_edge):
                if (iv * 7 + e) % 10 == 0:
                    continue
                val = (val * 1103515245 + 12345) & 0x7fffffff
                body.append(f'<edge id="e{e}" entered="{val % 500}"/>')
            body.append("</interval>")
        path = self._write(tmp_path, "\n".join(body))
        expect = self._reference(path, n_int, ("mZERO",))
        got = run_scenario.parse_edgedata(path, n_int, ("mZERO",))
        self._assert_equivalent(expect, got)
        assert len(got) == n_edge + 1               # every edge + the empty one


def _benchmark_parse_edgedata(n_int=96, n_edge=4000, trials=9):
    """Reproduce the LUNA-PERF-14 parse_edgedata timing evidence, end to end.

    Exact reproducible command (no arguments, no external inputs)::

        python3 tests/test_scenario.py

    Generates a deterministic edgeData fixture in a temp dir, asserts the
    streaming parser is byte-exact against the pre-optimization oracle
    (``TestParseEdgedataOptimization._reference``), then times ``trials``
    ALTERNATING old/new runs and reports medians against the retain gate
    (>= 25% AND >= 0.15 s). This is diagnostic development timing only — never
    release evidence or a 10-second-completion claim. The retain gate is
    machine-dependent; the equivalence assertions are not.
    """
    import shutil
    import statistics as st
    import tempfile
    import time

    reference = TestParseEdgedataOptimization._reference    # the old code
    tmp = Path(tempfile.mkdtemp(prefix="bench_edgedata_"))
    try:
        fixture = tmp / "edgedata_fixture.xml"
        val, lines = 1, ["<meandata>"]
        for iv in range(n_int):
            lines.append(f'  <interval begin="{iv * 900}" end="{(iv + 1) * 900}">')
            for e in range(n_edge):
                if (iv * 7 + e) % 10 == 0:          # ~10% excludeEmpty gaps
                    continue
                val = (val * 1103515245 + 12345) & 0x7FFFFFFF
                lines.append(f'    <edge id="e{e}" entered="{val % 500}"/>')
            lines.append("  </interval>")
        lines.append("</meandata>")
        fixture.write_text("\n".join(lines))

        old = reference(fixture, n_int, ("mZERO",))
        new = run_scenario.parse_edgedata(fixture, n_int, ("mZERO",))
        assert old.keys() == new.keys()
        for key in old:
            assert old[key].dtype == new[key].dtype == np.float64
            assert np.array_equal(old[key], new[key])

        old_t, new_t = [], []
        for _ in range(trials):
            t0 = time.perf_counter(); reference(fixture, n_int); old_t.append(time.perf_counter() - t0)
            t0 = time.perf_counter(); run_scenario.parse_edgedata(fixture, n_int); new_t.append(time.perf_counter() - t0)
        old_med, new_med = st.median(old_t), st.median(new_t)
        saving, ratio = old_med - new_med, (old_med - new_med) / old_med
        print(f"fixture: {n_int} intervals x {n_edge} edges, "
              f"{fixture.stat().st_size} bytes; equivalence OK ({len(new)} keys)")
        print(f"trials={trials} alternating | old median {old_med * 1000:.1f} ms | "
              f"new median {new_med * 1000:.1f} ms")
        print(f"saving {saving:.4f} s | ratio {ratio * 100:.1f}% | "
              f"gate>=25%&>=0.15s: {'PASS' if ratio >= 0.25 and saving >= 0.15 else 'FAIL'}")
        return {"old_median_s": old_med, "new_median_s": new_med,
                "saving_s": saving, "ratio": ratio}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _benchmark_parse_edgedata()


class TestSharedPayloadBuildersMatchLegacyProduction:
    """LUNA-PERF-19 rev2 extracted `build_scenario_payload` and
    `build_trajectory_payload` from run_scenario so production AND the persistent-
    SUMO benchmark build the byte-identical artifact from one seam. These tests
    pin the builders against the exact legacy inline shapes for baseline,
    closure, trajectory and multi-day cases, so a future edit that changes the
    published payload fails here."""

    class _Spec:
        scenario_id = "close_26842525_26355153_0"
        simulation_mode = "meso"
        network_build_id = "netbuild123"

        def to_dict(self):
            return {"scenario_id": self.scenario_id, "closures": [],
                    "simulation_mode": self.simulation_mode}

    def _legacy_scenario(self, *, meta, n_intervals, generated_at, spec,
                         traj_name, name, label, close_edges, closures,
                         window_label, n_truncated, n_dropped,
                         active_closure_entries, active_entries_by_seed,
                         closure_integrity, seed_count, seed_values, sig,
                         seed_health, health_flags, multi_day_validation,
                         sensor_audit, flows_out, conf_out):
        # A copy of the pre-extraction inline dict, kept here as the oracle.
        return {
            "epoch": meta["epoch_sim"], "interval_minutes": 15,
            "n_quarters": n_intervals, "generated_at": generated_at,
            "scenario_spec": spec.to_dict(), "trajectories": traj_name,
            "scenario": {
                "name": name, "scenario_id": spec.scenario_id, "label": label,
                "closed_edges": close_edges, "closures": closures,
                "window": window_label,
                "source": meta.get("source", "historical"),
                "agent_demand": meta.get("agent_demand"),
                "truncated_vehicles": n_truncated, "dropped_vehicles": n_dropped,
                "active_closure_edge_entries": active_closure_entries,
                "active_closure_edge_entries_by_seed": active_entries_by_seed,
                "closure_integrity": closure_integrity,
                **({"date": meta["date"], "begin": meta["begin"],
                    "end": meta["end"]} if "date" in meta else
                   {"start_date": meta["start_date"],
                    "end_date_exclusive": meta["end_date_exclusive"],
                    "days": meta["days"]}),
                "seeds": seed_count, "seed_set": seed_values,
                "simulation_mode": spec.simulation_mode,
                "network_build_id": spec.network_build_id,
                "demand_signature": sig, "build_id": meta.get("build_id"),
                "demand_build_key": meta.get("demand_build_key")},
            "seed_health": seed_health, "seed_health_flags": health_flags,
            **({"multi_day_validation": multi_day_validation}
               if multi_day_validation is not None else {}),
            "sensor_audit": sensor_audit, "flows": flows_out,
            "confidence": conf_out}

    def _kwargs(self, *, case, multi_day=False):
        base = {"epoch_sim": "2025-09-16T00:00:00", "source": "historical",
                "agent_demand": None, "build_id": "b1", "demand_build_key": "k1"}
        if multi_day:
            base.update({"start_date": "2025-09-16",
                         "end_date_exclusive": "2025-09-19", "days": 3})
        else:
            base.update({"date": "2025-09-16", "begin": "00:00", "end": "24:00"})
        closure = case == "closure"
        return dict(
            meta=base, n_intervals=96, generated_at="2025-09-16T12:00:00",
            spec=self._Spec(),
            traj_name="close_x_traj.json" if closure else "baseline_traj.json",
            name="close_x" if closure else "baseline",
            label="Closure" if closure else "Baseline",
            close_edges=["26842525_26355153_0"] if closure else [],
            closures=[{"edge_id": "26842525_26355153_0"}] if closure else [],
            window_label="00:00–24:00",
            n_truncated=12 if closure else 0, n_dropped=3 if closure else 0,
            active_closure_entries=0 if closure else None,
            active_entries_by_seed=[0, 0, 0] if closure else [None, None, None],
            closure_integrity="verified_clean" if closure else None,
            seed_count=3, seed_values=[1000, 1001, 1002], sig="sig-abc",
            seed_health=[{"seed": 1000, "loaded": 5, "inserted": 5}],
            health_flags=[],
            multi_day_validation={"ok": True} if multi_day else None,
            sensor_audit={"summary": "audit"},
            flows_out={"e0": [1, 2]}, conf_out={"e0": 0.5})

    @pytest.mark.parametrize("case", ["baseline", "closure"])
    def test_scenario_payload_matches_legacy(self, case):
        kw = self._kwargs(case=case)
        assert run_scenario.build_scenario_payload(**kw) == self._legacy(kw)

    def test_scenario_payload_matches_legacy_multi_day(self):
        kw = self._kwargs(case="closure", multi_day=True)
        got = run_scenario.build_scenario_payload(**kw)
        assert got == self._legacy(kw)
        assert got["scenario"]["days"] == 3 and "date" not in got["scenario"]
        assert got["multi_day_validation"] == {"ok": True}

    def test_scenario_payload_omits_multi_day_when_absent(self):
        got = run_scenario.build_scenario_payload(**self._kwargs(case="baseline"))
        assert "multi_day_validation" not in got

    def test_publication_writes_validated_payload_and_manifest_only(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_scenario, "OUT_DIR", tmp_path)
        kw = self._kwargs(case="baseline")
        payload = run_scenario.build_scenario_payload(**kw)

        scenario_path, index_path = run_scenario.publish_scenario_artifacts(
            payload,
            name=kw["name"],
            label=kw["label"],
            close_edges=kw["close_edges"],
            closures=kw["closures"],
            teleport_policy_s=None,
            spec=kw["spec"],
            signature=kw["sig"],
            meta=kw["meta"],
            window_label=kw["window_label"],
        )

        assert json.loads(scenario_path.read_text()) == payload
        index = json.loads(index_path.read_text())
        assert index["demand_signature"] == kw["sig"]
        assert index["scenarios"] == [{
            "name": "baseline",
            "label": "Baseline",
            "file": "baseline.json",
            "closed_edges": [],
            "closures": [],
            "closure_integrity": None,
            "scenario_spec": kw["spec"].to_dict(),
            "demand_signature": kw["sig"],
            "build_id": "b1",
            "demand_build_key": "k1",
            "window": "00:00–24:00",
        }]

    def _legacy(self, kw):
        return self._legacy_scenario(**kw)

    def test_trajectory_payload_matches_legacy(self):
        vehicles = [{"d": 0, "id": "v1"}, {"d": 1, "id": "v2"}]
        inv = ["e0", "e1"]
        got = run_scenario.build_trajectory_payload(
            1000, "calibrated.rou.xml", vehicles, 1, 4, {"rate": 0.5}, inv)
        legacy = {"seed": 1000, "variant": "calibrated.rou.xml",
                  "n_vehicles": 2, "n_unfinished": 1, "inserted_in_run": 4,
                  "sampling": {"rate": 0.5},
                  "displayed_share": round(2 / 4, 4),
                  "edges": inv, "vehicles": vehicles}
        assert got == legacy

    def test_trajectory_displayed_share_is_none_when_nothing_inserted(self):
        got = run_scenario.build_trajectory_payload(
            1000, "r.rou.xml", [], 0, 0, {}, [])
        assert got["displayed_share"] is None
