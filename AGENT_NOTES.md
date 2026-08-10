# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `PR C was independently reviewed on
  codex/review-closure-streaming-pr-c over Claude commit 1080ac7. Functional,
  semantic, restart and integrity work is green; the explicit process-total
  64-MiB exit gate remains open on comparable Darwin/arm64.`
- Summary: `The streaming calendar and three manifest-last NDJSON ledgers are
  sound and preserve v1 IDs/order/cache compatibility without the reverse
  unit->parents graph. Review found that the product CLI still wrote the full
  candidate ledger before independent-exhaustive screening discovered the
  known cap. It now runs exact preflight before network identity, runner setup
  or search-workspace publication; total parent and unit caps are enforced,
  and supported preflight counts must match the streamed pass.`
- Files changed: `Claude's PR C files plus review edits in
  run_monthly_closure_search.py, tools/benchmark_closure_streaming.py,
  tests/test_independent_daily.py, tests/test_benchmark_closure_streaming.py,
  validation/closure_search_streaming_v1.json, ARCHITECTURE.md, the scaling
  plan, TASKS.md and AGENT_NOTES.md.`
- Checks: `Focused PR C set 161 passed; expanded closure/monthly/calendar/
  preflight set 301 passed with one unrelated test_warm_horizon source-text
  assertion deselected because it also fails at base 01a0b16;
  API 126 passed with loopback permission; screen_closure_survivability
  --verify reproduces byte-for-byte; git diff --check clean.`
- Decisions and evidence: `The regenerated Darwin/arm64 diagnostic comparison
  is source-hash and content-key consistent. All six cases agree semantically.
  720 h: 2,186 parents / 5,676 units, streaming p95 7.425 s, 1.33 MiB over
  imports, 78.02 MiB process total, versus 250.80 MiB materialising. 360 h:
  11,813 / 23,349, streaming p95 30.474 s, 7.00 MiB over imports, versus
  785.77 MiB materialising. PR A's frozen baseline was not rewritten.`
- Blockers or risks: `open_fixed_import_cost_dominates is confirmed on the
  comparable host, not pending remeasurement: fixed imports are 76.69 MiB, so
  the 78.02-MiB process total cannot pass a 64-MiB gate even though enumeration
  adds only 1.33 MiB. Closing it requires a real fixed-import reduction or an
  explicitly reviewed gate-contract change. The review branch is local and has
  not been merged or pushed.`
- Suggested next action: `Decide whether to reduce real process import RSS for
  the existing gate or explicitly revise the gate; after that, integrate PR C
  and proceed to PR D.`
- Actor notes: `No held-out campaign, SUMO comparison, annual warming input,
  cap increase, policy/ranking change or frozen PR A baseline rewrite occurred.
  Legacy exact_balanced_daily_v1 remains stream-compatible and falls back to
  streaming cap checks because exact PR-B preflight intentionally refuses it.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
