"""The process census must survive the sandbox it actually runs in.

Phases 3 and 4 of the sub-hour plan could not publish a resource measurement
for a whole day of autonomous runs, and nothing was wrong with either
measurement: `/bin/ps` is setuid root on macOS, a Seatbelt sandbox refuses to
exec a setuid binary, and the census was `subprocess.run(["ps", ...])`. These
tests pin both halves of the repair — that the census no longer needs `ps`,
and that it still refuses to be read as "nothing is running" when it fails.
"""

import ctypes
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import process_census as pc


def _ps_available() -> bool:
    try:
        pc._ps_snapshot()
    except Exception:
        return False
    return True


class TestTheCensusNoLongerNeedsPs:
    """The regression: a census that cannot exec `ps` must still work."""

    def test_a_census_survives_an_unrunnable_ps(self, monkeypatch):
        def eperm(*args, **kwargs):
            raise PermissionError(1, "Operation not permitted", "ps")

        monkeypatch.setattr(pc.subprocess, "run", eperm)
        rows = pc.process_group_snapshot()
        assert any(pid == os.getpid() for pid, _, _ in rows)
        assert pc.census_mechanism() != "ps(-eo pid=,pgid=,rss=)"

    def test_the_preferred_mechanism_is_not_a_subprocess_on_darwin(self):
        if sys.platform != "darwin":
            pytest.skip("mechanism ladder is platform specific")
        assert pc.census_mechanism_names()[0].startswith("libproc")

    def test_a_live_child_is_counted_with_its_parent(self):
        process = subprocess.Popen(
            [sys.executable, "-c",
             "x = bytearray(64 * 1024 * 1024)\nimport time; time.sleep(5)"],
            start_new_session=True)
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                group = [(pid, rss) for pid, pgid, rss
                         in pc.process_group_snapshot() if pgid == process.pid]
                if sum(rss for _, rss in group) * 1024 > 64 * 1024 * 1024:
                    break
                time.sleep(0.2)
            assert process.pid in [pid for pid, _ in group]
            # The whole point of a group census: the child's memory is in the
            # total, not just whichever single process happened to be biggest.
            assert sum(rss for _, rss in group) * 1024 > 64 * 1024 * 1024
        finally:
            process.kill()
            process.wait(timeout=5.0)


