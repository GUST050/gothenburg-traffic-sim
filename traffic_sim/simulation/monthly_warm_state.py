"""Warm-state contracts for the monthly SUMO backend (LUNA-WARM-01).

Nothing here runs SUMO, touches a cache or enables anything. It defines three
pure contracts the cold path and a future warm path must BOTH satisfy:

1. `build_monthly_observation` — the ONE canonical production observation. Both
   arms call it, so a warm run cannot quietly compare a thinner object than the
   cold run produced. A hand-copied reduced evaluator is exactly the bug this
   prevents: it would let a warm arm "match" on the few fields someone
   remembered to copy while silently differing on the rest.
2. `evaluate_warm_eligibility` — deterministic, fail-closed. Anything unusual
   stays COLD; the warm path is never the fallback.
3. `monthly_warm_identity` — binds a state to the UNFILTERED baseline inputs.
   A closure-specific filtered route must never masquerade as reusable baseline
   identity, or a state warmed for one closure would be reused for another.

Warm execution is opt-in and unreachable by default in this revision.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from traffic_sim.simulation.warm_state_boundary import (
    MESO_RECONCILIATION_SCHEMA, PREFIX_ACCUMULATOR_SCHEMA,
    SNAPSHOT_FACTS_SCHEMA, TRIPINFO_PRECISION, WARM_OUTPUT_PRECISION,
    aggregate_split_time_loss, snapshot_digest, validate_prefix_accumulator,
    validate_snapshot_facts)
from traffic_sim.simulation.warm_state_cache import (
    WarmStateIdentity, create_warm_state_identity)

OBSERVATION_SCHEMA = "monthly_production_observation_v1"

# A warm point must land on a whole SUMO demand interval so the prefix is a
# complete, meaningful slice of the archive rather than an arbitrary cut.
WARM_ALIGNMENT_S = 900
# Below this there is nothing worth reusing: the saved prefix costs more to
# store and verify than it saves.
MINIMUM_PREFIX_S = 1800

SUPPORTED_WARM_MODES = frozenset({"meso"})

# Fields that describe HOW a result was produced, not WHAT it is. They are
# recorded for evidence but excluded from cross-arm semantic comparison —
# comparing them would make cold-vs-warm equality impossible by construction.
_EXECUTION_ONLY = frozenset({"execution_arm", "warm_point_s", "runtime_s",
                             "peak_rss_bytes", "phase_runtime_s",
                             # Criterion 6: the split's own evidence. It is
                             # content-keyed and reviewable, but it describes
                             # HOW the warm arm reached its numbers, so
                             # comparing it would make cold/warm equality
                             # impossible by construction.
                             "split_diagnostics"})


class WarmStateContractError(Exception):
    """Raised when a warm contract input is malformed. Never raised to skip."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def observation_digest(payload: Mapping[str, Any]) -> str:
    """Digest of the SEMANTIC payload only, so arms are comparable."""
    return hashlib.sha256(_canonical(semantic_payload(payload))).hexdigest()


