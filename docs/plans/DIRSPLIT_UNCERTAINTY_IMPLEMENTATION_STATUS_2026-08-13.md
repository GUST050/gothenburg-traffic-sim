# Implementation status — dirsplit uncertainty plan

Companion record for
`DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md`. The plan document
itself is left byte-unchanged; this file records what was built against it,
what was measured, and what remains blocked.

**Date:** 2026-08-13
**Branch:** `claude/architecture-review-docs-7c2as4`
**Status:** steps 0–8 implemented. No SUMO campaign executed; no outcome
artifact exists.

---

## Step status

| Step | Scope | State |
|---|---|---|
| 0 | Freeze + language boundary | **Done** — `dirsplit/schema.py`, `dirsplit/legacy.py` |
| 1 | `training_table_v2` | **Done** — `dirsplit/dataset_schema.py`, `dataset_v2.py` |
| 2 | Model tournament | **Done and RUN** — `dirsplit/models.py`, `evaluate.py` |
| 3 | Calibration + applicability | **Done** — `dirsplit/calibrate.py`, `evidence.py` |
| 4 | Joint day scenarios | **Done** — `dirsplit/scenarios.py`, `scenario_evaluation.py`, `ensemble.py` |
| 5 | Case-addressed demand | **Done** — `demand/cases.py`, `demand/intake.py` seam |
| 6 | Case ⊥ seed | **Done** — `traffic_sim/core/simulation_members.py` |
| 7 | Risk policy, API, UI contract | **Done (shadow, default off)** — `uncertainty_policy.py`, `uncertainty_presentation.py`, `serve.py` |
| 8 | Registration + gates | **Done, UNEXECUTED** — `uncertainty_gate.py`, frozen registration |

**Tests:** 315 new, all passing. Regression profile against the pre-work
commit `687f19f` is byte-identical (42 failures in 6 files, all pre-existing
— see "Pre-existing failures" below).

---

## Two findings from implementation

### 1. The shipped q10/q90 arms violate the pair-sum identity

Found while writing the legacy adapter, recorded in full in
`dirsplit/legacy.py`. `dirsplit/predict.py` writes:

```
edge_shares_q10 : e0 -> s10 , e1 -> 1 - s90
edge_shares_q90 : e0 -> s90 , e1 -> 1 - s10
```

so the pairs sum to `1 - (s90 - s10)` and `1 + (s90 - s10)`. A directed pair
splits ONE measured two-way total, so this is an identity, not a fitted
quantity.

At sensor 107 — the only two-way station in the registry, and the only place
the split touches a level-1 target — `build_targets` multiplies the measured
count by each share. With the tracked median interval width of 0.107 the q10
arm therefore calibrates against targets summing to ~89.3% of the measured
total and the q90 arm to ~110.7%.

**This is the mechanism behind the plan's own audit observation** that the
outer arms change total network loading: the tracked route files hold
19,845 / 20,836 / 21,749 vehicles, a 9.6% spread matching the 10.7% interval
width. The plan measured the symptom; this identifies the cause. It is a live
violation of plan invariant 1 by the currently shipped artifact.

Repaired in step 4: `scenarios.apply_block` derives the partner as `1 - s`,
so the identity holds by construction. Pinned by
`test_the_legacy_construction_would_fail_this_check`.

### 2. The model tournament selects 50/50

Step 2 was run against the tracked `data/dirsplit/training_table.csv`
(1,648 two-way rows after dropping 12,050 one-way rows, oriented toward
centre). Evidence:
`validation/dirsplit_point_benchmark_v1.json`.

| Model | leave_city_out | leave_station_out |
|---|---|---|
| `similarity_weighted_lgbm` | **−16.7%** CI [−0.0027, +0.0335] | **−29.1%** CI [+0.0137, +0.0434] |
| `shrunk_dfactor` | +0.3% (CI straddles 0) | +0.3% (CI straddles 0) |
| `beta_binomial_dfactor` | +0.3% (CI straddles 0) | +0.3% (CI straddles 0) |

