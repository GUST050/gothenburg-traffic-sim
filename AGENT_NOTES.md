# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Main. The direction-split plan is scope-corrected and
  decision-gated; no source implementation or policy activation has begun.`
- Summary: `The end-to-end audit found that today's q10/q50/q90 are learned
  from station-hour means, not raw day-level variation; weekend/off-hour
  predictions lack training support; applicability only covers static
  features; and global marginal quantiles are not coherent daily scenarios.
  q50 has only a 0.0008 pooled MAE advantage over 50/50 after shrinkage and is
  worse in three of four held-out domain cities. Review then established that
  only sensor 107's split directly creates two Level-1 targets; five opposite
  directions are surrenderable Level-2/3 evidence. The plan now starts with
  107's local 52/48 period anchor and a bounded matched-seed sensitivity study.
  Gates S/M/P prevent speculative scenario/monthly/warm/API/UI work.`
- Files changed: `Documentation only:
  docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md;
  IMPROVEMENT_PLAN.md pointer; current TASKS.md and AGENT_NOTES.md blocks.`
- Checks: `git diff --check clean; plan has balanced code fences and all Gate
  S/M/P, Exit A/C, Gren B/D and sensor-107 contract terms are present; current
  marker counts are exactly one start/end pair. No code tests were required
  because source behavior is unchanged.`
- Decisions and evidence: `50/50 winning does not imply zero variance, so exit
  requires both Gate M=BASELINE and Gate S=NO. The other combinations lead to
  no ensemble, a residual-only prototype, or a conditional-model prototype as
  documented. Sensor 107's annual D-factor is a local period anchor, not 96
  directed measurements. Existing closure v5 evidence remains unchanged.`
- Blockers or risks: `The raw, citable source/period semantics for 107's
  3,400/3,100 values must be bound before treating them as product evidence.
  Gate S must be preregistered before rerunning SUMO. Raw Norwegian day-block
  availability is measured later in Fas 1. No scenario cap or risk policy is
  needed unless Gate S/M open that branch.`
- Suggested next action: `Fas 0A only: add the provenance-bound 107 reference,
  anchor its period mean, and regression-test that the five directional
  Level-1 sensors remain unchanged. Then freeze Fas 0B; do not build schemas or
  product integration.`
- Actor notes: `Research used primary statistical, scenario-generation,
  traffic-monitoring and microsimulation sources. No existing evidence was
  edited, external data downloaded, policy activated or runtime gate weakened.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
