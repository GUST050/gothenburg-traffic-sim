# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `Five-level strict-route LOSO study complete; active demand
  restored to the selected 50-OD floor; keep scenario/monthly execution paused.`
- Status: `ROUTE CORRECTNESS PASSES; 50 WINS PREREGISTERED ABLATION; NO RUN ACTIVE.
  Sensor candidates are
  canonicalized to the deterministic global fastest route for their OD, every
  crossed sensor must have a finite strictly slower avoiding route, and the
  grounded fallback now searches the global shortest-path tree in both
  home-to-activity and activity-to-home directions. The fresh make demand build
  published 18,266 vehicles with 671/671 exact sensor constraints and a
  post-build audit requalified 539/539 candidate routes. Same-protocol floors
  25/50/100/200/500 yielded median daily factors
  1.642x/1.560x/1.946x/2.293x/2.425x and mean GEH<5
  55.8%/57.3%/49.6%/43.3%/37.0%. Floor 50 wins both frozen rules and its active
  candidate SHA exactly matches the LOSO evidence; all held stations remain
  underidentified.`
- Suggested next action: `Improve route composition/regularization or add
  independent sensors; do not increase raw route count, weaken the route
  contract or start scenario/monthly execution automatically.`
- Eligible actors: `Any capable implementer, tester, reviewer or monitor.`
- Safety boundary: `Preserve all partial and historical artifacts; use fresh
  append-only IDs/roots, one demand-workspace writer, no source edits during a
  bound run, no shortest/positive-gap/count/provenance/gate weakening, and no
  delete/commit/push/deploy.`
- Updated: `2026-09-01: completed preregistered 25/50/100/200/500 six-fold
  LOSO study, selected 50, and restored an identity-matched active demand.`
<!-- WORKFLOW_CONTROL_END -->

<!-- WORKFLOW_HISTORY_START -->
## WORKFLOW_HISTORY

- Status: `2026-08-30 REPAIR-BATCH PASS 6 (review-03 CHANGES_REQUIRED on
  PASS 5): fixed the two remaining findings. (1) Evidence durability was
  checked only at daily-cache reload's envelope/identity level and at
  monthly evidence's JSON-shape level -- neither actually resolved the
  nested canonical payload, `RoutingProvenance`, or the access-impact/
  transformed-route artifacts it names, so a missing/tampered/swapped
  durable artifact could be accepted as valid. Fixed with one shared
  `monthly_sumo.validate_canonical_observation_evidence`, wired into
  `IndependentDailyRunner._load_cached`/`_save_cached` (reload AND
  fresh-write) and `monthly_search.evidence_from_dict`/
  `_run_and_publish_candidate` (resume AND publish) via duck-typed
  `cache_root` accessors that return `None` (skip, unchanged) for any
  backend/test double exposing none. 11 new tests. (2) The verification
  tool's healthy-control check compared nothing to a reference and could
  apply to the former-timeout unit on a zero-denial variant. Fixed:
  membership now comes from a frozen `HEALTHY_CONTROL_UNIT_ID` constant
  (never an incidental zero-denial outcome), and a new `--reference-report`
  flag compares an explicit 8-field allowlist against a prior report,
  recording the reference path/sha256 and per-field equality.
  RE-VERIFIED with two fresh real-SUMO replays (`runs/closure-routing-
  verify-20260830-001/`, `-002/` with `--reference-report` pointed at the
  first): former-timeout unit `daily-unit-24737391111be0e137537df7`
  27.1-32.0 s first-attempt across both runs (limit 300 s), 0 denied, 0
  teleports, `active_closed_edge_throughput: 0`, 100% unaffected vehicles
  byte-identical (55,633-55,774 per variant), `healthy_control_
  semantic_check: None` on every variant (correctly excluded). Healthy
  control `daily-unit-2387bbad11130660b9de0d17` equally clean both runs
  (24.7-28.5 s), 57,388-57,501 byte-identical vehicles per variant,
  `all_passed: True`, and in the second run `reference_comparison.
  all_equal: True` across all 8 fields against the first run's report
  (sha256 `907e809c...` recorded). Focused+broader tests clean (855
  passed, 1 skipped); frozen-manifest suites show the same 19 pre-existing
  intentional source-digest-drift failures as PASS 3-5 (confirmed pure
  digest mismatch). No original run/evidence artifact modified; both new
  replay roots preserved; nothing committed, pushed, branched, or
  campaign-launched. See ARCHITECTURE.md's SIXTH-pass paragraph and
  AGENT_NOTES.md CURRENT_HANDOFF for the complete record.`
- Status: `2026-08-30 REPAIR-BATCH PASS 5: a fresh review ran PASS 4's own
  `tools/verify_closure_routing_frozen_units.py` for real (PASS 4 had built
  it but only exercised it statically alongside its own patch) and found
  three concrete defects. FIXED ALL THREE: (1) `active_closed_edge_
  throughput` read `null` on every variant of both frozen units --
  root-caused to `metrics.active_closure_throughput` indexing closure
  quarters by ABSOLUTE epoch-relative seconds against a `flows` array that
  is actually indexed from 0 at the run's own trimmed `--begin`; fixed with
  a `window_begin_s` parameter threaded through both real call sites. (2)
  `RoutingProvenance` gained `unit_id` and `transformed_route_sha256`,
  `access_impact_sha256` became required and hex-validated (not just
  length-64), two synthetic zero-valued provenance fallbacks in
  `monthly_sumo.py` were replaced with fail-closed errors, and both
  content-addressed evidence stores now validate an existing file's bytes
  before reusing it. `POLICY_VERSION` bumped v3->v4. (3) the verification
  tool's `byte_identical_to_source` used to give up (`null`) whenever
  anything was rerouted; it now durably preserves the transformed route
  file, names every rerouted vehicle id, and byte-diffs every genuinely
  unaffected vehicle directly, plus a new selected-field healthy-control
  semantic check. RE-VERIFIED with a fresh real-SUMO replay in a new
  exclusive root (`/tmp/closure-routing-verify-run3`; prior roots
  preserved): `daily-unit-24737391111be0e137537df7` (former timeout)
  30.94/26.45/25.36 s first-attempt (limit 300 s), 0 denied, 0 teleports,
  `active_closed_edge_throughput: 0`, 55,633-55,774 unaffected vehicles per
  variant BYTE-IDENTICAL to source; healthy control
  `daily-unit-2387bbad11130660b9de0d17` equally clean, 57,388-57,501
  byte-identical vehicles per variant; both units' `healthy_control_
  semantic_check.all_passed: True` on every variant. Focused+broader tests
  clean (857 passed, 2 skipped); frozen-manifest suites show the same 19
  pre-existing intentional source-digest-drift failures (v3->v4 plus the
  other hashed-file changes), confirmed pure digest mismatch. No original
  run/evidence artifact modified; nothing committed, pushed, branched, or
  campaign-launched. See ARCHITECTURE.md's FIFTH-pass paragraph and
  AGENT_NOTES.md CURRENT_HANDOFF for the complete record.`
