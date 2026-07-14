# Simulation Accuracy, Speed and Robustness Plan

**Review date:** 2026-07-14
**Scope:** SUMO network construction, demand-to-simulation contracts, normal
scenario runs, signal experiments, trajectories, queues, speed behaviour,
roundabouts, performance, and publication safety.
**This document is a review and implementation plan. It does not change the
simulation.** `ARCHITECTURE.md` remains the structural source of truth.

## Decision

Do not replace the current citywide mesoscopic simulation with microscopic
simulation. That would make the normal closure workflow materially slower and
would not make guessed traffic signals truthful.

Instead, maintain two explicitly different products:

1. **Fast citywide flow mode:** the existing mesoscopic Monte Carlo run. It
   remains the default for a whole-day map, demand calibration, and quick road
   closure screening.
2. **Detailed corridor mode:** a bounded microscopic SUMO run for the affected
   signal, roundabout, or closure corridor. It measures queues, speed, delay,
   lane use, and actual phase execution. It is only run when the user asks for
   that level of detail.

This is the only defensible route to both a fast normal product and a more
accurate detailed product. Microscopic car-following, lane-changing, signal
phases, and roundabout interactions necessarily cost more computation. The
solution is to limit the detailed spatial and temporal window, not to pretend
that the same citywide mesoscopic run has microscopic fidelity.

## What The Code Does Today

### Already present

- `build_sumo_net.py` assigns each edge a SUMO maximum speed from OSM
  `maxspeed`, with a road-class fallback, and a lane count. Vehicles therefore
  already have different *maximum* free-flow speeds on different roads.
- The generated `sumo/net.net.xml` currently contains 65 traffic-light
  programs and 56 explicit `<roundabout>` objects. Roundabouts are not absent
  from the generated SUMO network.
- `run_scenario.py` uses mesoscopic SUMO by default. It includes limited
  junction control only when a downstream queue is jammed. This makes normal
  whole-day scenarios fast enough for interactive road-closure work.
- The separate signal laboratory and optimization code runs microscopic SUMO,
  compares several candidate timing programs, and labels its results as
  synthetic. `signal_regulation.py` also applies a conservative Swedish
  timing envelope to synthetic plans.
- Per-seed statistics, timeout handling, and some scenario health gates exist.
  Those are valuable foundations, but the gates are not yet complete enough to
  guarantee a reproducible published release.

### Important limits

- `build_sumo_net.py` writes plain nodes without their source control type and
  then invokes `netconvert --tls.guess true`. The 65 normal-scenario traffic
  light programs are generated from geometry; they are not Gothenburg's
  controller plans. Their cycles, phases, offsets, detectors, bus priority,
  and pedestrian handling are unknown.
- The normal scenario path does not select and load a signal-plan artifact.
  Signal plans are exercised in separate experiments, not in the ordinary
  interactive closure simulation.
- Meso models queues and average segment travel time, but it does not model
  explicit lateral movement and does not support actuated traffic lights.
  It cannot be used as the authority for per-lane queue lengths, exact
  red/green delay, lane-changing, or detailed roundabout behaviour.
- Queue reporting is currently only a network-wide maximum `halting` count in
  summary output during metric experiments. It is not a queue length by
  approach, lane, junction, or time.
- Road speeds are inputs and SUMO defaults supply vehicle behaviour. The model
  has no calibrated local distribution of headway, acceleration, vehicle
  length, speed variation, or lane-changing parameters.

## Confirmed Problems, Ordered By Severity

### P0 - Fix before relying on detailed simulation results

