# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Use the adopted canonical weekday/weekend route catalog and
  continue reducing first-new road-closure p95; keep full annual warming
  disabled until explicitly launched.`
- Status: `CATALOG QUALIFIED, ADOPTED AND SOAKED. A new schema-v2 campaign used
  the same 6,000-candidate request in both arms for 30 counterbalanced pairs.
  Median cold demand preparation fell 55.246→24.715 s (2.235x), every day
  class was faster, PFE p95 improved, adapter p95 was 0.678 s and all hard
  correctness/resource gates passed. Seven catalog fixtures plus an explicit
  legacy rollback passed soak. The active 2027-09-08 release uses the verified
  weekday key, has 20,818 vehicles and matches all 672 integer sensor targets
  exactly. A refreshed annual plan/preflight passed and exactly one q50 warm
  state was produced and verified; the remaining 104,684 units were not run.
  Stress seeds map q10/q50/q90, widened PFE rungs cannot publish non-exact
  sensor counts, and mixed catalogs namespace tour IDs. Synthetic 50-sensor
  exact-projection and output gates pass, while real calibrated 50-station
  evidence remains open. The broader
  performance implementation remains in progress. The named external monthly search
  ui-monthly-euc9qp was stopped at 476/1,776 and its workspace remains
  resumable. Current server wiring already runs three interactive seeds in
  parallel (measured baseline 11.0 -> 5.9 s and closure 21.6 -> 13.9 s,
  byte-identical apart from generated_at). A clean 10-trial active-demand run
  now pins first-new closure p95 at 10.496 s: all outputs and 30 seed-health
  records match, but the <=10 s goal is still missed by 0.496 s. Exact-repeat
  structured cache p95 is 0.329 s over 10/10 verified hits with zero SUMO work,
  safely inside its <=2 s goal. Exact structured closure caching, result-neutral daily timing,
  opt-in routing controls and an active-slot guard are implemented. The
  qualified entered/timeLoss-only edgeData output is now the production
  default, with an isolated full-field rollback flag. Deterministic disruption
  analysis is split from publication and uses exact grouped/sparse shortest
  paths: the active process-free case fell from 4.1364 s to 1.051 s while
  matching the retained oracle. A per-content-key cross-process single-flight
  lock now prevents duplicate matched-baseline producers. Two
  live-shaped reusable-Python-worker single draws preserved exact evidence but
  measured 1.027x and 0.998x; they are insufficient to accept or reject the
  pool. The replacement harness requires at least four counterbalanced paired
  trials and the generic pool remains inactive. The new closure profile has
  median SUMO 6.636 s, disruption 1.184 s and trajectory publication 1.131 s;
  these measured phases, not another generic pool, define the next work. Targets remain
  exact-repeat p95 <=2 s, async ack p95 <=1 s, new validated closure p95 <=10
  s, and >=2.0x monthly verified-unit throughput without semantic or evidence
  changes. These targets now explicitly cover 50 physical stations and the
  larger calibrated population their evidence may require. Fixed-load 50-row
  validation measured 3.325 ms p95 and is not the closure bottleneck; a
  vehicle-load diagnostic completed 42,816 vehicles at 1.57x the 21,408 wall
  time, while an 85,632 arm collapsed with 28,977 vehicles waiting. Minimal
  production-field edgeData passed its 40-run/120-seed paired semantic and
  health gate, cutting baseline wall 16.4% and closure wall 7.1%, and is now
  adopted. The active demand/release and unresolved Gate S/M state remain as
  recorded in IMPROVEMENT_PLAN.md. The large-simulation target structure and
  staged evidence requirements are documented in
  LARGE_SIMULATION_FUNCTION_STRUCTURE_2026-08-23.md.`
- Standard-pool checkpoint: `The isolated post-picker tool binds date/build,
  route, network and targets; creates three deterministic explicit
  speedFactor arms; preserves all 21,240 vehicles/routes; and reached raw
  672/672 per arm with clean health. Spacing stayed non-bunched (median
  2.7→2.5 s, max one departure/s). It is not active: ten paired concurrent
  baseline trials measured median 1.805→1.846 s (+2.30%), and closure-shaped
  equivalence/performance is unmeasured.`
- Suggested next action: `Keep the passing exact-repeat cache unchanged and
  reduce at least 0.5 s from the first-new path, starting with a bounded paired
  experiment on its measured SUMO/trajectory/disruption phases. Also measure
  the still-open async acknowledgement p95. Do not launch the remaining 104,684 annual warm units or activate
  rejected generic/driver pools without a separate explicit decision.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve user-owned uncommitted source/generated artifacts
  and all frozen q, release and closure evidence. Do not weaken
  calibration, equivalence, provenance, health, survivability, failure-recall,
  regret, resource or held-out gates. Do not couple demand-case identity to
  random seeds, splice cost fields across scenarios, silently exclude roads
  for low observability, or activate policy/UI/global-best claims without the
  applicable frozen evidence. Do not hardcode 107's annual 0.5231 as 96
  measured quarters or present q10/q90 as calibrated probabilities.`
- Updated: `2026-08-24 after matched-size schema-v2 qualification, adoption,
  soak, active-day rebuild, one bounded q50 warm-state pilot and clean-tree
  exact-repeat/first-new closure p95 measurement.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### PERF-CLOSURE-2026-08-21 — Faster exact closures and monthly simulation

