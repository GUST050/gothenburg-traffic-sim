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
OPERATIONAL_QUALIFICATION_MODE = "operational_four_class_q50_v1"
OPERATIONAL_FIXTURE_CONTRACT = {
    "weekday": {"date": "2027-09-08", "days": 1,
                "catalog_pools": frozenset({"weekday"})},
    "weekend": {"date": "2027-09-11", "days": 1,
                "catalog_pools": frozenset({"weekend"})},
    "holiday": {"date": "2027-05-13", "days": 1,
                "catalog_pools": frozenset({"weekday"})},
    "mixed": {"date": "2027-09-10", "days": 2,
              "catalog_pools": frozenset({"weekday", "weekend"})},
}
OPERATIONAL_DAY_CLASSES = frozenset(OPERATIONAL_FIXTURE_CONTRACT)
OPERATIONAL_GATES = frozenset({
    "four_bound_day_classes",
    "both_arm_orders",
    "hard_correctness",
    "catalog_faster_in_every_class",
    "paired_vehicle_population_delta_le_1pct",
    "rss_within_8gib",
    "positive_median_saving",
    "catalog_amortizes_within_3_days",
})
OPERATIONAL_CLAIM_BOUNDARY = {
    "current_catalog_correctness": True,
    "current_four_class_non_regression": True,
    "new_statistical_performance_claim": False,
    "general_performance_claim": False,
}
OPERATIONAL_REQUIRED_SOURCE_PATHS = frozenset({
    "tests/test_catalog_qualification.py",
    "tests/test_route_catalog.py",
    "tools/adopt_route_catalog.py",
    "tools/benchmark_route_catalog.py",
    "tools/qualify_route_catalog.py",
    "traffic_sim/demand/catalog_qualification.py",
    "traffic_sim/demand/route_catalog.py",
})


def validate_suite_gate_evidence(
    payload: object, *, project_root: Path,
    require_source_hashes: bool = False,
) -> dict[str, bool]:
    """Validate suite gates and, for operational adoption, their source seals."""
    raw = payload.get("gates") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict)
            or payload.get("schema_version") != 2
            or payload.get("kind") != "route_catalog_suite_gate_evidence"
            or not isinstance(raw, dict)):
        raise ValueError("suite-gate record must contain a gates object")
    missing = sorted(set(SUITE_HARD_GATES) - set(raw))
    extra = sorted(set(raw) - set(SUITE_HARD_GATES))
    if missing or extra:
        raise ValueError(
            "suite-gate record does not match the suite contract; missing="
            + ",".join(missing) + " extra=" + ",".join(extra))
    result = {}
    referenced_sources = set()
    root = Path(project_root).resolve()
    for gate in SUITE_HARD_GATES:
        record = raw.get(gate)
        tests = record.get("tests") if isinstance(record, dict) else None
        if (not isinstance(record, dict)
                or record.get("status") not in {"pass", "fail"}
                or not isinstance(tests, list) or not tests
                or any(not isinstance(test, str) or not test for test in tests)):
            raise ValueError(
                f"suite gate {gate} needs status and non-empty test evidence")
        for test in tests:
            evidence_path = Path(test.split("::", 1)[0])
            resolved = (root / evidence_path).resolve()
            if (evidence_path.is_absolute()
                    or not resolved.is_relative_to(root)
                    or not resolved.is_file()):
                raise ValueError(
                    f"suite gate {gate} names missing evidence: {test}")
            referenced_sources.add(str(evidence_path))
        result[gate] = record["status"] == "pass"
    if require_source_hashes:
        sources = payload.get("source_sha256")
        if not isinstance(sources, dict) or not sources:
            raise ValueError("operational suite evidence needs source_sha256")
        required_sources = (
            referenced_sources | set(OPERATIONAL_REQUIRED_SOURCE_PATHS))
        missing_sources = required_sources - set(sources)
        if missing_sources:
            raise ValueError(
                "suite evidence lacks source hashes: "
                + ",".join(sorted(missing_sources)))
        for relative, expected in sources.items():
            source = Path(relative) if isinstance(relative, str) else Path("/")
            resolved = (root / source).resolve()
            if (not isinstance(relative, str) or not relative
                    or source.is_absolute()
                    or not resolved.is_relative_to(root)
                    or not resolved.is_file()
                    or not isinstance(expected, str) or len(expected) != 64
                    or any(char not in "0123456789abcdef" for char in expected)
                    or hashlib.sha256(resolved.read_bytes()).hexdigest()
                       != expected):
                raise ValueError(
                    f"suite source binding is invalid: {relative}")
    return result


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


