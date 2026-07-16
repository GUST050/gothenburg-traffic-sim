# Gothenburg Traffic Simulation Improvement Plan

**Date:** 2026-07-16 (consolidated 2026-07-15; status re-verified against the
working tree 2026-07-16)
**Status:** Canonical improvement plan — active implementation is in progress.
**Structural authority:** `ARCHITECTURE.md` remains the source of truth for
the six-stage pipeline and fixed contracts. This is the only improvement,
review, performance, simulation, closure, signal and sensor-growth plan.

## How To Use This Document

This document consolidates the former forward plan, program-improvement plan,
simulation accuracy/speed plans, code audit, destination-bias research, and
dated execution log. It retains the current conclusions and active work; it
does not preserve obsolete intermediate proposals as separate instructions.

1. Read `ARCHITECTURE.md` first for system structure and contracts.
2. Use this document for status, priorities, evidence gates and implementation
   order.
3. Treat an item as complete only when its acceptance gate and recorded
   measurement pass. A code change or green sensor GEH by itself is not enough.
4. Keep historical evidence in immutable run artifacts, tests, validation
   reports and Git history rather than duplicating it across planning files.
5. When resuming work, start from "Current verified status and concrete
   entry points" at the end of "Recommended Implementation Order" — it
   records what was last verified as done and the concrete entry point for
   each next step. The rest of this document explains why that order holds.

## Consolidated Status

### Foundations completed and retained

- Immutable run manifests, staged publication, per-seed health gates and
  durable job records prevent incomplete work from replacing active results.
- Run archiving is now scoped to the producing process: demand runs no longer
  glob stale closure routes from the shared `sumo/` directory, and scenario
  runs archive the exact scenario/trajectory they wrote instead of guessing
  from filesystem modification time. Run output records now include SHA-256
  content hashes. The golden-release mechanism extends the existing run
  registry under `runs/releases/`; its first frozen release remains a Phase 0
  deliverable, not a completed fact.
- Trajectory parsing includes SUMO reroutes in `routeDistribution`; unfinished
  vehicles and displayed-share integrity are explicit rather than silently
  omitted.
- The demand pipeline is split into modules, has a deterministic PFE benchmark
  fixture, per-variant fit gates, validation reporting and semantic speed
  benchmarks.
- Security hardening is in place for the local API: POST-only mutations,
  origin guard, CSP and safe dynamic result rendering.
- Sensor direction semantics now live in `data_in/sensors.json`, are validated
  before calibration, and participate in fingerprints.
- ScenarioSpec/ClosureSpec validation, time-windowed closures, paired closure
  comparisons, exhaustive candidate evaluation, network metadata and source-
  to-SUMO network auditing are implemented.
- New web closure and closure-time requests now carry the complete validated
  base/closure ScenarioSpec; `serve.py` archives it and invokes the runner
  with `--scenario-spec`. Legacy query requests remain only as a compatibility
  path during migration.
- Signal optimization now uses the same contract end to end: the browser
  sends the active scenario's full `ScenarioSpec`, the API archives and
  forwards it, and both plain and closure signal runners honor its exact seed
  set and demand-variant mapping. Legacy query requests remain supported for
  CLI/backward compatibility, but new UI jobs no longer reconstruct identity
  from loose edge/window parameters.
- Demand recalibration now uses a validated `DemandBuildSpec`: the browser/API
  archives the exact date, source, day range, effective window and structural
  reference; `build_sumo_demand.py` validates repeated legacy flags against it,
  writes `sumo/demand_build_spec.json`, and includes the contract plus all
  demand-affecting options in the content fingerprint. A matching
  `demand_build_key` is returned in job status and carried into `demand_meta`,
  so stale scenario sets cannot be mistaken for the requested calibration.
- Shared runtime code has been reorganized under `traffic_sim/` without
  breaking stable root CLI paths. Contracts, fingerprints, sensor intake,
  demand caching, SUMO metadata/runtime, disruption metrics, and run/release
  registries now each have one canonical implementation. Root compatibility
  imports contain no duplicate logic, and demand cache/build fingerprints hash
  the canonical package files.
- The browser supports focused normal, closure, closure-timing and synthetic
  signal-study workflows. Signal output already shows numeric timing changes
  with provenance.
- Scenario playback publishes a per-direction sensor audit (2026-07-16):
  every scenario JSON carries `sensor_audit` with the frozen source
  observation, the directional calibration target, the Monte Carlo ensemble
  mean and the displayed representative seed, plus GEH summaries — count
  delivery is read from a table, never inferred from dot density. Demand
  builds persist the exact `sensor_targets`/`sensor_observations` they
  calibrated against in `demand_meta`; scenarios built from older demand
  builds reconstruct them from current inputs and are labelled
  `reconstructed_current_inputs` until the next recalibration freezes them.
  Two-way Total stations stay labelled as physical-station totals, never as
  directional measurements, and missing values remain `null`, never zero.

### Active improvements, ordered by value and dependency

This list ranks the remaining work by value; the execution queue — which
interleaves these items with their gate dependencies — is "Recommended
Implementation Order" at the end of this document.

1. **Implemented:** enforce SensorRegistry validity, approved snaps and active
   dates before a sensor can constrain a build.
2. **Implemented:** make final SUMO sensor output auditable and calibrate
   normal runs against the frozen target at the correct physical-station
   aggregation. A fresh staged set fails closed when this evidence is absent.
3. Finish purpose-compatible route allocation and revalidate normal demand.
   Same-signature replacement is implemented; uncovered through/service
   signatures still require a larger compatible candidate pool.
4. Freeze and exercise a golden normal, closure and bounded micro-signal
   release, including rollback.
5. **Contribution slice implemented:** `sensor_contribution.py` now emits
   evidence-bound before/after holdout, confidence, coverage and isolation
   reports plus a placement screen. A new station still needs a real before/
   after artifact before it can be called an improvement.
6. **Certificate implemented:** every signal-optimization condition now gets
   a machine-readable TSFS-informed phase/link timing certificate. Remaining:
   optimize closure-driven arrivals under that certificate; this stays
   synthetic until a city controller plan is imported.
7. **Feasibility slice implemented:** closure-time ranking now rejects missing
   queue evidence, partial/no detours, truncation and unhealthy candidates;
   paired uncertainty and queue deltas are published with every candidate.
8. **Implementation slice complete:** continuous multi-day runs now emit cheap
   periodic SUMO summary evidence, per-day boundary accounting and a
   fail-closed staging gate. Remaining: run and freeze the two-day golden
   acceptance artifact before calling multi-day output trusted.
9. **Network provenance implemented:** the existing network audit records
   OSM/defaulted values and SUMO TLS membership. Actual NVDB import remains
   evidence-bound until a reviewed download is supplied.
10. **Study/job history implemented:** the start workspace now exposes the
   durable `/api/jobs` records; importing a city signal corridor remains
   evidence-bound and is intentionally not fabricated.

### Evidence-bound work that must not be faked

- Actual city controller plans, detector logic, movement conflicts, offsets,
  pedestrian/cycle timing and temporary work plans.
- Local link/path travel times, speed distributions, queue discharge and
  detailed lane-changing behaviour.
- Local OD, trip-purpose and through-traffic truth beyond what the six counts
  identify.

The absence of these data does not stop the current product. It limits claims:
citywide mesoscopic normal/closure results remain calibrated near sensors and
prior-driven elsewhere; signal results remain synthetic experiments until a
verified SignalPlan is imported.

## Consolidated Engineering Findings

### Demand, destinations and purposes

Six count stations constrain sensor crossings, not each vehicle's real origin,
destination or purpose. The previous candidate/PFE design could exploit that
underdetermination by selecting routes that ended immediately after a sensor.
The implemented response is retained as a permanent guard: joint natural
sensor-route sampling, destination naturalness masks, downstream-distance and
trip-length structure checks, near-sensor destination diagnostics, and
calibrated-output checks in addition to generated-pool checks.

The remaining improvement is not to relabel trips after calibration. It is a
purpose-compatible allocation or purpose-stratified calibration formulation
that makes the selected route instance compatible with purpose, departure time
and length before it is published. Until its incompatibility diagnostic is
zero, purpose labels are diagnostics rather than evidence of individual trip
intent. Through-traffic share remains a sensitivity-tested prior until a
cordon count or local OD source identifies it.

### Simulation integrity and closure realism

The former critical defects in rerouted-vehicle animation, missing unfinished
vehicles, fail-open health telemetry, stale route artifacts, unpaired closure
comparisons and closure window mismatch are resolved. Their protections stay
mandatory: final-route parsing, source-file reconciliation, per-seed health,
full input fingerprints, paired seed/variant comparisons and ScenarioSpec
window identity.

The remaining closure work is decision quality: apply access and detour gates
before ranking, evaluate all feasible windows when the candidate set is
bounded, reject low-delay results caused by lost traffic, and expose the
uncertainty/gate state rather than a single score.

### Sensor-value contract and final output calibration (P0)

The program has four different numbers at a sensor.  They must never be
silently substituted for one another:

1. **Source observation** is the delivered historical count, or the forecast
   value when a forecast day is selected.  A forecast value is an input
   estimate, not an observation.
2. **Frozen calibration target** is the exact target given to the demand
   build.  A directed station has one target on its measured directed edge.
   Station 107 is different: the delivery is one physical two-way Total; its
   directional q10/q50/q90 split is a model assumption, not two measured
   values.
3. **Final SUMO output** is the count from the final `edgeData` result after
   vehicles have actually entered the edge during that 15-minute interval.
   This is the number the road colour represents.
4. **Animated representative** is one seed/variant's vehicles.  It is useful
   for visual inspection, but it is not the ensemble mean and cannot be used
   to count all simulated traffic by eye.

