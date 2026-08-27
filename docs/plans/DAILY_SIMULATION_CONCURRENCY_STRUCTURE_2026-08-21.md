# Daily simulation concurrency structure — 2026-08-21

## Decision

Keep the existing one-shot Python worker per daily unit. Do not replace it with
a standard persistent `multiprocessing.Pool`. Put the next implementation effort
into a content-keyed, cross-process single-flight baseline cache and then
benchmark one bounded outer/inner concurrency budget.

This is a structure decision, not a claim that the monthly 2.0x goal is met.

## What the repository does now

The monthly orchestrator consumes units in canonical order.
`IsolatedDailySumoRunner` uses a bounded thread executor to launch an isolated
Python worker for each daily unit. That process builds an
`ArchivedDemandSumoRunner`, starts fresh SUMO children, writes only to its task
directory and returns a schema-checked result. The parent is the only authority
that publishes results.

This shape has useful failure boundaries, but concurrent units with the same
archive, variant, seed and window share one matched-baseline cache key. The
current cache does an unlocked `exists -> simulate -> exists -> atomic replace`
sequence. It neither serializes the expensive baseline calculation nor makes
waiters consume the winner. This is the first concurrency defect to repair.

## Local evidence

All timings below use frozen units from the stopped, resumable
`ui-monthly-euc9qp` workspace. They are diagnostics, not release evidence.

### Cold empty-cache concurrency

Six q50 units with three current one-shot workers and private empty baseline
caches failed reproducibly before the pool arm. Multiple workers ran the same
baseline and one raised `FileExistsError` when another worker published first.
The source- and input-bound failure is in
`validation/daily_worker_pool_cold_start_failure_2026-08-21.json`.

`tests/test_monthly_sumo.py::test_identical_concurrent_baselines_are_single_flight`
now states the desired contract as a strict expected failure: one baseline SUMO
execution, two identical successful consumers. An XPASS must be reviewed and
converted to an ordinary passing regression when the lock is implemented.

### Equal-cache steady-state concurrency

Both arms received a separate, prewarmed baseline cache; prewarm time was
excluded. Each timed arm ran six q50 units in two waves of three concurrent
workers, with fresh SUMO per task:

| Arm | Python worker PIDs | Wall time | Relative result |
| --- | ---: | ---: | ---: |
| Current one-shot workers | 6 | 61.039938 s | reference |
| Reusable spawn pool | 3 | 61.144384 s | 0.998292x |

All six evidence objects and digests were exactly equal. The pool ran second,
with favourable filesystem-cache order, but was still 0.17% slower. Worker peak
RSS was about 1.00–1.02 GB and the SUMO child peak was about 235 MB. The bound
report is
`validation/daily_worker_pool_structure_diagnostic_2026-08-21.json`.

The earlier serial two-unit diagnostic reported only 1.027x. The larger,
production-shaped test removes the remaining reason to adopt a generic pool.
Interpreter reuse is not the limiting resource here.

## Primary-source constraints

