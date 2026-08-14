# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Decision-gated direction split. Review found both gates
  technically wrong and the 107 anchor disconnected from the product; all are
  repaired. NEITHER gate is decided: Gate S = NOT_RUN (external egress
  policy), Gate M = INCONCLUSIVE. The four-quadrant matrix cannot be entered
  from either side and no exit may be declared.`
- Status: `FAS 0A NOW ACTUALLY WIRED: sensor 107 carries a provenance-bound
  directional_reference (3400/3100 of 6500, calendar-year 2025, source and
  verification, bearing->edge resolved from segment geometry at 352.1/174.4
  deg). The gap review found: load_direction_split had an anchor_day
  parameter that NO product caller passed, so the anchor changed nothing a
  real demand build produced. build_targets now takes anchor_day/anchor_epoch
  and all three build_sumo_demand call sites pass the build's own date, so
  the anchor reaches Level-1 targets. The annual D-factor is matched
  VOLUME-WEIGHTED from the same flows the build already loaded, because an
  unweighted mean of 96 slot shares is a different quantity from a published
  annual average daily D-factor. A 2027 forecast date is correctly refused by
  the reference's declared 2025 period. The pair still sums to exactly 1.0
  every slot; the five single-direction stations are byte-identical.
  FAS 0B RUN PATH REPAIRED, STILL NOT RUN: review found the tool could not
  have produced evidence — it never varied the route file or the seed (every
  q case and every seed ran the same simulation; DIRSPLIT_SENSITIVITY_SEED is
  read by nothing), it read disruption['total_time_loss_s'] which the product
  does not emit, it ignored its own registered 06:00-10:00 closure window,
  and it reduced a private objective rather than the preregistered decision
  fields. All four fixed: each (case, seed) pair is bound through its own
  one-seed ScenarioSpec (existing contract, unchanged), the closure window is
  applied and is part of the frozen key, and the decision is taken by
  traffic_sim.simulation.closure_ranking unchanged — viable set, ranking,
  winner, no-detour disqualification — with closure integrity and SUMO health
  as fail-closed preconditions. Recorded while repairing: the deployed
  ranking key is demand-side and therefore SEED-DETERMINISTIC by
  construction, so the old spread-ratio test on it was a tautology; v2
  verifies that invariant and refuses to publish if the seed axis was inert.
  Still NOT_RUN: build_candidates.py needs overpass-api.de and the session
  proxy answers 403 to CONNECT (also geodata.scb.se), re-verified 2026-08-14.
  Gate S = NOT_RUN. Evidence:
  validation/dirsplit_direction_sensitivity_blocker_v2.json.
  FAS 1 REDECIDED AS INCONCLUSIVE: the published BASELINE is WITHDRAWN. Three
  reasons, each sufficient. (a) Rule 6 of the frozen text makes an
  unbuildable fold kind INCONCLUSIVE; dirsplit/dataset.py aggregates away
  local_date so blocked_date yields zero folds, and the v1 code skipped it
  and published BASELINE anyway. (b) The population was wrong: v1 screened
  stations by the OSM oneway feature column (39 stations / 1,514 rows) while
  the deployed trainer screens by observed weekday-daytime share (81 stations
  / 3,665 rows). (c) The model was wrong: v1's LightGBM weighted toward the
  training centroid with no evidence weight, no training-window restriction
  and no shrinkage, while deployment centres on the target and ships
  0.5 + lambda*(pred-0.5). The claim "the deployed LightGBM is 31.6-39.2%
  worse" is withdrawn. The rule is now simplest_defensible_v2, which also
  requires the win under EVERY fold kind and compares a candidate against the
  CURRENT incumbent rather than always against 50/50. GATE M = INCONCLUSIVE.
  Evidence: validation/dirsplit_gate_m_outcome_v2.json (supersedes v1, which
  is preserved unedited).
  Fas 2, 3 and 4 remain closed. Previous closure v5 evidence and closed
  release gates are unchanged.`
- Suggested next action: `Two independent unblocks, neither optional.
  (1) Gate S: run the repaired tool on a machine that already has
  sumo/calibrated*.rou.xml — per review, --freeze-only completes there in
  about a second. Otherwise grant egress to overpass-api.de (and
  geodata.scb.se for a DeSO refresh) or supply a cached POI/candidate
  artifact, then: python3 build_sumo_demand.py --begin 06:00 --end 10:00;
  python3 tools/measure_direction_decision_sensitivity.py --freeze-only; then
  the same command without --freeze-only.
  (2) Gate M: build dataset v2 — one row per station_id x local_date x hour x
  heading with raw toward/away counts and a stable day_block_id — so
  blocked_date folds exist and blocks become station x date. That needs
  Norwegian volume egress. Do NOT synthesise dates; that fabricates the exact
  evidence the gate is asking for.
  Do not open Fas 2/3/4 until BOTH gates are decided.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve frozen q and closure evidence. Do not weaken
  calibration, equivalence, provenance, health, survivability, failure-recall,
  regret, resource or held-out gates. Do not couple demand-case identity to
  random seeds, splice cost fields across scenarios, silently exclude roads
  for low observability, or activate policy/UI/global-best claims before a
  preregistered shadow and held-out result passes. Existing 100,000 ceilings,
  worker budget, 300 s timeout and closed v5 gates remain unchanged. Do not
  hardcode 107's annual 0.52 as 96 measured quarters. NEITHER gate is
  decided: Gate S = NOT_RUN and Gate M = INCONCLUSIVE, and an unrunnable or
  inconclusive gate is at least as restrictive as a negative one. Do not
  quote the withdrawn Gate M = BASELINE or the withdrawn 31.6-39.2% figure as
  a current result. A gate whose tooling is wrong must be repaired and rerun,
  never reported from the broken run.`
- Updated: `2026-08-14 Claude. Gate S tooling, the 107 product wiring and the
  Gate M rule/population/model repaired on
  claude/gate-s-critical-findings-brkzye; both gates now undecided and
  honestly labelled.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### DIRSPLIT-UNCERTAINTY-V2 — Decide the smallest justified direction solution