def semantic_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The part of an observation two arms must agree on exactly."""
    if not isinstance(payload, Mapping):
        raise WarmStateContractError("observation must be a mapping")
    semantic = {
        key: value for key, value in payload.items()
        if key not in _EXECUTION_ONLY
    }
    # Routing provenance remains semantic: the transformed route, routing
    # policy, trip identity, denial count and reroute count must agree across
    # arms. The raw access-impact artifact deliberately records its arm, so
    # that raw digest differs. Its required semantic companion digest retains
    # every report fact except the arm label and remains compared below.
    provenance = semantic.get("provenance")
    if isinstance(provenance, Mapping):
        normalized_provenance = dict(provenance)
        routing = normalized_provenance.get("routing_provenance")
        if isinstance(routing, Mapping):
            normalized_routing = dict(routing)
            normalized_routing.pop("execution_arm", None)
            normalized_routing.pop("access_impact_sha256", None)
            normalized_provenance["routing_provenance"] = normalized_routing
        semantic["provenance"] = normalized_provenance
    return semantic


def _metrics_dict(metrics: Any, label: str) -> dict[str, Any]:
    """Accept a production dataclass or an already-canonical mapping."""
    if isinstance(metrics, Mapping):
        record = dict(metrics)
    else:
        import dataclasses
        if not dataclasses.is_dataclass(metrics):
            raise WarmStateContractError(
                f"{label} must be a production metrics dataclass or mapping")
        record = dataclasses.asdict(metrics)
    for required in ("total_time_loss_s", "trip_count", "unfinished_trips",
                     "unfinished_waiting_trips", "teleport_total", "loaded",
                     "inserted", "running_at_end", "waiting_at_end",
                     "truncated_unreachable", "dropped_unreachable"):
        if required not in record:
            raise WarmStateContractError(
                f"{label} is missing production field {required!r}; a reduced "
                f"metrics object is not an acceptable substitute")
    return record


def _buckets_list(buckets: Any, label: str) -> list[dict[str, Any]]:
    if buckets is None:
        raise WarmStateContractError(f"{label} must be present, not None")
    import dataclasses
    out = []
    for bucket in buckets:
        out.append(dict(bucket) if isinstance(bucket, Mapping)
                   else dataclasses.asdict(bucket))
    return out


SPLIT_DIAGNOSTIC_FIELDS = frozenset({
    "route_audit", "selected_warm_point_s", "prefix_completed_trips",
    "prefix_counters", "prefix_teleport_reasons", "prefix_queue_maximum",
    "raw_post_metrics", "reconstructed_metrics",
    # BOUNDED snapshot evidence. v3 published one entry per airborne vehicle,
    # which made the canonical payload grow with traffic; these carry the
    # snapshot's own facts plus the aggregation totals, which is all a reviewer
    # needs to re-derive the objective.
    "snapshot_facts", "prefix_aggregation", "corrected_post_metrics",
    "restore_correction",
})

AGGREGATION_FIELDS = frozenset({
    "total_time_loss_s", "trip_count", "completed_prefix_trips",
    "resumed_trips", "boundary_offset_applied", "tripinfo_precision",
})

RESTORE_CORRECTION_FIELDS = frozenset({
    "schema", "active_count", "resumed_count", "corrected_count",
    "prefix_accumulator_total_s",
    "raw_total_time_loss_s", "corrected_total_time_loss_s",
    "combined_total_time_loss_s",
    "correction_delta_s", "trip_count", "tripinfo_precision",
    "prefix_accumulator_digest", "records_digest",
})


def validate_restore_correction_summary(summary, *, raw_post=None,
                                        corrected_post=None) -> dict:
    """Validate the bounded projection of the per-vehicle correction."""
    if not isinstance(summary, Mapping) or set(summary) != RESTORE_CORRECTION_FIELDS:
        raise WarmStateContractError("restore correction summary field set is wrong")
    if summary["schema"] != MESO_RECONCILIATION_SCHEMA:
        raise WarmStateContractError("restore correction summary schema is wrong")
    for field in ("active_count", "resumed_count", "corrected_count",
                  "trip_count", "tripinfo_precision"):
        _nonnegative_int(summary[field], f"restore correction {field}")
    if summary["tripinfo_precision"] != TRIPINFO_PRECISION:
        raise WarmStateContractError(
            "restore correction precision does not match production tripinfo")
    for field in ("prefix_accumulator_total_s", "raw_total_time_loss_s",
                  "corrected_total_time_loss_s", "combined_total_time_loss_s"):
        _finite_number(summary[field], f"restore correction {field}")
    if isinstance(summary["correction_delta_s"], bool) or not isinstance(
            summary["correction_delta_s"], (int, float)) or not math.isfinite(
                float(summary["correction_delta_s"])):
        raise WarmStateContractError(
            "restore correction correction_delta_s must be finite")
    for field in ("prefix_accumulator_digest", "records_digest"):
        value = summary[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise WarmStateContractError(
                f"restore correction {field} must be a SHA-256 digest")
    if summary["correction_delta_s"] != (
            summary["corrected_total_time_loss_s"]
            - summary["raw_total_time_loss_s"]):
        raise WarmStateContractError(
            "restore correction delta does not recompute from its totals")
    if raw_post is not None and corrected_post is not None:
        if summary["raw_total_time_loss_s"] != raw_post["total_time_loss_s"] \
                or summary["corrected_total_time_loss_s"] != \
                corrected_post["total_time_loss_s"]:
            raise WarmStateContractError(
                "restore correction totals do not match the post metrics")
        differing = sorted(
            field for field in set(raw_post) | set(corrected_post)
            if field != "total_time_loss_s"
            and raw_post.get(field) != corrected_post.get(field))
        if differing:
            raise WarmStateContractError(
                f"restore correction altered non-objective fields: {differing}")
        if summary["resumed_count"] != corrected_post["trip_count"] \
                or summary["trip_count"] != corrected_post["trip_count"] \
                or summary["corrected_count"] != summary["active_count"]:
            raise WarmStateContractError(
                "restore correction identity counts do not cover tripinfo")
    return dict(summary)


def validate_split_diagnostics(diagnostics, warm_point_s,
                               candidate_metrics=None) -> dict:
    """Criterion 6 evidence must be COMPLETE and agree with the split.

    An empty object satisfied "not None" while proving nothing, and a
    `selected_warm_point_s` disagreeing with the observation's own warm point
    would describe a different split than the one that produced the numbers.
    """
    if not isinstance(diagnostics, Mapping):
        raise WarmStateContractError(
            "a warm observation must record its split diagnostics")
    if set(diagnostics) != SPLIT_DIAGNOSTIC_FIELDS:
        missing = sorted(SPLIT_DIAGNOSTIC_FIELDS - set(diagnostics))
        extra = sorted(set(diagnostics) - SPLIT_DIAGNOSTIC_FIELDS)
        raise WarmStateContractError(
            f"split diagnostics field set is wrong: missing={missing} "
            f"extra={extra}")
    selected = diagnostics["selected_warm_point_s"]
    _positive_int(selected, "split diagnostics selected_warm_point_s")
    if selected != warm_point_s:
        raise WarmStateContractError(
            f"split diagnostics describe warm point {selected}, but the "
            f"observation was produced at {warm_point_s}")
    validate_route_audit(diagnostics["route_audit"])

    queue = diagnostics["prefix_queue_maximum"]
    if queue is not None:
        _nonnegative_int(queue, "split diagnostics prefix_queue_maximum")

    # REUSE the production validators rather than re-deriving a partial copy of
    # them. Naming the fields exactly but typing only one value left strings,
    # negatives and impossible counter relationships passing everywhere else;
    # and a second registry would drift from the first the moment either moved.
    #
    # Building the prefix evidence from the diagnostic sections runs the whole
    # prefix contract over them — types, exact field sets, `inserted <= loaded`,
    # `trip_count <= inserted`, teleport reasons within their total.
    validate_snapshot_evidence(diagnostics["snapshot_facts"],
                               warm_point_s=selected)
    try:
        validate_prefix_sections(
            completed_trips=dict(diagnostics["prefix_completed_trips"])
            if isinstance(diagnostics["prefix_completed_trips"], Mapping)
            else diagnostics["prefix_completed_trips"],
            queue_maximum=queue,
            counters={
                **(dict(diagnostics["prefix_counters"])
                   if isinstance(diagnostics["prefix_counters"], Mapping)
                   else {"invalid": diagnostics["prefix_counters"]}),
                "teleport_reasons": diagnostics["prefix_teleport_reasons"],
            },
            warm_point_s=selected)
    except WarmStateContractError as error:
        raise WarmStateContractError(f"split diagnostics prefix: {error}")
    synthesized = {
        "counters": {
            **{k: v for k, v in diagnostics["prefix_counters"].items()},
        },
        "teleport_reasons": dict(diagnostics["prefix_teleport_reasons"]),
        "queue_maximum": queue,
    }

    # The raw SUMO metrics and the selectively corrected metrics are both
    # retained. Only total_time_loss_s may differ.
    try:
        raw_post = validate_post_warm_metrics(diagnostics["raw_post_metrics"])
    except WarmStateContractError as error:
        raise WarmStateContractError(f"split diagnostics raw_post_metrics: {error}")

    try:
        corrected_post = validate_post_warm_metrics(
            diagnostics["corrected_post_metrics"])
        validate_restore_correction_summary(
            diagnostics["restore_correction"], raw_post=raw_post,
            corrected_post=corrected_post)
    except WarmStateContractError as error:
        raise WarmStateContractError(f"split diagnostics correction: {error}")

    # The summary is checked only once BOTH segment inputs are typed, because it
    # is a claim about them: it is RECOMPUTED from the recorded prefix and
    # post-warm totals, not merely checked for shape. Without this a summary
    # claiming 123.0 was accepted while the inputs summed to 999.0.
    summary = validate_aggregation_summary(
        diagnostics["prefix_aggregation"],
        prefix_completed=diagnostics["prefix_completed_trips"],
        raw_post=corrected_post,
        continued_total=diagnostics["restore_correction"][
            "combined_total_time_loss_s"])

    try:
        recorded = validate_post_warm_metrics(diagnostics["reconstructed_metrics"])
    except WarmStateContractError as error:
        raise WarmStateContractError(
            f"split diagnostics reconstructed_metrics: {error}")

    # CROSS-SECTION CONSISTENCY. A fully valid but DIFFERENT reconstructed
    # object would otherwise be accepted, which is the one thing these
    # diagnostics are supposed to make checkable: they must reproduce the
    # reconstruction they claim to describe.
    expected = _reconstruct_from_sections(synthesized, corrected_post,
                                          aggregation=summary)
    if recorded != expected:
        differing = sorted(k for k in set(expected) | set(recorded)
                           if expected.get(k) != recorded.get(k))
        raise WarmStateContractError(
            f"split diagnostics reconstructed_metrics does not follow from the "
            f"recorded prefix and post-warm inputs; differs on {differing}")

    if candidate_metrics is not None:
        candidate = _metrics_dict(candidate_metrics, "candidate_metrics")
        if recorded != candidate:
            differing = sorted(k for k in set(candidate) | set(recorded)
                               if candidate.get(k) != recorded.get(k))
            raise WarmStateContractError(
                f"split diagnostics describe metrics the observation does not "
                f"report; differs on {differing}")
    return dict(diagnostics)


def build_monthly_observation(
    *,
    schedule_id: str,
    variant: str,
    seed: int,
    simulation_mode: str,
    baseline_metrics: Any,
    candidate_metrics: Any,
    baseline_buckets: Any,
    candidate_buckets: Any,
    feasibility: Mapping[str, Any],
    recovery: Any,
    failures: Sequence[str],
    matched_baseline_id: str,
    provenance_key: str,
    provenance: Mapping[str, Any],
    execution_arm: str = "cold",
    warm_point_s: int | None = None,
    split_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble THE canonical monthly production observation.

    Every field a monthly decision depends on is included: paired objective
    inputs, both per-seed decision metric sets, hard failures, health/end-state,
    recovery result and buckets, truncation/drop counts, matched-baseline
    identity and full provenance. Both execution arms must call this.
    """
    if execution_arm not in {"cold", "warm"}:
        raise WarmStateContractError("execution_arm must be 'cold' or 'warm'")
    if execution_arm == "warm" and not isinstance(warm_point_s, int):
        raise WarmStateContractError("a warm observation must record its warm point")
    if execution_arm == "cold" and warm_point_s is not None:
        raise WarmStateContractError("a cold observation has no warm point")
    if execution_arm == "cold" and split_diagnostics is not None:
        raise WarmStateContractError("a cold observation has no split to diagnose")

    if not isinstance(feasibility, Mapping) or "hard_failures" not in feasibility:
        raise WarmStateContractError("feasibility must be the production dict")
    if recovery is None:
        raise WarmStateContractError("recovery result must be present")

    baseline = _metrics_dict(baseline_metrics, "baseline_metrics")
    candidate = _metrics_dict(candidate_metrics, "candidate_metrics")
    recovery_record = (dict(recovery) if isinstance(recovery, Mapping)
                       else recovery.to_dict())

    payload = {
        "schema": OBSERVATION_SCHEMA,
        "schedule_id": str(schedule_id),
        "demand_variant": str(variant),
        "seed": int(seed),
        "simulation_mode": str(simulation_mode),
        # paired objective inputs — the ranking score
        "baseline_time_loss_s": float(baseline["total_time_loss_s"]),
        "candidate_time_loss_s": float(candidate["total_time_loss_s"]),
        # full per-seed decision metrics for both sides
        "baseline_metrics": baseline,
        "candidate_metrics": candidate,
        # gates and health
        "hard_failures": sorted(str(f) for f in failures),
        "feasibility": dict(feasibility),
        "health": {
            side: {
                "running_at_end": record["running_at_end"],
                "waiting_at_end": record["waiting_at_end"],
                "unfinished_trips": record["unfinished_trips"],
                "teleport_total": record["teleport_total"],
                "teleport_reasons": dict(record.get("teleport_reasons") or {}),
                "loaded": record["loaded"],
                "inserted": record["inserted"],
            }
            for side, record in (("baseline", baseline), ("candidate", candidate))
        },
        "truncation": {
            side: {
                "truncated_unreachable": record["truncated_unreachable"],
                "dropped_unreachable": record["dropped_unreachable"],
            }
            for side, record in (("baseline", baseline), ("candidate", candidate))
        },
        # recovery result AND the buckets it was computed from
        "recovery": recovery_record,
        "recovery_buckets": {
            "baseline": _buckets_list(baseline_buckets, "baseline_buckets"),
            "candidate": _buckets_list(candidate_buckets, "candidate_buckets"),
        },
        "matched_baseline_id": str(matched_baseline_id),
        "provenance_key": str(provenance_key),
        "provenance": dict(provenance),
        # execution-only, never compared across arms
        "execution_arm": execution_arm,
        "warm_point_s": warm_point_s,
    }
    if execution_arm == "warm":
        # Warm-only. Adding `split_diagnostics: None` to every cold observation
        # would change the cold canonical structure, which criterion 9 requires
        # to stay byte/structurally unchanged.
        payload["split_diagnostics"] = validate_split_diagnostics(
            split_diagnostics, warm_point_s, candidate)
    _canonical(payload)          # reject NaN/inf now, not at comparison time
    return payload


