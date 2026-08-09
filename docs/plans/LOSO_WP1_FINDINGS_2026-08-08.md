# WP0/WP1 result — why the junction stations fail, and what to treat next

**Date:** 2026-08-08
**Plan:** `docs/plans/LOSO_POOL_PICKER_IMPROVEMENT_PLAN_2026-08-08.md`,
work package 0 and the read-only part of work package 1
**Evidence:**
`validation/corrected_loso_sweep_v2_summary.json`,
`validation/corrected_loso_sweep_v3_summary.json`,
`validation/loso_route_support_audit_v1.json`
**Production behavioral change made:** none. V7 changes validation-only
integer publication; no candidate generation, production PFE, assignment
field, network or published product artifact was modified.

## Second correction — integer publication is the measured failure boundary

This section supersedes the earlier claim below that the fold's extra total is
already required by the continuous picker. Paired validation-only traces on
seeds `20260808` and `20260811` show the opposite for 134 and 2276: their
continuous held-edge flows are stable and much lower, then integer publication
creates the overprediction.

| Seed | Station | Continuous entries | Direct rounded | Published | Simulated |
|---:|---:|---:|---:|---:|---:|
| 20260808 | 107 | 7,051 | 4,622 | 6,867 | 6,863 |
| 20260808 | 134 | 2,748 | 6,102 | 7,771 | 7,771 |
| 20260808 | 2276 | 5,979 | 10,215 | 11,033 | 11,032 |
| 20260811 | 107 | 10,347 | 9,928 | 12,028 | 12,022 |
| 20260811 | 134 | 2,576 | 5,851 | 7,927 | 7,926 |
| 20260811 | 2276 | 5,972 | 10,832 | 11,911 | 11,909 |

`round_preserving_measured` supplies integer vehicles to routes that close the
remaining training-sensor counts. Many alternatives have identical incidence
on those active counts but different incidence on the held edge. The optional
structure/purpose repair then moves more vehicles within the same active
constraint signatures. It adds 818–2,076 further held entries for the
junction folds. Strict purpose provenance does not replace geometry, and SUMO
changes the published daily total by at most six vehicles. The failure is
therefore discrete route selection, not purpose replacement or timing.

The trace also found a validation-contract gap: v6 solved against held-derived
assignment ceilings continuously but passed them to integer publication as
diagnostic-only bounds. Protocol v7 selectively enforces those ceilings and
fails closed without enforcing every unrelated wide assignment bound. A paired
treatment run passed both seeds but was quality-negative/mixed:

| Station | v6 ratios | v7 ratios | Decision |
|---:|---:|---:|---|
| 107 | 1.071 / 1.876 | 1.220 / 2.005 | worse; spread remains |
| 134 | 2.474 / 2.523 | 2.491 / 2.530 | unchanged |
| 2276 | 2.466 / 2.662 | 2.425 / 2.601 | only 0.041–0.061 better |

Retain v7 as a validation-contract correction, but reject it as the quality
treatment and do not spend a six-seed campaign on it. The existing 5× ceiling
is too wide and mostly non-binding; it must not be tuned against held counts.
The next experiment should change the discrete route-selection objective or
support policy using non-held criteria, screened first on the same two seeds.
Tracked evidence:
`validation/loso_picker_decomposition_v1.json` and
`validation/loso_integer_publication_v7_pair.json`. Production PFE and warming
sources remain unchanged.

## Review correction — supersedes the fold-mechanism interpretation below

A code review after this read-only audit found that the production constraint
table was incorrectly read as the active constraint table for each LOSO fold.
For folds 134 and 2276, the reverse direction prior sourced from the held
sensor is removed at `loso.py` before the picker runs. Its reverse edge was,
however, still treated as covered while the assignment field was formatted,
and only the registered measured component edge was explicitly added back.
The inflating feeder therefore had **neither the soft prior nor a hard
ceiling**, rather than “only a weak soft prior”. This distinction materially
changes the mechanism, although it does not change the measured v5 ratios or
the conclusion that those feeder movements were unconstrained.

The validation-only v6 implementation adds every edge whose coverage came
from the held sensor: measured components, reverse-direction-prior edges, and
affected corridor-prior edges. No production assignment code was changed.
The audit now labels its constraint table as production-only and binds the
candidate metadata, prior inputs, diagnostic code, edge-data files, gate-pair
composition, and selected movement flows by quarter.

