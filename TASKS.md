# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Make the exact five-day monthly search robust for full-day
  comparisons, including fail-fast candidate feasibility and actionable
  demand-build errors. Keep the adopted canonical weekday/weekend route
  catalog stable, preserve exact scenario/trajectory/seed-health evidence and
  keep full annual warming disabled until explicitly launched.`
- Status: `CATALOG QUALIFIED, ADOPTED AND SOAKED. A matched-size campaign used
  the same 6,000-candidate request in both arms for 30 counterbalanced pairs.
  Median cold demand preparation fell 55.246→24.715 s (2.235x ratio of arm
  medians; 2.220x median paired speedup), every day
  class was faster, adapter p95 was 0.678 s and all hard
  correctness/resource gates passed. Seven catalog fixtures plus an explicit
  legacy rollback passed soak. The active 2027-11-11 release uses the verified
  weekday key, has 21,744 vehicles and matches all 672 integer sensor targets
  exactly. A refreshed annual plan/preflight passed and exactly one q50 warm
  state was produced and verified; the remaining 104,684 units were not run.
  Stress seeds map q10/q50/q90, widened PFE rungs cannot publish non-exact
  sensor counts, and mixed catalogs namespace tour IDs. Synthetic 50-sensor
  exact-projection and output gates pass, while real calibrated 50-station
  evidence remains open. The broader
  performance implementation remains in progress. The named external monthly search
  ui-monthly-euc9qp was stopped at 476/1,776 and its workspace remains
  resumable. Monthly resource-policy v2 now passes the recorded eight daily
  workers explicitly, logs awake monotonic active time across resumes, holds a
  macOS sleep assertion while the CLI owns the workspace, and supports an exact
  workday count through min=max without changing legacy default identities. On
  the stopped search's frozen input, exact five-day intent reduces the exact
  preflight from 1,776 periods/2,224 units to 780 periods/1,040 units. This is
  workload removal and instrumentation, not yet a measured throughput result.
  A new UI five-day search (`ui-monthly-12hg8f3`) exposed that an implicitly
  adopted catalog with a stale weekday identity aborted demand build
  06cc309e4779897c. Implicit catalog use now falls back to the qualified legacy
  builder with explicit metadata; the exact 2027-09-18 three-day build then
  succeeded with 6,048/6,048 exact integer sensor constraints across q50/q10/q90,
  zero infeasible intervals and passing candidate provenance. The active
  2027-11-11 live demand products were restored byte-exact afterwards.
  A later retry (`ui-monthly-12hg8f3`) reached the 2027-10-01 q50 projection
  after roughly 95 minutes and failed because sensor edge
  26842525_26355153_0 had no route exclusive to that sensor; q11/q16/q17
  therefore had physically valid targets that the candidate matrix could not
  represent. Candidate generation now installs the minimum legal,
  endpoint-grounded single-sensor incidence basis after normal filters and
  fails before PFE if any edge remains unsupported. The frozen failed pool was
  repaired with one route (6,030→6,031 route×purpose shapes), and exact integer
  publication passed q11/q16/q17. PFE errors now name date, variant, quarter
  and clock interval. The failed search is idle/error with its workspace
  retained. Replacement campaign `ui-monthly-13lhsoy-5d` ran with the explicit
  8x1 worker policy and is now OPERATOR-STOPPED (2026-08-27, SIGINT to process
  group 68201 at the user's request, after a 16/16 identity audit); it is
  resumable and has NOT been restarted. Its durable job record is
  `status: error` carrying an `operator_stop` block, while its workspace
  manifest still reads `running`/`completed: 0` because the shutdown path
  reset that pointer; neither is the resume authority - the content-addressed
  cache is, and it holds 1 083 of 1 950 units valid, 0 corrupt, 867 missing.
  The measured 8x1 policy never achieved eight: 0.905 achieved width, one busy
  worker against eight slots, because batching was parent-local. An earlier
  2026-08-27 test run rewrote the durable ledger record to `orphaned_running`;
  the then-live PID/PGID and owning server were reverified, the record was
  restored with an audit trail, and tests now isolate both `JOBS_DIR` and
  recovery-gate globals.
  Current server wiring already runs three interactive seeds in
  parallel (measured baseline 11.0 -> 5.9 s and closure 21.6 -> 13.9 s,
  byte-identical apart from generated_at). A clean 10-trial active-demand run
  pins first-new closure p95 at 10.496 s: all outputs and 30 seed-health
  records match. The user accepted that performance on 2026-08-24, then
  explicitly reopened the faster-closure goal on 2026-08-25. On the current
  2027-11-11 demand, single-write atomic JSON publication reduced a new
  ten-trial baseline/candidate comparison from p50 10.654 to 10.212 s and p95
  10.765 to 10.359 s, with identical scenario/trajectory digests and all 60
  seed-health records clean. The <=10 s p95 target remains open by 0.359 s. Exact-repeat
  structured cache p95 is 0.329 s over 10/10 verified hits with zero SUMO work,
  safely inside its <=2 s goal. Exact structured closure caching, result-neutral daily timing,
  opt-in routing controls and an active-slot guard are implemented. The
  qualified entered/timeLoss-only edgeData output is now the production
  default, with an isolated full-field rollback flag. Deterministic disruption
  analysis is split from publication and uses exact grouped/sparse shortest
  paths: the active process-free case fell from 4.1364 s to 1.051 s while
  matching the retained oracle. A per-content-key cross-process single-flight
  lock now prevents duplicate matched-baseline producers. Two
  live-shaped reusable-Python-worker single draws preserved exact evidence but
  measured 1.027x and 0.998x; they are insufficient to accept or reject the
  pool. The replacement harness requires at least four counterbalanced paired
  trials and the generic pool remains inactive. The current optimized closure
  profile has median SUMO 6.825 s, disruption 1.197 s, trajectory publication
  0.737 s and scenario-artifact publication 0.025 s.
  Remaining performance targets are first-new closure p95 <=10 s, exact-repeat p95 <=2 s, async ack p95 <=1 s
  and >=2.0x monthly verified-unit throughput without semantic or evidence
  changes. These targets now explicitly cover 50 physical stations and the
  larger calibrated population their evidence may require. Fixed-load 50-row
  validation measured 3.325 ms p95 and is not the closure bottleneck; a
  vehicle-load diagnostic completed 42,816 vehicles at 1.57x the 21,408 wall
  time, while an 85,632 arm collapsed with 28,977 vehicles waiting. Minimal
  production-field edgeData passed its 40-run/120-seed paired semantic and
  health gate, cutting baseline wall 16.4% and closure wall 7.1%, and is now
  adopted. The active demand/release and unresolved Gate S/M state remain as
  recorded in IMPROVEMENT_PLAN.md. The large-simulation target structure and
  staged evidence requirements are documented in
  LARGE_SIMULATION_FUNCTION_STRUCTURE_2026-08-23.md.
  COST-ORDERED BENCHMARK TIMEOUT/ISOLATION CONTINUATION (2026-08-28): a prior
  worker session was interrupted mid-way through adding a versioned SUMO
  timeout/undecided model and isolated-process arm execution to the
  cost-ordered-vs-exhaustive monthly benchmark (`traffic_sim/simulation/
  monthly_sumo.py`, `independent_daily.py`, `finalist_decision.py`,
  `pilot_selection.py`, `cost_ordered_execution.py`, `tools/product_arm.py`,
  `tools/cost_ordered_benchmark.py`), leaving the change dirty and untested.
  This session reviewed it end to end and confirms it is correct: a SUMO
  timeout is recorded as a structured `timeout_undecided` identity
  (`variant:seed:attempt1:threshold300s`), is folded into `hard_failures` for
  old readers, and forces `pilot_selection.select_pilot_finalists` /
  `finalist_decision.decide_finalists` to return `status: "inconclusive"`
  rather than silently treating the timed-out run as excluded or absent —
  exactly the v5 defect (arm-order-dependent timeout classification) this
  work exists to close. Each benchmark arm now runs in its own process and
  process group (`product_arm.run_arm_isolated`, `start_new_session=True`,
  bounded TERM-then-KILL reaping) at a frozen one-daily-worker/one-seed-
  worker/one-SUMO-slot shape, with an explicit `--no-isolate-arms`
  development-only escape hatch that a registered run must never pass.
  TWO REAL DEFECTS FOUND AND FIXED, both pinned by tests: (1)
  `bench.main`'s `--run` path now defaults `isolate_arms=True`, so the three
  pre-existing `tests/test_cost_ordered_benchmark_run.py` tests that drove it
  through `bench.main()` with the `wired` in-process `build_arm` monkeypatch
  were silently exercising the real-subprocess failure path instead of the
  intended fake-arm success path (the monkeypatch cannot cross a
  `subprocess.Popen` boundary) — one of the three actually asserted `code ==
  0` and failed outright; fixed by passing `--no-isolate-arms` in all three,
  matching the pattern already used by this file's other `wired`-based
  tests. (2) `tools/cost_ordered_benchmark_suite.py`'s `run_suite` never
  varied which arm starts first across suite cases, so the isolation-
  hardening plan's "counterbalance arm order across cases" requirement was
  unmet even though `run_benchmark` already supports a `counterbalance` flag
  — fixed by alternating `counterbalance=bool(index % 2)` per case, with a
  new monkeypatched-`run_benchmark` unit test
  (`test_run_suite_counterbalances_arm_order_across_cases`) since no test
  previously exercised `run_suite`'s execution path at all.
  VERIFIED CLEAN: every planner-suggested focused suite plus the full
  dependent-module surface found by grepping test imports of the six
  touched production modules (annual warm plan/population, benchmark
  closure-search scaling, closure calendar/ledgers/preflight/teleport,
  deterministic disruption, held-out v5/v6 freeze, independent daily queue,
  independent-vs-continuous harness, monthly demand/v2 benchmark, all ten
  monthly-warm-state freeze generations, period comparison, populate annual
  warming, search import cost, serve, unit budget, warm state boundary and
  population semantics) — 1,982 tests total, 146 failing. Confirmed by `git
  stash` and rerunning a representative sample against clean HEAD that all
  146 are PRE-EXISTING, unrelated to this session's or the interrupted
  worker's diff: they are frozen-contract tests whose recorded
  `source_fingerprints`/schema-version numbers (e.g. `run_scenario.py`'s
  digest, `monthly_prefix_evidence_v7` vs a live `v3`) have drifted from the
  live tree for reasons predating this session. Left untouched as out of
  scope. `make lint` and `git diff --check` are clean; `validation/*.json`
  v1-v5 have zero diff.
  NOT DONE, and deliberately not attempted this session: no new v6
  registration/outcome pair was designed or frozen, and no bounded
  benchmark was executed. The existing outcome-blind multi-case suite
  (`tools/cost_ordered_benchmark_suite.py`, schema
  `cost_ordered_benchmark_suite_registration_v1`, frozen instance
  `validation/cost_ordered_benchmark_registration_v5.json`) already covers
  most of what a "v6" would need — per-case and aggregate equivalence gates,
  SUMO runtime/platform fingerprinting, isolated-arm execution — but its
  `suite_thresholds`/`gate_thresholds` only require `sumo_verifications_
  saved_minimum: 1`, not the plan's `>=30% fewer aggregate new exact
  attempts` and `>=30% lower aggregate awake active wall time` gates, and the
  registration does not yet declare the timeout/retry protocol or bind
  isolated initial-cache snapshots by content. Designing and freezing that
  is real remaining work; it was not rushed here because a benchmark
  registration is meant to be immutable once frozen, and because actually
  running it costs real SUMO wall time this session did not spend without a
  completed, reviewed schema to run it against.`
  COST-ORDERED BENCHMARK REVIEW-02 CONTINUATION, THIRD PASS (2026-08-28): the
  second pass above (see AGENT_NOTES.md's matching entry) was itself
  interrupted by a session limit while finishing a CHANGES_REQUIRED review's
  fixes, leaving `monthly_sumo._timeout_identity`'s two production call sites
  calling the function without its new required `candidate_id`/
  `provenance_key` keywords — a live timeout would have raised `TypeError`.
  Fixed both call sites and the one test still asserting the retired v1
  string format. Then implemented the five review-02 findings review-fix-01
  had not yet reached (ordered-exhaustive execution, registered-run
  isolation and the fail-closed process census were already correct):
  exact SUMO-launch telemetry at the real `ArchivedDemandSumoRunner.
  _observations_for` launch seam, propagated through
  `MonthlyDemandResolverRunner`/`IndependentDailyRunner`/`product_arm.run_arm`
  into `compare_ordered_exhaustive`'s `exact_attempts_reduction_fraction`
  and `awake_active_time_reduction_fraction` 30% gates (replacing the old
  pilot-candidate-count approximation); independent recomputation of the
  cost-ordered stop proof's bound fields
  (`verified_prefix_digest`/`evidence_digest`/undecided set/first-unexamined
  cost) against the published cursor, per-candidate evidence and cost-ledger
  artifacts rather than the proof's own self-reported numbers, with six new
  tamper tests; a fresh-snapshot-pair digest assertion for the two arms'
  daily-results caches (cross-case reuse was already fixed upstream); and one
  shared `BENCHMARK_STUDY_PROVENANCE_KEY` for both compared arms so an
  arm-name label is never baked into semantic evidence content (`study_
  provenance_key` was confirmed already excluded from real cache identity,
  so this changes no caching behaviour). Verified: the full targeted suite —
  550 passed, 1 skipped; pylint on every touched production file clean
  (fixing one genuine new finding along the way, a `missing-kwoa` from a
  dict-kwargs dispatch pattern pylint could not verify); `git diff --check`
  clean; every changed file parses. `.ai-flow/runs/20260828-092722-2676/`
  and `validation/*.json` v1-v5 remain byte-identical. STILL NOT DONE,
  unchanged and explicitly out of scope for this pass: no v6 registration/
  outcome, no bounded benchmark, no monthly campaign, no commit or push.
  FOURTH PASS, SAME DAY (2026-08-28): that "550 passed" did not reproduce on
  a fresh run — `test_cost_ordered_benchmark_discovery.py`'s capstone
  `--preregister`+`--run` pipeline test failed all five semantic gates.
  Cause: `run_benchmark`'s `daily_cost_cache` was resolved against the
  module-global `ROOT` constant, not its own `data_root` parameter (unlike
  the sibling `exhaustive`/`cost_ordered` output roots, which already
  re-base onto `workspace_root`), so a test binding an isolated
  `data_root=tmp_path` still cloned its daily-results cache into the real
  repo's `runs/closure-search-benchmark-daily-costs-daily-results-*` — a
  destination the third pass's own (non-idempotent, uncleaned) run of this
  same test had already created, so every later run permanently collided
  with it once `_isolated_daily_results_cache_root`'s correct pre-existing-
  destination refusal saw it. Fixed by resolving `daily_cost_cache` from
  `data_root` (which still defaults to `ROOT`, so a real no-flags run is
  unaffected) after `data_root` is resolved, instead of from `ROOT`
  directly. No file under the real `runs/` was created, moved or deleted by
  this fix or its verification — confirmed via `stat`/`find -newer` only.
  Re-verified: 545 passed, 1 skipped across the same targeted battery plus
  `test_ai_flow.py`, and the capstone test alone now passes twice in a row
  (idempotent); pylint and `git diff --check` clean; the run directory and
  `validation/*.json` v1-v5 remain byte-identical; no v6 file anywhere.
  STILL NOT DONE, unchanged: no v6 registration/outcome, no bounded
  benchmark, no monthly campaign, no commit or push.
  FIFTH PASS, REVIEW-02 SELECTED REPAIR BATCH (2026-08-28): durable isolated-
  worker launch accounting now writes a start event before SUMO, recovers it
  after exceptions or termination, deduplicates start/final events and rebinds
  retry attempt numbers across fresh subprocesses. Stop proofs now bind the
  full verified pilot evidence population (observations/provenance,
  disruption, failures and structured timeouts), independently recomputed
  from integrity-verified workspace artifacts. Semantic comparison fails on
  malformed artifacts, missing candidate/stage populations, cache-event
  identity differences and any complete robust-decision difference. Focused
  process-free verification: 466 passed, 1 skipped. A broader selected run
  reached 504 passed, 1 skipped; its seven failures were the sandbox denying
  `ps` to the already fail-closed process-census tests. No SUMO benchmark,
  monthly campaign, v6 registration/outcome, commit or push was performed.
  SIXTH PASS, SUPPLIED REVIEW BATCH FOLLOW-UP (2026-08-28): closed the two
  remaining implementation gaps without reopening the wider patch. Stop-proof
  validation now independently derives the k-th viable cutoff, selection band,
  schema, counts, stop reason, cursor cutoff/reason, proof argument and arm-mode
  early-stop flag; a resealed execution cannot make any mutated proof field
  pass. Timeout-v3 reads now require the exact wire field set and native types,
  a canonical ISO date and the supported retry protocol, with malformed-record
  coverage at both cache/artifact readers. Focused process-free suites passed
  515 tests with 1 skipped; targeted pylint, AST parsing of all 28 changed
  Python files and `git diff --check` passed. The 29-file preservation manifests
  before/after both hash to `8af39ae988ef69e79a0d8a9af21d4ad8ff84346be1605967922e3e3c8b8ba9e0`
  and compare byte-identical. The broader product/process suite still has the
  same seven sandbox-only `ps: Operation not permitted` failures (169 passed,
  1 skipped); census failure remains fail-closed. No v6/SUMO/monthly run,
  commit or push was performed.
  SEVENTH PASS, REVIEW-02 FINAL THREE FINDINGS (2026-08-28): persisted a
  validated identity-bearing SHA-256 for every complete canonical SUMO
  observation through worker-result v3, independent-daily cache v4,
  aggregation/resume and pilot/finalist artifacts, and added it to complete
  semantic population/prefix comparison. Current monthly, daily-cache and
  worker-result envelopes now reject missing or unknown fields, including a
  deleted whole timeout population. The production
  MonthlyDemandResolverRunner -> IndependentDailyRunner chain now aggregates
  and validates the complete de-duplicated launch-record population across all
  archive runners and reconciles it with aggregate counters. Process-free
  focused suites passed 560 tests with 1 skipped; make lint, targeted pylint,
  AST parsing and diff checks passed. The separate product-arm suite retained
  only the seven sandbox `ps` denials (14 passed), fail-closed as designed.
  The 29-file preservation digest remains
  `8af39ae988ef69e79a0d8a9af21d4ad8ff84346be1605967922e3e3c8b8ba9e0`.
  No cost-ordered v6, SUMO benchmark, monthly campaign, commit or push was
  produced.
  EIGHTH PASS, REVIEW-02 THREE COMPARISON GATES (2026-08-28): exact launch
  records and aggregate counters now require native, complete schemas; the
  shared verified prefix requires identical attempt populations and permits
  exhaustive extras only after the stop boundary. Daily-result cache counters
  are recomputed from the identity-bearing event multiset. Observation and
  canonical-digest projections must be exactly equal, with every digest's
  candidate/date/variant/seed bound to a real stage launch. New malformed,
  unexplained-retry, aggregate-drift and re-sealed extra-digest regressions
  pass. The five-file benchmark suite passed 160 tests with 1 skipped;
  targeted pylint, AST parsing and `git diff --check` passed. Preserved
  evidence remains byte-identical at the digest above. No cost-ordered v6,
  SUMO benchmark, monthly campaign, commit or push was produced.`
- Standard-pool checkpoint: `The isolated post-picker tool binds date/build,
  route, network and targets; creates three deterministic explicit
  speedFactor arms; preserves all 21,240 vehicles/routes; and reached raw
  672/672 per arm with clean health. Spacing stayed non-bunched (median
  2.7→2.5 s, max one departure/s). It is not active: ten paired concurrent
  baseline trials measured median 1.805→1.846 s (+2.30%), and closure-shaped
  equivalence/performance is unmeasured.`
- Suggested next action: `Keep the byte-exact single-write JSON optimization
  and the passing exact-repeat cache. Campaign `ui-monthly-13lhsoy-5d` is
  operator-stopped, not running; do NOT restart it without a separate explicit
  user decision. When a restart is authorised, enable the global daily-unit
  queue by exporting TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS=8 and
  TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_SCREENING=independent-exhaustive before
  launching, then measure verified units per active hour against the frozen
  0.905 achieved width - the 7.78x figure is synthetic scheduler scaling and
  the 2.93/6.58 h figures are projections, so a real campaign number is still
  missing. Let
  source/provenance identity rebuild any incompatible candidate or day cache;
  do not force reuse, reuse created-at wall time or claim 2.7x before
  measurement.
  If interactive closure work continues, profile a
  bounded SUMO or disruption-analysis arm against the new 10.359 s p95
  reference; require exact digests and clean seed health. Do not launch the
  remaining 104,684 annual warm units or activate
  rejected generic/driver pools without a separate explicit decision.
  For the cost-ordered-benchmark timeout/isolation work: design and freeze a
  v6 suite registration whose gate thresholds actually encode the plan's
  >=30% aggregate-exact-attempts and >=30% aggregate-awake-active-wall-time
  reduction requirements (today's frozen v5 gate only requires
  `sumo_verifications_saved_minimum: 1`), declare the timeout/retry protocol
  and isolated-cache-snapshot bindings in the registration, then run that
  bounded suite once in fresh isolated roots and evaluate GO/NO-GO
  mechanically. Do not run or resume the full monthly campaign or annual
  warming while doing this.`
- Eligible actors: `Any model or person; no model-specific gate`
- Safety boundary: `Preserve user-owned uncommitted source/generated artifacts
  and all frozen q, release and closure evidence. Do not weaken
  calibration, equivalence, provenance, health, survivability, failure-recall,
  regret, resource or held-out gates. Do not couple demand-case identity to
  random seeds, splice cost fields across scenarios, silently exclude roads
  for low observability, or activate policy/UI/global-best claims without the
  applicable frozen evidence. Do not hardcode 107's annual 0.5231 as 96
  measured quarters or present q10/q90 as calibrated probabilities.`
- Updated: `2026-08-28, sixth narrow review-batch pass: strict timeout-v3
  deserialization and complete independent stop-proof field derivation are
  repaired and verified process-free; preserved evidence is byte-identical,
  and no excluded operational action was taken. Previously, a third pass the
  same day: the second pass below was
  interrupted by a session limit mid-fix, leaving two `_timeout_identity`
  call sites broken (missing required keywords) and five of review-02's
  findings unaddressed. Fixed the call sites, then implemented exact
  SUMO-launch/awake-active-time telemetry feeding the benchmark's 30%
  reduction gates, independent recomputation of the cost-ordered stop
  proof against published ledger/cursor/evidence artifacts (with tamper
  tests), a fresh-cache-snapshot-pair digest assertion, and one shared
  semantic study-provenance key for both compared arms. Full details in
  this field's paragraph above and in AGENT_NOTES.md's matching entry. 550
  tests passed (1 skipped), pylint and `git diff --check` clean, frozen
  evidence and the `.ai-flow` run directory untouched. No v6 registration/
  benchmark/campaign was created or run, and nothing was committed.
  Previously updated 2026-08-28, a second pass the same day, after a CHANGES_REQUIRED
  review of the entry directly below found and repaired six real defects in
  the timeout-model/isolation work: (1) `monthly_search.evidence_to_dict`/
  `evidence_from_dict` and three `CandidateEvidence(...)` call sites in
  `independent_daily.py` (cache rebind, cache save, target-trim) silently
  dropped `timeout_undecided` on every round trip — proven fixed with a
  direct serialize/replay test; (2) the benchmark's two arms shared one
  `daily-results` cache directory (derived from `daily_cost_cache`'s parent
  in both), so whichever arm ran second could reuse the first arm's real
  SUMO results — fixed with `_isolated_daily_results_cache_root`, which
  clones a content-verified snapshot into a separate root per arm and
  `run_benchmark` now refuses to proceed if both arms resolve to the same
  root; (3) `_candidate_costs` ignored `timeout_undecided` and the ledger-
  vs-exhaustive comparison never checked that the two populations were the
  SAME set — fixed with a `timeout_outcomes_identical` gate and a
  `ledger_population_complete` gate (both frozen into `GATE_THRESHOLDS`);
  (4) `--no-isolate-arms` was a real CLI flag reachable on a registered run
  — removed from the command line entirely; `main()`'s `--run` body
  (`_run_registered`/`_execute_and_publish`) always isolates, and the three
  tests that relied on the flag now call `_run_registered` directly with
  `isolate_arms=False` as an explicit non-CLI test seam; (5)
  `peak_rss_bytes` was `max(self, one reaped child)`, never a simultaneous
  process-tree total, and reaping only ran on timeout — fixed with
  `ProcessTreeRSSSampler` (background `ps`-based sampling of the whole
  process group) plus `_ensure_process_group_reaped`, now called on every
  isolated-arm exit path, not only the timeout branch; (6) this file's
  sibling `AGENT_NOTES.md` had a stale "is RUNNING" bullet contradicting its
  own current handoff's "OPERATOR-STOPPED" — corrected. All six are covered
  by new/updated tests in `tests/test_product_arm.py`,
  `tests/test_cost_ordered_benchmark.py` and
  `tests/test_cost_ordered_benchmark_run.py`; the full targeted suite for
  every touched module passed, `make lint`-equivalent pylint on the changed
  files was clean, and `git diff --check` was clean. A v6 registration/
  outcome pair and an actual benchmark run remain open — unchanged from the
  entry below, and correctly out of scope for a defect-repair pass.
  Previously updated the same day after reviewing, testing and repairing the
  interrupted cost-ordered-benchmark timeout/isolation continuation
  described above (two real defects found and fixed: an isolate-arms
  default flip that made three existing tests silently exercise the wrong
  path, and a missing cross-case arm-order counterbalance); a v6
  registration/outcome pair and an actual benchmark run remain open.
  Previously updated 2026-08-27 after completing,
  testing and self-reviewing the global
  daily-unit queue: the cache-identity defect was found and fixed (the CLI is
  one of nineteen provenance-bound sources, so its queue flag was removed and
  the file restored byte-exact; activation moved to the non-cache-bound
  environment seam), a retarget deadlock was fixed and pinned by a test that
  hangs on the pre-fix code, and the benchmark sampler was moved to process
  ancestry. The two 2026-08-27 validation reports are unchanged frozen
  evidence; the `cache_bound_source_proof` block in the baseline report is
  wrong about the source count and is superseded in this file. Previously
  updated the same day after isolating the test job ledger, restoring the
  verified live monthly record and explicitly declaring direct SciPy/NetworkX
  dependencies. Previously updated 2026-08-26 after repairing the real
  2027-10-01 candidate-matrix
  infeasibility with a grounded single-sensor basis and verifying all three
  formerly failing quarters; previous 2026-08-25 monthly exact-length, explicit-worker,
  awake-active-time and macOS keep-awake repairs, plus schema-v3 provenance
  repair of the matched-size
  qualification/adoption chain, atomic candidate-cache pruning,
  soak, active-day rebuild, one bounded q50 warm-state pilot and clean-tree
  exact-repeat/first-new closure p95 measurement and user acceptance of the
  current end-to-end UI time. The product UI now hides the
  internal representative seed/variant and distinguishes modelled trip purpose
  from geographic flow categories and measured sensor counts.`
<!-- WORKFLOW_CONTROL_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### PERF-CLOSURE-2026-08-21 — Faster exact closures and monthly simulation

- Status: `IN_PROGRESS — the canonical route catalog is provenance-bound,
  adopted and soaked; production demand preparation now defaults to catalog
  with an explicit tested legacy rollback. Full annual warming remains
  inactive after one successful bounded q50 pilot. Repository and primary-source research is complete;
  the named monthly run is explicitly paused and the phased implementation
  goal is active. Exact structured closure caching, result-neutral daily timing,
  isolated opt-in routing controls and an active-slot guard are implemented.
  Sol review repaired cache identity/single-flight/workspace locking, telemetry
  failure isolation, nested-concurrency admission and external monthly
  lifecycle handling. Production now defaults to entered/timeLoss edgeData;
  phase schema v2 separates disruption, payload construction and artifact
  publication; exact grouped/sparse disruption matches the retained oracle;
  and matched-baseline cache publication is protected by a cross-process
  per-key single-flight lock. The two generic reusable-Python-worker arms are
  single-draw diagnostics (1.027x and 0.998x), not rejection evidence; the new
  counterbalanced multi-trial harness is implemented but not yet run. The prior
  strict single-flight regression now passes. Multi-trial p95 and remaining
  resource-allocation evidence are still open. Monthly resource-policy v2 now
  explicitly runs 8 daily × 1 seed worker within eight slots; exact min=max
  workday intent, resumable awake active-time telemetry and macOS keep-awake
  are implemented. The old 2,224-unit run has not been reclassified as valid
  throughput evidence. The subsequent 2027-10-01 failure is repaired by a
  generator-level exact-incidence basis invariant; the real frozen q11/q16/q17
  projection now passes. Replacement campaign `ui-monthly-13lhsoy-5d` ran
  until 2026-08-27 and is now OPERATOR-STOPPED, not running (see the stop
  record below); its test-damaged durable record has been restored with audit
  fields and regression coverage prevents future live-ledger reconciliation.
  2026-08-27: campaign `ui-monthly-13lhsoy-5d` was STOPPED at the user's
  explicit request for performance optimization, after a 16/16 identity audit
  (PID/PGID leader/PPID==server_pid/start identity/full command/cwd/spec path
  and content key/search id/job record/workspace keys/workspace lock holder).
  SIGINT to process group 68201 was sufficient; no SIGTERM or SIGKILL. The
  group was gone in under 3 s, the workspace flock is free and no campaign
  Python, SUMO or caffeinate process remains. The stop is provably lossless:
  all 2 158 cache files are byte-identical across the stop, zero partial
  `.tmp` files, `verify_search_workspace` reports no errors, and a probe over
  the frozen unit ledger finds 1 083 of 1 950 units cached AND fully valid
  (schema + unit identity + backend digest + content key), 0 corrupt, 867
  missing. The durable job record carries an `operator_stop` block recording
  the signal, the audit and the resume state. THE FULL CAMPAIGN HAS NOT BEEN
  RESTARTED and awaits a separate user decision.
  ROOT CAUSE, measured not inferred: batching was PARENT-LOCAL. The frozen
  baseline shows 80 330.94 worker-seconds against 88 771.27 active seconds -
  a ratio of 0.905, i.e. ONE unit worker busy ~90% of wall time against eight
  configured slots (11.3% utilization) - and 20/20 live process samples showed
  exactly one worker and at most one SUMO. The cause is structural: a five-day
  parent supplies at most five units, and with 3 229 hits against 851 misses
  over 816 parents only ~1.04 of them are genuinely new, so the eight-wide
  pool was handed roughly one item at a time.
  FIX: an opt-in global bounded daily-unit queue in the orchestration-only
  `independent_daily.py`, width = `--daily-workers`, so every existing SUMO
  budget check still applies. SYNTHETIC SCHEDULER SCALING, 180-unit fixture
  with one seeded per-unit cost profile replayed by every arm and a sleeping
  stand-in in place of SUMO: legacy 170.33 s at width 0.999; queue w1
  170.25 s / 0.999; w2 85.31 s / 1.995; w4 42.91 s / 3.965; w8 21.89 s /
  7.771 - 7.78x, 97.1% of theoretical. This measures the SCHEDULER, not
  per-unit SUMO cost. Cache bytes were identical across every arm. SAVED REAL
  OBSERVATION, not repeated in later runs: one real cold SUMO arm reached
  exactly 8 concurrent isolated workers and 8 concurrent SUMO processes and
  never exceeded either, over 170 samples. The campaign ETAs (2.93 h resume,
  6.58 h cold at width 8) are PROJECTIONS - two separately measured
  quantities multiplied - and are labelled as such in the report; no cold or
  resumed full campaign has been run.
  CACHE IDENTITY - CORRECTED 2026-08-27, THE EARLIER CLAIM WAS WRONG.
  `monthly_sumo.py` hashes NINETEEN source files into `source_digest`, not
  fourteen, and `run_monthly_closure_search.py` is one of them. The frozen
  report `validation/monthly_global_queue_baseline_2026-08-27.json` states
  the opposite in its `cache_bound_source_proof` block; that block is
  FACTUALLY WRONG and is superseded here. It is left byte-unchanged because
  it is frozen evidence. Measured: adding a single CLI flag moved
  `source_digest` from
  c0bbfc3202bf30c0b1be52dbd5060da3fc7d77e9681466adec7cd2e7ffb0efb0 to
  8b040d909753823756a10a459186f1e83140e41656c173febd72e351b15bf6d6, which
  would have orphaned all 1 083 cached units. The flag was therefore REMOVED
  and `run_monthly_closure_search.py` restored byte-identical to HEAD
  (sha256 26c3dbca7acb50eb4273aa188f6f73a88e6eb739b85c49b89099b277188d3abf);
  `source_digest` is back to c0bbfc32... Activation now lives in
  `independent_daily.py`, which is NOT in the nineteen, via
  `TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS` plus a required
  `TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_SCREENING=independent-exhaustive`
  declaration. The second variable is a safety gate, not decoration: global
  lookahead produces units nobody asked for, which under
  `--screening-mode=independent-cost-ordered-exact` would simulate exactly
  the work that mode's stop proof claims to have skipped. The resolver fails
  closed on a missing, unknown or command-line-contradicting declaration.
  The aggregate backend digest is unchanged at
  90f07a50c203f88b5f2c690a0c56b48c339cb63202658e34aa81cab0cbfcbeef.
  REVIEW ROUND 2, 2026-08-27 - four defects found and fixed, each pinned by a
  test that fails against the pre-fix code: (i) global lookahead is now
  restricted to the exhaustive PILOT stage, because a finalist round rebuilt
  its remainder from all 1 950 prepared units and would have upgraded every
  one of them to finalist coverage; (ii) queue width is now bound to the real
  SUMO budget before any unit exists - the daily runner must be
  process-isolated and start exactly one SUMO per unit, and the width may
  exceed neither the declared `--daily-workers` nor the benchmark approval of
  8, which refuses two configurations that passed every prior check
  (`--daily-workers 1`, leaving the production TraCI runner unwrapped, and
  `--daily-workers 1 --seed-workers 8`, which would be 64 concurrent SUMO);
  (iii) the queue's pullers are daemon threads with a bounded
  threading-shutdown hook, because as non-daemon threads an abandoned queue
  hung the interpreter forever; (iv) the benchmark's real arm owns a process
  group with bounded TERM/KILL escalation and refuses a speed claim unless
  every arm exited 0, did not time out, published a complete non-empty
  evidence population, left no partials and produced real ancestry samples.
  SCOPE CORRECTION to the ETAs: 6.58 h cold and 2.93 h resume are PILOT-SWEEP
  projections that omit the finalist stage. Bounded by the policy's own
  ceilings (12 finalists x 5 daily units, 4 initial repetitions adapting to
  12) they become ~7.19 h / ~3.54 h initially and ~8.81 h / ~5.15 h at the
  adaptive maximum, so the upper bound CROSSES the eight-hour goal. The frozen
  reports state the unqualified figures and are left byte-unchanged.
  CAMPAIGN STATE, stated precisely because two records disagree by design:
  the durable JOB record is `status: error` with an `operator_stop` block
  (the KeyboardInterrupt is the server finalizing the delivered SIGINT, not a
  search failure), while the WORKSPACE manifest still reads
  `status: running` with `progress.completed: 0` because the shutdown path
  reset the pointer. Neither is the resume authority: the content-addressed
  cache is, and it holds 1 083 of 1 950 units valid, 0 corrupt, 867 missing.`
- Objective and scope: `Reduce cold daily demand preparation by reusing a
  verified weekday/weekend routed catalog without changing exact daily PFE or
  warm-state semantics. Also reduce exact-repeat and first-new interactive closure
  latency and increase independent-exhaustive monthly-search throughput. Start
  with measurement and one resource budget; then test output reduction,
  routing, exact result
  reuse, allocation, cost-ordered stopping and targeted warm prefixes in that
  evidence order. The current interactive path is accepted; retain semantic
  equivalence at 50 physical sensors when simulation load is fixed, then prove calibrated population tiers because
  new observations can require more vehicles. Keep high-risk custom
  SUMO/libsumo work profile-gated.`
- Completion outcome: `First-new interactive closure p95 <=10 s on the active
  frozen fixture with identical scenario/trajectory digests and clean per-seed
  health. Exact repeat p95
  <=2 s with zero SUMO calls; async ack p95 <=1 s; monthly verified-unit throughput
  >=2.0x the frozen baseline with a <=24 h stretch target on the named
  1,776-schedule/2,224-unit fixture. Every adopted arm preserves required
  semantics, evidence, health, restart and provenance fields.`
- Context or checkpoints: `Three interactive seed workers are already adopted;
  their recent adoption check measured the closure at 13.9 s. Persistent
  external SUMO was
  semantically equivalent but slower and is closed. Monthly eight-worker
  evidence measured 1.69x with 2.11 GiB RSS. Cost-order v5 saved calls but
  failed equivalence. A two-unit frozen-input diagnostic measured a standard
  reusable Python worker at 1.027x; the six-unit/three-worker equal-cache
  follow-up measured 0.998x with exact evidence. Both are single draws and do
  not establish a speed decision. The cold-cache arm reproduced
  duplicate baseline publication before the single-flight repair. The repaired
  multiprocess regression proves one producer per content key. A multi-snapshot warm pilot measured 20.8x state creation
  but lacks ordinary SUMO meso-accumulator serialization. The stopped search
  ui-monthly-euc9qp is 476/1,776; its manifest records an interrupted_by_user
  progress pointer with resumable scratch. Its exact five-day form now sizes to
  780 periods/1,040 units instead of 1,776/2,224. The replacement exact
  five-day campaign ran and is now operator-stopped and resumable; it was NOT
  restarted. The active process-free disruption
  case improved from 4.1364 s to 1.051 s exactly. One isolated three-seed
  production-shaped closure completed in 10.690 s versus 11.549 s with the
  full-field rollback, with equal scenario/trajectory digests and clean health;
  this is diagnostic evidence, not a p95 result. The fair catalog campaign ran
  30 matched 6,000-candidate pairs and measured 55.246→24.715 s median
  (2.235x ratio of arm medians; 2.220x median paired speedup); seven catalog
  soak fixtures and legacy rollback passed. The active
  catalog-built day matches 672/672 integer sensor targets, and its bounded
  annual q50 warm pilot succeeded 1/1 with no full-year launch.`
- Primary files: `docs/plans/CANONICAL_ROUTE_CATALOG_PLAN_2026-08-24.md;
  docs/plans/ROAD_CLOSURE_SIMULATION_SPEED_PLAN_2026-08-21.md;
  docs/plans/DAILY_SIMULATION_CONCURRENCY_STRUCTURE_2026-08-21.md;
  docs/plans/FIFTY_SENSOR_PERFORMANCE_CONTRACT_2026-08-22.md;
  docs/plans/LARGE_SIMULATION_FUNCTION_STRUCTURE_2026-08-23.md;
  run_scenario.py; serve.py; tools/benchmark_speed.py;
  traffic_sim/simulation/disruption.py;
  traffic_sim/simulation/execution.py;
  traffic_sim/storage/singleflight.py;
  traffic_sim/simulation/independent_daily.py;
  traffic_sim/simulation/independent_daily_worker.py;
  traffic_sim/demand/route_catalog.py;
  build_candidates.py; build_sumo_demand.py; demand/calibration.py;
  traffic_sim/demand/catalog_qualification.py;
  tools/benchmark_route_catalog.py; tools/qualify_route_catalog.py;
  tools/adopt_route_catalog.py; tools/prune_candidate_cache.py;
  traffic_sim/simulation/monthly_sumo.py; run_monthly_closure_search.py;
  traffic_sim/core/contracts.py; traffic_sim/core/closure_calendar.py;
  traffic_sim/simulation/closure_preflight.py;
  traffic_sim/simulation/search_workspace.py; web/app.js; web/index.html;
  tools/cost_ordered_benchmark_suite.py; related tests and new frozen validation
  contracts/outcomes; tests/test_closure_disruption.py;
  tests/test_singleflight.py.`
- Constraints and safety: `The named monthly run is OPERATOR-STOPPED and must
  not be restarted without a separate user decision; its workspace and cache
  remain resumable, so treat them as live evidence. Bind every S0/reference
  run to frozen inputs and isolated output. Do not mutate the stopped run's
  cache, ledgers or workspace, and do not mutate source read by newly spawned
  workers.
  Do not reduce seeds/variants, closure horizon, rerouter coverage, recovery,
  precision or outputs needed by the product; do not loosen timeouts or gates;
  do not activate proxy/global-best/cost-first claims without new frozen
  equivalence and held-out evidence. Preserve all user-owned dirty files and
  failed historical outcomes.`
- Acceptance criteria: `For the catalog sub-plan, preserve every hard demand,
  structure, provenance, 50-station and warming identity gate; catalog
  restore/materialization p95 <=5 s; paired cold-day median at least 25% faster;
  paired vehicle-population deviation <=1%; one-time catalog build amortized within three cold
  days. Also meet the per-product thresholds in the speed plan on
  frozen inputs; identical scenario/trajectory semantic digests and per-seed
  flow/health/integrity evidence; identical monthly legal candidates,
  observations, failures, selected ids, winner, restart and cache behavior;
  at 50 physical stations with otherwise identical simulation load, closure
  p95 <=10 s, <=5% latency regression, and sensor-only validation p95 <=50 ms;
  every published demand build matches every directed sensor-edge × 15-minute
  target exactly after the declared whole-vehicle rounding rule, with zero
  maximum and summed residual;
  calibrated 21k/32k/43k/50k/60k tiers meet their 10/20/30 s budgets with
  loaded=inserted and zero waiting, stopping at the first failed capacity tier;
  peak RSS <=8 GiB for the named host benchmark; no new timeout, orphan or
  cancellation defect; monthly throughput is derived from awake active wall
  time on an exact-length fixture; old path remains a tested rollback until
  adoption.`
- Useful checks: `make lint; focused scenario timing/benchmark tests; focused
  independent_daily/monthly_sumo/cost-order tests; paired idle-host benchmark
  reports; semantic digest and field-by-field evidence comparison; restart,
  cancel, corrupt-cache and process-reaping tests; 6/25/50-station PFE and
  21k/32k/43k/50k/60k calibrated-vehicle load matrix; qualified minimal-edgeData
  production-default/rollback checks; git diff --check.`
<!-- ACTIVE_TASK_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
