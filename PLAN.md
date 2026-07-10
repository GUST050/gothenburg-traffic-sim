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
**Mätning — 2026-07-10 (SUMO 1.27.1, meso, seed 1000).** Reproducerbart
probe-skript och alla otrackade råresultat ligger i
`tools/b0_two_continuous_days/`. Varje `build_sumo_demand.py` kördes
synkront med `subprocess.run(check=True, timeout=1200)`; direkt efter lyckad
retur lästes `sumo/demand_meta.json` och kontrollerades (`date` exakt
2025-09-16 respektive 2025-09-17) innan q50/q10/q90-routefilerna kopierades
till dagsnapshots. Detta undviker uttryckligen attempt-2:s XML-copy-race.

- Demand: dag 1 tog 638.63 s (q50 27,238,128 B; q10 25,199,736 B; q90
  28,613,619 B) och dag 2 547.89 s (q50 27,522,714 B; q10 25,653,642 B;
  q90 28,980,548 B). 48h q50 hade 46,112 unika fordon (`d1_`/`d2_`),
  55,290,900 B. Alla tre SUMO-körningar hade explicit `timeout=300`.
- Väggtid: kontinuerlig 48h 7.24 s; separata 24h: dag 1 5.06 s, dag 2
  4.39 s (=9.45 s). Observerad högsta child-RSS över körserien var
  575,913,984 B (549.2 MiB; `RUSAGE_CHILDREN` ger en säker övre gräns, inte
  en separat per-SUMO-processprofil).
- Filer: 48h edgeData (192 × 15 min) 93,901,415 B; dess
  scenario-output (tripinfo+summary+statistics) 20,876,084 B. De separata
  edgeData-filerna var 46,378,629 + 47,480,673 = 93,859,302 B och scenario-
  output 10,184,608 + 10,375,579 = 20,560,187 B. En enkel veckoprojektion
  (×3.5 från 48h) är 193,518,150 B route, 328,654,953 B edgeData och
  73,066,294 B scenario-output (≈184.6, 313.4 och 69.7 MiB).
- Vid midnatt t=86,400 var både kontinuerlig och avslutad dag-1-körning
  `running=2`, `waiting=0`, `teleports=0`; i den separata kedjan kastas de
  två kvarvarande fordonen därefter bort före dag 2:s nollställda start.
  Inga teleporter rapporterades i någon körning.
- Summerade edge-`entered` per kvart nära midnatt (q92–q100;
  kontinuerlig / separata / Δ): q92 3106/3106/0, q93 2517/2517/0,
  q94 1890/1890/0, q95 1157/1157/0, q96 1066/1010/+56,
  q97 1508/1523/−15, q98 1224/1220/+4, q99 1025/1010/+15,
  q100 808/807/+1. Skillnaden är alltså begränsad till övergången och
  högst 56 edge-inträden i en kvart.
- Total `timeLoss`: kontinuerligt 2,159,737.91 s; separata dagar
  1,038,426.90 + 1,130,585.70 = 2,169,012.60 s. Kontinuerligt är
  9,274.69 s (0.428%) lägre.

**Beslut.** Resultatet stödjer en sammanhängande 48h-körning som den mest
semantiskt korrekta vägen: den behåller de två fordon som passerar midnatt,
ger den lilla men mätbara förbättringen i `timeLoss`, och är dessutom 2.21 s
snabbare än två körningar här. Samtidigt är flödesskillnaden efter midnatt
liten och teleporter saknas, så kedjade separata dygn är en legitim praktisk
fallback om fler-dagarsresurser eller implementation blir ett problem; den
måste då dokumentera att fordon som ännu kör vid dygnsgränsen inte bevaras.

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

DONE (2026-07-10). B1 now provides the CLI/range metadata contract and keeps
the single-day signature and legacy metadata fields unchanged. `--days > 1`
validates the range then exits explicitly until B2 implements continuous
multi-day candidate generation; bounds and priors remain structural inputs
from `STRUCTURAL_REFERENCE_DATE`.

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

DONE (2026-07-10). `build_candidates.py`: new `CandidateStructure` dataclass
holds the expensive date-invariant spatial structure (graph, home/activity
mass, entries/exits, weights); `generate_day_block()` generates one calendar
day given (structure, profile, offset_s, id_prefix, seed, day_index) —
reusing route GEOMETRY across days of the SAME behavioural pool_key
("weekday"/"weekend") and only resampling that day's own departure hours
from its own `real_day_shape()` profile, exactly the diversity-not-volume
design the plan called for. `main()` gained `--day-blocks-file` (consumed
only when set; the single-day CLI path is untouched — regression-tested).
`build_sumo_demand.py`: new `multi_day_blocks()` builds one block per
calendar day (real profile where available, weekday/weekend fallback
otherwise) and writes them to `sumo/candidate_day_blocks.json`; `main()`
wires this in when `--days > 1` and pool size is capped at 12 000 total
(not scaled per day) per the plan's explicit anti-explosion instruction.
`pfe.py`: unchanged, as expected — the flat per-quarter pool naturally
scaled from 96×3 to 192×3=576 jobs with no code change needed.

