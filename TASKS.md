# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Closure-search scaling: PR C (streaming closure schedules and
  versioned workspace ledgers) from the 2026-08-10 scaling plan`
- Status: `PR C IMPLEMENTED, MEASURED AND GREEN, WITH ONE GATE HELD OPEN ON
  PURPOSE. iter_closure_schedules streams the identical enumeration; three
  versioned NDJSON ledgers replace the parent tuple, the unit tuple and the
  reverse unit->parents graph; publication is atomic with the manifest last and
  fails closed on corruption. Semantic counts are unchanged in all six frozen
  cases (2,186/5,676 and 11,813/23,349). Streaming enumeration peaks at 1.98 MiB
  over the import baseline for 720 h (materialising: 152.86 MiB) and 5.76 MiB
  for 360 h (624.73 MiB), so 360 h no longer fails for memory — it is still
  refused by the untouched 10,000-unit cap. The plan's under-64-MiB gate is
  stated as a PROCESS TOTAL and is left OPEN: on this Linux host a fresh
  interpreter that imports independent_daily (and therefore scipy) costs
  99.9 MiB before any work, which the frozen Darwin/arm64 baseline cannot be
  compared against.`
- Suggested next action: `Re-measure the 720 h streaming peak RSS on the dev
  Darwin/arm64 machine to close the last PR C gate, then PR D's process-free
  disruption provider.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot or diagnostic
  artifacts as release evidence. Do not edit annual plan-bound inputs while
  warming is active. Do not raise the 10,000-unit cap before cost-ordering
  equivalence and resource gates pass. Do not rewrite
  validation/closure_search_scaling_baseline_v1.json — its source drift on the
  four files PR C changed is intended evidence.`
- Updated: `Claude implemented PR C on claude/closure-streaming-ledgers-pr-c.
  Combined closure/monthly suite 1,924 passed with 122 pre-existing environment
  failures reproduced identically at the untouched base commit 01a0b16 (this
  container's gitignored sumo/net.net.xml differs from the dev machine's); API
  suite 126 passed / 2026-08-10`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-SCALING-C — Streaming closure schedules and versioned ledgers

- Status: `IMPLEMENTED, MEASURED AND TESTED; every PR C exit gate passes except
  the process-total memory gate, which is held open for the dev machine.`
- Objective and scope: `PR C only from
  docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md. Stream
  the calendar, write versioned NDJSON ledgers, and remove the full
  parent/reverse-parent object graph from the new execution path while
  preserving exact behaviour and backward compatibility. No cost ordering, no
  ranking change, no PR D/E, no cap change.`
- Completion outcome: `iter_closure_schedules yields the identical enumeration
  lazily and generate_closure_schedules is its materialising wrapper.
  closure_ledgers.py writes parents.ndjson, units.ndjson and
  parent_units.ndjson in one streaming pass, publishes them atomically and
  publishes the manifest LAST as the completion signal. The reverse
  unit->parents graph is gone from the new path; StreamingDailyUnit carries no
  parent list. monthly_search opens a v1 ledger, a published streaming manifest
  or an unpublished build area, in that order, and only ever builds a
  byte-offset ParentLedgerIndex. run_monthly_closure_search screens in one
  streaming pass.`
- Context or checkpoints: `Unit identity comes from ONE implementation
  (daily_unit_records) shared by both paths, so a streamed unit hits the same v1
  cache entry — regression-tested end to end. Its schedule object is built
  lazily; building it eagerly made decompose_schedules five times more
  expensive, which was found and fixed before measuring. 122 combined-suite
  failures are pre-existing and reproduce identically at base commit 01a0b16.`
- Primary files: `NEW traffic_sim/simulation/closure_ledgers.py,
  tools/benchmark_closure_streaming.py,
  validation/closure_search_streaming_v1.json, tests/test_closure_ledgers.py,
  tests/test_benchmark_closure_streaming.py. MODIFIED
  traffic_sim/core/closure_calendar.py,
  traffic_sim/simulation/independent_daily.py,
  traffic_sim/simulation/monthly_search.py, run_monthly_closure_search.py,
  tests/test_closure_calendar.py,
  tests/test_benchmark_closure_search_scaling.py, ARCHITECTURE.md, the scaling
  plan (section 9, appended), TASKS.md, AGENT_NOTES.md`
- Constraints and safety: `The 100,000-parent and 10,000-unit caps are
  unchanged and still refuse the 360 h case. Ranking, closure_cost_v1, pilot
  selection, finalist decision, teleport policy and survivability logic are
  untouched. No frozen v1/v6/v9/v10 artifact was rewritten, no v11 created, no
  held-out campaign run and no annual warming input touched. PR A's baseline is
  history and now reports intended source drift on four files.`
- Acceptance criteria: `Identical schedule IDs and order; byte-equivalent
  to_dict(); parent->unit order and unique unit IDs unchanged; interrupted,
  missing, malformed, miscounted and mis-digested ledgers never resumed as
  valid; restart idempotent; old v1 workspaces and caches still load; a
  100,000-parent synthetic search builds no object graph; 720 h streaming under
  64 MiB on comparable hardware.`
- Useful checks: `pytest -q tests/test_closure_ledgers.py (28 passed);
  tests/test_closure_calendar.py (50, 12 new); tests/test_benchmark_closure_streaming.py
  (28); combined closure/monthly suite 1,924 passed with the 122 pre-existing
  environment failures; full API suite 126 passed;
  tools/screen_closure_survivability.py --verify (dev machine only — this
  container's sumo/net.net.xml differs); git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
