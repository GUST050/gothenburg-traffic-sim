# Research: why simulated cars end their trips at/next to the sensors, and how to fix it

**Date:** 2026-07-12
**Trigger:** Gustav, watching the simulation: "many cars end their trip at the
sensor or just next to it."
**Status:** Diagnosed with real measurements (not visual impression), root
cause located in code, fixes researched against the traffic-modelling
literature and SUMO's own documentation. NOT yet implemented — this document
is the plan.

---

## 1. The measured evidence (all numbers from the real deployed artifacts)

Distance from each trip's DESTINATION edge to the nearest of the 7 measured
sensor edges (straight-line, network.geojson midpoints):

| population                                   | ≤100 m | ≤200 m | ≤1 000 m | median |
|----------------------------------------------|-------:|-------:|---------:|-------:|
| random network edge (5 000-draw baseline)    |  0.8 % |  2.1 % |   21.7 % | 1 744 m |
| candidate pool (`sumo/candidates.meta.json`, 11 998 trips) |  7.0 % | 11.8 % |   24.7 % | 2 497 m |
| **simulated vehicles** (`sumo/calibrated.rou.xml`, 22 301 veh) | **13.4 %** | **36.5 %** | **49.6 %** | **1 027 m** |

Trip-length distribution (straight-line origin→destination), vs RVU Västra
Götaland 2022-2023 (VGR Analys 2023:56, p.12 table 2):

| population              | 0–1 km | 1–5 km | 5–10 km | >10 km | median |
|-------------------------|-------:|-------:|--------:|-------:|-------:|
| candidate pool          |  2.5 % | 71.6 % |  25.9 % |    0 % | 3.54 km |
| **simulated vehicles**  |  **9.5 %** | 73.6 % |  17.0 % |    0 % | **2.18 km** |
| RVU (real behaviour)    |  9 %   | 31 %   |  19 %   |   41 % | ~7 km  |

(The 0 % >10 km share is a KNOWN, documented ceiling — the inner-city canvas
diameter is ~7.8 km, see build_candidates.py's own through-fraction help text.
The relevant signal here is the SHORT end and the medians.)

Two separate, compounding stages are visible in the numbers:

- **Stage 1 — candidate generation** puts destinations near sensors at 5-9×
  the random-edge rate (7.0 % vs 0.8 % within 100 m).
- **Stage 2 — PFE calibration** amplifies it ~3× further when choosing how
  many vehicles each candidate route carries (11.8 % → 36.5 % within 200 m;
  median trip shortens 3.54 → 2.18 km; the ≤1 km share nearly quadruples).

---

## 2. Root cause, stage 1: the deterrence kernel × the naturalness mask

`build_candidates.py` `generate_sensor_anchored_trips()` (lines ~1241-1293):

```python
far_w  = far_base * np.exp(-d_km / gravity_km)        # gravity_km = 2.6 km
masked = natural_far_end_weights(..., far_w)          # sensor must be ON the
f_pos  = rng.choice(..., p=masked / masked.sum())     # shortest anchor→dest path
```

Two interacting properties:

1. **The naturalness mask only admits destinations "beyond" the sensor** —
   `natural_far_end_weights` requires `anchor→m_u→m_v→dest` to equal the true
   shortest `anchor→dest` distance (tolerance 0.5 m). Every admitted
   destination therefore lies path-wise past the sensor exit.

2. **The deterrence kernel is a pure negative exponential**, which is
   monotone-DECREASING from d = 0. Its most-preferred member of any admitted
   set is always the CLOSEST one — i.e. the destination immediately past the
   sensor. This is exactly the observed artifact.

   Real trip-length distributions are NOT L-shaped: RVU's own bins put only
   9 % of trips under 1 km — the real distribution is unimodal with a mode of
   a few km. The transport-modelling literature is explicit that a pure
   negative exponential over-produces very short trips and that the standard
   correction is a **Tanner / gamma ("combined") deterrence function**
   f(d) = d^α · exp(−βd) with α > 0, which rises from zero before decaying
   (Wilson-family gravity models; see the deterrence-function comparison in
   Suprayitno 2018, IPTEK J. Eng.; NPTEL Ch. 8 Trip Distribution; Ortúzar &
   Willumsen). build_candidates.py's own history corroborates the misfit:
   "best fit across gravity_km 1-15 km still under 5 %" for RVU's 5.1-10 km
   share — no exponential scale parameter can reproduce RVU, because the
   SHAPE is wrong, not the scale.