- Status: `2026-08-29 REPAIR-BATCH PASS 4: corrected a false BLOCKED verdict
  (PASS 3's review-fix-03 tested `which sumo`/bare `import sumolib`
  instead of this repo's own `traffic_sim.simulation.runtime.sumo_home()`,
  which DOES resolve a working SUMO 1.27.1 here), fixed a real evidence-
  durability gap (canonical monthly observations were never persisted
  anywhere durable, only referenced by an unresolvable digest -- fixed via
  content-addressed `_preserve_canonical_observation`/
  `resolve_canonical_observation` in `monthly_sumo.py`, mirroring the
  existing access-impact pattern, no frozen-schema changes), and ran BOTH
  required frozen units through the real `MonthlyDemandResolverRunner`/
  `IndependentDailyRunner` path for the first time. Results: the former-
  timeout unit `daily-unit-24737391111be0e137537df7` (07:15-15:15 daytime
  closure) completed q10/seed 1000 in 29.72 s first attempt (limit 300 s),
  0 denied, 0 teleports, no closure leak, recovered; q50/q90 equally
  clean. Healthy control `daily-unit-2387bbad11130660b9de0d17` (00:00-
  08:00, same edge) also clean on all three variants, 0 invented denials.
  Full focused+broader test bundle re-run clean (835 passed, 1 skipped
  across two runs); frozen-manifest suites show the same 19 pre-existing
  intentional source-digest-drift failures as PASS 3 (confirmed pure
  digest mismatch, not behavioural). See AGENT_NOTES.md CURRENT_HANDOFF
  for the full record, including the disclosed scope limit on the
  byte-identical-unaffected-fragment check and one observed-but-unfixed
  cold-arm reporting-fidelity note (`closed_edge_throughput` can read
  `None` instead of an explicit `0` on a clean cold run; confirmed this
  does not weaken the leak hard-failure gate). No original run/evidence
  artifact modified; nothing committed, pushed, branched, or campaign-
  launched.`
