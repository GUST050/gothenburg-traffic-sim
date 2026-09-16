"""Shared measurement helpers for the 2026-09-16 WindowCostIndex diagnostics.

Measurement only. Nothing here changes a production path.

Two memory quantities are read separately and never substituted for one
another, because they answer different questions:

* ``resident_bytes`` / ``max_rss_bytes`` -- pages of this process currently
  (or at most) in physical RAM. ``max_rss_bytes`` is ``ru_maxrss``, the value
  ``/usr/bin/time -l`` prints as "maximum resident set size".
* ``phys_footprint_bytes`` / ``lifetime_max_phys_footprint_bytes`` -- the
  kernel's footprint ledger, which also counts this process's compressed and
  swapped-out anonymous memory. The lifetime maximum is the value
  ``/usr/bin/time -l`` prints as "peak memory footprint".

A footprint far above the resident size therefore means memory the process
still owns but that is not in RAM. It does not by itself say how much time
was lost to that.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import json
import os
import platform
import re
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[2]

_RUSAGE_INFO_V4 = 4
_V4_FIELDS = (
    "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups",
    "pageins", "wired_size", "resident_size", "phys_footprint",
    "proc_start_abstime", "proc_exit_abstime", "child_user_time",
    "child_system_time", "child_pkg_idle_wkups", "child_interrupt_wkups",
    "child_pageins", "child_elapsed_abstime", "diskio_bytesread",
    "diskio_byteswritten", "cpu_time_qos_default",
    "cpu_time_qos_maintenance", "cpu_time_qos_background",
    "cpu_time_qos_utility", "cpu_time_qos_legacy",
    "cpu_time_qos_user_initiated", "cpu_time_qos_user_interactive",
    "billed_system_time", "serviced_system_time", "logical_writes",
    "lifetime_max_phys_footprint", "instructions", "cycles",
    "billed_energy", "serviced_energy", "interval_max_phys_footprint",
    "runnable_time", "flags",
)


class _RusageInfoV4(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in _V4_FIELDS]


def _libproc():
    path = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
    library = ctypes.CDLL(path, use_errno=True)
    library.proc_pid_rusage.argtypes = (
        ctypes.c_int, ctypes.c_int, ctypes.POINTER(_RusageInfoV4))
    library.proc_pid_rusage.restype = ctypes.c_int
    return library


_LIBPROC = _libproc() if sys.platform == "darwin" else None


def process_memory() -> Dict[str, int]:
    """Current resident size and footprint of THIS process, in bytes."""
    if _LIBPROC is None:
        raise RuntimeError("proc_pid_rusage is only available on macOS")
    info = _RusageInfoV4()
    if _LIBPROC.proc_pid_rusage(os.getpid(), _RUSAGE_INFO_V4,
                                ctypes.byref(info)) != 0:
        raise OSError(ctypes.get_errno(), "proc_pid_rusage failed")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "resident_bytes": int(info.resident_size),
        "phys_footprint_bytes": int(info.phys_footprint),
        "lifetime_max_phys_footprint_bytes": int(
            info.lifetime_max_phys_footprint),
        # ru_maxrss is bytes on macOS (kilobytes on Linux).
        "max_rss_bytes": int(usage.ru_maxrss),
        "pageins": int(info.pageins),
        "major_faults": int(usage.ru_majflt),
        "minor_faults": int(usage.ru_minflt),
    }


def _sysctl(name: str) -> str:
    return subprocess.run(["sysctl", "-n", name], capture_output=True,
                          text=True, check=True).stdout.strip()


def _megabytes(text: str, label: str) -> float:
    match = re.search(label + r"\s*=\s*([0-9.]+)M", text)
    if match is None:
        raise ValueError(f"cannot read {label} from: {text!r}")
    return float(match.group(1))


def system_memory() -> Dict[str, Any]:
    """System-wide swap and compressor state. NOT a per-process quantity."""
    swap = _sysctl("vm.swapusage")
    page_size = int(_sysctl("hw.pagesize"))
    vm_stat = subprocess.run(["vm_stat"], capture_output=True, text=True,
                             check=True).stdout
    pages = {}
    for key, label in (("compressor_occupied", "Pages occupied by compressor"),
                       ("compressor_stored", "Pages stored in compressor"),
                       ("free", "Pages free"),
                       ("swapouts", "Swapouts"),
                       ("swapins", "Swapins")):
        match = re.search(re.escape(label) + r":\s+(\d+)", vm_stat)
        pages[key] = int(match.group(1)) if match else None

    def as_bytes(key: str):
        return pages[key] * page_size if pages[key] is not None else None

    return {
        "scope": "system-wide",
        "swap_used_mb": _megabytes(swap, "used"),
        "swap_total_mb": _megabytes(swap, "total"),
        "page_size_bytes": page_size,
        "compressor_occupied_bytes": as_bytes("compressor_occupied"),
        "compressor_stored_bytes": as_bytes("compressor_stored"),
        "free_bytes": as_bytes("free"),
        "swapouts_total": pages["swapouts"],
        "swapins_total": pages["swapins"],
    }


def cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


class Sampler:
    """Timestamped memory/CPU samples at named points of one process."""

    def __init__(self) -> None:
        self.wall_started = time.perf_counter()
        self.cpu_started = cpu_seconds()
        self.samples: List[Dict[str, Any]] = []

    def sample(self, label: str, **extra: Any) -> Dict[str, Any]:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        record = {
            "label": label,
            "wall_s": time.perf_counter() - self.wall_started,
            "cpu_s": cpu_seconds() - self.cpu_started,
            "user_s": usage.ru_utime,
            "sys_s": usage.ru_stime,
            **process_memory(),
            **extra,
        }
        self.samples.append(record)
        return record


def canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    handle = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            handle.update(block)
    return handle.hexdigest()


def source_bindings(paths: Iterable[str]) -> Dict[str, str]:
    return {path: file_sha256(ROOT / path) for path in paths}


def driver_binding(driver: Path) -> Dict[str, Any]:
    helper = Path(__file__).resolve()
    return {
        "path": str(Path(driver).resolve().relative_to(ROOT)),
        "sha256": file_sha256(Path(driver)),
        "helper_path": str(helper.relative_to(ROOT)),
        "helper_sha256": file_sha256(helper),
    }


def runtime_manifest() -> Dict[str, Any]:
    return {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "hw_memsize_bytes": int(_sysctl("hw.memsize")),
        "hw_ncpu": int(_sysctl("hw.ncpu")),
    }


def git_state() -> Dict[str, Any]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                          capture_output=True, text=True, check=True)
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=str(ROOT), capture_output=True, text=True, check=True)
    return {"head": head.stdout.strip(),
            "tracked_changes": status.stdout.splitlines()}


def publish(path: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    """Append-only write: refuses to replace existing evidence."""
    record = dict(record)
    record["content_key"] = digest(record)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def run_worker(argv: List[str],
               parse: Callable[[str], Any] = json.loads) -> Any:
    """Run one fresh worker process and return its last JSON stdout line."""
    completed = subprocess.run(
        argv, capture_output=True, text=True, cwd=str(ROOT),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if completed.returncode != 0:
        raise RuntimeError(
            f"worker failed ({completed.returncode}):\n"
            f"{completed.stderr[-4000:]}")
    lines = [row for row in completed.stdout.splitlines()
             if row.startswith("{")]
    if not lines:
        raise RuntimeError(
            f"worker printed no JSON:\n{completed.stderr[-4000:]}")
    return parse(lines[-1])
