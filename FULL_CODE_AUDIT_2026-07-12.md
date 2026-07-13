# Full Code Audit - 2026-07-12

## Scope

Read-only audit of the pipeline, PFE engine, SUMO scenarios, signal tools,
server, browser, direction-split package, tests and artifact contracts.
Findings are ordered by severity.

## P0 - Must Fix Before Decision Support or Shared Deployment

### P0-1. Mutable shared output directories corrupt provenance
**Where:** `build_sumo_demand.py:924-1307`, `run_scenario.py:764-949`,
`serve.py:410-567`, signal scripts.

All workflows reuse paths in `sumo/` and `web/data/scenarios/`. The server
lock protects only one server process; CLI runs, server restarts and signal
tools can overwrite active inputs/outputs. A result cannot prove the code,
network, demand, candidate pool or SUMO version that produced it.

**Fix:** write immutable `runs/<run_id>/` inputs, outputs, logs, versions and
hashes; atomically switch an active-run manifest after validation.

### P0-2. Recalibration deletes all scenarios before baseline replacement
**Where:** `serve.py:548-557`.

It deletes every scenario JSON before the new baseline exists. A baseline
failure therefore leaves no prior valid baseline or closure results.

**Fix:** build/validate a new run first, then publish it and retain older runs.

### P0-3. State-changing endpoints use unauthenticated GET
**Where:** `serve.py:340-359`, `web/index.html` API calls.

Closures, recalibration, suggestions and signal optimisation use GET query
parameters. GET can be replayed by caches, history, proxies and prefetchers.
The server has no auth, CSRF protection, rate limit or operator audit trail.

**Fix:** authenticated POST JSON requests that return a job ID; GET only for
status/artifacts. Treat the current server as trusted-local development only.

### P0-4. Job state is volatile and not addressable
**Where:** `serve.py:136-145`, endpoint-specific state dictionaries.

One global dictionary per job type is lost on restart and overwritten by the
next job. Tabs cannot retrieve a specific job and children can outlive server.

**Fix:** persist job records and expose `/api/jobs/<job_id>` with reconciliation,
cancellation and durable logs.

### P0-5. Signal scores are not a valid time-of-day network metric
**Where:** `run_scenario.py`, `signal_lab.py`, `signal_optimize.py`,
`signal_closure_combine.py`.

Bounded simulations omit pre-window queues and can stop in-window vehicles
before completion. `timeLoss` is neither a warmed-up network metric nor a
complete departure-cohort metric.

**Fix:** define `ExperimentProtocol`: warm-up, measurement start/end, admission
end, completion end, cohort rule and unfinished count. Use interval outputs for
network performance or filtered routes run to completion for cohort performance.

### P0-6. Synthetic signal plans cannot prove physical or legal safety
**Where:** `signal_regulation.py`, `signal_optimize.py`, `signal_lab.py`.

OSM/SUMO lacks verified signal heads, real phase diagrams, pedestrian/cycle
movements, detectors, priority rules and a physical conflict matrix. Timing
calculations are synthetic estimates, not legal compliance evidence.

**Fix:** retain `synthetic_tsfs_informed`; require a verified movement-conflict
matrix/manual overrides, per-transition clearance audit, and signal-engineer
review before any compliance claim.

## P1 - High Risk to Correctness, Performance, or Reliability

### P1-1. PFE relaxation conflicts with publication semantics
**Where:** `pfe.py:665-697`, `build_sumo_demand.py:1130-1190`.

The solver can widen tolerance and drop bounds, while final publication checks
original structural bounds. A long run can solve a relaxed problem then fail
late without a structured explanation of quarter, rung or residual.

**Fix:** classify non-relaxable versus soft constraints, record per-quarter
rung/residual/integer repair, and emit validity grades or infeasibility reports.

### P1-2. Integer route repair can over-constrain fractional targets
**Where:** `pfe.py:550-653`, `pfe.py:780-789`.

The repair MILP pins totals to `round(target)`, while continuous PFE uses bands
and direction splits can be fractional. It can reject a valid band-feasible
integer solution. Its 20-second limit and repair statistics are not exported.

**Fix:** use the same integer-feasible bands, export MILP status/runtime/objective
and add overlapping/fractional-target tests.

### P1-3. PFE has no reproducible performance contract
**Where:** `pfe.py:197-458`, `build_sumo_demand.py:827-887`.