The paired six-seed v6 campaign is now complete and frozen. Every required
edge received a ceiling in all runs, proving that the isolation contract is
fixed. It did **not** improve 134 or 2276: their per-seed daily ratios are
byte-for-byte numerically identical to v5, with medians 2.472 and 2.531.
Directional stations 1074, 1076 and 133 are also unchanged. Only the two-way
station-total evaluation for 107 moves slightly (median 1.231 to 1.243), and
its v6 range 1.071–1.876 exposes substantial pool sensitivity. TAG median is
unchanged at 68.5% and all six seeds remain below the >85% guideline. Therefore
the v5 omission was a real validation/provenance defect, but it was not the
cause of the junction overprediction. The earlier K3 recommendation below is
superseded unless a new non-held structural diagnostic independently supports
it; the next work is the planned picker/pool decomposition.

---

## 1. What was frozen (WP0)

`validation/corrected_loso_sweep_v2_summary.json` binds the corrected six-seed
result to the ignored evidence root that produced it. The acceptance gate in
plan §6 passed in full: six unique seeds, six distinct candidate-pool hashes,
protocol `loso_pfe_meso_v5` in every run, every held component edge given an
assignment ceiling, manifest status `complete`, per-seed TAG shares equal to
their own TAG reports, and all ten bound input hashes still matching the
current worktree bytes.

TAG satisfactory share: median **68.5%**, range **62.2–72.0%**, 0/6 above the
strict >85% guideline. Median daily ratios — 107 1.231, 1074 0.804, 1076
0.882, 133 **0.786**, 134 **2.472**, 2276 **2.531**. (The frozen artifact
stores 133 as 0.7855, the exact mean of the two middle draws; the plan text
rounded it to 0.786.)

---

## 2. The junction is underdetermined, exactly and measurably

Node `26355153` has 4 incoming and 4 outgoing legs. Three are measured: 133
incoming, 134 and 2276 outgoing.

**Conservation alone proves a majority of the outflow is unobserved.**
Measured outflow 7,614 veh/day (4,473 + 3,141) exceeds measured inflow 3,569
veh/day by **4,045 veh/day**, so at least **53.1%** of the measured outflow
must enter on legs no sensor sees. This assumes node conservation with no
parking or storage inside the junction, and nothing else.

**Route support at the junction is concentrated in six movements.** Of 16
legal `(incoming, outgoing)` movements, **10 carry zero candidate routes** —
including every movement touching the `165154328` approach/exit pair, which
the pool never uses at all. Over the six supported movements the measured
legs give rank 3, leaving **nullity 3**; holding out any one of the three
stations drops it to rank 2, **nullity 4**. Measuring either feeder leg
(`91615277_26355153_0`, `96523321_26355153_0`) or the `26355153_26842525_0`
exit each buys exactly one rank; measuring the `165154328` pair buys nothing,
because no route uses it. This is the ranked answer to "where would another
sensor help most here" — recorded as a finding, not as a data request
(external data was closed by the project owner on 2026-07-20).

---

## 3. The mechanism behind 134 and 2276 — measured, not inferred

Comparing each fold's published routes with the fully-measured baseline build
(same pool, same solver, one more constraint) isolates what losing the held
constraint did:

| Fold | Vehicles vs baseline | Inflating movement | Movement vs baseline | Held leg vs baseline |
|---|---:|---|---:|---:|
| 2276 | ×1.265 | `96523321_26355153_0 → 26355153_91615277_0` | **×3.28** | ×2.49 |
| 134 | ×1.141 | `91615277_26355153_0 → 26355153_96523321_0` | **×3.66** | ×2.61 |
| 133 | ×1.000 | — | ≤×1.47 | ×0.77 (held leg falls) |

In both failing folds essentially the whole extra vehicle count lands on one
movement out of one unmeasured feeder leg. Fold 2276 adds 5,742 vehicles over
baseline and 5,370 of them arrive on that single movement.

**Why that movement is free.** `calibrate_assignment_priors` skips any edge
already `covered` by a measurement, a direction prior or a corridor prior, so
an edge carrying only a weak soft prior receives **no hard plausibility
ceiling at all**:

| Leg | Constraints reaching it | Prior target | Prior median weight | Hard upper bound |
|---|---|---:|---:|---|
| `91615277_26355153_0` | direction prior (from 2276) | 4,435/day | 0.043 | **none** |
| `96523321_26355153_0` | direction prior (from 134) | 3,139/day | 0.087 | **none** |
| `165154328_26355153_0` | assignment ceiling | — | — | 480/day |

The folds push those legs to 8,852 and 7,902 veh/day — **2.0× and 2.5× their
own soft prior**, with nothing above them to clip it. The two legs that carry
no traffic in any build are the only ones with a hard ceiling.

**Ruled out by the same evidence.** Timing displacement is not the cause:
planned-departure minus simulated-entry totals differ by ~87–144 vehicles on
junction legs carrying 3,000–8,000, i.e. under 4%. Route plausibility is not
the cause: median cost stretch versus the fastest legal path is 1.09–1.13 at
every station, and the inflating movements have 150 and 320 supporting
variables, so the pool can serve them.

