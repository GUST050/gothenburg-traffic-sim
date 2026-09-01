"""Strict sensor-route contract shared by candidate generation and PFE.

A route used to explain a measured sensor must be a globally fastest legal
passenger route for its concrete OD pair.  Removing every measured sensor edge
that the route crosses must leave a finite legal route whose cost is strictly
higher.  The graph and free-flow costs deliberately match the deterministic
closure scorer, so qualification and later disruption pricing cannot disagree.
"""
from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from traffic_sim.simulation.metadata import build_metadata, sha256_file


POLICY_VERSION = "sensor_shortest_positive_gap_v1"
ABS_TOLERANCE_S = 1e-6
REL_TOLERANCE = 1e-9


def route_digest(edges: Sequence[str]) -> str:
    payload = json.dumps(list(edges), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def tolerance_s(*values: float) -> float:
    scale = max((abs(float(value)) for value in values), default=1.0)
    return max(ABS_TOLERANCE_S, REL_TOLERANCE * max(1.0, scale))


def load_network_contract(
    net_path: Path,
) -> tuple[dict[str, tuple[str, ...]], dict[str, float], str]:
    """Return the closure scorer's legal passenger graph and edge costs."""
    path = Path(net_path)
    metadata = build_metadata(path)
    adjacency = {
        str(source): tuple(str(target) for target in targets)
        for source, targets in metadata["successors"].items()
    }
    root = ET.parse(path).getroot()
    costs: dict[str, float] = {}
    for edge in root.iter("edge"):
        edge_id = edge.get("id")
        if (not edge_id or edge.get("function") == "internal"
                or edge_id.startswith(":")):
            continue
        lane = next((item for item in edge if item.tag == "lane"), None)
        if lane is None:
            continue
        try:
            length = float(lane.get("length", ""))
            speed = float(lane.get("speed", ""))
        except (TypeError, ValueError):
            continue
        if (math.isfinite(length) and math.isfinite(speed)
                and length > 0 and speed > 0):
            costs[str(edge_id)] = length / max(speed, 0.1)
    if not costs:
        raise ValueError(f"SUMO network {path} has no closure routing costs")
    filtered = {
        source: tuple(target for target in targets if target in costs)
        for source, targets in adjacency.items() if source in costs
    }
    return filtered, costs, sha256_file(path)


def qualify_route(
    edges: Sequence[str],
    measured_edges: Iterable[str],
    costs: Mapping[str, float],
    shortest_free_cost: float | None,
    shortest_without_sensor: Mapping[str, float | None],
    network_sha256: str,
) -> tuple[dict | None, str | None]:
    """Build a proof from precomputed OD costs, or return a stable reason."""
    route = tuple(str(edge) for edge in edges)
    if not route:
        return None, "missing_route"
    if any(edge not in costs for edge in route):
        return None, "unknown_or_unpriced_edge"
    measured = {str(edge) for edge in measured_edges}
    hits = tuple(sorted(measured.intersection(route)))
    if not hits:
        return None, "no_measured_sensor"
    if shortest_free_cost is None or not math.isfinite(shortest_free_cost):
        return None, "no_legal_free_route"

    # shortest_path_cost charges a successor when it is entered and therefore
    # excludes the already occupied origin edge.  Match that convention here.
    actual = sum(float(costs[edge]) for edge in route[1:])
    equality_tolerance = tolerance_s(actual, shortest_free_cost)
    if abs(actual - shortest_free_cost) > equality_tolerance:
        return None, "not_globally_shortest"

    gaps: dict[str, float] = {}
    for sensor in hits:
        banned_cost = shortest_without_sensor.get(sensor)
        if banned_cost is None or not math.isfinite(banned_cost):
            return None, "no_legal_sensor_detour"
        gap = float(banned_cost) - float(shortest_free_cost)
        if gap <= tolerance_s(banned_cost, shortest_free_cost):
            return None, "no_strict_sensor_penalty"
        gaps[sensor] = gap

    proof = {
        "policy_version": POLICY_VERSION,
        "pass": True,
        "network_sha256": network_sha256,
        "route_sha256": route_digest(route),
        "origin_edge": route[0],
        "destination_edge": route[-1],
        "route_cost_s": round(actual, 12),
        "shortest_free_cost_s": round(float(shortest_free_cost), 12),
        "sensor_penalty_s": {
            edge: round(value, 12) for edge, value in sorted(gaps.items())
        },
        "sensor_edges": list(hits),
        "absolute_tolerance_s": ABS_TOLERANCE_S,
        "relative_tolerance": REL_TOLERANCE,
    }
    return proof, None


def proof_error(
    edges: Sequence[str], proof: object, required_sensor_edges: Iterable[str],
) -> str | None:
    """Validate a persisted proof against the concrete candidate geometry."""
    route = tuple(str(edge) for edge in edges)
    required = {str(edge) for edge in required_sensor_edges}
    hits = sorted(required.intersection(route))
    if not hits:
        return "no_measured_sensor"
    if not isinstance(proof, dict):
        return "missing_proof"
    if proof.get("policy_version") != POLICY_VERSION or proof.get("pass") is not True:
        return "wrong_policy"
    if proof.get("route_sha256") != route_digest(route):
        return "route_digest_mismatch"
    if proof.get("origin_edge") != route[0] or proof.get("destination_edge") != route[-1]:
        return "od_mismatch"
    if proof.get("sensor_edges") != hits:
        return "sensor_set_mismatch"
    penalties = proof.get("sensor_penalty_s")
    if not isinstance(penalties, dict):
        return "missing_penalties"
    try:
        actual = float(proof["route_cost_s"])
        shortest = float(proof["shortest_free_cost_s"])
    except (KeyError, TypeError, ValueError):
        return "invalid_costs"
    if not math.isfinite(actual) or not math.isfinite(shortest):
        return "invalid_costs"
    if abs(actual - shortest) > tolerance_s(actual, shortest):
        return "not_globally_shortest"
    for sensor in hits:
        try:
            gap = float(penalties[sensor])
        except (KeyError, TypeError, ValueError):
            return "missing_penalty"
        if not math.isfinite(gap) or gap <= tolerance_s(shortest, shortest + gap):
            return "no_strict_sensor_penalty"
    network_digest = proof.get("network_sha256")
    if (not isinstance(network_digest, str) or len(network_digest) != 64
            or any(char not in "0123456789abcdef" for char in network_digest)):
        return "invalid_network_digest"
    return None