Profiling measured about 203 seconds in 288 interval solves, versus about 25
seconds candidate generation and 14 seconds route publication. The solver runs
200 IPF passes; tested micro-optimisations were slower and reverted.

**Fix:** add deterministic benchmark fixtures with time, memory, fit, route
diversity and rung metrics. Merge solver changes only when they beat it.

### P1-4. Demand orchestration is too large and coupled
**Where:** `build_sumo_demand.py` (1,300+ lines).
CLI, dates, subprocesses, candidates, constraints, PFE, feedback, variants, OD export and cleanup are coupled in one module.
**Fix:** split artifact repository, candidate provider, constraint builder, calibration executor, feedback loop and publisher.

### P1-5. Browser long-job handling is duplicated
**Where:** `web/index.html:1050-1660`.
Only recalibration recovers from malformed start responses; other job starts call `response.json()` directly and can show false failure.
**Fix:** one API/polling client with body validation, job IDs, backoff, cancellation, timeout and reload recovery.

### P1-6. Dynamic API data are inserted with `innerHTML`
**Where:** `web/index.html:1097-1103`, `1140-1145`, `1183`, `1416`.
Street names, errors and summaries become markup, creating an XSS boundary for poisoned artifacts/shared deployment.
**Fix:** fixed DOM nodes with `textContent`; remove inline scripts and add CSP.

### P1-7. Scenario parsing assumes absent output is known zero
**Where:** `run_scenario.py:738-761`.
Partial XML or failed intervals would appear as quiet traffic.
**Fix:** validate edge/interval coverage before zero-fill and publish coverage.

### P1-8. Closure losses are not seed/variant attributable
**Where:** `run_scenario.py:822-858`, `closure_metrics.py`.
Truncation/dropping is summed over variants then displayed beside one-variant seeds, weakening uncertainty reporting.
**Fix:** retain metrics keyed by variant and seed.

### P1-9. External data/training inputs are not uniformly pinned
**Where:** `fetch_deso.py`, `build_candidates.py`, `dirsplit/*`.
Live APIs/geocoding can change outputs without uniform source dates, checksums, retry outcomes or exclusion reasons in models/runs.
**Fix:** snapshot sources with hashes/licences and mandatory exclusion reports.

### P1-10. Input schema discovery remains fragile
**Where:** `build_data.py:365-413`, `607-613`, `668-672`.
Changed delivery names, encodings or labels can be partly inferred instead of rejected against a declared schema.
**Fix:** versioned input manifest/schema and sensor/date/quarter release gates.

### P1-11. Local/UTC time convention is fragile around DST
**Where:** `build_data.py`, `web/provider.js`, `web/clock.js`.
Timezone-less epochs rely on an implicit convention and cannot encode DST ambiguity.
**Fix:** explicit-offset ISO timestamps or epoch milliseconds plus display zone and DST contract tests.

### P1-12. `serve.py` is not a shared-production server
**Where:** `serve.py:310-338` and startup.
`SimpleHTTPRequestHandler` lacks production authentication, structured logs, request limits, persistent queue and graceful worker lifecycle.
**Fix:** local-only use; ASGI/WSGI plus a separate worker for shared deployment.

## P2 - Important Improvements

1. Generated runtime data and tracked artifacts cause large unrelated diffs; separate fixtures, runtime runs and release exports.
2. JSON contracts are documented but not schema-validated; use dataclasses/Pydantic/JSON Schema for flow, demand, scenario, job and signal data.
3. `serve.py:443-448` parses scenario identity from human stdout; return a machine-readable result path instead.
4. Browser polling has no cancellation/deadline and can disguise unreachable server as a permanent job.
5. Signal UI has paired deltas but no practical-effect threshold or confidence interval, so small effects can look actionable.
6. Norwegian direction-split transferability is weak; quantiles are honest but not local validation. Prioritize Gothenburg directional observations.
7. Six counters cannot identify citywide OD; mark every export as one plausible calibrated assignment.
8. Pin Python/SUMO/HiGHS dependencies and generate a startup compatibility report.
9. Move historical narrative out of large functions into docs so interfaces/invariants are easier to review.

## Rechecked Earlier Findings

- Scenario zero-versus-missing rendering is addressed by all-web-edge aggregation; coverage validation remains P1.
- Coordinate CSV mixing has a historical repair; strict schema gating remains required.
- Multi-day LOSO metadata mismatch is addressed in `validate_sim.py`.
- Rounding without bound checks is repaired, but P1 relaxation/integer-band risks remain.
- Signal provenance improved, but physical/legal validation remains P0.