The new `sensor_audit` payload correctly exposes all four concepts.  It is
not yet a proof that the final simulation delivers the sensor target.  On the
currently published 2027-10-20 forecast baseline, its PFE fit is 100% GEH<5
and all 53,311 variant vehicles were inserted without teleports, but the
displayed final SUMO values can still differ from their q50 targets.  This is
expected evidence of a missing final-output gate, not a reason to overwrite
the map with input values.  The current audit is additionally labelled
`reconstructed_current_inputs`; it must be rebuilt from a demand build that
persists its frozen inputs before it can be frozen as release evidence.

#### Confirmed defects and semantic risks

1. **P0 — publication validates the wrong stage.** `serve.py`'s
   `validate_staged_scenarios` validates demand/PFE fit and seed health, but
   does not validate `sensor_audit` or compare final SUMO `edgeData` output
   with a frozen target.  `validation_report.py` similarly reports
   `demand_meta.pfe_fit`, not final sensor output.  A normal baseline can
   therefore publish with an excellent PFE GEH result while its final SUMO
   entries at sensors are different.  No normal scenario may be described as
   having *exact sensor delivery* until this is fixed.
2. **P1 — closures lose audit variant identity.** The audit infers q50/q10/q90
   from the literal route filename.  `run_scenario.py` renames closure route
   files to forms such as `calibrated_v1_close_*.rou.xml`, while
   `target_key_for_route_path` recognises only the three original filenames.
   The resulting closure audit cannot recover a target for any seed, so its
   target and fit fields become empty even though the demand variant is known
   earlier in the job.  This is a confirmed code defect, not a modelling
   limitation.
3. **P1 — the two-way station can look double-counted in the table.** The raw
   Total for station 107 is intentionally repeated beside both directed rows
   and labelled `tvåvägs-total`.  A user can nevertheless read or sum it as
   two independent observations.  The calculation is not double-counting it,
   but the presentation is too easy to misread and does not meet the required
   physical-station contract.
4. **P2 — provenance is recoverable but not frozen for legacy runs.** The
   fallback reconstruction keeps old scenarios inspectable, but it reads
   current input files.  It is unsuitable for a historical release claim if
   those inputs later change.  New demand builds persist the data correctly;
   an end-to-end test and the next fresh baseline must prove that the exact
   stored arrays survive into the published audit unchanged.

#### Required implementation sequence

1. **Carry semantic variant identity, never infer it from a filename.** Put
   `demand_variant: q50|q10|q90` and the corresponding frozen target key in
   each seed job.  Return them from `run_seed_job` and use them in the audit,
   trajectory provenance and any paired comparison.  Filename parsing may
   remain only as a compatibility fallback.  Add unit coverage for each
   closure-renamed route and an end-to-end closure-audit test.
2. **Publish a versioned final-output fit artifact.** For every normal seed
   and every 15-minute interval, compare its final SUMO `edgeData` entry with
   that seed's frozen target.  Publish per-direction residuals, absolute
   error, GEH and missing-data state, plus an ensemble section.  Aggregate by
   physical station before declaring a measured fit:
   - sensors 133, 134, 2276, 1074 and 1076: compare their one measured
     directed edge directly;
   - sensor 107: compare `north + south` against the one delivered Total;
     show the q10/q50/q90 directional split separately as an assumption, not
     as two observations.
   The artifact must contain the frozen demand build ID, source fingerprint,
   target provenance and target arrays/hash.  It belongs in the scenario
   payload and in `validation.json`, with a schema version that makes absent
   output-fit data fail visibly rather than look healthy.
3. **Measure the residual mechanism before changing the solver.** PFE selects
   route/departure instances while SUMO counts actual edge entries.  Travel
   time from departure to a sensor, congestion and rerouting can move a
   vehicle into a neighbouring 15-minute `edgeData` bucket.  This is the
   leading hypothesis for part of the current mismatch, not an established
   root cause.  Instrument one frozen normal day to retain, per constrained
   edge and quarter: selected PFE route count, planned departure quarter,
   actual SUMO entry quarter and any reroute/truncation outcome.  Classify
   residuals into timing shift, missing route coverage, integer/bound effect,
   or unexplained.  Do not loosen an error threshold or change raw sensor
   values to make this diagnostic green.
4. **Correct output calibration with bounded feedback only after the
   diagnosis.** If timing is the cause, build the PFE incidence against the
   expected sensor-entry quarter (initially from recorded lag), then make at
   most one or two output-feedback corrections from actual `edgeData`
   residuals.  Reuse the same immutable candidate pool, route alternatives,
   seed/variant plan and hard feasibility bounds.  If a residual cannot be
   corrected within those constraints, report it as infeasible rather than
   fabricating a match.  Benchmark the correction against the golden normal
   day; it may add a bounded meso pass but must not increase PFE work or lower
   seed/variant fidelity.
5. **Make the gate stage-aware.** A normal baseline must require a present,
   frozen final-output-fit artifact and fail publication when a measured
   station's agreed output contract is not met.  The exact contract is zero
   residual after integer aggregation for a historical directed count, and
   zero residual for the station-total at 107; forecast runs apply the same
   check to their frozen forecast target, while remaining labelled forecast.
   Do not adopt a convenient GEH tolerance as a substitute for this exact
   count-delivery check.  A closure must not be forced to retain normal
   sensor counts: it must instead display the matched normal target/output,
   closure output and scenario effect, while retaining its health, access and
   closure-integrity gates.
6. **Make the UI physically unambiguous.** Keep the map value as final SUMO
   output.  On a sensor edge, show `source | target | SUMO output` with the
   active interval and seed/ensemble label.  Render station 107 as one
   physical Total row with two indented modelled-direction rows; never repeat
   the raw Total as though it were two directional observations.  The sensor
   audit table remains the full numerical source of truth; animation stays a
   representative visualisation.

#### Acceptance gate

- A newly recalibrated historical normal baseline stores (not reconstructs)
  all audit inputs and reproduces them byte-for-byte or by recorded content
  hash in the published scenario.
- Every directed measured station has an explicit final SUMO residual for
  every available quarter; sensor 107 additionally has one station-total
  residual.  `null` remains missing, never zero.
- The normal release cannot publish with a missing, stale or failed
  final-output fit.  A closure is correctly labelled as a changed scenario,
  not as a failed normal calibration.
- The output correction has a recorded before/after result on the frozen
  normal day, no new health/structure regression and no reduction in seeds,
  variants or simulation fidelity.

### Whole-program audit: confirmed gaps (2026-07-16)

#### Implementation status (2026-07-16)

The first P0/P1 implementation slice is now in the codebase:

- `SensorRegistry` is fail-closed for accepted quality, approved snaps,
  reviewed edge IDs, snap distance and active study dates. `build_data.py`
  validates the current graph resolution against the registry before writing
  flows. The six existing stations were migrated to their reviewed directed
  edges using true point-to-polyline distances.
- Demand metadata now records registry/network hashes and the exact sensor
  edge contract. Staged publication rejects a new build whose contract is
  missing or stale.
- SUMO scenario audits carry explicit q50/q10/q90 provenance, preserve the
  unrounded ensemble mean for fit calculations, and add a physical-station
  aggregation for two-way totals. The map still receives rounded integer
  flows only for display.
- The staged publication gate requires the new raw final-output-fit artifact,
  frozen demand provenance, complete sensor series and all three declared
  variants for a normal three-variant release. Legacy artifacts remain
  readable but are not treated as proof of the new contract.
- Verification on 2026-07-16: `python3 -m pytest -q tests` reports
  **942 passed, 21 skipped, 2 warnings** (loopback-enabled run; 963 tests
  collected). The warnings are the existing LibreSSL and pandas date-parser
  warnings, not failures.
- The same verification includes the multi-day summary parser/publication
  gate and the synthetic signal-plan timing certificate. Ordinary one-day
  scenario runs do not request summary output, so this evidence path does not
  slow the normal simulation.
- Purpose allocation now has a conservative replacement path: if a selected
  shape lacks the requested purpose but an already generated route has the
  identical measured-edge signature, its unconstrained leg is replaced and
  the count-preserving replacement is recorded. This reduces avoidable
  incompatibility without fabricating routes; remaining categories without a
  same-signature candidate stay explicitly flagged for candidate-generation
  work.

Still deliberately open: completing the purpose-compatible route allocation
(candidate coverage is still insufficient for every through signature),
the two-day golden release, closure feasibility and closure-driven signal
optimization. The signal certificate and multi-day publication gate are now
implemented, but their final golden artifacts still require an actual run.

This review covers the active intake, demand, normal simulation, closure,
signal, multi-day, publication, UI and release paths against the current code,
tests and generated artifacts.  The items below are **confirmed** by a code
path or an active artifact.  They are not speculative modelling ideas.  They
are deliberately separated from evidence limits such as unavailable real
signal plans or travel-time data, which remain limitations rather than bugs.

1. **P0 — SensorRegistry enforcement (resolved in the current slice).**
   This was a confirmed intake gap: `validate_data_sensors` used to check only
   unknown IDs, catalogue verification and coordinates, while automatic snaps
   could enter calibration.  It now enforces active dates, accepted quality,
   approved snaps, reviewed edge IDs and a true snap-distance limit; `build_data.py`
   also validates the current graph resolution before writing flows.

   **Implemented fix:** bootstrap-review and record the current six resolved directed
   edges, then require an active, accepted record and a resolved snap that
   matches the approved directed edges, bearing and distance threshold for the
   build's network fingerprint.  A changed OSM snap must stop the build and
   produce a review artifact; it must never silently move a measurement to a
   new road.  Date-range validation must exclude a sensor outside the study
   interval.  Store the resolved-snap artifact and registry hash in the
   demand build and add fail-closed tests for every field.

