"""Validation-only cold/prefix/resumed tripinfo forensics.

This module is pure: it imports no simulator client, opens no socket, starts no
process and writes nothing.  A caller supplies paths while it still owns them;
the observer parses the records immediately so a private workspace can then be
deleted normally.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping


RAW_SCHEMA = "monthly_warm_state_raw_forensics_v1"
REPORT_SCHEMA = "monthly_warm_state_forensic_report_v1"
GLOBAL_SCHEMA = "monthly_warm_state_forensic_verdict_v1"

PHASES = ("cold_candidate", "completed_prefix", "resumed")
TRIP_FIELDS = (
    "timeLoss", "depart", "arrival", "duration", "routeLength",
    "waitingTime", "waitingCount",
)

POPULATION_PARTITION_ERROR = "population_partition_error"
TIME_LOSS_ONLY_DRIFT = "time_loss_only_drift"
KINEMATIC_OUTPUT_DRIFT = "kinematic_output_drift"
AGGREGATE_REPORTING_INCONSISTENCY = "aggregate_reporting_inconsistency"
EXACT_AGREEMENT = "exact_agreement"
CLASSIFICATIONS = (
    POPULATION_PARTITION_ERROR,
    TIME_LOSS_ONLY_DRIFT,
    KINEMATIC_OUTPUT_DRIFT,
    AGGREGATE_REPORTING_INCONSISTENCY,
    EXACT_AGREEMENT,
)

EXEMPLAR_LIMIT = 12
REPORTING_PRECISION = 2


class ForensicObservationError(ValueError):
    """Raw diagnostic evidence is absent, malformed or contradictory."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ForensicObservationError(f"{label} must be numeric, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ForensicObservationError(
            f"{label} is not numeric: {value!r}") from error
    if not math.isfinite(number):
        raise ForensicObservationError(f"{label} is not finite: {value!r}")
    return number


