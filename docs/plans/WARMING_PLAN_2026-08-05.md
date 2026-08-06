# Warming — plan and operations card

**Date:** 2026-08-05, corrected 2026-08-06 · **Status:** NOT RUNNING, and no
annual unit has ever run. Supersedes Stage 5 of
`CLOSURE_INTEGRITY_PLAN_2026-08-05.md`, which was the warming half buried in a
closure plan.

Delete or archive this file when the run is finished and reconciled.

---

## 0. What is running

**Nothing.** This section said "population RUNNING" from 2026-08-05 until it
was corrected on 2026-08-06. It was never true: the log named below
(`runs/annual-warm-logs/2027-20260805-234645.log`) stops after the first
demand build for the 2027-01-01 window, and the progress store holds no
completed unit.

The keys quoted here were also stale in every direction. For the record, four
different plan keys have appeared in project documents:

| source | key |
| --- | --- |
| this file, before the correction | `8c1c681b…` |
| this file, "was" | `de071336…` |
| `TASKS.md` ACTIVE_TASK | `9cc823d3…` |
| `validation/annual_warm_preflight_v1.json` | `de071336…` |
| **actually current on 2026-08-06** | `8540680061febd86…` |

None of the first four validates. Do not copy a plan key into a document —
compute it (`python3 tools/plan_annual_warming.py --verify`) at the moment
you need it. Every source edit invalidates it again, and the fixes landed on
2026-08-06 touched `pfe.py`, `build_candidates.py` and
`tools/populate_annual_warming.py`, all three of which are bound sources.

The intended invocation, once the plan is regenerated:

```bash
python3 tools/plan_annual_warming.py --write
python3 tools/populate_annual_warming.py --execute --state-workers 3
```

| | |
| --- | --- |
| state requests | 104,685 (34,895 checkpoints × 3 variants) |
| demand builds | 367 three-day windows over 365 days |
| disk floor | ~55.8 GiB, DERIVED from selectable work (see §3a) |
| approved state workers | 8 (`approved_seed_workers()`, measured on this host) |

Everything finished is durable (SQLite + content-addressed store). Completed
units are skipped on resume; `pending`, `running` and `failed` are re-run. The
job is safe to interrupt.

### 3a. The disk gate is derived, not a constant

`TASKS.md` recorded the run as `BLOCKED_ON_192_GIB_DISK_PREFLIGHT`. There is
no 192 GiB constant anywhere in the tree, and there has not been since
`required_free_bytes()` replaced the flat whole-year threshold. The live gate
is

```
pending_units x 432 KiB + 2 x 326 MiB + 4 GiB + 8 GiB  ~=  55.8 GiB
```

which the stored preflight agrees with (`minimum_free_bytes =
59,877,867,520`). Free space on 2026-08-06 was 172 GiB. **The recorded
blocker was not real.**

---

## 1. THE CONSTRAINT — read before editing anything

`run_scenario.py` is a **bound input** of the annual plan, along with
`build_sumo_demand.py`, `build_candidates.py`, `demand/structure.py`,
`traffic_sim/simulation/warm_state_cache.py`, `monthly_sumo.py` and 43 others.

`_verify_plan_source_seal()` runs at **every demand-group boundary**. Editing
any bound source while the run is in flight aborts it there — deliberately, so
a half-old half-new bank can never exist.

Consequence for the closure work in `CLOSURE_INTEGRITY_PLAN_2026-08-05.md`:

| stage | touches a bound source? | safe during the run |
| --- | --- | --- |
| 1 — measure the leak/teleport mechanism | no (read-only) | **yes** |
| 2 — rerouter reach sweep | yes (`REROUTER_RADIUS_M`) | no |
| 3 — teleport policy | yes (`run_scenario.py`) | no |
| 4 — v10 selection precondition | no (`tools/`, `validation/`) | **yes** |

So during the run: do Stage 1 and Stage 4. Stages 2–3 wait, or the run is
deliberately killed and restarted from its durable progress after they land.
Regenerating the plan afterwards is cheap; the *bank already built* is what
would be at risk, and it is not — completed units survive a new plan only if
the plan key matches, so a source edit means the bank restarts. **That is the
real cost of editing mid-run: the units already built, not the run.**

---

## 2. What the run is expected to cost, and the open measurement

Measured before this run (per 3-day build):

```
demand build   ~332 s   solver/PFE, saturates every core
state units    ~252 s   chain-bound to 3 of 10 cores
               ------
total          ~584 s   x 367 builds = 59.5 h
```

`d88a42f` now overlaps the next demand build with the current group's state
units. If it works, per-build wall time should fall toward `max(332, 252)` =
~332 s rather than their sum, i.e. **59.5 h → ~34 h** at perfect overlap, and
realistically 40–46 h once the state workers and the solver contend for cores.

