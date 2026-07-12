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
