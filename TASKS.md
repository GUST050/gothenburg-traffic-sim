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
  cases are now discovered from archive metadata; the independent-vs-continuous
  outcome reports five categories.`
- Suggested next action: `On the host holding the calibrated archive library:
  `python3 tools/cost_ordered_benchmark.py --preregister --from-archives
  --runs-root <root> --registration
  validation/cost_ordered_benchmark_registration_v2.json`, then `--run --out
  validation/cost_ordered_benchmark_outcome_v2.json`. If and only if all eleven
  gates pass, freeze and run an untouched held-out campaign. Re-run
  measure_independent_vs_continuous.py there too with a v3 outcome.`
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
- Updated: `Branch claude/closure-evidence-gates on top of fe8b3d4. Focused
  suites: 297 passed (deterministic disruption 32, cost-ordered search 77,
  cost-ordered execution 21, execution review 10, benchmark registration 18,
  benchmark run 21, benchmark discovery 18, import cost 4, progress 10, policy
  v3 9, PR H pre-registration 17, IVC harness 27, libsumo preflight 11, golden
  4, independent daily 18). tests/test_serve.py: 126 passed. The affected sweep
  fails 181, an identical set to the recorded 73f5116 baseline — no failure is
  introduced. git diff --check clean.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-EVIDENCE-GATES — Review, benchmark runner, discovery, IVC v2

- Status: `IMPLEMENTED, REVIEWED AND TESTED. The evidence PIPELINE is complete
  and exercised end to end; the evidence itself is not produced, because this
  container holds no calibrated archive library. Nothing was activated.`
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
- Context or checkpoints: `The named data root /Users/gt/Documents/gs-project/
  runs does not exist in this container — there is no /Users at all, and a
  whole-filesystem search finds calibrated route files only under pytest temp
  directories. No v2 benchmark registration is committed: freezing one from
  zero archives would repeat exactly the v1 mistake this work exists to fix.
  Held-out validation is gated behind a passing benchmark and did not run.`
- Primary files: `NEW tools/product_arm.py,
  tests/test_cost_ordered_execution_review.py,
  tests/test_cost_ordered_benchmark_run.py,
  tests/test_cost_ordered_benchmark_discovery.py,
  validation/independent_vs_continuous_outcome_v2.json. MODIFIED
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
- Useful checks: `pytest -q on the fifteen focused modules (297 passed);
  tests/test_serve.py; python3 tools/verify_closure_cost_ordering_golden.py
  --verify; python3 tools/screen_closure_survivability.py --verify;
  git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
