import json
from pathlib import Path

import run_scenario
from traffic_sim.simulation.sensor_fit import assess_output_fit, summarize_rows


N_INTERVALS = 96
AGGREGATION_QUARTERS = 4
LOAD_REPORT = (
    Path(__file__).parents[1]
    / "validation/vehicle_load_and_edgedata_diagnostic_2026-08-22.json"
)
PAIRED_REPORT = (
    Path(__file__).parents[1]
    / "validation/edgedata_attributes_paired_adoption_2026-08-22.json"
)


def _rows(stations):
    return [
        {
            "sensor_id": f"sensor-{index:02d}",
            "target_mean": [100.0] * N_INTERVALS,
            "simulated_mean_raw": [100.0] * N_INTERVALS,
        }
        for index in range(stations)
    ]


def _audit(rows):
    summary = summarize_rows(
        rows,
        n_intervals=N_INTERVALS,
        aggregation_quarters=AGGREGATION_QUARTERS,
    )
    return {
        "directions": rows,
        "stations": rows,
        "output_fit": {
            "uses_raw_ensemble_mean": True,
            "aggregation_quarters": AGGREGATION_QUARTERS,
            "ensemble": summary,
            "station_ensemble": summary,
        },
    }


def test_fifty_sensor_output_contract_is_complete_and_exact():
    rows = _rows(50)

    result = assess_output_fit(
        _audit(rows), n_intervals=N_INTERVALS, days=1
    )

    assert result["errors"] == []
    assert result["directions"]["edge_quarters"] == 50 * 24
    assert result["stations"]["edge_quarters"] == 50 * 24
    assert result["directions"]["geh_lt_5_pct"] == 100.0


def test_one_bad_station_among_fifty_still_fails_closed():
    rows = _rows(50)
    rows[-1]["simulated_mean_raw"][0:4] = [200.0] * 4

    result = assess_output_fit(
        _audit(rows), n_intervals=N_INTERVALS, days=1
    )

    assert any("GEH" in error for error in result["errors"])
    assert result["stations"]["max_geh"] >= 5.0


def test_scenario_audit_discovers_fifty_sensors_without_a_fixed_cap(
        tmp_path, monkeypatch):
    features = []
    for index in range(50):
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[11.0, 57.0], [11.001, 57.001]],
            },
            "properties": {
                "id": f"edge-{index:02d}",
                "name": f"Road {index:02d}",
                "sensor_id": f"sensor-{index:02d}",
                "level": "Direction",
            },
        })
    network = tmp_path / "network.geojson"
    network.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": features,
    }))
    monkeypatch.setattr(run_scenario, "GEO_PATH", network)

    audit_edges = run_scenario.sensor_audit_edges()

    assert len(audit_edges) == 50
    assert [item["sensor_id"] for item in audit_edges] == [
        f"sensor-{index:02d}" for index in range(50)
    ]
    assert all(item["measurement"] == "directed" for item in audit_edges)


def test_vehicle_scale_report_rejects_insertion_backlog_as_capacity():
    report = json.loads(LOAD_REPORT.read_text())
    rows = report["vehicle_scale_comparison_with_minimal_edge_output"]

    complete = [row for row in rows if row.get("valid_complete_population", True)]
    rejected = [row for row in rows if not row.get("valid_complete_population", True)]

    assert complete
    assert all(row["loaded"] == row["inserted"] for row in complete)
    assert all(row["waiting"] == 0 for row in complete)
    assert rejected
    assert all(
        row["loaded"] != row["inserted"] or row["waiting"] > 0
        for row in rejected
    )


def test_minimal_edgedata_report_preserves_consumed_values_but_is_not_adopted():
    report = json.loads(LOAD_REPORT.read_text())
    comparison = report["edge_output_comparison"]["comparison"]

    assert comparison["flow_keys_equal"] is True
    assert comparison["flow_arrays_equal"] is True
    assert comparison["recovery_buckets_equal"] is True
    assert comparison["wall_reduction_pct"] > 0
    assert comparison["byte_reduction_pct"] > 0
    assert comparison["production_adoption_authorized"] is False


def test_paired_edgedata_gate_passes_without_claiming_production_adoption():
    report = json.loads(PAIRED_REPORT.read_text())
    equivalence = report["equivalence"]

    assert report["production_adopted"] is False
    assert report["recommendation"] == "eligible_for_production_default_change"
    assert report["execution"]["trials_per_case_per_arm"] >= 10
    assert report["execution"]["seed_executions"] == 120
    assert report["reading"]["paired_gate_passed"] is True
    assert report["reading"]["candidate_closure_p95_meets_target"] is False
    assert equivalence == {
        "scenario_digest_mismatches": 0,
        "trajectory_digest_mismatches": 0,
        "reference_mismatches": 0,
        "all_closures_verified_clean": True,
        "all_loaded_equal_inserted": True,
        "all_waiting_at_end_zero": True,
        "all_running_at_end_zero": True,
        "all_teleports_zero": True,
        "all_collisions_zero": True,
    }
