"""E2 publish-after-validate tests (IMPROVEMENT_PLAN.md; audit P0-2).

A recalibration must never destroy the live scenario set before a
validated replacement exists. These test the two pure helpers; the HTTP
flow wires them between the same subprocess calls as before.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import serve


def _hermetic_contract_hashes(monkeypatch):
    """Stub the registry/network digests behind the sensor contract.

    The real ``network_sha256`` hashes ``sumo/net.net.xml`` — a gitignored
    build product that only exists on a machine that has run the SUMO
    pipeline. Hashing through a stub keeps these tests exercising
    validate_staged_scenarios' comparison logic (contract present, hashes
    equal, sensor edges checked) instead of the developer machine's build
    state; on a fresh clone the unstubbed digest is None and every contract
    would be rejected as missing before the behaviour under test runs.
    """
    monkeypatch.setattr(serve, "sha256_file",
                        lambda path: f"digest:{Path(path).name}")


def _staged(tmp_path, geh=100.0, infeasible=0, with_baseline=True,
            flows=True, build_id=None, variant_fit=None, demand_key=None):
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    scen = {"name": "baseline", "file": "baseline.json"}
    index = {"scenarios": [scen] if with_baseline else []}
    (staging / "index.json").write_text(json.dumps(index))
    if with_baseline:
        payload = {"flows": {"e1": [1, 2]} if flows else {}, "n_quarters": 2}
        if build_id:
            payload["scenario"] = {"build_id": build_id}
            if demand_key:
                payload["scenario"]["demand_build_key"] = demand_key
        (staging / "baseline.json").write_text(json.dumps(payload))
        (staging / "baseline_traj.json").write_text(json.dumps(
            {"vehicles": []}))
    meta = tmp_path / "demand_meta.json"
    meta_payload = {"pfe_fit": {
        "geh_pct": geh, "infeasible_intervals": infeasible,
        "vehicles": 20000}}
    if build_id:
        meta_payload["build_id"] = build_id
    if demand_key:
        meta_payload["demand_build_key"] = demand_key
        meta_payload["demand_spec"] = {"build_key": demand_key}
    if variant_fit is not None:
        meta_payload["pfe_fit_variants"] = variant_fit
    meta.write_text(json.dumps(meta_payload))
    return staging, meta


class TestValidateStagedScenarios:
    def test_healthy_staging_passes(self, tmp_path):
        staging, meta = _staged(tmp_path)
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert ok, reason

    def test_missing_baseline_is_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, with_baseline=False)
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "baslinjen" in reason

    def test_empty_flows_are_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, flows=False)
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "inga flöden" in reason

    def test_geh_collapse_is_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, geh=71.3)
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "71.3" in reason

    def test_infeasible_intervals_are_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, infeasible=4)
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "olösliga" in reason

    def test_corrupt_staged_json_is_refused(self, tmp_path):
        staging, meta = _staged(tmp_path)
        (staging / "baseline.json").write_text("{not json")
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "ogiltig JSON" in reason

    def test_older_meta_without_pfe_fit_passes(self, tmp_path):
        staging, meta = _staged(tmp_path)
        meta.write_text(json.dumps({"date": "2025-09-16"}))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert ok, reason

    def test_build_id_mismatch_is_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, build_id="new-build")
        (staging / "baseline.json").write_text(json.dumps({
            "flows": {"e1": [1, 2]},
            "scenario": {"build_id": "old-build"},
        }))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "build-ID" in reason

    def test_demand_build_key_mismatch_is_refused(self, tmp_path):
        staging, meta = _staged(tmp_path, build_id="new-build",
                                demand_key="new-demand")
        (staging / "baseline.json").write_text(json.dumps({
            "flows": {"e1": [1, 2]},
            "scenario": {"build_id": "new-build",
                          "demand_build_key": "old-demand"},
        }))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "demand-build" in reason

    def test_each_uncertainty_variant_is_gated(self, tmp_path):
        staging, meta = _staged(
            tmp_path,
            variant_fit={
                "edge_shares": {"geh_pct": 100, "infeasible_intervals": 0},
                "edge_shares_q10": {"geh_pct": 98.0, "infeasible_intervals": 0},
            })
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "edge_shares_q10" in reason

    def test_new_sensor_contract_requires_raw_final_output_fit(self, tmp_path):
        staging, meta = _staged(tmp_path)
        demand = json.loads(meta.read_text())
        demand["sensor_targets"] = {"variants": {"edge_shares": {"e1": [1]}}}
        demand["n_variants"] = 1
        meta.write_text(json.dumps(demand))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "sensor-/nätverkskontrakt" in reason

    def test_new_sensor_contract_accepts_raw_fit_and_variant_provenance(
            self, tmp_path, monkeypatch):
        _hermetic_contract_hashes(monkeypatch)
        staging, meta = _staged(tmp_path)
        payload = json.loads((staging / "baseline.json").read_text())
        payload["scenario_spec"] = {
            "demand_variant_mapping": {"1000": "q50"}}
        sensor_edges = serve.current_sensor_edges()
        direction_rows = [
            {"target_mean": [1.0, 2.0], "simulated_mean_raw": [1.0, 2.0]}
            for edges in sensor_edges.values() for _edge in edges
        ]
        station_rows = [
            {"target_mean": [1.0, 2.0], "simulated_mean_raw": [1.0, 2.0]}
            for _sensor_id in sensor_edges
        ]
        payload["sensor_audit"] = {
            "provenance": {"targets": "demand_metadata",
                            "observations": "demand_metadata"},
            "directions": direction_rows,
            "stations": station_rows,
            "output_fit": {"uses_raw_ensemble_mean": True,
                           "ensemble": {"available": True,
                                        "edge_quarters": len(direction_rows) * 2,
                                        "geh_lt_5_pct": 100.0, "max_geh": 0.0,
                                        "mean_abs_error": 0.0, "max_abs_error": 0.0},
                           "station_ensemble": {"available": True,
                                                "edge_quarters": len(station_rows) * 2,
                                                "geh_lt_5_pct": 100.0, "max_geh": 0.0,
                                                "mean_abs_error": 0.0, "max_abs_error": 0.0}},
        }
        (staging / "baseline.json").write_text(json.dumps(payload))
        demand = json.loads(meta.read_text())
        demand["sensor_targets"] = {"variants": {"edge_shares": {"e1": [1]}}}
        demand["sensor_contract"] = {
            "registry_sha256": serve.sha256_file(
                serve.ROOT / "data_in" / "sensors.json"),
            "network_sha256": serve.sha256_file(
                serve.ROOT / "sumo" / "net.net.xml"),
            "sensor_edges": sensor_edges,
        }
        demand["n_variants"] = 1
        meta.write_text(json.dumps(demand))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert ok, reason

    def test_new_sensor_contract_refuses_bad_raw_sumo_fit(self, tmp_path, monkeypatch):
        _hermetic_contract_hashes(monkeypatch)
        staging, meta = _staged(tmp_path)
        payload = json.loads((staging / "baseline.json").read_text())
        sensor_edges = serve.current_sensor_edges()
        directions = [
            {"target_mean": [1.0, 1.0], "simulated_mean_raw": [30.0, 30.0]}
            for edges in sensor_edges.values() for _edge in edges
        ]
        stations = [
            {"target_mean": [1.0, 1.0], "simulated_mean_raw": [30.0, 30.0]}
            for _sensor_id in sensor_edges
        ]
        payload["sensor_audit"] = {
            "provenance": {"targets": "demand_metadata",
                           "observations": "demand_metadata"},
            "directions": directions, "stations": stations,
            # Deliberately lies about the compact summary: publication must
            # recompute from raw edgeData evidence rather than trust this.
            "output_fit": {"uses_raw_ensemble_mean": True,
                           "ensemble": {"available": True,
                                        "edge_quarters": len(directions) * 2,
                                        "geh_lt_5_pct": 100.0, "max_geh": 0.0,
                                        "mean_abs_error": 0.0, "max_abs_error": 0.0},
                           "station_ensemble": {"available": True,
                                                "edge_quarters": len(stations) * 2,
                                                "geh_lt_5_pct": 100.0, "max_geh": 0.0,
                                                "mean_abs_error": 0.0, "max_abs_error": 0.0}},
        }
        (staging / "baseline.json").write_text(json.dumps(payload))
        demand = json.loads(meta.read_text())
        demand["sensor_targets"] = {"variants": {"edge_shares": {"e1": [1]}}}
        demand["sensor_contract"] = {
            "registry_sha256": serve.sha256_file(serve.ROOT / "data_in" / "sensors.json"),
            "network_sha256": serve.sha256_file(serve.ROOT / "sumo" / "net.net.xml"),
            "sensor_edges": sensor_edges,
        }
        meta.write_text(json.dumps(demand))

        ok, reason = serve.validate_staged_scenarios(staging, meta)

        assert not ok
        assert "inkonsekvent" in reason or "GEH" in reason

    def test_new_sensor_contract_refuses_geojson_sensor_edge_mismatch(
            self, tmp_path, monkeypatch):
        _hermetic_contract_hashes(monkeypatch)
        staging, meta = _staged(tmp_path)
        demand = json.loads(meta.read_text())
        demand["sensor_targets"] = {"variants": {"edge_shares": {"e1": [1]}}}
        demand["sensor_contract"] = {
            "registry_sha256": serve.sha256_file(serve.ROOT / "data_in" / "sensors.json"),
            "network_sha256": serve.sha256_file(serve.ROOT / "sumo" / "net.net.xml"),
            "sensor_edges": {"wrong": ["e1"]},
        }
        meta.write_text(json.dumps(demand))

        ok, reason = serve.validate_staged_scenarios(staging, meta)

        assert not ok and "GeoJSON" in reason

    def test_three_variant_release_requires_full_variant_coverage(self, tmp_path):
        """An all-q50 mapping must not publish as a three-variant release:
        it would silently collapse the Monte Carlo uncertainty coverage."""
        staging, meta = _staged(tmp_path)
        payload = json.loads((staging / "baseline.json").read_text())
        payload["scenario_spec"] = {"demand_variant_mapping": {
            "1000": "q50", "1001": "q50", "1002": "q50"}}
        (staging / "baseline.json").write_text(json.dumps(payload))
        demand = json.loads(meta.read_text())
        demand["n_variants"] = 3
        meta.write_text(json.dumps(demand))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "varianttäckning" in reason

    def test_multi_day_staging_requires_boundary_evidence(self, tmp_path):
        staging, meta = _staged(tmp_path)
        demand = json.loads(meta.read_text())
        demand.update({"days": 2, "day_boundaries_s": [0, 86400, 172800]})
        meta.write_text(json.dumps(demand))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "flerdagsvalidering" in reason

        payload = json.loads((staging / "baseline.json").read_text())
        payload["multi_day_validation"] = {
            "schema_version": 1,
            "days": 2,
            "day_boundaries_s": [0, 86400, 172800],
            "complete": True,
            "seed_count": 1,
            "seeds": [{
                "complete": True,
                "days": [
                    {"day": 1, "loaded_delta": 10, "inserted_delta": 10,
                     "teleports_delta": 0, "loaded_at_boundary": 10,
                     "inserted_at_boundary": 10, "boundary_lag_s": 0},
                    {"day": 2, "loaded_delta": 11, "inserted_delta": 11,
                     "teleports_delta": 0, "loaded_at_boundary": 21,
                     "inserted_at_boundary": 21, "boundary_lag_s": 0},
                ],
            }],
            "per_day": [
                {"day": 1, "seed_count": 1, "loaded_delta_min": 10,
                 "inserted_delta_min": 10, "teleports_delta_max": 0,
                 "boundary_lag_s_max": 0,
                 "pending_insertion_at_boundary_max": 0},
                {"day": 2, "seed_count": 1, "loaded_delta_min": 11,
                 "inserted_delta_min": 11, "teleports_delta_max": 0,
                 "boundary_lag_s_max": 0,
                 "pending_insertion_at_boundary_max": 0},
            ],
        }
        (staging / "baseline.json").write_text(json.dumps(payload))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert ok, reason


class TestPublishStagedScenarios:
    def test_publish_replaces_live_set_and_prunes_stale(self, tmp_path):
        staging, _meta = _staged(tmp_path)
        live = tmp_path / "live"
        live.mkdir()
        (live / "index.json").write_text(json.dumps({"scenarios": [
            {"name": "old_closure", "file": "old_closure.json"}]}))
        (live / "old_closure.json").write_text(json.dumps({"flows": {}}))
        (live / "old_closure_traj.json").write_text(json.dumps({}))

        n = serve.publish_staged_scenarios(staging, live)

        assert n == 3
        live_names = {p.name for p in live.glob("*.json")}
        assert live_names == {"index.json", "baseline.json",
                              "baseline_traj.json"}
        index = json.loads((live / "index.json").read_text())
        assert index["scenarios"][0]["file"] == "baseline.json"

    def test_failed_validation_leaves_live_untouched(self, tmp_path):
        # The wiring contract: validation runs BEFORE publish; a refused
        # staging must leave the live set byte-identical.
        staging, meta = _staged(tmp_path, geh=50.0)
        live = tmp_path / "live"
        live.mkdir()
        (live / "index.json").write_text('{"scenarios": []}')
        ok, _ = serve.validate_staged_scenarios(staging, meta)
        assert not ok
        assert (live / "index.json").read_text() == '{"scenarios": []}'
