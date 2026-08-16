# Agent Notes

Only the marked `CURRENT_HANDOFF` block is current coordination context.
Historical detail lives in `docs/history/AGENT_NOTES_history.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `The canonical plan has been hardened for reproducible
  solver/evidence execution and a reversible NVDB network import; implementation
  is READY, not yet evidence-complete.`
- Summary: `Primary-source research corrected the prior implication that
  SciPy <1.17 is a permanent solution. The plan now requires Python 3.11.15 plus
  exact platform locks, a real-model root-cause reproducer, an independently
  checked SciPy/highspy comparison, separate reference/live/canary CI lanes,
  portable future Gate S bundles and a staged gold-set-calibrated NVDB patch.`
- Files changed: `IMPROVEMENT_PLAN.md; TASKS.md; AGENT_NOTES.md in this planning
  turn. The prior local commit 77fc15e still contains the emergency dependency,
  CI and Gate S provenance repairs.`
- Checks: `Primary sources reviewed for SciPy 1.17 milp option forwarding,
  HiGHS scheduler/thread rules, Python spawn/EOL status, pip/PyPA locks, SLSA
  provenance structure, Trafikverket NVDB lane semantics and SUMO PlainXML.
  Current-marker counts are exact, the active/historical implementation order
  is explicitly superseded, and git diff --check passes.`
- Decisions and evidence: `Keep scipy>=1.11,<1.17 only as an emergency barrier
  until A1-A5 pass. Do not guess whether SciPy, HiGHS, the global scheduler or
  process inheritance caused status 4. Prefer serial public SciPy if it meets a
  preregistered resource budget; use spawn-isolated highspy only if needed and
  equally exact. Import speed before direction-sensitive lanes; do not
  auto-edit topology, direction, connections or TLS in the first NVDB campaign.`
- Blockers or risks: `A Python-3.11.15 evidence environment and exact platform
  locks do not yet exist locally. Historical Gate S runs remain machine-local.
  Six clustered stations still underidentify allocation; NVDB improves
  documented physical inputs but cannot by itself prove traffic accuracy. The
  loso.py console-only median remains deferred to the next registered rerun.`
- Suggested next action: `Execute work packages A1-A2 and B1, then select the
  solver adapter through A3-A5. Begin NVDB work package D only after that
  reference environment is stable.`
- Actor notes: `No solver source, sealed demand source, network, release,
  demand, frozen evidence or external system was changed. Nothing was pushed.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in
`docs/history/AGENT_NOTES_history.md` (14,681 lines). It is preserved context
only; per `AGENTS.md`, nothing outside the marked block above is current.
