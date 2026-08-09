#!/usr/bin/env python3
"""Audit candidate-route and junction-movement support behind a LOSO sweep.

Work package 1 (read-only) of
``docs/plans/LOSO_POOL_PICKER_IMPROVEMENT_PLAN_2026-08-08.md``.

The corrected six-seed baseline fails TAG with a stable, junction-shaped
error.  This tool answers the prior question — what route support did the
picker actually have at each held location, and how is the shared
133/134/2276 junction supported — WITHOUT changing candidate generation, the
PFE, or any published artifact.

Each seed's candidate pool is recovered from the exact-input candidate cache
by its recorded SHA-256, so the audit reads the very pools the sweep used
instead of regenerating approximations of them (regeneration would also
overwrite the live pool, which is bound to the published report).

Outputs ``validation/loso_route_support_audit_v1.json``.  Every number is a
diagnostic measurement of pool/network/artifact contents; nothing here is a
turn ratio, a quality verdict, or a released result.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demand.intake import load_sensor_edges  # noqa: E402
from traffic_sim.confidence import route_support as rs  # noqa: E402
from traffic_sim.core.fingerprint import sha256_file  # noqa: E402
from traffic_sim.intake.sensors import load_registry  # noqa: E402

SCHEMA_VERSION = 1
KIND = "loso_route_support_audit"
JUNCTION_NODE = "26355153"
DEFAULT_MANIFEST = ROOT / "runs/loso-corrected-v2-20260808/manifest.json"
DEFAULT_OUTPUT = ROOT / "validation/loso_route_support_audit_v1.json"
CACHE_ROOT = ROOT / "sumo/candidate_cache"

FIELD_SEMANTICS = {
    "component_edges": "measurement",
    "measurement_semantics": "measurement",
    "legal_movements": "derived_exact",
    "candidate_pool_sha256": "derived_exact",
    "candidate_metadata_sha256": "derived_exact",
    "raw_candidate_legs": "diagnostic",
    "variables": "diagnostic",
    "effective_alternatives_per_od_group": "diagnostic",
    "gate_pair_composition": "diagnostic",
    "cost_stretch_vs_fastest": "diagnostic",
    "variables_crossing_no_other_station": "diagnostic",
    "selected_movement_flows": "diagnostic",
    "simulated_leg_flows": "diagnostic",
    "pairwise_jaccard": "diagnostic",
    "path_size": "model_assumption",
    "interpretation_rules": "decision",
}


def _repo_path(path: Path) -> str:
    """Repository-relative path for evidence, whatever form the CLI got."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def index_candidate_cache(cache_root: Path) -> dict[str, list[str]]:
    """Map a stored pool's SHA-256 to the cache entries that hold it.

    The cache manifest already records each output's digest, so the index
    costs a few hundred small reads instead of re-hashing gigabytes.
    """
    index: dict[str, list[str]] = {}
    if not cache_root.is_dir():
        return index
    for entry in sorted(cache_root.iterdir()):
        manifest = entry / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            stored = json.loads(manifest.read_text()).get("outputs") or {}
        except (OSError, json.JSONDecodeError):
            continue
        digest = (stored.get("candidates.rou.xml") or {}).get("sha256")
        if digest:
            index.setdefault(digest, []).append(entry.name)
    return index


def resolve_pool(seed_pool_sha: str, cache_index: dict[str, list[str]],
                 cache_root: Path, live_pool: Path) -> tuple[Path, Path, str]:
    """Return ``(rou, meta, provenance)`` for one seed's exact pool."""
    if sha256_file(live_pool) == seed_pool_sha:
        return (live_pool,
                live_pool.with_name("candidates.meta.json"),
                "live worktree pool")
    for entry in cache_index.get(seed_pool_sha, []):
        rou = cache_root / entry / "candidates.rou.xml"
        meta = cache_root / entry / "candidates.meta.json"
        if rou.is_file() and meta.is_file():
            return rou, meta, f"candidate cache entry {entry}"
    raise FileNotFoundError(
        f"candidate pool {seed_pool_sha[:12]} is not in the live worktree or "
        "the candidate cache; it cannot be audited without regenerating it")


def build_locations(sensor_edges: dict[str, list[str]],
                    registry) -> list[rs.Location]:
    """One row per physically observable station, 107 aggregated once."""
    return [
        rs.Location(
            station=str(station),
            measurement_semantics=registry[str(station)].measurement_semantics,
            component_edges=tuple(sorted(edges)),
        )
        for station, edges in sorted(sensor_edges.items())
    ]


