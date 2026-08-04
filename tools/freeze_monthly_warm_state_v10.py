"""Freeze the LIFECYCLE-SAFE paired warm-state contract v9 (LUNA-WARM-15).

Process-free: runs no SUMO, creates no run root, touches no cache, imports no
simulator, and reads NO `runs/` path at all.

WHY v9 EXISTS. v8 was correct about what a contract must BIND and wrong about
how a contract retires.

v8 fixed v7's real defect — v7 bound the resolver's own regressions but omitted
`tests/test_warm_state_boundary.py` and `tests/test_monthly_warm_state.py`, so
the tests that give the resolver meaning could have been weakened while its key
still validated. v9 keeps that fix and every rule underneath it unchanged.

What v8 got wrong was LIFECYCLE. Its regression suite pinned a predecessor's
TEST file as immutable evidence and asserted its own currency directly, so the
moment a successor landed, two immutable-history artifacts had to be edited to
stay honest. Frozen evidence that must be edited whenever the future arrives is
not frozen. v9 fixes the shape:

  * its immutable-history map pins the predecessor's TOOL and MANIFEST only —
    the artifacts that genuinely never change — never a test file, which must
    stay free to describe its own retirement;
  * its versioned suite contains no assertion that has to be rewritten merely
    because the harness default advances. Currency-dependent facts are read
    from production and adapt; which manifest is CURRENT is proved in the
    generic current suite, where that question belongs.

The resolver, controller, accounting, physical case, comparison, approval and
claim boundaries are inherited from v8 verbatim. v9 changes how a contract ages,
not how anything runs.

WHY THIS TOOL INHERITS RATHER THAN RE-DERIVES. The route-mutation audits, the
per-variant route-safe warm points and the archived-demand hashes are physical
facts about the SAME frozen archive and the SAME frozen case that v8 already
recorded. Re-deriving them would require reading the archive under `runs/`,
which this task is not permitted to touch, and would risk producing DIFFERENT
values for an identical case. The NETWORK identity is inherited for the same
reason: route safety describes the parent's network, so re-hashing the live file
could pair a changed network with stale audits. The live hash is verified
against the inherited one instead, and drift fails the freeze. They are
therefore inherited verbatim from the frozen v8 manifest — a `validation/`
artifact — and the v8 content key is bound into v9 so the inheritance itself is
tamper-evident. No `runs/` outcome is read: only tracked manifests are.

Run:  python3 tools/freeze_monthly_warm_state_v9.py --write
      python3 tools/freeze_monthly_warm_state_v9.py --verify

Do NOT verify this contract with an earlier freeze tool. v1, v2, v4, v5 and v6
are SPENT and v3, v7 and v8 are superseded candidates; live source drift makes
their `--verify` EXPECTED to fail, and that failure describes their supersession,
not v9.
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
OUT = "validation/monthly_warm_state_manifest_v10.json"
CAMPAIGN = "v10"
FROZEN_AT = "2026-08-02"

PARENT = "validation/monthly_warm_state_manifest_v9.json"
# The exact v8 bytes this campaign inherits physical facts from. Recomputed and
# compared at freeze time, so editing v8 after the fact cannot silently change
# what v9 describes.
PARENT_CONTENT_KEY = "cad9c072a0ca6f90b11bd6342a603337eeaceacc457cd0016a16a3d9fa04e7b2"

# The regressions that give the repaired resolver its meaning. v7 bound only the
# first two and was rejected for it: a contract that promises warming must bind
# the tests that prove warming's boundary can be reached, or those tests can be
# weakened while the key still validates. v8 established this set and v9 keeps
# it. Asserted against SOURCES below, so the manifest can never claim a binding
# it does not hold.
REQUIRED_REGRESSIONS = [
    "tests/test_sumo_runtime.py",
    "tests/test_monthly_sumo.py",
    "tests/test_warm_state_boundary.py",
    "tests/test_monthly_warm_state.py",
    "tests/test_monthly_warm_state_freeze.py",
    "tests/test_monthly_warm_state_v10_freeze.py",
]

# Bound sources: every file whose bytes change the meaning of the campaign.
SOURCES = [
    "traffic_sim/simulation/monthly_warm_state.py",
    "traffic_sim/simulation/warm_state_cache.py",
    # Snapshot accounting decides how a cached prefix becomes the objective and
    # what the saved state actually is. Leaving it unbound would let either
    # change under an unchanged campaign key.
    "traffic_sim/simulation/warm_state_boundary.py",
    "traffic_sim/simulation/warm_state_cache.py",
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
    "tools/freeze_monthly_warm_state_v10.py",
    # The resolver is meaning-bearing: it decides which SUMO the campaign talks
    # to at all.
    "traffic_sim/simulation/runtime.py",
    # The TESTS that prove the repair are interpreting sources too: if the
    # real-import regressions, the controller/boundary regressions, the
    # accounting regressions or the contract assertions change, what this
    # campaign key promises changes with them. v7 omitted two of these.
    *REQUIRED_REGRESSIONS,
]

CASES = [{
    "case_id": "warm-v10-paired-equivalence",
    "directed_edges": ["26354420_60476786_0"],
    "closure_begin_s": 25200,          # 07:00 on day 1
    "closure_end_s": 54000,            # 15:00 on day 1
    "closure_bound_warm_point_s": 24300,
    "rationale": ("the same physical closure every earlier campaign ran, so "
                  "the residual "
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
    """The frozen v9 manifest, verified by content key before anything is read.

    A parent whose key does not recompute is refused outright: inheriting
    physical facts from bytes that have drifted would produce a v10 campaign
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
            f"parent manifest is not the expected frozen v9 campaign "
            f"({recomputed} != {PARENT_CONTENT_KEY})")
    return parent


