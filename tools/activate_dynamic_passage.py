"""Stage a source-bound dynamic demand, test baseline/closure, then activate locally.

Activation is explicit (--activate), applies only to this demand window, and
preserves the existing scenario publication and exact-output diagnostic gates.
It does not change the default demand builder or qualify unseen demand dates.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import numpy as np

from demand.priors import build_interval_constraints, opposite_direction_bounds
from demand.structure import calibrated_agent_summary, calibrated_structure_report
from traffic_sim.core.fingerprint import make_fingerprint, sha256_file
from traffic_sim.demand.build_lock import demand_build_lock
from traffic_sim.demand.provenance import _validate_variant
from traffic_sim.experimental import dynamic_assignment as dynamic
from traffic_sim.simulation.runtime import sumo_home
from traffic_sim.simulation.workspace import WorkspaceLock
from tools.trial_dynamic_passage import load_source, materialize_selection

ROOT = Path(__file__).resolve().parents[1]


def source_relaxed_quarters(meta: dict, quarters: int) -> set[int]:
    """Recover only the source's explicit no-bounds intervals; never infer more."""
    fit = meta['pfe_fit_variants']['edge_shares']
    summary = fit.get('relaxation_summary', {})
    if set(summary) - {'clean', 'no_bounds_tol1'}:
        raise ValueError('unsupported source relaxation contract')
    relaxed = {row['quarter'] for row in fit.get('relaxed_bound_violations', [])}
    if any(isinstance(q, bool) or not isinstance(q, int) or not 0 <= q < quarters for q in relaxed) \
            or len(relaxed) != summary.get('no_bounds_tol1', 0) \
            or sum(summary.values()) != quarters:
        raise ValueError('source relaxation evidence is incomplete')
    return relaxed


def retained_bounds(meta: dict, root: Path) -> tuple[list[dict], dict]:
    """Rebuild the same structural/opposite-direction bounds from bound artifacts."""
    for label, relative in [('bounds', 'web/data/observability_bounds.json'),
                            ('direction_split', 'sumo/direction_split.json')]:
        expected = meta['build_fingerprint']['artifacts'][label]['sha256']
        if not expected or sha256_file(root / relative) != expected:
            raise ValueError(f'source {label} changed; activation requires a fresh trial')
    if sha256_file(root / 'data_in/sensors.json') != meta['sensor_contract']['registry_sha256']:
        raise ValueError('sensor registry changed')
    quarters = meta['n_intervals']
    relaxed = source_relaxed_quarters(meta, quarters)
    bounds_data = json.loads((root / 'web/data/observability_bounds.json').read_text())
    targets = meta['sensor_targets']['variants']['edge_shares']
    opposite = opposite_direction_bounds(targets, quarters, 0,
                                         registry_path=root / 'data_in/sensors.json')
    _, _, complete = build_interval_constraints(quarters, 0, bounds_data, {}, {}, {},
                                                opposite_bounds=opposite)
    retained = [{} if q in relaxed else row for q, row in enumerate(complete)]
    return retained, {'source_relaxed_quarters': sorted(relaxed),
                      'retained_constraints': sum(len(row) for row in retained),
                      'new_relaxations': 0}


def validate_closure(payload: dict) -> None:
    """A closure may change counts; it must still forbid entry and pass health."""
    scenario = payload.get('scenario', {})
    if scenario.get('closure_integrity') != 'verified_clean' \
            or scenario.get('active_closure_edge_entries') != 0:
        raise ValueError('closure integrity failed')
    if payload.get('seed_health_flags'):
        raise ValueError('closure health failed')


def _run_scenario(sumo_dir: Path, output: Path, name: str, closure: str | None) -> None:
    # Separate processes prevent module-level paths leaking into another run.
    script = (
        'import sys; from pathlib import Path; import run_scenario as r; '
        'r.SUMO_DIR=Path(sys.argv[1]); r.NET_PATH=r.SUMO_DIR/"net.net.xml"; '
        'r.OUT_DIR=Path(sys.argv[2]); '
        'sys.argv=["run_scenario.py"]+sys.argv[3:]+["--out-dir",str(r.OUT_DIR)]; r.main()')
    command = [sys.executable, '-c', script, str(sumo_dir.resolve()), str(output.resolve()),
               '--name', name, '--seeds', '3', '--seed-workers', '3',
               '--trajectories', '--keep-scratch']
    if closure:
        command += ['--close', closure]
    with (output.parent / f'{name}.log').open('w') as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=300)