@dataclass(frozen=True)
class WarmDecision:
    """Deterministic warm/cold decision. `reason` is always recorded."""

    eligible: bool
    reason: str
    warm_point_s: int | None = None

    def __post_init__(self) -> None:
        if self.eligible and not isinstance(self.warm_point_s, int):
            raise WarmStateContractError("an eligible decision needs a warm point")
        if not self.eligible and self.warm_point_s is not None:
            raise WarmStateContractError("an ineligible decision has no warm point")


def evaluate_warm_eligibility(
    *,
    closures: Sequence[Mapping[str, Any]] | None,
    scenario_start_s: int,
    simulation_mode: str,
    duration_s: int,
    alignment_s: int = WARM_ALIGNMENT_S,
    minimum_prefix_s: int = MINIMUM_PREFIX_S,
) -> WarmDecision:
    """Decide warm vs cold. Every unusual shape stays COLD.

    Warming is only sound when the whole simulation prefix before the closure
    is identical to the baseline run: no closure may begin at or before the
    warm point, and the run must start at the archive epoch.
    """
    if simulation_mode not in SUPPORTED_WARM_MODES:
        return WarmDecision(False, "unsupported_mode")
    if not closures:
        return WarmDecision(False, "baseline_only")
    if scenario_start_s != 0:
        return WarmDecision(False, "ambiguous_envelope_start")
    if alignment_s <= 0 or minimum_prefix_s <= 0:
        return WarmDecision(False, "invalid_warm_policy")

    begins = []
    for closure in closures:
        begin = closure.get("begin_s")
        end = closure.get("end_s")
        if not isinstance(begin, int) or isinstance(begin, bool):
            return WarmDecision(False, "unaligned_or_invalid_closure")
        if not isinstance(end, int) or isinstance(end, bool) or end <= begin:
            return WarmDecision(False, "unaligned_or_invalid_closure")
        if begin < 0 or end > duration_s:
            return WarmDecision(False, "closure_outside_archive")
        begins.append(begin)

    earliest = min(begins)
    if earliest <= 0:
        # Whole-window / offset-zero closure: there is no shared prefix at all.
        return WarmDecision(False, "whole_window_or_offset_zero")

    warm_point = ((earliest - 1) // alignment_s) * alignment_s
    if warm_point <= 0:
        return WarmDecision(False, "no_positive_aligned_warm_point")
    if warm_point < minimum_prefix_s:
        return WarmDecision(False, "prefix_shorter_than_required")
    if not earliest > warm_point:
        return WarmDecision(False, "warm_point_not_strictly_before_closure")
    return WarmDecision(True, "eligible", warm_point)


def validate_snapshot_binding(binding) -> dict:
    """The snapshot contract bound into a warm identity, fully checked.

    The state-serialization settings are part of the IDENTITY because they decide
    what the state on disk actually is. v3 recorded them and never applied them,
    so the key described a fidelity the snapshot did not have.
    """
    allowed = {"snapshot_schema", "save_state_rng", "save_state_precision"}
    if not isinstance(binding, Mapping) or set(binding) != allowed:
        raise WarmStateContractError(
            f"snapshot binding must be exactly {sorted(allowed)}, got "
            f"{sorted(set(binding)) if isinstance(binding, Mapping) else binding!r}")
    if binding["snapshot_schema"] != SNAPSHOT_FACTS_SCHEMA:
        raise WarmStateContractError(
            f"snapshot binding names schema {binding['snapshot_schema']!r}, not "
            f"{SNAPSHOT_FACTS_SCHEMA!r}")
    if binding["save_state_rng"] is not True:
        raise WarmStateContractError(
            "the snapshot binding must record that the RNG is saved")
    precision = binding["save_state_precision"]
    if isinstance(precision, bool) or not isinstance(precision, int) \
            or precision <= 0:
        raise WarmStateContractError(
            "snapshot binding save_state_precision must be a positive integer")
    return dict(binding)


def monthly_warm_identity(
    *,
    demand_build_id: str,
    demand_variant: str,
    seed: int,
    simulation_mode: str,
    warm_point_s: int,
    network_path: Path,
    unfiltered_route_path: Path,
    source_files: Mapping[str, Path],
    sumo_home: Path,
    archive_dir: Path | None = None,
    expected_variant_filename: str | None = None,
    route_audit: Mapping[str, Any] | None = None,
    audit_record_path: Path | None = None,
    snapshot_binding: Mapping[str, Any] | None = None,
    snapshot_record_path: Path | None = None,
    baseline_additional_files: Mapping[str, Path] | None = None,
) -> WarmStateIdentity:
    """Bind a warm state to the UNFILTERED baseline inputs.

    `unfiltered_route_path` must be the archive's own variant route. Passing a
    closure-filtered route here would let a state warmed under one closure be
    restored under another — the identity would look valid while describing a
    different simulation.
    """
    route = Path(unfiltered_route_path)
    # STRUCTURAL binding, not a filename heuristic. A name check would pass the
    # same filtered bytes renamed; the route must BE the archive's own variant
    # file, by resolved path and by content digest.
    if archive_dir is None or expected_variant_filename is None:
        raise WarmStateContractError(
            "warm identity requires the archive directory and the exact "
            "variant filename it must equal")
    expected = (Path(archive_dir) / expected_variant_filename)
    if not expected.is_file():
        raise WarmStateContractError(f"archive variant is missing: {expected}")
    if route.resolve() != expected.resolve():
        raise WarmStateContractError(
            f"warm identity requires the archive's own {expected_variant_filename}, "
            f"got {route}")
    if _sha256_file(route) != _sha256_file(expected):
        raise WarmStateContractError("archive variant bytes changed during binding")
    sources = dict(source_files)
    if route_audit is not None:
        # The audit is part of the identity: a state is reusable only for a
        # candidate whose filtering produced the SAME mutation from the same
        # route bytes. A stale audit must MISS, never be repaired.
        #
        # It is bound by CONTENT. `create_warm_state_identity` fingerprints a
        # file's bytes and records only {sha256, bytes}, never the path, so a
        # caller-supplied file holding the canonical audit gives a stable,
        # reproducible identity contribution.
        route_audit = validate_route_audit(route_audit)
        if audit_record_path is None:
            raise WarmStateContractError(
                "binding a route audit requires audit_record_path")
        record = Path(audit_record_path)
        record.write_bytes(_canonical(dict(sorted(route_audit.items()))))
        sources = {**sources, "route_audit": record}
    if snapshot_binding is not None:
        # Bound by CONTENT, exactly like the route audit: the state settings and
        # the snapshot schema decide how a cached prefix may be reused, so a
        # change to either must invalidate cached prefixes rather than silently
        # alter what a restored state means.
        snapshot_binding = validate_snapshot_binding(snapshot_binding)
        if snapshot_record_path is None:
            raise WarmStateContractError(
                "binding snapshot settings requires snapshot_record_path")
        record = Path(snapshot_record_path)
        record.write_bytes(_canonical(dict(sorted(snapshot_binding.items()))))
        sources = {**sources, "snapshot_binding": record}
    return create_warm_state_identity(
        demand_build_id=demand_build_id,
        demand_variant=demand_variant,
        seed=seed,
        simulation_mode=simulation_mode,
        warmup_end_s=warm_point_s,
        network_path=Path(network_path),
        route_path=route,
        # `sources`, NOT `source_files`: the audit-augmented map is the point.
        # Passing the original silently dropped the route audit from the
        # identity, so filtered-route drift would not have changed the key.
        source_files=sources,
        sumo_home=Path(sumo_home),
        baseline_additional_files=baseline_additional_files,
    )


# ── Real warm execution: plan, prefix accounting, isolation ──────────────────
# Fields that are additive across the prefix and post-warm segments. Anything
# not listed here cannot be combined arithmetically and must be taken from the
# segment that actually observed it — never invented.
# NOTE: the earlier `_ADDITIVE_FIELDS`/`_END_STATE_FIELDS` tuples were a SECOND,
# hand-maintained registry that could drift from FIELD_RULES without anything
# noticing. FIELD_RULES below is the single source of truth.


@dataclass(frozen=True)
class WarmExecutionPlan:
    """Exactly how a warm run is bootstrapped and resumed.

    `bootstrap_args` runs the UNFILTERED baseline to the warm point and saves a
    state; `post_warm_args` loads that state and applies the closure. Both are
    explicit so a reviewer can see that the warm arm differs from the cold arm
    only after the warm point.
    """

    warm_point_s: int
    state_path: Path
    unfiltered_route: Path
    filtered_route: Path
    closure_additional: Path
    bootstrap_args: tuple[str, ...]
    post_warm_args: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "warm_point_s": self.warm_point_s,
            "state_path": str(self.state_path),
            "unfiltered_route": str(self.unfiltered_route),
            "filtered_route": str(self.filtered_route),
            "closure_additional": str(self.closure_additional),
            "bootstrap_args": list(self.bootstrap_args),
            "post_warm_args": list(self.post_warm_args),
        }


