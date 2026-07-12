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
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import signal_optimize as so


def signal_run(seed, variant, time_loss, teleports=0, unfinished=0):
    metrics = so.cm.DisruptionMetrics(
        total_time_loss_s=time_loss, trip_count=10,
        unfinished_trips=unfinished, unfinished_waiting_trips=unfinished,
        teleport_total=teleports, teleport_reasons={}, loaded=10, inserted=10,
        running_at_end=unfinished, waiting_at_end=unfinished,
    )
    return so.SignalRun(seed=seed, demand_variant=variant, metrics=metrics)


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


class TestPairedComparison:
    def test_reports_seed_and_variant_matched_deltas(self):
        baseline = [signal_run(1000, "q50.rou.xml", 100.0),
                    signal_run(1001, "q10.rou.xml", 200.0)]
        candidate = [signal_run(1000, "q50.rou.xml", 90.0),
                     signal_run(1001, "q10.rou.xml", 230.0, teleports=1)]

        result = so.paired_comparison(baseline, candidate)

        assert result["pair_keys"] == ["1000:q50.rou.xml", "1001:q10.rou.xml"]
        assert result["time_loss_delta_s"] == [-10.0, 30.0]
        assert result["mean_time_loss_delta_s"] == 10.0
        assert result["median_time_loss_delta_s"] == 10.0
        assert result["stddev_time_loss_delta_s"] > 0
        assert result["teleport_delta"] == [0, 1]

    def test_rejects_unmatched_or_duplicate_runs(self):
        baseline = [signal_run(1000, "q50.rou.xml", 100.0)]
        unmatched = [signal_run(1001, "q50.rou.xml", 90.0)]
        duplicate = [signal_run(1000, "q50.rou.xml", 90.0),
                     signal_run(1000, "q50.rou.xml", 80.0)]

        with pytest.raises(ValueError, match="identical seed/demand pairs"):
            so.paired_comparison(baseline, unmatched)
        with pytest.raises(ValueError, match="duplicate"):
            so.paired_comparison(baseline, duplicate)


def _write_fake_sumo_metrics(tmp_path: Path) -> dict:
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text('<tripinfos><tripinfo id="v" arrival="10.0" '
                        'timeLoss="1.0" waitingTime="0.0"/></tripinfos>')
    statistics = tmp_path / "statistics.xml"
    statistics.write_text('<statistics><vehicles loaded="1" inserted="1" '
                          'running="0" waiting="0"/><teleports total="0"/></statistics>')
    summary = tmp_path / "summary.xml"
    summary.write_text('<summary><step time="0" halting="0"/></summary>')
    return {"tripinfo": tripinfo, "statistics": statistics, "summary": summary}


def _write_edgedata_output(tmp_path: Path, add_paths: list[Path], edges_xml: str) -> None:
    """Find the edgeData additional run_condition just wrote (there is
    exactly one when closed_edges is given) and write the edgeData FILE it
    references, exactly like a real sumo run would — reads the real
    file="..." attribute instead of guessing the internal naming scheme,
    so this test stays correct if that scheme ever changes."""
    ed_add = next(p for p in add_paths if p.name.startswith("edgedata_add_"))
    file_attr = ET.parse(ed_add).getroot().find("edgeData").get("file")
    (tmp_path / file_attr).write_text(f"<meandata><interval>{edges_xml}</interval></meandata>")


