"""
Contract tests for SUMO scenario output (run_scenario.py).

Scenario files must satisfy the same flowAt seam as flows.json, plus the
scenario extensions (metadata + per-edge confidence). Skipped if no
scenarios have been generated yet.
"""

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

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