def plan_warm_execution(
    *,
    warm_point_s: int,
    state_path: Path,
    unfiltered_route: Path,
    filtered_route: Path,
    closure_additional: Path,
) -> WarmExecutionPlan:
    """Build the two-phase plan. The bootstrap is candidate-FREE by construction.

    The bootstrap carries no closure additional and the unfiltered route, so a
    state can never encode the candidate it was later used for — that is what
    makes one state reusable across candidates sharing a prefix.
    """
    from traffic_sim.simulation.warm_state_cache import (
        load_state_arguments, save_state_arguments)

    if not isinstance(warm_point_s, int) or isinstance(warm_point_s, bool):
        raise WarmStateContractError("warm_point_s must be an integer")
    if warm_point_s <= 0:
        raise WarmStateContractError("warm_point_s must be positive")
    if Path(unfiltered_route).resolve() == Path(filtered_route).resolve():
        raise WarmStateContractError(
            "the closure arm must use the filtered route, not the baseline one")
    bootstrap = tuple(save_state_arguments(Path(state_path),
                                           warmup_end_s=warm_point_s))
    return WarmExecutionPlan(
        warm_point_s=warm_point_s,
        state_path=Path(state_path),
        unfiltered_route=Path(unfiltered_route),
        filtered_route=Path(filtered_route),
        closure_additional=Path(closure_additional),
        bootstrap_args=bootstrap,
        # load_state_arguments requires the file to exist, so it is deferred to
        # execution time; the plan records the intent.
        post_warm_args=("--load-state", str(Path(state_path).resolve()),
                        "--begin", str(warm_point_s)),
    )


# v2 carries the boundary-active ledger. v1 aggregates could not express WHICH
# vehicles were airborne at the snapshot, which is the information the executed
# LUNA-WARM-07 campaign proved is required: its objective was low by 7.73 s /
# 80.62 s / 138.97 s, scaling with the number of vehicles in flight.
PREFIX_EVIDENCE_SCHEMA = "monthly_prefix_evidence_v7"
# BOTH earlier schemas are refused, not upgraded. v1 predates boundary
# accounting entirely; v2 carries a per-vehicle ledger whose offset the approved
# LUNA-WARM-08 diagnostic showed double counts. Reinterpreting either would mean
# guessing which accounting produced numbers we did not compute.
LEGACY_PREFIX_EVIDENCE_SCHEMAS = ("monthly_prefix_evidence_v6",
                                  "monthly_prefix_evidence_v5",
                                  "monthly_prefix_evidence_v4",
                                  "monthly_prefix_evidence_v3",
                                  "monthly_prefix_evidence_v1",
                                  "monthly_prefix_evidence_v2")

# HOW each production metric field crosses the warm boundary. Bound
# mechanically to DisruptionMetrics below, so a new production field without a
# rule is a hard error rather than a silently dropped value.
#   sum_disjoint  — prefix and post-warm measure disjoint populations; add them
#   post_final    — describes the END of the whole run; the prefix's value is
#                   an intermediate state and must not leak into it
#   post_candidate— produced only by the candidate (closure-filtered) route,
#                   which does not exist before the warm point
#   max_measured  — a maximum over the run: take the largest MEASURED segment
#   merge_counts  — per-key counters, summed key-wise
#   post_closure  — closure-scoped; the prefix precedes the closure entirely
#   post_cumulative — SUMO's statistics counters continue accumulating across a
#                   loaded state, so the post-warm value ALREADY includes the
#                   prefix. Summing double-counts every vehicle live at the
#                   snapshot; LUNA-WARM-05 measured exactly that as loaded
#                   +1081 / inserted +1065 against the cold arm.
#   post_cumulative_counts — the same, per key.
FIELD_RULES = {
    "total_time_loss_s": "sum_disjoint",
    "trip_count": "sum_disjoint",
    "loaded": "post_cumulative",
    "inserted": "post_cumulative",
    "teleport_total": "post_cumulative",
    "teleport_reasons": "post_cumulative_counts",
    "unfinished_trips": "post_final",
    "unfinished_waiting_trips": "post_final",
    "running_at_end": "post_final",
    "waiting_at_end": "post_final",
    "truncated_unreachable": "post_candidate",
    "dropped_unreachable": "post_candidate",
    "max_queue_vehicles": "max_measured",
    "closed_edge_throughput": "post_closure",
}


def production_metric_fields() -> tuple[str, ...]:
    import dataclasses
    from traffic_sim.simulation.metrics import DisruptionMetrics
    return tuple(f.name for f in dataclasses.fields(DisruptionMetrics))


VALID_RULES = frozenset({
    "sum_disjoint", "post_cumulative", "post_cumulative_counts", "post_final",
    "post_candidate", "max_measured", "post_closure",
})


def verify_field_partition() -> None:
    """Every production field has exactly one rule from a CLOSED vocabulary.

    Checking only that each field HAS a rule would let a typo'd or invented
    label through: the freeze would succeed and reconstruction would raise
    later, encoding undefined semantics into a frozen campaign key.
    """
    unknown = sorted({rule for rule in FIELD_RULES.values()} - VALID_RULES)
    if unknown:
        raise WarmStateContractError(
            f"unknown warm-boundary rule label(s) {unknown}; the vocabulary is "
            f"closed to {sorted(VALID_RULES)}")
    fields = set(production_metric_fields())
    ruled = set(FIELD_RULES)
    missing = sorted(fields - ruled)
    orphaned = sorted(ruled - fields)
    if missing:
        raise WarmStateContractError(
            f"production metric field(s) have no warm-boundary rule: {missing}. "
            f"Adding a field without deciding how it crosses the warm point "
            f"would silently drop or double-count it.")
    if orphaned:
        raise WarmStateContractError(
            f"warm-boundary rule(s) name no production field: {orphaned}")


def validate_prefix_sections(*, completed_trips, queue_maximum, counters,
                             warm_point_s) -> dict:
    """Type and relationship rules over the prefix AGGREGATE sections.

    Shared by `build_prefix_evidence` and `validate_split_diagnostics` so the
    two cannot drift. It deliberately covers only the aggregate sections:
    split diagnostics publish bounded ledger FACTS (a count and a digest), not
    the per-vehicle map, so a validator that demanded the map could only be
    satisfied by fabricating one.
    """
    _positive_int(warm_point_s, "prefix warm_point_s")

    if not isinstance(completed_trips, Mapping) or set(completed_trips) != {
            "total_time_loss_s", "trip_count"}:
        raise WarmStateContractError(
            "prefix completed_trips must be exactly "
            "{'total_time_loss_s', 'trip_count'}")
    _finite_number(completed_trips["total_time_loss_s"],
                   "prefix total_time_loss_s")
    _nonnegative_int(completed_trips["trip_count"], "prefix trip_count")

    if not isinstance(counters, Mapping) or set(counters) != {
            "loaded", "inserted", "teleport_total", "teleport_reasons"}:
        raise WarmStateContractError(
            "prefix counters must be exactly {'loaded', 'inserted', "
            "'teleport_total', 'teleport_reasons'}")
    for name in ("loaded", "inserted", "teleport_total"):
        _nonnegative_int(counters[name], f"prefix counter {name!r}")
    _check_counter_relationships(
        {"loaded": counters["loaded"], "inserted": counters["inserted"],
         "trip_count": completed_trips["trip_count"]}, "prefix",
        trips_bounded_by_inserted=True)

    reasons = counters["teleport_reasons"]
    if reasons is None:
        reasons = {}
    if not isinstance(reasons, Mapping):
        raise WarmStateContractError("prefix teleport_reasons must be an object")
    for key, value in sorted(reasons.items(), key=lambda item: str(item[0])):
        if not isinstance(key, str) or not key:
            raise WarmStateContractError(
                f"prefix teleport reason key {key!r} must be a non-empty string")
        _nonnegative_int(value, f"prefix teleport reason {key!r}")
    reason_total = sum(int(v) for v in reasons.values())
    if reason_total > counters["teleport_total"]:
        raise WarmStateContractError(
            f"prefix teleport reasons sum to {reason_total}, exceeding "
            f"teleport_total {counters['teleport_total']}")

    if queue_maximum is not None:
        _nonnegative_int(queue_maximum, "prefix queue_maximum")
    # Returned NORMALISED (absent teleport reasons become an empty object) so
    # callers store exactly what was validated rather than re-deriving it.
    return dict(reasons)


