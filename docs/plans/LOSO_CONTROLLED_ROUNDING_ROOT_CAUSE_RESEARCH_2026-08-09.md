# LOSO picker root cause and controlled-rounding research

> Production update, 2026-08-09: the validation-only v8 treatment described
> below has now been replaced for new evidence by the production joint integer
> publisher and protocol `loso_pfe_meso_v9_joint_integer`. It canonicalises
> sensor order, includes retained hard/purpose constraints jointly, tries exact
> margins before the declared rung band, treats unsupported edges as explicit
> pool-coverage defects and fails before opening a staging route file on a real
> hard conflict. Targeted final-source results are 107 ratio 2.029 (GEH<5
> 16.7%) and 2276 ratio 2.044 (25.0%); the integer defect is fixed but absolute
> LOSO quality is not. See
> `validation/loso_production_joint_v9_targeted_20260809.json`. This production
> change supersedes annual plan key `adf91205…`; new verified key
> `6d466dfe…` has passing preflight, with population not started.

**Date:** 2026-08-09
**Status:** Root cause reproduced; validation-only treatment passed a targeted
two-seed screen for stations 134 and 2276; production decision not made.
**Primary evidence:**
`validation/loso_picker_decomposition_v1.json` (schema 3) and
`validation/loso_controlled_rounding_v8_pair.json`.

## 1. Outcome

The dominant 134/2276 failure is not SUMO travel-time displacement and is not
primarily the continuous PFE solution. It is a LOSO-dependent integer
publication artifact in `round_preserving_measured()`.

The current algorithm first performs one global largest-remainder rounding and
then repairs each active measured edge sequentially for four passes. Its first
tie-break prefers routes touching the fewest **currently active measured**
edges. When station 134 or 2276 is held out, a route from measured station 133
through the held branch touches only one active measured edge. A comparable
route from 133 through another measured branch often touches two. The held
route therefore receives thousands of correction units even though the held
station is absent from the objective and constraints.

This is leakage-free but still validation-dependent: removing a sensor changes
the combinatorial tie-break and makes routes through that omitted sensor look
artificially exclusive.

Replacing that sequential choice with a joint integer projection produces a
large, replicated improvement after the unchanged purpose/structure publisher
and SUMO:

| Seed | Station | v7 ratio | Controlled ratio | Ratio change | v7 GEH% | Controlled GEH% |
|---:|---:|---:|---:|---:|---:|---:|
| 20260808 | 134 | 2.491 | 1.496 | -0.995 | 33.3 | 54.2 |
| 20260811 | 134 | 2.530 | 1.590 | -0.940 | 29.2 | 45.8 |
| 20260808 | 2276 | 2.425 | 1.868 | -0.557 | 25.0 | 16.7 |
| 20260811 | 2276 | 2.601 | 1.893 | -0.708 | 12.5 | 25.0 |

The treatment is therefore worth continuing, but it is not yet good enough to
promote. Station 2276 remains badly overpredicted, and one seed's GEH share
worsens even while its daily ratio improves. A six-seed campaign now would
measure the remaining defect more precisely without first explaining it.

## 2. Exact local evidence

### 2.1 Publication-stage decomposition

The paired raw trace reconstructs each of the following stages from the exact
candidate pool and archived route artifacts:

| Seed | Station | Continuous | Global LR | Legacy direct | Joint controlled | v7 published | SUMO |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 20260808 | 134 | 2,748 | 3,734 | 6,102 | 3,346 | 7,771 | 7,771 |
| 20260811 | 134 | 2,576 | 3,814 | 5,851 | 3,400 | 7,927 | 7,926 |
| 20260808 | 2276 | 5,979 | 7,432 | 10,215 | 8,386 | 11,033 | 11,032 |
| 20260811 | 2276 | 5,972 | 8,022 | 10,832 | 8,683 | 11,911 | 11,909 |

