"""
Contract tests for SUMO scenario output (run_scenario.py).

Scenario files must satisfy the same flowAt seam as flows.json, plus the
scenario extensions (metadata + per-edge confidence). Skipped if no
scenarios have been generated yet.
"""

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import run_scenario

SCEN_DIR   = Path(__file__).parent.parent / "web" / "data" / "scenarios"
INDEX_PATH = SCEN_DIR / "index.json"
GEO_PATH   = Path(__file__).parent.parent / "web" / "data" / "network.geojson"

needs_scenarios = pytest.mark.skipif(
    not INDEX_PATH.exists(), reason="no scenarios built — run run_scenario.py"
)


@pytest.fixture(scope="module")
def index():
    with open(INDEX_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def geo_edge_ids():
    with open(GEO_PATH) as f:
        geo = json.load(f)
    return {feat["properties"]["id"] for feat in geo["features"]}


class TestLoadGeojsonMetaConfidence:
    """confidence=0 is a real value (far-from-sensor extrapolation), not a
    missing one — on the current network it is the MOST COMMON value
    (6 569/7 147 edges). The old `p.get("confidence") or 0.5` coerced every
    such edge to 0.5, displaying 50% confidence exactly where the spatial
    prior deliberately says 0% (IMPROVEMENT_REVIEW_2026-07-10 item 13.1)."""

    @staticmethod
    def _geo(tmp_path, props_list):
        geo = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
             "properties": props}
            for props in props_list
        ]}
        path = tmp_path / "network.geojson"
        path.write_text(json.dumps(geo))
        return path

    def test_zero_confidence_is_preserved_not_coerced_to_half(
            self, tmp_path, monkeypatch):
        path = self._geo(tmp_path, [
            {"id": "far_edge", "confidence": 0},
            {"id": "near_edge", "confidence": 0.93},
            {"id": "legacy_edge"},   # property absent entirely
        ])
        monkeypatch.setattr(run_scenario, "GEO_PATH", path)
        prior, _names = run_scenario.load_geojson_meta()
        assert prior["far_edge"] == 0.0        # the bug coerced this to 0.5
        assert prior["near_edge"] == 0.93
        assert prior["legacy_edge"] == 0.5     # only true absence falls back


class TestValidScenarioName:
    """--name flows into filesystem paths under sumo/ and web/data/scenarios/
    — a strict slug keeps generated paths inside those directories
    (IMPROVEMENT_REVIEW_2026-07-10 item 13.7, narrow scope; the API path was
    already safe since serve.py rejects unknown edge IDs before they can
    reach a filename)."""

    def test_accepts_the_auto_generated_name_shapes(self):
        assert run_scenario.valid_scenario_name("baseline")
        assert run_scenario.valid_scenario_name("close_60786979_3575001205_0")
        assert run_scenario.valid_scenario_name(
            "close_60786979_3575001205_0+1455801464_18241874_0")
        assert run_scenario.valid_scenario_name("close_2edges_a1b2c3d4")

    def test_rejects_path_separators_and_traversal(self):
        assert not run_scenario.valid_scenario_name("../escape")
        assert not run_scenario.valid_scenario_name("sub/dir")
        assert not run_scenario.valid_scenario_name("back\\slash")
        assert not run_scenario.valid_scenario_name("..")

    def test_rejects_spaces_empty_and_overlong(self):
        assert not run_scenario.valid_scenario_name("has space")
        assert not run_scenario.valid_scenario_name("")
        assert not run_scenario.valid_scenario_name("x" * 81)
        assert run_scenario.valid_scenario_name("x" * 80)


class TestClosureIntegrityStatus:
    """closure_integrity_status (found in review 2026-07-12): a genuinely
    verified-clean closure (active_closure_entries == 0) must not be
    reported the same way as "never measured" — 0 is falsy in Python, so
    the original inline `if active_closure_entries else ...` conflated the
    two. Extracted to a pure function so this is directly testable without
    a real SUMO run."""

    def test_positive_entries_is_a_failure(self):
        assert run_scenario.closure_integrity_status(3, [{"edge_id": "a"}]) == \
            "failed_active_edge_flow"

    def test_zero_entries_is_verified_clean_not_unmeasured(self):
        assert run_scenario.closure_integrity_status(0, [{"edge_id": "a"}]) == \
            "verified_clean"

    def test_none_entries_with_closures_is_not_measurable(self):
        assert run_scenario.closure_integrity_status(None, [{"edge_id": "a"}]) == \
            "not_measurable"

    def test_none_entries_without_closures_is_none(self):
        assert run_scenario.closure_integrity_status(None, []) is None


