"""Versioned vocabulary for direction uncertainty (plan step 0).

`docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md` opens by
refusing a choice the old code forced: "one simulation" versus "three
quantiles", as if those answered the same question. They do not, and the
reason the old code could not say so is that it had **one word** — `variant` —
for five different things. A q90 route file was a demand assumption, a seed
label, a cache key, a ranking column and, in the published confidence, a
stand-in for simulator noise.

This module gives each of those its own name and makes the illegal
combinations unrepresentable rather than merely discouraged:

``CentralProfile``
    The best point estimate for an edge, day type and slot. It is a *point*
    estimate and says so; nothing here implies an interval.

``MarginalInterval``
    A per-cell interval, carrying its own ``calibration_status``. An interval
    whose empirical coverage has never been measured out-of-sample serialises
    as ``stress_only``, so the phrase "80% interval" cannot reach a UI that
    has no right to say it.

``DemandCase``
    One complete, coherent day across *all* edges and slots. This is the unit
    the plan argues for: applying a marginal q10 to every edge and hour at
    once describes no real day, because marginal quantiles do not preserve the
    dependence between edges and hours (Ensemble Copula Coupling; Schefzik et
    al.). A case is probabilistic (carries a weight) or a stress case (carries
    none, ever).

``ScenarioPath``
    A version-bound sequence of demand cases across a whole warm-up → closure
    → recovery horizon. Multi-day closures reuse one path id; a path may hold
    different pre-registered day cases but may never re-pick "the worst" per
    day, per candidate or per cost field.

``SimulationMember``
    The explicit pair ``(scenario_path_id, simulation_seed)``. This is the
    type that makes plan invariant "a seed cannot imply a demand case"
    mechanical: a seed is an *argument* here, never a lookup key into a
    variant table.

``Applicability``
    The multi-dimensional evidence profile replacing a single kNN pass/fail,
    so "we know little about this street" widens uncertainty instead of
    silently banning the street.

Nothing in this module trains, predicts or simulates. It is the boundary the
later steps are written against, and it is deliberately importable without
numpy, LightGBM, osmnx or SUMO so that contract tests stay cheap.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence

SCHEMA_VERSION = "direction_ensemble_v2"

#: 15-minute slots in one day. The whole pipeline is quarter-indexed.
SLOT_COUNT = 96

#: Sum tolerance for a directed pair before rounding (plan invariant 1).
#: Tight on purpose: this is an identity, not a fitted quantity.
PAIR_SUM_TOLERANCE = 1e-9

#: Tolerance on the total probabilistic weight (plan: "exactly 1 within the
#: numeric tolerance the schema states").
WEIGHT_SUM_TOLERANCE = 1e-9

CalibrationStatus = Literal["calibrated", "stress_only", "unavailable"]
CaseKind = Literal["probabilistic", "stress"]
DayType = Literal["weekday", "weekend", "holiday"]

_CALIBRATION_STATUSES: frozenset[str] = frozenset(
    ("calibrated", "stress_only", "unavailable")
)
_CASE_KINDS: frozenset[str] = frozenset(("probabilistic", "stress"))
_DAY_TYPES: frozenset[str] = frozenset(("weekday", "weekend", "holiday"))

#: Evidence grades used by the applicability profile. Ordered worst → best so
#: a caller can compare them without inventing its own ranking.
EVIDENCE_GRADES: tuple[str, ...] = (
    "unavailable",      # input error or impossible edge pairing — fail closed
    "extrapolation",    # outside the training domain on some axis
    "limited",          # inside the domain but thin support
    "good",             # normal claims permitted
)

#: Measurement levels, worst → best. ``transfer_prior`` means no local
#: directional measurement exists at all and the number comes from a model
#: trained elsewhere.
MEASUREMENT_LEVELS: tuple[str, ...] = (
    "transfer_prior",
    "single_direction",
    "both_directions",
)


class SchemaError(ValueError):
    """Raised when a value would violate a plan invariant."""


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────
def _check_share_series(values: Sequence[float], label: str) -> tuple[float, ...]:
    """A per-slot share series: exactly SLOT_COUNT values, each in [0, 1]."""
    series = tuple(float(v) for v in values)
    if len(series) != SLOT_COUNT:
        raise SchemaError(
            f"{label} must have exactly {SLOT_COUNT} slots, got {len(series)}"
        )
    for index, value in enumerate(series):
        if value != value:                       # NaN
            raise SchemaError(f"{label} slot {index} is NaN")
        if not 0.0 <= value <= 1.0:
            raise SchemaError(
                f"{label} slot {index} = {value!r} is outside [0, 1]"
            )
    return series


def _check_edge_id(edge_id: Any, label: str) -> str:
    if not isinstance(edge_id, str) or not edge_id.strip():
        raise SchemaError(f"{label} must be a non-empty edge id")
    return edge_id


def content_digest(payload: Any) -> str:
    """Stable SHA-256 over a JSON-serialisable payload.

    Used for every id in this module. Ids are digests rather than counters so
    that a case id can never be confused with, or derived from, a seed number
    — which is exactly the confusion plan step 6 exists to remove.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ──────────────────────────────────────────────────────────────────────────
