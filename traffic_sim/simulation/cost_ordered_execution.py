"""Cost-first product execution: cost everything, simulate almost nothing.

Stage 1 of the cost-ordered product integration, on top of PR D's process-free
provider and PR E's state machine.

WHAT WAS MISSING.  PR E proved the ordering argument and registered a screening
mode, but that mode ran the exhaustive search FIRST and replayed the
cost-ordered scan afterwards. A post-hoc replay demonstrates the answer would
have been the same; it saves no SUMO, because every candidate was already
simulated. This module is the real execution: costs come first, and SUMO runs
only for the candidates the boundary requires.

THE ORDER OF OPERATIONS IS THE POINT.

    1. cost_units    every daily unit costed from calibrated routes, no SUMO
    2. cost_parents  daily costs summed PER VARIANT into a parent cost
    3. health_scan   no-detour parents refused before any simulation
    4. pilot         SUMO in cost order, stopping at the boundary
    5. finalists     the unchanged selector decides

Nothing here decides anything. The finalist set goes to
`select_pilot_finalists` exactly as the exhaustive path hands it over, so
`ready`, `capacity_exceeded`, `no_viable` and `incomplete` keep their meanings
and a finalist set can never be silently truncated.

DURABILITY.  A real search runs for hours and will be interrupted. Every
durable transition — the ledger, and each individual verification — is
published before the next step begins, and the resume refuses anything it
cannot prove is the same search: the provider and network identity, the policy,
the costing sources, the ordered cost ledger, and the exact verified prefix. A
cursor that does not match its prefix, a viable candidate that was never
verified, or evidence that is missing for a candidate the cursor claims is
done, all fail closed rather than continuing from a state nobody can explain.

RELEASE STATUS.  Everything this module writes carries
`release_evidence: false`. It is execution, not a claim: policy v3 is not
activated, no global-best claim opens, and the UI exposes nothing new.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from traffic_sim.core.contracts import ClosureSchedule, ClosureSearchSpec
from traffic_sim.simulation.closure_ranking import ClosureCost
from traffic_sim.simulation.cost_ordered_search import (
    CostOrderedCandidate,
    CostOrderedResult,
    CostOrderedState,
    identity_key,
    plan_order,
    run_cost_ordered_search,
)
from traffic_sim.simulation.deterministic_disruption import (
    DISRUPTION_SCHEMA,
    parent_closure_cost,
)
from traffic_sim.simulation.finalist_decision import CandidateEvidence
from traffic_sim.simulation.pilot_selection import PilotPolicy

SCHEMA = "cost_ordered_execution_v1"
SCHEMA_VERSION = 1

#: Workspace artifact kinds. The ledger is written once; the cursor is
#: republished after every verification, which is what makes a crash resumable
#: to the exact candidate rather than to the start of the phase.
COST_LEDGER_KIND = "cost_ordered_cost_ledger"
CURSOR_KIND = "cost_ordered_cursor"
#: The end-of-pilot account: how many candidates SUMO would have run against
#: how many it did, plus the stop proof for the ones it did not.
EXECUTION_KIND = "cost_ordered_execution_result"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ParentCost:
    """One parent's deterministic cost and the evidence behind it."""

    candidate_id: str
    cost: ClosureCost
    per_variant: tuple[Mapping[str, Any], ...]
    daily_unit_ids: tuple[str, ...] = ()
    cache_hits: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "cost": {
                "added_vehicle_hours": float(self.cost.added_vehicle_hours),
                "added_metres_total": float(self.cost.added_metres_total),
                "vehicles_affected": int(self.cost.vehicles_affected),
                "vehicles_no_detour": int(self.cost.vehicles_no_detour),
            },
            "per_variant": [dict(item) for item in self.per_variant],
            "daily_unit_ids": list(self.daily_unit_ids),
            "cache_hits": int(self.cache_hits),
        }


