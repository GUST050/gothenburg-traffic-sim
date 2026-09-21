#!/usr/bin/env python3
"""Freeze one effect-eligible month case, and prove it again later.

This is a registration and preflight, never a build. It takes the census
evidence of ``closure_effect_eligibility_q50_v2`` (which parses every qualified
archive variant and catalog pool once), re-derives the verdict of every
candidate from that inventory rather than trusting the census rows, applies
the unchanged structural order to the eligible candidates and freezes the
first one as the month case.

The registration binds everything a later guarded build must still find
unchanged: the policy version, the chosen directed edge, the complete
candidate list and its digest, each candidate's structural and effect
verdict, the qualified-demand manifest, every archive content key and
variant hash, the catalog keys and hashes, the census content key, the
source bytes and the month population.

``verify_registration`` re-proves all of it from the files on disk, and the
guarded runner refuses to start when anything differs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
import tools.closure_effect_eligibility as cee  # noqa: E402
import tools.cost_ordered_benchmark as bench  # noqa: E402
from traffic_sim.core.contracts import ClosureSearchSpec  # noqa: E402
from traffic_sim.core.fingerprint import sha256_file  # noqa: E402
from traffic_sim.simulation.independent_daily import (  # noqa: E402
    daily_unit_records,
    population_of,
)
from traffic_sim.simulation.window_cost_index import (  # noqa: E402
    publish_new_file,
)

SCHEMA = "wci_month_case_registration_q50_v2"
CENSUS_SCHEMA = "closure_effect_inventory_evidence_q50_v2"
SPEC_SCHEMA = "wci_effect_canary_spec_v2"
FROZEN_ZERO_SPEC = "validation/subhour_monthly_search_profile_spec_v1.json"
ORDER_RULE = ("effect-eligible candidates only, then the unchanged "
              "structural survivability order (dist_sensor_m, edge_id); "
              "the first one wins, never the busiest")
SOURCE_BINDINGS = (
    "tools/closure_effect_eligibility.py",
    "tools/cost_ordered_benchmark.py",
    "tools/build_window_cost_index.py",
    "tools/freeze_wci_month_case.py",
    "tools/guarded_wci_build.py",
    "tools/wci_guarded_child.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/metadata.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/window_cost_index.py",
)
VARIANT_FILE = {"q50": "calibrated.rou.xml", "q10": "calibrated_v1.rou.xml",
                "q90": "calibrated_v2.rou.xml"}
REQUIRED_VARIANTS = ("q50",)


def _digest(payload: Any) -> str:
    """One canonical digest for the whole policy, from the policy module."""
    return cee._digest(payload)


def _body(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in record.items()
            if key != "content_key"}


def inventory_of(census: Mapping[str, Any]) -> cee.Inventory:
    """The census inventory, refused unless its content key describes it."""
    record = census["inventory"]
    inventory = cee.Inventory(
        candidates=tuple(record["candidates"]), archives=record["archives"],
        catalogs=record["catalogs"], crossings=record["crossings"],
        catalog_routes=record["catalog_routes"], parses=record["parses"])
    if inventory.content_key != record.get("content_key") or (
            inventory.content_key != census.get("inventory_content_key")):
        raise ValueError("census inventory content key does not describe it")
    return inventory


def rederive(census: Mapping[str, Any], *,
             network_edges: Optional[frozenset] = None) -> Dict[str, Any]:
    """Re-run the policy over the census inventory and order the result.

    The census rows are not trusted: every candidate is assessed again from
    the inventory, and the two must agree.
    """
    inventory = inventory_of(census)
    if network_edges is None:
        network_edges, _catalog_root = bench._effect_sources(ROOT)
    units = {str(key): int(value) for key, value
             in (census["daily_units_by_build_key"] or {}).items()}
    keys = sorted(inventory.archives)
    verdicts = {edge: cee.assess(edge, inventory,
                                 network_edges=network_edges,
                                 required_build_keys=keys, daily_units=units)
                for edge in inventory.candidates}
    rows = []
    disagreements = []
    for row in census["rows"]:
        edge = str(row["edge_id"])
        verdict = verdicts[edge]
        if (bool(row["effect_eligible"]) != verdict.effect_eligible
                or list(row["reason_codes"]) != list(verdict.reason_codes)):
            disagreements.append(edge)
        rows.append({
            "edge_id": edge,
            "structural_rank": int(row["structural_rank"]),
            "structurally_survivable": bool(row["structurally_survivable"]),
            "measurement_status": row.get("measurement_status"),
            "effect_eligible": verdict.effect_eligible,
            "reason_codes": list(verdict.reason_codes),
            "summary": dict(verdict.summary),
        })
    ordered = cee.eligible_in_order(
        rows, lambda item: item,
        order_key=lambda item: item["structural_rank"])
    return {"rows": rows, "eligible_in_order": ordered,
            "disagreements": disagreements, "inventory": inventory,
            "daily_units_by_build_key": units}


def _chosen_requirements(chosen: Mapping[str, Any]) -> List[str]:
    """What the frozen case must show beyond being first in order."""
    summary = chosen["summary"]
    problems = []
    if not chosen["effect_eligible"] or chosen["reason_codes"] != ["eligible"]:
        problems.append("chosen case is not effect-eligible")
    if not chosen["structurally_survivable"]:
        problems.append("chosen case is not structurally survivable")
    pools = summary.get("catalog_routes") or {}
    if not pools or any(count <= 0 for count in pools.values()):
        problems.append(f"a catalog pool has no route support: {pools}")
    crossings = summary.get("crossings") or {}
    if sorted(crossings) != sorted(REQUIRED_VARIANTS) or any(
            count <= 0 for count in crossings.values()):
        problems.append(f"a variant has no crossing vehicle: {crossings}")
    if summary.get("archives_with_crossings") != summary.get("archives"):
        problems.append("not every archive has crossing traffic")
    if not (summary.get("daily_units_with_crossings") or 0) > 0:
        problems.append("no daily unit has crossing traffic")
    return problems


def _archive_bindings(inventory: cee.Inventory) -> Dict[str, Any]:
    archives = {}
    for key, record in sorted(inventory.archives.items()):
        variants = record["variants"]
        archives[key] = {
            "archive": record["archive"],
            "archive_content_key": record["content_key"],
            "demand_meta_sha256": record["demand_meta_sha256"],
            "variants": {variant: variants[variant]["validated_sha256"]
                         for variant in REQUIRED_VARIANTS},
        }
    return archives


def _catalog_bindings(inventory: cee.Inventory) -> Dict[str, Any]:
    catalogs = {}
    for key, entry in sorted(inventory.catalogs.items()):
        for pool in entry["pools"]:
            catalogs[pool] = {
                "catalog_key": key, "path": entry["path"],
                "routes_sha256": entry["sha256"],
                "metadata_sha256": entry["metadata_sha256"],
                "declared_candidates": entry["declared_candidates"],
            }
    return catalogs


def _nonzero(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    """Bounded canary evidence that this edge prices above zero."""
    records = []
    for path in paths:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        runs = record.get("runs")
        status = record.get("status")
        if runs is None:  # guarded run evidence
            outcome = record["outcome"]
            keys = record["bindings"]["build_keys"]
            totals = {str(len(keys)): outcome["telemetry"][
                "affected_vehicles_total"]}
            status = outcome["state"]
        elif isinstance(runs, dict):  # A/B evidence: runs per key count
            totals = {count: items[0]["summary"]["affected_vehicles_total"]
                      for count, items in runs.items()}
        else:  # canary evidence: a list of runs
            totals = {str(len(item["build_keys"])):
                      item["affected_vehicles_total"] for item in runs}
        records.append({
            "path": str(Path(path).resolve()),
            "schema": record.get("schema"),
            "content_key": record.get("content_key"),
            "status": status,
            "affected_vehicles_by_build_keys": totals,
        })
    return records


def drift_files(record: Mapping[str, Any]) -> Dict[str, str]:
    """Small files whose bytes a guarded run must find unchanged."""
    files = {str(ROOT / path): digest for path, digest
             in (record.get("source_bindings") or {}).items()}
    for binding in (record["qualified_demand_manifest"],
                    record["frozen_zero_spec"], record["census"],
                    record["canary_spec"]):
        files[str(Path(binding["path"]))] = binding["sha256"]
    for catalog in record["catalogs"].values():
        files[catalog["path"]] = catalog["routes_sha256"]
        files[str(Path(catalog["path"]).with_name("catalog.meta.json"))] = (
            catalog["metadata_sha256"])
    return files


def drift_archives(record: Mapping[str, Any]) -> Dict[str, Any]:
    """The archive files the raw phase reads, with their bound hashes."""
    return {key: {"archive": binding["archive"],
                  "files": {"demand_meta.json": binding["demand_meta_sha256"],
                            **{VARIANT_FILE[variant]: digest for variant,
                               digest in binding["variants"].items()}}}
            for key, binding in record["archives"].items()}


def verify_registration(record: Mapping[str, Any], *,
                        network_edges: Optional[frozenset] = None
                        ) -> List[str]:
    """Re-prove the registration from disk; empty when it still holds.

    ``network_edges`` exists so a test can prove the same code against a
    small synthetic network; production leaves it None and reads the SUMO
    network the selection itself used.
    """
    problems: List[str] = []
    if record.get("schema") != SCHEMA:
        return [f"unexpected schema: {record.get('schema')}"]
    if _digest(_body(record)) != record.get("content_key"):
        problems.append("registration content key does not describe it")
    try:
        if cee.policy_of(record) != cee.POLICY:
            problems.append("unexpected case-selection policy")
    except ValueError as error:
        return problems + [str(error)]
    for path, digest in sorted(drift_files(record).items()):
        if sha256_file(Path(path)) != digest:
            problems.append(f"bound file changed: {path}")
    if problems:
        return problems
    census = json.loads(Path(record["census"]["path"]).read_text(
        encoding="utf-8"))
    if census.get("content_key") != record["census"]["content_key"]:
        return ["census content key changed"]
    return _verify_selection(record, census, network_edges)


def _verify_selection(record: Mapping[str, Any], census: Mapping[str, Any],
                      network_edges: Optional[frozenset] = None
                      ) -> List[str]:
    problems = []
    derived = rederive(census, network_edges=network_edges)
    if derived["disagreements"]:
        problems.append("census rows disagree with the policy: "
                        f"{derived['disagreements']}")
    if cee.verify_inventory(census["inventory"]):
        problems.append("census inventory no longer verifies")
    candidates = [row["edge_id"] for row in derived["rows"]]
    if candidates != list(record["candidates"]):
        problems.append("candidate list changed")
    if _digest(candidates) != record["candidate_list_digest"]:
        problems.append("candidate list digest changed")
    ordered = [row["edge_id"] for row in derived["eligible_in_order"]]
    if ordered != list(record["eligible_in_order"]):
        problems.append("eligible order changed")
    if not ordered or ordered[0] != record["chosen_edge"]:
        problems.append("the chosen edge is no longer the policy's first")
    elif not problems:
        problems.extend(_chosen_requirements(derived["eligible_in_order"][0]))
    spec = ClosureSearchSpec.from_dict(record["spec"])
    if spec.content_key != record["spec_content_key"]:
        problems.append("spec content key does not describe the spec")
    if list(spec.directed_edges) != [record["chosen_edge"]]:
        problems.append("spec does not close the chosen edge")
    return problems


def build_registration(*, census_path: Path, canary_spec_path: Path,
                       nonzero_evidence: Sequence[Path]) -> Dict[str, Any]:
    """Freeze the month case the policy selects from this census."""
    census = json.loads(Path(census_path).read_text(encoding="utf-8"))
    if census.get("schema") != CENSUS_SCHEMA:
        raise SystemExit(f"unexpected census schema: {census.get('schema')}")
    if _digest(_body(census)) != census.get("content_key"):
        raise SystemExit("census content key does not describe it")
    stale = sorted(name for name, digest
                   in census["production_sources"].items()
                   if sha256_file(ROOT / name) != digest)
    if stale:
        raise SystemExit(f"census binds stale sources: {stale}")
    if cee.verify_inventory(census["inventory"]):
        raise SystemExit("census inventory no longer verifies")

    derived = rederive(census)
    if derived["disagreements"]:
        raise SystemExit("census rows disagree with the policy: "
                         f"{derived['disagreements']}")
    if not derived["eligible_in_order"]:
        raise SystemExit("no candidate is effect-eligible")
    chosen = derived["eligible_in_order"][0]
    problems = _chosen_requirements(chosen)
    if problems:
        raise SystemExit(f"the chosen case does not qualify: {problems}")

    spec_record = json.loads(Path(canary_spec_path).read_text(
        encoding="utf-8"))
    if spec_record.get("schema") != SPEC_SCHEMA:
        raise SystemExit("unexpected spec schema")
    spec = ClosureSearchSpec.from_dict(spec_record["spec"])
    if (spec.content_key != spec_record["spec_content_key"]
            or list(spec.directed_edges) != [chosen["edge_id"]]):
        raise SystemExit("the spec does not close the selected edge")
    source = spec_record["source_spec"]
    if sha256_file(ROOT / source["path"]) != source["sha256"]:
        raise SystemExit("the frozen zero spec changed")
    frozen = json.loads((ROOT / FROZEN_ZERO_SPEC).read_text(encoding="utf-8"))
    differing = {key for key in frozen
                 if frozen[key] != spec.to_dict().get(key)}
    if differing - {"directed_edges", "search_id"}:
        raise SystemExit("the spec differs from the frozen spec beyond its "
                         f"edge and id: {sorted(differing)}")

    inventory = derived["inventory"]
    candidates = [row["edge_id"] for row in derived["rows"]]
    record = {
        "schema": SCHEMA,
        "kind": "month_case_registration",
        "release_evidence": False,
        "is_full_build": False,
        "claim_boundary": ("a registration and preflight of one month case; "
                           "it prices nothing and adopts nothing"),
        "case_selection_policy": cee.POLICY,
        "order_rule": ORDER_RULE,
        "chosen_edge": chosen["edge_id"],
        "chosen_verdict": chosen,
        "spec": spec.to_dict(),
        "spec_content_key": spec.content_key,
        "search_id": spec.search_id,
        "canary_spec": {"path": str(Path(canary_spec_path).resolve()),
                        "sha256": sha256_file(Path(canary_spec_path)),
                        "content_key": spec_record["content_key"]},
        "candidates": candidates,
        "candidate_list_digest": _digest(candidates),
        "verdicts": derived["rows"],
        "eligible_in_order": [row["edge_id"]
                              for row in derived["eligible_in_order"]],
        "qualified_demand_manifest": census["qualified_demand_manifest"],
        "archives": _archive_bindings(inventory),
        "catalogs": _catalog_bindings(inventory),
        "census": {"path": str(Path(census_path).resolve()),
                   "sha256": sha256_file(Path(census_path)),
                   "content_key": census["content_key"],
                   "inventory_content_key": census["inventory_content_key"]},
        "daily_units_by_build_key": derived["daily_units_by_build_key"],
        "population": {**population_of(spec),
                       "build_keys": len(inventory.archives),
                       "units_per_build_key": sorted(set(
                           derived["daily_units_by_build_key"].values()))},
        "nonzero_canaries": _nonzero(nonzero_evidence),
        "frozen_zero_spec": {
            "path": str(ROOT / FROZEN_ZERO_SPEC),
            "sha256": sha256_file(ROOT / FROZEN_ZERO_SPEC),
            "left_unchanged": True,
            "directed_edges": frozen["directed_edges"]},
        "source_bindings": {name: sha256_file(ROOT / name)
                            for name in SOURCE_BINDINGS},
        "outputs_a_future_guarded_build_would_create": [
            "<index-root>/window-cost-index.json",
            "<build evidence>.json "
            "(subhour_phase5_window_cost_index_evidence_v1)",
            "<status-dir>/status.json and child-telemetry.json",
            "<runner evidence>.json (wci_guarded_run_v1)",
        ],
    }
    record["content_key"] = _digest(_body(record))
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--canary-spec", type=Path, required=True)
    parser.add_argument("--nonzero-evidence", type=Path, nargs="+",
                        required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")
    record = build_registration(census_path=args.census,
                                canary_spec_path=args.canary_spec,
                                nonzero_evidence=args.nonzero_evidence)
    problems = verify_registration(record)
    if problems:
        raise SystemExit(f"the fresh registration does not verify: "
                         f"{problems}")
    publish_new_file(args.out,
                     (json.dumps(record, indent=1, sort_keys=True)
                      + "\n").encode("utf-8"),
                     label="month case registration")
    print(json.dumps({
        "chosen_edge": record["chosen_edge"],
        "spec_content_key": record["spec_content_key"],
        "candidates": len(record["candidates"]),
        "eligible_in_order": record["eligible_in_order"],
        "population": record["population"],
        "archives": len(record["archives"]),
        "catalogs": sorted(record["catalogs"]),
        "content_key": record["content_key"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
