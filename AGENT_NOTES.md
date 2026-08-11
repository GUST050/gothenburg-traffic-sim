# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Stages 1-5 of the closure-search scaling plan, built on
  73f5116 on branch claude/closure-cost-ordered-product-integration. Cost-first
  execution is the product path; the benchmark and the
  independent-vs-continuous question are harnessed. Stage 3 (held-out) did not
  run and produced nothing. Nothing was activated.`
- Summary: `Stage 1: cost_ordered_execution.py prices every candidate from the
  calibrated routes BEFORE any SUMO process exists — the old mode ran the full
  exhaustive pilot and replayed the ordering afterwards, which saved nothing.
  run_monthly_search takes a cost_source and swaps in _cost_ordered_pilot;
  both pilots build evidence through the same _pilot_evidence_for, so they can
  differ in WHICH candidates are simulated and never in HOW. The cost ledger
  is published once under a content key binding daily-unit identity, three
  route digests, the network digest, demand metadata, the disruption schema and
  the costing source bytes; a cursor is written after every verification; a
  resume whose ledger key, bound identity or verified prefix does not match is
  refused, not repaired. The durable cursor MIRRORS cost_ordered_search.py
  rather than hooking into it, because that module's SHA-256 is bound by the
  golden record and a callback broke it — the mirror is rebuilt from the same
  inputs under the same rule and every run asserts it never diverged.
  Stage 2: the benchmark selects from candidate count, unique daily-unit count,
  work dates and archive completeness only, and the tests prove it by making
  any cost or disruption lookup raise. Stage 4: the IVC harness binds to the
  frozen pre-registration by content key, sorts all 84 cases into four buckets
  that must account for every case, and measures candidate correspondence,
  which needs no demand. Stage 5: the libsumo preflight separates the three
  faults that all present as ModuleNotFoundError and installs nothing.`
- Files changed: `NEW traffic_sim/simulation/cost_ordered_execution.py,
  tools/cost_ordered_benchmark.py, tools/measure_independent_vs_continuous.py,
  tools/preflight_libsumo.py, three validation records
  (benchmark registration, IVC outcome, libsumo preflight),
  docs/plans/CONTINUOUS_CLOSURE_CEILING_2026-08-11.md and four test modules.
  MODIFIED monthly_search.py, independent_daily.py (daily_units_for seam),
  run_monthly_closure_search.py, ARCHITECTURE.md, IMPROVEMENT_PLAN.md, the
  scaling plan, TASKS.md, AGENT_NOTES.md. UNCHANGED: cost_ordered_search.py and
  every frozen artifact and pre-registration.`
- Checks: `Focused suites 174 passed (deterministic disruption 32, cost-ordered
  search 77, cost-ordered execution 21, import cost 4, progress 10, policy v3
  9, PR H pre-registration 17, benchmark 18, IVC harness 22, libsumo preflight
  10). tests/test_serve.py 126 passed. The golden record's bound source digests
  are unchanged; drift after the cost_ordered_search.py revert was []. IVC
  harness content key stable across repeat runs. git diff --check clean.`
- Decisions and evidence: `Benchmark registration in this checkout:
  status=blocked_no_structurally_eligible_case, archives_available=0 — measured
  (runs/ holds only a failed demand build and a jobs directory, and no
  calibrated*.rou.xml exists on this host), not assumed. Its eight evaluated
  cases carry 81-90 candidates each, so they would discriminate. IVC outcome:
  84 examined, 24 unsupported_by_contract, 25 unpairable, 35
  blocked_missing_demand, 0 measured, 4.4 s, 137 MiB peak RSS. THIRD CONTRACT
  FINDING: 11 of the 35 "pairable" cases search different candidate spaces —
  equal_daily_rounded_v1 serves the work requirement in fewer days (470
  candidates vs 150 at 21 workdays, scheduling up to 5130 minutes for a 5040
  requirement), and independent can straddle a weekend continuous cannot.
  libsumo preflight verdict blocked_missing_python_binding: eclipse-sumo 1.27.1
  ships lib64/libsumocpp.so and the headers with no libsumo/__init__.py and no
  _libsumo*.so, so the plan's recorded fix would not have worked.`
- Blockers or risks: `The benchmark run needs a host with the calibrated
  archive library; this ephemeral container is not the dev machine. Held-out
  validation is gated behind that benchmark and did not run. PR G needs a SUMO
  build/wheel carrying the SWIG Python bindings — installing one requires user
  authority. PR I stays partly closed by the project's own 2026-07-20
  no-further-external-data decision, which is a fixed boundary, not a TODO.
  Policy v3 must NOT be activated and no global-best or UI claim may open.
  Do not raise _CONTINUOUS_MAX_WORKDAYS to make a comparison run; see
  docs/plans/CONTINUOUS_CLOSURE_CEILING_2026-08-11.md.`
- Suggested next action: `On the dev machine: cost_ordered_benchmark.py
  --preregister --overwrite, then --run, then re-run
  measure_independent_vs_continuous.py --overwrite. If and only if the
  benchmark measures a strictly positive saving with identical selected IDs and
  final decision, freeze an untouched held-out campaign.`
- Actor notes: `Nothing was activated and nothing frozen was rewritten. The
  independent-vs-continuous pre-registration is byte-identical after a full
  classification pass (pinned by a test). No demand was built, no held-out
  campaign was run, no libsumo was installed, no external data was requested,
  no annual warming input was touched, and both resource caps are unchanged.
  Every new record carries release_evidence false and activates nothing.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
