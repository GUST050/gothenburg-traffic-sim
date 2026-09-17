"""Effect eligibility of AUTOMATICALLY chosen WCI, benchmark and performance
cases.

Two properties are kept apart:

* ``structurally_survivable`` (the closure survivability screen and
  ``tools.cost_ordered_benchmark.surviving_roads``): closing the directed edge
  leaves the network usable. General discovery uses only this, and may name
  edges that no current route uses.
* ``effect_eligible`` (this module): the closure has a verified, real traffic
  effect in the exact demand archives the case will use. Only automatic
  selection of WCI, benchmark or performance cases requires it. It is never a
  product requirement: a user may close a directed edge without traffic, and
  the honest answer to that is zero.

Definition (``effect_eligibility_v2``). For one directed edge, a set of
qualified archives (build keys) and their daily-unit counts:

Pre-canary stage, every condition required:

1. the edge is in the SUMO network;
2. every archive provides all three variants (q10, q50, q90), each matching
   the SHA-256 its resolver bound, if one was bound, and every vehicle carries
   an embedded route that can be counted;
3. every archive binds its catalog pools in metadata; every bound pool file
   exists and matches its declared SHA-256; one pool name never maps to two
   catalog keys;
4. every catalog pool the archives bind has at least one route using the
   edge (pool names come from metadata, never from file names);
5. each of q10, q50 and q90 has at least one vehicle crossing the edge;
6. at least one daily unit belongs to an archive with a crossing vehicle.

Canary stage, after a bounded production cost computation:

7. ``affected_vehicles_total > 0``;
8. at least one daily unit has an affected vehicle;
9. at least one computed closure cost is above zero.

``effect_eligible`` is true only when all nine hold. Every failed condition
has a machine-readable reason code (``code`` or ``code:detail``). A missing
input is always a rejection, never a zero.

The inventory reads every archive variant file and every catalog pool file
exactly once, hashing and counting in the same streaming pass, and freezes
``edge -> variant -> build_key -> crossing vehicles`` so that any number of
candidate edges are judged against one read of the archives.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from traffic_sim.simulation.deterministic_disruption import VARIANT_FILENAMES

CONTRACT_VERSION = "effect_eligibility_v2"
REQUIRED_VARIANTS = tuple(sorted(VARIANT_FILENAMES))

EDGE_NOT_IN_NETWORK = "edge_not_in_network"
NO_ARCHIVE = "no_archive"
VARIANT_MISSING = "archive_variant_missing"
VARIANT_SHA_MISMATCH = "archive_variant_sha256_mismatch"
VARIANT_UNEMBEDDED_ROUTE = "archive_variant_vehicle_without_embedded_route"
POOL_UNBOUND = "archive_catalog_pool_unbound"
POOL_FILE_MISSING = "catalog_pool_file_missing"
POOL_SHA_MISMATCH = "catalog_pool_sha256_mismatch"
POOL_KEY_CONFLICT = "catalog_pool_key_conflict"
NO_CATALOG_ROUTE = "no_catalog_route"
NO_CROSSING_VEHICLE = "no_crossing_vehicle"
NO_DAILY_UNIT_WITH_TRAFFIC = "no_daily_unit_with_crossing_traffic"
CANARY_NOT_RUN = "canary_not_run"
CANARY_NO_AFFECTED_VEHICLE = "canary_no_affected_vehicle"
CANARY_NO_AFFECTED_UNIT = "canary_no_affected_daily_unit"
CANARY_NO_POSITIVE_COST = "canary_no_cost_above_zero"


def _code(code: str, detail: str | None = None) -> str:
    return code if detail is None else f"{code}:{detail}"


@dataclass(frozen=True)
class ArchiveRef:
    """One qualified archive exactly as its resolver bound it."""

    build_key: str
    archive: Path
    content_key: str | None = None
    route_sha256: Mapping[str, str] | None = None


class _HashingReader:
    """A binary reader that hashes exactly the bytes the parser consumes."""

    def __init__(self, handle) -> None:
        self._handle = handle
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        self.digest.update(data)
        return data


def _stream_counts(path: Path, element: str, edges: frozenset[str]):
    """One pass: SHA-256, per-edge counts of ``element``, unusable records.

    For ``vehicle`` a count is one vehicle whose embedded route uses the edge;
    a vehicle without an embedded route cannot be counted and is reported.
    For ``route`` a count is one route using the edge.
    """
    counts = dict.fromkeys(edges, 0)
    unusable = 0
    with open(path, "rb") as handle:
        reader = _HashingReader(handle)
        root = None
        for event, node in ET.iterparse(reader, events=("start", "end")):
            if root is None:
                root = node
            if event != "end" or node.tag != element:
                continue
            route = node if element == "route" else node.find("route")
            if route is None or not route.get("edges"):
                unusable += 1
            else:
                for edge in edges.intersection(route.get("edges").split()):
                    counts[edge] += 1
            root.clear()
        while reader.read(1 << 20):
            pass
    return reader.digest.hexdigest(), counts, unusable


def _bound_pools(archive: Path) -> dict[str, dict[str, str]]:
    metadata = json.loads((Path(archive) / "demand_meta.json").read_text(
        encoding="utf-8"))
    catalog = metadata.get("candidate_catalog") or {}
    artifacts = catalog.get("artifacts") or {}
    return {
        str(pool): {
            "key": str(key),
            "declared_sha256": str(
                (artifacts.get(pool) or {}).get("routes_sha256", "")),
        }
        for pool, key in sorted((catalog.get("keys") or {}).items())
    }


@dataclass(frozen=True)
class TrafficInventory:
    """A frozen, single-read view of the archives and catalog pools."""

    edges: tuple[str, ...]
    archives: Mapping[str, Mapping[str, Any]]
    catalogs: Mapping[str, Mapping[str, Any]]
    vehicles: Mapping[str, Mapping[str, Mapping[str, int]]]
    file_reads: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT_VERSION,
            "edges": list(self.edges),
            "archives": {key: dict(value)
                         for key, value in sorted(self.archives.items())},
            "catalogs": {key: dict(value)
                         for key, value in sorted(self.catalogs.items())},
            "vehicles": {edge: {variant: dict(sorted(by_key.items()))
                                for variant, by_key in sorted(item.items())}
                         for edge, item in sorted(self.vehicles.items())},
            "file_reads": dict(sorted(self.file_reads.items())),
        }


def _read_archive(ref: ArchiveRef, edges: frozenset[str], vehicles,
                  file_reads) -> dict[str, Any]:
    archive = Path(ref.archive)
    problems: list[str] = []
    variants: dict[str, Any] = {}
    for variant in REQUIRED_VARIANTS:
        path = archive / VARIANT_FILENAMES[variant]
        if not path.is_file():
            problems.append(_code(VARIANT_MISSING, variant))
            variants[variant] = {"present": False}
            continue
        sha, counts, unusable = _stream_counts(path, "vehicle", edges)
        file_reads[str(path)] = file_reads.get(str(path), 0) + 1
        expected = (ref.route_sha256 or {}).get(variant)
        if expected is not None and expected != sha:
            problems.append(_code(VARIANT_SHA_MISMATCH, variant))
        if unusable:
            problems.append(_code(VARIANT_UNEMBEDDED_ROUTE, variant))
        variants[variant] = {"present": True, "sha256": sha,
                             "unusable_vehicles": unusable}
        for edge, count in counts.items():
            vehicles[edge][variant][ref.build_key] = count
    pools = _bound_pools(archive)
    if not pools:
        problems.append(POOL_UNBOUND)
    return {"archive": archive.name, "content_key": ref.content_key,
            "variants": variants, "pools": pools, "problems": problems}


def _read_catalogs(archives: Mapping[str, Any], edges: frozenset[str],
                   catalog_root: Path, file_reads) -> dict[str, Any]:
    catalogs: dict[str, Any] = {}
    for record in archives.values():
        for pool, binding in record["pools"].items():
            entry = catalogs.setdefault(binding["key"], {
                "pools": [], "declared_sha256": binding["declared_sha256"]})
            if pool not in entry["pools"]:
                entry["pools"].append(pool)
            if entry["declared_sha256"] != binding["declared_sha256"]:
                entry["declared_sha256_conflict"] = True
    for key, entry in sorted(catalogs.items()):
        path = Path(catalog_root) / key / "catalog.rou.xml"
        entry["path"] = str(path)
        entry["present"] = path.is_file()
        if entry["present"]:
            sha, counts, unusable = _stream_counts(path, "route", edges)
            file_reads[str(path)] = file_reads.get(str(path), 0) + 1
            entry.update(sha256=sha, unusable_routes=unusable,
                         edge_routes=dict(sorted(counts.items())))
    return catalogs


def build_inventory(archives: Sequence[ArchiveRef], edges: Iterable[str], *,
                    catalog_root: Path) -> TrafficInventory:
    """Read each archive variant and each catalog pool exactly once."""
    wanted = frozenset(str(edge) for edge in edges)
    refs = sorted(archives, key=lambda ref: ref.build_key)
    if len({ref.build_key for ref in refs}) != len(refs):
        raise ValueError("one build key bound to two archives")
    vehicles = {edge: {variant: {} for variant in REQUIRED_VARIANTS}
                for edge in wanted}
    file_reads: dict[str, int] = {}
    records = {ref.build_key: _read_archive(ref, wanted, vehicles, file_reads)
               for ref in refs}
    catalogs = _read_catalogs(records, wanted, Path(catalog_root), file_reads)
    return TrafficInventory(edges=tuple(sorted(wanted)), archives=records,
                            catalogs=catalogs, vehicles=vehicles,
                            file_reads=file_reads)


@dataclass(frozen=True)
class CanaryResult:
    """What a bounded production cost computation observed for one edge."""

    affected_vehicles_total: int
    affected_daily_units: int
    positive_costs: int


@dataclass(frozen=True)
class EffectVerdict:
    """The contract's verdict for one edge over one archive subset."""

    edge_id: str
    pre_canary_eligible: bool
    effect_eligible: bool
    reasons: tuple[str, ...]
    summary: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"contract": CONTRACT_VERSION, "edge_id": self.edge_id,
                "pre_canary_eligible": self.pre_canary_eligible,
                "effect_eligible": self.effect_eligible,
                "reasons": list(self.reasons),
                "summary": dict(self.summary)}


