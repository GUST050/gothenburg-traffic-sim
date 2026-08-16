# Research review — the 2026-08-13 dirsplit plan

**Date:** 2026-08-16 · **Status:** findings only. No pipeline code changed, no
model retrained, no SUMO run. **Subject:**
`docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md`.

**Question asked:** go through the plan; research how the split should be done,
whether it should be done at all, and what is most robust.

**Method.** Findings are labelled `[DOC]` (carried from the plan or repo
history), `[NEW]` (measured today) and `[LIT]` (external practice, cited).
Every `[NEW]` number is reproduced by three scripts that run on tracked
artifacts only: `python3 -m tools.research_direction_split_evidence` (F1–F8,
plus a regenerated `sumo/direction_split.json`),
`python3 -m tools.research_direction_sum_constraint` (F9–F11) and
`python3 -m tools.research_direction_solution_space` (F12–F14).

**Two revisions, same day.** F9–F11 were added after "should I let entropy
choose?" and reversed this document's original recommendation — the correction
is marked in §3. F12–F14 were added after a request to search the whole
solution space; they did not reverse anything, but they changed what the
project's priority should be.

---

## 0. Headline

**The plan's audit is correct — I reproduced its artifact numbers to the
digit. But its preregistered outcome space has a hole that the evidence falls
straight into, and one of its "later, only if a gate opens" items is a live
defect in what ships today.**

Three things follow from measurement, not opinion:

1. The winner of the plan's own model tournament is **neither** of the two
   options its four-cell table allows. It is not 50/50 and it is not a
   street-conditioned model. It is an **unconditional time-of-day curve**,
   which wins on every fold design I ran while the deployed LightGBM stack
   loses to 50/50 on every one of them.
2. The direction split decomposes into a **level** and a **shape**, and only
   the shape transfers. The plan treats direction as one quantity; treating it
   as two is what makes both halves defensible.
3. The nominal 80 % interval has **47 % actual coverage**. That is not an
   unvalidated label — it is a measured factor-of-two understatement, and it
   feeds the confidence number on the map today.

And two things the plan does not consider at all, added after the fact:

**"Letting the solver choose the split" is not an option that exists.** A
constraint on the two-way sum rescales both carriageways uniformly, so the
split is decided entirely by the candidate pool — whose implied value at this
edge swings 35 percentage points on a routing nuisance parameter (F9–F11).

**And the whole project has been optimising the wrong 7 %.** A full search of
the solution space (F12–F14) ends with one number dominating: a **local
D-factor is worth +22.7 %, the entire transfer-modelling apparatus +7.0 % on
top of it.** Two further families — corridor continuity from the neighbouring
sensor, and profile deconvolution — were tested and falsified on the way.

---

## 1. What the plan gets right

`[DOC]` Most of it, and the parts that matter most.

- The audit table in §"Revisionsfynd" is accurate on every line I checked:
  `dataset.py` aggregates away day-to-day variation; `train.py` trains only
  weekday 06–20 so `is_weekend` is constantly zero; `predict.py` nevertheless
  emits all 24 hours with `is_weekend=0` and reuses one 96-slot profile across
  the calendar; `coverage.py` measures only static-feature kNN distance.
- The decision-surface analysis is right and load-bearing: sensor 107 is the
  only station with two approved edges, so it is the only one whose two-way
  total is split into two level-1 targets. Verified in
  `demand/intake.py:367` (`share` applied only when `len(edges) > 1`) and in
  `web/data/network.geojson` (107 has 2 features, the other five have 1 each).
- "Local evidence beats a Norwegian transfer model for the same aggregate
  quantity" is correct, and finding 6 below shows it is *quantitatively*
  correct — the transfer model is worse than a coin flip at the level.
- The refusal to hard-code `0.52` into `build_targets` is right.
- The relaxation-ladder discipline, the ban on field-wise splicing, and the
  insistence that seeds and demand cases be separate axes are all sound.