class TestAtomicWriteJson:
    """atomic_write_json (found in review 2026-07-10): a live browser polling
    a scenario/index file with cache: 'no-store' must never observe a
    truncated write while run_scenario.py or serve.py's recalibration
    thread overwrites it in place."""

    def test_writes_valid_json_readable_after_call(self, tmp_path):
        path = tmp_path / "out.json"
        run_scenario.atomic_write_json(path, {"a": 1, "b": [1, 2, 3]})
        assert json.loads(path.read_text()) == {"a": 1, "b": [1, 2, 3]}

    def test_old_content_survives_until_the_new_write_fully_lands(self, tmp_path):
        path = tmp_path / "out.json"
        run_scenario.atomic_write_json(path, {"version": "old"})
        assert json.loads(path.read_text()) == {"version": "old"}
        run_scenario.atomic_write_json(path, {"version": "new"})
        # No intermediate state should ever be observable from outside this
        # call — the only two valid reads are fully-old or fully-new.
        assert json.loads(path.read_text()) == {"version": "new"}

    def test_no_leftover_temp_file_after_a_successful_write(self, tmp_path):
        path = tmp_path / "out.json"
        run_scenario.atomic_write_json(path, {"a": 1})
        leftovers = [p for p in tmp_path.iterdir() if p != path]
        assert leftovers == []

    def test_temp_file_is_cleaned_up_on_a_failed_write(self, tmp_path):
        path = tmp_path / "out.json"

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            run_scenario.atomic_write_json(path, {"a": Unserializable()})
        assert not path.exists()
        leftovers = list(tmp_path.iterdir())
        assert leftovers == []

    def test_accepts_json_dump_kwargs(self, tmp_path):
        path = tmp_path / "out.json"
        run_scenario.atomic_write_json(path, {"a": 1}, indent=2)
        assert "\n" in path.read_text()   # indent=2 forces multi-line output


@needs_scenarios
def test_index_lists_existing_files(index):
    assert index["scenarios"], "index.json has no scenarios"
    for s in index["scenarios"]:
        assert (SCEN_DIR / s["file"]).exists(), f"missing file {s['file']}"
        assert s["name"] and s["label"]


@needs_scenarios
def test_scenario_files_satisfy_flow_contract(index, geo_edge_ids):
    for s in index["scenarios"]:
        with open(SCEN_DIR / s["file"]) as f:
            data = json.load(f)

        assert data["interval_minutes"] == 15
        assert "T" in data["epoch"]                    # ISO datetime
        assert data["scenario"]["name"] == s["name"]

        lengths = {len(arr) for arr in data["flows"].values()}
        assert len(lengths) == 1, "all flow arrays must have equal length"

        # Same ID space as the map — every edge must be drawable
        unknown = set(data["flows"]) - geo_edge_ids
        assert not unknown, f"{s['name']}: edges not in network.geojson: {sorted(unknown)[:5]}"

        for eid, arr in data["flows"].items():
            assert all(v is None or v >= 0 for v in arr), f"negative flow on {eid}"

        # Confidence: subset of flow edges, all in [0, 1]
        conf = data.get("confidence", {})
        assert set(conf) <= set(data["flows"])
        assert all(0.0 <= v <= 1.0 for v in conf.values())


@needs_scenarios
def test_closed_edges_have_reduced_flow(index):
    """Every closed edge must carry (almost) no traffic in its own scenario."""
    files = {s["name"]: s for s in index["scenarios"]}
    closures = [s for s in files.values() if s.get("closed_edges")]
    if not closures or "baseline" not in files:
        pytest.skip("need baseline + at least one closure scenario")

    with open(SCEN_DIR / files["baseline"]["file"]) as f:
        base = json.load(f)["flows"]
    for s in closures:
        with open(SCEN_DIR / s["file"]) as f:
            closed = json.load(f)["flows"]
        for ce in s["closed_edges"]:
            base_total   = sum(v or 0 for v in base.get(ce, []))
            closed_total = sum(v or 0 for v in closed.get(ce, []))
            assert closed_total < 0.2 * max(base_total, 1), (
                f"{s['name']}: closed edge {ce} still carries {closed_total} "
                f"(baseline {base_total})"
            )


