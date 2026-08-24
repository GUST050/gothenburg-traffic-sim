# Large-simulation function structure — 2026-08-23

## Decision

Keep this as one Python application and one repository. Scale it by making the
work units and resource ownership explicit, not by introducing microservices,
Kubernetes, a generic persistent worker pool, or nested pools.

The program should have four operational boundaries:

1. **Demand build** produces an immutable, validated demand release.
2. **Scenario execution** turns one immutable scenario request into per-seed
   evidence and one canonical result.
3. **Scheduling** gives independent work units a bounded number of CPU and
   memory slots, with interactive work ahead of batch work.
4. **Artifact storage** publishes content-addressed evidence atomically and
   provides exact-key reuse and single-flight coordination.

Fifty sensors are a product input target, not a worker count. Sensor rows make
calibration larger and can justify a larger vehicle population; the latter is
what can make the simulator nonlinear or overloaded.

## Implementation outcome — 2026-08-23

The low-complexity production slice is implemented:

- edgeData now writes only `entered timeLoss` by default; isolated
  `--full-edgedata` is the tested rollback;
- phase-profile schema v2 separates `disruption_analysis`,
  `payload_construction` and `artifact_publication`;
- `traffic_sim/simulation/disruption.py` groups common-origin queries and uses
  SciPy's sparse Dijkstra for larger batches, with a grouped Python fallback
  and the former per-OD implementation retained as the exact test oracle;
- seed subprocess inputs cross a frozen `SeedRunPlan`; final scenario/index
  publication is a separate function with no SUMO authority;
- matched monthly baselines use a per-content-key cross-process `flock`, so
  one producer runs while waiters re-read and verify the completed artifact.

On the active 21,408-vehicle closure case, the process-free disruption result
was byte/field equal to the oracle and fell from 4.1364 s to 1.051 s. One
isolated production-shaped three-seed closure completed in 10.690 s; the
full-edgeData rollback took 11.549 s. Both produced scenario digest
`7fcb7774...194a`, trajectory digest `a85eef00...aed4`, inserted every vehicle,
had no health flags and were `verified_clean`. This is a diagnostic adoption
check, not a p95 claim; the <=10 s p95 gate remains open.

The 975-line demand-build orchestration was deliberately not mechanically
rewritten in this slice. It is offline, its numerical kernels are already
separate, and a broad move would add scientific risk without improving closure
latency. Future extraction should occur only alongside a demand-build change
that needs one of the six stage seams below.

## Repository evidence

The computational kernels are already more modular than the command paths, so
this should be an extraction rather than a scientific rewrite.

Current orchestration sizes from the Python AST are:

| Function | Approximate size | Main concern |
| --- | ---: | --- |
| `build_sumo_demand.main` | 975 lines | resolves inputs, builds candidates, calibrates, validates and publishes |
| `run_scenario.main` | 505 lines | validates, prepares, launches seeds, aggregates, analyses and publishes |
| `monthly_search.run_monthly_search` | 371 lines | scheduling, progress, evidence and result lifecycle |
| `pfe.quarter_publish_counts` | 418 lines | dense calibration/publication policy |
| `pfe.write_calibration_report` | 406 lines | report construction and output |

The monthly-search domain is already the strongest model: it uses immutable
dataclasses and a `CandidateRunner` protocol. `run_scenario` and
`build_sumo_demand` still pass many open-ended dictionaries and keep I/O,
process control and domain decisions in the same functions.

### Newly isolated closure hotspot

The phase named `scenario_publication` is not just JSON writing. It calls
`closure_disruption_across_variants` before publishing. On the active
21,408-vehicle q50 release and the paired benchmark edge
`26842525_26355153_0`, a process-free local trace measured:

| Work | Measurement |
| --- | ---: |
| network graph plus free-flow costs | 0.2274 s |
| closure disruption, trial 1 | 4.1757 s |
| closure disruption, trial 2 | 4.0654 s |
| closure disruption, trial 3 | 4.1568 s |
| affected vehicles | 3,773 |
| unique affected origin/destination pairs | 340 |
| unique origins / destinations | 200 / 180 |
| current shortest-path calls | 1,360 |

The function scans the route XML and performs four Python Dijkstra searches
for every unique affected origin/destination pair: time and distance, each
with and without the closure. This explains almost all of the measured
approximately 4.7-second `scenario_publication` phase. It will grow with route
volume, affected OD diversity and the number of demand variants even when JSON
size does not.

This phase must be renamed/split before using its timing to choose another
optimization:

```text
disruption_analysis -> payload_construction -> artifact_publication
```

