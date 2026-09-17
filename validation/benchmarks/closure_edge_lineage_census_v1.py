"""Where the frozen month spec's closure edge came from, and what each
candidate direction would actually carry.

Answers, from repository artifacts only:

* lineage -- which files and tools put ``26355153_26842525_0`` into
  ``validation/subhour_monthly_search_profile_spec_v1.json``, and whether any
  step turned a road choice into a direction;
* contract -- whether ``closure_seconds`` closes exactly the spec's directed
  edges (no expansion to a street) and whether the web API's live-demand
  support gate would have admitted the edge;
* coverage -- for every edge in that lineage and for each edge's reverse (read
  from the SUMO network's own from/to attributes): registry role, catalog
  routes per pool, crossing vehicles per build key and variant over all 30
  qualified archives, exposed daily units, and whether the edge could support
  a nonzero WindowCostIndex canary.

The archives are read one at a time through the production resolution and
parser, and released before the next one is opened. "Exposed" daily units are
those whose archive has at least one crossing vehicle; the closure-window
predicate is NOT applied, so exposure is an upper bound on affected units.

Starts no SUMO, builds no demand or catalog, computes no WCI, changes no spec
or edge, and writes only its own append-only evidence file.
"""
from __future__ import annotations

import argparse
import dataclasses
import gc
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import wci_diag_common as common  # noqa: E402
from wci_closure_edge_census_v1 import (  # noqa: E402
    _network_contract,
    _resolve_archives,
)

SCHEMA = "closure_edge_lineage_census_v1"
VARIANTS = ("q10", "q50", "q90")
PROFILE_SPEC = "validation/subhour_monthly_search_profile_spec_v1.json"
ANCESTOR_SPECS = (
    "validation/golden_monthly_search_spec_v6.json",
    "validation/independent_daily_benchmark_spec_v1.json",
)
SCREEN = "validation/closure_survivability_screen_v2.json"
REGISTRATIONS = (
    "validation/subhour_bounded_sumo_registration_20260831-impl.json",
    "validation/subhour_bounded_sumo_registration_20260831-v38.json",
)
LIVE_ROUTES = ("sumo/calibrated.rou.xml", "sumo/calibrated_v1.rou.xml",
               "sumo/calibrated_v2.rou.xml")
PRODUCTION_SOURCES = (
    "tools/build_window_cost_index.py",
    "tools/cost_ordered_benchmark.py",
    "serve.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/demand/route_support.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/independent_daily.py",
)
CONTRACT_FILES = (
    "sumo/net.net.xml", "data_in/sensors.json", "web/data/network.geojson",
    PROFILE_SPEC, SCREEN, *ANCESTOR_SPECS, *REGISTRATIONS, *LIVE_ROUTES,
)


def _json(path: str) -> Dict[str, Any]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, check=True).stdout


def _spec_history(edge: str) -> List[Dict[str, Any]]:
    """Every commit of the profile spec (following copies) and its edges."""
    log = _git("log", "--follow", "--format=commit %H", "--name-only", "--",
               PROFILE_SPEC)
    history, commit = [], None
    for line in log.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1]
        elif line.strip() and commit:
            spec = json.loads(_git("show", f"{commit}:{line.strip()}"))
            edges = spec.get("directed_edges") or []
            history.append({
                "commit": commit[:7],
                "path": line.strip(),
                "search_id": spec.get("search_id"),
                "directed_edges": edges,
                "policy_status": spec.get("policy_status"),
                "contains_edge": edge in edges,
            })
            commit = None
    return history