The `Joint controlled` column is an offline counterfactual at the first integer
stage. The v8 ratios in §1 are stronger evidence because v8 feeds the joint
counts through the real unchanged downstream repair and SUMO.

The exact v8 post-publication replay now separates the remaining mechanisms:

| Seed | Station | Continuous | Joint controlled | Final v8 published | Downstream change | Simulated |
|---:|---:|---:|---:|---:|---:|---:|
| 20260808 | 134 | 2,748 | 3,346 | 4,699 | +1,353 | 4,698 |
| 20260811 | 134 | 2,576 | 3,400 | 4,998 | +1,598 | 4,995 |
| 20260808 | 2276 | 5,979 | 8,386 | 8,356 | -30 | 8,356 |
| 20260811 | 2276 | 5,972 | 8,683 | 8,469 | -214 | 8,468 |

Thus 134's remaining error is dominated by downstream purpose/structure/bound
repair reintroducing held geometry. For 2276 that repair slightly reduces held
flow; its remaining error is already present in the joint projection and must
be addressed through non-held route support or a separately justified
regularizer. The bound report is
`validation/loso_controlled_rounding_postpublication_v1.json`.

### 2.2 The edge that creates the bias

For both junction folds and both seeds, almost all extra held entries created
by the four-pass measurement repair are attributable to the still-measured
station-133 edge `26842525_26355153_0`:

| Seed | Held station | Global held entries | Legacy direct | Net held entries added by edge 133 repair |
|---:|---:|---:|---:|---:|
| 20260808 | 134 | 3,734 | 6,102 | +2,368 |
| 20260811 | 134 | 3,814 | 5,851 | +2,037 |
| 20260808 | 2276 | 7,432 | 10,215 | +2,783 |
| 20260811 | 2276 | 8,022 | 10,832 | +2,810 |

One inspected example, seed 20260808 / fold 134 / quarter 61:

- station-133 rounded target: 97;
- continuous flow on the station-133 edge: 96.933;
- global largest-remainder count on that edge: 0;
- 1,321 candidate variables touch station 133 and all have continuous flow
  below one;
- 217 of those variables cross held station 134 and carry only 13.502
  continuous vehicles;
- the legacy repair selects 97 held-crossing routes for all 97 additions,
  even though their combined continuous mass is only 10.039;
- a joint floor/ceil MILP satisfies all active measurements and the interval
  total with 84 held entries instead of the legacy 173.

The legacy sort key is `(number_of_active_measured_edges_touched,
integer_count - continuous_flow)`. Holding 134 out changes a 133→134 route's
first key from two to one. The validation fold itself therefore creates the
preference.

### 2.3 Station 107 is a different failure

Station 107 must not be silently included in the same quality claim. The legacy
publisher leaves a maximum active-measurement residual of 9 vehicles in seed
20260808 and 23 in seed 20260811. Yet exact conflict analysis shows only one
mathematically infeasible quarter per seed; the heuristic unnecessarily leaves
nonzero residuals in 7 and 34 quarters respectively.

The irreducible conflict is the same in both pools and excludes the interval
total. In quarter 11, active targets are 4 on incoming station-133 edge
`26842525_26355153_0`, but only 2 and 1 on the two pool-supported outgoing
branches `26355153_91615277_0` and `26355153_96523321_0`. The missing unit is a
candidate movement-support/topology conflict. Every one of the 192 paired
quarters is integer-feasible within the already declared 1× PFE measurement
band.

The validation fallback therefore uses that existing rung band and
lexicographically minimises maximum sensor residual, total sensor residual,
then route L1 deviation. It uses the band in exactly quarter 11 and limits the
maximum residual to one. This is technically valid but not a quality treatment:
107 changes from v7 `1.220/2.005` to `1.333/2.001`, and GEH changes from
`66.7/20.8%` to `54.2/25.0%`. Station 107's controlled-rounding quality variant
is rejected without six seeds. Evidence:
`validation/loso_integer_margin_conflicts_v1.json` and
`validation/loso_controlled_rounding_v8_station107_pair.json`.

