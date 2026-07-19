#!/usr/bin/env python3
"""Browser recovery test for the monthly closure search (Phase 4 step 8).

The one bug class this project has shipped TWICE (the 2026-07-06
recalibration incident and its earlier curl twin) is a browser abandoning
a long server job and the UI then lying about server state. The monthly
search is the longest-running job in the product, so its recovery path
must be proven in a real browser, not inferred from pytest:

1. a search started from one page keeps running when that page reloads
   mid-job (the tab that started it is gone — exactly the incident shape);
2. the fresh page's on-load discovery re-attaches: the workspace shows the
   live phase from the search's own persisted manifest and the run button
   is locked;
3. when the job finishes, the re-attached page renders the result panel
   with the honest claim wording.

Run:  python3 tools/browser_recovery_test.py
Requires Google Chrome and `pip install websocket-client`. The SUMO CLI is
faked (a real monthly search runs for hours); everything else — serve.py's
HTTP handler, threading, job records, status endpoints, and the real web
app JS — is exercised for real. Exit code 0 = every assertion passed.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import serve  # noqa: E402

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
)
CDP_PORT = 9223
# Long enough that page load (~3.5s), reload (~3.5s) and one 4s poll tick
# all land INSIDE the running window — a shorter fake job finishes before
# the mid-run assertions and the test races its own fixture.
FAKE_JOB_SECONDS = 35
EDGE_ID = None  # resolved from the real network at runtime


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def fake_full_result(search_id: str) -> dict:
    """A structurally faithful monthly result (bounded-exhaustive claims)."""
    schedule = {
        "schedule_id": "closure-recovery-1",
        "search_content_key": "irrelevant-here",
        "first_work_date": "2027-07-15",
        "day_count": 1,
        "daily_start": "06:00",
        "daily_end": "10:00",
        "intervals": [{
            "start_time": "2027-07-15T06:00:00",
            "end_time": "2027-07-15T10:00:00",
        }],
    }
    return {
        "schema_version": 1,
        "kind": "monthly_closure_search_result",
        "search_id": search_id,
        "status": "unique_winner",
        "winner_id": "closure-recovery-1",
        "tie_ids": [],
        "selected_schedules": [schedule],
        "shortlisted_schedules": [schedule],
        "screening": {
            "candidate_count": 3,
            "scoreable_candidate_count": 0,
            "shortlist_count": 3,
            "proxy_version": "bounded_exhaustive_sumo_v1",
        },
        "policy": {"policy_id": "monthly-closure-policy-v1",
                   "benchmark_id": "golden-monthly-search-2025-09-16-v6"},
        "pilot_selection": {"status": "ready"},
        "robust_decision": {
            "status": "unique_winner",
            "candidates": [{
                "candidate_id": "closure-recovery-1",
                "eligible": True,
                "hard_failures": [],
                "robust_point_s": 321.79,
                "robust_upper_95_s": 852.2,
                "worst_variant": "q90",
            }],
        },
        "simulation_backend": {
            "kind": "multi_envelope_monthly_sumo_backend",
            "simulation_mode": "meso",
            "sumo_version": "SUMO 1.27.1 (faked for browser test)",
            "demand_release_id": "browser-recovery-release",
            "source_digest": "fake", "simulation_source_digest": "fake",
        },
        "claim_boundary": {
            "best_result_available": True,
            "best_result_scope": "sumo_verified_bounded_exhaustive",
            "global_best_claim_allowed": False,
            "ui_exposure_allowed": True,
            "reason": "bounded exhaustive screening (browser recovery test)",
        },
    }


def fake_run_in_new_session(cmd, *, cwd, timeout):
    """Stand-in for the hours-long monthly CLI: real progress persistence,
    faked SUMO. Writes the workspace manifest phases the status endpoint
    reads, then the final result artifact serve.py summarizes."""
    spec_path = Path(cmd[cmd.index("--spec") + 1])
    search_id = json.loads(spec_path.read_text())["search_id"]
    workspace = serve.MONTHLY_SEARCH_ROOT / search_id
    (workspace / "artifacts").mkdir(parents=True, exist_ok=True)
    phases = ["enumerate", "screen", "prepare_backend", "pilot",
              "finalists", "decide", "publish"]
    step = FAKE_JOB_SECONDS / len(phases)
    for index, phase in enumerate(phases):
        (workspace / "manifest.json").write_text(json.dumps({
            "status": "running",
            "progress": {"phase": phase, "completed": index,
                         "total": len(phases),
                         "updated_at": "2026-07-19T00:00:00Z"},
        }))
        time.sleep(step)
    (workspace / "artifacts" / "result.json").write_text(
        json.dumps(fake_full_result(search_id)))
    (workspace / "manifest.json").write_text(json.dumps({
        "status": "succeeded", "progress": {"phase": "publish",
                                            "completed": len(phases),
                                            "total": len(phases),
                                            "updated_at": "now"}}))

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""
    return Completed()


class Browser:
    def __init__(self, chrome: str, profile: Path):
        self.proc = subprocess.Popen(
            [chrome, "--headless", f"--remote-debugging-port={CDP_PORT}",
             "--remote-allow-origins=*", f"--user-data-dir={profile}",
             "--window-size=1500,950", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import websocket  # deferred: give a clear error if missing
        self.websocket = websocket
        for _ in range(50):
            try:
                urllib.request.urlopen(
                    f"http://localhost:{CDP_PORT}/json/version", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            fail("Chrome CDP endpoint never came up")
        self.ws = None
        self.msg_id = 0

    def open_tab(self, url: str) -> None:
        request = urllib.request.Request(
            f"http://localhost:{CDP_PORT}/json/new?url=about:blank",
            method="PUT")
        tab = json.loads(urllib.request.urlopen(request).read())
        self.ws = self.websocket.create_connection(tab["webSocketDebuggerUrl"])
        self.send("Runtime.enable")
        self.send("Page.enable")
        self.navigate(url)

    def send(self, method: str, params: dict | None = None) -> dict:
        self.msg_id += 1
        self.ws.send(json.dumps({"id": self.msg_id, "method": method,
                                 "params": params or {}}))
        while True:
            payload = json.loads(self.ws.recv())
            if payload.get("id") == self.msg_id:
                return payload.get("result", {})

    def navigate(self, url: str) -> None:
        self.send("Page.navigate", {"url": url})
        time.sleep(3.5)   # app.js init + first data fetches

    def js(self, expression: str, *, await_promise: bool = False):
        result = self.send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True,
            "awaitPromise": await_promise})
        if "exceptionDetails" in result:
            fail(f"JS exception: {str(result['exceptionDetails'])[:400]}")
        return result.get("result", {}).get("value")

    def screenshot(self, path: Path) -> None:
        shot = self.send("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(shot["data"]))

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def main() -> None:
    chrome = next((c for c in CHROME_CANDIDATES if shutil.which(c)
                   or Path(c).is_file()), None)
    if chrome is None:
        fail("Google Chrome not found")

    scratch = Path(tempfile.mkdtemp(prefix="monthly-recovery-"))
    serve.MONTHLY_SEARCH_ROOT = scratch / "closure-search"
    serve.CLOSURE_SEARCH_SPEC_DIR = scratch / "specs"
    serve.JOBS_DIR = scratch / "jobs"
    serve.run_in_new_session = fake_run_in_new_session

    httpd = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    geo = json.loads((serve.WEB_DIR / "data" / "network.geojson").read_text())
    edge = next(f["properties"]["id"] for f in geo["features"]
                if f["geometry"]["type"] == "LineString")

    browser = Browser(chrome, scratch / "chrome-profile")
    try:
        # ── Page A starts a monthly search, then "dies" (we reload). ────
        browser.open_tab(base + "/")
        spec = {
            "search_id": "ui-monthly-recovery-test",
            "directed_edges": [edge],
            "demand_build_id": "browser-recovery-release",
            "source": "forecast",
            "permitted_date_start": "2027-07-15",
            "permitted_date_end": "2027-07-15",
            "required_work_minutes": 240,
            "max_consecutive_start_days": 1,
            "permitted_daily_band": {"earliest_start": "06:00",
                                     "latest_end": "10:00"},
            "allowed_weekdays": [3],
        }
        started = browser.js(f"""
        (async () => {{
          const res = await fetch('/api/monthly_search', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{closure_search_spec: {json.dumps(spec)}}}),
          }});
          return {{status: res.status, body: await res.json()}};
        }})()
        """, await_promise=True)
        if started["status"] != 202:
            fail(f"search did not start: {started}")
        print(f"1. search started (202, id {started['body']['search_id']})")

        # ── Reload mid-job: the tab that started the job is now gone. ───
        time.sleep(2)
        browser.navigate(base + "/")
        state = browser.js(
            "fetch('/api/monthly_search/status').then(r => r.json())",
            await_promise=True)
        if state["status"] != "running":
            fail(f"job should still be running after reload: {state}")
        print(f"2. reload mid-job: server still running "
              f"(phase {state.get('progress', {}).get('phase')})")

        # ── The fresh page must discover the running job on its own. ────
        browser.js("document.querySelector("
                   "'.task-card[data-task=\"monthly\"]').click()")
        time.sleep(6)   # ≥ one 4s poll tick of the on-load discovery
        locked = browser.js(
            "document.getElementById('monthly-run-btn').disabled")
        button_text = browser.js(
            "document.getElementById('monthly-run-btn').textContent")
        phase_text = browser.js(
            "document.getElementById('monthly-progress-hint').textContent")
        if not locked or "Söker" not in str(button_text):
            fail(f"fresh page did not re-attach: disabled={locked}, "
                 f"button={button_text!r}")
        if not phase_text:
            fail("workspace phase not surfaced after reload")
        print(f"3. fresh page re-attached: button {button_text!r}, "
              f"phase {phase_text!r}")

        # ── Completion must reach the re-attached page. ─────────────────
        deadline = time.time() + FAKE_JOB_SECONDS + 20
        while time.time() < deadline:
            if browser.js("document.getElementById('monthly-results')"
                          ".classList.contains('show')"):
                break
            time.sleep(1)
        else:
            fail("result panel never appeared on the re-attached page")
        meta = browser.js(
            "document.getElementById('monthly-results-meta').textContent")
        if "SUMO-verifierade" not in str(meta):
            fail(f"result meta lacks the honest claim wording: {meta!r}")
        if "globalt bäst" not in str(meta):
            fail(f"result meta lacks the global-best disclaimer: {meta!r}")
        idle_text = browser.js(
            "document.getElementById('monthly-run-btn').textContent")
        if "Sök arbetsperiod" not in str(idle_text):
            fail(f"run button did not return to idle: {idle_text!r}")
        print("4. completion rendered on the re-attached page with claim "
              "wording; controls back to idle")

        browser.screenshot(scratch / "recovery-final.png")
        print(f"screenshot: {scratch / 'recovery-final.png'}")
        print("BROWSER RECOVERY TEST PASSED")
    finally:
        browser.close()
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
