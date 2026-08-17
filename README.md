# Gothenburg Traffic Simulation

Traffic flow animation, ML forecasting and incident (road closure) simulation
for Gothenburg's inner city. Six real traffic sensors, 15-minute vehicle
counts for all of 2025, provided by Göteborgs Stad. Summer project at
Chalmers (supervisor: Prof. Miroslaw Staron).

## Working with AI models

The repository supports Codex, Claude and other models as interchangeable
actors. Any capable actor may plan, implement, test or review; there is no fixed
Sol/Luna routing or mandatory state machine. Start with `AGENTS.md`, then use
the marked current blocks in `TASKS.md` and `AGENT_NOTES.md` for context.
Historical role labels remain only for traceability.

## What it does

1. **Animate** historical traffic on a real map of Gothenburg (done)
2. **Forecast** normal flow with an ML model, exported as a 2027 forecast (done)
3. **Simulate** traffic after incidents / road closures with SUMO (done —
   calibrated demand, closure rerouting incl. time-windowed closures, Monte
   Carlo confidence, multi-day/week scenarios, scenario mode in the web app)
4. **Suggest** the least-disruptive time to close a road (implemented — the
   default proxy-ranked search is fast; `--exhaustive` evaluates every
   feasible window before making a global-best claim)

![Web app](plots/daily_profile.png)

## Scope

The canvas is Gothenburg's **inner city** (river → Krokslätt, Vallgraven →
Gårda; ~7 100 directed edges) — not the whole city, and no longer just the
two original sensor clusters (that scope was superseded 2026-07-05). Only 6
sensors exist, so accuracy is a **gradient**: hard/measured near a sensor,
prior-driven further away. Every edge carries a `confidence` value (0–1) —
`exp(-d²/2σ²)`, with σ fitted from real leave-one-station-out validation
(currently 144.0 m), not a guessed constant — that decays with distance from
the nearest sensor. The web app shows it on hover; simulation results are
presented with it rather than a false claim of citywide accuracy.

| Cluster | Sensors | Type |
|---|---|---|
| Götaplatsen (near Viktor Rydbergsgatan) | 133, 134, 2276 | Single direction |
| Scandinavium | 1074, 1076 | Single direction |
| Scandinavium | 107 | Genuinely two-way (both directions measured) |

Direction is **not** recoverable from the delivered two-way totals for the
5 single-direction sensors — every "Total" value is treated as a sum of both
directions permanently. A trained model (see `dirsplit/` below) estimates
the split for simulation purposes; it is disclosed as an estimate, not a
measurement.

## Architecture

Two halves that never mix:

- **Offline (Python)** builds static artifacts:
  `build_data.py` → `network.geojson` + `flows.json` + `graph.graphml`
  → `build_features.py` → feature matrices + `normal_profile.json`
  → `train_agent1.py` → per-sensor models → `build_agent1_flows.py` → `flows_forecast.json`
- **Runtime (browser)** is a static Leaflet app (`web/`); `serve.py` adds an
  optional local API for recalibration, road-closure simulation/search, and
  signal studies (feature-detected — historical, forecast, and already-built
  scenarios still work when hosted fully static).

The renderer only ever calls `flowAt(edgeId, quarterIndex)` — the provider
seam. Historical data, the forecast, and SUMO scenarios all plug in behind
the same interface without touching map/render code.

## Running

**Bara titta på kartan?** Den publicerade versionen kräver ingen dator av
dig alls: <https://gust050.github.io/gothenburg-traffic-sim/>. Karta,
2025 års historik, 2027-prognosen och de färdigbyggda scenarierna ligger
där (`.github/workflows/pages.yml` publicerar `web/` vid varje push till
`main`). Att köra *nya* avstängningar kräver fortfarande en lokal server —
den knappen döljer sig själv på den publicerade sidan, eftersom det inte
finns någon simulator bakom den.

```bash
pip install -r requirements.txt

# Full pipeline (auto-discovers data_in/, falls back to the original delivery)
make all

# Web app + scenario API → http://localhost:8000
make serve
```

