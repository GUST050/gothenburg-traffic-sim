#!/usr/bin/env python3
"""Run the real month-ledger profile under the approved budget, and watch it.

Two roles in one file so the watcher and the watched cannot drift apart.

``--role run`` executes ``tools/profile_monthly_cost_ledger.py`` unchanged,
after installing two MEASUREMENT-ONLY spies: one counts calls to the real
``parse_route_vehicles``, the other counts ``ArchiveDisruptionProvider``
constructions and keeps a weakref to each so liveness can be read without
holding the provider alive. Neither spy changes an argument, a return value
or an order, so the ledger it produces is the ledger the unpatched tool would
produce. They exist because the profile record carries phase WALL TIMES but no
call counts, and the acceptance conditions are stated in counts.

``--role supervise`` starts that child in its own process group and samples it
until it exits or breaches a limit. It aborts fail-closed on wall, physical
footprint, swap growth, a forbidden child process, or a new demand archive. It
never repairs, retries or relaxes anything.

Append-only: every output path must not already exist.
"""
from __future__ import annotations

import argparse
import ctypes
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
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_profile_rebuild_supervision_v1"
SAMPLE_SECONDS = 5.0
GIB = 1024 ** 3

# A SUMO or demand process appearing under the profile is an abort, not a
# warning: the profile's whole claim is that it costs a month without one.
FORBIDDEN_COMMANDS = (
    "sumo", "sumo-gui", "duarouter", "netconvert", "marouter", "jtrrouter",
    "polyconvert", "od2trips", "activitygen",
)


# --------------------------------------------------------------------------
# measurement helpers


class _RusageInfoV4(ctypes.Structure):
    _fields_ = [
        ("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user_time", "system_time", "pkg_idle_wkups",
            "interrupt_wkups", "pageins", "wired_size",
            "resident_size", "phys_footprint", "proc_start_abstime",
            "proc_exit_abstime", "child_user_time",
            "child_system_time", "child_pkg_idle_wkups",
            "child_interrupt_wkups", "child_pageins",
            "child_elapsed_abstime", "diskio_bytesread",
            "diskio_byteswritten", "cpu_time_qos_default",
            "cpu_time_qos_maintenance", "cpu_time_qos_background",
            "cpu_time_qos_utility", "cpu_time_qos_legacy",
            "cpu_time_qos_user_initiated",
            "cpu_time_qos_user_interactive",
            "billed_system_time", "serviced_system_time",
            "logical_writes", "lifetime_max_phys_footprint",
            "instructions", "cycles", "billed_energy",
            "serviced_energy", "interval_max_phys_footprint")] + [
        ("pad", ctypes.c_uint64 * 32)]


