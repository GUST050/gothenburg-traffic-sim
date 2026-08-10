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
  gates pass: p95 0.019 s / 0.310 s against a 3 s ceiling, peak RSS 19.0 /
  21.7 MiB against 32 MiB. POST /api/monthly_search/preflight is read-only and
  proven artifact-free; the UI refuses an over-budget search before any job
  exists and leaves every input editable. The 10,000-unit cap is reported, not
  raised. PR C onwards is untouched.`
- Suggested next action: `PR C — streaming ledgers and the memory gate, using
  validation/closure_search_scaling_baseline_v1.json as the reference (720 h
  materialization is 191.8 MiB today against the plan's under-64-MiB exit
  gate). Then PR D's process-free disruption provider.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot artifacts as release
  evidence. Do not edit annual plan-bound inputs while warming is active. Do
  not raise the 10,000-unit cap before streaming/cost-ordering equivalence and
  resource gates pass — the preflight reports it and must never bypass it.`
- Updated: `PR A/B on claude/closure-scaling-preflight-pr-a-b from 3bb2fa3;
  362 focused + 112 closure-integrity + 126 API tests pass, 2 pre-existing
  failures need the gitignored runs/ tree / 2026-08-10`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-SCALING-AB — Frozen scaling baseline and exact read-only preflight

- Status: `IMPLEMENTED AND MEASURED; both PR B exit gates pass. One verification
  gate could not be closed in this environment — see checkpoints.`
- Objective and scope: `PR A and PR B only from
  docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md. Freeze a
  comparable performance/semantic baseline, then show a search's exact size
  before it starts. No streaming ledgers, no cost ordering, no ranking, SUMO or
  release change.`
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
  approximated. UNCLOSED GATE: tools/screen_closure_survivability.py --verify
  does not reproduce here, and does not at the untouched base commit either —
  sumo/net.net.xml is gitignored and was rebuilt locally, so only its digest
  and the derived content key differ while every measured value is identical.`
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
- Useful checks: `pytest -q tests/test_closure_preflight.py (71 passed);
  focused preflight/calendar/monthly/period/independent-daily/benchmark set
  (362 passed, 2 pre-existing failures needing runs/); closure teleport and
  survivability regressions (112 passed); tests/test_serve.py (126 passed);
  git diff --check clean.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
