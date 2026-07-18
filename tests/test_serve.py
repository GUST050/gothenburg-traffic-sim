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
import signal_optimize as so
import validation_report


@pytest.fixture(autouse=True)
def _validation_report_writes_to_tmp(monkeypatch, tmp_path):
    """serve's recalibrate success path refreshes the live validation report.

    Redirect its output for every test in this module: a test run must never
    rewrite the real tracked web/data/validation.json (found 2026-07-16 —
    each full-suite run silently churned the artifact's generated_at). The
    real write path stays exercised; only the destination is isolated.
    """
    monkeypatch.setattr(validation_report, "OUT_PATH",
                        tmp_path / "validation.json")


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _signal_scenario_spec(*, closure=False, simulation_mode="micro",
                          analysis_window=True):
    spec = {
        "scenario_id": "signal-api-spec",
        "demand_build_id": "demand-a",
        "network_build_id": "network-b",
        "start_time": "2025-09-16T00:00:00",
        "end_time": "2025-09-17T00:00:00",
        "simulation_mode": simulation_mode,
        "seed_set": [1000, 1001],
        "demand_variant_mapping": {"1000": "q50", "1001": "q10"},
    }
    if closure:
        spec["scenario_id"] = "signal-close-api-spec"
        spec["closures"] = [{
            "edge_id": "a_b_0",
            "start_time": "2025-09-16T07:00:00",
            "end_time": "2025-09-16T09:00:00",
        }]
    if analysis_window:
        spec["analysis_window"] = {
            "warmup_s": 0,
            "measure_start": "2025-09-16T07:00:00",
            "measure_end": "2025-09-16T09:00:00",
            "drain_s": 0,
        }
    return spec


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


def post_json(url, timeout=5, payload=None):
    """J (2026-07-14): mutating endpoints are POST-only now."""
    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if data is None else {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def post_json_or_error(url, timeout=5, payload=None):
    try:
        return post_json(url, timeout, payload)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def base_url(tmp_path, monkeypatch):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    monkeypatch.setattr(serve, "SCEN_DIR", scen_dir)
    staging_dir = tmp_path / "scenarios_staging"
    monkeypatch.setattr(serve, "SCEN_STAGING_DIR", staging_dir)
    monkeypatch.setattr(serve, "SPEC_DIR", tmp_path / "scenario_specs")
    monkeypatch.setattr(serve, "DEMAND_SPEC_DIR", tmp_path / "demand_specs")
    sumo_dir = tmp_path / "sumo"
    sumo_dir.mkdir()
    # A healthy demand_meta so E2's publish gate passes in success-path
    # lifecycle tests; failure-path tests never reach validation.
    (sumo_dir / "demand_meta.json").write_text(json.dumps({
        "pfe_fit": {"geh_pct": 100.0, "infeasible_intervals": 0,
                    "vehicles": 20000}}))
    monkeypatch.setattr(serve, "SUMO_DIR", sumo_dir)
    monkeypatch.setattr(serve, "SUGGEST_OUT", tmp_path / "suggest_closure_web.json")
    monkeypatch.setattr(serve, "OPTIMIZE_OUT", tmp_path / "signal_optimize_web.json")
    monkeypatch.setattr(serve, "OPTIMIZE_CLOSURE_OUT",
                        tmp_path / "signal_closure_combine_web.json")

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
    serve._optimize_state.clear()
    serve._optimize_state.update(status="idle")
    serve.finish_active_job("close")
    serve.finish_active_job("recalibrate")
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
        status, body = post_json_or_error(f"{base_url}/api/close")
        assert status == 400
        assert "error" in body

    def test_unknown_edge_is_400(self, base_url):
        status, body = post_json_or_error(f"{base_url}/api/close?edges=nonexistent_0")
        assert status == 400
        assert "nonexistent_0" in body["error"]

    def test_busy_lock_returns_409(self, base_url):
        serve._sim_lock.acquire()
        try:
            status, body = post_json_or_error(f"{base_url}/api/close?edges=a_b_0")
            assert status == 409
        finally:
            serve._sim_lock.release()

    def test_returns_202_immediately_not_after_the_job_finishes(self, base_url, monkeypatch):
        started = threading.Event()

        def fake_run(cmd, **kw):
            started.set()
            time.sleep(0.3)   # stands in for the real ~30-90s job
            index = {"scenarios": [{
                "closed_edges": ["a_b_0"], "name": "close_a_b_0",
                "closure_integrity": "verified_clean",
            }]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0, stdout="Scenario 'close_a_b_0' (...)")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        t0 = time.time()
        status, body = post_json(f"{base_url}/api/close?edges=a_b_0")
        elapsed = time.time() - t0
        assert status == 202
        assert body["status"] == "started"
        assert elapsed < 0.2   # must not block on the background job
        assert started.wait(timeout=2)

    def test_successful_close_reports_the_matching_scenario_via_status(self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            assert "run_scenario.py" in cmd[1]
            index = {"scenarios": [{"closed_edges": ["a_b_0"], "name": "close_a_b_0",
                                    "file": "close_a_b_0.json",
                                    "closure_integrity": "verified_clean"}]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0, stdout="Scenario 'close_a_b_0' (...)")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = post_json(f"{base_url}/api/close?edges=a_b_0")
        assert status == 202
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "done")
        _, final = get_json(f"{base_url}/api/close/status")
        assert final["name"] == "close_a_b_0"
        assert final["file"] == "close_a_b_0.json"

    def test_close_with_unverified_integrity_is_never_reported_done(
            self, base_url, monkeypatch):
        """A zero exit code is insufficient when the manifest lacks proof.

        This defensive branch also protects the API if a future runner
        regresses and writes an old-style manifest despite run_scenario's
        own pre-publication integrity gate.
        """
        def fake_run(cmd, **kw):
            index = {"scenarios": [{
                "closed_edges": ["a_b_0"], "name": "close_a_b_0",
                "file": "close_a_b_0.json", "closure_integrity": "not_measurable",
            }]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0,
                                        stdout="Scenario 'close_a_b_0' (...)")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        assert post_json(f"{base_url}/api/close?edges=a_b_0")[0] == 202
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/close/status")
        assert "verifierat nollflöde" in final["error"]

    def test_structured_scenario_spec_is_archived_and_passed_to_runner(
            self, base_url, monkeypatch):
        spec = {
            "scenario_id": "close-api-spec",
            "demand_build_id": "demand-a",
            "network_build_id": "network-b",
            "start_time": "2025-09-16T00:00:00",
            "end_time": "2025-09-17T00:00:00",
            "closures": [{
                "edge_id": "a_b_0",
                "start_time": "2025-09-16T08:00:00",
                "end_time": "2025-09-16T10:00:00",
            }],
        }
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            spec_path = Path(cmd[cmd.index("--scenario-spec") + 1])
            captured["spec"] = json.loads(spec_path.read_text())
            (serve.SCEN_DIR / "index.json").write_text(json.dumps({
                "scenarios": [{"closed_edges": ["a_b_0"],
                                "name": "close-api-spec",
                                "file": "close-api-spec.json",
                                "closure_integrity": "verified_clean"}],
            }))
            return FakeCompletedProcess(returncode=0,
                                        stdout="Scenario 'close-api-spec' (...)")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = post_json(base_url + "/api/close",
                                 payload={"scenario_spec": spec})
        assert status == 202
        assert body["scenario_id"] == "close-api-spec"
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "done")
        assert "--scenario-spec" in captured["cmd"]
        assert captured["spec"]["scenario_id"] == "close-api-spec"
        assert captured["spec"]["closures"][0]["edge_id"] == "a_b_0"

    def test_failed_simulation_reports_error_status_and_releases_the_lock(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=1, stderr="boom"))
        post_json(f"{base_url}/api/close?edges=a_b_0")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "error")
        assert not serve._sim_lock.locked()   # must not leak the lock on failure
        status, _ = post_json(f"{base_url}/api/close?edges=a_b_0")
        assert status == 202   # lock really is free, not just the status flipped

    def test_missing_manifest_after_successful_simulation_is_clear_error(self, base_url, monkeypatch):
        """A concurrent recalibration must not turn this into an unhandled
        FileNotFoundError if an external process removes the manifest."""
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(
                                returncode=0, stdout="Scenario 'close_a_b_0' (...)"))
        post_json(f"{base_url}/api/close?edges=a_b_0")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/close/status")
        assert "scenariomanifest saknas" in final["error"]
        assert not serve._sim_lock.locked()

    def test_unexpected_exception_reports_error_not_stuck_running(self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            raise FileNotFoundError("run_scenario.py vanished")
        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        post_json(f"{base_url}/api/close?edges=a_b_0")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "error")
        assert wait_until(lambda: not serve._sim_lock.locked())


