"""Deterministic closure cost, computed without starting SUMO.

PR D / step 3 of
`docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`.

WHY.  The ranking objective `closure_cost_v1` is already demand-side and
congestion-independent: it reads calibrated route files, replays each vehicle's
baseline path against the closure windows, and compares the cheapest legal path
with and without the closure.  Nothing in that needs a simulator.  Until now it
was nevertheless reachable only through `ArchivedDemandSumoRunner`, so a search
had to construct a SUMO runner — and in the cost-ordered search of PR E, would
have had to SIMULATE every candidate — before it could learn which candidates
were even worth simulating.

WHAT THIS MODULE IS.  A public, process-free provider:

    provider = ArchiveDisruptionProvider(spec, archive=..., network=...)
    records  = provider.disruption(schedule)      # one per q10/q50/q90
    cost     = provider.closure_cost(schedule)    # field-wise worst variant

No TraCI, no SUMO process, no simulation output.  `DeterministicDisruptionProvider`
is the protocol; nothing in it may expose a live simulation handle.

IDENTITY IS THE POINT.  A daily cost is cached, so what the key binds decides
whether a cached number can silently describe different inputs.  It binds the
FULL daily-unit identity, the SHA-256 of each of the three route files, the
network SHA-256, the demand metadata, the disruption schema version and the
bytes of every source that computes the number.  A widened date range that
produces the same daily unit therefore HITS; a changed route file, network,
variant or line of costing code MISSES.

FAIL CLOSED.  A missing variant, a cache record whose stored identity does not
match the key it was found under, or a route/network identity that has moved,
raises instead of degrading to a plausible number.  `vehicles_no_detour` is
never folded into a cost — it disqualifies, with its evidence kept.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from traffic_sim.core.contracts import ClosureSchedule, ClosureSearchSpec
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation.closure_ranking import ClosureCost, worst_variant_cost

#: Version of the deterministic disruption contract. Any change to how a
#: record is computed or shaped must bump it; it is part of every cache key.
DISRUPTION_SCHEMA = "deterministic_closure_disruption_v1"

#: Version of the on-disk daily-cost cache.
DAILY_COST_CACHE_SCHEMA = "deterministic_daily_cost_cache_v1"

DEMAND_VARIANTS = ("q10", "q50", "q90")

#: Which calibrated route file carries which direction-split variant. Defined
#: here, in a module with no simulation dependency, because both the SUMO
#: runner and this process-free provider must agree on it exactly.
VARIANT_FILENAMES = {
    "q50": "calibrated.rou.xml",
    "q10": "calibrated_v1.rou.xml",
    "q90": "calibrated_v2.rou.xml",
}

#: Sources whose bytes change the NUMBER this module produces. A change to any
#: of them must miss the cache — a cached cost is only meaningful against the
#: code that computed it.
COSTING_SOURCES = (
    "run_scenario.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/closure_ranking.py",
)


class DisruptionError(Exception):
    """Base class for a deterministic-cost fault. Never a silent fallback."""


class DisruptionUnavailable(DisruptionError):
    """The inputs a cost needs are missing or do not match their identity."""


class DailyCostCacheCorrupt(DisruptionError):
    """A cached record does not match the identity it was stored under."""


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _digest(payload: Any, *, length: int = 64) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:length]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def costing_source_identity() -> dict[str, str]:
    """SHA-256 of every source that decides the cost number."""
    root = _project_root()
    identity: dict[str, str] = {}
    for name in COSTING_SOURCES:
        digest = sha256_file(root / name)
        if digest is None:
            raise DisruptionUnavailable(
                f"deterministic cost source is missing: {name}")
        identity[name] = digest
    return identity


class DeterministicDisruptionProvider(Protocol):
    """One schedule in, per-variant deterministic detour evidence out.

    Deliberately tiny, and deliberately free of any simulation handle: a
    caller that can only speak this protocol cannot start SUMO, cannot open a
    TraCI connection and cannot read a simulation outcome.
    """

    def identity(self) -> Mapping[str, Any]:
        """The immutable inputs this provider's numbers are bound to."""

    def disruption(
        self, schedule: ClosureSchedule
    ) -> tuple[Mapping[str, Any], ...]:
        """One record per demand variant, in q10/q50/q90 order."""


