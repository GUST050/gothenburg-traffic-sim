# Architecture overview

This is the short orientation map for humans and coding agents. The complete
technical source of truth remains `ARCHITECTURE.md`; follow links there only
for the subsystem being changed.

## Product boundary

The program turns six Gothenburg traffic sensors into historical animation,
2027 forecasts, calibrated sensor-crossing demand, SUMO road-closure studies
and confidence/evidence reports. The baseline deliberately simulates only
traffic supported by measured sensor crossings. A closure may reroute those
same vehicles away from sensors.

## Data flow

```text
city sensor files + sensor registry
  -> intake and network matching
  -> historical flows and graph snapshot
  -> forecast and direction allocation
  -> candidate routes and demand calibration (PFE)
  -> SUMO baseline/closure/signal runs
  -> confidence, gates and release evidence
  -> static web providers + local API
```

## Dependency map

```text
traffic_sim/core
  <- intake
  <- demand
  <- confidence
  <- simulation
  <- ops

dirsplit --------> demand intake/build
signals ---------> scenario/simulation runtime
root CLIs --------> canonical packages
serve.py --------> specs, simulation runners and curated API presentation
web/ ------------> generated provider artifacts (never Python internals)
```

Package code must not import root command modules. Root command paths stay
stable because some are referenced by documentation, tests and frozen evidence.
Reduce large entry points by extracting one cohesive, tested responsibility
into a canonical package at a time.

## Major areas

| Area | Responsibility | Primary checks |
| --- | --- | --- |
| `traffic_sim/core/` | contracts, fingerprints, calendars | `make test-fast` |
| `traffic_sim/intake/` | sensor registry and intake helpers | `make test-fast` |
| `traffic_sim/demand/` | PFE, route support, cache and provenance | `make test-demand` |
| `demand/` | intake/calibration/publication orchestration | `make test-demand` |
| `dirsplit/` | reusable directional allocation and validation | `make test-dirsplit` |
| `traffic_sim/simulation/` | SUMO, closures, monthly search, warm state | `make test-simulation` |
| `signals/` | signal-plan experiments | `make test-simulation` |
| `traffic_sim/confidence/` | LOSO and confidence reporting | `make test-demand` |
| `serve.py` | HTTP routing and job orchestration | `make test-web` |
| `web/` | static Leaflet application and providers | `make test-web` + browser smoke |
| `validation/` | frozen/versioned evidence | relevant gate tests |

## Evidence hierarchy

1. Measured sensor counts are hard constraints.
2. Conservation and justified mathematical bounds may determine additional
   values.
3. Held-out-validated priors may guide unobserved structure.
4. PFE reconciles route flows without relabeling estimates as measurements.

Missing is never silently converted to zero. A diagnostic or proxy result is
not release evidence unless its contract explicitly permits promotion.

## Stable command paths

Pipeline commands such as `build_data.py`, `build_candidates.py`,
`build_sumo_demand.py`, `run_scenario.py` and `serve.py` are public repository
entry points. Campaign-runner paths are also embedded in frozen evidence.
Preserve these paths and their arguments; move implementation behind them only
with compatibility and provenance tests.

## Generated artifacts

- `web/data/` contains shipped provider artifacts and the exact graph snapshot.
- `sumo/`, `runs/` and `cache/` contain generated local intermediates and are
  ignored unless a specific release contract says otherwise.
- `validation/` contains deliberate frozen evidence, not a general output
  directory.
- Large tracked legacy artifacts are pinned by
  `tools/repo_hygiene_allowlist.json`; new large files fail `make repo-hygiene`.

See `docs/ai/ARTIFACT_POLICY.md` before adding data, models or run output.

## Change workflow

1. Identify the owning domain and read its nearest `AGENTS.md`.
2. Bind the input/output contract and the narrow tests that protect it.
3. Change one responsibility without moving stable command/evidence paths.
4. Run the domain target, `make check`, and then the full suite when the change
   crosses domains or scientific/release contracts.
5. Update this map only when ownership or dependency direction changes; update
   `ARCHITECTURE.md` for the complete contract.

## Refactoring priorities

Files over 1,000 lines are review triggers, not automatic failures. Candidate
seams, in priority order, are:

1. candidate validation, route cleanup and trip generation out of
   `build_candidates.py`;
2. pure API presentation out of `serve.py`;
3. version the frozen source bindings before extracting scenario contracts,
   SUMO execution or output publication from `run_scenario.py`;
4. provider/controller/render responsibilities out of `web/app.js`;
5. solver orchestration versus numerical kernels in PFE.

Each extraction must preserve public imports while active tests depend on them
and must make the dependency direction simpler, not merely create forwarding
files. The large root files are SHA-256-bound by historical evidence, so they
cannot change without an explicit successor-evidence lifecycle. A trial
extraction on 2026-08-16 was reverted when the full suite correctly detected
this drift.
