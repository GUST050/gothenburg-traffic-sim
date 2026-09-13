"""Dynamic calibration must count passages, conserve demand and fail closed."""
import numpy as np
import pytest
from traffic_sim.experimental import dynamic_assignment as dynamic


def option(key, depart, edges, offsets, prior=1, group='od', upper=3):
    return dynamic.RouteDeparture(
        key, group, depart, tuple(edges), {'fit': tuple(offsets)}, prior, upper)


def test_one_route_contributes_to_different_sensor_quarters():
    trip = option('trip', 850, ['origin', 'near', 'far'], [0, 20, 100])
    system = dynamic.build_passage_system([trip], ['near', 'far'], 2)
    assert system.project([1])['fit'] == {'far': [0, 1], 'near': [1, 0]}
    result = dynamic.fit_integer_flows(
        system, {'near': [1, 0], 'far': [0, 1]}, {'od': 1})
    assert result.counts.tolist() == [1]
    assert result.status == 'predicted_exact_requires_sumo'


def test_route_and_departure_choices_are_solved_together():
    options = [
        option('wrong-road', 850, ['o', 'b', 'd'], [0, 20, 40]),
        option('too-early', 100, ['o', 'a', 'd'], [0, 100, 120], prior=0),
        option('correct-passage', 850, ['o', 'a', 'd'], [0, 100, 120], prior=0),
    ]
    system = dynamic.build_passage_system(options, ['a', 'b'], 2)
    result = dynamic.fit_integer_flows(system, {'a': [0, 1], 'b': [0, 0]}, {'od': 1})
    assert result.counts.tolist() == [0, 0, 1]


def test_equivalent_fit_preserves_prior_route_mix():
    options = [option('a', 100, ['o', 's'], [0, 10], prior=2),
               option('b', 200, ['o', 's'], [0, 10], prior=0)]
    system = dynamic.build_passage_system(options, ['s'], 1)
    result = dynamic.fit_integer_flows(system, {'s': [2]}, {'od': 2})
    assert result.counts.tolist() == [2, 0]


def test_cannot_steal_flow_from_another_od_purpose_group():
    options = [option('a', 100, ['o', 's'], [0, 10], group='work'),
               option('b', 100, ['o', 'unmeasured'], [0, 10], group='home')]
    system = dynamic.build_passage_system(options, ['s'], 1)
    with pytest.raises(dynamic.DynamicAssignmentError, match='infeasible'):
        dynamic.fit_integer_flows(system, {'s': [2]}, {'work': 1, 'home': 1})


def test_first_edge_emission_revisits_and_boundary_mass():
    options = [option('loop', -10, ['s', 'a', 's', 's'], [0, 5, 10, 910]),
               option('warmup', -20, ['o', 's'], [0, 10])]
    system = dynamic.build_passage_system(options, ['s'], 1)
    assert system.project([1, 1]) == {'fit': {'s': [1]}}
    assert system.boundary_counts([1, 1]) == {'fit': {'s': {'before': 1, 'after': 1}}}


def test_every_learning_travel_time_scenario_must_fit():
    trip = dynamic.RouteDeparture('a', 'od', 850, ('o', 's'),
                                  {'fast': (0, 10), 'slow': (0, 100)}, 1, 2)
    system = dynamic.build_passage_system([trip], ['s'], 2)
    with pytest.raises(dynamic.DynamicAssignmentError, match='infeasible'):
        dynamic.fit_integer_flows(system, {'s': [1, 0]}, {'od': 1})


@pytest.mark.parametrize('offsets', [[0], [0, -1], [0, float('nan')]])
def test_missing_or_invalid_travel_times_are_not_zero_lag(offsets):
    with pytest.raises(ValueError):
        dynamic.build_passage_system([option('a', 100, ['o', 's'], offsets)], ['s'], 1)