def verify_regression_binding() -> None:
    """The binding v7 was rejected for missing must be mechanically true.

    Checked here rather than asserted in prose: a manifest that DESCRIBES a
    complete regression set while fingerprinting less than it names would repeat
    v7's defect in a form that reads as fixed.
    """
    missing = [name for name in REQUIRED_REGRESSIONS if name not in SOURCES]
    if missing:
        raise SystemExit(
            f"required regressions are not bound as sources: {sorted(missing)}")
    absent = [name for name in REQUIRED_REGRESSIONS
              if not (ROOT / name).is_file()]
    if absent:
        raise SystemExit(f"required regressions do not exist: {sorted(absent)}")


VERSIONED_SUITE = "tests/test_monthly_warm_state_v10_freeze.py"


def verify_lifecycle_rules(suite: str = VERSIONED_SUITE) -> None:
    """Statically enforce the two rules v9 exists to establish.

    Checked mechanically rather than asserted in prose, because v8's manifest
    described a sound contract while its suite quietly coupled itself to the
    future. A rule that only the docstring knows about is not a rule.

    Rule 1 — an immutable-history map (a module-level dict of path -> sha256)
    must not pin any predecessor TEST file. Test files legitimately change when
    they describe their own retirement; pinning them makes a successor
    impossible to land without editing frozen evidence.

    Rule 2 — the versioned suite must not ASSERT that the harness default IS
    this manifest. That is true only until a successor arrives, and rewriting a
    frozen suite to keep it true is exactly the coupling being removed. `!=` is
    permitted: "no longer current" stays true forever once it is true. So is
    READING currency to branch on it — a suite that adapts is the goal — so the
    rule looks only inside `assert` statements, not at every comparison.
    """
    import ast                                          # noqa: PLC0415 — lazy
    path = ROOT / suite
    if not path.is_file():
        raise SystemExit(f"the versioned suite is missing: {suite}")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def is_digest(node):
        return (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and len(node.value) == 64
                and all(c in "0123456789abcdef" for c in node.value))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        if not any(is_digest(v) for v in node.values):
            continue                                    # not a hash map
        for key in node.keys:
            if (isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value.startswith("tests/")
                    and key.value.endswith("_freeze.py")):
                raise SystemExit(
                    f"{suite} pins a predecessor test file as immutable "
                    f"evidence ({key.value}); pin tools and manifests only")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        for inner in ast.walk(node.test):
            if not isinstance(inner, ast.Compare) or not inner.ops:
                continue
            if not isinstance(inner.ops[0], ast.Eq):
                continue
            rendered = ast.dump(inner)
            if "DEFAULT_MANIFEST" in rendered and "MANIFEST" in rendered:
                raise SystemExit(
                    f"{suite} asserts the harness default IS this manifest; "
                    f"that expires when a successor lands. Prove currency in "
                    f"the generic current suite instead")


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
    from traffic_sim.simulation.runtime import (
        REQUIRED_TRACI_API, REQUIRED_TRACI_NAMESPACES, TOOLS_DIRNAME,
        TRACI_PACKAGE)
    from traffic_sim.simulation.warm_state_cache import (
        STATE_PRECISION, STATE_RNG_SAVED)

    # A campaign whose accounting has an unclassified production field must not
    # be freezable at all.
    verify_field_partition()
    # Nor one that repeats the omission v7 was rejected for.
    verify_regression_binding()
    # Nor one whose suite would force edits to frozen evidence later.
    verify_lifecycle_rules()

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

    # The case must be the SAME physical closure the parent measured, or the
    # inherited route safety would describe a different filtering problem.
    parent_case = parent["cases"][0]
    for field in ("directed_edges", "closure_begin_s", "closure_end_s",
                  "closure_bound_warm_point_s"):
        if CASES[0][field] != parent_case[field]:
            raise SystemExit(
                f"case field {field!r} differs from the parent campaign "
                f"({CASES[0][field]!r} != {parent_case[field]!r}); inherited "
                f"route safety would not describe this closure")

    # The identity set must match the parent's too: v8 changes what the contract
    # BINDS, not what it would run.
    if frozen_schedule_ids != parent["frozen_schedule_ids"]:
        raise SystemExit(
            f"frozen schedule ids differ from the parent campaign "
            f"({frozen_schedule_ids} != {parent['frozen_schedule_ids']}); v8 "
            f"inherits the parent's physical case and must run the same one")

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
        # The resolver decides WHICH SUMO the campaign talks to, so its rules
        # are part of the campaign identity. v6's key promised warming and could
        # not import TraCI at all. Carried from v8 verbatim: v9 changes how a
        # contract retires, never its runtime rules.
        "traci_resolution": {
            "package": TRACI_PACKAGE,
            "tools_dirname": TOOLS_DIRNAME,
            "rule": ("TraCI is imported from the exact active <sumo_home>/tools "
                     "and the imported module's origin MUST resolve inside that "
                     "installation's traci package; a module from anywhere else "
                     "is refused"),
            "home_rule": ("a non-empty SUMO_HOME wins deterministically; a "
                          "declared-but-unusable home is an error, never a "
                          "silent fallback to a different installation"),
            "required_api": list(REQUIRED_TRACI_API),
            "required_namespaces": {k: list(v) for k, v in
                                    sorted(REQUIRED_TRACI_NAMESPACES.items())},
            "preflight_order": ("approval token -> TraCI resolve/API check -> "
                                "artifact-root absence -> campaign; a resolver "
                                "failure creates no root and runs no campaign"),
            "controller_rule": ("the controller resolves before the launcher "
                                "and before any port, so an unusable TraCI "
                                "starts no process"),
        },
        # What v7 was rejected for, recorded so the next reader does not have to
        # reconstruct it from a diff.
        "lifecycle_rules": {
            "immutable_history_pins": ("a campaign's immutable-history map "
                                       "pins predecessor TOOL and MANIFEST "
                                       "bytes only; predecessor TEST files are "
                                       "never pinned, because a retiring suite "
                                       "must stay free to describe its own "
                                       "supersession"),
            "no_currency_assertions_in_versioned_suites": (
                "a versioned suite contains no assertion that must be "
                "rewritten solely because the harness default advanced; "
                "currency-dependent facts are read from production, and which "
                "manifest is current is proved in the generic current suite"),
            "drift_is_the_retirement_mechanism": (
                "a superseded contract's fingerprints drift and its load fails "
                "closed; that drift is never repaired or re-synced"),
            "why": ("v8 pinned a predecessor test file and asserted its own "
                    "currency, so a successor could not land without editing "
                    "frozen evidence. Evidence that must be edited when the "
                    "future arrives is not frozen"),
            "enforced_at_freeze": True,
        },
        "regression_binding": {
            "rule": ("every regression that proves the resolver, the controller "
                     "boundary, the warm accounting or the campaign contract is "
                     "fingerprinted, so weakening one invalidates this key"),
            "required": list(REQUIRED_REGRESSIONS),
            "why": ("v7 bound the resolver's own regressions but omitted "
                    "tests/test_warm_state_boundary.py and "
                    "tests/test_monthly_warm_state.py, so those could have been "
                    "weakened while its key still validated. v8 closed that gap "
                    "and v9 inherits it unchanged"),
            "enforced_at_freeze": True,
        },
        "v6_diagnosis": {
            "campaign": "v6",
            "observed": ("cache_miss -> bootstrap_started -> snapshot_failed"
                         "[No module named 'traci', ModuleNotFoundError] -> "
                         "bootstrap_failed, on all three identities"),
            "cause": ("production did a bare `import traci`; the package ships "
                      "inside the installation at <sumo_home>/tools/traci and "
                      "is not on sys.path"),
            "consequence": ("warm execution never started once: every warm arm "
                            "since the controller was introduced was a cold "
                            "fallback caused by an import error"),
            "warm_executions": 0,
            "campaigns_spent_narrowing_it": ["v4", "v5", "v6"],
        },
        "v9_result": {
            "campaign": "v9",
            "content_key": PARENT_CONTENT_KEY,
            "disposition": "executed_failed",
            "executed": "once, under LUNA-WARM-16, with exact user approval",
            "outcome": ("warm execution SUCCEEDED for the first time in this "
                        "family — all three identities reached warm_completed — "
                        "and the paired comparison FAILED: 3 comparisons, 3 "
                        "mismatches, no cache published"),
            "residual_s": {"q10": -7.73, "q50": -80.62, "q90": -138.97},
            "refuted": ("the state-serialization hypothesis. v9 applied "
                        "--save-state.rng true and --save-state.precision 16 "
                        "and the residual was bit-identical to v2's, which "
                        "predates those settings entirely"),
            "performance": ("cold 91.38 s vs warm 103.15 s for the same "
                            "schedule; warm was slower, as expected while every "
                            "identity is a cache miss paying for its own prefix"),
        },
        "luna_warm_22_localization": {
            "diagnostic": "monthly_warm_state_residual_v2",
            "contract_content_key":
                "03f5260af470a4c29b17216129c145e06e39df6b3fe35b6f38f85a07c946f908",
            "finding": ("the residual is carried entirely by a MINORITY of the "
                        "vehicles in flight across the warm point"),
            "affected_of_in_flight": {"q10": "5 of 44", "q50": "10 of 50",
                                      "q90": "12 of 51"},
            "affected_of_population": {"q10": "5 of 84065",
                                       "q50": "10 of 86754",
                                       "q90": "12 of 89482"},
            "deltas_sum_to_the_residual_exactly": True,
            "all_negative": True,
            "all_in_resumed_phase": True,
            "most_lose_everything": ("1 of 5, 8 of 10 and 10 of 12 restored "
                                     "with EXACTLY 0.0 accumulated time loss"),
            "partition_was_clean": ("no missing, extra, overlapping or "
                                    "misplaced vehicles in any identity"),
            "selection_mechanism": "UNKNOWN and not required by this correction",
        },
        "restore_correction": {
            "rule": ("measure each active vehicle's accumulated time loss at "
                     "the save instant and again immediately after the load, "
                     "before any resumed step, and add back ONLY the observed "
                     "positive difference, once, to that vehicle's own resumed "
                     "record at production reporting precision"),
            "save_ledger_schema": "warm_save_ledger_v1",
            "restore_audit_schema": "warm_restore_audit_v1",
            "prefix_evidence_schema": "monthly_prefix_evidence_v4",
            "why_not_a_blanket_offset": (
                "v3 added a per-vehicle offset for every vehicle in flight. "
                "LUNA-WARM-22 measured that ~80% of them keep their accumulator "
                "intact, so a blanket offset would double count the majority"),
            "why_not_no_offset": (
                "LUNA-WARM-08 probed ONE vehicle and found its accumulator "
                "preserved. That generalised to a universal rule loses the "
                "measured minority, which is the entire residual"),
            "nothing_is_inferred": (
                "an unmeasured vehicle is never corrected; a vehicle whose "
                "value survived is left byte-semantically unchanged"),
            "refused": ["a restored value ABOVE its saved value",
                        "missing, extra or duplicate identity coverage",
                        "a deficit with no final resumed record",
                        "non-finite or negative values",
                        "a ledger or audit whose digest does not recompute",
                        "legacy evidence schemas"],
        },
        "hypothesis_under_test": {
            "claim": ("the v9 residual is exactly the accumulated time loss "
                      "that a measured minority of in-flight vehicles lose "
                      "across save/load, and restoring only those measured "
                      "deficits makes the warm arm reproduce the cold arm"),
            "status": "UNPROVEN — this campaign is what would test it",
            "refutation_condition": (
                "if the objective still differs after each measured deficit is "
                "restored once, the correction is incomplete or wrong, the "
                "residual has a further cause, and the campaign fails honestly"),
            "known_unknown": ("why those particular vehicles lose their "
                              "accumulator is not explained by any evidence so "
                              "far, and this correction does not require it"),
        },
        "v7_review": {
            "campaign": "v7",
            "content_key": "e6734a2029995fc86092572ee396b6057bf3a1e9351d6ba4876731092050c666",
            "disposition": "rejected_unapproved_unexecuted",
            "found_by": "process-free Sol review, no campaign spent",
            "defect": ("incomplete regression binding: the fingerprint set "
                       "omitted tests/test_warm_state_boundary.py and "
                       "tests/test_monthly_warm_state.py"),
            "correct_in_v7": ("the resolver repair, the origin proof, the "
                              "pre-root preflight ordering and the real-import "
                              "regression, all carried forward unchanged"),
            "probe": ("one audit-guarded import-only probe of the installed "
                      "package passed under v7 and is CONSUMED evidence: the "
                      "resolved origin lay inside the active "
                      "<sumo_home>/tools/traci and the required API was "
                      "complete. It has not been rerun and proves nothing "
                      "about warm execution, which has still never occurred"),
        },
        "v8_review": {
            "campaign": "v8",
            "content_key": PARENT_CONTENT_KEY,
            "disposition": "superseded_unapproved_unexecuted",
            "found_by": "process-free Sol review, no campaign spent",
            "defect": ("lifecycle coupling, not a runtime fault: v8's suite "
                       "pinned a predecessor TEST file as immutable evidence "
                       "and asserted its own currency directly, so a successor "
                       "landing forced edits to artifacts that are supposed to "
                       "be frozen"),
            "correct_in_v8": ("the complete regression binding and its "
                              "freeze-time enforcement, carried into v9 "
                              "unchanged"),
            "remedy": ("immutable-history maps pin predecessor TOOL and "
                       "MANIFEST bytes only; currency-dependent facts are read "
                       "from production and proved in the generic current "
                       "suite, so a versioned suite never needs rewriting "
                       "merely because the default advanced"),
        },
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
        # is verified against the inherited one above instead, so drift fails
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
        # below so the inheritance cannot be edited without invalidating v8.
        "route_safety": parent["route_safety"],
        "archive_files_sha256": dict(parent["archive_files_sha256"]),
        "inherited_from": {
            "manifest": PARENT,
            "content_key": PARENT_CONTENT_KEY,
            # The ledger lists EVERY inherited physical fact, including the
            # network: route safety describes the parent's network, so omitting
            # it would leave the ledger describing less than the manifest
            # actually inherits.
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
            "outcome": ("EXECUTED once under LUNA-WARM-16 with exact user "
                        "approval and FAILED honestly: warming ran for the "
                        "first time, all three identities warmed, and the "
                        "paired comparison mismatched on all three. No cache "
                        "was published"),
            "also_spent": ["v1", "v2", "v4", "v5", "v6"],
            "also_superseded_unexecuted": ["v3", "v7", "v8"],
        },
        "execution_history": {
            "warm_executions_to_date": 3,
            "note": ("v9 is the first campaign in this family whose warm arm "
                     "actually executed: three identities reached "
                     "warm_completed under LUNA-WARM-16. v4, v5 and v6 ran and "
                     "fell back cold on an import error; v3, v7, v8 and v10 "
                     "have never run. Warm execution is no longer unprecedented "
                     "— warm EQUIVALENCE is still unproven"),
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
