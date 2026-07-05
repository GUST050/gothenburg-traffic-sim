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

### B — Mathematics (`observability.py`) — TO BUILD (next)
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
Gate: GEH < 5 at 100 % of measured edges; no B-bound violated.

### D — Forecast (`train_agent1.py`, `build_agent1_flows.py`) — built
Per-station baseline + holiday factors; beats seasonal-naïve +12–29 %.
For future dates, C consumes D's series instead of history — same code path.

### E — Simulation (`build_sumo_net.py`, `run_scenario.py`, `serve.py`) — built
SUMO **mesoscopic** Monte Carlo (43× faster than micro at equal/better
sensor delivery, 0.87–0.96), identical edge IDs, local rerouters for
closures, interactive API. Gate: baseline delivery ≥ 0.85 at stations.

### F — Confidence (`validate_sim.py`) — TO BUILD
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