def audit_seed(seed: int, rou: Path, meta: Path, provenance: str,
               locations: list[rs.Location], network: rs.Network,
               movements: list[dict], cost_model: rs.CostModel,
               junction_measured: dict[str, str],
               junction_legs: list[str]) -> tuple[dict, dict]:
    legs = rs.load_pool(rou, meta)
    variables = rs.dedup_variables(legs)
    per_location = [
        rs.location_support(location, variables, locations, cost_model)
        for location in locations
    ]
    crossing_hashes = {
        location.station: rs.geometry_hashes(variables, location.component_edges)
        for location in locations
    }
    support = rs.movement_support(movements, variables, locations)
    # The system the picker actually faces is the one spanned by movements the
    # pool can serve; a legal movement with no candidate route is not a degree
    # of freedom for this build, so it is reported separately.
    supported = [row for row in support if row["variables"] > 0]
    record = {
        "seed": seed,
        "candidate_pool_sha256": sha256_file(rou),
        "candidate_metadata_sha256": sha256_file(meta),
        "pool_provenance": provenance,
        "raw_candidate_legs": len(legs),
        "variables": len(variables),
        "unique_edge_sequences": len({v.geometry_key for v in variables}),
        "orphan_tour_legs": sum(1 for leg in legs if leg.tour_partner_dropped),
        "locations": per_location,
        "junction_movement_support": support,
        "junction_movements_with_support": len(supported),
        "junction_movements_without_support": len(support) - len(supported),
        "junction_identifiability_over_supported_movements": {
            "all_stations_measured": rs.junction_identifiability(
                supported, junction_measured, junction_legs, held=None),
            **{
                f"holding_out_{station}": rs.junction_identifiability(
                    supported, junction_measured, junction_legs, held=station)
                for station in sorted(set(junction_measured.values()))
            },
        },
    }
    return record, crossing_hashes


def measured_leg_totals(run_root: Path, runs: list[dict],
                        legs: list[str]) -> dict[str, float]:
    """Read each measured junction leg's observed daily total from the sweep.

    The sweep's own LOSO reports already carry the held station's measured
    total for exactly the evaluated window, so this reuses that measurement
    instead of re-deriving a second, possibly differently-windowed one.
    """
    totals: dict[str, float] = {}
    for run in runs:
        report = run_root / f"seed-{run['seed']}" / "loso_report.json"
        if not report.is_file():
            continue
        stations = json.loads(report.read_text()).get("stations", {})
        for station in stations.values():
            for entry in station.get("edges", {}).values():
                components = entry.get("component_edges", [])
                if len(components) == 1 and components[0] in legs:
                    totals[components[0]] = float(entry["measured_total"])
        if totals:
            break
    return totals


def junction_conservation(measured_totals: dict[str, float], incoming: list[str],
                          outgoing: list[str],
                          measured_edges: dict[str, str]) -> dict:
    """State what conservation alone proves about the unmeasured legs.

    Total inflow equals total outflow at a junction, so inflow on the
    unmeasured legs is at least the measured outflow minus the measured
    inflow.  That is an EXACT consequence of the counts and the topology; it
    assumes no parking, driveway or storage inside the node, which is stated
    rather than hidden.
    """
    measured_in = {edge: value for edge, value in measured_totals.items()
                   if edge in incoming}
    measured_out = {edge: value for edge, value in measured_totals.items()
                    if edge in outgoing}
    inflow = sum(measured_in.values())
    outflow = sum(measured_out.values())
    return {
        "measured_incoming_legs": dict(sorted(measured_in.items())),
        "measured_outgoing_legs": dict(sorted(measured_out.items())),
        "unmeasured_incoming_legs": [edge for edge in incoming
                                     if edge not in measured_edges],
        "unmeasured_outgoing_legs": [edge for edge in outgoing
                                     if edge not in measured_edges],
        "measured_inflow_total": round(inflow, 3),
        "measured_outflow_total": round(outflow, 3),
        "implied_minimum_unmeasured_inflow": round(max(0.0, outflow - inflow), 3),
        "implied_minimum_unmeasured_inflow_share_of_measured_outflow": round(
            max(0.0, outflow - inflow) / outflow, 4) if outflow else None,
        "assumption": (
            "node conservation with no parking, driveway or storage inside "
            "the junction"),
    }


