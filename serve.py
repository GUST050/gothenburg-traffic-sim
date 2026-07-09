"""
Web server for the traffic app: static files + on-demand scenario API.

Run (from repo root):
  python3 serve.py            # or: make serve  →  http://localhost:8000

Endpoints:
  GET /...                    — static files from web/
  GET /api/ping               — {"ok": true}; the web app uses this to decide
                                whether to show the "Stäng väg" feature
  GET /api/close?edges=a,b,c  — runs run_scenario.py --close a b c (Monte
                                Carlo, ~30–90 s) and returns
                                {"file": "<scenario>.json", "label": ...}.
                                One simulation at a time (409 while busy).
  GET /api/recalibrate?date=YYYY-MM-DD&source=historical|forecast
                              — starts the whole-day PFE demand recalibration
                                for a NEW date/source (~6 min) in a
                                BACKGROUND THREAD and returns immediately
                                ({"status": "started"}) — see the note below
                                on why this is async rather than one long
                                blocking request.
  GET /api/recalibrate/status — {"status": "idle"|"running"|"done"|"error", ...}.
                                The frontend polls this instead of holding
                                one request open for the whole ~6 min, and
                                a fresh page load checks it once too, so a
                                job started from one tab/session is visible
                                from any other and survives a dropped
                                connection, laptop sleep, or closed tab.

WHY ASYNC (found from a real failure): a multi-minute job tied to a single
blocking HTTP GET is fragile — a browser's own request timeout, a closed
tab, a sleeping laptop, or a dropped wifi connection all abandon the
CLIENT side while the SERVER keeps computing regardless (subprocess.run
doesn't know or care that nobody is listening anymore). That happened
twice during development: once to the developer (a curl --max-time
timeout produced a BrokenPipeError when the finished response couldn't be
delivered to a dead connection) and once to the actual user testing "Byt
dag" in a real browser tab — the recalibration succeeded server-side
~10 minutes after they'd already given up and navigated away, silently
leaving the site calibrated against a date/source they never confirmed
seeing. Polling decouples the job's lifetime from any one connection's.

The API shells out to the same run_scenario.py used from the command line —
the server adds no simulation logic of its own, so CLI and UI can never
drift apart.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ROOT     = Path(__file__).parent
WEB_DIR  = ROOT / "web"
SCEN_DIR = WEB_DIR / "data" / "scenarios"
PORT     = 8000

_sim_lock   = threading.Lock()     # one simulation (close OR recalibrate) at a time
_recal_lock = threading.Lock()     # guards _recal_state below
_recal_state: dict = {"status": "idle"}


@lru_cache(maxsize=1)
def known_edges() -> frozenset[str]:
    with open(WEB_DIR / "data" / "network.geojson") as f:
        geo = json.load(f)
    return frozenset(feat["properties"]["id"] for feat in geo["features"])


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt, *args):   # quieter: only API calls
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)

    def end_headers(self):
        # SimpleHTTPRequestHandler sends NO cache-control header at all —
        # browsers then apply their own heuristic caching and can silently
        # keep serving a stale index.html (with old ?v=N script tags, so
        # even the JS cache-busting never kicks in) across edits. Found via
        # browser testing: a real fix committed to disk didn't reach the
        # page because the HTML document itself was served from cache, not
        # just the scripts it references. no-cache (not no-store) still
        # lets the server answer with a fast 304 via If-Modified-Since —
        # this only forces a revalidation round-trip, not a real refetch,
        # so it costs nothing at this app's scale.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/ping"):
            return self._json(200, {"ok": True})
        if self.path.startswith("/api/close"):
            return self._close()
        if self.path.startswith("/api/recalibrate/status"):
            return self._recalibrate_status()
        if self.path.startswith("/api/recalibrate"):
            return self._recalibrate()
        return super().do_GET()

    def _close(self) -> None:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        edges = [e for e in qs.get("edges", [""])[0].split(",") if e]
        if not edges:
            return self._json(400, {"error": "inga kanter angivna"})
        unknown = [e for e in edges if e not in known_edges()]
        if unknown:
            return self._json(400, {"error": f"okända kanter: {unknown}"})

        if not _sim_lock.acquire(blocking=False):
            return self._json(409, {"error": "en simulering kör redan — vänta"})
        try:
            res = subprocess.run(
                [sys.executable, "run_scenario.py", "--close", *edges],
                cwd=str(ROOT), capture_output=True, text=True, timeout=600,
            )
            if res.returncode != 0:
                print(res.stdout[-1500:], res.stderr[-1500:])
                return self._json(500, {"error": "simuleringen misslyckades — se serverloggen"})
        except subprocess.TimeoutExpired:
            return self._json(500, {"error": "simuleringen tog >10 min — avbruten"})
        finally:
            _sim_lock.release()

        # run_scenario updated index.json — find the scenario it just wrote
        with open(SCEN_DIR / "index.json") as f:
            index = json.load(f)
        match = next((s for s in index["scenarios"]
                      if sorted(s.get("closed_edges") or []) == sorted(edges)), None)
        if match is None:
            return self._json(500, {"error": "scenariot skrevs inte — se serverloggen"})
        return self._json(200, match)

    def _recalibrate(self) -> None:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        date   = qs.get("date", [""])[0]
        source = qs.get("source", ["historical"])[0]
        if not DATE_RE.match(date):
            return self._json(400, {"error": "datum måste vara YYYY-MM-DD"})
        if source not in ("historical", "forecast"):
            return self._json(400, {"error": "source måste vara historical eller forecast"})

        if not _sim_lock.acquire(blocking=False):
            return self._json(409, {"error": "en simulering kör redan — vänta"})
        # Lock stays held for the WHOLE job — released by the background
        # thread, not here. The request handler returns immediately; the
        # job's lifetime is no longer tied to this one HTTP connection.
        with _recal_lock:
            _recal_state.clear()
            _recal_state.update(status="running", date=date, source=source,
                                started_at=time.time())
        threading.Thread(target=self._run_recalibrate, args=(date, source),
                         daemon=True).start()
        return self._json(202, {"status": "started", "date": date, "source": source})

    @staticmethod
    def _set_recal(**kw) -> None:
        with _recal_lock:
            _recal_state.update(**kw)

    def _run_recalibrate(self, date: str, source: str) -> None:
        # NOTE: every exit path calls _set_recal BEFORE the finally block
        # releases _sim_lock. Doing it the other way around (release, then
        # update state) leaves a race window where a second recalibration
        # can acquire the freed lock and start (status="running") before
        # this thread's trailing "done"/"error" write lands — which would
        # then stomp the SECOND job's running state with the FIRST job's
        # result. Found in review, not by a failing test — the window is a
        # handful of bytecode instructions wide, easy to miss.
        try:
            res = subprocess.run(
                [sys.executable, "build_sumo_demand.py",
                 "--date", date, "--source", source,
                 "--begin", "00:00", "--end", "24:00"],
                cwd=str(ROOT), capture_output=True, text=True, timeout=2400,
            )
            if res.returncode != 0:
                print(res.stdout[-2000:], res.stderr[-2000:])
                last_line = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else ""
                msg = last_line if len(last_line) < 200 else "omkalibreringen misslyckades — se serverloggen"
                self._set_recal(status="error", error=msg)
                return

            # Old scenarios/closures reflect the PREVIOUS date's demand —
            # discard them rather than leave stale, silently-wrong entries
            # in the picker (a scenario file has no version marker to tell
            # them apart from a fresh one otherwise).
            for f in SCEN_DIR.glob("*.json"):
                f.unlink()

            res2 = subprocess.run(
                [sys.executable, "run_scenario.py"],
                cwd=str(ROOT), capture_output=True, text=True, timeout=300,
            )
            if res2.returncode != 0:
                print(res2.stdout[-2000:], res2.stderr[-2000:])
                self._set_recal(status="error",
                                error="ny baslinje kunde inte byggas — se serverloggen")
                return

            known_edges.cache_clear()   # network.geojson is unchanged but be safe
            self._set_recal(status="done", file="baseline.json",
                            date=date, source=source)
        except subprocess.TimeoutExpired:
            self._set_recal(status="error",
                            error="omkalibreringen tog för lång tid — avbruten")
        except Exception as e:
            # Anything unanticipated (a missing file, a permissions error on
            # the scenario-file cleanup, ...) must still flip the status off
            # "running" — otherwise the frontend polls forever showing a
            # fake ever-increasing elapsed time with no way to know the job
            # actually died. Found in review: only TimeoutExpired was caught.
            print(f"recalibrate: unexpected {type(e).__name__}: {e}")
            self._set_recal(status="error",
                            error=f"oväntat fel — se serverloggen ({type(e).__name__})")
        finally:
            _sim_lock.release()

    def _recalibrate_status(self) -> None:
        with _recal_lock:
            state = dict(_recal_state)
        if state.get("status") == "running":
            state["elapsed_s"] = round(time.time() - state["started_at"])
        return self._json(200, state)


def main() -> None:
    known_edges()   # fail fast if data is missing
    server = ThreadingHTTPServer(("", PORT), Handler)
    print(f"Serving web/ + scenario-API på http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