def closure_seconds(
    spec: ClosureSearchSpec,
    schedule: ClosureSchedule,
    *,
    epoch: datetime,
    duration_s: int,
) -> list[dict[str, Any]]:
    """Closure windows in seconds from an archive epoch.

    Extracted so the process-free provider and `ArchivedDemandSumoRunner` share
    ONE conversion. Two implementations of "when is this edge shut" is how a
    pre-SUMO cost and a post-SUMO cost quietly stop describing the same closure.
    """
    closures: list[dict[str, Any]] = []
    for interval in schedule.intervals:
        begin = int(
            (datetime.fromisoformat(interval.start_time) - epoch).total_seconds()
        )
        end = int(
            (datetime.fromisoformat(interval.end_time) - epoch).total_seconds()
        )
        if begin < 0 or end <= begin or end > duration_s:
            raise ValueError("closure interval lies outside demand archive")
        for edge in spec.directed_edges:
            closures.append({
                "edge_id": edge,
                "begin_s": begin,
                "end_s": end,
            })
    return closures


class NetworkCostModel:
    """Free-flow edge costs and the unbanned edge graph, built once.

    A search evaluates thousands of schedules against ONE network. Rebuilding
    the adjacency and free-flow tables per schedule is the difference between
    a costing pass that takes seconds and one that takes hours, so the model is
    constructed once and shared, and it carries the network digest it was built
    from so it cannot be reused against a different network.
    """

    def __init__(self, network_path: Path | None = None) -> None:
        import run_scenario as rs  # noqa: PLC0415 - heavy; imported on use

        self.network_path = Path(
            network_path if network_path is not None else rs.NET_PATH)
        if not self.network_path.is_file():
            raise DisruptionUnavailable(
                f"deterministic cost needs the SUMO network: "
                f"{self.network_path}")
        self.network_sha256 = sha256_file(self.network_path)
        self.adjacency = rs.build_edge_graph(set())
        self.edge_time, self.edge_len = rs.free_flow_edge_cost()

    def identity(self) -> dict[str, Any]:
        return {
            "path": str(self.network_path),
            "sha256": self.network_sha256,
        }