class DeterministicCostSource(Protocol):
    """Whatever can price a parent schedule without starting a simulator."""

    def identity(self) -> Mapping[str, Any]:
        """The immutable inputs every price is bound to."""

    def parent_cost(self, parent: ClosureSchedule) -> ParentCost:
        """One parent's cost, summed per variant then reduced field-wise."""


class IndependentDailyCostSource:
    """Prices a parent by pricing its daily units and summing them.

    The daily units are the reusable thing — the same unit appears in many
    overlapping parents — so the cache lives at unit level and a parent's price
    is usually free after the first schedule that touched those days.
    """

    def __init__(
        self,
        spec: ClosureSearchSpec,
        *,
        daily_units_for: Callable[[ClosureSchedule],
                                  Sequence[tuple[str, ClosureSchedule]]],
        provider_for: Callable[[ClosureSchedule], Any],
        cache: Any = None,
        window_cost_index: Any = None,
    ) -> None:
        self.spec = spec
        self._daily_units_for = daily_units_for
        self._provider_for = provider_for
        self._cache = cache
        self._window_cost_index = window_cost_index
        self._providers: dict[str, Any] = {}
        self._identity: dict[str, Any] | None = None
        if window_cost_index is not None:
            bound = getattr(window_cost_index, "bound_identity", {})
            if not isinstance(bound, Mapping) \
                    or bound.get("search_content_key") != spec.content_key:
                raise ValueError(
                    "window cost index is not bound to this search spec")
            provider_identity = bound.get("provider_identity")
            if not isinstance(provider_identity, Mapping):
                raise ValueError(
                    "window cost index lacks its provider identity")
            self._identity = dict(provider_identity)
        self.cache_hits = 0
        self.cache_misses = 0
        self.memory_cache_hits = 0
        self.memory_cache_misses = 0
        self.disk_cache_hits = 0
        self.disk_cache_misses = 0
        self.index_lookups = 0
        self.computed_units = 0
        # Population telemetry for the cold-ledger contract.  These are
        # distinct daily units, not parent-to-unit lookups: overlapping five
        # day parents must not inflate the 1,950 x 3 population.
        self._profile_unit_ids: set[str] = set()
        self._profile_variant_records = 0
        self._timings: dict[str, float] = {
            "xml_parse": 0.0,
            "route_vehicle_grouping": 0.0,
            "shortest_path_detour": 0.0,
            "window_aggregation": 0.0,
            "parent_aggregation_sorting": 0.0,
        }
        self._profile_daily_records: dict[str, dict[str, Any]] = {}

    def _provider(self, unit_schedule: ClosureSchedule):
        key = unit_schedule.schedule_id
        provider = self._providers.get(key)
        if provider is None:
            provider = self._provider_for(unit_schedule)
            self._providers[key] = provider
        return provider

    def identity(self) -> Mapping[str, Any]:
        if self._identity is None:
            raise RuntimeError(
                "the cost source has no identity until it has priced "
                "something: the archive identities are resolved per daily unit")
        return dict(self._identity)

    def validate_window_cost_index_binding(
        self, unit_id: str, unit_schedule: ClosureSchedule) -> None:
        """Reconstruct the current provider identity before the first lookup.

        Index JSON can validate its own bytes, but only the active resolver can
        independently reconstruct the archive, network and costing-source
        identity for this invocation.  This check intentionally performs no
        deterministic calculation and therefore keeps index loading fail
        closed without paying the old per-window cost.
        """
        if self._window_cost_index is None:
            return
        provider = self._provider(unit_schedule)
        actual = dict(provider.identity())
        bound = getattr(self._window_cost_index, "bound_identity", {})
        expected_by_unit = bound.get("provider_identities")
        expected = (expected_by_unit.get(str(unit_id))
                    if isinstance(expected_by_unit, Mapping)
                    else bound.get("provider_identity"))
        if not isinstance(expected, Mapping) or dict(expected) != actual:
            raise ValueError(
                "window cost index provider/input identity is stale or swapped")
        self._identity = actual

    def parent_cost(self, parent: ClosureSchedule) -> ParentCost:
        units = list(self._daily_units_for(parent))
        if not units:
            raise ValueError(
                f"parent {parent.schedule_id} has no daily units to price")
        daily_records: list[Sequence[Mapping[str, Any]]] = []
        unit_ids: list[str] = []
        hits = 0
        for unit_id, unit_schedule in units:
            if self._window_cost_index is not None:
                self.validate_window_cost_index_binding(unit_id, unit_schedule)
                records = self._window_cost_index.lookup(
                    unit_id, unit_schedule.schedule_id)
                self.index_lookups += 1
            else:
                provider = self._provider(unit_schedule)
                # Observe the two cache layers at their actual seams without
                # changing the deterministic provider (whose source digest is
                # part of the bound cost identity).  Providers retain one
                # memory record per daily schedule; the optional DailyCostCache
                # is consulted only after that memory miss.
                memory_hit = unit_schedule.schedule_id in getattr(
                    provider, "_memory", {})
                disk_hit = None
                if not memory_hit and self._cache is not None:
                    cache_identity = getattr(provider, "cache_identity", None)
                    cache_key = getattr(self._cache, "key", None)
                    path_for = getattr(self._cache, "path_for", None)
                    if callable(cache_identity) and callable(cache_key) \
                            and callable(path_for):
                        disk_hit = Path(path_for(cache_key(
                            cache_identity(unit_schedule)))).is_file()
                records = provider.disruption(unit_schedule)
                if memory_hit:
                    self.memory_cache_hits += 1
                else:
                    self.memory_cache_misses += 1
                if disk_hit is True:
                    self.disk_cache_hits += 1
                elif disk_hit is False:
                    self.disk_cache_misses += 1
            variants = tuple(str(item.get("demand_variant", ""))
                             for item in records)
            if variants != ("q10", "q50", "q90"):
                raise ValueError(
                    "deterministic cost provider must return one actual "
                    "q10/q50/q90 record per daily unit")
            if unit_id not in self._profile_unit_ids:
                self._profile_unit_ids.add(unit_id)
                self._profile_variant_records += len(records)
                self._profile_daily_records[unit_id] = {
                    "schedule_id": unit_schedule.schedule_id,
                    "records": tuple(dict(item) for item in records),
                }
            provider = (None if self._window_cost_index is not None
                        else provider)
            snapshot = getattr(provider, "timing_snapshot", None)
            if callable(snapshot):
                for phase, elapsed in snapshot().items():
                    if phase in self._timings:
                        # Providers expose cumulative values.  The snapshot is
                        # sampled below after each unit, so retain a separate
                        # per-provider watermark to avoid double counting.
                        previous = getattr(provider, "_profile_timing_seen", {})
                        delta = max(0.0, float(elapsed) -
                                    float(previous.get(phase, 0.0)))
                        self._timings[phase] += delta
                        previous[phase] = float(elapsed)
                        setattr(provider, "_profile_timing_seen", previous)
            if self._window_cost_index is None:
                if memory_hit:
                    hits += 1
            daily_records.append(records)
            unit_ids.append(unit_id)
            if self._identity is None:
                # Every unit's provider shares the network, the spec and the
                # costing sources; only the archive differs, and the archive of
                # each unit is already bound through the unit's own cache key.
                identity = dict(provider.identity())
                identity.pop("demand", None)
                self._identity = identity
        # Per-provider deltas above already update the aggregate memory
        # counter.  Do not add the local compatibility tally a second time.
        self.cache_hits = self.memory_cache_hits
        self.cache_misses = self.memory_cache_misses
        self.computed_units += len(units)
        aggregate_started = time.perf_counter()
        combined = parent_closure_cost(parent.schedule_id, daily_records)
        self._timings["parent_aggregation_sorting"] += (
            time.perf_counter() - aggregate_started)
        from traffic_sim.simulation.deterministic_disruption import (
            sum_daily_disruption,
        )
        return ParentCost(
            candidate_id=parent.schedule_id,
            cost=combined,
            per_variant=sum_daily_disruption(daily_records),
            daily_unit_ids=tuple(unit_ids),
            cache_hits=hits,
        )

    def timing_snapshot(self) -> Mapping[str, float]:
        """Return deterministic parent-aggregation timing for profiling."""
        return dict(self._timings)

    def population_snapshot(self) -> Mapping[str, int]:
        """Return the actual distinct unit/variant records observed."""
        return {
            "daily_units": len(self._profile_unit_ids),
            "daily_variant_records": self._profile_variant_records,
        }

    def cache_snapshot(self) -> Mapping[str, int]:
        """Return parent-facing and disk cache counters with one meaning."""
        return {
            "lookups": int(self.memory_cache_hits + self.memory_cache_misses),
            "memory_cache_hits": int(self.memory_cache_hits),
            "memory_cache_misses": int(self.memory_cache_misses),
            "disk_cache_lookups": int(self.disk_cache_hits +
                                       self.disk_cache_misses),
            "disk_cache_hits": int(self.disk_cache_hits),
            "disk_cache_misses": int(self.disk_cache_misses),
            "index_lookups": int(self.index_lookups),
        }

    def daily_records_snapshot(self) -> Mapping[str, Mapping[str, Any]]:
        """Return the exact daily oracle population observed by this source."""
        return {
            unit_id: {
                "schedule_id": str(value["schedule_id"]),
                "records": [dict(item) for item in value["records"]],
            }
            for unit_id, value in self._profile_daily_records.items()
        }