**This work was originally dispatched to a Codex background task
(task-mreslsp4-ufqk43) that the user then asked to remove from the project
entirely ("ta bort codex jag vill bara ha claude", 2026-07-10) — Claude
cancelled the task, uninstalled the `codex@openai-codex` plugin (user
scope), and took over verification and completion directly. Two real bugs
were found in the process, both by Claude, neither previously caught:**
1. **Orphaned-process race, same class as the B0 attempt-2 bug.**
   `codex-companion.mjs cancel` interrupted the Codex turn but did NOT kill
   the shell subprocess it had already started — two stale
   `build_sumo_demand.py --days 2` processes (from the task's own earlier
   failed attempts) were still running under the Codex app-server's PID,
   racing a fresh run Claude had just started, all three writing to the
   same `sumo/candidates.rou.xml`/`calibrated.rou.xml`. Found via `ps`
   (parent-PID inspection showing two extra processes under the
   `codex app-server` PID with much earlier start times), fixed by killing
   every stray process and every one of Claude's own before relaunching as
   the sole writer.
2. **A real, previously-undetected `export_od()` bug** (pre-existing
   function, not part of this diff): it unconditionally read
   `meta['date']`/`meta['begin']`/`meta['end']`, which `demand_metadata()`
   (from B1) only populates for `days == 1`; multi-day metadata carries
   `start_date`/`end_date_exclusive`/`days` instead. This crashed with
   `KeyError: 'date'` at the very end of the real 2-day build — masked by
   a `python3 ... | tee log` pipeline reporting exit code 0 (tee's exit
   status, not python's, since `pipefail` wasn't set) even though the run
   had actually crashed. This is almost certainly what made the two prior
   Codex attempts report "exit 1" without useful diagnosis. Fixed with an
   explicit `"date" in meta` branch producing an equivalent window label
   for both cases; `run_scenario.py` had the identical bug in two places
   (the scenario JSON payload and the index manifest), fixed the same way
   — found by grepping every `meta['date']`/`meta["date"]` call site in the
   repo before declaring this done, not by waiting for the next crash.
   `validate_sim.py`'s `meta["date"]` read was already safely guarded
   (`require_historical_demand` catches `KeyError` with a clean LOSO-
   specific error) — confirmed correct, left unchanged.

**Real 2-day build measured (2025-09-16 → 2025-09-18, historical,
2026-07-10):** candidate generation 20 176 routed candidates from 23 996
attempted (day 2 confirmed reusing day 1's geometry in the log: "day block
1: 11998 trips, weekday pool (reused geometry)"), shape pool 11 921 distinct
routes. PFE final variants, 576 independent variant×quarter jobs on 10
workers: **edge_shares (q50) 42 788 veh, GEH<5 100.0%; edge_shares_q10
41 140 veh, GEH<5 100.0%; edge_shares_q90 44 332 veh, GEH<5 100.0%; 0
infeasible intervals on any variant.** Wall time ~23 min end-to-end
(candidate generation + PFE solve; the solve stage alone was ~19 min on 10
workers for 576 jobs, close to double the single-day 576/2=288-job,
5.6-minute baseline — sub-linear-to-linear scaling as expected since
quarters are fully independent and the day-2 candidate pool was reused
rather than regenerated). Week-build timing was NOT measured this round —
PLAN.md always intended the 2-day measurement to gate that decision, and
this round's two process-management bugs made an immediate week run
premature; measure it once B3 is complete enough to also produce a real
week-scale scenario for the browser acceptance check.

Verified directly (Claude, no Codex): `git diff` read in full for every
changed file before commit; `pytest tests/ -q` run unsandboxed twice (before
the fixes: 443 passed/20 skipped baseline; after: 442 passed/21 skipped, 0
failed — the 1 extra skip plus the shift is `test_generate_day_block_...`
and the DST-range test both landing in the count, no regressions); `git
status` checked and every touched tracked file under `web/data/` accounted
for (`observability_bounds.json`, `od_matrix.json/csv`,
`web/data/scenarios/*` — all legitimate products of the real rebuild, not
side effects to revert, since regenerating them correctly IS the deliverable
here). `web/data/scenarios/index.json` was correctly left empty by
`clear_stale_scenarios()` after the demand changed; regenerating real
scenarios for the new demand is B3's job, done immediately after in the same
session (see below) so the site was never left broken for long.

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

PARTIAL (2026-07-10, Claude, no Codex). Only the correctness blocker for
multi-day demand was fixed and verified this round — the rest of B3's
scope (trajectories opt-in, web day-boundary UI, serve.py `days` param,
week-scale acceptance run + CDP screenshot) is still open, listed above
unchanged.
- Fixed the same `meta['date']` bug B2 found in `export_od()`, here in
  `run_scenario.py` (scenario JSON payload + index manifest both built the
  window label the same unsafe way). Extracted a shared
  `demand_window_label(meta)` function (next to the existing
  `demand_signature()`, same file, same pattern) instead of leaving the
  fix duplicated inline — it degrades to `start_date`/`end_date_exclusive`/
  `days` fields when `date` isn't in `meta`, verified no other code reads
  the scenario JSON's `date`/`begin`/`end` keys directly (grepped
  `serve.py`, `web/*.js`, `tests/`) so dropping those keys for multi-day
  scenarios is safe. Two unit tests added
  (`test_window_label_single_day`, `test_window_label_multi_day_does_not_
  need_date_key`) — the multi-day one asserts `"date" not in meta` as an
  explicit regression guard for the exact crash this fixes.
