# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Main. Gate M is measured and decided (BASELINE). Fas A of
  the 2026-08-16 remediation plan is landed: an evidence harness, its tests and
  an artifact. No model, pipeline, scenario or policy behaviour is changed.`
- Summary: `The August audit suspected the direction model was weak; it is now
  measured. train.py fits the shrinkage lambda by least squares on the pooled
  held-out pairs and then reports its MAE on those same pairs, so the published
  0.0557-vs-0.0565 margin is an upper bound, not a generalisation result.
  Refitting lambda on three cities and scoring on the fourth gives 0.0568
  versus 0.0565 for 50/50 (-0.53%); a 2,000-draw station-level bootstrap over
  41 stations gives delta +0.00030, 95% CI [-0.00301, +0.00406], P(model
  better)=0.32 — indistinguishable from writing 0.5. The per-fold lambda ranges
  0.166-0.484, which is the mechanism. Two further findings: the deployed
  [q10, q90] interval covers 39.3% of held-out observations against a nominal
  80% (under-dispersion; the shrinkage re-centring makes it marginally worse,
  41.2% -> 39.3%), and target_static_features() returns the away-from-centre
  carriageway for the four single-direction sensors 1076/133/134/2276, so their
  per-sensor kernels are centred outside the toward-centre-only training
  support while predict.py predicts on the mirrored side. Every per-sensor
  kernel effectively spans 68-91% of the training set, so "each road trained
  for itself" overstates the locality.`
- Files changed: `New: dirsplit/validate.py; tests/test_dirsplit_validate.py;
  validation/dirsplit_gate_m_20260816.json;
  docs/plans/DIRSPLIT_REMEDIATION_PLAN_2026-08-16.md. Edited: Makefile
  (dirsplit-validate target); CLAUDE.md and README.md stale-figure
  corrections; current TASKS.md and AGENT_NOTES.md blocks. No tracked model,
  demand, scenario or release artifact touched.`
- Checks: `python3 -m pytest tests/test_dirsplit_validate.py -q -> 22 passed.
  Full suite run before commit. python3 -m dirsplit.validate reproduces
  data/dirsplit/train_report.json exactly (lambda 0.289, raw 0.0641, shrunk
  0.0557, 50/50 0.0565) before measuring anything new, which is what makes the
  new numbers comparable. git diff --check clean; marker counts are exactly one
  start/end pair.`
- Decisions and evidence: `Gate M = BASELINE for the deployed model. This does
  NOT close the model tournament (dataset v2 and simpler candidates are
  untested) and does NOT imply zero variance — Gate S is still open and is now
  the only gate separating Exit A from Gren B. The remediation plan adds Fas F0,
  a preregistered last-chance retrain after mirroring the four mis-oriented
  target vectors, whose decision rule requires the bootstrap CI to exclude zero
  rather than a positive point estimate. Sensor 107's annual D-factor remains a
  local period anchor, not 96 directed measurements. Existing closure v5
  evidence and frozen q archives are unchanged.`
- Blockers or risks: `The q10/q90 labels are shipping today at 39.3% measured
  coverage; Fas E must rename or recalibrate them before the next published
  build, independently of Gate S. The raw, citable source/period semantics for
  107's 3,400/3,100 values must still be bound (Fas B). Gate S must be
  preregistered before rerunning SUMO. Only four cities exist, so a nested
  lambda has three blocks to fit on and city-level dependence is not captured
  by the station-level bootstrap — stated as a limitation in the plan, not
  worked around.`
- Suggested next action: `Fas B of the 2026-08-16 plan: the provenance-bound 107
  reference with its four named regression tests. Then preregister Fas C
  (Gate S): 4 demand cases x 4 matched seeds x 6 closures, common random
  numbers, decision rule written to validation/ before the first run. Do not
  build schemas or product integration.`
- Actor notes: `Measurements used dirsplit's own load_table/kernel_weights/
  make_model/target_static_features so the pipeline is identical to training.
  Research drew on FHWA TMG factor groups, GLUE/equifinality, ensemble
  under-dispersion diagnostics, DfT TAG proportionality and Bayesian demand
  calibration; sources are listed in the plan. lightgbm, scikit-learn, osmnx
  and pytest were installed into this session to run the harness. No existing
  evidence was edited, no external data downloaded, no policy activated and no
  runtime gate weakened.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
