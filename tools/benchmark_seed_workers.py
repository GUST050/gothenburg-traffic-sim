#!/usr/bin/env python3
"""Stage-A2 resource benchmark: is running SUMO seeds concurrently safe?

Runs the tracked golden monthly search twice against the SAME frozen demand
archive — once with one SUMO worker, once with several — and asks the two
questions the speed plan's stage-A2 gate asks:

  1. does concurrency change the evidence?   (result.json must be identical)
  2. does it fit the machine?                (measured peak RSS of the whole
                                              process tree vs a budget)

Only if both hold does it write the record that unlocks --seed-workers in
run_monthly_closure_search.py. Anything else leaves parallel SUMO closed.

    python3 tools/benchmark_seed_workers.py --workers 3

Run it on an otherwise idle machine: a concurrent build makes both the wall
time and the memory ceiling meaningless.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.simulation.monthly_sumo import SEED_WORKER_BENCHMARK_RECORD

GOLDEN_SPEC = Path("validation/golden_monthly_search_spec_v6.json")
GOLDEN_POLICY = Path("validation/monthly_search_policy_v1.json")
GOLDEN_RECORD = Path("validation/golden_monthly_search_v1.json")


def _tree_rss_bytes() -> int:
    """Resident bytes across this user's python/sumo processes.

    Deliberately coarse: the question is whether N concurrent SUMO runs fit
    in memory, and SUMO plus its driving python are the only things this
    benchmark starts.
    """
    try:
        out = subprocess.run(
            ["ps", "-Ao", "rss=,comm="], capture_output=True, text=True,
            check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    total = 0
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        rss, comm = parts
        name = comm.rsplit("/", 1)[-1].lower()
        if "sumo" in name or "python" in name:
            try:
                total += int(rss) * 1024
            except ValueError:
                continue
    return total


class RssSampler(threading.Thread):
    def __init__(self, interval_s: float = 1.0) -> None:
        super().__init__(daemon=True)
        self.interval_s = interval_s
        self.peak = 0
        # NOT _stop: threading.Thread.join() calls its own self._stop(), so an
        # attribute of that name shadows a method the runtime needs.
        self._done = threading.Event()

    def run(self) -> None:
        while not self._done.is_set():
            self.peak = max(self.peak, _tree_rss_bytes())
            self._done.wait(self.interval_s)

    def stop(self) -> int:
        self._done.set()
        self.join(timeout=10)
        return self.peak


def run_search(archive: Path, root: Path, cache: Path, workers: int,
               allow_unapproved: bool) -> dict:
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(cache, ignore_errors=True)
    root.mkdir(parents=True)
    cache.mkdir(parents=True)
    command = [
        sys.executable, "run_monthly_closure_search.py",
        "--spec", str(GOLDEN_SPEC),
        "--policy", str(GOLDEN_POLICY),
        "--demand-archive", str(archive),
        "--baseline-trip-duration-p99-s", "3600",
        "--screening-mode", "bounded-exhaustive",
        "--root", str(root),
        "--baseline-cache", str(cache),
        "--seed-workers", str(workers),
        # Pin daily workers to 1 on BOTH arms so seed parallelism is the only
        # variable. Left at its default of 3 the serial arm would itself run
        # 3 concurrent SUMO processes (making "serial" a misnomer and its RSS
        # baseline meaningless), and the parallel arm would be rejected
        # outright: seed and daily parallelism may not be combined.
        "--daily-workers", "1",
        # Both arms must run the SAME execution mode or the identity check
        # compares two different semantics and fails for the wrong reason.
        # Cold is the only mode that can run seeds concurrently at all
        # (run_monthly_closure_search rejects warm + --seed-workers > 1,
        # and its default flipped to warm after the 2026-07-21 record was
        # written), and it is also the shape this gate is asked about:
        # N independent SUMO processes, one connection each.
        "--cold-execution",
    ]
    environment = dict(os.environ)
    if allow_unapproved:
        # The benchmark is what produces the approval; it must be able to
        # exercise the parallel path before that record exists.
        environment["MONTHLY_SEED_WORKER_BENCHMARK"] = "1"
    sampler = RssSampler()
    sampler.start()
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True,
                               env=environment)
    wall_s = time.perf_counter() - started
    peak = sampler.stop()
    if completed.returncode != 0:
        raise SystemExit(
            f"golden search with {workers} worker(s) failed:\n"
            f"{completed.stdout[-4000:]}\n{completed.stderr[-4000:]}")
    results = sorted(root.rglob("result.json"))
    if len(results) != 1:
        raise SystemExit(f"expected exactly one result.json, found {results}")
    return {
        "seed_workers": workers,
        "wall_time_s": round(wall_s, 2),
        "peak_rss_bytes": peak,
        "result_path": str(results[0]),
        "result_sha256": hashlib.sha256(results[0].read_bytes()).hexdigest(),
        "result": json.loads(results[0].read_text(encoding="utf-8")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True,
                        help="Frozen demand archive for the golden spec "
                             "(demand_build_key f59ea19f882259b4).")
    parser.add_argument("--workers", type=int, default=3,
                        help="Concurrent seeds to benchmark (default 3).")
    parser.add_argument("--rss-budget-bytes", type=int,
                        default=8 * 1024 ** 3,
                        help="Peak resident ceiling the parallel run must "
                             "stay under (default 8 GiB).")
    parser.add_argument("--root", type=Path,
                        default=Path("runs") / "a2-seed-benchmark")
    parser.add_argument("--write-record", action="store_true",
                        help="Write the validation record when the gate "
                             "passes. Without it, only report.")
    args = parser.parse_args()
    if args.workers < 2:
        raise SystemExit("--workers must be at least 2 to benchmark anything")

    serial = run_search(args.archive, args.root / "serial",
                        args.root / "cache-serial", 1,
                        allow_unapproved=False)
    parallel = run_search(args.archive, args.root / "parallel",
                          args.root / "cache-parallel", args.workers,
                          allow_unapproved=True)

    identical = serial["result_sha256"] == parallel["result_sha256"]
    within_budget = parallel["peak_rss_bytes"] <= args.rss_budget_bytes
    passed = identical and within_budget
    speedup = (serial["wall_time_s"] / parallel["wall_time_s"]
               if parallel["wall_time_s"] else 0.0)

    print(f"serial   : {serial['wall_time_s']:8.2f}s  peak "
          f"{serial['peak_rss_bytes'] / 1024 ** 3:5.2f} GiB  "
          f"{serial['result_sha256'][:16]}")
    print(f"parallel : {parallel['wall_time_s']:8.2f}s  peak "
          f"{parallel['peak_rss_bytes'] / 1024 ** 3:5.2f} GiB  "
          f"{parallel['result_sha256'][:16]}  ({args.workers} workers)")
    print(f"identical evidence : {identical}")
    print(f"within RSS budget  : {within_budget} "
          f"(budget {args.rss_budget_bytes / 1024 ** 3:.1f} GiB)")
    print(f"speedup            : {speedup:.2f}x")
    print("GATE:", "PASS" if passed else "FAIL")

    if not args.write_record:
        return
    if not passed:
        raise SystemExit("gate failed; no approval record written")
    record = {
        "schema_version": 1,
        "kind": "monthly_seed_worker_benchmark_record",
        "gate_status": "pass",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "plan": "SPEED_ARCHITECTURE_PLAN_2026-07.md stage A2",
        "approved_seed_workers": args.workers,
        "evidence_identical": True,
        "peak_rss_within_budget": True,
        "rss_budget_bytes": args.rss_budget_bytes,
        "golden": {
            "spec_sha256": hashlib.sha256(GOLDEN_SPEC.read_bytes()).hexdigest(),
            "policy_sha256": hashlib.sha256(
                GOLDEN_POLICY.read_bytes()).hexdigest(),
            "demand_archive": str(Path(args.archive).resolve()),
        },
        "measurements": {
            key: {k: v for k, v in value.items() if k != "result"}
            for key, value in (("serial", serial), ("parallel", parallel))
        },
        "speedup": round(speedup, 2),
    }
    SEED_WORKER_BENCHMARK_RECORD.parent.mkdir(parents=True, exist_ok=True)
    SEED_WORKER_BENCHMARK_RECORD.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {SEED_WORKER_BENCHMARK_RECORD}")


if __name__ == "__main__":
    main()
