# Architecture — locked 2026-07-05

**Product contract:** the city drops 15-minute count data (any number of
stations, directional or two-way) into the program and gets back (1) a
simulation of the measured period, (2) a simulation of any future date, and
(3) "what if we close these streets" — with a per-street statement of how
trustworthy each answer is and *where that number came from*. Every added
station must improve all three outputs without code changes.

## The estimation hierarchy (the core idea)

Every quantity the program reports is produced at the highest possible level
of this hierarchy, and its provenance is carried all the way to the map:

1. **MEASURED** — a sensor's value. Never negotiated.
2. **MATHEMATICALLY DETERMINED** — flow conservation at junctions yields
   exact values where the system is locked, and intervals (min/max bounds)
   where it is not. Also yields consistency alarms between sensors.
   (Literature: link-flow observability, Castillo et al. 2015.)
3. **LEARNED PRIOR, INSIDE THE BOUNDS** — for what remains: the expected
   pattern for "a street of this type at this hour", learned from cities
   where directions/volumes ARE measured (Norwegian open directional data
   today — the validated dirsplit model; FCD fusion when the city licenses
   probe data). Soft pull, never overrides levels 1–2. (An informative
   prior is REQUIRED for identifiability — Marzano et al.)
4. **RECONCILIATION** — a single convex program selects concrete vehicle
   routes that (a) match level 1 exactly (within GEH tolerance), (b) never
   violate level 2, (c) stay close to level 3, with an entropy-flavoured
   preference against unwarranted structure. This is a modern
   **Path Flow Estimator** (Bell & Shield 1996; Chen et al. 2009 — the
   single-level formulation that explicitly tolerates counts on only part
   of the network, supports inequality constraints, and has published
   confidence-interval theory).

Flows on unmeasured streets are the OUTPUT of level 4 — never an input
guess. Their trustworthiness is measured (stage F), not presumed.

## The six agents

```
 data_in/ (city drops files)
    │
 [A INTAKE]        CSVs + coords + direction metadata → network.geojson, flows.json
    │
 [B MATHEMATICS]   conservation → exact values, intervals, consistency alarms
    │
 [C DESTINATIONS]  PFE-lite: candidates + hard counts + B's bounds + learned
    │              prior → vehicle routes + OD matrix     ← [D FORECAST] for
    │                                                        future dates
 [E SIMULATION]    SUMO meso Monte Carlo → flows everywhere, closures
    │
 [F CONFIDENCE]    provenance + leave-one-station-out error curve + MC spread
    │
 web/ + serve.py   (the window; interactive closures)
```

## Code organization

The repository keeps two deliberately different kinds of Python files
separate:

- **Root-level files are stable CLI entrypoints.** The build order, Makefile,
  run manifests and content fingerprints refer to commands such as
  `build_data.py`, `build_sumo_demand.py`, `run_scenario.py` and `serve.py`.
  Their paths are therefore treated as an interface and are not moved just
  to make the tree look tidy.
- **Reusable implementation lives in `traffic_sim/`.** Cross-cutting
  contracts and fingerprints are in `traffic_sim/core/`; sensor intake is in
  `traffic_sim/intake/`; demand calibration, the PFE solver/kernel and cache
  code are in `traffic_sim/demand/`; held-out validation and confidence
  reporting are in `traffic_sim/confidence/`; SUMO runtime, metadata, network
  audits and disruption metrics are in `traffic_sim/simulation/`; run/release
  bookkeeping is in `traffic_sim/ops/`.

The former root modules (`study_contracts.py`, `pipeline_fingerprint.py`,
`sensor_registry.py`, `candidate_cache.py`, `closure_metrics.py`,
`sumo_network_metadata.py`, `sumo_runtime.py`, `network_audit.py`,
`release_registry.py`, `runs.py`, `pfe.py`, `pfe_kernel.py`,
`validate_sim.py` and `validation_report.py`) are now thin compatibility
imports or CLI wrappers. They contain no second implementation. This preserves
existing scripts, tests and external command examples while giving new code
one canonical import location.
New reusable code must be added to `traffic_sim/` first; a root shim is added
only when an existing public command or import still requires it. Fingerprint
maps hash the canonical package files, so changing an implementation invalidates
the relevant cache or published build.

