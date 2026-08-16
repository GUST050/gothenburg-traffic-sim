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

Direction is only partly measured. Sensor 107 carries a two-way total plus a
published local period D-factor; the other five stations measure one reviewed
carriageway each and leave the opposite carriageway unmeasured. A trained model
(see `dirsplit/` below) estimates the missing allocation for simulation
purposes. The measured carriageway remains a hard count; the opposite estimate
is a soft prior/ceiling and is never presented as a measurement.

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

```bash
pip install -r requirements.txt

# Full pipeline (auto-discovers data_in/, falls back to the original delivery)
make all

# Web app + scenario API → http://localhost:8000
make serve
```

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
for a closure (`traffic_sim/simulation/metrics.py`) is scored primarily by Δ total
`timeLoss` against a same-demand baseline, with teleports and stranded
vehicles as hard disqualifying guards — GEH (sensor-fit) is deliberately
**not** used here, since it's blind to waiting time.

Every SUMO network build also writes `sumo/network_audit.json`, a provenance
sidecar showing imported versus defaulted speed/lane values, movement and
restriction tags, roundabouts, and the TLS membership produced by netconvert.
Golden artifacts can be staged and activated through `traffic_sim/ops/releases.py`.
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
not the 80/20 an earlier, unvalidated Gaussian AM/PM heuristic
(`estimate_directions.py`, retained only as historical/diagnostic code)
assumed. Predictions are James-Stein-shrunk toward 50/50 (only
~26% of the raw predicted deviation is treated as transferable signal,
calibrated against pooled leave-city-out error) and reported as q10/q50/q90
stress surfaces. Normal demand uses q50 in all three compatibility seed slots;
only an explicit `--direction-stress-variants` build substitutes q10/q90.
Those arms are uncalibrated stress cases, not probability intervals or release
evidence.

**Audit of the figures above, 2026-08-16.** The shrinkage coefficient λ in
`train_report.json` is fitted on the pooled held-out pairs and then scored on
those same pairs, so *that report's* margin over 50/50 is an upper bound rather
than a generalisation result. Refitting λ on three cities and scoring it on the
fourth (`make dirsplit-validate`) gives pooled MAE 0.0568 against 50/50's
0.0565, bootstrap 95% CI [−0.0030, +0.0041]. This applies to the **superseded**
v1 table and profile model on leave-city-out only — it is **not** a Gate M
result. Gate M's current authority is
`validation/dirsplit_gate_m_outcome_v5.json` = `MODEL`, measured by
`python3 -m dirsplit.evaluate` on the 247,464-row v2 table across three fold
kinds, and the leak is already fixed there. The genuinely new number is
interval coverage: the deployed `[q10, q90]` covers **39.3%** of held-out
observations against a nominal 80%, quantifying the q arms' declared
"uncalibrated stress case" status. Evidence:
`validation/dirsplit_train_report_leakage_diagnostic_v1.json`.

```bash
make dirsplit-stations   # station metadata (open API, no key)
make dirsplit-volumes    # hourly volumes by direction (resumable)
make dirsplit-match      # OSM matching + heading→bearing resolution
make dirsplit-dataset    # assemble the shared training table
make dirsplit-train      # per-sensor locally-weighted LightGBM quantile models
make dirsplit-predict    # → sumo/direction_split.json (edge_shares_q10/q50/q90)
make dirsplit-coverage   # are our sensor edges inside the training cloud?
make dirsplit-validate   # nested-λ, interval coverage, orientation → validation/
```

`make demand` automatically prefers the trained model over the Gaussian
fallback whenever `data/dirsplit/model.pkl` exists.

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

Shared implementation lives in one package; the repository root holds only
entry points that are invoked as commands.

```
traffic_sim/            canonical implementation package
  core/                 shared contracts and content fingerprints
  intake/               sensor registry and data-intake helpers
  demand/               PFE solver/kernel and candidate-artifact cache
  confidence/           held-out validation and validation reporting
  simulation/           SUMO runtime, network metadata/audits, disruption metrics
  ops/                  run and release registries

pipeline entry points
  build_data.py         intake CLI -> network.geojson, flows.json, graph.graphml
  build_features.py     flow matrix, adjacency, normal profile and splits
  build_dataset.py      windowed datasets for a future GNN
  train_agent1.py       LightGBM baseline + holiday factors
  build_agent1_flows.py 2027 forecast -> flows_forecast.json
  build_sumo_net.py     graph.graphml -> SUMO network (stable edge IDs)
  build_candidates.py   DeSO/OSM/RVU-grounded candidate routes
  build_sumo_demand.py  demand orchestration and calibrated SUMO routes
  run_scenario.py       baseline/closure Monte Carlo runs
  serve.py              static web + optional local API
  observability.py,     demand-estimation stages
  assignment_priors.py,
  prior_flows.py
  calibrate_theta.py,   candidate-pool and direction-split calibration
  estimate_directions.py
  fetch_deso.py         SCB DeSO population fetch
  warm_demand_horizon.py  pre-warm the demand day library
  sensor_contribution.py  sensor contribution / placement screen
  validate_sim.py       CLI for traffic_sim/confidence/loso.py
  validation_report.py  CLI for traffic_sim/confidence/report.py
  explore.py            one-off data exploration and plots

campaign runners        evidence-bound paths — recorded inside validation/
  run_monthly_closure_search.py, run_monthly_proxy_validation.py,
  run_monthly_warm_state_validation.py, screen_monthly_closures.py,
  suggest_closure_time.py

signals/                signal-timing lab, optimizer and closure combination
dirsplit/               trained direction-split model package
demand/                 demand model components and data contracts
web/                    static Leaflet browser runtime
web/data/               generated artifacts and exact graph snapshot
data_in/                user-delivered sensor/DeSO/POI inputs
validation/             frozen campaign evidence (see validation/README.md)
sumo/, runs/, cache/    generated intermediates, manifests and caches
tools/                  bounded experiments, probes, freezes and benchmarks
tests/                  contract + pipeline tests
docs/                   dated plans, reviews and history (see docs/README.md)
ARCHITECTURE.md         structural source of truth
IMPROVEMENT_PLAN.md     canonical improvement and delivery plan
```

The twelve former root shims are gone: every import names its real module.
Two root files, `validate_sim.py` and `validation_report.py`, still rebind
`sys.modules` to their implementation — deliberately, because
`make validate-temporal` runs them as commands *and* three production modules
import them and use the implementation's attributes. Retiring those two means
editing a sealed demand source, so it is a separate change.

The campaign runners stay in the root because their **paths** are recorded
inside frozen `validation/` artifacts and `tools/freeze_*.py`; moving one
would break evidence that cannot be regenerated. `signals/` carries no such
binding, which is why it could move.

Run every command from the repository root so relative artifact paths and
Makefile contracts remain deterministic. The `signals/` modules work both as
`python3 -m signals.signal_optimize` and `python3 signals/signal_optimize.py`.
