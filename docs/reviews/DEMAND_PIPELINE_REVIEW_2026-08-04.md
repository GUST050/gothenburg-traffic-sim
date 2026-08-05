# Trip pool and trip selection — review before starting warming

**Date:** 2026-08-04 · **Reviewer:** Luna High (Claude) · **Status:** review
only, nothing changed.

> **Measured effect of the S1 fix (added 2026-08-04).**
> `pfe_variants_and_rounding` fell **173.1 s → 57.5–59.6 s** on a 1-day build
> (`demand_meta.json` timings), with
> `tests/test_pfe.py::test_precomputed_touch_index_is_bitwise_equivalent`
> pinning exactness. Every 173.1 s figure below is the pre-fix baseline, not
> current performance.

> **Disposition added 2026-08-04.** The review describes the pre-fix pool.
> Candidate generation now proves 7,125/7,125 routable non-internal SUMO
> edges, and every q10/q50/q90 calibrated route product independently proves
> the same exact edge set. Missing unmeasured edges receive a deterministic
> minimum legal support set whose routes never cross measured edges; those
> support-only trips are simulated but excluded from behavioral purpose, trip-
> length and OD-fit statistics. Candidate/agent/route provenance now binds the
> complete edge sequence one-for-one. Finding S1 is also fixed: one immutable
> touch index is built before the worker fork and reused bit-exactly. S2 and S3
> remain measured optimization ideas, not defects: neither was adopted without
> a whole-build bit-exact proof because finite-iteration Gauss-Seidel dynamics
> can change even when a mathematical constraint is redundant. The rebuilt
> release contains 16,542 candidate routes (13,032 distinct route×purpose
> variables), 65,563 calibrated vehicles across three variants, 100% GEH<5,
> and zero infeasible intervals.

Scope: how the *candidate pool* is decided (`build_candidates.py`) and how the
*actual trips* are decided (`traffic_sim/demand/pfe.py`), reviewed for bugs,
architectural flaws and speed — as pre-work before the warming effort.

Every number below is measured on the checked-in artifacts
(`sumo/candidates.rou.xml`, `sumo/demand_meta.json`,
`sumo/assignment_priors.json`) or timed on this machine. Nothing is estimated
unless labelled so.

---

## Headline

**The biggest safe speedup available to you is not warming — it is in the PFE,
and it is exact.** 54 % of every quarter solve is spent rebuilding a data
structure that is provably identical across all quarters and all three variants,
and a further large slice enforces 4,137 ceilings of which **2 actually bind**.

For contrast with the warming work (see `WARMING_SPEED_REVIEW_2026-08-03.md`):
warming caps out at a 1.06× simulation speedup on the frozen case and is
currently net-negative. The two items below are measured, bit-exact, and worth
roughly 2× on the demand build.

| | measured |
|---|---|
| demand build, 1 day | 173.5 s total, of which **173.1 s is the PFE** |
| one quarter solve | 0.367 s |
| — of which rebuilding the `touch` map | **0.200 s (54 %)** |
| assignment ceilings touched by a route | 4,137 |
| assignment ceilings that actually bind | **2 (0.05 %)** |
| float ops per solve spent on ceilings | 253 M |
| same, across a 5-day build (1,440 solves) | **365 G** |

---

## Part 1 — How the pool is decided

`build_candidates.py` builds a *sensor-anchored subarea/cordon* population:

1. **Masses.** Home mass per edge from SCB DeSO 2023 population, spatialised to
   building footprints. Activity mass from OSM POIs in RVU's three purpose
   categories (arbete/studier, service, fritid).
2. **Classes.** E-E through trips (gate→gate), E-I/I-E paired commute tours, I-I
   internal paired tours. Split by `--through-fraction` (0.5).
3. **Anchoring.** Every trip must be traceable to at least one of the 7 measured
   sensor edges. The far-end pool is masked to the exact subset where
   anchor→sensor→far-end is genuinely the shortest path
   (`natural_far_end_weights`), so nothing is generated and then discarded.
4. **Departure shape** from the measured `normal_profile.json`.

Measured result:

| quantity | value |
|---|---|
| `--n-total` trips requested | 12,000 |
| candidate legs written | 46,958 |
| distinct route×purpose variables after dedup | **20,868** |
| duplicate legs in the file | 26,090 (56 %) |
| `candidates.rou.xml` on disk | 71.7 MB |
| mean route length | 66.0 edges (median 69, max 134) |
| candidates touching ≥1 measured edge | **46,958 (100 %)** |

### Finding P1 — [ARCHITECTURE] 41 % of the network can never carry calibrated traffic

```
network non-internal edges  : 7,125
edges reachable by the pool : 4,202 (59.0 %)
edges in NO candidate route : 2,923 (41.0 %)
```

This is a direct consequence of sensor-anchoring, and it is the right trade-off
for *counts* — but it has consequences the confidence map does not currently
express:

- **Closing an edge in that 41 % is a no-op.** There is no baseline flow to
  divert, so the scenario returns "no impact" for a structural reason, not a
  traffic reason. A user closing a residential street outside every
  sensor-crossing path gets a confidently empty answer.
