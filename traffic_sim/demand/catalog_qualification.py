"""Pure qualification math for adopting the canonical route catalog."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Iterable
import xml.etree.ElementTree as ET


PER_TRIAL_HARD_GATES = (
    "exact_sensor_targets",
    "zero_integer_residual",
    "population_contract",
    "sensor_anchor_contract",
    "candidate_structure",
    "route_agent_provenance",
    "confidence_health",
)

# These are invariant/negative-path contracts.  They are established once by
# focused suites and bound into the qualification evidence; repeating the same
# boolean in every benchmark arm would not turn one suite result into 60
# independent observations.
SUITE_HARD_GATES = (
    "purpose_route_compatibility",
    "deterministic_repeat",
    "malformed_catalog_rejected",
    "singleflight_recovery",
    "day_library_restore",
    "warm_state_identity",
    "sumo_runtime_no_regression",
)
REQUIRED_HARD_GATES = PER_TRIAL_HARD_GATES + SUITE_HARD_GATES


def semantic_route_digest(route_path: Path, metadata_path: Path) -> str:
    """Digest route semantics while ignoring IDs, XML layout and departure."""
    metadata = json.loads(Path(metadata_path).read_text())
    candidates = metadata.get("candidates") if isinstance(metadata, dict) else None
    if not isinstance(candidates, dict):
        raise ValueError("candidate metadata must contain an object")
    records = []
    for vehicle in ET.parse(route_path).getroot().findall("vehicle"):
        vehicle_id = vehicle.get("id")
        route = vehicle.find("route")
        if not vehicle_id or route is None or vehicle_id not in candidates:
            raise ValueError("route and candidate metadata are inconsistent")
        meta = candidates[vehicle_id]
        if not isinstance(meta, dict):
            raise ValueError("candidate metadata record must be an object")
        records.append({
            "edges": (route.get("edges") or "").split(),
            "purpose": meta.get("purpose"),
            "origin_edge": meta.get("origin_edge"),
            "destination_edge": meta.get("destination_edge"),
            "via_edge": meta.get("via_edge"),
            "leg": meta.get("leg"),
        })
    if not records:
        raise ValueError("route catalog is empty")
    records.sort(key=lambda record: json.dumps(
        record, sort_keys=True, separators=(",", ":")))
    return hashlib.sha256(json.dumps(
        records, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def nearest_rank_p95(values: Iterable[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered or any(not math.isfinite(value) for value in ordered):
        raise ValueError("p95 requires finite values")
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _stats(values: list[float]) -> dict:
    return {
        "median": statistics.median(values),
        "p95": nearest_rank_p95(values),
        "max": max(values),
        "min": min(values),
    }


def qualify_catalog_trials(trials: list[dict], *, catalog_build_s: float,
                           suite_gates: dict[str, bool],
                           rss_budget_bytes: int = 8 * 1024 ** 3) -> dict:
    """Return an adopt/reject/inconclusive verdict from frozen paired trials."""
    errors = []
    if (not isinstance(suite_gates, dict)
            or set(suite_gates) != set(SUITE_HARD_GATES)
            or any(not isinstance(value, bool)
                   for value in suite_gates.values())):
        errors.append("suite gates must exactly match the suite contract")
        suite_gates = {}
    if len(trials) < 30:
        errors.append("at least 30 paired trials are required")
    orders = {trial.get("order") for trial in trials}
    if not {"legacy_first", "catalog_first"}.issubset(orders):
        errors.append("both counterbalanced arm orders are required")
    classes = {str(trial.get("day_class")) for trial in trials}
    if not {"weekday", "weekend", "holiday", "mixed"}.issubset(classes):
        errors.append("weekday, weekend, holiday and mixed fixtures are required")

    arms = {"legacy": [], "catalog": []}
    trial_hard_failures = []
    suite_hard_failures = [
        gate for gate in SUITE_HARD_GATES
        if suite_gates.get(gate) is not True
    ]
    for index, trial in enumerate(trials):
        for arm in arms:
            record = trial.get(arm)
            if not isinstance(record, dict):
                errors.append(f"trial {index} is missing {arm}")
                continue
            try:
                vehicles = record["vehicles"]
                shape_variables = record.get("pfe_shape_variables")
                if (isinstance(vehicles, bool) or not isinstance(vehicles, int)
                        or vehicles < 1
                        or (shape_variables is not None
                            and (isinstance(shape_variables, bool)
                                 or not isinstance(shape_variables, int)
                                 or shape_variables < 1))):
                    raise ValueError("invalid workload size")
                wall_s = float(record["wall_s"])
                adapter_s = float(record.get("adapter_s", 0.0))
                pfe_s = float(record["pfe_s"])
                peak_rss_bytes = int(record["peak_rss_bytes"])
                if (not all(math.isfinite(value)
                            for value in (wall_s, adapter_s, pfe_s))
                        or wall_s <= 0 or adapter_s < 0 or pfe_s < 0
                        or peak_rss_bytes < 1):
                    raise ValueError("invalid timing/resource value")
                arms[arm].append({
                    "wall_s": wall_s,
                    "adapter_s": adapter_s,
                    "pfe_s": pfe_s,
                    "peak_rss_bytes": peak_rss_bytes,
                    "vehicles": vehicles,
                    "pfe_shape_variables": (
                        shape_variables
                        if shape_variables is not None
                        else None),
                })
            except (KeyError, TypeError, ValueError):
                errors.append(f"trial {index} has malformed {arm} timings")
            gates = record.get("hard_gates") or {}
            for gate in PER_TRIAL_HARD_GATES:
                if gates.get(gate) is not True:
                    trial_hard_failures.append({
                        "trial": index, "arm": arm, "gate": gate,
                    })
    if errors:
        return {"schema_version": 2, "verdict": "inconclusive",
                "errors": errors,
                "trial_hard_failures": trial_hard_failures,
                "suite_hard_failures": suite_hard_failures}

    metrics = {
        arm: {
            "wall_s": _stats([record["wall_s"] for record in records]),
            "adapter_s": _stats([record["adapter_s"] for record in records]),
            "pfe_s": _stats([record["pfe_s"] for record in records]),
            "peak_rss_bytes": _stats([
                float(record["peak_rss_bytes"]) for record in records]),
            "vehicles": _stats([
                float(record["vehicles"]) for record in records]),
        }
        for arm, records in arms.items()
    }
    shape_metrics = {}
    for arm, records in arms.items():
        values = [float(record["pfe_shape_variables"]) for record in records
                  if record["pfe_shape_variables"] is not None]
        shape_metrics[arm] = _stats(values) if len(values) == len(records) else None
    metrics["pfe_shape_variables"] = shape_metrics
    legacy_median = metrics["legacy"]["wall_s"]["median"]
    catalog_median = metrics["catalog"]["wall_s"]["median"]
    paired_savings = [
        float(trial["legacy"]["wall_s"])
        - float(trial["catalog"]["wall_s"])
        for trial in trials
    ]
    paired_speedups = [
        float(trial["legacy"]["wall_s"])
        / float(trial["catalog"]["wall_s"])
        for trial in trials
    ]
    saving_s = statistics.median(paired_savings)
    speedup = statistics.median(paired_speedups)
    class_medians = {}
    class_regressions = []
    for day_class in sorted(classes):
        class_trials = [trial for trial in trials
                        if str(trial.get("day_class")) == day_class]
        legacy = statistics.median(float(t["legacy"]["wall_s"])
                                   for t in class_trials)
        catalog = statistics.median(float(t["catalog"]["wall_s"])
                                    for t in class_trials)
        class_medians[day_class] = {"legacy_s": legacy, "catalog_s": catalog}
        if catalog > legacy:
            class_regressions.append(day_class)
    amortized_days = (catalog_build_s / saving_s
                      if saving_s > 0 and catalog_build_s >= 0 else math.inf)
    population_pair_deltas = []
    for trial in trials:
        legacy_vehicles = int(trial["legacy"]["vehicles"])
        catalog_vehicles = int(trial["catalog"]["vehicles"])
        population_pair_deltas.append(
            abs(catalog_vehicles - legacy_vehicles) / max(1, legacy_vehicles))
    gates = {
        "trial_count": len(trials) >= 30,
        "hard_correctness": (
            not trial_hard_failures and not suite_hard_failures),
        "adapter_p95_le_5s": metrics["catalog"]["adapter_s"]["p95"] <= 5.0,
        "cold_median_improves_25pct": catalog_median <= legacy_median * 0.75,
        "no_day_class_slower": not class_regressions,
        "paired_vehicle_population_delta_le_1pct": (
            max(population_pair_deltas) <= 0.01),
        "rss_within_8gib": (
            metrics["catalog"]["peak_rss_bytes"]["max"] <= rss_budget_bytes),
        "catalog_amortizes_within_3_days": amortized_days <= 3.0,
    }
    verdict = "adopt" if all(gates.values()) else "reject"
    return {
        "schema_version": 2,
        "verdict": verdict,
        "trials": len(trials),
        "metrics": metrics,
        "paired_speedup": speedup,
        "paired_speedup_stats": _stats(paired_speedups),
        "median_saving_s": saving_s,
        "catalog_build_s": float(catalog_build_s),
        "amortized_days": amortized_days if math.isfinite(amortized_days) else None,
        "day_class_medians": class_medians,
        "slower_day_classes": class_regressions,
        "trial_hard_failures": trial_hard_failures,
        "suite_hard_failures": suite_hard_failures,
        "suite_hard_gates": {
            gate: suite_gates.get(gate) is True for gate in SUITE_HARD_GATES
        },
        "paired_vehicle_population_relative_delta": _stats(
            population_pair_deltas),
        "pfe_timing_interpretation": (
            "PFE time is reported as end-to-end product performance, not as "
            "an isolated solver speedup. Catalog and legacy arms may expose "
            "different counts of distinct route-by-purpose variables; the "
            "counts are reported when the producer provides them."),
        "gates": gates,
    }
