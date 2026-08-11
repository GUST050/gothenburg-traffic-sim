# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Branch claude/closure-plan-complete. The daily-unit budget
  is wired into the real screening and preflight path and pauses correctly in
  the single-leg case. DURABLE RESUME IS NOT DONE and must not be relied on.
  Nothing was activated.`
- Summary: `Review findings 5 and 6 are fixed and finding 1 is only partly
  addressed. Finding 5 was a real bug: the parent that CROSSED the budget was
  recorded as the resume cursor even though its remaining units were never
  evaluated, so a resume would have skipped it and silently lost every unit
  after the crossing point. That parent is now excluded from the eligible list,
  removed from the parent count and recorded as `abandoned_parent_id`; the
  cursor is the last parent decomposed COMPLETELY. Finding 6: the enforcement
  reality is now stated in the module — the product path enforces
  maximum_daily_units and the unchanged maximum_parent_schedules;
  maximum_ledger_bytes and maximum_peak_rss_bytes are declared and checked by
  exceeded() but the streaming enumeration does not sample them, so they are
  contract and not yet gate.`
- Blockers or risks: `RESUME DOES NOT REACH PARITY, measured: replaying the
  brute-force-multi-day case in 300-unit legs produced 193 eligible parents and
  350 units against 754 and 910 for one uninterrupted run, and never
  terminated. Root cause identified and written into the code: carrying the
  evaluated-unit set forward makes the budget behave as a CUMULATIVE total, so
  each resumed leg starts already at the previous leg's spend and immediately
  re-crosses the line. Two different meanings were conflated — a budget that
  bounds MEMORY HELD AT ONCE and one that bounds TOTAL WORK — and they need
  separate fields and separate resume semantics. Until that is settled,
  --daily-unit-budget is safe only for the single-leg case the tests cover.
  Review findings still open: 1 (a budget-stopped screening still returns a
  payload that flows on into backend preparation rather than terminating the
  search as paused), 3, 4 (the end-to-end resume regression test), and 7
  (serve.py/API/UI wiring), so the six-month case is NOT yet runnable from the
  web product.`
- Archives: `The calibrated archive library exists on the development host at
  /Users/gt/Documents/gs-project/runs. It is not reachable from the Linux VM
  these commands execute in — that VM's root filesystem contains no /Users
  directory and no mount that could provide one — so every archive-dependent
  measurement (golden re-freeze, the v3 benchmark, held-out,
  independent-vs-continuous, worker and micro evidence) has to be run on the
  development host.`
- Checks: `141 passed, 1 skipped across unit budget, budget integration,
  provenance and monthly_sumo. git diff --check clean.`
- Suggested next action: `Settle the budget semantics first — decide whether
  the number bounds memory-at-once or total work, split the field if both are
  wanted, then implement termination-on-pause (finding 1) and the end-to-end
  resume test (finding 4) before any API/UI wiring.`
- Actor notes: `Nothing was activated. Policy v3, held-out, UI and global-best
  claims remain closed. No timeout raised, no cap weakened, no gate loosened.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