**Why 133 behaves differently.** 133 is the only station whose crossing
routes are **100% anchored** — every one of its ~1,296 variables also crosses
another measured station (1,095 of them cross 2276 on the straight-through
movement). Its fold therefore cannot inflate; it can only shrink, which is
exactly the stable mild underprediction observed (0.746–0.825). The other
five stations have 12–71% of their crossing routes anchored by no other
station.

---

## 4. Pool findings that bear on the treatment choice

Median across the six seeds:

| Station | Crossing variables | Unanchored | Single-route OD groups | Effective alternatives (median OD group) | Median cost stretch | Orphan tour legs |
|---|---:|---:|---:|---:|---:|---:|
| 107 | 2,355 | 71.2% | 90.0% | 1.0 | 1.133 | 14.4% |
| 1074 | 1,297 | 61.1% | 89.1% | 1.0 | 1.122 | 13.4% |
| 1076 | 1,622 | 56.2% | 89.3% | 1.0 | 1.110 | 13.3% |
| 133 | 1,296 | **0.0%** | 91.7% | 1.0 | 1.113 | 18.1% |
| 134 | **520** | 60.4% | 89.3% | 1.0 | 1.094 | 18.0% |
| 2276 | 1,241 | 12.4% | 92.4% | 1.0 | 1.107 | 19.5% |

- Route choice is effectively absent: ~90% of OD groups crossing any station
  have exactly one route, and the median effective-alternatives count is 1.0
  everywhere. This replicates the earlier finding on the corrected pools.
- Station 134 has 2.5–4.5× less support than the others, and its crossing
  routes are the most mutually overlapping (median pairwise Jaccard 0.197).
- **Seed instability is compositional, not volumetric.** Crossing-variable
  counts are stable across seeds (range ratio 1.03–1.13), but the pairwise
  Jaccard between seeds' crossing route SETS is only **0.10–0.14**. Each seed
  redraws almost the entire route set at a sensor while keeping its size. That
  is the source of the 0.99–1.88 spread at station 107 and it weakens every
  paired comparison the plan intends to run.

---

## 5. Decision: what to treat next

**Not P1, P2 or P3, and not a data request.**

- **P2 (multipath alternatives) is not indicated for the failing stations.**
  The inflating movements already have support and the routes carrying it are
  near-shortest. Adding alternatives would spread the same unbounded flow over
  more routes without bounding it. P2 remains a reasonable later treatment for
  the 90% single-route OD groups, but it does not address the measured cause.
- **P1 and P3 address composition, not the ceiling asymmetry.** Neither would
  have stopped one movement from tripling.
- **A data request is out of scope by the owner's standing decision**
  (2026-07-20, external data closed permanently). Plan §8.3 governs: topology-
  only movement support, exact conservation, and information-gain rankings for
  future sensors are allowed, and are what section 2 above delivers.

**Recommended next slice, in order:**

1. **Picker decomposition on one fold (plan §7.3), before any campaign.** The
   fold total grows 14–26%, so some constraint is *requiring* the extra
   volume rather than merely permitting it. Identify it — purpose quota,
   through-share target, structure group, or prior pull — with the opt-in
   diagnostics sink, byte-identical when disabled. This is cheap and decides
   whether step 2 is sufficient on its own.
2. **Treatment K3, in the specific form the evidence supports:** an edge
   covered by a soft prior must still receive a hard plausibility ceiling.
   Priors and ceilings are not alternatives — the current `covered` rule
   treats them as if they were. The ceiling must come from the structural
   assignment field rebuilt with the held sensor absent (already the corrected
   protocol), with its own uncertainty model rather than a retuned `5×`
   multiplier, and it must never be tuned against held counts.
3. **P4 (seed-stability contract) as infrastructure.** Jaccard 0.10–0.14 at
   stable counts means paired six-seed arms are comparing largely different
   route sets, which is exactly what P4 exists to fix. Credit it with
   experiment validity, not with LOSO quality.

Promotion of anything in step 2 still requires the full paired six-seed
campaign and the §11.5 thresholds. Nothing here promotes a change.

---

## 6. Reproduce

```bash
python3 tools/freeze_loso_baseline.py --check
python3 tools/audit_loso_route_support.py --fold-artifacts sumo --fold-seed 42
MPLCONFIGDIR=/tmp/gs-mpl PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  tests/test_freeze_loso_baseline.py tests/test_route_support.py \
  tests/test_validate_sim.py tests/test_validate_dmrb.py
```

The audit recovers each seed's exact candidate pool from
`sumo/candidate_cache` by the SHA-256 the sweep recorded, so it reads the
pools the sweep actually used and never regenerates or overwrites the live
one. Fold artifacts are seed 42's published folds only, and are labelled that
way in the evidence.
