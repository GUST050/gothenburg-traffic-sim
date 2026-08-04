#!/usr/bin/env python3
"""Measure whether a verified prefix can be extended to a later checkpoint.

This is diagnostic evidence, never cache publication or annual population. It
compares a candidate-free prefix advanced from an adopted v16 state with an
independent zero-to-target bootstrap under the current implementation.
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_MANIFEST = ROOT / "validation/monthly_warm_state_manifest_v16.json"
DEFAULT_CACHE = ROOT / "runs/warm-state"
DEFAULT_OUTPUT = ROOT / "validation/warm_prefix_chaining_diagnostic_v1.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("q10", "q50", "q90"),
                        default="q10")
    parser.add_argument("--target-s", type=int, default=110_700)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_entry(variant: str) -> Path:
    matches = []
    for manifest_path in sorted(DEFAULT_CACHE.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("identity", {}).get("demand_variant") == variant:
            matches.append(manifest_path.parent)
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one adopted {variant} cache entry, found {len(matches)}")
    return matches[0]


def _state_record(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    expanded = gzip.decompress(raw)
    return {
        "compressed_bytes": len(raw),
        "compressed_sha256": _sha256(raw),
        "expanded_bytes": len(expanded),
        "expanded_sha256": _sha256(expanded),
    }


def _evidence_sections(evidence: dict[str, object]) -> dict[str, object]:
    sections: dict[str, object] = {}
    for name, value in sorted(evidence.items()):
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode()
        sections[name] = {
            "sha256": _sha256(encoded),
            "equal_value": value,
        }
    return sections


def run(variant: str, target_s: int) -> dict[str, object]:
    from run_monthly_warm_state_validation import build_runner
    from traffic_sim.core.closure_calendar import generate_closure_schedules
    from traffic_sim.simulation.monthly_search import canonical_seed
    from traffic_sim.simulation.monthly_warm_state import (
        parse_prefix_evidence,
        plan_warm_execution,
        reconstruct_metrics,
    )
    from traffic_sim.simulation.warm_state_boundary import (
        aggregate_split_time_loss,
    )
    from traffic_sim.simulation import metrics as closure_metrics

    manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    entry = _cache_entry(variant)
    prior_manifest = json.loads(
        (entry / "manifest.json").read_text(encoding="utf-8"))
    prior_point = prior_manifest["identity"]["warmup_end_s"]
    if target_s <= prior_point:
        raise ValueError("target must be later than the adopted prefix")
    prior_evidence = parse_prefix_evidence(
        json.loads((entry / "prefix_evidence.json").read_text(encoding="utf-8")),
        expected_warm_point_s=prior_point,
    )
    seed = canonical_seed(variant, 0)
    with tempfile.TemporaryDirectory(
        prefix="warm-prefix-chain-", dir=ROOT / "runs"
    ) as raw_workspace:
        workspace = Path(raw_workspace)
        runner = build_runner(
            manifest, warm=True, workspace=workspace / "runner")

        chain_started = time.monotonic()
        chained = runner.extend_warm_state(
            variant=variant, seed=seed, warm_point_s=target_s,
            workspace=workspace / "chained",
            previous_state_path=entry / "state.xml.gz",
            previous_prefix_evidence=prior_evidence,
            schedule_id="diagnostic-prefix-chain",
        )
        chain_runtime = time.monotonic() - chain_started
        if chained is None:
            raise RuntimeError("candidate-free chained prefix declined")

        cold_started = time.monotonic()
        cold = runner.bootstrap_warm_state(
            variant=variant, seed=seed, warm_point_s=target_s,
            workspace=workspace / "cold",
            schedule_id="diagnostic-prefix-cold",
        )
        cold_runtime = time.monotonic() - cold_started
        if cold is None:
            raise RuntimeError("candidate-free zero-to-target prefix declined")

        chained_evidence = parse_prefix_evidence(
            chained["prefix_evidence"], expected_warm_point_s=target_s)
        cold_evidence = parse_prefix_evidence(
            cold["prefix_evidence"], expected_warm_point_s=target_s)
        chained_state = _state_record(Path(chained["state_path"]))
        cold_state = _state_record(Path(cold["state_path"]))
        chain_sections = _evidence_sections(chained_evidence)
        cold_sections = _evidence_sections(cold_evidence)
        section_comparison = {
            name: {
                "equal": chain_sections[name]["sha256"]
                == cold_sections[name]["sha256"],
                "chained_sha256": chain_sections[name]["sha256"],
                "cold_sha256": cold_sections[name]["sha256"],
            }
            for name in sorted(chain_sections)
        }
        chained_active = chained_evidence["active_accumulator"]
        cold_active = cold_evidence["active_accumulator"]
        chained_entries = chained_active["entries"]
        cold_entries = cold_active["entries"]
        shared_active = sorted(set(chained_entries) & set(cold_entries))
        active_deltas = {
            identifier: chained_entries[identifier] - cold_entries[identifier]
            for identifier in shared_active
            if chained_entries[identifier] != cold_entries[identifier]
        }
        schedules = [
            schedule for schedule in generate_closure_schedules(runner.spec)
            if runner.warm_decision(schedule).warm_point_s == target_s
        ]
        if len(schedules) != 1:
            raise RuntimeError(
                f"expected one real schedule at {target_s}, found {len(schedules)}")
        schedule = schedules[0]
        closures = runner._closure_seconds(schedule)

        def suffix(label: str, material: dict[str, object]):
            arm_workspace = workspace / label / "suffix"
            plan = plan_warm_execution(
                warm_point_s=target_s,
                state_path=Path(material["state_path"]),
                unfiltered_route=runner.variants[variant],
                filtered_route=arm_workspace / "filtered.rou.xml",
                closure_additional=arm_workspace / "closure.add.xml",
            )
            started = time.monotonic()
            measured = runner._default_warm_invoker(
                plan=plan, schedule=schedule, variant=variant, seed=seed,
                closures=closures, workspace=arm_workspace,
                prefix_evidence=material["prefix_evidence"],
            )
            if measured is None:
                raise RuntimeError(f"{label} suffix declined")
            evidence = parse_prefix_evidence(
                material["prefix_evidence"],
                expected_warm_point_s=target_s)
            aggregation = aggregate_split_time_loss(
                completed_prefix_total=evidence["completed_trips"][
                    "total_time_loss_s"],
                completed_prefix_trips=evidence["completed_trips"][
                    "trip_count"],
                resumed_total=measured["post_warm_metrics"].total_time_loss_s,
                resumed_trips=measured["post_warm_metrics"].trip_count,
                continued_total=measured["restore_correction"][
                    "combined_total_time_loss_s"],
            )
            whole_metrics = closure_metrics.DisruptionMetrics(
                **reconstruct_metrics(
                    evidence, measured["post_warm_metrics"],
                    expected_warm_point_s=target_s,
                    aggregation=aggregation,
                    continued_total=measured["restore_correction"][
                        "combined_total_time_loss_s"],
                ))
            return time.monotonic() - started, {
                "whole_metrics": dataclasses.asdict(whole_metrics),
                "post_warm_metrics": dataclasses.asdict(
                    measured["post_warm_metrics"]),
                "raw_post_warm_metrics": dataclasses.asdict(
                    measured["raw_post_warm_metrics"]),
                "restore_correction": measured["restore_correction"],
                "candidate_buckets": [
                    dataclasses.asdict(bucket)
                    for bucket in measured["candidate_buckets"]
                ],
            }

        chained_suffix_runtime, chained_suffix = suffix("chained", chained)
        cold_suffix_runtime, cold_suffix = suffix("cold", cold)
        suffix_equal = chained_suffix == cold_suffix

        cold_runner = build_runner(
            manifest, warm=False, workspace=workspace / "direct-cold-runner")
        cold_runner._run_baseline(variant, seed)
        direct_cold_started = time.monotonic()
        _observation, _failures, direct_cold = cold_runner._run_observation(
            schedule, variant=variant, seed=seed)
        direct_cold_runtime = time.monotonic() - direct_cold_started
        direct_cold_metrics = direct_cold["candidate_metrics"]
        warm_metrics_equal = (
            chained_suffix["whole_metrics"] == direct_cold_metrics)
        result = {
            "schema": "warm_prefix_chaining_diagnostic_v1",
            "status": "pass" if (
                chained_evidence == cold_evidence
                and suffix_equal
                and warm_metrics_equal
            ) else "fail",
            "diagnostic_only": True,
            "variant": variant,
            "seed": seed,
            "from_s": prior_point,
            "target_s": target_s,
            "replayed_prefix_s": target_s,
            "chained_segment_s": target_s - prior_point,
            "prefix_evidence_equal": chained_evidence == cold_evidence,
            "prefix_evidence_sections": section_comparison,
            "active_accumulator_comparison": {
                "population_digest_equal": (
                    chained_active["active_population_digest"]
                    == cold_active["active_population_digest"]),
                "chained_count": len(chained_entries),
                "cold_count": len(cold_entries),
                "missing_from_chained": sorted(
                    set(cold_entries) - set(chained_entries)),
                "extra_in_chained": sorted(
                    set(chained_entries) - set(cold_entries)),
                "value_mismatch_count": len(active_deltas),
                "value_delta_sum_s": sum(active_deltas.values()),
                "value_max_absolute_delta_s": max(
                    (abs(value) for value in active_deltas.values()),
                    default=0.0),
            },
            "expanded_state_equal": (
                chained_state["expanded_sha256"]
                == cold_state["expanded_sha256"]),
            "expanded_state_bytes_are_release_gate": False,
            "identical_closure_suffix": suffix_equal,
            "warm_metrics_equal_direct_cold": warm_metrics_equal,
            "closure_schedule_id": schedule.schedule_id,
            "runtime_s": {
                "chained": round(chain_runtime, 6),
                "cold": round(cold_runtime, 6),
                "chained_closure_suffix": round(
                    chained_suffix_runtime, 6),
                "cold_prefix_closure_suffix": round(
                    cold_suffix_runtime, 6),
                "direct_cold_candidate": round(direct_cold_runtime, 6),
            },
            "cache_hit_speedup_vs_direct_cold": round(
                direct_cold_runtime / chained_suffix_runtime, 6),
            "speedup": round(cold_runtime / chain_runtime, 6),
            "chained_state": chained_state,
            "cold_state": cold_state,
            "source_cache_entry": entry.relative_to(ROOT).as_posix(),
            "source_cache_manifest_sha256": _sha256(
                (entry / "manifest.json").read_bytes()),
        }
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run(args.variant, args.target_s)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