class TestSumoTimeout:
    """Neither sumo subprocess call had a timeout — a hung sumo process had
    no bound, and if THIS script's own parent (e.g. serve.py's outer
    subprocess.run) times out and kills it first, the sumo grandchild is
    orphaned permanently (a timeout only ever kills its direct child).
    Found in review 2026-07-07."""

    def test_run_sumo_timeout_exits_cleanly(self, monkeypatch, tmp_path):
        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="sumo", timeout=kw.get("timeout"))
        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            run_scenario.run_sumo(1000, tmp_path / "r.rou.xml", [],
                                  duration_s=900, home=tmp_path)

    def test_export_trajectories_timeout_returns_none_not_raises(self, monkeypatch, tmp_path):
        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="sumo", timeout=kw.get("timeout"))
        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        result = run_scenario.export_trajectories(
            "baseline", tmp_path / "r.rou.xml", [], duration_s=900,
            home=tmp_path, web_edges=set())
        assert result is None

    def test_run_sumo_metrics_are_opt_in(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)

        normal = run_scenario.run_sumo(
            1000, tmp_path / "demand.rou.xml", [], 900, tmp_path)
        measured = run_scenario.run_sumo(
            1001, tmp_path / "demand.rou.xml", [], 900, tmp_path, metrics=True)

        assert normal is None
        assert "--tripinfo-output" not in commands[0]
        assert "--statistic-output" not in commands[0]
        assert "--tripinfo-output" in commands[1]
        assert "--tripinfo-output.write-unfinished" in commands[1]
        assert "--statistic-output" in commands[1]
        assert "--summary-output" in commands[1]
        assert measured is not None

    def test_metrics_run_label_separates_signal_condition_artifacts(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        baseline = run_scenario.run_sumo(
            1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
            metrics=True, run_label="d2_baseline")
        candidate = run_scenario.run_sumo(
            1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
            metrics=True, run_label="d2_adapted")

        assert baseline["tripinfo"] != candidate["tripinfo"]
        assert "d2_baseline" in baseline["tripinfo"].name
        assert "d2_adapted" in candidate["tripinfo"].name


class TestRunSumoFlushOffset:
    """flush_s (fixed 2026-07-11, external review NEW_CHANGES_REVIEW section
    1): a bounded time-of-day window experiment must not admit departures
    past the requested window end. The default 3600s meso flush, reused
    unchanged for D1/D2/D3's windowed calls, let vehicles scheduled up to an
    hour AFTER the window still depart and count toward it — verified
    empirically against real demand (55% of a nominal 07:00-09:00 run's
    tripinfo entries had depart >= 09:00)."""

    def test_default_flush_is_3600_unchanged(self, monkeypatch, tmp_path):
        # Every whole-period caller (run_scenario.py main(), suggest_
        # closure_time.py) relies on this default for meso's insertion
        # backlog -- must be byte-identical to before flush_s existed.
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 7200, tmp_path)
        cmd = commands[0]
        assert cmd[cmd.index("--end") + 1] == str(7200 + 3600)

    def test_explicit_flush_s_zero_caps_end_exactly_at_the_window(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 28800,
                              tmp_path, flush_s=0)
        cmd = commands[0]
        assert cmd[cmd.index("--end") + 1] == "28800"


class TestRunSumoBeginOffset:
    """begin_s (added for signal_lab.py, PLAN.md D1): a bounded time-of-day
    window must shift SUMO's own --begin, not just filter results after the
    fact, so vehicles outside the window are never even inserted."""

    def test_default_begin_is_zero_unchanged(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path)
        cmd = commands[0]
        assert cmd[cmd.index("--begin") + 1] == "0"

    def test_explicit_begin_s_is_passed_through(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 7200,
                              tmp_path, begin_s=3600)
        cmd = commands[0]
        assert cmd[cmd.index("--begin") + 1] == "3600"
        # duration_s stays the far-end offset from t=0, unaffected by begin_s.
        assert cmd[cmd.index("--end") + 1] == str(7200 + 3600)


