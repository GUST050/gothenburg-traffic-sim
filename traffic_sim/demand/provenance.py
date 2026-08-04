"""Cross-artifact validation for calibrated routes and candidate provenance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Iterable, Sequence
import xml.etree.ElementTree as ET


def _candidate_records(path: Path) -> dict[str, dict]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    records = document.get("candidates")
    if not isinstance(records, dict):
        raise ValueError(f"candidate metadata is malformed: {path}")
    return records


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
    candidate_paths: dict[str, tuple[str, ...]] = {}
    for _event, element in ET.iterparse(candidate_routes, events=("end",)):
        if element.tag != "vehicle":
            continue
        candidate_id = element.get("id")
        route = element.find("route")
        edges = tuple((route.get("edges") if route is not None else "").split())
        if not candidate_id or candidate_id in candidate_paths or not edges:
            raise ValueError(
                f"invalid or duplicate candidate route in {candidate_routes}: "
                f"{candidate_id!r}")
        candidate_paths[candidate_id] = edges
        element.clear()
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
        routes: dict[str, tuple[tuple[str, ...], float]] = {}
        for _event, element in ET.iterparse(route_path, events=("end",)):
            if element.tag == "vehicle":
                vehicle_id = element.get("id")
                route = element.find("route")
                edges = tuple((route.get("edges") if route is not None else "").split())
                if not vehicle_id or vehicle_id in routes or not edges:
                    raise ValueError(
                        f"invalid or duplicate calibrated vehicle in {route_path}: "
                        f"{vehicle_id!r}")
                routes[vehicle_id] = (edges, float(element.get("depart")))
                element.clear()

        document = json.loads(agent_path.read_text(encoding="utf-8"))
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
            candidate_id = agent.get("candidate_id")
            candidate = candidates.get(candidate_id)
            if not isinstance(candidate_id, str) or not isinstance(candidate, dict):
                raise ValueError(
                    f"calibrated agent {vehicle_id!r} references unknown candidate "
                    f"{candidate_id!r}")
            edges, departure = routes[vehicle_id]
            if edges != candidate_paths[candidate_id]:
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
            if abs(agent_departure - departure) > 1e-9:
                raise ValueError(
                    f"calibrated agent {vehicle_id!r} departure differs from its route")
        if seen != set(routes):
            missing = sorted(set(routes) - seen)
            raise ValueError(
                f"{len(missing)} calibrated route(s) lack agent provenance in "
                f"{agent_path}: {missing[:3]}")
        total += len(seen)
        variant_reports.append({
            "route": route_path.name,
            "agents": agent_path.name,
            "vehicles": len(seen),
        })
    return {
        "schema_version": 1,
        "status": "pass",
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


def augment_calibrated_edge_support(
    candidate_routes: Path,
    candidate_metadata: Path,
    calibrated_routes: Path,
    calibrated_agents: Path,
    *,
    required_edges: Iterable[str],
    forbidden_edges: Iterable[str] = (),
    days: int = 1,
) -> dict:
    """Add a minimal deterministic support set without changing hard counts.

    PFE's fractional assignment can round weak, unmeasured links to zero. The
    candidate pool still contains exact legal routes for every edge, so choose
    a greedy set cover from routes explicitly labelled ``support_only``. Any
    route touching a measured/forbidden edge is ineligible; therefore this
    augmentation cannot alter the Level-1 sensor counts or GEH result.
    """
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("edge-support augmentation requires positive days")
    candidates = _candidate_records(Path(candidate_metadata))
    support: dict[str, tuple[tuple[str, ...], float, dict]] = {}
    for _event, element in ET.iterparse(candidate_routes, events=("end",)):
        if element.tag != "vehicle":
            continue
        candidate_id = element.get("id")
        record = candidates.get(candidate_id)
        route = element.find("route")
        edges = tuple((route.get("edges") if route is not None else "").split())
        if isinstance(record, dict) and record.get("support_only") is True \
                and candidate_id and edges:
            support[candidate_id] = (
                edges, float(element.get("depart", "0")), record)
        element.clear()

    route_tree = ET.parse(calibrated_routes)
    route_root = route_tree.getroot()
    calibrated_vehicles = list(route_root.findall("vehicle"))
    covered = {
        edge
        for vehicle in calibrated_vehicles
        for edge in (vehicle.find("route").get("edges") or "").split()
    }
    required = {str(edge) for edge in required_edges}
    forbidden = {str(edge) for edge in forbidden_edges}
    missing = required - covered
    eligible = {
        candidate_id: (edges, departure, record)
        for candidate_id, (edges, departure, record) in support.items()
        if not (set(edges) & forbidden)
    }
    selected: list[tuple[str, tuple[str, ...], float, dict]] = []
    while missing:
        choices = [
            (len(set(edges) & missing), -len(edges), candidate_id,
             edges, departure, record)
            for candidate_id, (edges, departure, record) in eligible.items()
        ]
        if not choices:
            raise ValueError(
                f"no support candidate can cover {len(missing)} required edge(s)")
        gain, _negative_length, candidate_id, edges, departure, record = max(choices)
        if gain <= 0:
            raise ValueError(
                f"support candidates leave {len(missing)} required edge(s) uncovered: "
                f"{sorted(missing)[:10]}")
        selected.append((candidate_id, edges, departure, record))
        missing.difference_update(edges)
        del eligible[candidate_id]

    agent_document = json.loads(Path(calibrated_agents).read_text(encoding="utf-8"))
    agents = agent_document.get("agents")
    if not isinstance(agents, list):
        raise ValueError(f"calibrated agent sidecar is malformed: {calibrated_agents}")
    used_ids = {vehicle.get("id") for vehicle in calibrated_vehicles}
    added = []
    for day in range(days):
        for position, (candidate_id, edges, departure, record) in enumerate(selected):
            vehicle_id = f"support{day}_{position}"
            if vehicle_id in used_ids:
                raise ValueError(f"duplicate support vehicle id {vehicle_id!r}")
            used_ids.add(vehicle_id)
            depart = (departure % 86400.0) + day * 86400.0
            vehicle = ET.Element("vehicle", id=vehicle_id, depart=f"{depart:.1f}")
            ET.SubElement(vehicle, "route", edges=" ".join(edges))
            added.append(vehicle)
            agents.append({
                "vehicle_id": vehicle_id,
                "candidate_id": candidate_id,
                "purpose": record.get("purpose"),
                "purpose_route_compatible": True,
                "tour_id": record.get("tour_id"),
                "leg": record.get("leg"),
                "origin_edge": edges[0],
                "destination_edge": edges[-1],
                "departure_s": round(depart, 1),
                "support_only": True,
                "coverage_edge": record.get("coverage_edge"),
                "synthetic_endpoint": record.get("synthetic_endpoint") is True,
                "unavoidable_loop": record.get("unavoidable_loop") is True,
            })
    vehicles = calibrated_vehicles + added
    vehicles.sort(key=lambda vehicle: (
        float(vehicle.get("depart", "0")), vehicle.get("id", "")))
    for vehicle in route_root.findall("vehicle"):
        route_root.remove(vehicle)
    route_root.extend(vehicles)
    final_edges = {
        edge for vehicle in vehicles
        for edge in (vehicle.find("route").get("edges") or "").split()
    }
    unresolved = required - final_edges
    if unresolved:
        raise ValueError(
            f"support augmentation left {len(unresolved)} edge(s) uncovered")
    # Fully serialize both products before publishing either.  There is no
    # portable two-file atomic rename, but staging removes partial/truncated
    # writes; the repository-wide demand-build lock prevents a reader from
    # observing the short replacement window during tracked builds.
    calibrated_routes = Path(calibrated_routes)
    calibrated_agents = Path(calibrated_agents)
    staged_route = _stage_tree(route_tree, calibrated_routes)
    staged_agents = _stage_json(agent_document, calibrated_agents)
    try:
        os.replace(staged_agents, calibrated_agents)
        os.replace(staged_route, calibrated_routes)
    finally:
        staged_route.unlink(missing_ok=True)
        staged_agents.unlink(missing_ok=True)
    newly_supported = sorted(required - covered)
    return {
        "schema_version": 1,
        "status": "pass",
        "core_supported_edges": len(covered & required),
        "required_edges": len(required),
        "newly_supported_edges": len(newly_supported),
        "newly_supported_edge_ids": newly_supported,
        "selected_candidates": len(selected),
        "vehicles_added": len(added),
        "days": days,
        "forbidden_edge_crossings": 0,
    }
