# Warming — plan and operations card

**Date:** 2026-08-05 · **Status:** population RUNNING. Supersedes Stage 5 of
`CLOSURE_INTEGRITY_PLAN_2026-08-05.md`, which was the warming half buried in a
closure plan.

Delete or archive this file when the run is finished and reconciled.

---

## 0. What is running

The 2027 annual warm population, started 2026-08-05.

```bash
python3 tools/populate_annual_warming.py --execute \
  --state-workers 3 --plan-key 8c1c681b63f08c132dc084233460670ae09b52b3076fa236cb145f3f999cd758
```

Log path is in `runs/annual-warm-logs/latest-path.txt`. Root is
`runs/annual-warm-2027/8c1c681b.../`.

| | |
| --- | --- |
| plan key | `8c1c681b63f08c13…` (was `de071336ab0e0c5d…`) |
| state requests | 104,685 (34,895 checkpoints × 3 variants) |
| demand builds | 367 three-day windows over 365 days |
| free disk at start | 155.6 GB against a 59.9 GB floor |
| approved state workers | 8 (running at 3 — see §3) |

Everything finished is durable (SQLite + content-addressed store). Completed
units are skipped on resume; `pending`, `running` and `failed` are re-run. The
job is safe to interrupt.

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

`approved_seed_workers()` permits 8 and `record_annual_warm_preflight.py:68`
hard-requires exactly 3, so raising it needs a new preflight record.

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

## 5. The bigger warming lever, still held

`speed-stage-b` — per-day demand, collapsing the 2.99× window redundancy (367
builds for 363 days). Already built: 23 commits, day library, window assembly,
gzipped storage, warm-horizon CLI, and a **full-scale golden A/B that passed
byte-identical** (`41a5195`).

It would take the demand half from 33.8 h to ~11.3 h — worth more than
everything in §2–§4 combined. It is unmerged by owner decision (`efd3230`)
pending a held-out set strong enough to test ranking discrimination.

v9 is now that set in all but one gate check (`median_spearman` +0.945 against
the v3-era −0.976), so **Stage 4 of the closure plan is what unblocks this**.
Integration cost measured: 9 conflicted files.

---

## 6. After the run

Population grants neither release nor activation rights. The artifacts need
their own completeness review, and product activation is a separate gate.

Do not warm and claim in the same step.
