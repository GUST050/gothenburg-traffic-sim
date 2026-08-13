# Whole-program architecture review — 2026-08-13

Scope: every Python module, the web runtime, the build graph, the data
contracts and the evidence system. Written from a full read of the tree plus
direct measurement against the committed artifacts (`web/data/network.geojson`,
`web/data/flows.json`, `web/data/scenarios/baseline.json`) and one full
`pytest` execution.

Method note, so the numbers can be trusted or discarded knowingly: the review
container had no `osmnx`, `geopandas`, `lightgbm`, `numba`, SUMO or `traci`.
`numpy`, `scipy`, `pandas`, `shapely`, `networkx` and `pytest` were installed
to run the suite. Every quantitative claim below is either computed from a
committed artifact, from static analysis of the source, or from an executed
test — never from the prose documentation. Where a claim rests on something I
could not execute, it says so.

---

## Part 0 — What the program is, in one paragraph

The city drops 15-minute vehicle counts into `data_in/`. The program turns
seven directed sensor edges' worth of measured traffic into (1) a browsable
animation of the measured year, (2) a forecast of any future date, and (3) a
"what if we close these streets" simulation — each with a per-street statement
of how trustworthy the answer is. The intellectual core is that the middle
step is an **inverse problem**: recovering plausible city-wide vehicle routes
from a radically underdetermined set of counts, done with a Path Flow
Estimator, and then honestly reporting how little that recovery is worth far
from a sensor.

---

## Part 1 — How everything works, in execution order

### The two halves, and the seam between them

The repository holds two halves that never mix, and this separation is the
single best structural decision in the project:

* **Offline (Python, run once)** — reads raw CSVs, produces static JSON.
* **Runtime (browser, static Leaflet app)** — reads that JSON, renders it.

The seam is `flowAt(edgeId, quarterIndex)`, and it worked out better than the
documents describe. `CLAUDE.md` and `provider.js:2` both anticipate a
`ModelProvider` and a `ScenarioProvider` plugging in alongside
`HistoricalProvider`. **Neither was ever needed.** Because all three data
sources share one schema — `{epoch, interval_minutes, flows}` — historical,
forecast *and* scenario data are all loaded by the same `HistoricalProvider`
class:

```js
foreProvider          = await new HistoricalProvider().load('data/flows_forecast.json');  // app.js:377
scenProviders[file]   = await new HistoricalProvider().load('data/scenarios/' + file);    // app.js:510
```

The provider then **self-describes from its payload** —
`this.isScenario = !!payload.scenario` (`provider.js:28`) — rather than being a
distinct subclass. `DeltaProvider` (`provider.js:207`) composes two of them to
answer "what did closing this road change?" without either knowing it is being
compared, and it encodes three honesty rules directly in the seam: changes
below the measured 6–19 % between-seed CV are reported `within_noise` rather
than coloured; edges where the spatial prior is 0 are marked `unclaimable`
rather than hidden; and a minimum baseline volume gates the relative view so
0 → 2 vehicles is not published as +200 %.

Two caveats. First, the renderer does branch on provider *kind*
(`_provider.isScenario` at `render.js:174` and `:601`, `_provider.isDelta` at
`:144`), so the seam is duck-typed rather than a true uniform interface — the
renderer knows more about its source than "it answers `flowAt`". Second, the
docs should be corrected: describing three provider classes that do not exist
sends a reader looking for code that was, correctly, never written.

### Stage A — Intake (`build_data.py`, 35 KB)

**Inputs:** quarterly CSVs in `data_in/`, `data_in/sensors.json` (the validated
station registry), `Mätpunkter_koordinater.csv`.

**What it does, in order:**

1. Parses 15-minute two-way counts for all of 2025.
2. Converts coordinates from **EPSG:3007 (SWEREF99 12 00)** to WGS84. The
   source file is mislabelled "SWEREF99TM"; reconverting as TM is a documented
   trap.
3. Pulls the OSM graph for `INNER_CITY_BBOX` via `osmnx`.
4. **Direction-aware snapping**: a compass-labelled sensor snaps only to an
   edge whose bearing matches the letter, else to the opposite carriageway
   within 80 m. Gate: every station ≤ 60 m with matching bearing.
5. Computes `dist_sensor_m` per edge and `confidence = exp(-d²/2σ²)`.

**Outputs:** `network.geojson` (7,147 LineString edges + nodes, stable `u_v_k`
IDs) and `flows.json`.

**Verified against the committed artifacts:**

| Artifact | Edges | Quarters | Epoch |
|---|---|---|---|
| `network.geojson` | 7,147 | — | — |
| `flows.json` | **7** | 35,040 | `2025-01-01T00:00:00` |
| `flows_forecast.json` | **7** | 35,040 | `2027-01-01T00:00:00` |
| `scenarios/baseline.json` | 7,147 | **96** | `2025-09-16T00:00:00` |

ID-space contract **holds**: `flows.json` ⊆ network, scenario ⊆ network, and
the scenario covers the network exactly. Epoch strings carry no timezone
suffix and `provider.js:16-20` appends `'Z'` before parsing — parse and
`getUTC*` formatting are consistent, as the contract requires.

Data quality is good: nulls run 0.30 %–1.95 % per sensor across the year,
which is the DST gap plus ordinary dropouts, and they are emitted as `null`
(never 0) as the rules demand.

**Why this design:** the "Total" label delivered by the city was proven wrong
for 4 of 5 sensors, so the registry — not the CSV header — is the source of
truth for what a station measures. Snapping is bearing-aware because a
single-direction station snapped to the wrong carriageway silently inverts a
constraint that everything downstream treats as measured truth.

### Stage B — Mathematics (`observability.py`, 16 KB)

From the graph plus the measured edge set, without any behavioural assumption:

1. **Junction solves** — where all-but-one leg of a junction is constrained,
   the remainder is derived exactly.
