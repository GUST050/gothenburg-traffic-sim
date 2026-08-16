# Agent Notes

Only the marked `CURRENT_HANDOFF` block is current coordination context.
Historical detail lives in `docs/history/AGENT_NOTES_history.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `AI-friendly repository structure is implemented; final
  full-suite comparison and GitHub publication are in progress.`
- Summary: `Concise layered AGENTS.md guidance now follows directory scope;
  CLAUDE.md imports the shared rules. A compact architecture map, artifact
  policy, AI eval set, development requirements and stable Make/CI checks make
  navigation and verification repeatable. Trial extractions from the three
  largest root modules were rejected after the full suite proved that those
  files participate in frozen provenance; all three were restored byte-for-byte.`
- Files changed: `Root and domain AGENTS/CLAUDE files; docs/ai and
  docs/architecture; README/ARCHITECTURE/docs index; Makefile, pyproject,
  requirements-dev and CI; repository-hygiene guard/test/allowlist. No product
  source, frozen evidence or sensor data changed.`
- Checks: `make check passes: Ruff correctness lint,
  repository hygiene and 59 package/contract tests. Final Make targets pass:
  demand 274, dirsplit 154, simulation 144 with 1 skipped, and web 155 (the web
  target requires localhost permission). An exploratory full suite with trial
  source extractions reached 3 738 passed, 27 skipped and 114 failed before
  interruption; the extractions caused additional provenance failures and were
  therefore fully reverted.`
- Decisions and evidence: `Root command paths remain stable because tests,
  docs and evidence depend on them. Large files are blocked at 5 MiB unless
  exact size and SHA-256 are explicitly allowlisted. The three existing large
  artifacts are pinned instead of migrated because Git LFS is unavailable and
  the shared storage destination requires a user/project decision.`
- Blockers or risks: `The branch baseline already has 12 warm-state failures
  and one intentional fail-closed Gate M source/report digest mismatch. These
  must not be hidden by weakening validation. Large source-bound modules cannot
  be modularized until successor evidence is authorized. The three pinned
  artifacts still live in Git until shared storage is selected.`
- Suggested next action: `Have Claude review the pushed branch using the short
  root context, choose Git LFS versus durable external artifact storage, then
  resume the unchanged TOT Gate-D plan.`
- Actor notes: `The TOT split research remains in
  docs/plans/TOTAL_SENSOR_DAILY_DIRECTION_SPLIT_PLAN_2026-08-16.md; it is
  paused, not superseded. This task changes repository ergonomics and module
  ownership only, not scientific behavior or frozen evidence.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in
`docs/history/AGENT_NOTES_history.md` (14,681 lines). It is preserved context
only; per `AGENTS.md`, nothing outside the marked block above is current.
