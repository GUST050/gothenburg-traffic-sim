# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Objective-aligned rolling road-closure period optimization
  across multi-month ranges, with the completed closure-integrity fix now
  integrated into the current branch`
- Status: `CLOSURE INTEGRITY INTEGRATED; MULTI-MONTH V2 IMPLEMENTED AND COMBINED
  TESTS GREEN. The branch now contains the reviewed Stage 3 teleport policy,
  corrected Stage 4 survivability screen and frozen held-out v10. The local
  rolling-period work was reapplied above it without code conflicts. It compares
  periods of up to 90 workdays across month boundaries with one fixed daily
  closure window and keeps policy v2 provisional. The historical v10 manifest
  correctly reports source drift against the still-uncommitted rolling changes;
  it was not rewritten.`
- Suggested next action: `Execute PR A/B from
  docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md: freeze
  the scaling baseline, implement the exact read-only preflight, differential-
  test it against calendar enumeration, and expose it in API/UI. Then proceed
  to streaming ledgers and pre-SUMO deterministic cost ordering before the
  discriminating policy benchmark.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot artifacts as release
  evidence. Do not edit annual plan-bound inputs while warming is active. Do
  not raise the 10,000-unit cap before streaming/cost-ordering equivalence and
  resource gates pass.`
- Updated: `Closure commits 11fc68b..03ca5d7 fast-forward-integrated while no
  warming/SUMO process was active; local rolling-period work restored above the
  integration. Combined verification passes: 531 focused tests plus 115 API
  tests; 5 skipped and the known historical v6 drift check deselected. The v2
  survivability artifact reproduces byte-for-byte / 2026-08-10`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-OBJECTIVE-V2 — Align ranking and compare rolling periods

- Status: `IMPLEMENTED, including multi-month rolling periods; first isolated
  benchmark passed diagnostically; closure-integrity dependency integrated;
  combined verification, scaling execution, discriminating benchmark and
  held-out validation pending`
- Objective and scope: `Prevent the pilot from discarding the true
  closure-cost winner, bind the objective explicitly in policy, and carry
  deterministic q10/q50/q90 disruption evidence through monthly and
  independent-day execution without rewriting v1 evidence. Allow the user to
  compare periods from eight through 90 workdays across several months without forcing
  any schedule into a calendar week, while keeping the exact same start and
  end time on every selected workday.`
- Completion outcome: `Pilot and final selection both use worst-variant added
  vehicle-hours, then exact added-distance/affected-vehicle tie-breakers;
  no-detour candidates are refused and missing v2 evidence fails closed.`
- Context or checkpoints: `The tracked v1 golden content key is preserved.
  The pinned benchmark in validation/monthly_search_v2_benchmark_v1.json ran
  on the original immutable archive, produced result SHA-256 a7ee1242… and
  resumed without new work. Two lower-cost candidates failed the teleport hard
  gate, leaving only one viable candidate; therefore it verifies execution but
  cannot calibrate an equivalence band or license production/UI claims. The
  closure-integrity implementation is now part of this branch: closure runs use
  the explicit no-teleport policy and held-out v10 filters topologically fatal
  candidates. That v10 artifact remains immutable historical evidence for
  03ca5d7: current rolling changes alter its fingerprinted closure_calendar.py
  and monthly_search.py, so a future untouched campaign must use a new version
  after those sources stabilize. The scaling plan first adds exact preflight
  and streaming ledgers, then proves a deterministic cost-ordered SUMO scan
  against exhaustive selection.`
- Primary files: `traffic_sim/simulation/{pilot_selection,finalist_decision,
  monthly_search,monthly_sumo,monthly_demand,independent_daily,
  period_comparison}.py,
  suggest_closure_time.py, run_monthly_closure_search.py,
  validation/monthly_search_policy_v2.json,
  tools/run_monthly_v2_benchmark.py,
  validation/monthly_search_v2_benchmark_{plan_v1,v1}.json,
  docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`
- Constraints and safety: `Do not alter annual warming inputs/artifacts. Do not
  promote v2 until a named benchmark and new untouched held-out evidence pass.
  Keep closure teleport/survivability provenance in all derived cache keys.`
- Acceptance criteria: `Objective is identical in pilot and final; monthly and
  daily evidence survives serialization/cache boundaries; rolling periods may
  cross week/month boundaries and retain exact dates; exact secondary ties
  resolve; legacy v1 identity remains stable; regression tests pass.`
- Useful checks: `pytest objective/monthly/daily/SUMO/period/benchmark tests
  plus closure-integrity suites (531 passed, 5 skipped, historical v6 drift
  check deselected); full test_serve.py with loopback allowed (115 passed);
  survivability v2 byte verification and git diff --check pass. Benchmark wall
  318.18 s; completed-result reload 0.0 s and identical a7ee1242… result hash.
  v10 --verify intentionally reports manifest source drift until the rolling
  changes receive a new immutable held-out campaign.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
