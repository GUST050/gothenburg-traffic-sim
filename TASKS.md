# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `FULL-DAY-ANNUAL-WARMING`
- Status: `RUNNING since 2026-08-06 23:46, under caffeinate. First units ever
  banked: 45 succeeded / 0 failed within the first few minutes. THREE separate
  faults had to be fixed to get here (32a883f, e0fbeaf, 8a6e463) — the run had
  never reached a single unit before.`
- Suggested next action: `Watch it. Check with
  tools/populate_annual_warming.py --status --state-workers 3. If it stops,
  resume with the SAME --execute command: completed units are durable and
  skipped, running/failed retry. Relaunch recipe:
  KEY=$(python3 -c "import json;print(json.load(open('validation/annual_warm_plan_2027.json'))['content_key'])")
  nohup python3 tools/populate_annual_warming.py --execute --state-workers 3 --plan-key $KEY > runs/annual-warm-logs/2027-$(date +%Y%m%d-%H%M%S).log 2>&1 &
  caffeinate -i -w <pid>
  Never resume or relabel an older root; the fix moved the plan key, so the
  roots under runs/annual-warm-2027/ from earlier keys are all superseded.`
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

- Status: `READY_TO_RUN — the stage-B day-library provenance blocker is FIXED
  (commit 32a883f) and the preparation re-run on top of it: plan verifies,
  preflight passes, fresh root initialized wholly pending at 104,685.`
- FIX, 2026-08-06 (`32a883f`): provenance is proven PER DAY, at calibration
  time, against that day's own pool — candidate cross-reference included — and
  the proof is stored beside the day's artifacts so a LIBRARY HIT carries it
  forward instead of skipping the check. The window then verifies those
  proofs, re-checks every invariant that survives assembly (route/agent
  pairing, unique ids, endpoints, purposes, departures) and binds the two by
  per-variant vehicle count, so no vehicle can be added, dropped or renumbered
  between the proven days and the published window. Strictly stronger than the
  single-pool check for these windows. The monolithic path is untouched and
  still uses the single-pool check, because `multi_day_blocks` gives each day
  a distinct prefix and one pool per window, so its ids do resolve.
  Verified: the exact window that failed (2027-01-01 +2d forecast) now exits 0
  with "93066 vehicles across 3 variant(s) from 2 proven day(s) — PASS", and
  again on a rerun with 2/2 days served from the library. Nine regression
  tests in `tests/test_demand_provenance.py`, one of which reproduces the
  original error so the reason this exists cannot be lost.
- THE BUG IT FIXED, measured 2026-08-06: `day_pool_blocks` (demand/intake.py:224)
  hardcodes `id_prefix "d0_"` for the day's own block — correct in isolation,
  because stage B calibrates each day ALONE. But the window loop
  (build_sumo_demand.py:846-871) calls `generate_candidates()` once per day,
  and each call OVERWRITES the shared `sumo/candidates.meta.json`. After the
  loop only the LAST day's pool survives. `assemble_window` then merges every
  day's agents, and `validate_calibrated_provenance` (build_sumo_demand.py:1115)
  checks the merged agents against that one surviving pool — so day 0's agents
  reference day-0 ids drawn from a pool that is no longer on disk. The old
  monolithic path did not have this: `multi_day_blocks` gives each day a
  DISTINCT prefix (`d{day_index}_`) and builds ONE pool for the whole window,
  which is why the 2026-08-05 attempt on this same window logged
  "calibrated candidate provenance: 90778 vehicles across 3 variant(s) — PASS".
  Reachable only for `days > 1` in day-library mode, so single-day builds and
  the live 2025-09-16 demand are unaffected — but ALL 367 annual demand builds
  are 3-day windows, so warming cannot pass its first build.
  NOT caused by the 2026-08-06 relaxation-ladder fix. Control test: with
  `pfe.py` reverted to 857e335 (pre-fix), the same window fails at the same
  line with the same error and a different id (`d0_4776` vs `d0_1942`). The
  ladder only changes WHICH id names the latent fault.
  Note also that the golden A/B in WARMING_PLAN §5 ("passed byte-identical",
  41a5195) compared route/agent bytes but evidently never exercised
  window-level provenance validation, or it would have caught this.
  Failing outright was the LUCKY outcome: where an id existed in both pools
  the old check silently compared an agent against the WRONG candidate and
  passed. That is why it could not be fixed by relaxing the check.
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