# central profile and marginal interval
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CentralProfile:
    """Best point estimate of the direction share, per edge and slot.

    ``shares`` maps a *directed* edge id to its 96 per-slot shares. Both
    members of a directed pair are stored explicitly, and the pair identity
    (they sum to 1) is checked here rather than assumed downstream — the
    2026-08-06 incident where a pair-normalised share silently halved a
    measured target is exactly what an unchecked pair produces.
    """

    model_id: str
    day_type: DayType
    shares: Mapping[str, tuple[float, ...]]
    edge_pairs: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise SchemaError("model_id must be non-empty")
        if self.day_type not in _DAY_TYPES:
            raise SchemaError(
                f"day_type must be one of {sorted(_DAY_TYPES)}, "
                f"got {self.day_type!r}"
            )
        checked: dict[str, tuple[float, ...]] = {}
        for edge_id, series in self.shares.items():
            key = _check_edge_id(edge_id, "shares key")
            checked[key] = _check_share_series(series, f"shares[{key}]")
        object.__setattr__(self, "shares", checked)
        _check_pairs(self.edge_pairs, checked, "CentralProfile")

    def share_at(self, edge_id: str, slot: int) -> float:
        return self.shares[edge_id][slot]


def _check_pairs(
    pairs: Iterable[tuple[str, str]],
    shares: Mapping[str, tuple[float, ...]],
    label: str,
) -> None:
    """Plan invariant 1: two directions sum to 1.0 in every slot."""
    seen: set[str] = set()
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise SchemaError(f"{label}: edge_pairs entries must be 2-tuples")
        a, b = (_check_edge_id(p, f"{label} pair member") for p in pair)
        if a == b:
            raise SchemaError(f"{label}: edge {a} paired with itself")
        for member in (a, b):
            if member in seen:
                raise SchemaError(
                    f"{label}: edge {member} appears in more than one pair"
                )
            seen.add(member)
            if member not in shares:
                raise SchemaError(f"{label}: pair member {member} has no shares")
        for slot in range(SLOT_COUNT):
            total = shares[a][slot] + shares[b][slot]
            if abs(total - 1.0) > PAIR_SUM_TOLERANCE:
                raise SchemaError(
                    f"{label}: pair ({a}, {b}) sums to {total!r} at slot {slot}; "
                    "directed shares must sum to 1.0 before rounding"
                )


