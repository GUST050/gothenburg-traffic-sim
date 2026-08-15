# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Decision-gated direction split. Fas 0A, 0B and Fas 1 are
  implemented and then REPAIRED after external review. Gate M is now
  INCONCLUSIVE (a required fold kind cannot be built from the tracked table);
  Gate S is NOT_RUN. NEITHER gate has returned an answer, so the four-quadrant
  matrix cannot be closed and no exit may be declared.`
- Status: `FAS 0A DONE: sensor 107 carries a provenance-bound
  directional_reference (3400/3100 of 6500, calendar-year 2025, source and
  verification, bearing->edge resolved from segment geometry at 352.1/174.4
  deg). Anchoring shifts an estimated series by ONE logit offset so the
  declared period reproduces the aggregate while the partner is derived as
  1-s; the pair sums to exactly 1.0 every slot and the measured two-way total
  survives build_targets. The five single-direction stations are untouched:
  load_direction_split(anchor_day=None) is byte-identical to before. REPAIRED
  after review: the anchor now actually reaches production. All three
  build_targets call sites in build_sumo_demand.py pass anchor_day, the offset
  is weighted by that day's measured two-way volume rather than flat across
  slots, and a non-2025 date is REFUSED visibly instead of anchoring a 2027
  forecast to a 2025 annual aggregate.
  FAS 0B DONE AS A RUN PATH, NOT AS A RESULT: the bounded matched-seed tool,
  its frozen materiality thresholds and its fail-closed Gate S rule exist and
  are tested, but the three q route files cannot be built here.
  build_candidates.py needs overpass-api.de and the session proxy answers 403
  to CONNECT (also geodata.scb.se, api.scb.se,
  trafikkdata-api.atlas.vegvesen.no, nominatim.openstreetmap.org). No
  registration was frozen, because the frozen candidate selection is computed
  from per-edge demand exposure in those files; a placeholder would defeat
  preregistration. Gate S = NOT_RUN. REPAIRED after review: the runner really
  varies what it claims to vary. Each cell now writes its own ScenarioSpec
  pinned to one seed and one demand variant (the old code passed --seeds as a
  COUNT and read an env var nothing set, so all 12 runs were identical), the
  objective reads a field that exists (disruption.added_vehicle_hours; the old
  total_time_loss_s never existed), and the reducer compares hard failure,
  health flags, vehicles_no_detour, the viable set, the ranking over viable
  candidates and the winner - not a mean objective alone.
  FAS 1 IMPLEMENTED; GATE M = INCONCLUSIVE, NOT BASELINE. One shared
  evaluation module runs constant_5050, shrunk_dfactor, beta_binomial_dfactor
  and lgbm_reimplementation over the same leakage-free blocked folds.
  blocked_date folds are IMPOSSIBLE here: the tracked table carries no dates
  and raw day-level volumes need the same denied host. The frozen rule says a
  gate whose required fold kind cannot be built is INCONCLUSIVE, and the code
  now enforces that instead of silently skipping the fold and publishing
  BASELINE. The rule is also enforced as written in two further respects: a
  candidate must win under EVERY fold kind that ran, and a more complex
  candidate must beat the CURRENT INCUMBENT pairwise, not merely beat 50/50
  alongside it.
  WITHDRAWN CLAIM: "the deployed LightGBM is 31.6-39.2% worse than 50/50" is
  unsupported and is retracted. The entrant was scored on a different
  population (an OSM oneway feature screen, 39 stations, 1,514 rows) than the
  deployment screens (observed share band [0.15, 0.85], now 81 stations, 3,665
  rows), and it is weighted toward the training centroid while the deployment
  aims its similarity kernel at the Gothenburg sensor edges. It is renamed
  lgbm_reimplementation and every candidate carries deployment_equivalent =
  false. validation/dirsplit_gate_m_outcome_v1.json is marked WITHDRAWN and
  superseded by v2.
  Fas 2, 3 and 4 remain closed. Previous closure v5 evidence and closed
  release gates are unchanged.`
- Suggested next action: `Both gates are open, and each needs a different
  unblock. Gate S: grant egress to overpass-api.de (and geodata.scb.se for a
  DeSO refresh) or supply a cached POI/candidate artifact, then run python3
  build_sumo_demand.py --begin 06:00 --end 10:00; python3
  tools/measure_direction_decision_sensitivity.py --freeze-only; then the same
  command without --freeze-only. Gate M: a date-preserving training table is
  required before it can answer at all - grant egress to
  trafikkdata-api.atlas.vegvesen.no and rebuild the table with local_date
  retained, so blocked_date folds exist. Neither Gate M = INCONCLUSIVE nor
  Gate S = NOT_RUN opens an exit, and two unmeasured gates are not Exit A. Do
  not open Fas 2 on either.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve frozen q and closure evidence. Do not weaken
  calibration, equivalence, provenance, health, survivability, failure-recall,
  regret, resource or held-out gates. Do not couple demand-case identity to
  random seeds, splice cost fields across scenarios, silently exclude roads
  for low observability, or activate policy/UI/global-best claims before a
  preregistered shadow and held-out result passes. Existing 100,000 ceilings,
  worker budget, 300 s timeout and closed v5 gates remain unchanged. Do not
  hardcode 107's annual 0.52 as 96 measured quarters. Neither gate has an
  answer: Gate M = INCONCLUSIVE and Gate S = NOT_RUN, and neither is an exit
  on its own. Exit A requires Gate S = NO alongside a DECIDED Gate M. An
  unrunnable gate is at least as restrictive as INCONCLUSIVE, never a
  substitute for a negative result. No tournament candidate may be described
  as the deployed dirsplit model.`
- Updated: `2026-08-15 Claude. Fas 0A/0B/1 implemented and then repaired on
  claude/dirsplit-gated-plan-v2 after external review found the Gate S runner
  invariant, the 107 anchor disconnected from the demand path, Gate M
  published as BASELINE without a required fold, and an unsupported claim
  about the deployed LightGBM. Gate M is now INCONCLUSIVE; Gate S NOT_RUN.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### DIRSPLIT-UNCERTAINTY-V2 — Decide the smallest justified direction solution

