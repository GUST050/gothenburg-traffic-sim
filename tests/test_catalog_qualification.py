import json

from tools.benchmark_route_catalog import (
    evaluate_hard_gates,
    load_suite_gate_record,
)
from tools.adopt_route_catalog import adoption_payload
from tools.qualify_route_catalog import _validate_trial_binding
from tools import soak_route_catalog
from traffic_sim.demand.catalog_qualification import (
    PER_TRIAL_HARD_GATES,
    REQUIRED_HARD_GATES,
    SUITE_HARD_GATES,
    nearest_rank_p95,
    qualify_catalog_trials,
    semantic_route_digest,
)


def _gates():
    return {gate: True for gate in PER_TRIAL_HARD_GATES}


def _suite_gates():
    return {gate: True for gate in SUITE_HARD_GATES}


def _trials(catalog_wall=7.0, catalog_pfe=4.1):
    classes = ("weekday", "weekend", "holiday", "mixed")
    trials = []
    for index in range(32):
        trials.append({
            "trial_id": index,
            "order": "legacy_first" if index % 2 == 0 else "catalog_first",
            "day_class": classes[index % len(classes)],
            "legacy": {
                "wall_s": 10.0, "adapter_s": 0.0, "pfe_s": 4.0,
                "peak_rss_bytes": 2_000_000_000, "vehicles": 10_000,
                "pfe_shape_variables": 5000, "hard_gates": _gates(),
            },
            "catalog": {
                "wall_s": catalog_wall, "adapter_s": 1.0,
                "pfe_s": catalog_pfe,
                "peak_rss_bytes": 2_100_000_000, "vehicles": 10_050,
                "pfe_shape_variables": 3000, "hard_gates": _gates(),
            },
        })
    return trials


def test_nearest_rank_p95_uses_declared_rule():
    assert nearest_rank_p95(range(1, 31)) == 29


def test_qualification_adopts_only_when_every_gate_passes():
    report = qualify_catalog_trials(
        _trials(), catalog_build_s=8.0, suite_gates=_suite_gates())
    assert report["verdict"] == "adopt"
    assert all(report["gates"].values())

    slower = qualify_catalog_trials(
        _trials(catalog_wall=8.0), catalog_build_s=8.0,
        suite_gates=_suite_gates())
    assert slower["verdict"] == "reject"
    assert not slower["gates"]["cold_median_improves_25pct"]


def test_qualification_is_inconclusive_without_30_counterbalanced_trials():
    report = qualify_catalog_trials(
        _trials()[:5], catalog_build_s=8.0,
        suite_gates=_suite_gates())
    assert report["verdict"] == "inconclusive"


def test_suite_gates_are_evaluated_once_not_copied_into_trials():
    suite = _suite_gates()
    suite["warm_state_identity"] = False
    report = qualify_catalog_trials(
        _trials(), catalog_build_s=8.0, suite_gates=suite)
    assert report["verdict"] == "reject"
    assert report["suite_hard_failures"] == ["warm_state_identity"]
    assert report["trial_hard_failures"] == []


def test_qualification_rejects_material_population_drift():
    trials = _trials()
    trials[0]["catalog"]["vehicles"] = 10_101
    report = qualify_catalog_trials(
        trials, catalog_build_s=8.0, suite_gates=_suite_gates())
    assert report["verdict"] == "reject"
    assert not report["gates"]["paired_vehicle_population_delta_le_1pct"]


def test_semantic_digest_ignores_vehicle_id_and_departure(tmp_path):
    first_rou = tmp_path / "first.rou.xml"
    second_rou = tmp_path / "second.rou.xml"
    first_meta = tmp_path / "first.meta.json"
    second_meta = tmp_path / "second.meta.json"
    first_rou.write_text(
        '<routes><vehicle id="a" depart="1"><route edges="x y"/></vehicle></routes>')
    second_rou.write_text(
        '<routes><vehicle id="b" depart="999"><route edges="x y"/></vehicle></routes>')
    record = {"purpose": "arbete", "origin_edge": "x",
              "destination_edge": "y", "via_edge": None, "leg": "outbound"}
    first_meta.write_text(json.dumps({"candidates": {"a": record}}))
    second_meta.write_text(json.dumps({"candidates": {"b": record}}))
    assert semantic_route_digest(first_rou, first_meta) == semantic_route_digest(
        second_rou, second_meta)


