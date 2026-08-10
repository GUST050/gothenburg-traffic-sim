# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Closure-search scaling: PR A (frozen baseline) and PR B
  (exact read-only preflight) from the 2026-08-10 scaling plan`
- Status: `PR A AND PR B IMPLEMENTED, MEASURED AND GREEN. The preflight counts
  a search exactly without materializing a ClosureSchedule and reproduces the
  plan's own six-month figures (2,186/5,676 and 11,813/23,349). Both PR B exit
  gates pass: p95 0.0147 s / 0.0514 s against a 3 s ceiling, peak RSS 16.4 /
  21.8 MiB against 32 MiB. PR A's required external arm measures deterministic
  cost p95 12.799 s and one q50 SUMO unit p95 9.040 s. The endpoint is read-only,
  the UI refuses an over-budget search before any job exists and the 10,000-unit
  cap is reported rather than raised. PR C onwards is untouched.`
- Suggested next action: `PR C — streaming ledgers and the memory gate, using
  validation/closure_search_scaling_baseline_v1.json as the reference (720 h
  materialization is 175.5 MiB today against the plan's under-64-MiB exit
  gate). Then PR D's process-free disruption provider.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot artifacts as release
  evidence. Do not edit annual plan-bound inputs while warming is active. Do
  not raise the 10,000-unit cap before streaming/cost-ordering equivalence and
  resource gates pass — the preflight reports it and must never bypass it.`
- Updated: `Claude commit 051ffea reviewed and corrected: cache counts exclude
  unexecutable units, macOS RSS is normalized, the DST fixture really spans the
  transition and the omitted deterministic/SUMO phases are now measured. The
  dev-only survivability and legacy v2 artifact gates pass; combined focused
  suite 636 passed / 5 skipped / 1 historical drift check deselected and API
  suite 126 passed / 2026-08-10`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-SCALING-AB — Frozen scaling baseline and exact read-only preflight

- Status: `IMPLEMENTED, REVIEWED AND MEASURED; PR A and PR B exit gates pass.`
- Objective and scope: `PR A and PR B only from
  docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md. Freeze a
  comparable performance/semantic baseline, then show a search's exact size
  before it starts. No streaming ledgers, no cost ordering, no ranking or
  release change; PR A alone may run an isolated diagnostic SUMO timing probe.`
- Completion outcome: `closure_preflight.py counts parents, per-workday-count
  groups, unique (date, start, end, road) units, out-of-demand-year units and
  cache hits/misses/unknown from a run-length identity rather than an object
  walk. POST /api/monthly_search/preflight exposes it read-only; the UI shows
  normal / large_but_runnable / over_resource_budget before start and refuses
  the last. benchmark_closure_search_scaling.py freezes six cases with exact
  Python, SUMO, network, route, policy and source identities.`
- Context or checkpoints: `Exactness is differential-tested against the real
  generator and decomposition, including 40 randomized contracts, year
  boundaries, leap days, both DST transitions, blackouts, overnight bands and
  the plan's 07:30-15:15 case. exact_balanced_daily_v1 is REFUSED (422), not
  approximated. On the dev machine, tools/screen_closure_survivability.py
  --verify reproduces byte-for-byte and both artifact-backed monthly-v2
  benchmark tests pass. The corrected baseline binds SUMO 1.27.1, its binary,
  demand metadata, network, routes, policies and source hashes.`
- Primary files: `traffic_sim/simulation/closure_preflight.py,
  tools/benchmark_closure_search_scaling.py,
  validation/closure_search_scaling_baseline_v1.json, serve.py, web/app.js,
  web/index.html, tests/test_closure_preflight.py,
  tests/test_benchmark_closure_search_scaling.py, tests/test_serve.py,
  ARCHITECTURE.md, the scaling plan`
- Constraints and safety: `The preflight reports the 100,000-parent and
  10,000-unit caps and never raises or bypasses them. It builds no demand,
  starts no SUMO, creates no job or artifact and takes no simulation lock.
  Closure teleport and survivability sources stay bound in the baseline's
  identity set. No frozen v1/v6/v9/v10 artifact was rewritten and no v11 was
  created.`
- Acceptance criteria: `Preflight counts equal exhaustive enumeration on every
  fixture; six-month p95 <= 3 s and peak RSS <= 32 MiB; the endpoint creates no
  artifact; the UI lets the user re-plan after seeing the estimate.`
- Useful checks: `pytest -q tests/test_closure_preflight.py
  tests/test_benchmark_closure_search_scaling.py (105 passed); artifact-backed
  monthly-v2 checks (2 passed); combined closure/monthly suite (636 passed,
  5 skipped, one historical v6 drift check deselected); full API suite
  (126 passed);
  tools/screen_closure_survivability.py --verify; git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
