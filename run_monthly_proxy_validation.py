#!/usr/bin/env python3
"""Run resumable, exhaustive SUMO outcomes for the frozen monthly proxy set."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import platform
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import run_scenario as rs
import suggest_closure_time as legacy
from screen_monthly_closures import build_screening_artifact
from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSearchSpec,
    write_closure_search_spec,
)
from traffic_sim.core.fingerprint import sha256_file, sumo_version
from traffic_sim.simulation import metrics as closure_metrics
from traffic_sim.simulation.monthly_proxy import ShortlistPolicy, SHORTLIST_VERSION
from traffic_sim.simulation.proxy_validation import (
    evaluate_partial_validation_set,
    evaluate_validation_set,
    validate_validation_manifest,
)


DEFAULT_MANIFEST = Path("validation/monthly_proxy_manifest.json")
DEFAULT_ROOT = Path("runs") / "closure-proxy-validation"
VALIDATION_POLICY = ShortlistPolicy(
    best_overall=2,
    best_per_day_count=1,
    best_per_date_block=1,
    date_block_days=7,
    validation_quantiles=(0.5, 1.0),
    exact_date_quantiles=(0.0, 1.0),
    low_support_multiplier=2.0,
    unavailable_multiplier=2.5,
    bounded_exhaustive_limit=1,
    maximum_shortlist=96,
)


def gate_record_for(
    report: Mapping[str, Any],
    manifest: Mapping[str, Any],
    completed_cases: int,
) -> dict[str, Any] | None:
    """Return local evidence for a passing run; publication remains gated.

    The record must name the campaign it came from. An unlabelled campaign is
    refused instead of being defaulted to the current one, a report built
    against a different manifest is refused, and an incomplete run is refused:
    each of those would otherwise produce a record the loader could mistake
    for untouched evidence of the frozen campaign.
    """
    campaign_version = manifest.get("campaign_version")
    manifest_content_key = manifest.get("content_key")
    if (
        report.get("gate_status") != "pass"
        or manifest.get("shortlist_version") != SHORTLIST_VERSION
        or manifest.get("shortlist_policy_content_key")
        != VALIDATION_POLICY.content_key
        or not isinstance(campaign_version, str)
        or not campaign_version
        or not isinstance(manifest_content_key, str)
        or not manifest_content_key
        or report.get("manifest_content_key") != manifest_content_key
        or completed_cases != len(manifest["cases"])
    ):
        return None
    return {
        **{
            key: value
            for key, value in report.items()
            if key != "case_reports"
        },
        "kind": "monthly_proxy_validation_gate_record",
        "heldout_set": campaign_version,
        "manifest_content_key": manifest_content_key,
        "required_cases": len(manifest["cases"]),
        "completed_cases": completed_cases,
        "shortlist_version": SHORTLIST_VERSION,
        "shortlist_policy_content_key": VALIDATION_POLICY.content_key,
    }


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bundle_digest(paths: list[Path]) -> str:
    records = []
    for path in paths:
        digest = sha256_file(path)
        if digest is None:
            raise FileNotFoundError(path)
        records.append({
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": digest,
        })
    return _canonical_digest(records)


def _demand_archives() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for meta_path in sorted(Path("runs").glob("demand-*/demand_meta.json")):
        try:
            metadata = _read(meta_path)
            manifest = _read(meta_path.parent / "manifest.json")
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        key = metadata.get("demand_build_key")
        if isinstance(key, str) and manifest.get("status") == "succeeded":
            found[key] = meta_path.parent
    return found


def _variants(archive: Path, metadata: Mapping[str, Any]) -> list[Path]:
    paths = [archive / "calibrated.rou.xml"]
    count = int(metadata.get("n_variants", 1))
    if count == 3:
        paths.extend([
            archive / "calibrated_v1.rou.xml",
            archive / "calibrated_v2.rou.xml",
        ])
    elif count != 1:
        raise ValueError(f"unsupported archived demand variant count {count}")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("archived demand routes missing: " + ", ".join(missing))
    return [path.resolve() for path in paths]


def _baseline(
    *,
    demand_key: str,
    archive: Path,
    metadata: Mapping[str, Any],
    variants: list[Path],
    run_root: Path,
    sumo_home: Path,
    seed_workers: int,
) -> tuple[closure_metrics.DisruptionMetrics, list[float], str, str]:
    inputs = [archive / "demand_meta.json", *variants]
    demand_digest = _bundle_digest(inputs)
    source_paths = [
        Path("run_scenario.py"),
        Path("suggest_closure_time.py"),
        Path("traffic_sim/simulation/metrics.py"),
        Path(__file__),
    ]
    source_digest = _bundle_digest(source_paths)
    runtime_version = str(sumo_version(sumo_home))
    cache_key = _canonical_digest({
        "demand_build_key": demand_key,
        "demand_digest": demand_digest,
        "network_sha256": sha256_file(rs.NET_PATH),
        "source_digest": source_digest,
        "sumo_version": runtime_version,
        "platform": platform.platform(),
        "seed_set": [1000, 1001, 1002],
        "simulation_mode": "meso",
        "metric_schema": "closure_decision_metrics_v1",
    })[:32]
    path = run_root / "baselines" / f"{cache_key}.json"
    if path.is_file():
        cached = _read(path)
        if (
            cached.get("cache_key") == cache_key
            and cached.get("demand_digest") == demand_digest
        ):
            evidence = {
                "metrics": cached.get("metrics"),
                "per_seed_time_loss_s": cached.get("per_seed_time_loss_s"),
            }
            if cached.get("evidence_sha256") != _canonical_digest(evidence):
                raise ValueError(
                    f"matched baseline cache is corrupt: {path}"
                )
            return (
                closure_metrics.DisruptionMetrics(**cached["metrics"]),
                [float(value) for value in cached["per_seed_time_loss_s"]],
                cache_key,
                demand_digest,
            )

    scratch: list[Path] = []
    temporary_root = Path(tempfile.mkdtemp(
        prefix=f"baseline-{demand_key}-", dir=str(run_root)
    ))
    try:
        metrics, _, _, per_seed = legacy.simulate_closure(
            name="baseline",
            closures=None,
            close_edges=[],
            variants=variants,
            seeds=3,
            n_intervals=int(metadata["n_intervals"]),
            duration_s=int(metadata["n_intervals"]) * 900,
            home=sumo_home,
            micro=False,
            adj=None,
            freeflow=None,
            scratch=scratch,
            work_dir=temporary_root / "run",
            seed_workers=seed_workers,
        )
        payload = {
            "schema_version": 1,
            "kind": "monthly_proxy_matched_baseline",
            "cache_key": cache_key,
            "demand_build_key": demand_key,
            "demand_digest": demand_digest,
            "network_sha256": sha256_file(rs.NET_PATH),
            "source_digest": source_digest,
            "sumo_version": runtime_version,
            "platform": platform.platform(),
            "seed_set": [1000, 1001, 1002],
            "simulation_mode": "meso",
            "metrics": dataclasses.asdict(metrics),
            "per_seed_time_loss_s": per_seed,
        }
        payload["evidence_sha256"] = _canonical_digest({
            "metrics": payload["metrics"],
            "per_seed_time_loss_s": payload["per_seed_time_loss_s"],
        })
        _atomic_json(path, payload)
        return metrics, per_seed, cache_key, demand_digest
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def _closure_seconds(schedule, epoch: datetime, edges: tuple[str, ...]) -> list[dict]:
    closures = []
    for interval in schedule.intervals:
        begin = int(
            (datetime.fromisoformat(interval.start_time) - epoch).total_seconds()
        )
        end = int(
            (datetime.fromisoformat(interval.end_time) - epoch).total_seconds()
        )
        if begin < 0 or end <= begin:
            raise ValueError(
                f"schedule {schedule.schedule_id} lies outside demand epoch"
            )
        for edge in edges:
            closures.append({"edge_id": edge, "begin_s": begin, "end_s": end})
    return closures


def _run_case(
    descriptor: Mapping[str, Any],
    *,
    archive: Path,
    run_root: Path,
    sumo_home: Path,
    seed_workers: int,
) -> dict[str, Any]:
    case_id = descriptor["case_id"]
    case_path = run_root / "cases" / f"{case_id}.json"

    spec = ClosureSearchSpec.from_dict(descriptor["closure_search_spec"])
    schedules = generate_closure_schedules(spec)
    if [schedule.schedule_id for schedule in schedules] != descriptor["schedule_ids"]:
        raise ValueError(f"frozen schedules changed for {case_id}")
    case_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path = case_path.with_suffix(".closure-search.json")
    write_closure_search_spec(spec_path, spec)
    screening = build_screening_artifact(
        spec_path,
        road_domain_status="in_domain",
        policy=VALIDATION_POLICY,
    )
    ranked = {
        candidate["schedule_id"]: candidate
        for candidate in screening["ranked_candidates"]
    }
    shortlisted = {
        entry["schedule_id"]
        for entry in screening["shortlist"]["entries"]
    }
    proxy_digest = _canonical_digest(screening)
    if case_path.is_file():
        saved = _read(case_path)
        if (
            saved.get("case_id") == case_id
            and saved.get("status") == "complete"
            and saved.get("closure_search_content_key") == spec.content_key
            and saved.get("proxy_input_sha256") == proxy_digest
        ):
            return saved["outcome"]

    metadata = _read(archive / "demand_meta.json")
    if metadata.get("demand_build_key") != spec.demand_build_id:
        raise ValueError(f"demand archive differs for {case_id}")
    variants = _variants(archive, metadata)
    baseline, baseline_seeds, baseline_id, demand_digest = _baseline(
        demand_key=spec.demand_build_id,
        archive=archive,
        metadata=metadata,
        variants=variants,
        run_root=run_root,
        sumo_home=sumo_home,
        seed_workers=seed_workers,
    )
    epoch = datetime.fromisoformat(str(metadata["epoch_sim"]))
    close_edges = list(spec.directed_edges)
    adjacency = rs.build_edge_graph(set(close_edges))
    freeflow = rs.edge_freeflow_times()
    rerouter_edges = rs.edges_near(close_edges, rs.REROUTER_RADIUS_M)
    detour = legacy.detour_availability(close_edges, rs.NET_PATH)

    progress = {
        "schema_version": 1,
        "kind": "monthly_proxy_validation_case_progress",
        "status": "running",
        "case_id": case_id,
        "closure_search_content_key": spec.content_key,
        "proxy_input_sha256": proxy_digest,
        "candidates": {},
    }
    if case_path.is_file():
        existing = _read(case_path)
        if (
            existing.get("closure_search_content_key") == spec.content_key
            and existing.get("proxy_input_sha256") == proxy_digest
        ):
            progress["candidates"] = dict(existing.get("candidates", {}))

    for index, schedule in enumerate(schedules):
        if schedule.schedule_id in progress["candidates"]:
            continue
        temporary_root = Path(tempfile.mkdtemp(
            prefix=f"{case_id}-{index:03}-", dir=str(run_root)
        ))
        scratch: list[Path] = []
        try:
            closures = _closure_seconds(schedule, epoch, spec.directed_edges)
            metrics, truncated, dropped, per_seed = legacy.simulate_closure(
                name=f"candidate-{index:03}",
                closures=closures,
                close_edges=close_edges,
                variants=variants,
                seeds=3,
                n_intervals=int(metadata["n_intervals"]),
                duration_s=int(metadata["n_intervals"]) * 900,
                home=sumo_home,
                micro=False,
                adj=adjacency,
                freeflow=freeflow,
                scratch=scratch,
                rerouter_edges=rerouter_edges,
                work_dir=temporary_root / "run",
                seed_workers=seed_workers,
            )
            interval = legacy.delta_time_loss_interval(
                per_seed, baseline_seeds
            )
            feasibility = legacy.closure_feasibility(
                metrics, baseline, detour=detour
            )
            candidate_proxy = ranked.get(schedule.schedule_id)
            progress["candidates"][schedule.schedule_id] = {
                "schedule_id": schedule.schedule_id,
                "eligible": bool(feasibility["eligible"]),
                "sumo_objective": (
                    float(interval["median_s"])
                    if feasibility["eligible"]
                    else None
                ),
                "proxy_rank": (
                    int(candidate_proxy["proxy_rank"])
                    if candidate_proxy is not None
                    else None
                ),
                "shortlisted": schedule.schedule_id in shortlisted,
                "proxy_failure_flag": candidate_proxy is None,
                "hard_failures": feasibility["hard_failures"],
                "paired_delta_time_loss": interval,
                "truncated_unreachable": truncated,
                "dropped_unreachable": dropped,
            }
            _atomic_json(case_path, progress)
            print(
                f"[{case_id}] {index + 1}/{len(schedules)} "
                f"{schedule.daily_start} "
                f"{'eligible' if feasibility['eligible'] else 'disqualified'}",
                flush=True,
            )
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    ordered = [
        progress["candidates"][schedule.schedule_id]
        for schedule in schedules
    ]
    outcome = {
        "case_id": case_id,
        "exhaustive": True,
        "provenance": {
            "network_sha256": str(sha256_file(rs.NET_PATH)),
            "demand_sha256": demand_digest,
            "proxy_input_sha256": proxy_digest,
            "simulation_mode": "meso",
            "seed_set": [1000, 1001, 1002],
            "sumo_version": str(sumo_version(sumo_home)),
            "matched_baseline_id": baseline_id,
        },
        "candidates": ordered,
    }
    progress["status"] = "complete"
    progress["outcome"] = outcome
    _atomic_json(case_path, progress)
    return outcome


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--case", action="append", default=[],
        help="Run only this frozen case ID (repeatable).",
    )
    parser.add_argument(
        "--seed-workers", type=int, default=1,
        help="Concurrent SUMO seeds (default 1; use >1 only after benchmarking).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.seed_workers < 1:
        raise SystemExit("--seed-workers must be at least 1")
    manifest = validate_validation_manifest(_read(args.manifest))
    run_root = DEFAULT_ROOT / manifest["content_key"]
    run_root.mkdir(parents=True, exist_ok=True)
    selected = set(args.case)
    unknown = selected - {case["case_id"] for case in manifest["cases"]}
    if unknown:
        raise SystemExit("unknown validation cases: " + ", ".join(sorted(unknown)))
    archives = _demand_archives()
    sumo_home = rs.sumo_home()
    outcomes = []
    missing = []
    for descriptor in manifest["cases"]:
        case_id = descriptor["case_id"]
        case_path = run_root / "cases" / f"{case_id}.json"
        if selected and case_id not in selected:
            if case_path.is_file():
                saved = _read(case_path)
                if saved.get("status") == "complete":
                    outcomes.append(saved["outcome"])
            continue
        demand_key = descriptor["closure_search_spec"]["demand_build_id"]
        archive = archives.get(demand_key)
        if archive is None:
            missing.append((case_id, demand_key))
            print(f"[{case_id}] missing archived demand {demand_key}", flush=True)
            continue
        outcomes.append(_run_case(
            descriptor,
            archive=archive,
            run_root=run_root,
            sumo_home=sumo_home,
            seed_workers=args.seed_workers,
        ))

    partial = {
        "schema_version": 1,
        "kind": "monthly_proxy_validation_outcomes",
        "proxy_version": manifest["proxy_version"],
        "shortlist_version": manifest.get("shortlist_version"),
        "shortlist_policy_content_key": manifest.get(
            "shortlist_policy_content_key"
        ),
        "manifest_content_key": manifest["content_key"],
        "cases": outcomes,
        "missing_cases": [
            {"case_id": case_id, "demand_build_id": demand_key}
            for case_id, demand_key in missing
        ],
    }
    _atomic_json(run_root / "outcomes.partial.json", partial)
    if len(outcomes) == len(manifest["cases"]):
        complete = {key: value for key, value in partial.items()
                    if key != "missing_cases"}
        _atomic_json(run_root / "outcomes.json", complete)
        report = evaluate_validation_set(manifest, complete)
        _atomic_json(run_root / "report.json", report)
        gate_record = gate_record_for(report, manifest, len(outcomes))
        if gate_record is not None:
            _atomic_json(run_root / "gate_record.json", gate_record)
        print(
            f"Validation {report['gate_status']}: {report['metrics']}; "
            f"UI exposure={report['ui_exposure_allowed']}"
        )
    else:
        partial_report = evaluate_partial_validation_set(manifest, partial)
        _atomic_json(run_root / "report.partial.json", partial_report)
        print(
            f"Validation {partial_report['gate_status']}: "
            f"{len(outcomes)}/{len(manifest['cases'])} cases complete; "
            f"winner-recall upper bound="
            f"{partial_report['metrics']['winner_recall_upper_bound']}; "
            "UI exposure remains false."
        )


if __name__ == "__main__":
    main()
