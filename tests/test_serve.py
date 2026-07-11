"""Integration tests for serve.py's HTTP API — the component with two
documented real production incidents (a BrokenPipeError from a blocking
recalibrate request, and a user's browser tab abandoning a 5-14 min
recalibration silently finishing without them) and, until this test file,
zero automated coverage.

Runs a real ThreadingHTTPServer on an ephemeral port and drives it with
real HTTP requests — run_in_new_session (serve.py's process-group-aware
subprocess.run wrapper) is monkeypatched so run_scenario.py/
build_sumo_demand.py are never actually invoked (they take minutes), which
lets these tests exercise the actual locking/threading/state-machine logic
in seconds."""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import serve


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def get_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def get_json_or_error(url, timeout=5):
    """urllib raises HTTPError for 4xx/5xx instead of returning them —
    normalise both paths to (status, body)."""
    try:
        return get_json(url, timeout)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def base_url(tmp_path, monkeypatch):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    monkeypatch.setattr(serve, "SCEN_DIR", scen_dir)
    monkeypatch.setattr(serve, "SUGGEST_OUT", tmp_path / "suggest_closure_web.json")

    def fake_known_edges():
        return frozenset({"a_b_0", "b_a_0"})
    fake_known_edges.cache_clear = lambda: None   # _run_recalibrate calls this on success
    monkeypatch.setattr(serve, "known_edges", fake_known_edges)

    serve._recal_state.clear()
    serve._recal_state.update(status="idle")
    serve._close_state.clear()
    serve._close_state.update(status="idle")
    serve._suggest_state.clear()
    serve._suggest_state.update(status="idle")
    if serve._sim_lock.locked():
        serve._sim_lock.release()

    httpd = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        # A background recalibration thread might still be a few bytecode
        # instructions from its own `finally: _sim_lock.release()` even
        # after /status has reported a terminal state (state is written
        # before the lock is released, not after — see serve.py). Acquire
        # (waiting briefly for that straggler) rather than blindly
        # releasing, so cleanup can never race a real release into a
        # "release unlocked lock" RuntimeError.
        if serve._sim_lock.acquire(timeout=2):
            serve._sim_lock.release()


def wait_until(predicate, timeout=5, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestPing:
    def test_ping_ok(self, base_url):
        status, body = get_json(f"{base_url}/api/ping")
        assert status == 200
        assert body == {"ok": True}


class TestServerStartup:
    def test_main_binds_to_loopback_by_default(self, monkeypatch):
        seen = {}

        class FakeServer:
            def __init__(self, address, handler):
                seen["address"] = address

            def serve_forever(self):
                raise KeyboardInterrupt

        monkeypatch.setattr(serve, "known_edges", lambda: frozenset())
        monkeypatch.setattr(serve, "ThreadingHTTPServer", FakeServer)
        serve.main()
        assert seen["address"] == ("127.0.0.1", serve.PORT)


class TestClose:
    """Made async 2026-07-10 (same reasoning and pattern as
    /api/recalibrate — found in review, same risk class: a blocking
    request up to 600s is fragile against a browser tab/proxy/dropped
    connection abandoning it well before that, even though a real closure
    usually finishes in ~30-90s). Validation stays synchronous (400s
    return immediately, no job started); the actual simulation is now
    started-then-polled via /api/close/status, mirroring
    TestRecalibrateAsyncLifecycle exactly."""

    def test_missing_edges_is_400(self, base_url):
        status, body = get_json_or_error(f"{base_url}/api/close")
        assert status == 400
        assert "error" in body

    def test_unknown_edge_is_400(self, base_url):
        status, body = get_json_or_error(f"{base_url}/api/close?edges=nonexistent_0")
        assert status == 400
        assert "nonexistent_0" in body["error"]

    def test_busy_lock_returns_409(self, base_url):
        serve._sim_lock.acquire()
        try:
            status, body = get_json_or_error(f"{base_url}/api/close?edges=a_b_0")
            assert status == 409
        finally:
            serve._sim_lock.release()

    def test_returns_202_immediately_not_after_the_job_finishes(self, base_url, monkeypatch):
        started = threading.Event()

        def fake_run(cmd, **kw):
            started.set()
            time.sleep(0.3)   # stands in for the real ~30-90s job
            index = {"scenarios": [{"closed_edges": ["a_b_0"], "name": "close_a_b_0"}]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0, stdout="Scenario 'close_a_b_0' (...)")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        t0 = time.time()
        status, body = get_json(f"{base_url}/api/close?edges=a_b_0")
        elapsed = time.time() - t0
        assert status == 202
        assert body["status"] == "started"
        assert elapsed < 0.2   # must not block on the background job
        assert started.wait(timeout=2)

    def test_successful_close_reports_the_matching_scenario_via_status(self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            assert "run_scenario.py" in cmd[1]
            index = {"scenarios": [{"closed_edges": ["a_b_0"], "name": "close_a_b_0",
                                    "file": "close_a_b_0.json"}]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0, stdout="Scenario 'close_a_b_0' (...)")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = get_json(f"{base_url}/api/close?edges=a_b_0")
        assert status == 202
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "done")
        _, final = get_json(f"{base_url}/api/close/status")
        assert final["name"] == "close_a_b_0"
        assert final["file"] == "close_a_b_0.json"

    def test_failed_simulation_reports_error_status_and_releases_the_lock(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=1, stderr="boom"))
        get_json(f"{base_url}/api/close?edges=a_b_0")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "error")
        assert not serve._sim_lock.locked()   # must not leak the lock on failure
        status, _ = get_json(f"{base_url}/api/close?edges=a_b_0")
        assert status == 202   # lock really is free, not just the status flipped

    def test_missing_manifest_after_successful_simulation_is_clear_error(self, base_url, monkeypatch):
        """A concurrent recalibration must not turn this into an unhandled
        FileNotFoundError if an external process removes the manifest."""
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(
                                returncode=0, stdout="Scenario 'close_a_b_0' (...)"))
        get_json(f"{base_url}/api/close?edges=a_b_0")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/close/status")
        assert "scenariomanifest saknas" in final["error"]
        assert not serve._sim_lock.locked()

    def test_unexpected_exception_reports_error_not_stuck_running(self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            raise FileNotFoundError("run_scenario.py vanished")
        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        get_json(f"{base_url}/api/close?edges=a_b_0")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "error")
        assert not serve._sim_lock.locked()


