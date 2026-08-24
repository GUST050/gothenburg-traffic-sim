# Fifty-sensor performance contract — 2026-08-22

## Decision

The supported design target is **50 physical sensor stations**, including the
larger vehicle population that those measurements may justify, without
relaxing the existing closure evidence contract. Sensor count and vehicle load
are measured as separate axes so that a cheap audit loop cannot hide a slow or
overloaded traffic simulation.

Sensor onboarding, demand calibration, interactive closure simulation and
exhaustive search are separate workloads. A closure click must never rebuild
the demand model. It consumes a prebuilt, validated, immutable demand release.

## What actually scales

For this program, approximate wall time has four different drivers:

```text
demand build       = candidate generation + calibration(sensor constraints)
interactive close  = preparation + max(parallel seed SUMO runs) + validation
monthly search     = unique daily units * unit cost / safe concurrent units
sensor audit       = directed sensor edges * intervals * seeds
```

Adding sensors directly expands calibration constraints and the small final
sensor-audit loop. It does not add SUMO seeds or extend the simulated day.
Indirectly, however, new observations can require more calibrated vehicles.
That indirect growth is part of the 50-sensor product and must be capacity
tested.

Fifty sensors do **not** imply fifty independent traffic totals. A route may
cross several counting locations and satisfy several margins with one vehicle.
The calibration must solve the joint route-incidence problem, retain the
citywide population/OD prior, and add vehicles only when the combined evidence
requires them. SUMO's own routeSampler uses the same idea and can explicitly
favour fewer vehicles that pass multiple counting locations. See
[routes from observation points](https://sumo.dlr.de/docs/Demand/Routes_from_Observation_Points.html)
and [routeSampler optimization](https://sumo.dlr.de/docs/Tools/Turns.html#optimization).

SUMO documents that more vehicles, unwanted jams and a backlog of vehicles
waiting to insert can make a simulation much slower. This is nonlinear: after
network capacity is reached, more demand creates repeated insertion attempts
rather than proportional useful work. See the
[SUMO performance FAQ](https://sumo.dlr.de/docs/FAQ.html#how-can-i-make-the-simulation-run-faster)
and [SUMO timing output](https://sumo.dlr.de/docs/Simulation/Output/index.html#commandline-output-verbose).

## Current reference and measured sensor-only cost

The current one-day build has:

- 6 physical stations / 7 directed sensor edges;
- 7,147 map edges;
- 21,408 calibrated vehicles;
- 96 intervals and 3 parallel seeds;
- candidate generation 77.402 s and PFE/rounding 87.792 s; and
- measured interactive closure about 13.9 s, with an older frozen p95 of
  10.4234 s.

A process-free 2026-08-22 diagnostic ran 300 complete output-fit validations at
each size on this host. It used 96 quarters, hourly GEH aggregation and exact
matching synthetic target/raw series:

| Physical/directed rows | p50 | p95 | Maximum |
| ---: | ---: | ---: | ---: |
| 6 | 0.399 ms | 0.423 ms | 0.444 ms |
| 50 | 3.273 ms | 3.325 ms | 3.826 ms |
| 100 | 6.434 ms | 7.105 ms | 16.401 ms |

This measures the final validation path, not PFE calibration or SUMO. It shows
that 50-row validation adds about 2.9 ms p95—not seconds. Correctness at 50
stations and detection of one bad station among 50 are pinned in
`tests/test_sensor_scale_contract.py`; the source-bound timing record is
`validation/sensor_validation_scale_50_2026-08-22.json`.

## Measured vehicle-load and output diagnostic

An isolated 2026-08-22 diagnostic used the active 21,408-vehicle route file,
one SUMO seed, mesoscopic mode, no closure and no trajectory output. It is a
runtime stress test, not a calibrated 50-sensor release.

| Demand scale | Loaded | Inserted | Waiting at end | Mean SUMO wall | Reading |
| ---: | ---: | ---: | ---: | ---: | --- |
| 1x | 21,408 | 21,408 | 0 | 1.882 s | valid complete load |
| 2x | 42,816 | 42,816 | 0 | 2.958 s | 1.57x wall for 2x vehicles |
| 4x | 85,632 | 56,655 | 28,977 | 66.118 s | overload; invalid complete-population evidence |

The 4x arm was 35.1 times slower than 1x and did not insert 33.8% of its
vehicles. It must not be used to claim support for 85,632 vehicles. The next
capacity ladder is 21,408, 32,000, 42,816, 50,000 and 60,000 vehicles, stopping
at the first tier that cannot finish with loaded equal to inserted and zero
waiting. Those points must eventually come from calibrated demand releases;
SUMO `--scale` is diagnostic only.

The same isolated run tested a simple output reduction. The default edgeData
file was 49.86 MB and took 2.283 s on average. Writing only the two attributes
used by production—`entered` for flow and `timeLoss` for recovery—was 10.16 MB
and took 1.897 s: 79.6% fewer bytes and 16.9% less wall time. All 3,242 flow
series and all 96 recovery buckets were equal. SUMO officially supports
selecting fields with `writeAttributes`; see
[edge-based traffic measures](https://sumo.dlr.de/docs/Simulation/Output/Lane-_or_Edge-based_Traffic_Measures.html).
This is the best small, low-complexity optimization found so far. A subsequent
paired test ran ten baseline and ten closure trials per arm, all with three
production seeds and trajectory output: 40 scenario runs / 120 seed executions.
Scenario and trajectory semantic digests were identical, all closures remained
`verified_clean`, and every seed inserted 21,408/21,408 vehicles with zero
waiting, running, teleports or collisions. Mean baseline wall time fell
6.561 -> 5.484 s (16.4%); mean closure wall fell 15.198 -> 14.120 s (7.1%).
The candidate therefore passes its paired evidence gate and is eligible for a
separate production-default change, but is not silently adopted by this test.
Closure p95 fell 15.403 -> 14.142 s and still misses the 10-second target. Bound records are
`validation/vehicle_load_and_edgedata_diagnostic_2026-08-22.json` and
`validation/edgedata_attributes_paired_adoption_2026-08-22.json`.

## User-facing time budget

There is no SUMO or transport-industry standard that prescribes one universal
road-closure response time. The service-level objectives below are product
requirements derived from the program's measured costs and established web/API
responsiveness patterns:

| Product event | Target | Failure/action threshold |
| --- | ---: | --- |
| Visual response to click | p75 <= 200 ms | Above 500 ms is poor interaction responsiveness |
| Valid request accepted and status URL available | p95 <= 1 s | Never hold the HTTP request open for SUMO |
| Exact cached closure displayed | p95 <= 2 s | Cache miss follows the asynchronous path |
| New exact one-day closure, up to 25k complete load | p50 <= 8 s, p95 <= 10 s | Current 21k closure is about 13.9 s and does not yet pass |
| New exact closure, 25k–43k complete load | p95 <= 20 s | Keep progress/cancel live; investigate above 20 s |
| New exact closure, 43k–60k complete load | p95 <= 30 s | Capacity tier, not yet a supported claim |
| Sensor-only aggregation/validation at 50 stations | p95 <= 50 ms | More than 5% of total closure wall time reopens profiling |

Google's Core Web Vital guidance treats <=200 ms as good interaction response;
that concerns the next visual feedback, not completion of a simulation. See
[Interaction to Next Paint](https://web.dev/articles/inp). Long-running work
should be acknowledged quickly and observed through a status resource. HTTP
202 explicitly represents accepted but incomplete processing, and Microsoft's
asynchronous request-reply pattern recommends a 202 plus a pollable status
endpoint. See [RFC 9110 section 15.3.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.3.3)
and [Microsoft's asynchronous request-reply pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/asynchronous-request-reply).

There is no research-backed universal number of seconds that every road-closure
model must finish within. The 10/20/30-second tiers are explicit product
budgets derived from this host's measured 21k and 43k costs, not an industry
standard. The 30-second value is an investigation/UX threshold, not permission
to drop vehicles, shorten recovery, skip seeds or kill an otherwise healthy
exact run. The existing bounded SUMO failure timeout remains separate.

## Batch time budget

Using the measured production-shaped median of about 29 s per cold daily unit,
a simple capacity estimate is:

```text
estimated batch wall = unique units * 29 s / verified safe workers + overhead
```

At six genuinely safe concurrent workers, the stopped fixture's 2,224 units are
about 3.0 compute-hours before overhead; 6,500 units are about 8.7 hours. The
50-sensor capacity objectives are therefore:

| Batch product | Target | Ceiling |
| --- | ---: | ---: |
| 2,224-unit named fixture | <= 4 h | 8 h |
| 6,500-unit full-year weekday example | <= 12 h | 24 h |
| First resumable progress/result summary | <= 15 min | 30 min |

These are capacity objectives, not current claims. They require the baseline
single-flight repair and the bounded outer/inner worker matrix. The stopped
live search took far longer and is not acceptable as the target architecture.

SUMO's documented scaling model supports independent simulations in parallel;
the simulation core itself is not the general multi-core lever. See the
[SUMO parallel-run FAQ](https://sumo.dlr.de/docs/FAQ.html#can-sumo-be-run-in-parallel-on-multiple-cores-or-computers).

## Offline demand-build budget

Fifty sensors will affect PFE and onboarding work, so it needs its own benchmark
rather than a linear extrapolation from six stations. This work is outside the
closure request path.

- One-day 50-station build: p95 <= 15 minutes on the reference host.
- Seven-day release build: p95 <= 60 minutes, resumable and isolated.
- A completed release is cached by full sensor/network/source identity.
- Closure simulation may use only a validated completed release; it never waits
  for opportunistic recalibration.

These budgets must be revised from measured 50-station builds, not relaxed to
make a run pass.

## Required 50-sensor evidence matrix

1. **Sensor-only:** 6/7, 50/50 and 50/100 station/directed-edge fixtures with
   96 intervals; exact audit output and one-bad-station failure recall.
2. **Demand build:** 6, 25 and 50 stations using the same network, priors and
   route-pool construction policy. Report the resulting vehicle population,
   candidate generation, PFE, integer repair, publication, peak RSS,
   variables, constraints and sparse nonzeros separately. Do not force equal
   vehicle totals between sensor-count arms.
3. **Interactive closure:** same 21,408 vehicles/network/closure/seeds at 6 and
   50 stations; p95 latency may regress at most 5% and must remain <=10 s.
4. **Calibrated load matrix:** approximately 21k, 32k, 43k, 50k and 60k
   vehicles, plus normal and high-congestion closures. Record loaded, inserted,
   waiting, running at drain end, teleports, SUMO real-time factor, updates per
   second, routing calls/time, output bytes, health and exact evidence. A tier
   fails closed if loaded differs from inserted or waiting is nonzero.
5. **Batch:** frozen 100-, 500- and 2,224-unit sets under the safe worker budget;
   verify baseline single-flight, cancellation, restart, peak aggregate RSS and
   no surviving SUMO children.
6. **Onboarding:** every one of 50 stations must pass registry/network mapping,
   route-pool support, observability and final raw-SUMO audit gates. Scale is
   not authorization to hide an unsupported station.

## Consequence for implementation order

1. Make the now-qualified `writeAttributes="entered timeLoss"` candidate the
   production default in a separate reviewable change, retaining a tested
   rollback; the paired exact-output gate has passed.
2. Fix content-keyed baseline single-flight before raising outer concurrency.
3. Keep 50-sensor output validation tests in the ordinary regression suite.
4. Build 6/25/50-station demand fixtures and report the *resulting* vehicle
   totals plus sparse PFE phase timings.
5. Build the calibrated 21k/32k/43k/50k/60k load ladder and stop at insertion
   failure. Never use `max-depart-delay` or dropped vehicles to make it pass.
6. Measure SUMO routing share. Only then benchmark the already isolated A* and
   rerouting-thread controls. ALT/CH, libsumo and saved-state work remain
   profile-gated rather than default complexity.
7. Benchmark the outer-unit/inner-seed worker matrix. Keep independent HiGHS
   jobs at one solver thread each and leave the rejected generic Python pool
   closed.

## Simple structure that should scale

```text
sensor registry (50)
    -> immutable candidate-route support, built once per network/registry
    -> sparse joint calibration by day/quarter
       (shared routes may satisfy multiple sensors)
    -> validated immutable demand release, including its actual vehicle count
    -> interactive closure: 3 seed runs in parallel
       -> minimal entered/timeLoss edgeData
       -> exact health + insertion + sensor audit
    -> content-keyed result cache
```

Keep calibration off the click path. Reuse candidate support and completed
demand releases by full content identity. Keep the current sparse matrices and
independent quarter jobs; do not add nested solver threads. HiGHS notes that
its parallel opportunities are limited and recommends one solver instance per
worker for independent problems; see
[HiGHS parallelism](https://ergo-code.github.io/HiGHS/stable/parallel/).

For SUMO, retain mesoscopic mode, one-second steps, disabled step logging,
local 400 m rerouters, streaming XML parsing and parallel independent seeds.
Mesoscopic simulation can be far faster than microscopic simulation, while
rerouting threads and A* should be enabled only after timing proves routing is
material. See [mesoscopic simulation](https://sumo.dlr.de/docs/Simulation/Meso.html),
[routing algorithms](https://sumo.dlr.de/docs/Simulation/Routing.html) and
[automatic routing](https://sumo.dlr.de/docs/Demand/Automatic_Routing.html).

Do not switch to TraCI/libsumo merely to chase speed: the current interactive
path invokes SUMO directly and therefore does not pay TraCI socket overhead.
Do not use saved state as exact evidence until its vehicle/timeLoss semantics
pass the existing equivalence gates. Do not reduce seeds, vehicles, recovery or
required outputs.
