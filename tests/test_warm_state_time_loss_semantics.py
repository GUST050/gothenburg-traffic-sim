"""Process-free tests for the saved-state time-loss diagnostic (LUNA-WARM-08 r2).

Every simulator interaction goes through an injected fake. These tests never
start SUMO, never import the real `traci`, and never touch `runs/` — the single
approved real execution is a separate, deliberate command.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOL = Path("tools/diagnose_warm_state_time_loss_semantics.py")


def _module():
    if "warm_semantics_tool" in sys.modules:
        return sys.modules["warm_semantics_tool"]
    spec = importlib.util.spec_from_file_location("warm_semantics_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["warm_semantics_tool"] = module
    spec.loader.exec_module(module)
    return module


D = _module()


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeTraci:
    """A scripted simulator. No socket, no process, no real traci."""

    def __init__(self, *, time_loss_at, arrive_at=140.0, present_from=0.0,
                 post_load_time_loss=None, start_time=0.0):
        self._time_loss_at = time_loss_at
        self._arrive_at = arrive_at
        self._present_from = present_from
        self._post_load = post_load_time_loss
        self.now = start_time
        self.saved = None
        self.closed = False
        self.speeds = []
        outer = self

        class _Sim:
            @staticmethod
            def getTime():
                return outer.now

            @staticmethod
            def getMinExpectedNumber():
                return 0 if outer.now > outer._arrive_at else 1

            @staticmethod
            def saveState(path):
                outer.saved = (path, outer.now)

        class _Veh:
            @staticmethod
            def getIDList():
                if outer._present_from <= outer.now <= outer._arrive_at:
                    return [D.VEHICLE_ID]
                return []

            @staticmethod
            def getTimeLoss(vehicle_id):
                if outer._post_load is not None and outer.now == outer.now \
                        and outer._first_read:
                    outer._first_read = False
                    return outer._post_load
                return outer._time_loss_at(outer.now)

            @staticmethod
            def setSpeed(vehicle_id, speed):
                outer.speeds.append((outer.now, speed))

        self._first_read = post_load_time_loss is not None
        self.simulation = _Sim()
        self.vehicle = _Veh()

    def init(self, port):
        self.port = port

    def simulationStep(self):
        self.now += D.STEP_LENGTH_S

    def close(self):
        self.closed = True


class FakeProcess:
    """The subprocess surface the runner actually relies on.

    It grew as the runner learned liveness checks, bounded waits and exit-code
    enforcement; a fake that lagged behind would leave those paths untested.
    """

    def __init__(self, returncode=0, poll_result=None, wait_raises=False,
                 stderr_text=""):
        self.waited = False
        self.killed = False
        self.returncode = returncode
        self._poll_result = poll_result
        self._wait_raises = wait_raises
        self.stderr_text = stderr_text

    def poll(self):
        return self._poll_result

    def wait(self, timeout=None):
        self.waited = True
        if self._wait_raises and timeout is not None:
            # Mirrors subprocess.TimeoutExpired: the bounded wait gives up.
            self._wait_raises = False
            raise RuntimeError("wait timed out")
        return self.returncode

    def kill(self):
        self.killed = True
        self._poll_result = self.returncode


def _runner(traci, process=None, **over):
    """A runner whose launcher writes the fake's stderr where the real one would."""
    process = process or FakeProcess()

    def launcher(cmd, *, cwd, port, stderr_path):
        Path(stderr_path).write_text(process.stderr_text, encoding="utf-8")
        return process

    return D.SumoRunner(traci_module=traci, launcher=launcher, port=54321,
                        sumo_binary="/fake/sumo", **over), process


# ── Contract and command ─────────────────────────────────────────────────────

