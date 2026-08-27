"""The queue benchmark must not publish a number a failed run produced.

Two separate defects motivated these: the real arm owned no process group, so
a timeout killed the parent and left isolated workers and their SUMO children
running (the frozen 2026-08-27 report records exactly that), and a speed claim
was permitted on cache-fingerprint equality alone, which two arms that both
crashed early satisfy perfectly.
"""

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "benchmark_independent_daily_queue",
    ROOT / "tools" / "benchmark_independent_daily_queue.py",
)
benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(benchmark)


def _arm(name, **overrides):
    arm = {
        "arm": name,
        "cache_fingerprint": {"k": "d"},
        "returncode": 0,
        "timed_out": False,
        "sumo_samples": 12,
        "partial_files": [],
    }
    arm.update(overrides)
    return arm


def test_a_healthy_pair_of_arms_may_carry_a_speed_number():
    identical, entries, blockers = benchmark.evaluate_speed_claim(
        [_arm("legacy_parent_local"), _arm("global_queue_w8")], mode="real"
    )
    assert identical and entries == [1, 1] and blockers == []


def test_two_arms_that_published_nothing_are_not_a_speed_measurement():
    """Empty caches compare equal, which is exactly the trap."""
    identical, _entries, blockers = benchmark.evaluate_speed_claim(
        [_arm("a", cache_fingerprint={}), _arm("b", cache_fingerprint={})],
        mode="real",
    )
    assert identical
    assert any("published no cache entries" in item for item in blockers)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"returncode": 1}, "exited 1"),
        ({"timed_out": True}, "timed out"),
        ({"sumo_samples": 0}, "no concurrency samples"),
        ({"partial_files": ["x.tmp"]}, "partial cache files"),
    ],
)
def test_a_broken_arm_blocks_the_speed_claim(overrides, expected):
    _identical, _entries, blockers = benchmark.evaluate_speed_claim(
        [_arm("legacy_parent_local"), _arm("global_queue_w8", **overrides)],
        mode="real",
    )
    assert any(expected in item for item in blockers), blockers


def test_an_arm_missing_evidence_blocks_the_speed_claim():
    _identical, _entries, blockers = benchmark.evaluate_speed_claim(
        [
            _arm("legacy_parent_local", cache_fingerprint={"a": "1", "b": "2"}),
            _arm("global_queue_w8", cache_fingerprint={"a": "1"}),
        ],
        mode="real",
    )
    assert any("missing evidence" in item for item in blockers), blockers


def _group_members(pgid):
    """Every process-table entry in ``pgid``, split live vs already dead.

    The test file reads the table itself rather than reusing the module under
    test, so a bug in that module's own census cannot make its tests agree
    with it.
    """
    listing = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,state="], capture_output=True, text=True, timeout=10
    ).stdout
    live, zombies = [], []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[1].isdigit() or int(parts[1]) != pgid:
            continue
        (zombies if parts[2][:1].upper() == "Z" else live).append(int(parts[0]))
    return live, zombies


def _spawn_parent_with_child():
    """A short-lived leader in its own session that forks one sleeping child."""
    script = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "time.sleep(120)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    pgid = os.getpgid(process.pid)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if len(_group_members(pgid)[0]) >= 2:
            break
        time.sleep(0.2)
    assert len(_group_members(pgid)[0]) >= 2, "child never started"
    return process, pgid


def _hard_kill(pgid):
    for pid in _group_members(pgid)[0]:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_the_whole_process_group_is_reaped_not_just_the_parent():
    """A parent-only kill is what orphaned SUMO children before."""
    process, pgid = _spawn_parent_with_child()
    try:
        assert benchmark.terminate_process_group(
            process, term_grace_s=10.0, kill_grace_s=10.0
        )
        live, _zombies = _group_members(pgid)
        # A lingering zombie belongs to the platform reaper and cannot run
        # another instruction; a LIVE member is the leak this guards against.
        assert live == [], live
    finally:
        _hard_kill(pgid)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_the_shutdown_reaps_the_leader_itself_and_says_so():
    """The leader must be waited on HERE, not left for a later caller.

    An unreaped child is a zombie, a zombie still reports its process group,
    so a shutdown that inspected the table without reaping could never
    observe its own success no matter how completely the group had died.
    """
    process, pgid = _spawn_parent_with_child()
    try:
        assert benchmark.terminate_process_group(
            process, term_grace_s=10.0, kill_grace_s=10.0
        )
        # No wait() call of our own: the returncode can only be set if
        # terminate_process_group reaped the leader.
        assert process.returncode is not None
        assert process.pid not in _group_members(pgid)[0]
    finally:
        _hard_kill(pgid)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_the_census_reports_an_unreaped_exit_as_a_zombie_not_as_alive():
    """A real, deterministic zombie: a child that exits and is never waited on."""
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    pgid = os.getpgid(process.pid)
    try:
        deadline = time.monotonic() + 20
        census = None
        while time.monotonic() < deadline:
            census = benchmark.inspect_process_group(pgid)
            if census is not None and process.pid in census.zombies:
                break
            time.sleep(0.1)
        assert census is not None
        assert census.zombies == (process.pid,), census
        assert census.live == (), census
    finally:
        process.wait(timeout=10)


class _FakeProcess:
    """A stand-in leader: no real pid is ever signalled through it."""

    def __init__(self, pid, reaped):
        self.pid = pid
        self.returncode = 0 if reaped else None

    def poll(self):
        return self.returncode


