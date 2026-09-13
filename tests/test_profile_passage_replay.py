"""Contract tests for the step-0 passage replay profiler.

The tool exists to MEASURE, so its own contracts are about honesty rather than
speed: it must never write into the evidence it reads, never call a compressed,
pruned or incomplete trace complete, never report a timing it did not take, and
never let a timing reach the semantic build identity.
"""
import gzip
import json
import math
from pathlib import Path
import shutil

import pytest

from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.experimental import dynamic_assignment as dynamic
from tools import profile_passage_replay as profiler
from tools import trial_dynamic_passage as trial

QUARTERS = 4
SENSOR = 's'
DEPARTURES = (100, 300, 1000, 1900, 2000, 2800)


def _entry_quarter(departure_s: float) -> int:
    """Where this vehicle enters the sensor edge, mirroring the system build."""
    return math.floor((departure_s + 60) / 900)


def evidence_root(tmp_path: Path, *, departures=DEPARTURES,
                  with_contract=True, with_result=True) -> Path:
    """One complete passage evidence root whose traces reconstruct exactly.

    The edgeData is DERIVED from the same route times the traces carry, so
    ``load_source``'s projection check passes for the reason it does in
    production: the counts really are what those routes produce.
    """
    source = tmp_path / 'passage' / 'q50'
    inputs = source / 'input'
    inputs.mkdir(parents=True)
    vehicles = [f'v{index}' for index in range(len(departures))]
    (inputs / 'calibrated.rou.xml').write_text('<routes>\n' + ''.join(
        f'<vehicle id="{name}" depart="{depart}"><route edges="o {SENSOR} d"/></vehicle>\n'
        for name, depart in zip(vehicles, departures)) + '</routes>\n')
    (inputs / 'calibrated.agents.json').write_text(json.dumps({'agents': [
        {'vehicle_id': name, 'candidate_id': f'c{index}', 'origin_edge': 'o',
         'destination_edge': 'd', 'purpose': 'work', 'departure_s': depart,
         'purpose_route_compatible': True}
        for index, (name, depart) in enumerate(zip(vehicles, departures))]}))
    counts = [0] * QUARTERS
    for depart in departures:
        counts[_entry_quarter(depart)] += 1
    (inputs / 'demand_meta.json').write_text(json.dumps({
        'n_intervals': QUARTERS, 'date': '2027-06-16', 'source': 'forecast',
        'sensor_targets': {'variants': {'edge_shares': {SENSOR: counts}}}}))
    (inputs / 'net.net.xml').write_text('<net/>')
    if with_contract:
        (inputs / profiler.automatic_passage.REPLAY_CONTRACT_NAME).write_text(json.dumps({
            'schema_version': 1,
            'policy': profiler.automatic_passage.POLICY,
            'retained_bounds_pq': [{} for _ in range(QUARTERS)],
            'source_sha256': profiler.automatic_passage.replay_source_sha256(),
        }, sort_keys=True))
    manifest = {'input_sha256': {path.name: sha256_file(path)
                                 for path in inputs.iterdir()},
                'evidence_sha256': {}}
    for arm in trial.LEARNING_ARMS:
        directory = source / 'evidence' / f'learning-0-arm-{arm}'
        directory.mkdir(parents=True)
        (directory / 'vehroute.xml').write_text('<routes>' + ''.join(
            f'<vehicle id="{name}" depart="{depart + 1}">'
            f'<route edges="o {SENSOR} d" exitTimes="{depart + 60} {depart + 120} '
            f'{depart + 180}"/></vehicle>'
            for name, depart in zip(vehicles, departures)) + '</routes>')
        (directory / 'edge.xml').write_text('<meandata>' + ''.join(
            f'<interval begin="{quarter * 900}" end="{quarter * 900 + 900}">'
            f'<edge id="{SENSOR}" entered="{counts[quarter]}"/></interval>'
            for quarter in range(QUARTERS)) + '</meandata>')
        for name in ('vehroute.xml', 'edge.xml'):
            path = directory / name
            manifest['evidence_sha256'][str(path.relative_to(source))] = sha256_file(path)
    (source / 'report.json').write_text(json.dumps(manifest))
    if with_contract and with_result:
        options, groups, metadata = trial.load_source(source)
        targets = metadata['sensor_targets']['variants']['edge_shares']
        expanded = dynamic.expand_departure_support(
            options, profiler.SHIFT_SUPPORT_S, begin_s=0,
            end_s=QUARTERS * 900, guard_s=profiler.GUARD_S)
        system = dynamic.build_passage_system(expanded, [SENSOR], QUARTERS)
        fit = dynamic.fit_integer_flows(
            system, targets, groups,
            departure_bounds=[{} for _ in range(QUARTERS)])
        selected = [option for option, count in zip(expanded, fit.counts) if count == 1]
        stage = tmp_path / '.saved-result-stage'
        candidate = profiler.automatic_passage._stage_selection(
            inputs, selected, stage)
        (source / 'result.json').write_text(json.dumps({
            'status': 'validated',
            'structural_repair': {'passes': 0, 'boundary_fallback': False},
            'selection_sha256': sha256_file(stage / 'selection.json'),
            'routes_sha256': sha256_file(candidate),
            'agents_sha256': sha256_file(stage / 'calibrated.agents.json'),
        }))
        shutil.rmtree(stage)
    return source


def _tree_state(root: Path) -> dict[str, tuple[str, int]]:
    return {str(path.relative_to(root)): (sha256_file(path), path.stat().st_size)
            for path in sorted(root.rglob('*')) if path.is_file()}


def _compress(source: Path, relative: str) -> None:
    path = source / relative
    with open(path, 'rb') as raw, gzip.open(path.with_name(path.name + '.gz'), 'wb') as packed:
        packed.write(raw.read())
    path.unlink()


