# Agent Notes

Only the marked `CURRENT_HANDOFF` block is current coordination context.
Historical detail lives in `docs/history/AGENT_NOTES_history.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Direction-split closeout, evidence audit and repository
  cleanup are integrated, verified and committed on
  codex/fix-dirsplit-gates-v3.`
- Summary: `The supported weekday 06-20 q50 model, local-anchor and 50/50
  fallback semantics, complementary stress pairs, Gate M/S evidence and
  fail-closed validation identity are preserved. The traffic_sim migration is
  also complete: 12 compatibility shims and dead volume_priors.py are removed,
  five signal-study modules live in signals/, benchmark_seed_workers.py lives
  in tools/, and the large archived validation record is losslessly gzipped.`
- Files changed: `Direction-split training/evaluation/prediction and evidence;
  demand/PFE integration; validation identity gate; canonical traffic_sim
  imports; signals/ and tools/ layout; package and integration tests;
  README/architecture/improvement plan/program audit and current coordination
  blocks.`
- Checks: `The integrated cleanup/dirsplit/demand/PFE/Gate-S/validation-report
  surface passes 908 tests with 1 skipped; all four changed server dispatch
  tests pass with local loopback binding. Direct invocation works for the
  moved signal and seed-benchmark CLIs. The gzipped archive expands to its
  members.json SHA-256, demand source identity is unchanged from c379629, and
  git diff --check is clean. The known historical whole-suite freeze/campaign
  drift was not rewritten or represented as a green global suite.`
- Decisions and evidence: `Gate M v5=MODEL selects
  similarity_weighted_lgbm_no_profile. Gate S v6=NO on 48/48 clean runs means
  q stress does not change the frozen closure decision and is not release
  evidence. Root paths recorded by immutable evidence remain interfaces;
  unbound signal scripts may live in signals/. Frozen evidence was compressed,
  not deleted, and the archived bytes remain bound by their existing hash.`
- Blockers or risks: `The retained published baseline is older than current
  demand, so simulation and sensor-output sections remain withheld until a
  deliberate matched baseline run. Temporal holdout is stale/missing. The
  repository-wide suite still contains historical freeze/campaign identity
  failures. Six stations in two clusters cannot validate citywide traffic.`
- Suggested next action: `After integrated checks pass, build and deliberately
  publish a baseline matching demand build 4afe9e3ae2e74a4b872e, then rebuild
  temporal holdout before adding boundary/cordon sensors.`
- Actor notes: `No push, release or publication was performed. Historical
  Gate M/S records remain separate versioned artifacts.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in
`docs/history/AGENT_NOTES_history.md` (14,681 lines). It is preserved context
only; per `AGENTS.md`, nothing outside the marked block above is current.