@pytest.fixture
def unsignalled(monkeypatch):
    """Record signals instead of delivering them, so units stay harmless."""
    sent = []
    monkeypatch.setattr(benchmark.os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
    return sent


_FAKE_PGID = 987654321


def test_a_dead_group_of_zombies_is_a_success_not_a_leak(monkeypatch, unsignalled):
    monkeypatch.setattr(
        benchmark, "inspect_process_group",
        lambda _pgid: benchmark.GroupCensus(live=(), zombies=(4242,)),
    )

    assert benchmark.terminate_process_group(
        _FakeProcess(4242, reaped=True), pgid=_FAKE_PGID,
        term_grace_s=0.05, kill_grace_s=0.05,
        poll_interval_s=0.01, zombie_settle_s=0.05,
    )
    # Nothing was signalled: the group was inspected BEFORE escalating, and a
    # reaped group of zombies has nobody left to send a signal to.
    assert unsignalled == []


def test_a_surviving_live_member_denies_the_success_claim(monkeypatch, unsignalled):
    monkeypatch.setattr(
        benchmark, "inspect_process_group",
        lambda _pgid: benchmark.GroupCensus(live=(4243,), zombies=()),
    )

    assert not benchmark.terminate_process_group(
        _FakeProcess(4242, reaped=True), pgid=_FAKE_PGID,
        term_grace_s=0.05, kill_grace_s=0.05, poll_interval_s=0.01,
    )
    # Escalated all the way to KILL rather than giving up after TERM.
    assert [sig for _pgid, sig in unsignalled] == [signal.SIGTERM, signal.SIGKILL]


def test_an_unreaped_leader_denies_the_success_claim(monkeypatch, unsignalled):
    """Nothing live left, but the leader was never waited on: not done."""
    monkeypatch.setattr(
        benchmark, "inspect_process_group",
        lambda _pgid: benchmark.GroupCensus(live=(), zombies=()),
    )

    assert not benchmark.terminate_process_group(
        _FakeProcess(4242, reaped=False), pgid=_FAKE_PGID,
        term_grace_s=0.05, kill_grace_s=0.05, poll_interval_s=0.01,
    )


def test_an_unreadable_process_table_is_unknown_never_success(monkeypatch, unsignalled):
    """Unknown is not gone; the frozen report's whole failure was claiming otherwise."""
    monkeypatch.setattr(benchmark, "inspect_process_group", lambda _pgid: None)

    assert not benchmark.terminate_process_group(
        _FakeProcess(4242, reaped=True), pgid=_FAKE_PGID,
        term_grace_s=0.05, kill_grace_s=0.05, poll_interval_s=0.01,
    )


@pytest.mark.parametrize(
    "listing", ["not a table at all\n", "1 2\n", "abc def ghi\n"]
)
def test_an_unparseable_process_table_is_reported_as_untrusted(monkeypatch, listing):
    monkeypatch.setattr(
        benchmark.subprocess, "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, listing, ""),
    )

    assert benchmark.inspect_process_group(_FAKE_PGID) is None


def test_a_failing_ps_is_reported_as_untrusted(monkeypatch):
    monkeypatch.setattr(
        benchmark.subprocess, "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", "ps: boom"),
    )

    assert benchmark.inspect_process_group(_FAKE_PGID) is None


def _pgid_lookup_unavailable(monkeypatch, own_group=111111):
    """Make os.getpgid fail for real pids while our own group stays readable."""

    def getpgid(pid):
        if pid == 0:
            return own_group
        raise ProcessLookupError(pid)

    monkeypatch.setattr(benchmark.os, "getpgid", getpgid)


def test_a_reaped_leader_is_not_a_reaped_group_when_a_descendant_survives(
    monkeypatch, unsignalled
):
    """Reaping the leader answers a different question than reaping the group.

    Descendants keep the process group after their leader exits, so an
    unavailable group lookup must never fall back on `poll()` alone - that
    would report a clean reaping over a live child.
    """
    _pgid_lookup_unavailable(monkeypatch)
    monkeypatch.setattr(
        benchmark, "inspect_process_group",
        lambda _pgid: benchmark.GroupCensus(live=(4243,), zombies=()),
    )

    assert not benchmark.terminate_process_group(
        _FakeProcess(4242, reaped=True),
        term_grace_s=0.05, kill_grace_s=0.05, poll_interval_s=0.01,
    )
    # It still addressed the RIGHT group: start_new_session makes the leader's
    # pid its own group id, so the identity never had to be looked up.
    assert [pgid for pgid, _signal in unsignalled] == [4242, 4242]


def test_the_owned_group_is_the_leader_pid_and_needs_no_lookup(monkeypatch):
    _pgid_lookup_unavailable(monkeypatch)

    assert benchmark.owned_process_group(_FakeProcess(4242, reaped=False)) == 4242


def test_a_process_that_does_not_lead_its_own_group_is_refused(monkeypatch):
    """Without start_new_session the pid is NOT the group id; do not guess."""
    monkeypatch.setattr(benchmark.os, "getpgid", lambda _pid: 7)

    with pytest.raises(ValueError, match="start_new_session"):
        benchmark.owned_process_group(_FakeProcess(4242, reaped=False))


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_the_real_arm_leader_leads_its_own_group():
    """The setsid contract the group identity rests on, measured."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert benchmark.owned_process_group(process) == process.pid
        assert os.getpgid(process.pid) == process.pid
    finally:
        process.kill()
        process.wait(timeout=10)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_it_refuses_to_signal_the_group_it_is_running_in(unsignalled):
    """Signalling our own group would kill the benchmark, and pytest with it."""
    with pytest.raises(ValueError, match="own group"):
        benchmark.terminate_process_group(
            _FakeProcess(os.getpid(), reaped=True), pgid=os.getpgid(0)
        )
    assert unsignalled == []