- Status: `2026-08-29 TIMEOUT REPAIR IMPLEMENTED AND VERIFIED. Search
  ui-monthly-12hg8f3 completed 1,690 candidates in 37,002 active seconds with
  no winner because 540 of 5,180 exact SUMO launches timed out. An isolated
  timed-out unit reproduced at the same 300 s limit with one worker and with
  both Dijkstra and A*, while its partial summary showed rapidly growing
  running/halting traffic during the active closure. Timeouts are now stored
  only as structured undecided evidence, never as permanent hard traffic
  failures. Product monthly runs now retain the 300 s first boundary but
  automatically replay only timed-out identities with the exact same model,
  seed and resources at a registered 1,800 s bound. A formerly timed-out real
  unit completed in 743.837 s and produced the determinate hard failures
  `unfinished_vehicle_share` and `recovery_congestion_not_dissipated`; no
  timeout or model change was involved. Only a second-attempt timeout remains
  terminal and stops remaining lookahead. The finished historical
  workspace remains unchanged and verifies with zero integrity errors. The
  affected regression surface passes 379/379 tests; the full suite reports
  5,319 passed, 27 skipped and 155 fail-closed historical fingerprint/freeze
  failures in the already drifted working tree. git diff --check passes.
  The keyless map uses the official OpenStreetMap raster endpoint with matching
  CSP and attribution.
  CATALOG QUALIFIED, ADOPTED AND SOAKED. A matched-size campaign used
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
  SUMO benchmark, monthly campaign, commit or push was produced.
  ROOT-CAUSE CLOSURE-ROUTING FIX (2026-08-29): the timeout repair above
  (registered 1,800 s replay) was a symptom fix; this pass implements the
  root fix underneath it. New `traffic_sim/simulation/closure_routing.py`
  (`closure_origin_routing_v1`) rewrites every affected vehicle's route,
  before SUMO starts, from its original origin to its original destination
  along the deterministic fastest legal path with every applicable closed
  edge excluded (fixed-point banned-set growth, provably terminating,
  reusing `disruption.py`'s shortest-path engine so routing and disruption
  ranking share one source of truth). Only a destination-on-a-closed-edge or
  genuinely-unreachable trip is denied — held outside the network, recorded
  as a stable provenance-bound `AccessImpactRecord`, never truncated, never
  simulated, never a generic timeout. `run_scenario.
  reroute_closure_affected_vehicles` replaces `truncate_stranded_vehicles` at
  all three production call sites (`run_scenario.py`'s scenario path,
  `suggest_closure_time.simulate_closure`, `ArchivedDemandSumoRunner`'s cold
  and warm-audit paths); the retired function is pinned unreachable from
  each by tests. Closure runs no longer force `time-to-teleport -1`
  (`closure_teleport.CLOSURE_ROUTING_TELEPORT_POLICY_S = None`, SUMO's own
  default) — safe because the hazard is eliminated pre-simulation, not
  suppressed during it; the retired `-1` constant is kept, named, for
  historical diagnostic reproduction only. `write_closure_additional`'s
  runtime rerouter is kept as a fail-closed structural declaration, not as
  the correctness mechanism. Monthly backend provenance
  (`monthly_sumo.py`'s source digest) now also hashes `closure_routing.py`
  and `disruption.py` (19 -> 21 files), so no pre-fix cache or warm state can
  satisfy a new lookup. NOT touched, deliberately out of the named scope
  (monthly road-closure timeouts): `signal_optimize.py`,
  `tools/benchmark_persistent_sumo.py`, `signal_closure_combine.py`,
  `tools/freeze_monthly_warm_state_v2.py`,
  `tools/measure_direction_decision_sensitivity.py` still call the retired
  `truncate_stranded_vehicles` + disabled-teleport pair, which remains
  internally self-consistent for them; migrating them is tracked as
  remaining work, not silently assumed done (see
  `tests/test_closure_teleport_wiring.py::TestEveryClosureSimulatorAgrees`'s
  updated docstring).
  VERIFIED: `tests/test_closure_routing.py` (20 new tests: fastest
  closure-excluding routing, windowed fixed-point growth, destination-closed
  and no-legal-path denial, deterministic tie-breaking, byte-identical
  unaffected fragments, fail-closed on unsupported route shapes, access-
  impact evidence schema, production-wiring guards). Full focused battery —
  `test_closure_routing.py`, `test_scenario.py`, `test_suggest_closure_time.py`
  (4 pre-existing tests updated for the new truncated->0/dropped->denied
  count semantics), `test_closure_teleport_wiring.py` (3 pre-existing tests
  updated for the new default constant), `test_closure_access_impact.py`,
  `test_closure_disruption.py`, `test_monthly_sumo.py`,
  `test_monthly_warm_state.py`, `test_independent_daily(_queue)`,
  `test_monthly_search.py`, `test_finalist_decision.py`,
  `test_cost_ordered_execution.py` — 606 tests passed, 0 failed (1 skipped)
  across this battery. `git stash` confirmed the 15
  `test_monthly_warm_state_freeze.py` fingerprint/freeze failures seen on a
  full run are the SAME 12 pre-existing "already drifted working tree"
  failures TASKS.md already documents, plus this session's own 3
  now-fixed `test_closure_teleport_wiring.py` regressions (fixed, not left
  failing). Targeted pylint (`closure_routing.py`, `disruption.py`,
  `run_scenario.py`, `suggest_closure_time.py`, `closure_teleport.py`,
  `monthly_sumo.py` and the three touched test files) is clean; `git diff
  --check` passes; every changed file parses.
  MEASURED REAL SUMO (2026-08-29, `run_scenario.py --closure` against the
  live 2027-11-11 demand, closing the SAME edge as the timed-out unit,
  `96527131_26842526_0`, 8 h window): a clean run completed in **10.05 s**
  total wall time (SUMO itself 1.95 s), 1,803 rerouted, 0 denied, 0
  teleports, closure integrity verified, scenario published — against the
  previous 300 s timeout / 743.837 s registered-retry for this edge. The
  timed-out unit's OWN literal window (07:15-15:15) also finished in 9.95 s
  but was correctly refused publication by the pre-existing
  `closure_integrity_status` fail-closed gate over one boundary-timing
  vehicle; `git stash` proved that exact single-vehicle event reproduces
  IDENTICALLY under the pre-fix code with normal teleport forced on, i.e. it
  is a pre-existing, deterministic characteristic of this edge/window/demand
  combination, not something this fix introduced. Full details and citation
  of the SUMO documentation this root-cause analysis relies on are in
  ARCHITECTURE.md's "Closure-integrity boundary" section.
  NOT DONE, honestly: this was a direct `run_scenario.py --closure` CLI
  verification exercising the exact production routing module, NOT a
  literal replay of the frozen `daily-unit-24737391111be0e137537df7` /
  `daily-unit-2387bbad11130660b9de0d17` monthly-search harness — wiring a
  bounded single-unit driver through `MonthlyDemandResolverRunner` /
  `IndependentDailyRunner` (matching `ClosureSchedule`, target repetitions
  and the exact worker-isolation path the real search uses) was not
  completed this session. No monthly campaign was run or resumed; no commit
  or push was made; `ui-monthly-12hg8f3`'s own workspace/ledgers were only
  ever read, never written. Do not claim a monthly-campaign-wide timing
  figure from the one-closure/one-seed numbers above.`
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
- Updated: `2026-08-29: implemented the root-cause closure-routing fix
  underneath the 2026-08-29 timeout-repair retry mechanism (see the
  ROOT-CAUSE CLOSURE-ROUTING FIX paragraph above for the full record: new
  closure_routing.py, retired truncate_stranded_vehicles from all three
  production call sites, teleport policy default flipped, 606 focused tests
  passed, real-SUMO verification measured 10.05 s against the prior 300 s
  timeout / 743.837 s retry on the same closed edge). Previously updated
  2026-08-28, sixth narrow review-batch pass: strict timeout-v3
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
<!-- WORKFLOW_HISTORY_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### SUBHOUR-CLOSURE-2026-08-30 — Exact cost-ordered monthly search

- Status: `DONE — FASTEST SENSOR-ROUTE GENERATION REPAIRED AND VERIFIED;
  broader Phase 0-7 execution remains paused.`
- Objective and scope: `Reduce exact SUMO work structurally while preserving
  the 30-date/65-window/five-consecutive-day scope and robust q10/q50/q90
  routing, health, recovery, provenance and tie semantics. Sensor-attributed
  emitted vehicles must use globally fastest routes, and removing their sensor
  edge must make the fastest legal reroute strictly slower.`
- Completion outcome: `Every READY monthly result has a machine-verifiable
  cost boundary and exact evidence; normal cold execution is <60 minutes, while
  pathological timeout/tie/budget cases terminate explicitly inconclusive.`
- Context or checkpoints: `The existing cost-ordered core has an exact ledger,
  durable cursor, stop proof and same-path ordered-exhaustive switch. Earlier
  v31–v36 Phase 3 attempts are partial, source-stale or pre-execution terminals
  and cannot be promoted. Phase 4 v12 measured 1,950/5,850/1,690 but lacks the
  now-required complete producer binding and is historical only. The repaired
  WindowCostIndex precomputes crossing events and unique-OD detours and still
  requires a fresh measured cold-benefit and field-identical oracle. q10/q90
  remain uncalibrated stress cases, not probability bounds. The registered zero
  diagnostic instead exposed incompatible upstream demand: bounded-detour
  sensor routes and equal-cost avoiding paths violate the user's route contract.
  The repaired generator chooses grounded ODs from the unrestricted global
  shortest-path tree, supports both tour directions, fills the exclusive floor,
  and persists strict route proofs checked again by PFE and route catalogs. A
  preregistered 25/50/100/200/500 ablation selected 50 on both median daily
  factor and mean GEH<5. Larger pools remained route-correct but added
  underidentified exclusive variables and worsened held-out accuracy.`
- Primary files: `.ai-flow/tasks/complete-subhour-plan.md`,
  `.ai-flow/tasks/fix-closure-pricing-then-continue.md`,
  `.ai-flow/config.complete-subhour.sonnet.toml`, `tools/ai_flow.py`,
  `run_monthly_closure_search.py`, `build_candidates.py`,
  `build_sumo_demand.py`, `traffic_sim/demand/pfe.py`,
  `docs/plans/Q10_Q90_AND_SUB_HOUR_MONTHLY_SEARCH_PLAN_2026-08-30.md`,
  `traffic_sim/simulation/cost_ordered_search.py`,
  `traffic_sim/simulation/cost_ordered_execution.py`, benchmark tooling and
  focused tests.`
