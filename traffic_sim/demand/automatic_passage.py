"""Automatic, measured route/departure calibration before demand publication.

Each invocation measures its own PFE output. No saved trial or calendar date is
special. Failed measurement, infeasibility or regression aborts the build; all
variants are staged before any of their route/agent files are replaced.
"""
from __future__ import annotations

from collections import Counter
import gzip
import hashlib
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET

import numpy as np

from demand.structure import (DEST_GROUP_CAP_MULT, calibrated_structure_report,
                              route_od_distance_km)
from traffic_sim.demand.structure_caps import integer_structure_cap
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.demand.provenance import _validate_variant
from traffic_sim.experimental import dynamic_assignment as dynamic
from traffic_sim.simulation.sensor_fit import assess_passage_accuracy
from tools import trial_dynamic_passage as trial
from tools import departure_reconciliation as passage

POLICY = 'automatic_dynamic_passage_v3'
REPLAY_CONTRACT_NAME = 'passage_replay_contract.json'


def replay_source_sha256() -> dict[str, str]:
    """Code identity needed to interpret a saved pure passage replay."""
    return {
        'automatic_passage': sha256_file(Path(__file__)),
        'dynamic_assignment': sha256_file(Path(dynamic.__file__)),
        'trial_dynamic_passage': sha256_file(Path(trial.__file__)),
        'departure_reconciliation': sha256_file(Path(passage.__file__)),
    }


@contextmanager
def preserve_demand_on_failure(sumo_dir: Path):
    """Keep the previous CLI demand coherent if post-PFE validation fails.

    The caller owns the demand build lock. Only conventional builder outputs
    are restored; failed-run evidence and unrelated files remain untouched.
    """
    names = ['demand_meta.json', 'demand_build_spec.json', 'candidates.rou.xml',
             'candidates.meta.json']
    names += [f'calibrated{suffix}{extension}' for suffix in ('', '_v1', '_v2')
              for extension in ('.rou.xml', '.agents.json')]
    with tempfile.TemporaryDirectory(prefix='demand-rollback-') as directory:
        backup = Path(directory)
        for name in names:
            if (sumo_dir / name).is_file():
                shutil.copy2(sumo_dir / name, backup / name)
        try:
            yield
        except BaseException:
            for name in names:
                target = sumo_dir / name
                if (backup / name).exists():
                    temporary = target.with_name(target.name + '.rollback-tmp')
                    shutil.copy2(backup / name, temporary)
                    os.replace(temporary, target)
                else:
                    target.unlink(missing_ok=True)
            raise


def runtime_identity(home: Path) -> dict:
    """Day cache must invalidate when its SUMO loading implementation changes."""
    digest = sha256_file(home / 'bin/sumo')
    if digest is None:
        raise ValueError('SUMO executable is unavailable for passage calibration')
    return {'policy': POLICY, 'sumo_binary_sha256': digest}


def _bounds(report: dict, bounds: list[dict]) -> list[dict]:
    retained = report.get('structural_bounds_retained_per_quarter')
    if retained is not None:
        if not isinstance(retained, list) or len(retained) != len(bounds) \
                or any(not isinstance(value, bool) for value in retained):
            raise ValueError('invalid per-quarter structural bounds contract')
        return [row if keep else {} for row, keep in zip(bounds, retained)]
    summary = report.get('relaxation_summary', {})
    relaxed = {r['quarter'] for r in report.get('relaxed_bound_violations', [])}
    if set(summary) - {'clean', 'no_bounds_tol1'} or sum(summary.values()) != len(bounds) \
            or len(relaxed) != summary.get('no_bounds_tol1', 0) \
            or any(isinstance(q, bool) or not isinstance(q, int) or not 0 <= q < len(bounds)
                   for q in relaxed):
        raise ValueError('unsupported or incomplete PFE relaxation evidence')
    return [{} if q in relaxed else row for q, row in enumerate(bounds)]


def _measure(route, network, directory, seed, targets, quarters, vehicles):
    trial.simulate_counts(route, network, directory, seed, targets, quarters, vehicles)
    return passage._parse_entered(directory / 'edge.xml', quarters, sorted(targets))


