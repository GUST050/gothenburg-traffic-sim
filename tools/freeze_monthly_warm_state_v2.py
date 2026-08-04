"""Freeze the ROUTE-SAFE paired warm-state contract v2 (LUNA-WARM-06).

Process-free: runs no SUMO, creates no run root, touches no cache. Beyond the
tracked sources it reads exactly the five approved archived-demand files, to
derive per-variant route-mutation audits and the ROUTE-SAFE warm point.

v1 was spent by LUNA-WARM-05, which produced the first real cold-versus-warm
comparison and FAILED: loaded/inserted differed by +1081/+1065 (SUMO's
statistics counters are cumulative across a loaded state, not disjoint),
closure throughput was unmeasured (None vs 0), and the prefix had been
simulated from the UNFILTERED route even though closure filtering changes some
vehicles. v2 freezes the corrected contract.

Run:  python3 tools/freeze_monthly_warm_state_v2.py [--verify]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VALIDATION = ROOT / "validation"
OUT = "validation/monthly_warm_state_manifest_v2.json"
CAMPAIGN = "v2"
FROZEN_AT = "2026-07-29"

# Bound sources: every file whose bytes change the meaning of the campaign.
SOURCES = [
    "traffic_sim/simulation/monthly_warm_state.py",
    "traffic_sim/simulation/warm_state_cache.py",
    "traffic_sim/simulation/monthly_sumo.py",
    "traffic_sim/simulation/envelope.py",
    "traffic_sim/simulation/metrics.py",
    # These DERIVE the approved identity set: the seed rule, the schedule
    # enumeration, the spec contract and the variant tuple. Unbound, they could
    # change which seeds and schedules an approved key actually executes.
    "traffic_sim/simulation/monthly_search.py",
    "traffic_sim/simulation/finalist_decision.py",
    "traffic_sim/core/closure_calendar.py",
    "traffic_sim/core/contracts.py",
    # The SUMO invocation itself and the feasibility gate interpret the state
    # and its prefix. Omitting them would let simulation or gate semantics
    # drift under an unchanged campaign key.
    "run_scenario.py",
    "suggest_closure_time.py",
    "run_monthly_warm_state_validation.py",
    "tools/freeze_monthly_warm_state_v2.py",
]

# One case is enough to prove or refute equivalence, and keeps a future
# approved run small. It is a time-windowed weekday closure with a real prefix.
CASES = [{
    "case_id": "warm-v2-paired-equivalence",
    "directed_edges": ["26354420_60476786_0"],
    "closure_begin_s": 25200,          # 07:00 on day 1
    "closure_end_s": 54000,            # 15:00 on day 1
    # NOT a fixed constant any more: the safe point is derived per variant from
    # the route-mutation audit and frozen below. This records the closure-bound
    # ceiling only.
    "closure_bound_warm_point_s": 24300,
    "rationale": ("a v6 held-out edge with demand support; one schedule over "
                  "three identities is enough to prove or refute equivalence"),
}]


def canonical_key(payload) -> str:
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


SEEDS = [1000, 1001, 1002]
DEMAND_VARIANTS = ["q10", "q50", "q90"]
SCHEDULES_PER_CASE = 1
REPETITIONS_PER_VARIANT = 1

SPEC_TEMPLATE = {
    "permitted_date_start": "2027-07-15",
    "permitted_date_end": "2027-07-19",
    "required_work_minutes": 480,
    "max_consecutive_start_days": 1,
    "permitted_daily_band": {"earliest_start": "07:00", "latest_end": "15:30"},
    "source": "forecast",
    "timezone": "Europe/Stockholm",
    "dst_policy": "exclude_transition_dates",
    "allowed_weekdays": [0, 1, 2, 3, 4, 5, 6],
    "blackout_dates": [],
    "same_daily_window": True,
    "resolution_minutes": 15,
    "closure_type": "full",
    "duration_basis": "required_work_time",
    "work_to_closure_assumption": "one_to_one",
    "objective_profile": "robust_time_loss",
    "policy_status": "user_supplied_unverified",
}


def frozen_identity_set() -> tuple[list, list]:
    """The EXACT schedules and seeds the contract approves.

    Derived from the live production rules and then FROZEN, so a later change to
    the seed rule, the schedule enumeration, the spec contract or the variant
    tuple invalidates this campaign key instead of silently changing what an
    approved run executes.
    """
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.core.closure_calendar import generate_closure_schedules
    from traffic_sim.simulation.finalist_decision import DEMAND_VARIANTS as PRODUCTION
    from traffic_sim.simulation.monthly_search import canonical_seed

    if sorted(DEMAND_VARIANTS) != sorted(PRODUCTION):
        raise SystemExit(
            f"declared variants {DEMAND_VARIANTS} do not match production "
            f"{list(PRODUCTION)}")
    spec = ClosureSearchSpec.from_dict({
        "search_id": CASES[0]["case_id"],
        "directed_edges": CASES[0]["directed_edges"],
        "demand_build_id": "2ac04275daabe93c",
        **SPEC_TEMPLATE,
    })
    schedules = generate_closure_schedules(spec)[:SCHEDULES_PER_CASE]
    if len(schedules) != SCHEDULES_PER_CASE:
        raise SystemExit("schedule generation did not produce the frozen count")
    seeds = sorted({canonical_seed(variant, repetition)
                    for variant in DEMAND_VARIANTS
                    for repetition in range(REPETITIONS_PER_VARIANT)})
    if seeds != sorted(SEEDS):
        raise SystemExit(
            f"production seed assignment {seeds} does not match declared {SEEDS}")
    return [s.schedule_id for s in schedules], seeds


ARCHIVE = ROOT / "runs" / "demand-20260721-222017-41bc682a-bbe1"
ARCHIVE_FILES = ("demand_meta.json", "manifest.json", "calibrated.rou.xml",
                 "calibrated_v1.rou.xml", "calibrated_v2.rou.xml")
VARIANT_ROUTES = {"q50": "calibrated.rou.xml", "q10": "calibrated_v1.rou.xml",
                  "q90": "calibrated_v2.rou.xml"}


def freeze_route_safety() -> dict:
    """Derive, per variant, the route-mutation audit and the SAFE warm point.

    Reads only the five approved archive files. Filtering is the production
    `truncate_stranded_vehicles`; the audit then reports which vehicles it
    changed or dropped and the earliest such departure. The safe point is
    strictly before both that departure and the closure — a snapshot at or
    after it would have simulated a route the candidate never uses.
    """
    import shutil
    import tempfile
    import run_scenario as rs
    from traffic_sim.simulation.monthly_warm_state import (
        audit_route_mutation, route_safe_warm_point)

    case = CASES[0]
    closures = [{"edge_id": edge, "begin_s": case["closure_begin_s"],
                 "end_s": case["closure_end_s"]}
                for edge in case["directed_edges"]]
    adjacency = rs.build_edge_graph(set(case["directed_edges"]))
    freeflow = rs.edge_freeflow_times()

    out = {}
    workspace = Path(tempfile.mkdtemp(prefix="warm-v2-audit-"))
    try:
        for variant, filename in sorted(VARIANT_ROUTES.items()):
            original = ARCHIVE / filename
            filtered = workspace / f"{variant}.filtered.rou.xml"
            rs.truncate_stranded_vehicles(
                original, list(case["directed_edges"]), filtered, adjacency,
                closures, freeflow)
            audit = audit_route_mutation(original, filtered)
            point = route_safe_warm_point(
                audit, closure_begin_s=case["closure_begin_s"])
            if point is None:
                raise SystemExit(
                    f"{variant}: no route-safe warm point exists for this "
                    f"closure; the campaign cannot warm it")
            out[variant] = {"audit": audit, "safe_warm_point_s": point}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return out


def build_manifest() -> dict:
    from traffic_sim.simulation.monthly_warm_state import (
        MINIMUM_PREFIX_S, OBSERVATION_SCHEMA, PREFIX_EVIDENCE_SCHEMA,
        ROUTE_AUDIT_SCHEMA, WARM_ALIGNMENT_S, evaluate_warm_eligibility,
        verify_field_partition)
    from traffic_sim.simulation.monthly_warm_state import (
        _EXECUTION_ONLY as EXECUTION_ONLY)

    # A campaign whose accounting has an unclassified production field must not
    # be freezable at all. Without this the verifier existed but nothing ran it
    # at freeze time, so a fresh manifest could be produced and `--verify`
    # could pass over incomplete semantics.
    verify_field_partition()

    frozen_schedule_ids, frozen_seeds = frozen_identity_set()

    for case in CASES:
        decision = evaluate_warm_eligibility(
            closures=[{"edge_id": case["directed_edges"][0],
                       "begin_s": case["closure_begin_s"],
                       "end_s": case["closure_end_s"]}],
            scenario_start_s=0, simulation_mode="meso", duration_s=432000)
        if not decision.eligible:
            raise SystemExit(f"{case['case_id']} is not warm-eligible: "
                             f"{decision.reason}")
        if decision.warm_point_s != case["closure_bound_warm_point_s"]:
            raise SystemExit(
                f"{case['case_id']} closure bound {decision.warm_point_s} != "
                f"recorded {case['closure_bound_warm_point_s']}")

    route_safety = freeze_route_safety()

    manifest = {
        "schema_version": 1,
        "kind": "monthly_warm_state_validation_manifest",
        "campaign_version": CAMPAIGN,
        "frozen_at": FROZEN_AT,
        "status": "frozen_unapproved_unexecuted",
        # Deliberately NO approval field. Storing one inside the hashed body
        # would be self-referential: setting it changes the content key the
        # approval names, so those exact frozen bytes could never be approved.
        # Approval lives in the workflow record; the harness verifies the
        # operator passed a token equal to this manifest's content key.
        "approval_mechanism": ("--approval-token must equal this manifest's "
                               "content_key; no approval is stored in the "
                               "manifest itself"),
        "artifact_root": "runs/monthly-warm-state-validation",
        "observation_schema": OBSERVATION_SCHEMA,
        "prefix_evidence_schema": PREFIX_EVIDENCE_SCHEMA,
        "route_audit_schema": ROUTE_AUDIT_SCHEMA,
        "simulation_mode": "meso",
        "seeds": frozen_seeds,
        "demand_variants": DEMAND_VARIANTS,
        "frozen_schedule_ids": frozen_schedule_ids,
        "warm_alignment_s": WARM_ALIGNMENT_S,
        "minimum_prefix_s": MINIMUM_PREFIX_S,
        "demand_requirement": {
            "demand_build_key": "2ac04275daabe93c",
            "archive_path": "runs/demand-20260721-222017-41bc682a-bbe1",
            "n_intervals": 480,
            "note": ("the campaign must bind this exact archive; resolving the "
                     "key alone is ambiguous across successful archives"),
        },
        "network_requirement": {"path": "sumo/net.net.xml",
                                "sha256": sha256_file(ROOT / "sumo/net.net.xml")},
        "comparison_policy": {
            "compares": ("the canonical production observation from each arm, "
                         "excluding execution-only fields"),
            # DERIVED from the live constant, never restated by hand: a policy
            # that drifts from the code it describes is worse than no policy.
            "excluded_from_comparison": sorted(EXECUTION_ONLY),
            "tolerance": "exact equality of the semantic payload",
            "on_mismatch": ("record honest fail evidence and publish no cache "
                            "material"),
        },
        "performance_reporting": {
            "records": ["phase_runtime_s", "peak_rss_bytes"],
            "claim_policy": ("speedup is REPORTED, never claimed as proven "
                             "until an approved paired run passes"),
        },
        "schedules_per_case": SCHEDULES_PER_CASE,
        "repetitions_per_variant": REPETITIONS_PER_VARIANT,
        "baseline_trip_duration_p99_s": 1800,
        "spec_template": dict(SPEC_TEMPLATE),
        "cases": CASES,
        "route_safety": route_safety,
        "archive_files_sha256": {
            name: sha256_file(ARCHIVE / name) for name in sorted(ARCHIVE_FILES)},
        "claim_scope": ("proves or refutes cold/warm production-observation "
                        "equivalence for the frozen cases only; it establishes "
                        "no product behaviour and no adoption"),
    }
    manifest["source_fingerprints"] = {name: sha256_file(ROOT / name)
                                       for name in sorted(SOURCES)}
    manifest["content_key"] = canonical_key(manifest)
    return manifest


def build_artifacts() -> dict:
    return {OUT: json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n"}


def publish(artifacts, root=ROOT, write=None):
    """All-or-nothing, no-clobber publication (same contract as the v6 freeze)."""
    root = Path(root)
    existing = [rel for rel in artifacts if (root / rel).exists()]
    if existing:
        raise SystemExit(f"refusing to overwrite existing artifacts: {sorted(existing)}")
    write = write or (lambda path, text: path.write_text(text))
    with tempfile.TemporaryDirectory() as tmp:
        staged = {}
        for relative, text in artifacts.items():
            path = Path(tmp) / Path(relative).name
            path.write_text(text)
            staged[relative] = path
        owned_scratch, owned_final = [], []
        try:
            for relative, path in sorted(staged.items()):
                target = root / relative
                scratch = target.with_name(target.name + ".partial")
                if target.exists() or target.is_symlink():
                    raise SystemExit(f"path appeared during publish: {target.name}")
                os.close(os.open(scratch, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
                owned_scratch.append(scratch)
                write(scratch, path.read_text())
                os.link(scratch, target)
                owned_final.append(target)
                scratch.unlink()
                owned_scratch.remove(scratch)
        except BaseException:
            residue = []
            for candidate in reversed(owned_final + owned_scratch):
                try:
                    if candidate.exists() or candidate.is_symlink():
                        candidate.unlink()
                except OSError as error:
                    residue.append(f"{candidate}: {error}")
            if residue:
                raise RuntimeError("rollback left residue behind: " + "; ".join(residue))
            raise
    return sorted(artifacts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true",
                        help="recompose and compare; never writes")
    args = parser.parse_args(argv)
    artifacts = build_artifacts()
    if args.verify:
        drift = [rel for rel, text in artifacts.items()
                 if not (ROOT / rel).is_file() or (ROOT / rel).read_text() != text]
        print("reproduces byte-for-byte:", not drift, drift or "")
        return 0 if not drift else 1
    publish(artifacts)
    manifest = json.loads(artifacts[OUT])
    print(f"  {OUT}: {sha256_file(ROOT / OUT)[:16]}…")
    print("content key:", manifest["content_key"])
    print("cases:", len(manifest["cases"]), "| sources:", len(manifest["source_fingerprints"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
