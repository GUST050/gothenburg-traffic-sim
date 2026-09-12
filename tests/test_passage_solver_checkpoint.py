"""A retry must reuse only the exact, independently checked numeric problem."""
import json
import numpy as np
import pytest
from scipy.optimize import Bounds, LinearConstraint, OptimizeResult, milp
from scipy.sparse import csr_matrix

pytestmark = pytest.mark.filterwarnings(
    "ignore:Unrecognized options detected.*threads.*:RuntimeWarning")


def problem(target=2):
    return dict(c=np.array([1., 2.]), integrality=np.ones(2),
                bounds=Bounds([0., 0.], [2., 2.]),
                constraints=[LinearConstraint(csr_matrix([[1., 1.]]), target, target)],
                options={'time_limit': 10., 'threads': 1})


def test_retry_reuses_solution_but_changed_bound_does_not(tmp_path):
    from traffic_sim.demand.passage_solver import solve_checkpointed
    calls = []
    def solve(**kwargs):
        calls.append(1)
        return milp(**kwargs)
    first = solve_checkpointed(solve, tmp_path/'first', tmp_path/'cache', **problem())
    second = solve_checkpointed(solve, tmp_path/'retry', tmp_path/'cache', **problem())
    changed = solve_checkpointed(solve, tmp_path/'changed', tmp_path/'cache', **problem(3))
    np.testing.assert_array_equal(first.x, second.x)
    assert changed.x.sum() == 3
    assert len(calls) == 2


def test_timeout_preserves_all_constraints_for_replay(tmp_path):
    from traffic_sim.demand.passage_solver import solve_checkpointed, read_request
    def timeout(**kwargs):
        return OptimizeResult(status=1, x=None, message='Time limit reached')
    result = solve_checkpointed(timeout, tmp_path/'attempt', tmp_path/'cache', **problem())
    assert result.status == 1
    restored = read_request(tmp_path/'attempt'/'request.npz')
    actual = milp(**restored)
    np.testing.assert_array_equal(actual.x, [2, 0])
    assert not list((tmp_path/'cache').glob('*/result.json'))


def test_cache_corruption_is_recomputed(tmp_path):
    from traffic_sim.demand.passage_solver import solve_checkpointed
    solve_checkpointed(milp, tmp_path/'first', tmp_path/'cache', **problem())
    for result in (tmp_path/'cache').glob('*/result.json'):
        result.write_text('{broken')
    result = solve_checkpointed(milp, tmp_path/'retry', tmp_path/'cache', **problem())
    np.testing.assert_array_equal(result.x, [2, 0])


def test_invalid_incumbent_is_never_cached(tmp_path):
    from traffic_sim.demand.passage_solver import solve_checkpointed
    def invalid(**kwargs):
        return OptimizeResult(status=0, x=np.array([0., 0.]), message='invalid')
    result = solve_checkpointed(invalid, tmp_path/'bad', tmp_path/'cache', **problem())
    assert not list((tmp_path/'cache').glob('*/result.json'))
    assert result.status == 0  # The caller retains its own rejection contract.


def test_invalid_incumbent_state_is_not_optimal(tmp_path):
    from traffic_sim.demand.passage_solver import solve_checkpointed
    def invalid(**kwargs):
        return OptimizeResult(status=0, x=np.array([0., 0.]), message='invalid')
    solve_checkpointed(invalid, tmp_path/'bad', tmp_path/'cache', **problem())
    assert json.loads((tmp_path/'bad/state.json').read_text())['status'] == 'invalid_incumbent'


def test_replay_cli_keeps_source_and_refuses_output_reuse(tmp_path):
    import subprocess
    import sys
    from traffic_sim.demand.passage_solver import solve_checkpointed
    solve_checkpointed(milp, tmp_path/'first', tmp_path/'cache', **problem())
    request = tmp_path/'first/request.npz'
    original = request.read_bytes()
    command = [sys.executable, '-m', 'traffic_sim.demand.passage_solver',
               str(request), '--output-dir', str(tmp_path/'replay')]
    first = subprocess.run(command, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    assert json.loads((tmp_path/'replay/state.json').read_text())['status'] == 'optimal'
    second = subprocess.run(command, capture_output=True, text=True, check=False)
    assert second.returncode != 0
    assert request.read_bytes() == original


@pytest.mark.parametrize('change', ['cost', 'capacity', 'coefficient', 'integrality', 'option'])
def test_cache_identity_covers_complete_numeric_problem(tmp_path, change):
    from traffic_sim.demand.passage_solver import solve_checkpointed
    calls = []
    def solve(**kwargs):
        calls.append(1)
        return milp(**kwargs)
    solve_checkpointed(solve, tmp_path/'first', tmp_path/'cache', **problem())
    changed = problem()
    if change == 'cost':
        changed['c'] = np.array([2., 1.])
    elif change == 'capacity':
        changed['bounds'] = Bounds([0., 0.], [1., 2.])
    elif change == 'coefficient':
        changed['constraints'] = [LinearConstraint(csr_matrix([[1., 2.]]), 2, 2)]
    elif change == 'integrality':
        changed['integrality'] = np.array([0, 1])
    else:
        changed['options']['presolve'] = False
    solve_checkpointed(solve, tmp_path/'changed', tmp_path/'cache', **changed)
    assert len(calls) == 2