**This run is the measurement.** No separate A/B arm is needed: the baseline
above is known, so per-build wall time from this run's own log answers it.

**Revert gate:** if the projection lands above ~50.6 h — less than 15% saved —
revert `d88a42f` rather than carry scheduling complexity for nothing.

### How to read the projection

```bash
LOG=$(cat runs/annual-warm-logs/latest-path.txt)
python3 tools/populate_annual_warming.py --status --state-workers 3 | python3 -m json.tool
```

Divide elapsed wall time by `succeeded` to get seconds per unit, and by the
number of completed demand groups to get seconds per build. Compare the latter
against 584 s.

---

## 3. Why 3 workers and not 8

`approved_seed_workers()` permits 8. This section used to add that
`record_annual_warm_preflight.py:68` "hard-requires exactly 3, so raising it
needs a new preflight record" — that was wrong twice over, and both halves
were fixed on 2026-08-06:

- **Nothing on the execute path reads that record.** Its only consumer is
  `tools/freeze_annual_warm_readiness.py`. `--execute` re-derives everything
  through `production_preflight()`, whose worker gate is
  `approved_seed_workers()` alone. So the record never constrained the run.
- **The tool could not have recorded another value anyway.** `main()`
  hardcoded `state_workers=3` and the validator rejected anything else, so
  the "new preflight record" the sentence asked for was unobtainable without
  a code edit. It now takes `--state-workers` and validates it against the
  approved count. Its `recorded_date` was likewise the frozen literal
  `2026-08-04`, written *and* required — a record made on any other day
  certified itself as two days old. It now records the real date.

More importantly, raising it alone would have done nothing: within one demand
archive only 3 units are ever dependency-ready, one per q10/q50/q90 chain,
because a checkpoint cannot start before its predecessor is durable. The cap
was never the worker count — it was the one-archive-at-a-time loop feeding it.
That is what `d88a42f` addresses, and it is why the earlier "3 → 6 saves ~13 h"
note in the old ops card was wrong.

If §2's measurement shows the overlap working, the *next* lever is running two
demand groups' units concurrently (6 ready units), which needs both a new
preflight record and a memory benchmark: only ~1.6 GiB was reclaimable under
load during the v9 campaign, against the 7.3 GiB the old card measured.

---

## 4. Item 3 — per-unit overhead, deliberately not designed yet

Measured 2.65 s per state unit against a 1.09 s/step floor for chained SUMO
with no TraCI, no artifact packaging and no store writes. So roughly 1.5 s/unit
is not simulation.

Candidate targets, none verified:

- the TraCI connect loop (`WARMING_SPEED_REVIEW` reports it polls up to 40× at
  50 ms),
- gzip level on state artifacts,
- store write + restore-validate on every unit,
- `WARM_OUTPUT_PRECISION = 16` output volume.

**Do not implement any of these until this run has been profiled.** Optimising
an unmeasured cost is how a change ships that buys nothing, and every one of
these edits a bound source (§1) so it cannot land mid-run anyway.

---

## 5. The bigger warming lever — MERGED 2026-08-06, this section was stale

`speed-stage-b` — per-day demand, collapsing the 2.99× window redundancy (367
builds for 363 days). Already built: 23 commits, day library, window assembly,
gzipped storage, warm-horizon CLI, and a **full-scale golden A/B that passed
byte-identical** (`41a5195`).

It takes the demand half from 33.8 h to ~11.3 h — worth more than everything
in §2–§4 combined.

**CORRECTED 2026-08-06.** This section said stage B was "unmerged by owner
decision (`efd3230`)". That was true when written and is not true now:

```
efd3230  2026-07-22  Record owner decision: hold B unmerged pending a stronger v3 set
9591bc7  2026-08-06  Merge stage B: the demand day library          <- MERGED
56414bd  2026-08-06  Regenerate the annual warm plan on stage B demand
```

`9591bc7` is an ancestor of HEAD, `demand/day_library.py` is present, and
`use_day_library` is ON by default for whole-day windows — which is exactly
what every annual demand build is (`begin 00:00`, `end 24:00`). The annual
plan itself was regenerated on stage B demand.

Leaving the stale text here caused a real error: a later estimate assumed
each of the 367 windows recalibrates all three of its days and projected
~61 h. The plan's own structure refutes that —

```
demand builds             367
day-slots across windows  1087
DISTINCT calendar days     363
redundancy factor         2.99x
```

— and the day library is what collapses 1087 day-calibrations to 363. Any
estimate must count DISTINCT DAYS, not window-days. See §2 for the corrected
projection.

---

## 6. After the run

Population grants neither release nor activation rights. The artifacts need
their own completeness review, and product activation is a separate gate.

Do not warm and claim in the same step.