def _lineage(edge: str) -> Dict[str, Any]:
    from tools import cost_ordered_benchmark as benchmark
    from traffic_sim.core.contracts import ClosureSearchSpec

    screen = _json(SCREEN)
    discovery = benchmark.surviving_roads(ROOT / SCREEN)
    registrations = {}
    for path in REGISTRATIONS:
        record = _json(path)
        registrations[path] = {
            "rule": record["selection"]["rule"],
            "distinct_edges": record["selection"]["distinct_edges"],
            "selected_edges": [case["spec"]["directed_edges"]
                               for case in record["selected_cases"]],
        }
    default_policy = next(item.default
                          for item in dataclasses.fields(ClosureSearchSpec)
                          if item.name == "policy_status")
    return {
        "profile_spec_history": _spec_history(edge),
        "ancestor_spec_edges": {path: _json(path)["directed_edges"]
                                for path in ANCESTOR_SPECS},
        "screen": {
            "path": SCREEN,
            "rule": screen["candidate_pool"]["rule"],
            "pool_size": screen["candidate_pool"]["size"],
            "treats_directions_independently": True,
        },
        "discovery": {
            "function": "tools.cost_ordered_benchmark.surviving_roads",
            "order": ("dist_sensor_m, then edge_id; first "
                      f"{benchmark.DISCOVERY_ROAD_LIMIT}"),
            "edges": [{"edge_id": road["edge_id"],
                       "dist_sensor_m": road["dist_sensor_m"],
                       "rank": rank}
                      for rank, road in enumerate(discovery, start=1)],
            "spec_builder": ("discovered_specs: one ClosureSearchSpec per "
                             "discovered edge with directed_edges=(edge,)"),
        },
        "registrations": registrations,
        "policy_status_default": default_policy,
        "policy_status_note": (
            "user_supplied_unverified is the dataclass default, so its "
            "presence does not show that a user supplied the edge"),
    }


def _contract_checks(spec, archive: Path, edges: List[str]) -> Dict[str, Any]:
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from traffic_sim.demand.route_support import combined_route_edges
    from traffic_sim.simulation.deterministic_disruption import (
        ArchiveInputs, closure_seconds)
    from traffic_sim.simulation.independent_daily import daily_unit_records

    inputs = ArchiveInputs.from_archive(archive)
    emitted, checked = set(), 0
    for parent in iter_closure_schedules(spec):
        for _unit, _identity, build in daily_unit_records(spec, parent):
            try:
                closures = closure_seconds(
                    spec, build(), epoch=inputs.epoch,
                    duration_s=inputs.duration_s)
            except ValueError:
                continue  # this unit lies in another archive's window
            emitted.update(item["edge_id"] for item in closures)
            checked += 1
        if checked:
            break
    if not checked:
        raise SystemExit("no daily unit fits the first archive")
    live = combined_route_edges([ROOT / path for path in LIVE_ROUTES])
    return {
        "closure_seconds_units_checked": checked,
        "closure_seconds_edges": sorted(emitted),
        "closure_seconds_equals_spec_edges": emitted == set(
            spec.directed_edges),
        "closure_seconds_note": ("closure_seconds emits one closure per "
                                 "spec.directed_edges entry; no street or "
                                 "reverse expansion exists"),
        "live_demand_supported": {edge: edge in live for edge in edges},
        "live_demand_gate": ("serve.py _require_supported_closure_edges "
                             "rejects (HTTP 422) any directed edge absent "
                             "from the live sumo/calibrated*.rou.xml; the "
                             "tool path that produced the profile spec has "
                             "no such gate"),
    }


def _registry(edge: str, sensors: List[Dict[str, Any]]):
    roles = []
    for sensor in sensors:
        opposite = sensor.get("opposite_direction") or {}
        verification = sensor.get("catalogue_verification") or {}
        provenance = {
            "sensor_id": sensor["sensor_id"],
            "source": sensor.get("source"),
            "catalogue_verification": verification.get("status"),
            "snap_status": sensor.get("snap_status"),
            "measurement_semantics": sensor.get("measurement_semantics"),
        }
        if edge in sensor.get("approved_edge_ids", ()):
            roles.append({"role": "measured_edge", **provenance})
        if opposite.get("edge_id") == edge:
            roles.append({"role": "opposite_direction",
                          "measurement_status":
                              opposite.get("measurement_status"),
                          "resolved_by": opposite.get("resolved_by"),
                          **provenance})
    if any(role["role"] == "measured_edge" for role in roles):
        status = "measured"
    elif roles:
        status = "unmeasured_opposite_of_sensor"
    else:
        status = "not_in_sensor_registry"
    return status, roles


def _catalog_routes(edges: List[str], pools: Dict[str, str]):
    counts = {}
    for pool, key in sorted(pools.items()):
        path = ROOT / "sumo/route_catalog" / key / "catalog.rou.xml"
        routes = [set(route.get("edges", "").split())
                  for route in ET.parse(path).getroot().iter("route")]
        counts[pool] = {
            "catalog_key": key,
            "routes_sha256": common.file_sha256(path),
            "routes": len(routes),
            "per_edge": {edge: sum(1 for route in routes if edge in route)
                         for edge in edges},
        }
    return counts


