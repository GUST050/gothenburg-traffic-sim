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

### CLOSURE-PLAN-COMPLETE — Provenance, simulator binding, unit budget

- Status: `PHASES 1 AND 4 IMPLEMENTED AND GREEN. Phases 2, 3, 5, 6, 7, 8 and 9
  require the calibrated archive library, which is not reachable from the
  environment these commands execute in. Nothing was activated.`
- Objective and scope: `Complete every safely actionable part of the closure
  scaling and validation plan, with real measurements.`
- Completion outcome: `Phase 1 — the registration now binds the SIMULATOR as
  well as the Python: resolved executable, SHA-256, version, resolution method
  and platform/machine, each reported as drift with both values named. It also
  binds the external gate state (monthly_gate_record.json, the adoption
  certificate, and the manifest the certificate names), where ABSENCE is bound
  as firmly as presence — a later-appearing certificate silently widening what
  a replay may claim is exactly the drift this catches. Phase 4 — the
  10,000-unit cap is replaced by a measured budget in
  traffic_sim/simulation/unit_budget.py: declared units/ledger-bytes/peak-RSS,
  a PAUSE with an explicitly incomplete resumable state instead of raising or
  truncating, and a status that can never be read as exhaustive. The default
  budget is still 10,000 so nothing changes by accident, and the
  100,000-parent protection is retained.`
- Context or checkpoints: `The plan's final acceptance criterion is measured
  and frozen as validation/closure_search_scaling_measurement_v2.json: the
  six-month 360 h case enumerates 11,813 parents and 23,349 unique daily units
  by streaming, in 14.6 s at 26.5 MiB peak RSS — well inside the 64 MiB process
  gate. It was never memory-bound; the legacy cap refuses it and a declared
  budget admits it. The 720 h case reproduces at 2,186/5,676 and stays valid
  under the legacy budget. The v1 baseline is UNCHANGED.`
- Primary files: `NEW traffic_sim/simulation/unit_budget.py,
  tests/test_unit_budget.py,
  validation/closure_search_scaling_measurement_v2.json. MODIFIED
  tools/cost_ordered_benchmark.py,
  tests/test_cost_ordered_benchmark_provenance.py, ARCHITECTURE.md,
  IMPROVEMENT_PLAN.md, TASKS.md, AGENT_NOTES.md. UNCHANGED: every frozen
  v1/v2 registration, outcome and baseline.`
- Constraints and safety: `Nothing was activated. No timeout raised, no cap
  weakened (the daily-unit default is unchanged and the parent cap is intact),
  no gate loosened, no libsumo installed, no external data obtained, no
  held-out run. No v3 registration or outcome was written.`
- Acceptance criteria: `A changed SUMO binary, version or platform is drift; an
  absent gate that appears is drift; a changed gate/certificate/manifest is
  drift; changed monthly_sumo.py or product_arm.py is drift; custom
  registration/outcome paths bind each other; unknown schema is refused;
  self-tampering is detected; the 360 h case is admitted by a declared budget
  and still refused by the legacy one; an incomplete search cannot pass as
  exhaustive.`
- Useful checks: `pytest -q tests/test_cost_ordered_benchmark_provenance.py
  (34 passed, 1 skipped); tests/test_unit_budget.py (16 passed); the four
  required focused modules plus the v1/v2 registration suite (154 passed);
  git diff --check clean.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
