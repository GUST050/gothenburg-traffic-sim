#!/usr/bin/env python3
"""Diagnostic fresh-interpreter versus reusable-Python-worker benchmark.

This harness does not enable a production pool.  It replays a small set of
frozen independent-day requests twice: once through the current one-process-
per-request transport and once through a spawn-based ``multiprocessing.Pool``.
Every request still constructs a fresh runner and starts fresh SUMO processes;
the only intended reuse is the Python interpreter and its imported modules.

Four paired trials run by default and alternate which arm runs first. Each
trial receives separate baseline-cache roots, every result must be byte-exact,
and the complete speedup range must lie on one side of the 10% continuation
threshold before the diagnostic says continue or reject. A straddling result
is inconclusive. No result from this harness is an adoption decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from math import ceil
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = "daily_worker_pool_diagnostic_v2"
REQUEST_SCHEMA = "independent_daily_worker_request_v1"
RESULT_SCHEMA = "independent_daily_worker_result_v1"
MIN_CONTINUATION_SPEEDUP = 1.10


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rss_bytes(who: int) -> int:
    value = int(resource.getrusage(who).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _execute_timed(request: Mapping[str, Any]) -> dict[str, Any]:
    """Execute one existing worker contract and include process diagnostics."""
    started = time.monotonic()
    from traffic_sim.simulation.independent_daily_worker import execute_request

    result = execute_request(request)
    if not isinstance(result, dict) or result.get("schema") != RESULT_SCHEMA:
        raise ValueError("daily worker returned a malformed result")
    return {
        "result": result,
        "wall_s": round(time.monotonic() - started, 6),
        "worker_peak_rss_bytes": _rss_bytes(resource.RUSAGE_SELF),
        "sumo_child_peak_rss_bytes": _rss_bytes(resource.RUSAGE_CHILDREN),
        "worker_pid": os.getpid(),
    }


def _run_hidden_worker(request_path: Path, result_path: Path) -> int:
    _write_json(result_path, _execute_timed(_read_object(request_path)))
    return 0


def _fresh_arm(
    requests: Sequence[Mapping[str, Any]], root: Path, timeout_s: float,
    workers: int,
) -> tuple[list[dict[str, Any]], float]:
    def execute(item: tuple[int, Mapping[str, Any]]) -> dict[str, Any]:
        index, request = item
        request_path = root / f"request-{index}.json"
        result_path = root / f"result-{index}.json"
        _write_json(request_path, request)
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker-request",
                str(request_path),
                "--worker-result",
                str(result_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-2000:]
            raise RuntimeError(
                f"fresh worker {index} exited {completed.returncode}: {detail}"
            )
        return _read_object(result_path)

    started = time.monotonic()
    with ThreadPoolExecutor(
        max_workers=min(workers, len(requests)),
        thread_name_prefix="fresh-daily-worker",
    ) as executor:
        results = list(executor.map(execute, enumerate(requests)))
    return results, time.monotonic() - started


def _pool_arm(
    requests: Sequence[Mapping[str, Any]], workers: int, recycle_after: int,
    task_timeout_s: float,
) -> tuple[list[dict[str, Any]], float]:
    context = multiprocessing.get_context("spawn")
    started = time.monotonic()
    with context.Pool(
        processes=workers,
        maxtasksperchild=recycle_after,
    ) as pool:
        pending = pool.map_async(_execute_timed, requests, chunksize=1)
        # ``AsyncResult.get`` timeout does not cancel work by itself. Exiting
        # this context on the raised error terminates and joins every member;
        # the bound covers the maximum serial waves plus spawn/teardown slack.
        timeout_s = task_timeout_s * ceil(len(requests) / workers) + 30.0
        try:
            results = pending.get(timeout=timeout_s)
        except multiprocessing.TimeoutError as error:
            raise RuntimeError(
                f"reusable worker pool exceeded {timeout_s:.1f} s"
            ) from error
    return list(results), time.monotonic() - started


def _load_units(path: Path) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid unit ledger JSON at line {line_number}"
                ) from error
            if not isinstance(item, dict) or not isinstance(item.get("schedule"), dict):
                raise ValueError(f"invalid unit ledger record at line {line_number}")
            units.append(item)
    return units


def build_requests(
    *,
    workspace: Path,
    release_path: Path,
    cache_root: Path,
    unit_count: int,
    variant: str,
    warm_execution: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build frozen, same-date requests without resolving or building demand."""
    from traffic_sim.core.contracts import ClosureSchedule, ClosureSearchSpec
    from traffic_sim.simulation.envelope import (
        EnvelopePolicy,
        build_simulation_envelope,
        independent_daily_demand_spec,
    )
    from traffic_sim.simulation.finalist_decision import DEMAND_VARIANTS

    if variant not in DEMAND_VARIANTS:
        raise ValueError(f"unknown demand variant: {variant}")
    spec_path = workspace / "input" / "closure_search.json"
    ledger_path = workspace / "ledgers" / "units.ndjson"
    spec = ClosureSearchSpec.from_dict(_read_object(spec_path))
    release = _read_object(release_path)
    request_contract = release.get("request")
    entries = release.get("entries")
    if (
        release.get("kind") != "monthly_demand_release"
        or not isinstance(request_contract, Mapping)
        or not isinstance(entries, list)
        or request_contract.get("search_content_key") != spec.content_key
        or request_contract.get("release_id") != spec.demand_build_id
    ):
        raise ValueError("release does not match the frozen search")
    baseline_p99 = request_contract.get("baseline_trip_duration_p99_s")
    if isinstance(baseline_p99, bool) or not isinstance(baseline_p99, int):
        raise ValueError("release baseline p99 is invalid")
    envelope_policy = EnvelopePolicy(**dict(request_contract["envelope_policy"]))
    by_build_key = {
        str(item.get("demand_build_spec", {}).get("build_key")): item
        for item in entries
        if isinstance(item, Mapping)
    }

    raw_units = _load_units(ledger_path)
    first_date = str(raw_units[0]["identity"]["work_date"])
    selected = [
        item for item in raw_units
        if str(item.get("identity", {}).get("work_date")) == first_date
    ][:unit_count]
    if len(selected) != unit_count:
        raise ValueError(
            f"ledger has only {len(selected)} units for {first_date}, "
            f"need {unit_count}"
        )

    requests: list[dict[str, Any]] = []
    archives: set[str] = set()
    for item in selected:
        schedule = ClosureSchedule.from_dict(item["schedule"])
        envelope = build_simulation_envelope(
            spec,
            schedule,
            baseline_trip_duration_p99_s=baseline_p99,
            policy=envelope_policy,
        )
        expected = independent_daily_demand_spec(spec, schedule, envelope)
        entry = by_build_key.get(expected.build_key)
        if entry is None:
            raise ValueError(
                f"release does not cover build {expected.build_key} for "
                f"{schedule.schedule_id}"
            )
        if entry.get("demand_build_spec") != expected.to_dict():
            raise ValueError("release demand-build contract is inconsistent")
        archive = Path(str(entry["archive"])).resolve()
        if not archive.is_dir():
            raise FileNotFoundError(archive)
        archives.add(str(archive))
        requests.append({
            "schema": REQUEST_SCHEMA,
            "execution": {
                "spec": spec.to_dict(),
                "archive": str(archive),
                "baseline_trip_duration_p99_s": baseline_p99,
                "study_provenance_key": (
                    "diagnostic-daily-worker-pool-" + spec.content_key
                ),
                "cache_root": str(cache_root),
                "envelope_policy": dict(request_contract["envelope_policy"]),
                "expected_demand_spec": expected.to_dict(),
                "warm_execution": warm_execution,
                "include_disruption": True,
            },
            "schedule": schedule.to_dict(),
            "target_repetitions": {
                name: (1 if name == variant else 0)
                for name in DEMAND_VARIANTS
            },
            "existing": None,
            "stage": "pilot",
        })
    return requests, {
        "search_content_key": spec.content_key,
        "work_date": first_date,
        "schedule_ids": [item["schedule"]["schedule_id"] for item in selected],
        "archive_paths": sorted(archives),
        "variant": variant,
        "warm_execution": warm_execution,
        "workspace_manifest_sha256": _sha256_file(workspace / "manifest.json"),
        "spec_sha256": _sha256_file(spec_path),
        "units_ledger_sha256": _sha256_file(ledger_path),
        "release_sha256": _sha256_file(release_path),
    }