def prepare(source_trial: Path, live_sumo: Path, output: Path, closure: str) -> dict:
    """Produce a fully reviewable local release before touching live artifacts."""
    options, groups, source_meta = load_source(source_trial)
    if source_meta.get('n_variants') != 1 or source_meta.get('days') != 1 \
            or source_meta.get('n_intervals') != 96 or source_meta.get('begin') != '00:00':
        raise ValueError('activation currently requires a one-day q50-only source')
    expected_inputs = json.loads((source_trial / 'report.json').read_text())['input_sha256']
    for name, expected in expected_inputs.items():
        if sha256_file(live_sumo / name) != expected:
            raise ValueError(f'active input differs from trial: {name}')
    bounds, bound_proof = retained_bounds(source_meta, ROOT)
    base_system = dynamic.build_passage_system(options, list(
        source_meta['sensor_targets']['variants']['edge_shares']), 96)
    matrix, low, high = dynamic.departure_bound_constraints(base_system, bounds)
    source_values = matrix @ np.ones(len(options))
    if np.any(source_values < low) or np.any(source_values > high):
        raise ValueError('reconstructed retained bounds disagree with source demand')
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source_trial / 'input', output / 'source')
    for path in (Path(__file__), Path(dynamic.__file__)):
        shutil.copy2(path, output / path.name)
    expanded = dynamic.expand_departure_support(options, [-900, -600, -300, 0, 300, 600, 900],
                                                begin_s=0, end_s=86400, guard_s=60)
    targets = {e: [int(round(v)) for v in series] for e, series in
               source_meta['sensor_targets']['variants']['edge_shares'].items()}
    system = dynamic.build_passage_system(expanded, list(targets), 96)
    fit = dynamic.fit_integer_flows(system, targets, groups, departure_bounds=bounds,
                                    time_limit_s=60)
    selected = [o for o, n in zip(expanded, fit.counts) if n == 1]
    selected_groups = Counter(o.group for o in selected)
    if selected_groups != groups or len(selected) != len(options):
        raise ValueError('selected OD/purpose totals differ')
    stage = output / 'sumo'
    materialize_selection(output / 'source/calibrated.rou.xml',
                          output / 'source/calibrated.agents.json', selected, stage)
    vehicles = _validate_variant(stage / 'calibrated.rou.xml', stage / 'calibrated.agents.json')
    for name in ('net.net.xml', 'network_metadata.json', 'plain.edg.xml'):
        if (live_sumo / name).exists():
            shutil.copy2(live_sumo / name, stage / name)
    meta = copy.deepcopy(source_meta)
    meta['source_calibration'] = {'build_id': source_meta['build_id'],
        'pfe_fit': source_meta['pfe_fit'], 'pfe_fit_variants': source_meta['pfe_fit_variants']}
    meta['passage_calibration'] = {
        'schema_version': 1, 'method': 'dynamic_route_time_integer_flow_v1',
        'scope': 'current_demand_window', 'source_build_id': source_meta['build_id'],
        'source_trial': str(source_trial.resolve()), 'retained_bounds': bound_proof,
        'measurement_basis': 'sensor_edge_entry_quarter',
        'prediction_exact': True, 'raw_exactness': 'see_sensor_audit',
        'deployment_status': 'user_enabled_local',
        'generalization': 'other_dates_and_sensors_not_validated',
    }
    fit_report = {'geh_pct': 100., 'infeasible_intervals': 0, 'vehicles': vehicles,
        'integer_sensor_constraints': len(targets)*96, 'integer_sensor_exact': len(targets)*96,
        'integer_sensor_exact_pct': 100., 'integer_sensor_max_abs_error': 0.,
        'integer_sensor_sum_abs_error': 0., 'integer_sensor_target_rule': 'int(round(target))',
        'measurement_basis': 'predicted_sensor_passage_quarter',
        'bound_violations': [], 'unserviceable_edges': [],
        'relaxation_summary': source_meta['pfe_fit_variants']['edge_shares']['relaxation_summary'],
        'relaxed_bound_quarters': bound_proof['source_relaxed_quarters']}
    meta['pfe_fit'] = fit_report
    meta['pfe_fit_variants'] = {'edge_shares': fit_report}
    meta['agent_demand'] = calibrated_agent_summary(stage / 'calibrated.rou.xml', 96)
    structure = calibrated_structure_report(stage / 'calibrated.rou.xml',
                                             pool_path=live_sumo / 'candidates.rou.xml')
    if structure is None:
        raise ValueError('new demand lacks structural evidence')
    source_flags = {str(v).split(':', 1)[0] for v in source_meta['calibrated_structure']['structure_flags']}
    new_flags = {str(v).split(':', 1)[0] for v in structure['structure_flags']}
    if new_flags - source_flags:
        raise ValueError(f'new structural warning classes: {new_flags - source_flags}')
    meta['calibrated_structure'] = structure
    meta['candidate_provenance'] = {'schema_version': 1, 'status': 'pass',
        'mode': 'source_bound_dynamic_flow', 'vehicles': vehicles,
        'source_provenance': source_meta['candidate_provenance'],
        'source_routes_sha256': expected_inputs['calibrated.rou.xml'],
        'source_agents_sha256': expected_inputs['calibrated.agents.json'],
        'selection_sha256': sha256_file(stage / 'selection.json'),
        'variants': [{'route': 'calibrated.rou.xml', 'agents': 'calibrated.agents.json',
                      'vehicles': vehicles}]}
    meta.pop('build_id')
    meta.pop('build_fingerprint')
    meta['build_fingerprint'] = make_fingerprint(
        contract={k: v for k, v in meta.items() if k != 'timings_s'},
        artifacts={'network': stage / 'net.net.xml', 'calibrated_q50': stage / 'calibrated.rou.xml',
                   'calibrated_q50_agents': stage / 'calibrated.agents.json',
                   'source_metadata': output / 'source/demand_meta.json',
                   'selection': stage / 'selection.json'},
        source_files={'activation': Path(__file__), 'dynamic_assignment': Path(dynamic.__file__)},
        sumo_home=sumo_home())
    meta['build_id'] = meta['build_fingerprint']['build_id']
    (stage / 'demand_meta.json').write_text(json.dumps(meta, indent=2)+'\n')
    scenarios = output / 'scenarios'
    scenarios.mkdir()
    _run_scenario(stage, scenarios, 'baseline', None)
    _run_scenario(stage, scenarios, 'dynamic_closure_test', closure)
    import serve
    passed, reason = serve.validate_staged_scenarios(scenarios, stage / 'demand_meta.json')
    if not passed:
        raise ValueError(f'unchanged publication gate rejected staged demand: {reason}')
    close_payload = json.loads((scenarios / 'dynamic_closure_test.json').read_text())
    validate_closure(close_payload)
    report = {'status': 'ready', 'source_input_sha256': expected_inputs,
        'build_id': meta['build_id'], 'closure_edge': closure, 'vehicles': vehicles,
        'bounds': bound_proof, 'publication_gate': {'passed': passed, 'reason': reason},
        'closure': close_payload['scenario'], 'source_structure_flags': source_flags,
        'new_structure_flags': new_flags}
    # Sets are only used for comparison, never persisted as opaque values.
    report['source_structure_flags'] = sorted(source_flags)
    report['new_structure_flags'] = sorted(new_flags)
    staged_files = [stage / name for name in (
        'calibrated.rou.xml', 'calibrated.agents.json', 'selection.json', 'demand_meta.json')]
    staged_files.extend(scenarios.glob('*.json'))
    report['staged_artifacts'] = {
        str(path.relative_to(output)): sha256_file(path) for path in staged_files}
    (output / 'activation.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        with source.open('rb') as original:
            shutil.copyfileobj(original, handle)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def publish(output: Path, live_sumo: Path, live_scenarios: Path) -> None:
    """Copy validated files with rollback; switch scenario index last, delete nothing."""
    report = json.loads((output / 'activation.json').read_text())
    if report['status'] != 'ready':
        raise ValueError('activation was not staged successfully')
    for name, expected in report['source_input_sha256'].items():
        if sha256_file(live_sumo / name) != expected:
            raise ValueError('active demand changed before activation')
    moves = [(output / 'sumo' / name, live_sumo / name)
             for name in ('calibrated.rou.xml', 'calibrated.agents.json', 'selection.json', 'demand_meta.json')]
    moves += [(path, live_scenarios / path.name) for path in sorted((output / 'scenarios').glob('*.json'))
              if path.name != 'index.json']
    moves.append((output / 'scenarios/index.json', live_scenarios / 'index.json'))
    sealed = report.get('staged_artifacts', {})
    actual = {str(source.relative_to(output)): sha256_file(source) for source, _ in moves}
    if not sealed or sealed != actual or any(value is None for value in actual.values()):
        raise ValueError('staged artifacts changed after validation')
    backup = output / 'rollback'
    backup.mkdir(exist_ok=False)
    originals = {}
    for index, (_, target) in enumerate(moves):
        if target.exists():
            saved = backup / str(index)
            shutil.copy2(target, saved)
            originals[target] = saved
    (backup / 'manifest.json').write_text(json.dumps({str(k): str(v) for k, v in originals.items()}, indent=2))
    try:
        for source, target in moves:
            atomic_copy(source, target)
    except BaseException:
        for target, saved in originals.items():
            atomic_copy(saved, target)
        raise
    report['status'] = 'active'
    report['active_artifacts'] = {str(target): sha256_file(target) for _, target in moves}
    (output / 'activation.json').write_text(json.dumps(report, indent=2)+'\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-trial', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--close-edge', required=True)
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args()
    with WorkspaceLock('dynamic passage activation'), demand_build_lock():
        report = prepare(args.source_trial, ROOT / 'sumo', args.out, args.close_edge)
        if args.activate:
            publish(args.out, ROOT / 'sumo', ROOT / 'web/data/scenarios')
        print(json.dumps({'build_id': report['build_id'], 'activated': args.activate}))


if __name__ == '__main__':
    main()
