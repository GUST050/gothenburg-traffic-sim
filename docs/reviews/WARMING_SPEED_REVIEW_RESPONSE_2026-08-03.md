# Warming speed review — verified disposition

**Date:** 2026-08-03  
**Scope:** Current code after the Luna High review; no annual population run was
started.

## Outcome

The central finding was valid: cache misses replayed every candidate-free
prefix from zero. Both the monthly optimization sweep and the annual population
tool now form ordered `(demand, seed, variant)` chains. The first checkpoint is
bootstrapped; every later checkpoint advances the nearest preceding state and
reconciles its exact unfinished-tripinfo accumulator. An extension failure stops
annual population and makes a monthly observation fall back cold.

The whole-repository Git commit is no longer part of warm-state identity v3.
Exact demand, route, network, SUMO, Python, platform, serialization settings and
interpreting-source fingerprints remain bound.

The annual executor also schedules only dependency-ready units. A partial
failure cannot cause a resumed checkpoint to race its unfinished predecessor.

## Findings

1. **Frozen 5.6% case:** the v9 miss measurement in the review is historical.
   The adopted v16 cache did populate three exactly equivalent states, and a
   recorded cache hit later ran 19.1% faster than cold. A new real 86%-reuse
   q10 measurement is exact and runs the cache-hit closure in 6.77 s versus
   16.73 s cold: **2.47x**.
2. **No prefix sharing:** fixed in monthly search and annual population. The
   900-second adjacent-checkpoint case is exact and populated in 1.66 s versus
   1.84 s from zero. A 24,300 -> 110,700 chain reproduces complete cold prefix
   evidence and an identical real closure for q10, q50 and q90.
3. **Cache cannot populate:** stale for v16; it passed and published three
   entries. Identity v3 deliberately misses v2 entries after the safety/key
   change, so old artifacts are retained as evidence rather than reinterpreted.
4. **Commit invalidation:** fixed. Documentation-only commits do not alter the
   v3 content key.
5. **Interactive close road:** an all-run closure begins at simulation time
   zero and therefore has no candidate-free prefix to warm safely. Time-window
   closures can use annual states only after the bank is populated and product
   activation supplies exact prefix flow evidence. The interactive default
   seed/variant mapping is now aligned with monthly/annual canonical identity;
   q50 remains the trajectory representative.
6. **Overheads:** cold and warm already use the same 3,600-second drain margin;
   a cache hit starts one SUMO process; production does not perform per-vehicle
   TraCI time-loss calls. Precision 16 remains necessary transport evidence.
7. **TraCI semantics/comment drift:** official TraCI documentation defines
   `getTimeLoss` as accumulated time loss. The issue is narrower: frozen SUMO
   1.27 mesoscopic save/load diagnostics show it does not exactly transport the
   private tripinfo accumulator across this boundary. Production therefore uses
   unfinished tripinfo; the old ledger is legacy diagnostic compatibility.

## Bound diagnostic evidence

- `validation/warm_prefix_chaining_diagnostic_v1.json` — q10, 110,700 s.
- `validation/warm_prefix_chaining_q50_diagnostic_v1.json` — q50, 110,700 s.
- `validation/warm_prefix_chaining_q90_diagnostic_v1.json` — q90, 110,700 s.
- `validation/warm_prefix_chaining_900s_diagnostic_v1.json` — adjacent 900 s.
- `validation/warm_prefix_favourable_diagnostic_v1.json` — q10, 371,700 s and
  direct cold candidate benchmark.

Raw state XML bytes are not an equality gate because a save/load cycle retains
additional serialization history. The required gates are stronger and
user-visible: identical complete prefix evidence, identical active population
and accumulator, identical closure suffix, and identical reconstructed cold
decision metrics.
