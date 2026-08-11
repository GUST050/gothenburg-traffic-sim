# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Closure-search scaling stages 1-5: cost-first product
  execution, the pre-registered discriminating benchmark, the
  independent-vs-continuous measurement harness and the libsumo preflight`
- Status: `IMPLEMENTED AND GREEN; EVERY RELEASE GATE REMAINS OPEN. Stage 1
  made cost ordering the actual product execution path — candidates are priced
  before any SUMO process exists, a content-keyed cost ledger and a per-
  verification cursor are durable, a mismatched resume is refused rather than
  repaired, and pre-SUMO cost is reconciled field-by-field against post-SUMO
  evidence. The durable cursor MIRRORS cost_ordered_search.py instead of
  hooking into it, because that module is bound byte-for-byte by the golden
  record; a fault-injection test proves the divergence check fires. Stage 2
  pre-registered the benchmark on structural criteria alone and froze eleven
  thresholds including a strictly positive saving minimum; it reports
  archives_available 0 in this checkout, measured rather than assumed, so it
  did not run. Stage 3 (held-out) therefore did not run and produced nothing.
  Stage 4 built and ran the independent-vs-continuous harness: 84 cases, 24
  unsupported by contract, 25 unpairable, 35 blocked on demand, 0 measured,
  plus a THIRD contract finding — 11 of the 35 "pairable" cases search
  different candidate spaces in both directions. Stage 5 corrected PR G's
  blocker: eclipse-sumo 1.27.1 IS installed and ships libsumo's C++ library and
  headers with no Python binding, so the previously recorded fix would not have
  worked.`
- Suggested next action: `On a host carrying the calibrated archive library,
  run `python3 tools/cost_ordered_benchmark.py --preregister --overwrite` and
  then `--run`; if it measures a strictly positive saving with identical
  selected IDs and final decision, freeze an untouched held-out campaign. Only
  after BOTH pass may policy v3 activation be reconsidered. Re-run
  `python3 tools/measure_independent_vs_continuous.py --overwrite` there too —
  its 35 blocked cases become measurable.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker, ordering
  thresholds, finalist selection or certificate. Do not weaken equivalence,
  provenance, health, survivability, failure-recall, regret, resource or
  held-out gates. Do not activate policy v3, the cost-ordered screening mode,
  UI exposure or a global-best claim before the discriminating benchmark and
  held-out gates actually pass. Do not raise the 100,000-parent or
  10,000-unit caps, and do not multiply worker pools past the approved seed
  budget. Do not raise _CONTINUOUS_MAX_WORKDAYS to make a benchmark run — see
  docs/plans/CONTINUOUS_CLOSURE_CEILING_2026-08-11.md. Do not rewrite any
  frozen v1/v2/v6/v9/v10 artifact or pre-registration; write separate outcome
  records. Do not install libsumo or obtain new external calibration data
  without explicit user authority; the 2026-07-20 no-further-external-data
  decision stands. Do not fabricate demand, held-out, benchmark or
  microsimulation evidence.`
- Updated: `Branch claude/closure-cost-ordered-product-integration on top of
  73f5116. Focused suites: 174 passed (deterministic disruption 32,
  cost-ordered search 77, cost-ordered execution 21, import cost 4, progress
  10, policy v3 9, PR H pre-registration 17, benchmark 18, IVC harness 22,
  libsumo preflight 10). tests/test_serve.py: 126 passed. The golden record's
  bound source digests are unchanged and it still reproduces byte-for-byte.
  git diff --check clean.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-COST-ORDERED-INTEGRATION — Stages 1-5 of the scaling plan

- Status: `IMPLEMENTED, TESTED AND GREEN. Cost-first execution is the product
  path. The discriminating benchmark and the independent-vs-continuous
  question are registered and harnessed; the benchmark, held-out and
  activation gates remain OPEN and nothing was activated.`
- Objective and scope: `Connect cost ordering to real product execution
  (stage 1); pre-register a discriminating benchmark on structural,
  outcome-independent criteria (stage 2); run held-out validation only after
  it passes (stage 3); build and run the independent-vs-continuous measurement
  harness (stage 4); report the PR G and PR I boundaries (stage 5).`
