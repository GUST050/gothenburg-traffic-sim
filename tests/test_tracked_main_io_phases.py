"""The demand run wrapper must account for its own bookkeeping.

`_tracked_main` copies every product into the immutable run directory, hashes
each copy, re-reads demand_meta.json and assembles the validation report. None
of that was measured; a previous report claimed it was, which was false. These
tests pin the measurement AND the failure semantics it must not disturb.
"""
import json
import sys
from pathlib import Path

import pytest

import build_sumo_demand as bsd
from traffic_sim.confidence import report as validation_report
from traffic_sim.ops import io_phases, runs as run_registry

PHASES = ('demand_main', 'demand_product_listing', 'demand_add_outputs',
          'demand_meta_read', 'demand_validation_report', 'demand_run_finish')


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A demand run wired to temporary files, with main() stubbed out."""
    sumo = tmp_path / 'sumo'
    sumo.mkdir()
    (sumo / 'demand_meta.json').write_text(json.dumps({
        'calibrated_structure': {'structure_flags': ['flag:a']}}))
    (sumo / 'demand_build_spec.json').write_text('{}')
    monkeypatch.setattr(bsd, 'SUMO_DIR', sumo)
    monkeypatch.setattr(run_registry, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(bsd, 'demand_run_products',
                        lambda sumo_dir=sumo: [sumo / 'demand_meta.json',
                                               sumo / 'demand_build_spec.json'])
    monkeypatch.setattr(sys, 'argv', ['build_sumo_demand.py', '--stub'])

    out = tmp_path / 'validation.json'
    out.write_text(json.dumps({'overall': 'warn'}))
    monkeypatch.setattr(validation_report, 'OUT_PATH', out)
    monkeypatch.setattr(validation_report, 'write_report',
                        lambda *a, **k: {'overall': 'warn'})
    return sumo


def _latest(tmp_path):
    pointer = tmp_path / 'runs' / 'latest_demand.json'
    return json.loads(pointer.read_text()) if pointer.is_file() else None


class TestTheDemandRunWrapperMeasuresItself:
    def test_every_bookkeeping_step_is_its_own_phase(self, workspace,
                                                     monkeypatch):
        monkeypatch.setattr(bsd, 'main', lambda: None)
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            bsd._tracked_main()

        phases = collector.report()['phases']
        for name in PHASES:
            assert name in phases, f'{name} was not measured'

    def test_the_failure_path_records_its_own_finish_phase(self, workspace,
                                                           monkeypatch):
        def explode():
            raise RuntimeError('calibration failed')

        monkeypatch.setattr(bsd, 'main', explode)
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            with pytest.raises(RuntimeError, match='calibration failed'):
                bsd._tracked_main()

        phases = collector.report()['phases']
        assert 'demand_run_finish_failed' in phases
        assert 'demand_run_finish' not in phases

    def test_measuring_leaves_no_observer_behind_after_a_failure(
            self, workspace, monkeypatch):
        monkeypatch.setattr(bsd, 'main',
                            lambda: (_ for _ in ()).throw(RuntimeError('x')))

        with pytest.raises(RuntimeError):
            with io_phases.observe(io_phases.PhaseCollector()):
                bsd._tracked_main()

        assert io_phases.current_collector() is None

    def test_production_installs_no_collector(self, workspace, monkeypatch):
        monkeypatch.setattr(bsd, 'main', lambda: None)

        bsd._tracked_main()

        assert io_phases.current_collector() is None


class TestMeasurementChangesNoRunSemantics:
    def test_a_successful_run_records_the_same_manifest_either_way(
            self, workspace, monkeypatch, tmp_path):
        monkeypatch.setattr(bsd, 'main', lambda: None)

        bsd._tracked_main()
        plain = sorted((tmp_path / 'runs').glob('*/manifest.json'))
        plain_manifest = json.loads(plain[-1].read_text())
        plain_pointer = _latest(tmp_path)

        with io_phases.observe(io_phases.PhaseCollector()):
            bsd._tracked_main()
        measured = sorted((tmp_path / 'runs').glob('*/manifest.json'))
        measured_manifest = json.loads(measured[-1].read_text())

        def comparable(manifest):
            return {
                'status': manifest['status'],
                'outputs': [{k: v for k, v in entry.items()
                             if k not in ('source_path',)}
                            for entry in manifest['outputs']],
                'recorded': manifest.get('recorded'),
            }

        assert comparable(measured_manifest) == comparable(plain_manifest)
        assert plain_pointer is not None
        assert _latest(tmp_path)['run_id'] != plain_pointer['run_id']

    def test_a_failed_run_keeps_its_status_and_leaves_no_latest_pointer(
            self, workspace, monkeypatch, tmp_path):
        monkeypatch.setattr(bsd, 'main',
                            lambda: (_ for _ in ()).throw(ValueError('no')))

        with io_phases.observe(io_phases.PhaseCollector()):
            with pytest.raises(ValueError):
                bsd._tracked_main()

        manifest = json.loads(
            sorted((tmp_path / 'runs').glob('*/manifest.json'))[-1].read_text())
        assert manifest['status'] == 'failed'
        assert manifest['error'].startswith('ValueError')
        assert _latest(tmp_path) is None

    def test_a_broken_validation_report_still_does_not_fail_the_build(
            self, workspace, monkeypatch, tmp_path):
        monkeypatch.setattr(bsd, 'main', lambda: None)
        monkeypatch.setattr(
            validation_report, 'write_report',
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('report broke')))

        with io_phases.observe(io_phases.PhaseCollector()):
            bsd._tracked_main()

        manifest = json.loads(
            sorted((tmp_path / 'runs').glob('*/manifest.json'))[-1].read_text())
        assert manifest['status'] == 'succeeded'
