# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `FULL-DAY-ANNUAL-WARMING`
- Status: `BLOCKED_ON_DISK_PREFLIGHT — demand, provenance, 96-link chaining and
  archive contracts pass; the corrected 192-GiB gate refuses current 168-GiB
  free space; no annual unit has run`
- Suggested next action: `Free at least 23.92 GiB (prefer 30 GiB margin), rerun
  preflight for plan 9cc823d316eee71d1895e90704537512e48ad7ed37604d9644d9b88a9845283b,
  then initialize its new zero-attempt root. Never resume or relabel an older
  root.`
- Eligible actors: `Any model or person; no Sol/Luna routing requirement`
- Safety boundary: `Independent reset is explicit and never continuous
  evidence. Exact daily forecast/variants/seeds, six-hour recovery, cache
  integrity and cold fallback remain mandatory. Population does not activate
  product reuse; proxy licensing, deployment and release remain separate.`
- Updated: `96-link audit complete; measured disk gate blocks launch / 2026-08-04`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### FULL-DAY-ANNUAL-WARMING — Populate exact reusable daily prefixes

- Status: `BLOCKED_ON_192_GIB_DISK_PREFLIGHT`
- Objective and scope: Populate the candidate-free full-day 2027 prefix bank
  for every exact 15-minute independent daily checkpoint and the production
  q10/q50/q90 seed mapping.
- Completion outcome: 104,685 states succeed under plan `9cc823d3…45283b` with
  no failed/running units; all 367 canonical demand archives and every stored
  artifact validate; product activation remains off.
- Context or checkpoints: The final candidate pool and all calibrated variants
  cover exactly 7,125/7,125 routable edges. Support-only vehicles are explicit,
  route-legal, measurement-disjoint and excluded from behavioral fit. Complete
  candidate/route/agent provenance and the latest immutable demand archive
  validate. The exact annual plan covers 1,682,634 supported intervals,
  34,895 checkpoints and 104,685 states. Checkpoints are ordered into exact
  demand-build/seed/variant chains: the first state starts from zero and each
  later state extends its nearest predecessor instead of replaying the whole
  prefix. The pre-run audit added exact SQLite
  row/lifecycle verification, orphan reconciliation, immutable publication,
  cross-binding of artifact bytes to demand-archive hashes, global demand-build
  serialization, runtime/source provenance and disk guards. The final audit
  additionally indexed plan lookup, retained one runner per worker/current
  demand build, reused verified archive records, restored only predecessor
  members actually consumed, batched progress commits and semantically checked
  crash-published orphans, plus route windows and native-millisecond
  accumulator transport. A real q10 chain
  completed all 96 links with zero failures; cold audits at links 2/48/96 match
  every behavioural section, and states remain 1.24–1.59 MiB. Measured storage
  invalidated the old disk estimate: the corrected 192-GiB minimum requires
  206,158,430,208 bytes but only 180,475,920,384 bytes are free. The final-plan
  root is therefore not initialized.
- Primary files: `validation/annual_warm_plan_2027.json`,
  `annual_warm_plan.py`, `annual_warm_progress.py`, `annual_warm_store.py`,
  `annual_warm_population.py`, `warm_route_windows.py`,
  `warm_state_boundary.py`, and `tools/populate_annual_warming.py`.
- Constraints and safety: Use only the exact current plan key; preserve the
  full six-hour recovery and unchanged hard gates; unsupported source-year/DST
  envelopes fall back cold; population is neither certification nor activation.
- Acceptance criteria: progress total remains exactly 104,685; succeeded reaches
  104,685; pending/running/failed reach zero; status and sampled restores pass.
- Useful checks: `python3 tools/plan_annual_warming.py --verify`; `python3
  tools/populate_annual_warming.py --preflight --state-workers 3`; focused
  annual/boundary/route suites and `validation/annual_warm_chain_pilot_v4.json`
  recorded in the current handoff. Do not initialize or execute until preflight
  passes.
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
