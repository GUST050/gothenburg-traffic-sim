#!/usr/bin/env python3
"""Replay two named frozen daily units through the REAL
MonthlyDemandResolverRunner/IndependentDailyRunner path and report
closure-routing evidence measured, not asserted.

Context (2026-08-29 repair-batch continuation, review-03/review-fix-03):
review-fix-03 reported BLOCKED because it tested `which sumo` / a bare
`import sumolib` instead of this repository's own runtime contract,
`traffic_sim.simulation.runtime.sumo_home()`, which DOES resolve a working
SUMO installation on this host. This tool resolves SUMO the same way
production does -- through `sumo_home()` only -- and then runs exactly the
two named daily units this review batch could never actually measure:
`daily-unit-24737391111be0e137537df7` (the unit whose earlier, DIFFERENT
direct-SUMO harness run timed out and leaked closed-edge throughput, before
the closure_routing.py rewrite existed) and
`daily-unit-2387bbad11130660b9de0d17` (a healthy control on the same closed
edge, different time-of-day window).

Scope, deliberately narrow:
* Reads the original `ui-monthly-12hg8f3` ledger read-only. Never writes
  into that workspace.
* Reconstructs each named unit's own one-day `ClosureSchedule` from
  `ledgers/units.ndjson` and independently re-derives its unit_id via
  `independent_daily.decompose_schedules`, asserting it matches the ledger's
  own id -- this is the "reconstruct and verify identity" step the plan
  requires, not a trust-the-ledger shortcut.
* Instantiates `MonthlyDemandResolverRunner` with `build_missing=False` (an
  existing succeeded demand archive must already cover 2027-09-28; this tool
  never triggers a fresh multi-minute demand calibration) and
  `IndependentDailyRunner` with `queue_workers=1`, wrapping it -- the exact
  production construction `run_monthly_closure_search.py` uses for an
  independent-daily search, minus the search/screening machinery. There is
  no code path here that can reach `run_monthly_search` or any campaign
  orchestration.
* `--output-root` MUST NOT already exist; every run gets its own exclusive
  release/baseline-cache/daily-cache/report roots so a failed or partial
  attempt never contaminates a later one, and nothing under the real
  `runs/` tree (other than the read-only demand archives this tool
  resolves against) is ever touched.
* Runs q10 alone first (so first-attempt wall time is directly observable
  in isolation), then adds q50, then q90, incrementally -- matching how a
  real pilot round accumulates repetitions.

For each (unit, variant) this records: first-attempt wall time, the SUMO
launch/timeout telemetry delta, rerouted/denied counts and denial reasons
(resolved from the durable access-impact report review finding 4's fix now
lets a reader reach), the routing-provenance record (policy version,
vehicle class, unit/schedule identity, transformed-route and access-report
digests), teleports, active closed-edge throughput, hard failures/recovery
status, and a PER-VEHICLE byte comparison of every vehicle NOT named
rerouted or denied against its original source fragment (review finding 3,
2026-08-30: an earlier version of this tool gave up -- reported `null` --
whenever anything at all was rerouted, because it only ever compared whole-
file digests. `closure_routing.ClosureRoutingResult` now names its rerouted
vehicles explicitly and the transformed route file itself is preserved
durably (`ArchivedDemandSumoRunner._preserve_transformed_route`), so this
tool can identify and byte-diff exactly the vehicles that should be
untouched, independently of the runner that produced them, whether or not
anything else in the same run was rerouted or denied). The designated
healthy-control unit additionally gets an explicit selected-field semantic
check against the invariants a healthy run must satisfy
(no denials, no hard failures, no teleports, every vehicle byte-identical)
-- see `_healthy_control_semantic_check`. The report also stores every exact
identity-bearing launch record and evaluates a top-level fail-closed verdict;
any missing unit/variant, retry, timeout, slow first attempt, health/recovery/
provenance failure or semantic mismatch returns exit code 1.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.contracts import ClosureSchedule, load_closure_search_spec
from traffic_sim.core.fingerprint import sha256_file, sumo_version
from traffic_sim.simulation import closure_ledgers
from traffic_sim.simulation.deterministic_disruption import VARIANT_FILENAMES
from traffic_sim.simulation.finalist_decision import DEMAND_VARIANTS
from traffic_sim.simulation.independent_daily import (
    INDEPENDENT_DAILY_ENVELOPE_POLICY,
    IndependentDailyRunner,
    decompose_schedules,
)
from traffic_sim.simulation.monthly_demand import MonthlyDemandResolverRunner
from traffic_sim.simulation import closure_routing, monthly_sumo
from traffic_sim.simulation.monthly_search import canonical_seed
from traffic_sim.simulation.runtime import SumoRuntimeError, sumo_home

SCHEMA = "closure_routing_frozen_unit_verification_v2"
MAX_FIRST_ATTEMPT_WALL_S = 300.0
REQUIRED_UNIT_IDS = frozenset({
    "daily-unit-24737391111be0e137537df7",
    "daily-unit-2387bbad11130660b9de0d17",
})

#: The ONE unit this tool treats as the healthy control (review finding 2,
#: 2026-08-30, review-03). Review-fix-03's version applied
#: `_healthy_control_semantic_check` to every zero-denial observation,
#: which let the former-timeout unit (daily-unit-24737391111be0e137537df7)
#: be labelled a healthy control on a variant where it happened to deny
#: nothing -- membership must come from the unit's own identity, never from
#: an incidental property of one run's output.
HEALTHY_CONTROL_UNIT_ID = "daily-unit-2387bbad11130660b9de0d17"

#: Explicit allowlist for the healthy-control reference comparison. Deliberately
#: excludes every field that legitimately drifts between two otherwise-identical
#: replays: content-addressed digests (transformed route / access-impact /
#: canonical observation -- these are stable identities, not comparison targets,
#: and are compared for equality separately as *identity*, not health), wall-clock
#: timing, launch-telemetry counters, and the routing-provenance record's own
#: `routing_policy_version` (a policy bump is expected drift, not a health
#: regression, and is reported elsewhere in this same document).
HEALTHY_CONTROL_REFERENCE_FIELDS = (
    "routing_provenance.denied_count",
    "hard_failures",
    "teleports.baseline",
    "teleports.candidate",
    "active_closed_edge_throughput",
    "unaffected_route_check.byte_identical_to_source",
    "unaffected_route_check.missing_vehicle_ids",
    "unaffected_route_check.mismatched_vehicle_ids",
)


def _dotted_get(payload: Mapping[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value

# The value `serve.py`'s `MONTHLY_BASELINE_TRIP_P99_S` uses -- the constant
# every "ui-monthly-*" search (including the source workspace this tool
# reads) was actually launched with. Not imported directly: `serve.py`
# pulls in the full scientific stack at module scope for unrelated reasons,
# and this tool must stay importable without it.
UI_MONTHLY_BASELINE_TRIP_P99_S = 3600


def _load_unit(directory: Path, unit_id: str) -> dict[str, Any]:
    for row in closure_ledgers.iter_unit_rows(directory):
        if str(row.get("unit_id")) == unit_id:
            return row
    raise ValueError(f"unit {unit_id!r} is not present in {directory}")


def _reconstruct_and_verify(spec, row: dict[str, Any]) -> ClosureSchedule:
    unit_id = str(row["unit_id"])
    schedule = ClosureSchedule.from_dict(row["schedule"])
    units, _parents = decompose_schedules(spec, [schedule])
    if len(units) != 1:
        raise ValueError(
            f"unit {unit_id!r}: reconstructed schedule decomposes into "
            f"{len(units)} daily units, expected exactly 1")
    derived = units[0]
    if derived.unit_id != unit_id:
        raise ValueError(
            f"unit {unit_id!r}: re-derived unit_id {derived.unit_id!r} does "
            "not match the ledger -- reconstruction is unsound")
    if dict(derived.identity) != dict(row["identity"]):
        raise ValueError(
            f"unit {unit_id!r}: re-derived identity does not match the "
            "ledger's own identity record")
    return schedule


def _index_vehicle_fragments(text: str) -> dict[str, str]:
    """Vehicle id -> exact source fragment, reusing closure_routing's own
    fragment grammar so this tool cannot silently disagree with the parser
    that actually produced the transformed route."""
    from traffic_sim.simulation import closure_routing  # noqa: PLC0415
    fragments: dict[str, str] = {}
    for match in closure_routing._VEHICLE_FRAGMENT_RE.finditer(text):
        fragment = match.group()
        open_tag = closure_routing._OPEN_TAG_RE.match(fragment)
        vehicle_id = closure_routing._ID_ATTR_RE.search(open_tag.group()).group(1)
        fragments[vehicle_id] = fragment
    return fragments


def _verify_unaffected_routes(
    *, source_path: Path, transformed_path: Path,
    rerouted_vehicle_ids: Sequence[str], denied_vehicle_ids: Sequence[str],
) -> dict[str, Any]:
    """Byte-diff every vehicle NOT named rerouted/denied, independently of
    the runner that produced the transformed file (review finding 3): a
    whole-file digest comparison only tells you SOMETHING moved when
    anything was rerouted or denied, never which vehicles stayed exact.
    """
    from traffic_sim.simulation import closure_routing  # noqa: PLC0415
    source_fragments = _index_vehicle_fragments(
        closure_routing._read_route_text(Path(source_path)))
    transformed_fragments = _index_vehicle_fragments(
        closure_routing._read_route_text(Path(transformed_path)))
    affected = set(rerouted_vehicle_ids) | set(denied_vehicle_ids)
    unaffected_ids = sorted(set(source_fragments) - affected)
    missing = [vid for vid in unaffected_ids if vid not in transformed_fragments]
    mismatched = [
        vid for vid in unaffected_ids
        if vid in transformed_fragments
        and transformed_fragments[vid] != source_fragments[vid]
    ]
    return {
        "method": "per_vehicle_fragment_byte_comparison",
        "checked_vehicle_count": len(unaffected_ids),
        "missing_vehicle_ids": missing,
        "mismatched_vehicle_ids": mismatched,
        "byte_identical_to_source": not missing and not mismatched,
    }


#: A "healthy control" observation is one that DENIES no departures --
#: acceptance criterion "the healthy control invents no denied trips".
#: Rerouting thousands of vehicles around a real closure is normal and
#: expected (both frozen units do this); it is a fabricated DENIAL that
#: would mean the routing policy invented lost access, so that -- not the
#: reroute count -- is what gates this check.
def _healthy_control_semantic_check(
    *, unit_id: str, routing: dict[str, Any] | None, canonical: dict[str, Any],
    unaffected_route_check: dict[str, Any] | None,
    reference_observation: Mapping[str, Any] | None,
    reference_report_path: Path | None,
    reference_report_sha256: str | None,
) -> dict[str, Any] | None:
    # Membership in the healthy-control comparison comes from the unit's OWN
    # identity, never from an incidental property (e.g. zero denials) of one
    # run's output -- see `HEALTHY_CONTROL_UNIT_ID`'s docstring (review
    # finding 2). The former-timeout unit is never eligible here, even on a
    # variant where it happens to deny nothing.
    if unit_id != HEALTHY_CONTROL_UNIT_ID:
        return None
    if routing is None or routing.get("denied_count") != 0:
        return None
    health = canonical.get("health", {})
    invariant_checks = {
        "no_denied_trips": routing.get("denied_count") == 0,
        "no_hard_failures": canonical.get("hard_failures") in (None, [], ()),
        "no_baseline_teleports": (
            health.get("baseline", {}).get("teleport_total") == 0),
        "no_candidate_teleports": (
            health.get("candidate", {}).get("teleport_total") == 0),
        "every_unaffected_vehicle_byte_identical": bool(
            unaffected_route_check
            and unaffected_route_check.get("byte_identical_to_source")),
    }
    observation: dict[str, Any] = {
        "routing_provenance": routing,
        "hard_failures": canonical.get("hard_failures", []),
        "teleports": {
            "baseline": health.get("baseline", {}).get("teleport_total"),
            "candidate": health.get("candidate", {}).get("teleport_total"),
        },
        "active_closed_edge_throughput": canonical.get(
            "candidate_metrics", {}).get("closed_edge_throughput"),
        "unaffected_route_check": unaffected_route_check,
    }
    reference_comparison: dict[str, Any] | None = None
    if reference_observation is not None:
        field_results = {}
        for field in HEALTHY_CONTROL_REFERENCE_FIELDS:
            current_value = _dotted_get(observation, field)
            reference_value = _dotted_get(reference_observation, field)
            field_results[field] = {
                "current": current_value,
                "reference": reference_value,
                "equal": current_value == reference_value,
            }
        reference_comparison = {
            "reference_report": (
                str(reference_report_path)
                if reference_report_path is not None else None),
            "reference_report_sha256": reference_report_sha256,
            "excluded_fields_reason": (
                "content-addressed digests, wall-clock timing, launch "
                "telemetry, and routing_policy_version are expected to "
                "drift between replays and are reported elsewhere, not "
                "compared here"),
            "fields": field_results,
            "all_equal": all(
                item["equal"] for item in field_results.values()),
        }
    return {
        "method": "selected_field_comparison_against_healthy_control_invariants",
        "checks": invariant_checks,
        "all_passed": all(invariant_checks.values()),
        "reference_comparison": reference_comparison,
    }


def _observation_record(
    resolved: MonthlyDemandResolverRunner,
    digest: monthly_sumo.CanonicalObservationDigest,
    *,
    source_route_path: Path,
    unit_id: str,
    reference_observation: Mapping[str, Any] | None = None,
    reference_report_path: Path | None = None,
    reference_report_sha256: str | None = None,
) -> dict[str, Any]:
    canonical = monthly_sumo.resolve_canonical_observation(
        resolved.cache_root, digest.sha256)
    provenance = canonical.get("provenance", {})
    routing = provenance.get("routing_provenance")
    access_impact = None
    routing_record = None
    if routing is not None:
        routing_record = closure_routing.RoutingProvenance.from_dict(routing)
        access_impact = monthly_sumo.resolve_access_impact_report(
            resolved.cache_root, routing_record.access_impact_sha256)
        transformed_route_path = monthly_sumo.resolve_transformed_route(
            resolved.cache_root, routing_record.transformed_route_sha256)
        closure_routing.validate_access_impact_report(
            access_impact, routing_record,
            transformed_route_path=transformed_route_path)
    reason_counts: dict[str, int] = {}
    unaffected_route_check = None
    if access_impact is not None:
        for record in access_impact.get("access_impact", []):
            reason = str(record.get("reason"))
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        denied_vehicle_ids = [
            str(record.get("vehicle_id"))
            for record in access_impact.get("access_impact", [])
        ]
        rerouted_vehicle_ids = list(
            access_impact.get("rerouted_vehicle_ids", []))
        unaffected_route_check = _verify_unaffected_routes(
            source_path=source_route_path,
            transformed_path=transformed_route_path,
            rerouted_vehicle_ids=rerouted_vehicle_ids,
            denied_vehicle_ids=denied_vehicle_ids)
    health = canonical.get("health", {})
    candidate_metrics = canonical.get("candidate_metrics", {})
    return {
        "candidate_time_loss_s": canonical.get("candidate_time_loss_s"),
        "baseline_time_loss_s": canonical.get("baseline_time_loss_s"),
        "hard_failures": canonical.get("hard_failures", []),
        "feasibility_hard_failures": canonical.get("feasibility", {}).get(
            "hard_failures"),
        "recovery": canonical.get("recovery"),
        "health": health,
        "teleports": {
            "baseline": health.get("baseline", {}).get("teleport_total"),
            "candidate": health.get("candidate", {}).get("teleport_total"),
        },
        "active_closed_edge_throughput": candidate_metrics.get(
            "closed_edge_throughput"),
        "routing_provenance": routing,
        "denial_reasons": reason_counts,
        "unaffected_route_check": unaffected_route_check,
        "healthy_control_semantic_check": _healthy_control_semantic_check(
            unit_id=unit_id, routing=routing, canonical=canonical,
            unaffected_route_check=unaffected_route_check,
            reference_observation=reference_observation,
            reference_report_path=reference_report_path,
            reference_report_sha256=reference_report_sha256),
        "canonical_observation_sha256": digest.sha256,
    }


def _find_reference_observation(
    reference_report: Mapping[str, Any] | None, *, unit_id: str, variant: str,
) -> Mapping[str, Any] | None:
    if reference_report is None:
        return None
    for unit_report in reference_report.get("units", []):
        if unit_report.get("unit_id") != unit_id:
            continue
        variant_report = unit_report.get("variants", {}).get(variant)
        if isinstance(variant_report, Mapping):
            return variant_report.get("observation")
    return None


_LAUNCH_IDENTITY_FIELDS = (
    "candidate_id", "work_date", "stage", "variant", "seed", "attempt",
)


def _launch_identity(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(record.get(field) for field in _LAUNCH_IDENTITY_FIELDS)


def _new_launch_records(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return exact records added by one call, rejecting mutation/removal."""
    before_by_identity = {_launch_identity(record): dict(record) for record in before}
    after_by_identity = {_launch_identity(record): dict(record) for record in after}
    if len(before_by_identity) != len(before) or len(after_by_identity) != len(after):
        raise ValueError("launch record identity is duplicated")
    for identity, record in before_by_identity.items():
        if after_by_identity.get(identity) != record:
            raise ValueError("existing launch record changed or disappeared")
    return [
        dict(record) for record in after
        if _launch_identity(record) not in before_by_identity
    ]


