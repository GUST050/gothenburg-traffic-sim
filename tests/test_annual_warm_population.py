import copy
import gzip
import hashlib
import json

import pytest

from traffic_sim.core.contracts import DemandBuildSpec
from traffic_sim.simulation.annual_warm_plan import (
    build_annual_warm_plan,
    default_2027_coverage_spec,
    iter_work_units,
)
from traffic_sim.simulation.annual_warm_population import (
    annual_unit_context,
    build_annual_prefix_runner,
    build_unit_metadata,
    validate_unit_artifact,
)
from tools.audit_annual_warm_chain import _behavioral_evidence_equal, _positions
from traffic_sim.simulation.annual_warm_store import AnnualWarmArtifactStore


RUNTIME = {
    "sumo_version": "SUMO test",
    "platform": "test-platform",
    "simulation_mode": "meso",
    "metric_schema": "closure_decision_metrics_v1",
}


@pytest.fixture(scope="module")
def plan():
    return build_annual_warm_plan(
        default_2027_coverage_spec(),
        input_fingerprints={
            "source": {"path": "source", "bytes": 1, "sha256": "a" * 64}
        },
        runtime_identity=RUNTIME,
    )


def _unit(plan):
    return next(iter(iter_work_units(plan)))


def _hint(unit):
    return {key: unit[key] for key in (
        "request_id", "demand_build_key", "checkpoint_s", "seed", "variant"
    )}


