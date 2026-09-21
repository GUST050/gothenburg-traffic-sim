"""closure_effect_eligibility_v1 on the six structural roads of the real month.

1. Resolve the 30 qualified archives through the production validator, which
   binds each variant's SHA-256 and the archive content key.
2. Build ONE inventory (``tools.closure_effect_eligibility.build_inventory``):
   each catalog pool and each archive variant is parsed once with the
   production route parser, for all six candidates together.
3. Judge each candidate against that frozen table; publish the inventory
   evidence (append-only).
4. Only then, if a candidate is eligible, write a NEW append-only canary spec:
   the frozen profile spec with only its directed edge and search id
   replaced, bound to the policy, the candidate-list digest, the qualified
   archive manifest, the catalog pool identities and the inventory evidence.
   Eligible candidates keep the unchanged structural order; the first wins.
   If none is eligible, no spec is written and no edge is substituted.

Starts no SUMO, builds no demand or catalog, computes no cost, changes no
existing spec, and writes only its own append-only files.
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

SCHEMA = "closure_effect_inventory_evidence_q50_v2"
SPEC_SCHEMA = "wci_effect_canary_spec_v2"
PROFILE_SPEC = "validation/subhour_monthly_search_profile_spec_v1.json"
PRODUCTION_SOURCES = (
    "tools/closure_effect_eligibility.py",
    "tools/cost_ordered_benchmark.py",
    "tools/build_window_cost_index.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/metadata.py",
    "traffic_sim/simulation/monthly_demand.py",
)
CONTRACT_FILES = (
    "sumo/net.net.xml", "data_in/sensors.json", "web/data/network.geojson",
    "validation/closure_survivability_screen_v2.json", PROFILE_SPEC,
)
ORDER_RULE = ("eligible candidates first, then the unchanged structural "
              "survivability order (dist_sensor_m, edge_id); the first wins")


def _midpoint(coordinates: List[List[float]]):
    lon = sum(point[0] for point in coordinates) / len(coordinates)
    lat = sum(point[1] for point in coordinates) / len(coordinates)
    return lat, lon


def _haversine_m(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _sensor_context(edges: List[str]) -> Dict[str, Any]:
    """Registry role, measurement status and nearest sensor per edge."""
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
        roles = []
        for sensor in sensors:
            if edge in sensor["approved_edge_ids"]:
                roles.append({"sensor_id": sensor["sensor_id"],
                              "role": "measured_edge"})
            if (sensor.get("opposite_direction") or {}).get("edge_id") == edge:
                roles.append({"sensor_id": sensor["sensor_id"],
                              "role": "opposite_direction"})
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


def _qualified_archives(builder, bound, spec) -> Dict[str, Dict[str, Any]]:
    """Resolver records exactly as the production validator bound them."""
    from traffic_sim.core.closure_calendar import iter_closure_schedules

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
    resolved = {}
    for key, needed in sorted(required.items()):
        matches = builder.find_demand_archives(
            bound["runs_root"], needed,
            qualified_manifest=bound["qualified_manifest"],
            _archive_index=index)
        if not matches:
            raise SystemExit(f"no qualified archive for build key {key}")
        resolved[key] = {
            "archive": str(Path(matches[0]["archive"]).resolve()),
            "archive_content_key": matches[0]["archive_content_key"],
            "outputs": matches[0]["outputs"]}
    return resolved


def _row(road, rank, verdict, contract, sensors):
    edge = str(road["edge_id"])
    return {
        "edge_id": edge,
        "structural_rank": rank,
        "from_node": contract[edge].get("from_node"),
        "to_node": contract[edge].get("to_node"),
        **sensors[edge],
        "structurally_survivable": bool(road.get("survives_topology")),
        "screen_dist_sensor_m": road.get("dist_sensor_m"),
        **verdict.summary,
        "effect_eligible": verdict.effect_eligible,
        "reason_codes": list(verdict.reason_codes),
        "reasons": [dict(item) for item in verdict.reasons],
    }


def _pool_identities(inventory) -> Dict[str, Dict[str, Any]]:
    pools: Dict[str, Dict[str, Any]] = {}
    for key, entry in sorted(inventory.catalogs.items()):
        for pool in entry["pools"]:
            pools[pool] = {"catalog_key": key, "sha256": entry.get("sha256"),
                           "declared_sha256": entry["declared_sha256"]}
    return pools


def _spec_record(spec, chosen, context):
    from traffic_sim.core.contracts import ClosureSearchSpec

    token = hashlib.sha256(chosen["edge_id"].encode("utf-8")).hexdigest()[:10]
    derived = dataclasses.replace(
        spec, search_id=f"{spec.search_id}-effect-canary-v2-{token}",
        directed_edges=(chosen["edge_id"],))
    if ClosureSearchSpec.from_dict(derived.to_dict()) != derived:
        raise SystemExit("derived canary spec does not round-trip")
    return {
        "schema": SPEC_SCHEMA,
        "kind": "closure_search_canary_spec",
        "release_evidence": False,
        "purpose": ("bounded nonzero WindowCostIndex canary; an automatically "
                    "selected performance case, not a user closure"),
        "case_selection_policy": context["policy"],
        "spec": derived.to_dict(),
        "spec_content_key": derived.content_key,
        "chosen_edge": chosen["edge_id"],
        "chosen_verdict": chosen,
        "candidate_list_digest": context["candidate_list_digest"],
        "order_rule": ORDER_RULE,
        "qualified_demand_manifest": context["qualified_ref"],
        "catalog_pools": context["catalog_pools"],
        "inventory_evidence": {"path": context["evidence_path"],
                               "content_key": context["evidence_key"]},
        "inventory_content_key": context["inventory_content_key"],
        "source_spec": {
            "path": PROFILE_SPEC,
            "sha256": common.file_sha256(ROOT / PROFILE_SPEC),
            "search_id": spec.search_id,
            "content_key": spec.content_key,
            "directed_edges": list(spec.directed_edges),
            "left_unchanged": True,
        },
        "derivation": ("identical to the source spec except directed_edges "
                       "and search_id"),
        "profile": context["profile"],
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
    from tools import closure_effect_eligibility as cee
    from tools import cost_ordered_benchmark as bench

    sampler = common.Sampler()
    bound = builder._bound_inputs(args.profile)
    spec = bound["spec"]
    resolved = _qualified_archives(builder, bound, spec)
    if sorted(resolved) != sorted(bound["qualified_manifest"]["archives"]):
        raise SystemExit("resolved archives are not the qualified set")
    roads = bench.surviving_roads()
    candidates = [str(road["edge_id"]) for road in roads]
    network_edges, catalog_root = bench._effect_sources(ROOT)
    inventory = cee.build_inventory(candidates, cee.archive_refs(resolved),
                                    catalog_root=catalog_root)
    sampler.sample("inventory")
    inventory_record = inventory.to_dict()
    if set(inventory.parses.values()) != {1} or len(inventory.parses) != (
            len(cee.REQUIRED_VARIANTS) * len(resolved) + len(inventory.catalogs)):
        raise SystemExit(f"a file was not parsed exactly once: "
                         f"{inventory.parses}")
    if cee.verify_inventory(inventory_record):
        raise SystemExit("fresh inventory does not verify")
    units = bench._daily_units_by_build_key(spec, bound["runs_root"])
    verdicts = {edge: cee.assess(edge, inventory, network_edges=network_edges,
                                 required_build_keys=list(resolved),
                                 daily_units=units)
                for edge in candidates}
    contract = _network_contract(candidates)
    sensors = _sensor_context(candidates)
    rows = [_row(road, rank, verdicts[str(road["edge_id"])], contract,
                 sensors) for rank, road in enumerate(roads, start=1)]
    chosen_rows = cee.eligible_in_order(
        rows, lambda row: row, order_key=lambda row: row["structural_rank"])
    chosen = chosen_rows[0] if chosen_rows else None
    sampler.sample("end")

    profile = {"path": str(args.profile),
               "sha256": common.file_sha256(args.profile)}
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "case_selection_policy": cee.POLICY,
        "driver": common.driver_binding(Path(__file__)),
        "helpers": common.source_bindings((
            "validation/benchmarks/wci_closure_edge_census_v1.py",)),
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "contract_files": common.source_bindings(CONTRACT_FILES),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "profile": profile,
        "qualified_demand_manifest": bound["qualified_ref"],
        "candidates": candidates,
        "candidate_list_digest": common.digest(candidates),
        "candidate_source": ("tools.cost_ordered_benchmark.surviving_roads, "
                             "unchanged"),
        "daily_units_by_build_key": dict(sorted(units.items())),
        "inventory": inventory_record,
        "inventory_content_key": inventory.content_key,
        "catalog_pools": _pool_identities(inventory),
        "rows": rows,
        "order_rule": ORDER_RULE,
        "eligible_in_order": [row["edge_id"] for row in chosen_rows],
        "selected_edge": chosen["edge_id"] if chosen else None,
        "outcome": ("canary spec written" if chosen else
                    "no structural candidate is effect-eligible; a new "
                    "benchmark case is needed"),
        "memory": {"samples": sampler.samples},
    }
    record["output_sha256"] = common.digest({
        "candidates": candidates, "rows": rows,
        "inventory_content_key": inventory.content_key,
        "eligible_in_order": record["eligible_in_order"],
    })
    published = common.publish(args.out, record)
    spec_record = None
    if chosen:
        spec_record = common.publish(args.canary_spec_out, _spec_record(
            spec, chosen, {
                "policy": cee.POLICY,
                "candidate_list_digest": record["candidate_list_digest"],
                "qualified_ref": bound["qualified_ref"],
                "catalog_pools": record["catalog_pools"],
                "evidence_path": str(args.out),
                "evidence_key": published["content_key"],
                "inventory_content_key": inventory.content_key,
                "profile": profile,
            }))
    print(json.dumps({
        "rows": [{k: row[k] for k in (
            "edge_id", "from_node", "to_node", "nearest_sensor",
            "measurement_status", "structurally_survivable",
            "catalog_routes", "crossings", "archives_with_crossings",
            "daily_units_with_crossings", "effect_eligible",
            "reason_codes")} for row in rows],
        "eligible_in_order": record["eligible_in_order"],
        "files_parsed": len(inventory.parses),
        "wall_s": round(sampler.samples[-1]["wall_s"], 1),
        "content_key": published["content_key"],
        "inventory_content_key": inventory.content_key,
        "output_sha256": record["output_sha256"],
        "canary_spec": (None if spec_record is None else {
            "search_id": spec_record["spec"]["search_id"],
            "spec_content_key": spec_record["spec_content_key"],
            "content_key": spec_record["content_key"]}),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