def _launch_state(
    records: Sequence[Mapping[str, Any]], *, run_candidate_wall_s: float,
) -> dict[str, Any]:
    ordered = sorted((dict(record) for record in records),
                     key=lambda record: record["attempt"])
    first = next((record for record in ordered if record["attempt"] == 1), None)
    retries = [record for record in ordered if record["attempt"] > 1]
    timeouts = [record for record in ordered if record["timed_out"]]
    return {
        "records": ordered,
        "attempt_count": len(ordered),
        "first_attempt": first,
        # The wrapper timer is an honest first-attempt measurement only when
        # the exact record population proves that no retry occurred.
        "first_attempt_wall_s": (
            run_candidate_wall_s if len(ordered) == 1 and first is not None
            else None),
        "run_candidate_wall_s": run_candidate_wall_s,
        "retry_records": retries,
        "timeout_records": timeouts,
        "final_outcome": ordered[-1]["outcome"] if ordered else None,
    }


def _verification_result(
    report: Mapping[str, Any], *, reference_required: bool,
) -> dict[str, Any]:
    """Evaluate every acceptance criterion and return a fail-closed verdict."""
    failures: list[str] = []
    if report.get("schema") != SCHEMA:
        failures.append("report schema is missing or incompatible")
    reference_path = report.get("reference_report")
    reference_sha256 = report.get("reference_report_sha256")
    if reference_required and (
            not isinstance(reference_path, str) or not reference_path
            or not isinstance(reference_sha256, str)
            or len(reference_sha256) != 64
            or any(char not in "0123456789abcdef" for char in reference_sha256)):
        failures.append("reference report path/digest is missing or invalid")
    units = report.get("units")
    if not isinstance(units, list):
        return {
            "status": "failed", "all_passed": False,
            "failures": ["units is not a list"],
        }
    unit_ids = [unit.get("unit_id") for unit in units if isinstance(unit, Mapping)]
    if (len(unit_ids) != len(units)
            or any(not isinstance(unit_id, str) or not unit_id
                   for unit_id in unit_ids)
            or len(unit_ids) != len(set(unit_ids))):
        failures.append("unit reports are malformed or duplicated")
    valid_unit_ids = {
        unit_id for unit_id in unit_ids
        if isinstance(unit_id, str) and unit_id
    }
    missing = sorted(REQUIRED_UNIT_IDS - valid_unit_ids)
    unexpected = sorted(valid_unit_ids - REQUIRED_UNIT_IDS)
    if missing:
        failures.append(f"missing required units: {missing}")
    if unexpected:
        failures.append(f"unexpected units: {unexpected}")

    for unit in units:
        if not isinstance(unit, Mapping):
            continue
        unit_id = unit.get("unit_id")
        schedule_id = unit.get("schedule_id")
        variants = unit.get("variants")
        if not isinstance(variants, Mapping):
            failures.append(f"{unit_id}: variants is not an object")
            continue
        if set(variants) != set(DEMAND_VARIANTS):
            failures.append(f"{unit_id}: variant population is incomplete")
        for variant in DEMAND_VARIANTS:
            prefix = f"{unit_id}/{variant}"
            variant_report = variants.get(variant)
            if not isinstance(variant_report, Mapping):
                failures.append(f"{prefix}: report is missing")
                continue
            if variant_report.get("error") is not None:
                failures.append(f"{prefix}: execution error recorded")
            if variant_report.get("hard_failures") != []:
                failures.append(f"{prefix}: hard failures are present")
            if variant_report.get("timeout_undecided") != []:
                failures.append(f"{prefix}: unresolved timeout is present")

            launch = variant_report.get("launch_state")
            if not isinstance(launch, Mapping):
                failures.append(f"{prefix}: exact launch state is missing")
            else:
                records = launch.get("records")
                first = launch.get("first_attempt")
                if not isinstance(records, list) or len(records) != 1:
                    failures.append(f"{prefix}: expected exactly one launch attempt")
                expected_launch = {
                    "candidate_id": schedule_id,
                    "work_date": (
                        first.get("work_date") if isinstance(first, Mapping)
                        else None),
                    "stage": "pilot", "variant": variant,
                    "seed": canonical_seed(variant, 0), "attempt": 1,
                    "timed_out": False, "outcome": "success",
                }
                if not isinstance(first, Mapping):
                    failures.append(f"{prefix}: first-attempt record is missing")
                else:
                    expected_launch["work_date"] = unit.get("work_date")
                    if dict(first) != expected_launch:
                        failures.append(
                            f"{prefix}: first-attempt launch identity/outcome is invalid")
                if isinstance(records, list):
                    retry_records = [
                        record for record in records
                        if isinstance(record, Mapping) and record.get("attempt", 0) > 1
                    ]
                    timeout_records = [
                        record for record in records
                        if isinstance(record, Mapping) and record.get("timed_out") is True
                    ]
                    final_outcome = (
                        records[-1].get("outcome")
                        if records and isinstance(records[-1], Mapping) else None)
                    if launch.get("attempt_count") != len(records):
                        failures.append(f"{prefix}: derived attempt count disagrees")
                    if launch.get("retry_records") != retry_records:
                        failures.append(f"{prefix}: derived retry records disagree")
                    if launch.get("timeout_records") != timeout_records:
                        failures.append(f"{prefix}: derived timeout records disagree")
                    if launch.get("final_outcome") != final_outcome:
                        failures.append(f"{prefix}: derived final outcome disagrees")
                timing_fields = {
                    "variant.first_attempt_wall_s": variant_report.get(
                        "first_attempt_wall_s"),
                    "variant.run_candidate_wall_s": variant_report.get(
                        "run_candidate_wall_s"),
                    "launch.first_attempt_wall_s": launch.get(
                        "first_attempt_wall_s"),
                    "launch.run_candidate_wall_s": launch.get(
                        "run_candidate_wall_s"),
                }
                invalid_timing = any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value <= 0
                    or value >= MAX_FIRST_ATTEMPT_WALL_S
                    for value in timing_fields.values()
                )
                if invalid_timing:
                    failures.append(
                        f"{prefix}: timing fields must be finite, positive and "
                        f"below {MAX_FIRST_ATTEMPT_WALL_S:g}s")
                elif len(set(timing_fields.values())) != 1:
                    failures.append(
                        f"{prefix}: duplicated timing fields disagree")
                if launch.get("retry_records") != []:
                    failures.append(f"{prefix}: retry was required")
                if launch.get("timeout_records") != []:
                    failures.append(f"{prefix}: timeout launch was recorded")
                if launch.get("final_outcome") != "success":
                    failures.append(f"{prefix}: final launch outcome is not success")
                telemetry = variant_report.get("launch_telemetry_delta")
                expected_telemetry = {
                    "pilot": {
                        "attempts": 1, "timeouts": 0, "other_outcomes": 1},
                    "finalist": {
                        "attempts": 0, "timeouts": 0, "other_outcomes": 0},
                }
                if telemetry != expected_telemetry:
                    failures.append(
                        f"{prefix}: launch counters disagree with exact records")

            observation = variant_report.get("observation")
            if not isinstance(observation, Mapping):
                failures.append(f"{prefix}: canonical observation is missing")
                continue
            if observation.get("hard_failures") != []:
                failures.append(f"{prefix}: canonical hard failures are present")
            if observation.get("feasibility_hard_failures") != []:
                failures.append(
                    f"{prefix}: closure feasibility hard failures are present")
            routing = observation.get("routing_provenance")
            if not isinstance(routing, Mapping):
                failures.append(f"{prefix}: routing provenance is missing")
            else:
                expected_routing = {
                    "unit_id": unit_id,
                    "candidate_id": schedule_id,
                    "work_date": unit.get("work_date"),
                    "demand_variant": variant,
                    "seed": canonical_seed(variant, 0),
                    "execution_arm": "cold",
                    "vehicle_class": closure_routing.DEFAULT_VCLASS,
                }
                if any(routing.get(key) != value
                       for key, value in expected_routing.items()):
                    failures.append(f"{prefix}: routing identity is inconsistent")
                if routing.get("denied_count") != 0:
                    failures.append(f"{prefix}: denied trips are present")
            if observation.get("denial_reasons") != {}:
                failures.append(f"{prefix}: denial reasons are present/inconsistent")
            teleports = observation.get("teleports")
            if teleports != {"baseline": 0, "candidate": 0}:
                failures.append(f"{prefix}: teleports are present or unmeasured")
            if observation.get("active_closed_edge_throughput") != 0:
                failures.append(f"{prefix}: closed-edge throughput is nonzero/unmeasured")
            recovery = observation.get("recovery")
            if not isinstance(recovery, Mapping) or recovery.get("recovered") is not True:
                failures.append(f"{prefix}: recovery is not proven")
            unaffected = observation.get("unaffected_route_check")
            if (not isinstance(unaffected, Mapping)
                    or unaffected.get("byte_identical_to_source") is not True
                    or unaffected.get("missing_vehicle_ids") != []
                    or unaffected.get("mismatched_vehicle_ids") != []):
                failures.append(f"{prefix}: unaffected routes are not byte-identical")
            if unit_id == HEALTHY_CONTROL_UNIT_ID:
                healthy = observation.get("healthy_control_semantic_check")
                if not isinstance(healthy, Mapping) or healthy.get("all_passed") is not True:
                    failures.append(f"{prefix}: healthy-control invariants failed")
                elif reference_required:
                    comparison = healthy.get("reference_comparison")
                    if (not isinstance(comparison, Mapping)
                            or comparison.get("all_equal") is not True):
                        failures.append(
                            f"{prefix}: healthy-control reference comparison failed")
                    elif (comparison.get("reference_report") != reference_path
                          or comparison.get("reference_report_sha256")
                          != reference_sha256):
                        failures.append(
                            f"{prefix}: healthy-control reference identity disagrees")
            elif observation.get("healthy_control_semantic_check") is not None:
                failures.append(
                    f"{prefix}: non-control unit was labelled healthy control")

    return {
        "status": "passed" if not failures else "failed",
        "all_passed": not failures,
        "criteria": {
            "required_unit_ids": sorted(REQUIRED_UNIT_IDS),
            "required_variants": list(DEMAND_VARIANTS),
            "max_first_attempt_wall_s_exclusive": MAX_FIRST_ATTEMPT_WALL_S,
            "reference_comparison_required": reference_required,
        },
        "failures": failures,
    }


