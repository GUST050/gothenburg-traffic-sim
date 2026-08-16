# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. Historical task details live in `docs/history/TASKS_history.md`.
See `AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Execute the researched safety/architecture protocol before
  importing reviewed NVDB road structure.`
- Status: `PLAN COMPLETE — the canonical IMPROVEMENT_PLAN now separates the
  emergency SciPy barrier from root-cause evidence, specifies an exact Python
  3.11.15 runtime and solver comparison, splits clean/live/canary CI, defines a
  portable Gate S bundle, and turns NVDB into a staged, reversible import with
  explicit promotion gates. Implementation has not yet passed those gates.`
- Suggested next action: `Execute A1-A2 and B1: create hashed Python-3.11.15
  platform locks, capture the real failing solver model/reproducer, and make
  reference CI install only the Linux lock. Then compare serial SciPy versus
  spawn-isolated highspy before beginning the staged NVDB import.`
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
- Cross-check: `2026-08-16 an independent nested-lambda/coverage harness
  (dirsplit/validate.py, make dirsplit-validate) was landed. It is NOT a Gate M
  outcome and does not supersede dirsplit_gate_m_outcome_v5.json (MODEL, winner
  similarity_weighted_lgbm_no_profile). It measures the SUPERSEDED v1 table
  (1,214 aggregated rows) and the previously deployed profile model on
  leave-city-out only, where it reproduces v5's own leave_city_out/all-rows tie.
  Its one finding not covered elsewhere is QUANTIFIED: the deployed [q10, q90]
  interval covers 39.3% of held-out observations against a nominal 80% (per city
  31.3-50.0%), putting a number on the already-declared "uncalibrated stress
  case" status of the q arms.`
- Updated: `2026-08-16 Codex — online primary-source research converted into
  a two-axis safety/architecture execution contract in IMPROVEMENT_PLAN.md.
  Merged 2026-08-16 with Claude's validation harness, the four reviewed plan
  amendments and the interval-coverage cross-check. Gate M authority is
  UNCHANGED at v5=MODEL; work package C stays conditional.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### HARDENING-PROTOCOL-1 — Reproducible solver and reversible NVDB import

- Status: `READY — plan and gates are defined; implementation is next.`
- Objective and scope: `Make solver/evidence production reproducible and then
  replace reviewed default network attributes from NVDB without silent
  topology, identity, held-out or closure-decision drift.`
- Completion outcome: `Exact Python-3.11.15 platform locks; diagnosed solver
  compatibility; an independently checked deterministic adapter; required
  clean/live/canary CI lanes; clean-checkout-verifiable future Gate S bundles;
  and a staged NVDB speed/lane patch with fresh demand, before/after evidence
  and atomic rollback.`
- Context or checkpoints: `The current SciPy <1.17 range remains an emergency
  barrier, not a permanent root-cause conclusion. Python 3.9 is EOL. New
  sensors and further dirsplit modelling are deferred. Historical Gate S v5/v6
  remain nonportable non-release diagnostics and are not rewritten.`
- Primary files: `IMPROVEMENT_PLAN.md Safety and Architecture Hardening
  Protocol; requirements/lock artifacts; CI workflow; solver adapter and
  corpus; future Gate S bundle/verifier; network audit, NVDB snapshot/matcher,
  PlainXML patch and staged network publication.`
- Constraints and safety: `Never weaken solver/post-solve checks, mutate the
  live network in place, auto-edit topology/direction/TLS in the first NVDB
  campaign, reuse demand across network identities, rewrite frozen evidence or
  claim accuracy from road-structure authority alone.`
- Acceptance criteria: `Every item in both columns of the plan's two-axis
  completion scorecard passes. Neither column may compensate for a failure in
  the other.`
- Useful checks: `Exact-lock clean install; real-model solver reproducer;
  exhaustive/adversarial solver corpus; expected-skip assertion; clean-room
  Gate S bundle verification; network ID/topology/snap/TLS diff; fresh demand,
  spatial LOSO, temporal holdout and matched closure comparison.`
- Cross-check landed: `dirsplit/validate.py plus tests/test_dirsplit_validate.py
  (22 tests) and validation/dirsplit_train_report_leakage_diagnostic_v1.json.
  Scope is deliberately narrow: it audits data/dirsplit/train_report.json, whose
  published margin fits the shrinkage lambda on the rows it then scores. That
  leak is already fixed in dirsplit/evaluate.py::_fit_shrinkage, so the harness
  changes no gate; it exists so the figure CLAUDE.md and README.md quote can be
  checked, and it measures the q-interval coverage nobody had quantified.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only; per `AGENTS.md`, nothing outside
the marked blocks above is current.
