import hashlib
import json
from pathlib import Path
import sys

import pytest

from tools import benchmark_route_catalog as catalog_benchmark
from tools.benchmark_route_catalog import (
    evaluate_hard_gates,
    load_suite_gate_record,
)
from tools.adopt_route_catalog import adoption_payload
from tools import qualify_route_catalog as qualify_tool
from tools.qualify_route_catalog import _validate_trial_binding
from tools import soak_route_catalog
from traffic_sim.demand import catalog_qualification as qualification
from traffic_sim.demand.catalog_qualification import (
    OPERATIONAL_REQUIRED_SOURCE_PATHS,
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


def _operational_trials():
    trials = _trials()[:4]
    for trial in trials:
        for arm in ("legacy", "catalog"):
            trial[arm]["variant_mode"] = "q50_only"
    return trials


def _operational_trial_payload(keys=None, sizes=None):
    keys = keys or {"weekday": "a" * 32, "weekend": "b" * 32}
    sizes = sizes or {"weekday": 6000, "weekend": 6000}
    trials = _operational_trials()
    fixtures = {
        "weekday": ("2027-09-08", 1),
        "weekend": ("2027-09-11", 1),
        "holiday": ("2027-05-13", 1),
        "mixed": ("2027-09-10", 2),
    }
    pools = {
        "weekday": {"weekday"},
        "weekend": {"weekend"},
        "holiday": {"weekday"},
        "mixed": {"weekday", "weekend"},
    }
    for trial in trials:
        trial["date"], trial["days"] = fixtures[trial["day_class"]]
        for arm in ("legacy", "catalog"):
            trial[arm]["candidate_n_total"] = 6000
            trial[arm]["candidate_source"] = arm
        expected = pools[trial["day_class"]]
        trial["legacy"]["catalog_keys"] = {}
        trial["legacy"]["catalog_selected_n_total"] = {}
        trial["catalog"]["catalog_keys"] = {
            pool: keys[pool] for pool in expected
        }
        trial["catalog"]["catalog_selected_n_total"] = {
            pool: sizes[pool] for pool in expected
        }
    return {
        "schema_version": 3,
        "kind": "route_catalog_paired_trials",
        "qualification_mode": "operational_four_class_q50_v1",
        "variant_mode": "q50_only",
        "requested_pairs": 4,
        "candidate_n_total": 6000,
        "execute": True,
        "catalog_build_evidence": {"sha256": "c" * 64},
        "suite_gate_evidence": {"sha256": "f" * 64},
        "trials": trials,
    }


def test_operational_qualification_accepts_exactly_four_bound_q50_classes():
    report = qualification.qualify_operational_catalog_trials(
        _operational_trials(), catalog_build_s=8.0,
        suite_gates=_suite_gates())

    assert report["verdict"] == "adopt"
    assert report["qualification_mode"] == (
        "operational_four_class_q50_v1")
    assert report["trials"] == 4
    assert all(report["gates"].values())
    assert report["claim_boundary"] == {
        "current_catalog_correctness": True,
        "current_four_class_non_regression": True,
        "new_statistical_performance_claim": False,
        "general_performance_claim": False,
    }


@pytest.mark.parametrize("mutation", [
    "three_trials", "duplicate_class", "single_order", "wrong_variant",
])
def test_operational_qualification_is_inconclusive_without_exact_design(mutation):
    trials = _operational_trials()
    if mutation == "three_trials":
        trials.pop()
    elif mutation == "duplicate_class":
        trials[-1]["day_class"] = "weekday"
    elif mutation == "single_order":
        for trial in trials:
            trial["order"] = "legacy_first"
    else:
        trials[0]["catalog"]["variant_mode"] = "direction_stress"

    report = qualification.qualify_operational_catalog_trials(
        trials, catalog_build_s=8.0, suite_gates=_suite_gates())

    assert report["verdict"] == "inconclusive"
    assert report["errors"]


@pytest.mark.parametrize("mutation", [
    "hard_gate", "population", "slower_class", "rss",
])
def test_operational_qualification_rejects_a_failed_runtime_gate(mutation):
    trials = _operational_trials()
    if mutation == "hard_gate":
        trials[0]["catalog"]["hard_gates"]["confidence_health"] = False
    elif mutation == "population":
        trials[0]["catalog"]["vehicles"] = 10_101
    elif mutation == "slower_class":
        trials[0]["catalog"]["wall_s"] = 10.1
    else:
        trials[0]["catalog"]["peak_rss_bytes"] = 9 * 1024 ** 3

    report = qualification.qualify_operational_catalog_trials(
        trials, catalog_build_s=8.0, suite_gates=_suite_gates())

    assert report["verdict"] == "reject"
    assert not all(report["gates"].values())


def _benchmark_argv(tmp_path, *, mode, trials):
    return [
        "benchmark_route_catalog.py",
        "--catalog-root", str(tmp_path / "catalog"),
        "--suite-gates", str(tmp_path / "suite.json"),
        "--catalog-build", str(tmp_path / "build.json"),
        "--out", str(tmp_path / "trials.json"),
        "--qualification-mode", mode,
        "--trials", str(trials),
    ]


def test_operational_benchmark_cli_requires_exactly_four_trials(
        tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", _benchmark_argv(
        tmp_path, mode="operational-four-class-q50", trials=4))
    args = catalog_benchmark.parse_args()
    assert args.trials == 4
    assert args.qualification_mode == "operational-four-class-q50"

    monkeypatch.setattr(sys, "argv", _benchmark_argv(
        tmp_path, mode="operational-four-class-q50", trials=5))
    with pytest.raises(SystemExit):
        catalog_benchmark.parse_args()


