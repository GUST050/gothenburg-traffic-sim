# Comprehensive Improvement Review - 2026-07-10

## Purpose and Scope

This is a technical improvement review of the current repository: data intake,
feature/model pipeline, direction-split package, observability, candidate
generation/PFE calibration, SUMO execution, scenario API, frontend, tests,
build system, and documentation. It is a roadmap, not a request to rewrite the
project at once.

> ## Most Important Changes - Act First
>
> 1. **Correct scenario confidence before presenting results.** A stored
>    confidence of `0` is currently converted to `0.5` during scenario export;
>    6,569 of 7,147 current map edges are affected. Fix this explicit-zero
>    handling and retain unrounded machine precision. See **13.1-13.3**.
> 2. **Validate the integer routes actually given to SUMO.** Continuous PFE
>    feasibility does not prove the rounded route file respects measured and
>    level-2 constraints. Add post-rounding verification and a constraint-aware
>    repair/solver before publishing demand. See **4.2-4.3**.
> 3. **Make all artifacts versioned and atomically published.** Current
>    existence-based caching and in-place writes can reuse stale structural
>    products or expose partial files to the server/browser. Introduce input
>    fingerprints, staging directories, and atomic publication. See
>    **2.1-2.4**.
> 4. **Make confidence empirical and decomposed.** Distance times Monte Carlo
>    CV is a display heuristic, not a validated probability of correctness.
>    Separate measured support, calibration error, demand/split/route/simulator
>    uncertainty, and simulation completeness. See **7.1-7.3**.
> 5. **Treat closure comparison as an accessibility-and-delay problem.** A
>    closure run can truncate unreachable trips, making raw time-loss
>    comparisons incomparable with baseline. Compare identical cohorts and
>    elevate active-window closed-edge flow to an integrity gate. See
>    **13.9-13.10**.

`ARCHITECTURE.md` is the structural authority. Recommendations below preserve
its six stages and the important contracts: stable edge IDs, WGS84 map data,
datetime-based time handling, `null` meaning missing rather than zero, and the
provider `flowAt(edgeId, time)` seam.

The repository was actively changing during review. In particular, the earlier
scenario-zero-flow and post-rounding-bound findings were being addressed in the
working tree. Do not treat an item here as proof that it remains unfixed; use it
as a design and verification checklist against the current revision.

## Executive Priority Order

### P0 - Before relying on new outputs

1. Make every generated artifact atomic, versioned, and provenance checked.
2. Finish the integer-feasible PFE path: validate the *written routes*, not
   merely the continuous relaxation.
3. Define confidence as a measured, decomposed quantity rather than one
   distance/CV scalar.
4. Repair stale documentation and make the supported workflow unambiguous.
5. Add end-to-end tests for the web-triggered, single-day and multi-day paths.

### P1 - Highest return in speed and maintainability

1. Split `build_sumo_demand.py`, `build_candidates.py`, `run_scenario.py`, and
   `web/index.html` into focused modules with typed interfaces.
2. Replace existence/count-based caches with content fingerprints and a build
   manifest.
3. Profile PFE/candidate generation and browser rendering before optimizing;
   then use sparse/batched representations and viewport-aware rendering.
4. Move expensive, mutable SUMO work into isolated job directories controlled
   by one job manager.

### P2 - Methodological upgrades

1. Re-run all validation after meaningful candidate/PFE changes and preserve
   result provenance.
2. Separate demand, direction split, route-choice, and simulator uncertainty.
3. Add temporal backtests and forecast intervals before presenting 2027 output
   as a point prediction.
4. Use a calibration formulation with an explicit integer-feasible final
   solution or an auditable post-rounding repair.

## 1. Architecture and Boundaries

### 1.1 Create a small domain package instead of importing top-level scripts

The project currently has useful functions inside executable scripts and then
imports them from other executable scripts. Examples include
`assignment_priors.py` importing `load_direction_split` from
`build_sumo_demand.py`, `observability.py` importing geometry from
`build_data.py`, and several files depending directly on `web/data/...` paths.
This makes imports expensive, creates hidden dependency direction, and makes
unit testing depend on process working directory.

Recommended structure:

```
traffic/
  config.py             # paths, epochs, schema versions; no side effects
  contracts.py          # dataclasses/Pydantic models for artifact schemas
  time.py               # date range, slot, timezone, DST policy
  graph.py              # edge ID, graph loading, geometry/projection helpers
  intake.py             # CSV normalization and sensor metadata
  observability.py      # stage B pure computations
  direction.py          # split loading/application
  demand/
    targets.py
    candidates.py
    pfe.py
    priors.py
  simulation/
    sumo.py
    scenarios.py
    metrics.py
  artifacts.py          # fingerprinted, atomic artifact read/write
  jobs.py               # server/CLI job lifecycle
```

Keep the existing files as thin CLI adapters. A CLI should parse arguments,
construct an explicit configuration object, call library code, and render a
report. It should not itself own business logic.

Benefits: dependency direction becomes clear, expensive external imports are
isolated, tests call pure functions, and command-line, API, and future worker
implementations share the same orchestration.

### 1.2 Centralize configuration and eliminate duplicated constants

Epochs, 15-minute slot assumptions, paths, default dates, speed/lane defaults,
and confidence-related assumptions appear across modules. Duplicated values
will eventually drift, especially as 2025 historical and 2027 forecast modes
coexist.

Create a typed `ProjectConfig` with separate immutable configuration for:

- source datasets and time axis;
- structural reference artifacts;
- SUMO execution parameters;
- PFE solver parameters;
- frontend export settings; and
- schema/model versions.

Pass it explicitly to functions. Avoid globals such as `SUMO_DIR`, `OUT_DIR`,
and hardcoded relative paths inside the computational layer. Relative paths are
acceptable in a CLI adapter only after resolving them against repository root.

### 1.3 Formalize the artifact contracts

The JSON contracts are central but are manually interpreted in many places.
Define a schema/model for `network.geojson`, historical/forecast flow payloads,
`demand_meta.json`, scenario payloads, observability artifacts, direction
splits, and scenario manifest entries.

Each artifact should carry at least:

- `schema_version`;
- producer name and git revision or source-tree fingerprint;
- input artifact fingerprints;
- generation time and command/config fingerprint;
- complete time-axis description (`epoch`, interval, number of slots,
  timezone convention); and
- network fingerprint, not merely edge count.

Validate on read. A bad/stale artifact should produce a clear error naming the
incompatible producer/input, not silently be reused because a file exists.

### 1.4 Keep decision history out of executable source

Many modules contain valuable but very long narratives about past investigations
and fixes. They make the files hard to scan, increase merge conflicts, and
obscure actual behavior. Retain the reasoning, but move extended chronology to
`docs/decisions/` as short ADRs or dated investigation notes. Keep code comments
to the invariant, counterintuitive rule, or reason a local implementation looks
unusual.

For example, an ADR can record why mesoscopic mode and local rerouters are the
default; `run_scenario.py` only needs a concise comment plus a link/reference.

## 2. Build, Reproducibility, and Artifact Lifecycle

### 2.1 Replace file-exists caching with content fingerprints

`ensure_bounds`, `ensure_observability`, `ensure_assignment_priors`, and other
helpers reuse outputs based on file presence or a weak condition such as number
of GeoJSON features. Equal edge counts do not imply equal topology, geometry,
sensor snapping, capacities, priors, or solver settings.

Use a canonical SHA-256 fingerprint of the relevant inputs and configuration.
For graph artifacts, fingerprint stable IDs plus topology and required
properties; for raw input, hash source files or store their byte hashes in an
intake manifest. Rebuild when producer version, input fingerprint, or parameter
fingerprint differs.

This is correctness work, not merely an optimization. Stale structural bounds
or priors can make calibration infeasible or quietly bias a new simulation.

### 2.2 Use atomic writes and publish only complete scenario sets

Several writers overwrite JSON/XML at their final path. A browser, API request,
or another process can observe truncated JSON or a scenario manifest that
references files not yet written.

DONE (2026-07-10), narrow scope — the browser-visible JSON writes: added
`run_scenario.atomic_write_json()` (write to a same-directory temp file via
`tempfile.mkstemp`, then `os.replace()`, which POSIX guarantees is atomic;
temp file cleaned up on any write failure) and applied it to the scenario
JSON, `index.json`, and the trajectory JSON — the three files a live browser
polls with `cache: 'no-store'` while `serve.py`'s recalibration/close thread
or a CLI rebuild overwrites them in place. `build_sumo_demand.py`'s
`clear_stale_scenarios()` got the same treatment for its `index.json` reset.
Verified with unit tests (old-content-survives-until-new-write-lands,
temp-file-cleanup-on-failure) and a real `run_scenario.py --close` run.

Not done — the wider scope of this item:

1. `fsync` for important outputs;
3. validate/read the write back before replacing;
4. write SUMO's own intermediate XML (routes, additional files) atomically
   too — only the JSON the browser reads was in scope here.

For a scenario, write all data files into a unique staging/job directory and
publish the manifest entry only after every file is complete. Use a generated
scenario ID, rather than repeatedly overwriting `baseline.json` or a
closure-derived filename. Retain the previous successful scenario until the
replacement is fully publishable.

### 2.3 Introduce a build manifest and proper task runner

The Makefile is a convenient command list but not a dependency graph: targets
rerun without input/output declarations, external dependencies are invisible,
and a partial failure can leave a mixed generation.

Use one of these approaches:

- a small Python task runner built around the artifact manifest;
- Snakemake for explicit file/dependency execution; or
- `nox`/`tox` plus a focused Python pipeline command if a full workflow engine
  is unnecessary.

The runner should support `plan`, `status`, `verify`, `rebuild --stage`, and
`clean --staging-only`; it should never need to delete valid published data to
recover from a failed rebuild.

### 2.4 Isolate jobs and make concurrency explicit

The web server correctly serializes its own close/recalibration requests, but
CLI runs and other processes still share mutable files under `sumo/` and
`web/data/scenarios/`. This makes concurrent work unsafe and makes failures
hard to diagnose.

Use a `runs/<job-id>/` directory containing demand, candidates, routes,
additional files, logs, raw SUMO outputs, metadata, and final exported
scenario. A job manager can lock only publication, while independent dry runs
or validation runs remain possible. It should store durable state in JSON or
SQLite, not only a process-local thread dictionary, so server restart does not
lose a running/failed job's status.

### 2.5 Record environment and deterministic seeds

Record Python version, package lockfile hash, SUMO version, OSM graph hash,
random seed sequence, OS, and relevant command arguments in each demand and
scenario artifact. Pin dependencies with a lockfile and split runtime,
development, and optional SUMO requirements. This is essential for comparing
calibration changes honestly.

