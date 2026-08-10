"""Resumable orchestration for robust recurring closure searches.

This module owns sequencing and persistence, not SUMO details.  A backend
receives an exact :class:`ClosureSchedule` plus deterministic target
replication counts and returns cumulative paired evidence.  Publishing each
completed candidate as an immutable workspace artifact makes process restart
safe: completed SUMO work is loaded and verified instead of repeated.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSchedule,
    ClosureSearchSpec,
)
from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    DEMAND_VARIANTS,
    FinalistPolicy,
    PairedObservation,
    decide_finalists,
)
from traffic_sim.simulation.pilot_selection import (
    PilotPolicy,
    select_pilot_finalists,
)
from traffic_sim.simulation.monthly_proxy import (
    HELD_OUT_VALIDATED_SHORTLIST_POLICY,
    SHORTLIST_VERSION,
)
from traffic_sim.simulation.search_workspace import (
    DEFAULT_ROOT,
    SearchWorkspace,
    open_search_workspace,
)
from traffic_sim.simulation.period_comparison import build_period_comparison


SCHEMA_VERSION = 1
POLICY_STATUSES = frozenset({"provisional", "golden_frozen"})
RANKING_OBJECTIVES = frozenset({"legacy_time_loss_v1", "closure_cost_v1"})
# The tracked held-out release gate (IMPROVEMENT_PLAN.md Phase 4).  When
# this record exists, is well-formed and says "pass", the pre-registered
# release contract is satisfied: the pilot/finalist policy is golden-frozen
# AND an untouched held-out set passed practical-winner recall, regret and
# failure recall.  Any problem with the record fails closed to the
# pre-release claim boundary.
HELDOUT_GATE_RECORD = Path("validation") / "monthly_gate_record.json"
# LUNA-V5-01: adoption now needs a SECOND, independent post-review artifact
# binding the record's exact bytes. Neither alone opens the gate.
HELDOUT_GATE_CERTIFICATE = (
    Path("validation") / "monthly_gate_adoption_certificate.json")
# The frozen campaign a passing record must belong to. Kept beside the gate
# path so the loader can bind a record to the exact untouched campaign and
# manifest identity rather than to the shortlist policy alone.
# The CURRENT frozen campaign. The adoption loader no longer consults this —
# an adoption certificate names its own manifest — but keeping it pointed at
# the live frozen campaign stops `frozen_campaign_identity()` going stale.
HELDOUT_CAMPAIGN_MANIFEST = (
    Path("validation") / "monthly_proxy_manifest_v6.json"
)


def frozen_campaign_identity(
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Identity of the frozen held-out campaign, or None (fail closed).

    Read through the production manifest validator, so a manifest whose
    recorded content key no longer recomputes is refused rather than trusted.
    """
    from traffic_sim.simulation.proxy_validation import (
        validate_validation_manifest,
    )

    try:
        manifest = validate_validation_manifest(json.loads(
            Path(path if path is not None else HELDOUT_CAMPAIGN_MANIFEST)
            .read_text(encoding="utf-8")
        ))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    campaign_version = manifest.get("campaign_version")
    content_key = manifest.get("content_key")
    if (
        not isinstance(campaign_version, str)
        or not campaign_version
        or not isinstance(content_key, str)
        or not content_key
        or not manifest.get("cases")
    ):
        return None
    return {
        "campaign_version": campaign_version,
        "manifest_content_key": content_key,
        "required_cases": len(manifest["cases"]),
    }


def load_passing_heldout_gate(
    path: Path | None = None,
    certificate_path: Path | None = None,
) -> dict[str, Any] | None:
    """Return the adopted passing held-out gate record, or None (fail closed).

    A gate record licenses claims for ONE campaign. Checking only the record's
    own fields was self-certifying: any byte edited inside it still validated
    against itself. Adoption therefore requires the record AND a post-review
    adoption certificate that independently binds its exact bytes, the frozen
    manifest identity and the bounded claim scope — see
    `traffic_sim.simulation.heldout_gate`. Missing or altered artifacts,
    earlier campaigns and incomplete runs all fail closed.
    """
    from traffic_sim.simulation.heldout_gate import (  # noqa: PLC0415
        load_adopted_gate)

    return load_adopted_gate(
        path if path is not None else HELDOUT_GATE_RECORD,
        certificate_path if certificate_path is not None
        else HELDOUT_GATE_CERTIFICATE,
    )


