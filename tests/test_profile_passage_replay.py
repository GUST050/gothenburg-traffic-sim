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


def evidence_root(tmp_path: Path, *, departures=DEPARTURES) -> Path:
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
        source = evidence_root(tmp_path)

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


class TestReplayReport:
    def test_the_original_measurement_is_reproduced_before_any_phase_is_reported(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert report['status'] == 'replayed'
        assert report['source_reconstruction_verified'] is True
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

    def test_a_solve_without_the_retained_bounds_says_so(self, tmp_path):
        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert report['solver_constraints'] == 'targets_and_groups_only'
        assert 'source_reports.json' in report['solver_constraints_reason']
        assert report['selection_reproduced']['state'] == 'unavailable'

    def test_the_report_is_written_beside_the_replay_not_the_evidence(self, tmp_path):
        source = evidence_root(tmp_path)

        profiler.replay(source, tmp_path / 'out')

        assert (tmp_path / 'out/replay_report.json').is_file()
        assert not (source / 'replay_report.json').exists()

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


class TestRepeatedProfile:
    def test_the_cold_first_pass_is_reported_apart_from_the_reused_process(self, tmp_path):
        source = evidence_root(tmp_path)

        summary = profiler.profile(source, tmp_path / 'out', repeats=3)

        assert [run['repeat'] for run in summary['runs']] == [1, 2, 3]
        assert all(run['status'] == 'replayed' for run in summary['runs'])
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

    def test_a_rejected_archive_is_recorded_rather_than_hidden(self, tmp_path, monkeypatch):
        from traffic_sim.simulation import monthly_demand
        self._stub(monkeypatch, [])
        monkeypatch.setattr(monthly_demand, 'validate_demand_archive',
                            lambda archive, required: (_ for _ in ()).throw(
                                ValueError('archive has another build contract')))
        spec = tmp_path / 'spec.json'
        spec.write_text(json.dumps({'days': 1}))

        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out',
                                 archive=tmp_path / 'archive', demand_spec=spec,
                                 archive_repeats=1)

        assert report['status'] == 'replayed'
        assert 'another build contract' in report['archive_validation']['calls'][0]

    def test_the_cli_refuses_an_archive_without_its_contract(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr('sys.argv', [
            'profile', '--source', str(evidence_root(tmp_path)),
            '--out', str(tmp_path / 'out'), '--archive', str(tmp_path / 'archive')])

        assert profiler.main() == 2
        assert '--demand-spec' in json.loads(capsys.readouterr().out)['reason']


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
