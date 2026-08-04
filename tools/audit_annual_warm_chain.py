#!/usr/bin/env python3
"""Audit one populated annual checkpoint chain against independent cold runs.

This is bounded diagnostic evidence, not cache publication.  It validates every
stored link in one chain and independently bootstraps selected link positions
from zero to prove that chained behavioural evidence remains exact at depth.

SUMO's ``loaded`` statistic includes a route-loader lookahead cursor.  A cold
run parsing the full route may therefore have loaded future definitions that a
departure-bounded extension deliberately has not parsed.  That field is not a
vehicle-state or outcome equality gate.  The audit records the difference and
still requires exact inserted/teleport counters and every behavioural section.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.plan_annual_warming import DEFAULT_OUTPUT, build_default_plan  # noqa: E402
from tools.populate_annual_warming import _reference_edge  # noqa: E402
from traffic_sim.core.contracts import DemandBuildSpec  # noqa: E402
from traffic_sim.simulation.annual_warm_plan import (  # noqa: E402
    iter_work_units,
    load_annual_warm_plan,
)
from traffic_sim.simulation.annual_warm_population import (  # noqa: E402
    AnnualWarmPlanContext,
    annual_unit_context,
    build_annual_prefix_runner,
    validate_restored_unit_payload,
    validate_unit_artifact,
)
from traffic_sim.simulation.annual_warm_store import (  # noqa: E402
    AnnualWarmArtifactStore,
)
from traffic_sim.simulation.monthly_demand import validate_demand_archive  # noqa: E402
from traffic_sim.simulation.monthly_warm_state import parse_prefix_evidence  # noqa: E402


DEFAULT_OUTPUT_PATH = Path("validation/annual_warm_chain_pilot_v1.json")
STATE_INFLATION_LIMIT = 4.0
STATE_EXPANDED_LIMIT_BYTES = 16 * 1024**2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--demand-build-key", required=True)
    parser.add_argument("--variant", choices=("q10", "q50", "q90"), required=True)
    parser.add_argument(
        "--positions", default="2,48,96",
        help="One-based link positions to compare with cold bootstraps.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser


def _positions(raw: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip()) for item in raw.split(","))
    except ValueError as exc:
        raise ValueError("chain positions must be comma-separated integers") from exc
    if not values or any(value < 1 for value in values) \
            or tuple(sorted(set(values))) != values:
        raise ValueError("chain positions must be unique positive integers in order")
    return values


def _state_record(path: Path) -> dict[str, Any]:
    compressed = hashlib.sha256()
    compressed_bytes = 0
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            compressed.update(block)
            compressed_bytes += len(block)
    expanded = hashlib.sha256()
    expanded_bytes = 0
    with gzip.open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            expanded.update(block)
            expanded_bytes += len(block)
    return {
        "compressed_bytes": compressed_bytes,
        "compressed_sha256": compressed.hexdigest(),
        "expanded_bytes": expanded_bytes,
        "expanded_sha256": expanded.hexdigest(),
    }


def _evidence_comparison(
    chained: dict[str, Any], cold: dict[str, Any]
) -> dict[str, Any]:
    sections = {}
    for name in sorted(chained):
        chained_bytes = json.dumps(
            chained[name], sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode()
        cold_bytes = json.dumps(
            cold[name], sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode()
        sections[name] = {
            "equal": chained_bytes == cold_bytes,
            "chained_sha256": hashlib.sha256(chained_bytes).hexdigest(),
            "cold_sha256": hashlib.sha256(cold_bytes).hexdigest(),
        }
    chained_active = chained["active_accumulator"]
    cold_active = cold["active_accumulator"]
    chained_entries = chained_active["entries"]
    cold_entries = cold_active["entries"]
    shared = set(chained_entries) & set(cold_entries)
    deltas = {
        identifier: chained_entries[identifier] - cold_entries[identifier]
        for identifier in shared
        if chained_entries[identifier] != cold_entries[identifier]
    }
    chained_completed = chained["completed_records"]
    cold_completed = cold["completed_records"]
    completed_shared = set(chained_completed) & set(cold_completed)
    completed_deltas = {
        identifier: chained_completed[identifier] - cold_completed[identifier]
        for identifier in completed_shared
        if chained_completed[identifier] != cold_completed[identifier]
    }
    return {
        "sections": sections,
        "active_accumulator": {
            "chained_count": len(chained_entries),
            "cold_count": len(cold_entries),
            "missing_from_chained_count": len(set(cold_entries) - set(chained_entries)),
            "extra_in_chained_count": len(set(chained_entries) - set(cold_entries)),
            "value_mismatch_count": len(deltas),
            "value_delta_sum_s": sum(deltas.values()),
            "value_max_absolute_delta_s": max(
                (abs(value) for value in deltas.values()), default=0.0
            ),
        },
        "completed_trips": {
            "chained": chained["completed_trips"],
            "cold": cold["completed_trips"],
            "missing_from_chained_count": len(
                set(cold_completed) - set(chained_completed)
            ),
            "extra_in_chained_count": len(
                set(chained_completed) - set(cold_completed)
            ),
            "value_mismatch_count": len(completed_deltas),
            "value_delta_sum_s": sum(completed_deltas.values()),
            "value_max_absolute_delta_s": max(
                (abs(value) for value in completed_deltas.values()), default=0.0
            ),
        },
        "counters": {"chained": chained["counters"], "cold": cold["counters"]},
    }


def _behavioral_evidence_equal(
    chained: dict[str, Any], cold: dict[str, Any]
) -> bool:
    """Compare every outcome-relevant field, excluding loader lookahead.

    ``loaded`` remains relationship-checked on both sides and the route-window
    arm may not claim more parsed definitions than the full-route cold arm.
    This is deliberately narrower than dropping the whole counters section:
    insertions, teleports, reasons, and all other evidence remain exact gates.
    """
    if set(chained) != set(cold):
        return False
    if any(
        chained[name] != cold[name]
        for name in chained
        if name != "counters"
    ):
        return False
    chained_counters = chained["counters"]
    cold_counters = cold["counters"]
    if set(chained_counters) != {"loaded", "inserted", "teleport_total"} \
            or set(cold_counters) != set(chained_counters):
        return False
    if any(
        chained_counters[name] != cold_counters[name]
        for name in ("inserted", "teleport_total")
    ):
        return False
    return (
        chained_counters["loaded"] >= chained_counters["inserted"]
        and cold_counters["loaded"] >= cold_counters["inserted"]
        and chained_counters["loaded"] <= cold_counters["loaded"]
    )


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
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


def run(
    *, plan_path: Path, root: Path, demand_build_key: str, variant: str,
    positions: tuple[int, ...],
) -> dict[str, Any]:
    plan = load_annual_warm_plan(plan_path)
    if plan != build_default_plan():
        raise ValueError("annual chain audit plan differs from current bound sources")
    root = Path(root)
    stored_plan = load_annual_warm_plan(root / "plan.json")
    if stored_plan != plan:
        raise ValueError("annual chain root belongs to another plan")
    units = sorted(
        (
            dict(unit) for unit in iter_work_units(plan)
            if unit["demand_build_key"] == demand_build_key
            and unit["variant"] == variant
        ),
        key=lambda unit: unit["checkpoint_s"],
    )
    if not units:
        raise ValueError("annual chain audit selection is empty")
    if positions[-1] > len(units):
        raise ValueError(
            f"chain has {len(units)} links, cannot audit position {positions[-1]}"
        )

    indexed = AnnualWarmPlanContext(plan)
    store = AnnualWarmArtifactStore(root / "store")
    profile: list[dict[str, Any]] = []
    selected: dict[int, dict[str, Any]] = {}
    archive_record = None
    request = None
    required = None
    with tempfile.TemporaryDirectory(
        prefix="annual-chain-audit-", dir=root / ".staging"
    ) as raw_workspace:
        workspace = Path(raw_workspace)
        for link, unit in enumerate(units, start=1):
            unit_id = unit["unit_id"]
            if not store.artifact_exists(unit_id):
                raise ValueError(f"annual chain link {link} is not populated")
            artifact = store.load_artifact_manifest(unit_id)
            validate_unit_artifact(plan, unit_id, artifact, plan_context=indexed)
            _manifest, restored = store.restore_artifact_subset(
                unit_id,
                workspace / "profile" / f"{link:03d}",
                ("state.xml.gz", "prefix_evidence.json", "demand_build_spec.json"),
            )
            hint = {
                key: unit[key] for key in (
                    "request_id", "demand_build_key", "checkpoint_s", "seed", "variant"
                )
            }
            evidence = validate_restored_unit_payload(
                plan, unit_id, restored, unit_hint=hint, plan_context=indexed
            )
            state = _state_record(restored["state.xml.gz"])
            profile.append({
                "link": link,
                "unit_id": unit_id,
                "checkpoint_s": unit["checkpoint_s"],
                "state_compressed_bytes": state["compressed_bytes"],
                "state_expanded_bytes": state["expanded_bytes"],
            })
            if link in positions:
                selected[link] = {
                    "unit": unit,
                    "state": state,
                    "evidence": evidence,
                }
            if archive_record is None:
                archive_record = artifact["metadata"]["demand_archive"]
                context = annual_unit_context(
                    plan, unit_id, unit_hint=hint, plan_context=indexed
                )
                request = context["request"]
                required = DemandBuildSpec.from_dict(context["demand_build_spec"])
            elif artifact["metadata"]["demand_archive"] != archive_record:
                raise ValueError("one annual chain references multiple demand archives")

        assert archive_record is not None and request is not None and required is not None
        archive = Path(archive_record["archive"])
        current_archive = validate_demand_archive(archive, required)
        if current_archive != archive_record:
            raise ValueError("annual chain archive record differs from current validation")
        reference_edge = _reference_edge(ROOT / "sumo/net.net.xml")
        runner = build_annual_prefix_runner(
            plan, request=request, required=required, archive=archive,
            reference_edge=reference_edge,
        )
        comparisons = []
        try:
            for link in positions:
                chained = selected[link]
                unit = chained["unit"]
                started = time.monotonic()
                cold = runner.bootstrap_warm_state(
                    variant=variant,
                    seed=unit["seed"],
                    warm_point_s=unit["checkpoint_s"],
                    workspace=workspace / "cold" / f"{link:03d}",
                    schedule_id=f"annual-chain-cold-{link}",
                )
                runtime_s = time.monotonic() - started
                if cold is None:
                    raise RuntimeError(f"cold comparison declined at link {link}")
                cold_evidence = parse_prefix_evidence(
                    cold["prefix_evidence"],
                    expected_warm_point_s=unit["checkpoint_s"],
                )
                cold_state = _state_record(Path(cold["state_path"]))
                inflation = (
                    chained["state"]["expanded_bytes"]
                    / cold_state["expanded_bytes"]
                )
                comparisons.append({
                    "link": link,
                    "checkpoint_s": unit["checkpoint_s"],
                    "prefix_evidence_equal": chained["evidence"] == cold_evidence,
                    "behavioral_prefix_evidence_equal":
                        _behavioral_evidence_equal(
                            chained["evidence"], cold_evidence
                        ),
                    "prefix_evidence_comparison": _evidence_comparison(
                        chained["evidence"], cold_evidence
                    ),
                    "expanded_state_equal": (
                        chained["state"]["expanded_sha256"]
                        == cold_state["expanded_sha256"]
                    ),
                    "expanded_state_bytes_are_release_gate": False,
                    "state_inflation_vs_cold": round(inflation, 6),
                    "cold_runtime_s": round(runtime_s, 6),
                    "chained_state": chained["state"],
                    "cold_state": cold_state,
                })
        finally:
            cleanup = getattr(runner, "cleanup", None)
            if callable(cleanup):
                cleanup()

    byte_exact = all(item["prefix_evidence_equal"] for item in comparisons)
    behavioral_exact = all(
        item["behavioral_prefix_evidence_equal"] for item in comparisons
    )
    bounded = all(
        item["state_inflation_vs_cold"] <= STATE_INFLATION_LIMIT
        and item["chained_state"]["expanded_bytes"] <= STATE_EXPANDED_LIMIT_BYTES
        for item in comparisons
    )
    return {
        "schema": "annual_warm_chain_pilot_v2",
        "status": "pass" if behavioral_exact and bounded else "fail",
        "diagnostic_only": True,
        "plan_content_key": plan["content_key"],
        "demand_build_key": demand_build_key,
        "variant": variant,
        "chain_length": len(units),
        "audited_positions": list(positions),
        "all_links_restored_and_semantically_valid": len(profile) == len(units),
        "selected_prefix_evidence_byte_exact": byte_exact,
        "selected_behavioral_evidence_exact": behavioral_exact,
        "state_growth_bounded": bounded,
        "state_inflation_limit": STATE_INFLATION_LIMIT,
        "state_expanded_limit_bytes": STATE_EXPANDED_LIMIT_BYTES,
        "archive": archive_record,
        "chain_profile": profile,
        "cold_comparisons": comparisons,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan_path = args.plan if args.plan.is_absolute() else ROOT / args.plan
    root = args.root if args.root.is_absolute() else ROOT / args.root
    output = args.output if args.output.is_absolute() else ROOT / args.output
    result = run(
        plan_path=plan_path,
        root=root,
        demand_build_key=args.demand_build_key,
        variant=args.variant,
        positions=_positions(args.positions),
    )
    _atomic_json(output, result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
