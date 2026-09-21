"""Activation must preserve provenance and existing publication/closure gates."""
import json
import pytest
from tools import activate_dynamic_passage as activation


def test_closure_gate_rejects_entries_even_when_summary_claims_clean():
    payload = {'scenario': {'closure_integrity': 'verified_clean',
                            'active_closure_edge_entries': 1}, 'seed_health_flags': []}
    with pytest.raises(ValueError, match='closure'):
        activation.validate_closure(payload)


def test_closure_gate_rejects_bad_health():
    payload = {'scenario': {'closure_integrity': 'verified_clean',
                            'active_closure_edge_entries': 0},
               'seed_health_flags': ['unfinished']}
    with pytest.raises(ValueError, match='health'):
        activation.validate_closure(payload)


def test_closure_gate_requires_measured_integrity():
    with pytest.raises(ValueError, match='closure'):
        activation.validate_closure({'scenario': {}, 'seed_health_flags': []})


def test_source_quarter_relaxations_are_not_extended():
    metadata = {'pfe_fit_variants': {'edge_shares': {
        'relaxation_summary': {'clean': 1, 'no_bounds_tol1': 1},
        'relaxed_bound_violations': [{'quarter': 1}]}}}
    assert activation.source_relaxed_quarters(metadata, 2) == {1}
    metadata['pfe_fit_variants']['edge_shares']['relaxation_summary']['no_bounds_tol1'] = 2
    with pytest.raises(ValueError, match='relaxation'):
        activation.source_relaxed_quarters(metadata, 2)


def staged_files(tmp_path):
    from traffic_sim.core.fingerprint import sha256_file
    output, live_sumo, live_web = (tmp_path / 'stage', tmp_path / 'sumo', tmp_path / 'web')
    for directory in (output / 'sumo', output / 'scenarios', live_sumo, live_web):
        directory.mkdir(parents=True)
    for name in ('calibrated.rou.xml', 'calibrated.agents.json', 'selection.json', 'demand_meta.json'):
        (output / 'sumo' / name).write_text('new ' + name)
        (live_sumo / name).write_text('old ' + name)
    for name in ('baseline.json', 'index.json'):
        (output / 'scenarios' / name).write_text('new ' + name)
        (live_web / name).write_text('old ' + name)
    (live_web / 'old_closure.json').write_text('preserve historical evidence')
    report = {'status': 'ready', 'source_input_sha256': {
        'calibrated.rou.xml': sha256_file(live_sumo / 'calibrated.rou.xml')},
        'staged_artifacts': {str(p.relative_to(output)): sha256_file(p)
            for folder in (output / 'sumo', output / 'scenarios') for p in folder.iterdir()}}
    (output / 'activation.json').write_text(json.dumps(report))
    return output, live_sumo, live_web


def test_activation_rejects_tampered_staging_before_any_live_write(tmp_path):
    output, live_sumo, live_web = staged_files(tmp_path)
    (output / 'sumo/calibrated.rou.xml').write_text('tampered')
    with pytest.raises(ValueError, match='staged'):
        activation.publish(output, live_sumo, live_web)
    assert (live_sumo / 'calibrated.rou.xml').read_text() == 'old calibrated.rou.xml'
    assert not (output / 'rollback').exists()


def test_activation_rolls_back_failed_switch(tmp_path, monkeypatch):
    output, live_sumo, live_web = staged_files(tmp_path)
    original = activation.atomic_copy
    calls = 0

    def fail_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError('injected write failure')
        original(source, destination)

    monkeypatch.setattr(activation, 'atomic_copy', fail_once)
    with pytest.raises(OSError, match='injected'):
        activation.publish(output, live_sumo, live_web)
    assert (live_sumo / 'calibrated.rou.xml').read_text() == 'old calibrated.rou.xml'
    assert (live_web / 'index.json').read_text() == 'old index.json'


def test_activation_switches_index_last_and_preserves_history(tmp_path, monkeypatch):
    output, live_sumo, live_web = staged_files(tmp_path)
    original = activation.atomic_copy
    written = []

    def record(source, destination):
        original(source, destination)
        written.append(destination)

    monkeypatch.setattr(activation, 'atomic_copy', record)
    activation.publish(output, live_sumo, live_web)
    assert written[-1] == live_web / 'index.json'
    assert (live_web / 'old_closure.json').read_text() == 'preserve historical evidence'
    assert json.loads((output / 'activation.json').read_text())['status'] == 'active'
