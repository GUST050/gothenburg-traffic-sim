#!/usr/bin/env python3
"""Measure exact structured closure-cache hits without launching new work."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = "http://127.0.0.1:8000/api/close"
P95_LIMIT_S = 2.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _require_loopback_close(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1", "localhost", "::1"}:
        raise ValueError("target must be a loopback HTTP URL")
    if parsed.path != "/api/close" or parsed.query or parsed.fragment:
        raise ValueError("target must be exactly /api/close without query data")


def _load_fixture(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    spec = payload.get("scenario_spec") if isinstance(payload, dict) else None
    if not isinstance(spec, dict) or not isinstance(spec.get("scenario_id"), str):
        raise ValueError("scenario fixture has no structured scenario_spec")
    closures = spec.get("closures")
    if not isinstance(closures, list) or not closures:
        raise ValueError("exact closure-cache benchmark requires a closure")
    trajectory_name = payload.get("trajectories")
    if not isinstance(trajectory_name, str) or not trajectory_name:
        raise ValueError("scenario fixture has no trajectory sidecar")
    trajectory = path.parent / trajectory_name
    if not trajectory.is_file():
        raise ValueError(f"trajectory sidecar is missing: {trajectory}")
    return spec, {
        "scenario_sha256": _sha256(path),
        "trajectory_sha256": _sha256(trajectory),
    }


def _json_request(url: str, *, payload: dict[str, Any] | None = None,
                  timeout: float) -> tuple[int, dict[str, Any]]:
    data = None
    method = "GET"
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        method = "POST"
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload_body = json.loads(response.read())
        if not isinstance(payload_body, dict):
            raise ValueError("server response must be a JSON object")
        return int(response.status), payload_body


def _git_identity() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True, timeout=20).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
            text=True, check=True, timeout=20).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None, None
    return commit or None, dirty


def run_benchmark(*, target: str, fixture: Path, trials: int,
                  timeout: float) -> dict[str, Any]:
    _require_loopback_close(target)
    if trials < 10:
        raise ValueError("exact cache evidence requires at least 10 trials")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    spec, before = _load_fixture(fixture)
    commit, dirty = _git_identity()
    latencies: list[float] = []
    errors: list[str] = []
    responses: list[dict[str, Any]] = []
    status_url = target + "/status"
    for trial in range(1, trials + 1):
        started = time.perf_counter()
        try:
            code, response = _json_request(
                target, payload={"scenario_spec": spec}, timeout=timeout)
            elapsed = time.perf_counter() - started
            status_code, status = _json_request(status_url, timeout=timeout)
        except (OSError, ValueError) as exc:
            errors.append(f"trial {trial}: {type(exc).__name__}: {exc}")
            continue
        latencies.append(elapsed)
        responses.append(response)
        if code != 202 or response.get("status") != "done" \
                or response.get("cached") is not True \
                or response.get("scenario_id") != spec["scenario_id"]:
            errors.append(f"trial {trial}: response was not an exact cache hit")
        if status_code != 200 or status.get("status") != "done" \
                or status.get("cached") is not True \
                or status.get("cache_hit") is not True \
                or "started_at" in status:
            errors.append(f"trial {trial}: cache status contract failed")
    _spec, after = _load_fixture(fixture)
    if after != before:
        errors.append("scenario or trajectory bytes changed during cache hits")
    response_digests = {
        hashlib.sha256(json.dumps(
            response, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        for response in responses
    }
    if len(response_digests) != 1:
        errors.append("cache-hit responses were not semantically identical")
    p95 = _percentile(latencies, 0.95) if latencies else None
    if len(latencies) != trials:
        errors.append(f"only {len(latencies)}/{trials} trials completed")
    if p95 is None or p95 > P95_LIMIT_S:
        errors.append(f"p95 {p95} exceeds the {P95_LIMIT_S}s budget")
    return {
        "schema_version": 1,
        "kind": "exact_close_cache_benchmark",
        "provenance": {
            "git_commit": commit,
            "git_dirty": dirty,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "target": target,
            "scenario_spec": spec,
            "fixture": str(fixture),
            "fixture_hashes": before,
            "trials": trials,
        },
        "measured": {
            "latencies_seconds": [round(value, 6) for value in latencies],
            "p50_seconds": round(_percentile(latencies, 0.5), 6)
            if latencies else None,
            "p95_seconds": round(p95, 6) if p95 is not None else None,
            "max_seconds": round(max(latencies), 6) if latencies else None,
            "response_digest": next(iter(response_digests), None),
            "completed_trials": len(latencies),
            "errors": errors,
        },
        "verdict": {
            "status": "pass" if not errors else "fail",
            "p95_seconds_maximum": P95_LIMIT_S,
            "zero_sumo_calls_evidence": (
                "every POST returned cached=true; status returned cache_hit=true "
                "without started_at; scenario and trajectory bytes were unchanged"),
        },
    }


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--write", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_benchmark(
            target=args.target, fixture=args.fixture, trials=args.trials,
            timeout=args.timeout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"refused: {exc}")
        return 2
    _write_atomic(args.write, report)
    print(json.dumps(report["verdict"], indent=2, sort_keys=True))
    return 0 if report["verdict"]["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
