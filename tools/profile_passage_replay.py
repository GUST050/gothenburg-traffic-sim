"""Read-only phase replay and timing inventory for one passage evidence root.

python3 -m tools.profile_passage_replay \
  --source runs/automatic-passage-<id>/q50 --out runs/<new>

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
from collections import Counter
import contextlib
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import xml.etree.ElementTree as ET

# Run as a script, sys.path[0] is this file's own directory, so the repository
# packages are not importable and `import demand` fails before argparse ever
# runs. Anchor to this file's repository the way build_sumo_demand._source_files
# already requires, rather than making every caller export PYTHONPATH.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np

from demand.structure import calibrated_structure_report, structure_context
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.demand import automatic_passage
from traffic_sim.experimental import dynamic_assignment as dynamic
from traffic_sim.ops import io_phases
from tools import trial_dynamic_passage as trial

SCHEMA_VERSION = 2
POLICY = 'passage_replay_profile_v2'
SHIFT_SUPPORT_S = [-900, -600, -300, 0, 300, 600, 900]
GUARD_S = 60

#: Required by ``trial.load_source``; a root missing any of them is incomplete.
REQUIRED_INPUTS = tuple(f'input/{name}' for name in trial.INPUT_NAMES)
REQUIRED_EVIDENCE = tuple(
    f'evidence/learning-0-arm-{arm}/{name}'
    for arm in trial.LEARNING_ARMS for name in ('edge.xml', 'vehroute.xml'))
REPLAY_CONTRACT = f'input/{automatic_passage.REPLAY_CONTRACT_NAME}'

#: Work this replay cannot account for, named so the residual stays explicit
#: instead of being silently folded into a measured phase.
UNMEASURED_CATEGORIES = {
    'sumo_subprocess': 'replay reads saved traces; no SUMO process is started',
    'learning_and_validation_waves': 'nine SUMO runs are historical, not replayed',
    'publication_and_rollback': 'route/agent replacement is a production-only side effect',
    'archive_validation': 'not requested; pass --archive together with --demand-spec',
    'costing_and_resolver': 'cost-ordered execution needs a monthly workspace, not this root',
}


#: The route and distance machinery behind ``calibrated_structure_report``.
#: Instrumented by name on the module, so a call made through the module's own
#: globals is counted wherever inside the report it happens.
STRUCTURE_CALL_NAMES = (
    '_route_structure_metrics', 'purpose_lengths_km', 'purpose_length_bins',
    'route_od_distance_km', 'gravity_distance_km', 'load_edge_geometry',
)


class CallCostProfiler:
    """Count calls and EXCLUSIVE time for wrapped functions.

    ``_route_structure_metrics`` calls the distance helpers, so adding every
    function's total would count the same seconds several times. Each frame
    reports its own time only: elapsed minus the time its instrumented
    children took.
    """

    def __init__(self):
        self._totals: dict[str, dict] = {}
        self._children_s: list[float] = []

    def wrap(self, name: str, function):
        def measured(*args, **kwargs):
            row = self._totals.setdefault(
                name, {'calls': 0, 'cumulative_s': 0.0, 'exclusive_s': 0.0})
            row['calls'] += 1
            self._children_s.append(0.0)
            began = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - began
                children = self._children_s.pop()
                row['cumulative_s'] += elapsed
                row['exclusive_s'] += elapsed - children
                if self._children_s:
                    self._children_s[-1] += elapsed
        return measured

    def report(self) -> dict:
        # Every instrumented name appears, including the ones never called:
        # a missing row and a zero row mean different things, and only one of
        # them is evidence.
        empty = {'calls': 0, 'cumulative_s': 0.0, 'exclusive_s': 0.0}
        return {name: {'calls': row['calls'],
                       'cumulative_s': round(row['cumulative_s'], 6),
                       'exclusive_s': round(max(row['exclusive_s'], 0.0), 6)}
                for name, row in sorted(
                    (name, self._totals.get(name, empty))
                    for name in STRUCTURE_CALL_NAMES)}


class SolverPhaseProfiler:
    """Exclusive time per solver phase, for the diagnostic hook.

    The same accounting rule as the structure profiler: a phase reports its OWN
    time, elapsed minus whatever instrumented phases ran inside it, so nesting
    can never be added twice.
    """

    def __init__(self):
        self._totals: dict[str, dict] = {}
        self._children_s: list[float] = []

    @contextmanager
    def phase(self, name: str):
        row = self._totals.setdefault(
            name, {'calls': 0, 'cumulative_s': 0.0, 'exclusive_s': 0.0})
        row['calls'] += 1
        self._children_s.append(0.0)
        began = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - began
            children = self._children_s.pop()
            row['cumulative_s'] += elapsed
            row['exclusive_s'] += elapsed - children
            if self._children_s:
                self._children_s[-1] += elapsed

    def report(self) -> dict:
        # Every declared phase, including the ones that did not run: on a cache
        # hit an absent milp_solve IS the evidence, so it must be visible as a
        # zero rather than as a missing key.
        empty = {'calls': 0, 'cumulative_s': 0.0, 'exclusive_s': 0.0}
        return {name: {'calls': row['calls'],
                       'cumulative_s': round(row['cumulative_s'], 6),
                       'exclusive_s': round(max(row['exclusive_s'], 0.0), 6)}
                for name, row in sorted(
                    (name, self._totals.get(name, empty))
                    for name in dynamic.SOLVER_PHASE_NAMES)}


@contextmanager
def measure_solver_phases():
    """Install the private solver hook for one measured fit, then remove it."""
    counter = SolverPhaseProfiler()
    with dynamic._observe_solver_phases(counter):
        yield counter


@contextmanager
def measure_structure_calls():
    """Count the structure report's route/distance work, changing nothing.

    The originals are restored even when the report raises, so an instrumented
    replay leaves the production module exactly as it found it.
    """
    from demand import structure
    counter = CallCostProfiler()
    originals = {name: getattr(structure, name) for name in STRUCTURE_CALL_NAMES}
    try:
        for name, function in originals.items():
            setattr(structure, name, counter.wrap(name, function))
        yield counter
    finally:
        for name, function in originals.items():
            setattr(structure, name, function)


def route_shape_inventory(route_path: Path) -> dict:
    """How many vehicles share how few distinct routes and endpoint pairs.

    This is the quantity step 2 is about: repeated geometry is only worth
    computing once per unique route if the repetition is real. Measured here,
    not cached — no route-facts table exists yet.

    A vehicle may carry its route inline OR reference a shared
    ``<route id=...>``. ``demand.structure`` resolves both and refuses an
    unresolvable one, so an inventory that reads only the inline form would
    under-count exactly the pool files the report handles. The two forms are
    counted separately because which one a file uses is itself a fact about
    the workload.
    """
    root = ET.parse(route_path).getroot()
    named_routes = {route.get('id'): route.get('edges', '')
                    for route in root.iter('route') if route.get('id')}
    edge_tuples: set[tuple[str, ...]] = set()
    endpoints: set[tuple[str, str]] = set()
    vehicles = inline = referenced = 0
    for vehicle in root.iter('vehicle'):
        route = vehicle.find('route')
        if route is not None and route.get('edges'):
            edges_text, is_inline = route.get('edges'), True
        else:
            edges_text, is_inline = named_routes.get(vehicle.get('route'), ''), False
        if not edges_text:
            raise ReplayRefused(
                f'vehicle {vehicle.get("id")!r} in {route_path} has no resolvable '
                'route: neither an inline <route edges=...> nor a reference to a '
                'shared <route id=...> in the same file')
        edges = tuple(edges_text.split())
        vehicles += 1
        inline += is_inline
        referenced += not is_inline
        edge_tuples.add(edges)
        endpoints.add((edges[0], edges[-1]))
    if not vehicles:
        raise ReplayRefused(f'route file has no vehicles: {route_path}')
    return {
        'vehicles': vehicles,
        'inline_route_vehicles': inline,
        'named_reference_vehicles': referenced,
        'named_route_definitions': len(named_routes),
        'unique_edge_tuples': len(edge_tuples),
        'unique_endpoint_pairs': len(endpoints),
        'vehicles_per_unique_edge_tuple': (
            round(vehicles / len(edge_tuples), 3) if edge_tuples else None),
    }


def structure_input_identity() -> dict:
    """Bind a structure measurement to the geometry CONTENT it was taken on.

    Read directly, never through ``load_edge_geometry``: a diagnostic must not
    warm or replace ``demand.structure._EDGE_GEOMETRY_CACHE`` and so change the
    very timings the next phase reports. mtime and size are deliberately not
    the identity — only the bytes and the measured-sensor set are.
    """
    from demand import structure
    path = Path(structure.GEO_PATH)
    if not path.is_file():
        raise ReplayRefused(f'structure geometry is unavailable: {path}')
    with open(path, encoding='utf-8') as handle:
        geometry = json.load(handle)
    sensor_edges = sorted(
        feature['properties']['id'] for feature in geometry.get('features', ())
        if feature.get('geometry', {}).get('type') == 'LineString'
        and feature.get('properties', {}).get('sensor_id'))
    # Convention, fixed so two machines produce the SAME identity for the same
    # sensor set: every sorted id newline-TERMINATED, UTF-8. A bare join gives a
    # different digest for identical edges, which is a content identity that
    # silently fails to compare.
    digest = hashlib.sha256(
        ''.join(f'{edge}\n' for edge in sensor_edges).encode('utf-8')).hexdigest()
    return {
        'geo_path': str(structure.GEO_PATH),
        'geometry_sha256': sha256_file(path),
        'measured_sensor_edges': len(sensor_edges),
        'measured_sensor_edge_identity': digest,
        'basis': ('geometry bytes and newline-terminated sorted measured-sensor '
                  'edge ids; never mtime or size'),
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


def _overlaps(path: Path, source: Path) -> bool:
    """True when writing to ``path`` could touch the evidence being read."""
    return path == source or source in path.parents or path in source.parents


def _resolve_isolation(source: Path, out: Path) -> tuple[Path, Path]:
    """Refuse any output path that would write into the evidence being read."""
    source = Path(source).resolve()
    out = Path(out).resolve()
    if _overlaps(out, source):
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
    contract_expected = digests.get(automatic_passage.REPLAY_CONTRACT_NAME)
    contract_path = source / REPLAY_CONTRACT
    if contract_path.is_file():
        contract_actual = sha256_file(contract_path)
        contract_state = ('raw' if contract_expected == contract_actual else
                          'raw_unrecorded' if not contract_expected else
                          'raw_hash_mismatch')
    else:
        contract_state = 'missing'
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
        'replay_contract': {
            'path': REPLAY_CONTRACT,
            'state': contract_state,
            'expected_sha256': contract_expected,
        },
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
    contract = inventory_report['replay_contract']
    if contract['state'] == 'raw':
        target = root / REPLAY_CONTRACT
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / REPLAY_CONTRACT, target)
    return root


def _load_replay_contract(source: Path, quarters: int) -> list[dict]:
    """Load exact saved solver inputs and require the producing code identity."""
    contract_path = source / REPLAY_CONTRACT
    if not contract_path.is_file():
        raise ReplayRefused(
            f'{REPLAY_CONTRACT} is missing; use --preparation-only for legacy evidence')
    try:
        contract = json.loads(contract_path.read_text())
    except (OSError, ValueError) as error:
        raise ReplayRefused(f'unreadable replay contract: {error}') from error
    if contract.get('schema_version') != 1 or contract.get('policy') != automatic_passage.POLICY:
        raise ReplayRefused('replay contract schema or passage policy differs')
    if contract.get('source_sha256') != automatic_passage.replay_source_sha256():
        raise ReplayRefused('production source differs from the code that created the evidence')
    bounds = contract.get('retained_bounds_pq')
    if not isinstance(bounds, list) or len(bounds) != quarters \
            or any(not isinstance(row, dict) for row in bounds):
        raise ReplayRefused('replay contract has invalid retained per-quarter bounds')
    return bounds


def _validate_archive(archive: Path, spec_path: Path, recorder: PhaseRecorder,
                      repeats: int) -> dict:
    """Time ``validate_demand_archive`` first-call against reused-process calls.

    The import is local: this tool must stay a leaf of the demand graph, and
    the monthly resolver is not needed to replay a passage root.
    """
    if repeats < 1:
        raise ReplayRefused('at least one archive validation is required')
    from traffic_sim.simulation.monthly_demand import (DemandBuildSpec,
                                                       validate_demand_archive)
    try:
        required = DemandBuildSpec.from_dict(json.loads(Path(spec_path).read_text()))
    except (OSError, TypeError, ValueError) as error:
        raise ReplayRefused(f'invalid demand build spec: {error}') from error
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
            'call_basis': ('all calls occur after module imports; later calls may reuse '
                           'validation caches in this process')}


def profile_archive_validation(archive: Path, spec_path: Path, out: Path,
                               *, repeats: int = 3) -> dict:
    """Measure archive validation independently from passage variant replay."""
    archive = Path(archive).resolve()
    spec_path = Path(spec_path).resolve()
    out = Path(out).resolve()
    if not archive.is_dir():
        raise ReplayRefused(f'demand archive does not exist: {archive}')
    if not spec_path.is_file():
        raise ReplayRefused(f'demand build spec does not exist: {spec_path}')
    if archive == out or archive in out.parents or out in archive.parents:
        raise ReplayRefused(f'output {out} must be outside demand archive {archive}')
    out.mkdir(parents=True, exist_ok=False)
    recorder = PhaseRecorder({'variant': None, 'date': None, 'input_identity': None})
    report = {
        'schema_version': SCHEMA_VERSION,
        'policy': POLICY,
        'diagnostic_only': True,
        'active_production': False,
        'status': 'refused',
        'archive': str(archive),
        'demand_spec': str(spec_path),
        'output_root': str(out),
    }
    try:
        with recorder.phase('archive_validation_profile'):
            report['archive_validation'] = _validate_archive(
                archive, spec_path, recorder, repeats)
        if any(call != 'valid' for call in report['archive_validation']['calls']):
            raise ReplayRefused('demand archive validation was rejected')
        report['status'] = 'profiled_archive_validation'
    except ReplayRefused as error:
        report['status'] = 'refused'
        report['reason'] = str(error)
        raise
    except BaseException as error:
        report['status'] = 'failed'
        report['reason'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        report['timing'] = recorder.summary()
        (out / 'archive_validation_report.json').write_text(
            json.dumps(report, indent=2) + '\n')
    return report


def _solver_checkpoint_state(checkpoint_dir: Path) -> dict:
    """Read the solver's OWN record of whether it reused this exact request.

    Production hits this cache, so a profile that cannot say whether its own
    solve was a hit is measuring an unknown workload. Fail closed instead.
    """
    path = Path(checkpoint_dir) / 'state.json'
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ReplayRefused(f'unreadable solver checkpoint state: {error}') from error
    cache_hit = state.get('cache_hit')
    if not isinstance(cache_hit, bool):
        raise ReplayRefused(
            f'solver checkpoint cache_hit is not a boolean: {cache_hit!r}')
    key = state.get('key')
    if not isinstance(key, str) or not key:
        raise ReplayRefused('solver checkpoint state carries no request key')
    return {'cache_hit': cache_hit, 'key': key}


def replay(source: Path, out: Path, *, label: str | None = None,
           pool: Path | None = None, allow_compressed: bool = False,
           solver_time_limit_s: float = 60, archive: Path | None = None,
           demand_spec: Path | None = None, archive_repeats: int = 3,
           preparation_only: bool = False, io_phase_measurement: bool = False,
           _solver_cache_dir: Path | None = None) -> dict:
    """Replay preparation, solving, staging and structure over saved traces."""
    source, out = _resolve_isolation(source, out)
    # Checked by the call that will WRITE it, before any directory is created:
    # profile() guards the cache it makes, but a direct replay() accepts one
    # from its caller and must not be the hole in that guarantee. Ownership is
    # checked as well as overlap — the cache belongs to THIS profile's layout,
    # profile-root/solver-cache beside profile-root/repeat-N, so it cannot be
    # aimed at an unrelated directory the profile does not own.
    if _solver_cache_dir is not None:
        _solver_cache_dir = Path(_solver_cache_dir).resolve()
        if _overlaps(_solver_cache_dir, source):
            raise ReplayRefused(
                f'solver cache {_solver_cache_dir} must be outside '
                f'the source evidence root {source}')
        if _solver_cache_dir.parent != out.parent:
            raise ReplayRefused(
                f'solver cache {_solver_cache_dir} must sit beside the replay '
                f'output {out}, under the same profile root {out.parent}')
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
    # Diagnostic only, and off unless asked for. The stack is closed in the
    # same finally that writes the report, so an observer cannot outlive this
    # call however the replay ends.
    io_stack = contextlib.ExitStack()
    io_collector = None
    if io_phase_measurement:
        io_collector = io_phases.PhaseCollector(
            unmeasured_categories=sorted(UNMEASURED_CATEGORIES))
        io_stack.enter_context(io_phases.observe(io_collector))
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
            if not preparation_only and found['replay_contract']['state'] != 'raw':
                raise ReplayRefused(
                    f'{REPLAY_CONTRACT} is {found["replay_contract"]["state"]}; '
                    'use --preparation-only for legacy evidence')
            replay_root = source
            if found['trace_state'] == 'compressed':
                with recorder.phase('expand_compressed'):
                    replay_root = expand_compressed(source, found, out)
                report['expanded_root'] = str(replay_root)

            with recorder.phase('baseline_reproduction') as baseline:
                with recorder.phase('load_source'):
                    verified = trial.load_verified_source(replay_root)
                options = list(verified.options)
                groups, metadata = dict(verified.groups), verified.metadata
                baseline['vehicles'] = len(options)
                baseline['od_purpose_groups'] = len(groups)
            # This verifies the saved learning measurement only. Exact output
            # reproduction is a separate, fail-closed digest comparison below.
            report['measurement_reconstruction_verified'] = True
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

            bounds = None if preparation_only else _load_replay_contract(replay_root, quarters)
            report['solver_constraints'] = (
                'not_loaded_preparation_only' if preparation_only
                else 'targets_groups_and_retained_pfe_bounds')

            with recorder.phase('system_construction') as construction:
                # Production reuses the system load_source already proved
                # against the raw entered cells; mirror that here so the phase
                # rows stay comparable with the builder's own timings.
                with recorder.phase('build_passage_system_base') as built:
                    base = verified.system
                    if base.sensors != tuple(edges) or base.n_intervals != quarters:
                        raise ReplayRefused(
                            'verified passage system does not match the saved targets')
                    built['source'] = 'reused_from_load_source'
                if bounds is not None:
                    with recorder.phase('departure_bound_constraints'):
                        matrix, lower, upper = dynamic.departure_bound_constraints(base, bounds)
                        counts = matrix @ np.ones(len(options))
                        if np.any(counts < lower) or np.any(counts > upper):
                            raise ReplayRefused(
                                'source violates the retained PFE structural bounds')
                with recorder.phase('expand_departure_support'):
                    expanded = dynamic._expand_departure_support_verified(
                        base, SHIFT_SUPPORT_S, begin_s=0, end_s=quarters * 900,
                        guard_s=GUARD_S)
                with recorder.phase('build_passage_system_expanded'):
                    system = dynamic.build_passage_system(expanded, edges, quarters)
                construction['base_columns'] = len(options)
                construction['expanded_columns'] = len(expanded)
                construction['sparse_nonzeros'] = int(system.matrix.nnz)
            report['expanded_columns'] = len(expanded)
            report['sparse_nonzeros'] = int(system.matrix.nnz)

            if preparation_only:
                if archive is not None and demand_spec is not None:
                    report['archive_validation'] = _validate_archive(
                        archive, demand_spec, recorder, archive_repeats)
                    if any(call != 'valid' for call in report['archive_validation']['calls']):
                        raise ReplayRefused('demand archive validation was rejected')
                report['selection_reproduced'] = {
                    'state': 'not_evaluated',
                    'reason': 'preparation-only mode does not solve or stage a selection',
                }
                root['vehicles'] = len(options)
                report['status'] = 'profiled_preparation_only'
                return report

            original = _load_original_result(source)
            repair = original.get('structural_repair') or {}
            if repair.get('boundary_fallback') or int(repair.get('passes', 0)) != 0:
                raise ReplayRefused(
                    'saved result used boundary fallback or structural repair; '
                    'this replay path cannot reproduce that solver branch')

            source_route = replay_root / 'input/calibrated.rou.xml'
            with recorder.phase('route_shape_inventory', route='source') as shape:
                source_shape = route_shape_inventory(source_route)
                shape.update(source_shape)
            # ONE context for this replay, exactly as _refine holds one per
            # calibration: the source and candidate reports share the pool's
            # facts and the network-wide baseline instead of each rebuilding it.
            structure_ctx = structure_context()
            with measure_structure_calls() as source_calls, \
                    recorder.phase('structure_source'):
                before = calibrated_structure_report(
                    source_route, pool_path=pool, _context=structure_ctx)
            if before is None:
                raise ReplayRefused('source structural report is unavailable')

            # A profile of repeats shares ONE cache created empty for the
            # profile, so repeat 1 is cold and later repeats reproduce the
            # cache hit production itself gets. A lone replay keeps a private
            # cache under its own output.
            cache_root = (_solver_cache_dir if _solver_cache_dir is not None
                          else out / 'solver-cache')
            cache_root.mkdir(parents=True, exist_ok=True)
            with recorder.phase('solver') as solver:
                with measure_solver_phases() as solver_phases, \
                        recorder.phase('fit_integer_flows'):
                    fit = dynamic.fit_integer_flows(
                        system, targets, groups, time_limit_s=solver_time_limit_s,
                        departure_bounds=bounds,
                        checkpoint_dir=out / 'solver',
                        cache_dir=cache_root)
                solver['status'] = fit.status
            checkpoint = _solver_checkpoint_state(out / 'solver')
            phases = solver_phases.report()
            report['solver_measurement'] = {
                'basis': ('observational; measured outside every demand, passage '
                          'and solver fingerprint'),
                'solver_cache_hit': checkpoint['cache_hit'],
                'milp_executed': phases['milp_solve']['calls'] > 0,
                'phases': phases,
            }
            solver['cache_hit'] = checkpoint['cache_hit']
            report['solver_cache_root'] = str(cache_root)
            report['solver_cache_hit'] = checkpoint['cache_hit']
            report['solver_request_key'] = checkpoint['key']
            report['fit_status'] = fit.status
            selected = [option for option, count in zip(expanded, fit.counts) if count == 1]
            report['selected_vehicles'] = len(selected)
            if Counter(option.group for option in selected) != Counter(groups) \
                    or len(selected) != len(options):
                raise ReplayRefused('replay changed the OD/purpose population')

            with recorder.phase('staging'):
                candidate = automatic_passage._stage_selection(
                    replay_root / 'input', selected, out / 'candidate')

            with recorder.phase('route_shape_inventory', route='candidate') as shape:
                candidate_shape = route_shape_inventory(candidate)
                shape.update(candidate_shape)
            with measure_structure_calls() as candidate_calls, \
                    recorder.phase('structure_candidate',
                                   pool_path=str(pool) if pool else None):
                after = calibrated_structure_report(
                    candidate, pool_path=pool, _context=structure_ctx)
            if after is None:
                raise ReplayRefused('candidate structural report is unavailable')
            report['structure_measurement'] = {
                'basis': ('observational; measured outside every semantic '
                          'fingerprint and never fed back into a report'),
                'route_facts_cache': 'operation_scoped_content_bound',
                'structure_context': {
                    'geometry_sha256': structure_ctx.geometry_sha256,
                    'sensor_identity': structure_ctx.sensor_identity,
                    'scope': 'one context per replay, shared by source and candidate',
                },
                'input_identity': structure_input_identity(),
                'source_route': source_shape,
                'candidate_route': candidate_shape,
                'structure_source_calls': source_calls.report(),
                'structure_candidate_calls': candidate_calls.report(),
            }
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
                if any(call != 'valid' for call in report['archive_validation']['calls']):
                    raise ReplayRefused('demand archive validation was rejected')

            report['selection_sha256'] = sha256_file(out / 'candidate/selection.json')
            report['routes_sha256'] = sha256_file(candidate)
            report['agents_sha256'] = sha256_file(out / 'candidate/calibrated.agents.json')
            report['selection_reproduced'] = _compare_original(original, report)
            if report['selection_reproduced']['state'] != 'identical':
                raise ReplayRefused('replayed route/departure selection differs from the saved result')
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
        io_stack.close()
        if io_collector is not None:
            report['io_measurement'] = io_collector.report()
            report['io_measurement']['basis'] = (
                'observational; outside every demand, passage, solver and '
                'replay identity. Concurrent parents report exclusive_s null '
                'and their measured region wall; descendants remain detail '
                'with zero additional wall contribution.')
        report['timing'] = recorder.summary()
        report['date'] = context['date']
        report['input_identity'] = context['input_identity']
        (out / 'replay_report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def _load_original_result(source: Path) -> dict:
    """Require the saved output that makes a solver replay comparable."""
    result_path = source / 'result.json'
    if not result_path.is_file():
        raise ReplayRefused('result.json is missing; exact selection comparison is unavailable')
    try:
        result = json.loads(result_path.read_text())
    except (OSError, ValueError) as error:
        raise ReplayRefused(f'result.json is unreadable: {error}') from error
    if result.get('status') != 'validated':
        raise ReplayRefused('saved passage result is not validated')
    return result


def _compare_original(original: dict, report: dict) -> dict:
    """Compare the replayed selection with the loaded saved result.

    Exact bytes are required by this performance-only experiment.
    """
    matches = {key: original.get(key) == report.get(key)
               for key in ('selection_sha256', 'routes_sha256', 'agents_sha256')}
    return {'state': 'identical' if all(matches.values()) else 'differs',
            'fields': matches}


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


def _io_phase_walls(run: dict) -> dict:
    """One run's measured I/O phases, as wall contribution plus bytes.

    A concurrent boundary contributes its measured region wall. Descendants
    remain visible as diagnostic thread time but contribute zero additional
    ranked wall time.
    """
    measurement = run.get('io_measurement') or {}
    result = {}
    for name, entry in (measurement.get('phases') or {}).items():
        wall = entry['wall_contribution_s']
        result[name] = {
            'wall_s': wall,
            'calls': entry['calls'],
            'concurrent': entry['concurrent'],
            'basis': next(
                (row['basis'] for row in measurement.get('ranking', [])
                 if row['phase'] == name),
                'concurrent_detail_only'),
            'bytes': entry['bytes'],
        }
    return result


def profile(source: Path, out: Path, *, repeats: int = 1, **options) -> dict:
    """Replay repeatedly in one already-imported process.

    The first repeat can pay lazy caches and filesystem cache misses. Module
    imports happen before this function and are therefore outside these clocks.
    """
    if repeats < 1:
        raise ReplayRefused('at least one replay is required')
    source, out = _resolve_isolation(source, out)
    out.mkdir(parents=True, exist_ok=False)
    # One empty cache for the whole profile: repeat 1 pays the cold solve and
    # later repeats reproduce production's cache hit. It lives under the
    # profile output, never in or around the evidence being read.
    cache_root = out / 'solver-cache'
    if _overlaps(cache_root, source):
        raise ReplayRefused(
            f'shared solver cache {cache_root} must be outside {source}')
    cache_root.mkdir(parents=True, exist_ok=False)
    runs = [replay(source, out / f'repeat-{index + 1}',
                   _solver_cache_dir=cache_root, **options)
            for index in range(repeats)]
    walls = [_phase_walls(run) for run in runs]
    names = sorted({name for row in walls for name in row})
    summary = {
        'schema_version': SCHEMA_VERSION, 'policy': POLICY,
        'diagnostic_only': True, 'active_production': False,
        'source_evidence_root': str(source), 'output_root': str(out),
        'repeats': repeats,
        'process_state': ('all repeats run after module imports; the first may populate lazy '
                          'and filesystem caches, later repeats reuse this process and the '
                          'shared solver cache this profile created empty'),
        'solver_cache_root': str(cache_root),
        'solver_cache_basis': 'empty_output_local_cache_then_shared_across_repeats',
        'runs': [{'repeat': index + 1, 'status': run['status'],
                  'report': str(out / f'repeat-{index + 1}/replay_report.json'),
                  'root_wall_s': run['timing']['root_wall_s'],
                  'residual_s': run['timing']['residual_s'],
                  'solver_cache_hit': run.get('solver_cache_hit'),
                  'solver_request_key': run.get('solver_request_key'),
                  'structure_measurement': run.get('structure_measurement'),
                  'solver_measurement': run.get('solver_measurement'),
                  'phase_wall_s': wall}
                 for index, (run, wall) in enumerate(zip(runs, walls))],
        'first_repeat_phase_wall_s': walls[0],
        'reused_process_phase_wall_s': {
            name: _spread([row[name] for row in walls[1:] if name in row])
            for name in names if len(walls) > 1 and any(name in row for row in walls[1:])},
    }
    io_walls = [_io_phase_walls(run) for run in runs]
    if any(io_walls):
        io_names = sorted({name for row in io_walls for name in row})
        summary['first_repeat_io_phase_s'] = io_walls[0]
        summary['reused_process_io_phase_s'] = {
            name: _spread([row[name]['wall_s'] for row in io_walls[1:]
                           if name in row])
            for name in io_names
            if len(io_walls) > 1 and any(name in row for row in io_walls[1:])}
        # Rank on the reused repeats when they exist: production hits a warm
        # process, and the first repeat also pays lazy imports and page cache.
        ranked_source = (summary['reused_process_io_phase_s']
                         or {name: {'median_s': entry['wall_s']}
                             for name, entry in io_walls[0].items()})
        def _median(entry):
            return entry['median_s'] if isinstance(entry, dict) else entry
        ranked_source = {
            name: entry for name, entry in ranked_source.items()
            if _median(entry) > 0
        }
        total = sum(_median(entry) for entry in ranked_source.values()) or 1.0
        summary['io_phase_ranking'] = [
            {'phase': name,
             'wall_s': round(_median(entry), 6),
             'share_percent': round(_median(entry) / total * 100, 2),
             'basis': io_walls[0].get(name, {}).get('basis', 'exclusive'),
             'bytes': io_walls[0].get(name, {}).get('bytes', {})}
            for name, entry in sorted(ranked_source.items(),
                                      key=lambda item: -_median(item[1]))]
        summary['io_measurement_basis'] = (
            'observational; outside every demand, passage, solver and replay '
            'identity. Ranked on the reused repeats; a concurrent boundary '
            'contributes its measured region wall and descendants add zero.')
    (out / 'profile_report.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        help='one variant root, e.g. runs/automatic-passage-<id>/q50')
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
    parser.add_argument('--archive-only', action='store_true',
                        help='time archive validation without coupling it to a passage replay')
    parser.add_argument('--repeats', type=int, default=1,
                        help='replay N times after imports to expose reusable process caches')
    parser.add_argument('--preparation-only', action='store_true',
                        help='profile parsing/system construction on legacy evidence; do not solve')
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--retention-root', type=Path,
                        help='measure prune_evidence on an owned copy of this '
                             'finished multi-variant evidence root')
    parser.add_argument('--io-phases', action='store_true',
                        help='also measure evidence I/O phases: reads, gzip, '
                             'writes, hashing, verification and publication')
    args = parser.parse_args()
    if args.archive_only:
        if args.archive is None or args.demand_spec is None:
            print(json.dumps({'status': 'refused', 'reason':
                              '--archive-only requires --archive and --demand-spec'}, indent=2))
            return 2
        try:
            report = profile_archive_validation(
                args.archive, args.demand_spec, args.out,
                repeats=args.archive_repeats)
        except ReplayRefused as error:
            print(json.dumps({'status': 'refused', 'reason': str(error)}, indent=2))
            return 2
        print(json.dumps(report, indent=2))
        return 0
    if args.retention_root is not None:
        try:
            report = profile_retention(args.retention_root, args.out,
                                       repeats=args.repeats)
        except ReplayRefused as error:
            print(json.dumps({'status': 'refused', 'reason': str(error)},
                             indent=2))
            return 2
        print(json.dumps(report['first_repeat']['ranking'], indent=2))
        return 0 if report['source_unchanged_after_every_repeat'] else 1
    if args.source is None:
        print(json.dumps(
            {'status': 'refused',
             'reason': '--source is required unless --archive-only or '
                       '--retention-root is used'}, indent=2))
        return 2
    if args.inventory_only:
        print(json.dumps(inventory(args.source), indent=2))
        return 0
    options = dict(label=args.label, pool=args.pool,
                   allow_compressed=args.expand_compressed,
                   solver_time_limit_s=args.solver_time_limit_s,
                   archive=args.archive, demand_spec=args.demand_spec,
                   archive_repeats=args.archive_repeats,
                   preparation_only=args.preparation_only,
                   io_phase_measurement=args.io_phases)
    if args.archive is not None and args.demand_spec is None:
        print(json.dumps({'status': 'refused',
                          'reason': '--archive requires --demand-spec'}, indent=2))
        return 2
    try:
        if args.repeats > 1:
            report = profile(args.source, args.out, repeats=args.repeats, **options)
            print(json.dumps(report, indent=2))
            accepted = {'replayed', 'profiled_preparation_only'}
            return 0 if all(run['status'] in accepted for run in report['runs']) else 2
        report = replay(args.source, args.out, **options)
    except ReplayRefused as error:
        print(json.dumps({'status': 'refused', 'reason': str(error)}, indent=2))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report['status'] in {'replayed', 'profiled_preparation_only'} else 2



RETENTION_UNMEASURED = [
    'sumo_subprocess: retention runs after every simulation has finished',
    'demand_build: this mode measures retention alone, never a calibration',
    'filesystem_cache_state: the first repeat pays cold page cache for a copy '
    'this tool just wrote, which is not the state a finished build meets',
]


def _retention_fingerprint(root: Path) -> dict:
    """Every file under ``root`` by relative path, size and digest."""
    return {str(path.relative_to(root)): (path.stat().st_size,
                                          sha256_file(path))
            for path in sorted(Path(root).rglob('*')) if path.is_file()}


def _decompressed_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _retention_repeat(root: Path, original: dict, work: Path,
                      index: int) -> dict:
    """One measured retention pass over an owned copy of ``root``.

    The copy is timed and reported SEPARATELY: it is this tool's own cost, not
    retention's, and folding it in would overstate the very thing being
    measured. Nothing is hard-linked, so compressing the copy cannot reach the
    original through a shared inode.
    """
    copy = work / 'copy'
    copy_started = time.perf_counter()
    shutil.copytree(root, copy)
    copy_s = time.perf_counter() - copy_started

    files = [path for path in copy.rglob('*') if path.is_file()]
    hardlink_free = all(path.stat().st_nlink == 1 for path in files)
    raw_before = sum(1 for path in files if path.suffix == '.xml')
    bytes_before = sum(path.stat().st_size for path in files)

    collector = io_phases.PhaseCollector(
        unmeasured_categories=RETENTION_UNMEASURED)
    started = time.perf_counter()
    with io_phases.observe(collector):
        with io_phases.phase('retention_root'):
            automatic_passage.prune_evidence(copy)
    wall = time.perf_counter() - started
    measurement = collector.report(root='retention_root')

    after = [path for path in copy.rglob('*') if path.is_file()]
    raw_after = sum(1 for path in after if path.suffix == '.xml')
    packed = [path for path in after if path.suffix == '.gz']
    bytes_after = sum(path.stat().st_size for path in after)

    # Every compressed file must still be the original bytes. Checked against
    # the ORIGINAL root, not against the copy it was made from, so a copy that
    # was already wrong cannot certify itself.
    mismatches, verified = [], 0
    for path in packed:
        relative = str(path.relative_to(copy))[:-len('.gz')]
        expected = original.get(relative)
        if expected is None:
            mismatches.append({'file': relative, 'reason': 'absent from source'})
            continue
        if _decompressed_digest(path) != expected[1]:
            mismatches.append({'file': relative, 'reason': 'digest differs'})
            continue
        verified += 1

    result = {
        'repeat': index + 1,
        'copy_s': round(copy_s, 6),
        'copy_is_hardlink_free': hardlink_free,
        'retention_root_wall_s': round(wall, 6),
        'phases': measurement['phases'],
        'ranking': measurement['ranking'],
        'residual_s': measurement['residual_s'],
        'raw_xml_files_before': raw_before,
        'raw_xml_files_after': raw_after,
        'compressed_files_after': len(packed),
        'bytes': {key: value for name in measurement['phases']
                  for key, value in measurement['phases'][name]['bytes'].items()},
        'bytes_on_disk_before': bytes_before,
        'bytes_on_disk_after': bytes_after,
        'compression_ratio': round(bytes_after / bytes_before, 6)
        if bytes_before else None,
        'gz_verified_against_original': verified,
        'gz_digest_mismatches': mismatches,
        'candidate_dirs_removed': not list(copy.glob('*/candidate*')),
        'rollback_files_removed': not list(copy.glob('original-*')),
        'source_reports_removed': not (copy / 'source_reports.json').exists(),
        'source_unchanged': _retention_fingerprint(root) == original,
    }
    # Aggregate the byte counters across phases rather than per phase, so the
    # summary answers "how many bytes moved" without hiding where.
    totals: dict[str, int] = {}
    for entry in measurement['phases'].values():
        for key, value in entry['bytes'].items():
            totals[key] = totals.get(key, 0) + value
    result['bytes'] = totals
    return result


def profile_retention(root: Path, out: Path, *, repeats: int = 3) -> dict:
    """Measure the real prune_evidence path on an owned copy of ``root``.

    A single-variant replay never runs retention: it compresses one copied
    file directly. This exercises the whole contract -- inventory, the thread
    pool, per-file verification, cleanup -- on a real three-variant root,
    without building anything and without touching the original.
    """
    root = Path(root).resolve()
    out = Path(out).resolve()
    if _overlaps(out, root):
        raise ReplayRefused(
            f'retention output {out} must be outside the evidence root {root}')
    if repeats < 1:
        raise ReplayRefused('at least one repeat is required')
    out.mkdir(parents=True, exist_ok=True)

    original = _retention_fingerprint(root)
    runs_out = []
    for index in range(repeats):
        work = out / f'repeat-{index + 1}'
        work.mkdir(parents=True, exist_ok=False)
        try:
            runs_out.append(_retention_repeat(root, original, work, index))
        finally:
            # Only this tool's own copy is removed, and only after its numbers
            # are in hand.
            shutil.rmtree(work / 'copy', ignore_errors=True)

    def spread(key):
        return _spread([run[key] for run in runs_out[1:]]) if len(runs_out) > 1 else {}

    report = {
        'schema_version': 1,
        'policy': 'passage_retention_profile_v1',
        'diagnostic_only': True,
        'active_production': False,
        'release_evidence': False,
        'source_evidence_root': str(root),
        'output_root': str(out),
        'repeats': repeats,
        'process_state': ('all repeats run in one process after imports; each '
                          'gets its own fresh copy and its own collector'),
        'source_inventory': {
            'files': len(original),
            'bytes': sum(size for size, _digest in original.values()),
            'raw_xml_files': sum(1 for name in original
                                 if name.endswith('.xml')),
            'raw_xml_bytes': sum(size for name, (size, _d) in original.items()
                                 if name.endswith('.xml')),
        },
        'source_unchanged_after_every_repeat': all(
            run['source_unchanged'] for run in runs_out),
        'runs': runs_out,
        'first_repeat': runs_out[0],
        'reused_process': {
            'retention_root_wall_s': spread('retention_root_wall_s'),
            'copy_s': spread('copy_s'),
        },
        'unmeasured_categories': RETENTION_UNMEASURED,
    }
    (out / 'retention_profile.json').write_text(
        json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    raise SystemExit(main())
