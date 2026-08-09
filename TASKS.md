# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Warming cleanup, safe throughput and presentation validation`
- Status: `DONE. Seventeen superseded annual banks plus pilot state stores were
  removed, freeing 19,171,217,408 allocated bytes; historical JSON/log evidence
  remains. The current 38d91d22… root is initialized with 104,685 pending and
  zero attempts. Three state-workers plus demand prefetch remains the fastest
  measured safe schedule (~18% pipeline improvement); eight workers cannot be
  fed by one group's three q-chains. A per-sensor temporal TAG presentation
  report now exposes ratio, GEH, flow-band criterion and rank gain. A separate
  all-sensor total-volume test includes 1076 and passes with 3.9% aggregate
  error against the declared project limit of 10%; strict TAG is unchanged.`
- Suggested next action: `If full warming is started, use the recorded three-worker
  prefetch schedule and monitor demand/PFE timing. Prototype two concurrent
  demand groups only as a separately benchmarked, byte-identical six-worker
  arm. Present calibration success and temporal holdout limitations separately.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot artifacts as release
  evidence. Full warming population and product activation remain separate.`
- Updated: `active build fa259a2892a974c27e8c; plan 38d91d22…, preflight
  f31f89ef… with 3 state-workers and 144,171,651,072 free bytes; 0/104685
  current-plan units attempted; 24 TAG/aggregate evaluator tests passed / 2026-08-09`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### WARMING-PRESENTATION-V14 — Clean bank and honest sensor evidence

- Status: `DONE; current bank empty and ready, full warming not started`
- Objective and scope: `Remove superseded warming data, retain decision evidence,
  choose the fastest already-proven safe schedule and publish presentation-ready
  per-sensor validation without conflating calibration with holdout evidence.`
- Completion outcome: `Only the current content-keyed annual root remains;
  preflight records the actually usable three-worker schedule; all sensor rows
  show complete-hour coverage, daily ratio, hourly GEH distribution, TAG
  flow-band result and structural observability meaning. The presentation leads
  with a simple six-sensor aggregate-volume test; no station is omitted.`
- Context or checkpoints: `Temporal holdout has 143 complete hours: GEH<5
  65.0%, flow-band 81.8%, either 82.5% versus >85%. Sensor 134 is closest in
  daily volume (ratio 1.015); 1076 is weakest (0.563, GEH<5 29.2%). All six
  rank gains are 1. Calibration-day raw SUMO output remains GEH<5 100%; closure
  stress remains verified_clean with zero teleports, collisions or edge leaks.`
- Primary files: `tools/validate_dmrb.py, tests/test_validate_dmrb.py,
  validation/sensor_validation_presentation_20260809.md,
  validation/annual_warm_cleanup_20260809.json,
  docs/plans/WARMING_SPEED_RESEARCH_2026-08-08.md`
- Constraints and safety: `Do not call rank gain a 0–1 score. Do not present
  calibration GEH as independent validation. Do not increase state workers or
  weaken exactness without a paired whole-pipeline benchmark.`
- Acceptance criteria: `Superseded state blobs removed without deleting current
  plan/evidence; current root starts at zero attempts; presentation table is
  reproducible from temporal report; official current TAG thresholds are used.`
- Useful checks: `24 focused TAG/aggregate tests pass; aggregate measured 25,761
  versus simulated 24,761 (3.9% error, PASS at <=10%); current progress is 104685 pending,
  0 running/succeeded/failed; preflight verifies; presentation regeneration
  reproduces 93/143 GEH, 117/143 flow-band and 118/143 combined-project cases.`
- Evidence: `validation/annual_warm_cleanup_20260809.json,
  validation/aggregate_sensor_volume_test_20260809.json and
  validation/sensor_validation_presentation_20260809.md`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
