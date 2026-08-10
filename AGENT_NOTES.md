# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `PR C (streaming closure schedules and versioned workspace
  ledgers) is implemented, tested and measured on branch
  claude/closure-streaming-ledgers-pr-c. One exit gate is deliberately left
  open for the dev machine.`
- Summary: `The enumeration no longer has to exist all at once.
  iter_closure_schedules yields the IDENTICAL sequence lazily and
  generate_closure_schedules is now tuple(iter_closure_schedules(spec)) — the
  body yields where it appended, because schedule IDs and order are contract.
  closure_ledgers.py writes three versioned NDJSON ledgers in one pass:
  parents, unique daily units, and each parent's ordered unit IDs. The reverse
  unit->parents graph is gone from the new path — it was only the inverse of
  the relationship ledger and cost memory proportional to parents x days.
  Publication is atomic per file with the MANIFEST LAST, so its presence is the
  completion signal: no manifest means "never built" and is safely rebuilt,
  while a manifest that disagrees with its ledgers on size, SHA-256 or row count
  raises and stops. monthly_search reads a v1 candidate-ledger.json, a published
  streaming manifest, or an unpublished build area, in that order, and holds
  only a byte-offset ParentLedgerIndex.`
- Files changed: `NEW traffic_sim/simulation/closure_ledgers.py,
  tools/benchmark_closure_streaming.py,
  validation/closure_search_streaming_v1.json, tests/test_closure_ledgers.py
  (28), tests/test_benchmark_closure_streaming.py (28). MODIFIED
  traffic_sim/core/closure_calendar.py (streaming API + wrapper),
  traffic_sim/simulation/independent_daily.py (daily_unit_records,
  daily_unit_schedule, StreamingDailyUnit, prepare_from_ledgers),
  traffic_sim/simulation/monthly_search.py (candidate ledger, bounded
  compatibility prepare), run_monthly_closure_search.py (both exhaustive
  builders stream), tests/test_closure_calendar.py (12 new),
  tests/test_benchmark_closure_search_scaling.py (intended baseline drift),
  ARCHITECTURE.md, the scaling plan (section 9), TASKS.md, AGENT_NOTES.md.`
- Checks: `tests/test_closure_ledgers.py 28 passed; test_closure_calendar.py 50;
  test_benchmark_closure_streaming.py 28; test_benchmark_closure_search_scaling
  34; combined closure/monthly/held-out/proxy suite 1,924 passed with 122
  failures that reproduce IDENTICALLY (same test IDs) at the untouched base
  commit 01a0b16 in a clean worktree — this container's gitignored
  sumo/net.net.xml is not the dev machine's, which also makes
  tools/screen_closure_survivability.py --verify differ in exactly the network
  digest and the derived content key, with every measured value equal. Full API
  suite 126 passed. git diff --check clean; no .partial file survives a
  successful publication.`
- Decisions and evidence: `validation/closure_search_streaming_v1.json is a
  separate diagnostic_comparison record; PR A's baseline is NOT rewritten and
  now correctly reports source drift on the four files PR C changed. Every case
  is measured twice — v1 materialising and streaming — in fresh child
  interpreters on the same host in the same run, because an RSS figure from one
  OS is not evidence about another. Streaming peaks 1.98 MiB over the import
  baseline for 720 h against 152.86 MiB materialising, and 5.76 MiB against
  624.73 MiB for 360 h; all six cases agree semantically (2,186/5,676 and
  11,813/23,349 reproduced). Byte equivalence with the pre-PR-C generator is
  pinned by frozen to_dict() digests over five contract shapes, and ledger bytes
  are reproduced under three PYTHONHASHSEED values in real child interpreters.
  A 100,000-parent synthetic search leaves at most two parents reachable through
  weak references afterwards.`
- Blockers or risks: `The plan's under-64-MiB gate is a PROCESS TOTAL and is
  reported open (open_fixed_import_cost_dominates), not passed: on this Linux
  host a fresh interpreter that imports independent_daily — and therefore
  finalist_decision and scipy — costs 99.9 MiB before any work, against an
  inferred ~21 MiB on the frozen Darwin/arm64 baseline host. The enumeration
  itself is 1.98 MiB. Moving the identity helpers to a scipy-free module would
  lower the number without changing the real search process, which imports
  finalist_decision anyway, so it was not done. Re-measure on the dev machine.`
- Suggested next action: `Re-measure the 720 h streaming peak RSS on
  Darwin/arm64 to close that gate, then PR D's process-free disruption
  provider.`
- Actor notes: `No frozen v1/v6/v9/v10 artifact was rewritten, no v11 created,
  no held-out campaign run, no annual warming input touched, and the 10,000-unit
  and 100,000-parent caps are unchanged — the 360 h case is still refused by the
  unit cap, it simply no longer fails for want of memory first. One regression
  was found and fixed inside this PR before measuring: building each daily
  unit's schedule eagerly made decompose_schedules five times more expensive for
  v1 callers, so the schedule is now built lazily, once per unique unit.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