## 3. Research synthesis

SUMO's official count-to-route documentation states that matching count data
alone does not define a unique demand solution; the available tools differ in
how they resolve that ambiguity. It also describes `routeSampler` as repeatedly
selecting from a supplied route whitelist to fulfil edge, turn and optionally
OD counts. This supports treating the route pool and the discrete selection
rule as parts of the model, not neutral serialization details:
<https://eclipse.dev/sumo/docs/Demand/Routes_from_Observation_Points.html>.

The PFE literature reaches the same observability boundary from a transport
model perspective. The 2004 UC research report says count number and location
significantly affect OD quality and that recovering the spatial OD pattern is
difficult even with counts on every link:
<https://escholarship.org/uc/item/8q85121w>. The original stochastic PFE is a
network observer that infers unmeasured link/path flows from detector data and
a route-choice model; those inferred flows are therefore model-dependent:
<https://www.sciencedirect.com/science/article/abs/pii/S0968090X97000090>.

Controlled-rounding research shows why independent or sequential rounding is
the wrong abstraction when several margins must survive. Doerr et al. construct
roundings with bounded row, column and whole-matrix error and describe a
dependent randomized version that is unbiased:
<https://arxiv.org/abs/cs/0604068>. Cont and Heidari formulate rounding under
integer constraints as an optimisation problem over all feasible integer
solutions rather than rounding each relaxed variable independently:
<https://arxiv.org/abs/1501.00014>.

The implementation uses SciPy's official MILP interface, which represents
integer variables and joint linear lower/upper constraints and is backed by
HiGHS. SciPy explicitly notes that simply rounding a relaxed solution need not
give the correct integer solution:
<https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html>.

These sources do not prove that this repository's particular objective is
optimal traffic science. They do support the narrower engineering conclusion:
when several sensor margins and a global total must coexist, solve the integer
constraints jointly and expose any infeasibility instead of relying on a
fold-dependent greedy ordering.

## 4. Experimental formulation

For one quarter, let:

- `x_j >= 0` be the continuous PFE flow for route×purpose variable `j`;
- `z_j` be the published nonnegative integer count;
- `M_ej` be 1 when route `j` crosses active measured edge `e`;
- `y_e` be the active measured target;
- `T = round(sum_j x_j)`.

The fast model solves:

```text
minimise    sum_j |z_j - x_j|
subject to  sum_j M_ej z_j = round(y_e)  for every active measured edge e
            sum_j z_j = T
            floor(x_j) <= z_j <= ceil(x_j)
            z_j integer
```

Inside the floor/ceil domain the exact L1 change from choosing the ceiling is
linear (`1 - 2*fraction_j`), so no deviation auxiliaries are required. If that
domain is infeasible but the exact margins are jointly feasible, a general L1
MILP allows any nonnegative integer allocation with the same total.

Across the targeted 384 quarters, 375 solved in the fast model and nine used
the general exact model. Every active measurement residual was zero and the
reported maximum branch-and-bound node count was one in the offline replay.

The held station appears nowhere in `M`, `y`, the objective or the solver
selection rule. It is used only afterwards for evaluation.

## 5. Code and evidence map

- `traffic_sim/confidence/controlled_rounding.py`: validation-only joint
  projection, explicit inconsistent-margin fallback, and a byte-equivalent
  legacy trace with correction attribution.
- `tools/analyze_loso_picker_diagnostics.py`: streaming schema-3 decomposition
  over the immutable raw traces.
- `traffic_sim/confidence/loso.py`: opt-in
  `--experimental-controlled-rounding`; computes pre-publication counts and
  passes them through the existing `quarter_publish_counts()` repair logic.
  The production rounder is temporarily substituted only after the worker
  pool closes and is restored in `finally`.