class TestRunSumoNetPath:
    """net_path (added for signal_optimize.py, PLAN.md D2): comparing TLS
    types (actuated/delay_based) needs an ALTERNATE net.net.xml, since
    those types are baked in at netconvert time, not a sumo runtime flag."""

    def test_default_net_path_is_the_module_constant(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path)
        cmd = commands[0]
        assert cmd[cmd.index("-n") + 1] == str(run_scenario.NET_PATH.resolve())

    def test_explicit_net_path_overrides_the_default(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        alt_net = tmp_path / "net_actuated.net.xml"
        run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
                              net_path=alt_net)
        cmd = commands[0]
        assert cmd[cmd.index("-n") + 1] == str(alt_net.resolve())


class TestRunSumoVehrouteOutput:
    """vehroute_output (added for signal_closure_combine.py, PLAN.md D4):
    extracting the ACTUALLY-driven post-closure routes needs the runtime
    rerouter's real decisions, only available from --vehroute-output,
    requested from the SAME run as the disruption metrics."""

    def test_default_omits_vehroute_flags(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        result = run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path)
        assert "--vehroute-output" not in commands[0]
        assert result is None   # every existing caller's behaviour unchanged

    def test_vehroute_output_adds_the_flag_and_returns_its_path(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        vr_path = tmp_path / "vehroutes.xml"
        result = run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
                                       vehroute_output=vr_path)
        cmd = commands[0]
        assert cmd[cmd.index("--vehroute-output") + 1] == str(vr_path.resolve())
        assert "--vehroute-output.exit-times" in cmd
        assert result == {"vehroute": vr_path}

    def test_metrics_and_vehroute_output_combine_in_one_run(self, monkeypatch, tmp_path):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stderr="")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)
        monkeypatch.setattr(run_scenario, "SUMO_DIR", tmp_path)
        vr_path = tmp_path / "vehroutes.xml"
        result = run_scenario.run_sumo(1000, tmp_path / "demand.rou.xml", [], 900, tmp_path,
                                       metrics=True, vehroute_output=vr_path)
        assert set(result) == {"tripinfo", "statistics", "summary", "vehroute"}
        assert "--tripinfo-output" in commands[0]
        assert "--vehroute-output" in commands[0]


class TestTrajectorySimulationMode:
    """A micro scenario used micro edge-flow simulation but its vehroute
    export always forced --mesosim, so the web UI displayed vehicle timings
    from a different simulation mode. Found in hygiene review 2026-07-10."""

    @pytest.mark.parametrize("micro", [False, True])
    def test_export_trajectories_matches_requested_simulation_mode(
            self, monkeypatch, tmp_path, micro):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, stderr="expected")

        monkeypatch.setattr(run_scenario.subprocess, "run", fake_run)

        result = run_scenario.export_trajectories(
            "mode-test", tmp_path / "r.rou.xml", [], duration_s=900,
            home=tmp_path, web_edges=set(), micro=micro)

        assert result is None
        assert ("--mesosim" in commands[0]) is not micro