class TestCancel:
    def test_cancel_close_stops_job_and_never_marks_it_done(self, base_url, monkeypatch):
        release = threading.Event()

        def fake_run(cmd, **kw):
            release.wait(timeout=2)
            index = {"scenarios": [{"closed_edges": ["a_b_0"],
                                    "name": "close_a_b_0", "file": "close_a_b_0.json",
                                    "closure_integrity": "verified_clean"}]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0, stdout="Scenario 'close_a_b_0' (...)" )

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        assert post_json(f"{base_url}/api/close?edges=a_b_0")[0] == 202
        assert post_json(f"{base_url}/api/cancel?kind=close")[0] == 202
        release.set()
        assert wait_until(
            lambda: get_json(f"{base_url}/api/close/status")[1]["status"] == "cancelled")
        _, status = get_json(f"{base_url}/api/close/status")
        assert "file" not in status
        assert not serve._sim_lock.locked()

    def test_cancel_recalibration_preserves_existing_scenarios(self, base_url, monkeypatch):
        release = threading.Event()
        old = serve.SCEN_DIR / "baseline.json"
        old.write_text('{"old": true}')

        def fake_run(cmd, **kw):
            release.wait(timeout=2)
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        assert post_json(f"{base_url}/api/recalibrate?date=2025-09-16")[0] == 202
        assert post_json(f"{base_url}/api/cancel?kind=recalibrate")[0] == 202
        release.set()
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "cancelled")
        assert old.exists()
        assert not serve._sim_lock.locked()

    def test_cancel_rejects_an_idle_or_unknown_job(self, base_url):
        assert post_json_or_error(f"{base_url}/api/cancel?kind=close")[0] == 409
        assert post_json_or_error(f"{base_url}/api/cancel?kind=unknown")[0] == 400