def _canonical_digest(value: Any, *, length: int = 24) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class MonthlySearchPolicy:
    """Explicit pilot/finalist policy pinned to one benchmark identity."""

    policy_id: str
    benchmark_id: str
    status: str
    pilot: PilotPolicy
    finalist: FinalistPolicy
    objective_method: str = "legacy_time_loss_v1"

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("monthly policy_id must be non-empty")
        if not isinstance(self.benchmark_id, str) or not self.benchmark_id.strip():
            raise ValueError("monthly benchmark_id must be non-empty")
        if self.status not in POLICY_STATUSES:
            raise ValueError(
                f"monthly policy status must be one of {sorted(POLICY_STATUSES)}"
            )
        if self.objective_method not in RANKING_OBJECTIVES:
            raise ValueError(
                "monthly objective_method must be one of "
                f"{sorted(RANKING_OBJECTIVES)}"
            )
        if self.pilot.variants != self.finalist.variants:
            raise ValueError("pilot and finalist demand variants must match")
        if self.pilot.repetitions_per_variant > (
            self.finalist.initial_repetitions
        ):
            raise ValueError(
                "pilot repetitions cannot exceed finalist initial repetitions"
            )

    @property
    def content_key(self) -> str:
        return _canonical_digest(self.to_dict(include_content_key=False))

    def to_dict(self, *, include_content_key: bool = True) -> dict[str, Any]:
        finalist = asdict(self.finalist)
        # The original v1 policy predates this explicit field. Keep its
        # canonical bytes stable so a compatibility addition cannot rewrite
        # a frozen golden identity. New objective-aligned policies serialize
        # the field and receive a distinct content key.
        if (
            self.objective_method == "legacy_time_loss_v1"
            and finalist.get("practical_equivalence_vehicle_hours") == 0.0
        ):
            finalist.pop("practical_equivalence_vehicle_hours", None)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "monthly_closure_search_policy",
            "policy_id": self.policy_id,
            "benchmark_id": self.benchmark_id,
            "status": self.status,
            "pilot": asdict(self.pilot),
            "finalist": finalist,
        }
        if self.objective_method != "legacy_time_loss_v1":
            payload["objective_method"] = self.objective_method
        if include_content_key:
            payload["content_key"] = self.content_key
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MonthlySearchPolicy":
        if not isinstance(raw, Mapping):
            raise ValueError("monthly search policy must be an object")
        if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise ValueError("unsupported monthly search policy schema")
        pilot_raw = dict(raw.get("pilot", {}))
        finalist_raw = dict(raw.get("finalist", {}))
        if "variants" in pilot_raw:
            pilot_raw["variants"] = tuple(pilot_raw["variants"])
        if "variants" in finalist_raw:
            finalist_raw["variants"] = tuple(finalist_raw["variants"])
        policy = cls(
            policy_id=str(raw.get("policy_id", "")),
            benchmark_id=str(raw.get("benchmark_id", "")),
            status=str(raw.get("status", "")),
            pilot=PilotPolicy(**pilot_raw),
            finalist=FinalistPolicy(**finalist_raw),
            objective_method=str(
                raw.get("objective_method", "legacy_time_loss_v1")
            ),
        )
        supplied = raw.get("content_key")
        if supplied is not None and supplied != policy.content_key:
            raise ValueError("monthly search policy content_key is invalid")
        return policy


class CandidateRunner(Protocol):
    """Simulation backend used by :func:`run_monthly_search`."""

    def provenance(self) -> Mapping[str, Any]:
        """Return immutable inputs/runtime/source identity for this backend."""

    def run_candidate(
        self,
        schedule: ClosureSchedule,
        *,
        target_repetitions: Mapping[str, int],
        existing: CandidateEvidence | None,
        stage: str,
    ) -> CandidateEvidence:
        """Return cumulative paired evidence through every requested target."""


class PreparatoryCandidateRunner(CandidateRunner, Protocol):
    """Backend that must freeze resources for the screened shortlist."""

    def prepare(self, schedules: Sequence[ClosureSchedule]) -> None:
        """Resolve and pin every resource before provenance is published."""


ScreenBuilder = Callable[[Path], Mapping[str, Any]]


def canonical_seed(demand_variant: str, repetition: int) -> int:
    """Stable interleaved q10/q50/q90 seed identity."""
    if demand_variant not in DEMAND_VARIANTS:
        raise ValueError("unknown demand variant")
    if (
        isinstance(repetition, bool)
        or not isinstance(repetition, int)
        or repetition < 0
    ):
        raise ValueError("repetition must be a non-negative integer")
    return 1000 + repetition * len(DEMAND_VARIANTS) + (
        DEMAND_VARIANTS.index(demand_variant)
    )


def evidence_to_dict(
    evidence: CandidateEvidence,
    *,
    stage: str,
    target_repetitions: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "monthly_closure_candidate_evidence",
        "stage": stage,
        "candidate_id": evidence.candidate_id,
        "target_repetitions": {
            variant: int(target_repetitions[variant])
            for variant in DEMAND_VARIANTS
        },
        "hard_failures": list(evidence.hard_failures),
        "observations": [asdict(item) for item in evidence.observations],
        "disruption": [dict(item) for item in evidence.disruption],
    }