class TestTheCensusAgreesWithPs:
    """`ps` is no longer used, so it becomes the independent check.

    A replacement measurement is only trustworthy if it reports what the tool
    it replaced reported. Where both mechanisms run, they must agree process
    for process on identity and group membership.
    """

    def test_every_shared_process_has_the_same_pgid(self):
        if not _ps_available():
            pytest.skip("`ps` cannot run here; nothing to cross-check against")
        ps_rows = {pid: pgid for pid, pgid, _ in pc._ps_snapshot()}
        native = {pid: pgid for pid, pgid, _ in pc._mechanisms()[0][1]()}
        shared = set(ps_rows) & set(native)
        assert len(shared) > 10
        assert [pid for pid in shared if ps_rows[pid] != native[pid]] == []

    def test_resident_sizes_agree_closely(self):
        if not _ps_available():
            pytest.skip("`ps` cannot run here; nothing to cross-check against")
        ps_rows = {pid: rss for pid, _, rss in pc._ps_snapshot()}
        native = {pid: rss for pid, _, rss in pc._mechanisms()[0][1]()}
        shared = [pid for pid in set(ps_rows) & set(native) if ps_rows[pid] > 0]
        deviations = sorted(
            abs(ps_rows[pid] - native[pid]) / ps_rows[pid] for pid in shared)
        # Not equality: the two snapshots are taken at different instants, so
        # a process that allocates between them genuinely differs.  The median
        # is the claim — the mechanisms measure the same quantity.
        assert deviations[len(deviations) // 2] < 0.01


class TestAFailedCensusIsNeverReadAsEmpty:
    """The trust contract, unchanged by the mechanism swap."""

    def test_every_mechanism_failing_raises(self, monkeypatch):
        def boom():
            raise OSError("no census here")

        monkeypatch.setattr(pc, "_mechanisms", lambda: (("fake", boom),))
        with pytest.raises(pc.ProcessCensusUnavailable, match="no census here"):
            pc.process_group_snapshot()

    def test_a_census_blind_to_its_own_caller_is_rejected(self, monkeypatch):
        # A mechanism that returns rows but cannot see the calling process is
        # not "an empty system" — it is a mechanism that has lost visibility,
        # and believing it would let a surviving SUMO process pass a reap gate.
        monkeypatch.setattr(
            pc, "_mechanisms",
            lambda: (("partial", lambda: [(os.getpid() + 100000, 1, 1)]),))
        with pytest.raises(pc.ProcessCensusUnavailable,
                           match="did not contain the calling process"):
            pc.process_group_snapshot()

    def test_a_zero_rss_self_row_is_rejected(self, monkeypatch):
        monkeypatch.setattr(
            pc, "_mechanisms",
            lambda: (("partial", lambda: [
                (os.getpid(), os.getpgrp(), 0)]),))
        with pytest.raises(pc.ProcessCensusUnavailable,
                           match="non-positive resident size"):
            pc.process_group_snapshot()

    def test_a_wrong_process_group_self_row_is_rejected(self, monkeypatch):
        monkeypatch.setattr(
            pc, "_mechanisms",
            lambda: (("partial", lambda: [
                (os.getpid(), os.getpgrp() + 1, 1)]),))
        with pytest.raises(pc.ProcessCensusUnavailable,
                           match="wrong process group"):
            pc.process_group_snapshot()

    def test_libproc_does_not_turn_unreadable_live_rss_into_zero(
            self, monkeypatch):
        class FakeLibproc:
            @staticmethod
            def proc_listpids(_selector, _typeinfo, buffer, _buffer_size):
                if buffer is None:
                    return ctypes.sizeof(ctypes.c_int32)
                ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int32))[0] = (
                    os.getpid())
                return ctypes.sizeof(ctypes.c_int32)

            @staticmethod
            def proc_pidinfo(pid, flavor, _arg, buffer, size):
                if flavor == pc._PROC_PIDTASKINFO:
                    return 0
                info = ctypes.cast(
                    buffer, ctypes.POINTER(pc._ProcBSDInfo)).contents
                info.pbi_pid = pid
                info.pbi_pgid = os.getpgrp()
                return size

        monkeypatch.setattr(pc, "_libproc", lambda: FakeLibproc())
        with pytest.raises(OSError, match="resident size for live pid"):
            pc._libproc_snapshot()

    def test_a_working_mechanism_after_a_failing_one_is_used(self, monkeypatch):
        def boom():
            raise OSError("first mechanism down")

        working = pc._mechanisms()[0][1]
        monkeypatch.setattr(
            pc, "_mechanisms",
            lambda: (("broken", boom), ("working", working)))
        assert pc.census_mechanism() == "working"
        assert any(pid == os.getpid()
                   for pid, _, _ in pc.process_group_snapshot())


class TestDescendantProcessGroups:
    def test_nested_new_sessions_are_owned_by_the_root(self, monkeypatch):
        monkeypatch.setattr(
            pc, "_relation_mechanisms",
            lambda: (("fake", lambda: [
                (100, 1, 100),
                (101, 100, 100),
                (102, 101, 102),
                (103, 102, 103),
                (200, 1, 200),
            ]),))

        assert pc.descendant_process_groups(100) == {102, 103}

    def test_an_invisible_root_fails_closed(self, monkeypatch):
        monkeypatch.setattr(
            pc, "_relation_mechanisms",
            lambda: (("fake", lambda: [(200, 1, 200)]),))

        with pytest.raises(pc.ProcessCensusUnavailable,
                           match="root pid 100 is not visible"):
            pc.descendant_process_groups(100)