1. **Closure animations omit rerouted vehicles.**

   `run_scenario.py:769` only reads a direct `<route>` from SUMO vehroute
   output. SUMO puts vehicles changed by a rerouter inside
   `<routeDistribution>`. The correct final-route selection already exists in
   `signal_closure_combine.py:101-103`.

   This was verified on the current closure artifact: 6,400 of 21,594 vehroute
   vehicles use `routeDistribution`, while the trajectory JSON draws 15,194 of
   21,594 vehicles (70.36%). The displayed animation therefore hides exactly
   the rerouted vehicles that matter most after a closure.

   **Required fix:** share one `final_route(vehicle)` parser, select the final
   route in either representation, and publish a reconciliation report. Every
   inserted vehicle must be one of: represented, explicitly outside the web
   map, explicitly unrenderable with a reason, or failed the run health gate.
   This is parsing and validation work; it adds no SUMO runtime.

2. **A server restart can leave an orphan simulation running while a new one
   starts.**

   `serve.py:226-252` marks a surviving process group as
   `orphaned_running`, but does not regain the simulation lock or expose a
   persisted-PGID cancel path. The normal cancel endpoint only controls an
   in-memory close/recalibration job. An orphan and a new job can therefore
   write the shared `sumo/` and scenario names concurrently.

   **Required fix:** on startup either terminate and verify every orphaned
   process group, or enter a durable recovery-blocked state until an operator
   cancels/reconciles it. Give every job a unique workspace and publish only a
   completed release pointer. Never let two jobs share intermediate filenames.

3. **Missing or corrupt health telemetry can publish as healthy.**

   `parse_seed_health()` returns `None` for missing/corrupt statistics and
   `seed_health_flags()` skips it. The staged publisher rejects only a
   non-empty flag list. An unmeasured seed can therefore pass a health gate.

   **Required fix:** require one parseable health record for every requested
   seed and demand variant. Missing telemetry must fail publication, not be
   treated as an older compatible artifact.

4. **Normal scenarios cannot truthfully claim usable signal control.**

   The default call in `run_scenario.py:530-674` is mesoscopic with limited
   junction control. It does not load a chosen signal plan. The signal-plan
   artifacts produced by `signal_optimize.py` are only used in separate micro
   experiments. Thus ordinary scenarios cannot currently report realistic
   red/green delays, signal queues, or optimized light behaviour.

   **Required fix:** do not turn on a generic flag globally. Implement the
   signal-plan contract and detailed corridor mode described below, and keep
   the normal mode labelled as flow-level simulation.

### P1 - Accuracy, reproducibility, and decision-quality gaps

5. **The scenario demand signature does not identify the actual demand.**

   `run_scenario.py:62-81` hashes date/window/source metadata but not route
   file bytes, candidate configuration, direction-split data, network,
   SUMO version, or code revision. Rebuilding the same date with different
   demand inputs can make stale scenario or signal artifacts appear current.

   **Required fix:** define one build ID from canonical configuration and
   SHA-256 hashes of calibrated routes, candidate pool, counts, direction
   split, network, signal plan, SUMO version, and Git commit. Require exact
   build-ID equality at every hand-off.

6. **q10/q90 uncertainty variants are simulated but not fully gated.**

   Scenario seeds rotate through the q50, q10, and q90 direction variants,
   but `build_sumo_demand.py` persists the PFE fit and structural diagnostics
   only for q50. A bad q10/q90 variant can influence two thirds of a published
   scenario ensemble even when q50 passes.

   **Required fix:** persist and gate GEH, hard-bound/infeasibility results,
   structural checks, purpose checks, route health, and vehicle conservation
   per variant. Exclude a failed variant or fail the release; never average it
   into uncertainty without disclosure.

7. **The run registry archives wrong uncertainty filenames.**

   Variants are named `calibrated_v1.rou.xml` and `calibrated_v2.rou.xml`, but
   `_tracked_main()` looks for nonexistent `calibrated_q10.rou.xml` and
   `calibrated_q90.rou.xml`. Current run manifests therefore record missing
   uncertainty artifacts.

   **Required fix:** archive the outputs declared in metadata, including every
   route file and agent sidecar, rather than hardcoding names. A run cannot be
   marked reproducible while required outputs are missing.

