# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Closure-search scaling: reviewed step 0, PR D, PR E, PR F,
  step 4 and PR H's pre-registration, plus real-golden PR D/E verification`
- Status: `IMPLEMENTED, REVIEWED AND GREEN; RELEASE GATES REMAIN OPEN. Step 0
  removed the fixed SciPy import from the search path:
  the import chain is 99.96 -> 21.62 MiB, the product CLI 130.60 -> 21.68 MiB
  and the final 720 h Darwin/arm64 streaming process total is 25.30 MiB, so
  PR C's under-64-MiB gate is PASSED on the frozen baseline platform. PR D
  moved the deterministic cost into a process-free provider with a versioned
  content-addressed daily cache; review fixed a network/path identity mismatch,
  bound adjacency metadata, made concurrent cache publication race-safe and
  stopped repeated route-file hashing. PR E is a cost-ordered state machine
  proved equivalent to exhaustive on fixtures and registered SHADOW ONLY;
  review made resume require the exact verified prefix and evidence-derived
  viability. The pinned real-golden replay now passes PR D field/cost/order
  identity and PR E status/selected-ID equivalence, but saves 0/3 verifications
  because that old benchmark has only one health-viable finalist. PR F froze
  policy v3 and its pre-registration without activating
  anything; step 4 declared the progress vocabulary and wired the UI detail;
  PR H froze the independent-vs-continuous question and found that the
  contract itself refuses a continuous closure above 21 workdays.`
- Suggested next action: `Use the existing calibrated archive library to
  pre-register and run a discriminating benchmark with several health-viable
  candidates. First connect the cost-ordered cursor/provider to actual product
  execution; the current CLI mode is still a post-hoc shadow replay. Keep
  policy v3 closed until positive savings and held-out gates pass.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot, shadow or diagnostic
  artifacts as release evidence. Do not activate policy v3, the cost-ordered
  screening mode, UI exposure or a global-best claim before the equivalence and
  held-out gates actually pass. Do not raise the 10,000-unit cap. Do not
  rewrite validation/closure_search_scaling_baseline_v1.json or any frozen
  v1/v6/v9/v10 artifact. Do not edit annual plan-bound inputs while warming is
  active. Do not fabricate demand, held-out or microsimulation evidence.`
- Updated: `Codex review branch codex/review-closure-scaling-remaining over
  Claude fa02dfe and Codex 8644f81. Focused implementation suites: 149 passed
  (deterministic disruption 32, cost-ordered 77, import cost 4, progress 10,
  policy v3 9, PR H 17); with independent-daily integration: 166 passed. API:
  126 passed. A broader affected sweep exposed one introduced workspace-lock
  regression; it is repaired and its focused regression passes. The remaining
  network-fixture failure is already reproducible at review base 8644f81.
  Final review sweep including the real-golden contract: 237 passed; API: 126
  passed; survivability: byte-identical. The golden record independently
  reproduces byte-for-byte.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-SCALING-REST — Cost-first execution, shadow mode and pre-registrations

- Status: `IMPLEMENTED, REVIEWED AND TESTED. The technical real-golden and
  named-benchmark equivalence gates pass. Shadow/replay and pre-registration
  remain inert; positive savings, discriminating benchmark, held-out and
  activation gates remain open.`
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
  daily costs under an identity that binds routes, network, adjacency metadata,
  schema and costing code. Concurrent writers use unique atomic partials, and
  route digests are pinned once with fail-closed drift detection rather than
  rehashed per candidate. cost_ordered_search.py verifies in cost order and
  stops only when the next candidate is strictly above cutoff + practical
  equivalence, handing the
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
  tools/verify_closure_cost_ordering_golden.py,
  validation/monthly_search_policy_v3.json,
  validation/monthly_search_policy_v3_preregistration.json,
  validation/independent_vs_continuous_preregistration_v1.json,
  validation/closure_cost_ordering_golden_v1.json, and seven new test modules.
  MODIFIED finalist_decision.py, monthly_sumo.py,
  monthly_demand.py, monthly_search.py, search_workspace.py,
  run_monthly_closure_search.py, web/app.js, web/index.html, ARCHITECTURE.md,
  the scaling plan (sections 10 and 11), IMPROVEMENT_PLAN.md, TASKS.md,
  AGENT_NOTES.md.`
- Constraints and safety: `Nothing was activated. Ranking, closure_cost_v1,
  pilot selection, finalist decision, teleport policy, survivability and both
  resource caps are unchanged; v1 and v2 policies and every frozen artifact are
  untouched; no held-out campaign was run and no demand, OSM or microsimulation
  evidence was fabricated.`
- Acceptance criteria: `Differential equivalence with the exhaustive path on
  every fixture; pre-SUMO and post-SUMO costs field-identical; caches that hit
  on a widened range and miss on any identity change; state-machine restart
  idempotent, exact-prefix resumes enforced and stale/evidence-inconsistent
  resumes refused; the progress vocabulary shared by search, API and UI;
  every pre-registration frozen before its outcome and claiming nothing.`
- Useful checks: `pytest -q tests/test_deterministic_disruption.py
  tests/test_cost_ordered_search.py tests/test_search_import_cost.py
  tests/test_monthly_progress_contract.py tests/test_policy_v3_preregistration.py
  tests/test_independent_vs_continuous_preregistration.py (149 passed); the
  combined closure/monthly sweep; tests/test_serve.py; git diff --check;
  python3 tools/benchmark_closure_streaming.py --repeats 5 --overwrite;
  python3 tools/verify_closure_cost_ordering_golden.py --verify.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
