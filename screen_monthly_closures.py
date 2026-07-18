#!/usr/bin/env python3
"""Build an internal, proxy-only SUMO shortlist for a monthly closure search.

This command does not run SUMO and does not publish anything to the web UI.
By default it creates an isolated ``runs/closure-search/<search_id>``
workspace and stores one immutable ``monthly_proxy.json`` artifact there.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from traffic_sim.core.closure_calendar import (
    generate_closure_schedules,
    validate_connected_worksite,
)
from traffic_sim.core.contracts import load_closure_search_spec
from traffic_sim.core.fingerprint import fingerprint_files
from traffic_sim.simulation.monthly_proxy import (
    PROXY_VERSION,
    ShortlistPolicy,
    plausible_detour_paths,
    rank_schedules,
    schedule_quarter_indices,
    score_schedule,
    stratified_shortlist,
)
from traffic_sim.simulation.proxy_projection import project_forecast_flows
from traffic_sim.simulation.search_workspace import create_search_workspace


FORECAST_PATH = Path("web/data/flows_forecast.json")
REFERENCE_PATH = Path("web/data/flows.json")
GEO_PATH = Path("web/data/network.geojson")
SENSORS_PATH = Path("data_in/sensors.json")
DIRECTION_PATH = Path("sumo/direction_split.json")
ASSIGNMENT_PATH = Path("sumo/assignment_priors.json")
PRIORS_PATH = Path("sumo/prior_flows.json")
OBSERVABILITY_PATH = Path("web/data/observability.json")
NETWORK_METADATA_PATH = Path("sumo/network_metadata.json")
STRUCTURAL_REFERENCE_DATE = "2025-09-16T00:00:00"


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"required proxy input is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"required proxy input must be a JSON object: {path}")
    return payload


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sensor_contracts(
    geo: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> tuple[dict[str, tuple[str, ...]], dict[str, str], dict[str, float | None]]:
    sensor_edges: dict[str, list[str]] = {}
    confidence: dict[str, float | None] = {}
    for feature in geo.get("features", ()):
        if feature.get("geometry", {}).get("type") != "LineString":
            continue
        properties = feature.get("properties", {})
        edge = str(properties.get("id", ""))
        if not edge:
            continue
        raw_confidence = properties.get("confidence")
        confidence[edge] = (
            float(raw_confidence) if raw_confidence is not None else None
        )
        sensor_id = properties.get("sensor_id")
        if sensor_id is not None:
            sensor_edges.setdefault(str(sensor_id), []).append(edge)
    semantics = {
        str(item["sensor_id"]): str(item["measurement_semantics"])
        for item in registry.get("sensors", ())
    }
    missing = sorted(set(sensor_edges) - set(semantics))
    if missing:
        raise ValueError(
            "sensor registry is missing measured sensors: " + ", ".join(missing)
        )
    return (
        {sensor: tuple(sorted(edges))
         for sensor, edges in sorted(sensor_edges.items())},
        semantics,
        confidence,
    )


def _direction_shares(payload: Mapping[str, Any]) -> dict[str, list[float]]:
    shares: dict[str, list[float]] = {}
    for sensor in payload.values():
        if not isinstance(sensor, Mapping):
            continue
        for edge, values in (sensor.get("edge_shares") or {}).items():
            shares[str(edge)] = list(values)
    return shares


def _projection_bounds(schedules) -> tuple[str, str]:
    starts = [
        datetime.fromisoformat(interval.start_time)
        for schedule in schedules
        for interval in schedule.intervals
    ]
    ends = [
        datetime.fromisoformat(interval.end_time)
        for schedule in schedules
        for interval in schedule.intervals
    ]
    if not starts or not ends:
        raise ValueError("closure search generated no intervals")
    return (
        min(starts).isoformat(timespec="seconds"),
        max(ends).isoformat(timespec="seconds"),
    )


def build_screening_artifact(
    spec_path: Path,
    *,
    road_domain_status: str = "not_evaluated",
    policy: ShortlistPolicy = ShortlistPolicy(),
) -> dict[str, Any]:
    """Build one proxy artifact from immutable project inputs."""
    spec = load_closure_search_spec(spec_path)
    schedules = generate_closure_schedules(spec)
    if not schedules:
        raise ValueError("closure search has no legal schedules")

    forecast = _read(FORECAST_PATH)
    reference = _read(REFERENCE_PATH)
    geo = _read(GEO_PATH)
    registry = _read(SENSORS_PATH)
    directions = _read(DIRECTION_PATH)
    assignment = _read(ASSIGNMENT_PATH)
    learned = _read(PRIORS_PATH)
    observability = _read(OBSERVABILITY_PATH)
    network = _read(NETWORK_METADATA_PATH)
    sensor_edges, sensor_semantics, confidence = _sensor_contracts(
        geo, registry
    )

    successors = network.get("successors", {})
    edge_metadata = network.get("edges", {})
    missing_worksite_edges = sorted(
        set(spec.directed_edges) - set(edge_metadata)
    )
    if missing_worksite_edges:
        raise ValueError(
            "worksite edges are missing from the simulation network: "
            + ", ".join(missing_worksite_edges)
        )
    endpoints = {}
    for edge in spec.directed_edges:
        parts = edge.rsplit("_", 2)
        if len(parts) != 3:
            raise ValueError(f"worksite edge ID has no u_v_key form: {edge}")
        endpoints[edge] = (parts[0], parts[1])
    validate_connected_worksite(spec.directed_edges, endpoints)
    edge_cost_s = {}
    for edge, metadata in edge_metadata.items():
        try:
            length = float(metadata["length_m"])
            speed = float(metadata["speed_m_s"])
        except (KeyError, TypeError, ValueError):
            continue
        if length > 0 and speed > 0:
            edge_cost_s[edge] = length / speed
    detours = plausible_detour_paths(
        spec.directed_edges,
        successors,
        edge_cost_s,
    )
    relevant_edges = set(spec.directed_edges)
    relevant_edges.update(edge for path in detours for edge in path.edges)
    projection_start, projection_end = _projection_bounds(schedules)
    projection = project_forecast_flows(
        sorted(relevant_edges),
        projection_start=projection_start,
        projection_end_exclusive=projection_end,
        forecast_payload=forecast,
        reference_payload=reference,
        sensor_edges=sensor_edges,
        sensor_semantics=sensor_semantics,
        direction_shares=_direction_shares(directions),
        assignment_flows=assignment.get("flows", {}),
        learned_priors=learned.get("edges", {}),
        corridor_payload=observability,
        structural_reference_date=STRUCTURAL_REFERENCE_DATE,
    )

    scored = []
    for schedule in schedules:
        quarters = schedule_quarter_indices(
            schedule,
            flow_epoch=datetime.fromisoformat(projection.flow_epoch),
            interval_minutes=projection.interval_minutes,
            n_intervals=projection.n_intervals,
        )
        station_coverage = (
            min(projection.scale_station_counts[index] for index in quarters)
            / projection.total_sensor_stations
            if quarters
            else 0.0
        )
        scored.append(
            score_schedule(
                schedule,
                closed_edges=spec.directed_edges,
                projected_flows=projection.flows,
                flow_epoch=projection.flow_epoch,
                interval_minutes=projection.interval_minutes,
                detour_paths=detours,
                capacity_veh_per_interval=observability.get(
                    "capacity_veh_per_quarter", {}
                ),
                structural_sources=projection.structural_sources,
                spatial_confidence=confidence,
                forecast_station_coverage=station_coverage,
                road_domain_status=road_domain_status,
            )
        )
    ranked = rank_schedules(scored)
    unavailable = [candidate for candidate in scored if not candidate["scoreable"]]
    shortlist = stratified_shortlist(
        ranked,
        all_candidate_count=len(scored),
        unavailable_candidate_count=len(unavailable),
        unavailable_candidates=unavailable,
        policy=policy,
    )

    inputs = {
        "closure_search_spec": Path(spec_path),
        "forecast": FORECAST_PATH,
        "reference": REFERENCE_PATH,
        "network_geojson": GEO_PATH,
        "sensor_registry": SENSORS_PATH,
        "direction_split": DIRECTION_PATH,
        "assignment_priors": ASSIGNMENT_PATH,
        "learned_priors": PRIORS_PATH,
        "observability": OBSERVABILITY_PATH,
        "network_metadata": NETWORK_METADATA_PATH,
    }
    return {
        "schema_version": 1,
        "kind": "monthly_closure_proxy_screening",
        "proxy_version": PROXY_VERSION,
        "search": spec.to_dict(),
        "claim_boundary": {
            "evidence_level": "screening_proxy",
            "predicted_delay_seconds": None,
            "global_best_claim_allowed": False,
            "ui_exposure_allowed": False,
            "reason": "held-out SUMO proxy gate has not passed",
        },
        "road_feature_domain_status": road_domain_status,
        "projection": {
            key: value
            for key, value in projection.to_dict().items()
            if key not in {"flows", "scale_factors", "scale_station_counts"}
        },
        "detour_paths": [path.to_dict() for path in detours],
        "candidate_count": len(scored),
        "scoreable_candidate_count": len(ranked),
        "unavailable_candidates": [
            {
                "schedule_id": candidate["schedule_id"],
                "evidence": candidate["evidence"],
                "coverage": candidate["coverage"],
            }
            for candidate in unavailable
        ],
        "ranked_candidates": ranked,
        "shortlist": shortlist,
        "input_fingerprints": fingerprint_files(inputs),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--spec", type=Path, required=True,
        help="Validated ClosureSearchSpec JSON.",
    )
    parser.add_argument(
        "--out", type=Path,
        help="Write directly here instead of an isolated search workspace.",
    )
    parser.add_argument(
        "--road-domain-status",
        choices=("not_evaluated", "in_domain", "out_of_domain"),
        default="not_evaluated",
        help="Applicability status from a frozen proxy validation stratum.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = None
    try:
        artifact = build_screening_artifact(
            args.spec,
            road_domain_status=args.road_domain_status,
        )
        if args.out is not None:
            _atomic_json(args.out, artifact)
            destination = args.out
        else:
            spec = load_closure_search_spec(args.spec)
            workspace = create_search_workspace(spec)
            scratch = workspace.scratch_dir / "monthly_proxy.json"
            _atomic_json(scratch, artifact)
            destination = workspace.publish_artifact(
                scratch,
                "monthly_proxy.json",
                kind="monthly_proxy_screening",
                provenance={
                    "proxy_version": PROXY_VERSION,
                    "search_content_key": spec.content_key,
                    "ui_exposure_allowed": False,
                },
            )
            workspace.finish("succeeded")
        shortlist = artifact["shortlist"]
        print(
            f"Wrote {destination}: {len(shortlist['entries'])} shortlisted "
            f"of {artifact['candidate_count']} legal schedules; "
            "screening only, UI remains disabled."
        )
    except (OSError, ValueError, KeyError) as exc:
        if workspace is not None and workspace.status == "running":
            workspace.finish("failed", error=str(exc))
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