class TestScenarioManifestDemandScope:
    def test_single_day_signature_is_identical_to_pre_b1_signature(self):
        meta = {
            "date": "2025-09-16", "source": "historical", "begin": "00:00",
            "end": "24:00", "n_intervals": 96,
            "epoch_sim": "2025-09-16T00:00:00", "n_variants": 3,
            "start_date": "2025-09-16", "days": 1,
            "end_date_exclusive": "2025-09-17",
            "day_boundaries_s": [0, 86400], "day_kinds": ["weekday"],
        }
        # Exact SHA-1/12 value emitted by the pre-B1 implementation.
        assert run_scenario.demand_signature(meta) == "b5116ac70049"

    def test_multi_day_signature_uses_range_contract(self):
        meta = {
            "source": "historical", "n_intervals": 192,
            "epoch_sim": "2025-09-16T00:00:00", "n_variants": 3,
            "start_date": "2025-09-16", "days": 2,
            "end_date_exclusive": "2025-09-18",
            "day_boundaries_s": [0, 86400, 172800],
            "day_kinds": ["weekday", "weekday"],
        }
        changed = dict(meta, end_date_exclusive="2025-09-19",
                       day_boundaries_s=[0, 86400, 172800, 259200])

        assert run_scenario.demand_signature(meta) != run_scenario.demand_signature(changed)

    def test_demand_signature_changes_when_window_changes(self):
        meta = {
            "date": "2025-09-16",
            "source": "historical",
            "begin": "00:00",
            "end": "24:00",
            "n_intervals": 96,
            "epoch_sim": "2025-09-16T00:00:00",
            "n_variants": 3,
        }
        changed = dict(meta, begin="07:00", end="09:00", n_intervals=8,
                       epoch_sim="2025-09-16T07:00:00")

        assert run_scenario.demand_signature(meta) != run_scenario.demand_signature(changed)

    def test_window_label_single_day(self):
        meta = {"date": "2025-09-16", "begin": "00:00", "end": "24:00"}
        assert run_scenario.demand_window_label(meta) == "2025-09-16 00:00–24:00"

    def test_window_label_multi_day_does_not_need_date_key(self):
        meta = {"start_date": "2025-09-16", "end_date_exclusive": "2025-09-18",
                "days": 2}
        assert "date" not in meta  # regression guard for the KeyError this fixes
        assert run_scenario.demand_window_label(meta) == \
            "2025-09-16 → 2025-09-18 (2 days)"

    def test_trajectories_default_on_for_single_day(self):
        args = argparse.Namespace(no_trajectories=False, trajectories=False)
        assert run_scenario.want_trajectories(args, n_intervals=96) is True

    def test_trajectories_default_off_above_one_day(self):
        args = argparse.Namespace(no_trajectories=False, trajectories=False)
        assert run_scenario.want_trajectories(args, n_intervals=192) is False

    def test_trajectories_flag_forces_on_for_multi_day(self):
        args = argparse.Namespace(no_trajectories=False, trajectories=True)
        assert run_scenario.want_trajectories(args, n_intervals=672) is True

    def test_no_trajectories_flag_forces_off_for_single_day(self):
        args = argparse.Namespace(no_trajectories=True, trajectories=False)
        assert run_scenario.want_trajectories(args, n_intervals=96) is False

    def test_trajectories_and_no_trajectories_together_is_a_cli_error(self, monkeypatch):
        monkeypatch.setattr(sys, "argv",
                            ["run_scenario.py", "--trajectories", "--no-trajectories"])
        with pytest.raises(SystemExit):
            run_scenario.parse_args()

    def test_aggregate_flows_includes_edges_with_zero_traffic_in_every_seed(self):
        """Finding #1 from a bug review 2026-07-10, independently verified
        and fixed: excludeEmpty="true" means an edge with genuinely zero
        traffic in every seed never appears in any per_seed dict at all —
        it must still get a real (zero) flows_out entry, not be silently
        dropped and rendered as a missing-data gap."""
        per_seed = [{"busy_edge": np.array([5.0, 5.0])}, {"busy_edge": np.array([5.0, 5.0])}]
        web_edges = {"busy_edge", "quiet_edge"}
        prior = {"busy_edge": 0.9, "quiet_edge": 0.7}

        flows_out, conf_out = run_scenario.aggregate_flows(per_seed, web_edges, prior, 2)

        assert set(flows_out) == web_edges
        assert flows_out["quiet_edge"] == [0, 0]
        assert conf_out["quiet_edge"] == 0.7   # pure spatial prior, no CV to penalize it

    def test_manifest_keeps_only_current_demand_entries(self):
        current = "abc123"
        old = "old999"
        index = {
            "scenarios": [
                {"name": "baseline", "demand_signature": current},
                {"name": "old_closure", "demand_signature": old},
                {"name": "legacy_without_signature"},
            ]
        }

        filtered = run_scenario.index_for_current_demand(index, current)

        assert filtered["demand_signature"] == current
        assert [s["name"] for s in filtered["scenarios"]] == ["baseline"]


