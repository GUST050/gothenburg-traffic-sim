# PLAN — Multi-day simulation, closure-timing suggestions, signal optimization

Detailed execution plan, written 2026-07-09 so that any AI (or human) can pick
up each step independently and finish the whole arc. **Read CLAUDE.md and
ARCHITECTURE.md first** — they are the project's context and structural source
of truth; this file only plans FORWARD work and repeats existing facts solely
where a step depends on them. When this file disagrees with ARCHITECTURE.md
about existing structure, ARCHITECTURE.md wins. When a step here is completed,
mark it `DONE (date, commit)` in place and record surprises under the step —
this file is also the execution log.

Requested by Gustav (project owner) 2026-07-09:
1. Simulate MULTIPLE DAYS in one scenario (e.g. a whole week), not just one day.
2. The computer should SUGGEST the best time window to close a given road
   ("I need to close Skånegatan for 6 hours — when does it disturb traffic
   the least?").
3. Simulate how TRAFFIC LIGHTS should behave for traffic to flow best,
   especially in combination with closures.
Plus the standing product promise: honesty — every number shown must carry its
real confidence, and "optimized" claims must state their baseline.

---

## 0. Ground rules for whoever executes this

- **Measure first, build second.** Phases begin with small prototypes that
  produce NUMBERS. Do not skip them; several past bugs in this repo were only
  found by measuring (see CLAUDE.md history). If a prototype invalidates a
  design choice below, update this plan rather than forcing the design.
- **Never break the contracts** in CLAUDE.md ("Contracts — fixed"): edge-ID
  space (`u_v_k`, identical across geojson/SUMO/graphml), `flowAt(edgeId, t)`
  seam, flows-JSON shape, WGS84, `null` = missing (never 0).
- **Run `python3 -m pytest tests/ -q` after every step** (417 passed,
  20 skipped as of 2026-07-09) and add regression tests for each new function,
  in the existing style: every test class docstring explains WHAT REAL BUG or
  requirement it guards.
- **Do not regenerate `web/data/network.geojson` casually**: `build_data.py`
  re-fetches live OSM on every run (NOT the frozen `graph.graphml`), so a
  rebuild silently pulls in unrelated OSM edits (measured 2026-07-09: 128
  street-name + 15 highway-class changes in one such run). Confidence-only
  changes should be applied surgically to the committed file (see commit
  094d440 for the pattern). A deliberate OSM refresh is its own future task:
  it must regenerate graphml + geojson + SUMO net TOGETHER and re-verify
  sensor snapping.
- **Parallelization traps** (both hit before, both documented in CLAUDE.md):
  multiprocessing pool workers are daemonic and cannot spawn child pools —
  keep exactly ONE pool level (flatten, as `run_pfe_variants_flat_parallel`
  in build_sumo_demand.py does). `fork` start method is used deliberately
  (macOS caveat noted in CLAUDE.md; watch for crashes in native libs).
- **Language/UI**: user-facing strings in the web app are Swedish. Light
  basemap, validated color ramp — do not change (CLAUDE.md Tech choices).
- Codex (codex:codex-rescue, gpt-5.6-terra) is available and has been used
  throughout for review/implementation passes; the pattern that worked:
  Claude frames + verifies, Codex implements/researches, Claude
  independently re-verifies before commit. Keep commits small per step.

## 0.1 Measured facts everything below relies on (as of 2026-07-09)

| Fact | Value | Where measured |
|---|---|---|
| Whole-day demand calibration (96 quarters × 3 variants, flat-parallel, 10 cores) | ~5.6 min (336.69 s) | commit 5aa0137 |
| Whole-day 3-seed meso closure scenario | ~40 s | CLAUDE.md / run_scenario runs |
| Whole-day micro run | ~25 min | CLAUDE.md (meso decision) |
| LOSO 6-fold validation | ~15 min (895 s) | commit be2bb8b |
| PFE quarters are independent (no cross-quarter state) | verified | commit 5aa0137 review |
| Candidate pool: spatial structure is date-independent; departure shape mixes the EXACT day's measured profile (build_sumo_demand.py `real_day_shape()` ~line 174, blended in build_candidates.py ~line 1419) | verified | 2026-07-09 |
| Candidate count scales with window: `n_total = max(6000, 12000 * duration_s/86400)` (build_sumo_demand.py ~line 834) — 7 days naïvely ⇒ 84 000 | verified | 2026-07-09 |
| TLS in net.net.xml: 65 `tlLogic`, ALL `type="static"` programID 0 offset 0, 90 s cycle — netconvert `--tls.guess` output, NOT real Gothenburg plans | verified | 2026-07-09 |
| Meso ignores signal programs; full junction control on guessed plans throttled delivery to 0.57 vs 0.92 measured; `--meso-junction-control.limited` ≡ off (0.822 both) at current demand; actuated TLS unsupported in meso | measured 2026-07-06 | run_scenario.py comment ~line 305 |
| Rerouter `<interval begin end>` supports time-windowed `closingReroute`; vehicles with no detour WAIT until interval end (SUMO docs) | doc-verified | Codex research 2026-07-09 |
| Web app handles arbitrary scenario length via `n_quarters` → `State.setMaxQI` | verified | provider.js:37, state.js:32, index.html:461 |
| flows.json = all of 2025 (35 040 quarters); flows_forecast.json = 2027 | existing | CLAUDE.md |

---

## Phase A — Hygiene (do first; small; everything later builds on these)

### A1. Fix the `--micro` / trajectory-export inconsistency  — size S
DONE (2026-07-10, commit 29cee91). `export_trajectories()` now takes
`micro: bool = False` and uses the same conditional meso-flag block as
`run_sumo()`; `main()` passes `args.micro` through. Regression test
`TestTrajectorySimulationMode` (tests/test_scenario.py) parametrized over
micro True/False, asserts `--mesosim` present iff not micro. Two independent
Codex passes (implement, then a fresh independent review) + Claude diff
review + full pytest (419 passed, 20 skipped) before commit. Second review
confirmed: default is backward-compatible with every existing call site, no
other trajectory-related code hardcodes meso (web only reads the filename;
serve.py runs default/non-micro), scenario metadata doesn't record which
mode was used (noted as non-blocking — no current reader needs it).

### A2. Remove the double XML parse in `parse_edgedata` — size S
DONE — already fixed in the tree before this pass (only one `ET.parse` call
found on inspection 2026-07-10); no change needed, verified by both Codex
and Claude independently.

### A3. Delete stale `sumo/*tls_verify*` artifacts — size S
DONE (2026-07-10). Files removed (gitignored dir, no commit needed):
`sumo/additional_tls_verify_1000.add.xml`, `edgedata_tls_verify_1000.xml`,
`vehroutes_tls_verify.xml`. Confirmed no code references them. Phase D step
D1 creates a proper, reproducible replacement.

### A4. Fix stale docs — size S
DONE — already correct before this pass: CLAUDE.md's REMAINING line already
pointed at this PLAN.md and already noted vehroute-based trajectories exist
(from the PLAN.md-authoring commit, fbf3e05). No further stale reference
found in CLAUDE.md/ARCHITECTURE.md on a fresh check 2026-07-10.

**Phase A complete.** Next: B0 and C1 (independent measurement gates) —
in progress.

---

## Phase B — Multi-day simulation

**Architecture decision (research-grounded, Codex 2026-07-09 with SUMO-doc
sources):** one CONTINUOUS SUMO run over the whole range (day 2 departs at
t=86 400 s, …). SUMO time is just seconds — no 24 h limit exists; a
continuous run preserves queues across midnight (required for closures
spanning nights); chained per-day runs artificially reset the network at
00:00. Night traffic here is low, so the difference MAY be small — that is
hypothesis B0 tests, not an assumption to hardcode.

### B0. Prototype: two continuous days — size S — GATE for B1-B3
No product code. Script (can live in `tools/` or a scratch dir) that:
1. Builds a 2-day demand by hand: run the existing single-day pipeline for
   two consecutive dates (e.g. 2025-09-16 + 17), then merge the two
   calibrated route files with day-2 departs shifted +86 400 s and vehicle
   IDs de-collided (prefix `d1_`), 192-interval edgeData.
2. Runs meso baseline over 48 h; also runs the two days separately.
3. Measures and RECORDS (in this file, under this step):
   - wall time, peak RSS, route/edgeData/scenario file sizes (extrapolate ×3.5 for a week);
   - vehicles still `running`/`waiting` at each midnight (summary output);
   - Δ between continuous 48 h and 2×24 h on: per-quarter flows near midnight
     (23:00–01:00), total timeLoss, teleports.
Acceptance: numbers recorded; decision confirmed or revised. If continuous ≈
chained AND resources are a problem, chaining becomes a legitimate fallback —
document whichever wins.

### B1. Date-range contract in demand metadata — size M
`build_sumo_demand.py`:
- New CLI: `--start-date YYYY-MM-DD --days N` (default 1). Keep `--date` as
  alias for `--start-date X --days 1` — backward compat is a hard
  requirement (serve.py, validate_sim.py, Makefile call the old form).
- Validate whole range inside the source year (historical=2025,
  forecast=2027); reject ranges crossing year boundary.
- `demand_meta.json` gains: `start_date`, `days`, `end_date_exclusive`,
  `day_boundaries_s` (list), `day_kinds` (list from `classify_day` per day),
  while keeping `date`/`begin`/`end` populated when `days == 1` (existing
  consumers: run_scenario.py, serve.py, validate_sim.py, web labels).
- `demand_signature()` (run_scenario.py ~line 56) must incorporate the new
  fields — extend `keys` so single-day signatures stay identical (only add
  fields when present) or bump deliberately and let `clear_stale_scenarios`
  wipe; either is fine, but be explicit in the commit message.
- `ensure_bounds()` (~line 269) and `ensure_priors()` (~line 356) cache per
  exact date — for multi-day they stay keyed to STRUCTURAL_REFERENCE_DATE
  logic exactly as today (structural, per CLAUDE.md design decision); only
  TARGETS become multi-day. Verify this explicitly in tests.
Tests: signature stability for single-day; range validation; metadata shape.

### B2. Multi-day demand build — size L — depends B0, B1
- Candidate pool: build per-day BLOCKS. Reuse the expensive spatial
  structure; per day, use that day's own `real_day_shape()` profile (keep the
  exact-day-capture design — do NOT regress to pure day-type pools), shift
  departs by `day_index * 86400`, prefix vehicle IDs per day.
  Refactor `build_candidates.py` minimally: a function generating one day
  block given (profile, offset_s, id_prefix, seed+day_index); the CLI keeps
  working for one day.
