# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Closure-search scaling: everything in the 2026-08-10 plan
  that can be built without calibrated demand — step 0, PR D, PR E, PR F,
  step 4 and PR H's pre-registration`
- Status: `IMPLEMENTED AND GREEN; FIVE GATES REMAIN OPEN, ALL ON THE SAME
  MISSING INPUT. Step 0 removed the fixed SciPy import from the search path:
  the import chain is 99.96 -> 21.62 MiB, the product CLI 130.60 -> 21.68 MiB
  and the 720 h streaming process total 23.25 MiB on Linux, so PR C's
  under-64-MiB gate is now closable by a measurement instead of structurally
  out of reach — it still needs a Darwin/arm64 run. PR D moved the
  deterministic cost into a process-free provider with a versioned
  content-addressed daily cache; PR E is a cost-ordered state machine proved
  equivalent to exhaustive on 70 differential tests and registered SHADOW
  ONLY; PR F froze policy v3 and its pre-registration without activating
  anything; step 4 declared the progress vocabulary and wired the UI detail;
  PR H froze the independent-vs-continuous question and found that the
  contract itself refuses a continuous closure above 21 workdays.`
- Suggested next action: `Build one calibrated q10/q50/q90 demand archive
  (make demand) on a host with OSM access. That single input unblocks the PR D
  and PR E equivalence gates, PR F's benchmark, PR G and PR H's measurement.
  Then re-measure the PR C memory gate on the Darwin/arm64 dev machine.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot, shadow or diagnostic
  artifacts as release evidence. Do not activate policy v3, the cost-ordered
  screening mode, UI exposure or a global-best claim before the equivalence and
  held-out gates actually pass. Do not raise the 10,000-unit cap. Do not
  rewrite validation/closure_search_scaling_baseline_v1.json or any frozen
  v1/v6/v9/v10 artifact. Do not edit annual plan-bound inputs while warming is
  active. Do not fabricate demand, held-out or microsimulation evidence.`
- Updated: `Claude, branch claude/closure-scaling-remaining-plan over Codex
  review 8644f81. Focused suites: deterministic disruption 27, cost-ordered 70,
  import cost 4, progress contract 10, policy v3 9, PR H pre-registration 17;
  closure/monthly/held-out/proxy sweep 2,057 passed with the same 122
  environment failures the review commit reproduces; API 126. The API suite
  has pre-existing timing flakes that also appear at 8644f81 / 2026-08-10`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-SCALING-REST — Cost-first execution, shadow mode and pre-registrations

- Status: `IMPLEMENTED AND TESTED. Every gate that can be closed without
  calibrated demand is closed; the rest are named with reproducible commands.`
- Objective and scope: `The remaining closure-search plan: step 0's memory
  gate, PR D's process-free deterministic cost, PR E's cost-ordered state
  machine in shadow mode, PR F's policy v3 pre-registration, step 4's progress
  and UI work, and PR H's pre-registration. PR G, PR I and step 8 are reported
  as blocked, not simulated.`
- Completion outcome: `finalist_decision imports SciPy lazily and the seed
  budget moved to a dependency-free module, so the CLI refuses an over-budget
  search in ~22 MiB instead of ~131 MiB. deterministic_disruption.py computes
  closure cost with no SUMO process, sums per variant before the field-wise
  worst, disqualifies no-detour candidates with full evidence, and caches
  daily costs under an identity that binds routes, network, schema and costing
  code. cost_ordered_search.py verifies in cost order and stops only when the
  next candidate is strictly above cutoff + practical equivalence, handing the
  finalist set to the unchanged selector. policy v3 and the
  independent-vs-continuous question are frozen but inert.`
- Context or checkpoints: `PR H's construction found that ClosureSearchSpec
  refuses a continuous closure above 21 workdays, so the plan's 1-90 day
  paired comparison is not expressible above 21 at all — which is why 21-day
  evidence may not be extrapolated to 90. It also found that with weekends
  excluded a continuous 7-workday run does not exist, because the two policies
  walk different date axes.`
- Primary files: `NEW traffic_sim/simulation/deterministic_disruption.py,
  cost_ordered_search.py, seed_worker_budget.py,
  tools/preregister_independent_vs_continuous.py,
  validation/monthly_search_policy_v3.json,
  validation/monthly_search_policy_v3_preregistration.json,
  validation/independent_vs_continuous_preregistration_v1.json, and five new
  test modules. MODIFIED finalist_decision.py, monthly_sumo.py,
  monthly_demand.py, monthly_search.py, search_workspace.py,
  run_monthly_closure_search.py, web/app.js, web/index.html, ARCHITECTURE.md,
  the scaling plan (sections 10 and 11), TASKS.md, AGENT_NOTES.md.`
- Constraints and safety: `Nothing was activated. Ranking, closure_cost_v1,
  pilot selection, finalist decision, teleport policy, survivability and both
  resource caps are unchanged; v1 and v2 policies and every frozen artifact are
  untouched; no held-out campaign was run and no demand, OSM or microsimulation
  evidence was fabricated.`
- Acceptance criteria: `Differential equivalence with the exhaustive path on
  every fixture; pre-SUMO and post-SUMO costs field-identical; caches that hit
  on a widened range and miss on any identity change; restart idempotent and
  stale resumes refused; the progress vocabulary shared by search, API and UI;
  every pre-registration frozen before its outcome and claiming nothing.`
- Useful checks: `pytest -q tests/test_deterministic_disruption.py
  tests/test_cost_ordered_search.py tests/test_search_import_cost.py
  tests/test_monthly_progress_contract.py tests/test_policy_v3_preregistration.py
  tests/test_independent_vs_continuous_preregistration.py (127 passed); the
  combined closure/monthly sweep; tests/test_serve.py; git diff --check;
  python3 tools/benchmark_closure_streaming.py --repeats 5 --overwrite.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
