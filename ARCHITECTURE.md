# Architecture — the definitive structure

**Product contract:** the city drops 15-minute count data (any number of
stations, any mix of directional/two-way) into the program and gets back
(1) a simulation of the measured period, (2) a simulation of any future
date, and (3) "what if we close these streets" — with an honest, per-street
statement of how trustworthy each answer is. **Every added station must
improve all three outputs without code changes.**

The program is six stages. Each stage has one job, fixed input/output
contracts, and a validation gate that must pass before the next stage may
consume its output. Stages only communicate through their contracts.

```
  data_in/                      (city drops files here)
     │
 [0] INTAKE          → network.geojson, flows.json
     │
 [1] OBSERVABILITY   → observability.json  (derived flows, consistency, classes)
     │
 [2] FORECAST        → flows_forecast.json (any future date)
     │
 [3] DEMAND          → calibrated routes ×3 variants, od_matrix
     │        (source = historical flows OR forecast — same code path)
 [4] SIMULATION      → scenarios/*.json    (baseline / closures, Monte Carlo)
     │
 [5] CONFIDENCE      → per-edge confidence, empirical error curve
     │
  web/ + serve.py               (the window into all of it)
```

## Invariants (violating any of these is a bug, everywhere)

- **One ID space.** Edge id `u_v_k` is identical in the map, the flow files,
  the SUMO network and every scenario. Never re-derive, never remap.
- **One coordinate system.** WGS84 on every interface; SWEREF99 12 00 only
  inside metric computations.
- **Time is absolute.** Epoch + 15-min index; never row order, never local
  conventions. Epoch strings are ISO with 'T' and parsed as UTC.
- **Directions are metadata, not guesses.** Each station's measured
  direction(s) come from the city (sensors metadata file). The delivered
  "Total" label has been proven unreliable — the metadata file is the truth.
- **Missing ≠ zero.** Unmeasured is `null` end to end and rendered as such.
- **Estimates are labelled.** Every derived number carries how it was
  obtained (measured / derived-by-conservation / forecast / simulated) and a
  confidence.

## Stage 0 — INTAKE (`build_data.py`)

*Job:* turn the city's files into a validated network + flow arrays.

Steps:
1. Parse & validate 15-min CSVs (schema, timestamp grid, duplicates, DST).
2. Join station metadata: coordinates + **measured direction(s)** —
   `data_in/sensors.csv` (matplats, direction: `Total|N|S|O|V|NO|SO|SV|NV`).
   A station without metadata is REJECTED with an actionable message, not
   guessed at.
3. Direction-aware snap to the OSM graph (edge bearing must match the
   letter; opposite-carriageway search ≤ 80 m; true point-to-polyline
   distances). Total → both directed edges; letter → exactly one.
4. Emit `network.geojson`, `flows.json`, `graph.graphml` (frozen graph).

Gate: every station snapped ≤ 60 m with matching bearing; every flow array
exactly N_QUARTERS long; daily means within ±15 % of the city catalogue
value when one is known (sanity anchor).

Scaling: new station = new rows in two files in `data_in/`. Nothing else.

## Stage 1 — OBSERVABILITY (new: `observability.py`)

*Job:* extract everything the measurements imply **before** any modelling —
the user-visible version of "at this junction I can compute the unmeasured
flow from the others". Formally the link-flow observability problem
(Castillo et al. 2015): flow conservation at junctions gives a linear
system relating measured and unmeasured directed edges.

Steps:
1. Build the junction incidence system from graph.graphml + the measured
   edge set.
2. Classify every directed edge: `measured` / `derived` (uniquely determined
   by conservation, e.g. a segment between two sensors with no entrances) /
   `bounded` (inequality constraints only) / `unobserved`.
