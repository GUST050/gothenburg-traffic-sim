# Program Improvement Plan

## Goal

Turn the current, feature-rich prototype into a reproducible and honest
traffic decision-support tool. The priority order is deliberate: trust the
run, validate the model, then extend optimisation and UI. `ARCHITECTURE.md`
remains the structural source of truth.

## Rules for every phase

- Keep the fixed contracts: stable edge IDs, WGS84, `null` means missing,
  and browser flow access only through `flowAt`.
- Measure before and after every performance change. Keep a baseline run with
  elapsed time, peak memory where practical, route count, GEH, relaxation
  rungs, structural-demand metrics and simulation-health metrics.
- Add a focused regression test for every real defect. Run the complete suite
  before merging.
- Do not claim a per-street result is accurate merely because it was
  simulated. Display the confidence source and uncertainty.

## Phase 1 - Immutable, health-gated runs

**Why first:** current files under `sumo/` and `web/data/scenarios/` are shared
mutable state. A completed-looking scenario can hide failed vehicles, a stale
input or a partial replacement.

1. Create a versioned run directory for every demand build and scenario:
   inputs, command, source commit, network fingerprint, SUMO version, random
   seeds, direction variant, timings and outputs.
2. Build demand/scenarios into that directory; atomically switch the web
   manifest only after all validation gates pass. Keep the previous active
   manifest on failure.
3. Emit a per-seed health report from SUMO: loaded, inserted, arrived,
   running, waiting, teleported, route errors and ignored vehicles.
4. Reject publication when vehicle conservation, interval coverage, seed
   success or teleport thresholds fail. Show a concise failure reason in UI.
5. Replace ad-hoc job globals with one durable job record for recalibration,
   closure, suggestion and signal optimisation. Every job gets an ID, status,
   log path, process group and cancellation endpoint.

**Acceptance gate:** rerunning or cancelling a job cannot corrupt the active
scenario; every published scenario links to complete inputs and health data.

## Phase 2 - Make individual-car playback truthful

1. Export direct routes and final routes nested in SUMO `routeDistribution`.
2. Request unfinished vehicle routes and represent unfinished/queued vehicles
   explicitly rather than silently omitting them.
3. Store the trajectory seed and direction variant in the artifact. The UI
   must label cars as a representative run when road colours are a Monte Carlo
   aggregate, or let the user select the same seed/variant.
4. Reconcile trajectory count against the seed health report and fail the
   artifact when coverage is incomplete.

**Acceptance gate:** no vehicle that contributes to the displayed seed’s road
flows silently disappears from playback.

## Phase 3 - Revalidate demand scientifically

1. Freeze one reproducible demand configuration and rerun leave-one-sensor-out
   validation after the current sensor-conditioned OD, purpose-time and
   structure-preservation changes. Replace stale LOSO numbers everywhere.
2. Publish one validation report per build: GEH, held-out recovery,
   candidate-to-calibrated trip-length drift, onward distance after last
   sensor, sensor-passage distribution, purpose-time allocation and
   purpose-route compatibility.
3. Define explicit warning/fail thresholds from baselines, not visual taste.
   A good GEH alone must never pass a structurally implausible build.
4. Treat the current purpose-route compatibility diagnostic as an uncertainty
   until a lightweight purpose-by-route constraint or local OD data supports a
   stronger claim.
5. Acquire permitted local travel-diary or OD data if available. Use it only
   to improve broad purpose x time x day-type distance priors; do not infer
   individual destinations from six counters.

**Acceptance gate:** the project can state, with current numbers, which sensor
fit and structural properties are validated and which remain priors.

## Phase 4 - Simplify the demand/simulation architecture

1. Split `build_sumo_demand.py` into small modules: date/window intake,
   candidate build, bounds/priors, calibration, feedback and publication.
2. Make one calibration orchestration path for one or many direction variants;
   retain the shared flat worker pool and structure guards in all cases.
3. Centralise artifact schemas as typed dataclasses/validation functions.
   The web manifest, demand metadata, health report and confidence report
   should have versioned schemas.
4. Convert global mutable subprocess/job state into explicit run objects.
5. Keep a deterministic realistic benchmark fixture for PFE: runtime, memory,
   GEH, route diversity, relaxation rungs and structural metrics.

**Acceptance gate:** a developer can run and profile one named stage without
silently regenerating unrelated products.

## Phase 5 - Measured performance work

1. Record phase timings for candidate generation, PFE preparation, interval
   solving, integer repair, SUMO seeds, aggregation and trajectory export.
2. Optimise only the measured dominant stage. Preserve model outputs within
   defined tolerance before accepting a speed-up.
3. Cache immutable network geometry, activity fields, observability and route
   shape pools by fingerprint; never cache date-dependent targets or outputs
   without their full input signature.
4. Benchmark worker counts. More processes are not automatically faster on
   small windows or memory-constrained machines.

**Acceptance gate:** each claimed improvement includes before/after timing and
the same validation gate result.

## Phase 6 - Decision-support product and security

1. Change mutating HTTP operations to authenticated POST requests. Keep GET
   read-only; add CSRF protection before any shared deployment.
2. Build task-focused views: normal/future simulation, road closure, closure
   timing suggestion, and signal experiment. Each view shows inputs, run
   status, results, confidence and limitations for that task only.
3. Use safe DOM APIs for dynamic API results and add a Content Security Policy.
4. Make uncertainty legible: measured, mathematically bounded, prior-driven
   and Monte Carlo variability should not be merged into one unexplained
   colour or percentage.

**Acceptance gate:** an operator can understand what was run, whether it
finished, and what the result does and does not establish without reading logs.

## Phase 7 - Signal optimisation only with defensible inputs

1. Keep signal results labelled as synthetic until real signal plans, movement
   permissions, detector logic and turn restrictions are available.
2. Import and validate those plans before claiming legal/safe timing plans.
3. Optimise against time-of-day demand, multiple seeds and closure conditions;
   report baseline comparison, queue spillback, pedestrian clearance and
   robustness rather than only an objective score.

**Acceptance gate:** a signal recommendation is reproducible, physically
feasible and compared fairly with its exact baseline.

## Recommended execution order

1. Phase 1 run registry and health gates.
2. Phase 2 trajectory correctness.
3. Phase 3 fresh LOSO and structural validation report.
4. Phase 4 demand refactor plus benchmark fixture.
5. Phase 5 measured speed improvements.
6. Phase 6 durable job/API/UI workflow.
7. Phase 7 real-data signal upgrade.

Do not start a new forecasting model, a city-wide expansion or more signal
heuristics before phases 1-3 are complete. They would multiply outputs before
the system can prove which outputs are trustworthy.
