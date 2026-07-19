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
from traffic_sim.simulation.search_workspace import (
    DEFAULT_ROOT,
    SearchWorkspace,
    open_search_workspace,
)


SCHEMA_VERSION = 1
POLICY_STATUSES = frozenset({"provisional", "golden_frozen"})


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
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
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

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("monthly policy_id must be non-empty")
        if not isinstance(self.benchmark_id, str) or not self.benchmark_id.strip():
            raise ValueError("monthly benchmark_id must be non-empty")
        if self.status not in POLICY_STATUSES:
            raise ValueError(
                f"monthly policy status must be one of {sorted(POLICY_STATUSES)}"
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
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "monthly_closure_search_policy",
            "policy_id": self.policy_id,
            "benchmark_id": self.benchmark_id,
            "status": self.status,
            "pilot": asdict(self.pilot),
            "finalist": asdict(self.finalist),
        }
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
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "monthly_closure_search_result",
        "search_id": spec.search_id,
        "search_content_key": spec.content_key,
        "policy": policy.to_dict(),
        "simulation_backend": dict(backend_provenance),
        "status": decision_status,
        "winner_id": winner_id,
        "tie_ids": tie_ids,
        "selected_schedules": [
            schedules[candidate_id].to_dict()
            for candidate_id in selected
        ],
        # Every SHORTLISTED schedule's exact intervals, so a reader can map
        # the per-candidate statistics in robust_decision back to real
        # dates/times without re-deriving the calendar (at most the
        # shortlist cap of schedules; each is a few intervals).
        "shortlisted_schedules": [
            schedules[candidate_id].to_dict()
            for candidate_id in shortlist_ids
        ],
        "screening": {
            "candidate_count": screening.get("candidate_count"),
            "scoreable_candidate_count": screening.get(
                "scoreable_candidate_count"
            ),
            "shortlist_count": len(screening["shortlist"]["entries"]),
            "proxy_version": screening.get("proxy_version"),
        },
        "pilot_selection": dict(pilot_selection),
        "robust_decision": dict(decision) if decision is not None else None,
        "claim_boundary": _claim_boundary(screening, decision_status),
    }


def _claim_boundary(
    screening: Mapping[str, Any],
    decision_status: str,
) -> dict[str, Any]:
    """Evidence-level honesty labels for one monthly result.

    Two different gates are folded here and must not be confused:

    - ``global_best_claim_allowed`` stays False in every mode until the new
      untouched monthly held-out release gate passes — even a bounded
      exhaustive search runs the frozen pilot retention band, which has
      only been exercised against the golden benchmark, not held out.
    - ``ui_exposure_allowed`` depends on how candidates were SCREENED.  A
      proxy shortlist may not reach the UI at all (its held-out gate
      failed).  A bounded exhaustive search has no proxy: every ranked
      candidate carries real SUMO evidence, which is exactly the evidence
      level the already-released closure-time feature shows, so the result
      may be displayed with the restricted "best among SUMO-verified
      finalists" wording.
    """
    exhaustive = (
        str(screening.get("proxy_version", ""))
        == "bounded_exhaustive_sumo_v1"
    )
    return {
        "best_result_available": decision_status == "unique_winner",
        "best_result_scope": (
            (
                "sumo_verified_bounded_exhaustive"
                if exhaustive
                else "sumo_verified_monthly_shortlist"
            )
            if decision_status == "unique_winner"
            else None
        ),
        "global_best_claim_allowed": False,
        "ui_exposure_allowed": exhaustive,
        "reason": (
            "bounded exhaustive screening: every ranked candidate is "
            "SUMO-verified; the global-best claim still awaits the new "
            "untouched monthly held-out release gate"
            if exhaustive
            else "a new untouched monthly held-out release gate has not passed"
        ),
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
        pilot_evidence: list[CandidateEvidence] = []
        pilot_targets = {
            variant: policy.pilot.repetitions_per_variant
            for variant in DEMAND_VARIANTS
        }
        for index, candidate_id in enumerate(shortlist_ids):
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
                "global_best_claim_allowed": False,
            },
        )
        workspace.finish("succeeded")
        return result
    except BaseException as exc:
        if workspace.status == "running":
            workspace.update_progress(phase, error=str(exc))
        raise