def build_prefix_evidence(*, completed_trips, queue_maximum, counters,
                          recovery_buckets, warm_point_s,
                          snapshot_facts=None, active_accumulator=None,
                          completed_records=None,
                          completed_record_order=None) -> dict:
    """Versioned, field-complete evidence about the pre-warm segment.

    A bare `DisruptionMetrics` cannot serve here: its `unfinished_trips` and
    end-state fields describe a run that ENDED, while the prefix merely paused.
    Storing one conflated a boundary-active vehicle with a finished observation.
    """
    # VALIDATE THE RAW INPUTS FIRST. Coercing with float()/int()/str() and then
    # self-validating launders wrong types into valid-looking evidence:
    # `int(True)` is 1, `int("1")` is 1, `str(5)` is "5". The stored bytes would
    # then pass every later check while describing something that was never
    # measured.
    reasons = validate_prefix_sections(
        completed_trips=completed_trips, queue_maximum=queue_maximum,
        counters=counters, warm_point_s=warm_point_s)

    # SUMO's mesoscopic tripinfo uses a private accumulator which its state
    # serializer omits. The documented TraCI value did not reproduce that
    # accumulator exactly through the frozen 1.27 save/load boundary. Therefore
    # the exact prefix accumulator comes from
    # high-precision unfinished tripinfo written by the state-owning process.
    if snapshot_facts is None:
        raise WarmStateContractError(
            "prefix evidence requires the snapshot facts; a state whose "
            "instant and serialization settings are unrecorded cannot be "
            "matched against the identity that claims them")
    facts = validate_snapshot_evidence(snapshot_facts,
                                       warm_point_s=warm_point_s)

    if active_accumulator is None:
        raise WarmStateContractError(
            "prefix evidence requires the meso prefix accumulator")
    try:
        ledger = validate_prefix_accumulator(
            active_accumulator, expected_warm_point_s=warm_point_s)
    except Exception as error:
        raise WarmStateContractError(f"prefix accumulator: {error}")
    if ledger["entries"] and \
            len(ledger["entries"]) != facts["active_vehicle_count"]:
        raise WarmStateContractError(
            "prefix accumulator count does not match snapshot active count")
    if not ledger["entries"] and facts["active_vehicle_count"] != 0:
        raise WarmStateContractError(
            "prefix accumulator is empty but snapshot records active vehicles")
    if not isinstance(completed_records, Mapping):
        raise WarmStateContractError("prefix completed records must be an object")
    canonical_completed = dict(sorted(completed_records.items()))
    for identifier, value in canonical_completed.items():
        if not isinstance(identifier, str) or not identifier:
            raise WarmStateContractError("prefix completed vehicle id is invalid")
        numeric = _finite_number(value, "prefix completed vehicle time loss")
        if numeric != round(numeric, TRIPINFO_PRECISION):
            raise WarmStateContractError(
                "prefix completed records must be at production precision")
    if set(canonical_completed) & set(ledger["entries"]):
        raise WarmStateContractError(
            "a vehicle cannot be both completed and active at the boundary")
    # Older process-free fixture callers predate this field; their canonical
    # map order is a valid explicit order. Production always supplies SUMO's
    # XML order from `build_prefix_accumulator` and a regression binds that
    # call site, so the compatibility fallback cannot affect live warming.
    record_order = (list(canonical_completed) if completed_record_order is None
                    else list(completed_record_order)
                    if isinstance(completed_record_order, list) else None)
    if record_order is None or len(record_order) != len(set(record_order)) \
            or any(not isinstance(identifier, str) or not identifier
                   for identifier in record_order) \
            or set(record_order) != set(canonical_completed):
        raise WarmStateContractError(
            "prefix completed record order does not exactly cover its records")
    if len(canonical_completed) != completed_trips["trip_count"] \
            or sum(canonical_completed[identifier]
                   for identifier in record_order) != \
            float(completed_trips["total_time_loss_s"]):
        raise WarmStateContractError(
            "prefix completed aggregate does not recompose from its records")

    evidence = {
        "schema": PREFIX_EVIDENCE_SCHEMA,
        "warm_point_s": warm_point_s,
        "snapshot_facts": facts,
        "active_accumulator": ledger,
        "completed_records": canonical_completed,
        "completed_record_order": record_order,
        # COMPLETED trips only: a vehicle still driving at the snapshot is
        # continued by the resumed run and is counted there, exactly once, with
        # its full time loss.
        "completed_trips": {
            "total_time_loss_s": float(completed_trips["total_time_loss_s"]),
            "trip_count": completed_trips["trip_count"],
        },
        "queue_maximum": queue_maximum,
        "counters": {name: counters[name]
                     for name in ("loaded", "inserted", "teleport_total")},
        "teleport_reasons": dict(reasons),
        # `_buckets_list` accepts the production `RecoveryBucket` dataclass and
        # normalises it to a mapping. That is not coercion: the dataclass is
        # already typed at its own boundary. Every resulting mapping is then
        # validated exactly.
        "recovery_buckets": [
            _validate_bucket(bucket, f"prefix bucket {index}")
            for index, bucket in enumerate(
                _buckets_list(recovery_buckets, "prefix recovery buckets"))],
    }
    _canonical(evidence)
    # Self-validate: publication must never be able to persist evidence that
    # restore would then reject.
    return parse_prefix_evidence(evidence, expected_warm_point_s=warm_point_s)


def _validate_bucket(bucket, label):
    """Exactly the production RecoveryBucket shape, fully typed."""
    import dataclasses
    from traffic_sim.simulation.envelope import RecoveryBucket

    expected = {f.name for f in dataclasses.fields(RecoveryBucket)}
    if not isinstance(bucket, Mapping):
        raise WarmStateContractError(f"{label} is not an object")
    if set(bucket) != expected:
        raise WarmStateContractError(
            f"{label} field set is wrong: {sorted(set(bucket))} != "
            f"{sorted(expected)}")
    _nonnegative_int(bucket["begin_s"], f"{label} begin_s")
    _nonnegative_int(bucket["end_s"], f"{label} end_s")
    _finite_number(bucket["time_loss_s"], f"{label} time_loss_s")
    return dict(bucket)


def _check_counter_relationships(values, label, *, trips_bounded_by_inserted=False):
    """Reject count relationships that cannot occur in a real run.

    Individually-valid numbers can still be jointly impossible: more vehicles
    inserted than loaded, or more unfinished trips than trips. Such evidence is
    not merely odd — it means the measurement is wrong, and reconstructing from
    it would produce a confident, invalid result.
    """
    loaded, inserted = values.get("loaded"), values.get("inserted")
    if loaded is not None and inserted is not None and inserted > loaded:
        raise WarmStateContractError(
            f"{label}: inserted {inserted} exceeds loaded {loaded}")
    trips, unfinished = values.get("trip_count"), values.get("unfinished_trips")
    if trips is not None and unfinished is not None and unfinished > trips:
        raise WarmStateContractError(
            f"{label}: unfinished_trips {unfinished} exceeds trip_count {trips}")
    waiting = values.get("unfinished_waiting_trips")
    if unfinished is not None and waiting is not None and waiting > unfinished:
        raise WarmStateContractError(
            f"{label}: unfinished_waiting_trips {waiting} exceeds "
            f"unfinished_trips {unfinished}")
    # PREFIX ONLY. A trip completed within [0, warm] must have been inserted
    # within [0, warm], so completed trips cannot exceed insertions. This does
    # NOT hold post-warm: a vehicle inserted before the snapshot can finish
    # after it, so post-warm trip_count legitimately exceeds post-warm inserted.
    if trips_bounded_by_inserted and trips is not None and inserted is not None \
            and trips > inserted:
        raise WarmStateContractError(
            f"{label}: completed trip_count {trips} exceeds inserted {inserted}")


def _positive_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WarmStateContractError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WarmStateContractError(f"{label} must be a non-negative integer")
    return value


def _finite_number(value, label):
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WarmStateContractError(f"{label} must be a number")
    if not math.isfinite(float(value)) or float(value) < 0:
        raise WarmStateContractError(f"{label} must be finite and non-negative")
    return float(value)