@dataclass(frozen=True)
class CostLedger:
    """Every candidate's deterministic price, frozen before any simulation."""

    search_content_key: str
    provider_identity: Mapping[str, Any]
    costs: tuple[ParentCost, ...]
    cache_hits: int
    computed_units: int
    cache_misses: int = 0
    memory_cache_hits: int = 0
    memory_cache_misses: int = 0
    disk_cache_hits: int = 0
    disk_cache_misses: int = 0

    @property
    def candidates(self) -> tuple[CostOrderedCandidate, ...]:
        return tuple(
            CostOrderedCandidate(
                candidate_id=item.candidate_id,
                cost=item.cost,
                disruption=tuple(item.per_variant),
            )
            for item in self.costs
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": SCHEMA,
            "version": SCHEMA_VERSION,
            "kind": COST_LEDGER_KIND,
            "release_evidence": False,
            "search_content_key": self.search_content_key,
            "disruption_schema": DISRUPTION_SCHEMA,
            "provider_identity": dict(self.provider_identity),
            "candidate_count": len(self.costs),
            "cache_hits": int(self.cache_hits),
            "cache_misses": int(self.cache_misses),
            "memory_cache_hits": int(self.memory_cache_hits),
            "memory_cache_misses": int(self.memory_cache_misses),
            "disk_cache_hits": int(self.disk_cache_hits),
            "disk_cache_misses": int(self.disk_cache_misses),
            "computed_daily_units": int(self.computed_units),
            "costs": [item.to_dict() for item in self.costs],
        }
        payload["content_key"] = _digest(payload)
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CostLedger":
        if not isinstance(raw, Mapping):
            raise ValueError("cost ledger must be an object")
        if (raw.get("schema") != SCHEMA
                or raw.get("version") != SCHEMA_VERSION):
            raise ValueError("cost ledger schema is unsupported")
        body = {key: value for key, value in raw.items() if key != "content_key"}
        if raw.get("content_key") != _digest(body):
            raise ValueError("cost ledger content key does not match its body")
        costs = []
        for item in raw.get("costs", ()):
            cost = item["cost"]
            costs.append(ParentCost(
                candidate_id=str(item["candidate_id"]),
                cost=ClosureCost(
                    candidate_id=str(item["candidate_id"]),
                    added_vehicle_hours=float(cost["added_vehicle_hours"]),
                    added_metres_total=float(cost["added_metres_total"]),
                    vehicles_affected=int(cost["vehicles_affected"]),
                    vehicles_no_detour=int(cost["vehicles_no_detour"]),
                ),
                per_variant=tuple(dict(record)
                                  for record in item.get("per_variant", ())),
                daily_unit_ids=tuple(str(value)
                                     for value in item.get("daily_unit_ids", ())),
                cache_hits=int(item.get("cache_hits", 0)),
            ))
        return cls(
            search_content_key=str(raw["search_content_key"]),
            provider_identity=dict(raw.get("provider_identity") or {}),
            costs=tuple(costs),
            cache_hits=int(raw.get("cache_hits", 0)),
            computed_units=int(raw.get("computed_daily_units", 0)),
            cache_misses=int(raw.get("cache_misses", 0)),
            memory_cache_hits=int(raw.get("memory_cache_hits", 0)),
            memory_cache_misses=int(raw.get("memory_cache_misses", 0)),
            disk_cache_hits=int(raw.get("disk_cache_hits", 0)),
            disk_cache_misses=int(raw.get("disk_cache_misses", 0)),
        )


