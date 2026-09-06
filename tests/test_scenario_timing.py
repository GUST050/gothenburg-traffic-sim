"""Phase timing must be diagnostic, bound, and invisible when unused.

Two failure modes would make this instrumentation worse than none. It could
change what a scenario publishes — turning an observability feature into a
silent result change — or it could emit timing that does not add up, is not
tied to the run it came from, or survives a partial failure, and a later
optimization would then be steered by a fiction. Every test here is non-SUMO:
the timer, the profile contract and the benchmark binding are exercised with
fakes.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_scenario as rs                     # noqa: E402
from tools import benchmark_speed             # noqa: E402


def _profile(**overrides):
    """A minimal valid profile, so each test can break exactly one thing."""
    profile = {
        "schema_version": rs.PHASE_SCHEMA_VERSION,
        "kind": "scenario_phase_profile",
        "status": "succeeded",
        "scenario_id": "baseline",
        "closures": [],
        "simulation_mode": "meso",
        "seed_set": [1000, 1001, 1002],
        "demand_variant_mapping": {"1000": "q50", "1001": "q10", "1002": "q90"},
        "demand_signature": "sig-abc",
        "demand_build_key": "demand-key",
        "network_build_id": "a" * 64,
        "source_fingerprints": {"run_scenario": "b" * 64, "network": "a" * 64,
                                "demand_meta": "c" * 64},
        "phase_schema": list(rs.PHASE_NAMES),
        "phases": {name: 1.0 for name in rs.PHASE_NAMES},
        "total": float(len(rs.PHASE_NAMES) + 2),
        "unattributed": 2.0,
        "sumo_seconds_by_seed": {"1000": 3.0, "1001": 3.1, "1002": 2.9},
        "seed_job_seconds_by_seed": {"1000": 3.4, "1001": 3.5, "1002": 3.2},
    }
    profile.update(overrides)          # replace, never merge
    return profile


class TestTimerIsInertWhenUnused:

    def test_default_cli_requests_no_sidecar(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["run_scenario.py"])
        assert rs.parse_args().timing_sidecar is None

    def test_a_disabled_timer_records_nothing(self):
        timer = rs.PhaseTimer(enabled=False)
        with timer.phase("sumo_execution"):
            pass
        timer.record_seed(1000, 5.0)
        timings = timer.timings()
        assert all(value == 0.0 for value in timings["phases"].values())
        assert timings["sumo_seconds_by_seed"] == {}

    def test_a_disabled_timer_does_not_police_phase_names(self):
        """Inert means inert: no new error can appear on the default path."""
        timer = rs.PhaseTimer(enabled=False)
        with timer.phase("not_a_real_phase"):
            pass
        with timer.phase("sumo_execution"):
            with timer.phase("cleanup"):        # would be refused if enabled
                pass


class TestPhaseAccounting:

    def test_each_phase_accumulates_and_totals_are_consistent(self):
        timer = rs.PhaseTimer(enabled=True)
        for name in rs.PHASE_NAMES:
            with timer.phase(name):
                pass
        timings = timer.timings()
        assert set(timings["phases"]) == set(rs.PHASE_NAMES)
        assert all(value >= 0 for value in timings["phases"].values())
        assert timings["unattributed"] >= 0
        assert (sum(timings["phases"].values()) + timings["unattributed"]
                == pytest.approx(timings["total"], abs=1e-3))

    def test_a_phase_may_be_reentered_and_accumulates(self):
        timer = rs.PhaseTimer(enabled=True)
        with timer.phase("job_preparation"):
            pass
        first = timer.timings()["phases"]["job_preparation"]
        with timer.phase("job_preparation"):
            pass
        assert timer.timings()["phases"]["job_preparation"] >= first

    def test_overlapping_phases_are_refused_not_double_counted(self):
        timer = rs.PhaseTimer(enabled=True)
        with pytest.raises(RuntimeError, match="must not overlap"):
            with timer.phase("aggregation_validation"):
                with timer.phase("artifact_publication"):
                    pass

    def test_an_unknown_phase_is_refused(self):
        timer = rs.PhaseTimer(enabled=True)
        with pytest.raises(ValueError, match="unknown timing phase"):
            with timer.phase("invented_phase"):
                pass

    def test_per_seed_times_are_separate_from_the_phase_sum(self):
        """Seeds may run concurrently; adding them would exceed the total."""
        timer = rs.PhaseTimer(enabled=True)
        with timer.phase("sumo_execution"):
            pass
        timer.record_seed(1000, 9.0)
        timer.record_seed(1001, 9.0)
        timings = timer.timings()
        assert timings["sumo_seconds_by_seed"] == {"1000": 9.0, "1001": 9.0}
        assert sum(timings["phases"].values()) < 1.0     # unaffected by 18 s
        assert timings["unattributed"] >= 0

    def test_a_phase_still_closes_when_its_body_raises(self):
        timer = rs.PhaseTimer(enabled=True)
        with pytest.raises(SystemExit):
            with timer.phase("input_validation"):
                raise SystemExit("invalid closure")
        with timer.phase("cleanup"):             # not left open
            pass


class TestProfileValidation:

    def test_a_complete_profile_validates(self):
        rs.validate_phase_profile(_profile())

    def test_identity_must_be_complete(self):
        for field in ("scenario_id", "simulation_mode", "demand_signature",
                      "demand_build_key"):
            with pytest.raises(ValueError, match="missing identity"):
                rs.validate_phase_profile(_profile(**{field: ""}))
        with pytest.raises(ValueError, match="network_build_id"):
            rs.validate_phase_profile(_profile(network_build_id=""))
        with pytest.raises(ValueError, match="missing identity: closures"):
            rs.validate_phase_profile(_profile(closures=None))
        with pytest.raises(ValueError, match="missing source fingerprints"):
            rs.validate_phase_profile(_profile(source_fingerprints={}))
        with pytest.raises(ValueError, match="not a sha256 digest"):
            rs.validate_phase_profile(_profile(source_fingerprints={
                "run_scenario": "not-a-digest", "network": "a" * 64,
                "demand_meta": "c" * 64}))

    def test_the_seed_and_variant_mapping_must_cover_the_seed_set(self):
        with pytest.raises(ValueError, match="seed/demand-variant"):
            rs.validate_phase_profile(_profile(
                demand_variant_mapping={"1000": "q50"}))
        with pytest.raises(ValueError, match="missing identity: seed_set"):
            rs.validate_phase_profile(_profile(seed_set=[]))

    def test_only_a_succeeded_run_may_produce_one(self):
        with pytest.raises(ValueError, match="succeeded"):
            rs.validate_phase_profile(_profile(status="failed"))

    def test_malformed_timings_are_refused(self):
        for broken, match in (
                ({"phases": {**{n: 1.0 for n in rs.PHASE_NAMES},
                             "cleanup": -0.5}}, "non-negative"),
                ({"phases": {**{n: 1.0 for n in rs.PHASE_NAMES},
                             "cleanup": float("nan")}}, "non-negative"),
                ({"total": 0.0}, "positive finite"),
                ({"total": float("inf")}, "positive finite"),
                ({"unattributed": -0.1}, "overlap or exceed"),
                ({"unattributed": 99.0}, "do not equal the total"),
        ):
            with pytest.raises(ValueError, match=match):
                rs.validate_phase_profile(_profile(**broken))

    def test_the_frozen_phase_set_cannot_drift(self):
        partial = {name: 1.0 for name in rs.PHASE_NAMES if name != "cleanup"}
        with pytest.raises(ValueError, match="frozen phases"):
            rs.validate_phase_profile(_profile(phases=partial))

    def test_per_seed_times_must_cover_the_seed_set(self):
        with pytest.raises(ValueError, match="does not cover exactly"):
            rs.validate_phase_profile(_profile(
                sumo_seconds_by_seed={"1000": 1.0}))
        with pytest.raises(ValueError, match="not finite"):
            rs.validate_phase_profile(_profile(
                sumo_seconds_by_seed={"1000": 1.0, "1001": 1.0, "1002": -1.0}))

    def test_phase_profile_builds_from_a_timer_and_validates(self):
        timer = rs.PhaseTimer(enabled=True)
        for name in rs.PHASE_NAMES:
            with timer.phase(name):
                pass
        timer.record_seed(1000, 2.0)
        profile = rs.phase_profile(
            timer, scenario_id="close_a_b_0",
            closures=[{"edge_id": "a_b_0",
                       "start_time": "2027-07-15T08:00:00",
                       "end_time": "2027-07-15T12:00:00"}],
            simulation_mode="meso", seed_set=[1000],
            variant_mapping={1000: "q50"}, demand_signature_value="sig",
            demand_build_key="key", network_build_id="a" * 64,
            source_fingerprints={"run_scenario": "b" * 64, "network": "a" * 64,
                                 "demand_meta": "c" * 64})
        rs.validate_phase_profile(profile)
        assert profile["scenario_id"] == "close_a_b_0"
        assert profile["closures"][0]["edge_id"] == "a_b_0"
        assert profile["sumo_seconds_by_seed"] == {"1000": 2.0}

    def test_a_defective_profile_raises_instead_of_being_written(self, tmp_path):
        """The profiler fails; it does not leave a half-true sidecar."""
        timer = rs.PhaseTimer(enabled=True)
        with pytest.raises(ValueError):
            rs.phase_profile(
                timer, scenario_id="", closures=[], simulation_mode="meso",
                seed_set=[1000], variant_mapping={1000: "q50"},
                demand_signature_value="sig", demand_build_key="key",
                network_build_id="a" * 64,
                source_fingerprints={"run_scenario": "b" * 64,
                                     "network": "a" * 64,
                                     "demand_meta": "c" * 64})
        assert list(tmp_path.iterdir()) == []


class TestSidecarIsWrittenAtomically:

    @pytest.mark.parametrize("dump_kwargs", [
        {},
        {"indent": 2, "sort_keys": True},
        {"separators": (",", ":"), "ensure_ascii": False},
    ])
    def test_single_write_encoding_matches_json_dump_exactly(
            self, tmp_path, dump_kwargs):
        target = tmp_path / "artifact.json"
        payload = {
            "unicode": "Göteborg",
            "nested": [{"values": list(range(40))}],
            "flag": True,
        }
        expected = tmp_path / "expected.json"
        with expected.open("w") as stream:
            json.dump(payload, stream, **dump_kwargs)

        rs.atomic_write_json(target, payload, **dump_kwargs)

        assert target.read_bytes() == expected.read_bytes()

    def test_atomic_write_leaves_no_partial_file_on_failure(self, tmp_path,
                                                            monkeypatch):
        target = tmp_path / "phase_profile.json"

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            rs.atomic_write_json(target, {"bad": Unserializable()})
        assert not target.exists()
        assert list(tmp_path.iterdir()) == []

    def test_a_written_sidecar_round_trips(self, tmp_path):
        target = tmp_path / "phase_profile.json"
        rs.atomic_write_json(target, _profile(), indent=2)
        rs.validate_phase_profile(json.loads(target.read_text()))


class TestTimingIsNotAResultChange:

    def test_timings_never_enter_the_semantic_digest(self):
        payload = {"generated_at": "t", "flows": {"a_b_0": [1, 2]},
                   "scenario": {"name": "baseline"}}
        with_timing = {**payload, "phase_profile": _profile()}
        # The digest ignores only declared runtime fields, so a timing sibling
        # WOULD change it — which is precisely why the sidecar is a separate
        # file and never merged into the payload.
        assert (benchmark_speed.canonical_digest(payload)
                != benchmark_speed.canonical_digest(with_timing))
        assert "phase_profile" not in payload

    def test_the_scenario_payload_contract_has_no_timing_keys(self):
        """Guard against a future edit publishing timings by accident."""
        source = Path("run_scenario.py").read_text()
        start = source.index('    payload = {\n        "epoch":')
        payload_block = source[start:source.index("\n    }", start)]
        assert '"flows":' in payload_block          # the real published payload
        for banned in ("phase", "timing", "wall_s", "perf_counter"):
            assert banned not in payload_block


class TestBenchmarkBinding:
    """Sol blocker 2: a sidecar must be bound to the scenario that was
    actually published, not merely to a name and a seed count."""

    FINGERPRINTS = {"source:run_scenario.py": "b" * 64, "network": "a" * 64,
                    "demand_meta": "c" * 64}

    def _payload(self, **scenario_overrides):
        spec = {
            "scenario_id": "baseline",
            "simulation_mode": "meso",
            "network_build_id": "a" * 64,
            "seed_set": [1000, 1001, 1002],
            "demand_variant_mapping": {"1000": "q50", "1001": "q10",
                                       "1002": "q90"},
            "closures": [],
        }
        scenario = {
            "scenario_id": "baseline",
            "demand_signature": "sig-abc",
            "demand_build_key": "demand-key",
        }
        scenario.update(scenario_overrides)
        return {"scenario_spec": spec, "scenario": scenario}

    def _sidecar(self, tmp_path, **overrides):
        path = tmp_path / "phase_profile.json"
        path.write_text(json.dumps(_profile(**overrides)))
        return path

    def test_a_matching_sidecar_is_bound(self, tmp_path):
        profile = benchmark_speed.load_phase_profile(
            self._sidecar(tmp_path), payload=self._payload(),
            inputs=self.FINGERPRINTS)
        assert profile["phases"].keys() == set(rs.PHASE_NAMES)

    def test_a_missing_sidecar_fails_the_case(self, tmp_path):
        with pytest.raises(RuntimeError, match="missing"):
            benchmark_speed.load_phase_profile(
                tmp_path / "absent.json", payload=self._payload(),
                inputs=self.FINGERPRINTS)

    def test_a_malformed_sidecar_fails_the_case(self, tmp_path):
        with pytest.raises(ValueError, match="overlap or exceed"):
            benchmark_speed.load_phase_profile(
                self._sidecar(tmp_path, unattributed=-1.0),
                payload=self._payload(), inputs=self.FINGERPRINTS)

    @pytest.mark.parametrize("overrides,match", [
        ({"scenario_id": "close_other_edge"}, "scenario_id"),
        ({"simulation_mode": "micro"}, "simulation_mode"),
        ({"network_build_id": "d" * 64}, "network_build_id"),
        ({"demand_signature": "another-sig"}, "demand_signature"),
        ({"demand_build_key": "another-key"}, "demand_build_key"),
        ({"seed_set": [2000, 2001, 2002],
          "demand_variant_mapping": {"2000": "q50", "2001": "q10",
                                     "2002": "q90"},
          "sumo_seconds_by_seed": {"2000": 3.0, "2001": 3.0, "2002": 3.0},
          "seed_job_seconds_by_seed": {"2000": 3.4, "2001": 3.4,
                                       "2002": 3.4}}, "seed_set"),
        ({"demand_variant_mapping": {"1000": "q90", "1001": "q10",
                                     "1002": "q50"}},
         "demand_variant_mapping"),
        ({"closures": [{"edge_id": "a_b_0",
                        "start_time": "2027-07-15T08:00:00",
                        "end_time": "2027-07-15T12:00:00"}]}, "closures"),
    ])
    def test_same_name_and_seed_count_but_a_changed_identity_is_refused(
            self, tmp_path, overrides, match):
        """Every one of these keeps the scenario name; none may pass."""
        with pytest.raises(RuntimeError, match=match):
            benchmark_speed.load_phase_profile(
                self._sidecar(tmp_path, **overrides), payload=self._payload(),
                inputs=self.FINGERPRINTS)

    @pytest.mark.parametrize("key", ["run_scenario", "network", "demand_meta"])
    def test_a_changed_source_or_input_generation_is_refused(self, tmp_path,
                                                             key):
        fingerprints = {**_profile()["source_fingerprints"], key: "f" * 64}
        with pytest.raises(RuntimeError, match=key):
            benchmark_speed.load_phase_profile(
                self._sidecar(tmp_path, source_fingerprints=fingerprints),
                payload=self._payload(), inputs=self.FINGERPRINTS)

    def test_a_scenario_missing_an_identity_cannot_be_bound(self, tmp_path):
        payload = self._payload()
        del payload["scenario"]["demand_build_key"]
        with pytest.raises(RuntimeError, match="does not carry"):
            benchmark_speed.load_phase_profile(
                self._sidecar(tmp_path), payload=payload,
                inputs=self.FINGERPRINTS)

    def test_the_spec_serialization_does_not_break_closure_binding(self,
                                                                   tmp_path):
        """ScenarioSpec adds closure_type/exceptions the run record lacks;
        binding must compare identity, not serialization."""
        payload = self._payload()
        payload["scenario_spec"]["closures"] = [{
            "edge_id": "a_b_0", "start_time": "2027-07-15T08:00:00",
            "end_time": "2027-07-15T12:00:00", "closure_type": "full",
            "permitted_access_exceptions": []}]
        profile = benchmark_speed.load_phase_profile(
            self._sidecar(tmp_path, closures=[{
                "edge_id": "a_b_0", "start_time": "2027-07-15T08:00:00",
                "end_time": "2027-07-15T12:00:00"}]),
            payload=payload, inputs=self.FINGERPRINTS)
        assert profile["closures"][0]["edge_id"] == "a_b_0"

    def test_the_benchmark_asks_run_scenario_for_the_sidecar(self):
        source = Path("tools/benchmark_speed.py").read_text()
        assert "--timing-sidecar" in source
        # ... and still compares only the semantic digests.
        assert '"scenario_digest", "trajectory_digest"' in source


class TestManualEnterExitAsMainUsesIt:
    """`main()` opens and closes phases explicitly because the boundaries do
    not nest as `with` blocks. That usage must behave identically."""

    def test_manual_enter_exit_records_when_enabled(self):
        timer = rs.PhaseTimer(enabled=True)
        span = timer.phase("aggregation_validation")
        span.__enter__()
        span.__exit__(None, None, None)
        assert timer.timings()["phases"]["aggregation_validation"] >= 0.0
        with timer.phase("cleanup"):          # the span really did close
            pass

    def test_manual_enter_exit_is_inert_when_disabled(self):
        timer = rs.PhaseTimer(enabled=False)
        span = timer.phase("artifact_publication")
        span.__enter__()
        span.__exit__(None, None, None)
        assert timer.timings()["phases"]["artifact_publication"] == 0.0

    def test_every_frozen_phase_is_opened_by_main(self):
        source = Path("run_scenario.py").read_text()
        main_body = source[source.index("\ndef main() -> None:"):]
        for name in rs.PHASE_NAMES:
            assert f'timer.phase("{name}")' in main_body, name


class TestPerSeedTimingBoundary:
    """Sol blocker 3: the per-seed timer must cover SUMO, not parsing."""

    def _job(self, tmp_path, timing=True):
        return {
            "seed": 1000, "route_path": tmp_path / "calibrated.rou.xml",
            "demand_variant": "q50", "add_paths": [], "duration_s": 900,
            "home": tmp_path, "micro": False,
            "stats_file": tmp_path / "stats.xml", "summary_file": None,
            "days": 1, "day_boundaries_s": [0, 86400],
            "edge_file": tmp_path / "edgedata.xml", "closure_edges": [],
            "n_intervals": 1, "vehroute_output": None,
            "work_dir": tmp_path, "timing": timing,
        }

    def _patch(self, monkeypatch, *, sumo_delay, parse_delay):
        import time as time_module

        def fake_run_sumo(*args, **kwargs):
            time_module.sleep(sumo_delay)

        def fake_parse_edgedata(*args, **kwargs):
            time_module.sleep(parse_delay)
            return {}

        def fake_health(*args, **kwargs):
            time_module.sleep(parse_delay)
            return None

        monkeypatch.setattr(rs, "run_sumo", fake_run_sumo)
        monkeypatch.setattr(rs, "parse_edgedata", fake_parse_edgedata)
        monkeypatch.setattr(rs, "parse_seed_health", fake_health)

    def test_sumo_time_excludes_parsing_and_the_job_span_includes_it(
            self, tmp_path, monkeypatch):
        self._patch(monkeypatch, sumo_delay=0.05, parse_delay=0.03)
        result = rs.run_seed_job(self._job(tmp_path))
        assert 0.05 <= result["sumo_seconds"] < 0.10          # not 0.05+0.06
        assert result["seed_job_seconds"] >= result["sumo_seconds"] + 0.05
        assert result["seed_job_seconds"] >= 0.11

    def test_the_default_path_carries_no_timing_fields(self, tmp_path,
                                                       monkeypatch):
        self._patch(monkeypatch, sumo_delay=0.0, parse_delay=0.0)
        result = rs.run_seed_job(self._job(tmp_path, timing=False))
        assert "sumo_seconds" not in result
        assert "seed_job_seconds" not in result

    def test_the_profile_reports_the_sumo_span_not_the_job_span(self):
        timer = rs.PhaseTimer(enabled=True)
        timer.record_seed(1000, 7.0, 9.5)
        timings = timer.timings()
        assert timings["sumo_seconds_by_seed"] == {"1000": 7.0}
        assert timings["seed_job_seconds_by_seed"] == {"1000": 9.5}

    def test_a_job_span_shorter_than_its_sumo_span_is_refused(self):
        with pytest.raises(ValueError, match="shorter than its SUMO span"):
            rs.validate_phase_profile(_profile(
                sumo_seconds_by_seed={"1000": 5.0, "1001": 1.0, "1002": 1.0},
                seed_job_seconds_by_seed={"1000": 1.0, "1001": 1.0,
                                          "1002": 1.0}))


class TestTheClockStopsBeforeProfilerWork:
    """Sol blocker 4: hashing and writing the sidecar are not the scenario."""

    def test_freeze_excludes_later_work_from_the_total(self):
        import time as time_module
        timer = rs.PhaseTimer(enabled=True)
        with timer.phase("cleanup"):
            time_module.sleep(0.02)
        timer.freeze()
        frozen_total = timer.timings()["total"]
        time_module.sleep(0.08)                     # fingerprinting/writing
        assert timer.timings()["total"] == frozen_total
        assert frozen_total < 0.08

    def test_freezing_twice_keeps_the_first_reading(self):
        import time as time_module
        timer = rs.PhaseTimer(enabled=True)
        timer.freeze()
        first = timer.timings()["total"]
        time_module.sleep(0.02)
        timer.freeze()
        assert timer.timings()["total"] == first

    def test_unattributed_stays_non_negative_after_freezing(self):
        timer = rs.PhaseTimer(enabled=True)
        for name in rs.PHASE_NAMES:
            with timer.phase(name):
                pass
        timer.freeze()
        timings = timer.timings()
        assert timings["unattributed"] >= 0
        assert (sum(timings["phases"].values()) + timings["unattributed"]
                == pytest.approx(timings["total"], abs=1e-3))

    def test_main_freezes_before_hashing_and_writing(self):
        source = Path("run_scenario.py").read_text()
        main_body = source[source.index("\ndef main() -> None:"):]
        freeze_at = main_body.index("timer.freeze()")
        assert freeze_at < main_body.index("sha256_file(NET_PATH)),")
        assert freeze_at < main_body.index("atomic_write_json(Path(args.timing_sidecar)")
        assert main_body.index('timer.phase("cleanup")') < freeze_at


# ── Parallel closure preparation (LUNA-PERF-12) ──────────────────────────────
import xml.etree.ElementTree as ET             # noqa: E402


class TestSerialClosurePreparation:
    """Closure-variant preparation is deterministic serial for every
    seed-worker setting. LUNA-PERF-12 tried a threaded path; the v5 campaign
    measured it as a regression (short, three-variant XML workload dominated by
    SUMO execution), so LUNA-PERF-14 rolled it back. These tests pin the serial
    contract: ordered variant outputs, correct totals, no executor is ever
    constructed, and a failed filter propagates without publishing. Every test
    here is non-SUMO."""

    NET_CONNECTIONS = [
        ("a_b", "b_c"), ("b_c", "c_d"),
        ("a_b", "b_e"), ("b_e", "e_c"), ("e_c", "c_d"),
        ("w_x", "x_y"), ("x_y", "y_z"),
    ]
    NET_EDGES = ("a_b", "b_c", "c_d", "b_e", "e_c", "w_x", "x_y", "y_z")
    EDGE_TRAVEL_S = {edge: 1.0 for edge in NET_EDGES}

    def _write_net(self, path):
        with open(path, "w") as handle:
            handle.write("<net>\n")
            for frm, to in self.NET_CONNECTIONS:
                handle.write(f'  <connection from="{frm}" to="{to}"/>\n')
            handle.write("</net>\n")

    def _write_variant(self, path, extra_stranded):
        # Each variant differs slightly so its output file is distinguishable,
        # but every one has the same detourable/stranded structure.
        with open(path, "w") as handle:
            handle.write("<routes>\n")
            handle.write('  <vehicle id="detourable" depart="0">'
                         '<route edges="a_b b_c c_d"/></vehicle>\n')
            handle.write('  <vehicle id="stranded" depart="0">'
                         '<route edges="w_x x_y y_z"/></vehicle>\n')
            handle.write('  <vehicle id="immediately_stranded" depart="0">'
                         '<route edges="y_z"/></vehicle>\n')
            for i in range(extra_stranded):
                handle.write(f'  <vehicle id="extra_{i}" depart="0">'
                             '<route edges="w_x x_y y_z"/></vehicle>\n')
            handle.write("</routes>\n")

    def _jobs(self, tmp_path, monkeypatch, tag):
        net = tmp_path / "net.net.xml"
        self._write_net(net)
        monkeypatch.setattr(rs, "NET_PATH", net)
        close = ["b_c", "y_z"]
        # `adj` MUST be the full, un-banned edge graph — the reroute planner
        # derives its own per-vehicle banned set (see
        # closure_routing.rewrite_route_file's docstring); every production
        # caller passes build_edge_graph(set()), not the closed edges.
        adj = rs.build_edge_graph(set())
        jobs = []
        for index, extra in enumerate((0, 2, 1)):        # three "q50/q10/q90"
            src = tmp_path / f"variant_{index}.rou.xml"
            self._write_variant(src, extra)
            jobs.append({
                "index": index, "route_path": src, "close_edges": close,
                "out_path": tmp_path / f"variant_{index}_{tag}.rou.xml",
                "adj": adj, "closures": None,
                "edge_travel_s": dict(self.EDGE_TRAVEL_S)})
        return jobs

    @staticmethod
    def _digest(paths):
        return [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths]

    def test_serial_preparation_is_ordered_and_totals_are_summed(
            self, tmp_path, monkeypatch):
        jobs = self._jobs(tmp_path, monkeypatch, "serial")
        paths, rerouted, denied, access_paths = rs.prepare_closure_variants(jobs)
        # Ordered variant positions (index 0,1,2), non-trivial totals.
        assert [p.name for p in paths] == [f"variant_{i}_serial.rou.xml"
                                           for i in range(3)]
        assert [p.name for p in access_paths] == [
            f"variant_{i}_serial.rou.access_impact.json" for i in range(3)]
        assert rerouted > 0             # "detourable" reroutes in every variant
        assert denied > 0               # the isolated w_x/x_y/y_z branch denies
        # Totals equal the sum of each variant's own filtering.
        per_variant = [rs.prepare_variant_job(dict(job, out_path=tmp_path
                       / f"ref_{job['index']}.rou.xml")) for job in jobs]
        assert rerouted == sum(r["rerouted"] for r in per_variant)
        assert denied == sum(r["denied"] for r in per_variant)

    def test_no_executor_is_ever_constructed(self, tmp_path, monkeypatch):
        """The rollback: preparation must never build a thread pool, whatever
        the caller's seed-worker count is (seeds are parallelized elsewhere)."""
        jobs = self._jobs(tmp_path, monkeypatch, "noexec")
        real_pool = rs.ThreadPoolExecutor

        class Spy(real_pool):
            def __init__(self, *a, **k):                 # pragma: no cover
                raise AssertionError("closure preparation must stay serial")

        monkeypatch.setattr(rs, "ThreadPoolExecutor", Spy)
        paths, _, _, _ = rs.prepare_closure_variants(jobs)
        assert len(paths) == 3

    def test_serial_preparation_calls_variants_in_index_order(
            self, tmp_path, monkeypatch):
        jobs = self._jobs(tmp_path, monkeypatch, "ord")
        seen = []
        real = rs.prepare_variant_job

        def spy(job):
            seen.append(job["index"])
            return real(job)

        monkeypatch.setattr(rs, "prepare_variant_job", spy)
        paths, _, _, _ = rs.prepare_closure_variants(jobs)
        assert seen == [0, 1, 2]                          # exact serial order
        assert [p.name for p in paths] == [f"variant_{i}_ord.rou.xml"
                                           for i in range(3)]

    def test_a_filter_failure_propagates_and_publishes_nothing(
            self, tmp_path, monkeypatch):
        jobs = self._jobs(tmp_path, monkeypatch, "fail")
        real = rs.prepare_variant_job

        def explode(job):
            if job["index"] == 1:
                raise RuntimeError("filter blew up")
            return real(job)

        monkeypatch.setattr(rs, "prepare_variant_job", explode)
        with pytest.raises(RuntimeError, match="filter blew up"):
            rs.prepare_closure_variants(jobs)
        # The caller replaces `variants` and publishes ONLY on a clean return,
        # so a raised preparation yields no filtered list to publish from.

    def test_prepare_variant_job_matches_a_direct_filter_call(self, tmp_path,
                                                             monkeypatch):
        [job] = self._jobs(tmp_path, monkeypatch, "unit")[:1]
        direct_out = tmp_path / "direct.rou.xml"
        direct_access = tmp_path / "direct.access_impact.json"
        r, d = rs.reroute_closure_affected_vehicles(
            job["route_path"], job["close_edges"], direct_out, job["adj"],
            closures=None, edge_travel_s=job["edge_travel_s"],
            access_impact_path=direct_access)
        result = rs.prepare_variant_job(job)
        assert (result["rerouted"], result["denied"]) == (r, d)
        assert result["index"] == 0 and result["out_path"] == job["out_path"]
        assert (hashlib.sha256(result["out_path"].read_bytes()).hexdigest()
                == hashlib.sha256(direct_out.read_bytes()).hexdigest())
        assert (hashlib.sha256(result["access_impact_path"].read_bytes())
                .hexdigest()
                == hashlib.sha256(direct_access.read_bytes()).hexdigest())
