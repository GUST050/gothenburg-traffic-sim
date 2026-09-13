"""Automatic calibration uses fresh measurements and never promotes failed fits."""
import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from traffic_sim.demand import automatic_passage as auto
from demand.day_library import assemble_window, merge_day_reports


def fixture(tmp_path, monkeypatch, variants=('',)):
    network = tmp_path / 'net.net.xml'
    network.write_text('<net/>')
    inputs, reports, calls = {}, {}, []
    for suffix in variants:
        route = tmp_path / f'calibrated{suffix}.rou.xml'
        route.write_text('<routes>\n<vehicle id="v" depart="850"><route edges="o s d" /></vehicle>\n</routes>\n')
        route.with_name(f'calibrated{suffix}.agents.json').write_text(json.dumps({'agents': [{
            'vehicle_id': 'v', 'departure_s': 850, 'origin_edge': 'o',
            'destination_edge': 'd', 'purpose': 'work', 'candidate_id': 'candidate',
            'purpose_route_compatible': True}]}))
        inputs[suffix] = {'out_path': route, 'targets': [{'s': 1}, {'s': 0}, {'s': 0}, {'s': 0}],
                          'hard_bounds_pq': [{}, {}, {}, {}]}
        reports[suffix] = {'vehicles': 1, 'relaxation_summary': {'clean': 4},
                           'relaxed_bound_violations': [], 'infeasible_intervals': 0,
                           'geh_pct': 100, 'integer_sensor_constraints': 4,
                           'integer_sensor_exact': 4, 'integer_sensor_max_abs_error': 0}

    def simulate(route, net, directory, seed, targets, quarters, population):
        calls.append((Path(route), seed))
        directory.mkdir(parents=True)
        root = ET.parse(route).getroot()
        counts = {'s': [0]*quarters}
        for v in root.findall('vehicle'):
            departure = float(v.get('depart'))
            v.find('route').set('exitTimes', f'{departure+100} {departure+110} {departure+120}')
            counts['s'][int((departure+100)//900)] += 1
        ET.ElementTree(root).write(directory / 'vehroute.xml')
        edge = ET.Element('meandata')
        for q in range(quarters):
            interval = ET.SubElement(edge, 'interval', begin=str(q*900), end=str((q+1)*900))
            ET.SubElement(interval, 'edge', id='s', entered=str(counts['s'][q]))
        ET.ElementTree(edge).write(directory / 'edge.xml')
        return {'seed': seed, 'absolute_error': sum(abs(a-b) for a,b in zip(counts['s'],targets['s']))}

    monkeypatch.setattr(auto.trial, 'simulate_counts', simulate)
    monkeypatch.setattr(auto, 'calibrated_structure_report', lambda *a, **kw: {'structure_flags': []})
    return inputs, reports, network, calls


def test_automatic_fit_collects_fresh_evidence_and_preserves_day_assembly(tmp_path, monkeypatch):
    inputs, reports, network, calls = fixture(tmp_path, monkeypatch)
    result = auto.refine_variants(inputs, reports, network, tmp_path / 'evidence')
    record = result['']['passage_calibration']
    assert record['status'] == 'validated'
    assert record['validation']['candidate_absolute_error'] == 0
    assert record['validation']['accuracy']['exact_any_seed'] == 4
    assert record['validation']['source_absolute_error'] == 6
    assert {seed for _,seed in calls} == {1000,1001,1002,4000,4001,4002}
    assert len(calls) == 9
    assert ET.parse(inputs['']['out_path']).getroot().find('vehicle').get('depart') != '850'
    output = tmp_path / 'assembled.rou.xml'
    assemble_window([tmp_path, tmp_path], output, tmp_path / 'assembled.agents.json')
    assert len(ET.parse(output).getroot().findall('vehicle')) == 2
    merged = merge_day_reports([result[''],result['']], quarters_per_day=4)
    assert len(merged['passage_calibration']['days']) == 2
    assert merged['passage_calibration']['policy'] == auto.POLICY
    incompatible = copy.deepcopy(result[''])
    incompatible['passage_calibration']['policy'] = 'automatic_dynamic_passage_old'
    with pytest.raises(ValueError, match='policy'):
        merge_day_reports([result[''], incompatible], quarters_per_day=4)


def test_unhealthy_or_failed_measurement_leaves_original_routes(tmp_path, monkeypatch):
    inputs, reports, network, _ = fixture(tmp_path, monkeypatch)
    original = inputs['']['out_path'].read_bytes()
    def fail(*a, **kw):
        raise RuntimeError('SUMO unhealthy')
    monkeypatch.setattr(auto.trial, 'simulate_counts', fail)
    with pytest.raises(RuntimeError, match='unhealthy'):
        auto.refine_variants(inputs, reports, network, tmp_path / 'evidence')
    assert inputs['']['out_path'].read_bytes() == original
    assert 'passage_calibration' not in reports['']


def test_failed_later_variant_does_not_install_earlier_variant(tmp_path, monkeypatch):
    inputs, reports, network, _ = fixture(tmp_path, monkeypatch, ('','_v1'))
    original = inputs['']['out_path'].read_bytes()
    reports['_v1']['relaxation_summary'] = {'unknown_policy': 4}
    with pytest.raises(ValueError, match='relaxation'):
        auto.refine_variants(inputs, reports, network, tmp_path / 'evidence')
    assert inputs['']['out_path'].read_bytes() == original


def test_source_identity_covers_automatic_dependencies():
    from traffic_sim.demand.source_identity import demand_source_paths
    paths = {str(p) for p in demand_source_paths(Path('.')).values()}
    for name in ['traffic_sim/demand/automatic_passage.py',
                 'traffic_sim/experimental/dynamic_assignment.py',
                 'tools/trial_dynamic_passage.py', 'tools/departure_reconciliation.py']:
        assert name in paths


def test_fit_summary_keeps_passage_evidence_and_measurement_basis():
    from build_sumo_demand import fit_summary
    report = {'passage_calibration': {'policy': auto.POLICY, 'status': 'validated'},
              'measurement_basis': 'predicted_sensor_passage_quarter'}
    summary = fit_summary(report)
    assert summary['passage_calibration'] == report['passage_calibration']
    assert summary['measurement_basis'] == report['measurement_basis']


def test_mixed_legacy_and_passage_days_cannot_be_merged():
    with pytest.raises(ValueError, match='passage'):
        merge_day_reports([{'passage_calibration': {'status': 'validated'}}, {}])


def test_runtime_identity_changes_with_sumo_binary(tmp_path):
    (tmp_path / 'bin').mkdir()
    binary = tmp_path / 'bin/sumo'
    binary.write_bytes(b'version-one')
    before = auto.runtime_identity(tmp_path)
    binary.write_bytes(b'version-two')
    assert auto.runtime_identity(tmp_path) != before
    binary.unlink()
    with pytest.raises(ValueError, match='SUMO'):
        auto.runtime_identity(tmp_path)


def test_regression_in_fresh_sumo_rejects_candidate(tmp_path, monkeypatch):
    inputs, reports, network, _ = fixture(tmp_path, monkeypatch)
    original = inputs['']['out_path'].read_bytes()
    measure = auto._measure
    def regressed(route, net, directory, *args):
        result = measure(route, net, directory, *args)
        if directory.name.startswith('after-'):
            return {'s': [0, 0, 0, 3]}
        return result
    monkeypatch.setattr(auto, '_measure', regressed)
    with pytest.raises(ValueError, match='regressed'):
        auto.refine_variants(inputs, reports, network, tmp_path / 'evidence')
    assert inputs['']['out_path'].read_bytes() == original


def test_direction_arms_with_equal_inputs_preserve_each_population(tmp_path, monkeypatch):
    inputs, reports, network, _ = fixture(tmp_path, monkeypatch, ('','_v1','_v2'))
    result = auto.refine_variants(inputs, reports, network, tmp_path / 'evidence')
    assert all(r['passage_calibration']['departure_population'] == [1,0,0,0]
               for r in result.values())


def test_failed_build_restores_routes_metadata_and_new_variant(tmp_path):
    old = tmp_path / 'calibrated.rou.xml'
    old.write_text('previous')
    meta = tmp_path / 'demand_meta.json'
    meta.write_text('previous identity')
    with pytest.raises(RuntimeError):
        with auto.preserve_demand_on_failure(tmp_path):
            old.write_text('unvalidated PFE output')
            meta.write_text('incomplete identity')
            (tmp_path / 'calibrated_v1.rou.xml').write_text('new failed arm')
            raise RuntimeError('passage fit rejected')
    assert old.read_text() == 'previous'
    assert meta.read_text() == 'previous identity'
    assert not (tmp_path / 'calibrated_v1.rou.xml').exists()


def test_bounds_use_explicit_pfe_quarter_contract_not_violation_inference():
    report = {'structural_bounds_retained_per_quarter': [True, False],
              'relaxation_summary': {'clean': 1, 'no_bounds_tol1': 1},
              'relaxed_bound_violations': []}
    assert auto._bounds(report, [{'a': (1,2)}, {'b': (1,2)}]) == [{'a': (1,2)}, {}]
    report['structural_bounds_retained_per_quarter'] = [True]
    with pytest.raises(ValueError, match='quarter'):
        auto._bounds(report, [{}, {}])


def test_measurement_batch_is_bounded_and_returns_input_order(monkeypatch):
    import threading
    import time
    active=0; peak=0;lock=threading.Lock()
    def measure(index):
        nonlocal active,peak
        with lock:
            active+=1;peak=max(peak,active)
        time.sleep(.02)
        with lock:active-=1
        return index
    monkeypatch.setattr(auto,'_measure',measure)
    assert auto._measure_batch([(i,) for i in range(9)])==list(range(9))
    assert 1<peak<=6


def test_short_trip_guard_encodes_the_same_clean_envelope_as_validation(monkeypatch):
    monkeypatch.setattr(auto, 'route_od_distance_km',
                        lambda edges: 0.5 if edges[-1] == 'short' else 5.0)
    base = [
        auto.dynamic.RouteDeparture(
            f's{i}', 'od', 100 + i, ('o', 'short'), {'fit': (0, 10)}, 1, 1)
        for i in range(10)
    ] + [
        auto.dynamic.RouteDeparture(
            f'l{i}', 'od', 100 + i, ('o', 'long'), {'fit': (0, 10)}, 1, 1)
        for i in range(40)
    ]
    expanded = auto.dynamic.expand_departure_support(
        base, [0, 900], begin_s=0, end_s=1800)
    report = {'trip_length_fit': {'under_1km_cap_audit': {
        'pool_share': 0.1, 'violating_quarters': 0,
    }}}

    groups = auto._short_trip_departure_guards(base, expanded, report, 2)

    by_name = {group.name: group for group in groups}
    assert by_name['trips_under_1km_cap'].bounds == ((0, 10), (0, 2))
    assert by_name['trips_under_1km_denominator'].bounds == ((48, 50), (0, 50))
    assert len(by_name['trips_under_1km_cap'].option_ids) == 20


def test_new_short_trip_warning_triggers_a_targeted_structural_refit(
        tmp_path, monkeypatch):
    inputs, reports, network, _ = fixture(tmp_path, monkeypatch)
    clean = {'structure_flags': [], 'trip_length_fit': {
        'under_1km_cap_audit': {'pool_share': 0.1,
                                'violating_quarters': 0,
                                'violations': []}}}
    warning = {'structure_flags': ['trips_under_1km_cap: synthetic'],
               'trip_length_fit': {'under_1km_cap_audit': {
                   'pool_share': 0.1, 'violating_quarters': 1,
                   'violations': [{'quarter': 0, 'actual': 3, 'limit': 2}]}}}
    def structure(path, **_kwargs):
        parent = Path(path).parent.name
        if parent == 'candidate':
            return warning
        return clean
    monkeypatch.setattr(auto, 'calibrated_structure_report', structure)
    monkeypatch.setattr(auto, 'route_od_distance_km', lambda _edges: .5)
    solve = auto.dynamic.fit_integer_flows
    seen = []
    def record(*args, **kwargs):
        seen.append(kwargs.get('departure_group_bounds'))
        return solve(*args, **kwargs)
    monkeypatch.setattr(auto.dynamic, 'fit_integer_flows', record)

    result = auto.refine_variants(inputs, reports, network,
                                  tmp_path / 'evidence')

    assert seen[0] is None
    assert {group.name for group in seen[1]} == {
        'trips_under_1km_cap', 'trips_under_1km_denominator'}
    assert result['']['passage_calibration']['structural_repair']['passes'] == 1
    assert result['']['passage_calibration']['structural_repair']['quarters'] == [0]


def test_direction_arms_conserve_their_own_unequal_populations(tmp_path, monkeypatch):
    import copy
    inputs, reports, network, calls = fixture(tmp_path, monkeypatch, ('', '_v1', '_v2'))
    for suffix, count in [('', 3), ('_v1', 2), ('_v2', 4)]:
        route = inputs[suffix]['out_path']
        root = ET.parse(route).getroot()
        vehicle = root.find('vehicle')
        agent_path = route.with_name(route.name.replace('.rou.xml', '.agents.json'))
        agent = json.loads(agent_path.read_text())['agents'][0]
        root.remove(vehicle)
        agents = []
        for index in range(count):
            item = copy.deepcopy(vehicle)
            item.set('id', f'v{index}')
            item.set('depart', str(850 + index))
            root.append(item)
            agents.append(dict(agent, vehicle_id=f'v{index}', departure_s=850 + index))
        ET.ElementTree(root).write(route)
        agent_path.write_text(json.dumps({'agents': agents}))
        inputs[suffix]['targets'][0]['s'] = count
        reports[suffix]['vehicles'] = count
    result = auto.refine_variants(inputs, reports, network, tmp_path / 'evidence')
    for suffix, count in [('', 3), ('_v1', 2), ('_v2', 4)]:
        record = result[suffix]['passage_calibration']
        assert sum(record['departure_population']) == count
        assert record['validation']['candidate_absolute_error'] == 0
        assert len(ET.parse(inputs[suffix]['out_path']).getroot().findall('vehicle')) == count
    assert len(calls) == 27


def test_retry_reuses_exact_solver_result_and_still_validates_sumo(tmp_path, monkeypatch):
    inputs, reports, network, calls = fixture(tmp_path, monkeypatch)
    originals = {path: path.read_bytes() for path in tmp_path.glob('calibrated*')}
    auto.refine_variants(inputs, reports, network, tmp_path/'attempt1')
    for path, content in originals.items():
        path.write_bytes(content)
    def unexpected_solve(**kwargs):
        raise AssertionError('identical completed numerical problem was solved again')
    monkeypatch.setattr(auto.dynamic, 'milp', unexpected_solve)
    auto.refine_variants(inputs, reports, network, tmp_path/'attempt2')
    assert len(calls) == 18  # Independent fresh learning and validation on both attempts.
    assert (tmp_path/'attempt2/q50/solver-candidate/request.npz').is_file()


def test_first_proven_infeasibility_tries_route_boundary_support(tmp_path, monkeypatch):
    inputs, reports, network, _ = fixture(tmp_path, monkeypatch)
    solve = auto.dynamic.fit_integer_flows
    attempts = []
    def fail_grid(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise auto.dynamic.DynamicAssignmentInfeasible('infeasible fixed grid')
        return solve(*args, **kwargs)
    monkeypatch.setattr(auto.dynamic, 'fit_integer_flows', fail_grid)
    result = auto.refine_variants(inputs, reports, network, tmp_path/'evidence')
    assert len(attempts) == 2
    assert result['']['passage_calibration']['structural_repair']['boundary_fallback']


def test_first_timeout_keeps_unknown_feasibility_and_does_not_expand(tmp_path, monkeypatch):
    inputs, reports, network, _ = fixture(tmp_path, monkeypatch)
    original = inputs['']['out_path'].read_bytes()
    def timeout(*args, **kwargs):
        raise auto.dynamic.DynamicAssignmentTimeout('time budget exhausted')
    def unexpected_expansion(*args, **kwargs):
        raise AssertionError('timeout is not proof that support is infeasible')
    monkeypatch.setattr(auto.dynamic, 'fit_integer_flows', timeout)
    monkeypatch.setattr(auto.dynamic, 'expand_passage_boundary_support', unexpected_expansion)
    with pytest.raises(auto.dynamic.DynamicAssignmentTimeout):
        auto.refine_variants(inputs, reports, network, tmp_path/'evidence')
    assert inputs['']['out_path'].read_bytes() == original


def test_passage_timings_separate_calibration_from_retention(tmp_path, monkeypatch):
    inputs, reports, network, _ = fixture(tmp_path, monkeypatch)
    auto.refine_variants(inputs, reports, network, tmp_path/'evidence')
    timing = json.loads((tmp_path/'evidence/timings.json').read_text())
    assert timing['variant_s']['q50'] >= 0
    assert timing['evidence_retention_s'] >= 0
    assert timing['total_s'] >= sum(timing['variant_s'].values()) + timing['evidence_retention_s']


@pytest.mark.parametrize('cpus,expected', [(12, 6), (2, 2), (None, 1)])
def test_measurements_use_bounded_available_parallelism(tmp_path, monkeypatch, cpus, expected):
    monkeypatch.setattr(auto.os, 'cpu_count', lambda: cpus)
    real_pool = auto.ThreadPoolExecutor
    workers = []
    def pool(*args, **kwargs):
        workers.append(kwargs['max_workers'])
        return real_pool(*args, **kwargs)
    monkeypatch.setattr(auto, 'ThreadPoolExecutor', pool)
    monkeypatch.setattr(auto, '_measure', lambda value: value)
    assert auto._measure_batch([(i,) for i in range(6)]) == list(range(6))
    assert workers == [expected]


def _system_build_spy(monkeypatch):
    """Record every passage-system build the production path performs."""
    from traffic_sim.experimental import dynamic_assignment as dynamic
    real = dynamic.build_passage_system
    calls = []

    def spy(options, sensors, n_intervals, **kwargs):
        calls.append({'options': len(options), 'sensors': tuple(sorted(sensors)),
                      'n_intervals': n_intervals})
        return real(options, sensors, n_intervals, **kwargs)

    monkeypatch.setattr(auto.dynamic, 'build_passage_system', spy)
    monkeypatch.setattr(auto.trial.dynamic, 'build_passage_system', spy)
    monkeypatch.setattr(dynamic, 'build_passage_system', spy)
    return calls


def test_refine_builds_the_base_system_once_and_skips_the_dummy_validation(
        tmp_path, monkeypatch):
    """Step 1: load_source built and verified the base system already.

    ``_refine`` rebuilt exactly the same system, and
    ``expand_departure_support`` built a third throwaway system only to repeat
    the per-option validation. Both are removed; the expanded candidate matrix
    is a different system and must still be built.
    """
    inputs, reports, network, _calls = fixture(tmp_path, monkeypatch)
    builds = _system_build_spy(monkeypatch)

    auto.refine_variants(inputs, reports, network, tmp_path / 'evidence')

    assert [b for b in builds if b['n_intervals'] == 1] == []
    base = [b for b in builds if b['options'] == 1]
    assert len(base) == 1, base
    assert base[0]['sensors'] == ('s',) and base[0]['n_intervals'] == 4
    assert [b for b in builds if b['options'] > 1], 'expanded system must still be built'


def test_refine_reuses_the_verified_system_for_the_bound_check(tmp_path, monkeypatch):
    """The bounds must be checked against the system that verified the traces."""
    inputs, reports, network, _calls = fixture(tmp_path, monkeypatch)
    seen = []
    real = auto.dynamic.departure_bound_constraints

    def spy(system, bounds):
        seen.append(system)
        return real(system, bounds)

    monkeypatch.setattr(auto.dynamic, 'departure_bound_constraints', spy)
    loaded = []
    real_load = auto.trial.load_verified_source
    monkeypatch.setattr(auto.trial, 'load_verified_source',
                        lambda source: loaded.append(real_load(source)) or loaded[-1])

    auto.refine_variants(inputs, reports, network, tmp_path / 'evidence')

    # The solver checks its own expanded system too; the FIRST check is the
    # source bound check, and it must use the verified base system.
    assert len(loaded) == 1
    assert seen[0] is loaded[0].system
    assert all(later is not loaded[0].system for later in seen[1:])


def test_the_replayed_base_and_boundary_matrices_are_bit_identical(tmp_path):
    """CSR gate: reuse must not change shape, indptr, indices or data."""
    from traffic_sim.experimental import dynamic_assignment as dynamic
    from tests.test_trial_dynamic_passage import fixture as trial_fixture

    source = trial_fixture(tmp_path)
    verified = auto.trial.load_verified_source(source)
    rebuilt = dynamic.build_passage_system(
        list(verified.options), sorted(verified.system.sensors),
        verified.metadata['n_intervals'])

    for name in ('matrix', 'boundary_matrix'):
        a, b = getattr(verified.system, name), getattr(rebuilt, name)
        assert a.shape == b.shape
        assert a.indptr.tolist() == b.indptr.tolist()
        assert a.indices.tolist() == b.indices.tolist()
        assert a.data.tolist() == b.data.tolist()


def test_the_solver_request_key_is_unchanged_by_reuse(tmp_path):
    """The solver must be handed byte-identical inputs either way."""
    import json
    from traffic_sim.experimental import dynamic_assignment as dynamic
    from tests.test_trial_dynamic_passage import fixture as trial_fixture

    source = trial_fixture(tmp_path)
    verified = auto.trial.load_verified_source(source)
    options, groups, metadata = auto.trial.load_source(source)
    quarters = metadata['n_intervals']
    targets = {'s': [int(round(v)) for v in
                     metadata['sensor_targets']['variants']['edge_shares']['s']]}
    bounds = [{} for _ in range(quarters)]
    shifts = [-900, 0, 900]
    # No guard band: this fixture's uncertainty scenarios would straddle a
    # quarter boundary and make the fit infeasible. The request key, not
    # feasibility, is what this gate measures.
    guard_s = 0

    keys = []
    for index, support in enumerate((
            dynamic.expand_departure_support(
                options, shifts, begin_s=0, end_s=quarters * 900, guard_s=guard_s),
            dynamic.expand_departure_support_verified(
                verified.system, shifts, begin_s=0, end_s=quarters * 900,
                guard_s=guard_s))):
        work = tmp_path / f'solve-{index}'
        dynamic.fit_integer_flows(
            dynamic.build_passage_system(support, ['s'], quarters),
            targets, groups, departure_bounds=bounds,
            checkpoint_dir=work / 'solver', cache_dir=work / 'cache')
        keys.append(json.loads((work / 'solver/state.json').read_text())['key'])

    assert keys[0] == keys[1]