8. **The signal optimizer builds and evaluates mismatched demand periods.**

   `signal_optimize.py` accepts a start and end, but it passes only `-b` to
   `tlsCycleAdaptation.py`. The installed SUMO tool uses the following hour,
   while `tlsCoordinator.py` has no period argument and reads the full merged
   route file. A requested 07:00-09:00 result can therefore use 07:00-08:00
   for green splits and whole-day demand for offsets, then be evaluated over
   two hours.

   **Required fix:** create a route artifact filtered to the exact analysis
   period (plus separately specified warm-up), give that same artifact to
   every timing tool, and add a regression test for a two-hour request.

9. **Signal studies start with an empty network and stop abruptly.**

   The current signal lab and optimizer use `--begin` at the measured window
   and `flush_s=0`. Vehicles and queues that should already exist at 07:00 are
   absent, and vehicles still active at the end cannot contribute an arrival
   result. This is suitable only for a bounded diagnostic, not operational
   plan selection.

   **Required fix:** simulate an explicit 30-60 minute warm-up, measure only
   the requested period, then drain long enough to account for in-window
   trips. Calibrate the warm-up sensitivity rather than hardcoding its length.

10. **Short approaches make signal and queue capacity fragile.**

    The existing signal screen finds 55 of 148 TLS approaches under 15 m,
    including very short fragments. SUMO documents a specific risk of
    unrealistic traffic-light flow reduction on very short edges. Such links
    also cannot hold a credible physical queue.

    **Required fix:** build a network-quality gate before timing work. Merge
    artificial fragments only when this preserves stable edge IDs through a
    mapping layer, or repair the junction geometry with a reviewed connection
    model. Validate the affected junctions individually before using their
    queues in an objective.

11. **The UI shows a fixed signal candidate as the result.**

    `serve.py:543-546` displays `adapted_coordinated`, whether or not it is
    the lowest-delay non-disqualified condition. A candidate can be worse than
    baseline while the UI calls it the "after" result.

    **Required fix:** select the best condition only after paired multi-seed
    comparison and all safety, health, queue, and provenance gates pass. If
    none passes, publish "no valid improvement" rather than a recommendation.

12. **The network loses important source semantics during conversion.**

    The GraphML contains known traffic-signal and roundabout tags, but
    `build_sumo_net.py` exports only coordinates, speed, lane count, and shape
    before using `--tls.guess`. The generated network does preserve 56
    roundabout objects, but there is no mapping/audit proving that each source
    roundabout, signal, turn restriction, priority, and lane movement has
    survived correctly.

    **Required fix:** preserve source control metadata in a sidecar keyed by
    stable IDs; emit/audit node type, roundabout membership, priority,
    `turn:lanes`, turn restrictions, directional lane counts, and permitted
    movements. Keep the existing stable edge ID contract.

### P2 - Robustness and refinement work

13. **Speed parsing is unsafe for common OSM formats.**

    `parse_speed_ms()` concatenates all digits. For example, `"30;50"` becomes
    3050 km/h and `"30 mph"` becomes 30 km/h rather than roughly 48 km/h.
    The local data may mostly use simple Swedish values, but the parser is not
    safe for future data.

    **Required fix:** use a strict OSM speed parser with units, semicolon and
    conditional values, sane bounds, source provenance, and an explicit
    fallback. Reject implausible values rather than silently creating a fast
    road.

14. **Lane allocation is only an approximation.**

    `parse_lanes()` halves a two-way `lanes` total and ignores
    `lanes:forward`, `lanes:backward`, `turn:lanes`, bus lanes, parking,
    restrictions, and short turn pockets. This can distort saturation flow,
    queues, and roundabout entry capacity.

    **Required fix:** prefer directed lane tags, retain turn-lane and movement
    restrictions, then validate only the priority corridors first. Do not
    invent detailed lane behaviour where the source has no data.

15. **Road-speed feedback is modelled from assumptions, not observed travel
    speeds.**

    Current BPR feedback derives congestion from calibrated flow and assumed
    capacities. It is useful as a prior, but does not validate actual travel
    time or queue tails. Adding arbitrary car-following parameters would make
    results look detailed without adding evidence.

    **Required fix:** obtain held-out travel-time/speed observations for a
    small set of corridors first. Only then calibrate a limited vehicle-type
    distribution and report its uncertainty.