- Constraints and safety: `Do not restart exhaustive monthly execution, weaken
  any evidence gate, mix backend provenances, delete preserved evidence,
  overlap Phase 3/4 writers, edit bound sources, commit, push or deploy.
  q50-only requires a separate complete sensitivity gate.`
- Acceptance criteria: `Same-path ordered-exhaustive equivalence; exact ledger
  equality; emitted-route equality to global shortest; strict positive
  banned-sensor gap for every affected vehicle; exact measured counts; a frozen
  support audit or truthful INCONCLUSIVE_SENSOR_SHORTEST_SUPPORT; identical
  decision in preregistered decidable cases; valid stop proof; >=30% fewer exact
  verifications and active time in bounded real tests; one cold READY month <60
  min before any single-run claim.`
- Useful checks: `502 focused candidate/build/PFE/catalog/LOSO/route-support
  tests pass after the final build. make demand exited 0, installed 166 missing
  exclusive basis routes, reached at least 50 distinct routes for all seven
  sensor edges, solved 96/96 PFE intervals and published 18,266 vehicles with
  671/671 exact integer sensor constraints. A post-build copied audit kept
  539/539 with zero drops/canonicalizations, zero nonpositive gaps and minimum
  gap 0.219582433405 s. Fresh loso_pfe_meso_v11_observability_gate completed
  six folds with all active PFE fits at 100% and every anchor proof passing;
  53 LOSO/route-support regressions pass. Station ratios old->new are 107
  1.443->0.506, 1074 0.884->0.602, 1076 0.685->0.270, 133 0.769->0.685,
  134 2.613->1.135 and 2276 2.499->1.104. Scenario and monthly execution
  remain paused. Same-v11 floors 25/50/100/200/500 all completed with valid
  routes and exact active fits; their median daily factors were
  1.642x/1.560x/1.946x/2.293x/2.425x. The final rebuilt 50 candidate SHA equals
  the selected report's bound SHA exactly.`
