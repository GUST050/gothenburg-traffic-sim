"""Freeze the DIAGNOSTIC-COMPLETE paired warm-state contract v5 (LUNA-WARM-11).

Process-free: runs no SUMO, creates no run root, touches no cache, and reads NO
`runs/` path at all — including the SPENT v4 outcome.

WHY v5 EXISTS. v4 was EXECUTED (LUNA-WARM-10) and failed honestly, but it failed
UNINFORMATIVELY: all three warm arms fell back to cold, so the campaign compared
cold production against cold production and produced three identical digests with
zero mismatches. Coverage was complete and the objective agreed perfectly — while
nothing had actually been warmed. The execution-evidence gate caught it, which is
exactly what that gate is for.

What the evidence could NOT say is why. The runner recorded free-text decline
reasons at eleven separate sites and the harness consumed none of them, so an
immutable package existed that could not distinguish eleven different failures.
That forces the rerun its own contract forbids.

v5 changes nothing about the accounting. It makes the diagnosis part of the
evidence: every warm-enabled observation finalizes exactly ONE structured attempt
record carrying its identity, ordered events with stable reason codes, and one
terminal outcome. The record is bound into the equivalence artifact, and a
missing, duplicated, unexpected, malformed or self-contradictory attempt fails the
campaign and forbids publication. Attempt coverage counts finalized IDENTITY
attempts, never events.

The v4 hypothesis is unchanged and still UNPROVEN: LUNA-WARM-07's residual may
have been caused by default state serialization. v5 is what would let a future
failure be read instead of guessed at.

WHY THIS TOOL INHERITS RATHER THAN RE-DERIVES. The route-mutation audits, the
per-variant route-safe warm points and the archived-demand hashes are physical
facts about the SAME frozen archive and the SAME frozen case that v4 already
recorded. Re-deriving them would require reading the archive under `runs/`,
which this task is not permitted to touch, and would risk producing DIFFERENT
values for an identical case. The NETWORK identity is inherited for the same
reason: route safety describes the parent's network, so re-hashing the live file
could pair a changed network with stale audits. The live hash is verified against
the inherited one instead, and drift fails the freeze. They are therefore inherited verbatim from the
frozen v4 manifest — a `validation/` artifact — and the v4 content key is bound
into v5 so the inheritance itself is tamper-evident. The SPENT v4 outcome under
`runs/` is never read: only its tracked manifest is.

Run:  python3 tools/freeze_monthly_warm_state_v5.py --write
      python3 tools/freeze_monthly_warm_state_v5.py --verify

Do NOT verify this contract with the v4 freeze tool. v4 is SPENT, and live
source drift makes its `--verify` EXPECTED to fail; that failure describes
v4's supersession and says nothing about v5.
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
OUT = "validation/monthly_warm_state_manifest_v5.json"
CAMPAIGN = "v5"
FROZEN_AT = "2026-07-30"

PARENT = "validation/monthly_warm_state_manifest_v4.json"
# The exact v2 bytes this campaign inherits physical facts from. Recomputed and
# compared at freeze time, so editing v2 after the fact cannot silently change
# what v3 describes.
PARENT_CONTENT_KEY = "d7db25c61b953c123ccb7594e01afaff042d6e1ffdce26190c297bdeb40bbf85"

# Bound sources: every file whose bytes change the meaning of the campaign.
SOURCES = [
    "traffic_sim/simulation/monthly_warm_state.py",
    "traffic_sim/simulation/warm_state_cache.py",
    # Snapshot accounting decides how a cached prefix becomes the objective and
    # what the saved state actually is. Leaving it unbound would let either
    # change under an unchanged campaign key.
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
    "tools/freeze_monthly_warm_state_v5.py",
]

CASES = [{
    "case_id": "warm-v5-paired-equivalence",
    "directed_edges": ["26354420_60476786_0"],
    "closure_begin_s": 25200,          # 07:00 on day 1
    "closure_end_s": 54000,            # 15:00 on day 1
    "closure_bound_warm_point_s": 24300,
    "rationale": ("the same physical closure v2 executed, so the residual "
                  "objective gap is measured against directly comparable "
                  "evidence rather than a new case that could differ for "
                  "unrelated reasons"),
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
            f"parent manifest is not the expected frozen v4 campaign "
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
        SNAPSHOT_FACTS_SCHEMA, TRIPINFO_PRECISION, snapshot_settings_arguments)
    from traffic_sim.simulation.monthly_sumo import (
        WARM_ATTEMPT_SCHEMA, WARM_INFORMATIONAL_CODES, WARM_OUTCOMES,
        WARM_TERMINAL_CODES)
    from traffic_sim.simulation.warm_state_cache import (
        STATE_PRECISION, STATE_RNG_SAVED)

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
    # The inherited route safety only describes the parent's network, so the
    # live file must still BE that network.
    inherited_network = parent["network_requirement"]
    live_network = sha256_file(ROOT / inherited_network["path"])
    if live_network != inherited_network["sha256"]:
        raise SystemExit(
            f"the live {inherited_network['path']} no longer matches the "
            f"network the inherited route safety was derived from "
            f"({live_network} != {inherited_network['sha256']}); re-deriving "
            f"route safety is a separate, deliberate step")

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
        "snapshot_facts_schema": SNAPSHOT_FACTS_SCHEMA,
        # The diagnostic contract is part of the campaign identity: a run whose
        # failures cannot be read is a run that must be repeated, and repeating
        # is what the one-shot approval forbids.
        "warm_attempt_contract": {
            "schema": WARM_ATTEMPT_SCHEMA,
            "outcomes": sorted(WARM_OUTCOMES),
            "terminal_codes": sorted(WARM_TERMINAL_CODES),
            "informational_codes": sorted(WARM_INFORMATIONAL_CODES),
            "coverage_rule": ("exactly one FINALIZED attempt per requested warm "
                              "identity; coverage counts identity attempts, "
                              "never events"),
            "required_attempts": len(DEMAND_VARIANTS) * REPETITIONS_PER_VARIANT
                                 * SCHEDULES_PER_CASE,
            "on_gap": ("a missing, duplicated, unexpected, malformed or "
                       "self-contradictory attempt fails the record and "
                       "forbids cache publication"),
        },
        "tripinfo_precision": TRIPINFO_PRECISION,
        # The state-serialization settings are part of the contract because they
        # decide what the saved state IS. Derived from the cache constants the
        # identity records, and asserted against the actual snapshot argv, so a
        # manifest can never claim a fidelity the command does not apply.
        "state_settings": {
            "save_state_rng": STATE_RNG_SAVED,
            "save_state_precision": STATE_PRECISION,
            "snapshot_arguments": snapshot_settings_arguments(),
        },
        "simulation_mode": "meso",
        "seeds": frozen_seeds,
        "demand_variants": DEMAND_VARIANTS,
        "frozen_schedule_ids": frozen_schedule_ids,
        "warm_alignment_s": WARM_ALIGNMENT_S,
        "minimum_prefix_s": MINIMUM_PREFIX_S,
        "demand_requirement": dict(parent["demand_requirement"]),
        # INHERITED, not re-hashed. The route audits and safe warm points come
        # from the parent and describe THAT network; re-hashing the live file
        # would happily pair a changed network with stale audits. The live hash
        # is verified against the inherited one below instead, so drift fails
        # the freeze rather than being silently absorbed.
        "network_requirement": dict(parent["network_requirement"]),
        "comparison_policy": {
            "compares": ("the canonical production observation from each arm, "
                         "excluding execution-only fields"),
            "excluded_from_comparison": sorted(EXECUTION_ONLY),
            "tolerance": "exact equality of the semantic payload",
            "on_mismatch": ("record honest fail evidence and publish no cache "
                            "material"),
        },
        "accounting": {
            "rule": ("the objective is the completed-prefix aggregate plus the "
                     "resumed aggregate; each vehicle is counted once and "
                     "whole, and NO boundary offset is applied"),
            "why_v3_is_rejected": (
                "v3 added a per-vehicle boundary offset on the theory that "
                "resumed tripinfo reports only post-boundary delay. The "
                "approved LUNA-WARM-08 diagnostic measured the opposite: the "
                "saved state preserves the accumulator and the resumed tripinfo "
                "reports 109.90 s against an uninterrupted 109.90 s, so the "
                "offset double counts"),
            "measured_evidence": {
                "outcome": "validation/warm_state_time_loss_semantics_v2_outcome",
                "classification": "full_accumulator_preserved",
                "boundary_capture_s": 15.718389417064149,
                "post_load_accumulator_s": 15.72,
                "uninterrupted_final_s": 109.9,
                "resumed_final_s": 109.9,
                "post_boundary_only_would_be_s": 94.18,
                "observed_return_codes": {"cold": 0, "prefix": 0, "resumed": 0},
            },
            "no_serialization_residual": (
                "aggregates are whole values, so nothing is rounded twice; the "
                "bounded skew v3 had to declare does not arise"),
        },
        "hypothesis": {
            "claim": ("LUNA-WARM-07's residual was caused by DEFAULT state "
                      "serialization: v2's identity recorded save-state.rng and "
                      "16-digit precision while its command applied neither, so "
                      "the resumed run began from a lower-fidelity state than "
                      "its key described"),
            "status": "UNPROVEN — this campaign is what would test it",
            "v2_residual_gap_s": {"q10": -7.73, "q50": -80.62, "q90": -138.97},
            "mechanism_under_test": (
                "the prefix snapshot command now carries exactly one "
                "--save-state.rng true and one --save-state.precision 16, "
                "derived from the cache constants the identity records"),
            "refutation_condition": (
                "if the objective still differs after preserved-accumulator "
                "aggregation with these settings applied, the hypothesis is "
                "REFUTED, the residual has a third cause, and the campaign "
                "fails honestly"),
            "note": ("LUNA-WARM-07's gap is currently UNEXPLAINED rather than "
                     "explained; the boundary-active explanation was refuted by "
                     "measurement, not confirmed"),
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
            # The ledger must list EVERY inherited physical fact, including the
            # network: route safety describes the parent's network, so omitting
            # it left the ledger describing less than the manifest actually
            # inherits.
            "fields": ["route_safety", "archive_files_sha256",
                       "demand_requirement", "network_requirement"],
            "reason": ("physical facts about the same frozen archive and the "
                       "same frozen closure; re-deriving them would require "
                       "reading the archive and could yield different values "
                       "for an identical case"),
        },
        "supersedes": {
            "campaign": parent["campaign_version"],
            "content_key": PARENT_CONTENT_KEY,
            "outcome": ("EXECUTED by LUNA-WARM-10 and failed honestly: coverage "
                        "complete and zero mismatches, but all three warm arms "
                        "fell back to cold, so the comparison was cold-versus-"
                        "cold and warm_executions was 0. The evidence could not "
                        "say which of eleven decline paths fired"),
            "also_spent": ["v1", "v2", "v3"],
        },
        "warming_default": ("OFF. This manifest authorizes nothing; warming "
                            "stays opt-in until one fresh approved paired "
                            "campaign passes"),
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
    parser.add_argument("--write", action="store_true",
                        help="publish the manifest; refuses to overwrite")
    parser.add_argument("--verify", action="store_true",
                        help="recompose and compare; never writes")
    args = parser.parse_args(argv)
    if args.write == args.verify:
        raise SystemExit("pass exactly one of --write or --verify")
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
