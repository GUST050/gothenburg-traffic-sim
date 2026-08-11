# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Closure-search evidence gates: review of the product
  integration, a real benchmark runner, archive-driven case discovery, and the
  five-category independent-vs-continuous outcome`
- Status: `IMPLEMENTED, REVIEWED AND GREEN; EVERY RELEASE GATE REMAINS OPEN.
  The review found one defect that voided the durability claim: every resume of
  a real cost-ordered search failed closed, because IndependentDailyRunner
  suppresses per-parent pilot artifacts and the resume then demanded evidence
  that had deliberately never been written. Reproduced, then fixed by disabling
  compaction whenever a cost source is present — cost-first execution simulates
  only the boundary set, so the file count compaction exists to bound does not
  arise; the exhaustive path still compacts. execution_record was also dead
  code and cost_ordered_result was discarded, so no artifact or result
  distinguished a cost-ordered run or carried its stop proof; both are
  published now. A third suspected defect (an orphan cursor stranding a resume)
  turned out not to be one — workspace verification refuses unledgered
  artifacts on load — and the speculative fix was reverted and pinned in both
  directions. tools/cost_ordered_benchmark.py --run is implemented and drives
  bindings, both arms, the comparison, the gates and the outcome; benchmark
  cases are now discovered from exact product-resolvable demand envelopes; the
  independent-vs-continuous outcome reports five categories. Codex review
  additionally fixed worktree/network identity, single-day discovery,
  real-archive validation, failed-outcome publication and Darwin libsumo
  detection. The frozen real v2 benchmark selected 13 schedules on 2027-03-22,
  but its first exhaustive SUMO verification hit the unchanged 300 s timeout;
  the separate outcome records `failed_execution` and all gates false. The
  runtime review found that independent-day cold runs simulated the archive
  tail and then aborted the whole search on a candidate-local SUMO timeout.
  Cold independent runs now start at the envelope midnight, use `flush=0`,
  bind the trimmed window into baseline caches, and record a SUMO timeout as a
  candidate hard failure. The frozen v2 outcome is unchanged; a diagnostic
  rerun still found a later 07:15 candidate that exceeds 300 s.`
- Suggested next action: `Preregister a NEW v3 with the corrected source
  digests, then run it without --allow-drift. Do not raise the timeout or edit
  v2. Held-out remains gated behind a passing discriminating benchmark.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker, ordering
  thresholds, finalist selection or certificate. Do not weaken equivalence,
  provenance, health, survivability, failure-recall, regret, resource or
  held-out gates. Do not activate policy v3, the cost-ordered screening mode,
  UI exposure or a global-best claim before the discriminating benchmark and
  held-out gates actually pass. Do not raise the 100,000-parent or
  10,000-unit caps, and do not multiply worker pools past the approved seed
  budget. Do not raise _CONTINUOUS_MAX_WORKDAYS to make a benchmark run — see
  docs/plans/CONTINUOUS_CLOSURE_CEILING_2026-08-11.md. Do not overwrite any
  frozen registration or historical outcome; write a NEW version. Do not freeze
  a registration that selected no case. Do not install libsumo or obtain new
  external calibration data without explicit user authority; the 2026-07-20
  no-further-external-data decision stands. Do not fabricate demand, held-out,
  benchmark or microsimulation evidence.`
- Updated: `Codex runtime review over f078b64. Focused suites: 175 runtime/
  demand tests passed; API: 126
  passed with loopback permission; survivability reproduces byte-for-byte.
  The real v2 registration and failed outcome are immutable, diagnostic and
  release_evidence=false. Darwin libsumo preflight v2 correctly finds SUMO
  1.27.1 and libsumocpp.dylib but no Python binding. git diff --check clean.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-EVIDENCE-GATES — Review, benchmark runner, discovery, IVC v2

- Status: `IMPLEMENTED, REVIEWED AND TESTED. The real v2 benchmark was frozen
  before outcome and attempted; it failed on the first arm's unchanged 300 s
  SUMO timeout. No held-out run or activation followed.`
- Objective and scope: `Review commits 16046fe..fe8b3d4 and repair what is
  wrong; implement a real --run that executes and compares both arms; discover
  benchmark cases from archive metadata rather than hand-written dates; run
  held-out validation only if the benchmark passes; re-report
  independent-vs-continuous with a fifth category.`
- Completion outcome: `Three review findings, one of them serious and
  reproduced before fixing. cost_ordered_benchmark.py --run now verifies every
  bound digest, runs both arms under one workspace lock into separate roots,
  compares the published cost ledger for EVERY candidate rather than the two
  both arms simulated, re-derives the stop proof against its own vocabulary,
  injects a fault and requires the resumed run to reproduce the uninterrupted
  outcome, and writes a separate immutable record. tools/product_arm.py is the
  single place an arm is constructed, out of the CLI's own helpers.
  --from-archives discovers cases from demand_meta.json around dates that exist
  and roads that survive their own closure, and refuses to freeze an empty
  registration. The IVC harness reports five categories with a cross-cutting
  pairing verdict.`
- Context or checkpoints: `The primary dev root contains 51 archive directories
  but only exact product validation may declare one runnable. Discovery now
  accounts for multi-day warm-up envelopes and selected build
  5ac74750843384b3 for 2027-03-22. The 13-candidate v2 registration is frozen;
  its first exhaustive SUMO run timed out after 300 s at seed 1000. Held-out
  validation remains gated and did not run.`
- Primary files: `NEW tools/product_arm.py,
  tests/test_cost_ordered_execution_review.py,
  tests/test_cost_ordered_benchmark_run.py,
  tests/test_cost_ordered_benchmark_discovery.py,
  validation/independent_vs_continuous_outcome_v2.json,
  validation/cost_ordered_benchmark_registration_v2.json,
  validation/cost_ordered_benchmark_outcome_v2.json and
  validation/libsumo_preflight_v2.json. MODIFIED
  monthly_search.py, cost_ordered_execution.py, run_monthly_closure_search.py,
  tools/cost_ordered_benchmark.py, tools/measure_independent_vs_continuous.py,
  ARCHITECTURE.md, IMPROVEMENT_PLAN.md, the scaling plan, TASKS.md,
  AGENT_NOTES.md. UNCHANGED: cost_ordered_search.py, every frozen registration
  and the v1 outcomes.`
- Constraints and safety: `Nothing was activated. Policy v3, global-best and UI
  claims are closed. No frozen artifact was edited — the v1 benchmark
  registration and the v1 IVC outcome are byte-identical, and v2 is a new file.
  No demand was built, no held-out campaign was run, no libsumo was installed,
  no external data was requested, _CONTINUOUS_MAX_WORKDAYS is unchanged and
  both resource caps are unchanged.`
- Acceptance criteria: `A crashed cost-ordered search with the deployed runner
  resumes and reproduces the uninterrupted answer; the saving and stop proof
  are durable and a re-entered pilot cannot report a different one; --run
  refuses drifted bindings, compares every ledger-priced candidate, and fails
  when a price is poisoned; discovery selects without consulting an outcome and
  refuses to freeze nothing; every measurement writes a separate versioned
  record.`
- Useful checks: `pytest -q on the fifteen focused modules (300 passed);
  tests/test_serve.py (126 passed); python3 tools/verify_closure_cost_ordering_golden.py
  --verify; python3 tools/screen_closure_survivability.py --verify;
  git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