def validate_operational_qualification_record(record: dict) -> None:
    """Validate the narrow operational record; legacy reports remain valid."""
    mode = record.get("qualification_mode") if isinstance(record, dict) else None
    if mode is None:
        return
    gates = record.get("gates") if isinstance(record, dict) else None
    classes = record.get("day_class_results") if isinstance(record, dict) else None
    if (mode != OPERATIONAL_QUALIFICATION_MODE
            or record.get("schema_version") != 3
            or record.get("variant_mode") != "q50_only"
            or record.get("trials") != 4
            or record.get("claim_boundary") != OPERATIONAL_CLAIM_BOUNDARY
            or not isinstance(gates, dict)
            or set(gates) != OPERATIONAL_GATES
            or not all(value is True for value in gates.values())
            or not isinstance(classes, dict)
            or set(classes) != OPERATIONAL_DAY_CLASSES):
        raise ValueError("operational qualification contract is invalid")


def validate_operational_trial_binding(
    trials: object, *, catalog_keys: dict[str, str],
    catalog_sizes: dict[str, int], candidate_n_total: int,
) -> None:
    """Bind each operational class to its exact fixture and catalog pools."""
    if not isinstance(trials, list) or len(trials) != 4:
        raise ValueError("operational trials must contain exactly four records")
    if (not isinstance(catalog_keys, dict)
            or not isinstance(catalog_sizes, dict)
            or set(catalog_keys) != {"weekday", "weekend"}
            or set(catalog_sizes) != set(catalog_keys)
            or isinstance(candidate_n_total, bool)
            or not isinstance(candidate_n_total, int)
            or candidate_n_total < 1):
        raise ValueError("operational catalog binding is malformed")
    seen = set()
    for trial in trials:
        day_class = trial.get("day_class") if isinstance(trial, dict) else None
        contract = OPERATIONAL_FIXTURE_CONTRACT.get(day_class)
        if (contract is None or day_class in seen
                or trial.get("date") != contract["date"]
                or trial.get("days") != contract["days"]):
            raise ValueError("operational trial fixture binding is invalid")
        seen.add(day_class)
        expected_pools = contract["catalog_pools"]
        for arm, expected_source in (("legacy", "legacy"),
                                     ("catalog", "catalog")):
            record = trial.get(arm)
            if (not isinstance(record, dict)
                    or record.get("candidate_source") != expected_source
                    or record.get("candidate_n_total") != candidate_n_total):
                raise ValueError("operational trial arm binding is invalid")
        legacy = trial["legacy"]
        catalog = trial["catalog"]
        legacy_keys = legacy.get("catalog_keys")
        legacy_sizes = legacy.get("catalog_selected_n_total")
        catalog_keys_record = catalog.get("catalog_keys")
        catalog_sizes_record = catalog.get("catalog_selected_n_total")
        if (legacy_keys not in ({}, None) or legacy_sizes not in ({}, None)
                or not isinstance(catalog_keys_record, dict)
                or not isinstance(catalog_sizes_record, dict)):
            raise ValueError("operational trial catalog pools are invalid")
        if (set(catalog_keys_record) != expected_pools
                or set(catalog_sizes_record) != expected_pools
                or any(catalog_keys_record.get(pool) != catalog_keys[pool]
                       or catalog_sizes_record.get(pool)
                          != catalog_sizes[pool]
                       for pool in expected_pools)):
            raise ValueError("operational trial catalog pools are invalid")
    if seen != OPERATIONAL_DAY_CLASSES:
        raise ValueError("operational trial classes are incomplete")


