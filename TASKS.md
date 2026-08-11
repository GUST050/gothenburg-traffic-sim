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

### CLOSURE-EVIDENCE-V3 — Benchmark provenance, then a v3 registration

- Status: `SOURCE WORK COMPLETE AND GREEN. The v3 registration and run are NOT
  produced: this container is not the machine that holds the archive library.
  Nothing was activated.`
- Objective and scope: `Fix the registration's provenance defects (outcome path,
  source seal, schema), then freeze and run a v3 cost-ordered benchmark against
  the corrected runtime.`
- Completion outcome: `Registration schema v3. outcome_record binds the
  caller's --out instead of the tool's default, and a run refuses to write an
  outcome its registration disowns. sources seals every project module on the
  arms' real import path (48 files) instead of ten chosen by hand; two of them
  (heldout_gate.py, proxy_validation.py) are imported lazily and decide the
  claim boundary, and were found by the closure test rather than the audit. v2
  stays readable and a v2 registration still produces a v2-schema outcome.`
- Context or checkpoints: `The v2 timeout is diagnosed and its cause is visible
  in the frozen registration with nothing running: a single-work-date case whose
  daily unit declares a one-day envelope resolves to the canonical THREE-day
  archive 5ac74750843384b3 (n_intervals 288), so SUMO ran 72 hours to observe a
  5-hour closure. adf765b bounds that. That same correction changed
  monthly_sumo.py, which the FROZEN golden record binds — so
  tests/test_closure_cost_ordering_golden.py's source-digest test now fails at
  adf765b, before any change of this branch. It needs a deliberate re-freeze on
  a host with the archives; do not edit the frozen record.`
- Primary files: `NEW tests/test_cost_ordered_benchmark_provenance.py (25
  tests). MODIFIED tools/cost_ordered_benchmark.py, two benchmark test modules,
  ARCHITECTURE.md, IMPROVEMENT_PLAN.md, TASKS.md, AGENT_NOTES.md. UNCHANGED:
  every frozen v1/v2 registration and outcome, byte for byte.`
- Constraints and safety: `Nothing was activated. No timeout was raised, no cap
  altered, no gate weakened, no libsumo installed, no external data obtained, no
  held-out run. --allow-drift, --overwrite and --no-fault-injection were not
  used. No v3 registration or outcome file was written, because discovery found
  zero archives and refuses to freeze an empty registration.`
- Acceptance criteria: `A custom v3 registration names its custom v3 outcome; a
  run refuses a disowned outcome; changing monthly_sumo.py or product_arm.py is
  reported as drift; the seal covers the re-derived import closure; the
  registration is self-consistent and detects tampering; frozen v1/v2 records
  still validate against themselves.`
- Useful checks: `pytest -q tests/test_cost_ordered_benchmark_run.py
  tests/test_cost_ordered_benchmark_discovery.py
  tests/test_cost_ordered_execution_review.py tests/test_monthly_sumo.py
  (135 passed); tests/test_cost_ordered_benchmark_provenance.py (25 passed);
  git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