class TestCloseWindowed:
    """Added 2026-07-11 for C5's 'load this suggested window' action:
    /api/close?edges=...&begin=ISO&end=ISO runs a time-windowed --closure
    instead of a whole-run --close."""

    def test_begin_without_end_is_400(self, base_url):
        status, body = get_json_or_error(
            f"{base_url}/api/close?edges=a_b_0&begin=2025-09-16T08:00:00")
        assert status == 400
        assert "error" in body

    def test_malformed_datetime_is_400(self, base_url):
        status, _ = get_json_or_error(
            f"{base_url}/api/close?edges=a_b_0&begin=not-a-date&end=also-not")
        assert status == 400

    def test_windowed_request_shells_out_with_closure_json_not_close(
            self, base_url, monkeypatch):
        seen_cmd = {}

        def fake_run(cmd, **kw):
            seen_cmd["cmd"] = cmd
            index = {"scenarios": [{"closed_edges": ["a_b_0"], "name": "close_a_b_0_deadbeef",
                                    "file": "close_a_b_0_deadbeef.json"}]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0,
                                        stdout="Scenario 'close_a_b_0_deadbeef' (...)")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = get_json(
            f"{base_url}/api/close?edges=a_b_0&begin=2025-09-16T08:00:00&end=2025-09-16T10:00:00")
        assert status == 202
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "done")
        assert "--closure" in seen_cmd["cmd"]
        assert "--close" not in seen_cmd["cmd"]
        closure_json = json.loads(seen_cmd["cmd"][seen_cmd["cmd"].index("--closure") + 1])
        assert closure_json == {"edge_id": "a_b_0", "begin": "2025-09-16T08:00:00",
                                "end": "2025-09-16T10:00:00"}

    def test_matches_by_name_not_just_closed_edges_when_manifest_has_both(
            self, base_url, monkeypatch):
        """The real bug this guards: a stale WHOLE-RUN scenario and a fresh
        WINDOWED scenario on the SAME edges can coexist in the manifest with
        different names — matching by closed_edges alone (the old approach)
        could silently report the wrong one."""
        def fake_run(cmd, **kw):
            index = {"scenarios": [
                {"closed_edges": ["a_b_0"], "name": "close_a_b_0",
                 "file": "close_a_b_0.json"},   # stale whole-run scenario
                {"closed_edges": ["a_b_0"], "name": "close_a_b_0_deadbeef",
                 "file": "close_a_b_0_deadbeef.json"},   # the fresh windowed one
            ]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0,
                                        stdout="Scenario 'close_a_b_0_deadbeef' (...)")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        get_json(f"{base_url}/api/close?edges=a_b_0&begin=2025-09-16T08:00:00"
                f"&end=2025-09-16T10:00:00")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "done")
        _, final = get_json(f"{base_url}/api/close/status")
        assert final["name"] == "close_a_b_0_deadbeef"
        assert final["file"] == "close_a_b_0_deadbeef.json"

    def test_unparseable_stdout_is_a_clear_error_not_a_wrong_match(
            self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            index = {"scenarios": [{"closed_edges": ["a_b_0"], "name": "close_a_b_0"}]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0, stdout="no scenario line here")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        get_json(f"{base_url}/api/close?edges=a_b_0")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/close/status")
        assert "scenarionamnet" in final["error"]


class TestRunInNewSession:
    """run_in_new_session (IMPROVEMENT_REVIEW 13.8): a timeout must kill the
    whole process GROUP, not only the direct child — run_scenario.py and
    build_sumo_demand.py both spawn grandchildren (SUMO seeds, fork-pool
    workers) that subprocess.run()'s own timeout kill leaves orphaned and
    still writing into the shared sumo/ directory while _sim_lock is
    already released for the next job."""

    def test_normal_completion_returns_completed_process(self):
        res = serve.run_in_new_session(
            [sys.executable, "-c", "print('hello')"], cwd=".", timeout=30)
        assert res.returncode == 0
        assert res.stdout.strip() == "hello"

    def test_nonzero_exit_is_reported_not_raised(self):
        res = serve.run_in_new_session(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            cwd=".", timeout=30)
        assert res.returncode == 3

    def test_timeout_kills_the_grandchild_too(self, tmp_path):
        import os
        import subprocess
        # The child spawns a grandchild that sleeps forever, records its
        # PID, then sleeps forever itself. After the timeout BOTH must be
        # gone — with plain subprocess.run the grandchild would survive,
        # reparented to init/launchd (the exact orphan-SUMO failure mode).
        pid_file = tmp_path / "grandchild.pid"
        child_code = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(600)\n"
        )
        with pytest.raises(subprocess.TimeoutExpired):
            serve.run_in_new_session(
                [sys.executable, "-c", child_code], cwd=".", timeout=3)
        assert pid_file.exists(), "child never got far enough to spawn"
        gpid = int(pid_file.read_text())
        # Give the SIGKILL a moment to be delivered, then probe existence
        # (signal 0). NOTE: PID-reuse could theoretically make this probe
        # hit an unrelated process, but within 2s of the kill that is
        # vanishingly unlikely.
        for _ in range(20):
            try:
                os.kill(gpid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(gpid, 9)   # clean up the leak before failing the test
            pytest.fail(f"grandchild {gpid} survived the group kill")


class TestRecalibrateValidation:
    def test_bad_date_format_is_400(self, base_url):
        status, _ = get_json_or_error(f"{base_url}/api/recalibrate?date=2025-9-16")
        assert status == 400

    def test_bad_source_is_400(self, base_url):
        status, _ = get_json_or_error(
            f"{base_url}/api/recalibrate?date=2025-09-16&source=astrology")
        assert status == 400

    def test_default_source_is_historical(self, base_url, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            if "build_sumo_demand.py" in cmd[1]:
                seen["source"] = cmd[cmd.index("--source") + 1]
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        wait_until(lambda: seen.get("source") is not None)
        assert seen["source"] == "historical"

    def test_default_days_is_one(self, base_url, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            if "build_sumo_demand.py" in cmd[1]:
                seen["cmd"] = cmd
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        wait_until(lambda: seen.get("cmd") is not None)
        # days=1 keeps the original --date/--begin/--end call shape —
        # no behaviour change for existing single-day callers.
        assert "--date" in seen["cmd"] and "--start-date" not in seen["cmd"]

    def test_days_zero_is_400(self, base_url):
        status, _ = get_json_or_error(
            f"{base_url}/api/recalibrate?date=2025-09-16&days=0")
        assert status == 400

    def test_days_eight_is_400(self, base_url):
        status, _ = get_json_or_error(
            f"{base_url}/api/recalibrate?date=2025-09-16&days=8")
        assert status == 400

    def test_days_not_an_integer_is_400(self, base_url):
        status, _ = get_json_or_error(
            f"{base_url}/api/recalibrate?date=2025-09-16&days=abc")
        assert status == 400

    def test_multi_day_uses_start_date_and_days_flags(self, base_url, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            if "build_sumo_demand.py" in cmd[1]:
                seen["cmd"] = cmd
                seen["timeout"] = kw.get("timeout")
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16&days=3")
        wait_until(lambda: seen.get("cmd") is not None)
        cmd = seen["cmd"]
        assert "--date" not in cmd
        assert cmd[cmd.index("--start-date") + 1] == "2025-09-16"
        assert cmd[cmd.index("--days") + 1] == "3"
        assert seen["timeout"] == 1700 + 700 * 3   # scaled, not the flat 2400 s


class TestRecalibrateAsyncLifecycle:
    """The actual production-incident territory: a request must return
    immediately, and the job's true state must be visible via /status from
    any client, independent of the request that started it."""

    def test_returns_202_immediately_not_after_the_job_finishes(self, base_url, monkeypatch):
        started = threading.Event()

        def fake_run(cmd, **kw):
            started.set()
            time.sleep(0.3)   # stands in for the real 5-14 min job
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        t0 = time.time()
        status, body = get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        elapsed = time.time() - t0
        assert status == 202
        assert body["status"] == "started"
        assert elapsed < 0.2   # must not block on the background job
        assert started.wait(timeout=2)   # the job did actually get kicked off

    def test_status_transitions_running_then_done(self, base_url, monkeypatch):
        release = threading.Event()

        def fake_run(cmd, **kw):
            release.wait(timeout=2)
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")

        _, status_body = get_json(f"{base_url}/api/recalibrate/status")
        assert status_body["status"] == "running"
        assert "elapsed_s" in status_body

        release.set()
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "done")
        _, final = get_json(f"{base_url}/api/recalibrate/status")
        assert final["file"] == "baseline.json"

    def test_build_failure_reports_error_status(self, base_url, monkeypatch):
        monkeypatch.setattr(
            serve, "run_in_new_session",
            lambda cmd, **kw: FakeCompletedProcess(returncode=1, stderr="line1\nfatal: boom"))
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/recalibrate/status")
        assert "boom" in final["error"]

    def test_second_run_scenario_failure_also_reports_error(self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            if "build_sumo_demand.py" in cmd[1]:
                return FakeCompletedProcess(returncode=0)
            return FakeCompletedProcess(returncode=1, stderr="scenario boom")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "error")

    def test_busy_recalibrate_returns_409(self, base_url, monkeypatch):
        release = threading.Event()
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: (release.wait(timeout=2), FakeCompletedProcess())[1])
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        status, _ = get_json_or_error(f"{base_url}/api/recalibrate?date=2025-09-17")
        assert status == 409
        release.set()
        wait_until(lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] != "running")

    def test_lock_is_free_again_the_instant_status_reports_done(self, base_url, monkeypatch):
        """Regression contract for this session's race-condition fix:
        _recal_state is written BEFORE _sim_lock is released (previously
        the other way around), so a client polling status until it sees a
        terminal state must never observe the lock as still held — and a
        fresh recalibrate request right after must be accepted, not 409."""
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=0))
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "done")

        status, _ = get_json(f"{base_url}/api/recalibrate?date=2025-09-17")
        assert status == 202
        wait_until(lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] != "running")

    def test_unexpected_exception_reports_error_not_stuck_running(self, base_url, monkeypatch):
        """Regression: _run_recalibrate used to catch only TimeoutExpired —
        any other exception (a missing file, a permissions error, ...) left
        _recal_state stuck at "running" forever, with the frontend polling
        an ever-increasing fake elapsed time and no way to know the job
        actually died. Found 2026-07-07 in review."""
        def fake_run(cmd, **kw):
            raise FileNotFoundError("build_sumo_demand.py vanished")
        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "error")
        # and the lock must be released too, not just the status flipped
        status, _ = get_json(f"{base_url}/api/recalibrate?date=2025-09-17")
        assert status == 202

    def test_old_scenario_files_are_wiped_on_successful_recalibration(self, base_url, monkeypatch):
        stale = serve.SCEN_DIR / "stale_scenario.json"
        stale.write_text("{}")
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=0))
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "done")
        assert not stale.exists()


