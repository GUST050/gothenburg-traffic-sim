# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Branch codex/closure-plan-finish. The authorized closure
  scaling plan and the 2026-08-13 Claude finding review are implemented.
  Release/adoption gates are closed by real outcomes, not missing
  implementation.`
- Summary: `Budget v3 separates per-invocation work from the cumulative hard
  ceiling. Parent commits are transactional; checkpoints bind exact unit and
  parent prefixes; paused screening has no shortlist and returns before SUMO.
  CLI/API/UI resume the same workspace and restore the exact form after reload.
  The 11,813-parent/23,349-unit six-month case is runnable. Cost-order v3 found
  a real calendar-order-vs-cost-order cursor bug; fixed and pinned. V4 completed
  both arms plus fault resume but saved 0/13 and ended no_viable, so adoption
  remains closed. Findings 1–5 and 12 were stale against the replaced
  checkpoint path; 6, 9–11 and 13 were hardened. Warm execution now requires
  the selected cold horizon to equal the full horizon v16 validated.`
- Files changed: `Core budget/search/API/UI sources; cost_ordered_execution;
  unit, integration, API, provenance and resume tests; ARCHITECTURE.md,
  IMPROVEMENT_PLAN.md, TASKS.md and the closure plan. New immutable evidence:
  closure_cost_ordering_golden_v2-v4, libsumo_preflight_v3, cost benchmark
  registration/outcome v3 and v4, independent_vs_continuous_outcome_v3.`
- Checks: `Post-review combined closure/API/cost-order suite: 519 passed, 1
  skipped. Golden v4 reproduces byte-for-byte; its JSON parses; git diff
  --check is clean. Historical v1-v3 records are unchanged.`
- Decisions and evidence: `V4: status/selected IDs/final decision/hard failures/
  health/restart/stopproof match, but no SUMO saving and exhaustive timeout
  records lack comparable post-SUMO cost fields. IVC v3: 35 missing-demand,
  25 unpairable, 24 unsupported, 0 measured. Libsumo v3: native library exists,
  Python binding absent.`
- Blockers or risks: `Policy v3, held-out, micro and global-best cannot be
  opened. A new attempt needs a pre-outcome, non-cherry-picked benchmark with
  multiple health-viable cases and calibrated archives matching the frozen IVC
  dates. External data/libsumo installation needs user authority.`
- Suggested next action: `None within the current authorized plan. If new
  evidence inputs are approved, create a new registration version; never edit
  v1-v4 history or select a case using observed v4 outcomes.`
- Actor notes: `No timeout raised, hard cap weakened, historical artifact
  overwritten, policy activated, held-out/micro evidence fabricated, external
  data fetched or dependency installed.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