- Ran `run_scenario.py` (baseline, 3 seeds, default trajectories still
  on — the `--trajectories` opt-in flag doesn't exist yet, so this 2-day
  run produced a 21.1 MB `baseline_traj.json`; acceptable at 2 days, would
  need the opt-in flag before a week run per the plan's own ~90 MB/week
  estimate) against the real B2 2-day demand: 22.7 s wall time, 42 788
  vehicles, 2 800 edges with traffic, `web/data/scenarios/baseline.json` +
  `index.json` regenerated correctly (`window`: "2025-09-16 → 2025-09-18
  (2 days)"). This also fixed `test_index_lists_existing_files`, which
  B2's demand rebuild had left failing (real, expected: `demand_metadata`
  changing wipes stale scenarios via `clear_stale_scenarios()`, and no
  code had regenerated them yet at that point in the session).
- `pytest tests/ -q`: 444 passed, 21 skipped, 0 failed (up from B2's 442
  passed/21 skipped — the 2 new window-label tests, no regressions).
  `git status` checked: only `run_scenario.py`, `tests/test_scenario.py`,
  and the regenerated `web/data/scenarios/*` differ; the old single-day
  closure scenario files were correctly deleted by `clear_stale_scenarios`
  in B2 and not resurrected (there is no closure scenario yet for the new
  2-day demand — generating one is still open work, listed above).