class DailyCostCache:
    """Content-addressed store for one daily unit's deterministic cost.

    The stored record repeats its own identity. That is not redundancy: it is
    what lets a read verify that the file found at a key really describes the
    inputs the caller asked about, rather than trusting the path.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def key(identity: Mapping[str, Any]) -> str:
        return _digest(identity)

    def path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def load(self, identity: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...] | None:
        key = self.key(identity)
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DailyCostCacheCorrupt(
                f"deterministic daily cost cache entry is unreadable: {path}"
            ) from error
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema") != DAILY_COST_CACHE_SCHEMA
            or payload.get("key") != key
            or payload.get("identity") != dict(identity)
        ):
            raise DailyCostCacheCorrupt(
                f"deterministic daily cost cache entry does not match its "
                f"identity: {path}")
        records = payload.get("disruption")
        if not isinstance(records, list) or len(records) != len(DEMAND_VARIANTS):
            raise DailyCostCacheCorrupt(
                f"deterministic daily cost cache entry lacks q10/q50/q90: "
                f"{path}")
        variants = [str(item.get("demand_variant", "")) for item in records]
        if variants != list(DEMAND_VARIANTS):
            raise DailyCostCacheCorrupt(
                f"deterministic daily cost cache entry has the wrong variant "
                f"order: {path}")
        return tuple(dict(item) for item in records)

    def store(
        self,
        identity: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
    ) -> Path:
        key = self.key(identity)
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": DAILY_COST_CACHE_SCHEMA,
            "key": key,
            "identity": dict(identity),
            "disruption": [dict(item) for item in records],
        }
        temporary = path.with_name(path.name + ".partial")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return path


@dataclass(frozen=True)
class ArchiveInputs:
    """One immutable demand archive, resolved to exactly three route files."""

    archive: Path
    variant_paths: Mapping[str, Path]
    epoch: datetime
    duration_s: int
    demand_meta_sha256: str | None

    @classmethod
    def from_archive(cls, archive: Path) -> "ArchiveInputs":
        archive = Path(archive)
        meta_path = archive / "demand_meta.json"
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DisruptionUnavailable(
                f"demand archive has no readable demand_meta.json: {archive}"
            ) from error
        variant_paths = {}
        for variant, filename in VARIANT_FILENAMES.items():
            path = (archive / filename).resolve()
            if not path.is_file():
                raise DisruptionUnavailable(
                    f"demand archive lacks the {variant} route file: {path}")
            variant_paths[variant] = path
        try:
            epoch = datetime.fromisoformat(str(metadata["epoch_sim"]))
            intervals = int(metadata["n_intervals"])
        except (KeyError, TypeError, ValueError) as error:
            raise DisruptionUnavailable(
                f"demand archive metadata lacks an epoch or interval count: "
                f"{archive}") from error
        return cls(
            archive=archive,
            variant_paths=variant_paths,
            epoch=epoch,
            duration_s=intervals * 900,
            demand_meta_sha256=sha256_file(meta_path),
        )

    def identity(self) -> dict[str, Any]:
        return {
            "archive": str(self.archive),
            "demand_meta_sha256": self.demand_meta_sha256,
            "epoch_sim": self.epoch.isoformat(),
            "duration_s": self.duration_s,
            "variant_routes": {
                variant: {
                    "filename": VARIANT_FILENAMES[variant],
                    "sha256": sha256_file(path),
                }
                for variant, path in sorted(self.variant_paths.items())
            },
        }


class ArchiveDisruptionProvider:
    """Deterministic closure cost from one archive, with no SUMO process.

    Implements :class:`DeterministicDisruptionProvider`.
    """

    def __init__(
        self,
        spec: ClosureSearchSpec,
        *,
        archive: Path,
        network: NetworkCostModel | None = None,
        cache: DailyCostCache | None = None,
        unit_identity: Mapping[str, Any] | None = None,
    ) -> None:
        self.spec = ClosureSearchSpec.from_dict(spec.to_dict())
        self.inputs = ArchiveInputs.from_archive(archive)
        self.network = network if network is not None else NetworkCostModel()
        self.cache = cache
        self._unit_identity = (
            dict(unit_identity) if unit_identity is not None else None)
        self._sources = costing_source_identity()
        self._memory: dict[str, tuple[Mapping[str, Any], ...]] = {}

    # -- identity ---------------------------------------------------------

    def identity(self) -> dict[str, Any]:
        """Everything a cached number is bound to except the schedule itself."""
        return {
            "schema": DISRUPTION_SCHEMA,
            "interday_policy": self.spec.interday_policy,
            "closure_type": self.spec.closure_type,
            "directed_edges": sorted(self.spec.directed_edges),
            "source": self.spec.source,
            "timezone": self.spec.timezone,
            "dst_policy": self.spec.dst_policy,
            "demand": self.inputs.identity(),
            "network": self.network.identity(),
            "costing_sources": dict(self._sources),
        }

    def cache_identity(self, schedule: ClosureSchedule) -> dict[str, Any]:
        """The full key for one daily unit's cost.

        The schedule's own identity is included as its canonical dictionary,
        not merely its ID: an ID is a digest of the contract, and a cache that
        keys on a digest of a digest is one contract change away from silently
        matching the wrong thing.
        """
        identity = self.identity()
        identity["schedule"] = schedule.to_dict()
        if self._unit_identity is not None:
            identity["daily_unit"] = dict(self._unit_identity)
        return identity

    # -- computation ------------------------------------------------------

    def _compute(
        self, schedule: ClosureSchedule
    ) -> tuple[Mapping[str, Any], ...]:
        import run_scenario as rs  # noqa: PLC0415 - heavy; imported on use

        closures = closure_seconds(
            self.spec, schedule,
            epoch=self.inputs.epoch,
            duration_s=self.inputs.duration_s,
        )
        closed = set(self.spec.directed_edges)
        records: list[Mapping[str, Any]] = []
        for variant in DEMAND_VARIANTS:
            report = rs.closure_disruption(
                self.inputs.variant_paths[variant],
                closed,
                closures,
                self.network.edge_time,
                self.network.edge_len,
                adj=self.network.adjacency,
            )
            if report is None:
                raise DisruptionUnavailable(
                    f"closure disruption is unavailable for {variant}")
            records.append({"demand_variant": variant, **report})
        return tuple(records)

    def disruption(
        self, schedule: ClosureSchedule
    ) -> tuple[Mapping[str, Any], ...]:
        """Per-variant detour evidence for one schedule, cached by identity."""
        if schedule.search_content_key != self.spec.content_key:
            raise DisruptionUnavailable(
                "schedule does not belong to this search spec")
        remembered = self._memory.get(schedule.schedule_id)
        if remembered is not None:
            return remembered
        identity = self.cache_identity(schedule)
        if self.cache is not None:
            cached = self.cache.load(identity)
            if cached is not None:
                self._memory[schedule.schedule_id] = cached
                return cached
        records = self._compute(schedule)
        if self.cache is not None:
            self.cache.store(identity, records)
        self._memory[schedule.schedule_id] = records
        return records

    def closure_cost(self, schedule: ClosureSchedule) -> ClosureCost:
        """Field-wise worst variant for ONE schedule."""
        return worst_variant_cost(
            schedule.schedule_id, list(self.disruption(schedule)))


def sum_daily_disruption(
    daily_records: Sequence[Sequence[Mapping[str, Any]]],
) -> tuple[dict[str, Any], ...]:
    """Sum a parent's daily records PER VARIANT, before any worst-case step.

    Order matters and is not interchangeable. Summing first and reducing after
    asks "what does this whole schedule cost under one directional assumption",
    which is the question. Reducing first and summing after would let a
    schedule take its worst day from q10 and its worst day from q90 and add
    them, describing a world in which the direction split changed overnight.

    This mirrors `independent_daily.aggregate_daily_evidence` exactly, so the
    pre-SUMO and post-SUMO paths reduce identically.
    """
    if not daily_records:
        raise DisruptionUnavailable(
            "a parent schedule must have at least one daily unit")
    indexed: list[dict[str, Mapping[str, Any]]] = []
    for records in daily_records:
        by_variant: dict[str, Mapping[str, Any]] = {}
        for record in records:
            variant = str(record.get("demand_variant", ""))
            if variant not in DEMAND_VARIANTS or variant in by_variant:
                raise DisruptionUnavailable(
                    "daily disruption evidence must contain one unique record "
                    "for each q10/q50/q90 variant")
            by_variant[variant] = record
        if set(by_variant) != set(DEMAND_VARIANTS):
            raise DisruptionUnavailable(
                "daily disruption evidence lacks q10/q50/q90 coverage")
        indexed.append(by_variant)

    combined: list[dict[str, Any]] = []
    for variant in DEMAND_VARIANTS:
        records = [item[variant] for item in indexed]
        combined.append({
            "demand_variant": variant,
            "vehicles_affected": sum(
                int(item["vehicles_affected"]) for item in records),
            "vehicles_considered": sum(
                int(item.get("vehicles_considered", 0)) for item in records),
            "vehicles_no_detour": sum(
                int(item["vehicles_no_detour"]) for item in records),
            "added_vehicle_hours": round(sum(
                float(item["added_vehicle_hours"]) for item in records), 4),
            "added_metres_total": round(sum(
                float(item["added_metres_total"]) for item in records), 1),
            "basis": (
                "sum of independent daily calibrated-route closure costs"),
            "reduction": "sum across independent daily reset units",
        })
    return tuple(combined)


def parent_closure_cost(
    candidate_id: str,
    daily_records: Sequence[Sequence[Mapping[str, Any]]],
) -> ClosureCost:
    """Sum per variant, then take the field-wise worst — in that order."""
    return worst_variant_cost(
        candidate_id, list(sum_daily_disruption(daily_records)))


def disqualification_evidence(
    cost: ClosureCost,
    combined: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Why a candidate was refused before SUMO, with the numbers intact.

    A no-detour candidate is not "expensive"; it is one that leaves a driver
    unable to reach their destination by car. The evidence is kept in full so
    the refusal can be read and argued with, rather than reduced to a flag.
    """
    return {
        "reason": "deterministic_no_detour",
        "schema": DISRUPTION_SCHEMA,
        "vehicles_no_detour": int(cost.vehicles_no_detour),
        "vehicles_affected": int(cost.vehicles_affected),
        "added_vehicle_hours": float(cost.added_vehicle_hours),
        "added_metres_total": float(cost.added_metres_total),
        "per_variant": [dict(item) for item in combined],
        "decided_before_sumo": True,
    }
