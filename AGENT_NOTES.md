# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `The remaining closure-search plan, built on top of Codex
  review 8644f81 and Claude fa02dfe, is reviewed and repaired on
  codex/review-closure-scaling-remaining. Step 0, PR D, PR E, PR F, step 4 and
  PR H's pre-registration are implemented and green. Shadow/replay stays inert;
  PR G, PR I and step 8 are reported, not simulated.`
- Summary: `Step 0 found that PR C's whole process total was one line — a
  module-scope scipy import in finalist_decision, which independent_daily
  imports, so enumeration, preflight, ledgers and cost ordering all paid
  81.7 MiB for a distribution they never evaluate. It is lazy now, the seed
  budget moved to a dependency-free module, and the CLI runs spec, policy and
  the exact preflight before importing the SUMO stack or taking the demand
  lock. PR D turned the deterministic cost into a public process-free provider
  with a versioned content-addressed daily cache, summing per variant before
  the field-wise worst and disqualifying no-detour candidates with their
  evidence intact; the SUMO runner now delegates to that same provider, so
  there is one implementation rather than two to keep equal. Review corrected
  the provider so the network it hashes is the network it actually costs,
  bound the optional adjacency metadata, made concurrent cache writes atomic
  with unique partials, pinned route digests once and detects in-process input
  drift. PR E verifies in cost order and stops only when the next candidate is
  strictly above
  cutoff + practical equivalence, handing the finalist set to the unchanged
  selector so capacity_exceeded and no_viable are untouched. Resume now
  requires cursor == the exact verified prefix, ordered viability and viability
  matching the persisted evidence; direct dataclass input cannot bypass it.`
- Files changed: `NEW deterministic_disruption.py, cost_ordered_search.py,
  seed_worker_budget.py, tools/preregister_independent_vs_continuous.py,
  tools/verify_closure_cost_ordering_golden.py, four validation records
  (policy v3, its pre-registration, the independent-vs-continuous
  pre-registration and real-golden verification) and seven test modules. MODIFIED
  finalist_decision.py, monthly_sumo.py, monthly_demand.py, monthly_search.py,
  search_workspace.py, run_monthly_closure_search.py, web/app.js,
  web/index.html, tests/test_independent_daily.py, tests/test_serve.py,
  ARCHITECTURE.md, IMPROVEMENT_PLAN.md, the scaling plan (sections 10 and 11),
  TASKS.md, AGENT_NOTES.md.`
- Checks: `Review-focused: deterministic disruption 32, cost-ordered 77,
  import cost 4, progress contract 10, policy v3 9, PR H 17 = 149 passed;
  adding independent-daily integration = 166 passed. API 126 passed. A broader
  affected sweep exposed one new workspace-lock regression from the lazy import
  reorder; it is fixed, the backend is now loaded only after lock ownership,
  and its focused regression passes. The remaining network-fixture failure is
  reproducible at 8644f81. Final review sweep including both benchmark
  contracts, the real-golden record and the lock regression: 237 passed.
  Survivability reproduces byte-for-byte. git diff --check clean; no orphan
  SUMO processes.`
- Decisions and evidence: `Measured after step 0: search import chain
  99.96 -> 21.62 MiB, product CLI 130.60 -> 21.68 MiB, 720 h streaming process
  total 25.30 MiB on Darwin/arm64 with imported_scipy false. The reproducible
  five-repeat 2026-08-11 record reports memory_gate.status=passed against the
  64 MiB ceiling on the frozen baseline platform; PR C's memory gate is closed.
  PR E verifies 4 of 50 candidates on a broad fixture and agrees with exhaustive on
  every differential test including 24 randomised masks. PR H's construction
  found that ClosureSearchSpec refuses a continuous closure above 21 workdays,
  so the plan's 1-90 day paired comparison is not expressible above 21 at all.
  The real-golden record passes current provider/runner identity and preserves
  the historical cost/order; cost-ordered and exhaustive both return ready with
  closure-d9af6f11562e20e708e5 selected.`
- Blockers or risks: `Claude's no-demand blocker was environment-local and is
  superseded: this dev machine has the pinned golden archive plus hundreds of
  calibrated daily archives. validation/closure_cost_ordering_golden_v1.json
  now passes PR D's real provider/runner field identity and PR E's named real
  status/selected-ID equivalence, reproducing byte-for-byte. It saves 0/3 SUMO
  verifications because only one candidate is health-viable, so it cannot
  activate policy v3. The next blocker is implementation/evidence, not data:
  actual product execution still uses exhaustive SUMO followed by post-hoc
  replay, and no pre-outcome discriminating benchmark or untouched held-out
  campaign has passed. PR G additionally lacks libsumo. PR I is partly closed
  by the project's own 2026-07-20 decision to take no further external data.
  Step 8 needs the discriminating benchmark and held-out gates. The CLI's
  registered cost-ordered mode is a
  post-hoc diagnostic replay; persistent product execution/resume is not
  activated. Policy v3 must NOT be activated and no global-best or UI claim
  may open.`
- Suggested next action: `Connect the state-machine cursor and deterministic
  provider to the actual product execution path, then pre-register and run a
  discriminating benchmark from the existing calibrated archive library.`
- Actor notes: `Nothing was activated and nothing frozen was rewritten: v1/v2
  policies, the PR A baseline and every v6/v9/v10 artifact are untouched, no
  v11 was created, no held-out campaign was run, no annual warming input was
  touched, and both resource caps are unchanged. Shadow and replay artifacts
  are labelled diagnostic with release_evidence false.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