<!-- ACTIVE_TASK_END -->

<!-- ACTIVE_TASK_HISTORY_START -->
## ACTIVE_TASK_HISTORY

### PERF-CLOSURE-2026-08-21 — Faster exact closures and monthly simulation

- Status: `IN_PROGRESS — 2026-08-29 REPAIR-BATCH PASS 4 (continuation of
  PASS 3, not a re-plan). PASS 3's BLOCKED verdict was itself wrong: it
  tested `which sumo`/a bare `import sumolib` instead of this repo's
  `runtime.sumo_home()`, which resolves a real, working SUMO 1.27.1
  install here. Re-verified findings 1-3 (windowed timing, single-vClass
  routing, teleport-policy provenance) against the current code with no
  defect found -- re-ran the same focused tests fresh, all pass. Finding 4
  turned out worse than PASS 3's own "NOT DONE" framing: canonical
  monthly observations were never durably persisted at all (an in-memory
  list reset every call), so `CandidateEvidence`'s digest was
  unresolvable in practice, not merely un-threaded through a dataclass.
  Fixed with a content-addressed persist/resolve pair in `monthly_sumo.py`
  (`_preserve_canonical_observation`/`resolve_canonical_observation`,
  fail-closed on tamper/absence) mirroring the existing access-impact
  pattern -- deliberately still NOT widening `PairedObservation`/
  `CandidateEvidence`/`CanonicalObservationDigest`'s own frozen field
  sets, same scope boundary PASS 2 and PASS 3 both reaffirmed. Built
  `tools/verify_closure_routing_frozen_units.py` (reconstructs each named
  unit's own schedule from the `ui-monthly-12hg8f3` ledger, re-derives its
  unit_id via `decompose_schedules` and asserts the match, then drives the
  REAL `MonthlyDemandResolverRunner`/`IndependentDailyRunner` with
  `build_missing=False`/`queue_workers=1` and fresh exclusive roots — no
  path reaches `run_monthly_search`) and RAN IT for real against both
  named units, q10 then q50 then q90 incrementally. MEASURED:
  `daily-unit-24737391111be0e137537df7` q10/seed 1000 — 29.72 s first-
  attempt wall time (limit 300 s), 1 attempt/0 timeouts, 0 denied, 0
  teleports, no closure leak, `recovered: True`; q50/q90 equally clean
  (26.11 s/25.20 s). `daily-unit-2387bbad11130660b9de0d17` (healthy
  control, same edge, different window) clean on all three variants, 0
  invented denials. Every `routing_provenance` record resolved end to end
  through the new finding-4 persistence path on real data. Source
  workspace verified untouched. Disclosed limit: neither run had
  zero-rerouted vehicles, so the tool's byte-identical fast path did not
  fire; it says so explicitly and defers to
  `TestUnaffectedRoutesArePreservedExactly` for that guarantee. Observed
  (not fixed, out of the four-finding scope): the cold arm's
  `closed_edge_throughput` can read `None` instead of an explicit `0` on a
  clean run (`suggest_closure_time.py::run_one` omits
  `measured_empty_edges`, unlike the warm arm's own prior fix for the same
  class of gap) — confirmed this is a reporting-fidelity gap only and does
  not weaken the leak hard-failure gate. Full focused+broader test bundle
  (506 + 329 = 835 passed, 1 skipped) and frozen-manifest suites (same 19
  pre-existing intentional-drift failures, confirmed pure digest mismatch)
  all re-run fresh. See AGENT_NOTES.md CURRENT_HANDOFF for the complete
  record. No original run/evidence artifact modified; nothing committed,
  pushed, branched, or campaign-launched.`
