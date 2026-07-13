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

DONE (2026-07-11). New standalone `suggest_closure_time.py`, not touching
serve.py/web/data/scenarios per the plan. Deviations from the spec, each
made deliberately and reasoned through before implementing:
- **Proxy stage uses the already-built baseline SCENARIO's flows**
  (`web/data/scenarios/baseline.json`, matched against the current
  `demand_signature` — hard error if stale/missing), not a fresh SUMO run
  or raw PFE `achieved` values (the latter is usually stripped from disk
  by `build_sumo_demand.py` unless `keep_achieved` is requested). This is
  what makes the proxy stage GENUINELY "seconds, no simulation" as
  specified — an earlier draft of this file ran an extra throwaway
  baseline simulation just for the proxy's edgeData, defeating that
  requirement; caught in review before committing, not after.
- **Detour availability is a topology-only, per-EDGE diagnostic**, computed
  ONCE (not per candidate window) and reported alongside the ranking, not
  folded into it — realized during design that a road's detour topology
  cannot change hour-to-hour for a fixed edge, so it has zero discriminating
  power between windows and would only ever act as either a no-op or a
  misleading tie-breaker if included in the per-window score.
- **Proxy score is a Borda-style average of two rank positions** (closed-
  edge flow, nearby-corridor flow) using `scipy.stats.rankdata(...,
  method="average")`, not a weighted sum of flow numbers — enforces the
  plan's own "never show it as predicted delay minutes" rule structurally
  (a rank position cannot be misread as a physical quantity) rather than
  by convention. An initial stable-sort-based rank implementation was
  proven wrong by `TestProxyScoresAndRanking.
  test_lower_flow_window_scores_better` (a tied-corridor-flow fixture) —
  ties leaked the fixture's original index order into the combined score
  as a spurious bias; fixed by switching to fractional/average-tie ranking.
- **`aggregate_seed_metrics`**: mean across seeds for volume-like fields
  (each seed is a full independent demand replication, not a partition —
  summing would count every vehicle `seeds` times), SUM for teleports (any
  seed teleporting is a real integrity signal, not something to dilute by
  averaging), truncated/dropped taken from the first seed (computed once
  per demand variant, shared across the seeds that draw it, not
  independently per seed).
Also found and fixed a real bug via the test suite before it ever ran
against SUMO: `detour_availability`'s adjacency graph was built by calling
`run_scenario.build_edge_graph()`, which hardcodes the module-level
`run_scenario.NET_PATH` instead of accepting a path argument — silently
ignoring the `net_path` this function was actually given. No production
impact (production always passes `rs.NET_PATH` for both), but genuinely
untestable and a real API footgun; fixed by building the banned-edge
adjacency directly from `edge_neighbors()`'s already-parsed successor map
instead of a second, path-inconsistent parse.

Verified against real SUMO and real (if small — the 4-hour morning-window
demand happened to be what was locally calibrated at the time) demand:
built a matching baseline scenario, ran `--edge 60786979_3575001205_0
--duration-hours 1 --slide-hours 0.5 --top-k 3 --extra-bad 1 --seeds 1`
end to end. Correctly found and reported this edge's known zero-detour
topology (0/8 predecessor→successor pairs reachable — matches the
`truncate_stranded_vehicles` closure-leak finding for this exact edge
documented above under "CLOSURE-LEAK FIX"), correctly disqualified two
candidate windows for `dropped_unreachable_vehicles`/`teleports`, correctly
skipped the Spearman check when fewer than 3 non-disqualified candidates
remained, wrote a valid result JSON. Added scratch-file tracking
(`--keep-scratch` to opt out) after noticing a search leaves dozens of
route/edgeData/tripinfo files in `sumo/` per run with no natural expiry;
verified twice — once confirming cleanup actually removes every tracked
file, once with `--keep-scratch` confirming it preserves them (including
the baseline's own metrics files, whose name doesn't contain the tool's
`sct_` scratch prefix at all since they're keyed off the unmodified
`calibrated.rou.xml` stem — worth knowing if extending the cleanup list
later). 26 new unit tests (window generation reproduces the plan's own
163-window and 19-window worked examples exactly; detour topology on tiny
synthetic nets; proxy ranking; candidate selection/dedup; seed
aggregation; baseline-loading error paths) — two of the three ranking
tests failed against the pre-fix code, confirming they catch the bugs
above, not just describing intended behavior. Tracked scenario files were
restored via `git checkout --` after the real verification run (the
baseline they produced was a throwaway 4-hour window, not the deployed
7-day one).

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