- **Pool size**: do NOT let `n_total` scale linearly to 84 k for a week
  (routeSampler/PFE input explosion). The pool needs DIVERSITY not volume
  (existing comment, build_sumo_demand.py ~line 62). Suggested: per-day-type
  pools of the current whole-day size, reused across same-type days but with
  per-day departure RESAMPLING from that day's profile. Measure PFE shape-pool
  size and solve time at 2 days before committing to the week design.
- PFE: no changes to pfe.py expected — quarters are independent; the flat
  pool just gets 96×N×3 jobs. Benchmark 2-day and 7-day builds; record here.
  Expected order: ~40-45 min/week (extrapolated — verify).
- Targets: `build_targets` already iterates `qi_start + i` over flows.json —
  multi-day needs only correct `qi_start`/`n_intervals` and DST care:
  2025-03-30 and 2025-10-26 have 4 missing quarters each (nulls; see
  CLAUDE.md DST note) — assert targets handle nulls (they do today; add a
  test for a range containing 2025-03-30).
Acceptance: 100% GEH<5 on all variants for a 2-day build; week build completes
under ~1 h; numbers recorded here.

### B3. Multi-day scenarios end-to-end — size M — depends B2
- `run_scenario.py`: `duration_s = n_intervals * 900` already generalizes;
  verify edgeData parsing >96 intervals (parse_edgedata caps at n_intervals —
  fine), keep the +3600 s flush (its output is already excluded).
