# Agent Notes

Only the marked `CURRENT_HANDOFF` block is current coordination context.
Historical detail lives in `docs/history/AGENT_NOTES_history.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Direction-split closeout and full program/evidence audit
  complete on branch codex/fix-dirsplit-gates-v3; ready for one local commit.`
- Summary: `The package trains and evaluates the supported q50 direction model,
  preserves local-anchor and 50/50 fallback semantics, repairs complementary
  stress pairs, measures Gate S decision sensitivity, records negative
  magnitude/shape evidence, and documents the end-to-end program. The audit's
  mixed-build validation defect is also fixed fail-closed.`
- Files changed: `Direction dataset/train/evaluate/predict pipeline; demand
  intake/priors/publication/calibration and PFE; generated training/model/Gate
  M artifacts; Gate S registrations/outcomes and diagnostic tool; focused
  tests; README/architecture/improvement plan/program audit; validation report
  identity gate and current coordination blocks.`
- Checks: `568 dirsplit/demand/PFE/Gate-S/validation-report tests passed with
  1 skipped; all changed JSON parsed; model.pkl loaded;
  Python syntax passed using a temporary bytecode cache; git diff --check
  passed. Current validation reproduces demand=4afe9e3ae2e74a4b872e and
  baseline=fa259a2892a974c27e8c, now overall=warn with stale sections withheld.
  A whole-suite diagnostic was interrupted at 74% after 13m18s: 3 693 passed,
  27 skipped and 110 failed. The failures shown are historical freeze/campaign
  drift against current demand, source hashes and warming schemas; those
  immutable records were not rewritten to manufacture a green suite.`
- Decisions and evidence: `Gate M v5=MODEL selects
  similarity_weighted_lgbm_no_profile. Gate S v6=NO on 48/48 clean runs means
  q stress does not change the frozen closure decision; it is not release
  evidence. Next improvement is a matched current baseline and temporal
  holdout, not another dirsplit model. More boundary measurements remain the
  largest fundamental information gain.`
- Blockers or risks: `The retained published baseline is older than current
  demand, so simulation and sensor-output sections are intentionally missing
  until a deliberate matched baseline run. Temporal holdout is stale/missing.
  The repository-wide suite is not globally green because many historical
  freeze tests still assert retired live identities. Six stations in two
  clusters cannot validate citywide flows, queues or travel times.`
- Suggested next action: `Build/publish a baseline for demand build
  4afe9e3ae2e74a4b872e, confirm scenario_identity=pass and final output gates,
  then rebuild temporal holdout before adding boundary/cordon sensors.`
- Actor notes: `No push, release or publication was performed. Historical
  Gate M/S records were kept as separate versioned artifacts.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in
`docs/history/AGENT_NOTES_history.md` (14,681 lines). It is preserved context
only; per `AGENTS.md`, nothing outside the marked block above is current.