## Evolution Roadmap

### Phase 1 - Reproducible execution foundation
Implement immutable run directories, content hashes, manifests, pinned environment, persistent jobs and atomic active-run publication.

### Phase 2 - Reliable local operator product
Convert mutations to POST job creation, centralize browser request/polling behavior, add lifecycle tests and retain prior active results after failure.

### Phase 3 - Scientific simulation validity
Specify experiment protocols, validate signal conflicts/provenance, add coverage gates and record uncertainty/validity grades in every result.

### Phase 4 - Measured performance work
Create PFE/SUMO benchmarks and CI budgets. Then investigate active-constraint reduction, validated warm starts, caching and worker orchestration without changing fit or uncertainty.

### Phase 5 - Product-grade modelling
Add schema-checked sensor intake, local directional ground truth, held-out forecast/incident validation and first-class confidence fields.

## Simulation Deep Dive - Concrete Faults

### SIM-P0-1. Vehicle animation can represent different demand than map flows
**Where:** `run_scenario.py:818-821`, `861-869`, `888-891`, `export_trajectories()`.
Road colors aggregate seeds across q50/q10/q90 demand variants, but trajectories always use `variants[0]` with seed 1000.
**Impact:** visible cars can show q50 while the road map shows a multi-variant average, without disclosure.
**Fix:** export a selected seed/variant with labels, or do not render individual vehicles; add trajectory seed/variant metadata.

### SIM-P0-2. Stale q10/q90 route files can contaminate a new demand run
**Where:** `run_scenario.py:492-501` and mutable `sumo/` artifacts.
`demand_variants()` loads `_v1` and `_v2` whenever files exist, without checking demand signature or declared current variant count.
**Impact:** a q50-only build can silently mix old q10/q90 route files into a new baseline or closure simulation.
**Fix:** run-directory route files, metadata-declared variant list and hash validation before every SUMO run.

### SIM-P0-3. SUMO can drop vehicles without scenario-level accounting
**Where:** `run_scenario.py:581-586`, `504-639`, `822-858`.
`--ignore-route-errors true` permits invalid/unroutable trips to disappear. Scenario JSON reports preprocessing drops but not all SUMO discards.
**Impact:** a closure can appear to improve delay/flow because trips vanished from the population.
**Fix:** parse SUMO statistics every run; reconcile loaded, inserted, arrived, waiting, teleported and discarded vehicles; disqualify imbalance.

### SIM-P1-1. Windowed-closure prefilter uses optimistic free-flow arrivals
**Where:** `run_scenario.py:366-490`.
Preprocessing decides active closure encounters from free-flow travel time, while real simulated congestion changes arrival time.
**Impact:** truncation/drop reporting can disagree with the closure a vehicle actually encounters in SUMO.
**Fix:** let SUMO establish actual encounters, or use conservative arrival intervals and report uncertainty.

### SIM-P1-2. Closure windows can affect unexported flush time
**Where:** `structured_closures()` at `run_scenario.py:171-203`; parsing at `717-731`.
Closure windows may extend to `duration_s + 3600`, while exported map flows stop at the calibrated duration.
**Impact:** closure effects during flush can exist without 15-minute scenario flow representation.
**Fix:** restrict closures to exported intervals or export/label flush intervals.

### SIM-P1-3. Three seeds are too few for confidence claims
**Where:** `run_scenario.py:150-151`, `734-761`.
Confidence is derived from mean CV over normally three runs; this is unstable, mixes seed and direction uncertainty, and is not calibrated correctness probability.
**Fix:** report separate uncertainty sources, sample counts/intervals and calibrate confidence against held-out sensor error.

### SIM-P1-4. Rounded means can hide seed-level simulation failure
**Where:** `run_scenario.py:753-761`.
The map exports integer-rounded seed means. A seed with queues, teleports or spillback can be averaged away; closure integrity checks only one condition.
**Fix:** persist seed-level health metrics and flag/disqualify aggregate results when any seed exceeds thresholds.

### SIM-P1-5. Rerouting is geographically limited, not demand-aware
**Where:** `run_scenario.py:291-314`, `822-830`.
The rerouter is attached in a fixed closure radius. Upstream traffic may reroute too late or queue unrealistically.
**Fix:** derive rerouter origins from closure-reachable paths/travel time and benchmark it against the radius approach.