`make serve` needs nothing but Python's standard library — the map, the
historical/forecast animation and every already-built scenario work on a
fresh clone before `pip install -r requirements.txt` has ever succeeded.
Only the endpoints that actually run a simulation (recalibration, closures,
signal studies) need the packages in `requirements.txt` and SUMO; each one
reports its own error if they are missing, instead of taking the server
down with it.

It also picks its own port and opens its own browser: a busy 8000 steps to
8001, 8002 and so on, and the URL that is printed is the one that was
actually bound. `--port N` pins a port instead (used as given, never
moved); `--no-open` skips the browser, which is also the default whenever
stdout is not a terminal, so `make serve &` and CI stay quiet.

**På macOS:** dubbelklicka `start.command` i Finder. Den byter själv till
repo-mappen, så den kan inte startas "på fel ställe", och kartan öppnas i
webbläsaren. Stäng terminalfönstret för att stoppa servern.

### Från noll på en ny dator

Ett kommando som klonar om det behövs, uppdaterar annars, och startar:

```bash
git clone https://github.com/GUST050/gothenburg-traffic-sim.git \
  ~/gothenburg-traffic-sim 2>/dev/null; \
  cd ~/gothenburg-traffic-sim && git pull --ff-only && python3 serve.py
```

**Om `localhost` säger ERR_CONNECTION_REFUSED:** ingen server lyssnar —
webbläsaren kan inte säga mer än så, men terminalen kan. Kör `python3
serve.py` i repo-roten och läs vad som skrivs ut:

- `Serving web/ + scenario-API på http://localhost:PORT` → servern lever;
  öppna **den** adressen (samma terminalfönster måste stå kvar öppet).
- `cd: no such file or directory` → repot är inte utcheckat på maskinen;
  se "Från noll" ovan. Ingen kod kan starta en server från ett repo som
  inte finns lokalt — det är den enda varianten av det här felet som inte
  går att bygga bort.
- `Portarna 8000-8019 är alla upptagna` → välj en ledig med `--port`.
- `Hittar inte web/data/network.geojson` → kartdatan är inte byggd i den
  här kopian; kör `make data`.
- En Python-`Traceback` → skicka den vidare; det är den enda felsökningen
  webbläsaren själv aldrig kan visa.

**Close roads from the map:** with `make serve` running, open
**Vägavstängning**. One workspace handles all road-closing scopes: simulate
the active period, optimize a window within the active day, or search a
multi-day permitted calendar. Select any streets on the map and run the
chosen operation. The server uses SUMO and the map can load the resulting
scenario with closed edges black-dashed and diverted traffic recoloured.
Multiple simultaneous closures are supported (`run_scenario.py --close e1
e2 …` from the CLI does the same).

**Simulating more than one day:** `build_sumo_demand.py --start-date
YYYY-MM-DD --days N` (N up to 7) builds one continuous multi-day demand
instead of a single day; `/api/recalibrate?date=&days=N` does the same from
the web UI's "Byt dag" panel. Candidate route geometry is pooled by weekday/
weekend, not regenerated per day, so a week costs roughly proportionally
more than one day, not N times more — see `IMPROVEMENT_PLAN.md` for measured timings.
Per-scenario trajectory (individual-vehicle) export defaults off above one
day (file size); pass `--trajectories` to force it on.

**Pre-warming a horizon:** every calendar day is calibrated once and stored
in a content-addressed day library (`runs/demand-days/`), so a window that
already has its days is assembled in seconds instead of solved again.
`make warm-horizon FROM=2027-01-01 TO=2027-12-31` fills that library ahead of
time — measured ~100–150 s per day-slot, about 30 h and ~24 GB for a full
year, run as a background job. Each build's own `runs/demand-*` archive is
discarded once its days are stored (it is a ~100 MB-per-day-slot uncompressed
duplicate under a build key nothing looks up; `--keep-archives` retains
them). It is safe to stop with ^C and rerun (it
resumes from `runs/warm-horizon/…/progress.json`), a window that fails is
recorded and the rest still build, and it takes the shared workspace lock
around each build, so the web app refuses a simultaneous simulation with a
message naming the warm run instead of interleaving files with it.
`make warm-horizon-plan` prints the plan and any date it cannot cover.