def test_benchmark_gates_require_runtime_metadata_and_suite_evidence():
    meta = {
        "pfe_fit": {
            "vehicles": 10, "integer_sensor_constraints": 4,
            "integer_sensor_exact": 4, "integer_sensor_max_abs_error": 0.0,
            "integer_sensor_sum_abs_error": 0.0,
        },
        "candidate_provenance": {"status": "pass", "vehicles": 10},
        "agent_demand": {"n_agents": 10, "n_behavioural_agents": 10},
        "calibrated_structure": {
            "onward_after_last_sensor": {"n_routes_without_sensor": 0},
        },
    }
    gates = evaluate_hard_gates(meta, {"overall": "warn"})
    assert all(gates.values())
    meta["pfe_fit"]["integer_sensor_exact"] = 3
    assert not evaluate_hard_gates(meta, {"overall": "warn"})[
        "exact_sensor_targets"]
    meta["pfe_fit"]["integer_sensor_exact"] = 4
    meta["agent_demand"]["n_agents"] = 9
    assert not evaluate_hard_gates(meta, {"overall": "warn"})[
        "population_contract"]


def test_suite_gate_record_is_complete_and_boolean(tmp_path):
    path = tmp_path / "gates.json"
    records = {
        gate: {"status": "pass", "tests": ["tests/test_pfe.py"]}
        for gate in SUITE_HARD_GATES
    }
    path.write_text(json.dumps({
        "schema_version": 2,
        "kind": "route_catalog_suite_gate_evidence",
        "gates": records,
    }))
    assert load_suite_gate_record(path) == _suite_gates()
    broken = dict(records)
    broken.pop("warm_state_identity")
    path.write_text(json.dumps({
        "schema_version": 2,
        "kind": "route_catalog_suite_gate_evidence",
        "gates": broken,
    }))
    try:
        load_suite_gate_record(path)
    except ValueError as exc:
        assert "warm_state_identity" in str(exc)
    else:
        raise AssertionError("incomplete suite gates were accepted")


def test_soak_tool_imports_through_the_tools_package():
    assert len(soak_route_catalog.SOAK_FIXTURES) == 7


def test_qualification_rejects_unmatched_candidate_sizes_and_catalog_keys():
    trials = _trials()
    keys = {"weekday": "a" * 32, "weekend": "b" * 32}
    sizes = {"weekday": 6000, "weekend": 6000}
    for trial in trials:
        trial["legacy"]["candidate_n_total"] = 6000
        trial["catalog"]["candidate_n_total"] = 6000
        trial["catalog"]["catalog_keys"] = dict(keys)
        trial["catalog"]["catalog_selected_n_total"] = dict(sizes)
    assert _validate_trial_binding(trials, keys, sizes) == 6000

    trials[0]["legacy"]["candidate_n_total"] = 12000
    try:
        _validate_trial_binding(trials, keys, sizes)
    except ValueError as exc:
        assert "different candidate sizes" in str(exc)
    else:
        raise AssertionError("confounded benchmark sizes were accepted")


def test_adoption_cross_binds_qualification_build_and_catalog(monkeypatch):
    keys = {"weekday": "a" * 32, "weekend": "b" * 32}
    sizes = {"weekday": 6000, "weekend": 6000}
    build = {
        "results": {
            pool: {"key": keys[pool], "n_total": sizes[pool]}
            for pool in keys
        },
    }
    qualification = {
        "verdict": "adopt", "gates": {"correct": True},
        "evidence_binding": {
            "catalog_build_sha256": "c" * 64,
            "catalog_keys": keys,
            "catalog_selected_n_total": sizes,
            "trials_path": "validation/trials.json",
            "trials_sha256": "e" * 64,
            "suite_gates_path": "validation/suite.json",
            "suite_gates_sha256": "f" * 64,
        },
    }
    monkeypatch.setattr(
        "tools.adopt_route_catalog.route_catalog.catalog_entry_matches",
        lambda *args, **kwargs: True)

    payload = adoption_payload(
        qualification, build, qualification_sha256="d" * 64,
        catalog_build_sha256="c" * 64,
        qualification_path="validation/qualification.json",
        catalog_build_path="validation/build.json", catalog_root="catalog")

    assert payload["schema_version"] == 3
    assert payload["catalog_keys"] == keys
    broken = dict(qualification)
    broken["evidence_binding"] = dict(
        qualification["evidence_binding"], catalog_build_sha256="e" * 64)
    try:
        adoption_payload(
            broken, build, qualification_sha256="d" * 64,
            catalog_build_sha256="c" * 64,
            qualification_path="validation/qualification.json",
            catalog_build_path="validation/build.json", catalog_root="catalog")
    except ValueError as exc:
        assert "not bound" in str(exc)
    else:
        raise AssertionError("unbound qualification was accepted")
