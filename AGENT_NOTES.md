# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `PRE-WARMING-FAULT-FIXES` / `READY_TO_LAUNCH`.
- Summary: four pre-warming faults found and fixed, and the recorded warming
  blocker turned out not to exist. The plan key and preflight are freshly
  regenerated and both verify. Warming can start.
- READ FIRST: `docs/OPEN_ISSUES_2026-08-06.md`. Every item marked
  MEASURED / DOCUMENTED / OPEN / FIXED, with where its evidence lives.
- The finding that outlives this task: **GEH<5 cannot police this project's
  own calibration contract.** The relaxation ladder was buying feasibility
  with up to 4x the measurement tolerance on 22.6% of intervals, and no
  quality gate could see it: the widest relaxed band peaks at GEH 3.81 for a
  400 veh/quarter target, while the largest count ever measured on any of
  the 7 measured edges in any quarter is **203**. A build can report 100%
  GEH<5 with a fifth of its intervals anywhere inside a 20% band. Use
  `relaxation_summary` in `demand_meta.json`, not GEH, to see it.
- The previous session's finding still stands: a SINGLE LOSO run cannot
  compare two pipeline versions (draw spread 0.608, SD 0.236, under
  identical code). n=4 per arm is the floor, 6 is comfortable.
  Evidence: `validation/loso_draw_variance_v1.json`.
- Landed this session, all four verified end to end:
  1. **PFE ladder ordering** (`pfe.py`). New `RUNG_NOBND_TOL1` drops the
     Level-2 plausibility bounds at the UNWIDENED band and runs FIRST; the
     tol-widening rungs follow. Measured on the same date, before vs after:
     intervals solved on a widened measurement band **14.2% -> 0.0%**,
     `relaxed_bound_violations` 3/1/4 -> 0/0/1, GEH<5 still 100.0% and 0
     infeasible on all three variants.
  2. **Half tours** (`build_candidates.py`). The route filters delete
     individual legs and none knew a tour has two, so 48.8% of non-through
     tours reached the pool with one leg -- directionally, since return legs
     route more circuitously and are filtered ~1.8x more often. Orphans are
     now MARKED (`tour_partner_dropped`, visible in
     `calibrated.agents.json`) and the per-leg split is printed every build.
     Dropping them instead (`--atomic-tours`) costs 13.9% of the pool and
     breaches the 75% supply floor, so it is opt-in, not the default.
  3. **Prefetch leak** (`tools/populate_annual_warming.py`). A group with no
     selectable work is no longer prefetched, and an unused prefetch is
     reconciled and pruned. Was a full ~332 s demand solve plus 326 MiB
     resident per already-finished group on a resumed run.
  4. **Preflight provenance** (`tools/record_annual_warm_preflight.py`).
     `recorded_date` was the frozen literal `2026-08-04`, written AND
     required, so a record made any other day certified itself as stale-proof
     while being untrue. Real date now; `--state-workers` is settable and
     checked against `approved_seed_workers()` (8 on this host).
  Plus the one non-seal test failure (`test_scenario.py`), whose skip guard
  did not account for the valid EMPTY manifest a demand rebuild leaves.
- Checks: full-day 2025-09-16 demand build green -- 100% GEH<5, 0 infeasible,
  7,125/7,125 edge support, 0% of intervals on a widened band.
  Full suite before -> after: `158 failed / 3727 passed / 21 skipped` ->
  `156 failed / 3745 passed / 23 skipped` (20m41s). The +18 passing are the
  new regression tests (4 ladder/through-share, 10 tour, 3 prefetch, 1
  scenario-manifest). The -2 failures are the only two that were NOT seal
  drift, both verified directly afterwards: `test_scenario.py` (empty-manifest
  guard) and `test_annual_warm_readiness.py` (fixed by regenerating the plan).
  Every remaining failure is frozen-contract seal drift.
  43/43 in the population suite. `plan_annual_warming.py --verify` and
  `record_annual_warm_preflight.py --verify` both exit 0 as of this handoff.
  The key is deliberately NOT written here — see the next bullet; recompute
  it before you use it.
- **The blocker that was not real:** `TASKS.md` recorded
  `BLOCKED_ON_192_GIB_DISK_PREFLIGHT`. No 192 GiB constant exists in the
  tree; `required_free_bytes()` derives ~55.8 GiB from selectable work, the
  stored preflight agrees, and 172 GiB were free. Also: four different plan
  keys appear across `TASKS.md`, `WARMING_PLAN`, the stored preflight and
  reality. **Never copy a plan key into a document -- compute it.**
- Next, in order:
  1. Start the population. `python3 tools/populate_annual_warming.py
     --execute --state-workers 3`. Do not edit a bound source while it runs.
  2. Freeze and run v10. v9 failed one check of seven
     (`ranking_case_fraction` 0.4 against 0.5); its cause was C1, now fixed,
     so the same selection rule should pass. `global_best_claim_allowed`
     stays False until it does.
  3. Measure link-flow observability for the five unmeasured carriageways.
     It decides whether the direction ceiling should exist at all, and it
     was never measured.
- Open, not blocking: the half-tour COMPOSITION bias is now measured and
  visible but not corrected -- correcting it means generating replacement
  tours, a generator change rather than a filter change. And the test
  suite's 158 failures are 157 seal drift plus nothing: the versioned
  manifests (`monthly_warm_state_manifest_v1…v16`) are never retired, so the
  count grows with every campaign freeze. When to retire a superseded vN
  seal is a design decision nobody has made.
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
