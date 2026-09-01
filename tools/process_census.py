"""One trustworthy answer to "which processes are alive, and how big are they".

The RSS and reaping gates in the bounded-SUMO benchmark and the cold ledger
profile both rest on a census of a process GROUP: sum the resident memory of
everything alive in the group at one instant, and confirm the group is really
empty before calling a campaign reaped. Getting that census is the whole job of
this module, and it exists because the obvious way to get it does not work
where the measurement actually runs.

WHY NOT `ps`. `/bin/ps` on macOS is setuid root (`-rwsr-xr-x root wheel`), and a
Seatbelt-sandboxed process may not exec a setuid binary — the kernel refuses
with EPERM before `ps` runs at all. Every autonomous run of this project's
workflow executes inside exactly such a sandbox, so `subprocess.run(["ps", ...])`
raised `PermissionError: [Errno 1] Operation not permitted: 'ps'` on every
attempt, and Phases 3 and 4 could only ever publish
`INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE`. The measurements themselves were
fine; the census tool was unreachable. Reproduced directly under a Seatbelt
profile matching the sandbox's own (`process-exec` allowed, `process-info*`
allowed for same-sandbox targets): `ps` EPERMs while the libproc path below
returns the full group, including a live child's resident size.

WHAT REPLACES IT. macOS exposes the same kernel data through `libproc`
(`proc_listpids` + `proc_pidinfo`), which is an ordinary unprivileged syscall
interface: no setuid binary, no subprocess, no text parsing. It is strictly
closer to the kernel than parsing `ps` output was, so this is not a weakened
measurement standing in for a blocked one — it is the same number obtained more
directly. `tests/test_process_census.py` pins that the two agree, process for
process, wherever both are available.

THE TRUST CONTRACT IS UNCHANGED, and it is the reason this module raises rather
than returns. An unavailable census read as "zero processes" is exactly how a
surviving SUMO process passes a reap gate, or an unmeasured peak passes an 8 GiB
memory gate. So every failure path here raises `ProcessCensusUnavailable`, and
a mechanism that cannot see the CALLER'S OWN process is rejected as untrusted
before it can be believed about anything else — a census blind to the one
process it is guaranteed to contain cannot be trusted to see a sibling.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
import sys

__all__ = [
    "ProcessCensusUnavailable",
    "process_group_snapshot",
    "census_mechanism",
    "census_mechanism_names",
    "descendant_process_groups",
]


class ProcessCensusUnavailable(RuntimeError):
    """No mechanism could be trusted to enumerate live processes right now.

    Callers whose reaped-process or peak-RSS evidence depends on the census
    must treat this as UNKNOWN, never as "zero processes"/"zero bytes".
    """


# libproc constants (`<libproc.h>`, `<sys/proc_info.h>`).
_PROC_ALL_PIDS = 1
_PROC_PIDTBSDINFO = 3
_PROC_PIDTASKINFO = 4


class _ProcBSDInfo(ctypes.Structure):
    """`struct proc_bsdinfo` — 136 bytes; the size IS the validity check."""

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32), ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32), ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32), ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32), ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


class _ProcTaskInfo(ctypes.Structure):
    """`struct proc_taskinfo` — 96 bytes; `pti_resident_size` is in BYTES."""

    _fields_ = [
        ("pti_virtual_size", ctypes.c_uint64),
        ("pti_resident_size", ctypes.c_uint64),
        ("pti_total_user", ctypes.c_uint64),
        ("pti_total_system", ctypes.c_uint64),
        ("pti_threads_user", ctypes.c_uint64),
        ("pti_threads_system", ctypes.c_uint64),
        ("pti_policy", ctypes.c_int32), ("pti_faults", ctypes.c_int32),
        ("pti_pageins", ctypes.c_int32), ("pti_cow_faults", ctypes.c_int32),
        ("pti_messages_sent", ctypes.c_int32),
        ("pti_messages_received", ctypes.c_int32),
        ("pti_syscalls_mach", ctypes.c_int32),
        ("pti_syscalls_unix", ctypes.c_int32),
        ("pti_csw", ctypes.c_int32), ("pti_threadnum", ctypes.c_int32),
        ("pti_numrunning", ctypes.c_int32), ("pti_priority", ctypes.c_int32),
    ]


def _libproc() -> ctypes.CDLL:
    library = ctypes.CDLL(
        ctypes.util.find_library("c") or "/usr/lib/libSystem.dylib",
        use_errno=True)
    library.proc_listpids.restype = ctypes.c_int
    library.proc_pidinfo.restype = ctypes.c_int
    return library


def _libproc_snapshot() -> list[tuple[int, int, int]]:
    """`(pid, pgid, rss_kib)` for every process this caller may inspect.

    Processes owned by ANOTHER user are skipped: `proc_pidinfo` refuses them,
    and they cannot be members of this process's group anyway, which is the
    only thing the callers filter on. A truncated pid list is treated as a
    failure rather than a short answer — a census that silently lost rows is
    the exact failure mode the trust contract exists to prevent.
    """
    library = _libproc()
    needed = library.proc_listpids(_PROC_ALL_PIDS, 0, None, 0)
    if needed <= 0:
        raise OSError(ctypes.get_errno(), "proc_listpids reported no size")
    for headroom in (256, 4096):
        capacity = needed // ctypes.sizeof(ctypes.c_int32) + headroom
        buffer = (ctypes.c_int32 * capacity)()
        written = library.proc_listpids(
            _PROC_ALL_PIDS, 0, ctypes.byref(buffer), ctypes.sizeof(buffer))
        if written <= 0:
            raise OSError(ctypes.get_errno(), "proc_listpids failed")
        if written < ctypes.sizeof(buffer):
            break
    else:
        raise OSError("proc_listpids filled every buffer offered; the pid "
                      "list may have been truncated")
    rows: list[tuple[int, int, int]] = []
    for pid in buffer[:written // ctypes.sizeof(ctypes.c_int32)]:
        if pid <= 0:
            continue
        bsd_info = _ProcBSDInfo()
        if library.proc_pidinfo(
                pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(bsd_info),
                ctypes.sizeof(bsd_info)) != ctypes.sizeof(bsd_info):
            continue  # another user's process, or exited mid-census
        task_info = _ProcTaskInfo()
        if library.proc_pidinfo(
                pid, _PROC_PIDTASKINFO, 0, ctypes.byref(task_info),
                ctypes.sizeof(task_info)) != ctypes.sizeof(task_info):
            # A process may disappear between the two proc_pidinfo calls. That
            # race is harmless, but a still-visible process whose RSS cannot be
            # read must not be represented as zero: doing so would silently
            # weaken both the peak-memory and reap evidence. Recheck identity
            # to distinguish those cases and fail closed for the latter.
            still_live = _ProcBSDInfo()
            if library.proc_pidinfo(
                    pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(still_live),
                    ctypes.sizeof(still_live)) == ctypes.sizeof(still_live):
                raise OSError(
                    ctypes.get_errno(),
                    f"proc_pidinfo could not read resident size for live "
                    f"pid {pid}")
            continue
        resident_bytes = int(task_info.pti_resident_size)
        rows.append((int(bsd_info.pbi_pid), int(bsd_info.pbi_pgid),
                     resident_bytes // 1024))
    return rows


def _procfs_snapshot() -> list[tuple[int, int, int]]:
    """`(pid, pgid, rss_kib)` from `/proc/<pid>/stat` — the Linux path."""
    page_kib = os.sysconf("SC_PAGE_SIZE") // 1024
    rows: list[tuple[int, int, int]] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8") as handle:
                stat = handle.read()
        except OSError:
            continue  # exited mid-census
        # comm can contain spaces and parentheses; everything after the last
        # ')' is positional, starting at field 3 (state).
        tail = stat.rpartition(")")[2].split()
        if len(tail) < 22:
            continue
        rows.append((int(entry), int(tail[2]), int(tail[21]) * page_kib))
    return rows


def _ps_snapshot() -> list[tuple[int, int, int]]:
    """`(pid, pgid, rss_kib)` via `ps` — kept as the last resort.

    `-eo pid=,pgid=,rss=` is the one invocation BSD `ps` (macOS) and GNU `ps`
    (Linux) agree on unambiguously. Unusable inside a Seatbelt sandbox, which
    is why it is no longer first.
    """
    completed = subprocess.run(
        ["ps", "-eo", "pid=,pgid=,rss="],
        capture_output=True, text=True, timeout=5)
    if completed.returncode != 0:
        raise OSError(f"`ps` exited {completed.returncode}: "
                      f"{completed.stderr.strip()}")
    rows = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            rows.append((int(parts[0]), int(parts[1]), int(parts[2])))
    return rows


def _libproc_relations_snapshot() -> list[tuple[int, int, int]]:
    """Return ``(pid, ppid, pgid)`` without executing a process utility."""
    library = _libproc()
    needed = library.proc_listpids(_PROC_ALL_PIDS, 0, None, 0)
    if needed <= 0:
        raise OSError(ctypes.get_errno(), "proc_listpids reported no size")
    capacity = needed // ctypes.sizeof(ctypes.c_int32) + 4096
    buffer = (ctypes.c_int32 * capacity)()
    written = library.proc_listpids(
        _PROC_ALL_PIDS, 0, ctypes.byref(buffer), ctypes.sizeof(buffer))
    if written <= 0 or written >= ctypes.sizeof(buffer):
        raise OSError(ctypes.get_errno(), "proc_listpids relation census failed")
    rows = []
    for pid in buffer[:written // ctypes.sizeof(ctypes.c_int32)]:
        if pid <= 0:
            continue
        info = _ProcBSDInfo()
        if library.proc_pidinfo(
                pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info),
                ctypes.sizeof(info)) == ctypes.sizeof(info):
            rows.append((int(info.pbi_pid), int(info.pbi_ppid),
                         int(info.pbi_pgid)))
    return rows


def _procfs_relations_snapshot() -> list[tuple[int, int, int]]:
    rows = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8") as handle:
                stat = handle.read()
        except OSError:
            continue
        tail = stat.rpartition(")")[2].split()
        if len(tail) >= 3:
            rows.append((int(entry), int(tail[1]), int(tail[2])))
    return rows


def _relation_mechanisms():
    if sys.platform == "darwin":
        return (("libproc(proc_pidinfo parent/group)",
                 _libproc_relations_snapshot),)
    return (("procfs(/proc/<pid>/stat parent/group)",
             _procfs_relations_snapshot),)


def descendant_process_groups(root_pid: int) -> set[int]:
    """Process groups currently descended from *root_pid*, excluding its own.

    The snapshot is taken while the owning process still exists.  It lets an
    orchestrator stop tool commands that deliberately created new sessions;
    killing only the root's process group cannot reach those descendants.
    """
    if isinstance(root_pid, bool) or int(root_pid) <= 0:
        raise ValueError("root pid must be positive")
    failures = []
    for name, mechanism in _relation_mechanisms():
        try:
            rows = mechanism()
        except (OSError, ValueError) as error:
            failures.append(f"{name}: {error}")
            continue
        by_pid = {pid: (ppid, pgid) for pid, ppid, pgid in rows}
        root = by_pid.get(int(root_pid))
        if root is None:
            failures.append(f"{name}: root pid {root_pid} is not visible")
            continue
        root_pgid = root[1]
        descendants = {int(root_pid)}
        changed = True
        while changed:
            changed = False
            for pid, (ppid, _pgid) in by_pid.items():
                if ppid in descendants and pid not in descendants:
                    descendants.add(pid)
                    changed = True
        return {
            by_pid[pid][1]
            for pid in descendants - {int(root_pid)}
            if by_pid[pid][1] > 0 and by_pid[pid][1] != root_pgid
        }
    raise ProcessCensusUnavailable(
        "no process relation census is available: " + "; ".join(failures))


def _mechanisms():
    """The census mechanisms to try, best-first for this platform."""
    if sys.platform == "darwin":
        return (("libproc(proc_listpids+proc_pidinfo)", _libproc_snapshot),
                ("ps(-eo pid=,pgid=,rss=)", _ps_snapshot))
    return (("procfs(/proc/<pid>/stat)", _procfs_snapshot),
            ("ps(-eo pid=,pgid=,rss=)", _ps_snapshot))


def _self_visibility_failure(
        rows: list[tuple[int, int, int]]) -> str | None:
    """Explain why *rows* cannot be trusted, or return ``None``.

    Seeing the caller's PID is necessary but not sufficient for resource
    evidence: the row must also bind it to the known process group and report
    a positive resident size. This prevents a partial mechanism from passing
    the trust check with a fabricated zero-RSS self row.
    """
    own_pid = os.getpid()
    own_pgid = os.getpgrp()
    own_rows = [(pgid, rss_kib) for pid, pgid, rss_kib in rows
                if pid == own_pid]
    if not own_rows:
        return (f"census did not contain the calling process ({own_pid}), "
                "so it cannot be trusted about any other")
    if not any(pgid == own_pgid for pgid, _ in own_rows):
        return (f"calling process {own_pid} had the wrong process group; "
                f"expected {own_pgid}")
    if not any(pgid == own_pgid and rss_kib > 0
               for pgid, rss_kib in own_rows):
        return (f"calling process {own_pid} had a non-positive resident "
                "size, so resource evidence cannot trust this census")
    return None


def process_group_snapshot() -> list[tuple[int, int, int]]:
    """`(pid, pgid, rss_kib)` for every visible process, or raise.

    Tries each platform mechanism in turn and returns the first result that
    passes the self-visibility check. Raises `ProcessCensusUnavailable`
    listing every mechanism's reason when none does — never an empty list,
    which a caller could not tell apart from a genuinely empty system.
    """
    failures = []
    for name, mechanism in _mechanisms():
        try:
            rows = mechanism()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            failures.append(f"{name}: {error}")
            continue
        trust_failure = _self_visibility_failure(rows)
        if trust_failure is not None:
            failures.append(f"{name}: {trust_failure}")
            continue
        return rows
    raise ProcessCensusUnavailable(
        "no process census mechanism is available: " + "; ".join(failures))


def census_mechanism_names() -> list[str]:
    """The mechanisms this platform would try, best-first.

    Declared without running a census, so an evidence record can state what
    was ATTEMPTED even on the path where every attempt failed.
    """
    return [name for name, _ in _mechanisms()]


def census_mechanism() -> str:
    """Name of the mechanism a census would use now, for evidence records.

    Runs a real census, so it reports what actually WORKS rather than what
    the platform is expected to support. Raises `ProcessCensusUnavailable`
    for the same reasons `process_group_snapshot` does.
    """
    failures = []
    for name, mechanism in _mechanisms():
        try:
            rows = mechanism()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            failures.append(f"{name}: {error}")
            continue
        trust_failure = _self_visibility_failure(rows)
        if trust_failure is None:
            return name
        failures.append(f"{name}: {trust_failure}")
    raise ProcessCensusUnavailable(
        "no process census mechanism is available: " + "; ".join(failures))