## How Stoplights Should Work

### Correct model of a junction

A junction should not optimize every lamp independently. A controller chooses
**phases**: each phase is a safe set of compatible movements that can be green
together (for example, non-conflicting opposing straight movements). It then
chooses:

- cycle length;
- green split for each compatible phase;
- clearance, yellow, all-red, and red-yellow intervals;
- offsets between nearby controllers;
- time-of-day plan changes;
- detector, pedestrian, cyclist, tram/bus-priority, and emergency rules.

The existing `signal_regulation.py` preserves the compatible green-state
groups present in its input plan and allocates one junction's green budget
jointly. It must remain a synthetic candidate generator. It cannot infer the
real phase diagram, controller logic, or transit priority from six vehicle
counters.

### Required signal data

Request through Miroslaw/Gothenburg City, for each controller:

1. physical signal object to intersection to SUMO TLS/link mapping;
2. phase diagrams, allowed movements, conflict matrix, stop lines, and lane
   arrows;
3. cycle, green split, offset, and plan-switch time by day type/time of day;
4. detector positions and actuation rules;
5. pedestrian/cycle crossing phases and clearance requirements;
6. tram/bus priority and emergency/pre-emption rules;
7. temporary plans used during works or incidents.

Until this data exists, call every plan **synthetic** and do not publish a
green/red recommendation as a real operational instruction. Swedish timing
rules are useful as a safety envelope, but they are not a substitute for a
Gothenburg controller plan.

### Signal implementation sequence

1. Build a signal inventory and validate its map locations and controlled
   movements in SUMO GUI/netedit.
2. Introduce a versioned `SignalPlan` artifact: plan ID, network build ID,
   day type, period, phase states, timing, detector configuration, provenance,
   and source hash.
3. Make a detailed scenario explicitly load one `SignalPlan`. Record the
   exact plan ID in every result and trajectory artifact.
4. Add legal and physical validation before a run: no conflicting protected
   greens, clearance timing, min/max greens, pedestrian/tram constraints, and
   valid link indices.
5. Use actual arrivals and lane saturation data, not only departures, to
   create candidate green splits. Generate candidates from compatible phase
   groups; do not create one phase per approach by default.
6. Compare baseline and candidates with identical demand, warm-up, seed set,
   network, and measurement window. Use paired results.
7. Publish a candidate only if it improves the stated objective and passes
   health, queue-spillback, safety, and uncertainty gates. Otherwise retain
   the baseline and report no valid improvement.

## Queues, Speeds, and Roundabouts

### Queues

For detailed corridor runs, add targeted output only at the approaches being
evaluated:

- `--queue-output` at 15-30 second periods for lane queueing length and
  queueing time;
- E2 lane-area detectors for arrival, discharge, occupancy, standing vehicles,
  maximum jam length, and delay;
- edge/lane travel time, throughput, stop count, and a TLS state trace;
- a spillback flag when a queue reaches the upstream link or blocks a previous
  junction.

Use ordinary `queueing_length` as the main queue-length measure. Do not use
the separate experimental queue-length field as an operational score. Keep
these outputs scoped to the micro corridor and measurement period so they do
not make the citywide fast path slower or create huge files.

### Road speeds and vehicle behaviour

1. First repair speed and lane parsing and attach a provenance label:
   measured/imported/defaulted.
2. Keep OSM/default maximum speed as the free-flow constraint.
3. In micro mode, let SUMO model leader, red-light, priority, and queue-induced
   speed reduction.
4. Only after obtaining observed travel-time/speed data, calibrate a small,
   interpretable vehicle mix: passenger car/truck/bus proportions, `tau`,
   acceleration/deceleration, speed factor/deviation, and lane-change model.
