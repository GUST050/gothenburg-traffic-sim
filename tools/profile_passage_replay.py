"""Read-only phase replay and timing inventory for one passage evidence root.

python3 -m tools.profile_passage_replay --source runs/<demand>/passage/q50 --out runs/<new>

IMPROVEMENT_PLAN.md "Körinstruktion för hastighetsarbetet", steg 0. The tool
measures where passage calibration spends its time by replaying the SAME
production functions over already-saved SUMO traces. It never runs SUMO, never
writes inside the evidence root it reads, and refuses to treat a compressed,
pruned or incomplete trace as a complete one.

Nothing here is imported by the pipeline: this module is a leaf so that adding
or changing it cannot move any demand, catalog or scenario identity.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import shutil
import time

from demand.structure import calibrated_structure_report
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.demand import automatic_passage
from traffic_sim.experimental import dynamic_assignment as dynamic
from tools import departure_reconciliation as passage
from tools import trial_dynamic_passage as trial

SCHEMA_VERSION = 1
POLICY = 'passage_replay_profile_v1'
SHIFT_SUPPORT_S = [-900, -600, -300, 0, 300, 600, 900]
GUARD_S = 60

#: Required by ``trial.load_source``; a root missing any of them is incomplete.
REQUIRED_INPUTS = tuple(f'input/{name}' for name in trial.INPUT_NAMES)
REQUIRED_EVIDENCE = tuple(
    f'evidence/learning-0-arm-{arm}/{name}'
    for arm in trial.LEARNING_ARMS for name in ('edge.xml', 'vehroute.xml'))

#: Work this replay cannot account for, named so the residual stays explicit
#: instead of being silently folded into a measured phase.
UNMEASURED_CATEGORIES = {
    'sumo_subprocess': 'replay reads saved traces; no SUMO process is started',
    'learning_and_validation_waves': 'six measurement runs are historical, not replayed',
    'publication_and_rollback': 'route/agent replacement is a production-only side effect',
    'archive_validation': 'not requested; pass --archive together with --demand-spec',
    'costing_and_resolver': 'cost-ordered execution needs a monthly workspace, not this root',
}


class ReplayRefused(RuntimeError):
    """The requested replay cannot be run honestly on this input."""


class PhaseRecorder:
    """Nested ``perf_counter`` phases with exclusive time and a residual.

    Concurrent children overlap in wall time, so their parent's exclusive time
    is undefined rather than guessed: summing a wave's children into its
    parent's total is exactly the double counting the plan forbids.
    """

    def __init__(self, context: dict, *, clock=time.perf_counter, pid: int | None = None):
        self._clock = clock
        self._pid = os.getpid() if pid is None else pid
        # A live reference, not a copy: the input identity is only known after
        # the evidence has been parsed, and every row must still carry it.
        self.context = context
        self._records: list[dict] = []
        self._stack: list[int] = []

    @contextmanager
    def phase(self, name: str, *, concurrent: bool = False, **attributes):
        index = len(self._records)
        record = {
            'index': index,
            'phase': name,
            'parent_phase': self._records[self._stack[-1]]['phase'] if self._stack else None,
            'parent_index': self._stack[-1] if self._stack else None,
            'pid': self._pid,
            'concurrent_children': bool(concurrent),
            **{key: value for key, value in attributes.items() if value is not None},
        }
        self._records.append(record)
        self._stack.append(index)
        record['start_s'] = self._clock()
        try:
            yield record
        finally:
            record['end_s'] = self._clock()
            record['wall_s'] = record['end_s'] - record['start_s']
            self._stack.pop()

    def records(self) -> list[dict]:
        """Every phase with its exclusive time resolved, in start order."""
        children: dict[int | None, list[dict]] = {}
        for record in self._records:
            children.setdefault(record['parent_index'], []).append(record)
        resolved = []
        for record in self._records:
            own = list(children.get(record['index'], ()))
            row = {**self.context, **record}
            if record['concurrent_children'] and own:
                row['exclusive_s'] = None
                row['exclusive_basis'] = 'concurrent_children_overlap'
                row['children_wall_sum_s'] = round(sum(c['wall_s'] for c in own), 6)
                row['children_wall_max_s'] = round(max(c['wall_s'] for c in own), 6)
            else:
                row['exclusive_s'] = round(
                    record['wall_s'] - sum(c['wall_s'] for c in own), 6)
                row['exclusive_basis'] = 'wall_minus_sequential_children'
            row['wall_s'] = round(record['wall_s'], 6)
            row['start_s'] = round(record['start_s'], 6)
            row['end_s'] = round(record['end_s'], 6)
            resolved.append(row)
        return resolved

    def summary(self) -> dict:
        """Root wall time, its top-level split and the unattributed residual."""
        rows = self.records()
        if not rows:
            return {'phases': [], 'root_wall_s': None, 'residual_s': None}
        root = rows[0]
        top = [row for row in rows if row['parent_index'] == root['index']]
        return {
            'phases': rows,
            'root_phase': root['phase'],
            'root_wall_s': root['wall_s'],
            'top_level_wall_sum_s': round(sum(row['wall_s'] for row in top), 6),
            'residual_s': root['exclusive_s'],
            'residual_basis': root['exclusive_basis'],
        }


def _resolve_isolation(source: Path, out: Path) -> tuple[Path, Path]:
    """Refuse any output path that would write into the evidence being read."""
    source = Path(source).resolve()
    out = Path(out).resolve()
    if source == out or source in out.parents or out in source.parents:
        raise ReplayRefused(
            f'output {out} must be outside the source evidence root {source}')
    if not source.is_dir():
        raise ReplayRefused(f'source evidence root does not exist: {source}')
    return source, out


def inventory(source: Path) -> dict:
    """Classify a saved evidence root before anything is called a measurement."""
    source = Path(source)
    manifest_path = source / 'report.json'
    if not manifest_path.is_file():
        return {'trace_state': 'incomplete', 'reason': 'evidence manifest is missing',
                'files': {}, 'pruned_markers': {}}
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as error:
        return {'trace_state': 'incomplete', 'reason': f'unreadable manifest: {error}',
                'files': {}, 'pruned_markers': {}}
    digests = dict(manifest.get('input_sha256') or {})
    digests.update(manifest.get('evidence_sha256') or {})
    files: dict[str, dict] = {}
    for relative in REQUIRED_INPUTS + REQUIRED_EVIDENCE:
        expected = digests.get(Path(relative).name if relative.startswith('input/')
                               else relative)
        path = source / relative
        packed = path.with_name(path.name + '.gz')
        if path.is_file():
            actual = sha256_file(path)
            state = 'raw' if expected and actual == expected else (
                'raw_unrecorded' if not expected else 'raw_hash_mismatch')
        elif packed.is_file():
            state = 'compressed'
        else:
            state = 'missing'
        files[relative] = {'state': state, 'expected_sha256': expected}
    states = {row['state'] for row in files.values()}
    if states == {'raw'}:
        trace_state = 'complete'
    elif states <= {'raw', 'compressed'} and 'compressed' in states:
        trace_state = 'compressed'
    else:
        trace_state = 'incomplete'
    return {
        'trace_state': trace_state,
        'files': files,
        'pruned_markers': {
            # prune_evidence removes these; their absence means the root can no
            # longer answer every question a fresh evidence root could.
            'candidate_stages_present': bool(list(source.glob('candidate*'))),
            'result_json_present': (source / 'result.json').is_file(),
            'source_reports_present': (source.parent / 'source_reports.json').is_file(),
        },
    }


def expand_compressed(source: Path, inventory_report: dict, out: Path) -> Path:
    """Materialize verified raw copies of a pruned root, never in place."""
    root = out / 'expanded'
    root.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source / 'report.json', root / 'report.json')
    for relative, row in inventory_report['files'].items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if row['state'] == 'raw':
            shutil.copy2(source / relative, target)
        elif row['state'] == 'compressed':
            with gzip.open(source / f'{relative}.gz', 'rb') as packed, \
                    open(target, 'wb') as raw:
                shutil.copyfileobj(packed, raw)
        else:
            raise ReplayRefused(
                f'cannot expand {relative}: it is {row["state"]}, not stored evidence')
        expected = row['expected_sha256']
        if expected and sha256_file(target) != expected:
            raise ReplayRefused(f'expanded evidence does not match its recorded digest: {relative}')
    return root


def _retained_bounds(source: Path, variant: str) -> tuple[list[dict] | None, str]:
    """The retained PFE bounds if this root still carries its source report."""
    reports_path = source.parent / 'source_reports.json'
    if not reports_path.is_file():
        return None, 'source_reports.json was pruned from the evidence root'
    try:
        reports = json.loads(reports_path.read_text())
    except (OSError, ValueError) as error:
        return None, f'source_reports.json is unreadable: {error}'
    key = '' if variant == 'q50' else variant
    if key not in reports:
        return None, f'source_reports.json has no entry for variant {variant!r}'
    report = reports[key]
    bounds_source = report.get('hard_bounds_pq')
    if bounds_source is None:
        return None, 'source report carries no per-quarter hard bounds'
    try:
        return automatic_passage._bounds(report, bounds_source), 'retained_pfe_bounds'
    except ValueError as error:
        return None, f'retained bounds are not usable: {error}'


def _validate_archive(archive: Path, spec_path: Path, recorder: PhaseRecorder,
                      repeats: int) -> dict:
    """Time ``validate_demand_archive`` first-call against reused-process calls.

    The import is local: this tool must stay a leaf of the demand graph, and
    the monthly resolver is not needed to replay a passage root.
    """
    from traffic_sim.simulation.monthly_demand import (DemandBuildSpec,
                                                       validate_demand_archive)
    required = DemandBuildSpec.from_dict(json.loads(Path(spec_path).read_text()))
    calls = []
    with recorder.phase('archive_validation', archive=str(archive)):
        for index in range(repeats):
            with recorder.phase('validate_demand_archive', call=index) as call:
                try:
                    validate_demand_archive(Path(archive), required)
                    call['outcome'] = 'valid'
                except (OSError, ValueError) as error:
                    call['outcome'] = f'rejected: {error}'
            calls.append(call['outcome'])
    return {'archive': str(archive), 'calls': calls,
            'call_basis': 'first call is program-cold; later calls reuse this process'}


def replay(source: Path, out: Path, *, label: str | None = None,
           pool: Path | None = None, allow_compressed: bool = False,
           solver_time_limit_s: float = 60, archive: Path | None = None,
           demand_spec: Path | None = None, archive_repeats: int = 3) -> dict:
    """Replay preparation, solving, staging and structure over saved traces."""
    source, out = _resolve_isolation(source, out)
    out.mkdir(parents=True, exist_ok=False)
    variant = source.name
    context = {'variant': variant, 'date': None, 'input_identity': None}
    recorder = PhaseRecorder(context)
    report: dict = {
        'schema_version': SCHEMA_VERSION, 'policy': POLICY,
        'diagnostic_only': True, 'active_production': False, 'status': 'refused',
        'source_evidence_root': str(source), 'output_root': str(out),
        'label': label, 'sample_selection': 'declared_by_operator',
        'reads_saved_traces_only': True,
        'unmeasured_categories': {
            name: reason for name, reason in UNMEASURED_CATEGORIES.items()
            if not (name == 'archive_validation' and archive is not None)},
        'source_sha256': {str(Path(path).resolve()): sha256_file(Path(path))
                          for path in (__file__, automatic_passage.__file__,
                                       dynamic.__file__, trial.__file__)},
    }
    try:
        with recorder.phase('replay') as root:
            # Hashing every stored file is real work, so it is timed where it
            # happens rather than declared complete before the clock starts.
            with recorder.phase('inventory') as counted:
                found = inventory(source)
                counted['files'] = len(found['files'])
            report['trace_state'] = found['trace_state']
            report['inventory'] = found
            if found['trace_state'] == 'incomplete':
                raise ReplayRefused('evidence root is incomplete: '
                                    + found.get('reason', 'required files are missing'))
            if found['trace_state'] == 'compressed' and not allow_compressed:
                raise ReplayRefused('evidence root is compressed; pass '
                                    '--expand-compressed to replay verified copies')
            replay_root = source
            if found['trace_state'] == 'compressed':
                with recorder.phase('expand_compressed'):
                    replay_root = expand_compressed(source, found, out)
                report['expanded_root'] = str(replay_root)

            with recorder.phase('baseline_reproduction') as baseline:
                with recorder.phase('load_source'):
                    options, groups, metadata = trial.load_source(replay_root)
                baseline['vehicles'] = len(options)
                baseline['od_purpose_groups'] = len(groups)
            # load_source raises unless every input/trace digest matches AND the
            # route-time projection reconstructs the raw entered cells, so reaching
            # this point IS the reproduction of the original measurement.
            report['source_reconstruction_verified'] = True
            report['vehicles'] = len(options)
            report['od_purpose_groups'] = len(groups)
            quarters = metadata['n_intervals']
            targets = {edge: [int(round(value)) for value in series] for edge, series
                       in metadata['sensor_targets']['variants']['edge_shares'].items()}
            edges = sorted(targets)
            context['date'] = metadata.get('date')
            context['input_identity'] = sha256_file(replay_root / 'report.json')
            report['quarters'] = quarters
            report['sensor_edges'] = len(edges)

            with recorder.phase('trace_parsing', arms=len(trial.LEARNING_ARMS)):
                for arm in trial.LEARNING_ARMS:
                    with recorder.phase('parse_entered', arm=arm):
                        passage._parse_entered(
                            replay_root / f'evidence/learning-0-arm-{arm}/edge.xml',
                            quarters, edges)

            bounds, bounds_basis = _retained_bounds(source, variant)
            report['solver_constraints'] = (
                'targets_groups_and_retained_pfe_bounds' if bounds is not None
                else 'targets_and_groups_only')
            report['solver_constraints_reason'] = bounds_basis

            with recorder.phase('system_construction') as construction:
                with recorder.phase('build_passage_system_base'):
                    base = dynamic.build_passage_system(options, edges, quarters)
                if bounds is not None:
                    with recorder.phase('departure_bound_constraints'):
                        dynamic.departure_bound_constraints(base, bounds)
                with recorder.phase('expand_departure_support'):
                    expanded = dynamic.expand_departure_support(
                        options, SHIFT_SUPPORT_S, begin_s=0, end_s=quarters * 900,
                        guard_s=GUARD_S)
                with recorder.phase('build_passage_system_expanded'):
                    system = dynamic.build_passage_system(expanded, edges, quarters)
                construction['base_columns'] = len(options)
                construction['expanded_columns'] = len(expanded)
                construction['sparse_nonzeros'] = int(system.matrix.nnz)
            report['expanded_columns'] = len(expanded)
            report['sparse_nonzeros'] = int(system.matrix.nnz)

            with recorder.phase('solver') as solver:
                with recorder.phase('fit_integer_flows'):
                    fit = dynamic.fit_integer_flows(
                        system, targets, groups, time_limit_s=solver_time_limit_s,
                        departure_bounds=bounds,
                        checkpoint_dir=out / 'solver',
                        cache_dir=out / 'solver-cache')
                solver['status'] = fit.status
            report['fit_status'] = fit.status
            selected = [option for option, count in zip(expanded, fit.counts) if count == 1]
            report['selected_vehicles'] = len(selected)

            with recorder.phase('staging'):
                candidate = automatic_passage._stage_selection(
                    replay_root / 'input', selected, out / 'candidate')

            with recorder.phase('structure_reporting') as structure:
                with recorder.phase('structure_source'):
                    before = calibrated_structure_report(
                        replay_root / 'input/calibrated.rou.xml', pool_path=pool)
                with recorder.phase('structure_candidate'):
                    after = calibrated_structure_report(candidate, pool_path=pool)
                structure['pool_path'] = str(pool) if pool else None
            report['structure_flags_source'] = sorted(
                automatic_passage._structure_flag_names(before or {}))
            report['structure_flags_candidate'] = sorted(
                automatic_passage._structure_flag_names(after or {}))

            with recorder.phase('retention') as retention:
                packed = out / 'retention'
                packed.mkdir()
                copied = shutil.copy2(candidate, packed / 'calibrated.rou.xml')
                with recorder.phase('gzip_verified'):
                    verified = automatic_passage._gzip_verified(Path(copied))
                retention['bytes'] = Path(copied).stat().st_size
                retention['verified'] = verified

            if archive is not None and demand_spec is not None:
                report['archive_validation'] = _validate_archive(
                    archive, demand_spec, recorder, archive_repeats)

            report['selection_sha256'] = sha256_file(out / 'candidate/selection.json')
            report['routes_sha256'] = sha256_file(candidate)
            report['agents_sha256'] = sha256_file(out / 'candidate/calibrated.agents.json')
            report['selection_reproduced'] = _compare_original(source, report)
            root['vehicles'] = len(options)
            report['status'] = 'replayed'

    except ReplayRefused as error:
        report['status'] = 'refused'
        report['reason'] = str(error)
        raise
    except BaseException as error:
        report['status'] = 'failed'
        report['reason'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        # A failed replay still measured real phases; write what was
        # measured, labelled as a failure, instead of losing it.
        report['timing'] = recorder.summary()
        report['date'] = context['date']
        report['input_identity'] = context['input_identity']
        (out / 'replay_report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def _compare_original(source: Path, report: dict) -> dict:
    """Compare the replayed selection with the evidence root's own result.json.

    A pruned root, or one solved under different constraints, cannot be
    expected to reproduce the original bytes; say so instead of failing.
    """
    result_path = source / 'result.json'
    if not result_path.is_file():
        return {'state': 'unavailable', 'reason': 'result.json was pruned from the root'}
    try:
        original = json.loads(result_path.read_text())
    except (OSError, ValueError) as error:
        return {'state': 'unavailable', 'reason': f'result.json is unreadable: {error}'}
    if report['solver_constraints'] != 'targets_groups_and_retained_pfe_bounds':
        return {'state': 'not_comparable',
                'reason': 'replay solved without the retained PFE bounds',
                'original_routes_sha256': original.get('routes_sha256')}
    matches = {key: original.get(key) == report.get(key)
               for key in ('selection_sha256', 'routes_sha256', 'agents_sha256')}
    return {'state': 'identical' if all(matches.values()) else 'differs',
            'fields': matches,
            'note': 'a differing but valid selection is possible when the fit has '
                    'several optimal solutions; it is not evidence of a defect'}


def _phase_walls(report: dict) -> dict[str, float]:
    """Wall time summed per phase NAME within one replay.

    Names repeat (one ``parse_entered`` per arm), so this is a per-name sum
    inside a single sequential replay — never a sum across concurrent work.
    """
    walls: dict[str, float] = {}
    for row in report['timing']['phases']:
        walls[row['phase']] = round(walls.get(row['phase'], 0.0) + row['wall_s'], 6)
    return walls


def _spread(values: list[float]) -> dict:
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / 2)
    return {'n': len(ordered), 'median_s': round(median, 6),
            'min_s': round(ordered[0], 6), 'max_s': round(ordered[-1], 6)}


def profile(source: Path, out: Path, *, repeats: int = 1, **options) -> dict:
    """Replay repeatedly in ONE process so program-cold cost becomes visible.

    The first repeat pays module import, geometry loading and OS cache misses;
    later repeats do not. Reporting them together as one average would hide
    exactly the difference the work contract asks to separate.
    """
    if repeats < 1:
        raise ReplayRefused('at least one replay is required')
    source, out = _resolve_isolation(source, out)
    out.mkdir(parents=True, exist_ok=False)
    runs = [replay(source, out / f'repeat-{index + 1}', **options)
            for index in range(repeats)]
    walls = [_phase_walls(run) for run in runs]
    names = sorted({name for row in walls for name in row})
    summary = {
        'schema_version': SCHEMA_VERSION, 'policy': POLICY,
        'diagnostic_only': True, 'active_production': False,
        'source_evidence_root': str(source), 'output_root': str(out),
        'repeats': repeats,
        'process_state': 'first repeat is program-cold; later repeats reuse this process',
        'runs': [{'repeat': index + 1, 'status': run['status'],
                  'report': str(out / f'repeat-{index + 1}/replay_report.json'),
                  'root_wall_s': run['timing']['root_wall_s'],
                  'residual_s': run['timing']['residual_s'],
                  'phase_wall_s': wall}
                 for index, (run, wall) in enumerate(zip(runs, walls))],
        'first_repeat_phase_wall_s': walls[0],
        'reused_process_phase_wall_s': {
            name: _spread([row[name] for row in walls[1:] if name in row])
            for name in names if len(walls) > 1 and any(name in row for row in walls[1:])},
    }
    (out / 'profile_report.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True,
                        help='one passage evidence variant root, e.g. .../passage/q50')
    parser.add_argument('--out', type=Path, required=True,
                        help='a new directory outside the evidence root')
    parser.add_argument('--label', help='weekday, mixed-pool, boundary, all-hit')
    parser.add_argument('--pool', type=Path, help='candidates.rou.xml for structure flags')
    parser.add_argument('--expand-compressed', action='store_true',
                        help='replay verified copies of a pruned, gzipped root')
    parser.add_argument('--solver-time-limit-s', type=float, default=60)
    parser.add_argument('--archive', type=Path,
                        help='demand archive to time validate_demand_archive against')
    parser.add_argument('--demand-spec', type=Path,
                        help='demand_build_spec.json the archive must satisfy')
    parser.add_argument('--archive-repeats', type=int, default=3)
    parser.add_argument('--repeats', type=int, default=1,
                        help='replay N times in one process to expose cold start')
    parser.add_argument('--inventory-only', action='store_true')
    args = parser.parse_args()
    if args.inventory_only:
        print(json.dumps(inventory(args.source), indent=2))
        return 0
    options = dict(label=args.label, pool=args.pool,
                   allow_compressed=args.expand_compressed,
                   solver_time_limit_s=args.solver_time_limit_s,
                   archive=args.archive, demand_spec=args.demand_spec,
                   archive_repeats=args.archive_repeats)
    if args.archive is not None and args.demand_spec is None:
        print(json.dumps({'status': 'refused',
                          'reason': '--archive requires --demand-spec'}, indent=2))
        return 2
    try:
        if args.repeats > 1:
            report = profile(args.source, args.out, repeats=args.repeats, **options)
            print(json.dumps(report, indent=2))
            return 0 if all(run['status'] == 'replayed' for run in report['runs']) else 2
        report = replay(args.source, args.out, **options)
    except ReplayRefused as error:
        print(json.dumps({'status': 'refused', 'reason': str(error)}, indent=2))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'replayed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