3. **A secondary sampling bias in the same direction**: acceptance across
   anchor draws is uneven. For a destination just past a sensor's exit,
   `anchor→sensor→dest` is the shortest path for a large share of anchors
   (roughly the sensor's whole upstream catchment); for a destination 3 km
   onward it holds only for a narrow directional corridor of anchors. The
   sampler renormalizes within each anchor's masked set and redraws anchors
   with zero mass (`max_anchor_redraws`), so near-sensor destinations win
   disproportionately often ACROSS draws even before the kernel is applied.
   This is the classic **sampling-of-alternatives bias**; the established fix
   is an importance-sampling correction dividing each alternative's weight by
   its inclusion probability (Frejinger, Bierlaire & Ben-Akiva 2009,
   Transportation Research Part B 43(10) — sampling corrections are required
   "in order not to produce biased parameter estimates").

---

## 3. Root cause, stage 2: count-matching under-determination in PFE

Matching sensor counts is mathematically under-determined (Van Zuylen &
Willumsen 1980 and the whole ODME literature). Under-determined count-matching
solvers systematically prefer routes that satisfy the counted edge while
"costing" as little as possible elsewhere — and a trip that ends right after
the sensor consumes no bound capacity on any other edge, while a long trip
crossing many assignment-prior-bounded edges risks infeasibility. So mass
concentrates on short, sensor-terminating candidates. This project has already
documented the same pathology once before (CLAUDE.md: "pfe.py's parsimony
objective pulls every unconstrained edge to ZERO"), and SUMO's own
routeSampler — the tool pfe.py replaced — documents it verbatim as a known
problem area:

> "Many short routes (can be prevented by setting option `--min-count`)"
> — SUMO Turns/routeSampler documentation

with dedicated mitigation options (`--min-count`, `--minimize-vehicles`
"favor solutions with fewer vehicles passing multiple counting locations
rather than more vehicles passing fewer locations", `--total-count`,
`--optimize`), and the general caveat: "Selecting a set of routes to match a
given set of counts is generally an underdetermined problem … If there are
insufficient constraints on the solution space, the chosen solution may
appear implausible."

The ODME literature's parallel warning is that count-matching distorts the
STRUCTURE of the seed demand (trip-length distribution included) unless the
estimator is explicitly constrained to preserve it — the entropy/ME2 family
(Willumsen's "most likely trip matrix consistent with counts AND the prior")
exists precisely for this, and modern ODME practice measures seed-structure
preservation explicitly (structural-similarity indices, e.g. MSSIM/GSSI on OD
matrices — Behara, QUT thesis 2019; Afandizadeh Zargari et al. 2021).

**Measured confirmation that stage 2 is real in OUR solver**: the candidate
pool's ≤1 km share is 2.5 %, but PFE's per-vehicle assignment raises it to
9.5 % — the solver actively loads the short routes the pool offers it.

---

## 4. The fix plan (ranked; 1 and 4 are the substance, 6 is the guard)

## 4A. Decision - the recommended model in plain terms

### What the model must mean

Every simulated trip must still pass at least one measured sensor. That is the
project's evidence boundary. It does **not** mean that a trip should start or
end near a sensor.

For a purpose `p`, time `t`, origin `o`, destination `d` and route `r`, the
target model is conceptually:

```
P(o, d, r | p, t, passes a sensor)
  proportional to
  P(origin=o | t)
  x P(destination=d | p, t)
  x P(trip length | p, t)
  x P(route=r | o, d, t)
  x 1(route r passes at least one measured sensor)
```

The final indicator is only an observation condition. It must never be used as
an attraction toward a sensor or as a bonus for ending just beyond one.

### What must not be done

- Do not add free background traffic. Every emitted vehicle remains
  sensor-observable, as decided for this project.
- Do not add a cosmetic fade or hide vehicles that end near a sensor.
- Do not simply forbid all short trips; short trips are real and RVU includes
  them.
- Do not penalise a route merely because it naturally crosses a second sensor.
  A real vehicle must count at every sensor it physically passes.
- Do not use a fixed arbitrary distance from a sensor as the primary model.
  A minimum onward distance is only a temporary guard if validation still
  detects a pathological near-sensor spike.

### Recommended implementation order

1. **Fit the destination-distance prior to RVU by purpose and day type.**
   Replace the pure exponential with a non-zero-mode trip-length distribution
   (Tanner/gamma form or an empirical binned distribution). This preserves
   plausible short trips while assigning meaningful probability to cross-city
   trips. The fit must be checked against the final route-length distribution,
   not only candidates.

2. **Generate city-wide OD candidates first, then condition on sensor passage.**
   Origin mass remains DeSO residential mass; destination mass remains the
   purpose-specific POI/activity field. Route choice uses ordinary network
   cost and stochastic alternatives. Retain candidates whose natural route
   passes a sensor, but correct the sampling probability so a destination is
   not favoured just because it is naturally reachable from many upstream
   anchors through that sensor.

3. **Make PFE preserve the seed's trip-length structure.**
   Add route-length-bin priors/bands for the calibrated vehicle counts. PFE
   may adjust route use to satisfy sensor counts, but it may not turn a
   realistic candidate distribution into mostly sensor-adjacent destinations.
   The sensor count is a hard observation; trip-length bins are a strong,
   validated behavioural prior.

4. **Publish validation gates with every demand build.**
   Report purpose x time trip-length bins, onward distance after the last
   sensor, sensor-passage count per route, PFE-versus-candidate distribution
   drift and held-out-sensor fit. Reject or flag a build when it has good GEH
   but fails those structural checks.

### How we know it is working

There is no way to identify each real person's destination from six counters.
The acceptance criteria are therefore population-level, not a claim about an
individual car:

- Sensor counts remain within the declared calibration tolerance.
- The final trip-length distribution is close to RVU for the relevant purpose,
  time and day type, including a non-trivial long-trip tail within the network
  boundary.
- Destinations do not show an unexplained spike immediately downstream of
  sensors relative to the candidate/POI field.
- A leave-one-sensor-out test remains acceptable; a model that only matches
  sensors it was told about is not validated.

This is the best defensible solution with the available data: use sensors to
condition and calibrate a behavioural OD/route model, not to invent the OD
model itself.

### Primary sources for purpose, time, day type and trip length

- **Swedish source for local transfer:** [SCB / Transport Analysis RES](https://www.scb.se/en/finding-statistics/statistics-by-subject-area/transport-and-communications/transport-patterns/the-national-travel-survey-res/)
  states that the official Swedish travel survey covers when trips are made,
  mode and trip purpose. The project should request/use the corresponding
  trip-diary microdata where permitted rather than infer a Gothenburg joint
  length distribution from sensor counts.
- **Trip-level empirical schema:** the [UK National Travel Survey technical
  report](https://www.gov.uk/government/statistics/national-travel-survey-2024-technical-report/chapter-3-fieldwork-procedures-and-response-rate)
  states that its seven-day diary records origin, destination, purpose, mode,
  distance and time for every trip. This is the type of microdata required to
  estimate a joint `purpose x time x day type x length` model.
- **Published joint marginals for a defensible prior:** the official [NTS
  purpose tables](https://www.gov.uk/government/statistical-data-sets/nts04-purpose-of-trips)
  provide purpose by weekday start time, day of week and average trip length
  by purpose. The official [ad-hoc NTS table index](https://www.gov.uk/government/statistical-data-sets/ad-hoc-national-travel-survey-analysis)
  additionally lists car-driver purpose-by-start-time, purpose-by-trip-length
  and weekday/weekend rush-period purpose tables.

These tables justify modelling the dimensions jointly, but they must not be
multiplied as if independent or presented as Gothenburg ground truth. Until
local diary microdata supports the interaction, fit broad periods with partial
pooling toward the VGR/RVU purpose-level distance distribution and publish the
uncertainty.

### Stage-1 fixes (build_candidates.py)

**Fix 1 — replace the exponential deterrence kernel with a Tanner/gamma
kernel** `f(d) = (d/β)^α · exp(−d/β)`, α ≈ 1.5-2, β refit so the RVU
short-bin L1 (`trip_length_fit`, already implemented) is minimized. One-line
kernel change at the three `np.exp(-d_km / gravity_km)` sites + a refit run
of the existing calibrate_theta harness. Directly kills the mode-at-zero that
produces "ends just past the sensor". Highest impact-per-effort.

**Fix 2 — importance-correct the masked destination weights** (Frejinger-
style): precompute, per sensor m, the anchor-marginal inclusion probability
`q_m(dest) = Σ_anchor p(anchor) · natural(anchor, dest)` (vectorizable with
the existing all-pairs distance matrix D: one boolean matrix per sensor,
7 × ~2 250² ops, cheap), then sample destinations with weight
`f(d) · natural / q_m(dest)`. Removes the residual "near-sensor destinations
are accepted from everywhere" bias that survives Fix 1. Do AFTER measuring
Fix 1's effect — it may already be sufficient.

**Fix 3 (fallback only) — minimum onward distance past the sensor** for the
far-end draw. Crude, but a two-line guard if 1+2 still leave a visible spike
in the first ~200 m past a sensor. The literature analog is the minimum
trip-length / intrazonal-exclusion convention in trip distribution.

### Stage-2 fixes (pfe.py)

**Fix 4 — give the entropy solver a trip-length-distribution constraint**
(ME2-style): bucket candidate routes into RVU's length bins and add per-bin
band constraints (or a strong soft prior) on total assigned flow shares, so
count-matching cannot shorten the distribution relative to the pool. This is
the principled equivalent of routeSampler's `--minimize-vehicles`/
`--total-count` mitigations, expressed in the solver this project actually
uses. Implementation shape: bins are just "virtual edges" every route
belongs to exactly one of — the existing band-constraint machinery
(`bounds`) can host them without new solver code.

**Fix 5 (cheap partial alternative to 4)** — reuse SUMO's mitigation idea
directly: bias the entropy seed toward routes passing MULTIPLE counting
locations (our data has 7), i.e. scale the PSL seed weight up slightly per
extra sensor crossed. Less principled than Fix 4; only worth it if Fix 4's
bands prove fiddly to tune.

### The permanent guard

**Fix 6 — measure the distortion forever**: compute `trip_length_fit` (and
the destination-to-sensor-distance histogram) on the CALIBRATED output
weighted by assigned vehicle counts, not just on the candidate pool, and
write both into `demand_meta.json` + print at build time. Any future
regression of this exact bug becomes a visible number instead of a visual
impression. Matches the project's established honesty discipline.

### Verification target

After Fixes 1 (+2 if needed) + 4: rebuild the standard 2025-09-16 demand and
re-run the destination-distance diagnostic. Expect the simulated ≤200 m share
to fall from 36.5 % toward ~5-10 %. It will NOT reach the 2.1 % random
baseline — and should not: sensor-anchored demand legitimately concentrates
TRAFFIC near sensors (the corridor should be busy); the bug is only that
trips END there. The pass criterion is visual (vehicles continue past the
sensors) plus the ≤200 m destination share and a `trip_length_fit` L1 on the
calibrated output no worse than the pool's.

### Explicitly out of scope

The 0 % >10 km share. That is the canvas-diameter ceiling, documented since
2026-07-08, and no kernel change can fix it — only a larger network or
explicit external-world OD anchoring could, which is a separate decision.

---

## 6. IMPLEMENTATION RESULTS (2026-07-12, same day — measured, not planned)

Implemented per §4A's order, with every step measured on the real graph
before the next. Two findings materially updated the plan itself:

**Finding A — the kernel was nearly irrelevant under the OLD sampler.** A
20-combo (α, β) sweep (tools/fit_deterrence_kernel.py, --fit-only runs on
the real graph) moved the generated destination near-sensor share only
12.2% → 9.6% (baseline 1.8%) and left the RVU L1 flat (~0.84-0.88) across
the entire grid. Fix 1 alone was NOT the dominant stage-1 mechanism.

**Finding B — a within-anchor importance division was not enough either.**
Implementing the Frejinger q-correction inside the existing per-sensor
sampler moved the share only 11.1% → 10.4%. Root cause of the residual: the
sampler pre-commits each draw to ONE sensor and renormalizes within that
sensor's admitted set, so an anchor whose only admissible destinations sit
next to the sensor emits exactly those with probability 1 — the per-anchor
renormalization is itself the bias, and no within-anchor reweighting can
remove it.

**The fix that worked — conditional joint sampling (§4A step 2, implemented
as natural_sensor_masks + rejection acceptance in
build_candidates.generate_sensor_anchored_trips):** draw the anchor from its
unconditioned field, compute city-wide destination weights, mask by the
UNION of all sensors' naturalness, and accept the anchor with probability
(sensor-passing mass / total mass) — exactly
P(anchor, dest | natural route passes ≥1 sensor). Sensor attribution
(via=, quota bookkeeping) moved AFTER the draw. Measured, generated pool at
the old kernel (α=0, β=1.8): near-sensor share **11.1% → 3.0%** (baseline
1.8%). Generation cost 16 s → ~100 s (real rejection retries) — acceptable
for a once-per-build stage.

**Kernel re-sweep under the fixed sampler** (9 combos): the kernel now
matters (the mask no longer dominates). The pre-declared lowest-L1 rule
selected α=3, β=2.6 (L1=0.746, near-sensor 1.1%) — but it wins by erasing
the 0-1 km bin to 0.1% vs RVU's 15%, violating §4A's explicit "do not
simply forbid all short trips". DOCUMENTED DEVIATION from the pre-declared
rule: deployed default is **α=1.5, β=1.8** (mode 2.7 km, inside §4A's own
recommended α≈1.5-2 range), whose near-sensor share lands exactly at the
all-edges baseline (**1.9% vs 1.8%** — no unexplained spike, §4A's actual
acceptance criterion) while still emitting short trips. L1 was rejected as
the selector because it structurally rewards short-trip erasure on this
canvas (the 1-5 km bin dominates all combos at ~90-95% for geometric
reasons; see "explicitly out of scope").

**Guard (Fix 6) implemented at both stages**: trip_length_fit +
destination_sensor_proximity now computed on the generated pool (written to
trip_length_fit.json) AND on the calibrated per-vehicle output
(build_sumo_demand.calibrated_structure_report → demand_meta.json's
calibrated_structure + printed at build time).

**Stage 2 (PFE) — three diagnostic builds, mechanism pinned down, then
fixed (§4A step 3):**

- With the stage-1 fix alone: pool 1.9% (== 1.8% baseline, fully
  compliant) but calibrated **19.4%** — the entire remaining spike was
  PFE's count-matching. Mechanism candidates tested empirically:
  - assignment-prior bounds: RULED OUT (a --no-assignment-prior build
    still showed 18.3%);
  - the actual mechanism, measured: 87 of 948 active shapes ended near a
    sensor and carried 4 550 vehicles at 52 veh/shape vs 24 for all
    others — a route crossing exactly one sensor and ENDING is a free
    variable for closing that sensor's hard count band without touching
    any other sensor's band, so under-determined count-matching loads
    exactly those routes. The reference field: the POI/activity mass puts
    2.6-3.5% of destination weight within 200 m of sensors (homes 1.7%),
    so ~19% was a genuine ~6× unexplained spike.
- **Fix implemented**: `groups` — a band constraint over an explicit
  route-index set, structurally identical to the existing edge bounds —
  added to solve_interval_entropy, solve_interval (LP fallback),
  solve_interval_with_relaxation (dropped at the same ladder stage as
  bounds: never at the counts' expense), AND repair_integer_bounds.
  build_sumo_demand caps the near-sensor-ending group's assigned share at
  2× its pool share (7.4%), two-pass per quarter (the band needs an
  absolute ceiling, known only after a first solve).
- **The integer stage mattered as much as the continuous one**: with only
  the continuous cap, the published result was still 13.7% and 95 of 96
  quarters violated the cap — largest-remainder rounding hands whole
  vehicles to the individually-largest fractional values, which are
  exactly the near-enders the cap squeezed into fewer, relatively larger
  shares. Extending the existing post-rounding MILP repair
  (repair_integer_bounds) with the same group band closed the leak.

**FINAL MEASURED RESULT (full 2027-10-22 forecast-day rebuild):**

| stage                      | destinations ≤200 m of a sensor |
|----------------------------|--------------------------------:|
| original (before any fix)  | **36.5 %** |
| activity/POI field (expected) | 2.6-3.5 % |
| generated pool (after stage-1 fix) | 1.9 % (baseline 1.8 %) |
| calibrated, continuous cap only    | 13.7 % |
| **calibrated, full fix**   | **7.5 %** (designed cap 7.4 %) |

GEH<5 stayed **100.0%** on all three variants (0 infeasible intervals);
calibrated trip-length shares [7.5, 68.9, 23.7]% vs RVU [15.3, 52.5,
32.2]% (L1 0.33); publish cost +5 s/variant for the MILP group repairs;
total PFE stage 534 s (was 300 s — the per-quarter two-pass re-solves).
The residual 7.5-vs-3.5% gap is the deliberate 2× cap slack
(DEST_GROUP_CAP_MULT) — tightening it is a one-constant change if the
visual result warrants it.

---

## 7. COMPLETION PASS (2026-07-13) — full §4A audit, gaps closed

Gustav asked whether §4A prescribed anything better/beyond what was
implemented. Honest audit answer: the §4A core (conditional sampling) was
already in and had proven decisively better than this doc's own original
Fix-2 (importance division). Three prescribed items were still missing;
two are now implemented, measured, and gated:

**§4A step 3, length-bin bands (was: spatial group only).** Measured
first: even with the near-sensor cap in place, PFE inflated the 0-1 km
bin 1.2% (pool) → 7.5% (calibrated) — the same under-determination
family, exactly the drift step 3 prohibits. `structure_groups_for_shapes`
now emits per-length-bin caps (RVU bin edges, 2× pool share, ceilings
only, no-op bins omitted) through the same groups machinery (continuous
two-pass + integer MILP repair). Result: 0-1 km share 7.5% → **2.4%**
(pool 1.2%, cap 2.3% + the per-quarter 2-vehicle integer floors).

**§4A step 4, the full gate set (was: proximity + aggregate length
only).** `calibrated_structure_report` now also measures:
- **onward distance after the LAST sensor the route actually crosses** —
  §4A's own metric, sharper than nearest-sensor proximity (immune to a
  destination that happens to sit near a DIFFERENT sensor the route never
  crossed). Final build: **median 2 902 m onward, only 6.0% under
  200 m** — vehicles visibly continue past the sensors.
- **sensor-passage count per route** (final build: 86% cross 1 sensor,
  12% cross 2, 1.6% cross 3+),
- **drift FLAGS vs the pool** (calibrated > 2.5× pool on proximity,
  onward-under-200m, or under-1km share ⇒ WARNING printed + stored in
  demand_meta.json's structure_flags). The final build carries **no
  drift flags**.

**Honest tension surfaced by the length-bin cap**: aggregate RVU L1
worsened 0.33 → 0.40, because RVU actually wants MORE 0-1 km trips
(15.3%) than the pool contains (1.2%) — but §4A step 3's principle is
that CALIBRATION must not invent structure the seed doesn't have; a
short-trip deficit in the pool is a GENERATION question (the
purpose-level prior below), not something PFE should backfill from
count-fitting freedom.

**§4A step 1's full form — length priors per purpose × day type —
IMPLEMENTED (2026-07-13)** after Gustav challenged the claim that it
needed unavailable data ("why can you not implement this with found data
online") — he was right; the claim was made without actually searching:
- **Trafikanalys "Resvanor i Sverige 2023"** (official national RVU,
  published Excel: trafa.se/globalassets/statistik/resvanor/2023/
  resvanor-i-sverige-2023.xlsx), Tabell 3 — distance per trip by MAIN
  PURPOSE and mode, car: arbete/tjänste/skola 24 km (±3), service/inköp
  30 km (±8), fritid 56 km (±9), samtliga 37 km (±4).
- **VGR Analys 2023:56 itself** (fetched: mellanarkiv-offentlig.vgregion
  .se/.../RVU231103.pdf), Tabell 3 — LOCAL distance-to-work/school
  distribution (0-3/3-5/5-10/>10 km = 19/14/19/46%), confirming the
  purpose ordering locally.
Implementation (build_candidates.PURPOSE_LENGTH_SCALE): national absolute
distances do NOT transfer to a 7.8 km canvas, so only the between-purpose
RATIOS are used, shrunk 50% toward 1 (partial pooling — §4A's own
prescription; the dirsplit national-to-local precedent; service's CI is
±27%), then normalized so the average-WEEKDAY purpose mix preserves the
aggregate calibration exactly (a unit test asserts mean == 1.0 — the
first draft claimed "≈1" and the test caught it failing at commute
hours). Final scales on the kernel's β: arbete 0.90, service 0.99,
fritid 1.38. Day-type/hour variation arrives through composition —
P(length | purpose) × P(purpose | hour, day type) — so the leisure-heavy
weekend mix yields ~1.11× mean length by design, the survey-implied
signal, not an artifact.

**§4A's LOSO acceptance criterion — run 2026-07-13, with LOSO's fold
calibration first brought into line with the deployed pipeline**
(validate_sim.calibrate_fold_parallel now applies the SAME
structure-preservation groups as deployment — the LOSO/production
config-mismatch class this project has fixed once before):
- Final ratios (full pipeline rerun 2026-07-13 with every fix, including
  the purpose scales, in both the candidates AND the fold calibration):
  **min 0.05 / median 0.78 / max 1.95** — per fold: 107-N 0.71,
  107-S 1.95, 1074 0.50, **1076 0.05**, 133 1.30, 134 0.78, 2276 0.81.
  (Previously documented: 0.830 / 0.896 / 2.410 — measured on the
  PRE-fix pipeline.)
- HONEST INTERPRETATION, not spin: the median held-out recovery DROPPED
  (0.90 → 0.78). The extreme fold (sensor 1076, edge
  30420757_30421744_0, ratio 0.05) is the strongest evidence for why:
  that edge is immediately upstream of the exact hot edges the amplified
  sensor-terminating shapes used to drive through — the OLD,
  better-looking recovery there was substantially powered by the
  artifact itself (count-matching freely dumping vehicles onto
  sensor-adjacent routes also inflated flow past neighbouring corridor
  sensors). Removing invented flow reduces apparent generalization
  measured against sensors that sit inside the same two compact
  clusters. The new numbers are the honest baseline for the fixed
  pipeline; the old ones should no longer be quoted for it.

**Final deployed build (2027-10-22 forecast, everything active):**
destinations within 200 m of a sensor **7.3%** (baseline 1.9%, field
2.6-3.5%, was 36.5%); onward after last crossed sensor **median
2 902 m**, only 5.9% under 200 m; 0-1 km share 2.1%; sensor passages
86%/14%/0.3% (1/2/3+); GEH<5 **100.0%** on all three variants, 0
infeasible intervals; **no drift flags**.

---

## 8. G1 CLOSED (2026-07-13) — the 1076 fold: artifact hypothesis PROVEN

§7 hypothesized that the old, better-looking LOSO recovery at sensor 1076
was "substantially powered by the artifact itself". PLAN.md step G1
demanded that be tested, not asserted: rerun the PRE-fix pipeline (a clean
clone at commit be2bb8b, the commit that produced the old report; SUMO net
+ gitignored inputs copied in; full `make demand` + a single-fold LOSO
driver mirroring validate_sim.main()), then decompose every vehicle
crossing 1076's edge (30420757_30421744_0) in each artifact: how far does
the route CONTINUE after the crossing, and does it also cross any OTHER
measured sensor (i.e., did any remaining count band actually need it)?

**Replication first**: the pre-fix fold reproduced the checked-in report
to three decimals — ratio 1.516 (7 307 simulated vs 4 820 measured), PFE
GEH 100%. The clone methodology is sound.

**Decomposition of everything that crosses 1076** (whole-day 2025-09-16):

| artifact | crossing veh | onward median | ends <500 m | also crosses another sensor |
|---|---|---|---|---|
| PRE-fix deployed (all bands) | 4 820 | **162 m** | **92.8%** | 0.0% (2 veh) |
| PRE-fix LOSO fold (1076 out) | 7 307 | **188 m** | **99.6%** | **0.4%** (28 veh) |
| POST-fix deployed (all bands) | 5 384 | 1 652 m | 23.8% | 7.4% |
| POST-fix LOSO fold (1076 out) | 242 | 1 415 m | ~33% | 67.8% |
| POST-fix candidate pool | 1 812 | 4 406 m | 2.7% | 33.8% |

**Verdict — both G1 branches answered:**

1. **The old recovery was artifact, essentially in full.** With 1076 held
   out, the pre-fix pipeline pushed 7 307 vehicles across it of which
   99.6% served NO other sensor's band and 99.6% evaporated within 500 m
   of the crossing (median 188 m onward) — vehicle-shaped free variables,
   not corridor traffic. Excluding near-terminating routes collapses the
   old ratio 1.516 → ~0.006. The old fold didn't "recover" 1076; it
   over-shot it 52% with phantom trips. (Note the old number was never
   0.83-good at this fold anyway — 1.516 with GEH 33% was already poor;
   the artifact made it LOOK like flow was present.)
2. **The fix lost no real corridor continuation.** The post-fix deployed
   build serves 1076's full measured count with routes whose onward
   median is 1 652 m (was 162 m) — real through-traffic — and the pool
   offers abundant continuation routes across 1076 (median 4 406 m
   onward). The post-fix fold's 0.05 is honest parsimony: 92.6% of
   1076's deployed flow crosses no other sensor, so when its band is
   removed, no remaining measurement requires that flow, and the 242
   vehicles that survive are almost exactly the genuine shared-corridor
   share (400/5 384 deployed). DEST_GROUP_CAP_MULT and the conditional
   sampling need NO retuning on this evidence.

**What 0.05 means going forward**: LOSO ratio at 1076 measures the
sensor's informational isolation, not model quality — the other five
stations genuinely tell us almost nothing about Skånegatan S. That is
exactly the honesty the per-edge confidence layer is supposed to carry.
Corollary for E3 (publication gates): the current post-fix baselines ARE
the reference baselines; no demand retuning precedes the run registry.

---

## 5. Sources

- SUMO routeSampler documentation (short-route problem area, `--min-count`,
  `--minimize-vehicles`, underdetermination): https://sumo.dlr.de/docs/Tools/Turns.html
- Willumsen (1978/1980), entropy-maximising OD estimation from counts;
  Van Zuylen & Willumsen, "The most likely trip matrix estimated from traffic
  counts", Transportation Research Part B (1980):
  https://www.sciencedirect.com/science/article/abs/pii/0191261580900089
- Frejinger, Bierlaire & Ben-Akiva (2009), "Sampling of alternatives for
  route choice modeling", Transportation Research Part B 43(10) — importance-
  sampling correction for biased choice-set generation:
  https://www.sciencedirect.com/science/article/abs/pii/S0191261509000381
- Suprayitno (2018), "Searching the Correct and Appropriate Deterrence
  Function...", IPTEK The Journal of Engineering — negative-power /
  negative-exponential / Tanner deterrence comparison:
  https://iptek.its.ac.id/index.php/joe/article/view/3762
- NPTEL Ch. 8, Trip Distribution (gravity model deterrence function forms):
  https://priodeep.weebly.com/uploads/6/5/4/9/65495087/lec-8.pdf
- Behara (2019), QUT PhD thesis, OD estimation + structural comparison of OD
  matrices: https://eprints.qut.edu.au/132444/
- Afandizadeh Zargari et al. (2021), structural similarity (MSSIM/GSSI) for
  OD matrices, J. Advanced Transportation:
  https://onlinelibrary.wiley.com/doi/10.1155/2021/9968698
- RVU Västra Götaland 2022-2023 (VGR Analys 2023:56) — already the project's
  behavioural ground truth (build_candidates.py header).
