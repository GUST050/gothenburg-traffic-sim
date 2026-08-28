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
import shutil
import tempfile
from collections.abc import Mapping as MappingABC
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

from traffic_sim.core.closure_calendar import iter_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSchedule,
    ClosureSearchSpec,
)
from traffic_sim.simulation import closure_ledgers
from traffic_sim.simulation.independent_daily import daily_unit_records
from traffic_sim.simulation.finalist_decision import (
    CanonicalObservationDigest,
    CandidateEvidence,
    DEMAND_VARIANTS,
    FinalistPolicy,
    PairedObservation,
    TimeoutIdentity,
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


#: SHARED by `MonthlySearchPolicy`, `evidence_to_dict`/`evidence_from_dict`
#: and several other artifact kinds in this module (see every
#: ``"schema_version": SCHEMA_VERSION`` site) — frozen golden artifacts such
#: as ``validation/monthly_search_policy_v1.json`` are pinned against this
#: exact value, so it must NEVER move for a change scoped to only one of
#: those artifact kinds. Candidate-evidence's own schema break (below) uses
#: its OWN dedicated version instead.
SCHEMA_VERSION = 1

#: v3: v2 made `timeout_undecided` entries validated `TimeoutIdentity`
#: records; v3 adds complete canonical-observation digests and requires the
#: exact current evidence envelope. This is deliberately its OWN constant,
#: separate from the shared
#: `SCHEMA_VERSION` above: candidate evidence is the only artifact kind this
#: pass changed the shape of, and bumping the shared constant would also
#: have silently invalidated `MonthlySearchPolicy` and every other artifact
#: keyed on it, including frozen golden validation files this pass must not
#: touch. The version-gate in `evidence_from_dict` fails closed on any
#: artifact written under v1; no v1 artifact is rewritten, it simply becomes
#: unreadable, exactly as a genuine schema break should.
EVIDENCE_SCHEMA_VERSION = 3
POLICY_STATUSES = frozenset({"provisional", "golden_frozen"})
RANKING_OBJECTIVES = frozenset({"legacy_time_loss_v1", "closure_cost_v1"})
# PR C.  The streaming enumeration lives in a workspace-owned directory OUTSIDE
# ``artifacts/``: workspace verification requires every file under
# ``artifacts/`` to be individually ledgered, but the three NDJSON files are one
# indivisible set whose completion signal is their manifest.  Publishing the
# manifest — and only the manifest — as a single artifact makes the workspace
# see one atomic publication instead of four independently interruptible ones.
LEDGER_DIRNAME = "ledgers"
LEDGER_MANIFEST_ARTIFACT = "candidate-ledger-manifest.json"
LEDGER_ARTIFACT_KIND = "closure_schedule_ledger_v2"
# A backend that cannot read the ledgers needs every shortlisted parent as an
# object.  That is the pre-PR-C behaviour and is fine for a bounded proxy
# shortlist; above this many schedules it is refused rather than silently
# materialised, so the memory gate cannot be lost to a fallback.
MATERIALISED_SHORTLIST_LIMIT = 512
# The full progress vocabulary a long run can report. Declared in one place so
# the API contract test, the web UI and the search cannot drift apart — a phase
# the UI has no label for reads as a bare "Söker" and tells the user nothing.
# `preflight`, `cost_units`, `cost_parents` and `health_scan` belong to the
# cost-ordered execution of PR D/E; `preflight` already runs on every product
# CLI invocation, the other three report the cost-ordered scan.
PROGRESS_PHASES = (
    "policy",
    "preflight",
    "enumerate",
    "screen",
    "paused_budget",
    "cost_units",
    "cost_parents",
    "health_scan",
    "prepare_backend",
    "pilot",
    "finalists",
    "decide",
    "adaptive_finalists",
    "publish",
)
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


class ResumableScreenBuilder(Protocol):
    """Screen builder that can consume an integrity-checked checkpoint."""

    def __call__(self, spec_path: Path) -> Mapping[str, Any]:
        """Start screening from the beginning."""

    def resume(
        self,
        spec_path: Path,
        checkpoint: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Continue after the latest published checkpoint."""


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
        "schema_version": EVIDENCE_SCHEMA_VERSION,
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
        "timeout_undecided": [
            item.to_dict() for item in evidence.timeout_undecided
        ],
        "canonical_observation_digests": [
            item.to_dict() for item in evidence.canonical_observation_digests
        ],
    }


def evidence_from_dict(raw: Mapping[str, Any]) -> CandidateEvidence:
    if not isinstance(raw, Mapping):
        raise ValueError("candidate evidence must be an object")
    expected_fields = {
        "schema_version", "kind", "stage", "candidate_id",
        "target_repetitions", "hard_failures", "observations", "disruption",
        "timeout_undecided", "canonical_observation_digests",
    }
    if set(raw) != expected_fields:
        raise ValueError("candidate evidence fields are invalid")
    if (
        raw.get("schema_version") != EVIDENCE_SCHEMA_VERSION
        or raw.get("kind") != "monthly_closure_candidate_evidence"
    ):
        raise ValueError("candidate evidence schema/kind is invalid")
    if raw["stage"] not in {"pilot", "finalist"}:
        raise ValueError("candidate evidence stage is invalid")
    if not isinstance(raw["candidate_id"], str):
        raise ValueError("candidate evidence candidate_id must be a string")
    if not isinstance(raw["target_repetitions"], Mapping):
        raise ValueError("candidate evidence target_repetitions is invalid")
    if set(raw["target_repetitions"]) != set(DEMAND_VARIANTS):
        raise ValueError("candidate evidence target_repetitions is incomplete")
    for value in raw["target_repetitions"].values():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("candidate evidence target repetition is invalid")
    for field in ("hard_failures", "observations", "disruption",
                  "timeout_undecided", "canonical_observation_digests"):
        if not isinstance(raw[field], list):
            raise ValueError(f"candidate evidence {field} must be a list")
    candidate_id = raw["candidate_id"]
    observations = tuple(
        PairedObservation(**dict(item))
        for item in raw["observations"]
    )
    return CandidateEvidence(
        candidate_id=candidate_id,
        observations=observations,
        hard_failures=tuple(str(item) for item in raw["hard_failures"]),
        disruption=tuple(dict(item) for item in raw["disruption"]),
        timeout_undecided=tuple(
            TimeoutIdentity.from_dict(item)
            for item in raw["timeout_undecided"]
        ),
        canonical_observation_digests=tuple(
            CanonicalObservationDigest.from_dict(item)
            for item in raw["canonical_observation_digests"]
        ),
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


class _MaterialisedCandidates(MappingABC):
    """Every parent schedule in memory — the pre-PR-C representation.

    Kept for two callers only: a workspace whose enumeration was published
    before PR C (``candidate-ledger.json``), and the small explicit
    compatibility path in :func:`_prepare_shortlist`.  New workspaces stream.
    """

    schema = "closure_schedule_ledger_v1"
    version = SCHEMA_VERSION
    ledger_directory: Path | None = None

    def __init__(self, schedules: Sequence[ClosureSchedule]) -> None:
        self._schedules: dict[str, ClosureSchedule] = {}
        for item in schedules:
            if item.schedule_id in self._schedules:
                raise ValueError("schedule ledger repeats a parent schedule")
            self._schedules[item.schedule_id] = item

    def __getitem__(self, schedule_id: str) -> ClosureSchedule:
        return self._schedules[schedule_id]

    def __contains__(self, schedule_id: object) -> bool:
        return schedule_id in self._schedules

    def __iter__(self) -> Iterator[str]:
        return iter(self._schedules)

    def __len__(self) -> int:
        return len(self._schedules)


class _StreamingCandidates(MappingABC):
    """Parent schedules read on demand from the published NDJSON ledgers.

    Holds byte offsets, not schedules.  Every consumer in this module already
    speaks ``Mapping[str, ClosureSchedule]``, so nothing downstream changes:
    a lookup seeks and parses one row instead of reading a dict entry.
    """

    schema = closure_ledgers.LEDGER_SCHEMA
    version = closure_ledgers.LEDGER_VERSION

    def __init__(
        self,
        directory: Path,
        manifest: closure_ledgers.LedgerManifest,
    ) -> None:
        self.ledger_directory = Path(directory)
        self.manifest = manifest
        self._index = closure_ledgers.ParentLedgerIndex(directory)
        if len(self._index) != manifest.parent_count:
            raise closure_ledgers.LedgerCorrupt(
                f"parent ledger holds {len(self._index)} schedules, manifest "
                f"froze {manifest.parent_count}")

    def __getitem__(self, schedule_id: str) -> ClosureSchedule:
        return self._index[schedule_id]

    def __contains__(self, schedule_id: object) -> bool:
        # Explicit, because `Mapping.__contains__` answers by CALLING
        # ``__getitem__`` and catching KeyError — which here means seeking to a
        # row, parsing it and constructing a schedule just to say "yes".
        # Screening asks this once per shortlisted ID, so on an exhaustive
        # search that default would parse the whole population.
        return schedule_id in self._index

    def __iter__(self) -> Iterator[str]:
        return iter(self._index.ids())

    def __len__(self) -> int:
        return len(self._index)


def _ledger_unit_records(
    spec: ClosureSearchSpec,
    parent: ClosureSchedule,
) -> Sequence[tuple[str, Mapping[str, Any], Callable[[], ClosureSchedule]]]:
    """Daily units for the ledger, or none when the policy has no daily units.

    Only ``independent_daily_reset_v1`` decomposes a parent into independent
    daily SUMO units.  Under any other interday policy the parent IS the unit
    of execution, so the unit and relationship ledgers stay empty rather than
    inventing a decomposition the executor would never use.
    """
    if spec.interday_policy != "independent_daily_reset_v1":
        return ()
    return daily_unit_records(spec, parent)


def _candidate_ledger(
    workspace: SearchWorkspace,
    spec: ClosureSearchSpec,
) -> _MaterialisedCandidates | _StreamingCandidates:
    """Open — or build once — this search's immutable enumeration.

    Three states, in priority order:

    1. A pre-PR-C ``closure_schedule_ledger`` artifact: an old workspace, read
       exactly as it was written.  Old searches must stay resumable.
    2. A published streaming manifest: FROZEN evidence.  It is verified, and a
       digest or count mismatch raises rather than rebuilding — regenerating
       would destroy the only trace that a completed artifact was damaged.
    3. Neither: enumerate lazily into ``ledgers/`` and publish the manifest as
       the single workspace artifact.  Files already sitting there belong to an
       interrupted, never-published build; they are validated if they happen to
       be complete and otherwise rebuilt, which is safe precisely because
       nothing has declared them finished.
    """
    records = _artifact_records(workspace, kind="closure_schedule_ledger")
    if records:
        if len(records) != 1:
            raise ValueError("workspace has duplicate schedule ledgers")
        payload = _read_artifact(workspace, records[0])
        schedules = tuple(
            ClosureSchedule.from_dict(item)
            for item in payload.get("schedules", ())
        )
        if not schedules or any(
            item.search_content_key != spec.content_key for item in schedules
        ):
            raise ValueError("schedule ledger does not belong to this search")
        return _MaterialisedCandidates(schedules)

    directory = workspace.directory / LEDGER_DIRNAME
    published = _artifact_records(workspace, kind=LEDGER_ARTIFACT_KIND)
    if published:
        if len(published) != 1:
            raise ValueError("workspace has duplicate schedule ledgers")
        frozen = _read_artifact(workspace, published[0])
        manifest = closure_ledgers.verify_ledgers(
            directory,
            expected_search_content_key=spec.content_key,
        )
        if frozen.get("content_key") != manifest.key:
            raise closure_ledgers.LedgerCorrupt(
                "published ledger manifest does not describe these ledgers")
        return _StreamingCandidates(directory, manifest)

    manifest = None
    if directory.is_dir():
        try:
            manifest = closure_ledgers.verify_ledgers(
                directory,
                expected_search_content_key=spec.content_key,
            )
        except closure_ledgers.LedgerError:
            # Unpublished ledgers are a build area, not evidence: an
            # interrupted write is rebuilt from the same deterministic
            # enumeration.  Fail-closed applies from publication onward.
            manifest = None
    if manifest is None:
        manifest = closure_ledgers.write_ledgers(
            directory,
            spec,
            iter_closure_schedules(spec),
            unit_records=_ledger_unit_records,
            provenance={
                "search_id": spec.search_id,
                "interday_policy": spec.interday_policy,
                "work_allocation_policy": spec.work_allocation_policy,
                "source": spec.source,
            },
        )
    if manifest.parent_count == 0:
        shutil.rmtree(directory, ignore_errors=True)
        raise ValueError("closure search has no legal schedules")

    _publish_json(
        workspace,
        manifest.to_dict(),
        LEDGER_MANIFEST_ARTIFACT,
        kind=LEDGER_ARTIFACT_KIND,
        provenance={
            "search_content_key": spec.content_key,
            "candidate_count": manifest.parent_count,
            "unique_daily_unit_count": manifest.unique_unit_count,
            # New workspaces declare the ledger contract they were built
            # with, so a future schema can refuse an old directory instead
            # of misreading it.
            "ledger_schema": manifest.schema,
            "ledger_version": manifest.version,
            "ledger_directory": LEDGER_DIRNAME,
            "ledger_manifest_content_key": manifest.key,
        },
    )
    return _StreamingCandidates(directory, manifest)


def _prepare_shortlist(
    runner: CandidateRunner,
    candidates: _MaterialisedCandidates | _StreamingCandidates,
    shortlist_ids: Sequence[str],
) -> None:
    """Hand the backend its shortlist without materialising the population.

    An independent exhaustive search shortlists EVERY parent, so building
    ``[candidates[i] for i in shortlist_ids]`` would reintroduce exactly the
    object graph PR C removes.  A runner that can read the ledgers is given
    the directory instead.

    The materialising path survives for old runners, but on a streaming
    workspace it is explicit and bounded: above ``MATERIALISED_SHORTLIST_LIMIT``
    it raises instead of quietly allocating, because a silent fallback is how a
    memory gate stops meaning anything.

    A PRE-PR-C workspace is exempt, deliberately.  Reading its
    ``candidate-ledger.json`` already put every parent in memory, so refusing to
    build a list of references to objects that are all alive anyway would break
    a resumable old search without saving a byte.
    """
    from_ledgers = getattr(runner, "prepare_from_ledgers", None)
    directory = candidates.ledger_directory
    if callable(from_ledgers) and directory is not None:
        from_ledgers(directory, tuple(shortlist_ids))
        return
    prepare = getattr(runner, "prepare", None)
    if prepare is None:
        return
    if directory is not None and len(shortlist_ids) > MATERIALISED_SHORTLIST_LIMIT:
        raise ValueError(
            f"shortlist of {len(shortlist_ids)} schedules exceeds the "
            f"materialising compatibility limit "
            f"{MATERIALISED_SHORTLIST_LIMIT}: this backend has no "
            f"prepare_from_ledgers and this search streams its ledgers"
        )
    prepare([candidates[candidate_id] for candidate_id in shortlist_ids])


def _screening_artifact(
    workspace: SearchWorkspace,
    spec: ClosureSearchSpec,
    candidates: Mapping[str, ClosureSchedule],
    screen_builder: ScreenBuilder,
) -> dict[str, Any]:
    records = _artifact_records(workspace, kind="monthly_proxy_screening")
    checkpoints = _artifact_records(
        workspace, kind="monthly_screening_checkpoint")
    should_publish = False
    if records:
        if len(records) != 1:
            raise ValueError("workspace has duplicate screening artifacts")
        payload = _read_artifact(workspace, records[0])
    else:
        if checkpoints:
            resume = getattr(screen_builder, "resume", None)
            if not callable(resume):
                raise ValueError(
                    "screening checkpoint exists but builder cannot resume"
                )
            payload = dict(resume(
                workspace.spec_path,
                _read_artifact(workspace, checkpoints[-1]),
            ))
        else:
            payload = dict(screen_builder(workspace.spec_path))
        should_publish = True
    search = payload.get("search")
    if not isinstance(search, Mapping) or search.get("content_key") != spec.content_key:
        raise ValueError("monthly screening artifact belongs to another search")
    if payload.get("kind") == "monthly_closure_screening_checkpoint":
        if records:
            raise ValueError("completed screening cannot become a checkpoint")
        state = payload.get("budget_state")
        checkpoint_state = payload.get("checkpoint_state")
        if (
            payload.get("status") != "paused"
            or payload.get("exhaustive") is not False
            or not isinstance(state, Mapping)
            or state.get("complete") is not False
            or not isinstance(checkpoint_state, Mapping)
            or checkpoint_state.get("search_content_key") != spec.content_key
            or not isinstance(payload.get("resume_token"), str)
            or not payload.get("resume_token")
            or "shortlist" in payload
        ):
            raise ValueError("monthly screening checkpoint is invalid")
        if should_publish:
            checkpoint_index = len(checkpoints)
            _publish_json(
                workspace,
                payload,
                f"screening-checkpoints/checkpoint-{checkpoint_index:04}.json",
                kind="monthly_screening_checkpoint",
                provenance={
                    "search_content_key": spec.content_key,
                    "resume_token": payload["resume_token"],
                    "budget_content_key": checkpoint_state.get(
                        "budget_content_key"),
                    "checkpoint_index": checkpoint_index,
                    "complete": False,
                },
            )
        return payload
    if payload.get("kind") != "monthly_closure_proxy_screening":
        raise ValueError("monthly screening artifact kind is invalid")
    entries = (payload.get("shortlist") or {}).get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("monthly screening shortlist is empty")
    selected = [str(item.get("schedule_id", "")) for item in entries]
    # Membership only: asking the ledger whether it knows an ID costs one
    # dict lookup, whereas building the set of every parent's schedule_id
    # would walk the population the streaming path exists to avoid.
    if len(selected) != len(set(selected)) or any(
        candidate_id not in candidates for candidate_id in selected
    ):
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
    cost_ordered_execution: Mapping[str, Any] | None = None,
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
        # Present only when cost-first execution ran. A reader of result.json
        # could otherwise not tell which execution path produced it, nor how
        # many SUMO runs the ordering saved.
        "cost_ordered_execution": (
            dict(cost_ordered_execution)
            if cost_ordered_execution is not None else None),
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


def _pilot_evidence_for(
    workspace: SearchWorkspace,
    runner: "CandidateRunner",
    schedule: ClosureSchedule,
    *,
    targets: Mapping[str, int],
    existing_records: Sequence[tuple[int, CandidateEvidence]],
    compact_pilot: bool,
    policy: "MonthlySearchPolicy",
) -> CandidateEvidence:
    """One candidate's pilot evidence: reused, run compactly, or published.

    Shared by the exhaustive and cost-ordered pilots so the two paths cannot
    differ in HOW a candidate is simulated — only in WHICH candidates are.
    """
    if existing_records:
        evidence = existing_records[-1][1]
        _validate_evidence_target(
            evidence,
            schedule_id=schedule.schedule_id,
            targets=targets,
        )
        return evidence
    if compact_pilot:
        evidence = runner.run_candidate(
            schedule,
            target_repetitions=targets,
            existing=None,
            stage="pilot",
        )
        _validate_evidence_target(
            evidence,
            schedule_id=schedule.schedule_id,
            targets=targets,
        )
        return evidence
    return _run_and_publish_candidate(
        workspace,
        runner,
        schedule,
        targets=targets,
        existing=None,
        stage="pilot",
        kind="monthly_pilot_candidate",
        round_index=0,
        policy=policy,
    )


def _runner_timing_snapshot(runner: "CandidateRunner") -> dict[str, Any]:
    """Read optional result-neutral S0 telemetry from a backend.

    Timing is diagnostic.  A broken optional hook or a value that cannot be
    persisted as strict JSON must not turn an otherwise valid search into a
    failed one.
    """
    snapshot = getattr(runner, "timing_snapshot", None)
    if not callable(snapshot):
        snapshot = getattr(getattr(runner, "daily_runner", None),
                           "timing_snapshot", None)
    if not callable(snapshot):
        return {}
    try:
        raw = snapshot()
        if not isinstance(raw, Mapping):
            return {}
        # Match SearchWorkspace's strict serialization boundary here so the
        # optional diagnostic cannot make update_progress fail later.
        serializable = json.loads(json.dumps(
            {str(key): value for key, value in raw.items()},
            allow_nan=False,
        ))
    except Exception:  # diagnostic hooks are explicitly fail-open
        return {}
    return serializable if isinstance(serializable, dict) else {}


def _exhaustive_pilot(
    workspace: SearchWorkspace,
    policy: "MonthlySearchPolicy",
    *,
    runner: "CandidateRunner",
    schedules,
    shortlist_ids: Sequence[str],
    pilot_targets: Mapping[str, int],
    pilot_records: Mapping[str, Any],
    compact_pilot: bool,
    phase: str = "pilot",
) -> list[CandidateEvidence]:
    """Simulate every shortlisted candidate. The reference path, unchanged."""
    pilot_evidence: list[CandidateEvidence] = []
    for index, candidate_id in enumerate(shortlist_ids):
        # A broad independent search can contain tens of thousands of
        # parents. Updating the workspace manifest for every additive
        # in-memory reconstruction turns cache hits into a filesystem
        # benchmark, so retain bounded progress writes instead.
        progress_stride = max(1, len(shortlist_ids) // 100)
        if not compact_pilot or index % progress_stride == 0:
            detail = _runner_timing_snapshot(runner)
            detail["candidate_index"] = index
            workspace.update_progress(
                phase,
                completed=index,
                total=len(shortlist_ids),
                detail=detail,
            )
        pilot_evidence.append(_pilot_evidence_for(
            workspace, runner, schedules[candidate_id],
            targets=pilot_targets,
            existing_records=pilot_records.get(candidate_id, []),
            compact_pilot=compact_pilot,
            policy=policy,
        ))
    return pilot_evidence


def _cost_ordered_pilot(
    workspace: SearchWorkspace,
    spec: ClosureSearchSpec,
    policy: "MonthlySearchPolicy",
    *,
    runner: "CandidateRunner",
    schedules,
    shortlist_ids: Sequence[str],
    cost_source: Any,
    pilot_targets: Mapping[str, int],
    pilot_records: Mapping[str, Any],
    compact_pilot: bool,
    disable_early_stop: bool = False,
) -> tuple[list[CandidateEvidence], Any]:
    """Price every candidate, then simulate only the boundary set.

    This is the real cost-first execution: no exhaustive SUMO pass happens
    first, and the candidates above the boundary are never simulated at all.
    The evidence it returns goes to the same unchanged selector the exhaustive
    path uses.

    `disable_early_stop=True` runs the ordered-exhaustive reference instead:
    every candidate in the same ledger order is simulated, none are skipped
    at the band.
    """
    from traffic_sim.simulation import cost_ordered_execution as coe

    def report(phase: str, completed: int, total: int,
               detail: Mapping[str, Any]) -> None:
        if workspace.status == "running":
            workspace.update_progress(
                phase, completed=completed, total=total, detail=dict(detail))

    # --- cost_units / cost_parents -------------------------------------
    ledger_records = _artifact_records(workspace, kind=coe.COST_LEDGER_KIND)
    if ledger_records:
        if len(ledger_records) != 1:
            raise ValueError("workspace has duplicate cost ledgers")
        ledger = coe.CostLedger.from_dict(
            _read_artifact(workspace, ledger_records[0]))
        if ledger.search_content_key != spec.content_key:
            raise ValueError("cost ledger belongs to another search")
        if [item.candidate_id for item in ledger.costs] != list(shortlist_ids):
            raise ValueError(
                "resumed cost ledger does not cover this shortlist")
    else:
        ledger = coe.build_cost_ledger(
            spec,
            [schedules[candidate_id] for candidate_id in shortlist_ids],
            cost_source,
            progress=report,
        )
        _publish_json(
            workspace,
            ledger.to_dict(),
            "cost-ledger.json",
            kind=coe.COST_LEDGER_KIND,
            provenance={
                "search_content_key": spec.content_key,
                "candidate_count": len(ledger.costs),
                "daily_cost_cache_hits": ledger.cache_hits,
                "release_evidence": False,
            },
        )

    # --- health_scan ---------------------------------------------------
    refused = [item for item in ledger.costs if item.cost.disqualified]
    report("health_scan", len(ledger.costs), len(ledger.costs), {
        "costed": len(ledger.costs),
        "cost_total": len(ledger.costs),
        "cache_hits": ledger.cache_hits,
        "deterministically_disqualified": len(refused),
    })

    # --- pilot ---------------------------------------------------------
    cursor_records = _artifact_records(workspace, kind=coe.CURSOR_KIND)
    cursor = None
    priced_by_id = {item.candidate_id: item for item in ledger.costs}
    verified_evidence: dict[str, CandidateEvidence] = {}
    if cursor_records:
        # The LAST published cursor is the position; earlier ones are the
        # history of getting there and are kept for forensics.
        cursor = coe.ExecutionCursor.from_dict(
            _read_artifact(workspace, cursor_records[-1]))
        for candidate_id in cursor.state.verified:
            records = pilot_records.get(candidate_id, [])
            if not records:
                raise ValueError(
                    "cost-ordered resume claims a candidate was verified but "
                    f"its evidence is missing: {candidate_id}")
            # Reconciled on the way back in as well: evidence published by an
            # earlier process must still agree with the ledger this run is
            # ordering by, or the resume is continuing a different search.
            verified_evidence[candidate_id] = coe.reconcile_disruption(
                records[-1][1], priced_by_id[candidate_id])

    # Counting manifest records is sufficient, and deliberately so. A process
    # killed between `publish_artifact`'s file copy and its manifest append
    # leaves an orphan the manifest does not mention — but such a workspace
    # never reaches this line: `verify_search_workspace` refuses any file under
    # `artifacts/` that is not in the ledger, so the workspace fails integrity
    # on load with a message naming the orphan. Scanning the directory here to
    # step over it would be strictly worse: it would resume a workspace whose
    # contents nobody can account for. Pinned by
    # `TestAnOrphanCursorIsRefusedNotSteppedOver`.
    checkpoint_index = len(cursor_records)

    def checkpoint(new_cursor, candidate_id: str,
                   evidence: CandidateEvidence) -> None:
        nonlocal checkpoint_index
        if workspace.status != "running":
            return
        _publish_json(
            workspace,
            new_cursor.to_dict(),
            f"cost-ordered-cursor-{checkpoint_index:05d}.json",
            kind=coe.CURSOR_KIND,
            provenance={
                "search_content_key": spec.content_key,
                "cursor": int(new_cursor.state.cursor),
                "candidate_id": candidate_id,
                "release_evidence": False,
            },
        )
        checkpoint_index += 1

    def verify(candidate_id: str) -> CandidateEvidence:
        evidence = _pilot_evidence_for(
            workspace, runner, schedules[candidate_id],
            targets=pilot_targets,
            existing_records=pilot_records.get(candidate_id, []),
            compact_pilot=compact_pilot,
            policy=policy,
        )
        # The price the ordering used and the price the runner reports must be
        # the same number. Checked on every candidate, not only in a benchmark.
        return coe.reconcile_disruption(evidence, priced_by_id[candidate_id])

    result = coe.run_cost_ordered_execution(
        spec, ledger, policy.pilot,
        verify=verify,
        practical_equivalence_vehicle_hours=(
            policy.finalist.practical_equivalence_vehicle_hours),
        cursor=cursor,
        verified_evidence=verified_evidence,
        checkpoint=checkpoint,
        progress=report,
        disable_early_stop=disable_early_stop,
    )

    # The durable account of what cost ordering actually did. Without it
    # nothing in the workspace or the result distinguishes a cost-ordered run
    # from an exhaustive one, and the stop proof — the argument that the
    # unexamined candidates could not have won — exists only in memory.
    # Deliberately free of wall time and peak RSS: those differ between a run
    # and its resume, and this artifact must reproduce exactly so a re-entered
    # pilot can prove it did not change its mind.
    record = coe.execution_record(
        spec, ledger, result,
        exhaustive_candidate_count=len(shortlist_ids),
        disable_early_stop=disable_early_stop,
    )
    existing_execution = _artifact_records(workspace, kind=coe.EXECUTION_KIND)
    if existing_execution:
        if len(existing_execution) != 1 or _canonical_digest(
                _read_artifact(workspace, existing_execution[0])
        ) != _canonical_digest(record):
            raise ValueError(
                "resumed cost-ordered execution is not deterministic: the "
                "re-entered pilot reports a different saving or stop proof")
    else:
        _publish_json(
            workspace,
            record,
            "cost-ordered-execution.json",
            kind=coe.EXECUTION_KIND,
            provenance={
                "search_content_key": spec.content_key,
                "cost_ordered_sumo_candidates": record[
                    "cost_ordered_sumo_candidates"],
                "sumo_verifications_saved": record["sumo_verifications_saved"],
                "release_evidence": False,
            },
        )
    return list(result.evidence), record


def run_monthly_search(
    spec: ClosureSearchSpec,
    policy: MonthlySearchPolicy,
    *,
    runner: CandidateRunner,
    screen_builder: ScreenBuilder,
    root: Path = DEFAULT_ROOT,
    cost_source: Any = None,
    disable_early_stop: bool = False,
) -> dict[str, Any]:
    """Run or resume one monthly search through a robust mesoscopic decision.

    `cost_source` switches the pilot phase from exhaustive to COST-ORDERED
    execution: every candidate is priced from calibrated routes first, and SUMO
    then runs only for the candidates the ordering boundary requires. It is the
    real execution, not a replay — the exhaustive path stays available and
    unchanged as the reference by simply not passing one.

    `disable_early_stop=True` requires `cost_source` and produces the
    ORDERED-EXHAUSTIVE reference run: the same cost ledger and candidate
    order as a real cost-ordered execution, but every candidate is verified
    regardless of the band. This is the apples-to-apples baseline a
    structural-speedup claim needs — see
    `cost_ordered_search.run_cost_ordered_search`'s docstring.
    """
    if disable_early_stop and cost_source is None:
        raise ValueError(
            "disable_early_stop requires cost_source: it only has meaning "
            "for a cost-ordered execution")
    spec = ClosureSearchSpec.from_dict(spec.to_dict())
    policy = MonthlySearchPolicy.from_dict(policy.to_dict())
    workspace, _ = open_search_workspace(spec, root=root)
    phase = "policy"
    try:
        if workspace.status == "running":
            workspace.update_progress(phase)
        _existing_policy(workspace, policy)

        phase = "preflight"
        if workspace.status == "running":
            # The exact size is already known — the product CLI computes it
            # before this function is reached — so the user sees what the run
            # is about to enumerate rather than a silent pause.
            workspace.update_progress(phase)

        phase = "enumerate"
        if workspace.status == "running":
            workspace.update_progress(phase)
        schedules = _candidate_ledger(workspace, spec)
        if workspace.status == "running":
            workspace.update_progress(
                phase,
                completed=len(schedules),
                total=len(schedules),
                detail={
                    "parent_schedules": len(schedules),
                    "ledger_schema": schedules.schema,
                    "ledger_version": schedules.version,
                },
            )

        phase = "screen"
        if workspace.status == "running":
            workspace.update_progress(phase)
        screening = _screening_artifact(
            workspace,
            spec,
            schedules,
            screen_builder,
        )
        shortlist_ids = [
            str(item["schedule_id"])
            for item in screening["shortlist"]["entries"]
        ] if screening.get("kind") == "monthly_closure_proxy_screening" else []

        if screening.get("kind") == "monthly_closure_screening_checkpoint":
            state = dict(screening["budget_state"])
            workspace.update_progress(
                "paused_budget",
                completed=int(state.get("parent_schedules", 0)),
                total=len(schedules),
                detail={
                    "parent_schedules": int(
                        state.get("parent_schedules", 0)),
                    "daily_units": int(state.get("daily_units", 0)),
                    "leg_daily_units": int(
                        state.get("leg_daily_units", 0)),
                    "resume_token": screening["resume_token"],
                    "stopped_by": state.get("stopped_by"),
                    "message": screening.get("budget_message"),
                },
            )
            return {
                "schema_version": SCHEMA_VERSION,
                "kind": "monthly_closure_search_pause",
                "status": "paused",
                "search_id": spec.search_id,
                "search_content_key": spec.content_key,
                "resume_token": screening["resume_token"],
                "budget": screening.get("budget"),
                "budget_state": state,
                "message": screening.get("budget_message"),
            }

        phase = "prepare_backend"
        if workspace.status == "running":
            workspace.update_progress(
                phase,
                completed=0,
                total=len(shortlist_ids),
            )
        _prepare_shortlist(runner, schedules, shortlist_ids)
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
        # Compaction exists because an EXHAUSTIVE independent pilot writes one
        # JSON file per parent — tens of thousands of them — and the parent
        # evidence is a deterministic sum of immutable daily cache entries
        # anyway. Cost-first execution simulates only the boundary set, so the
        # file count is bounded by the finalists and the objection does not
        # apply. It must not apply: without those files a resume cannot prove
        # the cursor's verified prefix, and every restart of a real
        # cost-ordered search fails closed on "evidence is missing" — which is
        # exactly the durability the cursor exists to provide.
        compact_pilot = (
            getattr(runner, "compact_pilot_artifacts", False) is True
            and cost_source is None
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
        cost_ordered_result: Any = None
        if cost_source is not None:
            if policy.objective_method != "closure_cost_v1":
                raise ValueError(
                    "cost-ordered execution requires the closure_cost_v1 "
                    "objective; the legacy time-loss key is not deterministic "
                    "and cannot order candidates before simulation")
            pilot_evidence, cost_ordered_result = _cost_ordered_pilot(
                workspace,
                spec,
                policy,
                runner=runner,
                schedules=schedules,
                shortlist_ids=shortlist_ids,
                cost_source=cost_source,
                pilot_targets=pilot_targets,
                pilot_records=pilot_records,
                compact_pilot=compact_pilot,
                disable_early_stop=disable_early_stop,
            )
        else:
            pilot_evidence = _exhaustive_pilot(
                workspace,
                policy,
                runner=runner,
                schedules=schedules,
                shortlist_ids=shortlist_ids,
                pilot_targets=pilot_targets,
                pilot_records=pilot_records,
                compact_pilot=compact_pilot,
                phase=phase,
            )

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
        workspace.update_progress(
            phase,
            detail=_runner_timing_snapshot(runner),
        )
        result = _final_result(
            spec,
            policy,
            schedules,
            screening,
            pilot_selection=pilot_payload,
            decision=decision_payload,
            backend_provenance=backend_provenance,
            cost_ordered_execution=cost_ordered_result,
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