@dataclass(frozen=True)
class MarginalInterval:
    """A per-cell interval and — crucially — its calibration status.

    The plan's rule, from Gneiting/Balabdaoui/Raftery: a narrow interval is
    only worth anything if its frequentist coverage holds. Until out-of-sample
    coverage has actually been measured, ``calibration_status`` stays
    ``stress_only`` and ``nominal_coverage`` must not be presented as a
    probability. ``empirical_coverage`` is None precisely when nobody has
    measured it, and the two fields are checked against each other so a
    'calibrated' label cannot exist without a measurement behind it.
    """

    lower: Mapping[str, tuple[float, ...]]
    upper: Mapping[str, tuple[float, ...]]
    nominal_coverage: float
    calibration_status: CalibrationStatus = "stress_only"
    empirical_coverage: float | None = None
    calibration_groups: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if self.calibration_status not in _CALIBRATION_STATUSES:
            raise SchemaError(
                "calibration_status must be one of "
                f"{sorted(_CALIBRATION_STATUSES)}"
            )
        if not 0.0 < float(self.nominal_coverage) < 1.0:
            raise SchemaError("nominal_coverage must lie strictly in (0, 1)")
        lower = {
            _check_edge_id(k, "lower key"): _check_share_series(v, f"lower[{k}]")
            for k, v in self.lower.items()
        }
        upper = {
            _check_edge_id(k, "upper key"): _check_share_series(v, f"upper[{k}]")
            for k, v in self.upper.items()
        }
        if set(lower) != set(upper):
            raise SchemaError("lower and upper must cover the same edges")
        for edge_id in lower:
            for slot in range(SLOT_COUNT):
                if lower[edge_id][slot] > upper[edge_id][slot]:
                    raise SchemaError(
                        f"interval crossing at {edge_id} slot {slot}: "
                        f"{lower[edge_id][slot]} > {upper[edge_id][slot]}"
                    )
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)
        if self.calibration_status == "calibrated" and self.empirical_coverage is None:
            raise SchemaError(
                "calibration_status='calibrated' requires a measured "
                "empirical_coverage; an unmeasured interval is stress_only"
            )
        if self.empirical_coverage is not None:
            if not 0.0 <= float(self.empirical_coverage) <= 1.0:
                raise SchemaError("empirical_coverage must lie in [0, 1]")

    @property
    def is_probability_statement(self) -> bool:
        """True only when the interval has earned its nominal label."""
        return self.calibration_status == "calibrated"

    def label(self) -> str:
        """Human-facing text. Never claims coverage it has not measured."""
        if self.is_probability_statement:
            pct = round(100 * float(self.nominal_coverage))
            return f"{pct}% intervall (empiriskt validerat)"
        if self.calibration_status == "unavailable":
            return "intervall saknas"
        return "okalibrerat stressintervall"


# ──────────────────────────────────────────────────────────────────────────
# demand cases, stress cases, scenario paths
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DemandCase:
    """One complete coherent day across every edge and slot.

    A case is the smallest thing that may be handed to SUMO as "a day". It is
    NOT a per-cell quantile: the plan's whole argument is that independently
    taking the marginal q10 of every edge-hour produces a world that never
    occurred. A case therefore carries the full edge × slot matrix at once,
    and the way it was produced is recorded in ``generator``.

    ``kind='stress'`` cases may never carry a weight (plan invariant 5). The
    constructor refuses it rather than dropping it silently, because a stress
    case that acquires a weight is precisely how an unvalidated extreme turns
    into a fake probability downstream.
    """

    case_id: str
    kind: CaseKind
    day_type: DayType
    shares: Mapping[str, tuple[float, ...]]
    edge_pairs: tuple[tuple[str, str], ...]
    generator: str
    weight: float | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise SchemaError("case_id must be non-empty")
        if self.kind not in _CASE_KINDS:
            raise SchemaError(f"kind must be one of {sorted(_CASE_KINDS)}")
        if self.day_type not in _DAY_TYPES:
            raise SchemaError(f"day_type must be one of {sorted(_DAY_TYPES)}")
        if not isinstance(self.generator, str) or not self.generator.strip():
            raise SchemaError("generator must name how the case was produced")
        checked = {
            _check_edge_id(k, "shares key"): _check_share_series(v, f"shares[{k}]")
            for k, v in self.shares.items()
        }
        object.__setattr__(self, "shares", checked)
        _check_pairs(self.edge_pairs, checked, f"DemandCase {self.case_id}")

        if self.kind == "stress":
            if self.weight is not None:
                raise SchemaError(
                    f"stress case {self.case_id} carries weight "
                    f"{self.weight!r}; a stress case has no probability mass "
                    "(plan invariant 5)"
                )
        else:
            if self.weight is None:
                raise SchemaError(
                    f"probabilistic case {self.case_id} needs a weight"
                )
            if not 0.0 < float(self.weight) <= 1.0:
                raise SchemaError(
                    f"probabilistic weight must lie in (0, 1], got {self.weight!r}"
                )

    @property
    def is_probabilistic(self) -> bool:
        return self.kind == "probabilistic"

    def edges(self) -> tuple[str, ...]:
        return tuple(sorted(self.shares))


def make_case_id(
    *,
    generator: str,
    day_type: str,
    shares: Mapping[str, Sequence[float]],
    provenance: Mapping[str, Any] | None = None,
) -> str:
    """Content-addressed case id.

    Two cases with the same content get the same id; a case id can never be
    guessed from a seed, and changing any share changes the id. The
    ``dc_`` prefix keeps it visually distinct from a seed in logs and paths.
    """
    payload = {
        "generator": generator,
        "day_type": day_type,
        "shares": {k: [round(float(x), 12) for x in v]
                   for k, v in sorted(shares.items())},
        "provenance": provenance or {},
    }
    return "dc_" + content_digest(payload)[:24]


