"""Replayable numeric passage solves; cache hits never replace SUMO validation.

Requests include the complete sparse problem, including retained PFE bounds.
Only optimal, integer-feasible numeric results are reused. Time-limited requests
remain on disk for replay; this does not serialize HiGHS' branch-and-bound tree.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, OptimizeResult
from scipy.sparse import csr_matrix, vstack


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, allow_nan=False)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _pack(kwargs):
    constraints = kwargs['constraints']
    matrix = vstack([item.A for item in constraints], format='csr')
    n = len(kwargs['c'])
    return {
        'c': np.asarray(kwargs['c'], dtype=float),
        'integrality': np.asarray(kwargs['integrality'], dtype=np.int64),
        'lower': np.broadcast_to(kwargs['bounds'].lb, (n,)).copy(),
        'upper': np.broadcast_to(kwargs['bounds'].ub, (n,)).copy(),
        'data': matrix.data, 'indices': matrix.indices, 'indptr': matrix.indptr,
        'shape': np.asarray(matrix.shape, dtype=np.int64),
        'row_lower': np.concatenate([np.broadcast_to(item.lb, (item.A.shape[0],))
                                     for item in constraints]),
        'row_upper': np.concatenate([np.broadcast_to(item.ub, (item.A.shape[0],))
                                     for item in constraints]),
        'options': np.asarray(json.dumps(kwargs['options'], sort_keys=True)),
    }


def read_request(path):
    """Load an exact numeric request without pickle or project input mutation."""
    with np.load(path, allow_pickle=False) as raw:
        arrays = {key: raw[key] for key in raw.files}
    matrix = csr_matrix((arrays['data'], arrays['indices'], arrays['indptr']),
                        shape=tuple(arrays['shape']))
    return dict(c=arrays['c'], integrality=arrays['integrality'],
                bounds=Bounds(arrays['lower'], arrays['upper']),
                constraints=[LinearConstraint(matrix, arrays['row_lower'], arrays['row_upper'])],
                options=json.loads(str(arrays['options'])))


def _feasible(result, kwargs):
    if result.x is None:
        return False
    x = np.asarray(result.x)
    if x.shape != np.shape(kwargs['c']) or not np.all(np.isfinite(x)):
        return False
    integer = np.asarray(kwargs['integrality']) != 0
    if np.any(abs(x[integer] - np.rint(x[integer])) > 1e-6):
        return False
    x = x.copy()
    x[integer] = np.rint(x[integer])
    if np.any(x < kwargs['bounds'].lb) or np.any(x > kwargs['bounds'].ub):
        return False
    for item in kwargs['constraints']:
        projected = item.A @ x
        if np.any(projected < item.lb) or np.any(projected > item.ub):
            return False
    return True


def solve_checkpointed(solve, directory, cache_root, **kwargs):
    """Persist all inputs and reuse only a checked solution to this exact model."""
    directory, cache_root = Path(directory), Path(cache_root)
    directory.mkdir(parents=True, exist_ok=True)
    arrays = _pack(kwargs)
    digest = hashlib.sha256()
    for key, value in sorted(arrays.items()):
        # An optimal solution remains usable under a different time budget.
        if key == 'options':
            options = dict(kwargs['options'])
            options.pop('time_limit', None)
            value = np.asarray(json.dumps(options, sort_keys=True))
        digest.update(key.encode())
        digest.update(str(value.dtype).encode())
        digest.update(str(value.shape).encode())
        digest.update(value.tobytes())
    digest.update(f'{np.__version__}:{scipy.__version__}'.encode())
    digest.update(Path(__file__).read_bytes())
    digest.update((Path(__file__).parents[1] / 'experimental/dynamic_assignment.py').read_bytes())
    key = digest.hexdigest()
    request = directory / 'request.npz'
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
        np.savez(handle, **arrays)
    try:
        os.replace(temporary, request)
    finally:
        temporary.unlink(missing_ok=True)
    state = {'key': key, 'status': 'running', 'cache_hit': False,
             'columns': len(kwargs['c']), 'rows': len(arrays['row_lower'])}
    _atomic_json(directory / 'state.json', state)
    cached_path = cache_root / key / 'result.json'
    started, cpu = time.perf_counter(), time.process_time()
    result = None
    try:
        cached = json.loads(cached_path.read_text())
        payload = cached['result']
        checksum = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        candidate = OptimizeResult(**payload)
        candidate.x = np.asarray(candidate.x, dtype=float)
        if cached['key'] == key and cached['sha256'] == checksum \
                and candidate.status == 0 and _feasible(candidate, kwargs):
            result = candidate
            state['cache_hit'] = True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    if result is None:
        try:
            result = solve(**kwargs)
        except BaseException as exc:
            state.update(status='interrupted', error=f'{type(exc).__name__}: {exc}')
            _atomic_json(directory / 'state.json', state)
            raise
        if result.status == 0 and _feasible(result, kwargs):
            payload = {'x': np.asarray(result.x).tolist(), 'status': int(result.status),
                       'message': str(result.message)}
            checksum = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            _atomic_json(cached_path, {'key': key, 'sha256': checksum, 'result': payload})
    status = {0: 'optimal', 1: 'time_limit', 2: 'infeasible'}.get(result.status, 'solver_error')
    if result.status == 0 and not _feasible(result, kwargs):
        status = 'invalid_incumbent'
    state.update(status=status, has_incumbent=result.x is not None,
                 wall_s=time.perf_counter()-started, cpu_s=time.process_time()-cpu,
                 message=str(result.message))
    _atomic_json(directory / 'state.json', state)
    return result


def main(argv=None):
    """Replay one numeric fit in a new directory, without running SUMO."""
    import argparse
    import warnings
    from scipy.optimize import milp

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--time-limit', type=float)
    args = parser.parse_args(argv)
    kwargs = read_request(args.request)
    if args.time_limit is not None:
        if not np.isfinite(args.time_limit) or args.time_limit <= 0:
            parser.error('--time-limit must be finite and positive')
        kwargs['options']['time_limit'] = args.time_limit
    # A replay must never overwrite the original request or earlier evidence.
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message=r"Unrecognized options detected: .*'threads'.*",
                                category=Warning)
        result = solve_checkpointed(milp, args.output_dir, args.output_dir/'cache', **kwargs)
    print((args.output_dir/'state.json').read_text())
    return 0 if result.status == 0 and _feasible(result, kwargs) else 1


if __name__ == '__main__':
    raise SystemExit(main())
