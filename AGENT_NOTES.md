# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Closure-integrity stages 3-4 are integrated and complete;
  rolling multi-month period optimization remains the active provisional work.`
- Summary: `The current branch fast-forwarded through 03ca5d7 while no warming
  or SUMO process was active. This brings in the explicit no-teleport closure
  policy, non-vacuous Stage 3 evidence, corrected approach-reachable Stage 4
  survivability check, provenance-bound tooling and frozen held-out v10. The
  pre-existing local policy-v2 and rolling-period work was then reapplied. It
  compares eight through 90 eligible workdays across week/month boundaries,
  keeps identical daily start/end times, and carries deterministic q10/q50/q90
  disruption cost through pilot and final selection.`
- Files changed: `Closure integration: run_scenario.py, signal_optimize.py,
  suggest_closure_time.py, run_monthly_proxy_validation.py,
  traffic_sim/simulation/{closure_teleport,closure_survivability,
  heldout_selection,monthly_sumo}.py, closure evidence tools/tests/artifacts and
  held-out v10 artifacts. Local active work: closure decision/search/daily
  modules, period_comparison.py, API/UI, benchmark tooling/artifacts, plan and
  focused tests.`
- Checks: `Post-integration combined focused suite: 531 passed, 5 skipped and
  the known historical v6 drift assertion deselected. Full test_serve.py with
  loopback permission: 115 passed. Survivability v2 reproduces byte-for-byte;
  git diff --check passes. v10 verification differs only in its manifest because
  the active rolling work changes the fingerprinted closure_calendar.py and
  monthly_search.py; the frozen artifact was deliberately not rewritten.`
- Decisions and evidence: `Closure integrity remains a hard input/provenance
  contract: no finite teleport threshold, no topology-fatal v10 candidate and
  no reinterpretation of zero teleports as an independent signal. Rolling
  periods are consecutive eligible dates, not ISO weeks, and policy v2 remains
  provisional with a zero vehicle-hour equivalence band.`
- Blockers or risks: `Closure runtime integrity is integrated and its focused
  tests pass. Historical held-out v10 reproduces at 03ca5d7 but is intentionally
  stale against the active rolling-source edits; do not rewrite it. Policy v2
  needs a multi-viable discriminating benchmark and a new untouched, newly
  versioned held-out campaign after those sources stabilize. A valid 360 h/
  six-month search exceeds the present 10,000-unit cap; the cap must not be
  raised before scaling/resource gates pass. Existing scenario outputs from
  before the teleport-policy identity change are not comparable to new outputs.`
- Suggested next action: `Run combined focused verification, then implement PR
  A/B in CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md: frozen
  scaling fixtures, exact read-only preflight, differential tests and API/UI
  estimate.`
- Actor notes: `The local work was protected in stash
  codex-pre-closure-integration-2026-08-10, reapplied after the fast-forward and
  left there as a recoverable backup. No annual warming artifact was edited.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