- Status: `IN PROGRESS — Fas 0A now wired into the product path. Fas 0B tool
  repaired but still NOT_RUN. Fas 1 redecided as INCONCLUSIVE. Matrix not
  closable from either side.`
- Objective and scope: `First use local evidence at sensor 107, then establish
  whether plausible direction variation changes closure decisions and whether
  any conditional point model beats 50/50. Build scenarios or product contracts
  only when both questions justify them.`
- Completion outcome: `Still open, and further from an exit than the previous
  handoff implied. Exit A needs Gate S = NO AND Gate M = BASELINE; Gate S has
  not been run and Gate M is INCONCLUSIVE, so no exit is claimed.`
- Context or checkpoints: `Gate M = INCONCLUSIVE (rule 6: blocked_date folds
  cannot be built from the aggregated table). Diagnostic numbers under the
  corrected deployed population — 81 stations / 3,665 rows / 162 blocks — and
  the deployed LightGBM: leave-city-out MAE 0.0656 vs 0.0627 for 50/50, CI95
  [+0.000768, +0.004979]; leave-station-out 0.0624 vs 0.0598, CI95 straddling
  zero; shrunk and beta-binomial D-factors beat 50/50 on leave-city-out and
  tie on leave-station-out. None of it decides anything. Evidence:
  data/dirsplit/gate_m_report.json and
  validation/dirsplit_gate_m_outcome_v2.json (v1 preserved, superseded).
  Gate S blocker recorded in
  validation/dirsplit_direction_sensitivity_blocker_v2.json (v1 preserved,
  superseded). Measured on the real sumo/direction_split.json while preparing
  inputs: q50 pairs sum to exactly 1.0000 while q10 sums low and q90 high by
  about 0.12. That repair belongs to Fas 2 and was not performed.`
- Primary files now: `tools/measure_direction_decision_sensitivity.py;
  dirsplit/evaluate.py; demand/intake.py; build_sumo_demand.py (three
  build_targets call sites only); data_in/sensors.json;
  traffic_sim/intake/sensors.py; the focused test files.
  Demand/monthly/warm/API/UI remain explicitly conditional future scope and
  were not touched.`
- Constraints and safety: `Legacy q archives remain immutable and readable. No
  probabilistic q claims without coverage validation; no arbitrary road ban
  from observability; no field-wise scenario splicing; no policy activation or
  held-out promotion before preregistered gates pass. An INCONCLUSIVE gate is
  not a negative result and must not be reported as one. Published outcome
  artifacts are append-only: supersede with a new version, never edit.`
- Acceptance criteria: `107 is correctly anchored AND the anchor reaches the
  real Level-1 target path (done). Gate S and Gate M are frozen and decided
  (both still pending: Gate S needs the route files, Gate M needs dataset
  v2). The four-outcome matrix then selects Exit A/C or Gren B/D.`
- Useful checks: `python3 -m pytest tests/test_sensor_107_directional_reference.py
  tests/test_dirsplit_legacy_pin.py tests/test_direction_decision_sensitivity.py
  tests/test_dirsplit_gate_m.py -q`;
  `python3 -m dirsplit.evaluate` reproduces the Gate M report byte for byte.
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
