# Traffic animation & incident simulation — Gothenburg

Persistent historical project context for any model or contributor. Keep this
file in the repo root. Collaboration and workflow behavior are defined by
`AGENTS.md`; this file does not assign Claude, Codex or another model a fixed
role.

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
- The product promise — REWRITTEN 2026-08-05 (supersedes "we SIMULATE
  everywhere but CLAIM only what the per-edge confidence supports"). The
  BASELINE RULE is now: **only what is measured is simulated.** Every
  calibrated vehicle crosses at least one measured edge; no synthetic
  background traffic exists anywhere in the pipeline. Every sensor the city
  adds still raises accuracy wherever it is placed, and it now also
  ENLARGES the simulated network, because coverage follows sensor-crossing
  paths.
  CONSEQUENCE, stated plainly: roughly half the inner city carries ZERO
  baseline flow (measured 2027-05-12: 3 339 of 7 125 edges, 46.9%, have no
  sensor-anchored candidate at all). Those edges are not "low confidence" —
  they are empty, so closing one is a structural no-op with nothing to
  divert, and any before/after comparison there is degenerate. This is the
  accepted price of not inventing traffic.
  The rule is a BASELINE rule only. In a closure scenario the same vehicles
  keep their destinations and reroute freely to the fastest remaining path,
  which may take them off every sensor — that is the closure effect being
  measured, not a violation.
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
    source) takes ~6 min after the flat quarter-parallel PFE change
    (2026-07-09) and wipes old scenario files (they'd
    silently reflect the previous date's demand otherwise) — screenshot-
    verified end-to-end for 2027-09-14 forecast (100% GEH<5, vehicles
    moving, correct labels).
  - PFE PARALLELISATION (2026-07-09, same day as the E-I/I-E fix): the
    E-I/I-E fix's ~7x bigger candidate pool made the whole pipeline slow
    enough that a real user hit serve.py's recalibration timeout
    ("omkalibreringen tog för lång tid — avbruten") simulating a 2027
    forecast day. Root cause (measured): candidate generation itself was
    only ~81 s; the three direction-split variants (q50/q10/q90) were
    each an independent ~9 min `pfe.calibrate()` LP solve, run
    SEQUENTIALLY — ~27+ min just for that stage. Since each variant reads
    the same candidate pool read-only and writes its own separate output
    file (verified: no RNG, no shared mutable state in pfe.py), they're
    safe to parallelise — done via `multiprocessing.get_context("fork")
    .Pool`, one process per variant. Measured after: 15.6 min end-to-end
    for a 2025-09-16 historical full-day build (down from a projected
    ~30+ min sequential), 100% GEH<5 on all three variants, output file
    sizes identical to a pre-parallelisation sequential run (byte-count
    match, consistent with parallel execution order not changing the LP
    solution — same inputs, same problem, just concurrent). serve.py's
    timeout raised 1200s → 2400s to match. Implemented by Codex
    (codex:codex-rescue, write mode) at Gustav's request ("lös detta
    problemet ... utan att ta bort prestanda") — verified independently
    by Claude afterward (diff review + own pytest run) before commit.
  - PFE FLAT QUARTER PARALLELISATION (2026-07-09, after dae472b):
    verified `solve_interval_entropy`, `solve_interval`, and
    `calibrate()` have no warm start, RNG, previous-quarter accumulator,
    shared solver object, or hidden module cache tying quarters together.
    The final q50/q10/q90 solve now flattens all 3×96 independent
    (variant, quarter) solves into one `fork` pool over all CPU cores,
    avoiding the illegal nested-pool trap from daemon variant workers.
    Route files are still written afterwards in deterministic
    variant/quarter/depart order, so SUMO's depart sorting and GEH
    aggregation stay unchanged. Measured full-day 2025-09-16 historical
    demand build: 336.69 s (5.61 min) end-to-end on this machine, using
    10 workers, with q50/q10/q90 all 100.0% GEH<5 and 0 infeasible
    intervals.
  - LOSO VALIDATION RUN (2026-07-09, `validate_sim.py`, fresh numbers
    superseding every prior LOSO figure in this file — all of them predate
    the E-I/I-E fix): before running it for real, audited the leakage-
    prevention methodology end to end (Codex + Claude, two review passes)
    and found ONE real leak: `assignment_priors.py`'s scale factor was fit
    against ALL sensors' measured flows, including whichever one a given
    fold was holding out. Measured how much this mattered by running LOSO
    three ways: (1) leaking, with assignment-prior: ratios 0.838/0.971/2.581
    (min/median/max); (2) assignment-prior disabled entirely: 0.308/1.102/
    4.130 — MUCH worse on 5/6 stations, confirming assignment-prior is
    doing real work (matches the earlier-documented recovery finding) and
    that simply disabling it would answer a different, less relevant
    question than "how does the deployed system generalize"; (3) FIXED —
    assignment_priors.py refactored so its expensive structural load
    computation (`compute_assignment_load`, ~40k-sample gravity/stochastic-
    multipath routing, independent of measured data) runs once, while the
    cheap scale-factor regression (`calibrate_assignment_priors`) is
    refit per fold with `exclude_sensor` removing every edge belonging to
    the held-out station — verified against real data that this filters
    exactly the right edges (sensor 107: 2 edges, the only two-way sensor;
    all others: 1 edge each) and that main()'s unexcluded path is byte-
    identical to the pre-refactor code (same scale, same R², same flow
    count). Final leak-free result: ratios 0.830/0.896/2.410 — close to
    the leaking version (confirms the leak was real but not the dominant
    driver of the earlier numbers), clearly closer to it than to the
    no-assignment-prior version. Per-station held-out GEH<5: 107 41.7%/
    29.2% (its two edges), 133 66.7%, 134 79.2%, 1074 75.0%, 1076 33.3%,
    2276 66.7%. Also reused the flat per-quarter PFE parallelization here
    (`calibrate_fold_parallel`) — full 6-station run in 895 s (~15 min),
    down from a projected ~60-75 min sequential. Written to
    `web/data/loso_report.json`.
    SUPERSEDED 2026-07-13: after the destination-bias fix (sensor-
    conditioned OD, structure-preservation caps, purpose×time priors —
    commits 51ad47f/6632bfc/62a1584; see the destination-integrity section
    of IMPROVEMENT_PLAN.md), the honest LOSO baseline is min 0.05 / median
    0.78 / max 1.95.
    The ratios above (0.830/0.896/2.410) were partly ARTIFACT-POWERED:
    36.5% of calibrated vehicles then terminated within 200 m of a sensor,
    and those near-sensor-ending routes inflated held-out recovery. The
    worst new fold (1076 at 0.05) was PROVEN artifact-powered (G1, closed
    2026-07-13; summarized in IMPROVEMENT_PLAN.md): a pre-fix rerun replicated
    its old ratio 1.516 exactly, and 99.6% of that crossing flow served no
    other sensor's band and evaporated within 500 m of the sensor. The fix
    lost no real corridor continuation; 0.05 measures 1076's informational
    isolation from the other stations. Do not quote the old ratios as
    current performance.
  - EMPIRICAL CONFIDENCE DECAY (2026-07-09, `build_data.py` consuming
    `web/data/loso_report.json`): the old guessed `CONF_SIGMA_M = 250 m`
    placeholder has been replaced by a LOSO-derived sigma when the report
    exists, with a documented fallback to 250 m for fresh installs before
    validation has been run. Method: for each held-out station, compute the
    physical midpoint of its measured edge(s) in `network.geojson`, measure
    distance to the nearest REMAINING station midpoint, then combine that
    distance with the held-out edge's GEH<5 percentage from LOSO. Each point
    implies one sigma via `accuracy = exp(-d² / 2σ²)` (accuracy clipped to
    [0.02, 0.98]); deployed value is the robust median across the 7 measured
    held-out edges. Current recomputation on the checked-in network gives
    implied sigmas 170.9, 144.0, 298.0, 165.3, 68.0, 89.6, 106.7 m; median
    = **144.0 m**. This is tighter/more conservative than the old 250 m
    guess. HONEST LIMITATION: every LOSO point is near-field inside the two
    dense sensor clusters (nearest-remaining-sensor distances 61-245 m in
    the current geometry). Inner-city edges kilometres from a sensor are
    still extrapolation; they are now anchored in real held-out validation,
    not magically validated citywide.
    CAVEAT FOUND DEPLOYING THIS (2026-07-09): `build_data.py`'s `main()`
    always calls `ox.graph_from_bbox()` fresh (osmnx-cached, not a frozen
    snapshot) — running it to pick up the new σ therefore also pulls
    whatever OSM has changed since the committed `network.geojson` (128
    street-name and 15 `highway`-classification edges drifted in one such
    run, e.g. real renames/reclassifications, not corruption — edge IDs and
    geometry were unaffected). That's out of scope for a confidence-only
    fix, so the deployed σ=144.0 m was applied by recomputing `confidence`
    directly on the already-committed `network.geojson` (same `dist_sensor_m`
    values, just the new σ), not by committing a full pipeline rerun. A
    real OSM refresh is a legitimate thing to want eventually, but should
    be its own deliberate, reviewed step — not a side effect of a
    confidence-formula change.
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
- `level` / measured directions — VERIFIED 2026-07-03 by Gustav against Göteborgs Stad's trafikmängder catalogue (Power BI). THE DELIVERED "Total" LABEL WAS WRONG for 4 of 5 sensors — it is the catalogue's Total row, which for single-direction stations equals the one measured direction. Source of truth = the validated `data_in/sensors.json` registry (the `SENSOR_MEASURED_DIRECTION` name in `build_data.py` is compatibility-only):
  - 107 Skånegatan: genuinely two-way (N+S+Total). City's own D-factor: N 3400/S 3100 of 6500 (2025) = 52/48; 2023–24 exactly 50/50 — LOCAL VALIDATION of the mild-split finding and the dirsplit model (predicted 0.47–0.52).
    MACHINE-BOUND 2026-08-16 (`directional_reference` in `data_in/sensors.json`,
    plan Fas 0A): the raw 3400/3100, their edge mapping (N = 60786979_
    3575001205_0 at bearing 352°, S = 1455801464_18241874_0 at 174°), the
    declared period and the source are now a validated registry record with
    `time_semantics: period_aggregate`. It is a PERIOD AGGREGATE and the
    registry loader REFUSES any record claiming per-slot semantics — a yearly
    D-factor must never be serialised as 96 Level-1 measurements. See the
    direction-split anchoring contract below for what it does to the split.
  - 1074 Valhallagatan V, 1076 Skånegatan S, 133 Läraregatan V, 134 Gibraltargatan SO, 2276 Läraregatan V — single-direction only. Their daily means match the catalogue's ÅMVD ±3%.
  - build_data.py snaps compass-labelled sensors direction-aware (edge bearing must match the letter, else opposite carriageway within 80 m). Only 7 measured directed edges now (107's pair + 5 singles). The opposite direction at single-direction stations is UNMEASURED — never constrain or display it as known.
  - New sensors: check the city catalogue FIRST and add a verified record to `data_in/sensors.json`.
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
- `network.geojson`: LineString features = edges, Point features = nodes. Stable string IDs. WGS84 coords. Each measured edge carries `sensor_id`. Every edge carries `dist_sensor_m` and `confidence` = exp(-d²/2σ²), d = distance to nearest sensor. DEPLOYED 2026-08-05 at σ=119.5 m by recomputing `confidence` in place on the committed `network.geojson` from its existing `dist_sensor_m` values — NOT by rerunning `build_data.py`, whose `main()` pulls fresh OSM and would drift street names and highway classes as a side effect (the same method used for the 144.0 m deployment). Verified: `confidence` is the only property that changed, on 477 edges, with zero geometry changes. Note this is an annotation only — it adds no traffic anywhere. CAVEAT: under the baseline rule this smooth decay no longer describes the whole network — ~46.9% of edges now carry zero baseline flow, so their real epistemic status is "not simulated", not "low confidence". The formula is retained for the sensor-reachable subgraph; treat a nonzero `confidence` on an edge with no baseline traffic as meaningless until the map distinguishes covered from uncovered. σ is now fitted from the leakage-free LOSO report when `web/data/loso_report.json` exists (current value **119.5 m**, robust median of 7 held-out measured-edge points; was 144.0 m before the sensor-crossing baseline rule changed the demand and LOSO was re-run); `build_data.py` falls back to the old guessed 250 m only when that validation artifact or the previous network file is missing. NOTE: this static confidence is empirically anchored only in the near field (<~300 m in the current two-cluster sensor layout). Citywide/inner-city distances beyond that remain labelled extrapolation. ScenarioProvider can further reduce confidence per edge/scenario using Monte Carlo spread; the renderer just displays whatever confidence number it gets.
- `flows.json` / `flows_forecast.json`: `{epoch, interval_minutes, flows}` where `flows[edgeId][quarterIndex] = count`, `null` where missing. Same edge IDs as the GeoJSON. Epoch strings have no timezone suffix — the web app parses them as UTC (provider.js appends 'Z'); keep parse and getUTC* formatting consistent.
- `candidates.meta.json` (`schema_version: 2`): one record per candidate leg, carrying `tour_id`, `leg`, purpose, endpoints and location pools; this provenance flows into `calibrated.agents.json`. ADDED 2026-08-06: `tour_partner_dropped: true` on a leg whose paired partner was removed by the route filters. It is NOT rare — 13.9% of the pool, reaching ~24% of published vehicles — because `validate_routed_candidates`, `drop_uturn_routes` and `drop_excessive_detours` delete individual legs and none of them knows a tour has two. Never read `tour_id`+`leg` as proof of a complete tour without checking this flag. The bias is directional (return legs are filtered ~1.8× more often than inbound ones), so it is reported per leg on every build; `--atomic-tours` drops half tours instead, at the cost of 13.9% of the pool and a breach of the 75% supply floor.
- PFE RELAXATION LADDER ORDER (`pfe.py`) is a contract, not an implementation
  detail: **the measured counts outrank every other constraint, so EVERY
  non-measurement layer is dropped before the measurement band is widened by
  one unit.** The order is `clean` → `no_bounds_tol1` (Level-2 bounds and
  structural caps off) → `no_purpose_quota_tol1` (purpose quotas off too) →
  `no_priors_tol1` (Level-3 priors off too) → `lp_fallback` (the complete LP,
  still at the declared band) → only then `relax_tol2x`/`relax_tol4x`/
  `relax_no_bounds`. Three separate inversions of this rule have been found
  and fixed (2026-08-06): bounds, then purpose quotas, then priors — each one
  a constraint that stayed active at every rung, so when it conflicted with a
  measurement the MEASUREMENT gave way. When adding any new constraint to the
  solver, the question to answer first is: at which rung does it yield?
  Two facts make violations of this contract invisible unless you look:
  - **GEH<5 cannot police it.** The ×4 band peaks at GEH 3.81 for a 400
    veh/quarter target and no measured edge has ever exceeded 203 in a
    quarter, so a build can report 100% GEH<5 with 22.6% of intervals
    anywhere inside a 20% band.
  - **`tol_mult` never enters the IPF iteration** — it is read only by
    `_check_entropy_solution`, so a widening rung returns a BIT-IDENTICAL
    vector to its unwidened counterpart and merely judges it by a looser
    ruler. A widened band is therefore never evidence that the route pool
    could not serve the counts; that question is decided by the LP, which is
    why the LP now sits above the widening rungs. Pinned by
    `test_widening_the_band_does_not_change_what_ipf_computes`.
  Read `relaxation_summary` in `demand_meta.json`: any `relax_tol2x`/
  `relax_tol4x`/`relax_no_bounds` count means a widened band was used, and
  `warn_widened_measurement_band` now reports that unconditionally on every
  build (it used to be nested inside the bound-violation warning, which
  returns early when there are no bound violations).
- DIRECTION-SPLIT ANCHORING (2026-08-16, `traffic_sim/intake/direction_anchor.py`,
  applied by `demand/intake.py::load_anchored_direction_split`): where the
  validated registry holds a verified `directional_reference`, the ESTIMATED
  per-slot split is re-levelled by ONE constant shift in log-odds until its
  flow-weighted mean over the declared period reproduces the published share.
  Properties that make this safe, each pinned by a test: the shape of the
  time-of-day profile is untouched (a constant shift preserves every log-odds
  difference and the ordering); the complement direction moves by −δ, which is
  exactly complement-consistent, so pair sums and mirrored-quantile relations
  survive; q10/q90 take the SAME δ as q50, so the stress band keeps its width
  rather than collapsing onto the anchor; per-slot values remain labelled
  `estimated`, and only their period mean is measurement-backed. Weights are the
  measured two-way totals per slot from the REFERENCE year (`web/data/flows.json`)
  no matter which source a build calibrates against — a D-factor describes the
  real street, not a forecast — with a declared `uniform_fallback_*` weighting
  when the period has no measurements. Anchoring is applied at LOAD time, not in
  `dirsplit/predict.py`, so the split file's bytes cannot hide a change in this
  code; the module is therefore part of the demand source fingerprint and the
  candidate-cache key. Measured on deployment: 107's transfer-model period mean
  was 0.4981, the anchor moves it to 0.52308 (δ = +0.100), max per-slot change
  0.025, and one real Level-1 target (2025-09-16 08:00, two-way total 127) moves
  from 63.0 to 66.2 vehicles.
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
   - `build_sumo_demand.py`: 15-min sensor counts (directional via the estimated split) → randomTrips candidate pool → routeSampler calibration. Fit: 100% GEH<5 at all 11 edges, all intervals. Also exports the implied OD matrix (calibrated trips aggregated to zones: two cluster areas + 8 compass entry sectors) → web/data/od_matrix.json/.csv — ONE plausible OD consistent with the counts; the true OD is not identifiable from 6 counters. Only sensor-crossing traffic is calibrated. As of 2026-08-05 this is ENFORCED, not merely typical: the edge-coverage support set and the calibrated edge-support augmentation are both removed, so streets that no sensor-crossing path reaches carry exactly zero baseline traffic rather than a little synthetic traffic. See the baseline rule at the top of this file.
   - `run_scenario.py [--close edgeId]`: Monte Carlo (3 seeds), per-edge 15-min flows + confidence = spatial_prior × exp(-CV) → web/data/scenarios/*.json + index.json manifest. Uses --ignore-route-errors (vehicles destined for the closed edge are dropped).
   - Web: "Scenario" toggle + dropdown; scenario colours ALL simulated edges (dots stay on sensor edges for perf); closed edges drawn black-dashed; URL params ?mode=scenario&file=&qi=.
   - MESOSCOPIC BY DEFAULT (2026-07-03): run_scenario runs sumo --mesosim (queue-based). Rationale: our product is 15-min edge flows, which doesn't need microscopic car-following. Measured: whole-day 3-seed closure 35 s meso vs 25 min micro (43×), AND delivery is BETTER (simulated/measured at sensors 0.87–0.96, total 0.91, vs micro 0.83–0.94). --micro flag kept for comparison runs. Closure rerouter is attached only to edges within 400 m of the closure (REROUTER_RADIUS_M) — global rerouter cost was the other bottleneck.
   - WHOLE-DAY WINDOWS: make demand builds 00:00–24:00 (build_sumo_demand --end 24:00 special-case; candidate pool capped at ~10k trips); make demand-morning keeps the fast 06–10 window for iteration. Scenario mode scrubs all 96 quarters.
   - INTERACTIVE CLOSURES (2026-07-03): `make serve` runs serve.py — static web/ + /api/close?edges=a,b,c which shells out to run_scenario.py (one sim at a time, lock + 409). Whole-day interactive closure ≈ 40 s thanks to meso. Web: 🚧 Stäng väg mode (feature-detected via /api/ping, hidden on static hosting) — click edges to select (red-dashed pending style), Simulera runs the MC and auto-loads the scenario. run_scenario supports MULTIPLE --close edges (scenario JSON: closed_edges list); shared closure rerouter file per scenario. Scenario fetches use cache-busting (re-runs overwrite files in place).
   - DATA INTAKE: data_in/ drop folder (build_data auto-discovers; falls back to the original Downloads delivery). New-sensor workflow in data_in/README.md; `make refresh` re-runs the whole chain raw→scenarios.
   - Simulation uses the FULL graphml graph (~2 250 edges) so rerouting has real alternatives; the web app displays the subset in network.geojson.
   - E-I/I-E FIX + REBUILD (2026-07-09, commit d0820ae): found and fixed the bug
     described above under "Make every simulated vehicle sensor-anchored" — E-I/I-E
     tours' return leg was reusing the outbound gate edge, which is structurally
     unreachable from the other direction, so the whole category silently produced
     0 trips. Fixed with a fresh, independently-drawn gate per return leg
     (`natural_origin_weights`, mirroring `natural_far_end_weights` for a fixed
     destination instead of a fixed origin). Rebuilt demand + scenarios end to end
     (`make demand && make scenario`, 2025-09-16 whole-day/historical, same window
     already deployed): dropped tour attempts fell from 1000/1000 (isolated E-I/I-E
     test, pre-fix) to 1/3000; GEH<5 stayed 100% on all three demand variants;
     `clear_stale_scenarios()` now leaves a valid empty `index.json` manifest
     instead of deleting it outright (a CLI-only `make demand` run has no
     guarantee `run_scenario.py` runs right after, unlike serve.py's recalibration
     path). Verified with Codex (codex:codex-rescue) at each stage — code review
     before push, plan alignment before rebuild, and a numbers sanity-check after.
   - CLOSURE-LEAK FIX (2026-07-09, same day, `truncate_stranded_vehicles` in
     run_scenario.py): root-caused and fixed the 39-vehicle leak found during the
     rebuild above (NOT caused by the E-I/I-E fix — the closure/rerouter code was
     untouched at the time; the new OD mix just exposed a pre-existing gap where
     the old one happened not to). Root cause, confirmed three independent ways:
     (1) the live sumo run's own warnings (only visible with `--no-warnings`
     removed) showed "No route for vehicle found" then "Teleporting vehicle;
     waited too long" — sumo's stuck-vehicle cleanup was forcibly relocating it
     PAST the closure at end-of-run, which then reads as if it drove the closed
     edge; (2) duarouter, given the exact same closure additional file and even
     replanning the trip from scratch, still routed through the closed edge —
     `<rerouter>`/`closingReroute` is a RUNTIME sumo concept the offline router
     doesn't evaluate at all; (3) a plain Dijkstra over net.net.xml's
     `<connection>` graph with the closed edges removed found no path either.
     Deeper cause: node 3575001205 (Skånegatan/Engelbrektsgatan) has exactly ONE
     incoming connection in the whole network — the edge being closed — so
     whatever's downstream is structurally cut off once it closes; not verified
     against a real map, could be a genuine one-way street or an OSM-import gap.
     Scale-checked (Gustav asked directly): only 63 of 7125 edges (0.9%) become
     unreachable once this specific closure applies — a small contained pocket,
     not the network being fragile to any random closure.
     THREE ROUNDS to get the fix right, each from real feedback:
     (i) first cut just DELETED stranded vehicles — Gustav, correctly: a driver
     whose literal destination is now unreachable by car still drives most of the
     trip and parks short of it, walking the rest; deleting the whole vehicle
     erased its real contribution to every OTHER edge on the route, not just the
     closed one. Fixed to TRUNCATE the route at the last edge before the closure
     instead (only actually dropped if the closed edge is the vehicle's very
     FIRST edge — no partial trip possible at all);
     (ii) Codex's review then caught that the reachability check used the
     route's ORIGIN as a proxy for "will the live rerouter save this vehicle" —
     wrong two ways: two vehicles sharing an origin/destination can be on
     different candidate routes, one already committed to a dead branch the
     other avoided; and with multiple `--close` edges, truncating at the FIRST
     one hit ignores whether a LATER one on the same route is what actually
     kills the detour. Both fixed the same way: check reachability from the
     edge immediately BEFORE the first closed edge in THAT vehicle's own route
     (matching exactly where sumo's live rerouter itself re-plans from), not
     from a shared origin — `reachable()` already removes every closed edge at
     once, so this correctly accounts for later closures too, not just the
     first hit;
     (iii) non-blocking cleanup: `build_edge_graph` now built ONCE per closure
     (in main()) and passed in, instead of re-parsing net.net.xml per demand
     variant file.
     Verified: 0/16467 leaking vehicles post-fix (was 39/16467), vehicle count
     unchanged (nothing deleted unnecessarily), reproduced clean on a second,
     unrelated closure (Läraregatan) tested live via `/api/close`. 11 tests
     (`TestTruncateStrandedVehicles`), including two regression tests that
     directly reproduce Codex's two review findings and confirm the fix.
     Two independent Codex review passes (codex:codex-rescue), both fresh
     threads: first found the two origin-vs-position bugs above, second
     confirmed both resolved with no new blocking issues.
   REMAINING: see IMPROVEMENT_PLAN.md — the canonical development plan for
   multi-day simulation, closure timing, signal optimization, sensors and
   evidence gates. Vehroute-based individual trajectories already play in the
   web app; only selective FCD for micro/signal windows remains deferred. The
   LOSO-derived confidence distance-decay is DONE as of 2026-07-09
   (`build_data.py` consumes `web/data/loso_report.json` and writes the fitted
   144.0 m sigma into `network.geojson`, with the near-field extrapolation
   caveat documented above).
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
   - DECISION-GATED REWORK (2026-08-16, plan
     `docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md`): an
     end-to-end audit found that the deployed q10/q50/q90 are learned from
     station-hour MEANS, so they describe spread between aggregated cells, not
     the day-to-day variation a future day has; that weekend/off-hour
     predictions have no training support (`is_weekend` is always 0 in
     training); and that q10/q90's nominal 80% coverage has never been
     validated. The unconditional response is built:
     - `dirsplit/dataset.py` now writes a RAW `training_table_v2.csv` (one row
       per station × local date × hour × heading, with both counts, the hour
       total, coverage, an explicit missingness reason instead of a silent
       drop, and a `day_block_id` that keeps simultaneous observations
       together). The old aggregate is kept as a labelled diagnostic. Profile
       features are split into `profile_total_*` (both directions, the legacy
       meaning) and `profile_dir_*` (this heading only, computable at a
       single-direction station) — never the same column name for two
       different quantities.
     - `dirsplit/benchmark.py` runs a four-model tournament (`constant_5050`,
       `shrunk_dfactor`, `lightgbm_similarity`, the deployed
       `lightgbm_similarity_shrunk`, and a beta-binomial count model when raw
       counts exist) over leave-city-out, leave-station-out and blocked-date
       folds, refitting scaling/bandwidth/shrinkage INSIDE each fold and
       bootstrapping over independent day blocks. Gate M's rule is frozen in
       source. MEASURED on the tracked aggregate (`--table legacy --hours
       supported`, 1 214 rows, 81 groups): `shrunk_dfactor` — hour × day type
       partially pooled toward 0.5, with NO street features — beats 50/50 by
       +4.5% leave-city-out (CI [−0.0053, −0.0006]) and +6.4% leave-station-out;
       the deployed shrunk LightGBM manages +2.1%/+3.7% with CIs spanning zero;
       the raw LightGBM is WORSE than 50/50 (−6.5%/−3.5%). Gate M nevertheless
       reports INCONCLUSIVE by its own rule, because the aggregate has no day
       blocks and no raw counts. Do not quote these as a decided verdict.
     - `dirsplit/coverage.py` gained observability v2: an evidence profile with
       seven separate dimensions (measurement level, static domain, temporal
       support, feature compatibility, effective sample size, calibration
       support, local cross-check). Weak evidence widens the claim and can make
       a result `inconclusive`; it NEVER excludes a road — `excludes_road` is
       False by construction and tested.
     - `tools/measure_direction_decision_sensitivity.py` + its committed
       preregistration measure Gate S: the full stress-case × seed cross product
       with the SAME seed list under every case and the SAME (case, seed) pair
       for baseline and candidate, reusing the existing `run_condition`/
       `paired_comparison` runners. It is diagnostic (`release_evidence: false`),
       append-only, and fails closed to INCONCLUSIVE without a demand build.
     GATES STILL OPEN: Gate S needs `make demand && make direction-sensitivity`;
     Gate M needs `make dirsplit-volumes && make dirsplit-dataset && make
     dirsplit-benchmark`. Nothing in Fas 2–4 (residual scenarios, ensemble
     manifests, monthly/warm/API/UI integration) may be built until they pass.
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
- (Closed 2026-07-20: NO MORE EXTERNAL DATA. Decided by Gustav — the
  project ships permanently on the delivered 2025 six-sensor counts. The
  four data requests in IMPROVEMENT_PLAN.md ("External Data Requests")
  will NOT be sent. Consequence: signal results are `synthetic`
  permanently (never `city-configured`); through-traffic share stays a
  sensitivity-tested prior; purpose labels state a behavioural class, not
  verified intent; no local speed/travel-time calibration. These are
  FIXED honest boundaries, not pending unlocks — do not re-open them as
  TODOs or recommend sending a data request. `docs/plans/DATA_REQUEST_2026-07.md` is
  a not-sent record only.)

## Files
- Pipeline (run in order): `build_data.py` → `build_features.py` → [`build_dataset.py` for future GNN] → `train_agent1.py` → `build_agent1_flows.py`. Or just `make all` (Makefile has the raw-data paths; `make serve` starts the web app).
- SUMO (Phase 3): `make sumo-net` → `make demand` → `make scenario` (or `python3 run_scenario.py --close <edgeId>`). Requires `pip install eclipse-sumo`. Intermediates in `sumo/` (gitignored); web products in `web/data/scenarios/` (tracked).
- `web/data/graph.graphml` — exact OSM graph snapshot (same node/edge IDs as network.geojson). Phase 3 SUMO/demand work MUST start from this graph, never a fresh OSM download.
- `explore.py` — one-off data exploration/plots. `tests/` — contract + pipeline tests (`python3 -m pytest tests/`).
- Generated in `web/data/`: network.geojson, flows.json, flows_forecast.json, normal_profile.json, features/, agent1/.
- Web app: `web/` (index.html, provider.js, state.js, render.js, controls.js, clock.js). Serve with `cd web && python3 -m http.server 8000`.
- (The old `archive/web_update_2026-06-27/` experiment was deleted 2026-07-02 — superseded by the real scenario system.)