3. Compute derived flow series for `derived` edges, with propagated
   measurement uncertainty (errors accumulate through the system — carry
   them, don't hide them).
4. Consistency check: conservation residuals BETWEEN sensors (e.g. two
   stations on the same street). Large residuals = data problem alarm at
   intake, before it poisons calibration.

Output: `observability.json` — classes, derived series ± uncertainty,
residual report.

Gate: residuals within tolerance; derived series non-negative.

Scaling: **this is where density pays off fastest** — each new station flips
edges from `unobserved` to `derived`/`bounded` and tightens the residual net.

## Stage 2 — FORECAST (`train_agent1.py`, `build_agent1_flows.py`)

*Job:* learn each station's normal rhythm; produce flows for any future date.

Steps: per-station LightGBM baseline (non-holiday) → holiday factors
(actual/baseline per slot) → export any target year in the flows.json
format.

Gate: leave-weeks-out CV must beat seasonal-naïve per station; holiday
factors spot-checked. (Current: +12–29 % on all stations.)

Scaling: automatic — one model per station.

## Stage 3 — DEMAND (`build_sumo_demand.py`)

*Job:* one network-wide traffic demand consistent with every measurement.

Steps:
1. **Select source: historical `flows.json` or Stage-2 forecast** — the same
   calibration code path; this is what "simulate the future" means.
2. Build per-edge 15-min targets: measured edges directly; `derived` edges
   from Stage 1 (down-weighted by their uncertainty); Total pairs split by
   the measured D-factor where the city provides one.
3. Candidate route pool (randomTrips) → routeSampler LP calibration.
   Route continuity guarantees conservation by construction; Stage-1 turn
   relations can be added as `--turn-files` constraints when available.
4. Emit calibrated routes at the q50/q10/q90 uncertainty variants +
   the implied OD matrix (zones), labelled as one plausible OD.

Gate: GEH < 5 at 100 % of constrained edges, all intervals, all variants.

Scaling: every station (and every Stage-1 derived edge) is one more
constraint on the same LP — calibration tightens automatically.

## Stage 4 — SIMULATION (`build_sumo_net.py`, `run_scenario.py`, `serve.py`)

*Job:* run the demand and answer what-if questions.

Steps: SUMO net from the frozen graph (identical edge IDs) → mesoscopic
Monte Carlo (seeds × demand variants; meso because the product is 15-min
edge flows — measured 43× faster than micro with equal-or-better delivery)
→ closures via local rerouters (≤ 400 m) → scenario JSON in the flows
format. Interactive path: `/api/close` shells out to the same CLI.

Gate: baseline simulated/measured ≥ 0.85 at every station; closed edges
< 20 % of baseline flow; scenario contract tests green.

## Stage 5 — CONFIDENCE (`validate_sim.py` — to build; placeholder exists)

*Job:* the honesty layer — a per-edge answer to "can I trust this here?".

Steps:
1. **Leave-one-station-out:** recalibrate without station i, simulate,
   measure the error at station i. Repeat for all stations.
2. Fit the empirical error curve vs (distance to nearest constraint,
   observability class, road class). This REPLACES the exp(-d²) placeholder.
3. Per-edge confidence = curve(edge) × Monte Carlo spread factor;
   `derived` edges inherit Stage-1 uncertainty; `unobserved` edges honestly
   low.
4. Publish into scenario files; the map already renders it.

Gate: the curve is monotone and cross-validated; no edge reports higher
confidence than the nearest measured edge of its class.

Scaling: with 6 stations the curve is coarse; every added station is a new
validation point — **the program gets more honest as it grows.**

## Status vs this architecture

| Stage | Status |
|---|---|
| 0 Intake | Built; **metadata must move from code to `data_in/sensors.csv`** |
| 1 Observability | **Not built** (the intersection insight lives here) |
| 2 Forecast | Done |
| 3 Demand | Built for historical; **forecast source not wired** |
| 4 Simulation | Done (meso, interactive, multi-closure) |
| 5 Confidence | Placeholder; **leave-one-out + curve not built** |

Build order: 0-metadata → 3-forecast-source → 1-observability → 5-validation.

Parked studies (kept in repo, not on the critical path): `dirsplit/`
(direction-split transfer model — superseded by city-provided direction
metadata; keep as a validated negative result), `estimate_directions.py`
(Gaussian fallback), `build_dataset.py` (GNN prep).

## References

- Castillo et al., *A State-of-the-Art Review of the Sensor Location, Flow
  Observability, Estimation, and Prediction Problems in Traffic Networks*,
  J. Sensors 2015 — the observability formalism behind Stage 1.
- SUMO docs: *Routes from Observation Points* / *Turns* — routeSampler's
  LP count-matching and turn-count constraints (Stage 3).
- FHWA Traffic Analysis Toolbox / GEH criterion — the Stage-3/4 gates.