def build_cost_ledger(
    spec: ClosureSearchSpec,
    parents: Sequence[ClosureSchedule],
    source: DeterministicCostSource,
    *,
    progress: Callable[[str, int, int, Mapping[str, Any]], None] | None = None,
) -> CostLedger:
    """Price every candidate before anything is simulated.

    This is the phase the plan calls `cost_units` and `cost_parents`. It starts
    no SUMO, and the progress callback reports real counters — how many
    candidates are priced and how many daily units came from the cache — rather
    than a spinner.
    """
    costs: list[ParentCost] = []
    total = len(parents)
    for index, parent in enumerate(parents):
        if progress is not None:
            progress("cost_units", index, total, {
                "costed": index,
                "cost_total": total,
                "cache_hits": getattr(source, "cache_hits", 0),
            })
        costs.append(source.parent_cost(parent))
    if progress is not None:
        progress("cost_parents", total, total, {
            "costed": total,
            "cost_total": total,
            "cache_hits": getattr(source, "cache_hits", 0),
        })
    cache = (dict(source.cache_snapshot())
             if callable(getattr(source, "cache_snapshot", None)) else {})
    memory_hits = int(cache.get("memory_cache_hits", getattr(
        source, "cache_hits", 0)))
    memory_misses = int(cache.get("memory_cache_misses", 0))
    return CostLedger(
        search_content_key=spec.content_key,
        provider_identity=dict(source.identity()),
        costs=tuple(costs),
        cache_hits=memory_hits,
        computed_units=int(getattr(source, "computed_units", 0)),
        cache_misses=memory_misses,
        memory_cache_hits=memory_hits,
        memory_cache_misses=memory_misses,
        disk_cache_hits=int(cache.get("disk_cache_hits", 0)),
        disk_cache_misses=int(cache.get("disk_cache_misses", 0)),
    )


