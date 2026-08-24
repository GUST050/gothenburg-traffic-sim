# Road-closure and simulation speed goal — 2026-08-21

> **Current supersession (2026-08-24):** the frozen first-new campaign measured
> 10.496 s p95, and the user accepted the current interactive experience after
> observing roughly 30 s end-to-end in the browser. The 0.496 s miss against
> the original <=10 s target is historical evidence, not remaining work. Exact
> repeat, acknowledgement, monthly-throughput and semantic gates remain as
> documented unless separately superseded.

## Decision and scope

This is the implementation plan for making road-closure work materially faster.
It is ready for Sol, or any other capable actor, to execute phase by phase. It
does not authorize a release claim, a heavy SUMO campaign while another campaign
is live, or a change to traffic meaning.

There are two different performance products and they must not be conflated:

1. **Interactive closure latency** — one new `/api/close` study over an existing
   demand build, plus exact repeat requests and the asynchronous acknowledgement.
2. **Monthly-search throughput** — hundreds or thousands of schedules reduced to
   reusable independent daily units, each evaluated under the frozen robust
   evidence contract.

The primary goal is:

- an exact repeat closure renders at p95 <= 2 seconds;
- an asynchronous start is acknowledged at p95 <= 1 second;
- a supported, previously unseen interactive closure completes and validates at
  p95 <= 10 seconds on a frozen current-demand fixture; and
- a live-shaped exhaustive monthly search completes with at least **2.0x the
  baseline verified-unit throughput**, with a stretch target of <= 24 hours for
  the frozen 1,776-schedule / 2,224-unit fixture.

The product capacity target is now explicitly 50 physical sensor stations.
Sensor count is an independent benchmark axis, not a multiplier on closure
latency: with the same network, vehicles, horizon, seeds and outputs, the
50-station interactive arm must remain p95 <= 10 seconds and regress no more
than 5% against the smaller-station arm. The complete capacity matrix and
user/batch time budgets are in
`docs/plans/FIFTY_SENSOR_PERFORMANCE_CONTRACT_2026-08-22.md`.

The throughput target is relative because machine and demand size matter. The
24-hour target is a stop/go target for the named fixture, not a promise for every
calendar or road.

## Non-negotiable result contract

No speed result is valid if it changes, omits, weakens or relabels any of:

- ScenarioSpec, demand, network, runtime or source identity;
- the ordinary q50 interactive seed mapping;
- the monthly q10/q50/q90 stress arms or their seed mapping;
- closure intervals, rerouter radius, recovery tail or simulation horizon;
- per-seed flows, matched baselines, disruption values, health or integrity;
- loaded/inserted equality, teleports, collisions, running/waiting-at-end rules;
- survivability, failure recall, practical-winner recall, regret or held-out
  gates;
- trajectory provenance and the distinction between a completed result and an
  animation still being built; or
- restart, cancellation, atomic publication and cache-corruption behavior.

Do not gain speed by dropping seeds or stress arms, shortening the one-hour
interactive flush, raising or ignoring the 300-second SUMO failure boundary,
loosening numerical tolerances, changing meso junction semantics, reducing
output precision, or calling proxy output exact.

## Verified baseline and current critical path

### Interactive path

`serve.py::_run_close` launches `run_scenario.py`, currently with
`SCENARIO_SEED_WORKERS = 3`. `run_scenario.py` prepares filtered closure routes,
starts one independent SUMO CLI process per seed, aggregates and validates the
edge output, builds the representative-seed trajectory, and atomically publishes
the scenario and index.

Known measurements are:

| Evidence | Result | Reading |
| --- | ---: | --- |
| Commit `46e7048`, current server wiring | closure 21.6 s -> 13.9 s; baseline 11.0 s -> 5.9 s | Three interactive workers are now adopted after byte-equivalence apart from `generated_at`. This supersedes the older 2026-07-23 current-default decision. |
| `validation/scenario_phase_profile_report_v6.json` | frozen closure p95 17.5994 s -> 10.4234 s; baseline p95 10.4722 s -> 5.883 s | Parallel seeds are a real 40.8–43.8% lever, but that older three-variant fixture still missed the 10-second closure gate. |
| `validation/interactive_closure_p95_catalog_v1_2026-08-24.json` | active catalog release, 10 first-new trials: p50 10.461 s, p95 10.496 s, range 10.409–10.508 s | All scenario/trajectory digests and 30 seed-health records match, but the <=10 s gate misses by 0.496 s. Median phases: SUMO 6.636 s, disruption 1.184 s, trajectory publication 1.131 s. |
| `validation/exact_close_cache_p95_catalog_v1_2026-08-24.json` | 10 exact structured hits: p50 0.312 s, p95 0.329 s, max 0.330 s | Passes the <=2 s cache target; every POST/status proves cache hit, no new start timestamp appears, and scenario/trajectory hashes remain unchanged. |
| `validation/persistent_sumo_campaign_v2_outcome/persistent_sumo_report.json` | persistent 11.3904 s vs fresh subprocess 11.0998 s | Reusing external SUMO processes was equivalent but 2.6% slower. That hypothesis is closed. |
| Historical phase profile | closure preparation about 1.16 s; vehroute parse about 0.6 s | Neither alone explains the remaining gap. Output *generation* cost still needs a paired measurement. |

The current route uses the SUMO CLI, not a step-by-step TraCI controller. This
matters: libsumo's documented advantage is removal of TraCI socket overhead, so
switching this CLI path to libsumo is not a justified first move.

### Monthly path

`run_monthly_closure_search.py` streams the legal schedule ledgers. For
`independent_daily_reset_v1`, `IndependentDailyRunner` deduplicates schedules
into content-addressed daily units. `IsolatedDailySumoRunner` runs up to eight
units concurrently, but starts a new Python interpreter for each unit; inside
that interpreter `ArchivedDemandSumoRunner` runs its variant/seed observations.
Exact daily results and matched baselines are already cached.

The live case `ui-monthly-euc9qp` is the best current scale observation. Its
manifest recorded 459/1,776 parent schedules at 2026-08-21T13:03:19Z, with
2,224 unique daily units, after starting 2026-08-19T18:52:49Z. It was still
running when inspected. This is operational telemetry, not a completed timing
benchmark; later progress must be read from its manifest.

Other measured boundaries are:

| Evidence | Result | Reading |
| --- | ---: | --- |
| `validation/a2_parallel_seed_benchmark_v1.json` | 164.53 s -> 97.49 s, 1.69x; identical; 2.11 GiB peak RSS with eight workers | Independent-process concurrency works, but worker allocation still needs a live-shaped benchmark. |
| Cost-order benchmark v5 | 18 verifications saved over four cases; one case saved 11/13 | Promising call reduction, but strict equivalence failed because timeouts and missing deterministic fields differed. Policy v3 remains closed. |
| Annual multi-snapshot pilot | 104.6 s chained -> 5.04 s in one SUMO process, about 20.8x | Large checkpoint-generation potential, but ordinary SUMO state omits the exact mesoscopic tripinfo accumulator required by this repository. Not adoptable as-is. |

Monthly wall time is therefore primarily a **number-of-exact-SUMO-runs x
per-run-cost / safe concurrency** problem. Enumeration and parent aggregation
are already streamed and are not the first target.

## Primary-source findings and how they apply here

