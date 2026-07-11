"""
Unit tests for signal_optimize.py (PLAN.md Phase D2).

The heavy end (actually running tlsCycleAdaptation.py/tlsCoordinator.py/
netconvert/sumo) is exercised manually against real demand, not here —
mirrors signal_lab.py's (D1) and suggest_closure_time.py's (C4) test style.
These tests cover the parts that must be correct independent of SUMO:
relative-percentage math and the subprocess command construction/error
handling for each of the three tool wrappers.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import signal_optimize as so


class TestRelativePct:
    def test_improvement_is_negative_percent(self):
        # candidate has LOWER time loss than baseline -> improvement -> negative %
        assert so.relative_pct(1000.0, 800.0) == -20.0

    def test_regression_is_positive_percent(self):
        assert so.relative_pct(1000.0, 1500.0) == 50.0

    def test_no_change_is_zero_percent(self):
        assert so.relative_pct(1000.0, 1000.0) == 0.0

    def test_zero_baseline_returns_none_not_a_crash(self):
        # A degenerate empty-window baseline (zero trips, zero time loss)
        # would otherwise divide by zero -- must degrade to an honest
        # "not computable", not raise or silently report inf/nan.
        assert so.relative_pct(0.0, 500.0) is None


class TestRunTlsCycleAdaptation:
    def test_builds_expected_command(self, monkeypatch, tmp_path):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(so.subprocess, "run", fake_run)
        home = tmp_path
        route = tmp_path / "demand.rou.xml"
        out = tmp_path / "adapted.add.xml"
        so.run_tls_cycle_adaptation(home, route, 25200, out, program_id="a")

        cmd = captured["cmd"]
        assert str(home / "tools" / "tlsCycleAdaptation.py") in cmd
        assert cmd[cmd.index("-b") + 1] == "25200"
        assert cmd[cmd.index("-p") + 1] == "a"
        assert cmd[cmd.index("-o") + 1] == str(out.resolve())

    def test_nonzero_exit_is_a_clear_sys_exit(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        monkeypatch.setattr(so.subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            so.run_tls_cycle_adaptation(tmp_path, tmp_path / "r.rou.xml", 0,
                                        tmp_path / "out.add.xml")


class TestRunTlsCoordinator:
    def test_builds_expected_command(self, monkeypatch, tmp_path):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(so.subprocess, "run", fake_run)
        home = tmp_path
        route = tmp_path / "demand.rou.xml"
        adapted = tmp_path / "adapted.add.xml"
        out = tmp_path / "coordinated.add.xml"
        so.run_tls_coordinator(home, route, adapted, out)

        cmd = captured["cmd"]
        assert str(home / "tools" / "tlsCoordinator.py") in cmd
        assert cmd[cmd.index("-a") + 1] == str(adapted.resolve())
        assert cmd[cmd.index("-o") + 1] == str(out.resolve())

    def test_nonzero_exit_is_a_clear_sys_exit(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        monkeypatch.setattr(so.subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            so.run_tls_coordinator(tmp_path, tmp_path / "r.rou.xml",
                                   tmp_path / "a.add.xml", tmp_path / "out.add.xml")


class TestBuildAltTypeNet:
    def test_missing_plain_xml_inputs_is_a_clear_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(so.rs, "SUMO_DIR", tmp_path)   # no plain.nod/edg.xml here
        with pytest.raises(SystemExit):
            so.build_alt_type_net(tmp_path, "actuated", tmp_path / "out.net.xml")

    def test_builds_expected_command_when_inputs_exist(self, monkeypatch, tmp_path):
        monkeypatch.setattr(so.rs, "SUMO_DIR", tmp_path)
        (tmp_path / "plain.nod.xml").write_text("<nodes/>")
        (tmp_path / "plain.edg.xml").write_text("<edges/>")
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(so.subprocess, "run", fake_run)
        so.build_alt_type_net(tmp_path, "delay_based", tmp_path / "out.net.xml")

        cmd = captured["cmd"]
        assert cmd[cmd.index("--tls.default-type") + 1] == "delay_based"
        assert cmd[cmd.index("-n") + 1] == str(tmp_path / "plain.nod.xml")
        assert cmd[cmd.index("-e") + 1] == str(tmp_path / "plain.edg.xml")

    def test_nonzero_exit_is_a_clear_sys_exit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(so.rs, "SUMO_DIR", tmp_path)
        (tmp_path / "plain.nod.xml").write_text("<nodes/>")
        (tmp_path / "plain.edg.xml").write_text("<edges/>")

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        monkeypatch.setattr(so.subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            so.build_alt_type_net(tmp_path, "actuated", tmp_path / "out.net.xml")