## 3. Data Intake and Spatial Foundation

### 3.1 Make the drop-folder intake strict and self-describing

The data-in directory needs an explicit manifest or filename convention:

- traffic source files are selected by a documented pattern or manifest;
- coordinate file is explicitly named/referenced and excluded from raw traffic
  ingestion;
- optional sensor metadata is a separate required table; and
- unknown columns are retained for audit but ignored only deliberately.

Do not infer semantic roles from a broad `*.csv` glob when the folder contains
multiple data types. The prior bug review documents why this can mix coordinate
rows into observations.

### 3.2 Validate before normalizing

Implement an intake report with hard errors/warnings for:

- exact 15-minute grid alignment, rather than rounding arbitrary timestamps to
  the nearest slot;
- duplicate `(sensor, timestamp)` policy with source-file/row provenance;
- missing four-slot hours and DST policy;
- negative, fractional, implausibly high, and unexpectedly changed counts;
- per-sensor coverage rate and date range;
- direction metadata completeness;
- coordinate CRS and plausible geographic extent; and
- snapping distance/bearing gate failures.

Store row-level rejects in a quarantine CSV/Parquet and fail the pipeline above
a configured threshold. A print warning is insufficient for a source whose
data feeds hard constraints.

### 3.3 Make sensor metadata data-driven

`SENSOR_MEASURED_DIRECTION` and manual snapping are important but code-bound.
Move them to a versioned `data_in/sensors.csv` with sensor ID, observed level,
verified source, verification date, compass direction, optional manual edge,
and notes. Load it through the intake contract. That makes new-sensor changes
auditable and avoids a code release for a metadata correction.

### 3.4 Persist graph provenance and network quality metrics

The OSM snapshot is the right source of stable edge IDs. Add automatic checks
for disconnected components, removed self-loops, invalid geometry, duplicate
edge IDs, junction degree distribution, edge length/speed/lane outliers, and
network-to-SUMO edge/connection coverage. Build a concise graph QA report and
require it before demand calibration.

### 3.5 Clarify the time-zone model

The application deliberately treats epochs as UTC for browser formatting while
the source timestamps are Swedish local wall time. This can be valid as an
abstract slot axis, but it must be declared as such in every contract.

Define one `TrafficTime` policy: source timestamps are Europe/Stockholm local
slots; artifact serialization is timezone-naive ISO plus an explicit
`timezone_semantics: "local_wall_time"`; browser display uses the same nominal
clock; simulation time zero maps to that local slot. Do not rely on appending
`Z` to make browser parsing work, because that semantically changes local time
to UTC. If retaining UTC serialization, convert from localized timestamps
explicitly and test both DST transitions end to end.

## 4. Observability, Priors, Candidate Generation, and PFE

### 4.1 Separate mathematical bounds from behavioral plausibility bounds

The architecture distinguishes level-2 mathematical intervals from level-3
learned/assignment information. The current broad assignment field is supplied
as an upper bound for computational reasons. That is pragmatic, but it blurs
provenance: a behavioral estimate should not be reported as a mathematical
constraint.

Represent each constraint with `kind`, `source`, `hardness`, `confidence`, and
`explanation`. At minimum distinguish `conservation_bound`, `capacity_bound`,
`assignment_plausibility_cap`, `direction_prior`, and `corridor_prior`. The UI
and validation can then state what actually constrained an edge.

### 4.2 Treat all post-solve integer routes as the product to validate

The continuous PFE solution is not the demand sent to SUMO. The emitted integer
routes are. Every per-quarter report should calculate, from the written route
file, all measured residuals, level-2 bounds, prior deviation, vehicle total,
and route diversity diagnostics.

For correctness, choose one of:

- solve an integer program for constrained route counts, with a sparse MIP
  formulation and a time budget/fallback;
- use dependent/constraint-aware randomized rounding followed by a repair MIP
  only around violated edges; or
- preserve continuous route weights and let SUMO use scaled flows only if the
  simulator semantics support it faithfully.

The current measured-edge-aware rounding is a useful step, but it cannot prove
all bounds unless bounds participate in the rounding/repair stage.

### 4.3 Replace ad hoc IPF convergence with measurable solver diagnostics

The entropy solver is attractive for speed and dispersion, but sequential
projections across overlapping measured edges, intervals, and priors can
oscillate. Fixed iteration counts and averaging hide whether a particular
quarter actually converged.

DONE (2026-07-10): `pfe.py` now tracks and reports which stage of the
relaxation ladder produced each interval's solution
(`solve_interval_with_relaxation` returns `(solution, rung)`;
`write_calibration_report`'s `relaxation_summary` field rolls this up per
variant) — the "fallback use and relaxation rung" bullet below, plus
max-hard-constraint-violation-after-rounding (`bound_violations`, from an
earlier pass). Still log per interval:

- objective/entropy or KL change;
- prior residual statistics;
- iteration count; and
- candidate coverage / solve time/memory.

Use explicit convergence and infeasibility criteria beyond the rung marker.
Compare a representative sample to a trusted LP/MIP reference in regression
tests. Keep the exact solver as an oracle, not only a last-resort operational
fallback.

### 4.4 Reduce PFE work with sparse, batched data structures

The path-edge incidence relation is naturally sparse. Avoid repeatedly
constructing Python dictionaries, `set(cand.edges)`, and per-interval lists
for every route/edge. Precompute:

