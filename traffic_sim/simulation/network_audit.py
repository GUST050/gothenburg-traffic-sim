"""Source-to-SUMO network audit sidecar.

The audit is generated at network-build time and is never used as a runtime
shortcut.  It records which values came from OSM tags versus highway defaults,
plus the TLS membership that netconvert actually produced.  This makes a
network rebuild reviewable without changing stable edge IDs or simulation
behaviour.
"""
from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from build_sumo_net import (_is_oneway, _parse_numeric_tag, _speed_kmh,
                            parse_lanes, parse_speed_ms, scalar)
from traffic_sim.simulation.metadata import sha256_file

SCHEMA_VERSION = 1
_AUDITED_TAGS = (
    "highway", "junction", "turn:lanes", "turn:lanes:forward",
    "turn:lanes:backward", "maxspeed", "lanes", "lanes:forward",
    "lanes:backward", "oneway", "access", "motor_vehicle", "restriction",
    "priority",
)


def _values(data: dict, key: str):
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return value


def _has_numeric_lane_tag(data: dict) -> bool:
    return any(_parse_numeric_tag(data.get(key)) is not None
               for key in ("lanes", "lanes:forward", "lanes:backward"))


def _tls_membership(net_path: Path) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    root = ET.parse(net_path).getroot()
    for connection in root.findall("connection"):
        edge = connection.get("from")
        tls = connection.get("tl")
        if edge and tls:
            result.setdefault(edge, set()).add(tls)
    return {edge: sorted(ids) for edge, ids in result.items()}


def _sumo_edge_values(net_path: Path) -> dict[str, dict]:
    values: dict[str, dict] = {}
    root = ET.parse(net_path).getroot()
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if not edge_id or edge.get("function") == "internal" or edge_id.startswith(":"):
            continue
        lane = edge.find("lane")
        speed = edge.get("speed") or (lane.get("speed") if lane is not None else None)
        lanes = edge.get("numLanes") or (len(edge.findall("lane")) if edge is not None else None)
        length = lane.get("length") if lane is not None else None
        try:
            speed_value = float(speed) if speed is not None else None
        except (TypeError, ValueError):
            speed_value = None
        try:
            lane_value = int(lanes) if lanes is not None else None
        except (TypeError, ValueError):
            lane_value = None
        try:
            length_value = float(length) if length is not None else None
        except (TypeError, ValueError):
            length_value = None
        values[edge_id] = {"speed_m_s": speed_value, "lanes": lane_value,
                           "length_m": length_value}
    return values


def build_audit(graph, net_path: Path) -> dict:
    """Return a deterministic audit for the source graph and SUMO net."""
    tls_by_edge = _tls_membership(Path(net_path))
    sumo_values = _sumo_edge_values(Path(net_path))
    edges: dict[str, dict] = {}
    for u, v, key, data in graph.edges(keys=True, data=True):
        if u == v:
            continue
        edge_id = f"{u}_{v}_{key}"
        highway = scalar(data.get("highway"))
        speed_imported = _speed_kmh(data.get("maxspeed")) is not None
        lane_imported = _has_numeric_lane_tag(data)
        junction = str(scalar(data.get("junction", "")) or "").lower()
        source_tags = {tag: _values(data, tag) for tag in _AUDITED_TAGS
                       if data.get(tag) is not None}
        try:
            graph_length = float(scalar(data.get("length")))
        except (TypeError, ValueError):
            graph_length = None
        sumo_length = sumo_values.get(edge_id, {}).get("length_m")
        # Driven length must match the map: netconvert's junction cutting once
        # shrank an 88 m street to a 0.20 m lane (found 2026-07-17), making
        # meso traverse it instantly.  Flag any disagreement beyond rounding
        # so a rebuild that reintroduces the distortion is visible in review.
        length_ok = None
        if graph_length is not None and sumo_length is not None:
            length_ok = abs(sumo_length - graph_length) <= max(
                2.0, 0.05 * graph_length)
        edges[edge_id] = {
            "highway": highway,
            "oneway": _is_oneway(data.get("oneway")),
            "roundabout": junction == "roundabout",
            "graph_length_m": graph_length,
            "sumo_length_m": sumo_length,
            "length_ok": length_ok,
            "speed_m_s": parse_speed_ms(data),
            "sumo_speed_m_s": sumo_values.get(edge_id, {}).get("speed_m_s"),
            "speed_source": "imported" if speed_imported else "defaulted",
            "lanes": parse_lanes(data),
            "sumo_lanes": sumo_values.get(edge_id, {}).get("lanes"),
            "lanes_source": "imported" if lane_imported else "defaulted",
            "source_tags": source_tags,
            "tls_ids": tls_by_edge.get(edge_id, []),
            "tls_source": "netconvert" if edge_id in tls_by_edge else "none",
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "net_sha256": sha256_file(Path(net_path)),
        "edges": edges,
    }


def write_audit(graph, net_path: Path, output_path: Path) -> dict:
    payload = build_audit(graph, Path(net_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=output_path.parent,
                                     prefix=f".{output_path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, output_path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return payload