def test_full_benchmark_cli_still_requires_thirty_trials(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", _benchmark_argv(
        tmp_path, mode="full-30-pair", trials=4))
    with pytest.raises(SystemExit):
        catalog_benchmark.parse_args()


def test_resume_requires_exact_campaign_identity():
    expected = {
        "schema_version": 3,
        "kind": "route_catalog_paired_trials",
        "qualification_mode": "operational_four_class_q50_v1",
        "variant_mode": "q50_only",
        "requested_pairs": 4,
        "fixtures": [{"name": "weekday"}],
        "candidate_n_total": 6000,
        "catalog_build_evidence": {"sha256": "a" * 64},
        "suite_gate_evidence": {"sha256": "b" * 64},
        "execute": True,
        "trials": [],
    }
    existing = dict(expected, trials=[{"trial_id": 1}])
    catalog_benchmark.validate_resume_campaign(existing, expected)

    drifted = dict(existing, variant_mode="direction_stress")
    with pytest.raises(ValueError, match="does not match"):
        catalog_benchmark.validate_resume_campaign(drifted, expected)


def test_q50_arm_scope_is_derived_from_produced_metadata():
    meta = {
        "n_variants": 1,
        "demand_variant_contract": {
            "schema_version": 1,
            "mode": "q50_only",
            "variants": [{"name": "q50"}],
        },
    }
    assert catalog_benchmark.demand_variant_mode(meta) == "q50_only"
    meta["n_variants"] = 3
    with pytest.raises(ValueError, match="q50"):
        catalog_benchmark.demand_variant_mode(meta)


def test_qualification_dispatch_is_explicit_and_fail_closed():
    assert qualify_tool.qualification_function_for_payload({}) is (
        qualification.qualify_catalog_trials)
    assert qualify_tool.qualification_function_for_payload({
        "schema_version": 3,
        "qualification_mode": "operational_four_class_q50_v1",
        "variant_mode": "q50_only",
    }) is qualification.qualify_operational_catalog_trials
    with pytest.raises(ValueError, match="qualification mode"):
        qualify_tool.qualification_function_for_payload({
            "qualification_mode": "unknown",
        })