def test_fractional_targets_require_explicit_integer_policy():
    system = dynamic.build_passage_system([option('a', 0, ['o', 's'], [0, 1])], ['s'], 1)
    with pytest.raises(ValueError, match='integer'):
        dynamic.fit_integer_flows(system, {'s': [1.4]}, {'od': 1})


def test_capacity_and_sensor_extension_are_hard_constraints():
    options = [option('a', 100, ['o', 'new-sensor'], [0, 10], upper=1)]
    system = dynamic.build_passage_system(options, ['new-sensor'], 1)
    with pytest.raises(dynamic.DynamicAssignmentError, match='infeasible'):
        dynamic.fit_integer_flows(system, {'new-sensor': [2]}, {'od': 2})


def test_departure_support_can_cross_quarter_without_changing_the_route():
    original = option('a', 880, ['o', 's'], [0, 40])
    choices = dynamic.expand_departure_support([original], [-60, 0, 60],
                                              begin_s=0, end_s=1800, guard_s=10)
    assert [x.departure_s for x in choices] == [820, 880, 940]
    assert [x.prior for x in choices] == [0, 1, 0]
    assert all(x.edges == ('o', 's') and x.group == 'od' for x in choices)
    system = dynamic.build_passage_system(choices, ['s'], 2)
    assert system.project([0, 1, 0])['fit']['s'] == [0, 1]
    assert system.project([0, 1, 0])['uncertainty_lower']['s'] == [0, 1]
    assert system.project([0, 1, 0])['uncertainty_upper']['s'] == [0, 1]


