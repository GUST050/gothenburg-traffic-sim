# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `FULL-DAY-ANNUAL-WARMING` /
  `BLOCKED_ON_192_GIB_DISK_PREFLIGHT`.
- Summary: Every-edge demand/provenance and maximum-depth warm chaining are
  validated. Final plan `9cc823d3…45283b` contains 104,685 units, but no root is
  initialized because the corrected disk gate requires 192 GiB and only about
  168 GiB is free. No annual unit has run.
- Files changed: content-addressed store reuse, complete archive source binding,
  route-window chaining, native-millisecond boundary transport, annual chain
  audit, disk gate, focused tests, final plan and pre-warming documentation.
- Checks: annual/store/progress/population/boundary/route suite — `164 PASS`;
  boundary/route/audit-focused suite — `125 PASS`; held-out mechanism suite —
  `176 PASS`; final plan verify and `git diff --check` — `PASS`; real 96-link
  q10 population — `96 succeeded, 0 failed`; v2 cold audit — `PASS`. Final
  production preflight — expected `FAIL` on disk (206,158,430,208 required;
  180,475,920,384 available).
- Decisions and evidence:
  1. Plan `9cc823d3…45283b`: 365 days, 96 clock slots, 1,699,440 possible and
     1,682,634 exact intervals, 34,895 checkpoints, 367 demand builds, 104,685
     q10/q50/q90 state units. The 16,806 exact-envelope gaps remain cold.
  2. Canonical archive `demand-20260804-100926-c6316856-7efa` contains all ten
     products, 179,232 calibrated vehicles across three days, every one of the
     7,125 routable edges, 100% GEH<5 and zero infeasible intervals. Its full
     current demand-source/runtime/output identity validates.
  3. The first full-chain pilot exposed full-route definition accumulation:
     depth-96 state grew to 45 MiB. Exact departure-window route shards fix it;
     final states remain 1.24–1.59 MiB.
  4. The final q10 chain populated 96/96 links with zero failures. Independent
     cold comparisons at links 2/48/96 have exact vehicle records, time-loss
     totals, active accumulators, completed order, insertion/teleport counters,
     queue and recovery buckets. Cold-only `loaded` lookahead differs by
     4/55/14 definitions and is explicitly non-behavioural and bounded.
  5. One-process 96-snapshot batching is rejected under the current exactness
     contract: unfinished tripinfo finalizes at SUMO exit, and save-state omits
     the private mesoscopic time-loss accumulator required at every checkpoint.
  6. A three-day archive measures 326 MiB and the q10 chain store 40 MiB. The
     old 160-GiB gate could admit a near-complete disk abort; 192 GiB provides
     measured headroom plus the separate 8-GiB runtime reserve.
- Blockers or risks: Free at least 23.92 GiB, preferably 30 GiB, then rerun the
  final preflight. Historical plans, pilots, preflight/readiness records and
  source-bound campaigns remain evidence only and must not be relabelled.
- Suggested next action: after freeing disk, run `python3
  tools/populate_annual_warming.py --preflight --state-workers 3`. Only if it
  passes, initialize plan `9cc823d316eee71d1895e90704537512e48ad7ed37604d9644d9b88a9845283b`
  and confirm 104,685 pending/zero attempts before execution.
- Actor notes: Do not use any older root or stale readiness command. Population
  still does not activate or certify the bank for product reuse.
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
