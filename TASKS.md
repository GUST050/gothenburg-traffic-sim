# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. Historical task details live in `docs/history/TASKS_history.md`.
See `AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Use the completed matched baseline and current spatial/
  temporal held-out pair to select the next actionable accuracy improvement.`
- Status: `DONE. Baseline build 4afe9e3ae2e74a4b872e passed staging and is
  published; validation is overall=pass with zero warnings/missing sections.
  Identity-matched spatial LOSO is 0.466–1.354 (median 0.613) and temporal
  LOSO is 0.445–1.356 (median 0.6225); all six stations are underidentified.`
- Suggested next action: `With new sensors deferred, execute a reviewed NVDB
  road-structure import/audit for high-flow and closure-relevant edges first.
  Preserve stable edge IDs and compare routing/LOSO before and after.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve historical q, closure and release evidence. Never
  weaken calibration, lineage, health, topology, no-detour, held-out or resource
  gates. The trained model runs only on weekday 06-20 support; 50/50 is the
  point fallback elsewhere. Learned opposite flow is soft plus ceiling-only,
  never a positive floor. Gate S and q10/q90 remain diagnostic stress evidence,
  not calibrated intervals or release evidence. Root paths recorded by frozen
  evidence remain interfaces; archived evidence may be losslessly compressed
  but not silently rewritten or deleted.`
- Updated: `2026-08-15 Codex — matched baseline published, current paired LOSO
  completed, exact validation projection added without changing sealed demand,
  and improvement order updated to NVDB while sensors wait.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### CURRENT-BASELINE-HELDOUT-1 — Bind current baseline and generalization evidence

- Status: `DONE — registered baseline and held-out pair are complete and
  identity-bound; no release claim was added.`
- Objective and scope: `Replace stale mixed-build simulation/held-out evidence
  with a staged, validated baseline and one current spatial/temporal pair.`
- Completion outcome: `Three baseline arms passed health and output fit; both
  LOSO reports bind pool 75947f43…, network 68ecde39…, 06–10 reference and
  through-share 0.25. validation.json is overall=pass.`
- Context or checkpoints: `The first temporal attempt failed closed on HiGHS
  time limit, not infeasibility. An exact algebraically condensed integer L1
  model solved the blocking quarter in 0.494 s over the full route domain;
  production demand source and fingerprint remain unchanged. A floor/ceil
  diagnostic was rejected before final evidence because it was not globally
  equivalent under overlapping margins.`
- Primary files: `traffic_sim/confidence/report.py;
  traffic_sim/confidence/controlled_rounding.py;
  traffic_sim/confidence/loso.py; web/data/validation.json;
  web/data/loso_report.json; web/data/temporal_holdout_report.json;
  web/data/scenarios/; validation/*baseline*_v1.json;
  validation/current_heldout_*_v1.json; IMPROVEMENT_PLAN.md;
  docs/PROGRAMGENOMGANG_OCH_EVIDENSAUDIT_2026-08-15.md.`
- Constraints and safety: `Keep reports characterization-only; never weaken
  solver, identity, sensor, health or publication gates. Preserve prior live
  scenarios in the recorded backup. New sensors wait for real data.`
- Acceptance criteria: `Met: staging validator passed before publication;
  scenario/demand identity passes; both LOSO protocols match registered
  pool/network/window; all active measurement residuals are zero; temporal
  evidence is current; documentation and coordination state are updated.`
- Useful checks: `git diff --check; parse changed JSON; focused controlled-
  rounding/LOSO/validation-report tests; run validation_report.py and require
  overall=pass with zero warning/missing sections.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only; per `AGENTS.md`, nothing outside
the marked blocks above is current.