5. Hold out corridors and dates for validation. Never tune these parameters
   solely until the six sensor counts look better.

### Roundabouts

The current SUMO network already has explicit roundabouts and SUMO can model
circulating priority. The missing work is fidelity validation, not a blind
replacement of roundabouts.

1. Audit every source roundabout against the generated one. Each must map to a
   SUMO roundabout or have an explicit reviewed exception.
2. Check entry yield, circulating priority, exits, prohibited turns, lane
   count, ring speed, approach speed, and geometry. Do this first for the
   corridors used by incidents and signals.
3. Use micro mode for roundabout queue/delay analysis because meso does not
   model lateral movement explicitly.
4. Validate entry delay, circulating flow, exit throughput, and the 95th
   percentile queue against observed data or a documented field audit.
5. Do not force a generic speed on every ring edge. Current ring-edge speed
   values are mostly inherited/defaulted and require source review.

## Detailed Corridor Architecture

### Inputs

- the exact calibrated demand build ID and fixed seeds;
- a selected incident or junction/roundabout corridor;
- a spatial boundary: affected approaches plus upstream/downstream buffer;
- a warm-up, measured period, and drain period;
- a named signal plan with provenance;
- boundary arrivals derived from the same citywide demand/simulation result;
- a no-double-count rule: each vehicle is represented once in a metric.

### Execution

1. Run fast citywide meso as today to screen a closure and identify affected
   corridors.
2. Select the smallest corridor that includes all likely queue/spillback paths.
3. Construct boundary inflow from the calibrated routes for that time period;
   validate conservation at every boundary.
4. Run the bounded micro scenario with warm-up, exact signal plan, and targeted
   queue/speed detectors.
5. Return corridor diagnostics alongside, never silently replacing, citywide
   meso flow colours.

### UI meaning

- **Fast flow result:** 15-minute citywide flow and closure routing; label it
  mesoscopic and show its confidence.
- **Detailed corridor result:** individual cars, phase trace, queue length,
  speed, delay, stops, and roundabout/junction diagnostics; label it
  microscopic and show its signal-plan provenance.

This avoids a misleading UI where visually detailed cars imply a level of
accuracy the underlying citywide flow model does not have.

## Performance Plan: Improve Without Making Results Worse

### Non-negotiable baseline

Before every change, record on a fixed one-day scenario:

- wall time for demand build, each SUMO seed, aggregation, trajectory export,
  and publication;
- peak RSS/CPU use;
- inputs/build ID/SUMO version;
- sensor fit and held-out validation;
- route/OD structural checks;
- loaded, inserted, arrived, waiting, running, teleports, and route errors;
- output byte/hash comparison where a refactor is meant to preserve behaviour.

The current demand profile shows interval solving as the dominant cost and
already uses a flat worker pool. There is no safe evidence for a simple
vectorization or "use more cores" change. Optimise only after measurement.

### Low-risk speed work

1. Fix the P0 parsing, release, and health gates first. They add validation,
   not simulation work.
2. Use content-addressed caching for immutable network, signal-plan, detector,
   and candidate artifacts. Cache keys must include the full build ID; never
   reuse a date-only cache.
3. Benchmark bounded seed parallelism versus sequential seeds. Use a single
   process budget across PFE workers and SUMO seeds so the machine is not
   oversubscribed. Adopt it only if measured runtime and results both pass.
4. Keep micro output targeted and periodic. Do not write full-city per-step
   FCD/queue data for normal runs.
5. Keep trajectories opt-in for multi-day scenarios and retain the compact
   format only after the route-reconciliation gate passes.
6. Version-pin SUMO for benchmark and production runs. A SUMO upgrade gets its
   own validation run; it is not a free performance change.

### What not to do

- Do not enable full meso signal control globally while plans are guessed.
- Do not use meso rankings to select a signal plan unless a controlled study
  proves agreement with the corresponding micro ranking.
- Do not calibrate car-following/lane change settings from the six counters.
- Do not make a detailed micro result overwrite a citywide meso result.
- Do not claim a speed-up unless the same validation gate and run-health gate
  pass at the new measured runtime.

