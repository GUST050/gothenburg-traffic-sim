# Gothenburg Traffic Simulation Improvement Plan

**Date:** 2026-07-18 (consolidated 2026-07-15; status re-verified against the
working tree 2026-07-18)
**Status:** Canonical improvement plan — active implementation is in progress.
**Structural authority:** `ARCHITECTURE.md` remains the source of truth for
the six-stage pipeline and fixed contracts. This is the only improvement,
review, performance, simulation, closure, signal and sensor-growth plan.

## Known Errors, Inaccuracies and Assumptions in the Simulation Flow

Audited end to end 2026-07-17 (raw data → network → demand → calibration →
SUMO → web), every number below re-checked against the working tree that
day.  This register is the honest answer to "what could be wrong?".  An item
being listed does not mean it is unaddressed — most are disclosed by design
(the confidence map exists because of them) — but nothing here may be
silently forgotten.  Ordered by pipeline stage.

### A. Sensor data (the ground truth itself)
1. **Six stations in two ~400 m clusters** constrain 7 directed edges of
   7 125.  Everything else is prior-driven inference; held-out accuracy is
   currently a typical factor 1.56 (geometric absolute error; LOSO
   2026-07-18 median ratio 0.994, range 0.763–2.576).
   Only more/better-placed sensors or external counts fundamentally fix
   this — that is the product's own pitch, not a bug, but every downstream
   number inherits it.
2. **Direction is modelled, not measured**, at the five single-direction
   stations' opposite carriageways and inside every "Total" sum: the
   dirsplit model is shrunk λ=0.256 toward 50/50 and validated only
   region-transfer (Nordic cities) plus two local spot checks (107: 52/48;
   1076 AM/PM 0.90).  The q10/q50/q90 variants carry this uncertainty into
   the ensemble — but the *center* of that interval is still a model.
3. **One year (2025), local time**: DST days each miss 4 quarters (kept as
   null); the 2027 forecast is LightGBM point estimates at the 6 sensors
   only, and simulating 2027 assumes 2025 structure (bounds/priors/
   corridor coupling frozen at STRUCTURAL_REFERENCE_DATE 2025-09-16) —
   documented design decision, unverifiable until 2027 data exists.

### B. Network (OSM → SUMO)
4. **9% of edges have defaulted speeds and 70% defaulted lane counts**
   (sumo/network_audit.json: 631 and 4 990 of 7 125) — OSM tags are absent
   there, so class-based defaults (e.g. residential 30 km/h, 1 lane) set
   capacity and free-flow time.  Meso travel times and queue capacity on
   those edges are assumptions.  NVDB import (plan: "Import reviewed road
   structure") is the evidence path.
5. **All 190 signal-controlled edges use GUESSED traffic lights**
   (netconvert --tls.guess; static synthetic programs).  Meso runs
   `--meso-junction-control.limited`, and SUMO's meso engine does not model
   actuated control at all (measured 2026-07-06).  Signal timing effects on
   corridor travel time are therefore approximations everywhere, and the
   signal-optimization products are relative comparisons on synthetic
   plans, never claims about the city's real controllers (deferred-claims
   list).
6. **The frozen OSM snapshot ages**: real drift observed (128 street-name /
   15 highway-class changes in one refresh probe).  Deliberate — stable
   IDs beat freshness — but a periodic reviewed refresh is eventually due.
   One suspicious import artifact remains unverified against reality: node
   3575001205 has a single incoming connection (closing it strands a 63-
   edge pocket, 0.9%).

### C. Demand generation (who drives where)
7. **Endpoint fields are proxies**: SCB DeSO population (2023) for homes,
   OSM buildings/POIs for activities (official buildings file absent —
   OSM fallback in use).  POI density ≠ trip attraction; no local trip-
   generation rates exist.
8. **Behavioural constants are regional survey values, not Gothenburg
   measurements**: purpose shares (RVU Västra Götaland: arbete 0.53 /
   service 0.30 / fritid 0.17 weekday, hourly-modulated), purpose length
   scales, gravity deterrence (Tanner, gravity_km 1.8, α 1.5), tour
   pairing AM/PM structure.  θ was frozen after GEH saturation — sensor
   counts cannot identify these parameters (that is WHY they were frozen),
   so they are priors in the strict sense.
