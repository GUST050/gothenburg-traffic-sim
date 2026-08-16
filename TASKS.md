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
- Status: `RESEARCHED PLAN REVISED AFTER SCOPE REVIEW; SOURCE IMPLEMENTATION NOT
  STARTED. Sensor 107 is the only two-edge station whose split directly creates
  two Level-1 targets; its documented 2025 52/48 anchor is not yet machine-bound
  with period semantics. The other five measured directions remain untouched
  Level 1 while their opposite estimates are surrenderable Level-2/3 inputs.
  The former nine-step integration is replaced by Gate S (closure sensitivity),
  Gate M (held-out point signal) and Gate P (offline scenario value). 50/50 plus
  107 is an explicit successful exit. Monthly, warm-state, schemas, API and UI
  are forbidden before their gates pass. Previous closure v5 evidence and
  closed release gates remain unchanged.`
- Suggested next action: `Implement Fas 0A from the dated plan, now including
  the verified N<->toward-centre mapping from the 2026-08-16 review
  (60786979_3575001205_0, bearing 352.1 deg), so the 52/48 anchor cannot be
  applied backwards. Independently of any gate, resolve the measured 47%
  coverage of the nominal 80% q10-q90 interval - widen to 0.193 or relabel
  stress_only - because it feeds the map's confidence today. Prefer running
  Fas 1's tournament before Fas 0B's SUMO matrix: it is nearly free and it
  changes what Gate S should be run on. Do not create DemandEnsemble,
  monthly, warm-state, API or UI code.`
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
- Updated: `2026-08-13 Codex scope correction. Documentation-only; local product
  code and tests are unchanged.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### DIRSPLIT-UNCERTAINTY-V2 — Decide the smallest justified direction solution

- Status: `READY — conditional plan complete; implementation begins at Fas 0A.`
- Objective and scope: `First use local evidence at sensor 107, then establish
  whether plausible direction variation changes closure decisions and whether
  any conditional point model beats 50/50. Build scenarios or product contracts
  only when both questions justify them.`
- Completion outcome: `Either a documented central-only exit with 50/50/local
  anchor and no unused infrastructure, or—only after Gate S/M/P—a minimal,
  validated scenario integration with orthogonal case/seed identity.`
- Context or checkpoints: `Current artifact: 1,214 aggregated training rows
  (of 15,346 collected - weekend and off-hour rows are dropped, then predicted
  anyway with is_weekend=0), shrunk pooled domain MAE 0.0557 versus 0.0565 for
  50/50, three of four cities worse than baseline, lambda 0.289. Current q
  route files contain 19,845/20,836/21,749 vehicles and seed identity is
  entangled with variant identity in several contracts. MEASURED 2026-08-16
  (docs/reviews/DIRSPLIT_PLAN_RESEARCH_REVIEW_2026-08-16.md): the q10-q90
  interval's nominal 80% is really 47.0% out-of-sample, honest width 0.193 not
  0.099, and that 47% is an upper bound because rows are ~8-day means. An
  unconditional hour-of-day curve with no street features beats 50/50 on
  leave-city-out and leave-station-out (+4.9 to +6.6%) while every LightGBM
  variant is 5-9% worse. Level does not transfer (curve is 2.91 pp off sensor
  107's published D-factor vs 50/50's 2.31 pp); shape does. pfe.py's groups
  parameter already supports constraining the measured two-way SUM instead of
  splitting it, but drops at RUNG_NOBND_TOL1 and would need a measured class.`
- Primary files now: `data_in/sensors.json; existing dirsplit dataset/train/
  predict/coverage modules; focused 107/legacy tests; one bounded sensitivity
  tool and append-only registration/outcome. Demand/monthly/warm/API/UI are
  explicitly conditional future scope.`
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