def _archive_counts(path: Path, edges: List[str]):
    from traffic_sim.simulation.disruption import parse_route_vehicles

    parsed = parse_route_vehicles(path)
    per_route = Counter(vehicle[0] for vehicle in parsed)
    total = len(parsed)
    del parsed
    wanted = set(edges)
    result = {edge: {"vehicles": 0, "unique_routes": 0} for edge in edges}
    for route, vehicles in per_route.items():
        for edge in wanted.intersection(route):
            result[edge]["vehicles"] += vehicles
            result[edge]["unique_routes"] += 1
    return total, result


def _usable(entry: Dict[str, Any]) -> Dict[str, Any]:
    reasons = []
    network = entry["network"]
    if not network.get("exists"):
        reasons.append("not in SUMO network")
    elif not network.get("incoming_connections"):
        reasons.append("no incoming connection")
    if sum(pool["routes"] for pool in entry["catalog_routes"].values()) == 0:
        reasons.append("no route in any recorded catalog pool")
    if entry["vehicles"]["total"] == 0:
        reasons.append("no calibrated vehicle in any of the 30 archives")
    survives = entry["screen"]["survives_topology"]
    if survives is None:
        reasons.append("not in the survivability screen")
    elif not survives:
        reasons.append("closing it severs a successor")
    return {"nonzero_canary_usable": not reasons, "reasons": reasons}


def _scan_archives(resolved, edges):
    """Per build key and variant counts, one archive in memory at a time."""
    from traffic_sim.simulation.deterministic_disruption import ArchiveInputs

    pools_seen: Dict[str, str] = {}
    per_key: Dict[str, Dict[str, Any]] = {}
    for item in resolved:
        archive = Path(item["archive"])
        inputs = ArchiveInputs.from_archive(archive)
        catalog = json.loads((archive / "demand_meta.json").read_text(
            encoding="utf-8"))["candidate_catalog"]
        for pool_name, key in catalog["keys"].items():
            if pools_seen.setdefault(pool_name, key) != key:
                raise SystemExit(f"two {pool_name} catalogs in the month")
        variants = {}
        for variant in VARIANTS:
            total, counts = _archive_counts(inputs.variant_paths[variant],
                                            edges)
            variants[variant] = {"file_sha256": inputs.variant_sha256[variant],
                                 "total_vehicles": total, "edges": counts}
        per_key[item["build_key"]] = {
            "archive": archive.name,
            "archive_content_key": item["archive_content_key"],
            "daily_units": item["daily_units"],
            "catalog_pools": sorted(catalog["keys"]),
            "variants": variants,
        }
        del inputs
        gc.collect()
    return pools_seen, per_key


