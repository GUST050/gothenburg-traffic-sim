#!/usr/bin/env python3
"""DIAGNOSTIC_ONLY_NOT_RELEASE_EVIDENCE: a manifest-free WCI builder.

Standalone standing-in for `tools/guarded_wci_build.py --mode build`,
matching its exact CLI shape (`--mode`, `--profile`, `--status-dir`,
`--evidence-out`, `--registration`, `--index-out`, `--build-evidence-out`,
`--evidence-id`) so `traffic_sim.simulation.window_cost_index_resolver`'s
`guarded_build_script=` override can invoke it exactly like the real one --
proving the RESOLVER's lock/subprocess/re-check contract for real.

The one thing it deliberately does NOT do is the real builder's Phase D
qualified-demand-manifest gate (`validate_qualified_demand_manifest_shape`,
CODE_APPROVED source freeze): those require a real, hours-long release
process this diagnostic canary is explicitly not trying to earn. Everything
else is the REAL production code: `tools.build_window_cost_index._raw_index_
records` (which itself resolves archives via `MonthlyDemandResolverRunner`
with `qualified_demand_manifest=None`, a documented, fully-supported
parameter value -- see `traffic_sim/simulation/monthly_demand.py:1107`),
the real `WindowCostIndex` class, and the real atomic `write_index`.

Never used by, or mistaken for, `tools/guarded_wci_build.py` itself: the
profile/registration files it reads are diagnostic-shaped
(`wci_diagnostic_profile_v1` / `wci_diagnostic_registration_v1`), which the
real Phase 4/5 tools would refuse outright.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", required=True, choices=("build",))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--index-out", type=Path, required=True)
    parser.add_argument("--build-evidence-out", type=Path, required=True)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args(argv)

    if args.evidence_out.exists():
        raise SystemExit(f"append-only output already exists: {args.evidence_out}")

    def _fail(reason: str) -> int:
        record = {
            "schema": "wci_diagnostic_builder_evidence_v1",
            "diagnostic_only": True,
            "release_evidence": False,
            "claim_boundary": "DIAGNOSTIC_ONLY_NOT_RELEASE_EVIDENCE",
            "evidence_id": args.evidence_id,
            "outcome": {"state": "failed", "reason": reason},
        }
        common.publish(args.evidence_out, record)
        return 1

    try:
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
        registration = json.loads(args.registration.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return _fail(f"cannot read profile/registration: {error}")

    if profile.get("schema") != "wci_diagnostic_profile_v1":
        return _fail("profile is not a wci_diagnostic_profile_v1")
    if registration.get("schema") != "wci_diagnostic_registration_v1":
        return _fail("registration is not a wci_diagnostic_registration_v1")

    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation.deterministic_disruption import DailyCostCache
    from traffic_sim.simulation.window_cost_index import WindowCostIndex, write_index
    from tools.build_window_cost_index import _raw_index_records

    try:
        spec_path = Path(profile["bound_spec"]["path"])
        spec = ClosureSearchSpec.from_dict(
            json.loads(spec_path.read_text(encoding="utf-8")))
        runs_root = Path(profile["runs_root"])
        args.index_out.mkdir(parents=True, exist_ok=True)
        # Real production (build_from_profile) reads cache_root from the
        # profile itself -- the SAME DailyCostCache the direct-path pricing
        # pass (profile_monthly_cost_ledger.py) already populated -- because
        # compare_oracle below can only compare against daily-unit costs that
        # already exist. A brand-new, empty cache here would make every
        # oracle lookup fail closed with "oracle is missing", exactly as it
        # would in production for a month/edge never priced by any direct
        # pass. Falling back to an index-local cache keeps this script usable
        # standalone when a caller has no prior direct-path cache to offer.
        cache_root = (Path(profile["cache_root"]) if profile.get("cache_root")
                     else args.index_out / "oracle-cache")
        records, oracle_records, measurement = _raw_index_records(
            spec, runs_root=runs_root, oracle_cache=DailyCostCache(cache_root),
            qualified_demand_manifest=None)
        index = WindowCostIndex(
            bound_identity={
                "schema": "wci_diagnostic_bound_identity_v1",
                "search_content_key": spec.content_key,
                "policy_content_key": profile.get("policy_content_key", ""),
                "producer_source_manifest": profile.get(
                    "producer_source_manifest", {}),
                "producer_runtime_manifest": profile.get(
                    "producer_runtime_manifest", {}),
                "provider_identities": measurement["provider_identities"],
                # IndependentDailyCostSource.__init__ requires this singular
                # field unconditionally (a fail-closed constructor check,
                # separate from validate_window_cost_index_binding's per-unit
                # provider_identities lookup at lookup time). Real production
                # (build_from_profile) sources it from the direct-path
                # ledger's own provider_identity; the caller here does the
                # same by passing its already-priced direct arm's identity.
                "provider_identity": profile.get(
                    "ledger_provider_identity", {}),
            },
            records=records,
        )
        oracle = index.compare_oracle(oracle_records)
        if not oracle["oracle_complete"] or not oracle["field_identical"]:
            return _fail(f"oracle mismatch: {oracle}")
        index_path = args.index_out / "window-cost-index.json"
        write_index(index_path, index)
    except Exception as error:  # noqa: BLE001 -- fail closed, report, never crash silently
        return _fail(f"{type(error).__name__}: {error}")

    build_evidence = {
        "schema": "wci_diagnostic_build_evidence_v1",
        "diagnostic_only": True,
        "release_evidence": False,
        "index_path": str(index_path),
        "daily_unit_count": index.to_dict()["daily_unit_count"],
    }
    common.publish(args.build_evidence_out, build_evidence)
    record = {
        "schema": "wci_diagnostic_builder_evidence_v1",
        "diagnostic_only": True,
        "release_evidence": False,
        "claim_boundary": "DIAGNOSTIC_ONLY_NOT_RELEASE_EVIDENCE",
        "evidence_id": args.evidence_id,
        "outcome": {"state": "passed"},
    }
    common.publish(args.evidence_out, record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
