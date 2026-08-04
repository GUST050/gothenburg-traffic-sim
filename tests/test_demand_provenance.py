import json

import pytest

from traffic_sim.demand.provenance import validate_calibrated_provenance
from traffic_sim.demand.route_support import combined_route_edges, route_edges


def _artifacts(tmp_path, *, candidate_id="c0"):
    metadata = tmp_path / "candidates.meta.json"
    candidate_routes = tmp_path / "candidates.rou.xml"
    routes = tmp_path / "calibrated.rou.xml"
    agents = tmp_path / "calibrated.agents.json"
    metadata.write_text(json.dumps({
        "schema_version": 1,
        "candidates": {
            "c0": {
                "purpose": "arbete",
                "origin_edge": "A",
                "destination_edge": "C",
            }
        },
    }))
    candidate_routes.write_text(
        '<routes><vehicle id="c0" depart="7.5">'
        '<route edges="A B C"/></vehicle></routes>')
    routes.write_text(
        '<routes><vehicle id="pfe0" depart="12.5">'
        '<route edges="A B C"/></vehicle></routes>')
    agents.write_text(json.dumps({
        "schema_version": 1,
        "agents": [{
            "vehicle_id": "pfe0",
            "candidate_id": candidate_id,
            "purpose": "arbete",
            "origin_edge": "A",
            "destination_edge": "C",
            "purpose_route_compatible": True,
            "departure_s": 12.5,
        }],
    }))
    return candidate_routes, metadata, routes, agents


def test_calibrated_provenance_binds_candidate_agent_and_route(tmp_path):
    candidate_routes, metadata, routes, agents = _artifacts(tmp_path)

    report = validate_calibrated_provenance(
        candidate_routes, metadata, [(routes, agents)])

    assert report["status"] == "pass"
    assert report["vehicles"] == 1
    assert report["candidate_records"] == 1


def test_calibrated_provenance_rejects_unknown_candidate(tmp_path):
    candidate_routes, metadata, routes, agents = _artifacts(
        tmp_path, candidate_id="missing")

    with pytest.raises(ValueError, match="unknown candidate"):
        validate_calibrated_provenance(
            candidate_routes, metadata, [(routes, agents)])


def test_calibrated_provenance_rejects_candidate_path_substitution(tmp_path):
    candidate_routes, metadata, routes, agents = _artifacts(tmp_path)
    routes.write_text(
        '<routes><vehicle id="pfe0" depart="12.5">'
        '<route edges="A X C"/></vehicle></routes>')

    with pytest.raises(ValueError, match="route differs from candidate"):
        validate_calibrated_provenance(
            candidate_routes, metadata, [(routes, agents)])


def test_route_support_reads_every_route_and_invalidates_on_change(tmp_path):
    _candidate_routes, _metadata, routes, _agents = _artifacts(tmp_path)
    other = tmp_path / "other.rou.xml"
    other.write_text('<routes><route id="r0" edges="D E"/></routes>')

    assert route_edges(routes) == frozenset({"A", "B", "C"})
    assert combined_route_edges((routes, other)) == frozenset(
        {"A", "B", "C", "D", "E"})

    routes.write_text(
        '<routes><vehicle id="pfe0" depart="12.5">'
        '<route edges="A B C F"/></vehicle></routes>')
    assert "F" in route_edges(routes)
