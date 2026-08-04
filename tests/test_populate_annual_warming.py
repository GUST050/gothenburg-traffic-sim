import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import populate_annual_warming as tool
from traffic_sim.simulation.annual_warm_plan import (
    build_annual_warm_plan,
    default_2027_coverage_spec,
)
from traffic_sim.simulation.annual_warm_progress import AnnualWarmProgressStore
from traffic_sim.simulation.annual_warm_population import (
    annual_unit_context,
    build_unit_metadata,
)
from traffic_sim.simulation.annual_warm_store import AnnualWarmArtifactStore
from traffic_sim.simulation.monthly_warm_state import build_prefix_evidence
from traffic_sim.simulation.warm_state_boundary import (
    BOUNDARY_ACTIVE_SCHEMA,
    PREFIX_ACCUMULATOR_SCHEMA,
    SNAPSHOT_FACTS_SCHEMA,
    WARM_OUTPUT_PRECISION,
)


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
            "source": {"path": "source", "bytes": 1, "sha256": "f" * 64}
        },
        runtime_identity=RUNTIME,
    )


@pytest.fixture(autouse=True)
def _synthetic_plan_source_seal(monkeypatch):
    # Synthetic plan fingerprints intentionally name no repository file.
    monkeypatch.setattr(tool, "_verify_plan_source_seal", lambda _plan: None)