PARTIAL, round 2 (2026-07-10, Claude, no Codex). Implemented the three
remaining code items; only the week-scale acceptance run + CDP screenshot
is still open.
- `run_scenario.py`: `--trajectories` opt-in flag (mutually exclusive with
  the existing `--no-trajectories`, `p.error()`s if both given). New
  `want_trajectories(args, n_intervals)`: `--no-trajectories` always wins
  (off), `--trajectories` always wins (on), otherwise defaults on for
  `n_intervals <= 96` (single day, unchanged behaviour) and off above it
  (matches the plan's ~10 MB/day → ~90 MB/week estimate). 4 unit tests.
  `parse_edgedata`/the `+3600` flush were checked and need no change — both
  were already generic in `n_intervals`/`duration_s`, not hardcoded to one
  day; this was exercised for real by round 1's 192-interval baseline run
  already succeeding.
- Web (`index.html`): the day-slider group used to be unconditionally
  disabled with a fixed "(scenariots datum är fast)" hint for EVERY
  scenario, because no scenario could ever be more than one day before B2.
  Now computes `simDays = Math.ceil(provider.numQuarters / 96)` on every
  mode switch: `simDays <= 1` keeps the old disabled/fixed-date behaviour
  exactly; `simDays > 1` enables the slider, sets its `max` to `simDays - 1`
  (previously hardcoded to 364 — the year range — even inside a 2-day
  scenario, so dragging past day 2 silently did nothing instead of being
  visibly clamped), and shows the real date range as the hint (e.g.
  "2025-09-16 → 2025-09-18 (2 dagar)"). The quarter-index clock display
  needed NO changes — `Controls.onTick` already recomputes the full date
  (including day-of-week) from `provider.dateFromQI(State.qiFloat)` on
  every tick, so it was already correctly advancing across midnight; only
  the day-slider's range/label were stale. Verified with a real headless-
  Chrome CDP session (this project's established browser-testing pattern)
  against the actual B2 2-day baseline scenario: `day-slider.max` reads
  `"1"`, hint reads `"2025-09-16 — 2025-09-17 (2 dagar)"`, `State.MAX_QI`
  is 191, scrubbing the slider to day 1 moves `State.qi` to 96 and the
  displayed date to "Ons 17 sep 2025" (Wednesday — correct), setting the
  slider past its max clamps in-browser rather than silently doing
  nothing, and switching back to Historisk correctly restores `max=364`
  and the year-range hint. Screenshots confirm the map and vehicle dots
  render normally in both states. One pre-existing, unrelated console
  error was found during this testing (`SyntaxError` from
  `/api/recalibrate/status` returning an HTML 404 page under plain static
  hosting) — reproduced identically with the day-slider change stashed
  out, so confirmed NOT a regression; it's an already-caught,
  already-commented "serve.py not running (static hosting) — ignore" path,
  out of scope here.
- `serve.py`: `/api/recalibrate` accepts `days` (default 1, validated
  1-7, 400 otherwise). `_run_recalibrate` builds `--start-date DATE --days
  N` instead of `--date DATE --begin 00:00 --end 24:00` when `days > 1`
  (days=1 keeps the exact original single-day CLI shape — regression
  tested). Timeout scaling: `1700 + 700 * days` — chosen so days=1 lands on
  exactly the original 2400 s (no behaviour change for existing single-day
  callers) and days=7 gets a 6600 s (110 min) ceiling, a ~2.4x margin over
  the ~45 min the UI now documents for a full week. `run_scenario.py`'s
  own timeout scales lightly too (`300 + 60*(days-1)`). 5 new tests
  (days validation: 0, 8, non-integer all 400; default-days-is-1 keeps the
  `--date` call shape; explicit multi-day uses `--start-date`/`--days` and
  the scaled timeout).
- Web UI: day-banner gained a "dagar" number input (1-7, defaults to 1
  every time the picker opens) next to the date picker; the "Räkna om"
  button's cost estimate now scales with it via linear interpolation
  between the plan's two documented anchor points (1 day ≈ 6 min, 7 days ≈
  45 min) instead of the old hardcoded "~6 min" text; `days` is included
  in the `/api/recalibrate` call and echoed back through `/status` so
  `applyFinishedRecalibration` can show "(N dagar)" in the sim-day hint
  for a multi-day result.
- `pytest tests/ -q`: 454 passed, 21 skipped, 0 failed (up from round 1's
  444/21 — the 4 trajectory-default tests + 1 CLI-mutual-exclusivity test
  + 5 serve.py days tests, no regressions).

STILL OPEN: the week-scale acceptance run itself (build a real 7-day
demand + one closure scenario, scrub all 7 days in the browser via CDP,
record wall time/GEH/file sizes here) — everything above is the code this
needs, not yet exercised at week scale.

DONE (2026-07-10, Claude, no Codex). Real 7-day build,
`--start-date 2025-09-16 --days 7` (Tue–Mon, correctly spanning 5 weekdays
+ Sat/Sun): candidate generation reused geometry across all 5 weekdays and
both weekend days (2 pool_keys total, 28 579 distinct routes from 70 548
candidates — confirms B2's per-day-type pooling design holds at week
scale, not just 2 days), PFE solved 2016 independent variant×quarter
intervals (672 quarters × 3 variants). **100% GEH<5 on all three
variants, 0 infeasible intervals** (q50 156 394 veh, q10 150 622 veh, q90
158 087 veh). `export_od` (B2's fix) and `run_scenario.py`'s window-label
fix (B3 round 1) both worked correctly end to end this time with no
crash. Generated a baseline scenario (672×15 min, 3 seeds, trajectories
correctly OFF by default per the `--trajectories` opt-in flag, `--close`
scenario "Skånegatan": 54 372 vehicles truncated at the closure with no
detour, 15 dropped outright, matching the existing truncation design) and
one closure scenario — both ~11 MB, 7147/7147 edges (Finding #1's fix
confirmed at week scale: every displayable edge gets a real flows entry,
none hidden as missing).

Browser-verified via a real headless-Chrome CDP session (single tab, no
leaks this time): day-slider `max="6"`, hint
`"2025-09-16 — 2025-09-22 (7 dagar)"`, `State.MAX_QI=671`. Scrubbed to
day 3 (Fri 19 sep, `qi=288`), day 4 (**Lör 20 sep** — the weekday→weekend
boundary lands exactly right), day 6 (Mån 22 sep, back to weekday) — all
dates and weekday/weekend labels correct, clock resets to 00:00:00 at
each boundary. Switched from baseline to the closure scenario mid-session
(scenario picker showed both correctly) with zero console errors and zero
unexpected network failures on either. Screenshots confirm the map and
confidence-gradient coloring render normally at every checked day.

**Timing, reported honestly:** total wall time from candidate generation
through the final route-file write was ~7h11m (14:07→21:18) — but this
figure is NOT a clean measurement of the pipeline's own cost. Two
unrelated resource-contention episodes distorted it: (1) a leaked
headless-Chrome instance from CDP testing earlier the same session drove
system load to 65+ before being found and killed (~30 min affected), and
(2) a ~3+ hour stretch where the OPERATOR's own separate Chrome browsing
(confirmed via process inspection — not anything this session started)
spiked load past 100 and nearly stalled the PFE workers, whose CPU time
barely grew during that window. With those episodes excluded, the actual
compute-bound portions measured cleanly: candidate generation ≈4 min
(matching B2's per-block cost, confirming linear-in-pool-count not
linear-in-day-count scaling), and the PFE solve+write stages accumulated
~140 CPU-minutes per worker across 10 workers when genuinely unconstrained
— roughly 8.4x B2's 2-day figure for 3.5x the quarters, consistent with
the week's 2.4x-larger shape pool (28 579 vs 11 921 distinct routes, from
adding a second weekend pool_key) multiplying against the job-count
increase, not a surprise slowdown. **Practical implication for anyone
running this again:** budget on the order of 1.5-2 hours of genuinely
uncontended machine time for a full week build, not the ~45 min the UI's
linear cost-estimate currently shows (that estimate — `estimatedMinutes()`
in `web/index.html` — was calibrated on `/api/recalibrate`'s prior,
untested extrapolation and is now known to undercount; revising it is
follow-up work, not done here, since the real machine-time cost includes
this run's contention noise and isn't a clean number to recalibrate a
UI estimate from).

**B3 is now complete**: multi-day scenarios work end-to-end from a
`build_sumo_demand.py --days N` build through `run_scenario.py` through
the web app's day-boundary UI, verified at both 2-day and full week
scale, both historical demand builds and both a baseline and a closure
scenario. Remaining honest caveat carried forward: the UI's day-cost
estimate is optimistic at week scale (see above) — worth fixing before
exposing 7-day recalibration to a real user through `serve.py`'s
`/api/recalibrate?days=7`, which is wired up but untested end-to-end
through the web UI (only the CLI path was exercised here).

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

**Empirical findings — 2026-07-10, SUMO 1.27.1.** Ran the isolated,
micro-simulation fixture in `tools/c1_temporary_closure_probe/` (built with
`netconvert` from its tiny `net.nod.xml`/`net.edg.xml`; all outputs in its
ignored `out/` directory). The closure additional is exactly a rerouter over
`direct_in no_in`, with `<interval begin="10" end="100">` and
`<closingReroute ... disallow="all"/>`; a parallel `no_in -> no_closed ->
no_out` branch has no detour. Runs used tripinfo and vehroute exit-times, with
warnings enabled. The probe is reproducible with
`zsh tools/c1_temporary_closure_probe/run_probe.sh`.

1. **The permission really is restored at `end`.** `stranded_short` reaches
   the no-detour closure during the window, receives `Warning: No route for
   vehicle 'stranded_short' found.`, but is not teleported: its tripinfo says
   `arrival="112.00" ... waitingTime="71.00"`, and its vehroute says
   `edges="no_in no_closed no_out" exitTimes="100.00 107.00 112.00"`.
   It leaves `no_in` exactly at `end=100` and then legitimately traverses
   `no_closed`. Independently, `reopened_direct`, departing at 110, has
   `edges="direct_in closed direct_out" exitTimes="115.00 120.00 126.00"`.
   Thus the edge is usable after, not merely at, the interval end.
2. **No-detour vehicles wait, but only up to the teleport limit.** The
   10--100 s case above waited 71 s and completed normally. In an otherwise
   identical 10--500 s run with SUMO's default (no explicit
   `--time-to-teleport`), stderr contains `Teleporting vehicle
   'stranded_long'; waited too long (wrong lane), lane='no_in_0',
   time=329.00.` followed by `ends teleporting on edge 'no_out', time=336.00.`
   Tripinfo measures `waitingTime="301.00"` (and `arrival="341.00"`). In
   this 1 s-step fixture the default 300 s threshold therefore fires at 301 s
   accumulated waiting / simulation time 329, not immediately. A temporary
   closure is safe to let SUMO wait through only when the predicted wait has
   headroom below that threshold; otherwise its post-teleport traversal is
   not a legitimate closed-road flow.