## Scale model

Track the axes independently:

| Axis | Capacity measure | Failure signal |
| --- | --- | --- |
| sensors | calibration rows, sparse nonzeros, rank/support | unsupported or unobservable station |
| vehicles | loaded, inserted, max active, UPS | loaded != inserted or waiting at drain |
| network | edges/connections and routing work | routing time/queries dominate |
| horizon | simulated seconds and intervals | state/output growth |
| stochastic evidence | seeds and variants | incomplete ensemble |
| scenarios | unique daily units and cache misses | queue growth and poor verified units/hour |
| outputs | bytes and parse/publish time | I/O dominates wall time |
| resources | peak RSS and active CPU slots | swapping, contention, orphaned children |

The existing evidence demonstrates the nonlinearity. A one-seed diagnostic
completed 21,408 vehicles in 1.882 seconds and 42,816 in 2.958 seconds, but an
85,632 arm took 66.118 seconds while leaving 28,977 vehicles waiting. A
50-row sensor-output audit itself measured only 3.325 ms p95. The capacity
boundary is therefore the calibrated population and congestion/insertion
state, not the audit loop.

SUMO exposes UPS, active vehicles and buffered/waiting vehicles for this exact
reason. Any supported load tier must finish with `loaded == inserted`, zero
waiting and running vehicles at the drain boundary, and the existing teleport,
collision and closure-integrity gates intact.

## Target module and function boundaries

The names are a target shape, not a request to move every file at once.

```text
traffic_sim/
  demand/
    planning.py       request -> build plan; no writes or children
    candidates.py     candidate cache/build and route support
    calibration.py    sparse matrices and independent interval solves
    validation.py     observability, fit and provenance gates
    publication.py    validated staged release -> atomic immutable release
  simulation/
    planning.py       ScenarioRequest -> ScenarioPlan + SeedRunPlan[]
    preparation.py    closure additions and private route variants
    executor.py       execute_seed(plan) -> SeedEvidence
    aggregation.py    SeedEvidence[] -> ScenarioEvidence
    disruption.py     indexed/batched deterministic disruption calculation
    publication.py    validated ScenarioEvidence -> scenario/trajectory/index
  orchestration/
    budget.py         one authoritative CPU/RSS/slot policy
    scheduler.py      bounded priority queue and cancellation
    singleflight.py   one producer per content key, many verifiers
  storage/
    identity.py       canonical keys and source fingerprints
    artifacts.py      atomic writes, digests and strict readers
```

`run_scenario.py`, `build_sumo_demand.py` and the HTTP handlers then become
thin adapters: parse a request, call the domain pipeline, translate progress or
errors, and exit. They should not contain scientific calculations.

### Typed boundary objects

Introduce these at process and publication boundaries first; do not convert
every internal dictionary in one change.

- `DemandBuildRequest`: sensor/source/network identity, horizon and policy.
- `DemandBuildPlan`: immutable inputs, candidate key and independent solve
  units.
- `DemandRelease`: content key, paths, population, variants and gate summary.
- `ScenarioRequest`: the existing `ScenarioSpec` plus output/evidence policy.
- `SeedRunPlan`: only picklable scalar values and paths needed by one SUMO
  child.
- `SeedEvidence`: paths/digests, health, flow summary and timings; no parsed
  full XML tree.
- `ScenarioEvidence`: ordered seed evidence plus validated aggregates.
- `ResourceRequest`: CPU slots, estimated RSS and priority.

Use frozen dataclasses for these contracts. Validate once when crossing a
boundary. This makes invalid states fail early and avoids repeatedly copying
large mutable dictionaries into workers.

### Function rules

1. Planning functions are pure: request in, immutable plan out.
2. An executor may start a child process but may not publish shared final
   artifacts.
3. A publisher may write shared artifacts but only from already validated
   evidence; it never launches SUMO.
4. Aggregators accept canonical seed order and are deterministic.
5. Worker results contain bounded summaries and artifact paths, not full XML
   trees or citywide objects.
6. Every reusable result is keyed by all semantic inputs and relevant source,
   runtime and output-policy fingerprints.
7. Large orchestration functions should normally fit in roughly 100–150 lines.
   This is a review signal, not an arbitrary correctness gate.

## Execution topology

Use one bounded scheduler for all SUMO work:

```text
interactive request
  -> exact cache/single-flight check
  -> acquire resource tokens
  -> prepare once
  -> 3 isolated seed processes in parallel
  -> ordered aggregate + validate
  -> disruption analysis
  -> atomic publish

monthly search
  -> canonical independent daily units
  -> bounded priority queue
  -> N one-shot daily workers
  -> each worker receives an explicit inner-seed budget
  -> ledgered result + resumable progress
```