def _archive_record(context, root):
    root = Path(root)
    defaults = {
        "demand_meta.json": b"{}",
        "demand_build_spec.json": json.dumps(
            context["demand_build_spec"], sort_keys=True
        ).encode(),
        "candidates.rou.xml": b"<routes/>",
        "candidates.meta.json": b"{}",
        "calibrated.rou.xml": b"<routes/>",
        "calibrated.agents.json": b"{}",
        "calibrated_v1.rou.xml": b"<routes/>",
        "calibrated_v1.agents.json": b"{}",
        "calibrated_v2.rou.xml": b"<routes/>",
        "calibrated_v2.agents.json": b"{}",
        "manifest.json": b"{}",
    }

    def raw(name):
        path = root / name
        return path.read_bytes() if path.is_file() else defaults[name]

    outputs = [
        {
            "name": name,
            "bytes": len(raw(name)),
            "sha256": hashlib.sha256(raw(name)).hexdigest(),
        }
        for name in (
            "demand_meta.json", "demand_build_spec.json",
            "candidates.rou.xml", "candidates.meta.json",
            "calibrated.rou.xml", "calibrated.agents.json",
            "calibrated_v1.rou.xml", "calibrated_v1.agents.json",
            "calibrated_v2.rou.xml", "calibrated_v2.agents.json",
        )
    ]
    return {
        "run_id": "fake-demand",
        "archive": str(root),
        "finished_at": "2026-08-03T00:00:00Z",
        "demand_build_spec": context["demand_build_spec"],
        "archive_manifest_sha256": hashlib.sha256(raw("manifest.json")).hexdigest(),
        "outputs": outputs,
        "archive_content_key": hashlib.sha256(json.dumps(
            outputs, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()).hexdigest(),
    }


def _member_files(root, warm_point_s, demand_build_spec):
    root.mkdir(parents=True, exist_ok=True)
    accumulator = {
        "schema": PREFIX_ACCUMULATOR_SCHEMA,
        "warm_point_s": warm_point_s,
        "output_precision": WARM_OUTPUT_PRECISION,
        "active_population_digest": hashlib.sha256(json.dumps({
            "schema": BOUNDARY_ACTIVE_SCHEMA,
            "warm_point_s": warm_point_s,
            "captured_at_s": float(warm_point_s),
            "vehicle_ids": [],
        }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest(),
        "entries": {},
    }
    accumulator["digest"] = hashlib.sha256(json.dumps(
        accumulator, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    prefix = build_prefix_evidence(
        completed_trips={"total_time_loss_s": 0.0, "trip_count": 0},
        completed_records={}, completed_record_order=[], queue_maximum=None,
        counters={"loaded": 0, "inserted": 0, "teleport_total": 0,
                  "teleport_reasons": {}},
        recovery_buckets=[], warm_point_s=warm_point_s,
        snapshot_facts={
            "schema": SNAPSHOT_FACTS_SCHEMA,
            "warm_point_s": warm_point_s,
            "captured_at_s": float(warm_point_s),
            "active_vehicle_count": 0,
            "save_state_rng": True,
            "save_state_precision": 16,
        },
        active_accumulator=accumulator,
    )
    values = {
        "state.xml.gz": gzip.compress(
            (f'<snapshot type="meso" version="test" '
             f'time="{warm_point_s:.3f}"/>').encode(), mtime=0),
        "prefix_evidence.json": json.dumps(prefix).encode(),
        "route.rou.xml": b"<routes/>",
        "demand_meta.json": b"{}",
        "demand_build_spec.json": json.dumps(
            demand_build_spec, sort_keys=True
        ).encode(),
        "demand_manifest.json": b"{}",
    }
    result = {}
    for name, value in values.items():
        path = root / name
        path.write_bytes(value)
        result[name] = path
    return result


def _progress(plan, root):
    return AnnualWarmProgressStore(root / tool.PROGRESS_FILENAME, plan)


def _hint(unit):
    return {key: unit[key] for key in (
        "request_id", "demand_build_key", "checkpoint_s", "seed", "variant"
    )}


def test_initialize_is_idempotent_and_contains_plan_progress_and_store(plan, tmp_path):
    root = tmp_path / "annual"
    first = tool.initialize_root(plan, root)
    second = tool.initialize_root(plan, root)
    assert first == second
    names = {path.name for path in root.iterdir()}
    assert {".staging", "plan.json", "progress.sqlite3", "store"} <= names
    assert names <= {
        ".staging", "plan.json", "progress.sqlite3", "progress.sqlite3-shm",
        "progress.sqlite3-wal", "store",
    }
    assert _progress(plan, root).summary() == first


def test_pool_reuses_runner_within_one_exact_demand(monkeypatch, tmp_path):
    from traffic_sim.simulation import monthly_sumo

    created = []

    class FakeRunner:
        def __init__(self, spec, **kwargs):
            self.spec = spec
            self.cleaned = False
            created.append(self)

        def cleanup(self):
            self.cleaned = True

    monkeypatch.setattr(monthly_sumo, "ArchivedDemandSumoRunner", FakeRunner)
    tool._POOL_RUNNER_KEY = None
    tool._POOL_RUNNER = None
    spec = SimpleNamespace(content_key="spec-one")
    required = SimpleNamespace(build_key="demand-one")
    kwargs = {"archive": tmp_path, "expected_demand_spec": required}

    first = tool._pooled_runner_factory(spec, **kwargs)
    second = tool._pooled_runner_factory(spec, **kwargs)
    assert first is second
    assert len(created) == 1

    replacement = tool._pooled_runner_factory(
        SimpleNamespace(content_key="spec-two"), **kwargs
    )
    assert replacement is not first
    assert first.cleaned is True
    assert len(created) == 2
    tool._POOL_RUNNER_KEY = None
    tool._POOL_RUNNER = None


def test_execute_population_uses_isolated_worker_results_before_success(
    plan, tmp_path, monkeypatch
):
    root = tmp_path / "annual"
    tool.initialize_root(plan, root)

    def fake_resolve(required):
        unit = _progress(plan, root).selectable(limit=1)[0]
        context = annual_unit_context(
            plan, unit["unit_id"], unit_hint=_hint(unit)
        )
        return _archive_record(context, tmp_path / "archive")

    def fake_worker(unit, archive):
        unit_id = unit["unit_id"]
        context = annual_unit_context(plan, unit_id, unit_hint=_hint(unit))
        metadata = build_unit_metadata(
            plan,
            unit_id,
            demand_archive=_archive_record(context, tmp_path / "archive"),
            unit_hint=_hint(unit),
        )
        return AnnualWarmArtifactStore(root / "store").pack_artifact(
            plan_content_key=plan["content_key"],
            unit_id=unit_id,
            members=_member_files(
                tmp_path / "members" / unit_id, unit["checkpoint_s"],
                context["demand_build_spec"],
            ),
            metadata=metadata,
        )

    monkeypatch.setattr(tool, "_resolve_demand_archive", fake_resolve)
    source_seals = []
    monkeypatch.setattr(
        tool, "_verify_plan_source_seal",
        lambda checked_plan: source_seals.append(checked_plan["content_key"]),
    )
    summary = tool.execute_population(
        plan,
        plan_path=tmp_path / "plan.json",
        root=root,
        state_workers=3,
        max_units=3,
        reference_edge="edge_0",
        demand_build_key=None,
        worker_function=fake_worker,
    )
    assert summary["succeeded"] == 3
    assert summary["pending"] == 104_682
    assert summary["failed"] == 0
    assert source_seals == [plan["content_key"]]


def test_population_passes_the_previous_checkpoint_to_each_chain(plan, tmp_path,
                                                                  monkeypatch):
    root = tmp_path / "annual"
    tool.initialize_root(plan, root)
    observed = []

    def fake_resolve(required):
        unit = _progress(plan, root).selectable(limit=1)[0]
        context = annual_unit_context(plan, unit["unit_id"], unit_hint=_hint(unit))
        return _archive_record(context, tmp_path / "archive")

    def fake_worker(unit, archive):
        predecessor = unit.get("_predecessor_unit")
        observed.append((
            unit["unit_id"],
            None if predecessor is None else predecessor["unit_id"],
        ))
        context = annual_unit_context(
            plan, unit["unit_id"], unit_hint=_hint(unit))
        metadata = build_unit_metadata(
            plan, unit["unit_id"],
            demand_archive=_archive_record(context, tmp_path / "archive"),
            unit_hint=_hint(unit),
            predecessor_unit_id=(
                None if predecessor is None else predecessor["unit_id"]
            ),
        )
        return AnnualWarmArtifactStore(root / "store").pack_artifact(
            plan_content_key=plan["content_key"], unit_id=unit["unit_id"],
            members=_member_files(
                tmp_path / "members" / unit["unit_id"], unit["checkpoint_s"],
                context["demand_build_spec"],
            ),
            metadata=metadata,
        )

    monkeypatch.setattr(tool, "_resolve_demand_archive", fake_resolve)
    tool.execute_population(
        plan, plan_path=tmp_path / "plan.json", root=root,
        state_workers=3, max_units=4, reference_edge="edge_0",
        variant="q10",
        worker_function=fake_worker,
    )

    assert [predecessor for _unit, predecessor in observed] == [
        None, observed[0][0], observed[1][0], observed[2][0]]


def test_population_never_submits_a_unit_before_its_predecessor(
    plan, tmp_path, monkeypatch
):
    root = tmp_path / "annual"
    tool.initialize_root(plan, root)
    submitted = []

    def fake_resolve(required):
        unit = _progress(plan, root).selectable(limit=1)[0]
        context = annual_unit_context(plan, unit["unit_id"], unit_hint=_hint(unit))
        return _archive_record(context, tmp_path / "archive")

    def fake_worker(unit, archive):
        predecessor = unit.get("_predecessor_unit")
        if predecessor is not None:
            assert AnnualWarmArtifactStore(root / "store").artifact_exists(
                predecessor["unit_id"]
            )
        submitted.append(unit["unit_id"])
        context = annual_unit_context(
            plan, unit["unit_id"], unit_hint=_hint(unit)
        )
        metadata = build_unit_metadata(
            plan, unit["unit_id"],
            demand_archive=_archive_record(context, tmp_path / "archive"),
            unit_hint=_hint(unit),
            predecessor_unit_id=(
                None if predecessor is None else predecessor["unit_id"]
            ),
        )
        return AnnualWarmArtifactStore(root / "store").pack_artifact(
            plan_content_key=plan["content_key"], unit_id=unit["unit_id"],
            members=_member_files(
                tmp_path / "members" / unit["unit_id"], unit["checkpoint_s"],
                context["demand_build_spec"],
            ),
            metadata=metadata,
        )

    monkeypatch.setattr(tool, "_resolve_demand_archive", fake_resolve)
    tool.execute_population(
        plan, plan_path=tmp_path / "plan.json", root=root,
        state_workers=4, max_units=6, reference_edge="edge_0",
        worker_function=fake_worker,
    )

    assert len(submitted) == 6


def test_failed_worker_is_recorded_and_stops_the_batch(plan, tmp_path, monkeypatch):
    root = tmp_path / "annual"
    tool.initialize_root(plan, root)
    unit = _progress(plan, root).selectable(limit=1)[0]
    context = annual_unit_context(
        plan, unit["unit_id"], unit_hint=_hint(unit)
    )
    monkeypatch.setattr(
        tool, "_resolve_demand_archive",
        lambda required: _archive_record(context, tmp_path / "archive"),
    )
    def fail_worker(unit, archive):
        raise RuntimeError("synthetic failure")
    with pytest.raises(RuntimeError, match="failed batch"):
        tool.execute_population(
            plan,
            plan_path=tmp_path / "plan.json",
            root=root,
            state_workers=1,
            max_units=1,
            reference_edge="edge_0",
            demand_build_key=None,
            worker_function=fail_worker,
        )
    failed = _progress(plan, root).selectable(limit=1)[0]
    assert failed["state"] == "failed"
    assert "synthetic failure" in failed["error"]


def test_valid_orphan_artifact_is_committed_without_rerunning(
    plan, tmp_path, monkeypatch
):
    root = tmp_path / "annual"
    tool.initialize_root(plan, root)
    progress = _progress(plan, root)
    unit = progress.selectable(limit=1)[0]
    context = annual_unit_context(plan, unit["unit_id"], unit_hint=_hint(unit))
    metadata = build_unit_metadata(
        plan,
        unit["unit_id"],
        demand_archive=_archive_record(context, tmp_path / "archive"),
        unit_hint=_hint(unit),
    )
    artifact = AnnualWarmArtifactStore(root / "store").pack_artifact(
        plan_content_key=plan["content_key"],
        unit_id=unit["unit_id"],
        members=_member_files(
            tmp_path / "orphan-members", unit["checkpoint_s"],
            context["demand_build_spec"],
        ),
        metadata=metadata,
    )
    monkeypatch.setattr(
        tool,
        "_resolve_demand_archive",
        lambda required: pytest.fail("orphan recovery must not resolve demand"),
    )
    summary = tool.execute_population(
        plan,
        plan_path=tmp_path / "plan.json",
        root=root,
        state_workers=1,
        max_units=1,
        reference_edge="edge_0",
        worker_function=lambda unit, archive: pytest.fail(
            "orphan recovery must not launch a worker"
        ),
    )
    assert summary["succeeded"] == 1
    completed = progress.summary()
    assert completed["attempts"] == 1
    assert completed["succeeded"] == 1
    assert artifact["content_key"] == AnnualWarmArtifactStore(
        root / "store"
    ).load_artifact(unit["unit_id"])["content_key"]


def test_semantically_invalid_orphan_is_never_promoted(plan, tmp_path):
    root = tmp_path / "annual"
    tool.initialize_root(plan, root)
    progress = _progress(plan, root)
    unit = progress.selectable(limit=1)[0]
    context = annual_unit_context(plan, unit["unit_id"], unit_hint=_hint(unit))
    metadata = build_unit_metadata(
        plan, unit["unit_id"],
        demand_archive=_archive_record(context, tmp_path / "archive"),
        unit_hint=_hint(unit),
    )
    members = _member_files(
        tmp_path / "invalid-orphan", unit["checkpoint_s"],
        context["demand_build_spec"],
    )
    members["prefix_evidence.json"].write_text("{}")
    AnnualWarmArtifactStore(root / "store").pack_artifact(
        plan_content_key=plan["content_key"], unit_id=unit["unit_id"],
        members=members, metadata=metadata,
    )

    with pytest.raises(ValueError, match="prefix evidence is invalid"):
        tool.execute_population(
            plan, plan_path=tmp_path / "plan.json", root=root,
            state_workers=1, max_units=1, reference_edge="edge_0",
            worker_function=lambda unit, archive: pytest.fail(
                "invalid orphan must not launch a worker"
            ),
        )

    summary = progress.summary()
    assert summary["pending"] == 104_685
    assert summary["attempts"] == 0


def test_worker_wires_exact_archive_state_route_and_prefix_into_store(
    plan, tmp_path, monkeypatch
):
    from traffic_sim.simulation import monthly_demand, monthly_warm_state

    root = tmp_path / "annual"
    tool.initialize_root(plan, root)
    unit = _progress(plan, root).selectable(limit=1)[0]
    unit_id = unit["unit_id"]
    context = annual_unit_context(plan, unit_id, unit_hint=_hint(unit))
    archive = tmp_path / "archive"
    archive.mkdir()
    required = context["demand_build_spec"]
    (archive / "demand_build_spec.json").write_text(json.dumps(required))
    (archive / "demand_meta.json").write_text("{}")
    (archive / "manifest.json").write_text("{}")
    for filename in tool.VARIANT_FILENAMES.values():
        (archive / filename).write_text("<routes/>")
    record = _archive_record(context, archive)
    monkeypatch.setattr(
        monthly_demand, "validate_demand_archive", lambda path, spec: record
    )
    monkeypatch.setattr(
        monthly_warm_state,
        "parse_prefix_evidence",
        lambda payload, expected_warm_point_s=None: dict(payload),
    )

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            assert kwargs["expected_demand_spec"].to_dict() == required
            assert kwargs["seed_workers"] == 1

        def bootstrap_warm_state(
            self, *, variant, seed, warm_point_s, workspace, schedule_id
        ):
            state = workspace / "state.xml.gz"
            state.write_bytes(gzip.compress(
                (f'<snapshot type="meso" version="test" '
                 f'time="{warm_point_s:.3f}"/>').encode(), mtime=0
            ))
            return {
                "state_path": state,
                "prefix_evidence": {"warm_point_s": warm_point_s},
            }

    artifact = tool._worker_artifact(
        plan,
        root=root,
        unit_id=unit_id,
        archive=archive,
        reference_edge="edge_0",
        unit_hint=_hint(unit),
        runner_factory=FakeRunner,
        controller_factory=lambda: object(),
    )
    assert artifact["metadata"]["unit_id"] == unit_id
    assert artifact["metadata"]["variant"] == "q10"
    restored = AnnualWarmArtifactStore(root / "store").restore_artifact(
        unit_id, tmp_path / "restored"
    )
    assert restored["route.rou.xml"].read_text() == "<routes/>"
    assert json.loads(restored["prefix_evidence.json"].read_text()) == {
        "warm_point_s": context["unit"]["checkpoint_s"]
    }


def test_preflight_path_does_not_create_the_requested_root(plan, tmp_path, monkeypatch):
    root = tmp_path / "missing" / "annual"
    probe_free = 123 * 1024**3
    monkeypatch.setattr(tool.shutil, "disk_usage", lambda path: type(
        "Usage", (), {"free": probe_free})())
    assert tool._free_bytes_without_create(root) == probe_free
    assert not root.exists()
    assert root.parent.exists() is False


class TestRequiredFreeBytesIsProportional:
    """The gate must charge for selectable work, not for the whole calendar.

    A flat whole-year constant refused bounded pilots for archives they would
    never build, which is why a one-day run could not proceed on a disk that
    could hold a hundred of them.
    """

    def test_pending_units_dominate_and_scale_linearly(self, plan):
        one = tool.required_free_bytes(
            plan, pending_units=1, keep_demand_archives=False)
        many = tool.required_free_bytes(
            plan, pending_units=1001, keep_demand_archives=False)
        assert many - one == 1000 * tool.PER_UNIT_STORE_BYTES

    def test_a_bounded_run_asks_for_far_less_than_the_whole_plan(self, plan):
        total = len(tuple(tool.iter_work_units(plan)))
        whole = tool.required_free_bytes(
            plan, pending_units=total, keep_demand_archives=False)
        one_day = tool.required_free_bytes(
            plan, pending_units=96 * 3, keep_demand_archives=False)
        assert one_day < whole
        # The point of the change: a day-sized pilot must fit in a few GiB.
        assert one_day < 16 * 1024**3

    def test_retaining_archives_charges_for_every_planned_build(self, plan):
        pruned = tool.required_free_bytes(
            plan, pending_units=10, keep_demand_archives=False)
        retained = tool.required_free_bytes(
            plan, pending_units=10, keep_demand_archives=True)
        builds = len(plan["demand_build_specs"])
        assert retained - pruned == (
            builds - tool.CONCURRENT_DEMAND_ARCHIVES
        ) * tool.DEMAND_ARCHIVE_BYTES

    def test_reserve_and_overhead_survive_an_empty_selection(self, plan):
        assert tool.required_free_bytes(
            plan, pending_units=0, keep_demand_archives=False
        ) >= tool.RUNTIME_FREE_RESERVE_BYTES + tool.FIXED_OVERHEAD_BYTES

    @pytest.mark.parametrize("bad", [-1, True, 1.5, "3"])
    def test_invalid_pending_counts_fail_closed(self, plan, bad):
        with pytest.raises(ValueError):
            tool.required_free_bytes(
                plan, pending_units=bad, keep_demand_archives=False)


class TestTransientLaunchFailureClassifier:
    """Only a failure to START may retry; a wrong result must still abort.

    Production lost a whole run to one worker's SUMO not accepting its TraCI
    socket while its two siblings started normally. Retrying that is right;
    retrying a validation failure would let a wrong bank be built.
    """

    class _FatalTraCIError(Exception):
        pass

    def test_traci_connect_failure_is_transient(self):
        assert tool._is_transient_launch_failure(
            self._FatalTraCIError("Could not connect in 61 tries"))

    def test_refused_and_reset_sockets_are_transient(self):
        assert tool._is_transient_launch_failure(
            ConnectionRefusedError(61, "Connection refused"))
        assert tool._is_transient_launch_failure(ConnectionResetError())

    @pytest.mark.parametrize("error", [
        ValueError("annual warm artifact route.rou.xml differs from demand archive"),
        ValueError("annual warm predecessor does not match the exact plan chain"),
        ValueError("worker artifact differs from published manifest"),
        ValueError("annual warm prefix evidence is invalid"),
        RuntimeError("demand build completed without a valid archive"),
    ])
    def test_correctness_failures_are_never_transient(self, error):
        assert tool._is_transient_launch_failure(error) is False

    def test_a_traci_error_that_is_not_about_starting_is_not_transient(self):
        # A TraCI error raised mid-simulation is a result problem, not a
        # startup problem, and must keep aborting.
        assert tool._is_transient_launch_failure(
            self._FatalTraCIError("vehicle 'v1' is not known")) is False


class TestTransientRetryInsideExecutePopulation:
    def _harness(self, plan, tmp_path, monkeypatch, failures_before_success):
        root = tmp_path / "annual"
        tool.initialize_root(plan, root)
        archive_dir = tmp_path / "runs" / "demand-20270101-000000-aaaa-bbbb"
        archive_dir.mkdir(parents=True)
        monkeypatch.setattr(tool, "ROOT", tmp_path)
        monkeypatch.setattr(
            tool, "_verify_plan_source_seal", lambda checked_plan: None)

        def fake_resolve(required):
            unit = _progress(plan, root).selectable(limit=1)[0]
            context = annual_unit_context(
                plan, unit["unit_id"], unit_hint=_hint(unit))
            return _archive_record(context, archive_dir)

        monkeypatch.setattr(tool, "_resolve_demand_archive", fake_resolve)
        seen: dict[str, int] = {}

        def fake_worker(unit, archive):
            unit_id = unit["unit_id"]
            seen[unit_id] = seen.get(unit_id, 0) + 1
            if seen[unit_id] <= failures_before_success:
                raise TestTransientLaunchFailureClassifier._FatalTraCIError(
                    "Could not connect in 61 tries")
            context = annual_unit_context(
                plan, unit_id, unit_hint=_hint(unit))
            predecessor = unit.get("_predecessor_unit")
            metadata = build_unit_metadata(
                plan, unit_id,
                demand_archive=_archive_record(context, archive_dir),
                unit_hint=_hint(unit),
                predecessor_unit_id=(
                    predecessor["unit_id"] if predecessor else None),
            )
            return AnnualWarmArtifactStore(root / "store").pack_artifact(
                plan_content_key=plan["content_key"],
                unit_id=unit_id,
                members=_member_files(
                    tmp_path / "members" / unit_id, unit["checkpoint_s"],
                    context["demand_build_spec"],
                ),
                metadata=metadata,
            )

        return root, fake_worker, seen

    def test_a_transient_failure_is_retried_and_the_run_continues(
        self, plan, tmp_path, monkeypatch
    ):
        root, worker, seen = self._harness(plan, tmp_path, monkeypatch, 1)
        summary = tool.execute_population(
            plan, plan_path=tmp_path / "plan.json", root=root,
            state_workers=1, max_units=1, reference_edge="edge_0",
            worker_function=worker,
        )
        assert summary["succeeded"] == 1
        assert summary["failed"] == 0
        assert max(seen.values()) == 2  # failed once, then succeeded

    def test_retries_stop_at_the_limit_and_the_run_aborts(
        self, plan, tmp_path, monkeypatch
    ):
        root, worker, seen = self._harness(
            plan, tmp_path, monkeypatch, tool.TRANSIENT_UNIT_RETRY_LIMIT + 5)
        with pytest.raises(RuntimeError, match="stopped after failed batch"):
            tool.execute_population(
                plan, plan_path=tmp_path / "plan.json", root=root,
                state_workers=1, max_units=1, reference_edge="edge_0",
                worker_function=worker,
            )
        assert max(seen.values()) == tool.TRANSIENT_UNIT_RETRY_LIMIT

    def test_a_correctness_failure_aborts_without_any_retry(
        self, plan, tmp_path, monkeypatch
    ):
        root, _worker, seen = self._harness(plan, tmp_path, monkeypatch, 0)

        def bad_worker(unit, archive):
            seen[unit["unit_id"]] = seen.get(unit["unit_id"], 0) + 1
            raise ValueError("worker artifact differs from published manifest")

        with pytest.raises(RuntimeError, match="stopped after failed batch"):
            tool.execute_population(
                plan, plan_path=tmp_path / "plan.json", root=root,
                state_workers=1, max_units=1, reference_edge="edge_0",
                worker_function=bad_worker,
            )
        assert max(seen.values()) == 1  # no retry for a correctness failure


class TestPruningInsideExecutePopulation:
    """End to end: the archive must actually disappear when a build completes.

    The unit tests below pin `_prune_demand_archive` in isolation; this one
    proves the group loop calls it, and that `--keep-demand-archives` still
    leaves the archive in place.
    """

    @staticmethod
    def _smallest_build(plan):
        counts: dict[str, int] = {}
        for unit in tool.iter_work_units(plan):
            counts[unit["demand_build_key"]] = counts.get(
                unit["demand_build_key"], 0) + 1
        return min(counts, key=lambda key: counts[key])

    def _run(self, plan, tmp_path, monkeypatch, *, keep):
        root = tmp_path / "annual"
        tool.initialize_root(plan, root)
        build_key = self._smallest_build(plan)
        # The archive must live where a real one does, so the prune guard's
        # "inside runs/ and named demand-*" check is genuinely exercised.
        archive_dir = tmp_path / "runs" / "demand-20270101-000000-aaaa-bbbb"
        archive_dir.mkdir(parents=True)
        # Deliberately NOT a real member name: writing e.g. calibrated.rou.xml
        # here would shadow _archive_record's default and fail route binding.
        (archive_dir / "build.log").write_text("placeholder")
        monkeypatch.setattr(tool, "ROOT", tmp_path)

        def fake_resolve(required):
            unit = _progress(plan, root).selectable(
                limit=1, demand_build_key=build_key)[0]
            context = annual_unit_context(
                plan, unit["unit_id"], unit_hint=_hint(unit))
            return _archive_record(context, archive_dir)

        def fake_worker(unit, archive):
            unit_id = unit["unit_id"]
            context = annual_unit_context(
                plan, unit_id, unit_hint=_hint(unit))
            # A whole-build run walks past link 1, so every later unit must
            # carry its exact plan predecessor or metadata validation refuses it.
            predecessor = unit.get("_predecessor_unit")
            metadata = build_unit_metadata(
                plan, unit_id,
                demand_archive=_archive_record(context, archive_dir),
                unit_hint=_hint(unit),
                predecessor_unit_id=(
                    predecessor["unit_id"] if predecessor else None
                ),
            )
            return AnnualWarmArtifactStore(root / "store").pack_artifact(
                plan_content_key=plan["content_key"],
                unit_id=unit_id,
                members=_member_files(
                    tmp_path / "members" / unit_id, unit["checkpoint_s"],
                    context["demand_build_spec"],
                ),
                metadata=metadata,
            )

        monkeypatch.setattr(tool, "_resolve_demand_archive", fake_resolve)
        monkeypatch.setattr(
            tool, "_verify_plan_source_seal", lambda checked_plan: None)
        tool.execute_population(
            plan,
            plan_path=tmp_path / "plan.json",
            root=root,
            state_workers=3,
            max_units=None,
            reference_edge="edge_0",
            demand_build_key=build_key,
            keep_demand_archives=keep,
            worker_function=fake_worker,
        )
        return archive_dir, _progress(plan, root), build_key

    def test_completed_build_releases_its_archive(
        self, plan, tmp_path, monkeypatch
    ):
        archive, progress, build_key = self._run(
            plan, tmp_path, monkeypatch, keep=False)
        assert progress.selectable(demand_build_key=build_key) == ()
        assert not archive.exists()

    def test_keep_flag_retains_the_archive(self, plan, tmp_path, monkeypatch):
        archive, progress, build_key = self._run(
            plan, tmp_path, monkeypatch, keep=True)
        assert progress.selectable(demand_build_key=build_key) == ()
        assert archive.is_dir()


class TestDemandArchivePruning:
    """Archive retention was ~117 GiB, more than the artifact store itself.

    Pruning is only safe while no unit that could need the archive is
    unfinished, so these tests pin the refusal cases as hard as the happy path.
    """

    def _archive(self, tmp_path, name="demand-20270101-000000-aaaaaaaa-bbbb"):
        archive = tmp_path / "runs" / name
        archive.mkdir(parents=True)
        (archive / "calibrated.rou.xml").write_text("route")
        return archive

    def test_prunes_only_when_no_unit_for_the_build_is_selectable(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tool, "ROOT", tmp_path)
        archive = self._archive(tmp_path)

        class Done:
            def selectable(self, *, demand_build_key=None):
                return ()

        assert tool._prune_demand_archive(archive, "key", Done()) is True
        assert not archive.exists()

    def test_refuses_while_any_unit_remains(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tool, "ROOT", tmp_path)
        archive = self._archive(tmp_path)

        class Pending:
            def selectable(self, *, demand_build_key=None):
                return ({"unit_id": "annual-unit-x"},)

        assert tool._prune_demand_archive(archive, "key", Pending()) is False
        assert archive.is_dir()

    def test_refuses_paths_outside_the_runs_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tool, "ROOT", tmp_path)
        outside = tmp_path / "elsewhere" / "demand-20270101-000000-aaaa-bbbb"
        outside.mkdir(parents=True)

        class Done:
            def selectable(self, *, demand_build_key=None):
                return ()

        assert tool._prune_demand_archive(outside, "key", Done()) is False
        assert outside.is_dir()

    def test_refuses_a_directory_that_is_not_a_demand_archive(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tool, "ROOT", tmp_path)
        other = tmp_path / "runs" / "warm-state"
        other.mkdir(parents=True)

        class Done:
            def selectable(self, *, demand_build_key=None):
                return ()

        assert tool._prune_demand_archive(other, "key", Done()) is False
        assert other.is_dir()

    def test_refuses_a_symlinked_archive(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tool, "ROOT", tmp_path)
        real = self._archive(tmp_path, "demand-real-0000-aaaa-bbbb")
        link = tmp_path / "runs" / "demand-20270101-000000-aaaa-bbbb"
        link.symlink_to(real, target_is_directory=True)

        class Done:
            def selectable(self, *, demand_build_key=None):
                return ()

        assert tool._prune_demand_archive(link, "key", Done()) is False
        assert real.is_dir()


def test_initialized_root_and_runtime_disk_guard_fail_closed(
    plan, tmp_path, monkeypatch
):
    root = tmp_path / "annual"
    tool.initialize_root(plan, root)
    link = tmp_path / "annual-link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="not a regular directory"):
        tool._verify_initialized_root(plan, link)

    monkeypatch.setattr(
        tool.shutil,
        "disk_usage",
        lambda path: type(
            "Usage", (), {"free": tool.RUNTIME_FREE_RESERVE_BYTES - 1}
        )(),
    )
    with pytest.raises(RuntimeError, match="disk exhaustion"):
        tool._runtime_disk_guard(root)


def test_initialized_root_rejects_a_symlinked_plan(plan, tmp_path):
    root = tmp_path / "annual"
    tool.initialize_root(plan, root)
    external = tmp_path / "external-plan.json"
    external.write_bytes((root / tool.PLAN_FILENAME).read_bytes())
    (root / tool.PLAN_FILENAME).unlink()
    (root / tool.PLAN_FILENAME).symlink_to(external)
    with pytest.raises(ValueError, match="plan is not a regular file"):
        tool.initialize_root(plan, root)
