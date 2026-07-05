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
  GET /api/recalibrate?date=YYYY-MM-DD — re-runs the whole-day PFE demand
                                calibration for a NEW date (~5–10 min: PFE
                                + assignment-prior bounds + a fresh
                                baseline scenario), discarding old
                                scenarios/closures since they reflect the
                                previous date's demand. Returns
                                {"file": "baseline.json", "date": ...}.

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
import urllib.parse
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ROOT     = Path(__file__).parent
WEB_DIR  = ROOT / "web"
SCEN_DIR = WEB_DIR / "data" / "scenarios"
PORT     = 8000

_sim_lock = threading.Lock()


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
        try:
            res = subprocess.run(
                [sys.executable, "build_sumo_demand.py",
                 "--date", date, "--source", source,
                 "--begin", "00:00", "--end", "24:00"],
                cwd=str(ROOT), capture_output=True, text=True, timeout=1200,
            )
            if res.returncode != 0:
                print(res.stdout[-2000:], res.stderr[-2000:])
                # sys.exit("short message") in build_sumo_demand.py (e.g. a
                # date/year mismatch) prints just that one line to stderr —
                # surface it directly instead of a generic "see the log".
                last_line = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else ""
                msg = last_line if len(last_line) < 200 else "omkalibreringen misslyckades — se serverloggen"
                return self._json(500, {"error": msg})

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
                return self._json(500, {"error": "ny baslinje kunde inte byggas "
                                                  "— se serverloggen"})
        except subprocess.TimeoutExpired:
            return self._json(500, {"error": "omkalibreringen tog för lång tid "
                                              "— avbruten"})
        finally:
            _sim_lock.release()

        known_edges.cache_clear()   # network.geojson is unchanged but be safe
        return self._json(200, {"file": "baseline.json", "date": date, "source": source})


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
