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

### A — Intake (`build_data.py`) — built
Validate 15-min CSVs; join coordinates + **measured-direction metadata**
(`data_in/sensors.csv` — the delivered "Total" label is proven unreliable);
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
  - **E-I / I-E / I-I** — PAIRED tours (the return leg is the same tour's
    second half, not a fresh sample — this is what makes AM/PM directional
    balance structural). Purpose sampled from RVU's split (43/33/24 %);
    destination drawn from that purpose's activity mass with a gravity
    deterrence.
  - **HOME mass** = real 2023 population per DeSO zone (`fetch_deso.py`:
    SCB open WFS for DeSO-2025 boundaries + PXWeb API for population,
    both live, no key needed), spread over each zone's residential-street
    length. 116 of 129 inner-city DeSO zones have data (13 are brand-new
    2025-boundary splits not yet back-filled by SCB — disclosed, not
    silently dropped).
  - **ACTIVITY mass** = OSM POIs in 3 categories matching RVU's purpose
    categories (arbete/service/fritid). DOCUMENTED PROXY: true workplace-
    location microdata (RAMS) was checked and confirmed NOT free below
    kommun level (SCB's day-population-by-workplace tables stop at 312
    kommun codes) — POI density is the honest substitute, same spirit as
    the road-attribute proxies used elsewhere in this project.
  - **Departure times** = our OWN measured `normal_profile.json` shape
    (finer-grained than RVU's coarse bins, and independently consistent
    with RVU's reported AM 7-8h / PM 16-17h peaks — a genuine cross-check,
    not a coincidence, since both measure the same city).
  - **θ (through_fraction, gravity_km)**: a 3×3 bounded grid search
    (`calibrate_theta.py`) — the proportionate version of simulation-based
    calibration (SPSA/metamodel methods, see references) for exactly 2 free
    parameters. FINDING: GEH-based scoring on a fast morning window
    SATURATED at 100 % for all 9 combinations — it only checks fit at
    MEASURED edges, which the PFE achieves regardless of candidate
    composition given enough route diversity; it cannot discriminate θ.
    Replaced with a trip-length fit against RVU's measured distance bins
    (table 2, p.12): gravity_km=2.6 minimizes the over-concentration of
    unrealistically short trips (21 % in 0–1 km vs 32 % at gravity_km=1.0;
    RVU's own share is 9 %, but genuine >10 km trips are structurally
    absent from a 2 km-wide canvas — through-trips absorb that mass by
    construction). through_fraction has NO local ground truth to
    discriminate it against — frozen at 0.5, disclosed as an unidentifiable
    neutral prior, not a calibrated value.
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
  - KNOWN GAP: `validate_sim.py` does not wire in `corridor_priors` from
    observability.json (only `prior_flows.json`'s direction priors) — the
    LOSO figures above are a slight underestimate of the deployed
    pipeline's actual behaviour. Fix alongside the next Agent F work.

### D — Forecast (`train_agent1.py`, `build_agent1_flows.py`) — built
Per-station baseline + holiday factors; beats seasonal-naïve +12–29 %.
For future dates, C consumes D's series instead of history — same code path.

### E — Simulation (`build_sumo_net.py`, `run_scenario.py`, `serve.py`) — built
SUMO **mesoscopic** Monte Carlo (43× faster than micro at equal/better
sensor delivery, 0.87–0.96), identical edge IDs, local rerouters for
closures, interactive API. Gate: baseline delivery ≥ 0.85 at stations.

### F — Confidence (`validate_sim.py`) — CORE BUILT
LOSO results (2026-07-05, whole day): the program recovers a median 32 %
of a hidden station's traffic (range 0.06–0.83). The spread IS the
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
5. data_in/sensors.csv metadata file (A's last hard-coding removed).

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