- CSR/CSC route-by-edge incidence;
- route-to-measured-edge and route-to-bounded-edge indices;
- per-day/slot targets in compact arrays; and
- route shape metadata once per candidate pool fingerprint.

Then batch quarters with the same active constraint pattern and use chunked
worker tasks. This reduces IPC overhead compared with sending large Python
dictionaries to one process per quarter. Benchmark memory as well as elapsed
time before and after.

### 4.5 Make candidate coverage a gate, not only a warning

An unserviceable measured edge means the calibrated output cannot satisfy a
hard observation. Continuing may be useful for diagnostics, but the resulting
demand should be marked failed/non-publishable for production simulation.

Define gates such as:

- every non-missing measured edge has at least a configured number of distinct
  candidate shapes;
- every sensor has routes in both plausible directions where applicable;
- candidate route lengths/detours pass quality filters;
- all active hard constraints are serviceable; and
- post-rounding hourly calibration meets the declared threshold.

Export a coverage report tied to the candidate fingerprint. Make the UI refuse
to label an output calibrated when the gate fails.

### 4.6 Revisit the assignment-prior mechanism after each solver change

The rationale for assignment priors was established against an earlier PFE
objective. The entropy solver explicitly seeds and selects relevant routes,
while upper-only assignment bounds may cap flows without encouraging them.
That interaction needs a fresh controlled ablation, rather than assuming the
previous LOSO lift persists.

Run the same dated, fingerprinted folds for: no assignment field; upper-bound
field; soft prior; and a principled entropy prior/route seed. Report median,
distribution, per-sensor values, calibration fit, runtime, and all inputs.
Avoid choosing on a seven-edge median alone.

### 4.7 Improve observability scaling and soundness

Per-edge min/max LPs can be expensive as the network grows. First restrict
them using graph decomposition, biconnected components, measured-neighborhood
screening, and reusable constraint matrices. Then cache/factor the shared
conservation system or use dual information where possible.

Methodologically, encode known sources/sinks, turn restrictions, signal
capacities, and directional consistency where available. A generic slack at
every node is a defensible approximation, but its magnitude should be a
parameter with sensitivity analysis, not a fixed hidden truth.

### 4.8 Quantify identifiability instead of implying a point OD is special

The OD matrix is correctly described as one plausible estimate. Improve it by
emitting alternative OD/route solutions sampled from a calibrated posterior or
from deliberate perturbations of candidate, direction, and prior assumptions.
Report which OD cells are stable versus non-identifiable. The product should
avoid visual prominence for a single OD matrix if many materially different
matrices fit the counters equally well.

## 5. Forecast and Direction-Split Methodology

### 5.1 Use rolling-origin evaluation for the forecast

Leave-weeks-out validation is useful but does not fully represent deployment:
training on past data and predicting later unseen periods. Add rolling-origin
folds across seasons, school breaks, and holidays. Report MAE, RMSE, MAPE/SMAPE
only where sensible, bias, peak error, and interval coverage per sensor.

Do not tune features or holiday handling using the final test period. Reserve a
locked holdout period or use nested cross-validation for model selection.

### 5.2 Treat holiday adjustments as uncertain transfer assumptions

Each 2025 holiday is seen once. Mapping its factor to 2027 is a reasonable
baseline, but it has no direct cross-year validation. Bound/shrink factors
toward one based on observation quality and cross-sensor agreement; report
them as assumptions. Compare against alternatives such as holiday-type groups,
calendar/weekend features, and a hierarchical model sharing strength across
sensors.

### 5.3 Export forecast uncertainty, not only rounded counts

The forecast output is point-valued and rounded early. Preserve floating-point
mean prediction and export calibrated prediction intervals or quantiles. For
the simulation pipeline, sample demand variants from those intervals with
correlation across sensors/time, rather than treating all forecast counts as
certain.

### 5.4 Keep directional uncertainty separate from traffic-volume uncertainty

The direction model's q10/q50/q90 variants are valuable, but using a few SUMO
seeds across variants combines model uncertainty with stochastic simulator
variation. Estimate and report them separately:

- outer samples: forecast/count and direction-split parameters;
- inner replications: SUMO random seed for a fixed demand/scenario; and
- summary: variance decomposition plus quantiles of edge flow and travel time.

Use a deterministic sampling design (e.g. Latin hypercube or stratified
quantile combinations) rather than cycling variants by seed index.

### 5.5 Strengthen direction-model governance

The Norwegian-data transfer is appropriately cautious. Improve it further by
versioning station selection, geocoding cache, source query windows, matching
decisions, feature statistics, model package, and coverage thresholds as one
reproducible training run. Add a local calibration override mechanism: a new
Gothenburg directional measurement should supersede a transferred estimate and
trigger a documented retraining/recalibration path.

### 5.6 Avoid model/package serialization risks

Pickle model loading is convenient but unsafe for untrusted files and brittle
across dependency versions. In a local trusted project this is not an urgent
security issue, but store model metadata and dependency versions; consider
LightGBM's native model format plus JSON metadata for portability.

## 6. SUMO Simulation and Incident Modelling

### 6.1 Validate SUMO network fidelity before calibrating demand

Net conversion guesses traffic lights and uses fallback speeds/lanes. Build a
network QA report comparing OSM graph edges to SUMO edges, lengths, speed,
lane count, connectivity, turn permissions, and removed self-loops. Highlight
sensor-adjacent discrepancies. Do not assume ID equality alone proves traffic
behavior equality.

### 6.2 Make closure semantics explicit

