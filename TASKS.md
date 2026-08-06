# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `FULL-DAY-ANNUAL-WARMING`
- Status: `READY_TO_RUN — plan regenerated against current sources and
  verifying, preflight passing, production root initialized wholly pending
  (104,685). No annual unit has run. The 192-GiB disk blocker recorded here
  was NOT REAL; the live gate is derived (~55.8 GiB) against 168.6 GiB free.`
- Suggested next action: `Commit the working tree first — a bound source left
  uncommitted is a live hazard, because _verify_plan_source_seal() runs at
  every demand-group boundary and any revert discards the bank already built,
  not just the run. Then launch: tools/populate_annual_warming.py --execute
  --state-workers 3 --plan-key <computed>. Never resume or relabel an older
  root.`
- Eligible actors: `Any model or person; no Sol/Luna routing requirement`
- Safety boundary: `Independent reset is explicit and never continuous
  evidence. Exact daily forecast/variants/seeds, six-hour recovery, cache
  integrity and cold fallback remain mandatory. Population does not activate
  product reuse; proxy licensing, deployment and release remain separate.`
- Updated: `pre-warming fault fixes landed; stale disk blocker withdrawn / 2026-08-06`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### FULL-DAY-ANNUAL-WARMING — Populate exact reusable daily prefixes

- Status: `READY_TO_RUN — plan regenerated, preflight passes, root initialized
  wholly pending (2026-08-06). Not launched; launching is an owner decision.`
- Objective and scope: Populate the candidate-free full-day 2027 prefix bank
  for every exact 15-minute independent daily checkpoint and the production
  q10/q50/q90 seed mapping.
- Completion outcome: 104,685 states succeed under the CURRENT plan key with
  no failed/running units; all 367 canonical demand archives and every stored
  artifact validate; product activation remains off. The key is deliberately
  NOT written here — four stale keys once circulated in project documents and
  none of them validated. Compute it:
  `python3 -c "import json;print(json.load(open('validation/annual_warm_plan_2027.json'))['content_key'])"`
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
  every behavioural section, and states remain 1.24–1.59 MiB.
  CORRECTED 2026-08-06 — the "192-GiB minimum / 206,158,430,208 bytes" recorded
  here was never a real gate. No such constant exists in the tree;
  `required_free_bytes()` derives the requirement from selectable work
  (`pending x 432 KiB + 2 x 326 MiB + 4 GiB + 8 GiB` ~= 55.8 GiB), which is
  what the stored preflight records and what `--execute` enforces. 172 GiB
  were free. The root is not initialized because the PLAN KEY is stale, not
  because of disk.
  PREPARED 2026-08-06 (later the same day): the plan was regenerated against
  current sources and verifies; `record_annual_warm_preflight.py --write
  --state-workers 3` passes and now binds the new plan key; `populate_annual_
  warming.py --preflight --state-workers 3` passes (104,685 pending, 168.6 GiB
  free against the derived 55.8 GiB gate, TraCI API complete, SUMO 1.27.1);
  and the production root is initialized WHOLLY PENDING (104,685 total,
  0 succeeded/running/failed). Twelve roots from superseded plan keys remain
  under `runs/annual-warm-2027/` (431 MiB total) — never resume or relabel
  one; the new key gets its own root.
  A FIFTH pre-warming fault was fixed the same day, also in a bound source:
  the Level-3 priors outranked the measured counts, so ~12 of 96 weekend
  intervals published against a band up to 4x the measured tolerance
  (`pfe.py`, `RUNG_NOPRIOR_TOL1`; see `docs/OPEN_ISSUES_2026-08-06.md` 6c).
  That fix is IN the plan's fingerprints, so the bank will be built on it.
  NOT DONE, and not required to launch: `freeze_annual_warm_readiness.py`
  cannot be re-frozen. Its storage pilot is bound to a superseded plan key,
  and its population pilot fails schema validation outright
  (`missing=['checkpoint_requests','runtime_identity'], unknown=['slots']`) —
  it predates a plan-schema change, so it was already stale before today.
  Nothing on the execute path reads that record (§3 of the warming plan);
  re-freezing it means re-running both pilots.
  Four pre-warming faults were fixed the same day, three of them in bound
  sources: the PFE relaxation ladder traded measured counts for plausibility
  bounds (`pfe.py`); half tours were left unlabelled in the candidate pool
  (`build_candidates.py`); the demand prefetch leaked a whole build per
  already-complete group on resume (`tools/populate_annual_warming.py`); and
  the preflight recorder stamped a frozen false date. Details and evidence in
  `docs/OPEN_ISSUES_2026-08-06.md`.
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