2. **P0 — Normal PFE fit is not final SUMO output fit (artifact/gate now
   implemented; correction threshold remains open).** This was the
   publication and audit issue described in the preceding sensor-value
   section.  The scenario now records raw edgeData fit, station aggregation,
   provenance and registry/network identity, and new staged releases fail
   closed when that artifact is absent or stale.  The remaining work is to
   calibrate the SUMO output itself until the frozen golden case meets the
   chosen residual threshold; GEH is not being used as a substitute for that
   decision.

3. **P0 — Current normal demand still has known realism gates in warning
   state.** The active `validation.json` records
   `purpose_incompatible_quarters_by_variant = 96` for q50/q10/q90, so
   purpose labels are correctly blocked from being evidence.  The new
   count-preserving same-signature replacement reduces avoidable provenance
   mismatches, but it cannot create a missing through-route signature.  It also reports
   two calibrated-versus-pool structure drifts above the 2.5x limit:
   `onward_under_200m_pct` 3.6 vs 1.4 and `trips_under_1km_pct` 1.43 vs 0.5.
   The simulation can still run, but it must not be presented as a validated
   purpose/route-distribution result until the allocation is repaired and
   temporal/LOSO checks are rerun.

4. **P1 — Uncertainty coverage can be silently collapsed by a valid
   ScenarioSpec.** `ScenarioSpec` requires every seed to have a q10/q50/q90
   label, and `run_scenario.py` resolves that mapping, but neither contract
   validation nor the publication gate requires a three-variant normal build
   to actually run all three variants.  A syntactically valid spec can map all
   seeds to q50 and still publish.  This violates the stated Monte Carlo
   uncertainty contract without changing a build ID.

   **Implemented fix:** derive the required variant set from `demand_meta.n_variants`.
   For a normal three-variant release, require q50, q10 and q90 at least once
   and require the published seed mapping to match the declared plan.  Permit
   a deliberately reduced mapping only for an explicitly labelled diagnostic
   study that cannot replace the normal baseline.  Test both rejection and
   the standard 1000/q50, 1001/q10, 1002/q90 path.

5. **P1 — The sensor audit calculates fit from display-rounded ensemble
   flows.** `aggregate_flows` rounds the three-seed mean to an integer for the
   map, then `build_sensor_audit` reuses that rounded value for
   `simulated_mean` and its reported ensemble GEH.  Rounding is appropriate
   for a road-colour label, but not for an accuracy calculation: it can change
   an ensemble residual by up to half a vehicle per directed edge/quarter.

   **Implemented fix:** retain per-seed integer output and an unrounded numerical mean in
   the audit/output-fit artifact.  Use the unrounded value only for ensemble
   statistics; retain the rounded value exclusively as `map_display_flow`.
   The q50 representative remains an exact integer count.  This change is
   computationally negligible and must be covered by a fractional-mean test.

6. **P1 — The UI offers 1–7 day normal studies before their release gate is
   complete.** The date panel exposes `days=1..7`, and the builder can create
   a continuous route list, but the plan still requires a frozen two-day
   golden run with per-day output fit, health, midnight carry-over accounting,
   cancellation/disk-budget tests and range-level publication validation.
   The current publish gate checks the build as one aggregate; it does not
   prove every day independently.  This is a product-claim gap, not evidence
   that every multi-day run is wrong.

   **Fix:** either temporarily mark the control `experimental` and prevent it
   from replacing the normal release, or complete the stated two-day gate
   before presenting multi-day output as a trusted study.  The better long-
   term option is the latter: produce per-day target/output/health sections
   plus one range manifest, while keeping one monotonically running SUMO
   process and explicit vehicles crossing midnight.

7. **P1 — Closure sensor audits lose target data after route filtering
   (resolved).** The runner now carries the semantic variant in each seed
   result and retains a filename fallback for legacy callers, so closure route
   copies cannot erase q10/q50/q90 audit identity.

8. **P2 — Documentation and release status contain stale assertions.**
   `IMPROVEMENT_PLAN.md` previously claimed a completed golden release even
   though `runs/releases/` is absent, and stated a full-suite result without
   naming its runner or environment (a 2026-07-16 dev-machine run records
   the actual result: 942 passed, 21 skipped, 963 collected — the count was
   real, but a claim without provenance cannot be told apart from a stale
   one, which is the defect).  `ARCHITECTURE.md` also contains
   dated PFE/LOSO figures that it explicitly marks pending revalidation, while
   its product wording says every added sensor *must* improve all outputs.
   The latter is stronger than the statistically honest `improved | neutral |
   insufficient evidence` rule already adopted here.

   **Fix:** record executable results only in immutable run/release artifacts,
   update static documentation with dates and provenance, and change the
   sensor promise to: every validated added station is incorporated without
   code changes and its contribution is measured, not guaranteed positive.
   Do not declare a full-suite count unless the exact runner and result are
   retained; localhost API tests require a runner permitted to bind loopback.

9. **P2 — Browser regression coverage is weaker than the backend contract
   coverage.** The Python suite contains 963 collected tests and strong
   contract tests, but there is no repeatable browser-level regression suite
   for the critical UI states: recovered background job, cancel transition,
   sensor-table station aggregation, stale scenario selection and 1/7-day
   control state.  Manual CDP testing has caught real defects in these paths
   before.

   **Fix:** add a small headless-browser smoke suite against `serve.py` with
   fixture scenario data and deterministic job status stubs.  It should check
   DOM state and console errors, not run a full SUMO demand build.  Keep the
   existing API/unit tests; this is the missing end-to-end seam.

### Performance and architecture

Measured performance work found the PFE's deliberately sequential
Gauss-Seidel update to be the dominant demand cost. Do not rewrite that solver
or reduce accuracy controls without a separate result-equivalence experiment.
The safe levers are complete input fingerprints, immutable candidate/network
caches, one process budget, isolated workspaces, reduced repeated I/O and
capturing required trajectory output during an existing seed run. Every
accepted speed-up requires semantic result comparison and a golden-case
before/after measurement.

### Active quality register

| Priority | Improvement | Completion evidence |
| --- | --- | --- |
| P0 | Enforce SensorRegistry validity and approved snap identity | Active/accepted station, reviewed directed snap, bearing/distance check and registry/network hashes in every demand build |
| P0 | Verify final SUMO sensor output, not only PFE input fit | Frozen output-fit artifact with correct station aggregation; normal publication fails closed on missing/stale/failed fit |
| P0 | Eliminate purpose-route incompatibility | Zero diagnostic across q10/q50/q90 plus no worse held-out/temporal result |
| P0 | Freeze a normal, closure and micro-signal golden release | Complete health/provenance records, semantic hashes, final-output fit and tested rollback |
| P0 | Make synthetic signal phases explicit and mechanically safe | Versioned `SignalPlan` JSON plus TSFS-informed phase/clearance certificate is emitted per signal condition; city configuration remains evidence-bound |
| P1 | Preserve semantic q10/q50/q90 identity in every scenario | No filename-derived provenance; normal releases cover every declared variant and closure audits retain targets |
| P1 | Separate numerical audit values from map-display rounding | Per-seed integers and raw ensemble mean used for fit; rounded flow only for rendering |
| P1 | Prove the value of every new sensor | `sensor_contribution.py` emits coverage/confidence/LOSO/placement evidence; real before/after artifacts remain required |
| P1 | Make closure advice robust | Access/detour, integrity, paired uncertainty, queue-proxy and no-viable-closure gates are evaluated before ranking |
| P1 | Make multi-day studies continuous and calendar-correct | Periodic summary/per-day boundary artifact and staging gate implemented; two-day golden study still required before trusted UI release |
| P1 | Complete study identity across API and UI | Active study exposes exact build, ScenarioSpec, job and validation artifact |
| P1 | Import reviewed road structure | NVDB/OSM mapping audit with provenance and no stable-ID drift |
| P2 | City-configure one signal corridor | Imported plan plus independent turn/travel-time validation |
| P2 | Add browser regression coverage for job and audit states | Deterministic headless smoke suite covers start/recover/cancel, scenario switch and sensor presentation |
| P2 | Keep documentation and release claims executable | Dated artifacts back every stated metric; no stale release/test-status assertion |
| P2 | Improve speed only with proof | Repeated benchmark improvement with unchanged semantic digest |

## Consolidation Coverage and Legacy Labels

The deleted review files are not lost work. Their actionable content is kept
in this plan at the following canonical locations:

| Former material | Canonical location here | Current handling |
| --- | --- | --- |
| Full code and simulation audit | Foundations, quality register and Phases 0-7 | Resolved defects remain permanent gates; unresolved risks have a named owner and acceptance gate |
| Destination, purpose and sensor-endpoint research | Consolidated Engineering Findings and Phase 3 | Prevent endpoint-biased trips; complete purpose-compatible allocation before making purpose claims |
| Speed and robustness implementation plan | Phase 0 and Phase 7 | Accept only result-preserving speed work measured against golden cases |
| Multi-day, closure-timing and signal execution plan | Phase 3, Phase 4 and Phase 5 | Treat time ranges, closures and signals as one versioned study, not unrelated commands |
| Product and sensor-growth roadmap | Practical Development Roadmap and Phases 1-2/6 | Add evidence, provenance and user workflow before expanding scope |

Some source comments, tests, manifests and old run artifacts still carry
historic labels such as `A-D`, `E-K`, `P0`, or `SIM-P1`. They remain useful for
Git-history traceability only; they are not current task identifiers. For
future work, use the phases in this document: historical hygiene/performance
labels map to Phase 7, multi-day labels to Phase 3, closure labels to Phase 4,
signal labels to Phase 5, release/health labels to the completed foundations
plus Phase 0, and demand-science labels to Phase 3. Do not create a new plan
or a new implementation branch from a legacy label.