- `tools/run_loso_picker_diagnostics.py`: immutable paired treatment runner.
- `runs/loso-controlled-rounding-v8-20260809/`: untracked raw paired run; 32
  archived artifacts re-hashed with zero mismatches.
- `validation/loso_controlled_rounding_v8_pair.json`: tracked compact decision
  artifact.

`traffic_sim/demand/pfe.py` was not modified. The treatment is not used by the
production demand build and does not invalidate the existing warming bank.

## 6. Decision and next work, in order

1. **Retain v8 for further validation, not production.** The effect is large,
   directionally consistent and survives the real publisher/SUMO in two seeds.
2. **Do not run six seeds yet.** Post-publication totals are now reconstructed:
   repair adds 1,353–1,598 held entries for 134 but removes 30–214 for 2276.
   Add per-repair-class instrumentation next. For 134, test a single joint
   projection that includes continuously active purpose margins and independent
   structure/bound constraints. For 2276, do not tune that repair; test
   non-held support or regularisation because the excess precedes it.
3. **Make matrix construction reusable.** Candidate incidence and constraint
   sparsity are constant across 96 quarters within a fold. Cache them before
   considering a broader campaign.
4. **Station 107 controlled rounding is quality-rejected.** Its exact conflict
   and rung-band behavior are now resolved, but paired quality is worse/flat.
   Repair or diversify independently justified candidate movement support at
   node `26355153` before another 107 picker treatment.
5. **Do not run the fixed six-seed campaign yet.** The all-station paired screen
   is mixed: 134/2276 improve, 107 does not, and each has a different remaining
   mechanism. A future combined treatment must first pass the same two seeds.
6. **Production/warming decision comes last.** If controlled rounding moves
   into `pfe.py` or another production-bound source, create a new warming plan
   key and bank under plan §13 Decision B. Until then Decision A applies and
   the existing bank remains compatible.

## 7. Non-claims

- This does not prove that the candidate pool is adequate; route-set Jaccard
  remains only 0.10–0.14 across seeds and most crossing OD strata have one
  effective route.
- This does not make TAG pass or establish release quality.
- Daily-ratio improvement does not guarantee hourly improvement.
- No held count was used to choose route integers, constraints, coefficients or
  the two pre-registered seeds.
- The station-107 residual policy is unresolved and must not be hidden behind a
  large objective weight or a silently widened band.

## 8. Supersession: production v11 generic sensor onboarding

The v8 decision above is preserved as the historical experiment. It is
superseded for current implementation by the production joint publisher and
the v11 observability gate.

The robust fix has three separate layers because they prevent three different
failure classes:

1. `build_candidates.py` checks each measured edge in the final routed pool,
   after routing and integrity filters. Any edge below `--min-per-sensor`
   fails the build; the previous warning/zero-only check is no longer enough.
2. `traffic_sim/demand/pfe.py` jointly projects every active sensor in canonical
   order. Inactive registered edges receive their rounded continuous prediction
   as a soft shadow margin. Held observations are never supplied. Active
   measurements, hard bounds and purpose constraints remain higher priority.
3. LOSO computes a route-incidence certificate. Active sensor rows plus the
   total-flow row form matrix `A`; held station row `h` is identifiable exactly
   when `rank(A) == rank([A; h])`. A rank gain marks the fold
   `underidentified`. The contribution reporter then refuses to describe a new
   sensor as an improvement unless its v11 certificate has
   `onboarding_ready=true`.

Real seed-20260811 station-107 evidence after the shadow publisher:

- ratio `1.615`, hourly GEH<5 `29.2%`;
- 2,361 route variables cross the two-way station total;
- 1,695 cross no active sensor in that fold;
- incidence rank `6 -> 7`, normalized row-span residual `0.540`;
- each component's published integer margin differs by zero vehicles per
  quarter from its rounded continuous shadow margin.

Therefore the remaining 107 error is not another rounding-order defect. The
other sensors do not mathematically determine that held street's flow. The same
pool-only audit marks every current station underidentified when held out. More
seeds can measure the variability but cannot create the missing information.