class TestRunConditionClosedEdges:
    """run_condition's closed_edges param (found in review 2026-07-12):
    without it, closure_metrics.disqualification_reasons()'s active-
    closure-edge-throughput leak check stayed permanently None -> inert
    for every run_condition-based caller, including D4's
    signal_closure_combine.py, the one caller that actually closes edges.
    Mocked here (real SUMO execution is exercised manually, matching this
    file's established test style) — verifies the wiring: an edgeData
    additional is requested per seed and its parsed throughput reaches
    build_metrics/DisruptionMetrics."""

    def test_no_closed_edges_leaves_closed_edge_throughput_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(so.rs, "SUMO_DIR", tmp_path)
        calls = []

        def fake_run_sumo(seed, route_path, add_paths, end_s, home, **kw):
            calls.append(add_paths)
            return _write_fake_sumo_metrics(tmp_path)

        monkeypatch.setattr(so.rs, "run_sumo", fake_run_sumo)
        metrics, runs = so.run_condition(
            net_path=tmp_path / "net.net.xml", add_paths=[], variants=[Path("v.rou.xml")],
            seeds=1, begin_s=0, end_s=900, home=tmp_path)
        assert metrics.closed_edge_throughput is None
        assert runs[0].metrics.closed_edge_throughput is None
        assert calls == [[]]   # no edgeData additional appended when closed_edges is empty

    def test_closed_edges_requests_edgedata_and_reports_real_throughput(self, tmp_path, monkeypatch):
        monkeypatch.setattr(so.rs, "SUMO_DIR", tmp_path)

        def fake_run_sumo(seed, route_path, add_paths, end_s, home, **kw):
            # The caller (run_condition) already wrote the edgeData additional
            # by this point; simulate SUMO by writing the edgeData FILE it
            # references, containing real throughput on the closed edge.
            assert len(add_paths) == 1   # the edgeData additional, no others here
            _write_edgedata_output(tmp_path, add_paths, '<edge id="closed_1" entered="3"/>')
            return _write_fake_sumo_metrics(tmp_path)

        monkeypatch.setattr(so.rs, "run_sumo", fake_run_sumo)
        metrics, runs = so.run_condition(
            net_path=tmp_path / "net.net.xml", add_paths=[], variants=[Path("v.rou.xml")],
            seeds=1, begin_s=0, end_s=900, home=tmp_path,
            closed_edges=["closed_1"])
        assert metrics.closed_edge_throughput == 3
        assert runs[0].metrics.closed_edge_throughput == 3

    def test_closed_edges_zero_throughput_is_a_real_zero_not_none(self, tmp_path, monkeypatch):
        # The verified-clean case: the closed edge is measured (edgeData
        # requested and written) but has zero entries -- must report 0,
        # not None, matching the closure_integrity_status fix above.
        monkeypatch.setattr(so.rs, "SUMO_DIR", tmp_path)

        def fake_run_sumo(seed, route_path, add_paths, end_s, home, **kw):
            _write_edgedata_output(tmp_path, add_paths, '<edge id="other_edge" entered="9"/>')
            return _write_fake_sumo_metrics(tmp_path)

        monkeypatch.setattr(so.rs, "run_sumo", fake_run_sumo)
        metrics, runs = so.run_condition(
            net_path=tmp_path / "net.net.xml", add_paths=[], variants=[Path("v.rou.xml")],
            seeds=1, begin_s=0, end_s=900, home=tmp_path,
            closed_edges=["closed_1"])
        assert metrics.closed_edge_throughput == 0
        assert runs[0].metrics.closed_edge_throughput == 0

    def test_scratch_edgedata_files_cleaned_up(self, tmp_path, monkeypatch):
        monkeypatch.setattr(so.rs, "SUMO_DIR", tmp_path)

        def fake_run_sumo(seed, route_path, add_paths, end_s, home, **kw):
            _write_edgedata_output(tmp_path, add_paths, "")
            return _write_fake_sumo_metrics(tmp_path)

        monkeypatch.setattr(so.rs, "run_sumo", fake_run_sumo)
        so.run_condition(
            net_path=tmp_path / "net.net.xml", add_paths=[], variants=[Path("v.rou.xml")],
            seeds=1, begin_s=0, end_s=900, home=tmp_path,
            closed_edges=["closed_1"])
        assert not list(tmp_path.glob("edgedata_*"))