@dataclass(frozen=True)
class ScenarioPath:
    """A version-bound sequence of demand cases across a whole horizon.

    Plan invariant 2: a multi-day closure uses ONE path id from warm-up to
    recovery. The path may hold different pre-registered day cases, but it may
    not re-pick the worst case per day, per candidate or per cost field. This
    class stores the ordered case ids and nothing that would let a consumer
    re-select them.
    """

    path_id: str
    case_ids: tuple[str, ...]
    calendar_dates: tuple[str, ...]
    generator_version: str
    weight: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path_id, str) or not self.path_id.strip():
            raise SchemaError("path_id must be non-empty")
        if not self.case_ids:
            raise SchemaError("a scenario path needs at least one demand case")
        if len(self.case_ids) != len(self.calendar_dates):
            raise SchemaError(
                "case_ids and calendar_dates must be the same length "
                f"({len(self.case_ids)} vs {len(self.calendar_dates)})"
            )
        if len(set(self.calendar_dates)) != len(self.calendar_dates):
            raise SchemaError("calendar_dates must be unique within a path")
        if list(self.calendar_dates) != sorted(self.calendar_dates):
            raise SchemaError("calendar_dates must be in ascending order")
        if self.weight is not None and not 0.0 < float(self.weight) <= 1.0:
            raise SchemaError("path weight must lie in (0, 1] when present")

    @property
    def horizon_days(self) -> int:
        return len(self.case_ids)

    def case_for_date(self, date: str) -> str:
        try:
            return self.case_ids[self.calendar_dates.index(date)]
        except ValueError:
            raise SchemaError(
                f"{date} is not part of scenario path {self.path_id}"
            ) from None


def make_path_id(*, case_ids: Sequence[str], calendar_dates: Sequence[str],
                 generator_version: str) -> str:
    return "sp_" + content_digest({
        "case_ids": list(case_ids),
        "calendar_dates": list(calendar_dates),
        "generator_version": generator_version,
    })[:24]


# ──────────────────────────────────────────────────────────────────────────
# simulation member — the case ⊥ seed boundary
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SimulationMember:
    """One observation: a scenario path run under one SUMO seed.

    This type is the mechanical form of the plan's central separation. In the
    old contract a seed *implied* a demand variant (`1000→q50`, `1001→q10`,
    `1002→q90`), so a three-seed run was three different demand worlds with
    one sample each, and the published `exp(-CV)` mixed simulator noise with
    direction uncertainty and could not separate them.

    Here the two are independent coordinates. Constructing a member requires
    naming both, and `matched_pair` builds the baseline/candidate pair that
    common random numbers require: same path, same seed, differing only in
    the closure.
    """

    scenario_path_id: str
    simulation_seed: int
    arm: Literal["baseline", "candidate"] = "baseline"

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_path_id, str) or \
                not self.scenario_path_id.strip():
            raise SchemaError("scenario_path_id must be non-empty")
        if isinstance(self.simulation_seed, bool) or \
                not isinstance(self.simulation_seed, int) or \
                self.simulation_seed < 0:
            raise SchemaError("simulation_seed must be a non-negative int")
        if self.arm not in ("baseline", "candidate"):
            raise SchemaError("arm must be 'baseline' or 'candidate'")

    @property
    def member_id(self) -> str:
        return f"{self.scenario_path_id}@{self.simulation_seed}:{self.arm}"

    def matched(self, arm: Literal["baseline", "candidate"]) -> "SimulationMember":
        """The same path and seed on the other arm (common random numbers)."""
        return SimulationMember(
            scenario_path_id=self.scenario_path_id,
            simulation_seed=self.simulation_seed,
            arm=arm,
        )


def matched_pair(scenario_path_id: str,
                 simulation_seed: int) -> tuple[SimulationMember, SimulationMember]:
    """Baseline and candidate sharing one path and one seed.

    Plan invariant 3, and the variance-reduction reason for it: comparing a
    candidate against a baseline under the *same* random draw removes the
    shared noise from their difference (Rathi & Venigalla on common random
    numbers in traffic network simulation).
    """
    base = SimulationMember(scenario_path_id, simulation_seed, "baseline")
    return base, base.matched("candidate")