Adding a sensor now automatically invalidates annual warming because
`data_in/sensors.json`, `build_candidates.py` and `pfe.py` are bound demand
source identities. After the final source changes the unexecuted plan is
`37e78c7f056530b0d62bf0c7a73f6de432d83541334ba7a61528b41566c7535f` and
preflight `c34a54d28a6e478c61be064b869db2760db42dbf507b6f4718e9284b4899efb7`
passes. No 104,685-state population was started because absolute LOSO/TAG
quality is still rejected.

## 9. Final vehicle sensor-anchor contract and rejected pool treatments

The physical demand contract is: every published vehicle must traverse at
least one edge belonging to a registered sensor. Candidate generation already
constructed sensor-crossing routes, but that alone did not prove the final XML
after integer selection and purpose-route replacement. Production publication
now receives the union of resolved registry edges and checks every final route
instance. A violation deletes the staging file and fails closed. The same
generic union is rebuilt when a sensor is added, so there is no station-specific
allow-list to maintain.

LOSO uses the same full physical registry for this check. Holding out a station
means that its measured count is absent from fitting; it does not mean that the
sensor, road or vehicles physically disappear. Two targeted experiments made
the distinction measurable:

- Filtering the fold pool to routes that cross a still-active sensor removed
  routes seen only by held station 107. Its recovery ratio collapsed from
  `1.615` to about `0.18`, so this treatment deletes real traffic and is
  rejected.
- Unioning the seed-20260808 and seed-20260811 candidate pools increased the
  pool to 18,201 candidates and 10,667 shapes. Station 107 worsened to about
  `1.67` (GEH<5 `29.2%`), while station 134 improved to about `1.03` (GEH<5
  `100%`). The mixed result and added volume show that more routes alone do not
  identify how active-sensor-equivalent mass should be allocated.

The final seed-20260811 station-107 run passed the new vehicle-level contract:
seven resolved registered edges, `unanchored_vehicles=0`, active PFE GEH<5
`100%`, held ratio `1.615`, held GEH<5 `29.2%`. The held station remains
underidentified (rank `6 -> 7`), so the sensor-anchor rule is satisfied but
cannot by itself make LOSO pass.

Decision: retain the final sensor-anchor publication gate; reject active-only
route deletion and the tested two-seed pool union. Do not force ratios below
one—the unbiased target is one, and values below one are underprediction.
Further absolute LOSO improvement requires independent movement information or
another sensor that closes the rank gap, followed by a fresh warming identity.

## 10. Production pool/picker robustness v13

The final pre-warming review found several generic defects that were independent
of any one station and therefore safe to fix without held observations:

1. Candidate generation trusted `flows.json` without proving it matched the
   reviewed sensor registry. The two edge sets now have to agree exactly and be
   non-empty, sensor edges are canonically sorted, and the registry plus loader
   enter the candidate-cache key. Adding or re-snapping a sensor therefore
   invalidates the old pool and fails closed if its flow series is missing.
2. A reusable weekday/weekend geometry template could inherit the actual hourly
   profile of whichever calendar date appeared first in a window. Geometry now
   receives a canonical day-type profile; real date profiles affect departures
   only.
3. The final sensor floor counted vehicles. Repeated copies of one physical
   route could therefore satisfy it in a multi-day build. It now counts distinct
   route geometries and every candidate must cross a current registry edge.
4. Random-cost routing could discard a sound OD/via request after creating a
   loop or implausible detour. Missing requests now receive one deterministic
   reroute with unchanged OD/via/departure/provenance and must pass every same
   physical filter before merge. This raised real-pool supply away from the 75%
   failure boundary without adding demand or relaxing validation.
5. Path-size overlap counted one physical geometry repeatedly when purpose
   provenance created several PFE variables. It now counts unique geometries,
   so adding purposes cannot change the physical route prior.