### SIM-P2-1. Scenario export checks XML parseability, not simulation completeness
**Where:** `parse_edgedata()` at `run_scenario.py:717-731`.
It does not validate interval count, begin/end boundaries, coverage or seed/network/demand identity.
**Fix:** add output validation gate and a simulation-health report before aggregation.

## Current Verified Findings - 2026-07-12

This section supersedes the severity order above. It was produced from a
fresh source inventory, direct artifact checks and a full pytest run. Only
findings with a concrete code path and reproducible evidence or primary-source
documentation are included. Local uncommitted changes in `build_candidates.py`
and generated scenario files were observed but not changed or attributed.

### P0 - Individual-vehicle simulation is currently misleading

#### P0-1. Trips are generated with destinations immediately after sensors
**Where:** `build_candidates.py:795-835`, `1016-1050`, `1229-1410`; `pfe.py:708-951`.

`natural_far_end_weights()` verifies only that a sensor lies on the shortest
path. It does not require any onward path distance after the sensor. The
generators then favour the closest permitted endpoint using
`exp(-distance/gravity_km)`, and PFE can repeat that route to match a count.

**Measured effect:** 5,973 of 22,301 vehicles in the active calibrated route
file (26.8%) have their last sensor on the final edge or one edge before it.
For internal-work outbound trips, 2,593 of 5,403 (48.0%) do. This directly
causes cars to end at the first junction after a sensor. It is not a renderer
failure or SUMO teleport.

**Required fix:** establish destination and route-choice priors before count
calibration, add a validated downstream network-distance condition, replace
the zero-mode exponential deterrence shape, and publish calibrated trip-length
and sensor-tail distributions. The detailed evidence is in
`DESTINATION_BIAS_RESEARCH_2026-07-12.md`.

#### P0-2. Rerouted closure vehicles are omitted from trajectory playback
**Where:** `run_scenario.py:654-726`, particularly `veh.find("route")`.

SUMO writes a rerouted vehicle's final route inside `routeDistribution`; the
exporter reads only a direct `route` child and skips the vehicle. The map can
therefore aggregate that vehicle's edge flow while the individual-car layer
does not show it.