## Release and Robustness Design

Build one immutable release directory per demand/scenario job:

```text
releases/<build-id>/
  manifest.json
  demand/
  network/
  signal-plan/
  scenarios/
  trajectories/
  validation/
  per-seed-health/
```

The manifest must include hashes, source date, time range, seeds, direction
variants, purpose/OD configuration, network, signal-plan provenance, SUMO
version, code commit, metrics, and gate results. Only atomically switch a
small `current-release.json` pointer when all gates pass. On failure or
cancellation, leave the current release untouched.

The publication policy must classify each check as `block`, `warn`, or
`informational`. At minimum, missing health output, a failed seed, unverified
signal plan, required artifact absence, hard-constraint violations, and a
build-ID mismatch are blocking errors.

## Acceptance Gates

| Area | Required evidence before publication |
| --- | --- |
| Fast citywide mode | No more than 5% runtime regression, same input/build ID, all seed-health records present, no failed health gate, sensor/held-out validation no worse than baseline. |
| Trajectories | 100% reconciliation of eligible inserted vehicles; rerouted, unfinished, off-map, and rejected vehicles counted separately. |
| Signal plan | Provenance recorded; phase/link validation passes; paired multi-seed baseline comparison; no new health failure; queue spillback and safety gates pass. |
| Queue result | Per-approach/lane definition, warm-up sensitivity, measurement period, queue length/time output, and spillback policy are recorded. |
| Speed result | Speed/lane source provenance; held-out travel-time/speed validation; parameter uncertainty reported. |
| Roundabout result | Source-to-SUMO mapping audit; priority/turn/lane/speed check; entry delay, circulating flow, exit throughput, and queue-tail validation. |
| Release | Complete immutable inputs/outputs and hashes; all q10/q50/q90 variants used by the ensemble pass their own gates. |

## Recommended Delivery Order

1. Repair P0 trajectory parsing, health fail-closed behaviour, orphan recovery,
   and per-job workspaces. Add regression tests and keep normal runtime
   unchanged.
2. Make demand/variant/release fingerprints complete. Gate every uncertainty
   variant and atomically publish one release pointer.
3. Build the network audit: signals, short approaches, lane direction/turns,
   speed provenance, and roundabout mapping. Fix only reviewed geometry.
4. Correct the signal optimization time window and add warm-up/measurement/
   drain semantics. Keep all results synthetic.
5. Build one golden microscopic corridor around a real junction/roundabout.
   Add queue/speed/phase outputs and a repeatable benchmark.
6. Add detailed-corridor jobs and UI. Preserve the fast citywide path exactly.
7. Import City signal data, validate mappings, and only then permit a
   `city-configured` signal plan to be evaluated as a recommendation.
8. Expand corridor coverage only after each previous corridor meets the
   acceptance gates.

## Research Sources

- [SUMO mesoscopic model, queue and traffic-light limitations](https://sumo.dlr.de/docs/Simulation/Meso.html)
- [SUMO traffic lights and importing real programs](https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html)
- [SUMO queue output](https://sumo.dlr.de/docs/Simulation/Output/QueueOutput.html)
- [SUMO lane-area detectors](https://sumo.dlr.de/docs/Simulation/Output/Lanearea_Detectors_%28E2%29.html)
- [SUMO road-network roundabout and priority semantics](https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html)
- [Swedish Transport Agency TSFS 2014:30 traffic-signal rules](https://www.transportstyrelsen.se/tsfs/TSFS%202014_30.pdf)

## Bottom Line

The normal simulation can stay fast and become more robust immediately through
the P0 release/trajectory fixes. Accurate traffic lights, queues, dynamic
speed, and roundabouts require a separate, bounded microscopic corridor
workflow plus verified network and controller inputs. The project should not
claim that guessed signal programs are Gothenburg's real lights, but it can
build the correct technical path now and preserve normal interactive
performance while doing so.
