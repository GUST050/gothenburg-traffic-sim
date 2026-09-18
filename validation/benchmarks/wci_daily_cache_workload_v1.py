#!/usr/bin/env python3
"""Freeze one build key's costing workload as a self-contained contract.

A diagnostic A/B has to give both arms the SAME work, but the frozen month
chain (profile, census, registration) binds the bytes of the very costing
sources an arm changes, so it cannot describe both arms at once. This
contract is the way out: it copies out everything a daily cost depends on
— the archive's four files, the network, both catalog pools, the closure
spec and the exact 65 canonical schedules — each pinned by SHA-256 or by a
canonical digest, so an arm can prove it is doing the intended work without
consulting the production chain at all.

The month registration is read ONLY to document WHICH workload was chosen.
Nothing here asserts that the registration, the census or the profile is
valid under any code other than the code that froze this file, and the
record says so in as many words.

Append-only and diagnostic (``release_evidence: false``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_daily_cache_ab_workload_v1"


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def build_contract(registration_path: Path, profile_path: Path,
                   build_key: str) -> Dict[str, Any]:
    """Everything the 65 daily costs of one build key depend on."""
    from tools import build_daily_cost_cache as builder
    from tools import build_window_cost_index as wci
    from tools import freeze_wci_month_case as month
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation import disruption as disruption_analysis
    from traffic_sim.simulation.deterministic_disruption import (
        VARIANT_FILENAMES, NetworkCostModel)

    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    problems = month.verify_registration(registration)
    if problems:
        raise SystemExit(
            "the workload may only be frozen from a registration that "
            f"verifies under the freezing code: {problems}")
    if build_key not in registration["archives"]:
        raise SystemExit(f"unknown build key: {build_key}")

    bound = wci._bound_inputs(profile_path)
    spec = ClosureSearchSpec.from_dict(registration["spec"])
    grouped = builder.units_of(spec, bound["runs_root"],
                               bound["qualified_manifest"])
    units = grouped[build_key]

    archive_record = registration["archives"][build_key]
    archive = Path(archive_record["archive"])
    files = {"demand_meta.json": common.file_sha256(
        archive / "demand_meta.json")}
    for _variant, filename in sorted(VARIANT_FILENAMES.items()):
        files[filename] = common.file_sha256(archive / filename)

    network = NetworkCostModel()
    spec_bytes = _canonical(spec.to_dict())
    schedules = [{"unit_id": str(unit_id),
                  "schedule_id": schedule.schedule_id,
                  "schedule": schedule.to_dict()}
                 for unit_id, _identity, schedule in units]
    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "adoption_eligible": False,
        "production_chain_rebuild_required": True,
        "claim_boundary": (
            "a frozen description of one build key's costing work, for a "
            "diagnostic A/B only. It adopts nothing, and it makes no claim "
            "that the profile, the census or the month registration is "
            "valid under any code other than the code that froze this file"),
        "driver": common.driver_binding(Path(__file__)),
        "frozen_by": common.git_state(),
        "runtime": common.runtime_manifest(),
        "build_key": build_key,
        "chosen_edge": registration["chosen_edge"],
        "spec": spec.to_dict(),
        "spec_content_key": spec.content_key,
        "spec_canonical_sha256": hashlib.sha256(spec_bytes).hexdigest(),
        "archive": {"path": str(archive),
                    "archive_content_key": archive_record[
                        "archive_content_key"],
                    "files": files,
                    "variant_filenames": dict(sorted(
                        VARIANT_FILENAMES.items()))},
        "network": dict(network.identity()),
        "catalogs": {
            pool: {"catalog_key": item["catalog_key"],
                   "path": item["path"],
                   "routes_sha256": item["routes_sha256"],
                   "metadata_sha256": item["metadata_sha256"]}
            for pool, item in sorted(registration["catalogs"].items())},
        "other_cost_inputs": {
            "max_assumed_congestion_delay_s":
                disruption_analysis.MAX_ASSUMED_CONGESTION_DELAY_S,
            "demand_variants": ["q10", "q50", "q90"],
        },
        "schedules": schedules,
        "daily_units": len(schedules),
        "schedule_order_digest": common.digest(
            [item["schedule_id"] for item in schedules]),
        "schedule_payload_digest": common.digest(
            [item["schedule"] for item in schedules]),
        "workload_source": {
            "role": ("documented source of the workload CHOICE only; this "
                     "contract does not report the registration as valid "
                     "under any other code"),
            "registration_path": str(registration_path),
            "registration_sha256": common.file_sha256(registration_path),
            "registration_content_key": registration["content_key"],
            "profile_path": str(profile_path),
            "profile_sha256": common.file_sha256(profile_path),
            "verified_under_the_freezing_code": True,
        },
    }
    # The content key is left to `common.publish`, which digests the record
    # as it writes it. Precomputing one here would digest a record that
    # already carried a key, and the published file would not describe
    # itself.
    return record


def verify_contract(contract: Mapping) -> List[str]:
    """Re-prove every costing input of the contract from bytes on disk."""
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation import disruption as disruption_analysis
    from traffic_sim.simulation.deterministic_disruption import (
        VARIANT_FILENAMES, NetworkCostModel)

    problems: List[str] = []
    if contract.get("schema") != SCHEMA:
        return [f"unexpected workload schema: {contract.get('schema')}"]
    if "content_key" in contract:
        body = {key: value for key, value in contract.items()
                if key != "content_key"}
        if common.digest(body) != contract["content_key"]:
            problems.append("workload content key does not describe it")
    archive = Path(contract["archive"]["path"])
    for name, digest in sorted(contract["archive"]["files"].items()):
        if common.file_sha256(archive / name) != digest:
            problems.append(f"archive file changed: {name}")
    if dict(VARIANT_FILENAMES) != dict(
            contract["archive"]["variant_filenames"]):
        problems.append("the variant filename map changed")
    if dict(NetworkCostModel().identity()) != dict(contract["network"]):
        problems.append("network changed")
    for pool, item in sorted(contract["catalogs"].items()):
        routes = Path(item["path"])
        if common.file_sha256(routes) != item["routes_sha256"]:
            problems.append(f"catalog changed: {pool}")
        if common.file_sha256(routes.with_name("catalog.meta.json")) != item[
                "metadata_sha256"]:
            problems.append(f"catalog metadata changed: {pool}")
    spec = ClosureSearchSpec.from_dict(contract["spec"])
    if hashlib.sha256(_canonical(spec.to_dict())).hexdigest() != contract[
            "spec_canonical_sha256"]:
        problems.append("the closure spec's canonical bytes changed")
    if spec.content_key != contract["spec_content_key"]:
        problems.append("the closure spec content key changed")
    if sorted(spec.directed_edges) != [contract["chosen_edge"]]:
        problems.append("the spec does not close the chosen edge alone")
    if disruption_analysis.MAX_ASSUMED_CONGESTION_DELAY_S != contract[
            "other_cost_inputs"]["max_assumed_congestion_delay_s"]:
        problems.append("the assumed congestion delay changed")
    schedules = contract["schedules"]
    if len(schedules) != contract["daily_units"]:
        problems.append("the schedule list does not match its own count")
    if common.digest([item["schedule_id"] for item in schedules]) != contract[
            "schedule_order_digest"]:
        problems.append("the schedule order changed")
    if common.digest([item["schedule"] for item in schedules]) != contract[
            "schedule_payload_digest"]:
        problems.append("a canonical schedule changed")
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--build-key", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"append-only output already exists: {args.out}")
    record = build_contract(args.registration, args.profile, args.build_key)
    problems = verify_contract(record)
    if problems:
        raise SystemExit(f"the fresh workload does not verify: {problems}")
    published = common.publish(args.out, record)
    # Verify the BYTES that were written, not the object in memory: an arm
    # will read the file, so the file is what has to describe itself.
    problems = verify_contract(
        json.loads(args.out.read_text(encoding="utf-8")))
    if problems:
        raise SystemExit(f"the published workload does not verify: "
                         f"{problems}")
    print(json.dumps({
        "build_key": record["build_key"],
        "chosen_edge": record["chosen_edge"],
        "daily_units": record["daily_units"],
        "archive": record["archive"]["path"],
        "schedule_order_digest": record["schedule_order_digest"],
        "content_key": published["content_key"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
