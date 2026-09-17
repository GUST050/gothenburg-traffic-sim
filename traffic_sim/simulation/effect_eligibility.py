"""Effect eligibility of AUTOMATICALLY chosen closure benchmark cases.

Structural survivability (the closure survivability screen and
``tools.cost_ordered_benchmark.surviving_roads``) answers one question: does
closing this directed edge leave the network usable? It says nothing about
whether any calibrated vehicle uses the edge. Nearness to a sensor is not
route support either (see ``traffic_sim.demand.route_support``). An
automatically chosen performance or WindowCostIndex case needs both answers,
kept apart: a structurally sound closure of an edge that no route uses prices
every candidate at exactly zero, so ranking, exactness and speed evidence
gathered on it is degenerate.

This contract is only for cases a tool picks by itself. It is never applied
to a closure a user chose: closing a directed edge without traffic is a
legitimate request, and its honest answer is zero.

Two stages, reported separately:

* pre-canary -- the edge is in the SUMO network; every archive binds catalog
  pools whose route files match their declared SHA-256; at least one route in
  those pools uses the edge; at least one archive variant carries a real
  vehicle over it;
* canary -- a bounded production computation reported
  ``affected_vehicles_total > 0`` and at least one closure cost above zero.

``effect_eligible`` is true only when both stages have passed.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from traffic_sim.demand.route_support import _artifact_identity
from traffic_sim.simulation.deterministic_disruption import VARIANT_FILENAMES

CONTRACT_VERSION = "effect_eligibility_v1"

EDGE_NOT_IN_NETWORK = "edge_not_in_network"
NO_ARCHIVE = "no_archive_to_assess"
CATALOG_POOL_UNBOUND = "archive_binds_no_catalog_pool"
CATALOG_POOL_MISSING = "bound_catalog_pool_file_missing"
CATALOG_POOL_MISMATCH = "bound_catalog_pool_sha256_mismatch"
NO_CATALOG_ROUTE = "no_catalog_route_uses_edge"
NO_ARCHIVE_VEHICLE = "no_archive_vehicle_crosses_edge"
CANARY_NOT_RUN = "canary_not_run"
CANARY_NO_AFFECTED_VEHICLE = "canary_affected_vehicles_total_zero"
CANARY_NO_POSITIVE_COST = "canary_no_closure_cost_above_zero"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=512)
def _edge_counts_cached(identity: tuple[str, int, int],
                        element: str) -> Mapping[str, int]:
    """Per-edge count of ``route`` or ``vehicle`` records in one file.

    A vehicle counts once per edge its embedded route uses, which is exactly
    "vehicles crossing the edge"; a route counts once per edge it uses.
    """
    counts: Counter = Counter()
    for _event, node in ET.iterparse(identity[0], events=("end",)):
        if node.tag == element:
            route = node if element == "route" else node.find("route")
            if route is not None:
                counts.update(set((route.get("edges") or "").split()))
            node.clear()
    return dict(counts)


def edge_counts(path: Path, element: str) -> Mapping[str, int]:
    """Cached per-edge counts, invalidated by the file's size and mtime."""
    if element not in ("route", "vehicle"):
        raise ValueError(f"unsupported element {element!r}")
    return _edge_counts_cached(_artifact_identity(Path(path)), element)


@dataclass(frozen=True)
class CatalogPool:
    """One catalog pool as an archive's metadata binds it."""

    pool: str
    key: str
    path: Path
    declared_sha256: str


def bound_catalog_pools(archive: Path,
                        catalog_root: Path) -> tuple[CatalogPool, ...]:
    """The catalog pools an archive's metadata binds, possibly none."""
    metadata = json.loads((Path(archive) / "demand_meta.json").read_text(
        encoding="utf-8"))
    catalog = metadata.get("candidate_catalog") or {}
    artifacts = catalog.get("artifacts") or {}
    return tuple(
        CatalogPool(
            pool=str(pool), key=str(key),
            path=Path(catalog_root) / str(key) / "catalog.rou.xml",
            declared_sha256=str(
                (artifacts.get(pool) or {}).get("routes_sha256", "")))
        for pool, key in sorted((catalog.get("keys") or {}).items()))