@dataclass(frozen=True)
class ExecutionCursor:
    """The durable position of a cost-ordered run.

    Republished after every verification. `bound_identity` is what a resume
    must still be able to reproduce: the ledger's content key, the provider
    identity, the policy and the practical-equivalence band. If any of them
    moved, the cursor describes a search that no longer exists.
    """

    bound_identity: str
    ledger_content_key: str
    state: CostOrderedState

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "version": SCHEMA_VERSION,
            "kind": CURSOR_KIND,
            "release_evidence": False,
            "bound_identity": self.bound_identity,
            "ledger_content_key": self.ledger_content_key,
            "state": self.state.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ExecutionCursor":
        if not isinstance(raw, Mapping):
            raise ValueError("cost-ordered cursor must be an object")
        if (raw.get("schema") != SCHEMA
                or raw.get("version") != SCHEMA_VERSION):
            raise ValueError("cost-ordered cursor schema is unsupported")
        state = CostOrderedState.from_dict(raw.get("state") or {})
        state.validate()
        return cls(
            bound_identity=str(raw["bound_identity"]),
            ledger_content_key=str(raw["ledger_content_key"]),
            state=state,
        )


def bound_identity(
    ledger: CostLedger,
    policy: PilotPolicy,
    *,
    practical_equivalence_vehicle_hours: float,
    disable_early_stop: bool = False,
) -> str:
    """One digest over everything a resume must not survive."""
    # The state machine binds the exact order it scans: deterministic
    # no-detour candidates removed, then ClosureCost.sort_key order.  Binding
    # the ledger's enumeration order here creates a cursor that the state
    # machine itself rejects on resume whenever calendar order differs from
    # cost order (the real v3 benchmark reproduced this after one injected
    # interruption).  The full ledger, including refused candidates and its
    # original order, remains independently bound by ``ledger_content_key``.
    ordered, _disqualified = plan_order(ledger.candidates)
    return identity_key(
        search_content_key=ledger.search_content_key,
        policy=policy,
        practical_equivalence_vehicle_hours=(
            practical_equivalence_vehicle_hours),
        provider_identity=ledger.provider_identity,
        ordered_costs=ordered,
        disable_early_stop=disable_early_stop,
    )


