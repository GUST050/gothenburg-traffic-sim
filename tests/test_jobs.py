"""E4 durable job records (PLAN.md; audit P0-4).

The four in-memory state dicts vanish with the server process; every job
now also writes runs/jobs/<id>.json at start and terminal transition, and
startup reconciles stranded 'running' records against live pids.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import serve


class TestJobRecords:
    def _patched(self, monkeypatch, tmp_path):
        monkeypatch.setattr(serve, "JOBS_DIR", tmp_path / "jobs")
        serve.finish_active_job(serve._active_job["kind"] or "close")
        return serve

    def test_begin_writes_running_record_with_args(self, monkeypatch, tmp_path):
        s = self._patched(monkeypatch, tmp_path)
        s.begin_active_job("recalibrate", {"date": "2025-09-16"})
        try:
            job_id = s._active_job["job_id"]
            record = s.job_read(job_id)
            assert record["status"] == "running"
            assert record["kind"] == "recalibrate"
            assert record["args"] == {"date": "2025-09-16"}
            assert record["server_pid"] == os.getpid()
        finally:
            s.finish_active_job("recalibrate")

    def test_finish_snapshots_the_kinds_terminal_state(self, monkeypatch, tmp_path):
        s = self._patched(monkeypatch, tmp_path)
        s.begin_active_job("recalibrate", {})
        job_id = s._active_job["job_id"]
        with s._recal_lock:
            s._recal_state.clear()
            s._recal_state.update(status="done", date="2025-09-16",
                                  file="baseline.json")
        s.finish_active_job("recalibrate")
        # The durable terminal write is deliberately off-thread (it must not
        # sit between terminal-state visibility and _sim_lock release) —
        # wait for it like any other client would.
        deadline = time.time() + 2
        record = None
        while time.time() < deadline:
            record = s.job_read(job_id)
            if record and record.get("status") != "running":
                break
            time.sleep(0.02)
        assert record["status"] == "done"
        assert record["result"]["file"] == "baseline.json"
        assert "finished_at" in record

    def test_job_list_newest_first(self, monkeypatch, tmp_path):
        s = self._patched(monkeypatch, tmp_path)
        for i in range(3):
            s.job_record(f"j{i}", status="done", started_at=i)
        assert [r["id"] for r in s.job_list()] == ["j2", "j1", "j0"]

    def test_job_read_rejects_path_traversal(self, monkeypatch, tmp_path):
        s = self._patched(monkeypatch, tmp_path)
        assert s.job_read("../../etc/passwd") is None
        assert s.job_read("a/b") is None


class TestReconcileOnStartup:
    def test_running_record_with_dead_pgid_becomes_orphaned(self, monkeypatch, tmp_path):
        monkeypatch.setattr(serve, "JOBS_DIR", tmp_path / "jobs")
        # A pid from a long-dead process: fork one and reap it.
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        serve.job_record("dead-job", kind="close", status="running",
                         started_at=time.time(), pgid=pid)
        n = serve.reconcile_jobs_on_startup()
        assert n == 1
        assert serve.job_read("dead-job")["status"] == "orphaned"

    def test_running_record_with_live_pgid_marked_orphaned_running(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(serve, "JOBS_DIR", tmp_path / "jobs")
        # Our own process group is definitely alive.
        serve.job_record("live-job", kind="close", status="running",
                         started_at=time.time(), pgid=os.getpgrp())
        serve.reconcile_jobs_on_startup()
        assert serve.job_read("live-job")["status"] == "orphaned_running"

    def test_terminal_records_untouched(self, monkeypatch, tmp_path):
        monkeypatch.setattr(serve, "JOBS_DIR", tmp_path / "jobs")
        serve.job_record("done-job", kind="close", status="done",
                         started_at=time.time())
        assert serve.reconcile_jobs_on_startup() == 0
        assert serve.job_read("done-job")["status"] == "done"

    def test_running_record_without_pgid_is_orphaned(self, monkeypatch, tmp_path):
        # Server died between job_start and subprocess launch.
        monkeypatch.setattr(serve, "JOBS_DIR", tmp_path / "jobs")
        serve.job_record("early-death", kind="recalibrate", status="running",
                         started_at=time.time())
        serve.reconcile_jobs_on_startup()
        assert serve.job_read("early-death")["status"] == "orphaned"
