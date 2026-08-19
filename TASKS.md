# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Decision-gated direction split: bind sensor 107's local
  evidence, measure whether direction changes closure decisions, then compare
  central models before authorizing any ensemble/product expansion.`
- Status: `UNCONDITIONAL PHASES IMPLEMENTED 2026-08-16; BOTH GATES STILL OPEN.
  Fas 0A is done and measured: sensor 107 carries a provenance-bound 2025
  directional_reference (3400/3100), and traffic_sim/intake/direction_anchor.py
  re-levels the ESTIMATED per-slot split at load time so the declared period
  reproduces 0.5231 (delta +0.100 log-odds; the transfer model alone gave
  0.4981). Per-slot values stay estimates; the five single-direction stations'
  Level-1 targets are unchanged. Fas 0B is built and preregistered but NOT run
  (needs a calibrated demand build + SUMO), so Gate S is undecided. Fas 1 is
  built and partially run: on the tracked aggregate the simplest conditional
  model (shrunk_dfactor) beats the deployed LightGBM family, whose raw form
  loses to 50/50 — but Gate M is INCONCLUSIVE by its own frozen rule because
  the aggregate has no day blocks and no raw counts. The DEPLOYED central
  profile was nevertheless switched to that winner at the user's explicit
  direction, and the superseded machinery was DELETED rather than defaulted
  away: dirsplit/train.py, model.pkl, the Norwegian acquisition client,
  estimate_directions.py and the rollback flag are gone, and prior_flows.py now
  reads the deployed split instead of re-running its own prediction. q10/q90
  are leave-city-out residual quantiles of the same model. Gren B/D, schemas, monthly, warm-state,
  API and UI remain untouched.`
- Suggested next action: `The SHAPE-SOURCE question is now closed on evidence:
  a nearby aligned donor cannot be shown better than the pooled group curve
  (only the widest band reaches 8 independent pairs, and there the interval
  spans zero), so the deployed construction stands and the donor route is not
  deployed. What remains is not more modelling: rebuild demand so the new
  central profile and the 107 anchor reach the artifacts (make demand), then
  run Gate S — it decides whether direction changes a closure decision at all,
  and therefore whether any further split work has product value.
  Gate S: make demand (2025-09-16 historical), then make direction-sensitivity.
  Gate M: supply raw per-station volumes by hand in data/dirsplit/volumes/
  (the acquisition client is gone and the API is blocked here), then
  make dirsplit-dataset && make dirsplit-benchmark. Only a YES on Gate S
  may later open Gren B/D; nothing beyond the dated plan's unconditional phases
  may be built before that.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve frozen q and closure evidence. Do not weaken
  calibration, equivalence, provenance, health, survivability, failure-recall,
  regret, resource or held-out gates. Do not couple demand-case identity to
  random seeds, splice cost fields across scenarios, silently exclude roads
  for low observability, or activate policy/UI/global-best claims before a
  preregistered shadow and held-out result passes. Existing 100,000 ceilings,
  worker budget, 300 s timeout and closed v5 gates remain unchanged. Do not
  hardcode 107's annual 0.52 as 96 measured quarters or proceed past Gate S/M/P
  without their frozen evidence.`
- Runtime repair: `2026-08-17 supersedes the earlier statement that the
  variant runtime remained untouched. Ordinary recalibration is now explicitly
  q50-only. q10/q90 are opt-in closure-envelope stress arms and retain q50's
  exact integer population in every quarter. This reliability repair decides
  neither Gate S nor Gate M and rewrites no frozen evidence.`
- Updated: `2026-08-17 robust variant-contract repair on latest main; no gate
  decided and no frozen evidence rewritten.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### DIRSPLIT-UNCERTAINTY-V2 — Decide the smallest justified direction solution

- Status: `IN_PROGRESS — Fas 0A complete and measured; Fas 0B and Fas 1 built,
  their gate-deciding runs pending (SUMO demand build; raw Norwegian volumes).`
- Objective and scope: `First use local evidence at sensor 107, then establish
  whether plausible direction variation changes closure decisions and whether
  any conditional point model beats 50/50. Build scenarios or product contracts
  only when both questions justify them.`
- Completion outcome: `Either a documented central-only exit with 50/50/local
  anchor and no unused infrastructure, or—only after Gate S/M/P—a minimal,
  validated scenario integration with orthogonal case/seed identity.`
- Context or checkpoints: `Current artifact: 1,214 aggregated training rows,
  shrunk pooled domain MAE 0.0557 versus 0.0565 for 50/50, three of four cities
  worse than baseline, lambda 0.289. Current q10-q90 median width is 0.107 but
  nominal coverage and joint temporal/spatial validity are unmeasured. Current
  q route files contain 19,845/20,836/21,749 vehicles and seed identity is
  entangled with variant identity in several contracts.`
- Primary files now: `data_in/sensors.json; traffic_sim/intake/sensors.py;
  traffic_sim/intake/direction_anchor.py; demand/intake.py;
  tools/measure_direction_decision_sensitivity.py + validation/
  direction_decision_sensitivity_registration_v1.json; dirsplit/dataset.py,
  benchmark.py, coverage.py + validation/dirsplit_point_benchmark_v1.json;
  tests/test_direction_anchor.py, tests/test_direction_decision_sensitivity.py,
  tests/test_dirsplit_v2.py. Monthly/warm/API/UI remain conditional future
  scope and are untouched.`
- Constraints and safety: `Legacy q archives remain immutable and readable.
  No probabilistic q claims without coverage validation; no arbitrary road ban
  from observability; no field-wise scenario splicing; no policy activation or
  held-out promotion before preregistered gates pass.`
- Acceptance criteria: `107 is correctly anchored; Gate S and Gate M are frozen
  and decided; the four-outcome matrix selects Exit A/C or Gren B/D. Exit is a
  valid completion. Gate P and product criteria apply only if a scenario branch
  is actually opened.`
- Useful checks: `For the current documentation change: marker uniqueness,
  internal-link/path checks and git diff --check. Implementation checks are
  specified step-by-step in the dated plan.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