def _run_unit(
    *,
    unit_id: str,
    schedule: ClosureSchedule,
    daily_runner: IndependentDailyRunner,
    resolved: MonthlyDemandResolverRunner,
    stage: str,
    reference_report: Mapping[str, Any] | None = None,
    reference_report_path: Path | None = None,
    reference_report_sha256: str | None = None,
) -> dict[str, Any]:
    variant_reports: dict[str, Any] = {}
    evidence = None
    targets = {variant: 0 for variant in DEMAND_VARIANTS}
    for variant in DEMAND_VARIANTS:
        targets = dict(targets)
        targets[variant] = 1
        before = resolved.launch_telemetry_snapshot()
        before_records = resolved.launch_records_snapshot()
        started = time.perf_counter()
        error: str | None = None
        try:
            evidence = daily_runner.run_candidate(
                schedule, target_repetitions=targets, existing=evidence,
                stage=stage)
        except Exception as exc:  # noqa: BLE001 -- report, do not hide
            error = f"{type(exc).__name__}: {exc}"
            evidence = None
        wall_s = time.perf_counter() - started
        after = resolved.launch_telemetry_snapshot()
        after_records = resolved.launch_records_snapshot()
        exact_records = _new_launch_records(before_records, after_records)
        launch_state = _launch_state(
            exact_records, run_candidate_wall_s=wall_s)
        delta = {
            key: {
                field: after[key][field] - before[key][field]
                for field in after[key]
            }
            for key in after
        }
        seed = canonical_seed(variant, 0)
        report: dict[str, Any] = {
            "seed": seed,
            "first_attempt_wall_s": launch_state["first_attempt_wall_s"],
            "run_candidate_wall_s": wall_s,
            "launch_state": launch_state,
            "launch_telemetry_delta": delta,
            "error": error,
        }
        if evidence is not None:
            report["hard_failures"] = list(evidence.hard_failures)
            report["timeout_undecided"] = [
                item.to_dict() if hasattr(item, "to_dict") else str(item)
                for item in evidence.timeout_undecided
            ]
            matching = [
                digest for digest in evidence.canonical_observation_digests
                if digest.variant == variant and digest.seed == seed
            ]
            if matching:
                source_route_path = (
                    resolved.archive_for(schedule) / VARIANT_FILENAMES[variant])
                try:
                    report["observation"] = _observation_record(
                        resolved, matching[0],
                        source_route_path=source_route_path,
                        unit_id=unit_id,
                        reference_observation=_find_reference_observation(
                            reference_report, unit_id=unit_id, variant=variant),
                        reference_report_path=reference_report_path,
                        reference_report_sha256=reference_report_sha256)
                except Exception as exc:  # noqa: BLE001 -- report and fail
                    report["error"] = f"{type(exc).__name__}: {exc}"
        variant_reports[variant] = report
        if error is not None or (
            evidence is not None
            and (evidence.hard_failures or evidence.timeout_undecided)
        ):
            # A hard failure or unresolved timeout on an earlier variant
            # still lets later variants attempt independently -- each
            # target_repetitions call is its own launch -- so keep going
            # rather than abandon the whole unit on one variant's outcome.
            continue
    return {
        "unit_id": unit_id,
        "schedule_id": schedule.schedule_id,
        "work_date": schedule.first_work_date,
        "variants": variant_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-workspace", type=Path, required=True)
    parser.add_argument("--unit", action="append", required=True, dest="units")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument(
        "--baseline-trip-duration-p99-s", type=int,
        default=UI_MONTHLY_BASELINE_TRIP_P99_S)
    parser.add_argument(
        "--reference-report", type=Path, default=None,
        help=(
            "path to a prior frozen_unit_verification.json (review finding 2): "
            "when supplied, the healthy control's (HEALTHY_CONTROL_UNIT_ID) "
            "observations are compared field-by-field against this report's "
            "matching unit/variant, recording the reference report path, its "
            "own sha256, and per-field equality. Read-only -- never written to."))
    args = parser.parse_args()

    if args.output_root.exists():
        parser.error(f"--output-root {args.output_root} already exists; "
                     "every replay must use a fresh exclusive root")

    reference_report: dict[str, Any] | None = None
    reference_report_sha256: str | None = None
    if args.reference_report is not None:
        if not args.reference_report.is_file():
            parser.error(
                f"--reference-report {args.reference_report} is not a file")
        reference_report = json.loads(
            args.reference_report.read_text(encoding="utf-8"))
        reference_report_sha256 = sha256_file(args.reference_report)

    try:
        home = sumo_home()
    except SumoRuntimeError as exc:
        parser.error(f"cannot resolve SUMO via runtime.sumo_home(): {exc}")
        return 2
    binary = home / "bin" / "sumo"
    if not binary.is_file():
        parser.error(f"sumo_home() resolved {home}, but {binary} is not a file")
        return 2

    ledgers_dir = args.source_workspace / "ledgers"
    input_spec_path = args.source_workspace / "input" / "closure_search.json"
    spec = load_closure_search_spec(input_spec_path)
    policy_path = args.source_workspace / "artifacts" / "policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    include_disruption = policy.get("objective_method") == "closure_cost_v1"

    args.output_root.mkdir(parents=True)
    release_root = args.output_root / "release"
    baseline_cache = args.output_root / "baseline-cache"
    daily_cache = args.output_root / "daily-cache"
    report_root = args.output_root / "report"
    report_root.mkdir()

    reconstructed: list[tuple[str, ClosureSchedule]] = []
    for unit_id in args.units:
        row = _load_unit(ledgers_dir, unit_id)
        schedule = _reconstruct_and_verify(spec, row)
        reconstructed.append((unit_id, schedule))

    resolved = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=args.baseline_trip_duration_p99_s,
        study_provenance_key=(
            "closure-routing-frozen-unit-verify-" + args.output_root.name),
        runs_root=args.runs_root,
        release_root=release_root,
        cache_root=baseline_cache,
        seed_workers=1,
        envelope_policy=INDEPENDENT_DAILY_ENVELOPE_POLICY,
        build_missing=False,
        include_disruption=include_disruption,
    )
    daily_runner = IndependentDailyRunner(
        spec, daily_runner=resolved, cache_root=daily_cache, queue_workers=1)

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "sumo_home": str(home),
        "sumo_version": sumo_version(home),
        "source_workspace": str(args.source_workspace),
        "output_root": str(args.output_root),
        "healthy_control_unit_id": HEALTHY_CONTROL_UNIT_ID,
        "reference_report": (
            str(args.reference_report)
            if args.reference_report is not None else None),
        "reference_report_sha256": reference_report_sha256,
        "units": [],
    }
    try:
        daily_runner.prepare([schedule for _uid, schedule in reconstructed])
        for unit_id, schedule in reconstructed:
            unit_report = _run_unit(
                unit_id=unit_id, schedule=schedule, daily_runner=daily_runner,
                resolved=resolved, stage="pilot",
                reference_report=reference_report,
                reference_report_path=args.reference_report,
                reference_report_sha256=reference_report_sha256)
            report["units"].append(unit_report)
    finally:
        daily_runner.cleanup()

    verification = _verification_result(
        report, reference_required=args.reference_report is not None)
    report["verification_status"] = verification["status"]
    report["all_passed"] = verification["all_passed"]
    report["verification"] = verification

    out_path = report_root / "frozen_unit_verification.json"
    out_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")
    print(f"wrote {out_path}")
    if verification["all_passed"]:
        return 0
    print("verification failed:", file=sys.stderr)
    for failure in verification["failures"]:
        print(f"- {failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