def parse_tripinfo(path: Path | str) -> dict[str, dict[str, float]]:
    """Parse exact ``tripinfo`` elements and reject ambiguous evidence."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ForensicObservationError(
            f"tripinfo must be a regular file: {path}")
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        raise ForensicObservationError(
            f"cannot parse tripinfo {path.name}: {error}") from error

    records: dict[str, dict[str, float]] = {}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) \
            else element.tag
        if tag != "tripinfo":
            continue
        vehicle = element.attrib.get("id")
        if not isinstance(vehicle, str) or not vehicle:
            raise ForensicObservationError(
                f"tripinfo element in {path.name} has no non-empty id")
        if vehicle in records:
            raise ForensicObservationError(
                f"duplicate tripinfo id {vehicle!r} in {path.name}")
        missing = [field for field in TRIP_FIELDS
                   if field not in element.attrib]
        if missing:
            raise ForensicObservationError(
                f"tripinfo {vehicle!r} in {path.name} lacks {missing}")
        records[vehicle] = {
            field: _finite_number(
                element.attrib[field],
                f"tripinfo {vehicle!r} field {field!r}")
            for field in TRIP_FIELDS
        }
    if not records:
        raise ForensicObservationError(
            f"tripinfo {path.name} contains no tripinfo elements")
    return records


def validate_records(records: Any, label: str) -> dict[str, dict[str, float]]:
    if not isinstance(records, Mapping) or not records:
        raise ForensicObservationError(f"{label} records must be non-empty")
    validated: dict[str, dict[str, float]] = {}
    for vehicle, record in records.items():
        if not isinstance(vehicle, str) or not vehicle:
            raise ForensicObservationError(f"{label} has an empty vehicle id")
        if not isinstance(record, Mapping) or set(record) != set(TRIP_FIELDS):
            raise ForensicObservationError(
                f"{label} record {vehicle!r} must contain exactly "
                f"{list(TRIP_FIELDS)}")
        validated[vehicle] = {
            field: _finite_number(record[field],
                                  f"{label}/{vehicle}/{field}")
            for field in TRIP_FIELDS
        }
    return validated


def _identity(schedule_id: Any, demand_variant: Any, seed: Any) -> dict[str, Any]:
    if not isinstance(schedule_id, str) or not schedule_id:
        raise ForensicObservationError("forensic identity needs a schedule id")
    if not isinstance(demand_variant, str) or not demand_variant:
        raise ForensicObservationError("forensic identity needs a variant")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ForensicObservationError("forensic identity needs an integer seed")
    return {"schedule_id": schedule_id, "demand_variant": demand_variant,
            "seed": seed}


def identity_label(identity: Mapping[str, Any]) -> str:
    checked = _identity(identity.get("schedule_id"),
                        identity.get("demand_variant"), identity.get("seed"))
    return (f"{checked['schedule_id']}__{checked['demand_variant']}__"
            f"{checked['seed']}")


def _quantile(sorted_values: list[float], numerator: int,
              denominator: int = 10) -> float | None:
    """Nearest-rank quantile with a frozen integer-only index rule."""
    if not sorted_values:
        return None
    index = max(0, math.ceil(numerator * len(sorted_values) / denominator) - 1)
    return float(sorted_values[index])


def _distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values)
    nonzero = [value for value in ordered if value != 0.0]
    return {
        "count": len(ordered),
        "nonzero_count": len(nonzero),
        "negative_count": sum(value < 0.0 for value in ordered),
        "zero_count": sum(value == 0.0 for value in ordered),
        "positive_count": sum(value > 0.0 for value in ordered),
        "sum_s": math.fsum(ordered),
        "min_s": None if not ordered else ordered[0],
        "q10_s": _quantile(ordered, 1),
        "q50_s": _quantile(ordered, 5),
        "q90_s": _quantile(ordered, 9),
        "max_s": None if not ordered else ordered[-1],
        "method": "nearest_rank_index=ceil(p*n)-1",
    }


def _raw_totals(cold: Mapping[str, Mapping[str, float]],
                prefix: Mapping[str, Mapping[str, float]],
                resumed: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    cold_total = math.fsum(record["timeLoss"] for record in cold.values())
    prefix_total = math.fsum(record["timeLoss"] for record in prefix.values())
    resumed_total = math.fsum(record["timeLoss"] for record in resumed.values())
    return {
        "cold_total_s": cold_total,
        "prefix_total_s": prefix_total,
        "resumed_total_s": resumed_total,
        "split_total_s": math.fsum((prefix_total, resumed_total)),
        "cold_trip_count": len(cold),
        "prefix_trip_count": len(prefix),
        "resumed_trip_count": len(resumed),
        "split_trip_count": len(prefix) + len(resumed),
    }


RECORDED_TOTAL_FIELDS = frozenset({
    "cold_total_s", "prefix_total_s", "resumed_total_s", "warm_total_s",
    "cold_trip_count", "prefix_trip_count", "resumed_trip_count",
    "warm_trip_count",
})


def _validate_recorded_totals(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != RECORDED_TOTAL_FIELDS:
        raise ForensicObservationError(
            "recorded totals must contain exactly "
            f"{sorted(RECORDED_TOTAL_FIELDS)}")
    out = {
        name: _finite_number(value[name], f"recorded total {name}")
        for name in ("cold_total_s", "prefix_total_s", "resumed_total_s",
                     "warm_total_s")
    }
    for name in ("cold_trip_count", "prefix_trip_count",
                 "resumed_trip_count", "warm_trip_count"):
        item = value[name]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ForensicObservationError(
                f"recorded total {name} must be a non-negative integer")
        out[name] = item
    return out


def _aggregate_mismatches(raw: Mapping[str, Any],
                          recorded: Mapping[str, Any]) -> list[str]:
    mismatches: list[str] = []
    for raw_name, recorded_name in (
            ("cold_total_s", "cold_total_s"),
            ("prefix_total_s", "prefix_total_s"),
            ("resumed_total_s", "resumed_total_s"),
            ("split_total_s", "warm_total_s")):
        if round(float(raw[raw_name]), REPORTING_PRECISION) != \
                float(recorded[recorded_name]):
            mismatches.append(
                f"{recorded_name}: raw {raw[raw_name]} rounds to "
                f"{round(float(raw[raw_name]), REPORTING_PRECISION)}, "
                f"recorded {recorded[recorded_name]}")
    for raw_name, recorded_name in (
            ("cold_trip_count", "cold_trip_count"),
            ("prefix_trip_count", "prefix_trip_count"),
            ("resumed_trip_count", "resumed_trip_count"),
            ("split_trip_count", "warm_trip_count")):
        if int(raw[raw_name]) != int(recorded[recorded_name]):
            mismatches.append(
                f"{recorded_name}: raw {raw[raw_name]} != "
                f"recorded {recorded[recorded_name]}")
    return mismatches


def build_raw_payload(*, identity: Mapping[str, Any], cold: Any, prefix: Any,
                      resumed: Any, recorded_totals: Any) -> dict[str, Any]:
    checked_identity = _identity(identity.get("schedule_id"),
                                 identity.get("demand_variant"),
                                 identity.get("seed"))
    cold_records = validate_records(cold, "cold")
    prefix_records = validate_records(prefix, "prefix")
    resumed_records = validate_records(resumed, "resumed")
    overlap = sorted(set(prefix_records) & set(resumed_records))
    if overlap:
        raise ForensicObservationError(
            f"vehicles occur in both prefix and resumed records: {overlap[:12]}")
    payload = {
        "schema": RAW_SCHEMA,
        "identity": checked_identity,
        "records": {"cold_candidate": cold_records,
                    "completed_prefix": prefix_records,
                    "resumed": resumed_records},
        "recorded_totals": _validate_recorded_totals(recorded_totals),
    }
    payload["content_digest"] = canonical_digest(payload)
    return payload


def validate_raw_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ForensicObservationError("raw forensic payload must be an object")
    expected = {"schema", "identity", "records", "recorded_totals",
                "content_digest"}
    if set(payload) != expected or payload.get("schema") != RAW_SCHEMA:
        raise ForensicObservationError("raw forensic payload schema is invalid")
    digest_body = {key: value for key, value in payload.items()
                   if key != "content_digest"}
    if canonical_digest(digest_body) != payload["content_digest"]:
        raise ForensicObservationError("raw forensic payload digest does not match")
    records = payload["records"]
    if not isinstance(records, Mapping) or set(records) != set(PHASES):
        raise ForensicObservationError("raw forensic phases are incomplete")
    return build_raw_payload(
        identity=payload["identity"], cold=records["cold_candidate"],
        prefix=records["completed_prefix"], resumed=records["resumed"],
        recorded_totals=payload["recorded_totals"])


def build_forensic_report(raw_payload: Any) -> dict[str, Any]:
    raw = validate_raw_payload(raw_payload)
    cold = raw["records"]["cold_candidate"]
    prefix = raw["records"]["completed_prefix"]
    resumed = raw["records"]["resumed"]
    split = {**prefix, **resumed}

    cold_ids, prefix_ids, resumed_ids = set(cold), set(prefix), set(resumed)
    split_ids = prefix_ids | resumed_ids
    missing = sorted(cold_ids - split_ids)
    extra = sorted(split_ids - cold_ids)
    overlap = sorted(prefix_ids & resumed_ids)

    field_counts = {field: 0 for field in TRIP_FIELDS}
    deltas: list[float] = []
    differences: list[dict[str, Any]] = []
    for vehicle in sorted(cold_ids & split_ids):
        changed = []
        for field in TRIP_FIELDS:
            if cold[vehicle][field] != split[vehicle][field]:
                field_counts[field] += 1
                changed.append(field)
        delta = split[vehicle]["timeLoss"] - cold[vehicle]["timeLoss"]
        deltas.append(delta)
        if changed:
            differences.append({
                "id": vehicle,
                "phase": "completed_prefix" if vehicle in prefix else "resumed",
                "changed_fields": changed,
                "cold_time_loss_s": cold[vehicle]["timeLoss"],
                "split_time_loss_s": split[vehicle]["timeLoss"],
                "delta_s": delta,
            })

    raw_totals = _raw_totals(cold, prefix, resumed)
    aggregate_mismatches = _aggregate_mismatches(
        raw_totals, raw["recorded_totals"])
    if missing or extra or overlap:
        classification = POPULATION_PARTITION_ERROR
    elif aggregate_mismatches:
        classification = AGGREGATE_REPORTING_INCONSISTENCY
    else:
        changed_fields = {field for field, count in field_counts.items() if count}
        if not changed_fields:
            classification = EXACT_AGREEMENT
        elif changed_fields == {"timeLoss"}:
            classification = TIME_LOSS_ONLY_DRIFT
        else:
            classification = KINEMATIC_OUTPUT_DRIFT

    exemplars = sorted(
        differences, key=lambda item: (-abs(item["delta_s"]), item["id"])
    )[:EXEMPLAR_LIMIT]
    report = {
        "schema": REPORT_SCHEMA,
        "identity": dict(raw["identity"]),
        "raw_content_digest": raw["content_digest"],
        "classification": classification,
        "population": {
            "cold_count": len(cold_ids), "prefix_count": len(prefix_ids),
            "resumed_count": len(resumed_ids), "split_count": len(split_ids),
            "missing_from_split": missing, "extra_in_split": extra,
            "overlap": overlap,
        },
        "raw_totals": raw_totals,
        "recorded_totals": dict(raw["recorded_totals"]),
        "aggregate_mismatches": aggregate_mismatches,
        "field_mismatch_counts": field_counts,
        "differing_vehicle_count": len(differences),
        "time_loss_delta_distribution": _distribution(deltas),
        "bounded_exemplars": exemplars,
        "record_digests": {
            phase: canonical_digest(raw["records"][phase])
            for phase in PHASES
        },
        "reporting_precision": REPORTING_PRECISION,
    }
    report["content_digest"] = canonical_digest(report)
    return report


def validate_forensic_report(report: Any, raw_payload: Any) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        raise ForensicObservationError("forensic report must be an object")
    rebuilt = build_forensic_report(raw_payload)
    if json.loads(json.dumps(report)) != json.loads(json.dumps(rebuilt)):
        raise ForensicObservationError(
            "forensic report does not recompute from its raw records")
    if rebuilt["classification"] not in CLASSIFICATIONS:
        raise ForensicObservationError("forensic classification is unknown")
    return rebuilt


def build_global_verdict(reports: list[Mapping[str, Any]],
                         expected_identities: list[Mapping[str, Any]]) -> dict[str, Any]:
    expected = sorted(identity_label(item) for item in expected_identities)
    observed = sorted(identity_label(item["identity"]) for item in reports)
    if observed != expected or len(observed) != len(set(observed)):
        raise ForensicObservationError(
            f"forensic identity coverage differs: {observed} != {expected}")
    counts = {classification: 0 for classification in CLASSIFICATIONS}
    for report in reports:
        classification = report.get("classification")
        if classification not in counts:
            raise ForensicObservationError(
                f"unknown report classification {classification!r}")
        counts[classification] += 1
    non_exact = [item for item in reports
                 if item["classification"] != EXACT_AGREEMENT]
    verdict = {
        "schema": GLOBAL_SCHEMA,
        "identity_count": len(reports),
        "classification_counts": counts,
        "all_exact": not non_exact,
        "first_non_exact_identity": (None if not non_exact else
                                     dict(non_exact[0]["identity"])),
        "report_digests": {
            identity_label(item["identity"]): item["content_digest"]
            for item in reports
        },
        "claim_scope": (
            "diagnostic localization only; not equivalence, performance, "
            "warming readiness, cache adoption or release evidence"),
    }
    verdict["content_digest"] = canonical_digest(verdict)
    return verdict


class ForensicObserver:
    """Thread-safe in-memory observer invoked before owned files disappear."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str, int], dict[str, Any]] = {}

    @staticmethod
    def _key(schedule_id: str, demand_variant: str, seed: int):
        checked = _identity(schedule_id, demand_variant, seed)
        return (checked["schedule_id"], checked["demand_variant"],
                checked["seed"])

    def capture(self, *, schedule_id: str, demand_variant: str, seed: int,
                phase: str, tripinfo_path: Path | str,
                reported_total_s: Any, reported_trip_count: Any) -> None:
        if phase not in PHASES:
            raise ForensicObservationError(f"unknown forensic phase {phase!r}")
        records = parse_tripinfo(tripinfo_path)
        total = _finite_number(reported_total_s, f"{phase} reported total")
        if isinstance(reported_trip_count, bool) or \
                not isinstance(reported_trip_count, int) or reported_trip_count < 0:
            raise ForensicObservationError(
                f"{phase} reported trip count must be a non-negative integer")
        key = self._key(schedule_id, demand_variant, seed)
        with self._lock:
            entry = self._entries.setdefault(key, {
                "identity": _identity(*key), "records": {}, "totals": {}})
            if phase in entry["records"]:
                raise ForensicObservationError(
                    f"duplicate forensic phase {phase} for {key}")
            entry["records"][phase] = records
            entry["totals"][phase] = {
                "total_time_loss_s": total, "trip_count": reported_trip_count}

    def capture_warm_summary(self, *, schedule_id: str, demand_variant: str,
                             seed: int, reported_total_s: Any,
                             reported_trip_count: Any) -> None:
        key = self._key(schedule_id, demand_variant, seed)
        total = _finite_number(reported_total_s, "warm reported total")
        if isinstance(reported_trip_count, bool) or \
                not isinstance(reported_trip_count, int) or reported_trip_count < 0:
            raise ForensicObservationError(
                "warm reported trip count must be a non-negative integer")
        with self._lock:
            entry = self._entries.setdefault(key, {
                "identity": _identity(*key), "records": {}, "totals": {}})
            if "warm" in entry["totals"]:
                raise ForensicObservationError(
                    f"duplicate warm forensic summary for {key}")
            entry["totals"]["warm"] = {
                "total_time_loss_s": total, "trip_count": reported_trip_count}

    def raw_payloads(self, expected_identities: list[Mapping[str, Any]]) \
            -> list[dict[str, Any]]:
        expected = [self._key(item.get("schedule_id"),
                              item.get("demand_variant"), item.get("seed"))
                    for item in expected_identities]
        with self._lock:
            if sorted(self._entries) != sorted(expected):
                raise ForensicObservationError(
                    f"observer identity coverage {sorted(self._entries)} != "
                    f"{sorted(expected)}")
            snapshot = {
                key: {"identity": dict(value["identity"]),
                      "records": {phase: dict(records)
                                  for phase, records in value["records"].items()},
                      "totals": {phase: dict(total)
                                 for phase, total in value["totals"].items()}}
                for key, value in self._entries.items()
            }
        payloads = []
        for key in expected:
            entry = snapshot[key]
            if set(entry["records"]) != set(PHASES):
                raise ForensicObservationError(
                    f"forensic phases for {key} are "
                    f"{sorted(entry['records'])}, expected {list(PHASES)}")
            if set(entry["totals"]) != set(PHASES) | {"warm"}:
                raise ForensicObservationError(
                    f"forensic totals for {key} are incomplete")
            totals = entry["totals"]
            payloads.append(build_raw_payload(
                identity=entry["identity"],
                cold=entry["records"]["cold_candidate"],
                prefix=entry["records"]["completed_prefix"],
                resumed=entry["records"]["resumed"],
                recorded_totals={
                    "cold_total_s": totals["cold_candidate"]["total_time_loss_s"],
                    "prefix_total_s": totals["completed_prefix"]["total_time_loss_s"],
                    "resumed_total_s": totals["resumed"]["total_time_loss_s"],
                    "warm_total_s": totals["warm"]["total_time_loss_s"],
                    "cold_trip_count": totals["cold_candidate"]["trip_count"],
                    "prefix_trip_count": totals["completed_prefix"]["trip_count"],
                    "resumed_trip_count": totals["resumed"]["trip_count"],
                    "warm_trip_count": totals["warm"]["trip_count"],
                }))
        return payloads

    def completed_ledger(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{
                "identity": dict(entry["identity"]),
                "captured_phases": sorted(entry["records"]),
                "captured_summaries": sorted(entry["totals"]),
                "record_counts": {phase: len(records)
                                  for phase, records in entry["records"].items()},
            } for _, entry in sorted(self._entries.items())]


