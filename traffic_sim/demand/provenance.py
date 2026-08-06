"""Cross-artifact validation for calibrated routes and candidate provenance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Sequence
import xml.etree.ElementTree as ET


DAY_PROVENANCE_NAME = "provenance.json"


def _candidate_records(path: Path) -> dict[str, dict]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    records = document.get("candidates")
    if not isinstance(records, dict):
        raise ValueError(f"candidate metadata is malformed: {path}")
    return records


def _load_routes(
    route_path: Path, noun: str = "calibrated vehicle",
) -> dict[str, tuple[tuple[str, ...], float | None]]:
    """Vehicle id -> (edges, depart) for one route XML, rejecting duplicates."""
    routes: dict[str, tuple[tuple[str, ...], float | None]] = {}
    for _event, element in ET.iterparse(route_path, events=("end",)):
        if element.tag != "vehicle":
            continue
        vehicle_id = element.get("id")
        route = element.find("route")
        edges = tuple((route.get("edges") if route is not None else "").split())
        if not vehicle_id or vehicle_id in routes or not edges:
            raise ValueError(
                f"invalid or duplicate {noun} in {route_path}: {vehicle_id!r}")
        depart = element.get("depart")
        routes[vehicle_id] = (edges,
                              float(depart) if depart is not None else None)
        element.clear()
    return routes


def _validate_variant(
    route_path: Path,
    agent_path: Path,
    candidates: dict[str, dict] | None = None,
    candidate_paths: dict[str, tuple[str, ...]] | None = None,
) -> int:
    """Check one variant's route/agent pair; return its vehicle count.

    ONE implementation of the structural rules, deliberately shared by both
    entry points so they cannot drift apart.

    ``candidates``/``candidate_paths`` additionally cross-reference every agent
    against the candidate pool it was drawn from. They are OPTIONAL because a
    candidate id is only an identity TOGETHER WITH the pool that produced it
    (the standard lineage rule that a bare name without its namespace is not an
    identity). Under the day library each calendar day draws its own pool and
    every day's own block reuses the ``d0_`` prefix, so a window assembled from
    several days has no single pool to resolve against. Those windows are
    proven per-day instead -- see validate_assembled_provenance -- and this
    call then enforces every remaining invariant that survives assembly.
    """
    routes = _load_routes(route_path)
    document = json.loads(Path(agent_path).read_text(encoding="utf-8"))
    agents = document.get("agents")
    if not isinstance(agents, list):
        raise ValueError(f"calibrated agent sidecar is malformed: {agent_path}")
    seen: set[str] = set()
    for agent in agents:
        if not isinstance(agent, dict):
            raise ValueError(f"calibrated agent record is malformed: {agent_path}")
        vehicle_id = agent.get("vehicle_id")
        if not isinstance(vehicle_id, str) or vehicle_id in seen:
            raise ValueError(
                f"invalid or duplicate calibrated agent in {agent_path}: "
                f"{vehicle_id!r}")
        seen.add(vehicle_id)
        if vehicle_id not in routes:
            raise ValueError(
                f"calibrated agent {vehicle_id!r} has no route in {route_path}")
        edges, departure = routes[vehicle_id]
        candidate_id = agent.get("candidate_id")
        if candidates is not None:
            candidate = candidates.get(candidate_id)
            if not isinstance(candidate_id, str) or not isinstance(candidate, dict):
                raise ValueError(
                    f"calibrated agent {vehicle_id!r} references unknown candidate "
                    f"{candidate_id!r}")
            if candidate_paths is not None \
                    and edges != candidate_paths[candidate_id]:
                raise ValueError(
                    f"calibrated agent {vehicle_id!r} route differs from "
                    f"candidate {candidate_id!r}")
            expected = {
                "purpose": candidate.get("purpose"),
                "origin_edge": candidate.get("origin_edge"),
                "destination_edge": candidate.get("destination_edge"),
            }
            actual = {key: agent.get(key) for key in expected}
            if actual != expected:
                raise ValueError(
                    f"calibrated agent {vehicle_id!r} provenance differs from "
                    f"candidate {candidate_id!r}")
        elif not isinstance(candidate_id, str) or not candidate_id:
            # Without the pool the id cannot be RESOLVED, but its presence is
            # still required: an agent that names no candidate at all has lost
            # the provenance the per-day proof established.
            raise ValueError(
                f"calibrated agent {vehicle_id!r} carries no candidate id "
                f"in {agent_path}")
        if agent.get("origin_edge") != edges[0] \
                or agent.get("destination_edge") != edges[-1]:
            raise ValueError(
                f"calibrated agent {vehicle_id!r} endpoints differ from its route")
        if agent.get("purpose_route_compatible") is not True:
            raise ValueError(
                f"calibrated agent {vehicle_id!r} has incompatible purpose")
        try:
            agent_departure = float(agent.get("departure_s"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"calibrated agent {vehicle_id!r} has invalid departure") from exc
        if departure is not None and abs(agent_departure - departure) > 1e-9:
            raise ValueError(
                f"calibrated agent {vehicle_id!r} departure differs from its route")
    if seen != set(routes):
        missing = sorted(set(routes) - seen)
        raise ValueError(
            f"{len(missing)} calibrated route(s) lack agent provenance in "
            f"{agent_path}: {missing[:3]}")
    return len(seen)


def validate_calibrated_provenance(
    candidate_routes: Path,
    candidate_metadata: Path,
    variants: Sequence[tuple[Path, Path]],
) -> dict:
    """Prove every emitted agent resolves to the exact candidate pool and route.

    The candidate sidecar is the source of OD/purpose intent; the route XML is
    the concrete path SUMO will simulate; the agent sidecar binds the two.
    Publication is refused unless all three agree one-for-one.
    """
    candidates = _candidate_records(Path(candidate_metadata))
    candidate_paths = {
        candidate_id: edges
        for candidate_id, (edges, _depart)
        in _load_routes(Path(candidate_routes), "candidate route").items()
    }
    if set(candidate_paths) != set(candidates):
        missing_routes = sorted(set(candidates) - set(candidate_paths))
        missing_metadata = sorted(set(candidate_paths) - set(candidates))
        raise ValueError(
            "candidate route/metadata identities differ: "
            f"routes_missing={missing_routes[:3]}, "
            f"metadata_missing={missing_metadata[:3]}")
    variant_reports = []
    total = 0
    for route_path, agent_path in variants:
        route_path, agent_path = Path(route_path), Path(agent_path)
        vehicles = _validate_variant(
            route_path, agent_path, candidates, candidate_paths)
        total += vehicles
        variant_reports.append({
            "route": route_path.name,
            "agents": agent_path.name,
            "vehicles": vehicles,
        })
    return {
        "schema_version": 1,
        "status": "pass",
        "mode": "single_pool",
        "candidate_records": len(candidates),
        "vehicles": total,
        "variants": variant_reports,
    }


def _stage_tree(tree: ET.ElementTree, destination: Path) -> Path:
    """Write and fsync an XML tree beside its destination for atomic replace."""
    descriptor, raw_path = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    staged = Path(raw_path)
    try:
        tree.write(staged, encoding="utf-8", xml_declaration=True)
        with staged.open("rb") as handle:
            os.fsync(handle.fileno())
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


def validate_assembled_provenance(
    day_directories: Sequence[Path],
    variants: Sequence[tuple[Path, Path]],
) -> dict:
    """Prove a window assembled from days that each drew their OWN pool.

    WHY THIS EXISTS (2026-08-06, found by the annual warming launch failing on
    its first demand build with "calibrated agent 'pfe0' references unknown
    candidate 'd0_1942'").

    A candidate id is only an identity together with the pool that produced it.
    Under the day library (stage B) every calendar day generates its own pool,
    and ``day_pool_blocks`` gives every day's own block the SAME ``d0_``
    prefix -- correct in isolation, because stage B calibrates each day ALONE
    so that a day's demand is a function of that day only. But the window loop
    regenerates candidates once per day and each call OVERWRITES the shared
    ``sumo/candidates.meta.json``, so after the loop only the LAST day's pool
    survives on disk. Validating an assembled window against that one pool asks
    day 0's agents to resolve against day 1's candidates: the ids collide but
    denote different routes, so the check either fails outright (what was
    observed) or -- where an id happens to exist in both pools -- silently
    compares an agent against the WRONG candidate and passes. The second
    outcome is the dangerous one, and it is why this is not fixed by simply
    relaxing the check.

    The old monolithic path had no such problem: ``multi_day_blocks`` gives
    each day a DISTINCT ``d{index}_`` prefix and builds ONE pool for the whole
    window, so ``validate_calibrated_provenance`` remains exactly right there
    and is still what that path uses.

    THE RULE APPLIED HERE: validate an invariant where its context holds. Each
    day is fully proven against its own pool at the moment it is calibrated,
    including the candidate cross-reference, and that proof is stored in the
    day library beside the day's artifacts. This function then

      1. verifies every day's stored proof,
      2. re-checks the assembled artifacts for every invariant that survives
         assembly (route/agent pairing, unique ids, endpoints, purposes,
         departures), and
      3. BINDS the two by vehicle count per variant,

    so a vehicle cannot be added, dropped or renumbered between the proven days
    and the published window. That is strictly stronger than the single-pool
    check it replaces for these windows, because it also binds each vehicle to
    the identity of the day that produced it.
    """
    day_records: list[dict] = []
    proven: dict[str, int] = {}
    for directory in day_directories:
        path = Path(directory) / DAY_PROVENANCE_NAME
        if not path.is_file():
            raise ValueError(
                f"day provenance record is missing: {path} -- the day library "
                "entry predates per-day provenance and must be rebuilt")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") != "pass" or record.get("schema_version") != 1:
            raise ValueError(f"day provenance record did not pass: {path}")
        entries = record.get("variants")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"day provenance record has no variants: {path}")
        for entry in entries:
            name = entry.get("route")
            if not isinstance(name, str):
                raise ValueError(f"day provenance variant is malformed: {path}")
            proven[name] = proven.get(name, 0) + int(entry.get("vehicles", 0))
        day_records.append({
            "day": Path(directory).name,
            "candidate_records": record.get("candidate_records"),
            "vehicles": record.get("vehicles"),
        })
    if not day_records:
        raise ValueError("no day provenance records to verify")

    variant_reports = []
    total = 0
    for route_path, agent_path in variants:
        route_path, agent_path = Path(route_path), Path(agent_path)
        vehicles = _validate_variant(route_path, agent_path)
        expected = proven.get(route_path.name)
        if expected is None:
            raise ValueError(
                f"no day provenance covers variant {route_path.name}; "
                f"days proved {sorted(proven)}")
        if vehicles != expected:
            raise ValueError(
                f"assembled {route_path.name} carries {vehicles} vehicles but "
                f"its {len(day_records)} day(s) proved {expected}")
        total += vehicles
        variant_reports.append({
            "route": route_path.name,
            "agents": agent_path.name,
            "vehicles": vehicles,
        })
    return {
        "schema_version": 1,
        "status": "pass",
        "mode": "assembled_day_library",
        "days": day_records,
        "vehicles": total,
        "variants": variant_reports,
    }


def _stage_json(document: dict, destination: Path) -> Path:
    """Write and fsync JSON beside its destination for atomic replace."""
    descriptor, raw_path = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


