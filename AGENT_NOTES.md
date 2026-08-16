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
- Cross-check merged (Claude, 2026-08-16): `A nested-lambda/coverage harness
  (dirsplit/validate.py, tests/test_dirsplit_validate.py, 22 tests,
  make dirsplit-validate) and the evidence record
  validation/dirsplit_train_report_leakage_diagnostic_v1.json. It audits
  data/dirsplit/train_report.json only: train.py fits the shrinkage lambda by
  least squares on the pooled held-out pairs and reports its MAE on those same
  pairs, so the 0.0557-vs-0.0565 margin CLAUDE.md and README.md quote is an
  upper bound. Refit on three cities and scored on the fourth it is 0.0568
  versus 0.0565, bootstrap CI [-0.00301, +0.00406]. THIS IS NOT A GATE M
  RESULT. dirsplit/evaluate.py::_fit_shrinkage already fits lambda inside the
  training fold, and dirsplit_gate_m_outcome_v5.json (MODEL,
  similarity_weighted_lgbm_no_profile, 247,464-row v2 table, three fold kinds)
  remains the sole current authority; the harness reproduces v5's own
  leave_city_out/all-rows tie on the superseded v1 table and the superseded
  profile model. Its genuinely new number is interval coverage: the deployed
  [q10, q90] covers 39.3% of held-out observations against a nominal 80% (per
  city 31.3-50.0%; the shrinkage re-centring moves it 41.2% -> 39.3%). That
  quantifies the already-declared "uncalibrated stress case" status rather than
  changing it. Also recorded: target_static_features() returns the
  away-from-centre carriageway for 1076/133/134/2276, outside the
  toward-centre-only training support, and every per-sensor kernel effectively
  spans 68-91% of the training set.`
- Solver finding (Claude, 2026-08-16): `tools/reproduce_highs_thread_option.py
  plus validation/highs_thread_option_order_dependence_v1.json. The status-4
  integer-repair failure is ORDER-dependent, not caused by SciPy 1.17 rejecting
  the forwarded option: threads=1 succeeds when it is the first HiGHS solve in a
  process and fails permanently after any default-thread solve, which is the
  documented HiGHS global scheduler and is inherited across fork. Bisected on
  CPython 3.11.15: 1.13.1 and 1.14.1 unaffected; 1.15.3, 1.16.3 and 1.17.1 all
  affected. The boundary is 1.15, so scipy>=1.11,<1.17 ADMITS two broken minor
  versions and a clean install resolves to a failing one — tests/test_pfe.py
  fails 38 tests on the fail-closed path when the file runs in order while the
  same tests pass in isolation. RECOMMENDED and deliberately NOT applied:
  narrow to <1.15 in requirements.txt, CI and the pin test. Not applied because
  it constrains every environment and A2 owns the production root cause.
  ARCHITECTURE.md and IMPROVEMENT_PLAN correction 1 are annotated, not
  rewritten.`
- Corrections accepted from review: `Two of four review points were wrong and
  are withdrawn. The solver pin IS tested — tests/test_pfe.py already has a real
  milp call asserting threads=1 plus a check that the range is pinned in both
  requirements.txt and CI; that test's diagnostics were improved rather than
  duplicated. Gate M was NOT re-decided; v5=MODEL stands and the earlier
  BASELINE wording is retracted, mirroring the plan's own note that a prior
  Gate M claim "matte en annan modell pa en annan population". Work package C
  stays conditional. The spawn proposal was too coarse: integer repair runs in
  the collect_counts pool over fork-inherited module globals, so A3 now reads
  serial SciPy -> spawn SciPy with an explicit initializer -> spawn highspy.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in
`docs/history/AGENT_NOTES_history.md` (14,681 lines). It is preserved context
only; per `AGENTS.md`, nothing outside the marked block above is current.