### Demand build identity

`DemandBuildSpec` is the boundary contract for recalibration. It carries the
start date, historical/forecast source, consecutive-day range, effective
window, and fixed structural-reference date. The API archives it before the
background job; `build_sumo_demand.py` validates any legacy flags against the
same object and writes the canonical copy to `sumo/demand_build_spec.json` only
after calibration succeeds. Its content key and all demand-affecting solver
options participate in the demand fingerprint. A scenario may be published
only when its `build_id` and `demand_build_key` match `demand_meta.json`; a
failed replacement therefore leaves the previous demand/scenario release
coherent and serving.

Golden releases extend that same registry under `runs/releases/`; they copy
only explicitly named case-artifact bundles and activate through one atomic
pointer. A normal/closure bundle keeps its scenario, representative trajectory
and exact route inputs under one integrity gate; it must not freeze a scenario
JSON while leaving referenced dependencies mutable. Neither demand nor
scenario runs may discover release inputs by globbing the shared `sumo/` or
`web/data/scenarios/` directories.

The domain packages `demand/` and `dirsplit/` remain separate because they are
model-specific pipelines with their own data contracts. `web/` is the browser
runtime, `tools/` contains bounded experiments, `tests/` contains contract and
regression tests, and generated artifacts stay under `web/data/`, `sumo/`,
`runs/` or `cache/` rather than in source packages.

### A — Intake (`build_data.py`) — built
Validate 15-min CSVs; join coordinates + **measured-direction metadata**
(`data_in/sensors.json` — the delivered "Total" label is proven unreliable);
direction-aware snapping (bearing must match; true point-to-polyline
distances). Gate: every station snapped ≤ 60 m with matching bearing.

### B — Mathematics (`observability.py`) — BUILT
From graph + measured edge set:
1. Junction solves: where all-but-one leg of a junction is constrained,
   derive the remainder exactly (the Läraregatan/Gibraltargatan case).
2. Interval bounds: per unmeasured edge, min/max flow LPs subject to
   conservation (with small slack for driveways/parking), measured values,
   non-negativity and a lane-capacity cap. (Scandinavium-side case: too
   open for exact solves, still yields bounds.)
3. Corridor consistency alarms: residuals between stations sharing a street
   (107 vs 1076 on Skånegatan) — a data-quality gate, run at intake time.
Output: `observability.json` (exact values, intervals, alarms, classes).

### C — Destinations (PFE-lite, evolves `build_sumo_demand.py`)
1. Candidate routes: randomTrips pool, **weighted by the prior** —
   gravity-style OD plausibility (SCB population/workplaces) and the
   learned street/time profiles (dirsplit model, in its correct role).
2. Solve the PFE LP (scipy): route-use variables ≥ 0; hard equalities at
   measured edges (per 15 min, per direction where known); inequalities
   from B; L1 deviation terms pulling edge flows toward level-3 priors with
   weights ∝ 1/uncertainty; total-count regularisation.
3. Emit routes (q50 + uncertainty variants) + the implied OD matrix.
routeSampler is kept as reference implementation/fallback (it cannot
express per-edge weights or intervals — verified against its docs).
BUILT (pfe.py + prior_flows.py + --engine pfe): unserveable counts are
dropped per-constraint (never fatal to an interval) and a relaxation
ladder (tol ×2, ×4, then without level-2 bounds) guarantees non-empty
intervals. Gate: GEH < 5 at ≥ 85 % of measured hourly values — the FHWA
calibration criterion. Current whole-day: 92–93 % GEH, delivery at
measured edges mean 0.88, opposite-direction priors delivered 0.64–1.01.

