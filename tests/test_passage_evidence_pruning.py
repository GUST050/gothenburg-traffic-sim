"""Retention: compress evidence, prove it survived, and account for the cost.

The plan names retention as step 4's largest suspected cost. Before anything
is made faster, two things must be true and were previously untested here:
the safety properties (an interrupted compression publishes nothing, a
corrupt target is refused, missing evidence fails structurally) and the
measurement (each byte-moving step reported separately, concurrency reported
as concurrency).
"""
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from traffic_sim.demand import automatic_passage as auto
from traffic_sim.ops import io_phases


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _evidence(root, *, arms=('q50',), files=2, size=4096):
    """An evidence root shaped like a finished passage run."""
    root = Path(root)
    for arm in arms:
        for index in range(files):
            path = root / arm / 'evidence' / f'trace{index}.xml'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('<routes>' + 'x' * size + '</routes>')
        stage = root / arm / 'candidate'
        stage.mkdir(parents=True, exist_ok=True)
        (stage / 'calibrated.rou.xml').write_text('<routes/>')
    (root / 'source_reports.json').write_text(json.dumps({'kept': False}))
    (root / 'original-0').write_text('rollback')
    return root


def _xml_files(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob('*.xml'))


class TestRetentionKeepsItsSafetyProperties:
    def test_every_compressed_file_round_trips_before_its_original_goes(
            self, tmp_path):
        root = _evidence(tmp_path / 'evidence')
        originals = {p: _sha(p) for p in Path(root).rglob('evidence/*.xml')}

        auto.prune_evidence(root)

        for path, digest in originals.items():
            packed = path.with_name(path.name + '.gz')
            assert packed.is_file() and not path.exists()
            with gzip.open(packed, 'rb') as handle:
                assert hashlib.sha256(handle.read()).hexdigest() == digest

    def test_an_interrupted_compression_publishes_nothing_and_leaves_no_temp(
            self, tmp_path, monkeypatch):
        root = _evidence(tmp_path / 'evidence', files=1)
        target = next(Path(root).rglob('evidence/*.xml'))

        def explode(*args, **kwargs):
            raise KeyboardInterrupt('cancelled mid-compression')

        monkeypatch.setattr(auto.shutil, 'copyfileobj', explode)
        with pytest.raises(KeyboardInterrupt):
            auto.prune_evidence(root)

        assert target.is_file(), 'the original must survive an interruption'
        assert not target.with_name(target.name + '.gz').exists()
        assert not list(Path(root).rglob('*.prune-tmp'))

    def test_a_target_that_does_not_round_trip_is_refused_and_keeps_the_source(
            self, tmp_path, monkeypatch):
        root = _evidence(tmp_path / 'evidence', files=1)
        target = next(Path(root).rglob('evidence/*.xml'))
        monkeypatch.setattr(auto, 'sha256_file', lambda path: 'not-the-digest')

        with pytest.raises(ValueError, match='did not survive compression'):
            auto.prune_evidence(root)

        assert target.is_file()
        assert not list(Path(root).rglob('*.prune-tmp'))

    def test_a_missing_evidence_root_fails_structurally(self, tmp_path):
        with pytest.raises(OSError):
            auto.prune_evidence(tmp_path / 'was-never-written')

    def test_keeping_evidence_is_still_honoured(self, tmp_path):
        root = _evidence(tmp_path / 'evidence')
        before = _xml_files(root)

        auto.prune_evidence(root, keep_all=True)

        assert _xml_files(root) == before


class TestRetentionAccountsForItsBytes:
    def _pruned_report(self, root):
        collector = io_phases.PhaseCollector()
        with io_phases.observe(collector):
            with io_phases.phase('retention'):
                auto.prune_evidence(root)
        return collector.report()

    def test_each_byte_moving_step_is_a_separate_phase(self, tmp_path):
        root = _evidence(tmp_path / 'evidence', files=3)

        phases = self._pruned_report(root)['phases']

        for name in ('retention_inventory', 'retention_compress',
                     'gzip_compress', 'gzip_source_read', 'gzip_target_write',
                     'gzip_source_hash', 'gzip_target_verify',
                     'gzip_publish', 'retention_cleanup'):
            assert name in phases, f'{name} was not measured'

    def test_the_concurrent_parent_does_not_add_overlapping_children(
            self, tmp_path):
        root = _evidence(tmp_path / 'evidence', files=3, size=200_000)

        entry = self._pruned_report(root)['phases']['retention_compress']

        assert entry['concurrent'] is True
        assert entry['exclusive_s'] is None
        assert entry['children_max_s'] <= entry['children_sum_s']

    def test_bytes_read_written_and_verified_are_reported(self, tmp_path):
        root = _evidence(tmp_path / 'evidence', files=2, size=10_000)
        expected = sum(p.stat().st_size
                       for p in Path(root).rglob('evidence/*.xml'))

        phases = self._pruned_report(root)['phases']

        assert phases['gzip_source_read']['bytes']['read'] == expected
        assert phases['gzip_target_write']['bytes']['written'] > 0
        assert phases['gzip_target_verify']['bytes']['verified'] == expected

    def test_measuring_does_not_change_what_retention_produces(self, tmp_path):
        plain = _evidence(tmp_path / 'plain')
        measured = _evidence(tmp_path / 'measured')

        auto.prune_evidence(plain)
        self._pruned_report(measured)

        def listing(root):
            return sorted((str(p.relative_to(root)), _sha(p))
                          for p in Path(root).rglob('*') if p.is_file())

        assert listing(measured) == listing(plain)

    def test_production_installs_no_collector(self, tmp_path):
        root = _evidence(tmp_path / 'evidence', files=1)

        auto.prune_evidence(root)

        assert io_phases.current_collector() is None
