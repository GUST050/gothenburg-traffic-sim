"""The population-scale warm boundary diagnostic (LUNA-WARM-17).

Process-free by construction: the tool under test is pure — it builds fixtures,
argv and verdicts, and never runs anything. These tests prove that, prove the
fixture really produces the three cohorts a boundary creates, and prove the
classifier cannot be talked into a mechanism the observations do not establish.

The diagnostic is FROZEN, UNAPPROVED and UNEXECUTED. Nothing here executes it.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "tools")
import diagnose_warm_state_population_semantics as diag  # noqa: E402

CONTRACT = Path("validation/warm_state_population_semantics_v2_contract.json")
V1_CONTRACT = Path("validation/warm_state_population_semantics_v1_contract.json")
TOOL = Path("tools/diagnose_warm_state_population_semantics.py")


def _cohorts():
    return diag.cohort_index(diag.build_population())


def _ids(cohort):
    return [v["id"] for v in diag.build_population() if v["cohort"] == cohort]


def _split_from(cold, boundary_arm_ids):
    """Split a cold mapping into (prefix, resumed) by vehicle id."""
    prefix = {k: v for k, v in cold.items() if k in boundary_arm_ids}
    resumed = {k: v for k, v in cold.items() if k not in boundary_arm_ids}
    return prefix, resumed


def _record(vehicle, index, *, time_loss=None):
    """A full tripinfo record whose depart/arrival put the vehicle in the
    cohort the fixture DECLARED for it, so tests exercise agreement by default
    and disagreement only when they ask for it."""
    boundary = diag.SAVE_TIME_S
    if vehicle["cohort"] == "completed_before_boundary":
        depart, arrival = 0.0 + index * 0.1, boundary - 5.0
    elif vehicle["cohort"] == "active_at_boundary":
        depart, arrival = 0.0 + index * 0.1, boundary + 50.0
    else:
        depart, arrival = boundary + 10.0, boundary + 60.0
    return {"id": vehicle["id"], "depart": depart, "arrival": arrival,
            "duration": arrival - depart, "routeLength": 302.42,
            "waitingTime": 0.0, "waitingCount": 0.0,
            "timeLoss": 10.0 + index * 0.37 if time_loss is None else time_loss}


def _ports(start=45000):
    """A deterministic stand-in for `free_port()`.

    The real allocator binds a socket, which is genuine socket activity and
    correctly refused by this task's audit guard — so it is injected, exactly
    like the runner, and the production path keeps the real one.
    """
    counter = iter(range(start, start + 100))
    return lambda: next(counter)


def _arm_records():
    cold = _perfect_cold()
    pre = set(_ids("completed_before_boundary"))
    prefix = {k: v for k, v in cold.items() if k in pre}
    resumed = {k: v for k, v in cold.items() if k not in pre}
    return {"cold": cold, "prefix": prefix, "resumed": resumed,
            "cold_control": cold, "prefix_control": prefix,
            "resumed_control": resumed}


def _boundary_facts():
    cold = _perfect_cold()
    active = sorted(k for k, v in diag.observed_cohorts(cold).items()
                    if v == "active_at_boundary")
    block = {"captured_at_s": diag.SAVE_TIME_S, "active_vehicle_ids": active,
             "active_vehicle_count": len(active), "state_sha256": "a" * 64}
    return {"prefix": dict(block), "prefix_control": dict(block)}


def _perfect_cold():
    """A cold result with a distinct, non-trivial value per vehicle."""
    return {v["id"]: _record(v, index)
            for index, v in enumerate(diag.build_population())}


def _compare(cold, prefix, resumed):
    return diag.compare_population(cold=cold, prefix=prefix, resumed=resumed,
                                   declared_cohorts=_cohorts())


class TestProcessFreeBoundary:
    """Criterion 1: importing and exercising this must touch nothing."""

    def test_importing_the_tool_imports_no_simulator(self):
        assert "traci" not in sys.modules and "libsumo" not in sys.modules

    def test_the_tool_imports_no_simulator_or_process_module_at_module_scope(self):
        tree = ast.parse(TOOL.read_text())
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                top.add(node.module.split(".")[0])
        assert not top & {"traci", "libsumo", "socket", "subprocess",
                          "multiprocessing"}, sorted(top)

    def test_process_and_simulator_use_is_confined_behind_the_gate(self):
        """Structural, not a substring ban.

        The runner legitimately uses `subprocess` and TraCI — it has to, to be a
        complete pipeline. What must stay true is stronger and checkable: those
        names appear ONLY inside the runner functions, never at module scope,
        and the entry point checks the approval token before anything else runs.

        My earlier version banned the substring `subprocess.` outright, which
        was fine while the runner was crippled and wrong the moment it worked.
        """
        tree = ast.parse(TOOL.read_text())
        runner_functions = {"run_arm", "run_prefix_atomic", "execute"}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.dump(node)
            touches = any(name in body for name in
                          ("subprocess", "traci", "Popen", "resolve_traci"))
            if touches:
                assert node.name in runner_functions, node.name

        entry = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "execute")
        first = entry.body[0]
        while isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            entry.body.pop(0)
            first = entry.body[0]
        assert "require_approval" in ast.dump(first), (
            "the gate must be the first thing execute() does")

    def test_the_tool_names_no_runs_path(self):
        for node in ast.walk(ast.parse(TOOL.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not node.value.startswith("runs/"), node.value

    def test_no_output_root_is_created_by_freezing_or_verifying(self):
        """The contract NAMES where a later run would write; it must not make it."""
        assert not Path(diag.APPROVED_OUTPUT_ROOT).exists()

    def test_the_runner_is_complete_and_gated_not_crippled(self):
        """Sol review: a permanently-dead runner is not a dormant pipeline.

        Enabling it later would mean editing this file — a BOUND source — which
        would change the very key an approval named. So the runner is complete
        and refused by a gate, not by absence.
        """
        assert callable(diag.execute)
        assert callable(diag.run_arm)
        assert callable(diag.run_prefix_atomic)
        with pytest.raises(SystemExit, match="frozen, unapproved and unexecuted"):
            diag.execute()

    def test_a_wrong_token_is_refused(self):
        with pytest.raises(SystemExit, match="does not match the frozen"):
            diag.execute(approval_token="not-the-key")

    def test_the_gate_accepts_only_the_exact_frozen_key(self):
        key = json.loads(CONTRACT.read_text())["content_key"]
        assert diag.require_approval(key) == key
        with pytest.raises(SystemExit):
            diag.require_approval(key[:-1] + ("0" if key[-1] != "0" else "1"))

    def test_simulator_imports_are_lazy_and_sit_behind_the_gate(self):
        """Importing this module must touch nothing; the runner's imports live
        inside functions, after the token check."""
        assert "traci" not in sys.modules and "libsumo" not in sys.modules
        tree = ast.parse(TOOL.read_text())
        for node in tree.body:
            assert not isinstance(node, (ast.Import, ast.ImportFrom)) or \
                "subprocess" not in ast.dump(node), ast.dump(node)
        gate = ast.dump(ast.parse(TOOL.read_text()))
        assert "require_approval" in gate

    def test_the_cli_offers_no_execute_mode(self):
        with pytest.raises(SystemExit):
            diag.main(["--execute"])


class TestTheFixtureReallyHasThreeCohorts:
    """Criterion 2: cohorts by construction, not by luck."""

    def test_every_cohort_is_non_empty(self):
        counts = {c: len(_ids(c)) for c in diag.COHORTS}
        assert all(counts.values()), counts

    def test_every_vehicle_belongs_to_exactly_one_cohort(self):
        population = diag.build_population()
        ids = [v["id"] for v in population]
        assert len(ids) == len(set(ids)), "duplicate vehicle id"
        assert {v["cohort"] for v in population} == set(diag.COHORTS)

    def test_the_active_cohort_is_large_enough_to_scale(self):
        """A one-vehicle fixture cannot show a population-scaled residual —
        that is exactly why the v2 diagnostic could not answer this."""
        assert len(_ids("active_at_boundary")) >= 8

    def test_the_fast_cohort_completes_before_the_boundary(self):
        probe = diag.select_probe_edge(diag.ROOT / diag.APPROVED_NETWORK)
        for vehicle in diag.build_population():
            if vehicle["cohort"] != "completed_before_boundary":
                continue
            arrival = vehicle["depart_s"] + probe["fast_travel_s"]
            assert arrival < diag.SAVE_TIME_S, (vehicle["id"], arrival)

    def test_the_active_cohort_straddles_the_boundary(self):
        probe = diag.select_probe_edge(diag.ROOT / diag.APPROVED_NETWORK)
        for vehicle in diag.build_population():
            if vehicle["cohort"] != "active_at_boundary":
                continue
            assert vehicle["depart_s"] < diag.SAVE_TIME_S
            assert vehicle["depart_s"] + probe["slow_travel_s"] > diag.SAVE_TIME_S

    def test_the_post_cohort_departs_after_the_boundary(self):
        for vehicle in diag.build_population():
            if vehicle["cohort"] == "depart_after_boundary":
                assert vehicle["depart_s"] > diag.SAVE_TIME_S, vehicle["id"]

    def test_every_vehicle_finishes_before_the_end(self):
        """An unfinished trip is excluded from tripinfo, which would silently
        break the partition rather than fail it."""
        probe = diag.select_probe_edge(diag.ROOT / diag.APPROVED_NETWORK)
        for vehicle in diag.build_population():
            travel = (probe["fast_travel_s"]
                      if vehicle["max_speed_mps"] == diag.FAST_SPEED_MPS
                      else probe["slow_travel_s"])
            assert vehicle["depart_s"] + travel < diag.END_TIME_S, vehicle["id"]

    def test_the_edge_selection_is_deterministic(self):
        first = diag.select_probe_edge(diag.ROOT / diag.APPROVED_NETWORK)
        second = diag.select_probe_edge(diag.ROOT / diag.APPROVED_NETWORK)
        assert first == second
        assert first["candidate_count"] >= 1

    def test_an_unusable_network_fails_closed(self, tmp_path):
        empty = tmp_path / "net.xml"
        empty.write_text("<net/>")
        with pytest.raises(SystemExit, match="no passenger-capable"):
            diag.select_probe_edge(empty)

    def test_the_route_file_covers_every_vehicle(self):
        population = diag.build_population()
        xml = diag.build_route_xml("edge-1", population)
        for vehicle in population:
            assert f'id="{vehicle["id"]}"' in xml
        assert xml.count("<vehicle ") == len(population)


class TestCommandShapes:
    """Criterion 3: arms differ only in declared ways."""

    def _cmd(self, **over):
        kwargs = dict(sumo_binary="sumo", network_path="n.net.xml",
                      route_path="r.rou.xml", tripinfo_path="t.xml")
        kwargs.update(over)
        return diag.build_command(**kwargs)

    def test_the_snapshot_flags_come_from_production(self):
        from traffic_sim.simulation.warm_state_boundary import (
            snapshot_settings_arguments)
        cmd = self._cmd(save_state_at=30.0, controller_owned=True,
                        remote_port=12345)
        for token in snapshot_settings_arguments():
            assert token in cmd, token

    def test_the_snapshot_settings_are_not_duplicated_as_literals(self):
        """If production's constants move, this diagnostic must move too.

        Scoped to CODE literals, excluding docstrings: the module docstring
        legitimately names `--save-state.rng true` when explaining which
        hypothesis v9 refuted. A raw substring ban would forbid describing the
        history, which is not what "do not duplicate the constant" means.
        """
        source = TOOL.read_text()
        assert "snapshot_settings_arguments()" in source
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and \
                        isinstance(body[0].value, ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or id(node) in docstrings:
                continue
            if isinstance(node.value, str):
                # `--save-state.times` and `.files` are FIXTURE parameters (the
                # instant and the path) and belong here. The RNG and precision
                # settings are production constants and must not be restated.
                assert node.value not in ("--save-state.rng",
                                          "--save-state.precision"), node.value

    def test_production_arms_set_no_precision_flag(self):
        assert "--precision" not in self._cmd()

    def test_control_arms_set_exactly_one_precision_flag(self):
        cmd = self._cmd(precision=diag.CONTROL_PRECISION)
        assert cmd.count("--precision") == 1
        assert cmd[cmd.index("--precision") + 1] == str(diag.CONTROL_PRECISION)

    def test_only_declared_differences_separate_cold_from_its_control(self):
        cold = self._cmd()
        control = self._cmd(precision=diag.CONTROL_PRECISION)
        assert control[:len(cold)] == cold or set(cold) <= set(control)
        assert set(control) - set(cold) == {"--precision",
                                            str(diag.CONTROL_PRECISION)}

    def test_a_controller_arm_without_a_port_fails_closed(self):
        """Review round 3: `traci.init` on a port SUMO never opened. Without
        `--remote-port` no TraCI server exists and the run dies before the
        boundary."""
        with pytest.raises(SystemExit, match="needs a --remote-port"):
            self._cmd(save_state_at=30.0, controller_owned=True)

    def test_a_controller_arm_without_a_boundary_fails_closed(self):
        with pytest.raises(SystemExit, match="must declare its boundary"):
            self._cmd(controller_owned=True, remote_port=12345)

    def test_the_controller_owns_the_save_so_the_cli_schedules_none(self):
        """Production carries the snapshot SETTINGS and no schedule; the state
        is written once, by the connected controller."""
        cmd = self._cmd(save_state_at=30.0, controller_owned=True,
                        remote_port=12345)
        assert "--save-state.times" not in cmd
        assert "--save-state.files" not in cmd
        assert "--save-state.rng" in cmd

    def test_determinism_flags_are_present_on_every_arm(self):
        for arm in (self._cmd(), self._cmd(load_state_path="s.xml"),
                    self._cmd(precision=12)):
            assert "--time-to-teleport" in arm and "--random" in arm
            assert arm[arm.index("--seed") + 1] == str(diag.SEED)

    def test_every_arm_runs_the_production_mesoscopic_core(self):
        """Sol review: omitting these measured a different simulator than the
        one whose residual is under investigation."""
        for arm in diag.ARMS:
            cmd = diag.frozen_command(arm)
            for token in diag.MESO_ARGUMENTS:
                assert token in cmd, (arm, token)

    def test_prefix_arms_end_after_the_boundary_so_the_state_is_written(self):
        """Sol review: SUMO's --end is exclusive, so a save AT --end never
        happens and the prefix would produce no snapshot at all."""
        specs = diag.arm_specifications()
        for arm in ("prefix", "prefix_control"):
            assert specs[arm]["end_s"] > diag.SAVE_TIME_S, arm
            assert specs[arm]["end_s"] == diag.SAVE_TIME_S + diag.PREFIX_FLUSH_S

    def test_a_save_at_or_after_end_is_refused(self):
        with pytest.raises(SystemExit, match="not strictly before --end"):
            self._cmd(end_s=30.0, save_state_at=30.0, controller_owned=True,
                      remote_port=12345)

    def test_only_prefix_arms_exclude_unfinished_trips(self):
        """Production's exact rule. Dropping unfinished trips from a cold or
        resumed arm would break the partition silently instead of loudly."""
        specs = diag.arm_specifications()
        for arm, spec in specs.items():
            expected = arm.startswith("prefix")
            assert spec["write_unfinished"] is not expected, arm
        for arm in ("cold", "resumed"):
            cmd = diag.frozen_command(arm)
            index = cmd.index("--tripinfo-output.write-unfinished")
            assert cmd[index + 1] == "true", arm
        for arm in ("prefix", "prefix_control"):
            cmd = diag.frozen_command(arm)
            index = cmd.index("--tripinfo-output.write-unfinished")
            assert cmd[index + 1] == "false", arm

    def test_the_frozen_argv_is_exact_not_prose(self):
        """The contract records executable argv, so command drift is detectable
        rather than a matter of interpretation."""
        frozen = json.loads(CONTRACT.read_text())["command_contract"]["exact_argv"]
        assert set(frozen) == set(diag.ARMS)
        for arm in diag.ARMS:
            assert frozen[arm] == diag.frozen_command(arm), arm

    def test_command_drift_would_be_detected(self):
        frozen = json.loads(CONTRACT.read_text())["command_contract"]["exact_argv"]
        mutated = list(frozen["cold"]) + ["--extra-flag", "1"]
        assert mutated != diag.frozen_command("cold")

    def test_every_declared_arm_has_a_specification(self):
        specs = diag.arm_specifications()
        assert set(specs) == set(diag.ARMS)
        assert {s["precision"] for s in specs.values()} == {
            None, diag.CONTROL_PRECISION}


class TestParsing:
    """Criterion 4: the FULL record, or nothing."""

    FULL = ('depart="1.00" arrival="20.00" duration="19.00" '
            'routeLength="302.42" waitingTime="0.00" waitingCount="0" ')

    def _write(self, tmp_path, body):
        path = tmp_path / "tripinfo.xml"
        path.write_text(f"<tripinfos>\n{body}\n</tripinfos>\n")
        return path

    def test_a_well_formed_file_parses_every_field(self, tmp_path):
        path = self._write(
            tmp_path, f'<tripinfo id="a" {self.FULL}timeLoss="1.50"/>')
        record = diag.parse_tripinfo(path)["a"]
        for field in diag.TRIPINFO_FIELDS:
            assert field in record, field
        assert record["timeLoss"] == 1.5
        assert record["depart"] == 1.0 and record["arrival"] == 20.0

    def test_a_record_missing_a_required_field_fails_closed(self, tmp_path):
        """The objective alone cannot establish cohort membership."""
        path = self._write(tmp_path, '<tripinfo id="a" timeLoss="1.50"/>')
        with pytest.raises(SystemExit, match="has no"):
            diag.parse_tripinfo(path)

    def test_a_record_without_an_id_fails_closed(self, tmp_path):
        path = self._write(tmp_path, f'<tripinfo {self.FULL}timeLoss="1.0"/>')
        with pytest.raises(SystemExit, match="has no id"):
            diag.parse_tripinfo(path)

    def test_a_duplicate_record_fails_closed(self, tmp_path):
        path = self._write(
            tmp_path,
            f'<tripinfo id="a" {self.FULL}timeLoss="1.0"/>\n'
            f'<tripinfo id="a" {self.FULL}timeLoss="2.0"/>')
        with pytest.raises(SystemExit, match="duplicate tripinfo"):
            diag.parse_tripinfo(path)

    def test_an_unparsable_value_fails_closed(self, tmp_path):
        path = self._write(
            tmp_path, f'<tripinfo id="a" {self.FULL}timeLoss="abc"/>')
        with pytest.raises(SystemExit, match="unparsable"):
            diag.parse_tripinfo(path)

    def test_a_non_finite_value_fails_closed(self, tmp_path):
        path = self._write(
            tmp_path, f'<tripinfo id="a" {self.FULL}timeLoss="nan"/>')
        with pytest.raises(SystemExit, match="non-finite"):
            diag.parse_tripinfo(path)

    def test_an_empty_file_fails_closed(self, tmp_path):
        path = self._write(tmp_path, "")
        with pytest.raises(SystemExit, match="no tripinfo records"):
            diag.parse_tripinfo(path)


class TestTheV1CounterexampleIsFixed:
    """The bytes that spent v1's one approved execution.

    SUMO writes its configuration as an XML COMMENT listing the options it ran
    with. v1 scanned for the text `<tripinfo\b`; a word boundary sits between
    `tripinfo` and `-`, so `<tripinfo-output .../>` and
    `<tripinfo-output.write-unfinished .../>` both scanned as records with no
    id, and the run died on its first arm.
    """

    HEADER = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!-- generated on 2026-07-31 by Eclipse SUMO sumo Version 1.27.1\n'
        '<configuration>\n'
        '    <output>\n'
        '        <tripinfo-output value="cold_tripinfo.xml"/>\n'
        '        <tripinfo-output.write-unfinished value="true"/>\n'
        '    </output>\n'
        '</configuration>\n'
        '-->\n')

    RECORD = ('<tripinfo id="pre-000" depart="1.00" arrival="20.00" '
              'duration="19.00" routeLength="302.42" waitingTime="0.00" '
              'waitingCount="0" timeLoss="0.12"/>')

    def _write(self, tmp_path, body):
        path = tmp_path / "cold_tripinfo.xml"
        path.write_text(f"{self.HEADER}<tripinfos>\n{body}\n</tripinfos>\n")
        return path

    def test_the_v1_rule_really_did_misfire_on_this_header(self):
        """Reproduce the defect against the OLD rule, so the fix is anchored to
        the actual failure rather than to my reconstruction of it."""
        import re
        old = re.compile(r'<tripinfo\b([^>]*)/?>')
        matched = [m.group(0) for m in old.finditer(self.HEADER)]
        assert len(matched) == 2, matched
        assert all('id="' not in m for m in matched)

    def test_the_real_record_survives_the_header_unchanged(self, tmp_path):
        record = diag.parse_tripinfo(self._write(tmp_path, self.RECORD))
        assert set(record) == {"pre-000"}, record
        assert record["pre-000"]["timeLoss"] == 0.12
        assert record["pre-000"]["depart"] == 1.0
        assert record["pre-000"]["arrival"] == 20.0

    def test_a_header_only_file_reports_no_records(self, tmp_path):
        path = tmp_path / "cold_tripinfo.xml"
        path.write_text(f"{self.HEADER}<tripinfos>\n</tripinfos>\n")
        with pytest.raises(SystemExit, match="no tripinfo records"):
            diag.parse_tripinfo(path)

    def test_malformed_xml_fails_closed(self, tmp_path):
        path = tmp_path / "cold_tripinfo.xml"
        path.write_text("<tripinfos><tripinfo id='a'</tripinfos>")
        with pytest.raises(SystemExit, match="malformed XML"):
            diag.parse_tripinfo(path)

    def test_a_record_with_no_id_still_fails_closed(self, tmp_path):
        body = ('<tripinfo depart="1.00" arrival="2.00" duration="1.00" '
                'routeLength="1.00" waitingTime="0.00" waitingCount="0" '
                'timeLoss="0.10"/>')
        with pytest.raises(SystemExit, match="has no id"):
            diag.parse_tripinfo(self._write(tmp_path, body))

    def test_duplicate_ids_still_fail_closed(self, tmp_path):
        with pytest.raises(SystemExit, match="duplicate tripinfo"):
            diag.parse_tripinfo(
                self._write(tmp_path, self.RECORD + "\n" + self.RECORD))

    def test_a_missing_required_field_still_fails_closed(self, tmp_path):
        body = '<tripinfo id="a" timeLoss="0.10"/>'
        with pytest.raises(SystemExit, match="has no"):
            diag.parse_tripinfo(self._write(tmp_path, body))

    def test_a_nonfinite_value_still_fails_closed(self, tmp_path):
        with pytest.raises(SystemExit, match="non-finite"):
            diag.parse_tripinfo(
                self._write(tmp_path, self.RECORD.replace('"0.12"', '"nan"')))

    def test_a_nonnumeric_value_still_fails_closed(self, tmp_path):
        with pytest.raises(SystemExit, match="unparsable"):
            diag.parse_tripinfo(
                self._write(tmp_path, self.RECORD.replace('"0.12"', '"abc"')))

    def test_a_nested_child_element_does_not_confuse_the_parser(self, tmp_path):
        """The container form: a record with `<emissions/>` inside it."""
        body = self.RECORD.replace("/>", ">") + '<emissions CO_abs="0"/></tripinfo>'
        record = diag.parse_tripinfo(self._write(tmp_path, body))
        assert set(record) == {"pre-000"}


class TestObservedCohortsBeatIdealArithmetic:
    """Sol review: ideal length/speed maths cannot prove membership under
    insertion queuing or car-following. Observation can."""

    def _rec(self, depart, arrival):
        return {"id": "v", "depart": depart, "arrival": arrival,
                "duration": arrival - depart, "routeLength": 1.0,
                "waitingTime": 0.0, "waitingCount": 0.0, "timeLoss": 1.0}

    def test_a_vehicle_finishing_before_the_boundary_is_pre(self):
        assert diag.observed_cohort(self._rec(0.0, diag.SAVE_TIME_S - 1)) == \
            "completed_before_boundary"

    def test_a_vehicle_straddling_the_boundary_is_active(self):
        assert diag.observed_cohort(self._rec(0.0, diag.SAVE_TIME_S + 1)) == \
            "active_at_boundary"

    def test_a_vehicle_departing_after_the_boundary_is_post(self):
        assert diag.observed_cohort(
            self._rec(diag.SAVE_TIME_S + 1, diag.SAVE_TIME_S + 9)) == \
            "depart_after_boundary"

    def test_arrival_exactly_at_the_boundary_counts_as_active(self):
        """The boundary instant belongs to the resumed side; a vehicle arriving
        exactly there has not completed before it."""
        assert diag.observed_cohort(self._rec(0.0, diag.SAVE_TIME_S)) == \
            "active_at_boundary"

    def test_an_insertion_delayed_vehicle_is_reclassified_not_assumed(self):
        """The exact failure ideal arithmetic cannot see: the fixture DECLARED
        this vehicle would clear the boundary, and it did not."""
        cold = _perfect_cold()
        victim = _ids("completed_before_boundary")[0]
        cold[victim] = dict(cold[victim], depart=1.0,
                            arrival=diag.SAVE_TIME_S + 20.0)
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        out = _compare(cold, prefix, resumed)
        disagreement = [d for d in out["cohort_disagreements"]
                        if d["id"] == victim]
        assert disagreement, "a reclassified vehicle must be reported"
        assert disagreement[0]["declared"] == "completed_before_boundary"
        assert disagreement[0]["observed"] == "active_at_boundary"

    def test_all_cohorts_populated_is_reported(self):
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        out = _compare(cold, prefix, resumed)
        assert out["all_cohorts_populated"]
        assert set(out["observed_cohort_counts"]) == set(diag.COHORTS)

    def test_an_empty_cohort_is_detected(self):
        """A fixture that did not exercise the boundary must not yield a
        verdict about it."""
        population = [v for v in diag.build_population()
                      if v["cohort"] != "active_at_boundary"]
        cold = {v["id"]: _record(v, i) for i, v in enumerate(population)}
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        out = diag.compare_population(
            cold=cold, prefix=prefix, resumed=resumed,
            declared_cohorts={v["id"]: v["cohort"] for v in population})
        assert not out["all_cohorts_populated"]


class TestPartitionIsCheckedBeforeArithmetic:
    """Criterion 4."""

    def test_a_clean_split_partitions_exactly(self):
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        out = _compare(cold, prefix, resumed)
        assert out["partition_ok"]
        assert out["vehicle_count"] == len(cold)
        assert out["total_delta_s"] == 0.0

    def test_a_vehicle_in_both_arms_is_a_partition_error(self):
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        duplicated = sorted(resumed)[0]
        prefix[duplicated] = resumed[duplicated]
        out = _compare(cold, prefix, resumed)
        assert not out["partition_ok"]
        assert out["duplicated_across_arms"] == [duplicated]

    def test_a_missing_vehicle_is_a_partition_error(self):
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        dropped = sorted(resumed)[0]
        del resumed[dropped]
        out = _compare(cold, prefix, resumed)
        assert not out["partition_ok"]
        assert out["missing_from_split"] == [dropped]

    def test_a_vehicle_unknown_to_the_fixture_is_a_partition_error(self):
        cold = _perfect_cold()
        cold["ghost"] = {"id": "ghost", "depart": 0.0, "arrival": 10.0,
                         "duration": 10.0, "routeLength": 1.0,
                         "waitingTime": 0.0, "waitingCount": 0.0,
                         "timeLoss": 1.0}
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        out = _compare(cold, prefix, resumed)
        assert not out["partition_ok"]
        assert out["unknown_to_fixture"] == ["ghost"]

    def test_totals_are_recomputed_from_per_vehicle_records(self):
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        out = _compare(cold, prefix, resumed)
        assert out["cold_total_s"] == pytest.approx(
            sum(r["timeLoss"] for r in cold.values()))
        recomputed = sum(r["split_time_loss_s"] - r["cold_time_loss_s"]
                         for r in out["per_vehicle"])
        assert out["total_delta_s"] == pytest.approx(recomputed, abs=1e-9)

    def test_deltas_are_attributed_per_cohort(self):
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        target = sorted(resumed)[0]
        resumed[target] = dict(resumed[target],
                               timeLoss=resumed[target]["timeLoss"] - 0.01)
        out = _compare(cold, prefix, resumed)
        cohort = _cohorts()[target]
        assert out["by_cohort"][cohort]["max_abs_delta_s"] == pytest.approx(0.01)


class TestFakeRunnerEndToEnd:
    """Prove the wiring, the evidence and the publication WITHOUT a socket or a
    process. The runner, the port allocator and the bound root are injected;
    everything else is the real path."""

    def _active_ids(self):
        cold = _perfect_cold()
        return sorted(k for k, v in diag.observed_cohorts(cold).items()
                      if v == "active_at_boundary")

    def _fake(self, records, *, active_ids=None, write_state=True,
              captured_at=None):
        """Stands in for SUMO: writes each arm's tripinfo, and for a controller
        arm also the state file and the boundary facts it observed."""
        def runner(*, cmd, cwd, **kwargs):
            tripinfo = Path([cmd[i + 1] for i, t in enumerate(cmd)
                             if t == "--tripinfo-output"][0])
            arm = tripinfo.name.replace("_tripinfo.xml", "")
            rows = "\n".join(
                f'<tripinfo id="{v}" depart="{r["depart"]:.2f}" '
                f'arrival="{r["arrival"]:.2f}" duration="{r["duration"]:.2f}" '
                f'routeLength="{r["routeLength"]:.2f}" waitingTime="0.00" '
                f'waitingCount="0" timeLoss="{r["timeLoss"]:.2f}"/>'
                for v, r in sorted(records[arm].items()))
            tripinfo.parent.mkdir(parents=True, exist_ok=True)
            tripinfo.write_text(f"<tripinfos>\n{rows}\n</tripinfos>\n")
            if "port" in kwargs:
                state = Path(kwargs["state_path"])
                if write_state:
                    state.parent.mkdir(parents=True, exist_ok=True)
                    state.write_text("STATE")
                ids = (self._active_ids() if active_ids is None else active_ids)
                return {"returncode": 0,
                        "captured_at_s": (diag.SAVE_TIME_S
                                          if captured_at is None
                                          else captured_at),
                        "active_vehicle_ids": list(ids),
                        "active_vehicle_count": len(ids)}
            return 0
        return runner

    def _records(self):
        cold = _perfect_cold()
        pre = set(_ids("completed_before_boundary"))
        prefix = {k: v for k, v in cold.items() if k in pre}
        resumed = {k: v for k, v in cold.items() if k not in pre}
        return {"cold": cold, "prefix": prefix, "resumed": resumed,
                "cold_control": cold, "prefix_control": prefix,
                "resumed_control": resumed}

    def _run(self, tmp_path, monkeypatch, **over):
        """The bound root is MONKEYPATCHED, never passed as an argument: the
        content key binds one root, so a caller must not be able to choose.

        Only the output root is redirected — `ROOT` stays real so the network,
        the contract and the live-byte verification are all the genuine ones.
        `Path / absolute` yields the absolute path, so this substitutes cleanly.
        """
        monkeypatch.setattr(diag, "APPROVED_OUTPUT_ROOT",
                            str(tmp_path / "outcome"))
        kwargs = dict(
            approval_token=json.loads(CONTRACT.read_text())["content_key"],
            home=tmp_path / "sumo", workspace=tmp_path / "work",
            runner=self._fake(self._records()), port_allocator=_ports())
        kwargs.update(over)
        return diag.execute(**kwargs)

    def test_execute_takes_no_output_root_argument(self):
        """Sol review: a matching token must not authorize writes anywhere the
        caller likes. The destination is bound by the content key."""
        import inspect
        assert "output_root" not in inspect.signature(diag.execute).parameters

    def test_the_full_path_runs_validates_and_publishes(self, tmp_path,
                                                        monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert result["verdict"]["classification"] == diag.EXACT_AGREEMENT
        assert set(result["arm_exit_status"]) == set(diag.ARMS)
        root = tmp_path / "outcome"
        assert root.is_dir()
        for member in diag.MEMBER_ORDER:
            assert (root / member).is_file(), member
        digests = json.loads((root / "members.json").read_text())
        for member, digest in digests.items():
            assert diag.sha256_file(root / member) == digest, member

    def test_a_snapshot_that_saw_nothing_is_refused(self, tmp_path, monkeypatch):
        """Sol review: the fake reported zero vehicles in flight and wrote no
        state, and the run still published `exact_agreement`. A boundary nobody
        can show did not happen."""
        with pytest.raises(SystemExit, match="does not describe this population"):
            self._run(tmp_path, monkeypatch,
                      runner=self._fake(self._records(), active_ids=[]))

    def test_a_missing_state_file_is_refused(self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit, match="wrote no state"):
            self._run(tmp_path, monkeypatch,
                      runner=self._fake(self._records(), write_state=False))

    def test_a_capture_at_the_wrong_instant_is_refused(self, tmp_path,
                                                       monkeypatch):
        with pytest.raises(SystemExit, match="not the contracted boundary"):
            self._run(tmp_path, monkeypatch,
                      runner=self._fake(self._records(), captured_at=29.0))

    def test_the_prefix_arms_are_wired_for_a_real_traci_server(self, tmp_path,
                                                               monkeypatch):
        """The defect review caught: `traci.init` on a port SUMO never opened."""
        seen = {}
        inner = self._fake(self._records())

        def runner(*, cmd, cwd, **kwargs):
            arm = [cmd[i + 1] for i, t in enumerate(cmd)
                   if t == "--tripinfo-output"][0]
            seen[Path(arm).name.replace("_tripinfo.xml", "")] = list(cmd)
            return inner(cmd=cmd, cwd=cwd, **kwargs)

        self._run(tmp_path, monkeypatch, runner=runner)
        for arm in ("prefix", "prefix_control"):
            cmd = seen[arm]
            assert "--remote-port" in cmd, arm
            assert int(cmd[cmd.index("--remote-port") + 1]) > 0
            assert "--save-state.times" not in cmd, arm
            assert "--save-state.files" not in cmd, arm
            assert "--save-state.rng" in cmd, arm
        for arm in ("cold", "resumed"):
            assert "--remote-port" not in seen[arm], arm

    def test_a_pre_existing_root_is_refused_before_anything_runs(
            self, tmp_path, monkeypatch):
        (tmp_path / "outcome").mkdir(parents=True)
        with pytest.raises(SystemExit, match="refusing to reuse an existing"):
            self._run(tmp_path, monkeypatch)

    def test_drifted_inputs_are_refused_before_any_simulator_import(self):
        """Verification happens while this is still a pure program."""
        import sys as _sys
        before = set(_sys.modules)
        with pytest.raises(SystemExit):
            diag.execute(approval_token="wrong")
        assert "traci" not in set(_sys.modules) - before

    def test_publication_is_all_or_nothing(self, tmp_path):
        root = tmp_path / "outcome"
        with pytest.raises(SystemExit, match="missing contracted members"):
            diag.publish_outcome(root, {"contract.json": "{}"})
        assert not root.exists(), "a failed publish must leave no root"
        assert not root.with_name(root.name + ".partial").exists()


class TestFailurePreservesItsOwnEvidence:
    """v1 failed inside a TemporaryDirectory and destroyed the one file that
    would have explained it. The failure path now preserves more carefully than
    the success path, because the failure path is when evidence is needed."""

    def _run(self, tmp_path, monkeypatch, runner):
        monkeypatch.setattr(diag, "APPROVED_OUTPUT_ROOT",
                            str(tmp_path / "outcome"))
        return diag.execute(
            approval_token=json.loads(CONTRACT.read_text())["content_key"],
            home=tmp_path / "sumo", workspace=tmp_path / "work",
            runner=runner, port_allocator=_ports())

    def _breaking_runner(self, bad_bytes):
        """Writes a cold tripinfo the parser must reject — the v1 shape."""
        def runner(*, cmd, cwd, **kwargs):
            tripinfo = Path([cmd[i + 1] for i, t in enumerate(cmd)
                             if t == "--tripinfo-output"][0])
            tripinfo.parent.mkdir(parents=True, exist_ok=True)
            tripinfo.write_text(bad_bytes)
            if "port" in kwargs:
                return {"returncode": 0, "captured_at_s": diag.SAVE_TIME_S,
                        "active_vehicle_ids": [], "active_vehicle_count": 0}
            return 0
        return runner

    BAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<!-- generated by Eclipse SUMO\n'
           '<configuration><output>\n'
           '  <tripinfo-output value="cold_tripinfo.xml"/>\n'
           '</output></configuration>\n-->\n'
           '<tripinfos>\n</tripinfos>\n')

    def test_a_parse_failure_publishes_a_failure_artifact_and_still_fails(
            self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit) as caught:
            self._run(tmp_path, monkeypatch, self._breaking_runner(self.BAD))
        assert "no tripinfo records" in str(caught.value), caught.value
        root = tmp_path / "outcome"
        assert root.is_dir(), "the failure must be preserved, not vanish"
        for member in diag.FAILURE_MEMBERS:
            assert (root / member).is_file(), member

    def test_the_offending_bytes_are_preserved_exactly(self, tmp_path,
                                                       monkeypatch):
        """The byte v1 destroyed."""
        with pytest.raises(SystemExit):
            self._run(tmp_path, monkeypatch, self._breaking_runner(self.BAD))
        preserved = (tmp_path / "outcome" / "cold_tripinfo.xml").read_text()
        assert preserved == self.BAD

    def test_the_failure_artifact_names_the_error_and_the_failing_arm(
            self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit):
            self._run(tmp_path, monkeypatch, self._breaking_runner(self.BAD))
        error = json.loads((tmp_path / "outcome" / "error.json").read_text())
        assert error["schema"] == diag.FAILURE_SCHEMA
        assert error["failing_arm"] == "cold"
        assert "no tripinfo records" in error["error_message"]
        assert error["error_type"]

    def test_a_failure_artifact_carries_no_verdict(self, tmp_path, monkeypatch):
        """A failed run must never look like a result."""
        with pytest.raises(SystemExit):
            self._run(tmp_path, monkeypatch, self._breaking_runner(self.BAD))
        root = tmp_path / "outcome"
        assert not (root / "verdict.json").exists()
        assert not (root / "observations.json").exists()
        blob = json.dumps(json.loads((root / "error.json").read_text()))
        for banned in ("exact_agreement", "output_quantization",
                       "save_load_integration_drift"):
            assert banned not in blob, banned

    def test_the_command_ledger_and_completed_arms_are_preserved(
            self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit):
            self._run(tmp_path, monkeypatch, self._breaking_runner(self.BAD))
        root = tmp_path / "outcome"
        ledger = json.loads((root / "command_ledger.json").read_text())
        assert "cold" in ledger and "--tripinfo-output" in ledger["cold"]
        completed = json.loads((root / "completed_arms.json").read_text())
        assert completed["parsed_arms"] == []
        assert "arm_exit_status" in completed

    def test_the_failure_artifact_has_a_verified_digest_manifest(
            self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit):
            self._run(tmp_path, monkeypatch, self._breaking_runner(self.BAD))
        root = tmp_path / "outcome"
        digests = json.loads((root / "members.json").read_text())
        for member, digest in digests.items():
            assert diag.sha256_file(root / member) == digest, member

    def test_a_preflight_failure_creates_no_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(diag, "APPROVED_OUTPUT_ROOT",
                            str(tmp_path / "outcome"))
        with pytest.raises(SystemExit, match="does not match the frozen"):
            diag.execute(approval_token="wrong")
        assert not (tmp_path / "outcome").exists()

    def test_a_pre_existing_root_is_refused_and_not_overwritten(
            self, tmp_path, monkeypatch):
        root = tmp_path / "outcome"
        root.mkdir(parents=True)
        (root / "sentinel").write_text("keep me")
        monkeypatch.setattr(diag, "APPROVED_OUTPUT_ROOT", str(root))
        with pytest.raises(SystemExit, match="refusing to reuse an existing"):
            diag.execute(
                approval_token=json.loads(CONTRACT.read_text())["content_key"],
                home=tmp_path / "sumo", workspace=tmp_path / "work",
                runner=self._breaking_runner(self.BAD), port_allocator=_ports())
        assert (root / "sentinel").read_text() == "keep me"

    def test_the_two_terminal_schemas_are_mutually_exclusive(self):
        assert diag.RESULT_SCHEMA != diag.FAILURE_SCHEMA
        contract = json.loads(CONTRACT.read_text())["terminal_schemas"]
        assert contract["mutually_exclusive"] is True
        assert set(contract["failure_members"]) == set(diag.FAILURE_MEMBERS)


class TestSolRoundTwoCounterexamples:
    """Each of these passed against the previous revision."""

    def _root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(diag, "APPROVED_OUTPUT_ROOT",
                            str(tmp_path / "outcome"))
        return tmp_path / "outcome"

    def _runner(self, payload):
        def runner(*, cmd, cwd, **kwargs):
            tripinfo = Path([cmd[i + 1] for i, t in enumerate(cmd)
                             if t == "--tripinfo-output"][0])
            tripinfo.parent.mkdir(parents=True, exist_ok=True)
            tripinfo.write_bytes(payload)
            if "port" in kwargs:
                return {"returncode": 0, "captured_at_s": diag.SAVE_TIME_S,
                        "active_vehicle_ids": [], "active_vehicle_count": 0}
            return 0
        return runner

    # Latin-1 bytes that are NOT valid UTF-8: a text round-trip destroys them.
    NON_UTF8 = b'<?xml version="1.0"?>\n<!-- caf\xe9 -->\n<tripinfos>\n</tripinfos>\n'

    def test_raw_failure_bytes_are_preserved_exactly(self, tmp_path,
                                                     monkeypatch):
        """`read_text(errors="replace")` + `write_text` silently rewrites the
        exact class of bytes a parser failure exists to explain."""
        root = self._root(tmp_path, monkeypatch)
        with pytest.raises(SystemExit):
            diag.execute(
                approval_token=json.loads(CONTRACT.read_text())["content_key"],
                home=tmp_path / "sumo", workspace=tmp_path / "work",
                runner=self._runner(self.NON_UTF8), port_allocator=_ports())
        preserved = (root / "cold_tripinfo.xml").read_bytes()
        assert preserved == self.NON_UTF8
        assert b"\xe9" in preserved, "the invalid byte must survive verbatim"

    def test_the_failure_artifact_validates(self, tmp_path, monkeypatch):
        root = self._root(tmp_path, monkeypatch)
        key = json.loads(CONTRACT.read_text())["content_key"]
        with pytest.raises(SystemExit):
            diag.execute(approval_token=key, home=tmp_path / "sumo",
                         workspace=tmp_path / "work",
                         runner=self._runner(self.NON_UTF8),
                         port_allocator=_ports())
        report = diag.validate_failure_artifact(root, contract_content_key=key)
        assert report["error"]["failing_arm"] == "cold"
        assert report["ledger"]["cold"]

    def test_a_tampered_failure_member_is_refused(self, tmp_path, monkeypatch):
        root = self._root(tmp_path, monkeypatch)
        key = json.loads(CONTRACT.read_text())["content_key"]
        with pytest.raises(SystemExit):
            diag.execute(approval_token=key, home=tmp_path / "sumo",
                         workspace=tmp_path / "work",
                         runner=self._runner(self.NON_UTF8),
                         port_allocator=_ports())
        (root / "cold_tripinfo.xml").write_bytes(b"tampered")
        with pytest.raises(SystemExit, match="does not match its digest"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_an_unlisted_member_is_refused(self, tmp_path, monkeypatch):
        root = self._root(tmp_path, monkeypatch)
        key = json.loads(CONTRACT.read_text())["content_key"]
        with pytest.raises(SystemExit):
            diag.execute(approval_token=key, home=tmp_path / "sumo",
                         workspace=tmp_path / "work",
                         runner=self._runner(self.NON_UTF8),
                         port_allocator=_ports())
        (root / "smuggled.json").write_text("{}")
        # Now caught by the derived allowlist, which fires earlier and harder
        # than the old manifest-based "unlisted" rule.
        with pytest.raises(SystemExit, match="does not permit"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_failure_artifact_with_a_verdict_is_refused(self, tmp_path,
                                                          monkeypatch):
        root = self._root(tmp_path, monkeypatch)
        key = json.loads(CONTRACT.read_text())["content_key"]
        with pytest.raises(SystemExit):
            diag.execute(approval_token=key, home=tmp_path / "sumo",
                         workspace=tmp_path / "work",
                         runner=self._runner(self.NON_UTF8),
                         port_allocator=_ports())
        digests = json.loads((root / "members.json").read_text())
        (root / "verdict.json").write_text("{}")
        digests["verdict.json"] = diag.sha256_file(root / "verdict.json")
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")
        # `verdict.json` is not in the derived allowlist at all, so it is
        # refused before the result-member rule is even reached.
        with pytest.raises(SystemExit, match="does not permit"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_failure_naming_the_wrong_contract_is_refused(self, tmp_path,
                                                            monkeypatch):
        root = self._root(tmp_path, monkeypatch)
        key = json.loads(CONTRACT.read_text())["content_key"]
        with pytest.raises(SystemExit):
            diag.execute(approval_token=key, home=tmp_path / "sumo",
                         workspace=tmp_path / "work",
                         runner=self._runner(self.NON_UTF8),
                         port_allocator=_ports())
        # Now caught by the embedded-contract recomputation, which fires before
        # the error.json key check and says which contract was actually found.
        with pytest.raises(SystemExit, match="not the one this artifact claims"):
            diag.validate_failure_artifact(root, contract_content_key="0" * 64)

    def test_the_rename_is_the_last_operation(self):
        """Sol review: renaming before the post-write checks meant a failing
        check left a success-shaped root, and the outer handler then saw
        `root.exists()` and skipped failure preservation entirely.

        Checked STRUCTURALLY — the rename must be the final statement of the
        try block. My first version compared substring positions in the AST
        dump and was measuring the word "renamed" in the docstring.
        """
        import ast as _ast
        source = Path("tools/diagnose_warm_state_population_semantics.py").read_text()
        fn = next(n for n in _ast.walk(_ast.parse(source))
                  if isinstance(n, _ast.FunctionDef) and n.name == "_publish")
        tries = [n for n in fn.body if isinstance(n, _ast.Try)]
        assert len(tries) == 1, "the publisher should have one commit block"
        last = tries[0].body[-1]
        assert isinstance(last, _ast.Expr), _ast.dump(last)
        call = last.value
        assert isinstance(call, _ast.Call), _ast.dump(last)
        assert getattr(call.func, "attr", None) == "rename", _ast.dump(call)

    def test_a_staging_failure_leaves_no_root_at_all(self, tmp_path):
        """A member that is not bytes must abort before any commit."""
        root = tmp_path / "outcome"
        members = {m: b"x" for m in diag.FAILURE_MEMBERS}
        members["error.json"] = "not bytes"
        with pytest.raises(SystemExit, match="must be bytes"):
            diag.publish_failure(root, members=members)
        assert not root.exists()
        assert not root.with_name(root.name + ".partial").exists()

    def test_a_preservation_failure_is_reported_not_swallowed(
            self, tmp_path, monkeypatch, capsys):
        """The original error still wins, but silence would recreate exactly
        the blindness v1 died of."""
        self._root(tmp_path, monkeypatch)
        key = json.loads(CONTRACT.read_text())["content_key"]

        def broken_publish(root, *, members):
            raise RuntimeError("disk full")

        monkeypatch.setattr(diag, "publish_failure", broken_publish)
        with pytest.raises(SystemExit) as caught:
            diag.execute(approval_token=key, home=tmp_path / "sumo",
                         workspace=tmp_path / "work",
                         runner=self._runner(self.NON_UTF8),
                         port_allocator=_ports())
        # The ORIGINAL parse failure propagates, not the preservation error.
        message = str(caught.value)
        assert "cold_tripinfo.xml" in message, message
        assert "disk full" not in message, "preservation must not mask it"
        assert "failure preservation failed" in capsys.readouterr().err

    def test_the_failing_arm_is_tracked_not_inferred(self):
        source = Path("tools/diagnose_warm_state_population_semantics.py").read_text()
        assert "active_arm = arm" in source
        assert '"failing_arm": active_arm' in source


class TestSolRoundThreeCounterexamples:
    """Each of these passed against the previous revision."""

    NON_UTF8 = b'<?xml version="1.0"?>\n<!-- caf\xe9 -->\n<tripinfos>\n</tripinfos>\n'

    def _runner(self, payload):
        def runner(*, cmd, cwd, **kwargs):
            tripinfo = Path([cmd[i + 1] for i, t in enumerate(cmd)
                             if t == "--tripinfo-output"][0])
            tripinfo.parent.mkdir(parents=True, exist_ok=True)
            tripinfo.write_bytes(payload)
            if "port" in kwargs:
                return {"returncode": 0, "captured_at_s": diag.SAVE_TIME_S,
                        "active_vehicle_ids": [], "active_vehicle_count": 0}
            return 0
        return runner

    def _failed_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(diag, "APPROVED_OUTPUT_ROOT",
                            str(tmp_path / "outcome"))
        key = json.loads(CONTRACT.read_text())["content_key"]
        with pytest.raises(SystemExit):
            diag.execute(approval_token=key, home=tmp_path / "sumo",
                         workspace=tmp_path / "work",
                         runner=self._runner(self.NON_UTF8),
                         port_allocator=_ports())
        return tmp_path / "outcome", key

    def _resign(self, root, name, payload):
        """Add a member AND its digest — an attacker who edits the manifest."""
        (root / name).write_text(payload)
        digests = json.loads((root / "members.json").read_text())
        digests[name] = diag.sha256_file(root / name)
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")

    def test_a_digest_listed_smuggled_member_is_refused(self, tmp_path,
                                                        monkeypatch):
        """Defining "unlisted" by the manifest meant editing the manifest was
        enough. The permitted set is derived from the contract instead."""
        root, key = self._failed_run(tmp_path, monkeypatch)
        self._resign(root, "smuggled.json", "{}")
        with pytest.raises(SystemExit, match="does not permit"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_the_allowlist_is_derived_from_the_contract(self):
        allowed = diag.failure_member_allowlist()
        assert set(diag.FAILURE_MEMBERS) <= allowed
        assert {f"{a}_tripinfo.xml" for a in diag.ARMS} <= allowed
        assert "smuggled.json" not in allowed
        assert "verdict.json" not in allowed

    def test_a_bogus_parsed_arm_is_refused(self, tmp_path, monkeypatch):
        """`parsed_arms: ["bogus"]` passed once its digest was updated."""
        root, key = self._failed_run(tmp_path, monkeypatch)
        completed = json.loads((root / "completed_arms.json").read_text())
        completed["parsed_arms"] = ["bogus"]
        self._resign(root, "completed_arms.json",
                     json.dumps(completed, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="undeclared arms"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_an_arm_after_the_failure_cannot_have_run(self, tmp_path,
                                                     monkeypatch):
        root, key = self._failed_run(tmp_path, monkeypatch)
        completed = json.loads((root / "completed_arms.json").read_text())
        completed["parsed_arms"] = ["resumed"]      # cold failed; resumed is later
        self._resign(root, "completed_arms.json",
                     json.dumps(completed, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="cannot have executed"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_parsed_arm_must_have_left_its_raw_file(self, tmp_path,
                                                     monkeypatch):
        """Claiming an arm parsed while its evidence is absent is refused."""
        root, key = self._failed_run(tmp_path, monkeypatch)
        error = json.loads((root / "error.json").read_text())
        error["failing_arm"] = "prefix"             # pretend cold succeeded
        self._resign(root, "error.json",
                     json.dumps(error, indent=2, sort_keys=True) + "\n")
        completed = json.loads((root / "completed_arms.json").read_text())
        completed["parsed_arms"] = ["cold"]
        completed["arm_exit_status"] = {"cold": 0}
        self._resign(root, "completed_arms.json",
                     json.dumps(completed, indent=2, sort_keys=True) + "\n")
        ledger = json.loads((root / "command_ledger.json").read_text())
        ledger["prefix"] = ["sumo"]
        self._resign(root, "command_ledger.json",
                     json.dumps(ledger, indent=2, sort_keys=True) + "\n")
        (root / "cold_tripinfo.xml").unlink()
        digests = json.loads((root / "members.json").read_text())
        del digests["cold_tripinfo.xml"]
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="no preserved raw file"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_boundary_facts_for_a_non_controller_arm_are_refused(
            self, tmp_path, monkeypatch):
        root, key = self._failed_run(tmp_path, monkeypatch)
        completed = json.loads((root / "completed_arms.json").read_text())
        completed["boundary_facts"] = {"cold": {"captured_at_s": 30.0}}
        self._resign(root, "completed_arms.json",
                     json.dumps(completed, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="owns no snapshot"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_stale_staging_path_refuses_before_any_arm_runs(self, tmp_path,
                                                              monkeypatch):
        """The worst shape available: a run starts, fails, and then cannot
        preserve anything, so NO terminal artifact exists at all."""
        root = tmp_path / "outcome"
        staging = root.with_name(root.name + ".partial")
        staging.mkdir(parents=True)
        monkeypatch.setattr(diag, "APPROVED_OUTPUT_ROOT", str(root))
        started = []

        def runner(*, cmd, cwd, **kwargs):
            started.append(cmd)
            return 0

        with pytest.raises(SystemExit, match="stale staging path"):
            diag.execute(
                approval_token=json.loads(CONTRACT.read_text())["content_key"],
                home=tmp_path / "sumo", workspace=tmp_path / "work",
                runner=runner, port_allocator=_ports())
        assert not started, "no arm may run when preservation is impossible"
        assert not root.exists()

    def test_the_staging_check_precedes_the_simulator_import(self):
        """Structural: the refusal must come before the lazy import, not after."""
        import ast as _ast
        source = Path("tools/diagnose_warm_state_population_semantics.py").read_text()
        fn = next(n for n in _ast.walk(_ast.parse(source))
                  if isinstance(n, _ast.FunctionDef) and n.name == "execute")
        staging_line = min(
            n.lineno for n in _ast.walk(fn)
            if isinstance(n, _ast.Constant) and isinstance(n.value, str)
            and "stale staging path" in n.value)
        import_line = min(
            n.lineno for n in _ast.walk(fn)
            if isinstance(n, _ast.ImportFrom) and n.module
            and "runtime" in n.module)
        assert staging_line < import_line, (staging_line, import_line)


class TestSolRoundFourCounterexamples:
    """One resigned artifact previously accepted a forged contract, a forged
    fixture, a nonsense ledger and a raw file from an arm that never ran."""

    NON_UTF8 = b'<?xml version="1.0"?>\n<!-- caf\xe9 -->\n<tripinfos>\n</tripinfos>\n'

    def _runner(self, payload):
        def runner(*, cmd, cwd, **kwargs):
            tripinfo = Path([cmd[i + 1] for i, t in enumerate(cmd)
                             if t == "--tripinfo-output"][0])
            tripinfo.parent.mkdir(parents=True, exist_ok=True)
            tripinfo.write_bytes(payload)
            if "port" in kwargs:
                return {"returncode": 0, "captured_at_s": diag.SAVE_TIME_S,
                        "active_vehicle_ids": [], "active_vehicle_count": 0}
            return 0
        return runner

    def _failed_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(diag, "APPROVED_OUTPUT_ROOT",
                            str(tmp_path / "outcome"))
        key = json.loads(CONTRACT.read_text())["content_key"]
        with pytest.raises(SystemExit):
            diag.execute(approval_token=key, home=tmp_path / "sumo",
                         workspace=tmp_path / "work",
                         runner=self._runner(self.NON_UTF8),
                         port_allocator=_ports())
        return tmp_path / "outcome", key

    def _resign(self, root, name, payload):
        (root / name).write_text(payload)
        digests = json.loads((root / "members.json").read_text())
        digests[name] = diag.sha256_file(root / name)
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")

    def test_a_clean_failure_artifact_still_validates(self, tmp_path,
                                                      monkeypatch):
        """The stricter rules must not reject a genuine artifact."""
        root, key = self._failed_run(tmp_path, monkeypatch)
        report = diag.validate_failure_artifact(root, contract_content_key=key)
        assert report["error"]["failing_arm"] == "cold"

    def test_a_forged_contract_is_refused(self, tmp_path, monkeypatch):
        root, key = self._failed_run(tmp_path, monkeypatch)
        self._resign(root, "contract.json", "{}")
        with pytest.raises(SystemExit, match="not a contract"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_contract_for_a_different_campaign_is_refused(self, tmp_path,
                                                            monkeypatch):
        root, key = self._failed_run(tmp_path, monkeypatch)
        self._resign(root, "contract.json", V1_CONTRACT.read_text())
        with pytest.raises(SystemExit, match="not the one this artifact claims"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_forged_fixture_is_refused(self, tmp_path, monkeypatch):
        """The fixture is deterministic, so it can be regenerated and compared."""
        root, key = self._failed_run(tmp_path, monkeypatch)
        self._resign(root, "population.rou.xml", "<routes/>\n")
        with pytest.raises(SystemExit, match="deterministic fixture"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_nonsense_ledger_entry_is_refused(self, tmp_path, monkeypatch):
        root, key = self._failed_run(tmp_path, monkeypatch)
        self._resign(root, "command_ledger.json",
                     json.dumps({"cold": "not-an-argv"}) + "\n")
        with pytest.raises(SystemExit, match="list of argv strings"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_ledger_argv_that_is_not_the_frozen_command_is_refused(
            self, tmp_path, monkeypatch):
        root, key = self._failed_run(tmp_path, monkeypatch)
        argv = list(diag.frozen_command("cold"))
        argv[argv.index("--no-warnings") + 1] = "false"
        self._resign(root, "command_ledger.json",
                     json.dumps({"cold": argv}) + "\n")
        with pytest.raises(SystemExit, match="frozen command has"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_ledger_argv_of_the_wrong_length_is_refused(self, tmp_path,
                                                          monkeypatch):
        root, key = self._failed_run(tmp_path, monkeypatch)
        argv = list(diag.frozen_command("cold")) + ["--extra"]
        self._resign(root, "command_ledger.json",
                     json.dumps({"cold": argv}) + "\n")
        with pytest.raises(SystemExit, match="tokens but the frozen command"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_substituted_placeholders_are_accepted(self):
        """Real paths and an allocated port must still match the frozen shape."""
        frozen = diag.frozen_command("prefix")
        actual = [t.replace("<free-port>", "45001").replace("<sumo>", "/x/sumo")
                  for t in frozen]
        actual = [("/tmp/net.net.xml" if t == "<net.net.xml>" else t)
                  for t in actual]
        actual = [("/tmp/p.rou.xml" if t == "<population.rou.xml>" else t)
                  for t in actual]
        actual = [("/tmp/s.xml.gz" if t == "<state.xml.gz>" else t)
                  for t in actual]
        actual = [("/tmp/prefix_tripinfo.xml"
                   if t == "<prefix_tripinfo.xml>" else t) for t in actual]
        diag.normalize_argv(frozen, actual)          # no raise

    def test_a_raw_file_from_an_arm_after_the_failure_is_refused(
            self, tmp_path, monkeypatch):
        """`resumed_tripinfo.xml` beside a failing `cold` describes a run that
        did not happen."""
        root, key = self._failed_run(tmp_path, monkeypatch)
        self._resign(root, "resumed_tripinfo.xml", "<tripinfos/>\n")
        with pytest.raises(SystemExit, match="cannot have produced output"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_boundary_fields_are_validated(self, tmp_path, monkeypatch):
        root, key = self._failed_run(tmp_path, monkeypatch)
        completed = json.loads((root / "completed_arms.json").read_text())
        completed["boundary_facts"] = {"prefix": {"captured_at_s": 29.0}}
        completed["parsed_arms"] = []
        ledger = json.loads((root / "command_ledger.json").read_text())
        ledger["prefix"] = list(diag.frozen_command("prefix"))
        self._resign(root, "command_ledger.json",
                     json.dumps(ledger, indent=2, sort_keys=True) + "\n")
        error = json.loads((root / "error.json").read_text())
        error["failing_arm"] = "prefix"
        self._resign(root, "error.json",
                     json.dumps(error, indent=2, sort_keys=True) + "\n")
        completed["parsed_arms"] = ["cold"]
        completed["arm_exit_status"] = {"cold": 0}
        self._resign(root, "completed_arms.json",
                     json.dumps(completed, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="lack"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_boundary_count_disagreeing_with_its_ids_is_refused(self):
        with pytest.raises(SystemExit, match="disagrees"):
            diag.validate_boundary_block("prefix", {
                "captured_at_s": diag.SAVE_TIME_S,
                "active_vehicle_ids": ["a", "b"],
                "active_vehicle_count": 5, "state_sha256": "a" * 64})

    def test_a_boundary_capture_at_the_wrong_instant_is_refused_on_success(self):
        """Success path only — see the failure-artifact scope rule."""
        with pytest.raises(SystemExit, match="not the contracted boundary"):
            diag.validate_boundary_block("prefix", {
                "captured_at_s": 29.0, "active_vehicle_ids": [],
                "active_vehicle_count": 0, "state_sha256": "a" * 64})

    def test_a_malformed_state_digest_is_refused(self):
        with pytest.raises(SystemExit, match="not a sha256"):
            diag.validate_boundary_block("prefix", {
                "captured_at_s": diag.SAVE_TIME_S, "active_vehicle_ids": [],
                "active_vehicle_count": 0, "state_sha256": "short"})

    def test_the_publication_prose_matches_the_rename_last_rule(self):
        """The contract said checks run AFTER the rename, contradicting the
        corrected commit rule."""
        publication = json.loads(CONTRACT.read_text())["publication_contract"]
        assert "post_publication_verification" not in publication
        assert "pre_commit_verification" in publication
        assert "only then is the staging directory renamed" in \
            publication["pre_commit_verification"]


class TestFailurePhasesAreSelfConsistent:
    """The tool must never produce an artifact its own validator rejects.

    The previous revision did exactly that: a post-arm failure named the LAST
    arm as failing while also listing it as parsed.
    """

    GOOD = ('<?xml version="1.0"?>\n<tripinfos>\n'
            '<tripinfo id="pre-000" depart="1.00" arrival="20.00" '
            'duration="19.00" routeLength="302.42" waitingTime="0.00" '
            'waitingCount="0" timeLoss="0.12"/>\n</tripinfos>\n').encode()
    BAD = b'<?xml version="1.0"?>\n<tripinfos>\n</tripinfos>\n'

    def _runner(self, *, payload=None, exit_for=None, boundary_over=None):
        def runner(*, cmd, cwd, **kwargs):
            tripinfo = Path([cmd[i + 1] for i, t in enumerate(cmd)
                             if t == "--tripinfo-output"][0])
            arm = tripinfo.name.replace("_tripinfo.xml", "")
            tripinfo.parent.mkdir(parents=True, exist_ok=True)
            tripinfo.write_bytes(payload if payload is not None else self.GOOD)
            code = (exit_for or {}).get(arm, 0)
            if "port" in kwargs:
                Path(kwargs["state_path"]).write_bytes(b"STATE")
                block = {"returncode": code, "captured_at_s": diag.SAVE_TIME_S,
                         "active_vehicle_ids": [], "active_vehicle_count": 0}
                block.update(boundary_over or {})
                return block
            return code
        return runner

    def _run(self, tmp_path, monkeypatch, runner):
        monkeypatch.setattr(diag, "APPROVED_OUTPUT_ROOT",
                            str(tmp_path / "outcome"))
        key = json.loads(CONTRACT.read_text())["content_key"]
        with pytest.raises(SystemExit) as caught:
            diag.execute(approval_token=key, home=tmp_path / "sumo",
                         workspace=tmp_path / "work", runner=runner,
                         port_allocator=_ports())
        return tmp_path / "outcome", key, caught.value

    def test_a_nonzero_arm_stops_the_run_immediately(self, tmp_path,
                                                     monkeypatch):
        """`cold` returning 1 must not let the other five arms run."""
        root, key, error = self._run(
            tmp_path, monkeypatch, self._runner(exit_for={"cold": 1}))
        assert "cold exited 1" in str(error), error
        ledger = json.loads((root / "command_ledger.json").read_text())
        assert list(ledger) == ["cold"], "no later arm may have run"
        report = diag.validate_failure_artifact(root, contract_content_key=key)
        assert report["error"]["failure_phase"] == diag.PHASE_ARM
        assert report["error"]["failing_arm"] == "cold"

    def test_an_arm_phase_artifact_validates(self, tmp_path, monkeypatch):
        root, key, _ = self._run(tmp_path, monkeypatch,
                                 self._runner(payload=self.BAD))
        report = diag.validate_failure_artifact(root, contract_content_key=key)
        assert report["error"]["failure_phase"] == diag.PHASE_ARM
        assert report["error"]["failing_arm"] == "cold"

    def test_a_post_arm_artifact_validates_and_names_no_arm(self, tmp_path,
                                                            monkeypatch):
        """Every arm completes; the failure comes afterwards. The previous
        revision blamed `resumed_control` and produced a self-invalid shape."""
        root, key, error = self._run(
            tmp_path, monkeypatch,
            self._runner(boundary_over={"captured_at_s": 29.0}))
        assert "resumed_control" not in str(error), error
        report = diag.validate_failure_artifact(root, contract_content_key=key)
        assert report["error"]["failure_phase"] == diag.PHASE_POST_ARM
        assert report["error"]["failing_arm"] is None
        completed = report["completed"]
        assert sorted(completed["parsed_arms"]) == sorted(diag.ARMS)
        assert all(v == 0 for v in completed["arm_exit_status"].values())
        # The artifact RETAINS the wrong instant that caused the failure — the
        # validator must not reject evidence for explaining itself.
        assert completed["boundary_facts"]["prefix"]["captured_at_s"] == 29.0

    def test_a_post_arm_artifact_keeps_every_raw_file(self, tmp_path,
                                                     monkeypatch):
        root, _key, _ = self._run(
            tmp_path, monkeypatch,
            self._runner(boundary_over={"captured_at_s": 29.0}))
        for arm in diag.ARMS:
            assert (root / f"{arm}_tripinfo.xml").is_file(), arm

    def test_a_post_arm_failure_naming_an_arm_is_refused(self, tmp_path,
                                                         monkeypatch):
        root, key, _ = self._run(
            tmp_path, monkeypatch,
            self._runner(boundary_over={"captured_at_s": 29.0}))
        error = json.loads((root / "error.json").read_text())
        error["failing_arm"] = "resumed_control"
        (root / "error.json").write_text(
            json.dumps(error, indent=2, sort_keys=True) + "\n")
        digests = json.loads((root / "members.json").read_text())
        digests["error.json"] = diag.sha256_file(root / "error.json")
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="must name NO failing arm"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_an_arm_phase_failure_without_an_arm_is_refused(self, tmp_path,
                                                            monkeypatch):
        root, key, _ = self._run(tmp_path, monkeypatch,
                                 self._runner(payload=self.BAD))
        error = json.loads((root / "error.json").read_text())
        error["failing_arm"] = None
        (root / "error.json").write_text(
            json.dumps(error, indent=2, sort_keys=True) + "\n")
        digests = json.loads((root / "members.json").read_text())
        digests["error.json"] = diag.sha256_file(root / "error.json")
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="must name its failing arm"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_an_unknown_phase_is_refused(self, tmp_path, monkeypatch):
        root, key, _ = self._run(tmp_path, monkeypatch,
                                 self._runner(payload=self.BAD))
        error = json.loads((root / "error.json").read_text())
        error["failure_phase"] = "somewhere"
        (root / "error.json").write_text(
            json.dumps(error, indent=2, sort_keys=True) + "\n")
        digests = json.loads((root / "members.json").read_text())
        digests["error.json"] = diag.sha256_file(root / "error.json")
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="failure_phase"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_post_arm_artifact_missing_an_arm_is_refused(self, tmp_path,
                                                           monkeypatch):
        root, key, _ = self._run(
            tmp_path, monkeypatch,
            self._runner(boundary_over={"captured_at_s": 29.0}))
        completed = json.loads((root / "completed_arms.json").read_text())
        completed["parsed_arms"] = ["cold"]
        (root / "completed_arms.json").write_text(
            json.dumps(completed, indent=2, sort_keys=True) + "\n")
        digests = json.loads((root / "members.json").read_text())
        digests["completed_arms.json"] = diag.sha256_file(
            root / "completed_arms.json")
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="requires every arm parsed"):
            diag.validate_failure_artifact(root, contract_content_key=key)


class TestArmSetupPhase:
    """Sol's counterexample: a failure BETWEEN completed arms and a running one.

    `cold` completes, then prefix port allocation throws — before the prefix
    command exists. The previous model had no phase for that: it recorded
    `post_arm` with only `cold` parsed, and the tool's own validator refused it.
    """

    GOOD = ('<?xml version="1.0"?>\n<tripinfos>\n'
            '<tripinfo id="pre-000" depart="1.00" arrival="20.00" '
            'duration="19.00" routeLength="302.42" waitingTime="0.00" '
            'waitingCount="0" timeLoss="0.12"/>\n</tripinfos>\n').encode()

    def _runner(self):
        def runner(*, cmd, cwd, **kwargs):
            tripinfo = Path([cmd[i + 1] for i, t in enumerate(cmd)
                             if t == "--tripinfo-output"][0])
            tripinfo.parent.mkdir(parents=True, exist_ok=True)
            tripinfo.write_bytes(self.GOOD)
            if "port" in kwargs:
                Path(kwargs["state_path"]).write_bytes(b"STATE")
                return {"returncode": 0, "captured_at_s": diag.SAVE_TIME_S,
                        "active_vehicle_ids": [], "active_vehicle_count": 0}
            return 0
        return runner

    def _exploding_allocator(self):
        """Allocation succeeds for nothing — it throws on the first call, which
        happens while `prefix` is being prepared."""
        def allocate():
            raise RuntimeError("no ports available")
        return allocate

    def _run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(diag, "APPROVED_OUTPUT_ROOT",
                            str(tmp_path / "outcome"))
        key = json.loads(CONTRACT.read_text())["content_key"]
        with pytest.raises(BaseException) as caught:
            diag.execute(approval_token=key, home=tmp_path / "sumo",
                         workspace=tmp_path / "work", runner=self._runner(),
                         port_allocator=self._exploding_allocator())
        return tmp_path / "outcome", key, caught.value

    def test_a_setup_failure_is_recorded_as_its_own_phase(self, tmp_path,
                                                          monkeypatch):
        root, key, error = self._run(tmp_path, monkeypatch)
        assert "no ports available" in str(error)
        recorded = json.loads((root / "error.json").read_text())
        assert recorded["failure_phase"] == diag.PHASE_ARM_SETUP
        assert recorded["failing_arm"] == "prefix"

    def test_the_generated_setup_artifact_validates_under_its_own_schema(
            self, tmp_path, monkeypatch):
        """The property that kept being missing: the tool must never emit an
        artifact its own validator rejects."""
        root, key, _ = self._run(tmp_path, monkeypatch)
        report = diag.validate_failure_artifact(root, contract_content_key=key)
        assert report["error"]["failure_phase"] == diag.PHASE_ARM_SETUP
        assert report["completed"]["parsed_arms"] == ["cold"]
        assert "prefix" not in report["ledger"]

    def test_the_completed_arm_is_preserved(self, tmp_path, monkeypatch):
        root, _key, _ = self._run(tmp_path, monkeypatch)
        assert (root / "cold_tripinfo.xml").is_file()
        assert not (root / "prefix_tripinfo.xml").exists()

    def test_a_setup_arm_with_a_command_is_refused(self, tmp_path, monkeypatch):
        """If it has a command it was not still being prepared."""
        root, key, _ = self._run(tmp_path, monkeypatch)
        ledger = json.loads((root / "command_ledger.json").read_text())
        ledger["prefix"] = list(diag.frozen_command("prefix"))
        (root / "command_ledger.json").write_text(
            json.dumps(ledger, indent=2, sort_keys=True) + "\n")
        digests = json.loads((root / "members.json").read_text())
        digests["command_ledger.json"] = diag.sha256_file(
            root / "command_ledger.json")
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="still being prepared"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_an_arm_after_the_setup_arm_is_refused(self, tmp_path, monkeypatch):
        root, key, _ = self._run(tmp_path, monkeypatch)
        completed = json.loads((root / "completed_arms.json").read_text())
        completed["parsed_arms"] = ["cold", "resumed"]
        (root / "completed_arms.json").write_text(
            json.dumps(completed, indent=2, sort_keys=True) + "\n")
        digests = json.loads((root / "members.json").read_text())
        digests["completed_arms.json"] = diag.sha256_file(
            root / "completed_arms.json")
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="cannot have executed"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_a_completed_controller_arm_must_keep_its_boundary_facts(
            self, tmp_path, monkeypatch):
        """Losing the snapshot evidence for an arm that demonstrably ran."""
        root, key, _ = self._run(tmp_path, monkeypatch)
        completed = json.loads((root / "completed_arms.json").read_text())
        completed["parsed_arms"] = ["cold", "prefix"]
        completed["arm_exit_status"] = {"cold": 0, "prefix": 0}
        completed["boundary_facts"] = {}
        (root / "completed_arms.json").write_text(
            json.dumps(completed, indent=2, sort_keys=True) + "\n")
        error = json.loads((root / "error.json").read_text())
        # Recast as an ARM-phase failure in `resumed`: cold and prefix are
        # complete, so prefix's boundary facts must still be there.
        error["failure_phase"] = diag.PHASE_ARM
        error["failing_arm"] = "resumed"
        (root / "error.json").write_text(
            json.dumps(error, indent=2, sort_keys=True) + "\n")
        ledger = json.loads((root / "command_ledger.json").read_text())
        ledger["prefix"] = list(diag.frozen_command("prefix"))
        ledger["resumed"] = list(diag.frozen_command("resumed"))
        (root / "command_ledger.json").write_text(
            json.dumps(ledger, indent=2, sort_keys=True) + "\n")
        # prefix must look genuinely complete, or an earlier rule fires first.
        (root / "prefix_tripinfo.xml").write_bytes(self.GOOD)
        digests = json.loads((root / "members.json").read_text())
        for name in ("completed_arms.json", "error.json", "command_ledger.json",
                     "prefix_tripinfo.xml"):
            digests[name] = diag.sha256_file(root / name)
        (root / "members.json").write_text(
            json.dumps(digests, indent=2, sort_keys=True) + "\n")
        with pytest.raises(SystemExit, match="kept no boundary facts"):
            diag.validate_failure_artifact(root, contract_content_key=key)

    def test_all_three_phases_are_frozen_in_the_contract(self):
        contract = json.loads(CONTRACT.read_text())["terminal_schemas"]
        assert set(diag.FAILURE_PHASES) == {"arm_setup", "arm", "post_arm"}
        assert "arm_setup" in contract["failure_phase_rule"]
        assert "being prepared" in contract["phase_evidence_rule"] or \
            "being prepared" in contract["failure_phase_rule"]


class TestTheGatedCli:

    def test_exactly_one_mode_is_required(self):
        with pytest.raises(SystemExit, match="exactly one"):
            diag.main([])
        with pytest.raises(SystemExit, match="exactly one"):
            diag.main(["--freeze", "--verify"])
        with pytest.raises(SystemExit, match="exactly one"):
            diag.main(["--verify", "--execute", "tok"])

    def test_verify_reports_reproduction(self, capsys):
        assert diag.main(["--verify"]) == 0
        assert "reproduces byte-for-byte: True" in capsys.readouterr().out

    def test_execute_requires_the_exact_key_and_exits_nonzero(self, capsys):
        """A diagnostic that fails quietly is worse than one that does not run."""
        assert diag.main(["--execute", "not-the-key"]) == 1
        assert "diagnostic failed" in capsys.readouterr().out

    def test_a_wrong_token_fails_before_any_simulator_import(self):
        import sys as _sys
        before = set(_sys.modules)
        assert diag.main(["--execute", "nope"]) == 1
        assert "traci" not in set(_sys.modules) - before


class TestV1IsPreservedAsSpent:

    def test_the_v1_contract_bytes_are_untouched(self):
        assert diag.sha256_file(V1_CONTRACT) == diag.V1_CONTRACT_SHA256

    def test_v2_records_v1_as_spent_and_failed(self):
        v1 = json.loads(CONTRACT.read_text())["v1_disposition"]
        assert v1["status"] == "spent_failed"
        assert v1["contract_sha256"] == diag.V1_CONTRACT_SHA256
        assert "tripinfo-output" in v1["cause"]
        assert "TemporaryDirectory" in v1["second_defect"]
        assert v1["bytes_preserved"] is True

    def test_v2_names_only_its_own_root(self):
        contract = json.loads(CONTRACT.read_text())
        assert contract["output_root"].endswith("v2_outcome")
        assert "v1_outcome" not in json.dumps(contract)


class TestSolCounterexamples:
    """The exact process-free counterexamples review used. Each one PASSED
    against the previous revision, which is why they are tests now."""

    def test_a_union_preserving_swap_is_not_a_partition(self):
        """Two vehicles trade arms; every set identity survives, and both are
        attributed to the wrong side of the boundary."""
        cold = _perfect_cold()
        pre = _ids("completed_before_boundary")[0]
        post = _ids("active_at_boundary")[0]
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        # swap: the completed vehicle moves to resumed, the active one to prefix
        prefix[post] = resumed.pop(post)
        resumed[pre] = prefix.pop(pre)
        out = _compare(cold, prefix, resumed)
        assert set(prefix) | set(resumed) == set(cold), "the union is intact"
        assert not out["partition_ok"], "a swap must not read as a partition"
        misplaced = {m["id"] for m in out["misplaced_vehicles"]}
        assert {pre, post} <= misplaced

    def test_a_forged_verdict_is_refused(self):
        """A result whose conclusion was replaced by hand must not validate."""
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        block = _compare(cold, prefix, resumed)
        key = json.loads(CONTRACT.read_text())["content_key"]
        result = diag.build_verdict(
            production=block, control=block, contract_content_key=key,
            arm_exit_status={arm: 0 for arm in diag.ARMS},
            arm_records=_arm_records(), boundary_facts=_boundary_facts())
        assert diag.validate_result(result, contract_content_key=key)
        forged = json.loads(json.dumps(result))
        forged["verdict"]["classification"] = diag.SAVE_LOAD_DRIFT
        with pytest.raises(SystemExit, match="does not recompute"):
            diag.validate_result(forged, contract_content_key=key)

    def test_a_forged_recommendation_is_refused(self):
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        block = _compare(cold, prefix, resumed)
        key = json.loads(CONTRACT.read_text())["content_key"]
        result = diag.build_verdict(
            production=block, control=block, contract_content_key=key,
            arm_exit_status={arm: 0 for arm in diag.ARMS},
            arm_records=_arm_records(), boundary_facts=_boundary_facts())
        result["verdict"]["recommendation"] = "adopt warming"
        with pytest.raises(SystemExit, match="recommendation does not recompute"):
            diag.validate_result(result, contract_content_key=key)

    def test_every_arm_must_record_a_zero_exit(self):
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        block = _compare(cold, prefix, resumed)
        key = json.loads(CONTRACT.read_text())["content_key"]
        missing = diag.build_verdict(
            production=block, control=block, contract_content_key=key,
            arm_exit_status={arm: 0 for arm in diag.ARMS[:-1]},
            arm_records=_arm_records(), boundary_facts=_boundary_facts())
        with pytest.raises(SystemExit, match="must record an exit status"):
            diag.validate_result(missing, contract_content_key=key)
        failed = diag.build_verdict(
            production=block, control=block, contract_content_key=key,
            arm_exit_status={**{a: 0 for a in diag.ARMS}, "resumed": 1},
            arm_records=_arm_records(), boundary_facts=_boundary_facts())
        with pytest.raises(SystemExit, match="exited non-zero"):
            diag.validate_result(failed, contract_content_key=key)

    def test_empty_arm_records_no_longer_skip_reconstruction(self):
        """Sol review: reconstruction was gated on truthiness, so a result with
        the REQUIRED key present but empty sailed through untouched."""
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        block = _compare(cold, prefix, resumed)
        key = json.loads(CONTRACT.read_text())["content_key"]
        result = diag.build_verdict(
            production=block, control=block, contract_content_key=key,
            arm_exit_status={arm: 0 for arm in diag.ARMS},
            arm_records={}, boundary_facts=_boundary_facts())
        with pytest.raises(SystemExit, match="must cover exactly"):
            diag.validate_result(result, contract_content_key=key)

    def test_a_tampered_comparison_field_is_caught_by_whole_object_equality(self):
        """Comparing a chosen subset left everything unlisted trusted."""
        key = json.loads(CONTRACT.read_text())["content_key"]
        records = _arm_records()
        block = diag.compare_population(
            cold=records["cold"], prefix=records["prefix"],
            resumed=records["resumed"], declared_cohorts=_cohorts())
        tampered = json.loads(json.dumps(block))
        tampered["cold_total_s"] = tampered["cold_total_s"] + 1.0
        result = diag.build_verdict(
            production=tampered, control=block, contract_content_key=key,
            arm_exit_status={arm: 0 for arm in diag.ARMS},
            arm_records=records, boundary_facts=_boundary_facts())
        with pytest.raises(SystemExit, match="does not reconstruct"):
            diag.validate_result(result, contract_content_key=key)

    def test_a_result_naming_the_wrong_contract_is_refused(self):
        cold = _perfect_cold()
        prefix, resumed = _split_from(cold, set(_ids("completed_before_boundary")))
        block = _compare(cold, prefix, resumed)
        key = json.loads(CONTRACT.read_text())["content_key"]
        result = diag.build_verdict(
            production=block, control=block, contract_content_key="0" * 64,
            arm_exit_status={arm: 0 for arm in diag.ARMS},
            arm_records=_arm_records(), boundary_facts=_boundary_facts())
        with pytest.raises(SystemExit, match="does not name the frozen contract"):
            diag.validate_result(result, contract_content_key=key)

    def test_the_prefix_is_terminated_at_the_boundary_not_by_the_batch_end(self):
        """Sol review: a save at 30 s with the batch running to 31 s can
        complete a vehicle in that step, which then appears in the prefix
        tripinfo AND in the state resumed from 30 s — counted twice."""
        source = TOOL.read_text()
        assert "def run_prefix_atomic(" in source
        assert "saveState" in source
        contract = json.loads(CONTRACT.read_text())
        rule = contract["command_contract"]["prefix_atomicity_rule"]
        assert "terminated AT the boundary" in rule
        assert "counting it twice" in rule


class TestClassifierIsFailClosed:
    """Criterion 5: mutually exclusive, and never generous."""

    def _obs(self, **over):
        base = {"partition_ok": True, "all_cohorts_populated": True,
                "total_delta_s": 0.0, "max_abs_delta_s": 0.0,
                "vehicle_count": 40}
        base.update(over)
        return base

    def test_a_partition_error_short_circuits_everything(self):
        out = diag.classify(self._obs(partition_ok=False),
                            self._obs(total_delta_s=999.0))
        assert out["classification"] == diag.PARTITION_ERROR
        assert out["recommendation"] is None

    def test_a_control_partition_error_also_short_circuits(self):
        out = diag.classify(self._obs(), self._obs(partition_ok=False))
        assert out["classification"] == diag.PARTITION_ERROR

    def test_exact_agreement_is_recognised(self):
        out = diag.classify(self._obs(), self._obs())
        assert out["classification"] == diag.EXACT_AGREEMENT
        assert out["recommendation"] is None

    def test_quantization_requires_the_deltas_to_vanish_at_high_precision(self):
        """The measured v9 shape: small, inside the rounding envelope, gone
        once precision is raised."""
        count = 40
        residual = -count * diag.QUANT_PER_VEHICLE_BOUND_S * 0.5
        out = diag.classify(
            self._obs(total_delta_s=residual, max_abs_delta_s=0.01,
                      vehicle_count=count),
            self._obs(total_delta_s=0.0, max_abs_delta_s=0.0,
                      vehicle_count=count))
        assert out["classification"] == diag.OUTPUT_QUANTIZATION
        assert "Do NOT 'fix' the simulation" in out["recommendation"]

    def test_a_residual_too_large_for_rounding_is_not_quantization(self):
        """Deltas vanishing at high precision is NOT enough on its own: the
        production residual must also fit the rounding envelope."""
        count = 40
        huge = count * diag.QUANT_PER_VEHICLE_BOUND_S * 100
        out = diag.classify(
            self._obs(total_delta_s=huge, max_abs_delta_s=5.0,
                      vehicle_count=count),
            self._obs(total_delta_s=0.0, max_abs_delta_s=0.0,
                      vehicle_count=count))
        assert out["classification"] == diag.INCONCLUSIVE

    def test_drift_requires_surviving_high_precision_and_exceeding_the_envelope(self):
        count = 40
        huge = count * diag.QUANT_PER_VEHICLE_BOUND_S * 100
        out = diag.classify(
            self._obs(total_delta_s=huge, max_abs_delta_s=5.0,
                      vehicle_count=count),
            self._obs(total_delta_s=huge, max_abs_delta_s=5.0,
                      vehicle_count=count))
        assert out["classification"] == diag.SAVE_LOAD_DRIFT
        assert "warming must stay off" in out["recommendation"]

    def test_surviving_deltas_inside_the_envelope_are_inconclusive(self):
        """Ambiguous evidence must not be rounded toward either hypothesis."""
        count = 40
        small = count * diag.QUANT_PER_VEHICLE_BOUND_S * 0.5
        out = diag.classify(
            self._obs(total_delta_s=small, max_abs_delta_s=0.01,
                      vehicle_count=count),
            self._obs(total_delta_s=small, max_abs_delta_s=0.001,
                      vehicle_count=count))
        assert out["classification"] == diag.INCONCLUSIVE
        assert out["recommendation"] is None

    def test_a_recommendation_is_emitted_only_for_a_unique_mechanism(self):
        count = 40
        cases = [
            (self._obs(partition_ok=False), self._obs()),
            (self._obs(), self._obs()),
            (self._obs(total_delta_s=count * diag.QUANT_PER_VEHICLE_BOUND_S * 0.5,
                       max_abs_delta_s=0.01, vehicle_count=count),
             self._obs(total_delta_s=count * diag.QUANT_PER_VEHICLE_BOUND_S * 0.5,
                       max_abs_delta_s=0.001, vehicle_count=count)),
        ]
        for production, control in cases:
            assert diag.classify(production, control)["recommendation"] is None

    def test_every_verdict_is_one_of_the_declared_classifications(self):
        count = 40
        env = count * diag.QUANT_PER_VEHICLE_BOUND_S
        seen = set()
        for prod_delta, prod_max, ctl_delta, ctl_max, ok in [
                (0.0, 0.0, 0.0, 0.0, False),
                (0.0, 0.0, 0.0, 0.0, True),
                (-env * 0.5, 0.01, 0.0, 0.0, True),
                (env * 100, 5.0, env * 100, 5.0, True),
                (env * 0.5, 0.01, env * 0.5, 0.001, True)]:
            out = diag.classify(
                self._obs(partition_ok=ok, total_delta_s=prod_delta,
                          max_abs_delta_s=prod_max, vehicle_count=count),
                self._obs(total_delta_s=ctl_delta, max_abs_delta_s=ctl_max,
                          vehicle_count=count))
            assert out["classification"] in diag.CLASSIFICATIONS
            seen.add(out["classification"])
        assert seen == set(diag.CLASSIFICATIONS)

    def test_exact_agreement_requires_the_control_to_agree_too(self):
        """Sol review finding: production-precision equality is NOT agreement.

        Two-decimal reporting can round a real difference away. Calling that
        `exact_agreement` would announce that there is nothing to explain at the
        exact moment the quantization case is staring at us.
        """
        out = diag.classify(
            self._obs(total_delta_s=0.0, max_abs_delta_s=0.0),
            self._obs(total_delta_s=0.004, max_abs_delta_s=0.004))
        assert out["classification"] != diag.EXACT_AGREEMENT
        # And it does not overclaim in the other direction either: production
        # says nothing and the control says something small, which establishes
        # neither mechanism. `inconclusive` is the honest answer, and the
        # recommendation stays empty.
        assert out["classification"] == diag.INCONCLUSIVE
        assert out["recommendation"] is None

    def test_an_empty_observed_cohort_is_a_partition_error(self):
        """A fixture that did not exercise the boundary yields no verdict."""
        out = diag.classify(self._obs(all_cohorts_populated=False),
                            self._obs())
        assert out["classification"] == diag.PARTITION_ERROR
        assert "cohort is empty" in out["why"]

    def test_the_rounding_envelope_allows_two_half_ulps_per_vehicle(self):
        """Sol review finding: the delta is a difference of TWO rounded values,
        so the conservative per-vehicle bound is a full ULP. A residual between
        one and two half-ULPs per vehicle is still fully explained by rounding
        and must not be reported as an unexplained mechanism."""
        assert diag.QUANT_PER_VEHICLE_BOUND_S == pytest.approx(
            2 * diag.QUANT_HALF_ULP_S)
        count = 40
        between = count * diag.QUANT_HALF_ULP_S * 1.5
        out = diag.classify(
            self._obs(total_delta_s=-between, max_abs_delta_s=0.01,
                      vehicle_count=count),
            self._obs(total_delta_s=0.0, max_abs_delta_s=0.0,
                      vehicle_count=count))
        assert out["classification"] == diag.OUTPUT_QUANTIZATION

    def test_a_missing_cohort_field_is_refused_not_assumed_true(self):
        """Sol review: treating an absent `all_cohorts_populated` as acceptable
        let an observation block skip the check entirely."""
        incomplete = self._obs()
        incomplete.pop("all_cohorts_populated")
        with pytest.raises(SystemExit, match="all_cohorts_populated"):
            diag.classify(incomplete, self._obs())

    def test_malformed_observations_fail_closed(self):
        with pytest.raises(SystemExit, match="missing"):
            diag.classify({"partition_ok": True}, self._obs())

    def test_the_classifier_never_claims_equivalence_or_speedup(self):
        source = TOOL.read_text().lower()
        for banned in ("equivalent", "speedup", "ready for adoption"):
            assert banned not in source.split("claim_scope")[0], banned


class TestFrozenContract:
    """Criterion 6."""

    def _load(self):
        return json.loads(CONTRACT.read_text())

    def test_the_contract_exists_and_is_canonical(self):
        payload = self._load()
        body = {k: v for k, v in payload.items() if k != "content_key"}
        assert hashlib.sha256(diag.canonical_bytes(body)).hexdigest() == \
            payload["content_key"]

    def test_it_reproduces_byte_for_byte(self):
        before = CONTRACT.read_bytes()
        assert diag.verify()
        assert CONTRACT.read_bytes() == before, "verification mutated it"

    def test_it_is_frozen_unapproved_and_unexecuted(self):
        assert self._load()["status"] == diag.STATUS_FROZEN

    def test_it_grants_no_execution_authority(self):
        authority = self._load()["execution_authority"]
        assert authority.startswith("NONE")
        assert "exact user approval" in authority

    def test_it_binds_the_tracked_network(self):
        requirement = self._load()["network_requirement"]
        assert requirement["path"] == diag.APPROVED_NETWORK
        assert requirement["sha256"] == diag.sha256_file(
            diag.ROOT / diag.APPROVED_NETWORK)

    def test_it_binds_the_production_producers_and_combiner(self):
        fingerprints = self._load()["source_fingerprints"]
        for required in ("traffic_sim/simulation/metrics.py",
                         "traffic_sim/simulation/warm_state_boundary.py",
                         "traffic_sim/simulation/monthly_warm_state.py",
                         "traffic_sim/simulation/monthly_sumo.py",
                         "tools/diagnose_warm_state_population_semantics.py",
                         "tests/test_warm_state_population_semantics.py"):
            assert required in fingerprints, required

    def test_every_bound_source_matches_the_live_tree(self):
        for name, digest in self._load()["source_fingerprints"].items():
            assert diag.sha256_file(Path(name)) == digest, name

    def test_a_source_edit_would_invalidate_the_contract(self):
        """Drift detection is real, proven without touching the tree."""
        fingerprints = self._load()["source_fingerprints"]
        target = "traffic_sim/simulation/warm_state_boundary.py"
        mutated = Path(target).read_bytes() + b"\n# drift\n"
        assert hashlib.sha256(mutated).hexdigest() != fingerprints[target]

    def test_it_records_the_refuted_hypothesis_and_the_measured_residual(self):
        payload = self._load()
        assert "-7.73" in payload["question"]
        assert "did not move" in payload["refuted_hypothesis"]
        assert "ONE vehicle" in payload["why_the_v2_diagnostic_could_not_answer_it"]

    def test_it_records_the_full_fixture_and_arms(self):
        payload = self._load()
        assert len(payload["fixture"]["population"]) == (
            diag.COHORT_COMPLETED_BEFORE + diag.COHORT_ACTIVE_AT
            + diag.COHORT_DEPART_AFTER)
        assert set(payload["arms"]) == set(diag.ARMS)
        assert payload["classification_contract"]["default"] == diag.INCONCLUSIVE
        assert payload["classification_contract"]["production_precision"] == \
            diag.TRIPINFO_PRECISION

    def test_it_names_its_future_output_root_without_creating_it(self):
        assert self._load()["output_root"] == diag.APPROVED_OUTPUT_ROOT
        assert not Path(diag.APPROVED_OUTPUT_ROOT).exists()

    def test_the_claim_scope_forbids_adoption(self):
        scope = self._load()["claim_scope"]
        assert "NOT equivalence evidence" in scope
        assert "warming stays OFF" in scope

    def test_freezing_refuses_to_clobber(self, tmp_path):
        target = tmp_path / "c.json"
        diag.freeze(target)
        with pytest.raises(SystemExit, match="refusing to overwrite"):
            diag.freeze(target)

    def test_verify_reports_drift_without_rewriting(self, tmp_path):
        target = tmp_path / "c.json"
        diag.freeze(target)
        target.write_text(json.dumps({"schema": "tampered"}))
        assert diag.verify(target) is False
        assert json.loads(target.read_text())["schema"] == "tampered"

    def test_a_missing_contract_fails_closed(self, tmp_path):
        with pytest.raises(SystemExit, match="missing"):
            diag.verify(tmp_path / "absent.json")

    def test_the_cli_requires_exactly_one_mode(self):
        with pytest.raises(SystemExit, match="exactly one"):
            diag.main([])