## Decision

This is the best path forward for the current project:

1. Keep the normal citywide simulation as calibrated, mesoscopic SUMO.
2. Make every normal run, closure run, closure recommendation, and signal
   study consume one exact versioned scenario definition.
3. Make sensor intake data-driven and prove the contribution of each new
   sensor instead of assuming that more data is automatically better.
4. Treat signal optimization as a phase-plan problem, not a collection of
   independent lamps. Use microscopic SUMO only where signal behavior must be
   judged.
5. Publish an answer only when its evidence and provenance are sufficient.

One engine cannot honestly provide both fast citywide flow estimates and
physical per-lane signal behavior. The correct product is one application
with two computation levels:

```text
SensorRegistry + versioned network
             |
             v
Calibrated DemandBuild (one build ID)
             |
             v
ScenarioSpec: normal case or exact closure schedule
             |
             v
Citywide mesoscopic SUMO
        |                         |
        v                         v
Closure decision engine     Signal study input: arrivals, reroutes,
                              affected controller set
                                      |
                                      v
                         Bounded microscopic signal evaluation
```

The user sees one coherent study and one result. Internally, the system uses
the least expensive model that can support each claim.

## Why This Order Is Necessary

The project already has strong foundations:

- Citywide mesoscopic simulation is fast enough for normal days and closure
  studies, with calibrated demand, uncertainty variants, health telemetry,
  build fingerprints, and scenario publication gates.
- Time-windowed road closures and a closure-time screener already exist.
- A signal experiment framework exists for normal and closed-road cases.
- The web application already has focused workspaces rather than a single
  overloaded screen.

There are also foundation gaps that should be resolved before adding more
heuristics:

- New sensor direction metadata historically lived in `build_data.py`
  (`SENSOR_MEASURED_DIRECTION`); it is now sourced from the validated
  `data_in/sensors.json` registry. The hard-won verification workflow (check
  the city's trafikmängder catalogue FIRST; the delivered "Total" label was
  wrong for 4 of 5 sensors) is now represented by explicit registry fields,
  not only by prose. The 2026-07-16 audit found that several of those fields
  are not yet enforced at build time: active dates, quality status and
  approved directed snap identity must become a fail-closed gate before the
  registry can be called fully adopted.
- Structural products can be stale after a sensor change if cache identity
  does not include the sensor registry and all relevant inputs.
- The closure-time feature defaults to a proxy-selected subset of windows for
  fast exploration. `--exhaustive` now evaluates every feasible window; only
  that mode may claim that it searched the global feasible set.
- The ordinary signal baseline is generated by `netconvert --tls.guess`.
  It is not a real Gothenburg controller plan, so synthetic timing results
  must not be presented as operational instructions.
- Legacy signal requests default to 07:00-09:00, but spec-driven signal
  studies now take their measurement window and closure interval from the
  shared ScenarioSpec. A selected time-windowed closure therefore cannot
  silently be optimized for a different period.
- The latest demand diagnostics record purpose-route incompatibility. Until
  this is resolved, trip-purpose labels are useful diagnostics, not proof of
  the real purpose of every simulated vehicle.

Already in place — do NOT rebuild these, extend them (verified against the
working tree 2026-07-15; spot re-verified 2026-07-16 — publish gates, run
registry and benchmark harness confirmed present):

- Trajectory reconciliation is complete: `final_route()` reads rerouted
  vehicles from `<routeDistribution>`, unfinished vehicles park visibly,
  and the artifact withholds itself below 98% source-file integrity. Any
  deliberately non-drawable path remains visible in the published
  `displayed_share` diagnostic rather than being silently lost.
- Health telemetry fails closed (a missing per-seed statistics file flags
  the build), and the E2 publisher refuses any flagged baseline.
- Build-ID equality and per-variant (q10/q50/q90) fit gating in the
  publish gate are implemented in the current working tree
  (`validate_staged_scenarios`); the canonical fingerprint and candidate
  cache implementations now live under `traffic_sim/` and are included in
  invalidation keys.
- An immutable run registry (`runs/<id>/` manifests, `latest_*` pointers,
  `/api/jobs` durable records with orphan reconciliation) exists — the
  release structure in Phase 0/1 must be an EXTENSION of it, never a
  second registry.
- The assembled per-build validation report (`web/data/validation.json` +
  the 🛡 panel) exists; Phase 6's study view builds on it.
- A results-preserving speed benchmark harness exists
  (`make benchmark-speed`, semantic hashes).
- Security hardening is done: mutating endpoints are POST-only with an
  Origin-based CSRF guard, CSP without inline scripts, reflected-XSS fix.

## Product Outcome

The finished program should provide four connected outputs from the same
release:

| User task | Input | Output | Claim level |
| --- | --- | --- | --- |
| Normal traffic | Date, historical or forecast source | Citywide 15-minute flow, representative vehicles, confidence | Calibrated near sensors; prior-driven where unmeasured |
| Road closure | Exact road directions and time window | Rerouted citywide flow, delay, access loss, confidence | Fast mesoscopic incident study |
| Best closure time | Closure requirement and permitted windows | Least disruptive feasible schedule, alternatives, uncertainty | Simulation-backed decision support |
| Signal timing | Normal or closure ScenarioSpec plus SignalPlan | Green/red seconds per phase, cycle, offset, queues, comparison to baseline | Synthetic experiment or city-configured recommendation, stated explicitly |

## Practical Development Roadmap

This section and the numbered Phases later in the document describe the SAME
work from two angles — read them this way, or their numbering will mislead:

- **Stages (this section)** are the product narrative: what the user can
  trust, in which order, and why.
- **Phases 0-7 (below)** are the engineering work-packages with acceptance
  gates. The **canonical execution order is the numbered task list** in
  "Recommended Implementation Order" at the end. Since 2026-07-16 that list
  interleaves the phases (registry, variant-identity and output-fit gates
  run before the Phase 0 freeze), so it is no longer simply the phase
  order; each task there names the phase whose acceptance gate governs it.

Mapping, so no executor has to reconstruct it:

| Stage | Built from phases |
| --- | --- |
| 1 Normal baseline trusted | 0 (freeze) + 3 (demand realism) |
| 2 Closure as incident study | 1 (ScenarioSpec) + 4 (decision engine) |
| 3 Signals on closure traffic | 5 (synthetic study, steps 1-4) |
| 4 Sensors prove their value | 1 (SensorRegistry) + 2 |
| 5 Physical network inputs | 3 (network realism) |
| 6 One city-configured corridor | 5 (steps 5-6, blocked on city data) |
| 7 Scale under gates | 7 |

A later stage may use an earlier stage's artifact only after that artifact
has passed its stated gate.

### Stage 1: Make normal traffic the trusted baseline

**Do now, without new external data.** Enforce the SensorRegistry and reviewed
directed snaps, then verify final SUMO output against frozen sensor targets at
the correct physical-station aggregation. Finish purpose-compatible route
allocation, retain exact time-of-day and day-type demand, and rerun temporal
holdout plus leave-one-sensor-out validation. Keep the existing structural
checks for route length, route diversity, onward distance after a sensor, and
near-sensor destinations. Freeze one normal golden day only after all q10/
q50/q90 variants pass their own input-fit, final-output-fit, structure and
health gates.

**Why first:** a closure or signal optimizer can only be as credible as the
normal traffic it compares against.

**Exit condition:** the baseline has a content-addressed build ID, an approved
sensor-snap manifest, a validated normal scenario, a reproducible validation
report with final sensor output fit, and no purpose-level claim when purpose
compatibility fails.

### Stage 2: Make road-closure simulation a proper incident study

**Do next, using the trusted normal build.** Every closure must be one exact
ScenarioSpec: directed edge IDs, active start/end, duration, permitted-access
exceptions, analysis window, demand build, network build, seed set, and
direction-variant mapping. Use the same seed/variant pairs for the normal
baseline and the closure so differences are caused by the closure rather than
Monte Carlo noise.

For each closure, the engine must:

1. verify that the selected road directions and closure interval are exactly
   what SUMO receives;
2. check topology before simulation, including detour existence, access loss,
   and whether an affected movement can become stranded;
3. reroute traffic through the full city graph, not only the visible map;
4. measure closed-edge leakage, dropped/truncated vehicles, teleports,
   unfinished vehicles, added distance, paired delay, throughput and queue/
   spillback proxy;
5. reject a superficially fast result when it obtained that result by losing
   access or dropping vehicles;
6. publish the changed flows, route consequences, uncertainty and explicit
   health gates alongside the map animation.

Use mesoscopic SUMO citywide for speed and coverage. Use a bounded
microscopic component only around the affected junctions when queue, lane,
roundabout or signal claims are needed. Do not turn the whole city
microscopic.

**Exit condition:** a closure result is reproducible, has no integrity or
health failure, and clearly distinguishes "lower delay" from "least harmful
overall". A closure-time recommendation uses `--exhaustive` for a bounded
candidate set, or explicitly says that it is only a screened subset.

### Stage 3: Optimize signals for the actual closure traffic

**Works now as a synthetic experiment.** Take the exact closure ScenarioSpec
from Stage 2, use its actual rerouted arrivals, and optimise only the legal
phase structure. The optimizer may change cycle, phase green budget, legal
yellow/all-red/red-yellow and offset; it may not create new simultaneous
greens. Compare candidate and baseline with identical warm-up, measurement,
drain, seeds and demand variants.

Publish numeric timings per controller/link and a paired delay comparison.
The candidate is rejected when it harms a hard safety/access/health gate,
creates spillback, or is not robust across uncertainty variants. While the
phase structure is generated from SUMO, label it `synthetic`; it is an
experiment, not an instruction to operate a city controller.