- Status: `IN_PROGRESS — 2026-08-29 REPAIR-BATCH PASS 3 (5 of 5 findings in
  this batch, severity-ordered): a THIRD review found the two real gaps
  PASS 2 left open (vClass/permission routing, provenance threading) plus
  one more real defect (suggest_closure_time.py's `closure_feasibility`
  publishing the WRONG teleport policy — its own legacy `-1` default,
  while production actually ran with SUMO's default/teleporting enabled).
  ALL THREE FIXED: (3) `metadata.build_metadata` (schema 1->2) now builds
  a `DEFAULT_VCLASS`-filtered successors graph + `restricted_edges` list;
  `run_scenario.build_edge_graph` (the one seam every production caller
  uses) sources from it on both cached and XML-fallback paths; a vehicle
  declaring an unrecognised `type=` now fails closed
  (`_check_vehicle_class`) instead of routing on an unproven assumption.
  Changes NOTHING on the real network today (zero `allow`/`disallow`
  anywhere in `net.net.xml`, verified) — makes an already-true claim
  provable. (4) the free-form `routing_provenance` dict is now a
  validated `closure_routing.RoutingProvenance` dataclass (fail-closed,
  strict `from_dict`), wired at both monthly_sumo.py call sites;
  `PairedObservation`/`CandidateEvidence`/`CanonicalObservationDigest`
  schema threading remains explicitly out of scope (16 frozen manifests),
  same call as PASS 2. (extra) both `closure_feasibility` call sites now
  pass `closure_teleport.CLOSURE_ROUTING_TELEPORT_POLICY_S if
  self.close_edges else None` explicitly instead of silently inheriting
  the function's legacy `-1` default. POLICY_VERSION bumped v2 -> v3.
  FINDING 5 STILL BLOCKED, now confirmed as a genuine ENVIRONMENT gap, not
  a scope choice: this sandboxed session has no sumo/netconvert/duarouter
  binary and no sumolib (verified directly), so neither frozen unit
  (`daily-unit-24737391111be0e137537df7`/`...2387bbad11130660b9de0d17`)
  could be run through the real monthly worker path — no SUMO subprocess
  can execute here at all. VERIFIED this pass: 44/44
  test_closure_routing.py; 274/274 (+1 skipped) across
  test_closure_routing/test_scenario/test_suggest_closure_time/
  test_closure_teleport_wiring/test_closure_access_impact/
  test_closure_disruption/test_sumo_network_metadata; 186/186 across
  test_monthly_sumo/test_independent_daily/test_finalist_decision. Full
  `tests/` run also executed; 3 pre-existing `test_ai_flow.py` failures
  are unrelated (different files, reproduce against the pre-existing
  dirty `.ai-flow/config.toml`, not touched by this pass). See
  AGENT_NOTES.md CURRENT_HANDOFF for the full record. No original run/
  evidence artifact modified; nothing committed, pushed, or launched.`