def _fake_suggest_result(**overrides) -> dict:
    """A structurally accurate (if minimal) suggest_closure_time.py result
    file, matching its real schema exactly (see suggest_closure_time.py's
    own `result = {...}` in main())."""
    base = {
        "method": "PLAN.md Phase C4", "edges": ["a_b_0"], "streets": ["Testgatan"],
        "duration_hours": 6.0, "slide_hours": 1.0, "total_duration_s": 86400,
        "n_candidate_windows": 19, "top_k": 2, "extra_bad": 1, "seeds": 3,
        "micro": False, "demand_signature": "abc123", "epoch_sim": "2025-09-16T00:00:00",
        "detour_availability": {"predecessors": ["p"], "successors": ["s"],
                                "reachable_pairs": 1, "total_pairs": 1, "score": 1.0},
        "baseline_metrics": {
            "total_time_loss_s": 10000.0, "trip_count": 500, "unfinished_trips": 0,
            "unfinished_waiting_trips": 0, "teleport_total": 0, "teleport_reasons": {},
            "loaded": 500, "inserted": 500, "running_at_end": 0, "waiting_at_end": 0,
            "truncated_unreachable": 0, "dropped_unreachable": 0,
            "max_queue_vehicles": 3, "closed_edge_throughput": None,
        },
        "baseline_per_seed_time_loss_s": [9900.0, 10000.0, 10100.0],
        "proxy_candidates": [],
        "simulated": [
            {
                "window": {"begin_s": 0, "end_s": 21600, "proxy_rank": 0},
                "metrics": {"total_time_loss_s": 10500.0, "trip_count": 500,
                           "unfinished_trips": 0, "unfinished_waiting_trips": 0,
                           "teleport_total": 0, "teleport_reasons": {}, "loaded": 500,
                           "inserted": 500, "running_at_end": 0, "waiting_at_end": 0,
                           "truncated_unreachable": 0, "dropped_unreachable": 0,
                           "max_queue_vehicles": 4, "closed_edge_throughput": None},
                "comparison": {"delta_time_loss_s": 500.0, "delta_unfinished_trips": 0,
                              "delta_teleports": 0, "delta_dropped_unreachable": 0,
                              "candidate_disqualified": False, "disqualification_reasons": []},
                "delta_time_loss_interval": {"median_s": 500.0, "min_s": 400.0,
                                             "max_s": 600.0, "n_seeds": 3},
                "truncated_vehicles": 0, "dropped_vehicles": 0,
            },
            {
                "window": {"begin_s": 3600, "end_s": 25200, "proxy_rank": 5},
                "metrics": {"total_time_loss_s": 20000.0, "trip_count": 500,
                           "unfinished_trips": 0, "unfinished_waiting_trips": 0,
                           "teleport_total": 1, "teleport_reasons": {"jam": 1}, "loaded": 500,
                           "inserted": 500, "running_at_end": 0, "waiting_at_end": 0,
                           "truncated_unreachable": 0, "dropped_unreachable": 2,
                           "max_queue_vehicles": 9, "closed_edge_throughput": None},
                "comparison": {"delta_time_loss_s": 10000.0, "delta_unfinished_trips": 0,
                              "delta_teleports": 1, "delta_dropped_unreachable": 2,
                              "candidate_disqualified": True,
                              "disqualification_reasons": ["teleports",
                                                           "dropped_unreachable_vehicles"]},
                "delta_time_loss_interval": {"median_s": 10000.0, "min_s": 9000.0,
                                             "max_s": 11000.0, "n_seeds": 3},
                "truncated_vehicles": 0, "dropped_vehicles": 2,
            },
        ],
        "validation": {
            "correlation": {"spearman_rho": 0.8, "p_value": 0.02, "n": 2,
                            "interpretation": "trust the ranking"},
            "simulated_best_in_proxy_top_k": True, "simulated_best_begin_s": 0,
        },
        "generated_at": "2026-07-11T12:00:00",
    }
    base.update(overrides)
    return base


