#!/usr/bin/env python3
"""Verify PR D/E against the pinned real golden benchmark without new SUMO.

This is development evidence, not held-out or release evidence. It validates
the already-pinned archive and completed benchmark workspace, recomputes the
deterministic disruption with current sources, and compares:

* process-free provider vs the current runner path, field for field;
* current cost/order vs the preserved pre-PR-D benchmark cost/order; and
* cost-ordered replay vs exhaustive pilot selection on the same observations.

No simulator is started and no existing run artifact is modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.contracts import (
    ClosureSchedule,
    load_closure_search_spec,
)
from traffic_sim.simulation.closure_ranking import (
    rank_closures,
    worst_variant_cost,
)
from traffic_sim.simulation.cost_ordered_search import (
    CostOrderedCandidate,
    compare_with_exhaustive,
    replay_shadow,
)
from traffic_sim.simulation.deterministic_disruption import (
    ArchiveDisruptionProvider,
    NetworkCostModel,
)
from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    PairedObservation,
)
from traffic_sim.simulation.monthly_search import MonthlySearchPolicy
from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner
from traffic_sim.simulation.pilot_selection import select_pilot_finalists
from traffic_sim.simulation.search_workspace import load_search_workspace


DEFAULT_OUT = ROOT / "validation" / "closure_cost_ordering_golden_v1.json"
PLAN_PATH = ROOT / "validation" / "monthly_search_v2_benchmark_plan_v1.json"
WORKSPACE_RELATIVE = (Path("runs") / "closure-objective-v2-benchmark-v1"
                      / "search" / "golden-monthly-search-2025-09-16-v6")
SCHEMA = "closure_cost_ordering_golden_v1"
MEASURED_AT = "2026-08-11"
CONTRACT_FIELDS = (
    "demand_variant",
    "vehicles_affected",
    "vehicles_considered",
    "vehicles_no_detour",
    "added_vehicle_hours",
    "added_metres_total",
)
SOURCE_PATHS = (
    "tools/verify_closure_cost_ordering_golden.py",
    "run_scenario.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/cost_ordered_search.py",
    "traffic_sim/simulation/closure_ranking.py",
    "traffic_sim/simulation/monthly_sumo.py",
    "traffic_sim/simulation/pilot_selection.py",
)


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _record(path: Path, *, data_root: Path | None = None) -> dict[str, Any]:
    path = Path(path).resolve()
    label = str(path)
    for root in (ROOT, data_root):
        if root is None:
            continue
        try:
            label = str(path.relative_to(Path(root).resolve()))
            break
        except ValueError:
            pass
    return {"path": label, "sha256": _sha256(path),
            "bytes": path.stat().st_size}


def _validate_plan_inputs(
    plan: Mapping[str, Any], data_root: Path,
) -> dict[str, Path]:
    if plan.get("schema_version") != 1:
        raise ValueError("unsupported v2 benchmark plan schema")
    inputs = plan.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError("v2 benchmark plan inputs are required")
    resolved: dict[str, Path] = {}
    for label, raw_record in inputs.items():
        if not isinstance(raw_record, Mapping):
            raise ValueError(f"benchmark input {label} must be an object")
        relative = Path(str(raw_record.get("path", "")))
        base = data_root if relative.parts[:1] == ("runs",) else ROOT
        path = (base / relative).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = _sha256(path)
        expected = str(raw_record.get("sha256", ""))
        if actual != expected:
            raise ValueError(
                f"benchmark input {label} drifted: {actual} != {expected}")
        resolved[str(label)] = path
    required = {
        "search_spec", "policy", "benchmark_runner", "archive_manifest",
        "demand_meta", "demand_routes_q10", "demand_routes_q50",
        "demand_routes_q90", "sumo_network",
    }
    if not required <= set(resolved):
        raise ValueError(
            f"benchmark plan lacks inputs: {sorted(required - set(resolved))}")
    resolved["archive"] = resolved["archive_manifest"].parent
    return resolved


def _evidence(raw: Mapping[str, Any], disruption) -> CandidateEvidence:
    candidate_id = str(raw["candidate_id"])
    observations = tuple(PairedObservation(
        candidate_id=str(item["candidate_id"]),
        demand_variant=str(item["demand_variant"]),
        seed=int(item["seed"]),
        baseline_time_loss_s=float(item["baseline_time_loss_s"]),
        candidate_time_loss_s=float(item["candidate_time_loss_s"]),
        matched_baseline_id=str(item["matched_baseline_id"]),
        provenance_key=str(item["provenance_key"]),
        simulation_mode=str(item.get("simulation_mode", "meso")),
    ) for item in raw.get("observations", ()))
    return CandidateEvidence(
        candidate_id=candidate_id,
        observations=observations,
        hard_failures=tuple(str(value) for value in raw.get(
            "hard_failures", ())),
        disruption=tuple(dict(item) for item in disruption),
    )


def _contract_projection(records) -> list[dict[str, Any]]:
    return [{field: item.get(field) for field in CONTRACT_FIELDS}
            for item in records]


def _cost_record(cost) -> dict[str, Any]:
    return {
        "candidate_id": cost.candidate_id,
        "added_vehicle_hours": float(cost.added_vehicle_hours),
        "added_metres_total": float(cost.added_metres_total),
        "vehicles_affected": int(cost.vehicles_affected),
        "vehicles_no_detour": int(cost.vehicles_no_detour),
    }


def build_record(*, data_root: Path = ROOT) -> dict[str, Any]:
    data_root = Path(data_root).resolve()
    plan = _read(PLAN_PATH)
    paths = _validate_plan_inputs(plan, data_root)
    workspace_path = data_root / WORKSPACE_RELATIVE
    workspace = load_search_workspace(workspace_path, verify=True)
    if workspace.status != "succeeded":
        raise ValueError("golden benchmark workspace is not succeeded")

    spec = load_closure_search_spec(paths["search_spec"])
    policy = MonthlySearchPolicy.from_dict(_read(paths["policy"]))
    ledger_path = workspace_path / "artifacts" / "candidate-ledger.json"
    ledger = _read(ledger_path)
    if ledger.get("search_content_key") != spec.content_key:
        raise ValueError("golden candidate ledger belongs to another search")
    schedules = tuple(
        ClosureSchedule.from_dict(raw) for raw in ledger.get("schedules", ())
    )
    if not schedules or len(schedules) != int(ledger.get("candidate_count", -1)):
        raise ValueError("golden candidate ledger is empty or inconsistent")

    # The runner uses run_scenario's module paths when it builds its shared
    # network tables. Bind those globals to the plan-validated network rather
    # than trusting the process working directory.
    import run_scenario as rs
    rs.NET_PATH = paths["sumo_network"]
    rs.SUMO_DIR = paths["sumo_network"].parent
    rs._FREE_FLOW_EDGE_TIME = None

    independent = ArchiveDisruptionProvider(
        spec,
        archive=paths["archive"],
        network=NetworkCostModel(paths["sumo_network"]),
    )
    runner = ArchivedDemandSumoRunner(
        spec,
        archive=paths["archive"],
        baseline_trip_duration_p99_s=int(
            plan["baseline_trip_duration_p99_s"]),
        study_provenance_key="golden-pr-d-e-verification-v1",
        cache_root=ROOT / "runs" / "closure-objective-v2-benchmark-v1"
        / "baseline-cache",
        seed_workers=1,
        include_disruption=True,
        warm_execution=False,
    )

    candidates: list[CostOrderedCandidate] = []
    evidence: dict[str, CandidateEvidence] = {}
    current_costs = []
    recorded_costs = []
    cases = []
    pilot_paths = []
    for schedule in schedules:
        pre_sumo = tuple(dict(item) for item in independent.disruption(schedule))
        runner_path = tuple(dict(item) for item in
                            runner._closure_disruption(schedule))
        pilot_path = (workspace_path / "artifacts" / "pilot"
                      / f"{schedule.schedule_id}-r000.json")
        raw_evidence = _read(pilot_path)
        pilot_paths.append(pilot_path)
        recorded = tuple(dict(item) for item in raw_evidence["disruption"])

        current_cost = worst_variant_cost(schedule.schedule_id,
                                          list(pre_sumo))
        recorded_cost = worst_variant_cost(schedule.schedule_id,
                                           list(recorded))
        current_costs.append(current_cost)
        recorded_costs.append(recorded_cost)
        candidates.append(CostOrderedCandidate(
            candidate_id=schedule.schedule_id,
            cost=current_cost,
            disruption=pre_sumo,
        ))
        evidence[schedule.schedule_id] = _evidence(raw_evidence, pre_sumo)
        cases.append({
            "candidate_id": schedule.schedule_id,
            "provider_runner_field_identical": pre_sumo == runner_path,
            "recorded_contract_fields_identical": (
                _contract_projection(pre_sumo)
                == _contract_projection(recorded)
            ),
            "current_only_fields": sorted(
                set().union(*(set(item) for item in pre_sumo))
                - set().union(*(set(item) for item in recorded))
            ),
            "current_cost": _cost_record(current_cost),
            "recorded_cost": _cost_record(recorded_cost),
        })

    exhaustive = select_pilot_finalists(
        [evidence[item.candidate_id] for item in candidates],
        policy.pilot,
        ranking_objective="closure_cost_v1",
        practical_equivalence_vehicle_hours=(
            policy.finalist.practical_equivalence_vehicle_hours),
    )
    cost_ordered = replay_shadow(
        candidates,
        policy.pilot,
        evidence,
        search_content_key=spec.content_key,
        provider_identity=independent.identity(),
        practical_equivalence_vehicle_hours=(
            policy.finalist.practical_equivalence_vehicle_hours),
    )
    comparison = compare_with_exhaustive(cost_ordered, exhaustive)
    comparison["evidence_class"] = "diagnostic_replay_of_preserved_pilot"
    comparison["release_evidence"] = False

    current_order = [item.candidate_id for item in rank_closures(
        current_costs)[0]]
    recorded_order = [item.candidate_id for item in rank_closures(
        recorded_costs)[0]]
    provider_runner_equal = all(
        item["provider_runner_field_identical"] for item in cases)
    recorded_contract_equal = all(
        item["recorded_contract_fields_identical"] for item in cases)
    ordering_equal = current_order == recorded_order
    named_benchmark_equal = bool(comparison["equivalent"])

    inputs = {
        "benchmark_plan": _record(PLAN_PATH),
        "workspace_manifest": _record(
            workspace_path / "manifest.json", data_root=data_root),
        "candidate_ledger": _record(ledger_path, data_root=data_root),
        "pilot_evidence": [
            _record(path, data_root=data_root) for path in pilot_paths],
        "archive_manifest": _record(
            paths["archive_manifest"], data_root=data_root),
        "demand_meta": _record(paths["demand_meta"], data_root=data_root),
        "routes": {
            variant: _record(paths[label], data_root=data_root)
            for variant, label in (("q10", "demand_routes_q10"),
                                   ("q50", "demand_routes_q50"),
                                   ("q90", "demand_routes_q90"))
        },
        "network": _record(paths["sumo_network"], data_root=data_root),
    }
    return {
        "schema": SCHEMA,
        "measured_at": MEASURED_AT,
        "kind": "closure_cost_ordering_golden_verification",
        "evidence_class": "diagnostic_development_benchmark",
        "release_evidence": False,
        "heldout_evidence": False,
        "inputs": inputs,
        "sources": {name: _record(ROOT / name) for name in SOURCE_PATHS},
        "cases": cases,
        "ordering": {
            "current": current_order,
            "recorded": recorded_order,
            "identical": ordering_equal,
        },
        "cost_ordered_comparison": comparison,
        "gates": {
            "pr_d_real_golden_provider_runner": {
                "status": "passed" if provider_runner_equal else "failed",
                "field_identical": provider_runner_equal,
                "recorded_cost_contract_identical": recorded_contract_equal,
                "sorting_order_identical": ordering_equal,
            },
            "pr_e_named_real_benchmark": {
                "status": "passed" if named_benchmark_equal else "failed",
                "pilot_status_identical": comparison["status_matches"],
                "selected_ids_identical": comparison[
                    "selected_ids_match"],
                "sumo_verifications_saved": comparison[
                    "sumo_verifications_saved"],
            },
            "policy_v3_activation": {
                "status": "closed",
                "reason": (
                    "this preserved benchmark has only one health-viable "
                    "finalist and is diagnostic, not the pre-registered "
                    "discriminating benchmark or held-out evidence"),
            },
        },
        "claim_boundary": {
            "opens_global_best": False,
            "activates_policy_v3": False,
            "permits_ui_claim": False,
            "reason": (
                "real golden development replay; no new SUMO execution and "
                "no held-out observation"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--data-root", type=Path, default=ROOT,
        help="repository root that owns ignored runs/ (default: this repo)",
    )
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    record = build_record(data_root=args.data_root)
    encoded = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.verify:
        if not args.out.is_file():
            raise SystemExit(f"verification record is missing: {args.out}")
        if args.out.read_text(encoding="utf-8") != encoded:
            raise SystemExit("verification record does not reproduce")
        print("reproduces byte-for-byte: True")
        return 0
    if args.out.exists() and not args.overwrite:
        raise SystemExit(f"refusing to overwrite {args.out}; pass --overwrite")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(encoded, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