**Feeding it new data:** drop new quarterly sensor CSVs in `data_in/`
(see `data_in/README.md`), verify the station's measured direction in the
city's traffic catalogue, add its metadata to `data_in/sensors.json`, and run
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

Demand calibration is a 4-level hierarchy
(`traffic_sim/demand/pfe.py`, `observability.py`, `assignment_priors.py`,
`prior_flows.py`): hard measured counts, mathematical
conservation bounds (junction/corridor), a structural gravity/stochastic-
multipath assignment field (Dial-style, the missing "4th step" of the
classic 4-step transport model), and entropy-maximising route-flow solving
(IPF/Bregman balancing) so flow disperses across realistic alternative
routes instead of collapsing onto one canonical path. Validated by
leave-one-station-out cross-validation (`python3 validate_sim.py` →
`web/data/loso_report.json`) — the honest empirical answer to "how wrong is
the program on a street it can't see."

Closed edges reroute live in SUMO; a vehicle with genuinely no detour around
a closure has its route truncated at the last reachable edge (it drives most
of the trip and "parks" short of the closure) rather than being deleted
outright or silently teleported through the closed edge. Disruption quality
for a closure (`closure_metrics.py`) is scored primarily by Δ total
`timeLoss` against a same-demand baseline, with teleports and stranded
vehicles as hard disqualifying guards — GEH (sensor-fit) is deliberately
**not** used here, since it's blind to waiting time.

Every SUMO network build also writes `sumo/network_audit.json`, a provenance
sidecar showing imported versus defaulted speed/lane values, movement and
restriction tags, roundabouts, and the TLS membership produced by netconvert.
Golden artifacts can be staged and activated through `release_registry.py`.
Each normal/closure/signal case is an integrity-checked bundle, so its scenario,
trajectory and exact route inputs cannot drift independently. Case-specific
subdirectories preserve same-named run manifests without collisions.
Activation requires an explicit validation record and can be rolled back by a
single pointer flip. Golden activation fails closed unless the full suite,
browser/API smoke, peak-memory measurement, and rollback exercise are all
recorded as passing. Golden rollback revalidates the predecessor's complete
bundle and gates before moving the pointer.

**Direction & OD estimation.** The calibrated routes are aggregated into an
origin–destination matrix over zones (inner-city sub-areas + eight compass
entry sectors) → `web/data/od_matrix.json`/`.csv`. Both the direction split
and the OD matrix are estimates and labelled as such: the true direction
split and OD are not identifiable from six counting points.

## Trained direction-split model (`dirsplit/`, deployed)

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

**Deployed finding:** real two-way city streets are only *mildly* asymmetric
— typical weekday deviation from 50/50 is 5–8 percentage points (e.g. 55/45),
not the 80/20 an earlier, unvalidated Gaussian AM/PM heuristic assumed.

A leakage-free tournament (`dirsplit/benchmark.py`: leave-city-out,
leave-station-out and blocked-date folds, everything refitted inside each fold,
bootstrapped over independent groups) then measured which model deserves to
ship. The per-sensor LightGBM quantile models did not: on the population they
served, their raw form was 11% *worse* than a flat 50/50 leave-city-out, and
their shrunk deployed form was statistically indistinguishable from it. The
winner is the simplest candidate — an hour × day-type D-factor pooled toward
50/50, with no street features at all — which beats 50/50 by 4.0% with a
bootstrap interval excluding zero. That model, and only that model, is now
deployed; the boosted family, its trained package and the Norwegian
acquisition client were removed in 2026-08.

