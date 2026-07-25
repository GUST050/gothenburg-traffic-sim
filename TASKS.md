# Tasks

<!-- WORKFLOW_CONTROL_START -->
## WORKFLOW_CONTROL

- State: `READY_FOR_SOL_REVIEW`
- Active task: `LUNA-REL-02`
- Task revision: `1`
- Owner: `Luna High`
- Next action: `SOL REVIEW`
- Approval gate: `NOT_REQUIRED`
- Allowed side effects: create local branch `integration/luna-rel-02`; edit
  `.gitignore`; stage only explicit task paths; create exactly three local
  commits; update Luna's terminal workflow triple and handoff. No remote or
  product/runtime side effect.
- Transition: `Luna High / LUNA DO / 2026-07-25`
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

<!-- ACTIVE_TASK_START -->
## ACTIVE_TASK

### LUNA-REL-02 — Integrate the bounded release candidate on a local branch

- Task ID: `LUNA-REL-02`
- Revision: `1`
- Owner: `Luna High`
- Status: `PLANNED — Sol-owned; Luna must not edit this field`
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

<!-- ACTIVE_TASK_END -->

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