class TestCommandContract:

    def _cmd(self, **over):
        base = dict(sumo_binary="/fake/sumo", network_path=Path("sumo/net.net.xml"),
                    route_path=Path("r.rou.xml"), tripinfo_path=Path("t.xml"),
                    end_s=400.0)
        base.update(over)
        return D.build_command(**base)

    def test_the_arms_differ_only_in_begin_and_load_state(self):
        cold = self._cmd()
        resumed = self._cmd(begin_s=D.SAVE_TIME_S,
                            load_state_path=Path("s.xml.gz"),
                            tripinfo_path=Path("t2.xml"))
        def without(cmd, *flags):
            out, skip = [], False
            for token in cmd:
                if skip:
                    skip = False
                    continue
                if token in flags:
                    skip = True
                    continue
                out.append(token)
            return out
        assert without(cold, "--begin", "--tripinfo-output") == \
            without(resumed, "--begin", "--tripinfo-output", "--load-state")

    def test_no_output_precision_is_ever_set(self):
        """The arms must measure the accumulator, not a precision flag."""
        assert "--precision" not in self._cmd()
        assert "--precision" not in self._cmd(begin_s=20.0,
                                              load_state_path=Path("s"))

    def test_the_step_length_and_seed_are_pinned(self):
        cmd = self._cmd()
        assert cmd[cmd.index("--step-length") + 1] == "1.00"
        assert cmd[cmd.index("--seed") + 1] == "1000"

    def test_teleporting_is_disabled_for_determinism(self):
        cmd = self._cmd()
        assert cmd[cmd.index("--time-to-teleport") + 1] == "-1"

    def test_only_the_resumed_arm_loads_state(self):
        assert "--load-state" not in self._cmd()
        assert "--load-state" in self._cmd(load_state_path=Path("s"))

    def test_the_builder_writes_nothing(self, tmp_path):
        target = tmp_path / "nope"
        self._cmd(tripinfo_path=target / "t.xml")
        assert not target.exists()


class TestRouteSelection:

    def test_the_route_is_one_vehicle_on_one_edge(self):
        xml = D.build_route_xml("a_b_0")
        assert xml.count("<vehicle") == 1
        assert 'edges="a_b_0"' in xml
        assert f'id="{D.VEHICLE_ID}"' in xml

    def _net(self, tmp_path, edges):
        path = tmp_path / "net.net.xml"
        body = "".join(
            f'<edge id="{eid}"{extra}>'
            f'<lane id="{eid}_0" length="{length}" speed="{speed}"{allow}/>'
            f'</edge>'
            for eid, length, speed, extra, allow in edges)
        path.write_text(f"<net>{body}</net>")
        return path

    def test_selection_is_lexicographic_not_file_order(self, tmp_path):
        net = self._net(tmp_path, [
            ("zzz_1", 300, 13.89, "", ""),
            ("aaa_1", 300, 13.89, "", ""),
        ])
        assert D.select_probe_edge(net)["edge_id"] == "aaa_1"

    def test_internal_edges_are_never_selected(self, tmp_path):
        net = self._net(tmp_path, [
            (":internal_0", 300, 13.89, ' function="internal"', ""),
            ("real_1", 300, 13.89, "", ""),
        ])
        assert D.select_probe_edge(net)["edge_id"] == "real_1"

    def test_edges_that_forbid_passengers_are_skipped(self, tmp_path):
        net = self._net(tmp_path, [
            ("aaa_bus", 300, 13.89, "", ' disallow="passenger"'),
            ("bbb_ok", 300, 13.89, "", ""),
        ])
        assert D.select_probe_edge(net)["edge_id"] == "bbb_ok"

    def test_an_allow_list_without_passenger_is_skipped(self, tmp_path):
        net = self._net(tmp_path, [
            ("aaa_rail", 300, 13.89, "", ' allow="rail"'),
            ("bbb_ok", 300, 13.89, "", ' allow="passenger bus"'),
        ])
        assert D.select_probe_edge(net)["edge_id"] == "bbb_ok"

    def test_short_edges_are_skipped(self, tmp_path):
        net = self._net(tmp_path, [
            ("aaa_short", 50, 13.89, "", ""),
            ("bbb_long", 300, 13.89, "", ""),
        ])
        assert D.select_probe_edge(net)["edge_id"] == "bbb_long"

    def test_edges_at_or_below_the_control_speed_are_skipped(self, tmp_path):
        """No speed headroom means no time loss, so the probe proves nothing."""
        net = self._net(tmp_path, [
            ("aaa_slow", 300, 1.0, "", ""),
            ("bbb_ok", 300, 13.89, "", ""),
        ])
        assert D.select_probe_edge(net)["edge_id"] == "bbb_ok"

    def test_no_usable_edge_fails_closed(self, tmp_path):
        net = self._net(tmp_path, [("aaa_short", 10, 13.89, "", "")])
        with pytest.raises(SystemExit, match="cannot be constructed"):
            D.select_probe_edge(net)

    def test_the_decision_records_how_many_candidates_there_were(self, tmp_path):
        net = self._net(tmp_path, [
            ("aaa_1", 300, 13.89, "", ""), ("bbb_1", 400, 13.89, "", "")])
        assert D.select_probe_edge(net)["candidate_count"] == 2