def run_cost_ordered_execution(
    spec: ClosureSearchSpec,
    ledger: CostLedger,
    policy: PilotPolicy,
    *,
    verify: Callable[[str], CandidateEvidence],
    practical_equivalence_vehicle_hours: float = 0.0,
    cursor: ExecutionCursor | None = None,
    verified_evidence: Mapping[str, CandidateEvidence] | None = None,
    checkpoint: Callable[[ExecutionCursor, str, CandidateEvidence], None] | None = None,
    progress: Callable[[str, int, int, Mapping[str, Any]], None] | None = None,
    disable_early_stop: bool = False,
    max_verifications: int | None = None,
    max_exact_launches: int | None = None,
) -> CostOrderedResult:
    """Verify in cost order, checkpointing after every SUMO run.

    `verify` is the REAL runner — this is where SUMO actually happens, and it
    happens only for candidates inside the boundary. `checkpoint` is called
    with the cursor AFTER each verification so a crash resumes at the exact
    next candidate rather than repeating the phase.

    `disable_early_stop=True` is the ordered-exhaustive reference execution:
    same ledger, same order, same verify callback, same checkpointing, but
    every candidate is verified regardless of the band. See
    `cost_ordered_search.run_cost_ordered_search` for why this is the correct
    way to isolate early stopping as the only difference being measured.
    """
    if ledger.search_content_key != spec.content_key:
        raise ValueError("cost ledger belongs to another search")
    identity = bound_identity(
        ledger, policy,
        practical_equivalence_vehicle_hours=(
            practical_equivalence_vehicle_hours),
        disable_early_stop=disable_early_stop,
    )
    ledger_key = ledger.to_dict()["content_key"]

    state = None
    if cursor is not None:
        if cursor.bound_identity != identity:
            raise ValueError(
                "cost-ordered resume is invalid: the policy, cost ledger or "
                "provider identity changed since the cursor was written")
        if cursor.ledger_content_key != ledger_key:
            raise ValueError(
                "cost-ordered resume is invalid: the cost ledger content key "
                "changed since the cursor was written")
        cursor.state.validate()
        state = cursor.state

    candidates = ledger.candidates
    ordered, _refused = plan_order(candidates)
    order = tuple(cost.candidate_id for cost in ordered)
    cost_by_id = {cost.candidate_id: cost for cost in ordered}
    total_orderable = len(order)

    # The checkpoint mirrors the scan's own bookkeeping rather than reaching
    # into it. `cost_ordered_search` is bound by the frozen golden record and
    # must stay byte-identical, so the durable cursor is reconstructed here
    # from the same inputs and the same rule — and `_assert_matches` below
    # proves at the end of every run that the mirror never diverged.
    verified: list[str] = list(state.verified) if state is not None else []
    viable: list[str] = list(state.viable) if state is not None else []

    def _cutoff() -> float | None:
        if len(viable) < policy.minimum_finalists:
            return None
        return float(
            cost_by_id[viable[policy.minimum_finalists - 1]].added_vehicle_hours)

    def verify_and_checkpoint(candidate_id: str) -> CandidateEvidence:
        evidence = verify(candidate_id)
        if not isinstance(evidence, CandidateEvidence):
            raise ValueError("cost-ordered verification must return evidence")
        if evidence.candidate_id != candidate_id:
            raise ValueError(
                "cost-ordered verification returned another candidate")
        verified.append(candidate_id)
        if evidence.eligible:
            viable.append(candidate_id)
        if progress is not None:
            progress("pilot", len(verified), total_orderable, {
                "verified": len(verified),
                "total": total_orderable,
                "cache_hits": ledger.cache_hits,
                "cutoff": _cutoff(),
            })
        if checkpoint is not None:
            mirrored = CostOrderedState(
                identity_key=identity,
                order=order,
                cursor=len(verified),
                verified=tuple(verified),
                viable=tuple(viable),
                cutoff=_cutoff(),
            )
            # Refuse to persist a position that is not internally consistent,
            # rather than writing a cursor a resume would then reject.
            mirrored.validate()
            checkpoint(
                ExecutionCursor(
                    bound_identity=identity,
                    ledger_content_key=ledger_key,
                    state=mirrored,
                ),
                candidate_id,
                evidence,
            )
        return evidence

    result = run_cost_ordered_search(
        candidates, policy,
        verify=verify_and_checkpoint,
        search_content_key=spec.content_key,
        provider_identity=ledger.provider_identity,
        practical_equivalence_vehicle_hours=(
            practical_equivalence_vehicle_hours),
        state=state,
        verified_evidence=verified_evidence,
        disable_early_stop=disable_early_stop,
        max_verifications=max_verifications,
        max_exact_launches=max_exact_launches,
    )
    # The per-candidate checkpoint above is deliberately written before the
    # search can inspect the returned evidence.  Publish the terminal cursor
    # as a second immutable checkpoint so a timeout/budget stop cannot be
    # reopened as active work after restart.  Re-entering an already terminal
    # cursor is idempotent and does not invoke ``verify`` again.
    if (checkpoint is not None and result.terminal_status is not None
            and (cursor is None or cursor.state.stop_reason is None)):
        if not result.state.verified:
            raise ValueError(
                "a terminal cost-ordered result has no verified cursor prefix")
        last_id = result.state.verified[-1]
        last_evidence = next(
            item for item in result.evidence if item.candidate_id == last_id)
        checkpoint(
            ExecutionCursor(
                bound_identity=identity,
                ledger_content_key=ledger_key,
                state=result.state,
            ),
            last_id,
            last_evidence,
        )
    # The mirror and the scan must agree, or a resumed run would continue from
    # a cursor describing a position the scan was never at.
    if (tuple(result.state.verified) != tuple(verified)
            or tuple(result.state.viable) != tuple(viable)):
        raise ValueError(
            "the durable cursor diverged from the cost-ordered scan; the "
            "persisted position does not describe this run")
    return result