def parse_prefix_evidence(payload, *, expected_warm_point_s=None) -> dict:
    """Fail closed on malformed, partial, stale or unknown-schema evidence.

    Validation is RECURSIVE. Checking only the container field sets would accept
    a string warm point or a negative counter, and those flow straight into the
    reconstructed metrics — a wrong number is far worse than a missing one.
    When `expected_warm_point_s` is given the evidence must match it exactly, so
    a state warmed to a different point cannot be paired with this run.
    """
    if not isinstance(payload, Mapping):
        raise WarmStateContractError("prefix evidence must be an object")
    if payload.get("schema") in LEGACY_PREFIX_EVIDENCE_SCHEMAS:
        raise WarmStateContractError(
            f"legacy prefix-evidence schema {payload.get('schema')!r} cannot "
            f"account for boundary-active vehicles; it is a cache MISS, never "
            f"repaired or reinterpreted")
    if payload.get("schema") != PREFIX_EVIDENCE_SCHEMA:
        raise WarmStateContractError(
            f"unsupported prefix-evidence schema {payload.get('schema')!r}; "
            f"legacy or unknown evidence is never interpreted")
    required = {"schema", "warm_point_s", "snapshot_facts",
                "completed_trips", "queue_maximum", "counters",
                "teleport_reasons", "recovery_buckets",
                "active_accumulator", "completed_records",
                "completed_record_order"}
    if set(payload) != required:
        raise WarmStateContractError(
            f"prefix evidence field set is wrong: {sorted(set(payload))}")

    warm_point = _positive_int(payload["warm_point_s"], "prefix warm_point_s")
    if expected_warm_point_s is not None and warm_point != expected_warm_point_s:
        raise WarmStateContractError(
            f"prefix evidence was warmed to {warm_point}, not the expected "
            f"{expected_warm_point_s}; it describes a different split")

    completed = payload["completed_trips"]
    if not isinstance(completed, Mapping) or set(completed) != {
            "total_time_loss_s", "trip_count"}:
        raise WarmStateContractError("prefix completed_trips is malformed")
    _finite_number(completed["total_time_loss_s"], "prefix total_time_loss_s")
    _nonnegative_int(completed["trip_count"], "prefix trip_count")

    counters = payload["counters"]
    if not isinstance(counters, Mapping) or set(counters) != {
            "loaded", "inserted", "teleport_total"}:
        raise WarmStateContractError("prefix counters are malformed")
    for name, value in sorted(counters.items()):
        _nonnegative_int(value, f"prefix counter {name!r}")

    queue = payload["queue_maximum"]
    if queue is not None:
        _nonnegative_int(queue, "prefix queue_maximum")

    reasons = payload["teleport_reasons"]
    if not isinstance(reasons, Mapping):
        raise WarmStateContractError("prefix teleport_reasons must be an object")
    for name, value in sorted(reasons.items()):
        if not isinstance(name, str) or not name:
            raise WarmStateContractError("prefix teleport reason keys must be names")
        _nonnegative_int(value, f"prefix teleport reason {name!r}")

    # READ side, not just write side: stored bytes are untrusted input. A state
    # whose recorded instant or serialization settings disagree with the
    # identity that claims them must be a cache MISS, never reinterpreted.
    facts = validate_snapshot_evidence(payload["snapshot_facts"],
                                       warm_point_s=warm_point)
    try:
        ledger = validate_prefix_accumulator(
            payload["active_accumulator"], expected_warm_point_s=warm_point)
    except Exception as error:
        raise WarmStateContractError(f"prefix accumulator: {error}")
    if len(ledger["entries"]) != facts["active_vehicle_count"]:
        raise WarmStateContractError(
            "prefix accumulator count does not match snapshot active count")
    completed_records = payload["completed_records"]
    if not isinstance(completed_records, Mapping) \
            or list(completed_records) != sorted(completed_records):
        raise WarmStateContractError(
            "prefix completed records are not a canonical object")
    for identifier, value in completed_records.items():
        if not isinstance(identifier, str) or not identifier:
            raise WarmStateContractError("prefix completed vehicle id is invalid")
        numeric = _finite_number(value, "prefix completed vehicle time loss")
        if numeric != round(numeric, TRIPINFO_PRECISION):
            raise WarmStateContractError(
                "prefix completed records must be at production precision")
    if set(completed_records) & set(ledger["entries"]):
        raise WarmStateContractError(
            "stored prefix completed and active identities overlap")
    record_order = payload["completed_record_order"]
    if not isinstance(record_order, list) \
            or len(record_order) != len(set(record_order)) \
            or any(not isinstance(identifier, str) or not identifier
                   for identifier in record_order) \
            or set(record_order) != set(completed_records):
        raise WarmStateContractError(
            "stored prefix completed record order does not cover its records")
    if len(completed_records) != completed["trip_count"] \
            or sum(completed_records[identifier]
                   for identifier in record_order) != \
            float(completed["total_time_loss_s"]):
        raise WarmStateContractError(
            "stored prefix aggregate does not recompose from its records")

    buckets = payload["recovery_buckets"]
    if not isinstance(buckets, list):
        raise WarmStateContractError("prefix recovery_buckets must be a list")
    for index, bucket in enumerate(buckets):
        _validate_bucket(bucket, f"prefix bucket {index}")

    reason_total = sum(int(count) for count in reasons.values())
    if reason_total > counters["teleport_total"]:
        raise WarmStateContractError(
            f"stored prefix teleport reasons sum to {reason_total}, exceeding "
            f"teleport_total {counters['teleport_total']}")

    # Relationship checks belong HERE, not only in the builder. Restore and
    # reconstruction read stored evidence through this parser, so a
    # digest-valid but impossible payload — written by an older path, or by
    # anything that bypassed the builder — would otherwise be accepted and
    # reconstructed into a confident invalid result. The write-side check
    # cannot protect the read side.
    _check_counter_relationships(
        {"loaded": counters["loaded"], "inserted": counters["inserted"],
         "trip_count": completed["trip_count"]}, "prefix evidence",
        trips_bounded_by_inserted=True)

    return dict(payload)


# Value domains for the post-warm segment, mirroring DisruptionMetrics.
_POST_COUNT_FIELDS = (
    "trip_count", "unfinished_trips", "unfinished_waiting_trips",
    "teleport_total", "loaded", "inserted", "running_at_end", "waiting_at_end",
    "truncated_unreachable", "dropped_unreachable",
)
_POST_OPTIONAL_COUNT_FIELDS = ("max_queue_vehicles", "closed_edge_throughput")


def validate_post_warm_metrics(post_warm) -> dict:
    """Type/value-validate the post-warm segment before any rule is applied.

    `_metrics_dict` only proved the FIELDS were present. A boolean unfinished
    count or a string end-state then flowed straight through `post_final` into
    the reconstructed metrics, because those rules copy rather than compute.
    """
    post = _metrics_dict(post_warm, "post_warm_metrics")
    # EXACT field set. `_metrics_dict` only proved the required fields were
    # present, so an invented field was accepted here and then silently dropped
    # by reconstruction — the caller would believe it had been accounted for.
    expected = set(production_metric_fields())
    if set(post) != expected:
        extra = sorted(set(post) - expected)
        missing = sorted(expected - set(post))
        raise WarmStateContractError(
            f"post_warm_metrics field set is wrong: extra={extra} "
            f"missing={missing}")
    _finite_number(post["total_time_loss_s"], "post-warm total_time_loss_s")
    for field in _POST_COUNT_FIELDS:
        _nonnegative_int(post[field], f"post-warm {field!r}")
    for field in _POST_OPTIONAL_COUNT_FIELDS:
        value = post.get(field)
        if value is not None:
            _nonnegative_int(value, f"post-warm {field!r}")
    reasons = post.get("teleport_reasons")
    if reasons is None:
        reasons = {}
    if not isinstance(reasons, Mapping):
        raise WarmStateContractError("post-warm teleport_reasons must be an object")
    for name, count in sorted(reasons.items(), key=lambda item: str(item[0])):
        if not isinstance(name, str) or not name:
            raise WarmStateContractError(
                "post-warm teleport reason keys must be names")
        _nonnegative_int(count, f"post-warm teleport reason {name!r}")
    reason_total = sum(int(count) for count in reasons.values())
    if reason_total > post["teleport_total"]:
        raise WarmStateContractError(
            f"post-warm teleport reasons sum to {reason_total}, exceeding "
            f"teleport_total {post['teleport_total']}")
    _check_counter_relationships(post, "post-warm")
    return post


