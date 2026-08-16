# Research review — the 2026-08-13 dirsplit plan

**Date:** 2026-08-16 · **Status:** findings only. No pipeline code changed, no
model retrained, no SUMO run. **Subject:**
`docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md`.

**Question asked:** go through the plan; research how the split should be done,
whether it should be done at all, and what is most robust.

**Method.** Findings are labelled `[DOC]` (carried from the plan or repo
history), `[NEW]` (measured today) and `[LIT]` (external practice, cited).
Every `[NEW]` number is reproduced by
`python3 -m tools.research_direction_split_evidence`, which runs only on
tracked artifacts plus a regenerated `sumo/direction_split.json`.

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

### F9 `[NEW]` "Don't split at all" is a real option, and the solver already supports it

The plan never lists it. But `traffic_sim/demand/pfe.py` already has
`groups: list[tuple[list[int], float, float]]` — a band constraint on the sum
over an explicit route-index set, enforced identically to a measured edge in
both the LP (`solve_interval`) and the entropy IPF, and checked in
`_check_entropy_solution` (`x[js].sum()` within band).

A two-way total is directly expressible as **one constraint on the sum of both
carriageways**. Nothing is split; entropy maximisation plus the assignment
prior choose the direction, and the split becomes an *output* with a defensible
distribution rather than an *input* with a fabricated point value. That is the
Van Zuylen & Willumsen (1980) framing this codebase already uses for the OD
problem, applied one level down.

Two caveats, both concrete:

- **(a) Wrong rung.** `groups` are dropped at `RUNG_NOBND_TOL1`, the *first*
  relaxation rung, because they currently encode structure-preservation
  plausibility. A measured two-way sum placed there would yield before the
  measurement band widens — inverting the ladder contract in exactly the way
  CLAUDE.md documents being fixed three times already. It needs its own
  level-1 class (`measured_groups`), not a reuse of the structural slot.
- **(b) Double-counted routes.** A route touching both carriageways counts once
  in a group but twice in the true two-way sum. `drop_uturn_routes` already
  removes the obvious cases, so the residual is small and auditable — but it
  must be measured, not assumed.

---

## 3. The three questions

### Should we split at all?

**Only at 107, and "split" should mean "constrain what was actually measured"
rather than "invent a per-direction number".** The other five stations must not
be split — their measured direction already enters level 1 untouched, and
`demand/intake.py:367` correctly guards this.

Two defensible designs, most robust first:

**Option A — constrain the sum (most robust).** Give the PFE the two-way total
as a single level-1 constraint over both carriageways (F9). Nothing is
fabricated. The split stops being an assumption and becomes a model output.
Cost: a new measured-class group in `pfe.py` plus the double-count audit.

**Option B — anchored curve (smallest change).** Level from Gothenburg's own
52.3/47.7, shape from the pooled tidal curve, interval widened to the honest
0.193 (F4). Keeps today's file format and every downstream consumer; replaces
the entire LightGBM stack with roughly thirty lines.

These are not exclusive — B is a good default for the five single-direction
stations' *estimated opposite carriageway* (a level-2 bound, which should also
be widened per F4, since it is currently about twice too tight), while A is the
principled treatment of 107's genuinely-measured total.

### What is best?

**Level from local data × shape from the pooled curve, with an honest
interval.** Retire the similarity-weighted per-sensor quantile LightGBM stack:
leave-city-out says it costs accuracy on all three fold designs, and its own
shrinkage λ says three-quarters of what it asserts is noise.

### What is most robust?

**Option A**, because it removes the failure mode rather than estimating around
it — there is no transferred quantity to be wrong about. Failing that, Option B,
whose two components are separately validated and whose errors are bounded and
measurable. The current deployment is the least robust of the three: it is
simultaneously **too flat in the middle** (0.32× the validated signal) and **too
narrow at the edges** (0.55× the honest width) — understating what is known and
what is unknown at the same time.

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

5. **Add the no-split option to the option space (§"Beslut i korthet").** It
   currently frames the choice as central-profile vs ensemble. Neither is
   "don't split" — which F9 shows the solver already supports.

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
