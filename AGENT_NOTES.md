# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `PR A and PR B of the closure-search scaling plan are
  implemented, measured and green on
  claude/closure-scaling-preflight-pr-a-b, branched from 3bb2fa3.`
- Summary: `The search can now be sized EXACTLY before it starts. Instead of
  walking every ClosureSchedule, the preflight uses a run-length identity — a
  maximal run of L usable dates holds max(0, L-n+1) windows of length n — which
  turns the six-month cases into ~40 (day-count, start) pairs over ~130 dates.
  It reproduces the plan's own recorded sizes exactly: 2,186 parents / 5,676
  units and 11,813 / 23,349. Two axes are needed because the generator advances
  differently per allocation policy: exact_equal_daily_v1 steps through
  consecutive ELIGIBLE dates, equal_daily_rounded_v1 through consecutive
  CALENDAR dates. PR A freezes six cases (both six-month cases plus four small
  brute-force ones) with full identity binding and per-phase timings.`
- Files changed: `NEW traffic_sim/simulation/closure_preflight.py,
  tools/benchmark_closure_search_scaling.py,
  validation/closure_search_scaling_baseline_v1.json,
  tests/test_closure_preflight.py,
  tests/test_benchmark_closure_search_scaling.py. MODIFIED serve.py (read-only
  preflight endpoint), web/app.js + web/index.html (estimate before start),
  tests/test_serve.py (11 endpoint tests + bundle version pin), ARCHITECTURE.md,
  the scaling plan (section 8, appended), TASKS.md, AGENT_NOTES.md.`
- Checks: `tests/test_closure_preflight.py 71 passed, including 40 randomized
  differential contracts. Focused preflight/calendar/monthly-search/
  monthly-SUMO/period-comparison/independent-daily/benchmark set: 362 passed,
  2 failed. Closure teleport + survivability regressions: 112 passed.
  tests/test_serve.py: 126 passed. git diff --check clean.`
- Decisions and evidence: `Both PR B gates pass — six-month preflight p95
  0.019 s (720 h) and 0.310 s (360 h) against 3 s, peak RSS 19.0 and 21.7 MiB
  against 32 MiB, versus 3.74 s/191.8 MiB and 15.30 s/467.9 MiB to materialize
  the same searches. Cache state is reported as `unknown`, not as a false miss:
  the daily backend identity only exists after the demand resolver has prepared
  an archive, which a read-only call must not do. exact_balanced_daily_v1 is
  refused with 422 rather than approximated. The 10,000-unit cap is reported
  and never raised; the 360 h case is classified over_resource_budget BEFORE
  any job exists, which is the late surprise the plan set out to remove.`
- Blockers or risks: `TWO GATES NOT CLOSED HERE, both environmental and both
  reproduced at the untouched base commit: (1)
  tools/screen_closure_survivability.py --verify does not reproduce, because
  sumo/net.net.xml is gitignored and was rebuilt locally — ONLY its digest and
  the derived content key differ, every measured value is identical;
  (2) tests/test_monthly_v2_benchmark.py's two tests need artifacts under the
  gitignored runs/ tree. Also: deterministic_cost and sumo_wall_time are
  recorded as UNMEASURED in the baseline, with reasons — calibrated demand
  cannot be built here because build_candidates.py needs overpass-api.de, which
  this environment's network policy denies.`
- Suggested next action: `Re-run tools/screen_closure_survivability.py --verify
  and tests/test_monthly_v2_benchmark.py on a machine with the real sumo/ and
  runs/ trees to close both gates. Then PR C: streaming ledgers against the
  frozen baseline, whose 720 h materialization figure (191.8 MiB) is the
  reference for the plan's under-64-MiB exit gate.`
- Actor notes: `No frozen v1/v6/v9/v10 artifact was rewritten, no v11 was
  created, no held-out campaign was run and no annual warming input was
  touched. validation/monthly_proxy_manifest_v10.json still reports its
  intentional historical source drift.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
