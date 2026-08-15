from pathlib import Path

from traffic_sim.simulation import metadata as nm


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