- Rerouting *can* place vehicles on those edges at runtime, so they are not
  invisible — but their baseline is exactly zero, which makes before/after
  comparison degenerate there.

Additionally, **622 covered edges (14.8 %) are supported by ≤5 candidates**.
Flow on those edges is decided by a handful of route geometries; closing one is
closer to editing the pool than to simulating a diversion.

### Finding P2 — [QUALITY] Per-sensor coverage is uneven by 3.3×

| sensor edge | candidates touching it |
|---|---|
| `26842525_26355153_0` | 13,873 (29.5 %) |
| `26355153_91615277_0` | 13,506 (28.8 %) |
| `30420757_30421744_0` | 11,327 (24.1 %) |
| `60790252_60790253_0` | 11,244 (23.9 %) |
| `1455801464_18241874_0` | 8,952 (19.1 %) |
| `60786979_3575001205_0` | 8,144 (17.3 %) |
| **`26355153_96523321_0`** | **4,130 (8.8 %)** |

`generate_sensor_anchored_trips` starts from an equal per-sensor quota
(`n_total // n_sensors`) and redraws up to 25 times, so this spread is the
*outcome* of how hard each sensor is to route through, not the intent. The
weakest sensor has 3.3× fewer route shapes to satisfy its counts with, which
directly limits how well the PFE can fit it — and is a plausible contributor to
the weak per-station LOSO numbers already recorded for 1076 and 107.

### Finding P3 — [QUALITY] The assignment prior's own fit is worse than a constant

`sumo/assignment_priors.json` records:

```
fit_r2      : -5.148
weight      : 0.15
n_samples   : 40000
```

A negative R² means the fitted scale predicts the measured flows *worse than
predicting their mean*. That field is used for two things: gate draw weights in
the pool, and 6,124 ceilings in the solver. Finding S2 below shows the ceilings
do essentially nothing. So the prior's value — which the LOSO record says is
real (median recovery 0.09 → 0.15) — must be coming from the *pool* side, not
the constraint side. Worth knowing before tuning it.

---

## Part 2 — How the actual trips are decided

`pfe.solve_interval_entropy` picks per-quarter route flows by
**maximum-entropy IPF** (Bregman/Sinkhorn balancing), not an LP:

- **Level 1** measured sensor targets — hard, pulled to the exact target each
  pass.
- **Level 2** structural bounds + assignment ceilings + structure groups — hard,
  rescaled into band.
- **Level 3** learned/corridor priors — soft partial pull.
- Feasible points are sampled after level 2 and averaged past a burn-in, so the
  result stays hard-feasible even if level 3 oscillates.
- 200 iterations, Gauss-Seidel order, executed by a numba kernel
  (`pfe_kernel.ipf_iterations`, verified bitwise against the Python reference).
- Each (variant, quarter) is independent and solved in a fork pool.

This is a sound design and the numba kernel is already in place — the
inefficiencies below are structural, not a missing optimisation.

### Finding S1 — [SPEED, largest] 54 % of each solve rebuilds an invariant index

`solve_interval_entropy` begins by building `touch` — for every candidate, for
every edge in it, append the candidate index if that edge is constrained. That
is ~1.3 M edge-set operations, and it is done **inside every solve**.

Measured:

```
rebuilding touch map : 0.200 s
full solve           : 0.367 s   -> 54 % of the solve
```

But the constrained edge set does not vary:

```
assignment-ceiling edge set size per quarter: [6124, 6124, 6124, 6124]
identical across quarters: True
```

The candidate pool is also identical across quarters *and* across the three
q10/q50/q90 variants. So the same index is rebuilt 1,440 times for a 5-day
build — **≈ 288 s of pure redundant setup**, more than the entire 1-day build
currently takes.

This is memoizable with no numerical change whatsoever: the map is a pure
function of (candidates, constrained-edge-set). The only care needed is that the
fork pool must build it *before* forking so workers inherit it copy-on-write
(`prepare_calibration` already establishes that pattern).

### Finding S2 — [SPEED] 4,137 ceilings are enforced every pass; 2 of them bind

Assignment ceilings are `(0.0, max(5.0, 5.0 × assigned_flow))` — deliberately
wide plausibility caps. Measured on quarter 32:

```
ceilings touched by a route : 4,137
ceilings actually AT the cap:     2  (0.05 %)
sum of touch-list lengths   : 1,267,306 adds per pass
per solve (200 passes)      : 253 M float ops
across 1,440 solves         : 365 G float ops
```

The kernel already skips the *multiply* when `factor == 1.0`, but it must still
compute the *sum* to discover that — and the sum is the dominant cost.

Two exact ways out, both standard:

- **Static domination.** Each route's flow is bounded above by the smallest
  measured target it touches. Precompute, per ceiling edge, the sum of those
  bounds; if it is below `hi`, that ceiling can never bind and can be dropped
  for the whole build. One O(E) pass, provably identical solutions.
- **Lazy constraints.** Solve without ceilings, check violations, re-solve with
  only the violated ones. With 2/4,137 binding this converges in one or two
  rounds and is the classic cutting-plane approach.