class TestMergeDemandVariants:
    def test_merges_every_variant_with_unique_vehicle_ids(self, tmp_path):
        q50 = tmp_path / "q50.rou.xml"
        q10 = tmp_path / "q10.rou.xml"
        q50.write_text("<routes><vehicle id=\"pfe0\" depart=\"0\"><route edges=\"a b\"/></vehicle></routes>")
        q10.write_text("<routes><vehicle id=\"pfe0\" depart=\"0\"><route edges=\"c d\"/></vehicle></routes>")
        out = tmp_path / "ensemble.rou.xml"

        assert so.merge_demand_variants([q50, q10], out) == 2
        root = so.ET.parse(out).getroot()
        assert [v.get("id") for v in root.findall("vehicle")] == ["variant0_pfe0", "variant1_pfe0"]
        assert [v.find("route").get("edges") for v in root.findall("vehicle")] == ["a b", "c d"]


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

        def fake_regulation(net_path, out_path):
            calls.append(("regulation", out_path))
            out_path.write_text("<additional/>")
            return 0

        def fake_enforce(net_path, proposed_path, out_path):
            calls.append(("enforce", out_path))
            out_path.write_text("<additional/>")
            return 0

        def fake_demand_weighted(net_path, route_path, out_path, program_id="dw",
                                 begin_s=None, end_s=None):
            calls.append(("demand_weighted", out_path, begin_s, end_s))
            out_path.write_text("<additional/>")
            return 0

        monkeypatch.setattr(so, "run_tls_cycle_adaptation", fake_adapt)
        monkeypatch.setattr(so, "run_tls_coordinator", fake_coord)
        monkeypatch.setattr(so, "build_alt_type_net", fake_alt_net)
        monkeypatch.setattr(so, "build_regulation_compliant_tls", fake_regulation)
        monkeypatch.setattr(so, "enforce_timing_minimums", fake_enforce)
        monkeypatch.setattr(so, "build_demand_weighted_tls", fake_demand_weighted)
        monkeypatch.setattr(so.rs, "SUMO_DIR", tmp_path)
        monkeypatch.setattr(so.rs, "NET_PATH", tmp_path / "net.net.xml")
        (tmp_path / "net.net.xml").write_text("<net/>")
        (tmp_path / "calibrated.rou.xml").write_text(
            "<routes><vehicle id=\"pfe0\" depart=\"0\"><route edges=\"a b\"/>"
            "</vehicle></routes>")

    def test_builds_all_artifacts_on_first_call(self, tmp_path, monkeypatch):
        calls = []
        self._stub_tools(monkeypatch, tmp_path, calls)
        conditions = so.build_signal_conditions(
            tmp_path, [tmp_path / "calibrated.rou.xml"], 0, 900, "label1")
        assert {c[0] for c in calls} == {"adapt", "coord", "net", "regulation",
                                         "enforce", "demand_weighted"}
        assert set(conditions) == set(so.SIGNAL_CONDITION_NAMES)
        assert len(conditions) == so.SIGNAL_CONDITION_COUNT

    def test_demand_weighted_tls_is_built_with_the_analysis_window(self, tmp_path, monkeypatch):
        # Found in review 2026-07-12: build_demand_weighted_tls used to be
        # called with no begin_s/end_s at all, allocating green time from
        # route_path's FULL demand rather than the window being optimized.
        calls = []
        self._stub_tools(monkeypatch, tmp_path, calls)
        so.build_signal_conditions(
            tmp_path, [tmp_path / "calibrated.rou.xml"], 25200, 32400, "label1")
        demand_weighted_call = next(c for c in calls if c[0] == "demand_weighted")
        assert demand_weighted_call[2] == 25200   # begin_s
        assert demand_weighted_call[3] == 32400   # end_s

    def test_second_call_with_the_same_label_reuses_cached_artifacts(self, tmp_path, monkeypatch):
        calls = []
        self._stub_tools(monkeypatch, tmp_path, calls)
        so.build_signal_conditions(tmp_path, [tmp_path / "calibrated.rou.xml"], 0, 900, "label1")
        calls.clear()
        so.build_signal_conditions(tmp_path, [tmp_path / "calibrated.rou.xml"], 0, 900, "label1")
        assert calls == []   # nothing rebuilt -- all artifacts already exist

    def test_different_label_rebuilds_instead_of_reusing_stale_artifacts(self, tmp_path, monkeypatch):
        # The actual bug: a NEW demand/network (-> new label) must NOT
        # silently reuse the previous label's cached files.
        calls = []
        self._stub_tools(monkeypatch, tmp_path, calls)
        so.build_signal_conditions(tmp_path, [tmp_path / "calibrated.rou.xml"], 0, 900, "label1")
        calls.clear()
        so.build_signal_conditions(tmp_path, [tmp_path / "calibrated.rou.xml"], 0, 900, "label2")
        assert {c[0] for c in calls} == {"adapt", "coord", "net", "enforce", "demand_weighted"}

    def test_regulation_compliant_is_cached_by_net_fingerprint_not_label(self, tmp_path, monkeypatch):
        # Unlike adapted/coordinated/alt-nets, this artifact depends only
        # on network geometry (TSFS 2014:30 is demand/window-independent)
        # -- a label change alone must NOT rebuild it.
        calls = []
        self._stub_tools(monkeypatch, tmp_path, calls)
        so.build_signal_conditions(tmp_path, [tmp_path / "calibrated.rou.xml"], 0, 900, "label1")
        calls.clear()
        so.build_signal_conditions(tmp_path, [tmp_path / "calibrated.rou.xml"], 0, 900, "label2")
        assert "regulation" not in {c[0] for c in calls}

    def test_alt_net_with_changed_non_tls_structure_aborts(self, tmp_path, monkeypatch):
        # Found in review 2026-07-12: this abort path (a full netconvert
        # --tls.default-type rebuild perturbing something besides <tlLogic>)
        # had zero test coverage — only the standalone non_tls_network_
        # fingerprint helper was tested on hand-built fixtures, never this
        # comparison-and-sys.exit branch inside build_signal_conditions.
        calls = []
        self._stub_tools(monkeypatch, tmp_path, calls)

        def fake_alt_net_diverged(home, tls_type, out_path):
            calls.append(("net", tls_type, out_path))
            # "actuated" rebuild silently adds a real edge -- a genuine
            # non-TLS structural change netconvert must never introduce.
            content = ('<net><edge id="new_edge"/></net>' if tls_type == "actuated"
                      else "<net/>")
            out_path.write_text(content)

        monkeypatch.setattr(so, "build_alt_type_net", fake_alt_net_diverged)
        with pytest.raises(SystemExit, match="actuated.*non-TLS structure"):
            so.build_signal_conditions(
                tmp_path, [tmp_path / "calibrated.rou.xml"], 0, 900, "label1")

    def test_alt_net_with_identical_non_tls_structure_succeeds(self, tmp_path, monkeypatch):
        calls = []
        self._stub_tools(monkeypatch, tmp_path, calls)
        conditions = so.build_signal_conditions(
            tmp_path, [tmp_path / "calibrated.rou.xml"], 0, 900, "label1")
        assert set(conditions) == set(so.SIGNAL_CONDITION_NAMES)


