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
- Status: `DONE. The Stage 3 paired gate passes on exact q50/q10/q90 demand:
  default throughput/teleports 1/1, policy 0/0, stuck growth 0 within budget 0.
  The corrected v2 topology screen finds 24/46 pool survivors and reproduces
  byte-for-byte. Held-out v10 is frozen with five cases / 75 schedules, refuses
  22 candidates by survivability, and reproduces byte-for-byte.`
- Suggested next action: `Integrate only BETWEEN warming runs because the
  plan-bound simulation source identity changes. The optional historical v9
  re-score can still be run separately; it is a projection, not a gate.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Do not use held observations in pool, picker or certificate.
  Do not weaken TAG/fit/provenance gates or promote pilot artifacts as release
  evidence. Do not edit annual plan-bound inputs while warming is active —
  run_scenario.py, suggest_closure_time.py and monthly_sumo.py are bound, so
  MERGING this branch during a population run discards the units already built.`
- Updated: `latest Claude push reviewed and corrected on
  codex/review-road-closing-v9sizg-latest; Stage 3 passes, Stage 4/v10 is frozen
  and reproducible, and evidence/provenance gaps are closed / 2026-08-10`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CLOSURE-INTEGRITY-34 — Stages 3 and 4 of the closure-integrity plan

- Status: `DONE; Stage 3 passes and Stage 4/v10 is frozen and reproducible.`
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
- Context or checkpoints: `validation/closure_teleport_policy_v1.json records
  the passing paired Stage 3 gate with exact input/source/SUMO identity and
  matched seeds. validation/closure_survivability_screen_v2.json supersedes v1:
  2066/7101 network edges are fatal, 24/46 pool candidates survive, and the
  content-keyed report reproduces. The v10 manifest key is
  c10b2dc9fbf8f0a9ad75d648224e1fdd0f43998c850590349678bf89cb07d5d7.`
- Primary files: `traffic_sim/simulation/closure_teleport.py,
  traffic_sim/simulation/closure_survivability.py, run_scenario.py,
  suggest_closure_time.py, traffic_sim/simulation/monthly_sumo.py,
  traffic_sim/simulation/heldout_selection.py, run_monthly_proxy_validation.py,
  tools/freeze_heldout_v10.py, tools/measure_closure_teleport_policy.py,
  tools/screen_closure_survivability.py,
  tools/remeasure_closure_disqualification.py`
- Constraints and safety: `Option D of the plan (stop counting teleport-induced
  entries as leaks) stays rejected — nothing here loosens a measurement. The
  baseline arm keeps SUMO's default teleporting, so a paired study retains a
  live integrity signal. Merging during an active warming run discards its
  built units.`
- Acceptance criteria: `Met. Stage 3's non-vacuous paired gate passes with
  measured zero policy throughput/teleports and bounded stuck growth. Stage 4
  has five surviving frozen cases and all artifacts reproduce.`
- Useful checks: `pytest -q tests/test_closure_teleport.py
  tests/test_closure_teleport_measurement.py
  tests/test_closure_teleport_wiring.py tests/test_closure_teleport_probe.py
  tests/test_closure_survivability.py
  tests/test_closure_survivability_screen.py tests/test_closure_disruption.py
  tests/test_heldout_v10_freeze.py
  tests/test_remeasure_closure_disqualification.py (128 passed, 5 skipped);
  git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
