"""Versioned effect eligibility for AUTOMATIC closure-case selection.

Structural discovery (``tools.cost_ordered_benchmark.surviving_roads`` and
``discovered_specs``) proves only that closing a directed edge leaves the
network usable; it may name edges that no calibrated route uses. When a tool
picks a WCI, benchmark or performance case by itself, the policy
``closure_effect_eligibility_v1`` additionally requires a verified traffic
effect in the exact archives the case will use. It is never applied to a
closure a user chose: a directed edge without traffic is a legitimate
closure, and its honest answer is zero.

Policy versions. Artifacts that name no ``case_selection_policy`` were
selected by ``structural_survivability_v1`` and are replayed with it.

Definition of ``closure_effect_eligibility_v1`` for one directed edge and the
build keys a case requires (reason codes in brackets; ``eligible`` when none
applies):

* the edge is in the SUMO network [missing_network_edge];
* every required build key has an inventoried archive
  [incomplete_archive_coverage];
* every such archive binds catalog pools in metadata, each pool file exists
  and matches its declared SHA-256, and one pool name never maps to two
  catalogs [missing_required_pool];
* every archive has q10, q50 and q90, each with a resolver-validated SHA-256
  and unchanged while it was parsed [missing_required_variant];
* every bound pool has at least one catalog route using the edge
  [no_catalog_route_support];
* each of q10, q50 and q90 has at least one vehicle crossing the edge, and
  at least one daily unit belongs to an archive with a crossing
  [no_observed_archive_crossings].

The inventory takes the candidate edge IDs, parses each catalog pool and each
archive variant exactly once with the production route parser
(``traffic_sim.simulation.disruption.parse_route_vehicles``), and freezes one
table ``edge -> variant -> build_key -> crossing vehicles`` for all
candidates. Archive variants are not rehashed here: their SHA-256 comes from
the production resolver that validated them. ``verify_inventory`` rehashes
every bound file later, so any catalog or archive drift invalidates the
evidence.

This module deliberately lives in ``tools/``, outside
``traffic_sim.demand.source_identity.demand_source_paths``: it only selects
benchmark cases and must not change the identity of any demand archive.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from traffic_sim.simulation.deterministic_disruption import VARIANT_FILENAMES
from traffic_sim.simulation.disruption import parse_route_vehicles

POLICY = "closure_effect_eligibility_v1"
LEGACY_POLICY = "structural_survivability_v1"
POLICIES = (LEGACY_POLICY, POLICY)
REQUIRED_VARIANTS = tuple(sorted(VARIANT_FILENAMES))

MISSING_NETWORK_EDGE = "missing_network_edge"
INCOMPLETE_ARCHIVE_COVERAGE = "incomplete_archive_coverage"
MISSING_REQUIRED_POOL = "missing_required_pool"
MISSING_REQUIRED_VARIANT = "missing_required_variant"
NO_CATALOG_ROUTE_SUPPORT = "no_catalog_route_support"
NO_OBSERVED_ARCHIVE_CROSSINGS = "no_observed_archive_crossings"
ELIGIBLE = "eligible"
REASON_ORDER = (MISSING_NETWORK_EDGE, INCOMPLETE_ARCHIVE_COVERAGE,
                MISSING_REQUIRED_POOL, MISSING_REQUIRED_VARIANT,
                NO_CATALOG_ROUTE_SUPPORT, NO_OBSERVED_ARCHIVE_CROSSINGS,
                ELIGIBLE)


def policy_of(record: Mapping[str, Any] | None) -> str:
    """The case-selection policy an artifact was produced under."""
    policy = (record or {}).get("case_selection_policy", LEGACY_POLICY)
    if policy not in POLICIES:
        raise ValueError(f"unknown case_selection_policy {policy!r}")
    return policy


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _state(path: Path) -> tuple[int, int, int]:
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


@dataclass(frozen=True)
class ArchiveRef:
    """One qualified archive with the variant hashes its resolver bound."""

    build_key: str
    archive: Path
    content_key: str | None = None
    variant_sha256: Mapping[str, str] = field(default_factory=dict)


def archive_refs(resolved: Mapping[str, Mapping[str, Any]]) -> list[ArchiveRef]:
    """Adapt resolver records (``routes`` or validator ``outputs``)."""
    by_file = {name: variant for variant, name in VARIANT_FILENAMES.items()}
    refs = []
    for key, record in sorted((resolved or {}).items()):
        hashes = {variant: item["sha256"]
                  for variant, item in (record.get("routes") or {}).items()
                  if item.get("sha256")}
        for item in record.get("outputs") or ():
            if item.get("name") in by_file and item.get("sha256"):
                hashes.setdefault(by_file[item["name"]], item["sha256"])
        refs.append(ArchiveRef(build_key=str(key),
                               archive=Path(record["archive"]),
                               content_key=record.get("archive_content_key"),
                               variant_sha256=hashes))
    return refs


def _count(parsed, wanted: frozenset[str]) -> dict[str, int]:
    counts = dict.fromkeys(wanted, 0)
    for vehicle in parsed:
        for edge in wanted.intersection(vehicle[0]):
            counts[edge] += 1
    return counts


def _bound_pools(archive: Path) -> dict[str, dict[str, str]]:
    metadata = json.loads((archive / "demand_meta.json").read_text(
        encoding="utf-8"))
    catalog = metadata.get("candidate_catalog") or {}
    artifacts = catalog.get("artifacts") or {}
    return {str(pool): {"key": str(key), "declared_sha256": str(
                (artifacts.get(pool) or {}).get("routes_sha256", ""))}
            for pool, key in sorted((catalog.get("keys") or {}).items())}


@dataclass(frozen=True)
class Inventory:
    """One frozen, single-parse view of the archives and catalog pools."""

    candidates: tuple[str, ...]
    archives: Mapping[str, Mapping[str, Any]]
    catalogs: Mapping[str, Mapping[str, Any]]
    crossings: Mapping[str, Mapping[str, Mapping[str, int]]]
    catalog_routes: Mapping[str, Mapping[str, int]]
    parses: Mapping[str, int]

    def body(self) -> dict[str, Any]:
        return {"schema": "closure_effect_inventory_v1", "policy": POLICY,
                "candidates": list(self.candidates),
                "archives": self.archives, "catalogs": self.catalogs,
                "crossings": self.crossings,
                "catalog_routes": self.catalog_routes,
                "parses": self.parses}

    @property
    def content_key(self) -> str:
        return _digest(self.body())

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(
            {**self.body(), "content_key": self.content_key}, default=str))


def _parse_archive(ref: ArchiveRef, wanted, crossings, parses):
    variants = {}
    for variant in REQUIRED_VARIANTS:
        path = Path(ref.archive) / VARIANT_FILENAMES[variant]
        if not path.is_file():
            variants[variant] = {"present": False}
            continue
        before = _state(path)
        parsed = parse_route_vehicles(path)
        stable = _state(path) == before
        parses[str(path)] = parses.get(str(path), 0) + 1
        for edge, count in _count(parsed, wanted).items():
            crossings[edge][variant][ref.build_key] = count
        variants[variant] = {
            "present": True, "stable": stable, "vehicles": len(parsed),
            "validated_sha256": (ref.variant_sha256 or {}).get(variant)}
    return {"archive": str(Path(ref.archive).resolve()),
            "content_key": ref.content_key,
            "pools": _bound_pools(Path(ref.archive)),
            "variants": variants}


def build_inventory(candidates: Iterable[str], archives: Sequence[ArchiveRef],
                    *, catalog_root: Path) -> Inventory:
    """Parse each archive variant and each bound catalog pool exactly once."""
    wanted = frozenset(str(edge) for edge in candidates)
    refs = sorted(archives, key=lambda ref: ref.build_key)
    if len({ref.build_key for ref in refs}) != len(refs):
        raise ValueError("one build key bound to two archives")
    crossings = {edge: {variant: {} for variant in REQUIRED_VARIANTS}
                 for edge in sorted(wanted)}
    parses: dict[str, int] = {}
    records = {ref.build_key: _parse_archive(ref, wanted, crossings, parses)
               for ref in refs}
    catalogs: dict[str, dict[str, Any]] = {}
    for record in records.values():
        for pool, binding in record["pools"].items():
            entry = catalogs.setdefault(binding["key"], {
                "pools": [], "declared_sha256": []})
            if pool not in entry["pools"]:
                entry["pools"].append(pool)
            if binding["declared_sha256"] not in entry["declared_sha256"]:
                entry["declared_sha256"].append(binding["declared_sha256"])
    catalog_routes: dict[str, dict[str, int]] = {}
    for key, entry in sorted(catalogs.items()):
        path = Path(catalog_root) / key / "catalog.rou.xml"
        entry.update(path=str(path.resolve()), present=path.is_file())
        if entry["present"]:
            entry["sha256"] = _sha256(path)
            before = _state(path)
            parsed = parse_route_vehicles(path)
            entry["stable"] = _state(path) == before
            parses[str(path)] = parses.get(str(path), 0) + 1
            entry["vehicles"] = len(parsed)
            catalog_routes[key] = _count(parsed, wanted)
    return Inventory(candidates=tuple(sorted(wanted)), archives=records,
                     catalogs=catalogs, crossings=crossings,
                     catalog_routes=catalog_routes, parses=parses)


@dataclass(frozen=True)
class Verdict:
    """The policy's verdict for one edge over one case's build keys."""

    edge_id: str
    effect_eligible: bool
    reasons: tuple[Mapping[str, Any], ...]
    summary: Mapping[str, Any]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        codes = {reason["code"] for reason in self.reasons}
        return tuple(code for code in REASON_ORDER if code in codes)

    def to_dict(self) -> dict[str, Any]:
        return {"policy": POLICY, "edge_id": self.edge_id,
                "effect_eligible": self.effect_eligible,
                "reason_codes": list(self.reason_codes),
                "reasons": [dict(reason) for reason in self.reasons],
                "summary": dict(self.summary)}


def _input_reasons(inventory: Inventory, keys: Sequence[str]):
    reasons = []
    pool_keys: dict[str, set[str]] = {}
    for key in keys:
        record = inventory.archives[key]
        if not record["pools"]:
            reasons.append((MISSING_REQUIRED_POOL, f"{key}:unbound"))
        for pool, binding in record["pools"].items():
            pool_keys.setdefault(pool, set()).add(binding["key"])
            catalog = inventory.catalogs[binding["key"]]
            if not catalog["present"]:
                reasons.append((MISSING_REQUIRED_POOL, f"{pool}:missing"))
            elif (catalog["sha256"] != binding["declared_sha256"]
                  or len(catalog["declared_sha256"]) != 1
                  or not catalog["stable"]):
                reasons.append((MISSING_REQUIRED_POOL,
                                f"{pool}:sha256_mismatch"))
        for variant in REQUIRED_VARIANTS:
            item = record["variants"][variant]
            if not item["present"]:
                reasons.append((MISSING_REQUIRED_VARIANT, f"{variant}@{key}"))
            elif not item["stable"] or not item["validated_sha256"]:
                reasons.append((MISSING_REQUIRED_VARIANT,
                                f"{variant}@{key}:unverified"))
    reasons.extend((MISSING_REQUIRED_POOL, f"{pool}:key_conflict")
                   for pool, used in sorted(pool_keys.items())
                   if len(used) > 1)
    return reasons


def _summary(edge, inventory, keys, daily_units):
    pools: dict[str, int] = {}
    for key in keys:
        for pool, binding in inventory.archives[key]["pools"].items():
            pools[pool] = int(inventory.catalog_routes.get(
                binding["key"], {}).get(edge, 0))
    table = inventory.crossings[edge]
    crossed = [key for key in keys
               if any(table[v].get(key, 0) for v in REQUIRED_VARIANTS)]
    return {
        "catalog_routes": dict(sorted(pools.items())),
        "crossings": {v: sum(table[v].get(key, 0) for key in keys)
                      for v in REQUIRED_VARIANTS},
        "archives": len(keys),
        "archives_with_crossings": len(crossed),
        "daily_units": sum(int(daily_units.get(key, 0)) for key in keys),
        "daily_units_with_crossings": sum(int(daily_units.get(key, 0))
                                          for key in crossed),
    }


def assess(edge: str, inventory: Inventory, *,
           network_edges: frozenset[str] | set[str],
           required_build_keys: Iterable[str],
           daily_units: Mapping[str, int]) -> Verdict:
    """Apply ``closure_effect_eligibility_v1`` to one edge."""
    edge = str(edge)
    if edge not in inventory.crossings:
        raise ValueError(f"{edge} is not an inventoried candidate")
    required = sorted(set(required_build_keys))
    keys = [key for key in required if key in inventory.archives]
    reasons: list[tuple[str, str | None]] = []
    if edge not in network_edges:
        reasons.append((MISSING_NETWORK_EDGE, None))
    missing = [key for key in required if key not in inventory.archives]
    if missing or not required:
        reasons.append((INCOMPLETE_ARCHIVE_COVERAGE,
                        ",".join(missing) or "no required build key"))
    reasons.extend(_input_reasons(inventory, keys))
    summary = _summary(edge, inventory, keys, daily_units)
    blocked = {code for code, _detail in reasons}
    if not blocked & {MISSING_REQUIRED_POOL, INCOMPLETE_ARCHIVE_COVERAGE}:
        reasons.extend((NO_CATALOG_ROUTE_SUPPORT, pool)
                       for pool, count in summary["catalog_routes"].items()
                       if count == 0)
    if not blocked & {MISSING_REQUIRED_VARIANT, INCOMPLETE_ARCHIVE_COVERAGE}:
        reasons.extend((NO_OBSERVED_ARCHIVE_CROSSINGS, variant)
                       for variant, count in summary["crossings"].items()
                       if count == 0)
        if keys and summary["daily_units_with_crossings"] == 0:
            reasons.append((NO_OBSERVED_ARCHIVE_CROSSINGS, "daily_units"))
    ordered = sorted(dict.fromkeys(reasons),
                     key=lambda item: REASON_ORDER.index(item[0]))
    return Verdict(
        edge_id=edge, effect_eligible=not ordered,
        reasons=tuple({"code": code, "detail": detail}
                      for code, detail in (ordered or [(ELIGIBLE, None)])),
        summary=summary)


def _eligible(verdict: Any) -> bool:
    if isinstance(verdict, Mapping):
        return bool(verdict.get("effect_eligible"))
    return bool(verdict.effect_eligible)


def eligible_in_order(items: Iterable[Any], verdict_of: Callable[[Any], Any],
                      *, order_key: Callable[[Any], Any]) -> list[Any]:
    """Filter on eligibility first, then apply the existing stable order."""
    return sorted((item for item in items if _eligible(verdict_of(item))),
                  key=order_key)


def screen_cases(cases: Sequence[tuple[Any, Mapping[str, Any]]], *,
                 network_edges: frozenset[str] | set[str],
                 catalog_root: Path,
                 daily_units_for: Callable[[Any], Mapping[str, int]],
                 ) -> tuple[dict[str, dict[str, Any]], Inventory]:
    """One inventory for all cases, then one verdict per case."""
    refs: dict[str, ArchiveRef] = {}
    edges: set[str] = set()
    for spec, resolved in cases:
        edges.update(spec.directed_edges)
        for ref in archive_refs(resolved):
            if refs.setdefault(ref.build_key, ref) != ref:
                raise ValueError(
                    f"build key {ref.build_key} resolved to two archives")
    inventory = build_inventory(edges, list(refs.values()),
                                catalog_root=catalog_root)
    verdicts: dict[str, dict[str, Any]] = {}
    for spec, resolved in cases:
        per_edge = [assess(edge, inventory, network_edges=network_edges,
                           required_build_keys=list(resolved or {}),
                           daily_units=daily_units_for(spec))
                    for edge in spec.directed_edges]
        eligible = all(item.effect_eligible for item in per_edge)
        codes = {code for item in per_edge for code in item.reason_codes}
        verdicts[spec.content_key] = {
            "policy": POLICY,
            "effect_eligible": eligible,
            "reason_codes": ([ELIGIBLE] if eligible else
                             [code for code in REASON_ORDER
                              if code in codes and code != ELIGIBLE]),
            "edges": [item.to_dict() for item in per_edge],
        }
    return verdicts, inventory


def verify_inventory(record: Mapping[str, Any]) -> list[str]:
    """Problems that invalidate inventory evidence now; empty when valid."""
    body = {key: value for key, value in record.items()
            if key != "content_key"}
    problems = []
    if _digest(body) != record.get("content_key"):
        problems.append("inventory content key mismatch")
    for key, archive in sorted((record.get("archives") or {}).items()):
        for variant, item in sorted(archive["variants"].items()):
            if not item.get("present"):
                continue
            path = Path(archive["archive"]) / VARIANT_FILENAMES[variant]
            if (not path.is_file()
                    or _sha256(path) != item.get("validated_sha256")):
                problems.append(f"archive {key} {variant} drifted")
    for key, catalog in sorted((record.get("catalogs") or {}).items()):
        path = Path(catalog["path"])
        if bool(catalog.get("present")) != path.is_file() or (
                path.is_file() and _sha256(path) != catalog.get("sha256")):
            problems.append(f"catalog {key} drifted")
    return problems