### C — Candidate generation (`build_candidates.py`) — GROUNDED (2026-07-05)
The route-candidate pool (what PFE selects among) is now the standard
**subarea/cordon** structure (FHWA/state-DOT subarea-analysis practice;
Cascetta's quasi-dynamic OD) with REAL data at every endpoint, replacing
uniform `randomTrips`:
  - **E-E** (through) — gate→gate, gate weight ∝ approach-road class
    (motorway/trunk draw more than a residential fringe street — the only
    local proxy available; no external cordon counts exist to calibrate
    gate weights better — disclosed limitation).
  - **E-I / I-E / I-I** — PAIRED candidate-tour support (the return leg is
    generated from the same tour's second half, not a fresh sample, providing
    an AM/PM directional prior). PFE then calibrates aggregate 15-minute
    trip uses independently, so the final vehicle animation does not claim
    persistent individual return journeys. CORRECTED 2026-07-09: this line described E-I/I-E
    as already working from this doc's original 2026-07-05 lock — false;
    only I-I (both ends internal) was ever implemented until 2026-07-08/09,
    which structurally capped tour length at the canvas's own diameter (see
    the θ entry below). E-I/I-E (one end a boundary gate) are now real,
    added via `--cross-fraction` (disclosed neutral prior, same status as
    through_fraction).
    Purpose is sampled from RVU's split (43/33/24 %, its WEEKLY average —
    Fig.11 has no day-type qualifier), but NOT as one flat number: split
    into weekday/weekend/holiday profiles AND by hour of departure (e.g.
    08h is ~81 % arbete on a weekday, 20h ~35 % fritid) — triangulated from
    RVU's total plus two external sources with real weekday/weekend/hourly
    granularity (NHTS 2017, UK NTS 2019), solved so the annual average
    reproduces RVU's 43/33/24 exactly (`build_candidates.PURPOSE_HOURLY_
    WEEKDAY/WEEKEND`). Destination drawn from that purpose's activity mass
    with a gravity deterrence.
  - **HOME mass** = real 2023 population per DeSO zone (`fetch_deso.py`:
    SCB open WFS for DeSO-2025 boundaries + PXWeb API for population,
    both live, no key needed), spatialised to anonymous residential-building
    footprints and their nearest usable road access. Building footprint area
    and levels are only a within-zone capacity proxy; the SCB zone total stays
    fixed for every DeSO with routable access. An official
    `data_in/deso/buildings.geojson` takes precedence (WGS84/EPSG:4326
    GeoJSON, longitude/latitude order); cached OSM footprints are the open
    fallback. A DeSO with no edge in the inner-city graph is
    explicitly reported and is not fabricated as an interior home. 116 of
    129 inner-city DeSO zones have data (13 are brand-new 2025-boundary splits
    not yet back-filled by SCB — disclosed, not silently dropped). Primary
    sources: [SCB DeSO open geodata](https://www.scb.se/vara-tjanster/oppna-data/oppna-geodata/demografiska-statistikomraden-deso/)
    and [Lantmäteriet Byggnad](https://www.lantmateriet.se/sv/nationella-geodataplattformen/datamangder/byggnad/),
    whose schema supplies building geometry, purpose and area when a complete
    official delivery is available.
  - **ACTIVITY mass** = OSM POIs in 3 categories matching RVU's purpose
    categories (arbete/service/fritid), each mapped once to its nearest
    usable road access rather than diffused across a broad road halo.
    DOCUMENTED PROXY: true workplace-
    location microdata (RAMS) was checked and confirmed NOT free below
    kommun level (SCB's day-population-by-workplace tables stop at 312
    kommun codes) — POI density is the honest substitute, same spirit as
    the road-attribute proxies used elsewhere in this project.
  - **Departure times** = our OWN measured `normal_profile.json` shape
    (finer-grained than RVU's coarse bins, and independently consistent
    with RVU's reported AM 7-8h / PM 16-17h peaks — a genuine cross-check,
    not a coincidence, since both measure the same city).
  - **θ (through_fraction, gravity_km, cross_fraction)**: a 3×3 bounded grid
    search (`calibrate_theta.py`) — the proportionate version of
    simulation-based calibration (SPSA/metamodel methods, see references)
    for a small number of free parameters. FINDING: GEH-based scoring on a
    fast morning window SATURATED at 100 % for all 9 combinations — it only
    checks fit at MEASURED edges, which the PFE achieves regardless of
    candidate composition given enough route diversity; it cannot
    discriminate θ. CORRECTED 2026-07-09: this doc (and calibrate_theta.py's
    own commit message) had claimed since 2026-07-05 that GEH-scoring was
    "replaced with a trip-length fit against RVU's measured distance bins"
    — that fit was never actually implemented until 2026-07-08; re-running
    it for real gave gravity_km=2.6 as the best of the original 3×3 grid
    (L1=0.5947 against RVU's short bins), though a wider manual sweep found
    gravity_km=12 fits marginally better (L1=0.5812) — an interior optimum,
    not monotonic. HARD CEILING, verified by direct measurement (not the
    "2 km-wide canvas" this doc previously and incorrectly claimed): this
    network's own diameter — gate-to-gate (E-E) AND gate-to-interior (E-I/
    I-E) alike — never exceeds ~7.8 km. RVU's 5.1-10km/>10km bins (51 % of
    all real trips) describe a WHOLE-REGION survey including long regional
    commutes; most of a trip like that happens on roads outside this graph
    entirely, so no θ value can close this gap — it is a scope mismatch
    (distance-within-this-graph vs. real door-to-door distance), not a
    mistuned parameter. E-I/I-E tours (added 2026-07-08/09, see above) are
    a genuine, if modest, improvement on this front (5.1-10km share:
    ~1-4 % I-I-only → 8.4 % E-I/I-E-only) since a gate-anchored end can span
    the full canvas diameter, unlike a purely internal I-I tour.
    through_fraction and cross_fraction have NO local ground truth to
    discriminate them against — frozen at 0.5 and 0.3, disclosed as
    unidentifiable neutral priors, not calibrated values.
  - **Validation — controlled A/B via `--legacy-random-pool`**: median LOSO
    recovery on the SAME city-scale network, same corridor coupling, same
    shape pool: grounded 0.093 vs legacy uniform pool 0.076 — a modest
    (+22 % relative) but genuine improvement, dominated by one edge (107's
    toward-centre direction, 0.95 vs 0.06) with a mixed, noisy picture
    elsewhere (n=7 measured directed edges). NOT a leakage artifact:
    verified `validate_sim.py` does not consume `corridor_priors` at all
    (a real gap vs the deployed pipeline, noted below).
    CONFOUND WARNING for anyone reading project history: an EARLIER LOSO
    number (0.32 median) was measured on the small two-cluster network,
    before the city-scale expansion, corridor coupling, and the PFE
    shape-pool fix — it is NOT comparable to the 0.09/0.093 figures above,
    which are both on today's full inner-city network. Recovery is
    structurally harder at city scale (vastly more unconstrained
    alternative paths) — lower numbers here are not a regression.
    STALE AS OF 2026-07-09 — PENDING RE-VALIDATION: every LOSO figure on
    this page (0.093/0.076 here, 0.09/0.154 in C.1, 0.32 in section F) was
    measured BEFORE E-I/I-E tours and hour/day-type-aware purpose sampling
    existed — the candidate-generation mechanism these numbers describe
    has materially changed since. Not yet re-run (a real compute cost, a
    deliberate choice to defer rather than guess at updated figures) — do
    not treat these as the current system's validated behaviour until
    `validate_sim.py` is re-run against the current pipeline.
  - FIXED 2026-07-09: `validate_sim.py` now wires in `corridor_priors`
    (`corridor_priors_for_fold()`, excluded per-fold whenever either anchor
    sensor is the one being held out — same leakage-prevention principle
    as `prior_flows.json`'s direction priors). The underlying mechanism was
    already fully general (scans every PAIR of measured sensors, no
    hardcoded IDs — new stations get corridor priors automatically), so
    this was a validation-ACCURACY gap only, not a scalability one; the
    real, deployed pipeline already had this. The LOSO figures above still
    predate the fix (see PENDING RE-VALIDATION above) — re-running will
    now also reflect corridor coupling, closing this specific
    underestimate alongside the E-I/I-E and hour/day-type-purpose changes.

### C.1 — Why grounding barely helped, and the fix that did (`assignment_priors.py`)
Gustav asked directly: why did realistic OD grounding only move LOSO from
0.076 to 0.093? Root-caused, not guessed: **`pfe.py`'s objective minimises
Σ x_r · EPS_PARSIMONY (total vehicle count) with NO offsetting pull on any
edge that carries no active hard constraint or soft prior.** A route only
gets LP weight if it serves an active constraint. `build_candidates.py`'s
population/POI grounding shapes WHICH routes exist in the candidate pool,
but does nothing to make the PFE actually USE routes through an edge unless
that edge (or one on the same route) is itself constrained. Confirmed
directly: station 107 recovered brilliantly in LOSO (0.95) because it sits
on paths between OTHER active constraints (the corridor-coupled 1076);
isolated stations (1074, 1076, 134) collapsed toward zero because nothing
pulled weight onto routes through them once their own count was hidden.
This is the classic 4-step transport model's missing 4th step — **traffic
assignment** — and the standard fix (Dial's stochastic multipath loading,
gravity-distributed) is what `assignment_priors.py` implements: gravity-
distribute the SAME home/activity masses across many sampled OD pairs
(now including E-E gate pairs, not just tours), route each via shortest
path, accumulate a loaded-flow field that is non-zero on every reachable
edge, and feed it in as a weak, wide-margin signal for edges no stronger
source covers.

THREE REAL BUGS FOUND AND FIXED DURING DEVELOPMENT (each verified with a
before/after diagnostic, not just reasoned about):
  1. **Length vs. time weighting.** First cut used physical edge length for
     shortest-path routing. Real route choice minimises travel TIME —
     length-weighting sent the assignment down slow residential shortcuts
     instead of the arterials sensors actually sit on.
  2. **All-or-nothing vs. stochastic multipath.** Even with time-weighting,
     a single deterministic shortest path per OD pair put ZERO load on 6 of
     7 sensor edges (verified directly) — all traffic collapses onto one
     canonical route, missing "good but not literally fastest" arterials.
     Fixed with Dial's classic mitigation: route each sample through one of
     several (10) randomly-perturbed (lognormal-jittered) travel-time
     graphs, spreading load across realistic alternatives.
  3. **LP intractability.** Feeding the assignment field in as ~6 500 soft
     L1 priors (pfe.py's `priors_pq`) — each costing 2 new LP variables + a
     row — stalled a whole-day solve at 100% CPU for 35+ minutes with zero
     output (killed, confirmed not simply slow). Fixed by feeding it in as
     a **wide interval BOUND** instead (`bounds_pq`, the same free,
     variable-less mechanism level-2 conservation bounds already use:
     [0, 5×assignment-value]) — arguably more honest too, since this field
     is a rough plausibility range, not a confident target. Whole-day solve
     time: 35+ min (stalled) → 7 min.

CALIBRATION, NOT TRANSFER (avoids the volume_priors.py mistake): the scale
converting loading-units to vehicles/day is fit LOCALLY by ROBUST median
ratio (not least-squares — see code comment on why LS with n=7 was unstable)
against our own 6-7 measured edges. No cross-city ML transfer risk.

**FINAL LOSO VERDICT — replicated twice, same direction both times:**
| | median LOSO recovery |
|---|---|
| Grounded OD, no assignment prior | 0.09 (also measured as 0.093 in an earlier run) |
| Grounded OD + assignment prior (bound form) | **0.154** (also 0.15 in the demand-level A/B) |

A genuine, reproducible **+65-70% relative improvement**, achieved with a
mechanistically understood, generalisable fix — not a tuned coincidence.
GENERALISATION TO NEW SENSORS: the assignment field is recomputed from
population/POI/network structure alone (no per-sensor tuning); only the
ONE scale factor is fit against measured edges, so more sensors -> a more
robust fit (and a natural extension to per-road-class factors), while the
sensors ALSO directly tighten level-2 bounds and unlock more corridor
couplings — three independent mechanisms all strengthening together as
the city adds stations, none requiring retraining.
STALE AS OF 2026-07-09 — see the PENDING RE-VALIDATION note in section C:
these figures predate E-I/I-E tours and hour/day-type-aware purpose
sampling.

### D — Forecast (`train_agent1.py`, `build_agent1_flows.py`) — built
Per-station baseline + holiday factors; beats seasonal-naïve +12–29 %.
For future dates, C consumes D's series instead of history — same code path.

### E — Simulation (`build_sumo_net.py`, `run_scenario.py`, `serve.py`) — built
SUMO **mesoscopic** Monte Carlo (43× faster than micro at equal/better
sensor delivery, 0.87–0.96), identical edge IDs, local rerouters for
closures, interactive API. Gate: baseline delivery ≥ 0.85 at stations.

### F — Confidence (`validate_sim.py`) — CORE BUILT
LOSO results (2026-07-05, whole day): the program recovers a median 32 %
of a hidden station's traffic (range 0.06–0.83). CONFOUND WARNING (missing
from this section until 2026-07-09 — an internal inconsistency with
section C's own caveat, which this exact figure is the subject of): this
0.32 was measured on the SMALL TWO-CLUSTER network, before the city-scale
expansion, corridor coupling, and the PFE shape-pool fix — it is NOT
comparable to section C's 0.09/0.093/0.154 figures (all on today's full
inner-city network, and themselves now also pending re-validation against
E-I/I-E + hour/day-type purposes). Kept here for the station-level pattern
it illustrates (which sensors are structurally easy/hard to infer), not as
a current headline number. The spread IS the
observability story quantified: 133 recovers 0.83 (its twin 2276 measures
the same street across the junction), isolated 1074 recovers 0.06 (nothing
infers it). Two consequences, both by design: (1) unmeasured streets carry
LOW confidence unless redundantly sensed — the map must say so; (2) the
parsimony bias (LP under-fills unconstrained streets) is measured, and the
constructive levers are the gravity OD prior and real network-wide level
data (city catalogue ÅMVD / FCD). Learned volume-LEVEL priors were tested
and REJECTED: leave-city-out error factor 4.5–14× (volume_priors.py kept
as the documented negative study) — levels do not transfer across cities,
shares do.
Per-edge confidence = f(provenance level, leave-one-station-out error curve,
Monte Carlo spread, B's interval width). The 107 direction split (city-
measured 52/48) is a standing validation anchor for "can the program infer
an unmeasured direction". Confidence doubles as a **sensor-placement
guide**: the map shows where the next station buys the most certainty.

## Invariants
One ID space (`u_v_k` everywhere) · WGS84 on all interfaces · absolute time
(epoch + 15-min index, ISO-T, UTC parse) · directions are metadata, never
guesses · missing ≠ zero · every number carries provenance + confidence.

## Build order
1. **B — observability module** (junction solves, bounds, alarms).
2. **C — PFE-lite LP** (replaces routeSampler as primary; keeps its I/O).
3. **D→C wiring** (`--source forecast --date …`) — "close a street next year".
4. **F — leave-one-station-out + provenance surfacing.**
5. data_in/sensors.json metadata file (A's last hard-coding removed).

## Parked (validated studies, not on the critical path)
`dirsplit/` transfer model — now level 3's engine for street/time priors;
its leave-city-out validation (λ=0.26, matches the city's measured 52/48 at
107) is the evidence it belongs there. `estimate_directions.py` (Gaussian),
`build_dataset.py` (GNN prep — revisit only at high sensor density).

## Key references
- Bell & Shield (1996); Chen et al. (2009); Yang & Sun — **Path Flow
  Estimator**: single-level convex estimation from partial counts.
- Castillo et al. (2015), *J. Sensors* — link-flow observability.
- Cascetta (1984) GLS; Van Zuylen & Willumsen (1980) entropy; Marzano et
  al. — identifiability: an informative prior is required.
- Cascetta et al. (2013) — quasi-dynamic OD structure for 15-min windows.
- DynaMIT / DYNASMART-X — the estimate-then-predict system archetype.
- TRR 2025 probe-volume estimation; arXiv 2605.09891 — FCD fusion upgrade
  path for level 3.
