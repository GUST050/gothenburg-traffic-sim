# Speed Architecture Plan — Demand Day-Library and Pre-Warmed Horizon

*Frozen 2026-07-21. Owner: Gustav. Research and drafting: Claude (this
document's every measured number and code claim was verified against the
repository at commit `4606d95`; file:line references included so any claim
can be re-checked). Read together with IMPROVEMENT_PLAN.md "Speed research
2026-07-18" — this plan EXTENDS that lever list (A1/A2 below are its
levers 1 and 4/benchmark path, completed to the day-library architecture),
and inherits its constraint verbatim: **results are not allowed to get
worse.***

## Status (updated 2026-07-21)

| Stage | State |
| --- | --- |
| A1 per-quarter parallel publish | **LANDED** `6045d34`. Byte-identity proven through the production orchestration on a real pool (`validation/a1_publish_identity_v1.json`) + regression tests in both suites. The running 40h/3mo search picks it up from its next envelope build. Golden-build byte comparison (§6.1) still owed once `sumo/` frees. |
| A2 parallel SUMO seeds | **CODE LANDED, GATE OPEN to 3** `f054ed0`/`4e657f4`. The monthly runner overlaps a candidate's observations but yields them in canonical order and truncates at the first hard failure, so evidence cannot change; equivalence is unit-tested. `benchmark_seed_workers.py` ran on an idle machine — serial vs 3 workers byte-identical `result.json`, 1.50×, peak 1.02 GiB — and `validation/a2_parallel_seed_benchmark_v1.json` now approves `--seed-workers` up to 3. |
| B calendar-date seeding + day library | **BUILT, PROVEN, HELD-OUT-VALIDATED on branch `speed-stage-b`; OWNER CHOSE NOT TO MERGE YET (2026-07-22).** All code in: four day-local streams + per-day pools, L1/L2 library as the only multi-day path (now gzip-stored, ~10×, year ≈ 13 GB), generation guard, kill-safe live-release guard covering `web/data/scenarios`. §6.2 golden A/B **PASSED at full scale** (`validation/b_golden_ab_v1.json`: Mon–Sun, cold 1580 s vs warm 32 s, six artifacts byte-identical). §6.3 first failed by the letter, so a **held-out v3 on B demand was run and PASSED all five gate checks with margin** (`validation/b_heldout_v3_campaign.json`: recall 1.0, regret 0.0, failure-recall 0.722 ≥ 0.6). BUT it re-used the weak v2 set: `median_spearman` +0.894→−0.976 is noise inside the 300 s band (only 1/7 cases has any objective spread), which re-confirms the set cannot test ranking discrimination. **Owner decision: do NOT merge, do NOT warm, until a STRONGER v3 set — cases with true objective spread > 300 s — is designed, frozen, and passed.** That design is the next work item. |
| C pre-warm job + horizon UI | **`warm_demand_horizon.py` written + tested** (3 window shapes/ISO week cover every composition; resumable; live-release guarded). Not run — gated behind the merge decision above. Serve.py `/api/horizon` + L4 view artifacts still to do. |

## 0. Goal, stated precisely

Make closure-search and simulation-viewing latency drop from hours to
minutes **without any change in what a result claims or how accurate it
is**, by eliminating recomputation — never by approximating.

Target end state (measured baseline → target):

| Operation | Today (10-core laptop) | Target warm | Mechanism |
| --- | --- | --- | --- |
| Cold 3-month/40 h search | ~20 h | ~4–5 h once per horizon | day-library build is the cold cost |
| Search against warm horizon | — | **20–40 min** | SUMO finalists only |
| Repeat search, same date region | — | **10–20 min** | + baseline cache hits |
| "Byt dag" (view a date) | ~6–7 min | **≤40 s** | assembly + one 3-seed meso run |
| Browse pre-run dates | — | **instant** | pre-built scenario artifacts |
| Close a road interactively | ~40 s | ~40 s (unchanged) | already fast |