- Trajectories: default OFF above 1 day (file would be ~90 MB/week); add
  `--trajectories` opt-in. Scenario JSON already carries `trajectories: null`
  path (web handles absence — verify, it was built that way).
- Web: day boundaries + day labels on the time scrubber in Simulering mode
  (epoch + qi arithmetic already exists; 2025 starts Wednesday — reuse the
  NormalProfile dayOfWeek convention, CLAUDE.md contract). Playback presets
  unchanged.
- serve.py `/api/recalibrate`: accept `days` param (default 1; cap at 7 and
  document the ~45 min cost in the UI text; timeout must scale: 2400 s is
  calibrated for ONE day — make it `base + per_day * days`).
Acceptance: run a full week baseline + one closure scenario; scrub through 7
days in the browser; CDP-screenshot verification (pattern from 2026-07-09
browser test: headless Chrome, `--remote-debugging-port`, real clicks).

---

## Phase C — "Best time to close a road" suggester

### C1. Prototype: temporary closure semantics in SUMO — size S — GATE for C2+
Minimal isolated net + demand (fixture-scale, like tests/) proving:
- `closingReroute` inside `<interval begin end>` with `disallow="all"`:
  road reopens at `end`; vehicles without detour WAIT (not teleport) until
  reopening (SUMO-doc claim — verify empirically, including what teleport
  warnings appear and after how long `--time-to-teleport` default 300 s);
