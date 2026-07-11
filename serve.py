"""
Web server for the traffic app: static files + on-demand scenario API.

Run (from repo root):
  python3 serve.py            # or: make serve  →  http://localhost:8000

Endpoints:
  GET /...                    — static files from web/
  GET /api/ping               — {"ok": true}; the web app uses this to decide
                                whether to show the "Stäng väg" feature
  GET /api/close?edges=a,b,c  — starts run_scenario.py --close a b c (Monte
                                Carlo, ~30–90 s) in a BACKGROUND THREAD and
                                returns immediately ({"status": "started"}).
                                One simulation at a time (409 while busy).
  GET /api/close/status       — {"status": "idle"|"running"|"done"|"error", ...};
                                on "done" the scenario manifest fields
                                (file, label, closed_edges, ...) are included
                                directly. Same async-plus-poll reasoning as
                                /api/recalibrate below (2026-07-10 — found in
                                review, matches an already-fixed real
                                incident for that other endpoint).
  GET /api/recalibrate?date=YYYY-MM-DD&source=historical|forecast&days=N
                              — starts the whole-day PFE demand recalibration
                                for a NEW date/source (~6 min for one day)
                                in a BACKGROUND THREAD and returns immediately
                                ({"status": "started"}) — see the note below
                                on why this is async rather than one long
                                blocking request. days (default 1, capped at
                                7) builds DATE through DATE+days-1 as one
                                continuous multi-day demand (B3) instead of
                                a single day; a full week costs ~45 min.
  GET /api/recalibrate/status — {"status": "idle"|"running"|"done"|"error", ...}.
                                The frontend polls this instead of holding
                                one request open for the whole ~6 min, and
                                a fresh page load checks it once too, so a
                                job started from one tab/session is visible
                                from any other and survives a dropped
                                connection, laptop sleep, or closed tab.
  GET /api/suggest_closure?edges=a,b&duration_hours=6&slide_hours=1
                            &top_k=15&extra_bad=2&seeds=3
                              — PLAN.md Phase C5: runs suggest_closure_time.py
                                (Phase C4) against the CURRENTLY calibrated
                                demand and the matching baseline scenario
                                already in web/data/scenarios/baseline.json.
                                Same async/poll pattern as /api/close and
                                /api/recalibrate; one simulation job of any
                                kind at a time (409 while busy).
  GET /api/suggest_closure/status — {"status": ..., ...}; on "done", a
                                SUMMARY of the result (ranked simulated
                                candidates + the proxy-validation numbers),
                                not the full result file (which can hold
                                every candidate window's raw metrics). Load
                                a specific row into a real, viewable
                                scenario via /api/close?edges=...&begin=
                                ...&end=... (the SAME endpoint used for
                                whole-run closures, extended 2026-07-11 to
                                accept an optional time window).

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
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$")

ROOT     = Path(__file__).parent
WEB_DIR  = ROOT / "web"
SCEN_DIR = WEB_DIR / "data" / "scenarios"
SUMO_DIR = ROOT / "sumo"
SUGGEST_OUT = SUMO_DIR / "suggest_closure_web.json"
PORT     = 8000

_sim_lock   = threading.Lock()     # one simulation (close OR recalibrate OR
                                    # suggest_closure) at a time
_recal_lock = threading.Lock()     # guards _recal_state below
_recal_state: dict = {"status": "idle"}
_close_lock = threading.Lock()     # guards _close_state below
_close_state: dict = {"status": "idle"}
_suggest_lock = threading.Lock()   # guards _suggest_state below
_suggest_state: dict = {"status": "idle"}


def run_in_new_session(cmd: list[str], *, cwd: str,
                       timeout: float) -> subprocess.CompletedProcess:
    """subprocess.run(), but a timeout kills the whole process GROUP.

    Every job here spawns grandchildren — run_scenario.py launches SUMO
    seeds, build_sumo_demand.py launches netconvert/duarouter and fork-pool
    workers. subprocess.run()'s own timeout kills only the DIRECT child;
    the grandchildren get reparented and keep running, still writing into
    the shared sumo/ directory — while the finally-block releases
    _sim_lock, so the NEXT job can start and race the orphan's output
    files (IMPROVEMENT_REVIEW 13.8, verified with a real child-spawns-
    grandchild test). start_new_session makes the child a process-group
    leader (pgid == its pid), so one killpg reaps the entire tree.
    """
    proc = subprocess.Popen(cmd, cwd=cwd, start_new_session=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass   # group already gone — the child died just as we timed out
        proc.wait()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


@lru_cache(maxsize=1)
def known_edges() -> frozenset[str]:
    with open(WEB_DIR / "data" / "network.geojson") as f:
        geo = json.load(f)
    return frozenset(feat["properties"]["id"] for feat in geo["features"])


def summarize_suggestion(result: dict) -> dict:
    """Curated table for the UI from suggest_closure_time.py's full result
    file — NOT the raw file itself, which carries every candidate window's
    full per-seed metrics (large for a week-scale search). Honest
    presentation rules (PLAN.md C5): a median + [min, max] interval over
    seeds, never a single fabricated number; the baseline totals included
    explicitly so 'better than what?' always has an answer on screen;
    proxy-only fields keep the word 'rank', never 'minutes'; and N
    simulated vs N candidate windows total is always shown, so a small
    top-k is visibly a small top-k, not silently presented as exhaustive."""
    top_k = result["top_k"]
    candidates = []
    for s in result["simulated"]:
        w = s["window"]
        interval = s["delta_time_loss_interval"]
        candidates.append({
            "begin_s": w["begin_s"], "end_s": w["end_s"],
            "proxy_rank": w["proxy_rank"],
            "in_proxy_top_k": w["proxy_rank"] < top_k,
            "delta_time_loss_median_s": interval["median_s"],
            "delta_time_loss_min_s": interval["min_s"],
            "delta_time_loss_max_s": interval["max_s"],
            "n_seeds": interval["n_seeds"],
            "disqualified": s["comparison"]["candidate_disqualified"],
            "disqualification_reasons": s["comparison"]["disqualification_reasons"],
            "truncated_vehicles": s["truncated_vehicles"],
            "dropped_vehicles": s["dropped_vehicles"],
            "max_queue_vehicles": s["metrics"]["max_queue_vehicles"],
        })
    candidates.sort(key=lambda c: c["proxy_rank"])
    return {
        "edges": result["edges"], "streets": result["streets"],
        "duration_hours": result["duration_hours"],
        "slide_hours": result["slide_hours"],
        "n_candidate_windows": result["n_candidate_windows"],
        "top_k": top_k, "extra_bad": result["extra_bad"],
        "seeds": result["seeds"], "n_simulated": len(result["simulated"]),
        "baseline_total_time_loss_s": result["baseline_metrics"]["total_time_loss_s"],
        "baseline_trip_count": result["baseline_metrics"]["trip_count"],
        "detour_availability": result["detour_availability"],
        "validation": result["validation"],
        "epoch_sim": result["epoch_sim"],
        "demand_signature": result["demand_signature"],
        "candidates": candidates,
    }


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
        if self.path.startswith("/api/close/status"):
            return self._close_status()
        if self.path.startswith("/api/close"):
            return self._close()
        if self.path.startswith("/api/recalibrate/status"):
            return self._recalibrate_status()
        if self.path.startswith("/api/recalibrate"):
            return self._recalibrate()
        if self.path.startswith("/api/suggest_closure/status"):
            return self._suggest_closure_status()
        if self.path.startswith("/api/suggest_closure"):
            return self._suggest_closure()
        return super().do_GET()

    def _close(self) -> None:
        # Async (2026-07-10, same reasoning and pattern as /api/recalibrate
        # below — found in a review of an external improvement document
        # that correctly flagged this as the same risk class, not yet
        # applied here): a closure run is shorter (~30-90s typically) than
        # a recalibration, but the failure mode is identical — a browser
        # tab, proxy, or dropped connection can abandon a blocking request
        # well before its up-to-600s timeout, while the server keeps
        # computing regardless. Polling decouples the job's lifetime from
        # any one connection's, exactly as already proven for recalibrate.
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        edges = [e for e in qs.get("edges", [""])[0].split(",") if e]
        if not edges:
            return self._json(400, {"error": "inga kanter angivna"})
        unknown = [e for e in edges if e not in known_edges()]
        if unknown:
            return self._json(400, {"error": f"okända kanter: {unknown}"})

        # Optional time window (2026-07-11, for C5's "load this suggested
        # window as a real scenario" action): when given, runs a
        # run_scenario.py --closure (time-windowed) instead of --close
        # (whole-run). Both share the same async/poll machinery below —
        # the ONLY difference is which CLI form _run_close shells out to.
        begin = qs.get("begin", [""])[0]
        end   = qs.get("end", [""])[0]
        if bool(begin) != bool(end):
            return self._json(400, {"error": "begin och end måste anges tillsammans"})
        if begin and not (DATETIME_RE.match(begin) and DATETIME_RE.match(end)):
            return self._json(400, {"error": "begin/end måste vara ISO-datetime "
                                    "(YYYY-MM-DDTHH:MM[:SS])"})

        if not _sim_lock.acquire(blocking=False):
            return self._json(409, {"error": "en simulering kör redan — vänta"})
        # Lock stays held for the whole job — released by the background
        # thread, not here (same reasoning as _recalibrate).
        with _close_lock:
            _close_state.clear()
            _close_state.update(status="running", edges=edges,
                                begin=begin or None, end=end or None,
                                started_at=time.time())
        threading.Thread(target=self._run_close, args=(edges, begin or None, end or None),
                         daemon=True).start()
        return self._json(202, {"status": "started", "edges": edges})

    @staticmethod
    def _set_close(**kw) -> None:
        with _close_lock:
            _close_state.update(**kw)

    def _run_close(self, edges: list[str], begin: str | None = None,
                   end: str | None = None) -> None:
        # Same lock-then-state-then-release ordering as _run_recalibrate,
        # for the same reason: writing state AFTER releasing the lock
        # leaves a race window where a second /api/close can acquire the
        # freed lock and start before this job's trailing "done"/"error"
        # write lands, which would stomp the SECOND job's running state
        # with the FIRST job's result.
        try:
            if begin and end:
                cmd = [sys.executable, "run_scenario.py"]
                for e in edges:
                    cmd += ["--closure",
                           json.dumps({"edge_id": e, "begin": begin, "end": end})]
            else:
                cmd = [sys.executable, "run_scenario.py", "--close", *edges]
            res = run_in_new_session(cmd, cwd=str(ROOT), timeout=600)
            if res.returncode != 0:
                print(res.stdout[-1500:], res.stderr[-1500:])
                self._set_close(status="error",
                                error="simuleringen misslyckades — se serverloggen")
                return

            # Matching by closed_edges alone (the old approach) breaks once
            # windowed closures exist: run_scenario.py gives a DISTINCT name
            # (edge set + a hash of the window) to each window on the same
            # edges, so several manifest entries can share the same
            # closed_edges and the old lookup could silently pick the WRONG
            # one. Instead of re-deriving run_scenario.py's own naming logic
            # here (a second implementation that could drift out of sync),
            # parse the exact name it used from its own first stdout line —
            # `print(f"Scenario '{name}' ...")` in main() — and match on
            # that. Found while adding the windowed-closure path (2026-07-11).
            name_match = re.search(r"^Scenario '([^']+)'", res.stdout, re.M)
            if name_match is None:
                self._set_close(status="error",
                                error="kunde inte läsa scenarionamnet — se serverloggen")
                return
            name = name_match.group(1)

            try:
                with open(SCEN_DIR / "index.json") as f:
                    index = json.load(f)
            except FileNotFoundError:
                self._set_close(status="error",
                                error="scenariomanifest saknas — se serverloggen")
                return

            match = next((s for s in index["scenarios"] if s["name"] == name), None)
            if match is None:
                self._set_close(status="error",
                                error="scenariot skrevs inte — se serverloggen")
                return
            self._set_close(status="done", **match)
        except subprocess.TimeoutExpired:
            self._set_close(status="error", error="simuleringen tog >10 min — avbruten")
        except Exception as e:
            print(f"close: unexpected {type(e).__name__}: {e}")
            self._set_close(status="error",
                            error=f"oväntat fel — se serverloggen ({type(e).__name__})")
        finally:
            _sim_lock.release()

    def _close_status(self) -> None:
        with _close_lock:
            state = dict(_close_state)
        if state.get("status") == "running":
            state["elapsed_s"] = round(time.time() - state["started_at"])
        return self._json(200, state)

    def _recalibrate(self) -> None:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        date   = qs.get("date", [""])[0]
        source = qs.get("source", ["historical"])[0]
        days_raw = qs.get("days", ["1"])[0]
        if not DATE_RE.match(date):
            return self._json(400, {"error": "datum måste vara YYYY-MM-DD"})
        if source not in ("historical", "forecast"):
            return self._json(400, {"error": "source måste vara historical eller forecast"})
        try:
            days = int(days_raw)
        except ValueError:
            return self._json(400, {"error": "days måste vara ett heltal"})
        if not (1 <= days <= 7):
            return self._json(400, {"error": "days måste vara 1-7 (en vecka i taget)"})

        if not _sim_lock.acquire(blocking=False):
            return self._json(409, {"error": "en simulering kör redan — vänta"})
        # Lock stays held for the WHOLE job — released by the background
        # thread, not here. The request handler returns immediately; the
        # job's lifetime is no longer tied to this one HTTP connection.
        with _recal_lock:
            _recal_state.clear()
            _recal_state.update(status="running", date=date, source=source,
                                days=days, started_at=time.time())
        threading.Thread(target=self._run_recalibrate, args=(date, source, days),
                         daemon=True).start()
        return self._json(202, {"status": "started", "date": date, "source": source,
                                "days": days})

    @staticmethod
    def _set_recal(**kw) -> None:
        with _recal_lock:
            _recal_state.update(**kw)

    def _run_recalibrate(self, date: str, source: str, days: int = 1) -> None:
        # NOTE: every exit path calls _set_recal BEFORE the finally block
        # releases _sim_lock. Doing it the other way around (release, then
        # update state) leaves a race window where a second recalibration
        # can acquire the freed lock and start (status="running") before
        # this thread's trailing "done"/"error" write lands — which would
        # then stomp the SECOND job's running state with the FIRST job's
        # result. Found in review, not by a failing test — the window is a
        # handful of bytecode instructions wide, easy to miss.
        #
        # Timeout scaling (B3, 2026-07-10): 2400 s was calibrated for ONE
        # day. base=1700/per_day=700 keeps days=1 at exactly the original
        # 2400 s (no behaviour change for existing single-day callers) and
        # gives days=7 a 6600 s (110 min) ceiling — a generous ~2.4x margin
        # over the ~45 min a week is documented to cost in the UI, matching
        # the safety margin the single-day timeout already had relative to
        # its own measured ~6-19 min runtime.
        build_cmd = [sys.executable, "build_sumo_demand.py", "--source", source]
        if days > 1:
            build_cmd += ["--start-date", date, "--days", str(days)]
        else:
            build_cmd += ["--date", date, "--begin", "00:00", "--end", "24:00"]
        build_timeout = 1700 + 700 * days
        try:
            res = run_in_new_session(build_cmd, cwd=str(ROOT),
                                     timeout=build_timeout)
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

            res2 = run_in_new_session(
                [sys.executable, "run_scenario.py"],
                cwd=str(ROOT), timeout=300 + 60 * (days - 1),
            )
            if res2.returncode != 0:
                print(res2.stdout[-2000:], res2.stderr[-2000:])
                self._set_recal(status="error",
                                error="ny baslinje kunde inte byggas — se serverloggen")
                return

            known_edges.cache_clear()   # network.geojson is unchanged but be safe
            self._set_recal(status="done", file="baseline.json",
                            date=date, source=source, days=days)
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

    def _suggest_closure(self) -> None:
        # PLAN.md Phase C5. Same async/poll pattern as _close/_recalibrate;
        # shares _sim_lock with them (this is genuinely a batch of SUMO
        # simulations, same resource class as a closure or recalibration —
        # running it concurrently with either would just starve both), but
        # keeps its OWN _suggest_lock/_suggest_state so its status polling
        # can never be confused with a recalibration's (PLAN.md: "separate
        # lock from demand-rebuild lock, understandable status").
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        edges = [e for e in qs.get("edges", [""])[0].split(",") if e]
        if not edges:
            return self._json(400, {"error": "inga kanter angivna"})
        unknown = [e for e in edges if e not in known_edges()]
        if unknown:
            return self._json(400, {"error": f"okända kanter: {unknown}"})

        try:
            duration_hours = float(qs.get("duration_hours", [""])[0])
        except ValueError:
            return self._json(400, {"error": "duration_hours krävs och måste vara ett tal"})
        if duration_hours <= 0:
            return self._json(400, {"error": "duration_hours måste vara > 0"})
        try:
            slide_hours = float(qs.get("slide_hours", ["1"])[0])
        except ValueError:
            return self._json(400, {"error": "slide_hours måste vara ett tal"})
        if slide_hours <= 0:
            return self._json(400, {"error": "slide_hours måste vara > 0"})

        def int_param(name: str, default: int, lo: int, hi: int) -> int | None:
            try:
                v = int(qs.get(name, [str(default)])[0])
            except ValueError:
                return None
            return v if lo <= v <= hi else None

        top_k = int_param("top_k", 15, 1, 30)
        if top_k is None:
            return self._json(400, {"error": "top_k måste vara ett heltal 1-30"})
        extra_bad = int_param("extra_bad", 2, 0, 5)
        if extra_bad is None:
            return self._json(400, {"error": "extra_bad måste vara ett heltal 0-5"})
        seeds = int_param("seeds", 3, 1, 5)
        if seeds is None:
            return self._json(400, {"error": "seeds måste vara ett heltal 1-5"})

        if not _sim_lock.acquire(blocking=False):
            return self._json(409, {"error": "en simulering kör redan — vänta"})
        with _suggest_lock:
            _suggest_state.clear()
            _suggest_state.update(status="running", edges=edges,
                                  duration_hours=duration_hours, started_at=time.time())
        threading.Thread(
            target=self._run_suggest_closure,
            args=(edges, duration_hours, slide_hours, top_k, extra_bad, seeds),
            daemon=True).start()
        return self._json(202, {"status": "started", "edges": edges})

    @staticmethod
    def _set_suggest(**kw) -> None:
        with _suggest_lock:
            _suggest_state.update(**kw)

    def _run_suggest_closure(self, edges: list[str], duration_hours: float,
                             slide_hours: float, top_k: int, extra_bad: int,
                             seeds: int) -> None:
        try:
            # Budget: one baseline run plus (top_k + extra_bad + 1 low-
            # traffic control) candidate simulations, each up to `seeds`
            # SUMO runs — generous per-candidate margin (suggest_closure_
            # time.py defaults to meso), capped so a large top_k×seeds
            # combination can't tie up the server indefinitely.
            n_candidates = top_k + extra_bad + 1
            timeout = min(3600, 180 + n_candidates * seeds * 60)
            cmd = [sys.executable, "suggest_closure_time.py",
                  "--edge", *edges,
                  "--duration-hours", str(duration_hours),
                  "--slide-hours", str(slide_hours),
                  "--top-k", str(top_k), "--extra-bad", str(extra_bad),
                  "--seeds", str(seeds), "--out", str(SUGGEST_OUT)]
            res = run_in_new_session(cmd, cwd=str(ROOT), timeout=timeout)
            if res.returncode != 0:
                print(res.stdout[-2000:], res.stderr[-2000:])
                # suggest_closure_time.py's own user-facing errors (stale
                # baseline, unknown edge, duration doesn't fit the demand
                # period) are sys.exit(msg) — surfaced verbatim to the UI
                # instead of a generic message, same as /api/recalibrate.
                tail = res.stderr.strip().splitlines()
                last_line = tail[-1] if tail else ""
                msg = last_line if last_line and len(last_line) < 200 else \
                     "förslaget kunde inte beräknas — se serverloggen"
                self._set_suggest(status="error", error=msg)
                return

            try:
                with open(SUGGEST_OUT) as f:
                    result = json.load(f)
            except FileNotFoundError:
                self._set_suggest(status="error",
                                  error="resultatfilen skrevs inte — se serverloggen")
                return
            self._set_suggest(status="done", result=summarize_suggestion(result))
        except subprocess.TimeoutExpired:
            self._set_suggest(status="error",
                              error="förslaget tog för lång tid — avbruten")
        except Exception as e:
            print(f"suggest_closure: unexpected {type(e).__name__}: {e}")
            self._set_suggest(status="error",
                              error=f"oväntat fel — se serverloggen ({type(e).__name__})")
        finally:
            _sim_lock.release()

    def _suggest_closure_status(self) -> None:
        with _suggest_lock:
            state = dict(_suggest_state)
        if state.get("status") == "running":
            state["elapsed_s"] = round(time.time() - state["started_at"])
        return self._json(200, state)


def main() -> None:
    known_edges()   # fail fast if data is missing
    # Mutating API endpoints have no authentication, so do not expose them
    # to the LAN by default. Explicit LAN support, if ever needed, should be
    # an intentional opt-in rather than the server's implicit bind address.
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving web/ + scenario-API på http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