def validate_aggregation_summary(summary, *, prefix_completed=None,
                                 raw_post=None, continued_total=None) -> dict:
    """Fail closed on a malformed bounded aggregation summary.

    Under preserved-accumulator accounting the objective is the SUM of two
    segment aggregates, so the summary must show both parts, their total, and
    that no boundary offset was applied. It is validated rather than trusted: an
    unchecked summary would let any objective be asserted.
    """
    if not isinstance(summary, Mapping):
        raise WarmStateContractError("aggregation summary must be an object")
    if set(summary) != AGGREGATION_FIELDS:
        raise WarmStateContractError(
            f"aggregation summary field set is wrong: "
            f"missing={sorted(AGGREGATION_FIELDS - set(summary))} "
            f"extra={sorted(set(summary) - AGGREGATION_FIELDS)}")
    _finite_number(summary["total_time_loss_s"], "aggregation time loss")
    for name in ("trip_count", "completed_prefix_trips", "resumed_trips",
                 "tripinfo_precision"):
        _nonnegative_int(summary[name], f"aggregation {name!r}")
    # The REFUTED rule must be visibly absent, not merely unmentioned.
    if summary["boundary_offset_applied"] is not False:
        raise WarmStateContractError(
            "a boundary offset was applied; the approved LUNA-WARM-08 evidence "
            "shows the resumed tripinfo already carries the accumulator, so an "
            "offset double counts the pre-boundary delay")
    expected = summary["completed_prefix_trips"] + summary["resumed_trips"]
    if summary["trip_count"] != expected:
        raise WarmStateContractError(
            f"aggregation trip_count {summary['trip_count']} does not equal "
            f"completed prefix + resumed ({expected})")

    # RECOMPUTE from the recorded inputs whenever they are available. Checking
    # only types and trip counts left the objective itself unverified: a summary
    # claiming 123.0 was accepted while the recorded prefix and post-warm totals
    # summed to 999.0. A summary is a CLAIM about two numbers we already hold,
    # so it must be re-derived from them rather than trusted.
    if prefix_completed is not None and raw_post is not None:
        recomputed = aggregate_split_time_loss(
            completed_prefix_total=prefix_completed["total_time_loss_s"],
            completed_prefix_trips=prefix_completed["trip_count"],
            resumed_total=raw_post["total_time_loss_s"],
            resumed_trips=raw_post["trip_count"],
            continued_total=continued_total)
        for field in ("total_time_loss_s", "trip_count",
                      "completed_prefix_trips", "resumed_trips"):
            if summary[field] != recomputed[field]:
                raise WarmStateContractError(
                    f"aggregation {field} is {summary[field]!r}, but the "
                    f"recorded prefix and post-warm inputs give "
                    f"{recomputed[field]!r}; the summary does not follow from "
                    f"the evidence it accompanies")
    return dict(summary)


def validate_snapshot_evidence(facts, *, warm_point_s=None) -> dict:
    """The snapshot's own facts, checked through the production validator."""
    try:
        return validate_snapshot_facts(
            facts, expected_warm_point_s=warm_point_s)
    except Exception as error:
        raise WarmStateContractError(f"snapshot facts: {error}")


def _reconstruct_from_sections(sections, post_warm, *, aggregation=None,
                               aggregated=None) -> dict:
    """The field-by-field combiner, over prefix SECTIONS rather than evidence.

    Split out so split diagnostics can be checked against the same rules
    without carrying the per-vehicle maps they deliberately exclude.
    """
    verify_field_partition()
    post = validate_post_warm_metrics(post_warm)
    if aggregated is None and aggregation is not None:
        aggregated = validate_aggregation_summary(aggregation)
    out: dict[str, Any] = {}
    for field in production_metric_fields():
        rule = FIELD_RULES[field]
        if rule == "sum_disjoint":
            if field in ("total_time_loss_s", "trip_count"):
                # The ONE objective rule: completed-prefix aggregate plus
                # resumed aggregate. v3 added a per-vehicle boundary offset
                # here; the approved LUNA-WARM-08 diagnostic showed the resumed
                # tripinfo already carries the accumulator, so that offset
                # double counted.
                if aggregated is None:
                    raise WarmStateContractError(
                        f"the aggregation summary is required for {field!r}")
                out[field] = aggregated[field]
                continue
            left = sections["counters"][field]
            right = post[field]
            if left is None or right is None:
                raise WarmStateContractError(
                    f"cannot sum {field!r}: a segment did not measure it")
            out[field] = left + right
        elif rule == "post_cumulative":
            # The prefix value is a LOWER BOUND, not an addend: the post-state
            # counter already contains it. A post value below it means the
            # restored run lost history and the split is invalid.
            floor = sections["counters"][field]
            value = post[field]
            if value < floor:
                raise WarmStateContractError(
                    f"cumulative {field!r} fell from {floor} (prefix) to "
                    f"{value} (post-warm); the restored state lost history")
            out[field] = value
        elif rule == "post_cumulative_counts":
            merged = dict(post.get(field) or {})
            for key, floor in (sections["teleport_reasons"] or {}).items():
                if merged.get(key, 0) < floor:
                    raise WarmStateContractError(
                        f"cumulative teleport reason {key!r} fell from {floor} "
                        f"(prefix) to {merged.get(key, 0)} (post-warm)")
            out[field] = {str(k): int(v) for k, v in merged.items()}
        elif rule in {"post_final", "post_candidate"}:
            out[field] = post[field]
        elif rule == "max_measured":
            measured = [value for value in (sections["queue_maximum"], post[field])
                        if value is not None]
            out[field] = max(measured) if measured else None
        elif rule == "post_closure":
            # ENFORCED BY THE SCHEMA, not by an optional lookup. The prefix is
            # produced candidate-free and its field set is exact, so a payload
            # carrying any closure-scoped measurement is rejected by
            # `parse_prefix_evidence` before reaching here. Re-asserting it as
            # `evidence.get(...)` would be permanently vacuous, since the key
            # can never be present. This states the invariant the schema keeps.
            if field in sections:
                raise WarmStateContractError(
                    f"prefix evidence carries closure-scoped {field!r}; the "
                    f"prefix must end before the closure begins")
            out[field] = post[field]
        else:                                        # unreachable by partition
            raise WarmStateContractError(f"unknown warm-boundary rule {rule!r}")
    return out


def reconstruct_metrics(prefix_evidence, post_warm, *,
                        expected_warm_point_s=None,
                        aggregation=None, continued_total=None) -> dict:
    """Rebuild the SAME DisruptionMetrics an uninterrupted run would produce.

    Every field is resolved through `FIELD_RULES`; there is no fallback branch,
    so an unclassified field raises rather than being guessed at. This is what
    LUNA-WARM-03 hit: `max_queue_vehicles` is a MAXIMUM, and the old combiner
    had no rule for it, so it refused (correctly) and the campaign stopped.
    """
    verify_field_partition()
    evidence = parse_prefix_evidence(
        prefix_evidence, expected_warm_point_s=expected_warm_point_s)
    post = validate_post_warm_metrics(post_warm)

    # PRESERVED-ACCUMULATOR AGGREGATION. The objective is the sum of the two
    # segment aggregates and nothing else: the prefix contributes the vehicles
    # that finished before the snapshot, the post-warm segment contributes every
    # vehicle that finished after it — each with its FULL time loss, because the
    # saved state preserved the accumulator. There is no per-vehicle map, no
    # offset, and therefore no sum-of-halves to round twice.
    #
    # Supplied explicitly by callers that already computed it (so the observation
    # and its diagnostics cannot disagree), and otherwise derived here from the
    # two segments the caller passed.
    if aggregation is not None:
        # A SUPPLIED summary is still recomputed from the two segments this call
        # already holds, so a caller cannot assert an objective its own inputs
        # do not support.
        aggregated = validate_aggregation_summary(
            aggregation, prefix_completed=evidence["completed_trips"],
            raw_post=post, continued_total=continued_total)
    else:
        aggregated = aggregate_split_time_loss(
            completed_prefix_total=evidence["completed_trips"]["total_time_loss_s"],
            completed_prefix_trips=evidence["completed_trips"]["trip_count"],
            resumed_total=post["total_time_loss_s"],
            resumed_trips=post["trip_count"],
            continued_total=continued_total)
        aggregated = validate_aggregation_summary(aggregated)

    return _reconstruct_from_sections(
        {"counters": evidence["counters"],
         "teleport_reasons": evidence["teleport_reasons"],
         "queue_maximum": evidence["queue_maximum"]},
        post, aggregated=aggregated)



