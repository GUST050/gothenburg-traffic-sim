# Gothenburg Traffic Simulation Improvement Plan

**Date:** 2026-07-18 (consolidated 2026-07-15; status re-verified against the
working tree 2026-07-18)
**Status:** Canonical improvement plan — active implementation is in progress.
**Structural authority:** `ARCHITECTURE.md` remains the source of truth for
the six-stage pipeline and fixed contracts. This is the only improvement,
review, performance, simulation, closure, signal and sensor-growth plan.
Historical Sol/Luna task names and exact-approval wording below describe the
process used at the time; current collaboration follows the flexible,
model-independent protocol in `AGENTS.md`.

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
3. **Implemented (verified 2026-07-20):** purpose-compatible route
   allocation is complete and `purpose_claims_allowed` is TRUE on the
   active release.  This entry previously described the pre-fix state and
   was stale: the fix landed with the 2026-07-17 realism pass (`46719f4`,
   hardened in `ce7112d`) and is exactly the formulation this plan asked
   for — compatibility BEFORE publication, not relabelling after
   calibration.  `prepare_calibration` keys the LP/IPF variable pool on
   `(geometry, provenance purpose)`, so a geometry occurring in several
   generated purpose classes becomes several variables and the selected
   route's purpose is an INVARIANT rather than a post-hoc label; the
   production path then uses `allocate_strict_interval_provenance`, which
   raises rather than substituting when a shape has mixed purposes or no
   matching-purpose source.  When an exact purpose margin is infeasible
   beside the hard sensor counts it falls back counts-first and discloses
   `mix_deviation` instead of forcing the mix.  Measured on the active
   2025-09-16 release, all three variants: 0 incompatible quarters, 0
   replaced routes, 0 relaxed-mix quarters, RVU length ordering intact
   (through 5.90 km > fritid 2.98 > service 2.78 > arbete 2.51 km median).
   Through-traffic SHARE remains a sensitivity-tested prior until a cordon
   count identifies it (external data request 2) — that is a separate
   open question from route/purpose compatibility, which is closed.
4. Freeze and exercise a golden normal, closure and bounded micro-signal
   release, including rollback.
5. **Contribution slice implemented, but the chain is UNEXERCISED
   (checked 2026-08-04):** `sensor_contribution.py` emits evidence-bound
   before/after holdout, confidence, coverage and isolation reports plus a
   placement screen, and imports cleanly with `sensor_registry.py` against
   the current tree. A new station still needs a real before/after artifact
   before it can be called an improvement.
   However, a repository-wide search finds NO contribution or placement
   artifact anywhere — the tool has never been run for real. So the path
   "new sensor → validated registry record → rebuild → measured
   contribution report" is unproven end to end, and the first time it is
   tried should not be when a real station arrives with someone waiting.
   Cheapest proof that needs no new data: run the contribution report with
   an EXISTING sensor held out and put back, which forces the whole chain
   through. `data_in/sensors.json` currently holds exactly the six
   validated stations (107, 133, 134, 1074, 1076, 2276).
6. **Certificate implemented, and extended to the closure path
   (2026-07-20):** every signal-optimization condition gets a
   machine-readable TSFS-informed phase/link timing certificate, and
   `signal_closure_combine.py` (D4) now emits the same evidence for
   closure-driven arrivals — versioned `SignalPlan` artifacts for both the
   uncertified baseline reference and the optimized plan, plus a timing
   certificate for the optimized plan that ABORTS the study before
   publication if the TSFS-informed envelope is violated.  The result
   carries `signal_plan_id`/artifacts/certificates and `serve.py` surfaces
   them, so Phase 5's exit condition ("every timing result identifies its
   SignalPlan") now holds on both the normal and closure paths.
   **This item is CLOSED, not pending.**  Per the 2026-07-20 decision
   ("External Data Requests — CLOSED, no further data coming"), no city
   controller plan is coming, so signal results are `synthetic`
   PERMANENTLY.  Do not read the earlier phrasing ("until a city controller
   plan is imported") as a waiting dependency — external data request 1
   will never be sent.
   What DOES remain is EXECUTION, not construction.  As of 2026-08-04 the
   only signal artifact anywhere in the repository is
   `signal_golden_smoke_0000_0015.json`: a 15-minute window, 25 vehicles,
   one seed, `recommendation_allowed: false`.  D1–D6 are built and
   unit-tested (117 tests across the signal and sensor modules, all
   importing cleanly against the current tree) but have never been run as
   a real study.  The next step is to RUN `signal_closure_combine.py`
   against a real closure ScenarioSpec in a peak window — it already
   accepts `--scenario-spec` from the closure search, so it can optimise
   against exactly the rerouted arrivals of a chosen closure.
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
11. **Robust finalist decision slice implemented:** matched mesoscopic
    observations now retain candidate, q10/q50/q90 variant, seed, baseline
    and provenance identity. `finalist_decision.py` uses simultaneous 95%
    paired intervals, ranks the worst-variant upper bound, requests adaptive
    repetitions, removes hard failures before ranking, and structurally
    returns `unique_winner`, `tie`, `inconclusive`, or `no_viable`.
    Conditional two/three-finalist microscopic confirmation remains separate
    from the mesoscopic score and exposes unavailable or incomplete queue
    detail instead of guessing. The prior three-seed interval artifacts do
    not retain variant identity and are deliberately not accepted as robust
    finalist evidence.

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

The improvement demanded here — not relabelling trips after calibration, but
a purpose-stratified formulation making the selected route instance
compatible with purpose, departure time and length BEFORE publication — is
**implemented and its incompatibility diagnostic is zero** (see ranked item
3 for the mechanism and the measured per-variant evidence).  Purpose labels
on the active release therefore rest on compatible provenance rather than
being diagnostics only, and `purpose_claims_allowed` is true.

Two honest limits survive that fix and must not be conflated with it: a
compatible label states which generated behavioural class the route came
from, not verified individual trip intent; and through-traffic SHARE remains
a sensitivity-tested prior until a cordon count or local OD source
identifies it.

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

#### Speed research 2026-07-18 (measured budget + ranked result-neutral levers)

Gustav's requirement: faster, with results that are NOT allowed to get
worse.  Every candidate below therefore has an equivalence argument and a
proof protocol (byte/semantic-digest comparison against a sequential run);
anything without one was rejected.  Measured time budget on the dev
machine, current deployed 2025-09-16 build:

| Stage | Measured | Notes |
| --- | --- | --- |
| run_scenario (3 seeds, whole day, audits, trajectories) | **13.8 s** | NOT a bottleneck; vehroute parse 0.6 s, JSON writes ~0 s |
| Demand rebuild, cache-hit (PFE stage) | **~173 s** | solve 73 s (already parallel) + route publish 34.5 s + per-variant integer/purpose/report ~65 s (SEQUENTIAL) |
| Candidate generation, cache MISS only | 40–90 s | duarouter + generation; cache hits are ~0.1 s |
| Full LOSO (6 folds) | ~13–15 min | folds SEQUENTIAL; prepare_calibration recomputed identically per fold |

Ranked levers (largest first, all result-neutral by construction):

1. **Parallelize the three variants' post-solve stage** (integer repair +
   purpose allocation + route/agents publish in write_calibration_report):
   independent inputs and output files, no RNG in the path — expected
   −60–70 s of the 173 s on every recalibration.  Proof: byte-identical
   calibrated{,_v1,_v2}.rou.xml + .agents.json + fit reports vs sequential.
2. **LOSO: hoist prepare_calibration out of the fold loop** (the six folds
   rebuild the identical shape pool six times) **and run 2–3 folds
   concurrently** (fully independent read-only inputs, per-fold output
   files, report assembled in sorted station order).  Expected wall
   ~15 min → ~5–7 min.  Proof: identical loso_report.json.
3. **Cache-miss candidate path**: duarouter `--routing-threads N` (SUMO
   docs: per-vehicle routing, deterministic given seed/weights) and
   `--xml-validation never` on our self-generated XML.  −10–30 s, only on
   parameter/date changes.
4. serve.py recalibration inherits (1): ~6 min → ~4–4.5 min cache-hit.

Measured and REJECTED as not worth it: vehroute/JSON parsing optimizations
(0.6 s), further meso flags (--no-step-log/--no-warnings already set).
`--seed-workers >1` for run_scenario was ALSO listed here as "not worth it"
on the early single-day numbers; that rationale is SUPERSEDED — the v4–v6
campaigns measured it as a large, result-preserving speed-up (43.8% baseline,
40.8% closure) and it was rejected for a different, harder reason: the closure
whole-window arm still misses the 10-second gate. See "Seed-parallel campaign
line — measured and closed" in Phase 7 for the final decision.  FORBIDDEN by
the results-must-not-change constraint: numba fastmath, micro `--threads`
(nondeterministic ordering), any solver approximation or tolerance
loosening.  Protocol for every implementation:
tools/benchmark_speed.py before/after + semantic digest + one golden-case
rebuild comparison, per the P2 register row.

**Implementation 2026-07-21 (levers 1, 3, 4 — triggered by the first
multi-week closure-envelope builds).**  A real 11-day envelope build
measured the publish stage as the DOMINANT cost at that scale, far beyond
the 2026-07-18 single-day numbers: 154 min total, of which the three
variants' serial route publishing took 110 min (~37 min each) while the
parallel interval solve took 44 min.

- **Lever 1 done, memory-gated:** `run_pfe_variants_flat_parallel` now
  publishes the three variants through a fork pool sized by
  `_publish_worker_budget()` — one worker per parent-RSS-sized slice of
  60% of machine RAM, else the previously proven serial path.  The
  serial fallback exists because forked publishers can each hold a full
  copy of the parent's shape/solution state (the reason publishing was
  serialized in the first place); the gate makes that a measured
  condition instead of a permanent worst-case assumption.  Result
  identity is by construction (same worker function, disjoint staged
  files, same validate-then-flip publication gate) and covered by tests
  including a real fork-pool vs serial byte comparison.  Expected: big
  envelope builds ~154 min → ~85 min; serve.py recalibration inherits
  this automatically (lever 4).