9. **Through traffic is prior-anchored, not measured locally**:
   `through_share_target=0.25` is now the calibrated default, while
   `through_fraction=0.5` controls only candidate-pool supply. Gate weights
   come from the gravity/Dial assignment field and verified via-pairs use
   the 45 s/20% bounded-detour rule. No cordon count exists to check the
   level directly (external data request 2 — the single highest-value
   missing measurement).
   RESEARCHED 2026-07-17 (external evidence survey): no measured
   through-share exists for Gothenburg's inner city.  The comparable
   MEASURED values found are all far lower: Potsdam's 2016 licence-plate
   cordon survey found 14% through traffic at the whole-city boundary and
   ~9% on the inner-city Havel bridges; Schwabach's transport plan
   measured 16–29% through on its main entry roads.  The only Nordic
   number found near ours — "70–80% of inner-ring traffic is through" for
   Uppsala — is a cycling-advocacy ESTIMATE with no cited measurement
   (verified by reading the source).  Two caveats kept the former 70%
   rush-hour output from being plainly refuted: (a) cordon geometry — our
   canvas is a small central
   box that deliberately contains the big approach roads as through
   gates, which raises the true through share relative to a whole-city
   cordon; (b) population — the displayed percentage describes the
   SENSOR-EXPLAINED calibrated population (every simulated vehicle must
   cross a sensor, and the sensors sit on through corridors), not all
   real traffic in the area; short internal trips that never touch a
   sensor are deliberately absent and their real-world share would dilute
   the through percentage.  Conclusion at that stage: the former 70% was
   plausible for this cordon and population but above every measured
   reference found, so it could not remain an unqualified emergent output.
   Only a local cordon/licence-plate count can settle the real level. UI
   FIXED the same day: the
   category is geographic (origin AND destination outside the canvas —
   a commuter driving through counts here), so the label "genomfart" was
   renamed "passerar området" with a tooltip defining every category and
   the population caveat (Gustav's 07:54 screenshot question — "why so
   little arbete at 8?" — was largely this labelling).
   IDENTIFIABILITY MEASURED 2026-07-17 (Gustav challenged the 70% —
   correctly noting the Uppsala figure reflects E4/Stockholm-corridor
   geography and is an unmeasured estimate, hence no anchor).  Same
   historical day built twice, through_fraction 0.5 (deployed) vs 0.3:
   GEH<5 stayed 100.0% on all three variants with 0 infeasible intervals
   in BOTH — and the whole-day calibrated through share was essentially
   UNCHANGED: 59% (prior 0.5) vs 60% (prior 0.3).  Three conclusions:
   (a) the sensor fit is completely indifferent to the through share —
   it can never be presented as a data result; (b) the share is not even
   set by the prior knob — it is an EMERGENT property of the pipeline
   (pre-verified through routes survive filtering at a higher rate than
   rejection-sampled tours, and the PFE amplifies the survivor pool's
   through share ~2× because through routes are its most flexible way to
   close sensor bands), so tuning through_fraction is NOT a lever for
   the displayed number; (c) the 70–75% figures seen in the UI are
   PER-QUARTER shares during commute hours — the whole-day share is
   ~59% — so part of the perceived excess was rush-hour composition of
   the display.  Whole-day ~59% against the measured references above
   (9–29% at other cities' cordons, geometry caveats apply): still
   plausibly high, genuinely unknowable from internal data; the
   cordon/licence-plate count is the only evidence that can move this
   number, in either direction.
   EVIDENCE LADDER (surveyed 2026-07-17, after a second literature pass
   found no further measured city-centre through shares — Oslo/Bergen/
   Trondheim publish only whole-urban-area figures, German VEPs keep the
   percentages inside non-indexed PDFs):
   (i) SELF-SERVE, highest value: Trafikverket's vägtrafikflödeskartan /
   Lastkajen / open API carries MEASURED flows on the state roads at our
   canvas boundary (E6, E20, Rv40, Oscarsleden — exactly the through
   gates).  Adding 3–5 boundary stations to data_in/sensors.json pins
   gate in/outflows; conservation (entered = terminated + exited)
   combined with the internal stations then makes the through/internal
   split PARTIALLY IDENTIFIABLE for the first time, using the product's
   own every-new-sensor mechanism.  No permissions needed.
   (ii) FREE ASK via Miroslaw: an OD extract over our cordon from the
   city/regional VISUM–Sampers model — modelled, but independently
   calibrated; would give a defensible through share quickly.
   (iii) PAID: mobile-network OD data (e.g. Telia Crowd Insights, which
   Swedish cities routinely buy) — direct through-share measurement.
   (iv) GOLD STANDARD: ANPR/Bluetooth cordon survey (the existing
   external data request).
   THROUGH-SHARE SWEEP JUDGED BY HELD-OUT SENSORS (2026-07-17 late
   evening, Gustav's proposal: borrow other cities' levels but let our
   own validation decide): the purpose-margin machinery ENFORCED through
   shares 0.25/0.35/0.45 (achieved exactly in every fold, verified from
   the agents sidecars) and full LOSO ran per level on 2025-09-16.
   Result — monotone, and the literature-anchored low end WINS:
     level    median  geo-err  mean GEH<5   ratios
     0.59 ref  1.71    1.82x     37.5%   0.55…2.73
     θ=0.25    1.00    1.57x     54.2%   0.70…2.51
     θ=0.35    1.18    1.62x     50.0%
     θ=0.45    1.67    1.93x     41.7%
   The cluster-corridor over-prediction largely WAS the through excess
   (107's first edge: 1.71 → 1.00 at θ=0.25).  Honest caveats: (a) this
   selects one knob from three candidates USING the validation set —
   mild tuning, disclosed, and the value must be CONFIRMED on the second
   day (2025-09-17 spec d2eb4e00b7d8be1c) before becoming default;
   (b) 2276 stays over-predicted (~2.4-2.5) at every level — its error
   is NOT through-driven and remains open; (c) 0.25 sits at the low end
   of the measured external range (9-29% + geometry uplift), so pushing
   below it would leave the prior-defensible band and overfit LOSO.
   CONFIRMED ON THE SECOND DAY (2026-07-18, 2025-09-17 historical,
   spec d2eb4e00b7d8be1c — a day never used for selection). The original
   sweep, before validation applied the deployed calendar activity margin,
   reported baseline median 1.63 / geo-err 1.79x / mean GEH<5 43.4% →
   θ=0.25 median 1.04 / 1.53x / 53.6%. REVIEW-CORRECTED exact
   validated-equals-shipped rerun (calendar activity margin + θ=0.25):
   median **1.111**, geo-err **1.545x**, mean GEH<5 **51.2%**, ratios
   0.647–2.515. The precise figures move, but the held-out improvement
   over the old mix survives on the untouched day; 134/2276 remain the
   non-through-driven residual. VERDICT: θ=0.25 is
   validated as "prior-anchored (measured external range) +
   held-out-selected + second-day-confirmed" and is now the default via
   `--through-share-target` (proper flag, provenance in demand_meta,
   disclosed in the UI tooltip) — NEVER described as a measured
   Gothenburg value; the cordon count remains the only decisive evidence.
   DEPLOYED/RE-VERIFIED 2026-07-18 on 2025-09-16: achieved whole-day share
   25.04% (5,422/21,656 agents), GEH<5 100% on all three direction
   variants with 0 infeasible intervals. Leakage-free LOSO under the exact
   shipped activity margin + through target gives median ratio 0.994; four
   of seven directed edges are 0.763–0.994, while the three documented
   residual over-predictions remain 2.111, 2.413 and 2.576.
10. **The 45 s / 20% detour-naturalness constants** are literature-
    plausible and LOSO-supported in *direction* (isolated station 0.05 →
    0.55) but not independently calibrated; LOSO 2026-07-18 shows the
    through target corrected the median to 0.994 but left three
    OVER-predicted cluster-corridor edges (2.111–2.576) — the next tuning
    lever, best
    constrained by tightening the assignment-field ceiling rather than
    refitting the constants against the validation set.
11. **The assignment field's scale fit is weak by construction**: robust
    median ratio on 6 measured edges, R² ≈ −5 (documented "informational
    only").  It is used as a weak ceiling (w=0.15) and for gate draw
    density, not as a load claim — but both uses inherit its shape errors.
12. **Candidate routing is free-flow by default** (congestion_iterations
    defaults to 1 = no feedback round): route choice ignores congestion
    unless a feedback build is requested.  Meso baseline delivery 0.87–
    0.96 suggests acceptable at current volumes; wrong in principle at
    saturation.
13. **Finite support pool** (~10 k candidates → ~6 k route×purpose
    variables): the PFE can only weight offered geometry.  Worst single
    shape still carries ~110–130 veh/day; convoy share ≥10 clones ~1–5%.

### D. Calibration (PFE)
14. **Conservation bounds between sensors assume no unmeasured sources/
    sinks along the corridor segments** — no turning-fraction measurements
    exist; corridor coupling ratios are learned from the same 6 stations.
15. **Structure guards cap against the POOL's own shares** (2.5×/3×
    multipliers): they preserve the generator's seed structure, which is
    itself assumption C7–C9.  Caps are dropped counts-first when
    infeasible (disclosed per quarter, currently 0–2 quarters/variant mix
    relaxation).
16. **Route geometry is shared across the day**: one shape set serves all
    96 quarters (departures vary, geometry does not) — no within-day
    route-choice drift (e.g. rush-hour rat-running) beyond what distinct
    shapes already encode.
17. **100% GEH<5 is fit, not accuracy**: it holds AT the 7 constrained
    edges; the honest generalization number is the LOSO factor (1.56×
    geometric absolute error, median delivery ratio 0.994) and
    it is displayed as the confidence map, never as citywide accuracy.

### E. Simulation (SUMO meso)
18. **Mesoscopic queue model**: no car-following, no lane-changing, no
    actuated signals; validated for 15-min edge flows (delivery 0.87–0.96
    vs micro 0.83–0.94), NOT for queue lengths, spillback geometry or
    travel-time distributions.  Micro exists behind --micro for windows
    that need it; queue-based closure advice carries the
    queue_proxy_unmeasured fail-closed gate for exactly this reason.
19. **Three seeds over three direction variants** is a small Monte Carlo
    ensemble; per-edge confidence = spatial_prior × exp(−CV) is a
    heuristic combination, not a calibrated probability.
20. **No en-route rerouting in the baseline** (closure rerouters only,
    within 400 m): drivers never divert due to congestion alone.
21. **Closure behaviour rules are assumptions**: truncate-stranded (driver
    parks at last reachable edge), rerouting radius 400 m, dropped-only-
    if-first-edge.  Reasonable, argued, untestable without incident data
    (external request: closure-period counts).
22. **1-second exit-time resolution** makes ~4% of edge traversals
    (short edges) look instantaneous in the trajectory export — display
    artifact, not a dynamics error.

### F. Display and confidence
23. **Confidence is distance-only**: exp(−d²/2σ²), σ=127.5 m fitted from
    7 near-field LOSO points inside two clusters.  It ignores network
    topology (a parallel unconnected street 150 m away scores high),
    direction, and volume; beyond ~300 m everything is labelled
    extrapolation with near-zero confidence — honest but coarse.  The
    far field has NO validation points at all.
24. **The Simulering colour scale is a display transform** (conf^(1/8))
    of the true confidence so the gradient stays legible; tooltips carry
    the exact value.  Any reader of the map alone sees relative, not
    absolute, certainty.
25. **The animated vehicles are ONE representative run** (seed 1000, q50)
    while road colours/audits use the ensemble mean — labelled in the UI;
    an individual animated car is an illustration of aggregate 15-min
    flows, not a tracked real journey (candidate tours provide AM/PM
    structure as a prior; PFE calibrates aggregates independently).

The mitigation order stays as recorded in the Recommended Implementation
Order: cluster-corridor over-prediction damping (D/10), golden freeze,
then the external data package (cordon count first) — external evidence is
the only closure for C8–C9, D14 and E21.

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
  demand calibration/PFE, held-out confidence validation, SUMO
  metadata/runtime, disruption metrics, and run/release registries now each
  have one canonical implementation. Root compatibility imports and CLI
  wrappers contain no duplicate logic, and demand cache/build fingerprints
  hash the canonical package files.
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
   fail-closed staging gate. The isolated 2025-09-16→18 acceptance study now
   passes every computational gate and its real 192-quarter browser playback
   was accepted by Gustav. Exact 3-, 4- and 5-day studies also passed, and the
   continuous seven-day study proves every day separately (including day 6).
   The bounded browser product samples at most 10 000 real q50 vehicles per
   day; the simulation and confidence calculations still use every vehicle.
   `golden-2025-09-16-7day-v1` is now validated and active.
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
closure feasibility and closure-driven signal optimization. The continuous
one-through-seven-day publication contract is now release-backed; a separate
six-day calibration was intentionally omitted because day 6 already has
explicit input, output, boundary and health evidence inside the stricter
seven-day run.

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

6. **RESOLVED 2026-07-18 — the UI's 1–7 day normal-study contract is
   release-backed.** Exact 3-, 4- and 5-day builds passed the same per-day
   q50/q10/q90 gates as the active two-day case. The continuous seven-day
   build produced 672 quarters and separately passed every day for PFE input,
   raw hourly SUMO output, health and midnight accounting; all 445 144
   variant vehicles were inserted with zero teleports. Day 6 is therefore
   explicitly proved within the week without paying for a redundant separate
   six-day optimization. The deterministic trajectory export is bounded to
   10 000 real q50 vehicles per day (70 000 total, 51.8 MB), while all
   vehicles still contribute to simulation flows and confidence. The staged
   publisher, HTTP API, validation report, integrity checks and real rollback
   exercise passed, and `golden-2025-09-16-7day-v1` is active.

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
| P1 | Make multi-day studies continuous and calendar-correct | Active seven-day golden release passes per-day PFE/output/health, boundary, bounded real-vehicle trajectory and publication gates; exact 3/4/5-day builds pass and day 6 is explicitly covered inside the week |
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

The trusted units are now one complete local calendar day and continuous
ranges through seven days. A multi-day study must not be implemented by
concatenating independent daily outputs or by silently resetting the network
at midnight. The active 2025-09-16→23 golden release proves that contract for
the maximum supported range; exact 3-, 4- and 5-day builds also passed, while
day 6 is explicitly gated inside the seven-day study.

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

**Acceptance gate:** the frozen continuous normal case has monotonic time,
correct local calendar labels, per-day q10/q50/q90 input and final-output fit
plus health, explicit midnight carry-over accounting, no missing interval
silently read as zero, bounded real-vehicle browser trajectories, and a
range-level manifest. The active seven-day release satisfies this gate.

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
6. Multi-day golden gate, then full UI release                (size M–L) ✓ seven-day release active; exact 3/4/5
                                                                          ranges pass and day 6 is gated inside week
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
- SUPERSEDED 2026-07-18 — `runs/releases/` was empty at this checkpoint.
  `golden-2025-09-16-v1` is now validated and active as described in the
  dated status entry below.
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
5. DONE — normal, closure and bounded micro-signal cases are frozen under the
   active golden release, including browser/API, memory, full-suite and
   rollback evidence.

### Simulation realism pass (2026-07-17): findings and fixes

Triggered by Gustav's report that the simulation/trips "does not look
right".  Three root causes found and fixed, one follow-up designed and
partially delivered.  Deployment spec throughout: 2027-09-15 forecast
(`5699948becd95c03`).

1. FIXED — **Driven lengths were distorted by netconvert junction cutting.**
   Where two OSM ways run parallel out of one node, netconvert's junction
   hull swallowed the street: 319 of 7 125 edges were >30% off, worst case
   an 88 m street simulated as a **0.20 m lane** (traversed instantly; the
   browser showed 426 km/h), and every edge was systematically shortened
   (meso has no internal-link distance).  Fix: `build_sumo_net.py` writes
   the OSM `length` explicitly on every plain-XML edge (netconvert honours
   it regardless of lane cutting; verified), the network audit records
   `graph_length_m`/`sumo_length_m`/`length_ok` per edge, and the build
   **fails closed** on any distorted length.  netconvert warnings are no
   longer discarded (`--no-warnings` removed; digest printed, full log in
   `sumo/netconvert_warnings.log` — the suppression had hidden this class
   of problem).  After rebuild: 0 mismatches; middle-edge traversals over
   130 km/h fell 2.07% → **0.000%**; instant (same-second) edge exits fell
   18.6% → 4.3% (the rest are genuinely short edges at SUMO's 1 s output
   resolution); trip durations essentially unchanged (p50 280 → 278 s);
   GEH<5 stayed 100% on all three variants with 0 infeasible intervals.

2. FIXED — **Candidate draw density ignored expected approach flow, so
   calibration stacked convoys of identical trips.**  Measured on the
   post-length-fix build: 776 distinct shapes carried all 17 983 vehicles;
   ONE shape (Boråsleden → via sensors 1074+1076 → Eklandagatan) carried
   1 486 veh/day, with 42 clones in a single quarter — visible in the
   browser as trains of identical vehicles.  Root cause chain: (a)
   `gate_weights()` was road-class-only, giving the busiest approach
   (Boråsleden, structural load 14 244 veh/day — the field's largest) just
   0.37% of candidate draws, so its main corridor had ONE pool shape; (b)
   E-E through pairs were drawn UNIFORMLY from the verified pair lists,
   ignoring approach importance entirely; (c) the PFE's origin-edge 3× caps
   were correctly violated-and-dropped by the counts-first fallback
   (19-21% of quarter flow from one origin vs its 1.1% cap) because the
   pool offered no alternatives — the guard's own design when feasibility
   demands it.  Fix: tour ANCHOR gate draws now follow the gravity/Dial
   structural assignment field (`sumo/assignment_priors.json`; weights
   normalised so the measured-data scale factor cancels — LOSO-safe;
   road-class fallback when the field is absent), wired through
   `build_sumo_demand.py` (cache-fingerprinted) with unit tests.
   MEASURED NEGATIVE RESULT, kept for the record: extending the same
   weighting to E-E through PAIRS (probability ∝ product of the two
   gates' weights) was implemented, built and measured — it made the
   worst-shape concentration (636 → 1 465 veh/day) and structure drift
   (18.5 → 25.2% near-sensor destinations) WORSE, and was reverted.
   Reason: the candidate pool is a SUPPORT SET for the PFE, which
   reweights freely — pool value is distinct-pair coverage, which the
   uniform draw maximises; anchor draws differ because rejection sampling
   makes their density decide which corridors exist in the pool at all.

3. FIXED — **Two web-app defects that made the simulation view lie.**
   (a) Colour semantics: non-sensor edges fell back to "count / own max",
   so EVERY street reached full red at its own peak — at rush hour the
   whole city lit up alarm-red next to visibly sparse vehicles.  Scenario
   providers now expose a per-edge calm midday (10:00-15:00) mean from
   their own flows; the renderer uses it with the same "vs calm daytime"
   semantics as sensor edges, with an absolute floor so a street under
   ~20 veh/15 min can never show alarm red.  Legend text updated.
   (b) `?mode=scenario` deep links switched the provider underneath the
   new workspace landing page without dismissing it; they now route
   through `openWorkspace()`.

4. FIXED (superseding the "signature-conditioned densification" follow-up
   drafted earlier the same day) — **Exact-shortest-path naturalness was
   the real root cause of endpoint inaccuracy** (Gustav: some areas get no
   trip starts, streets on/near sensors get far too many).  Every
   naturalness check (`via_is_natural_in_cost_matrix`,
   `natural_far_end_weights`, `natural_sensor_masks`,
   `natural_origin_weights`) required the sensor to lie on the EXACT
   shortest path (±0.5 s).  Measured on the real network: 6 of 7 sensor
   edges had ZERO verified through gate pairs (city-wide union: 2 pairs —
   ALL 6 000 through candidates, 76% of calibrated traffic, entered at 2
   street cuts and exited at 1), and tour destination masks admitted only
   the shadow cone immediately behind each sensor (destinations 100-200 m
   from a sensor: 18.0% of trips vs 1.2% of edges).  Real route choice is
   stochastic-multipath — the same finding assignment_priors.py already
   validated.  Fix: bounded-detour naturalness, `via − direct ≤ max(45 s,
   0.20 × direct)` (constants `VIA_DETOUR_ABS_S`/`VIA_DETOUR_FRAC`),
   admitting 18-58 pairs per sensor (union 265); the exact ±0.5 s rule
   stays only in `shortest_paths_use_node`, whose U-turn-guard purpose
   genuinely needs it.  Measured after full rebuild (same spec, GEH<5
   100%/0 infeasible on all variants, 974 tests green):
   - dests within 200 m of a sensor **18.5→2.8%** (baseline 1.9) — gone;
   - onward-after-sensor median 1 115→**2 904 m**, under-200 m 14.6→1.4%;
   - trip-length L1 vs RVU 0.69→**0.286**; structure gate **pass, zero
     flags** (first genuine pass on the length-corrected network);
   - through pool 2 origins/1 dest → **28 origins/19 dests** (every
     entry gate used); OD matrix now spans all 8 compass sectors;
   - worst shape 636→**111 veh/day**, ≥10-clone convoys 13.9→**0.9%**,
     distinct shapes 813→1 238, distinct trip origin edges 234→290;
   - purpose-incompatible through routes ~9 000→**~3 250** per variant
     (then to **0** by item 5 below — the purposes P0 is closed);
   - vehicles 17 097→21 338: single-sensor passages replace artificial
     multi-sensor chains (3+ passages 1 910→649), so the same counts
     need more, more-local vehicles — expected and more realistic.

5. CLOSED the same day — **purpose-stratified PFE** (implemented in the
   parallel session; pfe.py/demand/calibration.py/validate_sim.py): PFE
   variables are now (geometry × purpose provenance), the solver enforces
   each quarter's generated purpose mix as required groups in a two-stage
   solve (counts first, then the exact margin; counts-first fallback with
   honestly-reported mix deviation instead of relabelling), strict
   provenance allocation raises rather than fabricating a label, all
   variants stage-then-flip atomically, and the LOSO fold path uses the
   identical formulation.  Also fixed: sub-day windows read the correct
   purpose-mix clock (a 06:00 build no longer inherits the midnight mix).
   Verified on the combined rebuild (2027-09-15 forecast spec):
   **validation.json PASS overall with zero warnings — every section
   green for the first time** — purpose_incompatible 0/0/0,
   mix_relaxed 0/0/0, purpose_claims_allowed true, GEH<5 100% on all
   variants, 0 infeasible, 984 tests passing.  Final trip realism:
   1 492 distinct shapes, worst 127 veh/day, no shape ≥200/day, shapes
   ≥50/day carry 14.0% of vehicles (was 53%).  One disclosed relaxation:
   q90 quarter 85 relaxed 1 structural bound edge-quarter (sensor
   constraints retained).

6. VERIFIED + HARDENED 2026-07-18 on the historical reference day
   (`2025-09-16`) — the remaining purpose warning was not a candidate-support
   failure: every affected quarter had a feasible exact integer margin, but
   the local MILP abandoned it from a poor directly-rounded starting vector.
   Publication now retries exact provenance after count/bound repair and
   again after the optional structure repair supplies its final warm start.
   Result on the unchanged 9 424-candidate pool: q50/q10/q90 all remain
   **100% GEH<5, 0 infeasible, 0 purpose-incompatible, 0 mix-relaxed and 0
   structural flags**; `validation.json` is PASS with no warnings or missing
   sections.  The normal and Skånegatan reference-closure scenarios were
   rebuilt from that same demand release (all vehicles inserted, 0 teleports).
   That rebuild also exposed and fixed two scenario-publication regressions:
   legacy `--close` second offsets are now converted to bounded ISO
   `ClosureSpec` datetimes, and edges omitted by SUMO's
   `excludeEmpty="true"` edgeData are retained as measured zero specifically
   for closure-integrity evaluation.  Full suite: **1 011 passed, 20
   skipped**.

   Leakage-free LOSO was rerun under `loso_pfe_meso_v3`: held-out
   simulated/measured ratio min **0.76**, median **0.99**, max **2.58**.
   This is useful but not uniformly strong generalisation: edges 134 and
   2276 remain high at 2.41 and 2.58, and one direction of station 107 is
   2.11 while its opposite direction is 0.99.  Keep those outliers visible
   in confidence/reporting; a good median is not permission to claim every
   held-out road is accurate.

7. CLOSED 2026-07-18 — **the temporal holdout is now an executable,
   stale-safe release artifact**, not a manually quoted second-day run.
   `validate_sim.py --holdout-date YYYY-MM-DD` keeps the current candidate
   pool, network, assignment scale, structural priors and through-share
   contract frozen, moves the measurement window to a distinct same-day-type
   historical date, calibrates every fold only on the other stations, and
   reserves the held station's later-date values for evaluation.  It rejects
   same-day, cross-year, unlike day-type and <90%-coverage comparisons, writes
   atomically to `web/data/temporal_holdout_report.json`, and leaves the
   production demand/scenarios untouched.  `validation_report.py` includes
   the evidence only when candidate/network hashes, source, reference window
   and through-share target still match the current release; stale evidence
   becomes explicitly missing.  Reproducible command: `make
   validate-temporal`.

   Frozen 2025-09-16 release evaluated on independent 2025-09-17:
   min **0.757**, median **0.881**, max **2.536**, with 95–96 observed
   quarters on every directed sensor edge.  The same residual pattern
   survives across dates: 134/2276 remain high at 2.511/2.536 and one
   direction of 107 is 1.965, while the other four edges are 0.757–0.881.
   This is temporal stability evidence, not a claim that every corridor is
   accurate.

8. CLOSED 2026-07-18 — **the first golden release is validated and active.**
   `golden-2025-09-16-v1` contains 22 integrity-checked
   artifacts: the normal and Skånegatan scenario/trajectory pairs, all three
   q10/q50/q90 normal and closure route files, producer manifests,
   demand/network/sensor/validation evidence, and a fresh bounded microscopic
   signal smoke. Release schema v2 stores each case in its own directory so
   same-named manifests cannot collide, and activation now fails closed unless
   the full suite, browser/API smoke, peak-memory measurement, and rollback
   exercise are all explicitly passing. Golden rollback revalidates the
   predecessor's complete bundle and gates before flipping the pointer, so a
   damaged former release cannot be restored merely because it was once active.

   Final gate: **1 026 passed, 20 skipped**; release integrity has zero
   errors; the local API serves the expected 2025-09-16 demand signature,
   two-scenario manifest, clean closure integrity and PASS validation report.
   Isolated reruns are semantically identical to the frozen normal, closure and
   signal artifacts, with byte-identical representative trajectories. Peak
   RSS: normal **357 040 128 B**, closure **353 927 168 B**, bounded micro
   signal **222 052 352 B**. Manual Chrome validation loaded the real Scenario
   workspace and exposed one red console error: Leaflet's source-map request
   was blocked by the CSP. `serve.py` now narrowly permits the already-trusted
   pinned `unpkg.com` origin in `connect-src`; the security regression test
   passes and Gustav confirmed a clean Console after reload.

   The real registry pointer was exercised, not only a temporary unit-test
   root: ordinary bootstrap A→B→A first established the initial predecessor,
   then two complete golden clones were activated A→B and
   `rollback_golden_release()` restored A after revalidating its complete
   bundle/gates. The one-day release was then activated and later became the
   validated rollback predecessor of the two-day release described below.

9. CLOSED 2026-07-18 — **the continuous two-day golden release is validated
   and active.** An isolated historical build for
   2025-09-16→18 produced 192 monotonically ordered quarters. Every q50/q10/
   q90 variant passed **166/166 hourly sensor checks on each day** with zero
   infeasible intervals. The three continuous SUMO seeds inserted
   43 857/43 857, 42 548/42 548 and 45 224/45 224 vehicles with zero
   teleports. Final raw edgeData passed 168/168 directed sensor-hours per day
   and 144/144 physical station-hours per day; worst GEH was **1.320** on day
   1 and **2.985** on day 2.

   The first attempted publication was correctly rejected twice and exposed
   two contract bugs instead of hiding them. First, 15-minute GEH treated
   ordinary route travel across an adjacent quarter as lost demand
   (sensor 133, day 2 21:30: target 0, ensemble 12.67). PFE already uses the
   standard hourly GEH metric, so the final-output contract now retains every
   raw 15-minute value but declares and recomputes four-quarter/hour sums,
   independently per date. All 336 directed and 288 station hours pass; the
   worst hourly result is 2.985. Second, SUMO's `t=0` summary can already show
   one loaded vehicle. Day-1 deltas now use the mathematical zero baseline
   while retaining that observed snapshot for audit; the publisher
   independently revalidates the corrected cumulative counters.

   Midnight continuity is explicit: three q50 vehicles were pending insertion
   at the first boundary, 13 were actively driving across midnight, and all
   were accounted for on day 2. The 30.1 MB q50 trajectory product contains
   all 43 857 vehicles, no non-monotonic exit series, no exit before departure,
   and no unfinished vehicle. Against independently built one-day references,
   all **336/336** continuous-vs-reference directed sensor-hours pass GEH<5
   (worst 0.868/1.723), frozen targets are identical, daily q50 vehicle totals
   differ by only +0.466%/+0.432%, and citywide daily flow-vector cosine is
   0.9876/0.9848. Full regression: **1 034 passed, 20 skipped**.

   Gustav then exercised the staged 192-quarter result in the real browser at
   the midnight boundary and confirmed “de funkar”: the day label and clock,
   moving vehicles, scenario/validation panels, network requests and console
   showed no issue. The immutable 36-artifact release
   `golden-2025-09-16-2day-v1` passed integrity, API, browser, full-suite,
   peak-memory and rollback gates and was activated. `latest.json` now points
   to it with `golden-2025-09-16-v1` preserved as the validated rollback
   predecessor; both release validators return zero errors. It is retained as
   the rollback predecessor of the seven-day release below.

10. CLOSED 2026-07-18 — **normal studies through seven continuous days are
    validated and active.** Exact 3-, 4- and 5-day calibrations passed all
    daily q50/q10/q90 input gates. The final 2025-09-16→23 study produced 672
    quarters and independently passed all seven dates at 100% GEH<5 with zero
    infeasible intervals or hard-bound violations. Its three SUMO variants
    inserted 147 405/147 405, 143 704/143 704 and 154 035/154 035 vehicles,
    with zero teleports and zero unfinished vehicles at the end.

    Raw final SUMO output passed 100% of 1 152 directed sensor-hours and 984
    physical station-hours (range maximum GEH 2.000); each date separately
    passed 100%, including day 6 (maximum GEH 1.447). Every midnight boundary
    has fresh per-seed accounting with zero boundary lag and zero queue. A
    separate six-day calibration was intentionally not run: the user chose
    speed over a redundant prefix build, and day 6 is already tested as a
    first-class daily row inside the stricter week.

    Browser playback remains real-vehicle playback but is now bounded and
    deterministic: SHA256-lowest selection keeps at most 10 000 q50 vehicles
    per day, 70 000 of 147 405 for the week (51.8 MB, below the 96 MiB
    publication limit). All vehicles still drive in SUMO and contribute to
    flows and confidence; only the optional visual payload is sampled. The
    scenario took 96.56 s and peaked at 1 959 968 768 B RSS. Staging,
    validation-report and exact HTTP payload checks passed. The in-app browser
    was unavailable for a new visual run, so the visual basis remains
    Gustav's accepted continuous two-day boundary playback using the same UI
    contract; this limitation is recorded in the release evidence.

    Publishing the three independent q50/q10/q90 route variants in parallel
    reduced the exact three-day build from 1 085.90 s to 800.26 s (about 26%)
    while all six route/agent artifacts remained byte-identical. Full
    regression is **1 039 passed, 20 skipped**. Immutable release
    `golden-2025-09-16-7day-v1` passed integrity and rollback, was activated,
    rolled back to `golden-2025-09-16-2day-v1`, then reactivated; both
    validators return zero errors.

Honest status note: the 45 s / 20% detour constants are
literature-plausible route-choice bounds, chosen from the measured
admission curve (10%→157, 20%→265, 30%→464 pairs); they are assumptions
and the LOSO + temporal evidence above still covers only seven directed
near-field edges in two clusters.  It confirms a repeatable residual pattern,
not every road, weekend/holiday transfer, or citywide accuracy.

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