# ── Classification ───────────────────────────────────────────────────────────

class TestClassification:

    def _obs(self, **over):
        base = {"prefix_boundary_time_loss_s": 8.0,
                "post_load_time_loss_s": 0.0,
                "cold_final_tripinfo_time_loss_s": 60.0,
                "resumed_final_tripinfo_time_loss_s": 52.0}
        base.update(over)
        return base

    def test_a_reset_accumulator_is_post_load_segment_only(self):
        verdict = D.classify(self._obs())
        assert verdict["classification"] == D.POST_LOAD_SEGMENT_ONLY
        assert any("carried separately" in r for r in verdict["reasons"])

    def test_a_preserved_accumulator_is_reported_as_such(self):
        verdict = D.classify(self._obs(post_load_time_loss_s=8.0,
                                       resumed_final_tripinfo_time_loss_s=60.0))
        assert verdict["classification"] == D.FULL_ACCUMULATOR_PRESERVED
        assert any("DOUBLE COUNT" in r for r in verdict["reasons"])

    def test_disagreeing_signals_are_inconclusive(self):
        """Accumulator reset but final matching cold: the two signals conflict."""
        verdict = D.classify(self._obs(post_load_time_loss_s=0.0,
                                       resumed_final_tripinfo_time_loss_s=60.0))
        assert verdict["classification"] == D.INCONCLUSIVE
        assert any("do not agree" in r for r in verdict["reasons"])

    def test_an_unrecognised_pattern_is_inconclusive(self):
        verdict = D.classify(self._obs(post_load_time_loss_s=3.0,
                                       resumed_final_tripinfo_time_loss_s=17.0))
        assert verdict["classification"] == D.INCONCLUSIVE

    def test_the_vocabulary_is_closed(self):
        for obs in (self._obs(), self._obs(post_load_time_loss_s=8.0,
                                          resumed_final_tripinfo_time_loss_s=60.0),
                    self._obs(post_load_time_loss_s=3.0)):
            assert D.classify(obs)["classification"] in {
                D.FULL_ACCUMULATOR_PRESERVED, D.POST_LOAD_SEGMENT_ONLY,
                D.INCONCLUSIVE}

    def test_every_verdict_records_its_signals_and_epsilon(self):
        verdict = D.classify(self._obs())
        assert set(verdict["signals"]) == {
            "post_load_equals_boundary", "post_load_is_zero",
            "resumed_final_equals_cold_final",
            "resumed_final_equals_cold_minus_boundary"}
        assert verdict["epsilon_s"] == D.CLASSIFY_EPSILON_S


# ── Arm execution against the fake ───────────────────────────────────────────