class TestTruncateStrandedVehicles:
    """FOUND 2026-07-09: SUMO's runtime rerouter (write_closure_additional)
    reroutes vehicles around a closure fine WHEN a detour exists, but for an
    origin/destination pair with NO detour at all it can't find one either —
    confirmed directly (duarouter given the same closure file still routes
    through the "closed" edge; a rerouter is a runtime-only concept, not
    something the offline router evaluates) — and the vehicle just sits
    stuck until sumo's end-of-run cleanup teleports it past the closure,
    which then shows up in the exported flows/trajectory as if it had
    legitimately driven the closed edge.

    truncate_stranded_vehicles is the fix — but SHORTENS the route to end
    just short of the closure rather than deleting the vehicle outright
    (Gustav, correctly: deleting it also erases its real traffic
    contribution on every edge BEFORE the closure, not just the closed one
    — a driver whose actual destination is now unreachable by car still
    drives most of the way and parks short of it, walking the rest)."""

    @staticmethod
    def write_net(path: Path) -> None:
        # a_b --(closed)--> b_c --> c_d, with a detour a_b->b_e->e_c->c_d;
        # w_x->x_y--(closed)-->y_z is a dead end with no alternative — a
        # vehicle heading there can still legitimately drive w_x->x_y.
        connections = [
            ("a_b", "b_c"), ("b_c", "c_d"),
            ("a_b", "b_e"), ("b_e", "e_c"), ("e_c", "c_d"),
            ("w_x", "x_y"), ("x_y", "y_z"),
        ]
        with open(path, "w") as f:
            f.write("<net>\n")
            for frm, to in connections:
                f.write(f'  <connection from="{frm}" to="{to}"/>\n')
            f.write("</net>\n")

    @staticmethod
    def write_routes(path: Path) -> None:
        with open(path, "w") as f:
            f.write("<routes>\n")
            f.write('  <vehicle id="detourable" depart="0">\n'
                    '    <route edges="a_b b_c c_d"/>\n  </vehicle>\n')
            f.write('  <vehicle id="stranded" depart="0">\n'
                    '    <route edges="w_x x_y y_z"/>\n  </vehicle>\n')
            f.write('  <vehicle id="immediately_stranded" depart="0">\n'
                    '    <route edges="y_z"/>\n  </vehicle>\n')
            f.write('  <vehicle id="untouched" depart="0">\n'
                    '    <route edges="a_b b_e e_c c_d"/>\n  </vehicle>\n')
            f.write("</routes>\n")

    def test_detour_exists_route_is_untouched(self, monkeypatch, tmp_path):
        net_path = tmp_path / "net.net.xml"
        self.write_net(net_path)
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)

        route_path = tmp_path / "in.rou.xml"
        self.write_routes(route_path)
        out_path = tmp_path / "out.rou.xml"

        adj = run_scenario.build_edge_graph({"b_c", "y_z"})
        t, d = run_scenario.truncate_stranded_vehicles(
            route_path, ["b_c", "y_z"], out_path, adj)

        assert (t, d) == (1, 1)
        vehicles = {v.get("id"): v.find("route").get("edges")
                    for v in ET.parse(out_path).getroot().findall("vehicle")}
        assert vehicles["detourable"] == "a_b b_c c_d"        # rerouter handles it live
        assert vehicles["untouched"] == "a_b b_e e_c c_d"
        assert vehicles["stranded"] == "w_x x_y"               # truncated, not deleted
        assert "immediately_stranded" not in vehicles          # nothing to truncate to

    def test_no_affected_vehicles_still_writes_output(self, monkeypatch, tmp_path):
        net_path = tmp_path / "net.net.xml"
        self.write_net(net_path)
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)

        route_path = tmp_path / "in.rou.xml"
        self.write_routes(route_path)
        out_path = tmp_path / "out.rou.xml"

        adj = run_scenario.build_edge_graph({"nonexistent_edge"})
        t, d = run_scenario.truncate_stranded_vehicles(
            route_path, ["nonexistent_edge"], out_path, adj)

        assert (t, d) == (0, 0)
        ids = {v.get("id") for v in ET.parse(out_path).getroot().findall("vehicle")}
        assert ids == {"detourable", "stranded", "immediately_stranded", "untouched"}

    def test_same_origin_and_destination_but_only_one_branch_is_stranded(
            self, monkeypatch, tmp_path):
        """FOUND in Codex review 2026-07-09: the first version of this fix
        cached on (route[0], route[-1]) — global origin/destination
        reachability — as a proxy for "will the live rerouter save this
        vehicle". Wrong: two vehicles can share the same origin AND the
        same destination while being on different candidate routes, one
        of which is already committed to a branch with no way out even
        though the OTHER branch (which this vehicle isn't on) would have
        worked fine. The origin-level check would have left BOTH routes
        untouched (since SOME path from origin to destination exists),
        reproducing the exact teleport-through-a-closed-edge leak for the
        vehicle on the bad branch. The fix checks reachability from each
        vehicle's OWN position right before the closure, not from a
        shared origin."""
        net_path = tmp_path / "net.net.xml"
        connections = [
            ("a_b", "b_g"), ("b_g", "g_z"), ("g_z", "z_d"),        # good branch, avoids closure
            ("a_b", "b_h"), ("b_h", "h_closed"),                    # bad branch: dead end once closed
            ("h_closed", "closed_z"), ("closed_z", "z_d"),
        ]
        with open(net_path, "w") as f:
            f.write("<net>\n")
            for frm, to in connections:
                f.write(f'  <connection from="{frm}" to="{to}"/>\n')
            f.write("</net>\n")
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)

        route_path = tmp_path / "in.rou.xml"
        with open(route_path, "w") as f:
            f.write("<routes>\n")
            f.write('  <vehicle id="good_branch" depart="0">\n'
                    '    <route edges="a_b b_g g_z z_d"/>\n  </vehicle>\n')
            f.write('  <vehicle id="bad_branch" depart="0">\n'
                    '    <route edges="a_b b_h h_closed closed_z z_d"/>\n  </vehicle>\n')
            f.write("</routes>\n")
        out_path = tmp_path / "out.rou.xml"

        adj = run_scenario.build_edge_graph({"h_closed"})
        t, d = run_scenario.truncate_stranded_vehicles(
            route_path, ["h_closed"], out_path, adj)

        assert (t, d) == (1, 0)
        vehicles = {v.get("id"): v.find("route").get("edges")
                    for v in ET.parse(out_path).getroot().findall("vehicle")}
        # same origin (a_b) and same destination (z_d) as bad_branch, but
        # reachable overall — must stay fully untouched
        assert vehicles["good_branch"] == "a_b b_g g_z z_d"
        # stuck on its own branch despite origin->destination being
        # reachable via the OTHER branch — must be truncated, not left as-is
        assert vehicles["bad_branch"] == "a_b b_h"

    def test_multiple_closures_with_a_bypass_leaves_route_untouched(
            self, monkeypatch, tmp_path):
        """A candidate route can pass through TWO closed edges in sequence
        while a real detour exists that avoids both — truncating at the
        FIRST closed edge encountered (ignoring whether a later closure on
        the same route is what actually matters) would wrongly cut off a
        trip the live rerouter can complete just fine. Since `reachable()`
        removes every closed edge at once (not just the first), checking
        from right before the first closure already accounts for the
        second one too."""
        net_path = tmp_path / "net.net.xml"
        connections = [
            ("p_q", "q_r1"), ("q_r1", "r1_s"), ("r1_s", "s_t2"), ("s_t2", "t2_end"),
            ("p_q", "q_r2"), ("q_r2", "r2_s"), ("r2_s", "t2_end"),   # bypass around BOTH closures
        ]
        with open(net_path, "w") as f:
            f.write("<net>\n")
            for frm, to in connections:
                f.write(f'  <connection from="{frm}" to="{to}"/>\n')
            f.write("</net>\n")
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)

        route_path = tmp_path / "in.rou.xml"
        with open(route_path, "w") as f:
            f.write("<routes>\n")
            f.write('  <vehicle id="double_closure" depart="0">\n'
                    '    <route edges="p_q q_r1 r1_s s_t2 t2_end"/>\n  </vehicle>\n')
            f.write("</routes>\n")
        out_path = tmp_path / "out.rou.xml"

        adj = run_scenario.build_edge_graph({"q_r1", "s_t2"})
        t, d = run_scenario.truncate_stranded_vehicles(
            route_path, ["q_r1", "s_t2"], out_path, adj)

        assert (t, d) == (0, 0)
        vehicles = {v.get("id"): v.find("route").get("edges")
                    for v in ET.parse(out_path).getroot().findall("vehicle")}
        assert vehicles["double_closure"] == "p_q q_r1 r1_s s_t2 t2_end"


