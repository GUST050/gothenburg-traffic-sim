# Tasks

Only the marked `WORKFLOW_CONTROL` and `ACTIVE_TASK` blocks describe the
current focus. The remaining task ledger is historical context; old Sol/Luna
owners, states and approval formulas are not active workflow rules. See
`AGENTS.md` for the flexible actor protocol.

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- Mode: `FLEXIBLE — roles are capabilities, not model identities`
- Current focus: `FULL-DAY-ANNUAL-WARMING`
- Status: `BLOCKED_ON_DISK_PREFLIGHT — demand, provenance, 96-link chaining and
  archive contracts pass; the corrected 192-GiB gate refuses current 168-GiB
  free space; no annual unit has run`
- Suggested next action: `Free at least 23.92 GiB (prefer 30 GiB margin), rerun
  preflight for plan 9cc823d316eee71d1895e90704537512e48ad7ed37604d9644d9b88a9845283b,
  then initialize its new zero-attempt root. Never resume or relabel an older
  root.`
- Eligible actors: `Any model or person; no Sol/Luna routing requirement`
- Safety boundary: `Independent reset is explicit and never continuous
  evidence. Exact daily forecast/variants/seeds, six-hour recovery, cache
  integrity and cold fallback remain mandatory. Population does not activate
  product reuse; proxy licensing, deployment and release remain separate.`
- Updated: `96-link audit complete; measured disk gate blocks launch / 2026-08-04`
<!-- WORKFLOW_CONTROL_END -->

## ACTIVE_GOAL

Build a production-ready traffic simulation and road-closure decision system
whose supported user-facing workflows complete in seconds, without trading
away traffic fidelity, closure accuracy, robustness, provenance, or honest
uncertainty.

Success means:

- On named reference hardware, cached or precomputed simulations render at
  p95 <= 2 seconds, and a supported new scenario or road-closure query returns
  a validated result at p95 <= 10 seconds once its required demand inputs are
  available. Every benchmark must record hardware, data scope, cache state,
  candidate count, seeds, and model/version identity.
- When a trustworthy result cannot be produced inside the latency budget, the
  API responds within 1 second with an honest `running`, `inconclusive`, or
  `no_viable` state and completes full-fidelity verification asynchronously;
  it must never substitute a stale, partial, or unvalidated answer merely to
  appear fast.
- The road-closing function handles exact directed edges, dates, time windows,
  detours, rerouting, paired baselines, uncertainty, and hard failures, then
  returns a reproducible `unique_winner`, `tie`, `inconclusive`, or
  `no_viable` decision with clear provenance and confidence.
- Speed improvements come from immutable caches, precomputation, warmed
  demand artifacts, reuse of matched baselines, safe shortlist/bounded-search
  methods, parallelism, and validated fast models. They must not come from
  fewer required seeds, weaker validation, omitted candidates, looser recall
  or regret gates, hidden failures, or reduced provenance.
- Every release proves both sides of the contract: no regression in calibrated
  simulation quality, closure practical-winner recall, shortlist regret,
  failure-disqualification recall, reproducibility, and fail-closed behavior;
  and a measured improvement or preservation of p50/p95 latency and resource
  use.
- Full SUMO remains the accuracy authority and release evidence. Fast paths
  may serve results only inside their validated domain; otherwise they defer
  to SUMO and disclose the wait. Diagnostic replay is never release evidence.

The V4 promotion disposition is closed as `DO_NOT_PROMOTE`: its passing local
gate remains development evidence and may not be deployed. Work now proceeds
through measured, result-preserving performance tasks. Release, publication,
Stage-B merge, and horizon warming remain blocked.

<!-- COMPLETED_TASK_LUNA_PERF_19_START -->
## COMPLETED_TASK — LUNA-PERF-19

### LUNA-PERF-19 — Build and freeze the persistent-SUMO experiment harness

- Task ID: `LUNA-PERF-19`
- Revision: `2`
- Owner: `Luna High`
- Status: `DONE — Sol approved the unexecuted/unapproved freeze 2026-07-24; execution NOT authorized`
- Delivery size: `EXTENDED`
- Scope: Complete the Phase 7 C1 harness without weakening full production-
  artifact equivalence. Extract behavior-preserving scenario and trajectory
  payload builders from `run_scenario.py`; production and both benchmark arms
  must call those shared seams. Repair the partial harness's query preparation,
  one-query/three-seed reference arm, persistent output parsing, strict schema,
  preflight ordering, process cleanup, timeout and proof-row validation. Use
  fakes and static fixtures only, preserve every production output/API contract,
  and freeze one replacement v1 key only after all code and checks settle. Do
  not run SUMO/TraCI, open sockets, create outcomes, or change product behavior.
- Completion outcome: production uses the shared builders with unchanged
  scenario/trajectory semantics; the complete fail-closed harness and focused
  tests pass; one new immutable unexecuted/unapproved content key replaces the
  stale revision-1 key and is ready for separate Sol review.
- Internal checkpoints:
  1. Extract shared payload builders and prove legacy production payload parity.
  2. Complete both harness arms, preparation/evidence/lifecycle and fake-driven
     failure coverage.
  3. Run all focused non-SUMO checks, then fingerprint and freeze exactly once.

Allowed files:

- `tools/benchmark_persistent_sumo.py` (new)
- `tests/test_benchmark_persistent_sumo.py` (new)
- `run_scenario.py` (behavior-preserving shared payload-builder extraction only)
- `tests/test_scenario.py` (shared-builder parity/regression tests only)
- `validation/persistent_sumo_campaign_v1.json` (new static contract only)
- `IMPROVEMENT_PLAN.md` (Phase 7 selected-experiment freeze note only)
- `TASKS.md` (Luna handoff state triple only)
- `AGENT_NOTES.md` (current handoff plus one dated entry)

Read-only context:

- `ARCHITECTURE.md` simulation, provenance, job and publication boundaries
- `serve.py` and directly imported `traffic_sim/` helpers
  for command, parsing, digest, health, integrity and isolation semantics
- `tools/benchmark_speed.py`, `tools/benchmark_online_latency.py`,
  their focused tests, and the `TestClose`/`TestCancel` sections of
  `tests/test_serve.py`
- `sumo/net.net.xml`, `sumo/demand_meta.json`, and the active calibrated
  q50/q10/q90 route files for read-only fingerprints and identity fields only
- `IMPROVEMENT_PLAN.md` Phase 7 selected C1 boundary and reviewed
  LUNA-PERF-18 handoff; no raw campaign evidence or outcomes
- Official Eclipse SUMO TraCI/control/output documentation, including the
  facts that `--end` is ignored in server mode, `simulation.load` reloads with
  command-line options, and `close` shuts down SUMO

Acceptance criteria:

1. Importing the module, loading/validating the contract, requesting CLI help
   and running every focused test neither imports `traci`/libsumo nor starts
   SUMO, opens a socket, creates a campaign run/outcome, or writes outside a
   supplied pytest temporary directory. TraCI is imported lazily only after
   an explicit execute request passes complete contract and environment
   preflight. The CLI requires an exact contract path and an explicit
   `--execute`; `--validate-contract-only` is side-effect-free.
2. Extract shared `build_scenario_payload` and `build_trajectory_payload`
   seams from production assembly without changing output keys, values,
   omission rules, ordering, reconciliation, atomic-write behavior or
   publication paths. `run_scenario.main()` and
   `publish_trajectories_from_vehroute()` must call those helpers. Static
   fixtures must prove the helper results exactly match the legacy production
   shapes for baseline, closure and trajectory cases, including optional
   multi-day data; the harness must call the same helpers rather than duplicate
   or reduce their payloads.
3. Implement strict schema/ID/content-key validation using the canonical
   `sha256(json.dumps(payload_without_content_key, sort_keys=True,
   separators=(",", ":")).encode())` rule. Unknown/retired IDs, duplicate or
   missing fields at every bound object level, renamed contracts, key mismatch,
   source/input fingerprint drift, demand identity drift, SUMO-version drift,
   platform drift and any unbound execution option fail before run-directory
   creation, TraCI import, socket allocation or process spawn. JSON duplicate
   keys must be rejected during decoding, not silently overwritten.
4. Freeze exactly two arms and ten ordered queries:
   `baseline, closure, baseline, closure, baseline, closure, baseline,
   closure, baseline, closure`. Each query has a fresh-subprocess reference
   and persistent result; every query uses three concurrent slots with
   `member_0→seed 1000/q50`, `member_1→seed 1001/q10`,
   `member_2→seed 1002/q90`. Closure is
   `26842525_26355153_0`, 00:00–24:00; simulation ends at 90,000 seconds.
   There are no retries, extra trials, implicit warmups or reordered cases.
5. One command/option builder is authoritative for both arms and matches
   `run_scenario.run_sumo` semantics: mesoscopic and junction-control flags,
   exact net/routes/additionals, seed, begin/end, no-step-log/no-warnings,
   ignore-route-errors, statistic/summary/edgeData and trajectory outputs,
   private cwd and timeout. Only the TraCI server/connect option may differ.
   Each reference query launches exactly three fresh per-seed children once,
   never three full `run_scenario.py` scenario orchestrations. Focused tests
   capture commands, cwd, output ownership, concurrency and timeout without
   invoking SUMO.
6. Query preparation is shared and result-faithful: create each private
   edgeData/closure additional, call production closure-variant filtering for
   q50/q10/q90, propagate the selected route to both arms, parse required
   measured-zero closed edges, and compute per-seed active-closure throughput
   with the production helper. No query may reference a file that was not
   created. All recurring preparation, aggregation, payload construction,
   digesting and validation is inside the corresponding query wall for both
   arms; only one-time pool startup is outside.
7. The pool creates exactly three external SUMO members, one socket and private
   work directory per fixed seed slot. Startup/connect/readiness is measured
   and reported separately, `pool_warmup_queries=0`, and no member crosses
   slots. Every timed persistent query includes its `simulation.load`, target
   advance, required output finalization/readability, parsing and validation.
   Because TraCI ignores `--end` and `close` terminates SUMO, the harness must
   keep a member reusable without excluding shutdown, output flush/finalize,
   an extra reload, or equivalent recurring work from timing. If the official
   lifecycle cannot satisfy that boundary, Luna stops with source-backed
   blocker evidence rather than weakening or freezing the contract.
8. Per-query timeout is 600 seconds. A member error, EOF, timeout or invalid
   output retires, terminates and reaps that member and records the event; the
   affected seed/query may run once through the current fresh-subprocess cold
   fallback, but any fallback/member fault makes the experiment ineligible to
   pass. `try/finally` cleanup closes sockets and terminates/reaps every member
   on success, validation failure, exception, timeout, keyboard interruption
   and partial startup. Construction must kill/reap a process if connection
   fails before pool registration; graceful-close timeout must kill then reap;
   reference children have an equivalent query-wide abort path. Tests inject
   every class and prove no reusable/orphaned fake member remains.
9. Every persistent query is paired to the fresh reference for that same
   ordered query. Reuse the established semantic canonicalization rules and
   require equal `scenario_digest` and `trajectory_digest`; require per seed
   loaded==inserted with collisions, teleports, running_at_end and
   waiting_at_end all zero, plus `verified_clean` for every closure. Final
   parsed production statistics are authoritative for health; live TraCI
   counters may be diagnostic but cannot substitute incompatible last-step
   counters. Scenario digests cover the complete shared production payload
   and trajectory digests cover the complete shared trajectory payload.
   Missing, duplicate, extra, cross-paired or malformed evidence fails closed.
10. Report exact per-query parallel wall times, separately reported pool
   startup, member/fallback events and complete proof rows. The latency sample
   is only the five persistent closure-query wall times. Compute p95 by one
   frozen method shared with the reference arm. PASS requires every equality,
   health and integrity gate, no member fault/fallback, persistent p95
   `<=10.0` seconds, and improvement of at least `0.04` versus subprocess p95.
   A digest/gate miss cannot be converted to warning, diagnostic success or
   release evidence.
11. The contract binds schema/experiment ID and freeze timestamp; harness and
   relevant production-source fingerprints; network hash/build; demand build
   ID/key and q50/q10/q90 hashes; expected SUMO version/platform; exact option
   template and outputs; arm/query order; seed/member/variant mapping; workers
   and pool size; timeout; timer/finalization semantics; startup/warmup policy;
   lifecycle/fallback/cleanup rules; trial count; all gates and report schema.
   After code/tests settle, recompute fingerprints and content key once.
12. The harness imports TraCI and validates all executable dependencies only
    after contract/environment identity succeeds but before campaign-root
    creation, port allocation or process spawn. It writes only to a caller-
    supplied, initially absent private campaign root after preflight, preserves
    that evidence tree on terminal
    completion, and never publishes a scenario, trajectory, manifest, cache,
    state snapshot or `latest_*` pointer. It must refuse an existing root and
    path escape/symlink targets. Contract-only validation creates nothing.
13. Phase 7 records the frozen ID/key and explicitly says it is unexecuted,
    unapproved and not adoption authority. The contract carries no outcome,
    measured value or approval. The prior PERF-16 key/approval is spent and
    invalid; actual preflight/execution/outcome inspection needs a new Sol task
    and fresh exact-key user approval after this task passes Sol review.
14. No production, API, architecture, demand, scenario, frozen campaign v1–v6,
    release or publication behavior changes. The asynchronous `/api/close`
    path remains the product path and no performance claim is made.

Focused non-SUMO checks:

```text
python3 -m pytest -q tests/test_scenario.py tests/test_benchmark_persistent_sumo.py
python3 -m pytest -q tests/test_benchmark_speed.py tests/test_scenario_timing.py tests/test_benchmark_online_latency.py
python3 -m pytest -q tests/test_serve.py::TestClose tests/test_serve.py::TestCancel
python3 tools/benchmark_persistent_sumo.py --campaign validation/persistent_sumo_campaign_v1.json --validate-contract-only
git diff --check -- run_scenario.py tests/test_scenario.py tools/benchmark_persistent_sumo.py tests/test_benchmark_persistent_sumo.py validation/persistent_sumo_campaign_v1.json IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md
```

Forbidden work:

- Do not run/import SUMO, libsumo or TraCI; allocate/listen/connect to a socket;
  run a scenario/campaign/server/job; or create, inspect, enumerate, copy,
  repair or delete an outcome, report, sidecar, state snapshot, run tree,
  release artifact or prior campaign evidence. Mock/fake objects only.
- In `run_scenario.py`, do not change computation, validation, CLI, timing,
  process launch, closure preparation, publication destination, JSON shape/
  values/order/omission, manifest/cache/state behavior or error thresholds;
  only extract and call the shared payload builders. In `tests/test_scenario.py`,
  preserve all existing user-owned edits and add only parity/regression tests.
- Do not edit `serve.py`, `ARCHITECTURE.md`, `traffic_sim/`, other existing
  tests/tools/contracts, frozen v1–v6 artifacts, production defaults, API
  behavior, demand, seeds/variants/fidelity, closure semantics, output formats
  or phase schema.
- Do not reopen/refreeze v1–v6 or seed-parallel v7, change
  `CURRENT_CAMPAIGN_ID`, add a production pool/supervisor, adopt persistent
  SUMO, or present mocked/static validation as benchmark evidence.
- Do not weaken validation, provenance, semantic, health, closure-integrity,
  recall, regret, failure-recall, adoption, release, or publication gates.
- Do not build/warm demand, start horizon warming, merge Stage B, deploy,
  release, publish, or use the spent PERF-16 approval/key for any purpose.

Approval gate:

- `NOT_REQUIRED` for the named code/static-contract edits, source reads and
  fully mocked/non-SUMO checks. Any invocation or import of SUMO/libsumo/TraCI,
  socket/process preflight, campaign execution, or creation/inspection of an
  outcome requires a separate Sol task and fresh user approval matching the
  exact immutable key frozen and approved after this task's Sol review.

Terminal handoff conditions:

- Complete all three internal checkpoints autonomously and stop for `SOL
  REVIEW` only when the behavior-preserving helper extraction, complete
  harness, tests, frozen static contract, documentation note and every focused
  check pass. Stop earlier for any need to invoke SUMO/libsumo/TraCI, open a
  socket/process, inspect outcomes, alter production behavior/artifact
  contracts, hide recurring work outside the timer, materially expand scope,
  or after three failed serious approaches with the required evidence.

<!-- COMPLETED_TASK_LUNA_PERF_19_END -->

<!-- COMPLETED_TASK_LUNA_REL_01_START -->
## COMPLETED_TASK

### LUNA-REL-01 — Establish an opaque-only releasable worktree boundary

- Task ID: `LUNA-REL-01`
- Revision: `2`
- Owner: `Luna High`
- Status: `DONE — Sol approved the opaque-only v2 release boundary and complete
  guarded focused verification`
- Delivery size: `NARROW`
- Scope: Replace the rejected v1 boundary with an opaque-only v2 record built
  exclusively from Sol's exact safe-file allowlist. Delete v1 without reading
  it. Hash and classify only allowed source, tests and static contracts; list
  workflow documents without hashes because transitions mutate them. Represent
  campaign reports, outcome directories and runtime roots only by generic
  exclusion patterns—never open, hash, enumerate, count or derive facts from
  them. Run only statically proven non-SUMO tests. Recommend one integration
  slice with a workable immutable-file hash rule. Do not modify product code,
  execute campaigns, commit, publish, release, merge, warm or deploy.
- Completion outcome: one valid v2 record binds the exact 29 immutable
  release-candidate files, identifies four mutable workflow documents, defines
  opaque exclusion patterns without observing their members, records safe
  focused verification, and proposes one executable next integration slice.
- Internal checkpoints: `NOT_APPLICABLE`

Allowed files:

- `validation/release_candidate_boundary_v1.json` (delete without reading)
- `validation/release_candidate_boundary_v2.json` (new evidence record only)
- `TASKS.md` (Luna terminal state triple only)
- `AGENT_NOTES.md` (current handoff plus one dated entry)

Exact read-only release-candidate allowlist:

- Production/verification source: `run_scenario.py`,
  `run_monthly_closure_search.py`, `run_monthly_proxy_validation.py`,
  `traffic_sim/simulation/monthly_proxy.py`,
  `traffic_sim/simulation/monthly_search.py`,
  `traffic_sim/simulation/proxy_validation.py`,
  `tools/benchmark_speed.py`, `tools/benchmark_online_latency.py`,
  `tools/benchmark_persistent_sumo.py`
- Tests: `tests/test_scenario.py`, `tests/test_scenario_timing.py`,
  `tests/test_benchmark_speed.py`, `tests/test_benchmark_online_latency.py`,
  `tests/test_benchmark_persistent_sumo.py`, `tests/test_monthly_search.py`,
  `tests/test_proxy_validation.py`, `tests/test_heldout_v4_freeze.py`
- Static contracts: `validation/online_latency_benchmark_v1.json`,
  `validation/scenario_phase_profile_campaign_v1.json` through
  `validation/scenario_phase_profile_campaign_v6.json`,
  `validation/persistent_sumo_campaign_v1.json`,
  `validation/persistent_sumo_campaign_v2.json`,
  `validation/heldout_v4_selection.json`,
  `validation/monthly_proxy_policy_v4.json`,
  `validation/monthly_proxy_manifest_v4.json`
- Mutable workflow documents, readable but never hash-bound:
  `AGENTS.md`, `TASKS.md`, `AGENT_NOTES.md`, `IMPROVEMENT_PLAN.md`
- `git status --short`, current branch and `HEAD`; do not expand, count or
  record entries matching the forbidden patterns below
- `ARCHITECTURE.md` product/release boundaries and `IMPROVEMENT_PLAN.md`
  current status and Phase 7 conclusion, using targeted excerpts only

Forbidden work:

- Do not read, parse, hash, stat, enumerate, count, summarize, diff or otherwise
  inspect `validation/release_candidate_boundary_v1.json`,
  `validation/scenario_phase_profile_report_*.json`,
  `validation/*_outcome/`, `validation/online_latency_baseline_v1/`, `runs/`,
  `sumo/`, scenario staging, or any other campaign report/outcome/runtime root.
  Do not record concrete members, counts, metrics, sizes, hashes, attribution
  or existence claims for those patterns. The patterns alone are the boundary.
- Do not edit any production source, test, static contract, workflow protocol,
  architecture/priority document, generated evidence or user-owned file.
- Do not run SUMO, TraCI, libsumo, a live server/API job, executable campaign,
  demand generation, horizon warming, scenario publication, deployment,
  release activation, Stage-B merge, commit, push or destructive cleanup.
- The one explicitly authorized deletion of the invalid v1 boundary record is
  the sole exception to destructive-work prohibition.
- Do not treat an old approval, campaign result or diagnostic replay as
  authority for execution or release. Do not weaken fidelity, provenance,
  recall, regret, failure-recall, health, integrity or latency gates.
- Do not read the rejected v1 record or reuse its classifications, counts,
  hashes, claims or recommendation.

Acceptance criteria:

1. Revalidate the single current markers and exact task/revision/state/owner/
   next-action/transition/direction agreement; do not edit `ACTIVE_TASK`.
2. Delete v1 without first opening, hashing, parsing, statting or diffing it.
   Create only v2; v2 carries task `LUNA-REL-01`, revision `2`, current branch
   and `HEAD`, and the exact user direction recorded below.
3. V2 contains exactly the 29 allowlisted source/test/static-contract entries.
   Each has classification, disposition, role, provenance and current SHA-256.
   No other file is hashed or classified as a concrete candidate.
4. The four workflow documents appear in a separate mutable list with no hash
   and an explicit transition-mutation reason. V2 does not hash itself.
5. V2 contains only these opaque exclusion patterns:
   `validation/scenario_phase_profile_report_*.json`,
   `validation/*_outcome/`, `validation/online_latency_baseline_v1/`, `runs/`,
   `sumo/`, and scenario staging. It contains no member list, member count,
   existence assertion, attribution or derived fact about any pattern.
6. Before tests, prove from the selected test sources that the exact focused
   command cannot import/start SUMO/TraCI/libsumo, use a listening socket or
   HTTP, inspect a forbidden pattern, publish, or mutate live state. Any
   uncertainty makes the command `NOT_RUN`.
7. The safe focused suite covers every allowlisted source path. Record its
   exact command and result; do not claim full-suite or release verification.
8. The recommended next task stages/commits only the 29 hash-bound immutable
   candidates plus reviewed mutable documentation and `.gitignore`; immutable
   files must match v2 hashes, while mutable workflow files are verified by
   scoped diff and marker checks—not stale hashes. No push/release authority.
9. Do not restate campaign outcomes or performance metrics. State only that
   synchronous latency remains unresolved and async remains the supported path,
   as already recorded by the priority authority.
10. All pre-existing files except the explicitly discarded v1 record remain
    untouched; JSON validity, exact allowlist, safe hashes, markers and scoped
    whitespace checks pass; Luna changes only its legal terminal state triple
    and handoff, then stops.

Focused checks:

```text
git status --short
python3 -m json.tool validation/release_candidate_boundary_v2.json
<safe focused pytest commands selected and justified by the boundary audit>
<v2 exact-allowlist and 29-file SHA-256 verifier>
git diff --check -- TASKS.md AGENT_NOTES.md validation/release_candidate_boundary_v2.json
```

Approval gate:

- `NOT_REQUIRED`
- Exact unblock/direction message received (JSON-escaped verbatim):

  ```text
  "I direct Sol to discard the LUNA-REL-01 boundary evidence and create a fresh opaque-only revision. Do not open, hash, count, or inspect campaign reports\n    > or outcome directories. No outcome inspection is approved"
  ```

- User-message date: `2026-07-25`
- Sol recorder/date: `Sol High / 2026-07-25`
- This is authority to discard v1 and build the opaque-only v2 record only.
  It explicitly grants NO campaign-report or outcome inspection.

Terminal handoff conditions:

- Hand off in `READY_FOR_SOL_REVIEW` when the bounded boundary/evidence package
  and all safely runnable focused checks are complete.
- Any attempt or need to read/hash/stat/enumerate/count a forbidden pattern,
  any allowlist ambiguity, inability to prove a test non-mutating, architecture
  or artifact-contract change, or required execution/release authority is a
  terminal blocker. Stop before the action.
- This NARROW task does not implement or commit the recommended next slice.

<!-- COMPLETED_TASK_LUNA_REL_01_END -->

<!-- COMPLETED_TASK_LUNA_WARM_09_START -->
## COMPLETED_TASK

### LUNA-WARM-09 — Correct preserved-accumulator warming and freeze v4

- Task ID: `LUNA-WARM-09`
- Revision: `2`
- Owner: `Luna High`
- Status: `CONCLUDED — APPROVED by Sol review; non-executable`
- Delivery size: `EXTENDED`
- Objective and scope: Replace v3's refuted boundary-ledger offset with
  preserved-accumulator accounting: completed prefix trip aggregates plus
  resumed trip aggregates. Make the TraCI snapshot command actually apply the
  cache identity's RNG and 16-digit state settings, and harden its process
  lifecycle. Advance all warm evidence, identity diagnostics, runner and
  harness contracts fail-closed; legacy v1/v2 evidence must miss. Freeze one
  canonical, unexecuted v4 paired-validation manifest whose hypothesis is that
  default state serialization caused v2's residual. Keep warming default-OFF.
  Run no SUMO and inspect no outcome or `runs/` evidence.
- Completion outcome: a process-free, campaign-ready v4 warm path has no ledger
  double-count, its actual snapshot command matches its recorded identity, all
  focused contracts pass, and a no-overwrite v4 manifest is ready for one final
  paired cold/warm approval decision.
- Internal checkpoints:
  1. Snapshot integrity: exact-time TraCI save applies RNG/precision settings,
     uses bounded connect/run/reap behavior, rejects nonzero exits, and records
     bounded state facts without an unbounded vehicle ledger.
  2. Accounting integration: prefix evidence v3, reconstruction, diagnostics,
     cache identity, monthly runner and validation harness all implement
     preserved-accumulator aggregate accounting; old schemas fail closed.
  3. Freeze: v4 source/contract fingerprints, hypothesis/refutation boundary,
     focused tests, canonical manifest verification, and documentation all pass
     before the single Luna handoff.
- Allowed files and resources:
  - Read-only: relevant tracked source/test/document excerpts and the reviewed
    LUNA-WARM-08 keys and conclusions already recorded in current authorities.
  - Create/edit:
    `traffic_sim/simulation/warm_state_cache.py`,
    `traffic_sim/simulation/warm_state_boundary.py`,
    `traffic_sim/simulation/monthly_warm_state.py`,
    `traffic_sim/simulation/monthly_sumo.py`,
    `run_monthly_warm_state_validation.py`,
    `tools/freeze_monthly_warm_state_v4.py` (new),
    `validation/monthly_warm_state_manifest_v4.json` (new),
    `tests/test_warm_state_cache.py`, `tests/test_warm_state_boundary.py`,
    `tests/test_monthly_warm_state.py`, `tests/test_monthly_sumo.py`,
    `tests/test_monthly_warm_state_freeze.py`,
    `tests/test_monthly_warm_state_v2_freeze.py`,
    `tests/test_monthly_warm_state_v3_freeze.py`,
    `tests/test_monthly_warm_state_v4_freeze.py` (new), `ARCHITECTURE.md`,
    `IMPROVEMENT_PLAN.md`, `TASKS.md`, and `AGENT_NOTES.md`.
  - Temporary: test/freeze scratch outside `runs/`, removed before handoff.
- Forbidden work:
  - Do not run or import real SUMO/TraCI, open sockets, launch subprocesses from
    tests, generate demand, warm a horizon, or execute any campaign/preflight.
  - Do not inspect, stat, enumerate, hash, parse, copy, or mutate any `runs/`,
    outcome, report, archived-demand, cache, or campaign root.
  - Do not edit v1/v2/v3 manifests or freeze tools, network/demand/policy/
    threshold files, `run_scenario.py`, product/API/UI/release files, or active
    release. Source drift may make old freezes unadoptable; never repair them.
  - Do not weaken exact cold/warm semantic comparison, provenance, cache-miss,
    release, or publication gates. Do not activate warming, publish cache
    material, adopt Stage B, deploy, release, push, or publish.
- Acceptance criteria:
  1. Production warming never adds a boundary `timeLoss` offset. The sole
     objective rule is completed-prefix aggregate plus resumed aggregate,
     because the approved mechanism evidence shows resumed tripinfo already
     carries the accumulator. Trip count follows the same disjoint aggregate
     rule; every other field retains its explicit existing rule.
  2. Advance prefix evidence to `monthly_prefix_evidence_v3`. Remove production
     dependence on boundary/completed per-vehicle maps; validate exact field
     sets, values, warm point and snapshot facts on write and read. v1/v2 or
     unknown evidence is a cache miss/cold fallback, never reinterpreted.
  3. The prefix snapshot command contains exactly one
     `--save-state.rng true` and `--save-state.precision 16`, sourced from the
     same cache constants the identity records. It must not add global
     `--precision` or alter cold/post output semantics. Tests prove actual argv,
     identity and stored snapshot facts agree.
  4. Refactor the controller around an exact-time snapshot, not a ledger. It
     must use injected/lazy process, TraCI and socket seams; enforce bounded
     connect/simulation/reap behavior, stderr-file capture, observed zero return
     code and kill/reap cleanup without masking the primary error.
  5. Update monthly runner, cache restore/bootstrap, canonical split
     diagnostics, semantic validation and harness wiring end-to-end. Invalid
     warm state/evidence remains a recorded cold fallback; no failure may lose
     an observation or silently reuse legacy evidence.
  6. Property and integration tests reproduce the old ledger double-count,
     prove v4 does not apply it, prove completed+resumed exact aggregation,
     detect omitted/duplicated state flags and mismatched identity/facts, and
     exercise default production wiring rather than injection-only seams.
  7. Create a deterministic, canonical, no-overwrite
     `monthly_warm_state_v4` freeze. Bind source fingerprints, schemas, exact
     comparison policy, reviewed preserved-accumulator conclusion, state
     settings, schedule/seed identities and the hypothesis that default state
     serialization caused v2's gap. State that any mismatch refutes the
     hypothesis; no equivalence or speedup is claimed before execution.
  8. Old v1/v2/v3 artifacts remain byte-untouched and unadoptable after source
     drift. Their regression tests must distinguish preserved artifact bytes
     from expected live-source mismatch rather than rewriting history.
  9. Update architecture and priorities with the selected v4 design, why v3 is
     rejected, the still-unproven causal hypothesis, default-OFF status, and
     the exact remaining gate: one fresh approved paired campaign.
  10. Self-audit every criterion and complete all three internal checkpoints
      before one handoff. Report exact checks, schemas, source/manifest keys,
      old-freeze disposition and blockers.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_cache.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v4.py --write`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v4.py --verify`
  - `python3 -m json.tool validation/monthly_warm_state_manifest_v4.json`
  - process-free import/argv guards proving no real simulator, socket or process
    starts during the focused checks
  - `git diff --check -- traffic_sim/simulation/warm_state_cache.py
    traffic_sim/simulation/warm_state_boundary.py
    traffic_sim/simulation/monthly_warm_state.py
    traffic_sim/simulation/monthly_sumo.py
    run_monthly_warm_state_validation.py tools/freeze_monthly_warm_state_v4.py
    validation/monthly_warm_state_manifest_v4.json
    tests/test_warm_state_cache.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py ARCHITECTURE.md
    IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED` — this task is strictly process-free source,
  tests, documentation and creation/verification of one unexecuted tracked
  manifest. It authorizes no real SUMO/TraCI/socket or child-process execution
  and no evidence or outcome inspection.
- Terminal handoff conditions:
  - Hand off after all checkpoints and focused checks pass, or stop on any need
    for real SUMO/TraCI/socket or child-process execution,
    `runs/`/outcome/archive access, evidence repair, old artifact mutation,
    broader product/release changes, a new artifact contract outside v4, or
    three recorded serious failed approaches.
<!-- COMPLETED_TASK_LUNA_WARM_09_END -->

<!-- ARCHIVED_LUNA_WARM_16_START -->
## ACTIVE_TASK

### LUNA-WARM-16 — Execute the one-time v9 cold-versus-warm campaign

- Task ID: `LUNA-WARM-16`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — APPROVED honest failed experiment; v9 campaign spent,
  warming executed but equivalence failed and no cache was published`
- Delivery size: `STANDARD`
- Objective and scope: After exact user approval, run the frozen v9 paired
  cold-versus-warm campaign once and without resume. Validate immutable identity,
  focused tests, active SUMO/TraCI, network, exact archived demand and keyed-root
  absence before execution. Execute only key `cad9c072…`, allow validation-only
  provisional/cache material inside its keyed root, and inspect/recompute only
  that task-created evidence. Accept an honest pass or fail when provenance,
  coverage, warm-attempt and publication rules are internally consistent. Do
  not inspect other runs/outcomes, repair/rerun, change code/contracts, generate
  demand, activate product warming, adopt, release, deploy or publish.
- Completion outcome: one terminal, reviewable v9 campaign record at the exact
  keyed root, with cold/warm behavior and performance reported honestly; either
  complete exact equivalence with validated in-root cache publication or a
  fail-closed record proving no cache publication. No second attempt.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Before approval, read only tracked source/tests/manifests needed to verify
    this plan. Do not inspect the keyed root, archive or any `runs/` path.
  - After the exact approval is recorded, read the five exact archive files at
    `runs/demand-20260721-222017-41bc682a-bbe1/`: `demand_meta.json`,
    `manifest.json`, `calibrated.rou.xml`, `calibrated_v1.rou.xml`, and
    `calibrated_v2.rou.xml`, solely for canonical-demand/hash preflight.
  - Read/validate `sumo/net.net.xml`, the resolved active SUMO home/executable,
    and the installed TraCI origin/API solely for mandatory preflight.
  - Check absence, then create/execute/inspect only
    `runs/monthly-warm-state-validation/cad9c072a0ca6f90b11bd6342a603337eeaceacc457cd0016a16a3d9fa04e7b2`.
    Task-created workspaces, provisional states and validation-only published
    cache material must remain inside that root.
  - Edit `TASKS.md` and `AGENT_NOTES.md` only for terminal handoff. No product
    or source edit is authorized.
- Forbidden work:
  - Before approval is recorded: no archive/root check, installed TraCI import,
    executable invocation, SUMO, socket, subprocess, campaign or outcome access.
  - Do not edit/regenerate any source, test, tool, manifest, policy, threshold,
    architecture/improvement, product/API/UI, release or deployment file; do not
    create v10 or alter v9's key/bytes.
  - Do not inspect, enumerate, stat, hash or parse any other `runs/`, report,
    outcome, campaign or cache root. Do not reuse another campaign's evidence.
  - The campaign is non-resumable: no rerun, resume, repair, root deletion,
    evidence mutation or second invocation after execution begins, whether it
    passes, fails, crashes or is interrupted.
  - Do not generate demand/horizons, warm outside the keyed validation root,
    activate product warming/Stage B, adopt the cache into product state, mutate
    active release state, deploy, release or publish.
- Acceptance criteria:
  1. Sol records an exact matching user approval for task/revision, v9 key,
     keyed root, preflight, single execution and bounded inspection before any
     covered action. A mismatch or missing approval stops all work.
  2. v9 still verifies byte-for-byte with tool/test/manifest hashes
     `23bfb8c0…`/`997a93dd…`/`556e6a6f…`, unchanged harness/generic pointers,
     canonical key `cad9c072…`, exact source fingerprints and status
     `frozen_unapproved_unexecuted` before execution.
  3. The revision-7 focused suite passes process-free with both approved v2
     archive readers deselected and zero forbidden attempt. No check reads any
     `runs/` path before the approved preflight begins.
  4. Mandatory pre-root preflight proves: active `<sumo_home>/bin/sumo` is the
     executable used; TraCI resolves inside that home's `tools/traci` with the
     exact required API; `sumo/net.net.xml` hashes to `68ecde39…`; and the five
     named archive files match all frozen hashes, demand key `2ac04275daabe93c`,
     three variants and 480 intervals. Any failure is terminal before root.
  5. The exact keyed root is absent immediately before execution. The harness
     is invoked once with the v9 manifest, `--execute`, and approval token equal
     to the full v9 key; no alternate flags, key, manifest, root or second call.
  6. Inspect only the task-created keyed root. `equivalence_record.json` has a
     recomputable canonical key, exact manifest key, complete expected identity
     coverage and internally consistent cold/warm comparisons, attempts,
     diagnostics, performance, publication decision and observation failures.
  7. A pass requires three exact semantic matches, complete warm execution
     evidence and exactly the expected validation-only cache entries inside the
     root. A fail requires honest reasons, no published cache, and
     `NO_CACHE_PUBLISHED`; either internally consistent terminal result satisfies
     the experiment, but only pass supports later adoption consideration.
  8. No file outside the keyed root and terminal workflow notes changes. Record
     interruption/preflight failure honestly; never repair or retry. No product
     behavior, persistent warming outside the root, adoption or release claim.
- Focused checks:
  - v9 preserved-hash/canonical source check over the frozen tool, test,
    manifest, harness and generic current suite
  - guarded exact suite:
    `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_sumo_runtime.py tests/test_warm_state_cache.py
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py
    tests/test_monthly_sumo.py tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py
    tests/test_monthly_warm_state_v5_freeze.py
    tests/test_monthly_warm_state_v6_freeze.py
    tests/test_monthly_warm_state_v7_freeze.py
    tests/test_monthly_warm_state_v8_freeze.py
    tests/test_monthly_warm_state_v9_freeze.py
    --deselect=tests/test_monthly_warm_state_v2_freeze.py::TestFrozenV2Contract::test_the_five_approved_archive_files_are_bound
    --deselect=tests/test_monthly_warm_state_v2_freeze.py::TestFrozenV2Contract::test_the_spent_v2_package_no_longer_recomposes`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v9.py --verify`
  - approved exact SUMO executable/version, TraCI origin/API, network hash,
    five-file archived-demand contract/hash and keyed-root-absence preflight
  - frozen execution:
    `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v9.json --execute --approval-token
    cad9c072a0ca6f90b11bd6342a603337eeaceacc457cd0016a16a3d9fa04e7b2`
  - bounded keyed-root inventory/hash/JSON canonicality, production evaluator
    recomputation and cache-publication consistency audit
  - `git diff --check -- TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate:
  - `REQUIRED — RECORDED`
  - Exact scope/key/root required: one non-resumable v9 paired SUMO/TraCI
    campaign at content key
    `cad9c072a0ca6f90b11bd6342a603337eeaceacc457cd0016a16a3d9fa04e7b2`
    and artifact root
    `runs/monthly-warm-state-validation/cad9c072a0ca6f90b11bd6342a603337eeaceacc457cd0016a16a3d9fa04e7b2`,
    including frozen/process-free checks, exact SUMO/TraCI/network/archive/root
    preflight, one execution, in-root validation-only cache material, and
    inspection/recomputation only inside that task-created root.
  - Exact user message:
    “I explicitly approve LUNA-WARM-16 revision 1 to run the one-time
    non-resumable monthly_warm_state_v9 paired cold-versus-warm SUMO/TraCI
    campaign at content key
    cad9c072a0ca6f90b11bd6342a603337eeaceacc457cd0016a16a3d9fa04e7b2
    and artifact root runs/monthly-warm-state-validation/
    cad9c072a0ca6f90b11bd6342a603337eeaceacc457cd0016a16a3d9fa04e7b2,
    including the guarded focused process-free checks with both named legacy
    archive readers deselected, canonical manifest/source checks, exact SUMO
    executable, TraCI origin/API, network and five-file archived-demand preflight,
    keyed-root absence check, one frozen execution, task-created workspaces and
    validation-only cache material inside that root, and inspection and
    production-consistency recomputation only within that task-created root. No
    rerun, resume, repair, other runs/outcome/report inspection, demand or horizon
    generation, persistent warming outside that root, product activation, Stage
    B, adoption, release mutation, deployment or publication is approved.”
  - User-message date: `2026-07-31`
  - Sol recorder/date: `Sol High / 2026-07-31`
- Terminal handoff conditions:
  - Hand off once after preflight failure or the single terminal campaign and
    bounded audit; an honest fail is evidence, not authorization to retry.
  - Stop on approval/key/root/hash/source mismatch, pre-existing root, preflight
    failure, interruption, access outside exact boundaries, unexpected mutation,
    need for repair/rerun/contract expansion or three serious failed approaches.
    Do not clean up or resume a partial root.
<!-- ARCHIVED_LUNA_WARM_16_END -->

<!-- COMPLETED_TASK_LUNA_WARM_17_START -->
## ACTIVE_TASK

### LUNA-WARM-17 — Freeze a population-level warm time-loss diagnostic

- Task ID: `LUNA-WARM-17`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — APPROVED by Sol review on 2026-07-31`
- Delivery size: `STANDARD`
- Objective and scope: Build, test and freeze an unapproved, unexecuted
  synthetic diagnostic that reproduces the production cold/prefix/resume
  boundary with multiple vehicles. It must compare per-vehicle cold and split
  tripinfo across completed-before-boundary, active-at-boundary and
  depart-after-boundary cohorts, with production-precision and high-precision
  control arms. Classify population partition error, output quantization,
  save/load integration drift or exact agreement without weakening production
  equivalence. Bind the tracked network, production metric producers/combiner,
  fixture, command shapes, source hashes and exact future output root into one
  canonical contract. Do not execute SUMO or inspect prior evidence.
- Completion outcome: one process-free tested diagnostic tool and one canonical
  unapproved/unexecuted contract with an immutable key and exact future outcome
  root, capable of producing a per-vehicle, cohort-attributed verdict in one
  later approved synthetic execution.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - Create `tools/diagnose_warm_state_population_semantics.py`.
  - Create `tests/test_warm_state_population_semantics.py`.
  - Create
    `validation/warm_state_population_semantics_v1_contract.json`.
  - Read, but do not edit, `sumo/net.net.xml`, `run_scenario.py`,
    `traffic_sim/simulation/metrics.py`,
    `traffic_sim/simulation/warm_state_boundary.py`,
    `traffic_sim/simulation/monthly_warm_state.py`,
    `traffic_sim/simulation/monthly_sumo.py`,
    `tools/diagnose_warm_state_time_loss_semantics.py`, and their focused tests.
  - Edit `TASKS.md` and `AGENT_NOTES.md` only for the terminal handoff.
- Forbidden work:
  - No `runs/` access of any kind and no prior report, outcome, campaign,
    archive, cache or validation outcome inspection.
  - No SUMO or TraCI execution/import/probe/connection, libsumo, socket or child
    process from the diagnostic or tests; the authorized pytest/verification
    commands themselves are the only process-free check runners.
  - Do not execute, preflight or create the future diagnostic outcome root; do
    not reuse or mutate the v2 diagnostic outcome or any v9 campaign evidence.
  - Do not edit production behavior, existing freeze tools/manifests/tests,
    thresholds, policy, architecture/improvement, product/API/UI, active
    release or deployment files.
  - No demand/horizon generation or warming, campaign, cache publication,
    product activation, Stage B, adoption, release, deployment or publication.
- Acceptance criteria:
  1. The tool imports process-free and exposes pure fixture, command, parsing,
     comparison, classification, canonicalization and verification helpers.
     Import and focused tests make zero TraCI/libsumo, socket and child-process
     attempts and never resolve or touch any `runs/` path.
  2. The deterministic fixture contains non-empty completed-before-boundary,
     active-at-boundary and depart-after-boundary cohorts, with enough
     simultaneous active vehicles to expose a population-scaled residual. Its
     tracked-network edge selection, vehicle identities, departures, seed,
     snapshot instant, end instant and expected cohort invariants are frozen.
  3. Frozen command shapes mirror production semantics: one uninterrupted cold
     arm, one same-process prefix capture/save, one resumed arm, and matched
     high-precision cold/resumed controls. Only declared boundary/precision/
     output differences are permitted; snapshot RNG and precision settings are
     derived from production constants, not duplicated literals.
  4. The result contract requires normal zero exits, exact cold versus
     prefix-completed-plus-resumed vehicle-set partition, boundary cohort
     membership, full tripinfo fields and per-vehicle deltas. Totals are
     recomputed from those records; aggregate-only claims are rejected.
  5. Classification is mutually exclusive and fail-closed: it distinguishes
     population partition error, two-decimal output quantization, persistent
     high-precision save/load drift, exact agreement and inconclusive evidence.
     A correction recommendation is emitted only when the observations uniquely
     establish its mechanism; this diagnostic is never equivalence, performance,
     adoption or release evidence.
  6. The frozen JSON is canonical and status
     `frozen_unapproved_unexecuted`; it binds the exact tracked network hash,
     relevant producer/combiner and diagnostic source hashes, complete fixture,
     command shapes, member/result schema, future output root and recomputable
     content key. `--verify` reproduces it byte-for-byte without rewriting.
  7. Focused positive, malformed-input, adversarial-classifier, source-drift,
     command-drift, root-boundary and no-execution tests pass. No existing file
     changes except terminal workflow notes, and v9/product warming remain
     untouched and default-OFF.
- Focused checks:
  - guarded import and no-process/no-socket/no-`runs/` boundary tests in
    `tests/test_warm_state_population_semantics.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_population_semantics.py
    tests/test_warm_state_time_loss_semantics.py
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/diagnose_warm_state_population_semantics.py --verify`
  - exact changed-file and forbidden-attempt audit
  - `git diff --check -- tools/diagnose_warm_state_population_semantics.py
    tests/test_warm_state_population_semantics.py
    validation/warm_state_population_semantics_v1_contract.json TASKS.md
    AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED` — this revision is tracked, process-free
  construction and verification only. The frozen key/root grants no permission
  to preflight, import TraCI, execute SUMO or create/inspect an outcome; any
  later execution requires a new Sol task/revision and exact user approval.
- Terminal handoff conditions:
  - Hand off once when the complete tool/tests/canonical contract and all
    process-free checks pass, or on a terminal blocker under `AGENTS.md`.
  - Stop on any need for SUMO/TraCI/process/socket/`runs/`/outcome access,
    production or existing frozen-artifact edits, architecture/contract change,
    scope expansion or three serious failed approaches. Record the exact
    blocker and safest next decision; do not weaken the classifier or execute
    the diagnostic to make tests pass.
<!-- COMPLETED_TASK_LUNA_WARM_17_END -->

<!-- COMPLETED_TASK_LUNA_WARM_18_START -->
## ACTIVE_TASK

### LUNA-WARM-18 — Execute the frozen population diagnostic once

- Task ID: `LUNA-WARM-18`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — APPROVED fail-closed terminal execution failure`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, run the already-frozen
  `warm_state_population_semantics_v1` synthetic SUMO/TraCI diagnostic once and
  non-resumably. First revalidate its canonical key, bound sources, tracked
  network, six command arms, SUMO executable, exact TraCI origin/API and absent
  output root. Run the approved focused process-free checks, then invoke the
  gated runner exactly once. Inspect, hash, parse and recompute only the newly
  task-created outcome root. Record the observed classification and limitations;
  do not alter the diagnostic, production, evidence, warming or release paths.
- Completion outcome: one complete immutable exact-root diagnostic outcome with
  a recomputed per-vehicle/cohort verdict, or an honest terminal preflight or
  one-shot execution failure with no retry, repair or broadened access.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - Read only `tools/diagnose_warm_state_population_semantics.py`,
    `tests/test_warm_state_population_semantics.py`,
    `validation/warm_state_population_semantics_v1_contract.json`, its seven
    bound source files, and `sumo/net.net.xml`.
  - After matching approval only, create and inspect exactly
    `validation/warm_state_population_semantics_v1_outcome` and use only the
    diagnostic's task-created temporary workspace.
  - Edit `TASKS.md` and `AGENT_NOTES.md` only for the terminal handoff.
- Forbidden work:
  - No work at all while approval is unsatisfied.
  - No `runs/` access, archived demand, prior outcome/report/campaign/cache
    inspection, or access outside the exact new outcome root.
  - No source, test, contract, manifest, policy, threshold, product/API/UI,
    architecture/improvement, active-release or deployment edits.
  - No rerun, resume, repair or cleanup after preflight/execution failure or
    interruption; do not reuse a pre-existing root or partial root.
  - No demand/horizon generation, persistent warming or cache publication,
    product activation, Stage B, adoption, release, deployment or publication.
- Acceptance criteria:
  1. Before simulator import or execution, the frozen contract reproduces
     byte-for-byte, its content key is exactly
     `191c07264f3aed4ceccce0580c03b2fc29d7e9591f7422d24852181914466f9e`,
     all seven source fingerprints and the tracked-network hash match, and the
     exact approved output root is absent.
  2. The focused process-free suite passes without SUMO/TraCI/socket/child
     activity inside the tests and without any `runs/` or outcome access.
  3. The approved preflight proves the resolved SUMO executable exists and is
     executable and that TraCI resolves from that same SUMO installation with
     the APIs required by the frozen controller; any mismatch stops before root
     creation.
  4. Exactly one invocation of `execute(approval_token=<exact content key>)`
     runs the frozen cold, prefix, resumed and matched high-precision control
     arms. It is non-resumable and may create only the exact root and temporary
     workspace named above.
  5. Inspection remains inside the task-created root and verifies exact member
     set, SHA-256 manifest, embedded contract/key, six zero exits, six complete
     raw arm-record sets, boundary capture/state facts, whole-object comparison
     reconstruction, per-vehicle/cohort totals and recomputed verdict.
  6. The handoff reports the observed classification and recommendation without
     claiming equivalence, performance, warming readiness, adoption or release;
     product warming remains default-OFF.
  7. No source or pre-existing artifact changes occur beyond workflow notes; a
     preflight failure, nonzero/timeout, incomplete/partial root, provenance
     mismatch or validation failure is terminal and is not repaired or rerun.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_population_semantics.py
    tests/test_warm_state_time_loss_semantics.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/diagnose_warm_state_population_semantics.py --verify`
  - Approved read-only exact-key/source/network/SUMO/TraCI/root-absence preflight
    implementing acceptance criteria 1 and 3.
  - Exact single execution:
    `PYTHONDONTWRITEBYTECODE=1 python3 -c 'import sys;
    sys.path.insert(0,"tools"); import
    diagnose_warm_state_population_semantics as d;
    d.execute(approval_token="191c07264f3aed4ceccce0580c03b2fc29d7e9591f7422d24852181914466f9e")'`
  - Exact-root-only enumeration, SHA-256 verification, canonical identity and
    `validate_result` recomputation; then `git diff --check -- TASKS.md
    AGENT_NOTES.md` and `git status --short`.
- Approval gate: `REQUIRED — SATISFIED`.
  - Exact required scope/key/root: one non-resumable execution of the frozen
    six-arm `warm_state_population_semantics_v1` SUMO/TraCI diagnostic, its
    named process-free checks and preflight, task-created temporary workspace,
    and inspection/recomputation only of content key
    `191c07264f3aed4ceccce0580c03b2fc29d7e9591f7422d24852181914466f9e`
    at `validation/warm_state_population_semantics_v1_outcome`.
  - Exact quoted user message:
    > I explicitly approve LUNA-WARM-18 revision 1 to run the one-time
    > non-resumable warm_state_population_semantics_v1 synthetic SUMO/TraCI
    > diagnostic at content key
    > 191c07264f3aed4ceccce0580c03b2fc29d7e9591f7422d24852181914466f9e
    > and artifact root validation/warm_state_population_semantics_v1_outcome,
    > including the named focused process-free checks, canonical contract,
    > source and network checks, exact SUMO executable and TraCI origin/API
    > preflight, root-absence check, one frozen six-arm execution, task-created
    > temporary workspace, and inspection and recomputation only within that
    > task-created root. No rerun, resume, repair, runs/ access, archived demand,
    > other outcome/report/campaign/cache inspection, demand or horizon
    > generation, persistent warming or cache publication, product activation,
    > Stage B, adoption, release mutation, deployment or publication is approved.
  - User-message date: `2026-07-31`
  - Sol recorder/date: `Sol High / 2026-07-31`
- Terminal handoff conditions:
  - Luna hands off after the preflight fails or the single execution and
    exact-root audit completes. Stop without retry, cleanup or
    repair on any root/source/key/environment mismatch, interruption, nonzero
    arm, malformed evidence, out-of-scope access need or contract expansion.
<!-- COMPLETED_TASK_LUNA_WARM_18_END -->

<!-- COMPLETED_TASK_LUNA_WARM_19_START -->
## ACTIVE_TASK

### LUNA-WARM-19 — Harden parsing and freeze a failure-preserving v2 diagnostic

- Task ID: `LUNA-WARM-19`
- Revision: `1`
- Owner: `Luna High`
- Status: `DONE — Sol approved the unapproved/unexecuted v2 freeze on
  2026-08-01; execution is not authorized`
- Delivery size: `STANDARD`
- Objective and scope: Correct the spent v1 diagnostic process-free. Replace
  prefix-based tripinfo regex scanning with exact XML-element parsing that
  ignores SUMO configuration-header comments and option tags while preserving
  strict full-record validation. Make a future execution publish one atomic
  exact-root terminal artifact on success or failure, preserving the offending
  raw arm file, completed-arm facts, command ledger and error before temporary
  teardown. Add a gated CLI execution mode with reliable nonzero failure exit.
  Preserve v1 contract bytes, test the real header-tag counterexample and
  failure path with injected runners only, and freeze an unapproved/unexecuted
  canonical v2 contract and root.
- Completion outcome: one process-free tested v2 diagnostic whose parser accepts
  realistic SUMO headers, whose failures remain inspectable inside its one exact
  terminal root, and whose fresh canonical contract is ready for later Sol
  review without granting execution authority.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - Edit `tools/diagnose_warm_state_population_semantics.py`.
  - Edit `tests/test_warm_state_population_semantics.py`.
  - Create
    `validation/warm_state_population_semantics_v2_contract.json`.
  - Read and hash, but preserve byte-for-byte,
    `validation/warm_state_population_semantics_v1_contract.json`.
  - Read the same tracked network, bound production sources and focused tests
    authorized by LUNA-WARM-17; edit `TASKS.md` and `AGENT_NOTES.md` only for
    the terminal handoff.
- Forbidden work:
  - No SUMO or TraCI import/probe/connection/execution, libsumo, socket, child
    process from diagnostic tests, executable preflight or campaign.
  - No `runs/` access, archived demand, prior or future outcome/report/cache
    existence check, inspection, creation, mutation, cleanup, rerun or repair.
  - Do not edit the v1 contract, production behavior, existing warm manifests/
    freeze tools/tests, policy, thresholds, product/API/UI, architecture,
    improvement, active release or deployment files.
  - No demand/horizon generation, persistent warming, cache publication,
    product activation, Stage B, adoption, release, deployment or publication.
- Acceptance criteria:
  1. `parse_tripinfo` uses an XML parser and selects exact `tripinfo` elements,
     not textual prefixes. A realistic SUMO header comment containing
     `<tripinfo-output>` and `<tripinfo-output.write-unfinished>` is ignored,
     while duplicate ids, missing ids/full fields, malformed XML, nonnumeric and
     nonfinite values still fail closed.
  2. Pure parser regression tests reproduce the exact v1 counterexample and
     prove the real vehicle record is returned unchanged; no live sample,
     simulator import, process, socket or outcome is needed.
  3. A future gated execution has one exact v2 root and mutually exclusive
     terminal schemas. Success retains the fully recomputable v1 evidence.
     Failure atomically preserves contract, fixture, command ledger, completed
     arm/exit/boundary facts, error type/message/failing arm, all available raw
     arm files and a recomputable digest manifest before temporary teardown.
  4. Preflight/approval failures create no root. Once arm execution begins, an
     exception cannot be turned into success; the failure artifact is published
     once, the original failure remains visible, and rerun/reuse is refused.
  5. The CLI exposes exactly one of `--freeze`, `--verify` or gated `--execute`;
     execute requires the exact v2 content key, wrong/missing tokens fail before
     simulator import, and terminal execution failure exits nonzero.
  6. Fake-runner tests prove successful publication, parser-failure publication,
     raw offending-byte retention, exact-root/no-clobber behavior, digest and
     terminal-schema validation, and no success verdict in a failure artifact.
  7. The v1 contract SHA-256 remains
     `c02a9e64391b04430b13eb68630519cefe27056cdd6b1da01b1ef42334a539b2`.
     The new canonical v2 contract records v1 as spent/failed, binds all source,
     parser, terminal-member and command semantics, names only
     `validation/warm_state_population_semantics_v2_outcome`, has status
     `frozen_unapproved_unexecuted`, and reproduces byte-for-byte.
  8. Focused checks pass with zero forbidden attempts; no v2 outcome is created,
     no product warming changes, and the handoff makes no execution, equivalence,
     performance, readiness, adoption or release claim.
- Focused checks:
  - guarded import/no-process/no-socket/no-`runs/` and fake-runner tests in
    `tests/test_warm_state_population_semantics.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_population_semantics.py
    tests/test_warm_state_time_loss_semantics.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/diagnose_warm_state_population_semantics.py --verify`
  - exact v1 contract SHA-256 and v2 canonical/source/network/member-schema audit
  - `git diff --check -- tools/diagnose_warm_state_population_semantics.py
    tests/test_warm_state_population_semantics.py
    validation/warm_state_population_semantics_v2_contract.json TASKS.md
    AGENT_NOTES.md` and `git status --short`
- Approval gate: `NOT_REQUIRED` — this task is process-free correction, tests
  and freezing of one unapproved/unexecuted v2 contract only. It grants no
  simulator preflight, execution, root creation/inspection, warming or release
  authority. Any v2 execution requires a new task and exact user approval.
- Terminal handoff conditions:
  - Luna completes the whole parser/failure-evidence/CLI/test/freeze slice,
    self-audits every criterion, transitions once to `READY_FOR_SOL_REVIEW` and
    stops.
  - Stop on any need for simulator/process/socket/`runs/`/outcome access,
    production or v1-contract mutation, architecture/artifact scope expansion,
    approval, or three serious failed approaches; do not weaken preservation or
    validation to make the suite pass.
<!-- COMPLETED_TASK_LUNA_WARM_19_END -->

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### FULL-DAY-ANNUAL-WARMING — Populate exact reusable daily prefixes

- Status: `BLOCKED_ON_192_GIB_DISK_PREFLIGHT`
- Objective and scope: Populate the candidate-free full-day 2027 prefix bank
  for every exact 15-minute independent daily checkpoint and the production
  q10/q50/q90 seed mapping.
- Completion outcome: 104,685 states succeed under plan `9cc823d3…45283b` with
  no failed/running units; all 367 canonical demand archives and every stored
  artifact validate; product activation remains off.
- Context or checkpoints: The final candidate pool and all calibrated variants
  cover exactly 7,125/7,125 routable edges. Support-only vehicles are explicit,
  route-legal, measurement-disjoint and excluded from behavioral fit. Complete
  candidate/route/agent provenance and the latest immutable demand archive
  validate. The exact annual plan covers 1,682,634 supported intervals,
  34,895 checkpoints and 104,685 states. Checkpoints are ordered into exact
  demand-build/seed/variant chains: the first state starts from zero and each
  later state extends its nearest predecessor instead of replaying the whole
  prefix. The pre-run audit added exact SQLite
  row/lifecycle verification, orphan reconciliation, immutable publication,
  cross-binding of artifact bytes to demand-archive hashes, global demand-build
  serialization, runtime/source provenance and disk guards. The final audit
  additionally indexed plan lookup, retained one runner per worker/current
  demand build, reused verified archive records, restored only predecessor
  members actually consumed, batched progress commits and semantically checked
  crash-published orphans, plus route windows and native-millisecond
  accumulator transport. A real q10 chain
  completed all 96 links with zero failures; cold audits at links 2/48/96 match
  every behavioural section, and states remain 1.24–1.59 MiB. Measured storage
  invalidated the old disk estimate: the corrected 192-GiB minimum requires
  206,158,430,208 bytes but only 180,475,920,384 bytes are free. The final-plan
  root is therefore not initialized.
- Primary files: `validation/annual_warm_plan_2027.json`,
  `annual_warm_plan.py`, `annual_warm_progress.py`, `annual_warm_store.py`,
  `annual_warm_population.py`, `warm_route_windows.py`,
  `warm_state_boundary.py`, and `tools/populate_annual_warming.py`.
- Constraints and safety: Use only the exact current plan key; preserve the
  full six-hour recovery and unchanged hard gates; unsupported source-year/DST
  envelopes fall back cold; population is neither certification nor activation.
- Acceptance criteria: progress total remains exactly 104,685; succeeded reaches
  104,685; pending/running/failed reach zero; status and sampled restores pass.
- Useful checks: `python3 tools/plan_annual_warming.py --verify`; `python3
  tools/populate_annual_warming.py --preflight --state-workers 3`; focused
  annual/boundary/route suites and `validation/annual_warm_chain_pilot_v4.json`
  recorded in the current handoff. Do not initialize or execute until preflight
  passes.
<!-- ACTIVE_TASK_END -->

<!-- COMPLETED_TASK_LUNA_WARM_29_CURRENT_BLOCK_HISTORY_START -->
## ACTIVE_TASK — historical predecessor context

### MONTHLY-WARM-ACTIVATION — Validated production warm execution

- Task ID: `LUNA-WARM-29`
- Revision: `1`
- Status: `DONE`
- Owner: `Unassigned — any capable actor`
- Delivery size: `EXTENDED`
- Completion update: v16 replaced the refuted boundary-offset/lookahead models
  with exact unfinished-tripinfo reconstruction and SUMO-compatible decimal
  half-up normalization. A fresh paired campaign passed all q10/q50/q90 exact
  semantic comparisons, its three certified states were atomically adopted,
  and the production monthly command now enables the existing warm path by
  default with `--cold-execution` as an explicit escape hatch. The text below
  records the superseded LUNA-WARM-29 discovery contract for traceability.
- Historical objective and scope: Replace v14's zero-boundary-offset assumption with one
  source-derived candidate for the single mesoscopic interval lost at state
  restoration. Model SUMO 1.27.1's per-vehicle `TIME2STEPS` time-loss update,
  capture its exact bounded inputs at the saved step through the existing
  injected connection, bind them by active vehicle ID, and apply the computed
  interval once during whole-vehicle reconstruction. Reject constants,
  tolerances and aggregate fitting. Preserve every semantic gate and default-
  OFF product behavior; supersede v14 and freeze one unapproved/unexecuted v15
  candidate. The legacy task ID does not assign the work to a specific model.
- Completion outcome: one source-bound, fail-closed, process-free v15 candidate
  is ready for evidence-based review and a separately requested execution, with
  no claim that the candidate is correct or faster until a fresh paired
  campaign proves it.
- Internal checkpoints:
  1. Bind the relevant official SUMO 1.27.1 mesoscopic Tripinfo, vehicle-speed,
     reminder-update, state-save/load and time-conversion semantics; express a
     pure per-vehicle one-interval model and falsifying tests before wiring it.
     Freeze nothing unless the source call chain proves one omission and the
     selected TraCI getters are the exact C++ formula inputs.
  2. Capture, validate, digest and reconcile exact boundary inputs by identity;
     prove single application, ordinary final rounding and fail-closed behavior
     through the production warm path using fakes only.
  3. Preserve v14 bytes, convert only obsolete currency assertions, freeze and
     verify v15, update bounded documentation, and self-audit every criterion.
- Primary files (related files may be used when needed for the complete outcome):
  - Edit `traffic_sim/simulation/warm_state_boundary.py` and
    `traffic_sim/simulation/monthly_sumo.py` for the source-derived boundary-
    interval record, capture, validation, reconciliation and diagnostics only.
  - Edit `traffic_sim/simulation/warm_state_cache.py` only if required to bind
    the new evidence schema/identity and reject stale cache entries; do not
    enable or publish a cache.
  - Edit `tests/test_warm_state_boundary.py`, `tests/test_monthly_warm_state.py`,
    `tests/test_warm_state_cache.py` and `tests/test_monthly_sumo.py` only for
    focused fake-driven regression and integration coverage.
  - Edit `run_monthly_warm_state_validation.py` and
    `tests/test_monthly_warm_state_freeze.py` only for v15 schema/current-
    manifest support and process-free validation.
  - Preserve `tools/freeze_monthly_warm_state_v14.py` and
    `validation/monthly_warm_state_manifest_v14.json` byte-for-byte. Edit
    `tests/test_monthly_warm_state_v14_freeze.py` only to replace obsolete v14
    currency assertions with honest v15 supersession assertions.
  - Create `tools/freeze_monthly_warm_state_v15.py`,
    `tests/test_monthly_warm_state_v15_freeze.py` and
    `validation/monthly_warm_state_manifest_v15.json`.
  - Edit `ARCHITECTURE.md` and `IMPROVEMENT_PLAN.md` only for the reviewed v14
    result, new interval hypothesis, v15 status and default-OFF boundary;
    `TASKS.md` and `AGENT_NOTES.md` for the terminal handoff only.
  - Read the official Eclipse SUMO tag `v1_27_1` sources named in checkpoint 1
    and the current handoff's bounded v14 measurements; use task-created files
    under the system temporary directory for checks only.
- Constraints and safety:
  - No real socket, TraCI/libsumo import, probe, call or connection; no child
    process except the named Python test/verification commands; no SUMO,
    executable/network/archive preflight, campaign or scenario execution.
  - No `runs/`, archive, outcome, report or cache access, including the v14
    root. The reviewed current handoff is the only v14 result authority.
  - Do not patch SUMO, guess or fit a constant from v14, add a tolerance,
    special-case a seed/variant, change cold semantics, or weaken exact
    equivalence, provenance, coverage, health, cache, adoption or release gates.
  - Do not edit frozen v1-v14 tools/manifests, demand, network, schedule, seeds,
    thresholds or policies; do not create an outcome, approval token or usable
    cache; no demand/horizon generation, persistent warming, activation,
    Stage B, adoption, release mutation, deployment or publication.
- Acceptance criteria:
  1. A bounded source contract records the exact official SUMO 1.27.1 tag/files
     and distinguishes facts from the candidate inference: mesoscopic Tripinfo
     accumulates per-interval loss with millisecond `TIME2STEPS`, the device
     accumulator is not serialized, reminder timing is serialized, and v14's
     residual is consistent with—but does not prove—one lost boundary interval.
     Before implementation, trace save/close/load/first-update call order and
     prove that exactly one interval is omitted; otherwise hand off blocked.
  2. The pure model accepts only finite positive step length/allowed speed and
     finite speed in the source-valid range, applies SUMO's millisecond rounding
     per vehicle, returns a non-negative interval no larger than one step, and
     uses no campaign-derived constant, seed, variant or tolerance.
  3. Boundary capture occurs at exactly the frozen warm point on the same
     injected connection immediately adjacent to `saveState`, with no
     intervening simulation step. It captures a canonical sorted ID map of the
     exact speed/allowed-speed inputs and computed interval, binds warm point,
     step length and population digest, and rejects missing/duplicate/extra or
     malformed values. Source/API proof must show the chosen TraCI getters equal
     `meanSpeedVehicleOnLane` and `veh.getEdge()->getVehicleMaxSpeed(&veh)` in
     this mesoscopic path; an approximation is a blocker, not a frozen fix.
  4. Resumed reconciliation requires exact identity equality with the prefix
     accumulator and interval record, applies each interval exactly once to its
     boundary vehicle, then rounds each reconstructed whole vehicle once at the
     ordinary production precision. Completed-prefix and post-boundary vehicles
     receive no offset.
  5. Split diagnostics expose the bounded per-vehicle digest, total interval,
     count and explicit `boundary_offset_applied=true`; all summaries recompute
     from bounded inputs. Any absent/mismatched/tampered record or impossible
     total makes warm evidence invalid and invokes the existing honest cold
     fallback, never partial warm evidence.
  6. Pure unit vectors cover stopped, free-flow and partially delayed vehicles,
     SUMO millisecond rounding edges, zero-active boundaries, tampering,
     double-application refusal and final two-decimal normalization. The three
     reviewed v14 residual/count pairs are checked only against the analytic
     `[0, active_count * step_length]` bound and approximate mechanism scale;
     they are never used to choose production values.
  7. Fake end-to-end tests prove the real warm controller captures the new
     fields, the real runner consumes them, semantic equality remains exact,
     invalid evidence falls back cold, and no test imports TraCI or opens a
     socket, starts SUMO, reads `runs/`, or writes outside allowed/temp paths.
  8. Warm-cache identity/schema binds the new correction semantics so v14 and
     older entries cannot be restored as v15; default warming remains OFF and
     no cache is created or published in this task.
  9. v14 tool/manifest bytes remain unchanged. Its versioned tests retain
     immutable-byte, lineage, spent-approval and no-execution protections while
     asserting honest supersession without pinning mutable successor tests.
  10. v15 preserves v14's physical experiment—case, schedule, q10/q50/q90,
      seeds, warm point, demand/network/archive hashes, exact equality and
      fail-closed gates—and changes only the source-bound correction schema,
      provenance and successor identities.
  11. v15 recomposes byte-for-byte, fingerprints every interpreting source/test,
      is `frozen_unapproved_unexecuted`, carries no result or approval, and the
      execute path refuses it unless execution was deliberately requested.
      Generic pointers resolve v15; running it requires a clear user request
      for the frozen candidate and normal safety confirmation, not a specific
      model role.
  12. Current coordination markers occur once each and consistently describe a
      flexible, model-independent workflow. Historical Sol/Luna entries remain
      history and do not control current work.
- Useful checks:
  - audit-event guard around imports and the focused suite, forbidding real
    socket activity, TraCI/libsumo, SUMO/application subprocesses, `runs/`/
    archive/outcome/cache access and writes outside allowed/temp paths
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py
    tests/test_warm_state_cache.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v14_freeze.py
    tests/test_monthly_warm_state_v15_freeze.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v15.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v15.json` without `--execute`
  - preserved SHA-256 for v14 tool and manifest before/after
  - `git diff --check -- AGENTS.md traffic_sim/simulation/warm_state_boundary.py
    traffic_sim/simulation/monthly_sumo.py
    traffic_sim/simulation/warm_state_cache.py
    run_monthly_warm_state_validation.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py tests/test_warm_state_cache.py
    tests/test_monthly_sumo.py tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v14_freeze.py
    tests/test_monthly_warm_state_v15_freeze.py
    tools/freeze_monthly_warm_state_v15.py
    validation/monthly_warm_state_manifest_v15.json ARCHITECTURE.md
    IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Execution note: The process-free correction, fake-only checks, documentation
  and unexecuted freeze need no special approval. Real TraCI/SUMO execution,
  campaign creation, cache publication, warming or publication must be within a
  clear user request and follow normal safety/tool confirmation.
- Completion and escalation:
  - Complete all three checkpoints autonomously and self-audit every criterion.
    The same actor may continue through implementation and verification; an
    independent reviewer may be used when helpful.
  - Pause only for a source contradiction, need for real runtime/data outside
    the requested scope, a material architecture decision, a safety boundary or
    several failed credible approaches. Do not make the candidate pass by
    fitting v14, weakening exactness, accessing unrelated evidence or silently
    changing cold behavior.
<!-- COMPLETED_TASK_LUNA_WARM_29_CURRENT_BLOCK_HISTORY_END -->

<!-- COMPLETED_TASK_LUNA_WARM_28_START -->
## ACTIVE_TASK

### LUNA-WARM-28 — Execute the frozen v14 paired campaign once

- Task ID: `LUNA-WARM-28`
- Revision: `1`
- Owner: `Luna High`
- Status: `DONE — Sol approved the honest failed v14 experiment 2026-08-03;
  no warming/cache/adoption authority`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, perform one non-resumable
  `monthly_warm_state_v14` cold-versus-warm SUMO/TraCI campaign at content key
  `76ebb43577b9ef91a4e2c5b8504ee11bab960f560fa64f3fca4d0c1a27fd082c`
  in an environment permitting IPv4 TCP bind to `127.0.0.1:0`. Verify the
  frozen manifest/sources, production SUMO/TraCI environment, loopback bind,
  network and exact five-file archived demand before keyed-root inspection.
  Execute once, then inspect and independently recompute only that task-created
  root. Preserve honest pass/fail evidence and publish validation-only cache
  material inside the root only if every frozen gate passes.
- Completion outcome: one immutable v14 record establishes or refutes exact
  cold/warm equivalence and measures paired runtime without changing product
  warming, adoption, release or publication state.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - Read-only `validation/monthly_warm_state_manifest_v14.json`,
    `tools/freeze_monthly_warm_state_v14.py`,
    `run_monthly_warm_state_validation.py`, and all 24 fingerprinted sources.
  - After approval only, read exact `sumo/net.net.xml`; the production-resolved
    SUMO executable and its `tools/traci` package required for preflight/run.
  - After approval only, read these five files and no others under
    `runs/demand-20260721-222017-41bc682a-bbe1/`: `demand_meta.json`,
    `manifest.json`, `calibrated.rou.xml`, `calibrated_v1.rou.xml`, and
    `calibrated_v2.rou.xml`.
  - Create and inspect only `runs/monthly-warm-state-validation/
    76ebb43577b9ef91a4e2c5b8504ee11bab960f560fa64f3fca4d0c1a27fd082c`
    and task-created temporary workspaces/staging used by that execution.
  - `TASKS.md`, `AGENT_NOTES.md` for the terminal handoff only.
- Forbidden work:
  - Before approval: no guarded checks covered by the gate, executable/network/
    TraCI/archive/loopback preflight, keyed-root stat/absence check, SUMO,
    socket, execution or outcome inspection.
  - No rerun, resume or repair after any start, interruption or failure. Preserve
    the root exactly and stop; this content key is one-time and non-resumable.
  - No parent-directory enumeration and no other `runs/`, archive, outcome,
    report or cache access; never inspect another campaign/diagnostic result.
  - No source or frozen-artifact mutation, demand/horizon generation, threshold,
    tolerance, schedule, seed, policy or semantic change; no persistent warming
    or cache publication outside the keyed root, product activation, Stage B,
    adoption, release mutation, deployment or publication.
- Acceptance criteria:
  1. Before any gated action, Sol records the exact approval message, date,
     task/revision, key, root and scope below; every field matches.
  2. v14 `--verify`, canonical key, all 24 source fingerprints, schemas, case
     `warm-v14-paired-equivalence`, schedule
     `closure-8bcf7829ae545dffd8ce`, variants q10/q50/q90 and seeds
     1000/1001/1002 validate without drift.
  3. After approval, preflight resolves the exact SUMO executable/version and
     production TraCI origin/API, verifies network SHA-256
     `68ecde399ee7177bf8b3c9839a959170cca5d979f68bf15ca9f1cf6599ad5240`,
     demand build `2ac04275daabe93c` and all five frozen archive hashes. The
     execute path then proves IPv4/TCP bind to `127.0.0.1:0` before checking or
     creating the keyed root.
  4. The exact keyed root is absent, is created once by the frozen harness, and
     the command runs once without rerun/resume/repair. Task staging remains
     isolated and is atomically published inside that root or removed.
  5. Execution produces all three cold and three warm canonical observations
     and three finalized warm-attempt records. Missing identity, cold fallback,
     abnormal exit, timeout, cleanup failure, semantic mismatch or publication
     inconsistency makes the record fail honestly.
  6. Inspection stays within the task-created root and independently recomputes
     identities, full semantic equality, coverage, execution evidence,
     prefix-ledger/reconstruction summaries, performance and validation-only
     cache consistency from root bytes.
  7. A passing record has exact semantic equivalence, warm execution for every
     identity, complete coverage and exactly the expected restorable cache
     entries. A failing record contains `NO_CACHE_PUBLISHED` and no usable
     cache. Either result makes no product/adoption/release claim.
  8. The terminal handoff records exact commands, elapsed result, root-bounded
     evidence, cold/warm measurements and whether the hypothesis passed or was
     refuted, without inspecting or comparing another outcome.
- Focused checks:
  - guarded process-free suite:
    `PYTHONDONTWRITEBYTECODE=1 python3 /private/tmp/luna_warm27_audit_guard.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v14.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v14.json` (no `--execute`)
  - after approval, exact SUMO executable/version, production TraCI origin/API,
    network hash, five archive hashes/build identity and frozen execute-path
    loopback/keyed-root preflight
  - execute exactly once:
    `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v14.json --execute
    --approval-token
    76ebb43577b9ef91a4e2c5b8504ee11bab960f560fa64f3fca4d0c1a27fd082c`
  - root-bounded enumeration, hashing, JSON parsing and production-function
    recomputation only after that command terminates
  - `git diff --check -- TASKS.md AGENT_NOTES.md` and `git status --short`
- Approval gate: `REQUIRED — RECORDED`, for exactly this
  one-time non-resumable campaign, named checks/preflight, execution and
  inspection of its own keyed root. Exact key:
  `76ebb43577b9ef91a4e2c5b8504ee11bab960f560fa64f3fca4d0c1a27fd082c`.
  Exact root: `runs/monthly-warm-state-validation/
  76ebb43577b9ef91a4e2c5b8504ee11bab960f560fa64f3fca4d0c1a27fd082c`.
  Exact quoted user message, supplied and matched exactly:
  > I explicitly approve LUNA-WARM-28 revision 1 to run the one-time non-resumable monthly_warm_state_v14 paired cold-versus-warm SUMO/TraCI campaign at content key 76ebb43577b9ef91a4e2c5b8504ee11bab960f560fa64f3fca4d0c1a27fd082c and artifact root runs/monthly-warm-state-validation/76ebb43577b9ef91a4e2c5b8504ee11bab960f560fa64f3fca4d0c1a27fd082c, including the named guarded process-free checks, canonical manifest/source/schema/case/schedule/seed checks, exact SUMO executable/version, production TraCI origin/API, IPv4 TCP loopback-bind preflight at 127.0.0.1:0, network and five-file archived-demand preflight for demand_build_id 2ac04275daabe93c, keyed-root absence check, one frozen execution, task-created temporary workspaces/staging and validation-only cache material inside that root, and inspection and production-consistency recomputation only within that task-created root. No rerun, resume, repair, other runs/outcome/report/cache inspection, demand or horizon generation, persistent warming/cache publication outside that root, product activation, Stage B, adoption, release mutation, deployment or publication is approved.
  User-message date: `2026-08-03`. Sol recorder/date:
  `Sol High / 2026-08-03 — exact task/revision/key/root/scope matched and recorded`.
- Terminal handoff conditions:
  - Remain `BLOCKED` with no Luna action until the exact approval is supplied
    and Sol records it.
  - After approval, hand off once after the command terminates and root-only
    inspection completes, whether pass or fail.
  - Stop immediately without widening scope on preflight failure, pre-existing
    root/staging, interruption, need for rerun/repair, source drift, unexpected
    filesystem access or any product/release authority boundary.
<!-- COMPLETED_TASK_LUNA_WARM_28_END -->

<!-- COMPLETED_TASK_LUNA_WARM_27_START -->
## ACTIVE_TASK

### LUNA-WARM-27 — Fail before root on blocked loopback and freeze v14

- Task ID: `LUNA-WARM-27`
- Revision: `1`
- Owner: `Luna High`
- Status: `READY_FOR_SOL_PLAN`
- Delivery size: `EXTENDED`
- Objective and scope: Add an injectable production preflight that proves an
  IPv4 TCP socket can bind to `127.0.0.1:0`, because v13 spent its one-time key
  when the sandbox denied that exact operation before TraCI launch. Run it after
  approval and TraCI origin/API validation but before keyed-root inspection or
  creation. Preserve no-execute behavior and all semantic/cache gates. Add
  process-free ordering, denial and cleanup tests using fakes; record the
  reviewed v13 environmental failure without reading its root; supersede v13
  safely; and freeze one otherwise physically identical, unapproved/unexecuted
  v14 campaign candidate requiring a socket-capable execution environment.
- Completion outcome: the next campaign cannot consume its key or create a root
  in an environment that forbids TraCI’s required loopback bind, while v14
  retains the reviewed v13 semantic mechanism and exact physical experiment.
- Internal checkpoints:
  1. Implement the bounded injectable localhost-bind capability probe and prove
     success, denial, exception translation and deterministic closure without
     making a real socket call in tests.
  2. Wire strict fail-first ordering through the real execute path and prove no
     approval, no-execute, TraCI failure or bind failure can reach root checking
     or campaign execution; preserve all existing evaluator/cache behavior.
  3. Retire v13 without rewriting its tool/manifest, freeze/verify v14, run the
     guarded focused suite, update bounded documentation and self-audit every
     acceptance criterion.
- Allowed files:
  - Edit `run_monthly_warm_state_validation.py` and
    `tests/test_monthly_warm_state_freeze.py` only for the injectable loopback
    capability preflight, execute ordering, diagnostics and current pointer.
  - Preserve `tools/freeze_monthly_warm_state_v13.py` and
    `validation/monthly_warm_state_manifest_v13.json` byte-for-byte. Edit
    `tests/test_monthly_warm_state_v13_freeze.py` only to convert obsolete v13
    currency assertions into honest v14 supersession assertions while retaining
    immutable-byte, lineage, approval and no-execution protections.
  - Create `tools/freeze_monthly_warm_state_v14.py`,
    `tests/test_monthly_warm_state_v14_freeze.py`, and
    `validation/monthly_warm_state_manifest_v14.json`.
  - Edit `ARCHITECTURE.md` and `IMPROVEMENT_PLAN.md` only for the reviewed v13
    environmental failure, the pre-root bind gate, v14 status and default-OFF
    limit; `TASKS.md` and `AGENT_NOTES.md` for terminal handoff only.
  - Task-created temporary files under the system temporary directory for the
    process-free tests only.
- Forbidden work:
  - No real socket creation/bind/connect/listen, TraCI/libsumo import/probe/call
    or connection, application subprocess, SUMO, executable/network preflight
    or campaign execution; tests must inject fakes before the capability seam.
  - No `runs/`, archive, outcome, report or cache access, including the v13
    root. Use only the reviewed current handoff as the v13 result authority.
  - Do not edit frozen v1-v13 tools/manifests, demand, network, schedules, seeds,
    thresholds, comparison policy or semantic mechanism; do not patch SUMO.
  - Do not create an outcome root, campaign, cache or approval token; do not
    warm/generate demand or horizons, activate warming, Stage B, adoption,
    release mutation, deployment or publication.
- Acceptance criteria:
  1. Changed modules import process-free, and the guarded suite records no real
     socket, TraCI/libsumo, child process, `runs/`/outcome/cache access or
     non-temporary write outside the explicitly allowed files.
  2. The capability seam uses an injectable socket factory; production selects
     IPv4/TCP, binds exactly `127.0.0.1:0`, obtains a valid ephemeral port and
     closes deterministically. It returns only bounded capability metadata and
     translates denial/failure into a clear `HarnessError` stating that no
     campaign/root was started.
  3. Execute ordering is exactly: frozen-manifest validation, approval token,
     production TraCI origin/API, localhost-bind capability, keyed-root absence,
     then paired campaign. Tests prove each failure prevents all later steps.
  4. No-execute validation never imports TraCI, opens a socket, checks a root or
     starts a process. Existing approval-token mismatch, source drift,
     pre-existing-root and non-resumable protections remain unchanged.
  5. The actual campaign entry point cannot bypass the new probe. Unit tests
     cover fake success, `PermissionError(1, "Operation not permitted")`, bind
     failure, malformed socket metadata, closure on every path and exact call
     ordering without weakening `WarmPrefixController`’s own checks.
  6. v14 inherits exactly v13’s case, schedule, q10/q50/q90 variants, seeds,
     warm points, demand/network/archive hashes, semantic comparison, cache
     publication and mesoscopic reconstruction contracts. Only lifecycle,
     reviewed-v13 disposition and bind-capability/preflight bindings change.
  7. v14 records v13 as `executed_environment_blocked_no_cache`: three complete
     identities, zero semantic mismatches, zero valid warm executions, three
     permission-denied cold fallbacks and no cache. It makes no equivalence or
     performance claim from those fallback pairs.
  8. The frozen v14 JSON recomposes byte-for-byte, fingerprints every source and
     interpreting regression, is `frozen_unapproved_unexecuted`, default-OFF,
     and requires a new exact-key approval plus socket-capable execution for one
     future non-resumable campaign.
  9. v13 tool/manifest bytes stay unchanged; its versioned suite asserts honest
     supersession without pinning mutable successor tests. Generic current
     pointers resolve only v14, and old approval cannot authorize it.
  10. Documentation states the shortest remaining path accurately: review v14,
      obtain fresh exact-key approval, then execute the frozen command once with
      escalated socket permission; no further mechanism work is implied unless
      a socket-capable warm execution finds a real semantic mismatch.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py
    tests/test_warm_state_cache.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v12_freeze.py
    tests/test_monthly_warm_state_v13_freeze.py
    tests/test_monthly_warm_state_v14_freeze.py`
  - audit-event guard around import, freeze, verify and the same focused suite,
    forbidding real socket activity, TraCI/libsumo, child process, `runs/`/
    archive/outcome/cache access and non-temporary writes outside allowed files
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v14.py --verify`
  - preserved SHA-256 for v13 tool and manifest before/after
  - `git diff --check -- run_monthly_warm_state_validation.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v13_freeze.py
    tests/test_monthly_warm_state_v14_freeze.py
    tools/freeze_monthly_warm_state_v14.py
    validation/monthly_warm_state_manifest_v14.json ARCHITECTURE.md
    IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED`; this revision is process-free implementation,
  testing, documentation and freezing only. Any real socket probe, TraCI/SUMO
  execution, root access or v14 campaign requires a separate exact-key approval
  recorded by Sol.
- Terminal handoff conditions:
  - Hand off once when all checkpoints and acceptance criteria pass, or on an
    architecture/artifact/scope/authority boundary or three recorded serious
    failed approaches.
  - Stop without real socket/runtime activity or broader edits if fail-first
    ordering, fake-only testability, v13 byte preservation or exact v14 lineage
    cannot be established process-free; report the safest Sol decision.
<!-- COMPLETED_TASK_LUNA_WARM_27_END -->

<!-- COMPLETED_TASK_LUNA_WARM_26_START -->
## ACTIVE_TASK

### LUNA-WARM-26 — Execute the frozen v13 paired campaign once

- Task ID: `LUNA-WARM-26`
- Revision: `1`
- Owner: `Luna High`
- Status: `READY_FOR_SOL_PLAN`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, perform one non-resumable
  `monthly_warm_state_v13` cold-versus-warm SUMO/TraCI campaign at content key
  `0c8d42eb828c24e398acc3b642b4750c732addc2321db0e85935d015fe9eac77`.
  Verify the frozen manifest and sources, production SUMO/TraCI environment,
  network and exact five-file archived demand before creating the keyed root.
  Execute the frozen harness once, then inspect and independently recompute only
  that task-created root. Preserve honest pass or fail evidence; publish
  validation-only cache material inside the root only if every frozen semantic,
  execution and coverage gate passes. Record performance without converting it
  into an unproven product-speed claim.
- Completion outcome: one immutable, reviewed v13 equivalence/performance
  record establishes or refutes exact cold/warm equivalence for the frozen case
  and measures the paired runtime without changing product warming, adoption,
  release or publication state.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - Read-only `validation/monthly_warm_state_manifest_v13.json`,
    `tools/freeze_monthly_warm_state_v13.py`,
    `run_monthly_warm_state_validation.py`, and all 23 fingerprinted sources.
  - After approval only, read exact `sumo/net.net.xml`; the production-resolved
    SUMO executable and its `tools/traci` package required for preflight/run.
  - After approval only, read these five files and no others under
    `runs/demand-20260721-222017-41bc682a-bbe1/`: `demand_meta.json`,
    `manifest.json`, `calibrated.rou.xml`, `calibrated_v1.rou.xml`, and
    `calibrated_v2.rou.xml`.
  - Create and inspect only `runs/monthly-warm-state-validation/
    0c8d42eb828c24e398acc3b642b4750c732addc2321db0e85935d015fe9eac77`
    and task-created temporary workspaces/staging used by that execution.
  - `TASKS.md`, `AGENT_NOTES.md` for the terminal handoff only.
- Forbidden work:
  - Before approval: no checks covered by the gate, executable/network/TraCI or
    archive preflight, keyed-root stat/absence check, SUMO, socket, execution or
    outcome inspection.
  - No rerun, resume or repair after any start, interruption or failure. Preserve
    the root exactly and stop; this content key is one-time and non-resumable.
  - No parent-directory enumeration and no other `runs/`, archive, outcome,
    report or cache access. Never inspect another campaign/diagnostic result.
  - No demand/horizon generation, persistent warming or cache publication
    outside the keyed root; no source/artifact mutation, threshold/tolerance,
    schedule/seed/policy change, product activation, Stage B, adoption, release
    mutation, deployment or publication.
- Acceptance criteria:
  1. Before any gated action, Sol records the exact approval message, date,
     key, root and scope below for this task/revision; every field matches.
  2. The v13 manifest canonical key, `--verify`, 23 source fingerprints, schema,
     case `warm-v13-paired-equivalence`, schedule
     `closure-8bcf7829ae545dffd8ce`, variants q10/q50/q90 and seeds
     1000/1001/1002 validate without drift.
  3. Preflight resolves one exact SUMO executable/version and the production
     TraCI origin/API, verifies network SHA-256
     `68ecde399ee7177bf8b3c9839a959170cca5d979f68bf15ca9f1cf6599ad5240`,
     demand build `2ac04275daabe93c`, and all five frozen archive hashes before
     the keyed root is checked or created.
  4. The exact keyed root is absent, is created once by the frozen harness, and
     the command runs once without rerun/resume/repair. Task staging remains
     isolated and is either atomically published inside that root or removed.
  5. The frozen execution produces all three cold and three warm canonical
     observations and three required finalized warm-attempt records. Missing
     identity, fallback, abnormal exit, timeout, cleanup failure, semantic
     mismatch or publication inconsistency makes the record fail honestly.
  6. Inspection stays within the task-created root and independently recomputes
     canonical identities, comparison digests/full semantic equality, expected
     coverage, execution evidence, prefix-ledger/reconciliation summaries,
     performance fields and validation-only cache consistency from root bytes.
  7. A passing record has exact semantic equivalence, warm execution for every
     identity, complete coverage and exactly the expected restorable cache
     entries. A failing record contains `NO_CACHE_PUBLISHED` and no usable cache.
     Either result makes no product/adoption/release claim.
  8. The terminal handoff records exact commands, elapsed result, root-bounded
     evidence, cold/warm runtime measurements and whether the hypothesis passed
     or was refuted; it does not inspect or compare any other outcome.
- Focused checks:
  - guarded process-free suite recorded by LUNA-WARM-25 (`550 passed` baseline)
    plus `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v13.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v13.json` (no `--execute`)
  - after approval, exact SUMO executable/version, production TraCI origin/API,
    network hash, five archive hashes/build identity and keyed-root absence
  - execute exactly once:
    `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v13.json --execute
    --approval-token
    0c8d42eb828c24e398acc3b642b4750c732addc2321db0e85935d015fe9eac77`
  - root-bounded enumeration, hashing, JSON parsing and production-function
    recomputation only after that command terminates
  - `git diff --check -- TASKS.md AGENT_NOTES.md` and `git status --short`
- Approval gate: `REQUIRED` for exactly this one-time, non-resumable campaign,
  its named checks/preflight, execution and inspection of its own keyed root.
  Exact key: `0c8d42eb828c24e398acc3b642b4750c732addc2321db0e85935d015fe9eac77`.
  Exact root: `runs/monthly-warm-state-validation/
  0c8d42eb828c24e398acc3b642b4750c732addc2321db0e85935d015fe9eac77`.
  Exact quoted user message, supplied and matched exactly:
  > I explicitly approve LUNA-WARM-26 revision 1 to run the one-time non-resumable monthly_warm_state_v13 paired cold-versus-warm SUMO/TraCI campaign at content key 0c8d42eb828c24e398acc3b642b4750c732addc2321db0e85935d015fe9eac77 and artifact root runs/monthly-warm-state-validation/0c8d42eb828c24e398acc3b642b4750c732addc2321db0e85935d015fe9eac77, including the named guarded process-free checks, canonical manifest/source/schema/case/schedule/seed checks, exact SUMO executable/version, production TraCI origin/API, network and five-file archived-demand preflight for demand_build_id 2ac04275daabe93c, keyed-root absence check, one frozen execution, task-created temporary workspaces/staging and validation-only cache material inside that root, and inspection and production-consistency recomputation only within that task-created root. No rerun, resume, repair, other runs/outcome/report/cache inspection, demand or horizon generation, persistent warming/cache publication outside that root, product activation, Stage B, adoption, release mutation, deployment or publication is approved.
  User-message date: `2026-08-03`. Sol recorder/date:
  `Sol High / 2026-08-03 — exact task/revision/key/root/scope matched and recorded`.
- Terminal handoff conditions:
  - While blocked, stop until the exact approval is supplied and Sol records it.
  - After approval, hand off once after the command terminates and the root-only
    inspection completes, whether pass or fail.
  - Stop immediately without widening scope on preflight failure, pre-existing
    root/staging, interruption, need for rerun/repair, source drift, unexpected
    filesystem access, or any product/release authority boundary.
<!-- COMPLETED_TASK_LUNA_WARM_26_END -->

<!-- COMPLETED_TASK_LUNA_WARM_25_START -->
## ACTIVE_TASK

### LUNA-WARM-25 — Recover SUMO's omitted meso tripinfo accumulator and freeze v13

- Task ID: `LUNA-WARM-25`
- Revision: `1`
- Owner: `Luna High`
- Status: `READY_FOR_SOL_PLAN`
- Delivery size: `EXTENDED`
- Objective and scope: Correct the v12 warm path using the source-established
  mechanism: mesoscopic tripinfo reports private `myMesoTimeLoss`, save/load
  omits it, and TraCI exposes waiting time instead. Make the prefix process emit
  high-precision unfinished tripinfo at its normal close, bind those records to
  the exact boundary-active identities, persist their pre-boundary accumulator,
  and reconstruct each resumed whole-vehicle value before rounding once to
  production precision. Remove the refuted TraCI deficit correction, bump cache
  evidence/identity schemas, preserve cold fallback/default-OFF behavior, and
  freeze one immutable unapproved/unexecuted v13 campaign candidate.
- Completion outcome: a reviewable production v13 warm path retains the exact
  mesoscopic accumulator SUMO drops at restore, refuses incomplete identity or
  population evidence, preserves old entries as cache misses, and is ready for
  one final paired equivalence/performance campaign rather than another generic
  diagnostic round.
- Internal checkpoints:
  1. Replace the incorrect TraCI accumulator model with a canonical
     boundary-active tripinfo ledger and prove its partition/rounding rules with
     parser, malformed-input and arithmetic counterexamples.
  2. Wire prefix/resumed high-precision output, evidence/cache schema bumps,
     exact per-vehicle reconstruction and unchanged cold fallback through the
     real production seams; run end-to-end fake-driven regressions.
  3. Supersede v12 safely, freeze/verify v13, run guarded focused checks, update
     current documentation and self-audit every acceptance criterion.
- Allowed files:
  - Edit `run_scenario.py`, `run_monthly_warm_state_validation.py`,
    `traffic_sim/simulation/monthly_sumo.py`,
    `traffic_sim/simulation/monthly_warm_state.py`,
    `traffic_sim/simulation/warm_state_boundary.py`,
    `traffic_sim/simulation/warm_state_cache.py`, `tests/test_monthly_sumo.py`,
    `tests/test_monthly_warm_state.py`, `tests/test_warm_state_boundary.py`,
    `tests/test_warm_state_cache.py`, and
    `tests/test_monthly_warm_state_freeze.py` only for the v13 mechanism,
    schema, integration, guarded execution contract and current pointer.
  - Preserve `tools/freeze_monthly_warm_state_v12.py` and
    `validation/monthly_warm_state_manifest_v12.json` byte-for-byte. Edit
    `tests/test_monthly_warm_state_v12_freeze.py` only to replace obsolete
    currency/source-recomposition assertions with honest v13 supersession
    assertions; retain immutable-byte, lineage and no-execution protections.
  - Create `tools/freeze_monthly_warm_state_v13.py`,
    `tests/test_monthly_warm_state_v13_freeze.py`, and
    `validation/monthly_warm_state_manifest_v13.json`.
  - Edit `ARCHITECTURE.md` and `IMPROVEMENT_PLAN.md` only for the reviewed v12
    result, source-established defect, v13 design/status and default-OFF limit.
  - Read-only: `traffic_sim/simulation/warm_state_forensics.py`, `runtime.py`,
    `metrics.py`, the v12 evidence values already recorded in CURRENT_HANDOFF,
    and official SUMO documentation/source needed to cite the mechanism.
  - `TASKS.md` and `AGENT_NOTES.md` for the terminal handoff only; task-created
    temporary files under the system temporary directory for tests only.
- Forbidden work:
  - No SUMO or TraCI/libsumo import, probe, call or connection; no socket,
    application subprocess, executable/network preflight or campaign execution.
  - No `runs/`, archive, outcome, report or cache access, including the v12 root;
    use only the reviewed current handoff and frozen v12 manifest for lineage.
  - Do not edit frozen v1-v12 tools/manifests, unrelated historical freeze tests,
    demand, network, thresholds, schedules or seeds; do not patch/build SUMO.
  - Do not create an outcome root, campaign, cache or approval token; do not
    warm/generate demand or horizons, activate warming, Stage B, adoption,
    release mutation, deployment or publication.
  - Do not add a tolerance, weaken exact identity/population checks, treat unit
    evidence as runtime equivalence/performance evidence or enable warming.
- Acceptance criteria:
  1. Changed modules import process-free: all simulator, socket and child-process
     dependencies remain lazy and the audit-event guard records no forbidden
     action during import, freeze, verify or the focused suite.
  2. Code comments, manifest rationale and tests bind the exact defect: SUMO
     meso tripinfo accumulates `myMesoTimeLoss`; its device save/load fields omit
     that member; `MEVehicle::getTimeLoss()` returns waiting time. The old
     save/restore TraCI deficit path is no longer used as meso tripinfo evidence.
  3. Prefix construction requires exactly one `write-unfinished=true` and one
     global output precision of `16`; resumed construction requires precision
     `16`; the ordinary cold command remains byte-for-byte unchanged. Command
     validators reject omissions, duplicates, contradictory flags and drift.
  4. At the exact save instant the controller captures the sorted active ID set
     from the same connection that saves state. After normal prefix exit, strict
     XML parsing partitions completed and active tripinfo by arrival and captured
     identity, rejects duplicates/missing/extra/non-finite records, and persists
     a canonical digest-bound active accumulator ledger in prefix evidence.
  5. Prefix evidence uses a fresh schema and cache identity/source binding, so
     every v12/legacy entry is a fail-closed miss. Its completed-trip objective
     is recomputed by rounding each whole completed vehicle once to precision 2;
     aggregate claims must recompose from their records.
  6. Resumed reconciliation is exhaustive by vehicle ID. Boundary-active values
     equal `prefix_private_accumulator + resumed_private_accumulator`; vehicles
     departing after the boundary use only resumed values; every whole vehicle
     is rounded once to precision 2 before summing. Missing, overlapping,
     duplicated or unknown identities cause recorded cold fallback, never a
     partial correction or tolerance.
  7. End-to-end fake-driven tests exercise cache miss/bootstrap and cache hit,
     real public warm-observation wiring, output finalization, exact population
     partition, sub-centisecond rounding counterexamples, nonzero-prefix and
     zero-prefix vehicles, malformed evidence, abnormal exit/timeout, source
     drift and unchanged cold fallback/default-OFF behavior.
  8. Other production metrics retain the field-partition rules and canonical
     cold semantics. High precision is an output-only warm transport format;
     tests prove recovery buckets, queue/counter/health/closure fields are
     normalized or parsed identically rather than silently changing meaning.
  9. v13 inherits exactly the three v12 q10/q50/q90 identities, schedules,
     seeds, route/network hashes, warm point `24300`, demand build
     `2ac04275daabe93c` and paired evaluator gates. It adds source-mechanism,
     ledger-schema, precision and population-integrity bindings only.
  10. The frozen JSON recomposes byte-for-byte, binds every interpreting source
      and focused test, declares `frozen_unapproved_unexecuted`, requires a new
      exact-key approval for execution, and cannot publish a cache before exact
      cold/warm equivalence plus the existing performance gate pass.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py
    tests/test_warm_state_cache.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v12_freeze.py
    tests/test_monthly_warm_state_v13_freeze.py`
  - process-free audit-event guard around import and the same focused suite,
    forbidding TraCI/libsumo, socket, application child process, `runs/`/
    archive/outcome/cache access and non-temporary writes outside allowed files
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v13.py --verify`
  - preserved SHA-256 for v12 tool, test and manifest before/after
    except the explicitly authorized v12 test supersession edit
  - `git diff --check -- run_scenario.py run_monthly_warm_state_validation.py
    traffic_sim/simulation/monthly_sumo.py
    traffic_sim/simulation/monthly_warm_state.py
    traffic_sim/simulation/warm_state_boundary.py
    traffic_sim/simulation/warm_state_cache.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state.py tests/test_warm_state_boundary.py
    tests/test_warm_state_cache.py tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v12_freeze.py
    tests/test_monthly_warm_state_v13_freeze.py
    tools/freeze_monthly_warm_state_v13.py
    validation/monthly_warm_state_manifest_v13.json ARCHITECTURE.md
    IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED`; this revision is process-free implementation,
  testing, documentation and freezing only. v13 must fail closed on execution
  without a future exact-key user approval recorded by Sol.
- Terminal handoff conditions:
  - Hand off once when all checkpoints and acceptance criteria pass, or on an
    architecture/artifact/scope/authority boundary or three recorded serious
    failed approaches.
  - Stop without execution or broader edits if exact active-vehicle population,
    per-vehicle single-round reconstruction, or unchanged cold semantics cannot
    be established process-free; report the missing seam and safest Sol decision.
<!-- COMPLETED_TASK_LUNA_WARM_25_END -->

<!-- COMPLETED_TASK_LUNA_WARM_24_START -->
## ACTIVE_TASK

### LUNA-WARM-24 — Execute the frozen v12 paired campaign once

- Task ID: `LUNA-WARM-24`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved immutable negative evidence`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, perform one non-resumable
  `monthly_warm_state_v12` cold-versus-warm SUMO/TraCI campaign at content key
  `f8b03c614b8704eebb128e4f76cef67a0fc2bc871e870ae18fefb0ad08d71a0c`.
  Verify the frozen manifest and sources, production SUMO/TraCI environment,
  network and exact five-file archived demand before creating the keyed root.
  Execute the frozen harness once, then inspect and independently recompute only
  that task-created root. Preserve honest pass or fail evidence; publish
  validation-only cache material inside the root only if every frozen semantic,
  execution and coverage gate passes.
- Completion outcome: one immutable, reviewed v12 equivalence record establishes
  or refutes exact cold/warm equivalence for the frozen case without changing
  product warming, adoption, release or publication state.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - Read-only `validation/monthly_warm_state_manifest_v12.json`,
    `tools/freeze_monthly_warm_state_v12.py`,
    `run_monthly_warm_state_validation.py`, and all 22 fingerprinted sources.
  - After approval only, read exact `sumo/net.net.xml`; the production-resolved
    SUMO executable and its `tools/traci` package required for preflight/run.
  - After approval only, read these five files and no others under
    `runs/demand-20260721-222017-41bc682a-bbe1/`: `demand_meta.json`,
    `manifest.json`, `calibrated.rou.xml`, `calibrated_v1.rou.xml`, and
    `calibrated_v2.rou.xml`.
  - Create and inspect only `runs/monthly-warm-state-validation/
    f8b03c614b8704eebb128e4f76cef67a0fc2bc871e870ae18fefb0ad08d71a0c`
    and task-created temporary workspaces/staging used by that execution.
  - `TASKS.md`, `AGENT_NOTES.md` for the terminal handoff only.
- Forbidden work:
  - Before approval: no checks covered by the gate, executable/network/TraCI or
    archive preflight, root/stat check, SUMO, socket, execution or inspection.
  - No rerun, resume or repair after any start, interruption or failure. Preserve
    the root exactly and stop; this content key is one-time and non-resumable.
  - No parent-directory enumeration and no other `runs/`, archive, outcome,
    report or cache access. Never inspect any v1-v11 campaign/diagnostic result.
  - No demand/horizon generation, persistent warming or cache publication
    outside the keyed root; no source/artifact mutation, threshold/tolerance,
    schedule/seed/policy change, product activation, Stage B, adoption, release
    mutation, deployment or publication.
- Acceptance criteria:
  1. Before any gated action, Sol records the exact approval message, date,
     key, root and scope below for this task/revision; every field matches.
  2. The v12 manifest canonical key, `--verify`, 22 source fingerprints, schema,
     case `warm-v12-paired-equivalence`, schedule
     `closure-8bcf7829ae545dffd8ce`, variants q10/q50/q90 and seeds
     1000/1001/1002 validate without drift.
  3. Preflight resolves one exact SUMO executable/version and the production
     TraCI origin/API, verifies network SHA-256
     `68ecde399ee7177bf8b3c9839a959170cca5d979f68bf15ca9f1cf6599ad5240`,
     demand build `2ac04275daabe93c`, and all five frozen archive hashes before
     the keyed root is checked or created.
  4. The exact keyed root is absent, is created once by the frozen harness, and
     the command runs once without rerun/resume/repair. Task staging remains
     isolated and is either atomically published inside that root or removed.
  5. The frozen execution produces all three cold and three warm canonical
     observations and three required warm-attempt records. Missing identities,
     fallback, abnormal exit, timeout, cleanup failure, semantic mismatch or
     publication inconsistency makes the record fail honestly.
  6. Inspection stays within the task-created root and independently recomputes
     record canonical identity, comparison digests/full semantic equality,
     expected coverage, execution evidence, correction/final-ledger summaries,
     performance fields and validation-only cache consistency from root bytes.
  7. A passing record has exact semantic equivalence, warm execution for every
     identity, complete coverage and exactly the expected restorable cache
     entries. A failing record contains `NO_CACHE_PUBLISHED` and no usable cache.
     Either result makes no product/adoption/release claim.
  8. The terminal handoff records exact commands, elapsed result, root-bounded
     evidence, and whether the hypothesis passed or was refuted; it does not
     inspect or compare any other outcome.
- Focused checks:
  - guarded process-free suite from LUNA-WARM-23 revision 3 (`711 passed` baseline)
    plus `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v12.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v12.json` (no `--execute`)
  - after approval, exact SUMO executable/version, production TraCI origin/API,
    network hash, five archive hashes/build identity and keyed-root absence
  - execute exactly once:
    `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v12.json --execute
    --approval-token
    f8b03c614b8704eebb128e4f76cef67a0fc2bc871e870ae18fefb0ad08d71a0c`
  - root-bounded enumeration, hashing, JSON parsing and production-function
    recomputation only after that command terminates
  - `git diff --check -- TASKS.md AGENT_NOTES.md` and `git status --short`
- Approval gate: `REQUIRED` for exactly this one-time, non-resumable campaign,
  its named checks/preflight, execution and inspection of its own keyed root.
  Exact key: `f8b03c614b8704eebb128e4f76cef67a0fc2bc871e870ae18fefb0ad08d71a0c`.
  Exact root: `runs/monthly-warm-state-validation/
  f8b03c614b8704eebb128e4f76cef67a0fc2bc871e870ae18fefb0ad08d71a0c`.
  Exact quoted user message:
  > I explicitly approve LUNA-WARM-24 revision 1 to run the one-time non-resumable monthly_warm_state_v12 paired cold-versus-warm SUMO/TraCI campaign at content key f8b03c614b8704eebb128e4f76cef67a0fc2bc871e870ae18fefb0ad08d71a0c and artifact root runs/monthly-warm-state-validation/f8b03c614b8704eebb128e4f76cef67a0fc2bc871e870ae18fefb0ad08d71a0c, including the named guarded process-free checks, canonical manifest/source/schema/case/schedule/seed checks, exact SUMO executable/version, production TraCI origin/API, network and five-file archived-demand preflight for demand_build_id 2ac04275daabe93c, keyed-root absence check, one frozen execution, task-created temporary workspaces/staging and validation-only cache material inside that root, and inspection and production-consistency recomputation only within that task-created root. No rerun, resume, repair, other runs/outcome/report/cache inspection, demand or horizon generation, persistent warming/cache publication outside that root, product activation, Stage B, adoption, release mutation, deployment or publication is approved.
  User-message date: `2026-08-03`. Sol recorder/date:
  `Sol High / 2026-08-03 — exact task/revision/key/root/scope matched and recorded`.
- Terminal handoff conditions:
  - While blocked, stop until the exact approval is supplied and Sol records it.
  - After approval, hand off once after the command terminates and the root-only
    inspection completes, whether pass or fail.
  - Stop immediately without widening scope on preflight failure, pre-existing
    root/staging, interruption, need for rerun/repair, source drift, unexpected
    filesystem access, or any product/release authority boundary.
<!-- COMPLETED_TASK_LUNA_WARM_24_END -->

<!-- ACTIVE_TASK_LUNA_WARM_23_R3_HISTORY_START -->
## ACTIVE_TASK

### LUNA-WARM-23 — Close exact reconciliation and freeze v12

- Task ID: `LUNA-WARM-23`
- Revision: `3`
- Owner: `Luna High`
- Status: `CONCLUDED — APPROVED`
- Delivery size: `EXTENDED`
- Objective and scope: Complete the process-free exact selective-reconciliation
  slice retained from revision 2. Correct the v10 hash only in mutable tests and
  this successor contract; close all remaining hostile fixture and call-graph
  failures without weakening semantics. Preserve the rejected v11 tool and
  manifest, convert its mutable suite to truthful supersession checks, and
  freeze a fresh v12 candidate that binds the finished implementation. Update
  only current pointers and accurate documentation. Do not inspect runtime
  evidence or cross any SUMO, TraCI, cache-publication, product, release, or
  deployment boundary.
- Completion outcome: all exact-reconciliation behavior and lifecycle tests
  pass under the process guard, v11 is immutably retired, and one reproducible
  unapproved/unexecuted v12 candidate is ready for Sol review.
- Internal checkpoints:
  1. Repair the remaining fixtures and tests, including state-file setup,
     corrected preserved hashes, lifecycle-safe source assertions and any
     implementation defect those hostile tests expose.
  2. Re-prove the real default/cache path, exact save/restore/final ledgers,
     one terminal advance, single-round correction, failure cleanup and cold
     fallback under process-free fakes.
  3. Retire v11 without rewriting its tool/manifest, freeze and verify v12,
     update current pointers/docs, then run the guarded full focused suite and
     an acceptance-criterion self-audit before handoff.
- Allowed files:
  - `run_scenario.py`
  - `run_monthly_warm_state_validation.py`
  - `traffic_sim/simulation/warm_state_boundary.py`
  - `traffic_sim/simulation/monthly_warm_state.py`
  - `traffic_sim/simulation/monthly_sumo.py`
  - `traffic_sim/simulation/warm_state_cache.py`
  - `tests/test_sumo_runtime.py`
  - `tests/test_warm_state_boundary.py`
  - `tests/test_monthly_warm_state.py`
  - `tests/test_warm_state_cache.py`
  - `tests/test_monthly_sumo.py`
  - `tests/test_monthly_warm_state_freeze.py`
  - `tests/test_monthly_warm_state_v11_freeze.py` (supersession and fixture
    closure only; no weakening of v11 rejection or immutable-byte checks)
  - create `tests/test_monthly_warm_state_v12_freeze.py`
  - create `tools/freeze_monthly_warm_state_v12.py`
  - create `validation/monthly_warm_state_manifest_v12.json`
  - `ARCHITECTURE.md`, `IMPROVEMENT_PLAN.md`, `TASKS.md`, `AGENT_NOTES.md`
  - Read-only inputs: v9-v11 tools/manifests and v9/v10 tests; residual-v2 tool
    and contract; tracked meaning-bearing sources needed for v12 fingerprints.
- Forbidden work:
  - No SUMO, TraCI or libsumo import/probe/connection; no socket or application
    child process; no `runs/`, archive, outcome, report or cache access; no
    demand/horizon generation, warming, cache publication or campaign.
  - Preserve every v1-v11 freeze tool and manifest byte-for-byte, preserve v10
    test bytes, and do not open, stat, enumerate, hash or inspect any outcome.
  - Do not make v11 current again, repair or overwrite its manifest, hide its
    source drift, or claim it verified. It is rejected, unapproved/unexecuted.
  - No rounded-tripinfo correction, inferred or blanket offset, tolerance that
    hides loss, per-second stepping, missing identity coverage, unwired helper,
    raw-output rewrite, weaker equality, schedule/seed/policy/threshold change,
    product activation, Stage B, adoption, release, deployment or publication.
- Acceptance criteria:
  1. Revision 3 and mutable checks use the actual preserved v10-tool SHA-256
     `498b164f2866914b87b5d5ebf8c623f63bf3387785cc03aecf532bcd5efd0a2b`;
     the v10 manifest, v9 tool/manifest and residual-v2 tool/contract retain the
     five exact hashes from revision 2.
  2. All eleven recorded revision-2 failures are fixed or replaced by stricter
     current/supersession assertions. Tests create required temporary state
     inputs explicitly, and source-pin detection cannot match its own assertion
     text. No test is skipped, deleted, renamed away, or weakened to pass.
  3. Prefix schema v5 and cache evidence validate the exact save ledger; the
     real default warm invoker passes it to `run_resumed`, uses exact retention,
     captures restore before stepping, advances once to terminal time, reads
     every affected unrounded final value, and enforces exit/timeout/cleanup.
  4. Reconciliation changes only measured deficit-bearing identities and uses
     `round(final_unrounded + positive_deficit, production_precision)` once;
     mixed loss, 1.004+0.002, 5/10/12 and -7.73/-80.62/-138.97 fixtures, tamper,
     identity, cache-hit/miss, fallback and no-double-count cases all pass.
  5. v11 tool and manifest remain exactly
     `3fd9b6d57a1c1520992f6bc252040a0a91f4b2bee7f72e6ed6c4c8b9b43a480d`
     and `d31d1503598c3cdb54feab27ff8d65f6591a585846ff6579fb51efc80875cff8`.
     Its mutable test asserts unconditional retirement through recomposition
     drift and fail-before-approval loading; no successor pointer is pinned.
  6. v12 inherits the unchanged physical case, records v11 as rejected due to
     incomplete process-free closure, adds no runtime claim, binds every
     meaning-bearing source/regression without pinning mutable predecessor test
     files, freezes at a fresh canonical key, and verifies byte-for-byte.
  7. Only v12 becomes the harness and generic current-test pointer. Warming is
     still default-OFF, approval remains fail-first, and documentation states
     that v12 is unproven and requires separate exact-key approval.
  8. The complete focused suite passes under the fresh audit-event guard with
     zero forbidden imports, sockets, application child-process activity,
     forbidden path access, or non-temporary writes.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_sumo_runtime.py tests/test_monthly_sumo.py
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py
    tests/test_warm_state_cache.py tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v9_freeze.py
    tests/test_monthly_warm_state_v10_freeze.py
    tests/test_monthly_warm_state_v11_freeze.py
    tests/test_monthly_warm_state_v12_freeze.py`
  - the identical suite under a fresh audit-event guard that fails on forbidden
    imports, sockets, application child processes, paths and non-temporary writes
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v12.py --verify`
  - exact SHA-256 checks for v9-v11 preserved tools/manifests plus the
    residual-v2 tool/contract before and after; never inspect its outcome
  - `git diff --check --` every allowed changed file and `git status --short`
- Approval gate: `NOT_REQUIRED`; this process-free closure creates only an
  unapproved/unexecuted v12 candidate and grants no runtime, evidence, warming,
  product, release or publication authority.
- Terminal handoff conditions:
  - Hand off only after all checkpoints and criteria pass, or at an AGENTS.md
    terminal boundary with exact evidence and safe options.
  - Stop on any need to rewrite v1-v11 frozen bytes, access runtime evidence,
    invoke a simulator, weaken exactness, or expand product authority.
<!-- ACTIVE_TASK_LUNA_WARM_23_R3_HISTORY_END -->

<!-- ACTIVE_TASK_LUNA_WARM_23_R2_HISTORY_START -->
## ACTIVE_TASK

### LUNA-WARM-23 — Wire exact selective reconciliation and freeze v11

- Task ID: `LUNA-WARM-23`
- Revision: `2`
- Owner: `Luna High`
- Status: `CONCLUDED — FIX_REQUIRED; corrected successor revision required`
- Delivery size: `EXTENDED`
- Objective and scope: Replace rejected v10 with an exact, wired selective
  reconciliation path. Persist the save ledger with prefix evidence, capture
  the restored ledger before any step, retain affected vehicles for final
  unrounded TraCI reads, advance once to the terminal time, and add each
  measured restore deficit before a single production-precision rounding.
  Wire this through the real default invoker, cache-hit/miss paths,
  reconstruction and diagnostics; preserve cold fallback and default-OFF
  warming. Add hostile process-free tests, make retired-manifest refusal
  order-stable, preserve v1-v10 artifacts, and freeze a fresh unapproved and
  unexecuted v11 candidate without reading any runtime evidence.
- Completion outcome: the default validation-only warm path and persisted
  cache contract implement one exact, fail-closed reconciliation algorithm,
  and a reproducible v11 candidate binds it for a future separately approved
  paired campaign; no runtime or evidence boundary is crossed.
- Internal checkpoints:
  1. Define strict v5 prefix evidence and resumed-controller contracts for the
     saved, restored and unrounded final ledgers, with exact identity/time,
     deficit and one-rounding invariants.
  2. Wire those contracts through bootstrap, provisional/cache-hit state,
     `_default_warm_invoker`, reconstruction, diagnostics and cold fallback;
     prove the actual call graph and performance shape with hostile fakes.
  3. Supersede v10 without changing it, freeze and verify v11, update only
     current pointers/docs, and pass the guarded focused suite and self-audit.
- Allowed files:
  - `run_scenario.py`
  - `traffic_sim/simulation/warm_state_boundary.py`
  - `traffic_sim/simulation/monthly_warm_state.py`
  - `traffic_sim/simulation/monthly_sumo.py`
  - `traffic_sim/simulation/warm_state_cache.py`
  - `run_monthly_warm_state_validation.py`
  - `tests/test_sumo_runtime.py`
  - `tests/test_warm_state_boundary.py`
  - `tests/test_monthly_warm_state.py`
  - `tests/test_warm_state_cache.py`
  - `tests/test_monthly_sumo.py`
  - `tests/test_monthly_warm_state_freeze.py`
  - create `tests/test_monthly_warm_state_v11_freeze.py`
  - create `tools/freeze_monthly_warm_state_v11.py`
  - create `validation/monthly_warm_state_manifest_v11.json`
  - `ARCHITECTURE.md`, `IMPROVEMENT_PLAN.md`, `TASKS.md`, `AGENT_NOTES.md`
  - Read-only inputs: v9/v10 tools, manifests and tests; residual-v2 tool and
    contract; tracked sources/tests needed for v11 fingerprints. The
    residual-v2 outcome and every runs/archive/cache path are not inputs.
- Forbidden work:
  - No SUMO, TraCI or libsumo import/probe/connection; no socket or child
    process; no `runs/`, archive, outcome, report or cache inspection; no
    demand/horizon generation, warming, cache publication or campaign.
  - Preserve every v1-v10 freeze tool and manifest byte-for-byte. Preserve v10
    test bytes as well. Preserve the residual-v2 tool and contract; do not
    open, stat, enumerate, hash or otherwise inspect its outcome directory.
  - No correction from rounded tripinfo, blanket offset, inferred deficit,
    tolerance that hides loss, per-second TraCI stepping, missing final-ID
    coverage, or helper that is not invoked by the real default path.
  - Do not mutate SUMO vehicle state, rewrite raw SUMO outputs, weaken exact
    semantic equality, change physical schedules/seeds/policy/thresholds,
    enable product warming, activate Stage B, adopt, release, deploy or publish.
- Acceptance criteria:
  1. Prefix evidence advances to a new exact schema that embeds and validates
     the save ledger and its digest. Bootstrap, provisional state and cache
     publication/restore carry that same evidence; v3/v4 cache evidence is a
     miss and cannot be repaired or interpreted.
  2. `build_sumo_invocation` exposes a default-neutral, typed retention option
     and the warm resumed command binds one exact `--keep-after-arrival` value
     sufficient to query every saved vehicle at the terminal instant. All cold
     and non-warm argv remain byte-semantically unchanged.
  3. The bounded resumed controller connects immediately after load, before a
     simulation step; requires exact warm time and saved active-ID coverage;
     builds the restore audit; advances that same process once to its exact
     terminal time; and reads finite nonnegative unrounded final accumulators
     for every deficit-bearing ID. Any absent ID or extra step fails closed.
  4. Reconciliation derives `saved - restored` only for measured positive
     deficits and computes each affected whole value as
     `round(final_unrounded + deficit, production_precision)` exactly once.
     It never begins from rounded tripinfo. Unaffected tripinfo values stay
     unchanged and raw output files are never rewritten.
  5. Exact tripinfo identity coverage binds final TraCI values to raw outputs.
     Metrics and split diagnostics are derived from the same corrected record
     map and audit, recording affected count, total and digests without trusting
     caller summaries; invalid evidence records a cold fallback and cannot make
     a promotable cache entry.
  6. The real `_default_warm_invoker` builds the post command and calls
     `run_resumed`; neither it nor cache hits can bypass the save ledger,
     restore audit, final reads, normal-exit, timeout, cleanup and fallback
     gates. Tests fail if the helper merely exists but is not wired.
  7. Process-free tests prove one terminal `simulationStep(end_s)`, no
     per-second loop, the `1.004 + 0.002 -> 1.01` counterexample, mixed
     preserved/partial/full loss, exact 5/10/12 fixtures and
     -7.73/-80.62/-138.97 residuals, tamper, wrong-time, identity, retention,
     timeout, cleanup, exit, cache-hit/miss, no-double-count and cold-path cases.
  8. Frozen-manifest loading checks bound source drift before successor schema
     eligibility, keeping refusal fail-closed and the required v9/v10 suites
     green without editing either predecessor test.
  9. v11 inherits v10's exact physical case, adds the retention/final-ledger
     contract and its refutation conditions, binds every meaning-bearing source
     and regression, and becomes only the validation harness/current-test
     pointer. Product warming remains default-OFF and approval remains fail-first.
  10. v11 freezes and verifies at a new content key. Preserved hashes remain:
      v10 tool `498b164f2866914b87c5d5ebf8c623f63bf3387785cc03aecf532bcd5efd0a2b`,
      v10 manifest `55a3c857abad18434a80ea8da861a966a0fed1ff5c35f48579a91dc159f2dc56`,
      v9 tool `23bfb8c0118bb1580f7c128411fa6e2471e6262086bbd9e5cb02758ff291ab4b`,
      v9 manifest `556e6a6fd489b4b7d0527970bb7d2bfa713b313cf0d261c4c0c26bf892afa8a2`,
      residual-v2 tool `4ec7284dc3e0a507fab5552f23f43b3c9fa2c425695523490d1f0fa668367995`
      and contract `d583b7065dfae6e4312319c619d0e75e96f9b6ca34e743df560cde46dd77a892`.
  11. Documentation retracts revision 1's forbidden outcome-rehash claim,
      labels v10 rejected/unexecuted, and describes v11 as an unproven
      process-free hypothesis requiring one future exact-key approval.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_sumo_runtime.py tests/test_monthly_sumo.py
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py
    tests/test_warm_state_cache.py tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v9_freeze.py
    tests/test_monthly_warm_state_v10_freeze.py
    tests/test_monthly_warm_state_v11_freeze.py`
  - the same suite under a fresh audit-event guard that fails on TraCI/libsumo
    import, socket/child-process activity, any `runs/`/archive/outcome/cache
    path access, or writes outside task-created temporary directories
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v11.py --verify`
  - exact SHA-256 checks for the six preserved files named in criterion 10,
    before and after; never hash or inspect the residual-v2 outcome
  - `git diff --check --` every allowed changed file and `git status --short`
- Approval gate: `NOT_REQUIRED`; this revision is process-free and creates only
  an unapproved/unexecuted candidate. It grants no live runtime, evidence,
  cache-publication, product, release or publication authority.
- Terminal handoff conditions:
  - Hand off after all three checkpoints and every acceptance criterion pass.
  - Stop on any need for runtime/evidence access, v1-v10 artifact/test edit,
    correction from rounded data, per-step performance regression, weaker
    equality, product activation, or scope outside the allowed files.
<!-- ACTIVE_TASK_LUNA_WARM_23_R2_HISTORY_END -->

<!-- REJECTED_TASK_LUNA_WARM_23_R1_START -->
## REJECTED_TASK LUNA-WARM-23 revision 1

### LUNA-WARM-23 — Reconcile only measured restore deficits and freeze v10

- Task ID: `LUNA-WARM-23`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review requires a fresh process-free successor`
- Delivery size: `EXTENDED`
- Objective and scope: Replace the refuted all-or-nothing accumulator assumption
  with a selective restore-boundary contract. Capture active vehicle IDs and
  accumulated time loss at the exact save instant and immediately after load,
  before any resumed step. Refuse time/identity drift, derive only measured
  nonnegative deficits, and apply each deficit once to its matching resumed
  tripinfo record at production precision. Integrate this fail-closed path into
  validation-only warming, bump stored evidence semantics, add adversarial
  process-free coverage, preserve executed v2 and frozen v1-v9 artifacts, and
  freeze a fresh unapproved/unexecuted v10 paired candidate. Product warming
  remains default-OFF.
- Completion outcome: a lifecycle-safe v10 candidate reproducibly binds a
  selective, restore-measured correction that can be tested by one future
  approved paired campaign; no simulator or evidence boundary is crossed here.
- Internal checkpoints:
  1. Freeze the save/load ledger, restore-audit and correction invariants in
     strict schemas with fake-driven controller and arithmetic tests.
  2. Integrate the invariants through cache, resumed execution, reconstruction,
     diagnostics and cold fallback; prove the LUNA-WARM-22 pattern and hostile
     identity/precision/timeout cases process-free.
  3. Supersede v9 without changing historical artifacts, freeze v10, update
     current pointers/docs, and pass the guarded focused suite and self-audit.
- Allowed files:
  - `traffic_sim/simulation/warm_state_boundary.py`
  - `traffic_sim/simulation/monthly_warm_state.py`
  - `traffic_sim/simulation/monthly_sumo.py`
  - `traffic_sim/simulation/warm_state_cache.py`
  - `run_monthly_warm_state_validation.py`
  - `tests/test_warm_state_boundary.py`
  - `tests/test_monthly_warm_state.py`
  - `tests/test_warm_state_cache.py`
  - `tests/test_monthly_sumo.py`
  - `tests/test_monthly_warm_state_freeze.py`
  - create `tests/test_monthly_warm_state_v10_freeze.py`
  - create `tools/freeze_monthly_warm_state_v10.py`
  - create `validation/monthly_warm_state_manifest_v10.json`
  - `ARCHITECTURE.md`, `IMPROVEMENT_PLAN.md`, `TASKS.md`, `AGENT_NOTES.md`
  - Read-only inputs: v9 tool/manifest/test, the tracked source/test set needed
    for v10 fingerprints, and the current marked LUNA-WARM-22 review evidence.
- Forbidden work:
  - No SUMO, TraCI or libsumo import/probe/connection; no socket, child process,
    `runs/`, archive, outcome, report or cache inspection; no demand/horizon
    generation, warming, cache publication or campaign execution.
  - Preserve every v1-v9 freeze tool and manifest byte-for-byte. Preserve the
    executed residual v2 tool, contract and outcome byte-for-byte; do not use
    that outcome as a fixture or re-run/recompose its spent execution contract.
  - No blanket boundary offset, inferred deficit, tolerance that hides loss, or
    correction without exact save/load identity coverage and same-instant proof.
  - Do not mutate SUMO vehicle state, weaken exact semantic equality, alter
    campaign schedules/seeds/policy/thresholds, enable product warming, activate
    Stage B, adopt, release, deploy or publish.
- Acceptance criteria:
  1. Architecture and code state the LUNA-WARM-22 finding narrowly: most active
     accumulators survive, a measured minority lose some/all, and neither a
     blanket offset nor no-offset rule is sound. Selection mechanism remains
     unknown and is not required for a restore-time measurement correction.
  2. The save controller captures one canonical `vehicle_id -> timeLoss` ledger
     from the same TraCI connection, exact simulation instant and process that
     writes the state. The new schema validates unique IDs, finite nonnegative
     values, full digest binding and legacy-cache rejection.
  3. A bounded resumed controller launches the unchanged post-state scenario,
     connects before any resumed step, requires the exact warm time and exact
     active-ID set, captures restored accumulated values, then completes that
     same process with enforced timeout, cleanup and normal exit semantics.
  4. Reconciliation rejects restored increases, missing/extra/duplicate IDs,
     absent final tripinfo, non-finite values and altered evidence. It computes
     only positive `saved - restored` deficits, applies each once to the matching
     whole resumed record, normalizes once at production reporting precision,
     and leaves preserved vehicles byte-semantically unchanged.
  5. Production reconstruction and split diagnostics derive from the same raw
     prefix, resumed and restore-audit inputs. They record affected count, exact
     deficit total and digest without trusting caller summaries; any invalidity
     produces a recorded cold fallback and cannot create promotable cache state.
  6. Process-free tests reproduce the exact 5/10/12 and
     -7.73/-80.62/-138.97 pattern as fixtures, plus mixed full/partial/preserved
     loss, rounding-boundary, coverage, wrong-time, timeout, cleanup, tamper,
     legacy-schema, no-double-count and unchanged-cold-path cases.
  7. v10 inherits the exact v9 physical case, records this hypothesis and its
     refutation condition, binds every interpreting source and meaningful
     regression, preserves lifecycle-safe historical pins, and becomes only the
     validation harness/current-test pointer. Approval remains fail-first and
     product-default warming remains OFF.
  8. v10 freezes and verifies byte-for-byte at a new content key; v9's immutable
     tool/manifest bytes remain exact and its source drift makes it naturally
     unexecutable. No test requires editing a predecessor test on future
     supersession.
  9. Documentation records the executed v9/residual-v2 result and the new
     unproven selective correction honestly, removing stale claims that warming
     never executed or that accumulator preservation is universal.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_sumo_runtime.py tests/test_monthly_sumo.py
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py
    tests/test_warm_state_cache.py tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v9_freeze.py
    tests/test_monthly_warm_state_v10_freeze.py`
  - an audit-event-guarded run of that suite proving zero TraCI/libsumo imports,
    sockets, child subprocesses, `runs/`/archive/outcome/cache access or external
    writes by code under test
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v10.py --verify`
  - exact SHA-256 checks for preserved v9 tool/manifest and residual-v2
    tool/contract before and after the task
  - `git diff --check --` every allowed changed file and `git status --short`
- Approval gate: `NOT_REQUIRED`; this task is process-free, touches no runtime or
  evidence root, creates only an unapproved/unexecuted validation candidate and
  keeps every product/release/cache-publication boundary closed.
- Terminal handoff conditions:
  - Hand off after all three checkpoints and every acceptance criterion pass.
  - Stop on any need for live runtime/evidence access, historical artifact edit,
    weaker equality, inferred rather than observed correction, product
    activation, or scope outside the allowed files.
<!-- REJECTED_TASK_LUNA_WARM_23_R1_END -->

<!-- COMPLETED_TASK_LUNA_WARM_22_START -->
## ACTIVE_TASK

### LUNA-WARM-22 — Execute the boundary-aware residual diagnostic v2 once

- Task ID: `LUNA-WARM-22`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved diagnostic evidence; warming remains default-OFF`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, run the frozen boundary-aware v2
  diagnostic once against the existing v9 physical case. Revalidate its
  process-free suite, immutable key, source/parent/v9 fingerprints and exact
  network/archive/runtime prerequisites before creating anything. Require the
  root and staging path to be absent, execute the frozen q10/q50/q90 cold/warm
  pairs without retry, and inspect only the terminal root created by this task.
  Recompute all reports and the global verdict from raw vehicle records. This is
  diagnostic localization only and cannot activate or publish warming.
- Completion outcome: one immutable success or failure artifact identifies the
  first campaign-scale divergence layer without rerun, repair or evidence drift.
- Internal checkpoints: `NOT_APPLICABLE`.
- Allowed files and side effects after approval:
  - Read the frozen v2 contract and its bound tracked sources, preserved v1/v9
    files, tracked network, installed SUMO/TraCI runtime, and exactly the five
    archived-demand files named and hashed by the contract.
  - Run the contract's guarded focused process-free suite and frozen `--verify`.
  - Check exact root/staging absence, then invoke
    `tools/diagnose_monthly_warm_state_residual_v2.py --execute` once with the
    approved content key; allow its private temporary workspaces.
  - Create and inspect only
    `validation/monthly_warm_state_residual_v2_outcome`, including canonical
    identity, member/digest, raw/report/verdict and production-consistency
    recomputation. Edit `TASKS.md` and `AGENT_NOTES.md` only for handoff.
- Forbidden work:
  - No action covered above before exact approval is recorded. No second
    invocation, rerun, resume, repair, cleanup or mutation of a terminal root.
  - No source, test, contract, v1/v9 freeze, demand, network, runtime, cache,
    policy, threshold, production-default or product behavior edits.
  - No other `runs/`, archive member, outcome, report, campaign or cache access;
    no demand/horizon generation, persistent warming/cache publication,
    product activation, Stage B, adoption, release, deployment or publication.
- Acceptance criteria:
  1. Luna matches task/revision, exact quoted approval, key and root before any
     process, runtime/archive preflight, root check, execution or inspection.
  2. The guarded six-file focused suite passes with zero TraCI/libsumo imports,
     sockets, subprocesses, `runs/`/archive/outcome access or external writes;
     the v2 contract reproduces at the approved key and every bound v1/v9/source
     fingerprint remains exact.
  3. Preflight then verifies root/staging absence, exact executable SUMO, TraCI
     origin/API, tracked network and the five archived-demand hashes/metadata in
     the frozen order before one execution. Any failure before execution creates
     no root and consumes no attempt.
  4. Execute exactly once, without retry. Success or post-start failure publishes
     only the frozen terminal allowlist via verified staging and rename-last;
     failure preserves the original error and completed-arm ledger.
  5. Inspect only the task-created root. Reverify regular files, exact allowlist,
     every digest, embedded key, identity coverage, warm attempts and raw-derived
     report/global-verdict recomputation without changing any byte.
  6. Record the mutually exclusive classification and bounded exemplars exactly
     as diagnostic evidence. Make no claim of equivalence, speedup, readiness,
     adoption or release, and do not implement a fix in this task.
- Focused checks after approval:
  - guarded focused command frozen in the active v2 contract (`189` tests at
    freeze; current count must pass, not be forced to match)
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/diagnose_monthly_warm_state_residual_v2.py --verify`
  - ordered runtime/network/five-file archive/root-absence preflight and one
    `--execute 03f5260af470a4c29b17216129c145e06e39df6b3fe35b6f38f85a07c946f908`
  - v2 success/failure validator plus independent raw report/verdict recompute
  - `git diff --check -- TASKS.md AGENT_NOTES.md` and `git status --short`
- Approval gate: `REQUIRED — SATISFIED; one-shot consumed` for content key
  `03f5260af470a4c29b17216129c145e06e39df6b3fe35b6f38f85a07c946f908`
  and root `validation/monthly_warm_state_residual_v2_outcome`.
  - Exact user message received 2026-08-02:
    > I explicitly approve LUNA-WARM-22 revision 1 to perform the one-time
    > non-resumable monthly_warm_state_residual_v2 SUMO/TraCI diagnostic at
    > content key 03f5260af470a4c29b17216129c145e06e39df6b3fe35b6f38f85a07c946f908
    > and artifact root validation/monthly_warm_state_residual_v2_outcome,
    > including the frozen guarded process-free checks, canonical contract/source/v1/v9
    > checks, exact SUMO executable, TraCI origin/API, network and five-file
    > archived-demand preflight, root/staging absence check, one frozen execution,
    > task-created temporary workspaces, and inspection and validator recomputation
    > only within that task-created root. No rerun, resume, repair, other
    > runs/outcome/report/cache inspection, demand or horizon generation,
    > persistent warming/cache publication, product activation, Stage B, adoption,
    > release mutation, deployment or publication is approved.
  - Sol recorder/date: `Sol High / 2026-08-02`.
- Terminal handoff conditions:
  - Until approval: remain blocked and perform no covered action.
  - After approval: hand off after the single terminal artifact is validated;
    stop on preflight failure, any existing root/staging path, source/identity
    drift, need for a retry/repair/other evidence, or broader authority.
<!-- COMPLETED_TASK_LUNA_WARM_22_END -->

<!-- COMPLETED_TASK_LUNA_WARM_20_START -->
## ACTIVE_TASK

### LUNA-WARM-20 — Execute the frozen population-semantics v2 diagnostic once

- Task ID: `LUNA-WARM-20`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved the bounded diagnostic evidence;
  non-executable and not warming/release evidence`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, validate the frozen v2 identity,
  guarded focused suite, tracked network, SUMO executable and installed TraCI
  origin/API; prove the exact root and staging path are absent; invoke the
  frozen six-arm synthetic diagnostic exactly once; then inspect and recompute
  only its task-created terminal root. Preserve either success or failure
  evidence without retry, resume, cleanup, repair or source changes. Report the
  mechanism verdict or exact terminal failure as diagnostic evidence only.
- Completion outcome: one consumed v2 attempt with a validator-consistent exact
  terminal artifact and an honest bounded interpretation, or a preflight stop
  before execution; no product warming or activation.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - Read only `tools/diagnose_warm_state_population_semantics.py`,
    `validation/warm_state_population_semantics_v2_contract.json`, its seven
    bound source files, `sumo/net.net.xml` and the four focused test files.
  - After approval, create only
    `validation/warm_state_population_semantics_v2_outcome` and the diagnostic's
    task-created temporary workspace; inspect and recompute only that root.
  - Edit `TASKS.md` and `AGENT_NOTES.md` only for the terminal handoff.
- Forbidden work:
  - No source, test, contract, network, v1 artifact or terminal evidence edits.
  - No rerun, resume, repair, cleanup or second invocation after any execution
    attempt, regardless of interruption, nonzero exit or malformed evidence.
  - No `runs/` access, archived demand, other outcome/report/campaign/cache
    inspection, demand or horizon generation, persistent warming or cache
    publication.
  - No product activation, Stage B, adoption, release mutation, deployment or
    publication; diagnostic replay is not release evidence.
- Acceptance criteria:
  1. Before any approved action, the exact approval message, key, root, task ID,
     revision, scope, date and Sol recorder match this task. Otherwise stop.
  2. The guarded four-file suite passes with zero forbidden attempts; the v2
     contract reproduces byte-for-byte at key
     `7206ec40c7b96288ff8b998ccf780c6089373a437e141bd6bb2a38ad85d86910`,
     all seven source fingerprints and network SHA match, and status remains
     `frozen_unapproved_unexecuted` before invocation.
  3. Preflight confirms the exact SUMO executable, TraCI resolves only from that
     installation's `tools/traci`, and `init`, `close`, `simulationStep`,
     `simulation.getTime`, `simulation.saveState` and `vehicle.getIDList` exist;
     no connection or SUMO process starts during preflight.
  4. Exact root `validation/warm_state_population_semantics_v2_outcome` and its
     `.partial` staging path are absent before the attempt. Any mismatch is
     terminal and creates nothing.
  5. Invoke exactly once:
     `PYTHONDONTWRITEBYTECODE=1 python3
     tools/diagnose_warm_state_population_semantics.py --execute
     7206ec40c7b96288ff8b998ccf780c6089373a437e141bd6bb2a38ad85d86910`.
     No wrapper, retry or alternate token/root is allowed.
  6. Inspect only the exact task-created root. Verify its exact member allowlist,
     every digest, embedded contract/key/fixture and mutually exclusive terminal
     schema. On failure, `validate_failure_artifact` passes and the original
     error/phase/evidence is reported. On success, raw six-arm records rebuild
     both comparisons and the stored result/verdict passes `validate_result`.
  7. Preserve the root byte-for-byte after inspection. Record the invocation
     count, arm coverage/exits, boundary facts, classification and recommendation
     or exact failure. Do not turn diagnostic evidence into equivalence,
     performance, warming-readiness, adoption or release claims.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_population_semantics.py
    tests/test_warm_state_time_loss_semantics.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/diagnose_warm_state_population_semantics.py --verify`
  - exact canonical key, seven source fingerprints, network SHA, SUMO executable
    and TraCI origin/API preflight; exact root and `.partial` absence check
  - the single frozen `--execute` command above
  - exact-root inventory/digest/schema/identity and production recomputation;
    targeted `git diff --check -- TASKS.md AGENT_NOTES.md`; `git status --short`
- Approval gate: `REQUIRED — SATISFIED`.
  - Exact scope/key/root: one non-resumable synthetic v2 SUMO/TraCI diagnostic,
    its named preflight and focused checks, one frozen six-arm invocation, and
    inspection/recomputation only inside
    `validation/warm_state_population_semantics_v2_outcome`, content key
    `7206ec40c7b96288ff8b998ccf780c6089373a437e141bd6bb2a38ad85d86910`.
  - Exact user message received:
    > I explicitly approve LUNA-WARM-20 revision 1 to run the one-time
    > non-resumable warm_state_population_semantics_v2 synthetic SUMO/TraCI
    > diagnostic at content key
    > 7206ec40c7b96288ff8b998ccf780c6089373a437e141bd6bb2a38ad85d86910 and
    > artifact root validation/warm_state_population_semantics_v2_outcome,
    > including the named guarded focused process-free checks, canonical
    > contract/source/network checks, exact SUMO executable and TraCI origin/API
    > preflight, root and staging-path absence checks, one frozen six-arm
    > execution, its task-created temporary workspace, and inspection and
    > recomputation only within that task-created root. No rerun, resume, repair,
    > runs/ access, archived demand, other outcome/report/campaign/cache
    > inspection, demand or horizon generation, persistent warming or cache
    > publication, product activation, Stage B, adoption, release mutation,
    > deployment or publication is approved.
  - User-message date: `2026-08-01`.
  - Sol recorder/date: `Sol High / 2026-08-01`.
- Terminal handoff conditions:
  - Remain blocked until Sol records the exact approval. After approval, Luna
    hands off after a preflight stop or the single invocation and bounded audit.
  - Stop without retry, cleanup or repair on any key/source/network/environment/
    root mismatch, interruption, nonzero arm, preservation fault, malformed
    evidence, out-of-scope access need or contract expansion.
<!-- COMPLETED_TASK_LUNA_WARM_20_END -->

<!-- SUPERSEDED_TASK_LUNA_WARM_15_REV4_START -->
## ACTIVE_TASK

### LUNA-WARM-15 — Complete the resolver freeze as guarded v8

- Task ID: `LUNA-WARM-15`
- Revision: `4`
- Owner: `Luna High`
- Status: `BLOCKED — Sol review found unsatisfied tests and prohibited access`
- Delivery size: `STANDARD`
- Objective and scope: Close only the two process-free revision-3 review gaps.
  Preserve the three v7 files byte-for-byte. Correct the generic current-
  manifest pointer and stale `sumo_home` substring assertion, bind the complete
  resolver regression set, and freeze a fresh canonical v8 candidate inheriting
  v7's physical facts. Point the non-executing harness at v8 and document v7's
  rejection without changing resolver/controller behavior. Retain the consumed
  passing TraCI probe as evidence only; do not rerun or import installed TraCI.
  Do not access `runs/` or outcomes, execute SUMO, warm anything, activate a
  product path, adopt, release, deploy, or publish.
- Completion outcome: every focused process-free check passes; v7 remains
  byte-identical; the generic contract tests target v8 and retain structural
  no-simulator protection; and one reproducible, uniquely keyed v8 manifest is
  `frozen_unapproved_unexecuted` with complete resolver source/regression
  binding. Warming remains default-OFF and has never executed.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Read tracked source/tests, `sumo/net.net.xml`, and v1-v7 tracked warm-state
    manifests/tools/tests needed for inheritance and process-free verification.
    Do not read any `runs/` path, outcome, campaign report or cache artifact.
  - Edit `tests/test_monthly_warm_state_freeze.py` only to point `CURRENT` at
    v8 and remove `sumo_home` from the stale substring-ban tuple while
    preserving its AST/module-scope simulator and execution-entry guards.
  - Edit `run_monthly_warm_state_validation.py` only to make v8 the default
    manifest and accurately describe v7 as rejected/unapproved/unexecuted.
  - Create `tools/freeze_monthly_warm_state_v8.py`,
    `tests/test_monthly_warm_state_v8_freeze.py`, and
    `validation/monthly_warm_state_manifest_v8.json` from the reviewed v7
    candidate with only the revision-4 contract corrections.
  - Edit `ARCHITECTURE.md`, `IMPROVEMENT_PLAN.md`, `TASKS.md`, and
    `AGENT_NOTES.md` only for the v7 review disposition, v8 frozen status,
    exact checks, and terminal handoff.
  - Use task-local temporary guard/work directories for process-free checks.
- Forbidden work:
  - Do not edit, delete, rename, normalize, or regenerate
    `tools/freeze_monthly_warm_state_v7.py`,
    `tests/test_monthly_warm_state_v7_freeze.py`, or
    `validation/monthly_warm_state_manifest_v7.json`; their exact SHA-256 values
    are frozen in criterion 1.
  - Do not edit runtime/controller/monthly production behavior, v1-v7 artifacts
    or other tests/sources beyond the exact allowed edits above.
  - Do not import installed `traci` or `libsumo`, rerun any TraCI probe, call or
    connect TraCI, open a socket, start a child subprocess from a check, perform
    executable/network preflight, execute SUMO, or use `--execute`.
  - Do not access any `runs/` path, archived demand, outcome, report, campaign
    root or cache artifact; do not create an outcome or campaign root.
  - Do not generate demand, warm horizons, publish cache material, activate
    warming or Stage B, alter product/API/UI/policies/thresholds, adopt, release,
    deploy, or publish. Do not store approval in v8 or claim equivalence,
    speedup, readiness, or successful warming.
- Acceptance criteria:
  1. Before and after all work, require SHA-256
     `6aea802198ff8f51176d1aad9efad22fb48bdc24ebe86b635a9203d1302ea468`
     for `tools/freeze_monthly_warm_state_v7.py`,
     `1c6272dba01e30bfb3cb705888e0c564a22a0287267ffc90030d256b23bf83c6`
     for `tests/test_monthly_warm_state_v7_freeze.py`, and
     `9c0ed7610d34e360a4a1c7600f38f7cc9211cb32106e8e3c444c18727008968e`
     for `validation/monthly_warm_state_manifest_v7.json`.
  2. The generic freeze test makes exactly the authorized semantic corrections:
     `CURRENT` names v8, and the obsolete raw `sumo_home` ban is removed while
     structural guards still forbid module-scope simulator imports and actual
     SUMO entry points. No assertion is weakened beyond that stale substring.
  3. The v8 freeze inherits v7 by its exact canonical key, retains all physical
     case/archive/route/network/accounting/attempt/resolver facts, and changes no
     runtime rule. Its source fingerprints bind every production source and the
     complete relevant regression/contract set, including
     `tests/test_sumo_runtime.py`, `tests/test_warm_state_boundary.py`,
     `tests/test_monthly_warm_state.py`,
     `tests/test_monthly_warm_state_freeze.py`,
     `tests/test_monthly_sumo.py`, and the v8 freeze regression.
  4. v8 is uniquely keyed, reproducible byte-for-byte and no-clobber,
     `frozen_unapproved_unexecuted`, stores no approval, identifies v7 as a
     rejected unapproved candidate with incomplete regression binding, and
     makes no execution, equivalence, speedup, adoption or readiness claim.
  5. The harness default is v8; non-executing validation accepts v8, source or
     resolver-contract tampering fails closed, and v7 is naturally non-current
     without changing any v7 byte. No execution path is invoked.
  6. Run the exact focused suite under task-local guards that block and record
     socket, child-process, installed-TraCI/libsumo, executable and `runs/`
     activity. Require every test to pass and zero guard violation. Do not run
     the revision-3 import-only probe.
  7. Architecture and improvement notes state the exact boundary: revision 3's
     one probe passed and is consumed; v7 was rejected process-free for an
     incomplete test/source contract; v8 is frozen but unapproved/unexecuted;
     warming remains default-OFF and has never executed.
  8. Verify v7 hashes again, v8 canonical identity and source fingerprints,
     stale-current references, focused checks and diff hygiene; self-audit all
     criteria and hand off once for Sol review.
- Focused checks:
  - `shasum -a 256 tools/freeze_monthly_warm_state_v7.py
    tests/test_monthly_warm_state_v7_freeze.py
    validation/monthly_warm_state_manifest_v7.json` before and after work
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v8.py --write`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v8.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v8.json`
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_sumo_runtime.py tests/test_warm_state_cache.py
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py
    tests/test_monthly_sumo.py tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py
    tests/test_monthly_warm_state_v5_freeze.py
    tests/test_monthly_warm_state_v6_freeze.py
    tests/test_monthly_warm_state_v7_freeze.py
    tests/test_monthly_warm_state_v8_freeze.py`
  - task-local socket/child-process/installed-TraCI/libsumo/executable/`runs/`
    audit guard around that exact suite; require zero violations
  - canonical v8 key/parent/source/schema/resolver/identity/status verifier
  - stale v7-current/default and forbidden installed-import/probe search
  - `git diff --check -- run_monthly_warm_state_validation.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v8_freeze.py
    tools/freeze_monthly_warm_state_v8.py
    validation/monthly_warm_state_manifest_v8.json ARCHITECTURE.md
    IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate:
  - `REQUIRED — RECORDED`
  - Prior evidence disposition: revision 3's single passing installed-TraCI
    import-only probe is `CONSUMED`; revision 4 may retain its result but may not
    rerun, reproduce, widen or replace it.
  - Exact authorized scope: one process-free LUNA-WARM-15 revision-4 correction
    that preserves v7 unchanged; updates only the generic current-manifest
    pointer and stale `sumo_home` substring assertion; binds
    `tests/test_warm_state_boundary.py` and
    `tests/test_monthly_warm_state.py`; freezes one fresh unapproved/unexecuted
    v8 candidate; and runs focused process-free checks. No additional TraCI
    import/probe/call/connection, SUMO, libsumo, `runs/` or outcome access,
    campaign, cache publication, warming, adoption, release, deployment or
    publication is authorized.
  - Exact user message:
    “Sol, create LUNA-WARM-15 revision 4 as a process-free correction. Retain
    revision 3’s consumed passing TraCI probe without rerunning it; edit
    tests/test_monthly_warm_state_freeze.py only for the current-manifest
    pointer and stale sumo_home substring assertion; bind
    tests/test_warm_state_boundary.py and tests/test_monthly_warm_state.py;
    preserve v7 unchanged and freeze a fresh unapproved and unexecuted v8
    candidate; and run the focused process-free checks. No additional TraCI
    import or probe, TraCI call or connection, SUMO, libsumo, runs or outcome
    access, campaign, cache publication, warming, adoption, release, deployment,
    or publication is approved.”
  - User-message date: `2026-07-31`
  - Sol recorder/date: `Sol High / 2026-07-31`
  - Disposition: `CONSUMED` by the revision-4 attempt. That attempt produced a
    coherent v8 candidate but also read five archived-demand files through a
    prescribed legacy test despite the explicit no-`runs/` boundary; it is not
    reusable authority for revision 5.
- Terminal handoff conditions:
  - Hand off once after every criterion passes with the v8 key and exact
    unapproved/unexecuted status.
  - Stop on any v7 hash change, installed TraCI/libsumo import, probe attempt,
    socket/child-process/executable/`runs/` activity, need for production logic
    change, outcome/campaign access, artifact-contract expansion, unrelated-file
    mutation, approval boundary, or three serious failed approaches. Do not
    repair or replace v7 and do not compensate with any real execution.
<!-- SUPERSEDED_TASK_LUNA_WARM_15_REV4_END -->

<!-- SUPERSEDED_TASK_LUNA_WARM_15_REV3_START -->
## ACTIVE_TASK

### LUNA-WARM-15 — Fix TraCI discovery and freeze guarded v7

- Task ID: `LUNA-WARM-15`
- Revision: `3`
- Owner: `Luna High`
- Status: `BLOCKED — Sol review found incomplete checks and source binding`
- Delivery size: `STANDARD`
- Objective and scope: Make production resolve TraCI from the exact active SUMO
  home, honoring `SUMO_HOME` and rejecting missing or wrong-origin modules
  before any process or artifact-root creation. Exercise the resolver through
  Python's import machinery with a temporary fake SUMO tools package, then run
  one direct fresh-interpreter import-only probe of the actual installed
  package under audit-event guards and freeze v7 only if its exact origin and
  required API validate. Wire the same resolver into mandatory pre-root
  campaign preflight. Bind the repair, tests, inherited physical case, and
  spent v6 diagnosis in canonical v7. Do not connect TraCI, run SUMO, inspect
  outcomes or archives, activate warming, or approve v7.
- Completion outcome: the v6 `No module named 'traci'` defect is fixed and
  process-free tests and one actual import-only probe prove the production
  resolver, installed package origin/API, and preflight cannot regress; one
  fresh canonical v7 contract exists as unapproved and unexecuted. No TraCI
  connection, SUMO/child process, run root, cache, adoption, or readiness claim
  is created.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Read tracked source, tests, `sumo/net.net.xml`, and
    `validation/monthly_warm_state_manifest_v6.json`; do not read any `runs/`
    path or outcome.
  - Edit `traffic_sim/simulation/runtime.py`,
    `traffic_sim/simulation/warm_state_boundary.py`,
    `run_monthly_warm_state_validation.py`,
    `tests/test_warm_state_boundary.py`, `tests/test_monthly_warm_state.py`,
    `tests/test_monthly_sumo.py`, and new
    `tests/test_sumo_runtime.py`,
    `tests/test_monthly_warm_state_v7_freeze.py`.
  - Edit `tests/test_monthly_warm_state_v6_freeze.py` only to add v6
    supersession assertions after the authorized installed-package probe passes.
  - Create `tools/freeze_monthly_warm_state_v7.py` and
    `validation/monthly_warm_state_manifest_v7.json`.
  - Edit `ARCHITECTURE.md`, `IMPROVEMENT_PLAN.md`, `TASKS.md`, and
    `AGENT_NOTES.md` only for exact v6 diagnosis, v7 frozen status, and the
    terminal handoff.
  - Use task-local temporary directories containing a fake
    `<sumo_home>/tools/traci` package for process-free resolver tests.
  - Import the actual installed `traci` package exactly once, directly in a
    fresh Python interpreter through the production resolver, solely to validate
    resolved origin and required API attributes. Use audit-event guards that
    record and block socket and child-subprocess activity without monkeypatching
    or replacing `socket.socket`. Inspect only module metadata/attributes; make
    no TraCI call.
- Forbidden work:
  - No access of any kind to `runs/`, archived demand, campaign reports,
    outcome roots, old validation outcomes, or cache artifacts.
  - Do not run SUMO, call or connect TraCI, import libsumo, open sockets, start
    child subprocesses, perform executable/network preflight, run a campaign,
    or run the harness with `--execute`. The installed `traci` import is limited
    to the one recorded origin/API probe; no other installed simulator import is
    allowed.
  - Do not mutate v1-v6 manifests/tools/evidence, network, demand, unrelated
    source/tests, product/API/UI, policies, thresholds, release, or deployment.
  - Do not generate demand, warm horizons, create a campaign root, publish
    cache material, activate product warming or Stage B, release, or publish.
  - Do not store approval in v7, reuse the spent v6 key, or claim v7 has run,
    passed, improved speed, or established production equivalence.
- Acceptance criteria:
  1. `runtime.sumo_home()` honors a non-empty `SUMO_HOME` deterministically and
     otherwise resolves the installed `sumo` package; invalid homes fail with a
     specific error. Tests use temporary paths and injected/import-isolated
     packages only.
  2. `WarmPrefixController` resolves `traci` from the exact
     `<sumo_home>/tools` passed to `run_prefix`, proves the imported module's
     origin is inside that directory, preserves the explicit injected-module
     seam, and fails before launcher/socket/process activity when the package is
     absent, malformed, or resolves elsewhere.
  3. The executable harness invokes that same production resolver as a
     mandatory preflight after approval-token validation but before output-root
     existence checking, creation, or paired execution. Focused tests prove the
     ordering and prove a resolver failure creates no root and invokes no
     campaign.
  4. The resolver regression uses Python's real import machinery against a
     temporary fake `tools/traci` package and would fail against v6's bare
     import. Guards prove no installed simulator, socket, subprocess, `runs/`,
     or executable is touched in that suite and isolate `sys.path`/`sys.modules`
     effects.
  5. After the fake-package suite passes, run exactly one direct import-only
     production probe in a fresh Python interpreter. Install audit-event guards
     that record and block socket and child-subprocess activity without
     replacing or monkeypatching `socket.socket`. Require `traci.__file__` to
     resolve inside the exact active `<sumo_home>/tools/traci` tree and require
     callable `init`, `close`, and `simulationStep` plus
     `simulation.getTime`, `simulation.saveState`, and `vehicle.getIDList`.
     Record origin and API status only. Any guarded activity, missing/wrong
     origin, missing API, libsumo import, or exception is terminal and v7 must
     not be frozen.
  6. Freeze v7 canonically and no-clobber from the tracked v6 parent without
     reading v6's outcome. Bind every meaning-bearing repaired source and
     regression, exact parent key, inherited route/archive/network facts, three
     identities, snapshot/accounting/attempt contracts, resolver-origin and
     pre-root-preflight rules, and the exact v6 failure diagnosis.
  7. The v7 manifest is uniquely keyed, `frozen_unapproved_unexecuted`, stores
     no approval, supersedes the spent v6 key, and validates byte-for-byte.
     Non-executing harness validation accepts it; tampered resolver contract,
     source fingerprints, parent identity, or status fails closed.
  8. Only after the probe passes, add v6 supersession assertions to
     `tests/test_monthly_warm_state_v6_freeze.py`. Update architecture/
     improvement status without rewriting history: v6 diagnosed import
     resolution, zero warm executions occurred, warming remains off, and v7 is
     process-free/unapproved/unexecuted. Run every focused check, self-audit
     each criterion, and hand off once.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_sumo_runtime.py tests/test_warm_state_cache.py
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py
    tests/test_monthly_sumo.py tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py
    tests/test_monthly_warm_state_v5_freeze.py
    tests/test_monthly_warm_state_v6_freeze.py
    tests/test_monthly_warm_state_v7_freeze.py`
  - process/socket/installed-TraCI/libsumo/`runs/` guard around that exact suite
  - after the fake-package suite passes, exactly once:
    `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --check-traci-import-only`
    directly in a fresh interpreter under audit-event guards that record and
    block socket and child-subprocess activity without replacing
    `socket.socket`, plus libsumo/`runs/` guards
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v7.py --write`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v7.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v7.json`
  - canonical v7 key/parent/source/schema/resolver/identity/status verifier
  - stale v6 key and bare-production-import search
  - `git diff --check -- traffic_sim/simulation/runtime.py
    traffic_sim/simulation/warm_state_boundary.py
    run_monthly_warm_state_validation.py tests/test_sumo_runtime.py
    tests/test_warm_state_boundary.py tests/test_monthly_warm_state.py
    tests/test_monthly_sumo.py tests/test_monthly_warm_state_v6_freeze.py
    tests/test_monthly_warm_state_v7_freeze.py
    tools/freeze_monthly_warm_state_v7.py
    validation/monthly_warm_state_manifest_v7.json ARCHITECTURE.md
    IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate:
  - `REQUIRED — RECORDED`
  - Revision-2 disposition: its one approved probe was `CONSUMED` by the failed
    wrapper-shaped attempt and is not retried or reused.
  - Exact authorized scope: LUNA-WARM-15 revision 3 may perform one direct
    import-only TraCI probe in a fresh Python interpreter using audit-event
    guards that block and record socket and child-subprocess activity without
    replacing `socket.socket`; validate only the exact TraCI origin and required
    API; and, if and only if it passes, complete the already-scoped v7 freeze
    and update `tests/test_monthly_warm_state_v6_freeze.py` only with v6
    supersession assertions. No TraCI call or connection, SUMO execution,
    libsumo, `runs/` access, outcome inspection, campaign, cache publication,
    warming, adoption, release, deployment, or publication is authorized.
  - Exact user message:
    “Sol, create LUNA-WARM-15 revision 3 and authorize one direct import-only
    TraCI probe in a fresh Python interpreter using audit-event guards that
    block and record socket and child-subprocess activity without replacing
    socket.socket. Validate only the exact TraCI origin and required API. If and
    only if it passes, complete the already-scoped v7 freeze and update
    tests/test_monthly_warm_state_v6_freeze.py only with v6 supersession
    assertions. No TraCI call or connection, SUMO execution, libsumo, runs
    access, outcome inspection, campaign, cache publication, warming, adoption,
    release, deployment, or publication is approved.”
  - User-message date: `2026-07-31`
  - Sol recorder/date: `Sol High / 2026-07-31`
  - Disposition: `CONSUMED` by the single passing revision-3 import-only probe;
    it is not authority for another probe or for revision-4 scope.
- Terminal handoff conditions:
  - Hand off after all criteria pass with the final v7 key and exact
    unapproved/unexecuted status.
  - Stop on any origin/API probe failure, guarded socket/child-subprocess
    activity, TraCI call/connection, libsumo activity, need for archive/outcome
    access, campaign execution,
    artifact-contract or architecture expansion, approval boundary,
    unrelated-file mutation, or three recorded serious failed approaches.
    Never freeze v7 after a failed probe or use a real run to compensate for a
    missing process-free regression.
<!-- SUPERSEDED_TASK_LUNA_WARM_15_REV3_END -->

<!-- COMPLETED_TASK_LUNA_WARM_14_START -->
## ACTIVE_TASK

### LUNA-WARM-14 — Execute diagnostic-complete v6 paired warm-state campaign once

- Task ID: `LUNA-WARM-14`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved the honest failed result; v6 key spent`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, validate the canonical v6
  manifest, its 16 bindings, diagnostic contract, physical case, archived
  demand, network, SUMO executable, and absent exact output root. Execute the
  frozen three-identity cold-versus-warm campaign exactly once. Inspect only
  its task-created root and recompute canonical identity and production
  consistency, including specific bootstrap diagnostics, arm outcomes,
  semantic comparisons, failures, runtime, RSS, and validation-local cache
  material. Preserve an honest pass, fail, incomplete, or corrupt result
  without rerun, resume, repair, or evidence mutation. Do not activate product
  warming, Stage B, release, deployment, or publication.
- Completion outcome: one immutable v6 campaign result establishes whether all
  three frozen identities execute from warm state with production-equivalent
  observations and measurable resource use; any fallback records its specific
  cause. Product warming remains off pending a separate Sol adoption decision.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Read the tracked
    `validation/monthly_warm_state_manifest_v6.json`, its 16
    fingerprint-bound tracked sources/tests, the ten focused test files named
    below, and `sumo/net.net.xml`.
  - After exact approval is recorded, read only
    `runs/demand-20260721-222017-41bc682a-bbe1/demand_meta.json`,
    `manifest.json`, `calibrated.rou.xml`, `calibrated_v1.rou.xml`, and
    `calibrated_v2.rou.xml` for the frozen archive preflight.
  - After approval, run the exact SUMO/version/network/archive/root-absence
    preflight and the single frozen command below. Create only task-local
    temporary workspaces and
    `runs/monthly-warm-state-validation/df96224408d700e2e20d218a84c4e798c6a4a33ca837288d2f02f0b185052ca8`,
    including validation-only cache material inside that root.
  - Inspect, enumerate, stat, hash, and parse only that task-created exact root
    after execution. Edit `TASKS.md` and `AGENT_NOTES.md` only for the terminal
    handoff.
- Forbidden work:
  - While approval is unsatisfied: no `runs/` access or root-existence check,
    archive or executable/network preflight, SUMO, TraCI, socket, campaign,
    outcome creation, or outcome inspection.
  - No rerun, resume, repair, partial reuse, alternate key/root, or inspection
    of any other `runs/`, report, campaign, cache, or outcome path.
  - Do not generate demand, warm horizons, persist warming outside the exact
    task root, or mutate source, tests, manifests, tools, network, archived
    demand, product/API/UI, policies, thresholds, architecture, or release.
  - No cache adoption, product activation, Stage B, deployment, release, or
    publication.
- Acceptance criteria:
  1. Before any approved side effect, rerun the exact ten-file process-free
     suite, v6 byte-for-byte freeze verification, non-executing harness
     validation, and process/socket/TraCI guard. All pass without `runs/`
     access.
  2. After approval, preflight proves the canonical manifest key is exactly
     `df96224408d700e2e20d218a84c4e798c6a4a33ca837288d2f02f0b185052ca8`;
     all 16 fingerprints, schemas, physical-case fields, three identities,
     schedule, warm points, snapshot settings, tracked network, exact SUMO
     executable/version, demand build `2ac04275daabe93c`, 480 intervals, and
     five archive-file hashes match; the exact output root does not exist.
  3. Execute exactly once:
     `PYTHONDONTWRITEBYTECODE=1 python3
     run_monthly_warm_state_validation.py --manifest
     validation/monthly_warm_state_manifest_v6.json --execute
     --approval-token
     df96224408d700e2e20d218a84c4e798c6a4a33ca837288d2f02f0b185052ca8`.
     A nonzero exit or interruption is preserved as evidence and is not
     retried, resumed, repaired, or replaced.
  4. Root-only inspection recomputes the record's canonical content key and
     manifest binding; proves exact 3/3 identity and arm coverage, exactly
     three ordered identity-bound attempts, consistent terminal outcomes,
     complete execution evidence, and no unreported fallback; and verifies
     production-equivalence fields and every semantic comparison.
  5. A bootstrap fallback must preserve its specific preceding cause, such as
     `controller_absent`, `snapshot_failed`, or `state_file_missing`; a bare
     `bootstrap_failed` without the emitted specific cause is incomplete
     diagnostics and cannot support a pass or another blind-run recommendation.
  6. A pass requires actual warm execution for all three identities, zero
     semantic mismatches and observation failures, correct boundary/attempt
     diagnostics, and exactly the expected task-local cache entries. Any other
     result remains fail/incomplete/corrupt with its readable reason and no
     publishable cache claim.
  7. Report wall time and peak RSS honestly. Make no speed or readiness claim
     unless the record passes. Product-default warming remains off and no
     adoption or release artifact is created. Self-audit every criterion and
     hand off once for Sol review.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_cache.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py
    tests/test_monthly_warm_state_v5_freeze.py
    tests/test_monthly_warm_state_v6_freeze.py`
  - process/socket/TraCI guard around that exact suite
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v6.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v6.json`
  - after approval, canonical manifest/source/schema/SUMO/network/archive/
    demand/identity/root-absence preflight
  - the exact single execution command in acceptance criterion 3
  - exact-root-only enumeration, canonical-record recomputation,
    warm-attempt/cause/arm/semantic/cache consistency verifier
  - `git diff --check -- TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate:
  - `REQUIRED — RECORDED`
  - Exact authorized scope/key/root: one non-resumable
    `monthly_warm_state_v6` paired cold-versus-warm SUMO/TraCI campaign,
    including the named process-free checks, canonical contract checks, exact
    executable/network/archive preflight, one frozen execution, task-created
    temporary workspaces and root-local validation cache, and inspection and
    production-consistency verification only within
    `runs/monthly-warm-state-validation/df96224408d700e2e20d218a84c4e798c6a4a33ca837288d2f02f0b185052ca8`
    at content key
    `df96224408d700e2e20d218a84c4e798c6a4a33ca837288d2f02f0b185052ca8`.
  - Exact user message:
    “I explicitly approve LUNA-WARM-14 revision 1 to run the one-time
    non-resumable monthly_warm_state_v6 paired cold-versus-warm SUMO/TraCI
    campaign at content key
    df96224408d700e2e20d218a84c4e798c6a4a33ca837288d2f02f0b185052ca8
    and artifact root
    runs/monthly-warm-state-validation/df96224408d700e2e20d218a84c4e798c6a4a33ca837288d2f02f0b185052ca8,
    including the named focused process-free checks, canonical
    manifest/source/schema and warm-attempt-contract checks, exact SUMO
    executable/network/archived-demand preflight, one frozen execution,
    task-created temporary workspaces and validation-only cache material
    inside that root, and inspection and production-consistency verification
    only within that task-created root. No rerun, resume, repair, other
    runs/outcome inspection, demand generation, persistent warming outside
    that root, product activation, Stage B, release mutation, deployment, or
    publication is approved.”
  - User-message date: `2026-07-31`
  - Sol recorder/date: `Sol High / 2026-07-31`
- Terminal handoff conditions:
  - After approval, hand off after the one execution and exact-root-only
    inspection, whether it passes, fails, is interrupted, or produces
    incomplete/corrupt evidence.
  - Stop on any approval/state/provenance mismatch, pre-existing exact root,
    architecture/artifact-contract change, material scope expansion, or three
    recorded serious failed approaches. Never repair or rerun evidence.
<!-- COMPLETED_TASK_LUNA_WARM_14_END -->

<!-- COMPLETED_TASK_LUNA_WARM_13_START -->
## COMPLETED_TASK — LUNA-WARM-13

### LUNA-WARM-13 — Wire bootstrap diagnostics and freeze v6

- Task ID: `LUNA-WARM-13`
- Revision: `2`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol approved the process-free v6 diagnostic freeze;
  non-executable`
- Delivery size: `STANDARD`
- Objective and scope: Repair the production warm-observation call so the
  current identity's structured attempt reaches `bootstrap_warm_state`. Add
  call-site-level regressions proving the real public path records the
  bootstrap's specific decline event before its terminal cold fallback,
  including absent controller, snapshot failure, and missing state file.
  Preserve every existing fail-closed rule and cold behavior. Freeze a
  canonical, source-complete, unapproved/unexecuted v6 manifest inheriting v5's
  exact physical case without reading its spent outcome. This task is strictly
  process-free: no SUMO, TraCI, sockets, archive or `runs/` access, campaign,
  warming, cache publication, product activation, Stage B, release, deployment,
  or publication.
- Completion outcome: the production call site cannot silently drop bootstrap
  diagnostics, and one reproducible v6 contract is ready for a separate
  execution/approval decision that will reveal the actual bootstrap decline
  cause if warming still falls back.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Edit `traffic_sim/simulation/monthly_sumo.py` only to pass the existing
    attempt through the production bootstrap call without changing cold or
    warm semantics.
  - Edit `tests/test_monthly_sumo.py` and
    `tests/test_monthly_warm_state.py` for call-site-level diagnostic
    regressions. Edit `tests/test_monthly_warm_state_v5_freeze.py` only for
    supersession/default assertions, and create
    `tests/test_monthly_warm_state_v6_freeze.py`.
  - Revision 2 may edit `tests/test_monthly_warm_state_freeze.py` only to move
    its shared `CURRENT` live-contract pointer from spent v5 to v6.
  - Create `tools/freeze_monthly_warm_state_v6.py` and
    `validation/monthly_warm_state_manifest_v6.json`. Edit
    `run_monthly_warm_state_validation.py` only for v6 live-contract/default
    selection and process-free validation.
  - Read the tracked v5 manifest/freeze tool and its fingerprint-bound tracked
    sources to inherit the same physical case and bind v6. Edit `TASKS.md` and
    `AGENT_NOTES.md` only for the terminal handoff.
- Forbidden work:
  - Do not open, stat, hash, enumerate, or inspect any `runs/` path, including
    the spent v5 root. No archive/executable/network preflight, SUMO, TraCI,
    socket, child simulator, campaign, outcome creation, or outcome inspection.
  - Do not edit v1–v5 manifests/tools or any preserved evidence, archived
    demand, network, unrelated source/tests, product/API/UI, policies,
    thresholds, architecture, improvement priorities, or active release.
  - No diagnostic weakening, rerun, resume, repair, cache publication,
    persistent warming, demand/horizon generation, product activation,
    Stage B, deployment, release, or publication.
- Acceptance criteria:
  1. `run_warm_observation` passes the exact current `attempt` object to
     `bootstrap_warm_state`; no new attempt is fabricated and `None` behavior
     remains compatible for callers outside an evidence campaign.
  2. At least one regression enters through the real public warm-observation
     path, not a direct `bootstrap_warm_state` call, and fails if the production
     call omits `attempt=attempt`. It proves the same object receives
     `cache_miss`, `bootstrap_started`, a specific bootstrap decline code, and
     terminal `bootstrap_failed`, followed by `cold_fallback`.
  3. Public-path tests cover `controller_absent`, `snapshot_failed`, and
     `state_file_missing`; ordering, exact identity, one finalized attempt, and
     bounded structured details remain valid. No specific cause may be replaced
     by only the generic terminal code.
  4. Existing eligibility, route-safety, identity, cache-hit, invoker,
     evidence-validation, unexpected-error, successful-warm, publication, and
     cold paths retain their current behavior. Focused tests remain
     process/socket/TraCI-free.
  5. Freeze v6 from exact recomputing v5 parent key
     `d16407b1dd41f9a5fd9d7ae558784c9c1ded9fd832dc75dd8eecf5bd1fb76432`,
     inheriting its schedule, seeds, warm points, route audits, archive/network
     identities, snapshot settings, comparison policy, and attempt schema
     without any `runs/` access. Bind every source that can interpret, emit,
     validate, freeze, or test the repaired diagnostic, including
     `tests/test_monthly_sumo.py` and
     `tests/test_monthly_warm_state_v6_freeze.py`.
  6. The v6 manifest is canonical, source-complete, reproducible
     byte-for-byte, `frozen_unapproved_unexecuted`, and selected by the harness
     default. Import/help/validation/freeze/tests create no campaign root or
     cache and start no simulator, socket, or TraCI connection.
  7. Cold commands/results, warm execution semantics, search behavior,
     product-default warming OFF, and v1–v5 frozen bytes remain unchanged.
     Self-audit every criterion and hand off once for Sol review.
  8. The shared live-contract suite points `CURRENT` at v6, so its approval
     refusal, non-executing validation, and real-schema checks exercise the
     current source fingerprints rather than the spent v5 contract.
  9. New v6 freeze-tool and v6-test prose accurately identifies v6 as
     unapproved/unexecuted, v5 as its tracked spent parent, and v6 as the
     artifact inheriting that parent. Remove stale copied claims that the
     current artifact is v4, the parent is v2/v3/v4, or v5 is the inheritor.
     Regenerate the canonical v6 manifest/key after every bound-byte correction.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_cache.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py
    tests/test_monthly_warm_state_v5_freeze.py
    tests/test_monthly_warm_state_v6_freeze.py`
  - process/socket/TraCI guard around that exact suite
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v6.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v6.json`
  - canonical v6/source/schema/v5-parent/physical-case verifier
  - immutable-byte checks for v1–v5 manifests and tools
  - `git diff --check -- traffic_sim/simulation/monthly_sumo.py
    run_monthly_warm_state_validation.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v5_freeze.py
    tests/test_monthly_warm_state_v6_freeze.py
    tools/freeze_monthly_warm_state_v6.py
    validation/monthly_warm_state_manifest_v6.json TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED` — this task is limited to process-free tracked
  code/tests/contracts and explicitly forbids `runs/`, archived-demand,
  executable, simulator, socket, outcome, warming, release, and publication
  access or side effects.
- Terminal handoff conditions:
  - Hand off after every acceptance criterion passes, or on an
    architecture/artifact-contract change, authority boundary, material scope
    expansion, or three recorded serious failed approaches.
  - Stop rather than inspect v5 evidence, execute v6, weaken attempt
    completeness, edit a prior frozen contract, or enable warming.
<!-- COMPLETED_TASK_LUNA_WARM_13_END -->

<!-- COMPLETED_TASK_LUNA_WARM_12_START -->
## COMPLETED_TASK — LUNA-WARM-12

### LUNA-WARM-12 — Execute diagnostic-complete v5 paired warm-state campaign once

- Task ID: `LUNA-WARM-12`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol approved the honest failed v5 campaign;
  non-executable`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, validate the canonical v5
  manifest, bound sources, warm-attempt schema, physical case, archived demand,
  network, SUMO executable, and absent exact output root. Execute the frozen
  three-identity cold-versus-warm campaign exactly once. Inspect only its
  task-created root and recompute canonical identity and production
  consistency, including structured attempt diagnostics, arm outcomes,
  semantic comparisons, failures, runtime, RSS, and validation-local cache
  material. Preserve an honest pass, fail, incomplete, or corrupt result
  without rerun, resume, repair, or evidence mutation. Do not activate product
  warming, Stage B, release, deployment, or publication.
- Completion outcome: one immutable, diagnostic-complete v5 campaign result
  establishes whether the three frozen monthly identities can execute from
  warm state with production-equivalent results and measurable resource use;
  product warming remains off pending a separate Sol adoption decision.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Read the tracked
    `validation/monthly_warm_state_manifest_v5.json`, its 14 fingerprint-bound
    tracked sources, the nine focused test files named below, and
    `sumo/net.net.xml`.
  - After exact approval is recorded, read only
    `runs/demand-20260721-222017-41bc682a-bbe1/demand_meta.json`,
    `manifest.json`, `calibrated.rou.xml`, `calibrated_v1.rou.xml`, and
    `calibrated_v2.rou.xml` for the frozen archive preflight.
  - After approval, run the exact SUMO/version/network/archive/root-absence
    preflight and the single frozen command below. Create only task-local
    temporary workspaces and
    `runs/monthly-warm-state-validation/d16407b1dd41f9a5fd9d7ae558784c9c1ded9fd832dc75dd8eecf5bd1fb76432`,
    including validation-only cache material inside that root.
  - Inspect, enumerate, stat, hash, and parse only that task-created exact root
    after execution. Edit `TASKS.md` and `AGENT_NOTES.md` only for the terminal
    handoff.
- Forbidden work:
  - While approval is unsatisfied: no `runs/` access or root-existence check,
    archive or executable/network preflight, SUMO, TraCI, socket, campaign,
    outcome creation, or outcome inspection.
  - No rerun, resume, repair, partial reuse, alternate key/root, or inspection
    of any other `runs/`, report, campaign, cache, or outcome path.
  - Do not generate demand, warm horizons, persist warming outside the exact
    task root, or mutate source, tests, manifests, tools, network, archived
    demand, product/API/UI, policies, thresholds, or active release.
  - No cache adoption, product activation, Stage B, deployment, release, or
    publication.
- Acceptance criteria:
  1. Before any approved side effect, rerun the nine-file process-free suite,
     v5 byte-for-byte freeze verification, non-executing harness validation,
     and process/socket/TraCI guard. All pass without `runs/` access.
  2. After approval, preflight proves the canonical manifest key is exactly
     `d16407b1dd41f9a5fd9d7ae558784c9c1ded9fd832dc75dd8eecf5bd1fb76432`;
     all 14 source fingerprints, schemas, physical-case fields, three
     identities, schedule, warm points, snapshot settings, tracked network,
     exact SUMO executable/version, demand build `2ac04275daabe93c`, 480
     intervals, and five archive-file hashes match; the exact output root does
     not exist.
  3. Execute exactly once:
     `PYTHONDONTWRITEBYTECODE=1 python3
     run_monthly_warm_state_validation.py --manifest
     validation/monthly_warm_state_manifest_v5.json --execute
     --approval-token
     d16407b1dd41f9a5fd9d7ae558784c9c1ded9fd832dc75dd8eecf5bd1fb76432`.
     A nonzero exit or interruption is preserved as evidence and is not
     retried, resumed, repaired, or replaced.
  4. Root-only inspection recomputes the record's canonical content key and
     manifest binding; proves exact 3/3 identity and arm coverage, exactly
     three valid ordered warm-attempt records, consistent terminal outcomes,
     complete execution evidence, and no unreported fallback; and verifies
     production-equivalence fields and every semantic comparison.
  5. A pass requires actual warm execution for all three identities, zero
     semantic mismatches and observation failures, correct boundary/attempt
     diagnostics, and exactly the expected task-local cache entries. Any other
     result remains fail/incomplete/corrupt with its readable reason and no
     publishable cache claim.
  6. Report wall time and peak RSS honestly. Make no speed or readiness claim
     unless the record passes. Product-default warming remains off and no
     adoption or release artifact is created.
  7. Self-audit every criterion and hand off once for Sol review without
     changing the frozen contract or evidence.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_cache.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py
    tests/test_monthly_warm_state_v5_freeze.py`
  - process/socket/TraCI guard around that exact suite
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v5.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v5.json`
  - after approval, canonical manifest/source/schema/SUMO/network/archive/
    demand/identity/root-absence preflight
  - the exact single execution command in acceptance criterion 3
  - exact-root-only enumeration, canonical-record recomputation,
    warm-attempt/arm/semantic/cache consistency verifier
  - `git diff --check -- TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `REQUIRED — SATISFIED`. Exact immutable scope is the one-time
  non-resumable `monthly_warm_state_v5` paired cold-versus-warm SUMO/TraCI
  campaign, the named focused process-free checks, canonical manifest/source/
  schema and warm-attempt-contract checks, exact SUMO executable/network/
  archived-demand preflight, one frozen execution, task-created temporary
  workspaces and validation-only cache material inside its task root, and
  inspection and production-consistency verification only within that
  task-created root at content key
  `d16407b1dd41f9a5fd9d7ae558784c9c1ded9fd832dc75dd8eecf5bd1fb76432`,
  and artifact root
  `runs/monthly-warm-state-validation/d16407b1dd41f9a5fd9d7ae558784c9c1ded9fd832dc75dd8eecf5bd1fb76432`.
  Exact quoted user message dated `2026-07-30`:
  > I explicitly approve LUNA-WARM-12 revision 1 to run the one-time
  > non-resumable monthly_warm_state_v5 paired cold-versus-warm SUMO/TraCI
  > campaign at content key
  > d16407b1dd41f9a5fd9d7ae558784c9c1ded9fd832dc75dd8eecf5bd1fb76432 and
  > artifact root runs/monthly-warm-state-validation/
  > d16407b1dd41f9a5fd9d7ae558784c9c1ded9fd832dc75dd8eecf5bd1fb76432,
  > including the named focused process-free checks, canonical
  > manifest/source/schema and warm-attempt-contract checks, exact SUMO
  > executable/network/archived-demand preflight, one frozen execution,
  > task-created temporary workspaces and validation-only cache material inside
  > that root, and inspection and production-consistency verification only
  > within that task-created root. No rerun, resume, repair, other runs/outcome
  > inspection, demand generation, persistent warming outside that root,
  > product activation, Stage B, release mutation, deployment, or publication
  > is approved.
  Sol recorder/date: `Sol High / 2026-07-30`. This approval does not authorize
  any excluded work and may not be reused for another task, revision, key, or
  root.
- Terminal handoff conditions:
  - After approval, hand off after the one execution and exact-root-only
    inspection, whether it passes, fails, is interrupted, or produces
    incomplete/corrupt evidence.
  - Stop on any approval/state/provenance mismatch, pre-existing exact root,
    architecture/artifact-contract change, material scope expansion, or three
    recorded serious failed approaches. Never repair or rerun evidence.
<!-- COMPLETED_TASK_LUNA_WARM_12_END -->

<!-- COMPLETED_TASK_LUNA_WARM_11_START -->
## COMPLETED_TASK — LUNA-WARM-11

### LUNA-WARM-11 — Persist warm-attempt diagnostics and freeze v5

- Task ID: `LUNA-WARM-11`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved the process-free v5 freeze;
  non-executable`
- Delivery size: `STANDARD`
- Objective and scope: Replace the runner's unstructured, unconsumed warm
  reason tuples with one canonical structured attempt record per requested
  warm identity. Preserve ordered diagnostic events and a terminal outcome,
  including successful warming and every cold-fallback path. Bind those records
  into the equivalence artifact and require exact attempt coverage before a
  campaign can pass or publish cache material. Add exhaustive fake-driven,
  process-free tests, then freeze an unapproved/unexecuted v5 manifest inheriting
  the v4 physical case without reading its spent outcome. Do not diagnose v4 by
  guess, execute SUMO/TraCI, inspect `runs/`, or enable product warming.
- Completion outcome: a process-free reviewed v5 contract is ready for a
  separate execution decision; any future failed warm arm will say which
  identity was attempted, what happened, and why it fell back.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Edit `traffic_sim/simulation/monthly_sumo.py` and
    `run_monthly_warm_state_validation.py` only for structured warm-attempt
    capture, validation, and immutable-record persistence.
  - Edit focused tests only:
    `tests/test_monthly_sumo.py`,
    `tests/test_monthly_warm_state.py`,
    `tests/test_monthly_warm_state_freeze.py`,
    `tests/test_monthly_warm_state_v2_freeze.py`,
    `tests/test_monthly_warm_state_v3_freeze.py`,
    `tests/test_monthly_warm_state_v4_freeze.py`, and new
    `tests/test_monthly_warm_state_v5_freeze.py`.
  - Create `tools/freeze_monthly_warm_state_v5.py` and
    `validation/monthly_warm_state_manifest_v5.json`.
  - Read only the tracked v4 manifest/freeze tool and its bound tracked sources
    to inherit and fingerprint the same physical case. Edit `TASKS.md` and
    `AGENT_NOTES.md` only for the terminal handoff.
- Forbidden work:
  - No SUMO, TraCI, sockets, executable/network/archive preflight, campaign,
    rerun, resume, repair, or outcome creation.
  - Do not open, stat, hash, enumerate, or inspect any `runs/` path, including
    the spent LUNA-WARM-10 root. Do not infer its missing decline cause.
  - Do not edit v1–v4 manifests/tools, archived demand, network, unrelated
    source/tests, product/API/UI, active release, architecture, policy, or
    thresholds.
  - No cache publication, persistent warming, product activation, Stage B,
    deployment, release, or publication.
- Acceptance criteria:
  1. Define one versioned, JSON-safe warm-attempt schema. Each attempt carries
     exact `schedule_id`, `demand_variant`, and integer `seed`; one terminal
     outcome (`warm_executed` or `cold_fallback`); and ordered structured events
     with stable reason codes. Optional details are bounded and diagnostic only,
     never parsed as authority.
  2. Exactly one attempt is finalized for every warm-enabled
     `_run_observation` call, including eligibility, route audit/safety,
     identity, cache lookup/bootstrap, absent controller or state file,
     snapshot, invoker, split-evidence, unexpected-error, and successful-warm
     paths. Informational cache-miss/bootstrap events are distinguishable from
     the terminal outcome; no fallback may be unreported.
  3. The validation harness snapshots only the current campaign's attempts,
     writes them into `execution_evidence`, and validates their schema and exact
     identity set. Missing, duplicate, unexpected, malformed, or contradictory
     attempts; a cold arm without a terminal decline; or a warm arm without a
     matching successful attempt makes the record fail and forbids publication.
  4. Attempt count is the number of finalized identity attempts, not the number
     of events/reasons. The frozen three-identity case requires exactly three
     attempts. Record canonicalization includes the diagnostics, and record
     content-key recomputation rejects any post-write change.
  5. Fake-driven tests exercise every exit family, multi-event bootstrap
     success/failure, duplicate/missing identity evidence, mismatched arm
     outcomes, malformed reason data, unexpected exceptions, successful warm
     execution, and fail-closed cache publication. A guard proves the focused
     suite starts no SUMO/TraCI process and opens no socket.
  6. Freeze v5 from the exact recomputing v4 parent key
     `d7db25c61b953c123ccb7594e01afaff042d6e1ffdce26190c297bdeb40bbf85`,
     inheriting its physical case, schedule, seeds, route audits, warm points,
     archive/network identities, and snapshot settings without any `runs/`
     access. Bind the attempt schema and every interpreting source.
  7. The v5 manifest is canonical, source-complete, reproducible byte-for-byte,
     and `frozen_unapproved_unexecuted`. Import/help/validation/freeze/tests
     remain process-free and create no campaign root or cache.
  8. Cold production semantics, search early-stop behavior, observation
     semantics, product-default warming OFF, and v1–v4 frozen bytes remain
     unchanged. Self-audit every criterion and hand off once for Sol review.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_cache.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py
    tests/test_monthly_warm_state_v5_freeze.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v5.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v5.json`
  - process/socket/TraCI guard around the focused suite
  - canonical manifest/source/schema and v4-parent identity verifier
  - `git diff --check -- traffic_sim/simulation/monthly_sumo.py
    run_monthly_warm_state_validation.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state.py tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py
    tests/test_monthly_warm_state_v5_freeze.py
    tools/freeze_monthly_warm_state_v5.py
    validation/monthly_warm_state_manifest_v5.json TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED` — this task is process-free, reads no `runs/`
  path, executes no simulator, and creates only tracked source/test/contract
  files.
- Terminal handoff conditions:
  - Hand off after all acceptance criteria pass, or on an architecture/artifact
    contract change, authority boundary, material scope expansion, or three
    recorded serious failed approaches.
  - Stop rather than inspect old evidence, execute a campaign, weaken attempt
    completeness, edit a prior frozen contract, or enable warming.
<!-- COMPLETED_TASK_LUNA_WARM_11_END -->

<!-- COMPLETED_TASK_LUNA_WARM_10_START -->
## COMPLETED_TASK — LUNA-WARM-10

### LUNA-WARM-10 — Execute the final v4 paired warm-state campaign once

- Task ID: `LUNA-WARM-10`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved the honest failed campaign;
  non-executable`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, validate the immutable v4
  manifest, live source/schema bindings, focused process-free suite, SUMO
  executable, tracked network, exact archived demand, and absence of the exact
  task root. Execute the frozen cold-versus-warm campaign exactly once for its
  three identities. Inspect only the task-created root and independently verify
  its canonical record, coverage, arm identity, semantic comparisons,
  performance, and validation-only cache publication or non-publication.
  Preserve an honest pass, fail, incomplete, or corrupt result without rerun,
  resume, repair, product activation, or release.
- Completion outcome: one immutable evidence package decides whether v4 warm
  execution exactly matches cold production observations for q10/q50/q90 and
  reports measured runtime; any task-local cache material exists only after a
  complete exact pass.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Read-only tracked inputs:
    `validation/monthly_warm_state_manifest_v4.json`, its 14 bound source
    files, the eight focused test files, and `sumo/net.net.xml`.
  - Read-only approved archive:
    `runs/demand-20260721-222017-41bc682a-bbe1/demand_meta.json`,
    `manifest.json`, `calibrated.rou.xml`, `calibrated_v1.rou.xml`, and
    `calibrated_v2.rou.xml`.
  - Execute the resolved SUMO binary for version preflight and the single
    frozen SUMO/TraCI campaign only.
  - Create only
    `runs/monthly-warm-state-validation/d7db25c61b953c123ccb7594e01afaff042d6e1ffdce26190c297bdeb40bbf85`
    plus task-created temporary workspaces removed on normal completion.
    Validation-only cache material published by the frozen harness must remain
    inside that root.
  - Edit only `TASKS.md` and `AGENT_NOTES.md` for the terminal handoff.
- Forbidden work:
  - No preflight, executable invocation, archive read, root existence check,
    campaign execution, or outcome inspection before exact approval is
    recorded for this task/revision/key/root.
  - No rerun, resume, repair, partial-root reuse, evidence mutation, alternate
    key/root, other `runs/` or outcome/report inspection, demand generation,
    or persistent warming outside the task-created validation root.
  - No source, test, manifest, network, archive, policy, threshold, product,
    API/UI, active-release, Stage B, deployment, release, or publication
    mutation.
- Acceptance criteria:
  1. Before root creation, the manifest recomputes to
     `d7db25c61b953c123ccb7594e01afaff042d6e1ffdce26190c297bdeb40bbf85`;
     all bound sources and schemas match; the focused process-free suite passes.
  2. Preflight verifies the exact executable/version, tracked-network hash,
     archive path/build key/interval count, all five recorded archive hashes,
     three frozen seeds/variants, one schedule identity, snapshot settings, and
     exact artifact-root absence. Any failure stops before execution.
  3. Run exactly once:
     `PYTHONDONTWRITEBYTECODE=1 python3
     run_monthly_warm_state_validation.py --manifest
     validation/monthly_warm_state_manifest_v4.json --execute
     --approval-token
     d7db25c61b953c123ccb7594e01afaff042d6e1ffdce26190c297bdeb40bbf85`.
     A nonzero honest campaign result is evidence, not permission to retry.
  4. Inspect only the exact task-created root. Recompute the equivalence
     record's canonical key and verify manifest identity, expected/completed
     identity sets, three comparisons, cold/warm arm labels, warm points,
     split diagnostics, exact semantic equality or explicit mismatches,
     observation failures, runtime/RSS, and cache publication consistency.
  5. Pass requires complete coverage, actual cold and warm arms, zero semantic
     mismatches/failures, and exactly the expected task-local cache entries.
     Any other result is preserved and reported honestly without adoption.
  6. Warming remains product-default OFF. No equivalence, speedup, adoption,
     Stage B, release, or publication claim is made beyond this frozen case.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
    tests/test_warm_state_cache.py tests/test_warm_state_boundary.py
    tests/test_monthly_warm_state.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py
    tests/test_monthly_warm_state_v3_freeze.py
    tests/test_monthly_warm_state_v4_freeze.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v4.py --verify`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v4.json`
  - Approved read-only executable/network/archive/root-absence preflight
    implementing acceptance criteria 1–2.
  - The exact one-time execution command in acceptance criterion 3.
  - Approved inspection-only canonical/evaluator checks confined to the exact
    task-created root, followed by scoped `git diff --check` and `git status`.
- Approval gate: `REQUIRED — SATISFIED`. Exact immutable scope is the one-time
  non-resumable v4 SUMO/TraCI campaign, its named focused checks and canonical
  checks, exact executable/network/archive preflight, task-created temporary
  workspaces and validation-only cache material inside the task root, one
  frozen execution, and inspection/production-consistency verification only of
  its own outcome at content key
  `d7db25c61b953c123ccb7594e01afaff042d6e1ffdce26190c297bdeb40bbf85`
  and root
  `runs/monthly-warm-state-validation/d7db25c61b953c123ccb7594e01afaff042d6e1ffdce26190c297bdeb40bbf85`.
  Exact quoted user message dated `2026-07-30`:
  > I explicitly approve LUNA-WARM-10 revision 1 to run the one-time
  > non-resumable monthly_warm_state_v4 paired cold-versus-warm SUMO/TraCI
  > campaign at content key
  > d7db25c61b953c123ccb7594e01afaff042d6e1ffdce26190c297bdeb40bbf85 and
  > artifact root runs/monthly-warm-state-validation/
  > d7db25c61b953c123ccb7594e01afaff042d6e1ffdce26190c297bdeb40bbf85,
  > including the named focused process-free checks, canonical
  > manifest/source/schema checks, exact SUMO executable/network/archived-demand
  > preflight, one frozen execution, task-created temporary workspaces and
  > validation-only cache material inside that root, and inspection and
  > production-consistency verification only within that task-created root. No
  > rerun, resume, repair, other runs/outcome inspection, demand generation,
  > persistent warming outside that root, product activation, Stage B, release
  > mutation, deployment, or publication is approved.
  Sol recorder/date: `Sol High / 2026-07-30`.
- Terminal handoff conditions:
  - Hand off after the one execution and exact-root inspection, whether pass,
    fail, incomplete, or corrupt; or stop before execution on any approval,
    manifest/source/schema, process-free test, executable, network, archive,
    identity, or root-absence preflight failure.
  - Stop on any need to rerun/resume/repair, inspect other evidence, mutate
    source/product/release state, or expand the approved scope.
<!-- COMPLETED_TASK_LUNA_WARM_10_END -->

## 2026-07-29 — LUNA-WARM-07 revision 1 concluded task

<!-- COMPLETED_TASK_LUNA_WARM_07_START -->
## ACTIVE_TASK

### LUNA-WARM-07 — Execute the decisive route-safe v2 paired campaign once

- Task ID: `LUNA-WARM-07`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — honest campaign fail accepted; unauthorized external
  cache existence check disclosed; no equivalence, cache, or speedup claim`
- Delivery size: `EXTENDED`
- Objective and scope: After exact approval, validate the immutable v2
  warm-state contract, its source/archive/network bindings, focused process-free
  tests, SUMO executable, and absence of its exact outcome root. Execute the
  frozen three-identity cold-versus-warm campaign exactly once. Inspect only
  that task-created root and independently verify its canonical record,
  coverage, execution evidence, semantic comparisons, performance, and cache
  publication or non-publication. Preserve an honest pass, fail, incomplete, or
  corrupt result without rerun, resume, repair, product activation, or release.
- Completion outcome: one content-key-bound decisive evidence package records
  whether route-safe warm execution exactly matches cold production
  observations for q10/q50/q90 and reports measured runtime; cache material
  exists only after a complete exact pass.
- Internal checkpoints:
  1. Approval, immutable contract, focused tests, SUMO/network/archive
     preflight, and exact-root absence all pass.
  2. The frozen command executes once over the three exact identities; a
     nonzero result, interruption, or exception is preserved without retry.
  3. Exact-root-only inspection independently classifies pass, fail,
     incomplete, or corrupt and verifies cache publication consistency.
- Allowed files and resources:
  - Read-only: `validation/monthly_warm_state_manifest_v2.json`,
    `tools/freeze_monthly_warm_state_v2.py`,
    `run_monthly_warm_state_validation.py`, the manifest's 13 exact
    `source_fingerprints` paths, the six named focused test files,
    `sumo/net.net.xml`, and the resolved SUMO executable/version.
  - Read-only after approval, exactly:
    - `runs/demand-20260721-222017-41bc682a-bbe1/demand_meta.json`
    - `runs/demand-20260721-222017-41bc682a-bbe1/manifest.json`
    - `runs/demand-20260721-222017-41bc682a-bbe1/calibrated.rou.xml`
    - `runs/demand-20260721-222017-41bc682a-bbe1/calibrated_v1.rou.xml`
    - `runs/demand-20260721-222017-41bc682a-bbe1/calibrated_v2.rou.xml`
  - Create and inspect only after approval:
    `runs/monthly-warm-state-validation/c2c904655d59c48374d81fe4f9fe42540b2fb05e229faaae28a17030ffbd64e6`
    and task-created temporary workspaces/destinations.
  - Edit only `TASKS.md` and `AGENT_NOTES.md` for the terminal handoff.
- Forbidden work:
  - Before exact approval: no executable/archive/network preflight, exact-root
    existence check, SUMO/TraCI invocation, temporary campaign workspace,
    outcome creation, or outcome inspection.
  - Never inspect, enumerate, stat, hash, parse, mutate, or delete another
    `runs/` path, campaign root, report, outcome, or cache.
  - Do not edit the v2 manifest, bound sources, tests, demand, network, policy,
    thresholds, product/API/UI, active release, architecture, or priorities.
  - Do not rerun or resume after any campaign start; do not repair, normalize,
    synthesize, or delete evidence. A pre-existing exact root is a terminal
    blocker and is not inspected.
  - Do not generate or warm demand/horizons, activate product warming or Stage
    B, alter a release, deploy, push, release, or publish.
- Acceptance criteria:
  1. Luna acts only after Sol records the exact approval message, key, root,
     scope, dates, task ID, and revision and transitions this task to
     `READY_FOR_LUNA`.
  2. Before SUMO, require canonical key
     `c2c904655d59c48374d81fe4f9fe42540b2fb05e229faaae28a17030ffbd64e6`,
     status `frozen_unapproved_unexecuted`, exact q10/q50/q90 identity set,
     schedule, seeds, safe points, schemas, comparison policy, 13 live source
     fingerprints, five archive hashes, and network hash.
  3. Run all named focused process-free tests and the byte-reproducible freeze
     verifier. Any drift or failure is terminal; do not repair it here.
  4. Under approval, require the exact root absent without inspecting any
     existing content, resolve the required SUMO executable/version, and
     validate the exact network and archived demand. Any mismatch is terminal.
  5. Execute exactly once:
     `python3 run_monthly_warm_state_validation.py --manifest
     validation/monthly_warm_state_manifest_v2.json --execute
     --approval-token
     c2c904655d59c48374d81fe4f9fe42540b2fb05e229faaae28a17030ffbd64e6`.
     A nonzero exit is an evidence result, not permission to rerun.
  6. Inspect only the task-created exact root. Snapshot and hash every member;
     parse and recompute the equivalence record's canonical key and require the
     exact manifest key, three distinct expected identities, complete coverage,
     bounded split diagnostics, execution labels/points, semantic comparisons,
     observation failures, and performance fields to be internally consistent.
  7. A `pass` requires exactly three equivalent comparisons, zero mismatches,
     complete execution evidence, exact cold/warm labels and points, and exactly
     three distinct published cache entries. Validate every published entry's
     manifest/member digests and production restore path into task temp.
  8. A fail/incomplete/corrupt result must publish no usable cache material.
     Require the marker, record, root contents, and publication fields to agree;
     never convert or repair the disposition.
  9. Re-snapshot the exact root and require byte identity after inspection.
     Record bounded equivalence, identity, failure, runtime, cache, and integrity
     evidence; transition once to `READY_FOR_SOL_REVIEW`.
- Focused checks:
  - `PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/gs-mpl python3 -m pytest -q
    tests/test_scenario.py tests/test_warm_state_cache.py
    tests/test_monthly_sumo.py tests/test_monthly_warm_state.py
    tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    tools/freeze_monthly_warm_state_v2.py --verify`
  - `python3 -m json.tool
    validation/monthly_warm_state_manifest_v2.json`
  - canonical manifest/source/archive/network/identity recomputation
  - exact approved executable/archive/network/root-absence preflight
  - the exact one-time campaign command in acceptance criterion 5
  - exact-root-only path/SHA-256 snapshots, record recomputation, and cache
    manifest/member/restore verification
  - `git diff --check -- TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `REQUIRED`
  - Approval status: `CONSUMED ONCE`; it cannot authorize any further
    execution, inspection, rerun, resume, or repair.
  - Exact scope/key/root: the one-time non-resumable
    `monthly_warm_state_v2` paired SUMO campaign, named frozen/process-free
    checks, required executable/network/archive preflight, temporary warm-state
    bootstrap, exact execution, and inspection/verification only of content key
    `c2c904655d59c48374d81fe4f9fe42540b2fb05e229faaae28a17030ffbd64e6`
    at artifact root
    `runs/monthly-warm-state-validation/c2c904655d59c48374d81fe4f9fe42540b2fb05e229faaae28a17030ffbd64e6`.
  - Exact user message (recorded verbatim; presentation line-wrap normalized
    only):
    > I explicitly approve LUNA-WARM-07 revision 1 to run the one-time
    > non-resumable monthly_warm_state_v2 paired cold-versus-warm SUMO campaign
    > at content key
    > c2c904655d59c48374d81fe4f9fe42540b2fb05e229faaae28a17030ffbd64e6
    > and artifact root
    > runs/monthly-warm-state-validation/c2c904655d59c48374d81fe4f9fe42540b2fb05e229faaae28a17030ffbd64e6,
    > including canonical manifest/source/archive/network/SUMO preflight, the
    > named process-free checks, one frozen execution, its task-created
    > temporary workspaces and warm-state bootstrap, and inspection and
    > production consistency verification only within that task-created root.
    > No rerun, resume, repair, other run/outcome inspection, demand or horizon
    > generation/warming, product activation, Stage B, release mutation,
    > deployment, or publication is approved.
  - User-message date: `2026-07-29`
  - Sol recorder/date: `Sol High / 2026-07-29`
- Terminal handoff conditions:
  - Remain `BLOCKED` and perform no preflight or execution until the exact
    approval above is received and recorded by Sol.
  - After approval, Luna completes all checkpoints and hands off once with the
    honest pass/fail/incomplete/corrupt disposition, or stops at an exact
    approval, pre-existing-root, drift, executable, archive, network,
    provenance, integrity, or interruption boundary. No second execution.
<!-- COMPLETED_TASK_LUNA_WARM_07_END -->

## 2026-07-29 — LUNA-WORKFLOW-02 revision 1 completed task

<!-- LUNA_WORKFLOW_02_REV1_ACTIVE_TASK_HISTORY_START -->
## ACTIVE_TASK

### LUNA-WORKFLOW-02 — Make Codex/Sol and Claude/Luna handoffs state-routed

- Task ID: `LUNA-WORKFLOW-02`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — APPROVED by Sol review; non-executable`
- Delivery size: `STANDARD`
- Objective and scope: Improve the Markdown coordination protocol for the
  user's real two-tool workflow: Codex acts as Sol and Claude acts as Luna by
  default. Add a state-routed `CONTINUE` command so the user need not name the
  role or command, bias planning toward larger cohesive delivery slices, and
  allow Sol to combine an approved review with planning the next safe,
  repository-discoverable task. Preserve explicit actor boundaries, current
  marker validation, exact approvals, and every execution, outcome, release,
  provenance, validation, and publication gate. Change only the three workflow
  Markdown files; do not automate invocation of either external tool.
- Completion outcome: `AGENTS.md` supports a materially faster external-Claude
  loop with unambiguous default roles, one neutral continuation command,
  bounded larger tasks, and safe review-plus-plan chaining.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - `AGENTS.md`
  - `TASKS.md`
  - `AGENT_NOTES.md`
- Forbidden work:
  - Do not edit code, tests, architecture, improvement priorities, validation
    artifacts, run artifacts, or historical workflow evidence.
  - Do not invoke Claude, SUMO, TraCI, servers, scenarios, campaigns, demand or
    horizon generation/warming, outcome access, deployment, release, or
    publication.
  - Do not weaken approval matching, authority separation, fail-closed marker
    validation, revision binding, or any product/evidence safety gate.
  - Do not make `CONTINUE` infer approval, cross `BLOCKED`, silently broaden a
    task, or let Claude perform Sol work or Codex perform Luna work by default.
- Acceptance criteria:
  1. Define runtime defaults: Codex is `Sol High`; Claude/Claude Code is `Luna
     High`. An explicit user role assignment may override a default only when
     the requested action is legal for the current state.
  2. Define `CONTINUE` as a state-routed command. Each actor validates the fast
     path, executes only the legal current action assigned to its role, and
     fails closed with the exact expected actor/action when opened in the wrong
     tool or in `BLOCKED`/conflicting state.
  3. Preserve the explicit `SOL PLAN`, `LUNA DO`, `LUNA FIX`, and `SOL REVIEW`
     aliases as stepwise controls. `CONTINUE` changes routing convenience, not
     permissions or transition semantics.
  4. Add a larger-slice planning bias: use `EXTENDED` for one cohesive outcome
     spanning multiple tightly coupled checkpoints, while forbidding unrelated
     batching. Luna must self-audit every acceptance criterion and repair
     in-scope defects before the terminal handoff.
  5. Add `SOL REVIEW+PLAN`: after an approved review, Sol may atomically archive
     the completed task and plan the next repository-discoverable priority in
     the same turn only when no user choice, new approval, scope expansion, or
     authority boundary is needed. Otherwise it ends at `READY_FOR_SOL_PLAN` or
     `BLOCKED` exactly as today.
  6. Make every terminal handoff state the next actor and include one canonical
     pasteable instruction: `CONTINUE using AGENTS.md`. Do not embed or require
     growing history as startup context.
  7. Keep the protocol compact and internally consistent; all three current
     marker pairs remain unique and the task ID, revision, state, owner, next
     action, transition, and approval fields agree.
- Focused checks:
  - read-only marker-count assertions for `WORKFLOW_CONTROL`, `ACTIVE_TASK`, and
    `CURRENT_HANDOFF`
  - targeted state/role/command consistency review for every legal state
  - targeted searches proving `CONTINUE`, default actor assignment,
    `SOL REVIEW+PLAN`, larger-slice bias, and all stop boundaries are explicit
  - `git diff --check -- AGENTS.md TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED`; documentation-only protocol work. This task
  grants no product, execution, outcome, artifact, release, or publication
  authority.
- Terminal handoff conditions:
  - Luna completes the full documentation slice, self-audits all seven criteria,
    runs the focused read-only checks, updates the current handoff, atomically
    transitions to `READY_FOR_SOL_REVIEW`, and stops.
  - Stop blocked for any marker/state conflict, need to modify a fourth file or
    historical evidence, uncertain preservation of a safety/approval gate, or
    material expansion beyond this external-Claude workflow contract.
<!-- LUNA_WORKFLOW_02_REV1_ACTIVE_TASK_HISTORY_END -->

<!-- COMPLETED_TASK_LUNA_WARM_06_START -->
## COMPLETED_TASK — LUNA-WARM-06

### LUNA-WARM-06 — Make the warm split route-safe and freeze decisive v2 evidence

- Task ID: `LUNA-WARM-06`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — APPROVED by Sol review; non-executable`
- Delivery size: `EXTENDED`
- Objective and scope: After exact read-only approval, correct the three
  mechanisms exposed by LUNA-WARM-05: candidate-filtered routes must be
  identical through the selected snapshot, restored SUMO statistics must not
  double-count prefix counters, and post-warm closure throughput must be
  measured. Make the validation harness execute all frozen identities despite
  production hard failures and retain bounded split diagnostics. Preserve cold
  production defaults and exact semantic equality. Use only process-free
  tests and the five named archived-demand inputs to freeze an immutable,
  unapproved/unexecuted v2 campaign. Do not run SUMO or inspect any campaign
  outcome.
- Completion outcome: one reviewed route-safe warm implementation and
  byte-reproducible v2 manifest ready for a separately approved decisive
  three-identity SUMO campaign.
- Internal checkpoints:
  1. Route-safe split selection, cumulative-counter reconstruction and closure
     throughput measurement pass focused unit/command tests.
  2. Validation-only all-identity orchestration and bounded split diagnostics
     pass complete/mismatch/publication-rollback tests without changing
     production search fail-fast behavior.
  3. Exact archived routes are read once to freeze route hashes, mutation
     audits and per-variant safe warm points; the full process-free gate passes.
- Allowed files:
  - `traffic_sim/simulation/monthly_warm_state.py`
  - `traffic_sim/simulation/monthly_sumo.py`
  - `traffic_sim/simulation/metrics.py`
  - `traffic_sim/simulation/warm_state_cache.py`
  - `run_scenario.py`
  - `run_monthly_warm_state_validation.py`
  - `tools/freeze_monthly_warm_state_v2.py`
  - `validation/monthly_warm_state_manifest_v2.json`
  - `tests/test_scenario.py`
  - `tests/test_monthly_warm_state.py`
  - `tests/test_monthly_sumo.py`
  - `tests/test_warm_state_cache.py`
  - `tests/test_monthly_warm_state_freeze.py`
  - `tests/test_monthly_warm_state_v2_freeze.py`
  - `ARCHITECTURE.md`
  - `IMPROVEMENT_PLAN.md`
  - `TASKS.md`
  - `AGENT_NOTES.md`
  - read-only after exact approval:
    - `runs/demand-20260721-222017-41bc682a-bbe1/demand_meta.json`
    - `runs/demand-20260721-222017-41bc682a-bbe1/manifest.json`
    - `runs/demand-20260721-222017-41bc682a-bbe1/calibrated.rou.xml`
    - `runs/demand-20260721-222017-41bc682a-bbe1/calibrated_v1.rou.xml`
    - `runs/demand-20260721-222017-41bc682a-bbe1/calibrated_v2.rou.xml`
- Forbidden work:
  - While blocked, do not open, stat, hash or test existence of any named
    archived-demand file.
  - Do not inspect, enumerate, stat, hash, parse, mutate or delete the spent
    LUNA-WARM-05 root or any other `runs/` path beyond the five exact read-only
    files after approval.
  - Do not run SUMO, TraCI, executable/network preflight, a campaign, demand or
    horizon generation/warming, or create any outcome root.
  - Do not edit the spent v1 manifest/evidence, persistent-SUMO contract, proxy
    policy, thresholds or active release; do not weaken exact equality,
    provenance, closure-integrity, health, recovery, recall, regret,
    failure-recall, release or publication gates.
  - Do not activate warming in product/API/search, adopt Stage B, deploy, push,
    release or publish.
- Acceptance criteria:
  1. Candidate filtering occurs before state lookup/bootstrap. Compare the
     original and filtered route by vehicle ID, departure and route; choose an
     aligned snapshot strictly before the earliest departed vehicle that is
     changed or removed. If no positive snapshot satisfies the frozen minimum
     prefix and closure bound, warming fails closed to the unchanged cold path.
  2. The selected route-safe warm point, original/filtered route digests,
     changed/dropped vehicle counts and earliest affected departure are bound
     to the warm-state identity and recorded as validation execution evidence.
     A stale point, route or audit is a cache miss, never repaired.
  3. Add an explicit restored-statistics rule: `loaded`, `inserted`,
     `teleport_total` and per-reason teleport counts are cumulative in SUMO's
     loaded state, so reconstruction takes the validated post-state cumulative
     values rather than summing prefix plus post. Reject post values below
     their prefix lower bounds or internally impossible count relationships.
     Completed-trip aggregates remain disjoint and additive.
  4. The post-warm invoker parses its measured edgeData with closed edges
     zero-filled, computes active-closure throughput over fully contained
     buckets with the shared production function, and passes a measured integer
     into metrics. A closure with an unmeasured post domain fails closed;
     measured zero remains distinct from missing.
  5. Refactor validation-only orchestration to request every frozen
     `(schedule, variant, seed)` directly from the production observation path,
     so a q10 hard failure does not suppress q50/q90 evidence. Ordinary
     `run_candidate` retains its fail-fast ordering and behavior unchanged.
  6. Each comparison records bounded execution-only split diagnostics:
     route-safety audit, selected warm point, prefix completed aggregates and
     counters, raw post metrics and reconstructed metrics. Diagnostics are
     content-keyed evidence but excluded from semantic equality; exact
     production-observation equality remains the only passing rule.
  7. Publication remains campaign-atomic and impossible unless all three
     frozen identities have real cold/warm labels, their exact selected points,
     complete coverage, zero semantic mismatches and one distinct restorable
     cache entry each. Any mismatch publishes none.
  8. Add process-free regressions reproducing LUNA-WARM-05's +1081/+1065
     double-count shape, missing-versus-zero throughput, a changed vehicle
     departing before the old 24300 point, the 7.73-second mismatch diagnostic,
     and q10 hard failure with q50/q90 still compared. Prove invalid audits,
     non-monotone counters and route drift fail closed.
  9. Cold construction, commands, route filtering, candidate results, caches,
     production fail-fast orchestration and canonical payloads remain
     byte/structurally unchanged. Warm execution remains validation-only and
     default-off.
  10. Create, do not overwrite, `monthly_warm_state_manifest_v2.json`. Bind the
      five approved archive-file hashes, exact mutation audit and safe point
      for each q10/q50/q90 route, same case/schedule/seeds/network/demand, all
      interpreting source fingerprints, closed comparison policy and v2
      schema. Status is `frozen_unapproved_unexecuted`; no approval is stored.
  11. Update targeted architecture/improvement text with the real LUNA-WARM-05
      failure and honest boundary: v2 process-free proof does not establish
      SUMO equivalence or speedup.
- Focused checks:
  - `python3 -m pytest -q tests/test_scenario.py
    tests/test_warm_state_cache.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state.py tests/test_monthly_warm_state_freeze.py
    tests/test_monthly_warm_state_v2_freeze.py`
  - process-free monkeypatch assertions that no SUMO, TraCI or campaign
    subprocess runs and only the five approved archive paths are opened
  - mechanical field-rule, route-audit, selected-point and three-identity
    coverage verifiers
  - `python3 tools/freeze_monthly_warm_state_v2.py --verify`
  - `python3 -m json.tool validation/monthly_warm_state_manifest_v2.json`
  - canonical v2 content-key, archive-hash and source-fingerprint recomputation
  - `git diff --check --` every changed allowed source/test/artifact/document
  - `git status --short`
- Approval gate: `REQUIRED`
  - Approval status: `APPROVED ONCE` for the five exact files and named
    process-free scope.
  - Exact scope: read only the five named files under exact archive
    `runs/demand-20260721-222017-41bc682a-bbe1` to derive and freeze route
    hashes, mutation audits and safe warm points; implement/test/document the
    process-free corrections and create the v2 freeze. No other outcome access.
  - Exact user message (recorded verbatim):
    > I explicitly approve LUNA-WARM-06 revision 1 to read only
    > runs/demand-20260721-222017-41bc682a-bbe1/demand_meta.json,
    > manifest.json, calibrated.rou.xml, calibrated_v1.rou.xml and
    > calibrated_v2.rou.xml for route hashes, mutation audits and route-safe
    > warm-point derivation; implement and test the named process-free
    > warm-boundary, closure-throughput, all-identity and diagnostic
    > corrections; and create the unapproved/unexecuted
    > validation/monthly_warm_state_manifest_v2.json. No SUMO, TraCI,
    > executable/network preflight, campaign or other runs/outcome inspection,
    > demand or horizon generation/warming, product activation, Stage B,
    > release, deployment or publication is approved.
  - User-message date: `2026-07-29`
  - Sol recorder/date: `Sol High / 2026-07-29`
- Terminal handoff conditions:
  - While blocked, stop after this plan. Only the exact quoted approval can
    authorize Sol to record the gate and transition to `READY_FOR_LUNA`.
  - After approval, Luna completes all three internal checkpoints and hands
    off once when every acceptance criterion passes, or stops at an AGENTS.md
    terminal boundary with exact evidence and the safest next decision.
<!-- COMPLETED_TASK_LUNA_WARM_06_END -->

<!-- LUNA_WARM_05_REV1_ACTIVE_TASK_HISTORY_START -->
## ACTIVE_TASK

### LUNA-WARM-05 — Execute the fresh paired warm-state campaign once

- Task ID: `LUNA-WARM-05`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol approved the honest terminal campaign failure;
  equivalence and speedup remain unproven`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, validate the immutable
  `monthly_warm_state_v1` contract and environment, then execute its paired
  cold-versus-warm SUMO campaign exactly once. Inspect only the new
  content-keyed root created by this task and independently validate its
  equivalence, coverage, execution, cache-publication and performance record.
  Preserve an honest pass, fail, interruption or preflight refusal without
  rerun, resume, repair or source change. Do not inspect any other outcome,
  warm demand/horizons, activate warm product behavior, adopt Stage B, release
  or publish.
- Completion outcome: one preserved, independently checked campaign record
  proving or rejecting production-observation equivalence and reporting
  measured warm-path performance for the exact frozen case.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - `runs/monthly-warm-state-validation/21989bfe040e482e0af3e2f884b78233ee935cd69c3218c598c1b6cbdc98eb1d/**`
    (new task-created campaign root only, after exact approval)
  - task-created temporary workspaces used by the named checks or frozen
    process, removed before handoff; no pre-existing temporary path
  - `TASKS.md`
  - `AGENT_NOTES.md`
- Forbidden work:
  - While blocked, do not run focused checks, executable/network/demand
    preflight, SUMO, TraCI or the campaign; do not create, inspect or test for
    existence of the proposed root.
  - After approval, do not rerun, resume, repair, delete or overwrite the
    campaign root; inspect any other `runs/` artifact/report/outcome; or edit
    source, tests, manifests, demand, network, policy, thresholds or evidence.
  - Do not repair the unrelated persistent-SUMO fingerprint drift in this
    evidence task.
  - Do not enable warm execution by default, expose it through the API/product,
    claim equivalence beyond the frozen case, generate/warm demand or horizons,
    adopt Stage B, deploy, push, release or publish.
  - Do not weaken exact semantic comparison, provenance, cache integrity,
    health, closure-integrity, feasibility, recovery, recall, regret,
    failure-recall, release or publication gates.
- Acceptance criteria:
  1. Before any approved action, task ID/revision/state and the recorded exact
     approval scope, key and root match across all current workflow blocks.
  2. Run the named process-free suite and frozen-manifest verification. The
     manifest must reproduce byte-for-byte at key
     `21989bfe040e482e0af3e2f884b78233ee935cd69c3218c598c1b6cbdc98eb1d`,
     remain `frozen_unapproved_unexecuted`, and retain all 13 matching source
     fingerprints plus the frozen field partition and prefix schema.
  3. Before root creation, validate the exact schedule ID, q10/q50/q90 seeds,
     warm point, network hash, demand build key, archive path, 480 intervals
     and route files; require the supported SUMO executable/version; confirm
     the final root is absent and not a symlink. Any mismatch stops without
     creating the root.
  4. Execute exactly once:
     `PYTHONDONTWRITEBYTECODE=1 python3
     run_monthly_warm_state_validation.py --manifest
     validation/monthly_warm_state_manifest_v1.json --execute
     --approval-token
     21989bfe040e482e0af3e2f884b78233ee935cd69c3218c598c1b6cbdc98eb1d`.
     A nonzero exit or interruption is terminal; do not retry or repair.
  5. Inspect only the task-created content-keyed root. Recompute the record
     content key and validate manifest identity, exactly three comparisons,
     complete identity coverage, real cold/warm arm labels, the frozen warm
     point, semantic mismatches, phase runtimes and peak RSS.
  6. A passing record requires zero mismatches, complete execution evidence,
     exactly three distinct published cache keys, and three cache entries that
     restore under their exact identities. A failed record must publish no
     usable cache material. Report either outcome honestly.
  7. Preserve the complete task-created root unchanged. Do not infer adoption,
     release, generalized equivalence or guaranteed speedup from a pass; a fail
     authorizes no rerun, repair or weakened comparison.
  8. Record exact commands, exit status, bounded evidence and whether any
     temporary workspace remains; then hand off once for Sol review.
- Focused checks:
  - `python3 -m pytest -q tests/test_scenario.py
    tests/test_warm_state_cache.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state.py tests/test_monthly_warm_state_freeze.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3
    run_monthly_warm_state_validation.py --manifest
    validation/monthly_warm_state_manifest_v1.json`
  - `python3 tools/freeze_monthly_warm_state_v1.py --verify`
  - `python3 -m json.tool validation/monthly_warm_state_manifest_v1.json`
  - read-only exact network/SUMO/archive/schedule/seed/root-absence preflight
  - exact one-time execution command in acceptance criterion 4
  - read-only canonical verifier confined to the task-created root
  - `git diff --check -- TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `REQUIRED`
  - Approval status: `APPROVED ONCE` for this exact task, key, root and scope.
  - Exact scope/key/root: the named process-free checks, read-only executable/
    network/exact archived-demand preflight, one frozen paired SUMO execution,
    task-created temporary workspaces, and inspection only of
    `runs/monthly-warm-state-validation/21989bfe040e482e0af3e2f884b78233ee935cd69c3218c598c1b6cbdc98eb1d`.
  - Exact user message (recorded verbatim):
    > I explicitly approve LUNA-WARM-05 revision 1 to run the one-time
    > monthly_warm_state_v1 paired cold-versus-warm SUMO campaign at content key
    > 21989bfe040e482e0af3e2f884b78233ee935cd69c3218c598c1b6cbdc98eb1d
    > and artifact root runs/monthly-warm-state-validation/
    > 21989bfe040e482e0af3e2f884b78233ee935cd69c3218c598c1b6cbdc98eb1d,
    > including the named process-free checks, canonical manifest/source/
    > schedule/seed checks, exact SUMO/network/archived-demand preflight, one
    > frozen execution, its task-created temporary workspaces, and inspection
    > only of that task-created root. No rerun, resume, repair, other
    > run/outcome inspection, demand or horizon warming, product activation,
    > Stage B, release, deployment or publication is approved.
  - User-message date: `2026-07-29`
  - Sol recorder/date: `Sol High / 2026-07-29`
- Terminal handoff conditions:
  - While blocked, stop after this plan. Only the exact quoted approval can
    authorize Sol to record the gate and transition to `READY_FOR_LUNA`.
  - After approval, hand off after the single preflight/execution/inspection
    path, whether pass, fail, interruption or environment mismatch. Do not
    retry, repair, inspect elsewhere, activate warming or broaden scope.
<!-- LUNA_WARM_05_REV1_ACTIVE_TASK_HISTORY_END -->

<!-- LUNA_WARM_04_REV1_ACTIVE_TASK_HISTORY_START -->
## ACTIVE_TASK

### LUNA-WARM-04 — Make split observation accounting exhaustive and refreeze

- Task ID: `LUNA-WARM-04`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED` — Sol approved the exhaustive process-free prefix
  accounting and fresh unapproved freeze; SUMO equivalence and speedup remain
  unproven.
- Delivery size: `STANDARD`
- Objective and scope: Replace the aggregate-only prefix combiner exposed by
  LUNA-WARM-03 with versioned, field-complete prefix evidence that reconstructs
  the same canonical production observation as an uninterrupted run. Correct
  trip, counter, maximum, candidate-only and recovery-bucket boundary
  semantics; mechanically reject any unclassified production metric field.
  Preserve cold defaults and cache atomicity. Run only process-free tests,
  update the warm-state design documentation, and refreeze the existing v1
  validation manifest to a fresh unapproved/unexecuted content key. Do not run
  SUMO, inspect outcomes or activate warm execution.
- Completion outcome: one reviewed implementation slice whose exhaustive
  prefix contract and process-free end-to-end tests are ready for a separately
  approved fresh paired campaign.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - `traffic_sim/simulation/monthly_warm_state.py`
  - `traffic_sim/simulation/monthly_sumo.py`
  - `traffic_sim/simulation/metrics.py`
  - `traffic_sim/simulation/warm_state_cache.py`
  - `run_scenario.py`
  - `run_monthly_warm_state_validation.py`
  - `tools/freeze_monthly_warm_state_v1.py`
  - `validation/monthly_warm_state_manifest_v1.json`
  - `tests/test_scenario.py`
  - `tests/test_monthly_warm_state.py`
  - `tests/test_monthly_sumo.py`
  - `tests/test_warm_state_cache.py`
  - `tests/test_monthly_warm_state_freeze.py`
  - `ARCHITECTURE.md`
  - `IMPROVEMENT_PLAN.md`
  - `TASKS.md`
  - `AGENT_NOTES.md`
- Forbidden work:
  - Do not run SUMO, TraCI, executable/network/demand preflight, demand or
    horizon generation/warming, or any campaign command.
  - Do not create, enumerate, stat, hash, parse, mutate or delete any `runs/`
    path, including the spent LUNA-WARM-03 root.
  - Do not reuse the spent key or approval; approve a successor campaign;
    enable warm execution by default; expose it through product/API/search;
    adopt Stage B; or mutate an active release.
  - Do not weaken exact semantic comparison, provenance, cache integrity,
    health, closure-integrity, feasibility, recovery, recall, regret,
    failure-recall, release or publication gates.
  - Do not deploy, push, release or publish.
- Acceptance criteria:
  1. Replace stored `prefix_metrics` as the semantic contract with a versioned
     prefix-evidence schema. It distinguishes completed-prefix trip aggregates,
     prefix queue maximum, prefix recovery buckets and any boundary/counter
     evidence from a final `DisruptionMetrics`; malformed, partial, stale or
     unknown-schema evidence fails closed.
  2. The bootstrap requests completed-only tripinfo so vehicles active at the
     warm boundary are not counted once as unfinished prefix trips and again
     after restore. Add the narrow `run_sumo` option needed for this while
     preserving `write-unfinished=true` for every existing/default caller.
  3. Reconstruct every `DisruptionMetrics` field through an explicit registry:
     completed-prefix plus post-warm for disjoint trip accumulators; post-warm
     for final unfinished/end-state and candidate-route-only truncation fields;
     maximum across measured segments for `max_queue_vehicles`; post-warm
     closure throughput with a fail-closed pre-closure invariant; and an
     explicitly documented counter rule for loaded/inserted/teleports. Missing,
     wrong-typed or contradictory inputs raise instead of guessing.
  4. Bind the registry mechanically to `dataclasses.fields(DisruptionMetrics)`.
     Every field appears exactly once, and adding/removing a production field
     without assigning semantics fails a focused test and manifest verification.
  5. Reconstruct candidate recovery buckets as an ordered, non-overlapping,
     gap-free concatenation of prefix and post-warm buckets at the aligned warm
     point. Reject duplicate, missing, out-of-order or boundary-crossing
     intervals; never synthesize a bucket. The canonical cold/warm payload
     retains the same full bucket domain.
  6. Store prefix evidence inside each warm-state entry's existing atomic,
     digest-bound member set. Restore validates member name, schema and content
     before use; legacy `prefix_metrics`-only, missing, corrupt or incompatible
     entries are cache misses and are never repaired or overwritten.
  7. Cold construction, commands, metrics, caches and canonical observations
     remain byte/structurally unchanged. Warm execution remains reachable only
     from the validation harness's explicit option.
  8. Process-free tests reproduce the real failure (`max_queue_vehicles` 0/5),
     boundary-active trips, partial measurement, candidate-only values,
     counter differences and bucket boundary defects. They prove exact field
     coverage, one complete equivalent fake paired campaign, mismatch rollback,
     atomic publication/restore and unchanged cold command defaults.
  9. Refreeze `monthly_warm_state_manifest_v1.json` byte-for-byte with all 13
     source fingerprints fresh, status `frozen_unapproved_unexecuted`, no stored
     approval, the same frozen case/schedule/seeds/demand/network requirements,
     and a content key different from the spent
     `688f3591eee94d5b8422259ccc72a8ccc48ef5919df744656a4e87652634c1f5`.
     No campaign root may be created or inspected.
  10. Update the targeted architecture/improvement text to describe the
      prefix-evidence schema and honest remaining boundary: process-free tests
      and a fresh freeze do not prove SUMO equivalence or speedup.
- Focused checks:
  - `python3 -m pytest -q tests/test_scenario.py
    tests/test_warm_state_cache.py tests/test_monthly_sumo.py
    tests/test_monthly_warm_state.py tests/test_monthly_warm_state_freeze.py`
  - process-free monkeypatch assertions that implementation/test paths do not
    invoke SUMO, TraCI or campaign subprocesses
  - mechanical `DisruptionMetrics` field-partition verifier
  - `python3 tools/freeze_monthly_warm_state_v1.py --verify`
  - `python3 -m json.tool validation/monthly_warm_state_manifest_v1.json`
  - canonical content-key and 13-source-fingerprint recomputation
  - `git diff --check --` every allowed source/test/artifact/document path
  - `git status --short`
- Approval gate: `NOT_REQUIRED` — this revision is process-free and may not
  inspect or create outcomes. Any SUMO/preflight/outcome work requires a new
  task, a fresh immutable key/root and exact user approval.
- Terminal handoff conditions:
  - Hand off when every acceptance criterion and focused check passes, with the
    fresh unapproved key and exact changed-file list recorded.
  - Stop earlier only for an AGENTS.md terminal condition, especially if exact
    split semantics require a production artifact-contract expansion beyond
    the allowed files. Do not run a diagnostic simulation or weaken equality
    to resolve uncertainty.
<!-- LUNA_WARM_04_REV1_ACTIVE_TASK_HISTORY_END -->

<!-- LUNA_WARM_03_REV1_ACTIVE_TASK_HISTORY_START -->
## ACTIVE_TASK

### LUNA-WARM-03 — Run the frozen paired warm-state campaign once

- Task ID: `LUNA-WARM-03`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol approved the honest terminal campaign failure;
  equivalence and speedup remain unproven`
- Delivery size: `STANDARD`
- Objective and scope: After exact approval, validate the immutable
  `monthly_warm_state_v1` contract and environment, then execute its paired
  cold-versus-warm SUMO campaign exactly once. Inspect only the content-keyed
  root created by this task and recompute its record, coverage, execution,
  publication and performance evidence. Preserve an honest pass or fail
  without rerun, resume, repair or source change. Do not inspect any other
  run, warm demand/horizons, activate product behavior, merge Stage B, release
  or publish.
- Completion outcome: one preserved, independently checked campaign record
  proving or rejecting production-observation equivalence and reporting
  measured warm-path performance for the exact frozen case.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - `runs/monthly-warm-state-validation/688f3591eee94d5b8422259ccc72a8ccc48ef5919df744656a4e87652634c1f5/**`
    (new task-created campaign root only, after approval)
  - one task-created system temporary workspace for the frozen process,
    removed before handoff; no pre-existing temporary path may be inspected
  - `TASKS.md` and `AGENT_NOTES.md`
- Forbidden work:
  - Before exact approval, do not run checks or preflight SUMO/demand/network,
    create temporary workspaces, execute SUMO, or create/inspect any outcome.
  - After approval, do not rerun, resume, repair, delete or overwrite the
    campaign root; inspect any other `runs/` artifact/report/outcome; or edit
    source, tests, manifests, demand, network, policy, thresholds or evidence.
  - Do not enable warm execution by default, expose it through the API/product,
     claim equivalence or speedup, generate/warm demand or horizons, adopt
     Stage B, deploy, push, release or publish.
  - Do not weaken exact identity, health, closure-integrity, feasibility,
    recovery, provenance, recall, regret, failure-recall or release gates.
- Acceptance criteria:
  1. Define one canonical monthly production-observation payload from existing
     production types/functions, covering paired objective inputs, per-seed
     decision metrics, hard failures, health/end-state fields, recovery result
     and buckets, truncation/drop counts, matched-baseline identity and runtime/
     demand/network/source provenance. The cold and warm arms must call this
     shared assembly; no hand-copied reduced evaluator is accepted.
  2. Warm eligibility is deterministic and fail-closed: only a time-windowed
     closure with an earliest begin strictly after a positive, aligned warm
     point may branch. Whole-window/offset-zero, baseline-only, unsupported
     mode, missing prefix requirement or ambiguous envelope remains cold.
  3. Warm-state identity uses the original unfiltered route plus exact network,
     demand build, variant, seed, mode, warm point, baseline additionals,
     mandatory simulation sources, Git/Python/SUMO/platform, RNG-state and
     precision fields. Closure-specific filtered routes/additionals never
     masquerade as reusable baseline identity.
  4. State creation is isolated and candidate-free. Restore requires an
     immutable matching state and passing full monthly-production equivalence
     record for the same identity/source contract. Missing, corrupt, partial,
     stale or incompatible material is a cache miss and executes the unchanged
     cold path; an invalid existing entry is never overwritten or repaired.
  5. The warm branch starts at the certified warm point, applies the exact
     production filtered route and closure additional, and accounts explicitly
     for every pre-warm value required by criterion 1. It must not silently
     omit or synthesize prefix trip, edge, health, recovery or trajectory data.
  6. Default construction and all existing callers select cold execution with
     unchanged commands, results, cache behavior and provenance. Warm mode is
     reachable only from the validation harness's explicit option; product,
     API and ordinary monthly-search activation remain impossible in revision
     1 even if an unrelated cache entry exists.
  7. The paired harness runs the same frozen schedule, demand, seeds and mode
     through cold production and warm production arms in isolated workspaces;
     compares canonical criterion-1 payloads, closure integrity and health;
     records phase/runtime/RSS evidence; and publishes a passing equivalence
     record/cache material only after every semantic check passes. Any
     mismatch produces honest fail evidence and no usable cache.
  8. Freeze a canonical v1 manifest with exact case/schedule, seeds, mode,
     warm point, demand/network requirements, source fingerprints, comparison
     policy, performance reporting and isolated future artifact root. Its
     content key reproduces byte-for-byte; the runner refuses drift, a
     pre-existing root, unfrozen inputs or unapproved execution.
  9. Process-free tests cover all eligibility branches, identity changes,
     corruption/partial-entry cold fallback, no-overwrite behavior, default
     cold regression, shared-payload equivalence/mismatch, concurrent
     per-seed isolation, frozen-manifest drift and the rule that no subprocess
     or SUMO path runs in this task.
  10. A passing record requires exact execution evidence for every frozen
      identity: cold observation labelled `cold`, paired observation labelled
      `warm` with the frozen warm point, exactly one matching provisional state,
      and exactly one published cache key. Any cold fallback, wrong/missing arm
      metadata, state-set mismatch or publication-count mismatch fails honestly
      and leaves no usable cache. Process-free end-to-end regressions prove both
      the refusal cases and one complete passing case.
  11. Before root creation, the approved key and full root match this revision;
      the manifest reproduces byte-for-byte; all 13 source fingerprints,
      schedule ID, variants, canonical seeds and network hash match; the exact
      demand archive path/build key/480 intervals/q10-q50-q90 routes are valid;
      the required SUMO executable/version is available; and the final root is
      absent and not a symlink. Any mismatch stops without creating the root.
  12. The frozen execution command runs once:
      `PYTHONDONTWRITEBYTECODE=1 python3
      run_monthly_warm_state_validation.py --manifest
      validation/monthly_warm_state_manifest_v1.json --execute
      --approval-token
      688f3591eee94d5b8422259ccc72a8ccc48ef5919df744656a4e87652634c1f5`.
      Interruption or nonzero exit is terminal; no retry or repair is allowed.
  13. Inspection is confined to the task-created content-keyed root. Recompute
      the equivalence-record key and validate manifest identity, exactly three
      comparisons, complete coverage/execution evidence, distinct publication
      keys, semantic mismatches, phase runtimes and peak RSS. A passing record
      must expose exactly three restorable cache entries; a failed record must
      expose none.
  14. Report pass or fail honestly. A pass proves only the frozen case and
      authorizes no adoption; a fail is preserved diagnosis and authorizes no
      source/evidence repair, rerun or weakened gate.
- Focused checks:
  - `python3 -m pytest -q tests/test_warm_state_cache.py
    tests/test_monthly_sumo.py tests/test_monthly_warm_state.py
    tests/test_monthly_warm_state_freeze.py`
  - process-free monkeypatch assertion that no SUMO/subprocess call occurs
  - manifest production validation, content-key/source-fingerprint verifier
    and byte-for-byte freeze reproduction
  - `python3 -m json.tool validation/monthly_warm_state_manifest_v1.json`
  - `git diff --check -- traffic_sim/simulation/warm_state_cache.py
    traffic_sim/simulation/monthly_sumo.py
    traffic_sim/simulation/monthly_warm_state.py
    run_monthly_warm_state_validation.py
    tools/freeze_monthly_warm_state_v1.py tests/test_warm_state_cache.py
    tests/test_monthly_sumo.py tests/test_monthly_warm_state.py
    tests/test_monthly_warm_state_freeze.py
    validation/monthly_warm_state_manifest_v1.json TASKS.md AGENT_NOTES.md`
  - `git status --short`
  - exact execution command in criterion 12
  - read-only canonical verifier over only the task-created root, recorded
    verbatim in the Luna handoff
- Approval gate: `REQUIRED`
  - Approval status: `CONSUMED ONCE` by the frozen command; the content key is
    spent and cannot authorize a rerun, resume, repair or successor revision.
  - Exact scope/key/root: the named process-free checks, read-only executable/
    network/exact archived-demand preflight, one frozen paired SUMO execution,
    task-created temporary workspace, and inspection only of
    `runs/monthly-warm-state-validation/688f3591eee94d5b8422259ccc72a8ccc48ef5919df744656a4e87652634c1f5`.
  - Exact user message (recorded verbatim):
    > I explicitly approve LUNA-WARM-03 revision 1 to run the one-time
    > monthly_warm_state_v1 paired cold-versus-warm SUMO campaign at content key
    > 688f3591eee94d5b8422259ccc72a8ccc48ef5919df744656a4e87652634c1f5
    > and artifact root runs/monthly-warm-state-validation/
    > 688f3591eee94d5b8422259ccc72a8ccc48ef5919df744656a4e87652634c1f5,
    > including the named process-free checks, canonical manifest/source/
    > schedule/seed checks, exact SUMO/network/archived-demand preflight, one
    > frozen execution, its task-created temporary workspace, and inspection
    > only of that task-created root. No rerun, resume, repair, other
    > run/outcome inspection, demand or horizon warming, product activation,
    > Stage B, release, deployment or publication is approved.
  - User-message date: `2026-07-28`
  - Sol recorder/date: `Sol High / 2026-07-28`
- Terminal handoff conditions:
  - Execute only while `LUNA-WARM-03` revision 1 remains `READY_FOR_LUNA` and
    every recorded approval field still matches the key, root and scope.
  - After approval, hand off after the single run and bounded inspection,
    whether pass, fail, interruption or environment mismatch. Do not retry,
    repair, inspect elsewhere, warm, activate or broaden scope.
<!-- LUNA_WARM_03_REV1_ACTIVE_TASK_HISTORY_END -->

<!-- COMPLETED_TASK_LUNA_V6_05_START -->
## ACTIVE_TASK

### LUNA-V6-05 — Execute the untouched v6 held-out SUMO campaign once

- Task ID: `LUNA-V6-05`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved honest fail; non-executable`
- Delivery size: `EXTENDED`
- Objective and scope: After exact approval, validate the immutable v6
  manifest, policy, selection, source fingerprints and exact canonical demand;
  run the focused process-free suite and SUMO executable preflight; then
  execute the five-case/75-schedule campaign once with `seed_workers=3`.
  Resume only this task's root after interruption. Inspect only that root,
  recompute its production report and record an honest pass, fail, incomplete
  or corrupt disposition. Do not warm/generate demand, inspect other outcomes,
  repair evidence, adopt a gate, activate Stage B, release or publish.
- Completion outcome: one content-key-bound v6 evidence root accounts for all
  five frozen cases and 75 schedules, with stored outcomes/report and gate-
  record presence exactly matching production evaluation. Pass or fail is
  accepted honestly; no adoption certificate or product change follows.
- Internal checkpoints:
  1. Exact approval, immutable identities, 336 focused tests, exact-demand
     binding, root absence and SUMO executable/version preflight all pass.
  2. The exact command completes all five cases; an interruption may resume
     only this task-created root, never rerunning completed case evidence.
  3. Exact-root integrity, provenance, production evaluator recomputation and
     gate-record presence agree before the terminal handoff.
- Allowed files and resources:
  - Read-only frozen inputs:
    `validation/monthly_proxy_manifest_v6.json`,
    `validation/monthly_proxy_policy_v6.json`,
    `validation/heldout_v6_selection.json`, their ten recorded source paths,
    production validators/evaluator, required SUMO/network inputs, and the
    exact five canonical demand files under
    `runs/demand-20260721-222017-41bc682a-bbe1`.
  - After approval, create/resume/inspect only:
    `runs/closure-proxy-validation/e82718daca2ca890a3d4c13e1743204ae68be02bce2cf41131be227a23c506a0`.
  - Edit only `TASKS.md` and `AGENT_NOTES.md` for approval recording and the
    terminal workflow/handoff.
- Forbidden work:
  - While blocked, do not check root existence, preflight, resolve SUMO,
    inspect/hash/open any `runs/` member or execute any task check.
  - Never open, stat, enumerate, count, hash, parse or summarize another
    outcome/report/campaign root or demand archive. Do not mutate the canonical
    demand, frozen v6 inputs, source fingerprints, runner, tests or thresholds.
  - Do not generate/warm demand or horizons, change worker count/command,
    repair/normalize/delete evidence, rerun completed cases, synthesize a gate
    record, create an adoption certificate, activate Stage B, mutate release,
    push, deploy, publish or broaden claims.
- Acceptance criteria:
  1. Luna acts only after Sol records an approval for this exact task/revision,
     manifest key, root, command scope and approval message/date; any mismatch
     remains non-executable.
  2. After approval and before root creation, production-validate manifest key
     `e82718daca2ca890a3d4c13e1743204ae68be02bce2cf41131be227a23c506a0`,
     v6 identity, five cases/75 schedules, policy/selection keys, all ten source
     fingerprints, and byte-for-byte freeze reproduction.
  3. Run the 336-test focused process-free set. Bind only selection-recorded
     archive `runs/demand-20260721-222017-41bc682a-bbe1` and require its build
     ID `42d841800726b9b911df`, metadata, clean provenance and five hashes.
     Any failure is terminal before SUMO or root creation.
  4. Under approval, require the exact outcome root absent before the first
     attempt and require the SUMO executable/version and network inputs to
     resolve. A pre-existing root is terminal and is not inspected or reused.
  5. Execute exactly
     `python3 run_monthly_proxy_validation.py --manifest
     validation/monthly_proxy_manifest_v6.json --selection
     validation/heldout_v6_selection.json --seed-workers 3`. Resume with only
     that same command after interruption; never rerun a completed case.
  6. Inspect only the exact task-created root. Require complete, parseable and
     identity-bound evidence for five distinct frozen cases and all 75 schedule
     IDs exactly once, with complete candidate/runtime/demand/network/seed/
     SUMO provenance and no unexpected case or schedule.
  7. Recompute `evaluate_validation_set(manifest, outcomes)` in memory and
     require canonical equality with `report.json`. Require `gate_record.json`
     exactly when `gate_record_for(...)` returns a record, and canonical
     equality when present; forbid it for fail/incomplete evidence.
  8. Accept honest `pass` or `fail`. Record every gate check and bounded metric,
     exact identities/commands and final file hashes. Revalidate frozen inputs
     and transition once without adoption, warming, release or publication.
- Focused checks:
  - after approval: manifest/policy/selection/content-key/ten-fingerprint and
    byte-for-byte freeze verifier
  - exact 336-test process-free command from LUNA-V6-04
  - exact canonical-demand binder plus root-absence and SUMO/network preflight
  - exact command in acceptance criterion 5
  - exact-root-only completeness, identity, provenance and hash snapshot
  - in-memory production evaluator and gate-record recomputation/equality
  - post-run frozen-input identity and exact-root snapshot consistency
  - `git diff --check -- TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate:
  - `REQUIRED — RECORDED`
  - Exact required scope/key/root: one resumable `monthly_proxy_v6` held-out
    SUMO campaign using the command in criterion 5 with `seed_workers=3`, its
    canonical frozen-input/focused-test/exact-demand/SUMO preflight, creation
    and resumption only of its task-created root, and inspection/recomputation
    only within content key
    `e82718daca2ca890a3d4c13e1743204ae68be02bce2cf41131be227a23c506a0`
    at root
    `runs/closure-proxy-validation/e82718daca2ca890a3d4c13e1743204ae68be02bce2cf41131be227a23c506a0`.
  - Exact user message received:
    “I explicitly approve LUNA-V6-05 revision 1 to run the one-time resumable
    monthly_proxy_v6 held-out SUMO campaign at content key
    e82718daca2ca890a3d4c13e1743204ae68be02bce2cf41131be227a23c506a0
    and artifact root
    runs/closure-proxy-validation/e82718daca2ca890a3d4c13e1743204ae68be02bce2cf41131be227a23c506a0,
    including canonical manifest/policy/selection and source-fingerprint
    checks, the named focused process-free tests, exact canonical-demand and
    SUMO executable/network preflight, creation and execution with
    seed_workers=3 using the frozen command, resumption only of its own task-
    created root after interruption, and inspection and production-evaluator
    recomputation only within that root. No other runs/outcome/report
    inspection, demand or horizon generation/warming, evidence repair,
    adoption certificate, Stage B activation, release mutation, deployment or
    publication is approved.”
  - User-message date: `2026-07-28`
  - Sol recorder/date: `Sol High / 2026-07-28`
- Terminal handoff conditions:
  - Execute only while this exact approval record and `READY_FOR_LUNA` state
    remain consistent for task `LUNA-V6-05` revision 1.
  - After approval, hand off once after honest complete pass/fail evidence or
    an approval/preflight/pre-existing-root/identity/provenance/integrity/
    executable blocker. Do not inspect elsewhere, repair evidence, broaden
    scope or attempt a distinct campaign.
<!-- COMPLETED_TASK_LUNA_V6_05_END -->

<!-- CONCLUDED_TASK_LUNA_V6_04_START -->
## ACTIVE_TASK

### LUNA-V6-04 — Bind v6 execution to its exact frozen demand archive

- Task ID: `LUNA-V6-04`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved; non-executable`
- Delivery size: `STANDARD`
- Objective and scope: Harden the monthly validation runner before any v6
  execution. Require v6 to consume its frozen selection artifact and bind the
  exact canonical archive path, metadata and five hashes already recorded
  there; never discover sibling demand archives by glob. Validate every
  identity before creating a run root or resolving SUMO. Add process-free
  regression tests, regenerate only the v6 manifest for the changed runner
  fingerprint/content key, and keep policy/selection bytes fixed. Do not run
  SUMO, inspect outcomes, warm demand or broaden adoption.
- Completion outcome: v6 has one reproducible, process-free-verified execution
  contract that can only use its designated demand bytes; its final manifest
  key is ready for a separate one-time campaign approval.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Create/edit `run_monthly_proxy_validation.py`,
    `tests/test_monthly_proxy_runner.py`,
    `tests/test_heldout_v5_freeze.py`,
    `validation/monthly_proxy_manifest_v6.json`, `TASKS.md`, and
    `AGENT_NOTES.md`.
  - Read-only: `validation/heldout_v6_selection.json`,
    `validation/monthly_proxy_policy_v6.json`, v4/v5 frozen artifacts and
    adoption contract for regression checks; `tools/freeze_heldout_v6.py`,
    `traffic_sim/simulation/heldout_selection.py`, relevant pure validators,
    and existing focused tests.
  - Read/hash/parse only these exact canonical archive members:
    `runs/demand-20260721-222017-41bc682a-bbe1/{calibrated.rou.xml,
    calibrated_v1.rou.xml,calibrated_v2.rou.xml,demand_meta.json,manifest.json}`.
    No other `runs/` member is readable.
- Forbidden work:
  - Do not open, stat, enumerate, hash or inspect any other `runs/` member,
    outcome, report or campaign root. Do not invoke SUMO/TraCI, resolve its
    executable/version, preflight a campaign, create a run root, generate/warm
    demand or create evidence.
  - Do not edit the v6 policy or selection, any v1-v5 frozen artifact,
    adoption contract, production gate/evaluator thresholds, shortlist
    behavior, architecture, priorities, release, UI or unrelated user changes.
  - Do not accept demand-key-only resolution for v6, silently fall back to
    `_demand_archives()`, refresh spent v5 fingerprints, weaken validation,
    adopt, deploy, release or publish.
- Acceptance criteria:
  1. The v6 CLI requires an explicit selection artifact. It verifies a regular
     non-symlink file, canonical selection content key equal to the manifest's
     `selection_content_key`, matching campaign identity and a complete
     `canonical_demand` record before using it.
  2. The recorded canonical path is repository-confined and passed to
     `bind_canonical_archive`; exact key, build ID, epoch, interval count,
     clean provenance and all five hashes must match. Every v6 case demand ID
     must equal that bound key.
  3. V6 never calls, globs or falls back to sibling discovery. Missing or
     altered selection/archive fields, traversal, symlink, hash/metadata/key
     mismatch, case mismatch or ambiguous sibling traps fail before SUMO
     resolution and before `runs/closure-proxy-validation/<key>` exists.
  4. Legacy non-v6 behavior is not broadened or silently relabeled. The exact
     approved future v6 command shape is frozen as
     `python3 run_monthly_proxy_validation.py --manifest
     validation/monthly_proxy_manifest_v6.json --selection
     validation/heldout_v6_selection.json --seed-workers 3`.
  5. Process-free synthetic tests exercise valid binding and every fail-closed
     branch without reading live sibling archives or invoking SUMO. A read/
     discovery guard proves only the five exact canonical files are touched by
     the real-v6 binding check.
  6. Regeneration leaves v6 policy and selection byte-identical and changes
     only the manifest fields implied by the runner fingerprint/content key.
     All ten final source fingerprints match; byte-for-byte freeze verification
     passes and no outcome/gate/certificate exists.
  7. Spent v5 stays frozen and unadoptable after runner-source drift; its test
     records the additional expected mismatch without refreshing any v5 byte.
  8. All focused checks pass, edits remain within the six allowed files, and
     the terminal handoff records the final v6 manifest key for Sol review.
- Focused checks:
  - synthetic exact-binding/no-sibling/no-root/no-SUMO runner tests
  - `python3 -m json.tool validation/monthly_proxy_manifest_v6.json`
  - `python3 tools/freeze_heldout_v6.py --verify`
  - final v6 content-key/source-fingerprint recomputation and policy/selection
    before/after SHA-256 equality
  - `python3 -m pytest -q tests/test_monthly_proxy_runner.py
    tests/test_heldout_gate.py tests/test_heldout_v5_freeze.py
    tests/test_heldout_selection.py tests/test_heldout_v6_freeze.py
    tests/test_monthly_search.py tests/test_monthly_proxy.py
    tests/test_proxy_validation.py`
  - `git diff --check -- run_monthly_proxy_validation.py
    tests/test_monthly_proxy_runner.py tests/test_heldout_v5_freeze.py
    validation/monthly_proxy_manifest_v6.json TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED` — this task is process-free and may read only
  the already designated five canonical demand files. It cannot execute SUMO,
  inspect/create outcomes, warm demand, adopt or release.
- Terminal handoff conditions:
  - Hand off once every criterion passes with the final manifest key.
  - Stop early for state/authority conflict, any sibling/outcome access,
    canonical-byte drift, need to alter selection/policy/production semantics,
    material scope expansion or three serious failed approaches. Do not execute
    or request retroactive approval against a pre-regeneration key.
<!-- CONCLUDED_TASK_LUNA_V6_04_END -->

<!-- CONCLUDED_TASK_LUNA_V6_03_START -->
## ACTIVE_TASK

### LUNA-V6-03 — Close v5/v6 compatibility tests without moving evidence

- Task ID: `LUNA-V6-03`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved; non-executable`
- Delivery size: `STANDARD`
- Objective and scope: Correct the nine stale process-free assertions exposed
  by the v6 freeze. Make gate tests use the frozen v6 manifest for positive
  synthetic compatibility while proving spent v4/v5 campaigns remain
  unadoptable. Make v5-freeze tests evaluate their recorded v1-v4 boundary,
  canonical frozen identities and deliberate source drift without treating
  later v6 inputs as v5 history. Change tests only; do not recompose, rewrite
  or re-fingerprint any frozen artifact, alter production behavior, inspect
  outcomes, run SUMO, or start warming.
- Completion outcome: all seven focused process-free modules pass; v6 is the
  tested current frozen identity, v4/v5 remain immutable and fail closed, and
  the repository is ready for a separately planned warming/execution decision.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Edit only `tests/test_heldout_gate.py`,
    `tests/test_heldout_v5_freeze.py`, `TASKS.md`, and `AGENT_NOTES.md`.
  - Read-only: v4/v5/v6 policy, selection and manifest artifacts;
    `validation/monthly_gate_adoption_contract_v1.json`;
    `traffic_sim/simulation/heldout_gate.py`,
    `traffic_sim/simulation/monthly_search.py`,
    `traffic_sim/simulation/proxy_validation.py`,
    `run_monthly_proxy_validation.py`, and the v5/v6 freeze tools solely to
    understand the existing process-free assertions.
- Forbidden work:
  - Do not edit production source, freeze tools, adoption contract, any v1-v6
    policy/selection/manifest, gate/certificate, architecture, priorities,
    active release or unrelated user-owned change.
  - Do not open, stat, enumerate, hash or inspect any `runs/` member, outcome,
    report or campaign root; do not invoke SUMO/TraCI, preflight executables,
    generate/warm demand or create evidence.
  - Do not make v4/v5 adoptable, re-sync their source fingerprints, weaken
    fail-closed checks, fabricate an actual gate/certificate, or make tests
    pass by skipping, xfail, deleting coverage or relaxing assertions.
- Acceptance criteria:
  1. Positive synthetic gate/certificate compatibility fixtures bind the
     current v6 manifest and retain all field, byte, canonical-key, threshold,
     metric, source-fingerprint and producer/loader consistency coverage.
  2. Explicit negative tests prove v4 is rejected and v5 cannot adopt because
     its frozen enforcement fingerprints no longer match the live tree; no
     actual gate record or adoption certificate is created.
  3. V5 disjointness is checked against an explicit v1-v4 historical input
     set, never a glob that absorbs v6 or later manifests; the recorded count
     and intersection remain true for the boundary v5 froze.
  4. V5 canonical content keys and recorded fingerprint values remain
     unchanged. Tests prove current source/recomposition drift invalidates
     reuse while the process-free builder does not mutate tracked artifacts.
     No spent fingerprint or artifact is refreshed.
  5. The nine previously failing assertions pass for the stated semantics, and
     all surrounding negative security/fail-closed tests remain active.
  6. The exact focused command and diff check pass with changes confined to
     the four allowed files; the handoff makes no warming-readiness claim
     beyond process-free prerequisite completion.
- Focused checks:
  - `python3 -m pytest -q tests/test_heldout_gate.py
    tests/test_heldout_v5_freeze.py tests/test_heldout_selection.py
    tests/test_heldout_v6_freeze.py tests/test_monthly_search.py
    tests/test_monthly_proxy.py tests/test_proxy_validation.py`
  - direct assertion that no test in the edited modules skips/xfails the nine
    named cases and that positive helpers name
    `validation/monthly_proxy_manifest_v6.json`
  - before/after SHA-256 equality for every v4/v5/v6 policy, selection and
    manifest artifact while the focused checks run
  - `git diff --check -- tests/test_heldout_gate.py
    tests/test_heldout_v5_freeze.py TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED` — this is a process-free test-only correction;
  it cannot inspect evidence, execute SUMO, warm demand, adopt or release.
- Terminal handoff conditions:
  - Hand off once when every acceptance criterion passes.
  - Stop early only for state/authority conflict, frozen-artifact mutation,
    need for production/contract changes, material scope expansion or three
    serious failed approaches with the required evidence and safe options.
    Restore no user-owned file and do not broaden into warming or execution.
<!-- CONCLUDED_TASK_LUNA_V6_03_END -->

<!-- CONCLUDED_TASK_LUNA_V6_02_START -->
## ACTIVE_TASK

### LUNA-V6-02 — Bind canonical demand and freeze untouched held-out v6

- Task ID: `LUNA-V6-02`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review blocked approval; successor scope required`
- Delivery size: `EXTENDED`
- Objective and scope: Bind v6 selection exclusively to the successful clean-
  tree July 21 demand archive by exact path, build ID and five file hashes.
  Extend the approved process-free resolver with an exact-path mode that never
  scans sibling archives. Compute deterministic q10/q50/q90 schedule-window
  exposure and topology features, exclude all v1-v5 edges and physical
  neighbors, and freeze exactly five cases/75 schedules as v6 policy,
  selection and manifest artifacts. Bind demand inputs and final source
  fingerprints, reproduce byte-for-byte, update accurate bounded
  documentation, and keep adoption closed. No outcomes, SUMO, warming, prior-
  result tuning, Stage B activation, release, deployment or publication.
- Completion outcome: three reproducible v6 artifacts bind one exact clean
  demand archive and select five traffic-bearing, temporally varying,
  physically independent untouched cases before outcomes; production remains
  default-closed and no campaign evidence or adoption artifact exists.
- Internal checkpoints:
  1. Add and test exact-path archive binding plus a streaming, process-free
     route-exposure extractor; freeze the versioned support/variation formula.
  2. Select five v1-v5-disjoint independent cases and freeze policy, selection
     and manifest with raw pre-outcome features and canonical identities.
  3. Reproduce all artifacts byte-for-byte, validate final fingerprints and
     default-closed behavior, correct documentation, and hand off once.
- Allowed files and resources:
  - Read-only canonical archive:
    `runs/demand-20260721-222017-41bc682a-bbe1/{calibrated.rou.xml,
    calibrated_v1.rou.xml,calibrated_v2.rou.xml,demand_meta.json,manifest.json}`.
    No other `runs/` member is readable in this task.
  - Read-only tracked inputs: `web/data/network.geojson`,
    `sumo/network.net.xml`; existing v1-v5 policy/selection/manifest artifacts
    only for identity, schedules and edge exclusion; relevant pure
    schedule/manifest/parser source; and the existing adoption contract only
    to prove default-closed compatibility.
  - Create/edit:
    `traffic_sim/simulation/heldout_selection.py`,
    `tools/freeze_heldout_v6.py`,
    `tests/test_heldout_selection.py`,
    `tests/test_heldout_v6_freeze.py`,
    `validation/monthly_proxy_policy_v6.json`,
    `validation/heldout_v6_selection.json`,
    `validation/monthly_proxy_manifest_v6.json`,
    `traffic_sim/simulation/monthly_search.py`,
    `tests/test_monthly_search.py`, `ARCHITECTURE.md`,
    `IMPROVEMENT_PLAN.md`, `TASKS.md`, and `AGENT_NOTES.md`.
- Canonical demand identity:
  - Path:
    `runs/demand-20260721-222017-41bc682a-bbe1`
  - Demand key / build ID / horizon:
    `2ac04275daabe93c` / `42d841800726b9b911df` / 480 intervals from
    `2027-07-15T00:00:00`
  - Manifest: `kind=demand`, `status=succeeded`,
    `git_commit=a26a068c0a54fe697aa5c97497469a71bc58c399`,
    `git_dirty=false`
  - SHA-256:
    `calibrated.rou.xml=56000bc43a8fb00a6f6dd9b47db70a1cf214a8fc95edaade70e3e6e2cbc523ca`;
    `calibrated_v1.rou.xml=4201008a778ec699c55a7fd90aa96c393b914ec48197c5a4d9fd8e1795687a0c`;
    `calibrated_v2.rou.xml=8f8bdacf8bcd772a01cd3899b5b81b3351274a232e1a11ecf5d9cb7aebf3a259`;
    `demand_meta.json=3c6c61040c01236cd52223cdab7e0262af64fcb31ed047885a57bba9c36d6026`;
    `manifest.json=dd54e26fcb63809d3a904c24dddc3c3b0b8fc919b964737f8a2367e48c04e0e9`.
  - Sol rationale: it is the only successful claimant built from a clean Git
    tree; its three route bytes were independently reproduced by the later
    archive. This designation is v6-local and outcome-blind; it does not repair
    or redefine the globally ambiguous demand key.
- Forbidden work:
  - Do not open, stat, enumerate, count, hash or parse any other `runs/` member,
    campaign outcome/report/root or spent v5 evidence. The exact-path resolver
    must not glob or discover siblings.
  - Do not invoke SUMO/TraCI, perform executable campaign preflight, create an
    outcome/gate/adoption certificate, generate/warm demand or mutate the
    canonical archive.
  - Do not edit v1-v5 frozen artifacts, the adoption contract, evaluator/gate
    thresholds, proxy/shortlist/Stage B behavior, active release or unrelated
    user-owned changes.
  - Do not use any prior simulation result to select cases or tune the
    support/variation rule, and do not claim that demand exposure guarantees a
    300-second SUMO objective spread.
- Acceptance criteria:
  1. Exact-path binding requires a real non-symlink directory and five regular
     non-symlink files resolving within that directory; it validates
     every canonical identity field/hash above before parsing routes and never
     reads or discovers a sibling. Any mismatch fails before artifact writes.
  2. Synthetic tests reject wrong path, traversal/symlink, missing file, hash,
     key, build, horizon, epoch, kind, status, commit or dirty-state mismatch;
     synthetic fixtures prove the generic resolver remains fail-closed on
     ambiguity without reading another live archive.
  3. A streaming process-free extractor correctly handles the canonical SUMO
     route constructs and computes per edge, q10/q50/q90 variant and exact
     schedule closure window: weighted vehicle/flow exposure, minimum support,
     median support and temporal range. Boundary and weighting behavior have
     synthetic positive/negative tests.
  4. Before artifact generation, code and tests define a versioned,
     noninteractive selection formula: positive exposure in every required
     variant/window is mandatory; eligible candidates are ranked
     deterministically by temporal-variation signal, robust support and stable
     edge ID; expected-discriminating labels use only that frozen relative
     rule. No manual edge or threshold choice after feature computation.
  5. Filtering excludes every v1-v5 directed edge, reverse, shared-junction
     neighbor and short/stub edge. Selection yields exactly five pairwise
     independent cases/75 unique schedules; otherwise fail without final
     artifacts. At least two cases are preregistered expected-discriminating.
  6. The selection artifact records the exact canonical archive identity and
     five hashes, formula version, raw per-variant/window features, exclusion
     proof, ranking/tie-break reason and no outcome-derived field.
  7. Policy, selection and manifest content keys are canonical. The production
     manifest validates, declares `frozen_before_outcomes`, binds policy and
     selection keys, and records final executable source fingerprints after
     all code changes; the selection key transitively binds demand bytes.
  8. The freeze tool writes through a temporary staging area, refuses
     overwrite/partial finals, reads no `runs/` sibling, creates no run root and
     reproduces all three artifacts byte-for-byte in a clean temporary
     destination. Source or demand-byte drift invalidates validation.
  9. Only after final artifacts/fingerprints pass, current frozen-campaign
     identity moves from v5 to v6. The absence of a gate plus adoption
     certificate still closes Stage B; no product behavior or claim expands.
  10. Documentation corrects stale “v5 unexecuted” text: v5 ran and failed
      discrimination; v6 is frozen but unexecuted; its archive designation is
      local, and any SUMO execution requires a new exact task and user approval.
  11. All focused checks pass and the terminal handoff records exact identities,
      commands and bounded evidence without broadening scope.
- Focused checks:
  - exact canonical archive regular-file/path/identity/five-hash verifier
  - `python3 -m json.tool validation/monthly_proxy_policy_v6.json`
  - `python3 -m json.tool validation/heldout_v6_selection.json`
  - `python3 -m json.tool validation/monthly_proxy_manifest_v6.json`
  - process-free exposure, formula, exclusion, independence, 5-case/75-
    schedule, canonical-key and source-fingerprint verifier
  - production manifest validation and default-closed adoption check
  - clean-temporary-destination byte-for-byte freeze reproduction
  - `python3 -m pytest -q tests/test_heldout_selection.py
    tests/test_heldout_v6_freeze.py tests/test_heldout_gate.py
    tests/test_monthly_search.py tests/test_monthly_proxy.py
    tests/test_proxy_validation.py`
  - `git diff --check -- traffic_sim/simulation/heldout_selection.py
    tools/freeze_heldout_v6.py tests/test_heldout_selection.py
    tests/test_heldout_v6_freeze.py validation/monthly_proxy_policy_v6.json
    validation/heldout_v6_selection.json
    validation/monthly_proxy_manifest_v6.json
    traffic_sim/simulation/monthly_search.py tests/test_monthly_search.py
    ARCHITECTURE.md IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED` — Sol has made the bounded architecture/
  provenance designation; implementation is process-free, reads only the exact
  archive, and cannot execute SUMO, inspect outcomes or activate/release.
- Terminal handoff conditions:
  - Hand off once when every acceptance criterion passes.
  - Stop early only for state/authority conflict, exact canonical byte drift,
    unsupported canonical route construct, fewer than five eligible independent
    cases, required architecture/artifact-contract change, material scope
    expansion or three serious failed approaches with mandated evidence and
    safe options. Do not inspect another archive or loosen the formula.
<!-- CONCLUDED_TASK_LUNA_V6_02_END -->

<!-- COMPLETED_TASK_LUNA_V6_01_START -->
## COMPLETED_TASK

### LUNA-V6-01 — Freeze a demand-supported untouched held-out v6 package

- Task ID: `LUNA-V6-01`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — APPROVED fail-closed stop; v6 not frozen`
- Delivery size: `EXTENDED`
- Objective and scope: Implement a deterministic, outcome-blind selector that
  uses only immutable archived-demand route exposure plus topology to choose
  traffic-bearing, closure-time-sensitive candidate edges. Resolve exact
  demand build `2ac04275daabe93c` fail-closed, exclude every v1-v5 edge and
  physical neighbor, pre-register explicit support/variation thresholds, and
  freeze five cases/75 schedules as new v6 policy, selection and manifest
  artifacts. Bind exact demand-input digests and final source fingerprints.
  Keep adoption default-closed. Do not read any campaign outcome/report/root,
  run SUMO, generate/warm demand, tune from prior outcomes, adopt Stage B,
  alter a release, deploy or publish.
- Completion outcome: reproducible v6 policy, selection and manifest artifacts
  identify five physically independent, v1-v5-disjoint cases whose scheduled
  closure windows meet the frozen demand-support/variation contract; all
  process-free checks pass and no v6 outcome, gate record or adoption
  certificate exists.
- Internal checkpoints:
  1. Implement and test a pure archive resolver/exposure extractor that rejects
     missing or divergent duplicate archives and binds exact input hashes.
  2. Freeze five eligible cases/75 unique schedules with explicit pre-outcome
     feature evidence, independence/exclusion proofs and canonical identities.
  3. Reproduce the three artifacts byte-for-byte, validate final source
     fingerprints/default-closed behavior, update bounded documentation and
     hand off once.
- Allowed files:
  - Read-only inputs: `web/data/network.geojson`, `sumo/network.net.xml`; the
    successful archived-demand metadata, manifest and q10/q50/q90 route files
    that deterministically resolve demand build `2ac04275daabe93c`; existing
    v1-v5 policy/selection/manifest artifacts only for immutable identity and
    edge exclusion; and relevant pure schedule/manifest/parser source.
  - Create/edit:
    `traffic_sim/simulation/heldout_selection.py`,
    `tools/freeze_heldout_v6.py`,
    `tests/test_heldout_selection.py`,
    `tests/test_heldout_v6_freeze.py`,
    `validation/monthly_proxy_policy_v6.json`,
    `validation/heldout_v6_selection.json`,
    `validation/monthly_proxy_manifest_v6.json`,
    `traffic_sim/simulation/monthly_search.py`,
    `tests/test_monthly_search.py`, `ARCHITECTURE.md`,
    `IMPROVEMENT_PLAN.md`, `TASKS.md`, and `AGENT_NOTES.md`.
- Forbidden work:
  - Do not open, stat, enumerate, count, hash, parse or otherwise inspect any
    campaign outcome/report/root, including the spent v5 root; do not use any
    prior outcome-derived value as selector input or threshold justification.
  - Do not invoke SUMO/TraCI, run campaign/preflight code with executable side
    effects, create an outcome/gate/adoption certificate, generate or warm
    demand/horizons, or mutate archived demand.
  - Do not edit v1-v5 frozen artifacts, the adoption contract, evaluator/gate
    thresholds, proxy behavior, Stage B behavior, active release, deployment,
    publication or unrelated user-owned changes.
  - Do not claim that demand support predicts or guarantees a 300-second SUMO
    spread; it is a preregistered selection signal only.
- Acceptance criteria:
  1. The archive resolver accepts only a successful exact build
     `2ac04275daabe93c` with q10/q50/q90 calibrated routes and full schedule
     horizon. Missing inputs or multiple non-byte-identical candidates fail
     closed; identical duplicates resolve by one documented deterministic rule.
  2. The selector is process-free, deterministic and window-aware. For every
     candidate and frozen schedule it computes route/vehicle exposure for all
     three demand variants, records exact input SHA-256 digests, and applies
     versioned minimum-support and temporal-variation rules fixed in code and
     policy before artifact creation.
  3. Synthetic tests prove interval boundaries, route weighting, variant
     aggregation, zero-support rejection, variation ranking, divergent archive
     rejection, identical-duplicate resolution and deterministic tie-breaking.
  4. Candidate filtering excludes every directed v1-v5 case edge, its reverse,
     shared-junction neighbors and short/stub edges. The five selected edges are
     pairwise physically independent and meet every frozen demand rule without
     consulting simulation outcomes.
  5. The v6 selection records auditable raw pre-outcome features and reasons,
     exactly five unique cases and 75 unique schedules, with at least two cases
     preregistered as expected-discriminating solely under the versioned
     demand-support/variation rule.
  6. Policy, selection and manifest have canonical content keys; the manifest
     validates with production code, declares `frozen_before_outcomes`, binds
     the selection/policy keys and final executable source fingerprints, and
     transitively binds every demand input through the selection key.
  7. The freeze tool refuses overwrite, emits no partial final artifacts,
     reproduces byte-for-byte in a clean temporary destination, and does not
     inspect/create a run root. Tests prove source edits invalidate the frozen
     manifest.
  8. The current frozen-campaign identity points to v6 only after artifacts and
     fingerprints are final. Product adoption remains fail-closed: no gate or
     adoption certificate is created and Stage B behavior is unchanged.
  9. Documentation states that v5 honestly failed discrimination, v5 evidence
     is spent/opaque, v6 is frozen but unexecuted, and any future SUMO campaign
     needs a new exact Sol contract and user approval.
  10. All focused checks pass; Luna records exact commands and one terminal
      handoff without broadening scope.
- Focused checks:
  - `python3 -m json.tool validation/monthly_proxy_policy_v6.json`
  - `python3 -m json.tool validation/heldout_v6_selection.json`
  - `python3 -m json.tool validation/monthly_proxy_manifest_v6.json`
  - process-free exact archive/demand-input digest, edge-exclusion, physical-
    independence, support/variation, 5-case/75-schedule and canonical-identity
    verifier
  - production manifest/source-fingerprint validation and default-closed
    adoption check
  - clean-temporary-destination byte-for-byte freeze reproduction
  - `python3 -m pytest -q tests/test_heldout_selection.py
    tests/test_heldout_v6_freeze.py tests/test_heldout_gate.py
    tests/test_monthly_search.py tests/test_monthly_proxy.py
    tests/test_proxy_validation.py`
  - `git diff --check -- traffic_sim/simulation/heldout_selection.py
    tools/freeze_heldout_v6.py tests/test_heldout_selection.py
    tests/test_heldout_v6_freeze.py validation/monthly_proxy_policy_v6.json
    validation/heldout_v6_selection.json
    validation/monthly_proxy_manifest_v6.json
    traffic_sim/simulation/monthly_search.py tests/test_monthly_search.py
    ARCHITECTURE.md IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED` — this slice is process-free and read-only
  toward exact archived demand; it cannot run SUMO, inspect outcomes or
  activate/adopt/release anything.
- Terminal handoff conditions:
  - Hand off once when all acceptance criteria pass.
  - Stop early only for an approval/state conflict, divergent exact-demand
    archives, unavailable required immutable inputs, necessary architecture or
    artifact-contract change, material scope expansion, or three distinct
    serious failed approaches with the mandated blocker evidence and safe
    options.
<!-- COMPLETED_TASK_LUNA_V6_01_END -->

<!-- COMPLETED_TASK_LUNA_V5_02_START -->
## COMPLETED_TASK

### LUNA-V5-02 — Execute the untouched held-out v5 campaign once

- Task ID: `LUNA-V5-02`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — APPROVED execution; v5 gate FAIL`
- Delivery size: `EXTENDED`
- Scope: After exact approval, validate the immutable v5 manifest, policy,
  selection, adoption contract and eight bound source fingerprints; run focused
  process-free tests; verify SUMO and the exact existing archived-demand
  identity; then execute the five-case/75-schedule held-out campaign once with
  `seed_workers=3`. Resume only its task-created root after interruption and
  inspect only that root. Report honest complete pass/fail evidence. Do not
  generate or warm demand/horizons, inspect other outcomes, repair evidence,
  adopt a gate, activate Stage B, alter a release, deploy or publish.
- Completion outcome: one complete evidence package exists only at
  `runs/closure-proxy-validation/ce709248a2724fcb3bb326351d1b9cb4ae8b5e8c01f7698f80a7e28d75e9225f`;
  all five frozen cases are accounted for; the production gate status and
  bounded metrics are ready for Sol audit. A passing gate record may be
  created by the frozen runner, but no adoption certificate or product change
  follows.
- Internal checkpoints:
  1. Matching approval, canonical identities, eight source fingerprints,
     focused tests, root absence, SUMO and exact archived-demand preflight pass.
  2. All five cases complete, or the same task-owned root remains safely
     resumable without rerunning completed candidates/cases.
  3. Final outcome/report integrity and gate-record presence agree with the
     frozen evaluator before one terminal handoff.
- Allowed files and resources:
  - Read-only: `validation/monthly_proxy_manifest_v5.json`,
    `validation/monthly_proxy_policy_v5.json`,
    `validation/heldout_v5_selection.json`,
    `validation/monthly_gate_adoption_contract_v1.json`, the manifest's eight
    exact source-fingerprint paths, required SUMO executable/network inputs,
    and archived demand metadata/manifest/routes resolving exact demand
    identity `2ac04275daabe93c`.
  - Create/resume/inspect only:
    `runs/closure-proxy-validation/ce709248a2724fcb3bb326351d1b9cb4ae8b5e8c01f7698f80a7e28d75e9225f`.
  - Edit only `TASKS.md` and `AGENT_NOTES.md` for approval recording and the
    terminal handoff.
- Forbidden work:
  - Before exact approval is recorded: no executable/archive/root preflight,
    root existence check, SUMO invocation, outcome creation or inspection.
  - Never open, hash, stat, enumerate, count, summarize or inspect another
    campaign/report/outcome/run root.
  - Do not edit or repair any frozen manifest, policy, selection, adoption
    contract, source, test, demand, network, evidence or product file.
  - Do not generate or warm demand/horizons; create an adoption certificate;
    adopt/merge Stage B; expose UI/global-best claims; mutate the active
    release; push, tag, open a PR, deploy, release or publish.
  - Do not weaken or reinterpret provenance, recall, regret, failure-recall,
    discrimination, source-fingerprint, release or publication gates.
- Acceptance criteria:
  1. Luna acts only after Sol records a matching approval for this exact task
     and revision, content key, root, quoted user message and dates.
  2. Production-validate manifest key
     `ce709248a2724fcb3bb326351d1b9cb4ae8b5e8c01f7698f80a7e28d75e9225f`,
     exactly five cases/75 schedules, policy key
     `4d20c722e1ee29f33ee70e7eda9962c491e321370f30e6a215f72f2d11be8526`,
     selection key
     `5b5b91301fa5499732ad1a61bd9e71eb7d3c8485b2346dd490914999c170bd2a`,
     adoption-contract key
     `26a9ca0234ec981f7b79f6dadeac6a4c0b0a2be7496c97da700157d6cf156f40`,
     and all eight recorded source fingerprints.
  3. Run the focused process-free checks before executable preflight. Any
     failure or frozen drift is terminal; do not repair it in this task.
  4. Under recorded approval, require the exact root to be absent before the
     attempt; require SUMO/version resolution and archived demand identity
     `2ac04275daabe93c`, successful archive manifest, exact required route
     variants and horizon coverage for every frozen schedule. A pre-existing
     root or mismatch is terminal and must not be reused.
  5. Execute exactly `python3 run_monthly_proxy_validation.py --manifest
     validation/monthly_proxy_manifest_v5.json --seed-workers 3`. After process
     interruption, only the same task-created root may resume with that command;
     completed candidates/cases must not rerun.
  6. Inspect only the exact root. Require five distinct complete frozen cases,
     no missing cases, 75 exact candidates, matching manifest/proxy/shortlist/
     demand/network/SUMO/seed provenance and a final evaluator `report.json`.
  7. Accept honest `pass` or `fail`. Require `gate_record.json` exactly when
     the final report passes and forbid it when failed/incomplete. Never create
     an adoption certificate or copy evidence into the product path.
  8. Revalidate frozen hashes and focused checks; record bounded gate metrics,
     artifact inventory/digests and exact commands; transition once to
     `READY_FOR_SOL_REVIEW` and stop without inspecting elsewhere.
- Focused checks:
  - `python3 -m json.tool validation/monthly_proxy_manifest_v5.json`
  - `python3 -m json.tool validation/monthly_proxy_policy_v5.json`
  - `python3 -m json.tool validation/heldout_v5_selection.json`
  - `python3 -m json.tool validation/monthly_gate_adoption_contract_v1.json`
  - production manifest/content-key, contract-key, 5-case/75-schedule and
    eight-source-fingerprint verifier
  - `python3 -m pytest -q tests/test_heldout_gate.py
    tests/test_heldout_v5_freeze.py tests/test_monthly_proxy.py
    tests/test_proxy_validation.py tests/test_monthly_search.py`
  - exact SUMO/archive/root preflight after approval
  - exact campaign command from acceptance criterion 5
  - exact-root-only final integrity/gate inspection
  - `git diff --check -- TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate:
  - `REQUIRED — RECORDED`
  - Exact scope/key/root: one resumable `monthly_proxy_v5` held-out SUMO
    campaign; its canonical identity/source and focused process-free checks;
    required SUMO and exact archived-demand preflight; creation/execution with
    `seed_workers=3`; and inspection only of
    `runs/closure-proxy-validation/ce709248a2724fcb3bb326351d1b9cb4ae8b5e8c01f7698f80a7e28d75e9225f`
    at content key
    `ce709248a2724fcb3bb326351d1b9cb4ae8b5e8c01f7698f80a7e28d75e9225f`.
  - Exact user message:
    “I explicitly approve LUNA-V5-02 revision 1 to run the one-time resumable
    monthly_proxy_v5 held-out SUMO campaign at content key
    ce709248a2724fcb3bb326351d1b9cb4ae8b5e8c01f7698f80a7e28d75e9225f
    and artifact root
    runs/closure-proxy-validation/ce709248a2724fcb3bb326351d1b9cb4ae8b5e8c01f7698f80a7e28d75e9225f,
    including canonical manifest/policy/selection/adoption-contract and
    source-fingerprint checks, focused process-free tests, required SUMO and
    exact archived-demand preflight for demand_build_id 2ac04275daabe93c,
    creation and execution with seed_workers=3, and inspection only of that
    task-created root. No demand or horizon generation/warming, other
    report/outcome inspection, evidence repair, adoption certificate, Stage B
    activation, release mutation, deployment or publication is approved.”
  - User-message date: `2026-07-27`
  - Sol recorder/date: `Sol High / 2026-07-27`
- Terminal handoff conditions:
  - While approval is absent, remain `BLOCKED`; no Luna action is legal.
  - After approval, hand off once after honest complete pass/fail evidence or
    on an approval/identity/pre-existing-root/frozen-drift/executable/archive/
    provenance/integrity blocker. Do not broaden, repair frozen inputs, inspect
    elsewhere or attempt a distinct campaign approach.
<!-- COMPLETED_TASK_LUNA_V5_02_END -->

<!-- COMPLETED_TASK_LUNA_V5_01_START -->
## ACTIVE_TASK

### LUNA-V5-01 — Harden gate adoption before freezing fresh held-out evidence

- Task ID: `LUNA-V5-01`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — APPROVED`
- Delivery size: `EXTENDED`
- Scope: Remove the rejected v4 tracked gate candidate without reading any
  preserved outcome. Build a strict two-artifact adoption seam in which a
  post-review certificate binds the complete gate-record bytes, frozen
  manifest identity and claim boundary; absence or alteration of either
  artifact fails closed. Then freeze a new five-case v5 held-out design,
  selected without outcome knowledge and disjoint from v1-v4, after the
  hardened loader is final so its exact source hash is included. Correct
  rejected-v4 documentation. Do not run SUMO, inspect outcomes, activate
  Stage B, tune gates, mutate releases, deploy or publish.
- Completion outcome: the product remains in bounded-exhaustive fail-closed
  mode; a future audited gate can be adopted without changing frozen
  executable sources; and one canonical untouched v5 policy, selection and
  manifest package is ready for a separately approved campaign. V4 remains
  immutable historical evidence and is explicitly non-adoptable against the
  hardened source. No gate record, adoption certificate or outcome for v5
  exists.
- Internal checkpoints:
  1. Rejected v4 activation is absent and the default product path is closed;
     the authenticated gate/certificate loader rejects every independent
     record or certificate mutation.
  2. The v5 policy, deterministic selection and manifest freeze exactly five
     new cases and 75 schedules using only pre-outcome inputs; all source
     fingerprints are captured after loader hardening.
  3. Canonical identities, disjointness, process-free regression checks,
     documentation and no-outcome/no-activation boundaries all pass.
- Allowed files and resources:
  - Remove only the rejected product candidate
    `validation/monthly_proxy_v4_gate.json`; do not read or inspect its
    preserved source.
  - Edit `traffic_sim/simulation/monthly_search.py`,
    `run_monthly_proxy_validation.py`, `serve.py`,
    `tests/test_monthly_search.py`, `tests/test_serve.py`,
    `tests/test_proxy_validation.py`, `tests/test_heldout_v4_freeze.py`,
    `ARCHITECTURE.md`, `IMPROVEMENT_PLAN.md`, `TASKS.md`, `AGENT_NOTES.md`.
  - Create, if cohesive and minimal,
    `traffic_sim/simulation/heldout_gate.py`,
    `tests/test_heldout_gate.py`, `tests/test_heldout_v5_freeze.py`,
    `validation/monthly_gate_adoption_contract_v1.json`,
    `validation/monthly_proxy_policy_v5.json`,
    `validation/heldout_v5_selection.json`,
    `validation/monthly_proxy_manifest_v5.json`, and one deterministic
    process-free freeze helper under `tools/`.
  - Read only tracked v1-v4 policies/selections/manifests, their declared
    source-fingerprint files, and tracked network/forecast/demand metadata
    needed to select new cases. Never read any outcome/report/run root.
- Forbidden work:
  - Do not open, parse, hash, stat, enumerate, copy, compare or otherwise
    inspect the rejected v4 gate candidate before removing it, its preserved
    source, or any report/outcome/campaign/run directory.
  - Do not run/resume SUMO, execute the monthly campaign, generate/warm demand
    or horizons, create outcome roots, replay old outcomes, or treat diagnostic
    evidence as release evidence.
  - Do not change proxy weights, shortlist behavior, gate thresholds,
    practical-equivalence semantics, failure-recall semantics, production
    schedules, frozen v1-v4 inputs or their source fingerprints.
  - Do not create a v5 gate record or adoption certificate; do not activate
    proxy screening, Stage B, UI/global-best claims or any release.
  - Do not mutate the active release, push, tag, open a PR, deploy, release or
    publish.
- Acceptance criteria:
  1. Preserve the user-directed correction: LUNA-V4-04 remains concluded and
     rejected. Remove only its untracked/tracked product gate candidate using
     a patch-level deletion; source evidence remains untouched and uninspected.
  2. Define a strict versioned adoption-certificate schema with an exact key
     set and canonical content identity. It binds the gate record's SHA-256
     and byte length, manifest path/content key/campaign version, required case
     count, proxy/shortlist identities and bounded claim scope.
  3. The production loader requires both regular non-symlink artifacts,
     production-validates the named frozen manifest, verifies certificate
     identity and exact gate bytes, rejects unknown/missing fields, and then
     validates the gate record's strict schema, identities, case completeness,
     thresholds, checks and claim flags. Any error returns `None`.
  4. Prove process-free that a changed metric, threshold, gate check, claim
     flag, unknown field or any byte in the record fails against its unchanged
     certificate; and that a changed/repointed/incomplete certificate, earlier
     campaign, altered manifest or absent artifact also fails closed.
  5. With no default gate/certificate present, require
     `load_passing_heldout_gate()` to return `None`, `serve.py` to select
     bounded-exhaustive with its hard cap, and all proxy Stage B/UI/global-best
     claims to remain closed. Preserve existing bounded-exhaustive semantics.
  6. Freeze v5 before outcomes with the unchanged gate policy: exactly five
     distinct cases and 15 canonical schedules per case, deterministic
     pre-outcome selection, declared road/date/topology strata, and directed
     edges disjoint from every v1-v4 release/diagnostic held-out edge.
  7. Production-validate the v5 manifest; require unique policy, selection and
     manifest content keys, `frozen_before_outcomes=true`, no outcome path or
     outcome-derived field, and source fingerprints covering every executable
     gate/selection/evaluator input after all code changes are final.
  8. Never update a v4 source fingerprint. Convert its regression to prove the
     recorded enforcement hash remains frozen, the hardened current loader no
     longer matches it, and no default loader/certificate path can adopt v4.
  9. Update architecture/improvement status to say v4 audit passed but adoption
     was rejected for whole-record integrity; v5 is frozen but unexecuted and
     unapproved. Retain the negative-Spearman and failure-recall caveats.
  10. Run all focused process-free checks, JSON/canonical/source-fingerprint
     verification, `git diff --check` and status; hand off once without SUMO,
     outcome access, product activation, release mutation or publication.
- Focused checks:
  - `python3 -m json.tool validation/monthly_gate_adoption_contract_v1.json`
  - `python3 -m json.tool validation/monthly_proxy_policy_v5.json`
  - `python3 -m json.tool validation/heldout_v5_selection.json`
  - `python3 -m json.tool validation/monthly_proxy_manifest_v5.json`
  - production v5 manifest/content-key/source-fingerprint verifier
  - exact v1-v5 directed-edge disjointness and 5-case/75-schedule verifier
  - `python3 -m pytest -q tests/test_heldout_gate.py
    tests/test_heldout_v5_freeze.py tests/test_heldout_v4_freeze.py
    tests/test_monthly_search.py tests/test_proxy_validation.py
    tests/test_serve.py`
  - explicit default-closed server orchestration and record/certificate
    mutation matrix within those tests
  - `git diff --check -- traffic_sim/simulation/heldout_gate.py
    traffic_sim/simulation/monthly_search.py run_monthly_proxy_validation.py
    serve.py tests/test_heldout_gate.py tests/test_heldout_v5_freeze.py
    tests/test_heldout_v4_freeze.py tests/test_monthly_search.py
    tests/test_proxy_validation.py tests/test_serve.py
    validation/monthly_gate_adoption_contract_v1.json
    validation/monthly_proxy_policy_v5.json
    validation/heldout_v5_selection.json
    validation/monthly_proxy_manifest_v5.json ARCHITECTURE.md
    IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate: `NOT_REQUIRED`; this task is process-free, removes a rejected
  product candidate, creates only pre-outcome design/contracts and keeps every
  product/release claim closed. The user's correction directive dated
  `2026-07-27` authorizes concluding LUNA-V4-04 as rejected and planning this
  path; it is not SUMO, outcome-inspection, adoption or release approval.
- Terminal handoff conditions:
  - Hand off after all three checkpoints and acceptance criteria pass.
  - Stop with exact evidence on any need to inspect outcomes, run/generate
    SUMO or demand, change frozen gate semantics, activate product claims,
    alter a prior frozen contract, or expand the architecture beyond the
    two-artifact adoption seam.

<!-- COMPLETED_TASK_LUNA_V5_01_END -->

<!-- REJECTED_TASK_LUNA_V4_04_START -->
## ACTIVE_TASK

### LUNA-V4-04 — Adopt the audited v4 gate through the fail-closed product seam

- Task ID: `LUNA-V4-04`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — rejected by Sol review; user-directed state correction`
- Delivery size: `STANDARD`
- Scope: After fresh exact approval, read only the audited v4
  `gate_record.json`, require its reviewed SHA-256 and frozen campaign
  identity, and copy it byte-for-byte to the tracked v4 gate path. Prove the
  default loader accepts only this complete v4 record, the monthly API selects
  proxy screening only behind that gate, and UI/global-best claims remain
  limited to SUMO-verified schedules within the enumerated search space.
  Correct stale v2/v4 documentation and tests. Do not rerun SUMO, inspect other
  evidence, tune the proxy, change thresholds/policy, deploy or publish.
- Completion outcome: the audited v4 passing record is the tracked,
  fail-closed product gate; proxy-screened monthly searches can use the
  existing Stage B path and bounded claim wording, while missing, altered,
  earlier-campaign or incomplete records close the gate. No runtime campaign,
  release mutation, deployment or publication occurs.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - After exact approval, read/stat/hash only
    `runs/closure-proxy-validation/1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6/gate_record.json`
  - Read frozen identity/policy inputs:
    `validation/monthly_proxy_manifest_v4.json`,
    `validation/monthly_proxy_policy_v4.json`,
    `validation/heldout_v4_selection.json`
  - Create `validation/monthly_proxy_v4_gate.json`
  - Edit only as needed for exact gate binding, process-free coverage and
    accurate status/wording: `traffic_sim/simulation/monthly_search.py`,
    `serve.py`, `web/app.js`, `tests/test_monthly_search.py`,
    `tests/test_serve.py`, `tests/test_heldout_v4_freeze.py`,
    `ARCHITECTURE.md`, `IMPROVEMENT_PLAN.md`, `TASKS.md`, `AGENT_NOTES.md`
- Forbidden work:
  - Before exact approval, do not check existence, stat, hash, open, parse or
    copy the preserved gate record; do not implement product activation.
  - Never inspect any other member of the approved root or any other report,
    outcome, campaign root or unrelated run artifact.
  - Do not run/resume SUMO, regenerate/repair evidence, warm demand/horizons,
    tune proxy weights, alter shortlist policy, thresholds, manifests,
    selections, source fingerprints, schedules or production evidence.
  - Do not broaden claims beyond SUMO-verified schedules in the enumerated
    search space; do not claim reliable full ranking, safety, permission or
    TA-plan compliance.
  - Do not mutate the active release, merge unrelated Stage B work, push, tag,
    open a PR, deploy, release or publish.
- Acceptance criteria:
  1. Luna acts only after Sol records an exact user message authorizing this
     task/revision, exact content key/root member, tracked destination,
     process-free checks and bounded product Stage B activation.
  2. Require the sole preserved source member to be a regular non-symlink file
     resolving inside the exact approved root, with SHA-256
     `9ba2fa10a96d0e9b25dda5d2e9130032688ba4786a659f5d795c6e4f43759eaf`.
     Do not enumerate or inspect siblings.
  3. Parse and production-validate the frozen v4 manifest. Require content key
     `1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6`,
     campaign label, five required/completed cases, current proxy/shortlist
     identities, `gate_status=pass`, and both claim flags true in the source
     record. Any mismatch is terminal; do not rewrite or normalize it.
  4. Create `validation/monthly_proxy_v4_gate.json` byte-for-byte from the
     approved source; require identical SHA-256 and canonical JSON identity.
  5. Prove the default production loader accepts the tracked v4 record and
     rejects absence, corruption, tampering, older campaign labels/manifests,
     incomplete cases and changed frozen manifest identity.
  6. Prove process-free API orchestration selects proxy screening only with the
     passing v4 gate and otherwise uses bounded exhaustive fallback; verify the
     result/UI wording permits only the existing enumerated-space,
     SUMO-verified shortlist claim and retains all policy disclaimers.
  7. Update stale v2/no-gate architecture and improvement-plan statements with
     the exact v4 adoption, audited metrics and negative-Spearman limitation;
     do not erase historical results or describe the proxy as a full ranker.
  8. Run the focused checks, `git diff --check` and ordinary status; hand off
     for Sol review without SUMO, campaign mutation, active-release mutation,
     deployment or publication.
- Focused checks:
  - exact single-member regular-file/resolution/SHA-256 verifier after approval
  - production v4 manifest and tracked gate-record identity verifier
  - `python3 -m json.tool validation/monthly_proxy_v4_gate.json`
  - `python3 -m pytest -q tests/test_heldout_v4_freeze.py
    tests/test_monthly_search.py tests/test_serve.py`
  - process-free server orchestration/claim-boundary assertions within those
    focused tests
  - `git diff --check -- validation/monthly_proxy_v4_gate.json
    traffic_sim/simulation/monthly_search.py serve.py web/app.js
    tests/test_monthly_search.py tests/test_serve.py
    tests/test_heldout_v4_freeze.py ARCHITECTURE.md IMPROVEMENT_PLAN.md
    TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate:
  - `REQUIRED — RECORDED`
  - Required exact scope/key/root member: one bounded adoption of the already
    audited gate record at content key
    `1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6`,
    reading only
    `runs/closure-proxy-validation/1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6/gate_record.json`,
    copying it byte-for-byte to
    `validation/monthly_proxy_v4_gate.json`, running the named process-free
    checks, and activating only the existing fail-closed proxy-screened monthly
    product path and bounded claim semantics.
  - Exact user message:
    “I explicitly approve LUNA-V4-04 revision 1 to read only the audited
    gate_record.json at content key
    1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6
    and root
    runs/closure-proxy-validation/1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6,
    copy it byte-for-byte to validation/monthly_proxy_v4_gate.json, run the
    named process-free checks, and activate the existing fail-closed
    proxy-screened monthly Stage B path with claims limited to SUMO-verified
    schedules within the enumerated search space. No SUMO, other evidence
    inspection, rerun, repair, proxy tuning, policy or threshold change,
    active-release mutation, deployment, release, or publication is approved.”
  - User-message date: `2026-07-27`
  - Sol recorder/date: `Sol High / 2026-07-27`
- Terminal handoff conditions:
  - Execute only while this exact approval record and `READY_FOR_LUNA` state
    remain consistent.
  - After approval, hand off once on complete acceptance or an
    authority/source-hash/identity/canonical-copy/test/claim-boundary blocker.
    Do not repair evidence, broaden scope or inspect another artifact.

<!-- REJECTED_TASK_LUNA_V4_04_END -->

<!-- COMPLETED_TASK_LUNA_V4_03_START -->
## ACTIVE_TASK

### LUNA-V4-03 — Audit the preserved v4 evidence without rerunning

- Task ID: `LUNA-V4-03`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved the inspection-only audit`
- Delivery size: `NARROW`
- Scope: After exact approval, inspect only the preserved v4 root at the
  frozen content key. Enumerate, parse and hash its members; validate complete
  case/schedule identity and provenance; recompute the production evaluator
  in memory from the frozen manifest and preserved outcomes; and verify the
  stored report and gate-record presence against that recomputation. Record an
  honest pass, fail, incomplete or corrupt disposition for Sol. Do not run or
  resume SUMO, mutate evidence, inspect another root, change code, expose the
  result, merge product Stage B, adopt a policy, release or publish.
- Completion outcome: a bounded review package establishes whether the
  preserved exact-root evidence is complete, identity-bound and internally
  consistent and states its production gate disposition. The preserved root
  remains byte-for-byte unchanged; no adoption or publication follows.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files and resources:
  - Read-only tracked inputs: `validation/monthly_proxy_manifest_v4.json`,
    `validation/monthly_proxy_policy_v4.json`,
    `validation/heldout_v4_selection.json`, the manifest's seven
    source-fingerprint paths, and production evaluator imports
  - Read-only inspect/hash/parse only:
    `runs/closure-proxy-validation/1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6`
  - Edit only `TASKS.md` and `AGENT_NOTES.md` for terminal handoff
- Forbidden work:
  - Before exact approval: no root existence check, enumeration, stat, hash,
    open, parse, summary or evaluator run using preserved outcome content.
  - Never inspect another report, outcome directory, campaign root or
    unrelated run artifact.
  - Do not create, delete, rename, rewrite, repair or normalize any root member;
    do not run/resume SUMO or regenerate any evidence.
  - Do not edit frozen inputs, bound sources, tests, product/UI/release files or
    improvement priorities; do not merge/adopt Stage B, warm demand/horizons,
    alter the active release, push, tag, open a PR, deploy or publish.
  - Do not weaken or reinterpret recall, regret, failure-recall,
    discrimination, health, provenance, release or publication gates.
- Acceptance criteria:
  1. Luna acts only after an approval record legally moves this exact task and
     revision to `READY_FOR_LUNA` with the exact content key/root/message/date.
  2. Snapshot SHA-256 hashes for every exact-root member before inspection and
     again after all checks; require identical path/hash sets. Never traverse
     outside the exact root.
  3. Production-validate the frozen manifest and require its content key,
     policy key, selection key, five case IDs, 75 frozen schedule IDs and all
     seven source fingerprints to match the active contract.
  4. Require a parseable complete `outcomes.json` bound to the exact manifest,
     proxy and shortlist identities, with exactly five distinct frozen cases,
     no missing case, exactly the frozen schedule IDs once per case, and
     complete candidate provenance.
  5. Recompute `evaluate_validation_set(manifest, outcomes)` in memory. Compare
     all gate status, checks, thresholds, metrics, case reports, claim flags and
     identity fields against the stored `report.json`; any mismatch is corrupt
     evidence, not a value to repair.
  6. Require `gate_record.json` exactly if the recomputed final report passes;
     when present, recompute `gate_record_for(...)` and require exact canonical
     equality. Require no gate record for fail or incomplete evidence.
  7. Record bounded totals, canonical file hashes, final gate status, each
     frozen gate metric/check, and any integrity defect in the terminal
     handoff. Accept honest pass/fail/incomplete/corrupt as the audit outcome.
  8. Recheck byte identity, `git diff --check`, and ordinary status; transition
     to `READY_FOR_SOL_REVIEW` and stop without mutation, adoption or
     publication.
- Focused checks:
  - production manifest/content-key and seven-source-fingerprint verifier
  - exact-root-only recursive path/SHA-256 snapshot
  - exact-root-only JSON/schema/identity/provenance completeness inspector
  - in-memory production evaluator recomputation and stored-report comparison
  - in-memory gate-record recomputation/presence check
  - post-check exact-root path/SHA-256 snapshot equality
  - `git diff --check -- TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate:
  - `REQUIRED — RECORDED`
  - Exact scope/key/root: one inspection-only audit, including enumeration,
    stat, hashing, parsing, production-evaluator recomputation and gate-record
    consistency checks, confined to content key
    `1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6`
    and preserved artifact root
    `runs/closure-proxy-validation/1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6`.
  - Exact user message:
    “I explicitly approve one inspection-only audit of the preserved
    monthly_proxy_v4 evidence at content key
    1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6
    and artifact root
    runs/closure-proxy-validation/1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6,
    including enumeration, stat, hashing, parsing, canonical identity and
    provenance validation, production-evaluator recomputation, and gate-record
    consistency checks within that root only. No SUMO, rerun, resume, repair,
    mutation, other report/outcome inspection, product Stage B, adoption,
    release or publication is approved.”
  - User-message date: `2026-07-26`
  - Sol recorder/date: `Sol High / 2026-07-26`
- Terminal handoff conditions:
  - Remain `BLOCKED` with no Luna action until exact approval is recorded.
  - After approval, hand off once with the honest audit disposition or an
    authority/access/identity/provenance/integrity blocker. Do not mutate,
    repair, broaden, execute SUMO or inspect another root.

<!-- COMPLETED_TASK_LUNA_V4_03_END -->

<!-- COMPLETED_TASK_LUNA_V4_02_START -->
## ACTIVE_TASK

### LUNA-V4-02 — Execute the frozen held-out v4 campaign once

- Task ID: `LUNA-V4-02`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol approved fail-closed stop; campaign not executed`
- Delivery size: `EXTENDED`
- Scope: After exact approval, validate the immutable v4 contract and bound
  sources, run focused process-free tests, verify the exact archived demand and
  SUMO executable, then execute the five-case held-out campaign once with
  `seed_workers=3`. The attempt may resume only its own task-created root after
  interruption. Inspect only that root to report complete gate evidence,
  whether pass or fail. Do not inspect any other report/outcome, alter frozen
  inputs, warm demand, expose results in the UI, merge product Stage B, adopt a
  policy, push, release, deploy or publish.
- Completion outcome: one complete, content-key-bound v4 evidence package
  exists at the exact root; all five frozen cases are accounted for; the gate
  status and bounded metrics are recorded honestly for Sol review; a passing
  gate record exists only if every production gate passes. No adoption or
  publication follows from this task.
- Internal checkpoints:
  1. Approval, immutable identity, source fingerprints, root-absence,
     executable/archive preflight and focused process-free tests all pass.
  2. Each of the five frozen cases completes or the same task-owned attempt is
     safely resumable; completed cases are never rerun.
  3. Final outcomes/report integrity and gate-record presence/absence agree
     with the frozen production evaluator before terminal handoff.
- Allowed files and resources:
  - Read-only: `validation/monthly_proxy_manifest_v4.json`,
    `validation/monthly_proxy_policy_v4.json`,
    `validation/heldout_v4_selection.json`, the manifest's seven exact
    `source_fingerprints` paths, required SUMO executable/network inputs, and
    archived demand metadata/manifest/routes resolving exact demand identity
    `2ac04275daabe93c`
  - Create/resume/inspect only:
    `runs/closure-proxy-validation/1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6`
  - Edit only `TASKS.md` and `AGENT_NOTES.md` for the terminal handoff
- Forbidden work:
  - Before exact approval is recorded: no preflight, root existence check,
    SUMO invocation, outcome creation or outcome inspection.
  - Never open, hash, stat, enumerate, count, summarize or inspect any other
    campaign report, outcome directory or unrelated run artifact.
  - Do not edit the manifest, policy, selection, bound sources, tests, demand,
    network, production code, UI, release artifacts or improvement priorities.
  - Do not generate/warm demand or horizons; merge/adopt product Stage B;
    alter the active release; push, tag, open a PR, release, deploy or publish.
  - Do not weaken or reinterpret recall, regret, failure-recall,
    discrimination, health, provenance, release or publication gates.
- Acceptance criteria:
  1. Luna acts only after WORKFLOW_CONTROL is legally moved to
     `READY_FOR_LUNA` by a Sol approval record matching this task/revision,
     exact content key, exact root, exact quoted user message and dates.
  2. Validate the manifest with the production validator; require content key
     `1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6`,
     five exact cases, policy key
     `65798d8f1a8f1ec69c6bcaae5947c1ddcbfe9c9335b1d5ce4a28c0ff06153daa`,
     selection key
     `886aca871332d41ee5a4d2ed02bdf3ca9106164a62c766a1ef1b2885b705474d`,
     and all seven recorded source fingerprints.
  3. Run the focused process-free test set before executable preflight. Any
     failure or frozen-file drift is terminal; do not repair in this task.
  4. Under the recorded approval, require the exact outcome root to be absent
     before the attempt, the SUMO executable/version to resolve, and archived
     demand identity `2ac04275daabe93c` plus its required variants to match.
     Any pre-existing root or identity mismatch is terminal and is not reused.
  5. Execute exactly
     `python3 run_monthly_proxy_validation.py --manifest
     validation/monthly_proxy_manifest_v4.json --seed-workers 3`. A process
     interruption may resume only this task-created root with the same command;
     complete saved cases must not rerun.
  6. Inspect only the exact root. Require five complete distinct frozen cases,
     no missing cases, manifest/policy/selection/proxy identities matching the
     contract, complete per-candidate provenance, and a final `report.json`
     produced by the frozen evaluator.
  7. Accept either honest `pass` or `fail` as the campaign result. Require
     `gate_record.json` exactly when the final report passes and forbid it when
     the report fails/incompletes. Never synthesize or edit evidence.
  8. Revalidate frozen hashes and focused contract tests, record bounded gate
     metrics and exact commands in the terminal handoff, transition to
     `READY_FOR_SOL_REVIEW`, and stop without adoption or publication.
- Focused checks:
  - `python3 -m json.tool validation/monthly_proxy_manifest_v4.json`
  - `python3 -m json.tool validation/monthly_proxy_policy_v4.json`
  - `python3 -m json.tool validation/heldout_v4_selection.json`
  - production manifest/content-key and seven-source-fingerprint verifier
  - `python3 -m pytest -q tests/test_heldout_v4_freeze.py
    tests/test_monthly_proxy.py tests/test_proxy_validation.py
    tests/test_monthly_search.py`
  - exact SUMO/archive/root preflight after approval
  - exact campaign command in acceptance criterion 5
  - exact-root-only final integrity/gate inspection
  - `git diff --check -- TASKS.md AGENT_NOTES.md`
  - `git status --short`
- Approval gate:
  - `REQUIRED — RECORDED`
  - Exact scope/key/root: one resumable `monthly_proxy_v4` held-out SUMO
    campaign, its canonical contract/source checks, focused process-free tests,
    required SUMO and archived-demand preflight, creation/execution, and
    inspection only of
    `runs/closure-proxy-validation/1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6`
    at content key
    `1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6`.
  - Exact user message:
    “I explicitly approve the one-time resumable monthly_proxy_v4 held-out
    SUMO campaign, its canonical contract and source-fingerprint checks,
    focused process-free tests, required SUMO and exact archived-demand
    preflight, creation and execution with seed_workers=3, and inspection of
    only its own outcome at content key
    1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6
    and artifact root
    runs/closure-proxy-validation/1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6.
    No other report or outcome inspection is approved.”
  - User-message date: `2026-07-26`
  - Sol recorder/date: `Sol High / 2026-07-26`
- Terminal handoff conditions:
  - While approval is absent, remain `BLOCKED`; no Luna action is legal.
  - After approval, hand off once after honest complete pass/fail evidence or
    on an authority/identity/pre-existing-root/frozen-drift/executable/archive/
    provenance/integrity blocker. Do not broaden, repair frozen inputs, inspect
    elsewhere, or attempt a distinct campaign approach.

<!-- COMPLETED_TASK_LUNA_V4_02_END -->

<!-- COMPLETED_TASK_LUNA_REL_03_START -->
## ACTIVE_TASK

### LUNA-REL-03 — Land the approved release candidate on local main

- Task ID: `LUNA-REL-03`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved; non-executable`
- Delivery size: `STANDARD`
- Scope: Preserve the Sol-approved LUNA-REL-02 candidate byte-for-byte. Record
  this task's terminal workflow handoff in one local commit on
  `integration/luna-rel-02`, then switch to `main` and fast-forward it to the
  branch. Verify the exact linear graph, immutable v2 hashes, synthetic ignore
  probes and clean ordinary status. This is repository landing only: do not
  edit product or contract content, inspect excluded material, run tests that
  can touch excluded paths, merge product Stage B, push, tag, release, deploy,
  warm demand, run SUMO or publish anything.
- Completion outcome: local `main` and `integration/luna-rel-02` point to the
  same reviewed tip, exactly four linear commits ahead of approved base
  `b99e9e7e41ca7919dd5058ee66508d9548f475ff`; the final commit records the
  terminal Luna handoff, all 29 immutable hashes match v2, and ordinary status
  is clean.
- Internal checkpoints: `NOT_APPLICABLE`
- Allowed files:
  - `TASKS.md`, `AGENT_NOTES.md` (terminal workflow/handoff only)
  - Local branches `integration/luna-rel-02` and `main`, the index, and exactly
    one new local commit named `Land approved release candidate locally`
  - Read-only verification of `validation/release_candidate_boundary_v2.json`,
    its 29 immutable allowlisted paths, `.gitignore`, and Git metadata
- Forbidden work:
  - Do not read, parse, hash, stat, enumerate, count, summarize, diff, stage or
    inspect any member matching v2's six opaque patterns; boundary checks use
    only the patterns and synthetic nonexistent probes.
  - Do not edit product, test, immutable candidate, `.gitignore`, `AGENTS.md`,
    `IMPROVEMENT_PLAN.md`, or v2 content.
  - Do not use `git add -A`, `git add .`, staging globs, amend, rebase, reset,
    cherry-pick, non-fast-forward merge, branch deletion or force movement.
  - Do not push, tag, open a PR, release, deploy, publish, merge product Stage
    B, warm demand/horizons, run SUMO/TraCI/libsumo or start a live API job.
  - Do not weaken or reinterpret any evidence, health, integrity, latency,
    release or publication gate.
- Acceptance criteria:
  1. Revalidate unique markers and matching LUNA-REL-03 revision 1 control
     fields. Require current branch `integration/luna-rel-02`, current `HEAD`
     `ba3aea2d999763da4170a0880374facd3357f957`, local `main` at approved base,
     and ordinary status containing only `TASKS.md` and `AGENT_NOTES.md`.
  2. Require `ba3aea2` to be exactly three non-merge commits ahead of the base
     with the already-approved subjects and exact 11/18/6 path sets.
  3. Recompute exactly the 29 v2 allowlisted hashes and require 29/29 matches.
     Verify all six ignore rules only with synthetic nonexistent probes.
  4. Write the legal terminal `READY_FOR_SOL_REVIEW` triple and bounded Luna
     handoff without editing `ACTIVE_TASK`; stage only `TASKS.md` and
     `AGENT_NOTES.md` using `git add -- TASKS.md AGENT_NOTES.md`.
  5. Require the cached path set to be exactly those two files and
     `git diff --cached --check` to pass. Commit once with exact subject
     `Land approved release candidate locally`; do not amend or retry
     destructively.
  6. Switch to `main` and run only
     `git merge --ff-only integration/luna-rel-02`. This repository
     fast-forward is not approval to activate or merge product Stage B.
  7. Require `main` and `integration/luna-rel-02` to equal the same final
     commit, four non-merge commits ahead of the approved base, with the first
     three commits unchanged and the exact fourth subject/path set.
  8. Reverify 29/29 hashes, scoped diff hygiene and empty ordinary status.
     Do not enumerate ignored content. Stop without remote or runtime action.
- Focused checks:
  - `python3 -m json.tool validation/release_candidate_boundary_v2.json`
  - v2 29-file SHA-256 verifier
  - `git check-ignore -v <six synthetic nonexistent probes>`
  - `git diff --check -- TASKS.md AGENT_NOTES.md`
  - `git diff --cached --check`
  - `git diff --cached --name-only`
  - `git rev-list --count <base>..HEAD`
  - `git rev-list --merges <base>..HEAD`
  - `git log --reverse --format=%s <base>..HEAD`
  - `git diff-tree --no-commit-id --name-only -r <each commit>`
  - `git rev-parse main integration/luna-rel-02`
  - `git status --short`
- Approval gate:
  - `NOT_REQUIRED`
  - Sol review already approved the exact candidate. This task permits only a
    local fast-forward and one workflow commit; it grants no product Stage B,
    remote, release, publication, runtime or excluded-evidence authority.
  - The user's `2026-07-25` direction still forbids report/outcome inspection.
- Terminal handoff conditions:
  - Hand off once in `READY_FOR_SOL_REVIEW` only after the exact commit,
    fast-forward, graph/hash checks and clean ordinary status all pass.
  - Any marker, branch, base, hash, path, commit, fast-forward or status
    mismatch; forbidden access; need to edit non-workflow content; or broader
    authority need is terminal. Preserve state and report the exact blocker;
    do not amend, reset, force, delete, retry destructively or broaden.

<!-- COMPLETED_TASK_LUNA_REL_03_END -->

<!-- COMPLETED_TASK_LUNA_REL_02_START -->
## ACTIVE_TASK

### LUNA-REL-02 — Integrate the bounded release candidate on a local branch

- Task ID: `LUNA-REL-02`
- Revision: `1`
- Owner: `Luna High`
- Status: `CONCLUDED — Sol review approved; non-executable`
- Delivery size: `STANDARD`
- Scope: Create local branch `integration/luna-rel-02` from approved base
  `b99e9e7e41ca7919dd5058ee66508d9548f475ff`. Preserve the 29 immutable v2
  candidates byte-for-byte, add only the missing ignore rules for opaque local
  evidence, rerun portable guarded focused verification, and commit the
  approved source/contracts, performance tooling, boundary/documentation and
  terminal workflow handoff in exactly three coherent commits. Use explicit
  path staging only. Do not inspect excluded material, modify product behavior,
  amend/rebase, push, tag, release, deploy, merge Stage B, warm demand, run
  SUMO or publish anything.
- Completion outcome: the named local branch is exactly three reviewed commits
  ahead of the approved base, contains the 29 hash-matching candidates, v2
  boundary, four workflow documents and protective ignore rules, has no
  visible unstaged/staged task work, and is ready for Sol review without any
  remote or runtime action.
- Internal checkpoints: `NOT_APPLICABLE`

Allowed files and Git state:

- `.gitignore` (add only the three missing validation-evidence rules below)
- The 29 `immutable_release_candidates` in
  `validation/release_candidate_boundary_v2.json` (stage/commit only; no edits)
- `AGENTS.md`, `IMPROVEMENT_PLAN.md` (stage/commit only; no edits)
- `validation/release_candidate_boundary_v2.json` (stage/commit only; no edits)
- `TASKS.md`, `AGENT_NOTES.md` (terminal workflow/handoff update, then commit)
- Local Git branch `integration/luna-rel-02`, index and exactly three new local
  commit objects rooted at
  `b99e9e7e41ca7919dd5058ee66508d9548f475ff`

Exact `.gitignore` additions:

```text
# Local measured campaign evidence — preserved on disk, never committed
validation/scenario_phase_profile_report_*.json
validation/*_outcome/
validation/online_latency_baseline_v1/
```

Commit 1 — `Integrate monthly validation V4`:

- `run_monthly_closure_search.py`
- `run_monthly_proxy_validation.py`
- `traffic_sim/simulation/monthly_proxy.py`
- `traffic_sim/simulation/monthly_search.py`
- `traffic_sim/simulation/proxy_validation.py`
- `tests/test_monthly_search.py`
- `tests/test_proxy_validation.py`
- `tests/test_heldout_v4_freeze.py`
- `validation/heldout_v4_selection.json`
- `validation/monthly_proxy_policy_v4.json`
- `validation/monthly_proxy_manifest_v4.json`

Commit 2 — `Integrate guarded performance tooling`:

- `run_scenario.py`
- `tools/benchmark_speed.py`
- `tools/benchmark_online_latency.py`
- `tools/benchmark_persistent_sumo.py`
- `tests/test_scenario.py`
- `tests/test_scenario_timing.py`
- `tests/test_benchmark_speed.py`
- `tests/test_benchmark_online_latency.py`
- `tests/test_benchmark_persistent_sumo.py`
- `validation/online_latency_benchmark_v1.json`
- `validation/scenario_phase_profile_campaign_v1.json` through
  `validation/scenario_phase_profile_campaign_v6.json`
- `validation/persistent_sumo_campaign_v1.json`
- `validation/persistent_sumo_campaign_v2.json`

Commit 3 — `Record release boundary and repository guards`:

- `.gitignore`
- `AGENTS.md`
- `IMPROVEMENT_PLAN.md`
- `validation/release_candidate_boundary_v2.json`
- `TASKS.md`
- `AGENT_NOTES.md`

Forbidden work:

- Do not read, parse, hash, stat, enumerate, count, summarize, diff, stage or
  otherwise inspect any member matching the six opaque patterns in v2. Use
  only the patterns and synthetic nonexistent probe paths.
- Do not use `git add -A`, `git add .`, a staging glob, or any command that
  could capture a path not explicitly listed in the active task.
- Do not edit any of the 29 immutable candidates, `AGENTS.md`,
  `IMPROVEMENT_PLAN.md` or v2. Do not add another source, test, contract,
  report, outcome or generated artifact.
- Do not amend, rebase, reset, cherry-pick, merge, delete another branch,
  switch back to `main`, push, tag, open a PR, release, deploy, publish, merge
  Stage B, warm demand/horizons, run SUMO/TraCI/libsumo or start a live API job.
- Do not weaken or reinterpret any validation, provenance, recall, regret,
  failure-recall, health, integrity, latency or publication gate.

Acceptance criteria:

1. Revalidate current markers and approval/direction. Confirm current branch is
   `main`, `HEAD` equals the approved base, and
   `integration/luna-rel-02` does not exist before creating it. Any mismatch is
   terminal; do not reuse, overwrite or delete a branch.
2. Before edits or staging, recompute the 29 hashes solely from v2's immutable
   allowlist and require an exact match. Never traverse or query opaque paths.
3. Create `integration/luna-rel-02` once. Add exactly the three specified
   `.gitignore` rules, retaining the existing `runs/`, `sumo/` and scenario-
   staging rules. Verify all six rules only with synthetic nonexistent probes.
4. Install v2's stored deny hook in-process before test imports. Rerun its
   self-tests, fingerprint negative control, 253-test focused suite, pure
   canonical-digest check and pure persistent-gate test. All must pass; do not
   run either full harness module or claim full-suite/release verification.
5. Recompute the 29 v2 hashes after checks and before every commit; any change
   is terminal. `AGENTS.md`, `IMPROVEMENT_PLAN.md` and v2 must also remain
   byte-identical to their pre-task snapshots until staged.
6. Stage each commit using one explicit `git add -- <listed paths...>` command.
   Before committing, require the cached path set to equal that commit's exact
   list and contain no opaque pattern. Use the three exact messages/order above.
7. Before commit 3, write the legal terminal `READY_FOR_SOL_REVIEW` workflow
   triple and bounded handoff without editing `ACTIVE_TASK`. Then stage exactly
   commit 3's six paths and commit once.
8. Final branch/graph check proves the branch is exactly three commits ahead of
   the approved base in the required message order, with no merge commit. The
   29 hashes still match v2 and all committed paths are within the union of the
   three exact commit lists.
9. Final `git status --short` is empty. Do not use ignored-file enumeration to
   prove this; ordinary status plus synthetic `git check-ignore` probes is the
   only allowed boundary check.
10. Record exact commands/results and commit identities available before the
    self-referential final documentation commit; identify commit 3 by exact
    message and final `HEAD` for Sol to verify. Stop without push or release.

Focused checks:

```text
python3 -m json.tool validation/release_candidate_boundary_v2.json
<portable in-process v2 deny-hook self-tests and negative control>
<portable in-process guarded 253-test command>
<portable in-process guarded pure digest and persistent-gate checks>
<v2 29-file SHA-256 verifier>
git check-ignore -v <six synthetic nonexistent probes>
git diff --check -- <exact task paths>
git diff --cached --check
git status --short
git log --oneline --decorate -3
```

Approval gate:

- `NOT_REQUIRED`
- Local branch creation, explicit staging and local commits are the complete
  authorized side effect. The user's `2026-07-25` direction still explicitly
  forbids report/outcome inspection.
- No push, PR, tag, release, deployment, publication, merge, warming, SUMO or
  live-job authority is granted.

Terminal handoff conditions:

- Hand off once in `READY_FOR_SOL_REVIEW` only after all three exact commits,
  clean ordinary status, guarded checks and graph/hash verification pass.
- Any base/branch mismatch, hash drift, staging-path mismatch, forbidden access,
  check failure, need to edit an immutable file, commit failure, architecture
  change or broader authority need is terminal. Preserve the local branch and
  commits already made; do not amend, reset, delete, retry destructively or
  broaden.

<!-- COMPLETED_TASK_LUNA_REL_02_END -->

<!-- LUNA_PERF_21_TASK_START -->
## ACTIVE_TASK

### LUNA-PERF-21 — Repair and re-freeze the persistent-SUMO experiment

- Task ID: `LUNA-PERF-21`
- Revision: `1`
- Owner: `Luna High`
- Status: `DONE — Sol approved the process-free v2 repair and immutable freeze; unexecuted, unapproved, C1 untested`
- Delivery size: `STANDARD`
- Scope: Repair the two proven v1 execute-path defects without running SUMO:
  make pool startup use a valid network-backed TraCI bootstrap assembled by a
  pure, contract-bound builder; keep every timed query load identical to the
  fresh-subprocess result-affecting arguments; and recognize production's real
  filtered-route filenames in seed health. Add process-free tests that exercise
  the actual default-spawn composition and closure health path. Retire the spent
  v1 identity, freeze one strict v2 contract with current fingerprints, and
  document the unexecuted boundary. Preserve all user work and the v1 outcome.
- Completion outcome: a reviewable, process-free repaired harness and test
  suite plus one immutable `persistent_sumo_v2` contract whose exact key can be
  presented for a separate approval-gated execution task.
- Internal checkpoints: `NOT_APPLICABLE`

Allowed files:

- `tools/benchmark_persistent_sumo.py`
- `tests/test_benchmark_persistent_sumo.py`
- `validation/persistent_sumo_campaign_v2.json` (new frozen contract only)
- `IMPROVEMENT_PLAN.md` (Phase 7 v2 freeze/boundary note only)
- `TASKS.md` (Luna terminal state triple only)
- `AGENT_NOTES.md` (current handoff plus one dated entry)

Read-only inputs:

- `validation/persistent_sumo_campaign_v1.json` (spent lineage/identity only)
- `run_scenario.py`
- the network, demand metadata and calibrated routes fingerprinted by the
  contract
- official SUMO semantics already resolved by Sol: a simulation needs a
  network; TraCI `load` reloads from command-line-like options

Forbidden work:

- Do not import the installed SUMO, TraCI or libsumo packages; run a SUMO
  binary or executable environment preflight; allocate or connect a socket;
  spawn a process; create a campaign root; or execute any campaign.
- Do not open or inspect
  `validation/persistent_sumo_campaign_v1_outcome/` or any other outcome,
  report, sidecar, snapshot or run tree. Do not edit, re-key, delete, move or
  rerun the spent v1 contract or outcome.
- Do not edit `run_scenario.py`, `serve.py`, production defaults, network,
  demand, routes, API behavior, publication/release state, validation gates or
  the experiment matrix, timers, equivalence, health, integrity or performance
  thresholds.
- Do not add measured values, claim C1 evidence, record approval, adopt a
  persistent pool, deploy, release, publish, merge Stage B or weaken any gate.

Acceptance criteria:

1. Replace the implicit bare member launch with one pure bootstrap-command
   builder used by `_TraciConnector._default_spawn`. It must bind the exact SUMO
   binary, absolute `NET_PATH`, actual allocated port, single-client setting,
   member work directory and new-session ownership. The v2 contract separately
   binds this bootstrap template; a re-keyed mutation is refused.
2. Persistent per-query `simulation.load` receives the same complete
   result-affecting arguments as the fresh-subprocess arm: net, selected route,
   additionals, seed, begin/end, meso flags and output paths, with vehroute only
   for the trajectory seed and no bootstrap-only remote-port option.
3. `_variant_family` accepts baseline and exact production filtered names for
   q50/q10/q90, including
   `calibrated.rou_close_<edge>.rou.xml`, while malformed or cross-bound
   seed/variant evidence still fails closed. Clean three-seed closure telemetry
   using those names passes `_seed_health_ok`.
4. Add a process-free composition test that calls the real default-spawn path
   with fake `sumo` module identity and intercepted `subprocess.Popen`, proving
   exact argv/cwd/session settings. Existing failed-connect, timeout, abort,
   cleanup and fallback lifecycle tests remain passing.
5. Make `persistent_sumo_v2` at
   `validation/persistent_sumo_campaign_v2.json` the sole canonical executable
   identity and mark v1 retired/spent. The CLI refuses v1, renamed copies,
   stale IDs and edited/re-keyed invalid contracts before any executable
   boundary.
6. The v2 contract preserves the v1 matrix, seed/member map, query order,
   timer boundary, report schema, production payload builders and strict gates;
   adds exact bootstrap identity; binds the finalized harness, current
   `run_scenario.py`, network, demand and route fingerprints; has
   `outcomes_present_at_freeze:false`; names v1 as the failed/spent predecessor;
   and has a recomputable 64-hex content key.
7. Import, help, contract-only validation and the full focused tests remain
   process/socket/outcome-free and never import installed SUMO/TraCI/libsumo.
   Contract construction may copy the frozen environment/version identity from
   v1; it must not probe the executable.
8. Update only the Phase 7 note to name the exact new key and state:
   repaired/frozen, unexecuted, unapproved, no measured C1 result, no adoption,
   v1 never rerunnable, and fresh exact-key approval required for any execution
   or outcome inspection.
9. Preserve unrelated working-tree changes and the complete v1 outcome. Stop
   after the static contract/test evidence; do not begin preflight or execution.

Focused checks:

```text
python3 tools/benchmark_persistent_sumo.py --campaign validation/persistent_sumo_campaign_v2.json --validate-contract-only
python3 -m pytest -q tests/test_benchmark_persistent_sumo.py
python3 -c 'import hashlib,json,pathlib; p=pathlib.Path("validation/persistent_sumo_campaign_v2.json"); c=json.loads(p.read_text()); payload={k:v for k,v in c.items() if k!="content_key"}; key=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest(); assert c["experiment_id"]=="persistent_sumo_v2" and c["content_key"]==key and len(key)==64 and c["outcomes_present_at_freeze"] is False; print(key)'
git diff --check -- tools/benchmark_persistent_sumo.py tests/test_benchmark_persistent_sumo.py validation/persistent_sumo_campaign_v2.json IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md
```

Approval gate:

- `NOT_REQUIRED`
- This task authorizes only process-free source, test, contract and
  documentation work. `SOL PLAN` is not approval to execute SUMO. Any v2
  executable preflight, socket/process activity, campaign root creation,
  execution or outcome inspection requires a later task with the new exact key
  and a fresh recorded user message.

Terminal handoff conditions:

- Hand off in `READY_FOR_SOL_REVIEW` when every acceptance criterion and
  focused check passes.
- Stop with exact blocker evidence if installed SUMO/TraCI, a process/socket,
  outcome access, production/artifact architecture change, scope expansion or
  new authority would be required.
- After three distinct serious failed approaches, record all attempts,
  remaining safe options and Sol's recommended decision; do not attempt a
  fourth approach.

<!-- LUNA_PERF_21_TASK_END -->

<!-- LUNA_PERF_20_TASK_START -->
## ACTIVE_TASK

### LUNA-PERF-20 — Execute and interpret the frozen persistent-SUMO campaign

- Task ID: `LUNA-PERF-20`
- Revision: `1`
- Owner: `Luna High`
- Status: `DONE — Sol approved closure of the preserved FAILED EXPERIMENT; one attempt spent, C1 untested, no retry or adoption`
- Delivery size: `STANDARD`
- Scope: After Sol records fresh approval matching the exact immutable key,
  run the frozen `persistent_sumo_v1` harness exactly once at its canonical
  path and exact new artifact root. Preserve the complete run tree, inspect
  only that campaign's report, apply the pre-committed equivalence, health,
  integrity, fault/fallback, latency and improvement gates, and record an
  honest go/no-go result. Do not edit the harness or contract, retry a spent
  attempt, adopt the persistent arm, change production, or inspect any other
  outcome.
- Completion outcome: one spent campaign attempt is preserved and its exact
  verdict is documented for Sol review, or a preflight/execution failure is
  preserved and handed off without retry.
- Internal checkpoints: `NOT_APPLICABLE`

Allowed files and artifacts:

- `validation/persistent_sumo_campaign_v1_outcome/` (new exact run root,
  generated once after approval; preserve completely)
- `IMPROVEMENT_PLAN.md` (Phase 7 result/go-no-go note only)
- `TASKS.md` (Luna terminal state triple only)
- `AGENT_NOTES.md` (current handoff plus one dated entry)

Read-only inputs:

- `validation/persistent_sumo_campaign_v1.json`
- `tools/benchmark_persistent_sumo.py`
- `run_scenario.py` and the frozen network/demand/route inputs bound by the
  contract fingerprints

Forbidden work:

- While `BLOCKED`, do not run executable preflight, import SUMO/libsumo/TraCI,
  allocate a socket, create the artifact root, start a process, or inspect any
  outcome.
- After approval, do not edit or re-freeze the contract, harness, production
  source, tests, network, demand or calibrated routes; do not change an option,
  query, seed, variant, timeout, timer boundary, gate or report schema.
- Invoke `--execute` at most once. The attempt is spent on invocation whether
  it passes, fails, is interrupted or produces no report. Never repair, delete,
  overwrite or rerun the root.
- Do not inspect any other report, run tree, sidecar, state snapshot or
  campaign evidence. Do not publish a scenario, trajectory, manifest, cache,
  state or `latest_*` pointer.
- Do not adopt a persistent pool, edit `serve.py`, change `/api/close`, merge
  Stage B, warm demand/horizons, deploy, release, publish, or weaken any
  validation, provenance, equivalence, health, integrity or performance gate.

Acceptance criteria:

1. Before any executable preflight, Sol has recorded the exact approving user
   message, exact scope/key, user-message date and recorder/date for this task
   and revision; all three current blocks agree.
2. The canonical contract-only check and focused harness tests pass before the
   one execution. Any failure stops without creating the run root.
3. The artifact root is exactly
   `validation/persistent_sumo_campaign_v1_outcome` and is initially absent.
4. Invoke the canonical contract with `--execute` exactly once. Contract and
   environment identity must pass before TraCI import, port allocation, root
   creation or child spawn.
5. The attempt preserves every generated file and reaps every reference child
   and persistent member on success, fault, timeout, interruption or partial
   startup. Any unproved reap is terminal.
6. No fallback/member fault is eligible. Every one of the ten paired rows must
   have exact production scenario and trajectory digests, healthy three-seed
   telemetry, and `verified_clean` closure integrity where required.
7. A PASS additionally requires persistent closure p95 `<=10.0` seconds and
   improvement `>=0.04` versus the paired subprocess p95. No missing or
   malformed evidence may be interpreted.
8. Inspect only
   `validation/persistent_sumo_campaign_v1_outcome/persistent_sumo_report.json`
   after the attempt. Its key, envelope, ten rows, ordering and verdict must
   match the frozen contract; otherwise the experiment fails closed.
9. Record the pre-committed reading: PASS advances only to a separate
   adoption-planning task; equivalent but slow/insufficient improvement is a
   definitive C1 no-go; any equivalence/health/integrity miss is a failed
   experiment. This task grants no adoption authority.
10. Update only the Phase 7 result note and terminal handoff, make no
    performance claim beyond the preserved report, and stop for `SOL REVIEW`.

Focused checks, in order after approval:

```text
python3 tools/benchmark_persistent_sumo.py --campaign validation/persistent_sumo_campaign_v1.json --validate-contract-only
python3 -m pytest -q tests/test_benchmark_persistent_sumo.py
python3 tools/benchmark_persistent_sumo.py --campaign validation/persistent_sumo_campaign_v1.json --execute --artifact-dir validation/persistent_sumo_campaign_v1_outcome
python3 -c 'import json,pathlib; p=pathlib.Path("validation/persistent_sumo_campaign_v1_outcome/persistent_sumo_report.json"); r=json.loads(p.read_text()); assert r["content_key"]=="72108df6b3ec61de33e5006181d38abc3aba3292bcb8b907643dd9d7f431f588"; assert len(r["persistent_queries"])==len(r["subprocess_queries"])==10; print(json.dumps(r["verdict"],sort_keys=True))'
git diff --check -- IMPROVEMENT_PLAN.md TASKS.md AGENT_NOTES.md
```

Approval gate:

- `REQUIRED`
- Exact scope/key: one canonical contract-only check, focused harness test,
  executable environment preflight, one SUMO/TraCI paired campaign invocation,
  and inspection of only its exact artifact root/report at content key
  `72108df6b3ec61de33e5006181d38abc3aba3292bcb8b907643dd9d7f431f588`.
- Exact user message received (JSON-escaped verbatim):

  ```text
  "I explicitly approve the one-time persistent_sumo_v1 SUMO/TraCI paired campaign, its required preflight and inspection of only its own outcome, at content\n  > key 72108df6b3ec61de33e5006181d38abc3aba3292bcb8b907643dd9d7f431f588 and artifact root validation/persistent_sumo_campaign_v1_outcome."
  ```

- User-message date: `2026-07-24`
- Sol recorder/date: `Sol High / 2026-07-24`

Terminal handoff conditions:

- Hand off in `READY_FOR_SOL_REVIEW` after the single attempt and exact
  go/no-go documentation are complete.
- If preflight, execution, cleanup or report validation fails, preserve the
  evidence, do not retry, and hand off the exact terminal failure for Sol
  review.
- Luna must match this exact approval record before preflight/execution. Any
  approval for a different task, revision, key, root or scope is invalid.

<!-- LUNA_PERF_20_TASK_END -->

<!-- COMPLETED_TASK_LUNA_PERF_14_START -->
## COMPLETED_TASK

### LUNA-PERF-14 — Remove the failed prep parallelism and accelerate edge-data parsing

- Task ID: `LUNA-PERF-14`
- Revision: `1`
- Owner: `Luna High`
- Status: `DONE — Sol approved result-neutral implementation and non-SUMO evidence 2026-07-23`
- Delivery size: `STANDARD`
- Scope: Restore deterministic serial closure-variant preparation for every
  seed-worker setting because v5 measured the threaded path as a regression.
  Then optimize the production `parse_edgedata` hot path with standard-library
  code only, retaining its exact returned flows, measured-zero semantics,
  failure behavior, and all downstream scenario/closure results. Prove the
  parser change against the current implementation on deterministic non-SUMO
  fixtures before retaining it. Do not create a v6 campaign in this task.
- Completion outcome: the failed closure-preparation experiment is removed;
  retain a parser optimization only if semantic equivalence and a repeatable
  local speed improvement are both demonstrated. Otherwise preserve the
  serial rollback, revert the unproven parser attempt, and report the no-go.
- Internal checkpoints: `NOT_APPLICABLE`.

Allowed files:

- `run_scenario.py`
- `tests/test_scenario.py`
- `tests/test_scenario_timing.py`
- `TASKS.md` (handoff state fields only)
- `AGENT_NOTES.md` (current handoff plus one dated entry)

Read-only context:

- `IMPROVEMENT_PLAN.md` result-preservation and performance-proof rules
- `AGENT_NOTES.md` approved PERF-13 summary only; do not open or enumerate the
  v5 report, run tree, seed files, trajectories, or any other outcome
- `validation/scenario_phase_profile_campaign_v5.json` identity metadata only

Acceptance criteria:

1. `prepare_closure_variants` always uses the existing ordered serial calls;
   remove its closure-preparation executor and stale concurrency claims while
   leaving the independently approved multi-seed SUMO executor untouched.
2. Parser tests compare the optimized implementation with a test-local
   reference of the current behavior across multiple intervals, absent/zero
   entries, required measured-empty closure edges, duplicate edge records,
   out-of-range intervals, and malformed numeric/XML input. Returned keys,
   NumPy values/dtypes, overwrite behavior, and exceptions remain equivalent.
3. Use a deterministic synthetic SUMO edge-data fixture representative of 96
   intervals and thousands of edges. Record fixture shape, exact command,
   trial count, old/new medians, absolute saving, and ratio. Retain the parser
   change only if at least seven alternating measured trials show a median
   improvement of `>= 25%` and `>= 0.15 s` on the named machine; the timing is
   diagnostic development evidence, not release evidence or a 10-second claim.
4. Existing closure integrity, result ordering, timing-sidecar, fail-closed
   publication, and worker-1/worker-3 semantic tests remain green. No phase
   schema, seed count, simulation, trajectory, scenario, API, default, or
   artifact contract changes.
5. Luna records the retained/rejected implementation, files, exact checks,
   benchmark evidence, blockers, and next step, then stops for Sol review.

Focused non-SUMO checks:

```text
python3 -m pytest -q tests/test_scenario.py tests/test_scenario_timing.py
git diff --check -- run_scenario.py tests/test_scenario.py tests/test_scenario_timing.py TASKS.md AGENT_NOTES.md
```

Forbidden work:

- Do not run SUMO, a scenario, a campaign, or any v1-v5 execution. Do not
  create, inspect, enumerate, copy, repair, or refresh any outcome or run tree.
- Do not freeze v6, change campaign identities/fingerprints, change production
  worker defaults or API behavior, or claim the completion target is met.
- Do not build/warm demand, start horizon warming, merge Stage B, deploy,
  release, publish, or promote V4.
- Do not weaken validation, provenance, semantic comparison, closure
  integrity, recall, regret, failure-recall, release, or publication gates.
  Diagnostic replay and synthetic timing are never release evidence.

Terminal handoff rule:

- Stop for `SOL REVIEW` after the focused non-SUMO evidence. Any real campaign
  would require a later Sol task, a newly frozen identity, and fresh exact user
  approval; it is not an automatic next step.

<!-- COMPLETED_TASK_LUNA_PERF_14_END -->

<!-- COMPLETED_TASK_LUNA_PERF_13_START -->
## COMPLETED_TASK

### LUNA-PERF-13 — Execute frozen paired seed campaign v5 exactly once

- Task ID: `LUNA-PERF-13`
- Revision: `1`
- Owner: `Luna High`
- Status: `DONE — Sol approved diagnostic execution 2026-07-23; adoption gate not met`
- Delivery size: `STANDARD`
- Scope: After Sol records the exact approval below, validate frozen v5,
  execute its 20-row paired one-worker/three-worker SUMO campaign exactly
  once, and validate the immutable report and content-keyed run tree. Report
  result equivalence, latency/resource measurements, and the frozen adoption
  verdict for concurrent closure preparation plus parallel seeds. Do not
  change source, tests, campaign identity, production defaults, gates, or
  release state. This is diagnostic performance evidence only.
- Completion outcome: one preserved successful report/run tree, or one
  preserved failed/partial one-shot attempt with no retry or repair.
- Internal checkpoints: `NOT_APPLICABLE`.

Allowed files after approval:

- `validation/scenario_phase_profile_report_v5.json` (new)
- `runs/scenario-phase-profile/1578d3506291011ff88d4edfb12fd291935171950914feb97e64d0b2d0726f14/`
  (new)
- `TASKS.md` (`State`, `Next action`, and `Transition` only at handoff)
- `AGENT_NOTES.md` (`CURRENT_HANDOFF` plus one dated entry only)

Read-only context after approval:

- `validation/scenario_phase_profile_campaign_v5.json`
- `tools/benchmark_speed.py`
- `tests/test_benchmark_speed.py`
- `tests/test_scenario_timing.py`

Forbidden work:

- Before approval is recorded, do not run tests or preflight and do not create,
  inspect, or execute any v5 outcome path.
- Do not edit source, tests, the frozen campaign, identities/fingerprints, or
  any v1-v4 report/run tree. Do not retry, resume, repair, overwrite, refreeze,
  or use an alternate execution path after the one invocation starts.
- Do not change worker defaults, API behavior, demand, fidelity, seeds,
  closure semantics, semantic comparison, validation, provenance, recall,
  regret, failure-recall, release, or publication gates.
- Do not build/warm demand, start horizon warming, merge Stage B, deploy,
  release, publish, promote V4, or treat diagnostic evidence as release proof.

Acceptance criteria after approval:

1. The focused suite passes and non-executing preflight confirms the exact
   content key, seven bound fingerprints, demand identity, 20 unexecuted rows,
   and absent v5 output paths before the one-shot run.
2. The exact command below is invoked once. Success creates one immutable
   report and exactly 20 trial directories: five for each combination of
   baseline/whole-window closure and worker count `1`/`3`.
3. Production validation passes with complete campaign, matrix, fingerprint,
   demand, phase, health, closure, and provenance evidence; semantic and
   reference mismatch collections are empty.
4. Paired scenario/trajectory digests are identical. The report preserves
   per-row wall/profile/phase/RSS evidence and records the recomputed frozen
   adoption verdict: both cases must meet p95 `<= 10 s` and improvement
   `>= 20%` for `adoptable: true`; any miss fails closed.
5. A failed, timed-out, or non-adoptable attempt is preserved honestly and
   handed to Sol without retry. Even `adoptable: true` authorizes no default,
   API, deployment, release, publication, Stage-B, or horizon-warming change.

Focused checks after approval and before execution:

```text
python3 -m pytest -q tests/test_scenario_timing.py tests/test_benchmark_speed.py
python3 tools/benchmark_speed.py --campaign validation/scenario_phase_profile_campaign_v5.json --preflight-only
```

Authorized one-shot command after approval:

```text
python3 tools/benchmark_speed.py --campaign validation/scenario_phase_profile_campaign_v5.json --artifact-dir runs/scenario-phase-profile/1578d3506291011ff88d4edfb12fd291935171950914feb97e64d0b2d0726f14 --write validation/scenario_phase_profile_report_v5.json
```

Final focused check:

```text
git diff --check -- validation/scenario_phase_profile_report_v5.json TASKS.md AGENT_NOTES.md
```

Approval gate:

- Exact scope: one invocation of the authorized command against frozen content
  key `1578d3506291011ff88d4edfb12fd291935171950914feb97e64d0b2d0726f14`.
- Required exact user message: `I explicitly approve the one-time LUNA-PERF-13
  SUMO paired seed campaign at content key
  1578d3506291011ff88d4edfb12fd291935171950914feb97e64d0b2d0726f14.`
- Recorded user approval: `I explicitly approve the one-time LUNA-PERF-13 SUMO paired seed campaign at content key 1578d3506291011ff88d4edfb12fd291935171950914feb97e64d0b2d0726f14.`
- User approval date: `2026-07-23`
- Sol recorder/date: `Sol High / 2026-07-23`
- Approval status: `CONSUMED — exact task, scope, and content key matched; one invocation completed`
- Approval may not be inferred from `SOL PLAN`, earlier approvals, or another
  task/key. When the exact message arrives, Sol records it and transitions
  this same revision to `READY_FOR_LUNA` without replanning.

Terminal handoff rule:

- After the one authorized invocation succeeds or fails, preserve its exact
  artifacts and stop for `SOL REVIEW`; no retry is authorized.

<!-- COMPLETED_TASK_LUNA_PERF_13_END -->

<!-- COMPLETED_TASK_LUNA_PERF_12_START -->
## COMPLETED_TASK

### LUNA-PERF-12 — Parallelize closure preparation and freeze campaign v5

- Task ID: `LUNA-PERF-12`
- Revision: `1`
- Owner: `Luna High`
- Status: `DONE — Sol approved through the v5 pre-outcome freeze boundary 2026-07-23`
- Delivery size: `STANDARD`
- Scope: Retire spent v4 from executable status and replace its stale
  pre-outcome assertion. In `run_scenario.py`, use the existing
  `--seed-workers` bound to prepare independent closure-filtered demand
  variants concurrently, while preserving the worker-1 serial path, ordered
  outputs, counts, failure behavior, and exact scenario semantics. Add focused
  non-SUMO equivalence/failure tests. After source and harness changes are
  final, freeze fresh campaign v5 with the same cases, seeds, worker arms,
  fidelity, hard gates, and authority limits as v4. Run no SUMO and inspect or
  create no v5 outcome.
- Completion outcome: one deterministic result-preserving closure-preparation
  optimization plus a production-valid, content-keyed v5 pre-outcome contract.
- Internal checkpoints: `NOT_APPLICABLE`.

Allowed files:

- `run_scenario.py`
- `tools/benchmark_speed.py`
- `tests/test_scenario_timing.py`
- `tests/test_benchmark_speed.py`
- `validation/scenario_phase_profile_campaign_v5.json` (new)
- `TASKS.md` (`State`, `Next action`, and `Transition` only at handoff)
- `AGENT_NOTES.md` (`CURRENT_HANDOFF` plus one dated entry only)

Read-only context:

- `validation/scenario_phase_profile_campaign_v4.json`
- `validation/scenario_phase_profile_report_v4.json` diagnostic summary only;
  do not open or enumerate its run tree
- `traffic_sim/simulation/warm_state_cache.py` only to preserve cache and
  provenance boundaries; horizon warming remains out of scope

Forbidden work:

- Do not run SUMO, execute a campaign, create or inspect v5 outcomes, rerun or
  mutate v4 evidence, or copy v4 observed values into v5 thresholds/results.
- Do not change the default seed-worker count, `serve.py`, API behavior,
  scenario/closure semantics, seeds, variants, fidelity, demand, network,
  trajectory/audit products, publication ordering, or production outputs.
- Do not weaken the 10-second ceiling, 20% improvement floor, semantic,
  health, closure-integrity, validation, provenance, recall, regret,
  failure-recall, release, or publication gates.
- Do not build/warm demand, start horizon warming, merge Stage B, deploy,
  release, publish, promote V4, or treat diagnostic evidence as release proof.

Acceptance criteria:

1. Production refuses spent v4 and recognizes only fresh v5. Tests no longer
   require consumed v4 outcome paths to be absent and never enumerate/open
   those paths; they require v5 report/run paths to be absent pre-outcome.
2. Worker `1` executes the unchanged serial closure-preparation path. A larger
   existing `--seed-workers` value bounds concurrent filtering to the number
   of demand variants; each job calls the same filtering function with shared
   read-only graph/free-flow inputs and a distinct staged output.
3. Focused fixture tests prove serial and concurrent preparation produce the
   same ordered variant paths, byte-identical route artifacts, identical
   truncated/dropped totals, and identical exceptions. A worker failure
   cancels/joins remaining work and cannot publish a scenario or partial cache.
4. No execution/default/API/output contract changes. Result ordering remains
   deterministic and existing phase, seed-health, closure-integrity,
   trajectory, audit, cleanup, and fail-closed tests remain green.
5. V5 is frozen only after source/harness finalization with a new identity,
   recomputed content key and fingerprints, accurate lineage, exactly 20
   baseline/whole-window closure rows for worker arms `[1, 3]` and five trials,
   unchanged seeds/demand/fidelity, unchanged 10-second and 20% adoption gates,
   and authority limited to diagnostic consideration.
6. Production preflight verifies all frozen inputs and plans exactly 20 rows
   with `executed: false`, while v5 report and content-keyed run root remain
   absent. No SUMO or outcome access occurs.

Focused checks:

```text
python3 -m pytest -q tests/test_scenario_timing.py tests/test_benchmark_speed.py
python3 tools/benchmark_speed.py --campaign validation/scenario_phase_profile_campaign_v5.json --preflight-only
git diff --check -- run_scenario.py tools/benchmark_speed.py tests/test_scenario_timing.py tests/test_benchmark_speed.py validation/scenario_phase_profile_campaign_v5.json TASKS.md AGENT_NOTES.md
```

Approval gate:

- `NOT_REQUIRED`. This task authorizes no SUMO, campaign execution, v5 outcome
  access, production-default change, deployment, release, or publication.

Terminal handoff rule:

- Stop for `SOL REVIEW` after all non-SUMO criteria pass, or stop blocked if
  deterministic equivalence requires architecture/artifact-contract expansion.

<!-- COMPLETED_TASK_LUNA_PERF_12_END -->

<!-- COMPLETED_TASK_LUNA_PERF_11_START -->
## COMPLETED_TASK

### LUNA-PERF-11 — Execute frozen paired seed campaign v4 exactly once

- Task ID: `LUNA-PERF-11`
- Revision: `1`
- Owner: `Luna High`
- Status: `DONE — Sol approved diagnostic execution 2026-07-23; adoption gate not met`
- Delivery size: `STANDARD`
- Scope: After Sol records the exact approval below, validate the frozen v4
  contract, execute its 20-row paired one-worker/three-worker SUMO campaign
  exactly once, and validate the immutable report and content-keyed run tree.
  Report result equivalence, latency/resource measurements, and the frozen
  adoption verdict. Do not change source, tests, campaign identity, production
  defaults, or release state. This is diagnostic performance evidence only.
- Completion outcome: one preserved successful report/run tree, or one
  preserved failed/partial one-shot attempt with no retry or repair.
- Internal checkpoints: `NOT_APPLICABLE`.

Allowed files after approval:

- `validation/scenario_phase_profile_report_v4.json` (new)
- `runs/scenario-phase-profile/feeed57cb38a0c189aa9002c3e3611cb7233c467c9ff8cc1906d1c0daf712fa6/`
  (new)
- `TASKS.md` (`State`, `Next action`, and `Transition` only at handoff)
- `AGENT_NOTES.md` (`CURRENT_HANDOFF` plus one dated entry only)

Read-only contract context after approval:

- `validation/scenario_phase_profile_campaign_v4.json`
- `tools/benchmark_speed.py`
- `tests/test_benchmark_speed.py`
- `tests/test_scenario_timing.py`

Forbidden work:

- Before approval is recorded, do not run tests or preflight and do not create,
  inspect, or execute any v4 outcome path.
- Do not edit source, tests, the frozen campaign, its identity/fingerprints, or
  any v1-v3 report/run tree. Do not retry, resume, repair, overwrite, refreeze,
  or use an alternate execution path after the one invocation starts.
- Do not change worker defaults, scenario/API behavior, demand, fidelity,
  seeds, closure semantics, semantic comparison, validation, provenance,
  recall, regret, failure-recall, release, or publication gates.
- Do not build/warm demand, start horizon warming, merge Stage B, deploy,
  release, publish, or treat this diagnostic campaign as release evidence.

Acceptance criteria after approval:

1. The focused suite passes and non-executing preflight confirms the exact
   content key, seven bound fingerprints, 20 unexecuted rows, and absent output
   paths before the one-shot run.
2. The exact command below is invoked once. Success creates one immutable
   report and exactly 20 trial directories: five trials for each combination
   of baseline/whole-window closure and worker count `1`/`3`.
3. Production validation passes with complete campaign, matrix, fingerprint,
   demand, phase, health, closure, and provenance evidence; semantic and
   reference mismatch collections are empty.
4. Paired result digests are identical. The report records per-case/per-arm
   p50, p95, maximum wall/profile/phase time, peak RSS, percentage improvement,
   the 10-second gap, all frozen gate results, and the final adoption verdict.
5. A failed or timed-out attempt is preserved honestly and handed to Sol
   without retry. A successful/adoptable verdict authorizes no production
   change, release, publication, Stage-B merge, or horizon warming.

Focused checks after approval and before execution:

```text
python3 -m pytest -q tests/test_benchmark_speed.py tests/test_scenario_timing.py
python3 tools/benchmark_speed.py --campaign validation/scenario_phase_profile_campaign_v4.json --preflight-only
```

Authorized one-shot command after approval:

```text
python3 tools/benchmark_speed.py --campaign validation/scenario_phase_profile_campaign_v4.json --artifact-dir runs/scenario-phase-profile/feeed57cb38a0c189aa9002c3e3611cb7233c467c9ff8cc1906d1c0daf712fa6 --write validation/scenario_phase_profile_report_v4.json
```

Final focused check:

```text
git diff --check -- validation/scenario_phase_profile_report_v4.json TASKS.md AGENT_NOTES.md
```

Approval gate:

- Exact scope: one invocation of the authorized command against frozen content
  key `feeed57cb38a0c189aa9002c3e3611cb7233c467c9ff8cc1906d1c0daf712fa6`.
- Required exact user message: `I explicitly approve the one-time LUNA-PERF-11
  SUMO paired seed campaign at content key
  feeed57cb38a0c189aa9002c3e3611cb7233c467c9ff8cc1906d1c0daf712fa6.`
- Recorded user approval: `I explicitly approve the one-time LUNA-PERF-11 SUMO paired seed campaign at content key feeed57cb38a0c189aa9002c3e3611cb7233c467c9ff8cc1906d1c0daf712fa6.`
- User approval date: `2026-07-23`
- Sol recorder/date: `Sol High / 2026-07-23`
- Approval status: `CONSUMED — exact task, scope, and content key matched; one invocation completed`
- Approval may not be inferred from `SOL PLAN`, prior campaign approvals, or
  approval of another content key. When the exact message arrives, Sol records
  it and transitions this same revision to `READY_FOR_LUNA` without replanning.

Terminal handoff rule:

- After the one authorized invocation succeeds or fails, preserve its exact
  artifacts and stop for `SOL REVIEW`; no retry is authorized.

<!-- COMPLETED_TASK_LUNA_PERF_11_END -->

<!-- COMPLETED_TASK_LUNA_PERF_10_START -->
## COMPLETED_TASK

### LUNA-PERF-10 — Freeze paired serial/parallel seed campaign v4

- Task ID: `LUNA-PERF-10`
- Revision: `1`
- Owner: `Luna High`
- Status: `DONE — Sol approved 2026-07-23`
- Delivery size: `STANDARD`
- Scope: Retire spent `scenario_phase_profile_v3` in the production harness
  and freeze fresh `scenario_phase_profile_v4` before outcomes. Extend only
  the performance-campaign contract/matrix to execute ordered worker arms
  `[1, 3]` across the same baseline and whole-window closure, five trials per
  arm, with identical meso mode, seeds, variants, demand, timeout, fresh-cache
  state, and fidelity. Bind result-equivalence and latency adoption gates. Do
  not change `run_scenario.py`, its default worker count, any API path, or
  production behavior. Run no SUMO and create or inspect no v4 outcome.
- Completion outcome: one production-valid, content-keyed v4 contract whose
  non-executing preflight plans exactly 20 paired rows, with focused tests that
  prove executable worker binding and fail-closed semantic comparison.
- Internal checkpoints: `NOT_APPLICABLE`.

Allowed files:

- `tools/benchmark_speed.py`
- `tests/test_benchmark_speed.py`
- `validation/scenario_phase_profile_campaign_v4.json` (new)
- `TASKS.md` (`State`, `Next action`, and `Transition` only at handoff)
- `AGENT_NOTES.md` (`CURRENT_HANDOFF` plus one dated entry only)

Read-only contract context:

- `validation/scenario_phase_profile_campaign_v3.json`
- `validation/scenario_phase_profile_report_v3.json` only if needed to verify
  the already-approved diagnostic identity; do not copy observed values into
  v4
- `run_scenario.py` existing seed-worker execution seam
- `tests/test_scenario_timing.py`
- `IMPROVEMENT_PLAN.md` performance proof rules

Forbidden work:

- Do not run SUMO, a scenario, or any campaign execution; do not create or
  inspect v4 reports, run trees, sidecars, or outcomes. Preflight must remain
  non-executing and create nothing.
- Do not inspect v1/v2 reports or run trees, mutate v3 campaign/report/run
  artifacts, or revive any spent identity. Do not use diagnostic evidence as
  release evidence.
- Do not edit `run_scenario.py`, production defaults, server/API behavior,
  demand, network, scenario semantics, phase timing, seeds/variant mapping,
  closure edge/window, validation/provenance gates, or semantic digest rules.
- Do not build/warm demand, start a server or horizon warming, merge Stage B,
  promote V4, optimize production, release, publish, or touch unrelated files.

Acceptance criteria:

1. The production loader names v4 as the sole executable campaign and refuses
   v1, v2, and spent v3 before artifact creation or subprocess execution. V4
   has a new ID/content key, `outcomes_present_at_freeze: false`, accurate
   lineage, and a harness fingerprint computed only after source is final.
2. The v4 schema binds ordered worker arms `[1, 3]` to production behavior.
   Its matrix is exactly 20 rows: two unchanged cases × two worker arms × five
   trials, each using canonical seeds `1000/1001/1002` mapped to q50/q10/q90,
   meso, timeout 1800, no warmups, and no cache substitution.
3. Campaign CLI overrides remain refused. Focused mocked execution proves all
   20 rows call `run_case()` with the frozen worker count and that any serial/
   parallel scenario or trajectory digest mismatch produces a non-zero result.
4. V4 carries machine-readable adoption gates: zero semantic mismatches; all
   existing provenance, seed-health, closure-integrity and hard-failure gates;
   parallel p95 wall time <= 10 seconds for each case; and >= 20% p95 wall-time
   improvement over its paired serial arm. These gates are additive and do not
   authorize deployment or weaken any existing gate.
5. Production preflight recomputes the exact key, seven live fingerprints,
   demand identity, and 20-row matrix with `executed: false`. The fresh v4
   report path and content-keyed run root are absent before and after checks.
6. The obsolete v3-output-absence assertion is replaced by immutable retired-
   identity coverage plus v4 pre-outcome absence coverage. No v3 artifact is
   read, changed, deleted, overwritten, or used as release evidence.

Focused non-SUMO checks:

```bash
python3 -m pytest -q tests/test_benchmark_speed.py tests/test_scenario_timing.py
python3 tools/benchmark_speed.py --campaign validation/scenario_phase_profile_campaign_v4.json --preflight-only
git diff --check -- tools/benchmark_speed.py tests/test_benchmark_speed.py validation/scenario_phase_profile_campaign_v4.json TASKS.md AGENT_NOTES.md
```

- Approval gate: `NOT_REQUIRED` — this task cannot execute SUMO or create/
  inspect v4 outcomes. A later execution task requires Sol approval of the
  frozen key and fresh exact-key user approval.
- Terminal handoff conditions: hand off in `READY_FOR_SOL_REVIEW` after the
  contract, tests, preflight, absence proof, and notes are complete; otherwise
  hand off the exact terminal blocker without broadening scope.
<!-- COMPLETED_TASK_LUNA_PERF_10_END -->

<!-- COMPLETED_TASK_LUNA_WORKFLOW_01_START -->
## COMPLETED_TASK — LUNA-WORKFLOW-01

### LUNA-WORKFLOW-01 — Make the Sol/Luna Markdown handoff compact and fail-closed

- Task ID: `LUNA-WORKFLOW-01`
- Revision: `1`
- Owner: `Luna High`
- Status: `DONE — Sol approved 2026-07-23`
- Scope: Improve only the Markdown coordination protocol so current authority
  is compact, revision-bound, single-source, and fail-closed. Preserve all
  existing project history and every safety, validation, provenance, release,
  publication, and execution gate.
- Approval gate: `NOT_REQUIRED`; authorization is limited to the documentation
  files and read-only documentation checks below. It does not authorize any
  product workflow, outcome access, or artifact generation.

Allowed files:

- `AGENTS.md`
- `TASKS.md` (this marked active task and `WORKFLOW_CONTROL` only)
- `AGENT_NOTES.md` (`CURRENT_HANDOFF` plus one new dated entry only)

Forbidden work:

- Do not change `ARCHITECTURE.md`, `IMPROVEMENT_PLAN.md`, product code, tests,
  validation artifacts, run artifacts, or any historical task/evidence entry.
- Do not run SUMO, scenarios, benchmarks, servers, endpoints, product tests,
  outcome inspection, demand generation/warming, release, or publication work.
- Do not modify or normalize the large existing unrelated diff.

Acceptance criteria:

1. In `AGENTS.md`, define one source of truth per concern: stable protocol in
   `AGENTS.md`, current task/state in the marked `WORKFLOW_CONTROL` and
   `ACTIVE_TASK` blocks of `TASKS.md`, and current execution/review evidence in
   the marked `CURRENT_HANDOFF` block of `AGENT_NOTES.md`.
2. Replace the ambiguous instruction to read entire growing ledgers with a
   precise startup fast path: read all of `AGENTS.md`, only the marked current
   blocks in `TASKS.md` and `AGENT_NOTES.md`, inspect `git status --short`, and
   inspect targeted diffs for allowed files. Read architecture, improvement,
   or history sections only when the active task names them or a decision
   needs them.
3. Define a small fail-closed state machine with legal Sol/Luna transitions,
   exactly one active task, task ID plus revision checks, ownership of each
   field, and explicit behavior for stale/missing/conflicting state.
4. Add compact copy-paste task and handoff schemas. A task must name scope,
   allowed files, forbidden work, acceptance criteria, checks, approval gate,
   and escalation conditions. A Luna handoff must name task ID/revision,
   files changed, tests/checks with results, evidence, blockers, and next
   state. Keep summaries bounded; history below the current blocks is not
   startup context.
5. Strengthen approval handling: approval-requiring work stays `BLOCKED` until
   Sol records the exact user message, scope/key, and date in the active task;
   Luna must match all of them and may never infer, reuse, or retroactively
   apply approval. Preserve the existing SUMO/outcome/warming/release gates.
6. Remove or consolidate duplicated workflow wording only where necessary.
   Do not rewrite historical task or evidence entries, and do not change
   `ARCHITECTURE.md`, `IMPROVEMENT_PLAN.md`, product code, tests, validation
   artifacts, or run artifacts.

Focused checks:

```bash
python3 - <<'PY'
from pathlib import Path
for name, start, end in (
    ("TASKS.md", "<!-- WORKFLOW_" "CONTROL_START -->", "<!-- WORKFLOW_" "CONTROL_END -->"),
    ("TASKS.md", "<!-- ACTIVE_" "TASK_START -->", "<!-- ACTIVE_" "TASK_END -->"),
    ("AGENT_NOTES.md", "<!-- CURRENT_" "HANDOFF_START -->", "<!-- CURRENT_" "HANDOFF_END -->"),
):
    text = Path(name).read_text()
    assert text.count(start) == 1, (name, start)
    assert text.count(end) == 1, (name, end)
print("workflow markers: ok")
PY
git diff --check -- AGENTS.md TASKS.md AGENT_NOTES.md
```

Escalate when:

- Any required marker is missing or duplicated, current IDs/revisions
  conflict, the requested change needs a fourth file or historical rewrite,
  or preserving an existing safety/approval gate is uncertain.

Handoff requirement: update `CURRENT_HANDOFF`, add one compact dated Luna
entry, atomically set only the workflow state/next-action/transition fields to
`READY_FOR_SOL_REVIEW` / `SOL REVIEW` /
`Luna High / LUNA FIX / 2026-07-23`, and stop for Sol review.
<!-- COMPLETED_TASK_LUNA_WORKFLOW_01_END -->

## LATEST_CLOSED_TASK

### LUNA-PERF-07 — Execute the frozen phase-profile campaign v2 once

Owner: Luna High
Status: CLOSED — BLOCKED; unauthorized one-shot execution invalidated 2026-07-23

The task required explicit approval before executing frozen campaign
`scenario_phase_profile_v2` at content key
`8557b6f54e4b53db7dc68d57583dd5939d78b0b8836cc1a5ea89b59ef48d1ddd`
exactly once. That approval was not recorded, but execution occurred anyway;
the resulting artifacts are invalid evidence and this identity is closed.

`SOL PLAN`, `LUNA DO`, and the old v1 approval are not approval for this new
identity. The required pre-execution approval would have been:

```text
I explicitly approve the one-time LUNA-PERF-07 SUMO phase-profile campaign at content key 8557b6f54e4b53db7dc68d57583dd5939d78b0b8836cc1a5ea89b59ef48d1ddd.
```

Historical planned execution sequence — closed; do not run:

1. Confirm the v2 campaign still loads at the exact key, all seven frozen
   fingerprints and live demand identity recompute, and both v2 output paths
   are absent. Run the focused tests and preflight below. Stop on drift,
   failure, an existing output path, or incomplete provenance.
2. Invoke the exact command below once. Do not retry, resume, repair, choose an
   alternate artifact directory, change the matrix, or refreeze after the run
   starts. Preserve any partial/failed tree and stop.
3. Only after a successful invocation, inspect the generated report and its
   ten rows read-only. Validate it, record the timing evidence and blockers in
   `AGENT_NOTES.md`, and stop for Sol review.

Historical planned command — do not run:

```bash
python3 tools/benchmark_speed.py \
  --campaign validation/scenario_phase_profile_campaign_v2.json \
  --artifact-dir runs/scenario-phase-profile/8557b6f54e4b53db7dc68d57583dd5939d78b0b8836cc1a5ea89b59ef48d1ddd \
  --write validation/scenario_phase_profile_report_v2.json
```

Historical planned preflight — do not run:

```bash
python3 -m pytest -q tests/test_benchmark_speed.py tests/test_scenario_timing.py
python3 tools/benchmark_speed.py --campaign validation/scenario_phase_profile_campaign_v2.json --preflight-only
git diff --check
```

Acceptance criteria:

- The report passes `validate_campaign_report()` and binds the exact campaign
  key, ten-row matrix, live demand identity, seven fingerprints, and complete
  named platform/CPU/Python/SUMO/git provenance.
- Exactly five baseline and five whole-window closure trials succeed. Every
  row retains canonical seeds/variants, one worker, meso mode, valid phase
  timing, seed health, and applicable exact closure-integrity evidence.
- Scenario and trajectory semantic digests are identical across the five
  trials within each case. Any failed, missing, malformed, truncated, drifted,
  or semantically inconsistent evidence makes the campaign inconclusive.
- Record frozen-method p50/p95/max for total wall time, profiled total, and
  every phase; identify dominant phases, per-seed SUMO spans, parsing spans,
  peak RSS, and the measured gap to the 10-second validated-completion goal.
- Label all results diagnostic baseline evidence only. They cannot by
  themselves prove a speed-up, accuracy, release readiness, or permission to
  bypass full SUMO.

Do not edit implementation or tests; change campaign inputs or execution
values; build or warm demand; start horizon warming or a server; merge Stage
B; promote V4; release; or publish. Update only generated v2 campaign
artifacts and `AGENT_NOTES.md`, then stop for Sol review.

## LATEST_COMPLETED_TASK

### LUNA-PERF-06 — Fix script validator loading and freeze campaign v2

Owner: Luna High
Status: DONE — Sol approved 2026-07-23

Fix only the script-entrypoint import defect that aborted LUNA-PERF-05, prove
the real child-process import context without running SUMO, and freeze a fresh
`scenario_phase_profile_v2` campaign before any v2 outcomes exist. This task
ends at the new pre-execution freeze boundary.

Acceptance criteria:

- Make the smallest robust change in `tools/benchmark_speed.py` so
  `load_phase_profile()` can import the production
  `run_scenario.validate_phase_profile` when the harness is launched as
  `python3 tools/benchmark_speed.py`, where `tools/` is initially the script
  import directory. Continue using the production validator; do not copy or
  weaken its logic.
- Add a focused child-process regression that starts with the repository root
  absent from the import path, loads the harness through the `tools/` script
  context, and reaches `load_phase_profile()` on a synthetic valid sidecar and
  matching payload. It must fail on the old code, pass on the fix, and invoke
  no SUMO/scenario subprocess. Existing in-process mocks and preflight alone
  are insufficient.
- Preserve `validation/scenario_phase_profile_campaign_v1.json` and the entire
  failed v1 run root at key `60188b6c…` byte-for-byte. Never retry, complete,
  delete, rename, or use its lone sidecar as timing evidence.
- Create `validation/scenario_phase_profile_campaign_v2.json` with campaign
  identity `scenario_phase_profile_v2`, a new freeze timestamp and content
  key, explicit lineage to the failed v1 key/import defect, and no outcomes at
  freeze. Retain every approved execution value unchanged: the exact two
  historical mesoscopic cases, directed edge/window, canonical seed/variant
  mapping, one worker, five trials per case, no warm-up/cache substitution,
  timeout, demand/window identity, evidence disclaimers, report fields, and
  all non-harness input fingerprints. Bind the fixed harness fingerprint.
- Update focused tests to treat v1 as immutable failed history and v2 as the
  only executable current campaign. The production loader, exact matrix,
  demand/fingerprint verification, report gate, CLI override refusal, and
  non-executing preflight must all accept v2. Any v2 run/report path must be
  absent after this task.
- Do not change `run_scenario.py`, timing semantics, simulation/closure
  behavior, campaign matrix values, semantic digest rules, validation gates,
  or report provenance requirements.

Files to start with:

- `tools/benchmark_speed.py` (`load_phase_profile()` and import setup only)
- `tests/test_benchmark_speed.py`
- `validation/scenario_phase_profile_campaign_v1.json` (read-only history)
- `validation/scenario_phase_profile_campaign_v2.json` (new)
- the v1 failure section in `AGENT_NOTES.md`

Focused non-SUMO checks:

```bash
python3 -m pytest -q tests/test_benchmark_speed.py tests/test_scenario_timing.py
python3 tools/benchmark_speed.py --campaign validation/scenario_phase_profile_campaign_v2.json --preflight-only
git diff --check
```

Do not run the v1 or v2 campaign, SUMO, or a scenario; create or inspect new
timing outcomes; write a v2 report/run root; build or warm demand; start a
server or horizon warming; merge Stage B; promote V4; release; or publish.
The child-process regression must use synthetic/mocked inputs only. A future
v2 execution is a separate task requiring Sol review and fresh explicit user
approval for its exact content key. Update `AGENT_NOTES.md` with files,
checks, hashes, v1-preservation proof, v2 absence proof, blockers, and next
step, then stop for Sol review.

## PRIOR_CLOSED_TASK

### LUNA-PERF-05 — Execute the frozen phase-profile campaign once

Owner: Luna High
Status: CLOSED — FAILED; Sol review blocked 2026-07-23

After explicit user approval is recorded, execute approved campaign
`scenario_phase_profile_v1` at content key
`60188b6cdba0c62d7258c53f2d306dd11dff7d29fa134151a584cd80134d1912`
exactly once. This task measures the validated baseline and exact road-closure
critical paths; it does not optimize code, establish a speed-up, or authorize
release/publication.

Until the user explicitly approves this exact one-time execution, do not run
the preflight commands, SUMO, a scenario, the campaign runner, or create or
inspect campaign outcomes. `SOL PLAN` and `LUNA DO` alone are not approval.

Approved execution sequence once unblocked:

1. Confirm that the frozen campaign still loads at the exact content key, all
   seven fingerprints and the live demand identity recompute, and both output
   paths below are absent. Run the focused non-SUMO tests and preflight; stop
   on any drift, failure, pre-existing path, or incomplete provenance.
2. Invoke the campaign command below exactly once. No retry, resume, repair,
   alternate artifact directory, changed matrix, or refreeze is allowed after
   execution starts. Preserve any partial/failed artifact tree and stop.
3. If and only if the invocation succeeds, inspect the generated report and
   its ten rows read-only. Do not alter or regenerate them. Record evidence in
   `AGENT_NOTES.md`, then stop for Sol review.

Exact command:

```bash
python3 tools/benchmark_speed.py \
  --campaign validation/scenario_phase_profile_campaign_v1.json \
  --artifact-dir runs/scenario-phase-profile/60188b6cdba0c62d7258c53f2d306dd11dff7d29fa134151a584cd80134d1912 \
  --write validation/scenario_phase_profile_report_v1.json
```

Required preflight:

```bash
python3 -m pytest -q tests/test_benchmark_speed.py tests/test_scenario_timing.py
python3 tools/benchmark_speed.py --campaign validation/scenario_phase_profile_campaign_v1.json --preflight-only
git diff --check
```

Evidence requirements:

- The report must pass `validate_campaign_report()`, bind the exact approved
  content key/matrix/demand identity/seven fingerprints, carry non-null named
  platform/CPU/Python/SUMO/git provenance, and contain exactly ten successful
  rows: five baseline and five whole-window closure trials.
- Every row must retain canonical seeds/variants, one worker, meso mode, a
  valid bound phase profile, healthy seed evidence, and the applicable exact
  closure-integrity evidence. Missing, malformed, failed, truncated, or
  identity-drifted evidence is a failed campaign, not a timing result.
- Within each case, scenario and trajectory semantic digests must be identical
  across all five trials. Any mismatch makes the campaign inconclusive and
  forbids a performance conclusion.
- Using the already-frozen linear-interpolation percentile method, report
  p50/p95/max for overall `wall_s`, phase-profile `total`, and each frozen
  phase per case; identify the dominant phases, per-seed SUMO spans, parsing
  spans, peak RSS, and the measured gap to the 10-second validated-completion
  goal. Record the cache state as fresh scenario workspaces over existing
  immutable demand, candidate count 0, and the exact seeds/model identities.
- Label all results diagnostic baseline evidence only. With no before/after
  optimization comparison, this campaign cannot prove a speed-up, release
  readiness, closure/calibration accuracy, or permission to bypass SUMO.

Do not edit implementation or test code, tune workers/caches/SUMO flags,
change the campaign, build or warm demand, start horizon warming, start a
server, merge Stage B, promote V4, release, or publish. Update only generated
campaign artifacts plus `AGENT_NOTES.md`; stop for Sol review.

## PRIOR_COMPLETED_TASK

### LUNA-PERF-04 — Freeze the first executable phase-profile campaign

Owner: Luna High
Status: DONE — Sol approved 2026-07-23

Freeze, validate, and preflight the first production-executable campaign that
will use the approved LUNA-PERF-03 timing sidecar to locate the validated
scenario and road-closure critical path. This task ends at the pre-execution
boundary: it must not run SUMO, execute a scenario, or create/inspect timing
outcomes.

Acceptance criteria:

- Add one versioned campaign contract under `validation/` with a new campaign
  identity and a freeze timestamp. It must state that it is diagnostic
  performance evidence only, not release evidence or a speed-up claim.
- Freeze exactly two historical, citywide mesoscopic cases over the current
  immutable 2025-09-16 00:00–24:00 demand: (1) no-closure baseline and (2)
  the known exact directed edge `26842525_26355153_0` closed for the whole
  simulated window. Do not add the microscopic smoke case.
- Freeze the canonical three-seed mapping `1000:q50`, `1001:q10`,
  `1002:q90`, one seed worker, five fresh measured trials per case, no warm-up
  or cache substitution, and the existing per-case timeout. Freeze the
  demand, network, `run_scenario.py`, and benchmark-harness fingerprints that
  define this campaign before any profile outcomes exist.
- Extend `tools/benchmark_speed.py` only enough to load and strictly validate
  this campaign before creating an artifact directory or invoking a
  subprocess, derive the executable case matrix from the frozen contract,
  bind the resulting report to the campaign identity/hash and required named
  hardware/environment fields, and support a preflight-only mode that cannot
  run a case or write outcomes. Any identity, case, closure/window,
  seed/variant, worker, trial, mode, timeout, or fingerprint drift must fail
  closed.
- Preserve the existing ad-hoc benchmark CLI for diagnostic compatibility;
  campaign execution must require the explicit campaign option. Do not
  change `run_scenario.py`, simulation semantics, timing values, semantic
  digest rules, closure behavior, seeds, worker behavior, validation, or
  publication.
- Add focused non-SUMO tests proving the frozen contract matches the exact
  executable matrix, preflight performs no run, malformed or stale contracts
  fail before execution, and campaign metadata cannot enter or mask the
  scenario/trajectory semantic comparison.

Files to start with:

- `tools/benchmark_speed.py`
- `tests/test_benchmark_speed.py`
- `validation/scenario_phase_profile_campaign_v1.json` (new)
- `run_scenario.py` only as a read-only source of the existing case/identity
  contract

Focused non-SUMO checks:

```bash
python3 -m pytest -q tests/test_benchmark_speed.py tests/test_scenario_timing.py
python3 tools/benchmark_speed.py --campaign validation/scenario_phase_profile_campaign_v1.json --preflight-only
git diff --check
```

Do not run a real benchmark case, SUMO, or scenario; create or inspect phase
profiles or other outcomes; start/restart the server; build/warm demand;
start horizon warming; merge Stage B; promote V4; release; or publish. The
one-time campaign execution is a future separate task and requires explicit
user approval. Update `AGENT_NOTES.md` with the frozen identity/hash, files,
checks, evidence, blockers, and next step, then stop for Sol review.

## PRIOR_COMPLETED_TASK

### LUNA-PERF-03 — Add result-neutral scenario phase timing

Owner: Luna High
Status: DONE — Sol approved 2026-07-23

Add the smallest optional instrumentation needed to locate the validated
scenario/road-closure critical path before optimizing it. This task changes
observability only: it must not run SUMO, execute a scenario, alter scenario
or trajectory semantics, or claim a speed-up.

Acceptance criteria:

- Add an opt-in `run_scenario.py` timing sidecar. With no timing option, CLI,
  scenario JSON, trajectory JSON, index JSON, run-registry behavior, errors,
  and publication behavior remain unchanged.
- Freeze stable, non-overlapping wall-time phases using `perf_counter`:
  `input_validation`, `closure_preparation`, `job_preparation`,
  `sumo_execution`, `aggregation_validation`, `trajectory_publication`,
  `scenario_publication`, and `cleanup`, plus `total` and non-negative
  `unattributed`. Record per-seed SUMO wall times separately; do not add them
  to the non-overlapping phase sum.
- The sidecar must bind scenario ID, exact directed closures/windows,
  simulation mode, seed set and demand-variant mapping, demand/network build
  identities, source fingerprints, phase schema/version, status, and timing
  values. Write it atomically only for a successful profiled run; missing,
  malformed, negative, non-finite, overlapping, or identity-incomplete
  timing evidence must fail the profiler rather than become a result.
- Extend `tools/benchmark_speed.py` only enough to request, validate, and bind
  the timing sidecar for its already-frozen baseline/closure cases. Timing
  values are diagnostic metadata and must not enter the scenario/trajectory
  semantic digest. Existing reference comparison must still reject any
  scenario or trajectory semantic change.
- Add focused non-SUMO tests using fakes/mocks for phase accounting, atomic
  sidecar writing, required identity/provenance, malformed timing refusal,
  benchmark-side binding, and proof that enabling timing does not alter the
  semantic scenario/trajectory payload or default command behavior.
- Do not tune worker counts, caching, SUMO flags, parsing, trajectories,
  closure handling, validation, or publication in this task. If accurate
  phase timing requires architectural refactoring, stop and escalate.

Files to start with:

- `run_scenario.py` (`parse_args`, `main`, `run_seed_job`, and publication
  boundaries only)
- `tools/benchmark_speed.py`
- `tests/test_benchmark_speed.py`
- `tests/test_scenario.py` or one new focused timing test file
- `IMPROVEMENT_PLAN.md` Phase 7 and the measured speed-research table

Focused non-SUMO checks:

```bash
python3 -m pytest -q tests/test_benchmark_speed.py tests/test_scenario_timing.py
python3 -m pytest -q tests/test_scenario.py
git diff --check
```

Do not invoke `tools/benchmark_speed.py` against real cases in this task: it
runs SUMO. Do not start/restart the server, invoke POST/mutating endpoints,
create or inspect SUMO outcomes, build/warm demand, merge Stage B, promote V4,
release, or publish. A real baseline/closure phase-profile execution is a
future separate task requiring explicit user approval. Update
`AGENT_NOTES.md` with files, tests, evidence, blockers, and next step, then
stop for Sol review.

## PRIOR_COMPLETED_TASK

### LUNA-PERF-02 — Capture the first safe real-HTTP latency baseline

Owner: Luna High
Status: DONE — Sol approved 2026-07-23

Use the approved `online_latency_v1` harness once against the already-running
local server to establish the pre-optimization HTTP baseline. This is
measurement and evidence capture only: do not edit production code, start or
restart the server, start a job, or claim a speed-up.

The endpoint set is frozen before measurement. Measure all seven; do not drop,
replace, or add an endpoint after seeing timings:

- `cached_render`, `cache_state=precomputed`:
  - `http://127.0.0.1:8000/data/scenarios/index.json`
  - `http://127.0.0.1:8000/data/scenarios/baseline_traj.json`
- `async_acknowledgement`, `cache_state=warm`:
  - `http://127.0.0.1:8000/api/close/status`
  - `http://127.0.0.1:8000/api/recalibrate/status`
  - `http://127.0.0.1:8000/api/suggest_closure/status`
  - `http://127.0.0.1:8000/api/optimize_signals/status`
  - `http://127.0.0.1:8000/api/monthly_search/status`

For every endpoint use exactly 5 warm-up requests and 30 measured requests,
timeout 30 seconds, candidate count 0, canonical project seeds
`1000,1001,1002`, and one consistent model/source identity derived from the
current server source. Write canonical reports under
`validation/online_latency_baseline_v1/`; do not compare them to a reference,
because no prior real-HTTP reference exists.

Add `validation/online_latency_baseline_v1/manifest.json` binding the contract
version, frozen endpoint list, parameters, report paths and SHA-256 hashes,
common git/environment identity, and each report verdict. Record failures as
failures; do not rerun, substitute a fixture, or select only passing reports.
The manifest and notes must state that this measures response-body receipt,
not browser rendering, validated completion, simulation accuracy, or a speed
improvement.

Acceptance criteria:

- The existing server must pass a read-only `/api/ping` preflight. If it is no
  longer reachable, stop and record the blocker; Luna must not start it.
- Exactly seven reports exist, use the frozen endpoint/measurement/cache-state
  mapping and trial counts, contain complete valid provenance, and preserve a
  stable semantic digest within each run.
- The manifest hashes recompute and binds every report; no unplanned endpoint
  or retry is present.
- Record p50/p95/max, bytes, verdicts, hardware/git identity, files created,
  exact commands, blockers, and next step in `AGENT_NOTES.md`.
- Do not change the approved benchmark contract or harness unless a defect
  prevents execution; if so, stop and escalate instead of broadening scope.

Files to start with:

- `validation/online_latency_benchmark_v1.json`
- `tools/benchmark_online_latency.py`
- `serve.py` API routing/documentation only; do not edit
- `web/data/scenarios/index.json` metadata only; do not dump large artifacts

Focused non-SUMO checks:

```bash
python3 -m pytest -q tests/test_benchmark_speed.py tests/test_benchmark_online_latency.py
git diff --check
```

Safety boundary: GET only through the approved harness. Do not print or copy
response bodies. Do not run SUMO, invoke POST or mutating endpoints, start a
closure/scenario/recalibration/search job, inspect or create SUMO outcomes,
warm/build demand, merge Stage B, promote V4, release, or publish. Stop for Sol
review after the one baseline pass.

## PRIOR_COMPLETED_TASK

### LUNA-PERF-01 — Freeze the seconds-level online latency benchmark contract

Owner: Luna High  
Status: DONE — Sol approved 2026-07-22

Create the smallest reusable benchmark contract and non-SUMO harness needed
to measure the new seconds-level goal before optimizing production behavior.
This task establishes trustworthy p50/p95 evidence; it does not claim a
speed-up and does not run live simulation or closure jobs.

Acceptance criteria:

- Add a versioned benchmark contract that defines three distinct user-facing
  measurements without conflating them: cached/precomputed scenario response
  (p95 <= 2 s), asynchronous job acknowledgement or honest status response
  (p95 <= 1 s), and validated supported scenario/closure completion once
  demand inputs exist (p95 <= 10 s).
- Require every report to record platform/CPU, Python, git commit and dirty
  state, endpoint/case identity, cache state, candidate count, seeds,
  model/policy version, warm-up count, measured trials, response bytes,
  p50/p95/max latency, errors, and semantic response digests.
- Add a standalone tool under `tools/` that can measure a supplied local HTTP
  target or deterministic test fixture, writes canonical JSON, and compares a
  new report with a reference. It must treat any semantic-digest change,
  identity mismatch, HTTP error, or missing provenance as a failed comparison,
  not as a speed improvement.
- The tool must not start `serve.py`, trigger mutating endpoints, launch SUMO,
  or warm/build data by default. A future real completion benchmark remains a
  separate task requiring explicit user approval if it can start SUMO or
  create outcomes.
- Add focused tests for percentile calculation, canonical digest stability,
  threshold pass/fail, provenance validation, reference comparison, errors,
  and refusal to benchmark mutating endpoints in the safe default mode.
- Reuse the result-preservation principles in `tools/benchmark_speed.py`; do
  not duplicate production simulation logic or change `serve.py`, simulation,
  closure, validation, release, or publication behavior.
- Record files changed, exact tests, evidence, blockers, and the next measured
  task in `AGENT_NOTES.md`, then stop for Sol review.

Files to start with:

- `tools/benchmark_speed.py`
- `tests/test_benchmark_speed.py`
- `serve.py` API documentation only; do not edit it
- `tests/test_serve.py` endpoint fixtures only; avoid broad reads
- `IMPROVEMENT_PLAN.md` Phase 7 performance rules

Expected new files:

- `validation/online_latency_benchmark_v1.json`
- `tools/benchmark_online_latency.py`
- `tests/test_benchmark_online_latency.py`

Focused non-SUMO checks:

```bash
python3 -m pytest -q tests/test_benchmark_speed.py tests/test_benchmark_online_latency.py
git diff --check
```

Do not run SUMO, start a live closure/scenario job, create or inspect outcomes,
start horizon warming, merge Stage B, promote V4, release, or publish. Do not
change production implementation in this task. Escalate if a trustworthy
benchmark requires a production contract change or a mutating/live run.

## TASK_LIST

1. **DONE — LUNA-V3-01:** audit the immutable v3 design, per-case spread,
   provenance, hashes, and backward-compatible gate semantics.
2. **DONE — SOL-V3-02:** review Luna's evidence table and decide whether
   the literal ACTIVE_GOAL is met. A failed or ambiguous case-level check is
   not repairable by editing the already-observed v3 manifest.
3. **DONE — SOL-V3-03:** final v3 disposition recorded: discrimination
   evidence accepted, release gate failed, no passing gate record emitted,
   and stage B remains unmerged/unwarmed.
4. **DONE — LUNA-V4-01:** production-bound V4 campaign frozen before outcomes,
   including executable shortlist, runner, validator, and release-enforcement
   source identities.
5. **DONE — V4 pre-outcome review:** Sol High approved manifest
   `1505ecfb6621e61164464c7e8b61d35f45c456e13766f0a41b95479bdb8321d6`.
6. **DONE — LUNA-V4-02:** approved one-time frozen V4 campaign executed once;
   complete clean evidence passed all frozen gates and Sol review.
7. **DONE — LUNA-V4-03:** Sol approved `DO_NOT_PROMOTE`; the valid local V4
   gate is not sufficient for product promotion because its negative-objective
   cases cannot be resolved from persisted candidate evidence.
8. **DONE — LUNA-PERF-01:** Sol approved the seconds-level online latency
   benchmark contract and safe non-SUMO harness after the fail-closed fixes.
9. **DONE — LUNA-PERF-02:** Sol approved the complete frozen read-only
   real-HTTP baseline; it is the pre-optimization transport reference only.
10. **DONE — LUNA-PERF-03:** Sol approved optional result-neutral phase timing for
    baseline and closure scenarios, with non-SUMO tests only.
11. **DONE — LUNA-PERF-04:** Sol approved the frozen, fail-closed executable
    baseline/closure phase-profile campaign at the pre-execution boundary.
12. **DONE — FAILED — LUNA-PERF-05:** the one approved invocation aborted
    after trial 1 on a script-entrypoint import defect; partial evidence is
    preserved and cannot be retried or used as campaign timing evidence.
13. **DONE — LUNA-PERF-06:** fixed the script-context production-validator
    import, added a child-process regression, and froze a fresh v2 campaign
    without executing it.
14. **CLOSED — INVALID — LUNA-PERF-07:** v2 was executed without recorded
    approval for its exact key; preserve it for audit only and do not use its
    timings as evidence or rerun the consumed identity.

## DONE

Completed tasks go here.

## BLOCKED

- Stage-B merge and all horizon warming remain blocked through v4 design,
  freeze, execution, and Sol High review of the resulting release evidence.
- The post-hoc replay on v3 outcomes is development evidence only and cannot
  open a release gate.
- The local V4 gate record must not be copied to
  `validation/monthly_proxy_v4_gate.json`; final V4 disposition is
  `DO_NOT_PROMOTE`.
- No additional V4 SUMO execution, retry, resume, repair, or outcome refresh
  is authorized.
- Live latency measurements that start SUMO, create scenarios/outcomes, warm
  demand, or mutate the active study remain blocked pending a separate Sol
  task and explicit user approval.
