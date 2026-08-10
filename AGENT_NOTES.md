# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Closure-integrity stages 3-4 implemented on
  claude/road-closing-improvement-v9sizg; both measured gates unrun. Annual
  warming untouched.`
- Summary: `Stage 1 had already REVISED the plan's premise — the leak and the
  teleport are not one event — but left the fact stage 3 rests on: a teleport is
  the NECESSARY condition for closed-edge throughput, 0 of 35 throughput
  schedules lacked one. Closure runs therefore pass --time-to-teleport -1. That
  makes teleport_total zero by construction, so the policy record travels with
  every run and states teleport_count_is_informative: false — otherwise the fix
  would quietly convert a real check into a vacuous one. Stage 4 adds a
  pre-outcome survivability condition to held-out v10, which required splitting
  closure_disruption's vehicles_no_detour into denied departures (access the
  closure removes; every busy street has some) and severed destinations
  (topology; disqualifies).`
- Files changed: `traffic_sim/simulation/closure_teleport.py (new),
  traffic_sim/simulation/closure_survivability.py (new), run_scenario.py,
  suggest_closure_time.py, traffic_sim/simulation/monthly_sumo.py,
  traffic_sim/simulation/heldout_selection.py, run_monthly_proxy_validation.py,
  tools/freeze_heldout_v10.py, tools/measure_closure_teleport_policy.py,
  tools/remeasure_closure_disqualification.py, five new test modules,
  tests/test_closure_disruption.py, tests/test_monthly_proxy_runner.py,
  tests/test_heldout_gate.py, docs/plans/CLOSURE_INTEGRITY_STAGES_3_4_2026-08-10.md,
  docs/README.md, docs/OPEN_ISSUES_2026-08-06.md, TASKS.md, AGENT_NOTES.md.`
- Checks: `105 passed across the seven closure-focused modules, including a
  SUMO-backed probe that measures the mechanism rather than asserting it:
  default arm closed-edge throughput 1 / teleports 1 / unfinished 0, policy arm
  0 / 0 / 1 (validation/closure_teleport_mechanism_probe_v1.json). Full suite
  compared against a clean worktree of the same base. Two pinned canary
  assertions updated deliberately (EXACT_DEMAND_BINDING_CAMPAIGNS gains v10;
  the v6 drifted-source set gains heldout_selection.py).`
- Decisions and evidence: `-1 rather than the plan's proposed finite threshold,
  because any finite value still teleports and stage 3's own gate requires
  throughput to reach zero. The BASELINE arm keeps SUMO's default so a paired
  study retains one live integrity signal. Warm and cold closure arms share the
  constant, so the warm arm stays an optimisation of an equivalent cold arm.
  MAX_CLOSURE_WAIT_S was separated from the teleport option: it models a driver
  who parks short of an eight-hour closure, not a simulator setting.`
- Blockers or risks: `Neither gate is decided — sumo/ and runs/ are gitignored,
  so there is no network and no canonical demand archive here. The teleport
  policy is an input to results exactly as REROUTER_RADIUS_M is, so existing
  scenario outputs are not comparable to new ones. run_scenario.py,
  suggest_closure_time.py and monthly_sumo.py are annual-plan-bound: merging
  during an active population run discards its built units.`
- Suggested next action: `On the dev machine, between warming runs: run
  tools/measure_closure_teleport_policy.py (stage 3 gate), then
  tools/freeze_heldout_v10.py --dry-run before freezing. The re-score,
  tools/remeasure_closure_disqualification.py against the stored v9 outcomes,
  needs no SUMO and can run any time.`
- Actor notes: `Work was done on a branch based on sensor-crossing-baseline,
  which is where the closure plan and its stage 1-2 evidence live; main is six
  days behind and has no closure_ranking module at all.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
