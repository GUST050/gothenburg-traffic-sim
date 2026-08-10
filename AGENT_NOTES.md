# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `The remaining closure-search plan, built on top of Codex
  review 8644f81, on branch claude/closure-scaling-remaining-plan. Step 0,
  PR D, PR E, PR F, step 4 and PR H's pre-registration are implemented and
  green. PR G, PR I and step 8 are blocked on one missing input and are
  reported, not simulated.`
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
  there is one implementation rather than two to keep equal. PR E verifies in
  cost order and stops only when the next candidate is strictly above
  cutoff + practical equivalence, handing the finalist set to the unchanged
  selector so capacity_exceeded and no_viable are untouched.`
- Files changed: `NEW deterministic_disruption.py, cost_ordered_search.py,
  seed_worker_budget.py, tools/preregister_independent_vs_continuous.py, three
  validation records (policy v3, its pre-registration, the
  independent-vs-continuous pre-registration) and six test modules. MODIFIED
  finalist_decision.py, monthly_sumo.py, monthly_demand.py, monthly_search.py,
  search_workspace.py, run_monthly_closure_search.py, web/app.js,
  web/index.html, tests/test_independent_daily.py, tests/test_serve.py,
  ARCHITECTURE.md, the scaling plan (sections 10 and 11), TASKS.md,
  AGENT_NOTES.md.`
- Checks: `Focused: deterministic disruption 27, cost-ordered 70, import cost
  4, progress contract 10, policy v3 9, PR H 17. Combined closure / monthly /
  held-out / proxy sweep: 2,057 passed with the same 122 failures the review
  commit 8644f81 reproduces in a clean worktree (this container's gitignored
  sumo/net.net.xml is not the dev machine's). API 126 passed. The API suite has
  pre-existing timing flakes — three runs at 8644f81 reproduced one — and each
  passes in isolation. git diff --check clean; no .partial files; no orphan
  SUMO processes.`
- Decisions and evidence: `Measured after step 0: search import chain
  99.96 -> 21.62 MiB, product CLI 130.60 -> 21.68 MiB, 720 h streaming process
  total 23.25 MiB on Linux/x86_64 with imported_scipy false. PR C's gate now
  reads open_pending_baseline_host instead of open_fixed_import_cost_dominates:
  it is closable by a Darwin/arm64 measurement, which it was not before. PR E
  verifies 4 of 50 candidates on a broad fixture and agrees with exhaustive on
  every differential test including 24 randomised masks. PR H's construction
  found that ClosureSearchSpec refuses a continuous closure above 21 workdays,
  so the plan's 1-90 day paired comparison is not expressible above 21 at all.`
- Blockers or risks: `ONE missing input blocks five gates: there is no
  calibrated q10/q50/q90 demand archive, and `make demand` fails here because
  build_candidates.py needs OSM/Overpass, which the environment's network
  policy denies. That blocks the PR D and PR E equivalence gates, PR F's
  benchmark and activation, PR G and PR H's measurement. PR G is additionally
  blocked because libsumo is not installed. PR I is partly closed by the
  project's own 2026-07-20 decision to take no further external data. Step 8
  needs the PR D/E gates first. Policy v3 must NOT be activated and no
  global-best or UI claim may open.`
- Suggested next action: `Build one calibrated demand archive on a host with
  OSM access, then run the exhaustive and cost-ordered arms on the same spec
  and compare; separately, re-measure the PR C memory gate on Darwin/arm64.`
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
