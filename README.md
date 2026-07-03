# Gothenburg Traffic Simulation

Traffic flow animation, ML forecasting and (upcoming) incident simulation around
two sensor clusters in Gothenburg — **Götaplatsen** and **Scandinavium**.
Six real traffic sensors, 15-minute vehicle counts for all of 2025, provided by
Göteborgs Stad. Summer project at Chalmers (supervisor: Prof. Miroslaw Staron).

## What it does

1. **Animate** historical traffic on a real map of Gothenburg (done)
2. **Forecast** normal flow with an ML model, exported as a 2027 forecast (done)
3. **Simulate** traffic after incidents / road closures with SUMO (working slice —
   calibrated demand, closure rerouting, Monte Carlo confidence, scenario mode in the web app)

![Web app](plots/daily_profile.png)

## Scope

Two small areas (~400 m) around each sensor cluster — **not** the whole city.
Only 6 sensors exist, so results can only be validated where there is ground
truth. Every edge in the network carries a `confidence` value (0–1) that decays
with distance from the nearest sensor; the web app shows it on hover, and
Phase 3 simulation results must be presented with it.

| Cluster | Sensors | Type |
|---|---|---|
| Götaplatsen (near Viktor Rydbergsgatan) | 133, 134, 2276 | Total (two-way sum) |
| Scandinavium | 107, 1074 | Total (two-way sum) |
| Scandinavium | 1076 | S (single direction) |

Direction is **not** recoverable from the delivered two-way totals — treat all
"Total" values as sums of both directions permanently.

## Architecture

Two halves that never mix:

- **Offline (Python)** builds static artifacts:
  `build_data.py` → `network.geojson` + `flows.json` + `graph.graphml`
  → `build_features.py` → feature matrices + `normal_profile.json`
  → `train_agent1.py` → per-sensor models → `build_agent1_flows.py` → `flows_forecast.json`
- **Runtime (browser)** is a static Leaflet app (`web/`), no server needed.

The renderer only ever calls `flowAt(edgeId, quarterIndex)` — the provider seam.
Historical data, the forecast, and the future SUMO ScenarioProvider all plug in
behind the same interface without touching map code.

## Running

```bash
pip install -r requirements.txt

# Full pipeline (auto-discovers data_in/, falls back to the original delivery)
make all

# Web app + scenario API → http://localhost:8000
make serve
```

**Close roads from the map:** with `make serve` running, click **🚧 Stäng väg**,
select any streets on the map, and hit *Simulera* — the server runs the SUMO
Monte Carlo on demand (~1 min) and the map switches to the resulting scenario,
closed edges drawn black-dashed and diverted traffic recoloured. Multiple
simultaneous closures are supported (`run_scenario.py --close e1 e2 …` from
the CLI does the same).

**Feeding it new data:** drop new quarterly sensor CSVs in `data_in/`
(see `data_in/README.md`), check the station's measured direction in the
city's traffic catalogue, add it to `SENSOR_MEASURED_DIRECTION`, and run
`make refresh` — the new sensor gets an edge on the map, a coverage check,
its own direction model and a place in the demand calibration automatically.

Web app controls: play/scrub 2025 traffic, toggle to the 2027 forecast or a
SUMO scenario, space = play/pause, ←/→ = ±15 min, Shift+←/→ = ±1 day. Hover
any road for counts and simulation confidence.

## Incident simulation (SUMO)

```bash
make sumo-net    # graph.graphml → SUMO network (edge IDs identical to the map)
make demand      # direction-split estimate + calibrate demand against the 6 sensors
make scenario    # baseline + a Skånegatan closure, 3 Monte Carlo seeds each
# custom closure:
python3 run_scenario.py --close <edgeId>
```

**Direction & OD estimation.** The sensors deliver two-way sums, so
`estimate_directions.py` decomposes each sensor's daily profile into
off-peak + AM-peak + PM-peak components (least-squares Gaussian fit,
R² 0.73–0.89) and assigns the AM component to the direction pointing toward
the city centre — an estimated, time-varying split (~80/20 at peak) instead
of a naive 50/50. The calibrated routes are then aggregated into an
origin–destination matrix over zones (the two cluster areas + eight compass
entry sectors) → `web/data/od_matrix.json`/`.csv`. Both are estimates and
labelled as such: the true direction split and OD are not identifiable from
six counting points.

## Trained direction-split model (`dirsplit/`, in progress)

Instead of guessing the split, a model is trained on cities where hourly
**directional** counts are open data: Statens vegvesen's
[trafikkdata API](https://trafikkdata.atlas.vegvesen.no/om-api) (394 stations
in Oslo, Bergen, Trondheim, Stavanger). One shared feature pipeline describes
every directed edge — road class, speed, lanes, toward-centre alignment,
residential streets behind vs destinations ahead — identically for Norwegian
stations and Gothenburg sensor edges, so adding a sensor requires nothing but
a row in `network.geojson`. Station directions arrive as place names; they
are resolved to bearings by geocoding + road-axis matching with a consistency
requirement, and ambiguous stations are excluded (all decisions stored for
audit). An applicability-domain check (kNN in standardized feature space)
verifies the Gothenburg edges lie inside the training distribution before any
prediction is trusted.

```bash
make dirsplit-stations   # station metadata (open API, no key)
make dirsplit-volumes    # hourly volumes by direction (resumable)
make dirsplit-match      # OSM matching + heading→bearing resolution
make dirsplit-coverage   # are our sensor edges inside the training cloud?
```

Planned: profile features from the fetched volumes, LightGBM training with
leave-city-out validation, then wiring predictions into the SUMO demand
calibration in place of `estimate_directions.py`.

Scenarios appear under the **Scenario** toggle in the web app: every simulated
street is coloured by flow, the closed edge is drawn black-dashed, and each
edge's confidence combines the distance-to-sensor prior with the spread across
Monte Carlo seeds. "Total" sensor counts are split 50/50 per direction
(direction is not recoverable from the delivered data), and only
sensor-crossing traffic is calibrated — the confidence value says exactly
where the simulation can and cannot be trusted.

## Forecast model ("Agent 1")

LightGBM baseline (7 cyclic time features, trained on non-holiday days only)
+ **Holiday Baseline Adjustment**: per-sensor factors
`actual_2025 / baseline_prediction` for 15 Swedish holidays. The baseline
handles the day-of-week shift between years; the factor captures the holiday
effect itself (e.g. the New Year's Eve midnight spike) with no manual
corrections.

Cross-validated MAE beats the seasonal-naïve baseline by 12–29 % on all six
sensors (non-holiday days). Note: holiday factors are calibrated, not
cross-validated — with a single year of data each holiday is observed once.

## Repository layout

```
build_data.py          raw CSVs + OSM → network.geojson, flows.json, graph.graphml
build_features.py      flow matrix, adjacency, normal profile, train/val/test split
build_dataset.py       windowed datasets for a future GNN
train_agent1.py        LightGBM baseline + holiday factors
build_agent1_flows.py  2027 forecast → flows_forecast.json
build_sumo_net.py      graph.graphml → SUMO network (same edge IDs as the map)
build_sumo_demand.py   sensor counts → calibrated SUMO routes (routeSampler)
run_scenario.py        baseline/closure runs → web/data/scenarios/*.json
explore.py             one-off EDA, writes plots/
tests/                 contract + pipeline tests (python3 -m pytest tests/)
web/                   static Leaflet app (index.html, provider/state/render/clock/controls)
web/data/              generated artifacts incl. graph.graphml (exact OSM snapshot —
                       Phase 3 SUMO work must start from this graph, not a fresh download)
CLAUDE.md              full project rules, contracts and decisions
```