1. SUMO states that the simulation itself runs on one core. Routing can use
   `--device.rerouting.threads`, and duarouter can use `--routing-threads`;
   general `--threads` does not yet give meaningful speedup. The repository
   already uses independent processes and threaded duarouter, so the untested
   in-simulation routing option is the relevant narrow experiment.
   [SUMO FAQ](https://sumo.dlr.de/docs/FAQ.html#can-sumo-be-run-in-parallel-on-multiple-cores-or-computers)
2. SUMO's mesoscopic model can be up to 100x faster than microscopic simulation.
   This program already uses meso for citywide closure work, with bounded
   junction semantics. Switching model class is not a remaining speed lever.
   [SUMO mesoscopic simulation](https://sumo.dlr.de/docs/Simulation/Meso.html)
3. Libsumo removes TraCI protocol/socket overhead, but parallel Python libsumo
   instances require multiprocessing. That can matter in the warm-state
   controller, not automatically in the interactive CLI path. The valid
   persistent-process experiment already showed process reuse did not help its
   frozen interactive case.
   [SUMO libsumo](https://sumo.dlr.de/docs/Libsumo.html)
4. `traci.simulation.loadState` avoids reloading the network and can be much
   faster than `traci.load`. Future departures are not stored in a state, RNG
   state is not stored unless explicitly requested, RNG states are platform
   dependent, and some internal vehicle-model state is not serialized. Any
   targeted warm replay must therefore retain route input and the repository's
   existing RNG, precision, prefix-evidence and cold-fallback gates.
   [SUMO save/load](https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html)
5. SUMO describes Dijkstra as the simplest and slowest routing algorithm, A* as
   often faster, ALT as a precomputed acceleration, and contraction hierarchies
   as efficient for many queries but unsuitable for ordinary time-dependent
   weights without time-slice preprocessing. Algorithm changes may alter chosen
   equal-cost routes, so speed alone is insufficient; this repository must
   compare exact semantic output.
   [SUMO routing algorithms](https://sumo.dlr.de/docs/Simulation/Routing.html#routing-algorithms)
6. duarouter supports bulk routing, routing threads and Dijkstra/A*/CH variants.
   The repository already measured 2.2x routing speed from `--routing-threads`
   with equivalent route bodies. Do not count that completed work again as a
   future simulation improvement.
   [SUMO duarouter options](https://sumo.dlr.de/docs/duarouter.html)
7. Simulation-optimization research supports sequential elimination and
   multi-fidelity filtering when formal correctness guarantees are defined and
   the high-fidelity simulator remains in the loop. These are research leads,
   not permission to activate the failed monthly proxy or remove the exact
   release path.
   [Frazier 2014](https://doi.org/10.1287/opre.2014.1282),
   [Boesel, Nelson and Kim 2003](https://doi.org/10.1287/opre.51.5.814.16751),
   [Peherstorfer, Willcox and Gunzburger 2018](https://doi.org/10.1137/16M1082469)

## Ranked work programme

The expected-benefit labels are hypotheses unless a local artifact above
already measured them.

| Rank | Work item | Affects | Expected benefit | Risk | Reversibility |
| ---: | --- | --- | --- | --- | --- |
| 0 | Freeze phase/resource baselines after the live search | both | Enables honest decisions; no direct speed | Low | Complete |
| 1 | Benchmark in-SUMO rerouting threads and A* under one total process budget | both first-run paths | Medium if routing is material; otherwise closes cheaply | Medium semantic risk | Flag-off |
| 2 | Add exact whole-query reuse for interactive repeats | repeat closures | Very high on a hit; zero for a new closure | Low if fully keyed | Delete/disable cache |
| 3 | Tune monthly outer-unit vs inner-seed allocation with resource isolation | monthly | Existing evidence supports up to 1.69x, exact gain unknown | Medium operational risk | CLI/default rollback |
| 4 | Repair and revalidate exact cost-ordered stopping | monthly | Prior four-case run saved 18 verifications; potentially large | High evidence risk | Keep exhaustive default |
| 5 | Build only warm prefixes whose ledger reuse exceeds measured break-even | monthly, later-start closures | Potentially high for repeated date/start slots | High exactness/storage risk | Cold fallback and discard store |
| 6 | Measure then optionally retain isolated Python workers | monthly | **Closed:** live-shaped diagnostic found only 2.7% | Medium lifecycle risk | Per-unit child retained |
| 7 | Measure trajectory-output generation and conditionally split enrichment | interactive | Unknown; only useful if output writing is material | Medium product-contract risk | Keep same-run trajectory |
| 8 | Versioned SUMO patch for exact meso accumulator serialization | warm generation | 20.8x measured for state generation only | Very high maintenance risk | Pin official runtime |
| 9 | libsumo migration | warm controller only | Low priority until socket time is measured | High architecture risk | Keep external SUMO path |

## Phase S0 — instrument and freeze the baselines

Implementation status: the interactive `PhaseTimer`/timing-sidecar path is
present, and independent-daily execution now records result-neutral cache
verification, cache writes, worker wall time and simulated-unit counters in
the resumable workspace progress detail. The named monthly run was stopped at
476/1,776, so its terminal throughput baseline still needs a new isolated
reference campaign; telemetry alone is not a performance result.

Do this only after `ui-monthly-euc9qp` has reached a terminal state or has been
explicitly paused. Do not compete with it for CPU, memory or demand-workspace
ownership.

### Work

1. Preserve its final manifest and report completed schedules, completed unique
   units, cache hits/misses, elapsed active wall time, failure/timeout counts and
   final status. Do not promote it to release evidence merely because it
   finished.
2. Extend timing sidecars without changing artifact identity:
   - interactive: input validation, closure preparation per variant, per-seed
     SUMO wall time, edge/stat/vehroute parse, aggregation, trajectory writing,
     publication and cleanup;
   - monthly unit: worker spawn/import, archive/provenance preparation, baseline
     cache lookup/build, closure preparation, each SUMO observation, parsing,
     evidence serialization and cache publication;
   - host: active SUMO/Python process count, CPU utilization, peak RSS, disk
     bytes and involuntary failures.
3. Freeze two benchmark inputs:
   - current q50-only interactive baseline plus one whole-window closure on the
     active demand build; and
   - a live-shaped independent-daily monthly fixture plus the existing four-case
     cost-order suite.
4. Run at least ten interactive trials per arm and enough monthly unit batches
   to estimate p50/p95 unit latency and verified units/hour. Separate cold cache,
   warm cache and exact result-cache cases.

### Files

- `run_scenario.py`, `tools/benchmark_speed.py`,
  `tests/test_scenario_timing.py`, `tests/test_benchmark_speed.py`
- `traffic_sim/simulation/independent_daily.py`,
  `traffic_sim/simulation/independent_daily_worker.py`,
  `traffic_sim/simulation/monthly_sumo.py`
- a new process-free contract and executable harness under `validation/` and
  `tools/`; freeze the contract before generating an outcome

### S0 gate

The benchmark must record platform, SUMO/Python/git identity, full inputs,
cache state, worker allocation, all trial values, p50/p95/max, peak RSS,
timeouts and semantic digests. An unbound timing note is diagnostic only.

## Phase S1 — safe first-run and repeat improvements

Implementation status: exact structured interactive reuse is implemented in
`serve.py` with schema-v2 provenance, direct input/runtime/source fingerprints,
scenario/trajectory byte-digest checks, workspace-locked verification,
same-request single-flight, stable pre/post-run identity and malformed-artifact
refusal. A cache hit carries no fresh start-time claim. The routing matrix below
is available only through explicit CLI experiment flags that require isolated
output and timing paths and reject live publication. Locked verification uses
an explicit `checking_cache` API state; only a miss claims `running` and a new
start timestamp. No routing arm is adopted as a production default.

### S1A: routing matrix

Add optional, default-neutral parameters to the shared SUMO command builder and
test this bounded matrix under a fixed maximum of eight simultaneous SUMO/routing
threads:

- Dijkstra with rerouting threads 1, 2 and 3;
- A* with rerouting threads 1, 2 and 3; and
- only if the profile shows many repeated static routing queries, one separate
  ALT experiment with a content-addressed landmark table.

Do not test generic `--threads`. Do not adopt CH/CHWrapper for time-dependent
or permission-sensitive closure routing without a separate semantic argument.
Compare every scenario/trajectory digest, per-seed edge series, closure
integrity, health, disruption and route evidence. If every exact arm is slower
or changes output, record a no-go and remove the experimental default.

### S1B: exact whole-query reuse

Create a content-addressed result index whose key includes at least:

- canonical ScenarioSpec and closure intervals;
- demand build key plus exact route fingerprints and seed/variant mapping;
- network, SUMO version/platform, simulation flags and source fingerprints;
- output/trajectory configuration, validation schema and publication schema.

A hit must verify both the scenario and trajectory bytes/digests before serving
the original provenance. It must never claim a new run occurred. A miss follows
the unchanged simulation path. Concurrent identical misses need single-flight
deduplication, with waiters observing the same terminal result; a failed leader
must not publish a cache entry.

Target: ten repeat requests, zero SUMO invocations, identical bytes apart from
explicitly volatile delivery fields, p95 <= 2 seconds.

### S1C: trajectory-output measurement

Run the representative seed with and without vehroute output using the same
closure inputs. If SUMO output generation plus parsing is less than 5% of
validated completion, close this line. If it is material, design a versioned
two-stage product:

- the validated flow result may become `done` only if the existing product
  contract permits a result without animation;
- trajectory enrichment runs against the exact same ScenarioSpec/build/seed and
  publishes `trajectory_status` plus its semantic digest; and
- the UI never shows a stale trajectory or labels enrichment complete early.

This is conditional because a second SUMO run may save perceived latency while
increasing total work; that is acceptable only if the product distinction is
explicit and measured.

## Phase S2 — monthly throughput and deterministic capacity

Implementation status: the monthly CLI now accepts one declared
`--max-active-sumo-slots` budget and refuses worker combinations that exceed
it. Nested daily plus seed parallelism remains refused because the isolated
children and shared baseline cache have not passed their concurrency/equivalence
gate. A production-shaped cold-cache diagnostic has now reproduced that race:
three workers can calculate one identical baseline and fail when publishing it.
This is a safety guard, not an adoption claim; matched-baseline single-flight
must precede the resource arms. The detailed structure and test contract are in
`docs/plans/DAILY_SIMULATION_CONCURRENCY_STRUCTURE_2026-08-21.md`.

### S2A: one resource scheduler

The current layers can multiply concurrency: daily-unit workers, per-candidate
seed workers, routing threads and concurrent demand/PFE work. Replace independent
defaults with one declared process budget. Benchmark, at minimum:

Before raising the outer-worker count, put a per-content-key cross-process lock
around matched-baseline cache misses. Recheck and verify after acquiring the
lock, let one winner publish atomically, and make waiters consume that exact
artifact. Prefilling the known baseline keys before fan-out may reduce latency,
but it is not a substitute for the lock on restart or unexpected misses.

| Arm | Daily-unit workers | Inner seed workers | Rerouting threads | Maximum active routing/SUMO slots |
| --- | ---: | ---: | ---: | ---: |
| Reference | 8 | 1 | 1 | 8 |
| A | 4 | 2 | 1 | 8 |
| B | 2 | 3 | 1 | 6 |
| C | 4 | 1 | 2 | 8 |

Demand building and evidence-producing SUMO should use separate phases unless a
paired benchmark proves overlap improves total throughput without load-sensitive
timeouts. Preserve canonical consumption order so speculative concurrent work
cannot change which hard failure is published.

Adopt an arm only if it has identical evidence and restart state, at least 15%
better verified units/hour, peak RSS <= the existing 8 GiB benchmark budget,
and no increase in failures or timeouts. Keep the best passing arm as the new
reference; do not stack percentage claims from separate fixtures.

### S2B: persistent *Python* workers, only after profiling

This is different from the closed persistent-SUMO hypothesis. A bounded pool of
Python worker processes may amortize interpreter imports and preload immutable
network/archive metadata, while each task still starts and owns fresh SUMO
processes.

**Measured 2026-08-21 — generic pool line closed.**
`validation/daily_worker_pool_diagnostic_2026-08-21.json` replayed two exact
July 15 units from the stopped `ui-monthly-euc9qp` workspace against its frozen
demand release in the cold-execution arm. The current fresh-interpreter arm took 54.445 s; one reusable
spawn worker took 53.027 s, a 1.027x speedup (2.7%). Evidence objects and their
digests were exactly equal. The pool ran second, so it received the favourable
filesystem-cache condition, yet still missed the 1.10x continuation threshold.
The stronger production-shaped test in
`validation/daily_worker_pool_structure_diagnostic_2026-08-21.json` gave both
arms separate prewarmed baseline caches, then ran six units in two waves of
three workers. Fresh one-shot workers took 61.040 s; the three-member spawn pool
took 61.144 s, or 0.998x. All six evidence digests were identical. Peak worker
RSS was approximately 1.00–1.02 GiB and peak SUMO-child RSS about 235 MiB.
No production pool was enabled. Do not spend lifecycle complexity on a standard
interpreter pool or a larger counterbalanced campaign unless another profile
shows a materially different setup fraction. A date-affinity metadata cache
would be a different design and must first show that metadata construction—not
SUMO—is at least 5% of unit wall time.

Required lifecycle: request/result schema validation, private task directory,
max-tasks-per-child recycling, retire-on-exception, bounded task timeout,
terminate-and-reap on cancellation, cold per-unit fallback, no inherited TraCI
connection, and source/runtime identity in every result. If worker/import/setup
is below 5% of unit wall time or the pool improves throughput by less than 10%,
close the line.

### S2C: cache efficiency

Keep the existing stable backend identity that intentionally excludes search id.
Add telemetry rather than weakening it: hit/miss/corrupt counts, miss reason,
bytes read/written and time spent verifying. Optimize directory lookup or add an
index only if verification/I/O is at least 5% of unit wall time. A cache index is
advisory; the content-addressed artifact and digest remain authoritative.

## Phase S3 — reduce exact SUMO calls

### S3A: repair cost-ordered equivalence before retrying adoption

Do not reactivate policy v3. Build a new version only after these defects from v5
are removed in both exhaustive and cost-ordered arms:

1. A timeout must carry the same deterministic disruption fields as a pre-SUMO
   ledger entry.
2. Treat wall-clock timeout as an explicit undecided execution outcome. Define a
   frozen retry/resource protocol shared by both arms; do not silently increase
   the existing threshold or let arm order decide whether a candidate gets more
   CPU.
3. Run arms under the same isolated resource budget and compare every candidate
   observation, hard failure, health field, selected id, winner, restart cursor,
   cache event and stop proof.
4. Freeze the multi-case registration before outcomes. Require all cases to be
   field-equivalent, zero load-dependent outcome crossings, and at least 30%
   fewer new SUMO observations in aggregate. One case saving calls is not enough.

Cost may order exact work immediately after this gate. It may stop exact work
only under the separately proven stopping theorem and practical-equivalence
band. Until then, exhaustive remains the product default.

### S3B: targeted on-demand warm prefixes

Do not populate another annual bank first. Count reuse directly from the frozen
daily-unit ledger for each `(date, start slot, variant, seed)` prefix. Measure:

- `C_cold`: cold observation cost;
- `C_warm_build`: exact prefix creation and certification cost; and
- `C_post`: restored post-prefix observation cost.

Warm only identities whose reuse `n` satisfies
`C_warm_build + n*C_post < n*C_cold` with a 20% safety margin. Start with the
highest-reuse slots in a bounded date range. Use the existing
`WarmStateIdentity`, RNG/precision settings, prefix evidence, atomic store,
verification and cold fallback. `simulation.loadState` may be prototyped because
it avoids network reload, but it must pass exact cold/warm observation equality,
future-departure coverage, meso time-loss reconstruction, closure throughput,
health, recovery, restart and cross-platform refusal.

Stop if no live-shaped prefix reaches break-even or if any exactness field needs
a new tolerance.

## Phase S4 — high-risk research, not the default roadmap

### Exact mesoscopic state serialization

The one-process multi-snapshot pilot is the largest measured mechanism-level
opportunity: 104.6 to 5.04 seconds for checkpoint generation. Pursue it only if
S2/S3 leave warm-prefix creation material. Use a pinned, versioned SUMO build
that serializes or exactly exposes `MSDevice_Tripinfo::myMesoTimeLoss`; never
patch around it by accepting a different number.

Required evidence: upstream-source test, state schema/version refusal, active
vehicle save/load, all 96 boundaries, several dates/variants/seeds, exact tripinfo
and objective reconstruction, crash/orphan/resume, official-runtime fallback,
and a full golden A/B. The custom runtime hash becomes part of every state and
result identity. Prefer an upstreamable patch; a permanent private simulator fork
is a maintenance cost that must be justified by at least 2x end-to-end monthly
throughput, not merely a 20.8x subphase.

### Statistical and multi-fidelity search

Common random numbers are already represented by matched seeds and must remain.
Sequential elimination or a surrogate can be researched as an *ordering* or
preview aid without changing exact output. Any proposal that actually skips
high-fidelity candidates needs a new preregistered statistical correctness and
held-out contract, including probability of correct selection, regret, failure
recall and out-of-domain refusal. The current failed monthly proxy cannot be
relabelled or reused as that proof.

## Adoption ladder and rollback

Every phase follows the same ladder:

1. process-free contract and tests;
2. diagnostic paired benchmark on immutable inputs;
3. semantic/evidence review;
4. opt-in production flag with the old path as fallback;
5. golden A/B plus restart/cancel/cache-corruption tests;
6. default change only after the phase target passes; and
7. rollback by disabling the flag/default and invalidating only artifacts whose
   identity includes that implementation.

An experiment is a no-go if it changes a required digest/field, produces a new
timeout/failure, exceeds the memory budget, cannot clean up child processes, or
misses its phase improvement floor. Record the no-go so it is not repeatedly
reopened. Never rewrite or delete the failed campaign evidence.

## Final acceptance matrix

| Product | Required performance | Required equality/evidence |
| --- | --- | --- |
| Exact interactive repeat | p95 <= 2 s over >= 10 trials; zero SUMO calls | full cache key and artifact digests; original provenance |
| Async acknowledgement | p95 <= 1 s over >= 30 loopback trials | correct running/inconclusive/no-viable state; no false completion |
| New interactive closure | p95 <= 10 s over >= 10 paired trials | scenario + trajectory semantic digest, every seed flow/health/integrity field |
| Monthly unit engine | >= 1.15x verified units/hour per adopted resource step | byte/field-identical evidence, restart and cache behavior; <= 8 GiB peak RSS |
| Monthly combined goal | >= 2.0x baseline verified-unit throughput; stretch <= 24 h on named fixture | same legal candidates, observations, failures, winner, recall/regret and provenance |
| High-risk runtime | >= 2.0x end-to-end monthly throughput | all cold/warm exactness and lifecycle gates; versioned runtime identity |

## Recommended first Sol task

Wait for the current monthly search to finish or be explicitly paused. Then
implement **S0 only**: archive its terminal telemetry, add non-semantic phase and
resource timing, freeze the interactive and live-shaped monthly benchmark
contracts, run the references in an otherwise idle environment, and report the
ranked measured budget. Do not combine S0 with a production optimization.

After S0, the first implementation experiment should be S1A if rerouting is a
material phase, otherwise S2A. S1B may proceed independently because it affects
exact repeats rather than first-run simulation throughput.
