"""Month-wide census of the frozen search spec's closure edge.

Question: does ANY calibrated vehicle in the 30 qualified September archives
cross the spec's closure edge, and if none does, why?

The v1 phase-cost benchmark looked at three archives only. This driver reads
all 30 qualified archives, resolved and fully validated through the same
production path the index builder uses, strictly one at a time: each
archive's parsed routes are released before the next archive is opened.

Per build key and variant it counts, with the production parser
(``parse_route_vehicles``):

* vehicles whose route contains the closure edge as an exact token;
* distinct routes containing it; total vehicles; distinct routes;
* the same for the reverse edge, derived from the SUMO network's own
  ``from``/``to`` attributes -- never from the edge-ID string;
* consecutive reverse->closure pairs (a U-turn onto the closure edge).

An independent byte census of each route file cross-checks the parser: a
substring count of zero proves that no token equals the edge ID.

If every archive has zero crossings, the finding is classified from the
recorded facts: the network contract, the validated sensor registry and the
route catalogs the archives record by SHA-256.

Starts no SUMO, builds no demand, writes no index, changes no production
code, and writes only its own append-only evidence file.
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_closure_edge_census_v1"
PRODUCTION_SOURCES = (
    "tools/build_window_cost_index.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/core/closure_calendar.py",
)
CONTRACT_FILES = (
    "sumo/net.net.xml",
    "data_in/sensors.json",
)
VARIANTS = ("q10", "q50", "q90")


def _network_contract(closure_edges: List[str]) -> Dict[str, Any]:
    """Topology of each closure edge, read from the SUMO network itself."""
    root = ET.parse(ROOT / "sumo/net.net.xml").getroot()
    edges = {edge.get("id"): edge for edge in root.iter("edge")
             if edge.get("function") != "internal"
             and not str(edge.get("id", "")).startswith(":")}
    connections = [(c.get("from"), c.get("to")) for c in root.iter("connection")
                   if not str(c.get("from", "")).startswith(":")
                   and not str(c.get("to", "")).startswith(":")]
    result = {}
    for edge_id in closure_edges:
        edge = edges.get(edge_id)
        if edge is None:
            result[edge_id] = {"exists": False}
            continue
        source, target = edge.get("from"), edge.get("to")
        reverse = sorted(key for key, item in edges.items()
                         if item.get("from") == target
                         and item.get("to") == source)
        result[edge_id] = {
            "exists": True,
            "from_node": source,
            "to_node": target,
            "reverse_edges": reverse,
            "reverse_rule": ("edges whose SUMO from/to attributes are this "
                             "edge's to/from; the edge-ID string is not "
                             "parsed"),
            "incoming_connections": sorted({f for f, t in connections
                                            if t == edge_id}),
            "outgoing_connections": sorted({t for f, t in connections
                                            if f == edge_id}),
            "lanes": [{"id": lane.get("id"), "allow": lane.get("allow"),
                       "disallow": lane.get("disallow"),
                       "speed": lane.get("speed")}
                      for lane in edge.iter("lane")],
        }
    return result


def _registry_roles(edge_ids: List[str]) -> List[Dict[str, Any]]:
    registry = json.loads((ROOT / "data_in/sensors.json").read_text(
        encoding="utf-8"))
    roles = []
    for sensor in registry["sensors"]:
        opposite = sensor.get("opposite_direction") or {}
        for edge_id in edge_ids:
            if edge_id in sensor.get("approved_edge_ids", ()):
                roles.append({"edge_id": edge_id,
                              "sensor_id": sensor["sensor_id"],
                              "role": "measured_edge",
                              "measurement_semantics":
                                  sensor.get("measurement_semantics")})
            if opposite.get("edge_id") == edge_id:
                roles.append({"edge_id": edge_id,
                              "sensor_id": sensor["sensor_id"],
                              "role": "opposite_direction",
                              "measurement_status":
                                  opposite.get("measurement_status"),
                              "measured_edges":
                                  list(sensor.get("approved_edge_ids", ()))})
    return roles


def _catalog_census(metadata: Dict[str, Any], edges: List[str],
                    seen: Dict[str, Any]) -> Dict[str, Any]:
    """Count catalog routes containing each edge, bound by routes SHA-256."""
    catalog = metadata.get("candidate_catalog") or {}
    out = {}
    for pool, key in sorted((catalog.get("keys") or {}).items()):
        declared = catalog["artifacts"][pool]["routes_sha256"]
        if key not in seen:
            path = ROOT / "sumo/route_catalog" / key / "catalog.rou.xml"
            entry: Dict[str, Any] = {"catalog_key": key,
                                     "present": path.is_file()}
            if path.is_file():
                data = path.read_bytes()
                routes = [route.get("edges", "").split()
                          for route in ET.fromstring(data).iter("route")]
                entry.update({
                    "routes_sha256": common.file_sha256(path),
                    "routes": len(routes),
                    "edge_routes": {
                        edge: sum(1 for route in routes if edge in route)
                        for edge in edges},
                    "edge_substring_bytes": {
                        edge: data.count(edge.encode()) for edge in edges},
                })
            seen[key] = entry
        entry = seen[key]
        out[pool] = {
            "catalog_key": key,
            "declared_routes_sha256": declared,
            "sha256_matches_declared": entry.get("routes_sha256") == declared,
        }
    return out


def _variant_census(path: Path, closure: str, reverse: List[str]
                    ) -> Dict[str, Any]:
    from traffic_sim.simulation.disruption import parse_route_vehicles

    data = path.read_bytes()
    byte_census = {
        "vehicle_tags": data.count(b"<vehicle "),
        "trip_tags": data.count(b"<trip "),
        "flow_tags": data.count(b"<flow "),
        "embedded_route_tags": data.count(b"<route "),
        "closure_substring": data.count(closure.encode()),
        "reverse_substring": {edge: data.count(edge.encode())
                              for edge in reverse},
    }
    del data
    started = time.perf_counter()
    parsed = parse_route_vehicles(path)
    parse_s = time.perf_counter() - started
    routes = {vehicle[0] for vehicle in parsed}
    closure_vehicles = sum(1 for vehicle in parsed if closure in vehicle[0])
    closure_routes = sorted(" ".join(route) for route in routes
                            if closure in route)
    reverse_counts = {}
    for edge in reverse:
        reverse_counts[edge] = {
            "vehicles": sum(1 for vehicle in parsed if edge in vehicle[0]),
            "unique_routes": sum(1 for route in routes if edge in route),
            "uturn_onto_closure_vehicles": sum(
                1 for vehicle in parsed
                if any(a == edge and b == closure
                       for a, b in zip(vehicle[0], vehicle[0][1:]))),
        }
    result = {
        "total_vehicles": len(parsed),
        "unique_routes": len(routes),
        "closure_vehicles": closure_vehicles,
        "closure_unique_routes": len(closure_routes),
        "closure_route_digest": common.digest(closure_routes),
        "reverse": reverse_counts,
        "byte_census": byte_census,
        "parse_s": parse_s,
    }
    del parsed, routes
    return result


def _resolve_archives(profile: Path):
    """The 30 qualified archives, via the production resolution passes."""
    from tools import build_window_cost_index as builder

    bound = builder._bound_inputs(profile)
    spec = bound["spec"]
    resolver = builder.MonthlyDemandResolverRunner(
        spec, runs_root=bound["runs_root"], build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="wci-closure-edge-census",
        qualified_demand_manifest=bound["qualified_manifest"])
    units_by_key: Dict[str, set] = {}
    required_by_key = {}
    for parent in bound["parents"]:
        for unit_id, _identity, build in builder.daily_unit_records(spec, parent):
            required = resolver._required(build())
            required_by_key.setdefault(required.build_key, required)
            units_by_key.setdefault(required.build_key, set()).add(str(unit_id))
    index = builder._archives_for_build_key(bound["runs_root"])
    resolved = []
    for build_key, required in sorted(required_by_key.items()):
        matches = builder.find_demand_archives(
            bound["runs_root"], required,
            qualified_manifest=bound["qualified_manifest"],
            _archive_index=index)
        if not matches:
            raise SystemExit(f"no qualified archive for build key {build_key}")
        resolved.append({
            "build_key": build_key,
            "archive": str(Path(matches[0]["archive"]).resolve()),
            "archive_content_key": matches[0].get("archive_content_key"),
            "daily_units": len(units_by_key[build_key]),
        })
    return bound, spec, resolved


def _classify(contract, roles, totals, catalogs, closure, reverse):
    facts = contract[closure]
    edge_valid = bool(facts.get("exists")) and bool(
        facts.get("incoming_connections"))
    catalog_closure_routes = sum(
        entry.get("edge_routes", {}).get(closure, 0)
        for entry in catalogs.values())
    catalog_closure_bytes = sum(
        entry.get("edge_substring_bytes", {}).get(closure, 0)
        for entry in catalogs.values())
    catalog_reverse_routes = {
        edge: sum(entry.get("edge_routes", {}).get(edge, 0)
                  for entry in catalogs.values()) for edge in reverse}
    reverse_everywhere = bool(reverse) and all(
        all(value > 0 for value in archive_reverse.values())
        for archive_reverse in totals["reverse_vehicles_by_archive"])
    unmeasured_opposite = [role for role in roles
                           if role["edge_id"] == closure
                           and role["role"] == "opposite_direction"]
    if totals["closure_vehicles"] > 0:
        category = "traffic_present"
    elif not edge_valid:
        category = "wrong_edge"
    elif not reverse_everywhere:
        category = "demand_archives_lack_the_street"
    elif catalog_closure_routes == 0:
        category = "catalog_coverage"
    else:
        category = "calibration_selects_no_candidate"
    return {
        "category": category,
        "rule": ("traffic_present if any archive has a crossing; else "
                 "wrong_edge if the edge is absent or has no incoming "
                 "connection; else demand_archives_lack_the_street if the "
                 "reverse (same street) edge is empty in any archive; else "
                 "catalog_coverage if no route in the recorded catalogs "
                 "contains the edge; else calibration_selects_no_candidate"),
        "edge_valid_and_reachable": edge_valid,
        "closure_is_registry_unmeasured_opposite": unmeasured_opposite,
        "reverse_edge_carries_traffic_in_every_archive": reverse_everywhere,
        "catalog_routes_containing_closure": catalog_closure_routes,
        "catalog_closure_substring_bytes": catalog_closure_bytes,
        "catalog_routes_containing_reverse": catalog_reverse_routes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"evidence already exists: {args.out}")

    from traffic_sim.simulation.deterministic_disruption import ArchiveInputs

    sampler = common.Sampler()
    system_before = common.system_memory()
    bound, spec, resolved = _resolve_archives(args.profile)
    closures = sorted(spec.directed_edges)
    if len(closures) != 1:
        raise SystemExit(f"expected one closure edge, got {closures}")
    closure = closures[0]
    contract = _network_contract(closures)
    reverse = contract[closure].get("reverse_edges", [])
    roles = _registry_roles(closures + list(reverse))
    sampler.sample("resolved", archives=len(resolved))

    catalogs: Dict[str, Any] = {}
    archives = []
    for position, item in enumerate(resolved):
        archive = Path(item["archive"])
        inputs = ArchiveInputs.from_archive(archive)
        metadata = json.loads((archive / "demand_meta.json").read_text(
            encoding="utf-8"))
        catalog_refs = _catalog_census(metadata, [closure] + list(reverse),
                                       catalogs)
        del metadata
        variants = {}
        for variant in VARIANTS:
            path = inputs.variant_paths[variant]
            variants[variant] = {
                "file": path.name,
                "sha256": inputs.variant_sha256[variant],
                **_variant_census(path, closure, reverse),
            }
        del inputs
        gc.collect()
        memory = sampler.sample(f"after_archive_{position + 1}")
        archives.append({
            **item,
            "variants": variants,
            "catalogs": catalog_refs,
            "resident_bytes_after_release": memory["resident_bytes"],
            "phys_footprint_bytes_after_release":
                memory["phys_footprint_bytes"],
        })

    totals = {
        "archives": len(archives),
        "variant_files": sum(len(a["variants"]) for a in archives),
        "total_vehicles": sum(v["total_vehicles"] for a in archives
                              for v in a["variants"].values()),
        "closure_vehicles": sum(v["closure_vehicles"] for a in archives
                                for v in a["variants"].values()),
        "closure_substring_bytes": sum(
            v["byte_census"]["closure_substring"] for a in archives
            for v in a["variants"].values()),
        "archives_with_closure_traffic": sorted(
            a["build_key"] for a in archives
            if any(v["closure_vehicles"] for v in a["variants"].values())),
        "reverse_vehicles": {
            edge: sum(v["reverse"][edge]["vehicles"] for a in archives
                      for v in a["variants"].values()) for edge in reverse},
        "reverse_vehicles_by_archive": [
            {edge: sum(v["reverse"][edge]["vehicles"]
                       for v in a["variants"].values()) for edge in reverse}
            for a in archives],
        "uturn_onto_closure_vehicles": sum(
            v["reverse"][edge]["uturn_onto_closure_vehicles"]
            for a in archives for v in a["variants"].values()
            for edge in reverse),
        "parsed_equals_vehicle_tags": all(
            v["total_vehicles"] == v["byte_census"]["vehicle_tags"]
            for a in archives for v in a["variants"].values()),
    }
    classification = _classify(contract, roles, totals, catalogs, closure,
                               reverse)
    sampler.sample("end")
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "question": ("does any calibrated vehicle in the 30 qualified "
                     "archives cross the frozen spec's closure edge"),
        "driver": common.driver_binding(Path(__file__)),
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "contract_files": common.source_bindings(CONTRACT_FILES),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "profile": str(args.profile),
        "profile_sha256": common.file_sha256(args.profile),
        "spec": {"search_id": spec.search_id,
                 "content_key": spec.content_key,
                 "directed_edges": closures},
        "qualified_demand_manifest": bound["qualified_ref"],
        "archive_identities_digest": common.digest([
            {"build_key": a["build_key"],
             "archive_content_key": a["archive_content_key"],
             "variant_sha256": {k: v["sha256"]
                                for k, v in a["variants"].items()}}
            for a in archives]),
        "network_contract": contract,
        "registry_roles": roles,
        "catalogs": catalogs,
        "archives": archives,
        "totals": totals,
        "classification": classification,
        "memory": {"samples": sampler.samples,
                   "system_before": system_before,
                   "system_after": common.system_memory()},
        "wall_s": sampler.samples[-1]["wall_s"],
    }
    # Timings and memory vary run to run; the output hash binds only the
    # deterministic counts, so a reproduction must match it exactly.
    record["output_sha256"] = common.digest({
        "archives": [{
            "build_key": a["build_key"],
            "archive_content_key": a["archive_content_key"],
            "daily_units": a["daily_units"],
            "catalogs": a["catalogs"],
            "variants": {name: {k: v for k, v in counts.items()
                                if k != "parse_s"}
                         for name, counts in a["variants"].items()},
        } for a in archives],
        "totals": totals,
        "classification": classification,
    })
    published = common.publish(args.out, record)
    print(json.dumps({
        "closure": closure, "reverse": reverse,
        "totals": {k: v for k, v in totals.items()
                   if k != "reverse_vehicles_by_archive"},
        "classification": classification,
        "wall_s": round(record["wall_s"], 1),
        "max_resident_after_release_mb": round(max(
            a["resident_bytes_after_release"] for a in archives) / 1e6, 1),
        "content_key": published["content_key"],
        "output_sha256": record["output_sha256"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
