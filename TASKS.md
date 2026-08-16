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
- Status: `GATE M MEASURED AND DECIDED = BASELINE (2026-08-16). The deployed
  direction model is statistically indistinguishable from writing 0.5: with the
  shrinkage lambda refitted on three cities and scored on the fourth, pooled
  domain MAE is 0.0568 against 0.0565 for 50/50, and a station-level bootstrap
  gives delta +0.00030 with 95% CI [-0.00301, +0.00406], P(model better)=0.32.
  The published 0.0557-vs-0.0565 margin is an artifact of fitting lambda on the
  same pooled rows it is then scored on. Two further measured defects: the
  deployed [q10, q90] interval covers 39.3% of held-out observations against a
  nominal 80%, and 4 of 6 per-sensor kernels are centred on the away-from-centre
  carriageway, outside the toward-centre-only training support. Evidence:
  validation/dirsplit_gate_m_20260816.json; harness: dirsplit/validate.py.
  Gate S remains OPEN and is now the only gate that separates Exit A from
  Gren B. Gate P stays closed. Sensor 107's 52/48 anchor is still not
  machine-bound. Previous closure v5 evidence and closed release gates remain
  unchanged.`
- Suggested next action: `Fas B of docs/plans/DIRSPLIT_REMEDIATION_PLAN_2026-08-16.md:
  provenance-bind sensor 107's yearly directional reference without fabricating
  per-slot measurements, with the four named regression tests. Then preregister
  Fas C (Gate S) — 4 demand cases x 4 matched seeds x 6 closures with common
  random numbers — writing the decision rule to validation/ BEFORE the first
  SUMO run. Do not create DemandEnsemble, monthly, warm-state, API or UI code.`
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
- Updated: `2026-08-16 Gate M measured. Adds dirsplit/validate.py, its tests and
  an evidence artifact; corrects stale figures in CLAUDE.md/README.md. No model,
  pipeline or policy behaviour changed.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### DIRSPLIT-UNCERTAINTY-V2 — Decide the smallest justified direction solution

- Status: `IN PROGRESS — Gate M decided (BASELINE, measured 2026-08-16). Fas A
  of the remediation plan is landed; implementation continues at Fas B.`
- Objective and scope: `First use local evidence at sensor 107, then establish
  whether plausible direction variation changes closure decisions and whether
  any conditional point model beats 50/50. Build scenarios or product contracts
  only when both questions justify them.`
- Completion outcome: `Either a documented central-only exit with 50/50/local
  anchor and no unused infrastructure, or—only after Gate S/M/P—a minimal,
  validated scenario integration with orthogonal case/seed identity.`
- Context or checkpoints: `Current artifact: 1,214 aggregated training rows,
  lambda 0.289, published shrunk pooled domain MAE 0.0557 versus 0.0565 for
  50/50, three of four cities worse than baseline. MEASURED 2026-08-16: that
  margin does not survive a nested lambda (0.0568 versus 0.0565; bootstrap CI
  straddles zero), the deployed q10-q90 interval covers 39.3% against a nominal
  80%, and 4 of 6 per-sensor kernels sit outside the training support on
  radial_cos while every per-sensor kernel effectively spans 68-91% of the
  training set. Current q route files contain 19,845/20,836/21,749 vehicles —
  direction assumptions move total network load by ~10% even though the model
  cannot predict direction — and seed identity is still entangled with variant
  identity in several contracts.`
- Primary files now: `data_in/sensors.json; existing dirsplit dataset/train/
  predict/coverage modules; focused 107/legacy tests; one bounded sensitivity
  tool and append-only registration/outcome. Demand/monthly/warm/API/UI are
  explicitly conditional future scope.`
- Constraints and safety: `Legacy q archives remain immutable and readable.
  No probabilistic q claims without coverage validation; no arbitrary road ban
  from observability; no field-wise scenario splicing; no policy activation or
  held-out promotion before preregistered gates pass.`
- Acceptance criteria: `107 is correctly anchored; Gate M is frozen and decided
  (DONE — BASELINE); Gate S is registered before it is run, then decided; the
  outcome matrix selects Exit A or Gren B. Exit is a valid completion. The
  q10/q90 labels must not ship at 39.3% measured coverage regardless of which
  branch wins. Gate P and product criteria apply only if a scenario branch is
  actually opened.`
- Useful checks: `python3 -m pytest tests/test_dirsplit_validate.py (22 tests);
  python3 -m dirsplit.validate reproduces data/dirsplit/train_report.json's
  lambda and pooled MAEs before measuring anything new; marker uniqueness,
  internal-link/path checks and git diff --check. Remaining implementation
  checks are specified per phase in the 2026-08-16 remediation plan.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
