#!/usr/bin/env python3
"""Analyze immutable paired LOSO picker diagnostic replays."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.demand import pfe
from traffic_sim.confidence.controlled_rounding import (
    ControlledRoundingError,
    controlled_round_preserving_measured,
    legacy_rounding_trace,
)

DEFAULT_RUN = ROOT / "runs/loso-picker-diagnostic-v1-20260808"
DEFAULT_OUTPUT = ROOT / "validation/loso_picker_decomposition_v1.json"
RETAINED_BOUND_RUNGS = {
    "clean",
    "measurement_tolerance_2x",
    "measurement_tolerance_4x",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def selected_edge_series(route_path: Path, edges: list[str], n_quarters: int) -> dict:
    """Planned departures whose immutable route crosses each evaluation edge."""
    per_edge = {edge: [0] * n_quarters for edge in edges}
    for vehicle in ET.parse(route_path).getroot().iter("vehicle"):
        quarter = int(float(vehicle.get("depart", "0")) // 900)
        if quarter < 0 or quarter >= n_quarters:
            continue
        route = vehicle.find("route")
        route_edges = set((route.get("edges") if route is not None else "").split())
        for edge in edges:
            if edge in route_edges:
                per_edge[edge][quarter] += 1
    station = [sum(per_edge[edge][quarter] for edge in edges)
               for quarter in range(n_quarters)]
    return {"by_edge": per_edge, "station_evaluation": station}


def simulated_edge_series(edge_data_path: Path, edges: list[str],
                          n_quarters: int) -> dict:
    per_edge = {edge: [0.0] * n_quarters for edge in edges}
    for interval in ET.parse(edge_data_path).getroot().findall("interval"):
        quarter = int(float(interval.get("begin", "0")) // 900)
        if quarter < 0 or quarter >= n_quarters:
            continue
        for edge in interval.findall("edge"):
            edge_id = edge.get("id")
            if edge_id in per_edge:
                per_edge[edge_id][quarter] = float(edge.get("entered") or 0.0)
    station = [sum(per_edge[edge][quarter] for edge in edges)
               for quarter in range(n_quarters)]
    return {"by_edge": per_edge, "station_evaluation": station}


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def mapping_delta(first: Mapping[str, float], second: Mapping[str, float]) -> list[dict]:
    rows = [{"key": key,
             "first": round(float(first.get(key, 0.0)), 6),
             "second": round(float(second.get(key, 0.0)), 6),
             "delta_second_minus_first": round(
                 float(second.get(key, 0.0)) - float(first.get(key, 0.0)), 6)}
            for key in sorted(set(first) | set(second))]
    return sorted(rows, key=lambda row: (-abs(row["delta_second_minus_first"]),
                                         row["key"]))


def verify_archived_artifacts(run_root: Path, summary: dict) -> None:
    for relative, expected in summary["archived_artifacts_sha256"].items():
        path = run_root / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"archived artifact hash mismatch: {path}")


def read_leading_json_value(path: Path, key: str, limit: int = 4 * 1024 * 1024):
    """Read a small top-level value that precedes the large quarters array."""
    with path.open(encoding="utf-8") as handle:
        prefix = handle.read(limit)
    marker = json.dumps(key) + ":"
    start = prefix.find(marker)
    if start < 0:
        raise ValueError(f"{key!r} not found near start of {path}")
    start += len(marker)
    while start < len(prefix) and prefix[start].isspace():
        start += 1
    return json.JSONDecoder().raw_decode(prefix, start)[0]


def iter_json_array(path: Path, key: str, chunk_size: int = 1024 * 1024):
    """Stream top-level array items with bounded memory using stdlib JSON."""
    marker = json.dumps(key) + ": ["
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8") as handle:
        buffer = ""
        while marker not in buffer:
            chunk = handle.read(chunk_size)
            if not chunk:
                raise ValueError(f"array {key!r} not found in {path}")
            buffer += chunk
            if len(buffer) > len(marker) + chunk_size:
                buffer = buffer[-(len(marker) + chunk_size):]
        buffer = buffer.split(marker, 1)[1]
        eof = False
        while True:
            buffer = buffer.lstrip()
            while not buffer:
                chunk = handle.read(chunk_size)
                if not chunk:
                    raise ValueError(f"truncated array {key!r} in {path}")
                buffer = chunk.lstrip()
            if buffer.startswith("]"):
                return
            if buffer.startswith(","):
                buffer = buffer[1:].lstrip()
                while not buffer:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        raise ValueError(f"truncated array {key!r} in {path}")
                    buffer = chunk.lstrip()
                if buffer.startswith("]"):
                    return
            while True:
                try:
                    item, end = decoder.raw_decode(buffer)
                    break
                except json.JSONDecodeError:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        eof = True
                        break
                    buffer += chunk
            if eof:
                raise ValueError(f"truncated array {key!r} in {path}")
            yield item
            buffer = buffer[end:]


def resolve_candidate_pool(expected_sha256: str) -> tuple[Path, str]:
    """Resolve the exact immutable pool used by a replay seed."""
    live = ROOT / "sumo/candidates.rou.xml"
    if live.is_file() and sha256_file(live) == expected_sha256:
        return live, "live_pool"
    cache_root = ROOT / "sumo/candidate_cache"
    if cache_root.is_dir():
        for entry in sorted(cache_root.iterdir()):
            manifest = entry / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                outputs = json.loads(manifest.read_text()).get("outputs") or {}
            except (OSError, json.JSONDecodeError):
                continue
            recorded = (outputs.get("candidates.rou.xml") or {}).get("sha256")
            candidate = entry / "candidates.rou.xml"
            if recorded == expected_sha256 and candidate.is_file():
                if sha256_file(candidate) != expected_sha256:
                    raise ValueError(f"candidate cache hash mismatch: {candidate}")
                return candidate, f"candidate_cache/{entry.name}"
    raise FileNotFoundError(
        f"exact candidate pool {expected_sha256[:12]} is unavailable")


def shape_purpose(shape) -> str:
    purposes = {str(source.intent.get("purpose", "unknown"))
                for source in (shape.source_candidates or [shape])}
    if len(purposes) != 1:
        raise ValueError("diagnostic replay expected purpose-stratified shapes")
    return next(iter(purposes))


def published_shape_counts(route_path: Path, agents_path: Path, shapes: list,
                           n_quarters: int) -> list[Counter]:
    """Map the exact published route×purpose instances back to PFE shapes."""
    document = json.loads(agents_path.read_text())
    agents = document.get("agents") if isinstance(document, dict) else None
    if not isinstance(agents, list):
        raise ValueError(f"invalid agent ledger: {agents_path}")
    by_vehicle = {str(agent["vehicle_id"]): agent for agent in agents}
    if len(by_vehicle) != len(agents):
        raise ValueError(f"duplicate vehicle id in agent ledger: {agents_path}")
    shape_index = {
        (tuple(shape.edges), shape_purpose(shape)): index
        for index, shape in enumerate(shapes)
    }
    if len(shape_index) != len(shapes):
        raise ValueError("prepared shape pool is not unique by geometry×purpose")
    counts = [Counter() for _ in range(n_quarters)]
    route_ids = set()
    for vehicle in ET.parse(route_path).getroot().iter("vehicle"):
        vehicle_id = str(vehicle.get("id"))
        route_ids.add(vehicle_id)
        agent = by_vehicle.get(vehicle_id)
        if agent is None:
            raise ValueError(f"route vehicle missing from agent ledger: {vehicle_id}")
        route = vehicle.find("route")
        edges = tuple((route.get("edges") if route is not None else "").split())
        key = (edges, str(agent.get("purpose", "unknown")))
        if key not in shape_index:
            raise ValueError(f"published route×purpose absent from pool: {vehicle_id}")
        quarter = int(float(vehicle.get("depart", "0")) // 900)
        if 0 <= quarter < n_quarters:
            counts[quarter][shape_index[key]] += 1
    if route_ids != set(by_vehicle):
        raise ValueError("agent ledger and route XML vehicle sets differ")
    return counts


def stage_decomposition(shapes: list, vectors: list[np.ndarray],
                        held_edges: list[str],
                        movement_pairs: set[tuple[str, str]]) -> dict:
    """Decompose exact held-station evaluation entries for one publication stage."""
    held = set(held_edges)
    by_purpose: dict[str, float] = defaultdict(float)
    by_movement: dict[str, float] = defaultdict(float)
    by_od: dict[str, float] = defaultdict(float)
    total_entries = 0.0
    crossing_vehicles = 0.0
    for vector in vectors:
        for index in np.flatnonzero(vector > 0):
            flow = float(vector[index])
            shape = shapes[int(index)]
            entries = sum(edge in held for edge in set(shape.edges))
            if not entries:
                continue
            total_entries += flow * entries
            crossing_vehicles += flow
            purpose = shape_purpose(shape)
            by_purpose[purpose] += flow * entries
            by_od[f"{shape.edges[0]}->{shape.edges[-1]}"] += flow * entries
            for left, right in zip(shape.edges, shape.edges[1:]):
                if (left, right) in movement_pairs:
                    by_movement[f"{left}->{right}"] += flow
    return {
        "held_evaluation_edge_entries": round(total_entries, 6),
        "vehicles_crossing_held_location": round(crossing_vehicles, 6),
        "held_entry_flow_by_purpose": {
            key: round(value, 6) for key, value in sorted(by_purpose.items())},
        "held_entry_flow_by_od": {
            key: round(value, 6) for key, value in sorted(by_od.items())},
        "held_crossing_flow_by_legal_movement": {
            key: round(value, 6) for key, value in sorted(by_movement.items())},
    }


def binding_summaries(quarters) -> tuple[dict, dict, dict, dict]:
    """Reclassify binding with utilisation, excluding zero-flow tiny caps."""
    groups: Counter = Counter()
    ceilings: Counter = Counter()
    retained_max: dict[str, float] = defaultdict(float)
    declared_max: dict[str, float] = defaultdict(float)
    for quarter in quarters:
        if "solution" in quarter and quarter["solution"] is None:
            continue
        for row in quarter.get("structure_groups", []):
            utilisation = row.get("utilisation")
            if (utilisation is not None
                    and float(row.get("achieved_flow", 0.0)) > 1e-9
                    and float(utilisation) >= 0.99):
                groups[str(row["name"])] += 1
        for row in quarter.get("assignment_ceiling_utilisation", []):
            utilisation = row.get("utilisation")
            if utilisation is None:
                continue
            edge = str(row["edge"])
            declared_max[edge] = max(declared_max[edge], float(utilisation))
            if quarter.get("relaxation_rung") not in RETAINED_BOUND_RUNGS:
                continue
            retained_max[edge] = max(retained_max[edge], float(utilisation))
            if float(row.get("achieved_flow", 0.0)) > 1e-9 and float(utilisation) >= 0.99:
                ceilings[edge] += 1
    return (dict(sorted(groups.items())), dict(sorted(ceilings.items())),
            {key: round(value, 6) for key, value in sorted(retained_max.items())},
            {key: round(value, 6) for key, value in sorted(declared_max.items())})


def strict_binding_summaries(diagnostic: dict) -> tuple[dict, dict, dict]:
    groups, ceilings, retained, _declared = binding_summaries(
        diagnostic["quarters"])
    return groups, ceilings, retained


def load_station(run_root: Path, summary: dict, station: str,
                 shapes: list, *, published_root: Path | None = None,
                 published_summary: dict | None = None) -> dict:
    artifact_root = published_root or run_root
    diagnostic_path = run_root / "picker" / f"picker_{station}.json"
    n_quarters = int(read_leading_json_value(diagnostic_path, "n_quarters"))
    held_edges = list(read_leading_json_value(
        diagnostic_path, "held_component_edges"))
    planned = selected_edge_series(
        artifact_root / "folds" / f"loso_{station}.rou.xml",
        held_edges, n_quarters)
    simulated = simulated_edge_series(
        artifact_root / "folds" / f"loso_ed_{station}.xml",
        held_edges, n_quarters)
    planned_series = planned["station_evaluation"]
    simulated_series = simulated["station_evaluation"]

    selected_hashes = set()
    positive_routes = []
    continuous_vectors = [np.zeros(len(shapes), dtype=float)
                          for _ in range(n_quarters)]
    global_vectors = [np.zeros(len(shapes), dtype=float)
                      for _ in range(n_quarters)]
    direct_vectors = [np.zeros(len(shapes), dtype=float)
                      for _ in range(n_quarters)]
    # Station 107 has mutually inconsistent rounded active margins in at least
    # one quarter.  Its defect is reported from the legacy residual trace; a
    # held-flow counterfactual would require a separately justified sensor-
    # error policy and is intentionally not invented here.
    controlled_enabled = station in {"134", "2276"}
    controlled_vectors = (
        [np.zeros(len(shapes), dtype=float) for _ in range(n_quarters)]
        if controlled_enabled else None)
    movement_pairs: set[tuple[str, str]] = set()
    rung_counts: Counter = Counter()
    aggregate_held_od: dict[str, float] = defaultdict(float)
    aggregate_held_purpose: dict[str, float] = defaultdict(float)
    aggregate_held_movement: dict[str, float] = defaultdict(float)
    legacy_continuous_total = 0.0
    legacy_correction_by_edge: dict[str, Counter] = defaultdict(Counter)
    controlled_method_counts: Counter = Counter()
    controlled_mip_nodes: list[int] = []
    direct_max_measurement_residual = 0
    controlled_max_measurement_residual = 0
    streamed_quarters = []
    for quarter in iter_json_array(diagnostic_path, "quarters"):
        rung_counts[str(quarter["relaxation_rung"])] += 1
        contribution = quarter.get("held_location_flow_contribution") or {}
        for key, value in contribution.get("by_od", {}).items():
            aggregate_held_od[key] += float(value)
        for key, value in contribution.get("by_purpose", {}).items():
            aggregate_held_purpose[key] += float(value)
        for key, value in contribution.get("by_movement", {}).items():
            aggregate_held_movement[key] += float(value)
        legacy_continuous_total += float(
            quarter.get("held_location_selected_flow", 0.0))
        # Keep only the small rows needed for binding classification. The
        # selected-route list is the reason each raw file is ~240 MiB.
        streamed_quarters.append({
            "solution": quarter.get("solution", "present"),
            "relaxation_rung": quarter.get("relaxation_rung"),
            "structure_groups": quarter.get("structure_groups", []),
            "assignment_ceiling_utilisation": quarter.get(
                "assignment_ceiling_utilisation", []),
        })
        if "solution" in quarter and quarter["solution"] is None:
            continue
        positive_routes.append(int(quarter["positive_routes"]))
        quarter_index = int(quarter["quarter"])
        vector = np.zeros(len(shapes), dtype=float)
        for route in quarter["selected_routes"]:
            vector[int(route["route_index"])] = float(route["selected_flow"])
            for movement in route.get("junction_movements", []):
                left, right = str(movement).split("->", 1)
                movement_pairs.add((left, right))
        selected_hashes.update(
            route["route_geometry_sha256"]
            for route in quarter["selected_routes"]
            if route["touches_held_location"])
        targets = {row["edge"]: float(row["target"])
                   for row in quarter.get("targets", [])}
        continuous_vectors[quarter_index] = vector
        global_vectors[quarter_index] = pfe.largest_remainder_round(
            vector).astype(float)
        direct, legacy_trace = legacy_rounding_trace(
            vector, shapes, targets, held_edges)
        direct_vectors[quarter_index] = direct.astype(float)
        for edge, values in legacy_trace["correction_by_measured_edge"].items():
            row = legacy_correction_by_edge[edge]
            if values.get("routes_added") or values.get("routes_removed"):
                row["quarters_touched"] += 1
            for key in (
                "routes_added",
                "routes_removed",
                "held_entries_added",
                "held_entries_removed",
            ):
                row[key] += int(values.get(key, 0))
        direct_max_measurement_residual = max(
            direct_max_measurement_residual,
            *(abs(int(value))
              for value in legacy_trace["measurement_residuals"].values()),
        )
        if not controlled_enabled:
            continue
        try:
            controlled, controlled_report = controlled_round_preserving_measured(
                vector, shapes, targets)
        except ControlledRoundingError as exc:
            raise ControlledRoundingError(
                f"station={station} quarter={quarter_index}: {exc}") from exc
        controlled_vectors[quarter_index] = controlled.astype(float)
        controlled_method_counts[controlled_report["method"]] += 1
        controlled_mip_nodes.append(int(controlled_report["mip_nodes"]))
        controlled_max_measurement_residual = max(
            controlled_max_measurement_residual,
            *(abs(int(value))
              for value in controlled_report["measurement_residuals"].values()),
        )

    published_counts = published_shape_counts(
        artifact_root / "folds" / f"loso_{station}.rou.xml",
        artifact_root / "folds" / f"loso_{station}.agents.json",
        shapes, n_quarters)
    published_vectors = []
    for counts in published_counts:
        vector = np.zeros(len(shapes), dtype=float)
        for index, value in counts.items():
            vector[index] = value
        published_vectors.append(vector)
    continuous_stage = stage_decomposition(
        shapes, continuous_vectors, held_edges, movement_pairs)
    global_stage = stage_decomposition(
        shapes, global_vectors, held_edges, movement_pairs)
    direct_stage = stage_decomposition(
        shapes, direct_vectors, held_edges, movement_pairs)
    controlled_stage = (
        stage_decomposition(shapes, controlled_vectors, held_edges, movement_pairs)
        if controlled_vectors is not None else None)
    published_stage = stage_decomposition(
        shapes, published_vectors, held_edges, movement_pairs)
    (group_bindings, ceiling_bindings, retained_ceiling_max,
     declared_ceiling_max) = binding_summaries(streamed_quarters)
    direct_published_l1 = round(sum(
        float(np.abs(direct - published).sum())
        for direct, published in zip(direct_vectors, published_vectors)), 6)
    direct_continuous_l1 = round(sum(
        float(np.abs(direct - continuous).sum())
        for direct, continuous in zip(direct_vectors, continuous_vectors)), 6)
    controlled_continuous_l1 = (
        round(sum(
            float(np.abs(controlled - continuous).sum())
            for controlled, continuous
            in zip(controlled_vectors, continuous_vectors)), 6)
        if controlled_vectors is not None else None)
    correction_summary = {}
    for edge, values in sorted(legacy_correction_by_edge.items()):
        row = dict(sorted(values.items()))
        row["net_held_entries_added"] = (
            int(values["held_entries_added"])
            - int(values["held_entries_removed"])
        )
        correction_summary[edge] = row

    station_summary = (published_summary or summary)["stations"][station]
    continuous_total = round(legacy_continuous_total, 6)
    planned_total = float(sum(planned_series))
    if published_stage["held_evaluation_edge_entries"] != planned_total:
        raise ValueError(
            f"published decomposition differs from route edge series for {station}")
    simulated_total = round(float(sum(simulated_series)), 6)
    l1 = float(sum(abs(planned_value - simulated_value)
                   for planned_value, simulated_value
                   in zip(planned_series, simulated_series)))
    return {
        "ratio": station_summary["ratio"],
        "geh_ok_pct": station_summary["geh_ok_pct"],
        "measured_total": station_summary["measured_total"],
        "continuous_pre_integer_held_flow_legacy_route_union": continuous_total,
        "publication_stages": {
            "continuous_pre_integer": continuous_stage,
            "global_largest_remainder_round": global_stage,
            "direct_measurement_preserving_round": direct_stage,
            "controlled_joint_measurement_round": controlled_stage,
            "published_after_structure_and_purpose_repair": published_stage,
            "held_entries_delta_global_round_minus_continuous": round(
                global_stage["held_evaluation_edge_entries"]
                - continuous_stage["held_evaluation_edge_entries"], 6),
            "held_entries_delta_direct_round_minus_continuous": round(
                direct_stage["held_evaluation_edge_entries"]
                - continuous_stage["held_evaluation_edge_entries"], 6),
            "held_entries_delta_controlled_round_minus_continuous": round(
                controlled_stage["held_evaluation_edge_entries"]
                - continuous_stage["held_evaluation_edge_entries"], 6)
            if controlled_stage is not None else None,
            "held_entries_delta_post_round_repair": round(
                published_stage["held_evaluation_edge_entries"]
                - direct_stage["held_evaluation_edge_entries"], 6),
            "held_entries_delta_post_controlled_round_repair": round(
                published_stage["held_evaluation_edge_entries"]
                - controlled_stage["held_evaluation_edge_entries"], 6)
            if controlled_stage is not None else None,
            "route_count_l1_direct_round_vs_published": direct_published_l1,
            "route_count_l1_direct_round_vs_continuous": direct_continuous_l1,
            "route_count_l1_controlled_round_vs_continuous": (
                controlled_continuous_l1),
            "direct_round_max_abs_measurement_residual": (
                direct_max_measurement_residual),
            "controlled_round_max_abs_measurement_residual": (
                controlled_max_measurement_residual
                if controlled_enabled else None),
            "controlled_round_method_counts": dict(
                sorted(controlled_method_counts.items())),
            "controlled_round_mip_nodes_max": max(controlled_mip_nodes, default=0),
            "controlled_round_scope_note": (
                "not_run: active integer margins are mutually inconsistent; "
                "a sensor-residual policy requires separate validation"
                if not controlled_enabled else "active margins projected jointly"),
            "legacy_correction_by_measured_edge": correction_summary,
        },
        "planned_departure_edge_entries": planned_total,
        "simulated_edge_entries": simulated_total,
        "planned_minus_simulated_total": round(planned_total - simulated_total, 6),
        "quarter_l1_planned_vs_simulated": round(l1, 6),
        "quarter_l1_share_of_planned": round(l1 / planned_total, 6)
        if planned_total else None,
        "positive_routes_per_quarter_median": statistics.median(positive_routes)
        if positive_routes else 0,
        "selected_held_route_geometries": len(selected_hashes),
        "selected_held_route_geometry_hashes": sorted(selected_hashes),
        "assignment_ceiling_binding_quarters_retained_rungs": ceiling_bindings,
        "assignment_ceiling_max_utilisation_retained_rungs": retained_ceiling_max,
        "assignment_ceiling_max_utilisation_all_declared_rungs": declared_ceiling_max,
        "structure_group_near_cap_quarters": group_bindings,
        "relaxation_rung_counts": dict(sorted(rung_counts.items())),
        "held_flow_by_od": {key: round(value, 6)
                            for key, value in sorted(aggregate_held_od.items())},
        "held_flow_by_purpose": {
            key: round(value, 6)
            for key, value in sorted(aggregate_held_purpose.items())},
        "held_flow_by_movement": {
            key: round(value, 6)
            for key, value in sorted(aggregate_held_movement.items())},
        "planned_by_quarter": planned_series,
        "simulated_by_quarter": simulated_series,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--published-run-root", type=Path,
        help="optional paired treatment root whose exact published routes and "
             "SUMO outputs replace the diagnostic run's publication stage")
    args = parser.parse_args()
    run_root = args.run_root if args.run_root.is_absolute() else ROOT / args.run_root
    manifest_path = run_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "complete":
        raise SystemExit("picker diagnostic manifest is not complete")
    if len(manifest["runs"]) < 2:
        raise SystemExit("paired picker analysis requires at least two runs")
    published_run_root = None
    published_manifest = None
    published_runs = {}
    analysis_stations = list(manifest["stations"])
    if args.published_run_root is not None:
        published_run_root = (
            args.published_run_root if args.published_run_root.is_absolute()
            else ROOT / args.published_run_root)
        published_manifest_path = published_run_root / "manifest.json"
        published_manifest = json.loads(published_manifest_path.read_text())
        if published_manifest.get("status") != "complete":
            raise SystemExit("alternate published-run manifest is not complete")
        if set(published_manifest.get("seeds", [])) != set(manifest["seeds"]):
            raise SystemExit("alternate published-run seeds differ")
        analysis_stations = [
            station for station in analysis_stations
            if station in set(published_manifest.get("stations", []))]
        if not analysis_stations:
            raise SystemExit(
                "diagnostic and alternate published runs share no stations")
        published_runs = {
            str(run["seed"]): run for run in published_manifest["runs"]}
    recorded_pfe = (manifest.get("inputs_sha256") or {}).get(
        "traffic_sim/demand/pfe.py")
    if recorded_pfe != sha256_file(ROOT / "traffic_sim/demand/pfe.py"):
        raise SystemExit(
            "current PFE implementation differs from the diagnostic replay; "
            "route-index reconstruction is unsafe")

    per_seed = {}
    geometry_sets: dict[str, dict[str, set[str]]] = defaultdict(dict)
    for run in manifest["runs"]:
        seed = str(run["seed"])
        seed_root = run_root / f"seed-{seed}"
        summary = json.loads((seed_root / "summary.json").read_text())
        verify_archived_artifacts(seed_root, summary)
        treatment_seed_root = None
        treatment_summary = None
        if published_run_root is not None:
            treatment_seed_root = published_run_root / f"seed-{seed}"
            treatment_summary = published_runs.get(seed)
            if treatment_summary is None:
                raise SystemExit(f"alternate published run lacks seed {seed}")
            verify_archived_artifacts(treatment_seed_root, treatment_summary)
            if (treatment_summary["candidate_pool_sha256"]
                    != summary["candidate_pool_sha256"]):
                raise SystemExit(
                    f"alternate published pool differs for seed {seed}")
            if treatment_summary["demand_build_id"] != summary["demand_build_id"]:
                raise SystemExit(
                    f"alternate published demand build differs for seed {seed}")
        candidate_path, pool_provenance = resolve_candidate_pool(
            summary["candidate_pool_sha256"])
        shapes, _route_cost = pfe.prepare_calibration(candidate_path)
        per_seed[seed] = {
            "candidate_pool_sha256": summary["candidate_pool_sha256"],
            "candidate_pool_provenance": pool_provenance,
            "demand_build_id": summary["demand_build_id"],
            "stations": {},
        }
        for station in analysis_stations:
            record = load_station(
                seed_root, summary, station, shapes,
                published_root=treatment_seed_root,
                published_summary=treatment_summary)
            geometry_sets[station][seed] = set(
                record.pop("selected_held_route_geometry_hashes"))
            per_seed[seed]["stations"][station] = record

    seeds = [str(seed) for seed in manifest["seeds"]]
    first, second = seeds[0], seeds[1]
    comparisons = {}
    for station in analysis_stations:
        left = per_seed[first]["stations"][station]
        right = per_seed[second]["stations"][station]
        left_stages = left["publication_stages"]
        right_stages = right["publication_stages"]
        comparisons[station] = {
            "first_seed": int(first),
            "second_seed": int(second),
            "ratio_delta_second_minus_first": round(
                float(right["ratio"]) - float(left["ratio"]), 6),
            "simulated_total_delta_second_minus_first": round(
                float(right["simulated_edge_entries"])
                - float(left["simulated_edge_entries"]), 6),
            "selected_route_geometry_jaccard": round(jaccard(
                geometry_sets[station][first], geometry_sets[station][second]), 6),
            "publication_stage_held_entry_deltas_second_minus_first": {
                stage: (
                    round(
                        right_stages[stage]["held_evaluation_edge_entries"]
                        - left_stages[stage]["held_evaluation_edge_entries"], 6)
                    if (right_stages[stage] is not None
                        and left_stages[stage] is not None)
                    else None)
                for stage in (
                    "continuous_pre_integer",
                    "global_largest_remainder_round",
                    "direct_measurement_preserving_round",
                    "controlled_joint_measurement_round",
                    "published_after_structure_and_purpose_repair",
                )
            },
            "largest_od_flow_deltas": mapping_delta(
                left["held_flow_by_od"], right["held_flow_by_od"])[:20],
            "purpose_flow_deltas": mapping_delta(
                left["held_flow_by_purpose"], right["held_flow_by_purpose"]),
            "movement_flow_deltas": mapping_delta(
                left["held_flow_by_movement"], right["held_flow_by_movement"]),
        }

    report = {
        "schema_version": 3,
        "kind": (
            "paired_loso_controlled_rounding_postpublication_decomposition"
            if published_run_root is not None
            else "paired_loso_picker_decomposition"),
        "evidence_class": "diagnostic_replay",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "manifest": str(manifest_path.relative_to(ROOT)),
            "manifest_sha256": sha256_file(manifest_path),
            "analysis_tool_sha256": sha256_file(Path(__file__)),
            "controlled_rounding_sha256": sha256_file(
                ROOT / "traffic_sim/confidence/controlled_rounding.py"),
            "published_manifest": (
                str((published_run_root / "manifest.json").relative_to(ROOT))
                if published_run_root is not None else None),
            "published_manifest_sha256": (
                sha256_file(published_run_root / "manifest.json")
                if published_run_root is not None else None),
        },
        "field_semantics": {
            "measured_total": "measurement",
            "ratio": "diagnostic",
            "selected_flow": "diagnostic",
            "assignment_ceiling": "model_assumption",
            "structure_group": "model_assumption",
            "movement_flow": "diagnostic_not_observed_turn_share",
            "continuous_pre_integer": "diagnostic_reconstructed_from_6dp_trace",
            "direct_measurement_preserving_round": "diagnostic_recomputed",
            "controlled_joint_measurement_round": (
                "diagnostic_counterfactual_not_production"),
            "published_after_structure_and_purpose_repair": "exact_route_artifact",
        },
        "seeds": [int(seed) for seed in seeds],
        "stations": analysis_stations,
        "per_seed": per_seed,
        "paired_comparison": comparisons,
        "interpretation_rules": [
            "Candidate or selected movement flow is not an observed turn share.",
            "Quarter L1 planned-vs-simulated is an aggregate displacement proxy, "
            "not vehicle-level attribution.",
            "A structure cap is classified near-cap only at >=99% utilisation "
            "with positive achieved flow; the raw one-vehicle tolerance is not used.",
            "Assignment ceiling binding is classified only on solver rungs that "
            "retained structural bounds.",
            "This replay selects explanations; it is not treatment or release evidence.",
        ],
    }
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8")
    temporary.replace(output)
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
