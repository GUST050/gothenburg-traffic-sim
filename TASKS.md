# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. Historical task details live in `docs/history/TASKS_history.md`.
See `AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Validate and plan a reusable daily-anchored, time-varying
  direction split for two-way TOT sensors, beginning with sensor 107.`
- Status: `RESEARCHED PLAN COMPLETE — the plan separates the published 2025
  period aggregate (3 400/3 100) from an optional daily-exact model policy,
  defines a logit-projected and regularized intraday profile, and requires a
  new Gate D over masked truly directional stations before activation. No
  product code, active architecture or frozen evidence has changed.`
- Suggested next action: `Execute D0-D4 in
  docs/plans/TOTAL_SENSOR_DAILY_DIRECTION_SPLIT_PLAN_2026-08-16.md: freeze the
  baseline, build the raw day-blocked pseudo-TOT benchmark, preregister the
  candidate family and run blocked-date, leave-station-out and leave-city-out
  Gate D before changing demand intake.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve historical Gate M/S, demand, route, closure and
  release evidence. The 52/48 source is a 2025 aggregate, not daily or
  per-quarter measurement. Do not hardcode sensor 107, silently transfer the
  anchor to 2027, alter single-direction Level-1 targets, let PFE absorb
  residuals through free split variables, or label uncalibrated q arms as
  intervals. Missing remains missing and both directions must preserve every
  measured two-way total exactly.`
- Updated: `2026-08-16 Codex — user reprioritized the current focus to a
  generalizable daily TOT split; primary-source research and repository
  diagnostics were converted into a Gate-D implementation plan.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### DIRSPLIT-TOT-DAILY-1 — Generaliserbar dygnsförankrad TOT-split

- Status: `READY FOR EVIDENCE BUILD — researched plan complete; implementation
  and Gate D have not started.`
- Objective and scope: `Test whether sensor 107 should use 52.3077/47.6923 as
  an exact volume-weighted daily policy while allowing a more realistic,
  data-learned intraday shape, and make the method reusable for future
  two-directional TOT sensors without sensor-specific code.`
- Completion outcome: `A provenance-bound pseudo-TOT daily dataset, registered
  flat/period/daily/band candidates, complete blocked-date/station/city Gate D,
  a pure whole-day projection contract, explicit missing/DST/future-year
  behaviour and, only after a passing gate, an integrated auditable policy.`
- Context or checkpoints: `The active 107 profile is already nearly daily
  fixed (about 52.22-52.36% over valid 2025 days) but spans only about 1.9
  percentage points intraday after anchoring. Research supports local and
  time-specific directional profiles, but does not turn a period aggregate
  into measured daily truth. Existing Gate M v5 remains authoritative for the
  current model; new daily-shape evidence gets a separate append-only Gate D.`
- Primary files: `docs/plans/TOTAL_SENSOR_DAILY_DIRECTION_SPLIT_PLAN_2026-08-16.md;
  future dirsplit daily dataset/evaluator; traffic_sim/intake/sensors.py;
  demand/intake.py; build_sumo_demand.py; validation Gate D records and focused
  tests.`
- Constraints and safety: `Separate source evidence from application policy;
  preserve per-slot totals and complementary shares; project the whole day
  before slicing a window; never infer daily truth from the 2025 aggregate;
  never train amplitude on 107's unknown directions; never give PFE free split
  authority; preserve all unrelated dirty changes and frozen artifacts.`
- Acceptance criteria: `Gate D contains blocked date, station and city folds;
  daily-exact must first prove non-inferior to period-only; a shaped candidate
  must beat flat daily in every required fold family without a primary-group
  loss; unsupported or incomplete inputs fall back visibly; another TOT sensor
  can be added through registry data and policy only.`
- Useful checks: `Raw-manifest digest; masked paired-direction benchmark;
  per-slot total/complement property tests; daily weighted equality; amplitude
  invariance; multi-day/full-day-before-window; DST/missing/zero/future-year;
  single-direction byte identity; clean-checkout provenance verification; Gate
  S rerun only if the selected profile materially changes closure inputs.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only; per `AGENTS.md`, nothing outside
the marked blocks above is current.