- Status: `IN PROGRESS — Fas 0A, 0B and Fas 1 delivered and then repaired
  after external review. Gate M = INCONCLUSIVE (no date-preserving table).
  Gate S = NOT_RUN (blocked externally). Matrix not closable.`
- Objective and scope: `First use local evidence at sensor 107, then establish
  whether plausible direction variation changes closure decisions and whether
  any conditional point model beats 50/50. Build scenarios or product contracts
  only when both questions justify them.`
- Completion outcome: `Still open, and now open on BOTH gates. Exit A requires
  Gate S = NO alongside a decided Gate M; Gate S has not been run and Gate M
  is INCONCLUSIVE, so no exit is claimed.`
- Context or checkpoints: `Gate M ran on 3,665 rows / 81 stations / 162 blocks
  after the population screen was aligned with the deployment's observed-share
  rule, but it returns INCONCLUSIVE because blocked_date folds cannot be built
  from a table with no dates. Indicative pooled numbers under the two fold
  kinds that did run: shrunk_dfactor +3.4%/+2.9% and beta_binomial_dfactor
  +2.9%/+2.5% against 50/50, lgbm_reimplementation -8.6%/-21.5%. These are
  NOT a gate result and the LGBM figure is not a measurement of the deployed
  model. Evidence: data/dirsplit/gate_m_report.json and
  validation/dirsplit_gate_m_outcome_v2.json; v1 is marked WITHDRAWN.
  Gate S blocker recorded in
  validation/dirsplit_direction_sensitivity_blocker_v1.json. Measured on the
  real sumo/direction_split.json while preparing inputs: q50 pairs sum to
  exactly 1.0000, q10 to 0.7030-0.9480 and q90 to 1.0520-1.2970 (mean -/+
  0.1220, median interval width 0.107). That repair belongs to Fas 2 and was
  not performed.`
- Primary files now: `data_in/sensors.json; traffic_sim/intake/sensors.py;
  demand/intake.py; dirsplit/evaluate.py;
  tools/measure_direction_decision_sensitivity.py; the four focused test files.
  Demand/monthly/warm/API/UI remain explicitly conditional future scope and
  were not touched.`
- Constraints and safety: `Legacy q archives remain immutable and readable. No
  probabilistic q claims without coverage validation; no arbitrary road ban
  from observability; no field-wise scenario splicing; no policy activation or
  held-out promotion before preregistered gates pass. Gate M alone must not
  open an exit.`
- Acceptance criteria: `107 is correctly anchored and the anchor reaches the
  demand path (done). Gate S and Gate M are frozen and DECIDED — both are
  still open: Gate M needs a date-preserving table, Gate S needs its inputs.
  The four-outcome matrix then selects Exit A/C or Gren B/D.`
- Useful checks: `python3 -m pytest tests/test_sensor_107_directional_reference.py
  tests/test_dirsplit_legacy_pin.py tests/test_direction_decision_sensitivity.py
  tests/test_dirsplit_gate_m.py -q` (154 passed, 2 skipped);
  `python3 -m dirsplit.evaluate` reproduces the Gate M report and prints
  INCONCLUSIVE with the missing fold kind named.
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
