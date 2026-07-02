# Traffic animation & incident simulation — Gothenburg

Persistent project context for Claude Code. Keep this file in the repo root.

## What this project is
Summer project for Prof. Miroslaw Staron (Chalmers). Owner: Gustav (student).
Goal arc, in order:
1. Animate historical traffic flow on a real map of Gothenburg.
2. Train an AI model to forecast normal flow.
3. Simulate traffic after incidents (road closures) — the real goal.

## Scope — DECIDED
- NOT the whole city, and NOT one continuous corridor. Scope = TWO small areas, one around each sensor cluster (Götaplatsen cluster and Scandinavium cluster), a few hundred metres around the sensors.
- Implemented in build_data.py: background edges are kept only within `--clip_radius` (default 400 m) of the NEAREST sensor.
- Reason: only 6 sensors exist. You can only validate/calibrate where there is ground truth; a citywide claim would be ungrounded extrapolation.
- Every edge carries a simulation confidence (see Contracts) that decays with distance from the nearest sensor — simulated results far from ground truth must be presented as less trustworthy.

## The data
- Source: Göteborgs Stad (Felicia Gauffin Jatta, Stadsbyggnadsförvaltningen). 15-minute two-way vehicle counts ("Antal passager"), all of 2025.
- 6 sensors, two clusters:
  - Götaplatsen area (physically ~400 m SW of the square, near Viktor Rydbergsgatan/Vasaparken): 133, 134, 2276
  - Scandinavium area: 107, 1074, 1076
- Raw inputs (what build_data.py actually reads): quarterly CSVs in `~/Downloads/Data till Chalmers_20260618/` + `~/Downloads/Mätpunkter_koordinater.csv`. (`clean.csv` was an exploration intermediate and is not part of the pipeline.)
- `level`: five sensors = "Total" (both directions summed). 1076 = "S" (single direction only; NOT comparable to the Totals).
- Coordinates: source file was mislabelled "SWEREF99TM" but is actually SWEREF99 12 00 (EPSG:3007). build_data.py converts to WGS84. DO NOT reconvert as TM.
- Direction is NOT recoverable from the delivered two-way totals (geometry + conservation is underdetermined). DECIDED: Felicia will NOT deliver a directional re-export — treat all "Total" values as two-way sums permanently; both directed edges of a Total sensor carry the same summed count.
- Known limitation (DST): timestamps are Swedish local time. 2025-03-30 has a missing hour (becomes `null`), 2025-10-26 has a duplicated hour — build_data.py keeps the last value ("last wins"), so 4 slots of real data are dropped there.

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
   - Web: "Scenario" toggle + dropdown; scenario colours ALL simulated edges (dots stay on sensor edges for perf); closed edge drawn black-dashed; URL params ?mode=scenario&file=&qi=.
   - Simulation uses the FULL graphml graph (~2 250 edges) so rerouting has real alternatives; the web app displays the subset in network.geojson.
   REMAINING: leave-one-out validation at the 6 sensors (empirical confidence decay); whole-day windows; more scenarios; per-vehicle trajectory playback (FCD → TrajectoryProvider); ML surrogate.
5. IN PROGRESS — Trained direction-split model (`dirsplit/` package), replacing the AM/PM-Gaussian guess in estimate_directions.py:
   - Training data: OPEN hourly directional counts from Statens vegvesen's trafikkdata GraphQL API (no key) — 394 stations in Oslo/Bergen/Trondheim/Stavanger bboxes; volumes fetched per station for 4 ISO weeks (36,37,20,45) of its newest available year. UK DfT raw counts identified as secondary source (hourly 07–19 by direction, bulk CSV) — not yet integrated.
   - Heading→bearing: station directions are PLACE NAMES; resolved by geocoding both names (Nominatim, cached, 1 req/s) and matching against the OSM edge's two axis bearings, with a consistency requirement (opposite candidates, ≤75° each) — ambiguous stations are EXCLUDED, all decisions stored for audit in stations_matched.json.
   - ONE feature code path (dirsplit/features.py) for training stations and Gothenburg sensor edges: road class/speed/lanes/oneway, dist-to-centre, radial_cos (toward-centre alignment), residential/major street length behind vs ahead within 1 km half-discs (population/activity proxy from the road graph itself — upgrade path: GHS-POP raster). New sensors in network.geojson are picked up automatically.
   - Applicability check (dirsplit/coverage.py): kNN distance of each sensor edge in standardized feature space vs the training cloud; >90th percentile ⇒ flagged EXTRAPOLATION.
   - make dirsplit-stations / dirsplit-volumes / dirsplit-match / dirsplit-coverage. Raw volumes gitignored (re-fetchable); metadata/matches/coverage tracked.
   - REMAINING: full multi-city volume+match fetch (hours, throttled), profile features from volumes, LightGBM training with leave-city-out validation, wire predictions into build_sumo_demand (replace estimate_directions), UK DfT integration.

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
- `archive/web_update_2026-06-27/` — an older web experiment incl. sim_provider.js sketch; NOT current code, never copy it over web/.