class TestArmExecution:

    def test_the_prefix_arm_captures_and_saves_at_the_same_step(self, tmp_path):
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4)
        runner, process = _runner(traci)
        observed = runner.run_arm(label="probe", cmd=["sumo"], cwd=tmp_path,
                                  capture_at_s=D.SAVE_TIME_S,
                                  save_state_path=tmp_path / "s.gz")
        assert observed["capture_time_s"] == D.SAVE_TIME_S
        saved_path, saved_time = traci.saved
        assert saved_time == D.SAVE_TIME_S == observed["saved_at_s"]
        assert observed["captured_time_loss_s"] == pytest.approx(8.0)

    def test_the_control_speed_is_applied_every_step(self, tmp_path):
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4, arrive_at=5.0)
        runner, _ = _runner(traci)
        runner.run_arm(label="probe", cmd=["sumo"], cwd=tmp_path)
        assert traci.speeds, "the probe was never controlled"
        assert {speed for _, speed in traci.speeds} == {D.CONTROL_SPEED_MPS}

    def test_an_absent_probe_at_the_capture_step_fails_closed(self, tmp_path):
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4, present_from=100.0)
        runner, _ = _runner(traci)
        with pytest.raises(SystemExit, match="not active at the capture step"):
            runner.run_arm(label="probe", cmd=["sumo"], cwd=tmp_path,
                           capture_at_s=D.SAVE_TIME_S,
                           save_state_path=tmp_path / "s.gz")

    def test_the_process_is_always_reaped_and_the_connection_closed(self, tmp_path):
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4, present_from=100.0)
        runner, process = _runner(traci)
        with pytest.raises(SystemExit):
            runner.run_arm(label="probe", cmd=["sumo"], cwd=tmp_path,
                           capture_at_s=D.SAVE_TIME_S,
                           save_state_path=tmp_path / "s.gz")
        assert traci.closed and process.waited

    def test_a_sumo_that_died_before_connecting_is_reported_not_hung(
            self, tmp_path):
        """A rejected command line exits at once; connecting to a dead process
        is what turns a bad argv into a hang instead of an error."""
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4)
        dead = FakeProcess(returncode=1, poll_result=1,
                           stderr_text="Error: unknown option")
        runner, _ = _runner(traci, dead)
        with pytest.raises(SystemExit, match="unknown option"):
            runner.run_arm(label="probe", cmd=["sumo", "--bogus"], cwd=tmp_path)

    def test_the_resumed_arm_reads_the_accumulator_before_stepping(self, tmp_path):
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4,
                          post_load_time_loss=8.0, start_time=D.SAVE_TIME_S)
        runner, _ = _runner(traci)
        observed = runner.run_arm(label="probe", cmd=["sumo"], cwd=tmp_path,
                                  capture_after_load=True)
        assert observed["post_load_time_s"] == D.SAVE_TIME_S
        assert observed["post_load_present"] is True
        assert observed["post_load_time_loss_s"] == 8.0


# ── Preflight and output ─────────────────────────────────────────────────────