class TestCloseWindowed:
    """Added 2026-07-11 for C5's 'load this suggested window' action:
    /api/close?edges=...&begin=ISO&end=ISO runs a time-windowed --closure
    instead of a whole-run --close."""

    def test_begin_without_end_is_400(self, base_url):
        status, body = post_json_or_error(
            f"{base_url}/api/close?edges=a_b_0&begin=2025-09-16T08:00:00")
        assert status == 400
        assert "error" in body

    def test_malformed_datetime_is_400(self, base_url):
        status, _ = post_json_or_error(
            f"{base_url}/api/close?edges=a_b_0&begin=not-a-date&end=also-not")
        assert status == 400

    def test_windowed_request_shells_out_with_closure_json_not_close(
            self, base_url, monkeypatch):
        seen_cmd = {}

        def fake_run(cmd, **kw):
            seen_cmd["cmd"] = cmd
            index = {"scenarios": [{"closed_edges": ["a_b_0"], "name": "close_a_b_0_deadbeef",
                                    "file": "close_a_b_0_deadbeef.json",
                                    "closure_integrity": "verified_clean"}]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0,
                                        stdout="Scenario 'close_a_b_0_deadbeef' (...)")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = post_json(
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
                 "file": "close_a_b_0.json",
                 "closure_integrity": "verified_clean"},   # stale whole-run scenario
                {"closed_edges": ["a_b_0"], "name": "close_a_b_0_deadbeef",
                 "file": "close_a_b_0_deadbeef.json",
                 "closure_integrity": "verified_clean"},   # the fresh windowed one
            ]}
            (serve.SCEN_DIR / "index.json").write_text(json.dumps(index))
            return FakeCompletedProcess(returncode=0,
                                        stdout="Scenario 'close_a_b_0_deadbeef' (...)")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        post_json(f"{base_url}/api/close?edges=a_b_0&begin=2025-09-16T08:00:00"
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
        post_json(f"{base_url}/api/close?edges=a_b_0")
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
        def gone() -> bool:
            try:
                os.kill(gpid, 0)
            except ProcessLookupError:
                return True
            # A SIGKILLed orphan can linger as a zombie until init reaps it,
            # and kill(pid, 0) still succeeds on a zombie. Dead-but-unreaped
            # counts as killed here — it can never write into sumo/ again.
            try:
                with open(f"/proc/{gpid}/stat") as stat:
                    return stat.read().rsplit(")", 1)[1].split()[0] == "Z"
            except OSError:
                return False   # no /proc (macOS) — keep the plain probe

        for _ in range(20):
            if gone():
                break
            time.sleep(0.1)
        else:
            try:
                os.kill(gpid, 9)   # clean up the leak before failing the test
            except ProcessLookupError:
                return             # died between the last probe and now — gone
            pytest.fail(f"grandchild {gpid} survived the group kill")


class TestRecalibrateValidation:
    def test_structured_demand_spec_is_archived_and_forwarded(self, base_url, monkeypatch):
        seen = {}
        payload = {
            "demand_spec": {
                "start_date": "2027-09-16",
                "source": "forecast",
                "days": 1,
                "begin": "00:00",
                "end": "24:00",
                "structural_reference_date": "2025-09-16",
            }
        }

        def fake_run(cmd, **kw):
            if "build_sumo_demand.py" in cmd[1]:
                seen["cmd"] = cmd
            return FakeCompletedProcess(returncode=1, stderr="stop after capture")

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, response = post_json(base_url + "/api/recalibrate", payload=payload)
        assert status == 202
        assert response["demand_build_key"]
        wait_until(lambda: "cmd" in seen)
        cmd = seen["cmd"]
        assert "--demand-spec" in cmd
        spec_path = Path(cmd[cmd.index("--demand-spec") + 1])
        assert spec_path.exists()
        archived = json.loads(spec_path.read_text())
        assert archived["source"] == "forecast"
        assert archived["build_key"] == response["demand_build_key"]

    def test_structured_demand_spec_cannot_mix_query_parameters(self, base_url):
        status, _ = post_json_or_error(
            base_url + "/api/recalibrate?date=2025-09-16",
            payload={"demand_spec": {"start_date": "2025-09-16"}})
        assert status == 400

    def test_generic_recalibrate_rejects_closure_envelope_demand(self, base_url):
        status, _ = post_json_or_error(
            base_url + "/api/recalibrate",
            payload={"demand_spec": {
                "start_date": "2025-09-16",
                "days": 8,
                "purpose": "closure_envelope",
            }})
        assert status == 400

    def test_bad_date_format_is_400(self, base_url):
        status, _ = post_json_or_error(f"{base_url}/api/recalibrate?date=2025-9-16")
        assert status == 400

    def test_bad_source_is_400(self, base_url):
        status, _ = post_json_or_error(
            f"{base_url}/api/recalibrate?date=2025-09-16&source=astrology")
        assert status == 400

    def test_default_source_is_historical(self, base_url, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            if "build_sumo_demand.py" in cmd[1]:
                seen["source"] = cmd[cmd.index("--source") + 1]
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        post_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        wait_until(lambda: seen.get("source") is not None)
        assert seen["source"] == "historical"

    def test_default_days_is_one(self, base_url, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            if "build_sumo_demand.py" in cmd[1]:
                seen["cmd"] = cmd
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        post_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        wait_until(lambda: seen.get("cmd") is not None)
        # days=1 keeps the original --date/--begin/--end call shape —
        # no behaviour change for existing single-day callers.
        assert "--date" in seen["cmd"] and "--start-date" not in seen["cmd"]

    def test_days_zero_is_400(self, base_url):
        status, _ = post_json_or_error(
            f"{base_url}/api/recalibrate?date=2025-09-16&days=0")
        assert status == 400

    def test_days_eight_is_400(self, base_url):
        status, _ = post_json_or_error(
            f"{base_url}/api/recalibrate?date=2025-09-16&days=8")
        assert status == 400

    def test_days_not_an_integer_is_400(self, base_url):
        status, _ = post_json_or_error(
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
        post_json(f"{base_url}/api/recalibrate?date=2025-09-16&days=3")
        wait_until(lambda: seen.get("cmd") is not None)
        cmd = seen["cmd"]
        assert "--date" not in cmd
        assert cmd[cmd.index("--start-date") + 1] == "2025-09-16"
        assert cmd[cmd.index("--days") + 1] == "3"
        assert seen["timeout"] == 1700 + 700 * 3   # scaled, not the flat 2400 s


def _fake_staged_build(cmd, staging_dir):
    """Mimic run_scenario.py --out-dir: write a minimal valid staged set.

    E2 made publication conditional on a real staged artifact — a fake
    that returns success without producing one now correctly fails the
    publish gate, so success-path lifecycle fakes must model the output,
    not just the exit code."""
    if any("run_scenario.py" in str(part) for part in cmd):
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "baseline.json").write_text(
            json.dumps({"flows": {"a_b_0": [1]}}))
        (staging_dir / "baseline_traj.json").write_text(json.dumps({}))
        (staging_dir / "index.json").write_text(json.dumps(
            {"scenarios": [{"name": "baseline", "file": "baseline.json"}]}))


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
        status, body = post_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        elapsed = time.time() - t0
        assert status == 202
        assert body["status"] == "started"
        assert elapsed < 0.2   # must not block on the background job
        assert started.wait(timeout=2)   # the job did actually get kicked off

    def test_status_transitions_running_then_done(self, base_url, monkeypatch):
        release = threading.Event()

        def fake_run(cmd, **kw):
            release.wait(timeout=2)
            _fake_staged_build(cmd, serve.SCEN_STAGING_DIR)
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        post_json(f"{base_url}/api/recalibrate?date=2025-09-16")

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
        post_json(f"{base_url}/api/recalibrate?date=2025-09-16")
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
        post_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "error")

    def test_busy_recalibrate_returns_409(self, base_url, monkeypatch):
        release = threading.Event()
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: (release.wait(timeout=2), FakeCompletedProcess())[1])
        post_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        status, _ = post_json_or_error(f"{base_url}/api/recalibrate?date=2025-09-17")
        assert status == 409
        release.set()
        wait_until(lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] != "running")

    def test_lock_is_free_again_the_instant_status_reports_done(self, base_url, monkeypatch):
        """Regression contract for this session's race-condition fix:
        _recal_state is written BEFORE _sim_lock is released (previously
        the other way around), so a client polling status until it sees a
        terminal state must never observe the lock as still held — and a
        fresh recalibrate request right after must be accepted, not 409."""
        def fake_run(cmd, **kw):
            _fake_staged_build(cmd, serve.SCEN_STAGING_DIR)
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        post_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "done")

        status, _ = post_json(f"{base_url}/api/recalibrate?date=2025-09-17")
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
        post_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "error")
        # and the lock must be released too, not just the status flipped
        status, _ = post_json(f"{base_url}/api/recalibrate?date=2025-09-17")
        assert status == 202

    def test_old_scenario_files_are_wiped_on_successful_recalibration(self, base_url, monkeypatch):
        stale = serve.SCEN_DIR / "stale_scenario.json"
        stale.write_text("{}")

        def fake_run(cmd, **kw):
            _fake_staged_build(cmd, serve.SCEN_STAGING_DIR)
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        post_json(f"{base_url}/api/recalibrate?date=2025-09-16")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/recalibrate/status")[1]["status"] == "done")
        assert not stale.exists()


