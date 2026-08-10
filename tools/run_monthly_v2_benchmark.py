#!/usr/bin/env python3
"""Run the objective-aligned monthly benchmark on pinned immutable inputs.

This intentionally bypasses the mutable demand resolver and its global
workspace recovery path.  The benchmark reads one frozen archive and writes
only to the dedicated search and baseline-cache roots declared in the tracked
plan.  It is resumable: an interrupted search reuses verified immutable
artifacts rather than deleting or overwriting them.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_monthly_closure_search import _bounded_exhaustive_builder
from traffic_sim.core.contracts import load_closure_search_spec
from traffic_sim.simulation.monthly_search import (
    MonthlySearchPolicy,
    run_monthly_search,
)
from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

PLAN_PATH = ROOT / "validation" / "monthly_search_v2_benchmark_plan_v1.json"


def _read(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain an object")
    return raw


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def validate_plan(plan: Mapping[str, Any]) -> dict[str, Path]:
    if plan.get("schema_version") != 1:
        raise ValueError("unsupported v2 benchmark plan schema")
    if plan.get("kind") != "monthly_search_v2_benchmark_plan":
        raise ValueError("v2 benchmark plan kind is invalid")
    inputs = plan.get("inputs")
    outputs = plan.get("outputs")
    if not isinstance(inputs, Mapping) or not isinstance(outputs, Mapping):
        raise ValueError("v2 benchmark plan inputs/outputs are required")

    resolved: dict[str, Path] = {}
    for label, record in inputs.items():
        if not isinstance(record, Mapping):
            raise ValueError(f"benchmark input {label} must be an object")
        path = _path(str(record.get("path", ""))).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        expected = str(record.get("sha256", ""))
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(
                f"benchmark input {label} drifted: {actual} != {expected}"
            )
        resolved[str(label)] = path

    required_inputs = {
        "search_spec",
        "policy",
        "benchmark_runner",
        "archive_manifest",
        "demand_meta",
        "demand_routes_q10",
        "demand_routes_q50",
        "demand_routes_q90",
        "sumo_network",
    }
    if not required_inputs <= set(resolved):
        raise ValueError(
            "benchmark plan lacks inputs: "
            f"{sorted(required_inputs - set(resolved))}"
        )
    archive = resolved["archive_manifest"].parent
    for label in (
        "demand_meta",
        "demand_routes_q10",
        "demand_routes_q50",
        "demand_routes_q90",
    ):
        if resolved[label].parent != archive:
            raise ValueError(f"benchmark input {label} leaves the archive")
    if resolved["sumo_network"] != (ROOT / "sumo" / "net.net.xml").resolve():
        raise ValueError("benchmark must bind the active SUMO network")
    resolved["archive"] = archive

    for label in ("search_root", "baseline_cache"):
        value = outputs.get(label)
        if not isinstance(value, str) or not value:
            raise ValueError(f"benchmark output {label} is required")
        path = _path(value).resolve()
        if ROOT not in path.parents:
            raise ValueError(f"benchmark output {label} leaves the repository")
        resolved[label] = path
    if resolved["search_root"] == resolved["baseline_cache"]:
        raise ValueError("search and baseline-cache roots must differ")
    return resolved


def preflight(plan: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    spec = load_closure_search_spec(paths["search_spec"])
    policy = MonthlySearchPolicy.from_dict(_read(paths["policy"]))
    if policy.objective_method != "closure_cost_v1":
        raise ValueError("v2 benchmark policy must bind closure_cost_v1")
    if policy.status != "provisional":
        raise ValueError("v2 benchmark starts from a provisional policy")

    study_key = _canonical_digest({
        "kind": "monthly_search_v2_benchmark_v1",
        "search_content_key": spec.content_key,
        "policy_content_key": policy.content_key,
        "archive_digest": plan.get("archive_digest"),
    })
    runner = ArchivedDemandSumoRunner(
        spec,
        archive=paths["archive"],
        baseline_trip_duration_p99_s=int(
            plan["baseline_trip_duration_p99_s"]
        ),
        study_provenance_key=study_key,
        cache_root=paths["baseline_cache"],
        seed_workers=1,
        include_disruption=True,
        warm_execution=False,
    )
    provenance = runner.provenance()
    if provenance["archive_digest"] != plan.get("archive_digest"):
        raise ValueError("frozen archive digest differs from benchmark plan")
    if provenance["ranking_objective_evidence"] != "closure_cost_v1":
        raise ValueError("runner did not bind closure-cost evidence")
    return {
        "spec": spec,
        "policy": policy,
        "runner": runner,
        "preflight": {
            "search_content_key": spec.content_key,
            "policy_content_key": policy.content_key,
            "archive_digest": provenance["archive_digest"],
            "matched_baseline_id": provenance["matched_baseline_id"],
            "ranking_objective_evidence": provenance[
                "ranking_objective_evidence"
            ],
            "simulation_source_digest": provenance[
                "simulation_source_digest"
            ],
            "sumo_version": provenance["sumo_version"].splitlines()[0],
            "search_root": str(paths["search_root"]),
            "baseline_cache": str(paths["baseline_cache"]),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate every pinned input and construct no search artifacts",
    )
    args = parser.parse_args(argv)

    plan = _read(PLAN_PATH)
    paths = validate_plan(plan)
    prepared = preflight(plan, paths)
    if args.preflight_only:
        print(json.dumps(prepared["preflight"], indent=2, sort_keys=True))
        return 0

    screen_builder = functools.partial(
        _bounded_exhaustive_builder,
        maximum_candidates=int(plan["maximum_candidates"]),
        proxy_version="bounded_exhaustive_sumo_v1",
    )
    started = time.perf_counter()
    result = run_monthly_search(
        prepared["spec"],
        prepared["policy"],
        runner=prepared["runner"],
        screen_builder=screen_builder,
        root=paths["search_root"],
    )
    elapsed_s = round(time.perf_counter() - started, 2)
    result_path = (
        paths["search_root"]
        / prepared["spec"].search_id
        / "artifacts"
        / "result.json"
    )
    summary = {
        **prepared["preflight"],
        "elapsed_s": elapsed_s,
        "result_path": str(result_path),
        "result_sha256": _sha256(result_path),
        "status": result["status"],
        "selected_schedule_ids": [
            item["schedule_id"] for item in result["selected_schedules"]
        ],
        "claim_boundary": result["claim_boundary"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