class TestExitCodeEnforcement:
    """Criterion 2-3. v1's evidence was rejected because the arms' return codes
    were never looked at, and an unchecked exit is indistinguishable from a
    clean one."""

    def test_a_clean_arm_records_an_observed_zero(self, tmp_path):
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4, arrive_at=5.0)
        runner, process = _runner(traci, FakeProcess(returncode=0))
        observed = runner.run_arm(label="cold", cmd=["sumo"], cwd=tmp_path)
        assert observed["return_code"] == 0
        assert observed["killed_during_cleanup"] is False
        assert process.waited, "the bounded wait never ran"

    def test_a_nonzero_exit_after_a_normal_completion_fails_closed(self, tmp_path):
        """The exact hole in v1: the arm ran to completion and SUMO still failed."""
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4, arrive_at=5.0)
        runner, _ = _runner(traci, FakeProcess(
            returncode=3, stderr_text="Error: could not write output"))
        with pytest.raises(SystemExit, match="return code 3"):
            runner.run_arm(label="cold", cmd=["sumo"], cwd=tmp_path)

    def test_the_failure_carries_bounded_stderr_context(self, tmp_path):
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4, arrive_at=5.0)
        runner, _ = _runner(traci, FakeProcess(
            returncode=3, stderr_text="X" * 5000 + "TAIL-MARKER"))
        with pytest.raises(SystemExit) as caught:
            runner.run_arm(label="cold", cmd=["sumo"], cwd=tmp_path)
        message = str(caught.value)
        assert "TAIL-MARKER" in message
        assert len(message) < 3000, "stderr context must be bounded"

    def test_a_wait_timeout_kills_reaps_and_fails(self, tmp_path):
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4, arrive_at=5.0)
        stuck = FakeProcess(returncode=0, wait_raises=True)
        runner, _ = _runner(traci, stuck)
        with pytest.raises(SystemExit, match="killed during cleanup"):
            runner.run_arm(label="cold", cmd=["sumo"], cwd=tmp_path)
        assert stuck.killed and stuck.waited

    def test_a_body_failure_is_not_masked_by_the_exit_code_check(self, tmp_path):
        """The primary error must survive cleanup, not be replaced by a
        complaint about a process we already knew had failed."""
        traci = FakeTraci(time_loss_at=lambda t: t * 0.4, present_from=100.0)
        runner, process = _runner(traci, FakeProcess(returncode=9))
        with pytest.raises(SystemExit, match="not active at the capture step"):
            runner.run_arm(label="prefix", cmd=["sumo"], cwd=tmp_path,
                           capture_at_s=D.SAVE_TIME_S,
                           save_state_path=tmp_path / "s.gz")
        # Cleanup still happened; it simply did not speak over the real error.
        assert process.waited

    def test_the_canonical_result_requires_three_explicit_zeros(self):
        """Structural: the guard names all three arms and demands 0 from each."""
        code = TOOL.read_text()
        body = code.split("return_codes = {")[1].split("verdict = classify")[0]
        for arm in ("cold", "prefix", "resumed"):
            assert f'"{arm}"' in body, arm
        assert "three explicit zero exits" in body
        assert '{"cold": 0, "prefix": 0,' in code, (
            "the stored result must be re-verified against three zeros")


class TestPreflight:

    def _runner_stub(self, tmp_path):
        class Stub:
            def sumo_binary(self):
                return tmp_path / "sumo"

            def sumo_version(self):
                return "Eclipse SUMO sumo 1.27.1"

            def traci(self):
                return object()
        return Stub()

    def test_an_unapproved_network_is_refused(self, tmp_path):
        with pytest.raises(SystemExit, match="approved tracked network"):
            D.preflight(network="other/net.xml",
                        output_root=D.APPROVED_OUTPUT_ROOT,
                        runner=self._runner_stub(tmp_path))

    def test_an_unapproved_output_root_is_refused(self, tmp_path):
        with pytest.raises(SystemExit, match="approved outcome root"):
            D.preflight(network=D.APPROVED_NETWORK, output_root="validation/other",
                        runner=self._runner_stub(tmp_path))

    def test_an_existing_outcome_root_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(D, "ROOT", tmp_path)
        (tmp_path / D.APPROVED_OUTPUT_ROOT).mkdir(parents=True)
        monkeypatch.setattr(D, "select_probe_edge", lambda p: {})
        monkeypatch.setattr(Path, "is_file", lambda self: True)
        with pytest.raises(SystemExit, match="never reused"):
            D.preflight(network=D.APPROVED_NETWORK,
                        output_root=D.APPROVED_OUTPUT_ROOT,
                        runner=self._runner_stub(tmp_path))


