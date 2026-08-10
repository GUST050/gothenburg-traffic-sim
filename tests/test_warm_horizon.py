"""The horizon pre-warm run: complete coverage, and survivable over a year.

Two properties are worth a permanent test here, because both were real bugs.

* COVERAGE. A date is warm only when the library holds its entry under every
  pool composition a consumer can ask for — its own day type, and mixed. A
  fixed Mon–Fri/Sat–Sun week plan quietly fails that around holidays (a
  holiday makes the Mon–Fri window mixed, so the week's weekdays never get a
  weekday-only entry): 30 dates of 2027 and 3 more at the year's start.
* SURVIVAL. A year-long run must not lose the night to one failed window,
  must not interleave its files with the web app's, and must resume.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import warm_demand_horizon as wh                       # noqa: E402
from traffic_sim.simulation import workspace as ws     # noqa: E402


def _plan(first, last):
    return wh.plan_windows(pd.Timestamp(first), pd.Timestamp(last))


class TestPlanCoverage:

    def test_full_year_leaves_no_date_uncovered(self):
        first, last = pd.Timestamp("2027-01-01"), pd.Timestamp("2027-12-31")
        items = _plan(first, last)
        assert wh.uncovered(items, first, last) == []

    def test_historical_year_also_fully_covered(self):
        first, last = pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")
        assert wh.uncovered(_plan(first, last), first, last) == []

    def test_holiday_week_still_yields_weekday_only_entries(self):
        """2027-01-06 (Epiphany, a Wednesday) splits its week.

        The regression: a Mon–Fri window over that week is MIXED, so Monday
        and Tuesday never receive a ``(weekday,)`` entry and a one-day view
        of them rebuilds from scratch.
        """
        first, last = pd.Timestamp("2027-01-04"), pd.Timestamp("2027-01-10")
        covered = wh.coverage(_plan(first, last))
        assert ("weekday",) in covered["2027-01-05"]
        assert ("weekend",) in covered["2027-01-06"]      # the holiday itself
        assert ("weekday",) in covered["2027-01-07"]
        assert ("weekday", "weekend") in covered["2027-01-05"]

    def test_every_window_is_a_single_calendar_block_of_one_composition(self):
        for item in _plan("2027-03-22", "2027-04-05"):
            start = pd.Timestamp(item["start_date"])
            assert tuple(item["composition"]) == wh.composition_of(
                start, item["days"])

    def test_plan_has_no_duplicate_windows(self):
        items = _plan("2027-01-01", "2027-12-31")
        keys = [(item["start_date"], item["days"]) for item in items]
        assert len(keys) == len(set(keys))

    def test_short_horizon_reports_what_it_cannot_cover(self):
        first, last = pd.Timestamp("2027-07-20"), pd.Timestamp("2027-07-22")
        gaps = wh.uncovered(_plan(first, last), first, last)
        assert [gap["date"] for gap in gaps] == [
            "2027-07-20", "2027-07-21", "2027-07-22"]
        assert all(gap["missing"] == ["weekday+weekend"] for gap in gaps)

    def test_single_day_horizon_builds_that_day(self):
        items = _plan("2027-02-11", "2027-02-11")
        assert [(i["start_date"], i["days"]) for i in items] == [
            ("2027-02-11", 1)]

    def test_year_cost_is_two_entries_per_date(self):
        items = _plan("2027-01-01", "2027-12-31")
        slots = sum(item["days"] for item in items)
        assert 730 <= slots <= 740        # 365 dates × own-type + mixed


class TestIdentityFingerprint:
    """Resume may only skip work whose stored entries can still be hit."""

    def test_fingerprint_covers_exactly_the_identity_sources(self):
        import build_sumo_demand as bsd

        source = Path("build_sumo_demand.py").read_text()
        marker = source.split('if name in {"build_candidates"')[1]
        named = {part.split('"')[1] for part in marker.split(",")[:8]
                 if '"' in part}
        named.add("build_candidates")
        assert named <= set(wh.IDENTITY_SOURCE_FILES)
        assert set(wh.IDENTITY_SOURCE_FILES) <= set(bsd.SOURCE_FILES)

    def test_fingerprint_changes_when_a_source_changes(self, tmp_path,
                                                       monkeypatch):
        probe = tmp_path / "probe.py"
        probe.write_text("x = 1\n")
        monkeypatch.setitem(wh.IDENTITY_SOURCE_FILES, "probe", probe)
        before = wh.source_fingerprint()
        probe.write_text("x = 2\n")
        assert wh.source_fingerprint() != before


class TestWorkspaceLock:

    def test_second_holder_is_refused_and_told_who_holds_it(self, tmp_path):
        path = tmp_path / "lock"
        first = ws.WorkspaceLock("warm run", path=path)
        assert first.acquire()
        try:
            assert ws.workspace_holder(path)["owner"] == "warm run"
            second = ws.WorkspaceLock("web app", path=path)
            assert not second.acquire()
            with pytest.raises(ws.WorkspaceBusy, match="warm run"):
                second.acquire_or_raise()
        finally:
            first.release()
        assert ws.workspace_holder(path) is None
        assert ws.WorkspaceLock("web app", path=path).acquire()

    def test_holder_is_none_when_the_file_never_existed(self, tmp_path):
        assert ws.workspace_holder(tmp_path / "absent") is None

    def test_a_cli_search_waits_for_the_workspace_and_says_whose_it_is(
            self, monkeypatch):
        """A search rebuilds envelopes into sumo/ for hours; starting one
        beside a running horizon warm must refuse, not interleave."""
        import run_monthly_closure_search as search

        monkeypatch.setattr(search, "WorkspaceLock",
                            lambda owner: ws.WorkspaceLock(owner, path=LOCK))
        # Preflight now intentionally runs before the demand lock, so give it
        # a real valid spec. The heavy backend loader must remain unreachable
        # when another process owns the workspace.
        monkeypatch.setattr(
            search, "_simulation_backends",
            lambda: (_ for _ in ()).throw(AssertionError(
                "a lock-refused search must not import the SUMO backend")))
        monkeypatch.setattr(sys, "argv", [
            "run_monthly_closure_search.py", "--spec",
            "validation/golden_monthly_search_spec_v6.json",
            "--policy", "validation/monthly_search_policy_v1.json",
            "--baseline-trip-duration-p99-s", "3600",
            "--workspace-wait-s", "0"])
        outsider = ws.WorkspaceLock("warm_demand_horizon 777", path=LOCK)
        assert outsider.acquire()
        try:
            with pytest.raises(SystemExit, match="warm_demand_horizon 777"):
                search.main()
        finally:
            outsider.release()

    def test_serve_refuses_a_job_while_another_process_holds_it(self,
                                                               monkeypatch):
        import serve

        monkeypatch.setattr(serve, "WorkspaceLock",
                            lambda owner: ws.WorkspaceLock(owner, path=LOCK))
        outsider = ws.WorkspaceLock("warm_demand_horizon 4242", path=LOCK)
        assert outsider.acquire()
        try:
            slot = serve._SimulationSlot()
            assert not slot.acquire(blocking=False)
            assert "warm_demand_horizon" in slot.blocked_by
            assert not slot.locked()        # the thread lock is given back
        finally:
            outsider.release()
        slot = serve._SimulationSlot()
        assert slot.acquire(blocking=False)
        slot.release()


# Kept out of the repository tree: the lock file is process state, not a
# project artifact, and a test must not leave one behind.
LOCK = Path(tempfile.gettempdir()) / "gs-pytest-workspace.lock"


class TestRunSurvivesFailures:
    """One bad window must cost that window, not the horizon."""

    @staticmethod
    def _run(monkeypatch, tmp_path, results, extra_args=()):
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            log = kwargs["stdout"]
            index = len(calls) - 1
            code = results[index] if index < len(results) else 0
            log.write("  demand day library: 1/1 day(s) reused, 0 calibrated\n"
                      if code == 0 else "boom: solver failed\n")
            return subprocess.CompletedProcess(command, code)

        monkeypatch.setattr(wh.subprocess, "run", fake_run)
        monkeypatch.setattr(wh, "snapshot_live_demand_release", lambda: {})
        monkeypatch.setattr(wh, "restore_live_demand_release", lambda _s: None)
        # Never touch the machine's real kill-recovery marker, or its real
        # run archives, from a test: main() now discards build archives by
        # default, and a background job's directory must not be collateral.
        monkeypatch.setattr(wh, "recover_live_demand_release", lambda: None)
        monkeypatch.setattr(wh, "discard_new_archives", lambda _before: 0)
        monkeypatch.setattr(wh, "library_has_dates", lambda item: True)
        monkeypatch.setattr(wh, "WorkspaceLock",
                            lambda owner: ws.WorkspaceLock(owner, path=LOCK))
        monkeypatch.setattr(wh, "_source_year", lambda source: 2027)
        monkeypatch.setattr(sys, "argv", [
            "warm_demand_horizon.py", "--source", "forecast",
            "--from", "2027-03-22", "--to", "2027-03-28",
            "--log-dir", str(tmp_path), *extra_args])
        return calls

    def test_a_failed_window_is_recorded_and_the_rest_still_build(
            self, monkeypatch, tmp_path, capsys):
        calls = self._run(monkeypatch, tmp_path, {0: 3})
        with pytest.raises(SystemExit) as exit_info:
            wh.main()
        assert exit_info.value.code == 1              # the run reports failure
        assert len(calls) == 3                        # but built the others
        progress = json.loads((tmp_path / "progress.json").read_text())
        statuses = {key: entry["status"]
                    for key, entry in progress["items"].items()}
        assert sorted(statuses.values()) == ["failed", "ok", "ok"]
        assert "FAILED" in capsys.readouterr().out

    def test_stop_on_error_stops(self, monkeypatch, tmp_path):
        calls = self._run(monkeypatch, tmp_path, {0: 3}, ("--stop-on-error",))
        with pytest.raises(SystemExit):
            wh.main()
        assert len(calls) == 1

    def test_resume_skips_completed_windows_and_retries_failed_ones(
            self, monkeypatch, tmp_path):
        self._run(monkeypatch, tmp_path, {0: 3})
        with pytest.raises(SystemExit):
            wh.main()
        calls = self._run(monkeypatch, tmp_path, {})
        wh.main()
        assert len(calls) == 1                        # only the failed one

    def test_a_changed_source_fingerprint_invalidates_the_resume(
            self, monkeypatch, tmp_path):
        self._run(monkeypatch, tmp_path, {})
        wh.main()
        progress = json.loads((tmp_path / "progress.json").read_text())
        progress["source_fingerprint"] = {"build_candidates": "different"}
        (tmp_path / "progress.json").write_text(json.dumps(progress))
        calls = self._run(monkeypatch, tmp_path, {})
        wh.main()
        assert len(calls) == 3                        # everything rebuilt

    def test_low_disk_stops_before_starting_a_build(self, monkeypatch,
                                                    tmp_path, capsys):
        calls = self._run(monkeypatch, tmp_path, {})
        monkeypatch.setattr(wh, "_free_gb", lambda path=Path("."): 1.0)
        with pytest.raises(SystemExit) as exit_info:
            wh.main()
        assert exit_info.value.code == 1
        assert calls == []
        assert "below the" in capsys.readouterr().out

    def test_a_busy_workspace_stops_the_run_resumably(self, monkeypatch,
                                                      tmp_path, capsys):
        calls = self._run(monkeypatch, tmp_path, {}, ("--lock-timeout", "0"))
        outsider = ws.WorkspaceLock("web app 99", path=LOCK)
        assert outsider.acquire()
        try:
            with pytest.raises(SystemExit):
                wh.main()
        finally:
            outsider.release()
        assert calls == []
        assert "web app 99" in capsys.readouterr().out


class TestHorizonGuards:

    def test_a_horizon_outside_the_source_year_is_refused_up_front(
            self, monkeypatch):
        monkeypatch.setattr(wh, "_source_year", lambda source: 2025)
        monkeypatch.setattr(sys, "argv", [
            "warm_demand_horizon.py", "--source", "historical",
            "--from", "2027-01-01", "--to", "2027-01-07", "--dry-run"])
        with pytest.raises(SystemExit, match="2025"):
            wh.main()

    def test_reversed_range_is_refused(self, monkeypatch):
        monkeypatch.setattr(wh, "_source_year", lambda source: 2027)
        monkeypatch.setattr(sys, "argv", [
            "warm_demand_horizon.py", "--source", "forecast",
            "--from", "2027-02-02", "--to", "2027-02-01", "--dry-run"])
        with pytest.raises(SystemExit, match="precede"):
            wh.main()

    def test_command_asks_for_a_whole_day_window(self):
        command = wh.item_command({"start_date": "2027-02-11", "days": 3},
                                  "forecast")
        assert command[1:] == [
            "build_sumo_demand.py", "--start-date", "2027-02-11",
            "--days", "3", "--begin", "00:00", "--end", "24:00",
            "--source", "forecast", "--keep-scenarios"]


class TestPruneStaleEntries:
    """Superseded library entries are invisible: they can never be hit again,
    but they still hold the disk a year-long horizon needs."""

    @staticmethod
    def _entry(root, date, source_hashes, size=2048):
        directory = root / date / "key"
        directory.mkdir(parents=True)
        (directory / "calibrated.rou.xml.gz").write_bytes(b"x" * size)
        (directory / "manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "kind": "calibrated_demand_day",
            "key": "key",
            "identity": {"date": date, "source": "forecast",
                         "source_hashes": source_hashes},
            "artifacts": {},
        }))
        return directory

    def test_only_entries_with_a_superseded_source_are_reported(
            self, tmp_path, monkeypatch):
        current = wh.source_fingerprint()
        live = self._entry(tmp_path, "2027-05-03", dict(current))
        dead = self._entry(tmp_path, "2027-05-04",
                           {**current, "pfe": "0" * 64})
        stale = wh.stale_entries(tmp_path)
        assert [entry["path"] for entry in stale] == [dead]
        assert stale[0]["superseded"] == ["pfe"]
        assert stale[0]["bytes"] >= 2048
        assert live.is_dir()

    def test_an_entry_naming_an_unknown_source_is_left_alone(self, tmp_path):
        self._entry(tmp_path, "2027-05-05", {"some_future_module": "abc"})
        assert wh.stale_entries(tmp_path) == []

    def test_prune_reports_before_it_deletes(self, tmp_path, monkeypatch,
                                             capsys):
        current = wh.source_fingerprint()
        dead = self._entry(tmp_path, "2027-05-06", {**current, "pfe": "1" * 64})
        monkeypatch.setattr(wh, "stale_entries",
                            lambda root=tmp_path: [
                                {"path": dead, "bytes": 2048,
                                 "date": "2027-05-06", "superseded": ["pfe"]}])
        wh.prune(delete=False)
        assert dead.is_dir()
        assert "--prune --yes" in capsys.readouterr().out
        wh.prune(delete=True)
        assert not dead.exists()


class TestArchiveDiscard:
    """Warming writes ~100 MB of duplicated demand per day-slot into runs/.
    Reclaiming it may never touch an archive this run did not create."""

    def test_only_archives_created_during_the_build_are_removed(self, tmp_path,
                                                                monkeypatch):
        runs = tmp_path / "runs"
        (runs / "demand-old").mkdir(parents=True)
        (runs / "demand-old" / "calibrated.rou.xml").write_bytes(b"x" * 100)
        monkeypatch.setattr(wh, "_demand_archives",
                            lambda root=runs: {p for p in runs.glob("demand-*")
                                               if p.is_dir()})
        before = wh._demand_archives()
        fresh = runs / "demand-new"
        fresh.mkdir()
        (fresh / "calibrated.rou.xml").write_bytes(b"y" * 2048)

        freed = wh.discard_new_archives(before)

        assert freed == 2048
        assert not fresh.exists()
        assert (runs / "demand-old").is_dir()      # untouched evidence

    def test_projection_names_the_archive_share(self, monkeypatch, capsys):
        monkeypatch.setattr(wh, "_source_year", lambda source: 2027)
        monkeypatch.setattr(sys, "argv", [
            "warm_demand_horizon.py", "--source", "forecast",
            "--from", "2027-01-01", "--to", "2027-12-31", "--dry-run"])
        wh.main()
        out = capsys.readouterr().out
        assert "GB projected" in out
        assert "run archive is discarded" in out   # the 8x-projection bug


class TestResumeChecksTheRightEntry:

    def test_a_date_with_only_the_other_composition_is_not_counted_warm(
            self, tmp_path):
        """A date normally holds two entries. Resume must check the one this
        window produced, not merely that the date directory exists."""
        entry = tmp_path / "2027-05-05" / "key"
        entry.mkdir(parents=True)
        (entry / "manifest.json").write_text(json.dumps({
            "identity": {"pool_composition": ["weekday", "weekend"]}}))
        item = {"start_date": "2027-05-05", "days": 1,
                "composition": ["weekday"]}
        assert not wh.library_has_dates(item, root=tmp_path)
        item_mixed = {"start_date": "2027-05-05", "days": 1,
                      "composition": ["weekday", "weekend"]}
        assert wh.library_has_dates(item_mixed, root=tmp_path)