class TestSummarizeSuggestion:
    """Pure function — no server needed. Verifies the honest-presentation
    rules PLAN.md's C5 spec asks for explicitly."""

    def test_shows_median_and_interval_not_a_single_number(self):
        summary = serve.summarize_suggestion(_fake_suggest_result())
        c = summary["candidates"][0]
        assert (c["delta_time_loss_median_s"], c["delta_time_loss_min_s"],
               c["delta_time_loss_max_s"]) == (500.0, 400.0, 600.0)

    def test_names_the_baseline_explicitly(self):
        summary = serve.summarize_suggestion(_fake_suggest_result())
        assert summary["baseline_total_time_loss_s"] == 10000.0
        assert summary["baseline_trip_count"] == 500

    def test_flags_which_candidates_were_inside_the_proxy_top_k(self):
        summary = serve.summarize_suggestion(_fake_suggest_result())
        by_begin = {c["begin_s"]: c for c in summary["candidates"]}
        assert by_begin[0]["in_proxy_top_k"] is True     # proxy_rank 0 < top_k 2
        assert by_begin[3600]["in_proxy_top_k"] is False  # proxy_rank 5 >= top_k 2

    def test_disqualified_candidate_carries_its_reasons(self):
        summary = serve.summarize_suggestion(_fake_suggest_result())
        by_begin = {c["begin_s"]: c for c in summary["candidates"]}
        assert by_begin[3600]["disqualified"] is True
        assert by_begin[3600]["disqualification_reasons"] == [
            "teleports", "dropped_unreachable_vehicles"]

    def test_states_n_simulated_of_n_total_windows(self):
        summary = serve.summarize_suggestion(_fake_suggest_result())
        assert summary["n_simulated"] == 2
        assert summary["n_candidate_windows"] == 19

    def test_candidates_sorted_by_proxy_rank(self):
        result = _fake_suggest_result()
        result["simulated"] = list(reversed(result["simulated"]))
        summary = serve.summarize_suggestion(result)
        ranks = [c["proxy_rank"] for c in summary["candidates"]]
        assert ranks == sorted(ranks)