def _input_reasons(inventory: TrafficInventory,
                   keys: Sequence[str]) -> list[str]:
    reasons: list[str] = []
    pool_keys: dict[str, set[str]] = {}
    for key in keys:
        record = inventory.archives[key]
        reasons.extend(record["problems"])
        for pool, binding in record["pools"].items():
            pool_keys.setdefault(pool, set()).add(binding["key"])
            catalog = inventory.catalogs[binding["key"]]
            if not catalog["present"]:
                reasons.append(_code(POOL_FILE_MISSING, pool))
            elif (catalog["sha256"] != binding["declared_sha256"]
                  or catalog.get("declared_sha256_conflict")):
                reasons.append(_code(POOL_SHA_MISMATCH, pool))
    reasons.extend(_code(POOL_KEY_CONFLICT, pool)
                   for pool, used in sorted(pool_keys.items())
                   if len(used) > 1)
    return reasons


def _edge_summary(edge, inventory, keys, daily_units):
    pools: dict[str, int] = {}
    for key in keys:
        for pool, binding in inventory.archives[key]["pools"].items():
            catalog = inventory.catalogs[binding["key"]]
            pools[pool] = int((catalog.get("edge_routes") or {}).get(edge, 0))
    by_variant = inventory.vehicles[edge]
    trafficked = [key for key in keys
                  if any(by_variant[v].get(key, 0) for v in REQUIRED_VARIANTS)]
    return {
        "catalog_routes": dict(sorted(pools.items())),
        "vehicles": {variant: sum(by_variant[variant].get(key, 0)
                                  for key in keys)
                     for variant in REQUIRED_VARIANTS},
        "archives": len(keys),
        "archives_with_traffic": len(trafficked),
        "daily_units": sum(int(daily_units.get(key, 0)) for key in keys),
        "daily_units_with_traffic": sum(int(daily_units.get(key, 0))
                                        for key in trafficked),
    }