**Primary evidence:** [SUMO VehRoutes](https://sumo.dlr.de/docs/Simulation/Output/VehRoutes.html)
documents the nested `routeDistribution` output for replaced routes.

**Required fix:** parse the final nested route as well as direct routes; add a
rerouted-vehicle fixture and publish trajectory export coverage by seed and
demand variant.

#### P0-3. Unfinished vehicles are absent from the car layer
**Where:** `run_scenario.py:654-726`; `web/render.js:181-239`.

The exporter does not request `--vehroute-output.write-unfinished`. SUMO
therefore exports only finished trips by default. A queued or still-driving
vehicle may affect the edge-flow aggregate but be invisible in the animation.

**Primary evidence:** [SUMO VehRoutes](https://sumo.dlr.de/docs/Simulation/Output/VehRoutes.html)
documents the default and the unfinished-trip option.

**Required fix:** export unfinished vehicles with an explicit state/last point,
or state that the layer contains completed trips only; reconcile it with SUMO
vehicle totals before publishing.

### P1 - High-risk correctness, safety and reproducibility defects

#### P1-1. Scenario publication has no complete vehicle-health gate
**Where:** `run_scenario.py:518-651`, `822-955`.

Publication follows edgeData parsing, not a per-seed/variant reconciliation of
loaded, inserted, arrived, running, waiting, teleported and ignored-route-error
vehicles. A plausible mean flow can therefore conceal failed insertion or
teleports.

**Primary evidence:** [SUMO Statistic Output](https://sumo.dlr.de/userdoc/Simulation/Output/StatisticOutput.html)
and [SUMO Output](https://sumo.dlr.de/docs/Simulation/Output/index.html) define
these categories and caution that fixed-end comparisons must include missing
vehicles.

**Required fix:** immutable per-seed health reports and a publication gate on
vehicle conservation, route errors and teleport thresholds.

#### P1-2. Road colours and visible cars represent different populations
**Where:** `run_scenario.py:872-913`, `928-955`.

Road flows average seeds and q50/q10/q90 demand variants, while the trajectory
export always runs `variants[0]` with seed 1000. The UI does not disclose that
the car layer is one representative run rather than the map aggregate.

**Required fix:** label seed/variant, let the operator select one, or remove
individual cars when displaying an uncertainty aggregate.

#### P1-3. Only two long-running job types are cancellable
**Where:** `serve.py:145-239`, `409-659`; `web/index.html` job controls.

Cancellation tracks direct closures and recalibration, but not the similarly
long suggestion or signal-optimisation jobs sharing the same simulation lock.

**Required fix:** one durable job model for every job type, with id, state,
process-group handle and common cancellation endpoint.

#### P1-4. Mutating API operations use unauthenticated GET
**Where:** `serve.py` handlers and `web/index.html` fetch calls.

Closures, recalibration, cancellation, suggestion and signal optimisation are
state-changing GET requests. This is acceptable only on trusted localhost, not
shared deployment.

**Required fix:** authenticated POST JSON plus CSRF protection; GET only for
read-only status and artifacts.

#### P1-5. Shared mutable artifacts prevent reproducible provenance
**Where:** `build_sumo_demand.py`, `run_scenario.py`, `serve.py`, signal tools.

The server lock cannot protect command-line runs, restarts or another server
process from overwriting `sumo/` and `web/data/scenarios/`.

**Required fix:** immutable run directories with input/environment hashes and
atomic activation after validation.

#### P1-6. Recalibration deletes valid scenarios before the replacement exists
**Where:** `serve.py:628-641`.

After demand succeeds, every scenario JSON is removed before the new baseline
is validated. A later failure leaves no prior valid baseline.

**Required fix:** build into a new run and switch the active manifest only
after the replacement passes its health gate.

### P2 - Important verified engineering gaps

1. Dynamic suggestion/signal results still cross `innerHTML` boundaries; use
   DOM construction with `textContent` and add CSP before shared deployment.
2. PFE has no deterministic benchmark for runtime, memory, diversity,
   relaxation-rung distribution and fit on a realistic fixture.
3. Raw input, external API and model provenance is not uniformly hashed into
   every scenario manifest; add a versioned intake/environment manifest.

### Rechecked Status

- The prior stale q10/q90 issue is resolved: current `demand_variants()`
  follows declared `n_variants` and rejects an incomplete declared set.
- Direct closure/recalibration cancellation now kills its registered process
  group and has integration tests. The remaining coverage gap is P1-3.
- The full test suite was run during this audit. Passing tests do not cover
  the trajectory XML schemas or destination-tail distribution gates above.

## Verified Review Of The 2026-07-13 Demand Changes

This review covers commits `51ad47f` and `6632bfc`. It was performed after
the destination-clustering and purpose-length changes were committed, with a
focused demand/PFE test run (`211 passed`). The tests establish unit-level
assertions; the findings below concern production paths they do not exercise.
Application code was not changed as part of this review.

### P0 - Structure guards are bypassed in a supported default configuration

**Where:** `build_sumo_demand.py:1525-1540`; `pfe.py:1013-1042`.

The new near-sensor-destination and trip-length-bin guards are constructed
only by `run_pfe_variants_flat_parallel()`. That function is called only when
`has_split_quantiles()` returns true. When `sumo/direction_split.json` lacks
q10/q90 fields, the supported one-variant path calls `pfe.calibrate()` instead.
`pfe.calibrate()` accepts neither structure groups nor a group-builder, so it
publishes unrestricted PFE results.

This is not merely diagnostic: the new guards prevent PFE from re-amplifying
routes ending near a sensor or routes in the shortest-length bin. The exact
regression that the two commits claim to fix can return whenever direction
quantiles are unavailable, disabled, or not yet built.

**Required fix:** use one PFE orchestration path for one or many variants,
always build/pass structure groups, and add an integration test with a valid
direction-split file that deliberately has no quantiles. Assert that a group
cap constrains the final integer route file, not only the continuous solver.

### P0 - Calibrated vehicle purposes are not conditioned on departure time

**Where:** `pfe.py:1024-1028`, `890-934`; `build_candidates.py:1362-1365`.

Candidate generation selects a purpose from the intended outbound hour.
Calibration then solves every 15-minute interval over **all** route shapes,
regardless of each candidate's sampled hour. When route counts are written,
`source_pool[dup % len(source_pool)]` merely rotates provenance records among
duplicates. It does not filter by the calibrated quarter, reweight by
`purpose_shares_for_hour()`, or preserve the sampled purpose distribution.

Consequently, `calibrated.agents.json` labels are not a calibrated purpose-by-
time demand model. In the current artifact, work-labelled traffic is 35.0% at
07:00 and 28.4% at 18:00, while the declared weekday prior has 85.9% work at
07:00 and 47.0% at 18:00 before through/external categories. The new
purpose-specific distance scale changes route inventory but cannot guarantee a
purpose-specific or time-specific final vehicle population.

**Required fix:** partition route-shape variables by purpose and broad time
period, or retain purpose-time availability mass per shape and constrain each
calibrated quarter to it. Allocate vehicle provenance from the same
purpose-time distribution, and validate published agents after PFE.

### P1 - Multi-day demand breaks the new purpose-time relationship

**Where:** `build_candidates.py:1601-1627`, `1767-1797`;
`build_sumo_demand.py:461-487`.

For multi-day runs, a template's geometry and purpose are sampled using
`h_out` inside `generate_sensor_anchored_trips()`. `generate_day_block()` then
discards that hour and gives the template an independently resampled departure
hour from the exact-day profile. An AM work-purpose geometry can therefore be
emitted in the evening, and a leisure-purpose geometry in the AM peak.
Template reuse makes this systematic for later days sharing a pool key.

Because destination distance now varies by purpose, the multi-day path does
not preserve the stated joint distribution
`P(purpose, departure time, trip length | day type)`.

**Required fix:** retain a purpose-time stratum in each template and resample
only within that stratum, or generate/reweight purpose-time route pools. Add a
two-day test proving that both weekday and weekend output respect the intended
purpose-time distribution after template reuse.

### P1 - Weekday length-scale normalisation uses the wrong average

**Where:** `build_candidates.py:251-270`.

The code says `_WEEKDAY_AVG_MIX` preserves aggregate weekday calibration. It
is the flat arithmetic mean of 24 hourly purpose shares, but trips use
`daily_shape(False)`, whose peak hours have more mass. The actual
traffic-weighted weekday mix is work 52.77%, service 30.32%, leisure 16.91%,
not the hard-coded 59.8% / 24.0% / 16.2%.

On current constants the traffic-weighted mean scale is `1.00898`, not the
documented exact `1.0`. The numerical error is modest, but the contract is
false and will drift if either hourly table changes.

**Required fix:** derive the mix as
`daily_shape(False) @ PURPOSE_HOURLY_WEEKDAY`, or normalise inside the
day-profile pipeline. Test the traffic-weighted identity, not the flat-hour
identity.

### P2 - LP fallback can reintroduce a group after it was dropped

**Where:** `pfe.py:753-773`.

The relaxation contract says group caps are discarded with bounds at
`RUNG_RELAX_NOBND` so measured counts always win. The entropy pass does this,
but the final LP fallback calls `solve_interval(..., groups=groups)` again.
If entropy fails and the cap conflicts with counts, the LP can report an
interval infeasible even though the no-group policy says it should serve the
measurements.

This is a fallback-path risk, not evidence that the active artifact failed.

**Required fix:** pass `groups=None` to the LP fallback after the no-bounds
stage, and test an entropy failure with a feasible unconstrained LP.

## Resolution Of The 2026-07-13 Findings

Implemented after the review; the demand solver's route-count variables and
network simulation were not expanded.

- The final q50-only path now uses `run_pfe_variants_flat_parallel()` too, so
  destination and trip-length structure guards apply with or without q10/q90
  direction variants.
- The LP fallback now retains the documented counts-first relaxation policy:
  it does not restore bounds or structure groups after the no-bounds rung.
- Multi-day template reuse samples each retained template hour conditional on
  its purpose and the exact-day profile, rather than independently assigning
  an arbitrary hour.
- PFE publication allocates each quarter's **exact candidate purpose mix** to
  the selected vehicles. It records `purpose_route_compatible` per vehicle
  and a demand-level compatibility diagnostic when a selected route lacks a
  source candidate of the allocated purpose. This preserves simulator routes
  and performance while making the remaining OD-purpose uncertainty visible.
- Weekday purpose-length normalisation now uses the measured traffic-weighted
  `daily_shape(False)` rather than a flat 24-hour arithmetic mean.

Regression coverage includes structure-group solver behaviour, the LP
fallback, purpose allocation, purpose-compatible provenance and
multi-day conditional sampling. The focused suite passed with `216 passed`;
the full project suite passed with `745 passed, 21 skipped`.
