"""Step 4: the production I/O path must account for itself, and change nothing.

Every phase here is diagnostic. The tests that matter most are the negative
ones: measuring must not alter a byte of the source evidence, nor any of the
artifacts a build publishes, nor leave an observer behind.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_profile_passage_replay import evidence_root
from traffic_sim.demand import automatic_passage as auto
from traffic_sim.ops import io_phases, runs as run_registry
from tools import trial_dynamic_passage as trial

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tree(root):
    return sorted((str(p.relative_to(root)),
                   hashlib.sha256(p.read_bytes()).hexdigest())
                  for p in Path(root).rglob('*') if p.is_file())


class TestLoadingSourceEvidenceAccountsForItsReads:
    def test_json_and_xml_reads_are_separate_phases(self, tmp_path):
        source = evidence_root(tmp_path)
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            trial.load_verified_source(source)

        phases = collector.report()['phases']
        for name in ('source_manifest_json', 'source_input_hash',
                     'source_routes_xml', 'source_agents_json',
                     'source_trace_hash', 'source_trace_xml'):
            assert name in phases, f'{name} was not measured'

    def test_reading_the_evidence_does_not_touch_it(self, tmp_path):
        source = evidence_root(tmp_path)
        before = _tree(source)

        collector = io_phases.PhaseCollector()
        with io_phases.observe(collector):
            trial.load_verified_source(source)

        assert _tree(source) == before

    def test_the_measured_load_returns_the_same_system_as_the_plain_one(
            self, tmp_path):
        source = evidence_root(tmp_path)

        plain = trial.load_verified_source(source)
        collector = io_phases.PhaseCollector()
        with io_phases.observe(collector):
            measured = trial.load_verified_source(source)

        assert [o.option_id for o in measured.options] == \
            [o.option_id for o in plain.options]
        assert measured.groups == plain.groups
        assert measured.metadata == plain.metadata


class TestStagingSelectionAccountsForItsWrites:
    def _staged(self, tmp_path, *, measured):
        source = evidence_root(tmp_path)
        verified = trial.load_verified_source(source)
        stage = tmp_path / ('measured' if measured else 'plain')
        if not measured:
            auto._stage_selection(source / 'input', list(verified.options), stage)
            return stage, None
        collector = io_phases.PhaseCollector()
        with io_phases.observe(collector):
            auto._stage_selection(source / 'input', list(verified.options), stage)
        return stage, collector.report()

    def test_transform_write_and_reformat_are_separate_phases(self, tmp_path):
        _stage, report = self._staged(tmp_path, measured=True)

        for name in ('selection_source_routes_xml', 'selection_source_agents_json',
                     'selection_transform', 'selection_write',
                     'stage_validate', 'stage_reformat_xml'):
            assert name in report['phases'], f'{name} was not measured'

    def test_measuring_publishes_exactly_the_same_artifacts(self, tmp_path):
        plain, _ = self._staged(tmp_path / 'a', measured=False)
        measured, _ = self._staged(tmp_path / 'b', measured=True)

        assert _tree(measured) == _tree(plain)


class TestRunRegistryAccountsForItsCopies:
    def test_copy_and_target_verification_are_separate_phases(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_registry, 'RUNS_DIR', tmp_path / 'runs')
        run = run_registry.start_run('step4-test', {'input': 'x'})
        product = tmp_path / 'product.json'
        product.write_text(json.dumps({'a': 1}))

        collector = io_phases.PhaseCollector()
        with io_phases.observe(collector):
            run.add_output(product)

        phases = collector.report()['phases']
        assert 'run_output_copy' in phases
        assert 'run_output_verify' in phases
        assert phases['run_output_copy']['bytes']['written'] == \
            product.stat().st_size

    def test_the_recorded_manifest_is_unchanged_by_measurement(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_registry, 'RUNS_DIR', tmp_path / 'runs')
        product = tmp_path / 'product.json'
        product.write_text(json.dumps({'a': 1}))

        plain_run = run_registry.start_run('step4-test', {'input': 'x'})
        plain_run.add_output(product)
        collector = io_phases.PhaseCollector()
        measured_run = run_registry.start_run('step4-test', {'input': 'x'})
        with io_phases.observe(collector):
            measured_run.add_output(product)

        def outputs(run):
            return [{k: v for k, v in entry.items() if k != 'source_path'}
                    for entry in run.manifest['outputs']]

        assert outputs(measured_run) == outputs(plain_run)


class TestTheProfilerStartsWithoutAPathWorkaround:
    """Run as a script, sys.path[0] is tools/, so `import demand` failed.

    The real environment is kept and only PYTHONPATH removed: that single
    variable is the workaround under test, and stripping the rest would test
    whether numpy is installed instead.
    """

    @staticmethod
    def _help():
        import os
        environment = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        return subprocess.run(
            [sys.executable, 'tools/profile_passage_replay.py', '--help'],
            cwd=REPO_ROOT, env=environment, capture_output=True, text=True)

    def test_help_works_from_the_repository_root_without_pythonpath(self):
        result = self._help()

        assert result.returncode == 0, result.stderr
        assert '--source' in result.stdout

    def test_the_measurement_flag_is_offered(self):
        assert '--io-phases' in self._help().stdout


class TestTheProfilerReportsIoPhases:
    def test_io_measurement_is_absent_unless_it_is_asked_for(self, tmp_path):
        from tools import profile_passage_replay as profiler

        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out')

        assert 'io_measurement' not in report

    def test_asking_for_it_reports_phases_a_ranking_and_its_basis(
            self, tmp_path):
        from tools import profile_passage_replay as profiler

        report = profiler.replay(evidence_root(tmp_path), tmp_path / 'out',
                                 io_phase_measurement=True)

        measurement = report['io_measurement']
        assert measurement['phases']
        assert measurement['ranking']
        assert 'exclusive_s null' in measurement['basis']
        assert profiler.io_phases.current_collector() is None

    def test_a_profile_separates_the_cold_repeat_from_the_reused_ones(
            self, tmp_path):
        from tools import profile_passage_replay as profiler

        summary = profiler.profile(evidence_root(tmp_path), tmp_path / 'out',
                                   repeats=3, io_phase_measurement=True)

        assert summary['first_repeat_io_phase_s']
        assert summary['reused_process_io_phase_s']
        for name, entry in summary['first_repeat_io_phase_s'].items():
            assert 'wall_s' in entry and 'bytes' in entry
        assert summary['io_phase_ranking'][0]['share_percent'] >= \
            summary['io_phase_ranking'][-1]['share_percent']
