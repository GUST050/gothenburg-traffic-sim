"""Unit tests for paired picker replay analysis helpers."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from traffic_sim.demand.pfe import Candidate


MODULE_PATH = Path(__file__).parent.parent / "tools/analyze_loso_picker_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("analyze_picker", MODULE_PATH)
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def test_selected_edge_series_uses_departure_quarters_and_edge_entries(tmp_path):
    path = tmp_path / "routes.rou.xml"
    path.write_text(
        '<routes>'
        '<vehicle id="a" depart="0"><route edges="O A B D"/></vehicle>'
        '<vehicle id="b" depart="901"><route edges="O B D"/></vehicle>'
        '<vehicle id="c" depart="99999"><route edges="O A D"/></vehicle>'
        '</routes>')

    result = analysis.selected_edge_series(path, ["A", "B"], 2)

    assert result["by_edge"] == {"A": [1, 0], "B": [1, 1]}
    assert result["station_evaluation"] == [2, 1]


def test_streams_large_json_array_without_loading_document(tmp_path):
    path = tmp_path / "diagnostic.json"
    path.write_text(json.dumps({
        "held_component_edges": ["A", "B"],
        "n_quarters": 2,
        "quarters": [{"quarter": 0}, {"quarter": 1}],
        "summary": {"unused": True},
    }, indent=2, sort_keys=True))

    assert analysis.read_leading_json_value(path, "n_quarters") == 2
    assert analysis.read_leading_json_value(path, "held_component_edges") == ["A", "B"]
    assert list(analysis.iter_json_array(path, "quarters", chunk_size=17)) == [
        {"quarter": 0}, {"quarter": 1}]


def test_jaccard_and_mapping_delta_are_deterministic():
    assert analysis.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert analysis.jaccard(set(), set()) == 1.0
    rows = analysis.mapping_delta({"a": 5, "b": 1}, {"a": 2, "c": 8})
    assert [row["key"] for row in rows] == ["c", "a", "b"]


def test_published_shape_counts_maps_route_and_purpose(tmp_path):
    shapes = [
        Candidate(0, ["O", "H", "D"], intent={"purpose": "arbete"}),
        Candidate(0, ["O", "X", "D"], intent={"purpose": "fritid"}),
    ]
    routes = tmp_path / "fold.rou.xml"
    routes.write_text(
        '<routes>'
        '<vehicle id="a" depart="10"><route edges="O H D"/></vehicle>'
        '<vehicle id="b" depart="910"><route edges="O X D"/></vehicle>'
        '</routes>')
    agents = tmp_path / "fold.agents.json"
    agents.write_text(json.dumps({"agents": [
        {"vehicle_id": "a", "purpose": "arbete"},
        {"vehicle_id": "b", "purpose": "fritid"},
    ]}))

    counts = analysis.published_shape_counts(routes, agents, shapes, 2)

    assert counts[0] == {0: 1}
    assert counts[1] == {1: 1}


def test_stage_decomposition_uses_station_edge_entries():
    shapes = [
        Candidate(0, ["O", "A", "B", "D"], intent={"purpose": "arbete"}),
        Candidate(0, ["O", "B", "D"], intent={"purpose": "fritid"}),
    ]

    result = analysis.stage_decomposition(
        shapes, [np.asarray([2.0, 3.0])], ["A", "B"], {("A", "B")})

    assert result["held_evaluation_edge_entries"] == 7.0
    assert result["vehicles_crossing_held_location"] == 5.0
    assert result["held_entry_flow_by_purpose"] == {
        "arbete": 4.0, "fritid": 3.0}
    assert result["held_crossing_flow_by_legal_movement"] == {"A->B": 2.0}


def test_strict_binding_summaries_excludes_zero_and_dropped_bounds():
    diagnostic = {"quarters": [
        {
            "relaxation_rung": "clean",
            "structure_groups": [
                {"name": "zero", "achieved_flow": 0, "utilisation": 1.0},
                {"name": "near", "achieved_flow": 9.9, "utilisation": 0.99},
            ],
            "assignment_ceiling_utilisation": [
                {"edge": "E", "achieved_flow": 99, "utilisation": 0.99},
            ],
        },
        {
            "relaxation_rung": "drop_bounds_tol1",
            "structure_groups": [],
            "assignment_ceiling_utilisation": [
                {"edge": "E", "achieved_flow": 120, "utilisation": 1.2},
            ],
        },
    ]}

    groups, ceilings, maximum = analysis.strict_binding_summaries(diagnostic)

    assert groups == {"near": 1}
    assert ceilings == {"E": 1}
    assert maximum == {"E": 0.99}
