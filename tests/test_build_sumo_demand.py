"""build_sumo_demand's direction-share split: the specific behaviour that
prevents a two-way ('Total') sensor's raw count from silently being
duplicated in full onto both of its directed edges (which would make a
perfectly-calibrated direction look like it only delivers ~50%, an artifact
found 2026-07-06 while investigating sensor 107 — see CLAUDE.md)."""

import json
import xml.etree.ElementTree as ET

import build_sumo_demand as bsd


def write_direction_split(tmp_path, shares: dict[str, list[float]]) -> None:
    (tmp_path / "direction_split.json").write_text(json.dumps({
        "107": {"edge_shares": shares},
    }))


def test_build_targets_splits_two_way_total_by_direction_share(monkeypatch, tmp_path):
    monkeypatch.setattr(bsd, "SUMO_DIR", tmp_path)
    write_direction_split(tmp_path, {"edgeN": [0.6] * 96, "edgeS": [0.4] * 96})

    flows = {"edgeN": [100.0], "edgeS": [100.0]}
    sensor_edges = {"107": ["edgeN", "edgeS"]}
    targets = bsd.build_targets(flows, sensor_edges, qi_start=0, n_intervals=1)

    assert targets[0]["edgeN"] == 60.0
    assert targets[0]["edgeS"] == 40.0
    # the two directions must sum back to the raw measured total, not 2x it
    assert targets[0]["edgeN"] + targets[0]["edgeS"] == 100.0


def test_build_targets_single_direction_sensor_takes_full_count(monkeypatch, tmp_path):
    monkeypatch.setattr(bsd, "SUMO_DIR", tmp_path)
    # no direction_split.json at all -> even-split fallback, which for a
    # lone edge is 1/1 = the full count (matches single-direction sensors
    # like 1076, 133, 134, 2276, 1074)
    flows = {"edgeS": [80.0]}
    sensor_edges = {"1076": ["edgeS"]}
    targets = bsd.build_targets(flows, sensor_edges, qi_start=0, n_intervals=1)

    assert targets[0]["edgeS"] == 80.0


def test_write_counts_splits_two_way_total_by_direction_share(monkeypatch, tmp_path):
    monkeypatch.setattr(bsd, "SUMO_DIR", tmp_path)
    write_direction_split(tmp_path, {"edgeN": [0.6] * 96, "edgeS": [0.4] * 96})

    flows = {"edgeN": [100.0], "edgeS": [100.0]}
    sensor_edges = {"107": ["edgeN", "edgeS"]}
    out_path = tmp_path / "counts.xml"
    bsd.write_counts(flows, sensor_edges, qi_start=0, n_intervals=1, out_path=out_path)

    root = ET.parse(out_path).getroot()
    counts = {e.get("id"): float(e.get("count")) for e in root.find("interval")}
    assert counts["edgeN"] == 60.0
    assert counts["edgeS"] == 40.0