2. **Interval bounds** — per unmeasured edge, min/max flow LPs subject to
   conservation, measured values, non-negativity and a lane-capacity cap.
3. **Corridor consistency alarms** — residuals between stations sharing a
   street, run at intake time as a data-quality gate.

**Output:** `observability.json` (1.5 MB committed).

**Why this stage exists at all:** it is the only place in the pipeline that
produces numbers with *zero* modelling assumptions. Everything downstream is
allowed to be wrong; stage B is not. That is why its bounds enter the solver
as hard constraints rather than priors.

### Stage D (built before C, consumed by it) — Forecast

`build_features.py` → `normal_profile.json` (the calm September baseline used
for ratio colouring). `train_agent1.py` → per-sensor LightGBM models with a
Holiday Baseline Adjustment, beating seasonal-naïve by 12–29 %.
`build_agent1_flows.py` → `flows_forecast.json` for 2027.

**Why LightGBM and not the GNN:** with seven measured edges there is no
spatial graph to learn over. A GNN is the right family *at sensor density*,
and `build_dataset.py` is parked for that. Shipping the baseline first is the
correct order.

### The direction-split model (`dirsplit/`, 1,522 lines)

A separate, self-contained pipeline solving one problem: the delivered counts
are two-way sums, and direction is not recoverable from geometry plus
conservation.

1. `fetch_norway` — 394 stations of **open hourly directional counts** from
   Statens vegvesen (no API key), 4 ISO weeks each.
2. `match` — station headings are *place names*; resolved by geocoding both
   names and matching against the OSM edge's two axis bearings, requiring
   opposite candidates ≤ 75°. Ambiguous stations are **excluded**, all
   decisions stored for audit.
3. `dataset` / `train` — one shared feature path (`dirsplit/features.py`) for
   both Norwegian training stations and Gothenburg sensor edges: road class,
   speed, lanes, oneway, distance-to-centre, `radial_cos`, and residential/
   major street length behind vs ahead within 1 km half-discs.
4. `coverage` — kNN distance in standardized feature space flags
   **EXTRAPOLATION** above the 90th percentile (sensor 1074's edges are
   flagged, and that flag is carried into predictions).
5. `predict` — locally weighted per-sensor quantile regression (q10/q50/q90),
   then **James–Stein shrinkage λ = 0.256** toward 50/50.

**Why the shrinkage is the most intellectually honest thing in the repo:**
leave-city-out validation showed the transferable signal is small (Oslo
+11.1 %, Trondheim −0.9 %, Bergen/Stavanger worse because their streets really
are 50/50). Rather than ship a model that beats the baseline in one city and
loses in two, λ = 0.256 keeps only the ~26 % of predicted deviation that
transfers. By construction the shrunk estimate **cannot be worse than 50/50 in
expectation**. Pooled domain MAE: shrunk 0.0559 < 50/50's 0.0564 < raw 0.0654.
It is validated locally too: sensor 107 is genuinely two-way and the city's own
D-factor is 52/48, inside the model's predicted 0.47–0.52.

This also correctly *replaced* an earlier guess. `estimate_directions.py`'s
AM/PM Gaussian decomposition produced ~80/20 peak splits; the trained model
says real city streets deviate only 5–8 pp. The Gaussian over-attributed
because both directions peak in the morning. `estimate_directions.py` survives
only as a fallback when `model.pkl` is absent.

### Stage C.0 — Candidate generation (`build_candidates.py`, 4,410 lines)

This is the largest module and the one carrying the most real-world grounding.
It builds the *pool of possible routes* the solver will choose among. Order:

1. **Masses.** HOME mass = real 2023 SCB population per DeSO zone (open WFS +
   PXWeb, no key), spatialised to residential building footprints and their
   nearest routable road access. 116 of 129 inner-city DeSO zones have data;
   the 13 missing are new 2025-boundary splits, **reported, not fabricated**.
   ACTIVITY mass = OSM POIs in three categories matching RVU's purposes, each
   mapped once to its nearest road access.
2. **Tours.** Four structural classes — E-E (gate→gate through traffic),
   E-I / I-E (one end a boundary gate), I-I (both internal). Paired tours give
   an AM/PM directional prior.
3. **Purpose.** RVU Västra Götaland's 43/33/24 split, but not as one flat
   number — resolved into weekday/weekend/holiday × hour-of-departure profiles
   (08h ≈ 81 % arbete, 20h ≈ 35 % fritid), triangulated against NHTS 2017 and
   UK NTS 2019 and solved so the annual average reproduces RVU exactly.
4. **Departure times** from the project's own measured `normal_profile.json` —
   independently consistent with RVU's reported AM/PM peaks, a genuine
   cross-check.
5. **Routing** via `duarouter`, then physical filters: `validate_routed_
   candidates`, `drop_uturn_routes`, `drop_excessive_detours`,
   `drop_local_roundabout_detours`.
6. **Gates.** Every final candidate must cross at least one registered sensor
   edge; every measured edge must retain ≥ `--min-per-sensor` *distinct
   physical route geometries* (repeated vehicles cannot make a thin sensor
   pass).

**θ (through_fraction, gravity_km, cross_fraction)** is set by
`calibrate_theta.py`'s bounded grid search. The finding here is worth
preserving: GEH-based scoring **saturated at 100 % for all nine combinations**
— GEH only checks fit at measured edges, which the PFE achieves regardless of
candidate composition. It cannot discriminate θ. The replacement (trip-length
fit against RVU distance bins) gives an interior optimum around
`gravity_km` 2.6–12, and `through_fraction`/`cross_fraction` are frozen at
0.5/0.3 and **disclosed as unidentifiable neutral priors, not calibrated
values**. That disclosure is the right call.

### Stage C.1 — Assignment priors (`assignment_priors.py`, 31 KB)

This module exists because of a root cause that is worth restating, since it
explains most of the project's LOSO history:

> `pfe.py`'s objective minimises total vehicle count with **no offsetting pull
> on any edge that carries no active constraint**. Grounding *which* routes
> exist does nothing to make the solver *use* a route through an
> unconstrained edge.

Confirmed directly: station 107 recovered at 0.95 in LOSO because it sits on
paths between *other* active constraints; isolated stations collapsed toward
zero. This is the classic four-step model's missing fourth step — traffic
assignment.

The fix implements Dial-style stochastic multipath loading. Three real bugs
were found and fixed by measurement during its development, each instructive:

1. **Length vs. time weighting** — length-weighting routed traffic down slow
   residential shortcuts instead of the arterials sensors actually sit on.
2. **All-or-nothing vs. stochastic multipath** — a single deterministic
   shortest path per OD pair put **zero** load on 6 of 7 sensor edges. Fixed
   by routing each sample through one of 10 lognormal-jittered travel-time
   graphs.
3. **LP intractability** — feeding the field in as ~6,500 soft L1 priors (2 LP
   variables + a row each) stalled a whole-day solve at 100 % CPU for 35+
   minutes. Fixed by feeding it as a **wide interval bound** `[0, 5×value]`,
   the same free, variable-less mechanism the level-2 bounds already use.
   35+ min → 7 min.

The bound form is also the more honest representation: this field is a
plausibility range, not a confident target.

**Calibration, not transfer**: only one scale factor (loading-units →
vehicles/day) is fit locally, by robust median ratio against the 6–7 measured
edges. No cross-city ML transfer risk. Measured effect: median LOSO recovery
0.09 → 0.154, replicated twice.

### Stage C.2 — The reconciliation (`traffic_sim/demand/pfe.py`, 2,932 lines)

The heart of the program. A single convex problem selects concrete route flows
that match measured counts exactly, never violate level-2 bounds, and stay
close to level-3 priors.

**The solver is max-entropy IPF (Sinkhorn/Bregman balancing)**, not an LP.
This replaced `solve_interval`'s linear parsimony objective for a reason that
is mathematically correct: minimising Σx_r is *indifferent* between one route
carrying 100 vehicles and ten routes carrying 10 each, so ties were broken by
the LP solver's vertex selection — which is exactly where the observed route
concentration came from. Penalising concentration afterwards (`MAX_ROUTE_SHARE`,
dispersion passes, MSA damping) was a pile of tuned knobs on the wrong
objective, and at scale too many dispersion passes collapsed GEH from 100 % to
7 %.

The textbook fix (Van Zuylen & Willumsen 1980) maximises −Σx_r·ln(x_r), whose
Lagrangian has closed form x_r = A_r·Π exp(λ_e): a route's flow is a *product*
of per-edge multipliers. Solving for those by alternating proportional
rescaling is both more principled and far cheaper — no HiGHS call per pass.

**The relaxation ladder is the estimation hierarchy in executable form.** When
an interval is infeasible, what gives is decided in this order
(`pfe.py:1063-1071`):

```
RUNG_CLEAN         tol ×1, everything on
RUNG_NOBND_TOL1    tol ×1, level-2 bounds OFF
RUNG_NOQUOTA_TOL1  tol ×1, + purpose quotas OFF
RUNG_NOPRIOR_TOL1  tol ×1, + level-3 priors OFF
RUNG_LP_FALLBACK   tol ×1, complete LP
RUNG_RELAX_TOL2X   tol ×2
RUNG_RELAX_TOL4X   tol ×4
RUNG_RELAX_NOBND   tol ×4, bounds off
```

The invariant: **every non-measurement layer is surrendered before the
measurement band moves by one unit.** I verified the implementation at
`pfe.py:1140-1196` matches this: stage 1 drops bounds/quotas/priors at
unwidened band, stage 2 runs the complete LP still unwidened, stage 3 widens.

Two facts make violations of this invisible, and both are correctly
documented:

* **GEH cannot police it.** The ×4 band peaks at GEH 3.81 for a 400 veh/quarter
  target, and no measured edge has ever exceeded 203 in a quarter. A build can
  report 100 % GEH<5 with a fifth of its intervals anywhere inside a 20 % band.
* **`tol_mult` never enters the IPF iteration** — it is read only by
  `_check_entropy_solution`, so a widening rung returns a **bit-identical**
  vector and merely judges it by a looser ruler. A widened band is therefore
  never evidence about the route pool. This is pinned by
  `test_widening_the_band_does_not_change_what_ipf_computes`.

Three separate inversions of this ordering were found and fixed in one day
(2026-08-06) — bounds, then purpose quotas, then priors — each a constraint
that stayed active at every rung, so when it conflicted with a measurement the
*measurement* gave way. That is the correct diagnosis of a subtle and
genuinely dangerous class of bug.

**Parallelisation.** The final solve flattens all 3 variants × 96 quarters into
one `fork` pool over all cores, after verifying there is no warm start, RNG,
previous-quarter accumulator or shared solver object tying quarters together.
Measured: 336.69 s end-to-end for a full day on 10 workers, q50/q10/q90 all
100 % GEH<5.

**Integer publication** is a separate boundary from the continuous solve: one
joint integer projection over every pool-supported active sensor, retained hard
bounds and an exact purpose margin, with sorted sensor keys so registry
insertion order cannot become a priority.

### Stage E — Simulation (`run_scenario.py`, 3,187 lines)

`build_sumo_net.py` converts `graph.graphml` → plain XML → `netconvert` →
`sumo/net.net.xml`, preserving `u_v_k` edge IDs exactly. `run_scenario.py`
runs SUMO **mesoscopic** Monte Carlo across 3 seeds spread over the q50/q10/q90
demand variants, so direction uncertainty reaches the per-edge confidence.