class TestTimeWindowedClosures:
    def test_write_closure_additional_emits_one_interval_per_window(self, tmp_path):
        path = tmp_path / "closure.add.xml"
        closures = [
            {"edge_id": "a_b", "begin_s": 600, "end_s": 1200},
            {"edge_id": "c_d", "begin_s": 1800, "end_s": 2400},
        ]

        run_scenario.write_closure_additional(path, closures, ["a_b", "c_d"])

        intervals = ET.parse(path).getroot().findall(".//interval")
        assert [(i.get("begin"), i.get("end"),
                 i.find("closingReroute").get("id")) for i in intervals] == [
            ("600", "1200", "a_b"), ("1800", "2400", "c_d")]

    def test_prefilter_only_truncates_windowed_no_detour_when_wait_can_teleport(
            self, monkeypatch, tmp_path):
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <connection from="lead" to="closed"/>
  <connection from="closed" to="destination"/>
</net>""")
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)
        route_path = tmp_path / "in.rou.xml"
        route_path.write_text("""<routes>
  <vehicle id="long_wait" depart="0"><route edges="lead closed destination"/></vehicle>
  <vehicle id="short_wait" depart="330"><route edges="lead closed destination"/></vehicle>
  <vehicle id="after_open" depart="500"><route edges="lead closed destination"/></vehicle>
