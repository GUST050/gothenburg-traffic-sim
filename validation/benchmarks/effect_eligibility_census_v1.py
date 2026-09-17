"""The six structural benchmark roads, judged for effect on the real month.

Runs the production ``tools.cost_ordered_benchmark.road_screen`` against the
30 qualified September archives and reports, for every road the unchanged
survivability rule ranks in its first six, the two properties side by side:
``structurally_survivable`` and ``effect_eligible`` (pre-canary stage; the
canary stage is recorded separately by ``wci_effect_canary_v1``). Every road
the NEW rule would choose is listed too.

The nearest sensor is taken from the validated registry when the road is a
sensor's measured edge or opposite carriageway, and otherwise from the
shortest great-circle distance between the road's geometry midpoint and a
measured sensor edge's midpoint in ``web/data/network.geojson``.

Starts no SUMO, builds no demand or catalog, computes no WCI, changes no spec
or edge, and writes only its own append-only evidence file.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import wci_diag_common as common  # noqa: E402
from wci_closure_edge_census_v1 import _network_contract  # noqa: E402

SCHEMA = "effect_eligibility_census_v1"
PRODUCTION_SOURCES = (
    "tools/cost_ordered_benchmark.py",
    "traffic_sim/simulation/effect_eligibility.py",
    "traffic_sim/demand/route_support.py",
    "traffic_sim/simulation/metadata.py",
    "traffic_sim/simulation/deterministic_disruption.py",
)
CONTRACT_FILES = (
    "sumo/net.net.xml", "data_in/sensors.json", "web/data/network.geojson",
    "validation/closure_survivability_screen_v2.json",
)


def _midpoint(coordinates: List[List[float]]):
    lon = sum(point[0] for point in coordinates) / len(coordinates)
    lat = sum(point[1] for point in coordinates) / len(coordinates)
    return lat, lon


def _haversine_m(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _registry_roles(edge: str, sensors: List[Dict[str, Any]]):
    roles = []
    for sensor in sensors:
        opposite = sensor.get("opposite_direction") or {}
        if edge in sensor["approved_edge_ids"]:
            roles.append({"sensor_id": sensor["sensor_id"],
                          "role": "measured_edge",
                          "measurement_semantics":
                              sensor.get("measurement_semantics")})
        if opposite.get("edge_id") == edge:
            roles.append({"sensor_id": sensor["sensor_id"],
                          "role": "opposite_direction",
                          "measurement_status":
                              opposite.get("measurement_status")})
    return roles


def _sensor_context(edges: List[str]) -> Dict[str, Any]:
    sensors = json.loads((ROOT / "data_in/sensors.json").read_text(
        encoding="utf-8"))["sensors"]
    geo = json.loads((ROOT / "web/data/network.geojson").read_text(
        encoding="utf-8"))
    midpoints = {feature["properties"]["id"]:
                 _midpoint(feature["geometry"]["coordinates"])
                 for feature in geo["features"]
                 if feature["geometry"]["type"] == "LineString"}
    measured = [(sensor["sensor_id"], edge) for sensor in sensors
                for edge in sensor["approved_edge_ids"]]
    result = {}
    for edge in edges:
        roles = _registry_roles(edge, sensors)
        distance, nearest = min(
            ((_haversine_m(midpoints[edge], midpoints[other]), sensor_id)
             for sensor_id, other in measured
             if edge in midpoints and other in midpoints),
            default=(None, None))
        if any(role["role"] == "measured_edge" for role in roles):
            status = "measured"
        elif roles:
            status = "unmeasured_opposite_of_sensor"
        else:
            status = "not_in_sensor_registry"
        result[edge] = {
            "measurement_status": status,
            "registry_roles": roles,
            "nearest_sensor": {
                "sensor_id": roles[0]["sensor_id"] if roles else nearest,
                "basis": ("registry" if roles else
                          "midpoint great-circle to measured sensor edge"),
                "midpoint_distance_m": (round(distance, 1)
                                        if distance is not None else None),
            },
        }
    return result


def _qualified_check(bound, archives: List[Path]) -> Dict[str, Any]:
    keys = sorted(json.loads((archive / "demand_meta.json").read_text(
        encoding="utf-8"))["demand_build_key"] for archive in archives)
    qualified = sorted(bound["qualified_manifest"]["archives"])
    return {"archive_build_keys_equal_qualified_manifest": keys == qualified,
            "archives": len(keys), "qualified": len(qualified)}


def _row(road, contract, sensors, new_rank):
    edge = str(road["edge_id"])
    verdict = road["effect"]
    evidence = verdict["evidence"]
    return {
        "edge_id": edge,
        "from_node": contract[edge].get("from_node"),
        "to_node": contract[edge].get("to_node"),
        "reverse_edges": contract[edge].get("reverse_edges"),
        **sensors[edge],
        "screen_dist_sensor_m": road.get("dist_sensor_m"),
        "catalog_routes": {name.split(":", 1)[0]: count for name, count
                           in evidence["catalog_routes"].items()},
        "catalog_pool_keys": sorted(evidence["catalog_routes"]),
        "vehicles": evidence["archive_vehicles"],
        "vehicles_total": sum(evidence["archive_vehicles"].values()),
        "archives_with_vehicle": evidence["archives_with_vehicle"],
        "structurally_survivable": road["structurally_survivable"],
        "pre_canary_eligible": verdict["pre_canary_eligible"],
        "effect_eligible": verdict["effect_eligible"],
        "reasons": verdict["reasons"],
        "rejection_reasons": [reason for reason in verdict["reasons"]
                              if reason != "canary_not_run"],
        "new_rule_rank": new_rank.get(edge),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"evidence already exists: {args.out}")

    from tools import build_window_cost_index as builder
    from tools import cost_ordered_benchmark as bench

    sampler = common.Sampler()
    bound = builder._bound_inputs(args.profile)
    runs_root = Path(bound["runs_root"])
    archives = bench._complete_archives(runs_root)
    qualified = _qualified_check(bound, archives)
    if not qualified["archive_build_keys_equal_qualified_manifest"]:
        raise SystemExit(f"archive set is not the qualified set: {qualified}")

    structural_six = [str(road["edge_id"]) for road in bench.surviving_roads()]
    screen = bench.road_screen(runs_root, data_root=ROOT)
    chosen = bench.benchmark_roads(runs_root, data_root=ROOT)
    new_rank = {str(road["edge_id"]): rank
                for rank, road in enumerate(chosen, start=1)}
    by_edge = {str(road["edge_id"]): road for road in screen}
    reported = list(dict.fromkeys(structural_six + list(new_rank)))
    contract = _network_contract(reported)
    sensors = _sensor_context(reported)
    rows = [_row(by_edge[edge], contract, sensors, new_rank)
            for edge in reported]
    sampler.sample("end")

    summary = {
        "structural_six": structural_six,
        "structural_six_effect_capable": [
            row["edge_id"] for row in rows
            if row["edge_id"] in structural_six and row["pre_canary_eligible"]],
        "new_rule_choice": list(new_rank),
        "screened_roads": len(screen),
        "screened_roads_effect_capable": sum(
            1 for road in screen if road["effect"]["pre_canary_eligible"]),
        "canary_stage": ("not run here; effect_eligible stays false with "
                         "canary_not_run until wci_effect_canary_v1 records "
                         "the canary"),
    }
    full_screen = [{
        "edge_id": road["edge_id"],
        "structurally_survivable": road["structurally_survivable"],
        "pre_canary_eligible": road["effect"]["pre_canary_eligible"],
        "reasons": road["effect"]["reasons"],
        "evidence": road["effect"]["evidence"],
    } for road in screen]
    archive_set = {
        **qualified,
        "digest": common.digest({
            archive.name: {name: common.file_sha256(archive / name)
                           for name in sorted(bench.VARIANT_FILENAMES.values())}
            for archive in archives}),
    }
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "driver": common.driver_binding(Path(__file__)),
        "census_helper": {
            "path": "validation/benchmarks/wci_closure_edge_census_v1.py",
            "sha256": common.file_sha256(
                HERE / "wci_closure_edge_census_v1.py")},
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "contract_files": common.source_bindings(CONTRACT_FILES),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "profile": str(args.profile),
        "profile_sha256": common.file_sha256(args.profile),
        "qualified_demand_manifest": bound["qualified_ref"],
        "runs_root": str(runs_root),
        "archive_set": archive_set,
        "road_selection_rule": bench.ROAD_SELECTION_RULE,
        "summary": summary,
        "candidates": rows,
        "full_screen": full_screen,
        "memory": {"samples": sampler.samples},
    }
    record["output_sha256"] = common.digest({
        "summary": summary, "candidates": rows,
        "full_screen": full_screen, "archive_set": archive_set,
    })
    published = common.publish(args.out, record)
    print(json.dumps({
        "summary": summary,
        "candidates": [{k: row[k] for k in (
            "edge_id", "from_node", "to_node", "nearest_sensor",
            "measurement_status", "catalog_routes", "vehicles",
            "structurally_survivable", "pre_canary_eligible",
            "effect_eligible", "rejection_reasons", "new_rule_rank")}
            for row in rows],
        "wall_s": round(sampler.samples[-1]["wall_s"], 1),
        "content_key": published["content_key"],
        "output_sha256": record["output_sha256"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