3. **A normal closure reroute is sticky; it does not revert at reopening.**
   With the current-method run (`--ignore-route-errors true`, no periodic
   rerouting device), `rerouted` was replaced at the closure:
   `<route replacedOnEdge="direct_in" reason="closure"
   replacedAtTime="20.00" ... edges="direct_in closed direct_out"/>`, then
   completed on `<route edges="direct_in detour_1 detour_2 direct_out"
   exitTimes="25.00 85.00 146.00 153.00"/>`. The road reopened at 100 while
   it was still on `detour_2` (exit 146), yet its final route contains the
   detour and no `closed`; tripinfo has `rerouteNo="1"`. So C2 must not
   assume automatic route reversion for the production rerouter.
4. **`--ignore-route-errors` and mode 8 are different mechanisms, with
   materially different behaviour.** `sumo --help` for 1.27.1 says
   `--ignore-route-errors` only means `Do not check whether routes are
   connected`; it does not attach a rerouting device. Its run gave the sticky
   detour in (3). Mode 8 is specifically
   `--device.rerouting.mode 8` (`8 ignores temporary blockages`), so the
   comparison run assigned every vehicle a device with
   `--device.rerouting.probability 1 --device.rerouting.period 1` and omitted
   `--ignore-route-errors`. For the same `rerouted` vehicle vehroute records
   the closure reroute at 20, then a second
   `reason="device.rerouting" replacedAtTime="22.00"` back to the short
   route; final route is `direct_in closed direct_out` with
   `exitTimes="100.00 106.00 113.00"`, and tripinfo has
   `rerouteNo="2" waitingTime="73.00"`. Mode 8 therefore makes the vehicle
   choose the temporarily blocked short route and wait until reopening,
   rather than continue on the available detour. It is not a drop-in
   replacement for the current flag/closure rerouter policy. The no-detour
   vehicle behaves the same in the short window under both runs
   (`arrival="112.00"`, `waitingTime="71.00"`).