# Version 2 is deliberately additive.  The rejected v1 contract remains
# readable and its tests retain their historical behaviour; v2 adds the warm
# boundary that is required to decide whether a vehicle belongs in the prefix
# or resumed population.
RAW_SCHEMA_V2 = "monthly_warm_state_raw_forensics_v2"
REPORT_SCHEMA_V2 = "monthly_warm_state_forensic_report_v2"
GLOBAL_SCHEMA_V2 = "monthly_warm_state_forensic_verdict_v2"
V2_IDENTITY_FIELDS = frozenset({
    "schedule_id", "demand_variant", "seed", "warm_point_s"})


def _identity_v2(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != V2_IDENTITY_FIELDS:
        raise ForensicObservationError(
            "v2 identity must contain exactly schedule_id, demand_variant, "
            "seed and warm_point_s")
    base = _identity(value["schedule_id"], value["demand_variant"],
                     value["seed"])
    point = value["warm_point_s"]
    if isinstance(point, bool) or not isinstance(point, int) or point <= 0:
        raise ForensicObservationError(
            "v2 identity warm_point_s must be a positive integer")
    return {**base, "warm_point_s": point}


def identity_label_v2(identity: Mapping[str, Any]) -> str:
    checked = _identity_v2(identity)
    return identity_label(checked)


def build_raw_payload_v2(*, identity: Mapping[str, Any], cold: Any,
                         prefix: Any, resumed: Any,
                         recorded_totals: Any) -> dict[str, Any]:
    payload = {
        "schema": RAW_SCHEMA_V2,
        "identity": _identity_v2(identity),
        "records": {
            "cold_candidate": validate_records(cold, "cold"),
            "completed_prefix": validate_records(prefix, "prefix"),
            "resumed": validate_records(resumed, "resumed"),
        },
        "recorded_totals": _validate_recorded_totals(recorded_totals),
    }
    payload["content_digest"] = canonical_digest(payload)
    return payload


def validate_raw_payload_v2(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ForensicObservationError("v2 raw forensic payload must be an object")
    expected = {"schema", "identity", "records", "recorded_totals",
                "content_digest"}
    if set(payload) != expected or payload.get("schema") != RAW_SCHEMA_V2:
        raise ForensicObservationError("v2 raw forensic payload schema is invalid")
    digest_body = {key: value for key, value in payload.items()
                   if key != "content_digest"}
    if canonical_digest(digest_body) != payload["content_digest"]:
        raise ForensicObservationError(
            "v2 raw forensic payload digest does not match")
    records = payload["records"]
    if not isinstance(records, Mapping) or set(records) != set(PHASES):
        raise ForensicObservationError("v2 raw forensic phases are incomplete")
    return build_raw_payload_v2(
        identity=payload["identity"], cold=records["cold_candidate"],
        prefix=records["completed_prefix"], resumed=records["resumed"],
        recorded_totals=payload["recorded_totals"])


def build_forensic_report_v2(raw_payload: Any) -> dict[str, Any]:
    raw = validate_raw_payload_v2(raw_payload)
    identity = raw["identity"]
    point = identity["warm_point_s"]
    cold = raw["records"]["cold_candidate"]
    prefix = raw["records"]["completed_prefix"]
    resumed = raw["records"]["resumed"]

    cold_ids, prefix_ids, resumed_ids = set(cold), set(prefix), set(resumed)
    split_ids = prefix_ids | resumed_ids
    overlap = sorted(prefix_ids & resumed_ids)
    missing = sorted(cold_ids - split_ids)
    extra = sorted(split_ids - cold_ids)
    expected_prefix = {
        vehicle for vehicle, record in cold.items()
        if record["arrival"] <= float(point)
    }
    expected_resumed = cold_ids - expected_prefix
    misplaced_prefix = sorted(prefix_ids & expected_resumed)
    misplaced_resumed = sorted(resumed_ids & expected_prefix)

    # Only an unambiguous split record may participate in per-field comparison.
    # Population errors retain their raw records and are classified first.
    split = dict(prefix)
    for vehicle, record in resumed.items():
        if vehicle not in split:
            split[vehicle] = record
    comparable = sorted((cold_ids & split_ids) - set(overlap))
    field_counts = {field: 0 for field in TRIP_FIELDS}
    deltas: list[float] = []
    differences: list[dict[str, Any]] = []
    for vehicle in comparable:
        changed = []
        for field in TRIP_FIELDS:
            if cold[vehicle][field] != split[vehicle][field]:
                field_counts[field] += 1
                changed.append(field)
        delta = split[vehicle]["timeLoss"] - cold[vehicle]["timeLoss"]
        deltas.append(delta)
        if changed:
            differences.append({
                "id": vehicle,
                "phase": ("completed_prefix" if vehicle in prefix
                          else "resumed"),
                "changed_fields": changed,
                "cold_time_loss_s": cold[vehicle]["timeLoss"],
                "split_time_loss_s": split[vehicle]["timeLoss"],
                "delta_s": delta,
            })

    raw_totals = _raw_totals(cold, prefix, resumed)
    raw_delta = math.fsum((raw_totals["split_total_s"],
                           -raw_totals["cold_total_s"]))
    recorded = raw["recorded_totals"]
    recorded_delta = math.fsum((recorded["warm_total_s"],
                                -recorded["cold_total_s"]))
    aggregate_mismatches = _aggregate_mismatches(raw_totals, recorded)
    population_error = any((missing, extra, overlap, misplaced_prefix,
                            misplaced_resumed))
    if population_error:
        classification = POPULATION_PARTITION_ERROR
    elif aggregate_mismatches:
        classification = AGGREGATE_REPORTING_INCONSISTENCY
    else:
        changed_fields = {field for field, count in field_counts.items() if count}
        if not changed_fields:
            classification = EXACT_AGREEMENT
        elif changed_fields == {"timeLoss"}:
            classification = TIME_LOSS_ONLY_DRIFT
        else:
            classification = KINEMATIC_OUTPUT_DRIFT

    report = {
        "schema": REPORT_SCHEMA_V2,
        "identity": dict(identity),
        "raw_content_digest": raw["content_digest"],
        "classification": classification,
        "population": {
            "cold_count": len(cold_ids),
            "prefix_count": len(prefix_ids),
            "resumed_count": len(resumed_ids),
            "split_count": len(split_ids),
            "expected_prefix_count": len(expected_prefix),
            "expected_resumed_count": len(expected_resumed),
            "missing_from_split": missing,
            "extra_in_split": extra,
            "overlap": overlap,
            "misplaced_in_prefix": misplaced_prefix,
            "misplaced_in_resumed": misplaced_resumed,
            "boundary_rule": (
                "cold arrival <= warm_point_s is completed_prefix; "
                "cold arrival > warm_point_s is resumed"),
        },
        "raw_totals": raw_totals,
        "recorded_totals": dict(recorded),
        "aggregate_deltas": {
            "split_minus_cold_time_loss_s": raw_delta,
            "warm_minus_cold_time_loss_s": recorded_delta,
        },
        "aggregate_mismatches": aggregate_mismatches,
        "field_mismatch_counts": field_counts,
        "differing_vehicle_count": len(differences),
        "time_loss_delta_distribution": _distribution(deltas),
        "bounded_exemplars": sorted(
            differences, key=lambda item: (-abs(item["delta_s"]), item["id"])
        )[:EXEMPLAR_LIMIT],
        "record_digests": {
            phase: canonical_digest(raw["records"][phase])
            for phase in PHASES
        },
        "reporting_precision": REPORTING_PRECISION,
    }
    report["content_digest"] = canonical_digest(report)
    return report


def validate_forensic_report_v2(report: Any,
                                raw_payload: Any) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        raise ForensicObservationError("v2 forensic report must be an object")
    rebuilt = build_forensic_report_v2(raw_payload)
    if json.loads(json.dumps(report)) != json.loads(json.dumps(rebuilt)):
        raise ForensicObservationError(
            "v2 forensic report does not recompute from raw records")
    return rebuilt


def build_global_verdict_v2(
        reports: list[Mapping[str, Any]],
        expected_identities: list[Mapping[str, Any]]) -> dict[str, Any]:
    expected_checked = [_identity_v2(item) for item in expected_identities]
    expected = sorted(identity_label_v2(item) for item in expected_checked)
    observed_checked = [_identity_v2(item["identity"]) for item in reports]
    observed = sorted(identity_label_v2(item) for item in observed_checked)
    if observed != expected or len(observed) != len(set(observed)):
        raise ForensicObservationError(
            f"v2 forensic identity coverage differs: {observed} != {expected}")
    expected_by_label = {
        identity_label_v2(item): item for item in expected_checked}
    for item in observed_checked:
        label = identity_label_v2(item)
        if item != expected_by_label[label]:
            raise ForensicObservationError(
                f"v2 forensic identity content differs for {label}")
    counts = {classification: 0 for classification in CLASSIFICATIONS}
    for report in reports:
        classification = report.get("classification")
        if classification not in counts:
            raise ForensicObservationError(
                f"unknown v2 report classification {classification!r}")
        counts[classification] += 1
    non_exact = [item for item in reports
                 if item["classification"] != EXACT_AGREEMENT]
    verdict = {
        "schema": GLOBAL_SCHEMA_V2,
        "identity_count": len(reports),
        "classification_counts": counts,
        "all_exact": not non_exact,
        "first_non_exact_identity": (None if not non_exact else
                                     dict(non_exact[0]["identity"])),
        "report_digests": {
            identity_label_v2(item["identity"]): item["content_digest"]
            for item in reports
        },
        "claim_scope": (
            "diagnostic localization only; not equivalence, performance, "
            "warming readiness, cache adoption or release evidence"),
    }
    verdict["content_digest"] = canonical_digest(verdict)
    return verdict


class BoundaryAwareForensicObserver:
    """A v2 observer configured with the exact boundary for every identity."""

    def __init__(self, expected_identities: list[Mapping[str, Any]]) -> None:
        checked = [_identity_v2(item) for item in expected_identities]
        if not checked:
            raise ForensicObservationError(
                "v2 observer needs at least one expected identity")
        self._expected: dict[tuple[str, str, int], dict[str, Any]] = {}
        for identity in checked:
            key = self._key(identity["schedule_id"],
                            identity["demand_variant"], identity["seed"])
            if key in self._expected:
                raise ForensicObservationError(
                    f"duplicate or conflicting v2 identity {key}")
            self._expected[key] = identity
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str, int], dict[str, Any]] = {}

    @staticmethod
    def _key(schedule_id: str, demand_variant: str, seed: int):
        checked = _identity(schedule_id, demand_variant, seed)
        return (checked["schedule_id"], checked["demand_variant"],
                checked["seed"])

    def _known_key(self, schedule_id: str, demand_variant: str, seed: int):
        key = self._key(schedule_id, demand_variant, seed)
        if key not in self._expected:
            raise ForensicObservationError(
                f"unknown v2 forensic identity {key}")
        return key

    def capture(self, *, schedule_id: str, demand_variant: str, seed: int,
                phase: str, tripinfo_path: Path | str,
                reported_total_s: Any, reported_trip_count: Any) -> None:
        if phase not in PHASES:
            raise ForensicObservationError(f"unknown forensic phase {phase!r}")
        key = self._known_key(schedule_id, demand_variant, seed)
        records = parse_tripinfo(tripinfo_path)
        total = _finite_number(reported_total_s, f"{phase} reported total")
        if isinstance(reported_trip_count, bool) or \
                not isinstance(reported_trip_count, int) or \
                reported_trip_count < 0:
            raise ForensicObservationError(
                f"{phase} reported trip count must be a non-negative integer")
        with self._lock:
            entry = self._entries.setdefault(key, {
                "identity": dict(self._expected[key]), "records": {},
                "totals": {}})
            if phase in entry["records"]:
                raise ForensicObservationError(
                    f"duplicate v2 forensic phase {phase} for {key}")
            entry["records"][phase] = records
            entry["totals"][phase] = {
                "total_time_loss_s": total,
                "trip_count": reported_trip_count,
            }

    def capture_warm_summary(self, *, schedule_id: str, demand_variant: str,
                             seed: int, reported_total_s: Any,
                             reported_trip_count: Any) -> None:
        key = self._known_key(schedule_id, demand_variant, seed)
        total = _finite_number(reported_total_s, "warm reported total")
        if isinstance(reported_trip_count, bool) or \
                not isinstance(reported_trip_count, int) or \
                reported_trip_count < 0:
            raise ForensicObservationError(
                "warm reported trip count must be a non-negative integer")
        with self._lock:
            entry = self._entries.setdefault(key, {
                "identity": dict(self._expected[key]), "records": {},
                "totals": {}})
            if "warm" in entry["totals"]:
                raise ForensicObservationError(
                    f"duplicate v2 warm forensic summary for {key}")
            entry["totals"]["warm"] = {
                "total_time_loss_s": total,
                "trip_count": reported_trip_count,
            }

    def raw_payloads(self, expected_identities=None) -> list[dict[str, Any]]:
        if expected_identities is None:
            expected = list(self._expected.values())
        else:
            expected = [_identity_v2(item) for item in expected_identities]
            if len(expected) != len({identity_label_v2(item)
                                     for item in expected}):
                raise ForensicObservationError(
                    "v2 observer expected identities are duplicated")
            if {identity_label_v2(item): item for item in expected} != {
                    identity_label_v2(item): item
                    for item in self._expected.values()}:
                raise ForensicObservationError(
                    "v2 observer expected identity contract changed")
        ordered_keys = [self._key(item["schedule_id"],
                                  item["demand_variant"], item["seed"])
                        for item in expected]
        with self._lock:
            if set(self._entries) != set(ordered_keys):
                raise ForensicObservationError(
                    "v2 observer identity coverage is incomplete")
            snapshot = {
                key: {"identity": dict(value["identity"]),
                      "records": {phase: dict(records)
                                  for phase, records in value["records"].items()},
                      "totals": {phase: dict(total)
                                 for phase, total in value["totals"].items()}}
                for key, value in self._entries.items()
            }
        payloads = []
        for key in ordered_keys:
            entry = snapshot[key]
            if set(entry["records"]) != set(PHASES):
                raise ForensicObservationError(
                    f"v2 forensic phases for {key} are incomplete")
            if set(entry["totals"]) != set(PHASES) | {"warm"}:
                raise ForensicObservationError(
                    f"v2 forensic totals for {key} are incomplete")
            totals = entry["totals"]
            payloads.append(build_raw_payload_v2(
                identity=entry["identity"],
                cold=entry["records"]["cold_candidate"],
                prefix=entry["records"]["completed_prefix"],
                resumed=entry["records"]["resumed"],
                recorded_totals={
                    "cold_total_s": totals["cold_candidate"][
                        "total_time_loss_s"],
                    "prefix_total_s": totals["completed_prefix"][
                        "total_time_loss_s"],
                    "resumed_total_s": totals["resumed"][
                        "total_time_loss_s"],
                    "warm_total_s": totals["warm"]["total_time_loss_s"],
                    "cold_trip_count": totals["cold_candidate"]["trip_count"],
                    "prefix_trip_count": totals["completed_prefix"]["trip_count"],
                    "resumed_trip_count": totals["resumed"]["trip_count"],
                    "warm_trip_count": totals["warm"]["trip_count"],
                }))
        return payloads

    def completed_ledger(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{
                "identity": dict(entry["identity"]),
                "captured_phases": sorted(entry["records"]),
                "captured_summaries": sorted(entry["totals"]),
                "record_counts": {phase: len(records)
                                  for phase, records in entry["records"].items()},
            } for _, entry in sorted(self._entries.items())]