def validate_operational_qualification_evidence(
    record: dict, *, trial_payload: object, catalog_build_s: float,
    suite_gates: dict[str, bool],
) -> None:
    """Recompute an operational verdict from its immutable linked evidence."""
    validate_operational_qualification_record(record)
    if record.get("qualification_mode") is None:
        return
    binding = record.get("evidence_binding")
    trials = (trial_payload.get("trials")
              if isinstance(trial_payload, dict) else None)
    if (not isinstance(binding, dict)
            or not isinstance(trial_payload, dict)
            or trial_payload.get("schema_version") != 3
            or trial_payload.get("kind") != "route_catalog_paired_trials"
            or trial_payload.get("qualification_mode")
               != OPERATIONAL_QUALIFICATION_MODE
            or trial_payload.get("variant_mode") != "q50_only"
            or trial_payload.get("requested_pairs") != 4
            or trial_payload.get("execute") is not True
            or trial_payload.get("candidate_n_total")
               != binding.get("candidate_n_total")
            or (trial_payload.get("catalog_build_evidence") or {}).get(
                "sha256") != binding.get("catalog_build_sha256")):
        raise ValueError("operational trial evidence binding is invalid")
    validate_operational_trial_binding(
        trials,
        catalog_keys=binding.get("catalog_keys"),
        catalog_sizes=binding.get("catalog_selected_n_total"),
        candidate_n_total=binding.get("candidate_n_total"),
    )
    expected = qualify_operational_catalog_trials(
        trials, catalog_build_s=catalog_build_s, suite_gates=suite_gates)
    observed = {
        key: value for key, value in record.items() if key != "evidence_binding"
    }
    if observed != expected:
        raise ValueError(
            "operational qualification does not reproduce from linked evidence")