What can NEVER become minutes on this machine: the one-time cold build of
a full horizon (arithmetic floor: ~230 build-days × 288 quarter-variants
× ~3 s ÷ 10 cores ≈ 5.5 h of solver work for a 3-month search;
~30–36 h for a full year). The design amortizes that cost once, in the
background; it does not pretend it away.

## 1. Invariants — things this plan must not change

1. **No solver change.** The per-quarter IPF/LP solve, its iteration
   order, tolerances and the numba kernel are untouched (IMPROVEMENT_PLAN
   forbids approximation; measured 91–96 % of demand time).
2. **No fidelity change.** Meso stays meso; 3 seeds stay 3 seeds;
   q10/q50/q90 stay separate epistemic variants; every existing hard gate
   (GEH, structure guards, purpose provenance, closure integrity, health)
   runs unchanged.
3. **One code path.** The library assembly MUST BE the production build
   path, not a parallel implementation that could drift. "Monolithic
   window build" becomes "build missing days, then assemble" — there is
   no second way to produce a window.
4. **Content-addressed everything.** No cache is keyed on less than the
   full input fingerprint (Phase 7 rule). A code or input change
   invalidates exactly the affected layer, automatically.
5. **Fail closed.** A missing/corrupt library entry triggers a rebuild of
   that day, never a silent fallback to different data.
6. **The live release stays protected.** All library work happens in
   `runs/` + a new `runs/demand-days/` tree; the snapshot/restore guard
   around `sumo/` and `web/data` stays mandatory.

## 2. Verified code facts this design rests on

Each fact below was read from the code, not assumed. If any of these is
later found false, STOP and re-derive — they are the load-bearing walls.

- **F1. Calibration quarters are independent.** No warm start, RNG,
  cross-quarter accumulator or shared solver state ties quarters together
  (verified for the flat parallelization, 2026-07-09, and re-confirmed:
  `demand/calibration.py:run_pfe_variants_flat_parallel` solves
  (variant × quarter) tasks in one unordered pool). Midnight continuity
  is a SUMO property (vehicles crossing midnight inside one sim), NOT a
  calibration coupling. → Per-day calibration is mathematically the same
  computation as windowed calibration, GIVEN identical per-day inputs.
- **F2. Per-quarter inputs are per-date functions today, except the
  candidate pool.** Targets index `flows`/`flows_forecast` by absolute
  quarter (`qi_start + i`); bounds/priors/corridor priors use
  slot-of-day + the fixed structural reference (2025-09-16); activity
  purpose margins use calendar day-type
  (`demand/intake.py:activity_purpose_shares_for_window`); the
  assignment-prior scale is fit on the FULL-YEAR 2025 daily means
  (`assignment_priors.py:521-546` — window-independent, verified
  2026-07-21).
- **F3. The candidate pool is the ONLY window-coupled input.**
  `build_candidates.py:2606-2620` seeds each day block with
  `np.random.default_rng(seed + day_index)` where `day_index` is
  window-relative, and route GEOMETRY is generated once per day-type
  (`pool_key` weekday/weekend) by the FIRST block of that type in the
  window, then reused (`build_candidates.py:2967-2981`). Consequence:
  the same calendar day gets different candidate draws in different
  windows, and a window's weekday geometry depends on which day the
  window starts on. **This is what B changes** (§4).
- **F4. The screening proxy never reads candidate pools.**
  `screen_monthly_closures.py:build_screening_artifact` consumes
  forecast/reference/geo/registry/direction/assignment/priors/
  observability/network only. → B does not move a single proxy rank.
- **F5. SUMO determinism is by file bytes + seed.** run_scenario invokes
  sumo with explicit `--seed`; per-vehicle stochastic attributes are
  drawn at load in file order. Identical route-file bytes ⇒ identical
  simulation. The plan therefore requires assembly to be byte-identical
  to the (post-B) monolithic writer — guaranteed by Invariant 3 (same
  code path), and additionally PROVEN once by a golden A/B (§6.2).
