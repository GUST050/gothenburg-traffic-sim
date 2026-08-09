#!/usr/bin/env python3
"""Find exact integer-margin conflicts in immutable LOSO picker traces."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analyze_loso_picker_diagnostics import (
    iter_json_array,
    read_leading_json_value,
    resolve_candidate_pool,
    sha256_file,
    verify_archived_artifacts,
)
from traffic_sim.confidence.controlled_rounding import (
    controlled_round_preserving_measured,
    integer_margin_feasibility,
    legacy_rounding_trace,
    minimal_inconsistent_integer_margins,
)
from traffic_sim.demand import pfe


DEFAULT_RUN = ROOT / "runs/loso-picker-diagnostic-v1-20260808"
DEFAULT_OUTPUT = ROOT / "validation/loso_integer_margin_conflicts_v1.json"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8")
    temporary.replace(path)


def analyze_seed(seed_root: Path, summary: dict, station: str,
                 shapes: list) -> dict:
    path = seed_root / "picker" / f"picker_{station}.json"
    held_edges = list(read_leading_json_value(path, "held_component_edges"))
    exact_infeasible = []
    exact_feasible_count = 0
    band_feasible_count = 0
    conflict_counts: Counter = Counter()
    legacy_residual_quarters = 0
    for quarter in iter_json_array(path, "quarters"):
        if "solution" in quarter and quarter["solution"] is None:
            continue
        vector = np.zeros(len(shapes), dtype=float)
        for route in quarter["selected_routes"]:
            vector[int(route["route_index"])] = float(route["selected_flow"])
        targets = {
            str(row["edge"]): float(row["target"])
            for row in quarter.get("targets", [])
        }
        _legacy_counts, legacy = legacy_rounding_trace(
            vector, shapes, targets, held_edges)
        legacy_nonzero = {
            edge: int(value)
            for edge, value in legacy["measurement_residuals"].items()
            if int(value) != 0
        }
        if legacy_nonzero:
            legacy_residual_quarters += 1
        exact = integer_margin_feasibility(vector, shapes, targets)
        band = integer_margin_feasibility(
            vector, shapes, targets, measurement_tol_mult=1.0)
        exact_feasible_count += int(exact["feasible"])
        band_feasible_count += int(band["feasible"])
        if exact["feasible"]:
            continue
        conflict = minimal_inconsistent_integer_margins(
            vector, shapes, targets)
        conflict_counts[" + ".join(conflict)] += 1
        _counts, optimum = controlled_round_preserving_measured(
            vector, shapes, targets)
        exact_infeasible.append({
            "quarter": int(quarter["quarter"]),
            "relaxation_rung": str(quarter["relaxation_rung"]),
            "continuous_interval_total": round(float(vector.sum()), 6),
            "integer_interval_total": int(round(float(vector.sum()))),
            "targets": {key: round(value, 6)
                        for key, value in sorted(targets.items())},
            "deletion_minimal_conflict": conflict,
            "legacy_measurement_residuals_nonzero": legacy_nonzero,
            "minimum_max_abs_measurement_residual": optimum.get(
                "minimum_max_abs_measurement_residual"),
            "minimum_sum_abs_measurement_residual": optimum.get(
                "minimum_sum_abs_measurement_residual"),
            "minimum_residual_solution": optimum["measurement_residuals"],
            "declared_1x_band_feasible": bool(band["feasible"]),
        })
    return {
        "candidate_pool_sha256": summary["candidate_pool_sha256"],
        "demand_build_id": summary["demand_build_id"],
        "station": station,
        "quarters_analyzed": exact_feasible_count + len(exact_infeasible),
        "exact_integer_margin_feasible_quarters": exact_feasible_count,
        "exact_integer_margin_infeasible_quarters": len(exact_infeasible),
        "declared_1x_band_feasible_quarters": band_feasible_count,
        "legacy_nonzero_residual_quarters": legacy_residual_quarters,
        "deletion_minimal_conflict_counts": dict(sorted(conflict_counts.items())),
        "infeasible_quarters": exact_infeasible,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--station", default="107")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run_root = args.run_root if args.run_root.is_absolute() else ROOT / args.run_root
    manifest_path = run_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "complete":
        raise SystemExit("picker diagnostic manifest is not complete")
    station = str(args.station)
    if station not in manifest["stations"]:
        raise SystemExit(f"station {station} absent from diagnostic manifest")

    per_seed = {}
    for run in manifest["runs"]:
        seed = str(run["seed"])
        seed_root = run_root / f"seed-{seed}"
        summary = json.loads((seed_root / "summary.json").read_text())
        verify_archived_artifacts(seed_root, summary)
        candidate_path, provenance = resolve_candidate_pool(
            summary["candidate_pool_sha256"])
        shapes, _route_cost = pfe.prepare_calibration(candidate_path)
        result = analyze_seed(seed_root, summary, station, shapes)
        result["candidate_pool_provenance"] = provenance
        per_seed[seed] = result

    report = {
        "schema_version": 1,
        "kind": "paired_loso_integer_margin_conflict_analysis",
        "evidence_class": "diagnostic_replay",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "station": station,
        "seeds": [int(seed) for seed in manifest["seeds"]],
        "source": {
            "manifest": str(manifest_path.relative_to(ROOT)),
            "manifest_sha256": sha256_file(manifest_path),
            "analysis_tool_sha256": sha256_file(Path(__file__)),
            "controlled_rounding_sha256": sha256_file(
                ROOT / "traffic_sim/confidence/controlled_rounding.py"),
        },
        "semantics": {
            "exact": "each active target and interval total rounded separately",
            "declared_1x_band": (
                "integer bounds derived from the production Level-1 PFE "
                "measurement tolerance; not fitted to held counts"),
            "conflict": (
                "deletion-minimal under one-at-a-time constraint removal; "
                "not guaranteed minimum-cardinality"),
        },
        "per_seed": per_seed,
    }
    output = args.output if args.output.is_absolute() else ROOT / args.output
    write_json(output, report)
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