- **Lever 3 done:** duarouter now runs with `--routing-threads` (≤8) and
  `--xml-validation never` on our self-generated trip XML.  Proven on
  real project data: route bodies byte-identical to a single-threaded
  run (only the header comment's timestamp/echoed options differ) at
  2.2x routing speed.
- **Lever 2 (LOSO hoist + concurrent folds) deliberately deferred:** it
  is not on the monthly-search critical path, and its required proof
  (identical loso_report.json before/after) cannot be run honestly while
  a closure-envelope search occupies the shared `sumo/` directory —
  LOSO would read a closure envelope's candidate pool and fight the
  search for cores.  Do it after the active search completes, with the
  live release restored.

Compatibility note: demand `build_key`s are content-addressed over the
DemandBuildSpec (never source code), and both changes are output-
identical, so archives built before/after this landing mix safely inside
one monthly release.  A search already running picks the fixes up from
its NEXT envelope build (each build is a fresh subprocess); the build in
flight at edit time finishes on the old serial path.

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

That rule remains the default and the only continuous-traffic claim. A separate
user-authorized planning approximation now exists under the explicit contract
`independent_daily_reset_v1`. It records the reset in search, schedule, backend
and result identity; keeps downloaded date-specific demand and the full
production recovery window for every daily unit; and may never be relabelled as
continuous evidence. Its purpose is long-range road-work timing where the user
accepts negligible cross-work-day carryover. Exact daily results are cached and
summed only by matched variant/seed identity. A work sequence advances through
consecutive eligible work dates, skipping deselected weekdays and blackouts;
the exact selected dates are part of the immutable schedule identity. Any
caller that omits the policy continues to receive the continuous behaviour
described below.

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

### Final design decision (frozen 2026-07-18)

Version 1 optimizes a **traffic closure schedule**, not construction
productivity and not permit compliance. Its precise promise is:

> Within the dates and daily hours allowed by the user, find the simulated
> full-road-closure schedule that has the smallest robust traffic impact.

The road is closed during one contiguous work interval per selected calendar
day, at exactly the same local clock time every day. It is open between work
shifts. The selected days are consecutive calendar dates; a disallowed
weekday or blackout date invalidates the whole candidate rather than being
silently skipped.

The duration input means **minimum total work time required**. Version 1 uses
the explicit assumption that one scheduled work minute requires one minute
of road closure: the road is closed for the complete work shift and opens
between shifts. Setup, teardown and pauses must be included by the user in
the required work time when they consume part of the shift. An optional
per-shift overhead/productivity model is deliberately deferred until there
is real construction evidence.

Version 1 supports:

- one connected worksite/corridor made from one or more directed SUMO edges;
- the same closure interval for every selected direction;
- forecast demand and a permitted date range;
- minimum total required work minutes;
- maximum consecutive closure days, initially 1--7;
- an earliest start and latest end daily band, including an overnight band;
- allowed weekdays and explicit blackout dates;
- one 15-minute-aligned contiguous interval per day;
- full motor-vehicle closure only.

It does not silently accept lane-only closures, access exceptions, disjoint
worksites, changing daily work hours, split shifts, non-consecutive workdays,
or effective-work-hour claims. Those require later explicit contracts and
validation.

### Canonical inputs and generated schedules

Create one immutable `ClosureSearchSpec` in `traffic_sim/core/`:

```text
search_id and content_key
directed_edges
source and demand_build_id
permitted_date_start, permitted_date_end
timezone = Europe/Stockholm
dst_policy = exclude_transition_dates
required_work_minutes
max_consecutive_start_days
permitted_daily_band
allowed_weekdays
blackout_dates
same_daily_window = true
resolution_minutes = 15
closure_type = full
duration_basis = required_work_time
work_to_closure_assumption = one_to_one
objective_profile
policy_status = user_supplied_unverified
```

For each possible day count `n`, the generator uses
`ceil(required_work_minutes / (15*n))` quarters per day. It enumerates
every feasible same-time daily window and records the resulting rounding
overshoot. A `ClosureSchedule` contains the exact dated intervals,
start/end clock time, day count, required and scheduled work minutes, actual
closed minutes, and overshoot. Under the version-1 one-to-one assumption,
scheduled work minutes and actual closed minutes are identical. Fewer shifts
and less rounding overshoot are tie-breakers only; they may never defeat a
meaningfully better traffic result.

An overnight interval occupies both calendar dates. The complete interval,
including its after-midnight part, must remain inside the permitted range and
must not touch a blackout or disallowed date. Until the forecast contract is
fully timezone-aware, Swedish daylight-saving transition dates are rejected
rather than interpreted ambiguously.

All selected edges must be connected in the underlying undirected road graph.
Repeated closures of the same edge are valid only when their intervals do
not overlap. `ScenarioSpec` validation must therefore change from "edge may
appear once" to "same edge may appear in multiple non-overlapping
intervals". Simultaneous selected edges are emitted together in each SUMO
rerouter interval; one interval is emitted for every workday.

### Simulation envelope: warm-up, continuous days, and recovery

Every finalist is one continuous multi-day simulation. Days must never be
simulated independently and added together, because vehicles and congestion
can carry into the next shift.

The simulation envelope is separate from the workday count:

1. warm up before the first closure using a duration derived from the
   baseline p95/p99 trip duration plus a safety margin;
2. measure from the first closure through all work shifts;
3. keep simulating after the final shift until the affected network has
   returned near its matched baseline for a sustained interval;
4. apply a bounded recovery cap; if traffic has not recovered at the cap,
   mark the candidate ineligible as `congestion_not_dissipated`.

This may require eight or nine calendar days for a seven-day closure. The
current 1--7-day demand contract must therefore be extended and
resource-tested before the UI can offer seven workdays. The engine must fail
closed instead of clipping the warm-up, overnight interval, or recovery tail.

A warmed baseline state may be cached once per unique demand/date block and
branched for candidates, including SUMO's random-number state. This
optimization is allowed only after an equivalence test proves that a
save/load branch produces the same decision metrics as an uninterrupted run.
The cache key includes all inputs, network and demand hashes, code
fingerprint, SUMO version and platform; a mismatch invalidates the cache.

### Two-stage monthly search

Running SUMO for every 15-minute start time across a month would be wasteful.
The search therefore has two explicitly different evidence levels.

**Stage A -- screening proxy**

Enumerate every legal schedule and rank it cheaply from the forecast demand,
the structural 96-slot assignment field, traffic on the closed edges, and
estimated load/reserve on plausible detour corridors. Missing values remain
missing and never become zero. The proxy produces a rank and screening
features, not invented seconds of delay and not the final recommendation.

The shortlist is stratified so adjacent times on one date cannot consume it:
include the best overall schedules, the best for every feasible day count,
distinct date blocks, and validation controls. Low spatial support, a missing
assignment prior, or out-of-domain road features automatically enlarge the
SUMO shortlist or withhold a recommendation.

Before release, validate the proxy out of sample on representative held-out
roads, road classes, sensor distances, topologies, durations and day types.
Freeze the validation set before tuning. Report:

- Spearman rank correlation as a diagnostic;
- recall of the exhaustive SUMO winner in the shortlist;
- shortlist regret: the difference between the exhaustive SUMO optimum and
  the best shortlisted SUMO candidate;
- failure/disqualification recall.

The initial gate is at least 90% winner recall, p90 normalized shortlist
regret at most 10%, and median Spearman at least 0.6. If it fails, increase
the shortlist or fall back to exhaustive SUMO for a bounded search; the UI
must not claim a global best from an unvalidated proxy.

**Stage B -- matched SUMO finalists**

Group finalists by demand/date envelope so their calibrated demand, matched
baseline and validated warm state can be reused. Begin with one heavy SUMO
worker. Enable parallel workers only after a resource benchmark proves a real
speed gain, semantic equivalence and safe peak memory.

### SUMO evidence and hard gates

Use citywide mesoscopic SUMO for screening finalists because this project has
validated it for 15-minute edge-flow studies, not for exact lane queues or
spillback geometry. The primary comparison is the paired change in total
SUMO `timeLoss` against the same no-closure baseline. Also retain validated
edge travel time, waiting time, entered/left counts, throughput, added
distance and affected/rerouted vehicles where available.

The current mesoscopic `halting`/network-wide queue value is diagnostic only.
It must never be a primary ranking objective or be presented as an exact
queue length. For the top two or three candidates, run bounded microscopic
confirmation when the worksite is near a signal, roundabout or known
bottleneck, when the halting diagnostic is high, or when the candidates are
too close to distinguish. If microscopic confirmation is not available,
state `queue_detail_not_assessed`.

Reject a candidate before ranking when any of these gates fails:

- no valid detour or unacceptable access loss;
- trips dropped in a way that can make low flow look artificially good;
- closed-edge leakage outside the declared tolerance;
- teleport, route-error, stranded, unfinished or simulation-health failure;
- closure interval mismatch, midnight clipping or stale demand/baseline;
- recovery not complete before the bounded drain cap;
- incompatible scenario, network, demand, variant or cache provenance.

Flow reduction is never evidence of success by itself.

### Uncertainty, repetitions, and the winner

The q10, q50 and q90 direction-demand variants represent different
epistemic demand assumptions. They are not independent random seeds and must
never be pooled as if they were.

For every variant:

1. run baseline and candidate with common matched seeds;
2. start with four random seeds;
3. use paired differences and add repetitions adaptively until the
   pre-registered 95% precision target is met or a maximum run cap is
   reached;
4. preserve the separate variant result and confidence interval.

The initial statistical tolerance is 5% at 95% confidence; an absolute
tolerance floor and maximum repetition count are frozen from the golden
benchmark before release. Multiple finalist comparisons use a Holm-adjusted
or simultaneous procedure, rather than repeated uncorrected pairwise tests.
At the repetition cap, an unresolved comparison is reported as inconclusive.

The frozen v1 primary objective is the smallest **worst-variant upper 95%
confidence bound of paired total time-loss increase**. It remains available
only for compatibility with evidence and policy identities produced under
that contract.

The provisional v2 primary objective is deterministic `closure_cost_v1`:
field-wise worst added vehicle-hours across q10/q50/q90, followed
lexicographically by added metres and affected vehicles. A vehicle with no
legal detour disqualifies the schedule. Pilot and finalist stages must use the
same explicit objective, and missing disruption evidence fails closed. Paired
time loss, throughput/access integrity and queue/halting remain health or
diagnostic evidence; they do not silently replace the v2 ranking key. An exact
vehicle-hour tie may use the secondary keys, while a declared nonzero
vehicle-hour equivalence band produces an honest practical tie.

**Rolling multi-month period comparison, 2026-08-10.** The work-period API/UI
now accepts a date interval spanning multiple months and compares rolling
periods of up to 90 permitted workdays. A period may cross ISO-week and month
boundaries; `rolling_period_v1` groups the compact response by candidate start
date but preserves the exact winning schedule, end date and workday count.
Rolling comparison always uses `exact_equal_daily_v1`: every selected workday
has the same start and end time, and period lengths that cannot divide the
requested work into equal 15-minute-aligned shifts are excluded. The balanced
policy remains available only to explicit non-rolling internal workflows. This
keeps allocation count bounded through 90 days; the older continuous model
retains its 21-day ceiling.

Every candidate is still evaluated by exact date-specific independent daily
SUMO units and the v2 closure-cost objective. This is provisional analysis,
not a new release claim: the golden-policy and untouched-heldout gates remain
closed.

**Scaling and validation implementation plan, 2026-08-10.** The next
implementation sequence is specified in
`docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`. It
freezes the measured 10,000-daily-unit limitation, adds an exact read-only
preflight, replaces full parent/unit materialization with versioned streaming
ledgers, moves deterministic disruption cost before SUMO, and proves a
cost-ordered exact pilot scan against the current exhaustive result before
activation. Persistent workers/libsumo are a later measured optimization;
demand/work-zone calibration and independent-reset validation are separate
evidence tracks. No existing policy, held-out record, or release claim is
changed by the plan.

**Objective-alignment benchmark checkpoint, 2026-08-10.** The pinned isolated
v1 diagnostic for policy v2 completed on the original immutable golden archive
in 318.18 s and resumed in 0.0 s with the identical result hash. Both pilot and
final records name the closure-cost methods, and q10/q50/q90 disruption
evidence is present. It is deliberately not a freeze benchmark: 06:00 and
06:15 were cheaper on vehicle-hours but failed the teleport hard gate, leaving
06:30 as the only viable schedule. The next benchmark must select cases by
pre-outcome structural criteria that yield multiple health-viable candidates;
only then can a nonzero equivalence tolerance be justified before the new
untouched held-out campaign is frozen.

### Confidence, policy, and wording

Do not compress confidence into a fake probability. The result exposes:

- forecast temporal error at sensors;
- LOSO/spatial support for the worksite and detours;
- proxy held-out coverage and regret;
- spread across q10/q50/q90 demand variants;
- paired seed confidence intervals;
- simulation and closure-integrity health.

Low spatial confidence or out-of-domain proxy use may still produce
exploratory results, but suppresses the strong `recommended` label.

The program says **trafficmässigt bäst inom angivna tider**. It does not say
that a schedule is permitted, safe for workers, compliant with a TA plan, or
acceptable for public transport, events, emergency access or construction
noise. Allowed weekdays and blackout dates are user-supplied and stored as
`policy_status = user_supplied_unverified`. Relevant warnings are shown, but
policy cannot become a hard automatic gate until authoritative machine-
readable inputs exist.

### Persistence, API, and UI

Each search writes an isolated immutable artifact under
`runs/closure-search/<search_id>/`, including the input spec, candidate
ledger, proxy evidence, exact simulated schedules, matched baselines,
statistics, health reports, hashes and final `DecisionResult`. It must never
reuse the old shared suggestion file or overwrite the active golden release.

The API follows the existing start/status/cancel job pattern. Progress stages
are enumerate, screen, build/cache demand, warm baseline, simulate finalists,
confirm, and publish. Cancellation removes only isolated scratch data; a
failed or cancelled search leaves the active release untouched.

The UI shows the exact interpretation before starting:

```text
Road closed 08:00--14:00 on 5 consecutive permitted days
Minimum work requested: 30 h; scheduled work/closure: 30 h
Screened schedules: N; SUMO-verified finalists: K
```

The result separates proxy-screened candidates from SUMO-simulated
candidates, shows every exact interval and each evidence component, and can
load only the exact winning/tied `ScenarioSpec` back into the forecast
simulation.

### Build order and stop gates

1. Implement pure contracts and calendar enumeration; stop until month-end,
   leap-day, overnight, blackout, weekday, rounding and DST tests pass.
2. Implement repeated SUMO intervals plus warm-up and adaptive drain; stop
   until continuous 2-, 7-, and extended-envelope resource tests pass.
3. Add isolated workspaces, baseline/warm-state caching and save/load
   equivalence; stop on any provenance or semantic mismatch.
4. Build and validate the monthly proxy out of sample; do not expose it in
   the UI until recall/regret gates pass.
5. Add paired robust finalist statistics and conditional microscopic
   confirmation; stop until tie, inconclusive and no-viable cases work.
6. Add asynchronous API, status, cancellation and restart recovery.
7. Add the forecast UI and exact-schedule handoff.
8. Freeze a golden monthly search, benchmark wall time/RSS/disk, run browser
   recovery tests, and publish only if every previous gate passes.

**Implementation status 2026-07-18:** Steps 1, 2, 3 and 5 are internally
complete. Step 5's synthetic stop cases cover unique winner, practical tie,
adaptive/inconclusive, repetition-cap, no-viable, incompatible provenance,
conditional micro confirmation and missing queue detail. Existing SUMO
evidence also reproduces a real `no_viable` case; old eligible evidence is
three-seed and variant-collapsed, so it correctly cannot be promoted into the
new decision contract. Step 4 is implemented, but its held-out release gate
failed; it is therefore complete as an evidence-producing stage and
deliberately not released to the UI. Consequently, Step 5's decision engine
is also internal only: no API/UI/global-best claim is permitted. The
absolute precision floor, practical-equivalence tolerance and repetition cap
remain required explicit policy inputs and must be frozen against the named
golden monthly benchmark before release rather than hidden in code defaults.

Step 1 added the canonical `ClosureSearchSpec`, `DailyTimeBand`,
`ClosureInterval` and `ClosureSchedule` contracts in
`traffic_sim/core/contracts.py`, plus the pure deterministic generator,
exact `ClosureSpec` expansion, DST exclusion and connected-worksite
validator in `traffic_sim/core/closure_calendar.py`.

Step 2 now lets one directed edge close in multiple non-overlapping
intervals, groups simultaneous multi-edge closures into one SUMO rerouter
interval, and proves with a real SUMO network that the edge reopens between
two work shifts. `traffic_sim/simulation/envelope.py` derives a continuous
full-day envelope from the baseline trip-duration p99 plus margin, rejects
DST transitions and envelopes beyond the validated nine-day ceiling, and
evaluates sustained post-closure recovery against the matched no-closure
per-edge `timeLoss` series. The ordinary recalibration contract remains
limited to seven days; only an isolated `purpose=closure_envelope` build can
represent days eight and nine, and the generic recalibration API rejects
that internal purpose.

The resource gate uses the existing frozen two- and seven-day evidence plus
`tools/benchmark_nine_day_envelope.py`. The two-day computational run passed
in 25.34 s at 623,591,424 bytes RSS; the frozen seven-day acceptance passed
in 96.56 s at 1,959,968,768 bytes RSS. The isolated nine-day SUMO 1.27.1
proof ran 190,730 vehicles in 19.345 s at 208,715,776 bytes RSS, with every
vehicle loaded and inserted, none waiting or running at drain completion,
and zero teleports. Days eight and nine deliberately repeat frozen q50 days
one and two, so this proves continuity and resources only, not new
calibration. The focused integration gate passes 210 tests and the complete
project suite passed 1,088 tests with 20 expected skips at the step-2 gate.

Step 3 adds exclusive `runs/closure-search/<search_id>` workspaces in
`traffic_sim/simulation/search_workspace.py`. The exact search input and
every published artifact are hashed and ledgered; search IDs cannot be
reused, path traversal and overwrites fail closed, unledgered/tampered files
block success, failure preserves only its own diagnostic scratch, and
cancellation removes only that isolated scratch without touching an active
release.

`traffic_sim/simulation/warm_state_cache.py` adds two immutable caches: one
for SUMO warm states and one for matched no-closure baseline metrics. The
warm-state key covers the network and route bytes, demand build and variant,
seed, mode, warm-up boundary, any baseline additional inputs, mandatory code
hashes, Git commit, Python, the full SUMO version, platform, RNG-state flag
and state precision. Baseline identity additionally covers its exact
analysis window, affected-edge set, objective profile and metric schema.
Missing, changed, corrupt or incompatible provenance is a cache miss; an
invalid existing entry cannot be silently overwritten.

Cache publication requires a save/load equivalence certificate for the
exact warm-state identity and the fixed
`closure_decision_metrics_v1` schema. The cache itself enforces the policy:
`entered`, `left`, primary `timeLoss` and every other field are exact
(absolute tolerance zero); only supporting per-edge `travel_time_s` may
differ by at most one default SUMO simulation step (1 s). A caller cannot
substitute a looser global tolerance or another metric schema.

MONTHLY PAIRED WARM-STATE VALIDATION (LUNA-WARM-01..04, 2026-07-28). The
monthly backend has an opt-in, default-OFF warm branch plus a frozen paired
cold-versus-warm harness. Its first real execution (LUNA-WARM-03) FAILED, and
usefully: `combine_prefix_and_post_warm` refused to merge `max_queue_vehicles`
(prefix 0, post-warm 5) because a network-wide MAXIMUM is neither additive nor
something two segments agree on. The aggregate prefix object was the wrong
contract — it also conflated boundary-active trips with finished observations
and carried no prefix recovery buckets.
It is replaced by VERSIONED PREFIX EVIDENCE (`monthly_prefix_evidence_v1`):
completed-only prefix trip aggregates, prefix queue maximum, prefix counters and
prefix recovery buckets, stored inside the warm-state entry's atomic
digest-bound member set and re-verified on every restore. Legacy
`prefix_metrics`-only entries are a cache MISS, never repaired. Every
`DisruptionMetrics` field crosses the boundary by an explicit rule bound
mechanically to `dataclasses.fields`, so adding a production field without
deciding its semantics fails a focused test: disjoint accumulators sum,
end-state and candidate-route-only fields come from the post-warm segment,
`max_queue_vehicles` takes the maximum over MEASURED segments, and closure
throughput is post-closure with a fail-closed pre-closure invariant. Recovery
buckets are concatenated into one ordered, gap-free domain and never
synthesised. The bootstrap requests completed-only tripinfo
(`run_sumo(tripinfo_write_unfinished=False)`, default True everywhere else) so a
vehicle still driving at the snapshot is counted once, by the resumed run.
LUNA-WARM-05 EXECUTED v1 AND IT FAILED (2026-07-29). The first real
cold-versus-warm comparison. The warm branch genuinely ran at the frozen warm
point, and baseline metrics, feasibility, hard failures, recovery, the
concatenated bucket domain, truncation and provenance all matched the cold arm
EXACTLY — which is what made the three real differences legible:
`loaded` 84065 vs 85146 and `inserted` 84065 vs 85130; `closed_edge_throughput`
0 vs None; `total_time_loss_s` 558026.99 vs 558019.26 (-7.73 s on 558k).
Coverage was 1 of 3, because `run_candidate` stops at the first hard failure and
q10 hit `truncated_unreachable_vehicles`, so q50/q90 never ran. Nothing was
published.
LUNA-WARM-06 corrected three mechanisms and froze v2:
(i) SUMO's `loaded`/`inserted`/`teleport_total`/per-reason counters are
CUMULATIVE across a loaded state, so reconstruction takes the post-state value
and treats the prefix as a lower bound instead of summing — summing
double-counted every vehicle live at the snapshot, which is exactly the
+1081/+1065 shape;
(ii) the post-warm invoker now measures active-closure throughput from its own
edgeData with closed edges zero-filled, so measured zero stays distinct from
never-looked;
(iii) the candidate route is now filtered BEFORE any state lookup, audited
against the original by vehicle id/departure/route, and the snapshot is chosen
strictly before the earliest changed or dropped departure — a prefix simulated
from the unfiltered route is invalid for any vehicle the filtering touches.
Measured on the real archive, this closure changes 23-38 vehicles per variant
with the earliest affected departure around 24 900 s, so the old 24300 point
happened to be route-safe: route mutation was NOT what broke LUNA-WARM-05, and
the audit is a guarantee rather than a retrofit for that specific failure.
The validation harness now requests every frozen identity directly from the
production observation path, so one disqualified variant no longer hides the
other two; ordinary search keeps its fail-fast ordering unchanged.
LUNA-WARM-07 EXECUTED v2 AND IT FAILED — narrowly, and informatively
(2026-07-29). Coverage was 3 of 3, execution evidence complete, and 16 of 18
semantic groups matched EXACTLY. One group differed: the objective,
`total_time_loss_s`, with warm LOWER on every identity — q10 -7.73 s, q50
-80.62 s, q90 -138.97 s — increasing with demand. Warm was also slower on this
case (98.4 s vs 85.7 s), so no speedup is claimed.
The monotone-in-demand ordering is the diagnosis. A vehicle still driving at
the snapshot is BOUNDARY-ACTIVE: completed-only prefix tripinfo excludes it,
and after `--load-state` its tripinfo reports only post-boundary time loss, so
its pre-boundary delay is counted NOWHERE. Denser demand strands more such
vehicles. Completed-only tripinfo (LUNA-WARM-04) had removed double-counting
and replaced it with under-counting; neither aggregate is sufficient, because
the right answer depends on WHICH vehicles were airborne.
LUNA-WARM-08 fixes the accounting and freezes v3:
(i) `warm_state_boundary.py`'s `WarmPrefixController` owns ONE SUMO process and
its TraCI connection, captures a PER-VEHICLE ledger at exactly the saved step
and saves the state through that SAME connection, so the two cannot disagree
about which vehicles were in flight; it refuses outright if the simulation did
not land on the warm point, and always reaps the process. `traci`, `subprocess`
and `socket` are imported lazily inside its methods, so constructing one starts
nothing and every check stays process-free. The validation harness supplies a
real controller to the warm arm by default — without that the campaign silently
fell back to cold and could not test what it was frozen to test;
(ii) `monthly_prefix_evidence_v2` carries that ledger plus the per-vehicle
completed-trip map, and the objective is RECONCILED by vehicle identity: each
boundary vehicle contributes its post-warm trip plus its ledger offset exactly
once. Segment values stay RAW and are normalised ONCE per final per-vehicle
total — rounding halves separately loses 0.01 s on a vehicle accruing 1.005 s
either side of the snapshot. Raw in memory is not enough: the post-warm half
round-trips through a file SUMO writes at its reported precision. That residual
has no precision-based fix — `--precision` is global and perturbs recovery and
waiting semantics, and no finite precision guarantees exactness (proven for
2..12 decimals). Warm argv stays byte-identical to cold argv and the residual is
DECLARED in the manifest: at most one unit in the last reported place per
boundary vehicle, which can make the campaign fail rather than pass quietly. The completed map must agree
with its aggregates (count, total, disjoint from the active set) on write AND
read. Every invalid-warm-evidence path records a reason and returns None so the
unchanged cold arm runs — the guard is at the consuming boundary, so it covers
failures nobody predicted. Missing, duplicate or overlapping identities, malformed tripinfo, and
legacy v1 evidence all fail closed; v1 evidence is a cache MISS, never repaired
or reinterpreted;
(iii) the boundary schema and tripinfo precision are bound into the warm
identity by content — precision is a runtime parameter, so binding module bytes
alone would not catch a change from 2 to 3 decimals, which would silently alter
every reconstructed objective;
(iv) split diagnostics expose only BOUNDED facts (count, digest, reconciliation
totals), never the per-vehicle map, which would make the canonical payload grow
with traffic.
Reconciliation exactness is tested as a PROPERTY, not an example: randomised
splits reproduce the uninterrupted total exactly, and the v2 failure mode is
reproduced (120.0, losing 30.0) and fixed (150.0).
TRACI DISCOVERY FIXED, v8 FROZEN 2026-07-31 (LUNA-WARM-14/15). The v6 campaign
named the cause of every fallback: `No module named 'traci'`. Production imported
TraCI bare; it ships inside the SUMO installation at `<sumo_home>/tools/traci`.
Warming had therefore never started — warm_executions was 0 in v4, v5 and v6.
`runtime.resolve_traci()` now imports from the exact active home and proves the
module's origin; the controller resolves before launching anything; and the
harness runs the same resolver as a mandatory preflight before any artifact root
is created, so an unusable environment cannot consume an approved campaign again.
A fake-SUMO-tree regression exercises the real import machinery — the check that
was missing for three campaigns. One audit-guarded import-only probe of the
installed package confirmed the origin and required API; it is consumed evidence
and proves nothing about warming.
v7 carried that repair but was REJECTED in process-free review for binding an
incomplete regression set (it omitted `tests/test_warm_state_boundary.py` and
`tests/test_monthly_warm_state.py`, which could then have been weakened without
invalidating its key). It was never approved or executed, cost no campaign, and
is preserved byte-for-byte rather than repaired in place. v8 supersedes it with
the same rules and the complete binding, enforced at freeze time.
SUPERSEDED 2026-08-02: v9 was approved and EXECUTED once (LUNA-WARM-16). Warming
ran for the first time and the comparison failed with residual -7.73/-80.62/
-138.97 s — bit-identical to v2's, refuting the state-serialization hypothesis.
LUNA-WARM-22 localized it to 5/10/12 vehicles in flight across the warm point,
whose deltas sum to the residual exactly; most return with 0.0 accumulated time
loss while 99.99% of the population is unchanged. v12 (LUNA-WARM-23/24) then
bound and executed a selective `saved - restored` TraCI correction. All three
warm arms completed, but their save/restore ledgers were equal and the exact
7.730000004/80.620000002/138.970000003-second cold-minus-warm residual remained.
That refutes the correction; no cache was published.

V13 (LUNA-WARM-25) is grounded in the SUMO mesoscopic implementation rather than
another behavioral guess. Meso tripinfo outputs private
`MSDevice_Tripinfo::myMesoTimeLoss`; device save/load omit it; and TraCI
documents `getTimeLoss()` as accumulated loss. Frozen SUMO 1.27 mesoscopic
save/load evidence nevertheless shows it does not exactly reproduce the private
tripinfo accumulator across this boundary. The prefix therefore captures
only the exact active ID set on the state-writing connection and lets SUMO emit
high-precision unfinished tripinfo at normal close. Prefix and resumed private
accumulators are joined per identity, then each whole vehicle is rounded once to
production precision. The active-population digest is reconstructed from the
warm point and exact ledger IDs on every read, rather than accepted as a
self-consistent string. Prefix XML completion order is retained separately from
canonical identity storage, and resumed records continue that accumulator so
floating-point grouping cannot manufacture a mismatch. Completed,
boundary-active and post-boundary populations must be disjoint and exhaustive;
malformed, duplicate, missing, overlapping or unknown pre-boundary records cause
a recorded cold fallback. Warm-only global
precision is normalized per edge before recovery aggregation so supporting
metrics retain cold semantics. The obsolete keep-after-arrival terminal ledger
is removed, avoiding its memory/runtime cost. Cache identity advances to schema
2 and legacy entries fail closed as misses.

V13 executed once but its sandbox denied the localhost IPv4/TCP bind before all
three warm prefixes. The three fallback payloads matched cold exactly, but zero
valid warm executions means they prove neither equivalence nor speedup; no cache
was published. V14 preserves the v13 mechanism and physical experiment and adds
a mandatory bind-capability check before keyed-root inspection/creation. It is
frozen, unapproved and unexecuted. Warming stays default-OFF; after review, the
shortest remaining path is fresh exact-key approval and one frozen execution
with escalated socket permission. Further mechanism work is justified only if
that socket-capable warm run produces a real semantic mismatch.

v4 FROZEN 2026-07-30 (LUNA-WARM-09), REPLACING v3's REFUTED DESIGN.
Preserved-accumulator accounting: the objective is the completed-prefix aggregate
plus the resumed aggregate, each vehicle counted once and whole, with no
per-vehicle boundary offset. Prefix evidence is `monthly_prefix_evidence_v3`
carrying bounded snapshot facts rather than a ledger; v1/v2 evidence are cache
misses. Because aggregates are whole values, v3's ±0.01 s serialization residual
does not arise at all.
The prefix snapshot command now carries exactly one `--save-state.rng true` and
one `--save-state.precision 16`, derived from the cache constants the identity
records — v3 recorded both and applied neither, which is the current hypothesis
for the residual. `WarmPrefixController` snapshots at the exact step through one
process and connection, requires an observed zero exit, and reaps without masking
errors.
STILL UNPROVEN, and recorded as such in the manifest: LUNA-WARM-07's
−7.73/−80.62/−138.97 s gap is UNEXPLAINED. The default-serialization hypothesis is
what a campaign would test, and v4 states the condition that refutes it. Warming
is default-OFF, v4 is unapproved and unexecuted, and the one remaining gate is a
single fresh approved paired campaign.

MEASURED 2026-07-30 (LUNA-WARM-08 revision 3), AND IT REFUTES THE v3 PREMISE.
One approved non-campaign SUMO/TraCI diagnostic
(`tools/diagnose_warm_state_time_loss_semantics.py`, outcome at
`validation/warm_state_time_loss_semantics_v2_outcome`, with cold/prefix/resumed
return codes all observed as 0) asked whether SUMO's saved state preserves a
vehicle's `timeLoss` accumulator. It does. The earlier revision-2 run produced
the same numbers but never checked its processes' exit codes, so it was rejected
and rerun under enforcement rather than reinterpreted. On the
controlled fixture the restored vehicle reports 15.72 s immediately after
`--load-state` against a boundary capture of 15.7184 s, and the resumed
tripinfo reports 109.90 s — identical to the uninterrupted run, field for field,
not the 94.18 s a post-boundary-only segment would give.
So a resumed vehicle's tripinfo ALREADY carries its whole trip's time loss, and
v3's per-vehicle ledger offset would double count the pre-boundary delay. Two
things follow. First, v3's reconciliation rests on the opposite assumption and
cannot be adopted as written; its artifacts are left untouched pending a new
evidence-based decision, and selecting the replacement is separate work.
Second, the
original LUNA-WARM-07 gap is now UNEXPLAINED again: if resumed tripinfo is
complete, completed-only prefix plus resumed already sums correctly, so the
monotone −7.73 / −80.62 / −138.97 s shortfall has some other cause. It was only
ever consistent with boundary-active accounting, never proven to be it.
Limits of the measurement: one synthetic vehicle, one edge, no interacting
traffic, one SUMO version and platform, one snapshot instant. It says nothing
about a vehicle mid-junction, mid-lane-change, teleporting, or queued at the
snapshot.

HONEST BOUNDARY: this is process-free work. Passing tests and a fresh freeze
prove the ACCOUNTING is exhaustive and fail-closed; they prove NOTHING about
whether warm and cold agree under real SUMO, or about any speedup. v3 states
its own refutation condition in advance: if the objective still differs after
reconciliation, boundary-active accounting is NOT the cause and the campaign
fails honestly. The v1 and v2 keys are spent, their roots are preserved failed
evidence, and a fresh paired campaign needs a new task, a new frozen key and
explicit user approval.

The real small-network closure test proves that a closure introduced after
the warm state produces the same decision metrics in an uninterrupted,
freshly loaded and cache-restored branch. The production-scale proof in
`tools/validate_warm_state_equivalence.py` uses the frozen Gothenburg
seven-day q50 release with SUMO 1.27.1: four 15-minute intervals, 7,125
edges, 4,806 entered, 4,793 left and total `timeLoss` 27.33 s were exact in
all branches. Four of 28,500 supporting travel-time values differed, with a
maximum 0.23 s and signed total 0.04 s, inside the explicit one-step limit;
the difference is disclosed rather than mislabeled as bit-identical. The
restored matched baseline was identical to the stored evidence. The focused
step-3 gate passes 113 tests and the complete project suite passes 1,115
tests with 20 expected skips.

Step 4 adds the reusable proxy, projection and validation modules under
`traffic_sim/simulation/`, with `screen_monthly_closures.py` as an internal
screening CLI and `run_monthly_proxy_validation.py` as the resumable,
isolated SUMO validation runner. The proxy reports separate ranks/features
for closed-edge vehicle exposure, existing detour utilization/reserve and
post-diversion utilization. It never emits invented delay seconds. Missing
closed-edge data or a missing evaluable detour makes a schedule unscoreable;
low spatial/structural/domain support enlarges the shortlist and withholds a
recommendation.

The 2027 projection is explicit about its evidence boundary. Agent 1 directly
forecasts only seven measured directed sensor edges. Every other requested
edge comes from the fixed 96-slot structural hierarchy
`learned_direction_prior → corridor_prior → assignment_prior`, scaled per
quarter by the median station-level change from the real 2025-09-16
reference. Two-way sensor 107 is direction-split once before aggregation;
each physical station receives equal scale influence. Fewer than three valid
stations leaves the structural projection `null`, never zero. A real
July-2027 smoke search ranked 272 legal schedules in 2.2 s, selected 54
stratified SUMO finalists, labelled all road-domain evidence unvalidated and
kept both UI/global-best flags false.

The held-out manifest
`validation/monthly_proxy_manifest.json` was frozen before SUMO outcomes
with content key
`b3c2416d7b9a5b8784a30f756a96d37c915f0f0bccb734701aa7fefb9d0d53c1`.
It contains 12 cases and 140 exact schedules spanning five road classes,
three sensor-distance bands, three topology classes, four work-duration
classes and weekday/weekend/holiday/mixed periods. Every outcome requires an
exhaustive schedule set, matched three-seed baseline, SUMO/network/demand/
proxy hashes and explicit disqualifications. Partial evidence can never open
the release gate.

Nine cases (81 schedules) could be run entirely from immutable archived
demand without touching the active release. The observed diagnostic metrics
were winner recall 0.6667, p90 normalized shortlist regret 0.08168, median
Spearman 0.50, Spearman case coverage 0.50 and failure/disqualification
recall 0.6726. Three demand envelopes remain unbuilt, but the gate is already
mathematically decided: even if all three became ranking cases and recalled
their winners, the optimistic upper bound would be
`(4 + 3) / (6 + 3) = 0.7778`, below the required 0.90. The current proxy
therefore has a conclusive `fail`; the UI was not changed and may not claim a
global best.

Any proxy-v2 tuning must treat these outcomes as development evidence and
freeze a new untouched held-out set before release. The safe alternative is
to enlarge the SUMO shortlist or use bounded exhaustive SUMO while continuing
to withhold a global recommendation. Re-running or relabelling the same
frozen cases as new out-of-sample evidence is forbidden. The focused step-4
gate passes 59 tests; the complete project suite passes 1,148 tests with 20
expected skips.

**Step-4 recovery decision, frozen 2026-07-19.**  The v1 failure is not a
reason to fit new rank weights to the nine observed cases.  Inspection found
two different problems that must not be conflated:

1. one real safety bug: a legal candidate whose structural detour flow could
   not be scored was omitted even when the normal bounded-exhaustive policy
   had room to run every schedule;
2. two apparent winner misses were differences of about 4 s and 22 s in
   whole-network aggregate time loss, while their three-seed ranges spanned
   hundreds of seconds.  Calling either noisy median a unique exact winner is
   false precision, not evidence for a new proxy weight.

The shortlist contract is therefore `stratified_shortlist_v2` while the
unchanged analytical ranks remain `monthly_proxy_v1`.  For at most 120 legal
schedules, every candidate is selected for SUMO, including candidates with
no proxy rank.  Above that bound, unscoreable candidates are explicit SUMO
controls whenever the 240-candidate cap permits; any omitted unscoreable
candidate structurally withholds a recommendation.  Missing evidence is
never interpreted as poor traffic performance.

The researched release path is a three-fidelity ranking-and-selection
procedure:

1. use the zero-cost analytical proxy only to order and stratify candidates;
2. use matched mesoscopic SUMO pilot runs for the broad shortlist, preserving
   q10/q50/q90 identities and common random seeds;
3. spend adaptive repetitions only on the surviving finalists and decide
   them with `finalist_decision.py`'s worst-variant simultaneous bound.

This follows SUMO's documented division of labour: mesoscopic simulation is
intended for large urban areas and is much faster than microscopic
simulation, while microscopic confirmation is reserved for local
lane/signal/bottleneck questions
([SUMO mesoscopic model](https://sumo.dlr.de/docs/Simulation/Meso.html)).
It also follows simulation ranking-and-selection practice: the smallest
operationally meaningful separation must be pre-registered as an
indifference zone, common random numbers reduce comparison variance, and
inferior alternatives should be eliminated sequentially rather than giving
every option the final replication budget
([INFORMS simulation optimization tutorial](https://pubsonline.informs.org/doi/pdf/10.1287/educ.2013.0118?download=true)).

This decision does **not** reopen the UI gate.  Before a global-best claim,
the pilot/finalist policy, practical-equivalence tolerance, precision floor
and repetition cap are frozen against a named golden monthly benchmark; a
new untouched held-out set then measures practical-winner recall, regret and
failure recall.  The old v1 cases remain development diagnostics only.

**Implementation checkpoint 2026-07-19.**  The shortlist-v2 safety contract,
the fail-closed matched pilot selector, explicit per-replication
q10/q50/q90+seed records and identity-based baseline/candidate pairing are
implemented.  Pilot output is structurally `screening_only` and cannot carry
a winner.  Missing pilot pairs return `incomplete`; too many contenders
inside the frozen retention band return `capacity_exceeded`; every hard-gated
candidate returns `no_viable`.  The real v1 failure case
`tertiary-far-weekday-4h` now selects all 9/9 legal schedules despite having
0/9 scoreable proxy schedules.  The focused recovery gate passes 117 tests
and the full suite passes 1,190 tests with 20 expected skips.  The golden
policy freeze and new held-out SUMO outcomes remain the next stop gate.

**Runnable decision checkpoint 2026-07-19.**  The existing active-demand
closure-time path now returns the same fail-closed robust result categories
instead of choosing the minimum of three noisy medians.  It runs a matched
three-seed q50/q10/q90 pilot, removes candidates that fail closure-integrity
or health gates, then evaluates retained finalists with 12 matched SUMO
runs (four per variant).  Only `finalist_decision.py` may emit
`unique_winner`; the API and web UI otherwise show `tie`, `inconclusive`,
`no_viable`, or that the pilot could not advance.  Structured web requests
must be mesoscopic and carry the canonical 1000--1011 seed/variant mapping.

The frozen real smoke record
`validation/robust_closure_search_smoke_v1.json` closes the executable slice:
on the 2025-09-16 demand release and edge `26842526_96527131_0`, three
four-hour candidates were run in SUMO 1.27.1.  Two failed health/integrity
gates; 00:00--04:00 was the robust winner among the verified finalists after
12 matched runs, with q90 the worst demand variant and a 142.96 s
simultaneous upper bound.  The record deliberately retains
`global_best_claim_allowed=false`.  It validates the implementation and
result honesty, not the future monthly scheduling policy.

The next stop gate is unchanged: build the resumable multi-day monthly
orchestrator, freeze its pilot/finalist tolerances and repetition cap against
a named golden monthly benchmark, then evaluate once on a newly frozen
untouched held-out set.  Until that passes, the product may return “best
among SUMO-verified finalists” for the active demand window but may not claim
the globally best work period for a month.  This checkpoint passes 183
focused search/API tests and the complete suite passes 1,193 tests with 20
expected skips.

**Resumable monthly execution checkpoint 2026-07-19.**  The missing
orchestration layer is now implemented in
`traffic_sim/simulation/monthly_search.py`, with an archived-demand SUMO
backend in `monthly_sumo.py` and
`run_monthly_closure_search.py` as the stable root CLI.  The workspace
persists immutable policy, calendar ledger, screening, each pilot candidate,
pilot selection, cumulative adaptive finalist rounds, every robust decision,
complete input/runtime/source provenance and the terminal result.  A restart
loads completed candidate evidence instead of rerunning SUMO; a succeeded
search is idempotent.  A changed policy, backend/archive, semantic source
digest, malformed screening or non-canonical seed fails before it can
reinterpret an old result.

The implementation review corrected one prerequisite that the earlier pure
decision slice could not represent: candidates on different dates
necessarily have different no-closure traffic.  They may now use distinct
matched baseline IDs while sharing one study provenance. Candidates inside
the same date/envelope baseline group still must share the exact baseline
value and common variant/seed identity. This preserves paired comparisons
without incorrectly requiring July 5 and July 20 to have identical normal
traffic.

**Golden monthly policy v1 — PASS (internal), 2026-07-19.**  The tracked
policy in `validation/monthly_search_policy_v1.json` is frozen from the
diagnostic and confirmed by
`validation/golden_monthly_search_v1.json`: one pilot repetition per
q10/q50/q90 variant, 300 s pilot retention band, four initial finalist
repetitions, 600 s absolute precision floor, 300 s practical-equivalence
tolerance and at most 12 repetitions per variant.  The final portable golden
v6 ran three bounded-exhaustive four-hour windows on a real Gothenburg demand
archive in SUMO 1.27.1.  It completed in **211.42 s**, peak RSS
**427,819,008 bytes**, used one worker, and selected **06:30--10:30** as the
only viable window; 06:00 and 06:15 failed the teleport gate.  The finalist
met precision after q10/q50/q90 repetition counts **4/5/7**, below the cap.
The immutable workspace passed its ledger/hash verification, and an
idempotent completed-result reload required no new SUMO work.

This closes the CLI/orchestration and named golden-policy parts of the stop
gate, not the release gate.  The production backend currently accepts one
explicit successful demand archive and proves that all shortlisted envelopes
fit it. A real future-month search still needs a resolver that groups
schedules by their 1--9 day envelopes and builds/reuses every required
forecast demand archive.  After that, one newly frozen untouched monthly
held-out set must pass practical-winner recall, regret and failure recall.
The asynchronous API/status/cancel path, exact-schedule UI handoff and
browser recovery test also remain. Therefore the golden result deliberately
retains `global_best_claim_allowed=false` and `ui_exposure_allowed=false`.
The focused monthly execution gate passes 63 tests; the complete project
suite passes **1,212 tests with 20 expected skips**.

**Multi-envelope resolver + forecast smoke — 2026-07-19.**  The missing
resolver exists: `traffic_sim/simulation/monthly_demand.py` groups
shortlisted schedules by the exact `DemandBuildSpec` of their simulation
envelope, finds or builds one succeeded immutable archive per envelope,
freezes the mapping in a release manifest under
`runs/monthly-demand-releases/`, and routes each candidate to its matched
`ArchivedDemandSumoRunner`.  A pinned archive that changes after freezing
fails closed.  `validation/multi_envelope_forecast_smoke_v1.json` records a
real two-envelope 2027 forecast search (two demand builds + 28 SUMO runs in
489 s): 07-15 06:00--10:00 was the robust winner and the 07-22 candidate was
excluded by real teleport/throughput hard gates.  Claim boundary unchanged:
diagnostic smoke, `global_best_claim_allowed=false`.

**Async API + live-release protection checkpoint (2026-07-19, this
session).**  Build-order step 6 is implemented: `POST /api/monthly_search`
(body exactly `{"closure_search_spec": ...}`), `GET
/api/monthly_search/status` and `POST /api/cancel?kind=monthly` in
`serve.py`, sharing `_sim_lock` and the durable job records with the other
four simulation jobs.  The server forces the frozen golden policy file and
bounded-exhaustive screening; a browser cannot supply tolerances or a
proxy shortlist (the proxy stays failed/unreleased).  Status polling
surfaces the CLI child's own persisted workspace progress pointer, so any
tab sees the live phase; the curated "done" summary always carries the
result's `claim_boundary` verbatim.  Cancel kills the process group but the
workspace stays resumable — POSTing the same spec continues from completed
SUMO evidence.

Two defects found reviewing the resolver slice, both fixed with regression
tests:

1. **P0 — envelope demand builds clobbered the live release.**  The
   resolver's automatic `build_sumo_demand.py` runs write THROUGH the live
   `sumo/` demand products and `web/data/od_matrix.*`; the smoke left the
   deployed site silently calibrated for 2027-07-22 forecast (committed in
   9bb3e28).  `monthly_demand.py` now snapshots the runtime release product
   set before the first missing-envelope build and restores it
   byte-for-byte afterwards, on success and failure; the live release was
   repaired to the documented 2025-09-16 historical build
   (57e3fd904e32776bc481) from its immutable run archive, with
   scenarios/OD/validation verified coherent.
2. **P1 — archived routes could run on a different network.**  Demand
   archives carry no `net.net.xml`, so the runner's network check was
   vacuous.  It now enforces `demand_meta.json`'s
   `sensor_contract.network_sha256` against the active `sumo/net.net.xml`
   whenever the record exists.

Remaining before release: the new untouched monthly held-out set
(practical-winner recall, regret, failure recall), the step-7 forecast-UI
exact-schedule handoff, and the browser recovery test.  Until then the API
result keeps `global_best_claim_allowed=false` and the winner wording stays
"best among SUMO-verified finalists".  Complete suite after this
checkpoint: **1,237 tests with 20 expected skips**.

**Step-7 UI + evidence-level claim boundary (2026-07-19, same session).**
The claim boundary in `_final_result` is now evidence-level aware: bounded
exhaustive screening involves no proxy — every ranked candidate carries
real SUMO evidence, the same evidence level the released closure-time
feature already shows — so those results carry
`ui_exposure_allowed=true` with scope `sumo_verified_bounded_exhaustive`;
proxy-screened results stay unexposed, and `global_best_claim_allowed`
stays false in EVERY mode until the untouched held-out gate passes (the
pilot retention band is golden-frozen, not held-out validated).  The
result now also records `shortlisted_schedules` (every SUMO-verified
candidate's exact intervals) so readers can map robust statistics to real
dates without re-deriving the calendar.

The web app gained the "Bästa arbetsperiod" workspace: edge picking reused
from the closure flow, a multi-month date-range/daily-band/weekday/work-hours
form with rolling periods up to 90 workdays,
start + poll with the workspace's own persisted phase shown live, cancel
(kind=monthly, workspace stays resumable — the deterministic
form-content-derived `search_id` means re-running the same search resumes
its immutable workspace), an on-load running-job discovery, and a result
table showing every SUMO-verified schedule with its worst-variant robust
point/upper-95 deltas, hard-failure tags and honest wording ("bäst bland
SUMO-verifierade scheman inom angivna tider", never "globalt bäst").  The
exact-schedule handoff builds the loaded scenario from the schedule's OWN
intervals: if the live demand does not cover the schedule's dates it first
runs the ordinary recalibration pipeline (confirmed with the honest
~6 min/day cost), then runs a normal windowed multi-interval closure
ScenarioSpec through `/api/close` (the ScenarioSpec contract already
accepts multiple non-overlapping intervals per edge — verified).  Headless
Chrome/CDP smoke: page loads with zero console errors, the workspace
opens, weekday/source controls respond, and the run gate (no edges → no
POST) holds.  The full browser recovery test (reload mid-search) remains
open for step 8.  Complete suite: **1,238 tests with 20 expected skips**.

**Step-8 browser recovery test — PASS (2026-07-19, same session).**
`tools/browser_recovery_test.py` is a repeatable headless-Chrome/CDP test
of the exact incident shape this project shipped twice in 2026-07: a
long server job whose starting tab disappears.  It runs the real serve.py
handler/threading/status stack and the real web-app JS against a faked
monthly CLI (35 s, persisting the same workspace manifest phases the
status endpoint reads), and asserts four things: (1) a search started
from one page keeps running after that page reloads mid-job; (2) the
fresh page's on-load discovery re-attaches — run button locked to
"Söker…" and the live workspace phase surfaced from the persisted
manifest; (3) completion reaches the re-attached page, rendering the
result panel with the honest claim wording and the global-best
disclaimer; (4) controls return to idle.  Screenshot-verified.  A first
run of the test caught its own fixture racing a too-short fake job —
the mid-run window must exceed page load + reload + one 4 s poll tick.

The only remaining release gate for the monthly product is the untouched
monthly held-out set (practical-winner recall, regret, failure recall),
which is what keeps `global_best_claim_allowed=false`; bounded-exhaustive
results are UI-exposed with the restricted wording.

**Held-out v2 campaign — PASS (2026-07-20).**  The release gate has now
run and passed.  Method, frozen before outcomes (commit e4edb90):
`validation/monthly_proxy_manifest_v2.json` — 12 NEW cases, 104 exact
schedules, every edge disjoint from all 12 v1 edges, full strata
coverage, with the gate thresholds content-keyed INTO the manifest
(practical-winner recall ≥0.90 at the golden policy's frozen 300 s
practical-equivalence tolerance, p90 normalized shortlist regret ≤0.10,
failure recall ≥0.60; Spearman and strict exact-tie recall demoted to
reported diagnostics per the step-4 recovery decision — v1's two "missed
winners" were 4 s and 22 s on medians whose seed ranges spanned hundreds
of seconds).  Three missing demand envelopes (2027-12-24 holiday 1-day,
2027-07-15 3-day and 5-day) were built first, all 100% GEH<5 with zero
infeasible intervals, with the live release snapshotted/restored around
the builds (verified back on 2025-09-16/57e3fd90 afterwards).

**V5 EXECUTED AND FAILED; V6 FROZEN UNEXECUTED (LUNA-V5-02 / LUNA-V6-02,
2026-07-27).** The v5 campaign ran once and failed honestly: median objective
spread 0.0 across all five held-out edges, so `discriminating_case_coverage`
and `discriminating_practical_winner_recall` failed while practical-winner
recall, regret, failure recall and shortlist coverage passed. No gate record was
written and none was adopted. Root cause was the SELECTION, not the proxy: v5
chose edges structurally, with no pre-outcome signal for objective spread. V6
replaces that rule with `demand_exposure_v1`, which requires strictly positive
q10/q50/q90 route exposure in every frozen closure window and ranks candidates
by temporal variation, computed from the canonical archived demand bound by
exact path and five file hashes. V6 is frozen, UNEXECUTED and UNAPPROVED; its
archive designation is v6-local and does not repair the globally ambiguous
demand key. Demand exposure is a selection signal only and is NOT claimed to
guarantee a 300-second SUMO spread. Every case keeps its raw per-window
q10/q50/q90 counts and the schedule IDs they belong to, so the ranking is
recomputable from the frozen artifact alone rather than trusted; the freeze
tool has no overwrite flag and rolls back a failed publish. Adoption remains
default-closed: no gate record and no adoption certificate exist.

**AUDIT PASSED, ADOPTION REJECTED (LUNA-V4-04 concluded rejected;
LUNA-V5-01, 2026-07-27).** The v4 audit stands: the preserved evidence is
complete, identity-bound and reproduces its report and gate record
canonically. ADOPTION was rejected for whole-record integrity — a lone gate
record is self-certifying, so a byte edited inside it still validated
against itself. The tracked candidate was REMOVED; the product is in
bounded-exhaustive fail-closed mode with UI/global-best claims closed.
Adoption now needs a gate record AND a post-review adoption certificate
binding its exact bytes (contract:
`validation/monthly_gate_adoption_contract_v1.json`). V5 is FROZEN but
UNEXECUTED and UNAPPROVED: five cases, 75 schedules, edges disjoint from all
v1-v4 held-out edges, deterministic pre-outcome selection, v4 thresholds
unchanged; no v5 gate record or certificate exists. The caveats below still
apply to any future adoption — negative median Spearman (shortlister, NOT a
reliable ranker) and failure-disqualification recall only modestly above its
floor. Historical v4 detail follows.

**Superseded header (v4 adoption, now rejected):** The v2 result below is retained as history and is NOT the
active gate. The audited v4 record (campaign key `1505ecfb…`, root
`runs/closure-proxy-validation/1505ecfb…`, record SHA-256 `9ba2fa10…`) was
copied byte-for-byte to `validation/monthly_proxy_v4_gate.json` after the
LUNA-V4-03 audit reproduced its report and gate record canonically. Frozen
gate: 5/5 cases, 75 schedules, all seven checks pass — practical-winner recall
1.0, discriminating practical-winner recall 1.0, p90 normalized shortlist
regret 0.0, failure-disqualification recall 0.6819, discriminating case
fraction 0.6, ranking case fraction 1.0, all shortlists contain an eligible
candidate. LIMITATION: median Spearman is NEGATIVE (-0.371; -0.637 on
discriminating cases), so the proxy is adopted as a SHORTLISTER and explicitly
NOT as a reliable full ranker; Spearman remains a diagnostic under v4's
practical-winner gate. Claims stay bounded to SUMO-verified schedules within
the enumerated search space, and the loader fails closed on a missing,
altered, incomplete or earlier-campaign record.

Result (`validation/monthly_proxy_v2_gate.json`, evidence digests inside;
raw outcomes under `runs/closure-proxy-validation/dec211d4…/`): all 12
cases and 104 schedules completed exhaustively.  **Practical-winner
recall 1.0 (strict recall also 1.0), p90 regret 0.0, failure recall
0.867 (39/45 disqualified schedules caught), ranking coverage 7/12 —
every check passed.**  Honest composition: 5 of 7 ranking cases involved
genuine proxy pruning (shortlists of 4-6 of 9) and still recalled every
exhaustive winner with zero regret; 2 ranking cases were
unscoreable-fallback (shortlist-everything, trivially recalled — the
deployed safety behavior); 5 cases were failure-only, with real
infrastructure findings (closing those roads strands ~200-4,300 vehicles
per run).  Spearman was measurable in only 1 of 7 ranking cases (0.894)
— reported, not gated.

Consequence, wired fail-closed in `monthly_search.py`
(`load_passing_heldout_gate` + the evidence-aware claim boundary): with
the tracked passing record present, bounded-exhaustive results now carry
`global_best_claim_allowed=true` (scope: the enumerated search space),
and `monthly_proxy_v1`-screened results are UI-exposable with
`sumo_verified_monthly_shortlist_heldout_validated` scope.  Any missing,
failed or malformed record reverts every claim to the pre-release
boundary; a proxy version not covered by the record stays closed.  The
pre-registered release contract (golden-frozen policy + passing untouched
held-out set) is therefore satisfied and the claim language may say
**trafikmässigt bäst inom angivna tider** — still never permitted/safe/
TA-plan compliant, which remain user-supplied unverified policy.

### Final acceptance gate

- The displayed schedule is byte-for-byte derivable from the immutable
  schedule that SUMO ran.
- Same daily hours, consecutive dates, opening between shifts and requested
  total work time are all enforced by contract tests.
- Warm-up, overnight vehicle carryover and recovery cannot be truncated.
- Every eligible candidate uses matched baselines, seeds and separate demand
  variants from the same demand release.
- Proxy quality passes held-out recall/regret gates or the system clearly
  falls back/withholds a global recommendation.
- Mesoscopic diagnostics are never mislabeled as exact queues.
- Access, integrity, simulation health and policy limitations cannot be
  hidden by a favorable delay score.
- The system can return a unique robust winner, a tie, an inconclusive
  result, no viable closure, or insufficient evidence without fabricating
  certainty.

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
  **DONE 2026-07-20.**  The data was always durable and API-reachable
  (job records embed the full spec; the scenario index carries
  `scenario_spec`/`build_id`/`demand_build_key`), but no surface exposed
  it — the history panel showed only status/kind/time/id, so an operator
  had to hand-query `/api/jobs/<id>`.  Job rows are now clickable and
  open a detail view rendering the job record, the run's exact
  ScenarioSpec (summary fields plus the verbatim JSON), its demand and
  network build IDs, closure intervals and seed set — and a linkage line
  stating whether the active validation report covers THIS job's build,
  with the same amber warning as the shield when it does not.  Built with
  `textContent` throughout: job args echo user-supplied edge IDs and
  error strings.
- The 🛡 validation panel reflects the ACTIVE study's build, not merely
  the latest demand build.
  **DONE 2026-07-20.**  This was a real integrity gap, not a cosmetic
  one: `validation.json` recorded `demand_window`/`demand_source` but no
  build identity, and the panel rendered it beside whatever scenario was
  loaded — so a scenario from another build showed a green shield
  validating something else.  The report now records `demand_build_id`
  (null, never invented, when the build did not record one), and the web
  app compares it against the active scenario's own
  `scenario_spec.demand_build_id`.  On mismatch the shield drops to "–"
  (never "pass"), an amber banner states that the report describes a
  DIFFERENT build and says the gates below mean nothing for what is on
  screen, and the gate table is visually muted.  Verified in headless
  Chrome in both states (matching → normal shield with the build id in
  the provenance line; mismatched → warning state), plus regression tests
  on the report side.

  KNOWN STATE while doing this (2026-07-20): the tracked
  `web/data/validation.json` is deliberately left at its last COHERENT
  version and therefore does not yet carry `demand_build_id`; it will on
  the next legitimate regeneration.  Reason: the held-out v2 campaign's
  envelope builds overwrote `sumo/candidates.rou.xml`, which the report
  hashes to prove the frozen temporal-holdout evidence still belongs to
  the live release.  Regenerating now records a FALSE stale for that
  section — the evidence is valid for release `57e3fd90…`, only the proof
  file on disk was clobbered — and the live pool is not recoverable (no
  archive stores it, and rebuilding would mint a new build id that no
  longer matches the published scenarios).  The pool is now part of the
  protected live-release product set so this cannot recur.  The UI treats
  a missing `demand_build_id` as "cannot compare": it neither warns
  falsely nor claims the gates apply.
- The sensor table never makes a source observation, a split assumption, a
  rounded map value and a final SUMO count look like the same number.
  **VERIFIED ALREADY SATISFIED 2026-07-20** (audited, no change needed):
  the source cell is labelled by kind (`riktad` / `tvåvägs-total (en
  mätning)`); a two-way station renders as ONE station row with indented
  children whose source cell reads `modellandel av total`, so the raw
  Total is never repeated as if it were two measurements; frozen target
  and simulated output are separate columns, as are representative-seed
  and ensemble values; `auditSimMean` prefers the pre-rounding
  `simulated_mean_raw` over the map's rounded integer; every cell keeps
  the exact value in its `title`; and `auditSum` propagates null so a
  missing directed value makes the station value unknown, never zero.
- A cancelled or failed study leaves the previous published study visible
  and clearly labelled as the one still in force.
  **DONE 2026-07-20.**  The BEHAVIOUR was already correct and tested
  (staging plus atomic publish; cancellation/failure leaves the live
  scenario set untouched; the publish gate refuses staged sets on GEH
  collapse, infeasible intervals, build-ID mismatch, variant gaps or
  corrupt JSON).  The LABEL was missing: a failure fired a transient
  `alert()` and then went quiet, so after a failed 2027-03-15
  recalibration the operator was looking at 2025-09-16 data with the only
  evidence being a dismissed dialog.  A persistent, operator-dismissible
  banner now names both sides — e.g. "Omkalibreringen för 2027-03-15
  (forecast) misslyckades. Kartan visar fortfarande den föregående
  studien: 2025-09-16 (historik), bygge 57e3fd904e32." — and is wired
  into the recalibration and closure paths, whose outcomes replace what
  is displayed.  Deliberately NOT wired into the closure-time and monthly
  searches: those do not publish a study, so claiming "the previous study
  is still shown" would misdescribe what happened; they keep their local
  error reporting.  Verified in headless Chrome by driving the real
  failure path (stubbed API, app's own poll loop), asserting the previous
  scenario stays loaded and the banner names the attempted study, the
  in-force study and its build.

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
   trials show a material wall-time improvement with identical results — AND it
   clears the user-facing latency contract for the case it targets. Parallel
   seed execution was measured this way and NOT adopted: see "Seed-parallel
   campaign line — measured and closed" below.
6. Keep detailed vehicle, lane, and queue output limited to the selected
   signal study so citywide runs do not produce unnecessary I/O.

Do not reduce seed count, uncertainty variants, solver iterations, closure
rerouter coverage, or simulation step fidelity as a speed shortcut.

### Seed-parallel campaign line — measured and closed (2026-07-23)

The paired serial/parallel seed-worker campaign line (phase-profile v4–v6,
`--seed-workers 3` over the frozen baseline and whole-window closure) was
executed under recorded approval and is now CLOSED. Final reviewed evidence
from the v6 verification run (three seeds q50/q10/q90, five trials per arm,
mesoscopic, result-preserving with identical scenario/trajectory digests):

- baseline: p95 wall **5.883 s** at three workers, a **43.8%** improvement over
  the serial arm — comfortably under the 10 s validated-completion gate.
- closure whole-window: p95 wall **10.4234 s** at three workers, a **40.8%**
  improvement — but it **misses the 10 s gate by 0.4234 s**.

Three consecutive campaigns (v4, v5, v6) all landed the closure whole-window
parallel arm just over the ceiling; the case is dominated by `sumo_execution`,
which seed-parallelism at three workers does not shrink enough. The result is
faster and result-preserving but does not satisfy the latency promise for the
one case it needed to.

DECISION: seed parallelism is **not adopted** as a production default, **not
retried**, and **not mechanically refrozen as a v7**. The production
seed-worker default is unchanged, the phase-profile campaign harness has no
current executable campaign (`CURRENT_CAMPAIGN_ID = None`; v1–v6 all retired),
and the honest product path for a closure query that cannot finish inside the
budget is the ALREADY-IMPLEMENTED asynchronous `/api/close` start/poll/cancel
workflow (serve.py + web/app.js) — no new async work is created or claimed
here. A materially different architecture, not a fourth seed-parallel campaign,
is the only path that would reopen this.

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

### Architecture boundary for closure latency — static study (2026-07-24)

Static, non-SUMO boundary discovery after the v4–v6 seed-parallel line closed.
No SUMO/libsumo/TraCI was invoked, no outcome or state snapshot was inspected,
and nothing here is a measurement: every number is either a source fact or an
ESTIMATE derived only from the reviewed PERF-16/17 summaries.

**1. The supported new-closure control path (source symbols, not inference).**
`serve.py::_run_close()` writes a `ScenarioSpec` under `SPEC_DIR`, then shells
`run_scenario.py --scenario-spec` (or `--closure` JSON per window, or legacy
`--close`) through `run_in_new_session(..., timeout=600)`. It never blocks the
HTTP request: `_close_state` under `_close_lock` drives `GET /api/close/status`
(`idle|running|done|error|cancelled`) and `POST /api/cancel?kind=close` stops
the job by process group. `runs/jobs/<id>.json` records each job and, at
startup, `simulation_recovery_block()` marks a surviving pgid
`orphaned_running` (cancellable) or a dead one `orphaned` — that is **orphan
detection, visibility and cancellation, NOT resumption**: an interrupted close
job is never continued, only reported and stopped. `run_scenario.main()` then
runs the frozen `PHASE_NAMES` sequence:

| phase | source | work |
|---|---|---|
| `input_validation` | `main()` | spec/window/demand validation |
| `job_preparation` | `main()` | window, variant and seed resolution (`demand_variants`, `variant_path`) |
| `closure_preparation` | `main()` | `edges_near(close_edges, REROUTER_RADIUS_M=400)` → `write_closure_additional()` (`<rerouter>`/`closingReroute` per `grouped_closure_intervals`), `build_edge_graph(set(close_edges))`, `edge_freeflow_times()`, then `prepare_closure_variants(prep_jobs)` → serial `prepare_variant_job` → `truncate_stranded_vehicles` per demand variant |
| `job_preparation` | `main()` | per-seed `scratch_dir/seed-<seed>` isolation + `write_edgedata_additional` |
| `sumo_execution` | `run_seed_job` → `run_sumo` | one SUMO **subprocess per seed** (`--seed <seed>`, `--mesosim true`, private `work_dir`) |
| `aggregation_validation` | `parse_edgedata`, `aggregate_flows`, `closure_integrity_status` | flows, Monte-Carlo confidence, integrity/health gates |
| `trajectory_publication` | `publish_trajectories_from_vehroute` | trajectory product |
| `scenario_publication` | `atomic_write_json` | `web/data/scenarios/<name>.json` + `index.json` manifest entry |
| `cleanup` | `cleanup_scenario_workspace` | scratch removal, only after successful publication |

Artifact lifecycle. **Staged/mutable** — everything under
`create_scenario_workspace()` (`runs/<run-id>/scratch` or a private temp dir):
`closure_<name>.add.xml`, the filtered `<stem>_<name>.rou.xml` variants,
per-seed dirs, edgedata additionals, vehroute XML. **Published/durable**, and
these carry DIFFERENT fields — the scenario JSON and its `index.json` entry
carry `scenario_spec`, `closures`, `closure_integrity`, `demand_signature`,
`build_id`, `demand_build_key`; the trajectory JSON carries only
`n_vehicles`, `n_unfinished`, `inserted_in_run`, `sampling`,
`displayed_share`, `edges`, `vehicles`, i.e. no ScenarioSpec/build/closure
identity of its own. **Reusable/immutable inputs** — `sumo/net.net.xml`, the
calibrated q50/q10/q90 route variants, `sumo/demand_meta.json`.

**2. Identity/key matrix, by layer.** No single existing structure covers the
whole thing; reuse must be keyed at the layer it actually applies to.

| layer | required key | what exists today |
|---|---|---|
| network-derived indices | `net_sha256`, `schema_version` | `metadata.load_metadata()` refuses a stale `sumo/network_metadata.json` |
| simulator state snapshot | demand build id, network build id, demand variant, seed, simulation mode, warmup end, input + source fingerprints, git commit, Python/SUMO version, platform, `save_state_rng`, `save_state_precision` | `WarmStateIdentity` encodes exactly these **and only these** — it does not carry ScenarioSpec/closure intervals, output configuration, validation rules or publication identity |
| closure-input preparation | closed edge set, closure intervals, demand variant content, network build | no cache exists |
| whole-query result | everything above **plus** output configuration, validation rules and publication identity (scenario name, manifest entry) | no cache exists |

Missing identity must invalidate reuse, never silently widen a key.

**3–4. Candidate classes.**

*A. Exact-query result reuse.* **Not implemented today.** The manifest is not
consulted before a run: `index_for_current_demand()` is called once, inside
`scenario_publication`, purely to drop entries from a different demand
calibration before writing `index.json`; neither `/api/close` nor
`run_scenario.main()` performs a pre-run lookup. Removable phase: on a hit, the
ENTIRE `PHASE_NAMES` pipeline (input validation through publication). Remaining floor:
a manifest read plus the HTTP response. But a
correct key must include the full whole-query layer above, so a genuinely NEW
closure can never hit — it answers repeats only. Concurrency/restart: a lookup
is a read of the published manifest, so it adds no concurrency of its own, but
it must not observe a half-published run — `scenario_publication` writes the
scenario JSON and `index.json` with `atomic_write_json`, and a lookup would
have to treat an entry as valid only once both writes have landed; a server
restart loses nothing because the manifest is on disk. Invalidation: any
demand, network, source, SUMO or output-configuration change. Provenance: a
served result must carry the original run's identity, never be re-attributed to
the new request. Deterministic-output risk: serving a stored result is exact by
construction (no simulation re-runs), so the risk is not numerical drift but
MIS-ATTRIBUTION — a key any coarser than the whole-query layer would return a
different query's bytes, which is why the key cannot be narrowed to make it hit
more often. **Rejected as a new-query speed-up** (it would serve the
already-fast cached-render case, not validated completion of a new closure).

*B. Fully keyed closure-input preparation reuse.* Removable phase:
`closure_preparation`, ≈1.15–1.25 s (ESTIMATE from the reviewed summaries).
Its network-only component is **already cached** —
`edge_freeflow_times()` and `build_edge_graph()` both take the
`load_metadata(NET_PATH, sumo/network_metadata.json)` fast path. What remains
is `truncate_stranded_vehicles` per demand variant, keyed on closed edges +
closure intervals + variant content, so a new closure cannot hit and a repeat
degenerates to class A. Remaining floor: `sumo_execution`. Concurrency/restart:
pure per-variant work writing distinct staged files (`prepare_variant_job`
returns counts only), so it parallelises safely in principle; but a cache would
move those files OUT of `create_scenario_workspace()`, which today guarantees
`cleanup_scenario_workspace()` removes them only after successful publication —
a restart mid-write would leave a cached artifact no run tree owns, so the
cache would need its own atomic publish and staleness sweep. Invalidation:
variant content, network build, or any change to `truncate_stranded_vehicles`
itself (its filtering rules are part of the key, not just its inputs).
Provenance: filtered routes are staged inputs, never published. Deterministic-
output risk: `truncate_stranded_vehicles` is deterministic given
(routes, closed edges, adjacency, free-flow times), so a correctly keyed hit
reproduces the same bytes; the risk is a key that omits one of those inputs —
notably the closure INTERVALS, since the same edge closed over a different
window yields different truncation. **Rejected**: no new-query benefit remains.

*C1. Persistent EXTERNAL sumo controlled over TraCI.* A long-lived `sumo`
process driven by the TraCI socket protocol. Removable phase: **only per-seed
process spawn and teardown**, NOT network load. Official SUMO docs are explicit
that TraCI `simulation.load` reloads the simulation *with command-line options*
— it re-reads the net and additionals for a new scenario; the distinct
`loadState` operation is the one that retains the network/additional objects,
and that is class D, not this. A new closure changes the rerouter additional and
the truncated routes, so it needs a full `load` and re-parses the network anyway.
Remaining floor: the simulated 24 h itself with the closure active PLUS the
network reload on every new query. IPC is NOT a per-simulated-second cost: SUMO
documents `simulationStep(t)` as advancing to a target time in a single call,
so a batch closure using SUMO's own `<rerouter>`/`closingReroute` runs to the
end with a small constant number of socket round-trips, not one per second — the
earlier "per-step IPC net cost" claim was wrong and is withdrawn.
Concurrency/restart — a genuinely NEW ownership boundary, not the current one.
Today each request is a short-lived `run_scenario.py` process group that
serve.py reaps with `killpg`; a SUMO process that must survive to serve the NEXT
request cannot be owned by that exiting group. A persistent pool therefore needs
its own longer-lived supervisor (serve.py itself, or a dedicated pool manager)
with an explicitly different model: (i) LIFECYCLE — the pool is spawned at
server start or lazily on first close and retired wholesale on any net/demand/
SUMO-version/configuration change; (ii) CANCELLATION — a per-request cancel must
abort the in-flight `load`/`simulationStep` on the borrowed member and return or
discard THAT member, not `killpg` the pool, so serve.py's current per-request
pgid cancellation no longer covers it and must be extended; (iii) CRASH/ORPHAN —
a member that crashes or hangs mid-query is discarded (never reused), and pool
members orphaned by a server crash must be detectable and reapable the way
`runs/jobs/<id>.json` makes subprocess jobs recoverable today; (iv) COLD
FALLBACK — a query that cannot get a healthy member falls back to the current
fresh-subprocess path rather than blocking. None of this exists yet; it is part
of what any adoption after the experiment would have to build and have reviewed. Invalidation: net, demand, SUMO version or
configuration change must retire the process. Provenance: `source_fingerprints`
and the phase profile currently describe a fresh process per seed; a reused
process must bind and re-verify that identity per query. Deterministic-output
risk: LOW here — a per-query `simulation.load` re-reads command-line options
including `--seed`, so each query is re-seeded exactly as a fresh process would
be; the risk reduces to proving no simulation state leaks across a `load`, which
is what the paired digest check below verifies.

*C2. In-process libsumo.* Removable phase: per-seed process spawn and teardown
plus TraCI socket setup — but, as in C1, NOT network load (only
`loadState`/class D retains it). Process boundary, corrected: libsumo would run
inside `run_scenario.py`, which `serve.py::_run_close` already launches as a
job CHILD via `run_in_new_session`; a libsumo crash therefore takes down that
job child, not `serve.py`, and the existing job-gate/orphan-recovery machinery
still applies. Concurrency/restart: SUMO documents that concurrent libsumo
instances require Python `multiprocessing` (one interpreter cannot host
concurrent simulations), so keeping the parallel-seed capability is a design
obligation — one worker process per concurrent seed, which also RESTORES the
per-seed cwd isolation `run_sumo` needs for relative edgeData paths — not a
capability proved impossible; a crashed worker is discarded and respawned.
Remaining floor: the same simulated 24 h plus per-query network reload.
Invalidation: as C1. Provenance: as C1, and each worker must bind and
re-verify identity per query. Deterministic-output risk: with per-query
`load` re-applying `--seed` the RNG hazard is the same LOW one as C1; the
in-process specifics to prove are that no module-level state leaks between a
worker's successive queries and that each worker keeps its own cwd, both
verifiable by the paired digest check.

*D. Per-seed/variant save-load checkpoint replay before the earliest closure.*
Machinery exists and is keyed: `save_state_arguments()` /
`load_state_arguments()` (`--save-state.rng true`, `--save-state.precision 16`),
`WarmStateIdentity`, `store_warm_state` / `restore_warm_state`,
`certify_warm_state_equivalence`, and `run_sumo()`'s `save_state_path` /
`save_state_time_s` / `load_state_path` — but `main()` never passes them, so the
`/api/close` path does not use it. Removable phase: simulated time before the
earliest closure. **Decisive limit for the failing case**: the frozen
`closure_whole_window` case has `start_offset_s: 0`, so there is nothing before
the closure to skip and the mechanism removes zero. For time-windowed closures
(`--closure` JSON with a later `begin`) one warm state per (demand, network,
seed, variant, warmup_end_s) would serve many different closure edges, which is
a genuine new-query benefit — for a different case. Remaining floor: simulated
time from the warm point to the end. Concurrency/restart: per-seed states are
independent files, so seeds parallelise unchanged; `store_warm_state` ALREADY
publishes atomically (writes a `.{content_key}.tmp` directory and `os.replace`s
it into place), so a crash mid-write leaves no half-published entry, and
`restore_warm_state` already refuses an entry whose identity does not verify —
the remaining obligation is only that a verification miss falls back to a cold
t=0 run rather than loading a partial snapshot, which the existing
`CacheLookup` miss path already does.
Invalidation: any field of `WarmStateIdentity`. Provenance: the
published run must record that it resumed from a certified state, not claim a
cold run. Deterministic-output risks to prove first: RNG continuity across the
seam, incrementally loaded vehicles at the load boundary, edgeData/vehroute
output continuity, state precision and SUMO version compatibility, and closure
timing alignment. Note `CACHE_FIELD_TOLERANCES = {"travel_time_s": 1.0}` is a
**decision-metric** policy, not an exact-flow equivalence, and must not be
repurposed as one.

**5. Decision: select ONE bounded, future approval-gated experiment — a
persistent-process (C1) arm proven result-equivalent to the current subprocess
arm.** Criterion 5 asks for a candidate that could *plausibly* affect the hard
ceiling with paired before/after cases and semantic + health equivalence proof.
Producing the SAME scenario and trajectory as today is exactly what that proof
checks, so it is the target, not a disqualifier. Ruling the field down:

- **A, B**: remove no work from a NEW closure query (rejected above).
- **D (save/load checkpoint)**: removes ZERO for the failing case, whose closure
  is active from `start_offset_s: 0`; it helps only time-windowed closures, a
  different case.
- **C2 (in-process libsumo)**: removes the same process-creation cost as C1 plus
  socket setup, but requires a `multiprocessing` redesign to keep parallel seeds
  and per-seed cwd isolation, a larger change for a marginal additional saving
  over C1. Not selected; kept as a fallback only if C1 proves the lever real but
  socket cost material.
- **C1 (persistent EXTERNAL sumo over TraCI)**: SELECTED. It keeps SUMO in
  external processes, so external isolation is retained — but, per the
  Concurrency/restart clause above, a pool that spans requests is a NEW
  ownership boundary: serve.py's current per-request `killpg` cannot own it and
  must be EXTENDED with member-level cancellation and pool orphan-reaping. A
  per-query `simulation.load` re-applies `--seed`, so determinism is preserved by
  construction; and its output is identical to the subprocess arm by design, so
  paired digest/health/integrity equality is directly provable. Its removable
  work is per-seed process creation only (the net is reloaded on each `load`), an
  **unmeasured** quantity — NOT assumed small — and whether it reaches the
  ≈0.42 s p95 gap is precisely what the experiment measures.

**The one bounded experiment (defined here, NOT authorized or executed).**

- *Question*: does a persistent-process closure arm reduce the p95 PARALLEL wall
  time of the failing case below the 10 s ceiling — and below the current
  subprocess arm — while producing a semantically identical result?
- *Proposed files*: a new benchmark harness under `tools/` that drives a fixed
  pool of three reused, TraCI-controlled `sumo` processes (one per concurrent
  seed), plus focused tests. No change to
  `run_scenario.py`, `serve.py`, production defaults or any contract; production
  keeps the subprocess path until and unless a separate adoption task passes.
- *Immutable key*: the canonical scheme in the note below.
- *Arms and query sequence, exact*: two arms — `arm_subprocess` (today's fresh
  process per seed) and `arm_persistent`, a fixed pool of **three** reused
  TraCI-driven `sumo` processes, one dedicated member per seed slot (member_0 →
  seed 1000/q50, member_1 → seed 1001/q10, member_2 → seed 1002/q90), each in its
  own private `work_dir` so the per-seed cwd isolation `run_sumo` relies on is
  preserved. Every query runs its three seeds concurrently across the three
  members, each member serving its seed via `simulation.load` (which re-applies
  that seed) and never crossing to another seed's slot; a member that faults on
  any query is retired and, on the cold-fallback path, that seed for that query
  runs as a fresh subprocess. Because a stale or no-op reload could silently
  return the PREVIOUS query's result and still "match" a same-query reference,
  the persistent arm must run a sequence of DISTINCT queries that exercises both
  transition directions and both scenarios, not five reloads of one closure:
  `baseline → closure → baseline → closure → …` for ten queries (five
  `closure_whole_window` on `26842525_26355153_0` 00:00–24:00 interleaved with
  five `baseline`), all seeds 1000/1001/1002 → q50/q10/q90, meso, same net and
  demand build. The five closure queries are the latency gate; the interleaved
  baseline queries are the isolation control.
- *Equivalence proof (semantic, hard gates, any miss fails the experiment)*: for
  EACH query in the sequence, that query's `scenario_digest` AND
  `trajectory_digest` must equal a fresh-subprocess reference of THAT SAME query
  — so a reload that returns the wrong scenario (baseline digest where a closure
  is expected, or vice versa) fails immediately. These are the harness's
  `canonical_digest()` values, i.e. SEMANTIC equality (it strips
  `generated_at`/`created_at`/`finished_at` and `path`/`source_path`/`workspace`
  before hashing), not raw byte identity; the claim is exact semantic
  equivalence, not byte-for-byte files. Every seed-health record must stay 0
  collisions, 0 teleports, 0 running_at_end, 0 waiting_at_end and
  loaded == inserted; every closure query must stay `verified_clean`.
- *Latency gate, frozen numerically*: the statistic is the p95 of the five
  closure queries' PARALLEL wall time with `seed_workers = 3` (the deployed count,
  one seed per pool member), never a sum or median of per-seed spawn times.
  TIMER BOUNDARY, frozen: the measured wall time is per-query and EXCLUDES the
  one-time pool startup/warm-up — that is amortized across queries and is exactly
  what the persistent arm exists to remove — but INCLUDES the per-query
  `simulation.load` (net reload) on every query, since that recurs per closure.
  The one-time pool bring-up (`pool_warmup_queries = 0` billable warm-up queries;
  the pool is ready before the timed sequence begins) is measured and reported
  separately, never folded into the gate. PASS requires BOTH
  `parallel_p95_wall_s ≤ 10.0` (the hard ceiling) AND
  `parallel_p95_wall_s < arm_subprocess_p95` by at least
  `min_p95_improvement_fraction = 0.04` (≈ the 0.42 s / 10.4 s crossing the
  failing case needs); an identical-or-slower persistent arm is a no-go, not a
  tie.
- *Failure cleanup*: on success, failure OR interruption the harness closes every
  TraCI socket and terminates and reaps all three resident `sumo` members (no
  orphaned simulator may outlive the run), publishes no scenario, manifest entry
  or state, preserves its run tree, and spends the attempt. A per-query
  `timeout_seconds = 600` bounds any single query (matching serve.py's current
  close timeout); a member that exceeds it is killed and reaped, not left
  resident.
- *Execution boundary*: it invokes SUMO, so it requires a clear user request for
  the frozen experiment plus the normal safety confirmation appropriate to an
  expensive, evidence-producing run. Nothing here initiates that run, and no
  key or value is computed or frozen.
- *Pre-committed reading*: if the closure p95 is ≤ 10.0 s AND ≥ 4% below the
  subprocess arm with EVERY query semantically identical and healthy, it advances
  to a separate adoption task (production change and the C1 supervisor model
  still gated). If every query is semantically identical but the closure p95
  stays over 10.0 s (or the improvement is below 4%), process-lifecycle
  amortization is a definitive NO-GO for this case and the line closes for good.
  Any digest/health/integrity miss on ANY query fails the experiment outright —
  a faster but different result is never adopted.

**Frozen experiment contract (LUNA-PERF-19, UNEXECUTED / UNAPPROVED).** The C1
experiment defined above is now built as a fail-closed, non-production harness
(`tools/benchmark_persistent_sumo.py`) and frozen as
`validation/persistent_sumo_campaign_v1.json`, experiment id
`persistent_sumo_v1`, content key
`72108df6b3ec61de33e5006181d38abc3aba3292bcb8b907643dd9d7f431f588`. It has NOT
been executed: no SUMO/TraCI ran, no socket opened, no outcome exists, and the
contract carries no measured value or approval. Importing the harness, validating
the contract, or running its focused tests never imports TraCI or starts SUMO;
TraCI is imported lazily only after an explicit `--execute` passes the full
contract + environment preflight. This freeze is NOT adoption authority and NOT a
performance claim. The prior seed-parallel PERF-16 key/approval is spent and
invalid for this experiment. Actually running it — preflight, execution or any
outcome inspection — requires a separate explicit user request matching the
frozen key above, normal execution-safety confirmation, and a real TraCI driver
that is
deliberately out of this pre-outcome build. Until then the asynchronous
`/api/close` path remains the product path.

**RESULT (LUNA-PERF-20, 2026-07-24): FAILED EXPERIMENT — C1 REMAINS UNTESTED.**
The one authorized invocation ran at key `72108df6…` into
`validation/persistent_sumo_campaign_v1_outcome` (264 files preserved; the
attempt is spent and must not be rerun). Verdict `eligible_and_passed: false`,
failed gates `member_fault`, `fallback_used`, `seed_health:{1,3,5,7,9}`,
`parallel_latency_ceiling`, `p95_improvement_floor`. This is NOT a C1 no-go:
the persistent arm never existed, so nothing about persistent SUMO was
measured. Two defects in the never-executed `--execute` path caused it, both
invisible to the fake-driven suite:
(1) FATAL — `_TraciConnector._default_spawn` launches `sumo --remote-port <p>
--num-clients 1` with NO network file, so SUMO exits at once ("Quitting (on
error)"); all three pool members died during warm-up and all 30 persistent
seed-runs faulted with "Connection closed by SUMO" and took the cold
fallback. Reproduced independently with a bare launch. The reported
`persistent_p95_wall_s` 19.28 s vs `subprocess_p95_wall_s` 11.38 s
(-69.3%) therefore measures dead-pool retry plus cold-child overhead, NOT
process reuse, and must never be quoted as a persistent-SUMO measurement.
(2) `_variant_family` does not recognise the real filtered-route filename
`calibrated.rou_close_<edge>.rou.xml` (`Path("calibrated.rou.xml").stem`
keeps `.rou`), so seed health failed on all five closure queries even though
the telemetry itself was perfect.
What the run DOES establish, because both arms degenerated to fresh
subprocesses: the shared production payload/assembly path is sound end to end
— 10/10 scenario digests and 10/10 trajectory digests identical across arms,
5/5 closures `verified_clean`, every seed `loaded == inserted` with zero
teleports/collisions/running/waiting, and no orphaned process after the run.
The frozen key is spent; retesting C1 needs a repaired harness, a NEW frozen
identity and fresh exact-key user approval. No adoption, deployment, release
or publication follows, and `/api/close` remains the product path.

**REPAIRED AND RE-FROZEN (LUNA-PERF-21, 2026-07-24): `persistent_sumo_v2`,
content key
`fa07c8b8b356d8cd938f22a9e8b27f2b5fbc98d5deaff963bf12a838ed215e70`,
UNEXECUTED and UNAPPROVED.** Both proven v1 execute-path defects are fixed
process-free: (1) a pure `build_bootstrap_args` now starts each pool member as
`sumo -n <net> --remote-port <port> --num-clients 1` in its own work directory
and session, so a member can actually reach a TraCI client; the v2 contract
binds that bootstrap template exactly and refuses a re-keyed mutation of it.
Every TIMED query still `simulation.load`s the full fresh-subprocess argument
set — the bootstrap network is scaffolding only and no bootstrap-only option
ever enters a timed load. (2) `_variant_family` now maps production's real
filtered-route names (`calibrated.rou_close_<edge>.rou.xml` and the q10/q90
equivalents), so clean three-seed closure telemetry passes seed health while
cross-bound or malformed evidence still fails closed. v2 preserves v1's matrix,
seed/member map, ten-query order, timer boundary, report schema, shared
production builders and every strict gate, binds the finalized harness plus the
current `run_scenario.py`/network/demand/route fingerprints, sets
`outcomes_present_at_freeze:false`, and names v1 as its failed/spent
predecessor. `persistent_sumo_v1` is RETIRED in the harness and refused before
any executable boundary; its spent attempt and 264-file outcome tree are
preserved read-only and may never be rerun. There is still NO measured C1
result and no adoption authority. Any v2 preflight, execution or outcome
inspection requires a separate task and fresh exact-key user approval naming
`fa07c8b8…`. `/api/close` remains the product path.

**RESULT (LUNA-PERF-22, 2026-07-24): C1 IS A DEFINITIVE NO-GO — VALID
EXPERIMENT, HYPOTHESIS REJECTED.** The repaired campaign ran once at key
`fa07c8b8…` into `validation/persistent_sumo_campaign_v2_outcome`; the run tree
is preserved and the attempt is spent and never rerunnable. Unlike the failed
v1 attempt this run was fully ELIGIBLE, on the preserved report's own evidence:
`member_faults: 0`, `fallbacks: 0`, `pool_warmup_queries: 0` with a reported
one-time `pool_warmup_wall_s` of 3.03 s excluded from every query wall, 10/10
scenario digests and 10/10 trajectory digests equal between the persistent and
paired fresh-subprocess arms, 5/5 closures `verified_clean`, every seed
`loaded == inserted` with zero teleports/collisions/running/waiting, the frozen
alternating query order, and a report envelope matching the contract schema.
The repaired seed-health path was exercised on production's real
`calibrated*.rou_close_<edge>.rou.xml` names.
It failed exactly the two performance gates: `parallel_latency_ceiling` and
`p95_improvement_floor`. Persistent closure p95 **11.3904355838 s** vs paired
subprocess p95 **11.0998385168 s** — improvement **-0.0261802968**, i.e.
process reuse is marginally SLOWER, and both arms sit above the 10.0 s ceiling.
Baseline queries show the same pattern (persistent 6.10-6.66 s vs subprocess
6.07-6.35 s).
INTERPRETATION, per the pre-committed reading "equivalent but slow/insufficient
improvement is a definitive C1 no-go": persistent SUMO process reuse does NOT
deliver the required speed-up. What this experiment establishes is narrow and
exact — ELIMINATING PER-QUERY PROCESS CREATION DID NOT IMPROVE p95. It carries
NO phase-profile evidence, so it must not be read as showing which remaining
phase dominates a query; that would need a separate profiling task. C1 is
CLOSED — do not re-open persistent pooling as a latency lever without a new
hypothesis. Equivalence is positively demonstrated in the exact sense the gate
defines: TraCI-driven reuse reproduces artifacts that are equal under the
frozen CANONICAL SEMANTIC DIGEST, which deliberately excludes volatile
timestamps (`generated_at`/`created_at`/`finished_at`) and
path/`source_path`/`workspace` fields. That is semantic equivalence, NOT a
byte-identity claim. No adoption, production default, API, deployment, release
or publication follows; `/api/close` remains the product path, and the
10-second goal must be pursued elsewhere in Phase 7.

**Reusable identity scheme (used by the experiment above; defined, not
instantiated).** The immutable key is hex
`sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())`
with the contract's own `content_key` removed — identical to
`campaign_content_key()` in `tools/benchmark_speed.py`, so identity semantics do
not fork. `payload` must carry every identity-bearing field: schema/experiment
id and freeze timestamp; `net_sha256` and network build id; demand build id,
`demand_build_key` and calibrated variant fingerprints; source and harness
fingerprints; SUMO version and the exact argument template (`--mesosim true`,
`--meso-junction-control true`, `--meso-junction-control.limited true`, `-n`,
`-r`, `-a`, `--seed`, `--begin`/`--end`, `--no-step-log`, `--no-warnings`); the
two-arm, ten-query sequence with its exact `baseline`/`closure` ORDER, and per
query the seed↔demand-variant mapping
(`{"1000": "q50", "1001": "q10", "1002": "q90"}`), simulated window and (for
closure queries) the closed edge `26842525_26355153_0`; the deployed
`seed_workers = 3` used for the parallel p95 and the matching three-member pool
size; the per-query `timeout_seconds = 600`; the frozen gate values
(`max_parallel_p95_wall_s = 10.0`, `min_p95_improvement_fraction = 0.04`, the
health and closure-integrity requirements); the timer boundary
(per-query timing EXCLUDES one-time pool warm-up, INCLUDES the per-query
`simulation.load`) and `pool_warmup_queries = 0`; the persistent-arm lifecycle
and restart policy (seed↔member binding, cold-fallback rule, retire-on-fault
rule, terminate-and-reap-on-exit rule); trial count; and platform id. Missing any field
invalidates the key rather than widening it. No key or value is computed or
frozen here.

Until and unless that experiment is approved, executed and passes BOTH its
equivalence and its latency gate, **the product path stays the already-implemented
asynchronous validated completion**: `/api/close` starts the job and returns
immediately, `/api/close/status` polls, `/api/cancel` cancels, and orphaned jobs
are detected and cancellable at startup (not resumed). No new async work is
created or claimed here.

**6.** This study adopts no mechanism, freezes no v7, reopens no v1–v6
identity, and changes no code, test, contract, production default or
architecture.

## External Data Requests — CLOSED, no further data coming (decided 2026-07-20)

**DECISION (Gustav, 2026-07-20): there is no more data. The project ships
with the delivered 2025 six-sensor counts as its permanent measured input.**
The four requests below will NOT be sent, and their absence is no longer a
pending dependency — it is a fixed boundary. The consequences are therefore
PERMANENT honest labels, not provisional ones awaiting an unlock:

- Signal results are `synthetic` **permanently** — a real, mechanically
  safe, TSFS-certified experiment, never `city-configured`. The step-5/6
  city-import rungs of Phase 5 are closed as won't-do, not deferred.
- Through-traffic SHARE stays a sensitivity-tested prior, permanently. The
  OD matrix stays "one plausible matrix consistent with the six counts",
  as already labelled.
- Purpose labels rest on compatible generated provenance (ranked item 3,
  done) and state a behavioural class, not verified individual intent —
  permanently.
- No local road-speed/travel-time calibration; queue/roundabout/spillback
  numbers stay diagnostic, never presented as measured.

None of this stops the product. It is complete on the data it has, and the
confidence map plus these labels are exactly the honesty mechanism that
makes shipping on six sensors defensible. `docs/plans/DATA_REQUEST_2026-07.md` is
retained only as a record of what WOULD strengthen which claim, marked
not-sent; it is not an action item.

The original four (kept for the record — what each would have unlocked):

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
  and only `purposes` still warns (see next bullet — that warning is
  SUPERSEDED; every section is green on the current release).
- ~~Purpose compatibility remains the open P0~~ **SUPERSEDED 2026-07-20.**
  This bullet described the state on 2026-07-16, before the realism pass;
  it is retained only to date the change.  Purpose-stratified calibration
  landed and closed it: on the active release all three variants report 0
  incompatible quarters, 0 replaced routes and 0 relaxed-mix quarters, and
  `purpose_claims_allowed` is TRUE.  See ranked item 3 for the mechanism
  and evidence.  The cordon count is still wanted, but for the
  through-traffic SHARE — not for route/purpose compatibility.
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
4. DONE (2026-07-20) — residual CLASSIFIED as unbiased stochastic
   dispersion; no bounded output correction is justified and none was
   applied.  Analysis on the active golden release's frozen audit series
   (7 directed sensor edges × 96 quarters, targets vs raw edgeData
   ensemble): (a) NOT a timing shift — MAE at lag 0 (1.9-3.2 veh/q) is
   strictly better than at ±1 quarter (4.2-8.6) on every edge; (b) NOT a
   coverage deficit — per-edge daily sums deviate +0.04% to +0.80%
   (network +0.42%, slightly positive, no end-of-day loss pattern);
   (c) volume-proportional dispersion — |residual| 0.95 veh/q below 20
   veh/q vs 3.94 at ≥60 veh/q (~5.4% of volume), signed mean +0.16 veh/q,
   and the ensemble-vs-target residual (2.62 veh/q mean) is the same
   order as the single-seed vs ensemble-mean spread (2.07 veh/q) — i.e.
   the residual is at the Monte-Carlo noise floor of a 3-run ensemble.
   A "correction" would fit noise.  Consequently the contingent temporal/
   LOSO rerun is not triggered: the release is unchanged and the standing
   post-destination-fix LOSO baseline remains authoritative.
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

### Completed: exact monthly warm-state reuse (2026-08-03)

The warming accuracy blocker is closed. The private mesoscopic tripinfo
accumulator omitted by SUMO state serialization is transported through exact
unfinished-tripinfo identity reconstruction, and whole-vehicle values use
SUMO-compatible decimal half-up formatting. The fresh v16 paired campaign
passed 3/3 exact semantic comparisons and published three certified states.
Those states are installed atomically in the product cache and the monthly
command now selects warm execution by default with an explicit cold escape
hatch and fail-closed fallback. Measured cache-hit runtime improved from
88.506 s cold to 71.568 s warm across q10/q50/q90 (19.1%). The next performance
work is coverage and connection-safe parallelism, not another accuracy retry.

### Superseded: narrow 2027 candidate-free population (2026-08-03)

The coverage and isolated-population foundation is ready at annual plan key
`b89e4a5e…105a542`. It binds 363 eligible dates, 1,089 closure slots, 363 exact
daily demand contracts and 3,267 production-mapped requests
(`1000→q10`, `1001→q50`, `1002→q90`). The compact store retains the exact route
input because future departures are absent from SUMO state files; a byte-exact
pilot reduced 375,668,139 unique original bytes to 33,316,391 stored bytes
(8.87%) with zero restore mismatches.

A real three-worker SUMO/TraCI pilot populated the July 15 06:45 checkpoint for
all three variants with zero failures. The production root is initialized with
all 3,267 units pending and can resume by rerunning the frozen command in
`validation/annual_warm_readiness_v1.json`. This is population readiness only:
annual artifacts remain candidate-free and uncertified for product reuse, so
route-safe binding, equivalence evidence and cold fallback are unchanged. Full
population under that narrow plan was not started and the plan is no longer
eligible for execution.

### RUNNING: audited full-day 2027 population (started 2026-08-04)

Plan `de071336…f203db` is the active plan; it supersedes `9cc823d3…45283b` and
every earlier root after the candidate/demand release was extended to all 7,125
routable edges, the disk architecture was corrected (archive pruning,
proportional gate, LZMA encoding) and the transient-launch retry policy was
added. Each of those edits changed a fingerprinted source, so the source seal
correctly forced a fresh plan key and root — that is the seal working, not
churn. It retains every
15-minute-aligned
00:00–24:00 independent daily interval. Exact source-year and DST rules support
1,682,634 of 1,699,440 interval placements; these collapse to 367 canonical
demand builds, 34,895 checkpoints and 104,685 q10/q50/q90 states. Unsupported
envelopes remain explicit cold fallbacks rather than synthesized coverage.

Population is organized into exact demand-build/seed/variant chains. Only the
first checkpoint in a chain starts at zero; each later checkpoint validates and
extends its predecessor. This removes the previous design error where 104,685
distinct keys implied 104,685 independent prefix simulations. Real q10/q50/q90
SUMO diagnostics reproduce exact prefix evidence and closure metrics after
chaining. A late favourable checkpoint reduced direct candidate runtime from
16.726 s to a 6.773 s cache-hit suffix; a 900-second adjacent extension also
beat a new prefix run.

The final maximum-depth q10 pilot completed all 96 links with zero failures.
Route-window shards prevent the full three-day route from accumulating in every
saved state; expanded states remain 1.24–1.59 MiB. Independent cold checks at
links 2, 48 and 96 match every behavioural evidence section exactly. The sole
byte difference is the recorded non-behavioural `loaded` lookahead count from
the cold full-route parser; inserted/teleport counters and all vehicle evidence
remain exact gates. Native millisecond accumulator handoffs are pinned by a
96-link regression.

Warm-cache schema v3 removes the Git commit from effective identity while
retaining exact source/input/runtime fingerprints, so documentation-only
commits no longer invalidate states. Historical schema-v2 entries remain
fail-closed and are not silently promoted.

The final pre-run audit also removes scale-only overhead that would have become
hours during population: plan context is indexed once, one runner is reused per
worker/current demand build, archive validation records are forwarded from the
main process, predecessor restore selects only state/prefix evidence, and SQLite
finishes each dependency-ready batch transactionally. Semantic orphan recovery
now validates prefix, demand and SUMO state contents before promotion, and
provisional monthly workspaces are cleaned on success or failure. Bound
measurements and official SUMO references are recorded in
`docs/reviews/WARMING_FINAL_AUDIT_2026-08-03.md`.

Progress is transactional SQLite rather than a 100k-entry rewritten JSON
ledger. The pre-run audit added exact immutable-row/lifecycle verification,
SQLite integrity checking, orphan-artifact reconciliation, non-replacing
manifest publication, archive-to-member hash binding, unique atomic temp files,
symlink rejection, shared inter-process demand-build ownership, runtime/source
plan provenance, and realistic initial/runtime disk gates. Three persistent
spawn-isolated workers bind the plan once and retain private TraCI connections.

A measured canonical three-day archive occupies 326 MiB and the q10 96-link
store occupies 40 MiB. **Superseded 2026-08-04: the flat 192-GiB gate was
sized around retaining all 367 three-day archives at once (367 x 326 MiB ~=
117 GiB), which dwarfed the ~42 GiB of artifacts.** Retention was never
necessary — `pack_artifact` already binds the route, demand meta, build spec
and manifest as content-addressed blobs, and the group loop only resolves an
archive while that build still has selectable units. Three changes replaced the
gate:

- **Archive pruning.** `_prune_demand_archive` deletes a three-day archive once
  every unit for its build is durably succeeded, refusing if any unit remains
  selectable, if the path is outside `runs/`, if it is not named `demand-*`, or
  if it is a symlink. `--keep-demand-archives` opts out. Peak archives fall from
  116.8 GiB to 0.6 GiB.
- **A proportional gate.** `required_free_bytes` derives the requirement from
  the units this invocation can actually select, at a measured per-unit rate.
  The flat constant had refused every bounded pilot for archives it would never
  build, which made `--max-units`, `--demand-build-key` and `--variant`
  unusable on any realistic disk.
- **LZMA as a third store encoding**, chosen per member from measured output
  size: route 6,371,443 -> 950,432 B (14.9%), prefix evidence 181,037 ->
  110,204 B (60.9%). SUMO's already-gzipped state correctly stays `identity`;
  a ratio guard skips the LZMA attempt so no chain pays CPU for zero bytes.

Projected peak is now 42.0 GiB against a 55.8-GiB preflight requirement (the
gate charges the unsealed rate, so real usage falls below it as chains
complete). Preflight passes and full population is RUNNING.

A further 3.2x was measured but deliberately not taken: `prefix_evidence.json`
stores a cumulative record at every link, 97.8 MB of JSON per chain carrying
665 KB of new information (0.9%). Sealing a whole chain as one LZMA stream
collapses evidence to 0.23 MB and states to 2.79 MB, taking the store to
~4.1 GiB and the peak to ~13 GiB. Two measured caveats: containers must group
like members together (interleaving states and evidence costs 16.6 MB instead
of 3.0 MB), and the state saving only materialises if states are stored
EXPANDED — which means asking SUMO for uncompressed state XML and therefore
re-running the 96-link chain audit. It was not done because it would invalidate
a passing audit immediately before the run.

**Transient-failure policy (2026-08-04).** The first production run aborted
after 311 units when one worker's SUMO did not accept its TraCI socket
("Could not connect in 61 tries") while its two siblings started normally, 38
units into a build's chain. Aborting a multi-day population for a startup
hiccup discards every completed chain, and over 104,685 units such a hiccup is
near-certain to recur. `_is_transient_launch_failure` now permits at most
`TRANSIENT_UNIT_RETRY_LIMIT` retries for process/socket startup failures only;
every validation, provenance, artifact and semantic failure still aborts
immediately, because those mean the bank would be wrong rather than merely
delayed. Retries are printed, never silent.

Release, adoption and any proxy licence remain separate evidence gates and do
not weaken exact exhaustive execution.

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

## Current supersession note — 2026-08-09

The earlier statement that a full population was running describes a prior
warming identity and is historical evidence, not current execution state.
Production source changes created plan `9b640a0c…`; its preflight passes, but
only two deliberately bounded q10 pilot units have been populated. The current
status is 2 succeeded, 0 failed and 104,683 pending. Full population remains a
separate decision. The active local demand build is `dbb44172f30778adf8c0`,
with zero short-trip cap violations and zero unanchored vehicles. Fresh
temporal LOSO remains structurally underidentified and fails the TAG-aligned
aggregate, so no absolute validation claim is made.