def _libproc():
    library = ctypes.CDLL("libproc.dylib", use_errno=True)
    library.proc_pid_rusage.argtypes = (
        ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
    library.proc_pid_rusage.restype = ctypes.c_int
    return library


_LIBPROC = _libproc() if sys.platform == "darwin" else None


class _MachTimebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


def _mach_ticks_to_seconds():
    """`proc_pid_rusage` reports CPU in mach absolute-time units, not ns.

    Reading them as nanoseconds understates CPU by the timebase ratio — on
    Apple silicon 125/3, about 42x — which is how this was caught: the
    converted value disagreed with `getrusage` for the same process.
    """
    if sys.platform != "darwin":
        return lambda ticks: float(ticks)
    timebase = _MachTimebase()
    ctypes.CDLL("libSystem.dylib").mach_timebase_info(ctypes.byref(timebase))
    numer, denom = int(timebase.numer), int(timebase.denom)
    return lambda ticks: ticks * numer / denom / 1e9


_TICKS_TO_SECONDS = _mach_ticks_to_seconds()


def _process_footprint(pid: int) -> Optional[Dict[str, int]]:
    """Footprint of ONE pid, or None when it is gone or not ours."""
    if _LIBPROC is None:
        return None
    info = _RusageInfoV4()
    if _LIBPROC.proc_pid_rusage(
            pid, 4, ctypes.cast(ctypes.byref(info),
                                ctypes.POINTER(ctypes.c_void_p))) != 0:
        return None
    return {
        "phys_footprint_bytes": int(info.phys_footprint),
        "lifetime_max_phys_footprint_bytes": int(
            info.lifetime_max_phys_footprint),
        "resident_size_bytes": int(info.resident_size),
        "cpu_s": _TICKS_TO_SECONDS(
            int(info.user_time) + int(info.system_time)),
    }


def _process_table() -> List[Dict[str, Any]]:
    """Every process as (pid, ppid, comm), skipping the probe's own tools."""
    result = subprocess.run(
        ["ps", "-Ao", "pid=,ppid=,comm="], capture_output=True, text=True,
        check=True)
    rows: List[Dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        comm = parts[2].strip()
        if Path(comm).name in ("ps", "sysctl", "vm_stat"):
            continue
        rows.append({"pid": pid, "ppid": ppid, "comm": comm})
    return rows


def _descendants(root_pid: int,
                 table: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_parent: Dict[int, List[Dict[str, Any]]] = {}
    for row in table:
        by_parent.setdefault(row["ppid"], []).append(row)
    out: List[Dict[str, Any]] = []
    frontier = [root_pid]
    seen = {root_pid}
    while frontier:
        pid = frontier.pop()
        for child in by_parent.get(pid, ()):
            if child["pid"] in seen:
                continue
            seen.add(child["pid"])
            out.append(child)
            frontier.append(child["pid"])
    return out


def _forbidden(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{**row, "why": "forbidden command"}
            for row in rows
            if Path(row["comm"]).name in FORBIDDEN_COMMANDS]


def _archive_counts(roots: Sequence[Path]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for root in roots:
        try:
            counts[str(root)] = sum(
                1 for entry in Path(root).iterdir()
                if entry.is_dir() and entry.name.startswith("demand-"))
        except OSError:
            counts[str(root)] = -1
    return counts


# --------------------------------------------------------------------------
# role: run


def _role_run(inner_out: Path, argv: Sequence[str]) -> int:
    """Execute the profiler unchanged, with counting spies attached."""
    import weakref

    from traffic_sim.simulation import deterministic_disruption as dd
    from traffic_sim.simulation import disruption

    parse_calls: List[Dict[str, Any]] = []
    provider_records: List[Dict[str, Any]] = []
    provider_refs: List[Any] = []

    # Patch the DEFINING module, not the importing one: `_vehicles` does a
    # fresh ``from traffic_sim.simulation.disruption import
    # parse_route_vehicles`` on every call, so the importing module never
    # holds an attribute to replace. Counting `_vehicles` calls instead of
    # parses would also overcount, because a reusing provider returns its
    # memo without parsing.
    real_parse = disruption.parse_route_vehicles

    def counting_parse(path, *args, **kwargs):
        parse_calls.append({"path": str(path), "at_s": time.time()})
        return real_parse(path, *args, **kwargs)

    disruption.parse_route_vehicles = counting_parse

    real_init = dd.ArchiveDisruptionProvider.__init__

    def counting_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        # How many providers built BEFORE this one are still alive? With one
        # provider per archive scope, every provider must open onto zero.
        alive_now = sum(1 for ref in provider_refs if ref() is not None)
        provider_records.append({
            "index": len(provider_records),
            "alive_when_opened": alive_now,
            "reuse_parsed_routes": bool(
                getattr(self, "_reuse_parsed_routes", False)),
            "parses_so_far": len(parse_calls),
            "at_s": time.time(),
        })
        provider_refs.append(weakref.ref(self))

    dd.ArchiveDisruptionProvider.__init__ = counting_init

    # A supervised abort arrives as SIGTERM, whose default action ends the
    # process without unwinding — so the counts measured up to that moment
    # were lost, which is exactly when they are most worth having. Turn it
    # into an exception so the `finally` below still writes them.
    def _terminate(signum, _frame):
        raise SystemExit(f"terminated by signal {signum}")

    signal.signal(signal.SIGTERM, _terminate)

    from tools import profile_monthly_cost_ledger as profiler

    status = 1
    error = None
    try:
        status = profiler.main(list(argv))
    except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        alive_after = sum(1 for ref in provider_refs if ref() is not None)
        inner_out.write_text(json.dumps({
            "schema": "wci_profile_rebuild_instrumentation_v1",
            "exit_status": status,
            "error": error,
            "xml_parses": len(parse_calls),
            "distinct_parsed_paths": len({
                call["path"] for call in parse_calls}),
            "providers_created": len(provider_records),
            "providers": provider_records,
            "alive_when_each_provider_opened": [
                record["alive_when_opened"] for record in provider_records],
            "providers_alive_after_the_run": alive_after,
            "reuse_parsed_routes_every_provider": (
                all(record["reuse_parsed_routes"]
                    for record in provider_records)
                if provider_records else None),
        }, indent=1, sort_keys=True), encoding="utf-8")
    return status


# --------------------------------------------------------------------------
# role: supervise


def _role_supervise(args: argparse.Namespace, argv: Sequence[str]) -> int:
    watched = [Path(root) for root in (args.watch_archive_root or [])]
    baseline_archives = _archive_counts(watched)
    baseline_system = common.system_memory()
    baseline_swap_mb = baseline_system.get("swap_used_mb")

    child_log = args.child_log.open("w", encoding="utf-8")
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()),
         "--role", "run", "--inner-out", str(args.inner_out), "--"]
        + list(argv),
        cwd=str(ROOT), start_new_session=True,
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
            time.sleep(SAMPLE_SECONDS)
            wall = time.time() - started

            table = _process_table()
            tree = ([{"pid": child.pid, "ppid": os.getpid(),
                      "comm": "profile"}]
                    + _descendants(child.pid, table))
            peak_tree_size = max(peak_tree_size, len(tree))

            footprint = 0
            lifetime = 0
            cpu_s = 0.0
            for row in tree:
                memory = _process_footprint(row["pid"])
                if memory is None:
                    continue
                footprint += memory["phys_footprint_bytes"]
                lifetime += memory["lifetime_max_phys_footprint_bytes"]
                cpu_s += memory["cpu_s"]
            peak_footprint = max(peak_footprint, footprint)
            peak_lifetime = max(peak_lifetime, lifetime)
            # CPU only ever rises for a live tree, but a child that exits
            # between samples takes its total with it, so keep the maximum
            # rather than the last reading.
            peak_cpu_s = max(peak_cpu_s, cpu_s)

            system = common.system_memory()
            swap_mb = system.get("swap_used_mb")
            swap_growth = None
            if swap_mb is not None and baseline_swap_mb is not None:
                swap_growth = int((swap_mb - baseline_swap_mb) * 1024 * 1024)
                max_swap_growth_bytes = max(max_swap_growth_bytes,
                                            swap_growth)

            hits = _forbidden(tree)
            archives = _archive_counts(watched)
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
    leftovers = _descendants(child.pid, _process_table())

    inner: Optional[Dict[str, Any]] = None
    if args.inner_out.is_file():
        try:
            inner = json.loads(args.inner_out.read_text(encoding="utf-8"))
        except ValueError:
            inner = None

    record = {
        "schema": SCHEMA,
        "diagnostic_only": False,
        "release_evidence": False,
        "claim_boundary": (
            "supervision telemetry for one real month-ledger profile run. "
            "The profile's own published record is the result; this record "
            "is how the run was bounded and what it was observed to do"),
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "command": list(argv),
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
        "sample_interval_s": SAMPLE_SECONDS,
        "memory": {
            "peak_tree_phys_footprint_bytes": peak_footprint,
            "peak_tree_lifetime_max_phys_footprint_bytes": peak_lifetime,
            "instrument": (
                "proc_pid_rusage over the whole child process tree, sampled; "
                "the lifetime maximum is a high-water mark that never falls"),
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
            "final_counts": _archive_counts(watched),
        },
        "instrumentation": inner,
    }
    published = common.publish(args.summary, record)
    print(json.dumps({
        "stopped": record["stopped"],
        "stop_reason": stop_reason,
        "exit_code": returncode,
        "wall_s": round(wall, 3),
        "peak_footprint_bytes": peak_footprint,
        "max_swap_growth_bytes": max_swap_growth_bytes,
        "xml_parses": (inner or {}).get("xml_parses"),
        "providers_created": (inner or {}).get("providers_created"),
        "providers_alive_after_the_run": (
            inner or {}).get("providers_alive_after_the_run"),
        "content_key": published["content_key"],
    }, indent=1))
    return 0 if (returncode == 0 and stop_reason is None) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", choices=("supervise", "run"), required=True)
    parser.add_argument("--inner-out", type=Path, required=True)
    parser.add_argument("--sample-log", type=Path)
    parser.add_argument("--child-log", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--wall-limit-s", type=float, default=11210.0)
    parser.add_argument("--memory-limit-bytes", type=int, default=4 * GIB)
    parser.add_argument("--swap-growth-limit-bytes", type=int, default=GIB)
    parser.add_argument("--watch-archive-root", action="append")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    rest = list(args.rest)
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        raise SystemExit("no profiler command given after --")

    if args.role == "run":
        return _role_run(args.inner_out, rest)

    for required in ("sample_log", "child_log", "summary"):
        if getattr(args, required) is None:
            raise SystemExit(f"--{required.replace('_', '-')} is required")
    for path in (args.inner_out, args.sample_log, args.child_log,
                 args.summary):
        if path.exists():
            raise SystemExit(f"append-only output already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    return _role_supervise(args, rest)


if __name__ == "__main__":
    raise SystemExit(main())
