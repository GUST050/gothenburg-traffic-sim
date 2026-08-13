"""Demand case and SUMO seed as orthogonal axes (plan step 6).

The contract this replaces conflated them. `monthly_search.canonical_seed`
computes ``1000 + repetition * 3 + index_of(variant)``, so the demand
assumption is an *addend of the seed number*: you cannot hold one fixed and
vary the other, because they are the same integer. `ScenarioSpec`'s default
``demand_variant_mapping`` then hands out one seed per variant, so the
canonical three-seed run is three different demand worlds with a single
sample each — and the published ``confidence = spatial_prior * exp(-CV)``
computes its CV across those three, mixing simulator noise with direction
uncertainty in a way no downstream consumer can separate.

Here the two are independent coordinates:

* :class:`SimulationMemberSpec` names ``scenario_path_id``,
  ``demand_case_id``, ``simulation_seed`` and ``arm`` separately. There is no
  function anywhere in this module that derives one from another.
* :func:`build_plan` produces the full cross product, and pairs every
  candidate observation with a baseline observation sharing the **same path,
  case and seed** — common random numbers, which removes the shared draw from
  their difference (Rathi & Venigalla) and is why a paired design needs far
  fewer runs than an unpaired one for the same resolution.
* :class:`CostVector` is a COMPLETE observation from one member, and the
  reduction functions can only ever *select* one, never assemble a new one
  field by field.

That last point is plan invariant 4, and it is a correctness rule rather than
a style preference. Taking the worst ``total_time_loss_s`` from one case and
the worst ``unfinished_trips`` from another produces a scorecard that
occurred in no simulated world; a candidate can then be rejected for a
combination of failures that never co-occurred. The type system here makes
that impossible: a reducer returns a ``CostVector`` it was given.

This module is additive. `ScenarioSpec` and `canonical_seed` are untouched,
so every frozen golden and resume test keeps its exact bytes; step 7 is where
v2 becomes selectable, and it arrives as opt-in shadow mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence

SCHEMA_VERSION = "simulation_member_v2"

Arm = Literal["baseline", "candidate"]
_ARMS: frozenset[str] = frozenset(("baseline", "candidate"))

#: Decision outcomes. The contract is fail-closed: anything that is not a
#: clean win is named as what it is, and none of these can be read as a
#: winner by a consumer that only checks for absence of error.
Outcome = Literal["unique_winner", "tie", "inconclusive", "no_viable", "paused"]
_OUTCOMES: frozenset[str] = frozenset(
    ("unique_winner", "tie", "inconclusive", "no_viable", "paused")
)


class PlanError(ValueError):
    """Raised when a plan or reduction would violate a plan invariant."""


@dataclass(frozen=True)
class SimulationMemberSpec:
    """One observation: a path, a demand case, a seed and an arm.

    Every coordinate is explicit. Note what is absent: any mapping from seed
    to case, in either direction.
    """

    scenario_path_id: str
    demand_case_id: str
    simulation_seed: int
    arm: Arm = "baseline"

    def __post_init__(self) -> None:
        for name in ("scenario_path_id", "demand_case_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise PlanError(f"{name} must be a non-empty string")
        if isinstance(self.simulation_seed, bool) or \
                not isinstance(self.simulation_seed, int) or \
                self.simulation_seed < 0:
            raise PlanError("simulation_seed must be a non-negative int")
        if self.arm not in _ARMS:
            raise PlanError(f"arm must be one of {sorted(_ARMS)}")

    @property
    def member_id(self) -> str:
        return (f"{self.scenario_path_id}|{self.demand_case_id}"
                f"|{self.simulation_seed}|{self.arm}")

    @property
    def pair_key(self) -> str:
        """Identity shared by the two arms of one matched pair."""
        return (f"{self.scenario_path_id}|{self.demand_case_id}"
                f"|{self.simulation_seed}")

    def with_arm(self, arm: Arm) -> "SimulationMemberSpec":
        return SimulationMemberSpec(self.scenario_path_id, self.demand_case_id,
                                    self.simulation_seed, arm)


def build_plan(
    *,
    scenario_path_ids: Sequence[str],
    demand_case_ids: Sequence[str],
    seeds: Sequence[int],
    paired: bool = True,
) -> list[SimulationMemberSpec]:
    """Full cross product of paths x cases x seeds, both arms when paired.

    The same seed list is used for every case and for both arms. That is what
    makes the candidate-minus-baseline difference a paired quantity, and it is
    checked by :func:`validate_plan` rather than left to convention.
    """
    if not scenario_path_ids:
        raise PlanError("at least one scenario path is required")
    if not demand_case_ids:
        raise PlanError("at least one demand case is required")
    if not seeds:
        raise PlanError("at least one seed is required")
    if len(set(seeds)) != len(seeds):
        raise PlanError("seeds must be unique")

    members: list[SimulationMemberSpec] = []
    arms: tuple[Arm, ...] = ("baseline", "candidate") if paired else ("baseline",)
    for path_id in scenario_path_ids:
        for case_id in demand_case_ids:
            for seed in seeds:
                for arm in arms:
                    members.append(
                        SimulationMemberSpec(path_id, case_id, seed, arm)
                    )
    return members


def validate_plan(members: Sequence[SimulationMemberSpec]) -> None:
    """Every case must see the same seeds, and every pair must have both arms.

    An unbalanced plan silently destroys the pairing: if the candidate arm
    ran seeds {1000, 1001} and the baseline {1000, 1002}, the difference is
    computed across different random draws and the variance reduction that
    justified the design is gone.
    """
    if not members:
        raise PlanError("plan is empty")

    seeds_by_case: dict[str, set[int]] = {}
    for member in members:
        seeds_by_case.setdefault(member.demand_case_id, set()).add(
            member.simulation_seed
        )
    reference = None
    for case_id, seeds in sorted(seeds_by_case.items()):
        if reference is None:
            reference = seeds
        elif seeds != reference:
            raise PlanError(
                f"case {case_id} ran seeds {sorted(seeds)}, but another case "
                f"ran {sorted(reference)}; every case must see the same seeds"
            )

    arms_by_pair: dict[str, set[str]] = {}
    for member in members:
        arms_by_pair.setdefault(member.pair_key, set()).add(member.arm)
    unpaired = [key for key, arms in arms_by_pair.items()
                if arms != {"baseline", "candidate"}]
    if unpaired and any(len(a) == 2 for a in arms_by_pair.values()):
        raise PlanError(
            f"{len(unpaired)} observation(s) lack a matched arm, e.g. "
            f"{sorted(unpaired)[0]}; a paired comparison needs both arms on "
            "the same path, case and seed"
        )


def matched_pairs(members: Sequence[SimulationMemberSpec]
                  ) -> list[tuple[SimulationMemberSpec, SimulationMemberSpec]]:
    """Baseline/candidate pairs sharing path, case and seed."""
    by_key: dict[str, dict[str, SimulationMemberSpec]] = {}
    for member in members:
        by_key.setdefault(member.pair_key, {})[member.arm] = member
    pairs = []
    for key in sorted(by_key):
        arms = by_key[key]
        if "baseline" in arms and "candidate" in arms:
            pairs.append((arms["baseline"], arms["candidate"]))
    return pairs


# ──────────────────────────────────────────────────────────────────────────
# complete cost vectors — no field-wise splicing
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CostVector:
    """A complete scorecard from ONE simulated world.

    ``values`` holds the named cost fields exactly as one member produced
    them. The class carries its own provenance so that a reduction result can
    always be traced back to the member it came from — and so that a vector
    assembled from several members is representable only by lying about
    ``member``, which the reducers below never do.
    """

    member: SimulationMemberSpec
    values: Mapping[str, float]
    hard_failure: bool = False
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.values:
            raise PlanError("a cost vector must carry at least one field")
        cleaned = {}
        for name, value in self.values.items():
            if not isinstance(name, str) or not name:
                raise PlanError("cost field names must be non-empty strings")
            cleaned[name] = float(value)
        object.__setattr__(self, "values", cleaned)
        if self.hard_failure and not self.failure_reason:
            raise PlanError("a hard failure must name its reason")

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(sorted(self.values))

    def get(self, name: str) -> float:
        if name not in self.values:
            raise PlanError(
                f"cost field {name!r} is absent from the vector produced by "
                f"{self.member.member_id}; taking it from a different member "
                "would splice fields across worlds (plan invariant 4)"
            )
        return self.values[name]


def require_same_fields(vectors: Sequence[CostVector]) -> tuple[str, ...]:
    """Every vector must describe the same scorecard.

    A missing field in one world is not a zero. If one case reported
    ``closed_edge_throughput`` and another did not, a reduction over both
    would compare a measured value against an absence.
    """
    if not vectors:
        raise PlanError("no cost vectors to reduce")
    reference = vectors[0].fields
    for vector in vectors[1:]:
        if vector.fields != reference:
            raise PlanError(
                f"cost vectors disagree on their fields: {reference} vs "
                f"{vector.fields}; a reduction across them would compare a "
                "measured value against an absence"
            )
    return reference


def select_worst(vectors: Sequence[CostVector], objective: str) -> CostVector:
    """Return the single worst COMPLETE vector by one named objective.

    This is the anti-splicing primitive. It returns a vector it was given, so
    the result describes a world that actually occurred. It deliberately
    offers no way to take the worst of each field separately.
    """
    require_same_fields(vectors)
    live = [v for v in vectors if not v.hard_failure]
    if not live:
        raise PlanError("every vector is a hard failure; nothing to rank")
    return max(live, key=lambda v: v.get(objective))


def weighted_expectation(vectors: Sequence[CostVector], objective: str,
                         weights: Mapping[str, float]) -> float:
    """Weighted mean of one objective across probabilistic cases.

    ``weights`` maps demand_case_id to probability mass. A vector whose case
    has no weight is excluded (that is what a stress case is), and a missing
    weight for a case that IS present raises rather than being treated as
    zero.
    """
    require_same_fields(vectors)
    total = 0.0
    mass = 0.0
    seen: set[str] = set()
    for vector in vectors:
        if vector.hard_failure:
            continue
        case_id = vector.member.demand_case_id
        if case_id not in weights:
            continue                      # stress case: no probability mass
        seen.add(case_id)
        weight = float(weights[case_id])
        total += weight * vector.get(objective)
        mass += weight
    missing = set(weights) - seen
    if missing:
        raise PlanError(
            f"no observation for weighted case(s) {sorted(missing)}; a "
            "weighted expectation over an incomplete set would silently "
            "re-normalise"
        )
    if mass <= 0:
        raise PlanError("no probabilistic mass to reduce over")
    return total / mass


def max_regret(by_candidate: Mapping[str, Sequence[CostVector]],
               objective: str) -> dict[str, float]:
    """Maximum regret per candidate, computed within each shared world.

    Regret is only meaningful between candidates observed in the SAME world,
    so this groups by ``pair_key`` first. Comparing a candidate's cost in one
    demand case against another's in a different case is the same splicing
    error in a different shape.
    """
    if not by_candidate:
        return {}
    per_world: dict[str, dict[str, float]] = {}
    for name, vectors in by_candidate.items():
        for vector in vectors:
            if vector.hard_failure:
                continue
            per_world.setdefault(vector.member.pair_key, {})[name] = \
                vector.get(objective)

    regrets: dict[str, list[float]] = {name: [] for name in by_candidate}
    for world in per_world.values():
        if len(world) < 2:
            continue
        best = min(world.values())
        for name, value in world.items():
            regrets[name].append(value - best)
    return {name: (max(values) if values else float("nan"))
            for name, values in regrets.items()}


# ──────────────────────────────────────────────────────────────────────────
# the decision gate
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DecisionResult:
    outcome: Outcome
    winner: str | None
    reason: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise PlanError(f"outcome must be one of {sorted(_OUTCOMES)}")
        if self.outcome == "unique_winner" and not self.winner:
            raise PlanError("a unique_winner must name a winner")
        if self.outcome != "unique_winner" and self.winner:
            raise PlanError(
                f"outcome {self.outcome!r} must not name a winner; only "
                "unique_winner may"
            )


def decide(
    *,
    scores: Mapping[str, float],
    budget_complete: bool,
    paused: bool = False,
    disqualified: Mapping[str, str] | None = None,
    tie_threshold: float = 0.0,
) -> DecisionResult:
    """Fail-closed decision over candidate scores. Lower is better.

    Plan invariant 6 in executable form: an incomplete scenario or seed
    budget can NEVER publish a unique_winner. That check comes first,
    deliberately — before the scores are even compared — so no ordering of
    later conditions can let a partial run through.
    """
    disqualified = dict(disqualified or {})
    live = {name: value for name, value in scores.items()
            if name not in disqualified}

    if paused:
        return DecisionResult("paused", None,
                              "the run was paused before its budget completed",
                              {"disqualified": disqualified})
    if not live:
        return DecisionResult(
            "no_viable", None,
            "every candidate was disqualified by a hard safety gate",
            {"disqualified": disqualified},
        )
    if not budget_complete:
        return DecisionResult(
            "inconclusive", None,
            "the scenario/seed budget did not complete; a partial run cannot "
            "publish a winner",
            {"disqualified": disqualified, "scores": dict(live)},
        )

    ordered = sorted(live.items(), key=lambda item: item[1])
    best_name, best_score = ordered[0]
    if len(ordered) > 1:
        _runner_name, runner_score = ordered[1]
        if runner_score - best_score <= tie_threshold:
            return DecisionResult(
                "tie", None,
                f"the two best candidates differ by "
                f"{runner_score - best_score:.6g}, within the pre-registered "
                f"tie threshold {tie_threshold:.6g}",
                {"scores": dict(live), "disqualified": disqualified},
            )
    return DecisionResult(
        "unique_winner", best_name,
        f"{best_name} is best by more than the tie threshold",
        {"scores": dict(live), "disqualified": disqualified},
    )


__all__ = [
    "SCHEMA_VERSION", "PlanError", "SimulationMemberSpec", "build_plan",
    "validate_plan", "matched_pairs", "CostVector", "require_same_fields",
    "select_worst", "weighted_expectation", "max_regret", "DecisionResult",
    "decide",
]
