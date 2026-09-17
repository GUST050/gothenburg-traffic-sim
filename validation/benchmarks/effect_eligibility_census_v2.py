"""Effect eligibility of the six structural benchmark roads on the real month.

One streaming inventory (``effect_eligibility.build_inventory``) reads each of
the 30 qualified archives' three variant files and each bound catalog pool
exactly once, verifies every variant against the SHA-256 the production
archive validator bound, and freezes
``edge -> variant -> build_key -> crossing vehicles``. The six roads the
unchanged survivability rule ranks first are then judged against that one
mapping with ``effect_eligibility.assess`` (pre-canary stage).

The first road, in structural order, that passes the pre-canary stage becomes
an append-only canary spec: the frozen profile spec with only its directed
edge and search id replaced, with full provenance. If no road passes, no spec
is written and no other edge is substituted.

Starts no SUMO, builds no demand or catalog, computes no cost, changes no
existing spec, and writes only its own append-only evidence and spec files.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
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

SCHEMA = "effect_eligibility_census_v2"
SPEC_SCHEMA = "wci_effect_canary_spec_v1"
PROFILE_SPEC = "validation/subhour_monthly_search_profile_spec_v1.json"
PRODUCTION_SOURCES = (
    "traffic_sim/simulation/effect_eligibility.py",
    "tools/cost_ordered_benchmark.py",
    "tools/build_window_cost_index.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/metadata.py",
    "traffic_sim/simulation/monthly_demand.py",
)
CONTRACT_FILES = (
    "sumo/net.net.xml", "data_in/sensors.json", "web/data/network.geojson",
    "validation/closure_survivability_screen_v2.json", PROFILE_SPEC,
)
SELECTION_RULE = (
    "the first of the six structurally surviving roads (unchanged "
    "survivability order) that passes the pre-canary stage of "
    "effect_eligibility_v2 over all 30 qualified archives")


def _midpoint(coordinates: List[List[float]]):
    lon = sum(point[0] for point in coordinates) / len(coordinates)
    lat = sum(point[1] for point in coordinates) / len(coordinates)
    return lat, lon


def _haversine_m(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _registry_roles(edge: str, sensors) -> List[Dict[str, Any]]:
    roles = []
    for sensor in sensors:
        opposite = sensor.get("opposite_direction") or {}
        if edge in sensor["approved_edge_ids"]:
            roles.append({"sensor_id": sensor["sensor_id"],
                          "role": "measured_edge"})
        if opposite.get("edge_id") == edge:
            roles.append({"sensor_id": sensor["sensor_id"],
                          "role": "opposite_direction",
                          "measurement_status":
                              opposite.get("measurement_status")})
    return roles


def _sensor_context(edges: List[str]) -> Dict[str, Any]:
    """Measurement status and nearest sensor, registry first."""
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
                                        if distance is not None else None)},
        }
    return result


def _qualified_archives(builder, bound, spec):
    """ArchiveRefs exactly as the production validator bound them."""
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from traffic_sim.simulation import effect_eligibility as effect

    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=bound["runs_root"], build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="subhour-phase5-raw-index",
        qualified_demand_manifest=bound["qualified_manifest"])
    required = {}
    for parent in iter_closure_schedules(spec):
        for _unit, _identity, build in builder.daily_unit_records(spec, parent):
            needed = resolver._required(build())
            required.setdefault(needed.build_key, needed)
    index = builder._archives_for_build_key(bound["runs_root"])
    variant_by_file = {name: variant
                       for variant, name in effect.VARIANT_FILENAMES.items()}
    refs = []
    for key, needed in sorted(required.items()):
        matches = builder.find_demand_archives(
            bound["runs_root"], needed,
            qualified_manifest=bound["qualified_manifest"],
            _archive_index=index)
        if not matches:
            raise SystemExit(f"no qualified archive for build key {key}")
        match = matches[0]
        refs.append(effect.ArchiveRef(
            build_key=key, archive=Path(match["archive"]).resolve(),
            content_key=match["archive_content_key"],
            route_sha256={variant_by_file[item["name"]]: item["sha256"]
                          for item in match["outputs"]
                          if item["name"] in variant_by_file}))
    return refs


def _candidate_row(road, rank, verdict, contract, sensors):
    edge = str(road["edge_id"])
    summary = verdict.summary
    return {
        "edge_id": edge,
        "structural_rank": rank,
        "from_node": contract[edge].get("from_node"),
        "to_node": contract[edge].get("to_node"),
        **sensors[edge],
        "structurally_survivable": bool(road.get("survives_topology")),
        "screen_dist_sensor_m": road.get("dist_sensor_m"),
        "catalog_routes": summary["catalog_routes"],
        "vehicles": summary["vehicles"],
        "archives": summary["archives"],
        "archives_with_traffic": summary["archives_with_traffic"],
        "daily_units": summary["daily_units"],
        "daily_units_with_traffic": summary["daily_units_with_traffic"],
        "pre_canary_eligible": verdict.pre_canary_eligible,
        "effect_eligible": verdict.effect_eligible,
        "reason_codes": list(verdict.reasons),
        "rejection_reason_codes": [code for code in verdict.reasons
                                   if code != "canary_not_run"],
    }


def _canary_spec_record(spec, chosen, census_path, census_key, profile):
    from traffic_sim.core.contracts import ClosureSearchSpec

    token = hashlib.sha256(chosen["edge_id"].encode("utf-8")).hexdigest()[:10]
    derived = dataclasses.replace(
        spec, search_id=f"{spec.search_id}-effect-canary-v1-{token}",
        directed_edges=(chosen["edge_id"],))
    if ClosureSearchSpec.from_dict(derived.to_dict()) != derived:
        raise SystemExit("derived canary spec does not round-trip")
    return {
        "schema": SPEC_SCHEMA,
        "kind": "closure_search_canary_spec",
        "release_evidence": False,
        "purpose": ("bounded nonzero WindowCostIndex canary; an automatically "
                    "selected performance case, not a user closure"),
        "spec": derived.to_dict(),
        "spec_content_key": derived.content_key,
        "provenance": {
            "source_spec": {
                "path": PROFILE_SPEC,
                "sha256": common.file_sha256(ROOT / PROFILE_SPEC),
                "search_id": spec.search_id,
                "content_key": spec.content_key,
                "directed_edges": list(spec.directed_edges),
            },
            "source_spec_left_unchanged": True,
            "derivation": ("identical to the source spec except "
                           "directed_edges and search_id"),
            "profile": profile,
            "selection_rule": SELECTION_RULE,
            "effect_census": {"path": str(census_path),
                              "content_key": census_key},
            "chosen": chosen,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--canary-spec-out", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.out, args.canary_spec_out):
        if path.exists():
            raise SystemExit(f"append-only output already exists: {path}")

    from tools import build_window_cost_index as builder
    from tools import cost_ordered_benchmark as bench
    from traffic_sim.simulation import effect_eligibility as effect

    sampler = common.Sampler()
    bound = builder._bound_inputs(args.profile)
    spec = bound["spec"]
    refs = _qualified_archives(builder, bound, spec)
    if sorted(ref.build_key for ref in refs) != sorted(
            bound["qualified_manifest"]["archives"]):
        raise SystemExit("resolved archives are not the qualified set")
    sampler.sample("resolved")

    roads = bench.surviving_roads()
    edges = [str(road["edge_id"]) for road in roads]
    network_edges, catalog_root = bench._effect_sources(ROOT)
    inventory = effect.build_inventory(refs, edges, catalog_root=catalog_root)
    reads = inventory.file_reads
    if set(reads.values()) != {1} or len(reads) != 3 * len(refs) + len(
            inventory.catalogs):
        raise SystemExit(f"inventory did not read each file once: {reads}")
    sampler.sample("inventory")
    units = bench._daily_units_by_build_key(spec, bound["runs_root"])
    contract = _network_contract(edges)
    sensors = _sensor_context(edges)
    rows = [
        _candidate_row(road, rank, effect.assess(
            str(road["edge_id"]), inventory, network_edges=network_edges,
            daily_units=units), contract, sensors)
        for rank, road in enumerate(roads, start=1)]
    chosen = next((row for row in rows if row["pre_canary_eligible"]), None)
    sampler.sample("end")

    profile = {"path": str(args.profile),
               "sha256": common.file_sha256(args.profile)}
    inventory_record = inventory.to_dict()
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
        "profile": profile,
        "qualified_demand_manifest": bound["qualified_ref"],
        "contract": effect.CONTRACT_VERSION,
        "structural_rule": ("tools.cost_ordered_benchmark.surviving_roads, "
                            "unchanged"),
        "selection_rule": SELECTION_RULE,
        "daily_units_by_build_key": dict(sorted(units.items())),
        "candidates": rows,
        "selected_edge": chosen["edge_id"] if chosen else None,
        "outcome": ("canary spec written" if chosen else
                    "no structural candidate is effect-eligible; a new "
                    "benchmark case is needed"),
        "canary_spec_path": str(args.canary_spec_out) if chosen else None,
        "inventory": inventory_record,
        "inventory_digest": common.digest(inventory_record),
        "memory": {"samples": sampler.samples},
    }
    record["output_sha256"] = common.digest({
        "candidates": rows, "selected_edge": record["selected_edge"],
        "inventory_digest": record["inventory_digest"],
        "daily_units_by_build_key": record["daily_units_by_build_key"],
    })
    published = common.publish(args.out, record)
    spec_record = None
    if chosen:
        spec_record = common.publish(args.canary_spec_out, _canary_spec_record(
            spec, chosen, args.out, published["content_key"], profile))
    print(json.dumps({
        "candidates": [{k: row[k] for k in (
            "edge_id", "from_node", "to_node", "nearest_sensor",
            "measurement_status", "structurally_survivable",
            "catalog_routes", "vehicles", "archives_with_traffic",
            "daily_units_with_traffic", "pre_canary_eligible",
            "effect_eligible", "rejection_reason_codes")} for row in rows],
        "selected_edge": record["selected_edge"],
        "files_read": len(reads),
        "wall_s": round(sampler.samples[-1]["wall_s"], 1),
        "content_key": published["content_key"],
        "output_sha256": record["output_sha256"],
        "canary_spec": (None if spec_record is None else {
            "search_id": spec_record["spec"]["search_id"],
            "spec_content_key": spec_record["spec_content_key"],
            "content_key": spec_record["content_key"]}),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