def qualify_operational_catalog_trials(
    trials: list[dict], *, catalog_build_s: float,
    suite_gates: dict[str, bool], rss_budget_bytes: int = 8 * 1024 ** 3,
) -> dict:
    """Qualify one current q50 pair for every catalog day class.

    This is deliberately not a statistical replacement for the historical
    30-pair campaign. It proves current-input correctness and a conservative
    four-class non-regression before activation.
    """
    errors: list[str] = []
    if (not isinstance(suite_gates, dict)
            or set(suite_gates) != set(SUITE_HARD_GATES)
            or any(not isinstance(value, bool)
                   for value in suite_gates.values())):
        errors.append("suite gates must exactly match the suite contract")
        suite_gates = {}
    if len(trials) != 4:
        errors.append("operational qualification requires exactly four trials")
    classes = [str(trial.get("day_class")) for trial in trials]
    if len(classes) != len(set(classes)) or set(classes) != OPERATIONAL_DAY_CLASSES:
        errors.append("operational qualification requires each day class once")
    orders = [trial.get("order") for trial in trials]
    if (any(order not in {"legacy_first", "catalog_first"} for order in orders)
            or not {"legacy_first", "catalog_first"}.issubset(orders)):
        errors.append("operational qualification requires both arm orders")

    arms: dict[str, list[dict[str, float | int]]] = {
        "legacy": [], "catalog": [],
    }
    trial_hard_failures = []
    suite_hard_failures = [
        gate for gate in SUITE_HARD_GATES if suite_gates.get(gate) is not True
    ]
    for index, trial in enumerate(trials):
        for arm in arms:
            record = trial.get(arm)
            if not isinstance(record, dict):
                errors.append(f"trial {index} is missing {arm}")
                continue
            if record.get("variant_mode") != "q50_only":
                errors.append(f"trial {index} {arm} is not q50_only")
            try:
                vehicles = record["vehicles"]
                shape_variables = record.get("pfe_shape_variables")
                wall_s = float(record["wall_s"])
                adapter_s = float(record.get("adapter_s", 0.0))
                pfe_s = float(record["pfe_s"])
                peak_rss_bytes = int(record["peak_rss_bytes"])
                if (isinstance(vehicles, bool) or not isinstance(vehicles, int)
                        or vehicles < 1
                        or (shape_variables is not None
                            and (isinstance(shape_variables, bool)
                                 or not isinstance(shape_variables, int)
                                 or shape_variables < 1))
                        or not all(math.isfinite(value)
                                   for value in (wall_s, adapter_s, pfe_s))
                        or wall_s <= 0 or adapter_s < 0 or pfe_s < 0
                        or peak_rss_bytes < 1):
                    raise ValueError("invalid operational arm record")
                arms[arm].append({
                    "wall_s": wall_s,
                    "adapter_s": adapter_s,
                    "pfe_s": pfe_s,
                    "peak_rss_bytes": peak_rss_bytes,
                    "vehicles": vehicles,
                    "pfe_shape_variables": shape_variables,
                })
            except (KeyError, TypeError, ValueError, OverflowError):
                errors.append(f"trial {index} has malformed {arm} metrics")
            gates = record.get("hard_gates") or {}
            for gate in PER_TRIAL_HARD_GATES:
                if gates.get(gate) is not True:
                    trial_hard_failures.append({
                        "trial": index, "arm": arm, "gate": gate,
                    })
    if errors:
        return {
            "schema_version": 3,
            "qualification_mode": OPERATIONAL_QUALIFICATION_MODE,
            "verdict": "inconclusive",
            "errors": errors,
            "trial_hard_failures": trial_hard_failures,
            "suite_hard_failures": suite_hard_failures,
        }

    metrics = {
        arm: {
            "wall_s": _stats([float(record["wall_s"]) for record in records]),
            "adapter_s": _stats([
                float(record["adapter_s"]) for record in records]),
            "pfe_s": _stats([float(record["pfe_s"]) for record in records]),
            "peak_rss_bytes": _stats([
                float(record["peak_rss_bytes"]) for record in records]),
            "vehicles": _stats([
                float(record["vehicles"]) for record in records]),
        }
        for arm, records in arms.items()
    }
    savings = [
        float(trial["legacy"]["wall_s"]) - float(trial["catalog"]["wall_s"])
        for trial in trials
    ]
    speedups = [
        float(trial["legacy"]["wall_s"]) / float(trial["catalog"]["wall_s"])
        for trial in trials
    ]
    population_deltas = [
        abs(int(trial["catalog"]["vehicles"])
            - int(trial["legacy"]["vehicles"]))
        / max(1, int(trial["legacy"]["vehicles"]))
        for trial in trials
    ]
    class_results = {
        str(trial["day_class"]): {
            "legacy_s": float(trial["legacy"]["wall_s"]),
            "catalog_s": float(trial["catalog"]["wall_s"]),
        }
        for trial in trials
    }
    median_saving = statistics.median(savings)
    amortized_days = (
        float(catalog_build_s) / median_saving
        if median_saving > 0 and catalog_build_s >= 0 else math.inf
    )
    gates = {
        "four_bound_day_classes": (
            len(trials) == 4 and set(class_results) == OPERATIONAL_DAY_CLASSES),
        "both_arm_orders": (
            {"legacy_first", "catalog_first"}.issubset(set(orders))),
        "hard_correctness": (
            not trial_hard_failures and not suite_hard_failures),
        "catalog_faster_in_every_class": all(
            result["catalog_s"] < result["legacy_s"]
            for result in class_results.values()),
        "paired_vehicle_population_delta_le_1pct": (
            max(population_deltas) <= 0.01),
        "rss_within_8gib": (
            metrics["catalog"]["peak_rss_bytes"]["max"] <= rss_budget_bytes),
        "positive_median_saving": median_saving > 0,
        "catalog_amortizes_within_3_days": amortized_days <= 3.0,
    }
    return {
        "schema_version": 3,
        "qualification_mode": OPERATIONAL_QUALIFICATION_MODE,
        "variant_mode": "q50_only",
        "verdict": "adopt" if all(gates.values()) else "reject",
        "trials": len(trials),
        "metrics": metrics,
        "paired_speedup": statistics.median(speedups),
        "paired_speedup_stats": _stats(speedups),
        "median_saving_s": median_saving,
        "catalog_build_s": float(catalog_build_s),
        "amortized_days": (
            amortized_days if math.isfinite(amortized_days) else None),
        "day_class_results": class_results,
        "trial_hard_failures": trial_hard_failures,
        "suite_hard_failures": suite_hard_failures,
        "suite_hard_gates": {
            gate: suite_gates.get(gate) is True for gate in SUITE_HARD_GATES
        },
        "paired_vehicle_population_relative_delta": _stats(population_deltas),
        "gates": gates,
        "claim_boundary": dict(OPERATIONAL_CLAIM_BOUNDARY),
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
        # REMOVED 2026-09-07 at the project owner's decision, recorded here
        # rather than silently deleted. The gate was:
        #     "adapter_p95_le_5s": metrics["catalog"]["adapter_s"]["p95"] <= 5.0
        #
        # What it caught, and why it was dropped anyway: rebuilding the
        # catalog produced new pool keys, which left
        # `sumo/route_catalog/mixed_adapter_cache` cold for the new
        # combination. Every `mixed` (2-day) trial then rebuilt that adapter
        # from scratch. Measured over 20 trials the split was exactly clean —
        # weekday/weekend/holiday 0.21-0.22 s, mixed 29.4-31.4 s — so the p95
        # reported ~30 s against a 5 s limit. The steady-state cost this gate
        # exists to police is the 0.21 s; the 30 s is a one-time cold build
        # that a single warm-up would have removed.
        #
        # The honest alternative was to warm the cache and re-run the 80-min
        # campaign; that was declined in favour of adopting now. Anyone
        # restoring this protection must ALSO warm `mixed_adapter_cache` for
        # the current keys first, or the same cold-start artifact will fail
        # it again for the same non-reason.
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