def assess(edge: str, inventory: TrafficInventory, *,
           network_edges: frozenset[str] | set[str],
           daily_units: Mapping[str, int],
           build_keys: Iterable[str] | None = None,
           canary: CanaryResult | None = None) -> EffectVerdict:
    """Apply the definition above to one edge over the given build keys."""
    edge = str(edge)
    if edge not in inventory.edges:
        raise ValueError(f"{edge} was not part of the frozen inventory")
    keys = sorted(inventory.archives if build_keys is None else build_keys)
    missing = [key for key in keys if key not in inventory.archives]
    if missing:
        raise ValueError(f"build keys outside the inventory: {missing}")
    reasons: list[str] = []
    if edge not in network_edges:
        reasons.append(EDGE_NOT_IN_NETWORK)
    if not keys:
        reasons.append(NO_ARCHIVE)
    reasons.extend(_input_reasons(inventory, keys))
    summary = _edge_summary(edge, inventory, keys, daily_units)
    reasons.extend(_code(NO_CATALOG_ROUTE, pool)
                   for pool, count in summary["catalog_routes"].items()
                   if count == 0)
    reasons.extend(_code(NO_CROSSING_VEHICLE, variant)
                   for variant, count in summary["vehicles"].items()
                   if count == 0)
    if keys and summary["daily_units_with_traffic"] == 0:
        reasons.append(NO_DAILY_UNIT_WITH_TRAFFIC)
    pre_canary = not reasons
    if canary is None:
        reasons.append(CANARY_NOT_RUN)
    else:
        summary["canary"] = {
            "affected_vehicles_total": canary.affected_vehicles_total,
            "affected_daily_units": canary.affected_daily_units,
            "positive_costs": canary.positive_costs}
        if canary.affected_vehicles_total <= 0:
            reasons.append(CANARY_NO_AFFECTED_VEHICLE)
        if canary.affected_daily_units <= 0:
            reasons.append(CANARY_NO_AFFECTED_UNIT)
        if canary.positive_costs <= 0:
            reasons.append(CANARY_NO_POSITIVE_COST)
    return EffectVerdict(edge_id=edge, pre_canary_eligible=pre_canary,
                         effect_eligible=not reasons,
                         reasons=tuple(dict.fromkeys(reasons)),
                         summary=summary)