**Winner: `constant_5050`.** The deployed boosted model is significantly
*worse* than an even split on leave-station-out — its paired CI lies entirely
above zero — and the two hierarchical models tie.

This agrees in sign with the deployed trainer's own tracked
`train_report.json` (domain leave-city-out: Oslo −12.5%, Bergen −27.9%,
Stavanger −15.6%, Trondheim +1.6%), so two independent measurements point the
same way.

**`CLAUDE.md:418` still reports "Oslo +11.1%", which the tracked report
contradicts (−12.5%).** It also reports λ=0.256 where the tracked report says
0.289. Both need correcting; this work did not edit `CLAUDE.md`.

**Caveats, recorded in the artifact:** this is the v1 aggregate, so
day-to-day variation was already averaged away, `blocked_date` folds are
impossible, and only 8 bootstrap blocks exist (4 cities × 2 day types) — the
CIs are correspondingly coarse. The interval metrics on this table describe
spread between aggregated station-hours, **not** between days, and are not
calibration evidence. Re-running on `training_table_v2` once volumes are
fetched is what makes the interval question answerable.

---

## What is deliberately NOT done

**No SUMO campaign was executed.** The environment has no SUMO, no `traci`,
and the plan's step 8 execution requires explicit approval for an expensive,
evidence-producing run. `validation/closure_uncertainty_shadow_outcome_v1.json`
does not exist, and `test_the_frozen_artifact_is_present_and_unexecuted`
fails the suite if one appears without a real run.

**`test_dates` and `test_edges` are frozen EMPTY** in the registration.
Outcome-blind selection has not been performed; filling them in after any
measurement would void the pre-registration. The registration must be
re-frozen as v2 once a versioned outcome-blind rule has drawn them.

**v2 is opt-in and off by default.** `DIRSPLIT_V2_SHADOW=1` enables shadow
mode; `production_default` remains `legacy_q10_q50_q90`. Step 6 is additive:
`ScenarioSpec` and `canonical_seed` are untouched, so every frozen golden and
resume test keeps its exact bytes.

**Steps 5–7 provide the contracts, not a full rewiring of the monthly search.**
`monthly_search.py`, `monthly_sumo.py`, `finalist_decision.py`,
`pilot_selection.py`, `independent_daily.py`, `deterministic_disruption.py`
and `closure_ranking.py` still use the legacy q-key path. The v2 types they
would migrate to exist and are tested; migrating them is a separate change
that will alter frozen goldens and should be done deliberately, with the
golden updates as its visible diff.

---

## Pre-existing failures (not caused by this work)

Verified by running the identical test selection at commit `687f19f` in a
clean worktree. The profile is byte-identical:

| File | Failures |
|---|---|
| `test_monthly_demand.py` | 14 |
| `test_warm_state_population_semantics.py` | 13 |
| `test_benchmark_speed.py` | 7 |
| `test_pfe_kernel.py` | 5 |
| `test_monthly_warm_state_residual_v2.py` | 1 |
| `test_benchmark_persistent_sumo.py` | 1 |

Several are instances of the frozen-contract drift documented as P5 in
`docs/reviews/ARCHITECTURE_REVIEW_2026-08-13.md`: contracts fingerprint the
whole of `run_scenario.py`, which changed on 2026-08-10.

---

## Suggested next actions

1. **Correct `CLAUDE.md:418`** (Oslo +11.1% → the tracked −12.5%; λ 0.256 →
   0.289). The next reader will otherwise re-derive the old conclusion.
2. **Use the city's measured D-factor at sensor 107** (52/48 for 2025). It is
   Level 1 by the project's own hierarchy, it is available now, and 107 is the
   only station where the split touches a level-1 target.
3. **Fetch volumes and run `dirsplit.dataset_v2`**, then re-run the tournament
   on the v2 table. That is what turns the interval question from unanswerable
   into measured.
4. **Decide the step-2 exit.** If `constant_5050` wins again on the v2 table,
   the direction axis has no signal to model and steps 4–8 lose most of their
   subject matter for that axis — while the case⊥seed and common-random-number
   work in step 6 remains valuable regardless.
5. Only then consider migrating the monthly search onto the v2 contracts.
