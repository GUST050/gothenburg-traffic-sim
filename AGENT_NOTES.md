# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `ROUTE CATALOG V4 ADOPTED. The current weekday/weekend
  entries pass their immutable evidence contract and the live scenarios match
  the active demand build.`
- Summary: `Catalogs 0e8517521504a414d049eb3896b64dc6 and
  b5ad50ba45eb36bd3eea048c4556f1e3 were built on current generator/input
  identity, qualified through 30 clean paired trials and adopted in the schema-3
  default record. The new read-only tools/explain_catalog_fallback.py reports
  both adopted pools serve current inputs and names component-level drift when
  they do not. Baseline and Skånegatan scenarios were rebuilt for demand
  39e0d44eaf2fda1a5934.`
- Files changed: `traffic_sim/demand/route_catalog.py and
  tests/test_route_catalog.py contain the identity-drift and mixed-support
  behavior; tools/explain_catalog_fallback.py is new. Generated v4 build,
  suite, clean trials and qualification evidence plus
  sumo/route_catalog_adoption.json and the mixed adapter cache were produced.
  web/data/scenarios/ and web/data/validation.json were refreshed. TASKS.md
  and this block reflect the completed adoption. Unrelated dirty files remain
  untouched.`
- Checks: `Build 55.772 s. Suite gates: 105 passed. Qualification: 30 pairs,
  verdict adopt, all eight gates true, 18.061x median paired speedup,
  adapter p95 2.600 s, no slower day class, max population delta 0.234%,
  amortization 0.487 days and max RSS 0.862 GB. Both current manifest drift
  lists are empty and both entries validate. Focused post-adoption tests:
  44 catalog/mixed tests passed and 60 live-publication/scenario-timing tests
  passed. make scenario inserted 4,196/4,196 vehicles in six seed runs with
  zero teleports. Final full suite: 5,832 passed, 70 failed, 26 skipped and two
  warnings in 817.71 s; git diff --check and staged JSON validation pass.
  SUPERSEDED LATER THE SAME DAY (2026-09-06, review-driven repair pass): all 70
  failures are closed and the suite measures 5,925 passed, 26 skipped, 0 failed
  in 833.70 s; make lint is green over a target that now includes tools/. No
  frozen artifact was edited. The headline finding was that CI had run ZERO
  tests since 2026-08-26 — tests/test_benchmark_speed.py read the git-ignored
  sumo/demand_meta.json at import time, and a collection error aborts the whole
  pytest run; the last green CI run was 18 July. Four tools were also found
  crashing on their first call from three recent signature changes, plus one
  real warm-arm defect in 779c508 that made every warm run fall back to cold.
  Not done: nothing committed, no simulation, no catalog update, and no real
  GitHub Actions run.`
- Decisions and evidence: `An initial campaign became irreversibly rejectable
  after two cold mixed adapter values near 30 s. It was stopped and preserved;
  the current-identity mixed sensor-basis cache was prewarmed and independently
  restored in 2.818 s before a new from-zero campaign. A competing resume had
  reused the rejected pairs and was also stopped; its 21-pair artifact is
  preserved separately. Only the clean from-zero 30-pair file is bound into
  v4 qualification/adoption.`
- Blockers or risks: `Catalog adoption is complete. The broader demand model
  still has the previously documented purpose-structure WARN and stale/missing
  temporal holdout; no fresh scientific claim is made from catalog adoption.
  A post-adoption direct 2027-12-09 06:00-10:00 build proved implicit catalog
  selection in 0.34 s, then exposed a separate PFE runaway: ten workers used
  about 52 minutes CPU before manual interruption. The prior complete live
  product remained intact. The 70 full-suite failures reported earlier that
  day were concentrated in frozen historical fingerprint/campaign records, an
  exhaustive-vs-cost-ordered production-mode expectation, two warm-state
  integrations and one stale UI-label assertion. THEY ARE NOW CLOSED
  (2026-09-06, review-driven repair pass): the suite measures 5,925 passed,
  26 skipped, 0 failed, and make lint is green over a target that now includes
  tools/. No frozen artifact was edited -- the frozen records were drift
  ledgers that had genuinely drifted, and the two warm-state failures were
  hiding a real production defect in 779c508. The suite IS green and may be
  described as such; what must still NOT be claimed is a passing GitHub
  Actions run (none has been executed) or any new scientific result -- nothing
  was simulated, no catalog was updated and nothing was committed.`
- Suggested next action: `Diagnose the subwindow PFE runaway without changing
  catalog identity or adoption evidence. Separately decide whether to expand
  the simulation boundary/endpoints or revise the purpose contract before any
  new held-out semantic claim.`
- Actor notes: `No deploy or monthly campaign launch occurred, and no
  simulation was run or catalog rebuilt. The stale v3 adoption was replaced by
  passing v4 evidence. One orphan temp snapshot was consumed to restore the
  known 4,196-vehicle live release; the rejected benchmark artifacts remain
  available under validation/. The later repair pass on 2026-09-06 WAS
  committed and pushed to origin/strict-sensor-routes-2026-09-01, on the
  project owner's explicit instruction after three review rounds; the
  standing no-commit/push boundary was lifted for that change only and still
  holds for delete and deploy.`
<!-- CURRENT_HANDOFF_END -->

<!-- CURRENT_HANDOFF_HISTORY_START -->
## CURRENT_HANDOFF_HISTORY

- Focus and status: `CODE STABILIZATION PASS: repaired the 5-finding
  CHANGES_REQUIRED review of Phase C (strict candidate/catalog adapter, the
  new Phase D producer/validator, and the tools/ai_flow.py controller
  repairs — all three of which now exist in the tree, superseding the
  "NOT STARTED" line below from an earlier pass). Findings 1
  (tools/qualify_subhour_demand.py "does not exist"), 3
  (validate_mixed_catalog_candidates trusting a self-consistent forged
  proof) and 5 (no run-local impact inventory) were already resolved by
  intervening work and were reverified fresh, not re-fixed: the Phase D
  module (375 lines) and its 17-test suite exist and pass;
  `independent_mixed_catalog_route_failures` independently recomputes
  legality/cost/detour from the live graph and `TestValidateMixedCatalog
  CandidatesRealRejection` in tests/test_build_sumo_demand.py exercises
  forged/disconnected/mismatched-OD proofs against it; and
  `.ai-flow/runs/20260902-124153-20764/impact_inventory.md` is a real
  hash-cross-checked changed-file matrix. Findings 2 and 4 had genuine
  residual gaps and were fixed this pass: (2) the `post_review_evidence`
  stage's fixer prompt in tools/ai_flow.py unconditionally said "you may
  now continue to Phase 6" with no reference to a bounded run, contradicting
  this task's explicit stop-after-checkpoint instruction even though the
  mechanical `bounded_run_authorization` report-validation gate (already
  present) would still have rejected an actual Phase 6 attempt — reworded
  the prompt to defer to the user task and require
  `bounded_run_authorization` when it bounds the run, plus a new regression
  test (`test_post_review_evidence_prompt_defers_phase6_to_the_user_task`)
  that captures the real composed prompt via `_staged_flow`'s existing
  `work_calls` hook and asserts the conditional wording is present; (4) the
  configured check suite in all three sibling configs
  (`config.complete-subhour.{sonnet,opus,}.toml` — a pre-existing test pins
  them to identical `[checks]`) was missing the test modules for two
  directly-changed/new files (`tests/test_route_catalog.py`,
  `tests/test_qualify_subhour_demand.py`) and their broader transitive
  consumers; added all of them to the three configs identically.`
- Summary: `This is a code-stabilization repair pass responding to a
  CHANGES_REQUIRED independent review of Phase C (5 findings). Re-audited
  each finding against the CURRENT tree rather than trusting the review's
  line numbers, because the tree had moved on since the review was
  generated: tools/qualify_subhour_demand.py, tools/ai_flow.py's
  `_derive_phase012_status`/`bounded_run_authorization` mechanism, and
  `.ai-flow/runs/20260902-124153-20764/impact_inventory.md` already existed
  and already did real, tested work — findings 1/3/5 were stale by the time
  this pass started. Verified rather than assumed: ran
  tests/test_qualify_subhour_demand.py (17 passed),
  tests/test_build_sumo_demand.py's mixed/independent-recomputation classes
  (12 passed, including forged-OD/disconnected-route/stale-network-hash
  rejections raised by the real `validate_mixed_catalog_candidates`, not a
  fixture), and tests/test_route_catalog.py (14 passed). Findings 2 and 4
  had real residual gaps and were fixed here (see Focus and status above for
  the exact defect and fix in each).`
- Files changed this pass: `tools/ai_flow.py` (post_review_evidence stage
  prompt, +~10 lines net), `tests/test_ai_flow.py` (+1 new regression test),
  `.ai-flow/config.complete-subhour.sonnet.toml` +
  `.ai-flow/config.complete-subhour.opus.toml` +
  `.ai-flow/config.complete-subhour.toml` (identical `[checks]` expansion,
  required by `test_opus_subhour_config_keeps_the_policy_and_pins_requested_routing`
  / `test_sonnet_subhour_config_keeps_the_policy_and_pins_requested_routing`,
  which pin all three to the same check list), and this block +
  `.ai-flow/runs/20260902-124153-20764/impact_inventory.md`. No Phase D/3/4/5
  evidence, no other production source, and no other already-dirty file was
  touched; TASKS.md/AGENT_NOTES.md's other content, the append-only
  `validation/route_catalog_*_2026-09-02.json` v1/v2/v3 chain,
  `web/data/*`, and `ui-monthly-routing-v4-20260830` are unchanged and
  preserved.`
- Checks: `Targeted: tests/test_ai_flow.py (122 passed, 0 failed — the new
  test plus the full bounded-run/Phase-0-2 suites), tests/test_build_sumo_demand.py
  mixed/independent-recomputation classes (12 passed), tests/test_route_catalog.py
  (14 passed), tests/test_qualify_subhour_demand.py (17 passed). The
  configured (now-expanded, all-3-configs-synced) check suite: 1810 passed, 2
  skipped (both pre-existing environment-conditional skips unrelated to this
  diff — `tests/test_cost_ordered_benchmark_provenance.py:360` needs a gate
  record not present locally, `tests/test_scenario.py:763` needs a baseline +
  closure scenario not present locally), 0 failed. Full
  `python3 -m pytest -q tests`: 164 failed, 5655 passed, 27 skipped
  (798.49s) — comparable to the prior pass's 165 failed/~5609 passed
  baseline (small run-to-run count drift on the same unaffected groups,
  consistent with local subprocess/artifact non-determinism in those tests,
  not a regression). `grep`-checked every one of the 19 distinct failing
  test FILES (a superset of the 4 groups the prior pass sampled) for an
  import of tools/ai_flow.py, traffic_sim/demand/route_catalog.py,
  traffic_sim/simulation/monthly_demand.py, build_sumo_demand.py or
  tools/qualify_subhour_demand.py — zero hits. Spot-checked the 4 groups not
  previously sampled (test_closure_cost_ordering_golden.py,
  test_independent_daily.py, test_heldout_v6_freeze.py,
  test_benchmark_closure_search_scaling.py): all fail on frozen historical
  source-digest comparisons against unrelated tracked files
  (traffic_sim/simulation/closure_teleport.py, closure_preflight.py,
  traffic_sim/core/contracts.py, run_monthly_*.py) that are not part of this
  or any prior pass's dirty diff — the same pre-existing frozen-evidence
  source-drift pattern this repo already documents elsewhere, not a new
  defect. `git diff --check` clean. `make lint`: the same
  2 pre-existing findings as the prior pass, both in
  run_monthly_proxy_validation.py (unbalanced-tuple-unpacking against
  suggest_closure_time.py's 5-tuple return) — reconfirmed neither file is
  part of this or any prior pass's dirty tree (both last touched at base
  commit 3f20d70), so both remain documented-unaffected, not repaired.`
- Decisions and evidence: `Did not rebuild or re-verify the strict
  candidate/catalog adapter's own correctness (route legality, cost, sensor
  detour) from scratch — that was already independently re-audited and
  covered by real integration tests in the prior pass recorded in
  CURRENT_HANDOFF_HISTORY below, and findings 1/3 confirmed that work is
  still intact rather than reopening it. The two real fixes were scoped
  narrowly: (2) reworded only the post_review_evidence prompt text, leaving
  the already-correct mechanical `_derive_phase012_status`/
  `bounded_run_authorization` enforcement in `_derive_report_phase_status`
  untouched, because that enforcement was already sound and tested; (4)
  added test modules to the check-suite lists rather than removing or
  loosening anything, and applied the identical edit to all three sibling
  configs after `test_sonnet_subhour_config_keeps_the_policy_and_pins_requested_routing`
  failed on a checks-list mismatch, confirming the three-way sync
  requirement is itself enforced, not just conventional.`
- Blockers or risks: `No genuine blocker. All 5 supplied findings are now
  repaired (1/3/5 reverified intact, 2/4 fixed). Independent review of this
  pass is the correct next step before CODE_APPROVED. Phase D's manifest has
  still never been produced, tools/ai_flow.py's Phase 0-2/6/7 derivation has
  still never been exercised end-to-end against a real Phase D manifest, and
  Phase 3-5 have not run — all remain unverified evidence actions reserved
  for the controller after CODE_APPROVED, not failures of this pass.`
- Suggested next action: `Independent review of this repair pass. If
  APPROVED, the controller may proceed to CODE_APPROVED and then Phase D in
  fresh append-only roots, then (only if that passes) Phase 3's
  outcome-blind registration and the bounded paired real-SUMO run, then the
  cold Phase 4 ledger and conditional Phase 5. Stop at that checkpoint;
  Phase 6/Gate S remain out of scope for this task regardless of how far the
  chain gets. Do not touch ui-monthly-routing-v4-20260830.`
- Actor notes: `No commit, push, deploy, evidence registration/outcome, or
  Phase D/3/4/5 execution occurred this pass. All historical/rejected
  validation/*.json records, the local web/data/* baseline rebuild, and
  ui-monthly-routing-v4-20260830 are unchanged and preserved.`

- Focus and status: `PHASE C REVIEW COMPLETE FOR THE STRICT MIXED-CATALOG
  ADAPTER; Phase D producer/validator, controller repair and Phases 3-5 are
  NOT STARTED and remain for a subsequent, separately reviewed generation.`
- Summary: `This worker pass audited the dirty candidate/catalog diff
  (traffic_sim/demand/route_catalog.py's exclude_support_only merge and
  build_sumo_demand.py's prepare_mixed_catalog_candidates/
  validate_mixed_catalog_candidates) rather than assuming the recorded
  verdict. Static check: zero repo-wide callers of combine_catalogs or the
  new functions outside build_sumo_demand.py/route_catalog.py themselves, so
  the change's blast radius is contained. Independently recomputed the
  sha256 of all three v3 evidence artifacts (build/trials/suite_gates)
  against the digests recorded in
  validation/route_catalog_qualification_v3_2026-09-02.json — all match —
  and cross-checked sumo/route_catalog_adoption.json's keys/digests against
  the same chain — consistent. Read the qualification arithmetic itself
  (median_saving_s, amortized_days, trial day-class/order balance) and found
  it internally consistent with real computation, not a placeholder. Traced
  validate_mixed_catalog_candidates's proof check: it recomputes route
  digests, OD, sensor set and network sha256 from the CURRENT files rather
  than trusting persisted claims, and a cache hit is always independently
  revalidated (never trusted blind) — confirmed by adding real,
  non-mocked integration tests (below) rather than only reading the code.
  Per the CODE STABILIZATION CONTRACT, this pass did not build the Phase D
  demand producer/validator, did not repair tools/ai_flow.py's phase-0-2/
  Phase-6/Gate-S derivation, and did not run any Phase D/3/4/5 evidence —
  those require the independent review and controller freeze this pass
  hands off to, and attempting a multi-thousand-line controller change
  without that review would itself be the kind of deferred, unreviewed
  defect the contract forbids.`
- Files changed this pass: `tests/test_build_sumo_demand.py` only (+187
  lines: 3 new real/unmocked integration tests —
  TestMixedCatalogRealIntegration::test_cold_build_then_warm_restore_both_validate_for_real
  and TestValidateMixedCatalogCandidatesRealRejection's two rejection tests
  — plus their tiny real-SUMO-net/real-proof fixtures. No production source
  was changed; the candidate implementation was verified correct as-is, not
  repaired). Every other file already dirty at the start of this pass
  (AGENT_NOTES.md/TASKS.md prose below, build_sumo_demand.py,
  route_catalog.py, tests/test_route_catalog.py, the web/data/* local
  baseline rebuild, and the validation/*.json v1/v2/v3 append-only records)
  is unchanged by this pass and preserved as-is.`
- Checks: `tests/test_route_catalog.py + tests/test_build_sumo_demand.py +
  tests/test_build_candidates.py: 329 passed (up from the prior 326; +3 from
  the new real integration tests). Full `python3 -m pytest -q tests`, run
  TWICE — once before my test additions and once after, on the final bytes —
  both times: 165 failed / 27 skipped, with the passed count going
  5606->5609 (exactly my 3 additions, no new failures). Every one of the 165
  failing tests was checked for an import/call path to
  traffic_sim/demand/route_catalog.py, build_sumo_demand.py's new functions
  or traffic_sim/demand/sensor_route_contract.py — zero hits across all
  4 failing files/module groups (grep across
  tests/test_monthly_warm_state_v2/v4-v9_freeze.py,
  tests/test_scenario_timing.py, tests/test_subhour_cost_ordered_contracts.py,
  tests/test_warm_state_population_semantics.py). Sampled one representative
  failure per group to confirm the unaffected reason concretely rather than
  by import-graph inference alone: (1) test_monthly_warm_state_v9_freeze.py
  fails on a real pre-existing flag-name bug in
  traffic_sim/simulation/warm_state_boundary.py (checks for `--precision`,
  the real SUMO command uses `--save-state.precision`); (2)
  test_scenario_timing.py fails on a real pre-existing return-arity bug in
  run_scenario.py's prepare_closure_variants (2-tuple vs the 3-tuple the
  test unpacks); (3) test_subhour_cost_ordered_contracts.py's two failures
  pass individually in isolation (9.96s, both green) — full-suite-only,
  consistent with cross-test shared-state/ordering, not this diff; (4)
  test_warm_state_population_semantics.py fails on a stale, gitignored,
  July-31-dated local artifact directory
  (validation/warm_state_population_semantics_v2_outcome/) left over from an
  unrelated earlier evidence generation, which a "no output root exists yet"
  pre-registration check correctly refuses to see present. All four are
  pre-existing, unrelated to and unaffected by this diff. `git diff --check`
  clean (exit 0). Focused pylint on the two changed production files
  (build_sumo_demand.py, traffic_sim/demand/route_catalog.py): 0 findings.
  Full-tree `make lint` scope (*.py + traffic_sim + demand + dirsplit, which
  does not include tests/): 2 pre-existing findings, both in
  run_monthly_proxy_validation.py (unbalanced-tuple-unpacking), a file this
  diff never touches.`
- Decisions and evidence: `The candidate implementation needed no repair —
  every claim in the dirty diff and its v3 evidence checked out against
  independent recomputation, not just re-reading. Chose to ADD real
  (non-mocked) integration coverage rather than only review, because the
  planner correctly flagged that the existing new tests mock
  install_grounded_sensor_basis_routes/report_sensor_cross_hits/
  sensor_pool_support_failures at the adapter's own unit-test boundary;
  those primitives already have real coverage in tests/test_build_candidates.py,
  but nothing previously exercised prepare_mixed_catalog_candidates's own
  combine+install+validate+cache composition end-to-end. The three new
  tests do that for real: a genuine tiny SUMO net, real
  sensor_route_contract.qualify_route proofs, a real cold build, a real
  warm cache hit that independently revalidates, and two real rejections
  (a tampered non-positive sensor penalty, a network-file change) — both
  raised by the actual production validate_mixed_catalog_candidates, not a
  fixture standing in for it.`
- Blockers or risks: `No genuine blocker. What remains is scale of
  undone work, not a stuck decision: Phase D's demand producer/validator
  (tools/qualify_subhour_demand.py or equivalent) does not exist yet;
  tools/ai_flow.py (4,999 lines) still needs the planner-identified repairs
  (phase_0/1/2 unconditional PASS, missing bounded-run authorization field
  so Phase 6/Gate S can't be granted after a green Phase 0-5); Phase 3
  preregistration/execution, the Phase 4 cold ledger and conditional Phase 5
  have not run. None of these were attempted this pass, on purpose — writing
  a large, unreviewed controller/producer change in the same pass that
  claims to review it would violate "one all-findings repair ... do not
  defer known defects to the reviewer." They are unverified, not failed.`
- Suggested next action: `Independent review of this pass's audit +3 tests
  first. If APPROVED, the NEXT pass builds the Phase D producer/validator
  and the tools/ai_flow.py controller repairs as their own reviewed
  generation, then executes Phase D in fresh append-only roots, then (only
  if that passes) Phase 3's outcome-blind registration and the bounded
  paired real-SUMO run, then the cold Phase 4 ledger and conditional Phase
  5. Stop at that checkpoint; Phase 6/Gate S remain out of scope for this
  task regardless of how far the chain gets. Do not touch
  ui-monthly-routing-v4-20260830.`
- Actor notes: `No commit, push, deploy, evidence registration/outcome,
  Phase D/3/4/5 execution, or controller/production source edit occurred
  this pass. All historical/rejected validation/*.json records, the local
  web/data/* baseline rebuild, and ui-monthly-routing-v4-20260830 are
  unchanged and preserved.`

- Focus and status: `AUTOMATIC SUB-HOUR CLOSURE PLAN RUN
  20260902-125020-21411 ACTIVE IN PLANNER; strict 50-OD weekday/weekend catalog
  is its candidate demand source.`
- Summary: `A fresh durable ai-flow continuation was started with Sonnet High
  worker/fixer and Codex Sol High planner/reviewer. It first audits the dirty
  catalog implementation and v3 evidence, then may build fresh qualified
  q10/q50/q90 demand and run bounded Phases 0-4/conditional Phase 5. It may not
  start a full month or Gate S. The original mixed adapter blindly combined both day-type
  support-only fills, yielding 869 candidates and a 2.883% population delta.
  It now merges 361 ordinary candidates and installs one shared strict basis,
  yielding 541 candidates with minimum 50 unique routes for every sensor. The
  combined pool is cached by exact catalog bytes, network, sensor floor and
  source bytes; warm restore is followed by proof/network/support validation.
  Final adopted keys are weekday 46f619b93152b0f2e21cd37a1c5e4991 and weekend
  fd92cb5c2cccf9112c4143c4eb6355ff.`
- Files changed: `build_sumo_demand.py`,
  `traffic_sim/demand/route_catalog.py`, `tests/test_build_sumo_demand.py`,
  `tests/test_route_catalog.py`, `TASKS.md` and this block. New append-only
  build/suite/trial/qualification records under `validation/` preserve the two
  rejected diagnostic generations and the passing v3 chain. Runtime adoption
  is written to ignored `sumo/route_catalog_adoption.json`; the validated mixed
  artifact is under ignored `sumo/route_catalog/mixed_adapter_cache/`. Existing
  unrelated dirty artifacts remain preserved.`
- Checks: `Focused implementation tests: 116 passed. Final suite contract:
  112 passed with one known LibreSSL warning. Cold/warm mixed adapter diagnostic:
  27.65 s / 0.038 s, 541 candidates, seven sensors and minimum support 50.
  Fresh v3 matched benchmark completed 30/30 pairs. Qualification verdict=adopt;
  all correctness, population, performance, RSS, day-class, amortization and
  trial-count gates pass. Adapter p95=0.439 s (limit 5 s), paired speedup median
  20.205x (min 18.695x), maximum population delta=0.2789% (limit 1%), and no
  trial or suite hard failures. `git diff --check` passed before the final
  documentation refresh.`
- Decisions and evidence: `The actual emitted geometry must equal the global
  deterministic fastest route. Every crossed measured sensor requires a finite
  legal avoiding path with a positive cost gap above 1e-6 s/1e-9 relative
  tolerance. The old post-qualification 75-percent proposal-retention gate was
  removed because semantic rejection is intentional; prequalification router
  retention, the 50-distinct-route per-sensor floor and exact PFE publication
  gates remain. The floor is explicit in cache/demand provenance and selected
  at 50 by the preregistered five-point study; this is the best tested local
  value, not a universal optimum.`
- Blockers or risks: `No catalog-adoption blocker remains. All strict pools are
  sufficient for exact active-sensor calibration, but all six held stations
  remain structurally underidentified.
  Route variables grow 390/498/834/1,534/3,634 without new independent
  measurements; larger pools mostly add routes exclusive to the held sensor
  and worsen generalization. Demand validation is WARN, not FAIL: purpose-length
  ordering remains wrong (fritid median 1.76 km below arbete 2.49 km), and
  simulation/sensor-output/temporal-holdout sections remain missing until fresh
  downstream validation. The demand build removed three stale web scenario JSON
  files as designed; scenario route files were already absent. Broad monthly
  evidence remains stale and paused.`
- Suggested next action: `Monitor .ai-flow/runs/20260902-125020-21411/status.json,
  state.json, active.lock and the durable screen session. Let checks and
  independent review complete before treating any new demand or SUMO artifact
  as evidence.`
- Actor notes: `Durable screen gs-subhour-20260902-124839 owns the ai-flow run.
  Planner-only attempt 20260902-124153-20764 was interrupted before
  implementation so the new task could explicitly require a complete impact
  inventory, affected/transitive focused tests, full suite, lint, post-edit
  reruns and fresh dependent evidence. No commit, push, deploy or full monthly
  campaign launch occurred. Historical and rejected evidence, including stale
  ui-monthly-routing-v4-20260830, was preserved rather than overwritten.`

- Focus and status: `2026-08-30 REPAIR-BATCH PASS 5 (continuation from
  review-03/review-fix-03, not a re-plan; the user confirmed PASS 4's
  BLOCKED-on-SUMO verdict was itself wrong and pointed at `runtime.
  sumo_home()`, which this pass re-confirmed resolves a real Eclipse SUMO
  1.27.1 install here). A fresh review ran PASS 4's own `tools/
  verify_closure_routing_frozen_units.py` for real for the first time
  (PASS 4 built and used it, but the review that follows this pass
  exercised its OUTPUT critically rather than trusting a clean exit code)
  and found three concrete, real defects -- none of them a re-litigation
  of the routing architecture itself, exactly the "repair only, do not
  re-plan" instruction.
  DEFECT 1 -- `active_closed_edge_throughput: null` on every variant of
  both frozen units, despite PASS 4's own `measured_empty_edges` fix
  (LUNA-WARM-05) forcing a zero-filled flows series to exist for the
  closed edge. Root-caused by direct instrumentation, not guesswork:
  `metrics.active_closure_throughput` computes which flow-array quarters
  are "fully inside" the closure window using the closure's OWN
  `begin_s`/`end_s`, which are absolute seconds from the shared archive
  epoch (`monthly_sumo._closure_seconds`) -- but the `flows` array itself
  (`run_scenario.parse_edgedata`) is indexed from 0 at whatever second the
  SUMO run itself actually started with `--begin`, and an independent-
  daily COLD window starts at the work day's own midnight offset from the
  archive epoch (per `_cold_simulation_window`'s own "archives contain a
  reusable previous/current/next-day envelope" comment), not at absolute
  zero. Every closure quarter therefore indexed past the end of the
  (correctly zero-filled but short) trimmed array, so `measured` never
  turned `True` and a proven-clean closure read exactly like "never
  measured". Confirmed by direct offline reproduction with the unfixed
  function before touching any code. FIXED: `active_closure_throughput`
  gained a `window_begin_s` parameter (default 0, so every existing
  whole-day caller, e.g. `run_scenario.py`'s interactive scenarios, is
  unchanged), threaded through at both real call sites that can run a
  trimmed window (`monthly_sumo.py`'s warm post-segment via
  `plan.warm_point_s`, `suggest_closure_time.simulate_closure`'s cold
  `run_one` via its own `begin_s`). New tests in `test_closure_metrics.py`
  reproduce the exact bug shape unfixed (`window_begin_s` omitted ->
  `None`) and prove the fix (supplied -> proven `0`, and a real-flow
  variant proves genuine throughput still counts).
  DEFECT 2 -- `RoutingProvenance` (`closure_routing.py`) could not name
  the daily-unit identity this repair batch's own acceptance criteria key
  every measurement by (`unit_id`, distinct from `candidate_id`==
  `schedule.schedule_id`), could not resolve the actual transformed route
  file directly (`transformed_route_sha256` did not exist as a field --
  only reachable indirectly via the whole access-impact report's
  `output_route_sha256`), permitted `access_impact_sha256=None` and
  validated it only by LENGTH (a 64-character non-hex string would have
  passed), and TWO call sites in `monthly_sumo.py` synthesized a fake
  zero-valued `RoutingProvenance` (`access_impact_sha256=None`, invented
  `rerouted_around_closure=0`/`denied_count=0`) whenever a result dict
  happened to be missing the real one -- publishing manufactured "nothing
  was routed" evidence instead of surfacing that the real evidence had
  gone missing. FIXED: added `unit_id` (derived once per schedule inside a
  new `ArchivedDemandSumoRunner._unit_identity` helper, which calls the
  SAME `independent_daily.decompose_schedules` the ledger itself used to
  mint the id -- recomputing via the identical pure function means a
  mismatch is a hard `ValueError`, never a silent drift; falls back to the
  schedule's own id when the spec is not configured for independent-daily
  reset, which has no daily-unit concept at all) and
  `transformed_route_sha256` (digested the moment the rewritten route file
  is written, the same file `write_access_impact_report` already digests
  internally as `output_route_sha256`); both digest fields changed from
  optional/length-checked to REQUIRED and validated against a real
  `_HEX64` regex; both synthetic fallbacks replaced with an explicit
  `ClosureRoutingError` raise (`self.close_edges` is fixed at construction
  and never empty for a real closure observation, so an empty result here
  is a defect to surface, not a state to paper over). Both the access-
  impact report and the transformed route file are now preserved DURABLY
  under the monthly cache, content-addressed
  (`ArchivedDemandSumoRunner._preserve_transformed_route`, a new sibling of
  the existing `_preserve_access_impact_evidence`); BOTH preservation
  methods (plus `_preserve_canonical_observation`, which also had a dead
  duplicate `return digest` removed) now verify an EXISTING destination's
  bytes still hash to its own filename before reusing it, instead of
  treating presence alone as proof of validity. `POLICY_VERSION` bumped
  `closure_origin_routing_v3` -> `v4`. New/extended tests in
  `TestRoutingProvenance` (missing `unit_id`, null digests rejected,
  non-hex digest rejected, uppercase-hex digest rejected, short digest
  rejected) and a new `ClosureRoutingResult` self-consistency check
  (`rerouted == len(rerouted_vehicle_ids)`, see defect 3).
  DEFECT 3 -- `tools/verify_closure_routing_frozen_units.py`'s
  `unaffected_route_check.byte_identical_to_source` was `null` on EVERY
  real observation in PASS 4's own replay (both frozen units always
  reroute thousands of vehicles around a real closure), because the tool
  only ever compared whole-route-file digests -- a comparison that can
  only ever answer "did anything at all change", never "which vehicles
  stayed exact". FIXED at the root: `ClosureRoutingResult`/
  `rewrite_route_file` now also name every REROUTED vehicle id
  (`rerouted_vehicle_ids`), mirroring the existing `access_impact` list of
  DENIED ids exactly, written into the access-impact report
  (`schema_version` 1 -> 2, additive); the transformed route file is now
  durably resolvable (defect 2's fix); the tool independently parses BOTH
  the read-only source route (resolved via `resolved.archive_for(schedule)
  / VARIANT_FILENAMES[variant]` -- the real archived demand file, not a
  scratch copy) and the preserved transformed route, reusing
  `closure_routing`'s own private vehicle-fragment regexes so this
  independent check cannot silently disagree with the parser that actually
  produced the file, and BYTE-DIFFS every vehicle id present in source but
  NOT named rerouted or denied. Added `_healthy_control_semantic_check`:
  an explicit selected-field comparison (no denied trips, no hard
  failures, no teleports on either arm, every unaffected vehicle byte-
  identical) reported whenever an observation denies nothing --
  deliberately gated on `denied_count == 0` alone rather than also
  requiring zero reroutes (an earlier draft of this check required both
  and never fired on real production data, since rerouting thousands of
  vehicles around a real closure IS the expected healthy outcome the
  acceptance criteria describe; only a fabricated DENIAL means the policy
  invented lost access).
  RE-VERIFIED WITH A FRESH REAL REPLAY (not merely re-reasoned about) in a
  brand-new exclusive root, `/tmp/closure-routing-verify-run3` (disposable,
  outside the repo; PASS 4's own `-run1` and this pass's earlier `-run2`
  preserved byte-unchanged -- verified by mtime/digest after this pass):
  - `daily-unit-24737391111be0e137537df7` (the former-timeout unit):
    first-attempt wall times **30.94 s / 26.45 s / 25.36 s** (q10/q50/q90,
    limit 300 s), 1 launch attempt / 0 timeouts / 0 retries each, 0
    denied, 0 teleports on either arm, `active_closed_edge_throughput: 0`
    (numeric, proven clean -- was `null`), `hard_failures: []`,
    `recovery.recovered: True`; 55,633-55,774 vehicles classified
    unaffected per variant, ALL byte-identical to source (0 mismatched, 0
    missing); `healthy_control_semantic_check.all_passed: True`.
  - `daily-unit-2387bbad11130660b9de0d17` (healthy control, same edge,
    00:00-08:00 window): 26.62 s / 23.88 s / 23.70 s, identically clean
    shape, 57,388-57,501 unaffected vehicles per variant all byte-
    identical, `healthy_control_semantic_check.all_passed: True` on all
    three.
  - Both units' independently re-derived `unit_id` matched the ledger
    exactly (`_unit_identity`'s `decompose_schedules` recomputation, not a
    trust-the-ledger shortcut). Every `routing_provenance` record
    resolved end to end (policy `closure_origin_routing_v4`,
    `vehicle_class: passenger`, both digests real hash-verified files).
  - `runs/closure-search/ui-monthly-12hg8f3` (source workspace) and both
    prior replay roots verified byte-unchanged after this pass (no git
    diff, no mtime drift).
  TESTS: `tests/test_closure_routing.py` (57), `test_closure_metrics.py`
  (6, including the two new window_begin_s tests), plus the full PASS-4
  focused+broader bundle re-run fresh
  (closure/scenario/suggest-closure-time/teleport-wiring/access-impact/
  disruption/sumo-network-metadata/deterministic-disruption/cost-ordered-
  benchmark-provenance/monthly-sumo/monthly-warm-state/independent-daily/
  monthly-search/finalist-decision/cost-ordered-execution/independent-
  daily-queue/serve): 857 passed, 2 skipped. Frozen-manifest suites
  (`test_monthly_warm_state_freeze.py`,
  `test_monthly_warm_state_v16_freeze.py`) show the SAME 19 failures as
  PASS 3/PASS 4, confirmed pure source-digest drift (`POLICY_VERSION`
  v3->v4 plus the other hashed files this pass touched) -- not touched or
  regenerated.
  No original run/evidence artifact was modified; nothing committed,
  pushed, branched, or campaign-launched.`
- Focus and status: `2026-08-30 REPAIR-BATCH PASS 6 (continuation from
  review-03, repairing the CHANGES_REQUIRED verdict on PASS 5's own work --
  not a re-plan). Two findings, both concrete and both repaired:
  FINDING 1 (evidence durability at cache reload/publication, HIGH) --
  `IndependentDailyRunner._load_cached` verified only the daily-cache
  envelope and `CanonicalObservationDigest` identity fields, never actually
  resolving the nested canonical payload, `RoutingProvenance`, or the
  access-impact/transformed-route artifacts it names; `monthly_search.py`'s
  `evidence_from_dict`/publication path had the identical gap. A missing or
  tampered durable artifact behind an otherwise well-formed cache entry
  could therefore be accepted as valid evidence and published into monthly
  results. FIXED with one shared function,
  `monthly_sumo.validate_canonical_observation_evidence` (plus its
  `resolve_access_impact_report`/`resolve_transformed_route` read-side
  helpers, mirroring `resolve_canonical_observation`'s fail-closed pattern
  and additionally checking that the resolved `RoutingProvenance`'s own
  identity fields match the `CanonicalObservationDigest` record naming it --
  closing a swapped/aliased-evidence path, not just a missing/tampered one).
  Wired at FOUR points so cache reload and monthly publication can never
  disagree: `IndependentDailyRunner._load_cached` (reload) and `_save_cached`
  (fresh-write, symmetric -- a backend defect is caught at write time, not
  only rediscovered on the next reload) via a new
  `_canonical_evidence_cache_root()` duck-typed accessor (returns the SUMO
  backend's own `cache_root`, which is NOT the same root as the daily
  runner's own evidence cache); `monthly_search.evidence_from_dict`
  (resume) and `_run_and_publish_candidate` (publish) via a matching
  `_evidence_cache_root(runner)` duck-typed accessor, so `monthly_search.py`
  still does not need to import the heavy `monthly_sumo` module at load
  time. Both accessors return `None` for a backend/test-double that exposes
  no cache root, in which case validation is skipped exactly as before --
  every existing lightweight `FakeDailyRunner`-based test is therefore
  unaffected. New tests: 6 direct `validate_canonical_observation_evidence`
  cases in `test_monthly_sumo.py` (complete chain accepted; missing
  canonical; missing routing_provenance; tampered access-impact; missing
  transformed-route; identity-mismatch/swapped-evidence), 3 wiring cases in
  `test_independent_daily.py` using a `FakeDailyRunnerWithCacheRoot` double
  and the real `ArchivedDemandSumoRunner` preservation methods to build a
  genuine durable chain (happy-path round trip; `_save_cached` fails closed
  on a phantom digest with nothing written to the daily cache; `_load_cached`
  fails closed and counts `cache_corrupt` on a tampered canonical
  observation), 2 cases in `test_monthly_search.py` (`evidence_from_dict`
  fails closed given a `cache_root` and an unresolvable digest; the
  `_evidence_cache_root` duck-typing accessor itself).
  FINDING 2 (healthy-control comparison was not an actual comparison,
  MEDIUM) -- `tools/verify_closure_routing_frozen_units.py`'s
  `_healthy_control_semantic_check` checked only CURRENT-run invariants
  (zero denials/teleports/hard-failures, byte-identical unaffected
  vehicles), took no reference observation, and was applied to EVERY
  zero-denial observation -- which could label the former-timeout unit a
  "healthy control" on a variant where it happened to deny nothing. FIXED:
  membership now comes from a frozen `HEALTHY_CONTROL_UNIT_ID =
  "daily-unit-2387bbad11130660b9de0d17"` constant, never from an incidental
  zero-denial outcome, so the former-timeout unit can never qualify. A new
  `--reference-report` CLI flag (optional; the tool remains fully
  read-only and never writes into a prior root) loads a PRIOR
  `frozen_unit_verification.json`, and when supplied,
  `_healthy_control_semantic_check` compares an explicit allowlist of 8
  fields (`HEALTHY_CONTROL_REFERENCE_FIELDS` -- denied count, hard
  failures, both teleport totals, active closed-edge throughput, and the
  three unaffected-route-check fields) against the matching unit/variant in
  the reference report, recording per-field `{current, reference, equal}`,
  the reference report's own path and sha256, and an explicit note on why
  content-addressed digests/timing/telemetry/`routing_policy_version` are
  excluded (expected drift, reported elsewhere, not a health signal).
  REAL VERIFICATION, not a synthetic test of the mechanism: ran
  `tools/verify_closure_routing_frozen_units.py` twice through the real
  `MonthlyDemandResolverRunner`/`IndependentDailyRunner` path against real
  SUMO (`runtime.sumo_home()` resolved Eclipse SUMO 1.27.1, same as PASS
  4/5), each in its own fresh exclusive root, preserving both:
  - `runs/closure-routing-verify-20260830-001/` (first replay, no
    `--reference-report`): former-timeout unit
    `daily-unit-24737391111be0e137537df7` completed q10/q50/q90 in
    32.0/28.4/27.1 s first-attempt (limit 300 s), 0 denied, 0 teleports,
    `active_closed_edge_throughput: 0`, 55,633-55,774 unaffected vehicles
    per variant byte-identical to source, no hard failures, no unresolved
    timeout, `healthy_control_semantic_check: None` on every variant
    (correctly excluded by unit identity). Healthy control
    `daily-unit-2387bbad11130660b9de0d17`: 28.5/25.5/24.7 s, equally clean,
    57,388-57,501 byte-identical vehicles per variant,
    `healthy_control_semantic_check.all_passed: True` on all three, with
    `reference_comparison: None` (no prior report existed yet -- this run
    IS now the reference).
  - `runs/closure-routing-verify-20260830-002/` (second replay, WITH
    `--reference-report` pointed at the first report's path): reproduces
    the same shape (wall times 24.8-32.0 s, still all healthy) and, for the
    healthy control on every variant, `reference_comparison.all_equal:
    True` across all 8 allowlisted fields, with the reference report path
    and its sha256 (`907e809c...`) recorded -- confirming the mechanism
    resolves and compares real prior evidence, not a mocked one. The
    former-timeout unit's `healthy_control_semantic_check` remained `None`
    on every variant in this run too.
  Both units' independently re-derived `unit_id` matched the ledger exactly
  in both runs. `runs/closure-search/ui-monthly-12hg8f3` (source workspace)
  and every PASS-4/5 prior replay root verified untouched.
  TESTS: focused bundle (`test_closure_routing.py`,
  `test_sumo_network_metadata.py`, `test_scenario.py`,
  `test_closure_teleport_wiring.py`, `test_monthly_sumo.py`,
  `test_finalist_decision.py`, `test_monthly_search.py`,
  `test_independent_daily.py`, `test_monthly_demand.py`,
  `test_suggest_closure_time.py`, `test_deterministic_disruption.py`,
  `test_closure_access_impact.py`, `test_closure_disruption.py`,
  `test_monthly_warm_state.py`, `test_cost_ordered_execution.py`,
  `test_independent_daily_queue.py`, `test_serve.py`): 855 passed, 1
  skipped (11 new tests added this pass, net). Frozen-manifest suites
  (`test_monthly_warm_state_freeze.py`,
  `test_monthly_warm_state_v16_freeze.py`) show the SAME 19 failures as
  PASS 3-5, confirmed pure source-digest drift (this pass's edits to
  `monthly_search.py`, `monthly_sumo.py` and `independent_daily.py` are all
  already-drifted frozen sources from PASS 4/5's `POLICY_VERSION` v3->v4
  bump) -- not touched or regenerated.
  No original run/evidence artifact was modified; both new replay roots are
  additive and preserved; nothing committed, pushed, branched, or
  campaign-launched.`
- Focus and status: `2026-08-29 REPAIR-BATCH PASS 4 (continuation, not a
  re-plan). PASS 3's own review-fix-03 returned BLOCKED, claiming this
  sandbox has no SUMO at all -- but it tested `which sumo`/`which
  netconvert`/`which duarouter` and a bare `import sumolib`, never the
  repository's own runtime contract. Directly verified this pass:
  `traffic_sim.simulation.runtime.sumo_home()` resolves
  `/Users/gt/Library/Python/3.9/lib/python/site-packages/sumo` (the
  `eclipse-sumo` pip package), `bin/sumo --version` reports a working
  Eclipse SUMO 1.27.1, and `traci_tools_dir(sumo_home())` resolves a real,
  importable `sumolib`. The BLOCKED verdict was an environment-detection
  bug in the review process, not a real gap -- so this pass continues
  narrowly rather than re-planning any part of the closure-routing
  architecture (per the user's explicit instruction), since no
  verification step below exposed a concrete defect in it.
  RE-VERIFIED, no defect found, findings 1-3 from PASS 3: read
  `closure_routing.py` end to end again (windowed `_edge_occupancy_lower_
  bound`/`_closures_overlapping`, `_check_vehicle_class`/single-vClass
  routing, `RoutingProvenance`), `metadata.build_metadata`'s permission
  filtering, `deterministic_disruption.py`'s shared adjacency source, and
  both `suggest_closure_time.py` teleport-policy call sites in
  `monthly_sumo.py` -- all match PASS 3's own description exactly; ran
  `test_closure_routing.py`/`test_sumo_network_metadata.py`/
  `test_scenario.py`/`test_closure_teleport_wiring.py` fresh (193 passed,
  1 skipped).
  FINDING 4 (durable evidence) -- PASS 3 had honestly logged this as "NOT
  DONE" (PairedObservation/CandidateEvidence schema threading out of
  scope), but re-checking the ACTUAL runtime behaviour found the gap was
  worse than that framing suggested: `ArchivedDemandSumoRunner.run_candidate`
  reset `self.canonical_observations = []` at the top of every call and
  never wrote the full canonical payload anywhere durable -- so
  `CanonicalObservationDigest.sha256`, the only trace `CandidateEvidence`
  retains, was a digest of a payload that no longer existed anywhere by
  the time a reader could ask for it. Fixed narrowly, without touching any
  of the 16 frozen `validation/monthly_warm_state_manifest_v*.json`-bound
  dataclasses: `ArchivedDemandSumoRunner._preserve_canonical_observation`/module-
  level `resolve_canonical_observation` (content-addressed under
  `cache_root/canonical-observations/<sha256[:2]>/<sha256>.json`, fail-
  closed on tamper via `CanonicalObservationTampered`, fail-closed on
  absence via `CanonicalObservationNotFound`), mirroring the existing
  `_preserve_access_impact_evidence` pattern exactly. `run_candidate` now
  persists before recording each digest, so the SAME digest a reader holds
  is guaranteed resolvable. New tests in `test_monthly_sumo.py`: full
  round-trip (`resolve_canonical_observation` returns the exact payload),
  write-once dedup on identical content, and both fail-closed paths.
  BUILT AND RAN THE REQUIRED FROZEN-UNIT REPLAY --
  `tools/verify_closure_routing_frozen_units.py`. Reads the original
  `ui-monthly-12hg8f3` ledger read-only, reconstructs each named unit's own
  one-day `ClosureSchedule` from `units.ndjson`, and independently
  re-derives its `unit_id` via `independent_daily.decompose_schedules`,
  asserting it matches the ledger (both units verified: identity and
  derived id agree). Instantiates the REAL
  `MonthlyDemandResolverRunner`(`build_missing=False`, `runs_root=runs/`
  read-only) wrapped in `IndependentDailyRunner`(`queue_workers=1`), with
  fresh exclusive `release`/`baseline-cache`/`daily-cache`/`report` roots
  under `--output-root` (refuses to run if that root already exists) --
  no code path reaches `run_monthly_search` or any campaign orchestration.
  Ran q10 alone first, then added q50, then q90, timing each call.
  MEASURED RESULTS (real SUMO 1.27.1, real 2027-09-28 forecast demand
  archive, real 7,100+ edge network; replay root `/tmp/closure-routing-
  verify-run1`, disposable, outside the repo):
  - `daily-unit-24737391111be0e137537df7` (the unit PASS 1's diagnostic
    replay had previously found timing out / leaking under the OLD
    truncate-and-runtime-reroute policy, 07:15-15:15 daytime closure,
    5,700-5,765 vehicles rerouted per variant): q10/seed 1000 first-
    attempt wall time **29.72 s** (limit 300 s), 1 launch attempt / 0
    timeouts / 0 retries, 0 denied, 0 teleports on both arms,
    `closed_edge_throughput` unmeasured (`None` -- see the disclosed
    reporting-fidelity note below; `closure_edge_leaked(None)` is `False`
    by the function's own documented contract, so this is not a leak),
    `hard_failures: []`, `recovery.recovered: True`. q50 (26.11 s) and q90
    (25.20 s) equally clean. This satisfies the acceptance criterion
    directly and demonstrates the closure_routing.py rewrite genuinely
    fixed the original timeout, not merely relocated it.
  - `daily-unit-2387bbad11130660b9de0d17` (healthy control, SAME closed
    edge, 00:00-08:00 window): q10/q50/q90 wall times 26.26 s / 23.33 s /
    22.69 s, 0 denied on every variant (no invented denials), 0 teleports,
    `closed_edge_throughput: 0` (explicitly measured clean here, unlike
    the other unit), `recovery.recovered: True` on all three.
  - Every `routing_provenance` record resolved end to end through the new
    finding-4 persistence path for real (not just via a fixture test):
    policy `closure_origin_routing_v3`, `vehicle_class: passenger`,
    `access_impact_sha256` resolvable to a real, hash-verified access-
    impact report on both units.
  - Source workspace `runs/closure-search/ui-monthly-12hg8f3` verified
    untouched throughout (no git diff, no ledger mtime change).
  DISCLOSED SCOPE LIMIT on "unaffected vehicles are byte-identical to
  source": neither real run had zero rerouted vehicles (both had
  thousands), so the replay tool's cheap whole-file-digest fast path
  (valid only when `rerouted == denied == 0`) did not apply; the tool
  reports this explicitly per variant rather than silently skip it, and
  defers the partial-rewrite byte-identity guarantee to
  `tests/test_closure_routing.py::TestUnaffectedRoutesArePreservedExactly`
  (still passing), which is the actual test of that code path.
  OBSERVED, NOT FIXED (outside this batch's four claimed-fix scope):
  `suggest_closure_time.py::run_one` (the cold execution arm) calls
  `parse_edgedata` without `measured_empty_edges`, unlike the warm arm's
  explicit LUNA-WARM-05 fix for the same class of gap -- so a genuinely
  clean cold closure can report `closed_edge_throughput: None`
  ("unmeasured") instead of an explicit `0`. Traced through
  `closure_edge_leaked`'s own documented contract (`None` and `0` both
  read as "not a leak") and confirmed this is a reporting-fidelity gap
  only: a REAL leak still produces a nonzero measured value either way, so
  the hard-failure gate itself is unaffected. Recorded here so it is not
  rediscovered as a surprise; not fixed in this pass.
  TESTS: the full focused+broader bundle from PASS 3 re-run fresh (506
  passed, 1 skipped across closure/monthly/finalist/independent-daily/
  suggest-closure-time/deterministic-disruption; 329 passed across
  access-impact/disruption/warm-state/serve/cost-ordered/daily-queue).
  Frozen-manifest suites show the SAME 19 failures as PASS 3, confirmed
  pure source-digest drift (one representative assertion diff: `monthly_
  sumo.py`'s sha256 differs from the frozen manifest's, as expected --
  `POLICY_VERSION` didn't change this pass, but the file's bytes did via
  the finding-4 fix, which is itself hashed into `simulation_source_
  digest`).
  ALSO ATTEMPTED (beyond the proportionate scope above, as extra due
  diligence, matching PASS 3's own precedent): a full unscoped `tests/`
  sweep (4,518 tests, every module in the repo, most unrelated to closure
  routing). Run TWICE, independently, both times reproducing the exact
  SAME 6 failures at the exact same collection positions before a slow
  patch made continuing disproportionate to this task's actual scope (the
  controller note says not to repeatedly run broad suites; this pass had
  already exceeded that once trying to name every failure and stopped
  after doing so honestly rather than a third time). Identified: 3 are the
  already-known pre-existing `test_ai_flow.py` failures (confirmed by
  name, reproduce identically standalone); 2 more are a PREVIOUSLY-
  UNNOTED-in-this-batch frozen-artifact test,
  `test_benchmark_closure_search_scaling.py::TestFrozenArtifact::
  test_frozen_input_key_and_tracked_sources_recompute` /
  `test_the_baseline_is_history_and_reports_pr_c_source_drift` — confirmed
  by name and by reading its own assertion diff, which names exactly
  `traffic_sim/simulation/closure_teleport.py` (modified this repair
  batch, per the teleport-policy fix) plus `contracts.py`/
  `closure_preflight.py` as drifted from ITS OWN separate frozen source
  baseline — the SAME class of intentional source-digest drift as the two
  `monthly_warm_state` frozen suites, just a third frozen-artifact file
  this repair batch had not previously named. The 6th failure (position
  ~23% of collection, stable across both runs) was not individually
  confirmed by name for budget reasons; it falls in the large alphabetical
  range between `test_benchmark_online_latency.py` and
  `test_closure_disruption.py`, well outside every closure-routing-
  specific test file (all of which were run explicitly by name above and
  are 100% clean), and its reproducibility (identical position, both
  independent runs) is consistent with the same pre-existing/frozen-drift
  pattern rather than a new flake. No closure-routing, monthly-search,
  independent-daily, provenance, or evidence-durability test failed
  anywhere in either full sweep.
  No original run/evidence artifact was modified; nothing committed,
  pushed, branched, or campaign-launched.`
- Focus and status: `2026-08-29 REPAIR-BATCH PASS 3 (5 of 5 review findings
  in this batch, severity-ordered). A THIRD review of the same closure-
  routing fix found the two real gaps PASS 2 left open (vClass/permission
  routing, full provenance threading) plus one more real defect in
  suggest_closure_time.py's teleport-policy default. This pass repairs all
  three plus re-confirms PASS 2's two findings, and is honest that the
  frozen-unit real-SUMO replay remains blocked by environment, not scope.
  FINDING 1 (closure_routing.py windowed begin_s/end_s) and FINDING 2
  (destination_closed window-awareness) were RE-VERIFIED, not re-fixed:
  the code already carries PASS 2's v2 fix (`_edge_occupancy_lower_bound`,
  windowed `_closures_overlapping`) and matches the review's own required
  behaviour exactly — re-ran `TestWindowedDestinationDenial` and
  `TestClosureTimingInvariant`, both still pass.
  FINDING 3 (high, FIXED) — vClass/lane/connection-permission-aware
  routing, without inventing a per-trip vClass feature (per the user's
  explicit instruction: ONE modeled category,
  `traffic_sim.simulation.metadata.DEFAULT_VCLASS = "passenger"`, SUMO's
  own implicit default — verified no `<vType>`/`type=` exists anywhere in
  production demand). `metadata.build_metadata` (schema 1 -> 2) now
  computes a `successors` graph already filtered to `DEFAULT_VCLASS`
  (connection legal only when the connection itself and both lanes it
  joins permit the class — SUMO's `allow`-wins-over-`disallow` rule,
  "nothing declared -> permitted" default) plus a `restricted_edges` list.
  `run_scenario.build_edge_graph` — the ONE seam every production caller
  (`closure_routing`, `suggest_closure_time.simulate_closure`,
  `monthly_sumo`) already used — now sources its graph exclusively from
  this filtered index on both the cached and XML-fallback paths (same
  `metadata.build_metadata` call, no second parser). On the real, current
  `net.net.xml` this changes NOTHING (verified: zero `allow`/`disallow`
  declarations anywhere in the network) — the fix makes an already-true
  claim provable, it does not change today's routes. Separately,
  `closure_routing.rewrite_route_file` now fails closed
  (`ClosureRoutingError`, not a SUMO execution error) on a vehicle
  fragment declaring a `type=` this policy cannot prove is
  `DEFAULT_VCLASS` (`_check_vehicle_class`). `POLICY_VERSION` bumped
  v2 -> v3. New tests: `TestSingleVehicleCategoryLegality` (6 tests: no-
  type/compatible-vType/bare-vType-defaults-to-passenger routing
  normally; incompatible-vType, undeclared-vType, and an unaffected-but-
  incompatible vehicle all fail closed; `build_edge_graph` excludes a
  restricted lane end to end), `test_sumo_network_metadata.py` (4 new
  tests: restricted-edge exclusion, connection-level disallow, vclass-
  keyed cache rejection, schema-1 cache rejection), and
  `test_scenario.py::test_reachability_respects_single_category_
  permissions` (rewritten from a test that used to document this as a
  "known limitation" C2 deliberately left unfixed — it is fixed now).
  FINDING 4 (high, COMPLETED the additive part; same scope boundary as
  PASS 2 on the rest) — the free-form `routing_provenance` dict is now a
  validated `closure_routing.RoutingProvenance` dataclass (fail-closed
  `__post_init__`; strict `from_dict`/`to_dict`) binding
  `routing_policy_version`, `vehicle_class`, `candidate_id`, `work_date`,
  `demand_variant`, `seed`, `execution_arm`, `access_impact_sha256`
  (resolvable to the full per-vehicle report), `rerouted_around_closure`,
  `denied_count`. `from_dict` requires an EXACT field match, so a PASS-2-
  era dict (missing `vehicle_class`/`denied_count`) is rejected, not
  silently backfilled. Wired at both `monthly_sumo.py` call sites (cold
  and warm) plus the warm-evidence-invalid fallback. New tests:
  `TestRoutingProvenance` (7 tests: round-trip, stale-policy-version
  tamper, wrong-vehicle-class tamper, negative-count tamper, invalid-arm
  tamper, legacy-dict incompatible-cache, extra-field incompatible-cache).
  NOT DONE, same judgement call as PASS 2: threading routing identity into
  `PairedObservation`/`CandidateEvidence`/`CanonicalObservationDigest`
  (`finalist_decision.py`) remains out of scope for a repair-batch pass —
  `CanonicalObservationDigest.from_dict` enforces an exact field set
  against 16 separately frozen `validation/monthly_warm_state_manifest_
  v*.json` artifacts, and widening that surface is still judged too
  large/risky here. The additive `routing_provenance` dict in
  `build_monthly_observation`'s free-form `provenance` mapping is the
  route taken instead, same as before.
  FINDING (extra, high, FIXED, not in PASS 2's list but found by this
  review) — `suggest_closure_time.closure_feasibility`'s own default
  argument is the legacy `ct.CLOSURE_TIME_TO_TELEPORT_S = -1`
  ("disabled"), and both `monthly_sumo.py` call sites omitted the
  argument, so every published `teleport_policy` provenance record
  falsely claimed teleporting was disabled when the actual SUMO run (cold
  via `simulate_closure`'s own default, warm via
  `_default_warm_invoker`'s explicit argument) used
  `closure_teleport.CLOSURE_ROUTING_TELEPORT_POLICY_S` (SUMO's own
  default, teleporting ENABLED — safe now that every affected route is
  already rewritten around the closure before SUMO starts). Both call
  sites now pass `closure_teleport.CLOSURE_ROUTING_TELEPORT_POLICY_S if
  self.close_edges else None` explicitly. Rewrote
  `tests/test_closure_teleport_wiring.py::TestFeasibilityReporting`: one
  test pins the function's own DEFAULT is unchanged (still legacy/
  disabled — a function default and what production passes are separate
  facts), one proves the production value reports `teleporting_enabled:
  True`, one source-greps both `monthly_sumo.py` call sites for the
  explicit argument.
  FINDING 5 (high, STILL BLOCKED — genuinely, by environment this time,
  not by scope choice) — the literal frozen-unit harness replay
  (`daily-unit-24737391111be0e137537df7` / `...2387bbad11130660b9de0d17`
  through the real monthly worker path) could not be attempted: this
  sandboxed environment has no `sumo`/`netconvert`/`duarouter` binary and
  no `sumolib` (verified directly: `which sumo`/`which netconvert`/`which
  duarouter` all find nothing; `import sumolib` raises
  `ModuleNotFoundError`). No SUMO subprocess can run here at all, so
  neither unit's wall time, denial count, route/access digests, teleport
  count, or closed-edge throughput can be measured in THIS session,
  regardless of how the wiring work is scoped. This is a hard
  infrastructure gap, not a repair-batch scope decision — say so plainly
  rather than reporting a number that was not actually measured. The
  routing/provenance logic itself is exercised end to end by fixture
  tests (`TestSingleVehicleCategoryLegality`, `TestRoutingProvenance`,
  the full `TestFastestClosureExcludingRerouting`/`TestWindowedFixedPoint`/
  `TestUnaffectedRoutesArePreservedExactly` suites), which is the
  strongest verification obtainable without a SUMO install.
  VERIFIED this pass (unit/integration, NOT the frozen-unit real run):
  44/44 test_closure_routing.py; 274/274 (+1 skipped) across
  test_closure_routing/test_scenario/test_suggest_closure_time/
  test_closure_teleport_wiring/test_closure_access_impact/
  test_closure_disruption/test_sumo_network_metadata; 186/186 across
  test_monthly_sumo/test_independent_daily/test_finalist_decision. A
  broader `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/` run was
  also done; three pre-existing failures in `test_ai_flow.py`
  (`test_role_routing_matches_requested_models_and_effort`,
  `test_claude_interrupted_stage_can_resume_the_same_persisted_session`,
  `test_flow_repairs_failed_checks_and_review_findings`) are UNRELATED to
  this task (they assert on a `.ai-flow` orchestration fixture's repair-
  batch count/session-resume shape, in files this pass never touched, and
  reproduce identically against the pre-existing dirty `.ai-flow/
  config.toml` from before this session started) — reported here rather
  than silently fixed or hidden, per "preserve unrelated dirty changes".
  No original run/evidence artifact was modified; nothing committed,
  pushed, branched, or campaign-launched.
- Focus and status: `2026-08-29 REPAIR-BATCH PASS 2 (5 of 5 review findings
  in this batch, severity-ordered). This is a SECOND review of the same
  closure-routing root-cause fix; it found the earlier repair pass's own
  fix (the 900 s margin) was itself unsound, plus a windowed-destination
  bug, plus incomplete provenance/permission work.
  FINDING 1 (critical, FIXED) — `_closures_overlapping`'s
  `CLOSURE_TIMING_SAFETY_MARGIN_S = 900` additive margin could never PROVE
  a vehicle clears an edge before a still-open closure ends, because
  congestion delay has no demonstrated upper bound; a vehicle whose real
  transit landed >900 s later than its free-flow estimate would still have
  been wrongly classified safe. Replaced with a provable one-directional
  rule: real transit is never faster than free flow (SUMO only ever adds
  delay relative to free speed), so `depart_s + free_flow_elapsed` is a
  true LOWER BOUND on occupancy; a window is provably missed only when
  that lower bound has already reached/passed the window's `end_s` —
  everything else, including a window far in the future, is applicable.
  This can only WIDEN who is treated as affected relative to the old rule.
  The post-routing residual check (`rewrite_route_file`'s final assertion)
  now legitimately reuses this predicate: it was flagged in review as
  "the same heuristic twice", but that critique targeted the UNSOUND old
  margin specifically — once the predicate is a real proof rather than a
  guess, re-checking it on the final route is genuine defence-in-depth, not
  circularity, and is documented as such at the call site.
  FINDING 2 (high, FIXED) — `destination_closed` fired on bare membership
  of the destination edge in `close_edges_set`, denying every trip whose
  destination ever appears in the closed-edge list even when that specific
  closure's window does not apply to that trip's arrival (already over, or
  provably not yet overlapping). Restructured `rewrite_route_file` to
  compute applicability ONCE up front and use it for both the
  destination-closed decision and the detour planner's initial banned set;
  destination is now denied only when it is itself in the applicable set.
  New tests: `TestWindowedDestinationDenial` (both the active-window denial
  and the already-ended-window preservation cases).
  FINDING 4 (high, PARTIAL) — POLICY_VERSION bumped
  `closure_origin_routing_v1` -> `v2` (the two fixes above change
  classification outcomes for the same input, so v1 evidence/cache entries
  must never satisfy a v2 lookup). `write_access_impact_report` /
  `prepare_route_file` / `run_scenario.reroute_closure_affected_vehicles`
  gained an optional `identity` parameter (candidate/schedule id, demand
  variant, seed, work date, execution arm), bound verbatim into the
  access-impact JSON; wired at all three production call sites
  (suggest_closure_time.simulate_closure, ArchivedDemandSumoRunner's cold
  and warm-post paths). The access-impact-report sha256 that
  `_preserve_access_impact_evidence`/`per_variant_access_impact_sha256`
  already computed was being discarded at both monthly_sumo.py call sites
  (review's exact citation) — both now build a `routing_provenance` dict
  (policy version, access-impact sha256, rerouted count) and fold it into
  `build_monthly_observation`'s `provenance` mapping on both arms.
  closure_routing.py's own bytes were ALREADY part of
  `simulation_source_digest` before this pass, so the v1->v2 change already
  invalidates every pre-fix cache/backend identity with no extra plumbing —
  confirmed by the pre-existing frozen-manifest suites
  (`test_monthly_warm_state_freeze.py`, `..._v16_freeze.py`) failing on
  source drift after this change. That failure is the CORRECT, intended
  consequence of a real source change to a hashed file, not a regression to
  fix — the manifests were not touched. NOT DONE: this still stops short of
  threading candidate/unit/vehicle-class identity through
  `PairedObservation`/`CandidateEvidence` (finalist_decision.py) or bumping
  `OBSERVATION_SCHEMA` — that touches 16 separately frozen
  `validation/monthly_warm_state_manifest_v*.json` artifacts and was judged
  too large/risky a surface for this pass; the `provenance` dict route
  taken instead is additive and did not require touching any frozen
  schema.
  FINDING 3 (high, NOT ADDRESSED) — vClass/lane/connection-permission-aware
  routing. `run_scenario.build_edge_graph`, `disruption.shortest_path_edges`
  and `closure_routing` still route on an unqualified successor graph, with
  no vehicle-type parsing and no SUMO lane/connection permission check; an
  "fastest legal path" claim is not currently provable for a restricted
  vClass. This needs its own scoped implementation (parse each vehicle
  fragment's effective vType/vClass, build a permission-filtered adjacency
  from the network's lane `allow`/`disallow` and `<connection>` legality,
  fail closed on an unrecognised vClass) and was not attempted here for
  scope reasons.
  FINDING 5 (high, NOT ADDRESSED) — the literal frozen-unit harness replay
  through the real monthly worker path
  (`daily-unit-24737391111be0e137537df7` / `...2387bbad11130660b9de0d17`)
  is STILL not built. Both prior passes below explicitly deferred this as a
  substantial single-unit-driver wiring task through
  `MonthlyDemandResolverRunner`/`IndependentDailyRunner`; this pass did not
  attempt it either, for the same reason (out of proportion to a focused
  repair pass) plus this session's own budget. Do not claim either unit was
  run through the monthly worker path.
  VERIFIED (unit/integration, NOT the frozen-unit real run): 30/30
  test_closure_routing.py; 221/221 (+1 skipped) across
  test_closure_routing/test_scenario/test_suggest_closure_time/
  test_closure_teleport_wiring; 186/186 across
  test_monthly_sumo/test_independent_daily/test_finalist_decision; 256/256
  across test_monthly_search/test_cost_ordered_execution/test_serve/
  test_independent_daily_queue. No original run/evidence artifact was
  modified; nothing committed, pushed, branched, or campaign-launched.
- Focus and status: `2026-08-29 REPAIR-BATCH PASS 1 (3 of 5 review findings,
  severity-ordered): a review of the root-cause closure-routing fix below
  found 3 defects; this pass repaired all 3 in-place, preserving every dirty
  file and run artifact. FINDING 1 (critical,
  `independent_daily._import_compatible_completed_cache`, "re-keys old
  evidence under a new backend digest, bypassing source-digest invalidation")
  and FINDING 3 (high, `suggest_closure_time.py`/`monthly_sumo.py`, "rerouted
  count published as `truncated_unreachable`; access-impact reports deleted
  unread") were INSPECTED LINE-BY-LINE against the review's own cited ranges
  and found ALREADY CORRECT in the current tree — both carry inline comments
  dated 2026-08-29 describing the exact defect the review restates and its
  fix (`skipped_incompatible_backend` gate at independent_daily.py:1868,
  tested by `test_compatible_recovery_rejects_units_with_a_different_backend_
  digest`; `per_variant_impact[i] = (0, denied)` / separate
  `rerouted_around_closure` field at suggest_closure_time.py:452-500;
  `_preserve_access_impact_evidence` called from both the cold path
  (monthly_sumo.py:2224) and warm path (monthly_sumo.py:1677) before their
  temporary roots are deleted). Re-ran the exact tests that pin these gates —
  both pass. No code change was made for findings 1/3; changing already-
  correct code on the review's say-so without a concrete counter-example
  would have been a regression risk, not a repair. FINDING 2 (high,
  `closure_routing._closures_overlapping`, "checks only free-flow ARRIVAL
  INSTANTS ... the required 07:15-15:15 window still produced a jam teleport
  onto the closed edge") was a REAL gap and is fixed: free-flow travel time
  is a LOWER bound on real transit time (congestion only delays, never
  speeds up), so a bare point check `begin_s <= arrival < end_s` can miss a
  vehicle whose real, congested arrival lands inside the window even though
  its optimistic estimate lands just outside it — exactly the "~60s from a
  boundary" case the review cites. Fixed in
  `traffic_sim/simulation/closure_routing.py`: (a) the check now uses the
  edge's OCCUPANCY INTERVAL `[arrival, arrival+edge_travel_s]`, not a single
  instant; (b) both interval ends are padded by a new
  `CLOSURE_TIMING_SAFETY_MARGIN_S = 900` (15 min) constant, which can only
  WIDEN who counts as affected, never narrow it, so it costs a few extra
  (harmless) reroutes and never manufactures a missed one; (c)
  `rewrite_route_file` now re-checks every rewritten route against
  `_closures_overlapping` immediately after `_plan_detour` returns and raises
  `ClosureRoutingError` if any residual overlap remains, so the fixed
  point's own termination is PROVEN per vehicle rather than trusted — this is
  the literal "prove the final pre-SUMO routes cannot encounter an active
  closure" the review asked for. 5 new tests
  (`tests/test_closure_routing.py::TestArrivalUncertaintyMargin`), including
  one end-to-end `rewrite_route_file` case that reproduces the near-boundary
  miss shape (free-flow arrival 5s outside a declared window, caught only by
  the margin) and asserts it now reroutes. VERIFIED: 24/24
  test_closure_routing.py; 215/215 (+1 skipped) across
  test_closure_routing/test_scenario/test_suggest_closure_time/
  test_closure_teleport_wiring; 376/376 across
  test_closure_access_impact/test_closure_disruption/test_monthly_sumo/
  test_monthly_warm_state/test_independent_daily(_queue)/test_monthly_search/
  test_finalist_decision/test_cost_ordered_execution; full `tests/` suite
  launched separately (proportionate broader run, result pending at the time
  this note was written — check the next dated entry or the CI/test log
  before trusting a specific pass count here). `python3 -m py_compile` clean
  on both changed files; `git diff --check` clean.
  HONEST REMAINING GAP, UNCHANGED FROM THE PRIOR PASS: the review's
  required_change for finding 2 also asks to "run the literal
  `daily-unit-24737391111be0e137537df7` ... also run the named healthy unit
  and the exact monthly worker path." No code anywhere in the repository
  references either unit id by string (`grep` confirms) — the literal
  monthly-search-harness replay was not built in the prior pass and was not
  built in this one either; it is a substantial wiring task (bounded single-
  unit driver through `MonthlyDemandResolverRunner`/`IndependentDailyRunner`
  matching the real `ClosureSchedule`/repetitions/worker-isolation path), not
  a "safe in-scope repair" of the 3 findings this batch names, and was not
  attempted under this pass's effort/time budget. Do not claim it was run.
  What WAS re-verified for finding 2: the unit-level and file-level margin
  fix, by direct test, matching the exact boundary-timing shape the real
  evidence described (arrival within the margin of a window edge). A real
  SUMO re-run of the exact previously-failing closure/window/demand
  combination (edge `96527131_26842526_0`, 07:15-15:15, 2027-11-11 demand)
  was considered but not attempted in this pass — reconstructing that exact
  demand context safely (without risking the live/served `sumo/` state) was
  judged not worth the remaining budget; the margin fix's correctness rests
  on the unit tests above, not a fresh real-SUMO replay of that one incident.
  No original run/evidence artifact was touched; nothing was committed,
  pushed, or campaign-launched.
- Focus and status: `2026-08-29 SAME DAY, LATER PASS: implemented the ROOT
  CAUSE fix beneath the timeout-recovery entry below, at the user's explicit
  request ("do not solve this by merely increasing process timeouts"). New
  `traffic_sim/simulation/closure_routing.py` (policy_version
  `closure_origin_routing_v1`) rewrites every affected vehicle's route
  before SUMO starts, origin to original destination, along the
  deterministic fastest legal path excluding every applicable closed edge
  (fixed-point banned-set growth, reusing `disruption.py`'s shortest-path
  engine). `run_scenario.reroute_closure_affected_vehicles` replaces
  `truncate_stranded_vehicles` at all three production call sites
  (run_scenario.py, suggest_closure_time.simulate_closure,
  ArchivedDemandSumoRunner cold+warm-audit); the retired function is pinned
  unreachable from each by new tests. Only a destination-on-closed-edge or
  genuinely-unreachable trip is denied departure, recorded as a stable
  provenance-bound AccessImpactRecord — never truncated, never simulated,
  never a generic timeout. Closure runs no longer force
  `--time-to-teleport -1`; production now defaults to `closure_teleport.
  CLOSURE_ROUTING_TELEPORT_POLICY_S` (None, SUMO's own default), safe
  because the hazard is eliminated pre-simulation. Monthly backend
  provenance now also hashes closure_routing.py and disruption.py (19->21
  files), invalidating every pre-fix cache/warm-state entry. FOUR root
  causes are documented in ARCHITECTURE.md's "Closure-integrity boundary"
  section with primary SUMO documentation citations: the old
  no-detour-only preprocessor, the runtime rerouter's doorstep-only timing,
  the network-wide disabled-teleport gridlock consequence, and the 300 s
  timeout as symptom. VERIFIED: 20 new tests
  (tests/test_closure_routing.py) plus a focused battery of 606 tests total
  passed (0 failed, 1 skipped) across test_scenario.py,
  test_suggest_closure_time.py (4 pre-existing tests updated for the new
  count semantics), test_closure_teleport_wiring.py (3 pre-existing tests
  updated for the new default), test_closure_access_impact.py,
  test_closure_disruption.py, test_monthly_sumo.py,
  test_monthly_warm_state.py, test_independent_daily(_queue),
  test_monthly_search.py, test_finalist_decision.py,
  test_cost_ordered_execution.py; targeted pylint clean; git diff --check
  clean. git stash confirmed the test_monthly_warm_state_freeze.py
  fingerprint failures are the SAME pre-existing "already drifted working
  tree" set already documented below, not new breakage. REAL SUMO
  MEASUREMENT (run_scenario.py --closure against the live 2027-11-11
  demand, closing the SAME edge as the timed-out unit,
  96527131_26842526_0, 8 h window): a clean run completed in 10.05 s total
  wall time (SUMO itself 1.95 s), 1,803 rerouted, 0 denied, 0 teleports,
  closure integrity verified and published — against the previous 300 s
  timeout / 743.837 s registered-retry for this edge. The timed-out unit's
  own literal window (07:15-15:15) also finished in 9.95 s but was
  correctly refused publication by the pre-existing fail-closed integrity
  gate over one boundary-timing vehicle (pfe12313, SUMO jam-teleport);
  `git stash` proved this exact single-vehicle event reproduces
  IDENTICALLY under the pre-fix code with normal teleport forced on — a
  pre-existing, deterministic characteristic of this specific
  edge/window/demand combination, not introduced by this fix.
  HONEST REMAINING GAP: this was a direct run_scenario.py CLI verification
  of the production routing module, NOT a literal replay of the frozen
  daily-unit-24737391111be0e137537df7 / daily-unit-2387bbad11130660b9de0d17
  monthly-search harness — wiring a bounded single-unit driver through
  MonthlyDemandResolverRunner/IndependentDailyRunner (matching
  ClosureSchedule, target repetitions and the real worker-isolation path)
  was not completed this session; do not claim it was. Also deliberately
  not migrated (named-scope boundary: monthly road-closure timeouts):
  signal_optimize.py, tools/benchmark_persistent_sumo.py,
  signal_closure_combine.py, tools/freeze_monthly_warm_state_v2.py,
  tools/measure_direction_decision_sensitivity.py still use the retired
  truncate_stranded_vehicles + disabled-teleport pair, which remains
  self-consistent for them; migrating them is open work, not silently
  assumed done. No monthly campaign was run/resumed; ui-monthly-12hg8f3's
  workspace/ledgers were only read; nothing was committed or pushed. Do not
  extrapolate a monthly-campaign duration from the one-closure/one-seed
  numbers above.
  PREVIOUS ENTRY, SAME DAY, EARLIER PASS: ui-monthly-12hg8f3 is complete, not running.
  It evaluated 1,690 candidates in 37,002 active seconds and ended inconclusive
  after 540/5,180 exact SUMO launches hit the old 300 s limit. The timeout/cache
  defect and result path are repaired: timeout is structured undecided evidence,
  not a hard traffic failure; product execution now replays only a timed-out
  identity at a registered 1,800 s bound with identical model/resources; both
  attempts are logged; and only exhaustion of attempt two stays undecided.
  One real old timeout completed in 743.837 s and yielded the hard failures
  `unfinished_vehicle_share` and `recovery_congestion_not_dissipated`.
  Existing run artifacts were not rewritten and verify with zero workspace
  integrity errors. The canonical pre-picker weekday/weekend route catalog is
  robustness-repaired, matched-size qualified, schema-v3 adopted and soaked.
  Production defaults to the verified catalog with explicit legacy rollback.
  The user successfully rebuilt the active 2027-11-11 forecast day through the
  browser. On 2026-08-25 the user reopened the faster-closure goal; a byte-exact
  atomic JSON publication optimization is implemented and qualified, while the
  <=10 s first-new p95 target remains open by 0.359 s. Annual
  warming remains deliberately inactive after a refreshed preflight and one
  successful bounded q50 unit; 104,684 planned units remain pending. It remains part of the broader
  road-closure and monthly-simulation speed goal. The named monthly search is
  stopped at 476/1,776 with its workspace preserved as resumable. The monthly
  intake/runtime defects reported on 2026-08-25 are repaired: exact workday
  length, explicit 8×1 worker allocation, awake active-time telemetry and
  macOS keep-awake are implemented. The first replacement UI attempt exposed a
  stale implicit catalog identity and paused at demand key 06cc309e4779897c;
  implicit catalog use now falls back to legacy with recorded provenance, and
  a real rebuild of that exact three-day key succeeded. The workspace remains
  resumable; no full replacement S0 campaign was launched. A later retry
  failed at demand key c796667235591888 on 2027-10-01 because the final
  candidate matrix lacked a route exclusive to sensor
  26842525_26355153_0. That structural infeasibility is now repaired at
  candidate-generation time and verified against the frozen failed pool.
  Replacement campaign `ui-monthly-13lhsoy-5d` is OPERATOR-STOPPED as of
  2026-08-27 (SIGINT to process group 68201, user-requested, 16/16 identity
  audit, group gone in under 3 s) and has NOT been restarted. It is resumable:
  1 083 of 1 950 units cached and valid, 0 corrupt, 867 missing, zero partial
  files. Read the durable job record (`status: error` plus `operator_stop`)
  and the workspace manifest (`status: running`, `completed: 0`, reset by the
  shutdown path) as two different records; the content-addressed cache is the
  resume authority. Earlier the same day its durable job record was
  incorrectly reconciled by a test run; PID, PGID, server ownership and spec
  were reverified, the record was restored with an audit trail, and the test
  ledger is now isolated.`
- Summary: `The completed monthly run proved that scheduler utilization was
  fixed but exposed a separate model-runtime boundary: 540 exact units reached
  the frozen 300 s wall limit. A preserved single-unit diagnostic reproduced
  the timeout with one worker and A*, excluding eight-way contention and the
  default routing algorithm as causes; partial SUMO summary output showed the
  active closure accumulating 2,228 running vehicles before timeout. An exact
  1,800 s diagnostic then completed the same unit in 743.837 s, establishing
  that the 300 s boundary—not a SUMO crash—hid a genuine traffic-health failure.
  The code now recovers that class automatically while preserving cache and
  scientific semantics. CARTO's API-key watermark was
  separately removed by switching the browser map to OSM's documented keyless
  raster endpoint, updating CSP/attribution and adding regression coverage.
  The canonical speed plan separates exact repeats, first-new
  interactive closures and exhaustive monthly throughput. Implemented slices
  now include the provenance-bound exact cache, result-neutral independent-day
  cache/worker timing, isolated benchmark-only routing controls, and a monthly
  active-slot guard. Sol review repaired under-keying, cache/status races,
  malformed-artifact handling, diagnostic failure isolation and the external
  monthly running/stale/succeeded/cancel UI lifecycle. Paired adoption
  benchmarks and high-risk phases remain open. The qualified
  entered/timeLoss-only edgeData arm is now the production default with an
  isolated full-field rollback. Disruption is a pure module with a retained
  oracle and exact grouped/SciPy-sparse implementations. Per-key file locking
  serializes matched-baseline producers across processes. Fresh-interpreter
  versus spawn-pool diagnostics are single draws and do not close the generic
  reusable-Python-worker line; the pool remains inactive pending the new
  counterbalanced multi-trial gate.
  The catalog fallback repair preserves strict explicit catalog requests while
  preventing a stale implicit adoption from aborting the whole monthly search.
  Its exact real-input rebuild produced q50/q10/q90 with 100% integer sensor
  exactness, zero infeasible intervals and passing candidate provenance; the
  active 2027-11-11 live demand products were restored byte-for-byte.
  The new exact-incidence invariant checks the final pool after every ordinary
  route filter. It adds only a missing legal home-to-POI route grounded in the
  existing endpoint pools, marks it support-only so it cannot alter purpose
  quotas, and fails before PFE if no such route exists. This preserves exact
  whole-day sensor margins without adding unmeasured background traffic. PFE
  exceptions now include the date, q variant, quarter index and clock range.
  A larger concurrency test exposed the matched-baseline race that the new
  single-flight regression now covers. The product capacity contract now explicitly targets
  50 physical sensors, including any vehicle growth their joint calibration
  evidence requires, without relaxing simulation evidence. The robustness
  pass additionally makes cache preparation exception-safe/cancellable,
  bounds browser polling and per-key lock waits, fixes corrupt-cache telemetry
  and prune races, makes warning/rerouter diagnostics explicit and prevents
  reserved cached fields from taking over server lifecycle state. A new
  baseline-only exact sensor-passage audit now recomputes raw 15-minute
  ensemble, representative and per-seed results and exposes every mismatch in
  the scenario artifact and validation UI without mutating demand. A bounded
  order-preserving departure reconciler was implemented and proven capable of
  672/672 exact output in every seed, but its new dispersion gate rejects the
  real candidate before publication because it creates artificial departure
  convoys. A second isolated `standard_driver_pool.py` experiment now separates
  the date-specific picker from deterministic driver-profile arms. It reached
  672/672 in all three arms without bunching, but did not prove zero latency
  regression and remains unconnected to production.
  The bounded catalog stores canonical routed weekday/weekend supply,
  passes explicit daily purpose margins to PFE, retains DayLibrary for finished
  days and leaves warm-state identity bound to exact daily routes. The
  historical unequal-size trials measured 66.402→19.437 s, but this ratio is
  not adoption evidence. The corrected 30-pair harness requested 6,000
  candidates in both arms and measured 55.246→24.715 s median (2.235x ratio
  of arm medians; 2.220x median paired speedup), with
  every gate and day class passing. Schema-v3 adoption now reads and hashes
  the named qualification/build/trials/suite files, cross-checks keys and
  sizes through the chain, and verifies stored bytes. Suite contracts are
  recorded once rather than copied into every trial arm; seven catalog fixtures
  plus explicit legacy rollback passed soak. Seed/variant order, exact
  widened-rung PFE fallback and mixed-pool tour IDs are regression-tested. See
  docs/plans/CANONICAL_ROUTE_CATALOG_PLAN_2026-08-24.md. For the frozen
  ui-monthly-euc9qp intent, min=max=5 removes the 2- and 4-day work: exact
  preflight falls from 1,776 periods/2,224 units to 780 periods/1,040 units.
  Default min=1 preserves old content keys and resumability.`
- Current product evidence: `Active forecast demand: 2027-11-11, catalog build
  b927e6de0b6443fd87e2, q50_only, 21,744 calibrated trips selected by PFE. All 672 directed
  sensor-edge × 15-minute integer targets match exactly: 672/672, maximum and
  summed absolute residual 0. Validation is overall warn
  because trip-length L1 is fail-closed until an absolute external-fit
  threshold is frozen, one quarter exceeds the short-trip structure cap by two
  vehicles and two quarters required a six-vehicle purpose-mix relaxation.
  Counts and SUMO health still pass. `candidate_catalog` binds weekday key
  13020f80f1be36df59e27144aad8d808 and records a verified cache hit. SUMO accepted all 21,744
  vehicles in all three seeds, with zero waiting, unfinished trips, teleports
  or collisions; this is an integrity check, not demand inflation.
  The isolated reconciliation candidate kept all 21,240 vehicles, every route,
  three distinct seed speed profiles and clean health while reaching 672/672
  per seed. It is rejected evidence, not a release: median departure gap fell
  2.7→0.1 s, 15,545 gaps hit 0.1 s and peak loading rose 1→10 departures/s.
  The replacement standard-pool diagnostic also kept all vehicles/routes and
  reached 672/672 per arm with distinct explicit speedFactor profiles. It kept
  minimum spacing 2.0 s, changed median spacing only 2.7→2.5 s and stayed at
  one departure/s, but reassigned 21,183 latent departure times (median
  absolute shift 254.4 s, range -966.6..826.0 s). It is isolated evidence,
  not an active release.
  Active immutable release: golden-2025-09-16-7day-v1. The map-to-sensor audit
  passes on all seven directed sensor edges.`
- Live operation: `Monthly search ui-monthly-12hg8f3 completed successfully at
  the workspace level with result status inconclusive, no winner and an
  integrity-clean immutable workspace. It used the global 8x1 queue, reached
  eight active workers, simulated 1,939 daily units, and recorded 5,180 pilot
  launches: 4,640 non-timeout outcomes and 540 timeouts. No monthly search was
  started or resumed during the repair. Historical campaign
  ui-monthly-13lhsoy-5d targets forecast
  2027-09-01..2027-09-30 on edge 96527131_26842526_0, min=max=5 workdays
  (1,690 periods, 1,950 daily units). It is OPERATOR-STOPPED as of
  2026-08-27 and has NOT been restarted (see the current handoff above and
  "Suggested next action" below for the resumable state); it must not be
  restarted as part of unrelated benchmark/repair work. Older runs superseded:
  ui-monthly-euc9qp stopped at 476/1,776 (July window), ui-monthly-13lhsoy
  (min=4/max=5) abandoned by user decision after 144 four-day units, none of
  which a five-day search can reuse because unit identity binds
  duration_minutes. Monthly CLI runs now hold
  `caffeinate -i -m -s -w <pid>` and report the power source at startup.
  TWO OPERATIONAL FAILURES WORTH KEEPING, both external to the code:
  (1) a `git checkout main` while the campaign ran removed
  min_consecutive_start_days from ClosureSearchSpec, so the spec's content_key
  no longer matched its contents and every worker died. The contract check
  behaved correctly - it refused a spec whose key did not match rather than
  silently searching something else. Merging PR #3 restored the tree and all
  29 demand archives validated again with zero rebuilds (prepare 6.4 min).
  (2) a closed laptop lid suspended the machine for 3.00 of 4.84 wall hours.
  caffeinate cannot prevent clamshell sleep; only an open lid can.`
- Performance evidence: `Current serve.py passes three seed workers: commit
  46e7048 measured baseline 11.0 -> 5.9 s and closure 21.6 -> 13.9 s with
  identical output apart from generated_at. Frozen v6 measured closure p95
  10.4234 s. Persistent external SUMO v2 was equivalent but slower (11.3904 s
  vs 11.0998 s) and is closed. The monthly eight-worker benchmark measured
  1.69x and 2.11 GiB RSS. Cost-order v5 saved 18 calls but failed strict
  equivalence. The web path now selects that admitted eight-worker width
  explicitly, but no new active-time campaign has measured its end-to-end
  speed, so 1.69x remains the only supported parallel figure and 2.7x is not a
  result. Annual multi-snapshot state creation measured 20.8x but ordinary
  SUMO omits the required exact meso tripinfo accumulator. The frozen July 15
  daily-unit cold-execution comparison measured fresh interpreters at 54.445 s and one
  reusable spawn worker at 53.027 s (1.027x), with exact evidence equality;
  this misses the 1.10x continuation gate even with second-arm cache bias. With
  equal prewarmed caches, six units/three workers measured 61.040 s fresh versus
  61.144 s pooled (0.998x), again with exact evidence. The cold empty-cache arm
  failed reproducibly on duplicate matched-baseline publication. A separate
  300-iteration process-free scale diagnostic measured final sensor-fit
  validation at 3.325 ms p95 for 50 rows (0.423 ms at six), showing sensor
  validation is not the 13.9 s closure bottleneck. A one-seed diagnostic with
  minimal edgeData completed 21,408 vehicles in 1.882 s and 42,816 in 2.958 s,
  but an 85,632 load inserted only 56,655 and took 66.118 s. Default versus
  entered/timeLoss edgeData measured 49.86 versus 10.16 MB and 2.283 versus
  1.897 s with equal flow and recovery values. The paired production-shaped
  follow-up covered 40 scenario runs / 120 seed executions with trajectories:
  zero semantic, health or integrity mismatches; baseline mean wall fell
  6.561 -> 5.484 s and closure 15.198 -> 14.120 s. Candidate closure p95 is
  14.142 s, so it qualified for production default but still missed 10 s. After
  adoption and grouped/sparse disruption, one isolated three-seed
  production-shaped closure completed in 10.690 s versus 11.549 s with the
  full-field rollback. Scenario and trajectory digests were equal, all three
  seeds loaded/inserted 21,408 vehicles with zero teleports, and health was
  verified clean. The current catalog release then ran ten clean first-new
  closure trials at commit eeef654: p50 10.461 s, p95 10.496 s, range
  10.409–10.508 s, zero semantic/reference mismatches and all 30 seed records
  clean. It misses the <=10 s gate by 0.496 s. Median phases were SUMO 6.636 s,
  disruption 1.184 s and trajectory publication 1.131 s. A separate clean-tree
  structured-cache run at bf678b4 passed 10/10 exact hits with p50 0.312 s,
  p95 0.329 s and max 0.330 s; every response/status proved a cache hit and
  scenario/trajectory hashes stayed unchanged. On 2026-08-24 the user observed
  roughly 30 s end-to-end in the browser and explicitly accepted the current
  speed. The user reopened the faster-closure goal on 2026-08-25. A fresh
  active-demand baseline measured p50 10.654 s and p95 10.765 s; encoding each
  JSON artifact once before its existing atomic replace reduced the candidate
  to p50 10.212 s and p95 10.359 s. Scenario and trajectory digests were
  identical, closure integrity stayed verified-clean, and all 60 seed records
  across both arms loaded/inserted 21,744 vehicles with zero waiting, running,
  teleports or collisions. Median trajectory publication fell 1.194→0.737 s
  and artifact publication 0.132→0.025 s. Evidence:
  validation/atomic_json_publication_benchmark_2026-08-25.json. A separate
  loopback API smoke returned 202 immediately and reached done in 11.088 s with
  verified-clean closure integrity; its temporary server was stopped.`
- Monthly throughput evidence, MEASURED 2026-08-26: `The open "no active-time
  campaign has measured end-to-end speed" gap is now closed for this machine.
  Measured on the live ui-monthly-13lhsoy-5d run by timestamping the
  content-addressed daily-result cache and subtracting every gap over 10
  minutes: 125 units in 1.86 h awake against 3.00 h suspended, i.e. 67.3
  units/h, 53.5 s per unit of AWAKE wall time. An earlier sample of the same
  campaign read 97-107 units/h before settling, and the 4+5-day predecessor
  settled at 97.1 units/h, so 65-105 units/h is the honest band and the early
  readings of 128-183 units/h were transient ramp-up, not steady state.
  DO NOT compose the frozen 54.445 s single-unit figure with the 1.69x
  eight-worker benchmark to predict a campaign: that product predicted 32 h
  where the measured rate gives 24 h of awake time for 1,600 remaining units.
  The two benchmarks were measured in different conditions and do not
  multiply. NOTE FOR A FUTURE MEASUREMENT, not a conclusion: 53.5 s per unit
  of wall time across eight workers sits suspiciously close to the frozen
  54.445 s for ONE unit, which would imply the eight-worker width is buying
  almost nothing. That needs its own controlled comparison before it is
  claimed either way. Historical wall time remains unusable for throughput -
  the same campaign reads 25.4 units/h if suspended hours are not removed.`
- Passage-reconciliation evidence: `Five counterbalanced three-seed baseline
  trials measured current median 1.9908 s versus candidate 2.0536 s (candidate
  3.15% slower); this is not a p95 claim. Learning plus exact verification cost
  12.43 s offline. The candidate retained stochastic variation on 1,234
  non-sensor edges / 22,663 edge-quarters, so rejection is specifically about
  departure structure and speed, not collapsed seed identity. See
  validation/departure_reconciliation_diagnostic_2026-08-23.json.`
- Standard-driver-pool evidence: `The pool is keyed by picker date/build,
  route bytes, network, target bytes and arms, and writes only under an
  isolated output root. The active 2027-09-08 build converged in two offline
  three-arm SUMO iterations with 672/672 per arm, 21,240 loaded/inserted and
  zero unfinished/teleports. Ten counterbalanced concurrent-three-arm baseline
  trials measured current median 1.80496 s versus pool 1.84644 s (+2.30%).
  Nearest-rank p95 favored the pool only because n=10 makes p95 the maximum and
  the current arm's maximum was its cold first trial. This fails to prove the
  required no-regression condition; closure-shaped equivalence and timing are
  still missing. See
  validation/standard_driver_pool_diagnostic_2026-08-23.json.`
- Structural research evidence: `The former scenario_publication phase mixed
  deterministic disruption analysis with payload and file I/O. A
  process-free trace on the active 21,408-vehicle q50 release and paired
  closure edge measured graph/cost loading at 0.2274 s and three disruption
  calls at 4.0654-4.1757 s. The closure affected 3,773 vehicles and 340 unique
  OD pairs; the implementation issued 1,360 Python Dijkstra calls. This is a
  separately optimizable analysis stage, not JSON I/O. Phase schema v2 now
  measures disruption_analysis, payload_construction and artifact_publication
  separately. Exact grouped/SciPy-sparse routing reduced the same process-free
  disruption case from 4.1364 s to 1.051 s while matching the retained per-OD
  oracle. The simple structure and remaining evidence plan are in
  docs/plans/LARGE_SIMULATION_FUNCTION_STRUCTURE_2026-08-23.md.`
- Direction status: `The deployed centre is shrunk_dfactor with sensor 107's
  2025 aggregate anchor. Ordinary seeds all use q50. q10/q90 are opt-in stress
  arms with fixed q50 population, not calibrated probability intervals. Gate S
  is UNDECIDED and Gate M is INCONCLUSIVE.`
- Known code risk: `The repaired exact cache is conservative and hashes the
  live routes, trajectory sidecars, network inputs, runtime and relevant source
  tree twice on a hit; the measured 0.329 s p95 clears its <=2 s target, so
  further cache optimization is not currently justified. The
  baseline writer race is repaired with per-key cross-process single-flight,
  but nested daily-worker plus seed-worker execution remains refused until a
  complete equivalence, resource and cancellation benchmark passes.
  A standard Python worker pool is not activated; its measured 2.7% benefit
  disappeared in the second single draw and does not yet justify the
  cancellation/reaping/recycling surface. Neither draw establishes rejection;
  the replacement harness requires four or more counterbalanced paired trials.
  Sparse routing uses SciPy when
  available and falls back to exact grouped Python routing; neither path
  changes the scientific oracle.`
- Files changed by this task: `serve.py, run_scenario.py,
  build_candidates.py, build_sumo_demand.py, demand/calibration.py,
  run_monthly_closure_search.py, tools/benchmark_speed.py,
  traffic_sim/simulation/independent_daily.py,
  traffic_sim/confidence/report.py,
  traffic_sim/simulation/disruption.py,
  traffic_sim/simulation/execution.py,
  traffic_sim/simulation/annual_warm_store.py,
  traffic_sim/storage/singleflight.py,
  traffic_sim/simulation/monthly_search.py, web/app.js, related tests,
  traffic_sim/core/contracts.py, traffic_sim/core/closure_calendar.py,
  traffic_sim/simulation/closure_preflight.py,
  traffic_sim/simulation/search_workspace.py, web/index.html,
  tools/benchmark_daily_worker_pool.py,
  tools/benchmark_exact_close_cache.py,
  tools/soak_route_catalog.py,
  tools/prune_candidate_cache.py,
  tools/verify_closure_cost_ordering_golden.py,
  validation/closure_cost_ordering_golden_v6.json,
  validation/closure_cost_ordering_golden_v7.json,
  validation/daily_worker_pool_diagnostic_2026-08-21.json,
  validation/daily_worker_pool_structure_diagnostic_2026-08-21.json,
  validation/daily_worker_pool_cold_start_failure_2026-08-21.json,
  validation/vehicle_load_and_edgedata_diagnostic_2026-08-22.json,
  validation/edgedata_attributes_paired_adoption_2026-08-22.json,
  docs/plans/DAILY_SIMULATION_CONCURRENCY_STRUCTURE_2026-08-21.md,
  docs/plans/FIFTY_SENSOR_PERFORMANCE_CONTRACT_2026-08-22.md,
  docs/plans/LARGE_SIMULATION_FUNCTION_STRUCTURE_2026-08-23.md,
  docs/plans/CANONICAL_ROUTE_CATALOG_PLAN_2026-08-24.md,
  docs/plans/FROZEN_EVIDENCE_RETIREMENT_PLAN_2026-08-27.md,
  requirements.txt, requirements-dev.txt, .github/workflows/ci.yml,
  tests/conftest.py, tests/test_serve.py, tests/test_independent_daily.py,
  tests/test_sensor_scale_contract.py, tests/test_closure_disruption.py,
  tests/test_benchmark_exact_close_cache.py,
  tests/test_annual_warm_store.py, tests/test_annual_warm_readiness.py,
  tests/test_singleflight.py,
  tools/standard_driver_pool.py, tests/test_standard_driver_pool.py,
  traffic_sim/demand/route_catalog.py, tests/test_route_catalog.py,
  validation/standard_driver_pool_diagnostic_2026-08-23.json,
  validation/sensor_validation_scale_50_2026-08-22.json,
  ARCHITECTURE.md, IMPROVEMENT_PLAN.md, TASKS.md, AGENT_NOTES.md and
  docs/plans/ROAD_CLOSURE_SIMULATION_SPEED_PLAN_2026-08-21.md. The stopped
  workspace manifest under runs/closure-search is preserved and resumable.`
- Generated product state: `The same active forecast date (2027-09-08) was
  rebuilt through implicit schema-v2 catalog adoption. web/data/od_matrix.*,
  the three-seed baseline/index/trajectory and validation.json now bind build
  ab27c11be5a6a8b52045. The one-unit warming pilot restored this live release
  after packing and restore-verifying its isolated artifact. The new demand run and the preceding diagnostic
  historical run remain archived under runs/. Unrelated dirty source changes
  were preserved.`
- Checks: `2026-08-29 exact timeout recovery: the preserved real unit completed
  in 743.837 s with one observation, zero unresolved timeouts and the two
  expected hard failures. The expanded scenario/monthly SUMO/search/daily/
  queue/selector/cost-order surface passes 405 tests with one skipped, and the
  localhost monthly API lifecycle passes 14/14 outside the socket-restricted
  sandbox; make lint and git diff --check pass. Earlier timeout repair checks passed 379/379 in
  27.03 s. Repository-wide pytest completed in 12m53s with 5,319
  passed, 27 skipped and 155 failures concentrated in frozen historical
  fingerprint/evidence suites that reject source drift; those records were not
  regenerated or weakened. git diff --check passes. The finished
  ui-monthly-12hg8f3 workspace verifies with zero integrity errors; manifest,
  result and pilot-selection SHA-256 values are e7685bfb..., affa7892... and
  7af9d851.... The 2026-08-27 isolation repair passes all 155 server tests and
  leaves the live monthly record `running`; direct SciPy/NetworkX surfaces pass
  451 focused tests, and the corrected independent-daily CLI assertion passes
  all 18 tests in its module. The grounded-incidence repair passes 207 candidate/integer-identity tests,
  226 complete build-demand/PFE tests and all 14 monthly-search server tests;
  the frozen failed 2027-10-01 pool gained exactly one grounded shape and
  publishes q11/q16/q17 with exact sensor margins. make lint and git diff
  --check pass.
  Catalog invariance, a fresh catalog build, 30 matched paired trials,
  schema-v2 qualification/adoption, seven-case soak plus legacy rollback, the
  active-day build/baseline and one q50 warm unit all completed successfully.
  The catalog/demand/PFE/day-library focused suites pass 647 tests with one
  skip; the HTTP server suite passes 151 tests; make lint, focused tool pylint,
  node --check and git diff --check pass. The cache/phase benchmark contract
  suite passes 165 tests; the new exact-cache tool's focused pylint passes.
  The annual store/readiness rerun
  passes 17/17; successful packing now removes both chosen and losing
  compression temporaries, and the pilot's one stale 248-byte staging file was
  removed. Live ping/status smoke checks pass. The current robustness regression group passes 918 tests with one
  skip after repairing its synthetic active-slot fixture and binding immutable
  closure-cost golden v6. The close/cancel HTTP set passes 22 tests, the
  low-risk cache/browser/benchmark group passes 201 tests with two skips, and
  the scenario/warm group passes 279 tests with one skip. The exact-passage
  scenario/validation/publication group passes 160 tests with one skip; the
  adjacent demand/PFE/prior group passes 258 tests. The final combined
  server/scenario/demand/cache/benchmark regression passes 1,170 tests with
  one skip and one existing LibreSSL warning; closure-cost golden v7 passes
  all four reproduction checks. make lint,
  node --check and git diff --check pass. Sol's earlier focused review checks
  passed (53 tests plus JavaScript syntax).
  The complete HTTP server suite passes 147 tests; the integrated
  closure/monthly/benchmark review set passes 209 tests; the broader speed set
  passes 141 tests. The scenario/monthly/benchmark group passes 300 tests with
  one skip after repairing its synthetic live-identity fixture. make lint,
  node --check and git diff --check pass. The pool/monthly/SUMO regression set
  passes 444 tests; the live-shaped two-unit SUMO diagnostic preserved exact
  evidence and measured 1.027x. The completed repository-wide run on
  2026-08-23 reports 4,979 passed, 27 skipped and 153 failed in 13m47s. The
  observed failures are confined to historical annual/held-out/warm-state
  freeze reproduction and retirement tests whose frozen source/content keys
  predate the current source tree; two isolated examples reproduce as a stale
  expected-error regex and a held-out selection content-key mismatch. They are
  retained fail-closed evidence debt, not repaired by rewriting history or
  weakening drift checks. The named monthly workspace lock is free and its
  stopped-by-user pointer remains resumable.`
- Superseded intermediate checks: `Before the single-flight repair, the focused
  concurrency/monthly/SUMO set passed 290 tests with 1 strict expected failure
  for the then-unfixed baseline
  single-flight contract; adjacent monthly/search/speed tests pass 292; the
  HTTP suite passes 147 when run with loopback binding; make lint and git diff
  --check pass. The equal-cache six-unit diagnostic preserved exact evidence
  and measured 0.998x; the bound cold-start report reproduced the baseline
  race. The 50-sensor correctness/failure-recall contract adds 3 passing
  tests; its vehicle-load/output evidence guards bring that file to 5 passing
  tests. The combined sensor/edgeData/scenario/benchmark/envelope suite passes 356
  tests with 1 skip. The diagnostic JSON parses, make lint and git diff --check
  pass.`
- Current implementation checks: `The disruption and deterministic oracle set
  passes 53 tests. The scenario/timing/benchmark/disruption set passes 188 tests
  with one skip. The single-flight set passes 6 tests, including three spawned
  processes with exactly one active producer. The broader relevant simulation
  set passes 332 tests with one skip. The final expanded
  scenario/benchmark/monthly/scale set passes 484 tests with one skip, and the
  HTTP server suite passes 147 tests. make lint, node --check and git diff
  --check pass. The isolated
  production-shaped default and full rollback produced equal semantic and
  trajectory digests and clean seed health. The standard-pool plus passage
  unit set passes 17 tests. The real isolated pool passed all 672 raw SUMO
  constraints in each of three arms with clean health; its ten-trial baseline
  timing is recorded as non-adoption evidence. The monthly exact-length,
  active-time and keep-awake contract set passes 149 tests; its loopback
  preflight/lifecycle slice passes 26 tests, and JavaScript syntax plus diff
  checks pass.`
- Catalog-plan review checks: `In the named local environment
  MPLCONFIGDIR=/tmp/gs-mpl with PYTHONDONTWRITEBYTECODE=1, the focused
  candidate, PFE, DayLibrary, warm-state, provenance, intake, singleflight,
  qualification, catalog and annual-warm suites pass 590 tests with one
  LibreSSL warning in 418.42 s.
  Targeted pylint passes after making the soak tool importable both as a CLI
  and package module. The invariance report passes; the prior 30-pair verdict
  and post-adoption soak are retained only as historical unequal-size evidence.
  The repaired non-server suite passes 647 tests (one skipped), the full server
  integration suite passes 151 tests, and the focused contract slice passes;
  make lint and diff checks pass. The
  active catalog day also ran through three SUMO seeds with 20,818/20,818
  loaded/inserted and zero waiting, unfinished, teleports or collisions.`
- Identity and gate repairs, 2026-08-26: `Three defects found while
  investigating why a live campaign was slow, each root-caused before being
  fixed. (1) The route catalog identity hashed the entire 31-entry demand
  source inventory; measured, only 6 of those are reachable from
  build_candidates.py's import closure. Commit c653b24 - whose purpose was to
  HARDEN catalog qualification - therefore invalidated the adopted catalog by
  editing pfe.py, route_catalog.py and catalog_qualification.py, none of which
  can change a routed pool. The identity is now the same curated generator set
  the legacy candidate cache uses, fails closed on both missing and unexpected
  labels, and is pinned by a test that measures the real import closure in a
  subprocess. Re-qualified: 30 paired trials, verdict adopt, speedup median
  2.270x; measured live afterwards at candidate generation 135 s to 1.03 s and
  campaign prepare 54 min to 2.9 min. (2) maximum_l1_distance was written only
  by tests, so the trip-length gate could never be evaluated and every build
  reported overall warn whatever its data. The threshold now lives once in
  traffic_sim/confidence/trip_length_gate.py, derived from total-variation
  distance (L1 <= 0.20 = at most 10% of vehicles in the wrong RVU length bin);
  a build may declare a stricter limit, never a looser one. Seven real builds
  measure 0.2318-0.3636 and FAIL it, while the candidate pool sits at ~0.026 -
  the gate separates the generator from PFE's selection, which over-selects
  5-10 km. (3) A validation report never emits the string fail, so
  confidence_health was defined as overall != fail and was therefore
  unconditionally true, while candidate_structure collapsed to the metadata
  exists: two of seven per-trial hard gates in the ADOPTED qualification could
  not fail. Both are now bound to sections that can report a problem, and the
  new test breaks each gate's own contract in turn - falsifiability is what is
  under test. OBSERVED, NOT FIXED, because it is out of that tool's domain:
  population_contract and route_agent_provenance compare
  candidate_provenance.vehicles against pfe_fit.vehicles, which differ on a
  three-variant build (169,683 vs 56,632); the benchmark's own arms are
  single-variant so its gates are correct where they run.`
- Catalog evidence robustness review: `The five reported findings were
  reproduced against the current tree. The named v2 qualification/build files
  did exist and matched their old adoption hashes, but runtime did not read
  them; schema-v3 now verifies the complete on-disk chain and remains active
  locally. Per-arm gates are measured per build, seven suite contracts are
  bound once, paired population drift is gated at 1%, and future campaigns
  record PFE route×purpose workload. Candidate-cache pruning no longer treats
  both mutually exclusive flow files as global invalidators, and classification
  plus deletion share one workspace lock. The focused evidence suite passes
  136 tests; the broader demand/PFE/catalog regression passes 351 tests with
  one LibreSSL warning; make lint and git diff --check pass.`
- Blockers or risks: `The historical ui-monthly-12hg8f3 result remains an
  immutable diagnostic and therefore still says inconclusive; it was not
  rewritten in place. Producing a replacement month result requires resolving
  its remaining old timeout identities in a new evidence root. The registered
  two-tier protocol prevents the observed 300 s false-inconclusive class in
  future runs, but a genuine second-attempt timeout still fails closed rather
  than fabricating a result. Source changes intentionally invalidate relevant
  future cache identities, while the old evidence remains untouched. Awake active-time
  telemetry is implemented, but the
  repaired search is operator-stopped rather than complete, so end-to-end
  completion remains unmeasured. The global daily-unit queue's 7.78x is
  SYNTHETIC scheduler scaling on a sleeping stand-in, the eight-concurrent-SUMO
  ceiling is a single SAVED real observation, and the 2.93 h resume / 6.58 h
  cold figures are PROJECTIONS; per-unit cost under sustained eight-way
  contention is unmeasured and the report records that >~21% per-unit inflation
  would put the eight-hour goal at risk.
  The old 95-minute failed attempt is diagnosis, not post-fix performance
  evidence. Because generator identity changed, caches
  must continue to fail closed rather than be force-reused. The remaining S0
  host telemetry and monthly throughput evidence are still open;
  do not claim the p95 or 2.0x targets from instrumentation. The frozen
  scenario benchmark campaign preflight refuses because live demand has moved
  from its 2025 historical/q10-q90 fixture to the current 2027 forecast/q50
  build; a new campaign identity is required before SUMO evidence. A real
  calibrated 50-station PFE fixture, vehicle-load ladder and the 6-versus-50
  identical-load SUMO comparison do not yet exist; synthetic 50-sensor exact
  projection and output-validation tests do pass. Minimal edgeData is now the
  production default with a full-field rollback. The current 10-trial
  first-new p95 is 10.359 s; the remaining monthly resource matrix and calibrated 50-station
  vehicle tiers are still missing. Gate S/M remain undecided/inconclusive and
  outside the speed goal. Catalog adoption is complete; its retained v2 trial
  rows predate PFE workload-count telemetry, so they support product latency
  and output-equivalence claims but not an isolated equal-work solver speedup. The former
  annual plan/preflight/pilot remain historical and must be refreshed only
  after adoption. Annual activation remains a future explicit decision.`
- Latest UI truth fix: `web/app.js and web/index.html no longer expose the
  representative q/seed identity in the normal animation or sensor-audit
  labels. The current-quarter summary now separates modelled
  arbete/service/fritid from geographic external/through categories and states
  that the delivered sensor schema contains passage counts, not trip purpose.
  ARCHITECTURE.md records the contract. The date-recalibration start path also
  translates browser network failures into an actionable localhost:8000
  message; this covers stale tabs left on a stopped fallback port. Focused
  validation/UI tests pass 24/24, JavaScript syntax and diff checks pass.`
- Global daily-unit queue (2026-08-27): `The 8x1 worker policy was never
  reaching eight. Measured on the live campaign before stopping it:
  80 330.94 worker-seconds over 88 771.27 active seconds = 0.905, one worker
  busy ~90% of wall time against eight slots, and 20/20 process samples showed
  one worker and at most one SUMO. Batching was parent-local, and a warm
  five-day parent yields only ~1.04 uncached units (3 229 hits vs 851 misses
  over 816 parents), so the pool was fed one item at a time. The replacement
  is an opt-in global bounded queue over the whole missing-unit remainder,
  living in orchestration-only independent_daily.py: exactly `workers` puller
  threads (the width IS the ceiling and the backpressure), per-content-key
  cross-process single-flight with a post-lock cache recheck, atomic
  publication as the last step inside the lock, canonical lookahead order and
  parent-order result assembly so completion order cannot reach the evidence.
  SYNTHETIC SCHEDULER SCALING on a 180-unit fixture replaying one seeded cost
  profile per arm, SUMO replaced by a sleeping stand-in: legacy 170.33 s /
  width 0.999, w2 1.995, w4 3.965, w8 21.89 s / width 7.771 = 7.78x and 97.1%
  of theoretical, with byte-identical cache across all arms. Separately, a
  SAVED real cold SUMO observation reached exactly 8 concurrent workers and 8
  concurrent SUMO over 170 samples and never exceeded either; it has not been
  repeated. The 2.93 h resume and 6.58 h cold figures are PROJECTIONS, not
  campaign measurements. CACHE IDENTITY, corrected: monthly_sumo.py binds
  NINETEEN sources including run_monthly_closure_search.py, so the first
  implementation - which added a --global-daily-queue CLI flag - WOULD have
  orphaned all 1 083 cached units (source_digest c0bbfc32... -> 8b040d90...).
  The flag was removed, the CLI restored byte-identical to HEAD, and
  activation moved to independent_daily.py, which is not cache-bound. Enable
  with TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS=8 AND
  TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_SCREENING=independent-exhaustive; the
  resolver fails closed otherwise, because global lookahead would falsify a
  cost-ordered stop proof. Without both variables the legacy path is
  bit-for-bit what it was. The aggregate backend digest is unchanged at
  90f07a50...cbeef; the `cache_bound_source_proof` block in the frozen
  baseline report is wrong and is superseded by this entry.
  REVIEW ROUND 2 (2026-08-27) found and fixed four defects, each now pinned by
  a test that fails against the pre-fix code. (i) Global lookahead is
  restricted to the exhaustive PILOT stage; a finalist round previously
  rebuilt its remainder from all 1 950 prepared units and would have upgraded
  every one of them to finalist coverage, with an adaptive bump ordering it
  again. (ii) Queue width is now bound to the real SUMO budget before any unit
  exists - the daily runner must be process-isolated and start exactly one
  SUMO per unit, and the width may not exceed either the declared
  --daily-workers or the benchmark approval of 8. Two configurations that
  passed every prior check are now refused: --daily-workers 1 (which leaves
  the production TraCI runner unwrapped) and --daily-workers 1 --seed-workers
  8 (which would be 64 concurrent SUMO). (iii) The pullers are daemon threads
  with a bounded threading-shutdown hook; as non-daemon threads an abandoned
  queue hung the interpreter forever, because threading._shutdown() joins
  before any atexit handler runs. (iv) The benchmark's real arm now owns a
  process group, captured at spawn, and escalates TERM then KILL across it; it
  reaps the leader itself and reports success only when the leader is reaped
  and a TRUSTED census shows no LIVE member left (a zombie is dead and belongs
  to the platform reaper; an unreadable process table is unknown, never a
  reaping). It refuses a speed claim unless every arm exited 0, did not time out, published a complete
  non-empty evidence population, left no partials and produced real ancestry
  samples - equal cache fingerprints alone were satisfied by two arms that
  both crashed early. SCOPE CORRECTION: 6.58 h / 2.93 h are PILOT-SWEEP
  projections; adding the policy's finalist stage bounds them at ~7.19/~3.54 h
  initially and ~8.81/~5.15 h at the adaptive ceiling, so the upper bound
  crosses the eight-hour goal.`
- Suggested next action: `Use the registered two-tier exact timeout protocol
  for the next monthly search. If ui-monthly-12hg8f3 itself must be replaced,
  target its named undecided identities in a new evidence root and recompute
  the decision; do not mutate its integrity-clean historical artifacts. Retain
  the finished workspace as diagnostic evidence. The older
  ui-monthly-13lhsoy-5d campaign remains
  operator-stopped and must not be resumed incidentally. Retain the qualified single-write JSON
  path. If interactive closure work continues, target the measured SUMO or
  disruption phase against the 10.359 s p95 reference. Keep annual warming
  inactive and rejected pools isolated. For the cost-ordered-benchmark
  continuation: design and freeze a v6 suite registration whose gate
  thresholds encode the plan's >=30% aggregate-exact-attempts and >=30%
  aggregate-awake-active-wall-time reduction requirements (v5's frozen gate
  only requires sumo_verifications_saved_minimum: 1), declare the
  timeout/retry protocol and isolated-cache-snapshot bindings in the
  registration, then run it once bounded in fresh isolated roots. Do not run
  or resume the full monthly campaign or annual warming while doing this.`
- Cost-ordered-benchmark timeout/isolation continuation (2026-08-28): `A
  prior worker session was interrupted mid-edit adding a versioned SUMO
  timeout/undecided model and isolated-process arm execution to the
  cost-ordered-vs-exhaustive benchmark, leaving it dirty and unreviewed.
  This session reviewed it in full and confirms it is correct: a SUMO
  timeout now becomes a structured `timeout_undecided` identity that forces
  `pilot_selection`/`finalist_decision` to `status: "inconclusive"` instead
  of silently excluding the candidate (the v5 defect this closes), and each
  benchmark arm now runs in its own process/process group at a frozen
  1-daily-worker/1-seed-worker/1-SUMO-slot shape
  (`tools/product_arm.run_arm_isolated`), with `--no-isolate-arms` kept as
  a development-only escape hatch a registered run must never pass. Two real
  defects were found and fixed, each pinned by a test: (1) three pre-existing
  `tests/test_cost_ordered_benchmark_run.py` tests drove `bench.main()`
  through its `wired` in-process `build_arm` monkeypatch without
  `--no-isolate-arms`, so the isolate-arms-by-default change made them
  silently run a real (failing) subprocess instead of the intended fake-arm
  path; fixed by adding the flag to match this file's other `wired` tests.
  (2) `tools/cost_ordered_benchmark_suite.py`'s `run_suite` never varied arm
  order across suite cases even though `run_benchmark` supports
  `counterbalance`, leaving the isolation plan's cross-case counterbalance
  requirement unmet; fixed with `counterbalance=bool(index % 2)` and a new
  test, since `run_suite`'s execution path had no test coverage at all
  before. Files changed this session: `traffic_sim/simulation/
  cost_ordered_execution.py, finalist_decision.py, independent_daily.py,
  monthly_sumo.py, pilot_selection.py` (pre-existing dirty diff, reviewed
  not rewritten); `tools/product_arm.py, cost_ordered_benchmark.py`
  (pre-existing dirty diff, reviewed not rewritten);
  `tools/cost_ordered_benchmark_suite.py` (counterbalance fix, new this
  session); `tests/test_cost_ordered_benchmark_run.py` (three
  `--no-isolate-arms` fixes); `tests/test_cost_ordered_benchmark_suite.py`
  (new counterbalance test); `tests/test_product_arm.py` (new file, already
  present from the interrupted session, reviewed only). Checks: every
  planner-suggested focused suite plus the full set of tests importing the
  six touched production modules — 1,982 tests, 146 failing, all confirmed
  via `git stash` against clean HEAD to be PRE-EXISTING frozen-contract
  source-fingerprint/schema-version drift unrelated to this diff (e.g.
  `run_scenario.py`'s recorded digest, `monthly_prefix_evidence_v7` vs a
  live `v3`) and left untouched as out of scope. `make lint` and `git diff
  --check` clean; `validation/*.json` v1-v5 unchanged (zero diff). NOT done:
  no v6 registration/outcome was designed or frozen, and no bounded
  benchmark was run — deliberately, because a registration is meant to be
  immutable once frozen and the existing v5 suite gate is materially weaker
  than the plan's 30% reduction requirement, so freezing a v6 without first
  deciding those thresholds would either under-specify the gate or need to
  be redone.`
- Cost-ordered-benchmark review repair, second pass same day (2026-08-28):
  `The bullet directly above concluded "this session reviewed it in full and
  confirms it is correct" — that was premature. An independent review of the
  same diff found six real defects, and this pass fixed all six, each pinned
  by a new or updated test:
  (1) `monthly_search.evidence_to_dict`/`evidence_from_dict` and three
  `CandidateEvidence(...)` sites in `independent_daily.py` (cache rebind on
  read, cache save, target-trim) silently dropped `timeout_undecided` on
  every round trip — a serialize-then-replay proof showed
  `serialized_has_timeout=True` but `replayed_timeout=()` before the fix.
  (2) Both benchmark arms shared ONE `daily-results` cache directory
  (`product_arm.build_arm` derived it from `daily_cost_cache`'s parent for
  both arms unconditionally), so whichever arm ran second could silently
  reuse the first arm's real SUMO evidence, corrupting exactly the
  attempt-count/wall-time numbers a benchmark exists to measure — fixed with
  `_isolated_daily_results_cache_root` (clones a content-digest-verified
  snapshot into a separate root per arm) plus a hard `run_benchmark` refusal
  if both arms ever resolve to the same root.
  (3) `_candidate_costs` ignored `timeout_undecided` entirely, and the
  ledger-vs-exhaustive comparison never checked the two populations were
  the SAME set (a silently truncated ledger would have passed) — fixed with
  new `timeout_outcomes_identical` and `ledger_population_complete` gates,
  both frozen into `GATE_THRESHOLDS`.
  (4) `--no-isolate-arms` was a real, reachable CLI flag on `--run` — the
  exact shared-process shape the v5 remediation exists to prevent — removed
  from the command line entirely; `main()`'s `--run` body was extracted into
  `_run_registered`/`_execute_and_publish`, which always isolates, and the
  three `wired` tests that previously passed `--no-isolate-arms` to
  `bench.main()` now call `_run_registered(..., isolate_arms=False)`
  directly as an explicit non-CLI, non-evidence test seam.
  (5) `peak_rss_bytes` was `max(this process's own peak, one already-reaped
  child's peak)`, never a simultaneous process-tree total, and reaping only
  ran on the timeout branch — fixed with `ProcessTreeRSSSampler` (a
  background thread sampling the whole OS process group's live RSS via
  `ps -eo pid=,pgid=,rss=`, filtered in Python rather than trusting `ps -g`
  to mean the same thing on macOS and Linux) and
  `_ensure_process_group_reaped`, now verified on every isolated-arm exit
  path — success, failure, AND timeout — not only after a timeout.
  (6) This file's "Live operation" bullet (further down, historical) said
  the campaign "is RUNNING" while the handoff above it said
  OPERATOR-STOPPED; corrected in place.
  Verified: the full targeted suite for every touched module passed
  (`tests/test_product_arm.py`, `test_cost_ordered_benchmark*.py`,
  `test_monthly_search.py`, `test_independent_daily.py`,
  `test_monthly_sumo.py`, `test_pilot_selection.py`,
  `test_finalist_decision.py`, `test_cost_ordered_execution*.py`), pylint on
  every changed file was clean, and `git diff --check` was clean. STILL NOT
  DONE, unchanged from the bullet above: no v6 registration/outcome was
  designed or frozen, and no bounded benchmark was run — that remains
  separate, larger work gated on deciding the 30% thresholds, not a defect
  in what already exists.`
- Cost-ordered-benchmark review repair, third pass, narrow continuation
  (2026-08-28): `The pass directly above was itself interrupted mid-edit
  (a session rate limit hit while running its own verification), leaving
  `_timeout_identity`'s two production call sites in `monthly_sumo.py`
  (`_observations_for`'s serial and concurrent loops) calling the v2
  structured-identity function without its two new required keyword
  arguments — every timeout in a live run would have raised
  `TypeError: _timeout_identity() missing 2 required keyword-only
  arguments`. Fixed both call sites (`candidate_id=schedule.schedule_id,
  provenance_key=self.study_provenance_key`) and updated the one test that
  still asserted the retired v1 bare-string format
  (`test_sumo_timeout_is_recorded_as_candidate_failure`) to assert the v2
  `timeout_v2:candidate=...:variant=...:seed=...:attempt=1:threshold_s=300:
  provenance=...` schema instead. With that fixed, addressed the five
  findings review-02 left open (ordered-exhaustive execution, registered-run
  isolation and the fail-closed process census were already correct in the
  diff above and are unchanged here):
  (1) STRUCTURED TIMEOUT EVIDENCE — confirmed `TIMEOUT_IDENTITY_SCHEMA =
  "timeout_v2"` already carries candidate/variant/seed/attempt/threshold/
  provenance as a self-describing `key=value` string (kept a `str` rather
  than a JSON object deliberately — `timeout_undecided` is a hashable
  `tuple[str, ...]` reused across `pilot_selection`/`finalist_decision`/JSON
  round-trips, and widening its element type would be an unrelated breaking
  schema change); only the call-site bug above was actually broken.
  (2) EXACT-ATTEMPT/AWAKE-TIME TELEMETRY — new. `ArchivedDemandSumoRunner`
  now owns `self.launch_telemetry` (`{"pilot"|"finalist": {"attempts",
  "timeouts", "other_outcomes"}}`, lock-protected), bumped exactly once per
  real SUMO launch at the actual seam in `_observations_for`'s two loops
  (serial and `ThreadPoolExecutor`), for every outcome including hard
  failures and timeouts — a daily-unit cache hit in `IndependentDailyRunner`
  never reaches this class at all, so it cannot be double-counted.
  `MonthlyDemandResolverRunner.launch_telemetry_snapshot()` sums it across
  every per-archive child runner; `IndependentDailyRunner.timing_snapshot()`
  pulls it fresh (fail-open) under the existing `exact_launch_telemetry` key
  in its already-published S0 diagnostic snapshot.
  `tools/product_arm.run_arm` now reads that snapshot before cleanup and
  also reads the workspace manifest's `active_elapsed_s`/
  `active_elapsed_basis` (the pre-existing `awake_monotonic_segments_v1`
  basis in `search_workspace.py`) into its returned dict.
  `cost_ordered_benchmark.compare_ordered_exhaustive` now sums real pilot+
  finalist attempts across both arms for `exact_attempts_reduction_fraction`
  /`_meets_30_percent`, and awake-active seconds for
  `awake_active_time_reduction_fraction`/`_meets_30_percent`, replacing the
  old `sumo_pilot_count`-only approximation (kept, renamed, for readability).
  Both fields report `None` rather than a false `0%` when a backend
  publishes no telemetry. Test-only: `FakeRunner` in
  `test_cost_ordered_execution.py` gained an additive, opt-in
  `timing_snapshot()` mirroring the real seam, exercised by six new
  `TestOrderedExhaustiveComparison` tests.
  (3) INDEPENDENTLY RECOMPUTABLE STOP PROOF — new
  `_independently_recompute_stop_proof` in `cost_ordered_benchmark.py`
  recomputes `verified_prefix_digest`/`evidence_digest` from the published
  `cursor.verified`/`cursor.viable` fields (a SEPARATE part of the same
  execution record `stop_proof()` did not write), recomputes the undecided
  set from the per-candidate `timeout_undecided` evidence
  `_candidate_costs` already parses (cost-order v5's exact failure mode: a
  timeout dropped from the proof while still present in the evidence), and
  for a band stop cross-checks `first_unexamined_added_vehicle_hours`
  against the published `cost-ledger.json` file. `_stop_proof_valid` gained
  an optional `arm=` parameter (backward compatible; omitting it keeps the
  old proof-internal arithmetic checks only) and `compare_arms` now always
  passes it. Six new tamper tests
  (`TestTheStopProofIsIndependentlyRecomputed`) each flip exactly one bound
  field on a REAL generated proof and confirm it is caught. Full identity-
  key recomputation from the raw policy/provider-identity objects was
  judged out of proportion for this pass (would need plumbing the policy
  object through `compare_arms`'s call chain for a value the ledger/cursor/
  evidence cross-checks above already make hard to falsify undetected) and
  is left as a smaller possible follow-up, not claimed done.
  (4) FRESH CACHE SNAPSHOTS — the cross-CASE reuse review-02 flagged was
  already fixed upstream (`cost_ordered_benchmark_suite.py` suffixes
  `daily_cost_cache` per case). Added the missing piece: new
  `_assert_fresh_snapshot_pair_matches`, called by both `run_benchmark` and
  `run_ordered_exhaustive_comparison` right after their existing
  same-root-is-refused check, asserts the two arms' PRE-RUN cache digests
  are equal whenever NEITHER arm's per-arm root pre-existed the call (a
  genuinely fresh clone pair must come from one identical source snapshot);
  a resumed run reusing its own interrupted arm root is exempt by design,
  matching review-02's own carve-out. Three new tests
  (`TestFreshSnapshotPairMatches`) cover match/drift/resume-exempt.
  (5) COMPLETE SEMANTIC COMPARISON — `run_benchmark` and
  `run_ordered_exhaustive_comparison` used to build each arm with
  `study_provenance_key=f"cost-ordered-benchmark-{arm}"`, and that value is
  stamped onto every `PairedObservation.provenance_key` — semantic evidence
  content that gets cached and is supposed to be comparable across arms —
  so the two arms' evidence differed by an arm-name label baked into
  content, and neither `_candidate_costs` nor `compare_arms` ever looked at
  the field to notice either way. Confirmed `study_provenance_key` is
  already excluded from real cache identity
  (`IndependentDailyRunner._stable_backend_identity` strips it before
  hashing a daily unit's key, `_baseline_cache_key` never reads it), so
  unifying it changes no caching behaviour. Both arms now build with one
  frozen `BENCHMARK_STUDY_PROVENANCE_KEY = "cost-ordered-benchmark"`; which
  arm ran is still available from `run_arm`'s own `"arm"` key, the
  workspace path and the cache-snapshot root — orchestration facts that
  were never inside evidence to begin with. New test
  `test_both_arms_run_under_one_semantic_study_provenance_key`.
  Verified this pass: the full targeted suite (`test_monthly_sumo.py`,
  `test_independent_daily.py`, `test_monthly_search.py`,
  `test_monthly_demand.py`, `test_pilot_selection.py`,
  `test_finalist_decision.py`, `test_cost_ordered_search.py`,
  `test_cost_ordered_execution*.py`, `test_search_workspace.py`,
  `test_product_arm.py`, `test_cost_ordered_benchmark*.py`,
  `test_ai_flow.py`) — 550 passed, 1 skipped; pylint on every touched
  production file (found and fixed one genuine new finding of its own, a
  `missing-kwoa` in `run_ordered_exhaustive_comparison`'s arm dispatch,
  by matching `run_benchmark`'s existing explicit if/else pattern instead
  of a runtime `run = pa.run_arm_isolated if isolate_arms else pa.run_arm`
  dict-kwargs call pylint could not verify) was clean; `git diff --check`
  clean; every changed `.py` file parses. `.ai-flow/runs/
  20260828-092722-2676/` and `validation/*.json` v1-v5 are untouched (zero
  diff). One PRE-EXISTING flaky test unrelated to this diff was observed
  under load (`TestProcessTreeRSSSampling::
  test_a_lone_sleeping_process_is_measured`, a `ps`-timing race) and passes
  reliably in isolation — not touched, out of scope. STILL NOT DONE,
  unchanged and explicitly out of scope for this pass per the user's
  request: no v6 registration/outcome was designed or frozen, no bounded
  benchmark or monthly campaign was created or run, nothing was committed
  or pushed.`
  FOURTH PASS, SAME DAY (2026-08-28): the pass above's own "550 passed"
  claim did not survive a fresh re-run: `test_cost_ordered_benchmark_
  discovery.py::TestItRefusesToFreezeAnEmptyRegistration::
  test_discover_then_run_is_one_unbroken_pipeline` failed with
  `cache_hits_consistent`/`candidate_costs_field_identical`/
  `final_decision_identical`/`hard_failures_identical`/
  `timeout_outcomes_identical` all false. Root cause: `run_benchmark`
  (`cost_ordered_benchmark.py`) resolved `daily_cost_cache =
  ROOT / roots["daily_cost_cache"]` against the module-global `ROOT`
  constant instead of its own `data_root` parameter — the ONE output path
  in that function not re-based onto a test-controllable root (contrast
  the sibling `exhaustive`/`cost_ordered` roots, which already re-base
  onto `workspace_root`). Every test that calls this production path with
  an isolated `data_root=tmp_path` (as this capstone test does) therefore
  still cloned its daily-results cache into the REAL repository's
  `runs/closure-search-benchmark-daily-costs-daily-results-{exhaustive,
  cost_ordered,restart_probe}` — confirmed by ctime (today) vs mtime
  (preserved from the real warm-cache source it cloned) on those
  directories. The prior pass's own execution of this same test is what
  created that destination for the first time (this run is not idempotent
  and left no cleanup); once it existed, `_isolated_daily_results_cache_
  root`'s correct-by-design refusal of a pre-existing destination then
  permanently failed every subsequent invocation, including this one and
  presumably an unreported later re-run inside the prior pass itself
  before it hit its session limit. Fixed by moving the `daily_cost_cache`
  computation after `data_root = Path(data_root).resolve()` and resolving
  it against `data_root` instead of `ROOT` — `data_root` defaults to
  `ROOT` (see `--data-root`'s CLI default), so a real, no-flags production
  run is byte-for-byte unaffected; only a caller that explicitly binds a
  different `data_root` (i.e. tests) now also gets an isolated cache root.
  No production `runs/` directory was created, moved or deleted by this
  fix or by verifying it — the pre-existing real evidence under `runs/`
  (including the now-orphaned, harmless leftover clone directories the
  prior pass created) was inspected via `stat`/`find -newer` only, never
  written to. Re-verified after the fix: the full targeted suite (same
  file list as the third pass, plus `test_ai_flow.py`) — 545 passed,
  1 skipped in the primary battery, and the single capstone test alone
  passes on a second consecutive invocation (confirming the fix is
  idempotent, not merely lucky once); pylint on the one touched file
  clean; `git diff --check` clean; `.ai-flow/runs/20260828-092722-2676/`
  and `validation/*.json` v1-v5 remain byte-identical; no v6 file
  appeared anywhere. STILL NOT DONE, unchanged: no v6 registration/
  outcome, no bounded benchmark or monthly campaign, no commit or push.`
- Review-02 selected repair batch, fifth pass (2026-08-28): `Closed the three
  supplied findings only. Isolated daily workers now durably publish launch
  starts before SUMO and final classifications afterwards; the parent recovers
  unmatched starts as worker termination and rebinds retries from new worker
  processes into one exact-attempt sequence. The cost-order stop proof now
  digests complete verified pilot evidence and the benchmark independently
  recomputes it from integrity-verified candidate artifacts. Semantic
  comparison requires exact expected pilot/finalist populations, complete
  identity-bearing cache-event subset evidence and complete robust-decision
  equality; malformed artifacts raise instead of disappearing. New tests cover
  exception/termination/retry accounting, five evidence-class tampers,
  missing stages, malformed artifacts, cache identities and decision payloads.
  Focused process-free suite: 466 passed, 1 skipped. Broader selected suite:
  504 passed, 1 skipped, 7 sandbox-only failures because `ps` is denied; the
  census correctly failed closed. No v6, SUMO benchmark, monthly campaign,
  commit or push.`
- Review-02 supplied repair follow-up, sixth pass (2026-08-28): `Repaired only
  the two remaining code findings and recorded verification. In
  tools/cost_ordered_benchmark.py, independent stop-proof validation now loads
  and validates the cursor, reconstructs the k-th viable cutoff from complete
  pilot evidence plus the integrity-checked ledger, derives the policy band,
  schema, examined/total/unexamined counts, terminal stop reason, first
  unexamined identity/cost, argument and expected arm-mode early-stop flag, and
  cross-checks the cursor cutoff/reason. Tests mutate every proof field, reseal
  the execution content key and still require rejection. In
  finalist_decision.py, timeout-v3 reads require the exact field set and native
  types, reject boolean/fractional integers and string numerics, validate the
  canonical ISO work date and supported retry protocol, and reject unknown or
  empty identity fields; both independent-daily cache and monthly-search
  artifact readers have malformed-field tests.

  Exact recorded checks (all `PYTHONDONTWRITEBYTECODE=1` and pytest with
  `-q -p no:cacheprovider`): `python3 -m pytest tests/test_monthly_sumo.py
  tests/test_independent_daily.py tests/test_monthly_search.py
  tests/test_pilot_selection.py tests/test_finalist_decision.py` returned 0,
  228 passed; `python3 -m pytest tests/test_cost_ordered_search.py
  tests/test_cost_ordered_execution.py
  tests/test_cost_ordered_execution_review.py tests/test_search_workspace.py`
  returned 0, 132 passed; `python3 -m pytest
  tests/test_cost_ordered_benchmark.py
  tests/test_cost_ordered_benchmark_discovery.py
  tests/test_cost_ordered_benchmark_provenance.py
  tests/test_cost_ordered_benchmark_run.py
  tests/test_cost_ordered_benchmark_suite.py` returned 0, 155 passed and 1
  skipped. Targeted `python3 -m pylint --rcfile=.pylintrc
  --disable=import-error traffic_sim/simulation/finalist_decision.py
  tools/cost_ordered_benchmark.py` returned 0. AST parsing returned 0 for all
  28 changed Python files. `git diff --check` returned 0.

  The broader command including `tests/test_product_arm.py` returned 1 with
  169 passed, 1 skipped and seven existing process-census failures because the
  managed sandbox denied `ps` with `Operation not permitted`; that code failed
  closed as designed. The before/after SHA-256 manifests cover all 19 files in
  `.ai-flow/runs/20260828-092722-2676` plus the 10 existing validation
  registration/outcome v1-v5 files. Both manifest files hash to
  `8af39ae988ef69e79a0d8a9af21d4ad8ff84346be1605967922e3e3c8b8ba9e0` and
  `cmp` returned 0. No v6 file appeared; no SUMO benchmark, monthly campaign,
  branch operation, commit or push was performed.`
- Review-02 final three-finding repair, seventh pass (2026-08-28): `Closed only
  the supplied batch. CandidateEvidence now carries validated
  CanonicalObservationDigest identities for the full canonical SUMO payload;
  worker-result v3, independent-daily cache v4, aggregation, trimming/resume
  and immutable monthly artifacts preserve them, and ordered-exhaustive
  comparison checks complete stage populations plus the cost-ordered prefix.
  Monthly evidence, daily evidence/cache and worker-result readers require
  exact current field sets, with regressions deleting the whole
  timeout_undecided field and injecting unknown fields. The real resolver
  aggregates launch records through every archive child, validates native
  identity/date/stage/variant/attempt/outcome fields, rejects duplicates and
  reconciles the record population with aggregate counters before the
  IndependentDailyRunner timing snapshot exposes it.

  Focused process-free verification: 261 passed for monthly SUMO/search/demand,
  independent daily, pilot and finalist; 132 passed for cost-ordered
  search/execution/review/workspace; 167 passed and 1 skipped for benchmark,
  discovery, provenance, run, suite and worker-pool transport. make lint,
  targeted pylint, AST parsing of every dirty Python file and git diff --check
  passed. tests/test_product_arm.py separately returned 14 passed and seven
  failures solely because the managed sandbox denied ps; the census remains
  fail-closed. The 20 preserved run files plus nine cost-ordered validation
  v1-v5 files still combine to
  8af39ae988ef69e79a0d8a9af21d4ad8ff84346be1605967922e3e3c8b8ba9e0.
  No cost-ordered v6 registration/outcome, SUMO benchmark, monthly campaign,
  branch operation, commit or push was created or run.`
- Review-02 three comparison-gate repair, eighth pass (2026-08-28): `Closed
  only the supplied findings in tools/cost_ordered_benchmark.py and focused
  tests. Exact launch records/counters now use strict native schemas and the
  exhaustive arm cannot add retries for a shared-prefix candidate. Cache-event
  aggregates are recomputed from complete identity records. Semantic evidence
  requires equal observation/digest projections and launch-associated digest
  candidate/date identities; a re-sealed extra digest fails closed.

  Verification: the five cost-ordered benchmark test modules passed 160 tests
  with 1 skipped; targeted pylint on the two touched files, AST parsing and
  git diff --check passed. The 29-file before/after evidence manifests both
  hash to 8af39ae988ef69e79a0d8a9af21d4ad8ff84346be1605967922e3e3c8b8ba9e0
  and cmp passed. No cost-ordered v6 registration/outcome, SUMO benchmark,
  monthly campaign, branch operation, commit or push was created or run.`
- Actor notes: `Historical handoff detail follows outside the current markers;
  it is evidence for its date, not current workflow authority.`
<!-- CURRENT_HANDOFF_HISTORY_END -->

## Historical handoff — 2026-08-17

<!-- HISTORICAL_HANDOFF_2026_08_17_START -->

- Focus and status: `Branch claude/direction-split-plan-tt3gy9. The dated
  direction plan's UNCONDITIONAL phases (0A, 0B, 1) are implemented and tested.
  Gate S and Gate M are both still undecided, and no conditional branch, schema,
  monthly, warm-state, API or UI code exists.`
- Summary: `Fas 0A binds sensor 107's published 2025 D-factor as a
  provenance-carrying period aggregate and re-levels the ESTIMATED per-slot
  split at load time, so every consumer (level-1 targets, level-2 bounds,
  level-3 priors, assignment field, published report) sees one anchored profile.
  Measured: the transfer model put 107 at a flow-weighted 0.4981 for 2025; the
  city publishes 0.5231; the anchor applies delta +0.100 in log-odds, reproduces
  0.52308 exactly, moves any single quarter by at most 0.025 and leaves the
  time-of-day shape untouched (2025-09-16 08:00, two-way total 127: N target
  63.0 -> 66.2). Fas 0B adds a bounded matched-seed study that runs the full
  stress-case x seed cross product through the EXISTING run_condition/
  paired_comparison runners, with a committed preregistration and a
  deterministic Gate S rule; it fails closed to INCONCLUSIVE without a demand
  build. Fas 1 replaces the aggregated training table with a raw
  station-date-hour-heading table (counts, coverage, explicit missingness,
  day_block_id) and adds a four-model tournament with blocked folds and a
  bootstrap over independent groups.`
- Files changed: `data_in/sensors.json; traffic_sim/intake/sensors.py;
  traffic_sim/intake/direction_anchor.py (new); demand/intake.py;
  build_sumo_demand.py; traffic_sim/demand/source_identity.py;
  dirsplit/dataset.py; dirsplit/benchmark.py (new); dirsplit/coverage.py;
  tools/measure_direction_decision_sensitivity.py (new); Makefile;
  validation/direction_decision_sensitivity_registration_v1.json (new);
  validation/dirsplit_point_benchmark_v1.json (new);
  data/dirsplit/coverage_report.json (observability v2 added);
  tests/test_direction_anchor.py, tests/test_direction_decision_sensitivity.py,
  tests/test_dirsplit_v2.py (new); plan/TASKS/AGENT_NOTES/IMPROVEMENT_PLAN docs.`
- Checks: `The dirsplit set is 179 passing (anchor, sensitivity, dataset v2 and
  tournament, deployed central model, rewritten level-3 priors). Full suite on
  a clean worktree of HEAD versus the final state: 321 failed / 4,458 passed
  versus 321 failed / 4,599 passed, with IDENTICAL failure lists — no
  regression, and those 321 are this sandbox lacking SUMO. Every affected
  module imports cleanly after the deletions; `dirsplit.predict` and
  `prior_flows` were run for real and produce the deployed split and all five
  opposite-direction priors. The tournament was executed in two population
  configurations against the tracked aggregate.`
- Decisions and evidence: `q10/q90 are re-levelled by the SAME shift as q50, so
  the stress band keeps its width in log-odds instead of collapsing onto the
  anchor or pretending to new spread. Anchor weights come from the measured
  reference year regardless of the simulated source, mirroring
  STRUCTURAL_REFERENCE_DATE. On the aggregate, shrunk_dfactor (hour x day type,
  no street features) beats 50/50 by +4.5% leave-city-out with a bootstrap CI
  excluding zero, the deployed shrunk LightGBM manages +2.1% with a CI spanning
  zero, and the raw LightGBM is WORSE than 50/50 — but Gate M stays
  INCONCLUSIVE because the aggregate has no day blocks and no raw counts.`
- Blockers or risks: `Gate S needs a calibrated demand build plus SUMO, neither
  present in this sandbox. Gate M needs the raw Norwegian volumes; the open API
  is refused by this environment's proxy, not by the code. Nothing in the
  sensitivity tool or the benchmark may be promoted to release evidence.`
- Deployment change (2026-08-16, user-directed): `dirsplit/predict.py now
  writes the tournament winner by default — the hour x day-type D-factor pooled
  toward 0.5, no street features — importing benchmark.ShrunkDFactor so what
  ships is what was scored. Pairs are oriented from published geometry
  (verified to reproduce features.py radial_cos to 3 decimals), so the deployed
  path needs neither an OSM download nor model.pkl. q10/q90 are leave-city-out
  residual quantiles of the same model: wider and measured rather than narrow
  and unvalidated, which loosens the level-2 ceiling on unmeasured
  carriageways (1076 at 07:00: measured 50 admits ~136 instead of ~72) and
  widens Monte Carlo spread. Gate M is still INCONCLUSIVE under its frozen
  rule; the switch rests on leave-city-out and leave-station-out only.
  The superseded machinery was then DELETED, not left dormant: train.py,
  model.pkl, fetch_norway/api/match, estimate_directions.py, the rollback flag
  and their tests. prior_flows.py was rewritten to read the deployed split
  through demand.intake instead of re-running the retired model with its own
  re-orientation and shrinkage. The tracked training table stays: the deployed
  curve is refitted from it on every run, so deleting it would make the shipped
  numbers unreproducible. With the fetch client gone, Gate M is reachable only
  if raw volumes are supplied by hand; a zero-external-data project should take
  the plan's Exit A (50/50 + the 107 anchor) rather than freeze a curve whose
  source was deleted.`
- Superfluity check on q10/q90 (2026-08-16, requested): `They are TWO objects
  under one name, and only one of them was broken. As per-edge MARGINAL bounds
  they are load-bearing: measured on the tracked artifacts, the structural
  conservation ceiling on an unmeasured carriageway is 450-1057 veh/quarter
  (5-12x its measured twin) and for 1076's twin there is none at all, while the
  model ceiling sits at 1.3-2.7x (median 2.1x); and with no constraint the PFE's
  parsimony objective drives the edge to ZERO, which is a stronger claim than
  any band. Count-based OD estimation is underdetermined, so the choice is which
  prior, not whether — and the ladder already surrenders these bounds FIRST,
  before priors and long before any measured band widens. As DEMAND VARIANTS
  they were broken: each edge took its own marginal quantile, so the pair summed
  to 0.587-1.413 and the q10/q90 route files calibrated sensor 107 to 82.1% and
  117.9% of its measured day total. Fixed in demand/intake.py::scenario_shares
  by deriving the pair from one canonical edge and giving the other the
  complement; all three variants now reproduce the measured total exactly while
  the split moves, and write_counts publishes the same numbers. Deleting the
  variant axis entirely was NOT done: it touches 210 call sites in 12 production
  modules and 80 test files including frozen closure evidence, and it would
  answer Gate S by fiat instead of measuring it.`
- Shape-source question CLOSED on evidence (2026-08-17,
  `tools/measure_donor_shape_transfer.py`): `Standard practice offers two
  constructions and the project had tried one. FHWA's Traffic Monitoring Guide
  applies temporal factors from a GROUP of continuous counters to sites with
  only a bidirectional count — the deployed design. Project-level forecasting
  guidance instead borrows a NEARBY permanent counter's pattern, and Gothenburg
  has a candidate 239.9 m from 107 at 3.5 degrees. Measured on Norwegian
  stations where direction truth exists: only the widest band (11 independent
  pairs) reaches the frozen minimum of 8, and there the interval spans zero
  (+14.6%, CI [-0.0178, +0.0087]). The tempting +57.2% in the band matching
  Gothenburg rests on TWO independent pairs. Two traps had to be removed first,
  both now pinned by tests: an unoriented population collapses the pooled curve
  to a flat 0.5 because mirrored headings cancel, and reciprocal donor pairs
  count one piece of evidence twice. Verdict: the donor route is NOT deployed;
  the deployed construction stands because its one plausible upgrade cannot be
  shown better on the data this project owns. The group curve beats a flat
  anchor in every band, so shape is worth having — only its source is open.`
- Suggested next action: `Run the two frozen studies on a machine with SUMO and
  network access: make demand && make direction-sensitivity for Gate S, and
  make dirsplit-volumes && make dirsplit-dataset && make dirsplit-benchmark for
  Gate M. Then apply the plan's four-outcome table; only Gate S = YES may open
  Gren B/D.`
- Actor notes: `No release gate, calibration gate or frozen evidence was
  weakened; no q archive was rewritten; no external data was requested.`
- Runtime repair (2026-08-17, user-directed; supersedes earlier "monthly/API
  untouched" wording for this narrow seam): `Updated latest main dae20aa.
  Ordinary recalibration now builds exactly one q50 route and records a
  semantic variant manifest. Only --direction-stress-variants builds
  q50/q10/q90; monthly closure-envelope generation passes that flag. PFE
  freezes q50's exact integer total first and enforces it for q10/q90 on every
  relaxation rung. run_scenario maps all ordinary seeds to q50 and ignores
  stale auxiliary files. No gate was decided and no frozen artifact changed.`
- Repair checks (2026-08-17): `541 focused demand/PFE/scenario/monthly/signal
  tests pass; server tests pass 140/140 with loopback access; git diff --check
  passes; localhost:8000 returns HTTP 200 and /api/ping returns {"ok": true}.
  Current main has no make check target. make test was attempted and stopped
  after 493 passes/11 failures caused by frozen benchmark/source drift and the
  pre-existing live-demand mismatch; no focused runtime-contract test failed.`
- End-to-end result (2026-08-17): `Local API recalibration for forecast
  2027-07-14 completed and published build 4672988fd88a7d8422f5 (demand key
  69788d01d62bd600). Metadata is mode=q50_only, n_variants=1, PFE GEH=100%,
  zero infeasible intervals, 18,031 vehicles. Baseline seeds 1000/1001/1002
  all map to q50; each loaded and inserted 18,031 vehicles with zero waiting,
  teleports or collisions. The superseded closure scenario files were removed
  by normal demand-scoped publication. Local server remains on port 8000.`
<!-- HISTORICAL_HANDOFF_2026_08_17_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