class TestCanonicalOutput:

    def test_the_manifest_refuses_a_missing_member(self, tmp_path):
        with pytest.raises(SystemExit, match="member is missing"):
            D.member_manifest(tmp_path, members=("contract.json",))

    def test_the_manifest_digests_every_member_in_a_fixed_order(self, tmp_path):
        for name in ("a", "b"):
            (tmp_path / name).write_text(name)
        manifest = D.member_manifest(tmp_path, members=("a", "b"))
        assert list(manifest) == ["a", "b"]
        assert all({"sha256", "bytes"} == set(v) for v in manifest.values())

    def test_the_content_key_excludes_itself_and_is_order_stable(self):
        left = {"b": 2, "a": 1}
        right = {"a": 1, "b": 2}
        assert D.canonical_key(left) == D.canonical_key(right)
        assert D.canonical_key(dict(left, content_key="x")) == \
            D.canonical_key(left)

    def test_tripinfo_parsing_refuses_duplicates_and_malformed_records(
            self, tmp_path):
        good = tmp_path / "g.xml"
        good.write_text('<tripinfos>\n<tripinfo id="v" timeLoss="1.50"/>\n'
                        '</tripinfos>\n')
        assert D.parse_tripinfo_time_loss(good) == {"v": 1.5}
        dup = tmp_path / "d.xml"
        dup.write_text('<tripinfos>\n<tripinfo id="v" timeLoss="1.0"/>\n'
                       '<tripinfo id="v" timeLoss="2.0"/>\n</tripinfos>\n')
        with pytest.raises(SystemExit, match="duplicate tripinfo"):
            D.parse_tripinfo_time_loss(dup)
        bad = tmp_path / "b.xml"
        bad.write_text('<tripinfos>\n<tripinfo id="v"/>\n</tripinfos>\n')
        with pytest.raises(SystemExit, match="lacks id/timeLoss"):
            D.parse_tripinfo_time_loss(bad)

    def test_the_container_tag_is_not_read_as_a_record(self, tmp_path):
        path = tmp_path / "t.xml"
        path.write_text('<tripinfos>\n<tripinfo id="v" timeLoss="1.00"/>\n'
                        '</tripinfos>\n')
        assert D.parse_tripinfo_time_loss(path) == {"v": 1.0}

    def test_the_contract_declares_its_own_claim_scope(self):
        contract = D.build_contract(
            network_sha256="0" * 64, sumo_version="Eclipse SUMO sumo 1.27.1",
            probe={"edge_id": "a_b_0"}, source_fingerprints={},
            command_shapes={})
        assert "not equivalence evidence" in contract["claim_scope"]
        assert D.canonical_key(contract) == contract["content_key"]


class TestCliContract:

    def test_exactly_one_mode_is_required(self):
        with pytest.raises(SystemExit, match="exactly one"):
            D.main([])
        with pytest.raises(SystemExit, match="exactly one"):
            D.main(["--preflight", "--execute"])


class TestProcessFree:

    def test_no_simulator_is_imported_at_module_scope(self):
        import ast
        tree = ast.parse(TOOL.read_text())
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                top.add(node.module.split(".")[0])
        assert not top & {"traci", "libsumo", "socket", "subprocess"}, sorted(top)

    def test_this_suite_imports_no_real_simulator(self):
        assert "traci" not in sys.modules and "libsumo" not in sys.modules

    def test_the_tool_names_no_forbidden_location_in_code(self):
        """Code only. The docstring DISCUSSES the forbidden location — that is
        the documented boundary, not a violation."""
        import ast
        import io
        import tokenize
        text = TOOL.read_text()
        tree = ast.parse(text)
        docstrings = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add((body[0].value.lineno, body[0].value.col_offset))
        kept = []
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING and token.start in docstrings:
                continue
            kept.append(token.string)
        code = "\n".join(kept)
        assert "run" + "s/" not in code, "must never reference that path in code"

    def test_constructing_the_runner_starts_nothing(self):
        D.SumoRunner()
        assert "traci" not in sys.modules
