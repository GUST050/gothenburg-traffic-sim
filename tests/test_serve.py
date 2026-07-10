"""Integration tests for serve.py's HTTP API — the component with two
documented real production incidents (a BrokenPipeError from a blocking
recalibrate request, and a user's browser tab abandoning a 5-14 min
recalibration silently finishing without them) and, until this test file,
zero automated coverage.

Runs a real ThreadingHTTPServer on an ephemeral port and drives it with
real HTTP requests — subprocess.run is monkeypatched so run_scenario.py/
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

    def fake_known_edges():
        return frozenset({"a_b_0", "b_a_0"})
    fake_known_edges.cache_clear = lambda: None   # _run_recalibrate calls this on success
    monkeypatch.setattr(serve, "known_edges", fake_known_edges)

    serve._recal_state.clear()
    serve._recal_state.update(status="idle")
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

    def test_successful_close_returns_the_matching_scenario(self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            assert "run_scenario.py" in cmd[1]
            index = {"scenarios": [{"closed_edges": ["a_b_0"], "name": "close_a_b_0"}]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        status, body = get_json(f"{base_url}/api/close?edges=a_b_0")
        assert status == 200
        assert body["name"] == "close_a_b_0"

    def test_failed_simulation_is_500_and_releases_the_lock(self, base_url, monkeypatch):
        monkeypatch.setattr(serve.subprocess, "run",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=1, stderr="boom"))
        status, _ = get_json_or_error(f"{base_url}/api/close?edges=a_b_0")
        assert status == 500
        assert not serve._sim_lock.locked()   # must not leak the lock on failure

    def test_missing_manifest_after_successful_simulation_is_clear_500(self, base_url, monkeypatch):
        """A concurrent recalibration must not turn this into an unhandled
        FileNotFoundError if an external process removes the manifest."""
        monkeypatch.setattr(serve.subprocess, "run",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=0))
        status, body = get_json_or_error(f"{base_url}/api/close?edges=a_b_0")
        assert status == 500
        assert "scenariomanifest saknas" in body["error"]
        assert not serve._sim_lock.locked()


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

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        wait_until(lambda: seen.get("source") is not None)
        assert seen["source"] == "historical"

    def test_default_days_is_one(self, base_url, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            if "build_sumo_demand.py" in cmd[1]:
                seen["cmd"] = cmd
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
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

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
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

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
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

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
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
            serve.subprocess, "run",
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

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "error")

    def test_busy_recalibrate_returns_409(self, base_url, monkeypatch):
        release = threading.Event()
        monkeypatch.setattr(serve.subprocess, "run",
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
        monkeypatch.setattr(serve.subprocess, "run",
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
        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "error")
        # and the lock must be released too, not just the status flipped
        status, _ = get_json(f"{base_url}/api/recalibrate?date=2025-09-17")
        assert status == 202

    def test_old_scenario_files_are_wiped_on_successful_recalibration(self, base_url, monkeypatch):
        stale = serve.SCEN_DIR / "stale_scenario.json"
        stale.write_text("{}")
        monkeypatch.setattr(serve.subprocess, "run",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=0))
        get_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "done")
        assert not stale.exists()