- Status: `IN_PROGRESS — the canonical route catalog is schema-v2 qualified,
  adopted and soaked; production demand preparation now defaults to catalog
  with an explicit tested legacy rollback. Full annual warming remains
  inactive after one successful bounded q50 pilot. Repository and primary-source research is complete;
  the named monthly run is explicitly paused and the phased implementation
  goal is active. Exact structured closure caching, result-neutral daily timing,
  isolated opt-in routing controls and an active-slot guard are implemented.
  Sol review repaired cache identity/single-flight/workspace locking, telemetry
  failure isolation, nested-concurrency admission and external monthly
  lifecycle handling. Production now defaults to entered/timeLoss edgeData;
  phase schema v2 separates disruption, payload construction and artifact
  publication; exact grouped/sparse disruption matches the retained oracle;
  and matched-baseline cache publication is protected by a cross-process
  per-key single-flight lock. The two generic reusable-Python-worker arms are
  single-draw diagnostics (1.027x and 0.998x), not rejection evidence; the new
  counterbalanced multi-trial harness is implemented but not yet run. The prior
  strict single-flight regression now passes. Multi-trial p95 and remaining
  resource-allocation evidence are still open.`
- Objective and scope: `Reduce cold daily demand preparation by reusing a
  verified weekday/weekend routed catalog without changing exact daily PFE or
  warm-state semantics. Also reduce exact-repeat and first-new interactive closure
  latency and increase independent-exhaustive monthly-search throughput. Start
  with measurement and one resource budget; then test output reduction,
  routing, exact result
  reuse, allocation, cost-ordered stopping and targeted warm prefixes in that
  evidence order. Prove the same <=10 s interactive p95 at 50 physical sensors
  when simulation load is fixed, then prove calibrated population tiers because
  new observations can require more vehicles. Keep high-risk custom
  SUMO/libsumo work profile-gated.`
- Completion outcome: `Exact repeat p95 <=2 s with zero SUMO calls; async ack
  p95 <=1 s; new validated closure p95 <=10 s; monthly verified-unit throughput
  >=2.0x the frozen baseline with a <=24 h stretch target on the named
  1,776-schedule/2,224-unit fixture. Every adopted arm preserves required
  semantics, evidence, health, restart and provenance fields.`
