#!/usr/bin/env python3
"""Measure simulation speed without weakening any simulation contract.

This is deliberately a standalone benchmark rather than a pytest test.  It
runs the real ``run_scenario.py`` entry point in a temporary output directory,
records the exact inputs and SUMO/Python environment, and computes semantic
digests after removing only non-semantic timestamps and run paths.  The tool
can therefore compare serial and bounded-seed execution without treating a
faster but different traffic result as a win.

Examples::

    python3 tools/benchmark_speed.py --trials 3 --workers 1 2 3
    python3 tools/benchmark_speed.py --case baseline --trials 1 --workers 1 2
    python3 tools/benchmark_speed.py --reference old.json --write report.json

The default cases are the frozen plan cases: historical baseline, the known
detour closure, and a microscopic baseline smoke run.  A run can take several
minutes on a laptop, especially for micro; no command is started on import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import secrets
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "run_scenario.py"
KNOWN_CLOSURE = "26842525_26355153_0"


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(payload) -> str:
    """Hash JSON after recursively removing explicitly non-semantic fields."""
    def normalise(value):
        if isinstance(value, dict):
            return {
                key: normalise(item)
                for key, item in sorted(value.items())
                if key not in {"generated_at", "created_at", "finished_at"}
                and key not in {"path", "source_path", "workspace"}
            }
        if isinstance(value, list):
            return [normalise(item) for item in value]
        return value

    encoded = json.dumps(normalise(payload), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    # macOS reports bytes, Linux reports KiB.
    return value if sys.platform == "darwin" else value * 1024


def file_fingerprints() -> dict[str, str | None]:
    """Inputs that can change a scenario result, kept in every report."""
    paths = {
        "network": ROOT / "sumo/net.net.xml",
        "graph": ROOT / "web/data/graph.graphml",
        "flows": ROOT / "web/data/flows.json",
        "direction_split": ROOT / "sumo/direction_split.json",
        "demand_meta": ROOT / "sumo/demand_meta.json",
        "bounds": ROOT / "web/data/observability_bounds.json",
        "priors": ROOT / "web/data/assignment_priors.json",
        "calibrated_q50": ROOT / "sumo/calibrated.rou.xml",
        "calibrated_q10": ROOT / "sumo/calibrated_v1.rou.xml",
        "calibrated_q90": ROOT / "sumo/calibrated_v2.rou.xml",
    }
    source_names = (
        "run_scenario.py", "closure_metrics.py", "sumo_runtime.py",
        "build_sumo_demand.py", "demand/calibration.py",
    )
    paths.update({f"source:{name}": ROOT / name for name in source_names})
    return {label: sha256_file(path) for label, path in paths.items()}


def sumo_version() -> str | None:
    try:
        import sumo
        home = Path(sumo.__file__).resolve().parent
        result = subprocess.run([str(home / "bin/sumo"), "--version"],
                                capture_output=True, text=True, timeout=20,
                                check=False)
        return (result.stdout or result.stderr).strip()
    except (ImportError, OSError, subprocess.SubprocessError):
        return None


def run_case(case: str, workers: int, seeds: int, micro: bool,
             timeout_s: int, root: Path) -> dict:
    """Run one frozen case in a private staging directory."""
    closure = case == "closure"
    is_micro = case == "micro" or micro
    name = "baseline" if case in {"baseline", "micro"} else "close_" + KNOWN_CLOSURE
    out_dir = root / "output"
    command = [sys.executable, str(SCENARIO), "--seeds", str(seeds),
               "--seed-workers", str(workers), "--out-dir", str(out_dir)]
    if closure:
        command += ["--close", KNOWN_CLOSURE]
    if is_micro:
        command.append("--micro")
    started = time.monotonic()
    before_rss = rss_bytes()
    completed = subprocess.run(command, cwd=ROOT, capture_output=True,
                               text=True, timeout=timeout_s, check=False)
    elapsed = time.monotonic() - started
    peak = max(before_rss, rss_bytes())
    log_path = root / "stdout.log"
    log_path.write_text(completed.stdout + "\n--- stderr ---\n" + completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{case} workers={workers} failed ({completed.returncode}); "
            f"see {log_path}: {completed.stderr[-1000:]}")

    scenario_path = out_dir / f"{name}.json"
    if not scenario_path.exists():
        raise RuntimeError(f"scenario output missing: {scenario_path}")
    payload = json.loads(scenario_path.read_text())
    trajectories = payload.get("trajectories")
    trajectory_payload = None
    if trajectories:
        trajectory_path = out_dir / trajectories
        if not trajectory_path.exists():
            raise RuntimeError(f"trajectory output missing: {trajectory_path}")
        trajectory_payload = json.loads(trajectory_path.read_text())

    return {
        "case": case,
        "workers": workers,
        "seeds": seeds,
        "micro": is_micro,
        "command": command,
        "returncode": completed.returncode,
        "wall_s": round(elapsed, 3),
        "peak_child_rss_bytes": peak,
        "scenario_bytes": scenario_path.stat().st_size,
        "trajectory_bytes": (out_dir / trajectories).stat().st_size
        if trajectories else 0,
        "scenario_digest": canonical_digest(payload),
        "trajectory_digest": (canonical_digest(trajectory_payload)
                               if trajectory_payload is not None else None),
        "seed_health": payload.get("seed_health"),
        "closure_integrity": payload.get("scenario", {}).get("closure_integrity"),
        "log": str(log_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--case", action="append", choices=("baseline", "closure", "micro"),
                        help="case(s) to run; default is all three frozen cases")
    parser.add_argument("--workers", nargs="+", type=int, default=[1, 2],
                        help="seed-worker counts to compare (default: 1 2)")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--trials", type=int, default=1,
                        help="fresh trials per case/worker pair (default 1; use 3 for adoption)")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="per-case timeout in seconds")
    parser.add_argument("--reference", type=Path,
                        help="optional previous report; semantic mismatches are reported")
    parser.add_argument("--write", type=Path, default=None,
                        help="write the JSON report to this path")
    parser.add_argument("--artifact-dir", type=Path, default=None,
                        help="persistent directory for logs and staged outputs "
                             "(default: /private/tmp/gs-speed-<timestamp>)")
    args = parser.parse_args()
    if not args.case:
        args.case = ["baseline", "closure", "micro"]
    if args.seeds < 1 or args.trials < 1 or any(w < 1 for w in args.workers):
        parser.error("--seeds, --trials and --workers must be >= 1")
    return args


def main() -> int:
    args = parse_args()
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    report = {
        "schema_version": 1,
        "started_at": started,
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "git_commit": None,
        "git_dirty": None,
        "sumo_version": sumo_version(),
        "inputs": file_fingerprints(),
        "cases": [],
    }
    try:
        report["git_commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=False, timeout=20).stdout.strip() or None
        report["git_dirty"] = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
            text=True, check=False, timeout=20).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass

    artifact_dir = args.artifact_dir or Path("/private/tmp") / (
        "gs-speed-" + time.strftime("%Y%m%d-%H%M%S") + "-" +
        secrets.token_hex(2))
    artifact_dir.mkdir(parents=True, exist_ok=False)
    report["artifact_dir"] = str(artifact_dir)
    run_root = artifact_dir
    for case in args.case:
        for workers in args.workers:
            for trial in range(args.trials):
                trial_root = run_root / f"{case}-w{workers}-t{trial + 1}"
                trial_root.mkdir()
                result = run_case(case, workers, args.seeds, False,
                                  args.timeout, trial_root)
                result["trial"] = trial + 1
                report["cases"].append(result)
                print(json.dumps(result, sort_keys=True), flush=True)

    # A worker-count change is accepted only when it is result-preserving. The
    # report remains useful when a run differs: the non-zero return value makes
    # CI/manual adoption stop before anyone calls a faster result accurate.
    mismatches = []
    grouped: dict[str, dict[int, dict]] = {}
    for item in report["cases"]:
        grouped.setdefault(item["case"], {}).setdefault(item["trial"], {})[
            item["workers"]] = item
    for case, trials in grouped.items():
        for trial, by_worker in trials.items():
            serial = by_worker.get(1)
            if not serial:
                continue
            for workers, candidate in by_worker.items():
                if workers == 1:
                    continue
                for field in ("scenario_digest", "trajectory_digest"):
                    if candidate.get(field) != serial.get(field):
                        mismatches.append({"case": case, "trial": trial,
                                           "workers": workers, "field": field,
                                           "serial": serial.get(field),
                                           "candidate": candidate.get(field)})
    reference_mismatches = []
    if args.reference:
        reference = json.loads(args.reference.read_text())
        reference_rows = {
            (row.get("case"), row.get("workers"), row.get("trial")): row
            for row in reference.get("cases", [])
        }
        for row in report["cases"]:
            key = (row["case"], row["workers"], row["trial"])
            old = reference_rows.get(key)
            if old is None:
                reference_mismatches.append({"key": key, "reason": "missing_reference_row"})
                continue
            for field in ("scenario_digest", "trajectory_digest"):
                if row.get(field) != old.get(field):
                    reference_mismatches.append({
                        "key": key, "field": field,
                        "reference": old.get(field), "current": row.get(field),
                    })
    report["semantic_mismatches"] = mismatches
    report["reference_mismatches"] = reference_mismatches
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"wrote {args.write}")
    print(json.dumps({"semantic_mismatches": mismatches,
                      "reference_mismatches": reference_mismatches,
                      "n_runs": len(report["cases"])}, indent=2))
    return 2 if mismatches or reference_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