def test_operational_adoption_requires_the_narrow_claim_boundary(monkeypatch):
    keys = {"weekday": "a" * 32, "weekend": "b" * 32}
    sizes = {"weekday": 6000, "weekend": 6000}
    build = {"elapsed_s": 8.0, "results": {
        pool: {"key": keys[pool], "n_total": sizes[pool]}
        for pool in keys
    }}
    report = qualification.qualify_operational_catalog_trials(
        _operational_trials(), catalog_build_s=8.0,
        suite_gates=_suite_gates())
    report["evidence_binding"] = {
        "candidate_n_total": 6000,
        "catalog_build_sha256": "c" * 64,
        "catalog_keys": keys,
        "catalog_selected_n_total": sizes,
        "trials_path": "validation/trials.json",
        "trials_sha256": "e" * 64,
        "suite_gates_path": "validation/suite.json",
        "suite_gates_sha256": "f" * 64,
    }
    monkeypatch.setattr(
        "tools.adopt_route_catalog.route_catalog.catalog_entry_matches",
        lambda *args, **kwargs: True)

    payload = adoption_payload(
        report, build, qualification_sha256="d" * 64,
        catalog_build_sha256="c" * 64,
        qualification_path="validation/qualification.json",
        catalog_build_path="validation/build.json", catalog_root="catalog",
        trial_payload=_operational_trial_payload(keys, sizes),
        suite_gates=_suite_gates())
    assert payload["catalog_keys"] == keys

    report["claim_boundary"]["new_statistical_performance_claim"] = True
    with pytest.raises(ValueError, match="operational qualification"):
        adoption_payload(
            report, build, qualification_sha256="d" * 64,
            catalog_build_sha256="c" * 64,
            qualification_path="validation/qualification.json",
            catalog_build_path="validation/build.json", catalog_root="catalog",
            trial_payload=_operational_trial_payload(keys, sizes),
            suite_gates=_suite_gates())


def test_operational_evidence_recomputes_report_from_bound_trials():
    payload = _operational_trial_payload()
    report = qualification.qualify_operational_catalog_trials(
        payload["trials"], catalog_build_s=8.0,
        suite_gates=_suite_gates())
    report["evidence_binding"] = {
        "candidate_n_total": 6000,
        "catalog_build_sha256": "c" * 64,
        "suite_gates_sha256": "f" * 64,
        "catalog_keys": {"weekday": "a" * 32, "weekend": "b" * 32},
        "catalog_selected_n_total": {"weekday": 6000, "weekend": 6000},
    }
    qualification.validate_operational_qualification_evidence(
        report, trial_payload=payload, catalog_build_s=8.0,
        suite_gates=_suite_gates())

    payload["trials"][0]["catalog"]["wall_s"] = 11.0
    with pytest.raises(ValueError, match="reproduce"):
        qualification.validate_operational_qualification_evidence(
            report, trial_payload=payload, catalog_build_s=8.0,
            suite_gates=_suite_gates())


@pytest.mark.parametrize("mutation", [
    "wrong_weekday_pool", "mixed_missing_pool", "wrong_date", "wrong_source",
    "malformed_pool_mapping",
])
def test_operational_trial_binding_rejects_wrong_fixture_or_pool(mutation):
    payload = _operational_trial_payload()
    trials = payload["trials"]
    if mutation == "wrong_weekday_pool":
        trials[0]["catalog"]["catalog_keys"] = {"weekend": "b" * 32}
        trials[0]["catalog"]["catalog_selected_n_total"] = {"weekend": 6000}
    elif mutation == "mixed_missing_pool":
        trials[3]["catalog"]["catalog_keys"].pop("weekend")
        trials[3]["catalog"]["catalog_selected_n_total"].pop("weekend")
    elif mutation == "wrong_date":
        trials[0]["date"] = "2027-09-09"
    elif mutation == "malformed_pool_mapping":
        trials[0]["catalog"]["catalog_keys"] = ["weekday"]
    else:
        trials[0]["catalog"]["candidate_source"] = "legacy"
    with pytest.raises(ValueError, match="operational trial"):
        qualification.validate_operational_trial_binding(
            trials,
            catalog_keys={"weekday": "a" * 32, "weekend": "b" * 32},
            catalog_sizes={"weekday": 6000, "weekend": 6000},
            candidate_n_total=6000)


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
    healthy = {
        "overall": "warn",
        "sections": {
            "counts_fit": {"status": "pass"},
            "structure": {"status": "warn"},
            "simulation": {"status": "pass"},
        },
    }
    gates = evaluate_hard_gates(meta, healthy)
    assert all(gates.values())
    meta["pfe_fit"]["integer_sensor_exact"] = 3
    assert not evaluate_hard_gates(meta, healthy)["exact_sensor_targets"]
    meta["pfe_fit"]["integer_sensor_exact"] = 4
    meta["agent_demand"]["n_agents"] = 9
    assert not evaluate_hard_gates(meta, healthy)["population_contract"]
    meta["agent_demand"]["n_agents"] = 10


