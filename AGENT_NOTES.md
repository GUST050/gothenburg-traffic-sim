# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Main. Direction-split uncertainty v2 is the current focus.
  A researched implementation plan exists; no source implementation or policy
  activation has begun.`
- Summary: `The end-to-end audit found that today's q10/q50/q90 are learned
  from station-hour means, not raw day-level variation; weekend/off-hour
  predictions lack training support; applicability only covers static
  features; and global marginal quantiles are not coherent daily scenarios.
  q50 has only a 0.0008 pooled MAE advantage over 50/50 after shrinkage and is
  worse in three of four held-out domain cities. The plan uses central demand
  for broad screening, empirical joint residual-day scenarios for finalists,
  multiple matched SUMO seeds, complete-scenario ranking and evidence-aware
  observability.`
- Files changed: `Documentation only:
  docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md;
  IMPROVEMENT_PLAN.md pointer; current TASKS.md and AGENT_NOTES.md blocks.`
- Checks: `git diff --check clean; current marker counts are exactly one start
  and one end; all referenced existing primary source paths were found; plan is
  720 lines and its measured artifact values were re-read from the repository.
  No code tests were required because source behavior is unchanged.`
- Decisions and evidence: `Current q10/q90 remain legacy stress identities,
  not calibrated probability cases. Demand-case identity and SUMO seed must be
  orthogonal. Same cases/seeds must be paired across baseline and closure.
  Low observability widens uncertainty and narrows claims rather than banning a
  road; topology/no-detour remain hard gates. Existing closure v5 negative
  evidence and closed gates are unchanged.`
- Blockers or risks: `Raw Norwegian count availability and simultaneous
  station-day coverage must be measured in Step 1; they determine how strong a
  joint residual library can be. Exact calibration tolerances, scenario cap
  and risk preference must be preregistered before held-out measurement. The
  current model is not evidence for activating the proposed policy.`
- Suggested next action: `Implement Step 0 and Step 1: freeze legacy behavior,
  add the versioned DemandEnsemble schema/adapter, then preserve station-date-
  hour counts and day blocks in training_table_v2 with leakage tests.`
- Actor notes: `Research used primary statistical, scenario-generation,
  traffic-monitoring and microsimulation sources. No existing evidence was
  edited, external data downloaded, policy activated or runtime gate weakened.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
