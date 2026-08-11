# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Benchmark provenance on branch claude/closure-evidence-v3
  over adf765b. Source work is complete and green; the v3 registration and run
  are NOT produced, because this container has no archive library.`
- Summary: `Registration schema v3 fixes two claims the record made that were
  false. outcome_record was hard-coded to the tool's default, so a registration
  written to a v3 path still named the v2 outcome; it now binds the caller's
  --out, and a run refuses to write an outcome its registration disowns —
  checked after binding verification, so drift in the INPUTS is still reported
  first. sources sealed ten hand-picked files while the arms import 48; the seal
  now covers the real construction and execution closure. Two modules
  (heldout_gate.py, proxy_validation.py) were found by the closure test rather
  than the audit: they are imported lazily by load_passing_heldout_gate, which
  decides the claim boundary. Both changes alter what a registration MEANS, so
  it is a new schema; v2 remains readable and a v2 registration still yields a
  v2-schema outcome.`
- Files changed: `NEW tests/test_cost_ordered_benchmark_provenance.py.
  MODIFIED tools/cost_ordered_benchmark.py,
  tests/test_cost_ordered_benchmark_run.py,
  tests/test_cost_ordered_benchmark_discovery.py, ARCHITECTURE.md,
  IMPROVEMENT_PLAN.md, TASKS.md, AGENT_NOTES.md. UNCHANGED: every frozen v1/v2
  registration and outcome.`
- Checks: `Required focused set 135 passed. Provenance regressions 25 passed.
  git diff --check clean. verify_closure_cost_ordering_golden.py --verify fails
  on the absent golden archive; screen_closure_survivability.py --verify differs
  only in the gitignored sumo/net.net.xml digest — both pre-existing here.
  tests/test_closure_cost_ordering_golden.py's source-digest test fails at
  adf765b BEFORE this branch, because that commit changed monthly_sumo.py, which
  the frozen golden record binds.`
- Decisions and evidence: `The v2 timeout root cause, computed from the frozen
  registration with nothing running: the selected case is one work date
  (2027-03-22, 07:00-15:00, 300 required minutes, 13 candidates); its daily unit
  declares envelope 2027-03-22T00:00:00 -> 2027-03-23T00:00:00 (86,400 s) but
  resolves to demand build 5ac74750843384b3, which is start_date 2027-03-21,
  days 3, n_intervals 288 (259,200 s). SUMO ran to the archive's far end, so a
  5-hour closure cost 72 simulated hours per observation — about 3x the
  necessary work, and the 300 s timeout was reached with zero completed pilots.
  adf765b bounds an independent-daily cold run to its declared envelope, which
  is exactly this.`
- Blockers or risks: `/Users/gt/Documents/gs-project does not exist in this
  container (there is no /Users at all), so --preregister --from-archives found
  0 complete archives and correctly REFUSED to freeze an empty registration. No
  v3 registration or outcome file exists. The golden record's stale
  monthly_sumo.py digest needs a deliberate re-freeze on a host with the
  archives — do not edit the frozen record. Policy v3, held-out, UI and
  global-best remain closed.`
- Suggested next action: `On the machine with the archive library, re-run the
  preregister and run commands from this task verbatim. If all eleven gates
  pass, the next gate is a separately preregistered untouched held-out campaign
  — not part of that task. Also re-freeze the cost-ordering golden record as a
  new version so its source seal matches the corrected runtime.`
- Actor notes: `Nothing was activated and nothing frozen was rewritten. No
  timeout was raised, no cap altered, no gate weakened. --allow-drift,
  --overwrite and --no-fault-injection were not used.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