class TestInventory:
    def test_a_complete_root_is_complete(self, tmp_path):
        found = profiler.inventory(evidence_root(tmp_path))

        assert found['trace_state'] == 'complete'
        assert {row['state'] for row in found['files'].values()} == {'raw'}
        assert found['replay_contract']['state'] == 'raw'

    def test_a_compressed_root_is_never_called_complete(self, tmp_path):
        source = evidence_root(tmp_path)
        _compress(source, 'evidence/learning-0-arm-1000/edge.xml')

        found = profiler.inventory(source)

        assert found['trace_state'] == 'compressed'
        assert found['files']['evidence/learning-0-arm-1000/edge.xml']['state'] == 'compressed'

    def test_a_pruned_root_is_incomplete_not_merely_smaller(self, tmp_path):
        source = evidence_root(tmp_path)
        (source / 'evidence/learning-0-arm-1002/vehroute.xml').unlink()

        found = profiler.inventory(source)

        assert found['trace_state'] == 'incomplete'
        assert found['files']['evidence/learning-0-arm-1002/vehroute.xml']['state'] == 'missing'

    def test_a_file_that_no_longer_matches_its_recorded_digest_is_not_raw_evidence(self, tmp_path):
        source = evidence_root(tmp_path)
        path = source / 'evidence/learning-0-arm-1000/edge.xml'
        path.write_text(path.read_text().replace('entered="1"', 'entered="9"'))

        found = profiler.inventory(source)

        assert found['trace_state'] == 'incomplete'
        assert found['files']['evidence/learning-0-arm-1000/edge.xml']['state'] \
            == 'raw_hash_mismatch'

    def test_a_root_without_a_manifest_cannot_be_classified_as_evidence(self, tmp_path):
        source = evidence_root(tmp_path)
        (source / 'report.json').unlink()

        assert profiler.inventory(source)['trace_state'] == 'incomplete'

    def test_pruning_markers_are_reported_rather_than_inferred(self, tmp_path):
        source = evidence_root(tmp_path, with_result=False)

        markers = profiler.inventory(source)['pruned_markers']

        assert markers['candidate_stages_present'] is False
        assert markers['result_json_present'] is False
        assert markers['source_reports_present'] is False


class TestOutputIsolation:
    def test_an_output_inside_the_evidence_root_is_refused(self, tmp_path):
        source = evidence_root(tmp_path)

        with pytest.raises(profiler.ReplayRefused, match='outside'):
            profiler.replay(source, source / 'profile')

    def test_an_output_that_contains_the_evidence_root_is_refused(self, tmp_path):
        source = evidence_root(tmp_path)

        with pytest.raises(profiler.ReplayRefused, match='outside'):
            profiler.replay(source, source.parent)

    def test_the_replay_leaves_every_source_byte_untouched(self, tmp_path):
        source = evidence_root(tmp_path)
        before = _tree_state(source)

        profiler.replay(source, tmp_path / 'out')

        assert _tree_state(source) == before

    def test_an_existing_output_directory_is_refused(self, tmp_path):
        source = evidence_root(tmp_path)
        (tmp_path / 'out').mkdir()

        with pytest.raises(FileExistsError):
            profiler.replay(source, tmp_path / 'out')


class TestRefusalBeforeMeasurement:
    def test_a_compressed_root_is_not_replayed_without_an_explicit_opt_in(self, tmp_path):
        source = evidence_root(tmp_path)
        _compress(source, 'evidence/learning-0-arm-1000/edge.xml')

        with pytest.raises(profiler.ReplayRefused, match='compressed'):
            profiler.replay(source, tmp_path / 'out')

    def test_an_incomplete_root_is_never_replayed(self, tmp_path):
        source = evidence_root(tmp_path)
        (source / 'evidence/learning-0-arm-1002/edge.xml').unlink()

        with pytest.raises(profiler.ReplayRefused, match='incomplete'):
            profiler.replay(source, tmp_path / 'out')

    def test_expanded_evidence_is_verified_against_its_recorded_digest(self, tmp_path):
        source = evidence_root(tmp_path)
        relative = 'evidence/learning-0-arm-1000/edge.xml'
        _compress(source, relative)
        with gzip.open(source / f'{relative}.gz', 'wb') as packed:
            packed.write(b'<meandata/>')

        with pytest.raises(profiler.ReplayRefused, match='recorded digest'):
            profiler.replay(source, tmp_path / 'out', allow_compressed=True)

    def test_a_trace_that_does_not_reconstruct_its_counts_is_not_timed(self, tmp_path):
        source = evidence_root(tmp_path)
        path = source / 'evidence/learning-0-arm-1000/edge.xml'
        path.write_text(path.read_text().replace('entered="2"', 'entered="3"'))
        manifest = json.loads((source / 'report.json').read_text())
        manifest['evidence_sha256'][
            'evidence/learning-0-arm-1000/edge.xml'] = sha256_file(path)
        (source / 'report.json').write_text(json.dumps(manifest))

        with pytest.raises(ValueError, match='reconstruct'):
            profiler.replay(source, tmp_path / 'out')

    def test_changed_production_code_identity_is_refused(self, tmp_path):
        source = evidence_root(tmp_path)
        path = source / profiler.REPLAY_CONTRACT
        contract = json.loads(path.read_text())
        contract['source_sha256']['dynamic_assignment'] = '0' * 64
        path.write_text(json.dumps(contract))
        manifest = json.loads((source / 'report.json').read_text())
        manifest['input_sha256'][path.name] = sha256_file(path)
        (source / 'report.json').write_text(json.dumps(manifest))

        with pytest.raises(profiler.ReplayRefused, match='production source differs'):
            profiler.replay(source, tmp_path / 'out')

    def test_a_saved_structural_repair_is_not_misprofiled_as_the_simple_path(self, tmp_path):
        source = evidence_root(tmp_path)
        result = json.loads((source / 'result.json').read_text())
        result['structural_repair']['passes'] = 1
        (source / 'result.json').write_text(json.dumps(result))

        with pytest.raises(profiler.ReplayRefused, match='structural repair'):
            profiler.replay(source, tmp_path / 'out')