def _measure_batch(jobs):
    """Independent SUMO processes, bounded by CPU count/six and returned in seed order."""
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(jobs), os.cpu_count() or 1)) as pool:
        return list(pool.map(lambda arguments: _measure(*arguments), jobs))


def _error(counts, targets):
    return sum(abs(value - targets[edge][q])
               for edge, series in counts.items() for q, value in enumerate(series))


def _short_trip_departure_guards(base_options, expanded, structure: dict,
                                 quarters: int,
                                 active_quarters: set[int] | None = None,
                                 ) -> list[dynamic.DepartureGroupBounds]:
    """Keep a clean PFE short-trip envelope valid while shifting departures.

    The audit's floor depends on the resulting quarter total.  Encode both
    sides of that contract: an absolute short-trip ceiling and the smallest
    denominator for which the same exact integer ceiling remains valid.
    """
    audit = structure.get('trip_length_fit', {}).get('under_1km_cap_audit')
    if not isinstance(audit, dict) or audit.get('violating_quarters') != 0:
        return []
    pool_share = audit.get('pool_share')
    if isinstance(pool_share, bool) or not isinstance(pool_share, (int, float)) \
            or not np.isfinite(pool_share) or not 0 <= pool_share <= 1:
        raise ValueError('invalid short-trip pool-share evidence')
    cap_share = DEST_GROUP_CAP_MULT * float(pool_share)
    if cap_share >= 1:
        return []
    source_totals = [0] * quarters
    known_routes = 0
    distance_by_route: dict[tuple[str, ...], float | None] = {}
    def distance(option):
        key = tuple(option.edges)
        if key not in distance_by_route:
            distance_by_route[key] = route_od_distance_km(key)
        return distance_by_route[key]
    for option in base_options:
        if distance(option) is None:
            continue
        quarter = int(option.departure_s // 900)
        if not 0 <= quarter < quarters:
            raise ValueError('source departure lies outside passage horizon')
        source_totals[quarter] += 1
        known_routes += 1
    short_ids, known_ids = set(), set()
    for option in expanded:
        trip_distance = distance(option)
        if trip_distance is None:
            continue
        known_ids.add(option.option_id)
        if trip_distance <= 1.0:
            short_ids.add(option.option_id)
    if not short_ids or not known_ids:
        return []
    active = (set(range(quarters)) if active_quarters is None
              else set(active_quarters))
    if any(isinstance(q, bool) or not isinstance(q, int)
           or not 0 <= q < quarters for q in active):
        raise ValueError('short-trip guard quarters lie outside the horizon')
    short_bounds, denominator_bounds = [], []
    for quarter, source_total in enumerate(source_totals):
        cap = integer_structure_cap(source_total, cap_share)
        minimum_total = next(
            total for total in range(source_total + 1)
            if integer_structure_cap(total, cap_share) >= cap)
        short_bounds.append((0, cap if quarter in active else known_routes))
        denominator_bounds.append(
            (minimum_total if quarter in active else 0, known_routes))
    return [
        dynamic.DepartureGroupBounds(
            'trips_under_1km_cap', frozenset(short_ids), tuple(short_bounds)),
        dynamic.DepartureGroupBounds(
            'trips_under_1km_denominator', frozenset(known_ids),
            tuple(denominator_bounds)),
    ]


def _stage_selection(inputs: Path, selected, stage: Path) -> Path:
    trial.materialize_selection(
        inputs / 'calibrated.rou.xml', inputs / 'calibrated.agents.json',
        selected, stage)
    candidate = stage / 'calibrated.rou.xml'
    _validate_variant(candidate, stage / 'calibrated.agents.json')
    # Day assembly consumes one complete vehicle element per line. Preserve
    # this established format, including the absence of an XML declaration.
    root = ET.parse(candidate).getroot()
    candidate.write_text('<routes>\n' + ''.join(
        ET.tostring(child, encoding='unicode').strip()+'\n' for child in root)
        + '</routes>\n')
    return candidate


def _structure_flag_names(report: dict) -> set[str]:
    return {str(value).split(':', 1)[0]
            for value in report.get('structure_flags', [])}


def _short_trip_violation_quarters(report: dict) -> set[int]:
    audit = report.get('trip_length_fit', {}).get('under_1km_cap_audit', {})
    violations = audit.get('violations', []) if isinstance(audit, dict) else []
    result = set()
    for violation in violations:
        quarter = violation.get('quarter') if isinstance(violation, dict) else None
        if isinstance(quarter, bool) or not isinstance(quarter, int):
            raise ValueError('short-trip audit has an invalid quarter')
        result.add(quarter)
    return result


def _refine(data, source_report, network, work, candidate_pool):
    quarters = len(data['targets'])
    edges = sorted({edge for row in data['targets'] for edge in row})
    if not quarters or not edges or any(set(row) != set(edges) for row in data['targets']):
        raise ValueError('passage targets require explicit coverage, including zeros')
    targets = {e: [int(round(row[e])) for row in data['targets']] for e in edges}
    bounds = _bounds(source_report, data['hard_bounds_pq'])
    route = Path(data['out_path'])
    agents = route.with_name(route.name.replace('.rou.xml', '.agents.json'))
    vehicles = _validate_variant(route, agents)
    if not vehicles:
        raise ValueError('automatic passage calibration requires a nonempty population')
    inputs = work / 'input'
    inputs.mkdir(parents=True)
    for source, name in [(route, 'calibrated.rou.xml'), (agents, 'calibrated.agents.json'),
                         (network, 'net.net.xml')]:
        shutil.copy2(source, inputs / name)
    metadata = {'n_intervals': quarters, 'sensor_targets': {'variants': {'edge_shares': targets}}}
    (inputs / 'demand_meta.json').write_text(json.dumps(metadata))
    (inputs / REPLAY_CONTRACT_NAME).write_text(json.dumps({
        'schema_version': 1,
        'policy': POLICY,
        'retained_bounds_pq': bounds,
        'source_sha256': replay_source_sha256(),
    }, sort_keys=True, separators=(',', ':')) + '\n')
    manifest = {'input_sha256': {p.name: sha256_file(p) for p in inputs.iterdir()},
                'evidence_sha256': {}}
    (work / 'evidence').mkdir()
    _measure_batch([(inputs / 'calibrated.rou.xml', network,
                     work / 'evidence' / f'learning-0-arm-{seed}',
                     seed, targets, quarters, vehicles) for seed in trial.LEARNING_ARMS])
    for seed in trial.LEARNING_ARMS:
        directory = work / 'evidence' / f'learning-0-arm-{seed}'
        for name in ('edge.xml', 'vehroute.xml'):
            path = directory / name
            manifest['evidence_sha256'][str(path.relative_to(work))] = sha256_file(path)
    (work / 'report.json').write_text(json.dumps(manifest))
    # The loader already built and PROVED this system: its projection
    # reconstructed the raw entered cells. Rebuilding an identical one here was
    # pure duplication, so the verified object is carried through instead.
    verified = trial.load_verified_source(work)
    options, groups = list(verified.options), dict(verified.groups)
    original = verified.system
    if original.sensors != tuple(edges) or original.n_intervals != quarters:
        raise ValueError('verified passage system does not match the calibrated targets')
    matrix, lower, upper = dynamic.departure_bound_constraints(original, bounds)
    counts = matrix @ np.ones(len(options))
    if np.any(counts < lower) or np.any(counts > upper):
        raise ValueError('source violates retained PFE structural bounds')
    expanded = dynamic._expand_departure_support_verified(
        original, [-900, -600, -300, 0, 300, 600, 900],
        begin_s=0, end_s=quarters*900, guard_s=60)
    before_structure = calibrated_structure_report(route, pool_path=candidate_pool)
    if before_structure is None:
        raise ValueError('automatic passage calibration lacks structural evidence')

    def solve_and_stage(support, name: str, *, group_bounds=None,
                        time_limit_s: float = 60):
        system = dynamic.build_passage_system(support, edges, quarters)
        fitted = dynamic.fit_integer_flows(
            system, targets, groups, time_limit_s=time_limit_s,
            departure_bounds=bounds,
            departure_group_bounds=group_bounds,
            checkpoint_dir=work / f'solver-{name}',
            cache_dir=work.parent.parent / 'passage-solver-cache')
        chosen = [option for option, count in zip(support, fitted.counts)
                  if count == 1]
        if Counter(option.group for option in chosen) != groups \
                or len(chosen) != vehicles:
            raise ValueError('dynamic fit changed OD/purpose population')
        chosen_stage = work / name
        chosen_candidate = _stage_selection(inputs, chosen, chosen_stage)
        structure = calibrated_structure_report(
            chosen_candidate, pool_path=candidate_pool)
        if structure is None:
            raise ValueError('automatic passage calibration lacks structural evidence')
        return fitted, chosen, chosen_stage, chosen_candidate, structure

    initial_boundary = False
    try:
        fit, selected, stage, candidate, after_structure = solve_and_stage(
            expanded, 'candidate')
    except dynamic.DynamicAssignmentInfeasible:
        expanded = dynamic.expand_passage_boundary_support(
            options, edges, max_shift_s=900, begin_s=0,
            end_s=quarters*900, guard_s=60)
        initial_boundary = True
        fit, selected, stage, candidate, after_structure = solve_and_stage(
            expanded, 'candidate-boundaries', time_limit_s=120)
    old_flags = _structure_flag_names(before_structure)
    new_flags = _structure_flag_names(after_structure)
    structural_repair = {
        'passes': 0, 'quarters': [],
        'support': 'passage_boundaries' if initial_boundary else 'fixed_shift_grid',
        'boundary_fallback': initial_boundary,
    }
    if new_flags - old_flags:
        if new_flags - old_flags != {'trips_under_1km_cap'}:
            raise ValueError(f'new structural warnings: {sorted(new_flags - old_flags)}')
        active_quarters: set[int] = set()
        support = expanded
        boundary_fallback = initial_boundary
        force_refit = False
        repair_index = 0
        while new_flags - old_flags:
            violations = _short_trip_violation_quarters(after_structure)
            newly_active = violations - active_quarters
            if newly_active:
                active_quarters.update(newly_active)
            elif force_refit:
                force_refit = False
            elif not boundary_fallback:
                support = dynamic.expand_passage_boundary_support(
                    options, edges, max_shift_s=900, begin_s=0,
                    end_s=quarters*900, guard_s=60)
                boundary_fallback = True
            else:
                break
            guards = _short_trip_departure_guards(
                options, support, before_structure, quarters, active_quarters)
            if not guards:
                break
            repair_index += 1
            try:
                fit, selected, stage, candidate, after_structure = solve_and_stage(
                    support, f'candidate-repair-{repair_index}',
                    group_bounds=guards,
                    time_limit_s=120 if boundary_fallback else 60)
            except dynamic.DynamicAssignmentInfeasible:
                if boundary_fallback:
                    raise
                support = dynamic.expand_passage_boundary_support(
                    options, edges, max_shift_s=900, begin_s=0,
                    end_s=quarters*900, guard_s=60)
                boundary_fallback = True
                force_refit = True
                continue
            structural_repair['passes'] += 1
            structural_repair['quarters'] = sorted(active_quarters)
            structural_repair['support'] = (
                'passage_boundaries' if boundary_fallback else 'fixed_shift_grid')
            structural_repair['boundary_fallback'] = boundary_fallback
            new_flags = _structure_flag_names(after_structure)
            unexpected = (new_flags - old_flags) - {'trips_under_1km_cap'}
            if unexpected:
                raise ValueError(f'new structural warnings: {sorted(unexpected)}')
        if new_flags - old_flags:
            raise ValueError(f'new structural warnings: {sorted(new_flags - old_flags)}')
    before, after = {}, {}
    jobs = [(source, network, work / f'{label}-{seed}', seed, targets, quarters, vehicles)
            for seed in trial.VALIDATION_ARMS for label, source in (
                ('before', inputs / 'calibrated.rou.xml'), ('after', candidate))]
    measured = _measure_batch(jobs)
    for index, seed in enumerate(trial.VALIDATION_ARMS):
        before[seed], after[seed] = measured[2*index:2*index+2]
        if _error(after[seed], targets) > _error(before[seed], targets):
            raise ValueError(f'passage validation regressed for seed {seed}')
    for edge in edges:
        if sum(_error({edge: row[edge]}, targets) for row in after.values()) > sum(
                _error({edge: row[edge]}, targets) for row in before.values()):
            raise ValueError(f'passage validation regressed for sensor edge {edge}')
    rows = [{'target_mean': targets[e], 'simulated_mean_raw': [
        sum(row[e][q] for row in after.values()) / len(after) for q in range(quarters)],
        'seed_runs': [{'seed': seed, 'simulated_raw': row[e]} for seed, row in after.items()]}
        for e in edges]
    accuracy = assess_passage_accuracy({'directions': rows}, n_intervals=quarters)
    if quarters % 4 == 0 and not accuracy['ok']:
        raise ValueError('automatic passage output failed existing passage accuracy criteria')
    totals = [0]*quarters
    for option in selected:
        totals[int(option.departure_s//900)] += 1
    evidence = {'policy': POLICY, 'status': 'validated',
        'measurement_basis': 'sensor_edge_entry_quarter',
        'prediction_exact': True, 'raw_exactness': 'see_validation_and_scenario_sensor_audit',
        'source_inputs': manifest['input_sha256'], 'learning_evidence': manifest['evidence_sha256'],
        'source_pfe_fit': source_report,
        'retained_constraints': sum(len(row) for row in bounds), 'new_relaxations': 0,
        'structural_repair': structural_repair,
        'departure_population': totals,
        'validation': {'source_absolute_error': sum(_error(row, targets) for row in before.values()),
                       'candidate_absolute_error': sum(_error(row, targets) for row in after.values()),
                       'source_counts': before, 'candidate_counts': after, 'accuracy': accuracy},
        'evidence_directory': str(work.resolve()),
        'selection_sha256': sha256_file(stage / 'selection.json'),
        'routes_sha256': sha256_file(candidate),
        'agents_sha256': sha256_file(stage / 'calibrated.agents.json')}
    report = copy.deepcopy(source_report)
    report.update({'passage_calibration': evidence,
        'measurement_basis': 'predicted_sensor_passage_quarter',
        'achieved': targets, 'geh_ok': quarters*len(edges), 'geh_total': quarters*len(edges),
        'geh_pct': 100., 'integer_sensor_constraints': quarters*len(edges),
        'integer_sensor_exact': quarters*len(edges), 'integer_sensor_exact_pct': 100.,
        'integer_sensor_max_abs_error': 0., 'integer_sensor_sum_abs_error': 0.,
        'integer_sensor_target_rule': 'int(round(target))'})
    # These describe the PFE draw, not the newly sampled departure population.
    report.pop('purpose_allocation', None)
    report.pop('purpose_allocation_summary', None)
    (work / 'result.json').write_text(json.dumps(evidence, indent=2))
    return report, stage, totals


KEEP_EVIDENCE_ENV = 'TRAFFIC_SIM_KEEP_PASSAGE_EVIDENCE'
# Measurements whose SHA-256 is recorded in result.json. Compressed, never
# dropped: the digest still verifies through one gunzip, so a later reader can
# check what was actually measured.
_COMPRESSED = ('input', 'evidence', 'before-', 'after-')


def _gzip_verified(source: Path) -> bool:
    """Write ``source``.gz deterministically and prove it round-trips."""
    packed = source.with_name(source.name + '.gz')
    temporary = packed.with_name(packed.name + '.prune-tmp')
    try:
        with open(source, 'rb') as raw, open(temporary, 'wb') as packed_stream, \
                gzip.GzipFile(filename='', mode='wb', fileobj=packed_stream,
                              compresslevel=3, mtime=0) as out:
            shutil.copyfileobj(raw, out)
        original = sha256_file(source)
        digest = hashlib.sha256()
        with gzip.open(temporary, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b''):
                digest.update(chunk)
        if digest.hexdigest() != original:
            return False
        os.replace(temporary, packed)
        return True
    except (OSError, EOFError, gzip.BadGzipFile):
        return False
    finally:
        temporary.unlink(missing_ok=True)


def prune_evidence(evidence_root: Path, *, keep_all: bool = False) -> None:
    """Shrink one finished evidence root without weakening its record.

    Nothing here is read by the pipeline: ``evidence_directory`` is written and
    never loaded, and every durable number already lives in ``result.json`` and
    in ``demand_meta.json``'s ``passage_calibration``. So the rule is by kind,
    not by size. Measurements are COMPRESSED, because their SHA-256 is recorded
    and must stay checkable. Copies whose bytes exist elsewhere are REMOVED:
    a staged selection is byte-identical to the file now in ``sumo/``, and
    ``original-*`` are rollback backups whose purpose ends when the swap
    commits. Solver requests are kept in EVERY case: an existing contract
    requires a replayable request even after a cache hit.

    Each compressed file is verified before its original is removed. A failed
    round-trip retains that original; previously verified files remain packed.
    """
    root = Path(evidence_root)
    if keep_all or os.environ.get(KEEP_EVIDENCE_ENV):
        return
    paths = [path for path in sorted(root.rglob('*.xml'))
             if any(part.startswith(_COMPRESSED) for part in path.relative_to(root).parts)]
    if paths:
        with ThreadPoolExecutor(max_workers=min(3, len(paths), os.cpu_count() or 1)) as pool:
            for path, verified in zip(paths, pool.map(_gzip_verified, paths)):
                if not verified:
                    raise ValueError(f'passage evidence did not survive compression: {path}')
                path.unlink()
    for arm in sorted(p for p in root.iterdir() if p.is_dir()):
        for stage in sorted(arm.glob('candidate*')):
            shutil.rmtree(stage, ignore_errors=True)
    (root / 'source_reports.json').unlink(missing_ok=True)
    for backup in sorted(root.glob('original-*')):
        backup.unlink(missing_ok=True)


def refine_variants(variant_inputs: dict, reports: dict, network: Path,
                    evidence_root: Path, *, candidate_pool: Path | None = None) -> dict:
    """Stage every arm, validate against fresh SUMO, then replace route pairs.

    Each arm conserves its own PFE OD/purpose population. Direction stress
    arms may have different vehicle totals; q50 departure totals cannot be
    imposed on their independently sampled populations.
    PFE remains the source of behavioural priors and retained hard bounds.
    """
    if '' not in variant_inputs or set(variant_inputs) != set(reports):
        raise ValueError('automatic passage calibration requires a matching median arm')
    started = time.perf_counter()
    timings = {'variant_s': {}}
    evidence_root.mkdir(parents=True, exist_ok=False)
    (evidence_root / 'source_reports.json').write_text(json.dumps(reports, indent=2))
    result, staged = {}, []
    for suffix in sorted(variant_inputs, key=lambda value: (value != '', value)):
        variant_started = time.perf_counter()
        report, stage, _population = _refine(
            variant_inputs[suffix], reports[suffix], network,
            evidence_root / ('q50' if not suffix else suffix), candidate_pool)
        timings['variant_s'][suffix or 'q50'] = time.perf_counter() - variant_started
        result[suffix] = report
        target = Path(variant_inputs[suffix]['out_path'])
        staged.extend([(stage / 'calibrated.rou.xml', target),
                       (stage / 'calibrated.agents.json',
                        target.with_name(target.name.replace('.rou.xml', '.agents.json')))])
    backups = {}
    for index, (_, target) in enumerate(staged):
        saved = evidence_root / f'original-{index}'
        shutil.copy2(target, saved)
        backups[target] = saved
    try:
        for source, target in staged:
            temporary = target.with_name(target.name + '.passage-tmp')
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
    except BaseException:
        for target, saved in backups.items():
            shutil.copy2(saved, target)
        raise
    # The routes are already committed. Reclaiming disk is an optimisation and
    # must never fail a valid build, so a prune failure is reported and the
    # evidence is left whole rather than rolled back.
    retention_started = time.perf_counter()
    try:
        prune_evidence(evidence_root)
    except (OSError, ValueError) as exc:
        print(f'  passage evidence retention incomplete: {exc}')
    timings['evidence_retention_s'] = time.perf_counter() - retention_started
    timings['total_s'] = time.perf_counter() - started
    print(f"  passage evidence retention: {timings['evidence_retention_s']:.1f}s", flush=True)
    try:
        (evidence_root / 'timings.json').write_text(json.dumps(timings, indent=2))
    except OSError as exc:
        print(f'  passage timing report unavailable: {exc}')
    return result