Whole-run and timed closures, local rerouter radius, truncation of impossible
trips, ignored route errors, and teleport behavior all materially change the
result. Scenario metadata should include:

- closure definition and effective SUMO interval (already present);
- rerouter coverage/radius;
- DONE (2026-07-10): count of truncated/dropped vehicles — `run_scenario.py`
  now writes `truncated_vehicles`/`dropped_vehicles` into the scenario JSON's
  `scenario` object (previously only printed to the build log and lost).
  Per-vehicle IDs and teleport counts are still not tracked;
- simulation mode and signal-control configuration; and
- whether a vehicle trajectory is from the same sample as aggregate flows.

The map should still expose a short disruption-quality status from these
counts, not just a closed-edge style — the backend now emits the numbers but
the frontend does not yet surface them. A closure with high teleport/
truncation rate is a lower-confidence scenario.

### 6.3 Use output metrics as production gates

`closure_metrics.py` is a good basis. Promote it into the run pipeline. Reject
or prominently flag a scenario when closed-edge throughput, teleports,
unfinished trips, invalid routes, or queue diagnostics exceed thresholds.
Persist baseline-versus-closure comparisons, not merely raw edge flows.

### 6.4 Improve simulator throughput from measurements, not guesses

Mesoscopic mode is the right default for 15-minute edge-flow output, provided
it remains validated. Benchmark a representative suite of normal and closure
cases against microscopic mode and against observed counts. Maintain a small
benchmark dataset and track runtime, delivery, diversion patterns, and queue
metrics across SUMO upgrades.

### 6.5 Avoid treating zero-entry edges as high-certainty by default

An edge with no simulated vehicles has a well-defined simulated value, but that
does not mean the *real-world* zero is credible. The confidence model should
distinguish data completeness (`zero was simulated`) from epistemic confidence
(`the demand/routing model supports that zero`). This matters especially far
from sensors.

## 7. Confidence, Validation, and Product Claims

### 7.1 Replace the single confidence scalar with a provenance vector

The current distance prior multiplied by an MC-CV factor is a useful temporary
display heuristic, but it is not a calibrated probability of accuracy. CV only
measures variation across the chosen samples; it cannot capture demand bias,
network error, or unidentifiability.

Export per edge/interval, or per edge with references to interval series:

- `source_class`: measured, mathematically derived, constrained, prior-led,
  simulation-only;
- `spatial_support`: empirical LOSO-derived distance/support score;
- `calibration_error`: expected error from held-out studies;
- `demand_uncertainty`, `direction_uncertainty`, `route_choice_uncertainty`,
  `simulation_variability`, and `network_model_risk`;
- `coverage`: whether every intended simulation sample produced a value; and
- `confidence_version` and calibration dataset fingerprint.

The UI can render a simple honest indicator, while APIs/reports retain the
components needed for scientific review.

### 7.2 Make validation a first-class stage, not an occasional script

LOSO should produce a versioned report for a concrete pipeline fingerprint.
It should be run automatically for every material change to data, candidate
generation, PFE, priors, SUMO configuration, or confidence calibration.
Separate validation tasks include:

- temporal holdout at measured sensors;
- leave-one-sensor-out spatial recovery;
- closure plausibility/consistency tests where no incident ground truth exists;
- sensitivity analysis for structural parameters; and
- regression comparison against a locked baseline.

Never compare values across changed network scope, candidates, target period,
or solver configuration without declaring the confound.

### 7.3 Calibrate confidence empirically

Fit a conservative error model using validation residuals, distance/network
distance, constraint provenance, and uncertainty features. With few sensors,
use bootstrapping/hierarchical shrinkage and publish wide intervals rather than
overfit a precise curve. Check calibration: among edges/conditions labelled 80%
reliable, how often is an explicitly defined error threshold met?

## 8. Server and API

### 8.1 Model jobs explicitly

The asynchronous recalibration improvement is correct. Generalize it to all
long-running operations, including closures and validation. Use `POST` to
create a job and return `202` plus `job_id`; `GET /api/jobs/<id>` returns
durable state; optional `POST /cancel` cooperatively cancels the subprocess
group; logs are available through a bounded endpoint/file.

Do not use mutating `GET` endpoints. It is unsafe with caches/prefetchers and
prevents conventional request semantics. Loopback binding limits exposure, but
the API shape should still be correct.

### 8.3 Validate all inputs and resource limits centrally

Validate closure count, unique IDs, scenario name format, date range,
trajectory request, seed count, and selected source against schemas. Set
explicit maximums for concurrent jobs, disk use, scenario retention, request
body/query length, and generated trajectory size. Return structured error
codes, not only localized error strings.

### 8.4 Improve logging and observability

Use structured logs with job ID, input fingerprints, command, elapsed time,
exit code, and artifact paths. Preserve full subprocess logs per job; show a
small sanitized tail in the UI/API. Add a health endpoint that verifies
required artifacts and executable availability, not just that HTTP responds.

## 9. Browser Application and Rendering

### 9.1 Measure and reduce per-frame work

`render.js` performs work for every edge on every animation frame, including
two provider lookups and style-key work, even when data slots have not changed
or an edge is off-screen. At city scale this becomes the main frontend risk.

Profile with Chrome Performance first. Likely improvements:

- update flow styles on slot transition or a throttled interpolation cadence,
  not necessarily at 60 FPS for all edges;
