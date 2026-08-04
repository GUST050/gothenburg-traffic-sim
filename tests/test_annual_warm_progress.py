import sqlite3

import pytest

from traffic_sim.simulation.annual_warm_plan import (
    build_annual_warm_plan,
    default_2027_coverage_spec,
)
from traffic_sim.simulation.annual_warm_progress import AnnualWarmProgressStore


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
            "source": {"path": "source", "bytes": 1, "sha256": "d" * 64}
        },
        runtime_identity=RUNTIME,
    )


def test_sqlite_progress_initializes_resumes_and_transitions_exactly(plan, tmp_path):
    store = AnnualWarmProgressStore(tmp_path / "progress.sqlite3", plan)
    store.initialize()
    assert store.summary()["pending"] == 104_685
    unit = store.selectable(limit=1)[0]
    store.mark_running(unit["unit_id"])
    store.mark_failed(unit["unit_id"], "interrupted")
    assert store.selectable(limit=1)[0]["unit_id"] == unit["unit_id"]
    store.mark_running(unit["unit_id"])
    store.mark_succeeded(unit["unit_id"], "a" * 64)
    summary = store.summary()
    assert summary["succeeded"] == 1
    assert summary["pending"] == 104_684
    assert summary["attempts"] == 2
    store.verify()


def test_progress_filter_and_plan_binding_fail_closed(plan, tmp_path):
    store = AnnualWarmProgressStore(tmp_path / "progress.sqlite3", plan)
    store.initialize()
    unit = store.selectable(limit=1)[0]
    selected = store.selectable(
        limit=3, demand_build_key=unit["demand_build_key"]
    )
    assert selected
    assert {item["demand_build_key"] for item in selected} == {
        unit["demand_build_key"]
    }
    other = build_annual_warm_plan(
        default_2027_coverage_spec(),
        input_fingerprints={
            "source": {"path": "other", "bytes": 1, "sha256": "e" * 64}
        },
        runtime_identity=RUNTIME,
    )
    with pytest.raises(ValueError, match="another plan"):
        AnnualWarmProgressStore(store.path, other).verify()


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "UPDATE work_units SET request_id='tampered' WHERE ordinal=0",
            "identity differs",
        ),
        (
            "UPDATE work_units SET attempts=1 WHERE ordinal=0",
            "pending annual progress unit is inconsistent",
        ),
    ],
)
def test_progress_verification_recomputes_rows_and_lifecycle(
    plan, tmp_path, statement, message
):
    store = AnnualWarmProgressStore(tmp_path / "progress.sqlite3", plan)
    store.initialize()
    with sqlite3.connect(store.path) as db:
        db.execute(statement)
    with pytest.raises(ValueError, match=message):
        store.verify()


def test_existing_empty_progress_file_is_not_silently_reinitialized(plan, tmp_path):
    path = tmp_path / "progress.sqlite3"
    path.touch()
    with pytest.raises(ValueError, match="lacks bound metadata"):
        AnnualWarmProgressStore(path, plan).initialize()


def test_batch_transitions_are_atomic(plan, tmp_path):
    store = AnnualWarmProgressStore(tmp_path / "progress.sqlite3", plan)
    store.initialize()
    first, second = store.selectable(limit=2)
    running = store.mark_running_many([first["unit_id"], second["unit_id"]])
    assert set(running) == {first["unit_id"], second["unit_id"]}
    store.finish_batch(
        succeeded={first["unit_id"]: "a" * 64},
        failed={second["unit_id"]: "synthetic failure"},
    )
    summary = store.summary()
    assert summary["succeeded"] == 1
    assert summary["failed"] == 1

    third, fourth = store.selectable(limit=2)
    store.mark_running(third["unit_id"])
    with pytest.raises(ValueError, match="running annual unit"):
        store.finish_batch(
            succeeded={
                third["unit_id"]: "b" * 64,
                fourth["unit_id"]: "c" * 64,
            },
            failed={},
        )
    # The transaction rolls back the first update when the second is invalid.
    rows = {row["unit_id"]: row for row in store.selectable(limit=4)}
    assert rows[third["unit_id"]]["state"] == "running"