**Why mesoscopic:** measured, not assumed. A whole-day 3-seed closure is 35 s
meso vs 25 min micro (43×), *and* delivery is better (0.87–0.96 at sensors vs
0.83–0.94). The product is 15-minute edge flows, which does not need
microscopic car-following. `--micro` is kept for comparison runs.

Closures attach a rerouter only within `REROUTER_RADIUS_M` = 400 m of the
closure — a global rerouter was the other measured bottleneck.

**`truncate_stranded_vehicles`** deserves its own note as a model of how to fix
a bug properly. A 39-vehicle "leak" through a closed edge was root-caused three
independent ways (SUMO's own teleport warnings; `duarouter` routing through the
closure because `<rerouter>` is a *runtime* concept an offline router never
evaluates; a plain Dijkstra over `<connection>` confirming no path exists). The
fix then took three rounds of real feedback:

1. First cut **deleted** stranded vehicles — wrong, because a driver whose
   destination is unreachable still drives most of the trip and parks short of
   it. Changed to **truncate** the route at the last edge before the closure.
2. Review caught that reachability was checked from the route's *origin* as a
   proxy. Two vehicles sharing an origin can be on different routes, and with
   multiple closures, truncating at the first hit ignores a later one. Fixed to
   check from the edge immediately **before the first closed edge in that
   vehicle's own route** — exactly where SUMO's live rerouter re-plans from.
3. `build_edge_graph` hoisted to run once per closure rather than per variant.

Result: 0/16,467 leaking vehicles (was 39), vehicle count unchanged, reproduced
clean on a second unrelated closure.

### Runtime — `serve.py` (2,353 lines) and `web/`

`serve.py` runs a `ThreadingHTTPServer` bound to **127.0.0.1 only**, because
the mutating endpoints have no authentication — an explicit, documented choice
rather than an accident. Origin checks guard against cross-origin POSTs from a
browser.

Every long-running action is **start + poll**, never a blocking GET. This was
learned from a real production incident: a `/api/recalibrate` call held one
HTTP GET for 5–14 minutes, the user's tab did not survive it, the server
finished ~10 minutes later with nobody watching, and the UI looked idle. The
fix returns 202 immediately, exposes `/api/recalibrate/status`, and the
frontend checks status **on every page load** so a job started from a
since-closed tab is still visible. Jobs are additionally written durably to
`runs/jobs/<id>.json` so a server restart mid-job can reconcile against live
pids. This is the correct architecture for the problem, arrived at the hard
way.

The browser app is `index.html` + `state.js` + `provider.js` + `render.js` +
`controls.js` + `clock.js` + **`app.js`**. Leaflet with `preferCanvas: true`
(7,000 SVG DOM nodes would crawl), CARTO `light_all` tiles, traffic ramp
`#1f9d55 → #d97706 → #dc2626` validated against the light surface for contrast
≥ 3:1 and CVD separation 16.9.

---

## Part 2 — Where the architecture is genuinely right

These are worth stating explicitly, because the problems below should not
obscure them.

1. **The provider seam.** `flowAt(edgeId, t)` absorbed two entirely new data
   sources (a 2027 forecast, then Monte Carlo scenarios) and a composing
   `DeltaProvider` **without a single new provider class being written**,
   because the schema was designed once and reused. That is the strongest
   possible evidence a seam was drawn in the right place.
2. **The estimation hierarchy is executable, not aspirational.** Most projects
   describe a hierarchy in a document. Here it is the *order of a relaxation
   ladder*, pinned by a test that proves widening the band cannot change what
   IPF computes.
3. **Measured decisions over assumed ones.** Meso vs micro (43×), bounds vs
   soft priors (35 min → 7 min), stochastic multipath vs shortest path (0 of 7
   sensor edges loaded), IPF vs LP parsimony. Each was measured, and in several
   cases the measurement overturned the initial guess.
4. **Negative results are preserved as first-class evidence.** θ's GEH
   saturation, the v1–v5 proxy gate failures, the refuted warm-state
   hypotheses. A project that records what did *not* work is a project whose
   positive claims can be believed.
5. **The baseline rule — "only what is measured is simulated."** Refusing to
   invent background traffic, and stating plainly that ~47 % of the inner city
   consequently carries zero baseline flow, is a harder and better choice than
   painting a plausible city.