def _archive_record(context):
    values = {
        "demand_meta.json": b"{}",
        "demand_build_spec.json": b"{}",
        "candidates.rou.xml": b"<routes/>",
        "candidates.meta.json": b"{}",
        "calibrated.rou.xml": b"<routes/>",
        "calibrated.agents.json": b"{}",
        "calibrated_v1.rou.xml": b"<routes/>",
        "calibrated_v1.agents.json": b"{}",
        "calibrated_v2.rou.xml": b"<routes/>",
        "calibrated_v2.agents.json": b"{}",
    }
    outputs = [
        {
            "name": name,
            "bytes": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
        for name, value in values.items()
    ]
    return {
        "run_id": "demand-one",
        "archive": "/immutable/demand-one",
        "finished_at": "2026-08-03T00:00:00Z",
        "demand_build_spec": context["demand_build_spec"],
        "archive_manifest_sha256": hashlib.sha256(b"{}").hexdigest(),
        "outputs": outputs,
        "archive_content_key": hashlib.sha256(json.dumps(
            outputs, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()).hexdigest(),
    }


def _members(root):
    root.mkdir(parents=True)
    values = {
        "state.xml.gz": gzip.compress(b"state", mtime=0),
        "prefix_evidence.json": b'{}',
        "route.rou.xml": b"<routes/>",
        "demand_meta.json": b"{}",
        "demand_build_spec.json": b"{}",
        "demand_manifest.json": b"{}",
    }
    result = {}
    for name, value in values.items():
        path = root / name
        path.write_bytes(value)
        result[name] = path
    return result


def test_bound_hint_resolves_exact_unit_without_ledger_scan(plan):
    unit = _unit(plan)
    context = annual_unit_context(plan, unit["unit_id"], unit_hint=_hint(unit))
    assert context["unit"] == {
        key: value for key, value in unit.items()
        if key not in {"state", "attempts", "artifact_content_key", "error"}
    }
    assert context["request"]["request_id"] == unit["request_id"]


def test_audit_and_population_share_the_exact_runner_constructor(plan, tmp_path):
    unit = _unit(plan)
    context = annual_unit_context(plan, unit["unit_id"], unit_hint=_hint(unit))
    captured = {}

    class FakeRunner:
        def __init__(self, spec, **kwargs):
            captured["spec"] = spec
            captured["kwargs"] = kwargs

    runner = build_annual_prefix_runner(
        plan,
        request=context["request"],
        required=DemandBuildSpec.from_dict(context["demand_build_spec"]),
        archive=tmp_path / "archive",
        reference_edge="edge-a",
        runner_factory=FakeRunner,
        controller_factory=lambda: "controller",
    )
    assert isinstance(runner, FakeRunner)
    assert captured["spec"].directed_edges == ("edge-a",)
    assert captured["spec"].interday_policy == "independent_daily_reset_v1"
    assert captured["kwargs"]["warm_execution"] is True
    assert captured["kwargs"]["boundary_controller"] == "controller"
    assert captured["kwargs"]["expected_demand_spec"].build_key \
        == unit["demand_build_key"]


def test_chain_audit_positions_are_strictly_ordered():
    assert _positions("2,48,96") == (2, 48, 96)
    for value in ("", "2,2", "48,2", "0,2", "one"):
        with pytest.raises(ValueError):
            _positions(value)


def test_chain_audit_accepts_only_nonbehavioral_loaded_lookahead():
    cold = {
        "completed_trips": {"trip_count": 3, "total_time_loss_s": 1.2},
        "counters": {"loaded": 9, "inserted": 5, "teleport_total": 0},
        "active_accumulator": {"entries": {"v": 0.125}},
        "teleport_reasons": {},
    }
    chained = copy.deepcopy(cold)
    chained["counters"]["loaded"] = 5
    assert _behavioral_evidence_equal(chained, cold)

    for mutation in (
        lambda value: value["counters"].update(inserted=4),
        lambda value: value["counters"].update(teleport_total=1),
        lambda value: value["active_accumulator"]["entries"].update(v=0.126),
        lambda value: value["counters"].update(loaded=10),
        lambda value: value["counters"].update(loaded=4),
    ):
        bad = copy.deepcopy(chained)
        mutation(bad)
        assert not _behavioral_evidence_equal(bad, cold)


def test_only_exact_demand_provenance_builds_metadata(plan):
    unit = _unit(plan)
    context = annual_unit_context(plan, unit["unit_id"], unit_hint=_hint(unit))
    metadata = build_unit_metadata(
        plan, unit["unit_id"], demand_archive=_archive_record(context),
        unit_hint=_hint(unit),
    )
    assert metadata["unit_id"] == unit["unit_id"]
    wrong = _archive_record(context)
    wrong["demand_build_spec"] = {**context["demand_build_spec"], "build_key": "x"}
    with pytest.raises(ValueError, match="does not match"):
        build_unit_metadata(
            plan, unit["unit_id"], demand_archive=wrong, unit_hint=_hint(unit)
        )


def test_artifact_validation_rederives_bound_identity(plan, tmp_path):
    unit = _unit(plan)
    context = annual_unit_context(plan, unit["unit_id"], unit_hint=_hint(unit))
    metadata = build_unit_metadata(
        plan, unit["unit_id"], demand_archive=_archive_record(context),
        unit_hint=_hint(unit),
    )
    artifact = AnnualWarmArtifactStore(tmp_path / "store").pack_artifact(
        plan_content_key=plan["content_key"], unit_id=unit["unit_id"],
        members=_members(tmp_path / "members"), metadata=metadata,
    )
    assert validate_unit_artifact(plan, unit["unit_id"], artifact) == artifact
    forged = copy.deepcopy(artifact)
    forged["metadata"]["checkpoint_s"] += 900
    with pytest.raises(ValueError, match="not in the plan"):
        validate_unit_artifact(plan, unit["unit_id"], forged)

    wrong_route = copy.deepcopy(artifact)
    wrong_route["members"]["route.rou.xml"]["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="route.rou.xml differs"):
        validate_unit_artifact(plan, unit["unit_id"], wrong_route)


def test_artifact_requires_the_exact_immediate_predecessor(plan, tmp_path):
    units = iter(iter_work_units(plan))
    predecessor = next(units)
    next(units)
    next(units)
    unit = next(units)
    assert (unit["seed"], unit["variant"]) == (
        predecessor["seed"], predecessor["variant"]
    )
    context = annual_unit_context(plan, unit["unit_id"], unit_hint=_hint(unit))
    with pytest.raises(ValueError, match="exact plan chain"):
        build_unit_metadata(
            plan, unit["unit_id"], demand_archive=_archive_record(context),
            unit_hint=_hint(unit), predecessor_unit_id="annual-unit-wrong",
        )
    metadata = build_unit_metadata(
        plan, unit["unit_id"], demand_archive=_archive_record(context),
        unit_hint=_hint(unit), predecessor_unit_id=predecessor["unit_id"],
    )
    artifact = AnnualWarmArtifactStore(tmp_path / "store").pack_artifact(
        plan_content_key=plan["content_key"], unit_id=unit["unit_id"],
        members=_members(tmp_path / "members"), metadata=metadata,
    )
    forged = copy.deepcopy(artifact)
    forged["metadata"]["population"]["predecessor_unit_id"] = (
        "annual-unit-wrong"
    )
    with pytest.raises(ValueError, match="population provenance"):
        validate_unit_artifact(plan, unit["unit_id"], forged)
