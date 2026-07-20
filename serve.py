"""
Web server for the traffic app: static files + on-demand scenario API.

Run (from repo root):
  python3 serve.py            # or: make serve  →  http://localhost:8000

Endpoints:
  GET /...                    — static files from web/
  GET /api/ping               — {"ok": true}; the web app uses this to decide
                                whether to show the "Stäng väg" feature
  POST /api/close?edges=a,b,c — legacy loose-parameter form; starts
                                run_scenario.py --close a b c (Monte
                                Carlo, ~30–90 s) in a BACKGROUND THREAD and
                                returns immediately ({"status": "started"}).
                                One simulation at a time (409 while busy).
                                New callers POST {"scenario_spec": {...}}.
  GET /api/close/status       — {"status": "idle"|"running"|"done"|"error", ...};
                                on "done" the scenario manifest fields
                                (file, label, closed_edges, ...) are included
                                directly. Same async-plus-poll reasoning as
                                /api/recalibrate below (2026-07-10 — found in
                                review, matches an already-fixed real
                                incident for that other endpoint).
  POST /api/recalibrate with {"demand_spec": {...}} (legacy query form
                              ?date=YYYY-MM-DD&source=historical|forecast&days=N
                              remains supported)
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
  POST /api/cancel?kind=close|recalibrate|monthly
                              — stops the active process group for the
                                selected job and reports status="cancelled"
                                once cleanup has completed.
  POST /api/suggest_closure?edges=a,b&duration_hours=6&slide_hours=1
                            &top_k=15&extra_bad=2&seeds=3
                              — IMPROVEMENT_PLAN.md Phase C5: runs suggest_closure_time.py
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
  POST /api/optimize_signals (new callers send a full ScenarioSpec; loose
                                edges/window parameters remain supported)
                              — IMPROVEMENT_PLAN.md Phase D5: runs signal_optimize.py
                                (D2, edges omitted/empty — no active
                                closure) or signal_closure_combine.py (D4,
                                edges given — adapts signals to the ACTUAL
                                post-closure routes) against the currently
                                calibrated demand, using optional
                                window_start/window_end parameters (defaults
                                07:00-09:00; not exposed in the UI, same
                                scope-narrowing reasoning as C5's top_k/
                                extra_bad/seeds). Same async/poll pattern,
                                shares _sim_lock with the other three.
  GET /api/optimize_signals/status — {"status": ..., ...}; on "done", a
                                UNIFORM before/after summary regardless of
                                which script ran (see
                                summarize_signal_optimization) — a metric
                                card, a per-junction cycle/split/offset
                                plan diff, and the tls_provenance="synthetic"
                                label/caveat every signal result must carry
                                until IMPROVEMENT_PLAN.md D6 imports real city plans.
  POST /api/monthly_search with {"closure_search_spec": {...}}
                              — Phase 4 step 6: runs the resumable robust
                                recurring closure search
                                (run_monthly_closure_search.py) against the
                                server's FROZEN policy
                                (validation/monthly_search_policy_v1.json).
                                Screening mode follows the held-out gate:
                                with a passing v2 record the validated proxy
                                screens a large candidate set down to a
                                bounded shortlist (only then SUMO-verified);
                                without one it falls back to bounded-
                                exhaustive with a hard cap. Same
                                async/poll pattern, shares _sim_lock.
                                Cancellation kills the process tree but the
                                immutable workspace under
                                runs/closure-search/<search_id>/ stays
                                resumable: POSTing the same spec again
                                loads completed SUMO evidence instead of
                                rerunning it.
  GET /api/monthly_search/status — {"status": ..., ...}; while running,
                                includes the workspace's own persisted
                                progress pointer (phase enumerate/screen/
                                prepare_backend/pilot/finalists/decide/
                                publish + counts) read from its manifest,
                                so a page load in any tab sees a live
                                search. On "done", a curated summary that
                                ALWAYS carries the result's claim_boundary
                                verbatim — global-best/UI claims stay
                                disabled until the new held-out gate passes.

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
import math
import os
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from signal_optimize import SIGNAL_CONDITION_COUNT
from traffic_sim.core.contracts import (ClosureSearchSpec, DemandBuildSpec,
                                         ScenarioSpec,
                                         write_closure_search_spec,
                                         write_demand_build_spec,
                                         write_scenario_spec)
from traffic_sim.simulation.monthly_search import (MonthlySearchPolicy,
                                                   load_passing_heldout_gate)
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation.sensor_fit import assess_output_fit
from traffic_sim.simulation.trajectory_contract import (
    MULTIDAY_MAX_ARTIFACT_BYTES,
    validate_multiday_trajectory,
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$")

ROOT     = Path(__file__).parent
WEB_DIR  = ROOT / "web"
SCEN_DIR = WEB_DIR / "data" / "scenarios"
# E2 staging area (IMPROVEMENT_PLAN.md, audit P0-2): a recalibration builds its complete
# new scenario set HERE, validates it, and only then replaces the live set —
# the standard build-aside/atomic-switch publication pattern (blue-green
# deployment, Humble & Farley "Continuous Delivery"; per-file atomicity from
# POSIX rename(2) via os.replace). Same filesystem as SCEN_DIR so os.replace
# is guaranteed atomic, not a copy.
SCEN_STAGING_DIR = WEB_DIR / "data" / "scenarios_staging"
SUMO_DIR = ROOT / "sumo"
SPEC_DIR = ROOT / "runs" / "scenario_specs"
DEMAND_SPEC_DIR = ROOT / "runs" / "demand_specs"
SUGGEST_OUT = SUMO_DIR / "suggest_closure_web.json"
OPTIMIZE_OUT = SUMO_DIR / "signal_optimize_web.json"
OPTIMIZE_CLOSURE_OUT = SUMO_DIR / "signal_closure_combine_web.json"
# IMPROVEMENT_PLAN.md D5: not exposed in the UI, same scope-narrowing reasoning C5 used
# for top_k/extra_bad/seeds — sane fixed defaults matching every signal_*.py
# CLI tool's own default, rather than a cluttered advanced-options form.
OPTIMIZE_WINDOW_START = "07:00"
OPTIMIZE_WINDOW_END = "09:00"
OPTIMIZE_SEEDS = 3
# D2 owns this count; import it so an added/removed experiment condition
# cannot silently leave the HTTP job timeout stale.
OPTIMIZE_SIGNAL_CONDITIONS = SIGNAL_CONDITION_COUNT
# Monthly closure search (Phase 4 step 6). The policy file is the frozen
# golden artifact — the API never accepts tolerances from the client, so a
# browser cannot vary what was frozen against the golden benchmark.
MONTHLY_POLICY_PATH = ROOT / "validation" / "monthly_search_policy_v1.json"
MONTHLY_SEARCH_ROOT = ROOT / "runs" / "closure-search"
CLOSURE_SEARCH_SPEC_DIR = ROOT / "runs" / "closure_search_specs"
# Frozen with the golden monthly benchmark (its workspace's backend
# provenance records exactly this value); a longer p99 only lengthens
# warm-up, so this is the conservative choice. Changing it is a policy
# change and needs a new golden freeze, not a code edit.
MONTHLY_BASELINE_TRIP_P99_S = 3600
# Fallback cap for bounded-exhaustive mode, used ONLY when no passing
# held-out gate record exists (see _run_monthly_search). With the v2 gate
# passed, the web path uses validated proxy screening instead, which has no
# such cap because it screens to a bounded shortlist before any SUMO. Above
# this cap the bounded-exhaustive CLI fails with a clear message rather than
# silently truncating the search space.
MONTHLY_BOUNDED_EXHAUSTIVE_CAP = 12
# A monthly search is resumable by design; a timeout here only pauses it
# (the workspace keeps every completed candidate), so the cap can be
# generous without risking an unbounded server job.
MONTHLY_TIMEOUT_S = 4 * 3600
PORT     = 8000


def _signal_window_from_spec(spec: ScenarioSpec) -> tuple[str, str]:
    """Return a single-day HH:MM signal window from a validated spec."""
    if spec.analysis_window is None:
        raise ValueError("signal ScenarioSpec requires analysis_window")
    try:
        measure_start = datetime.fromisoformat(
            spec.analysis_window.measure_start.replace("Z", "+00:00"))
        measure_end = datetime.fromisoformat(
            spec.analysis_window.measure_end.replace("Z", "+00:00"))
        scenario_start = datetime.fromisoformat(
            spec.start_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("analysis_window must contain valid ISO datetimes") from exc
    if measure_start.date() != scenario_start.date() or \
            measure_end.date() != scenario_start.date():
        raise ValueError("signal analysis_window must stay within the scenario's first day")
    return measure_start.strftime("%H:%M"), measure_end.strftime("%H:%M")

_sim_lock   = threading.Lock()     # one simulation (close OR recalibrate OR
                                    # suggest_closure OR optimize_signals) at
                                    # a time
_recal_lock = threading.Lock()     # guards _recal_state below
_recal_state: dict = {"status": "idle"}
_close_lock = threading.Lock()     # guards _close_state below
_close_state: dict = {"status": "idle"}
_suggest_lock = threading.Lock()   # guards _suggest_state below
_suggest_state: dict = {"status": "idle"}
_optimize_lock = threading.Lock()  # guards _optimize_state below
_optimize_state: dict = {"status": "idle"}
_monthly_lock = threading.Lock()   # guards _monthly_state below
_monthly_state: dict = {"status": "idle"}

# Per-kind live state, one map — the durable job layer below reads a
# kind's terminal status through this instead of four hardcoded branches.
_STATE_BY_KIND: dict = {
    "recalibrate": (_recal_lock, _recal_state),
    "close": (_close_lock, _close_state),
    "suggest": (_suggest_lock, _suggest_state),
    "optimize": (_optimize_lock, _optimize_state),
    "monthly": (_monthly_lock, _monthly_state),
}

# There is only one simulation-class job at a time (_sim_lock). Keep the
# process-group leader for that job so the UI's Avbryt action can stop the
# actual SUMO/build process tree rather than merely hiding its controls.
_active_job_lock = threading.Lock()
_active_job: dict = {"kind": None, "process": None, "cancel_requested": False,
                     "job_id": None}
_RECOVERY_BLOCKED = False
_ORPHANED_JOB_IDS: set[str] = set()

# ── E4: durable job records (IMPROVEMENT_PLAN.md; audit P0-4) ──────────────────────────
# The four in-memory state dicts vanish with the process: a server restart
# (or crash) mid-job left the UI polling an "idle" server while the
# detached subprocess tree kept running and writing — invisible,
# uncancellable. Every job now also writes runs/jobs/<id>.json at start
# (kind, args, server pid, subprocess pgid once known) and at each terminal
# transition; startup reconciles records left in "running" against live
# pids. In-memory dicts stay the fast path for the existing per-kind
# status endpoints — the durable layer is evidence + recovery, mirroring
# runs.py's manifest-before-work principle at the HTTP-job level.
JOBS_DIR = ROOT / "runs" / "jobs"
_jobs_file_lock = threading.Lock()


def _job_write(job_id: str, record: dict) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = JOBS_DIR / f"{job_id}.json"
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(record, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def job_read(job_id: str) -> dict | None:
    path = JOBS_DIR / f"{job_id}.json"
    if not path.exists() or "/" in job_id or ".." in job_id:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def job_record(job_id: str, **fields) -> None:
    """Read-modify-write one job record (atomic replace, lock serialized)."""
    with _jobs_file_lock:
        record = job_read(job_id) or {"id": job_id}
        record.update(fields)
        _job_write(job_id, record)


def job_list(limit: int = 20) -> list[dict]:
    if not JOBS_DIR.exists():
        return []
    records = [r for p in JOBS_DIR.glob("*.json")
               if (r := job_read(p.stem)) is not None]
    records.sort(key=lambda r: r.get("started_at", 0), reverse=True)
    return records[:limit]


def reconcile_jobs_on_startup(*, block_new_jobs: bool = False) -> int:
    """Resolve records stranded in 'running' by a dead server.

    A subprocess started via start_new_session survives its parent, so a
    record's pgid may still be live after a server crash: mark those
    'orphaned_running' (visible, cancellable by pgid) rather than guessing
    an outcome. A dead pgid is marked 'orphaned' — the job's real outcome
    is unknown and saying so beats claiming failure or success."""
    global _RECOVERY_BLOCKED
    n = 0
    stranded: set[str] = set()
    for record in job_list(limit=1000):
        if record.get("status") != "running":
            continue
        pgid = record.get("pgid")
        alive = False
        if pgid:
            try:
                os.killpg(int(pgid), 0)
                alive = True
            except (ProcessLookupError, PermissionError, ValueError):
                alive = False
        job_record(record["id"],
                   status="orphaned_running" if alive else "orphaned",
                   reconciled_at=time.time())
        stranded.add(record["id"])
        n += 1
    _ORPHANED_JOB_IDS.update(stranded)
    if block_new_jobs and stranded:
        _RECOVERY_BLOCKED = True
    if n:
        print(f"jobs: reconciled {n} stranded record(s) from a previous server")
    return n


def simulation_recovery_block() -> dict | None:
    """Return an admission error until stranded jobs are acknowledged."""
    if not _RECOVERY_BLOCKED:
        return None
    live = sorted(_ORPHANED_JOB_IDS)
    return {
        "error": "simulering blockerad: ett tidigare jobb måste återställas "
                 "eller avbrytas",
        "orphaned_jobs": live,
        "recovery": "/api/cancel?job_id=<id>",
    }


def _acknowledge_orphan(job_id: str) -> tuple[bool, str]:
    """Stop or acknowledge one persisted orphan and release the gate."""
    global _RECOVERY_BLOCKED
    record = job_read(job_id)
    if record is None or record.get("status") not in {
            "orphaned", "orphaned_running"}:
        return False, "okänt eller redan hanterat orphan-jobb"
    pgid = record.get("pgid")
    if pgid:
        try:
            os.killpg(int(pgid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, ValueError):
            pass
    job_record(job_id, status="cancelled", finished_at=time.time(),
               recovery_action="operator_cancelled")
    _ORPHANED_JOB_IDS.discard(job_id)
    if not _ORPHANED_JOB_IDS:
        _RECOVERY_BLOCKED = False
    return True, ""


def begin_active_job(kind: str, args: dict | None = None) -> None:
    job_id = f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}"
    with _active_job_lock:
        _active_job.update(kind=kind, process=None, cancel_requested=False,
                           job_id=job_id)
    job_record(job_id, kind=kind, args=args or {}, status="running",
               started_at=time.time(), server_pid=os.getpid())


def finish_active_job(kind: str) -> None:
    with _active_job_lock:
        if _active_job["kind"] != kind:
            return
        job_id = _active_job["job_id"]
        _active_job.update(kind=None, process=None, cancel_requested=False,
                           job_id=None)
    if job_id is None:
        return
    # The per-kind state dict was already written by the job thread (state
    # BEFORE lock-release ordering, see _run_recalibrate) — snapshot it NOW,
    # synchronously, as the job's terminal record: a second job of the same
    # kind may overwrite the state dict the instant _sim_lock is released.
    entry = _STATE_BY_KIND.get(kind)
    terminal = {}
    if entry is not None:
        lock, state = entry
        with lock:
            terminal = {k: v for k, v in state.items()
                        if isinstance(v, (str, int, float, bool, type(None)))}
    finished = time.time()
    # The FILE write happens off-thread: finish_active_job runs in every
    # job's finally block between the terminal-state write and
    # _sim_lock.release(), and synchronous I/O there widens the exact
    # status-terminal-but-lock-still-held race the state-before-release
    # ordering exists to keep negligible (caught by the existing lifecycle
    # tests). The record's CONTENT is already frozen in `terminal`.
    threading.Thread(
        target=job_record, args=(job_id,),
        kwargs={"status": terminal.get("status", "unknown"),
                "finished_at": finished, "result": terminal},
        daemon=True).start()


def cancel_active_job(kind: str) -> bool:
    """Request cancellation and kill the current process group if spawned."""
    with _active_job_lock:
        if _active_job["kind"] != kind:
            return False
        _active_job["cancel_requested"] = True
        proc = _active_job["process"]
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return True


def active_job_cancelled(kind: str) -> bool:
    with _active_job_lock:
        return _active_job["kind"] == kind and bool(_active_job["cancel_requested"])


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
    with _active_job_lock:
        if _active_job["kind"] is not None:
            _active_job["process"] = proc
            cancel_now = bool(_active_job["cancel_requested"])
            job_id = _active_job["job_id"]
        else:
            cancel_now = False
            job_id = None
    if job_id is not None:
        # start_new_session makes the child a group leader: pgid == pid.
        # Durable, so a post-crash reconcile can see/kill the live tree.
        job_record(job_id, pgid=proc.pid)
    if cancel_now:
        os.killpg(proc.pid, signal.SIGKILL)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass   # group already gone — the child died just as we timed out
        proc.wait()
        raise
    finally:
        with _active_job_lock:
            if _active_job["process"] is proc:
                _active_job["process"] = None
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


@lru_cache(maxsize=1)
def known_edges() -> frozenset[str]:
    with open(WEB_DIR / "data" / "network.geojson") as f:
        geo = json.load(f)
    return frozenset(feat["properties"]["id"] for feat in geo["features"])


def current_sensor_edges() -> dict[str, list[str]]:
    """Return the live GeoJSON's exact reviewed sensor-to-edge mapping.

    This intentionally reads the artifact afresh instead of sharing
    ``known_edges``' process cache: recalibration publication must compare its
    frozen contract against the file that is live at that instant.
    """
    with open(WEB_DIR / "data" / "network.geojson") as handle:
        geo = json.load(handle)
    mapping: dict[str, set[str]] = {}
    for feature in geo.get("features", []):
        props = feature.get("properties") or {}
        sensor_id, edge_id = props.get("sensor_id"), props.get("id")
        if sensor_id is not None and edge_id is not None:
            mapping.setdefault(str(sensor_id), set()).add(str(edge_id))
    return {sensor_id: sorted(edges) for sensor_id, edges in sorted(mapping.items())}


def validate_staged_scenarios(staging_dir: Path,
                              demand_meta_path: Path) -> tuple[bool, str]:
    """E2 publish gate: may this freshly built scenario set replace the live one?

    Checks cover both release structure and final, raw SUMO evidence. PFE
    fit alone is not enough: a scenario may publish only if its staged
    baseline proves the same frozen targets against actual ``edgeData``.
    Returns (ok, reason) — reason is user-displayable Swedish on failure,
    "" on success.
    """
    index_path = staging_dir / "index.json"
    if not index_path.exists():
        return False, "staging saknar index.json"
    try:
        with open(index_path) as f:
            index = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False, "index.json i staging är ogiltig JSON"
    files = [s.get("file") for s in index.get("scenarios", [])]
    if "baseline.json" not in files:
        return False, "den nya baslinjen saknas i staging-manifestet"
    staged_payloads = []
    for fname in files:
        p = staging_dir / str(fname)
        if not p.exists():
            return False, f"manifestet refererar {fname} som saknas i staging"
        try:
            with open(p) as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False, f"{fname} i staging är ogiltig JSON"
        if not payload.get("flows"):
            return False, f"{fname} innehåller inga flöden"
        # E3: a seed that failed its health gates (mass non-insertion,
        # gridlock, teleport leak — run_scenario.seed_health_flags) must
        # not silently become the published truth. Older payloads without
        # the field pass (gate is new).
        if payload.get("seed_health_flags"):
            first = payload["seed_health_flags"][0]
            return False, f"{fname}: simuleringshälsa underkänd ({first})"
        staged_payloads.append((fname, payload))
    baseline = next((payload for fname, payload in staged_payloads
                     if fname == "baseline.json"), None)
    if not demand_meta_path.exists():
        return False, "demand_meta.json saknas efter ombyggnad"
    try:
        with open(demand_meta_path) as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False, "demand_meta.json är ogiltig JSON"
    days = int(meta.get("days", 1) or 1)
    expected_build_id = meta.get("build_id")
    expected_demand_key = meta.get("demand_build_key")
    demand_spec = meta.get("demand_spec") or {}
    if expected_demand_key and demand_spec.get("build_key") != expected_demand_key:
        return False, "demand_meta har en inkonsekvent DemandBuildSpec-identitet"
    if expected_build_id or expected_demand_key:
        for fname, payload in staged_payloads:
            if expected_build_id:
                actual = (payload.get("scenario") or {}).get("build_id")
                if actual != expected_build_id:
                    return False, (f"{fname}: build-ID {actual!r} matchar inte "
                                   f"demandens {expected_build_id!r}")
            if expected_demand_key:
                actual_key = (payload.get("scenario") or {}).get("demand_build_key")
                if actual_key != expected_demand_key:
                    return False, (f"{fname}: demand-build-nyckel {actual_key!r} "
                                   f"matchar inte demandens {expected_demand_key!r}")
    fit = meta.get("pfe_fit") or {}
    geh = fit.get("geh_pct")
    # The deployed pipeline has measured 100.0 on every healthy build; the
    # gate uses 99.0 so a marginal rounding artifact can't block a good
    # build, while a genuinely broken calibration (GEH collapse) cannot
    # publish. Older demand_meta without pfe_fit passes (gate is new).
    if geh is not None and geh < 99.0:
        return False, f"kalibreringen nådde bara GEH<5 {geh}% — publiceras inte"
    if fit.get("infeasible_intervals"):
        return False, (f"{fit['infeasible_intervals']} kvartar var olösliga — "
                       "publiceras inte")
    # q10/q50/q90 are separate demand hypotheses.  A passing q50 cannot hide
    # an infeasible uncertainty variant that contributes to the Monte Carlo
    # ensemble.  Older metadata has no per-variant section and remains
    # backwards compatible.
    for label, variant in (meta.get("pfe_fit_variants") or {}).items():
        geh = variant.get("geh_pct")
        if geh is not None and geh < 99.0:
            return False, (f"varianten {label} nådde bara GEH<5 {geh}% — "
                           "publiceras inte")
        if variant.get("infeasible_intervals"):
            return False, f"varianten {label} har olösliga intervall"
        if variant.get("bound_violations"):
            return False, f"varianten {label} bryter mot hårda bounds"
        if variant.get("unserviceable_edges"):
            return False, f"varianten {label} saknar rutter för mätta kanter"
        if days > 1:
            daily = variant.get("per_day")
            if not isinstance(daily, list) or len(daily) != days:
                return False, (
                    f"varianten {label} saknar separat PFE-fit för varje dag")
            for day_index, row in enumerate(daily, 1):
                if not isinstance(row, dict) or row.get("day") != day_index:
                    return False, (
                        f"varianten {label} saknar PFE-fit för dag {day_index}")
                daily_geh = row.get("geh_pct")
                if daily_geh is None or daily_geh < 99.0:
                    return False, (
                        f"varianten {label} dag {day_index} nådde bara "
                        f"GEH<5 {daily_geh}% — publiceras inte")

    # P0 sensor contract: the PFE target fit is not the final SUMO output.
    # New demand builds persist the frozen target/observation arrays, so a
    # newly staged baseline must also carry the raw edgeData audit.  Legacy
    # metadata has no way to prove this contract and remains readable until
    # it is rebuilt; it must not be mistaken for a validated new release.
    if meta.get("sensor_targets") or meta.get("sensor_output_fit_required"):
        sensor_contract = meta.get("sensor_contract") or {}
        if not sensor_contract.get("registry_sha256") or \
                not sensor_contract.get("network_sha256") or \
                not sensor_contract.get("sensor_edges"):
            return False, "demand saknar sensor-/nätverkskontrakt"
        current_registry_hash = sha256_file(ROOT / "data_in" / "sensors.json")
        current_network_hash = sha256_file(ROOT / "sumo" / "net.net.xml")
        if current_registry_hash and \
                sensor_contract["registry_sha256"] != current_registry_hash:
            return False, "sensorregistret har ändrats sedan demand byggdes"
        if current_network_hash and \
                sensor_contract["network_sha256"] != current_network_hash:
            return False, "SUMO-nätet har ändrats sedan demand byggdes"
        try:
            contract_edges = {
                str(sensor_id): sorted(str(edge_id) for edge_id in edges)
                for sensor_id, edges in sensor_contract["sensor_edges"].items()
            }
            live_edges = current_sensor_edges()
        except (AttributeError, OSError, json.JSONDecodeError, TypeError):
            return False, "kunde inte verifiera aktuella sensor-kanter i GeoJSON"
        if contract_edges != live_edges:
            return False, "sensor-kanterna i GeoJSON matchar inte fryst demand-kontrakt"
        audit = baseline.get("sensor_audit") if baseline else None
        output_fit = audit.get("output_fit") if isinstance(audit, dict) else None
        if not isinstance(audit, dict) or not isinstance(output_fit, dict):
            return False, "baseline saknar SUMO:s slutliga sensor-output-fit"
        expected_quarters = int(baseline.get("n_quarters", 0) or 0)
        assessment = assess_output_fit(
            audit, n_intervals=expected_quarters, days=days)
        if assessment["errors"]:
            return False, assessment["errors"][0]
        provenance = audit.get("provenance") or {}
        if provenance.get("targets") != "demand_metadata" or \
                provenance.get("observations") != "demand_metadata":
            return False, "sensor-output-fit saknar frysta demand-metadata"
    if int(meta.get("n_variants", 1)) >= 3:
        # A staged set without baseline.json must fail with this message, not
        # crash: an AttributeError here would abort publication validation
        # without telling the operator which contract was violated.
        variant_mapping = ((baseline or {}).get("scenario_spec") or {}).get(
            "demand_variant_mapping", {})
        present = {
            str(value) for value in variant_mapping.values()
        } if isinstance(variant_mapping, dict) else set()
        if not {"q50", "q10", "q90"}.issubset(present):
            return False, "baseline saknar q50/q10/q90-varianttäckning"
    # Continuous multi-day demand is a separate evidence contract.  An
    # aggregate end-of-run health record cannot prove that a midnight
    # boundary did not lose vehicles, so staged multi-day results must carry
    # the periodic SUMO summary proof before they replace the live set.
    if days > 1:
        from traffic_sim.simulation.multiday import validate as validate_multiday
        multi_day = baseline.get("multi_day_validation") if baseline else None
        boundaries = meta.get("day_boundaries_s") or [day * 86400
                                                       for day in range(days + 1)]
        errors = validate_multiday(multi_day or {}, days=days,
                                   day_boundaries_s=boundaries)
        if errors:
            return False, "flerdagsvalidering underkänd: " + "; ".join(errors[:2])

        # Simulation mode promises real vehicles. Multi-day playback is a
        # deterministic bounded sample of the representative q50 run, while
        # flows and audits above remain complete. Refuse a technically healthy
        # range that would open in the browser with no cars, an unbounded file,
        # a missing date, or a trajectory from the wrong seed.
        trajectory_name = (baseline or {}).get("trajectories")
        if (not isinstance(trajectory_name, str)
                or Path(trajectory_name).name != trajectory_name):
            return False, "flerdagsbaslinjen saknar säker fordonsfil"
        trajectory_path = staging_dir / trajectory_name
        if not trajectory_path.is_file():
            return False, "flerdagsbaslinjens fordonsfil saknas i staging"
        if trajectory_path.stat().st_size > MULTIDAY_MAX_ARTIFACT_BYTES:
            return False, "flerdagsbaslinjens fordonsfil överskrider diskbudgeten"
        try:
            with open(trajectory_path) as handle:
                trajectory = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return False, "flerdagsbaslinjens fordonsfil är ogiltig JSON"
        trajectory_seed = trajectory.get("seed")
        variant_mapping = ((baseline or {}).get("scenario_spec") or {}).get(
            "demand_variant_mapping", {})
        if (not isinstance(variant_mapping, dict)
                or variant_mapping.get(str(trajectory_seed)) != "q50"):
            return False, "flerdagsfordon kommer inte från representativt q50-frö"
        representative_health = next((
            row for row in (baseline.get("seed_health") or [])
            if isinstance(row, dict) and row.get("seed") == trajectory_seed
        ), None)
        expected_inserted = (
            representative_health.get("inserted")
            if isinstance(representative_health, dict) else None)
        trajectory_errors = validate_multiday_trajectory(
            trajectory, days=days, expected_inserted=expected_inserted)
        if trajectory_errors:
            return False, "flerdagsfordon underkända: " + "; ".join(
                trajectory_errors[:2])
    return True, ""


def publish_staged_scenarios(staging_dir: Path, live_dir: Path) -> int:
    """Atomically switch the live scenario set to the staged one.

    Order matters and is deliberate: (1) scenario/trajectory files first,
    (2) index.json LAST — the index is the UI's entry point, so a reader
    can never see the new manifest before every file it references exists;
    (3) only after the new index is live, delete old files it no longer
    references. Each step is an os.replace/unlink — per-file atomic on the
    same filesystem (POSIX rename(2)); a crash mid-publish leaves a
    superset of a valid state, never a hole. Known, accepted window: a
    reader holding the OLD index for the milliseconds of step (1) can
    fetch a same-named file (baseline.json) that is already new — the next
    index fetch self-heals, and the alternative (unique per-run filenames)
    is E1 slice 2's pointer indirection, deferred with it. Returns the
    number of files published.
    """
    live_dir.mkdir(parents=True, exist_ok=True)
    staged = sorted(p.name for p in staging_dir.glob("*.json"))
    for name in staged:
        if name != "index.json":
            os.replace(staging_dir / name, live_dir / name)
    os.replace(staging_dir / "index.json", live_dir / "index.json")
    with open(live_dir / "index.json") as f:
        index = json.load(f)
    referenced = {s.get("file") for s in index.get("scenarios", [])}
    referenced |= {str(s.get("file", "")).replace(".json", "_traj.json")
                   for s in index.get("scenarios", [])}
    referenced.add("index.json")
    for p in live_dir.glob("*.json"):
        if p.name not in referenced:
            p.unlink()
    return len(staged)


def summarize_suggestion(result: dict) -> dict:
    """Curated table for the UI from suggest_closure_time.py's full result
    file — NOT the raw file itself, which carries every candidate window's
    full per-seed metrics (large for a week-scale search). Honest
    presentation rules (IMPROVEMENT_PLAN.md C5): a median + [min, max] interval over
    seeds, never a single fabricated number; the baseline totals included
    explicitly so 'better than what?' always has an answer on screen;
    proxy-only fields keep the word 'rank', never 'minutes'; and N
    simulated vs N candidate windows total is always shown, so a small
    top-k is visibly a small top-k, not silently presented as exhaustive."""
    top_k = result["top_k"]
    decision = result.get("robust_decision")
    decision_stats = {
        item["candidate_id"]: item
        for item in (decision or {}).get("candidates", [])
    }
    winner_id = (decision or {}).get("winner_id")
    tie_ids = set((decision or {}).get("tie_ids", []))
    candidates = []
    for s in result["simulated"]:
        w = s["window"]
        interval = s["delta_time_loss_interval"]
        feasibility = s.get("feasibility")
        candidate_id = f"w{w['begin_s']}"
        robust = decision_stats.get(candidate_id, {})
        candidates.append({
            "candidate_id": candidate_id,
            "begin_s": w["begin_s"], "end_s": w["end_s"],
            "proxy_rank": w["proxy_rank"],
            "in_proxy_top_k": w["proxy_rank"] < top_k,
            "delta_time_loss_median_s": interval["median_s"],
            "delta_time_loss_min_s": interval["min_s"],
            "delta_time_loss_max_s": interval["max_s"],
            "n_seeds": interval["n_seeds"],
            "disqualified": (not feasibility.get("eligible", False)
                             if isinstance(feasibility, dict)
                             else s["comparison"]["candidate_disqualified"]),
            "disqualification_reasons": (feasibility.get("hard_failures", [])
                                          if isinstance(feasibility, dict)
                                          else list(s["comparison"]["disqualification_reasons"])),
            "truncated_vehicles": s["truncated_vehicles"],
            "dropped_vehicles": s["dropped_vehicles"],
            "max_queue_vehicles": s["metrics"]["max_queue_vehicles"],
            "queue_delta": ((feasibility.get("queue") or {}).get("delta")
                            if isinstance(feasibility, dict) else None),
            "evidence_level": s.get("evidence_level", "legacy"),
            "robust_upper_95_s": robust.get("robust_upper_95_s"),
            "worst_variant": robust.get("worst_variant"),
            "is_robust_winner": candidate_id == winner_id,
            "is_robust_tie": candidate_id in tie_ids,
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
        "pilot_selection": result.get("pilot_selection"),
        "robust_decision": decision,
        "claim_boundary": result.get("claim_boundary", {
            "global_best_claim_allowed": False,
        }),
        "candidates": candidates,
    }


def summarize_signal_optimization(result: dict, closure: bool) -> dict:
    """One uniform shape for the UI regardless of which script produced
    `result` — IMPROVEMENT_PLAN.md D5's "Optimera signaler" runs D2's signal_optimize.py
    (no active closure) or D4's signal_closure_combine.py (active closure),
    whose result JSONs have genuinely different internal layouts (5 named
    conditions vs a fixed 2-pass before/after), but the same "before/after
    metric card + per-junction plan diff + provenance label" the web app
    needs to render either way. `closure` says which schema `result` is in.

    D2's own schema has no explicit is_disqualified() field for its
    baseline condition (only comparisons_vs_baseline, computed against the
    CANDIDATE); the same disqualification rule (closure_metrics.py:
    teleports or dropped_unreachable vehicles) is applied here directly to
    `before` so both schemas expose it uniformly — this mirrors
    disqualification_reasons(), not a new policy."""
    def is_disqualified(m: dict) -> bool:
        return bool(m["teleport_total"]) or bool(m.get("dropped_unreachable"))

    if closure:
        before = result["pass1_baseline_signals"]["metrics"]
        after = result["pass2_optimized_signals"]["metrics"]
        comparison = result["comparison"]
        paired = result.get("paired_comparison")
        extra = {
            "closed_edges": result["closed_edges"], "streets": result["streets"],
            "truncated_vehicles": result["truncated_vehicles"],
            "dropped_vehicles": result["dropped_vehicles"],
            "route_stability": result["route_stability"],
        }
    else:
        before = result["conditions"]["baseline"]["metrics"]
        after = result["conditions"]["adapted_coordinated"]["metrics"]
        comparison = result["comparisons_vs_baseline"]["adapted_coordinated"]
        paired = comparison.get("paired")
        extra = {"closed_edges": [], "streets": [],
                 "truncated_vehicles": None, "dropped_vehicles": None,
                 "route_stability": None}

    plan_diff = result["tls_plan_diff"]
    cycle_deltas = [d["cycle_delta_pct"] for d in plan_diff if d["cycle_delta_pct"] is not None]
    median_cycle_delta_pct = statistics.median(cycle_deltas) if cycle_deltas else None

    # The paired (common-random-numbers, seed/demand-variant-matched)
    # comparison was computed by signal_optimize.py/signal_closure_
    # combine.py but, before this fix, never reached the UI — surfaced
    # here alongside a threshold-free sign-agreement check (found in
    # review 2026-07-12). No numeric "practical equivalence" tolerance is
    # invented — that is a research-methodology call for whoever reads
    # these numbers, not something to decide here.
    paired_sign_disagrees = None
    if paired is not None:
        agg = comparison["delta_time_loss_s"]
        med = paired["median_time_loss_delta_s"]
        paired_sign_disagrees = bool(agg != 0 and med != 0 and (agg > 0) != (med > 0))

    return {
        "closure": closure,
        "window_start": result["window_start"], "window_end": result["window_end"],
        "seeds": result["seeds"],
        "tls_provenance": result["tls_provenance"], "caveat": result["caveat"],
        "recommendation_allowed": result["tls_provenance"] != "synthetic",
        "before": before, "after": after,
        "before_disqualified": is_disqualified(before),
        "after_disqualified": is_disqualified(after),
        "delta_time_loss_s": comparison["delta_time_loss_s"],
        "relative_time_loss_pct": comparison["relative_time_loss_pct"],
        "disqualified": comparison["candidate_disqualified"],
        "disqualification_reasons": list(comparison["disqualification_reasons"]),
        "paired_comparison": paired,
        "paired_sign_disagrees": paired_sign_disagrees,
        "n_junctions": len(plan_diff),
        "median_cycle_delta_pct": median_cycle_delta_pct,
        "tls_plan_diff": plan_diff,
        "tls_timing_schedule": result.get("tls_timing_schedule", []),
        # Phase 5 exit condition: every timing result identifies its
        # SignalPlan. Both schemas now carry versioned plan artifacts and
        # per-plan timing certificates (D2 keyed by condition, D4 by pass);
        # the reader gets the identity and the safety status, not just the
        # delay numbers.
        "signal_plan_id": result.get("signal_plan_id"),
        "signal_plan_artifacts": result.get("signal_plan_artifacts", {}),
        "signal_plan_certificate_status": {
            name: certificate.get("status")
            for name, certificate in (
                result.get("signal_plan_certificates") or {}).items()
        },
        **extra,
    }


def summarize_monthly_search(result: dict) -> dict:
    """Curated monthly-search result for the UI.

    Drops the backend's full source-file records (large, server-internal)
    but keeps its identity digests, keeps the exact selected schedules
    verbatim (they ARE the step-7 exact-schedule handoff payload), and
    ALWAYS carries claim_boundary through unchanged — the honesty labels
    (global_best_claim_allowed / ui_exposure_allowed and their reason) must
    never be summarized away between the search and the reader."""
    backend = result.get("simulation_backend") or {}
    policy = result.get("policy") or {}
    return {
        "search_id": result.get("search_id"),
        "status": result.get("status"),
        "winner_id": result.get("winner_id"),
        "tie_ids": result.get("tie_ids", []),
        "selected_schedules": result.get("selected_schedules", []),
        "shortlisted_schedules": result.get("shortlisted_schedules", []),
        "screening": result.get("screening", {}),
        "robust_decision": result.get("robust_decision"),
        "policy_id": policy.get("policy_id"),
        "policy_benchmark_id": policy.get("benchmark_id"),
        "simulation_backend": {
            key: backend.get(key)
            for key in ("kind", "simulation_mode", "sumo_version",
                        "demand_release_id", "source_digest",
                        "simulation_source_digest")
        },
        "claim_boundary": result.get("claim_boundary"),
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
        # J (IMPROVEMENT_PLAN.md; audit P0-3/P1-12): Content-Security-Policy. script-src
        # 'self' works because the app's JS lives in files (the 1300-line
        # inline block moved to app.js for exactly this); unpkg.com serves
        # Leaflet; CARTO serves the basemap tiles; style-src needs
        # unsafe-inline because Leaflet positions panes via inline styles.
        # DevTools also fetches Leaflet's declared source map from the same
        # pinned CDN origin. Allowing that already-trusted origin in
        # connect-src keeps browser diagnostics clean without broadening the
        # policy to arbitrary network destinations.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com; "
            "img-src 'self' data: https://*.basemaps.cartocdn.com; "
            "connect-src 'self' https://unpkg.com; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def _same_origin_ok(self) -> bool:
        """CSRF guard for mutating endpoints (J; audit P0-3).

        The server is loopback-only, but ANY website the user visits can
        fire cross-origin POSTs at http://localhost:8000 — the browser
        blocks reading the RESPONSE, not sending the REQUEST, so a
        malicious page could start recalibrations/closures blind.
        Browsers always attach an Origin header to cross-origin POSTs;
        same-origin fetches from our own page send our own origin. Accept
        only loopback origins — or a missing header (curl and the test
        client, which are not browsers and carry no ambient authority)."""
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        host = urllib.parse.urlparse(origin).hostname
        return host in ("localhost", "127.0.0.1", "::1")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self) -> dict:
        """Read one bounded JSON object from a POST body.

        New callers send a ScenarioSpec body so the complete study identity
        travels as one validated object. An empty body deliberately returns
        ``{}`` for the compatibility path; malformed or oversized bodies are
        rejected before a job can start.
        """
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length måste vara ett heltal") from exc
        if length < 0 or length > 128 * 1024:
            raise ValueError("JSON-förfrågan är för stor")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("POST-kroppen måste vara giltig JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("POST-kroppen måste vara ett JSON-objekt")
        return payload

    # J (IMPROVEMENT_PLAN.md; audit P0-3): read-only endpoints stay GET; every endpoint
    # that STARTS or CANCELS work is POST-only — GET must never mutate
    # (prefetchers, link previews and crawlers follow GETs), and the POST
    # path carries the same-origin CSRF guard above.
    _MUTATING = ("/api/close", "/api/recalibrate", "/api/suggest_closure",
                 "/api/optimize_signals", "/api/monthly_search", "/api/cancel")

    def do_GET(self):
        if self.path.startswith("/api/ping"):
            return self._json(200, {"ok": True})
        if self.path.startswith("/api/close/status"):
            return self._close_status()
        if self.path.startswith("/api/recalibrate/status"):
            return self._recalibrate_status()
        if self.path.startswith("/api/suggest_closure/status"):
            return self._suggest_closure_status()
        if self.path.startswith("/api/optimize_signals/status"):
            return self._optimize_signals_status()
        if self.path.startswith("/api/monthly_search/status"):
            return self._monthly_search_status()
        if self.path.startswith("/api/jobs"):
            return self._jobs()
        if any(self.path.startswith(p) for p in self._MUTATING):
            return self._json(405, {"error": "denna endpoint ändrar "
                                             "tillstånd — använd POST"})
        return super().do_GET()

    def do_POST(self):
        if not self._same_origin_ok():
            return self._json(403, {"error": "förfrågan från främmande "
                                             "webbplats avvisad (CSRF-skydd)"})
        if self.path.startswith("/api/cancel"):
            return self._cancel()
        if self.path.startswith("/api/close/status"):
            return self._json(405, {"error": "statusläsning använder GET"})
        if self.path.startswith("/api/close"):
            return self._close()
        if self.path.startswith("/api/recalibrate/status"):
            return self._json(405, {"error": "statusläsning använder GET"})
        if self.path.startswith("/api/recalibrate"):
            return self._recalibrate()
        if self.path.startswith("/api/suggest_closure/status"):
            return self._json(405, {"error": "statusläsning använder GET"})
        if self.path.startswith("/api/suggest_closure"):
            return self._suggest_closure()
        if self.path.startswith("/api/optimize_signals/status"):
            return self._json(405, {"error": "statusläsning använder GET"})
        if self.path.startswith("/api/optimize_signals"):
            return self._optimize_signals()
        if self.path.startswith("/api/monthly_search/status"):
            return self._json(405, {"error": "statusläsning använder GET"})
        if self.path.startswith("/api/monthly_search"):
            return self._monthly_search()
        return self._json(404, {"error": "okänd endpoint"})

    def _jobs(self) -> None:
        """E4 durable job records: /api/jobs (recent) or /api/jobs/<id>."""
        tail = urllib.parse.urlparse(self.path).path[len("/api/jobs"):]
        job_id = tail.strip("/")
        if job_id:
            record = job_read(job_id)
            if record is None:
                return self._json(404, {"error": "okänt jobb-id"})
            return self._json(200, record)
        return self._json(200, {"jobs": job_list()})

    def _cancel(self) -> None:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        orphan_id = qs.get("job_id", [""])[0]
        if orphan_id:
            ok, reason = _acknowledge_orphan(orphan_id)
            if not ok:
                return self._json(404, {"error": reason})
            return self._json(202, {"status": "cancelled",
                                    "job_id": orphan_id})
        kind = qs.get("kind", [""])[0]
        states = {
            "close": (_close_lock, _close_state),
            "recalibrate": (_recal_lock, _recal_state),
            "monthly": (_monthly_lock, _monthly_state),
        }
        if kind not in states:
            return self._json(400, {"error": "okänd körning att avbryta"})
        lock, state = states[kind]
        with lock:
            if state.get("status") not in {"running", "cancelling"}:
                return self._json(409, {"error": "ingen sådan körning pågår"})
            state["status"] = "cancelling"
        if not cancel_active_job(kind):
            return self._json(409, {"error": "körningen avslutades redan"})
        return self._json(202, {"status": "cancelling", "kind": kind})

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
        try:
            body = self._json_body()
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        structured = body.get("scenario_spec")
        spec: ScenarioSpec | None = None
        if structured is not None:
            if qs:
                return self._json(400, {"error":
                                        "ScenarioSpec och query-parametrar får inte blandas"})
            try:
                spec = ScenarioSpec.from_dict(structured)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                return self._json(400, {"error": f"ogiltig ScenarioSpec: {exc}"})
            edges = list(dict.fromkeys(
                closure.edge_id for closure in spec.closures))
            if not edges:
                return self._json(400, {"error":
                                        "ScenarioSpec måste innehålla minst en stängning"})
            begin = end = ""
        else:
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
        if spec is None:
            begin = qs.get("begin", [""])[0]
            end   = qs.get("end", [""])[0]
        if bool(begin) != bool(end):
            return self._json(400, {"error": "begin och end måste anges tillsammans"})
        if begin and not (DATETIME_RE.match(begin) and DATETIME_RE.match(end)):
            return self._json(400, {"error": "begin/end måste vara ISO-datetime "
                                    "(YYYY-MM-DDTHH:MM[:SS])"})

        blocked = simulation_recovery_block()
        if blocked:
            return self._json(503, blocked)
        if not _sim_lock.acquire(blocking=False):
            return self._json(409, {"error": "en simulering kör redan — vänta"})
        # Lock stays held for the whole job — released by the background
        # thread, not here (same reasoning as _recalibrate).
        with _close_lock:
            _close_state.clear()
            _close_state.update(status="running", edges=edges,
                                begin=begin or None, end=end or None,
                                scenario_spec=(spec.to_dict() if spec else None),
                                started_at=time.time())
        begin_active_job("close", {"edges": edges, "begin": begin or None,
                           "end": end or None,
                           "scenario_spec": spec.to_dict() if spec else None})
        threading.Thread(target=self._run_close,
                         args=(edges, begin or None, end or None, spec),
                         daemon=True).start()
        return self._json(202, {"status": "started", "edges": edges,
                                "scenario_id": spec.scenario_id if spec else None})

    @staticmethod
    def _set_close(**kw) -> None:
        with _close_lock:
            _close_state.update(**kw)

    def _run_close(self, edges: list[str], begin: str | None = None,
                   end: str | None = None,
                   spec: ScenarioSpec | None = None) -> None:
        # Same lock-then-state-then-release ordering as _run_recalibrate,
        # for the same reason: writing state AFTER releasing the lock
        # leaves a race window where a second /api/close can acquire the
        # freed lock and start before this job's trailing "done"/"error"
        # write lands, which would stomp the SECOND job's running state
        # with the FIRST job's result.
        try:
            if spec is not None:
                SPEC_DIR.mkdir(parents=True, exist_ok=True)
                spec_path = SPEC_DIR / (
                    f"{spec.scenario_id}-{time.strftime('%Y%m%d-%H%M%S')}-"
                    f"{os.urandom(2).hex()}.json")
                write_scenario_spec(spec_path, spec)
                cmd = [sys.executable, "run_scenario.py",
                       "--scenario-spec", str(spec_path.resolve())]
            elif begin and end:
                cmd = [sys.executable, "run_scenario.py"]
                for e in edges:
                    cmd += ["--closure",
                           json.dumps({"edge_id": e, "begin": begin, "end": end})]
            else:
                cmd = [sys.executable, "run_scenario.py", "--close", *edges]
            res = run_in_new_session(cmd, cwd=str(ROOT), timeout=600)
            if active_job_cancelled("close"):
                self._set_close(status="cancelled")
                return
            if res.returncode != 0:
                print(res.stdout[-1500:], res.stderr[-1500:])
                detail = f"{res.stdout}\n{res.stderr}"
                if "closure integrity is not verified" in detail:
                    self._set_close(
                        status="error",
                        error=("avstängningen kunde inte verifieras: fordon använde "
                               "den stängda vägen eller så saknas edgeData-bevis"))
                    return
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
            if match.get("closure_integrity") != "verified_clean":
                self._set_close(
                    status="error",
                    error=("avstängningen saknar verifierat nollflöde på den "
                           "stängda vägen och visas därför inte"))
                return
            self._set_close(status="done", **match)
        except subprocess.TimeoutExpired:
            self._set_close(status="error", error="simuleringen tog >10 min — avbruten")
        except Exception as e:
            print(f"close: unexpected {type(e).__name__}: {e}")
            self._set_close(status="error",
                            error=f"oväntat fel — se serverloggen ({type(e).__name__})")
        finally:
            finish_active_job("close")
            _sim_lock.release()

    def _close_status(self) -> None:
        with _close_lock:
            state = dict(_close_state)
        if state.get("status") in {"running", "cancelling"}:
            state["elapsed_s"] = round(time.time() - state["started_at"])
        return self._json(200, state)

    def _recalibrate(self) -> None:
        try:
            body = self._json_body()
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        structured = body.get("demand_spec")
        if structured is not None:
            if qs:
                return self._json(400, {"error":
                                        "DemandBuildSpec och query-parametrar får inte blandas"})
            try:
                demand_spec = DemandBuildSpec.from_dict(structured)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                return self._json(400, {"error": f"ogiltig DemandBuildSpec: {exc}"})
        else:
            date = qs.get("date", [""])[0]
            source = qs.get("source", ["historical"])[0]
            days_raw = qs.get("days", ["1"])[0]
            if not DATE_RE.fullmatch(date):
                return self._json(400, {"error":
                                        "datum saknas eller måste vara YYYY-MM-DD"})
            try:
                days = int(days_raw)
            except ValueError:
                return self._json(400, {"error": "days måste vara ett heltal"})
            try:
                demand_spec = DemandBuildSpec(
                    start_date=date, source=source, days=days,
                    begin="00:00", end="24:00",
                    structural_reference_date="2025-09-16")
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
        if demand_spec.structural_reference_date != "2025-09-16":
            return self._json(400, {"error":
                                    "structural_reference_date måste vara 2025-09-16"})
        if demand_spec.purpose != "standard":
            return self._json(400, {"error":
                                    "closure_envelope-demand får bara startas "
                                    "av den isolerade stängningsstudien"})
        date = demand_spec.start_date
        source = demand_spec.source
        days = demand_spec.days

        blocked = simulation_recovery_block()
        if blocked:
            return self._json(503, blocked)
        if not _sim_lock.acquire(blocking=False):
            return self._json(409, {"error": "en simulering kör redan — vänta"})
        # Lock stays held for the WHOLE job — released by the background
        # thread, not here. The request handler returns immediately; the
        # job's lifetime is no longer tied to this one HTTP connection.
        with _recal_lock:
            _recal_state.clear()
            _recal_state.update(status="running", date=date, source=source,
                                days=days, demand_spec=demand_spec.to_dict(),
                                demand_build_key=demand_spec.build_key,
                                started_at=time.time())
        begin_active_job("recalibrate", {"date": date, "source": source,
                                 "days": days,
                                 "demand_spec": demand_spec.to_dict()})
        threading.Thread(target=self._run_recalibrate, args=(demand_spec,),
                         daemon=True).start()
        return self._json(202, {"status": "started", "date": date, "source": source,
                                "days": days, "demand_build_key": demand_spec.build_key})

    @staticmethod
    def _set_recal(**kw) -> None:
        with _recal_lock:
            _recal_state.update(**kw)

    def _run_recalibrate(self, demand_spec: DemandBuildSpec | str,
                         source: str | None = None, days: int = 1) -> None:
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
        # E2 publish-after-validate (IMPROVEMENT_PLAN.md; audit P0-2): the old flow
        # deleted every live scenario BETWEEN the demand build and the
        # baseline rebuild, so a failure/timeout/cancel in that window left
        # the UI with nothing. Now: keep the old coherent set serving, build
        # the new set into staging, gate it, then switch atomically. On any
        # failure the previous set is untouched (the error message says so).
        # Accept the old positional form for in-process callers during the
        # migration, but immediately convert it to the same validated contract
        # used by the HTTP endpoint.
        if not isinstance(demand_spec, DemandBuildSpec):
            demand_spec = DemandBuildSpec(
                start_date=str(demand_spec), source=source or "historical",
                days=days, begin="00:00", end="24:00",
                structural_reference_date="2025-09-16")
        date, source, days = (demand_spec.start_date, demand_spec.source,
                              demand_spec.days)
        DEMAND_SPEC_DIR.mkdir(parents=True, exist_ok=True)
        spec_path = DEMAND_SPEC_DIR / f"{demand_spec.build_key}.json"
        write_demand_build_spec(spec_path, demand_spec)
        # Keep the legacy flags during migration. build_sumo_demand validates
        # every repeated value against --demand-spec, so these flags are
        # compatibility evidence rather than a second source of truth.
        build_cmd = [sys.executable, "build_sumo_demand.py", "--demand-spec",
                     str(spec_path.resolve()), "--source", source,
                     "--keep-scenarios"]
        if days > 1:
            build_cmd += ["--start-date", date, "--days", str(days)]
        else:
            build_cmd += ["--date", date, "--begin", "00:00", "--end", "24:00"]
        build_timeout = 1700 + 700 * days
        try:
            res = run_in_new_session(build_cmd, cwd=str(ROOT),
                                     timeout=build_timeout)
            if active_job_cancelled("recalibrate"):
                self._set_recal(status="cancelled")
                return
            if res.returncode != 0:
                print(res.stdout[-2000:], res.stderr[-2000:])
                last_line = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else ""
                msg = last_line if len(last_line) < 200 else "omkalibreringen misslyckades — se serverloggen"
                self._set_recal(status="error",
                                error=f"{msg} (gamla scenarier behålls)")
                return

            if SCEN_STAGING_DIR.exists():
                for f in SCEN_STAGING_DIR.glob("*"):
                    f.unlink()
            res2 = run_in_new_session(
                [sys.executable, "run_scenario.py",
                 "--out-dir", str(SCEN_STAGING_DIR)],
                cwd=str(ROOT), timeout=300 + 60 * (days - 1),
            )
            if active_job_cancelled("recalibrate"):
                self._set_recal(status="cancelled")
                return
            if res2.returncode != 0:
                print(res2.stdout[-2000:], res2.stderr[-2000:])
                self._set_recal(status="error",
                                error="ny baslinje kunde inte byggas — "
                                      "gamla scenarier behålls")
                return

            ok, reason = validate_staged_scenarios(
                SCEN_STAGING_DIR, SUMO_DIR / "demand_meta.json")
            if not ok:
                print(f"recalibrate: staged set failed validation: {reason}")
                self._set_recal(status="error",
                                error=f"{reason} — gamla scenarier behålls")
                return
            n = publish_staged_scenarios(SCEN_STAGING_DIR, SCEN_DIR)
            print(f"recalibrate: published {n} staged scenario files")
            # G3: the report must describe the LIVE set — refresh after
            # the atomic switch, not from staging.
            try:
                import validation_report
                validation_report.write_report()
            except Exception as exc:
                print(f"validation report: {type(exc).__name__}: {exc}")

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
            finish_active_job("recalibrate")
            _sim_lock.release()

    def _recalibrate_status(self) -> None:
        with _recal_lock:
            state = dict(_recal_state)
        if state.get("status") in {"running", "cancelling"}:
            state["elapsed_s"] = round(time.time() - state["started_at"])
        return self._json(200, state)

    def _suggest_closure(self) -> None:
        # IMPROVEMENT_PLAN.md Phase C5. Same async/poll pattern as _close/_recalibrate;
        # shares _sim_lock with them (this is genuinely a batch of SUMO
        # simulations, same resource class as a closure or recalibration —
        # running it concurrently with either would just starve both), but
        # keeps its OWN _suggest_lock/_suggest_state so its status polling
        # can never be confused with a recalibration's (IMPROVEMENT_PLAN.md: "separate
        # lock from demand-rebuild lock, understandable status").
        try:
            body = self._json_body()
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        structured = body.get("scenario_spec")
        spec: ScenarioSpec | None = None
        structured_seed_count: int | None = None
        if structured is not None:
            if qs:
                return self._json(400, {"error":
                                        "ScenarioSpec och query-parametrar får inte blandas"})
            try:
                spec = ScenarioSpec.from_dict(structured)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                return self._json(400, {"error": f"ogiltig ScenarioSpec: {exc}"})
            if spec.closures:
                return self._json(400, {"error":
                                        "stängningstidssökningen kräver en bas-ScenarioSpec"})
            structured_seed_count = len(spec.seed_set)
            expected_mapping = {
                1000 + index: ("q50", "q10", "q90")[index % 3]
                for index in range(structured_seed_count)
            }
            if (
                spec.simulation_mode != "meso"
                or structured_seed_count < 12
                or structured_seed_count % 3
                or dict(spec.demand_variant_mapping) != expected_mapping
            ):
                return self._json(400, {"error":
                    "robust stängningstidssökning kräver meso och minst 12 "
                    "kanoniskt parade q50/q10/q90-seeds"})
            raw_edges = body.get("edges", [])
            edges = [str(edge) for edge in raw_edges] if isinstance(raw_edges, list) else []
            value = lambda name, default=None: body.get(name, default)
        else:
            edges = [e for e in qs.get("edges", [""])[0].split(",") if e]
            value = lambda name, default=None: qs.get(name, [default])[0]
        if not edges:
            return self._json(400, {"error": "inga kanter angivna"})
        unknown = [e for e in edges if e not in known_edges()]
        if unknown:
            return self._json(400, {"error": f"okända kanter: {unknown}"})

        try:
            duration_hours = float(value("duration_hours", ""))
        except ValueError:
            return self._json(400, {"error": "duration_hours krävs och måste vara ett tal"})
        # float("nan")/float("inf") parse successfully but satisfy neither
        # "> 0" nor "<= 0" (every NaN comparison is False) — the bare
        # duration_hours <= 0 check below silently let NaN/inf through to
        # the background job, where suggest_closure_time.py's
        # round(args.duration_hours * 3600) then raises an unhandled
        # ValueError instead of a clean 400 at the API boundary. Found in
        # external review 2026-07-11 (NEW_CHANGES_REVIEW section 7.2).
        if (not math.isfinite(duration_hours) or duration_hours <= 0 or
                not math.isclose(duration_hours * 4, round(duration_hours * 4),
                                 rel_tol=0.0, abs_tol=1e-9)):
            return self._json(400, {"error": "duration_hours måste vara ett positivt multipel av 0.25"})
        try:
            slide_hours = float(value("slide_hours", "1"))
        except ValueError:
            return self._json(400, {"error": "slide_hours måste vara ett tal"})
        if (not math.isfinite(slide_hours) or slide_hours <= 0 or
                not math.isclose(slide_hours * 4, round(slide_hours * 4),
                                 rel_tol=0.0, abs_tol=1e-9)):
            return self._json(400, {"error": "slide_hours måste vara ett positivt multipel av 0.25"})

        def int_param(name: str, default: int, lo: int, hi: int) -> int | None:
            try:
                v = int(value(name, default))
            except ValueError:
                return None
            return v if lo <= v <= hi else None

        top_k = int_param("top_k", 15, 1, 30)
        if top_k is None:
            return self._json(400, {"error": "top_k måste vara ett heltal 1-30"})
        extra_bad = int_param("extra_bad", 2, 0, 5)
        if extra_bad is None:
            return self._json(400, {"error": "extra_bad måste vara ett heltal 0-5"})
        seeds = int_param(
            "seeds",
            structured_seed_count or 12,
            12,
            24,
        )
        if (
            seeds is None
            or seeds % 3
            or (
                structured_seed_count is not None
                and seeds != structured_seed_count
            )
        ):
            return self._json(400, {"error":
                                    "seeds måste vara 12, 15, 18, 21 eller 24"})

        blocked = simulation_recovery_block()
        if blocked:
            return self._json(503, blocked)
        if not _sim_lock.acquire(blocking=False):
            return self._json(409, {"error": "en simulering kör redan — vänta"})
        with _suggest_lock:
            _suggest_state.clear()
            _suggest_state.update(status="running", edges=edges,
                                  duration_hours=duration_hours,
                                  scenario_spec=(spec.to_dict() if spec else None),
                                  started_at=time.time())
        begin_active_job("suggest", {"edges": edges,
                                     "duration_hours": duration_hours,
                                     "scenario_spec": spec.to_dict() if spec else None})
        threading.Thread(
            target=self._run_suggest_closure,
            args=(edges, duration_hours, slide_hours, top_k, extra_bad, seeds, spec),
            daemon=True).start()
        return self._json(202, {"status": "started", "edges": edges,
                                "scenario_id": spec.scenario_id if spec else None})

    @staticmethod
    def _set_suggest(**kw) -> None:
        with _suggest_lock:
            _suggest_state.update(**kw)

    def _run_suggest_closure(self, edges: list[str], duration_hours: float,
                             slide_hours: float, top_k: int, extra_bad: int,
                             seeds: int,
                             spec: ScenarioSpec | None = None) -> None:
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
            if spec is not None:
                SPEC_DIR.mkdir(parents=True, exist_ok=True)
                spec_path = SPEC_DIR / (
                    f"{spec.scenario_id}-search-{time.strftime('%Y%m%d-%H%M%S')}-"
                    f"{os.urandom(2).hex()}.json")
                write_scenario_spec(spec_path, spec)
                cmd += ["--scenario-spec", str(spec_path.resolve())]
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
            finish_active_job("suggest")
            _sim_lock.release()

    def _suggest_closure_status(self) -> None:
        with _suggest_lock:
            state = dict(_suggest_state)
        if state.get("status") == "running":
            state["elapsed_s"] = round(time.time() - state["started_at"])
        return self._json(200, state)

    def _optimize_signals(self) -> None:
        # IMPROVEMENT_PLAN.md Phase D5. "Optimera signaler" runs against the CURRENTLY
        # loaded scenario's own closed edges — `edges` empty/absent means
        # the active scenario has no closure, so this dispatches to D2's
        # plain signal_optimize.py; edges present means an active closure,
        # so it dispatches to D4's signal_closure_combine.py instead (the
        # combined closure+signal pipeline that adapts signals to where
        # traffic ACTUALLY goes once the road is closed). Same async/poll
        # pattern and same shared _sim_lock as /api/close, /api/recalibrate,
        # /api/suggest_closure — this is the same batch-of-real-SUMO-runs
        # resource class as all three.
        try:
            body = self._json_body()
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        structured = body.get("scenario_spec")
        spec: ScenarioSpec | None = None
        if structured is not None:
            if qs or set(body) != {"scenario_spec"}:
                return self._json(400, {"error":
                                        "ScenarioSpec och query-parametrar får inte blandas"})
            try:
                spec = ScenarioSpec.from_dict(structured)
                if spec.simulation_mode != "micro":
                    raise ValueError("signaloptimering kräver simulation_mode=micro")
                window_start, window_end = _signal_window_from_spec(spec)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                return self._json(400, {"error": f"ogiltig signal-ScenarioSpec: {exc}"})
            edges = list(dict.fromkeys(
                closure.edge_id for closure in spec.closures))
        else:
            edges = [e for e in qs.get("edges", [""])[0].split(",") if e]
            window_start = qs.get("window_start", [OPTIMIZE_WINDOW_START])[0]
            window_end = qs.get("window_end", [OPTIMIZE_WINDOW_END])[0]
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", window_start) or \
                not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", window_end):
            return self._json(400, {"error": "window_start/window_end måste vara HH:MM"})
        unknown = [e for e in edges if e not in known_edges()]
        if unknown:
            return self._json(400, {"error": f"okända kanter: {unknown}"})

        blocked = simulation_recovery_block()
        if blocked:
            return self._json(503, blocked)
        if not _sim_lock.acquire(blocking=False):
            return self._json(409, {"error": "en simulering kör redan — vänta"})
        with _optimize_lock:
            _optimize_state.clear()
            _optimize_state.update(status="running", edges=edges,
                                   window_start=window_start,
                                   window_end=window_end,
                                   scenario_spec=(spec.to_dict() if spec else None),
                                   started_at=time.time())
        begin_active_job("optimize", {"edges": edges,
                                       "window_start": window_start,
                                       "window_end": window_end,
                                       "scenario_spec": spec.to_dict() if spec else None})
        threading.Thread(target=self._run_optimize_signals,
                         args=(edges, window_start, window_end, spec),
                         daemon=True).start()
        return self._json(202, {"status": "started", "edges": edges,
                                "window_start": window_start,
                                "window_end": window_end,
                                "scenario_id": spec.scenario_id if spec else None})

    @staticmethod
    def _set_optimize(**kw) -> None:
        with _optimize_lock:
            _optimize_state.update(**kw)

    def _run_optimize_signals(self, edges: list[str],
                              window_start: str = OPTIMIZE_WINDOW_START,
                              window_end: str = OPTIMIZE_WINDOW_END,
                              spec: ScenarioSpec | None = None) -> None:
        try:
            closure = bool(edges)
            spec_path = None
            if spec is not None:
                SPEC_DIR.mkdir(parents=True, exist_ok=True)
                spec_path = SPEC_DIR / (
                    f"{spec.scenario_id}-signals-{time.strftime('%Y%m%d-%H%M%S')}-"
                    f"{os.urandom(2).hex()}.json")
                write_scenario_spec(spec_path, spec)
            seed_count = len(spec.seed_set) if spec is not None else OPTIMIZE_SEEDS
            if closure:
                out_path = OPTIMIZE_CLOSURE_OUT
                cmd = [sys.executable, "signal_closure_combine.py",
                      "--seeds", str(seed_count), "--out", str(out_path)]
                n_sumo_runs = 2   # D4: exactly two passes, each OPTIMIZE_SEEDS seeds
            else:
                out_path = OPTIMIZE_OUT
                cmd = [sys.executable, "signal_optimize.py",
                      "--seeds", str(seed_count), "--out", str(out_path)]
                n_sumo_runs = OPTIMIZE_SIGNAL_CONDITIONS
            if spec_path is not None:
                cmd += ["--scenario-spec", str(spec_path.resolve())]
            else:
                cmd[2:2] = ["--close", *edges] if closure else [
                    "--window-start", window_start, "--window-end", window_end]
            # Budget: generous per-seed margin for MICRO (much slower than
            # meso) plus the tlsCycleAdaptation/tlsCoordinator/netconvert
            # setup steps, same reasoning as /api/suggest_closure's timeout.
            timeout = 180 + n_sumo_runs * seed_count * 90
            res = run_in_new_session(cmd, cwd=str(ROOT), timeout=timeout)
            if res.returncode != 0:
                print(res.stdout[-2000:], res.stderr[-2000:])
                tail = res.stderr.strip().splitlines()
                last_line = tail[-1] if tail else ""
                msg = last_line if last_line and len(last_line) < 200 else \
                     "signaloptimeringen misslyckades — se serverloggen"
                self._set_optimize(status="error", error=msg)
                return

            try:
                with open(out_path) as f:
                    result = json.load(f)
            except FileNotFoundError:
                self._set_optimize(status="error",
                                   error="resultatfilen skrevs inte — se serverloggen")
                return
            self._set_optimize(status="done",
                               result=summarize_signal_optimization(result, closure))
        except subprocess.TimeoutExpired:
            self._set_optimize(status="error",
                               error="signaloptimeringen tog för lång tid — avbruten")
        except Exception as e:
            print(f"optimize_signals: unexpected {type(e).__name__}: {e}")
            self._set_optimize(status="error",
                               error=f"oväntat fel — se serverloggen ({type(e).__name__})")
        finally:
            finish_active_job("optimize")
            _sim_lock.release()

    def _optimize_signals_status(self) -> None:
        with _optimize_lock:
            state = dict(_optimize_state)
        if state.get("status") == "running":
            state["elapsed_s"] = round(time.time() - state["started_at"])
        return self._json(200, state)

    def _monthly_search(self) -> None:
        # Phase 4 step 6 (IMPROVEMENT_PLAN.md "Persistence, API, and UI").
        # Same async/poll pattern and shared _sim_lock as the other four
        # simulation jobs. The client supplies ONLY the ClosureSearchSpec
        # (user intent); the pilot/finalist policy is the server's frozen
        # golden artifact and screening is bounded-exhaustive, so nothing a
        # browser sends can widen a claim or vary a frozen tolerance.
        try:
            body = self._json_body()
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        structured = body.get("closure_search_spec")
        if structured is None or qs or set(body) != {"closure_search_spec"}:
            return self._json(400, {"error":
                "POST-kroppen måste vara exakt "
                "{\"closure_search_spec\": {...}}"})
        try:
            spec = ClosureSearchSpec.from_dict(structured)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            return self._json(400, {"error": f"ogiltig ClosureSearchSpec: {exc}"})
        unknown = [e for e in spec.directed_edges if e not in known_edges()]
        if unknown:
            return self._json(400, {"error": f"okända kanter: {unknown}"})
        try:
            policy = MonthlySearchPolicy.from_dict(
                json.loads(MONTHLY_POLICY_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return self._json(500, {"error":
                "den frusna månadspolicyn kunde inte läsas — se serverloggen",
                "detail": str(exc)[:200]})
        if policy.status != "golden_frozen":
            return self._json(500, {"error":
                "månadspolicyn är inte golden_frozen — sökning vägras"})

        blocked = simulation_recovery_block()
        if blocked:
            return self._json(503, blocked)
        if not _sim_lock.acquire(blocking=False):
            return self._json(409, {"error": "en simulering kör redan — vänta"})
        with _monthly_lock:
            _monthly_state.clear()
            _monthly_state.update(status="running",
                                  search_id=spec.search_id,
                                  search_content_key=spec.content_key,
                                  policy_id=policy.policy_id,
                                  edges=list(spec.directed_edges),
                                  started_at=time.time())
        begin_active_job("monthly", {
            "search_id": spec.search_id,
            "search_content_key": spec.content_key,
            "closure_search_spec": spec.to_dict(),
        })
        threading.Thread(target=self._run_monthly_search, args=(spec,),
                         daemon=True).start()
        return self._json(202, {"status": "started",
                                "search_id": spec.search_id})

    @staticmethod
    def _set_monthly(**kw) -> None:
        with _monthly_lock:
            _monthly_state.update(**kw)

    def _run_monthly_search(self, spec: ClosureSearchSpec) -> None:
        try:
            CLOSURE_SEARCH_SPEC_DIR.mkdir(parents=True, exist_ok=True)
            spec_path = CLOSURE_SEARCH_SPEC_DIR / (
                f"{spec.search_id}-{time.strftime('%Y%m%d-%H%M%S')}-"
                f"{os.urandom(2).hex()}.json")
            write_closure_search_spec(spec_path, spec)
            cmd = [sys.executable, "run_monthly_closure_search.py",
                   "--spec", str(spec_path.resolve()),
                   "--policy", str(MONTHLY_POLICY_PATH.resolve()),
                   "--baseline-trip-duration-p99-s",
                   str(MONTHLY_BASELINE_TRIP_P99_S)]
            # Screening mode follows the held-out gate: with a passing v2
            # record the proxy IS validated (practical-winner recall 1.0),
            # so use it — it screens hundreds/thousands of legal schedules
            # down to a bounded shortlist that only THEN gets SUMO. Without
            # a passing record, fall back to bounded-exhaustive (every
            # candidate SUMO-verified, hard cap) — the pre-gate safe mode
            # that could only handle a tiny search. This is the same gate
            # the claim boundary checks, so mode and claim stay consistent.
            if load_passing_heldout_gate() is not None:
                cmd += ["--screening-mode", "proxy"]
            else:
                cmd += ["--screening-mode", "bounded-exhaustive",
                        "--bounded-exhaustive-cap",
                        str(MONTHLY_BOUNDED_EXHAUSTIVE_CAP)]
            res = run_in_new_session(cmd, cwd=str(ROOT),
                                     timeout=MONTHLY_TIMEOUT_S)
            if active_job_cancelled("monthly"):
                # The killed CLI leaves its workspace status "running" with
                # every completed candidate published — deliberately NOT
                # finish("cancelled"), which would forbid resuming. POSTing
                # the same spec again continues from the saved evidence.
                self._set_monthly(status="cancelled",
                                  note="arbetsytan är återupptagbar — starta "
                                       "samma sökning igen för att fortsätta")
                return
            if res.returncode != 0:
                print(res.stdout[-2000:], res.stderr[-2000:])
                # run_monthly_closure_search.py's own user-facing errors
                # (over-cap enumeration, failed/cancelled workspace, missing
                # demand archive) are SystemExit(msg) — surface them
                # verbatim, same as /api/suggest_closure.
                tail = res.stderr.strip().splitlines()
                last_line = tail[-1] if tail else ""
                msg = last_line if last_line and len(last_line) < 300 else \
                    "månadssökningen misslyckades — se serverloggen"
                self._set_monthly(status="error", error=msg)
                return
            result_path = (MONTHLY_SEARCH_ROOT / spec.search_id
                           / "artifacts" / "result.json")
            try:
                with open(result_path) as f:
                    result = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                self._set_monthly(status="error",
                                  error="resultatfilen skrevs inte — se serverloggen")
                return
            self._set_monthly(status="done",
                              result=summarize_monthly_search(result))
        except subprocess.TimeoutExpired:
            self._set_monthly(status="error",
                              error="månadssökningen nådde tidsgränsen och "
                                    "pausades — arbetsytan är återupptagbar, "
                                    "starta samma sökning igen")
        except Exception as e:
            print(f"monthly_search: unexpected {type(e).__name__}: {e}")
            self._set_monthly(status="error",
                              error=f"oväntat fel — se serverloggen "
                                    f"({type(e).__name__})")
        finally:
            finish_active_job("monthly")
            _sim_lock.release()

    def _monthly_search_status(self) -> None:
        with _monthly_lock:
            state = dict(_monthly_state)
        if state.get("status") in {"running", "cancelling"}:
            state["elapsed_s"] = round(time.time() - state["started_at"])
            # The CLI child persists a resumable progress pointer in its
            # workspace manifest (atomic replace) — surface it so any tab,
            # including one opened after the job started, sees which stage
            # (enumerate/screen/prepare_backend/pilot/finalists/decide/
            # publish) is running. search_id is contract-validated as a
            # safe key, so it cannot escape MONTHLY_SEARCH_ROOT.
            search_id = state.get("search_id")
            if search_id:
                manifest_path = (MONTHLY_SEARCH_ROOT / str(search_id)
                                 / "manifest.json")
                try:
                    with open(manifest_path) as f:
                        manifest = json.load(f)
                except (OSError, json.JSONDecodeError):
                    manifest = None
                if isinstance(manifest, dict) and \
                        isinstance(manifest.get("progress"), dict):
                    state["progress"] = manifest["progress"]
        return self._json(200, state)


def main() -> None:
    known_edges()   # fail fast if data is missing
    # E4/P0: a restarted server must not start another writer while a prior
    # process group may still be producing artifacts.  The operator can
    # acknowledge each stranded job through /api/cancel?job_id=... after
    # inspecting its durable record.
    reconcile_jobs_on_startup(block_new_jobs=True)
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
