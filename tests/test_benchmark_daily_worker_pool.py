from argparse import Namespace
import json
import multiprocessing
import subprocess
import sys

import pytest

from tools import benchmark_daily_worker_pool as benchmark


def _record(evidence, *, pid=1):
    return {
        "result": {
            "schema": benchmark.RESULT_SCHEMA,
            "evidence": evidence,
        },
        "wall_s": 1.0,
        "worker_peak_rss_bytes": 10,
        "sumo_child_peak_rss_bytes": 20,
        "worker_pid": pid,
    }


class _Lock:
    def __init__(self, _owner):
        self.released = False

    def acquire(self, **_kwargs):
        return True

    def release(self):
        self.released = True


def _args(tmp_path):
    return Namespace(
        workspace=tmp_path / "workspace",
        release=tmp_path / "release.json",
        workspace_wait_s=0.0,
        units=2,
        variant="q50",
        warm_execution=False,
        prewarm_baselines=False,
        task_timeout_s=10.0,
        workers=1,
        recycle_after=25,
        trials=4,
    )


def _stub_execution(monkeypatch, *, fresh_evidence, pool_evidence,
                    fresh_wall, pool_wall):
    import traffic_sim.simulation.workspace as workspace

    monkeypatch.setattr(workspace, "WorkspaceLock", _Lock)
    frozen = {
        "archive_paths": ["archive"],
        "schedule_ids": ["a", "b"],
        "search_content_key": "key",
    }
    monkeypatch.setattr(
        benchmark,
        "build_requests",
        lambda **_kwargs: ([{"request": 1}, {"request": 2}], dict(frozen)),
    )
    monkeypatch.setattr(
        benchmark,
        "_fresh_arm",
        lambda *_args: (
            [_record(item, pid=index + 10)
             for index, item in enumerate(fresh_evidence)],
            fresh_wall,
        ),
    )
    monkeypatch.setattr(
        benchmark,
        "_pool_arm",
        lambda *_args: (
            [_record(item, pid=20) for item in pool_evidence],
            pool_wall,
        ),
    )


def test_pool_diagnostic_rejects_small_speedup(monkeypatch, tmp_path):
    evidence = [{"candidate_id": "a"}, {"candidate_id": "b"}]
    _stub_execution(
        monkeypatch,
        fresh_evidence=evidence,
        pool_evidence=evidence,
        fresh_wall=10.0,
        pool_wall=9.7,
    )

    report = benchmark.run_benchmark(_args(tmp_path))

    comparison = report["comparison"]
    assert comparison["exact_evidence_equal"] is True
    assert comparison["speedup"] == 1.030928
    assert comparison["speedup_range"] == [1.030928, 1.030928]
    assert comparison["decision"] == "reject_generic_pool"
    assert comparison["continue_to_counterbalanced_campaign"] is False
    assert comparison["production_adoption_authorized"] is False
    assert [trial["arm_order"] for trial in report["trials"]] == [
        ["fresh_interpreter", "reusable_spawn_pool"],
        ["reusable_spawn_pool", "fresh_interpreter"],
        ["fresh_interpreter", "reusable_spawn_pool"],
        ["reusable_spawn_pool", "fresh_interpreter"],
    ]


def test_pool_diagnostic_rejects_fast_but_different_evidence(
    monkeypatch, tmp_path
):
    _stub_execution(
        monkeypatch,
        fresh_evidence=[{"candidate_id": "a"}, {"candidate_id": "b"}],
        pool_evidence=[{"candidate_id": "a"}, {"candidate_id": "changed"}],
        fresh_wall=10.0,
        pool_wall=5.0,
    )

    report = benchmark.run_benchmark(_args(tmp_path))

    assert report["comparison"]["speedup"] == 2.0
    assert report["comparison"]["exact_evidence_equal"] is False
    assert report["comparison"]["continue_to_counterbalanced_campaign"] is False
    assert report["comparison"]["production_adoption_authorized"] is False


def test_pool_timeout_exits_context_for_member_cleanup(monkeypatch):
    state = {"exited": False, "timeout": None}

    class Pending:
        def get(self, *, timeout):
            state["timeout"] = timeout
            raise multiprocessing.TimeoutError

    class Pool:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, _exc, _traceback):
            state["exited"] = True
            assert exc_type is RuntimeError

        def map_async(self, *_args, **_kwargs):
            return Pending()

    class Context:
        def Pool(self, **_kwargs):  # pylint: disable=invalid-name
            return Pool()

    monkeypatch.setattr(
        benchmark.multiprocessing, "get_context", lambda method: Context()
    )

    with pytest.raises(RuntimeError, match="exceeded 50.0 s"):
        benchmark._pool_arm([{"request": 1}, {"request": 2}], 1, 25, 10.0)

    assert state == {"exited": True, "timeout": 50.0}


@pytest.mark.parametrize("flag", ["--units", "--workers", "--recycle-after"])
def test_positive_pool_controls_are_enforced(flag):
    with pytest.raises(SystemExit):
        benchmark.parse_args([flag, "0"])


@pytest.mark.parametrize("trials", [1, 3, 5])
def test_trials_must_be_even_and_at_least_four(trials):
    with pytest.raises(SystemExit):
        benchmark.parse_args(["--trials", str(trials)])


def test_help_is_process_free():
    script = (
        "import subprocess, sys; "
        "before=set(sys.modules); "
        "import tools.benchmark_daily_worker_pool as h; "
        "after=set(sys.modules); "
        "assert 'traci' not in after-before; "
        "assert 'libsumo' not in after-before; "
        "assert 'numpy' not in after-before; "
        "assert 'scipy' not in after-before; "
        "raise SystemExit(h.main(['--help']))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "reusable-Python-worker benchmark" in completed.stdout


def test_failed_cold_start_writes_bound_race_diagnostic(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "workspace"
    (workspace / "input").mkdir(parents=True)
    (workspace / "manifest.json").write_text("{}")
    release = tmp_path / "release.json"
    release.write_text("{}")
    output = tmp_path / "failure.json"
    monkeypatch.setattr(
        benchmark,
        "run_benchmark",
        lambda _args: (_ for _ in ()).throw(RuntimeError(
            "monthly baseline cache raced with another writer: cache.json"
        )),
    )

    with pytest.raises(RuntimeError, match="raced with another writer"):
        benchmark.main([
            "--workspace", str(workspace),
            "--release", str(release),
            "--units", "6",
            "--workers", "3",
            "--write", str(output),
        ])

    failure = json.loads(output.read_text())
    assert failure["status"] == "failed"
    assert failure["failure"]["baseline_cache_race"] is True
    assert failure["configuration"]["fresh_workers"] == 3
    assert len(failure["source_fingerprints"]["harness"]) == 64