- Status: `IN_PROGRESS — 2026-08-29 REPAIR-BATCH PASS 2 (5 of 5 findings in
  this batch, severity-ordered): a SECOND review of the root-cause
  closure-routing fix found 5 new defects in the v1 policy this file's
  PREVIOUS entry shipped. TWO REAL CORRECTNESS FIXES, both in
  closure_routing.py, POLICY_VERSION bumped v1 -> v2:
  (1) CRITICAL — the v1 fix's own `CLOSURE_TIMING_SAFETY_MARGIN_S = 900`
  additive margin was itself unproven: congestion delay has no demonstrated
  upper bound, so no finite margin can PROVE a vehicle clears an edge
  before a still-open closure ends. Replaced with the one interval fact
  that IS provable without bounding congestion: real transit is never
  faster than free flow, so a free-flow lower bound that has already
  reached/passed a window's end IS proof of safety; every other case
  (including a window still far in the future) is now classified
  applicable/affected. This can only widen who is treated as affected,
  never narrow it — see `_edge_occupancy_lower_bound`'s docstring.
  (2) HIGH — `destination_closed` fired on bare membership of the
  destination edge in the closed-edge set, denying trips whose destination
  closure window does not even apply to them (already over, or not yet
  reachable within the window). Now evaluated with the same applicability
  predicate as routing, so a destination is only denied when ITS OWN
  window is applicable to that trip.
  PARTIAL PROGRESS on provenance (finding 4/5, high): monthly backend
  provenance now carries a `routing_provenance` block (routing policy
  version, access-impact-report sha256, rerouted count) on BOTH the cold
  and warm execution arms — previously the access-impact digest was
  computed and discarded at both call sites. `write_access_impact_report`/
  `prepare_route_file`/`reroute_closure_affected_vehicles` gained an
  optional `identity` parameter (candidate/schedule id, demand variant,
  seed, work date, execution arm) bound verbatim into the evidence file.
  closure_routing.py's own source hash was ALREADY part of
  `simulation_source_digest` (monthly_sumo.py), so the v1->v2 semantic
  change already invalidates every pre-fix monthly cache/backend identity
  without any extra wiring — confirmed by the pre-existing frozen-manifest
  tests failing on source drift after this change (expected; NOT
  "repaired" — re-freezing them would hide the exact invalidation this
  finding asked for). NOT DONE this pass, both real gaps:
  (3) HIGH, NOT ADDRESSED — vClass/lane/connection-permission-aware
  routing. `build_edge_graph`/`shortest_path_edges`/`closure_routing` still
  route on an unqualified successor graph with no vehicle type or SUMO
  permission awareness; an advertised "fastest legal path" is not proven
  legal for a restricted vClass. Needs its own scoped pass (parse vType per
  vehicle fragment, filter the adjacency by lane/connection `allow`/
  `disallow` per SUMO's network format, fail closed on an unrecognised
  vClass) — not attempted here for scope reasons, same as prior passes.
  (5) HIGH, NOT ADDRESSED — the literal frozen-unit harness replay
  (`daily-unit-24737391111be0e137537df7`/`...2387bbad11130660b9de0d17`
  through the real monthly worker path) is STILL not built, exactly as the
  two prior passes below already recorded; this pass did not attempt it
  either — see AGENT_NOTES.md CURRENT_HANDOFF for the honest record.
  VERIFIED this pass: 30/30 test_closure_routing.py (10 new/rewritten
  tests replacing the v1 margin tests plus new destination-window and
  identity/tamper tests); 221/221 (+1 skipped) across
  test_closure_routing/test_scenario/test_suggest_closure_time/
  test_closure_teleport_wiring; 186/186 across
  test_monthly_sumo/test_independent_daily/test_finalist_decision; 256/256
  across test_monthly_search/test_cost_ordered_execution/test_serve/
  test_independent_daily_queue. The pre-existing frozen-manifest suites
  (test_monthly_warm_state_freeze.py, test_monthly_warm_state_v16_freeze.py)
  now fail on source drift, as expected and intended — not touched. No
  original run/evidence artifact modified; nothing committed, pushed, or
  campaign-launched.`
