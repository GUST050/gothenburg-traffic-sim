# Traffic animation & incident simulation — Gothenburg

Persistent project context for Claude Code. Keep this file in the repo root.

**ARCHITECTURE.md is the source of truth for the program's structure** (six
stages: Intake → Observability → Forecast → Demand → Simulation → Confidence,
with contracts, gates and build order). This file holds project context,
history and rules; when they disagree about structure, ARCHITECTURE.md wins.

## What this project is
Summer project for Prof. Miroslaw Staron (Chalmers). Owner: Gustav (student).
Goal arc, in order:
1. Animate historical traffic flow on a real map of Gothenburg.
2. Train an AI model to forecast normal flow.
3. Simulate traffic after incidents (road closures) — the real goal.

## Scope — RE-DECIDED 2026-07-05 (supersedes the two-cluster scope)
- The canvas is GOTHENBURG'S INNER CITY (INNER_CITY_BBOX in build_data.py:
  river→Krokslätt, Vallgraven→Gårda; ~7 100 directed edges, bridges and big
  approaches as through-traffic gates). No display clip.
- The product promise: accuracy is a GRADIENT — hard (measured/PFE-
  constrained) near sensors, prior-driven elsewhere — and every sensor the
  city adds raises accuracy wherever it is placed. The confidence map is
  the product's honesty: we SIMULATE everywhere but CLAIM only what the
  per-edge confidence supports. (This resolves the original "citywide would
  be ungrounded extrapolation" concern: the extrapolation is now labelled.)
- Sensor edge IDs verified IDENTICAL across the expansion — all contracts
  survived.
- OD/candidate generation is GROUNDED (2026-07-05): SCB DeSO population +
  OSM POI proxy + RVU Västra Götaland behaviour — see ARCHITECTURE.md's
  "C — Candidate generation" section for the full story, including the
  GEH-saturation finding, the θ freeze, and the controlled LOSO comparison
  (grounded 0.093 vs legacy uniform 0.076, same city network — a real but
  modest win). Do NOT compare this to the old "0.32" LOSO figure anywhere
  in project history — that was measured on the small two-cluster network
  and is not comparable (see the CONFOUND WARNING in ARCHITECTURE.md).
- ASSIGNMENT PRIOR (2026-07-05, `assignment_priors.py`, ARCHITECTURE.md
  section C.1): root-caused why grounding alone barely helped — pfe.py's
  parsimony objective pulls every unconstrained edge to ZERO regardless of
  how realistic the candidate pool is; only edges on paths BETWEEN other
  active constraints get traffic "for free". Fix: a gravity+network
  traffic-ASSIGNMENT field (Dial-style stochastic multipath — plain
  shortest-path collapses onto one canonical route and misses real
  arterials, verified directly) fed in as a WIDE INTERVAL BOUND (not a
  soft prior — 6500 soft L1 priors stalled the PFE >35 min; bounds are
  free variable-wise). Replicated LOSO result: median recovery 0.09 → 0.15
  (+65-70%, confirmed twice). Enabled by default
  (`--no-assignment-prior` to disable for comparison).
- WEB UI + SIMULATION FIXES (2026-07-05/06, from Gustav's direct testing
  feedback — "bilar vorde alltid vara simuleringen", "prickarna syns hela
  tiden", "jag vill kunna välja vilka dagar", "simuleringen ska ske i
  framtiden så 2027", "sekund för sekund"):
  - ROOT CAUSE of "dots always visible": `state.js`'s playback clock
    wrapped against the YEAR's length (35 040 quarters, `MAX_QI`) even in
    Simulering mode, where a scenario is only ~96 quarters (one day) —
    past quarter 96 there's no data, so at normal speed the map sat on
    "ingen data" for a fake extra ~364 days before ever looping back to
    the start. Fixed: `State.setMaxQI(n)`, called on every provider
    switch with the ACTIVE provider's own length (`numQuarters`, derived
    from its flow-array length) — Historical/Forecast keep the year-long
    default, Simulering gets its own scenario's length.
  - Vehicles are now ALWAYS the Simulering view (no more 🚗 toggle) — the
    conveyor-dot illustration never shows in that mode.
  - UI unified into one "Simulering" panel (was a confusing split between
    a "Scenario" mode toggle and a separately-floating "Stäng väg"
    button) — scenario picker, "+ Ny avstängning", "📅 Byt dag" all live
    together; #map/#controls now flexbox so the panel can grow/shrink
    without ever overlapping the map (`Render.invalidateSize()` called on
    the transition).
  - "Simulate the future" (`--source historical|forecast` on
    build_sumo_demand.py, `/api/recalibrate?date=&source=` on serve.py):
    calibrates the PFE's hard-count TARGETS against
    `flows_forecast.json` (Agent 1's LightGBM 2027 forecast) instead of
    actual 2025 counts. DESIGN DECISION: bounds/priors/corridor-coupling
    are NOT recomputed per date — they're structural (conservation math,
    learned direction-shares, spatial ratios), and there's no network-
    wide "2027 historical" ground truth to derive them from (the
    forecast only has point estimates AT the 6 sensors) — so they always
    come from the fixed real reference `STRUCTURAL_REFERENCE_DATE =
    "2025-09-16"` regardless of which date/source is actually being
    simulated. Only the target values switch. Recalibrating (either
    source) takes ~7-14 min and wipes old scenario files (they'd
    silently reflect the previous date's demand otherwise) — screenshot-
    verified end-to-end for 2027-09-14 forecast (100% GEH<5, vehicles
    moving, correct labels).
  - Real-time playback: speed presets are now MODE-DEPENDENT
    (`Controls.setSpeedMode`) — Historisk/Prognos keep the fast quarter-
    scrubbing presets (1×/4×/24×/96×, quarters/sec, for browsing a year
    of 15-min flow buckets); Simulering gets presets anchored to actual
    elapsed seconds (Realtid/10×/60×/300×, where "Realtid" = 1 real
    second per 1 simulated second) for watching individual vehicles.
    Clock display now shows HH:MM:SS (was HH:MM) using the fractional
    quarter index so seconds visibly tick.
- PRODUCTION INCIDENT (2026-07-06): Gustav reported "testa i webbläsaren
  det fungerade inte för mig". pytest doesn't cover frontend JS, so this
  needed BROWSER testing — set up headless Chrome + Chrome DevTools
  Protocol over a raw websocket (`pip install websocket-client`; launch
  with `--remote-debugging-port=9222 --remote-allow-origins=*` — newer
  Chrome rejects the CDP socket without that second flag) to actually
  drive the app and capture real console errors/exceptions/network
  failures, which a plain `--dump-dom` screenshot cannot see. FOUND: the
  live deployed site was sitting on 2027-09-16 FORECAST, not the
  documented 2025-09-16 historical default. ROOT CAUSE (via serve.py's
  own log): a real `/api/recalibrate?date=2027-09-16&source=forecast`
  call at 06:31:32 that morning — Gustav had tried the new "Byt dag"
  feature himself. It succeeded server-side, but the OLD design held one
  HTTP GET blocked for the full 5-14 minutes; a browser tab almost
  certainly doesn't survive that (timeout, closed tab, sleeping laptop),
  so he saw nothing happen and moved on — while the site silently
  finished recalibrating ~10 minutes later with no one watching. This is
  the SAME class of bug the developer hit earlier the same day (a
  BrokenPipeError from a client curl timeout on the same endpoint) —
  found twice before it was actually fixed. FIX: `/api/recalibrate` now
  starts a background thread and returns 202 immediately;
  `/api/recalibrate/status` reports running/done/error + elapsed
  seconds; the frontend polls with live elapsed-time button text AND
  checks status once on every page load, so a job started from any tab/
  session (including one whose tab has since closed) is still visible
  and correctly shows "still calibrating" instead of a misleadingly idle
  UI. Verified with a real CDP test that reloaded the page mid-job.
  LESSON: no admin action whose server-side work outlives a reasonable
  browser request lifetime should ever be tied to a single blocking HTTP
  call — start + poll, always.

## The data
- Source: Göteborgs Stad (Felicia Gauffin Jatta, Stadsbyggnadsförvaltningen). 15-minute two-way vehicle counts ("Antal passager"), all of 2025.
- 6 sensors, two clusters:
  - Götaplatsen area (physically ~400 m SW of the square, near Viktor Rydbergsgatan/Vasaparken): 133, 134, 2276
  - Scandinavium area: 107, 1074, 1076
- Raw inputs (what build_data.py actually reads): quarterly CSVs in `~/Downloads/Data till Chalmers_20260618/` + `~/Downloads/Mätpunkter_koordinater.csv`. (`clean.csv` was an exploration intermediate and is not part of the pipeline.)
- `level` / measured directions — VERIFIED 2026-07-03 by Gustav against Göteborgs Stad's trafikmängder catalogue (Power BI). THE DELIVERED "Total" LABEL WAS WRONG for 4 of 5 sensors — it is the catalogue's Total row, which for single-direction stations equals the one measured direction. Source of truth = SENSOR_MEASURED_DIRECTION in build_data.py:
  - 107 Skånegatan: genuinely two-way (N+S+Total). City's own D-factor: N 3400/S 3100 of 6500 (2025) = 52/48; 2023–24 exactly 50/50 — LOCAL VALIDATION of the mild-split finding and the dirsplit model (predicted 0.47–0.52).
  - 1074 Valhallagatan V, 1076 Skånegatan S, 133 Läraregatan V, 134 Gibraltargatan SO, 2276 Läraregatan V — single-direction only. Their daily means match the catalogue's ÅMVD ±3%.
  - build_data.py snaps compass-labelled sensors direction-aware (edge bearing must match the letter, else opposite carriageway within 80 m). Only 7 measured directed edges now (107's pair + 5 singles). The opposite direction at single-direction stations is UNMEASURED — never constrain or display it as known.
  - New sensors: check the city catalogue FIRST and add to SENSOR_MEASURED_DIRECTION.
- Coordinates: source file was mislabelled "SWEREF99TM" but is actually SWEREF99 12 00 (EPSG:3007). build_data.py converts to WGS84. DO NOT reconvert as TM.
- Direction is NOT recoverable from the delivered two-way totals (geometry + conservation is underdetermined). DECIDED: Felicia will NOT deliver a directional re-export — treat all "Total" values as two-way sums permanently; both directed edges of a Total sensor carry the same summed count.
  - REPORTING TRAP (found 2026-07-06 while investigating an apparent ~0.4 delivery ratio at sensor 107): because both directed edges carry the SAME raw two-way total, any ad-hoc script that reads `flows.json` directly and treats that value as the "true" per-direction measured count will show ~50% delivery on a perfectly-calibrated two-way sensor — the value must first be split by the estimated direction share (`build_sumo_demand.build_targets`/`write_counts`, which already do this correctly for both PFE targets and `validate_sim.py`'s LOSO comparison). This is a comparison-methodology gotcha, not a pipeline bug — confirmed the actual demand/validation code already applies the split; regression-tested in `tests/test_build_sumo_demand.py`.
- Known limitation (DST): timestamps are Swedish local time. 2025-03-30 has a missing hour (becomes `null`, the spring-forward gap). CORRECTED 2026-07-07 (was documented as a duplicated fall-back hour, which would need build_data.py's "last wins" dedup to resolve): verified directly against the raw CSVs — 2025-10-26 has exactly 92 rows per sensor, the SAME as 2025-03-30, not the ~100 a genuine duplicate would produce, and a full-year scan finds zero duplicate (sensor, date, time) rows anywhere. The delivered data is simply missing the same 4 quarters on both DST-transition days (the sensor export doesn't record the repeated fall-back hour at all) — build_data.py's dedup-by-last-value path exists for safety but never actually fires on this dataset.

## Architecture — keep these seams
- Two halves that never mix:
  - Offline (Python, run once): `build_data.py` reads the raw CSVs + coordinates → `network.geojson` + `flows.json`; then `build_features.py` → features + `normal_profile.json`; `train_agent1.py` → models; `build_agent1_flows.py` → `flows_forecast.json`.
  - Runtime (browser): static Leaflet app.
- The map is a GRAPH: nodes = intersections, edges = road segments. Animate EDGES, not dots.
- The seam: the renderer only ever calls `flowAt(edgeId, t)`. Today a HistoricalProvider reads flows.json. Later a ModelProvider (forecasts) and a ScenarioProvider (`flowAt(edgeId, t, scenario)` for incidents) plug into the same interface. The map/animation code never changes when the source changes.

## Contracts — fixed; everything depends on these
- `network.geojson`: LineString features = edges, Point features = nodes. Stable string IDs. WGS84 coords. Each measured edge carries `sensor_id`. Every edge carries `dist_sensor_m` and `confidence` = exp(-d²/2σ²), σ = `CONF_SIGMA_M` (250 m), d = distance to nearest sensor. NOTE: this static value is a PLACEHOLDER spatial prior (σ is a guess). In Phase 3 the real confidence comes from the simulation itself — (a) leave-one-out calibration error at the 6 sensors gives the empirical distance-decay, (b) spread across Monte Carlo runs gives per-scenario uncertainty — delivered by the ScenarioProvider per edge/scenario, replacing this prior. The renderer just displays whatever number it gets.
- `flows.json` / `flows_forecast.json`: `{epoch, interval_minutes, flows}` where `flows[edgeId][quarterIndex] = count`, `null` where missing. Same edge IDs as the GeoJSON. Epoch strings have no timezone suffix — the web app parses them as UTC (provider.js appends 'Z'); keep parse and getUTC* formatting consistent.
- One ID space across data/model/sim/map. One coordinate system (WGS84). Time = ISO datetime / abstract index — never "row in the 2025 file".
- `NormalProfile.flowAt/calmAt(edgeId, qi, dayOfWeek)`: dayOfWeek (0=Mon) MUST be derived from the ACTIVE provider's epoch (2025 starts Wednesday, 2027 Friday) — never from qi alone.

## Tech choices
- Renderer: Leaflet + CARTO light_all raster tiles (Gustav wants a LIGHT basemap — don't switch to dark). Traffic ramp #1f9d55→#d97706→#dc2626 and slate background network are validated against the light surface; re-validate colors before changing them. Background edges fade with `confidence`. ALL drivable roads within clip_radius are included (no highway-type filter). Sufficient at corridor scale. Swap to MapLibre/deck.gl ONLY if rendering thousands of edges/trajectories — and then only the render layer changes.
- Data/model: Python. Forecast model = spatio-temporal graph network (GNN family, e.g. DCRNN/Graph WaveNet) AFTER a seasonal-naïve baseline.
- Incident engine: a mechanistic simulator (likely SUMO — confirm with Miroslaw), because road closures are out-of-distribution for a model learned only on normal 2025 traffic. SUMO imports OSM and reroutes around closures by construction. "AI" = demand calibration, an ML surrogate of the simulator, or baseline forecasting.

## Build order — vertical slices that always run
0. Lock decisions: scope DECIDED (two cluster areas); directional re-export DECIDED (not coming); simulation engine still open (Miroslaw).
1. DONE — Data foundation: build_data.py → network.geojson + flows.json.
2. DONE — Animation: Leaflet + provider/state/render/clock/controls, play + scrub over 2025.
3. DONE — Forecast model ("Agent 1"): LightGBM baseline + Holiday Baseline Adjustment, exported as flows_forecast.json (Prognos 2027 toggle in the web app). GNN deferred.
4. IN PROGRESS — Incident simulation (SUMO). Working vertical slice DONE (2026-07-02):
   - `build_sumo_net.py`: graph.graphml → plain XML → netconvert → sumo/net.net.xml. SUMO edge IDs are IDENTICAL to our edge IDs (u_v_k) — never break this.
   - `estimate_directions.py`: ESTIMATES a time-of-day direction split for Total sensors — unsupervised AM/PM Gaussian decomposition of each September weekday profile (R² 0.73–0.89), AM component assigned to the directed edge pointing toward the city centre (prior, not measured). Peak split ~80/20. Falls back to 50/50 when fit is weak. → sumo/direction_split.json
   - `build_sumo_demand.py`: 15-min sensor counts (directional via the estimated split) → randomTrips candidate pool → routeSampler calibration. Fit: 100% GEH<5 at all 11 edges, all intervals. Also exports the implied OD matrix (calibrated trips aggregated to zones: two cluster areas + 8 compass entry sectors) → web/data/od_matrix.json/.csv — ONE plausible OD consistent with the counts; the true OD is not identifiable from 6 counters. Only sensor-crossing traffic is calibrated — streets far from sensors carry little traffic, which the confidence value communicates honestly.
   - `run_scenario.py [--close edgeId]`: Monte Carlo (3 seeds), per-edge 15-min flows + confidence = spatial_prior × exp(-CV) → web/data/scenarios/*.json + index.json manifest. Uses --ignore-route-errors (vehicles destined for the closed edge are dropped).
   - Web: "Scenario" toggle + dropdown; scenario colours ALL simulated edges (dots stay on sensor edges for perf); closed edges drawn black-dashed; URL params ?mode=scenario&file=&qi=.
   - MESOSCOPIC BY DEFAULT (2026-07-03): run_scenario runs sumo --mesosim (queue-based). Rationale: our product is 15-min edge flows, which doesn't need microscopic car-following. Measured: whole-day 3-seed closure 35 s meso vs 25 min micro (43×), AND delivery is BETTER (simulated/measured at sensors 0.87–0.96, total 0.91, vs micro 0.83–0.94). --micro flag kept for comparison runs. Closure rerouter is attached only to edges within 400 m of the closure (REROUTER_RADIUS_M) — global rerouter cost was the other bottleneck.
   - WHOLE-DAY WINDOWS: make demand builds 00:00–24:00 (build_sumo_demand --end 24:00 special-case; candidate pool capped at ~10k trips); make demand-morning keeps the fast 06–10 window for iteration. Scenario mode scrubs all 96 quarters.
   - INTERACTIVE CLOSURES (2026-07-03): `make serve` runs serve.py — static web/ + /api/close?edges=a,b,c which shells out to run_scenario.py (one sim at a time, lock + 409). Whole-day interactive closure ≈ 40 s thanks to meso. Web: 🚧 Stäng väg mode (feature-detected via /api/ping, hidden on static hosting) — click edges to select (red-dashed pending style), Simulera runs the MC and auto-loads the scenario. run_scenario supports MULTIPLE --close edges (scenario JSON: closed_edges list); shared closure rerouter file per scenario. Scenario fetches use cache-busting (re-runs overwrite files in place).
   - DATA INTAKE: data_in/ drop folder (build_data auto-discovers; falls back to the original Downloads delivery). New-sensor workflow in data_in/README.md; `make refresh` re-runs the whole chain raw→scenarios.
   - Simulation uses the FULL graphml graph (~2 250 edges) so rerouting has real alternatives; the web app displays the subset in network.geojson.
   REMAINING: leave-one-out validation at the 6 sensors (empirical confidence decay); whole-day windows; more scenarios; per-vehicle trajectory playback (FCD → TrajectoryProvider); ML surrogate.
5. IN PROGRESS — Trained direction-split model (`dirsplit/` package), replacing the AM/PM-Gaussian guess in estimate_directions.py:
   - Training data: OPEN hourly directional counts from Statens vegvesen's trafikkdata GraphQL API (no key) — 394 stations in Oslo/Bergen/Trondheim/Stavanger bboxes; volumes fetched per station for 4 ISO weeks (36,37,20,45) of its newest available year. UK DfT raw counts identified as secondary source (hourly 07–19 by direction, bulk CSV) — not yet integrated.
   - Heading→bearing: station directions are PLACE NAMES; resolved by geocoding both names (Nominatim, cached, 1 req/s) and matching against the OSM edge's two axis bearings, with a consistency requirement (opposite candidates, ≤75° each) — ambiguous stations are EXCLUDED, all decisions stored for audit in stations_matched.json.
   - ONE feature code path (dirsplit/features.py) for training stations and Gothenburg sensor edges: road class/speed/lanes/oneway, dist-to-centre, radial_cos (toward-centre alignment), residential/major street length behind vs ahead within 1 km half-discs (population/activity proxy from the road graph itself — upgrade path: GHS-POP raster). New sensors in network.geojson are picked up automatically.
   - Applicability check (dirsplit/coverage.py): kNN distance of each sensor edge in standardized feature space vs the training cloud; >90th percentile ⇒ flagged EXTRAPOLATION.
   - make dirsplit-stations / dirsplit-volumes / dirsplit-match / dirsplit-dataset / dirsplit-train / dirsplit-predict / dirsplit-coverage. Raw volumes gitignored (re-fetchable); metadata/matches/coverage tracked.
   - TRAINED & DEPLOYED (2026-07-02): 346 stations' volumes, 218 matched, 15 346-row table. KEY FINDINGS (report these honestly):
     (a) One-way/ramp stations must be filtered (97 dropped) — their 0/1 shares poison training.
     (b) Real two-way city streets are MILDLY asymmetric: typical weekday-daytime deviation from 50/50 is only 5–8 pp (55/45), NOT the 80/20 the old Gaussian estimate produced. The Gaussian AM/PM decomposition over-attributes — both directions peak in the morning, just slightly unevenly.
     (c) Leave-city-out MAE ≈ baseline overall (+8.6% Oslo — most Gothenburg-like, +1.8% Trondheim, worse Bergen/Stavanger): the transferable signal is real but small. The model's value is CALIBRATION (mild, empirically-grounded splits) rather than large error reduction.
     Deployed via dirsplit/predict.py → sumo/direction_split.json (pair-normalised, clamped [0.1,0.9], hourly→96 slots). make demand auto-prefers the model over the Gaussian fallback. Sensor 1074's edges flagged EXTRAPOLATION in coverage (94th pctl) — carried into predictions. estimate_directions.py kept only as fallback when model.pkl is absent.
   - UPGRADED (same day): similarity-weighted training (Gaussian kernel toward the Gothenburg edges' features, bandwidth=median), PER-SENSOR locally weighted models ("each road trained for itself" — new sensors get their own model on retrain), and QUANTILE regression q10/q50/q90. Domain-matched leave-city-out (stations resembling ours, locally weighted like the deployment): Oslo +11.1%, Trondheim −0.9%, Bergen/Stavanger worse (their streets are ~exactly 50/50 — nothing to predict). The interval IS the honest product: predict.py writes edge_shares(_q10/_q90); build_sumo_demand builds THREE demand variants (q50/q10/q90 — all still 100% GEH<5, the sum constraint is split-invariant); run_scenario spreads Monte Carlo seeds over the variants so direction uncertainty reaches the per-edge confidence.
   - Local ground truth check: sensor 1076 (the only direction-measured street) has weekday AM/PM ratio 0.90 in its single direction — nearly symmetric, confirming the mild-split finding for these central mixed-use streets.
   - FINAL METHODOLOGY (2026-07-02, "absolut bäst" pass): (i) mirrored-duplicate rows removed — train on toward-centre direction only (predict.py orients each pair accordingly); (ii) station-level weight normalisation — hourly rows within a station are correlated, every station gets equal total influence; (iii) James-Stein-style SHRINKAGE calibrated on pooled leave-city-out domain predictions: λ=0.256 — only ~26% of predicted deviation from 50/50 is transferable signal; deployment uses 0.5+λ(pred−0.5), intervals re-centred. Pooled domain MAE: shrunk 0.0559 beats 50/50's 0.0564 (and raw 0.0654). By construction the shrunk estimate cannot be worse than 50/50 in expectation. Literature anchor: this is the traffic-engineering D-factor; FHWA/TxDOT typical urban values 0.50–0.59 — our shrunk predictions (0.47–0.52 ±0.1) sit exactly there.
   - Gothenburg's own trafikmängder Power BI (public link on goteborg.se) may hold per-direction city measurements near the clusters — check manually; a local directional measurement would beat all of the above as calibration.
   - REMAINING: UK DfT integration for more training breadth.

## Rules — do / never
- DO route all flow access through `flowAt(edgeId, t)`. NEVER fetch data inside render code.
- DO render whatever edges are in the GeoJSON. NEVER hardcode the 6 sensors or a fixed marker list.
- DO create map layers once and `setStyle` on tick. NEVER recreate markers/layers per frame.
- DO treat time as a datetime. NEVER key logic on CSV row order.
- DO emit `null` for missing data and draw it as a gap. NEVER render missing as 0.
- DO keep WGS84 everywhere on the map; use SWEREF only for metric distance maths.

## Open questions
- Miroslaw: confirm the simulation engine (SUMO / MATSim / his own).
- (Closed: scope = two cluster areas; Felicia will not send anything more — no directional re-export.)

## Files
- Pipeline (run in order): `build_data.py` → `build_features.py` → [`build_dataset.py` for future GNN] → `train_agent1.py` → `build_agent1_flows.py`. Or just `make all` (Makefile has the raw-data paths; `make serve` starts the web app).
- SUMO (Phase 3): `make sumo-net` → `make demand` → `make scenario` (or `python3 run_scenario.py --close <edgeId>`). Requires `pip install eclipse-sumo`. Intermediates in `sumo/` (gitignored); web products in `web/data/scenarios/` (tracked).
- `web/data/graph.graphml` — exact OSM graph snapshot (same node/edge IDs as network.geojson). Phase 3 SUMO/demand work MUST start from this graph, never a fresh OSM download.
- `explore.py` — one-off data exploration/plots. `tests/` — contract + pipeline tests (`python3 -m pytest tests/`).
- Generated in `web/data/`: network.geojson, flows.json, flows_forecast.json, normal_profile.json, features/, agent1/.
- Web app: `web/` (index.html, provider.js, state.js, render.js, controls.js, clock.js). Serve with `cd web && python3 -m http.server 8000`.
- (The old `archive/web_update_2026-06-27/` experiment was deleted 2026-07-02 — superseded by the real scenario system.)
