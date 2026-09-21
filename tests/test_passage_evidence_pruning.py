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
from traffic_sim.demand import automatic_passage as ap


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


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def evidence(tmp_path):
    """One finished evidence root with the shapes a real arm produces."""
    root = tmp_path / 'automatic-passage-test'
    payload = b'<routes>\n<vehicle id="1"/>\n</routes>\n'
    for arm in ('q50', '_v1'):
        work = root / arm
        for name in ('input', 'candidate', 'candidate-repair-1',
                     'evidence/learning-0-arm-1000', 'before-4000', 'after-4000'):
            (work / name).mkdir(parents=True)
            (work / name / 'edge.xml').write_bytes(payload)
        (work / 'result.json').write_text(json.dumps({'policy': 'x'}))
        (work / 'report.json').write_text('{}')
        for solver, status in (('solver-candidate', 'optimal'),
                               ('solver-candidate-repair-1', 'time_limit')):
            (work / solver).mkdir()
            (work / solver / 'request.npz').write_bytes(b'0' * 2048)
            (work / solver / 'state.json').write_text(json.dumps({'status': status}))
    (root / 'source_reports.json').write_text('{}')
    for index in range(4):
        (root / f'original-{index}').write_bytes(payload)
    return root, payload


def test_durable_records_survive(evidence):
    root, _ = evidence
    ap.prune_evidence(root)
    for arm in ('q50', '_v1'):
        assert (root / arm / 'result.json').is_file()
        assert (root / arm / 'report.json').is_file()


def test_measurements_are_compressed_and_still_hash_to_the_same_bytes(evidence):
    root, payload = evidence
    ap.prune_evidence(root)
    for arm in ('q50', '_v1'):
        for name in ('input', 'evidence/learning-0-arm-1000', 'before-4000', 'after-4000'):
            plain = root / arm / name / 'edge.xml'
            packed = plain.with_suffix('.xml.gz')
            assert not plain.exists(), f'{plain} should be compressed'
            assert packed.is_file()
            with gzip.open(packed, 'rb') as handle:
                assert _digest(handle.read()) == _digest(payload)


def test_consumed_copies_and_rollback_backups_are_removed(evidence):
    root, _ = evidence
    ap.prune_evidence(root)
    assert not (root / 'source_reports.json').exists()
    assert not list(root.glob('original-*'))
    for arm in ('q50', '_v1'):
        assert not (root / arm / 'candidate').exists()
        assert not (root / arm / 'candidate-repair-1').exists()


def test_every_replay_request_survives_pruning(evidence):
    """test_automatic_passage pins a replayable request even after a cache hit,
    so pruning must not treat a finished solve as a request it may discard."""
    root, _ = evidence
    ap.prune_evidence(root)
    for arm in ('q50', '_v1'):
        for solver in ('solver-candidate', 'solver-candidate-repair-1'):
            assert (root / arm / solver / 'request.npz').is_file()
            assert (root / arm / solver / 'state.json').is_file()


def test_keep_all_changes_nothing(evidence):
    root, _ = evidence
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob('*'))
    ap.prune_evidence(root, keep_all=True)
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob('*')) == before


def test_a_corrupt_gzip_round_trip_fails_closed(evidence, monkeypatch):
    root, _ = evidence
    monkeypatch.setattr(ap, '_gzip_verified', lambda source: False)
    with pytest.raises(ValueError):
        ap.prune_evidence(root)
    assert (root / 'q50' / 'input' / 'edge.xml').is_file()


def test_a_prune_failure_never_fails_a_committed_build(evidence, monkeypatch, capsys):
    """The routes are already swapped in; disk reclaim must not undo that."""
    root, _ = evidence
    monkeypatch.setattr(ap, '_gzip_verified', lambda source: False)
    try:
        ap.prune_evidence(root)
    except ValueError as exc:
        print(f'  passage evidence left uncompressed: {exc}')
    assert 'left uncompressed' in capsys.readouterr().out
    assert (root / 'q50' / 'result.json').is_file()


def test_evidence_compression_uses_bounded_cpu_and_closes_output(tmp_path, monkeypatch):
    """Evidence retention must not default to costly level-nine compression."""
    source = tmp_path / 'evidence.xml'
    payload = b'<vehicle route="a b c"/>\n' * 1000
    source.write_bytes(payload)
    real_gzip = gzip.GzipFile
    calls = []
    def tracked(*args, **kwargs):
        if kwargs.get('mode') == 'wb':
            calls.append((kwargs.get('compresslevel', 9), kwargs.get('fileobj')))
        return real_gzip(*args, **kwargs)
    monkeypatch.setattr(gzip, 'GzipFile', tracked)
    assert ap._gzip_verified(source)
    assert calls and calls[0][0] <= 3
    assert calls[0][1].closed
    with gzip.open(source.with_name('evidence.xml.gz'), 'rb') as handle:
        assert handle.read() == payload
    assert source.read_bytes() == payload


def test_retention_compresses_independent_files_with_bounded_workers(evidence, monkeypatch):
    root, _ = evidence
    original = ap.ThreadPoolExecutor
    workers = []
    def pool(*args, **kwargs):
        workers.append(kwargs['max_workers'])
        return original(*args, **kwargs)
    monkeypatch.setattr(ap, 'ThreadPoolExecutor', pool)
    monkeypatch.setattr(ap.os, 'cpu_count', lambda: 12)
    ap.prune_evidence(root)
    assert workers == [ap.RETENTION_MAX_WORKERS]
    assert not list(root.rglob('*.prune-tmp'))
