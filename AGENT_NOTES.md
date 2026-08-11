# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Closure-search evidence gates on branch
  codex/review-closure-evidence-gates over Claude f078b64. The pipeline is
  reviewed and repaired; the real v2 registration was frozen and attempted.
  Its first exhaustive SUMO verification timed out at the unchanged 300 s
  limit, so every release gate remains closed and nothing was activated. A
  runtime correction now bounds independent-day cold runs to the envelope and
  records candidate-local SUMO timeouts instead of aborting the search.`
- Summary: `The review found one defect that voided the durability claim.
  IndependentDailyRunner declares compact_pilot_artifacts, so no per-parent
  pilot evidence was published; the cursor still recorded every verification,
  and the resume then demanded evidence that had deliberately never been
  written — so every restart of a real cost-ordered search raised "evidence is
  missing". Reproduced directly, then fixed by disabling compaction whenever a
  cost source is present: compaction exists because an EXHAUSTIVE independent
  pilot writes one file per parent, and cost-first execution simulates only the
  boundary set, so the objection does not apply. The exhaustive path still
  compacts, pinned by a test. Second, execution_record was dead code and
  cost_ordered_result was assigned and never read, so neither the workspace nor
  result.json distinguished a cost-ordered run or carried its stop proof; both
  are published now, without timing so a re-entered pilot must reproduce them
  exactly. Third, a suspected orphan-cursor defect turned out NOT to be one —
  verify_search_workspace refuses unledgered artifacts on load, which is
  stricter than stepping over them — so the speculative fix was reverted and
  the real behaviour pinned in both directions. Separately, the IVC harness
  called run_monthly_search in-process while holding no WorkspaceLock; it now
  takes the same lock the product CLI takes.`
- Files changed: `NEW tools/product_arm.py and three test modules
  (execution review 10, benchmark run 21, benchmark discovery 18), plus
  validation/independent_vs_continuous_outcome_v2.json, the immutable real
  benchmark v2 registration/failed outcome, and Darwin libsumo preflight v2.
  MODIFIED
  monthly_search.py, cost_ordered_execution.py, run_monthly_closure_search.py
  (_cost_source_for takes an explicit cache path), tools/cost_ordered_
  benchmark.py, tools/measure_independent_vs_continuous.py, ARCHITECTURE.md,
  IMPROVEMENT_PLAN.md, the scaling plan, TASKS.md, AGENT_NOTES.md.`
- Checks: `Focused suites 300 passed. tests/test_serve.py 126 passed with
  loopback permission. Survivability reproduces byte-for-byte. The
  affected sweep fails 181, an IDENTICAL set to the recorded 73f5116 baseline
  (diff empty), while the branch adds 72+ passing tests. The golden record's
  bound source digests are unchanged and its own suite passes; note that
  monthly_search.py is NOT among the digests the golden record binds, which is
  why the review fixes were possible without touching frozen evidence.
  git diff --check clean.`
- Decisions and evidence: `Benchmark --run measured on in-memory fixture arms:
  45 exhaustive pilots against 2 cost-ordered, 43 saved, identical selected IDs
  and final decision, valid band_exhausted stop proof (first unexamined 3.0 >
  band 2.0), cache hits consistent, restart equivalent, all eleven gates
  passed. Two first-cut errors were caught by reading the numbers rather than
  the green tests: the stop proof was re-derived against invented field names,
  and the cost gate compared 2 candidates of 45 because the cost-ordered arm
  only has pilot statistics for what it simulated AND _final_result truncates
  them further — it now reads the workspace pilot-selection and the published
  cost ledger, so 45 candidates are compared and a poisoned price fails.
  IVC v2: 84 cases, 24 unsupported, 25 unpairable, 35 blocked, 0 measured;
  pairings 24 identical / 11 different candidate spaces / 25 unpairable / 24
  unsupported. The primary dev root was then used for real evidence. Product
  validation selected the exact three-day build 5ac74750843384b3 and froze 13
  schedules for 2027-03-22. The first exhaustive observation timed out after
  300 s (seed 1000); outcome v2 records failed_execution and all gates false.`
- Blockers or risks: `The next blocker is runtime, not archive absence: the
  corrected reset-window run completes the first candidate but a later
  07:15/q10 closure still exceeds the existing 300 s limit. Do not raise the
  limit to pass. Held-out remains gated.
  PR G's Darwin v2 preflight finds packaged SUMO 1.27.1 and
  lib/libsumocpp.dylib but no SWIG Python binding. PR I stays closed by the
  2026-07-20 decision. Policy v3 and every UI/global-best claim remain closed.`
- Suggested next action: `Create a NEW v3 preregistration with current source
  digests and rerun the corrected product path without --allow-drift. Preserve
  the failed v2 outcome; do not activate any gate on the diagnostic rerun.`
- Actor notes: `Nothing was activated and nothing frozen was rewritten. The
  v1 benchmark registration and the v1 IVC outcome are byte-identical; v2 files
  are new. No demand was built, no held-out campaign was run, no libsumo was
  installed, no external data was requested, _CONTINUOUS_MAX_WORKDAYS is
  unchanged and both resource caps are unchanged. Every new record carries
  release_evidence false.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
