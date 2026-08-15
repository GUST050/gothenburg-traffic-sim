# Agent Notes

Only the marked `CURRENT_HANDOFF` block is current coordination context.
Historical detail lives in `docs/history/AGENT_NOTES_history.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Matched baseline plus current spatial/temporal LOSO are
  complete on codex/fix-dirsplit-gates-v3; final repository checks remain.`
- Summary: `Baseline build 4afe9e3ae2e74a4b872e passed staging and replaced
  the stale live set. Spatial and temporal reports now share exact pool, net,
  reference and through-share identity. validation.json is overall=pass.`
- Files changed: `Fail-closed held-out identity in confidence/report.py;
  exact condensed LOSO projection in controlled_rounding.py and loso.py;
  tests; current baseline/LOSO/scenario/validation artifacts; two registrations,
  two outcomes; improvement plan, program audit and current blocks.`
- Checks: `Matched three-arm simulation inserted 14,994/14,994 vehicles with
  zero unfinished/teleports; raw output fit is 100% GEH<5. Spatial LOSO
  0.466–1.354 median 0.613; temporal 0.445–1.356 median 0.6225; every station
  underidentified and every active integer measurement residual zero. The
  scenario/LOSO/publication, PFE/demand and server/API suites pass 623 tests
  with 1 skipped; JSON/hash verification and git diff --check pass.`
- Decisions and evidence: `The temporal blocker was a 20/60/180-second solver
  time limit, not infeasibility. The exact condensed full-domain L1 model
  solved the captured hard model optimally in 0.494 s and remains validation-
  only; a non-equivalent floor/ceil diagnostic was rejected. Sealed demand
  code is unchanged. Both reports are characterization, not release evidence.`
- Blockers or risks: `Six clustered stations still cannot support strong
  citywide accuracy. New sensors are deferred. 4,990 lane counts and 631
  speeds remain defaults; synthetic signals and OD/assignment priors remain.`
- Suggested next action: `Import reviewed NVDB road structure on high-flow and
  closure-relevant edges, preserving stable IDs and producing before/after
  network, routing, held-out and scenario comparisons.`
- Actor notes: `The old live closure set was replaced only after staging pass
  and is recoverable at runs/prepublish-baseline-fa259a2892a974c27e8c-
  20260815T152748Z. No release promotion or push has been performed.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in
`docs/history/AGENT_NOTES_history.md` (14,681 lines). It is preserved context
only; per `AGENTS.md`, nothing outside the marked block above is current.