def _arm_summary(records: Sequence[Mapping[str, Any]], wall_s: float) -> dict[str, Any]:
    return {
        "wall_s": round(wall_s, 6),
        "task_wall_s": [float(record["wall_s"]) for record in records],
        "worker_pids": sorted({int(record["worker_pid"]) for record in records}),
        "worker_peak_rss_bytes": max(
            int(record["worker_peak_rss_bytes"]) for record in records
        ),
        "sumo_child_peak_rss_bytes": max(
            int(record["sumo_child_peak_rss_bytes"]) for record in records
        ),
        "evidence_digests": [
            _canonical_digest(record["result"]["evidence"])
            for record in records
        ],
    }


def _aggregate_arm_summaries(
    summaries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate counterbalanced trials without hiding their raw timings."""
    walls = [float(item["wall_s"]) for item in summaries]
    return {
        "wall_s": round(median(walls), 6),
        "wall_s_statistic": "median",
        "trial_wall_s": walls,
        "trials": list(summaries),
        "worker_peak_rss_bytes": max(
            int(item["worker_peak_rss_bytes"]) for item in summaries
        ),
        "sumo_child_peak_rss_bytes": max(
            int(item["sumo_child_peak_rss_bytes"]) for item in summaries
        ),
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from traffic_sim.simulation.workspace import WorkspaceLock

    workspace = Path(args.workspace).resolve()
    release_path = Path(args.release).resolve()
    lock = WorkspaceLock(f"daily worker pool diagnostic {os.getpid()}")
    if not lock.acquire(timeout=args.workspace_wait_s, poll_s=1.0):
        raise RuntimeError(f"demand workspace busy: {lock.holder_description()}")
    try:
        with tempfile.TemporaryDirectory(prefix="daily-pool-diagnostic-") as raw:
            root = Path(raw)
            frozen: dict[str, Any] | None = None
            trials: list[dict[str, Any]] = []
            for trial_index in range(args.trials):
                trial_root = root / f"trial-{trial_index + 1}"
                fresh_requests, fresh_frozen = build_requests(
                    workspace=workspace,
                    release_path=release_path,
                    cache_root=trial_root / "fresh-baselines",
                    unit_count=args.units,
                    variant=args.variant,
                    warm_execution=args.warm_execution,
                )
                pool_requests, pool_frozen = build_requests(
                    workspace=workspace,
                    release_path=release_path,
                    cache_root=trial_root / "pool-baselines",
                    unit_count=args.units,
                    variant=args.variant,
                    warm_execution=args.warm_execution,
                )
                comparable_fresh = {
                    key: value for key, value in fresh_frozen.items()
                    if key != "archive_paths"
                }
                comparable_pool = {
                    key: value for key, value in pool_frozen.items()
                    if key != "archive_paths"
                }
                if comparable_fresh != comparable_pool:
                    raise RuntimeError(
                        "benchmark arms were built from different inputs")
                if frozen is None:
                    frozen = fresh_frozen
                elif comparable_fresh != {
                    key: value for key, value in frozen.items()
                    if key != "archive_paths"
                }:
                    raise RuntimeError(
                        "benchmark inputs changed between measured trials")

                prewarm: dict[str, Any] | None = None
                if args.prewarm_baselines:
                    fresh_warm_records, fresh_warm_wall = _fresh_arm(
                        fresh_requests[:1], trial_root / "fresh-prewarm",
                        args.task_timeout_s, 1)
                    pool_warm_records, pool_warm_wall = _fresh_arm(
                        pool_requests[:1], trial_root / "pool-prewarm",
                        args.task_timeout_s, 1)
                    prewarm = {
                        "timed_comparison_excludes_prewarm": True,
                        "fresh_cache": _arm_summary(
                            fresh_warm_records, fresh_warm_wall),
                        "pool_cache": _arm_summary(
                            pool_warm_records, pool_warm_wall),
                    }

                fresh_first = trial_index % 2 == 0
                if fresh_first:
                    fresh_records, fresh_wall = _fresh_arm(
                        fresh_requests, trial_root / "fresh-transport",
                        args.task_timeout_s, args.workers)
                    pool_records, pool_wall = _pool_arm(
                        pool_requests, args.workers, args.recycle_after,
                        args.task_timeout_s)
                    arm_order = ["fresh_interpreter", "reusable_spawn_pool"]
                else:
                    pool_records, pool_wall = _pool_arm(
                        pool_requests, args.workers, args.recycle_after,
                        args.task_timeout_s)
                    fresh_records, fresh_wall = _fresh_arm(
                        fresh_requests, trial_root / "fresh-transport",
                        args.task_timeout_s, args.workers)
                    arm_order = ["reusable_spawn_pool", "fresh_interpreter"]

                fresh_evidence = [
                    record["result"]["evidence"] for record in fresh_records]
                pool_evidence = [
                    record["result"]["evidence"] for record in pool_records]
                trials.append({
                    "trial": trial_index + 1,
                    "arm_order": arm_order,
                    "baseline_prewarm": prewarm,
                    "fresh_interpreter": _arm_summary(
                        fresh_records, fresh_wall),
                    "reusable_spawn_pool": _arm_summary(
                        pool_records, pool_wall),
                    "exact_evidence_equal": fresh_evidence == pool_evidence,
                    "speedup": round(
                        fresh_wall / pool_wall if pool_wall > 0 else 0.0, 6),
                })
    finally:
        lock.release()

    assert frozen is not None
    exact = all(item["exact_evidence_equal"] for item in trials)
    speedups = [float(item["speedup"]) for item in trials]
    speedup = median(speedups)
    continue_testing = exact and min(speedups) >= MIN_CONTINUATION_SPEEDUP
    rejected = exact and max(speedups) < MIN_CONTINUATION_SPEEDUP
    decision = (
        "continue_to_larger_campaign" if continue_testing
        else "reject_generic_pool" if rejected
        else "inconclusive"
    )
    fresh_summaries = [item["fresh_interpreter"] for item in trials]
    pool_summaries = [item["reusable_spawn_pool"] for item in trials]
    return {
        "schema": SCHEMA,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "diagnostic_only": True,
        "arm_order": "alternating_counterbalanced",
        "order_bias": "even trials alternate which arm runs first",
        "frozen_inputs": frozen,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pool_start_method": "spawn",
        },
        "configuration": {
            "units": args.units,
            "fresh_workers": args.workers,
            "pool_workers": args.workers,
            "chunksize": 1,
            "recycle_after": args.recycle_after,
            "per_task_timeout_s": args.task_timeout_s,
            "minimum_continuation_speedup": MIN_CONTINUATION_SPEEDUP,
            "fresh_sumo_per_task": True,
            "baseline_prewarmed": args.prewarm_baselines,
            "trials": args.trials,
        },
        "trials": trials,
        "fresh_interpreter": _aggregate_arm_summaries(fresh_summaries),
        "reusable_spawn_pool": _aggregate_arm_summaries(pool_summaries),
        "comparison": {
            "exact_evidence_equal": exact,
            "speedup": round(speedup, 6),
            "speedup_statistic": "median of paired trials",
            "speedup_range": [round(min(speedups), 6), round(max(speedups), 6)],
            "decision": decision,
            "continue_to_counterbalanced_campaign": continue_testing,
            "production_adoption_authorized": False,
        },
        "source_fingerprints": {
            "harness": _sha256_file(Path(__file__).resolve()),
            "worker": _sha256_file(
                ROOT / "traffic_sim/simulation/independent_daily_worker.py"
            ),
            "monthly_sumo": _sha256_file(
                ROOT / "traffic_sim/simulation/monthly_sumo.py"
            ),
            "run_scenario": _sha256_file(ROOT / "run_scenario.py"),
            "network": _sha256_file(ROOT / "sumo/net.net.xml"),
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("runs/closure-search/ui-monthly-euc9qp"),
    )
    parser.add_argument(
        "--release",
        type=Path,
        default=Path(
            "runs/monthly-demand-releases/"
            "eff5a2cc8e000129b20f49149a9bebcf.json"
        ),
    )
    parser.add_argument("--units", type=int, default=2)
    parser.add_argument("--variant", choices=("q10", "q50", "q90"), default="q50")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--trials", type=int, default=4,
        help="Even counterbalanced paired trials (default 4, minimum 4).")
    parser.add_argument("--recycle-after", type=int, default=25)
    parser.add_argument("--task-timeout-s", type=float, default=900.0)
    parser.add_argument("--workspace-wait-s", type=float, default=0.0)
    parser.add_argument("--warm-execution", action="store_true")
    parser.add_argument("--prewarm-baselines", action="store_true")
    parser.add_argument("--write", type=Path)
    parser.add_argument("--worker-request", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker_request is not None or args.worker_result is not None:
        if args.worker_request is None or args.worker_result is None:
            parser.error("internal worker mode requires both worker paths")
        return args
    for name in ("units", "workers", "recycle_after"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.trials < 4 or args.trials % 2:
        parser.error("--trials must be an even number at least 4")
    if args.task_timeout_s <= 0 or args.workspace_wait_s < 0:
        parser.error("timeouts must be positive (workspace wait may be zero)")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.worker_request is not None:
        return _run_hidden_worker(args.worker_request, args.worker_result)
    try:
        report = run_benchmark(args)
    except Exception as error:
        if args.write is not None:
            failure = {
                "schema": SCHEMA,
                "created_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
                "diagnostic_only": True,
                "status": "failed",
                "failure": {
                    "type": type(error).__name__,
                    "message": str(error)[-4000:],
                    "baseline_cache_race": (
                        "monthly baseline cache raced with another writer"
                        in str(error)
                    ),
                },
                "configuration": {
                    "units": args.units,
                    "fresh_workers": args.workers,
                    "pool_workers": args.workers,
                    "trials": args.trials,
                    "baseline_prewarmed": args.prewarm_baselines,
                    "variant": args.variant,
                    "warm_execution": args.warm_execution,
                },
                "input_fingerprints": {
                    "workspace_manifest": _sha256_file(
                        Path(args.workspace) / "manifest.json"
                    ),
                    "release": _sha256_file(Path(args.release)),
                },
                "source_fingerprints": {
                    "harness": _sha256_file(Path(__file__).resolve()),
                    "worker": _sha256_file(
                        ROOT / "traffic_sim/simulation/independent_daily_worker.py"
                    ),
                    "monthly_sumo": _sha256_file(
                        ROOT / "traffic_sim/simulation/monthly_sumo.py"
                    ),
                },
            }
            _write_json(Path(args.write), failure)
        raise
    if args.write is not None:
        _write_json(Path(args.write), report)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