The deployed profile is a toward-centre curve (0.546 at 06:00 → 0.469 at
15:00, at most 4.6 points from 50/50), oriented per edge from the published
network geometry, with q10/q90 as *uncalibrated* stress bounds taken from the
same model's leave-city-out residuals. Where the city publishes a local
per-direction volume — today sensor 107's 3 400/3 100 for 2025 — that
period aggregate is bound in the sensor registry and re-levels the estimated
profile at load time, without ever being treated as 96 measured quarters.

```bash
make dirsplit-dataset      # assemble the training tables (needs raw volumes)
make dirsplit-predict      # → sumo/direction_split.json (edge_shares_q10/q50/q90)
make dirsplit-benchmark    # the model tournament (Gate M)
make dirsplit-coverage     # are our sensor edges inside the training cloud?
make dirsplit-observability  # evidence profile per sensor edge
```

`make demand` runs `dirsplit.predict` directly — there is one direction model
and no fallback path to drift from it.

Scenarios appear under the **Simulering** toggle in the web app: every
simulated street is coloured by flow (including a genuine, honest zero —
not hidden as missing data), the closed edge is drawn black-dashed, and each
edge's confidence combines the distance-to-sensor prior with the spread
across Monte Carlo seeds and direction-split variants.

## Forecast model ("Agent 1")

LightGBM baseline (7 cyclic time features, trained on non-holiday days only)
+ **Holiday Baseline Adjustment**: per-sensor factors
`actual_2025 / baseline_prediction` for 15 Swedish holidays. The baseline
handles the day-of-week shift between years; the factor captures the holiday
effect itself (e.g. the New Year's Eve midnight spike) with no manual
corrections, except when a mapped holiday's 2027 date falls on a *different*
day-of-week than its 2025 source (e.g. Första maj, Thursday in 2025 but
Saturday in 2027) — `build_agent1_flows.py`'s `dow_correction_ratios`
corrects for that specific mismatch.

Cross-validated MAE beats the seasonal-naïve baseline by 12–29 % on all six
sensors (non-holiday days). Note: holiday factors are calibrated, not
cross-validated — with a single year of data each holiday is observed once.

## Repository layout

```
traffic_sim/
  core/                 shared contracts and content fingerprints
  intake/               sensor registry and data-intake helpers
  demand/               PFE solver/kernel and candidate-artifact cache
  confidence/           held-out validation and validation reporting
  simulation/           SUMO runtime, network metadata/audits, disruption metrics
  ops/                  run and release registries

build_data.py           intake CLI -> network.geojson, flows.json, graph.graphml
build_features.py       flow matrix, adjacency, normal profile and splits
build_dataset.py        windowed datasets for a future GNN
train_agent1.py         LightGBM baseline + holiday factors
build_agent1_flows.py   2027 forecast -> flows_forecast.json
build_sumo_net.py       graph.graphml -> SUMO network (stable edge IDs)
build_candidates.py     DeSO/OSM/RVU-grounded candidate routes
build_sumo_demand.py    demand orchestration and calibrated SUMO routes
run_scenario.py         baseline/closure Monte Carlo runs
serve.py                static web + optional local API
observability.py,       demand-estimation stages (root CLI modules)
assignment_priors.py,
prior_flows.py
pfe.py, pfe_kernel.py   compatibility imports for traffic_sim/demand/
validate_sim.py         stable CLI wrapper for traffic_sim/confidence/loso.py
validation_report.py    stable CLI wrapper for traffic_sim/confidence/report.py
dirsplit/               trained direction-split model package
demand/                 demand model components and data contracts
web/                    static Leaflet browser runtime
web/data/               generated artifacts and exact graph snapshot
data_in/                user-delivered sensor/DeSO/POI inputs
sumo/, runs/, cache/    generated intermediates, manifests and caches
tools/                  bounded experiments and probes
tests/                  contract + pipeline tests
ARCHITECTURE.md         structural source of truth
IMPROVEMENT_PLAN.md     canonical improvement and delivery plan
```

Former shared root modules remain compatibility imports or CLI wrappers
pointing at `traffic_sim/`; they are intentionally not duplicate source. Run existing
commands from the repository root so relative artifact paths and Makefile
contracts remain deterministic.