def reconcile_disruption(
    evidence: CandidateEvidence,
    priced: ParentCost,
) -> CandidateEvidence:
    """Bind the runner's evidence to the price the ordering was built on.

    Two things happen here, and the second is a gate rather than plumbing.

    If the runner returned no disruption — which it need not, since the price
    was already computed before it ran — the ledger's records are attached, so
    the selector sees the same numbers the ordering used.

    If the runner DID return disruption, it must be FIELD-IDENTICAL to the
    ledger's. That is the pre-SUMO / post-SUMO equivalence the plan's benchmark
    gate asks for, checked on every candidate of every run rather than only in
    a benchmark: if the two ever disagreed, the candidate was ordered by one
    number and judged by another, and the ordering argument would be void.
    """
    if not evidence.disruption:
        return CandidateEvidence(
            candidate_id=evidence.candidate_id,
            observations=evidence.observations,
            hard_failures=evidence.hard_failures,
            disruption=tuple(priced.per_variant),
            timeout_undecided=evidence.timeout_undecided,
            canonical_observation_digests=(
                evidence.canonical_observation_digests),
        )
    produced = {str(item.get("demand_variant")): item
                for item in evidence.disruption}
    expected = {str(item.get("demand_variant")): item
                for item in priced.per_variant}
    if set(produced) != set(expected):
        raise ValueError(
            f"runner disruption for {evidence.candidate_id} covers "
            f"{sorted(produced)} but the cost ledger covers {sorted(expected)}")
    for variant, wanted in expected.items():
        actual = produced[variant]
        for field in ("vehicles_affected", "vehicles_no_detour",
                      "added_vehicle_hours", "added_metres_total"):
            if float(actual[field]) != float(wanted[field]):
                raise ValueError(
                    f"deterministic cost disagreement for "
                    f"{evidence.candidate_id} {variant} {field}: the runner "
                    f"reports {actual[field]} but the cost ledger the ordering "
                    f"used says {wanted[field]}")
    return evidence


