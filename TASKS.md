# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. Historical task details live in `docs/history/TASKS_history.md`.
See `AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Close and commit the trained direction-split work together
  with its Gate M/S evidence and the full program/evidence audit.`
- Status: `DONE. Gate M v5 selects the trained q50 model, Gate S v6 returns NO
  on 48/48 clean runs, and the validation-report build-coherence defect found
  by the audit now fails closed. Current validation is truthfully overall=warn
  because the retained baseline belongs to an older demand build.`
- Suggested next action: `Build and deliberately publish a baseline matching
  demand build 4afe9e3ae2e74a4b872e, then rebuild current temporal holdout.
  The largest later accuracy gain is 3-5 measured boundary/cordon stations.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve historical q, closure and release evidence. Never
  weaken calibration, lineage, health, topology, no-detour, held-out or resource
  gates. The trained model runs only on weekday 06-20 support; 50/50 is the
  point fallback elsewhere. Learned opposite flow is soft plus ceiling-only,
  never a positive floor. Gate S and q10/q90 remain diagnostic stress evidence,
  not calibrated intervals or release evidence.`
- Updated: `2026-08-15 Codex — dirsplit closeout, evidence audit and internal
  validation identity gate completed and verified.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### DIRSPLIT-CLOSEOUT-1 — Finish direction split and bind the evidence chain

- Status: `DONE — implementation, generated data/model, gates, diagnostics,
  documentation and fail-closed validation identity are aligned.`
- Objective and scope: `Train the direction split on supported source data,
  preserve local measurements and 50/50 fallback semantics, repair paired
  stress artifacts, measure decision sensitivity, and document what the full
  program can and cannot claim.`
- Completion outcome: `Weekday 06-20 trained q50 is active where supported;
  local directional references take precedence; 50/50 remains the fallback;
  q10/q90 remain uncalibrated stress cases. Gate M v5=MODEL and Gate S v6=NO
  are separate, provenance-bound decisions. Mixed-build validation now warns
  and withholds stale scenario evidence.`
- Context or checkpoints: `Training uses 232 500 usable source rows and the
  product model uses 23 472 supported rows from 83 stations. Gate S has 48/48
  usable observations, zero hard failures and identical viable set, ranking,
  winner and decision costs across q10/q50/q90. Spatial LOSO remains
  underidentified and no release claim was added.`
- Primary files: `dirsplit/; demand/; traffic_sim/demand/pfe.py;
  build_sumo_demand.py; prior_flows.py; data/dirsplit/; validation/dirsplit_*;
  tools/measure_direction_decision_sensitivity.py;
  tools/measure_dirsplit_magnitude_shape.py;
  traffic_sim/confidence/report.py; tests/; README.md; ARCHITECTURE.md;
  IMPROVEMENT_PLAN.md; docs/PROGRAMGENOMGANG_OCH_EVIDENSAUDIT_2026-08-15.md.`
- Constraints and safety: `Preserve measured counts, source provenance and
  historical evidence. Learned opposite direction is soft plus ceiling-only;
  q10/q90 and Gate S are diagnostic, not calibrated intervals or release
  evidence. Do not treat an old baseline as current.`
- Acceptance criteria: `Met: data/model artifacts are reproducible and parse;
  complementary pairs are valid; Gate M/S outcomes and activation policy are
  explicit; relevant tests pass; validation report fails closed across build
  mismatch; documentation describes the deployed policy and limitations.`
- Useful checks: `git diff --check; parse every changed JSON; load model.pkl;
  focused dirsplit/demand/PFE/Gate S/validation-report pytest suites; verify
  validation overall=warn and the exact baseline/demand build identities.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only; per `AGENTS.md`, nothing outside
the marked blocks above is current.