def seed_list(start: int, count: int) -> tuple[int, ...]:
    """A plain seed list, independent of any demand case.

    FHWA's Traffic Analysis Toolbox asks for several seeds per alternative
    before any variation claim; the plan's first finalist batch uses four.
    Note what this function does NOT take: a variant. That is the point.
    """
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        raise SchemaError("seed start must be a non-negative int")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise SchemaError("seed count must be a positive int")
    return tuple(start + offset for offset in range(count))


# ──────────────────────────────────────────────────────────────────────────
# applicability
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Applicability:
    """Multi-dimensional evidence profile for one target edge.

    Replaces the single kNN pass/fail in `dirsplit/coverage.py`, which could
    only see static road features and therefore could not notice missing
    temporal support, a profile-feature semantics mismatch, or a thin
    effective sample. Each dimension is graded separately so the product can
    widen uncertainty on the axis that is actually weak.

    The plan is explicit that low evidence must not ban a street. `grade`
    therefore returns the worst dimension for *claim strength*, and only a
    genuine input error (`unavailable`) is fail-closed.
    """

    edge_id: str
    measurement_level: str
    static_domain: str
    temporal_support: str
    feature_compatibility: str
    effective_sample_size: float
    calibration_support: str
    local_crosscheck: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    _GRADED_FIELDS = (
        "static_domain", "temporal_support",
        "feature_compatibility", "calibration_support",
    )

    def __post_init__(self) -> None:
        _check_edge_id(self.edge_id, "edge_id")
        if self.measurement_level not in MEASUREMENT_LEVELS:
            raise SchemaError(
                f"measurement_level must be one of {list(MEASUREMENT_LEVELS)}"
            )
        for name in self._GRADED_FIELDS:
            value = getattr(self, name)
            if value not in EVIDENCE_GRADES:
                raise SchemaError(
                    f"{name} must be one of {list(EVIDENCE_GRADES)}, got {value!r}"
                )
        if float(self.effective_sample_size) < 0:
            raise SchemaError("effective_sample_size must be non-negative")

    def grade(self) -> str:
        """Worst graded dimension — the ceiling on what may be claimed."""
        worst = min(
            (getattr(self, name) for name in self._GRADED_FIELDS),
            key=EVIDENCE_GRADES.index,
        )
        return worst

    @property
    def blocks_publication(self) -> bool:
        """Only a genuine input error fails closed. Thin evidence never does.

        This encodes the plan's line directly: excluding roads merely because
        observability is low is unreasonable; pretending ignorance is
        precision is worse. So a weak grade widens uncertainty elsewhere, and
        only `unavailable` stops publication here.
        """
        return self.grade() == "unavailable"

    def claim_strength(self) -> str:
        """How strong a claim this edge supports."""
        grade = self.grade()
        if grade == "unavailable":
            return "unavailable"
        if grade == "extrapolation":
            return "conservative_fallback"
        if grade == "limited":
            return "widened"
        return "normal"