def _edge_entry(edge, *, per_key, network, sensors, geo, pool,
                discovery_rank, lineage_edges, spec_edge, contract,
                catalog_routes):
    vehicles = {v: sum(k["variants"][v]["edges"][edge]["vehicles"]
                       for k in per_key.values()) for v in VARIANTS}
    vehicles["total"] = sum(vehicles.values())
    exposed = [key for key, k in per_key.items()
               if any(k["variants"][v]["edges"][edge]["vehicles"]
                      for v in VARIANTS)]
    status, roles = _registry(edge, sensors)
    props = geo.get(edge, {})
    screen_item = pool.get(edge) or {}
    entry = {
        "edge_id": edge,
        "network": network[edge],
        "network_geojson": {k: props.get(k) for k in (
            "name", "highway", "dist_sensor_m", "bidirectional",
            "sensor_id")},
        "measurement_status": status,
        "registry_roles": roles,
        "screen": {
            "in_pool": edge in pool,
            "survives_topology": screen_item.get("survives_topology"),
            "dist_sensor_m": screen_item.get("dist_sensor_m"),
            "discovery_rank": discovery_rank.get(edge),
        },
        "in_lineage": edge in lineage_edges,
        "is_profile_spec_edge": edge == spec_edge,
        "live_demand_supported": contract["live_demand_supported"][edge],
        "catalog_routes": {name: {"routes": counts["per_edge"][edge],
                                  "catalog_key": counts["catalog_key"]}
                           for name, counts in catalog_routes.items()},
        "vehicles": vehicles,
        "unique_routes_by_variant_summed_over_archives": {
            v: sum(k["variants"][v]["edges"][edge]["unique_routes"]
                   for k in per_key.values()) for v in VARIANTS},
        "archives_with_traffic": len(exposed),
        "daily_units_exposed": sum(per_key[key]["daily_units"]
                                   for key in exposed),
        "daily_units_exposed_note": (
            "units whose archive has at least one crossing vehicle in any "
            "variant; the closure-window predicate is not applied"),
        "per_build_key": {
            key: {v: k["variants"][v]["edges"][edge] for v in VARIANTS}
            for key, k in sorted(per_key.items())},
    }
    entry.update(_usable(entry))
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"evidence already exists: {args.out}")

    sampler = common.Sampler()
    bound, spec, resolved = _resolve_archives(args.profile)
    (spec_edge,) = spec.directed_edges
    lineage = _lineage(spec_edge)
    lineage_edges = {spec_edge}
    lineage_edges.update(road["edge_id"]
                         for road in lineage["discovery"]["edges"])
    for edges in lineage["ancestor_spec_edges"].values():
        lineage_edges.update(edges)
    for record in lineage["registrations"].values():
        lineage_edges.update(record["distinct_edges"])
    edges = set(lineage_edges)
    for facts in _network_contract(sorted(lineage_edges)).values():
        edges.update(facts.get("reverse_edges", ()))
    edges = sorted(edges)
    network = _network_contract(edges)
    contract = _contract_checks(spec, Path(resolved[0]["archive"]), edges)
    sampler.sample("lineage")

    pools_seen, per_key = _scan_archives(resolved, edges)
    sampler.sample("archives_scanned")
    catalog_routes = _catalog_routes(edges, pools_seen)
    screen_pool = {item["edge_id"]: item for item in
                   _json(SCREEN)["candidate_pool"]["edges"]}
    context = dict(
        per_key=per_key, network=network,
        sensors=_json("data_in/sensors.json")["sensors"],
        geo={feature["properties"]["id"]: feature["properties"]
             for feature in _json("web/data/network.geojson")["features"]
             if feature["geometry"]["type"] == "LineString"},
        pool=screen_pool,
        discovery_rank={road["edge_id"]: road["rank"]
                        for road in lineage["discovery"]["edges"]},
        lineage_edges=lineage_edges, spec_edge=spec_edge,
        contract=contract, catalog_routes=catalog_routes)
    report = {edge: _edge_entry(edge, **context) for edge in edges}
    archives = {}
    for key, value in sorted(per_key.items()):
        summary = {k: v for k, v in value.items() if k != "variants"}
        summary["variant_sha256"] = {v: value["variants"][v]["file_sha256"]
                                     for v in VARIANTS}
        summary["total_vehicles"] = {
            v: value["variants"][v]["total_vehicles"] for v in VARIANTS}
        archives[key] = summary
    sampler.sample("end")

    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "question": ("why the frozen month spec closes a direction with no "
                     "catalog traffic, and which directions could carry a "
                     "nonzero canary"),
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
        "spec": {"search_id": spec.search_id,
                 "content_key": spec.content_key,
                 "directed_edges": list(spec.directed_edges),
                 "closure_type": spec.closure_type,
                 "policy_status": spec.policy_status},
        "lineage": lineage,
        "contract_checks": contract,
        "catalog_pools": catalog_routes,
        "archives": archives,
        "edges": report,
        "memory": {"samples": sampler.samples},
    }
    record["output_sha256"] = common.digest({
        "lineage": lineage, "contract_checks": contract,
        "catalog_pools": catalog_routes, "archives": archives,
        "edges": report,
    })
    published = common.publish(args.out, record)
    print(json.dumps({
        "spec_edge": spec_edge,
        "profile_spec_history": [
            (h["commit"], h["path"], h["directed_edges"])
            for h in lineage["profile_spec_history"]],
        "discovery": lineage["discovery"]["edges"],
        "contract": {k: v for k, v in contract.items()
                     if not k.endswith("note") and k != "live_demand_gate"},
        "edges": {edge: {
            "from_to": (e["network"].get("from_node"),
                        e["network"].get("to_node")),
            "status": e["measurement_status"],
            "sensors": sorted({r["sensor_id"] for r in e["registry_roles"]}),
            "catalog": {p: c["routes"] for p, c in e["catalog_routes"].items()},
            "vehicles": e["vehicles"],
            "units_exposed": e["daily_units_exposed"],
            "archives": e["archives_with_traffic"],
            "live": e["live_demand_supported"],
            "usable": e["nonzero_canary_usable"],
            "reasons": e["reasons"],
        } for edge, e in report.items()},
        "wall_s": round(sampler.samples[-1]["wall_s"], 1),
        "content_key": published["content_key"],
        "output_sha256": record["output_sha256"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
