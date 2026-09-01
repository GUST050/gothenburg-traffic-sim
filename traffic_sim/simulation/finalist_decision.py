"""Fail-closed robust decisions for matched SUMO closure finalists.

This module deliberately does not run SUMO.  It is the pure decision seam
between a simulation orchestrator and the eventual API/UI:

* q10/q50/q90 are kept as separate epistemic demand variants;
* every observation is an explicit candidate/baseline pair with the same
  seed and provenance;
* hard-gated candidates are removed before statistics are calculated;
* the score is the worst-variant upper simultaneous 95% confidence bound;
* an answer may be a unique winner, a practical tie, inconclusive, or no
  viable closure;
* microscopic evidence is a separate conditional confirmation and is never
  folded into the mesoscopic score.

The caller owns adaptive SUMO execution.  ``DecisionResult.next_runs`` says
exactly which candidate/variant pairs still need matched repetitions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date
import math
from statistics import mean, median, stdev
from typing import Any, Mapping, Sequence


def _student_t_ppf(probability: float, degrees_of_freedom: int) -> float:
    """Student-t inverse CDF, importing SciPy only when one is needed.

    The call and its numerics are unchanged — this is the same
    ``scipy.stats.t.ppf`` the module always used.  What changed is WHEN SciPy
    loads.

    Importing it at module scope cost 81.7 MiB of resident memory in every
    process that so much as touched this module, and this module is imported
    transitively by `independent_daily` and therefore by the closure search's
    enumeration, preflight, ledger and cost-ordering phases — none of which
    computes a confidence interval.  Measured on the frozen PR C comparison,
    that single import was the whole reason a 720-hour streaming run whose own
    enumeration costs 1.98 MiB reported a ~102 MiB process total and could not
    meet the plan's 64 MiB gate.

    A confidence interval still pays for SciPy, once, exactly where it is
    computed.
    """
    from scipy.stats import t as student_t  # noqa: PLC0415

    return float(student_t.ppf(probability, degrees_of_freedom))


SCHEMA_VERSION = 1
DECISION_METHOD = "paired_worst_variant_ucb_v1"
CLOSURE_COST_DECISION_METHOD = "deterministic_worst_variant_closure_cost_v1"

#: Versioned schema tag for `TimeoutIdentity` records. v1/v2 (retired, never
#: emitted by any released code) were bare colon-encoded strings — first a
#: positionless `"{variant}:{seed}:attempt1:threshold{s}s"`, then a
#: self-describing but still-a-string `"timeout_v2:candidate=...:..."`.
#: Neither was a validated record: a reader could not distinguish a
#: malformed or truncated string from a real one without re-parsing it, and
#: nothing stopped an old bare string from round-tripping through JSON
#: forever. v3 is a real object (see `TimeoutIdentity`) that validates its
#: own fields on construction AND on every deserialization, so a legacy
#: string or a record missing a required field fails closed instead of
#: silently reading back as valid.
TIMEOUT_IDENTITY_SCHEMA = "timeout_v3"

#: Historical one-attempt protocol retained so existing immutable v3 evidence
#: remains readable. New product runs use the two-tier protocol below.
RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD = (
    "single_attempt_fixed_threshold_no_retry_v1"
)

#: Product recovery protocol for exact monthly SUMO observations.  The first
#: launch retains the established 300 s fast-fail boundary.  Only a launch
#: that reaches that boundary is repeated, with byte-identical simulation
#: inputs/resources and a larger registered wall-clock allowance.  A timeout
#: record is published only if the recovery attempt also expires.
RETRY_PROTOCOL_TWO_TIER_EXACT = "two_tier_exact_wallclock_v1"
SUPPORTED_TIMEOUT_RETRY_PROTOCOLS = frozenset({
    RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
    RETRY_PROTOCOL_TWO_TIER_EXACT,
})


@dataclass(frozen=True, order=True)
class TimeoutIdentity:
    """One validated, versioned record of a SUMO wall-clock timeout.

    Every field a reader needs to answer "which run, in which search,
    produced this undecided outcome, and under which protocol" — not encoded
    into a string a downstream reader has to re-parse, but real typed fields
    validated once here. Frozen and orderable so it stays usable exactly
    where the old string was: a hashable, sortable member of
    `CandidateEvidence.timeout_undecided`.

    `work_date` is the schedule's own `first_work_date` at the point the
    timeout was recorded. For an independent daily unit (day_count == 1)
    this is unambiguously the single day that timed out. For a multi-day
    warm-started schedule it identifies the SCHEDULE the timeout belongs to,
    not a specific day within it — a warm run's SUMO process spans the whole
    schedule, so no finer day-level attribution is available at the point a
    timeout is observed.
    """

    schema: str
    candidate_id: str
    work_date: str
    search_content_key: str
    variant: str
    seed: int
    attempt: int
    threshold_s: float
    retry_protocol: str
    search_provenance_key: str

    def __post_init__(self) -> None:
        string_fields = {
            "schema": self.schema,
            "candidate_id": self.candidate_id,
            "work_date": self.work_date,
            "search_content_key": self.search_content_key,
            "variant": self.variant,
            "retry_protocol": self.retry_protocol,
            "search_provenance_key": self.search_provenance_key,
        }
        malformed_strings = [
            name for name, value in string_fields.items()
            if not isinstance(value, str) or not value.strip()
        ]
        if malformed_strings:
            raise ValueError(
                "timeout identity string fields must be non-empty native "
                f"strings: {malformed_strings}"
            )
        if self.schema != TIMEOUT_IDENTITY_SCHEMA:
            raise ValueError(
                f"unsupported timeout identity schema: {self.schema!r}"
            )
        try:
            parsed_work_date = date.fromisoformat(self.work_date)
        except ValueError as error:
            raise ValueError(
                "timeout identity work_date must be an ISO calendar date"
            ) from error
        if parsed_work_date.isoformat() != self.work_date:
            raise ValueError(
                "timeout identity work_date must use canonical YYYY-MM-DD"
            )
        if self.variant not in DEMAND_VARIANTS:
            raise ValueError("timeout identity variant must be q10/q50/q90")
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or self.seed < 0
        ):
            raise ValueError("timeout identity seed must be a non-negative int")
        if (
            isinstance(self.attempt, bool)
            or not isinstance(self.attempt, int)
            or self.attempt < 1
        ):
            raise ValueError("timeout identity attempt must be a positive int")
        if (
            isinstance(self.threshold_s, bool)
            or not isinstance(self.threshold_s, (int, float))
            or not math.isfinite(self.threshold_s)
            or self.threshold_s <= 0
        ):
            raise ValueError("timeout identity threshold_s must be positive")
        if self.retry_protocol not in SUPPORTED_TIMEOUT_RETRY_PROTOCOLS:
            raise ValueError(
                "timeout identity retry_protocol is unsupported: "
                f"{self.retry_protocol!r}"
            )
        if (
            self.retry_protocol == RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD
            and self.attempt != 1
        ):
            raise ValueError(
                "single-attempt timeout identities must record attempt 1"
            )
        if (
            self.retry_protocol == RETRY_PROTOCOL_TWO_TIER_EXACT
            and self.attempt != 2
        ):
            raise ValueError(
                "two-tier timeout identities must record the terminal attempt 2"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "candidate_id": self.candidate_id,
            "work_date": self.work_date,
            "search_content_key": self.search_content_key,
            "variant": self.variant,
            "seed": self.seed,
            "attempt": self.attempt,
            "threshold_s": self.threshold_s,
            "retry_protocol": self.retry_protocol,
            "search_provenance_key": self.search_provenance_key,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "TimeoutIdentity":
        """Reconstruct a record, failing closed on anything not a valid v3.

        A bare string (the retired v1/v2 wire format) is exactly the shape
        this must reject: `isinstance(raw, Mapping)` is False for a `str`,
        so a legacy entry raises here instead of silently deserializing as
        something else. This is intentional and is never relaxed to "coerce
        old strings" — old artifacts are read-only history, not rewritten,
        and a reader that cannot validate them must refuse them.
        """
        if not isinstance(raw, Mapping):
            raise ValueError(
                "timeout identity record must be an object with named "
                f"fields, not {raw!r} (a legacy plain string is not "
                "accepted)"
            )
        required = {
            "schema", "candidate_id", "work_date", "search_content_key",
            "variant", "seed", "attempt", "threshold_s", "retry_protocol",
            "search_provenance_key",
        }
        actual = set(raw)
        missing = sorted(required - actual)
        extra = sorted(actual - required, key=str)
        if missing:
            raise ValueError(
                f"timeout identity record is missing fields: {missing}"
            )
        if extra:
            raise ValueError(
                f"timeout identity record has unknown fields: {extra}"
            )
        string_fields = (
            "schema", "candidate_id", "work_date", "search_content_key",
            "variant", "retry_protocol", "search_provenance_key",
        )
        wrong_strings = [
            key for key in string_fields if not isinstance(raw[key], str)
        ]
        if wrong_strings:
            raise ValueError(
                "timeout identity record string fields must use native string "
                f"values: {wrong_strings}"
            )
        if (isinstance(raw["seed"], bool)
                or not isinstance(raw["seed"], int)):
            raise ValueError(
                "timeout identity record seed must be a native integer"
            )
        if (isinstance(raw["attempt"], bool)
                or not isinstance(raw["attempt"], int)):
            raise ValueError(
                "timeout identity record attempt must be a native integer"
            )
        if (isinstance(raw["threshold_s"], bool)
                or not isinstance(raw["threshold_s"], (int, float))):
            raise ValueError(
                "timeout identity record threshold_s must be a native number"
            )
        return cls(
            schema=raw["schema"],
            candidate_id=raw["candidate_id"],
            work_date=raw["work_date"],
            search_content_key=raw["search_content_key"],
            variant=raw["variant"],
            seed=raw["seed"],
            attempt=raw["attempt"],
            threshold_s=raw["threshold_s"],
            retry_protocol=raw["retry_protocol"],
            search_provenance_key=raw["search_provenance_key"],
        )


RANKING_OBJECTIVES = frozenset({
    "auto",
    "legacy_time_loss_v1",
    "closure_cost_v1",
})
DEMAND_VARIANTS = ("q10", "q50", "q90")
_DECISION_STATUSES = frozenset(
    {"unique_winner", "tie", "inconclusive", "no_viable"}
)


def _finite(value: float, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


@dataclass(frozen=True)
class FinalistPolicy:
    """Pre-registered repetition and decision tolerances.

    ``absolute_precision_floor_s`` and ``max_repetitions`` intentionally
    have no defaults.  The release process must freeze them from a named
    benchmark rather than silently inheriting a convenient value.
    ``relative_precision`` is the maximum confidence half-width as a share
    of the paired mean impact; the absolute floor handles impacts near zero.
    """

    absolute_precision_floor_s: float
    practical_equivalence_s: float
    max_repetitions: int
    #: Equivalence band for the DETERMINISTIC closure objective, in vehicle-
    #: hours. practical_equivalence_s is a noise allowance for a sampled
    #: quantity and does not transfer: seconds are not hours, and there is no
    #: sampling error to allow for here. Defaults to 0.0 — with no noise, any
    #: difference is real — so a release must freeze a deliberate value if it
    #: wants trivial differences treated as ties.
    practical_equivalence_vehicle_hours: float = 0.0
    initial_repetitions: int = 4
    confidence_level: float = 0.95
    relative_precision: float = 0.05
    variants: tuple[str, ...] = DEMAND_VARIANTS
    micro_finalist_limit: int = 3

    def __post_init__(self) -> None:
        if _finite(
            self.absolute_precision_floor_s, "absolute_precision_floor_s"
        ) <= 0:
            raise ValueError("absolute_precision_floor_s must be above zero")
        if _finite(self.practical_equivalence_s, "practical_equivalence_s") < 0:
            raise ValueError("practical_equivalence_s cannot be negative")
        if _finite(
            self.practical_equivalence_vehicle_hours,
            "practical_equivalence_vehicle_hours",
        ) < 0:
            raise ValueError(
                "practical_equivalence_vehicle_hours cannot be negative"
            )
        if (
            isinstance(self.initial_repetitions, bool)
            or self.initial_repetitions < 2
        ):
            raise ValueError("initial_repetitions must be at least two")
        if (
            isinstance(self.max_repetitions, bool)
            or self.max_repetitions < self.initial_repetitions
        ):
            raise ValueError(
                "max_repetitions must be at least initial_repetitions"
            )
        if not 0 < _finite(self.confidence_level, "confidence_level") < 1:
            raise ValueError("confidence_level must be between zero and one")
        if not 0 < _finite(self.relative_precision, "relative_precision") < 1:
            raise ValueError("relative_precision must be between zero and one")
        if (
            not self.variants
            or len(set(self.variants)) != len(self.variants)
            or any(variant not in DEMAND_VARIANTS for variant in self.variants)
        ):
            raise ValueError("variants must be unique q10/q50/q90 identities")
        if (
            isinstance(self.micro_finalist_limit, bool)
            or not 2 <= self.micro_finalist_limit <= 3
        ):
            raise ValueError("micro_finalist_limit must be two or three")


@dataclass(frozen=True)
class PairedObservation:
    """One matched no-closure/candidate SUMO result."""

    candidate_id: str
    demand_variant: str
    seed: int
    baseline_time_loss_s: float
    candidate_time_loss_s: float
    matched_baseline_id: str
    provenance_key: str
    simulation_mode: str = "meso"

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if self.demand_variant not in DEMAND_VARIANTS:
            raise ValueError("demand_variant must be q10, q50, or q90")
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or self.seed < 0
        ):
            raise ValueError("seed must be a non-negative integer")
        _finite(self.baseline_time_loss_s, "baseline_time_loss_s")
        _finite(self.candidate_time_loss_s, "candidate_time_loss_s")
        if not self.matched_baseline_id or not self.provenance_key:
            raise ValueError("matched baseline and provenance are required")
        if self.simulation_mode != "meso":
            raise ValueError("finalist ranking observations must be mesoscopic")

    @property
    def delta_time_loss_s(self) -> float:
        return self.candidate_time_loss_s - self.baseline_time_loss_s


# Ranking objective (2026-08-05). See closure_ranking for why simulated
# delay was replaced: on this network delta_time_loss_s is noise (a real
# closure gave +0.050 s and -0.100 s per arm; congestion feedback converged
# at 0.0% change), while displaced vehicles and detour cost separate
# candidates by orders of magnitude.
#
# CONSEQUENCE, stated because it is easy to miss: the new objective is
# DETERMINISTIC. It has no sampling error, so for candidates ranked on it the
# confidence apparatus below (robust_lower/upper_95_s, precision_met,
# repetition_cap_reached, next_runs) is inert by construction — intervals are
# zero-width, precision is always met and more repetitions never help. It is
# retained, not deleted, because candidates WITHOUT disruption evidence still
# fall back to the time-loss path.
from traffic_sim.simulation.closure_ranking import (  # noqa: E402
    ClosureCost, rank_closures, worst_variant_cost)


@dataclass(frozen=True, order=True)
class CanonicalObservationDigest:
    """Identity and digest of one complete canonical SUMO observation."""

    candidate_id: str
    work_date: str
    variant: str
    seed: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError("canonical observation candidate_id is required")
        if not isinstance(self.work_date, str):
            raise ValueError("canonical observation work_date must be a string")
        try:
            parsed = date.fromisoformat(self.work_date)
        except ValueError as error:
            raise ValueError(
                "canonical observation work_date must be an ISO date"
            ) from error
        if parsed.isoformat() != self.work_date:
            raise ValueError("canonical observation work_date is not canonical")
        if self.variant not in DEMAND_VARIANTS:
            raise ValueError("canonical observation variant is invalid")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("canonical observation seed must be a non-negative int")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("canonical observation sha256 is invalid")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> "CanonicalObservationDigest":
        if not isinstance(raw, Mapping):
            raise ValueError("canonical observation digest must be an object")
        expected = {"candidate_id", "work_date", "variant", "seed", "sha256"}
        if set(raw) != expected:
            raise ValueError("canonical observation digest fields are invalid")
        return cls(**dict(raw))


@dataclass(frozen=True)
class CandidateEvidence:
    candidate_id: str
    observations: tuple[PairedObservation, ...] = ()
    hard_failures: tuple[str, ...] = ()
    #: One closure_disruption() record per demand variant, or () when the
    #: candidate was evaluated before this objective existed.
    disruption: tuple[Mapping, ...] = ()
    #: Validated `TimeoutIdentity` records for SUMO runs that hit the frozen
    #: wall-clock timeout for this candidate. A timeout is deliberately NOT
    #: folded into ``hard_failures``: no completed simulation exists from
    #: which to infer a traffic-health failure. This field lets a reader
    #: distinguish "we don't know" from "genuinely disqualified" — see
    #: ``has_undecided_timeout`` and ``pilot_selection.
    #: select_pilot_finalists``, which refuses to declare a decision while
    #: this is non-empty rather than silently treating a timed-out candidate
    #: as if it never existed.
    timeout_undecided: tuple[TimeoutIdentity, ...] = ()
    #: Complete canonical monthly-observation payloads are intentionally not
    #: duplicated into every cache/artifact. Their full canonical JSON digest
    #: is retained with the launch identity so semantic comparison can prove
    #: that recovery, feasibility, health, baseline and candidate metrics all
    #: matched, rather than comparing only the reduced ranking observation.
    canonical_observation_digests: tuple[CanonicalObservationDigest, ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if any(
            observation.candidate_id != self.candidate_id
            for observation in self.observations
        ):
            raise ValueError("observation candidate_id mismatch")
        if any(not reason for reason in self.hard_failures):
            raise ValueError("hard failure names cannot be empty")
        if any(
            not isinstance(item, TimeoutIdentity)
            for item in self.timeout_undecided
        ):
            raise ValueError(
                "timeout_undecided entries must be validated TimeoutIdentity "
                "records, not bare strings or other values"
            )
        if any(
            not isinstance(item, CanonicalObservationDigest)
            for item in self.canonical_observation_digests
        ):
            raise ValueError(
                "canonical_observation_digests entries must be validated records"
            )
        identities = [
            (item.candidate_id, item.work_date, item.variant, item.seed)
            for item in self.canonical_observation_digests
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("canonical observation digest identity is duplicated")
        # NOTE: a timeout identity's own `candidate_id` is deliberately NOT
        # required to equal `self.candidate_id` here. `independent_daily.
        # aggregate_daily_evidence` rolls several daily units' timeouts up
        # into one PARENT `CandidateEvidence`; each identity correctly names
        # the daily unit that actually timed out, not the parent schedule
        # that aggregates it.

    @property
    def eligible(self) -> bool:
        return not self.hard_failures

    @property
    def has_undecided_timeout(self) -> bool:
        return bool(self.timeout_undecided)


def paired_candidate_evidence(
    candidate_id: str,
    *,
    baseline_records: Sequence[Mapping[str, Any]],
    candidate_records: Sequence[Mapping[str, Any]],
    matched_baseline_id: str,
    provenance_key: str,
    hard_failures: Sequence[str] = (),
    disruption: Sequence[Mapping[str, Any]] = (),
) -> CandidateEvidence:
    """Build explicit paired evidence from SUMO replication records.

    Ordering is irrelevant.  Both sides must contain exactly the same unique
    ``(demand_variant, seed)`` identities; positional zip/pooling is forbidden.
    """

    def index_records(
        records: Sequence[Mapping[str, Any]],
        label: str,
    ) -> dict[tuple[str, int], float]:
        indexed: dict[tuple[str, int], float] = {}
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError(f"{label} replication record must be an object")
            variant = str(record.get("demand_variant", ""))
            seed = record.get("seed")
            if variant not in DEMAND_VARIANTS:
                raise ValueError(
                    f"{label} replication demand_variant must be q10/q50/q90"
                )
            if (
                isinstance(seed, bool)
                or not isinstance(seed, int)
                or seed < 0
            ):
                raise ValueError(
                    f"{label} replication seed must be non-negative"
                )
            identity = (variant, seed)
            if identity in indexed:
                raise ValueError(
                    f"{label} has duplicate replication identity {identity}"
                )
            indexed[identity] = _finite(
                record.get("total_time_loss_s"),
                f"{label} total_time_loss_s",
            )
        return indexed

    baseline = index_records(baseline_records, "baseline")
    candidate = index_records(candidate_records, "candidate")
    if set(baseline) != set(candidate):
        missing_candidate = sorted(set(baseline) - set(candidate))
        missing_baseline = sorted(set(candidate) - set(baseline))
        raise ValueError(
            "candidate and baseline replication identities differ: "
            f"missing_candidate={missing_candidate}, "
            f"missing_baseline={missing_baseline}"
        )
    observations = tuple(
        PairedObservation(
            candidate_id=candidate_id,
            demand_variant=variant,
            seed=seed,
            baseline_time_loss_s=baseline[(variant, seed)],
            candidate_time_loss_s=candidate[(variant, seed)],
            matched_baseline_id=matched_baseline_id,
            provenance_key=provenance_key,
        )
        for variant, seed in sorted(
            baseline,
            key=lambda identity: (
                DEMAND_VARIANTS.index(identity[0]),
                identity[1],
            ),
        )
    )
    return CandidateEvidence(
        candidate_id=candidate_id,
        observations=observations,
        hard_failures=tuple(str(reason) for reason in hard_failures),
        disruption=tuple(disruption),
    )


@dataclass(frozen=True)
class VariantStatistics:
    demand_variant: str
    n_pairs: int
    seeds: tuple[int, ...]
    mean_delta_s: float | None
    median_delta_s: float | None
    lower_95_s: float | None
    upper_95_s: float | None
    half_width_s: float | None
    precision_target_s: float | None
    precision_met: bool
    repetition_cap_reached: bool


@dataclass(frozen=True)
class CandidateStatistics:
    candidate_id: str
    eligible: bool
    hard_failures: tuple[str, ...]
    variants: tuple[VariantStatistics, ...]
    robust_point_s: float | None
    robust_lower_95_s: float | None
    robust_upper_95_s: float | None
    worst_variant: str | None
    all_variants_ready: bool
    precision_met: bool
    provenance_key: str | None
    #: Worst-variant closure cost, or None when no disruption evidence exists.
    closure_cost: ClosureCost | None = None
    timeout_undecided: tuple[TimeoutIdentity, ...] = ()


@dataclass(frozen=True)
class RunRequest:
    candidate_id: str
    demand_variant: str
    repetitions_to_add: int
    completed_repetitions: int

    def __post_init__(self) -> None:
        if not self.candidate_id or self.demand_variant not in DEMAND_VARIANTS:
            raise ValueError("run request identity is invalid")
        if (
            isinstance(self.repetitions_to_add, bool)
            or self.repetitions_to_add < 1
            or isinstance(self.completed_repetitions, bool)
            or self.completed_repetitions < 0
        ):
            raise ValueError("run request repetition counts are invalid")


@dataclass(frozen=True)
class DecisionResult:
    status: str
    winner_id: str | None
    tie_ids: tuple[str, ...]
    reason: str
    candidates: tuple[CandidateStatistics, ...]
    next_runs: tuple[RunRequest, ...]
    policy: FinalistPolicy
    confidence_level: float
    simultaneous_comparisons: int
    method: str = DECISION_METHOD
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in _DECISION_STATUSES:
            raise ValueError("invalid decision status")
        if self.status == "unique_winner":
            if not self.winner_id or self.tie_ids:
                raise ValueError("unique_winner requires exactly one winner")
        elif self.status == "tie":
            if self.winner_id is not None or len(set(self.tie_ids)) < 2:
                raise ValueError("tie requires at least two unique candidates")
        elif self.winner_id is not None or self.tie_ids:
            raise ValueError(
                "inconclusive/no_viable cannot carry a winner or tie set"
            )
        if self.next_runs and self.status != "inconclusive":
            raise ValueError("only an inconclusive decision can request runs")
        if not 0 < self.confidence_level < 1:
            raise ValueError("decision confidence level is invalid")
        if self.confidence_level != self.policy.confidence_level:
            raise ValueError("decision confidence does not match its policy")
        if self.simultaneous_comparisons < 1:
            raise ValueError("simultaneous comparison count is invalid")

    def to_dict(self) -> dict:
        return asdict(self)


def _closure_cost_for(candidate: CandidateEvidence) -> ClosureCost | None:
    """Reduce a candidate's per-variant disruption records to one cost.

    Returns None when the candidate has no disruption evidence, which keeps
    pre-objective candidates on the legacy time-loss path instead of silently
    ranking them as costless.
    """
    if not getattr(candidate, "disruption", ()):
        return None
    return worst_variant_cost(candidate.candidate_id, list(candidate.disruption))


def _validate_candidate_observations(
    candidate: CandidateEvidence,
    policy: FinalistPolicy,
) -> dict[str, list[PairedObservation]]:
    grouped = {variant: [] for variant in policy.variants}
    seen: set[tuple[str, int]] = set()
    provenance_keys: set[str] = set()
    baseline_ids: set[str] = set()
    for observation in candidate.observations:
        if observation.demand_variant not in grouped:
            raise ValueError(
                f"{candidate.candidate_id}: undeclared demand variant "
                f"{observation.demand_variant}"
            )
        pair = (observation.demand_variant, observation.seed)
        if pair in seen:
            raise ValueError(
                f"{candidate.candidate_id}: duplicate variant/seed pair {pair}"
            )
        seen.add(pair)
        provenance_keys.add(observation.provenance_key)
        baseline_ids.add(observation.matched_baseline_id)
        grouped[observation.demand_variant].append(observation)
    if len(provenance_keys) > 1:
        raise ValueError(
            f"{candidate.candidate_id}: incompatible scenario provenance"
        )
    if len(baseline_ids) > 1:
        raise ValueError(
            f"{candidate.candidate_id}: candidate spans multiple matched "
            "baseline envelopes"
        )
    for observations in grouped.values():
        observations.sort(key=lambda item: item.seed)
    return grouped


def _cross_candidate_provenance(
    candidates: Sequence[CandidateEvidence],
    policy: FinalistPolicy,
) -> None:
    """Require one study provenance and exact baselines inside each envelope.

    A monthly search compares schedules on different calendar dates.  Those
    schedules necessarily have different no-closure traffic and therefore
    different matched baseline IDs and values.  Common-random-number pairing
    is required *within* an envelope/baseline group, not across unrelated
    dates.  The study provenance still has to be identical for every
    finalist so network, code, policy and demand-release semantics cannot be
    mixed.
    """
    grouped: dict[
        str,
        dict[str, dict[str, list[PairedObservation]]],
    ] = {}
    study_provenance: set[str] = set()
    for candidate in candidates:
        if not candidate.eligible:
            continue
        candidate_grouped = _validate_candidate_observations(candidate, policy)
        baseline_ids = {
            observation.matched_baseline_id
            for observation in candidate.observations
        }
        if not baseline_ids:
            continue
        baseline_id = next(iter(baseline_ids))
        grouped.setdefault(baseline_id, {})[
            candidate.candidate_id
        ] = candidate_grouped
        for observation in candidate.observations:
            study_provenance.add(observation.provenance_key)
    if len(study_provenance) > 1:
        raise ValueError("finalists do not share compatible scenario provenance")
    for baseline_id, by_candidate in grouped.items():
        reference: dict[
            tuple[str, int],
            tuple[int, str, float],
        ] = {}
        for candidate_id, candidate_grouped in by_candidate.items():
            for variant in policy.variants:
                for repetition, observation in enumerate(
                    candidate_grouped[variant]
                ):
                    pair = (variant, repetition)
                    identity = (
                        observation.seed,
                        observation.provenance_key,
                        observation.baseline_time_loss_s,
                    )
                    previous = reference.setdefault(pair, identity)
                    if previous != identity:
                        raise ValueError(
                            "finalists in the same envelope do not share "
                            "the same matched baseline/common seed "
                            f"for {(baseline_id, *pair)}; mismatch at "
                            f"{candidate_id}"
                        )


def _variant_statistics(
    demand_variant: str,
    observations: Sequence[PairedObservation],
    policy: FinalistPolicy,
    *,
    simultaneous_comparisons: int,
) -> VariantStatistics:
    n_pairs = len(observations)
    cap = n_pairs >= policy.max_repetitions
    if n_pairs < policy.initial_repetitions:
        return VariantStatistics(
            demand_variant=demand_variant,
            n_pairs=n_pairs,
            seeds=tuple(item.seed for item in observations),
            mean_delta_s=None,
            median_delta_s=None,
            lower_95_s=None,
            upper_95_s=None,
            half_width_s=None,
            precision_target_s=None,
            precision_met=False,
            repetition_cap_reached=cap,
        )

    deltas = [item.delta_time_loss_s for item in observations]
    average = mean(deltas)
    center = median(deltas)
    precision_target = max(
        policy.absolute_precision_floor_s,
        policy.relative_precision * abs(average),
    )
    if len(set(deltas)) == 1:
        half_width = 0.0
    else:
        # Bonferroni simultaneous intervals control family-wise coverage
        # across every eligible candidate × demand variant.  This fulfils
        # the plan's simultaneous-comparison requirement without repeated
        # unadjusted pairwise tests.
        family_alpha = 1.0 - policy.confidence_level
        point_alpha = family_alpha / max(1, simultaneous_comparisons)
        critical = _student_t_ppf(1.0 - point_alpha / 2.0, n_pairs - 1)
        half_width = critical * stdev(deltas) / math.sqrt(n_pairs)
    return VariantStatistics(
        demand_variant=demand_variant,
        n_pairs=n_pairs,
        seeds=tuple(item.seed for item in observations),
        mean_delta_s=average,
        median_delta_s=center,
        lower_95_s=average - half_width,
        upper_95_s=average + half_width,
        half_width_s=half_width,
        precision_target_s=precision_target,
        precision_met=half_width <= precision_target,
        repetition_cap_reached=cap,
    )


def _candidate_statistics(
    candidate: CandidateEvidence,
    policy: FinalistPolicy,
    *,
    simultaneous_comparisons: int,
) -> CandidateStatistics:
    if not candidate.eligible:
        return CandidateStatistics(
            candidate_id=candidate.candidate_id,
            eligible=False,
            hard_failures=tuple(sorted(set(candidate.hard_failures))),
            variants=(),
            robust_point_s=None,
            robust_lower_95_s=None,
            robust_upper_95_s=None,
            worst_variant=None,
            all_variants_ready=False,
            precision_met=False,
            provenance_key=None,
            closure_cost=_closure_cost_for(candidate),
            timeout_undecided=tuple(sorted(set(candidate.timeout_undecided))),
        )
    grouped = _validate_candidate_observations(candidate, policy)
    variants = tuple(
        _variant_statistics(
            variant,
            grouped[variant],
            policy,
            simultaneous_comparisons=simultaneous_comparisons,
        )
        for variant in policy.variants
    )
    ready = all(item.mean_delta_s is not None for item in variants)
    if not ready:
        robust_point = robust_lower = robust_upper = None
        worst_variant = None
    else:
        worst = max(
            variants,
            key=lambda item: (
                float(item.upper_95_s),
                policy.variants.index(item.demand_variant),
            ),
        )
        robust_point = max(float(item.mean_delta_s) for item in variants)
        robust_lower = max(float(item.lower_95_s) for item in variants)
        robust_upper = max(float(item.upper_95_s) for item in variants)
        worst_variant = worst.demand_variant
    return CandidateStatistics(
        candidate_id=candidate.candidate_id,
        eligible=True,
        hard_failures=(),
        variants=variants,
        robust_point_s=robust_point,
        robust_lower_95_s=robust_lower,
        robust_upper_95_s=robust_upper,
        worst_variant=worst_variant,
        closure_cost=_closure_cost_for(candidate),
        all_variants_ready=ready,
        precision_met=ready and all(item.precision_met for item in variants),
        provenance_key=(
            candidate.observations[0].provenance_key
            if candidate.observations
            else None
        ),
    )


def _next_runs(
    candidates: Sequence[CandidateStatistics],
    policy: FinalistPolicy,
) -> tuple[RunRequest, ...]:
    requests = []
    for candidate in candidates:
        if not candidate.eligible:
            continue
        for variant in candidate.variants:
            if variant.precision_met or variant.repetition_cap_reached:
                continue
            target = (
                policy.initial_repetitions
                if variant.n_pairs < policy.initial_repetitions
                else min(policy.max_repetitions, variant.n_pairs + 1)
            )
            requests.append(
                RunRequest(
                    candidate_id=candidate.candidate_id,
                    demand_variant=variant.demand_variant,
                    repetitions_to_add=target - variant.n_pairs,
                    completed_repetitions=variant.n_pairs,
                )
            )
    return tuple(requests)


def decide_finalists(
    evidence: Sequence[CandidateEvidence],
    policy: FinalistPolicy,
    *,
    ranking_objective: str = "auto",
) -> DecisionResult:
    """Return the robust mesoscopic decision without guessing through gaps."""
    if not evidence:
        raise ValueError("at least one candidate is required")
    if ranking_objective not in RANKING_OBJECTIVES:
        raise ValueError(
            f"ranking_objective must be one of {sorted(RANKING_OBJECTIVES)}"
        )
    ids = [candidate.candidate_id for candidate in evidence]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate IDs must be unique")
    eligible = [candidate for candidate in evidence if candidate.eligible]
    declared_method = (
        CLOSURE_COST_DECISION_METHOD
        if ranking_objective == "closure_cost_v1"
        else DECISION_METHOD
    )
    if ranking_objective == "closure_cost_v1":
        missing = sorted(
            candidate.candidate_id
            for candidate in eligible
            if not candidate.disruption
        )
        if missing:
            raise ValueError(
                "closure_cost_v1 requires disruption evidence for every "
                f"viable candidate: missing={missing}"
            )
    comparisons = max(1, len(eligible) * len(policy.variants))
    _cross_candidate_provenance(evidence, policy)
    statistics = tuple(
        _candidate_statistics(
            candidate,
            policy,
            simultaneous_comparisons=comparisons,
        )
        for candidate in evidence
    )
    # Mirror pilot_selection's fail-closed rule at the finalist seam: a
    # finalist can acquire a fresh unresolved timeout during finalist-stage
    # verification even if it timed out nowhere during piloting, and the
    # same "we don't know if this would have changed the winner/tie" problem
    # applies. Never let an undecided candidate silently become invisible to
    # the decision the way cost-order v5's exhaustive arm did.
    undecided = tuple(sorted({
        identity
        for candidate in evidence
        for identity in candidate.timeout_undecided
    }))
    if undecided:
        return DecisionResult(
            status="inconclusive",
            winner_id=None,
            tie_ids=(),
            reason=(
                "finalist evidence includes an unresolved SUMO timeout; the "
                "search cannot rule out that candidate changing the winner "
                "or tie set"
            ),
            candidates=statistics,
            next_runs=(),
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
            method=declared_method,
        )

    viable = [candidate for candidate in statistics if candidate.eligible]
    if not viable:
        return DecisionResult(
            status="no_viable",
            winner_id=None,
            tie_ids=(),
            reason="every schedule failed at least one hard gate before ranking",
            candidates=statistics,
            next_runs=(),
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
            method=declared_method,
        )

    requests = _next_runs(viable, policy)
    if any(not candidate.all_variants_ready for candidate in viable):
        return DecisionResult(
            status="inconclusive",
            winner_id=None,
            tie_ids=(),
            reason=(
                "fewer than the pre-registered matched repetitions are "
                "available for at least one demand variant"
            ),
            candidates=statistics,
            next_runs=requests,
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
            method=declared_method,
        )

    # RANKING OBJECTIVE. When every viable candidate carries disruption
    # evidence, rank on what the closure costs the people driving — displaced
    # vehicles and detour, lexicographically, per closure_ranking. Candidates
    # that strand a driver are refused there rather than ranked: an
    # unreachable destination is not "a bit more delay", and averaging it into
    # a score hides it. Mixed or absent evidence falls back to the legacy
    # time-loss bound so pre-objective campaigns still decide.
    costed = [c for c in viable if c.closure_cost is not None]
    use_closure_cost = (
        ranking_objective == "closure_cost_v1"
        or (ranking_objective == "auto" and costed and len(costed) == len(viable))
    )
    if ranking_objective == "closure_cost_v1" and len(costed) != len(viable):
        missing = sorted(
            candidate.candidate_id
            for candidate in viable
            if candidate.closure_cost is None
        )
        raise ValueError(
            "closure_cost_v1 requires disruption evidence for every viable "
            f"candidate: missing={missing}"
        )
    decision_method = (
        CLOSURE_COST_DECISION_METHOD if use_closure_cost else DECISION_METHOD
    )
    if use_closure_cost:
        ordered, refused = rank_closures(c.closure_cost for c in costed)
        by_id = {c.candidate_id: c for c in costed}
        refused_ids = {c.candidate_id for c in refused}
        statistics = tuple(
            replace(
                candidate,
                eligible=False,
                hard_failures=tuple(sorted({
                    *candidate.hard_failures,
                    "vehicles_no_detour",
                })),
            )
            if candidate.candidate_id in refused_ids
            else candidate
            for candidate in statistics
        )
        ranked = [by_id[cost.candidate_id] for cost in ordered]
        viable = [c for c in viable if c.candidate_id not in refused_ids]
        if not ranked:
            return DecisionResult(
                status="no_viable",
                winner_id=None,
                tie_ids=(),
                reason=(
                    "every schedule leaves at least one destination "
                    "unreachable by car"
                ),
                candidates=statistics,
                next_runs=(),
                policy=policy,
                confidence_level=policy.confidence_level,
                simultaneous_comparisons=comparisons,
                method=decision_method,
            )
    else:
        ranked = sorted(
            viable,
            key=lambda candidate: (
                float(candidate.robust_upper_95_s),
                candidate.candidate_id,
            ),
        )
    best = ranked[0]
    if len(ranked) == 1:
        if best.precision_met or not requests:
            return DecisionResult(
                status="unique_winner",
                winner_id=best.candidate_id,
                tie_ids=(),
                reason="the only viable schedule has complete robust evidence",
                candidates=statistics,
                next_runs=(),
                policy=policy,
                confidence_level=policy.confidence_level,
                simultaneous_comparisons=comparisons,
                method=decision_method,
            )
        return DecisionResult(
            status="inconclusive",
            winner_id=None,
            tie_ids=(),
            reason="the only viable schedule has not met the precision target",
            candidates=statistics,
            next_runs=requests,
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
            method=decision_method,
        )

    if use_closure_cost:
        # Deterministic objective: no sampling error, so the confidence
        # apparatus has nothing to resolve. A candidate wins outright when
        # every rival costs more than it by more than the equivalence band.
        band = policy.practical_equivalence_vehicle_hours
        best_hours = best.closure_cost.added_vehicle_hours
        clear = [c for c in ranked[1:]
                 if c.closure_cost.added_vehicle_hours - best_hours > band]
        if len(clear) == len(ranked) - 1:
            return DecisionResult(
                status="unique_winner",
                winner_id=best.candidate_id,
                tie_ids=(),
                reason=(
                    "one schedule displaces fewer vehicles and adds less "
                    "driving than every rival, on deterministic evidence"
                ),
                candidates=statistics,
                next_runs=(),
                policy=policy,
                confidence_level=policy.confidence_level,
                simultaneous_comparisons=comparisons,
                method=decision_method,
            )
        contenders = tuple(
            candidate
            for candidate in ranked
            if candidate.closure_cost.added_vehicle_hours - best_hours <= band
        )
        # A non-zero primary difference inside the declared equivalence band
        # is a practical tie.  When the primary values are EXACTLY equal,
        # however, the documented lexicographic rule is allowed to use added
        # metres and then affected vehicles as genuine tie-breakers.
        if all(
            candidate.closure_cost.added_vehicle_hours == best_hours
            for candidate in contenders
        ):
            best_secondary = (
                best.closure_cost.added_metres_total,
                best.closure_cost.vehicles_affected,
            )
            secondary_ties = tuple(
                candidate.candidate_id
                for candidate in contenders
                if (
                    candidate.closure_cost.added_metres_total,
                    candidate.closure_cost.vehicles_affected,
                ) == best_secondary
            )
            if len(secondary_ties) == 1:
                return DecisionResult(
                    status="unique_winner",
                    winner_id=best.candidate_id,
                    tie_ids=(),
                    reason=(
                        "vehicle-hours are exactly tied and one schedule is "
                        "lexicographically better on added distance and "
                        "affected vehicles"
                    ),
                    candidates=statistics,
                    next_runs=(),
                    policy=policy,
                    confidence_level=policy.confidence_level,
                    simultaneous_comparisons=comparisons,
                    method=decision_method,
                )
            tied = secondary_ties
        else:
            tied = tuple(candidate.candidate_id for candidate in contenders)
        return DecisionResult(
            status="tie",
            winner_id=None,
            tie_ids=tied,
            reason=(
                "several schedules are within the vehicle-hour equivalence "
                "band; no further simulation can separate a deterministic "
                "objective"
            ),
            candidates=statistics,
            next_runs=(),
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
            method=decision_method,
        )

    statistically_better = all(
        float(best.robust_upper_95_s) + policy.practical_equivalence_s
        < float(other.robust_lower_95_s)
        for other in ranked[1:]
    )
    if statistically_better:
        return DecisionResult(
            status="unique_winner",
            winner_id=best.candidate_id,
            tie_ids=(),
            reason=(
                "one schedule is practically and statistically better under "
                "the worst-variant simultaneous 95% bound"
            ),
            candidates=statistics,
            next_runs=(),
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
            method=decision_method,
        )

    unresolved_contenders = tuple(
        candidate
        for candidate in ranked
        if not (
            float(best.robust_upper_95_s) + policy.practical_equivalence_s
            < float(candidate.robust_lower_95_s)
        )
    )
    practical_ties = tuple(
        candidate.candidate_id
        for candidate in unresolved_contenders
        if max(
            abs(
                float(candidate.robust_upper_95_s)
                - float(best.robust_lower_95_s)
            ),
            abs(
                float(best.robust_upper_95_s)
                - float(candidate.robust_lower_95_s)
            ),
        )
        <= policy.practical_equivalence_s
    )
    at_cap_or_precise = all(
        candidate.precision_met
        or all(variant.repetition_cap_reached for variant in candidate.variants)
        for candidate in ranked
    )
    if (
        len(practical_ties) >= 2
        and len(practical_ties) == len(unresolved_contenders)
        and at_cap_or_precise
    ):
        return DecisionResult(
            status="tie",
            winner_id=None,
            tie_ids=practical_ties,
            reason=(
                "the schedules' simultaneous bounds are within the "
                "pre-registered practical equivalence tolerance"
            ),
            candidates=statistics,
            next_runs=(),
            policy=policy,
            confidence_level=policy.confidence_level,
            simultaneous_comparisons=comparisons,
            method=decision_method,
        )

    return DecisionResult(
        status="inconclusive",
        winner_id=None,
        tie_ids=(),
        reason=(
            "no schedule is both practically and statistically better; "
            + (
                "run the requested matched repetitions"
                if requests
                else "the repetition cap was reached with no clear winner"
            )
        ),
        candidates=statistics,
        next_runs=requests,
        policy=policy,
        confidence_level=policy.confidence_level,
        simultaneous_comparisons=comparisons,
        method=decision_method,
    )