- only animate/compute visible edges using map bounds plus a margin;
- store provider arrays/indexed numeric data for direct lookup;
- keep a dirty-edge set on provider/mode/slot changes;
- use a canvas/WebGL line layer for thousands of independent line styles; and
- disable expensive dot work at low zoom or when simulation trajectories are
  active.

Do not optimize by recreating layers; retain the existing create-once rule.

### 9.2 Optimize vehicle overlay drawing

The vehicle canvas is resized every frame, which reallocates its backing store
and clears state. Resize only on map size/device-pixel-ratio change. `pointAlong`
linearly scans geometry segments per active vehicle per frame; precompute
segment lookup tables or use binary search for long polylines. Cap rendering
by visible vehicles and use device-pixel-ratio-aware drawing for crispness.

### 9.3 Move parsing and heavy preparation off the main thread

Scenario JSON can be large, particularly trajectories. Fetch with explicit
error handling and parse/decode in a Web Worker. Consider compact typed-array
binary payloads, gzip/brotli delivery, and a sparse/delta encoding for flows.
Keep the provider API unchanged so renderer code remains agnostic.

### 9.4 Harden frontend state transitions

DONE (2026-07-10): `web/index.html`'s `modeToken`/`scenarioToken` guards now
carry a real `AbortController` (`vehicleAbort`/`scenarioAbort`) that cancels
the in-flight trajectory/scenario `fetch` unconditionally on any newer mode
or scenario switch — including when the newer call itself resolves from
cache and would otherwise never touch the stale request. Verified via a
real headless-Chrome CDP run (throttled network, two genuinely uncached
scenario files switched rapidly): both superseded fetches were observed
aborted, only the final selection completed, and `AbortError` never
surfaced as an uncaught console exception.

Still open: representing mode, provider, selection, and job status as one
explicit state machine, so invalid combinations (e.g. a closure picker
running while a recalibration job swaps all scenario data) are structurally
impossible rather than guarded ad hoc.

### 9.5 Improve accessibility and interaction resilience

Use semantic labels/tooltips for icon-only controls, keyboard focus states,
ARIA live updates for simulation-job progress/errors, and non-modal error
surfaces rather than only `alert()`. Test narrow mobile layouts, long Swedish
labels, screen-reader navigation, slow network, tile failure, and missing
artifact behavior.

## 10. Tests, CI, and Quality Gates

### 10.1 Keep unit tests, add contract and integration tiers

The current unit suite is broad and useful, but skipped integration tests and
browser behavior leave real production paths under-tested. Define tiers:

- **fast unit**: pure functions, schemas, time/DST, candidate/PFE properties;
- **artifact contract**: committed/synthetic artifacts validated end to end;
- **SUMO integration**: small deterministic fixture network and routes;
- **browser integration**: Playwright/CDP tests against `serve.py`;
- **nightly scientific**: real-data rebuild/LOSO/benchmark suite.

Run fast and artifact tiers on every change; run SUMO/browser in CI where the
environment supports it; publish nightly reports as artifacts.

### 10.2 Add property and metamorphic tests

High-value properties include:

- no non-grid timestamp is silently reassigned to a quarter;
- every published flow edge is a network edge and every scenario mapped edge
  has the declared number of slots;
- `null` never becomes zero and zero never becomes `null` during a provider
  transition;
- direction-pair shares sum to one at every slot;
- changing input order does not change normalized intake output;
- changing the random seed changes only allowed stochastic outputs;
- a closed edge has no legitimate entered flow during its effective window;
- all post-rounding hard constraints are checked from emitted routes; and
- job publication is atomic from a reader's perspective.

### 10.3 Add regression fixtures and benchmark budgets

Create a tiny graph/CSV/SUMO fixture that covers a two-way sensor, a
single-direction sensor, a missing slot, a closure detour, no detour, and a
multi-day boundary. Store expected schemas and metric bands, not brittle full
JSON where random ordering is irrelevant.

Track runtime/memory budgets for data build, candidate generation, PFE,
baseline/closure SUMO, scenario export, and browser frame time. A speed
optimization is accepted only if it preserves declared calibration and contract
metrics.

### 10.4 Add static quality tooling

Adopt Ruff, Black, and a type checker such as pyright/mypy incrementally.
Enable strict types first in the new domain modules. Add ESLint/Prettier or an
equivalent small JavaScript setup. Enforce `git diff --check`, dependency audit,
and documentation link checks in CI.

## 11. Documentation and Operations

### 11.2 Add operator runbooks

Provide concise runbooks for:

- new sensor/data delivery;
- failed intake/snap;
- rebuild/rollback of a data release;
- recalibration and scenario job monitoring;
- validation interpretation;
- disk cleanup/retention; and
- upgrading SUMO/dependencies.

Each should state inputs, preflight checks, expected duration, output
fingerprints, success gates, and recovery without deleting working results.

### 11.3 Publish a limitations statement with every scenario

The project is strongest when it states what is measured, constrained,
prior-driven, and unknown. Generate a machine-readable and human-readable
limitations/provenance panel per scenario. It should mention no observed
incident data, uncertain OD/direction assumptions, signal timing limitations,
closure truncation/teleport diagnostics, and confidence calibration version.

## 12. Suggested Delivery Plan

### Milestone A: Make current behavior trustworthy

1. Add schemas, fingerprints, atomic artifact publishing, and job directories.
2. Repair/guard all single-day versus multi-day consumers.
3. Enforce intake validation and data-driven sensor metadata.
4. Validate emitted PFE routes and block production publication on violated
   hard constraints or unserviceable measured edges.
