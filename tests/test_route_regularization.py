"""Length weighting must remove sensitivity to arbitrary edge segmentation."""
import numpy as np
import pytest
from traffic_sim.demand.pfe import Candidate, path_size_weights
from traffic_sim.demand.route_regularization import length_path_size_weights


def candidates(*routes):
    return [Candidate(0, list(route)) for route in routes]


def test_splitting_an_edge_preserves_length_weighted_cost():
    before = candidates(('shared', 'a'), ('shared', 'b'))
    after = candidates(('s1', 's2', 'a'), ('s1', 's2', 'b'))
    left = length_path_size_weights(before, {'shared': 900, 'a': 100, 'b': 500})
    right = length_path_size_weights(after, {'s1': 300, 's2': 600, 'a': 100, 'b': 500})
    np.testing.assert_allclose(left, right, rtol=1e-14)
    assert not np.allclose(path_size_weights(before), path_size_weights(after), rtol=1e-6)


def test_equal_length_edges_match_existing_policy():
    routes = candidates(('s', 'a'), ('s', 'b'), ('b', 'c'))
    np.testing.assert_allclose(length_path_size_weights(routes, dict.fromkeys('sabc', 10)),
                               path_size_weights(routes))


def test_duplicate_purpose_does_not_change_geometry_weights():
    shapes = candidates(('s', 'a'), ('s', 'b'))
    lengths = dict.fromkeys('sab', 10)
    cost = length_path_size_weights(shapes, lengths)
    duplicate = Candidate(0, ['s', 'a'], intent={'purpose': 'fritid'})
    actual = length_path_size_weights(shapes + [duplicate], lengths)
    np.testing.assert_allclose(actual[:2], cost)
    assert actual[2] == cost[0]


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), None])
def test_invalid_lengths_fail_closed(value):
    with pytest.raises(ValueError):
        length_path_size_weights(candidates(('s',)), {'s': value})


def test_missing_edge_cannot_silently_get_an_invented_length():
    with pytest.raises(ValueError):
        length_path_size_weights(candidates(('s',)), {})


def test_comparison_binds_inputs_and_leaves_them_unchanged(tmp_path):
    import hashlib
    from traffic_sim.demand.route_regularization import compare

    routes = tmp_path / 'routes.xml'
    network = tmp_path / 'network.xml'
    routes.write_text('<routes><vehicle><route edges="s a"/></vehicle>'
                      '<vehicle><route edges="s b"/></vehicle></routes>')
    network.write_text('<net><edge id="s"><lane length="900"/></edge>'
                       '<edge id="a"><lane length="100"/></edge>'
                       '<edge id="b"><lane length="500"/></edge></net>')
    original = {path: path.read_bytes() for path in (routes, network)}
    result = compare(routes, network)
    assert result['unique_geometries'] == 2
    assert result['changed_costs'] == 2
    assert result['production_policy_changed'] is False
    assert result['held_out_accuracy_measured'] is False
    for path, data in original.items():
        assert path.read_bytes() == data
        assert result['input_sha256'][str(path)] == hashlib.sha256(data).hexdigest()