**Exit condition:** every timing result identifies its SignalPlan,
ScenarioSpec, timing window, provenance, safety status and comparison.

### Stage 4: Let every new sensor make a measured difference

**Do continuously as sensors arrive.** Add data through `data_in/` and the
SensorRegistry, never through a source-code constant. Validate station ID,
raw columns, timestamp coverage, count units, directional semantics,
coordinate CRS, bearing, snap distance, counterpart direction and active
period before calibration. A sensor must overlap the study date; future-only
data cannot validate a historical 2025 simulation.

After each addition validate its active period, quality status, approved snap,
bearing and network-specific snap distance; then rebuild features, direction
splits, observability, bounds, priors, forecast inputs, demand and validation.
Publish a contribution report:
the new measured edges, coverage, confidence change, holdout recovery,
affected closure corridors, and `improved`, `neutral`, or `insufficient
evidence`. Prefer new directional counters on signal approaches and detour
routes, where they can change a closure/signal decision rather than merely
duplicate an existing count.

**Exit condition:** no sensor can enter calibration without a validated
registry record, and its contribution is measured rather than assumed.

### Stage 5: Improve physical network inputs without inventing observations

Use public sources such as NVDB only for documented road structure: speed
limits, lanes, road class and prohibited directions. Reconcile them against
the stable edge-ID mapping, record imported/defaulted/manual provenance in
the network audit, and repair only reviewed mismatches. Do not calibrate
driving speed, lane changing or queue discharge from speed limits alone.

**Exit condition:** every network value used by SUMO is traceable to OSM,
NVDB, a reviewed override, or a declared fallback.

### Stage 6: Upgrade one corridor with city-provided evidence

When data access becomes possible, choose one signal corridor affected by a
realistic closure. Import its real controller plan, phase/movement mapping,
conflict and clearance matrices, pedestrian/cycle constraints, detector and
transit-priority rules, offsets, approach/turn counts and matching travel
times. Map and validate the package against the SUMO network before using it.

This upgrades that corridor from `synthetic` to `city-configured`; it does not
silently make the rest of the city equally accurate. Use it to validate the
synthetic model and decide whether its assumptions are useful elsewhere.

**Exit condition:** one corridor can reproduce its real signal plan and has
independent arrival/travel-time evidence for normal and closure evaluation.

### Stage 7: Scale only after the evidence gates pass

Expand sensor coverage, controller imports and closure scope incrementally.
Preserve performance by caching immutable fingerprinted artifacts, using a
measured process budget, and retaining citywide meso as the default. Every
speed claim requires a golden-case before/after measurement and semantic
result comparison. Never gain speed by dropping seeds, uncertainty variants,
rerouter coverage or solver work.

### What the user can do at each stage

| Stage reached | User-visible capability | Honest limitation |
| --- | --- | --- |
| 1 | Simulate normal traffic with calibrated sensor evidence and confidence | Unmeasured streets remain prior-driven |
| 2 | Close one or more roads and compare rerouted delay, access and confidence | Queue detail is limited outside micro study areas |
| 3 | See green/red numbers for normal or closure traffic | Synthetic controller phases until city plans are imported |
| 4 | Add a sensor through data/registry and see its measured benefit | More sensors help where they add independent information |
| 5 | Trust that speeds/lanes/turn rules trace to a named source | Free-flow structure only — never observed driving behaviour |
| 6 | Receive city-configured timings for one validated corridor | Does not validate every other junction automatically |
| 7 | Larger sensor sets and more corridors at the same trust level | Growth is gated — each expansion re-proves its gates |

## Non-Negotiable Rules

- Keep stable edge IDs, WGS84, absolute time, `null != 0`, and the browser
  `flowAt(edgeId, t)` seam.
- Every artifact must carry the exact demand build ID, network fingerprint,
  random seeds, direction variants, tool version, and source provenance.
- Do not make a run faster by reducing seeds, variants, solver iterations,
  rerouter coverage, or mesoscopic fidelity.
- A sensor count is measured evidence. A learned prior, mathematical bound,
  and simulated result must remain distinguishable in the UI and output.
- A failed or incomplete job must never replace the currently published
  release.
- A signal result may only be called a recommendation when its plan provenance
  and safety gates permit that claim.

## Phase 0: Freeze a Reference Release

**Purpose:** Establish one reproducible baseline before changing behavior.

### Work

1. Commit the already validated accuracy, robustness, and speed work as one
   named release.
2. Freeze three golden cases under that release — pinned to the exact
   inputs the repo already validates against, so the baseline is the
   system's own proven state rather than a new arbitrary one:
   - normal historical full day: **2025-09-16** (the structural reference
     date; the LOSO baseline, the PFE benchmark fixture, and every
     structure-gate number on record were measured against it);
   - a known-detour full-day closure: **Skånegatan two-edge closure**
     (60786979_3575001205_0 + 1455801464_18241874_0 — the closure whose
     rerouting, truncation, and leak behaviour is already documented);
   - one bounded microscopic signal smoke case.
3. Record semantic hashes, run time, peak memory, build ID, network hash,
   route artifacts, input and final-output sensor fit, approved-snap manifest,
   variant coverage, seed health, closure integrity, and trajectory
   reconciliation.
4. Keep the golden artifacts and their manifest separate from normal pytest
   timing tests. Build the release directory as an extension of the
   EXISTING `runs/` registry (same manifest conventions, same atomic
   `latest` pointer mechanism) — two parallel registries is how artifacts
   get separated from their provenance again.
5. The release pointer must be reversible: rolling back to the previous
   golden release is one pointer flip, exercised once as part of this
   phase (an untested rollback is not a rollback).

### Acceptance gate

- The full test suite passes.
- Each golden case has a complete health record and no publication gate
  failure.
- The normal golden case has frozen sensor inputs, approved sensor snaps and
  a passing final SUMO output-fit artifact; it is not a legacy reconstructed
  audit.
- A refactor intended to preserve results matches the semantic baseline.

## Phase 1: Shared Versioned Contracts

**Purpose:** Prevent normal, closure, and signal paths from using different
definitions of the same study.

### New artifacts

#### `SensorRegistry`

Store it as the versioned machine-readable file `data_in/sensors.json`. It
must contain at least:

```text
sensor_id
active_from, active_to
measurement_semantics: directional | two_way_total
measured_bearing or permitted_bearings
coordinates and coordinate_reference_system
source and source_file identifier
snap_status, approved_edge_ids, snap_distance_m
catalogue_verification: status, date, verifier   # trafikmängder catalogue
quality_status and notes
```

The registry, not a Python constant, becomes the source of truth for sensor
direction semantics (it absorbs `SENSOR_MEASURED_DIRECTION`). Manual
overrides remain possible, but must be explicit registry records with a
reason and reviewer marker. `catalogue_verification` encodes the project's
hardest-won intake lesson as a field instead of folklore: the delivered
"Total" label was wrong for 4 of 5 sensors and was only caught by checking
the city's own trafikmängder catalogue — a sensor whose semantics have not
been verified against the catalogue must not reach calibration.

The registry is not valid merely because its JSON parses.  For the exact
study interval and network fingerprint, intake must require: an active period
covering the data, `quality_status: accepted`, a reviewed snap status, one or
two explicitly approved directed edge IDs consistent with the measurement
semantics, matching bearing, and a recorded snap distance within the agreed
limit.  The network build writes a resolved-snap artifact; demand consumes
that artifact instead of silently trusting a fresh automatic snap.  This lets
OSM evolve without silently moving a real counter.

#### `ScenarioSpec`

One versioned object must be consumed by `run_scenario.py`,
`suggest_closure_time.py`, `signal_optimize.py`, `signal_closure_combine.py`,
`serve.py`, and the browser:

```text
scenario_id
demand_build_id
network_build_id
start_time, end_time
closures: [ClosureSpec]
simulation_mode
seed_set and demand_variant mapping
analysis_window: warmup_s, measure_start, measure_end, drain_s   # optional
objective_profile
signal_plan_id or no_signal_plan
```

`ClosureSpec` contains directed edge IDs, active start/end time, closure type,
and any permitted-access exceptions. No feature may recreate a closure from a
different fixed time window.

`analysis_window` exists because the accuracy review found signal studies
that started with an empty network at 07:00 and stopped abruptly: any
bounded evaluation must state its warm-up, its measured period, and its
drain explicitly, IN the spec — not as per-tool constants — so a
07:00-09:00 signal request can never silently mean "green splits from
07:00-08:00, offsets from the whole day, evaluated over two cold hours".

#### `SignalPlan`

This is the only input accepted by a signal study:

```text
signal_plan_id, network_build_id, provenance
controller/TLS mapping, link-index mapping
movement and conflict definitions
phase states and compatible movements
cycle, green, yellow, all-red, red-yellow, offset
min/max timing constraints
pedestrian, cycle, transit, emergency, and detector rules
day type and active time range
```

Until a verified city plan exists, the provenance must be `synthetic` and the
UI must describe the result as an experiment, not an operational instruction.

#### `DecisionResult`

The closure and signal tools should return a shared result envelope with the
input IDs, alternatives tested, objective metrics, uncertainty, gates,
provenance, and a machine-readable recommendation status.

### Implementation rules

1. Use dataclasses or typed validation functions at every file/API boundary.
2. Include all schemas and all sensor metadata in content fingerprints.
3. Reject a build if an artifact required by its job type is absent: a registry
   entry/snap approval where sensor calibration is used, a route artifact where
   routes are consumed, a SignalPlan for a signal study, or a matching build ID
   whenever artifacts are combined.
4. When `n_variants=3`, derive the required q50/q10/q90 coverage from demand
   metadata and reject a normal ScenarioSpec that maps every seed to one
   variant.  A reduced mapping is diagnostic-only and cannot publish a normal
   release.
