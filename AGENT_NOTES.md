# Agent Notes

Only the marked `CURRENT_HANDOFF` block is current coordination context.
Historical detail lives in `docs/history/AGENT_NOTES_history.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Matched baseline plus current spatial/temporal LOSO are
  complete on codex/fix-dirsplit-gates-v3; reviewed repository-cleanup fixes
  are being integrated before the next accuracy task.`
- Summary: `Baseline build 4afe9e3ae2e74a4b872e passed staging and replaced
  the stale live set. Spatial and temporal reports share exact pool, net,
  reference and through-share identity. validation.json is overall=pass.
  Cleanup review fixes restore the documented negative-study module, repair
  moved CLI execution and preserve stable provenance labels.`
- Files changed: `Fail-closed held-out identity in confidence/report.py;
  exact condensed LOSO projection in controlled_rounding.py and loso.py;
  current baseline/LOSO/scenario/validation artifacts and evidence records;
  reviewed traffic_sim migration fixes in tools, signals, layout tests and
  documentation.`
- Checks: `Matched three-arm simulation inserted 14,994/14,994 vehicles with
  zero unfinished/teleports; raw output fit is 100% GEH<5. Spatial LOSO
  0.466–1.354 median 0.613; temporal 0.445–1.356 median 0.6225; every station
  underidentified and every active integer measurement residual zero. The
  scenario/LOSO/publication, PFE/demand and server/API suites pass 623 tests
  with 1 skipped; JSON/hash verification and git diff --check pass.`
- Decisions and evidence: `The temporal blocker was a 20/60/180-second solver
  time limit, not infeasibility. The exact condensed full-domain L1 model
  solved the captured hard model optimally in 0.494 s and remains validation-
  only; sealed demand code is unchanged. Cleanup review established that
  content-sealed files require both path and SHA checks, and volume_priors.py
  is a retained negative study rather than dead code.`
- Blockers or risks: `Six clustered stations still cannot support strong
  citywide accuracy. New sensors are deferred. 4,990 lane counts and 631
  speeds remain defaults. A pre-existing tools/benchmark_speed.py bug remains:
  sumo_version() raises TypeError when local sumo/ shadows the Python package.`
- Suggested next action: `Fix and test the isolated sumo_version() namespace-
  package case, then import reviewed NVDB road structure on high-flow and
  closure-relevant edges with stable IDs and before/after evidence.`
- Actor notes: `The old live closure set is recoverable at
  runs/prepublish-baseline-fa259a2892a974c27e8c-20260815T152748Z. No release
  promotion has been performed.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in
`docs/history/AGENT_NOTES_history.md` (14,681 lines). It is preserved context
only; per `AGENTS.md`, nothing outside the marked block above is current.