- Status: `IN_PROGRESS — 2026-08-29 REPAIR-BATCH PASS 1: a review of the
  root-cause closure-routing fix (see below) found 3 defects. 2 of the 3
  (compatible-cache backend-digest invalidation in independent_daily.py;
  rerouted-vs-truncated/access-impact-preservation in
  suggest_closure_time.py + monthly_sumo.py) were checked line-by-line
  against the review's own citations and found ALREADY FIXED in the current
  tree (each carries a dated comment and a passing pinning test) — no code
  changed for those two. The third (closure_routing._closures_overlapping
  using a free-flow point-in-time check instead of an occupancy interval,
  which missed a real boundary-timing vehicle that was then jam-teleported
  onto the closed edge) was real and is fixed: occupancy-interval check +
  900 s safety margin + a post-detour residual-overlap assertion. 5 new
  tests, full targeted batch (24 + 215 + 376 tests) passes; broader `tests/`
  run in progress at last check. Literal frozen-unit harness replay
  (`daily-unit-24737391111be0e137537df7`/`...2387bbad11130660b9de0d17`
  through the real monthly worker path) still not built — see AGENT_NOTES.md
  CURRENT_HANDOFF for the full record and honest remaining gaps. No original
  run/evidence artifact touched; nothing committed, pushed, or launched.`
- Status: `IN_PROGRESS — 2026-08-29 SAME DAY, ROOT-CAUSE FIX ADDED beneath
  the timeout-recovery retry described below: closure_routing.py now
  rewrites every affected route around a closure before SUMO starts, so the
  1,800 s registered replay this paragraph describes should rarely be
  needed going forward (not yet re-measured on a full campaign — see the
  WORKFLOW_CONTROL Status field's ROOT-CAUSE CLOSURE-ROUTING FIX paragraph
  for the complete record, tests and real-SUMO measurements). The retry
  mechanism itself is unchanged and still exists as a symptom-level safety
  net. 2026-08-29 timeout recovery is implemented and
  verified. A first 300 s timeout triggers one registered 1,800 s exact replay;
  simulation inputs, resources and scientific gates stay unchanged, and both
  launches are recorded. A real ui-monthly-12hg8f3 timeout identity completed
  in 743.837 s under that allowance and was genuinely disqualified for
  unfinished traffic and failed recovery. Only exhaustion of attempt two is
  stored as undecided evidence. The historical workspace remains immutable;
  future searches use the new protocol, while resolving every old timeout into
  a replacement result still requires a new evidence-producing recovery run.
  The canonical
  route catalog is provenance-bound,
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
<!-- ACTIVE_TASK_HISTORY_END -->

## History

Everything before 2026-08-06 lives in `docs/history/TASKS_history.md`
(7,234 lines). It is preserved context only —
per `AGENTS.md`, nothing outside the marked blocks above is current.