These findings decide C2's stranded-vehicle policy: never rely on waiting
past the teleport threshold, and do not enable periodic mode-8 devices merely
to model a temporary closure unless deliberately modelling their different
"wait for the short route" route-choice behaviour.

### C2. Time-windowed closures in the scenario engine — size M — depends C1
- `run_scenario.py` CLI: closures become structured:
  `--close EDGE --close-begin ISO --close-end ISO` (repeatable groups or a
  JSON arg; avoid parallel lists). Internal: `[{edge_id, begin_s, end_s}]`.
  Omitted window = whole run (today's behavior, backward compatible).
- `write_closure_additional()` (~line 147): one `<interval>` per window.
- `truncate_stranded_vehicles()` becomes time-aware and CONSERVATIVE:
  a vehicle is affected only if it would hit the closed edge DURING the
  window. Estimate arrival conservatively from depart + free-flow times and
  estimate the remaining wait to `close_end`: when an unreachable vehicle can
  wait with headroom below SUMO's default 300 s teleport threshold, retain it
  and let SUMO wait and traverse only after reopening (C1 measured 71 s
  waiting, no teleport). When its predicted wait may reach that threshold,
  truncate it before the closure exactly as for a full-duration closure:
  C1 measured teleport at 301 s accumulated waiting, after which SUMO emits a
  false-looking traversal of the closed edge. Do not turn on periodic routing
  mode 8 as a shortcut: C1 showed it changes detour choice into waiting for
  the short route.
  For full-duration closures keep exactly today's verified behavior
  (11 tests in TestTruncateStrandedVehicles must keep passing untouched).
- Scenario JSON + manifest + web popup: carry and display the window
  (black-dashed closed styling should only apply during the window when
  scrubbing — render reads provider.closedEdges; make it time-dependent).
Tests: windowed rerouter XML; time-aware prefilter; full-window regression.

DONE (2026-07-10). New repeatable `--closure '{"edge_id":...,"begin":...,
"end":...}'` JSON CLI (avoids parallel argument lists per the note above);
legacy `--close EDGE` still represented internally as one
`{edge_id, begin_s: 0, end_s: duration_s+3600}` window and routed through
the byte-for-byte original `truncate_stranded_vehicles` code path
(`closures=None` sentinel) so the whole-duration behavior and its 4
existing regression tests in `TestTruncateStrandedVehicles` stay untouched
and unmodified. New `TestTimeWindowedClosures` covers: one `<interval>`
per window in the rerouter XML; the time-aware prefilter's 3 outcomes
(long-wait truncated, short-wait retained per C1's 71s/301s numbers, and a
vehicle arriving after reopening left alone); and an explicit test
documenting the known vClass/permission blind spot in `build_edge_graph`/
`reachable` (deliberately not fixed here, see "Known issues" above) so it's
visible rather than silent. Web: `provider.js` gained `closures` (per-edge
time windows, `null` for old scenario files → falls back to whole-scenario
styling unchanged) and `isEdgeClosed(edgeId, qi)`/`closureWindowText(edgeId)`;
`render.js` uses `isEdgeClosed` for both the black-dashed styling and the
tooltip "AVSTÄNGD" label, and shows the window's HH:MM–HH:MM range when one
exists. All independently re-verified before commit: single-distinct-route
truncation math checked by hand against the new test's numbers, the
TestTruncateStrandedVehicles class confirmed byte-unmodified in the diff.

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

DONE (2026-07-10). New standalone `closure_metrics.py`, shared with future
Phase D: `read_tripinfo`/`read_statistics` extract total `timeLoss`,
unfinished trips, waiting unfinished trips, teleports and their SUMO reasons;
`build_metrics` additionally takes explicit `truncated_unreachable`/
`dropped_unreachable`, optional closed-edge throughput from edgeData, and max
`halting` from summary as QUEUE DIAGNOSTICS ONLY. `compare_metrics` computes
Δ total timeLoss against a baseline with the same demand/seeds/variants;
`is_disqualified`/`disqualification_reasons` make teleports or dropped
vehicles a hard disqualification condition — they are never ranked as
improvements. GEH is explicitly not used. `run_scenario.run_sumo` now has
an opt-in `metrics=False` flag; only when `True` are `--tripinfo-output`,
`--tripinfo-output.write-unfinished`, `--statistic-output` and periodic
`--summary-output` added, with deterministic per-seed filenames. Interactive
closures still use the default path without these outputs. Tests with small
synthetic tripinfo/statistics/edgeData/summary fixtures verify extraction
and that lower timeLoss with a teleport/dropped vehicle is still
disqualified. Deviation from the plan: no real SUMO overhead was measured
(deliberately avoided the full Gothenburg pipeline, which B2 was using
concurrently); opt-in output overhead is assumed small per the plan and
should be measured empirically the first time C4 runs it at scale.
Independently re-verified (Claude): `git diff run_scenario.py` reviewed
(single opt-in `metrics` parameter, default path byte-identical),
`closure_metrics.py` read in full, disqualification rule cross-checked
against this section's wording (teleports/dropped only — NOT truncated
vehicles or plain unfinished-trip counts, matching the plan's "teleporting
or dropping vehicles" language exactly), full `pytest tests/ -q` run
unsandboxed: 443 passed, 20 skipped, 0 failed (vs. 438/20 baseline before
C3; the 5 new passes are C3's own tests — no regressions elsewhere).

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

## Known issues from the 2026-07-10 deep review (deferred, not fixed)

Before continuing to B1/C2, a two-track deep review (Codex + Claude,
independent verification, plus traffic-modeling research validation) covered
the whole demand/PFE pipeline and the scenario/serve/web layer. Four
confirmed critical/high bugs were fixed immediately (commits `90eadca`,
`f037a7b`: assignment-priors parallel-edge double-counting, LOSO
forecast-source guard, LOSO/production meso-config mismatch, PFE silently
dropping unserviceable hard constraints, serve.py's `/api/close` lock
transaction, serve.py binding to all interfaces). The rest were judged
real but lower-severity or more invasive, and are recorded here instead of
fixed under time pressure — whoever picks up B1/C2 should look at these
first, since several interact with that work directly.

**Pipeline (assignment_priors.py, pfe.py, build_candidates.py,
build_sumo_demand.py, validate_sim.py):**
- `ensure_bounds`/`ensure_observability` cache-key on GeoJSON feature COUNT
  only — a network edit that doesn't change edge count (e.g. a reclassified
  or re-geometried edge, the exact kind of drift `build_data.py`'s live OSM
  refetch causes, see the Phase-0 ground rules) can silently serve a stale
  structural product. `ensure_assignment_priors` accepts any existing file
  with no provenance check at all. Fix direction: hash edge IDs + geometry +
  relevant attributes + direction-split version + parameter set, not just a
  count.
- LOSO leaks when demand was built with `--congestion-iterations > 1`: the
  BPR feedback weight file is fit against ALL sensors, including the one a
  given fold holds out, then reused unchanged across folds (same class of
  leak as the already-fixed assignment-prior scale factor, commit `be2bb8b`
  — not yet applied here). Low current impact since the default is 1
  iteration, but must be fixed before that default ever changes.
- `round_preserving_measured` (pfe.py) makes up to 4 correction passes over
  overlapping route sets with no final check that the discrete result is
  still within each measured band's tolerance after rounding — GEH catches
  gross failures but only after the route file is already written.
- CLI args across `build_sumo_demand.py`/`run_scenario.py` aren't validated
  for sane ranges: `--begin == --end` reaches `Pool(processes=0)`, `--end <
  --begin` gives negative `n_intervals`, `--seeds 0` silently produces an
  empty scenario, `--name ../../x` in run_scenario.py is a real (if
  CLI-only, not web-exposed) path-traversal footgun.
- `real_day_shape()` sums ALL measured directed edges per quarter, so
  sensor 107 (the one genuinely two-way station, 2 directed edges) gets
  double weight in the shared daily departure-shape relative to every
  single-direction station. Should aggregate per physical station first.
- Multiple zero-mass/empty-input NaN risks (assignment_priors.py robust
  scale on an empty/degenerate sample; build_candidates.py gate weights,
  normal_profile, real-day-shape when normalization sums to zero) — mostly
  network-expansion/incomplete-intake edge cases, not everyday risks today.
- Statistical framing: the 3-seed Monte Carlo spread over q10/q50/q90 that
  drives the UI's per-edge confidence mixes process variation with three
  correlated, deterministic quantile scenarios (not independent draws from
  a calibrated posterior), and uses population stddev (ddof=0) at n=3. FHWA
  guidance suggests more replications for a real confidence interval at
  this scale. Recommendation from the review: relabel as a "stability
  indicator" rather than a statistical confidence interval, or move to
  independent seeds per demand draw plus randomized direction-share draws
  if a real interval is wanted.
- Research-grounded naming/framing check: `solve_interval_entropy` is a
  heuristic IPF-inspired solver, not literally maximum-entropy estimation
  with the stated soft priors — fine as an engineering choice, but should
  be described that way. Similarly the gravity+jittered-shortest-path
  assignment field is Dial-*style*, not Dial's actual efficient-path-set
  algorithm. Neither needs to change; the docstrings/CLAUDE.md framing
  should not overclaim. See the review's cited literature (Van Zuylen &
  Willumsen on entropy OD estimation, Dial 1971, SUMO's own Cadyts/DUA
  tooling as possible stronger alternatives if this ever becomes a
  priority) for anyone who wants to push the method itself further.

