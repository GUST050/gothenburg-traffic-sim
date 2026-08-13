# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Main. The closure scaling plan, real multi-month product
  E2E and next preregistered cost-order benchmark are executed.
  Release/adoption gates are closed by real outcomes, not missing
  implementation.`
- Summary: `Budget v3 separates per-invocation work from the cumulative hard
  ceiling. Parent commits are transactional; checkpoints bind exact unit and
  parent prefixes; paused screening has no shortlist and returns before SUMO.
  CLI/API/UI resume the same workspace and restore the exact form after reload.
  The 11,813-parent/23,349-unit six-month case is runnable. Cost-order v3 found
  a real calendar-order-vs-cost-order cursor bug; fixed and pinned. The v5
  outcome-blind four-case suite saved 18 verifications and had three cases with
  two health-viable pilots, but failed strict equivalence because wall-clock
  timeouts changed health/finalists in two cases and timeout observations lacked
  comparable post-SUMO cost fields. Adoption remains closed. Warm execution
  still requires the selected cold horizon to equal the full horizon v16
  validated.`
- Files changed: `Core budget/search/API/UI sources; cost_ordered_execution;
  multi-case benchmark harness and directed-edge workspace identity fix; unit,
  integration, API, provenance and resume tests; architecture, plan and current
  coordination docs. New immutable evidence includes the product E2E and cost
  benchmark registration/outcome v5; v1-v4 remain history.`
- Checks: `Post-review combined closure/API/cost-order suite: 519 passed, 1
  skipped. Benchmark/provenance suite: 103 passed, 1 skipped. API suite: 127
  passed. Golden v4 reproduces byte-for-byte; v5 registration verifies without
  drift; JSON and diff checks are clean.`
- Decisions and evidence: `V5: four cases, two dates, four roads, 18 SUMO
  verifications saved, three health-discriminating cases and four valid stop
  proofs. One case retained an identical winner while saving 11/13; two cases
  changed timeout/health/finalist/restart evidence across arm order; one was
  strictly identical but saved 0. IVC v3: 35 missing-demand,
  25 unpairable, 24 unsupported, 0 measured. Libsumo v3: native library exists,
  Python binding absent.`
- Blockers or risks: `Policy v3, held-out, micro and global-best cannot be
  opened. V5 shows that the fixed wall-clock timeout is not classification-
  stable under arm order and that the exhaustive path lacks deterministic cost
  fields after some timeouts. A new v6 must be preregistered only after those
  issues are addressed without raising the timeout or weakening gates.
  Calibrated archives for frozen IVC dates and libsumo remain unavailable.`
- Suggested next action: `Preserve v5 as negative evidence. Design and test
  pre-SUMO exhaustive cost recording plus a paired runtime method, then freeze
  a new v6 before any outcome. Never edit v1-v5 history or choose cases from
  observed outcomes.`
- Actor notes: `No timeout raised, hard cap weakened, historical artifact
  overwritten, policy activated, held-out/micro evidence fabricated, external
  data fetched or dependency installed.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