- **F6. Measured stage costs** (real 11-day envelope build, 2026-07-21
  log `runs/search_40h_3mo.log`): candidates 122 s; interval solving
  2 659.6 s (3 168 tasks, 10 workers, 0.84 s/task); route publish
  3 × ~2 190 s (per-quarter integer repair + purpose allocation + XML,
  serial per variant — parallel across variants since `4606d95`);
  whole PFE stage 9 219 s. Baseline single-day numbers for viewing:
  run_scenario whole day, 3 seeds, audits + trajectories = **13.8 s**
  (IMPROVEMENT_PLAN speed table).
- **F7. Storage reality.** One day of demand ≈ 25 MB q50 XML + ~7 MB
  agents, ×3 variants ≈ ~95 MB raw, ~10 MB gzipped. A full year ≈ 35 GB
  raw / ~4 GB compressed. Disk free: 219 GB. Scenario view artifacts
  (flows + bounded trajectories) ≈ 10–30 MB/day.

## 3. Architecture: five content-addressed layers

```
L0 geometry templates   per (day_type, structure_fp, seed)   — NEW canonical
L1 day demand           per (date, source, variant, L0_fp, inputs_fp)
L2 window assembly      per (start_date, days, source)  = concat of L1 days
L3 SUMO baselines       per (L2 archive digest, variant, seed)   — exists today
L4 view artifacts       per (date, L2/L3 fp)  scenario JSON + trajectories
```

- **L0 (new):** weekday/weekend/holiday route geometry generated ONCE per
  day type from a canonical seed derived as
  `rng(seed_base XOR sha256(day_type))` — no longer dependent on which
  window asks first. Key includes the full structural fingerprint
  (network, DeSO/POI endpoints, RVU parameters, generator source hash).
- **L1 (new):** each calendar day's calibrated demand: per-quarter solved
  + integer-repaired + purpose-allocated vehicle lists, stored
  DAY-LOCAL (departures 0–86 400 s, day-local vehicle ids), plus the
  day's fit report (GEH, infeasible count, structure metrics). Key
  includes: date, source, L0 key, targets fingerprint (the exact
  flows/forecast slice), bounds/priors/margins fingerprints, solver
  source hash. Departure draws seeded by
  `rng(seed_base + days_since_epoch(date))` — the calendar-date seeding.
- **L2:** a window build = fetch/build its L1 days → shift departures by
  `day_index*86400` → renumber ids sequentially in depart order → write
  route XML + agents + assembled `demand_meta.json` whose per-day fit
  sections come from L1. This IS `build_sumo_demand.py`'s only path
  afterwards (Invariant 3). Assembly cost: XML serialization only,
  seconds to ~2 min for long windows.
- **L3:** unchanged mechanism (`monthly_sumo.py` baseline cache), now hit
  far more often because L2 digests repeat across searches.
- **L4 (new, optional but cheap):** the pre-warm job also runs the
  standard 3-seed baseline scenario per day and stores the web artifacts,
  so Simulering-browsing is pure playback. Never replaces the live
  `web/data/scenarios/` contract — a date is ACTIVATED by copying from
  L4 through the existing validate-then-publish gate.

## 4. The one results-affecting change, isolated and named

**B-seeding: every per-day stream becomes day-local.** Implementation
(2026-07-21) found FOUR window-relative streams, not the two §2's F3
described. All are the same family — same generator, same distributions,
different stream position — and all had to move for a day to be reusable:

1. candidate **departures**, seeded `seed + day_index` → keyed by calendar
   date (`build_candidates.day_block_seed`);
2. route **geometry**, taken from whichever block of a day type came first
   in the window → canonical per day type
   (`build_candidates.day_type_template_seed`);
3. the writer's **endpoint draw ordinals**, running continuously across
   midnight → restarted at each day boundary (`pfe.write_calibration_report`
   `day_quarters`);
4. the **intra-quarter departure scramble**, keyed by the absolute quarter
   index → keyed within the day.

