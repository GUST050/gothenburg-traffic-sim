import json
import xml.etree.ElementTree as ET

import pytest

from traffic_sim.demand.provenance import (
    augment_calibrated_edge_support,
    validate_calibrated_provenance,
)


def _fixture(tmp_path, *, forbidden_route=False):
    candidates = tmp_path / "candidates.rou.xml"
    metadata = tmp_path / "candidates.meta.json"
    calibrated = tmp_path / "calibrated.rou.xml"
    agents = tmp_path / "calibrated.agents.json"
    middle = "X" if forbidden_route else "C"
    candidates.write_text(
        '<routes>'
        '<vehicle id="c1" depart="100"><route edges="B %s"/></vehicle>'
        '<vehicle id="c2" depart="200"><route edges="D E"/></vehicle>'
        '</routes>' % middle)
    records = {
        "c1": {"purpose": "arbete", "tour_id": "coverage-1", "leg": "coverage",
               "origin_edge": "B", "destination_edge": middle,
               "support_only": True, "coverage_edge": middle,
               "synthetic_endpoint": True, "unavoidable_loop": False},
        "c2": {"purpose": "service", "tour_id": "coverage-2", "leg": "coverage",
               "origin_edge": "D", "destination_edge": "E",
               "support_only": True, "coverage_edge": "E",
               "synthetic_endpoint": True, "unavoidable_loop": False},
    }
    metadata.write_text(json.dumps({"candidates": records}))
    calibrated.write_text(
        '<routes><vehicle id="pfe0" depart="0"><route edges="A B"/>'
        '</vehicle></routes>')
    agents.write_text(json.dumps({"agents": [{
        "vehicle_id": "pfe0", "candidate_id": "c1", "purpose": "arbete",
        "purpose_route_compatible": True, "origin_edge": "B",
        "destination_edge": middle, "departure_s": 0.0,
    }]}))
    return candidates, metadata, calibrated, agents


def test_augmentation_covers_every_edge_without_touching_measurements(tmp_path):
    candidates, metadata, calibrated, agents = _fixture(tmp_path)
    report = augment_calibrated_edge_support(
        candidates, metadata, calibrated, agents,
        required_edges={"A", "B", "C", "D", "E"}, forbidden_edges={"X"})

    assert report["status"] == "pass"
    assert report["newly_supported_edges"] == 3
    assert report["vehicles_added"] == 2
    actual = {
        edge for vehicle in ET.parse(calibrated).getroot().iter("vehicle")
        for edge in vehicle.find("route").get("edges").split()
    }
    assert actual == {"A", "B", "C", "D", "E"}
    # Validate only augmented agents here: the core fixture intentionally uses
    # a different route than its candidate and is outside this helper's scope.
    document = json.loads(agents.read_text())
    assert sum(agent.get("support_only") is True
               for agent in document["agents"]) == 2


def test_augmentation_refuses_to_change_a_measured_edge(tmp_path):
    candidates, metadata, calibrated, agents = _fixture(
        tmp_path, forbidden_route=True)
    with pytest.raises(ValueError, match="leave.*uncovered"):
        augment_calibrated_edge_support(
            candidates, metadata, calibrated, agents,
            required_edges={"A", "B", "X"}, forbidden_edges={"X"})