- Completion outcome: `cost_ordered_execution.py prices every candidate from
  calibrated routes BEFORE any SUMO process exists — no exhaustive run
  precedes it — publishes a cost ledger whose content key binds daily-unit
  identity, three route digests, the network digest, demand metadata, the
  disruption schema and the costing source bytes, writes a cursor after every
  verification, refuses a resume whose ledger key/bound identity/verified
  prefix does not match, and reconciles pre-SUMO cost against post-SUMO
  evidence on vehicles_affected, vehicles_no_detour, added_vehicle_hours and
  added_metres_total. The pilot reports cost_units, cost_parents, health_scan,
  pilot and finalists progress with real counts. cost_ordered_search.py is
  UNCHANGED: the cursor mirrors it and asserts non-divergence at the end of
  every run. tools/cost_ordered_benchmark.py registers structurally and refuses
  to overwrite. tools/measure_independent_vs_continuous.py binds by content key
  and writes a separate outcome. tools/preflight_libsumo.py diagnoses read-only
  and installs nothing.`
- Context or checkpoints: `Stage 4 found a THIRD contract divergence beyond the
  two PR H already recorded: the pre-registration's pairability test compares
  only the FIRST schedule, and 11 of its 35 "pairable" cases search different
  spaces. equal_daily_rounded_v1 rounds each daily shift up, so continuous can
  serve the work requirement in FEWER days (17-21 day schedules, 470 candidates
  against 150, scheduling up to 5130 minutes for a 5040-minute requirement);
  exact_equal_daily_v1 cannot express those. In the other direction the
  independent policy can straddle a weekend that calendar-consecutive
  continuous cannot. A differing candidate space, or a winner the other arm
  cannot express, is never reported as low risk.`
- Primary files: `NEW traffic_sim/simulation/cost_ordered_execution.py,
  tools/cost_ordered_benchmark.py, tools/measure_independent_vs_continuous.py,
  tools/preflight_libsumo.py, validation/cost_ordered_benchmark_registration_v1.json,
  validation/independent_vs_continuous_outcome_v1.json,
  validation/libsumo_preflight_v1.json,
  docs/plans/CONTINUOUS_CLOSURE_CEILING_2026-08-11.md and four test modules.
  MODIFIED monthly_search.py, independent_daily.py,
  run_monthly_closure_search.py, ARCHITECTURE.md, IMPROVEMENT_PLAN.md, the
  scaling plan (gate table and section 12), TASKS.md, AGENT_NOTES.md.
  UNCHANGED: cost_ordered_search.py and every frozen artifact.`
- Constraints and safety: `Nothing was activated. Policy v3, global-best and UI
  claims are closed. No frozen artifact or pre-registration was edited — the
  independent-vs-continuous pre-registration is byte-identical and its outcome
  is a separate record. No demand was built, no held-out campaign was run, no
  libsumo was installed, no external data was requested, and both resource caps
  are unchanged.`
- Acceptance criteria: `Cost-first execution runs SUMO on strictly fewer
  candidates than exhaustive on fixtures while reaching the same decision;
  crash/resume is fail-closed and equivalent; the benchmark's selection cannot
  consult an outcome; every measurement writes a separate record naming its
  registration by content key; every bucket count accounts for every case.`
- Useful checks: `pytest -q tests/test_deterministic_disruption.py
  tests/test_cost_ordered_search.py tests/test_cost_ordered_execution.py
  tests/test_search_import_cost.py tests/test_monthly_progress_contract.py
  tests/test_policy_v3_preregistration.py
  tests/test_independent_vs_continuous_preregistration.py
  tests/test_cost_ordered_benchmark.py
  tests/test_independent_vs_continuous_harness.py
  tests/test_preflight_libsumo.py (174 passed); tests/test_serve.py;
  python3 tools/verify_closure_cost_ordering_golden.py --verify;
  python3 tools/screen_closure_survivability.py --verify; git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
