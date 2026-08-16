# Agent Notes

Only the marked `CURRENT_HANDOFF` block is current coordination context.
Historical detail lives in `docs/history/AGENT_NOTES_history.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Review findings on 9e9ecfd are closed for solver
  compatibility, clean-checkout CI and future Gate S provenance without
  changing a sealed demand source.`
- Summary: `SciPy >=1.11,<1.17 is now explicit in requirements and CI, keeping
  the tested fork-safe HiGHS threads=1 contract. Live dirsplit checks no longer
  error in clean CI when gitignored artifacts are absent. Gate S v5/v6 remain
  unchanged, with an append-only record of their nonportable historical scope.`
- Files changed: `Dependency/CI manifests; PFE compatibility regression test;
  live-artifact test boundaries; Gate S provenance v2 path handling and tests;
  evidence-status JSON; architecture, improvement plan and current blocks.`
- Checks: `PFE: 128 passed. Direction-sensitivity + magnitude live suite:
  114 passed with local artifacts present. Six-module focused run: 443 passed,
  6 failed; all six failures are the pre-existing frozen speed campaign versus
  the current 06–10 demand identity, not this change. Evidence content key and
  v5/v6 hashes verify; git diff --check passes; changed bound demand sources =
  empty.`
- Decisions and evidence: `SciPy 1.17's public milp option list does not expose
  threads and the reviewed 1.17.1 path returned status 4 before solve. Pinning
  the last compatible range repairs CI without invalidating build
  4afe9e3…; removing threads would reintroduce observed nested-executor risk.
  Gate S v6 is narrowed to four frozen candidates and is not a general
  direction-insensitivity claim.`
- Blockers or risks: `The loso.py console-only median still uses the upper
  middle element. Changing it now would drift the source fingerprint bound by
  current_heldout_registration_v1; fix it with the next registered LOSO rerun.
  Six clustered stations remain underidentified; new sensors are deferred;
  4,990 lane counts and 631 speeds remain defaulted.`
- Suggested next action: `Import reviewed NVDB road structure on high-flow and
  closure-relevant edges with stable IDs and before/after network, routing,
  held-out and scenario evidence.`
- Actor notes: `No release promotion, demand rebuild or frozen-artifact rewrite
  was performed. The old live closure set remains recoverable at
  runs/prepublish-baseline-fa259a2892a974c27e8c-20260815T152748Z.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in
`docs/history/AGENT_NOTES_history.md` (14,681 lines). It is preserved context
only; per `AGENTS.md`, nothing outside the marked block above is current.