5. Keep backward-compatible CLI shims while migrating callers one by one.

### Acceptance gate

- A normal, closure, and signal job loaded from the same ScenarioSpec report
  identical date/window/closure identity.
- A selected 13:00-15:00 closure cannot accidentally produce a 07:00-09:00
  signal study.
- A stale observability, bounds, prior, or signal artifact is rejected rather
  than reused.
- A pending, inactive, unapproved or changed sensor snap is rejected before
  it can affect a calibration target.
- A normal uncertainty build cannot publish unless it includes every declared
  demand variant and records the exact seed-to-variant mapping.

## Phase 2: Sensor Growth That Proves Its Value

**Purpose:** Let the city add sensors without source-code edits and make the
accuracy benefit visible.

### Intake workflow

1. Validate raw CSV columns, timestamps, count units, active period, missing
   quarters, duplicate records, impossible negative values, and station IDs.
2. Validate every registry entry against the network: distance to geometry,
   direction/bearing, two-way counterpart when applicable, edge uniqueness,
   and lane/road-class plausibility.
3. Fail closed for an unknown sensor or ambiguous directional meaning. A warning
   is not enough because an incorrectly snapped count corrupts calibration.
4. Rebuild features, direction split, observability, bounds, corridor priors,
   assignment priors, forecast inputs, demand, baseline, and validation from
   the new release fingerprint.
5. Preserve the old release until the replacement satisfies all gates.

### Sensor contribution report

For every added sensor, publish:

- its data-quality and snap report;
- measured edges and time coverage;
- bounds narrowed by the sensor;
- leave-one-sensor-out and temporal holdout results before and after addition;
- confidence reduction by edge and by incident-relevant corridor;
- forecast impact once sufficient history exists;
- a clear `improved`, `neutral`, or `insufficient evidence` conclusion.

The LOSO mechanism already exists (`validate_sim.py`, calibrating each fold
with the exact deployed constraint set) — this phase reuses it per added
sensor rather than building new machinery; the temporal holdout is the new
piece. One documented interpretation rule carries over from the G1
investigation: a fold ratio near zero can measure the sensor's
INFORMATIONAL ISOLATION (nothing else constrains its corridor — sensor
1076 sits at 0.05 for exactly this reason, proven by a controlled pre-fix
rerun), not model error. The contribution report must therefore always
pair a recovery number with the sensor's isolation context before
concluding `improved` or `neutral`.

More sensors should improve the estimate where they add independent information.
The program must not falsely promise that one poorly located or low-quality
sensor improves every road in the city.

### Sensor placement guide

Rank prospective locations by expected information gain, combining:

- wide observability bounds;
- low current confidence;
- network connectivity and corridor coverage;
- closure-critical detour corridors;
- complementarity with existing sensors;
- practical station data quality and directional observability.

### Acceptance gate

- A new station can be added through data and registry metadata only.
- Its addition forces a new fingerprinted build.
- The map shows both the added measurement and its actual confidence impact.
- The generated `network.geojson` and demand manifest name the same reviewed
  directed snap, bearing, active period and registry hash.

## Phase 3: Improve Normal Citywide Realism

**Purpose:** Improve what the default simulation means before making more
decisions from it.

### Demand and validation work

1. DONE and guarded (verified 2026-07-16): the exact-day departure shape is
   aggregated per physical station, not per directed edge — `real_day_shape`
   counts a duplicated two-way Total once, and would sum genuinely different
   directional arrays instead of discarding one (regression test
   `test_genuinely_directional_values_are_summed_once_per_station`).
2. Calibrate and validate the final SUMO edge-entry output against the frozen
   target before interpreting a normal run as sensor-delivering.  Preserve
   per-seed integers and raw ensemble values separately from map rounding;
   compare station 107 only at its physical two-way total.  Diagnose entry-
   time residuals before introducing the bounded output-feedback correction
   described above.
3. Make purpose compatible with the selected route instance. The proper fix is
   a purpose-aware route allocation or a purpose-stratified PFE formulation,
   benchmarked against the current solver. Do not simply relabel incompatible
   routes after solving. (The current length-aware post-solve allocation
   preserves P(length|purpose) and the exact purpose×time mix but current
   q10/q50/q90 diagnostics report at least one provenance-incompatible
   allocation in all 96 quarters. Do not quote a vehicle-share percentage
   until a versioned artifact calculates it; this item must drive the
   incompatibility diagnostic to zero. The H2 benchmark fixture exists
   precisely so a solver-formulation change here cannot silently alter
   results.)
4. Surface purpose compatibility in the validation report and block claims
   about purpose-specific behavior when the diagnostic fails.
5. Rerun leave-one-sensor-out validation and temporal holdouts after each
   substantive demand change, and update the recorded honest baseline
   (currently min 0.05 / median 0.78 / max 1.95, measured 2026-07-13 —
   quoting any older number is a documentation bug). Sensor GEH is
   calibration fit, not independent validation.
6. Keep structure gates for trip length, near-sensor destinations, onward
   distance after the last sensor, route diversity, and unserviceable counts.

### Multi-day simulation

The current trusted unit is one complete local calendar day. A multi-day study
must not be implemented by concatenating independent daily outputs or by
silently resetting the network at midnight. Build it only after a two-day
continuous prototype proves the following contract:

Until that gate passes, the existing 1–7-day control is an **experimental
build request**, not a normal release selector.  It may produce a continuous
route list, but it must show that status and must not silently replace a
trusted one-day baseline.

1. `ScenarioSpec` and demand metadata carry an explicit local-date range,
   time zone, ordered analysis windows and the exact source selected for each
   day. Internally use monotonic simulation seconds; retain ISO datetimes with
   offsets for artifact identity and browser display.
2. Produce one monotonically departed route set across the entire range.
   Vehicles still active at midnight remain accounted for into the next day;
   no vehicle is dropped merely because its departure date changes.
3. Preserve the correct day-of-week, holiday and forecast/historical profile
   for every 15-minute interval. The known DST gaps remain `null`/explicit
   gaps, never invented zero traffic or a duplicated hour.
4. Calibrate and health-check each date and direction variant separately, then
   publish one range-level manifest that links all daily diagnostics. Do not
   let a good first day hide a failed later day.
5. Aggregate flows by absolute timestamp and day, retain boundary-vehicle,
   unfinished and route-error accounting, and make trajectory export opt-in
   or sampled for multi-day runs so the browser and disk footprint stay
   bounded.
6. Add cancellation, disk-budget and process-budget tests before permitting a
   multi-day API request. A longer study must not block ordinary one-day work
   or overwrite its artifacts.

**Acceptance gate:** a frozen two-continuous-day normal case has monotonic
time, correct local calendar labels, per-day q10/q50/q90 input and final-
output fit plus health, explicit midnight carry-over accounting, no missing
interval silently read as zero, and a result-equivalence comparison against
the two single-day reference studies except for the explicitly measured
boundary carry-over.

### Network realism work

1. Build a source-to-SUMO network audit sidecar for speed, lane direction,
   turn lanes, turn restrictions, priority, traffic-signal membership, and
   roundabout membership.
2. Retain source provenance for each value: imported, defaulted, or manually
   reviewed.
3. Repair only reviewed network issues while preserving the stable edge-ID
   mapping layer.
4. Use OSM/default speeds as free-flow constraints. Do not tune vehicle
   behavior, lane changing, or speed factors without held-out speed/travel-time
   observations.

### Confidence work

Replace a single unexplained confidence number with components for measured
evidence, mathematical bounds, held-out error, demand uncertainty, and
Monte Carlo stability. Keep the simple map presentation, but allow the user
to inspect why an edge is uncertain.

### Acceptance gate

- All q10/q50/q90 variants pass their own input-fit, final-output-fit,
  structural, and health gates.
- Purpose incompatibility is zero or explicitly blocks purpose-level claims.
- The new release is no worse on frozen temporal and held-out validation.

## Phase 4: Make Road-Closure Advice a Real Decision Engine

**Purpose:** Answer "when should this road be closed?" as a constrained,
auditable decision rather than a low-flow guess.

### Candidate definition

The user supplies road directions, closure duration, permitted dates/times,
access requirements, and optional operational restrictions. The engine builds
all feasible 15-minute-aligned windows within that scope.

### Feasibility screen

Reject or flag a candidate before ranking when it has:

- no network detour for affected movements;
- unacceptable lost access or stranded trips;
- closed-edge leakage;
- health failure, excessive teleporting, or excessive unfinished vehicles;
- an incompatible ScenarioSpec or stale baseline;
- policy constraints such as emergency, public transport, pedestrian, or
  construction restrictions when those inputs are available.

### Evaluation

1. Reuse the exact normal demand build and same seed-to-variant mapping.
2. Compare each candidate against its matched baseline seed/variant pair.
   Do not compare a candidate only to a baseline mean when pairing exists.
3. Prefer exhaustive simulation of all feasible windows for a bounded
   one-day study, after measuring the candidate grid. A closure spanning
   `d` 15-minute intervals permits `96 - d + 1` start slots within a day;
   the measured whole-day 3-seed meso closure costs ~40 s, but actual
   runtime also depends on closure geometry and candidate count. Benchmark
   multi-day demand separately before promising an overnight result. A
   proxy may prioritize work, but it must never be presented as proof of
   the best global choice.
4. Use bounded independent SUMO processes only after the benchmark confirms
   that the selected worker count is faster and result-equivalent.
5. Score expected and worst-case delay, added distance, rerouted vehicles,
   access loss, stranded/truncated trips, queue/spillback proxy, closure
   integrity, and confidence. A short time loss caused by dropping access is
   not a good outcome.

### Result