- Context or checkpoints: `Three interactive seed workers are already adopted;
  their recent adoption check measured the closure at 13.9 s. Persistent
  external SUMO was
  semantically equivalent but slower and is closed. Monthly eight-worker
  evidence measured 1.69x with 2.11 GiB RSS. Cost-order v5 saved calls but
  failed equivalence. A two-unit frozen-input diagnostic measured a standard
  reusable Python worker at 1.027x; the six-unit/three-worker equal-cache
  follow-up measured 0.998x with exact evidence. Both are single draws and do
  not establish a speed decision. The cold-cache arm reproduced
  duplicate baseline publication before the single-flight repair. The repaired
  multiprocess regression proves one producer per content key. A multi-snapshot warm pilot measured 20.8x state creation
  but lacks ordinary SUMO meso-accumulator serialization. The stopped search
  ui-monthly-euc9qp is 476/1,776; its manifest records an interrupted_by_user
  progress pointer with resumable scratch. The active process-free disruption
  case improved from 4.1364 s to 1.051 s exactly. One isolated three-seed
  production-shaped closure completed in 10.690 s versus 11.549 s with the
  full-field rollback, with equal scenario/trajectory digests and clean health;
  this is diagnostic evidence, not a p95 result. The fair catalog campaign ran
  30 matched 6,000-candidate pairs and measured 55.246→24.715 s median
  (2.235x); seven catalog soak fixtures and legacy rollback passed. The active
  catalog-built day matches 672/672 integer sensor targets, and its bounded
  annual q50 warm pilot succeeded 1/1 with no full-year launch.`
- Primary files: `docs/plans/CANONICAL_ROUTE_CATALOG_PLAN_2026-08-24.md;
  docs/plans/ROAD_CLOSURE_SIMULATION_SPEED_PLAN_2026-08-21.md;
  docs/plans/DAILY_SIMULATION_CONCURRENCY_STRUCTURE_2026-08-21.md;
  docs/plans/FIFTY_SENSOR_PERFORMANCE_CONTRACT_2026-08-22.md;
  docs/plans/LARGE_SIMULATION_FUNCTION_STRUCTURE_2026-08-23.md;
  run_scenario.py; serve.py; tools/benchmark_speed.py;
  traffic_sim/simulation/disruption.py;
  traffic_sim/simulation/execution.py;
  traffic_sim/storage/singleflight.py;
  traffic_sim/simulation/independent_daily.py;
  traffic_sim/simulation/independent_daily_worker.py;
  traffic_sim/simulation/monthly_sumo.py; run_monthly_closure_search.py;
  tools/cost_ordered_benchmark_suite.py; related tests and new frozen validation
  contracts/outcomes; tests/test_closure_disruption.py;
  tests/test_singleflight.py.`
- Constraints and safety: `The named monthly run is paused; still bind every
  S0/reference run to frozen inputs and isolated output. Do not compete with a
  newly resumed evidence-producing run.
  Do not reduce seeds/variants, closure horizon, rerouter coverage, recovery,
  precision or outputs needed by the product; do not loosen timeouts or gates;
  do not activate proxy/global-best/cost-first claims without new frozen
  equivalence and held-out evidence. Preserve all user-owned dirty files and
  failed historical outcomes.`
- Acceptance criteria: `For the catalog sub-plan, preserve every hard demand,
  structure, provenance, 50-station and warming identity gate; catalog
  restore/materialization p95 <=5 s; paired cold-day median at least 25% faster;
  PFE p95 regression <=5%; one-time catalog build amortized within three cold
  days. Also meet the per-product thresholds in the speed plan on
  frozen inputs; identical scenario/trajectory semantic digests and per-seed
  flow/health/integrity evidence; identical monthly legal candidates,
  observations, failures, selected ids, winner, restart and cache behavior;
  at 50 physical stations with otherwise identical simulation load, closure
  p95 <=10 s, <=5% latency regression, and sensor-only validation p95 <=50 ms;
  every published demand build matches every directed sensor-edge × 15-minute
  target exactly after the declared whole-vehicle rounding rule, with zero
  maximum and summed residual;
  calibrated 21k/32k/43k/50k/60k tiers meet their 10/20/30 s budgets with
  loaded=inserted and zero waiting, stopping at the first failed capacity tier;
  peak RSS <=8 GiB for the named host benchmark; no new timeout, orphan or
  cancellation defect; old path remains a tested rollback until adoption.`
- Useful checks: `make lint; focused scenario timing/benchmark tests; focused
  independent_daily/monthly_sumo/cost-order tests; paired idle-host benchmark
  reports; semantic digest and field-by-field evidence comparison; restart,
  cancel, corrupt-cache and process-reaping tests; 6/25/50-station PFE and
  21k/32k/43k/50k/60k calibrated-vehicle load matrix; qualified minimal-edgeData
  production-default/rollback checks; git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
