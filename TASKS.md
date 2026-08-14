# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Decision-gated direction split, after TWO review rounds.
  Round 1 found both gates technically wrong and the 107 anchor disconnected
  from the product. Round 2 found Gate S could still return a FALSE YES by
  three routes, and — the load-bearing one — that the q10/q90 stress cases do
  not isolate the direction axis at all. All repaired. NEITHER gate is
  decided: Gate S = NOT_RUN (egress policy AND confounded stress cases),
  Gate M = INCONCLUSIVE. The four-quadrant matrix cannot be entered from
  either side and no exit may be declared.`
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
  ROUND 2 (2026-08-14) — GATE S COULD STILL RETURN A FALSE YES. Three
  separate routes, all closed: (a) a ranking key that varied across seeds was
  treated as evidence that direction matters, when it is a BROKEN
  MEASUREMENT — the deployed key is demand-side and cannot vary with the
  seed, so a violation now yields INCONCLUSIVE; (b) a candidate disqualified
  by the no-detour rule in EVERY stress case could open the gate through its
  cost spread, although the policy never reads that cost — it is now
  decision_relevant: false, and if every candidate is disqualified the
  outcome is INCONCLUSIVE because no viable set was ever formed; (c) the
  seed-inertness check grouped by stress case alone, so a
  candidate-to-candidate difference could stand in for a seed difference —
  it now groups by (case, candidate) and requires EVERY group to vary.
  Also closed: a closure window outside the demand build was silently
  replaced by the whole window (now a hard error), the topology filter
  failed open without sumolib (now fail-closed, network read once), and the
  registered date was never checked against demand_meta.json (now verified
  before selection runs).
  NEW BLOCKING FINDING — THE STRESS CASES DO NOT ISOLATE DIRECTION.
  dirsplit/predict.py writes edge_shares_q10 as (e0 -> s10, e1 -> 1 - s90)
  and edge_shares_q90 as (e0 -> s90, e1 -> 1 - s10), so each outer pair sums
  to 1 -/+ (s90 - s10) rather than 1. Measured on the artifacts rebuilt this
  session: max |pair sum - 1| = 0.297 against a 0.001 tolerance; q50 sums to
  exactly 1. A Gate S difference on those files could be a change in TOTAL
  VOLUME rather than direction, and nothing afterwards can separate the two.
  measure_pair_sum_isolation now runs at registration time, its result is
  inside the frozen content key, and decide_gate_s returns INCONCLUSIVE when
  isolation is not certified. REBUILDING THE q ARTIFACTS WITH A
  PAIR-SUM-PRESERVING CONSTRUCTION IS A PREREQUISITE FOR A MEANINGFUL GATE S,
  not an improvement; that work belongs to Fas 2 and was not done.
  SECOND 107 GAP CLOSED: write_counts (the routeSampler branch) still loaded
  the unanchored split, so the two demand branches would have calibrated to
  different targets from identical inputs. It now takes the same
  anchor_day/anchor_epoch. While fixing it, found that write_counts ALSO
  lacked the single-direction guard build_targets grew on 2026-08-06 —
  measured on the real artifacts, sensor 1076's edge carries a modelled
  share of 0.48, so every measured single-direction count was being written
  out at 48% of its value. Both fixed and pinned.
  GATE M ROUND 2: the evaluation still was not the deployed model — it fit
  ONE model per held-out city instead of one per station, and the nested
  shrinkage reused the outer fold's centre. Both fixed; the run also now
  completes under -W error::RuntimeWarning after explicit numerical floors.
  Verdict unchanged (INCONCLUSIVE); the LightGBM leave-city-out figure moved
  from -4.7% to -3.3%, so the earlier number overstated the loss. The report
  now carries a content key plus digests of the table and the scoring code.
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
  Gate S = NOT_RUN, now for TWO reasons: the egress denial, and the
  stress-case confound above. Evidence:
  validation/dirsplit_direction_sensitivity_blocker_v3.json (supersedes v2,
  which is preserved unedited).
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
  Evidence: validation/dirsplit_gate_m_outcome_v3.json (supersedes v2, which
  supersedes v1; both preserved unedited).
  Fas 2, 3 and 4 remain closed. Previous closure v5 evidence and closed
  release gates are unchanged.`
- Suggested next action: `Two independent unblocks, neither optional.
  (1) Gate S needs BOTH unblocks before it can say anything. Evidence
  quality first: rebuild q10/q90 with a pair-sum-preserving construction, or
  every run returns INCONCLUSIVE by design. Then inputs: run on a machine
  that already has sumo/calibrated*.rou.xml, or grant egress to
  overpass-api.de (and geodata.scb.se for a DeSO refresh), then: python3
  build_sumo_demand.py --begin 06:00 --end 10:00; python3
  tools/measure_direction_decision_sensitivity.py --freeze-only; then the
  same command without --freeze-only.
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
  never reported from the broken run. A measurement fault must never be
  reported as a positive finding: if the ranking key moves across seeds, or
  the stress cases do not isolate direction, the answer is INCONCLUSIVE, not
  YES. Do not run Gate S for evidence on q artifacts whose direction pairs
  do not sum to 1.`
- Updated: `2026-08-14 Claude, round 2. Gate S can no longer return a false
  YES (three routes closed), its inertness check is grouped correctly, the
  stress cases must now prove they isolate direction, the 107 anchor reaches
  BOTH demand branches, and Gate M evaluates one model per station as
  deployment does. Both gates remain undecided.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### DIRSPLIT-UNCERTAINTY-V2 — Decide the smallest justified direction solution

- Status: `IN PROGRESS — Fas 0A wired into BOTH demand branches. Fas 0B tool
  repaired twice and still NOT_RUN, now also blocked on evidence quality:
  the q10/q90 stress cases do not isolate direction. Fas 1 INCONCLUSIVE, with
  its diagnostic numbers re-measured on a per-station deployment-equivalent
  fit. Matrix not closable from either side.`
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
- Acceptance criteria: `107 is correctly anchored AND the anchor reaches
  BOTH demand branches, PFE and routeSampler (done). Gate S and Gate M are
  frozen and decided — both still pending: Gate S needs pair-sum-preserving
  q artifacts AND the route files, Gate M needs dataset v2. The four-outcome
  matrix then selects Exit A/C or Gren B/D.`
- Useful checks: `python3 -m pytest tests/test_sensor_107_directional_reference.py
  tests/test_dirsplit_legacy_pin.py tests/test_direction_decision_sensitivity.py
  tests/test_dirsplit_gate_m.py -q`;
  `python3 -m dirsplit.evaluate` reproduces the Gate M report byte for byte.
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
