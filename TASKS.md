# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Closure integrity stages 3-4, on a branch, alongside the
  running annual warming`
- Status: `IMPLEMENTED, TWO GATES UNRUN. Closure runs now disable SUMO
  teleporting (the necessary condition stage 1 measured for closed-edge
  throughput), with the policy recorded beside every integrity verdict so a
  zero teleport count cannot be read as a healthy run. Held-out v10 adds a
  pre-outcome "the edge must survive its own closure" condition and is
  registered for exact demand binding before its freeze. Neither the stage 3
  paired measurement nor the v10 freeze has been run: both need SUMO plus the
  canonical demand archive, and this container has neither. The annual warming
  plan 38d91d22… continues untouched on the dev machine.`
- Suggested next action: `On the dev machine, and BETWEEN warming runs: run
  tools/measure_closure_teleport_policy.py for the stage 3 gate, then
  tools/freeze_heldout_v10.py --dry-run before freezing. Also run
  tools/remeasure_closure_disqualification.py against the stored v9 outcomes —
  that one needs no SUMO.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot artifacts as release
  evidence. Do not edit annual plan-bound inputs while warming is active —
  run_scenario.py, suggest_closure_time.py and monthly_sumo.py are bound, so
  MERGING this branch during a population run discards the units already built.`
- Updated: `closure integrity stages 3-4 implemented on
  claude/road-closing-improvement-v9sizg; 97 focused tests pass and two pinned
  canary assertions were deliberately updated / 2026-08-10`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-INTEGRITY-34 — Stages 3 and 4 of the closure-integrity plan

- Status: `IMPLEMENTED; the two measured gates are UNRUN`
- Objective and scope: `Close out
  docs/plans/CLOSURE_INTEGRITY_PLAN_2026-08-05.md. Stages 1-2 were already
  measured and revised/refuted its premises; this delivers stage 3 (an explicit
  teleport policy for closure runs), stage 4 (a pre-outcome survivability
  condition for held-out v10) and the unplanned finding's re-score. Stage 5 is
  superseded by the warming plan.`
- Completion outcome: `Closure runs pass --time-to-teleport -1; every other
  caller's argv is byte-identical. The policy travels with the run so a zero
  teleport count is never readable as evidence on its own. closure_disruption
  splits denied departures from severed destinations. v10 refuses any candidate
  that severs a destination or cuts off a successor, inherits everything else
  from v9 unchanged, and is registered for exact demand binding BEFORE its
  freeze.`
- Context or checkpoints: `The MECHANISM is measured against real SUMO on the
  eight-edge c1 probe (validation/closure_teleport_mechanism_probe_v1.json):
  default arm throughput 1 / teleports 1 / unfinished 0, policy arm 0 / 0 / 1.
  Neither GATE can be decided here: sumo/ and runs/ are gitignored, so there is
  no calibrated demand and no canonical archive.`
- Primary files: `traffic_sim/simulation/closure_teleport.py,
  traffic_sim/simulation/closure_survivability.py, run_scenario.py,
  suggest_closure_time.py, traffic_sim/simulation/monthly_sumo.py,
  traffic_sim/simulation/heldout_selection.py, run_monthly_proxy_validation.py,
  tools/freeze_heldout_v10.py, tools/measure_closure_teleport_policy.py,
  tools/remeasure_closure_disqualification.py`
- Constraints and safety: `Option D of the plan (stop counting teleport-induced
  entries as leaks) stays rejected — nothing here loosens a measurement. The
  baseline arm keeps SUMO's default teleporting, so a paired study retains a
  live integrity signal. Merging during an active warming run discards its
  built units.`
- Acceptance criteria: `Stage 3 is decided by
  validation/closure_teleport_policy_v1.json: throughput measured and zero, no
  teleports, and the unfinished/dropped population growing by no more than the
  demand-side vehicles_no_detour budget. Stage 4 is decided by a v10 freeze
  carrying at least four surviving cases.`
- Useful checks: `pytest -q tests/test_closure_teleport.py
  tests/test_closure_teleport_wiring.py tests/test_closure_teleport_probe.py
  tests/test_closure_survivability.py tests/test_closure_disruption.py
  tests/test_heldout_v10_freeze.py
  tests/test_remeasure_closure_disqualification.py (105 passed);
  git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