Never independently configure daily workers, seed workers, SUMO simulation
threads and routing threads. Their product is the true concurrency. A single
`ResourceBudget` should reject or queue a request whose total CPU/RSS claim
does not fit. Reserve capacity for interactive work and make batch work yield
at unit boundaries.

The next resource benchmark remains the bounded matrix already identified by
the project: `8 daily x 1 seed`, `4 x 2`, `2 x 3`, and a separately identified
routing-thread arm. Compare verified units/hour, p95 unit latency, aggregate
RSS, cancellation and exact evidence. Do not activate nested daily/seed
parallelism until the matched-baseline cache has cross-process per-key
single-flight.

For multiple machines later, keep the exact same independent daily-unit
contract. A queue consumer or Slurm job array can execute a unit and publish a
content-addressed result. Multi-host execution should not require changes to
the calibration or ranking algorithms.

## Disruption-analysis redesign

This is the clearest remaining interactive-latency opportunity and should be
handled in conservative stages.

### Stage D0 — correct observability

- Add `disruption_analysis`, `payload_construction` and
  `artifact_publication` timers.
- Record route records scanned, affected vehicles, unique OD pairs, routing
  queries and routing wall time.
- Preserve current bytes and semantic digest.

### Stage D1 — grouped Python routing

- Group affected OD pairs by origin (or reverse by destination, whichever has
  fewer groups).
- Run one multi-target Dijkstra per group and metric, stopping after all needed
  targets settle.
- Precompute the no-closure time/distance values once per demand/network
  release. Only the closure-specific detour values remain on the click path.
- Keep the old per-OD implementation as the oracle in paired tests.

For the measured case, the current 1,360 searches could become at most 720
grouped searches by using 180 reverse-destination groups for both metrics and
both closure states; precomputing the baseline half would leave at most 360
closure-time grouped searches. Actual speed-up must be measured because each
grouped search explores more of the graph.

### Stage D2 — immutable route-impact index

Only if D1 misses the p95 target, build a demand-release sidecar that maps each
edge to compact vehicle/OD/arrival records. A closure then unions the records
for its edge set and filters by closure windows without parsing every route.
The sidecar key must include route bytes, network identity, cost policy and
schema version. Publication of this sidecar belongs in the offline demand
release, never in the closure request.

### Stage D3 — SUMO router backend benchmark

Only if Python routing remains dominant, compare the exact D1 oracle with a
batched SUMO/duarouter implementation. Official duarouter provides
`--bulk-routing` for common origins, routing threads, and Dijkstra, A*, CH and
CHWrapper. This is a benchmark candidate, not an automatic adoption: edge-cost
conventions, permissions, turnaround penalties and disconnected cases must
match the current published metric exactly.

## Demand-build structure for 50 sensors

The closure path must consume a finished release and never recalibrate on a
click. Split `build_sumo_demand.main` into the following resumable stages:

1. `resolve_build_request`
2. `build_or_restore_candidate_pool`
3. `build_sparse_calibration_problem`
4. `solve_independent_intervals`
5. `validate_staged_release`
6. `publish_demand_release`

Keep the sensor/route incidence matrices sparse CSC/CSR. Record rows, columns,
nonzeros, candidate-generation time, solver time, integer-repair time and peak
RSS separately. HiGHS documents limited gains from internal parallelism and
recommends assigning independent instances to independent workers when those
instances exist. That matches the quarter/day decomposition already present;
it does not support an unbounded solver-thread pool.

Candidate route generation is already about 77 seconds for the current release
and should remain content-addressed by sensor, network, source and policy
identity. New sensors should invalidate only the layers whose meaning changes,
not every downstream artifact indiscriminately.

## Output and parsing structure

- Adopt the already qualified `entered timeLoss` edgeData field set in its own
  reviewed production change; the paired test reduced closure mean wall time
  by 7.1% with equal semantic digests but still missed the 10-second target.
- Continue streaming parsers. `sumolib.xml.parse_fast` is available for simple
  line-oriented elements, but its ordering/format restrictions mean it must be
  benchmarked against the current `iterparse` readers before adoption.
- Do not enable compression, CSV or Parquet by assumption. SUMO supports them,
  but CPU, bytes and parse time must be measured together.
- Keep trajectory sampling and evidence outputs semantically separate. The
  browser product may be bounded; integrity and health evidence may not be
  sampled away.

## Capacity and regression tests

### Function-level

