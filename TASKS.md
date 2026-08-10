# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Closure-search scaling: PR C (streaming closure schedules and
  versioned workspace ledgers), independently reviewed and corrected`
- Status: `IMPLEMENTATION GREEN; ONE DOCUMENTED EXIT GATE REMAINS OPEN. Claude's
  streaming calendar, atomic versioned ledgers, v1 compatibility and minimal
  parent index are intact. Review added the missing exact preflight before an
  independent-exhaustive product run can write ledgers, enforces the total
  parent cap even when some parents are unavailable, and checks preflight counts
  against the streamed enumeration. All six frozen cases retain identical
  counts (2,186/5,676 and 11,813/23,349). On comparable Darwin/arm64, streaming
  adds 1.33 MiB for 720 h and 7.00 MiB for 360 h, but the 720 h process total is
  78.02 MiB, so the explicit under-64-MiB process gate is still OPEN.`
- Suggested next action: `Choose and implement a real reduction of fixed import
  RSS if the 64-MiB process-total gate must pass; otherwise review and explicitly
  revise that gate before beginning PR D. Another measurement alone cannot close
  it.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot or diagnostic
  artifacts as release evidence. Do not edit annual plan-bound inputs while
  warming is active. Do not raise the 10,000-unit cap before cost-ordering
  equivalence and resource gates pass. Do not rewrite
  validation/closure_search_scaling_baseline_v1.json — its source drift on the
  four files PR C changed is intended evidence.`
- Updated: `Codex review branch codex/review-closure-streaming-pr-c over Claude
  commit 1080ac7. Focused closure/monthly/preflight checks pass 301/301; API 126/126;
  survivability artifact reproduces byte-for-byte. One unrelated brittle
  warm-horizon source-text test also fails on base 01a0b16. Darwin comparison
  artifact regenerated with current source hashes / 2026-08-10.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-SCALING-C — Streaming closure schedules and versioned ledgers

- Status: `IMPLEMENTED, REVIEWED AND TESTED; semantic/restart gates pass, while
  the comparable process-total memory gate remains open at 78.02 MiB.`
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
  byte-offset ParentLedgerIndex. run_monthly_closure_search preflights supported
  independent-exhaustive specs before ledger creation, then screens in one
  streaming pass.`
- Context or checkpoints: `Unit identity comes from ONE implementation
  (daily_unit_records) shared by both paths, so a streamed unit hits the same v1
  cache entry — regression-tested end to end. Its schedule object is built
  lazily; building it eagerly made decompose_schedules five times more
  expensive, which was found and fixed before measuring. Review also fixed the
  previously late cap refusal, a flaky small-fixture RSS assertion and a
  misleading comparable-host gate explanation.`
- Primary files: `NEW traffic_sim/simulation/closure_ledgers.py,
  tools/benchmark_closure_streaming.py,
  validation/closure_search_streaming_v1.json, tests/test_closure_ledgers.py,
  tests/test_benchmark_closure_streaming.py. MODIFIED
  traffic_sim/core/closure_calendar.py,
  traffic_sim/simulation/independent_daily.py,
  traffic_sim/simulation/monthly_search.py, run_monthly_closure_search.py,
  tests/test_closure_calendar.py,
  tests/test_benchmark_closure_search_scaling.py, tests/test_independent_daily.py,
  ARCHITECTURE.md, the scaling plan (section 9), TASKS.md, AGENT_NOTES.md`
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
- Useful checks: `Focused PR C set 161 passed before final review additions;
  expanded monthly/closure/calendar/preflight set 301 passed, one unrelated
  base-existing brittle warm-horizon test deselected; benchmark contract included;
  full API 126 passed with loopback permission; survivability byte verification
  true; git diff --check clean.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