5. Reconcile README/runbooks and add artifact provenance to the UI/API.

Acceptance: a clean rebuild from a manifest produces a fully traceable,
atomically published scenario; validation and UI consume the same metadata.

### Milestone B: Improve speed without weakening guarantees

1. Benchmark baseline timings/memory/frame time.
2. Refactor PFE input/incidence to sparse arrays and batch worker tasks.
3. Cache content-addressed candidate and structural products.
4. Move frontend heavy parsing to a worker and make rendering viewport/dirty
   driven.
5. Record before/after calibration/constraint metrics with every optimization.

Acceptance: documented runtime/memory/frame-time reductions with no regression
in contract tests, post-rounding constraints, or validation report.

### Milestone C: Improve scientific confidence

1. Versioned temporal and LOSO validation suite.
2. Empirical, decomposed confidence model and UI provenance.
3. Uncertainty sampling design across demand, split, route choice, and SUMO.
4. Forecast intervals and direction-model governance.
5. Scenario quality gates from closure metrics.

Acceptance: every map claim has a provenance class, measured validation basis,
and uncertainty definition; results are comparable across revisions.

## What Not To Do

- Do not replace the `flowAt` provider seam while optimizing rendering.
- Do not hide unvalidated edges to make maps look cleaner; expose confidence
  and provenance instead.
- Do not call behavioral priors mathematical constraints in reports.
- Do not use a faster solver without validating emitted integer routes.
- Do not compare validation scores across changed scope/configuration without
  attaching the full fingerprint and declaring the confound.
- Do not add parallelism to all commands before isolating mutable run outputs.
- Do not introduce a GNN merely because feature artifacts exist; require a
  locked backtest showing value over the seasonal/LightGBM baseline.

## Review Evidence

The review examined the Python and browser application source, architecture,
Makefile, README, and tests. Existing automated verification at review start:
`454 passed, 21 skipped`. Syntax checks were also run for browser JavaScript
and Python modules. These checks establish a working baseline, not proof that
the cross-stage, scientific, or operational recommendations above are already
satisfied.

## 13. Second-Pass Additions

This section contains additional findings and recommendations from a further
source pass. They are intentionally non-duplicative of the main roadmap.

### 13.1 Preserve a valid zero confidence; do not use truthiness for numeric defaults

`run_scenario.py` loads static confidence using:

```python
prior[p["id"]] = p.get("confidence") or 0.5
```

`0` is a valid and common confidence value, not an absent value. In the current
network, 6,569 of 7,147 edges have stored confidence `0` after rounding. The
truthiness fallback turns each of those into `0.5` in scenario exports before
Monte Carlo variability is applied. A far-away edge can therefore be displayed
as roughly 50% confident when its static spatial prior deliberately says 0%.

Use an explicit missing check:

```python
value = p.get("confidence")
prior[p["id"]] = 0.5 if value is None else float(value)
```

More generally, audit all `x or default` uses when zero, empty string, `False`,
or an empty collection carry distinct domain meaning. Add a regression test
with `confidence=0`, `confidence=None`, and a positive confidence.

### 13.2 Do not round the static confidence field before using it for modelling

`build_data.py` rounds the Gaussian distance prior to three decimals. At
city-scale distances this creates a large hard plateau of exactly zero, losing
the ranking among far edges. Even once the truthiness defect is corrected, all
those edges become indistinguishable to downstream confidence logic.

Keep an unrounded numeric value in machine artifacts, or at least six decimal
places, and round only in the browser display. Better still, store distance and
let the confidence-calibration stage derive display confidence from its own
versioned model. A static distance heuristic should not permanently quantize
the future confidence model's input.

### 13.3 Separate simulation completeness from epistemic confidence in the UI

After the zero-flow export fix, all map edges can correctly carry an explicit
zero simulated count. That is a *coverage/completeness* fact. It should not be
merged into confidence or used to infer that a distant no-flow result is
credible. Export and render two separate signals:

- `simulation_complete`: all required runs produced a value for this edge/time;
- `epistemic_confidence`: confidence in correspondence to real traffic.

This avoids the common but damaging interpretation that a calculated zero is a
validated real-world zero.

### 13.4 Replace POI edge assignment's quadratic scan with a spatial index

`build_candidates.activity_mass()` loops over every road edge and computes a
vector distance to every POI in each purpose category. Its complexity is
approximately `O(number_of_edges × number_of_POIs × categories)`. It allocates
large temporary distance arrays repeatedly and becomes a major startup cost as
the inner-city graph or POI set grows.

Project edge midpoints and POIs to a metric CRS once, build a `scipy.spatial`
`cKDTree`/Shapely `STRtree` per category, and query each edge's radius count or
weighted neighbors. If activity mass needs a kernel rather than a hard radius,
use a sparse radius-neighbor matrix. Cache the derived per-edge activity field
by OSM graph and POI-source fingerprint, not only the raw GeoJSON response.

Benchmark this separately because it will improve both candidate generation and
assignment-prior computation, which call the same conceptual mass model.

### 13.5 Make external-source caches refreshable and auditable

The POI cache, DeSO data, Nominatim geocode cache, Norwegian station metadata,
and volume files are effectively indefinite once present. A successful but
partial/stale response can persist without a source timestamp, query bounds,
response status, schema version, or expiry policy.