def _fake_suggest_result(**overrides) -> dict:
    """A structurally accurate (if minimal) suggest_closure_time.py result
    file, matching its real schema exactly (see suggest_closure_time.py's
    own `result = {...}` in main())."""
    base = {
        "method": "IMPROVEMENT_PLAN.md Phase C4", "edges": ["a_b_0"], "streets": ["Testgatan"],
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
    rules IMPROVEMENT_PLAN.md's C5 spec asks for explicitly."""

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
        status, body = post_json_or_error(
            f"{base_url}/api/suggest_closure?duration_hours=6")
        assert status == 400
        assert "error" in body

    def test_unknown_edge_is_400(self, base_url):
        status, body = post_json_or_error(
            f"{base_url}/api/suggest_closure?edges=nope_0&duration_hours=6")
        assert status == 400
        assert "nope_0" in body["error"]

    def test_missing_duration_hours_is_400(self, base_url):
        status, _ = post_json_or_error(f"{base_url}/api/suggest_closure?edges=a_b_0")
        assert status == 400

    def test_zero_duration_hours_is_400(self, base_url):
        status, _ = post_json_or_error(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=0")
        assert status == 400

    def test_nan_duration_hours_is_400_not_a_silent_pass_through(self, base_url):
        # float("nan") parses successfully but satisfies neither "> 0" nor
        # "<= 0" (every NaN comparison is False) -- a bare `<= 0` check
        # would let it through to the background job, where
        # round(nan * 3600) raises an unhandled ValueError instead of a
        # clean 400 here. Found in external review 2026-07-11.
        status, body = post_json_or_error(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=nan")
        assert status == 400
        assert "error" in body

    def test_infinite_duration_hours_is_400(self, base_url):
        status, _ = post_json_or_error(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=inf")
        assert status == 400

    def test_nan_slide_hours_is_400(self, base_url):
        status, _ = post_json_or_error(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6&slide_hours=nan")
        assert status == 400

    def test_fractional_duration_or_slide_is_rejected_before_starting_a_job(self, base_url):
        duration_status, _ = post_json_or_error(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=1.1")
        slide_status, _ = post_json_or_error(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=1&slide_hours=0.3")
        assert duration_status == 400
        assert slide_status == 400

    def test_top_k_out_of_range_is_400(self, base_url):
        status, _ = post_json_or_error(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6&top_k=999")
        assert status == 400

    def test_busy_lock_returns_409(self, base_url):
        serve._sim_lock.acquire()
        try:
            status, _ = post_json_or_error(
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
        status, body = post_json(
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
        status, body = post_json(
            f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        assert status == 202
        assert wait_until(
            lambda: get_json(f"{base_url}/api/suggest_closure/status")[1]["status"] == "done")
        _, final = get_json(f"{base_url}/api/suggest_closure/status")
        assert final["result"]["n_simulated"] == 2
        assert final["result"]["candidates"][0]["delta_time_loss_median_s"] == 500.0

    def test_structured_base_spec_is_passed_to_search_tool(self, base_url, monkeypatch):
        spec = {
            "scenario_id": "normal-api-spec",
            "demand_build_id": "demand-a",
            "network_build_id": "network-b",
            "start_time": "2025-09-16T00:00:00",
            "end_time": "2025-09-17T00:00:00",
        }
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            spec_path = Path(cmd[cmd.index("--scenario-spec") + 1])
            captured["spec"] = json.loads(spec_path.read_text())
            serve.SUGGEST_OUT.write_text(json.dumps(_fake_suggest_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = post_json(
            f"{base_url}/api/suggest_closure",
            payload={"scenario_spec": spec, "edges": ["a_b_0"],
                     "duration_hours": 6},
        )
        assert status == 202
        assert body["scenario_id"] == "normal-api-spec"
        assert wait_until(
            lambda: get_json(f"{base_url}/api/suggest_closure/status")[1]["status"] == "done")
        assert "--scenario-spec" in captured["cmd"]
        assert captured["spec"]["scenario_id"] == "normal-api-spec"

    def test_failed_search_surfaces_the_tool_own_error_message(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(
                                returncode=1,
                                stderr="web/data/scenarios/baseline.json not found — "
                                      "run `python3 run_scenario.py` first"))
        post_json(f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/suggest_closure/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/suggest_closure/status")
        assert "baseline.json not found" in final["error"]

    def test_missing_output_file_after_success_is_a_clear_error(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=0))
        post_json(f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/suggest_closure/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/suggest_closure/status")
        assert "resultatfilen" in final["error"]

    def test_failure_releases_the_lock(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=1, stderr="boom"))
        post_json(f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/suggest_closure/status")[1]["status"] == "error")
        assert not serve._sim_lock.locked()
        status, _ = post_json(f"{base_url}/api/suggest_closure?edges=a_b_0&duration_hours=6")
        assert status == 202

    def test_shares_the_sim_lock_with_close(self, base_url):
        """A suggest_closure search and a /api/close run are the same
        resource class (both real SUMO batches) — must not run concurrently."""
        serve._sim_lock.acquire()
        try:
            status, _ = post_json_or_error(f"{base_url}/api/close?edges=a_b_0")
            assert status == 409
        finally:
            serve._sim_lock.release()


def _fake_metrics(**overrides) -> dict:
    base = {
        "total_time_loss_s": 10000.0, "trip_count": 500, "unfinished_trips": 0,
        "unfinished_waiting_trips": 0, "teleport_total": 0, "teleport_reasons": {},
        "loaded": 500, "inserted": 500, "running_at_end": 0, "waiting_at_end": 0,
        "truncated_unreachable": 0, "dropped_unreachable": 0,
        "max_queue_vehicles": 3, "closed_edge_throughput": None,
    }
    base.update(overrides)
    return base


_FAKE_PLAN_DIFF = [
    {"tls_id": "J1", "cycle_before_s": 90.0, "cycle_after_s": 24.0,
     "cycle_delta_pct": -73.3, "offset_before_s": 0.0, "offset_after_s": 12.5,
     "n_phases_before": 4, "n_phases_after": 4, "max_split_change_pct": 30.0},
    {"tls_id": "J2", "cycle_before_s": 90.0, "cycle_after_s": 31.0,
     "cycle_delta_pct": -65.6, "offset_before_s": 0.0, "offset_after_s": 74.7,
     "n_phases_before": 6, "n_phases_after": 6, "max_split_change_pct": 17.1},
]


def _fake_signal_optimize_result(**overrides) -> dict:
    """A structurally accurate (if minimal) signal_optimize.py result file,
    matching its real schema (see signal_optimize.py's own `result = {...}`
    in main(), extended for IMPROVEMENT_PLAN.md D5's tls_plan_diff field)."""
    adapted_coord_metrics = _fake_metrics(total_time_loss_s=8000.0, teleport_total=1)
    base = {
        "method": "IMPROVEMENT_PLAN.md Phase D2", "window_start": "07:00", "window_end": "09:00",
        "begin_s": 25200, "end_s": 32400, "seeds": 3,
        "tls_provenance": "synthetic", "caveat": "synthetic baseline caveat",
        "recommendation_allowed": False,
        "net_fingerprint": "abc123", "net_fingerprints_by_condition": {},
        "demand_signature": "xyz789", "sumo_version": "SUMO 1.27.1", "command": [],
        "conditions": {
            "baseline": {"metrics": _fake_metrics(), "per_seed_time_loss_s": [10000.0],
                        "elapsed_s": 3.0},
            "adapted_coordinated": {"metrics": adapted_coord_metrics,
                                    "per_seed_time_loss_s": [8000.0], "elapsed_s": 3.0},
        },
        "comparisons_vs_baseline": {
            "adapted_coordinated": {
                "baseline": _fake_metrics(), "candidate": adapted_coord_metrics,
                "delta_time_loss_s": -2000.0, "delta_unfinished_trips": 0,
                "delta_teleports": 1, "delta_dropped_unreachable": 0,
                "candidate_disqualified": True, "disqualification_reasons": ["teleports"],
                "relative_time_loss_pct": -20.0,
                "paired": {
                    "pair_keys": ["1000:q50.rou.xml", "1001:q10.rou.xml"],
                    "n_pairs": 2, "time_loss_delta_s": [-1800.0, -2200.0],
                    "mean_time_loss_delta_s": -2000.0, "median_time_loss_delta_s": -2000.0,
                    "stddev_time_loss_delta_s": 282.8,
                    "min_time_loss_delta_s": -2200.0, "max_time_loss_delta_s": -1800.0,
                    "teleport_delta": [1, 1], "unfinished_trip_delta": [0, 0],
                },
            },
        },
        "tls_plan_diff": _FAKE_PLAN_DIFF,
        "elapsed_s": 15.0, "generated_at": "2026-07-11T12:00:00",
    }
    base.update(overrides)
    return base


def _fake_signal_closure_combine_result(**overrides) -> dict:
    """Matches signal_closure_combine.py's real `result = {...}` schema (D4)."""
    pass1_metrics = _fake_metrics(total_time_loss_s=300000.0, teleport_total=5)
    pass2_metrics = _fake_metrics(total_time_loss_s=260000.0, teleport_total=5)
    base = {
        "method": "IMPROVEMENT_PLAN.md Phase D4",
        "closed_edges": ["a_b_0"], "streets": ["Testgatan"],
        "window_start": "07:00", "window_end": "08:00",
        "begin_s": 25200, "end_s": 28800, "seeds": 1,
        "demand_signature": "xyz789", "net_fingerprint": "abc123",
        "sumo_version": "SUMO 1.27.1", "tls_provenance": "synthetic",
        "caveat": "synthetic baseline caveat",
        "truncated_vehicles": 9, "dropped_vehicles": 0, "n_extracted_routes": 1234,
        "pass1_baseline_signals": {"metrics": pass1_metrics,
                                   "per_seed_time_loss_s": [300000.0], "disqualified": True},
        "pass2_optimized_signals": {"metrics": pass2_metrics,
                                    "per_seed_time_loss_s": [260000.0], "disqualified": True},
        "comparison": {
            "baseline": pass1_metrics, "candidate": pass2_metrics,
            "delta_time_loss_s": -40000.0, "delta_unfinished_trips": -22,
            "delta_teleports": 0, "delta_dropped_unreachable": 0,
            "candidate_disqualified": True, "disqualification_reasons": ["teleports"],
            "relative_time_loss_pct": -13.3,
        },
        "paired_comparison": {
            "pair_keys": ["1000:q50.rou.xml"], "n_pairs": 1,
            "time_loss_delta_s": [-40000.0], "mean_time_loss_delta_s": -40000.0,
            "median_time_loss_delta_s": -40000.0, "stddev_time_loss_delta_s": 0.0,
            "min_time_loss_delta_s": -40000.0, "max_time_loss_delta_s": -40000.0,
            "teleport_delta": [0], "unfinished_trip_delta": [-22],
        },
        "route_stability": {"n_common_vehicles": 1227, "n_identical_routes": 1226,
                            "fraction_identical": 0.999},
        "tls_plan_diff": _FAKE_PLAN_DIFF,
        "generated_at": "2026-07-11T12:00:00",
    }
    base.update(overrides)
    return base


class TestSummarizeSignalOptimization:
    """Pure function — no server needed. Verifies the uniform-shape and
    honest-provenance rules IMPROVEMENT_PLAN.md's D5 spec asks for: one shape for the
    UI regardless of which script (D2 or D4) actually ran."""

    def test_no_closure_uses_baseline_vs_adapted_coordinated(self):
        summary = serve.summarize_signal_optimization(
            _fake_signal_optimize_result(), closure=False)
        assert summary["closure"] is False
        assert summary["before"]["total_time_loss_s"] == 10000.0
        assert summary["after"]["total_time_loss_s"] == 8000.0
        assert summary["delta_time_loss_s"] == -2000.0
        assert summary["closed_edges"] == []
        assert summary["route_stability"] is None

    def test_closure_uses_pass1_vs_pass2(self):
        summary = serve.summarize_signal_optimization(
            _fake_signal_closure_combine_result(), closure=True)
        assert summary["closure"] is True
        assert summary["before"]["total_time_loss_s"] == 300000.0
        assert summary["after"]["total_time_loss_s"] == 260000.0
        assert summary["closed_edges"] == ["a_b_0"]
        assert summary["route_stability"]["fraction_identical"] == 0.999

    def test_paired_comparison_surfaced_for_no_closure_case(self):
        # Found in review 2026-07-12: the seed/demand-variant-matched
        # paired comparison was computed by signal_optimize.py but never
        # reached the UI-facing summary at all.
        summary = serve.summarize_signal_optimization(
            _fake_signal_optimize_result(), closure=False)
        assert summary["paired_comparison"]["median_time_loss_delta_s"] == -2000.0
        assert summary["paired_comparison"]["n_pairs"] == 2
        assert summary["paired_sign_disagrees"] is False   # both agree: improvement

    def test_paired_comparison_surfaced_for_closure_case(self):
        summary = serve.summarize_signal_optimization(
            _fake_signal_closure_combine_result(), closure=True)
        assert summary["paired_comparison"]["median_time_loss_delta_s"] == -40000.0
        assert summary["paired_sign_disagrees"] is False

    def test_paired_sign_disagreement_is_flagged(self):
        # Aggregate says IMPROVED (-2000s) but the paired median says WORSE
        # (+500s) -- exactly the case this check exists to catch: an
        # aggregate mean dominated by one outlier seed contradicting the
        # seed-matched comparison.
        result = _fake_signal_optimize_result()
        result["comparisons_vs_baseline"]["adapted_coordinated"]["paired"][
            "median_time_loss_delta_s"] = 500.0
        summary = serve.summarize_signal_optimization(result, closure=False)
        assert summary["paired_sign_disagrees"] is True

    def test_missing_paired_data_degrades_to_none_not_a_crash(self):
        # Older result files (pre-dating the paired-comparison feature)
        # have no "paired"/"paired_comparison" key at all.
        result = _fake_signal_optimize_result()
        del result["comparisons_vs_baseline"]["adapted_coordinated"]["paired"]
        summary = serve.summarize_signal_optimization(result, closure=False)
        assert summary["paired_comparison"] is None
        assert summary["paired_sign_disagrees"] is None

    def test_synthetic_provenance_disallows_recommendation(self):
        summary = serve.summarize_signal_optimization(
            _fake_signal_optimize_result(), closure=False)
        assert summary["tls_provenance"] == "synthetic"
        assert summary["recommendation_allowed"] is False

    def test_non_synthetic_provenance_allows_recommendation(self):
        summary = serve.summarize_signal_optimization(
            _fake_signal_optimize_result(tls_provenance="city-configured"), closure=False)
        assert summary["tls_provenance"] == "city-configured"
        assert summary["recommendation_allowed"] is True

    def test_before_disqualification_is_derived_from_its_own_metrics(self):
        # D2's own schema has no explicit is_disqualified() on the baseline
        # condition (only comparisons_vs_baseline, computed against the
        # CANDIDATE) -- summarize_signal_optimization must derive it from
        # `before`'s own metrics, not silently report False regardless of
        # real teleports/drops in the baseline run itself.
        result = _fake_signal_optimize_result()
        result["conditions"]["baseline"]["metrics"] = _fake_metrics(teleport_total=2)
        summary = serve.summarize_signal_optimization(result, closure=False)
        assert summary["before_disqualified"] is True

    def test_median_cycle_delta_pct_summarizes_the_full_plan_diff(self):
        summary = serve.summarize_signal_optimization(
            _fake_signal_optimize_result(), closure=False)
        assert summary["n_junctions"] == 2
        assert summary["median_cycle_delta_pct"] == pytest.approx(-69.45, abs=0.1)
        assert len(summary["tls_plan_diff"]) == 2

    def test_median_cycle_delta_pct_ignores_none_entries(self):
        result = _fake_signal_optimize_result()
        result["tls_plan_diff"] = _FAKE_PLAN_DIFF + [
            {"tls_id": "J3", "cycle_before_s": 0.0, "cycle_after_s": 0.0,
             "cycle_delta_pct": None, "offset_before_s": 0.0, "offset_after_s": 0.0,
             "n_phases_before": 2, "n_phases_after": 2, "max_split_change_pct": None}]
        summary = serve.summarize_signal_optimization(result, closure=False)
        assert summary["n_junctions"] == 3   # every junction still counted...
        # ...but the median itself only ever averages real (non-None) deltas
        assert summary["median_cycle_delta_pct"] == pytest.approx(-69.45, abs=0.1)


class TestOptimizeSignals:
    def test_plain_signal_timeout_covers_every_registered_condition(self, base_url, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            seen["timeout"] = kw["timeout"]
            serve.OPTIMIZE_OUT.write_text(json.dumps(_fake_signal_optimize_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        post_json(f"{base_url}/api/optimize_signals")
        assert wait_until(lambda: "timeout" in seen)
        assert seen["timeout"] == 180 + serve.OPTIMIZE_SIGNAL_CONDITIONS * serve.OPTIMIZE_SEEDS * 90
        assert serve.OPTIMIZE_SIGNAL_CONDITIONS == len(so.SIGNAL_CONDITION_NAMES)

    def test_unknown_edge_is_400(self, base_url):
        status, body = post_json_or_error(f"{base_url}/api/optimize_signals?edges=nope_0")
        assert status == 400
        assert "nope_0" in body["error"]

    def test_structured_plain_signal_spec_is_archived_and_forwarded(
            self, base_url, monkeypatch):
        spec = _signal_scenario_spec()
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            spec_path = Path(cmd[cmd.index("--scenario-spec") + 1])
            captured["spec"] = json.loads(spec_path.read_text())
            serve.OPTIMIZE_OUT.write_text(json.dumps(_fake_signal_optimize_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = post_json(base_url + "/api/optimize_signals",
                                 payload={"scenario_spec": spec})
        assert status == 202
        assert body["scenario_id"] == "signal-api-spec"
        assert wait_until(
            lambda: get_json(f"{base_url}/api/optimize_signals/status")[1]["status"] == "done")
        assert "signal_optimize.py" in captured["cmd"][1]
        assert "--scenario-spec" in captured["cmd"]
        assert "--window-start" not in captured["cmd"]
        assert captured["spec"]["demand_variant_mapping"] == {
            "1000": "q50", "1001": "q10"}

    def test_structured_closure_signal_spec_dispatches_without_loose_edges(
            self, base_url, monkeypatch):
        spec = _signal_scenario_spec(closure=True)
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            spec_path = Path(cmd[cmd.index("--scenario-spec") + 1])
            captured["spec"] = json.loads(spec_path.read_text())
            serve.OPTIMIZE_CLOSURE_OUT.write_text(
                json.dumps(_fake_signal_closure_combine_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = post_json(base_url + "/api/optimize_signals",
                                 payload={"scenario_spec": spec})
        assert status == 202
        assert body["scenario_id"] == "signal-close-api-spec"
        assert wait_until(
            lambda: get_json(f"{base_url}/api/optimize_signals/status")[1]["status"] == "done")
        assert "signal_closure_combine.py" in captured["cmd"][1]
        assert "--scenario-spec" in captured["cmd"]
        assert "--close" not in captured["cmd"]
        assert captured["spec"]["closures"][0]["edge_id"] == "a_b_0"

    @pytest.mark.parametrize("kwargs,needle", [
        ({"analysis_window": False}, "analysis_window"),
        ({"simulation_mode": "meso"}, "simulation_mode=micro"),
    ])
    def test_structured_signal_spec_rejects_missing_or_incompatible_contract(
            self, base_url, kwargs, needle):
        status, body = post_json_or_error(
            base_url + "/api/optimize_signals",
            payload={"scenario_spec": _signal_scenario_spec(**kwargs)})
        assert status == 400
        assert needle in body["error"]

    def test_no_edges_dispatches_to_signal_optimize(self, base_url, monkeypatch):
        # edges omitted entirely means "optimize against the currently
        # loaded scenario's own closed edges", which is empty for a plain
        # baseline scenario -- must NOT be rejected as a missing parameter.
        started = threading.Event()

        def fake_run(cmd, **kw):
            started.set()
            assert "signal_optimize.py" in cmd[1]
            serve.OPTIMIZE_OUT.write_text(json.dumps(_fake_signal_optimize_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = post_json(f"{base_url}/api/optimize_signals")
        assert status == 202
        assert started.wait(timeout=2)

    def test_edges_present_dispatches_to_signal_closure_combine(self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            assert "signal_closure_combine.py" in cmd[1]
            assert "--close" in cmd and "a_b_0" in cmd
            serve.OPTIMIZE_CLOSURE_OUT.write_text(
                json.dumps(_fake_signal_closure_combine_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        status, body = post_json(f"{base_url}/api/optimize_signals?edges=a_b_0")
        assert status == 202
        assert wait_until(
            lambda: get_json(f"{base_url}/api/optimize_signals/status")[1]["status"] == "done")

    def test_busy_lock_returns_409(self, base_url):
        serve._sim_lock.acquire()
        try:
            status, _ = post_json_or_error(f"{base_url}/api/optimize_signals")
            assert status == 409
        finally:
            serve._sim_lock.release()

    def test_returns_202_immediately(self, base_url, monkeypatch):
        started = threading.Event()

        def fake_run(cmd, **kw):
            started.set()
            time.sleep(0.3)
            serve.OPTIMIZE_OUT.write_text(json.dumps(_fake_signal_optimize_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        t0 = time.time()
        status, body = post_json(f"{base_url}/api/optimize_signals")
        elapsed = time.time() - t0
        assert status == 202
        assert body["status"] == "started"
        assert elapsed < 0.2
        assert started.wait(timeout=2)

    def test_successful_no_closure_run_reports_a_summary_via_status(self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            serve.OPTIMIZE_OUT.write_text(json.dumps(_fake_signal_optimize_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        post_json(f"{base_url}/api/optimize_signals")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/optimize_signals/status")[1]["status"] == "done")
        _, final = get_json(f"{base_url}/api/optimize_signals/status")
        assert final["result"]["closure"] is False
        assert final["result"]["delta_time_loss_s"] == -2000.0

    def test_successful_closure_run_reports_a_summary_via_status(self, base_url, monkeypatch):
        def fake_run(cmd, **kw):
            serve.OPTIMIZE_CLOSURE_OUT.write_text(
                json.dumps(_fake_signal_closure_combine_result()))
            return FakeCompletedProcess(returncode=0)

        monkeypatch.setattr(serve, "run_in_new_session", fake_run)
        post_json(f"{base_url}/api/optimize_signals?edges=a_b_0")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/optimize_signals/status")[1]["status"] == "done")
        _, final = get_json(f"{base_url}/api/optimize_signals/status")
        assert final["result"]["closure"] is True
        assert final["result"]["closed_edges"] == ["a_b_0"]

    def test_failed_run_surfaces_the_tool_own_error_message(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(
                                returncode=1, stderr="demand_meta.json not found"))
        post_json(f"{base_url}/api/optimize_signals")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/optimize_signals/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/optimize_signals/status")
        assert "demand_meta.json not found" in final["error"]

    def test_missing_output_file_after_success_is_a_clear_error(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=0))
        post_json(f"{base_url}/api/optimize_signals")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/optimize_signals/status")[1]["status"] == "error")
        _, final = get_json(f"{base_url}/api/optimize_signals/status")
        assert "resultatfilen" in final["error"]

    def test_failure_releases_the_lock(self, base_url, monkeypatch):
        monkeypatch.setattr(serve, "run_in_new_session",
                            lambda cmd, **kw: FakeCompletedProcess(returncode=1, stderr="boom"))
        post_json(f"{base_url}/api/optimize_signals")
        assert wait_until(
            lambda: get_json(f"{base_url}/api/optimize_signals/status")[1]["status"] == "error")
        assert not serve._sim_lock.locked()
        status, _ = post_json(f"{base_url}/api/optimize_signals")
        assert status == 202

    def test_shares_the_sim_lock_with_close(self, base_url):
        """An optimize_signals run and a /api/close run are the same
        resource class (both real SUMO batches) — must not run concurrently."""
        serve._sim_lock.acquire()
        try:
            status, _ = post_json_or_error(f"{base_url}/api/close?edges=a_b_0")
            assert status == 409
        finally:
            serve._sim_lock.release()


class TestSecurityHardening:
    """J (IMPROVEMENT_PLAN.md; audit P0-3/P1-12): GET must never mutate; cross-origin
    POSTs are refused; every response carries the CSP."""

    def test_get_on_mutating_endpoint_is_405(self, base_url):
        for ep in ("close?edges=a_b_0", "recalibrate?date=2025-09-16",
                   "cancel?kind=close", "suggest_closure?edges=a_b_0",
                   "optimize_signals?edges="):
            status, body = get_json_or_error(f"{base_url}/api/{ep}")
            assert status == 405, ep
            assert "POST" in body["error"]

    def test_cross_origin_post_is_403(self, base_url):
        req = urllib.request.Request(
            f"{base_url}/api/cancel?kind=close", method="POST",
            headers={"Origin": "https://evil.example"})
        try:
            urllib.request.urlopen(req, timeout=5)
            status = 200
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 403

    def test_loopback_origin_post_is_accepted(self, base_url):
        req = urllib.request.Request(
            f"{base_url}/api/cancel?kind=close", method="POST",
            headers={"Origin": "http://localhost:8000"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status != 403   # cancel with no active job is 409/200, never CSRF

    def test_csp_header_present_on_api_and_static(self, base_url):
        for path in ("/api/ping", "/index.html"):
            with urllib.request.urlopen(f"{base_url}{path}", timeout=5) as r:
                csp = r.headers.get("Content-Security-Policy", "")
            assert "script-src 'self' https://unpkg.com" in csp, path
            assert "connect-src 'self' https://unpkg.com" in csp, path
            assert "frame-ancestors 'none'" in csp, path

    def test_status_endpoints_stay_get(self, base_url):
        status, _ = get_json(f"{base_url}/api/recalibrate/status")
        assert status == 200
