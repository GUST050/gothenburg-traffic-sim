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


def test_audit_flags_length_distortion(tmp_path):
    # The SUMO lane is far shorter than the source edge — exactly the
    # junction-cutting failure the explicit plain-XML length prevents.
    net = tmp_path / "net.net.xml"
    net.write_text("""<net>
      <edge id="1_2_0"><lane id="1_2_0_0" index="0" speed="13.89"
            length="0.20"/></edge>
      <edge id="2_3_0"><lane id="2_3_0_0" index="0" speed="13.89"
            length="88.10"/></edge>
    </net>""")
    graph = FakeGraph([
        (1, 2, 0, {"highway": "residential", "length": 88.0}),
        (2, 3, 0, {"highway": "residential", "length": 88.0}),
    ])
    payload = na.build_audit(graph, net)
    cut = payload["edges"]["1_2_0"]
    assert cut["graph_length_m"] == 88.0
    assert cut["sumo_length_m"] == 0.20
    assert cut["length_ok"] is False
    ok = payload["edges"]["2_3_0"]
    assert ok["length_ok"] is True


def test_audit_length_unknown_when_untagged(tmp_path):
    net = tmp_path / "net.net.xml"
    net.write_text("""<net><edge id="1_2_0"><lane id="1_2_0_0" index="0"
        speed="13.89" length="50.0"/></edge></net>""")
    graph = FakeGraph([(1, 2, 0, {"highway": "residential"})])
    rec = na.build_audit(graph, net)["edges"]["1_2_0"]
    assert rec["graph_length_m"] is None
    assert rec["length_ok"] is None


def test_write_audit_is_atomic_and_has_network_hash(tmp_path):
    net = tmp_path / "net.net.xml"
    net.write_text("<net/>")
    out = tmp_path / "nested" / "network_audit.json"
    payload = na.write_audit(FakeGraph([]), net, out)
    assert out.exists()
    assert json.loads(out.read_text()) == payload
    assert len(payload["net_sha256"]) == 64