Wrap each cache in a metadata envelope containing source URL/query, retrieval
time, input graph/bbox fingerprint, HTTP status/ETag where available, parser
version, and a refresh policy. Support `--refresh`, `--offline`, and
`--validate-cache`. For failed geocodes, use a shorter TTL than successful
results; a transient Nominatim failure should not permanently exclude a
training station.

### 13.6 Make candidate-generation randomness traceable at substream level

The code correctly seeds major random generators, but a single shared RNG
couples unrelated choices: changing one generation branch can alter all later
draws. That makes before/after comparisons noisy and hard to attribute.

Use named deterministic substreams derived from a root seed, for example
`SeedSequence([root_seed, stage_id, day_index, purpose_id])`. Persist these
subseeds in the artifact. Then changing POI sampling does not silently change
through-trip or direction-variant draws. This is particularly valuable for
controlled LOSO and parameter ablations.

### 13.7 Validate scenario names and keep filesystem paths data-independent

`run_scenario.py --name` is interpolated into route, additional, JSON,
trajectory, and manifest filenames. CLI usage is currently local, but names
such as path separators, control characters, very long strings, or `..` can
write outside the scenario directory or create inconsistent manifest entries.

Accept either a generated opaque ID or a strict slug (`[a-z0-9_-]{1,64}`), and
keep the human-readable label as data in the manifest. Always resolve and check
that generated paths remain under the intended run/staging directory. This also
improves cross-platform behavior.

### 13.8 Kill subprocess groups, not only direct children

The scenario runner invokes SUMO, while the server invokes the scenario runner.
A timeout in the outer process can leave a grandchild simulator running. This
causes CPU leaks, output-file races, and false "busy" behavior after a failed
request.

Start each long-running command in a new process session/group. On timeout or
cancellation, terminate the entire group, wait briefly, then force-kill if
needed. Record the PIDs/job ID in the durable job record. Add an integration
test that launches a child process which itself launches a sleep process and
asserts both exit after cancellation.

### 13.9 Make closure-quality comparisons cohort-consistent

The no-detour handling deliberately truncates some closure vehicles before the
closed edge. That is a reasonable behavioral approximation for flows, but it
changes the closure run's completed trip length and destination set. Comparing
raw total `timeLoss` against the untruncated baseline can make a closure appear
better merely because some journeys end early.

For disruption ranking, either:

- run the baseline on the identical truncated trip cohort;
- normalize by a clearly defined common cohort and report excluded/truncated
  demand separately; or
- treat accessibility loss as a separate disqualifying/primary impact metric.

At minimum, `truncated_unreachable` must be a prominent comparison guard, like
teleports and dropped vehicles, rather than a silent auxiliary count. Report
lost destination access and person/trip exposure separately from delay.

### 13.10 Treat closed-edge throughput as a scenario-integrity gate

`closure_metrics.py` can calculate closed-edge throughput, but its current
disqualification logic only checks teleports and dropped vehicles. A closure
with nonzero flow on a closed edge during an active window is an integrity
failure unless explicitly explained by entry before the window or interval
aggregation. The metric needs time-window-aware evaluation, not a whole-run
sum alone.

Export per-interval closed-edge entries and classify each entry against the
closure interval. Make unexplained active-window throughput fail publication or
at least visibly mark the scenario invalid. Test a vehicle that enters just
before closure start separately from one that enters during closure.

### 13.11 Avoid full-network `fitBounds` and unbounded hit testing at startup

The renderer constructs all 7,147 Leaflet polylines and then flattens every
polyline point to compute `fitBounds`. This is a one-time cost but can delay
first usable render and allocate a large intermediate array. The map's extent
can be exported offline as a bounding box with `network.geojson`, or accumulated
incrementally while features load. More importantly, thousands of interactive
polylines make click hit testing expensive even outside closure-picking mode.

Store a geographic bounds property offline; make non-picking background edges
non-interactive; enable edge interaction only while selecting a closure; and
consider a spatial index/custom canvas hit test when all-edge closure selection
must remain available.

### 13.12 Use `AbortController` and response checks for every browser fetch

Some fetch paths check `res.ok`; others immediately parse JSON. A failed
trajectory/index/status response can produce a JSON parsing error with little
context, and stale requests still download after mode changes. Centralize fetch
through a helper that checks status/content type, attaches request context,
uses an `AbortController` per mode/job, and reports structured errors.

This is a smaller change with high UX value on a local server that can rebuild
or replace files while the browser remains open.

### 13.13 Make platform support intentional around multiprocessing

PFE parallelism explicitly requests the `fork` multiprocessing context. This
is efficient on the present Unix-like development system, but unavailable on
Windows and increasingly constrained in some managed macOS/Python contexts.
It also relies on inherited module globals for the large shape pool.

Declare Unix/fork as a supported requirement, or provide a portable worker
initialization path. A robust design serializes compact immutable inputs once
per worker initializer, supports `spawn`, and chooses worker count from both
CPU and available memory. Include a sequential fallback for debugging and CI.

### 13.14 Stream large XML and route files where possible

Several routines parse whole SUMO XML files into memory (`ET.parse`) or count
vehicles by scanning an entire output after writing it. This is acceptable for
current day scale but becomes a limit for weeks, trajectories, and richer SUMO
outputs.

Use `xml.etree.ElementTree.iterparse` for edge data, tripinfo, vehicle routes,
and large route transformations. Aggregate needed fields then clear elements.
This lowers peak memory and supports larger scenario horizons without changing
the public artifacts.
