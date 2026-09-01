# Sonnet High continuation after a quota-terminated repair cycle

Continue the full sub-hour plan from the latest safe state. Reuse the
hash-bound READY plan from run `20260901-105635-73537`; do not resume any
terminal controller.

## What happened in run `20260901-142528-90507`

`worker-01` returned IMPLEMENTED and the controller's own checks passed
(`git diff --check` clean; 895 passed / 1 skipped). Independent
`code-review-01` then returned CHANGES_REQUIRED with two high findings:

1. `run_monthly_closure_search.py` commits the outcome at :815 but publishes
   its mandatory receipt only at :843. Process death between them leaves an
   immutable outcome with no receipt: retry refuses because the outcome
   exists (:778-779) and terminal validation rejects the missing receipt
   (`tools/ai_flow.py:2361-2364`). The fixed `.committed.tmp` path (:811) can
   likewise survive process death and permanently block retry. The existing
   interruption tests raise normally and so run `finally` cleanup; they do not
   cover process death in either commit window.
2. Reused-plan provenance was verified only once, on controller entry
   (`tools/ai_flow.py:4220-4222`). `assert_frozen_evidence` (:3463-3492) draws
   its source manifest from config globs that omit current/source `plan.json`
   and ancestry state, so a plan changed after adoption could survive
   checkpoint checks, final checks and final review.

`code-review-fix-01` (Claude Opus High) then ran 72 of its 100 turns and was
killed mid-flight by an API 429: `You've hit your session limit`. It died
BEFORE running the suite. The controller recorded ERROR and released the lock
cleanly; `code_repair_cycles` is spent, which is why this is a fresh run.

## State of the worktree — treat ALL of it as unapproved code

The interrupted fixer's partial repair is preserved:

- `run_monthly_closure_search.py` and `tests/test_subhour_cost_ordered_contracts.py`
  carry finding 1's implementation plus four kill-point regressions
  (`test_process_death_after_the_staging_commit_never_blocks_the_retry`,
  `..._before_the_receipt_commit_recovers_without_timing`,
  `..._after_the_receipt_commit_leaves_a_complete_publication`,
  `test_recovered_receipt_cannot_claim_timing_or_a_promotable_terminal`).
- `tools/ai_flow.py` carries finding 2's implementation:
  `assert_reused_plan_provenance` (:3097) persists the lineage the first time
  it is proven and raises on any later mismatch, wired into an
  `assert_plan_provenance()` helper called at 11 sites including `finish()`.

Two operator actions were taken outside the workflow and are disclosed here
for review rather than hidden:

- The dying fixer left `tests/test_subhour_cost_ordered_contracts.py` using
  `subprocess`, `sys` and `os` at lines 2647-2651 without importing any of
  them, so 5 of its own new tests failed with `NameError`. The three stdlib
  imports were added. Nothing else in that file was touched.
- `.ai-flow/config.complete-subhour.sonnet.toml` was created from the Opus
  config, differing in exactly three lines: its own self-reference in
  `source_globs` and the `model` value of the `worker` and `fixer` roles.
  `tests/test_ai_flow.py` gained
  `test_sonnet_subhour_config_keeps_the_policy_and_pins_requested_routing`,
  mirroring the Opus pin, so the new live config cannot silently carry a
  different evidence policy.

After those two actions: `git diff --check` clean, 902 passed / 1 skipped.

## What is known to be MISSING

`code-review-01` finding 2 explicitly required "a live-run regression that
mutates source or target provenance during evidence and proves the run stops
before approval or further evidence". `tests/test_ai_flow.py` did not gain one
(it stood at 109 tests before and after the fixer; the 110th is the config pin
above). Audit whether the 11 call sites genuinely cover "before and after every
mutable actor, before CODE_APPROVED freeze, checkpoint/final validation, and
terminal return", and close the gap with a real live-run regression that is
mutation-verified against the consumer path.

## Your task

Audit the preserved partial repair on evidence, keep or correct each part on
evidence, complete finding 2's missing regression, and satisfy both findings
in full. Run the configured checks, then obtain the all-findings review plus
the reserved verification review before creating any evidence.

Worker and fixer are Claude Sonnet High for this run, at the user's explicit
direction, because the Claude session limit — not model capability — is what
terminated the previous run. Planner and independent reviewer remain Codex Sol
High. No gate may be relaxed to suit the cheaper routing.

After a new CODE_APPROVED freeze, continue autonomously through fresh bounded
Phase 3-5 evidence, checkpoint review, conditionally allowed Phase 6, Phase 7
and the terminal report. Do not promote or overwrite the interrupted
generation-1 artifacts of `20260901-123040-80853`.

Preserve unrelated dirty changes and all historical/partial artifacts. Do not
weaken scientific, provenance, source-freeze, resource, routing, health,
publication or review gates. Do not delete, commit, push or deploy.
