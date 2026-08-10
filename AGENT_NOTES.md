# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Annual warming is active; professional desktop UI change
  implemented and tested, with visual browser review pending.`
- Summary: `Plan 38d91d22… runs under resume_warming.sh with three state-workers
  and demand prefetch. Latest bound status showed 20,145 succeeded, 84,540
  pending and zero failed; a later process check confirmed three live SUMO
  children plus demand generation. Independently, the web shell was simplified
  to a neutral graphite/blue desktop design with solid panels, restrained
  corners/shadows and text-first action labels.`
- Files changed: `web/index.html, web/controls.js, web/app.js,
  tests/test_serve.py, TASKS.md, AGENT_NOTES.md.`
- Checks: `114 tests passed in tests/test_serve.py; node --check passed for
  app.js and controls.js; git diff --check passed. Warming recorded zero failed
  units at the latest status query.`
- Decisions and evidence: `Only UI source and static contract tests changed;
  semantic traffic/status colours remain. The desktop contract is min-width
  1180px with a four-column home grid. Cache query versions were bumped for both
  changed JavaScript files.`
- Blockers or risks: `The in-app browser backend exposed no browser, so no live
  screenshot/interaction review was possible. Normal desktop visual QA is the
  remaining UI check.`
- Suggested next action: `Open the UI in a desktop browser, check the home,
  simulation and results panels, then commit/push if accepted. Continue warming
  monitoring without changing plan-bound inputs.`
- Actor notes: `The UI work did not interrupt warming; process evidence showed
  supervisor, executor, demand builder and active SUMO children.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