6. **Leakage discipline in validation.** The LOSO audit found a real leak
   (`assignment_priors`' scale factor was fit against the held-out sensor),
   measured how much it mattered by running three variants, and fixed it by
   splitting the expensive data-independent structural computation from the
   cheap per-fold regression.
7. **Fail-closed everywhere.** Missing evidence, malformed ledgers, drifted
   fingerprints and unsupported sensor edges all fail closed rather than
   degrading silently.

---

## Part 3 — Architectural problems, ranked

### P1 — The confidence field is numerically dead across 94 % of the network

**This is the most serious problem, because it silently breaks the product's
central promise.**

`confidence = exp(-d²/2σ²)` with the deployed σ. Measured directly from the
committed `network.geojson`:

* Implied σ from the data: **119.508 m** (444 samples).
* `dist_sensor_m`: median **1,758 m**, p90 3,241 m, max 4,370 m.
* **6,703 of 7,147 edges (93.8 %) have `confidence` exactly 0.0.**

The reason is arithmetic. At the median edge distance, exp(−1758²/(2·119.5²))
= 1.0 × 10⁻⁴⁷. A Gaussian fitted on near-field distances of 61–245 m is being
evaluated 14σ out, where it underflows.

Cross-referencing confidence against what is *actually simulated*
(`scenarios/baseline.json`):

| | count | share |
|---|---|---|
| flow > 0 **and** confidence > 0 | 309 | 4.3 % |
| flow > 0 but confidence == 0 | **2,830** | 39.6 % |
| flow == 0 but confidence > 0 | 135 | 1.9 % |
| flow == 0 and confidence == 0 | 3,873 | 54.2 % |

* **Of the 3,139 edges that are actually simulated, 90.2 % render at floor
  confidence** — visually identical to streets carrying nothing.
* **Of the 444 edges with nonzero confidence, 30.4 % carry no traffic at all.**

The map's primary epistemic encoding is now roughly *anti*-correlated with the
thing a user needs to know: whether this street was simulated at all.

`render.js:175-186` already knows something is wrong and applies a
`conf^(1/8)` display transform. That transform is monotone and honest, but it
only rescues the gradient to about 1,000 m:

| distance | raw conf | transformed | rendered opacity |
|---|---|---|---|
| 300 m | 4.3e-2 | 0.674 | 0.671 |
| 500 m | 1.6e-4 | 0.335 | 0.484 |
| 1,000 m | 6.2e-16 | 0.013 | 0.307 |
| 1,758 m | 1.0e-47 | 0.000 | 0.300 |

**5,690 of 7,147 edges (79.6 %) lie beyond 1,000 m**, so in Simulering mode
roughly four-fifths of the city renders as one flat maximum-uncertainty red at
identical opacity — including the 2,830 edges carrying real simulated traffic.
Worse, the *background* layer (`render.js:489`) uses the **raw** confidence:
`bgOpacity = 0.10 + 0.65 * confidence`, so 93.8 % of edges sit at exactly
0.10. The comment two lines above calls the confidence gradient "the base
layer"; that gradient does not exist at city scale.

`CLAUDE.md` already anticipates the epistemics ("treat a nonzero confidence on
an edge with no baseline traffic as meaningless until the map distinguishes
covered from uncovered") but the map still has no `covered` concept —
`grep` finds none in `web/*.js` outside an unrelated `app.js` variable.

**Compounding this: σ appears as four different numbers in the tree.**

| Location | Value |
|---|---|
| `network.geojson` (computed from the data) | **119.508 m** |
| `CLAUDE.md:276` | 119.5 m |
| `CLAUDE.md:405` | **144.0 m** |
| `render.js:177` comment | **127.5 m** |
| `build_data.py` fallback | 250 m |

**Recommendation.** Separate the two questions the field is being asked to
answer. Publish a boolean/categorical `coverage` property (`measured` /
`sensor_reachable` / `not_simulated`) derived from the calibrated route pool,
and let *that* drive the base layer and the legend. Keep `confidence` as a
continuous number only within the sensor-reachable subgraph, and switch the
functional form to something that does not underflow at 15σ (exponential or
power-law decay). Then reconcile the four σ values to one.

### P2 — 42,628 lines of warm-state machinery, and a contradicted default

`find . -name "*warm*"` returns **115 files**; the Python among them totals
**42,628 lines** — for what is, by design, a *speed optimisation* of an
already-working cold path.

Widening the lens to the whole campaign/evidence family (warm-state, monthly
search, proxy screening, held-out gates, cost-ordered benchmarks) and counting
implementation, its tests and its tooling separately:

| Bucket | Lines | Share of all Python |
|---|---|---|
| Product code | 39,914 | 24.8 % |
| Campaign/warm implementation | 24,159 | 15.0 % |
| Campaign/warm tests | 36,279 | 22.6 % |
| Campaign/warm tooling | 26,284 | 16.3 % |
| Product tests | 27,038 | 16.8 % |
| Other tooling | 7,150 | 4.4 % |
| **Total Python** | **160,824** | |

**The campaign/evidence family totals 86,722 lines — 54 % of all Python in the
repository, against 39,914 lines of actual product.** That is a 2.2 : 1 ratio
of validation machinery to the thing being validated.

The campaign history runs v1 → v16. Reading it in order: v1 failed
(cumulative counters double-counted), v2 failed (boundary-active
under-counting), v3 retired by its own diagnostic, v4–v6 never warmed at all
because production did a bare `import traci` while the package ships inside
the SUMO installation, v7 rejected in review, v9 failed with the exact v2
residual, v12 refuted its own selective correction, v13 blocked by a sandbox
socket denial, v14 frozen and unexecuted. v16 finally passed exact semantic
equality and delivered a measured **19.1 %** speedup (71.568 s vs 88.506 s) on
a true cache-hit run.

The engineering discipline through that sequence is genuinely impressive —
each failure was diagnosed rather than tolerated, and the refutations are
recorded. But the architectural judgement is the problem: **a 19.1 % speedup on
one path does not justify 27 % of the codebase**, and the subsystem is now the
single largest source of coupling, frozen contracts and test failures in the
tree.

**And the source-of-truth document contradicts itself about whether it is
on.** Verified:

* `ARCHITECTURE.md:909` — "Product-default warming remains **OFF**"
* `ARCHITECTURE.md` (Invariants → Monthly warm-state activation) — "The
  recurring monthly command now **enables** the existing mesoscopic warm path
  **by default**"
* `run_monthly_closure_search.py:857` — `parser.set_defaults(warm_execution=True)`

The code is authoritative: warming is **on** by default for the monthly search.
Line 909 is stale and should be corrected, because a reader checking whether an
unproven optimisation is active will find the wrong answer.

**Recommendation.** Freeze the warm-state family at v16, move the superseded
v1–v14 campaign code (not the evidence JSON) to an `archive/` tree outside the
import and test paths, and correct line 909. The evidence artifacts should
stay; the fourteen versions of *executable* freeze tooling should not remain
live code.

### P3 — Layering inversion: the library imports the CLI

`ARCHITECTURE.md` states the rule clearly: root files are stable CLI
entrypoints, reusable implementation lives in `traffic_sim/`, and "new reusable
code must be added to `traffic_sim/` first". Static analysis of all 135 modules
finds the dependency arrow reversed in five places, four of them at **module
scope**:

| Importer (library) | Imports (root CLI) | Lazy? |
|---|---|---|
| `traffic_sim/simulation/monthly_sumo.py:22` | `run_scenario` (3,187 L) | **no** |
| `traffic_sim/simulation/monthly_sumo.py:23` | `suggest_closure_time` (1,232 L) | **no** |
| `traffic_sim/confidence/loso.py:54` | `build_sumo_demand` (1,495 L) | **no** |
| `traffic_sim/confidence/loso.py:51` | `assignment_priors` | **no** |
| `traffic_sim/simulation/network_audit.py:17` | `build_sumo_net._is_oneway`, `_speed_kmh`, `_parse_numeric_tag` | **no** |

The last one imports **private** functions across the layer boundary.
`deterministic_disruption.py` does it correctly — lazily, inside functions,
with a `noqa` explaining why.

Three genuine import cycles exist:

```
build_sumo_net → traffic_sim.simulation.network_audit → build_sumo_net
assignment_priors → demand.intake → demand.priors → assignment_priors
demand.intake → demand.priors → demand.intake
```

The practical cost is already visible: importing `validate_sim` (a thin shim
over `traffic_sim/confidence/loso.py`) transitively requires `osmnx`, because
`loso.py` → `assignment_priors` → `import osmnx` at module scope. A validation
module cannot be imported without a heavyweight geospatial dependency it does
not need for most of its surface.

**Recommendation.** Extract the shared helpers (`_is_oneway`, `_speed_kmh`,
`build_targets`, `ensure_observability`, the `run_scenario` entry points
`monthly_sumo` actually needs) into `traffic_sim/`, and make the root files
import *downward* only. This is mechanical, low-risk, and would break all three
cycles.

### P4 — Two packages named `demand`

`demand/` (3,675 lines: `calibration`, `day_library`, `feedback`, `intake`,
`locations`, `priors`, `publication`, `structure`) and `traffic_sim/demand/`
(3,688 lines: `pfe`, `pfe_kernel`, `cache`, `provenance`, `route_support`,
`structure_caps`, `source_identity`, `build_lock`) split one pipeline stage
across two packages with the same name at different depths.

`ARCHITECTURE.md` justifies this ("model-specific pipelines with their own data
contracts") but that justification does not survive contact with the file
names: `demand/structure.py` and `traffic_sim/demand/structure_caps.py`,
`demand/priors.py` and the PFE's prior handling. It also directly contradicts
the "new reusable code goes in `traffic_sim/` first" rule, and it is the site
of two of the three import cycles.

**Recommendation.** Merge into `traffic_sim/demand/`. `dirsplit/` genuinely is
a separate pipeline with its own contracts and should stay; `demand/` is not.

### P5 — Frozen contracts bind whole god-files, so they break on contact

The frozen-evidence system fingerprints *entire source files*. **41 validation
artifacts bind `run_scenario.py`.** That file last changed on 2026-08-10
(`11fc68b`), and the frozen contracts have not been reconciled since.

This is not theoretical. Executed on the committed tree, environment-independent:

```
FAILED tests/test_warm_state_population_semantics.py::TestFrozenContract::
       test_every_bound_source_matches_the_live_tree
AssertionError: run_scenario.py
  expected 5c61d421af8146b7...  actual d552557c878e68dc...
```

The design intent is right — evidence that does not name its inputs is not
evidence. The **granularity** is wrong. Binding a 3,187-line module that is
simultaneously (a) the closure simulator, (b) imported by the library layer,
and (c) under active development means every ordinary change invalidates dozens
of frozen contracts at once, and the resulting red tests are indistinguishable
from real regressions.

**Recommendation.** Fingerprint the *behaviours* the evidence depends on, not
the files: extract the closure-relevant functions into small, stable modules
(`closure_teleport.py` already demonstrates the pattern) and bind those. Then
split `run_scenario.py` so the parts that must be stable actually can be.

### P6 — The test suite: 4,206 tests, 28 minutes, 332 failing

Executed run: **332 failed, 4,038 passed, 7 skipped in 1,683.89 s (28:03)**.

Sampling the failures shows two distinct causes, and they need to be
disentangled because they have different fixes:

1. **Environmental** — `sumo/net.net.xml` missing (`sumo/` is gitignored as an
   intermediate). This exposes a real build-graph gap: `make all` = `data
   features agent1 forecast test`, which runs `make test` but **never runs
   `make sumo-net`**. A fresh clone following the documented entry point
   therefore cannot pass its own test suite. `make refresh` does include
   `sumo-net`, but `make all` is what `README`/`Makefile` present first.
2. **Genuine drift** — the `run_scenario.py` fingerprint mismatch in P5, which
   fails on any machine.

Timing is very unevenly distributed, which is good news for the fix. Measured
per file: `test_closure_metrics` 0.05 s, `test_agent1` 0.43 s, `test_pfe`
**2.93 s (121 passed)**, `test_build_sumo_demand` 1.57 s, `test_day_library`
7.85 s, `test_serve` **57.9 s**. The core estimation logic is fast and green;
essentially all 28 minutes sit in the campaign/warm-state files.

Beyond the failures, 28 minutes with no parallelisation (`pytest-xdist` is not
in `requirements.txt`) is past the point where a developer runs the suite
before committing. 63,317 lines of tests against 39,914 lines of actual product
code is a ratio worth questioning on its own.

### P6b — `test_serve.py` is racy, and it is racing on the production job locks

This deserves separating from P6 because it may not be a test problem.

`tests/test_serve.py` was run four times in a row on an otherwise idle machine.
Each full-file run produced **a different single failure**, and every one of
those tests **passes when run alone or as its own class**:

| Run | Failing test |
|---|---|
| 1 | `TestCancel::test_cancel_recalibration_preserves_existing_scenarios` |
| 2 | `TestOptimizeSignals::test_failure_releases_the_lock` |
| 3 | `TestSuggestClosure::test_cancel_stops_suggestion_without_reporting_an_error` |
| — | each of the above, in isolation: **passed** |

Three different tests, three different runs, all in the cancel / lock-release /
job-lifecycle area. That pattern is a race, not an ordering dependency.

The likely mechanism is that `serve.py` keeps its job state in **module-level
globals** — `_recal_state`, `_close_state`, `_suggest_state`, `_optimize_state`,
`_monthly_state`, `_sim_lock`, `_active_job`, `_ORPHANED_JOB_IDS` — and the
tests exercise real `threading.Thread` job runs against that shared state
inside one process. Either the tests are leaking state between cases, or the
cancel/lock-release paths genuinely race. **The second possibility matters:
these are precisely the paths that stop a running SUMO process tree and
release the single-simulation lock.** A lock that is occasionally not released
would present to a user exactly as the documented "en simulering kör redan"
refusal with nothing actually running.

**Recommendation.** Before treating this as test hygiene, determine which it
is: run the three tests under a deterministic seed with thread-state assertions
around `_sim_lock` and `_active_job`, and add a fixture that resets every
module global between cases. If the race survives a clean fixture, it is a
production concurrency bug in the job manager and should be fixed there.

**Recommendation.** Split the suite into `fast` (contracts, pure logic, target
< 60 s) and `slow`/`campaign` markers; add `pytest-xdist`; make the fast tier
the pre-commit gate. Make `make all` either depend on `sumo-net` or run only
the tier that does not need SUMO artifacts. Then drive the 332 to zero — a
permanently red suite trains everyone to ignore it, which defeats the entire
fail-closed philosophy the rest of the project is built on.

### P7 — `web/app.js` is a 2,898-line anonymous IIFE

The backend has versioned contracts, fingerprints and fail-closed gates. The
frontend's main module is one `(async () => { ... })()` with **zero top-level
functions**, 38 `addEventListener` calls and 16 distinct fetch endpoints, every
identifier sharing one closure scope. The indentation histogram tells the
story: the body sits at 8–14 spaces because the file was lifted verbatim out of
an inline `<script>` block in `index.html` (correctly, to allow a real CSP) and
never restructured afterwards.

It is also **undocumented**: `CLAUDE.md`'s Files section lists
`index.html, provider.js, state.js, render.js, controls.js, clock.js` — the
largest file in the web app is absent. `index.html` additionally carries 536
lines of inline `<style>`.

There is no frontend test of any kind. The project has already been bitten by
this twice — the "dots always visible" `MAX_QI` bug and the `setSpeed(0.25)`
floor that silently clamped Realtid to 225× — both found only by driving a real
browser over CDP, both invisible to `pytest`.

**Recommendation.** Extract the mode-switching, scenario-loading and job-polling
concerns into named modules behind the same load-order discipline the other
five files already follow, and add the CDP harness to CI as a smoke test. The
seam architecture that makes the rest of the frontend good simply was not
applied here.

### P8 — Git is being used as an artifact store

* `.git` is **51 MB**; `validation/` is **64 MB** across 163 tracked files.
* Single largest tracked file: **37 MB**
  (`validation/monthly_warm_state_v15_q10_forensics/raw_closure-…__q10__1000.json`).
* Also tracked: an 11 MB `annual_warm_plan_2027.json`, a **14 MB
  `data/dirsplit/model.pkl`**, two trajectory JSONs of 13 MB and 12 MB.
* **187 version-suffixed files** (`*_v1`…`*_v16`).
* `tools/` holds 74 scripts / 33,100 lines, of which **19 are referenced
  nowhere** outside their own tests — including `freeze_heldout_v2/v3/v7/v8`
  and `validate_warm_state_equivalence.py`.

Separately, the tracked `model.pkl` is loaded via `pickle.load`
(`dirsplit/predict.py:87`, `prior_flows.py:71`, `train_agent1.py:372`). Pickle
executes arbitrary code on load and is brittle across scikit-learn versions —
an odd choice in a project this careful about reproducibility, and one that
sits awkwardly beside the cache identity work that already fingerprints NumPy
and SciPy versions precisely because they are not stable.

**Recommendation.** Move large evidence blobs to Git LFS or an external store
addressed by the digest the manifests already record. Archive superseded freeze
tools. Consider ONNX or a plain JSON coefficient dump for the dirsplit model.

### P9 — The core solver's docstring states the ladder backwards

`traffic_sim/demand/pfe.py:329-331`, inside `solve_interval_entropy`:

> "…the SAME relaxation ladder in `calibrate()` still applies (**widen
> tolerance, drop bounds, in that order**)"

That is the pre-2026-08-06 order — precisely the inversion that
`ARCHITECTURE.md` documents as having been found and fixed three times in one
day, and that the surrounding 80 lines of comment exist to prevent. The
implementation is correct; the docstring on the function implementing the
project's central invariant is not. This is a two-line fix and a high-value
one, because this docstring is exactly what a future contributor reads first.

### P10 — The product is two different products wearing one UI

| Mode | Edges with data | Time span |
|---|---|---|
| Historisk | **7** of 7,147 (0.1 %) | 35,040 quarters (full year) |
| Prognos | **7** of 7,147 (0.1 %) | 35,040 quarters (full year) |
| Simulering | 3,139 of 7,147 (43.9 %) | 96 quarters (one day) |

Goal #1 of the project — "animate historical traffic flow on a real map of
Gothenburg" — is, in the shipped Historisk mode, an animation of **seven
edges**. This follows correctly and honestly from the baseline rule, and the
missing edges are drawn as gaps rather than zeros exactly as the rules require.
But the two modes have coverage and time-span profiles that differ by three
orders of magnitude in opposite directions, and the UI presents them as three
peers on one toggle. A user switching from Historisk to Simulering is not
changing the data source; they are changing what the product *is*.

**Recommendation.** This is a framing and labelling problem, not a code
problem. Say in the UI what each mode covers ("7 mätpunkter, hela 2025" vs
"3 139 gator, ett dygn"), so the toggle stops implying equivalence.

### P11 — `YEAR_MAX_QI` is hardcoded and not derived from the provider

`web/state.js:2` — `const YEAR_MAX_QI = 365 * 96 - 1;`

`CLAUDE.md` describes the fix as calling `State.setMaxQI(n)` "on every provider
switch with the ACTIVE provider's own length (`numQuarters`, derived from its
flow-array length)". The code (`app.js:338`) is
`State.setMaxQI(isSim ? provider.numQuarters : null)` — so **only** Simulering
uses its own length; Historisk and Prognos fall back to the hardcoded constant.

It happens to be right for 2025 and 2027 (both 365 days), and both committed
flow files do have exactly 35,040 quarters, so nothing is broken today. But a
leap year (2028, or any 366-day historical delivery) would silently truncate
the final day, and the documentation already describes behaviour the code does
not have. `provider.numQuarters` is available on all three providers; passing
it unconditionally is a one-line fix.

### P12 — The documentation has outgrown its readers

`CLAUDE.md` 45 KB + `ARCHITECTURE.md` 121 KB + `IMPROVEMENT_PLAN.md` 261 KB =
**427 KB of prose**, plus 1.8 MB in `docs/`. `IMPROVEMENT_PLAN.md` alone has
100 headings.

The content is high quality and the honesty is exceptional. The problem is
retrieval: this review found four different values of σ, a directly
contradicted warm-state default, a stale ladder docstring, an undocumented
2,898-line frontend file, and a `144.0 m` figure superseded by `119.5 m` in the
*same file* — all in documents whose explicit purpose is to be the source of
truth. Contradictions at this volume are not a discipline failure; they are the
predictable result of append-only documents that never compact.

**Recommendation.** Split each document into a short **current-state**
section (what is true now, no history) and a dated **history** appendix, and
move superseded narrative to `docs/history/` — a convention the repo already
has. Every number that appears in prose *and* in an artifact should be
generated, not retyped: a small `tools/check_doc_constants.py` that asserts the
σ in `CLAUDE.md` matches the σ implied by `network.geojson` would have caught
three of the findings above.

### P13 — `serve.py` dispatch is ordering-dependent

Routing is a linear chain of `self.path.startswith(...)` tests
(`serve.py:1227-1276`) where correctness depends on declaration order:
`/api/close/status` must be tested before `/api/close`, and the same for
`recalibrate`, `suggest_closure`, `optimize_signals` and `monthly_search`. A
future `/api/closest` would silently match `/api/close`.

Alongside it sit five near-identical job lifecycles (`_run_close`,
`_run_recalibrate`, `_run_suggest_closure`, `_run_optimize_signals`,
`_run_monthly_search`), each with its own lock, state dict, status endpoint and
cancel path. `_STATE_BY_KIND` already unifies part of this, which shows the
generalisation was recognised — it just was not carried through to the handlers
themselves.

**Recommendation.** Parse the path once into `(resource, action)` and dispatch
from a dict; fold the five lifecycles into one parameterised job runner.

---

## Part 4 — What I would do next, in order

1. **P9** — fix the `solve_interval_entropy` docstring. Two lines, prevents a
   dangerous misreading of the project's central invariant.
2. **P2 (doc half)** — correct `ARCHITECTURE.md:909`. One line, currently tells
   readers an active optimisation is off.
3. **P1** — publish a `coverage` property and drive the map from it. This is
   the difference between a map that shows what it knows and one that shows a
   number that is zero almost everywhere.
4. **P6b** — determine whether the `test_serve.py` race is test pollution or a
   real job-manager concurrency bug. This is ahead of the rest of P6 because a
   lock that occasionally fails to release is a user-visible production fault,
   not a test-hygiene issue.
5. **P6** — split the suite into fast/slow tiers, add `xdist`, fix `make all`'s
   missing `sumo-net` dependency, drive the 332 failures to zero. The core
   estimation tests already run in under 3 s, so a useful fast tier is close at
   hand.
6. **P3 / P4** — extract shared helpers into `traffic_sim/`, merge `demand/`
   into `traffic_sim/demand/`, break all three import cycles. Mechanical.
7. **P5** — split `run_scenario.py` and re-bind frozen contracts to small
   stable modules.
8. **P2 (code half)** — archive v1–v14 warm-state tooling outside the import
   and test paths.
9. **P7** — modularise `app.js`; add the CDP smoke test to CI.
10. **P8, P12, P13, P10, P11** — as opportunity allows.

---

## Closing assessment

The estimation core of this project is better than the code around it. The
hierarchy-as-relaxation-ladder, the max-entropy reformulation, the assignment
prior as a bound rather than a penalty, the shrinkage in `dirsplit`, and the
refusal to invent traffic are all decisions a professional transport-modelling
group would be pleased with, and several were reached by overturning an
initial guess with a measurement — which is the hard way and the right way.

The architecture's problem is not wrongness; it is **proportion**. A
39,914-line product carries 86,722 lines of campaign and evidence machinery —
54 % of all Python in the repository, a 2.2 : 1 ratio — including a warm-state
family that ran sixteen versions to deliver a 19.1 % speedup on one cached
path. Alongside it sit a 63,000-line test suite that takes 28 minutes and fails
332 times, 19 tools referenced nowhere, 187 version-suffixed files, and 427 KB
of prose that now contradicts itself on at least four verifiable points
(σ, the warm default, the ladder order, the frontend file list).

The rigour that makes the estimation core trustworthy has been applied
uniformly to everything, including work that did not warrant it. That uniform
application is why the core is good *and* why the project is now slow to move:
the same fail-closed fingerprinting that correctly protects a published demand
build also makes an ordinary edit to `run_scenario.py` invalidate 41 frozen
contracts.

The single highest-value change is P1: the confidence field is the promise the
product makes to the city, and it is currently zero on 94 % of the map,
including on 90 % of the streets the simulation actually models.
