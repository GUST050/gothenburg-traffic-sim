# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Direction-split uncertainty v2: improve dirsplit and use
  central demand, coherent daily demand cases/paths and matched SUMO seeds
  correctly in multi-month closure optimization.`
- Status: `RESEARCHED IMPLEMENTATION PLAN COMPLETE; SOURCE IMPLEMENTATION NOT
  STARTED. The audit shows that current q10/q50/q90 are trained from aggregated
  station-hour means, are not validated for nominal 80% coverage, and are
  applied as marginal global surfaces rather than coherent joint day
  scenarios. q50 barely improves pooled MAE over 50/50 and loses in three of
  four held-out domain cities. The new plan preserves legacy evidence, uses a
  central estimate for broad screening, builds empirically validated joint
  day scenarios for finalists, separates demand-case identity from SUMO seed,
  and turns observability into evidence/claim strength rather than an
  arbitrary road ban. Previous closure-scaling implementation remains done;
  its failed v5 and closed release gates remain unchanged.`
- Suggested next action: `Implement Step 0 then Step 1 in
  docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md: pin the
  legacy q contracts, introduce versioned demand-case schema and adapter, then
  build the non-aggregated training_table_v2 with day blocks and leakage tests.
  Do not activate a new policy or rename q10/q90 as probabilities before the
  calibration and joint-scenario gates pass.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve frozen q and closure evidence. Do not weaken
  calibration, equivalence, provenance, health, survivability, failure-recall,
  regret, resource or held-out gates. Do not couple demand-case identity to
  random seeds, splice cost fields across scenarios, silently exclude roads
  for low observability, or activate policy/UI/global-best claims before a
  preregistered shadow and held-out result passes. Existing 100,000 ceilings,
  worker budget, 300 s timeout and closed v5 gates remain unchanged.`
- Updated: `2026-08-13 Codex research and repository plan. Documentation-only;
  local code and tests are unchanged.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### DIRSPLIT-UNCERTAINTY-V2 — Calibrated direction scenarios for closure decisions

- Status: `READY — detailed researched plan is complete; implementation awaits
  Step 0.`
- Objective and scope: `Improve dirsplit from an aggregated marginal-quantile
  transfer model into a leakage-tested central model plus calibrated marginal
  diagnostics and coherent joint daily demand cases. Integrate those cases
  into multi-month closure screening/finalists with demand uncertainty and SUMO
  randomness kept separate.`
- Completion outcome: `Central-only broad screening; lazy scenario generation
  for finalists; common random numbers for matched baseline/candidate runs;
  complete-scenario risk reduction; truthful observability and UI states; and
  versioned shadow/held-out evidence before activation.`
- Context or checkpoints: `Current artifact: 1,214 aggregated training rows,
  shrunk pooled domain MAE 0.0557 versus 0.0565 for 50/50, three of four cities
  worse than baseline, lambda 0.289. Current q10-q90 median width is 0.107 but
  nominal coverage and joint temporal/spatial validity are unmeasured. Current
  q route files contain 19,845/20,836/21,749 vehicles and seed identity is
  entangled with variant identity in several contracts.`
- Primary files: `dirsplit/dataset.py, train.py, predict.py, coverage.py;
  demand/intake.py, demand/priors.py, build_sumo_demand.py;
  traffic_sim/core/contracts.py and the monthly/finalist/ranking/warm-state
  simulation modules; API/UI contracts; new versioned validation artifacts.`
- Constraints and safety: `Legacy q archives remain immutable and readable.
  No probabilistic q claims without coverage validation; no arbitrary road ban
  from observability; no field-wise scenario splicing; no policy activation or
  held-out promotion before preregistered gates pass.`
- Acceptance criteria: `All ten Definition-of-done items in the dated plan:
  baseline-qualified point model, calibrated labels, coherent days, orthogonal
  case/seed identity, matched repetitions, complete-scenario ranking,
  evidence-aware observability, lazy multi-month execution, truthful UI and
  passing shadow/held-out activation gates.`
- Useful checks: `For the current documentation change: marker uniqueness,
  internal-link/path checks and git diff --check. Implementation checks are
  specified step-by-step in the dated plan.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