Also worth noting: **33.8 % of ceiling slots collapse to the flat 5.0 floor**
(`max(5.0, 5·v)` where `5·v < 5`), so a third of them are not carrying
assignment information at all — they are a constant.

### Finding S3 — [SPEED] The iteration count is fixed at 200 with no convergence test

`ipf_iterations` runs `for it in range(max_iterations)` with no early exit. Every
solve pays all 200 passes whether it converged at pass 30 or not.

The burn-in averaging genuinely needs a run of samples, so this cannot simply be
cut — but a stability test on the level-1 residual (all measured edges within
band, and x changing by < ε between passes) could stop early while keeping a
fixed *sample* count. Given that GEH<5 is 100 % on all variants, most quarters
are likely converging well before 200. **This one needs measurement before
acting** — I did not verify the convergence profile, and it is the only speed
item here I would not call safe on inspection alone.

### Finding S4 — [SPEED, minor] The pool file is 56 % duplicates

46,958 legs dedupe to 20,868 distinct route×purpose variables at load. The 71.7 MB
XML is parsed every build (1.1 s — not itself a problem) and copied into every
demand archive. Writing the pool pre-deduplicated with a multiplicity attribute
would cut the artifact by more than half. Low value, listed for completeness.

---

## Part 3 — Possible bugs

I found **no outright correctness bug** in either the pool generator or the
solver on this pass. Two things are worth flagging as latent rather than broken:

### Finding B1 — Every route is activated, so "parsimony" no longer prunes anything

The seeding rule activates a route if it touches a measured edge, a prior, or a
bound with `lo > 0`. Because **100 % of the pool touches a sensor by
construction**, every route is seeded active:

```
active routes 20,868 / 20,868
```

The comment history describes this rule as restoring the LP's parsimony (only
routes *required* to be nonzero start active). Against a sensor-anchored pool it
no longer does — the condition is universally true. That is not wrong (max-
entropy wants spread, and the seed is scaled to 1e-3), but the code reads as if
a filter is doing work that it cannot do, which will mislead the next reader.

### Finding B2 — `max(5.0, ...)` silently floors a third of the ceilings

Not a bug in itself, but `max(5.0, 5.0 * value)` means any edge with an assigned
flow below 1.0 veh/quarter gets the *same* ceiling of 5.0 regardless of how
implausible 5 vehicles would be there. On this build that is 198,979 of 587,904
slots (33.8 %). If the intent was "a wide multiple of the plausible value", the
floor defeats it exactly where the assignment field is most confident that flow
should be near zero.

---

## Part 4 — Architectural observations

1. **Sensor-anchoring is doing two jobs at once** — guaranteeing every vehicle is
   explainable by sensor data (good, and Gustav's explicit requirement), and
   implicitly defining the simulated network as the 59 % of edges on
   sensor-crossing paths (a consequence nobody chose). Those could be separated:
   anchor the *calibrated* population, but allow a small unanchored background
   population carrying explicitly zero-confidence flow, so closures outside the
   sensor-reachable subgraph have something to divert.
2. **Congestion feedback is off by default** (`--congestion-iterations 1`). The
   code cites research that simultaneous count+equilibrium calibration beats
   one-shot sequential, then defaults to one-shot. Either the default should
   move or the comment should say why it does not.
3. **The demand build is user-facing.** `serve.py`'s recalibrate path is how a
   user changes simulated date, and it is bounded by a 2400 s timeout. The PFE
   is ~99.8 % of that build, so S1+S2 translate directly into a faster,
   less timeout-prone product action — unlike warming, which only affects the
   monthly closure-search backend.

---

## Part 5 — Ranked recommendations before starting warming

| # | Change | Expected gain | Risk | Exactness |
|---|---|---|---|---|
| 1 | Memoize the `touch` index across quarters and variants (build before fork) | **~54 % of PFE time** — ≈288 s on a 5-day build | Low | Bit-exact |
| 2 | Drop provably-non-binding ceilings by static domination, or make them lazy | Most of 253 M ops/solve | Low–Medium | Bit-exact (domination) |
| 3 | Measure the IPF convergence profile; early-stop if it converges well before 200 | Potentially 2–5× on top | Medium | Needs verification |
| 4 | Rebalance per-sensor candidate quotas (weakest sensor has 3.3× fewer shapes) | Fit quality, not speed | Medium | Changes results |
| 5 | Reconsider the `max(5.0, …)` ceiling floor (33.8 % of slots) | Prior fidelity | Low | Changes results |
| 6 | Decide what closures outside the sensor-reachable 41 % should mean | Product correctness | — | Design |

**1 and 2 are the ones I would do first.** They are bit-exact, independently
verifiable against the existing `tools/verify_pfe_kernel.py` fingerprint, and
together they are worth more wall-clock than warming can deliver on its best
day — on the code path a user actually waits for.

**On warming specifically:** nothing in this review blocks it, but the ordering
looks wrong. Warming is a ~6 % simulation saving on the monthly search backend,
currently net-negative and blocked on an unresolved residual. The demand pipeline
has a measured ~2× sitting in it, on the interactive path, with no correctness
question attached.
