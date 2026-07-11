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


class TestSignalArtifactLabel:
    """Content-addressed labels (fixed 2026-07-11, external review section
    6.1): a stale artifact from a DIFFERENT demand/network build must never
    be reused just because the window matches."""

    def test_same_inputs_give_the_same_label(self):
        a = so.signal_artifact_label("07:00", "09:00", "sig1", "net1")
        b = so.signal_artifact_label("07:00", "09:00", "sig1", "net1")
        assert a == b

    def test_different_demand_signature_gives_a_different_label(self):
        a = so.signal_artifact_label("07:00", "09:00", "sig1", "net1")
        b = so.signal_artifact_label("07:00", "09:00", "sig2", "net1")
        assert a != b

    def test_different_net_fingerprint_gives_a_different_label(self):
        a = so.signal_artifact_label("07:00", "09:00", "sig1", "net1")
        b = so.signal_artifact_label("07:00", "09:00", "sig1", "net2")
        assert a != b

    def test_same_window_different_demand_would_have_collided_under_the_old_label(self):
        # The bug this guards against directly: the OLD label was just the
        # window, so these two would have been IDENTICAL filenames despite
        # coming from different demand builds.
        old_style_a = "0700_0900"
        old_style_b = "0700_0900"
        assert old_style_a == old_style_b   # confirms the old collision
        new_a = so.signal_artifact_label("07:00", "09:00", "sigA", "netA")
        new_b = so.signal_artifact_label("07:00", "09:00", "sigB", "netA")
        assert new_a != new_b   # the fix: no longer collides


class TestBuildSignalConditions:
    """build_signal_conditions (fixed 2026-07-11): a single shared
    implementation for D2 and D3, so they can never diverge on caching
    behavior or which conditions exist — the actual bug this replaced was
    signal_meso_screen.py silently reusing stale artifacts by bare filename
    existence with no freshness check."""

    def _stub_tools(self, monkeypatch, tmp_path, calls):
        def fake_adapt(home, route_path, begin_s, out_path, program_id="a"):
            calls.append(("adapt", out_path))
            out_path.write_text("<additional/>")

        def fake_coord(home, route_path, adapted_path, out_path):
            calls.append(("coord", out_path))
            out_path.write_text("<additional/>")

        def fake_alt_net(home, tls_type, out_path):
            calls.append(("net", tls_type, out_path))
            out_path.write_text("<net/>")

        monkeypatch.setattr(so, "run_tls_cycle_adaptation", fake_adapt)
        monkeypatch.setattr(so, "run_tls_coordinator", fake_coord)
        monkeypatch.setattr(so, "build_alt_type_net", fake_alt_net)
        monkeypatch.setattr(so.rs, "SUMO_DIR", tmp_path)
        monkeypatch.setattr(so.rs, "NET_PATH", tmp_path / "net.net.xml")
        (tmp_path / "net.net.xml").write_text("<net/>")

    def test_builds_all_artifacts_on_first_call(self, tmp_path, monkeypatch):
        calls = []
        self._stub_tools(monkeypatch, tmp_path, calls)
        conditions = so.build_signal_conditions(
            tmp_path, [tmp_path / "calibrated.rou.xml"], 0, "label1")
        assert {c[0] for c in calls} == {"adapt", "coord", "net", "net"}
        assert set(conditions) == {"baseline", "adapted", "adapted_coordinated",
                                   "actuated", "delay_based"}

    def test_second_call_with_the_same_label_reuses_cached_artifacts(self, tmp_path, monkeypatch):
        calls = []
        self._stub_tools(monkeypatch, tmp_path, calls)
        so.build_signal_conditions(tmp_path, [tmp_path / "calibrated.rou.xml"], 0, "label1")
        calls.clear()
        so.build_signal_conditions(tmp_path, [tmp_path / "calibrated.rou.xml"], 0, "label1")
        assert calls == []   # nothing rebuilt -- all 4 artifacts already exist

    def test_different_label_rebuilds_instead_of_reusing_stale_artifacts(self, tmp_path, monkeypatch):
        # The actual bug: a NEW demand/network (-> new label) must NOT
        # silently reuse the previous label's cached files.
        calls = []
        self._stub_tools(monkeypatch, tmp_path, calls)
        so.build_signal_conditions(tmp_path, [tmp_path / "calibrated.rou.xml"], 0, "label1")
        calls.clear()
        so.build_signal_conditions(tmp_path, [tmp_path / "calibrated.rou.xml"], 0, "label2")
        assert {c[0] for c in calls} == {"adapt", "coord", "net", "net"}


class TestConditionNetFingerprints:
    def test_baseline_and_adapted_share_the_deployed_network_fingerprint(self, tmp_path):
        net = tmp_path / "net.net.xml"
        net.write_text("<net/>")
        alt = tmp_path / "alt.net.xml"
        alt.write_text("<net different/>")
        conditions = {
            "baseline": {"net_path": net, "add_paths": []},
            "adapted": {"net_path": net, "add_paths": []},
            "actuated": {"net_path": alt, "add_paths": []},
        }
        fps = so.condition_net_fingerprints(conditions)
        assert fps["baseline"] == fps["adapted"]
        assert fps["actuated"] != fps["baseline"]

    def test_each_net_file_hashed_only_once(self, tmp_path, monkeypatch):
        net = tmp_path / "net.net.xml"
        net.write_text("<net/>")
        calls = []
        orig = so.net_fingerprint

        def counting_fp(p):
            calls.append(p)
            return orig(p)

        monkeypatch.setattr(so, "net_fingerprint", counting_fp)
        conditions = {"baseline": {"net_path": net, "add_paths": []},
                     "adapted": {"net_path": net, "add_paths": []}}
        so.condition_net_fingerprints(conditions)
        assert calls == [net]   # hashed once, reused for the second condition