class TestConditionTlsProvenance:
    """Found in review 2026-07-12: this used to special-case only
    "regulation_compliant" by name, silently labelling "adapted"/
    "adapted_coordinated"/"demand_weighted_coordinated" (which ALSO go
    through the TSFS-informed rebuild_phases() pipeline) the same as
    netconvert's raw arbitrary guess."""

    def test_every_tsfs_grounded_condition_gets_the_regulation_label(self):
        conditions = {name: {} for name in so.SIGNAL_CONDITION_NAMES}
        prov = so.condition_tls_provenance(conditions)
        for name in ("adapted", "adapted_coordinated", "regulation_compliant",
                    "demand_weighted_coordinated"):
            assert prov[name] == so.REGULATION_PROVENANCE

    def test_conditions_that_never_touch_tsfs_logic_stay_plain_synthetic(self):
        conditions = {name: {} for name in so.SIGNAL_CONDITION_NAMES}
        prov = so.condition_tls_provenance(conditions)
        for name in ("baseline", "actuated", "delay_based"):
            assert prov[name] == so.TLS_PROVENANCE

    def test_regulation_and_plain_provenance_are_distinct_values(self):
        # A future D6 flipping TLS_PROVENANCE to "city-configured" must not
        # accidentally make these two literally the same string again.
        assert so.REGULATION_PROVENANCE != so.TLS_PROVENANCE


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


class TestNonTlsNetworkFingerprint:
    def test_ignores_tls_program_changes_but_detects_network_changes(self, tmp_path):
        baseline = tmp_path / "baseline.net.xml"
        tls_variant = tmp_path / "tls_variant.net.xml"
        changed = tmp_path / "changed.net.xml"
        baseline.write_text("""<net><edge id="e" from="a" to="b"><lane id="e_0" length="10"/></edge><tlLogic id="J" type="static"><phase duration="20" state="Gr"/></tlLogic></net>""")
        tls_variant.write_text("""<net><edge id="e" from="a" to="b"><lane id="e_0" length="10"/></edge><tlLogic id="J" type="actuated"><phase duration="30" state="rG"/></tlLogic></net>""")
        changed.write_text("""<net><edge id="e" from="a" to="b"><lane id="e_0" length="11"/></edge><tlLogic id="J" type="actuated"><phase duration="30" state="rG"/></tlLogic></net>""")

        assert so.non_tls_network_fingerprint(baseline) == so.non_tls_network_fingerprint(tls_variant)
        assert so.non_tls_network_fingerprint(baseline) != so.non_tls_network_fingerprint(changed)