class TestReplayReport:
    def test_the_original_measurement_is_reproduced_before_any_phase_is_reported(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert report['status'] == 'replayed'
        assert report['measurement_reconstruction_verified'] is True
        phases = [row['phase'] for row in report['timing']['phases']]
        assert phases.index('load_source') < phases.index('fit_integer_flows')

    def test_every_phase_carries_parent_date_variant_and_pid(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        rows = report['timing']['phases']
        assert {row['variant'] for row in rows} == {'q50'}
        assert {row['date'] for row in rows} == {'2027-06-16'}
        assert all(isinstance(row['pid'], int) for row in rows)
        assert rows[0]['parent_phase'] is None
        assert all(row['parent_phase'] is not None for row in rows[1:])

    def test_the_two_separate_system_constructions_are_timed_separately(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        phases = [row['phase'] for row in report['timing']['phases']]
        assert 'load_source' in phases
        assert 'build_passage_system_base' in phases
        assert 'build_passage_system_expanded' in phases

    def test_categories_this_replay_cannot_measure_are_named(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert 'sumo_subprocess' in report['unmeasured_categories']
        assert report['reads_saved_traces_only'] is True

    def test_legacy_evidence_is_preparation_only_and_never_runs_the_solver(self, tmp_path):
        source = evidence_root(tmp_path, with_contract=False, with_result=False)

        with pytest.raises(profiler.ReplayRefused, match='preparation-only'):
            profiler.replay(source, tmp_path / 'refused')

        report = profiler.replay(
            source, tmp_path / 'out', preparation_only=True)
        assert report['status'] == 'profiled_preparation_only'
        assert report['solver_constraints'] == 'not_loaded_preparation_only'
        assert report['selection_reproduced']['state'] == 'not_evaluated'
        assert 'fit_integer_flows' not in {
            row['phase'] for row in report['timing']['phases']}

    def test_the_report_is_written_beside_the_replay_not_the_evidence(self, tmp_path):
        source = evidence_root(tmp_path)

        profiler.replay(source, tmp_path / 'out')

        assert (tmp_path / 'out/replay_report.json').is_file()
        assert not (source / 'replay_report.json').exists()

    def test_any_changed_route_selection_is_a_failed_equivalence_gate(self, tmp_path):
        source = evidence_root(tmp_path)
        result = json.loads((source / 'result.json').read_text())
        result['routes_sha256'] = 'f' * 64
        (source / 'result.json').write_text(json.dumps(result))

        with pytest.raises(profiler.ReplayRefused, match='selection differs'):
            profiler.replay(source, tmp_path / 'out')

        report = json.loads((tmp_path / 'out/replay_report.json').read_text())
        assert report['status'] == 'refused'
        assert report['selection_reproduced']['state'] == 'differs'

    def test_a_compressed_root_replays_from_verified_copies_and_stays_labelled(self, tmp_path):
        source = evidence_root(tmp_path)
        _compress(source, 'evidence/learning-0-arm-1001/vehroute.xml')

        report = profiler.replay(source, tmp_path / 'out', allow_compressed=True)

        assert report['status'] == 'replayed'
        assert report['trace_state'] == 'compressed'
        assert report['expanded_root'].endswith('expanded')

    def test_main_reports_a_refusal_instead_of_a_timing(self, tmp_path, capsys, monkeypatch):
        source = evidence_root(tmp_path)
        (source / 'evidence/learning-0-arm-1000/edge.xml').unlink()
        monkeypatch.setattr('sys.argv', ['profile', '--source', str(source),
                                         '--out', str(tmp_path / 'out')])

        assert profiler.main() == 2
        assert json.loads(capsys.readouterr().out)['status'] == 'refused'


class TestPhaseAccounting:
    def _recorder(self, ticks):
        values = iter(ticks)
        return profiler.PhaseRecorder({'variant': 'q50'}, clock=lambda: next(values), pid=7)

    def test_nested_time_is_never_counted_twice(self):
        recorder = self._recorder([0, 1, 4, 5, 10, 10])

        with recorder.phase('parent'):
            with recorder.phase('child_a'):
                pass
            with recorder.phase('child_b'):
                pass

        rows = {row['phase']: row for row in recorder.records()}
        assert rows['parent']['wall_s'] == 10
        assert rows['child_a']['wall_s'] == 3 and rows['child_b']['wall_s'] == 5
        assert rows['parent']['exclusive_s'] == 2

    def test_a_concurrent_wave_gets_no_subtracted_exclusive_time(self):
        recorder = self._recorder([0, 1, 5, 2, 6, 10])

        with recorder.phase('wave', concurrent=True):
            with recorder.phase('worker_a'):
                pass
            with recorder.phase('worker_b'):
                pass

        wave = {row['phase']: row for row in recorder.records()}['wave']
        assert wave['exclusive_s'] is None
        assert wave['exclusive_basis'] == 'concurrent_children_overlap'
        assert wave['children_wall_sum_s'] == 8 and wave['children_wall_max_s'] == 4

    def test_the_unattributed_remainder_is_reported_explicitly(self):
        recorder = self._recorder([0, 2, 5, 9])

        with recorder.phase('root'):
            with recorder.phase('only_child'):
                pass

        summary = recorder.summary()
        assert summary['root_wall_s'] == 9
        assert summary['top_level_wall_sum_s'] == 3
        assert summary['residual_s'] == 6
        assert summary['residual_basis'] == 'wall_minus_sequential_children'

    def test_a_failing_phase_still_records_its_wall_time(self):
        recorder = self._recorder([0, 3])

        with pytest.raises(RuntimeError):
            with recorder.phase('boom'):
                raise RuntimeError('failed work still costs time')

        assert recorder.records()[0]['wall_s'] == 3


class TestTimingsStayOutsideIdentity:
    def test_observational_timings_do_not_change_the_demand_build_id(self):
        import build_sumo_demand as bsd
        from traffic_sim.core.fingerprint import make_fingerprint

        base = {'schema_version': 1, 'build': {'days': 1}}
        fast = {**base, 'timings_s': {'candidate_generation': 1.0},
                'pfe_timing_s': {'solve': 2.0}}
        slow = {**base, 'timings_s': {'candidate_generation': 900.0},
                'pfe_timing_s': {'solve': 800.0}}

        identities = [make_fingerprint(
            contract=bsd.demand_build_fingerprint_contract(meta),
            artifacts={}, source_files={}, source_file_records={})['build_id']
            for meta in (fast, slow)]

        assert identities[0] == identities[1]

    def test_the_profiler_is_not_part_of_the_demand_source_fingerprint(self):
        from traffic_sim.demand.source_identity import demand_source_paths

        tracked = {Path(path).resolve()
                   for path in demand_source_paths(Path.cwd()).values()}

        assert Path(profiler.__file__).resolve() not in tracked

    def test_the_profiler_is_not_part_of_the_route_catalog_identity(self):
        from traffic_sim.demand.route_catalog import CATALOG_SOURCE_LABELS

        assert not any('profile_passage_replay' in str(label)
                       for label in CATALOG_SOURCE_LABELS)

    def test_the_profiler_writes_nothing_the_pipeline_reads(self, tmp_path):
        source = evidence_root(tmp_path)

        profiler.replay(source, tmp_path / 'out')

        written = {path.name for path in (tmp_path / 'out').rglob('*') if path.is_file()}
        assert 'demand_meta.json' not in written
        assert 'demand_build_spec.json' not in written


def test_the_replayed_system_is_the_production_system(tmp_path):
    """The profiler must not carry its own copy of the algorithm it measures."""
    source = evidence_root(tmp_path)
    options, _groups, metadata = trial.load_source(source)

    report = profiler.replay(source, tmp_path / 'out')
    expected = dynamic.build_passage_system(
        options, [SENSOR], metadata['n_intervals'])

    assert report['sparse_nonzeros'] >= expected.matrix.nnz
    assert report['vehicles'] == len(options)


def test_production_evidence_persists_the_exact_replay_contract(tmp_path, monkeypatch):
    from tests.test_automatic_passage import fixture

    inputs, reports, network, _calls = fixture(tmp_path, monkeypatch)
    profiler.automatic_passage.refine_variants(
        inputs, reports, network, tmp_path / 'evidence')

    contract_path = tmp_path / 'evidence/q50/input' \
        / profiler.automatic_passage.REPLAY_CONTRACT_NAME
    contract = json.loads(contract_path.read_text())
    manifest = json.loads((tmp_path / 'evidence/q50/report.json').read_text())
    assert contract['retained_bounds_pq'] == [{}, {}, {}, {}]
    assert contract['source_sha256'] == profiler.automatic_passage.replay_source_sha256()
    assert manifest['input_sha256'][contract_path.name] == sha256_file(contract_path)


class TestRepeatedProfile:
    def test_the_first_pass_is_reported_apart_from_the_reused_process(self, tmp_path):
        source = evidence_root(tmp_path)

        summary = profiler.profile(source, tmp_path / 'out', repeats=3)

        assert [run['repeat'] for run in summary['runs']] == [1, 2, 3]
        assert all(run['status'] == 'replayed' for run in summary['runs'])
        assert 'after module imports' in summary['process_state']
        assert 'load_source' in summary['first_repeat_phase_wall_s']
        assert summary['reused_process_phase_wall_s']['load_source']['n'] == 2

    def test_one_repeat_reports_no_reused_process_evidence(self, tmp_path):
        summary = profiler.profile(evidence_root(tmp_path), tmp_path / 'out', repeats=1)

        assert summary['reused_process_phase_wall_s'] == {}

    def test_zero_repeats_is_refused(self, tmp_path):
        with pytest.raises(profiler.ReplayRefused, match='at least one'):
            profiler.profile(evidence_root(tmp_path), tmp_path / 'out', repeats=0)

    def test_each_repeat_keeps_its_own_evidence_directory(self, tmp_path):
        profiler.profile(evidence_root(tmp_path), tmp_path / 'out', repeats=2)

        assert (tmp_path / 'out/repeat-1/replay_report.json').is_file()
        assert (tmp_path / 'out/repeat-2/replay_report.json').is_file()
        assert (tmp_path / 'out/profile_report.json').is_file()


class TestSharedSolverCache:
    """H1: three repeats measured three COLD solves, then called them reuse.

    Production hit its solver cache, so a profile whose every repeat starts
    from an empty cache ranks the solver far above what production pays.
    """

    def test_repeat_one_is_cold_and_later_repeats_hit_the_shared_cache(self, tmp_path):
        summary = profiler.profile(evidence_root(tmp_path), tmp_path / 'out', repeats=3)

        assert [run['solver_cache_hit'] for run in summary['runs']] == [False, True, True]
        keys = {run['solver_request_key'] for run in summary['runs']}
        assert len(keys) == 1 and all(isinstance(key, str) and key for key in keys)

    def test_the_shared_cache_is_created_under_the_profile_output(self, tmp_path):
        source = evidence_root(tmp_path)
        before = _tree_state(source)

        summary = profiler.profile(source, tmp_path / 'out', repeats=2)

        cache = Path(summary['solver_cache_root'])
        assert cache.is_dir()
        assert cache.parent == (tmp_path / 'out').resolve()
        assert source.resolve() not in cache.parents and cache not in source.resolve().parents
        assert _tree_state(source) == before

    def test_the_cache_starts_empty_so_the_first_solve_cannot_inherit(self, tmp_path):
        summary = profiler.profile(evidence_root(tmp_path), tmp_path / 'out', repeats=1)

        assert summary['runs'][0]['solver_cache_hit'] is False
        assert summary['solver_cache_basis'] \
            == 'empty_output_local_cache_then_shared_across_repeats'

    def test_the_summary_describes_process_and_solver_cache_separately(self, tmp_path):
        summary = profiler.profile(evidence_root(tmp_path), tmp_path / 'out', repeats=2)

        assert 'after module imports' in summary['process_state']
        assert 'solver cache' in summary['process_state']

    def test_a_single_replay_keeps_its_own_private_cache(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert report['solver_cache_hit'] is False
        assert (tmp_path / 'out/solver-cache').is_dir()

    def test_a_missing_solver_state_is_refused(self, tmp_path, monkeypatch):
        source = evidence_root(tmp_path)
        real = profiler.dynamic.fit_integer_flows

        def _fit_without_state(*args, **kwargs):
            fit = real(*args, **kwargs)
            Path(kwargs['checkpoint_dir'], 'state.json').unlink()
            return fit

        monkeypatch.setattr(profiler.dynamic, 'fit_integer_flows', _fit_without_state)

        with pytest.raises(profiler.ReplayRefused, match='solver checkpoint state'):
            profiler.replay(source, tmp_path / 'out')

    def test_a_non_boolean_cache_status_is_refused(self, tmp_path, monkeypatch):
        source = evidence_root(tmp_path)
        real = profiler.dynamic.fit_integer_flows

        def _fit_with_bad_state(*args, **kwargs):
            fit = real(*args, **kwargs)
            path = Path(kwargs['checkpoint_dir'], 'state.json')
            state = json.loads(path.read_text())
            state['cache_hit'] = 'yes'
            path.write_text(json.dumps(state))
            return fit

        monkeypatch.setattr(profiler.dynamic, 'fit_integer_flows', _fit_with_bad_state)

        with pytest.raises(profiler.ReplayRefused, match='cache_hit'):
            profiler.replay(source, tmp_path / 'out')

    def test_preparation_only_reports_no_solver_cache_status(self, tmp_path):
        source = evidence_root(tmp_path, with_contract=False, with_result=False)

        report = profiler.replay(source, tmp_path / 'out', preparation_only=True)

        assert report['status'] == 'profiled_preparation_only'
        assert 'solver_cache_hit' not in report


class TestDirectReplayCacheIsolation:
    """A caller-supplied solver cache must be checked by the call that uses it.

    ``profile()`` checked the shared cache, but a direct ``replay()`` accepted
    any path and created it. The guard belongs in the call that writes.
    """

    def test_a_cache_inside_the_evidence_is_refused(self, tmp_path):
        source = evidence_root(tmp_path)

        with pytest.raises(profiler.ReplayRefused, match='solver cache'):
            profiler.replay(source, tmp_path / 'out',
                            _solver_cache_dir=source / 'stolen-cache')

    def test_a_cache_containing_the_evidence_is_refused(self, tmp_path):
        source = evidence_root(tmp_path)

        with pytest.raises(profiler.ReplayRefused, match='solver cache'):
            profiler.replay(source, tmp_path / 'out', _solver_cache_dir=source.parent)

    def test_the_cache_may_not_be_the_evidence_root_itself(self, tmp_path):
        source = evidence_root(tmp_path)

        with pytest.raises(profiler.ReplayRefused, match='solver cache'):
            profiler.replay(source, tmp_path / 'out', _solver_cache_dir=source)

    def test_the_refusal_happens_before_anything_is_written(self, tmp_path):
        source = evidence_root(tmp_path)
        before = _tree_state(source)

        with pytest.raises(profiler.ReplayRefused):
            profiler.replay(source, tmp_path / 'out',
                            _solver_cache_dir=source / 'input')

        assert not (tmp_path / 'out').exists()
        assert _tree_state(source) == before


class TestStructureMeasurement:
    """Step 2 instrumentation ONLY: measure the structure cost, change nothing.

    The June replay found 51,640 vehicles behind 491 distinct edge sequences,
    and 229,444 distance calls in one report. Before any route-facts cache is
    written, the profile must state those counts and what the route and
    distance functions actually cost.
    """

    def test_the_route_shape_of_each_measured_file_is_reported(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        shape = report['structure_measurement']['source_route']
        assert shape['vehicles'] == len(DEPARTURES)
        # Every fixture vehicle drives the same o->s->d route.
        assert shape['unique_edge_tuples'] == 1
        assert shape['unique_endpoint_pairs'] == 1
        assert shape['vehicles_per_unique_edge_tuple'] == float(len(DEPARTURES))
        assert report['structure_measurement']['candidate_route']['vehicles'] \
            == len(DEPARTURES)

    def test_the_route_and_distance_functions_are_counted_and_timed(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        measured = report['structure_measurement']
        for key in ('structure_source_calls', 'structure_candidate_calls'):
            rows = measured[key]
            assert rows['_route_structure_metrics']['calls'] == 1
            for name, row in rows.items():
                assert row['calls'] >= 0
                assert 0 <= row['exclusive_s'] <= row['cumulative_s'] + 1e-9, name

    def test_exclusive_time_never_double_counts_a_nested_distance_call(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        rows = report['structure_measurement']['structure_source_calls']
        phases = {row['phase']: row for row in report['timing']['phases']}
        total_exclusive = sum(row['exclusive_s'] for row in rows.values())
        assert total_exclusive <= phases['structure_source']['wall_s'] + 1e-6

    def test_the_instrumentation_is_removed_again(self, tmp_path):
        from demand import structure

        originals = {name: getattr(structure, name)
                     for name in profiler.STRUCTURE_CALL_NAMES}

        profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        for name, function in originals.items():
            assert getattr(structure, name) is function, name

    def test_measuring_does_not_change_what_the_structure_report_says(self, tmp_path):
        from demand.structure import calibrated_structure_report
        source = evidence_root(tmp_path)
        route = source / 'input/calibrated.rou.xml'

        plain = calibrated_structure_report(route)
        with profiler.measure_structure_calls() as measured:
            instrumented = calibrated_structure_report(route)

        assert instrumented == plain
        assert measured.report()['_route_structure_metrics']['calls'] == 1

    def test_the_measurement_is_declared_observational(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert 'fingerprint' in report['structure_measurement']['basis']

    def test_no_route_facts_cache_is_written(self, tmp_path):
        """Step 2 is measurement only; nothing may be reused between reports."""
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        rows = report['structure_measurement']
        assert rows['structure_source_calls']['_route_structure_metrics']['calls'] \
            == rows['structure_candidate_calls']['_route_structure_metrics']['calls'] == 1


class TestCacheOwnership:
    """A caller-supplied cache must belong to the profile layout, not anywhere."""

    def test_the_keyword_is_private(self):
        import inspect

        parameters = inspect.signature(profiler.replay).parameters
        assert '_solver_cache_dir' in parameters
        assert 'solver_cache_dir' not in parameters

    def test_a_cache_outside_the_profile_layout_is_refused(self, tmp_path):
        source = evidence_root(tmp_path)

        with pytest.raises(profiler.ReplayRefused, match='beside'):
            profiler.replay(source, tmp_path / 'out',
                            _solver_cache_dir=tmp_path / 'elsewhere/solver-cache')

    def test_the_profile_layout_is_accepted(self, tmp_path):
        source = evidence_root(tmp_path)
        root = tmp_path / 'profile'
        root.mkdir()

        report = profiler.replay(source, root / 'repeat-1',
                                 _solver_cache_dir=root / 'solver-cache')

        assert report['status'] == 'replayed'
        assert report['solver_cache_hit'] is False

    def test_ownership_is_checked_before_anything_is_created(self, tmp_path):
        source = evidence_root(tmp_path)
        before = _tree_state(source)

        with pytest.raises(profiler.ReplayRefused):
            profiler.replay(source, tmp_path / 'out',
                            _solver_cache_dir=tmp_path / 'stray/solver-cache')

        assert not (tmp_path / 'out').exists()
        assert not (tmp_path / 'stray').exists()
        assert _tree_state(source) == before

    def test_a_cache_inside_the_evidence_is_still_refused(self, tmp_path):
        source = evidence_root(tmp_path)

        with pytest.raises(profiler.ReplayRefused, match='solver cache'):
            profiler.replay(source, source.parent / 'out',
                            _solver_cache_dir=source / 'solver-cache')


class TestNamedRouteInventory:
    """Production resolves shared <route id=...>; the inventory must too."""

    def _write(self, path, body):
        path.write_text(f'<routes>\n{body}\n</routes>\n')
        return path

    def test_named_references_are_resolved_and_counted(self, tmp_path):
        route = self._write(tmp_path / 'named.rou.xml', (
            '<route id="r1" edges="o s d"/>\n'
            '<vehicle id="v0" depart="0" route="r1"/>\n'
            '<vehicle id="v1" depart="1" route="r1"/>\n'
            '<vehicle id="v2" depart="2"><route edges="o s d"/></vehicle>\n'))

        shape = profiler.route_shape_inventory(route)

        assert shape['vehicles'] == 3
        assert shape['inline_route_vehicles'] == 1
        assert shape['named_reference_vehicles'] == 2
        assert shape['named_route_definitions'] == 1
        # The named and inline forms describe the same physical route.
        assert shape['unique_edge_tuples'] == 1
        assert shape['unique_endpoint_pairs'] == 1

    def test_an_unresolvable_reference_is_refused(self, tmp_path):
        route = self._write(tmp_path / 'broken.rou.xml',
                            '<vehicle id="v0" depart="0" route="missing"/>')

        with pytest.raises(profiler.ReplayRefused, match='no resolvable route'):
            profiler.route_shape_inventory(route)

    def test_an_empty_route_is_refused(self, tmp_path):
        route = self._write(tmp_path / 'empty.rou.xml',
                            '<vehicle id="v0" depart="0"><route edges=""/></vehicle>')

        with pytest.raises(profiler.ReplayRefused, match='no resolvable route'):
            profiler.route_shape_inventory(route)

    def test_a_file_without_vehicles_is_refused(self, tmp_path):
        route = self._write(tmp_path / 'none.rou.xml', '<route id="r1" edges="o s"/>')

        with pytest.raises(profiler.ReplayRefused, match='no vehicles'):
            profiler.route_shape_inventory(route)

    def test_the_inline_fixture_is_reported_as_inline(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        shape = report['structure_measurement']['source_route']
        assert shape['inline_route_vehicles'] == len(DEPARTURES)
        assert shape['named_reference_vehicles'] == 0
        assert shape['named_route_definitions'] == 0


class TestGeometryIdentity:
    """The measurement must say which geometry and sensor set produced it."""

    def test_every_instrumented_function_is_reported_even_at_zero_calls(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        rows = report['structure_measurement']['structure_source_calls']
        assert set(rows) == set(profiler.STRUCTURE_CALL_NAMES)

    def test_the_input_identity_binds_geometry_content_and_sensors(self, tmp_path):
        from demand import structure

        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        identity = report['structure_measurement']['input_identity']
        assert identity['geo_path'] == str(structure.GEO_PATH)
        assert identity['geometry_sha256'] == sha256_file(structure.GEO_PATH)
        assert identity['measured_sensor_edges'] == 7
        assert len(identity['measured_sensor_edge_identity']) == 64

    def test_the_identity_does_not_warm_or_mutate_the_geometry_cache(self, tmp_path):
        from demand import structure

        structure._EDGE_GEOMETRY_CACHE = None
        identity = profiler.structure_input_identity()

        assert structure._EDGE_GEOMETRY_CACHE is None
        assert identity['measured_sensor_edges'] == 7

    def test_the_sensor_identity_is_deterministic_and_order_free(self, tmp_path):
        first = profiler.structure_input_identity()
        second = profiler.structure_input_identity()

        assert first == second


class TestArchiveValidationPhase:
    def _stub(self, monkeypatch, calls):
        from traffic_sim.simulation import monthly_demand

        class _Spec:
            @staticmethod
            def from_dict(payload):
                return payload

        monkeypatch.setattr(monthly_demand, 'DemandBuildSpec', _Spec)
        monkeypatch.setattr(monthly_demand, 'validate_demand_archive',
                            lambda archive, required: calls.append((archive, required)))

    def test_archive_validation_is_named_as_unmeasured_when_not_requested(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert 'archive_validation' in report['unmeasured_categories']
        assert 'archive_validation' not in report

    def test_each_archive_call_is_timed_on_its_own(self, tmp_path, monkeypatch):
        calls = []
        self._stub(monkeypatch, calls)
        spec = tmp_path / 'spec.json'
        spec.write_text(json.dumps({'days': 1}))

        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out',
                                 archive=tmp_path / 'archive', demand_spec=spec,
                                 archive_repeats=3)

        assert len(calls) == 3
        assert report['archive_validation']['calls'] == ['valid'] * 3
        assert 'archive_validation' not in report['unmeasured_categories']
        timed = [row for row in report['timing']['phases']
                 if row['phase'] == 'validate_demand_archive']
        assert [row['call'] for row in timed] == [0, 1, 2]
        assert all(row['parent_phase'] == 'archive_validation' for row in timed)

    def test_a_rejected_archive_refuses_the_replay_after_recording_it(self, tmp_path, monkeypatch):
        from traffic_sim.simulation import monthly_demand
        self._stub(monkeypatch, [])
        monkeypatch.setattr(monthly_demand, 'validate_demand_archive',
                            lambda archive, required: (_ for _ in ()).throw(
                                ValueError('archive has another build contract')))
        spec = tmp_path / 'spec.json'
        spec.write_text(json.dumps({'days': 1}))

        with pytest.raises(profiler.ReplayRefused, match='archive validation'):
            profiler.replay(evidence_root(tmp_path), tmp_path / 'out',
                            archive=tmp_path / 'archive', demand_spec=spec,
                            archive_repeats=1)

        report = json.loads((tmp_path / 'out/replay_report.json').read_text())
        assert report['status'] == 'refused'
        assert 'another build contract' in report['archive_validation']['calls'][0]

    def test_the_cli_refuses_an_archive_without_its_contract(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr('sys.argv', [
            'profile', '--source', str(evidence_root(tmp_path)),
            '--out', str(tmp_path / 'out'), '--archive', str(tmp_path / 'archive')])

        assert profiler.main() == 2
        assert '--demand-spec' in json.loads(capsys.readouterr().out)['reason']

    def test_archive_validation_can_be_profiled_without_a_passage_source(
            self, tmp_path, monkeypatch):
        calls = []
        self._stub(monkeypatch, calls)
        archive = tmp_path / 'archive'
        archive.mkdir()
        spec = tmp_path / 'spec.json'
        spec.write_text(json.dumps({'days': 1}))

        report = profiler.profile_archive_validation(
            archive, spec, tmp_path / 'out', repeats=3)

        assert report['status'] == 'profiled_archive_validation'
        assert report['archive_validation']['calls'] == ['valid'] * 3
        assert len(calls) == 3

    def test_archive_only_cli_does_not_require_source(self, tmp_path, capsys, monkeypatch):
        self._stub(monkeypatch, [])
        archive = tmp_path / 'archive'
        archive.mkdir()
        spec = tmp_path / 'spec.json'
        spec.write_text(json.dumps({'days': 1}))
        monkeypatch.setattr('sys.argv', [
            'profile', '--archive-only', '--archive', str(archive),
            '--demand-spec', str(spec), '--out', str(tmp_path / 'out')])

        assert profiler.main() == 0
        assert json.loads(capsys.readouterr().out)['status'] \
            == 'profiled_archive_validation'

    def test_archive_only_refusal_is_written_and_returns_nonzero(
            self, tmp_path, capsys, monkeypatch):
        from traffic_sim.simulation import monthly_demand
        self._stub(monkeypatch, [])
        monkeypatch.setattr(monthly_demand, 'validate_demand_archive',
                            lambda *_args: (_ for _ in ()).throw(ValueError('wrong variants')))
        archive = tmp_path / 'archive'
        archive.mkdir()
        spec = tmp_path / 'spec.json'
        spec.write_text(json.dumps({'days': 1}))
        monkeypatch.setattr('sys.argv', [
            'profile', '--archive-only', '--archive', str(archive),
            '--demand-spec', str(spec), '--out', str(tmp_path / 'out')])

        assert profiler.main() == 2
        assert json.loads(capsys.readouterr().out)['status'] == 'refused'
        saved = json.loads((tmp_path / 'out/archive_validation_report.json').read_text())
        assert saved['status'] == 'refused'
        assert 'wrong variants' in saved['archive_validation']['calls'][0]

    def test_zero_archive_repeats_is_refused_in_combined_mode(self, tmp_path, monkeypatch):
        self._stub(monkeypatch, [])
        archive = tmp_path / 'archive'
        archive.mkdir()
        spec = tmp_path / 'spec.json'
        spec.write_text(json.dumps({'days': 1}))

        with pytest.raises(profiler.ReplayRefused, match='at least one'):
            profiler.replay(evidence_root(tmp_path), tmp_path / 'out',
                            archive=archive, demand_spec=spec, archive_repeats=0)

    def test_archive_only_invalid_spec_is_a_structured_refusal(
            self, tmp_path, capsys, monkeypatch):
        archive = tmp_path / 'archive'
        archive.mkdir()
        spec = tmp_path / 'spec.json'
        spec.write_text('{')
        monkeypatch.setattr('sys.argv', [
            'profile', '--archive-only', '--archive', str(archive),
            '--demand-spec', str(spec), '--out', str(tmp_path / 'out')])

        assert profiler.main() == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload['status'] == 'refused'
        assert 'invalid demand build spec' in payload['reason']


def test_a_failed_replay_still_leaves_the_phases_it_did_measure(tmp_path):
    """Losing the measurement is the one thing a measuring tool must not do."""
    source = evidence_root(tmp_path)
    path = source / 'evidence/learning-0-arm-1000/edge.xml'
    path.write_text(path.read_text().replace('entered="2"', 'entered="3"'))
    manifest = json.loads((source / 'report.json').read_text())
    manifest['evidence_sha256'][
        'evidence/learning-0-arm-1000/edge.xml'] = sha256_file(path)
    (source / 'report.json').write_text(json.dumps(manifest))

    with pytest.raises(ValueError):
        profiler.replay(source, tmp_path / 'out')

    report = json.loads((tmp_path / 'out/replay_report.json').read_text())
    assert report['status'] == 'failed'
    assert 'reconstruct' in report['reason']
    assert [row['phase'] for row in report['timing']['phases']][:2] \
        == ['replay', 'inventory']
    assert 'load_source' in [row['phase'] for row in report['timing']['phases']]


def test_a_refusal_is_recorded_as_a_refusal_not_a_failure(tmp_path):
    source = evidence_root(tmp_path)
    (source / 'evidence/learning-0-arm-1001/edge.xml').unlink()

    with pytest.raises(profiler.ReplayRefused):
        profiler.replay(source, tmp_path / 'out')

    report = json.loads((tmp_path / 'out/replay_report.json').read_text())
    assert report['status'] == 'refused'
    assert report['trace_state'] == 'incomplete'
    assert report['timing']['phases'][1]['phase'] == 'inventory'
    assert report['timing']['phases'][1]['files'] == len(report['inventory']['files'])


def test_the_replay_mirrors_production_and_reuses_the_verified_base_system(tmp_path):
    """The profile must measure the path production actually runs."""
    source = evidence_root(tmp_path)

    report = profiler.replay(source, tmp_path / 'out')

    rows = {row['phase']: row for row in report['timing']['phases']}
    assert rows['build_passage_system_base']['source'] == 'reused_from_load_source'
    assert report['status'] == 'replayed'
    assert report['selection_reproduced']['state'] == 'identical'


def test_a_verified_system_that_does_not_match_the_saved_targets_is_refused(
        tmp_path, monkeypatch):
    source = evidence_root(tmp_path)
    real = profiler.trial.load_verified_source

    def _mismatched(path):
        verified = real(path)
        return profiler.trial.VerifiedSource(
            verified.options, verified.groups, verified.metadata,
            profiler.dynamic.build_passage_system(
                list(verified.options), [SENSOR], QUARTERS + 1))

    monkeypatch.setattr(profiler.trial, 'load_verified_source', _mismatched)

    with pytest.raises(profiler.ReplayRefused, match='does not match the saved targets'):
        profiler.replay(source, tmp_path / 'out')


def test_each_repeat_carries_its_own_structure_measurement(tmp_path):
    summary = profiler.profile(evidence_root(tmp_path), tmp_path / 'out', repeats=2)

    for run in summary['runs']:
        measured = run['structure_measurement']
        assert measured['route_facts_cache'] == 'operation_scoped_content_bound'
        assert measured['source_route']['unique_edge_tuples'] == 1
        assert measured['structure_source_calls']['_route_structure_metrics']['calls'] == 1


def test_the_sensor_identity_uses_the_newline_terminated_convention():
    """Two machines must derive the SAME digest from the same sensor set."""
    import hashlib
    import json
    from demand import structure

    geometry = json.loads(Path(structure.GEO_PATH).read_text())
    ids = sorted(feature['properties']['id'] for feature in geometry['features']
                 if feature.get('geometry', {}).get('type') == 'LineString'
                 and feature.get('properties', {}).get('sensor_id'))
    expected = hashlib.sha256(
        ''.join(f'{edge}\n' for edge in ids).encode('utf-8')).hexdigest()

    identity = profiler.structure_input_identity()

    assert identity['measured_sensor_edge_identity'] == expected
    # A bare join is a different digest for identical edges; pin the difference.
    assert expected != hashlib.sha256('\n'.join(ids).encode('utf-8')).hexdigest()


class TestSharedStructureContext:
    """One context per replay: source and candidate must share it."""

    def test_the_replay_declares_an_operation_scoped_content_bound_cache(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert report['structure_measurement']['route_facts_cache'] \
            == 'operation_scoped_content_bound'

    def test_source_and_candidate_reports_share_one_context(self, tmp_path, monkeypatch):
        from demand import structure

        built = []
        real = structure.StructureContext
        monkeypatch.setattr(structure, 'StructureContext',
                            lambda: (built.append(1), real())[1])

        profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        # One for the replay itself; the two reports must not each make their own.
        assert len(built) == 1

    def test_the_shared_context_is_reported_by_its_content_identity(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        measured = report['structure_measurement']
        assert measured['input_identity']['geometry_sha256'] \
            == measured['structure_context']['geometry_sha256']
        assert measured['structure_context']['sensor_identity'] \
            == measured['input_identity']['measured_sensor_edge_identity']


class TestSolverMeasurement:
    """Step 3 instrumentation, activated privately from the profiler."""

    def test_the_replay_reports_every_declared_solver_phase(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        measured = report['solver_measurement']
        assert set(measured['phases']) == set(profiler.dynamic.SOLVER_PHASE_NAMES)
        assert measured['phases']['milp_solve']['calls'] == 1
        assert measured['milp_executed'] is True

    def test_a_cache_hit_shows_that_milp_did_not_run(self, tmp_path):
        summary = profiler.profile(evidence_root(tmp_path), tmp_path / 'out', repeats=2)

        first, second = (run['solver_measurement'] for run in summary['runs'])
        assert first['milp_executed'] is True
        assert second['milp_executed'] is False
        assert second['phases']['milp_solve']['calls'] == 0
        assert second['phases']['checkpoint_cache_lookup']['calls'] == 1
        assert first['solver_cache_hit'] is False and second['solver_cache_hit'] is True

    def test_exclusive_solver_time_fits_inside_the_measured_phase(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        phases = {row['phase']: row for row in report['timing']['phases']}
        total = sum(row['exclusive_s']
                    for row in report['solver_measurement']['phases'].values())
        assert 0 < total <= phases['fit_integer_flows']['wall_s'] + 1e-6

    def test_the_solver_measurement_is_declared_observational(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert 'fingerprint' in report['solver_measurement']['basis']

    def test_the_hook_is_uninstalled_after_the_replay(self, tmp_path):
        profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert profiler.dynamic._SOLVER_PHASE_OBSERVER is None

    def test_the_hook_is_uninstalled_after_a_refusal(self, tmp_path):
        source = evidence_root(tmp_path)
        (source / 'evidence/learning-0-arm-1001/edge.xml').unlink()

        with pytest.raises(profiler.ReplayRefused):
            profiler.replay(source, tmp_path / 'out')

        assert profiler.dynamic._SOLVER_PHASE_OBSERVER is None

    def test_measurement_does_not_reach_the_solver_checkpoint(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        state = json.loads((tmp_path / 'out/solver/state.json').read_text())
        assert set(state) == {'key', 'status', 'cache_hit', 'columns', 'rows',
                              'has_incumbent', 'wall_s', 'cpu_s', 'message'}
        assert report['solver_request_key'] == state['key']
