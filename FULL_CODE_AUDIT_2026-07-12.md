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

