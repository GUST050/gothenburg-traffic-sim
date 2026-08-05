# Closure integrity and warming speed — researched plan

**Date:** 2026-08-05 · **Author:** Claude (Opus 5) · **Status:** plan, nothing
implemented from it yet. Items 1–2 of the warming half are already committed
(`d88a42f`, `1de1cf5`); everything below is proposed work.

Read with `SPEED_ARCHITECTURE_PLAN_2026-07.md` (stage A/B/C, still authoritative
for the demand day-library) and `WARMING_SPEED_REVIEW_2026-08-03.md`.

---

## 0. What forced this plan

The v9 held-out campaign finished 2026-08-05 and **failed on exactly one gate
check** out of seven:

```
practical_winner_recall            1.0    (floor 0.9)  PASS
discriminating_practical_winner    1.0    (floor 0.9)  PASS
p90_normalized_shortlist_regret    0.0    (ceil 0.1)   PASS
failure_disqualification_recall    0.79   (floor 0.6)  PASS
discriminating_case_fraction       0.5    (floor 0.4)  PASS
all_shortlists_contain_eligible    true                PASS
ranking_case_fraction              0.4    (floor 0.5)  FAIL
median_spearman                   +0.945
```

The evidence quality is the best this project has produced — `median_spearman`
was **−0.976** on the v3-era set, the number that made that campaign
uninterpretable. The near-sensor band, the minimum in-window flow floor and the
vehicle-hours objective did what the 2026-07-27 owner decision asked for.

It fails only because **three of five cases had zero eligible schedules**:

| case | eligible | in-window support |
| --- | --- | --- |
| v9-control-tertiary-2 | 11/15 | 182 |
| v9-discriminating-tertiary-a | 3/15 | 17 |
| v9-control-secondary-3 | 0/15 | 57 |
| v9-control-tertiary-1 | 0/15 | 27 |
| v9-discriminating-tertiary-b | 0/15 | 154 |

61 schedules disqualified, every one of them for
`active_closure_edge_throughput` and/or `teleports`. Two rankable cases; the
gate needs three.

This is not only a campaign problem. It is the same mechanism behind the
user-visible fact that **the 🚧 Stäng väg button hard-fails on a large share of
the map**, and it decides whether closure results can be trusted at all.

---

## 1. Root cause, established rather than assumed

### 1.1 The leak and the teleports are the same event

SUMO's teleport, [documented behaviour](https://sumo.dlr.de/docs/Simulation/Why_Vehicles_are_teleporting.html):
a gridlocked vehicle is *removed from the network and moved along its route*,
with reinsertion attempted at each subsequent edge.

A vehicle stuck at the mouth of a closed edge is therefore relocated **along its
own route — through the closure** — and SUMO's `edgeData` records an `entered`
count on the closed edge. `active_closure_throughput()` reads exactly that.

So `active_closure_edge_throughput` and `teleports` are not two independent
failures. They are one phenomenon counted twice, which is why they co-occur in
15/15 schedules of the failing cases. `CLAUDE.md` already recorded this
("sumo's stuck-vehicle cleanup was forcibly relocating it PAST the closure");
this plan takes it as the primary cause rather than an incident note.

### 1.2 Two hypotheses tested and REFUTED

Recording these so they are not re-litigated:

