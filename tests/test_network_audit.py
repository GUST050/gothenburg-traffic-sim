import json
from pathlib import Path

import network_audit as na


class FakeGraph:
    def __init__(self, edges):
        self._edges = edges

    def edges(self, keys=True, data=True):
        return iter(self._edges)


def test_audit_records_sources_roundabouts_and_tls(tmp_path):
    net = tmp_path / "net.net.xml"
    net.write_text("""<net><edge id=\"1_2_0\"/><connection from=\"1_2_0\" to=\"2_3_0\" tl=\"tls0\"/></net>""")
    graph = FakeGraph([
        (1, 2, 0, {"highway": "primary", "maxspeed": "50", "lanes": "2",
                   "oneway": "yes", "turn:lanes": "left|through"}),
        (2, 3, 0, {"highway": "residential", "junction": "roundabout"}),
    ])
    payload = na.build_audit(graph, net)
    first = payload["edges"]["1_2_0"]
    assert first["speed_source"] == "imported"
    assert first["lanes_source"] == "imported"
    assert first["tls_ids"] == ["tls0"]
    assert first["source_tags"]["turn:lanes"] == "left|through"
    assert payload["edges"]["2_3_0"]["roundabout"] is True
    assert payload["edges"]["2_3_0"]["speed_source"] == "defaulted"


def test_write_audit_is_atomic_and_has_network_hash(tmp_path):
    net = tmp_path / "net.net.xml"
    net.write_text("<net/>")
    out = tmp_path / "nested" / "network_audit.json"
    payload = na.write_audit(FakeGraph([]), net, out)
    assert out.exists()
    assert json.loads(out.read_text()) == payload
    assert len(payload["net_sha256"]) == 64