class TestSuggestClosure:
    def test_missing_edges_is_400(self, base_url):
        status, body = get_json_or_error(
            f"{base_url}/api/suggest_closure?duration_hours=6")
        assert status == 400
        assert "error" in body

    def test_unknown_edge_is_400(self, base_url):
        status, body = get_json_or_error(
            f"{base_url}/api/suggest_closure?edges=nope_0&duration_hours=6")
        assert status == 400
        assert "nope_0" in body["error"]

    def test_missing_duration_hours_is_400(self, base_url):
        status, _ = get_json_or_error(f"{base_url}/api/suggest_closure?edges=a_b_0")
        assert status == 400

    def test_zero_duration_hours_is_400(self, base_url):
        status, _ = get_json_or_error(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=0")
        assert status == 400

    def test_top_k_out_of_range_is_400(self, base_url):
        status, _ = get_json_or_error(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6&top_k=999")
        assert status == 400

    def test_busy_lock_returns_409(self, base_url):
        serve._sim_lock.acquire()
        try:
            status, _ = get_json_or_error(
                f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
            assert status == 409
        finally:
            serve._sim_lock.release()

    def test_returns_202_immediately(self, base_url, monkeypatch):
        started = threading.Event()

        def fake_run(cmd, **kw):
            started.set()
            time.sleep(0.3)
            serve.SUGGEST_OUT.write_text(json.dumps(_fake_suggest_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        t0 = time.time()
        status, body = get_json(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        elapsed = time.time() - t0
        assert status == 202
        assert body["status"] == "started"
        assert elapsed < 0.2
        assert started.wait(timeout=2)

    def test_successful_run_reports_a_summary_via_status(self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            assert "suggest_closure_time.py" in cmd[1]
            assert "--edge" in cmd and "a_b_0" in cmd
            serve.SUGGEST_OUT.write_text(json.dumps(_fake_suggest_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = get_json(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        assert status == 202
        assert wait_until(
            lambda: get_json(f"{base_url}/api/suggest_closure/status")[1]["status"] == "done")
        _, final = get_json(f"{base_url}/api/suggest_closure/status")
        assert final["result"]["n_simulated"] == 2
        assert final["result"]["candidates"][0]["delta_time_loss_median_s"] == 500.0

    def test_failed_search_surfaces_the_tool_own_error_message(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(
                                returncode=1,
                                stderr="web/data/scenarios/baseline.json not found — "
                                      "run `python3 run_scenario.py` first"))
        get_json(f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/suggest_closure/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/suggest_closure/status")
        assert "baseline.json not found" in final["error"]

    def test_missing_output_file_after_success_is_a_clear_error(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=0))
        get_json(f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/suggest_closure/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/suggest_closure/status")
        assert "resultatfilen" in final["error"]

    def test_failure_releases_the_lock(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=1, stderr="boom"))
        get_json(f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/suggest_closure/status")[1]["status"] == "error")
        assert not serve._sim_lock.locked()
        status, _ = get_json(f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        assert status == 202

    def test_shares_the_sim_lock_with_close(self, base_url):
        """A suggest_closure search and a /api/close run are the same
        resource class (both real SUMO batches) — must not run concurrently."""
        serve._sim_lock.acquire()
        try:
            status, _ = get_json_or_error(f"{base_url}/api/close?edges=a_b_0")
            assert status == 409
        finally:
            serve._sim_lock.release()