Return a ranked feasible set, a clear best candidate only when it is robust,
and `no viable closure` when every option violates a hard gate. If the top
alternatives overlap under uncertainty, show that there is no clear winner.

### Acceptance gate

- The reported closure schedule is exactly the schedule simulated.
- Every eligible candidate uses matched seeds/variants and the same demand
  build.
- The result distinguishes lowest delay from least harmful overall outcome.

## Phase 5: Build a Defensible Signal Optimizer

**Purpose:** Produce actual green/red values in a way that preserves safety
and reflects normal or closure traffic.

### Usable now: the current synthetic signal study

This feature is already useful without waiting for a new data delivery. The
user selects a normal scenario or an exact road-closure scenario; the program
uses the same demand build, closure interval, seeds, and direction variants;
then it runs microscopic SUMO signal experiments against the rerouted traffic.
For a closure, the two-pass study first drives the closure with the baseline
program, extracts the trips that actually rerouted, and optimizes/evaluates
the signal timings against those post-closure trips. The UI already reports
numeric cycle, offset, green, yellow, and red seconds per controlled link.

The present source of phase compatibility is `netconvert --tls.guess`, so the
result must be labelled **synthetic experiment**. It answers: "within this
network model and these conservative timing rules, which green splits reduce
the simulated delay after this closure?" It does not answer: "change
Gothenburg's controller to these values." The distinction is a claim gate,
not a reason to withhold the useful simulated result.

### Execution order: useful now, then city-configured

1. **Freeze the current synthetic study.** Keep the existing normal and
   closure paths (`signal_optimize.py` and `signal_closure_combine.py`) as the
   usable product. Every result must identify `synthetic` provenance, the
   exact ScenarioSpec, the closure's active interval, the measured window,
   seed/variant pairs, and whether it passed health and closure-integrity
   gates.
2. **Make a versioned synthetic SignalPlan.** Export the generated TLS
   topology, link mapping, phase states, compatible-movement sets, timing
   limits, and clearance values to a `SignalPlan` artifact. Preserve the
   generated phase structure; never infer a new simultaneous green merely
   because two movements have high demand. Reject a junction whose generated
   link map or phase sequence cannot be reconciled with the SUMO network.
3. **Optimize the closure's actual arrivals.** Use the closure ScenarioSpec
   rather than a fixed time window. Run citywide meso once for demand/reroute
   context; use micro only for the affected signal component and its queues.
   Optimise cycle, phase green budget, legal clearance, and offsets jointly.
   A higher flow may receive a longer green, but only after competing phases,
   pedestrian/cycle minimums, and spillback guards are evaluated.
4. **Publish an honest numeric result.** For every controller show baseline
   versus candidate green/red/yellow/all-red, the affected movements, paired
   delay change, queue/spillback/health state, and `no valid improvement`
   when the candidate is unsafe, disqualified, or not robust across q10/q50/
   q90 demand variants.
5. **Import one real junction cluster before scaling.** Build an adapter from
   the city's documents into the same SignalPlan schema, run mapping and
   safety checks, and compare synthetic versus imported plans on the same
   normal and closure scenarios. Do not import citywide plans first: one
   reviewed corridor is the correct acceptance gate.
6. **Promote only verified plans.** `city-configured` is allowed only when
   every controller/link/phase mapping, conflict matrix, clearance, detector
   rule, and active time plan is present and valid. Otherwise keep
   `synthetic` even if the numerical result looks attractive.

### Sensor growth and signal accuracy

A new sensor improves this workflow by narrowing the calibrated traffic
arrivals, not by directly revealing a green split. The most valuable future
sensors are directional counters on approaches to the selected signal cluster
and on likely detour routes before and after a closure. A station must overlap
the simulated date and be added through the registry with its coordinate CRS,
measurement semantics, direction/bearing, active dates, raw-file identity,
and approved edge snap. After every addition, rebuild all derived artifacts
and publish a before/after contribution report: holdout recovery, confidence
change, affected movement demand, and whether the signal recommendation
changed. A sensor added after the historical date may improve future studies;
it must not be silently used as evidence for a 2025 historical run.

### Verified data position: what is available, what is only city-held

The table below deliberately separates evidence that is publicly confirmed
from data that the city is likely to manage but has not committed to deliver.
"City-held" means the documented signal process requires or uses the artifact;
it does **not** mean that this project is automatically entitled to receive
it.

| Data | Status on 2026-07-15 | Use now | Needed for |
| --- | --- | --- | --- |
| Supplied 2025 six-sensor, 15-minute counts | Present in this project | Calibrate current normal and closure demand | Current simulation |
| Göteborg traffic catalogue: counts, average speeds, nearby flow measurements from 2019 onward | Public interactive catalogue; no bulk/API delivery is assumed | Manual/approved external reasonableness checks only | Extra validation, not calibration replacement |
| NVDB lane counts, speed limits, functional class, forbidden direction and road network | Publicly documented API data; download requires registration | Network audit and legal/free-flow constraints | Better routing structure, not observed travel speed |
| Signal plans, conflict/spärrtid matrices, signal-group functions, detector functions, plan selection and priority rules | City signal process requires these documents; no public per-controller dataset was found | Not yet | Real controller-compatible timing |
| Historical link/path travel times and detector logs | A city/Trafikverket travel-time camera system is documented, but no current public historical export was verified | Not yet | Validate speeds, queues and signal effects |
| Turning counts at signal approaches | Not publicly verified | Not yet | Validate left/through/right demand |
| Local OD/purpose microdata | Not open data; SCB access is a project-specific, reviewed order | Not needed for the current synthetic study | Stronger OD and purpose claims |