def evidence_from_dict(raw: Mapping[str, Any]) -> CandidateEvidence:
    if not isinstance(raw, Mapping):
        raise ValueError("candidate evidence must be an object")
    if (
        raw.get("schema_version") != SCHEMA_VERSION
        or raw.get("kind") != "monthly_closure_candidate_evidence"
    ):
        raise ValueError("candidate evidence schema/kind is invalid")
    candidate_id = str(raw.get("candidate_id", ""))
    observations = tuple(
        PairedObservation(**dict(item))
        for item in raw.get("observations", ())
    )
    return CandidateEvidence(
        candidate_id=candidate_id,
        observations=observations,
        hard_failures=tuple(str(item) for item in raw.get("hard_failures", ())),
        disruption=tuple(dict(item) for item in raw.get("disruption", ())),
    )


def _artifact_records(
    workspace: SearchWorkspace,
    *,
    kind: str,
) -> list[dict[str, Any]]:
    return [
        record
        for record in workspace.manifest.get("artifacts", ())
        if record.get("kind") == kind
    ]


def _read_artifact(
    workspace: SearchWorkspace,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    path = workspace.directory / str(record["path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"workspace artifact must be an object: {path}")
    return payload


def _publish_json(
    workspace: SearchWorkspace,
    payload: Mapping[str, Any],
    name: str,
    *,
    kind: str,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    scratch = workspace.scratch_dir / (name.replace("/", "__") + ".tmp.json")
    _atomic_json(scratch, payload)
    workspace.publish_artifact(
        scratch,
        name,
        kind=kind,
        provenance=provenance,
    )
    scratch.unlink(missing_ok=True)
    return dict(payload)


def _schedule_ledger(
    workspace: SearchWorkspace,
    spec: ClosureSearchSpec,
) -> tuple[ClosureSchedule, ...]:
    records = _artifact_records(workspace, kind="closure_schedule_ledger")
    if records:
        if len(records) != 1:
            raise ValueError("workspace has duplicate schedule ledgers")
        payload = _read_artifact(workspace, records[0])
        schedules = tuple(
            ClosureSchedule.from_dict(item)
            for item in payload.get("schedules", ())
        )
    else:
        schedules = generate_closure_schedules(spec)
        if not schedules:
            raise ValueError("closure search has no legal schedules")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "closure_schedule_ledger",
            "search_content_key": spec.content_key,
            "candidate_count": len(schedules),
            "schedules": [item.to_dict() for item in schedules],
        }
        _publish_json(
            workspace,
            payload,
            "candidate-ledger.json",
            kind="closure_schedule_ledger",
            provenance={
                "search_content_key": spec.content_key,
                "candidate_count": len(schedules),
            },
        )
    if (
        not schedules
        or any(item.search_content_key != spec.content_key for item in schedules)
    ):
        raise ValueError("schedule ledger does not belong to this search")
    return schedules


def _screening_artifact(
    workspace: SearchWorkspace,
    spec: ClosureSearchSpec,
    schedules: Sequence[ClosureSchedule],
    screen_builder: ScreenBuilder,
) -> dict[str, Any]:
    records = _artifact_records(workspace, kind="monthly_proxy_screening")
    should_publish = False
    if records:
        if len(records) != 1:
            raise ValueError("workspace has duplicate screening artifacts")
        payload = _read_artifact(workspace, records[0])
    else:
        payload = dict(screen_builder(workspace.spec_path))
        should_publish = True
    if payload.get("kind") != "monthly_closure_proxy_screening":
        raise ValueError("monthly screening artifact kind is invalid")
    search = payload.get("search")
    if not isinstance(search, Mapping) or search.get("content_key") != spec.content_key:
        raise ValueError("monthly screening artifact belongs to another search")
    schedule_ids = {item.schedule_id for item in schedules}
    entries = (payload.get("shortlist") or {}).get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("monthly screening shortlist is empty")
    selected = [str(item.get("schedule_id", "")) for item in entries]
    if len(selected) != len(set(selected)) or not set(selected) <= schedule_ids:
        raise ValueError("monthly screening shortlist has invalid schedule IDs")
    if should_publish:
        _publish_json(
            workspace,
            payload,
            "monthly-proxy.json",
            kind="monthly_proxy_screening",
            provenance={
                "search_content_key": spec.content_key,
                "proxy_version": payload.get("proxy_version"),
                "ui_exposure_allowed": False,
            },
        )
    return payload


def _evidence_records(
    workspace: SearchWorkspace,
    *,
    kind: str,
) -> dict[str, list[tuple[int, CandidateEvidence]]]:
    grouped: dict[str, list[tuple[int, CandidateEvidence]]] = {}
    for record in _artifact_records(workspace, kind=kind):
        provenance = record.get("provenance", {})
        candidate_id = str(provenance.get("candidate_id", ""))
        round_index = int(provenance.get("round", 0))
        evidence = evidence_from_dict(_read_artifact(workspace, record))
        if evidence.candidate_id != candidate_id:
            raise ValueError("candidate evidence provenance mismatch")
        grouped.setdefault(candidate_id, []).append((round_index, evidence))
    for values in grouped.values():
        values.sort(key=lambda item: item[0])
        if len({item[0] for item in values}) != len(values):
            raise ValueError("candidate evidence round is duplicated")
    return grouped


def _counts(evidence: CandidateEvidence) -> dict[str, int]:
    counts = {variant: 0 for variant in DEMAND_VARIANTS}
    seen: set[tuple[str, int]] = set()
    for observation in evidence.observations:
        identity = (observation.demand_variant, observation.seed)
        if identity in seen:
            raise ValueError("candidate evidence has duplicate variant/seed pair")
        seen.add(identity)
        counts[observation.demand_variant] += 1
    return counts


def _validate_evidence_target(
    evidence: CandidateEvidence,
    *,
    schedule_id: str,
    targets: Mapping[str, int],
) -> None:
    if evidence.candidate_id != schedule_id:
        raise ValueError("simulation backend returned another candidate")
    if evidence.hard_failures:
        return
    counts = _counts(evidence)
    expected = {variant: int(targets[variant]) for variant in DEMAND_VARIANTS}
    if counts != expected:
        raise ValueError(
            f"simulation backend returned {counts}, expected {expected}"
        )
    for observation in evidence.observations:
        variant_index = DEMAND_VARIANTS.index(observation.demand_variant)
        if (observation.seed - 1000 - variant_index) % len(DEMAND_VARIANTS):
            raise ValueError("simulation backend returned non-canonical seed mapping")
        repetition = (
            observation.seed - 1000 - variant_index
        ) // len(DEMAND_VARIANTS)
        if repetition < 0 or observation.seed != canonical_seed(
            observation.demand_variant, repetition
        ):
            raise ValueError("simulation backend returned non-canonical seed mapping")


def _run_and_publish_candidate(
    workspace: SearchWorkspace,
    runner: CandidateRunner,
    schedule: ClosureSchedule,
    *,
    targets: Mapping[str, int],
    existing: CandidateEvidence | None,
    stage: str,
    kind: str,
    round_index: int,
    policy: MonthlySearchPolicy,
) -> CandidateEvidence:
    evidence = runner.run_candidate(
        schedule,
        target_repetitions=targets,
        existing=existing,
        stage=stage,
    )
    _validate_evidence_target(
        evidence,
        schedule_id=schedule.schedule_id,
        targets=targets,
    )
    payload = evidence_to_dict(
        evidence,
        stage=stage,
        target_repetitions=targets,
    )
    _publish_json(
        workspace,
        payload,
        f"{stage}/{schedule.schedule_id}-r{round_index:03}.json",
        kind=kind,
        provenance={
            "candidate_id": schedule.schedule_id,
            "round": round_index,
            "policy_content_key": policy.content_key,
            "stage": stage,
        },
    )
    return evidence


def _existing_policy(
    workspace: SearchWorkspace,
    policy: MonthlySearchPolicy,
) -> None:
    records = _artifact_records(workspace, kind="monthly_search_policy")
    if records:
        if len(records) != 1:
            raise ValueError("workspace has duplicate monthly policies")
        stored = MonthlySearchPolicy.from_dict(
            _read_artifact(workspace, records[0])
        )
        if stored.content_key != policy.content_key:
            raise ValueError("workspace monthly policy differs from requested policy")
        return
    _publish_json(
        workspace,
        policy.to_dict(),
        "policy.json",
        kind="monthly_search_policy",
        provenance={
            "policy_content_key": policy.content_key,
            "benchmark_id": policy.benchmark_id,
            "status": policy.status,
        },
    )


def _backend_provenance(
    workspace: SearchWorkspace,
    runner: CandidateRunner,
) -> dict[str, Any]:
    payload = dict(runner.provenance())
    if not payload:
        raise ValueError("monthly simulation backend provenance is empty")
    # Validate JSON safety before comparing or publishing.
    normalized = json.loads(json.dumps(
        payload,
        sort_keys=True,
        allow_nan=False,
    ))
    records = _artifact_records(
        workspace,
        kind="monthly_simulation_backend_provenance",
    )
    if records:
        if len(records) != 1 or _canonical_digest(
            _read_artifact(workspace, records[0])
        ) != _canonical_digest(normalized):
            raise ValueError(
                "workspace simulation backend provenance differs from "
                "the requested backend"
            )
        return normalized
    _publish_json(
        workspace,
        normalized,
        "simulation-backend.json",
        kind="monthly_simulation_backend_provenance",
        provenance={
            "provenance_content_key": _canonical_digest(normalized),
            "simulation_mode": normalized.get("simulation_mode"),
        },
    )
    return normalized


def _final_result(
    spec: ClosureSearchSpec,
    policy: MonthlySearchPolicy,
    schedules: Mapping[str, ClosureSchedule],
    screening: Mapping[str, Any],
    *,
    pilot_selection: Mapping[str, Any],
    decision: Mapping[str, Any] | None,
    backend_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    decision_status = (
        str(decision["status"])
        if decision is not None
        else (
            "no_viable"
            if pilot_selection.get("status") == "no_viable"
            else "inconclusive"
        )
    )
    winner_id = decision.get("winner_id") if decision else None
    tie_ids = list(decision.get("tie_ids", ())) if decision else []
    selected = [item for item in [winner_id, *tie_ids] if item]
    shortlist_ids = [
        str(item["schedule_id"])
        for item in screening["shortlist"]["entries"]
    ]
    independent_exhaustive = (
        screening.get("proxy_version")
        == "independent_daily_exhaustive_sumo_v1"
    )
    response_schedule_ids = shortlist_ids
    response_pilot_selection = dict(pilot_selection)
    period_comparison = (
        build_period_comparison(
            schedules,
            shortlist_ids,
            pilot_selection,
            winner_id=winner_id,
            tie_ids=tie_ids,
            unavailable_count=len(
                screening.get("unavailable_candidates") or ()
            ),
            objective_method=policy.objective_method,
            final_decision=decision,
        )
        if spec.period_comparison_policy == "rolling_period_v1"
        else None
    )
    if independent_exhaustive:
        # The immutable workspace already contains the complete schedule
        # ledger and pilot-selection statistics. Copying tens of thousands of
        # interval-heavy schedules into the API result would make a cache hit
        # slow and memory-heavy. The response needs only the bounded finalists;
        # the total exhaustive population remains explicit in screening.
        finalist_ids = [
            str(item) for item in pilot_selection.get("selected_ids", ())
        ]
        finalist_id_set = set(finalist_ids)
        response_schedule_ids = list(dict.fromkeys([
            *selected,
            *finalist_ids,
        ]))
        response_pilot_selection["candidate_count"] = len(
            pilot_selection.get("candidates", ())
        )
        response_pilot_selection["candidates"] = [
            item for item in pilot_selection.get("candidates", ())
            if item.get("candidate_id") in finalist_id_set
        ]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "monthly_closure_search_result",
        "search_id": spec.search_id,
        "search_content_key": spec.content_key,
        "closure_search_spec": spec.to_dict(),
        "policy": policy.to_dict(),
        "simulation_backend": dict(backend_provenance),
        "status": decision_status,
        "winner_id": winner_id,
        "tie_ids": tie_ids,
        "selected_schedules": [
            schedules[candidate_id].to_dict()
            for candidate_id in selected
        ],
        # Proxy/legacy mode includes every bounded shortlist schedule. Broad
        # independent exhaustive mode includes only its bounded finalists;
        # its complete immutable schedule ledger stays in the workspace.
        "shortlisted_schedules": [
            schedules[candidate_id].to_dict()
            for candidate_id in response_schedule_ids
        ],
        "screening": {
            "candidate_count": screening.get("candidate_count"),
            "scoreable_candidate_count": screening.get(
                "scoreable_candidate_count"
            ),
            "shortlist_count": len(screening["shortlist"]["entries"]),
            "unavailable_count": len(
                screening.get("unavailable_candidates") or ()
            ),
            "proxy_version": screening.get("proxy_version"),
        },
        "pilot_selection": response_pilot_selection,
        "robust_decision": dict(decision) if decision is not None else None,
        "period_comparison": period_comparison,
        "claim_boundary": _claim_boundary(
            screening,
            decision_status,
            heldout_gate=load_passing_heldout_gate(),
            policy_status=policy.status,
            objective_method=policy.objective_method,
        ),
    }


def _claim_boundary(
    screening: Mapping[str, Any],
    decision_status: str,
    *,
    heldout_gate: Mapping[str, Any] | None,
    policy_status: str = "golden_frozen",
    objective_method: str = "legacy_time_loss_v1",
) -> dict[str, Any]:
    """Evidence-level honesty labels for one monthly result.

    The pre-registered release contract (IMPROVEMENT_PLAN.md, step-4
    recovery decision): a global-best claim requires the golden-frozen
    pilot/finalist policy AND a passing untouched held-out gate measuring
    practical-winner recall, regret and failure recall.  ``heldout_gate``
    is the tracked passing record or None; everything fails closed.

    - Bounded exhaustive screening has no proxy: every ranked candidate
      carries real SUMO evidence, so results are UI-exposable regardless,
      and the global-best claim (within the enumerated search space)
      opens once the held-out gate has passed.
    - Proxy screening may reach the UI and claim a global best only when
      the passing record covers the SAME proxy version that produced the
      shortlist.
    """
    proxy_version = str(screening.get("proxy_version", ""))
    exhaustive = proxy_version in {
        "bounded_exhaustive_sumo_v1",
        "independent_daily_exhaustive_sumo_v1",
    }
    independent_exhaustive = (
        proxy_version == "independent_daily_exhaustive_sumo_v1"
    )
    unavailable_count = len(screening.get("unavailable_candidates") or ())
    independent_complete = (
        not independent_exhaustive or unavailable_count == 0
    )
    release_policy_ready = (
        policy_status == "golden_frozen"
        or objective_method != "closure_cost_v1"
    )
    validated_proxy = (
        release_policy_ready
        and
        heldout_gate is not None
        and not exhaustive
        and proxy_version == heldout_gate.get("proxy_version")
    )
    independent_gate = (
        heldout_gate is not None
        and heldout_gate.get("interday_policy")
        == "independent_daily_reset_v1"
    )
    released = (
        release_policy_ready
        and
        exhaustive
        and heldout_gate is not None
        and (not independent_exhaustive or (
            independent_gate and independent_complete
        ))
    ) or validated_proxy
    if not release_policy_ready:
        scope = (
            "sumo_verified_independent_daily_exhaustive"
            if independent_exhaustive and independent_complete
            else "sumo_verified_analysis"
        )
        reason = (
            f"policy status {policy_status}: SUMO evidence may be shown as "
            "analysis, but the global-best claim requires a golden-frozen "
            "policy and its matching held-out release gate"
        )
    elif exhaustive:
        scope = (
            (
                "sumo_verified_independent_daily_exhaustive"
                if independent_complete
                else "sumo_verified_independent_daily_available_schedules"
            )
            if independent_exhaustive
            else "sumo_verified_bounded_exhaustive"
        )
        exhaustive_label = (
            "independent-day exhaustive screening"
            if independent_exhaustive
            else "bounded exhaustive screening"
        )
        if heldout_gate is not None and (
            not independent_exhaustive
            or (independent_gate and independent_complete)
        ):
            reason = (
                f"{exhaustive_label}: every ranked candidate is "
                "SUMO-verified and the held-out release gate "
                f"({heldout_gate.get('heldout_set', 'v2')}) has passed"
            )
        else:
            gate_requirement = (
                (
                    f"coverage for {unavailable_count} unavailable schedule(s)"
                    if independent_exhaustive and not independent_complete
                    else "a passing untouched held-out release gate covering "
                    "this inter-day policy"
                )
                if independent_exhaustive
                else "a passing untouched monthly held-out release gate"
            )
            reason = (
                f"{exhaustive_label}: every ranked candidate is "
                "SUMO-verified; the global-best claim still awaits "
                + gate_requirement
            )
    elif validated_proxy:
        scope = "sumo_verified_monthly_shortlist_heldout_validated"
        reason = (
            f"proxy {proxy_version} passed the untouched held-out release "
            f"gate ({heldout_gate.get('heldout_set', 'v2')}): practical-"
            "winner recall, regret and failure recall within frozen "
            "thresholds"
        )
    else:
        scope = "sumo_verified_monthly_shortlist"
        reason = (
            "no passing untouched held-out release gate covers this "
            "screening"
        )
    return {
        "best_result_available": decision_status == "unique_winner",
        "best_result_scope": (
            scope if decision_status == "unique_winner" else None
        ),
        "global_best_claim_allowed": released,
        "ui_exposure_allowed": exhaustive or validated_proxy,
        "heldout_gate_record": (
            {
                "heldout_set": heldout_gate.get("heldout_set"),
                "manifest_content_key": heldout_gate.get(
                    "manifest_content_key"
                ),
                "gate_status": heldout_gate.get("gate_status"),
            }
            if heldout_gate is not None
            else None
        ),
        "reason": reason,
    }


def run_monthly_search(
    spec: ClosureSearchSpec,
    policy: MonthlySearchPolicy,
    *,
    runner: CandidateRunner,
    screen_builder: ScreenBuilder,
    root: Path = DEFAULT_ROOT,
) -> dict[str, Any]:
    """Run or resume one monthly search through a robust mesoscopic decision."""
    spec = ClosureSearchSpec.from_dict(spec.to_dict())
    policy = MonthlySearchPolicy.from_dict(policy.to_dict())
    workspace, _ = open_search_workspace(spec, root=root)
    phase = "policy"
    try:
        if workspace.status == "running":
            workspace.update_progress(phase)
        _existing_policy(workspace, policy)

        phase = "enumerate"
        if workspace.status == "running":
            workspace.update_progress(phase)
        schedule_values = _schedule_ledger(workspace, spec)
        schedules = {item.schedule_id: item for item in schedule_values}

        phase = "screen"
        if workspace.status == "running":
            workspace.update_progress(phase)
        screening = _screening_artifact(
            workspace,
            spec,
            schedule_values,
            screen_builder,
        )
        shortlist_ids = [
            str(item["schedule_id"])
            for item in screening["shortlist"]["entries"]
        ]
        shortlisted_schedules = [
            schedules[candidate_id] for candidate_id in shortlist_ids
        ]

        phase = "prepare_backend"
        if workspace.status == "running":
            workspace.update_progress(
                phase,
                completed=0,
                total=len(shortlisted_schedules),
            )
        prepare = getattr(runner, "prepare", None)
        if prepare is not None:
            prepare(shortlisted_schedules)
        backend_provenance = _backend_provenance(workspace, runner)
        final_records = _artifact_records(
            workspace,
            kind="monthly_closure_search_result",
        )
        if workspace.status == "succeeded":
            if len(final_records) != 1:
                raise ValueError(
                    "succeeded monthly search has no unique final result"
                )
            return _read_artifact(workspace, final_records[0])

        phase = "pilot"
        pilot_records = _evidence_records(
            workspace,
            kind="monthly_pilot_candidate",
        )
        compact_pilot = (
            getattr(runner, "compact_pilot_artifacts", False) is True
        )
        if compact_pilot and pilot_records:
            raise ValueError(
                "compact independent pilot cannot mix per-parent pilot artifacts"
            )
        pilot_evidence: list[CandidateEvidence] = []
        pilot_targets = {
            variant: policy.pilot.repetitions_per_variant
            for variant in DEMAND_VARIANTS
        }
        for index, candidate_id in enumerate(shortlist_ids):
            # A broad independent search can contain tens of thousands of
            # parents. Updating the workspace manifest for every additive
            # in-memory reconstruction turns cache hits into a filesystem
            # benchmark, so retain bounded progress writes instead.
            progress_stride = max(1, len(shortlist_ids) // 100)
            if not compact_pilot or index % progress_stride == 0:
                workspace.update_progress(
                    phase,
                    completed=index,
                    total=len(shortlist_ids),
                )
            existing = pilot_records.get(candidate_id, [])
            if existing:
                evidence = existing[-1][1]
                _validate_evidence_target(
                    evidence,
                    schedule_id=candidate_id,
                    targets=pilot_targets,
                )
            elif compact_pilot:
                evidence = runner.run_candidate(
                    schedules[candidate_id],
                    target_repetitions=pilot_targets,
                    existing=None,
                    stage="pilot",
                )
                _validate_evidence_target(
                    evidence,
                    schedule_id=candidate_id,
                    targets=pilot_targets,
                )
            else:
                evidence = _run_and_publish_candidate(
                    workspace,
                    runner,
                    schedules[candidate_id],
                    targets=pilot_targets,
                    existing=None,
                    stage="pilot",
                    kind="monthly_pilot_candidate",
                    round_index=0,
                    policy=policy,
                )
            pilot_evidence.append(evidence)

        pilot_selection = select_pilot_finalists(
            pilot_evidence,
            policy.pilot,
            ranking_objective=policy.objective_method,
            practical_equivalence_vehicle_hours=(
                policy.finalist.practical_equivalence_vehicle_hours
            ),
        )
        pilot_payload = pilot_selection.to_dict()
        pilot_selection_records = _artifact_records(
            workspace,
            kind="monthly_pilot_selection",
        )
        if pilot_selection_records:
            if len(pilot_selection_records) != 1 or _canonical_digest(
                _read_artifact(workspace, pilot_selection_records[0])
            ) != _canonical_digest(pilot_payload):
                raise ValueError("resumed pilot selection is not deterministic")
        else:
            _publish_json(
                workspace,
                pilot_payload,
                "pilot-selection.json",
                kind="monthly_pilot_selection",
                provenance={
                    "policy_content_key": policy.content_key,
                    "status": pilot_selection.status,
                    "screening_only": True,
                },
            )

        decision_payload: dict[str, Any] | None = None
        if pilot_selection.status == "ready":
            phase = "finalists"
            finalist_records = _evidence_records(
                workspace,
                kind="monthly_finalist_candidate",
            )
            current: dict[str, CandidateEvidence] = {}
            round_by_candidate: dict[str, int] = {}
            initial_targets = {
                variant: policy.finalist.initial_repetitions
                for variant in DEMAND_VARIANTS
            }
            pilot_by_id = {
                item.candidate_id: item for item in pilot_evidence
            }
            for index, candidate_id in enumerate(
                pilot_selection.selected_ids
            ):
                workspace.update_progress(
                    phase,
                    completed=index,
                    total=len(pilot_selection.selected_ids),
                )
                existing_rounds = finalist_records.get(candidate_id, [])
                if existing_rounds:
                    round_index, evidence = existing_rounds[-1]
                    round_by_candidate[candidate_id] = round_index
                    current[candidate_id] = evidence
                    continue
                evidence = _run_and_publish_candidate(
                    workspace,
                    runner,
                    schedules[candidate_id],
                    targets=initial_targets,
                    existing=pilot_by_id[candidate_id],
                    stage="finalist",
                    kind="monthly_finalist_candidate",
                    round_index=0,
                    policy=policy,
                )
                round_by_candidate[candidate_id] = 0
                current[candidate_id] = evidence

            decision_round = len(
                _artifact_records(workspace, kind="monthly_robust_decision")
            )
            while True:
                phase = "decide"
                workspace.update_progress(phase, completed=decision_round)
                decision = decide_finalists(
                    [
                        current[candidate_id]
                        for candidate_id in pilot_selection.selected_ids
                    ],
                    policy.finalist,
                    ranking_objective=policy.objective_method,
                )
                decision_payload = decision.to_dict()
                existing_decisions = _artifact_records(
                    workspace,
                    kind="monthly_robust_decision",
                )
                same_round = [
                    record
                    for record in existing_decisions
                    if int(record.get("provenance", {}).get("round", -1))
                    == decision_round
                ]
                if same_round:
                    if len(same_round) != 1 or _canonical_digest(
                        _read_artifact(workspace, same_round[0])
                    ) != _canonical_digest(decision_payload):
                        raise ValueError(
                            "resumed robust decision is not deterministic"
                        )
                else:
                    _publish_json(
                        workspace,
                        decision_payload,
                        f"decisions/round-{decision_round:03}.json",
                        kind="monthly_robust_decision",
                        provenance={
                            "round": decision_round,
                            "policy_content_key": policy.content_key,
                            "status": decision.status,
                        },
                    )
                if not decision.next_runs:
                    break

                targets_by_candidate = {
                    candidate_id: _counts(current[candidate_id])
                    for candidate_id in pilot_selection.selected_ids
                }
                for request in decision.next_runs:
                    targets_by_candidate[request.candidate_id][
                        request.demand_variant
                    ] += request.repetitions_to_add
                phase = "adaptive_finalists"
                for index, candidate_id in enumerate(
                    pilot_selection.selected_ids
                ):
                    targets = targets_by_candidate[candidate_id]
                    if targets == _counts(current[candidate_id]):
                        continue
                    workspace.update_progress(
                        phase,
                        completed=index,
                        total=len(pilot_selection.selected_ids),
                    )
                    next_round = round_by_candidate[candidate_id] + 1
                    current[candidate_id] = _run_and_publish_candidate(
                        workspace,
                        runner,
                        schedules[candidate_id],
                        targets=targets,
                        existing=current[candidate_id],
                        stage="finalist",
                        kind="monthly_finalist_candidate",
                        round_index=next_round,
                        policy=policy,
                    )
                    round_by_candidate[candidate_id] = next_round
                decision_round += 1

        phase = "publish"
        workspace.update_progress(phase)
        result = _final_result(
            spec,
            policy,
            schedules,
            screening,
            pilot_selection=pilot_payload,
            decision=decision_payload,
            backend_provenance=backend_provenance,
        )
        # Return the exact JSON representation that is persisted so an
        # idempotent reload cannot differ only because dataclass tuples became
        # JSON arrays on disk.
        result = json.loads(json.dumps(
            result,
            sort_keys=True,
            allow_nan=False,
        ))
        _publish_json(
            workspace,
            result,
            "result.json",
            kind="monthly_closure_search_result",
            provenance={
                "search_content_key": spec.content_key,
                "policy_content_key": policy.content_key,
                "status": result["status"],
                "global_best_claim_allowed": (
                    result["claim_boundary"]["global_best_claim_allowed"]
                ),
            },
        )
        workspace.finish("succeeded")
        return result
    except BaseException as exc:
        if workspace.status == "running":
            workspace.update_progress(phase, error=str(exc))
        raise