Statistically neutral, numerically different. Everything else in this plan
is bit-identical by construction. Callers with no date and no day structure
(single-day builds, sub-day windows, LOSO folds, tests) keep the old
behaviour exactly, so only multi-day windows move.

A fifth consequence is structural rather than random: the PFE solves every
quarter over the whole shape pool, so a day's result depends on which
day-type geometry pools the window contains. That is why `pool_composition`
is part of a stored day's identity (`demand/day_library.DayIdentity`) —
the same date calibrated in a weekday-only window and beside a weekend is
two different, equally correct entries. The alternative, always pooling
every day type, was rejected: it would roughly double the variable count
(and the solve cost) of weekday-only envelopes to buy key simplicity.

Consequences, handled honestly:

1. Every new build after B has a new fingerprint (correct: content
   changed). Old archives stay valid for their old searches. **Mixing
   old- and new-seeded archives inside ONE search is NOT currently
   refused** (re-verified 2026-07-21: `validate_demand_archive` checks
   the spec contract, which is content-addressed WITHOUT generator
   source hashes — so a `prepare` in flight across B's landing could
   mix, since each envelope build is a fresh subprocess importing new
   code). Stage B therefore adds a guard: the multi-envelope release
   manifest records each archive's candidate-generator source hash and
   refuses a release whose entries disagree. Additionally, B lands only
   while no search is running (already required for other reasons).
2. The golden releases and benchmarks must be RE-FROZEN on B-seeded
   builds (protocol §6). The old goldens remain as historical evidence.
3. The held-out v2 gate: proxy ranks are untouched (F4); SUMO outcomes
   shift within Monte-Carlo noise. The gate record therefore remains
   evidence for the proxy MECHANISM, but we do not merely assert this —
   §6.3 spot-checks it empirically before any UI claim continues.

## 5. Implementation stages (each with a stop gate)

**Stage A1 — per-quarter parallel publish (result-identical, no
prerequisites).** Parallelize integer repair + purpose allocation across
quarters inside each variant (they are per-quarter independent, F1/F6);
XML written serially from precomputed per-quarter vehicle blocks in
deterministic order. Proof: byte-identical calibrated*.rou.xml +
.agents.json + fit reports vs current code on one single-day and one
multi-day golden case. Expected (honest arithmetic, not best case:
3 variants × 1 056 quarters × ~2.1 s ÷ 10 cores ≈ 11 min + serial XML
writes): publish 37 min → **~10–12 min** on an 11-day build; big
envelope build ~85 → **~60 min**. *Can land immediately; running
searches pick it up from their next envelope build (each build is a fresh
subprocess).*

**Stage A2 — parallel SUMO runs.** Enable `seed_workers > 1` (the CLI
deliberately blocks it pending a resource benchmark — that benchmark is
this stage). Runs are separate processes with disjoint output files and
own seeds; determinism is per-run. Gate: golden monthly benchmark re-run
with workers=1 vs workers=N gives identical per-run metrics and
result.json; peak RSS within budget. Expected: SUMO stage 4–5 h → ~1–1.5 h.

**Stage B — calendar-date seeding + L0/L1 library + L2 assembly.**
Order inside the stage:
 1. L0 canonical geometry + L1 writer/reader with full fingerprints.
 2. Rewrite the multi-day path of `build_sumo_demand.py` to
    build-missing-days-then-assemble (single-day = window of 1: same
    path). Delete no old code until the golden A/B passes.
 3. Golden A/B (§6.2), then re-freeze goldens, then the §6.3 spot-check.
Gate: all of §6 passes. Until then, B stays on a branch — the running
production path is untouched.

**Stage C — pre-warm job + L4 + UI.** A resumable background CLI
(`warm_demand_horizon.py --source forecast --from 2027-01-01 --to
2027-12-31`) building L1 days (and optionally L4 view artifacts) with the
live-release snapshot guard around any shared-path writes; nice/low
priority; safe to interrupt anywhere. serve.py gains
`/api/horizon/status` (coverage map) and "Byt dag" becomes: L1 hit →
assemble + run baseline (≤40 s) or L4 hit → publish instantly through the
existing staging gate.

