"""Freeze the BOUNDARY-ACCOUNTING paired warm-state contract v3 (LUNA-WARM-08).

Process-free: runs no SUMO, creates no run root, touches no cache, and — unlike
the v2 freeze — reads NO `runs/` path at all.

WHY v3 EXISTS. v2 was spent by LUNA-WARM-07, which produced the most complete
cold-versus-warm comparison so far and still FAILED honestly: coverage was 3/3,
execution evidence was complete, and 16 of 18 semantic groups matched exactly.
The single residual group was the objective itself, `total_time_loss_s`, where
warm was lower on every identity — q10 -7.73 s, q50 -80.62 s, q90 -138.97 s —
monotone in demand volume.

That ordering identified the cause. A vehicle still in flight at the snapshot is
BOUNDARY-ACTIVE: completed-only prefix tripinfo excludes it, and after
`--load-state` its tripinfo reports only post-boundary time loss, so its
pre-boundary delay is counted nowhere. Denser demand strands more vehicles at
the warm point, which is exactly the observed pattern. v3 freezes the corrected
accounting: a per-vehicle boundary ledger captured AT the saved step and
reconciled against the resumed trips by vehicle identity.

WHY THIS TOOL INHERITS RATHER THAN RE-DERIVES. The route-mutation audits, the
per-variant route-safe warm points and the archived-demand hashes are physical
facts about the SAME frozen archive and the SAME frozen case that v2 already
recorded. Re-deriving them would require reading the archive under `runs/`,
which this task is not permitted to touch, and would risk producing DIFFERENT
values for an identical case. They are therefore inherited verbatim from the
frozen v2 manifest — a `validation/` artifact — and the v2 content key is bound
into v3 so the inheritance itself is tamper-evident.

Run:  python3 tools/freeze_monthly_warm_state_v3.py [--verify]
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
OUT = "validation/monthly_warm_state_manifest_v3.json"
CAMPAIGN = "v3"
FROZEN_AT = "2026-07-29"

PARENT = "validation/monthly_warm_state_manifest_v2.json"
# The exact v2 bytes this campaign inherits physical facts from. Recomputed and
# compared at freeze time, so editing v2 after the fact cannot silently change
# what v3 describes.
PARENT_CONTENT_KEY = "c2c904655d59c48374d81fe4f9fe42540b2fb05e229faaae28a17030ffbd64e6"

# Bound sources: every file whose bytes change the meaning of the campaign.
SOURCES = [
    "traffic_sim/simulation/monthly_warm_state.py",
    "traffic_sim/simulation/warm_state_cache.py",
    # NEW in v3: boundary-active accounting decides how a cached prefix becomes
    # the objective. Leaving it unbound would let the reconciliation change
    # under an unchanged campaign key — the precise failure v3 exists to fix.
    "traffic_sim/simulation/warm_state_boundary.py",
    "traffic_sim/simulation/monthly_sumo.py",
    "traffic_sim/simulation/envelope.py",
    "traffic_sim/simulation/metrics.py",
    "traffic_sim/simulation/monthly_search.py",
    "traffic_sim/simulation/finalist_decision.py",
    "traffic_sim/core/closure_calendar.py",
    "traffic_sim/core/contracts.py",
    "run_scenario.py",
    "suggest_closure_time.py",
    "run_monthly_warm_state_validation.py",
    "tools/freeze_monthly_warm_state_v3.py",
]

CASES = [{
    "case_id": "warm-v3-paired-equivalence",
    "directed_edges": ["26354420_60476786_0"],
    "closure_begin_s": 25200,          # 07:00 on day 1
    "closure_end_s": 54000,            # 15:00 on day 1
    "closure_bound_warm_point_s": 24300,
    "rationale": ("the v2 case unchanged, so the residual objective gap is "
                  "measured against directly comparable evidence rather than "
                  "a new case that could differ for unrelated reasons"),
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


def load_parent() -> dict:
    """The frozen v2 manifest, verified by content key before anything is read.

    A parent whose key does not recompute is refused outright: inheriting
    physical facts from bytes that have drifted would produce a v3 campaign
    describing an archive nobody can reconstruct.
    """
    path = ROOT / PARENT
    if not path.is_file():
        raise SystemExit(f"parent manifest is missing: {PARENT}")
    parent = json.loads(path.read_text())
    recomputed = canonical_key(parent)
    if recomputed != parent.get("content_key"):
        raise SystemExit(
            f"parent manifest content key does not recompute "
            f"({recomputed} != {parent.get('content_key')})")
    if recomputed != PARENT_CONTENT_KEY:
        raise SystemExit(
            f"parent manifest is not the expected frozen v2 campaign "
            f"({recomputed} != {PARENT_CONTENT_KEY})")
    return parent


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


def build_manifest() -> dict:
    from traffic_sim.simulation.monthly_warm_state import (
        MINIMUM_PREFIX_S, OBSERVATION_SCHEMA, PREFIX_EVIDENCE_SCHEMA,
        ROUTE_AUDIT_SCHEMA, WARM_ALIGNMENT_S, evaluate_warm_eligibility,
        verify_field_partition)
    from traffic_sim.simulation.monthly_warm_state import (
        _EXECUTION_ONLY as EXECUTION_ONLY)
    from traffic_sim.simulation.warm_state_boundary import (
        BOUNDARY_LEDGER_SCHEMA, TRIPINFO_PRECISION)

    # A campaign whose accounting has an unclassified production field must not
    # be freezable at all.
    verify_field_partition()

    parent = load_parent()
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

    # The case must be the SAME physical closure v2 measured, or the inherited
    # route safety would describe a different filtering problem.
    parent_case = parent["cases"][0]
    for field in ("directed_edges", "closure_begin_s", "closure_end_s",
                  "closure_bound_warm_point_s"):
        if CASES[0][field] != parent_case[field]:
            raise SystemExit(
                f"case field {field!r} differs from the parent campaign "
                f"({CASES[0][field]!r} != {parent_case[field]!r}); inherited "
                f"route safety would not describe this closure")

    manifest = {
        "schema_version": 1,
        "kind": "monthly_warm_state_validation_manifest",
        "campaign_version": CAMPAIGN,
        "frozen_at": FROZEN_AT,
        "status": "frozen_unapproved_unexecuted",
        "approval_mechanism": ("--approval-token must equal this manifest's "
                               "content_key; no approval is stored in the "
                               "manifest itself"),
        "artifact_root": "runs/monthly-warm-state-validation",
        "observation_schema": OBSERVATION_SCHEMA,
        "prefix_evidence_schema": PREFIX_EVIDENCE_SCHEMA,
        "route_audit_schema": ROUTE_AUDIT_SCHEMA,
        "boundary_ledger_schema": BOUNDARY_LEDGER_SCHEMA,
        "tripinfo_precision": TRIPINFO_PRECISION,
        "simulation_mode": "meso",
        "seeds": frozen_seeds,
        "demand_variants": DEMAND_VARIANTS,
        "frozen_schedule_ids": frozen_schedule_ids,
        "warm_alignment_s": WARM_ALIGNMENT_S,
        "minimum_prefix_s": MINIMUM_PREFIX_S,
        "demand_requirement": dict(parent["demand_requirement"]),
        "network_requirement": {"path": "sumo/net.net.xml",
                                "sha256": sha256_file(ROOT / "sumo/net.net.xml")},
        "comparison_policy": {
            "compares": ("the canonical production observation from each arm, "
                         "excluding execution-only fields"),
            "excluded_from_comparison": sorted(EXECUTION_ONLY),
            "tolerance": "exact equality of the semantic payload",
            "on_mismatch": ("record honest fail evidence and publish no cache "
                            "material"),
        },
        "boundary_accounting": {
            "hypothesis": ("v2's residual objective gap is caused by "
                           "boundary-active vehicles whose pre-warm time loss "
                           "is excluded from the completed-only prefix and "
                           "absent from their resumed tripinfo"),
            "v2_residual_gap_s": {"q10": -7.73, "q50": -80.62, "q90": -138.97},
            "mechanism": ("capture a per-vehicle ledger at exactly the saved "
                          "step; reconcile it against resumed tripinfo by "
                          "vehicle identity, applying each boundary offset "
                          "exactly once"),
            "refutation_condition": ("if the objective still differs after "
                                     "reconciliation, boundary-active "
                                     "accounting is NOT the cause and the "
                                     "campaign fails honestly"),
            # Declared BEFORE execution, so a failure of exactly this shape is
            # diagnostic rather than a surprise to be explained away afterwards.
            "known_residual": {
                "mechanism": ("the post-warm half of a boundary vehicle reaches "
                              "the reconstruction through a file SUMO writes at "
                              "its reported precision, so the sum of halves can "
                              "cross a rounding boundary the whole value does "
                              "not"),
                "bound_s_per_boundary_vehicle": 0.01,
                "why_not_fixed_by_more_digits": (
                    "no finite output precision suffices: for any precision the "
                    "true sum can sit closer to a rounding boundary than the "
                    "serialization error, proven for 2..12 decimals in "
                    "tests/test_warm_state_boundary.py; and SUMO's --precision "
                    "is global, so raising it also perturbs recovery and "
                    "waiting semantics the contract requires to stay unchanged"),
                "consequence": ("warm argv is byte-identical to cold argv and "
                                "the comparison stays exact with no tolerance, "
                                "so this residual can make the campaign FAIL "
                                "rather than silently passing"),
            },
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
        # Inherited verbatim — see the module docstring. Bound to the parent key
        # below so the inheritance cannot be edited without invalidating v3.
        "route_safety": parent["route_safety"],
        "archive_files_sha256": dict(parent["archive_files_sha256"]),
        "inherited_from": {
            "manifest": PARENT,
            "content_key": PARENT_CONTENT_KEY,
            "fields": ["route_safety", "archive_files_sha256",
                       "demand_requirement"],
            "reason": ("physical facts about the same frozen archive and the "
                       "same frozen closure; re-deriving them would require "
                       "reading the archive and could yield different values "
                       "for an identical case"),
        },
        "supersedes": {
            "campaign": parent["campaign_version"],
            "content_key": PARENT_CONTENT_KEY,
            "outcome": ("executed and failed on total_time_loss_s only; 16 of "
                        "18 semantic groups matched exactly"),
        },
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
    """All-or-nothing, no-clobber publication (same contract as the v2 freeze)."""
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
