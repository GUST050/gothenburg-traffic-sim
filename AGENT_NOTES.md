# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Branch claude/closure-plan-complete over f77783a. Phases 1
  and 4 of the closure scaling plan are implemented, measured and green. The
  phases that need the calibrated archive library are not done here.`
- Summary: `Phase 1 closes two provenance holes. The registration sealed every
  line of Python and none of the simulator, so it now binds the resolved SUMO
  executable, its SHA-256, its reported version, how it was resolved and the
  platform/machine — a different binary or version is exactly as much of a
  semantic change as an edited source file, and neither is visible in a source
  digest. It also binds the external gate artifacts that decide the CLAIM
  BOUNDARY, including their ABSENCE: "there was no adopted gate when this was
  frozen" is itself a claim, and a certificate that appears later would
  silently widen what a replay may claim. Phase 4 replaces the 10,000-unit cap
  with a measured budget rather than a bigger number: declared units, ledger
  bytes and peak RSS; a pause with a resumable, explicitly INCOMPLETE state
  instead of raising or truncating; and a status that is deliberately not a
  variant of "ready" so nothing downstream can read a partial enumeration as
  exhaustive.`
- Files changed: `NEW traffic_sim/simulation/unit_budget.py,
  tests/test_unit_budget.py,
  validation/closure_search_scaling_measurement_v2.json. MODIFIED
  tools/cost_ordered_benchmark.py,
  tests/test_cost_ordered_benchmark_provenance.py, and the four docs.`
- Checks: `Provenance 34 passed 1 skipped; unit budget 16 passed; focused
  benchmark/execution/monthly_sumo set 154 passed; git diff --check clean.`
- Decisions and evidence: `Measured and frozen: the six-month 360 h case is
  11,813 parents / 23,349 unique daily units, streamed in 14.6 s at 26.5 MiB
  peak RSS — the plan's frozen numbers reproduce exactly, and the peak is a
  fraction of the 64 MiB process gate. The enumeration was never memory-bound,
  so the 10,000 constant was arbitrary. The 720 h case reproduces at
  2,186/5,676. Written to closure_search_scaling_measurement_v2.json, content
  key 3e617bd370600d4d; the v1 baseline is untouched.`
- Blockers or risks: `The commands in this task execute on a Linux VM whose
  root filesystem contains no /Users directory at all, so the macOS development
  workspace and its archive library are not reachable from where these tools
  run. Phases 2, 3, 5, 6, 7, 8 and 9 need that library — golden re-freeze, the
  real v3 benchmark, independent-vs-continuous measurement, the worker
  benchmark, micro validation, held-out and product verification. Separately,
  tests/test_closure_cost_ordering_golden.py's source-digest test fails at
  adf765b BEFORE this branch, because that commit changed monthly_sumo.py,
  which the frozen golden record binds; it needs a deliberate re-freeze as a
  new version, not an edit.`
- Suggested next action: `On the machine holding the archive library, re-run
  Phases 2, 3 and 5 in order: re-freeze the cost-ordering golden as a new
  version against the corrected runtime, then preregister and run the v3
  benchmark, then measure independent-vs-continuous. Held-out remains gated
  behind a passing benchmark.`
- Actor notes: `Nothing was activated and nothing frozen was rewritten. No
  timeout raised, no cap weakened, no gate loosened. --allow-drift, --overwrite
  and --no-fault-injection were not used.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
