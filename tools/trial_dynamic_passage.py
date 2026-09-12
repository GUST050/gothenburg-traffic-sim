"""Reproducible dynamic passage fit from frozen, source-bound SUMO traces.

python3 -m tools.trial_dynamic_passage --source-trial runs/<frozen-trial> --out runs/<new>
Outputs are isolated experiments, never demand archives or published scenarios.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET

from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.experimental import dynamic_assignment as dynamic
from traffic_sim.simulation.runtime import sumo_home
from tools import departure_reconciliation as passage

LEARNING_ARMS = (1000, 1001, 1002)
VALIDATION_ARMS = (4000, 4001, 4002)
INPUT_NAMES = ('calibrated.rou.xml', 'calibrated.agents.json', 'demand_meta.json', 'net.net.xml')


def load_source(source: Path) -> tuple[list[dynamic.RouteDeparture], dict[str, int], dict]:
    """Require complete, hashed route/time evidence and source OD/purpose identity."""
    manifest = json.loads((source / 'report.json').read_text())
    for name in INPUT_NAMES:
        expected = manifest['input_sha256'].get(name)
        if not expected or sha256_file(source / 'input' / name) != expected:
            raise ValueError(f'input hash differs: {name}')
    vehicles = passage.read_route_vehicles(source / 'input/calibrated.rou.xml')
    by_id = {v.vehicle_id: v for v in vehicles}
    agents_list = json.loads((source / 'input/calibrated.agents.json').read_text())['agents']
    agents = {a['vehicle_id']: a for a in agents_list}
    if len(agents) != len(agents_list) or set(agents) != set(by_id):
        raise ValueError('agent identities differ from route population')
    traces = {}
    for arm in LEARNING_ARMS:
        relative = f'evidence/learning-0-arm-{arm}/vehroute.xml'
        expected = manifest['evidence_sha256'].get(relative)
        if not expected or sha256_file(source / relative) != expected:
            raise ValueError(f'trace hash differs: {relative}')
        offsets = {}
        for _, element in ET.iterparse(source / relative, events=('end',)):
            if element.tag != 'vehicle':
                continue
            vehicle_id = element.attrib['id']
            if vehicle_id not in by_id or vehicle_id in offsets:
                raise ValueError('trace has unknown or duplicated vehicle')
            original = by_id[vehicle_id]
            route = element.find('route')
            if route is None or tuple(route.attrib['edges'].split()) != original.edges:
                raise ValueError('trace route differs from source')
            exits = tuple(float(x) for x in route.attrib['exitTimes'].split())
            actual_depart = float(element.attrib['depart'])
            if not math.isfinite(actual_depart) or any(not math.isfinite(x) for x in exits) \
                    or len(exits) != len(original.edges) or any(x < actual_depart for x in exits) \
                    or any(a > b for a, b in zip(exits, exits[1:])):
                raise ValueError('trace has incomplete or backwards route times')
            offsets[vehicle_id] = (actual_depart - original.depart_s,) + tuple(
                x - original.depart_s for x in exits[:-1])
            element.clear()
        if set(offsets) != set(by_id):
            raise ValueError('trace population is incomplete')
        traces[str(arm)] = offsets
    options, groups = [], Counter()
    for vehicle in vehicles:
        agent = agents[vehicle.vehicle_id]
        if agent.get('origin_edge') != vehicle.edges[0] \
                or agent.get('destination_edge') != vehicle.edges[-1] \
                or not agent.get('purpose') or agent.get('departure_s') != vehicle.depart_s:
            raise ValueError('agent route, purpose or departure identity differs')
        group = json.dumps([agent['origin_edge'], agent['destination_edge'], agent['purpose']])
        groups[group] += 1
        options.append(dynamic.RouteDeparture(
            vehicle.vehicle_id, group, vehicle.depart_s, vehicle.edges,
            {arm: values[vehicle.vehicle_id] for arm, values in traces.items()}, 1, 1))
    metadata = json.loads((source / 'input/demand_meta.json').read_text())
    sensors = list(metadata['sensor_targets']['variants']['edge_shares'])
    system = dynamic.build_passage_system(options, sensors, metadata['n_intervals'])
    projected = system.project([1] * len(options))
    for arm in LEARNING_ARMS:
        relative = f'evidence/learning-0-arm-{arm}/edge.xml'
        expected = manifest['evidence_sha256'].get(relative)
        if not expected or sha256_file(source / relative) != expected:
            raise ValueError(f'edgeData hash differs: {relative}')
        actual = passage._parse_entered(source / relative, metadata['n_intervals'], sensors)
        if projected[str(arm)] != actual:
            raise ValueError('route-time projection does not reconstruct raw entered cells')
    return options, dict(groups), metadata


def materialize_selection(source_route: Path, source_agents: Path,
                          selected: list[dynamic.RouteDeparture], output: Path) -> None:
    """Materialize selected anonymous trips with full route/endpoint provenance.

    The model selects flow alternatives, not identities of real people. Reusing
    a source template is recorded explicitly; total OD/purpose flows are checked
    by the solver. Default SUMO driver randomness remains active for validation.
    """
    root = ET.parse(source_route).getroot()
    templates = {e.attrib['id']: e for e in root.findall('vehicle')}
    source_document = json.loads(source_agents.read_text())
    agents = {a['vehicle_id']: a for a in source_document['agents']}
    candidate = ET.Element('routes', root.attrib)
    for child in root:
        if child.tag != 'vehicle':
            candidate.append(copy.deepcopy(child))
    result_agents, mapping = [], []
    for index, option in enumerate(sorted(selected, key=lambda o: (o.departure_s, o.option_id))):
        base_id = option.option_id.rsplit('@', 1)[0]
        element = copy.deepcopy(templates[base_id])
        if tuple(element.find('route').attrib['edges'].split()) != option.edges:
            raise ValueError('selected route lost source provenance')
        vehicle_id = f'dynamic{index}'
        element.set('id', vehicle_id)
        element.set('depart', f'{option.departure_s:.1f}')
        # Templates come from original demand, not the fitted profile-arm XML.
        candidate.append(element)
        agent = dict(agents[base_id], vehicle_id=vehicle_id,
                     departure_s=round(option.departure_s, 1))
        result_agents.append(agent)
        mapping.append({'vehicle_id': vehicle_id, 'source_vehicle_id': base_id,
                        'option_id': option.option_id})
    output.mkdir(exist_ok=False)
    ET.ElementTree(candidate).write(output / 'calibrated.rou.xml', encoding='utf-8', xml_declaration=True)
    source_document['agents'] = result_agents
    (output / 'calibrated.agents.json').write_text(json.dumps(source_document))
    (output / 'selection.json').write_text(json.dumps(mapping))


def simulate_counts(route: Path, network: Path, directory: Path, seed: int,
                    targets: dict, n_intervals: int, expected_vehicles: int) -> dict:
    """Run one bounded diagnostic and compare raw entered counts, including zeros."""
    directory.mkdir(exist_ok=False)
    home = sumo_home()
    passage._write_edgedata_additional(directory / 'edge.add.xml', 'edge.xml',
                                       sorted(targets), n_intervals * 900)
    command = passage._sumo_command(
        sumo_home=home, net_path=network, route_path=route, seed=seed,
        duration_s=n_intervals * 900, stats_path=directory / 'stats.xml',
        additional_path=directory / 'edge.add.xml', vehroute_path=directory / 'vehroute.xml')
    began = time.perf_counter()
    passage._run_sumo(command, cwd=directory, sumo_home=home)
    passage._validate_stats(directory / 'stats.xml', expected_vehicles)
    entered = passage._parse_entered(directory / 'edge.xml', n_intervals, sorted(targets))
    errors = [abs(entered[e][q]-target) for e, series in targets.items()
              for q, target in enumerate(series)]
    return {'seed': seed, 'exact_cells': errors.count(0), 'cells': len(errors),
            'absolute_error': sum(errors), 'sumo_wall_s': round(time.perf_counter()-began, 3)}


def run_trial(source: Path, output: Path, *, guard_s: float = 60) -> dict:
    """Fit once, then validate without refitting on three unseen SUMO seeds."""
    options, groups, metadata = load_source(source)
    raw_targets = metadata['sensor_targets']['variants']['edge_shares']
    targets = {edge: [int(round(v)) for v in values] for edge, values in raw_targets.items()}
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / 'input', output / 'input')
    source_manifest = json.loads((source / 'report.json').read_text())
    if any(sha256_file(output / 'input' / name) != source_manifest['input_sha256'][name]
           for name in INPUT_NAMES):
        raise ValueError('source changed while copying trial input')
    (output / 'code').mkdir()
    for code_path in (Path(__file__), Path(dynamic.__file__), Path(passage.__file__)):
        shutil.copy2(code_path, output / 'code' / code_path.name)
    home = sumo_home()
    start = time.perf_counter()
    report = {
        'diagnostic_only': True, 'active_production': False, 'status': 'refused',
        'source_trial': str(source.resolve()), 'source_manifest_sha256': sha256_file(source / 'report.json'),
        'input_sha256': {name: sha256_file(output / 'input' / name) for name in INPUT_NAMES},
        'source_sha256': {str(Path(p).resolve()): sha256_file(Path(p))
                          for p in (__file__, dynamic.__file__, passage.__file__)},
        'sumo_binary_sha256': sha256_file(home / 'bin/sumo'),
        'target_semantics': 'explicit round-to-nearest integer from existing edge_shares',
        'shift_support_s': [-900, -600, -300, 0, 300, 600, 900], 'guard_s': guard_s,
        'source_reconstruction_verified': True,
        'learning_arms': list(LEARNING_ARMS), 'validation_arms': list(VALIDATION_ARMS),
        'vehicles': len(options), 'od_purpose_groups': len(groups),
        'target_source': metadata.get('source'), 'target_date': metadata.get('date'),
        'unvalidated_contracts': ['production structural bounds and entropy priors',
                                  'other dates and sensors', 'production latency'],
    }
    try:
        expanded = dynamic.expand_departure_support(
            options, report['shift_support_s'], begin_s=0,
            end_s=metadata['n_intervals'] * 900, guard_s=guard_s)
        system = dynamic.build_passage_system(expanded, list(targets), metadata['n_intervals'])
        report.update(columns=len(expanded), sparse_nonzeros=system.matrix.nnz)
        fit_start = time.perf_counter()
        fit = dynamic.fit_integer_flows(system, targets, groups)
        report.update(fit_s=round(time.perf_counter()-fit_start, 3),
                      predicted_status=fit.status, absolute_prior_change=fit.absolute_prior_change)
        selected = [o for o, count in zip(expanded, fit.counts) if count == 1]
        if len(selected) != len(options):
            raise ValueError('selected population differs')
        report['selected_shifted_options'] = sum(not o.option_id.endswith('@0') for o in selected)
        report['predicted_boundary_counts'] = system.boundary_counts(fit.counts)
        materialize_selection(output / 'input/calibrated.rou.xml',
                              output / 'input/calibrated.agents.json', selected, output / 'candidate')
        report['verification'] = []
        for arm in VALIDATION_ARMS:
            baseline = simulate_counts(
                output / 'input/calibrated.rou.xml', output / 'input/net.net.xml',
                output / f'baseline-{arm}', arm, targets, metadata['n_intervals'], len(options))
            treatment = simulate_counts(
                output / 'candidate/calibrated.rou.xml', output / 'input/net.net.xml',
                output / f'validation-{arm}', arm, targets, metadata['n_intervals'], len(selected))
            treatment['baseline'] = baseline
            report['verification'].append(treatment)
        report['status'] = ('sumo_exact_isolated' if all(
            r['absolute_error'] == 0 for r in report['verification']) else 'sumo_mismatch')
    except (dynamic.DynamicAssignmentError, passage.DepartureReconciliationError, ValueError) as error:
        report['reason'] = str(error)
    report['elapsed_s'] = round(time.perf_counter()-start, 3)
    report['artifact_sha256'] = {str(p.relative_to(output)): sha256_file(p)
        for p in output.rglob('*') if p.is_file() and p.suffix in ('.xml', '.json')}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-trial', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--guard-s', type=float, default=60)
    args = parser.parse_args()
    report = run_trial(args.source_trial, args.out, guard_s=args.guard_s)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'sumo_exact_isolated' else 2


if __name__ == '__main__':
    raise SystemExit(main())