6. Network coverage output printed a constant full numerator and duarouter left
   unused multi-megabyte alternative-route sidecars. Coverage now reports the
   real candidate/network intersection and the transient sidecars are removed.

Two full 12,000-request production pool builds were used rather than synthetic
unit data alone. Seed 20260811 finished with 9,280 candidates, 5,964 distinct
routes, 3,619 OD pairs, 3,729 network edges, zero unanchored routes and minimum
distinct sensor support 516. Seed 20260808 finished with 9,309 / 5,998 / 3,691 /
3,769 / zero / 527 respectively. The deterministic recovery added 238 candidates
to the first seed, including 153 route geometries, 135 OD pairs and 14 network
edges, moving supply retention from 75.35% to 77.33%. A lower random route-
diversity factor of 1.5 was rejected: it retained 9,138 candidates but reduced
distinct routes to 4,935, edge coverage to 3,604 and minimum sensor support to
390.

The LOSO result is deliberately mixed and must not be oversold. On the new
seed-20260811 pool, station 107 moved from the prior v11 ratio 1.615 to 1.644
(GEH<5 remained 29.2%). Station 134 moved from 0.819/87.5% on the old pool with
the current picker to 0.844/91.7%, improving hourly fit while leaving the daily
ratio somewhat low. A second pool seed gave station 134 ratio 0.884/91.7%, but
used the same assignment-field seed and is therefore only a pool-sensitivity
diagnostic, not a fully paired release comparison. Every current station still
has rank gain 1 when held out. The robustness changes are accepted; a generic
absolute LOSO/TAG-quality claim is not.

The exact compact record is
`validation/pool_picker_robustness_v13_20260809.json`. The source changes require
fresh warming identity `66fb46d46e751b86bb1851be148e17a6d921288396b97868d0b28c73a4ee6177`;
preflight `6f2d99700e06…` passes and population remains unstarted.

## 11. Cumulative structure active set and fresh temporal decision

The final pre-warming rebuild exposed a separate integer-repair defect. The
optional structure pass recalculated only the groups overflowing in the current
solution. Repairing a newly overflowing group could therefore re-break a group
that had passed in that particular pass, and the hard-coded three passes merely
moved the short-trip warning between quarters. The production repair now keeps
a cumulative active set: once a structure group overflows it stays constrained
for the rest of that quarter's repair. Iteration is bounded by the finite number
of structure groups and stops on no progress or when no new group activates.

The audit and solver also used different cap semantics. The integer solver used
`floor(raw_cap + 0.5)` while the report compared an integer count with the raw
fractional cap. Both now use `traffic_sim/demand/structure_caps.py` as their
single integer-cap definition. The active build `dbb44172f30778adf8c0` has zero
under-1-km cap violations, no structure flags, no unanchored vehicles, PFE
GEH<5 `100%`, and raw hourly SUMO sensor GEH<5 `100%` (maximum GEH `1.639`).
The post-fix focused checks are 121 PFE tests plus 212 candidate/provenance tests,
all passing.

A fresh temporal holdout on 2025-09-17 was then completed against the unchanged
candidate pool `dbfaf49a…`. Station ratios were 107 `1.260`, 1074 `0.706`, 1076
`0.563`, 133 `0.734`, 134 `1.015`, and 2276 `1.249`. The TAG-aligned aggregate
had 143 hourly cases: GEH<5 `65.0%`, flow-difference `81.8%`, and either
criterion `82.5%`, all below the `85%` guideline. Every held station again had
rank gain 1. This is an explicit rejection of an absolute LOSO/TAG claim, not a
reason to alter pool or picker with held counts. Additional folds without new
movement information cannot remove that structural underidentification.

The temporal run preceded only the cumulative integer structure implementation.
Candidate geometry, assignment fields and observability did not change, so its
rank certificate remains applicable; the exact station metrics are labelled a
pre-active-set-revision diagnostic rather than final release evidence.