- request-to-plan tests with no filesystem or subprocess mocking;
- old versus grouped disruption equality for normal, timed, multi-edge,
  denied-departure and severed-destination closures;
- cache-key invalidation tests for route/network/source/policy changes;
- publisher tests proving no artifact appears before all validation passes;
- corrupt/truncated artifact readers fail closed.

### Process-level

- one seed child writes only to its private directory;
- ordered aggregation is invariant to child completion order;
- cancellation kills the complete process group and leaves no published
  partial result;
- one producer/many verifier single-flight across processes;
- resource budget prevents `daily x seed x routing-thread` oversubscription.

### Scale ladder

- sensor fixtures: 6, 25 and 50 physical stations;
- calibrated vehicle tiers near 21k, 32k, 43k, 50k and 60k;
- normal and high-congestion closures;
- 100, 500 and 2,224 independent daily units;
- report p50/p95 wall, phase times, UPS, loaded/inserted/waiting, output bytes,
  peak RSS, cache behavior and semantic digests.

Stop at the first unsupported vehicle tier. Never make a tier pass by dropping
vehicles, shortening recovery, reducing seeds, allowing teleports or weakening
fit/integrity gates.

## Implementation order

1. **Done:** adopt the qualified minimal edgeData output with rollback tests.
2. **Done:** split and measure disruption analysis separately from publication.
3. **Done:** implement grouped/sparse disruption routing with an exact retained
   oracle; the measured process-free phase improvement is about 74.6%.
4. **Done:** add cross-process baseline single-flight.
5. **Partly done:** extract typed seed execution, disruption and publication
   boundaries from `run_scenario.main`; continue with plan, prepare and aggregate
   and publish while preserving outputs at each step.
6. Extract the six demand-build stages only when an in-scope demand change
   needs them; do not perform a risky mechanical rewrite for its own sake.
7. Benchmark the bounded outer/inner worker matrix.
8. Build the 6/25/50-sensor calibrated fixtures and vehicle-capacity ladder.
9. Consider a duarouter backend, immutable route-impact index, or multi-host
   daily workers only if the preceding measurements still miss their targets.

## Approaches deliberately not recommended now

- a generic persistent Python pool: the repository benchmark measured 0.998x;
- nested process pools: difficult cancellation and multiplied resource use;
- libsumo rewrite: much larger correctness/lifecycle surface, and parallel
  libsumo instances still require multiprocessing;
- saved simulation state for exact published evidence: future vehicles and
  parts of the simulation/RNG state have important documented limitations;
- dynamic user assignment on the interactive path: iterative routing and SUMO
  work belongs offline;
- microservices or distributed infrastructure before the single-host work-unit
  boundary and single-flight contract are sound.

## Primary sources

- [SUMO performance and parallel-run FAQ](https://sumo.dlr.de/docs/FAQ.html#how-can-i-make-the-simulation-run-faster)
- [SUMO output and timing metrics](https://sumo.dlr.de/docs/Simulation/Output/index.html)
- [SUMO simulation summary fields](https://sumo.dlr.de/docs/Simulation/Output/Summary.html)
- [SUMO mesoscopic model](https://sumo.dlr.de/docs/Simulation/Meso.html)
- [SUMO edge/lane traffic measures](https://sumo.dlr.de/docs/Simulation/Output/Lane-_or_Edge-based_Traffic_Measures.html)
- [SUMO duarouter options](https://sumo.dlr.de/docs/duarouter.html)
- [SUMO shortest/optimal routing](https://sumo.dlr.de/docs/Demand/Shortest_or_Optimal_Path_Routing.html)
- [SUMO automatic rerouting](https://sumo.dlr.de/docs/Demand/Automatic_Routing.html)
- [SUMO vehicle insertion](https://sumo.dlr.de/docs/Simulation/VehicleInsertion.html)
- [SUMO routes from observation points](https://sumo.dlr.de/docs/Demand/Routes_from_Observation_Points.html)
- [SUMO save/load limitations](https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html)
- [SUMO sumolib parsing](https://sumo.dlr.de/docs/Tools/Sumolib.html)
- [SciPy `milp` sparse solver interface](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html)
- [HiGHS parallel guidance](https://ergo-code.github.io/HiGHS/stable/parallel/)
- [Python `concurrent.futures`](https://docs.python.org/3/library/concurrent.futures.html)
- [Azure queue-based load leveling](https://learn.microsoft.com/en-us/azure/architecture/patterns/queue-based-load-leveling)
- [Slurm job arrays](https://slurm.schedmd.com/job_array.html)