`[DOC]` The plan's numbers check out. Regenerating `sumo/direction_split.json`
from the tracked `model.pkl` gives median |q50 − 0.5| = **0.0070**, max
**0.0340**, median q10–q90 width **0.1070** — matching the plan's quoted
0.007 / 0.034 / 0.107 exactly. Its artifact audit can be trusted.

---

## 2. Findings

### F1 `[NEW]` The deployed model applies essentially nothing

At sensor 107 — the only place the split carries level-1 authority — over the
07:00–09:00 peak of 2025-09-16 (997 veh two-way):

| split source | toward-centre veh | vs 50/50 |
|---|---:|---:|
| 50/50 | 498 | 0 |
| deployed dirsplit q50 | 495 | **−4** |
| pooled tidal curve (F3) | 536 | +37 |

The entire subsystem — Norwegian API fetch, geocoding, station matching,
similarity-weighted per-sensor quantile LightGBM, James-Stein shrinkage —
currently moves the calibration target by **four vehicles**, in the direction
opposite to the physically expected one.

### F2 `[NEW]` Direction is about half a time-of-day property, not mostly a street property

Over the 3 665 two-way toward-centre station-hours in the tracked table:

- between-station variance (each street's own level): 0.00369, **sd 0.061**
- within-station variance (hour and day type): 0.00416, **sd 0.065**
- → **53 %** of all variation is time-of-day, not street identity

The tidal sign is the textbook one: median AM(07–08) − PM(16–17) toward-centre
share is **+0.065**, with the classic AM-inbound sign at **56 of 81** stations.

### F3 `[NEW]` A pooled hour-of-day curve beats both 50/50 and every LightGBM variant

Leave-city-out and leave-station-out, station-normalised weights, everything
fit inside the training fold. `pooled_hour_curve` uses **no street features at
all** — one global curve of toward-centre share by hour, partially pooled to
0.5.

| fold design | 50/50 | pooled curve | lgbm plain | lgbm sim-weighted |
|---|---:|---:|---:|---:|
| leave-city-out, all two-way | 0.0639 | **0.0604 (+5.4 %)** | 0.0686 (−7.4 %) | 0.0670 (−4.9 %) |
| leave-city-out, domain subset | 0.0569 | **0.0532 (+6.6 %)** | 0.0618 (−8.7 %) | 0.0609 (−7.1 %) |
| leave-station-out, 81 folds | 0.0639 | **0.0608 (+4.9 %)** | 0.0687 (−7.5 %) | 0.0679 (−6.3 %) |

The shrinkage λ is the tell — it measures how much of a model's claimed
deviation from 50/50 survives validation:

- pooled curve: **λ = 0.93–0.98** (nearly all of it is real)
- LightGBM: **λ = 0.21–0.44** (validation deletes 56–79 % of it)

The deployed λ = 0.289 is not a tuning quirk. It is validation correctly
reporting that most of what the complex model asserts is noise.

The curve itself is physically clean — weekday peak-to-peak **0.099**:

```
06:00  0.556 toward centre      12:00  0.494      15:00  0.457      23:00  0.470
```

### F4 `[NEW]` The nominal 80 % interval has 47 % coverage

Same quantile architecture, refit leave-city-out:

| | coverage | mean width | interval score |
|---|---:|---:|---:|
| raw quantile models | **47.0 %** | 0.099 | 0.3836 |
| after deployed shrinkage | 46.7 % | 0.099 | 0.3865 |
| honest 80 % (empirical residual spread) | 80 % | **0.193** | — |

The deployed interval is **~half the width it needs to be**. And because the
training rows are already ~8-day means, single-day coverage is *worse* than
this — **47 % is an upper bound.**

This is not a labelling nicety. `edge_shares_q10/q90` build the demand variants
whose Monte Carlo spread becomes the per-edge `confidence` shown on the map. On
the direction axis, that confidence number is currently optimistic by about a
factor of two.

### F5 `[NEW]` Level does not transfer; shape does

Flow-weighted across all of 2025 at sensor 107, against Göteborgs Stad's own
published D-factor (N 3400 / S 3100 of 6500 = 52.3/47.7):

| estimator | implied N share | error |
|---|---:|---:|
| 50/50 | 0.5000 | 2.31 pp |
| deployed dirsplit q50 | 0.4981 | 2.50 pp |
| pooled tidal curve | 0.4940 | **2.91 pp** |

**No transferred model recovers the level — the curve is the worst of the
three at it.** That is not a contradiction of F3: the curve is validated for
the *within-day shape*, and it carries almost no level information by
construction. With a between-station sd of 0.061 (F2), a street's own level is
a ±6 pp unknown that only local measurement resolves.

### F6 `[NEW]` The two compose cleanly, and the geometry checks out

A single logit offset of **+0.1166** makes the pooled curve reproduce the
published anchor exactly (0.5231) while preserving the tidal amplitude (0.098
vs 0.099 unanchored).

Direction mapping verified from `network.geojson` geometry:
`60786979_3575001205_0` has bearing **352.1° (N)** and is the toward-centre
carriageway (radial_cos +0.61); `1455801464_18241874_0` is 174.4° (S). **The
catalogue's N row is the toward-centre edge** — so 52/48 can be applied without
a sign error. Getting this backwards would be a 4.6 pp swing.

Resulting profile at 107 (level = Gothenburg's own counts, shape = validated
transfer):

```
06:00  58.4/41.6      09:00  54.8/45.2      12:00  52.3/47.7
15:00  48.6/51.4      17:00  51.6/48.4      23:00  49.9/50.1
```

### F7 `[NEW]` 92 % of the collected data is discarded before training

15 346 rows in the tracked table → **1 214** used (178 → 81 stations). Weekend
rows are already collected and then dropped; `predict.py` then emits every hour
with `is_weekend=0`. The two day types genuinely differ — weekday peak-to-peak
0.099 vs weekend 0.078, and the weekend AM inbound peak is absent. This is
free accuracy currently thrown away.

### F8 `[LIT]` FHWA supports exactly this decomposition

The Traffic Monitoring Guide states that a road near an urban centre "often has
a D-factor near 50 %", while suburban roads are directionally imbalanced "due
to larger traffic traveling toward an urban area in the morning and away from
an urban area in the evening."

Read as two statements: the **level** in a city centre is ≈50/50 (nothing to
predict — matching F5), and the **shape** is the tidal effect (real and
systematic — matching F3). The standard practice literature says the
transferable part is the tide, not the street. The deployed architecture bets
on the opposite.

### F9 `[NEW]` "Don't split at all" is expressible — but entropy does not choose

The plan never lists it, and `traffic_sim/demand/pfe.py` does already have
`groups: list[tuple[list[int], float, float]]` — a band on the sum over an
explicit route-index set, enforced in both the LP and the entropy IPF and
checked in `_check_entropy_solution`. A two-way total is therefore directly
expressible as one constraint on the sum of both carriageways.

**But the phrase "let entropy choose" does not describe what would happen.**
Groups are appended to `bounds_items`, whose correction is:

```python
for j in js:
    x_list[j] *= factor        # one factor for every member route
```

A group rescales all its members **uniformly**. It never redistributes between
them. A constraint on the sum therefore carries no information about the split,
and the seed's ratio passes through untouched — the standard entropy result
that constraints tilt the solution only along constrained directions.

Verified numerically by driving `solve_interval_entropy` on a two-carriageway
toy pool:

| pool | sum constraint only | imposed 52/48 | sum, seed favours A 3:1 |
|---|---:|---:|---:|
| 10 on A, 10 on B | 0.500 | 0.520 | 0.750 |
| 10 on A, 5 on B | 0.667 | 0.520 | 0.857 |
| 10 on A, 20 on B | 0.333 | 0.520 | 0.600 |
| 30 on A, 6 on B | 0.833 | 0.520 | 0.938 |
| 4 on A, 40 on B | **0.091** | 0.520 | 0.231 |

The "sum only" column is exactly `n_A / (n_A + n_B)`. **"Let entropy choose"
means "let the candidate pool's directional composition choose"** — an
unvalidated structural prior, not an absence of assumption.

Two further caveats stand regardless: `groups` drop at `RUNG_NOBND_TOL1`, so a
measured sum placed there would yield before the measurement band widens and
would need its own level-1 class; and a route touching both carriageways counts
once in a group but twice in the true sum.

### F10 `[NEW]` That structural prior is not usable at this edge

Gravity-sampled OD pairs routed over the frozen graph with perturbed travel
times — a simplified stand-in for `assignment_priors.compute_assignment_load`,
24 000 routed pairs, 95 % CIs about ±0.04:

| routing assumption | implied split at 107 |
|---|---:|
| shortest path, no perturbation | **0.230** |
| stochastic multipath σ = 0.10 | 0.314 |
| stochastic multipath σ = 0.15 | 0.396 |
| stochastic multipath σ = 0.20 | 0.471 |
| stochastic multipath σ = 0.30 | 0.555 |
| stochastic multipath σ = 0.45 | 0.581 |

**A 35-percentage-point range driven by a routing nuisance parameter alone**,
against a real physical range of roughly 0.44–0.60 (F2, F3) and a measured
value of 0.523. Plain shortest-path routing gives 23/77 — the pool at this edge
is decided by which of two parallel carriageways happens to be marginally
faster as a through-route, which flips with σ.

`[LIT]` This is the textbook behaviour, not a surprise: the OD-estimation
literature is consistent that entropy and information-minimising estimators
leave accuracy "highly dependent upon the quality of the prior information."
The method is sound; the prior is simply outside its validated domain here. The
assignment prior was built and validated as a network-wide plausibility bound,
never as a resolver of a near-50/50 split between two carriageways of one
street.

**Honest limit:** this probe is not the production prior — no DeSO population
weighting, no POI activity fields, no paired tours, no SUMO routing costs. The
real one may be better centred. The *sensitivity* finding is a property of the
network topology and would very likely survive; the specific values should not
be quoted as the production prior's output.

### F11 `[NEW]` A two-sided band does not express "unknown" either

The obvious repair — sum at level 1, each carriageway bounded at level 2 — was
tested and does not do what it looks like:

| band on A | `lo_A/(lo_A+lo_B)` | split, pools 10/10 · 10/20 · 30/6 |
|---|---:|---|
| [0.449, 0.642] | 0.556 | 0.556 · 0.556 · 0.556 |
| [0.400, 0.600] | 0.500 | 0.500 · 0.500 · 0.500 |
| [0.500, 0.700] | 0.625 | 0.625 · 0.625 · 0.625 |

The good news is that the result becomes **completely independent of pool
composition**. The bad news is where it lands: with the small IPF seed both
carriageways are pushed to their *lower* bounds and then rescaled by the group,
so the answer is a deterministic `lo_A / (lo_A + lo_B)` — the band's lower
corners, not its centre. A band centred on 0.545 returns 0.556; one centred on
0.600 returns 0.625.

**Within a single PFE solve there is no way to represent "I don't know the
split."** One solve returns one number. Direction uncertainty has to live
*across* solves — which is exactly what the existing q-variant architecture
does, and the only thing wrong with it is that the variants are 2× too narrow
(F4).

### F12 `[NEW]` Corridor continuity from 1076 — falsified

Sensor 1076 measures **southbound Skånegatan 257 m from 107**, which measures
the two-way total on the same street. If 1076 were a clean downstream
cross-section, `1076 / 107_total` would simply *be* the southbound share — a
measured, 15-minute-resolution, full-year direction split with no model at all.
It is the most attractive idea in the whole space, and it is wrong:

- implied southbound share **0.677** against a published 0.477 — **20 pp off**;
- 1076 exceeds 107's **two-way total** in **7.9 %** of quarters, which is
  impossible for a nested cross-section.

Substantial flow enters between the two stations. Worth recording as a dead end
precisely because the geometry makes it look so promising.

### F13 `[NEW]` Profile deconvolution — falsified twice, and it explains the legacy 80/20

The `estimate_directions.py` family assumes the total is a sum of two
counter-phased tides. With five *locally measured* single-direction profiles
available as bases, that is now directly testable. Fitting
`107_total = a·Pᵢ + b·mirror(Pⱼ)` over all 20 ordered pairs:

- implied N share ranges **0.878 – 1.034**, median 0.956, at R² 0.93–0.98.
  **A share above 1.0 is not physical.**
- the control settles it: the mirrored basis beats an un-mirrored one in
  **0 of 10** pairs.

Gothenburg's own data rejects the counter-phase premise. The two carriageways
of a central street are close to *in phase* — which matches this repo's own
earlier finding that "both directions peak in the morning, just slightly
unevenly," and explains why the AM/PM Gaussian produced 80/20: it forced a
counter-phase structure the data does not contain.

### F14 `[NEW]` The decisive decomposition — the anchor is worth 3× the shape

This is the measurement that settles the design. Each held-out station is
predicted by its **own mean share** — the exact analogue of the city's published
annual D-factor for 107 — and then by that anchor plus the pooled time-of-day
shape composed on the logit scale.

| estimator | MAE | vs 50/50 | vs anchor |
|---|---:|---:|---:|
| 50/50 flat | 0.0639 | — | −29.4 % |
| pooled curve, no anchor | 0.0603 | +5.5 % | −22.2 % |
| **local anchor, flat** | **0.0494** | **+22.7 %** | — |
| **local anchor + pooled shape** | **0.0459** | **+28.1 %** | **+7.0 %** |

Replicated under leave-station-out: +22.7 % and +7.2 %. The shape's increment
is statistically real — paired bootstrap 95 % CI [+0.0020, +0.0048], excluding
zero — but it is the smaller half by a factor of three.

**The single most valuable input is a local D-factor, not a model.** One
published number is worth more than the entire transfer-modelling apparatus,
and the apparatus as deployed is worth less than nothing (F3).

---

## 3. The three questions

### Should we split at all?

**Only at 107, and "split" should mean "constrain what was actually measured"
rather than "invent a per-direction number".** The other five stations must not
be split — their measured direction already enters level 1 untouched, and
`demand/intake.py:367` correctly guards this.

**CORRECTION, 2026-08-16 (same day).** An earlier revision of this document
recommended constraining the sum and letting the solver pick the split, and
called it the most robust option. **F9–F11 measured that and it is wrong.**
Entropy does not pick the split; a sum constraint scales both carriageways
uniformly and hands the decision to the candidate pool, whose implied split at
this edge ranges over 35 percentage points on a routing nuisance parameter.
The recommendation below replaces it.

**Recommended — anchored curve.** Level from Gothenburg's own 52.3/47.7, shape
from the pooled tidal curve, interval widened to the honest 0.193 (F4). Keeps
today's file format and every downstream consumer; replaces the entire LightGBM
stack with roughly thirty lines. Every component is separately validated: the
level is a local measurement, the shape is leave-city-out and
leave-station-out validated, the width is measured residual spread.

The same treatment is the right default for the five single-direction stations'
*estimated opposite carriageway* — a level-2 bound, which should also be
widened per F4, since it is currently about twice too tight.

**Not recommended — sum constraint alone.** It is expressible (F9), but it
replaces a measured, validated estimate with an unvalidated structural one
(F10), and the natural repair of bounding the split turns into a disguised
point estimate at the band's lower corners (F11). The one property worth
keeping from it is that the *measured* quantity at 107 is genuinely the total,
not two directions — which is an argument for auditing the two level-1 targets
against their sum, not for abandoning the split.

### What is best?

**Level from local data × shape from the pooled curve, with an honest interval
carried across demand variants.** Retire the similarity-weighted per-sensor
quantile LightGBM stack: leave-city-out says it costs accuracy on all three
fold designs, and its own shrinkage λ says three-quarters of what it asserts is
noise.

The full space, with every family actually tested:

| # | Approach | Verdict | Evidence |
|---|---|---|---|
| 1 | Per-street ML transfer (deployed) | ✗ worse than a coin flip | −7.4 / −8.7 / −7.5 % on three fold designs (F3) |
| 2 | 50/50 flat | baseline | 2.31 pp level error at 107 (F5) |
| 3 | Pooled tidal curve, no anchor | ✓ small win | +5.5 % / +4.9 % (F14) |
| 4 | **Local anchor, flat** | ✓✓ | **+22.7 %** (F14) |
| 5 | **Local anchor + pooled shape** | ✓✓✓ **best** | **+28.1 %**, increment significant (F14) |
| 6 | Sum constraint, "let entropy choose" | ✗ | split = pool ratio; 35 pp swing (F9, F10) |
| 7 | Sum + two-sided band | ✗ | collapses to `lo_A/(lo_A+lo_B)` (F11) |
| 8 | Sum + nearby sensor identifies split | ✗ | still 10–15 pp off, needs unknown route-sharing fraction |
| 9 | Corridor continuity 1076 → 107 | ✗ | 20 pp off; violated in 7.9 % of quarters (F12) |
| 10 | Profile deconvolution / AM-PM Gaussian | ✗ | implied shares 0.878–1.034; mirror wins 0/10 (F13) |

**And the finding that reframes the project.** The value is overwhelmingly in
the *local anchor*, not the modelling: one published number buys +22.7 %, the
whole transfer apparatus buys +7.0 % on top of it. `dirsplit/` has been
optimising the 7 % while the 23 % sat in a public catalogue.

The highest-leverage remaining action is therefore **not a better model** — it
is checking whether Göteborgs Stad's public trafikmängder catalogue carries
directional rows for the other five stations or for nearby streets. CLAUDE.md
already flags this as an open manual check; F14 is the argument for its
priority. It is a public catalogue, not a data request, so it does not touch
the 2026-07-20 "no more external data" decision — but confirm that reading
before acting on it.

### What is most robust?

**The anchored curve**, because each of its three parts is separately validated
against held-out data and its errors are bounded and measurable. Robustness
here does not come from refusing to state a number — F9–F11 show that refusing
to state one just hands the decision to a less validated mechanism. It comes
from stating a number whose level, shape and width each have evidence behind
them.

Ranked, at sensor 107:

| | level error vs published | direction signal | uncertainty width |
|---|---|---|---|
| anchored curve | 0 by construction | validated, ~10 pp tide | measured 0.193 |
| 50/50 | 2.31 pp | none | none stated |
| deployed dirsplit | 2.50 pp | 0.32× validated | 0.55× honest |
| sum constraint alone | unbounded (0.230–0.581 observed) | pool artifact | none stated |

### The deliverable

Level = the city's published 0.5231. Shape = the pooled weekday tidal curve.
Width = the measured residual spread, ±0.0965 carried across demand variants.

| hour | N / S at 107 | 80 % band on N |
|---|---|---|
| 00:00 | 50.3 / 49.7 | 0.406 – 0.599 |
| 06:00 | 58.3 / 41.7 | 0.487 – 0.680 |
| 08:00 | 56.0 / 44.0 | 0.464 – 0.657 |
| 12:00 | 52.3 / 47.7 | 0.427 – 0.620 |
| 15:00 | 48.4 / 51.6 | 0.388 – 0.581 |
| 17:00 | 51.6 / 48.4 | 0.420 – 0.613 |
| 23:00 | 49.8 / 50.2 | 0.401 – 0.594 |

Tidal amplitude 0.099, anchored mean 0.5231. Note the band is wider than the
whole tidal swing — which is the honest state of this quantity, and the reason
the interval matters more than the point.

Two implementation notes. The curve must be built from the **full weekday**,
not the 06–20 training window (F7) — the rows are already in the tracked table.
And for the five single-direction stations there is no local anchor, so their
*estimated opposite carriageway* gets row 3 of the table above (pooled curve,
+5.5 %) as a level-2 bound, widened per F4.

The current deployment sits third: it is simultaneously **too flat in the
middle** (0.32× the validated signal) and **too narrow at the edges** (0.55×
the honest width) — understating what is known and what is unknown at the same
time. The sum constraint sits fourth because its error is not merely large but
unbounded by anything physical.

---

## 4. Recommended changes to the plan

1. **Add a fifth outcome to the tournament table (§"Modellturnering").** The
   four-cell grid assumes the point model is either 50/50 or
   conditional-on-street. The measured winner is neither — it is an
   unconditional *time-varying* curve. As preregistered, the honest result has
   nowhere to land, and would be forced into "BASELINE", which would then be
   read as "direction has no signal". It has signal; it just isn't
   street-specific.

2. **Run Fas 1 before Fas 0B.** The plan makes the matched-seed SUMO
   sensitivity test (Fas 0B) unconditional and puts the tournament (Fas 1)
   after it. Fas 1 is nearly free — most of it ran in minutes on tracked data.
   Fas 0B is the expensive one. Running the cheap gate first would have
   surfaced the 47 % coverage before any SUMO time was spent.

3. **Gate S's premise is weaker than the plan assumes.** §"Före
   produktändring" proposes measuring decision sensitivity using the *existing*
   q10/q50/q90 artifacts as stress cases. Those span 0.107 — about half the
   honest width (F4). A Gate S run on them would understate direction
   sensitivity *by construction* and could return `NO` for the wrong reason.
   **If Gate S runs, run it on the honest ±0.0965 band, not the deployed one.**
   This is a methodological flaw, not a preference.

4. **Promote the interval fix out of Gren B/D.** The plan defers all interval
   work behind Gate S and Gate P. But q10/q90 feed the Monte Carlo that feeds
   the map's confidence number *today*. Either widen it to the measured 0.193
   or relabel it `stress_only` — both are small, and both are correct
   regardless of how any gate resolves.

5. **Record the no-split option as tested and rejected, not as unexplored.**
   §"Beslut i korthet" frames the choice as central profile vs ensemble, and
   never mentions letting the solver decide. F9–F11 close that gap with
   evidence: it is expressible, it is not what "entropy chooses" suggests, and
   at this edge the mechanism it defers to is unusable. Worth keeping as
   negative evidence so it is not re-proposed.

6. **Fas 0A should record the verified N↔toward-centre mapping**, not just the
   raw 3400/3100 numbers. F6 supplies it (bearing 352.1°). Without it the
   anchor can be applied backwards, and nothing downstream would catch a 4.6 pp
   sign error.

7. **Stop discarding the weekend and off-hours rows (F7)** — they are already
   collected, tracked, and free.

---

## 5. What this review did not do

- No SUMO run, so **Gate S is untouched** — decision sensitivity remains
  unmeasured, and nothing here claims otherwise.
- No pipeline, demand, PFE or web code changed. No model retrained.
- `sumo/direction_split.json` was regenerated (gitignored) from the tracked
  `model.pkl` purely to audit it; it reproduces the plan's quoted numbers
  exactly.
- The tournament used the tracked aggregated table, so it inherits that
  table's own limitation: it cannot measure true day-to-day variance. That is
  precisely the plan's dataset-v2 point, and it stands. The 47 % coverage
  figure is therefore an upper bound, which only strengthens F4.

---

## Sources

- FHWA, *Traffic Monitoring Guide* — traffic monitoring theory, directional
  distribution and D-factor:
  <https://www.fhwa.dot.gov/policyinformation/tmguide/tmg_2013/traffic-monitoring-theory.cfm>
- FHWA, *Traffic Data Computation Method Pocket Guide* (FHWA-PL-18-027):
  <https://www.fhwa.dot.gov/policyinformation/pubs/pl18027_traffic_data_pocket_guide.pdf>
- Van Zuylen & Willumsen (1980), *The most likely trip matrix estimated from
  traffic counts*, Transportation Research Part B 14(3):281–293:
  <https://www.sciencedirect.com/science/article/abs/pii/0191261580900089>
- Gneiting, Balabdaoui & Raftery (2007), *Probabilistic forecasts, calibration
  and sharpness* — the calibration-before-sharpness argument behind F4:
  <https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jrssb.pdf>