# ──────────────────────────────────────────────────────────────────────────
# the whole artifact
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DirectionEnsemble:
    """`sumo/direction_ensemble_v2.json` in typed form.

    Holds the central profile, the marginal intervals, the probabilistic
    demand cases with weights summing to 1, the stress cases with no weights
    at all, the applicability profiles and the digests binding all of it to
    its source data, model and calibration.
    """

    model_digest: str
    training_data_digest: str
    calibration_digest: str
    central_profiles: tuple[CentralProfile, ...]
    marginal_intervals: MarginalInterval | None
    demand_cases: tuple[DemandCase, ...]
    stress_cases: tuple[DemandCase, ...] = ()
    scenario_paths: tuple[ScenarioPath, ...] = ()
    applicability: tuple[Applicability, ...] = ()
    path_generator: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    slot_minutes: int = 15

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError(
                f"schema_version must be {SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}"
            )
        if self.slot_minutes != 15:
            raise SchemaError("slot_minutes must be 15 for this pipeline")
        for name in ("model_digest", "training_data_digest", "calibration_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SchemaError(f"{name} must be non-empty")

        for case in self.demand_cases:
            if not case.is_probabilistic:
                raise SchemaError(
                    f"demand_cases must all be probabilistic; {case.case_id} "
                    f"is {case.kind!r} — put it in stress_cases"
                )
        for case in self.stress_cases:
            if case.is_probabilistic:
                raise SchemaError(
                    f"stress_cases must all be stress; {case.case_id} is "
                    "probabilistic"
                )

        ids = [c.case_id for c in self.demand_cases + self.stress_cases]
        if len(set(ids)) != len(ids):
            raise SchemaError("case ids must be unique across demand and stress")

        if self.demand_cases:
            total = sum(float(c.weight or 0.0) for c in self.demand_cases)
            if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
                raise SchemaError(
                    f"probabilistic weights sum to {total!r}, must be 1.0 "
                    f"within {WEIGHT_SUM_TOLERANCE}"
                )

        known = set(ids)
        for path in self.scenario_paths:
            missing = [cid for cid in path.case_ids if cid not in known]
            if missing:
                raise SchemaError(
                    f"scenario path {path.path_id} references unknown cases "
                    f"{missing}"
                )

    @property
    def has_calibrated_intervals(self) -> bool:
        return (self.marginal_intervals is not None
                and self.marginal_intervals.is_probability_statement)

    def case(self, case_id: str) -> DemandCase:
        for candidate in self.demand_cases + self.stress_cases:
            if candidate.case_id == case_id:
                return candidate
        raise SchemaError(f"unknown case id {case_id!r}")

    def to_json(self) -> dict[str, Any]:
        """Serialise to the artifact shape named in the plan."""
        intervals: dict[str, Any]
        if self.marginal_intervals is None:
            intervals = {"calibration_status": "unavailable"}
        else:
            mi = self.marginal_intervals
            intervals = {
                "nominal_coverage": mi.nominal_coverage,
                "calibration_status": mi.calibration_status,
                "empirical_coverage": mi.empirical_coverage,
                "calibration_groups": list(mi.calibration_groups),
                "lower": {k: list(v) for k, v in sorted(mi.lower.items())},
                "upper": {k: list(v) for k, v in sorted(mi.upper.items())},
                "notes": mi.notes,
            }
        return {
            "schema_version": self.schema_version,
            "model_digest": self.model_digest,
            "training_data_digest": self.training_data_digest,
            "calibration_digest": self.calibration_digest,
            "slot_minutes": self.slot_minutes,
            "central_profile": {
                profile.day_type: {
                    "model_id": profile.model_id,
                    "edge_pairs": [list(p) for p in profile.edge_pairs],
                    "shares": {k: list(v)
                               for k, v in sorted(profile.shares.items())},
                }
                for profile in self.central_profiles
            },
            "marginal_intervals": intervals,
            "demand_cases": [_case_json(c) for c in self.demand_cases],
            "stress_cases": [_case_json(c) for c in self.stress_cases],
            "scenario_paths": [
                {
                    "path_id": p.path_id,
                    "case_ids": list(p.case_ids),
                    "calendar_dates": list(p.calendar_dates),
                    "generator_version": p.generator_version,
                    "weight": p.weight,
                }
                for p in self.scenario_paths
            ],
            "path_generator": dict(self.path_generator),
            "applicability": {
                a.edge_id: {
                    "measurement_level": a.measurement_level,
                    "static_domain": a.static_domain,
                    "temporal_support": a.temporal_support,
                    "feature_compatibility": a.feature_compatibility,
                    "effective_sample_size": a.effective_sample_size,
                    "calibration_support": a.calibration_support,
                    "local_crosscheck": a.local_crosscheck,
                    "grade": a.grade(),
                    "claim_strength": a.claim_strength(),
                    "detail": dict(a.detail),
                }
                for a in self.applicability
            },
            "provenance": dict(self.provenance),
        }


def _case_json(case: DemandCase) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "case_id": case.case_id,
        "kind": case.kind,
        "day_type": case.day_type,
        "generator": case.generator,
        "edge_pairs": [list(p) for p in case.edge_pairs],
        "edge_shares": {k: list(v) for k, v in sorted(case.shares.items())},
        "provenance": dict(case.provenance),
    }
    # A stress case has no weight key at all, rather than a null one: a
    # missing key cannot be read as "weight zero" by a lenient consumer.
    if case.is_probabilistic:
        payload["weight"] = case.weight
    return payload


__all__ = [
    "SCHEMA_VERSION", "SLOT_COUNT", "PAIR_SUM_TOLERANCE",
    "WEIGHT_SUM_TOLERANCE", "EVIDENCE_GRADES", "MEASUREMENT_LEVELS",
    "SchemaError", "CentralProfile", "MarginalInterval", "DemandCase",
    "ScenarioPath", "SimulationMember", "Applicability", "DirectionEnsemble",
    "content_digest", "make_case_id", "make_path_id", "matched_pair",
    "seed_list",
]
