#!/usr/bin/env python3
"""Benchmark the parent-local batch path against the global daily-unit queue.

Two modes, because two different things need measuring and only one of them
is cheap:

``scheduler`` (default)
    Replaces SUMO with a deterministic sleeping stand-in whose per-unit cost
    follows a declared distribution.  This measures the SCHEDULER - achieved
    width, speedup and the parent-local ceiling - without spending a real
    SUMO-hour per arm.  It is honest about what it is: the per-unit COST is
    not measured here, it is taken from production.

``real``
    Drives ``run_monthly_closure_search.py`` end to end per arm on an
    isolated spec, workspace root and cache root, sampling the live process
    table for SUMO processes that DESCEND FROM THIS ARM.  Expensive but
    conclusive.

Both modes bind evidence equality: every arm's content-addressed cache must
agree byte for byte, otherwise the timings are not comparable and the report
says so instead of publishing a speed number.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import signal
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    PairedObservation,
)
from traffic_sim.simulation.independent_daily import (
    QUEUE_SCREENING_ENV,
    QUEUE_SUPPORTED_SCREENING,
    QUEUE_WORKERS_ENV,
    IndependentDailyRunner,
)
from traffic_sim.simulation.monthly_search import canonical_seed


TARGETS = {"q10": 1, "q50": 1, "q90": 1}
# Production reality, from the frozen 2026-08-27 baseline: 80 330.94 worker
# seconds over 851 simulated units.
PRODUCTION_SECONDS_PER_UNIT = 94.396


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cache_fingerprint(root: Path) -> dict[str, str]:
    """Content key -> file digest for every published entry."""
    return {
        path.stem: _sha256(path)
        for path in sorted(Path(root).glob("*/*.json"))
    }


def benchmark_spec(
    *, days: int, band=("06:00", "12:00"), daily_work_minutes: int = 240
) -> ClosureSearchSpec:
    """A sliding five-day fixture shaped like the production campaign.

    The band is deliberately WIDER than the daily work, so each date carries
    several distinct start windows - the production spec resolves to ~65
    unique units per date, and a one-window-per-date fixture would understate
    how much independent work the global remainder really holds.
    """
    end_day = min(days, 28)
    return ClosureSearchSpec(
        search_id="independent-daily-queue-benchmark",
        directed_edges=("96527131_26842526_0",),
        demand_build_id="benchmark-forecast-2027",
        source="forecast",
        permitted_date_start="2027-09-01",
        permitted_date_end=f"2027-09-{end_day:02d}",
        required_work_minutes=5 * daily_work_minutes,
        max_consecutive_start_days=5,
        permitted_daily_band=DailyTimeBand(*band),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
    )


def five_day_parents(spec: ClosureSearchSpec):
    return [
        item for item in generate_closure_schedules(spec)
        if item.day_count == 5
    ]


class TimedStandInRunner:
    """Deterministic evidence with a declared, reproducible per-unit cost."""

    def __init__(self, *, mean_s: float, spread: float, seed: int):
        self.mean_s = mean_s
        self.spread = spread
        self._rng = random.Random(seed)
        self._durations: dict[str, float] = {}
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.samples: list[float] = []

    def prepare(self, schedules):
        # Draw every duration up front from one seeded stream so each arm
        # replays the SAME per-unit cost profile. Otherwise a wide arm could
        # win simply by drawing cheaper units.
        for schedule in sorted(schedules, key=lambda item: item.schedule_id):
            self._durations[schedule.schedule_id] = max(
                0.0,
                self._rng.gauss(self.mean_s, self.mean_s * self.spread),
            )

    def provenance(self):
        return {"kind": "benchmark-daily-stand-in", "identity": "v1"}

    def queue_sumo_profile(self):
        # The scheduler arm replaces SUMO with a sleep, so it declares the
        # isolated-with-one-SUMO profile the real path must prove.
        return True, 1

    def run_candidate(self, schedule, *, target_repetitions, existing, stage):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        started = time.perf_counter()
        try:
            time.sleep(self._durations[schedule.schedule_id])
            observations = []
            date_number = int(schedule.first_work_date[-2:])
            for variant in ("q10", "q50", "q90"):
                for repetition in range(target_repetitions[variant]):
                    baseline = 1000.0 + date_number
                    observations.append(PairedObservation(
                        candidate_id=schedule.schedule_id,
                        demand_variant=variant,
                        seed=canonical_seed(variant, repetition),
                        baseline_time_loss_s=baseline,
                        candidate_time_loss_s=(
                            baseline + schedule.actual_closed_minutes
                        ),
                        matched_baseline_id=(
                            f"baseline-{schedule.first_work_date}"
                        ),
                        provenance_key=f"daily-{schedule.first_work_date}",
                    ))
            return CandidateEvidence(
                candidate_id=schedule.schedule_id,
                observations=tuple(observations),
            )
        finally:
            elapsed = time.perf_counter() - started
            with self._lock:
                self.active -= 1
                self.samples.append(elapsed)


def run_scheduler_arm(
    *,
    label: str,
    queue_workers: int,
    cache_root: Path,
    spec: ClosureSearchSpec,
    parents,
    mean_s: float,
    spread: float,
    seed: int,
) -> dict:
    child = TimedStandInRunner(mean_s=mean_s, spread=spread, seed=seed)
    runner = IndependentDailyRunner(
        spec,
        daily_runner=child,
        cache_root=cache_root,
        queue_workers=queue_workers,
    )
    runner.prepare(tuple(parents))
    started = time.monotonic()
    try:
        for parent in parents:
            runner.run_candidate(
                parent,
                target_repetitions=TARGETS,
                existing=None,
                stage="pilot",
            )
        wall = time.monotonic() - started
        timing = runner.timing_snapshot()
    finally:
        runner.cleanup()

    samples = sorted(child.samples)
    return {
        "arm": label,
        "queue_workers": queue_workers,
        "wall_seconds": round(wall, 4),
        "units_simulated": timing["units_simulated"],
        "worker_seconds": round(timing["worker_seconds"], 4),
        "achieved_width": (
            round(timing["worker_seconds"] / wall, 3) if wall > 0 else None
        ),
        "max_concurrent_unit_workers": child.max_active,
        "unit_seconds_p50": (
            round(statistics.median(samples), 4) if samples else None
        ),
        "unit_seconds_p95": (
            round(samples[int(len(samples) * 0.95) - 1], 4)
            if len(samples) >= 20 else None
        ),
        "cache_hits": timing["cache_hits"],
        "cache_misses": timing["cache_misses"],
        "cache_corrupt": timing["cache_corrupt"],
        "singleflight_skips": timing.get("queue_singleflight_skips", 0),
        "cache_fingerprint": cache_fingerprint(cache_root),
        "partial_files": [
            str(p) for p in Path(cache_root).rglob("*.tmp")
        ],
    }


def _process_table() -> tuple[dict[int, int], dict[int, str]]:
    """``pid -> ppid`` and ``pid -> command`` for every visible process."""
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return {}, {}
    parents: dict[int, int] = {}
    commands: dict[int, str] = {}
    for line in listing.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        parents[pid] = ppid
        commands[pid] = parts[2]
    return parents, commands


def _descends_from(pid: int, root: int, parents: dict[int, int]) -> bool:
    """Walk the ancestry chain, guarding against cycles and reparenting."""
    seen: set[int] = set()
    current = pid
    while current not in (0, 1) and current not in seen:
        if current == root:
            return True
        seen.add(current)
        current = parents.get(current, 0)
    return current == root


def count_sumo_descendants(root_pid: int) -> int:
    """SUMO processes that descend from ``root_pid``, and only those.

    Counting every ``sumo`` line in ``ps`` instead - which is what an earlier
    version of this sampler did - measures the whole machine.  It is inflated
    by the sampler's own command line, by an unrelated campaign, and by any
    agent whose argv happens to mention SUMO, so it cannot support a claim
    about THIS run's concurrency ceiling.  Ancestry can.
    """
    parents, commands = _process_table()
    return sum(
        1 for pid, command in commands.items()
        if "sumo" in command.lower()
        and "sumo/bin/sumo" in command
        and _descends_from(pid, root_pid, parents)
    )


def sample_sumo_children(
    stop: threading.Event, out: list[int], root_pid: int
) -> None:
    while not stop.is_set():
        out.append(count_sumo_descendants(root_pid))
        stop.wait(1.0)


class GroupCensus(NamedTuple):
    """What the process table says about one process group, right now.

    ``live`` are members that can still execute; ``zombies`` are members that
    have already exited and are only waiting to be reaped.  The distinction
    matters in both directions: escalating against a zombie signals nothing
    and would loop until the grace expired, while counting one as a survivor
    would report a leak that cannot run a single further instruction.
    """

    live: tuple[int, ...]
    zombies: tuple[int, ...]


def inspect_process_group(pgid: int) -> GroupCensus | None:
    """Census one process group, or ``None`` when the table cannot be trusted.

    ``None`` is deliberately distinct from an empty census.  An unreadable or
    unparseable process table means the outcome is UNKNOWN, and an unknown
    outcome must never be reported as a clean reaping - that is precisely the
    claim the frozen 2026-08-27 report could not support.
    """
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,pgid=,state="],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    live: list[int] = []
    zombies: list[int] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            # A row this function cannot read may be the very process it is
            # looking for, so the whole census is untrustworthy.
            return None
        if int(parts[1]) != pgid:
            continue
        # POSIX state codes carry trailing modifiers ("Ss", "Z+"); only the
        # first letter names the state, and 'Z' is the only dead one.
        if parts[2][:1].upper() == "Z":
            zombies.append(int(parts[0]))
        else:
            live.append(int(parts[0]))
    return GroupCensus(tuple(live), tuple(zombies))


def owned_process_group(process: subprocess.Popen) -> int:
    """The group id of a child spawned with ``start_new_session=True``.

    ``setsid()`` makes that child both session and process-group leader, so
    its group id IS its pid, by construction - there is nothing to look up
    and therefore nothing to race.  The lookup below only CONFIRMS that
    construction, while the leader is still unreaped and its pid cannot have
    been recycled.

    There is deliberately no "unknown" answer here.  An unknown group id used
    to fall back on "was the leader reaped?", and that question is not the
    same question: descendants keep the process group after their leader
    exits, so a reaped leader over a live child would have been reported as a
    clean reaping.
    """
    pgid = process.pid
    try:
        observed = os.getpgid(process.pid)
    except (ProcessLookupError, PermissionError, OSError):
        # The leader has not been reaped by this process yet, so its pid is
        # still its own; an unreadable lookup does not change the setsid
        # contract that produced the group.
        return pgid
    if observed != pgid:
        raise ValueError(
            f"process {process.pid} does not lead its own group ({observed}); "
            "the arm must be spawned with start_new_session=True"
        )
    return pgid


def terminate_process_group(
    process: subprocess.Popen,
    *,
    term_grace_s: float = 20.0,
    kill_grace_s: float = 10.0,
    pgid: int | None = None,
    poll_interval_s: float = 0.25,
    zombie_settle_s: float = 2.0,
) -> bool:
    """TERM then KILL the arm's whole process group, bounded at each step.

    Returns True only when the arm's own leader has been REAPED by this
    process and no live member of its group remains.  Both halves are load
    bearing.  Escalation is bounded on purpose: an unbounded wait on a wedged
    SUMO would hang the benchmark in the same place the timeout was supposed
    to rescue it from, and an immediate KILL would deny the search process the
    chance to reap its own children first.

    The leader is reaped here, not left to the caller, because an unreaped
    child stays in the process table as a zombie and therefore still reports
    this group - so a version that only inspected the table could never
    observe its own success, no matter how thoroughly the group had died.
    """
    if pgid is None:
        pgid = owned_process_group(process)
    if pgid == os.getpgid(0):
        # Signalling our own group would kill the benchmark, and pytest with
        # it. This can only happen if the arm was spawned without
        # start_new_session, which is a programming error, not a runtime one.
        raise ValueError(
            f"refusing to signal this process's own group ({pgid}); the arm "
            "must be spawned with start_new_session=True"
        )

    def settled() -> bool | None:
        """True when done, False when live members remain, None when unknown."""
        reaped = process.poll() is not None
        census = inspect_process_group(pgid)
        if census is None:
            return None
        return reaped and not census.live

    def finished() -> bool:
        if not settled():
            return False
        _wait_for_zombies_to_clear(pgid, zombie_settle_s, poll_interval_s)
        return True

    for signal_number, grace in (
        (signal.SIGTERM, term_grace_s),
        (signal.SIGKILL, kill_grace_s),
    ):
        # Look BEFORE signalling. A group that is already reaped and holds
        # nothing but zombies needs no signal at all, and sending one is the
        # false escalation this function claims not to do.
        if finished():
            return True
        try:
            os.killpg(pgid, signal_number)
        except (ProcessLookupError, PermissionError, OSError):
            # Nothing to signal, or not permitted to. Either way the census
            # below - not this call - decides what actually happened.
            pass
        deadline = time.monotonic() + grace
        while True:
            if finished():
                return True
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_interval_s)
    return bool(settled())


def _wait_for_zombies_to_clear(
    pgid: int, settle_s: float, poll_interval_s: float
) -> None:
    """Give the platform reaper a bounded moment to clear dead members.

    A grandchild whose parent this tool just killed is reparented to init and
    reaped there, not here, so its zombie entry is not something this process
    can wait for. Pausing briefly keeps the common case tidy; refusing to
    block on it keeps the timeout path bounded, because a zombie can no
    longer execute anything either way.
    """
    deadline = time.monotonic() + settle_s
    while time.monotonic() < deadline:
        census = inspect_process_group(pgid)
        if census is not None and not census.zombies:
            return
        time.sleep(poll_interval_s)


def run_real_arm(
    *,
    label: str,
    queue_workers: int,
    use_queue: bool,
    spec_path: Path,
    policy_path: Path,
    root: Path,
    cache_root: Path,
    timeout_s: float,
) -> dict:
    command = [
        sys.executable, "run_monthly_closure_search.py",
        "--spec", str(spec_path),
        "--policy", str(policy_path),
        "--baseline-trip-duration-p99-s", "3600",
        "--screening-mode", "independent-exhaustive",
        "--daily-workers", str(queue_workers),
        "--seed-workers", "1",
        "--max-active-sumo-slots", "8",
        "--root", str(root),
        "--daily-result-cache", str(cache_root),
    ]
    # The queue is activated through the environment, never through the CLI:
    # `run_monthly_closure_search.py` is hashed into the daily-unit cache
    # identity, so a flag there would orphan every cached unit.
    environment = dict(os.environ)
    environment.pop(QUEUE_WORKERS_ENV, None)
    environment.pop(QUEUE_SCREENING_ENV, None)
    if use_queue:
        environment[QUEUE_WORKERS_ENV] = str(queue_workers)
        environment[QUEUE_SCREENING_ENV] = QUEUE_SUPPORTED_SCREENING
    stop = threading.Event()
    samples: list[int] = []
    started = time.monotonic()
    timed_out = False
    group_reaped: bool | None = None
    stderr_tail = ""
    returncode: int | None = None
    # Bound every result variable before the try, so a failure path can never
    # leave one unset and turn a reportable timeout into a crash.
    wall = 0.0
    # Own a process GROUP.  An arm is a search process that forks isolated
    # daily workers that fork SUMO, so killing the parent alone - which is
    # what this tool used to do on timeout - leaves both generations running:
    # the frozen 2026-08-27 report records exactly that outcome.  A new
    # session makes the whole tree addressable in one signal, and it also
    # detaches the arm from the terminal group, so a Ctrl-C aimed at the
    # benchmark cannot half-kill a live campaign that shares the terminal.
    process = subprocess.Popen(
        command, cwd=ROOT, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    # Bind the owned group id HERE, while the leader is guaranteed alive and
    # its pid cannot have been recycled. start_new_session makes the child its
    # own group leader, so this is its pid; confirming it at spawn is the only
    # moment the check cannot race the leader's exit.
    owned_pgid = owned_process_group(process)
    # Sampling is anchored to THIS arm's own process, so a concurrent
    # campaign or agent cannot inflate the observed ceiling.
    sampler = threading.Thread(
        target=sample_sumo_children,
        args=(stop, samples, process.pid),
        daemon=True,
    )
    sampler.start()
    try:
        try:
            _, stderr = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            group_reaped = terminate_process_group(process, pgid=owned_pgid)
            _, stderr = process.communicate()
        wall = time.monotonic() - started
        returncode = process.returncode
        stderr_tail = (stderr or "")[-1500:]
    finally:
        stop.set()
        sampler.join(timeout=10)
    return {
        "arm": label,
        "queue_workers": queue_workers,
        "global_queue": use_queue,
        "wall_seconds": round(wall, 3),
        "timed_out": timed_out,
        "process_group_reaped": group_reaped,
        "returncode": returncode,
        "stderr_tail": stderr_tail,
        "max_concurrent_sumo": max(samples) if samples else None,
        "mean_concurrent_sumo": (
            round(sum(samples) / len(samples), 3) if samples else None
        ),
        "sumo_samples": len(samples),
        "cache_fingerprint": cache_fingerprint(cache_root),
        "partial_files": [str(p) for p in Path(cache_root).rglob("*.tmp")],
    }


def evaluate_speed_claim(
    arms: list[dict], *, mode: str
) -> tuple[bool, list[int], list[str]]:
    """Decide whether these arms may carry a speed number at all.

    Byte-identical evidence is necessary and NOT sufficient: two arms that
    both crashed before publishing anything agree perfectly, and so do two
    that were killed at the same point. Every blocker below is something that
    has to be false before comparing their wall clocks means anything.
    """
    fingerprints = [
        arm["cache_fingerprint"] for arm in arms
        if arm.get("cache_fingerprint") is not None
    ]
    identical = (
        bool(arms)
        and len(fingerprints) == len(arms)
        and all(item == fingerprints[0] for item in fingerprints)
    )
    entries_per_arm = [len(item) for item in fingerprints]
    blockers: list[str] = []
    if not identical:
        blockers.append("arms do not agree byte for byte")
    if not entries_per_arm or not all(entries_per_arm):
        blockers.append("an arm published no cache entries")
    if mode == "real":
        expected = max(entries_per_arm) if entries_per_arm else 0
        for arm in arms:
            if arm.get("returncode") != 0:
                blockers.append(f"{arm['arm']} exited {arm.get('returncode')}")
            if arm.get("timed_out"):
                blockers.append(f"{arm['arm']} timed out")
            if not arm.get("sumo_samples"):
                blockers.append(f"{arm['arm']} produced no concurrency samples")
            if len(arm.get("cache_fingerprint") or {}) < expected:
                blockers.append(f"{arm['arm']} is missing evidence")
    for arm in arms:
        if arm.get("partial_files"):
            blockers.append(f"{arm['arm']} left partial cache files")
    return identical, entries_per_arm, blockers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("scheduler", "real"),
                        default="scheduler")
    parser.add_argument("--widths", default="1,2,4,8")
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--mean-unit-seconds", type=float, default=0.9440,
                        help="Per-unit stand-in cost. The default is "
                             "production's measured 94.396 s scaled by 1/100 "
                             "so an arm costs seconds, not hours.")
    parser.add_argument("--time-scale", type=float, default=100.0,
                        help="Factor the stand-in cost was divided by.")
    parser.add_argument("--spread", type=float, default=0.35)
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--workdir", type=Path,
                        default=ROOT / "runs" / "queue-benchmark")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--policy", type=Path,
                        default=ROOT / "validation"
                        / "monthly_search_policy_v2.json")
    parser.add_argument("--timeout-s", type=float, default=5400.0)
    args = parser.parse_args()

    widths = [int(value) for value in args.widths.split(",") if value.strip()]
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "schema_version": 1,
        "kind": "independent_daily_queue_benchmark",
        "release_evidence": False,
        "mode": args.mode,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "widths": widths,
        "arms": [],
    }

    if args.mode == "scheduler":
        spec = benchmark_spec(days=args.days)
        parents = five_day_parents(spec)
        report["fixture"] = {
            "parents": len(parents),
            "permitted_date_start": spec.permitted_date_start,
            "permitted_date_end": spec.permitted_date_end,
            "targets": TARGETS,
            "mean_unit_seconds_standin": args.mean_unit_seconds,
            "time_scale_vs_production": args.time_scale,
            "production_seconds_per_unit": PRODUCTION_SECONDS_PER_UNIT,
            "duration_spread": args.spread,
            "seed": args.seed,
            "note": (
                "Every arm replays the same seeded per-unit duration profile, "
                "so a wide arm cannot win by drawing cheaper units."
            ),
        }
        # Legacy first, then increasing widths; each arm gets a virgin cache.
        arms = [("legacy_parent_local", 1)] + [
            (f"global_queue_w{width}", width) for width in widths
        ]
        for label, width in arms:
            cache_root = workdir / f"cache-{label}"
            if cache_root.exists():
                for path in sorted(cache_root.rglob("*")):
                    if path.is_file():
                        path.unlink()
            result = run_scheduler_arm(
                label=label,
                queue_workers=1 if label == "legacy_parent_local" else width,
                cache_root=cache_root,
                spec=spec,
                parents=parents,
                mean_s=args.mean_unit_seconds,
                spread=args.spread,
                seed=args.seed,
            )
            report["arms"].append(result)
            print(f"{label:24s} wall={result['wall_seconds']:8.3f}s "
                  f"units={result['units_simulated']:4d} "
                  f"width={result['achieved_width']} "
                  f"maxactive={result['max_concurrent_unit_workers']}")
    else:
        if args.spec is None:
            parser.error("--mode real requires --spec")
        arms = [("legacy_parent_local", 1, False)] + [
            (f"global_queue_w{width}", width, True) for width in widths
        ]
        for label, width, use_queue in arms:
            result = run_real_arm(
                label=label,
                queue_workers=width,
                use_queue=use_queue,
                spec_path=args.spec,
                policy_path=args.policy,
                root=workdir / f"root-{label}",
                cache_root=workdir / f"cache-{label}",
                timeout_s=args.timeout_s,
            )
            report["arms"].append(result)
            print(f"{label:24s} wall={result['wall_seconds']:9.1f}s "
                  f"rc={result['returncode']} "
                  f"maxsumo={result['max_concurrent_sumo']}")

    # Evidence equality is a precondition for reporting any speed number -
    # but it is NOT sufficient. Two arms that both produced nothing agree
    # byte for byte, and so do two arms that were both killed at the same
    # point. Every blocker below is something that has to be false before a
    # timing comparison means anything.
    identical, entries_per_arm, blockers = evaluate_speed_claim(
        report["arms"], mode=args.mode
    )
    report["evidence_equality"] = {
        "all_arms_byte_identical": identical,
        "entries_per_arm": entries_per_arm,
        "no_partial_files": all(
            not arm.get("partial_files") for arm in report["arms"]
        ),
    }
    report["speed_claim_blockers"] = blockers
    baseline = next(
        (arm for arm in report["arms"]
         if arm["arm"] == "legacy_parent_local"), None
    )
    if baseline and baseline.get("wall_seconds"):
        for arm in report["arms"]:
            if arm.get("wall_seconds"):
                arm["speedup_vs_legacy"] = round(
                    baseline["wall_seconds"] / arm["wall_seconds"], 3
                )
    report["speed_claim_permitted"] = not blockers
    for arm in report["arms"]:
        arm.pop("cache_fingerprint", None)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"\nevidence identical across arms: {identical}")
    if blockers:
        print("speed claim REFUSED:")
        for item in blockers:
            print(f"  - {item}")
    print(f"wrote {args.out}")
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
