# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. Historical task details live in `docs/history/TASKS_history.md`.
See `AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Close the 9e9ecfd review findings without invalidating the
  matched baseline or rewriting frozen direction evidence.`
- Status: `DONE for P0/P1. SciPy is pinned to the last compatible solver range
  in both dependency entry points, clean-checkout dirsplit tests distinguish
  unit checks from machine-local evidence checks, and future Gate S provenance
  uses repository-relative paths. Historical v5/v6 limitations have an
  append-only correction record. No sealed demand source changed.`
- Suggested next action: `With new sensors deferred, execute a reviewed NVDB
  road-structure import/audit for high-flow and closure-relevant edges first.
  Preserve stable edge IDs and compare routing/LOSO before and after.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve historical q, closure and release evidence. Never
  weaken calibration, lineage, health, topology, no-detour, held-out or resource
  gates. The trained model runs only on weekday 06-20 support; 50/50 is the
  point fallback elsewhere. Learned opposite flow is soft plus ceiling-only,
  never a positive floor. Gate S v5/v6 are historical, nonportable diagnostic
  observations; q10/q90 are not calibrated intervals or release evidence. Root
  paths recorded by frozen
  evidence remain interfaces; archived evidence may be losslessly compressed
  but not silently rewritten or deleted.`
- Updated: `2026-08-16 Codex — solver compatibility and CI repaired without
  demand-source drift; Gate S reproducibility scope corrected append-only.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### POST-REVIEW-HARDENING-1 — Repair solver, CI and Gate S provenance

- Status: `DONE — P0 and both P1 findings are repaired; P2 is intentionally
  deferred to the next evidence-producing LOSO rerun.`
- Objective and scope: `Restore demand publication on CI's dependency stack,
  remove clean-checkout dependence on gitignored dirsplit artifacts, and make
  future Gate S seals portable without rewriting v5/v6.`
- Completion outcome: `Supported SciPy is pinned to >=1.11,<1.17 while the
  fork-safe one-thread HiGHS contract remains intact. Live-only tests skip with
  explicit missing-artifact reasons. Gate S provenance v2 records repository
  paths relatively, and a bound correction record narrows v5/v6 authority.`
- Context or checkpoints: `Changing pfe.py would drift a sealed demand source;
  the dependency pin fixes the observed 1.17 incompatibility without moving
  the current demand fingerprint. The remaining loso.py upper-middle console
  print is not published evidence and stays until a fresh registered LOSO run
  can bind that source change honestly.`
- Primary files: `requirements.txt; .github/workflows/ci.yml; tests/test_pfe.py;
  tests/test_dirsplit_magnitude_shape.py;
  tests/test_direction_decision_sensitivity.py;
  tools/measure_direction_decision_sensitivity.py;
  validation/dirsplit_direction_sensitivity_evidence_status_v1.json;
  ARCHITECTURE.md; IMPROVEMENT_PLAN.md.`
- Constraints and safety: `Do not weaken solver failure semantics, rewrite
  frozen outcomes, promote Gate S to release evidence or drift sealed demand
  solely to fix diagnostic presentation.`
- Acceptance criteria: `Met: PFE suite passes; live tests remain active when
  inputs exist and skip only their machine-local checks otherwise; future
  source paths are portable; v5/v6 hashes remain byte-identical; zero bound
  demand sources changed.`
- Useful checks: `python3 -m pytest -q tests/test_pfe.py
  tests/test_dirsplit_magnitude_shape.py
  tests/test_direction_decision_sensitivity.py tests/test_validation_report.py
  tests/test_package_layout.py; JSON content-key/hash verification;
  demand_source_paths diff; git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only; per `AGENTS.md`, nothing outside
the marked blocks above is current.
