# Final pre-warming engineering audit

**Date:** 2026-08-03  
**Boundary:** no annual warming unit was executed and no product activation or
cache publication was performed.

> **Superseded-plan note (2026-08-04).** The engineering conclusions remain
> applicable, but the cited `4f92de3d…318c09` root is retired after the
> every-edge demand/provenance change. The replacement plan is
> `0ce86bca…98d10`; it has the same 104,685-unit coverage, passed a fresh SUMO
> 1.27.1 preflight, and is initialized with 104,685 pending and zero attempts.
> No state from the old root is relabelled or reusable under the new identity.

## Outcome

The chained annual implementation is fail-closed, dependency ordered and bound
to final plan `4f92de3d…318c09`. This audit fixed four scale defects and four
recovery/integrity defects that were not visible in the earlier correctness
campaign.

## Official SUMO findings applied

- SUMO state files omit vehicles that have not departed, and flows require the
  original route input. The annual artifact therefore retains and binds the
  route, and every resumed command supplies that route again.
  <https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html>
- SUMO recommends loading with the same inputs and setting `--begin` to the
  saved time. Both are mandatory in the warm invocation.
  <https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html>
- RNG state is not saved by default and state precision defaults to two digits.
  Warm identity and command validation require RNG saving and precision 16.
  <https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html>
- Quick TraCI `simulation.loadState` can omit incrementally loaded vehicles and
  flows. Production uses a fresh process with `--load-state`, the original route
  file and exact begin time instead.
  <https://sumo.dlr.de/docs/TraCI/Change_Simulation_State.html>
- Unfinished vehicles only appear in tripinfo when
  `--tripinfo-output.write-unfinished` is enabled. That output transports the
  mesoscopic accumulator omitted by state serialization.
  <https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html>
- Official TraCI documentation defines `getTimeLoss` as accumulated time loss.
  The production decision not to use it is based on narrower local evidence:
  frozen SUMO 1.27 mesoscopic save/load diagnostics show it does not reproduce
  the private tripinfo accumulator exactly across this boundary.
  <https://sumo.dlr.de/docs/TraCI/Vehicle_Value_Retrieval.html>
- SUMO recommends libsumo when socket overhead dominates. It is not installed
  on this host, and switching boundary controllers would require a fresh exact
  equivalence campaign. The proven TraCI backend remains bound for this run.
  <https://sumo.dlr.de/docs/Libsumo.html>

## Corrections made

1. **Constant-time plan lookup.** A validated `AnnualWarmPlanContext` indexes
   requests, demand contracts and exact predecessors once per process. Ten
   unindexed lookups took 8.22 s; 1,000 indexed lookups take 0.014 s after the
   one-time context setup.
2. **Runner reuse.** Archived runner construction measured 0.54–0.77 s and was
   repeated per state. Each isolated worker now retains one runner for the
   current exact demand build, reducing construction from 104,685 times to at
   most three times per daily archive.
3. **Demand validation reuse.** Archive validation measured 0.13–0.15 s per
   call. The main process now passes its exact validation record to workers;
   artifact metadata and member hashes still cross-bind the bytes.
4. **Selective predecessor restore.** Chaining consumes only state and prefix
   evidence. A real pilot artifact restored in 0.0010 s versus 0.0261 s for all
   members (25.49x), avoiding repeated restoration of shared route/demand blobs.
5. **Exact predecessor provenance.** Metadata and restore validation require
   the immediate plan-derived predecessor, not merely a non-empty compatible
   unit ID.
6. **Dependency-safe retries.** Parallel scheduling admits only units whose
   predecessor artifact is already durable, including after partial failures.
7. **Semantic orphan recovery.** A crash-published artifact is restored and its
   prefix schema/warm point, demand contract, SUMO state mode/time/version and
   hashes are validated before it may become succeeded.
8. **Durability and cleanup.** Blob directory entries are fsynced; progress
   transitions are batched atomically; production monthly searches remove all
   retained provisional workspaces in a `finally` path.

## Validation

- Broad current-code suite: **523 passed**. Six additional held-out-gate tests
  fail closed because their frozen campaign fingerprints predate the source
  changes; they are historical certificate drift, not warming regressions.
- Fresh q10 24,300 → 25,200 real SUMO diagnostic: exact prefix evidence, exact
  active accumulator and exact downstream closure metrics; prefix population
  1.821 s cold versus 1.686 s chained (**1.080x**). Early cache-hit candidate
  execution remains slower than direct cold at this low reuse point, which is
  why no universal speed claim is made.
- Previously measured favourable late checkpoint: 16.726 s direct cold versus
  6.773 s cache-hit suffix (**2.47x**) with exact metrics.
- AST parsing and `git diff --check`: pass.
- Rebound plan/preflight: 104,685 pending, zero attempts; SUMO 1.27.1 and three
  workers approved with 182.96 GB free against the 171.80 GB minimum.

## Remaining boundary

Only the full 2027 population is the next expensive operation. Its resulting
artifacts still require completion auditing and separate product activation;
population itself grants neither release nor interactive-use authority.
