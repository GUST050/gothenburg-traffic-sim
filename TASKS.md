# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Decision-gated direction split. Fas 0A, 0B and Fas 1 are
  implemented. Gate M is DECIDED = BASELINE. Gate S is BLOCKED by an external
  egress policy, so the four-quadrant matrix cannot be closed and no exit may
  be declared yet.`
- Status: `FAS 0A DONE: sensor 107 carries a provenance-bound
  directional_reference (3400/3100 of 6500, calendar-year 2025, source and
  verification, bearing->edge resolved from segment geometry at 352.1/174.4
  deg). Anchoring shifts an estimated series by ONE logit offset so the
  declared period reproduces the aggregate while the partner is derived as
  1-s; the pair sums to exactly 1.0 every slot and the measured two-way total
  survives build_targets. The five single-direction stations are untouched:
  load_direction_split(anchor_day=None) is byte-identical to before.
  FAS 0B DONE AS A RUN PATH, NOT AS A RESULT: the bounded matched-seed tool,
  its frozen materiality thresholds and its fail-closed Gate S rule exist and
  are tested, but the three q route files cannot be built here.
  build_candidates.py needs overpass-api.de and the session proxy answers 403
  to CONNECT (also geodata.scb.se, api.scb.se,
  trafikkdata-api.atlas.vegvesen.no, nominatim.openstreetmap.org). No
  registration was frozen, because the frozen candidate selection is computed
  from per-edge demand exposure in those files; a placeholder would defeat
  preregistration. Gate S = NOT_RUN.
  FAS 1 DONE AND DECIDED: one shared evaluation module runs constant_5050,
  shrunk_dfactor, beta_binomial_dfactor and similarity_weighted_lgbm over the
  same leakage-free blocked folds. On 1,514 rows / 39 stations / 74 blocks the
  deployed LightGBM is significantly WORSE than 50/50 (leave-city-out -31.6%,
  leave-station-out -39.2%, paired block-bootstrap CI entirely above zero) and
  the two hierarchical models tie. GATE M = BASELINE.
  blocked_date folds were impossible: the tracked table carries no dates and
  raw day-level volumes need the same denied host.
  Fas 2, 3 and 4 remain closed. Previous closure v5 evidence and closed
  release gates are unchanged.`
- Suggested next action: `Unblock Gate S, then decide the matrix. Either grant
  egress to overpass-api.de (and geodata.scb.se for a DeSO refresh) or supply a
  cached POI/candidate artifact, then run: python3 build_sumo_demand.py
  --begin 06:00 --end 10:00; python3
  tools/measure_direction_decision_sensitivity.py --freeze-only; then the same
  command without --freeze-only. If Gate S returns NO, Exit A applies with
  Gate M = BASELINE and the dirsplit build-out ends there. Do not open Fas 2
  on Gate M alone.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve frozen q and closure evidence. Do not weaken
  calibration, equivalence, provenance, health, survivability, failure-recall,
  regret, resource or held-out gates. Do not couple demand-case identity to
  random seeds, splice cost fields across scenarios, silently exclude roads
  for low observability, or activate policy/UI/global-best claims before a
  preregistered shadow and held-out result passes. Existing 100,000 ceilings,
  worker budget, 300 s timeout and closed v5 gates remain unchanged. Do not
  hardcode 107's annual 0.52 as 96 measured quarters. Gate M = BASELINE is NOT
  an exit on its own; Exit A additionally requires Gate S = NO. An unrunnable
  Gate S is at least as restrictive as INCONCLUSIVE.`
- Updated: `2026-08-14 Claude. Fas 0A/0B/1 implemented on
  claude/dirsplit-gated-plan-v2; Gate M decided, Gate S externally blocked.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### DIRSPLIT-UNCERTAINTY-V2 — Decide the smallest justified direction solution

- Status: `IN PROGRESS — Fas 0A, 0B and Fas 1 delivered. Gate M decided
  (BASELINE). Gate S blocked externally. Matrix not closable yet.`
- Objective and scope: `First use local evidence at sensor 107, then establish
  whether plausible direction variation changes closure decisions and whether
  any conditional point model beats 50/50. Build scenarios or product contracts
  only when both questions justify them.`
- Completion outcome: `Still open. Exit A requires Gate S = NO alongside the
  decided Gate M = BASELINE; Gate S has not been run, so no exit is claimed.`
- Context or checkpoints: `Gate M measured on 1,514 rows / 39 stations / 74
  blocks: LightGBM -31.6% (leave-city-out) and -39.2% (leave-station-out)
  against 50/50 with the paired CI entirely above zero; shrunk and
  beta-binomial D-factors tie at about +4.5%. Evidence:
  data/dirsplit/gate_m_report.json and validation/dirsplit_gate_m_outcome_v1.json.
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
- Acceptance criteria: `107 is correctly anchored (done). Gate S and Gate M are
  frozen and decided (Gate M done; Gate S pending an unblocked run). The
  four-outcome matrix then selects Exit A/C or Gren B/D.`
- Useful checks: `python3 -m pytest tests/test_sensor_107_directional_reference.py
  tests/test_dirsplit_legacy_pin.py tests/test_direction_decision_sensitivity.py
  tests/test_dirsplit_gate_m.py -q` (118 passed, 2 skipped);
  `python3 -m dirsplit.evaluate` reproduces the Gate M report.
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