def fold_artifact_section(seed: int, sumo_dir: Path, locations: list[rs.Location],
                          movements: list[dict], legs: list[str],
                          n_quarters: int,
                          baseline_routes: Path | None = None) -> dict | None:
    """Read the published fold artifacts for ONE seed, if they are on disk.

    LOSO overwrites ``sumo/loso_*.rou.xml`` per seed, so only the last seed's
    folds survive.  They are read here as diagnostic replay of that seed only
    and are labelled as such; they are never presented as sweep-wide evidence.
    """
    section: dict = {"seed": seed, "folds": {}}
    found = False
    baseline_movements: dict[str, int] = {}
    baseline_legs: dict[str, int] = {}
    if baseline_routes is not None and Path(baseline_routes).is_file():
        # The fully-measured build is the reference every fold is compared
        # with: it is the same pool and the same solver with one more
        # constraint, so a fold/baseline ratio isolates what losing that
        # constraint did.
        reference = rs.selected_movement_flows(
            Path(baseline_routes), movements, legs, n_quarters)
        baseline_movements = {key: sum(counts)
                              for key, counts in reference["by_movement"].items()}
        baseline_legs = {key: sum(counts)
                         for key, counts in reference["by_leg"].items()}
        section["baseline_all_stations_measured"] = {
            "route_file": _repo_path(Path(baseline_routes)),
            "route_file_sha256": sha256_file(Path(baseline_routes)),
            "selected_vehicles": reference["vehicles"],
            "selected_movement_totals": baseline_movements,
            "selected_leg_totals": baseline_legs,
        }
    for location in locations:
        rou = sumo_dir / f"loso_{location.station}.rou.xml"
        edgedata = sumo_dir / f"loso_ed_{location.station}.xml"
        if not rou.is_file():
            continue
        found = True
        selected = rs.selected_movement_flows(rou, movements, legs, n_quarters)
        fold = {
            "route_file": _repo_path(rou),
            "route_file_sha256": sha256_file(rou),
            "selected_vehicles": selected["vehicles"],
            "selected_movement_by_quarter": selected["by_movement"],
            "selected_movement_totals": {
                key: sum(counts) for key, counts in selected["by_movement"].items()},
            "selected_leg_totals": {
                key: sum(counts) for key, counts in selected["by_leg"].items()},
            "selected_leg_by_quarter": selected["by_leg"],
        }
        if edgedata.is_file():
            fold["edge_data_file"] = _repo_path(edgedata)
            fold["edge_data_file_sha256"] = sha256_file(edgedata)
            simulated = rs.simulated_edge_flows(edgedata, legs, n_quarters)
            fold["simulated_leg_totals"] = {
                edge: round(sum(values), 3) for edge, values in simulated.items()}
            fold["simulated_leg_by_quarter"] = {
                edge: [round(value, 3) for value in values]
                for edge, values in simulated.items()}
            fold["planned_minus_simulated_leg_totals"] = {
                edge: round(sum(fold["selected_leg_by_quarter"][edge])
                            - sum(simulated[edge]), 3)
                for edge in legs
            }
        if baseline_movements:
            fold["movement_ratio_vs_baseline"] = {
                key: round(value / baseline_movements[key], 4)
                for key, value in fold["selected_movement_totals"].items()
                if baseline_movements.get(key)
            }
            fold["leg_ratio_vs_baseline"] = {
                key: round(value / baseline_legs[key], 4)
                for key, value in fold["selected_leg_totals"].items()
                if baseline_legs.get(key)
            }
            fold["vehicle_ratio_vs_baseline"] = round(
                fold["selected_vehicles"]
                / section["baseline_all_stations_measured"]["selected_vehicles"], 4)
        section["folds"][location.station] = fold
    return section if found else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--net", type=Path, default=ROOT / "sumo/net.net.xml")
    parser.add_argument("--sensors", type=Path, default=ROOT / "data_in/sensors.json")
    parser.add_argument("--cache-root", type=Path, default=CACHE_ROOT)
    parser.add_argument("--live-pool", type=Path,
                        default=ROOT / "sumo/candidates.rou.xml")
    parser.add_argument("--junction", default=JUNCTION_NODE)
    parser.add_argument("--seeds", type=int, nargs="*",
                        help="audit only these seeds (default: every seed in "
                             "the manifest)")
    parser.add_argument("--fold-artifacts", type=Path,
                        help="directory holding loso_<station>.rou.xml from ONE "
                             "seed's folds (usually sumo/)")
    parser.add_argument("--fold-seed", type=int,
                        help="the seed those fold artifacts belong to")
    parser.add_argument("--baseline-routes", type=Path,
                        default=ROOT / "sumo/calibrated.rou.xml",
                        help="the fully-measured calibrated routes the folds "
                             "are compared against")
    parser.add_argument("--quarters", type=int, default=96)
    parser.add_argument("--direction-priors", type=Path,
                        default=ROOT / "sumo/prior_flows.json")
    parser.add_argument("--assignment-priors", type=Path,
                        default=ROOT / "sumo/assignment_priors.json")
    args = parser.parse_args()

    manifest_path = args.manifest if args.manifest.is_absolute() \
        else ROOT / args.manifest
    manifest = json.loads(manifest_path.read_text())
    runs = manifest["runs"]
    if args.seeds:
        wanted = set(args.seeds)
        runs = [run for run in runs if run["seed"] in wanted]
    if not runs:
        print("no matching seeds in the manifest")
        return 1

    network = rs.load_network(args.net)
    movements = rs.legal_movements(network, args.junction)
    incoming = list(network.incoming(args.junction))
    outgoing = list(network.outgoing(args.junction))
    cost_model = rs.CostModel(network)

    sensor_edges = load_sensor_edges()
    registry = load_registry(args.sensors)
    locations = build_locations(sensor_edges, registry)
    measured_edges = {edge: location.station
                      for location in locations for edge in location.component_edges}
    junction_legs = incoming + outgoing
    junction_measured = {edge: station for edge, station in measured_edges.items()
                         if edge in junction_legs}

    measured_totals = measured_leg_totals(manifest_path.parent, runs, junction_legs)
    conservation = junction_conservation(
        measured_totals, incoming, outgoing, measured_edges)
    identifiability = {
        "all_stations_measured": rs.junction_identifiability(
            movements, junction_measured, junction_legs, held=None),
        **{
            f"holding_out_{station}": rs.junction_identifiability(
                movements, junction_measured, junction_legs, held=station)
            for station in sorted(set(junction_measured.values()))
        },
    }
    direction_priors = json.loads(args.direction_priors.read_text())["edges"] \
        if args.direction_priors.is_file() else {}
    assignment_flows = json.loads(args.assignment_priors.read_text()).get("flows", {}) \
        if args.assignment_priors.is_file() else {}

    cache_index = index_candidate_cache(args.cache_root)
    per_seed = []
    crossing_by_seed: dict[str, dict[str, set[str]]] = {}
    for run in runs:
        seed = run["seed"]
        rou, meta, provenance = resolve_pool(
            run["candidate_pool_sha256"], cache_index, args.cache_root,
            args.live_pool)
        print(f"seed {seed}: auditing pool {run['candidate_pool_sha256'][:12]} "
              f"({provenance})", flush=True)
        record, hashes = audit_seed(seed, rou, meta, provenance, locations,
                                    network, movements, cost_model,
                                    junction_measured, junction_legs)
        per_seed.append(record)
        crossing_by_seed[str(seed)] = hashes

    fold_section = None
    if args.fold_artifacts:
        fold_seed = args.fold_seed or runs[-1]["seed"]
        print(f"reading fold artifacts for seed {fold_seed} …", flush=True)
        fold_section = fold_artifact_section(
            fold_seed, args.fold_artifacts, locations, movements,
            junction_legs, args.quarters, args.baseline_routes)

    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "evidence_class": "diagnostic",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "plan": "docs/plans/LOSO_POOL_PICKER_IMPROVEMENT_PLAN_2026-08-08.md",
        "inputs": {
            "manifest_path": _repo_path(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "network_sha256": sha256_file(args.net),
            "sensors_sha256": sha256_file(args.sensors),
            "direction_priors_sha256": sha256_file(args.direction_priors),
            "assignment_priors_sha256": sha256_file(args.assignment_priors),
            "audit_tool_sha256": sha256_file(Path(__file__)),
            "route_support_module_sha256": sha256_file(
                ROOT / "traffic_sim/confidence/route_support.py"),
            "candidate_cache_entries_indexed": sum(
                len(value) for value in cache_index.values()),
        },
        "field_semantics": dict(FIELD_SEMANTICS),
        "station_locations": [
            {
                "station": location.station,
                "measurement_semantics": location.measurement_semantics,
                "component_edges": list(location.component_edges),
            }
            for location in locations
        ],
        "junction": {
            "node": args.junction,
            "incoming_legs": incoming,
            "outgoing_legs": outgoing,
            "legs_total": len(incoming) + len(outgoing),
            "measured_legs": {edge: station for edge, station
                              in sorted(measured_edges.items())
                              if edge in junction_legs},
            "unmeasured_legs": [edge for edge in junction_legs
                                if edge not in measured_edges],
            "legal_movements": movements,
            "legal_movements_total": len(movements),
            "legal_movements_excluding_u_turns": sum(
                1 for movement in movements if not movement["u_turn"]),
            "measured_leg_daily_totals": measured_totals,
            "conservation": conservation,
            "identifiability": identifiability,
            "production_constraint_coverage": rs.constraint_coverage(
                junction_legs, direction_priors, assignment_flows, measured_edges),
            "production_constraint_coverage_scope": (
                "current fully-measured production artifacts; not the active "
                "constraint set of any LOSO fold"),
        },
        "per_seed": per_seed,
        "across_seed_stability": rs.across_seed_stability(crossing_by_seed),
        "fold_artifacts": fold_section,
        "interpretation_rules": [
            "Candidate frequency on a movement is ROUTE SUPPORT, never an "
            "observed or claimed turn ratio.",
            "A two-way-total station is one location; its directional rows "
            "are diagnostic only.",
            "Effective alternatives is exp(entropy) over normalized path-size "
            "weights, so near-identical routes do not count as independent "
            "choice.",
            "Fold artifacts describe one seed's published folds only.",
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