@dataclass(frozen=True)
class EdgeEffectEvidence:
    """What the artifacts say about one directed edge; no judgement yet."""

    edge_id: str
    in_network: bool
    archives: int
    pool_problems: tuple[str, ...]
    catalog_routes: Mapping[str, int]
    archive_vehicles: Mapping[str, int]
    archives_with_vehicle: int
    canary_affected_vehicles_total: int | None = None
    canary_positive_costs: int | None = None

    def with_canary(self, *, affected_vehicles_total: int,
                    positive_costs: int) -> "EdgeEffectEvidence":
        """A copy carrying the bounded canary's two effect measurements."""
        return replace(
            self,
            canary_affected_vehicles_total=int(affected_vehicles_total),
            canary_positive_costs=int(positive_costs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "in_network": self.in_network,
            "archives": self.archives,
            "pool_problems": list(self.pool_problems),
            "catalog_routes": dict(sorted(self.catalog_routes.items())),
            "archive_vehicles": dict(sorted(self.archive_vehicles.items())),
            "archives_with_vehicle": self.archives_with_vehicle,
            "canary_affected_vehicles_total":
                self.canary_affected_vehicles_total,
            "canary_positive_costs": self.canary_positive_costs,
        }


@dataclass(frozen=True)
class EffectAssessment:
    """The contract's verdict for one edge, with every failed condition."""

    edge_id: str
    pre_canary_eligible: bool
    effect_eligible: bool
    reasons: tuple[str, ...]
    evidence: EdgeEffectEvidence = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT_VERSION,
            "edge_id": self.edge_id,
            "pre_canary_eligible": self.pre_canary_eligible,
            "effect_eligible": self.effect_eligible,
            "reasons": list(self.reasons),
            "evidence": self.evidence.to_dict(),
        }


def collect_evidence(
    edges: Iterable[str],
    *,
    network_edges: frozenset[str] | set[str],
    archives: Sequence[Path],
    catalog_root: Path,
) -> dict[str, EdgeEffectEvidence]:
    """Read the network, the bound catalog pools and the archives once."""
    edges = sorted({str(edge) for edge in edges})
    problems: set[str] = set()
    pools: dict[str, CatalogPool] = {}
    vehicles = {edge: Counter() for edge in edges}
    with_vehicle: Counter = Counter()
    for archive in archives:
        bound = bound_catalog_pools(Path(archive), Path(catalog_root))
        if not bound:
            problems.add(CATALOG_POOL_UNBOUND)
        for pool in bound:
            if not pool.path.is_file():
                problems.add(CATALOG_POOL_MISSING)
            elif _sha256(pool.path) != pool.declared_sha256:
                problems.add(CATALOG_POOL_MISMATCH)
            else:
                pools.setdefault(f"{pool.pool}:{pool.key}", pool)
        carried = set()
        for variant, filename in sorted(VARIANT_FILENAMES.items()):
            counts = edge_counts(Path(archive) / filename, "vehicle")
            for edge in edges:
                if counts.get(edge, 0):
                    vehicles[edge][variant] += counts[edge]
                    carried.add(edge)
        with_vehicle.update(carried)
    catalog = {name: edge_counts(pool.path, "route")
               for name, pool in sorted(pools.items())}
    return {
        edge: EdgeEffectEvidence(
            edge_id=edge,
            in_network=edge in network_edges,
            archives=len(archives),
            pool_problems=tuple(sorted(problems)),
            catalog_routes={name: counts.get(edge, 0)
                            for name, counts in catalog.items()},
            archive_vehicles={variant: vehicles[edge].get(variant, 0)
                              for variant in sorted(VARIANT_FILENAMES)},
            archives_with_vehicle=with_vehicle.get(edge, 0),
        )
        for edge in edges
    }


def assess(evidence: EdgeEffectEvidence) -> EffectAssessment:
    """Apply the contract; every failed condition is named, in order."""
    reasons: list[str] = []
    if not evidence.in_network:
        reasons.append(EDGE_NOT_IN_NETWORK)
    if evidence.archives == 0:
        reasons.append(NO_ARCHIVE)
    reasons.extend(evidence.pool_problems)
    if sum(evidence.catalog_routes.values()) == 0:
        reasons.append(NO_CATALOG_ROUTE)
    if sum(evidence.archive_vehicles.values()) == 0:
        reasons.append(NO_ARCHIVE_VEHICLE)
    pre_canary = not reasons
    if evidence.canary_affected_vehicles_total is None:
        reasons.append(CANARY_NOT_RUN)
    else:
        if evidence.canary_affected_vehicles_total <= 0:
            reasons.append(CANARY_NO_AFFECTED_VEHICLE)
        if not evidence.canary_positive_costs:
            reasons.append(CANARY_NO_POSITIVE_COST)
    return EffectAssessment(
        edge_id=evidence.edge_id,
        pre_canary_eligible=pre_canary,
        effect_eligible=not reasons,
        reasons=tuple(reasons),
        evidence=evidence,
    )