def test_passage_boundary_support_uses_route_specific_change_points():
    original = option('a', 850, ['o', 's'], [0, 100])
    choices = dynamic.expand_passage_boundary_support(
        [original], ['s'], max_shift_s=300, begin_s=0, end_s=1800,
        guard_s=0)
    assert [choice.departure_s for choice in choices] == [799.9, 850, 900.0]
    system = dynamic.build_passage_system(choices, ['s'], 2)
    signatures = {
        (int(choice.departure_s // 900),
         tuple(system.project([int(i == index) for i in range(len(choices))])
               ['fit']['s']))
        for index, choice in enumerate(choices)
    }
    assert len(signatures) == len(choices)
    assert sum(choice.prior for choice in choices) == 1


def test_smaller_time_change_is_preferred_when_both_match():
    original = option('a', 850, ['o', 's'], [0, 20])
    choices = dynamic.expand_departure_support([original], [0, 60, 120],
                                              begin_s=0, end_s=1800, guard_s=0)
    result = dynamic.fit_integer_flows(dynamic.build_passage_system(choices, ['s'], 2),
                                      {'s': [0, 1]}, {'od': 1})
    assert result.counts.tolist() == [0, 1, 0]


def test_production_departure_bounds_survive_dynamic_passage_fit():
    options = [option('a', 850, ['o', 'capped', 's'], [0, 10, 100]),
               option('b', 850, ['o', 'other', 's'], [0, 10, 100], prior=0)]
    system = dynamic.build_passage_system(options, ['s'], 2)
    result = dynamic.fit_integer_flows(
        system, {'s': [0, 1]}, {'od': 1},
        departure_bounds=[{'capped': (0, 0)}, {}])
    assert result.counts.tolist() == [0, 1]


def test_departure_group_bounds_preserve_a_structural_class_per_quarter():
    options = [
        option('short', 100, ['o', 's'], [0, 10]),
        option('long', 100, ['o', 's'], [0, 10], prior=0),
    ]
    system = dynamic.build_passage_system(options, ['s'], 1)
    guard = dynamic.DepartureGroupBounds(
        'short_trip', frozenset({'short'}), ((0, 0),))
    result = dynamic.fit_integer_flows(
        system, {'s': [1]}, {'od': 1}, departure_group_bounds=[guard])
    assert result.counts.tolist() == [0, 1]


def test_dynamic_fit_never_relaxes_infeasible_production_bounds():
    system = dynamic.build_passage_system(
        [option('a', 850, ['o', 'capped', 's'], [0, 10, 100])], ['s'], 2)
    with pytest.raises(dynamic.DynamicAssignmentError, match='infeasible'):
        dynamic.fit_integer_flows(system, {'s': [0, 1]}, {'od': 1},
                                  departure_bounds=[{'capped': (0, 0)}, {}])


def test_direction_variants_can_preserve_median_departure_population():
    trips = [option('early', 800, ['o', 's'], [0, 150]),
             option('late', 1000, ['o', 's'], [0, 150], prior=0)]
    system = dynamic.build_passage_system(trips, ['s'], 2)
    fit = dynamic.fit_integer_flows(system, {'s': [0, 1]}, {'od': 1},
                                    departure_totals=[0, 1])
    assert fit.counts.tolist() == [0, 1]
    with pytest.raises(ValueError, match='departure totals'):
        dynamic.fit_integer_flows(system, {'s': [0, 1]}, {'od': 1},
                                  departure_totals=[1])


def test_binary_prior_cost_has_no_auxiliary_integer_program_columns(monkeypatch):
    solve = dynamic.milp
    widths = []
    def record(**kwargs):
        widths.append(len(kwargs['c']))
        return solve(**kwargs)
    monkeypatch.setattr(dynamic, 'milp', record)
    trips = [option('keep', 100, ['o','s'], [0,20], upper=1),
             option('new', 200, ['o','s'], [0,20], prior=0, upper=1)]
    fit = dynamic.fit_integer_flows(dynamic.build_passage_system(trips,['s'],1),
                                    {'s':[1]}, {'od':1})
    assert fit.counts.tolist() == [1,0]
    assert widths == [2]


def test_fractional_prior_keeps_exact_absolute_deviation_cost():
    trips = [option('a', 100, ['o','s'], [0,20], prior=.25, upper=2),
             option('b', 200, ['o','s'], [0,20], prior=1.75, upper=2)]
    fit = dynamic.fit_integer_flows(dynamic.build_passage_system(trips,['s'],1),
                                    {'s':[2]}, {'od':2})
    costs = [abs(x-.25)+abs(2-x-1.75) for x in range(3)]
    assert fit.absolute_prior_change == min(costs)


def test_equivalent_columns_are_solved_as_one_bounded_flow(monkeypatch):
    solve=dynamic.milp
    widths=[]
    def record(**kwargs):
        widths.append(len(kwargs['c']))
        return solve(**kwargs)
    monkeypatch.setattr(dynamic,'milp',record)
    trips=[option(str(i),100+i,['o','s'],[0,20],upper=1) for i in range(5)]
    fit=dynamic.fit_integer_flows(dynamic.build_passage_system(trips,['s'],1),
                                  {'s':[3]},{'od':3})
    assert widths==[1]
    assert fit.counts.sum()==3 and np.max(fit.counts)==1
    assert fit.absolute_prior_change==2


def test_aggregated_solver_tolerates_tiny_negative_roundoff(monkeypatch):
    solve = dynamic.milp
    def rounded(**kwargs):
        result = solve(**kwargs)
        result.x[0] = -1e-12
        return result
    monkeypatch.setattr(dynamic, 'milp', rounded)
    trips = [option('unused', 100, ['o', 'x'], [0, 10], prior=0, upper=1),
             option('used', 100, ['o', 's'], [0, 10], upper=1)]
    fit = dynamic.fit_integer_flows(
        dynamic.build_passage_system(trips, ['s'], 1), {'s': [1]}, {'od': 1})
    assert fit.counts.tolist() == [0, 1]


def test_passage_solver_caps_threads_before_next_day_fork(monkeypatch):
    actual = dynamic.milp
    seen = []
    def checked(**kwargs):
        seen.append(kwargs['options'].get('threads'))
        return actual(**kwargs)
    monkeypatch.setattr(dynamic, 'milp', checked)
    system = dynamic.build_passage_system([option('trip', 100, ['o', 's'], [0, 10])], ['s'], 1)
    dynamic.fit_integer_flows(system, {'s': [1]}, {'od': 1})
    assert seen == [1]


def test_next_day_fork_can_solve_after_parent_passage_fit():
    """Run in a fresh interpreter: native scheduler state is process-global."""
    import subprocess
    import sys
    from pathlib import Path
    script = '''
import multiprocessing as mp
from scipy.optimize import linprog
from traffic_sim.experimental.dynamic_assignment import RouteDeparture, build_passage_system, fit_integer_flows
import warnings

def next_day():
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        result = linprog([1.0], A_eq=[[1.0]], b_eq=[1.0], method='highs', options={'threads': 1})
    assert result.success

trip = RouteDeparture('trip', 'od', 100, ('o','s'), {'fit': (0,10)}, 1, 1)
for day in range(2):
    fit_integer_flows(build_passage_system([trip], ['s'], 1), {'s': [1]}, {'od': 1})
    child = mp.get_context('fork').Process(target=next_day)
    child.start()
    child.join(5)
    if child.is_alive():
        child.terminate()
        child.join(2)
        raise AssertionError('next-day worker stuck after parent HiGHS solve')
    assert child.exitcode == 0
'''
    result = subprocess.run([sys.executable, '-c', script],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr

@pytest.mark.parametrize('status, expected_name', [
    (1, 'DynamicAssignmentTimeout'), (2, 'DynamicAssignmentInfeasible'),
])
def test_solver_limit_is_distinct_from_proven_infeasibility(monkeypatch, status, expected_name):
    from scipy.optimize import OptimizeResult
    monkeypatch.setattr(dynamic, 'milp', lambda **_kwargs: OptimizeResult(
        status=status, x=None, message='test termination'))
    system = dynamic.build_passage_system(
        [option('a', 0, ['o', 's'], [0, 1])], ['s'], 1)
    with pytest.raises(dynamic.DynamicAssignmentError) as caught:
        dynamic.fit_integer_flows(system, {'s': [1]}, {'od': 1})
    assert type(caught.value).__name__ == expected_name


def test_solver_uses_sparse_scenario_differences_without_losing_constraints(monkeypatch):
    scenarios = {'a': (0, 10), 'b': (0, 100), 'c': (0, 20)}
    options = [dynamic.RouteDeparture('one', 'od', 850, ('o', 's'), scenarios, 1, 1),
               dynamic.RouteDeparture('two', 'od', 750, ('o', 's'), scenarios, 0, 1)]
    system = dynamic.build_passage_system(options, ['s'], 2)
    original = dynamic.milp
    captured = []
    def capture(**kwargs):
        captured.append(kwargs['constraints'][0])
        return original(**kwargs)
    monkeypatch.setattr(dynamic, 'milp', capture)
    result = dynamic.fit_integer_flows(system, {'s': [1, 0]}, {'od': 1})
    assert result.counts.tolist() == [0, 1]
    assert set(system.project(result.counts)) == {'a', 'b', 'c'}
    # The baseline sensor equations stay absolute; every other scenario is a
    # zero-valued difference equation, including the fully redundant c arm.
    assert captured[0].lb[:6].tolist() == [1, 0, 0, 0, 0, 0]


def test_difference_equations_keep_original_verification(monkeypatch):
    from scipy.optimize import OptimizeResult
    options = [dynamic.RouteDeparture('one', 'od', 850, ('o', 's'),
               {'a': (0, 10), 'b': (0, 100)}, 1, 1)]
    system = dynamic.build_passage_system(options, ['s'], 2)
    monkeypatch.setattr(dynamic, 'milp', lambda **_kwargs: OptimizeResult(
        status=0, x=np.array([1.]), message='only baseline fits'))
    with pytest.raises(dynamic.DynamicAssignmentError, match='passage constraints'):
        dynamic.fit_integer_flows(system, {'s': [1, 0]}, {'od': 1})


def test_shared_observation_support_matches_direct_counting():
    from dataclasses import replace
    import math
    base = option('base', 0, ['s', 'x', 's', 't', 's'], [0, 10, 90, 900, 1800])
    base.entry_offsets_s['other'] = (0, 20, 180, 950, 1900)
    alternatives = [replace(base, option_id=str(i), departure_s=t)
                    for i, t in enumerate([-1900, -90, 0, 810, 900, 1799])]
    # Same times, different physical route must not share a projected path.
    alternatives.append(replace(base, option_id='different', edges=('o', 'x', 't', 's', 't')))
    system = dynamic.build_passage_system(alternatives, ['s', 't'], 2)
    expected = np.zeros(system.matrix.shape)
    boundary = np.zeros(system.boundary_matrix.shape)
    for c, trip in enumerate(alternatives):
        for a, scenario in enumerate(system.scenarios):
            for edge, offset in zip(trip.edges[1:], trip.entry_offsets_s[scenario][1:]):
                if edge not in system.sensors:
                    continue
                row = a * len(system.sensors) + system.sensors.index(edge)
                q = math.floor((trip.departure_s + offset) / 900)
                if 0 <= q < 2:
                    expected[row * 2 + q, c] += 1
                else:
                    boundary[row * 2 + int(q >= 2), c] += 1
    np.testing.assert_array_equal(system.matrix.toarray(), expected)
    np.testing.assert_array_equal(system.boundary_matrix.toarray(), boundary)


def test_shared_observations_do_not_skip_per_alternative_validation():
    from dataclasses import replace
    base = option('base', 0, ['o', 's'], [0, 100])
    for invalid in [replace(base, option_id='bad', prior=-1),
                    replace(base, option_id='bad', capacity=-1),
                    replace(base, option_id='bad', departure_s=float('nan'))]:
        with pytest.raises(ValueError):
            dynamic.build_passage_system([base, invalid], ['s'], 1)
    dynamic.build_passage_system([base], ['s'], 1)
    base.entry_offsets_s['fit'] = (0, -1)
    with pytest.raises(ValueError, match='monotone'):
        dynamic.build_passage_system([base], ['s'], 1)


class TestVerifiedExpansion:
    """Step 1: a built system already validated its options.

    ``expand_departure_support`` re-validates them by building a throwaway
    one-sensor, one-interval system. That check cannot fail for options a real
    system already accepted, so the verified entry point skips it — and must
    produce exactly the same alternatives.
    """

    def _options(self):
        return [option('a', 850, ['o', 's', 'd'], [0, 100, 120]),
                option('b', 1900, ['o', 's', 'd'], [0, 90, 130], prior=0)]

    def _fields(self, alternatives):
        return [(o.option_id, o.group, o.departure_s, o.edges, o.prior,
                 o.capacity, o.preference_cost,
                 {name: values for name, values in sorted(o.entry_offsets_s.items())})
                for o in alternatives]

    def test_the_verified_expansion_equals_the_public_expansion(self):
        options = self._options()
        system = dynamic.build_passage_system(options, ['s'], 4)

        public = dynamic.expand_departure_support(
            options, [-900, 0, 900], begin_s=0, end_s=3600, guard_s=60)
        verified = dynamic._expand_departure_support_verified(
            system, [-900, 0, 900], begin_s=0, end_s=3600, guard_s=60)

        assert self._fields(verified) == self._fields(public)

    def test_both_expansions_build_the_same_matrices(self):
        options = self._options()
        system = dynamic.build_passage_system(options, ['s'], 4)
        public = dynamic.build_passage_system(dynamic.expand_departure_support(
            options, [-900, 0, 900], begin_s=0, end_s=3600, guard_s=60), ['s'], 4)
        verified = dynamic.build_passage_system(
            dynamic._expand_departure_support_verified(
                system, [-900, 0, 900], begin_s=0, end_s=3600, guard_s=60), ['s'], 4)

        for name in ('matrix', 'boundary_matrix'):
            a, b = getattr(public, name), getattr(verified, name)
            assert a.shape == b.shape
            assert a.indptr.tolist() == b.indptr.tolist()
            assert a.indices.tolist() == b.indices.tolist()
            assert a.data.tolist() == b.data.tolist()

    def test_the_verified_expansion_refuses_anything_but_a_built_system(self):
        options = self._options()

        with pytest.raises(ValueError, match='built passage system'):
            dynamic._expand_departure_support_verified(
                options, [0], begin_s=0, end_s=3600)

    def test_the_verified_expansion_still_checks_shifts_and_horizon(self):
        system = dynamic.build_passage_system(self._options(), ['s'], 4)

        with pytest.raises(ValueError, match='shifts including zero'):
            dynamic._expand_departure_support_verified(
                system, [300], begin_s=0, end_s=3600)
        with pytest.raises(ValueError, match='finite horizon'):
            dynamic._expand_departure_support_verified(
                system, [0], begin_s=3600, end_s=0)

    def test_the_verified_expansion_still_checks_the_per_option_envelope(self):
        options = [option('a', 850, ['o', 's', 'd'], [0, 100, 120], prior=2)]
        system = dynamic.build_passage_system(options, ['s'], 4)

        with pytest.raises(ValueError, match='unit capacity'):
            dynamic._expand_departure_support_verified(
                system, [0], begin_s=0, end_s=3600)

    def test_the_public_expansion_still_validates_unverified_options(self):
        backwards = dynamic.RouteDeparture(
            'x', 'od', 850, ('o', 's'), {'fit': (100, 10)}, 1, 1)

        with pytest.raises(ValueError, match='monotone entry offsets'):
            dynamic.expand_departure_support(
                [backwards], [0], begin_s=0, end_s=3600)

    def test_the_public_expansion_still_rejects_empty_input(self):
        with pytest.raises(ValueError, match='base alternatives are required'):
            dynamic.expand_departure_support([], [0], begin_s=0, end_s=3600)


def test_the_verified_support_path_is_not_public():
    """Only trusted internal callers may skip the option validation."""
    assert not hasattr(dynamic, 'expand_departure_support_verified')
    assert hasattr(dynamic, '_expand_departure_support_verified')
    assert 'expand_departure_support_verified' not in dir(dynamic)


class TestSolverPhaseInstrumentation:
    """Step 3 is MEASUREMENT only: the solve must be identical either way."""

    def _system(self, n=6):
        options = [option(f'v{i}', 100 + 900 * (i % 3), ['o', 's', 'd'],
                          [0, 10, 20], prior=1, upper=1) for i in range(n)]
        system = dynamic.build_passage_system(options, ['s'], 3)
        targets = {'s': [sum(1 for o in options
                             if int((o.departure_s + 10) // 900) == q)
                         for q in range(3)]}
        return system, targets, {'od': n}

    class _Observer:
        """The tool-side collector, reduced to what the hook must support."""

        def __init__(self):
            self.rows = {}
            self._children = []

        def phase(self, name):
            import contextlib
            import time

            @contextlib.contextmanager
            def _measure():
                row = self.rows.setdefault(name, {'calls': 0, 'exclusive_s': 0.0,
                                                  'cumulative_s': 0.0})
                row['calls'] += 1
                self._children.append(0.0)
                began = time.perf_counter()
                try:
                    yield
                finally:
                    elapsed = time.perf_counter() - began
                    children = self._children.pop()
                    row['cumulative_s'] += elapsed
                    row['exclusive_s'] += elapsed - children
                    if self._children:
                        self._children[-1] += elapsed
            return _measure()

    def test_the_hook_is_absent_unless_a_tool_installs_it(self):
        assert dynamic._SOLVER_PHASE_OBSERVER is None

    def test_instrumentation_changes_neither_request_nor_result(self, tmp_path):
        import json
        system, targets, groups = self._system()
        plain = dynamic.fit_integer_flows(
            system, targets, groups, checkpoint_dir=tmp_path / 'a',
            cache_dir=tmp_path / 'cache-a')
        with dynamic._observe_solver_phases(self._Observer()):
            measured = dynamic.fit_integer_flows(
                system, targets, groups, checkpoint_dir=tmp_path / 'b',
                cache_dir=tmp_path / 'cache-b')

        assert measured.counts.tolist() == plain.counts.tolist()
        assert (tmp_path / 'a/request.npz').read_bytes() \
            == (tmp_path / 'b/request.npz').read_bytes()
        first = json.loads((tmp_path / 'a/state.json').read_text())
        second = json.loads((tmp_path / 'b/state.json').read_text())
        assert first['key'] == second['key']
        assert set(first) == set(second)

    def test_a_miss_runs_milp_and_a_hit_does_not(self, tmp_path):
        system, targets, groups = self._system()
        cache = tmp_path / 'cache'
        miss, hit = self._Observer(), self._Observer()

        with dynamic._observe_solver_phases(miss):
            dynamic.fit_integer_flows(system, targets, groups,
                                      checkpoint_dir=tmp_path / 'one', cache_dir=cache)
        with dynamic._observe_solver_phases(hit):
            dynamic.fit_integer_flows(system, targets, groups,
                                      checkpoint_dir=tmp_path / 'two', cache_dir=cache)

        assert miss.rows['milp_solve']['calls'] == 1
        assert 'milp_solve' not in hit.rows
        assert hit.rows['checkpoint_cache_lookup']['calls'] == 1

    def test_every_declared_phase_is_measured_at_least_once(self, tmp_path):
        system, targets, groups = self._system()
        observer = self._Observer()

        with dynamic._observe_solver_phases(observer):
            dynamic.fit_integer_flows(
                system, targets, groups, checkpoint_dir=tmp_path / 'c',
                cache_dir=tmp_path / 'cache-c',
                departure_bounds=[{} for _ in range(3)])

        for name in dynamic.SOLVER_PHASE_NAMES:
            if name == 'checkpoint_cache_write':
                continue
            assert name in observer.rows, name

    def test_exclusive_time_is_never_double_counted(self, tmp_path):
        import time
        system, targets, groups = self._system()
        observer = self._Observer()

        began = time.perf_counter()
        with dynamic._observe_solver_phases(observer):
            dynamic.fit_integer_flows(system, targets, groups,
                                      checkpoint_dir=tmp_path / 'd',
                                      cache_dir=tmp_path / 'cache-d')
        elapsed = time.perf_counter() - began

        total = sum(row['exclusive_s'] for row in observer.rows.values())
        assert 0 < total <= elapsed + 1e-6

    def test_an_infeasible_fit_restores_the_hook(self, tmp_path):
        system, _targets, groups = self._system()
        impossible = {'s': [99, 0, 0]}

        with pytest.raises(dynamic.DynamicAssignmentInfeasible):
            with dynamic._observe_solver_phases(self._Observer()):
                dynamic.fit_integer_flows(system, impossible, groups,
                                          checkpoint_dir=tmp_path / 'e',
                                          cache_dir=tmp_path / 'cache-e')

        assert dynamic._SOLVER_PHASE_OBSERVER is None

    def test_an_arbitrary_exception_restores_the_hook(self):
        class _Boom(Exception):
            pass

        with pytest.raises(_Boom):
            with dynamic._observe_solver_phases(self._Observer()):
                raise _Boom()

        assert dynamic._SOLVER_PHASE_OBSERVER is None

    def test_a_validation_failure_restores_the_hook(self):
        system, _targets, groups = self._system()

        with pytest.raises(ValueError):
            with dynamic._observe_solver_phases(self._Observer()):
                dynamic.fit_integer_flows(system, {'other': [0, 0, 0]}, groups)

        assert dynamic._SOLVER_PHASE_OBSERVER is None