- SUMO documents parallel independent runs (including `runSeeds.py --threads`)
  as the normal multi-core shape. A single simulation is not the main place to
  expect broad core scaling. See the
  [SUMO parallel-run FAQ](https://sumo.dlr.de/docs/FAQ.html#can-sumo-be-run-in-parallel-on-multiple-cores-or-computers)
  and [randomness/reproducibility contract](https://sumo.dlr.de/docs/Simulation/Randomness.html).
- On macOS, Python uses `spawn`; `fork` is documented as unsafe in a
  multithreaded process. Pools need explicit lifecycle management and
  `maxtasksperchild` only recycles Python members. More importantly,
  terminating a member does not terminate its descendants, so a SUMO child can
  be orphaned. See the
  [Python multiprocessing documentation](https://docs.python.org/3/library/multiprocessing.html).
- Libsumo avoids the TraCI socket but parallel Python instances still require
  multiprocessing. That changes simulation ownership and evidence behavior; it
  is not a drop-in worker-pool optimization. See
  [Libsumo limitations](https://sumo.dlr.de/docs/Libsumo.html).
- SUMO state loading can save network initialization, but RNG state is not
  saved by default and some lane-change/car-follow state is not saved at all.
  This repository's failed exact warm-state work therefore remains a separate,
  evidence-gated design. See
  [SUMO SaveAndLoad](https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html).
- This host runs Python 3.9.6. Python 3.9 reached end of life on 2025-10-31;
  upgrade planning is warranted for support and newer executor lifecycle APIs,
  but an upgrade cannot create a speedup that the equal-cache test did not
  measure. See the
  [Python version status](https://devguide.python.org/versions/).

## Recommended production structure

1. **One parent authority.** The monthly orchestrator owns canonical ordering,
   restart ledgers, publication and the total active-SUMO budget.
2. **Group immutable inputs.** Resolve and verify a demand archive once, group
   work by archive/backend identity, and do not mutate the live demand release
   while evidence-producing children run.
3. **Single-flight every baseline key.** Use a per-key cross-process lock in the
   baseline cache directory. After taking the lock, re-read and fully verify the
   cache. The winner runs SUMO and atomically publishes; waiters verify and
   return the winner. A failed winner publishes nothing. Locks need bounded
   wait, recorded owner identity and stale-owner recovery based on process
   liveness—not age alone.
4. **Prefill known baseline keys.** Once single-flight exists, compute the small
   known set of archive/variant/seed/window baselines before fanning out closure
   units. Prefill is an optimization; the per-key lock remains the correctness
   mechanism for restart and cache misses.
5. **Keep one-shot daily workers.** Each worker owns its runner, fresh SUMO
   descendants, task directory and result schema. It cannot publish global
   state. Reaping the unit's process group is simpler than reusing members that
   have spawned external children.
6. **Use one slot budget.** Benchmark outer daily workers versus inner seed
   workers under one maximum. Do not multiply independent defaults. Keep
   canonical result consumption even when execution completes out of order.
7. **Cancel by owned process group.** The top-level CLI/server job must own a
   new process session, signal that group, wait, escalate if necessary and
   verify that no SUMO descendants remain. Do not treat Pool termination as
   descendant cleanup.

In compact form:

```text
monthly parent (ordering, ledger, publication, slot budget)
  -> verify/group immutable archive inputs
  -> single-flight + prefill baseline keys
  -> bounded one-shot daily workers
       -> one task directory
       -> fresh SUMO child/children
       -> schema-checked evidence only
  -> canonical consume/commit
```

## Required tests before increasing concurrency

1. Two and then three processes request one empty baseline key: exactly one
   baseline SUMO call, all consumers return byte-equivalent verified evidence.
2. Winner crash before publication: no cache artifact; one waiter may take over
   and publish exactly once.
3. Corrupt cached payload: every consumer fails closed with the same bounded
   diagnosis; no overwrite disguises corruption.
4. Timeout/cancel during baseline and closure work: no surviving worker, SUMO
   child, held lock or partial cache file.
5. Restart while a baseline lock is held: a live owner is never stolen; a dead
   owner can be recovered without accepting a partial artifact.
6. Frozen 6-unit/3-worker and a longer multi-date run: exact evidence,
   canonical failures and restart ledger match the one-worker reference.
7. Resource matrix (for example 8x1, 4x2, 2x3): report verified units/hour,
   p50/p95 unit time, peak aggregate RSS, failure/timeout count and actual
   maximum SUMO children. Adopt only a measured improvement under the plan's
   8 GiB and equivalence gates.

## Deferred alternatives

A date-affine persistent service is not a standard pool. It would need custom
key-to-worker routing, a supervisor that owns process groups, health checks,
member retirement, bounded queues, backpressure and cold fallback. Reconsider
it only if profiling shows archive/runner construction is at least 5% of unit
wall time and an isolated prototype clears 10% throughput with exact evidence.

Libsumo and save/load remain higher-risk experiments. They must pass the same
per-seed flow, health, recovery, trajectory, restart and cancellation contracts;
neither should block the lower-risk baseline single-flight and resource-matrix
work.


## 2026-08-27 — the width was never the binding constraint

The resource matrix above assumed the configured width was the thing to tune.
Production measurement says otherwise: the campaign ran with `--daily-workers 8
--max-active-sumo-slots 8` and reached ONE.

Frozen from the live campaign immediately before it was stopped
(`validation/monthly_global_queue_baseline_2026-08-27.json`):

| quantity | measured |
|---|---|
| worker-seconds | 80 330.94 |
| active elapsed seconds | 88 771.27 |
| worker/active ratio | 0.905 |
| effective utilization of 8 slots | 11.3% |
| live samples showing 1 worker | 20 / 20 |
| live samples showing >1 SUMO | 0 / 20 |
| cache hits vs misses over 816 parents | 3 229 vs 851 |
| genuinely new units per five-day parent | 1.04 |

The cause is structural, not a tuning error. `run_candidate` collected ONE
parent's pending units and handed that list to
`IsolatedDailySumoRunner.run_candidate_batch`, whose pool is sized
`min(unit_workers, len(requests))`. A five-day parent can supply at most five
units, and once the cache is warm it supplies about one. The pool was therefore
sized 1 no matter what the operator asked for.

### What replaced it

`GlobalDailyUnitQueue` in the orchestration-only `independent_daily.py`, opt-in
behind the environment pair `TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS` +
`TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_SCREENING=independent-exhaustive`. It is
deliberately NOT a CLI flag: `run_monthly_closure_search.py` is one of the
nineteen files `monthly_sumo.py` hashes into `source_digest`, which rides in
the backend provenance the daily-unit cache key is built from, so a flag there
moves cache identity (measured: c0bbfc32... -> 8b040d90...) and orphans every
cached unit. The screening declaration is a safety gate: global lookahead
would simulate the work a `independent-cost-ordered-exact` stop proof claims
to have skipped, so the resolver fails closed on a missing, unknown or
command-line-contradicting value. Missing units are enumerated ONCE across the
whole shortlist in canonical unit order and served by exactly `workers` puller
threads. A parent promotes its own units to the front and waits only for those;
the remaining threads run lookahead that lands in the shared cache for later
parents.

Properties, each pinned by a test in `tests/test_independent_daily_queue.py`:

- **The width is the ceiling, not a check.** There are exactly `workers`
  threads and each runs one unit synchronously, so at most `workers` isolated
  workers - hence at most `workers` SUMO processes - can exist. The same fact
  is the backpressure: pullers take one item at a time, so outstanding work
  never exceeds the width.
- **Single-flight with a post-lock recheck.** Take the cross-process `flock`
  for the content key, RE-READ and fully validate the cache, skip if another
  producer won, otherwise execute once and publish atomically as the last step
  inside the lock. A race costs a filesystem read, not a duplicate SUMO run.
- **Completion order cannot reach the evidence.** A parent assembles its result
  from the cache in its own canonical unit order, so a randomized-jitter run is
  byte-identical to the legacy path.
- **Coverage is part of the work identity.** A finalist round asking for more
  repetitions retires the queue and rebuilds it from a fresh coverage scan, so
  it can never hand back pilot-only evidence.
- **Cancellation reaps.** `cleanup()` clears the remainder and shuts the pool
  down with `wait=True`, so it blocks until real subprocesses are reaped rather
  than orphaning SUMO children. An interrupted unit publishes nothing.
- **Retirement never deadlocks.** A queue is stopped OUTSIDE `_state_lock`,
  under a separate build lock the pullers never take. Stopping it inside
  `_state_lock` - the obvious shape - hangs on the first finalist retarget,
  because `stop()` joins pullers that are blocked wanting that same lock.
  Pinned by `test_retargeting_does_not_deadlock_against_a_running_worker`,
  which hangs on the pre-fix code.

### Measured (`validation/monthly_global_queue_benchmark_2026-08-27.json`)

SYNTHETIC SCHEDULER SCALING. 180-unit sliding five-day fixture with a sleeping
stand-in in place of SUMO; every arm replays the SAME seeded per-unit cost
profile, so a wide arm cannot win by drawing cheaper units. These numbers
describe the scheduler, not per-unit SUMO cost.

| arm | wall | achieved width | speedup |
|---|---|---|---|
| legacy parent-local | 170.33 s | 0.999 | 1.00x |
| global queue w1 | 170.25 s | 0.999 | 1.00x |
| global queue w2 | 85.31 s | 1.995 | 2.00x |
| global queue w4 | 42.91 s | 3.965 | 3.97x |
| global queue w8 | 21.89 s | 7.771 | **7.78x** |

Cache bytes were identical across every arm; the harness refuses to report a
speed number otherwise. Note that `global queue w1` reproducing legacy exactly
is the control: it shows the gain is the WIDTH, not incidental rework.

SAVED REAL OBSERVATION, not repeated since. One cold SUMO arm at width 8
reached a maximum of 8 concurrent isolated workers and 8 concurrent SUMO
processes over 170 samples and never exceeded either. That run predates the
present activation seam, so its recorded command line still shows the removed
`--global-daily-queue` flag; the scheduler it exercised is the one that ships.
It was sampled by process ancestry rather than by `grep`, and
`tools/benchmark_independent_daily_queue.py` now implements ancestry sampling
so the tool matches the claim - the earlier grep-based sampler counted every
SUMO on the machine and could be inflated by an unrelated campaign.

The campaign ETAs in the report - 2.93 h resume, 6.58 h cold at width 8 - are
PROJECTIONS multiplying production's 94.396 s/unit by the measured width. No
full campaign has been run at any width, and per-unit cost under sustained
eight-way contention is unmeasured; the report records that more than ~21%
per-unit inflation would put the eight-hour goal at risk.

### How to enable it (and what it does NOT do)

The queue is off by default. Both variables are required, in the environment
of whichever process launches the search:

```sh
export TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS=8
export TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_SCREENING=independent-exhaustive
```

For the web UI this means exporting them BEFORE starting `serve.py`.
`run_in_new_session` does not pass an explicit `env`, so the CLI child
inherits them, and `monthly_screening_cli_args` already sends
`--screening-mode independent-exhaustive` for every independent-daily spec,
so the resolver's command-line cross-check agrees. For a direct CLI run,
export them in the same shell.

Turning the queue on does NOT restart anything by itself, does not change any
cache key, and does not change what a unit computes. With either variable
unset the legacy parent-local path runs bit-for-bit as before.

### Four defects found in review, and what now prevents them

Recorded because each was invisible from the outside and each is now pinned by
a test that fails against the pre-fix code.

**1. Global lookahead upgraded units nobody selected.** `_ensure_queue()`
rebuilt its remainder from every prepared unit whenever the target coverage
changed. Under the exhaustive PILOT sweep that is free - every prepared unit is
verified anyway, so the queue only reorders committed work. A FINALIST round is
different: the policy promotes at most 12 parents and asks them for 4
repetitions, adapting to 12, so a global rebuild at finalist coverage would
have upgraded all 1 950 prepared units, and an adaptive bump would have ordered
it again. Global lookahead is now permitted for `stage == "pilot"` only
(`QUEUE_LOOKAHEAD_STAGE`); every other stage gets a queue scoped to the units
it actually asked for.

**2. The width was not bound to the SUMO budget.** It was validated only as a
positive integer. Two configurations passed every existing check and broke the
eight-SUMO ceiling anyway: `--daily-workers 1` leaves the production runner
UNWRAPPED, so an eight-wide queue would pull one `WarmPrefixController`'s
single TraCI connection from eight threads; and `--daily-workers 1
--seed-workers 8` has a product of 8, so an eight-wide queue over it is 64
concurrent SUMO processes. `validate_queue_concurrency_budget()` now fails
closed before any unit exists, on four axes: the runner must be
process-isolated, it must start exactly one SUMO per unit, the width must not
exceed the declared `--daily-workers`, and it must not exceed the resource
benchmark's approval (`approved_seed_workers()`, currently 8). A test double
declares itself safe explicitly via `queue_sumo_profile()`; nothing is assumed
safe by default.

**3. An abandoned queue wedged the interpreter.** The pullers park on the
queue's own condition variable, which no shutdown path knows how to drain, and
as non-daemon threads they made exit unreachable - `threading._shutdown()`
joins them BEFORE any atexit handler runs, so neither `concurrent.futures`'
exit hook nor one of our own could wake them. Measured: an owner that skipped
`cleanup()` hung forever. The pullers are now daemon threads with a
`threading._register_atexit` hook that retires them and waits a bounded
`QUEUE_SHUTDOWN_GRACE_S` for the unit in flight, so the orderly path still
reaps its SUMO child and the disorderly path still exits.

**4. The benchmark orphaned what it killed, and would publish a speed number
for a failed run.** `run_real_arm()` created no process group and killed only
the parent on timeout - the exact behaviour the frozen report records leaving
isolated workers and SUMO children running. It now owns a session
(`start_new_session=True`), records that group id at spawn rather than deriving
it from a possibly reused pid at timeout, and escalates TERM then KILL across
the whole group with a bounded wait at each step. It also REAPS the leader
itself: an unwaited-for child stays in the process table as a zombie and still
reports its group, so a shutdown that only inspected the table could never
observe its own success. The census now separates live members from dead ones
(state `Z`), because escalating against a zombie signals nothing and counting
one as a survivor reports a leak that cannot execute another instruction; an
unreadable process table is UNKNOWN and never a success. It also refused too
little: a speed claim
needed only equal cache fingerprints, and two arms that both crashed early
agree byte for byte. `speed_claim_blockers` now additionally requires every arm
to exit 0, not time out, publish a non-empty and complete evidence population,
leave no partial files, and produce real ancestry-based concurrency samples.

### Honest limits

The 180-unit benchmark measures the SCHEDULER with a sleeping stand-in. The
per-unit COST is not measured there; it is taken from production's 94.396 s
(80 330.94 worker-seconds over 851 real units). Any full-month figure built
from those two numbers is a PROJECTION, and is labelled as one. Only a complete
cold campaign can settle it, and no campaign was restarted.

Those projections are also narrower in SCOPE than their name suggests: 6.58 h
cold and 2.93 h resume count unique units at the pilot's one repetition per
variant and omit the finalist stage entirely. Bounded above by the policy's own
ceilings that is ~7.19 h / ~3.54 h with the initial finalist round and ~8.81 h
/ ~5.15 h at the adaptive maximum - so the upper bound CROSSES the eight-hour
goal. See the corrected table in
`docs/plans/ROAD_CLOSURE_SIMULATION_SPEED_PLAN_2026-08-21.md`.
