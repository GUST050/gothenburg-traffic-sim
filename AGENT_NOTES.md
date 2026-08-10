# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `PR A and PR B are implemented and corrected after review
  of Claude commit 051ffea; all dev-machine gates are closed.`
- Summary: `The search can now be sized EXACTLY before it starts. Instead of
  walking every ClosureSchedule, the preflight uses a run-length identity — a
  maximal run of L usable dates holds max(0, L-n+1) windows of length n — which
  turns the six-month cases into ~40 (day-count, start) pairs over ~130 dates.
  It reproduces the plan's own recorded sizes exactly: 2,186 parents / 5,676
  units and 11,813 / 23,349. Two axes are needed because the generator advances
  differently per allocation policy: exact_equal_daily_v1 steps through
  consecutive ELIGIBLE dates, equal_daily_rounded_v1 through consecutive
  CALENDAR dates. PR A freezes six cases with full identity binding and now
  also measures its required deterministic-cost and SUMO wall-time phases.`
- Files changed: `NEW traffic_sim/simulation/closure_preflight.py,
  tools/benchmark_closure_search_scaling.py,
  validation/closure_search_scaling_baseline_v1.json,
  tests/test_closure_preflight.py,
  tests/test_benchmark_closure_search_scaling.py. MODIFIED serve.py (read-only
  preflight endpoint), web/app.js + web/index.html (estimate before start),
  tests/test_serve.py (11 endpoint tests + bundle version pin), ARCHITECTURE.md,
  the scaling plan (section 8, appended), TASKS.md, AGENT_NOTES.md.`
- Checks: `Post-review preflight/scaling suite: 105 passed, including new
  out-of-year cache and macOS RSS regressions. Both gitignored-input monthly-v2
  artifact tests pass; survivability v2 reproduces byte-for-byte; Claude's full
  API suite passes 126/126. Final combined closure/monthly suite: 636 passed,
  5 skipped and the known historical v6 source-drift check deselected.`
- Decisions and evidence: `Both PR B gates pass — six-month preflight p95
  0.0147 s (720 h) and 0.0514 s (360 h) against 3 s, peak RSS 16.4 and 21.8 MiB
  against 32 MiB, versus 2.90 s/175.5 MiB and 12.10 s/489.9 MiB to materialize
  the same searches. PR A's five-repeat external arm measures q10/q50/q90
  deterministic cost at p95 12.799 s and one q50 SUMO daily unit at p95 9.040
  s. Cache state is reported as `unknown`, not as a false miss:
  the daily backend identity only exists after the demand resolver has prepared
  an archive, which a read-only call must not do. exact_balanced_daily_v1 is
  refused with 422 rather than approximated. The 10,000-unit cap is reported
  and never raised; the 360 h case is classified over_resource_budget BEFORE
  any job exists, which is the late surprise the plan set out to remove.`
- Blockers or risks: `No PR A/B gate remains open. The external arm is
  diagnostic and measures one representative daily unit, not production
  throughput. Historical held-out v10 remains intentionally stale against the
  rolling-source edits and must not be rewritten. PR C has not started.`
- Suggested next action: `Integrate the reviewed PR A/B commit, then implement
  PR C streaming ledgers against the corrected 175.5-MiB 720 h baseline and
  preserve exact schedule IDs/order.`
- Actor notes: `No frozen v1/v6/v9/v10 release artifact was rewritten, no v11
  was created, no held-out campaign was run and no annual warming input was
  touched. The new PR A diagnostic baseline was regenerated before integration
  because its first version omitted mandatory phases and had a wrong DST
  fixture/RSS fallback.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