- **"The closure omits `allow`/`disallow`."** The
  [Rerouter docs](https://sumo.dlr.de/docs/Simulation/Rerouter.html) state that
  a `closingReroute` *without* those attributes lets vehicles with no
  alternative "simply continue on their old route and effectively ignore the
  closing". That would be a perfect explanation — but `run_scenario.py:1431`
  already writes `<closingReroute id="..." disallow="all"/>`. Refuted.
- **"Vehicles that entered legally before the closure are miscounted."**
  `active_closure_throughput()` counts only fully-contained 15-minute buckets,
  specifically to avoid that. Refuted by its own docstring and by the fact that
  entries occur mid-window.

### 1.3 The contributing cause: rerouter reach

`REROUTER_RADIUS_M = 400` (`run_scenario.py:1347`). A rerouter only re-plans a
vehicle when it *passes one of the rerouter's own edges*. A vehicle approaching
a busy corridor from beyond 400 m never touches one, so it keeps a route through
the closure, arrives, cannot enter (permissions are genuinely revoked), queues,
and teleports.

That radius was chosen deliberately — `CLAUDE.md` records that global rerouters
were a performance bottleneck. It is a speed/correctness trade-off that has
never been re-measured, and the near-sensor band moved the campaign onto exactly
the busy edges where it bites.

### 1.4 Why it hits busy edges specifically

`--time-to-teleport` defaults to **300 s**. On a low-flow far-field edge a
handful of vehicles clear before that threshold; on a near-sensor arterial the
queue does not. Production never sets this option — the only two places in the
tree that set `--time-to-teleport -1` are
`tools/diagnose_warm_state_*_semantics.py`, i.e. diagnostics, not the closure
path.

---

## 2. Options, with the trade-off stated honestly

| # | Change | Fixes | Cost / risk |
| --- | --- | --- | --- |
| A | Widen `REROUTER_RADIUS_M` | the cause (1.3) | runtime; the reason it is 400 |
| B | `--time-to-teleport -1` on closure runs | the symptom (1.1) | stuck vehicles never arrive; converts a false leak into honest unfinished trips |
| C | Offline pre-routing of planned closures | cause, at source | biggest change; alters what is being simulated |
| D | Stop counting teleport-induced entries as leaks | nothing | hides the phenomenon — **rejected** |

**D is rejected outright.** The integrity gate is zero-tolerance for good
reason; loosening the measurement because it reports something inconvenient is
how a project stops being able to trust its own results.

**C is the principled end state.** These are *planned* closures announced in
advance, so a real driver re-plans from the start rather than driving into the
obstruction and discovering it — which is precisely the assumption
`closure_disruption()` already encodes. But `CLAUDE.md` records that duarouter
ignores rerouter files entirely, so C means real edge removal or prohibitive
weights, plus a re-validation of every closure result. Too big to bundle with a
gate fix.

**A + B together are the proposal.** A reduces the population that can get
stuck; B ensures that whoever still gets stuck is counted honestly instead of
being teleported through the closure.

---

## 3. Staged plan

### Stage 1 — Measure the mechanism (no behaviour change)

Re-run one failing case (`v9-control-secondary-3`, 0/15) with SUMO warnings
captured, and record per schedule:

- teleport count and each teleporting vehicle's id,
- `entered` on the closed edge,
- whether each teleported vehicle's route contains the closed edge downstream.

**Gate:** teleported-vehicle ids must account for ≥90% of closed-edge entries.
If they do not, §1.1 is wrong and the rest of this plan is void. Write to
`validation/closure_leak_mechanism_v1.json`.

Cost: ~15 min, one case, no code change.

### Stage 2 — Rerouter reach sweep

Sweep `REROUTER_RADIUS_M` ∈ {400, 800, 1500, 3000} on two failing cases and one
passing case. Record leak count, teleports, eligible-schedule count and wall
time per setting.

**Gate:** adopt the smallest radius that yields ≥3 rankable cases without more
than doubling closure wall time. If no radius achieves it, go to Stage 3 on its
own.

Note: the radius is an input to results, so any change invalidates existing
scenario outputs and must be recorded like any other results-affecting change.

### Stage 3 — Teleport policy for closure runs

Add an explicit `--time-to-teleport` to the closure path (not the diagnostics),
defaulted to a value chosen from Stage 1's waiting-time distribution, with `-1`
available.

**Gate:** on the Stage 1 case, closed-edge throughput must fall to 0 while
`dropped_unreachable`/unfinished trips rise by no more than the number of
vehicles Stage 1 showed as genuinely detour-less. A vehicle that cannot get
through must show up as *not arriving*, never as *driving through*.

### Stage 4 — v10 selection precondition

Add a third condition to the held-out selection rule, alongside the 400 m band
and `MIN_WINDOW_SUPPORT = 10`: **the edge must survive its own closure**. It is
checkable pre-outcome and cheaply — `closure_disruption()` already computes
`vehicles_no_detour` from the demand side, and a reachability probe over
`net.net.xml` with the edge removed is the same check `truncate_stranded_
vehicles` already performs.

Freeze v10 the same way as v9 (outcome-blind, byte-for-byte reproducible,
`EXACT_DEMAND_BINDING_CAMPAIGNS` updated *before* freezing — that trap has now
voided two campaigns).

**Gate:** the frozen v10 set must contain ≥4 cases whose closure produces zero
structurally-severed vehicles, so `ranking_case_fraction` ≥ 0.5 is achievable
before any SUMO runs.

### Stage 5 — Warming: regenerate the plan, then measure

Independent of Stages 1–4 and can run in parallel.

The annual population **cannot execute today**: 7 of the frozen 2027 plan's 49
bound inputs have drifted (`build_sumo_demand.py`, `build_candidates.py`,
`demand/structure.py`, `run_scenario.py`, `tools/populate_annual_warming.py`,
`traffic_sim/simulation/warm_state_cache.py`), so `_verify_plan_source_seal`
aborts at the first group. The plan status is `full_day_planned_unexecuted` — it
was never run. A fresh plan is a prerequisite for any warming at all, not a
benchmark side effect.

1. Generate a new plan from current sources (`tools/plan_annual_warming.py`).
2. Bounded two-group pilot (`--max-units 274`) **both ways**: default, and
   `--no-prefetch-demand`. 273 units exhaust the first demand group, so 274 is
   the smallest run that exercises the overlap.
3. Profile one group's units to find where the measured 2.65 s/unit goes.

**Gate:** if the overlap saves less than 15% of pilot wall time, revert
`d88a42f` rather than carry scheduling complexity for nothing. Item 3 (per-unit
overhead) is **not** designed until step 3 says what to cut — the connect-loop
theory in `WARMING_SPEED_REVIEW` is plausible and unverified, and optimising an
unmeasured cost is how you ship a change that buys nothing.

Expected, for reference and to be falsified: demand ~332 s and state ~252 s per
build run back to back today, so a full overlap saves up to ~252 s per build,
i.e. 25.7 h of a 59.5 h population.

---

## 4. Ordering

Stage 1 first and alone — it is cheap and can void §1.1. Stages 2 and 3 are
sequential (3's gate is stated in terms of 2's outcome). Stage 4 needs 2 and 3
adopted, because the selection precondition should reflect the closure semantics
that will actually run. Stage 5 is independent throughout.

Stage 4 is what unblocks the honesty label: v9 already satisfies six of seven
checks, so a v10 that keeps its evidence quality and fixes only case selection
is the shortest path to a passing held-out campaign on the deployed objective.

## 5. Explicitly out of scope

- **Continuous warm state** (one process, all 96 checkpoints). Researched
  2026-08-05 and parked: `MSDevice_Tripinfo::saveState()` does not serialize
  `myMesoTimeLoss`, the member is private with no accessor, and tripinfo is
  emitted only at arrival or run end — so exact per-checkpoint prefix evidence
  requires ending a run at each checkpoint. The 17.6× measured earlier was on
  states alone and silently dropped the evidence requirement. The multi-snapshot
  primitive (`1de1cf5`) is committed and harmless but currently has no consumer.
- **Merging `speed-stage-b`.** Held by owner decision (`efd3230`) pending a
  stronger held-out set. v9/v10 is that set; revisit after Stage 4.
- **Re-opening any global-best claim.** `global_best_claim_allowed: False`
  stands until a campaign passes.