def concatenate_recovery_buckets(prefix_buckets, post_buckets, warm_point_s,
                                 *, domain_start_s=None, domain_end_s=None):
    """Join prefix and post-warm buckets into one ordered, gap-free domain.

    Never synthesises a bucket. Duplicate, missing, out-of-order or
    boundary-crossing intervals are rejected, because a silently patched
    recovery domain would change the recovery verdict without any evidence
    that the traffic actually recovered.

    BOTH segments must be non-empty and the joined domain must cover exactly
    the expected endpoints. Accepting two empty segments would return an empty
    domain that satisfies every adjacency rule vacuously — an "unmeasured"
    recovery reading as a clean one is the failure this exists to prevent.
    """
    left = _buckets_list(prefix_buckets, "prefix recovery buckets")
    right = _buckets_list(post_buckets, "post-warm recovery buckets")
    if not left:
        raise WarmStateContractError(
            "the prefix measured no recovery buckets; an empty domain cannot "
            "be treated as a recovered one")
    if not right:
        raise WarmStateContractError(
            "the post-warm segment measured no recovery buckets; an empty "
            "domain cannot be treated as a recovered one")
    combined = list(left) + list(right)
    for index, bucket in enumerate(combined):
        if not {"begin_s", "end_s"} <= set(bucket):
            raise WarmStateContractError("recovery bucket lacks begin_s/end_s")
        if bucket["end_s"] <= bucket["begin_s"]:
            raise WarmStateContractError(
                f"recovery bucket {index} is empty or inverted")
    for bucket in left:
        if bucket["end_s"] > warm_point_s:
            raise WarmStateContractError(
                f"prefix bucket ends {bucket['end_s']} after the warm point "
                f"{warm_point_s}; a boundary-crossing bucket is ambiguous")
    for bucket in right:
        if bucket["begin_s"] < warm_point_s:
            raise WarmStateContractError(
                f"post-warm bucket begins {bucket['begin_s']} before the warm "
                f"point {warm_point_s}")
    for previous, current in zip(combined, combined[1:]):
        if current["begin_s"] < previous["end_s"]:
            raise WarmStateContractError(
                f"recovery buckets overlap at {current['begin_s']}")
        if current["begin_s"] > previous["end_s"]:
            raise WarmStateContractError(
                f"recovery buckets leave a gap between {previous['end_s']} and "
                f"{current['begin_s']}; buckets are never synthesised to fill it")
    # The two segments must MEET exactly at the warm point, with no silent
    # truncation on either side.
    if left[-1]["end_s"] != warm_point_s:
        raise WarmStateContractError(
            f"the prefix domain ends at {left[-1]['end_s']}, not the warm point "
            f"{warm_point_s}")
    if right[0]["begin_s"] != warm_point_s:
        raise WarmStateContractError(
            f"the post-warm domain begins at {right[0]['begin_s']}, not the "
            f"warm point {warm_point_s}")
    if domain_start_s is not None and combined[0]["begin_s"] != domain_start_s:
        raise WarmStateContractError(
            f"the joined domain begins at {combined[0]['begin_s']}, not the "
            f"expected {domain_start_s}")
    if domain_end_s is not None and combined[-1]["end_s"] != domain_end_s:
        raise WarmStateContractError(
            f"the joined domain ends at {combined[-1]['end_s']}, not the "
            f"expected {domain_end_s}")
    return combined


# ── Route-safe split selection (LUNA-WARM-06) ────────────────────────────────
# LUNA-WARM-05 compared a warm arm whose PREFIX had been simulated from the
# UNFILTERED route while its post-warm segment used the closure-filtered one.
# Any vehicle whose route the filtering changes, and which departs before the
# snapshot, therefore drove the wrong route in the prefix. Choosing the warm
# point without consulting the filtering is unsound no matter how the counters
# are then combined.
_VEHICLE_ID_RE = re.compile(r'<vehicle\b[^>]*\bid="([^"]*)"')
_VEHICLE_DEPART_RE = re.compile(r'<vehicle\b[^>]*\bdepart="([0-9.eE+-]+)"')
_ROUTE_EDGES_RE = re.compile(r'<route\b[^>]*\bedges="([^"]*)"')

ROUTE_AUDIT_SCHEMA = "warm_route_mutation_audit_v1"


def stream_route_vehicles(path):
    """Yield `(vehicle_id, depart_s, edges)` for a one-line-per-vehicle route.

    Single pass, no XML tree: these files are ~125 MB each. An unparsable
    vehicle raises rather than being skipped — a silently dropped vehicle would
    look like an unchanged route.
    """
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line.startswith("<vehicle"):
                continue
            identifier = _VEHICLE_ID_RE.search(line)
            depart = _VEHICLE_DEPART_RE.search(line)
            edges = _ROUTE_EDGES_RE.search(line)
            if identifier is None or depart is None or edges is None:
                raise WarmStateContractError(
                    f"unparsable vehicle element in {Path(path).name}")
            yield identifier.group(1), float(depart.group(1)), edges.group(1)


def audit_route_mutation(original_path, filtered_path) -> dict:
    """Compare an original route with its closure-filtered form.

    Reports how many vehicles the filtering changed or dropped and, crucially,
    the EARLIEST departure among them: no snapshot at or after that time can be
    reused, because the prefix would already have simulated a route the
    candidate does not use.
    """
    original = {}
    for identifier, depart, edges in stream_route_vehicles(original_path):
        if identifier in original:
            raise WarmStateContractError(
                f"duplicate vehicle id {identifier!r} in {Path(original_path).name}")
        original[identifier] = (depart, edges)

    changed = dropped = 0
    earliest = None
    seen = set()
    for identifier, depart, edges in stream_route_vehicles(filtered_path):
        if identifier in seen:
            raise WarmStateContractError(
                f"duplicate vehicle id {identifier!r} in "
                f"{Path(filtered_path).name}")
        seen.add(identifier)
        before = original.get(identifier)
        if before is None:
            raise WarmStateContractError(
                f"filtered route introduces unknown vehicle {identifier!r}; "
                f"filtering may only shorten or drop")
        if before[0] != depart:
            # A shifted departure affects BOTH times: the prefix simulated the
            # original one. Taking only the new time would miss the earlier of
            # the two, so the audit records the earliest either way.
            changed += 1
            for moment in (before[0], depart):
                earliest = moment if earliest is None else min(earliest, moment)
        elif before[1] != edges:
            changed += 1
            earliest = depart if earliest is None else min(earliest, depart)
    for identifier, (depart, _edges) in original.items():
        if identifier not in seen:
            dropped += 1
            earliest = depart if earliest is None else min(earliest, depart)

    return {
        "schema": ROUTE_AUDIT_SCHEMA,
        "original_sha256": _sha256_file(original_path),
        "filtered_sha256": _sha256_file(filtered_path),
        "original_vehicles": len(original),
        "filtered_vehicles": len(seen),
        "changed_vehicles": changed,
        "dropped_vehicles": dropped,
        "earliest_affected_depart_s": earliest,
    }


AUDIT_FIELDS = frozenset({
    "schema", "original_sha256", "filtered_sha256", "original_vehicles",
    "filtered_vehicles", "changed_vehicles", "dropped_vehicles",
    "earliest_affected_depart_s",
})


def validate_route_audit(audit) -> dict:
    """A valid SCHEMA is not a valid audit.

    A partial record — right schema, missing or impossible counts — would let
    `route_safe_warm_point` compute a bound from nothing. Every field is
    required, typed, and checked for internal consistency.
    """
    if not isinstance(audit, Mapping):
        raise WarmStateContractError("route audit must be an object")
    if audit.get("schema") != ROUTE_AUDIT_SCHEMA:
        raise WarmStateContractError(
            f"unsupported route audit schema {audit.get('schema')!r}")
    if set(audit) != AUDIT_FIELDS:
        raise WarmStateContractError(
            f"route audit field set is wrong: {sorted(set(audit))}")
    for name in ("original_sha256", "filtered_sha256"):
        value = audit[name]
        if not isinstance(value, str) or len(value) != 64 or \
                any(c not in "0123456789abcdef" for c in value):
            raise WarmStateContractError(f"route audit {name} is not a sha256")
    for name in ("original_vehicles", "filtered_vehicles", "changed_vehicles",
                 "dropped_vehicles"):
        _nonnegative_int(audit[name], f"route audit {name}")
    affected = audit["earliest_affected_depart_s"]
    if affected is not None:
        _finite_number(affected, "route audit earliest_affected_depart_s")

    # Internal consistency: filtering may only shorten or drop.
    if audit["filtered_vehicles"] > audit["original_vehicles"]:
        raise WarmStateContractError(
            f"route audit: filtered {audit['filtered_vehicles']} exceeds "
            f"original {audit['original_vehicles']}; filtering may only drop")
    if audit["dropped_vehicles"] != (audit["original_vehicles"]
                                     - audit["filtered_vehicles"]):
        raise WarmStateContractError(
            "route audit: dropped_vehicles disagrees with the vehicle counts")
    if audit["changed_vehicles"] > audit["filtered_vehicles"]:
        raise WarmStateContractError(
            "route audit: more changed vehicles than the filtered route holds")
    affected_total = audit["changed_vehicles"] + audit["dropped_vehicles"]
    if affected_total and affected is None:
        raise WarmStateContractError(
            f"route audit reports {affected_total} affected vehicles but no "
            f"earliest affected departure")
    if not affected_total and affected is not None:
        raise WarmStateContractError(
            "route audit reports an affected departure but no affected vehicles")
    return dict(audit)


def route_safe_warm_point(audit, *, closure_begin_s,
                          alignment_s=WARM_ALIGNMENT_S,
                          minimum_prefix_s=MINIMUM_PREFIX_S):
    """The largest aligned snapshot that is safe for THIS candidate, or None.

    Safe means strictly before both the closure and the earliest departure the
    filtering affects. Returning None is a correct answer: the caller then runs
    the unchanged cold path rather than warming from an invalid prefix.
    """
    audit = validate_route_audit(audit)
    _positive_int(closure_begin_s, "closure_begin_s")
    bound = closure_begin_s
    earliest = audit.get("earliest_affected_depart_s")
    if earliest is not None:
        bound = min(bound, earliest)
    if bound <= 0:
        return None
    import math
    point = int(math.ceil(bound / alignment_s) - 1) * alignment_s
    if point <= 0 or point < minimum_prefix_s:
        return None
    if not point < bound:
        return None
    return point
