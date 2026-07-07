"""
Contract tests for SUMO scenario output (run_scenario.py).

Scenario files must satisfy the same flowAt seam as flows.json, plus the
scenario extensions (metadata + per-edge confidence). Skipped if no
scenarios have been generated yet.
"""

import json
import subprocess
import sys
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
