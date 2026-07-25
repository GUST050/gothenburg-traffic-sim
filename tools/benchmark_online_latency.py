#!/usr/bin/env python3
"""Measure the seconds-level online latency contract, safely and honestly.

The project's new goal is that supported user-facing work finishes in seconds.
Before optimizing anything, the measurement has to be pinned down, because the
easy way to hit a latency target is to answer a different, cheaper question.
So this harness measures exactly what
``validation/online_latency_benchmark_v1.json`` defines — cached render,
honest asynchronous acknowledgement, validated completion — never conflating
them, and it takes its thresholds from that contract rather than from code.

Two rules make a report trustworthy rather than merely fast-looking:

* **A changed answer is not a speed improvement.** Every trial's payload is
  reduced to a semantic digest (volatile keys removed, per the contract) and
  a report whose digest, identity or provenance differs from its reference
  FAILS the comparison. This is the same principle as
  ``tools/benchmark_speed.py``: result preservation first, timing second.
* **Safe by default.** The tool never starts the server, only issues GET, only
  to loopback, refuses any path the contract marks mutating, and refuses a
  measurement the contract marks as needing explicit approval. Measuring a
  real validated completion can start SUMO and create outcomes, so it stays a
  separate, approved task.

Usage (nothing here launches anything):

    python3 tools/benchmark_online_latency.py --measurement cached_render \\
        --target http://127.0.0.1:8000/data/scenarios/index.json \\
        --cache-state warm --candidate-count 0 --seeds 1000,1001,1002 \\
        --model-version scenario_index_v1 --write report.json

    python3 tools/benchmark_online_latency.py --measurement cached_render \\
        --fixture web/data/scenarios/index.json ... --compare reference.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "validation" / "online_latency_benchmark_v1.json"
SCHEMA_VERSION = 1


class BenchmarkRefused(Exception):
    """Raised when the safe default mode will not measure what was asked."""


def load_contract(path: Path = CONTRACT) -> dict[str, Any]:
    contract = json.loads(Path(path).read_text(encoding="utf-8"))
    if (contract.get("kind") != "online_latency_benchmark_contract"
            or contract.get("schema_version") != SCHEMA_VERSION
            or not contract.get("measurements")):
        raise ValueError(f"not a usable latency benchmark contract: {path}")
    return contract


def percentile(values: Sequence[float], fraction: float) -> float:
    """Linear interpolation between closest ranks, as the contract states."""
    if not values:
        raise ValueError("percentile requires at least one sample")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def semantic_digest(payload: Any, volatile_keys: Sequence[str]) -> str:
    """Hash a response with only the contract's volatile keys removed.

    Anything else that differs — a flow value, a status, a winner — changes
    the digest, which is exactly what must block a "faster" claim.
    """
    drop = set(volatile_keys)

    def normalise(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: normalise(item) for key, item in sorted(value.items())
                    if key not in drop}
        if isinstance(value, (list, tuple)):
            return [normalise(item) for item in value]
        return value

    encoded = json.dumps(normalise(payload), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def check_safe_target(url: str, measurement: str, contract: Mapping[str, Any],
                      *, approved_by: str | None = None) -> None:
    """Refuse anything the safe default mode must not touch."""
    entry = contract["measurements"].get(measurement)
    if entry is None:
        raise BenchmarkRefused(f"unknown measurement: {measurement}")
    if entry.get("requires_explicit_approval") and not approved_by:
        raise BenchmarkRefused(
            f"measurement {measurement} is not safe in default mode "
            f"({entry['description']}); it needs its own approved task")
    rules = contract["safe_default_mode"]
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise BenchmarkRefused(f"not an HTTP target: {url}")
    if (parsed.hostname or "") not in set(rules["hosts_allowed"]):
        raise BenchmarkRefused(
            f"refusing a non-loopback target: {parsed.hostname}")
    # The allow-list is checked FIRST and by exact path. Every honest status
    # endpoint contains its own job-start path as a substring
    # (`/api/close/status` contains `/api/close`), so marker matching alone
    # would refuse exactly the reads the 1 s acknowledgement budget exists to
    # measure, leaving that measurement reachable only through a fixture.
    if parsed.path in set(rules.get("read_only_endpoints", ())):
        return
    for marker in rules["mutating_path_markers"]:
        if marker in parsed.path:
            raise BenchmarkRefused(
                f"refusing to benchmark a mutating endpoint: {parsed.path} "
                f"(matches {marker})")


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Stop at any redirect instead of following it.

    ``urlopen`` follows redirects on its own, and the safety check only ever
    saw the URL that was typed. A permitted loopback read could therefore be
    answered with a 302 to a mutating path or an external host and be
    followed after the only check had passed. A benchmark that cannot say
    which resource it timed is not evidence, so a redirect is refused rather
    than validated-and-followed.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BenchmarkRefused(
            f"refusing to follow a {code} redirect to {newurl}: the safety "
            "check applies to the requested URL only")


def http_sampler(url: str, timeout: float) -> Callable[[], tuple[bytes, int]]:
    """A GET-only, redirect-refusing sampler. No other verb is reachable."""
    opener = urllib.request.build_opener(RefuseRedirects)

    def sample() -> tuple[bytes, int]:
        request = urllib.request.Request(url, method="GET")
        with opener.open(request, timeout=timeout) as response:
            return response.read(), int(response.status)
    return sample


def fixture_sampler(path: Path) -> Callable[[], tuple[bytes, int]]:
    """Deterministic local sampler, so the harness works with no server."""
    def sample() -> tuple[bytes, int]:
        return Path(path).read_bytes(), 200
    return sample


def measure(sampler: Callable[[], tuple[bytes, int]], *, warmup: int,
            trials: int, volatile_keys: Sequence[str]) -> dict[str, Any]:
    """Time a read-only sampler and reduce every response to its meaning."""
    if trials < 1:
        raise ValueError("trials must be at least one")
    errors: list[str] = []
    latencies: list[float] = []
    digests: list[str] = []
    sizes: list[int] = []
    for index in range(warmup + trials):
        started = time.perf_counter()
        try:
            body, status = sampler()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError) as error:
            errors.append(f"trial {index + 1}: {type(error).__name__}: {error}")
            continue
        elapsed = time.perf_counter() - started
        if status != 200:
            errors.append(f"trial {index + 1}: HTTP {status}")
            continue
        if index < warmup:
            continue
        latencies.append(elapsed)
        sizes.append(len(body))
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {"non_json_sha256": hashlib.sha256(body).hexdigest()}
        digests.append(semantic_digest(payload, volatile_keys))
    unique = sorted(set(digests))
    return {
        "measured_trials": len(latencies),
        "latencies_seconds": [round(value, 6) for value in latencies],
        "p50_seconds": round(percentile(latencies, 0.5), 6) if latencies else None,
        "p95_seconds": round(percentile(latencies, 0.95), 6) if latencies else None,
        "max_seconds": round(max(latencies), 6) if latencies else None,
        "response_bytes": max(sizes) if sizes else None,
        "semantic_digest": unique[0] if len(unique) == 1 else None,
        "distinct_semantic_digests": len(unique),
        "errors": errors,
    }


def provenance(args: argparse.Namespace) -> dict[str, Any]:
    commit, dirty = None, None
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, check=False,
                                timeout=20).stdout.strip() or None
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                    capture_output=True, text=True,
                                    check=False, timeout=20).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "python": sys.version,
        "git_commit": commit,
        "git_dirty": dirty,
        "measurement": args.measurement,
        "endpoint_identity": args.target or f"fixture:{args.fixture}",
        "cache_state": args.cache_state,
        "candidate_count": args.candidate_count,
        "seeds": [int(seed) for seed in args.seeds.split(",") if seed.strip()],
        "model_version": args.model_version,
        "warmup_trials": args.warmup,
        "measured_trials": args.trials,
    }


def missing_provenance(report: Mapping[str, Any],
                       contract: Mapping[str, Any]) -> list[str]:
    """Required provenance that is absent or empty — never assumed benign."""
    present = report.get("provenance") or {}
    missing = []
    for field in contract["required_provenance"]:
        value = present.get(field)
        # Absent, or an empty string/list/dict. `0` candidates and
        # `git_dirty: false` are real recorded values, not omissions.
        if value is None or (isinstance(value, (str, list, dict)) and not value):
            missing.append(field)
    for field in contract["required_measured_fields"]:
        if field not in (report.get("measured") or {}):
            missing.append(f"measured.{field}")
    return missing


_HEX = set("0123456789abcdef")


def invalid_measured(report: Mapping[str, Any]) -> list[str]:
    """Measured VALUES that do not prove what the report claims.

    Key presence was not enough: a report carrying ``semantic_digest: null``
    with ``distinct_semantic_digests: 1`` and plausible timings would pass
    every earlier check on both sides of a comparison, and a speed claim
    would then rest on answers that were never shown to be identical.
    """
    measured = report.get("measured") or {}
    problems: list[str] = []
    digest = measured.get("semantic_digest")
    if (not isinstance(digest, str) or len(digest) != 64
            or not set(digest) <= _HEX):
        problems.append("semantic_digest is not a 64-character hex digest — "
                        "the answers were never proven identical")
    if measured.get("distinct_semantic_digests") != 1:
        problems.append("distinct_semantic_digests is not exactly 1")
    trials = measured.get("measured_trials")
    if not isinstance(trials, int) or isinstance(trials, bool) or trials < 1:
        problems.append("measured_trials is not a positive integer")
    size = measured.get("response_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        problems.append("response_bytes is not a positive integer")
    timings = {}
    for field in ("p50_seconds", "p95_seconds", "max_seconds"):
        value = measured.get(field)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or value != value or value in (float("inf"), float("-inf"))
                or value < 0):
            problems.append(f"{field} is not a finite non-negative number")
        else:
            timings[field] = float(value)
    if len(timings) == 3 and not (timings["p50_seconds"]
                                  <= timings["p95_seconds"]
                                  <= timings["max_seconds"]):
        problems.append("timings are inconsistent: p50 <= p95 <= max fails")
    if not isinstance(measured.get("errors"), list):
        problems.append("errors is not a list")
    return problems


def evaluate(report: Mapping[str, Any],
             contract: Mapping[str, Any]) -> dict[str, Any]:
    """Threshold verdict for one report, with every reason it can fail."""
    measurement = (report.get("provenance") or {}).get("measurement")
    entry = contract["measurements"].get(measurement, {})
    threshold = entry.get("p95_seconds_maximum")
    measured = report.get("measured") or {}
    problems = [f"missing provenance: {field}"
                for field in missing_provenance(report, contract)]
    problems += invalid_measured(report)
    if measured.get("errors"):
        problems.append(f"{len(measured['errors'])} sampler/HTTP error(s)")
    p95 = measured.get("p95_seconds")
    if threshold is None:
        problems.append(f"no threshold for measurement {measurement!r}")
    elif p95 is None or p95 > threshold:
        problems.append(f"p95 {p95} exceeds the {threshold}s budget")
    return {
        "measurement": measurement,
        "p95_seconds": p95,
        "p95_seconds_maximum": threshold,
        "status": "pass" if not problems else "fail",
        "problems": problems,
    }


def compare(new: Mapping[str, Any], reference: Mapping[str, Any],
            contract: Mapping[str, Any]) -> dict[str, Any]:
    """Compare against a reference. Anything unequal is a FAILED comparison.

    A latency delta is only reported once identity, meaning, errors and
    provenance all agree; otherwise the two runs measured different things and
    the difference in seconds is meaningless.
    """
    problems: list[str] = []
    for side, report in (("new", new), ("reference", reference)):
        for field in missing_provenance(report, contract):
            problems.append(f"{side}: missing provenance {field}")
        for problem in invalid_measured(report):
            problems.append(f"{side}: {problem}")
        if (report.get("measured") or {}).get("errors"):
            problems.append(f"{side}: sampler/HTTP errors present")
    checks = {
        "contract_version": ("contract_version", None),
        "measurement": ("provenance", "measurement"),
        "endpoint_identity": ("provenance", "endpoint_identity"),
        "semantic_digest": ("measured", "semantic_digest"),
    }
    for label, (section, key) in checks.items():
        left = new.get(section) if key is None else (new.get(section) or {}).get(key)
        right = (reference.get(section) if key is None
                 else (reference.get(section) or {}).get(key))
        if left != right:
            problems.append(f"{label} mismatch: {left!r} != {right!r}")
    new_p95 = (new.get("measured") or {}).get("p95_seconds")
    ref_p95 = (reference.get("measured") or {}).get("p95_seconds")
    verdict = evaluate(new, contract)
    result = {
        "status": "fail" if problems else verdict["status"],
        "problems": problems + ([] if problems else verdict["problems"]),
        "new_p95_seconds": new_p95,
        "reference_p95_seconds": ref_p95,
        "speed_claim_allowed": False,
    }
    if not problems and new_p95 is not None and ref_p95 is not None:
        result["p95_delta_seconds"] = round(new_p95 - ref_p95, 6)
        result["speed_claim_allowed"] = (
            verdict["status"] == "pass" and new_p95 <= ref_p95)
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--measurement", required=True,
                        help="Contract measurement being timed.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--target", help="Local read-only HTTP URL (GET only).")
    source.add_argument("--fixture", type=Path,
                        help="Deterministic local file to time instead.")
    parser.add_argument("--cache-state", required=True,
                        help="cold | warm | precomputed — recorded, not inferred.")
    parser.add_argument("--candidate-count", type=int, required=True)
    parser.add_argument("--seeds", required=True, help="Comma-separated seeds.")
    parser.add_argument("--model-version", required=True,
                        help="Model/policy identity the response came from.")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--write", type=Path, help="Where to write the report.")
    parser.add_argument("--compare", type=Path, help="Reference report to compare.")
    parser.add_argument("--contract", type=Path, default=CONTRACT)
    parser.add_argument("--approved-by",
                        help="Recorded authorization for a measurement the "
                             "contract marks as needing explicit approval.")
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace,
                 contract: Mapping[str, Any]) -> dict[str, Any]:
    volatile = contract["semantic_digest"]["volatile_keys"]
    if args.target:
        check_safe_target(args.target, args.measurement, contract,
                          approved_by=args.approved_by)
        sampler = http_sampler(args.target, args.timeout)
    else:
        entry = contract["measurements"].get(args.measurement)
        if entry is None:
            raise BenchmarkRefused(f"unknown measurement: {args.measurement}")
        if entry.get("requires_explicit_approval") and not args.approved_by:
            raise BenchmarkRefused(
                f"measurement {args.measurement} needs explicit approval")
        sampler = fixture_sampler(args.fixture)
    measured = measure(sampler, warmup=args.warmup, trials=args.trials,
                       volatile_keys=volatile)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "online_latency_benchmark_report",
        "contract_version": contract["contract_version"],
        "approved_by": args.approved_by,
        "provenance": provenance(args),
        "measured": measured,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    contract = load_contract(args.contract)
    try:
        report = build_report(args, contract)
    except BenchmarkRefused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    verdict = evaluate(report, contract)
    report["verdict"] = verdict
    if args.compare:
        reference = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        report["comparison"] = compare(report, reference, contract)
    if args.write:
        Path(args.write).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    print(json.dumps(report.get("comparison", verdict), indent=2,
                     sort_keys=True))
    status = (report.get("comparison") or verdict)["status"]
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
