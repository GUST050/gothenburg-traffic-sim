#!/usr/bin/env python3
"""Supervise a serial daily-cost-cache build under a hard resource budget.

Generic wrapper mirroring wci_profile_rebuild_supervisor_v1.py's proven
supervise-role measurement code (imported, not reimplemented) but driving
tools/build_daily_cost_cache.py instead of the month-ledger profiler. That
tool runs all --build-key values serially in ONE process (no subprocess
workers of its own -- verified: its only subprocess use is a swapusage
sysctl probe), so a single watched process is sufficient.

Aborts fail-closed on wall, physical footprint, swap growth, a forbidden
child process (SUMO/demand), or a new demand archive appearing under any
watched root -- exactly the same conditions the profile supervisor enforces.
Append-only: the summary path must not already exist.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import wci_diag_common as common  # noqa: E402
import wci_profile_rebuild_supervisor_v1 as sup  # noqa: E402

SCHEMA = "wci_daily_cache_supervision_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--child-log", type=Path, required=True)
    parser.add_argument("--sample-log", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--wall-limit-s", type=float, default=17328.0)
    parser.add_argument("--memory-limit-bytes", type=int, default=4 * sup.GIB)
    parser.add_argument("--swap-growth-limit-bytes", type=int,
                        default=1 * sup.GIB)
    parser.add_argument("--watch-archive-root", action="append")
    parser.add_argument(
        "--build-keys-from-registration", type=Path,
        help=("read every archive build key from this registration file "
              "and splice '--build-key <k>' pairs into the child command "
              "in Python, entirely avoiding shell word-splitting"))
    parser.add_argument("child_argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    for path in (args.summary, args.child_log, args.sample_log):
        if path.exists():
            raise SystemExit(f"append-only output already exists: {path}")
    if not args.child_argv or args.child_argv[0] != "--":
        raise SystemExit("pass child command after a literal --")
    child_cmd = args.child_argv[1:]
    if args.build_keys_from_registration:
        registration = json.loads(
            args.build_keys_from_registration.read_text(encoding="utf-8"))
        keys = sorted(registration["archives"].keys())
        if not keys:
            raise SystemExit("registration names no archive build keys")
        key_flags: List[str] = []
        for key in keys:
            key_flags.extend(("--build-key", key))
        child_cmd = child_cmd + key_flags

    watched = [Path(root) for root in (args.watch_archive_root or [])]
    baseline_archives = sup._archive_counts(watched)
    baseline_system = common.system_memory()
    baseline_swap_mb = baseline_system.get("swap_used_mb")

    child_log = args.child_log.open("w", encoding="utf-8")
    child = subprocess.Popen(
        child_cmd, cwd=str(ROOT), start_new_session=True,
        stdout=child_log, stderr=subprocess.STDOUT)

    started = time.time()
    peak_footprint = 0
    peak_lifetime = 0
    peak_cpu_s = 0.0
    peak_tree_size = 0
    max_swap_growth_bytes = 0
    stop_reason: Optional[str] = None
    samples = 0

    with args.sample_log.open("w", encoding="utf-8") as log:
        while True:
            if child.poll() is not None:
                break
            time.sleep(sup.SAMPLE_SECONDS)
            wall = time.time() - started

            table = sup._process_table()
            tree = ([{"pid": child.pid, "ppid": os.getpid(),
                      "comm": "daily-cache"}]
                    + sup._descendants(child.pid, table))
            peak_tree_size = max(peak_tree_size, len(tree))

            footprint = 0
            lifetime = 0
            cpu_s = 0.0
            for row in tree:
                memory = sup._process_footprint(row["pid"])
                if memory is None:
                    continue
                footprint += memory["phys_footprint_bytes"]
                lifetime += memory["lifetime_max_phys_footprint_bytes"]
                cpu_s += memory["cpu_s"]
            peak_footprint = max(peak_footprint, footprint)
            peak_lifetime = max(peak_lifetime, lifetime)
            peak_cpu_s = max(peak_cpu_s, cpu_s)

            system = common.system_memory()
            swap_mb = system.get("swap_used_mb")
            swap_growth = None
            if swap_mb is not None and baseline_swap_mb is not None:
                swap_growth = int((swap_mb - baseline_swap_mb) * 1024 * 1024)
                max_swap_growth_bytes = max(max_swap_growth_bytes,
                                            swap_growth)

            hits = sup._forbidden(tree)
            archives = sup._archive_counts(watched)
            new_archives = {
                root: count for root, count in archives.items()
                if baseline_archives.get(root, count) != count}

            samples += 1
            log.write(json.dumps({
                "at_s": round(wall, 3),
                "tree_processes": len(tree),
                "cpu_s": round(cpu_s, 3),
                "phys_footprint_bytes": footprint,
                "lifetime_max_phys_footprint_bytes": lifetime,
                "swap_used_mb": swap_mb,
                "swap_growth_bytes": swap_growth,
                "forbidden": hits,
                "archive_counts": archives,
            }, sort_keys=True) + "\n")
            log.flush()

            if wall > args.wall_limit_s:
                stop_reason = f"WALL_OVER_LIMIT {wall:.1f}s"
            elif footprint > args.memory_limit_bytes:
                stop_reason = f"FOOTPRINT_OVER_LIMIT {footprint}"
            elif (swap_growth is not None
                  and swap_growth > args.swap_growth_limit_bytes):
                stop_reason = f"SWAP_GROWTH_OVER_LIMIT {swap_growth}"
            elif hits:
                stop_reason = f"FORBIDDEN_PROCESS {hits}"
            elif new_archives:
                stop_reason = f"NEW_DEMAND_ARCHIVE {new_archives}"

            if stop_reason:
                try:
                    os.killpg(os.getpgid(child.pid), signal.SIGTERM)
                    for _ in range(40):
                        if child.poll() is not None:
                            break
                        time.sleep(0.5)
                    if child.poll() is None:
                        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                break

    returncode = child.wait()
    child_log.close()
    wall = time.time() - started
    final_system = common.system_memory()
    leftovers = sup._descendants(child.pid, sup._process_table())

    record = {
        "schema": SCHEMA,
        "diagnostic_only": False,
        "release_evidence": False,
        "claim_boundary": (
            "supervision telemetry for the serial daily-cost-cache build. "
            "The tool's own --status output and per-build-key evidence are "
            "the result; this record is how the run was bounded and what "
            "it was observed to do"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "command": child_cmd,
        "limits": {
            "wall_hard_s": args.wall_limit_s,
            "memory_hard_bytes": args.memory_limit_bytes,
            "swap_growth_hard_bytes": args.swap_growth_limit_bytes,
        },
        "stopped": stop_reason is not None,
        "stop_reason": stop_reason,
        "exit_code": returncode,
        "wall_s": wall,
        "cpu_s": peak_cpu_s,
        "samples": samples,
        "sample_interval_s": sup.SAMPLE_SECONDS,
        "memory": {
            "peak_tree_phys_footprint_bytes": peak_footprint,
            "peak_tree_lifetime_max_phys_footprint_bytes": peak_lifetime,
            "instrument": (
                "proc_pid_rusage over the whole child process tree, "
                "sampled (reused from wci_profile_rebuild_supervisor_v1); "
                "the lifetime maximum is a high-water mark that never "
                "falls"),
            "swap": {
                "baseline_used_mb": baseline_swap_mb,
                "final_used_mb": final_system.get("swap_used_mb"),
                "max_growth_bytes": max_swap_growth_bytes,
            },
        },
        "processes": {
            "peak_tree_size": peak_tree_size,
            "surviving_children": leftovers,
        },
        "archives": {
            "watched_roots": [str(root) for root in watched],
            "baseline_counts": baseline_archives,
            "final_counts": sup._archive_counts(watched),
        },
    }
    published = common.publish(args.summary, record)
    print(json.dumps({
        "stopped": record["stopped"],
        "stop_reason": stop_reason,
        "exit_code": returncode,
        "wall_s": round(wall, 3),
        "peak_footprint_bytes": peak_footprint,
        "max_swap_growth_bytes": max_swap_growth_bytes,
        "content_key": published["content_key"],
    }, indent=1))
    return 0 if (returncode == 0 and stop_reason is None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
