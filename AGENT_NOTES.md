# Agent Notes

Only the marked `CURRENT_HANDOFF` block is current coordination context.
Historical detail lives in `docs/history/AGENT_NOTES_history.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `A researched, generalizable plan for daily-anchored,
  time-varying direction split at TOT sensors is complete; evidence build and
  implementation are not started.`
- Summary: `The plan keeps sensor 107's published 3 400/3 100 as a 2025 period
  fact and represents exact 52.3077/47.6923 per day as a separate optional
  model policy. It proposes a TOT-semantically matched daily profile on the
  logit scale, held-out-learned amplitude and a one-offset whole-day projection
  that preserves every measured total. A new Gate D compares flat, incumbent,
  daily-exact, daily-band and shaped policies over masked directional stations
  before product activation.`
- Files changed: `New
  docs/plans/TOTAL_SENSOR_DAILY_DIRECTION_SPLIT_PLAN_2026-08-16.md plus current
  references in IMPROVEMENT_PLAN.md, TASKS.md and AGENT_NOTES.md. Pre-existing
  dirty solver/NVDB and test changes were preserved.`
- Checks: `Repository direction path and tests inspected; current 107 q50 and
  2025 totals measured read-only. The anchored profile spans about 51.56-53.46%
  and produces about 52.22-52.36% daily share on valid 2025 days. Primary
  sources reviewed: FHWA TMG/HPMS, Trafikverket traffic-variation guidance,
  KDOT hourly directional factors, SUMO count-to-route documentation,
  compositional logit modelling, trend filtering, blocked CV and time-series
  conformal calibration. Marker counts and git diff --check pass. The combined
  publication suite reports 666 passed, 1 skipped and 13 failed. A detached
  HEAD comparison reproduces all 12 warm-state failures, so they predate this
  worktree. The remaining failure is the intended fail-closed Gate M check:
  dirsplit/evaluate.py changed while data/dirsplit/gate_m_report.json still
  binds the prior source digest.`
- Decisions and evidence: `Do not let PFE choose free per-quarter shares.
  Estimate the shape outside PFE, project it to the declared daily policy and
  pass complementary totals downstream. Do not hand-pick 35/65 or 40/60;
  learn and shrink amplitude under blocked date/station/city validation. A
  daily-exact policy must first show non-inferiority to period-only because the
  local source does not measure daily split. Gate M v5 remains current for the
  existing product; Gate D is append-only and answers the new question.`
- Blockers or risks: `Sensor 107 cannot validate its own intraday shape because
  only its two-way total is measured. Norwegian paired stations provide the
  available benchmark but may not transfer to Gothenburg; low applicability or
  any missing fold gives INCONCLUSIVE. Full-day handling of gaps and DST must be
  explicit, and a 2025 anchor cannot silently become a 2027 policy. Before
  merge, the Gate M source/report drift must be resolved through a new
  provenance-bound report/decision or by separating the source change; frozen
  evidence must not be rewritten merely to make the test pass.`
- Suggested next action: `Execute plan D0-D4 only: freeze the baseline, create
  the provenance-bound day-blocked pseudo-TOT table, preregister candidates and
  run Gate D. Do not edit demand intake until an outcome selects a policy.`
- Actor notes: `Planning/research only. No source implementation, sensor data,
  demand, network, solver, frozen validation artifact or external system was
  changed; nothing was pushed. The prior solver/NVDB hardening plan remains in
  IMPROVEMENT_PLAN.md but is reprioritized by the user's latest request.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in
`docs/history/AGENT_NOTES_history.md` (14,681 lines). It is preserved context
only; per `AGENTS.md`, nothing outside the marked block above is current.
