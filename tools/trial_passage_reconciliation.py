"""Run a bounded passage-timing trial on copied demand; never promote it.

Usage: python3 -m tools.trial_passage_reconciliation --demand-dir sumo --out runs/passage-trial
The output directory must not exist. A refusal exits 2 and retains evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import time

from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation.runtime import sumo_home
from tools.departure_reconciliation import (
    DepartureReconciliationError, reconcile_sensor_passage_times,
)


def run_trial(
    demand_dir: Path, output_dir: Path, *, method: str = "monotone",
    minimize_shifts: bool = False,
) -> dict:
    """Freeze source bytes, run three seeds on copies, and retain the verdict."""
    if method not in {"monotone", "quarter-assignment"}:
        raise ValueError("unknown passage reconciliation method")
    names = ('calibrated.rou.xml', 'calibrated.agents.json',
             'demand_meta.json', 'net.net.xml')
    inputs = {name: demand_dir / name for name in names}
    hashes = {name: sha256_file(path) for name, path in inputs.items()}
    if any(value is None for value in hashes.values()):
        raise ValueError('trial requires routes, agents, demand metadata and network')
    home = sumo_home()
    output_dir.mkdir(parents=True, exist_ok=False)
    snapshot = output_dir / 'input'
    snapshot.mkdir()
    for name, path in inputs.items():
        shutil.copy2(path, snapshot / name)
    if any(sha256_file(snapshot / name) != hashes[name] for name in names):
        raise ValueError('input changed while taking snapshot')
    candidate = output_dir / 'candidate'
    candidate.mkdir()
    for name in names[:2]:
        shutil.copy2(snapshot / name, candidate / name)
    metadata = json.loads((snapshot / 'demand_meta.json').read_text())
    targets = metadata['sensor_targets']['variants']['edge_shares']
    duration_s = len(next(iter(targets.values()))) * 900
    source = Path(__file__).with_name('departure_reconciliation.py')
    report = {
        'diagnostic_only': True, 'production_policy_changed': False, 'method': method,
        'input_sha256': hashes, 'source_sha256': sha256_file(source),
        'runner_sha256': sha256_file(Path(__file__)),
        'sumo_binary_sha256': sha256_file(home / 'bin' / 'sumo'),
        'seeds': [1000, 1001, 1002], 'duration_s': duration_s,
        'workspace': str(output_dir.resolve()),
    }
    started = time.perf_counter()
    try:
        if method == "quarter-assignment":
            from tools.standard_driver_pool import reconcile_quarter_departures
            report['result'] = reconcile_quarter_departures(
                snapshot / names[0], snapshot / names[1], metadata,
                snapshot / 'net.net.xml', output_dir / 'evidence', home=home,
                minimize_shifts=minimize_shifts)
            report['source_sha256'] = sha256_file(
                Path(__file__).with_name('standard_driver_pool.py'))
            report['seeds'] = [1000, 1001, 1002, 2000, 2001, 2002]
            report['status'] = ('pass' if report['result']['status'] ==
                                'verified_exact_isolated' else 'refused')
        else:
            report['result'] = reconcile_sensor_passage_times(
                candidate / names[0], candidate / names[1], targets,
                duration_s=duration_s, net_path=snapshot / 'net.net.xml',
                sumo_home=home, evidence_dir=output_dir / 'evidence')
            report['status'] = 'pass'
    except DepartureReconciliationError as error:
        report['status'] = 'refused'
        report['reason'] = str(error)
    report['elapsed_s'] = round(time.perf_counter() - started, 3)
    report['original_inputs_unchanged'] = all(
        sha256_file(path) == hashes[name] for name, path in inputs.items())
    if not report['original_inputs_unchanged']:
        report.update(status='refused', reason='source inputs changed during trial')
    report['evidence_sha256'] = {
        str(path.relative_to(output_dir)): sha256_file(path)
        for path in sorted((output_dir / 'evidence').rglob('*.xml'))
    }
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demand-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--method', choices=('monotone', 'quarter-assignment'),
                        default='monotone')
    parser.add_argument("--minimize-shifts", action="store_true",
                        help="Experimental minimum-cost assignment; not the verified default")
    args = parser.parse_args()
    report = run_trial(args.demand_dir, args.out, method=args.method,
                       minimize_shifts=args.minimize_shifts)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
