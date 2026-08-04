import xml.etree.ElementTree as ET

import pytest

from traffic_sim.simulation.warm_route_windows import WarmRouteWindowCache


def _route(path):
    path.write_text(
        "<routes>"
        "<vType id='car'/>"
        "<vehicle id='a' type='car' depart='0'><route edges='a b'/></vehicle>"
        "<vehicle id='b' type='car' depart='899.9'><route edges='b c'/></vehicle>"
        "<vehicle id='c' type='car' depart='900'><route edges='c d'/></vehicle>"
        "<vehicle id='d' type='car' depart='1800'><route edges='d e'/></vehicle>"
        "</routes>"
    )


def test_materializes_exact_half_open_departure_windows(tmp_path):
    source = tmp_path / "source.rou.xml"
    _route(source)
    cache = WarmRouteWindowCache(source, tmp_path / "cache")
    first = cache.materialize(0, 900, tmp_path / "first.rou.xml")
    second = cache.materialize(900, 1800, tmp_path / "second.rou.xml")
    assert [item.get("id") for item in ET.parse(first).getroot().findall("vehicle")] \
        == ["a", "b"]
    assert [item.get("id") for item in ET.parse(second).getroot().findall("vehicle")] \
        == ["c"]
    assert ET.parse(first).getroot().find("vType").get("id") == "car"


def test_source_is_parsed_once_and_fragments_are_reused(tmp_path, monkeypatch):
    source = tmp_path / "source.rou.xml"
    _route(source)
    cache = WarmRouteWindowCache(source, tmp_path / "cache")
    cache.materialize(0, 900, tmp_path / "first.rou.xml")
    monkeypatch.setattr(ET, "iterparse", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("source reparsed")
    ))
    cache.materialize(900, 1800, tmp_path / "second.rou.xml")


def test_refuses_unaligned_unsorted_or_unsupported_routes(tmp_path):
    source = tmp_path / "source.rou.xml"
    _route(source)
    cache = WarmRouteWindowCache(source, tmp_path / "cache")
    with pytest.raises(ValueError, match="align"):
        cache.materialize(1, 900, tmp_path / "bad.rou.xml")

    unsorted = tmp_path / "unsorted.rou.xml"
    unsorted.write_text(
        "<routes><vehicle id='a' depart='10'/><vehicle id='b' depart='9'/></routes>"
    )
    with pytest.raises(ValueError, match="departure-sorted"):
        WarmRouteWindowCache(unsorted, tmp_path / "unsorted-cache").materialize(
            0, 900, tmp_path / "unsorted-window.rou.xml"
        )

    flow = tmp_path / "flow.rou.xml"
    flow.write_text("<routes><flow id='f' begin='0' end='10'/></routes>")
    with pytest.raises(ValueError, match="unsupported"):
        WarmRouteWindowCache(flow, tmp_path / "flow-cache").materialize(
            0, 900, tmp_path / "flow-window.rou.xml"
        )