DONE (2026-07-11). Every bullet implemented; one deliberate scope narrowing
(no date-range picker — explained below) and one real upstream gap in C4
closed along the way (per-seed spread wasn't being tracked at all).

- **C4 gap closed first**: `suggest_closure_time.py`'s `simulate_closure`
  only ever returned the MEAN across seeds — there was no way to show "median
  ΔtimeLoss + seed interval" because the per-seed values were discarded
  before this step existed. Added `delta_time_loss_interval()` (median +
  [min, max] of each candidate's per-seed total_time_loss_s against the
  baseline's per-seed MEAN — not seed-paired, since variants cycle
  independently on each side, so there's no natural 1:1 pairing; same
  independent-ensemble framing already used for q10/q50/q90). 3 new unit
  tests (including the even-seed-count median case).
- **serve.py**: `/api/suggest_closure` (start) + `/api/suggest_closure/status`
  (poll), sharing `_sim_lock` with `/api/close`/`/api/recalibrate` (a
  suggest-closure search is genuinely the same resource class — a batch of
  real SUMO simulations — running it concurrently with either would starve
  both) but with its OWN `_suggest_lock`/`_suggest_state`, matching the
  plan's "separate lock from demand-rebuild lock, understandable status" —
  read as "don't conflate STATUS tracking across job types", not "skip
  resource exclusivity", since the latter would defeat the whole point of
  `_sim_lock`. On "done", the status response holds a CURATED SUMMARY
  (`summarize_suggestion()`), not the raw result file — the full file
  carries every candidate window's complete per-seed metrics, unbounded for
  a week-scale search.
  **`/api/close` was also extended** (`&begin=ISO&end=ISO`, optional) to run
  a time-windowed `--closure` instead of whole-run `--close` — this is how
  "loading a result row" turns a suggestion into a real, viewable scenario,
  reusing 100% of the existing async/poll/activate machinery rather than
  building a parallel path.
  **Real bug found and fixed while wiring this up** (not by a test —
  reasoning through the new code before writing it): the old
  `/api/close`→scenario-manifest matching looked up the just-created
  scenario by `closed_edges` alone. That was safe when only whole-run
  closures existed (one name per edge set, always overwritten). Once
  windowed closures can coexist with a whole-run closure on the SAME edges
  under DIFFERENT manifest names, matching by edge set alone is ambiguous —
  it could silently report the wrong scenario. Fixed by parsing the exact
  name `run_scenario.py` used from its own first stdout line
  (`Scenario '<name>' ...`) instead of re-deriving the naming logic a
  second time (which could drift out of sync with main()'s real logic).
  Two dedicated regression tests: one constructs the exact ambiguous-
  manifest scenario and asserts the right one is picked, one asserts an
  unparseable stdout is a clear error rather than a silent wrong match.
  18 new serve.py tests total (10 for `/api/suggest_closure`, 4 for the
  windowed `/api/close` extension including the ambiguity fix, 6 for
  `summarize_suggestion`'s honest-presentation rules directly).
- **Web UI**: new "🕐 Föreslå tid" entry point in the Simulering panel,
  reusing the EXACT SAME edge click-picking mechanism as "+ Ny avstängning"
  (`selected` Set, `Render.onEdgeClick`) with a third picking mode
  (`suggestMode`) — picking here means "search around this road", not
  "close it". Results render in a new floating panel (`#suggest-results`,
  distinct from the horizontal sim-panel — a ranked table needs real
  vertical space) showing: the baseline's own total time loss and trip
  count named explicitly ("Baslinje: X min över Y resor"); a detour-
  availability warning when the closed edge has a partial or zero-score
  topology; the Spearman correlation with an explicit "don't trust this
  ranking" warning when weak; every row's ΔTid as a MEDIAN with a
  separate min…max spread column (never a single collapsed number);
  disqualified rows visibly greyed with their reasons and an disabled,
  explained "Ladda" button; rows outside the proxy top-k (the low-traffic
  sanity control, the worst-window negative controls) tagged "kontroll" so
  they read as comparison points, not part of the ranked recommendation.
  `pollClose()` was refactored to take an `onProgress` callback (was
  hardcoded to update one specific button) so the SAME polling loop drives
  both the plain closure button and the "Ladda" button's progress text.
- **Scope narrowing, deliberate**: no date-range picker in the UI. C4's
  design (see its own DONE note) deliberately searches over WHATEVER demand
  period is currently calibrated, not an arbitrary caller-chosen range —
  building a new date range means recalibrating demand first (a separate,
  already-async "📅 Byt dag" action, ~6-45 min), which is out of scope for
  a search tool whose own point is being fast. The UI only exposes closure
  DURATION (hours); the search window is implicitly "however much demand is
  currently loaded", exactly matching what the CLI tool itself supports.
  `top_k`/`extra_bad`/`seeds` are also not exposed in the UI — sane
  server-side defaults (15/2/3) rather than a cluttered advanced-options
  form; they remain available via the CLI/API directly for anyone who wants
  to tune them.

Verified end-to-end with a real headless-Chrome CDP session against the
REAL serve.py (not mocked) and a real, matching baseline scenario: entered
Simulering, entered suggest-picking mode, clicked a real edge, ran a real
1-hour-duration search (4 candidate windows against the small locally
calibrated demand window, ~87 s wall time for baseline + 4×3-seed
candidates, all real meso SUMO runs), got a correctly rendered results
table (title, baseline reference, a real detour-availability warning for
this edge — 9/21 possible detours — a real Spearman ρ=0.74 "trust the
ranking" message, 4 rows with median+spread+status), clicked "Ladda" on
the top row, watched it start a REAL windowed closure via the extended
`/api/close`, poll to completion, and correctly activate a brand-new
scenario (`close_..._99fc2f6f.json`) on the map — zero console errors
throughout. Browser-test-produced scenario files and all `sumo/` scratch
were removed afterward; the tracked `baseline.json`/`index.json` restored
via `git checkout --` (the test baseline was a throwaway small-window
build, not the deployed one).

544 tests passed, 20 skipped, 0 failed (up from 518 before this step — 26
new: 3 for the per-seed interval helper, 23 across serve.py's new/changed
endpoints and `summarize_suggestion`).

**PLAN.md's Phase C ("best time to close a road") is now fully complete —
C1 through C5 all done.** Remaining work is entirely Phase D (traffic-
signal optimization), which was already the next item in the suggested
execution order.

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

DONE (2026-07-11). New standalone `signal_lab.py`. One small, low-risk
change to `run_scenario.run_sumo()` first: added an optional `begin_s: int
= 0` parameter (every existing caller's behaviour is byte-identical —
`--begin` was hardcoded to `"0"` before) so a BOUNDED time-of-day window
actually shifts SUMO's own simulation start instead of running the whole
day and filtering after the fact — a vehicle with `depart < begin_s`
simply never departs. Also found and fixed, while writing `signal_lab.py`
itself (before ever running it against SUMO): `run_sumo()` unconditionally
passed `-a ""` when `add_paths` was empty, which no EXISTING caller ever
does (every one always has at least an edgeData additional) — genuinely
untested SUMO territory. `signal_lab.py` is the first caller with no
additional file at all (`metrics=True` alone is sufficient — tripinfo/
statistics/summary are separate flags, independent of `-a`), so the `-a`
flag is now omitted entirely when `add_paths` is empty rather than risking
an empty-string argument. 2 new tests for `begin_s`, regression tests for
the existing metrics-opt-in behaviour re-run clean.

`window_offsets_s()` converts `HH:MM` wall-clock times to offsets from
`epoch_sim` using the exact same convention `structured_closures()` already
established for `--closure` begin/end (verified against the real
`demand_meta.json`: `epoch_sim` is always tz-naive local wall time, matching
`structured_closures()`'s own assumption — an initial test asserting a
trailing-`Z` epoch should work was itself wrong, since no producer in this
codebase ever writes one; fixed the test, not the code, once traced back to
the real contract). Provenance: `net_fingerprint()` (sha1 of `net.net.xml`'s
bytes — changes whenever TLS programs/connections/geometry change),
`demand_signature()` (reused from `run_scenario.py`), the raw CLI
`sys.argv`, and a parsed `sumo --version` string are all written into every
result JSON, plus an explicit `tls_provenance: "synthetic"` field
(hardcoded — every one of net.net.xml's 65 TLS programs is still a
netconvert `--tls.guess` default; this flips to `"city-configured"` only
when D6 imports real plans). `aggregate_seed_metrics()` is imported directly
from `suggest_closure_time.py` rather than re-derived a third time (same
mean/sum/max-per-field rules as C4/C5, already tested there).

**Runtime measurement, real data** (built a genuine fresh whole-day demand,
`build_sumo_demand.py --begin 00:00 --end 24:00`, 96 quarters/22 841
calibrated trips, restored the pre-existing tracked scenario/od-matrix
files afterward via `git checkout --` since this was a measurement run, not
an intentional redeploy):

| Window | Trips inserted | Wall time (1 seed) | Wall time (3 seeds, default) |
|---|---|---|---|
| 07:00–08:00 (60 min) | 3 419 | 5.3 s | — |
| 07:00–08:30 (90 min) | 4 139 | 6.3 s | — |
| 07:00–09:00 (120 min) | 4 829 | 8.3 s | 27.3 s (8.0+7.1+12.2) |

The plan's own expectation — "whole-day micro ≈ 25 min; a 2 h window should
be minutes" — is not just met but beaten: a 2-hour window is under 10
seconds per seed, under 30 seconds for the harness's default 3-seed run.
(`--end` still adds the same +3600 s flush margin every other `run_sumo()`
caller gets — meaning the ACTUAL simulated span for a "120-min" window is
07:00→09:00 clock time as requested, SUMO's own `--end` argument sits an
hour further out than that to let near-boundary vehicles finish rather than
being force-counted "unfinished" — this is why even the 60-min case's real
simulated span is closer to 2 hours of SUMO time, and the runtimes above
should be read as "safe to run interactively", not literally
proportional to window length.)

**Real finding, not a signal_lab.py bug**: the 3-seed 120-min run's per-seed
teleport counts were wildly uneven — 21, 8, then 360 for
`calibrated_v2.rou.xml` (the q90 direction-split demand variant) — versus
8-21 for the other two. `aggregate_seed_metrics`'s SUM-not-mean choice for
teleports is exactly what surfaced this rather than hiding it in an
averaged 129.7. This is a real signal about that specific demand variant's
micro-simulation behaviour (out of D1's own scope — this script's job is
the harness, not diagnosing PFE variant quality) worth flagging for whoever
picks up D2+: don't assume all three demand variants behave equivalently in
micro just because they're all 100% GEH<5 in meso/PFE terms.

10 new unit tests (`window_offsets_s` including the epoch-convention case
above, `net_fingerprint`, `sumo_version`'s graceful-degradation path, the
window-fits-inside-demand-period bound check) — the heavy end (an actual
micro SUMO run) is intentionally not unit-tested, exercised manually
against real demand instead, matching this project's established pattern
for every other SUMO-invoking script. Full suite: 556 passed, 20 skipped,
0 failed (up from 544 before this step).

**CORRECTED 2026-07-11.** The paragraph above about the `+3600 s` flush
margin was WRONG about what it actually does: it does not just "let near-
boundary vehicles finish" — it lets ENTIRELY NEW vehicles DEPART up to an
hour past the requested window and counts them in the "window" total.
Verified directly against real demand: a nominal 07:00-09:00 experiment
was actually simulating 07:00-10:00, and 1 886 of 3 419 tripinfo entries
(55%) had depart >= 09:00 — the reported numbers in the table above are
contaminated and superseded. See the consolidated correction section
after D3 below for the fix, the corrected table, and why this was found
by an external review, not caught here originally.

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

DONE (2026-07-11). New standalone `signal_optimize.py`. Two small,
additive changes to `run_scenario.run_sumo()` first (same low-risk pattern
as D1's `begin_s`): a `net_path: Path | None = None` parameter, because
`actuated`/`delay_based` turned out to be NETWORK-BUILD-time choices, not
runtime flags — verified directly (`sumo --tls.default-type` does not
exist; only `netconvert --tls.default-type` does), so each type needs its
own network file built from the SAME plain nod/edg XML
`build_sumo_net.py` uses (verified: 27 614/27 614 edge IDs identical
between the deployed network and a freshly rebuilt `--tls.default-type
actuated` variant — the contract holds).

**Mechanism verified empirically before relying on it** (this project's
established discipline, same as the C1 closure probe): it was NOT obvious
from documentation alone whether loading a second `<tlLogic>` program via
`-a` for an id SUMO already knows (from the net file) actually becomes the
ACTIVE program, or merely an inert alternate one requiring a runtime
switch. Used TraCI directly (`traci.trafficlight.getProgram(id)`
immediately after `traci.start(...)`) against the real project network:
confirmed the answer is "yes, the LAST-LOADED program is active by
default" — loading only `net.net.xml` gives active program `"0"`; adding
`-a adapted.add.xml` (tlsCycleAdaptation's own default programID `"a"`)
switches the active program to `"a"`; adding
`-a adapted.add.xml,coordinated.add.xml` together still resolves to `"a"`
with tlsCoordinator's offset override applied on top (its output is a
sparse `<tlLogic id=... programID="a" offset="..."/>` with no `<phase>`
children — an offset-only override of an EXISTING program, not a new one,
confirmed by inspecting its real output against our network: 481
coordinated TLS pairs, offsets from -280 to +366 s).

Five conditions run per window: `baseline` (deployed net, programID `"0"`,
untouched synthetic 90 s cycle), `adapted` (`-a` the Webster output),
`adapted_coordinated` (`-a` both Webster + coordinator outputs),
`actuated`, `delay_based` (each its own rebuilt network). `relative_pct()`
guards the zero-baseline division case explicitly (returns `None`, never
raises or emits inf/nan) — a real edge case for a genuinely empty window.

**Real result, run against the same real whole-day demand D1 measured
(96 quarters, 22 841 trips), window 07:00-09:00, 3 seeds, ~252 s total
runtime** — reported exactly as measured, not adjusted to match the
plan's own expectation of "possibly large wins":

| Condition | Δ time loss (abs) | Δ time loss (%) | Teleports | Disqualified |
|---|---|---|---|---|
| baseline | — | — | 389 | — |
| adapted | +3 871 943 s | +195.7% | 2 672 | yes |
| adapted_coordinated | +4 845 085 s | +244.9% | 3 276 | yes |
| actuated | +4 566 729 s | +230.9% | 3 228 | yes |
| delay_based | +2 868 791 s | +145.0% | 1 936 | yes |

**All four alternatives were WORSE than the naive synthetic baseline, by
a lot, and all four were disqualified for teleports** — the opposite of
what the plan anticipated ("expect possibly LARGE relative wins"). This
was investigated, not just reported blind: (1) confirmed it isn't a
config bug — a clean, warning-free run against the `actuated` network
with `--no-warnings` REMOVED produced zero stderr output, so there's no
suppressed "missing detector" or fallback warning explaining it away; (2)
per-seed time-loss values are wildly inconsistent ACROSS conditions
(e.g. seed index 1 is the BEST performer for `baseline`/`adapted`/
`adapted_coordinated`/`actuated` but the WORST for `delay_based`,
10 020 592 s vs 699 694-2 467 263 s for the same seed under other
conditions) — this chaotic, non-monotonic pattern, plus baseline ALREADY
showing 389 teleports before any optimization is even applied, is
consistent with this specific network+demand combination sitting close to
a genuine micro-simulation congestion-collapse threshold at this time
window, where small signal-timing differences tip individual seeds into
gridlock unpredictably, rather than any one condition being cleanly
"better" or "worse" in a stable sense. This is a PLAUSIBLE explanation,
stated as a hypothesis, not a proven root cause — confirming it properly
(e.g. testing an off-peak window to see if the pattern reverses, checking
whether Webster's isolated-intersection assumption breaks down under
network-wide spillback in this dense a grid) is real follow-up work, not
done here since D2's own scope is the harness + honest measurement, not
diagnosing PFE/demand/network capacity interactions. Flagged explicitly
for whoever picks up D3/D4: do not assume any of these four alternatives
is a safe default without investigating this further first — the
measured baseline currently outperforms all of them at this window.

15 new tests (subprocess command construction and error handling for all
three tool wrappers, `relative_pct`'s zero-baseline guard) plus 2 for
`run_sumo`'s new `net_path` parameter. Full suite: 569 passed, 20 skipped,
0 failed (up from 556). No tracked files changed by the real run (net/tls
outputs, netconvert rebuilds, and the result JSON all live in gitignored
`sumo/`).

**CORRECTED 2026-07-11 — see the consolidated correction section after D3
below.** A measurement-window bug found by external review invalidated
this section's original numbers (contaminated by up to ~55% extra,
out-of-window demand). The fix is applied, the finding was RE-MEASURED
and RE-CONFIRMED (same qualitative result, corrected magnitudes: all four
alternatives disqualified, +101% to +182% vs baseline instead of the
original +145% to +245%) — do not use the numbers in the table above.

### D3. Meso screening feasibility — size M — depends D1
Test whether cheap screening is possible: per-TLS
`<param key="meso.tls.control" value="true"/>` (full-detail static TLS at
selected junctions in meso) and `--meso-tls-penalty`/`--meso-tls-flow-penalty`
on 1-3 junctions near a closure; compare against micro ground truth from D1.
SUMO warns about short (<15 m) approach edges in meso TLS — check ours.
Outcome: either "meso screening correlates, use it for candidate filtering"
or "micro-only, windows stay short". Record either way.

DONE (2026-07-11). Answer: **micro-only — meso screening is NOT feasible**,
measured cleanly and unambiguously. New standalone `signal_meso_screen.py`,
reusing D2's exact 5-condition setup (`signal_optimize.py`'s
`run_tls_cycle_adaptation`/`run_tls_coordinator`/`build_alt_type_net`,
imported directly rather than re-derived) so the micro side of the
comparison is the SAME ground truth D2 already measured, not a fresh
re-derivation that could drift.

**`meso.tls.control` investigated first, before building anything around
it**: this project's own local SUMO 1.27.1 installation has no bundled
C++ source or documentation referencing this key anywhere (checked every
`.xsd`, every `tools/` script, every README — none mention it); the XSD
schema does confirm `<tlLogic>` structurally accepts arbitrary
`<param key=... value=.../>` children, and SUMO silently ACCEPTS the
param with no warning either way when added to a real project TLS and
run — but silence proves nothing (SUMO doesn't warn on unrecognized
`<param>` keys by design, they're a generic extensibility mechanism used
by many unrelated subsystems). Since no independent confirmation of its
real effect could be established, it was NOT relied upon. Used the
network-wide `--meso-junction-control`/`.limited` mechanism instead — this
project's OWN already-deployed, independently-verified configuration
(CLAUDE.md, measured 2026-07-06) — which sidesteps the question entirely
by testing the actual deployed screening capability, not a hypothetical
better one.

**Short-approach-edge check** (PLAN.md's own explicit ask): SUMO did not
print its documented short-edge warning text under this project's real
invocation (checked directly, `--no-warnings` removed) — but the
underlying geometric condition IS real and substantial: **55 of 148
(37.2%) TLS-approach edges are under 15 m**, most near-zero-length (0.2 m)
connector segments from the inner-city node-splitting. Reported as a plain
measurement rather than relying on SUMO's own (silent, in this version) warning.

**The core result**: ran all 5 conditions (baseline/adapted/
adapted_coordinated/actuated/delay_based) in BOTH micro and meso, same
window (07:00-09:00), same 3 seeds, same real whole-day demand D1/D2 used.
Micro reproduced D2's real, large differentiation (389-3 276 teleports,
1.98M-6.82M s total time loss, wildly different across conditions — the
genuine, chaotic near-saturation signal D2 already found). Meso's total
time loss was **essentially IDENTICAL across all five conditions**
(246 395-247 419 s — under 0.5% spread) with **zero teleports in every
single condition**, meaning meso is not merely weakly sensitive to which
TLS program/type is active, it is effectively NON-RESPONSIVE to it
entirely at this demand/window. Spearman correlation between the two
condition rankings: ρ=-0.80 (n=4, p=0.200 — not statistically
significant on its own with so few points, but the raw numbers already
settle the question without needing the correlation coefficient to be
significant: meso simply isn't moving).

**Plausible (not proven) explanation, consistent with the 37.2%
short-edge finding**: `--meso-junction-control.limited` only engages
full-detail control at junctions the model judges close to saturation,
and meso's coarse queue model has very little geometric "room" to
represent real queueing at near-zero-length approach edges — this may be
why so few (functionally zero, at this window) junctions ever cross that
threshold regardless of which TLS program is loaded. Confirming this
properly (e.g. testing full non-limited `--meso-junction-control`
everywhere, or specifically on a handful of long-approach junctions) is
follow-up work, not required here — the practical D3 answer already stands
on its own: don't build a meso-based candidate-filtering step for signal
optimization on this network. Whoever picks up D4 should budget for micro
time on every real candidate, not a meso pre-filter.

10 new tests (`short_tls_approaches`'s edge counting, internal-edge and
non-TLS-junction exclusion, zero-approach-edge degenerate case). Full
suite: 574 passed, 20 skipped, 0 failed (up from 569). One small additive
change to `signal_optimize.run_condition()` first (`micro: bool = True`,
default preserves D2's exact existing behaviour) so this script could
reuse it for both the micro and meso runs instead of a third
reimplementation of the same seed loop. No tracked files changed by the
real run.

**CORRECTED 2026-07-11** — same window-flush contamination as D1/D2. The
qualitative answer ("micro-only, meso does not correlate") is UNCHANGED
and RE-CONFIRMED under the fix; see below for the corrected numbers.

---

## Correction pass on C4/D1-D3 — 2026-07-11

A file named `NEW_CHANGES_REVIEW_2026-07-11.md` appeared in the repo root
mid-session — an independent review from a parallel session on the same
machine (same pattern as `BUG_REVIEW_2026-07-10.md`/
`IMPROVEMENT_REVIEW_2026-07-10.md` earlier). It raised real, specific,
line-numbered findings against the C4/C5/D1/D2/D3 work above. Every claim
was independently verified against the actual code/real data before
fixing anything (this project's established discipline) — some findings
were confirmed and fixed, one was a real methodological point already
covered by pre-existing PLAN.md notes (deferred, not re-litigated), and
several suggested a much larger redesign than the finding itself
justified (deferred with reasoning, matching the same proportionality
judgment already applied earlier in this project to a prior improvement
review).

### Fixed

1. **Window flush contaminated every D1/D2/D3 measurement (the review's
   own top "Act First" item, confirmed exactly).** `run_sumo()`'s
   `--end = duration_s + 3600` flush margin exists for MESO's insertion-
   queue backlog (documented, real, unrelated to this bug) — but
   `signal_lab.py`/`signal_optimize.py` reused the SAME margin for
   BOUNDED time-of-day window experiments, where it does something totally
   different: `--end` doesn't just cap the simulation clock, it also caps
   which vehicles get INSERTED at all, so the flush let vehicles scheduled
   to depart up to an HOUR PAST the requested window still count toward
   it. Verified directly: a nominal 07:00-09:00 experiment was actually
   simulating 07:00-10:00, and 1 886 of 3 419 tripinfo entries (55%) had
   depart >= 09:00. Fixed with a new `flush_s: int = 3600` parameter on
   `run_sumo()` (default preserves every whole-period caller's behaviour
   exactly — `run_scenario.py` main(), `suggest_closure_time.py`); D1/D2/D3
   now pass `flush_s=0`, so `--end` lands exactly on the window boundary.
   A vehicle that departed in-window but hasn't finished by then is
   honestly counted via the EXISTING `unfinished_trips`/
   `unfinished_waiting_trips` guard metrics, not silently padded in or
   dropped. 2 new tests, verified against real demand (`--window-start
   07:00 --window-end 08:00`: 3 419 trips before the fix, 1 533 after,
   exactly matching an independent manual `sumo --end 28800` control run).

2. **`suggest_closure_time.py` coerced missing (`null`) flow data to
   `0.0`, violating CLAUDE.md's own `null != 0` contract, in a place where
   the consequence is exactly backwards** — an edge with NO real data
   would score as the IDEAL (lowest-traffic) window to close it.
   `load_baseline_flows()` now returns `np.nan` for `null`; `proxy_scores()`
   EXCLUDES a candidate window entirely when its closed edge(s) have no
   real data anywhere in the window (reports the excluded count, both to
   stdout and a new `n_windows_excluded_missing_data` result field),
   rather than ranking it as ideal. Corridor coverage is weaker/supporting
   and now handled per-window with `nanmean` (a window missing SOME
   corridor edges still scores from whichever ones have data; only drops
   to closed-edge-only ranking if literally none of the corridor edges
   have data in that specific window) — `rank_candidates()` was rewritten
   to handle per-window (not just per-run) corridor availability, using
   scipy's average-tie rank-rescaling so a window without corridor data
   doesn't get a spurious index-order bias. 7 new tests.

3. **Truncated/dropped vehicle counts were summed across ALL demand
   variants (q50+q10+q90) and reported identically for EVERY seed, even
   though each seed only ever simulates ONE variant.** A seed drawing the
   untruncated-by-much q50 variant was reported as if it also carried
   q10/q90's truncation — overstating affected vehicles roughly by the
   variant count, and making it impossible to tell whether the demand
   realization a seed actually used was itself truncated.
   `simulate_closure()` now tracks truncation PER VARIANT and attributes
   each seed to its own variant's count; `aggregate_seed_metrics()` uses
   MAX across seeds (not mean, not "first seed") for the same reason
   teleports already use SUM — averaging or taking an arbitrary seed can
   hide a real dropped vehicle. The candidate-level total
   (`truncated_vehicles`/`dropped_vehicles` in the UI) sums over the
   DISTINCT variants actually drawn by the run's seeds, not over every
   variant that exists (so an unsampled variant's truncation is correctly
   excluded, and a repeated variant isn't double-counted). 5 new tests
   using a synthetic 3-variant fixture, verified to genuinely discriminate
   against the pre-fix code (re-ran against `git stash`'d old code: tests
   failed with `3 == 2` and `8 == 3`, confirming they catch the real bug,
   not just describing intended behaviour).

4. **`signal_meso_screen.py` reused cached `tls_adapted_*`/
   `tls_coordinated_*`/`net_actuated`/`net_delay_based` artifacts by bare
   filename existence, with no check that the current demand or network
   still matched what they were built from** — independently confirmed
   by 3 of my own 8 parallel self-review agents (see below) as well as
   the external review. A demand recalibration or network rebuild without
   changing `--window-start`/`--window-end` would silently reuse a stale
   artifact. Fixed by factoring the artifact-building logic (previously
   duplicated, and INCONSISTENTLY: `signal_optimize.py` always rebuilt
   unconditionally, `signal_meso_screen.py` cached without any freshness
   check at all) into one shared `build_signal_conditions()` in
   `signal_optimize.py`, keyed by a new content-addressed
   `signal_artifact_label()` that folds in `demand_signature` AND
   `net_fingerprint` — a stale artifact from different inputs now simply
   has a DIFFERENT filename, so it structurally cannot be mistaken for a
   fresh one. Both D2 and D3 now call the identical shared function, which
   also resolves the duplication/inconsistency findings from multiple
   review angles at once. Also added `net_fingerprints_by_condition` (a
   per-condition fingerprint, not just one global one hashing only the
   baseline network) since `actuated`/`delay_based` run against their OWN
   rebuilt network files — a change to just one of those could previously
   go undetected by the single top-level `net_fingerprint` field. 8 new
   tests (label collision/distinctness, cache-hit/cache-miss/rebuild-on-
   label-change, per-condition fingerprint sharing/counting).

5. **Self-review finding (not in the external doc): the D3 degenerate-
   ranking guard was dead code.** `micro_ranks`/`meso_ranks` were computed
   via `order.index(name)`, which is ALWAYS a clean 0..N-1 permutation for
   any list of unique names — so `len(set(ranks)) > 1` was always true
   regardless of whether the underlying `delta_time_loss_s` values were
   actually distinct, meaning the "cannot compute correlation" branch
   could never fire even for genuinely degenerate input (e.g. an upstream
   metrics failure returning the same number for every condition). Also,
   feeding `spearmanr` pre-computed index-ranks instead of raw values
   silently discarded any real TIE between conditions' actual time-loss
   numbers. Extracted into a new pure `condition_correlation()` function
   that correlates on the raw `delta_time_loss_s` values directly (scipy's
   `spearmanr` already rank-transforms with correct tie handling) and
   checks THEIR distinctness for the degenerate guard. 4 new tests,
   including one reproducing the exact bug (identical micro values across
   all conditions used to silently produce a confident-looking rho; now
   correctly returns `None`).

6. **`/api/suggest_closure`'s `duration_hours`/`slide_hours` validation
   let `NaN`/`inf` through.** `float("nan")` and `float("inf")` both parse
   successfully, but neither satisfies `<= 0` (every NaN comparison is
   `False`) nor is obviously invalid at a glance — the existing bound
   check silently passed NaN into the background job, where
   `round(nan * 3600)` raises an unhandled `ValueError` instead of a clean
   400 at the API boundary. Fixed with `math.isfinite()`. 3 new tests.

7. **`suggest_closure_time.py`'s scratch-file cleanup only ran after a
   FULLY successful search.** A SUMO timeout or any exception mid-search
   (baseline run or any candidate) skipped the cleanup block entirely,
   leaving that run's route/edgeData/tripinfo files behind with no
   retention policy. Wrapped the whole baseline-and-candidate procedure in
   `try/finally` so cleanup (or `--keep-scratch` preservation) always
   runs, matching the CLI flag's own stated purpose.

8. **Honesty/provenance labelling made structural, not just prose**
   (the external review's sections 4/5.1/7.4): D1/D2/D3 already had a
   `tls_provenance` field but no machine-checkable field actually
   ENFORCING the "never present a synthetic-TLS result as a
   recommendation" rule — a caller had to parse a caveat string. Added
   `recommendation_allowed: bool` (`False` whenever `tls_provenance !=
   "synthetic"`'s inverse — currently always `False`, since no other
   value exists until D6) to all three. Also de-duplicated a second
   hardcoded `"synthetic"` string literal in `signal_optimize.py` into an
   import of D1's own `TLS_PROVENANCE` constant, so the two can no longer
   silently drift apart when D6 eventually changes it. `suggest_closure_
   time.py` gets an analogous `recommendation_status` field
   (`"insufficient_evidence"` / `"screening_only_weak_correlation"` /
   `"screening_only_correlated"` — DELIBERATELY never `"validated"`, since
   even a strong correlation here comes from a small, non-random,
   selection-biased sample, not a stratified/held-out design) — the web
   UI already communicated this substance correctly via its own Swedish
   message text (verified by reading the actual JS), so no UI change was
   needed, only a structured field for any future programmatic consumer.
   4 new tests.

### Corrected measurements (supersede the numbers in the D1/D2/D3 sections above)

All re-measured against the identical real whole-day demand (96 quarters,
22 841 trips) and identical window/seeds as the original runs, with the
`flush_s=0` fix applied:

**D1** (`--seeds 1` per window):

| Window | Trips | Teleports | Wall time |
|---|---|---|---|
| 07:00–08:00 (60 min) | 1 533 | 6 | 2 s |
| 07:00–08:30 (90 min) | 2 531 | 9 | 4 s |
| 07:00–09:00 (120 min) | 3 419 | 14 | 5 s |

(Sanity-confirmed: the OLD "60 min" run's contaminated real span was
exactly 07:00→09:00 — its old numbers, 3 419 trips/14 teleports, are
IDENTICAL to the NEW correctly-scoped 120-min run above, which is exactly
what the bug predicts.)

**D2** (5 conditions, `--seeds 3`, window 07:00–09:00): the QUALITATIVE
finding is UNCHANGED — all four alternatives still disqualified for
teleports, still worse than the naive synthetic baseline — only the
magnitudes shrank (less contamination volume to begin with):

| Condition | Δ time loss (was) | Δ time loss (corrected) | Teleports (corrected) |
|---|---|---|---|
| adapted | +195.7% | **+110.8%** | 596 |
| adapted_coordinated | +244.9% | **+182.4%** | 967 |
| actuated | +230.9% | **+163.8%** | 1 038 |
| delay_based | +145.0% | **+101.0%** | 631 |

The chaotic per-seed pattern also persists under the fix (seed index 1 is
the best performer for baseline/adapted/adapted_coordinated/actuated but
the worst for delay_based) — consistent with, not an artifact of, the
near-saturation hypothesis already documented in D2's own section above.

**D3** (5 conditions x 2 modes, `--seeds 3`, window 07:00–09:00): the
QUALITATIVE finding is also UNCHANGED — meso's total time loss is still
essentially flat across all five conditions under the fix (180 323-
181 649 s, under 0.7% spread, ZERO teleports in every condition), while
micro shows the same large real differentiation as D2's corrected numbers.
Spearman ρ=-0.63 (was -0.80; still not statistically significant with
n=4, still a non-positive correlation either way) →
**micro-only, meso screening still not feasible.**

### Deferred (real points, disproportionate to fix reactively here)

- **Closure-truncation cohort consistency** (external review section
  3.2): comparing a truncated closure route's shorter timeLoss against a
  baseline where the same trip continues to its original destination is a
  real, already-documented limitation — see PLAN.md's own C3 section
  ("Treat closure comparison as an accessibility-and-delay problem") and
  the equivalent finding already recorded from the OLDER
  `IMPROVEMENT_REVIEW_2026-07-10.md` (its 13.9/13.10). Not new, not
  re-litigated here; a matched-cohort baseline redesign is real future
  work, not a bug fix.
- **Closed-edge integrity metric scoped to the active closure window**
  (section 3.3): a genuinely new capability (per-window throughput
  checking), not a fix to existing code — same disposition as the
  equivalent item already in the older review (its 13.10).
- **Full statistical redesign of C4's proxy validation and D3's
  correlation study** (sections 4, 5.3): stratified/held-out sampling,
  minimum sample sizes, confidence intervals, a multi-day/multi-window
  repeated study for D3. These are legitimate research-rigor asks, but a
  full redesign is disproportionate for a solo summer project's
  exploratory feasibility work — matches the same proportionality
  judgment already applied earlier in this project to a prior improvement
  review's structural suggestions. The honest-labelling fixes above
  (`recommendation_status`, `recommendation_allowed`) already prevent the
  concrete failure mode (a screening result being mistaken for a
  validated one) without the larger redesign.
- **Per-demand-variant signal-plan optimization/cross-evaluation**
  (section 5.2): `tlsCycleAdaptation`/`tlsCoordinator` are built from
  `variants[0]` (q50) only. Real substantial new work, not a fix; flagged
  for whoever picks up D4+.
- **Full topology equivalence gates for alternate networks beyond edge-ID
  equality** (section 5.5): edge-ID equality (27 614/27 614) was already
  verified real for the actuated/delay_based rebuild; a full
  lane/connection/restriction diff tool is new work, not a fix.
- **Job-ID system replacing serve.py's single global status object per
  operation type** (section 7.1): real for a true multi-tab race, but low
  practical risk for a single-operator local tool — same disposition as
  this exact class of suggestion earlier in this project's review
  history.
- **`serve.py` parsing `run_scenario.py`'s stdout to learn the generated
  scenario name** (section 7.3): brittle in principle, but this exact
  mechanism was ALREADY a deliberate, tested fix (2026-07-11, earlier
  today) for a WORSE prior bug (ambiguous manifest matching by
  `closed_edges` alone) — rearchitecting `run_scenario.py`'s output
  contract to a machine-readable result file is reasonable future work,
  not a reactive fix here.
- **Metrics-output filename collisions if these scripts are ever
  parallelized** (section 6.2): real but currently DORMANT — today's
  strictly sequential execution reads each metrics file immediately after
  writing it, before the next condition/seed can overwrite it. Only
  matters if/when someone parallelizes the 5-condition loop (itself
  deferred, see the efficiency findings below); noted here as a
  constraint to address AT that time, not preemptively engineered for now.

### Self-review cross-check

In parallel with reading the external document, 8 background finder
agents (the project's own `code-review` skill, high effort: line-by-line
scan, removed-behaviour audit, cross-file tracer, reuse/simplification/
efficiency/altitude/conventions angles) were run independently against
the same D1-D3 diff. Their findings substantially OVERLAPPED with the
external review (the stale-artifact-caching bug was independently found
by 3 of the 8 angles, plus the external doc — four independent
confirmations of the same real bug) and added: the dead degenerate-
ranking-guard bug (fixed above, not in the external doc), several more
instances of the same duplication-across-three-scripts pattern (window-
loading/validation boilerplate, the 5-condition dict literal, the window-
label expression) not yet consolidated — real cleanup opportunity, left
for a future pass since the HIGHEST-value instance of this duplication
(the artifact-caching logic, where duplication had already caused a
correctness bug) is now fixed — and an efficiency finding (the 5
conditions × N seeds loops in `signal_optimize.py`/`signal_meso_screen.py`
run strictly sequentially; this project has an established flat-pool
multiprocessing pattern for exactly this shape of independent-SUMO-run
workload, unused here) noted as real but not applied, since the current
~2-4 minute wall times are already comfortable for this project's
exploratory, interactive usage pattern.

Full suite after all fixes: 604 passed, 20 skipped, 0 failed (up from
574). No tracked files changed by any of the real re-verification runs.

### D4. Closure + signals combined — size L — depends C2, D2
Two-pass loop: run closure (meso) → extract ACTUALLY rerouted routes
(vehroute output) → optimize signals against those (D2 tools) → evaluate in
micro window (D1). Check whether one iteration stabilizes route choice or
signal changes shift routing enough to need a second pass (measure, decide).
This is the deliverable Gustav described: "when a road closes, how should the
lights adapt".

DONE (2026-07-11). New `signal_closure_combine.py`. **MICRO throughout, not
meso**, despite this section's own literal wording above — deliberate
deviation, not an oversight: D3 (just above) measured that meso does not
execute signal programs meaningfully at all (near-zero time-loss spread
across 5 wildly different TLS conditions), and D4's entire point is signal-
timing quality, so extracting routes from a run whose signals don't matter
and then judging signal quality on them would mix two regimes D3 already
showed disagree.

**Mechanism, empirically grounded before building on it**: probed a real
closure (Skånegatan, edge `60786979_3575001205_0`) with
`--vehroute-output --vehroute-output.exit-times` over real whole-day demand
— confirmed against 139 real rerouted vehicles, zero exceptions, that a
rerouted vehicle's actually-driven route is reliably the LAST `<route>`
child of its `<routeDistribution>` (real `exitTimes`, no
`replacedOnEdge`/`reason` markers; earlier entries are superseded plans kept
for audit only, `probability="0"`). Built `extract_final_routes()` on this
rule. Two additive parameter changes made this possible without duplicating
any existing machinery: `run_scenario.run_sumo()` gained
`vehroute_output: Path | None = None` (adds `--vehroute-output`/
`.exit-times`, returns a `"vehroute"` key alongside the existing tripinfo/
statistics/summary trio — every existing caller's return value is exactly
unchanged, verified via `metric_paths or None` and the full test suite);
`signal_optimize.run_condition()` gained the same parameter, applied only to
the run's first seed (one representative seed/variant, matching how D2's own
`tlsCycleAdaptation`/`tlsCoordinator` calls already only use `variants[0]`)
— return shape unchanged, so D2/D3's existing calls are untouched.

**Pipeline**: build the closure additional file + truncate stranded-vehicle
demand variants (same `run_scenario.write_closure_additional`/
`truncate_stranded_vehicles` C2/C4 already use — no third reimplementation)
→ Pass 1 = closure + the deployed BASELINE synthetic signals, window-bounded
micro (`run_condition`, `flush_s=0`, same D1-D3 discipline), captures metrics
+ one seed's vehroute → `extract_final_routes()` → D2's
`run_tls_cycle_adaptation`/`run_tls_coordinator` run against the EXTRACTED
post-closure routes (not the original pre-closure demand — the whole point)
→ Pass 2 = same closure/window/seeds with the newly-optimized signals →
`closure_metrics.compare_metrics()` for before/after, plus a new
`route_stability()` comparing Pass 1 vs Pass 2's captured routes (PLAN.md's
own "measure, decide" instruction — reported, not looped into an open-ended
convergence search; the script always runs exactly two passes).

**Real measured result — HONEST, not cherry-picked, and mixed**: same
closure, two window/seed sizes.
- 07:00-08:00, 1 seed: Pass 1 timeLoss=303 434 s (1 533 trips, 5 teleports) →
  Pass 2 timeLoss=259 473 s (5 teleports) — **-14.5%**, but still
  DISQUALIFIED (teleports present in both passes, so `compare_metrics` never
  reports this as a clean win regardless of the timeLoss direction — the
  disqualification-aware scorecard doing exactly its job). Route stability
  99.9% of 1 227 common vehicles identical between passes.
- 07:00-09:00, 3 seeds (the project's own default window): Pass 1
  timeLoss=869 205 s (37 teleports: 1 jam/4 yield, max_queue=144) → Pass 2
  timeLoss=998 265 s (108 teleports: 43 jam/22 yield/12 wrongLane,
  max_queue=357) — **+14.8% WORSE**, also DISQUALIFIED, and visibly more
  congested by every guard metric (more unfinished trips, 2.5× the queue
  peak). Route stability still 99.9% (2 996/2 999 common vehicles
  identical) — the routes barely moved, so the regression is a genuine
  signal-timing effect, not a routing artifact.

**Reading this honestly**: tlsCycleAdaptation.py's Webster-style cycle/
green-split recalculation, tuned against ONE representative seed's extracted
post-closure flow, does not reliably generalize to the full seed/direction-
variant mix it then gets evaluated against — sometimes it helps, sometimes
it measurably worsens congestion (more jams, much deeper queues), and which
one happens is not obvious in advance from the smaller/cheaper test alone.
This is a genuine capability limit of the off-the-shelf optimizer under
closure-induced demand redistribution, not a bug in this script (verified:
same truncated demand variants and closure file feed both passes; only the
signal additional files differ between them). Anyone using this for a real
recommendation should run it at the actual seed count they intend to trust
and read `route_stability`/teleport counts, not just the headline Δ.

8 new unit tests (`extract_final_routes`'s routeDistribution/plain-route/
mixed/no-route cases, `route_stability`'s identical/changed/
routeDistribution-on-both-sides/no-common-vehicle cases) — matching D1-D3's
established style of unit-testing only the pure-logic pieces and verifying
the SUMO-invoking parts by running them for real. Full suite: 615 passed, 20
skipped, 0 failed (up from 607). No tracked files changed by the real runs
(`--out` pointed at a scratch path for both verification runs; intermediate
`d4_*` files are cleaned up by the script itself, confirmed empty after
each run).

### D5. UI + provenance — size L — depends D2 (+D4 for combined)
"Optimera signaler" action per scenario (async start/poll like C5), result =
before/after metric card + per-junction plan diff (cycle/splits/offsets),
signal-provenance label rendered wherever signal results are shown.

DONE (2026-07-11). Every bullet implemented, verified against a REAL live
serve.py through a real headless-Chrome CDP session (zero console
errors/exceptions on either path) — not just unit-mocked.

- **New shared `signal_lab.tls_plan_diff()`** (D1's module, imported by both
  D2 and D4): pure XML parsing/diffing, no SUMO invocation — baseline
  (deployed net.net.xml's netconvert --tls.guess synthetic 90 s cycle) vs
  the optimized program (tlsCycleAdaptation.py's cycle/phase-duration
  recalculation, tlsCoordinator.py's offset merged on top — coordinator
  only ever writes offset, never phases, so its file is merged onto the
  matching adapted entry rather than parsed as a second cycle source).
  Per junction: cycle before/after + Δ%, offset before/after,
  `max_split_change_pct` (only computed when both programs have the SAME
  phase count — tlsCycleAdaptation.py rescales durations, it does not
  add/remove phases — else reported `null` rather than compared against a
  phase list of different meaning at the same index). 6 new unit tests.
  Wired into both `signal_optimize.py`'s (D2) and
  `signal_closure_combine.py`'s (D4) own result JSON as `tls_plan_diff`,
  reusing each script's ALREADY-BUILT `adapted_path`/`coordinated_path`
  (no second tlsCycleAdaptation/tlsCoordinator run). Verified against real
  leftover D2/D3 artifacts before wiring it in: 57 real junctions, e.g. a
  90 s guessed cycle collapsing to a 24 s Webster-computed one at a
  low-volume junction (-73.3%), a real 366.78 s coordination offset — sane,
  legible numbers.
- **`serve.py`**: `/api/optimize_signals?edges=` (start, edges optional) +
  `/api/optimize_signals/status` (poll), sharing `_sim_lock` with
  `/api/close`/`/api/recalibrate`/`/api/suggest_closure` (same batch-of-
  real-SUMO-runs resource class), own `_optimize_lock`/`_optimize_state`.
  `edges` empty/absent (the currently loaded scenario has no closure)
  dispatches to D2's plain `signal_optimize.py`; edges present (an active
  closure) dispatches to D4's `signal_closure_combine.py` instead — same
  fixed 07:00-09:00/3-seed MICRO window server-side for both, NOT exposed
  in the UI (deliberate scope narrowing, same reasoning C5 already used
  for top_k/extra_bad/seeds: sane defaults beat a cluttered advanced-
  options form for a solo research tool). New
  `summarize_signal_optimization(result, closure)` gives the frontend ONE
  uniform shape regardless of which script actually ran — D2's and D4's
  result JSONs have genuinely different internal layouts (5 named
  conditions vs a fixed 2-pass before/after) but the UI needs the same
  "before/after card + plan diff + provenance" either way. Found while
  writing it: D2's own schema has no explicit `is_disqualified()` field on
  its baseline condition (only `comparisons_vs_baseline`, computed against
  the CANDIDATE) — the summary function derives it directly from
  `before`'s own metrics (teleports/dropped_unreachable), matching
  `closure_metrics.disqualification_reasons()`'s rule, so both schemas
  expose `before_disqualified`/`after_disqualified` uniformly rather than
  D2's side silently defaulting to `false`. 18 new serve.py tests (6 pure-
  function tests for `summarize_signal_optimization`, 12 for the async
  start/poll/lock-sharing/error-path lifecycle, mirroring the existing
  `TestSuggestClosure` pattern).
- **Web UI**: new "⚡ Optimera signaler" button in the Simulering panel —
  no picking mode, unlike "+ Ny avstängning"/"🕐 Föreslå tid": it acts on
  WHICHEVER scenario is currently loaded (`scen-select`'s own
  `closed_edges`, already available from the scenario manifest fetched
  before the panel is ever shown), the same way "Byt dag" acts on the
  currently loaded day without needing a map selection first. Results
  render in a new floating panel (`#optimize-results`, own id — the
  content shape, a metric card plus a per-junction table, differs from
  `#suggest-results`' ranked-window table): a before/after card (time-loss
  minutes, trip counts, disqualification flags, Δ%), a provenance/caveat
  banner rendered UNCONDITIONALLY (not just on a bad result — PLAN.md's
  own requirement that a signal-provenance label appear wherever signal
  results are shown), route-stability and truncated/dropped-vehicle lines
  when a closure is active, and the full per-junction plan-diff table
  sorted client-side by `|cycle_delta_pct|` descending (biggest changes
  first — the far more useful reading order than the backend's
  tls_id-sorted list).
- **Real end-to-end verification** (headless Chrome + CDP, matching the
  project's established browser-testing discipline — pytest doesn't cover
  frontend JS): two real runs against a live, unmocked `serve.py`, each
  polled to completion and screenshotted via `innerText` extraction, zero
  console errors/exceptions on both.
  - No-closure path (baseline scenario active): dispatched to
    `signal_optimize.py`, 175 s wall time, rendered "FÖRE 15643 min ·
    3394 resor · diskvalificerad → EFTER 44168 min · 3308 resor ·
    diskvalificerad", "FÖRÄNDRING +182.4%", "diskvalificerad: teleports",
    "Medianförändring cykeltid: -73.3% över 57 korsningar", 57 plan-diff
    rows, the "INTE en rekommendation" caveat — every number matches the
    underlying JSON exactly (`total_time_loss_s`/60, `relative_time_loss_pct`).
  - Closure path (`close_60786979_3575001205_0.json` scenario selected via
    the real dropdown): dispatched to `signal_closure_combine.py`, 70 s
    wall time, rendered "avstängning: Skånegatan" in the title, "FÖRE
    14487 min → EFTER 16638 min", "+14.8%", "19 förkortade, 0 borttagna
    fordon", "Ruttstabilitet: 99.9% av 2999 jämförbara fordon" — matching
    D4's own already-documented mixed real finding (this exact closure/
    window/seed combination measurably WORSENS congestion, not a
    regression introduced by D5's wiring).
  - No tracked files touched by either run (`sumo/` is gitignored; the
    server writes to `sumo/signal_optimize_web.json` /
    `sumo/signal_closure_combine_web.json`, never into
    `web/data/scenarios/`) — confirmed via `git status` after both runs.
    Chrome and serve.py processes torn down afterward.

Full suite: 639 passed, 20 skipped, 0 failed (up from 615 before this step
— 24 new: 6 for `tls_plan_diff`, 18 for `serve.py`'s new endpoint/summary
function).

**PLAN.md's Phase D (traffic-signal optimization) is now fully complete —
D1 through D5 all done.** Only D6 remains, and it is external (blocked on
the city delivering real signal plans via Miroslaw) — nothing left to
execute autonomously in this arc.

### D6 (external, unblocks honesty upgrade). Real signal plans from the city
Ask via Miroslaw/city contacts for: signal-object ↔ intersection ↔ SUMO TLS-ID
mapping, phase diagrams, cycle/green/offset per time-of-day plan, detector
logic, bus priority. When delivered: import layer replacing the 65 guessed
programs, re-enable full junction control in meso (CLAUDE.md's stated
condition), and flip provenance to `city-configured`.

RESEARCHED 2026-07-11: before doing anything else, checked whether D6's own
ask could be answered from public sources instead of waiting on the city.
It cannot — checked `data.goteborg.se`, `goteborg.se/psidata`, and the
Teknisk Handbok's own traffiksignaler page directly: no public dataset of
OPERATIONAL signal programs exists (the open-data portal covers air
quality, bike infrastructure, bridges, parking, water levels, permits,
traffic cameras — nothing on signal control), and the handbook explicitly
routes such requests to Stadsmiljöförvaltningen, not a public register.
Trafikverket's NVDB is road INFRASTRUCTURE data, not municipal signal
control. This confirms D6's own "external" framing was correct — this
part still genuinely needs Miroslaw.

DONE (partial, 2026-07-11), a different and real question answerable right
now: not "what ARE Gothenburg's real signals doing" (needs the city) but
"what is every real Swedish signal LEGALLY REQUIRED to do" — Sweden's
binding national regulation for traffic signals, Transportstyrelsens
föreskrifter **TSFS 2014:30** (read in full, all 14 pages:
https://www.transportstyrelsen.se/tsfs/TSFS%202014_30.pdf), which specifies
real, citable, legally-binding minimums:
  - kap 2 § 11: red+yellow ("röd+gul") shown 1.5 s before every green phase.
  - kap 2 § 12: yellow shown 4 s where the speed limit is <60 km/h, 5 s
    where ≥60 km/h.
  - kap 2 § 14: green shown ≥4 s (vehicle signals).
  - kap 2 §§ 2-10 ("Separering i tid"): inter-green clearance time
    t_s = (s_out + l_f)/v_out − s_in/v_in > 0, using a standardized speed
    table (§9: vehicles 8/10/12/14/15 m/s for posted limits 30/40/50/60/
    70 km/h) and a default vehicle length (§10: 6 m).

**Checked our own deployed net.net.xml against this BEFORE building
anything**, and found a real, measurable gap: yellow phases were only ever
3.0 s or 5.0 s across all 57 real TLS junctions with any yellow phase at
all — the 3.0 s ones violate the ≥4 s minimum for any street under
60 km/h, which is nearly every inner-city approach; only 3 of ~330 phases
across 65 real `<tlLogic>` elements have ANY red-red clearance interval,
and there is no red+yellow transition anywhere. netconvert's guess is not
merely unlabelled, it is measurably non-compliant with the law every real
Swedish signal must follow.

**New `signal_regulation.py`**: parses net.net.xml's real per-connection
geometry (approach-edge lane speed, and — the key move that keeps this
grounded in real data rather than more guessing — the REAL internal-
junction lane length SUMO already computes for every connection, used
directly as the clearance formula's s_out distance) and rebuilds every
TLS's phase list: floors green to ≥4 s, resizes yellow per the speed-limit
rule, and inserts a computed all-red phase plus the mandatory 1.5 s
red+yellow warm-up before every transition into a green phase. Writes one
content-addressed `<additional>` file (`programID="reg"`, cached by
net_fingerprint ALONE — this depends only on geometry, never demand/
window) that SUMO activates via the exact same "last-loaded program wins"
mechanism D2's tlsCycleAdaptation output already relies on (re-verified
directly via TraCI here too: `getProgram()` returns `"reg"`) — no new
activation machinery needed. Every number in the module docstring is
directly cited to a TSFS paragraph; every SIMPLIFICATION (s_in=0 as a
deliberately conservative choice since it only ever grows t_s; s_out taken
as the MAX internal-lane length among a phase's clearing links rather than
resolved per exact conflicting pair; v_out the MIN — slowest, most
conservative — speed bucket; all-red shown before red+yellow rather than
the two requirements being assumed to overlap) is documented separately
from the citations so a future reader can tell law from engineering
judgement.

**A real bug found by testing, not assumed away**: the first version
classified any phase containing a 'G' character as a pure green phase,
even when it ALSO contained a 'y' for a different, actually-clearing link
(a legitimate SUMO construct for asymmetric ring designs where one
movement flows continuously while another clears) — such a phase would
have silently skipped ALL yellow-time/clearance handling for its own
clearing link. A test using a constructed mixed-state phase caught this
before it shipped; fixed by checking `"y" in state` first, `is_green` only
when `y` is absent. Verified this was not merely a hypothetical worry: it
changes the real, deployed rebuild for 2 of the network's 65 actual
junctions (`25658722`, `26025184`), both of which have a continuously-
green link mixed into a yellow-clearing phase — without the fix those two
junctions would have silently gotten NO yellow-time correction or
clearance insertion at all.

**Wired into D2 as a sixth condition**, `regulation_compliant`
(`signal_optimize.build_signal_conditions`), evaluated by the exact same
MICRO harness as baseline/adapted/adapted_coordinated/actuated/
delay_based, and automatically inherited by D3's meso-screening
comparison and available wherever D2's conditions dict flows (D5's UI
picks `baseline`/`adapted_coordinated` specifically by key, so this
addition doesn't change D5's rendering). Given its own distinct, more-
grounded-but-still-not-real provenance, added a new
`tls_provenance_by_condition` field (`"synthetic_regulation_compliant"`
for this one condition, `"synthetic"` for the other five) rather than
letting the file's single top-level `tls_provenance` field imply they're
all equally arbitrary.

**Real measured result** (07:00-08:00, 1 seed, real demand): baseline
timeLoss=308 593 s (6 teleports) → regulation_compliant timeLoss=317 896 s
(6 teleports, same as baseline) — **+3.0%**, a small, plausible regression
consistent with what adding real clearance/warm-up safety margins should
do (less green capacity per cycle in exchange for legally-mandated safety
time), not a sign of something broken. Still DISQUALIFIED under the
existing scorecard rule (baseline itself already carries 6 teleports, so
`compare_metrics` flags the comparison regardless of which side moved) —
an honest artifact of the disqualification rule being baseline-relative
here, not a claim that regulation-compliant timing itself causes new
instability (teleport count is UNCHANGED from baseline).

**Still explicitly NOT `city-configured`**: this grounds our synthetic
baseline in real, binding Swedish law instead of an arbitrary uniform
guess, but it is still not Gothenburg's actual deployed signal plans —
cycle length, phase count, and time-of-day variation remain netconvert's
own geometric guess, only the TIMING MINIMUMS are now legally grounded.
The city-data half of D6 (signal-object↔TLS-ID mapping, phase diagrams,
real cycle/green/offset plans, detector logic, bus priority) remains
external and blocked on Miroslaw, exactly as before.

22 new unit tests (`signal_regulation.py`'s speed-bucket/yellow-time
lookup tables, `rebuild_phases`'s green-flooring/yellow-resizing/
all-red-and-redyellow-insertion/continuously-green-preservation logic,
`parse_tls_link_geometry`'s connection/lane parsing against constructed
fixtures, `build_regulation_compliant_tls` end-to-end on a small fixture
net) plus 1 new `build_signal_conditions` test confirming the new
artifact caches by net_fingerprint independent of label. Full suite: 662
passed, 20 skipped, 0 failed (up from 639). No tracked files changed by
the real verification runs (`sumo/` is gitignored).

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
- ~~Outer subprocess timeouts...~~ FIXED (2026-07-11, commit `c48732a`,
  found independently via an external improvement-review pass and verified
  real with a live child-spawns-grandchild reproduction before fixing):
  `serve.run_in_new_session()` — `Popen(start_new_session=True)` +
  `os.killpg(SIGKILL)` on timeout — now wraps all three job subprocess
  sites (`/api/close`, recalibration's `build_sumo_demand`, and its
  baseline `run_scenario` call). `TestRunInNewSession.
  test_timeout_kills_the_grandchild_too` proves the fix (and that plain
  `subprocess.run(timeout=)` demonstrably does not).
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

---

# Phase E-K — Execution plan for PROGRAM_IMPROVEMENT_PLAN_2026-07-13 (written 2026-07-13)

The strategic goals live in `PROGRAM_IMPROVEMENT_PLAN_2026-07-13.md` (7
phases) and the findings backlog in `FULL_CODE_AUDIT_2026-07-12.md`
(P0-1…P1-12 + the 2026-07-13 addendum, whose own 5 findings are already
FIXED in commit 62a1584). This section turns those into the same kind of
concrete, independently-executable steps Phases A-D used. Sizes: S ≤ half a
day, M ≈ a day, L = multi-day.

## Phase E — Immutable, health-gated runs (improvement plan Phase 1; audit P0-1, P0-2, P0-4)

### E0. Stale-number hygiene — size S — DONE 2026-07-13
CLAUDE.md quoted the PRE-fix LOSO ratios (0.830/0.896/2.410). Replaced with
the honest post-fix baseline (min 0.05 / median 0.78 / max 1.95, 2026-07-13,
see DESTINATION_BIAS_RESEARCH §7) including the 1076-fold caveat. Standing
rule: any number quoted anywhere must be reproducible by the current
pipeline — improvement plan Phase 3.1's "replace stale LOSO numbers
everywhere" applies to prose, not just loso_report.json.

### E1. Run registry — size L — GATE for E2-E4
`runs/<run_id>/` per demand build and per scenario: manifest.json written
BEFORE launch (command, argv, source commit, net_fingerprint,
demand_signature, SUMO version, seeds, variant list, expected outputs),
status flipped atomically running→succeeded/failed. run_id =
content-hash of inputs + short random suffix. All generated route/edgeData/
metric/vehroute/result files land inside. `sumo/` becomes scratch-only.
Keep a `latest` PONTER file per product (demand, baseline scenario) that
names a run_id — the web manifest reads through it. This is audit P0-1's
fix and the precondition for safe parallelism (improvement plan Phase 5.4).

### E2. Publish-after-validate — size M — depends E1 (audit P0-2)
serve.py's recalibration currently deletes every scenario JSON before the
new baseline exists. Invert: build into the new run dir, run the validation
gates (E3), THEN atomically switch `latest` and prune. On failure the
previous active run stays untouched and the UI says why the new one was
rejected.

### E3. Per-seed health gate — size M — depends E1
Emit per-seed: loaded/inserted/arrived/running/waiting/teleports/route
errors/ignored + the existing structural gates (calibrated_structure).
Publication REFUSES (not warns) when: vehicle conservation fails, any seed
died, teleports exceed threshold, or a structure drift flag fires. Thresholds
derived from the current healthy baselines and written into the manifest so
a regression is diffable.

### E4. Durable jobs — size M — depends E1 (audit P0-4; supersedes the 4 per-type dicts)
One jobs table (JSON file per job under runs/jobs/): id, kind, args, pid,
pgid, status, log path, started/finished. `/api/jobs/<id>` +
`/api/jobs/<id>/cancel` (killpg, then status=cancelled). The 4 existing
status endpoints become thin views over it; a server restart re-reads job
files and reconciles against live pids.

## Phase F — Truthful individual-car playback (improvement plan Phase 2; audit "P0 - Individual-vehicle simulation is misleading")

### F1. Trajectory provenance — size M
Store seed + demand variant in the trajectory artifact; UI labels playback
"representativ körning (seed 1000, q50)" whenever road colours are the
3-seed Monte Carlo mean. Reconcile trajectory vehicle count against that
seed's health report; fail the artifact on mismatch.

### F2. Unfinished/queued vehicles — size M — depends F1
Vehicles still running/waiting at scenario end must appear (parked/queued
state), not silently vanish — same honesty rule as closure truncation.

## Phase G — Scientific revalidation (improvement plan Phase 3; partially DONE)

### G1. LOSO 1076-fold investigation — size M — no dependencies
The honest post-fix LOSO has fold 1076 at 0.05 (median 0.78). Hypothesis
(documented, unproven): the old 0.83+ recovery there was artifact-powered.
TEST IT: decompose sensor 1076's measured daily flow by what the OTHER
sensors' constrained routes imply across it (a) pre-fix pipeline (git
checkout the candidates generator at 51ad47f~1), (b) post-fix. If (a)'s
recovery collapses when the near-sensor-terminating shapes are excluded
from its implied flow, the hypothesis is proven and goes in the doc; if
not, the fix genuinely lost corridor continuation and the cap slack
(DEST_GROUP_CAP_MULT / conditional-sampling acceptance) needs revisiting.
Either way: one number, one paragraph, no hand-waving.

### G2. Purpose-route compatibility monitoring — size S — DONE in 62a1584,
keep as a gate: `purpose_route_compatible` per vehicle + demand-level
diagnostic. Add its threshold to E3's publication gate when E3 lands.

### G3. Per-purpose validation report — size M — depends E1
One report per build (improvement plan 3.2): GEH, held-out recovery,
candidate→calibrated drift, onward-after-last-sensor, sensor passages,
purpose×time allocation vs prior, purpose-route compatibility. Most metrics
exist (calibrated_structure, agents summary) — this step is assembling them
into runs/<id>/validation.json + a UI surface.

### G4. Local diary data request — EXTERNAL
Ask (via Miroslaw, same channel as D6) whether RVU Västra Götaland
microdata or an regional OD matrix is available for research use — would
upgrade PURPOSE_LENGTH_SCALE from national-ratio partial pooling to local
estimation. Until then the disclosed shrinkage stands.

## Phase H — Demand architecture refactor (improvement plan Phase 4; audit P1-4)

### H0. Review-found cleanup backlog — size S-M — no dependencies, safe any time
From the 2026-07-13 reuse/simplification review of e591bed..HEAD (each
verified against the working tree by the reviewer):
1. The two-pass structure-guard block in `_run_pfe_interval_job` is
   character-identical in build_sumo_demand.py (~:1102) and validate_sim.py
   (~:122), maintained by hand — extract to
   `pfe.solve_interval_with_structure_guard(...) -> (sol, rung)` so LOSO
   can never silently calibrate under a different guard policy than the
   deployed pipeline (the exact validated-vs-shipped mismatch class fixed
   twice already).
2. `GEO_PATH` midpoint/sensor parsing duplicated in
   `_route_structure_metrics` and `structure_groups_for_shapes`; the
   near-sensor predicate exists 3× with the 200 m radius parameterised in
   one copy and hard-coded in another — one cached
   `load_edge_geometry()` + a shared `NEAR_SENSOR_RADIUS_M` constant, so
   the enforced PFE cap and the drift-flag metric can't decohere.
3. The conditional-acceptance outbound-leg body is ~20 lines repeated 3×
   (I-I/E-I/I-E loops in generate_sensor_anchored_trips) — extract a
   `_draw_conditioned_outbound(...)` helper (return legs genuinely differ,
   keep those per-category). This is the statistical core of the
   destination-bias fix; three copies invite category-divergent drift the
   aggregate proximity guard can mask.
4. Delete dead `sample_anchor_and_far_end` (+ its 4 tests) — zero
   production callers, and the conditional-sampling design structurally
   cannot use its API. (`via_naturally_on_path` is ALIVE — E-E path.)
Non-issues, measured by the same review (do NOT "optimize" these): geojson
re-parse is 0.02 s ×3/build; the per-vertex edge-length loop is ~0.3 s/build;
`natural_sensor_masks` is 0.125 ms/call (~10-30 s of the ~100 s generation
budget — an anchor-keyed mask cache is the lever IF multi-day/more sensors
ever make it dominant).

### H1. Split build_sumo_demand.py — size L — depends E1 (do AFTER E, not before)
Modules: intake (dates/windows), candidates, bounds/priors, calibration,
feedback, publication. One orchestration path (the 62a1584 single-path fix
is the seed of this). Typed artifact schemas (demand_meta, health,
manifest) with versions — audit P1-10.

### H2. PFE benchmark fixture — size M (audit P1-3)
A deterministic realistic fixture (fixed candidates + targets) with recorded
runtime/GEH/rungs/structure metrics; CI-style check that a solver change
stays within tolerance. Protects against the next "optimization" silently
changing results.

## Phase I — Measured performance (improvement plan Phase 5) — ONLY after E+H
Timing breakdown already exists (timings_s). Candidates for measurement:
natural_sensor_masks per-try cost in generation (~100 s stage), the
two-pass PFE re-solves (534 s stage), geojson re-parsing (3× per build).
Rule: profile first, optimize the dominant stage only, benchmark fixture
(H2) must stay green.

## Phase J — Product & security hardening (improvement plan Phase 6; audit P0-3, P1-5, P1-6, P1-12)
POST + auth for mutating endpoints; CSP; innerHTML → safe DOM; job-centric
UI views reading E4's durable jobs. Explicitly LAST among the local work:
the server is loopback-only today (documented), so this blocks shared
deployment, not current research use.

## Phase K — Real-data signal upgrade (improvement plan Phase 7 = old D6 external)
Unchanged: blocked on city signal plans via Miroslaw. When they arrive:
import layer, flip provenance to city-configured, re-enable full meso
junction control, revisit signal scores per audit P0-5's ExperimentProtocol
(warm-up/measurement/admission/completion windows) — which should be
designed together with the import, not before it.

## Execution order
E0 (DONE) → E1 → {E2, E3, E4} → F1 → F2 ∥ G1 (independent of E) →
G3 → H1 → H2 → I → J. K external. G1 and H0 can start any time — neither
needs new infrastructure; G1 is two controlled pipeline runs and honest
arithmetic, H0 is verified-safe cleanup.
