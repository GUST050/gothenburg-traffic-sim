from pathlib import Path

import sumo_network_metadata as nm


def tiny_net(path: Path) -> None:
    path.write_text(
        '<net>'
        '<edge id="a" from="0" to="1" length="10" speed="5">'
        '<lane id="a_0" length="10" speed="5" shape="0,0 10,0"/>'
        '</edge>'
        '<edge id=":j_0" function="internal" length="1" speed="1">'
        '<lane id=":j_0_0" length="1" speed="1" shape="10,0 11,0"/>'
        '</edge>'
        '<connection from="a" to=":j_0"/>'
        '<connection from=":j_0" to="a"/>'
        '</net>'
    )


def test_index_is_atomic_and_excludes_internal_edges(tmp_path):
    net = tmp_path / "net.net.xml"
    index = tmp_path / "network_metadata.json"
    tiny_net(net)

    payload = nm.write_metadata(net, index)
    assert set(payload["edges"]) == {"a"}
    assert payload["successors"]["a"] == [":j_0"]
    assert nm.load_metadata(net, index)["net_sha256"] == nm.sha256_file(net)
    assert not list(tmp_path.glob("*.tmp"))


def test_hash_mismatch_rejects_stale_index(tmp_path):
    net = tmp_path / "net.net.xml"
    index = tmp_path / "network_metadata.json"
    tiny_net(net)
    nm.write_metadata(net, index)
    net.write_text(net.read_text().replace('length="10"', 'length="11"', 1))
    assert nm.load_metadata(net, index) is None


def restricted_net(path: Path) -> None:
    """One legal passenger route (a -> b) and one that requires a lane no
    passenger vehicle may use (a -> bike_only, whose only lane disallows
    the single modeled vClass)."""
    path.write_text(
        '<net>'
        '<edge id="a" from="0" to="1" length="10" speed="5">'
        '<lane id="a_0" index="0" length="10" speed="5" shape="0,0 10,0"/>'
        '</edge>'
        '<edge id="b" from="1" to="2" length="10" speed="5">'
        '<lane id="b_0" index="0" length="10" speed="5" shape="10,0 20,0"/>'
        '</edge>'
        '<edge id="bike_only" from="1" to="3" length="10" speed="5">'
        '<lane id="bike_only_0" index="0" length="10" speed="5" '
        'shape="10,0 10,10" disallow="passenger"/>'
        '</edge>'
        '<connection from="a" to="b" fromLane="0" toLane="0"/>'
        '<connection from="a" to="bike_only" fromLane="0" toLane="0"/>'
        '</net>'
    )


def test_edge_with_no_legal_lane_is_restricted_and_excluded_from_successors(tmp_path):
    net = tmp_path / "net.net.xml"
    restricted_net(net)
    payload = nm.build_metadata(net)
    assert payload["vclass"] == nm.DEFAULT_VCLASS
    assert payload["restricted_edges"] == ["bike_only"]
    # "a" only ever legally reaches "b" -- the bike-only successor is gone.
    assert payload["successors"]["a"] == ["b"]


def test_connection_level_disallow_is_independent_of_lane_permission(tmp_path):
    net = tmp_path / "net.net.xml"
    net.write_text(
        '<net>'
        '<edge id="a" from="0" to="1" length="10" speed="5">'
        '<lane id="a_0" index="0" length="10" speed="5" shape="0,0 10,0"/>'
        '</edge>'
        '<edge id="c" from="1" to="2" length="10" speed="5">'
        '<lane id="c_0" index="0" length="10" speed="5" shape="10,0 20,0"/>'
        '</edge>'
        '<connection from="a" to="c" fromLane="0" toLane="0" disallow="passenger"/>'
        '</net>'
    )
    # Both lanes are unrestricted, but the CONNECTION itself is not legal
    # for the modeled class -- it must not appear in successors either.
    payload = nm.build_metadata(net)
    assert payload["restricted_edges"] == []
    assert payload["successors"].get("a", []) == []


def test_cache_built_for_a_different_vclass_is_rejected(tmp_path):
    net = tmp_path / "net.net.xml"
    index = tmp_path / "network_metadata.json"
    tiny_net(net)
    nm.write_metadata(net, index, vclass="bicycle")
    # A schema-2 cache keyed to a different vClass must never satisfy a
    # lookup for the one this project actually models.
    assert nm.load_metadata(net, index, vclass=nm.DEFAULT_VCLASS) is None
    assert nm.load_metadata(net, index, vclass="bicycle") is not None


def test_schema_1_cache_is_rejected(tmp_path):
    import json
    net = tmp_path / "net.net.xml"
    index = tmp_path / "network_metadata.json"
    tiny_net(net)
    payload = nm.build_metadata(net)
    payload["schema_version"] = 1
    del payload["restricted_edges"]
    del payload["vclass"]
    index.write_text(json.dumps(payload))
    assert nm.load_metadata(net, index) is None
