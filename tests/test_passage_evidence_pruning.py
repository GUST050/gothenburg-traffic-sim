"""Pruning keeps every durable record and every hash verifiable."""
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from traffic_sim.demand import automatic_passage as ap


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
    assert workers == [3]
    assert not list(root.rglob('*.prune-tmp'))