def execution_record(
    spec: ClosureSearchSpec,
    ledger: CostLedger,
    result: CostOrderedResult,
    *,
    exhaustive_candidate_count: int,
    wall_time_s: float | None = None,
    peak_rss_bytes: int | None = None,
    disable_early_stop: bool = False,
) -> dict[str, Any]:
    """The durable account of what this run actually did.

    Deliberately reports the SAVING as a measured pair — how many candidates an
    exhaustive run would have simulated against how many this one did — rather
    than as a percentage, because the pair is checkable and the percentage is
    not.
    """
    payload = {
        "schema": SCHEMA,
        "version": SCHEMA_VERSION,
        "kind": "cost_ordered_execution_result",
        "release_evidence": False,
        "evidence_class": "product_execution",
        "search_content_key": spec.content_key,
        "provider_identity": dict(ledger.provider_identity),
        "cost_ledger_content_key": ledger.to_dict()["content_key"],
        "status": result.selection.status,
        "terminal_status": result.terminal_status,
        "selected_ids": list(result.selection.selected_ids),
        "ordered_candidates": len(result.ordered_costs),
        "deterministically_disqualified": len(result.disqualified),
        "exhaustive_sumo_candidates": int(exhaustive_candidate_count),
        "cost_ordered_sumo_candidates": int(result.verified_count),
        "sumo_verifications_saved": (
            int(exhaustive_candidate_count) - int(result.verified_count)),
        "daily_cost_cache_hits": int(ledger.cache_hits),
        "computed_daily_units": int(ledger.computed_units),
        "cutoff": result.state.cutoff,
        "stop_proof": dict(result.stop_proof),
        "cursor": result.state.to_dict(),
        "disable_early_stop": bool(disable_early_stop),
        "candidate_statuses": dict(result.candidate_statuses),
    }
    if wall_time_s is not None:
        payload["wall_time_s"] = round(float(wall_time_s), 3)
    if peak_rss_bytes is not None:
        payload["peak_rss_bytes"] = int(peak_rss_bytes)
    payload["content_key"] = _digest(payload)
    return payload