Sources: Göteborg's public catalogue explicitly exposes traffic volumes,
average speeds and nearby measurements, but as an interactive report rather
than a promised bulk feed ([Göteborg traffic catalogue](https://goteborg.se/wps/portal/start/trafik-och-resor/trafik-och-gator/trafikinformation/statistik-om-trafiken-i-goteborg/trafikmangder-pa-olika-gator)).
Trafikverket documents NVDB's available lane, speed-limit and forbidden-
direction datasets and its registration requirement for downloads
([NVDB open API](https://bransch.trafikverket.se/tjanster/data-kartor-och-geodatatjanster/nyheter-om-trafikverkets-data/2025/nvdb-vagdata-tillgangliga-i-trafikverkets-datautbytesportal-for-anvandning-i-oppet-api/)).
Göteborg's current technical handbook requires conflict and clearance matrices,
signal-group functions, detector functions and plan-selection/priority
descriptions for traffic-signal work
([signal requirements](https://tekniskhandbok.goteborg.se/12-projektering/12b-projekteringsforutsattningar/12bh-trafiksignaler/)).
It also documents travel-time cameras operated jointly with Trafikverket, but
the available public page is archival and does not establish a current raw-data
service ([travel-time system](https://tekniskhandbok.goteborg.se/Arkiv/2015-1/__site/__planering__planeringsf%C3%B6ruts%C3%A4ttningar__grunddata__restider.html)).
SCB confirms that microdata requires a defined research/statistics project and
a confidentiality review ([SCB microdata](https://www.scb.se/vara-tjanster/bestall-data-och-statistik/mikrodata/)).

### Exact request to send when data becomes possible

Ask the city for **one selected signal corridor**, not the whole city. Request
the following for one normal weekday and the planned closure period: controller
and junction IDs; signal-group-to-SUMO-movement mapping or drawings; active
time plans; phase sequence; conflict and clearance/spärrtid matrices; minimum/
maximum green, yellow, all-red and red-yellow; pedestrian/cycle constraints;
detector and transit-priority logic; offsets/coordination; temporary work-plan
rules; 5- or 15-minute approach/turn counts; and matching link/path travel
times. Ask separately whether the city can license a historical extract for
research. This is the smallest package that upgrades one corridor from a
synthetic experiment to a defensible city-configured study.

### Optimization model

1. Optimize legal **phases**, each a set of compatible movements that can be
   green simultaneously. Never optimize each lamp separately.
2. Preserve conflict-free phase structure from SignalPlan.
3. Choose cycle length, phase green split, yellow, all-red/red-yellow,
   offsets, and plan switching by time of day within legal min/max limits.
4. Use arrivals and movement demand derived from the exact normal or closure
   ScenarioSpec. A closure study must receive the actual closure edges and
   active times, not a fixed default window.
5. Evaluate a normal and closure plan against their own real baseline plan
   using equal warm-up, measurement, drain, seeds, variants, and network.
6. Optimize a robust objective: total and percentile delay, stops, queue
   length, spillback, throughput, transit/pedestrian constraints, and
   robustness across demand variants.

### Fidelity and performance rule

Use citywide meso to create normal/closure flows and identify affected signal
components. Use bounded microscopic evaluation for signal phases, queues,
lane use, and roundabouts. This can remain invisible to the user as an
implementation detail, but the result must state its model and area.

### Result shown to the user

For each controller and time period, show:

```text
baseline plan and provenance
cycle length and offset
phase name and compatible movements
green, yellow, all-red/red-yellow, and resulting red seconds
normal or closure ScenarioSpec identifier
paired baseline comparison and uncertainty
queue/spillback/safety status
```

### Acceptance gate

- No conflicting protected greens or invalid clearance timing.
- The actual scenario window, closure schedule, plan ID, and demand build ID
  match exactly.
- No health, access, closure-integrity, or spillback gate fails.
- A candidate is selected only when it robustly improves on the baseline;
  otherwise publish `no valid improvement`.

## Phase 6: One Application, Not Separate Tools

The existing task-oriented UI should remain. Do not rebuild it as another
landing page. Instead, make every task open a shared study context.

### Required behavior

1. Normal simulation selects a DemandBuild.
2. Road closure creates a ScenarioSpec from that build.
3. Closure timing creates candidate ScenarioSpecs from that same build.
4. Selecting a closure recommendation loads that exact ScenarioSpec.
5. Signal optimization consumes the selected ScenarioSpec and its exact time
   window.
6. The validation panel follows the active build and scenario.
7. A normal scenario exposes its source, frozen target, final SUMO output and
   final-output fit at each physical sensor; a two-way Total is presented as
   one station with modelled directional children.
8. A job page shows progress, cancellation, logs, artifacts, confidence, and
   final gates for each study.

Every result page must make three things immediately visible: what was run,
whether it completed healthily, and what the result is allowed to claim.

### Acceptance gate

- From any result page, the operator can reach the exact ScenarioSpec,
  build ID, job record, and validation report that produced it, without
  reading server logs.
- The 🛡 validation panel reflects the ACTIVE study's build, not merely
  the latest demand build.
- The sensor table never makes a source observation, a split assumption, a
  rounded map value and a final SUMO count look like the same number.
- A cancelled or failed study leaves the previous published study visible
  and clearly labelled as the one still in force.

## Phase 7: Maintain Performance Without Reducing Fidelity

The performance policy is result preservation first.

1. Continue using the benchmark harness and semantic hashes for normal,
   closure, and microscopic smoke cases.
2. Keep citywide meso as the default for normal and closure work.
3. Cache only immutable, fully fingerprinted artifacts such as candidate
   geometry, network metadata, and validated signal plans.
4. Use isolated workspaces and a single process budget for candidate/seed
   batches. Do not overlap PFE workers with SUMO workers.
5. Promote parallel seed or candidate execution only after repeated measured
   trials show a material wall-time improvement with identical results.
6. Keep detailed vehicle, lane, and queue output limited to the selected
   signal study so citywide runs do not produce unnecessary I/O.

Do not reduce seed count, uncertainty variants, solver iterations, closure
rerouter coverage, or simulation step fidelity as a speed shortcut.

One measured performance boundary to respect (2026-07-14): the dominant demand
cost is the deliberately sequential per-edge IPF update, already
flat-parallel across all cores — 96% of solve time with no safe lever
short of a JIT dependency. Performance work under this plan means caching,
scheduling, and I/O discipline, not touching the solver.

### Acceptance gate

- Every claimed speed-up ships with before/after wall time on a golden
  case AND an identical semantic hash (or a documented, reviewed reason
  the results changed).
- No cache is keyed on anything less than the full build fingerprint.

## External Data Requests (one consolidated list)

Four standing requests gate the highest rungs of this plan. Ask for them
together, through Miroslaw, as one package — each names exactly what it
unlocks:

1. **City signal controller plans** (phase diagrams, timings, detectors,
   priority rules — the exact one-corridor package is spelled out in
   Phase 5's "Exact request to send" section): unlocks `city-configured`
   signal recommendations; until then every signal result stays labelled
   synthetic.
2. **A cordon count for the inner city** (all gates, one day): the only
   measurement that identifies the through-traffic share — currently a
   supply-tuned prior with a documented sensitivity sweep in
   "Consolidated Engineering Findings." Unlocks a calibrated E-E/E-I/I-I
   composition and a stronger OD-matrix claim.
3. **RVU Västra Götaland microdata** (or a regional OD matrix): upgrades
   the purpose×length priors from shrunk national ratios to local
   estimates (Phase 3), and gives the purpose-compatibility work a ground
   truth to validate against.
4. **Time-stamped link travel-time or speed observations** (normal and, if
   available, incident periods): unlock local road-speed calibration,
   travel-time validation, and evidence for queue and roundabout claims.

## Recommended Implementation Order

```text
0. Enforce SensorRegistry active/quality/snap gate            (size M)  — bootstrap-review the six current snaps;
                                                                          record resolved-snap and registry hashes
1. Final normal SUMO sensor-output fit + publish gate         (size M–L) — input PFE GEH is not sufficient;
                                                                          retain raw per-seed and ensemble values
2. Variant identity/coverage and closure audit repair         (size S)  — semantic q10/q50/q90 IDs, no filename inference
3. Stale-artifact proof across every publication path         (size S–M) — complete existing DemandBuildSpec migration
4. Normal demand realism: purpose + structure repairs         (size M–L) — rerun temporal/LOSO validation afterward
5. Freeze reference release                                   (size S–M) — only after 0–4; freeze the proved state,
                                                                          not a mid-migration snapshot
6. Multi-day golden gate, then full UI release                (size M–L) — keep 1–7 days experimental until proved
7. Closure decision engine, paired feasible evaluation        (size L)  ◐ exhaustive CLI mode exists
8. SignalPlan audit and explicitly synthetic optimization     (size M–L; city-configured import blocked on data)
9. Browser study UI/history and regression smoke suite        (size M)
10. Expand sensor coverage and signal scope only after each gate passes
```

(Sizes: S ≤ half a day, M ≈ a day, L = multi-day.
◐ = partially done as of 2026-07-16; the markers are status, not a licence
to skip the item's remaining work or its owning phase's acceptance gate.)

Phase ownership, so each item inherits the right acceptance gate: item 0 →
Phases 1-2 (registry artifact and intake rules); item 1 → the sensor-value
contract section plus Phase 3's output-fit work and the publication gate;
items 2-3 → Phase 1; items 4 and 6 → Phase 3; item 5 → Phase 0; item 7 →
Phase 4; item 8 → Phase 5; item 9 → Phase 6; item 10 → Phases 2 and 7.

The immediate next implementation block is therefore: **make input identity
real** (approved SensorRegistry snap plus variant identity), then **prove
final normal SUMO sensor output**, then **complete stale-artifact proof**.
Only then repair purpose/structure allocation and freeze the resulting normal
release.  This order prevents a golden release from preserving an unreviewed
sensor mapping, an incomplete uncertainty set or a PFE-only notion of sensor
fit.  Send the external-data request package (see above) at the START of this
block — the city's response time, not implementation time, is the critical
path for the city-configured signal rung.

### Current verified status and concrete entry points

Verified end to end on the dev machine 2026-07-16 evening, after the
2027-10-20 forecast demand was REBUILT with the new code and the baseline
scenario rerun (this section describes the working tree about to be
committed as one unit):

- The active 2027-10-20 forecast baseline now carries FROZEN audit inputs
  (`provenance: demand_metadata` — the reconstructed-inputs caveat is gone),
  the raw-edgeData `output_fit` (100% GEH<5, mean abs error 2.07 veh,
  station-aggregated for 107) and per-station rows.  `validation.json`:
  counts_fit/structure/simulation/sensor_output/multi_day **pass** —
  the two long-standing structure drift flags cleared with this rebuild —
  and only `purposes` still warns (see next bullet).
- Purpose compatibility remains the open P0: signature-preserving
  replacement repaired ~2 500-2 650 routes per variant at identical sensor
  counts and 100% GEH<5, but ~5 300-5 900 mostly `through` routes per
  variant still lack same-purpose provenance in all 96 quarters, so
  `purpose_claims_allowed` stays false.  The remaining fix is
  purpose-stratified calibration and/or the cordon count (external data
  request 2).
- `data_in/sensors.json` has six catalogue-verified records with approved
  directed snaps; intake fails closed on pending/expired/unapproved/changed
  records and build_data revalidates resolved snaps against the registry.
- `runs/releases/` is still empty — the golden freeze (2025-09-16
  historical + Skånegatan closure + signal smoke) is the next block.
- Full suite on the dev machine 2026-07-16: **944 passed, 21 skipped**
  (~47 s).  Tests no longer rewrite the live `web/data/validation.json`
  (test_serve.py redirects the report path; found when suite runs churned
  the tracked artifact's generated_at).  A frozen release must still record
  its own exact runner and result in the release artifact.

Status of the previously listed concrete changes:

1. DONE — `validate_data_sensors` enforces quality/snap/active-date,
   `validate_resolved_edges` guards OSM drift, six-station bootstrap
   approved, negative tests in place.
2. DONE — semantic `demand_variant`/`target_key` on every seed job/result,
   `validate_variant_coverage` + publish-gate coverage check, tests for
   closure-renamed files and an all-q50 mapping.
3. DONE — `sensor_audit.output_fit` from raw pre-rounding edgeData in
   scenario JSON, `validation_report.py` (`sensor_output` section) and
   `serve.validate_staged_scenarios` (fails closed for new builds).
4. OPEN — residual classification (timing shift vs coverage vs integer
   effect) and any bounded output correction; current measured residual is
   mean 2.07 / max ~10.7 veh per station-quarter at 100% GEH<5.  Then rerun
   temporal + LOSO on 2025-09-16 historical.
5. OPEN — freeze the normal, closure and signal golden cases, including an
   exact browser/API smoke result and a loopback-capable full-test result.

## Definition of Success

The project has reached its intended next level when:

- a new sensor is added through a validated data record, triggers a new build,
  and reports the uncertainty it actually reduces;
- normal and closure simulations share one validated demand release;
- the closure tool can explain whether a road should be closed, when, and why
  a candidate is rejected or uncertain;
- the signal tool provides phase-level green/red seconds for both normal and
  closure demand, with the signal plan provenance made explicit;
- all results remain fast enough for the intended workflow without hiding
  model limitations;
- the user can inspect the exact evidence behind every recommendation.

## Explicitly Deferred Until Evidence Exists

- Claiming that synthetic SUMO signals are Gothenburg's actual controllers.
- Calibrating car-following, lane-changing, queue tails, or road-speed
  distributions without independent local speed/travel-time data.
- Claiming that every added sensor improves every edge rather than measuring
  its actual information contribution.
- Replacing the fast citywide mesoscopic model with citywide microscopic
  simulation.