</routes>""")
        out_path = tmp_path / "out.rou.xml"
        closures = [{"edge_id": "closed", "begin_s": 10, "end_s": 400}]
        adj = run_scenario.build_edge_graph({"closed"})

        truncated, dropped = run_scenario.truncate_stranded_vehicles(
            route_path, ["closed"], out_path, adj, closures=closures,
            edge_travel_s={"lead": 20})

        assert (truncated, dropped) == (1, 0)
        routes = {v.get("id"): v.find("route").get("edges")
                  for v in ET.parse(out_path).getroot().findall("vehicle")}
        assert routes["long_wait"] == "lead"       # 380 s may teleport
        assert routes["short_wait"] == "lead closed destination"  # 50 s waits safely
        assert routes["after_open"] == "lead closed destination"

    def test_reachability_ignores_permissions_known_limitation(self, monkeypatch, tmp_path):
        """Known limitation: build_edge_graph follows every <connection>.

        The bicycle-only detour is topologically reachable, so the current
        prefilter leaves this passenger route intact even though SUMO would
        reject the detour. This test makes the documented vClass/permission
        blind spot explicit; C2 deliberately does not attempt to fix it.
        """
        net_path = tmp_path / "net.net.xml"
        net_path.write_text("""<net>
  <connection from="lead" to="closed"/>
  <connection from="closed" to="destination"/>
  <connection from="lead" to="bike_detour" allow="bicycle"/>
  <connection from="bike_detour" to="destination" allow="bicycle"/>
</net>""")
        monkeypatch.setattr(run_scenario, "NET_PATH", net_path)
        route_path = tmp_path / "in.rou.xml"
        route_path.write_text("""<routes>
  <vType id="car" vClass="passenger"/>
  <vehicle id="passenger" type="car" depart="0"><route edges="lead closed destination"/></vehicle>
</routes>""")
        out_path = tmp_path / "out.rou.xml"
        adj = run_scenario.build_edge_graph({"closed"})
        closures = [{"edge_id": "closed", "begin_s": 0, "end_s": 1000}]

        assert run_scenario.reachable(adj, "lead", "destination", {"closed"})
        assert run_scenario.truncate_stranded_vehicles(
            route_path, ["closed"], out_path, adj, closures=closures) == (0, 0)
        assert ET.parse(out_path).getroot().find("vehicle/route").get("edges") == \
               "lead closed destination"