**Explicitly deferred:** LOSO lever 2 (not on this path; do after `sumo/`
frees up). Shortlist-policy slimming (governance change → held-out v3).

## 6. Proof protocol (nothing ships on argument alone)

1. **A1/A2 identity:** byte-comparison of all published artifacts (A1)
   and per-run metric + result.json equality (A2) on golden cases,
   recorded in `validation/` like every prior speed change.
2. **B assembly identity:** build one 11-day window BOTH ways with
   B-seeding — (a) directly, (b) forced through cold L1 + assembly — and
   require byte-identical route XML, agents and meta fit sections. This
   is a one-time proof that Invariant 3 holds in practice; afterwards a
   permanent regression test does the same on a 2-day fixture.
3. **B statistical neutrality:** re-run 3 of the 12 held-out v2 cases
   (one pruning ranking case, one fallback case, one failure-only case)
   on B-seeded demand. Required: same practical-winner recall, same
   hard-failure classifications, paired deltas within the frozen 300 s
   equivalence of the old outcomes. Any violation ⇒ full held-out v3
   before any global-best claim continues; record the outcome either way
   in `validation/b_seeding_neutrality_v1.json`.
4. **Golden re-freeze:** normal, closure, 7-day and monthly goldens
   rebuilt on B, with the same wall/RSS/disk benchmark discipline.

## 7. Failure modes designed against

| Risk | Design answer |
| --- | --- |
| Library entry corrupt/stale | sha256 per artifact in an L1 manifest; mismatch ⇒ rebuild that day (fail closed, Invariant 5) |
| Code change makes library silently wrong | source hashes inside every L1/L0 key ⇒ old entries simply never match |
| Two writers race on one day | per-day lockfile + atomic rename, same pattern as the run registry |
| Pre-warm clobbers live release | same snapshot/restore guard already proven in monthly_demand.py, plus L1 writes live only under `runs/demand-days/` |
| Disk fills | pre-warm checks free space, stores gzipped, and is prunable by key age; year ≈ 4 GB compressed (F7) |
| Assembly drifts from monolith | impossible by construction (one code path) + §6.2 regression test |
| Old vs new archives mixed in one search | existing backend/source-digest provenance checks fail closed (verified) |
| SIGKILL mid-build bypasses restore | known open item (task list): move the snapshot marker to disk so the NEXT start restores; folded into Stage C |

## 8. Alternatives investigated and rejected (so they are not re-litigated)

- **GPU / fastmath / solver tolerance / warm-starting q10 from q50:**
  change numerical results → forbidden by the precision constraint.
- **ML surrogate replacing SUMO runs:** precision loss by definition;
  the project's own rule is that only held-out-validated screening may
  narrow the field, and final claims stay SUMO-verified.
- **One continuous full-year SUMO baseline sliced per envelope:** not
  equivalent — paired comparison requires baseline and candidate to share
  the envelope's initial conditions (empty network at scenario start).
- **Per-day demand dedup WITHOUT B-seeding:** not result-identical (F3);
  rejected as a silent approximation.
- **Skipping pilots / shrinking the shortlist:** precision unchanged but
  governance-gated (policy is golden-frozen and held-out-validated);
  possible later via a v3 cycle, deliberately out of scope here.

## 9. What the user experience becomes

- Browse any pre-warmed date in Simulering: instant.
- Change to any un-warmed date: ≤40 s.
- Close a road on the viewed date: ~40 s (unchanged).
- "Bästa arbetsperiod" over months against a warm horizon: 10–40 min,
  dominated by honest SUMO evidence on the finalists.
- First-ever search on a cold horizon: hours, once, visibly labelled as
  library warm-up with per-day progress — never silently slow.

---
*Review note: this document was itself reviewed against the code twice
before freezing; the three claims most likely to be wrong (window-coupled
pool seeding F3, assignment-scale window-independence F2, proxy pool
independence F4) were each verified by direct code reading, not memory.*