def test_every_per_trial_hard_gate_can_actually_fail():
    """A gate that cannot fail is not a gate.

    Found 2026-08-26: `confidence_health` was `overall != "fail"`, and a
    validation report never emits "fail" for anything — so it was
    unconditionally true, and `candidate_structure` collapsed to "the
    metadata exists". Both sat in the adopted qualification as hard gates.
    This pins falsifiability itself, which is the property that was missing.
    """
    def healthy_inputs():
        meta = {
            "pfe_fit": {
                "vehicles": 10, "integer_sensor_constraints": 4,
                "integer_sensor_exact": 4,
                "integer_sensor_max_abs_error": 0.0,
                "integer_sensor_sum_abs_error": 0.0,
            },
            "candidate_provenance": {"status": "pass", "vehicles": 10},
            "agent_demand": {"n_agents": 10, "n_behavioural_agents": 10},
            "calibrated_structure": {
                "onward_after_last_sensor": {"n_routes_without_sensor": 0},
            },
        }
        validation = {
            "overall": "warn",
            "sections": {
                "counts_fit": {"status": "pass"},
                "structure": {"status": "warn"},
                "simulation": {"status": "pass"},
            },
        }
        return meta, validation

    # One targeted break per gate, each the failure the gate names.
    breaks = {
        "exact_sensor_targets":
            lambda m, v: m["pfe_fit"].update(integer_sensor_exact=3),
        "zero_integer_residual":
            lambda m, v: m["pfe_fit"].update(integer_sensor_max_abs_error=1.0),
        "population_contract":
            lambda m, v: m["agent_demand"].update(n_agents=9),
        "sensor_anchor_contract":
            lambda m, v: m["calibrated_structure"][
                "onward_after_last_sensor"].update(n_routes_without_sensor=3),
        "candidate_structure":
            lambda m, v: v["sections"].update(structure={"status": "missing"}),
        "route_agent_provenance":
            lambda m, v: m["candidate_provenance"].update(status="fail"),
        "confidence_health":
            lambda m, v: v["sections"].update(
                simulation={"status": "missing"}),
    }
    assert set(breaks) == set(PER_TRIAL_HARD_GATES)

    baseline_meta, baseline_validation = healthy_inputs()
    assert all(evaluate_hard_gates(baseline_meta, baseline_validation).values())

    for gate, break_it in breaks.items():
        meta, validation = healthy_inputs()
        break_it(meta, validation)
        assert not evaluate_hard_gates(meta, validation)[gate], (
            f"{gate} did not fail when its own contract was broken")


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


def test_operational_suite_gate_record_requires_current_source_hashes(tmp_path):
    path = tmp_path / "gates.json"
    source = "tests/test_pfe.py"
    digest = hashlib.sha256(
        (Path(__file__).parents[1] / source).read_bytes()).hexdigest()
    sources = {source: digest}
    for relative in OPERATIONAL_REQUIRED_SOURCE_PATHS:
        sources[relative] = hashlib.sha256(
            (Path(__file__).parents[1] / relative).read_bytes()).hexdigest()
    payload = {
        "schema_version": 2,
        "kind": "route_catalog_suite_gate_evidence",
        "gates": {
            gate: {"status": "pass", "tests": [source]}
            for gate in SUITE_HARD_GATES
        },
        "source_sha256": sources,
    }
    path.write_text(json.dumps(payload))
    assert load_suite_gate_record(
        path, require_source_hashes=True) == _suite_gates()

    payload["source_sha256"][source] = "0" * 64
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="source binding"):
        load_suite_gate_record(path, require_source_hashes=True)

    payload["source_sha256"][source] = digest
    payload["source_sha256"].pop(sorted(OPERATIONAL_REQUIRED_SOURCE_PATHS)[0])
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="lacks source hashes"):
        load_suite_gate_record(path, require_source_hashes=True)

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