- already-rerouted vehicles do NOT revert after reopening (doc-inferred,
  NOT guaranteed — this is exactly what to verify);
- compare `--ignore-route-errors` vs routing mode 8 (SUMO docs recommend
  mode 8 for temporary permission changes).
Record findings HERE. They decide C2's stranded-vehicle policy.

### C2. Time-windowed closures in the scenario engine — size M — depends C1
- `run_scenario.py` CLI: closures become structured:
  `--close EDGE --close-begin ISO --close-end ISO` (repeatable groups or a
  JSON arg; avoid parallel lists). Internal: `[{edge_id, begin_s, end_s}]`.
  Omitted window = whole run (today's behavior, backward compatible).
- `write_closure_additional()` (~line 147): one `<interval>` per window.
- `truncate_stranded_vehicles()` becomes time-aware and CONSERVATIVE:
  a vehicle is affected only if it would hit the closed edge DURING the
  window (estimate arrival conservatively from depart + free-flow times, or
  simpler: only prefilter vehicles whose whole trip lies inside the window;
  let SUMO's wait-until-reopen handle the rest — C1's findings decide).
  For full-duration closures keep exactly today's verified behavior
  (11 tests in TestTruncateStrandedVehicles must keep passing untouched).
- Scenario JSON + manifest + web popup: carry and display the window
  (black-dashed closed styling should only apply during the window when
  scrubbing — render reads provider.closedEdges; make it time-dependent).
Tests: windowed rerouter XML; time-aware prefilter; full-window regression.

### C3. Disruption metrics + fair baseline comparison — size M — SHARED with Phase D
New module (suggest `closure_metrics.py`):
- Enable per-seed `--tripinfo-output` with `tripinfo-output.write-unfinished`,
  `--statistic-output`, summary output in `run_sumo` (opt-in flag so
  interactive closures stay fast; measure the overhead — likely small).
- Primary score: Δ total timeLoss vs a baseline run with SAME demand, seeds,
  variants. Guard metrics reported alongside, ALWAYS: teleport count+reasons,
  stranded/truncated counts (split: unreachable vs waiting), max queue
  (queue-output is experimental — diagnostics only), throughput on the closed
  edge, unfinished-trip count. A candidate that "wins" timeLoss by teleporting
  or dropping vehicles is DISQUALIFIED, not ranked better.
- GEH is NOT a disruption metric (sensor throughput is blind to waiting time).
Tests: metric extraction from fixture tripinfo XML; disqualification rule.

### C4. `suggest_closure_time.py` — size M — depends C2, C3
Offline/batch two-stage search, standalone script (NOT logic inside serve.py):
1. **Proxy stage (seconds, no simulation):** for every candidate window
   (default: user gives closure duration D and a date range; windows slide
   hourly ⇒ e.g. 163 candidates for 6 h over a week): score = baseline/PFE
   flow on the closed edge during the window + detour availability (reuse
   `build_edge_graph`/`reachable`) + load headroom on the local detour
   corridor (from baseline edgeData). Proxy is a RANKING only — never show
   it as predicted delay minutes.
2. **Simulate top-k** (default 10-20) + controls (best proxy candidate per
   day, one intuitive low-traffic candidate, 1-2 deliberately-bad windows),
   all against the same baseline. Rank by C3 scorecard.
3. **Validate the proxy while running:** Spearman correlation proxy-rank vs
   simulated ΔtimeLoss, and whether the simulated best was inside proxy
   top-k. Print + store both. If correlation is poor, the tool must SAY so
   in its output — and the plan is to widen k, not to trust the proxy.
Output: result JSON with method, candidate set, proxy scores, simulated
metrics, demand_signature, seeds — reproducible without the web.
Compute: top-15 × ~40 s meso ≈ 10-15 min batch for a week — acceptable.

### C5. API + UI — size L — depends C4
- serve.py: start/status/result endpoints following the PROVEN async
  recalibrate pattern (background thread, 202 immediately, poll; page-load
  status check — see CLAUDE.md production-incident lesson: NEVER tie work
  outliving a browser request to one blocking call). Separate lock from
  demand-rebuild lock, understandable status.
- Web: panel in Simulering mode — pick edge(s) (reuse click-picking), closure
  duration, date range → progress → ranked result table (window, median
  ΔtimeLoss + seed interval, affected vehicles, max queue, honest "top-k of N
  simulated" label). Loading a result row loads its scenario.
- Honest presentation rules (product promise): show interval over seeds, name
  the baseline, label proxy-only numbers as ranking, forecast source labelled.

---

## Phase D — Traffic-signal optimization

Constraint recap (measured, see 0.1): meso does not execute signal plans;
all 65 TLS are synthetic 90 s guesses. Therefore signal results are computed
in MICRO on bounded windows, as a separate async analysis — meso stays the
engine for interactive closures. With guessed baselines the ONLY honest claim
is "optimized vs synthetic default plan in the model", never "better than
Gothenburg's real signals today" — label accordingly (new provenance field:
`synthetic | city-configured | verified`).

### D1. Reproducible signal-experiment harness + baseline — size M — depends A1
Script (suggest `signal_lab.py`): given demand + window (default: 07:00-09:00
of the calibrated day) runs micro with fixed seeds and records the C3 metric
set per run into a results JSON (command line + inputs hashed for
reproducibility — the thing the old tls_verify files lacked). Measure micro
runtime for 60/90/120-min windows (whole-day micro ≈ 25 min; a 2 h window
should be minutes — verify) and record here.

### D2. Off-the-shelf optimizers — size M — depends D1
Run SUMO tools on our calibrated routes:
- `tlsCycleAdaptation.py` (Webster cycle/green splits, per intersection,
  hourly) and `tlsCoordinator.py` (green-wave offsets; wants a common cycle —
  the net is uniformly 90 s, convenient). Both consume explicit
  `<vehicle><route>` — PFE output qualifies.
- Evaluate via D1 harness: baseline vs adapted vs adapted+coordinated,
  3 seeds. Record absolute + relative deltas. Expect possibly LARGE relative
  wins purely because the baseline is a naive guess — report absolute numbers
  and say "vs synthetic baseline" explicitly.
- Also test SUMO built-in `actuated` and `delay_based` TLS types in micro
  as a no-optimization reference point.

### D3. Meso screening feasibility — size M — depends D1
Test whether cheap screening is possible: per-TLS
`<param key="meso.tls.control" value="true"/>` (full-detail static TLS at
selected junctions in meso) and `--meso-tls-penalty`/`--meso-tls-flow-penalty`
on 1-3 junctions near a closure; compare against micro ground truth from D1.
SUMO warns about short (<15 m) approach edges in meso TLS — check ours.
Outcome: either "meso screening correlates, use it for candidate filtering"
or "micro-only, windows stay short". Record either way.

### D4. Closure + signals combined — size L — depends C2, D2
Two-pass loop: run closure (meso) → extract ACTUALLY rerouted routes
(vehroute output) → optimize signals against those (D2 tools) → evaluate in
micro window (D1). Check whether one iteration stabilizes route choice or
signal changes shift routing enough to need a second pass (measure, decide).
This is the deliverable Gustav described: "when a road closes, how should the
lights adapt".

### D5. UI + provenance — size L — depends D2 (+D4 for combined)
"Optimera signaler" action per scenario (async start/poll like C5), result =
before/after metric card + per-junction plan diff (cycle/splits/offsets),
signal-provenance label rendered wherever signal results are shown.

### D6 (external, unblocks honesty upgrade). Real signal plans from the city
Ask via Miroslaw/city contacts for: signal-object ↔ intersection ↔ SUMO TLS-ID
mapping, phase diagrams, cycle/green/offset per time-of-day plan, detector
logic, bus priority. When delivered: import layer replacing the 65 guessed
programs, re-enable full junction control in meso (CLAUDE.md's stated
condition), and flip provenance to `city-configured`.

---

## Suggested execution order

A1→A4 (one commit each or one small batch) → B0 ∥ C1 (both small gates,
independent) → B1 → B2 → B3 → C2 → C3 → C4 → C5, with D1 startable any time
after A1, D2/D3 after D1, D4 after C2+D2, D5 last. C3 is shared by C and D —
build it once, generically.

Everything committed to `main` with the session's established discipline:
verify with the full test suite + an end-to-end run before every push;
Codex review for anything subtle; honest commit messages recording what was
measured, not just what was written.
