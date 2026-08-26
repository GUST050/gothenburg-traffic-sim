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