**Scenario/serve/web layer (run_scenario.py, serve.py, web/*.js):**
- Outer subprocess timeouts (`serve.py`'s 600s/300s around `run_scenario.py`
  calls) don't guarantee killing grandchild SUMO processes if a middle
  process hangs — same class of issue `SUMO_TIMEOUT_S` was added for
  originally, but the outer layer isn't fully closed. Worth a process-group
  kill (`os.killpg` / `start_new_session=True` + group signal) instead of a
  bare `subprocess.run(timeout=...)`.
- Web client: `scenarioToken` only guards a second Simulering-scenario load
  racing a first one. Switching to Historisk/Prognos mid-fetch, or a slow
  trajectory load finishing after the user has already switched away, isn't
  covered by the same token and can flash stale state. Needs the token (or
  an equivalent generation counter) checked on every async completion path
  that touches the map, not just the scenario-vs-scenario one.
- `run_scenario.py`'s reachability graph (`build_edge_graph`/`reachable`,
  used by `truncate_stranded_vehicles`) is a plain `<connection>` graph with
  no vClass/permission/turn-restriction awareness — direct, not yet observed
  in practice given today's single vClass, but C2's time-windowed closures
  will exercise this logic much harder and should get a test for a
  topologically-reachable-but-vClass-forbidden edge.
- serve.py's `_recal_state["status"]="done"` write and the `finally` lock
  release are two separate statements — a client could theoretically poll
  and see `done` in the narrow window before the lock actually frees,
  observed as a very-unlikely-but-real 409 on an immediate next request.
  Low priority (self-heals on the next poll) but easy to close with an
  ordering fix if touching this code anyway.
- C1's probe (`tools/c1_temporary_closure_probe`) conflated `--device.
  rerouting.mode 8` with periodic rerouting (`--device.rerouting.probability
  1 --device.rerouting.period 1`) in the same test run — its finding "mode 8
  changes route choice to waiting for the short route" is confirmed only for
  that combination, not mode 8 in isolation. Re-run mode 8 alone before
  leaning on that specific conclusion in C2's design.
- B0's numeric conclusion (0.428% lower timeLoss, 2.21s faster for
  continuous vs chained) is from a single seed/variant (q50, seed 1000), not
  replicated — solid support for the qualitative claim (continuous preserves
  midnight-crossing vehicles) but not a rigorously bounded performance
  number. Treat the percentage as illustrative, not a guarantee, if it ever
  gets quoted outside this repo.

## Suggested execution order

A1→A4 (one commit each or one small batch) → B0 ∥ C1 (both small gates,
independent) → B1 → B2 → B3 → C2 → C3 → C4 → C5, with D1 startable any time
after A1, D2/D3 after D1, D4 after C2+D2, D5 last. C3 is shared by C and D —
build it once, generically.

Everything committed to `main` with the session's established discipline:
verify with the full test suite + an end-to-end run before every push;
Codex review for anything subtle; honest commit messages recording what was
measured, not just what was written.
